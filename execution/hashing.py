"""Hash tables that respect the frame budget, and hash partitioning to disk.

BucketTable is our own hash table: a dict from h2(key) to a bucket (a list of
entries). Looking a key up compares it with every entry of its bucket, and
each of those is one comparison (§1.4), so collisions are counted honestly
and the count is the same on every run. Every entry has a size in bytes (the
length of its encoded record plus a 4-byte slot, as if it were stored in a
slotted page), and the table refuses an entry that would take it past its
capacity of `pages` pages. That is how "a hash table holds at most its frames"
is enforced rather than promised.

Partitioner writes tuples to P temporary files by h1(key, depth) mod P, one
pinned frame per partition.
"""
from __future__ import annotations

from typing import Any, Callable, Dict, List, Optional, Sequence, Tuple

from storage.page import HEADER_SIZE, PAGE_SIZE, SLOT_SIZE

from .context import RunWriter, TempFile
from .schema import Row, Schema, stable_hash

PAGE_BYTES = PAGE_SIZE - HEADER_SIZE          # 4088 usable bytes per page
H2_SEED = 1000                                # no partitioning level uses it
MAX_DEPTH = 3                                 # partitioning levels before fallback


class BucketTable:
    def __init__(self, pages: int, count: Callable[[int], None]):
        self.capacity = max(0, pages) * PAGE_BYTES
        self.used = 0
        self.buckets: Dict[int, List[list]] = {}
        self.count = count
        self.entries = 0

    def find(self, key: Tuple) -> Optional[list]:
        bucket = self.buckets.get(stable_hash(key, H2_SEED))
        if bucket:
            for e in bucket:
                self.count(1)
                if e[0] == key:
                    return e
        return None

    def matches(self, key: Tuple) -> List[list]:
        """Every entry with this key (a join's build side keeps duplicates)."""
        out = []
        for e in self.buckets.get(stable_hash(key, H2_SEED), ()):
            self.count(1)
            if e[0] == key:
                out.append(e)
        return out

    def fits(self, size: int) -> bool:
        return self.used + size + SLOT_SIZE <= self.capacity

    def add(self, key: Tuple, value: Any, size: int) -> bool:
        """Insert a new entry [key, value]; False (nothing inserted) if full."""
        if not self.fits(size):
            return False
        self.used += size + SLOT_SIZE
        self.buckets.setdefault(stable_hash(key, H2_SEED), []).append([key, value])
        self.entries += 1
        return True

    def pages_used(self) -> int:
        return -(-self.used // PAGE_BYTES)

    def items(self):
        for bucket in self.buckets.values():
            for e in bucket:
                yield e

    def clear(self) -> None:
        self.buckets.clear()
        self.used = 0
        self.entries = 0


class Partitioner:
    """P output partitions, one pinned frame each."""

    def __init__(self, owner, schema: Schema, key_of: Callable[[Row], Tuple],
                 parts: int, depth: int, label: str):
        self.owner = owner
        self.key_of = key_of
        self.P = parts
        self.depth = depth
        self.files: List[TempFile] = [owner.new_temp(f"{label}_d{depth}p{i}", schema)
                                      for i in range(parts)]
        self.writers: List[RunWriter] = [f.writer() for f in self.files]

    def add(self, row: Row) -> None:
        h = stable_hash(self.key_of(row), self.depth)
        self.writers[h % self.P].add(row)

    def close(self) -> List[TempFile]:
        for w in self.writers:
            w.close()
        return self.files

    def abandon(self) -> None:
        for w in self.writers:
            w.abandon()
