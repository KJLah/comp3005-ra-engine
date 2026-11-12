"""Module 2 step 1: the pieces every operator is built on."""
import os
import shutil
import subprocess
import sys
import tempfile
import unittest

from storage.buffer import BufferPool, run_trace
from storage.disk import MemoryDisk
from storage.errors import BufferPoolError

from execution.conditions import equi_keys, join_predicate, row_predicate
from execution.context import Counters, ExecContext
from execution.errors import BindError, BudgetError, PlanError
from execution.plan import validate
from execution.schema import (REAL, Col, KeyComparator, Schema, decode, encode,
                              stable_hash)

R = Schema([Col("R.a", "INT"), Col("R.b", "VARCHAR", 20)])
S = Schema([Col("S.b", "VARCHAR", 20), Col("S.c", "INT")])


class TempDB(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp()

    def tearDown(self):
        shutil.rmtree(self.dir, ignore_errors=True)


# ---- Module 1 additions ------------------------------------------------------------
class BufferPoolAdditions(unittest.TestCase):
    def test_5_3_trace_still_reproduces(self):
        lru = run_trace([1, 2, 3, 1, 4, 2], 3, "lru")
        clock = run_trace([1, 2, 3, 1, 4, 2], 3, "clock")
        self.assertEqual((lru["hits"], lru["misses"], lru["evictions"]), (1, 5, 2))
        self.assertEqual((clock["hits"], clock["misses"], clock["evictions"]), (2, 4, 1))

    def test_peak_pinned(self):
        pool, disk = BufferPool(4), MemoryDisk()
        for p in (0, 1, 2):
            pool.fetch_page(disk, p)
        pool.fetch_page(disk, 0)              # second pin of a pinned page
        self.assertEqual((pool.pinned, pool.peak_pinned), (3, 3))
        for p in (0, 0, 1, 2):
            pool.unpin_page(disk, p, False)
        self.assertEqual((pool.pinned, pool.peak_pinned), (0, 3))

    def test_flush_then_discard_writes_once_and_frees_the_frame(self):
        pool, disk = BufferPool(2), MemoryDisk()
        pid, data = pool.new_page(disk)
        data[0] = 7
        pool.unpin_page(disk, pid, True)
        pool.flush_page(disk, pid)
        pool.flush_page(disk, pid)            # clean now: no second write
        pool.discard_page(disk, pid)
        self.assertEqual(pool.writes, 1)
        self.assertEqual(pool.resident(), [None, None])
        pool.fetch_page(disk, pid)            # really read back from disk
        self.assertEqual(pool.reads, 1)

    def test_discard_of_a_pinned_page_is_refused(self):
        pool, disk = BufferPool(2), MemoryDisk()
        pool.fetch_page(disk, 0)
        with self.assertRaises(BufferPoolError):
            pool.discard_page(disk, 0)

    def test_discard_file_never_writes_its_dirty_pages(self):
        pool, a, b = BufferPool(3), MemoryDisk("a"), MemoryDisk("b")
        for _ in range(2):
            pid, _ = pool.new_page(a)
            pool.unpin_page(a, pid, True)
        pool.fetch_page(b, 0)
        pool.unpin_page(b, 0, False)
        pool.discard(a)
        pool.flush_all()
        self.assertEqual(pool.writes, 0)
        self.assertEqual(pool.resident(), [None, None, 0])


# ---- plan validation -----------------------------------------------------------------
class PlanValidation(unittest.TestCase):
    def err(self, plan):
        with self.assertRaises(PlanError) as cm:
            validate(plan)
        return str(cm.exception)

    def test_every_handout_example_shape_is_accepted(self):
        scan = {"op": "seqscan", "table": "R"}
        for plan in [
            scan,
            {"op": "filter", "condition": "R.a > 500 and R.b = 'cs'", "child": scan},
            {"op": "project", "columns": ["R.a", "R.c"], "child": scan},
            {"op": "distinct", "mode": "hash", "child": scan},
            {"op": "sort", "keys": [["R.a", "asc"], ["R.b", "desc"]], "child": scan},
            {"op": "group_by", "mode": "sort", "keys": ["R.dept"],
             "aggregates": [["count", "*"], ["avg", "R.salary"], ["max", "R.name"]],
             "child": scan},
            {"op": "nested_loop_join", "condition": "R.b = S.b", "left": scan,
             "right": {"op": "seqscan", "table": "S"}},
            {"op": "limit", "n": 5, "child": scan},
            {"op": "except", "left": scan, "right": scan},
        ]:
            validate(plan)

    def test_unknown_op_names_the_node(self):
        msg = self.err({"op": "filter", "condition": "R.a = 1",
                        "child": {"op": "scan", "table": "R"}})
        self.assertIn("plan.child", msg)
        self.assertIn('"scan"', msg)

    def test_missing_field_names_node_and_field(self):
        msg = self.err({"op": "hash_join", "condition": "R.b = S.b",
                        "left": {"op": "seqscan", "table": "R"},
                        "right": {"op": "filter", "child": {"op": "seqscan", "table": "S"}}})
        self.assertIn("plan.right (filter)", msg)
        self.assertIn('"condition"', msg)

    def test_unknown_field_is_rejected(self):
        msg = self.err({"op": "project", "columns": ["R.a"], "chlid": {}})
        self.assertIn("plan (project)", msg)

    def test_bad_values(self):
        scan = {"op": "seqscan", "table": "R"}
        self.assertIn('"mode"', self.err({"op": "distinct", "mode": "fast", "child": scan}))
        self.assertIn('"keys"', self.err({"op": "sort", "keys": [["R.a", "up"]],
                                          "child": scan}))
        self.assertIn('"n"', self.err({"op": "limit", "n": -1, "child": scan}))
        self.assertIn("sum(*)", self.err({"op": "group_by", "mode": "hash", "keys": [],
                                          "aggregates": [["sum", "*"]], "child": scan}))
        self.assertIn("plan", self.err([1, 2]))


# ---- schemas, encoding, comparisons ---------------------------------------------------
class Schemas(unittest.TestCase):
    def test_duplicate_qualified_name_is_an_error(self):
        with self.assertRaises(BindError):
            R.concat(R, "plan")

    def test_encode_decode_round_trip_including_real_and_unicode(self):
        sch = Schema([Col("a", "INT"), Col("b", "VARCHAR", 10), Col("avg_x", REAL)])
        row = (-5, "héllo", 2.125)
        self.assertEqual(decode(sch, encode(sch, row)), row)

    def test_str_order_is_utf8_byte_order(self):
        words = ["z", "é", "a", "中", "Z", "\U0001F600", "ab", ""]
        self.assertEqual(sorted(words), sorted(words, key=lambda w: w.encode("utf-8")))

    def test_key_comparator_counts_one_per_call_and_honours_desc(self):
        n = []
        cmp = KeyComparator([(0, True), (1, False)], n.append)
        self.assertEqual(cmp((1, "a"), (1, "b")), 1)      # b before a when desc
        self.assertEqual(cmp((0, "z"), (1, "a")), -1)
        self.assertEqual(sum(n), 2)

    def test_stable_hash_is_the_same_in_another_process(self):
        here = [stable_hash([v], 0) for v in (1, "cs", "é")]
        code = ("from execution.schema import stable_hash;"
                "print([stable_hash([v], 0) for v in (1, 'cs', 'é')])")
        root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        out = subprocess.run([sys.executable, "-c", code], cwd=root, check=True,
                             capture_output=True, text=True,
                             env={**os.environ, "PYTHONHASHSEED": "12345"}).stdout
        self.assertEqual(out.strip(), str(here))
        self.assertNotEqual(stable_hash(["cs"], 0), stable_hash(["cs"], 1))


class Conditions(unittest.TestCase):
    def test_filter_condition(self):
        p = row_predicate("R.a > 5 and b = 'cs'", R, "plan")
        self.assertTrue(p((6, "cs")))
        self.assertFalse(p((6, "x")))

    def test_int_against_varchar_is_an_error_even_for_equality(self):
        with self.assertRaises(BindError):
            row_predicate("R.a = 'x'", R, "plan")
        with self.assertRaises(BindError):
            equi_keys("R.a = S.b", R, S, "plan")

    def test_unknown_column(self):
        with self.assertRaises(BindError):
            row_predicate("R.zz = 1", R, "plan")

    def test_join_condition_and_equi_keys(self):
        p = join_predicate("R.b = S.b and R.a < S.c", R, S, "plan")
        self.assertTrue(p((1, "x"), ("x", 2)))
        self.assertEqual(equi_keys("S.b = R.b", R, S, "plan"), ([1], [0]))
        self.assertEqual(equi_keys("R.b = S.b and R.a = S.c", R, S, "plan"),
                         ([1, 0], [0, 1]))
        with self.assertRaises(BindError):
            equi_keys("R.a < S.c", R, S, "plan")


# ---- budget and temp files ------------------------------------------------------------
class BudgetAndTempFiles(TempDB):
    def test_budget_refuses_more_than_b(self):
        ctx = ExecContext(self.dir, 4)
        ctx.reserve("sort", 3)
        with self.assertRaises(BudgetError):
            ctx.reserve("scan", 2)
        ctx.release("sort", 3)
        ctx.reserve("scan", 4)
        self.assertEqual(ctx.peak_reserved, 4)

    def test_temp_file_writes_through_and_is_deleted(self):
        ctx = ExecContext(self.dir, 4)
        c = Counters()
        t = ctx.new_temp("test", R, c)
        w = t.writer()
        for i in range(1000):
            w.add((i, "x" * 20))
        w.close()
        self.assertGreater(t.pages, 1)
        self.assertEqual(c.page_writes, t.pages)       # one write per page, at once
        self.assertEqual(ctx.pool.pinned, 0)
        back = list(t.rows())
        self.assertEqual(back, [(i, "x" * 20) for i in range(1000)])
        self.assertEqual(c.page_reads, t.pages)        # no page was cached
        list(t.rows())
        self.assertEqual(c.page_reads, 2 * t.pages)
        t.delete()
        self.assertFalse(os.path.exists(t.path))
        self.assertEqual(ctx.leftover_temps(), [])
        self.assertTrue(all(f.disk is not t.disk for f in ctx.pool.frames))
        self.assertLessEqual(ctx.pool.peak_pinned, 1)


if __name__ == "__main__":
    unittest.main()
