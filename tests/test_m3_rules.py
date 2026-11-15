"""Module 3 step 5: aggregation rules (3.1) and type rules (3.2).
Required cases 6, 7, 10, 13 and 14."""
import unittest

from sql.errors import AggregateError, SchemaError, TypeError_

from m3_helpers import compile_sql, scan


class Aggregation(unittest.TestCase):
    def test_case_6_non_grouped_column(self):
        with self.assertRaises(AggregateError) as cm:
            compile_sql("SELECT did, name, COUNT(*) FROM Emp GROUP BY did")
        self.assertIn("Emp.name must appear in GROUP BY", cm.exception.message)
        self.assertEqual(cm.exception.pos.col, 13)
        self.assertTrue(cm.exception.render().startswith("error: aggregate: "))

    def test_aggregate_without_group_by_makes_query_aggregating(self):
        with self.assertRaises(AggregateError):
            compile_sql("SELECT name, COUNT(*) FROM Emp")

    def test_star_in_grouped_query(self):
        with self.assertRaises(AggregateError):
            compile_sql("SELECT * FROM Emp GROUP BY did")

    def test_case_7_aggregate_in_where(self):
        with self.assertRaises(AggregateError) as cm:
            compile_sql("SELECT did FROM Emp WHERE COUNT(*) > 3 GROUP BY did")
        self.assertIn("not allowed in WHERE", cm.exception.message)
        self.assertEqual(cm.exception.pos.col, 27)

    def test_aggregate_in_on(self):
        with self.assertRaises(AggregateError):
            compile_sql("SELECT e.eid FROM Emp e JOIN Dept d ON MAX(e.did) = d.did")

    def test_having_only_grouping_columns_and_aggregates(self):
        compile_sql("SELECT did FROM Emp GROUP BY did HAVING did > 3 AND MAX(age) < 60")
        with self.assertRaises(AggregateError) as cm:
            compile_sql("SELECT did FROM Emp GROUP BY did HAVING age > 3")
        self.assertIn("Emp.age is neither", cm.exception.message)

    def test_having_reuses_select_aggregate(self):
        p = compile_sql("SELECT did, COUNT(*) AS n FROM Emp GROUP BY did HAVING COUNT(*) > 5")
        self.assertEqual(p["child"]["condition"], "n > 5")
        self.assertEqual(p["child"]["child"]["aggregates"], [["count", "*", "n"]])

    def test_having_only_aggregate_gets_default_name(self):
        p = compile_sql("SELECT did FROM Emp GROUP BY did HAVING AVG(salary) > 100")
        g = p["child"]["child"]
        self.assertEqual(g["aggregates"], [["avg", "Emp.salary", "avg_Emp_salary"]])
        self.assertEqual(p["child"]["condition"], "avg_Emp_salary > 100")
        self.assertEqual(p["columns"], ["Emp.did"])          # not selected

    def test_default_names(self):
        p = compile_sql("SELECT COUNT(*), COUNT(e.name), SUM(salary), MIN(name) FROM Emp e")
        self.assertEqual(p["columns"], ["count_star", "count_e_name", "sum_e_salary",
                                        "min_e_name"])

    def test_same_aggregate_twice_needs_an_alias(self):
        with self.assertRaises(SchemaError):
            compile_sql("SELECT COUNT(*), COUNT(*) FROM Emp")
        p = compile_sql("SELECT COUNT(*), COUNT(*) AS c2 FROM Emp")
        self.assertEqual(p["columns"], ["count_star", "c2"])

    def test_case_13_count_star_without_group_by(self):
        p = compile_sql("SELECT COUNT(*) FROM Emp")
        self.assertEqual(p, {"op": "project", "columns": ["count_star"],
                             "child": {"op": "group_by", "keys": [],
                                       "aggregates": [["count", "*", "count_star"]],
                                       "child": scan("Emp")}})

    def test_order_by_in_grouped_query(self):
        p = compile_sql("SELECT COUNT(*) AS n FROM Emp GROUP BY did ORDER BY did")
        self.assertEqual(p["op"], "project")                 # sort below: did not selected
        self.assertEqual(p["child"]["keys"], [["Emp.did", "asc"]])
        with self.assertRaises(AggregateError):
            compile_sql("SELECT COUNT(*) AS n FROM Emp GROUP BY did ORDER BY age")


class Types(unittest.TestCase):
    def test_case_10_int_against_string(self):
        with self.assertRaises(TypeError_) as cm:
            compile_sql("SELECT name FROM Emp WHERE age > '30'")
        self.assertEqual(cm.exception.render(),
                         "error: type: cannot compare Emp.age (INT) with '30' (VARCHAR) "
                         "at line 1, column 32")

    def test_equality_too(self):
        with self.assertRaises(TypeError_):
            compile_sql("SELECT name FROM Emp WHERE name = 3")
        with self.assertRaises(TypeError_):
            compile_sql("SELECT e.name FROM Emp e JOIN Dept d ON e.name = d.did")

    def test_literals_against_each_other(self):
        compile_sql("SELECT name FROM Emp WHERE 1 = 1")
        with self.assertRaises(TypeError_):
            compile_sql("SELECT name FROM Emp WHERE 1 = '1'")

    def test_sum_and_avg_need_int(self):
        for fn in ("SUM", "AVG"):
            with self.assertRaises(TypeError_) as cm:
                compile_sql(f"SELECT {fn}(name) FROM Emp")
            self.assertIn("needs an INT column", cm.exception.message)

    def test_count_min_max_take_anything(self):
        compile_sql("SELECT COUNT(name), MIN(name), MAX(name), MIN(age) FROM Emp")

    def test_having_types(self):
        compile_sql("SELECT did FROM Emp GROUP BY did HAVING AVG(age) > 30")
        with self.assertRaises(TypeError_):
            compile_sql("SELECT did FROM Emp GROUP BY did HAVING MAX(name) > 30")

    def test_case_14_union_type_mismatch(self):
        with self.assertRaises(SchemaError) as cm:
            compile_sql("SELECT name FROM Emp UNION SELECT did FROM Dept")
        self.assertIn("column 1 has different types", cm.exception.message)
        self.assertEqual(cm.exception.pos.col, 22)
        self.assertTrue(cm.exception.render().startswith("error: schema: "))

    def test_union_column_count(self):
        with self.assertRaises(SchemaError) as cm:
            compile_sql("SELECT name, age FROM Emp INTERSECT SELECT title FROM Dept")
        self.assertIn("same number of columns", cm.exception.message)

    def test_types_through_subquery(self):
        with self.assertRaises(TypeError_):
            compile_sql("SELECT t.n FROM (SELECT COUNT(*) AS n FROM Emp) t WHERE t.n = 'x'")


if __name__ == "__main__":
    unittest.main()