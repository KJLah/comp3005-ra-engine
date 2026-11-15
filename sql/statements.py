"""CREATE TABLE and INSERT (Module 3, Section 2): checking and applying.

`check_create` and `check_insert` decide whether a statement is valid against
a catalog, with the same error format as queries. `sql compile` and
`sql check` only check, recording created tables in an OverlayCatalog so
that later statements in the same file can use them; the database is never
changed. `sql exec` checks and then applies them through Module 1.
"""
from __future__ import annotations

from typing import List

from storage.record import MAX_VARCHAR
from storage.page import MAX_RECORD

from . import ast as A
from .errors import NameError_, SchemaError, TypeError_
from .resolve import INT, VARCHAR, ColInfo


def check_create(stmt: A.CreateTable, catalog) -> List[ColInfo]:
    if catalog.columns(stmt.name) is not None:
        raise SchemaError(f"table '{stmt.name}' already exists", stmt.pos)
    if stmt.name.lower() == "catalog":
        raise SchemaError("'catalog' is reserved (catalog.db holds the catalog)", stmt.pos)
    seen = set()
    cols = []
    for c in stmt.columns:
        if c.name in seen:
            raise SchemaError(f"column '{c.name}' appears twice in table '{stmt.name}'",
                              c.pos)
        seen.add(c.name)
        if c.type == VARCHAR and not 1 <= c.length <= MAX_VARCHAR:
            raise SchemaError(f"VARCHAR({c.length}) in column '{c.name}': the length "
                              f"must be 1 to {MAX_VARCHAR}", c.pos)
        cols.append(ColInfo(c.name, c.type, c.length))
    return cols


def check_insert(stmt: A.Insert, catalog) -> List[ColInfo]:
    cols = catalog.columns(stmt.table)
    if cols is None:
        known = ", ".join(sorted(catalog.names())) or "(none)"
        raise NameError_(f"unknown table '{stmt.table}' (tables: {known})",
                         stmt.table_pos or stmt.pos)
    if len(stmt.values) != len(cols):
        raise SchemaError(f"table '{stmt.table}' has {len(cols)} columns but "
                          f"{len(stmt.values)} values were given", stmt.pos)
    for c, v in zip(cols, stmt.values):
        if c.type == INT:
            if not isinstance(v, A.IntLit):
                raise TypeError_(f"column '{c.name}' is INT but the value is a string",
                                 v.pos)
            if not -2**31 <= v.value < 2**31:
                raise TypeError_(f"column '{c.name}': {v.value} does not fit in a 32-bit "
                                 f"INT", v.pos)
        else:
            if not isinstance(v, A.StrLit):
                raise TypeError_(f"column '{c.name}' is {c.type_text()} but the value is "
                                 f"an integer", v.pos)
            n = len(v.value.encode("utf-8"))
            if n > c.length:
                raise TypeError_(f"column '{c.name}' is {c.type_text()} but the value is "
                                 f"{n} bytes long", v.pos)
    size = sum(
        4 if c.type == INT
        else 2 + len(v.value.encode("utf-8"))
        for c, v in zip(cols, stmt.values)
    )

    if size > MAX_RECORD:
        raise SchemaError(
            f"table '{stmt.table}': record is {size} bytes; "
            f"maximum is {MAX_RECORD} bytes",
            stmt.pos,
        )
    return cols