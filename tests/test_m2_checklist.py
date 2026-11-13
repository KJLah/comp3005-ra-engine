"""Module 2 section 6: the checklist before submitting, one test per line.
Every plan in plans/ goes through the real command line."""
import glob
import io
import json
import os
import unittest
from collections import Counter

from execution.cli import main

from m2_helpers import make_db, run_plan, scan

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PLANS = sorted(glob.glob(os.path.join(ROOT, "plans", "*.json")))
R_ROWS = [f"{i},{['cs', 'math', 'bio', 'art'][i % 4]}{i % 60},{i % 97}" for i in range(3000)]
S_ROWS = [f"{['cs', 'math', 'bio', 'art'][i % 4]}{i % 45},{i}" for i in range(1500)]


def cli(*args):
    out, err = io.StringIO(), io.StringIO()
    code = main(list(args), out, err)
    return code, out.getvalue(), err.getvalue()


class Checklist(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        schema_r, schema_s = "a:INT,b:VARCHAR(10),c:INT", "b:VARCHAR(10),c:INT"
        cls.full = make_db({"R": (schema_r, R_ROWS), "S": (schema_s, S_ROWS),
                            "R4": ("b:INT", ["1", "2", "2", "3"]),
                            "S3": ("b:INT", ["2", "2", "4"])})
        cls.empty = make_db({"R": (schema_r, []), "S": (schema_s, [])})

    @classmethod
    def tearDownClass(cls):
        cls.full.cleanup()
        cls.empty.cleanup()

    def test_there_is_a_sample_plan_for_every_operator(self):
        ops = set()

        def walk(n):
            ops.add(n["op"])
            for f in ("child", "left", "right"):
                if f in n:
                    walk(n[f])
        for p in PLANS:
            with open(p) as f:
                walk(json.load(f))
        self.assertEqual(ops, {"seqscan", "filter", "project", "distinct", "sort",
                               "group_by", "nested_loop_join", "sort_merge_join",
                               "hash_join", "union_all", "union", "intersect",
                               "except", "limit"})

    def test_sort_example_2_5_reproduces(self):
        # covered with exact numbers by test_m2_sort.test_worked_example_2_5
        from test_m2_sort import ExternalSort
        suite = unittest.TestSuite([ExternalSort("test_worked_example_2_5")])
        self.assertTrue(unittest.TextTestRunner(stream=io.StringIO()).run(suite).wasSuccessful())

    def test_duplicate_key_example_returns_four_rows_for_every_join(self):
        for op in ("nested_loop_join", "sort_merge_join", "hash_join"):
            res = run_plan(self.full.name, {"op": op, "condition": "R4.b = S3.b",
                                            "left": scan("R4"), "right": scan("S3")}, 4)
            self.assertEqual(res.rows, [(2, 2)] * 4, op)

    def test_all_three_joins_return_the_same_bag(self):
        bags = [Counter(run_plan(self.full.name, {"op": op, "condition": "R.b = S.b",
                                                  "left": scan("R"), "right": scan("S")},
                                 4).rows)
                for op in ("nested_loop_join", "sort_merge_join", "hash_join")]
        self.assertGreater(sum(bags[0].values()), 3000)    # output larger than both inputs
        self.assertEqual(bags[0], bags[1])
        self.assertEqual(bags[1], bags[2])

    def test_block_nested_loop_reports_exactly_r_times_s(self):
        for frames in (4, 7, 32):
            res = run_plan(self.full.name, {"op": "nested_loop_join", "condition": "R.b = S.b",
                                            "left": scan("R"), "right": scan("S")}, frames)
            self.assertEqual(res.node("plan")["comparisons"], 3000 * 1500)

    def test_every_plan_runs_at_b_4_and_leaves_nothing_behind(self):
        for p in PLANS:
            code, out, err = cli("run", "--db", self.full.name, "--plan", p,
                                 "--frames", "4", "--json")
            self.assertEqual(code, 0, f"{os.path.basename(p)}: {err}")
            json.loads(out)
            self.assertFalse(os.path.exists(os.path.join(self.full.name, "_tmp")))

    def test_empty_input_through_every_operator(self):
        for p in PLANS:
            code, out, err = cli("run", "--db", self.empty.name, "--plan", p,
                                 "--frames", "4", "--json")
            self.assertEqual(code, 0, f"{os.path.basename(p)}: {err}")
            self.assertEqual(json.loads(out)["rows"], [], os.path.basename(p))

    def test_trace_runs_every_plan(self):
        for p in PLANS:
            code, out, err = cli("trace", "--db", self.full.name, "--plan", p, "--frames", "4")
            self.assertEqual(code, 0, err)
            self.assertIn("peak pinned frames=", out)

    def test_same_plan_same_b_same_counters(self):
        p = os.path.join(ROOT, "plans", "17_pipeline.json")
        a = cli("trace", "--db", self.full.name, "--plan", p, "--frames", "5")[1]
        b = cli("trace", "--db", self.full.name, "--plan", p, "--frames", "5")[1]
        self.assertEqual(a, b)


if __name__ == "__main__":
    unittest.main()
