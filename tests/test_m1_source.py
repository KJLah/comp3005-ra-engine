"""Module 1 to the engine: query stored tables with relational algebra."""
import tempfile
import unittest

from ra_engine.parser import parse_query
from ra_engine.planner import evaluate, plan
from ra_engine.relation import NUM, STR
from storage.buffer import BufferPool
from storage.catalog import Catalog
from storage.csvload import load_csv
from storage.errors import CatalogError
from storage.record import parse_schema
from storage.source import HeapSource, sources


class HeapSourceTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.pool = BufferPool(4)
        self.cat = Catalog(self.tmp.name, self.pool)
        self.cat.create_table("R", parse_schema("a:INT,b:VARCHAR(20)"))
        self.cat.create_table("S", parse_schema("a:INT,c:INT"))
        load_csv(self.cat, "R", ["1,amy", "2,bob", "3,cat"])
        load_csv(self.cat, "S", ["2,20", "3,30", "4,40"])

    def tearDown(self):
        self.tmp.cleanup()

    def query(self, text):
        return evaluate(plan(parse_query(text), sources(self.cat))).rows

    def test_attributes_come_from_the_catalog(self):
        src = HeapSource(self.cat, "R")
        self.assertEqual([(a.qualified, a.type) for a in src.attributes],
                         [("R.a", NUM), ("R.b", STR)])

    def test_scan_returns_rows_and_can_run_twice(self):
        src = HeapSource(self.cat, "R")
        rows = [(1, "amy"), (2, "bob"), (3, "cat")]
        self.assertEqual(list(src.scan()), rows)
        self.assertEqual(list(src.scan()), rows)

    def test_select_project_join_on_stored_tables(self):
        self.assertEqual(self.query("project[b](select[a > 1](R))"), [("bob",), ("cat",)])
        self.assertEqual(self.query("project[R.b, S.c](R join[R.a = S.a] S)"),
                         [("bob", 20), ("cat", 30)])

    def test_nothing_is_left_pinned(self):
        self.query("R join[R.a = S.a] S")
        self.assertEqual(self.pool.pinned_count(), 0)

    def test_unknown_table(self):
        with self.assertRaises(CatalogError):
            HeapSource(self.cat, "Nope")