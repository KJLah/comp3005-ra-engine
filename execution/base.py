"""The iterator interface every operator obeys (Module 2, Section 1.1).

    open(limit)   prepare, acquire frames, do any blocking work
    next()        the next output tuple, or END (None)
    close()       release every frame and temporary file; closes the children
    counters()    {tuples_out, comparisons, page_reads, page_writes}

Before open there is bind(), which builds the output schema bottom-up and does
every name and type check. bind() reads the catalog but no table data, so a
plan that names an unknown table or column is rejected before any work
happens (Section 4).

Frames (Section 1.2; full rule in DESIGN_M2.md). An operator records the
frames it holds with hold() / unhold(), which reserve them in the plan's
budget of B. The pool itself has exactly B frames, so even a bookkeeping
mistake cannot hold more than B pages: the pool would refuse.

    open(limit): `limit` is the most frames this subtree may still hold once
    open() returns and tuples start streaming. While open() runs, the subtree
    may also use, temporarily, every frame that is free in the budget at
    that moment (this is when blocking work happens). A blocking operator
    therefore gets "everything nobody else is holding", gives the extra
    back at the end of open(), and keeps at most `limit`.
"""
from __future__ import annotations

from typing import Callable, Dict, Iterator, List, Optional, Tuple, Type

from .context import Counters, ExecContext, TempFile
from .errors import BudgetError, ExecError
from .schema import Row, Schema

END = None

# op name -> Operator subclass; filled in by the operator modules
OPERATORS: Dict[str, Type["Operator"]] = {}


def register(name: str) -> Callable[[Type["Operator"]], Type["Operator"]]:
    def deco(cls):
        cls.op = name
        OPERATORS[name] = cls
        return cls
    return deco


class Operator:
    op = "?"
    #: True when the subtree can be reopened and re-read at the cost of its
    #: page reads only, holding at most one frame (seqscan, filter, project).
    pipelined = False
    #: Frames this operator needs for itself while streaming. If its parent
    #: leaves it fewer, it materialises its output (see open()).
    stream_need = 1

    def __init__(self, ctx: ExecContext, path: str, node: dict,
                 children: List["Operator"]):
        self.ctx = ctx
        self.path = path
        self.node = node
        self.children = children
        self.c = Counters()
        self.schema: Optional[Schema] = None
        self.state = "new"          # new -> bound -> open -> closed
        self.held = 0
        self.temps: List[TempFile] = []

    # ---- names -------------------------------------------------------------------
    @property
    def who(self) -> str:
        return f"{self.op} at {self.path}"

    def label(self) -> str:
        """What `exec trace` prints for this node."""
        return self.op

    # ---- bind ----------------------------------------------------------------------
    def bind(self) -> Schema:
        for ch in self.children:
            ch.bind()
        self.schema = self._bind()
        self.state = "bound"
        return self.schema

    def _bind(self) -> Schema:
        raise NotImplementedError

    # ---- open / next / close ----------------------------------------------------------
    def open(self, limit: int) -> None:
        if self.state != "bound":
            raise ExecError(f"{self.who}: open() called in state {self.state}")
        if limit < 1:
            raise BudgetError(f"{self.who}: no frame left to stream through")
        self.state = "open"
        self.mat_iter = None
        try:
            if limit < self.stream_need:
                self._open_materialised()
            else:
                self._open(limit)
        except BaseException:
            self.close()
            raise
        if self.subtree_held() > limit:
            raise BudgetError(f"{self.who} holds {self.subtree_held()} frames after "
                              f"open, more than its limit {limit}")

    def _open_materialised(self) -> None:
        """The parent leaves fewer frames than this operator needs to stream.
        Run it to completion inside open() with every free frame but one,
        write its output to a temporary file through that one frame, release
        everything, then stream the file holding 1 frame. The extra writes
        and reads are this operator's and appear in its counters."""
        self._open(max(1, self.ctx.free() - 1))
        out = self.new_temp("out")
        self.mat_writer = w = out.writer()
        while True:
            r = self._next()
            if r is None:
                break
            w.add(r)
        w.close()
        self.mat_writer = None
        self._close()
        for ch in self.children:
            ch.close()
        if self.held:
            self.unhold(self.held)
        self.hold(1)
        self.mat_iter = out.rows()

    def next(self) -> Optional[Row]:
        if self.state != "open":
            raise ExecError(f"{self.who}: next() called in state {self.state}")
        if self.mat_iter is not None:
            row = next(self.mat_iter, None)
        else:
            row = self._next()
        if row is not None:
            self.c.tuples_out += 1
        return row

    def close(self) -> None:
        if self.state == "closed":
            return
        was_open = self.state == "open"
        self.state = "closed"
        try:
            if getattr(self, "mat_iter", None) is not None:
                self.mat_iter.close()
                self.mat_iter = None
            elif was_open:
                w = getattr(self, "mat_writer", None)
                if w is not None:
                    w.abandon()
                self._close()
        finally:
            for t in list(self.temps):
                t.delete()
            self.temps.clear()
            if self.held:
                self.unhold(self.held)
            for ch in self.children:
                ch.close()

    def reset(self) -> None:
        """Close, then make the operator openable again (for rescans)."""
        self.close()
        for ch in self.children:
            ch.reset()
        self.state = "bound"

    def _open(self, limit: int) -> None:
        pass

    def _next(self) -> Optional[Row]:
        raise NotImplementedError

    def _close(self) -> None:
        pass

    def count(self, n: int = 1) -> None:
        """Add to this operator's comparison counter."""
        self.c.comparisons += n

    # ---- frames and temp files -----------------------------------------------------
    def hold(self, n: int) -> None:
        self.ctx.reserve(self.who, n)
        self.held += n

    def unhold(self, n: int) -> None:
        self.ctx.release(self.who, n)
        self.held -= n

    def subtree_held(self) -> int:
        return self.held + sum(ch.subtree_held() for ch in self.children)

    def new_temp(self, label: str, schema: Optional[Schema] = None) -> TempFile:
        t = self.ctx.new_temp(f"{self.op}_{label}", schema or self.schema, self.c)
        self.temps.append(t)
        return t

    def drop_temp(self, t: TempFile) -> None:
        t.delete()
        if t in self.temps:
            self.temps.remove(t)

    # ---- counters and tree walking -----------------------------------------------
    def counters(self) -> Dict[str, int]:
        return self.c.as_dict()

    def walk(self, depth: int = 0) -> Iterator[Tuple[int, "Operator"]]:
        """Every node in plan order (pre-order), with its depth."""
        yield depth, self
        for ch in self.children:
            yield from ch.walk(depth + 1)


def rows_of(op: Operator) -> Iterator[Row]:
    """Pull every remaining tuple out of an open operator."""
    while True:
        r = op.next()
        if r is None:
            return
        yield r
