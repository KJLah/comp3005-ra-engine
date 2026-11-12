"""Heap file (Module 1, section 4).

A heap file is a linked list of directory pages. Page 0 of every table file is
the first directory page. Each directory entry names one data page (a slotted
page) and its free space.

    directory page
    offset size field
         0    4 page_id        uint32
         4    4 next_dir_page  uint32, 0xFFFFFFFF if none
         8    2 entry_count    uint16
        10    6 entry 0        uint32 data_page_id, uint16 free_bytes
        16    6 entry 1 ...
    capacity = floor((4096 - 10) / 6) = 681 entries

free_bytes is the data page's free space as defined in section 3:
free_end - (8 + 4 * slot_count). An insert of L bytes fits iff free_bytes >= L + 4.

Pinning: no method ever holds more than one directory page and one data page,
so the heap file works with a pool of B >= 2 frames.
"""
from __future__ import annotations

import struct
from typing import Iterator, Tuple

from .buffer import BufferPool
from .errors import PageError, RecordError
from .page import MAX_RECORD, PAGE_SIZE, SLOT_SIZE, SlottedPage

NO_PAGE = 0xFFFFFFFF
DIR_HEADER = 10
DIR_ENTRY = 6
DIR_CAPACITY = (PAGE_SIZE - DIR_HEADER) // DIR_ENTRY      # 681

Rid = Tuple[int, int]


class DirectoryPage:
    """A view on a frame holding a directory page (no copying)."""

    def __init__(self, data: bytearray):
        self.data = data

    @classmethod
    def init(cls, data: bytearray, page_id: int) -> "DirectoryPage":
        data[:] = bytes(PAGE_SIZE)
        struct.pack_into("<IIH", data, 0, page_id, NO_PAGE, 0)
        return cls(data)

    @property
    def page_id(self) -> int:
        return struct.unpack_from("<I", self.data, 0)[0]

    @property
    def next_dir(self) -> int:
        return struct.unpack_from("<I", self.data, 4)[0]

    @next_dir.setter
    def next_dir(self, v: int) -> None:
        struct.pack_into("<I", self.data, 4, v)

    @property
    def entry_count(self) -> int:
        return struct.unpack_from("<H", self.data, 8)[0]

    def entry(self, i: int) -> Tuple[int, int]:
        return struct.unpack_from("<IH", self.data, DIR_HEADER + DIR_ENTRY * i)

    def set_free(self, i: int, free_bytes: int) -> None:
        struct.pack_into("<H", self.data, DIR_HEADER + DIR_ENTRY * i + 4, free_bytes)

    def add_entry(self, data_page_id: int, free_bytes: int) -> int:
        i = self.entry_count
        if i >= DIR_CAPACITY:
            raise PageError(f"directory page {self.page_id} is full")
        struct.pack_into("<IH", self.data, DIR_HEADER + DIR_ENTRY * i,
                         data_page_id, free_bytes)
        struct.pack_into("<H", self.data, 8, i + 1)
        return i


class HeapFile:
    def __init__(self, pool: BufferPool, disk, name: str = "table"):
        self.pool = pool
        self.disk = disk
        self.name = name

    @classmethod
    def create(cls, pool: BufferPool, disk, name: str = "table") -> "HeapFile":
        """Format an empty file: page 0 becomes the first directory page."""
        if disk.num_pages != 0:
            raise PageError(f"{disk.path}: cannot create a heap file in a non-empty file")
        page_id, data = pool.new_page(disk)
        DirectoryPage.init(data, page_id)
        pool.unpin_page(disk, page_id, True)
        return cls(pool, disk, name)

    # ---- insert (section 4.1) ------------------------------------------------
    def insert(self, record: bytes) -> Rid:
        n = len(record)

        if n > MAX_RECORD:
            raise RecordError(
                f"table {self.name}: record of {n} bytes is longer than "
                f"the {MAX_RECORD}-byte maximum "
                f"(4088 free bytes minus a {SLOT_SIZE}-byte slot)"
            )

        need = n + SLOT_SIZE
        held = {}  # page_id -> whether this insertion changed the page

        def fetch(pid):
            data = self.pool.fetch_page(self.disk, pid)
            held[pid] = False
            return data

        def allocate():
            pid, data = self.pool.new_page(self.disk)
            held[pid] = True
            return pid, data

        def release(pid):
            self.pool.unpin_page(self.disk, pid, held[pid])
            del held[pid]

        try:
            dir_id = 0

            while True:
                d = DirectoryPage(fetch(dir_id))

                for i in range(d.entry_count):
                    data_id, free = d.entry(i)

                    if free >= need:
                        page = SlottedPage(fetch(data_id))
                        slot = page.insert(record)

                        if slot is None:
                            raise PageError(
                                "directory free-space entry is inconsistent"
                            )

                        held[data_id] = True
                        d.set_free(i, page.free_space())
                        held[dir_id] = True
                        return data_id, slot

                if d.next_dir == NO_PAGE:
                    break

                nxt = d.next_dir
                release(dir_id)
                dir_id = nxt

            # The last directory page is still pinned here.
            if d.entry_count >= DIR_CAPACITY:
                new_dir_id, new_dir_data = allocate()
                DirectoryPage.init(new_dir_data, new_dir_id)

                d.next_dir = new_dir_id
                held[dir_id] = True
                release(dir_id)

                dir_id = new_dir_id
                d = DirectoryPage(new_dir_data)

            data_id, data = allocate()
            page = SlottedPage.init(data, data_id)
            slot = page.insert(record)

            if slot is None:
                raise PageError(
                    "record did not fit on an empty data page"
                )

            d.add_entry(data_id, page.free_space())
            held[dir_id] = True
            return data_id, slot

        finally:
            for pid in reversed(list(held)):
                release(pid)

    # ---- get / delete --------------------------------------------------------
    def get(self, rid: Rid) -> bytes:
        page_id, slot = rid
        self._check_data_page(page_id)
        page = SlottedPage(self.pool.fetch_page(self.disk, page_id))
        try:
            return page.get(slot)
        finally:
            self.pool.unpin_page(self.disk, page_id, False)

    def delete(self, rid: Rid) -> None:
        page_id, slot = rid
        self._check_data_page(page_id)

        if slot < 0:
            raise PageError(
                f"rid {rid}: slot number must be nonnegative"
            )

        # Locate a real data-page entry before modifying the page.
        dir_id = 0

        while dir_id != NO_PAGE:
            d = DirectoryPage(
                self.pool.fetch_page(self.disk, dir_id)
            )
            dir_dirty = False

            try:
                for i in range(d.entry_count):
                    data_id, old_free = d.entry(i)

                    if data_id != page_id:
                        continue

                    page = SlottedPage(
                        self.pool.fetch_page(self.disk, page_id)
                    )
                    deleted = False

                    try:
                        page.delete(slot)
                        deleted = True
                        free = page.free_space()
                    finally:
                        self.pool.unpin_page(
                            self.disk, page_id, deleted
                        )

                    if old_free != free:
                        d.set_free(i, free)
                        dir_dirty = True

                    return

                nxt = d.next_dir

            finally:
                self.pool.unpin_page(
                    self.disk, dir_id, dir_dirty
                )

            dir_id = nxt

        raise PageError(
            f"rid {rid}: page is not a listed data page "
            f"of table {self.name}"
        )

    def _check_data_page(self, page_id: int) -> None:
        if not 0 < page_id < self.disk.num_pages:
            raise PageError(f"rid ({page_id}, ...): table {self.name} has no data page {page_id}")

    # ---- scan (section 4.1, strict pinning rule) -------------------------------
    def scan(self) -> Iterator[Tuple[Rid, bytes]]:
        """Yield (rid, record) for every live record.

        At any instant at most one directory page and one data page are
        pinned. The records are never collected into a list. If the caller
        stops early, the finally blocks still unpin both pages.
        """
        dir_id = 0
        while dir_id != NO_PAGE:
            d = DirectoryPage(self.pool.fetch_page(self.disk, dir_id))
            try:
                for i in range(d.entry_count):
                    data_id, _ = d.entry(i)
                    page = SlottedPage(self.pool.fetch_page(self.disk, data_id))
                    try:
                        for slot in page.live_slots():
                            yield (data_id, slot), page.get(slot)
                    finally:
                        self.pool.unpin_page(self.disk, data_id, False)
                nxt = d.next_dir
            finally:
                self.pool.unpin_page(self.disk, dir_id, False)
            dir_id = nxt

    # ---- facts the catalog needs ---------------------------------------------
    def page_count(self) -> int:
        return self.disk.num_pages

    def live_count(self) -> int:
        return sum(1 for _ in self.scan())
