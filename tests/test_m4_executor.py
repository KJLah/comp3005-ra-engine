"""Module 4 step 1: the executor forms Module 3 plans need (rename, cross,
project with names, named aggregates)."""
import unittest

from execution.errors import PlanError
from execution.plan import validate

from m2_helpers import make_db, run_plan, scan

EMP = "eid:INT,name:VARCHAR(10),mgr:INT,did:INT"
ROWS = ["1,Ann,0,1", "2,Bob,1,1", "3,Cy,1,2"]


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = make_db({"Emp": (EMP, ROWS), "Dept": ("did:INT,title:VARCHAR(10)",
                                                         ["1,Eng", "2,Ops"])})

    def tearDown(self):
        self.tmp.cleanup()

    def run_(self, plan, frames=8):
        return run_plan(self.tmp.name, plan, frames)


class Rename(Base):
    def test_self_join_through_two_renames(self):
        plan = {"op": "project", "columns": ["e.name", "m.name"],
                "child": {"op": "nested_loop_join", "condition": "e.mgr = m.eid",
                          "left": {"op": "rename", "to": "e", "child": scan("Emp")},
                          "right": {"op": "rename", "to": "m", "child": scan("Emp")}}}
        self.assertEqual(sorted(self.run_(plan).rows), [("Bob", "Ann"), ("Cy", "Ann")])

    def test_rename_requalifies_unqualified_names(self):
        plan = {"op": "project", "columns": ["t.did", "t.n"],
                "child": {"op": "rename", "to": "t", "child": {
                    "op": "group_by", "mode": "hash", "keys": ["Emp.did"],
                    "aggregates": [["count", "*", "n"]], "child": scan("Emp")}}}
        self.assertEqual(sorted(self.run_(plan).rows), [(1, 2), (2, 1)])

    def test_rename_costs_nothing(self):
        r = self.run_({"op": "rename", "to": "e", "child": scan("Emp")})
        c = r.node("plan")
        self.assertEqual((c["page_reads"], c["page_writes"], c["comparisons"]), (0, 0, 0))
        self.assertEqual(len(r.rows), 3)

    def test_bad_rename(self):
        with self.assertRaises(PlanError):
            validate({"op": "rename", "to": "a.b", "child": scan("Emp")})


class Cross(Base):
    def test_nested_loop_join_without_condition(self):
        plan = {"op": "nested_loop_join", "left": scan("Emp"), "right": scan("Dept")}
        rows = self.run_(plan).rows
        self.assertEqual(len(rows), 6)

    def test_other_joins_still_need_a_condition(self):
        for op in ("sort_merge_join", "hash_join"):
            with self.assertRaises(PlanError):
                validate({"op": op, "left": scan("Emp"), "right": scan("Dept")})


class Names(Base):
    def test_project_as(self):
        plan = {"op": "sort", "keys": [["who", "asc"]],
                "child": {"op": "project", "columns": ["Emp.name", "Emp.did"],
                          "as": ["who", None], "child": scan("Emp")}}
        r = self.run_(plan)
        self.assertEqual(r.rows[0], ("Ann", 1))
        self.assertEqual(r.root.schema.names(), ["who", "Emp.did"])

    def test_project_as_must_match_columns(self):
        with self.assertRaises(PlanError):
            validate({"op": "project", "columns": ["Emp.name"], "as": ["a", "b"],
                      "child": scan("Emp")})

    def test_named_aggregates(self):
        plan = {"op": "filter", "condition": "n > 1",
                "child": {"op": "group_by", "mode": "sort", "keys": ["Emp.did"],
                          "aggregates": [["count", "*", "n"], ["max", "Emp.eid", "top"]],
                          "child": scan("Emp")}}
        r = self.run_(plan)
        self.assertEqual(r.rows, [(1, 2, 2)])
        self.assertEqual(r.root.schema.names(), ["Emp.did", "n", "top"])

    def test_two_field_aggregates_unchanged(self):
        plan = {"op": "group_by", "mode": "hash", "keys": [],
                "aggregates": [["count", "*"]], "child": scan("Emp")}
        r = self.run_(plan)
        self.assertEqual(r.root.schema.names(), ["count_star"])


if __name__ == "__main__":
    unittest.main()