"""Catalog (Module 1, section 6). The on-page format is ours; see DESIGN_M1.md.

catalog.db is itself a heap file (section 4), so it is read and written
through the buffer pool like any table. Each table is one catalog record:

    offset  size        field
         0     1        format version (1)
         1     2 + n    table name        (uint16 length, UTF-8)
       ...     2 + n    data file name    (uint16 length, UTF-8)
       ...     4        number of pages   uint32
       ...     4        number of live tuples  uint32
       ...     2        column count      uint16
               per column:
                   2 + n  column name   (uint16 length, UTF-8)
                   1      type          0 = INT, 1 = VARCHAR
                   2      length        n of VARCHAR(n), 0 for INT
       ...     2 + n    statistics       (uint16 length, UTF-8 JSON; "{}" now)

The trailing statistics field is the room left for Module 4: it can grow
(histograms, distinct counts) without changing anything before it, and the
version byte lets a later format be told apart.
"""
from __future__ import annotations

import json
import os
import struct
from dataclasses import dataclass, field
from typing import Dict, Iterator, List, Optional, Tuple

from .buffer import BufferPool
from .disk import DiskManager
from .errors import CatalogError
from .heap import HeapFile
from .record import INT, VARCHAR, Column

CATALOG_FILE = "catalog.db"
FORMAT_VERSION = 1
TYPE_CODES = {INT: 0, VARCHAR: 1}
CODE_TYPES = {0: INT, 1: VARCHAR}


@dataclass
class TableInfo:
    name: str
    columns: List[Column]
    file: str
    num_pages: int = 0
    num_tuples: int = 0
    stats: Dict = field(default_factory=dict)      # Module 4 fills this


# ---- encoding one catalog record ------------------------------------------------
def _put_str(out: bytearray, s: str) -> None:
    b = s.encode("utf-8")
    out += struct.pack("<H", len(b)) + b


def _get_str(data: bytes, pos: int) -> Tuple[str, int]:
    (n,) = struct.unpack_from("<H", data, pos)
    pos += 2
    return data[pos:pos + n].decode("utf-8"), pos + n


def encode_info(t: TableInfo) -> bytes:
    out = bytearray(struct.pack("<B", FORMAT_VERSION))
    _put_str(out, t.name)
    _put_str(out, t.file)
    out += struct.pack("<IIH", t.num_pages, t.num_tuples, len(t.columns))
    for c in t.columns:
        _put_str(out, c.name)
        out += struct.pack("<BH", TYPE_CODES[c.type], c.length or 0)
    _put_str(out, json.dumps(t.stats, sort_keys=True))
    return bytes(out)


def decode_info(data: bytes) -> TableInfo:
    if data[0] != FORMAT_VERSION:
        raise CatalogError(f"catalog record has unknown format version {data[0]}")
    name, pos = _get_str(data, 1)
    file, pos = _get_str(data, pos)
    pages, tuples, ncols = struct.unpack_from("<IIH", data, pos)
    pos += 10
    cols = []
    for _ in range(ncols):
        cname, pos = _get_str(data, pos)
        code, length = struct.unpack_from("<BH", data, pos)
        pos += 3
        typ = CODE_TYPES[code]
        cols.append(Column(cname, typ, length if typ == VARCHAR else None))
    stats_text, pos = _get_str(data, pos)
    return TableInfo(name, cols, file, pages, tuples, json.loads(stats_text))


def check_table_name(name: str) -> None:
    ok = name and (name[0].isalpha() or name[0] == "_") and all(
        ch.isalnum() or ch == "_" for ch in name) and name.isascii()
    if not ok:
        raise CatalogError(f"bad table name '{name}': use letters, digits and _ "
                           f"(it becomes the file name {name}.db)")
    if name.lower() == "catalog":
        raise CatalogError("'catalog' is reserved (catalog.db holds the catalog)")


# ---- the catalog ------------------------------------------------------------------
class Catalog:
    """All tables of one database directory, sharing one buffer pool.

    One DiskManager object is kept per file, because the buffer pool tells
    files apart by their DiskManager (section 5 decision 1).
    """

    def __init__(self, db_dir: str, pool: BufferPool):
        self.db_dir = db_dir
        self.pool = pool
        self._disks: Dict[str, DiskManager] = {}
        disk = self.disk(CATALOG_FILE)
        if disk.num_pages == 0:
            self.heap = HeapFile.create(pool, disk, "catalog")
        else:
            self.heap = HeapFile(pool, disk, "catalog")

    def disk(self, file: str) -> DiskManager:
        if file not in self._disks:
            self._disks[file] = DiskManager(os.path.join(self.db_dir, file))
        return self._disks[file]

    # ---- reading ---------------------------------------------------------------
    def _entries(self) -> Iterator[Tuple[Tuple[int, int], TableInfo]]:
        for rid, rec in self.heap.scan():
            yield rid, decode_info(rec)

    def _find(self, name: str) -> Optional[Tuple[Tuple[int, int], TableInfo]]:
        for rid, info in self._entries():
            if info.name == name:
                return rid, info
        return None

    def tables(self) -> List[TableInfo]:
        return [info for _, info in self._entries()]

    def get(self, name: str) -> TableInfo:
        found = self._find(name)
        if found is None:
            known = ", ".join(t.name for t in self.tables()) or "(none)"
            raise CatalogError(f"unknown table '{name}'; tables: {known}")
        return found[1]

    def open_heap(self, name: str) -> HeapFile:
        info = self.get(name)
        return HeapFile(self.pool, self.disk(info.file), info.name)

    # ---- writing ---------------------------------------------------------------
    def create_table(self, name: str, columns: List[Column]) -> TableInfo:
        check_table_name(name)
        if self._find(name) is not None:
            raise CatalogError(f"table '{name}' already exists")
        file = f"{name}.db"
        disk = self.disk(file)
        if disk.num_pages != 0:
            raise CatalogError(f"{file} already exists in {self.db_dir} but is not "
                               f"in the catalog")
        heap = HeapFile.create(self.pool, disk, name)
        info = TableInfo(name, list(columns), file, heap.page_count(), 0)
        self.heap.insert(encode_info(info))
        return info

    def update(self, info: TableInfo) -> None:
        """Replace a table's catalog record (page/tuple counts, statistics)."""
        found = self._find(info.name)
        if found is None:
            raise CatalogError(f"unknown table '{info.name}'")
        rid, _ = found
        self.heap.delete(rid)
        self.heap.insert(encode_info(info))

    def refresh_counts(self, name: str) -> TableInfo:
        """Recount pages and live tuples from the table file and store them."""
        info = self.get(name)
        heap = self.open_heap(name)
        info.num_pages = heap.page_count()
        info.num_tuples = heap.live_count()
        self.update(info)
        return info

    def flush(self) -> None:
        self.pool.flush_all()
