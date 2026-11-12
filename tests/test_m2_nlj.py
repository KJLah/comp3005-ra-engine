"""Module 2 step 5: block nested loop join (Section 2.7)."""
import math
import unittest
from collections import Counter

from execution.errors import BindError

from m2_helpers import make_db, run_plan, scan

R_ROWS = [(i, f"k{i % 50}", "x" * 60) for i in range(1000)]
S_ROWS = [(f"k{i % 70}", i, "y" * 60) for i in range(700)]


def csv(rows):
    return [",".join(str(v) for v in r) for r in rows]


def nlj(cond, left="R", right="S"):
    return {"op": "nested_loop_join", "condition": cond,
            "left": scan(left), "right": scan(right)}


class BlockNestedLoop(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = make_db({
            "R": ("a:INT,b:VARCHAR(10),pad:VARCHAR(60)", csv(R_ROWS)),
            "S": ("b:VARCHAR(10),c:INT,pad:VARCHAR(60)", csv(S_ROWS)),
            "E": ("b:VARCHAR(10),c:INT,pad:VARCHAR(60)", []),
        })
        cls.db = cls.tmp.name

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    def expected_equi(self):
        return Counter(r + s for r in R_ROWS for s in S_ROWS if r[1] == s[0])

    def test_result_and_exact_comparisons_at_every_b(self):
        expect = self.expected_equi()
        for frames in (4, 5, 8, 16, 64):
            res = run_plan(self.db, nlj("R.b = S.b"), frames)
            self.assertEqual(Counter(res.rows), expect, frames)
            self.assertEqual(res.node("plan")["comparisons"], 1000 * 700, frames)

    def test_page_reads_against_formula(self):
        """Measured: outer M + 1, inner ceil(M/K) * (N + 1). The +1s are the
        heaps' directory pages; the inner's is read again on every rescan."""
        for frames in (4, 5, 8, 16, 64):
            res = run_plan(self.db, nlj("R.b = S.b"), frames)
            M = res.ctx.catalog.get("R").num_pages - 1
            N = res.ctx.catalog.get("S").num_pages - 1
            K = res.root.K
            self.assertEqual(K, frames - 2)
            blocks = math.ceil(M / K)
            self.assertEqual(res.root.blocks, blocks)
            self.assertEqual(res.node("plan.left")["page_reads"], M + 1)
            self.assertEqual(res.node("plan.right")["page_reads"], blocks * (N + 1))
            self.assertEqual(res.node("plan")["page_reads"], 0)
            self.assertLessEqual(res.ctx.pool.peak_pinned, frames)

    def test_any_condition_including_inequality(self):
        res = run_plan(self.db, nlj("R.a < S.c and R.a > 990"), 4)
        expect = Counter(r + s for r in R_ROWS for s in S_ROWS if 990 < r[0] < s[1])
        self.assertEqual(Counter(res.rows), expect)
        self.assertEqual(res.node("plan")["comparisons"], 1000 * 700)

    def test_output_larger_than_both_inputs(self):
        res = run_plan(self.db, {"op": "nested_loop_join", "condition": "R.a < 30",
                                 "left": scan("R"), "right": scan("S")}, 4)
        self.assertEqual(len(res.rows), 30 * 700)

    def test_non_rescannable_inner_is_materialised_once(self):
        plan = {"op": "nested_loop_join", "condition": "R.b = S.b", "left": scan("R"),
                "right": {"op": "sort", "keys": [["S.c", "desc"]], "child": scan("S")}}
        res = run_plan(self.db, plan, 4)
        self.assertEqual(Counter(res.rows), self.expected_equi())
        self.assertGreater(res.node("plan")["page_writes"], 0)    # the inner temp file

    def test_empty_inputs(self):
        self.assertEqual(run_plan(self.db, nlj("R.b = E.b", "R", "E"), 4).rows, [])
        res = run_plan(self.db, nlj("E.b = S.b", "E", "S"), 4)
        self.assertEqual(res.rows, [])
        self.assertEqual(res.node("plan.right")["page_reads"], 0)  # never scanned

    def test_self_join_needs_distinct_names(self):
        with self.assertRaises(BindError):
            run_plan(self.db, nlj("R.a = R.a", "R", "R"), 4)

    def test_type_mismatch(self):
        with self.assertRaises(BindError):
            run_plan(self.db, nlj("R.a = S.b"), 4)


if __name__ == "__main__":
    unittest.main()
