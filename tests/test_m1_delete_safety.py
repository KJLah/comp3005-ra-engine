import unittest

from storage.buffer import BufferPool
from storage.disk import MemoryDisk
from storage.errors import PageError
from storage.heap import (
    DIR_CAPACITY,
    NO_PAGE,
    DirectoryPage,
    HeapFile,
)
from storage.page import PAGE_SIZE, SlottedPage


class DeleteSafety(unittest.TestCase):
    def test_failed_delete_does_not_add_a_write(self):
        disk = MemoryDisk()
        pool = BufferPool(4)
        heap = HeapFile.create(pool, disk, "T")
        rid = heap.insert(b"abc")
        pool.flush_all()

        heap.delete(rid)
        pool.flush_all()
        writes = pool.stats()["writes"]

        with self.assertRaises(PageError):
            heap.delete(rid)

        pool.flush_all()

        self.assertEqual(pool.stats()["writes"], writes)
        self.assertEqual(pool.pinned_count(), 0)

    def test_invalid_slots_leave_records_unchanged(self):
        disk = MemoryDisk()
        pool = BufferPool(2)
        heap = HeapFile.create(pool, disk, "T")
        rid = heap.insert(b"abc")
        pool.flush_all()
        writes = pool.stats()["writes"]

        for slot in (-1, 99):
            with self.subTest(slot=slot):
                with self.assertRaises(PageError):
                    heap.delete((rid[0], slot))

                pool.flush_all()
                self.assertEqual(
                    pool.stats()["writes"], writes
                )
                self.assertEqual(heap.get(rid), b"abc")
                self.assertEqual(pool.pinned_count(), 0)

        page = SlottedPage.init(bytearray(PAGE_SIZE), 1)
        page.insert(b"abc")

        with self.assertRaises(PageError):
            page.get(-1)

    def test_directory_page_cannot_be_deleted_as_a_record(self):
        disk = MemoryDisk()
        pool = BufferPool(4)
        heap = HeapFile.create(pool, disk, "T")

        # Two records per page; enough to require a second directory.
        count = 2 * DIR_CAPACITY + 1
        for _ in range(count):
            heap.insert(b"z" * 2040)

        data = pool.fetch_page(disk, 0)
        directory_id = DirectoryPage(data).next_dir
        pool.unpin_page(disk, 0, False)
        self.assertNotEqual(directory_id, NO_PAGE)

        data = pool.fetch_page(disk, directory_id)
        before = bytes(data)
        pool.unpin_page(disk, directory_id, False)

        with self.assertRaises(PageError):
            heap.delete((directory_id, 0))

        data = pool.fetch_page(disk, directory_id)
        self.assertEqual(bytes(data), before)
        pool.unpin_page(disk, directory_id, False)

        self.assertEqual(heap.live_count(), count)
        self.assertEqual(pool.pinned_count(), 0)