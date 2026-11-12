"""Module 2 step 3: external merge sort (Section 2.5)."""
import math
import random
import unittest

from execution.errors import BudgetError

from m2_helpers import make_db, run_plan, scan

# 106-byte records + 4-byte slot = 110 bytes -> 37 per page, so 296 rows fill
# exactly 8 data pages, in the table and in every run.
PER_PAGE = 37


def rows(n, seed=1):
    rnd = random.Random(seed)
    keys = list(range(n))
    rnd.shuffle(keys)
    return [f"{k},{chr(97 + k % 26) * 100}" for k in keys]


def formula_passes(n_pages, b):
    r0 = math.ceil(n_pages / b)
    return 1 if r0 <= 1 else 1 + math.ceil(math.log(r0) / math.log(b - 1))


class ExternalSort(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = make_db({
            "R8": ("a:INT,b:VARCHAR(100)", rows(8 * PER_PAGE)),
            "R80": ("a:INT,b:VARCHAR(100)", rows(80 * PER_PAGE, 2)),
            "R100": ("a:INT,b:VARCHAR(100)", rows(100 * PER_PAGE, 3)),
            "D": ("a:INT,b:VARCHAR(5)", [f"{i % 3},{'xyz'[i % 2]}" for i in range(50)]),
            "E": ("a:INT,b:VARCHAR(5)", []),
        })
        cls.db = cls.tmp.name

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    def sort(self, table, frames, keys=(("a", "asc"),)):
        plan = {"op": "sort", "keys": [[f"{table}.{k}", d] for k, d in keys],
                "child": scan(table)}
        return run_plan(self.db, plan, frames)

    def test_worked_example_2_5(self):
        """N = 8, B = 3 for the sort (B = 4 for the plan: the seqscan holds 1
        frame during pass 0). Carrying the third run forward gives the
        handout's 22 reads / 14 writes: 14 / 14 on the sort, 8 on the
        seqscan (+1 for the heap's directory page)."""
        res = self.sort("R8", 4)
        self.assertEqual([r[0] for r in res.rows], list(range(8 * PER_PAGE)))
        s = res.root.sorter
        self.assertEqual(s.F, 3)
        self.assertEqual(s.passes, 3)
        self.assertEqual([p.get("reads") for p in s.log[:2]], [0, 6])
        self.assertEqual([p.get("writes") for p in s.log[:2]], [8, 6])
        sort_c, scan_c = res.node("plan"), res.node("plan.child")
        self.assertEqual((sort_c["page_reads"], sort_c["page_writes"]), (14, 14))
        self.assertEqual((scan_c["page_reads"], scan_c["page_writes"]), (8 + 1, 0))
        self.assertLessEqual(res.ctx.pool.peak_pinned, 4)

    def test_twenty_times_b_at_b_4(self):
        """§5 'The budget': a sort of 20 x B pages at B = 4 completes."""
        res = self.sort("R80", 4)
        self.assertEqual([r[0] for r in res.rows], list(range(80 * PER_PAGE)))
        s = res.root.sorter
        self.assertEqual(s.initial_runs, math.ceil(80 / 3))
        self.assertEqual(s.passes, formula_passes(80, 3))      # 6
        self.assertLessEqual(res.ctx.pool.peak_pinned, 4)
        self.assertEqual(res.ctx.peak_reserved, 4)

    def test_passes_match_formula_for_several_n_and_b(self):
        for table, n in (("R8", 8), ("R80", 80), ("R100", 100)):
            for frames in (4, 5, 8, 12, 33):
                res = self.sort(table, frames)
                f = res.root.sorter.F
                self.assertEqual(res.root.sorter.passes, formula_passes(n, f),
                                 (table, frames))

    def test_fits_in_memory_is_one_pass_and_writes_nothing(self):
        res = self.sort("R8", 16)
        self.assertEqual(res.root.sorter.passes, 1)
        self.assertEqual(res.node("plan")["page_writes"], 0)
        self.assertEqual(res.node("plan")["page_reads"], 0)

    def test_multi_key_desc_and_stable(self):
        res = self.sort("D", 4, keys=(("b", "desc"), ("a", "asc")))
        expect = sorted(sorted(res.rows, key=lambda r: r[0]),
                        key=lambda r: r[1], reverse=True)
        self.assertEqual(res.rows, expect)
        self.assertEqual(len(res.rows), 50)

    def test_counters_repeat(self):
        a = [op.counters() for _, op in self.sort("R80", 5).root.walk()]
        b = [op.counters() for _, op in self.sort("R80", 5).root.walk()]
        self.assertEqual(a, b)

    def test_empty_input(self):
        self.assertEqual(self.sort("E", 4).rows, [])

    def test_b_3_merges_with_the_childs_frame_and_b_2_cannot(self):
        # B = 3: pass 0 has F = 2 (the seqscan holds 1); merging takes the
        # seqscan's frame back once it is closed.
        res = self.sort("R80", 3)
        self.assertEqual([r[0] for r in res.rows], list(range(80 * PER_PAGE)))
        self.assertEqual(res.root.sorter.F, 3)
        with self.assertRaises(BudgetError):
            self.sort("R80", 2)       # at most 2 frames ever: cannot merge

    def test_limit_over_sort_stops_and_cleans_up(self):
        res = run_plan(self.db, {"op": "limit", "n": 3, "child": {
            "op": "sort", "keys": [["R80.a", "asc"]], "child": scan("R80")}}, 4)
        self.assertEqual([r[0] for r in res.rows], [0, 1, 2])

    def test_sort_under_a_parent_with_one_frame_materialises(self):
        """A sort whose parent leaves it 1 frame merges down to one run."""
        res = run_plan(self.db, {"op": "union_all",
                                 "left": {"op": "sort", "keys": [["R8.a", "asc"]],
                                          "child": scan("R8")},
                                 "right": scan("R8")}, 4)
        self.assertEqual(len(res.rows), 16 * PER_PAGE)


if __name__ == "__main__":
    unittest.main()
