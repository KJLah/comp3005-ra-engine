"""External merge sort (Module 2, Section 2.5).

    pass 0   fill F frames with input tuples, sort them in memory (the one
             place the language's sort is allowed: one run that fits), write
             the run to a temporary file.            -> R0 = ceil(N / F) runs
    pass k   merge F-1 runs at a time into one (1 frame per input run, 1 for
             output). A last group of a single run is carried forward
             untouched, not rewritten.
    final    the last merge does not write: it streams to the parent.

F is the number of frames free in the budget when the sort's blocking work
starts (after its child is open). It is fixed for the whole sort, so F plays
the role of B in the handout's formulas:

    passes = 1 + ceil(log(R0) / log(F-1))

Pass 0 reads are the child's (a seqscan's reads land on the seqscan); every
read and write of run files is the sort's own.

ExternalSorter is the reusable engine. The sort operator uses it directly;
distinct, group_by, the set operators and sort_merge_join use it to sort
their inputs, charging the work to themselves.
"""
from __future__ import annotations

import heapq
from functools import cmp_to_key
from typing import Callable, Iterable, Iterator, List, Optional

from storage.page import SlottedPage

from .base import Operator, register, rows_of
from .context import TempFile
from .errors import BudgetError, ExecError
from .schema import KeyComparator, Row, Schema, decode, encode


class _Entry:
    """Heap entry for the k-way merge: one comparison per __lt__ call. Equal
    keys are ordered by run number, so the merge is stable and deterministic."""
    __slots__ = ("row", "run", "cmp")

    def __init__(self, row, run, cmp):
        self.row, self.run, self.cmp = row, run, cmp

    def __lt__(self, other: "_Entry") -> bool:
        c = self.cmp(self.row, other.row)
        return c < 0 or (c == 0 and self.run < other.run)


def kmerge(sources: List[Iterator[Row]], cmp) -> Iterator[Row]:
    heap = []
    for i, src in enumerate(sources):
        r = next(src, None)
        if r is not None:
            heap.append(_Entry(r, i, cmp))
    heapq.heapify(heap)
    while heap:
        top = heap[0]
        yield top.row
        r = next(sources[top.run], None)
        if r is None:
            heapq.heappop(heap)
        else:
            heapq.heapreplace(heap, _Entry(r, top.run, cmp))


class ExternalSorter:
    def __init__(self, owner: Operator, schema: Schema, cmp: Callable[[Row, Row], int],
                 frames: int, label: str = "run"):
        self.owner = owner
        self.ctx = owner.ctx
        self.schema = schema
        self.cmp = cmp
        self.F = frames
        self.label = label
        self.runs: List[TempFile] = []
        self.mem_run: Optional[List[Row]] = None   # a single run kept in memory
        self.mem_pages = 0
        self.passes = 0
        self.initial_runs = 0
        self.log: List[dict] = []                  # per pass, for the report

    # ---- pass 0 ------------------------------------------------------------------
    def build_runs(self, source: Iterable[Row], keep_limit: int) -> None:
        """Pass 0. If the whole input turns out to be one run of at most
        `keep_limit` pages, it stays in memory and is never written."""
        if self.F < 1:
            raise BudgetError(f"{self.owner.who}: no frame left to sort with")
        pool = self.ctx.pool
        scratch = self.ctx.scratch_disk(f"{self.owner.path} sort", self.owner.c)
        pages: List = []               # (pid, SlottedPage), all pinned
        w0 = self._io()

        def take_run(last: bool) -> None:
            n_pages = len(pages)
            rows: List[Row] = []
            for pid, pg in pages:
                rows.extend(decode(self.schema, pg.get(s)) for s in range(pg.slot_count))
                pool.unpin_page(scratch, pid, False)
                pool.discard_page(scratch, pid)
            pages.clear()
            rows.sort(key=cmp_to_key(self.cmp))
            if last and not self.runs and n_pages <= keep_limit:
                self.mem_run, self.mem_pages = rows, n_pages
                return
            t = self.owner.new_temp(self.label, self.schema)
            w = t.writer()
            rows.reverse()
            while rows:                 # the run shrinks as it is written
                w.add(rows.pop())
            w.close()
            self.runs.append(t)

        try:
            for row in source:
                rec = encode(self.schema, row)
                if not pages or pages[-1][1].insert(rec) is None:
                    if len(pages) == self.F:
                        take_run(False)
                    pid, data = pool.new_page(scratch)
                    pg = SlottedPage.init(data, pid)
                    pages.append((pid, pg))
                    if pg.insert(rec) is None:
                        raise ExecError(f"{self.owner.who}: a tuple of {len(rec)} bytes "
                                        f"does not fit in a page")
            if pages:
                take_run(True)
        finally:
            for pid, _ in pages:        # error path only
                pool.unpin_page(scratch, pid, False)
                pool.discard_page(scratch, pid)
        self.passes = 1
        self.initial_runs = len(self.runs) + (1 if self.mem_run is not None else 0)
        self._log(0, None, self.initial_runs, w0)

    # ---- merge passes ----------------------------------------------------------------
    def merge_until(self, cap: int) -> None:
        """Merge passes inside open, until at most `cap` runs remain (cap is at
        most F - 1: the final merge needs one frame per run)."""
        cap = max(1, min(cap, self.F - 1))
        if len(self.runs) > cap and self.F < 3:
            raise BudgetError(f"{self.owner.who}: {len(self.runs)} runs to merge but "
                              f"only {self.F} frame(s); merging needs at least 3")
        fan_in = self.F - 1
        while len(self.runs) > cap:
            before = len(self.runs)
            w0 = self._io()
            new: List[TempFile] = []
            for i in range(0, len(self.runs), fan_in):
                group = self.runs[i:i + fan_in]
                if len(group) == 1:          # carried forward, not rewritten
                    new.append(group[0])
                    continue
                t = self.owner.new_temp(self.label, self.schema)
                w = t.writer()
                for r in kmerge([g.rows() for g in group], self.cmp):
                    w.add(r)
                w.close()
                for g in group:
                    self.owner.drop_temp(g)
                new.append(t)
            self.runs = new
            self._log(self.passes, before, len(new), w0)
            self.passes += 1

    # ---- final pass -------------------------------------------------------------------
    def stream_frames(self) -> int:
        """Frames the streaming phase holds."""
        if self.mem_run is not None:
            return self.mem_pages
        return len(self.runs)

    def stream(self) -> Iterator[Row]:
        if self.mem_run is not None:
            rows = self.mem_run
            self.mem_run = None
            rows.reverse()
            while rows:
                yield rows.pop()
            return
        if len(self.runs) >= 2:
            self.passes += 1
            self.log.append({"pass": self.passes - 1, "runs_in": len(self.runs),
                             "runs_out": "streamed"})
        if len(self.runs) == 1:
            yield from self.runs[0].rows()
        elif self.runs:
            yield from kmerge([t.rows() for t in self.runs], self.cmp)

    # ---- bookkeeping ------------------------------------------------------------------
    def _io(self):
        return (self.owner.c.page_reads, self.owner.c.page_writes)

    def _log(self, p, runs_in, runs_out, start) -> None:
        r, w = self._io()
        self.log.append({"pass": p, "runs_in": runs_in, "runs_out": runs_out,
                         "reads": r - start[0], "writes": w - start[1]})


def open_child_for_blocking(op: Operator, child: Operator, reserve_for_self: int = 3) -> None:
    """Open a child whose output a blocking operator will consume inside its
    own open(). The child may stream with whatever is left after leaving
    `reserve_for_self` frames for the parent, and at least 1 (a child that
    needs more to stream materialises its output instead)."""
    child.open(max(1, op.ctx.free() - reserve_for_self))


def sort_child(op: Operator, child: Operator, keys, cap: int,
               label: str = "run") -> ExternalSorter:
    """Sort a child's whole output inside op.open(): open the child, take
    every free frame (F), build and merge runs until at most `cap` remain,
    close the child, and keep only the frames the streaming phase needs."""
    open_child_for_blocking(op, child)
    F = op.ctx.free()
    op.hold(F)
    sorter = ExternalSorter(op, child.schema, KeyComparator(keys, op.count), F, label)
    sorter.build_runs(rows_of(child), keep_limit=cap)
    child.close()
    if sorter.F < 3 and len(sorter.runs) > 1:
        # Merging needs 3 frames. F is otherwise fixed for the whole sort, but
        # when pass 0 had fewer than 3, the frames the child just gave back
        # are taken for the merge passes (DESIGN_M2.md, external sort).
        extra = min(op.ctx.free(), 3 - sorter.F)
        op.hold(extra)
        F += extra
        sorter.F = F
    sorter.merge_until(cap)
    op.unhold(F - sorter.stream_frames())
    return sorter


@register("sort")
class Sort(Operator):
    def label(self) -> str:
        s = getattr(self, "sorter", None)
        return f"sort passes={s.passes}" if s else "sort"

    def _bind(self) -> Schema:
        child = self.children[0].schema
        where = f"{self.path} (sort)"
        self.keys = [(child.index(name, where), d == "asc") for name, d in self.node["keys"]]
        return child

    def _open(self, limit: int) -> None:
        self.sorter = sort_child(self, self.children[0], self.keys, limit)
        self.it = self.sorter.stream()

    def _next(self) -> Optional[Row]:
        return next(self.it, None)

    def _close(self) -> None:
        it = getattr(self, "it", None)
        if it is not None:
            it.close()
