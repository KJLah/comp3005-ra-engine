"""Module 4 Section 3: rewrite rules, the worked example and preconditions."""
import unittest

from optimizer.explain import logical_lines
from optimizer.rewrite import Rewriter

from m3_helpers import compile_sql

TABLES = {"Emp": ["eid", "name", "age", "salary", "did", "mgr", "city"],
          "Dept": ["did", "title", "budget", "city"],
          "Proj": ["pid", "pname", "did"], "Works": ["eid", "pid", "hours"]}


def rw(sql, rules=range(1, 8)):
    r = Rewriter(TABLES, rules)
    out = r.run(compile_sql(sql))
    return out, r.fired


def tree(plan):
    return "\n".join(logical_lines(plan))


def ops(plan):
    out = [plan["op"]]
    for k in ("child", "left", "right"):
        if k in plan:
            out += ops(plan[k])
    return out


class Section3Example(unittest.TestCase):
    SQL = ("SELECT e.name FROM Emp e, Dept d "
           "WHERE e.did = d.did AND e.age > 60 AND d.budget > 1000")

    def test_after_tree(self):
        out, fired = rw(self.SQL)
        self.assertEqual(tree(out), "\n".join([
            "project [e.name]",
            "  join e.did = d.did",
            "    project [e.name,e.did]",
            "      select e.age > 60",
            "        rename e (scan Emp)",
            "    project [d.did]",
            "      select d.budget > 1000",
            "        rename d (scan Dept)"]))
        self.assertEqual(fired, {1, 2, 3, 6})

    def test_before_tree_is_the_module3_plan(self):
        self.assertEqual(tree(compile_sql(self.SQL)).splitlines()[:3], [
            "project [e.name]",
            "  select e.did = d.did and e.age > 60 and d.budget > 1000",
            "    cross"])

    def test_comma_and_join_on_rewrite_to_the_same_tree(self):
        a, _ = rw(self.SQL)
        b, _ = rw("SELECT e.name FROM Emp e JOIN Dept d ON e.did = d.did "
                  "WHERE e.age > 60 AND d.budget > 1000")
        self.assertEqual(a, b)


class Preconditions(unittest.TestCase):
    def test_rule2_never_pushes_a_two_sided_predicate(self):
        out, _ = rw("SELECT e.name FROM Emp e, Dept d WHERE e.age > d.budget")
        self.assertEqual(ops(out)[:3], ["project", "select", "cross"])

    def test_rule3_needs_an_equality(self):
        out, fired = rw("SELECT e.name FROM Emp e, Dept d WHERE e.did < d.did")
        self.assertNotIn("join", ops(out))
        self.assertNotIn(3, fired)

    def test_rule5_never_moves_an_aggregate_predicate(self):
        out, _ = rw("SELECT did, COUNT(*) FROM Emp GROUP BY did HAVING COUNT(*) > 2")
        self.assertEqual(ops(out)[:2], ["select", "group_by"])

    def test_rule5_moves_a_key_predicate(self):
        out, fired = rw("SELECT did, COUNT(*) FROM Emp GROUP BY did HAVING did > 2")
        self.assertEqual(ops(out)[0], "group_by")
        self.assertIn("select", ops(out))
        self.assertIn(5, fired)

    def test_rule4_rewrites_names_through_a_rename(self):
        out, fired = rw("SELECT t.n FROM (SELECT name AS n, age FROM Emp) AS t WHERE t.age > 3")
        self.assertIn(4, fired)
        text = tree(out)
        self.assertIn("select Emp.age > 3", text)

    def test_rule4_pushes_into_both_union_branches(self):
        out, _ = rw("SELECT u.city FROM (SELECT city FROM Emp UNION ALL SELECT city FROM Dept) "
                    "AS u WHERE u.city = 'X'")
        text = tree(out)
        self.assertIn("select Emp.city = 'X'", text)
        self.assertIn("select Dept.city = 'X'", text)

    def test_rule6_keeps_columns_a_predicate_above_needs(self):
        out, _ = rw("SELECT e.name FROM Emp e JOIN Dept d ON e.did = d.did "
                    "WHERE e.salary > d.budget")
        text = tree(out)
        self.assertIn("e.salary", text.split("join")[1])
        self.assertIn("d.budget", text.split("join")[1])

    def test_rule7_collapses_adjacent_selects(self):
        out, _ = rw("SELECT name FROM Emp WHERE age > 3 AND salary > 4")
        self.assertEqual(ops(out), ["project", "select", "scan"])


class Switches(unittest.TestCase):
    SQL = Section3Example.SQL

    def test_rule1_off_keeps_the_conjunction_whole(self):
        out, fired = rw(self.SQL, rules={2, 3, 4, 5, 6, 7})
        self.assertNotIn("join", ops(out))
        self.assertNotIn(1, fired)

    def test_rule3_off_keeps_the_cross(self):
        out, _ = rw(self.SQL, rules={1, 2, 4, 5, 6, 7})
        self.assertIn("cross", ops(out))
        self.assertNotIn("join", ops(out))

    def test_rule6_off_adds_no_projection(self):
        out, _ = rw(self.SQL, rules={1, 2, 3, 4, 5, 7})
        self.assertEqual(ops(out).count("project"), 1)

    def test_no_rules_is_the_input(self):
        out, fired = rw(self.SQL, rules=())
        self.assertEqual(out, compile_sql(self.SQL))
        self.assertEqual(fired, set())


if __name__ == "__main__":
    unittest.main()