"""Module 2 step 4: distinct, group_by, union, intersect, except."""
import random
import unittest
from collections import Counter, defaultdict

from execution.errors import BindError, ExecError

from m2_helpers import make_db, run_plan, scan

rnd = random.Random(7)
DEPTS = ["cs", "math", "bio", "phys", "chem"]
G_ROWS = [(DEPTS[i % 5], rnd.randint(1, 100000), f"n{rnd.randint(0, 1999):04d}")
          for i in range(3000)]
D_ROWS = [(rnd.randint(0, 1499), "x" * 30) for _ in range(5000)]
A_ROWS = [(rnd.randint(0, 2999), "a" * 40) for _ in range(4000)]
B_ROWS = [(rnd.randint(1500, 4499), "a" * 40) for _ in range(4000)]


def csv(rows):
    return [",".join(str(v) for v in r) for r in rows]


class Aggregates(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = make_db({
            "G": ("dept:VARCHAR(10),salary:INT,name:VARCHAR(20)", csv(G_ROWS)),
            "D": ("a:INT,b:VARCHAR(30)", csv(D_ROWS)),
            "A": ("a:INT,b:VARCHAR(40)", csv(A_ROWS)),
            "B": ("a:INT,b:VARCHAR(40)", csv(B_ROWS)),
            "E": ("dept:VARCHAR(10),salary:INT,name:VARCHAR(20)", []),
        })
        cls.db = cls.tmp.name

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    def run_(self, plan, frames=4):
        return run_plan(self.db, plan, frames)

    # ---- distinct ---------------------------------------------------------------------
    def test_distinct_both_modes_at_several_b(self):
        expect = sorted(set(D_ROWS))
        for mode in ("sort", "hash"):
            for frames in (4, 5, 16, 64):
                res = self.run_({"op": "distinct", "mode": mode, "child": scan("D")}, frames)
                self.assertEqual(sorted(res.rows), expect, (mode, frames))

    def test_hash_distinct_partitions_when_it_does_not_fit(self):
        res = self.run_({"op": "distinct", "mode": "hash", "child": scan("D")}, 4)
        self.assertGreater(res.root.partitions_made, 0)
        self.assertGreater(res.node("plan")["page_writes"], 0)
        small = self.run_({"op": "distinct", "mode": "hash", "child": scan("D")}, 64)
        self.assertEqual(small.root.partitions_made, 0)
        self.assertEqual(small.node("plan")["page_writes"], 0)

    def test_sort_distinct_of_project_drops_adjacent_duplicates(self):
        res = self.run_({"op": "distinct", "mode": "sort",
                         "child": {"op": "project", "columns": ["G.dept"], "child": scan("G")}})
        self.assertEqual(res.rows, [(d,) for d in sorted(DEPTS)])

    # ---- group_by ---------------------------------------------------------------------
    def reference(self, key_fn):
        groups = defaultdict(list)
        for r in G_ROWS:
            groups[key_fn(r)].append(r)
        return groups

    def test_group_by_handout_example_both_modes(self):
        plan = {"op": "group_by", "keys": ["G.dept"],
                "aggregates": [["count", "*"], ["avg", "G.salary"], ["max", "G.name"]],
                "child": scan("G")}
        expect = sorted((d, len(rs), sum(r[1] for r in rs) / len(rs), max(r[2] for r in rs))
                        for d, rs in self.reference(lambda r: r[0]).items())
        for mode in ("sort", "hash"):
            res = self.run_({**plan, "mode": mode})
            self.assertEqual(res.root.schema.names(),
                             ["G.dept", "count_star", "avg_G_salary", "max_G_name"])
            self.assertEqual(sorted(res.rows), expect, mode)

    def test_group_by_many_groups_overflows_and_still_agrees(self):
        plan = {"op": "group_by", "keys": ["G.name"],
                "aggregates": [["sum", "G.salary"], ["min", "G.dept"], ["count", "G.dept"]],
                "child": scan("G")}
        expect = sorted((n, sum(r[1] for r in rs), min(r[0] for r in rs), len(rs))
                        for n, rs in self.reference(lambda r: r[2]).items())
        sort_res = self.run_({**plan, "mode": "sort"})
        hash_res = self.run_({**plan, "mode": "hash"})
        self.assertEqual(sorted(sort_res.rows), expect)
        self.assertEqual(sorted(hash_res.rows), expect)
        self.assertGreater(hash_res.root.partitions_made, 0)

    def test_no_keys_aggregates_everything(self):
        res = self.run_({"op": "group_by", "mode": "hash", "keys": [],
                         "aggregates": [["count", "*"], ["sum", "G.salary"]],
                         "child": scan("G")})
        self.assertEqual(res.rows, [(3000, sum(r[1] for r in G_ROWS))])

    def test_empty_input_edge_cases(self):
        for mode in ("sort", "hash"):
            res = self.run_({"op": "group_by", "mode": mode, "keys": [],
                             "aggregates": [["count", "*"]], "child": scan("E")})
            self.assertEqual(res.rows, [(0,)])
            res = self.run_({"op": "group_by", "mode": mode, "keys": ["E.dept"],
                             "aggregates": [["count", "*"], ["avg", "E.salary"]],
                             "child": scan("E")})
            self.assertEqual(res.rows, [])
            with self.assertRaises(ExecError):
                self.run_({"op": "group_by", "mode": mode, "keys": [],
                           "aggregates": [["count", "*"], ["avg", "E.salary"]],
                           "child": scan("E")})
            self.assertEqual(self.run_({"op": "distinct", "mode": mode,
                                        "child": scan("E")}).rows, [])

    def test_sum_and_avg_require_int(self):
        with self.assertRaises(BindError):
            self.run_({"op": "group_by", "mode": "sort", "keys": [],
                       "aggregates": [["sum", "G.name"]], "child": scan("G")})

    def test_group_by_feeds_a_sort(self):
        """AVG (REAL) and SUM (8-byte INT) must survive a trip through temp files."""
        res = self.run_({"op": "sort", "keys": [["avg_G_salary", "desc"]], "child": {
            "op": "group_by", "mode": "hash", "keys": ["G.name"],
            "aggregates": [["avg", "G.salary"], ["sum", "G.salary"]], "child": scan("G")}})
        avgs = [r[1] for r in res.rows]
        self.assertEqual(avgs, sorted(avgs, reverse=True))

    def test_counters_repeat(self):
        plan = {"op": "group_by", "mode": "hash", "keys": ["G.name"],
                "aggregates": [["count", "*"]], "child": scan("G")}
        a = [op.counters() for _, op in self.run_(plan).root.walk()]
        b = [op.counters() for _, op in self.run_(plan).root.walk()]
        self.assertEqual(a, b)

    # ---- set operators ---------------------------------------------------------------
    def test_union_intersect_except_set_semantics_beyond_memory(self):
        a, b = set(A_ROWS), set(B_ROWS)
        for op, expect in (("union", a | b), ("intersect", a & b), ("except", a - b)):
            for frames in (4, 8):
                res = self.run_({"op": op, "left": scan("A"), "right": scan("B")}, frames)
                self.assertEqual(sorted(res.rows), sorted(expect), (op, frames))
                self.assertEqual(len(res.rows), len(set(res.rows)))

    def test_set_ops_take_left_names_and_check_types(self):
        res = self.run_({"op": "intersect", "left": scan("A"),
                         "right": {"op": "project", "columns": ["B.a", "B.b"],
                                   "child": scan("B")}})
        self.assertEqual(res.root.schema.names(), ["A.a", "A.b"])
        with self.assertRaises(BindError):
            self.run_({"op": "except", "left": scan("A"),
                       "right": {"op": "project", "columns": ["B.b", "B.a"],
                                 "child": scan("B")}})

    def test_set_ops_with_empty_sides(self):
        e = {"op": "project", "columns": ["E.salary", "E.name"], "child": scan("E")}
        self.assertEqual(self.run_({"op": "intersect", "left": scan("A"), "right": e}).rows, [])
        self.assertEqual(len(self.run_({"op": "except", "left": scan("A"), "right": e}).rows),
                         len(set(A_ROWS)))
        self.assertEqual(self.run_({"op": "union", "left": e, "right": e}).rows, [])

    def test_set_op_under_a_sort_materialises(self):
        """A sort leaves its child 1 frame at B = 4; intersect needs 2 to
        stream, so it writes its output to a temp file and streams that."""
        res = self.run_({"op": "sort", "keys": [["A.a", "desc"]], "child": {
            "op": "intersect", "left": scan("A"), "right": scan("B")}})
        expect = sorted(set(A_ROWS) & set(B_ROWS), reverse=True)
        self.assertEqual(res.rows, expect)


if __name__ == "__main__":
    unittest.main()
