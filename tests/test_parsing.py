"""Required test cases 1-17 (tokenizer, grammar, precedence), plus extra
cases of our own. Run from the repository root with:

    python -m unittest discover -s tests -v
"""
import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from ra_engine import lexer as L                      # noqa: E402
from ra_engine.ast_nodes import Compare, SetOp        # noqa: E402
from ra_engine.errors import LexicalError, ParseError  # noqa: E402
from ra_engine.parser import parse_definitions, parse_query  # noqa: E402
from ra_engine.printer import format_tree             # noqa: E402


def kinds_and_texts(q):
    return [(t.kind, t.text) for t in L.tokenize(q) if t.kind != L.EOF]


def tree(q):
    return format_tree(parse_query(q))


def only_comparison(q):
    """Return the Compare node of a query of the form select[a op b](R)."""
    cond = parse_query(q).cond
    assert isinstance(cond, Compare)
    return cond


class Tokenizer_7_1(unittest.TestCase):
    def test_01_no_whitespace(self):
        self.assertEqual(kinds_and_texts("select[x1=3](R)"), [
            (L.KEYWORD, "select"), (L.LBRACKET, "["), (L.IDENT, "x1"), (L.OP, "="),
            (L.NUMBER, "3"), (L.RBRACKET, "]"), (L.LPAREN, "("), (L.IDENT, "R"),
            (L.RPAREN, ")")])

    def test_02_whitespace_gives_identical_tree(self):
        self.assertEqual(tree("select[ x1 = 3 ](R)"), tree("select[x1=3](R)"))

    def test_03_ge_is_one_token(self):
        toks = kinds_and_texts("select[Age>=30](R)")
        self.assertIn((L.OP, ">="), toks)
        self.assertNotIn((L.OP, ">"), toks)

    def test_04_gt_then_negative_number(self):
        toks = kinds_and_texts("select[Age>-30](R)")
        self.assertIn((L.OP, ">"), toks)
        self.assertIn((L.NUMBER, "-30"), toks)
        c = only_comparison("select[Age>-30](R)")
        self.assertEqual((c.op, c.right.value), (">", -30))

    def test_05_paren_inside_string(self):
        self.assertEqual(only_comparison("select[Name='Bob)'](R)").right.value, "Bob)")

    def test_06_comma_inside_string(self):
        self.assertEqual(only_comparison("select[Name='a,b'](R)").right.value, "a,b")

    def test_07_doubled_quote(self):
        self.assertEqual(only_comparison("select[Name='O''Brien'](R)").right.value,
                         "O'Brien")

    def test_08_attribute_spelled_like_keyword(self):
        c = only_comparison("select[union=3](R)")
        self.assertEqual((c.left.name, c.right.value), ("union", 3))

    def test_09_unterminated_string(self):
        with self.assertRaises(LexicalError) as cm:
            parse_query("select[Name='Bob](R)")
        self.assertEqual(cm.exception.pos.col, 13)  # the opening quote
        self.assertIn("unterminated", cm.exception.message)


class Grammar_7_2(unittest.TestCase):
    def test_10_union_minus_groups_left(self):
        self.assertEqual(tree("A union B minus C"),
                         "Minus\n"
                         "├── Union\n"
                         "│   ├── Relation(A)\n"
                         "│   └── Relation(B)\n"
                         "└── Relation(C)")

    def test_11_minus_is_left_associative(self):
        t = parse_query("A minus B minus C")
        self.assertIsInstance(t, SetOp)
        self.assertIsInstance(t.left, SetOp)          # (A minus B) minus C
        self.assertEqual(t.right.name, "C")

    def test_12_not_and_or(self):
        self.assertEqual(tree("select[not (a=1 and b=2) or c>3](R)"),
                         "Select\n"
                         "├── condition: Or\n"
                         "│   ├── Not\n"
                         "│   │   └── And\n"
                         "│   │       ├── Eq(Attr(a), Num(1))\n"
                         "│   │       └── Eq(Attr(b), Num(2))\n"
                         "│   └── Gt(Attr(c), Num(3))\n"
                         "└── Relation(R)")

    def test_12b_not_binds_tighter_than_and(self):
        # not a=1 and b=2  ==  (not a=1) and b=2
        self.assertIn("condition: And\n│   ├── Not", tree("select[not a=1 and b=2](R)"))

    def test_13_and_binds_tighter_than_or(self):
        self.assertEqual(tree("select[a=1 and b=2 or c=3](R)"),
                         tree("select[(a=1 and b=2) or c=3](R)"))

    def test_14_three_levels_of_nesting(self):
        self.assertEqual(
            tree("project[Name](select[Age>30](select[DID='D1'](Employees)))"),
            "Project(attrs=[Name])\n"
            "└── Select(cond=Gt(Attr(Age), Num(30)))\n"
            "    └── Select(cond=Eq(Attr(DID), Str('D1')))\n"
            "        └── Relation(Employees)")

    def test_15_parentheses_override(self):
        t = parse_query("(A union B) minus (C intersect D)")
        self.assertEqual(t.op, "minus")
        self.assertEqual(t.left.op, "union")
        self.assertEqual(t.right.op, "intersect")

    def test_16_missing_paren(self):
        with self.assertRaises(ParseError) as cm:
            parse_query("select[Age>30](R")
        self.assertIn("missing ')'", cm.exception.message)
        self.assertEqual(cm.exception.pos.col, 17)

    def test_17_empty_projection(self):
        with self.assertRaises(ParseError) as cm:
            parse_query("project[](R)")
        self.assertIn("empty attribute list", cm.exception.message)


class ExtraCases(unittest.TestCase):
    def test_precedence_levels(self):
        # times > intersect > union
        t = parse_query("A union B times C intersect D")
        self.assertEqual(t.op, "union")
        self.assertEqual(t.right.op, "intersect")

    def test_join_is_left_associative(self):
        t = parse_query("A join[A.x=B.x] B join[B.y=C.y] C")
        self.assertEqual(t.right.name, "C")

    def test_missing_operand(self):
        with self.assertRaises(ParseError):
            parse_query("A union")
        with self.assertRaises(ParseError):
            parse_query("union A")

    def test_chained_comparison_rejected(self):
        with self.assertRaises(ParseError):
            parse_query("select[a<b<c](R)")

    def test_keyword_cannot_be_relation_name(self):
        with self.assertRaises(ParseError):
            parse_query("rename[union](R)")

    def test_and_is_hard_reserved(self):
        with self.assertRaises(ParseError):
            parse_query("select[and=1](R)")

    def test_lone_bang(self):
        with self.assertRaises(LexicalError):
            parse_query("select[a!1](R)")

    def test_number_running_into_letters(self):
        with self.assertRaises(LexicalError):
            parse_query("select[a=3b](R)")

    def test_unmatched_close_paren(self):
        with self.assertRaises(ParseError):
            parse_query("A)")

    def test_definitions(self):
        text = ("// employees and their departments\n"
                "Employees (EID, Name, Age, DID) = {\n"
                "  E1, John, 32, D1\n"
                "\n"
                "  // a comment line\n"
                "  E2, 'O''Brien, Pat', -28, D2\n"
                "}\n"
                "Empty(a) = { }\n")
        emp, empty = parse_definitions(text)
        self.assertEqual(emp.attrs, ["EID", "Name", "Age", "DID"])
        self.assertEqual(emp.rows, [["E1", "John", 32, "D1"],
                                    ["E2", "O'Brien, Pat", -28, "D2"]])
        self.assertEqual(empty.rows, [])

    def test_definition_unquoted_paren(self):
        with self.assertRaises(LexicalError):
            parse_definitions("R(a) = {\n x(1)\n}")


if __name__ == "__main__":
    unittest.main()
