"""Module 2 step 6: sort-merge join (Section 2.8)."""
import unittest
from collections import Counter

from execution.errors import BindError

from m2_helpers import make_db, run_plan, scan

R_ROWS = [(i, f"k{i % 50}", "x" * 60) for i in range(1000)]
S_ROWS = [(f"k{i % 70}", i, "y" * 60) for i in range(700)]


def csv(rows):
    return [",".join(str(v) for v in r) for r in rows]


def smj(cond, left, right):
    return {"op": "sort_merge_join", "condition": cond, "left": left, "right": right}


class SortMerge(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = make_db({
            "R": ("a:INT,b:VARCHAR(10),pad:VARCHAR(60)", csv(R_ROWS)),
            "S": ("b:VARCHAR(10),c:INT,pad:VARCHAR(60)", csv(S_ROWS)),
            "R4": ("b:INT", ["1", "2", "2", "3"]),
            "S3": ("b:INT", ["2", "2", "4"]),
            # one key on 500 left and 300 right tuples: a right run of ~21 KB
            "HL": ("k:INT,pad:VARCHAR(60)", [f"{7 if i < 500 else i},{'l' * 60}"
                                             for i in range(800)]),
            "HR": ("k:INT,pad:VARCHAR(60)", [f"{7 if i < 300 else 10000 + i},{'r' * 60}"
                                             for i in range(600)]),
            "E": ("b:INT", []),
        })
        cls.db = cls.tmp.name

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    def test_duplicate_key_example_returns_four_rows(self):
        res = run_plan(self.db, smj("R4.b = S3.b", scan("R4"), scan("S3")), 4)
        self.assertEqual(res.rows, [(2, 2)] * 4)

    def test_same_bag_as_nested_loop_at_several_b(self):
        expect = Counter(r + s for r in R_ROWS for s in S_ROWS if r[1] == s[0])
        for frames in (4, 5, 8, 16, 64):
            res = run_plan(self.db, smj("R.b = S.b", scan("R"), scan("S")), frames)
            self.assertEqual(Counter(res.rows), expect, frames)
            self.assertLessEqual(res.ctx.pool.peak_pinned, frames)

    def test_key_run_larger_than_memory_spills(self):
        res = run_plan(self.db, smj("HL.k = HR.k", scan("HL"), scan("HR")), 4)
        self.assertEqual(len(res.rows), 500 * 300)
        self.assertEqual(res.root.spills, 1)

    def test_condition_in_either_order_and_equality_only(self):
        res = run_plan(self.db, smj("S3.b = R4.b", scan("R4"), scan("S3")), 4)
        self.assertEqual(len(res.rows), 4)
        with self.assertRaises(BindError):
            run_plan(self.db, smj("R4.b < S3.b", scan("R4"), scan("S3")), 4)

    def test_empty_inputs(self):
        self.assertEqual(run_plan(self.db, smj("E.b = S3.b", scan("E"), scan("S3")), 4).rows, [])
        self.assertEqual(run_plan(self.db, smj("R4.b = E.b", scan("R4"), scan("E")), 4).rows, [])

    def test_counters_repeat(self):
        plan = smj("R.b = S.b", scan("R"), scan("S"))
        a = [op.counters() for _, op in run_plan(self.db, plan, 5).root.walk()]
        b = [op.counters() for _, op in run_plan(self.db, plan, 5).root.walk()]
        self.assertEqual(a, b)


if __name__ == "__main__":
    unittest.main()