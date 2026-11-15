"""Name resolution, scopes and the semantic rules (Module 3, Section 3).

The translator calls into this module while it walks a query. Everything a
query means is decided here: which table a column belongs to, whether a
comparison is well typed, whether an aggregate is allowed where it appears.
No plan nodes are built here; translate.py does that.

Catalogs. Name resolution needs each table's columns and types. Three
sources share one interface, `columns(table) -> list of ColInfo or None`:

  * DictCatalog     a dict, for tests and for `sql parse`-style use,
  * StorageCatalog  the Module 1 catalog in a database directory,
  * OverlayCatalog  any catalog plus tables created by CREATE TABLE earlier
                    in the same file, without writing them to disk
                    (`sql compile` and `sql check` never change the database).
"""
from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Dict, Iterable, List, Optional, Sequence, Tuple

from ra_engine.errors import Position

from . import ast as A
from .errors import AggregateError, AmbiguityError, NameError_, SchemaError, TypeError_

INT = "INT"
VARCHAR = "VARCHAR"
REAL = "REAL"          # only AVG produces it


@dataclass(frozen=True)
class ColInfo:
    name: str                     # short name, e.g. "salary"
    type: str                     # INT, VARCHAR or REAL
    length: Optional[int] = None  # n of VARCHAR(n)

    def type_text(self) -> str:
        if self.type == VARCHAR and self.length is not None:
            return f"VARCHAR({self.length})"
        return self.type


def kind(t: str) -> str:
    """Comparison families. INT and REAL (an AVG) are both numbers."""
    return "string" if t == VARCHAR else "number"


# ---- catalogs ---------------------------------------------------------------------
class DictCatalog:
    """{table: [(name, "INT"), (name, "VARCHAR", n), ...]}"""

    def __init__(self, tables: Dict[str, Sequence[Sequence]]):
        self.tables = {t: [ColInfo(*c) for c in cols] for t, cols in tables.items()}

    def columns(self, table: str) -> Optional[List[ColInfo]]:
        return self.tables.get(table)

    def names(self) -> List[str]:
        return list(self.tables)


class StorageCatalog:
    """The Module 1 catalog of a database directory, read once."""

    def __init__(self, db_dir: str):
        from storage.buffer import BufferPool
        from storage.catalog import CATALOG_FILE, Catalog
        if not os.path.isfile(os.path.join(db_dir, CATALOG_FILE)):
            raise FileNotFoundError(f"'{db_dir}' is not a database directory "
                                    f"(no {CATALOG_FILE})")
        cat = Catalog(db_dir, BufferPool(4))
        self.tables = {t.name: [ColInfo(c.name, c.type, c.length) for c in t.columns]
                       for t in cat.tables()}

    def columns(self, table: str) -> Optional[List[ColInfo]]:
        return self.tables.get(table)

    def names(self) -> List[str]:
        return list(self.tables)


class OverlayCatalog:
    """A catalog plus tables created earlier in the same script, in memory."""

    def __init__(self, base):
        self.base = base
        self.created: Dict[str, List[ColInfo]] = {}

    def columns(self, table: str) -> Optional[List[ColInfo]]:
        if table in self.created:
            return self.created[table]
        return self.base.columns(table) if self.base is not None else None

    def names(self) -> List[str]:
        return (self.base.names() if self.base is not None else []) + list(self.created)

    def create(self, table: str, cols: List[ColInfo]) -> None:
        self.created[table] = list(cols)


# ---- scopes ------------------------------------------------------------------------
@dataclass
class Entry:
    key: str                      # alias, or table name when there is none
    columns: List[ColInfo]
    pos: Position

    def find(self, name: str) -> Optional[ColInfo]:
        for c in self.columns:
            if c.name == name:
                return c
        return None


class Scope:
    """The FROM clause of one select block: one entry per table reference,
    in FROM order. Keys must be distinct."""

    def __init__(self):
        self.entries: List[Entry] = []

    def add(self, entry: Entry, table_name: Optional[str] = None) -> None:
        for e in self.entries:
            if e.key == entry.key:
                hint = ""
                if table_name is not None and table_name == entry.key:
                    hint = (f"; give each use of {table_name} its own alias, as in "
                            f"FROM {table_name} a, {table_name} b")
                raise NameError_(f"duplicate alias '{entry.key}' in FROM{hint}", entry.pos)
        self.entries.append(entry)

    def keys(self) -> List[str]:
        return [e.key for e in self.entries]

    def lookup(self, ref: A.ColumnRef) -> Tuple[str, ColInfo]:
        """Resolve a column reference to (fully qualified name, column)."""
        if ref.table is not None:
            for e in self.entries:
                if e.key == ref.table:
                    c = e.find(ref.name)
                    if c is None:
                        cols = ", ".join(x.name for x in e.columns)
                        raise NameError_(f"'{e.key}' has no column '{ref.name}' "
                                         f"(its columns: {cols})", ref.pos)
                    return f"{e.key}.{c.name}", c
            raise NameError_(f"unknown table or alias '{ref.table}' in "
                             f"'{ref.text()}' (in scope: {', '.join(self.keys())})", ref.pos)
        hits = [(e, e.find(ref.name)) for e in self.entries]
        hits = [(e, c) for e, c in hits if c is not None]
        if not hits:
            raise NameError_(f"unknown column '{ref.name}' (no table in FROM has it; "
                             f"in scope: {', '.join(self.keys())})", ref.pos)
        if len(hits) > 1:
            cands = ", ".join(f"{e.key}.{c.name}" for e, c in hits)
            raise AmbiguityError(f"column '{ref.name}' is ambiguous: it could be "
                                 f"{cands}; qualify it", ref.pos)
        e, c = hits[0]
        return f"{e.key}.{c.name}", c

    def expand_star(self) -> List[Tuple[str, ColInfo]]:
        """SELECT *: every column of every entry, FROM order then schema order."""
        return [(f"{e.key}.{c.name}", c) for e in self.entries for c in e.columns]


# ---- aggregates -----------------------------------------------------------------------
def default_agg_name(fn: str, arg: Optional[str]) -> str:
    """Output name of an aggregate written without AS (DESIGN_M3.md section 4):
    count_star for COUNT(*), else fn_qualifier_column, e.g. avg_Emp_salary.
    The same rule Module 2's group_by uses for its output schema."""
    if arg is None:
        return "count_star"
    return f"{fn}_{arg.replace('.', '_')}"


def agg_type(call: A.AggCall, arg: Optional[ColInfo], arg_name: Optional[str]) -> ColInfo:
    """Type rules of Section 3.2: SUM and AVG need INT, COUNT takes anything,
    MIN and MAX take either type. Returns the output column type."""
    fn = call.fn

    if fn == "count":
        return ColInfo("", INT)

    if call.arg is None:
        raise AggregateError(
            f"{fn.upper()}(*) is not allowed; only COUNT accepts *",
            call.pos,
        )

    assert arg is not None
    if fn in ("sum", "avg") and arg.type != INT:
        raise TypeError_(f"{fn.upper()} needs an INT column, but {arg_name} is "
                         f"{arg.type_text()}", call.pos)
    if fn == "sum":
        return ColInfo("", INT)
    if fn == "avg":
        return ColInfo("", REAL)
    return ColInfo("", arg.type, arg.length)


def first_aggregate(c: Optional[A.Condition]) -> Optional[A.AggCall]:
    if c is None:
        return None
    for o in A.condition_operands(c):
        if isinstance(o, A.AggCall):
            return o
    return None


def reject_aggregates(c: Optional[A.Condition], clause: str, why: str) -> None:
    agg = first_aggregate(c)
    if agg is not None:
        raise AggregateError(f"aggregate {agg.text()} is not allowed in {clause}{why}",
                             agg.pos)


# ---- comparisons -----------------------------------------------------------------------
def literal_type(o) -> Tuple[str, ColInfo]:
    from .lexer import quote
    if isinstance(o, A.IntLit):
        return str(o.value), ColInfo("", INT)
    return quote(o.value), ColInfo("", VARCHAR)


def check_comparison(cmp: A.Compare, left: Tuple[str, ColInfo],
                     right: Tuple[str, ColInfo]) -> None:
    """Check the types of an ordinary condition."""
    (ln, lt), (rn, rt) = left, right

    if kind(lt.type) != kind(rt.type):
        raise TypeError_(
            f"cannot compare {ln} ({lt.type_text()}) with "
            f"{rn} ({rt.type_text()})",
            cmp.pos,
        )


def check_set_operands(op: A.SetOp, left: Sequence[ColInfo],
                       right: Sequence[ColInfo],
                       lnames: Sequence[str],
                       rnames: Sequence[str]) -> None:
    """Set operations require matching column counts and types."""
    word = op.op.upper().replace("_", " ")

    if len(left) != len(right):
        raise SchemaError(
            f"{word} needs the same number of columns on both sides: "
            f"the left query has {len(left)}, "
            f"the right has {len(right)}",
            op.pos,
        )

    for i, (a, b) in enumerate(zip(left, right), 1):
        if a.type != b.type:
            raise SchemaError(
                f"{word} column {i} has different types: "
                f"{lnames[i-1]} is {a.type_text()} on the left, "
                f"{rnames[i-1]} is {b.type_text()} on the right",
                op.pos,
            )