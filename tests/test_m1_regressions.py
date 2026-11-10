import contextlib
import io
import json
import tempfile
import unittest
from argparse import Namespace

from storage.buffer import BufferPool
from storage.catalog import Catalog
from storage.cli import cmd_scan
from storage.csvload import convert, load_csv, parse_int
from storage.disk import MemoryDisk
from storage.errors import BufferPoolError, LoadError
from storage.heap import HeapFile
from storage.record import parse_schema


class Reject99Disk(MemoryDisk):
    def read_page(self, page_id):
        if page_id == 99:
            raise OSError("simulated read failure")
        return super().read_page(page_id)


class FailureRecovery(unittest.TestCase):
    def test_failed_read_keeps_pool_usable(self):
        for policy in ("lru", "clock"):
            for resident in (False, True):
                with self.subTest(policy=policy, resident=resident):
                    disk = Reject99Disk()
                    pool = BufferPool(1, policy)

                    if resident:
                        page = pool.fetch_page(disk, 0)
                        page[0] = 123
                        pool.unpin_page(disk, 0, True)

                    with self.assertRaises(OSError):
                        pool.fetch_page(disk, 99)

                    page = pool.fetch_page(disk, 0)
                    self.assertEqual(
                        page[0], 123 if resident else 0
                    )
                    pool.unpin_page(disk, 0, False)
                    self.assertEqual(pool.pinned_count(), 0)

    def test_failed_allocation_does_not_consume_page_id(self):
        for policy in ("lru", "clock"):
            with self.subTest(policy=policy):
                disk = MemoryDisk()
                pool = BufferPool(1, policy)

                pid, _ = pool.new_page(disk)
                before = disk.num_pages

                with self.assertRaises(BufferPoolError):
                    pool.new_page(disk)

                self.assertEqual(disk.num_pages, before)

                pool.unpin_page(disk, pid, True)
                next_pid, _ = pool.new_page(disk)

                self.assertEqual(next_pid, before)
                pool.unpin_page(disk, next_pid, True)

    def test_failed_insert_releases_only_its_own_pins(self):
        for policy in ("lru", "clock"):
            with self.subTest(policy=policy):
                disk = MemoryDisk("R")
                other = MemoryDisk("other")
                pool = BufferPool(2, policy)
                heap = HeapFile.create(pool, disk, "R")

                # The caller keeps a page pinned.
                pool.fetch_page(other, 0)
                before = disk.num_pages

                with self.assertRaises(BufferPoolError):
                    heap.insert(b"x")

                self.assertEqual(disk.num_pages, before)
                self.assertEqual(pool.pinned_count(), 1)

                # After releasing the caller's pin, retry successfully.
                pool.unpin_page(other, 0, False)
                rid = heap.insert(b"x")

                self.assertEqual(heap.get(rid), b"x")
                self.assertEqual(pool.pinned_count(), 0)


class CSVFailures(unittest.TestCase):
    def test_field_count_errors_identify_line_and_column(self):
        schema = parse_schema("a:INT,b:INT")

        for fields, column in (
            (["1"], "'b'"),
            (["1", "2", "3"], "column 3"),
        ):
            with self.subTest(fields=fields):
                with self.assertRaises(LoadError) as cm:
                    convert(fields, schema, 7)

                self.assertIn("line 7", str(cm.exception))
                self.assertIn(column, str(cm.exception))

    def test_huge_integer_has_contextual_error(self):
        with self.assertRaises(LoadError) as cm:
            convert(
                ["9" * 5000],
                parse_schema("a:INT"),
                3,
            )

        self.assertIn("line 3", str(cm.exception))
        self.assertIn("column 'a'", str(cm.exception))
        self.assertEqual(parse_int("0" * 5000 + "1"), 1)


class ScanLimits(unittest.TestCase):
    def test_limit_does_not_read_the_next_page(self):
        with tempfile.TemporaryDirectory() as db:
            cat = Catalog(db, BufferPool(4))
            cat.create_table("R", parse_schema("a:INT"))
            load_csv(cat, "R", [str(i) for i in range(512)])

            # One INT record costs 4 data bytes plus a 4-byte slot:
            # floor(4088 / 8) = 511 records per page.
            for limit, count, reads in (
                (0, 0, 0),
                (1, 1, 2),
                (511, 511, 2),
                (512, 512, 3),
            ):
                with self.subTest(limit=limit):
                    args = Namespace(
                        db=db,
                        table="R",
                        frames=2,
                        policy="lru",
                        limit=limit,
                        json=True,
                    )
                    output = io.StringIO()

                    with contextlib.redirect_stdout(output):
                        cmd_scan(args)

                    doc = json.loads(output.getvalue())
                    self.assertEqual(len(doc["rows"]), count)
                    self.assertEqual(
                        doc["stats"]["reads"], reads
                    )