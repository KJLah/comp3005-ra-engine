"""Schemas, tuple equality, and the in-memory Relation.

A tuple is a plain Python tuple of values. A value is an int, a float or a
str. Two tuples are equal when ``tuple_key`` gives the same key (see below).
Deduplication is always done through that key, never through Python's own
idea of equality, so the definition of "same tuple" is written down here, in
one place.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, Iterable, List, Optional, Sequence, Tuple, Union

Value = Union[int, float, str]
Row = Tuple[Value, ...]

NUM = "number"
STR = "string"


def value_type(v: Value) -> str:
    return STR if isinstance(v, str) else NUM


@dataclass(frozen=True)
class Attribute:
    qualifier: str          # the relation name this attribute is known under
    name: str
    type: Optional[str]     # NUM, STR, or None when unknown (empty column)

    @property
    def qualified(self) -> str:
        return f"{self.qualifier}.{self.name}"


# ---- tuple equality -----------------------------------------------------------
def value_key(v: Value):
    """Our definition of value equality.

    * A number and a string are never equal: 32 and '32' are different values.
    * Numbers compare by numeric value: 32 and 32.0 are the same value.
    * Strings compare character by character and are case-sensitive.
    """
    if isinstance(v, str):
        return ("s", v)
    if isinstance(v, float) and v.is_integer():
        v = int(v)
    return ("n", v)


def tuple_key(row: Row):
    """Two tuples are equal iff they have the same length and their values are
    equal position by position (by value_key)."""
    return tuple(value_key(v) for v in row)


def distinct(rows: Iterable[Row]) -> List[Row]:
    """Remove duplicate tuples, keeping the first occurrence and input order.

    The dict is only a lookup table keyed by *our* tuple_key. Equality is
    decided by tuple_key, not by Python comparing the rows themselves.
    """
    seen: Dict[tuple, Row] = {}
    for row in rows:
        k = tuple_key(row)
        if k not in seen:
            seen[k] = row
    return list(seen.values())


# ---- a materialised relation ----------------------------------------------------
@dataclass
class Relation:
    name: Optional[str]           # None for derived results
    attributes: List[Attribute]
    rows: List[Row]

    def display_names(self) -> List[str]:
        return display_names(self.attributes)


def display_names(attributes: Sequence[Attribute]) -> List[str]:
    """Show the bare name when it is unique in the schema, and the qualified
    name (employeeTable2.DID) when two attributes share a bare name."""
    counts: Dict[str, int] = {}
    for a in attributes:
        counts[a.name] = counts.get(a.name, 0) + 1
    return [a.qualified if counts[a.name] > 1 else a.name for a in attributes]


def format_value(v: Value) -> str:
    return v if isinstance(v, str) else repr(v)


def format_relation(rel: Relation) -> str:
    """Draw the result as a table. An empty result still prints its schema."""
    headers = rel.display_names()
    cells = [[format_value(v) for v in row] for row in rel.rows]
    widths = [len(h) for h in headers]
    for row in cells:
        for i, c in enumerate(row):
            if len(c) > widths[i]:
                widths[i] = len(c)

    def line(values):
        return " | ".join(v.ljust(widths[i]) for i, v in enumerate(values)).rstrip()

    out = [line(headers), "-+-".join("-" * w for w in widths)]
    out.extend(line(row) for row in cells)
    n = len(rel.rows)
    out.append(f"({n} tuple{'s' if n != 1 else ''})")
    return "\n".join(out)
