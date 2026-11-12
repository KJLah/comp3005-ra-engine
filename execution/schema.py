"""Schemas, tuple encoding and comparisons (Module 2, Section 1.3).

A schema is an ordered list of qualified column names ("R.a") with types.
Aggregate outputs such as "count_star" have no qualifier.

Types are INT and VARCHAR(n) from Module 1, plus REAL, which only an AVG
produces. REAL never reaches a table file; it exists so that a group_by
output can go through a sort or a hash partition (temporary files) like any
other tuple. It is encoded as an 8-byte little-endian IEEE double.

Comparisons follow Bonus Project 1: same type only. VARCHAR order is byte-wise
on the UTF-8 encoding, which is the same as Python's str order, because UTF-8
preserves code point order. So plain < on str is correct and no encoding is
needed to compare.
"""
from __future__ import annotations

import struct
from dataclasses import dataclass
from typing import Callable, List, Optional, Sequence, Tuple

from ra_engine.relation import NUM, STR, Attribute
from storage.record import INT, VARCHAR, Column

from .errors import BindError

REAL = "REAL"
BIGINT = "BIGINT"     # SUM output: an INT that may exceed 32 bits (8 bytes)
Row = Tuple


@dataclass(frozen=True)
class Col:
    name: str                      # qualified, e.g. "R.a"; or "count_star"
    type: str                      # INT, VARCHAR or REAL
    length: Optional[int] = None   # n of VARCHAR(n)

    @property
    def qualifier(self) -> str:
        q, dot, _ = self.name.rpartition(".")
        return q if dot else ""

    @property
    def short(self) -> str:
        return self.name.rpartition(".")[2]

    def to_json(self) -> list:
        """The shape of `run --json`: ["R.a","INT"] or ["S.c","VARCHAR",20]."""
        if self.type == VARCHAR:
            return [self.name, VARCHAR, self.length]
        if self.type == BIGINT:
            return [self.name, INT]
        return [self.name, self.type]

    def renamed(self, name: str) -> "Col":
        return Col(name, self.type, self.length)


class Schema:
    def __init__(self, cols: Sequence[Col], where: str = "plan"):
        self.cols: List[Col] = list(cols)
        seen = set()
        for c in self.cols:
            if c.name in seen:
                raise BindError(f"{where}: column '{c.name}' appears twice in one schema "
                                f"(Section 1.3); a self join needs two different "
                                f"table names")
            seen.add(c.name)

    @classmethod
    def of_table(cls, table: str, columns: Sequence[Column]) -> "Schema":
        return cls([Col(f"{table}.{c.name}", c.type, c.length) for c in columns],
                   f"table {table}")

    def __len__(self) -> int:
        return len(self.cols)

    def names(self) -> List[str]:
        return [c.name for c in self.cols]

    def types(self) -> List[str]:
        return [c.type for c in self.cols]

    def concat(self, other: "Schema", where: str) -> "Schema":
        return Schema(self.cols + other.cols, where)

    def index(self, name: str, where: str) -> int:
        """Position of a column. A bare name ("a") is allowed if it is unique."""
        for i, c in enumerate(self.cols):
            if c.name == name:
                return i
        matches = [i for i, c in enumerate(self.cols) if c.short == name]
        if "." not in name and len(matches) == 1:
            return matches[0]
        known = ", ".join(self.names()) or "(none)"
        if len(matches) > 1:
            raise BindError(f"{where}: column '{name}' is ambiguous; qualify it "
                            f"(columns: {known})")
        raise BindError(f"{where}: unknown column '{name}'; columns: {known}")

    def attributes(self) -> List[Attribute]:
        """The same schema as Bonus 1 attributes, so its condition compiler can be
        reused. INT and REAL are both numbers there; VARCHAR is a string."""
        return [Attribute(c.qualifier, c.short, STR if c.type == VARCHAR else NUM)
                for c in self.cols]

    def to_json(self) -> list:
        return [c.to_json() for c in self.cols]


# ---- tuple encoding (temporary files and working frames) ---------------------
def encode(schema: Schema, row: Row) -> bytes:
    out = bytearray()
    for c, v in zip(schema.cols, row):
        if c.type == INT:
            out += struct.pack("<i", v)
        elif c.type == REAL:
            out += struct.pack("<d", v)
        elif c.type == BIGINT:
            out += struct.pack("<q", v)
        else:
            b = v.encode("utf-8")
            out += struct.pack("<H", len(b)) + b
    return bytes(out)


def decode(schema: Schema, data: bytes) -> Row:
    vals = []
    pos = 0
    for c in schema.cols:
        if c.type == INT:
            vals.append(struct.unpack_from("<i", data, pos)[0])
            pos += 4
        elif c.type == REAL:
            vals.append(struct.unpack_from("<d", data, pos)[0])
            pos += 8
        elif c.type == BIGINT:
            vals.append(struct.unpack_from("<q", data, pos)[0])
            pos += 8
        else:
            (n,) = struct.unpack_from("<H", data, pos)
            pos += 2
            vals.append(data[pos:pos + n].decode("utf-8"))
            pos += n
    return tuple(vals)


# ---- comparisons -----------------------------------------------------------------
def check_same_type(a: Col, b: Col, where: str) -> None:
    """Section 1.3: same type only, for every operator including equality.
    INT and REAL are both numeric; INT against VARCHAR is an error."""
    num = (INT, REAL, BIGINT)
    if (a.type in num) != (b.type in num):
        raise BindError(f"{where}: cannot compare {a.name} ({a.type}) with "
                        f"{b.name} ({b.type})")


def cmp_values(a, b) -> int:
    return -1 if a < b else (1 if a > b else 0)


class KeyComparator:
    """Compares two rows on a list of (position, ascending) keys, counting one
    comparison per call (Section 1.4: "compared two keys"). A multi-column
    key is one comparison, the same way a compound condition is one
    evaluation in filter."""

    def __init__(self, keys: Sequence[Tuple[int, bool]], counter: Callable[[int], None]):
        self.keys = list(keys)
        self.count = counter

    def __call__(self, r: Row, s: Row) -> int:
        self.count(1)
        for i, asc in self.keys:
            c = cmp_values(r[i], s[i])
            if c:
                return c if asc else -c
        return 0


class PairComparator:
    """Compares a left row to a right row on matching key positions (joins)."""

    def __init__(self, left_keys: Sequence[int], right_keys: Sequence[int],
                 counter: Callable[[int], None]):
        self.pairs = list(zip(left_keys, right_keys))
        self.count = counter

    def __call__(self, r: Row, s: Row) -> int:
        self.count(1)
        for i, j in self.pairs:
            c = cmp_values(r[i], s[j])
            if c:
                return c
        return 0


# ---- deterministic hashing ------------------------------------------------------
_FNV_OFFSET = 0xCBF29CE484222325
_FNV_PRIME = 0x100000001B3
_MASK = 0xFFFFFFFFFFFFFFFF


def stable_hash(values: Sequence, seed: int) -> int:
    """FNV-1a over a canonical byte form of the values, mixed with a seed.

    Python's hash() of a str changes from run to run (PYTHONHASHSEED), which
    would break "same plan, same B, same counters" (Section 1.2). h1 at
    depth d is stable_hash(key, d); h2 for the in-memory table uses a seed no
    partitioning level uses."""
    h = (_FNV_OFFSET ^ (seed * 0x9E3779B97F4A7C15)) & _MASK
    for v in values:
        if isinstance(v, str):
            data = b"s" + v.encode("utf-8") + b"\x00"
        elif isinstance(v, float) and v.is_integer():
            data = b"i" + str(int(v)).encode() + b"\x00"   # 2.0 and 2 hash alike
        else:
            data = b"i" + str(v).encode() + b"\x00"
        for byte in data:
            h ^= byte
            h = (h * _FNV_PRIME) & _MASK
    # final avalanche so that low bits (used by mod P) depend on every byte
    h ^= h >> 33
    h = (h * 0xFF51AFD7ED558CCD) & _MASK
    h ^= h >> 33
    return h
