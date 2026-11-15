"""Module 4 Section 2: selectivity and cardinality."""
import unittest

from optimizer.estimate import Estimator, card, pages_for, str_num
from optimizer.stats import ColStats, TableStats

from m4_helpers import section2_stats


def scan(t):
    return {"op": "scan", "table": t}


def sel(c, child=None):
    return {"op": "select", "condition": c, "child": child or scan("Emp")}


class WorkedExample(unittest.TestCase):
    """Section 2: the four lines of the worked example."""

    def setUp(self):
        self.E = Estimator(section2_stats())

    def test_did_eq_7(self):
        n = sel("Emp.did = 7")
        self.assertAlmostEqual(self.E.props(n).sel, 0.02)
        self.assertEqual(self.E.rows(n), 200)

    def test_age_gt_60(self):
        n = sel("Emp.age > 60")
        self.assertAlmostEqual(self.E.props(n).sel, 0.20)
        self.assertEqual(self.E.rows(n), 2000)

    def test_conjunction(self):
        n = sel("Emp.did = 7 and Emp.age > 60")
        self.assertAlmostEqual(self.E.props(n).sel, 0.004)
        self.assertEqual(self.E.rows(n), 40)

    def test_fk_join(self):
        j = {"op": "join", "condition": "Emp.did = Dept.did", "left": scan("Emp"),
             "right": scan("Dept")}
        self.assertEqual(self.E.rows(j), 10000)


class Formulas(unittest.TestCase):
    def setUp(self):
        hist = [[1, 10, 600, 10], [11, 12, 300, 2], [13, 100, 100, 88]]
        self.t = TableStats("T", 1000, 10, 40.0, {
            "a": ColStats(100, 1, 100, 4.0, hist),
            "s": ColStats(3, "apple", "melon", 8.0),
            "x": ColStats(10, 0, 9, 4.0)}, True)
        self.E = Estimator({"T": self.t})

    def s(self, c):
        return self.E.props(sel(c, scan("T"))).sel

    def test_eq_uses_the_bucket(self):
        self.assertAlmostEqual(self.s("T.a = 11"), 300 / (1000 * 2))
        self.assertAlmostEqual(self.s("T.a = 50"), 100 / (1000 * 88))

    def test_eq_without_histogram_is_one_over_ndv(self):
        self.assertAlmostEqual(self.s("T.x = 3"), 0.1)

    def test_eq_out_of_range_is_zero_but_rows_never_below_one(self):
        self.assertEqual(self.s("T.a = 500"), 0.0)
        self.assertEqual(self.E.rows(sel("T.a = 500", scan("T"))), 1)

    def test_range_sums_buckets_plus_a_fraction(self):
        self.assertAlmostEqual(self.s("T.a > 12"), 0.1)
        self.assertAlmostEqual(self.s("T.a < 11"), 0.6)
        mid = self.s("T.a < 5.5")
        self.assertAlmostEqual(mid, 600 * 0.5 / 1000)

    def test_flipped_constant(self):
        self.assertAlmostEqual(self.s("12 < T.a"), self.s("T.a > 12"))

    def test_not_equal_or_not(self):
        self.assertAlmostEqual(self.s("T.x <> 3"), 0.9)
        self.assertAlmostEqual(self.s("T.x = 3 or T.x = 4"), 0.1 + 0.1 - 0.01)
        self.assertAlmostEqual(self.s("not T.x = 3"), 0.9)

    def test_varchar_range_is_interpolated(self):
        s = self.s("T.s < 'grape'")
        self.assertTrue(0.2 < s < 0.6, s)
        self.assertLess(str_num("apple"), str_num("grape"))
        self.assertLess(str_num("grape"), str_num("melon"))

    def test_same_table_column_equality(self):
        self.assertAlmostEqual(self.s("T.a = T.x"), 1 / 100)

    def test_other_two_column_predicate_is_point_one(self):
        self.assertAlmostEqual(self.s("T.a < T.x"), 0.1)

    def test_cardinality_and_pages(self):
        self.assertEqual(card(0.2), 1)
        self.assertEqual(card(10.01), 11)
        self.assertEqual(pages_for(1000, 40.96), 10)

    def test_projection_reduces_pages(self):
        p = {"op": "project", "columns": ["T.a"], "child": scan("T")}
        self.assertEqual(self.E.props(p).pages, pages_for(1000, 4.0))
        self.assertLess(self.E.props(p).pages, self.t.n_pages)


class NoStatistics(unittest.TestCase):
    def test_documented_defaults(self):
        from optimizer.stats import defaults_for
        from storage.catalog import TableInfo
        from storage.record import parse_schema
        info = TableInfo("T", parse_schema("a:INT,b:VARCHAR(10)"), "T.db", 11, 1000)
        E = Estimator({"T": defaults_for(info)})
        n = sel("T.a = 5", scan("T"))
        self.assertAlmostEqual(E.props(n).sel, 0.1)
        self.assertAlmostEqual(E.props(sel("T.a > 5", scan("T"))).sel, 1 / 3)
        self.assertEqual(E.props(scan("T")).pages, 10)
        j = {"op": "join", "condition": "a.a = b.a",
             "left": {"op": "rename", "to": "a", "child": scan("T")},
             "right": {"op": "rename", "to": "b", "child": scan("T")}}
        self.assertEqual(E.rows(j), 1000 * 1000 // 100)          # ndv = n/10


if __name__ == "__main__":
    unittest.main()