"""union, intersect, except (Module 2, Section 2.10). union_all is in scan.py.

All three are sort-based: each input is sorted on all its columns with the
external sort, so they work when both inputs exceed memory. Then one merge
pass with set semantics: every output tuple appears once.

Frames: the left input is sorted first and kept streaming with 1 frame (it is
merged down to a single run, or kept in memory if it is one page); the right
input is then sorted with every frame left, and may stream with up to
limit - 1. So the operator needs 2 frames to stream.

Comparisons: those inside both sorts, plus one per comparison in the merge,
plus one per adjacent pair checked when skipping duplicates.
"""
from __future__ import annotations

from typing import Iterator, Optional

from .base import Operator, register
from .scan import setop_schema
from .schema import PairComparator, Row, Schema
from .sort import sort_child
from .nlj import is_rescannable


def _unique(rows: Iterator[Row], cmp) -> Iterator[Row]:
    prev = None
    for r in rows:
        if prev is not None and cmp(prev, r) == 0:
            continue
        prev = r
        yield r


class _SetOp(Operator):
    stream_need = 2

    def _bind(self) -> Schema:
        return setop_schema(self)

    def _open(self, limit: int) -> None:
        n = len(self.schema)
        keys = [(i, True) for i in range(n)]
        left, right = self.children

        self.ls = sort_child(self, left, keys, 1, "left")
        parked = 0

        if self.ctx.free() < 4 and not is_rescannable(right):
            parked = self.ls.stream_frames()

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
            self, right, keys, max(1, limit - 1), "right"
        )
        self.hold(parked)

        cmp = PairComparator(range(n), range(n), self.count)
        self.it = self._merge(
            _unique(self.ls.stream(), cmp),
            _unique(self.rs.stream(), cmp),
            cmp,
        )

    def _next(self) -> Optional[Row]:
        return next(self.it, None)

    def _close(self) -> None:
        it = getattr(self, "it", None)
        if it is not None:
            it.close()


@register("union")
class Union(_SetOp):
    def _merge(self, ls, rs, cmp):
        l, r = next(ls, None), next(rs, None)
        while l is not None and r is not None:
            c = cmp(l, r)
            if c < 0:
                yield l
                l = next(ls, None)
            elif c > 0:
                yield r
                r = next(rs, None)
            else:
                yield l
                l, r = next(ls, None), next(rs, None)
        while l is not None:
            yield l
            l = next(ls, None)
        while r is not None:
            yield r
            r = next(rs, None)


@register("intersect")
class Intersect(_SetOp):
    def _merge(self, ls, rs, cmp):
        l, r = next(ls, None), next(rs, None)
        while l is not None and r is not None:
            c = cmp(l, r)
            if c < 0:
                l = next(ls, None)
            elif c > 0:
                r = next(rs, None)
            else:
                yield l
                l, r = next(ls, None), next(rs, None)


@register("except")
class Except(_SetOp):
    def _merge(self, ls, rs, cmp):
        l, r = next(ls, None), next(rs, None)
        while l is not None:
            if r is None:
                yield l
                l = next(ls, None)
                continue
            c = cmp(l, r)
            if c < 0:
                yield l
                l = next(ls, None)
            elif c > 0:
                r = next(rs, None)
            else:
                l = next(ls, None)
