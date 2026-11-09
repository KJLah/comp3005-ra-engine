"""Record encoding (Module 1, section 2).

A record is the concatenation of its field encodings, in schema order, with
no header and no null bitmap. All integers are little-endian.

    INT          4 bytes, signed two's complement
    VARCHAR(n)   2-byte unsigned length L, then L bytes of UTF-8, L <= n <= 255
"""
from __future__ import annotations

import struct
from dataclasses import dataclass
from typing import List, Optional, Sequence, Tuple, Union

from .errors import RecordError

INT = "INT"
VARCHAR = "VARCHAR"
MAX_VARCHAR = 255

Value = Union[int, str]


@dataclass(frozen=True)
class Column:
    name: str
    type: str                 # INT or VARCHAR
    length: Optional[int] = None   # n for VARCHAR(n), None for INT

    def to_json(self) -> list:
        """The shape required by `scan --json`: ["a","INT"] or ["b","VARCHAR",20]."""
        if self.type == INT:
            return [self.name, INT]
        return [self.name, VARCHAR, self.length]


def parse_schema(text: str) -> List[Column]:
    """Parse "a:INT,b:VARCHAR(20),c:INT" by hand (no regex)."""
    columns: List[Column] = []
    for part in text.split(","):
        name, sep, typ = part.strip().partition(":")
        name, typ = name.strip(), typ.strip().upper()
        if not sep or not name:
            raise RecordError(f"bad column '{part.strip()}': expected name:TYPE")
        if typ == INT:
            columns.append(Column(name, INT))
        elif typ.startswith("VARCHAR(") and typ.endswith(")"):
            digits = typ[len("VARCHAR("):-1]
            if not digits.isdigit():
                raise RecordError(f"bad VARCHAR length in '{part.strip()}'")
            n = int(digits)
            if not 1 <= n <= MAX_VARCHAR:
                raise RecordError(f"VARCHAR({n}) in column '{name}': n must be 1..{MAX_VARCHAR}")
            columns.append(Column(name, VARCHAR, n))
        else:
            raise RecordError(f"unknown type '{typ}' for column '{name}'")
    if len({c.name for c in columns}) != len(columns):
        raise RecordError("column names must be distinct")
    return columns


def encode(schema: Sequence[Column], values: Sequence[Value]) -> bytes:
    if len(values) != len(schema):
        raise RecordError(f"expected {len(schema)} values, got {len(values)}")
    out = bytearray()
    for col, v in zip(schema, values):
        if col.type == INT:
            if type(v) is not int or not -2**31 <= v < 2**31:
                raise RecordError(f"column '{col.name}': {v!r} is not a 32-bit INT")
            out += struct.pack("<i", v)
        else:
            if not isinstance(v, str):
                raise RecordError(f"column '{col.name}': {v!r} is not a string")
            data = v.encode("utf-8")
            if len(data) > col.length:
                raise RecordError(f"column '{col.name}': {len(data)} bytes is longer "
                                  f"than VARCHAR({col.length})")
            out += struct.pack("<H", len(data)) + data
    return bytes(out)


def decode(schema: Sequence[Column], data: bytes) -> Tuple[Value, ...]:
    values = []
    pos = 0
    for col in schema:
        if col.type == INT:
            (v,) = struct.unpack_from("<i", data, pos)
            pos += 4
        else:
            (n,) = struct.unpack_from("<H", data, pos)
            pos += 2
            v = data[pos:pos + n].decode("utf-8")
            pos += n
        values.append(v)
    if pos != len(data):
        raise RecordError(f"record has {len(data) - pos} trailing bytes")
    return tuple(values)
