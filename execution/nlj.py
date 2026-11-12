"""Block nested loop join (Module 2, Section 2.7).

    for each block of K = B - 2 pages of the outer (left) input:
        stream the whole inner (right) input past it, one page at a time,
        and test every (outer, inner) pair.

    page reads  = M + ceil(M / K) * N
    comparisons = |R| * |S|        (every pair, whatever matches)

B here is the frames the join may hold while streaming (its open() limit).
Of those, K = B - 2 hold the outer block, 1 is the inner page (the inner
child's own frame), and the "output frame" is the outer child's input frame:
output tuples go to the parent through next(), so no output page is ever
filled, and that frame is free to read the outer input through.

The inner input is read ceil(M / K) times. A pipelined inner (seqscan,
filter, project) is simply reopened. Anything else (a sort, a join, ...) is
run once inside open() and written to a temporary file, which is then read
once per block; that costs N writes and is reported.
"""
from __future__ import annotations

from typing import Iterator, List, Optional

from storage.page import SlottedPage

from .base import Operator, register, rows_of
from .conditions import join_predicate
from .errors import BudgetError, ExecError
from .schema import Row, Schema, encode


def is_rescannable(op: Operator) -> bool:
    return op.pipelined and all(is_rescannable(c) for c in op.children)


@register("nested_loop_join")
class NestedLoopJoin(Operator):
    stream_need = 3

    def label(self) -> str:
        b = getattr(self, "blocks", None)
        return f"nested_loop_join blocks={b}" if b is not None else "nested_loop_join"

    def _bind(self) -> Schema:
        left, right = self.children[0].schema, self.children[1].schema
        where = f"{self.path} (nested_loop_join)"
        out = left.concat(right, where)
        self.pred = join_predicate(self.node["condition"], left, right, where)
        return out

    def _open(self, limit: int) -> None:
        outer, inner = self.children
        self.inner_file = None
        if not is_rescannable(inner):
            # materialise the inner input once; it is then read once per block
            inner.open(max(1, self.ctx.free() - 1))
            self.inner_file = self.new_temp("inner", inner.schema)
            w = self.inner_file.writer()
            for r in rows_of(inner):
                w.add(r)
            w.close()
            inner.close()
        outer.open(1)
        # K = B - 2: the inner page and the outer input frame are the other two
        self.K = min(limit - 2, self.ctx.free() - 1)
        if self.K < 1:
            raise BudgetError(f"{self.who}: needs at least 3 frames, has "
                              f"{self.ctx.free() + 1}")
        self.hold(self.K)
        if self.inner_file is not None:
            self.hold(1)                      # the frame that reads the temp file
        self.blocks = 0
        self.scratch = self.ctx.scratch_disk(f"{self.path} block", self.c)
        self.pinned: List[int] = []
        self.it = self._join()

    # ---- one block of the outer input in K pinned frames -------------------------
    def _fill_block(self, outer_rows: Iterator[Row], carry: Optional[Row]):
        pool = self.ctx.pool
        block: List[Row] = []
        page = None
        nxt = carry if carry is not None else next(outer_rows, None)
        while nxt is not None:
            rec = encode(self.children[0].schema, nxt)
            if page is None or page.insert(rec) is None:
                if len(self.pinned) == self.K:
                    return block, nxt              # block full: nxt starts the next one
                pid, data = pool.new_page(self.scratch)
                self.pinned.append(pid)
                page = SlottedPage.init(data, pid)
                if page.insert(rec) is None:
                    raise ExecError(f"{self.who}: tuple too large for a page")
            block.append(nxt)
            nxt = next(outer_rows, None)
        return block, None

    def _release_block(self) -> None:
        for pid in self.pinned:
            self.ctx.pool.unpin_page(self.scratch, pid, False)
            self.ctx.pool.discard_page(self.scratch, pid)
        self.pinned.clear()

    def _inner_rows(self) -> Iterator[Row]:
        inner = self.children[1]
        if self.inner_file is not None:
            yield from self.inner_file.rows()
            return
        if inner.state != "bound":
            inner.reset()
        inner.open(1)
        try:
            yield from rows_of(inner)
        finally:
            inner.close()

    def _join(self) -> Iterator[Row]:
        outer_rows = rows_of(self.children[0])
        carry = None
        pred = self.pred
        while True:
            block, carry = self._fill_block(outer_rows, carry)
            if not block:
                break
            self.blocks += 1
            n = len(block)
            for s in self._inner_rows():
                self.c.comparisons += n
                for r in block:
                    if pred(r, s):
                        yield r + s
            self._release_block()
            if carry is None:
                break
        self.children[0].close()

    def _next(self) -> Optional[Row]:
        return next(self.it, None)

    def _close(self) -> None:
        it = getattr(self, "it", None)
        if it is not None:
            it.close()
        if getattr(self, "pinned", None):
            self._release_block()
