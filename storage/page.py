"""Slotted page (Module 1, section 3). All offsets below are required.

    offset size field
         0    4 page_id      uint32
         4    2 slot_count   uint16  (includes dead slots)
         6    2 free_end     uint16  lowest byte offset used by record data
         8    4 slot 0       uint16 offset, uint16 length
        12    4 slot 1 ...
    record data grows backward from the end of the page.

The class works directly on a bytearray (a buffer-pool frame), so nothing is
copied: changing the page changes the frame.
"""
from __future__ import annotations

import struct
from typing import Iterator, List, Optional, Tuple

from .errors import PageError

PAGE_SIZE = 4096
HEADER_SIZE = 8
SLOT_SIZE = 4
MAX_RECORD = PAGE_SIZE - HEADER_SIZE - SLOT_SIZE   # 4084: see note in DESIGN_M1


class SlottedPage:
    def __init__(self, data: bytearray):
        if len(data) != PAGE_SIZE:
            raise PageError(f"a page must be {PAGE_SIZE} bytes, got {len(data)}")
        self.data = data

    @classmethod
    def init(cls, data: bytearray, page_id: int) -> "SlottedPage":
        """Format `data` (in place) as an empty slotted page."""
        data[:] = bytes(PAGE_SIZE)
        page = cls(data)
        page.page_id = page_id
        page.slot_count = 0
        page.free_end = PAGE_SIZE
        return page

    # ---- header fields ------------------------------------------------------
    @property
    def page_id(self) -> int:
        return struct.unpack_from("<I", self.data, 0)[0]

    @page_id.setter
    def page_id(self, v: int) -> None:
        struct.pack_into("<I", self.data, 0, v)

    @property
    def slot_count(self) -> int:
        return struct.unpack_from("<H", self.data, 4)[0]

    @slot_count.setter
    def slot_count(self, v: int) -> None:
        struct.pack_into("<H", self.data, 4, v)

    @property
    def free_end(self) -> int:
        return struct.unpack_from("<H", self.data, 6)[0]

    @free_end.setter
    def free_end(self, v: int) -> None:
        struct.pack_into("<H", self.data, 6, v)

    # ---- slots ----------------------------------------------------------------
    def slot(self, n: int) -> Tuple[int, int]:
        return struct.unpack_from("<HH", self.data, HEADER_SIZE + SLOT_SIZE * n)

    def _set_slot(self, n: int, offset: int, length: int) -> None:
        struct.pack_into("<HH", self.data, HEADER_SIZE + SLOT_SIZE * n, offset, length)

    def free_space(self) -> int:
        return self.free_end - (HEADER_SIZE + SLOT_SIZE * self.slot_count)

    # ---- operations (section 3.1) ---------------------------------------------
    def insert(self, record: bytes) -> Optional[int]:
        """Return the new slot number, or None (NO_SPACE) if it does not fit."""
        n = len(record)
        if self.free_space() < n + SLOT_SIZE:
            return None
        end = self.free_end - n
        self.data[end:end + n] = record
        self.free_end = end
        slot_no = self.slot_count
        self._set_slot(slot_no, end, n)
        self.slot_count = slot_no + 1
        return slot_no

    def get(self, slot_no: int) -> bytes:
        if slot_no >= self.slot_count:
            raise PageError(f"rid ({self.page_id}, {slot_no}): slot does not exist "
                            f"(page has {self.slot_count} slots)")
        offset, length = self.slot(slot_no)
        if offset == 0:
            raise PageError(f"rid ({self.page_id}, {slot_no}): record was deleted")
        return bytes(self.data[offset:offset + length])

    def delete(self, slot_no: int) -> None:
        self.get(slot_no)            # same errors: missing or already dead
        self._set_slot(slot_no, 0, 0)   # space is not reclaimed (no compaction)

    def live_slots(self) -> Iterator[int]:
        for n in range(self.slot_count):
            if self.slot(n)[0] != 0:
                yield n

    def directory(self) -> List[Tuple[int, int, int]]:
        """[(slot_no, offset, length)] for the layout command."""
        return [(n,) + self.slot(n) for n in range(self.slot_count)]
