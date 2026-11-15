"""Module 3 step 3: logical plan shape, explain output, checklist validator."""
import json
import unittest

from sql import logical as L


EX_5_1 = {"op": "project", "columns": ["Emp.name", "Emp.age"],
          "child": {"op": "select", "condition": "Emp.age > 30",
                    "child": {"op": "scan", "table": "Emp"}}}


class Shape(unittest.TestCase):
    def test_constructors_match_the_handout_json(self):
        p = L.project(["Emp.name", "Emp.age"], L.select("Emp.age > 30", L.scan("Emp")))
        self.assertEqual(p, EX_5_1)
        # key order too, so printed JSON reads like the handout
        self.assertEqual(json.dumps(p), json.dumps(EX_5_1))

    def test_project_as_only_when_an_alias_exists(self):
        self.assertNotIn("as", L.project(["R.a"], L.scan("R"), [None]))
        p = L.project(["R.a", "R.b"], L.scan("R"), [None, "x"])
        self.assertEqual(p["as"], [None, "x"])
        self.assertEqual(list(p), ["op", "columns", "as", "child"])

    def test_setop_names(self):
        for op in L.SET_OPS:
            self.assertEqual(L.setop(op, L.scan("R"), L.scan("S"))["op"], op)


class Explain(unittest.TestCase):
    def test_indented_tree(self):
        self.assertEqual(L.explain(EX_5_1),
                         'project ["Emp.name", "Emp.age"]\n'
                         '  select "Emp.age > 30"\n'
                         '    scan Emp')

    def test_union_and_union_all_differ(self):
        a = L.project(["Emp.city"], L.scan("Emp"))
        b = L.project(["Dept.city"], L.scan("Dept"))
        u = L.explain(L.setop("union", a, b)).splitlines()
        ua = L.explain(L.setop("union_all", a, b)).splitlines()
        self.assertEqual(u[0], "union")
        self.assertEqual(ua[0], "union_all")
        self.assertEqual(u[1:], ua[1:])

    def test_every_node_kind(self):
        p = L.limit(10, L.sort([["n", "desc"]], L.distinct(L.project(
            ["e.did", "n"], L.group_by(["e.did"], [["count", "*", "n"]],
                                       L.cross(L.rename("e", L.scan("Emp")),
                                               L.join("a.x = b.x", L.scan("A"), L.scan("B"))))))))
        self.assertEqual(L.explain(p).splitlines(), [
            "limit 10",
            '  sort [["n", "desc"]]',
            "    distinct",
            '      project ["e.did", "n"]',
            '        group_by keys ["e.did"] aggregates [["count", "*", "n"]]',
            "          cross",
            "            rename e",
            "              scan Emp",
            '            join "a.x = b.x"',
            "              scan A",
            "              scan B"])


class Validator(unittest.TestCase):
    def test_clean_plan(self):
        self.assertEqual(L.check_plan(EX_5_1), [])

    def test_physical_name_rejected(self):
        p = {"op": "hash_join", "condition": "R.a = S.a",
             "left": L.scan("R"), "right": L.scan("S")}
        self.assertIn("not a logical operator", L.check_plan(p)[0])

    def test_unqualified_column_rejected(self):
        self.assertTrue(L.check_plan(L.project(["name"], L.scan("Emp"))))
        self.assertTrue(L.check_plan(L.select("age > 3", L.scan("Emp"))))

    def test_aggregate_output_names_allowed_above_their_group_by(self):
        g = L.group_by(["Emp.did"], [["count", "*", "n"]], L.scan("Emp"))
        self.assertEqual(L.check_plan(L.project(["Emp.did", "n"], L.select("n > 5", g))), [])
        # but not below it
        bad = L.group_by(["Emp.did"], [["count", "*", "n"]], L.select("n > 5", L.scan("Emp")))
        self.assertTrue(L.check_plan(bad))

    def test_aggregate_call_in_condition_rejected(self):
        self.assertTrue(L.check_plan(L.select("COUNT(*) > 5", L.scan("Emp"))))


if __name__ == "__main__":
    unittest.main()