"""CSV loader (Module 1, section 6). Hand-written, character by character.

Format: no header line, one record per line, fields separated by commas.
A field containing a comma must be wrapped in double quotes; inside quotes a
doubled quote "" stands for one ". Quoted fields cannot span lines.

Choices (see DESIGN_M1.md section 7):
  * the whole file is checked before anything is inserted, so a bad line
    leaves the table unchanged ("the load stops");
  * INT fields may have spaces around the number; VARCHAR fields are kept
    exactly as written (spaces included);
  * a trailing newline at the end of the file is fine; any other empty line
    is an error (it would be a record with the wrong number of fields).

The loader never opens a file: the caller passes the lines.
"""
from __future__ import annotations

from typing import List, Sequence, Tuple

from .catalog import Catalog
from .errors import LoadError, RecordError
from .page import MAX_RECORD
from .record import INT, Column, Value, encode


def split_line(line: str, line_no: int) -> List[str]:
    """Split one CSV line into raw field strings."""
    fields: List[str] = []
    cur: List[str] = []
    i, n = 0, len(line)
    at_field_start = True
    while i < n:
        ch = line[i]
        if at_field_start and ch == '"':
            i += 1
            while True:
                if i >= n:
                    raise LoadError(f"line {line_no}, column {len(fields) + 1}: "
                                    f"unterminated quoted field")
                if line[i] == '"':
                    if i + 1 < n and line[i + 1] == '"':
                        cur.append('"')
                        i += 2
                        continue
                    i += 1
                    break
                cur.append(line[i])
                i += 1
            if i < n and line[i] != ",":
                raise LoadError(f"line {line_no}, column {len(fields) + 1}: "
                                f"unexpected {line[i]!r} after a closing quote")
            at_field_start = False
            continue
        if ch == ",":
            fields.append("".join(cur))
            cur = []
            at_field_start = True
        else:
            if ch == '"':
                raise LoadError(f"line {line_no}, column {len(fields) + 1}: a quote inside "
                                f"an unquoted field (wrap the field in quotes and double it)")
            cur.append(ch)
            at_field_start = False
        i += 1
    fields.append("".join(cur))
    return fields


def parse_int(text: str):
    t = text.strip(" \t")
    body = t[1:] if t[:1] in ("-", "+") else t
    if not body or not all("0" <= ch <= "9" for ch in body):
        return None
    v = int(t)
    return v if -2**31 <= v < 2**31 else None


def convert(fields: Sequence[str], columns: Sequence[Column], line_no: int) -> Tuple[Value, ...]:
    if len(fields) != len(columns):
        raise LoadError(f"line {line_no}: expected {len(columns)} fields, found {len(fields)}")
    values: List[Value] = []
    for col, raw in zip(columns, fields):
        if col.type == INT:
            v = parse_int(raw)
            if v is None:
                raise LoadError(f"line {line_no}, column '{col.name}': {raw!r} is not "
                                f"a 32-bit INT")
            values.append(v)
        else:
            nbytes = len(raw.encode("utf-8"))
            if nbytes > col.length:
                raise LoadError(f"line {line_no}, column '{col.name}': value is {nbytes} "
                                f"bytes, longer than VARCHAR({col.length})")
            values.append(raw)
    return tuple(values)


def _records(lines: Sequence[str], table: str, columns: Sequence[Column]):
    """Yield (line_no, encoded record); raises LoadError on the first bad line."""
    last = len(lines)
    for idx, line in enumerate(lines):
        line_no = idx + 1
        if line.endswith("\r"):
            line = line[:-1]
        if line == "" and line_no == last:
            continue                          # trailing newline at end of file
        rec = encode(columns, convert(split_line(line, line_no), columns, line_no))
        if len(rec) > MAX_RECORD:
            raise LoadError(f"table {table}, line {line_no}: record is {len(rec)} bytes, "
                            f"longer than the {MAX_RECORD}-byte maximum")
        yield line_no, rec


def load_csv(catalog: Catalog, table: str, lines: Sequence[str]) -> int:
    """Load every line into `table`. Nothing is inserted unless every line is
    valid. Returns the number of records inserted, and updates the catalog's
    page and tuple counts."""
    info = catalog.get(table)
    for _ in _records(lines, table, info.columns):     # pass 1: check everything
        pass
    heap = catalog.open_heap(table)
    count = 0
    for line_no, rec in _records(lines, table, info.columns):   # pass 2: insert
        try:
            heap.insert(rec)
        except RecordError as e:              # cannot happen after pass 1; kept for safety
            raise LoadError(f"table {table}, line {line_no}: {e}")
        count += 1
    catalog.refresh_counts(table)
    catalog.flush()
    return count
