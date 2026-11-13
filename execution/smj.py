"""Sort-merge join (Module 2, Section 2.8). Equality conditions only.

Both inputs are sorted on the join key with the external sort (§2.5), each
merged down to a single run, then two cursors advance in step. Duplicate keys
follow the handout's pseudocode: the run of equal keys on the right is kept,
and for every left tuple of the matching run the right run is "rewound" and
replayed.

    cost        = sort(M) + sort(N) + M + N
    comparisons = key comparisons in the merge + those inside both sorts

Frames while merging: 1 for the left run, 1 for the right run, 1 for the
right run of equal keys (§3: "its sorts, then 3 for the merge"). A run of
equal keys larger than that one frame is spilled to a temporary file and
re-read for every left tuple with that key; it is never assumed to fit.
"""
from __future__ import annotations

from typing import Iterator, List, Optional

from .base import Operator, register
from .conditions import equi_keys
from .hashing import PAGE_BYTES
from .schema import KeyComparator, PairComparator, Row, Schema, encode
from .sort import sort_child
from .nlj import is_rescannable

SLOT = 4


class RunBuffer:
    """The right side's run of equal keys: in memory while it fits in one
    page, otherwise in a temporary file (written through the same frame)."""

    def __init__(self, op: Operator, schema: Schema):
        self.op, self.schema = op, schema
        self.rows: List[Row] = []
        self.used = 0
        self.file = None
        self.writer = None
        self.first: Optional[Row] = None

    def add(self, row: Row) -> None:
        if self.first is None:
            self.first = row
        if self.file is None:
            size = len(encode(self.schema, row)) + SLOT
            if self.used + size <= PAGE_BYTES:
                self.rows.append(row)
                self.used += size
                return
            # spill: everything so far, then this tuple, go to a temp file
            self.op.spills += 1
            self.file = self.op.new_temp("run", self.schema)
            self.writer = self.file.writer()
            for r in self.rows:
                self.writer.add(r)
            self.rows = []
        self.writer.add(row)

    def replay(self) -> Iterator[Row]:
        if self.file is None:
            return iter(self.rows)
        if self.writer is not None:
            self.writer.close()
            self.writer = None
        return self.file.rows()

    def release(self) -> None:
        if self.writer is not None:
            self.writer.abandon()
        if self.file is not None:
            self.op.drop_temp(self.file)


@register("sort_merge_join")
class SortMergeJoin(Operator):
    stream_need = 3

    def _bind(self) -> Schema:
        left, right = self.children[0].schema, self.children[1].schema
        where = f"{self.path} (sort_merge_join)"
        out = left.concat(right, where)
        self.lk, self.rk = equi_keys(self.node["condition"], left, right, where)
        return out

    def _open(self, limit: int) -> None:
        self.spills = 0
        left, right = self.children

        self.ls = sort_child(
            self, left,
            [(i, True) for i in self.lk],
            1, "left",
        )

        parked = 0

        if self.ctx.free() < 4 and not is_rescannable(right):
            parked = self.ls.stream_frames()

            # A memory-resident left run must reach disk before
            # its reservation can be released.
            if self.ls.mem_run is not None:
                t = self.new_temp("parked_left", left.schema)
                w = t.writer()

                rows = self.ls.mem_run
                rows.reverse()
                while rows:
                    w.add(rows.pop())

                w.close()
                self.ls.mem_run = None
                self.ls.mem_pages = 0
                self.ls.runs = [t]

            self.unhold(parked)

        self.rs = sort_child(
            self, right,
            [(i, True) for i in self.rk],
            1, "right",
        )

        # Restore the left reader reservation, when parked,
        # and reserve the equal-key run buffer.
        self.hold(1 + parked)
        self.it = self._merge()

    def label(self) -> str:
        if getattr(self, "spills", 0):
            return f"sort_merge_join spills={self.spills}"
        return "sort_merge_join"

    def _merge(self) -> Iterator[Row]:
        lr = PairComparator(self.lk, self.rk, self.count)
        rr = KeyComparator([(i, True) for i in self.rk], self.count)
        L, R = self.ls.stream(), self.rs.stream()
        l, r = next(L, None), next(R, None)
        while l is not None and r is not None:
            c = lr(l, r)
            if c < 0:
                l = next(L, None)
            elif c > 0:
                r = next(R, None)
            else:
                run = RunBuffer(self, self.children[1].schema)
                try:
                    run.add(r)
                    r = next(R, None)
                    while r is not None and rr(run.first, r) == 0:
                        run.add(r)
                        r = next(R, None)
                    # for each left tuple of this key: rewind the right run
                    while l is not None and lr(l, run.first) == 0:
                        for s in run.replay():
                            yield l + s
                        l = next(L, None)
                finally:
                    run.release()

    def _next(self) -> Optional[Row]:
        return next(self.it, None)

    def _close(self) -> None:
        it = getattr(self, "it", None)
        if it is not None:
            it.close()
