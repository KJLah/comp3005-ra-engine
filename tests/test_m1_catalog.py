"""Module 1 section 6: catalog."""
import os
import tempfile
import unittest

from storage.buffer import BufferPool
from storage.catalog import Catalog, TableInfo, decode_info, encode_info
from storage.errors import CatalogError
from storage.record import decode, encode, parse_schema

SCHEMA = parse_schema("a:INT,b:VARCHAR(20),c:INT")


class CatalogTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.dir = self.tmp.name

    def tearDown(self):
        self.tmp.cleanup()

    def reopen(self, frames=4):
        return Catalog(self.dir, BufferPool(frames))

    def test_record_round_trip_keeps_everything(self):
        info = TableInfo("R", SCHEMA, "R.db", 12, 345, {"a": {"distinct": 7}})
        self.assertEqual(decode_info(encode_info(info)), info)

    def test_new_database_has_catalog_db_and_no_tables(self):
        cat = self.reopen()
        cat.flush()
        self.assertTrue(os.path.exists(os.path.join(self.dir, "catalog.db")))
        self.assertEqual(cat.tables(), [])

    def test_create_table_survives_reopen(self):
        cat = self.reopen()
        cat.create_table("R", SCHEMA)
        cat.flush()
        info = self.reopen().get("R")
        self.assertEqual((info.name, info.file, info.columns), ("R", "R.db", SCHEMA))
        self.assertEqual([c.to_json() for c in info.columns],
                         [["a", "INT"], ["b", "VARCHAR", 20], ["c", "INT"]])
        self.assertTrue(os.path.exists(os.path.join(self.dir, "R.db")))

    def test_catalog_is_read_through_the_buffer_pool(self):
        cat = self.reopen()
        cat.create_table("R", SCHEMA)
        cat.flush()
        pool = BufferPool(4)
        Catalog(self.dir, pool).get("R")
        self.assertGreater(pool.stats()["reads"], 0)

    def test_counts_are_stored_and_reloaded(self):
        cat = self.reopen()
        cat.create_table("R", SCHEMA)
        heap = cat.open_heap("R")
        for i in range(300):
            heap.insert(encode(SCHEMA, (i, "x", i)))
        info = cat.refresh_counts("R")
        self.assertEqual((info.num_pages, info.num_tuples), (3, 300))   # dir + 2 data
        cat.flush()
        again = self.reopen()
        self.assertEqual((again.get("R").num_pages, again.get("R").num_tuples), (3, 300))
        rows = [decode(SCHEMA, r) for _, r in again.open_heap("R").scan()]
        self.assertEqual(rows[299], (299, "x", 299))

    def test_two_tables_share_one_pool_without_mixing_pages(self):
        cat = self.reopen(3)
        cat.create_table("R", SCHEMA)
        cat.create_table("S", SCHEMA)
        cat.open_heap("R").insert(encode(SCHEMA, (1, "r", 1)))
        cat.open_heap("S").insert(encode(SCHEMA, (2, "s", 2)))
        cat.flush()
        again = self.reopen(3)
        self.assertEqual([decode(SCHEMA, r) for _, r in again.open_heap("R").scan()],
                         [(1, "r", 1)])
        self.assertEqual([decode(SCHEMA, r) for _, r in again.open_heap("S").scan()],
                         [(2, "s", 2)])

    def test_duplicate_unknown_and_bad_names_are_errors(self):
        cat = self.reopen()
        cat.create_table("R", SCHEMA)
        with self.assertRaises(CatalogError):
            cat.create_table("R", SCHEMA)
        with self.assertRaises(CatalogError) as cm:
            cat.get("Nope")
        self.assertIn("R", str(cm.exception))
        for bad in ("", "1abc", "a-b", "../x", "catalog"):
            with self.assertRaises(CatalogError):
                cat.create_table(bad, SCHEMA)

    def test_statistics_field_has_room_for_module_4(self):
        cat = self.reopen()
        info = cat.create_table("R", SCHEMA)
        info.stats = {"b": {"distinct": 120, "histogram": list(range(50))}}
        cat.update(info)
        cat.flush()
        self.assertEqual(self.reopen().get("R").stats, info.stats)
