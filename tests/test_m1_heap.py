"""Module 1 section 4: heap file."""
import os
import tempfile
import unittest

from storage.buffer import BufferPool
from storage.disk import DiskManager, MemoryDisk
from storage.errors import BufferPoolError, PageError, RecordError
from storage.heap import DIR_CAPACITY, NO_PAGE, DirectoryPage, HeapFile
from storage.record import decode, encode, parse_schema

SCHEMA = parse_schema("a:INT,b:VARCHAR(5),c:INT")


def rec13(i):
    """A 13-byte record, as in the 3.2 example: (i, 'bob', 7)."""
    return encode(SCHEMA, (i, "bob", 7))


def new_heap(frames=8):
    disk = MemoryDisk("R")
    pool = BufferPool(frames)
    return HeapFile.create(pool, disk, "R"), pool, disk


class PinWatcher:
    """Wraps a pool and records the most pages ever pinned at once."""

    def __init__(self, pool):
        self.pool, self.max_pinned = pool, 0
        self._fetch = pool.fetch_page
        pool.fetch_page = self.fetch_page

    def fetch_page(self, disk, pid):
        data = self._fetch(disk, pid)
        self.max_pinned = max(self.max_pinned, self.pool.pinned_count())
        return data


class DirectoryFormat(unittest.TestCase):
    def test_capacity_is_681(self):
        self.assertEqual(DIR_CAPACITY, 681)

    def test_new_file_page_0_is_an_empty_directory(self):
        heap, pool, disk = new_heap()
        pool.flush_all()
        page0 = disk.pages[0]
        self.assertEqual(page0[0:10].hex(" "), "00 00 00 00 ff ff ff ff 00 00")
        self.assertEqual(list(heap.scan()), [])


class InsertGetDelete(unittest.TestCase):
    def test_round_trip(self):
        heap, _, _ = new_heap()
        rid = heap.insert(rec13(42))
        self.assertEqual(rid, (1, 0))
        self.assertEqual(decode(SCHEMA, heap.get(rid)), (42, "bob", 7))

    def test_first_fit_fills_page_1_then_allocates_page_2(self):
        heap, _, _ = new_heap()
        rids = [heap.insert(rec13(i)) for i in range(241)]
        self.assertEqual(rids[239], (1, 239))      # 240 records fit on page 1 (3.3)
        self.assertEqual(rids[240], (2, 0))        # the 241st opens page 2

    def test_directory_tracks_free_bytes(self):
        heap, pool, disk = new_heap()
        heap.insert(rec13(1))
        d = DirectoryPage(pool.fetch_page(disk, 0))
        self.assertEqual((d.entry_count, d.entry(0)), (1, (1, 4071)))   # 3.2 row 2
        pool.unpin_page(disk, 0, False)

    def test_first_fit_uses_earlier_page_with_room(self):
        heap, _, _ = new_heap()
        big = encode(parse_schema("s:VARCHAR(255)"), ("x" * 255,))      # 257 bytes
        for _ in range(15):                        # 15 * 261 = 3915, leaves 173 on page 1
            heap.insert(big)
        self.assertEqual(heap.insert(big), (2, 0))  # does not fit page 1
        self.assertEqual(heap.insert(rec13(1)), (1, 15))  # but a small one does

    def test_deleted_record_is_an_error_naming_the_rid(self):
        heap, _, _ = new_heap()
        rid = heap.insert(rec13(1))
        heap.delete(rid)
        with self.assertRaises(PageError) as cm:
            heap.get(rid)
        self.assertIn("(1, 0)", str(cm.exception))

    def test_record_too_long_names_the_table(self):
        heap, _, _ = new_heap()
        with self.assertRaises(RecordError) as cm:
            heap.insert(b"x" * 4085)
        self.assertIn("R", str(cm.exception))
        self.assertEqual(heap.insert(b"x" * 4084), (1, 0))

    def test_get_and_delete_leave_nothing_pinned(self):
        heap, pool, _ = new_heap()
        rid = heap.insert(rec13(1))
        heap.get(rid)
        heap.delete(rid)
        self.assertEqual(pool.pinned_count(), 0)


class Scan(unittest.TestCase):
    def test_scan_returns_live_records_in_order(self):
        heap, _, _ = new_heap()
        rids = [heap.insert(rec13(i)) for i in range(5)]
        heap.delete(rids[1])
        heap.delete(rids[3])
        self.assertEqual([decode(SCHEMA, r)[0] for _, r in heap.scan()], [0, 2, 4])

    def test_delete_everything_then_scan_returns_nothing(self):
        heap, _, _ = new_heap()
        rids = [heap.insert(rec13(i)) for i in range(500)]
        for rid in rids:
            heap.delete(rid)
        self.assertEqual(list(heap.scan()), [])

    def test_scan_of_table_20x_larger_than_pool_pins_at_most_two(self):
        frames = 4
        heap, pool, disk = new_heap(frames)
        n = 240 * 20 * frames                      # 80 data pages
        for i in range(n):
            heap.insert(rec13(i))
        watch = PinWatcher(pool)
        count = sum(1 for _ in heap.scan())
        self.assertEqual(count, n)
        self.assertGreaterEqual(disk.num_pages - 1, 20 * frames)
        self.assertLessEqual(watch.max_pinned, 2)
        self.assertEqual(pool.pinned_count(), 0)

    def test_scan_reads_every_page_once_on_a_cold_small_pool(self):
        heap, pool, disk = new_heap(8)
        for i in range(240 * 10):                  # exactly 10 full data pages
            heap.insert(rec13(i))
        pool.flush_all()
        for frames in (2, 8, 64):
            cold = BufferPool(frames)
            reader = HeapFile(cold, disk, "R")
            sum(1 for _ in reader.scan())
            self.assertEqual(cold.stats()["reads"], 11)     # 1 directory + 10 data

    def test_stopping_a_scan_early_unpins(self):
        heap, pool, _ = new_heap()
        for i in range(10):
            heap.insert(rec13(i))
        it = heap.scan()
        next(it)
        it.close()
        self.assertEqual(pool.pinned_count(), 0)

    def test_same_scan_twice_gives_identical_counters(self):
        heap, pool, disk = new_heap()
        for i in range(1000):
            heap.insert(rec13(i))
        pool.flush_all()
        results = []
        for _ in range(2):
            cold = BufferPool(3, "clock")
            list(HeapFile(cold, disk, "R").scan())
            results.append(cold.stats())
        self.assertEqual(results[0], results[1])


class DirectoryChain(unittest.TestCase):
    def test_682nd_data_page_starts_a_second_directory_page(self):
        heap, pool, disk = new_heap(4)
        big = b"z" * 2040                          # 2 records per page: 2 * 2044 = 4088
        for _ in range(2 * DIR_CAPACITY + 1):
            heap.insert(big)
        d0 = DirectoryPage(pool.fetch_page(disk, 0))
        self.assertEqual(d0.entry_count, DIR_CAPACITY)
        second = d0.next_dir
        pool.unpin_page(disk, 0, False)
        self.assertNotEqual(second, NO_PAGE)
        d1 = DirectoryPage(pool.fetch_page(disk, second))
        self.assertEqual((d1.entry_count, d1.next_dir), (1, NO_PAGE))
        pool.unpin_page(disk, second, False)
        self.assertEqual(sum(1 for _ in heap.scan()), 2 * DIR_CAPACITY + 1)


class Persistence(unittest.TestCase):
    def test_reopen_from_disk(self):
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, "R.db")
            pool = BufferPool(4)
            heap = HeapFile.create(pool, DiskManager(path), "R")
            for i in range(300):
                heap.insert(rec13(i))
            pool.flush_all()
            again = HeapFile(BufferPool(4), DiskManager(path), "R")
            self.assertEqual([decode(SCHEMA, r)[0] for _, r in again.scan()],
                             list(range(300)))

    def test_one_frame_is_not_enough_for_insert(self):
        disk = MemoryDisk("R")
        heap = HeapFile.create(BufferPool(1), disk, "R")
        with self.assertRaises(BufferPoolError):
            heap.insert(rec13(1))
