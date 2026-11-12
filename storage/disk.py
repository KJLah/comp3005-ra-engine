"""Disk manager (Module 1, section 1): page N of a file is bytes
[N*4096, (N+1)*4096). No header outside the pages.

The buffer pool is the only caller. It counts one READ per read_page call and
one WRITE per write_page call (section 5), so these methods must do exactly
one page of I/O each.
"""
from __future__ import annotations

import os
from typing import Dict

from .page import PAGE_SIZE


class DiskManager:
    """One table file (or catalog.db)."""

    def __init__(self, path: str):
        self.path = path
        if not os.path.exists(path):
            with open(path, "wb"):
                pass
        size = os.path.getsize(path)
        if size % PAGE_SIZE:
            raise ValueError(f"{path}: size {size} is not a multiple of {PAGE_SIZE}")
        self.num_pages = size // PAGE_SIZE

    def read_page(self, page_id: int) -> bytes:
        if not 0 <= page_id < self.num_pages:
            raise ValueError(f"{self.path}: page {page_id} does not exist "
                             f"(file has {self.num_pages} pages)")
        with open(self.path, "rb") as f:
            f.seek(page_id * PAGE_SIZE)
            return f.read(PAGE_SIZE)

    def write_page(self, page_id: int, data: bytes) -> None:
        if len(data) != PAGE_SIZE:
            raise ValueError("write_page needs exactly one page")
        with open(self.path, "r+b") as f:
            f.seek(page_id * PAGE_SIZE)
            f.write(data)
        self.num_pages = max(self.num_pages, page_id + 1)

    def allocate_page(self) -> int:
        """Reserve the next page id. Nothing is written until the buffer pool
        flushes or evicts the page, so allocation itself is not a WRITE."""
        page_id = self.num_pages
        self.num_pages += 1
        return page_id


class MemoryDisk:
    """Same interface, kept in memory. Used by tests and by `storage trace`,
    where page ids come from a trace file and no real table exists. A page
    that was never written reads as zeros."""

    def __init__(self, path: str = "<memory>"):
        self.path = path
        self.pages: Dict[int, bytes] = {}
        self.num_pages = 0

    def read_page(self, page_id: int) -> bytes:
        return self.pages.get(page_id, bytes(PAGE_SIZE))

    def write_page(self, page_id: int, data: bytes) -> None:
        self.pages[page_id] = bytes(data)
        self.num_pages = max(self.num_pages, page_id + 1)

    def allocate_page(self) -> int:
        page_id = self.num_pages
        self.num_pages += 1
        return page_id
