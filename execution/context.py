"""Everything one plan run shares (Module 2, Sections 1.1, 1.2 and 1.4).

ExecContext holds:
  * the one BufferPool of exactly B frames (the catalog reads through it too),
  * the frame budget: which operator holds how many of the B frames,
  * the temporary files, so that "no temporary files remain" can be checked.

Counter attribution (Section 1.4). Module 1's pool counts reads and writes for
the whole pool, but Module 2 wants them per operator. Every file an operator
reads or writes is opened through its own OwnedDisk, a DiskManager that also
adds each read_page / write_page call to that operator's counters. A seqscan
opens its own handle on its table file; a sort owns its run files. So a write
caused by evicting a dirty page of a sort's run lands on the sort, whoever
caused the eviction, and catalog reads land on nobody.

Temporary pages are written through and never cached: a page is written as
soon as it is full and dropped from the pool, and a page that has been read is
dropped when it is unpinned. Otherwise a page still sitting in the pool would
give a hit instead of a read, and a file deleted before its pages were evicted
would never be counted as written, so measured I/O would drift below the
formulas for reasons that have nothing to do with the algorithm.
"""
from __future__ import annotations

import os
import shutil
from typing import Dict, Iterator, List, Optional, Tuple

from storage.buffer import BufferPool
from storage.catalog import Catalog
from storage.disk import DiskManager, MemoryDisk
from storage.page import SlottedPage

from .errors import BudgetError, ExecError
from .schema import Row, Schema, decode, encode

TMP_DIR = "_tmp"


class Counters:
    __slots__ = ("tuples_out", "comparisons", "page_reads", "page_writes")

    def __init__(self):
        self.tuples_out = self.comparisons = self.page_reads = self.page_writes = 0

    def as_dict(self) -> Dict[str, int]:
        return {"tuples_out": self.tuples_out, "comparisons": self.comparisons,
                "page_reads": self.page_reads, "page_writes": self.page_writes}


class OwnedDisk(DiskManager):
    """A file handle whose page I/O is charged to one operator."""

    def __init__(self, path: str, counters: Counters):
        super().__init__(path)
        self.counters = counters

    def read_page(self, page_id: int) -> bytes:
        self.counters.page_reads += 1
        return super().read_page(page_id)

    def write_page(self, page_id: int, data: bytes) -> None:
        self.counters.page_writes += 1
        super().write_page(page_id, data)


class ScratchDisk(MemoryDisk):
    """Where an operator's working frames (a BNL outer block, a hash table)
    are allocated. These pages are never meant to reach disk: they are
    discarded as soon as they are unpinned. If one ever were written, the
    write would show up in the operator's counters, so a bug is visible."""

    def __init__(self, label: str, counters: Counters):
        super().__init__(label)
        self.counters = counters

    def write_page(self, page_id: int, data: bytes) -> None:
        self.counters.page_writes += 1
        super().write_page(page_id, data)


class ExecContext:
    def __init__(self, db_dir: str, frames: int, policy: str = "lru"):
        if frames < 1:
            raise ExecError("--frames must be at least 1")
        self.db_dir = db_dir
        self.frames = frames
        self.pool = BufferPool(frames, policy)
        # The catalog is read at bind, before the plan runs, through its own
        # 2-frame pool (Module 1's heap scan pins a directory page and a data
        # page). Metadata lookups therefore never take frames from the plan's
        # B, and B = 1 still works for a single seqscan. See DESIGN_M2.md §3.
        self.catalog = Catalog(db_dir, BufferPool(2, policy))
        self.reserved = 0
        self.peak_reserved = 0
        self.holders: Dict[str, int] = {}
        self.temp_files: List["TempFile"] = []
        self._next_temp = 0
        self.tmp_dir = os.path.join(db_dir, TMP_DIR)

    # ---- frame budget (Section 1.2) --------------------------------------------
    def free(self) -> int:
        return self.frames - self.reserved

    def reserve(self, who: str, n: int) -> None:
        if n < 0:
            raise ValueError("negative reservation")
        if n > self.free():
            raise BudgetError(f"{who} needs {n} frame(s) but only {self.free()} of "
                              f"B = {self.frames} are left "
                              f"(held: {self._holding()})")
        self.reserved += n
        self.holders[who] = self.holders.get(who, 0) + n
        self.peak_reserved = max(self.peak_reserved, self.reserved)

    def release(self, who: str, n: int) -> None:
        have = self.holders.get(who, 0)
        if n > have:
            raise ValueError(f"{who} releases {n} frames but holds {have}")
        self.reserved -= n
        if have == n:
            del self.holders[who]
        else:
            self.holders[who] = have - n

    def _holding(self) -> str:
        return ", ".join(f"{k}={v}" for k, v in self.holders.items()) or "nothing"

    # ---- disks -------------------------------------------------------------------
    def table_disk(self, file: str, counters: Counters) -> OwnedDisk:
        return OwnedDisk(os.path.join(self.db_dir, file), counters)

    def scratch_disk(self, label: str, counters: Counters) -> ScratchDisk:
        return ScratchDisk(f"<scratch {label}>", counters)

    def new_temp(self, label: str, schema: Schema, counters: Counters) -> "TempFile":
        os.makedirs(self.tmp_dir, exist_ok=True)
        self._next_temp += 1
        path = os.path.join(self.tmp_dir, f"t{self._next_temp:05d}_{label}.db")
        t = TempFile(self, path, schema, counters)
        self.temp_files.append(t)
        return t

    def leftover_temps(self) -> List[str]:
        if not os.path.isdir(self.tmp_dir):
            return []
        return sorted(os.listdir(self.tmp_dir))

    def cleanup(self) -> None:
        """Remove the temp directory if it is empty (it is, after a clean close)."""
        if os.path.isdir(self.tmp_dir) and not os.listdir(self.tmp_dir):
            shutil.rmtree(self.tmp_dir)


class TempFile:
    """A temporary file of slotted pages holding encoded tuples (a sorted run,
    a hash partition, a materialised input). Pages are numbered 0, 1, 2 ...
    with no directory page: the file is only ever read front to back or by
    page number, so a directory would cost a read for nothing."""

    def __init__(self, ctx: ExecContext, path: str, schema: Schema, counters: Counters):
        self.ctx = ctx
        self.pool = ctx.pool
        self.schema = schema
        self.disk = OwnedDisk(path, counters)
        self.path = path
        self.pages = 0          # pages written so far
        self.tuples = 0
        self.deleted = False

    # ---- writing -----------------------------------------------------------------
    def new_page(self) -> Tuple[int, SlottedPage]:
        """A pinned, empty page that will become page `self.pages` of the file."""
        pid, data = self.pool.new_page(self.disk)
        return pid, SlottedPage.init(data, pid)

    def finish_page(self, pid: int, count: int) -> None:
        """Write a filled page now (one WRITE) and drop it from the pool."""
        self.pool.unpin_page(self.disk, pid, True)
        self.pool.flush_page(self.disk, pid)
        self.pool.discard_page(self.disk, pid)
        self.pages += 1
        self.tuples += count

    def writer(self) -> "RunWriter":
        return RunWriter(self)

    # ---- reading -----------------------------------------------------------------
    def read_page(self, pid: int) -> List[Row]:
        """Read one page (one READ), decode it, and drop it from the pool. The
        decoded list is one page of tuples, i.e. one frame's worth."""
        data = self.pool.fetch_page(self.disk, pid)
        try:
            page = SlottedPage(data)
            rows = [decode(self.schema, page.get(s)) for s in range(page.slot_count)]
        finally:
            self.pool.unpin_page(self.disk, pid, False)
            self.pool.discard_page(self.disk, pid)
        return rows

    def rows(self, start: int = 0, end: Optional[int] = None) -> Iterator[Row]:
        """Every tuple of pages [start, end), one page in memory at a time."""
        end = self.pages if end is None else end
        for pid in range(start, end):
            for r in self.read_page(pid):
                yield r

    # ---- deleting ----------------------------------------------------------------
    def delete(self) -> None:
        if self.deleted:
            return
        self.pool.discard(self.disk)
        if os.path.exists(self.path):
            os.remove(self.path)
        self.deleted = True
        if self in self.ctx.temp_files:
            self.ctx.temp_files.remove(self)


class RunWriter:
    """Appends tuples to a TempFile through one pinned frame."""

    def __init__(self, temp: TempFile):
        self.temp = temp
        self.pid: Optional[int] = None
        self.page: Optional[SlottedPage] = None
        self.count = 0

    def add(self, row: Row) -> None:
        rec = encode(self.temp.schema, row)
        if self.page is None:
            self.pid, self.page = self.temp.new_page()
            self.count = 0
        if self.page.insert(rec) is None:
            self.temp.finish_page(self.pid, self.count)
            self.pid, self.page = self.temp.new_page()
            self.count = 0
            if self.page.insert(rec) is None:
                raise ExecError(f"a tuple of {len(rec)} bytes does not fit in a page")
        self.count += 1

    def close(self) -> None:
        """Write the last, partly filled page (if any)."""
        if self.page is not None:
            self.temp.finish_page(self.pid, self.count)
            self.page = None

    def abandon(self) -> None:
        """Unpin and drop the current page without writing it (error path)."""
        if self.page is not None:
            self.temp.pool.unpin_page(self.temp.disk, self.pid, False)
            self.temp.pool.discard_page(self.temp.disk, self.pid)
            self.page = None
