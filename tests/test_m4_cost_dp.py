"""Module 4 Sections 4 and 5: the cost model and the Selinger DP."""
import unittest

from optimizer import conds as C
from optimizer.cost import blocks, join_terms, passes, sort_cost
from optimizer.dp import JoinDP, Pred, Rel


class Formulas(unittest.TestCase):
    def test_passes_match_section4(self):
        self.assertEqual(passes(10, 32), 1)
        self.assertEqual(passes(100, 32), 2)            # ceil(100/32) = 4 runs, fan-in 31
        self.assertEqual(passes(108, 4), 1 + 3)         # 27 runs, fan-in B - 1 = 3
        self.assertEqual(sort_cost(100, 32), 2 * 100 + 100)

    def test_join_formulas_with_intermediate_inputs(self):
        # neither input a base pipeline: the whole Section 4 formula is the join's own
        own = lambda algo: sum(v for _, v in join_terms(algo, 20, 400, 32, False, False, 400))
        self.assertEqual(own("nested_loop_join"), 20 + 1 * 400)
        self.assertEqual(own("hash_join"), 3 * (20 + 400))
        self.assertEqual(own("sort_merge_join"),
                         sort_cost(20, 32) + sort_cost(400, 32) + 20 + 400)

    def test_base_inputs_are_read_by_their_own_seqscan(self):
        own = lambda algo: sum(v for _, v in join_terms(algo, 10, 100, 32, True, True, 100))
        self.assertEqual(own("nested_loop_join"), 0)    # one block: inner read once, by its scan
        self.assertEqual(own("hash_join"), 2 * (10 + 100))
        self.assertEqual(blocks(100, 32), 4)


def section51():
    def rel(name, pages, rows, cols):
        return Rel(name, {"op": "seqscan", "table": name}, cols, rows, pages, pages, True, pages)
    rels = [rel("A", 100, 5000, ["A.x"]), rel("B'", 10, 500, ["B.x", "B.y"]),
            rel("C", 400, 20000, ["C.y"])]
    preds = [Pred(C.parse("A.x = B.x"), frozenset([0, 1])),
             Pred(C.parse("B.y = C.y"), frozenset([1, 2]))]
    sizes = {frozenset([0, 1]): (1000, 20), frozenset([1, 2]): (2000, 40),
             frozenset([0, 1, 2]): (4000, 80)}
    return JoinDP(rels, preds, 32, size=lambda S: sizes[S])


class Section51(unittest.TestCase):
    def test_table_reproduces(self):
        dp = section51()
        best = dp.run()
        self.assertEqual(best.cost, 530)
        lines = [" ".join(l.split()) for l in dp.table().splitlines()]
        self.assertEqual(lines[1:8], [
            "{A} scan A 100",
            "{B'} scan B' 10",
            "{C} scan C 400",
            "{A,B'} B' outer, BNL with A inner, 10 + 1×100 110",
            "{A,C} no predicate, skipped -",
            "{B',C} B' outer, BNL with C inner, 10 + 1×400 410",
            "{A,B',C} {A,B'} outer (20 pages), BNL with C inner, 110 + 20 + 1×400 530"])
        # Section 5.1 prints 550 for this line using 1 block for 40 pages;
        # with B - 2 = 30 frames 40 pages are 2 blocks (DESIGN_M4.md).
        self.assertTrue(lines[8].startswith("alternative, {B',C} outer (40 pages)"))
        self.assertTrue(lines[8].endswith("410 + 40 + 2×100 650"))

    def test_winner_is_left_deep_and_joins_the_small_relation_first(self):
        dp = section51()
        best = dp.run()
        self.assertEqual(best.outer, frozenset([0, 1]))
        self.assertEqual(best.inner, 2)
        self.assertEqual(dp.best[best.outer].outer, frozenset([1]))

    def test_every_connected_subset_is_enumerated(self):
        dp = section51()
        dp.run()
        self.assertEqual(sorted(len(S) for S in dp.best), [1, 1, 1, 2, 2, 3])
        self.assertEqual(dp.skipped, [frozenset([0, 2])])

    def test_a_disconnected_graph_may_use_a_cross_product(self):
        dp = section51()
        dp.preds = dp.preds[:1]                      # nothing joins C
        dp.size = lambda S: (1000, 20) if len(S) == 2 else (4000, 80)
        best = dp.run()
        self.assertEqual(best.cost, min(e.cost for e in dp.cands[frozenset([0, 1, 2])]))
        crosses = [e for S, e in dp.best.items() if len(S) > 1 and not e.preds]
        self.assertTrue(crosses)
        self.assertTrue(all(e.algo == "nested_loop_join" for e in crosses))

    def test_smaller_budget_switches_algorithm(self):
        algos = {}
        for B in (4, 32):
            dp = section51()
            dp.B = B
            algos[B] = dp.run().algo
        self.assertEqual(algos[32], "nested_loop_join")
        self.assertNotEqual(algos[4], "nested_loop_join")


if __name__ == "__main__":
    unittest.main()