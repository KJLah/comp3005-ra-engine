"""Module 3 step 2: grammar and parser (Section 2)."""
import unittest

from sql import ast as A
from sql.errors import SyntaxError_
from sql.parser import parse_condition, parse_query, parse_script
from sql.printer import format_condition


class ConditionPrecedence(unittest.TestCase):
    def test_and_binds_tighter_than_or(self):
        c = parse_condition("a = 1 OR b = 2 AND c = 3")
        self.assertIsInstance(c, A.Or)
        self.assertIsInstance(c.right, A.And)

    def test_not_binds_tighter_than_and(self):
        c = parse_condition("NOT a = 1 AND b = 2")
        self.assertIsInstance(c, A.And)
        self.assertIsInstance(c.left, A.Not)

    def test_and_or_are_left_associative(self):
        c = parse_condition("a = 1 OR b = 2 OR c = 3")
        self.assertIsInstance(c.left, A.Or)
        self.assertEqual(c.right.left.name, "c")
        c = parse_condition("a = 1 AND b = 2 AND c = 3")
        self.assertIsInstance(c.left, A.And)

    def test_parentheses_override(self):
        c = parse_condition("(a = 1 OR b = 2) AND c = 3")
        self.assertIsInstance(c, A.And)
        self.assertIsInstance(c.left, A.Or)

    def test_not_nests(self):
        c = parse_condition("NOT NOT a = 1")
        self.assertIsInstance(c.operand, A.Not)

    def test_printing_keeps_the_tree(self):
        for text in ("a = 1 or b = 2 and c = 3", "(a = 1 or b = 2) and c = 3",
                     "not (a = 1 and b = 2)", "not a = 1 and b = 2",
                     "a = 1 or (b = 2 or c = 3)", "x = 'it''s' and y <> -4",
                     "R.a <= S.b"):
            c = parse_condition(text)
            self.assertEqual(format_condition(c), text)
            self.assertEqual(parse_condition(format_condition(c)), c)

    def test_aggregate_operand(self):
        c = parse_condition("COUNT(*) > 5")
        self.assertEqual((c.left.fn, c.left.arg), ("count", None))


class SetOperators(unittest.TestCase):
    def test_left_associative_one_level(self):
        q = parse_query("SELECT a FROM R UNION SELECT a FROM S EXCEPT SELECT a FROM T")
        self.assertEqual(q.op, "except")
        self.assertEqual(q.left.op, "union")
        q = parse_query("SELECT a FROM R EXCEPT SELECT a FROM S UNION SELECT a FROM T")
        self.assertEqual((q.op, q.left.op), ("union", "except"))
        q = parse_query("SELECT a FROM R INTERSECT SELECT a FROM S UNION ALL SELECT a FROM T")
        self.assertEqual((q.op, q.left.op), ("union_all", "intersect"))

    def test_parentheses_group_right(self):
        q = parse_query("SELECT a FROM R UNION (SELECT a FROM S EXCEPT SELECT a FROM T)")
        self.assertEqual((q.op, q.right.op), ("union", "except"))

    def test_union_all(self):
        self.assertEqual(parse_query("SELECT a FROM R UNION ALL SELECT a FROM S").op,
                         "union_all")


class Blocks(unittest.TestCase):
    def test_full_block(self):
        q = parse_query("SELECT DISTINCT did, COUNT(*) AS n FROM Emp WHERE age > 25 "
                        "GROUP BY did HAVING COUNT(*) > 5 ORDER BY n DESC, did LIMIT 10")
        self.assertTrue(q.distinct)
        self.assertEqual([i.alias for i in q.items], [None, "n"])
        self.assertEqual([c.name for c in q.group_by], ["did"])
        self.assertEqual([(o.column.name, o.direction) for o in q.order_by],
                         [("n", "desc"), ("did", "asc")])
        self.assertEqual(q.limit, 10)

    def test_table_alias_with_and_without_as(self):
        q = parse_query("SELECT e.name FROM Emp e JOIN Dept AS d ON e.did = d.did")
        self.assertEqual((q.first.name, q.first.alias), ("Emp", "e"))
        self.assertEqual(q.steps[0].ref.alias, "d")
        self.assertEqual(q.steps[0].kind, "join")

    def test_comma_list(self):
        q = parse_query("SELECT * FROM Emp e, Dept d, Proj")
        self.assertIsInstance(q.items, A.Star)
        self.assertEqual([s.kind for s in q.steps], ["cross", "cross"])

    def test_subquery_in_from(self):
        q = parse_query("SELECT t.n FROM (SELECT COUNT(*) AS n FROM Emp) AS t")
        self.assertIsInstance(q.first, A.SubqueryRef)
        self.assertEqual(q.first.alias, "t")

    def test_keywords_any_case(self):
        q = parse_query("select a from R where a > 1 order by a desc limit 3")
        self.assertEqual(q.limit, 3)


class Statements(unittest.TestCase):
    def test_script_with_several_statements(self):
        s = parse_script("CREATE TABLE R (a INT, b VARCHAR(10));\n"
                         "INSERT INTO R VALUES (1, 'x');\n"
                         "-- a comment\nSELECT a FROM R;;")
        self.assertEqual([type(x).__name__ for x in s], ["CreateTable", "Insert", "SelectBlock"])
        self.assertEqual([(c.name, c.type, c.length) for c in s[0].columns],
                         [("a", "INT", None), ("b", "VARCHAR", 10)])
        self.assertEqual([v.value for v in s[1].values], [1, "x"])

    def test_empty_script(self):
        self.assertEqual(parse_script("  -- nothing\n"), [])


class SyntaxErrors(unittest.TestCase):
    def check(self, text, line, col, fragment):
        with self.assertRaises(SyntaxError_) as cm:
            parse_script(text)
        e = cm.exception
        self.assertEqual((e.pos.line, e.pos.col), (line, col), e.render())
        self.assertIn(fragment, e.message)
        self.assertTrue(e.render().startswith("error: syntax: "))

    def test_missing_from(self):
        self.check("SELECT a WHERE a > 1", 1, 10, "FROM")

    def test_subquery_needs_alias(self):
        self.check("SELECT * FROM (SELECT a FROM R)", 1, 32, "alias")

    def test_mixing_commas_and_join(self):
        self.check("SELECT * FROM R JOIN S ON R.a = S.a, T", 1, 36, "not both")

    def test_select_alias_needs_as(self):
        self.check("SELECT a b FROM R", 1, 10, "FROM")

    def test_having_without_group_by(self):
        self.check("SELECT a FROM R HAVING a > 1", 1, 17, "GROUP BY")

    def test_keyword_as_name(self):
        self.check("SELECT count FROM R", 1, 14, "'('")

    def test_missing_operator(self):
        self.check("SELECT a FROM R WHERE a 1", 1, 25, "comparison operator")

    def test_statements_need_semicolons(self):
        self.check("SELECT a FROM R SELECT b FROM S", 1, 17, "';'")

    def test_unclosed_parenthesis(self):
        self.check("SELECT a FROM R WHERE (a = 1", 1, 29, "')'")

    def test_negative_limit(self):
        self.check("SELECT a FROM R LIMIT -1", 1, 23, "negative")


if __name__ == "__main__":
    unittest.main()