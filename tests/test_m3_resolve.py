"""Module 3 step 4: scopes and name resolution (Section 3).
Required cases 1, 2, 3, 9, 11 and 12."""
import unittest

from sql.errors import AmbiguityError, NameError_, SchemaError
from sql.logical import check_plan, walk

from m3_helpers import compile_sql, rename, scan


class Scope(unittest.TestCase):
    def test_case_12_duplicate_alias(self):
        with self.assertRaises(NameError_) as cm:
            compile_sql("SELECT * FROM Emp, Emp")
        self.assertIn("duplicate alias 'Emp'", cm.exception.message)
        self.assertEqual(cm.exception.pos.col, 20)
        self.assertTrue(cm.exception.render().startswith("error: name: duplicate alias"))

    def test_two_aliases_for_one_table_are_fine(self):
        compile_sql("SELECT e.eid FROM Emp e, Emp m")

    def test_alias_equal_to_another_table_name_clashes(self):
        with self.assertRaises(NameError_):
            compile_sql("SELECT * FROM Emp Dept, Dept")

    def test_case_1_self_join(self):
        p = compile_sql("SELECT e.name, m.name FROM Emp e JOIN Emp m ON e.mgr = m.eid")
        self.assertEqual(p["child"], {"op": "join", "condition": "e.mgr = m.eid",
                                      "left": rename("e", scan("Emp")),
                                      "right": rename("m", scan("Emp"))})
        self.assertEqual(p["columns"], ["e.name", "m.name"])

    def test_alias_hides_the_table_name(self):
        with self.assertRaises(NameError_) as cm:
            compile_sql("SELECT Emp.name FROM Emp e")
        self.assertIn("unknown table or alias 'Emp'", cm.exception.message)


class Qualified(unittest.TestCase):
    def test_case_11_unknown_table_and_unknown_column_differ(self):
        with self.assertRaises(NameError_) as t:
            compile_sql("SELECT name FROM Empp")
        with self.assertRaises(NameError_) as c:
            compile_sql("SELECT nam FROM Emp")
        self.assertIn("unknown table 'Empp'", t.exception.message)
        self.assertIn("unknown column 'nam'", c.exception.message)
        self.assertNotEqual(t.exception.message, c.exception.message)
        self.assertEqual((t.exception.pos.col, c.exception.pos.col), (18, 8))

    def test_qualified_names_which_part_failed(self):
        with self.assertRaises(NameError_) as cm:
            compile_sql("SELECT x.name FROM Emp e")
        self.assertIn("unknown table or alias 'x'", cm.exception.message)
        with self.assertRaises(NameError_) as cm:
            compile_sql("SELECT e.nope FROM Emp e")
        self.assertIn("'e' has no column 'nope'", cm.exception.message)

    def test_identifiers_are_case_sensitive(self):
        with self.assertRaises(NameError_):
            compile_sql("SELECT name FROM emp")
        with self.assertRaises(NameError_):
            compile_sql("SELECT Name FROM Emp")


class Unqualified(unittest.TestCase):
    def test_unique_name_is_qualified(self):
        p = compile_sql("SELECT title FROM Emp e, Dept d")
        self.assertEqual(p["columns"], ["d.title"])

    def test_case_3_ambiguous_column_names_both(self):
        with self.assertRaises(AmbiguityError) as cm:
            compile_sql("SELECT did FROM Emp, Dept")
        msg = cm.exception.message
        self.assertIn("Emp.did", msg)
        self.assertIn("Dept.did", msg)
        self.assertTrue(cm.exception.render().startswith("error: ambiguity: "))
        self.assertEqual(cm.exception.pos.col, 8)

    def test_ambiguous_in_where(self):
        with self.assertRaises(AmbiguityError):
            compile_sql("SELECT e.eid FROM Emp e, Dept d WHERE city = 'x'")

    def test_on_sees_only_tables_joined_so_far(self):
        with self.assertRaises(NameError_):
            compile_sql("SELECT e.eid FROM Emp e JOIN Dept d ON e.did = p.did "
                        "JOIN Proj p ON p.did = d.did")


class Star(unittest.TestCase):
    def test_case_2_star_over_three_tables_with_a_shared_name(self):
        p = compile_sql("SELECT * FROM Emp, Dept, Proj")
        cols = p["columns"]
        self.assertEqual(cols[:7], ["Emp.eid", "Emp.name", "Emp.age", "Emp.salary",
                                    "Emp.did", "Emp.mgr", "Emp.city"])
        self.assertEqual(cols[7:11], ["Dept.did", "Dept.title", "Dept.budget", "Dept.city"])
        self.assertEqual(cols[11:], ["Proj.pid", "Proj.pname", "Proj.did"])
        self.assertEqual(sum(c.endswith(".did") for c in cols), 3)

    def test_star_uses_aliases(self):
        p = compile_sql("SELECT * FROM Emp e, Dept d")
        self.assertEqual(p["columns"][0], "e.eid")
        self.assertEqual(p["columns"][7], "d.did")


class OrderBy(unittest.TestCase):
    def test_alias_first(self):
        p = compile_sql("SELECT name AS age, salary FROM Emp ORDER BY age")
        self.assertEqual(p["op"], "sort")
        self.assertEqual(p["keys"], [["age", "asc"]])   # the alias, not Emp.age
        self.assertEqual(p["child"]["as"], ["age", None])

    def test_from_scope_second(self):
        p = compile_sql("SELECT name, age FROM Emp ORDER BY Emp.age DESC")
        self.assertEqual(p["keys"], [["Emp.age", "desc"]])
        self.assertEqual(p["child"]["op"], "project")

    def test_column_selected_under_an_alias(self):
        p = compile_sql("SELECT age AS years FROM Emp ORDER BY age")
        self.assertEqual(p["keys"], [["years", "asc"]])

    def test_case_9_order_by_column_not_selected(self):
        p = compile_sql("SELECT name FROM Emp ORDER BY age")
        self.assertEqual(p, {"op": "project", "columns": ["Emp.name"],
                             "child": {"op": "sort", "keys": [["Emp.age", "asc"]],
                                       "child": scan("Emp")}})
        self.assertEqual(check_plan(p), [])

    def test_not_selected_with_alias_mix(self):
        p = compile_sql("SELECT name AS nm FROM Emp ORDER BY nm, age DESC LIMIT 2")
        self.assertEqual(p["op"], "limit")
        proj = p["child"]
        self.assertEqual(proj["op"], "project")
        self.assertEqual(proj["child"]["keys"], [["Emp.name", "asc"], ["Emp.age", "desc"]])

    def test_distinct_needs_order_by_in_select_list(self):
        with self.assertRaises(SchemaError):
            compile_sql("SELECT DISTINCT name FROM Emp ORDER BY age")


class Subqueries(unittest.TestCase):
    def test_subquery_scope_exposes_its_output_names(self):
        p = compile_sql("SELECT t.n FROM (SELECT did, COUNT(*) AS n FROM Emp GROUP BY did) t")
        self.assertEqual(p["columns"], ["t.n"])
        with self.assertRaises(NameError_):
            compile_sql("SELECT t.age FROM (SELECT did FROM Emp) AS t")

    def test_subquery_output_alias(self):
        p = compile_sql("SELECT t.who FROM (SELECT name AS who FROM Emp) AS t")
        self.assertEqual(p["columns"], ["t.who"])

    def test_subquery_cannot_see_outer_tables(self):
        with self.assertRaises(NameError_):
            compile_sql("SELECT * FROM Dept d, (SELECT d.did FROM Emp) AS t")

    def test_subquery_with_two_columns_of_one_name(self):
        with self.assertRaises(SchemaError) as cm:
            compile_sql("SELECT * FROM (SELECT e.name, m.name FROM Emp e, Emp m) AS t")
        self.assertIn("'name'", cm.exception.message)


class NoUnqualifiedNames(unittest.TestCase):
    QUERIES = [
        "SELECT name FROM Emp WHERE age > 3 AND city = 'Ottawa' ORDER BY salary",
        "SELECT * FROM Emp e JOIN Dept d ON e.did = d.did JOIN Proj p ON p.did = d.did",
        "SELECT title, COUNT(eid) FROM Emp e, Dept d WHERE e.did = d.did GROUP BY title",
        "SELECT t.x FROM (SELECT MAX(age) AS x FROM Emp) AS t",
    ]

    def test_every_column_is_qualified(self):
        for q in self.QUERIES:
            p = compile_sql(q)
            self.assertEqual(check_plan(p), [], q)


if __name__ == "__main__":
    unittest.main()