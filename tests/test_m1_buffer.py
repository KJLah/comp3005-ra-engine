"""Module 1 section 5: buffer pool, LRU and CLOCK."""
import os
import tempfile
import unittest

from storage.buffer import BufferPool, run_trace
from storage.disk import DiskManager, MemoryDisk
from storage.errors import BufferPoolError
from storage.page import PAGE_SIZE

TRACE = [1, 2, 3, 1, 4, 2]


def stats(policy, trace=TRACE, frames=3):
    return {k: v for k, v in run_trace(trace, frames, policy).items()
            if k in ("hits", "misses", "evictions", "reads", "writes")}


class WorkedExample_5_3(unittest.TestCase):
    """Run this first: if these numbers are wrong, everything later is wrong."""

    def test_lru_totals(self):
        self.assertEqual(stats("lru"), {"hits": 1, "misses": 5, "evictions": 2,
                                        "reads": 5, "writes": 0})

    def test_clock_totals(self):
        self.assertEqual(stats("clock"), {"hits": 2, "misses": 4, "evictions": 1,
                                          "reads": 4, "writes": 0})

    def step_by_step(self, policy):
        disk = MemoryDisk()
        pool = BufferPool(3, policy)
        seen = []
        for pid in TRACE:
            pool.fetch_page(disk, pid)
            pool.unpin_page(disk, pid, False)
            seen.append(sorted(p for p in pool.resident() if p is not None))
        return seen

    def test_lru_pool_after_each_fetch(self):
        self.assertEqual(self.step_by_step("lru"),
                         [[1], [1, 2], [1, 2, 3], [1, 2, 3], [1, 3, 4], [1, 2, 4]])

    def test_clock_pool_after_each_fetch(self):
        self.assertEqual(self.step_by_step("clock"),
                         [[1], [1, 2], [1, 2, 3], [1, 2, 3], [2, 3, 4], [2, 3, 4]])

    def test_same_trace_twice_gives_identical_counters(self):
        for policy in ("lru", "clock"):
            self.assertEqual(stats(policy), stats(policy))


class PinningRules(unittest.TestCase):
    def test_pinned_page_is_never_evicted(self):
        for policy in ("lru", "clock"):
            disk, pool = MemoryDisk(), BufferPool(2, policy)
            pool.fetch_page(disk, 1)
            pool.fetch_page(disk, 2)          # both stay pinned
            with self.assertRaises(BufferPoolError):
                pool.fetch_page(disk, 3)
            self.assertEqual(sorted(pool.resident()), [1, 2])

    def test_unpin_at_zero_is_an_error(self):
        disk, pool = MemoryDisk(), BufferPool(2)
        pool.fetch_page(disk, 1)
        pool.unpin_page(disk, 1, False)
        with self.assertRaises(BufferPoolError):
            pool.unpin_page(disk, 1, False)

    def test_unpin_of_missing_page_is_an_error(self):
        with self.assertRaises(BufferPoolError):
            BufferPool(2).unpin_page(MemoryDisk(), 9, False)

    def test_lru_skips_pinned_lru_frame(self):
        disk, pool = MemoryDisk(), BufferPool(2, "lru")
        pool.fetch_page(disk, 1)              # stays pinned, and is LRU
        pool.fetch_page(disk, 2)
        pool.unpin_page(disk, 2, False)
        pool.fetch_page(disk, 3)              # must evict 2, not 1
        self.assertEqual(sorted(pool.resident()), [1, 3])


class DirtyPages(unittest.TestCase):
    def test_dirty_page_written_once_on_eviction(self):
        disk, pool = MemoryDisk(), BufferPool(1)
        data = pool.fetch_page(disk, 0)
        data[0] = 0xAB
        pool.unpin_page(disk, 0, True)
        pool.fetch_page(disk, 1)              # evicts dirty page 0
        self.assertEqual(pool.stats()["writes"], 1)
        self.assertEqual(disk.pages[0][0], 0xAB)

    def test_flush_all_writes_dirty_frames_only_once(self):
        disk, pool = MemoryDisk(), BufferPool(4)
        for pid in (0, 1, 2):
            pool.fetch_page(disk, pid)
            pool.unpin_page(disk, pid, pid != 1)
        pool.flush_all()
        pool.flush_all()
        self.assertEqual(pool.stats()["writes"], 2)

    def test_new_page_is_zeroed_pinned_dirty(self):
        disk, pool = MemoryDisk(), BufferPool(2)
        pid, data = pool.new_page(disk)
        self.assertEqual((pid, bytes(data)), (0, bytes(PAGE_SIZE)))
        self.assertEqual(pool.pinned_count(), 1)
        pool.unpin_page(disk, pid, False)
        pool.flush_all()
        self.assertEqual(pool.stats(), {"hits": 0, "misses": 0, "evictions": 0,
                                        "reads": 0, "writes": 1})


class RealFile(unittest.TestCase):
    def test_round_trip_through_a_file(self):
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, "R.db")
            disk, pool = DiskManager(path), BufferPool(2)
            pid, data = pool.new_page(disk)
            data[10:13] = b"abc"
            pool.unpin_page(disk, pid, True)
            pool.flush_all()
            self.assertEqual(os.path.getsize(path), PAGE_SIZE)
            fresh = BufferPool(2)
            self.assertEqual(bytes(fresh.fetch_page(DiskManager(path), 0)[10:13]), b"abc")

    def test_two_files_do_not_share_page_ids(self):
        a, b, pool = MemoryDisk("a"), MemoryDisk("b"), BufferPool(4)
        pool.fetch_page(a, 0)
        pool.fetch_page(b, 0)
        self.assertEqual(pool.stats()["misses"], 2)
