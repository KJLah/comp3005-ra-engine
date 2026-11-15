"""Module 3 step 1: the SQL scanner (Section 1)."""
import unittest

from sql.errors import LexicalError
from sql.lexer import (EOF, IDENT, INT, KEYWORD, OP, SEMI, STRING, quote,
                       tokenize)


def kinds(text):
    return [(t.kind, t.value) for t in tokenize(text)[:-1]]


class Keywords(unittest.TestCase):
    def test_keywords_are_case_insensitive(self):
        for spelling in ("SELECT", "select", "SeLeCt"):
            (tok,) = tokenize(spelling)[:-1]
            self.assertEqual((tok.kind, tok.value), (KEYWORD, "SELECT"))
            self.assertEqual(tok.text, spelling)      # source text is kept

    def test_identifiers_are_case_sensitive(self):
        self.assertEqual(kinds("Emp emp EMP_2 _x"),
                         [(IDENT, "Emp"), (IDENT, "emp"), (IDENT, "EMP_2"), (IDENT, "_x")])

    def test_aggregate_names_are_keywords(self):
        self.assertEqual(kinds("count Sum AVG min MAX"),
                         [(KEYWORD, w) for w in ("COUNT", "SUM", "AVG", "MIN", "MAX")])


class Literals(unittest.TestCase):
    def test_doubled_quote_is_one_quote(self):
        (tok,) = tokenize("'it''s'")[:-1]
        self.assertEqual((tok.kind, tok.value), (STRING, "it's"))

    def test_empty_string(self):
        self.assertEqual(kinds("''"), [(STRING, "")])

    def test_quote_round_trip(self):
        for s in ("", "a", "it's", "''", "O'Brien's"):
            (tok,) = tokenize(quote(s))[:-1]
            self.assertEqual(tok.value, s)

    def test_signed_integers(self):
        self.assertEqual(kinds("30 -30 +30 0"),
                         [(INT, 30), (INT, -30), (INT, 30), (INT, 0)])

    def test_sign_needs_a_digit(self):
        with self.assertRaises(LexicalError) as cm:
            tokenize("a - 3")
        self.assertEqual(cm.exception.pos.col, 3)

    def test_decimals_are_rejected(self):
        with self.assertRaises(LexicalError):
            tokenize("3.5")

    def test_unclosed_string(self):
        with self.assertRaises(LexicalError) as cm:
            tokenize("WHERE a = 'abc")
        self.assertEqual((cm.exception.pos.line, cm.exception.pos.col), (1, 11))


class Operators(unittest.TestCase):
    def test_maximal_munch(self):
        self.assertEqual([t.value for t in tokenize("<= >= <> < > =")[:-1]],
                         ["<=", ">=", "<>", "<", ">", "="])
        self.assertEqual([t.value for t in tokenize("a<>b")[:-1]], ["a", "<>", "b"])

    def test_not_equal_is_spelled_angle_brackets(self):
        with self.assertRaises(LexicalError):
            tokenize("a != b")

    def test_comparison_against_negative_number(self):
        self.assertEqual(kinds("a<>-3"), [(IDENT, "a"), (OP, "<>"), (INT, -3)])


class CommentsAndStatements(unittest.TestCase):
    def test_comment_to_end_of_line(self):
        self.assertEqual(kinds("a -- b c\nd"), [(IDENT, "a"), (IDENT, "d")])

    def test_comment_at_end_of_file(self):
        self.assertEqual(kinds("a --"), [(IDENT, "a")])

    def test_several_statements(self):
        toks = tokenize("SELECT a FROM R; SELECT b FROM S;")
        self.assertEqual(sum(t.kind == SEMI for t in toks), 2)
        self.assertEqual(toks[-1].kind, EOF)

    def test_positions_count_lines_and_columns(self):
        toks = tokenize("SELECT a\n  FROM R")
        self.assertEqual([(t.pos.line, t.pos.col) for t in toks[:-1]],
                         [(1, 1), (1, 8), (2, 3), (2, 8)])

    def test_error_format(self):
        with self.assertRaises(LexicalError) as cm:
            tokenize("SELECT a\nFROM R WHERE a = #")
        self.assertEqual(cm.exception.render(),
                         "error: lexical: unexpected character '#' at line 2, column 18")


class NoBannedTools(unittest.TestCase):
    def test_no_regex_or_eval_in_sql_package(self):
        import os
        root = os.path.join(os.path.dirname(__file__), "..", "sql")
        for name in os.listdir(root):
            if name.endswith(".py"):
                with open(os.path.join(root, name), encoding="utf-8") as f:
                    src = f.read()
                self.assertNotIn("import re\n", src, name)
                for call in ("eval(", "exec("):
                    at = src.find(call)
                    while at != -1:     # a bare call, not part of a longer name
                        before = src[at - 1] if at else " "
                        self.assertTrue(before.isalnum() or before in "_.",
                                        f"{name} calls {call}")
                        at = src.find(call, at + 1)


if __name__ == "__main__":
    unittest.main()