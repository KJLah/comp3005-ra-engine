"""Pipelined operators (Module 2, Sections 2.1-2.3 and 2.10):
seqscan, filter, project, limit, union_all."""
from __future__ import annotations

from typing import Iterator, List, Optional

from storage.errors import CatalogError
from storage.heap import NO_PAGE, DirectoryPage
from storage.page import SlottedPage
from storage.record import VARCHAR, decode as decode_record

from .base import Operator, register
from .conditions import row_predicate
from .errors import BindError
from .schema import Col, Row, Schema


@register("seqscan")
class SeqScan(Operator):
    """Every live tuple of a table in heap order, one page pinned at a time.

    Module 1's HeapFile.scan() keeps a directory page and a data page pinned
    together (2 frames), but §2.1 allows 1. So seqscan walks the directory
    itself: pin a directory page, copy its list of data page ids (at most 681
    integers), unpin it, then pin each data page in turn. Reads are the M
    data pages plus one per directory page (1 for tables up to 681 pages)."""

    pipelined = True

    def label(self) -> str:
        return f"seqscan {self.node['table']}"

    def _bind(self) -> Schema:
        try:
            self.info = self.ctx.catalog.get(self.node["table"])
        except CatalogError as e:
            raise BindError(f"{self.path} (seqscan): {e}") from None
        # one handle per operator, kept across rescans; its I/O is ours
        self.disk = self.ctx.table_disk(self.info.file, self.c)
        return Schema.of_table(self.info.name, self.info.columns)

    def _open(self, limit: int) -> None:
        self.hold(1)
        self.it = self._scan()

    def _scan(self) -> Iterator[Row]:
        pool, disk, cols = self.ctx.pool, self.disk, self.info.columns
        dir_id = 0
        while dir_id != NO_PAGE:
            d = DirectoryPage(pool.fetch_page(disk, dir_id))
            try:
                data_ids = [d.entry(i)[0] for i in range(d.entry_count)]
                nxt = d.next_dir
            finally:
                pool.unpin_page(disk, dir_id, False)
            for pid in data_ids:
                page = SlottedPage(pool.fetch_page(disk, pid))
                try:
                    rows = [decode_record(cols, page.get(s)) for s in page.live_slots()]
                finally:
                    pool.unpin_page(disk, pid, False)
                yield from rows        # one page of tuples: the frame we hold
            dir_id = nxt

    def _next(self) -> Optional[Row]:
        return next(self.it, None)

    def _close(self) -> None:
        self.it.close()


@register("filter")
class Filter(Operator):
    """One comparison per tuple examined, however compound the condition."""

    pipelined = True

    def _bind(self) -> Schema:
        child = self.children[0].schema
        self.pred = row_predicate(self.node["condition"], child, f"{self.path} (filter)")
        return child

    def _open(self, limit: int) -> None:
        self.children[0].open(limit)

    def _next(self) -> Optional[Row]:
        child = self.children[0]
        while True:
            r = child.next()
            if r is None:
                return None
            self.c.comparisons += 1
            if self.pred(r):
                return r


@register("project")
class Project(Operator):
    """Keeps the listed columns in the listed order. Bag semantics: no
    duplicate removal (that is distinct's job)."""

    pipelined = True

    def _bind(self) -> Schema:
        child = self.children[0].schema
        where = f"{self.path} (project)"
        self.idx = [child.index(name, where) for name in self.node["columns"]]
        return Schema([child.cols[i] for i in self.idx], where)

    def _open(self, limit: int) -> None:
        self.children[0].open(limit)

    def _next(self) -> Optional[Row]:
        r = self.children[0].next()
        if r is None:
            return None
        return tuple(r[i] for i in self.idx)


@register("limit")
class Limit(Operator):
    """The first n tuples. After the n-th it never calls next on its child
    again, and closes the child at once so its frames come back."""

    def _bind(self) -> Schema:
        self.n = self.node["n"]
        return self.children[0].schema

    def _open(self, limit: int) -> None:
        self.seen = 0
        if self.n > 0:
            self.children[0].open(limit)
        else:
            self.children[0].close()

    def _next(self) -> Optional[Row]:
        if self.seen >= self.n:
            return None
        r = self.children[0].next()
        if r is None:
            self.seen = self.n
            return None
        self.seen += 1
        if self.seen == self.n:
            self.children[0].close()       # stop: no further pulls, frames back
        return r


def setop_schema(op: Operator) -> Schema:
    """§2.10: same number of columns with the same types, position by
    position. Names may differ; the output takes the left input's names.
    VARCHAR lengths may differ; the output takes the larger, so a right
    tuple always fits the output schema."""
    left, right = op.children[0].schema, op.children[1].schema
    where = f"{op.path} ({op.op})"
    if len(left) != len(right):
        raise BindError(f"{where}: left has {len(left)} columns, right has "
                        f"{len(right)}")
    cols: List[Col] = []
    for i, (a, b) in enumerate(zip(left.cols, right.cols)):
        if a.type != b.type:
            raise BindError(f"{where}: column {i + 1} is {a.type} ({a.name}) on the "
                            f"left but {b.type} ({b.name}) on the right")
        length = max(a.length, b.length) if a.type == VARCHAR else None
        cols.append(Col(a.name, a.type, length))
    return Schema(cols, where)


@register("union_all")
class UnionAll(Operator):
    """Concatenation: the left input, then the right. Only one side is open at
    a time, so it holds no more than either side alone."""

    def _bind(self) -> Schema:
        return setop_schema(self)

    def _open(self, limit: int) -> None:
        self.limit = limit
        self.side = 0
        self.children[0].open(limit)

    def _next(self) -> Optional[Row]:
        while True:
            r = self.children[self.side].next()
            if r is not None:
                return r
            if self.side == 1:
                return None
            self.children[0].close()
            self.side = 1
            self.children[1].open(self.limit)
