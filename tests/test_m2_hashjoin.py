"""Module 2 step 7: Grace hash join (Section 2.9), overflow and skew."""
import unittest
from collections import Counter

from execution.errors import BindError

from m2_helpers import make_db, run_plan, scan

R_ROWS = [(i, f"k{i % 50}", "x" * 60) for i in range(1000)]
S_ROWS = [(f"k{i % 70}", i, "y" * 60) for i in range(700)]
# skew: key 0 on 40% of the rows on both sides
SK_L = [(0 if i % 5 < 2 else i, "l" * 40) for i in range(1500)]
SK_R = [(0 if i % 5 < 2 else i, "r" * 40) for i in range(1000)]


def csv(rows):
    return [",".join(str(v) for v in r) for r in rows]


def hj(cond, left, right):
    return {"op": "hash_join", "condition": cond, "left": left, "right": right}


class GraceHashJoin(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = make_db({
            "R": ("a:INT,b:VARCHAR(10),pad:VARCHAR(60)", csv(R_ROWS)),
            "S": ("b:VARCHAR(10),c:INT,pad:VARCHAR(60)", csv(S_ROWS)),
            "R4": ("b:INT", ["1", "2", "2", "3"]),
            "S3": ("b:INT", ["2", "2", "4"]),
            "KL": ("k:INT,pad:VARCHAR(40)", csv(SK_L)),
            "KR": ("k:INT,pad:VARCHAR(40)", csv(SK_R)),
            "E": ("b:INT", []),
        })
        cls.db = cls.tmp.name

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    def test_duplicate_keys(self):
        res = run_plan(self.db, hj("R4.b = S3.b", scan("R4"), scan("S3")), 4)
        self.assertEqual(res.rows, [(2, 2)] * 4)

    def test_same_bag_as_the_other_joins_at_several_b(self):
        expect = Counter(r + s for r in R_ROWS for s in S_ROWS if r[1] == s[0])
        for frames in (4, 5, 8, 16, 64):
            res = run_plan(self.db, hj("R.b = S.b", scan("R"), scan("S")), frames)
            self.assertEqual(Counter(res.rows), expect, frames)
            self.assertEqual(res.root.P, frames - 1)
            self.assertLessEqual(res.ctx.pool.peak_pinned, frames)

    def test_cost_is_about_three_times_m_plus_n_when_nothing_overflows(self):
        res = run_plan(self.db, hj("R.b = S.b", scan("R"), scan("S")), 64)
        self.assertEqual([e for e in res.root.events], [])
        M = res.ctx.catalog.get("R").num_pages - 1
        N = res.ctx.catalog.get("S").num_pages - 1
        j = res.node("plan")
        written = sum(a + b for a, b in res.root.partition_pages)
        self.assertEqual(j["page_writes"], written)
        # a pair with an empty side is skipped unread: nothing can match
        probed = sum(a + b for a, b in res.root.partition_pages if a and b)
        self.assertEqual(j["page_reads"], probed)
        # partition pages = M + N plus at most one partly filled page each
        self.assertLessEqual(written, M + N + 2 * res.root.P)

    def test_skew_overflow_falls_back_and_is_still_right(self):
        expect = Counter(l + r for l in SK_L for r in SK_R if l[0] == r[0])
        res = run_plan(self.db, hj("KL.k = KR.k", scan("KL"), scan("KR")), 4)
        self.assertEqual(Counter(res.rows), expect)
        kinds = [e[0] for e in res.root.events]
        self.assertIn("repartition", kinds)
        self.assertIn("fallback", kinds)

    def test_empty_inputs(self):
        self.assertEqual(run_plan(self.db, hj("E.b = S3.b", scan("E"), scan("S3")), 4).rows, [])
        self.assertEqual(run_plan(self.db, hj("R4.b = E.b", scan("R4"), scan("E")), 4).rows, [])

    def test_equality_only(self):
        with self.assertRaises(BindError):
            run_plan(self.db, hj("R4.b != S3.b", scan("R4"), scan("S3")), 4)

    def test_all_three_joins_agree(self):
        bags = []
        for op in ("nested_loop_join", "sort_merge_join", "hash_join"):
            res = run_plan(self.db, {"op": op, "condition": "KL.k = KR.k",
                                     "left": scan("KL"), "right": scan("KR")}, 4)
            bags.append(Counter(res.rows))
        self.assertEqual(bags[0], bags[1])
        self.assertEqual(bags[1], bags[2])

    def test_counters_repeat(self):
        plan = hj("KL.k = KR.k", scan("KL"), scan("KR"))
        a = [op.counters() for _, op in run_plan(self.db, plan, 4).root.walk()]
        b = [op.counters() for _, op in run_plan(self.db, plan, 4).root.walk()]
        self.assertEqual(a, b)


if __name__ == "__main__":
    unittest.main()
