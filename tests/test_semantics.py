"""Required test cases 18-25 (semantics), the ambiguity data from GRAMMAR.md,
the five error categories, and extra operator tests.

    python -m unittest discover -s tests -v
"""
import os
import subprocess
import sys
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.join(HERE, "..")
sys.path.insert(0, ROOT)

from ra_engine.errors import (LexicalError, ParseError, RANameError,  # noqa: E402
                              RATypeError, SchemaError)
from ra_engine.loader import load_file, load_text                      # noqa: E402
from ra_engine.operators import evaluate, plan                        # noqa: E402
from ra_engine.parser import parse_query                              # noqa: E402
from ra_engine.relation import format_relation                        # noqa: E402


def catalog():
    cat = {}
    load_file(os.path.join(ROOT, "data", "employees.ra"), cat)
    load_file(os.path.join(HERE, "data", "cases.ra"), cat)
    load_file(os.path.join(HERE, "data", "ambiguity.ra"), cat)
    return cat


CAT = catalog()


def run(q, cat=None):
    return evaluate(plan(parse_query(q), cat or CAT))


def rows(q):
    return sorted(run(q).rows, key=repr)


def cli(*args):
    """Run ra.py as a user would and capture what they see."""
    p = subprocess.run([sys.executable, os.path.join(ROOT, "ra.py")] + list(args),
                       capture_output=True, text=True, cwd=ROOT)
    return p.returncode, p.stdout + p.stderr


class Semantics_7_3(unittest.TestCase):
    def test_18_attribute_vs_attribute(self):
        self.assertEqual(rows("select[A=B](R18)"), [(1, 1), (5, 5)])

    def test_19_qualified_names_and_distinguishable_schema(self):
        r = run("Emp join[Emp.DID=Dept.DID] Dept")
        self.assertEqual(len(r.rows), 4)
        names = r.display_names()
        self.assertIn("Emp.DID", names)
        self.assertIn("Dept.DID", names)
        self.assertEqual(len(r.attributes), 7)       # 5 + 2, nothing merged

    def test_20_self_join_with_rename(self):
        r = run("project[Emp.Name, E2.Name](rename[E2](Emp) join[Emp.MgrID=E2.EID] Emp)")
        self.assertEqual(sorted(r.rows), sorted([
            ("John", "John"), ("Alice", "John"), ("O'Brien, Pat", "John"),
            ("Bob", "Alice")]))

    def test_20b_self_join_without_rename_is_an_error(self):
        with self.assertRaises(SchemaError):
            run("Emp join[Emp.MgrID=Emp.EID] Emp")

    def test_21_union_different_schemas(self):
        with self.assertRaises(SchemaError) as cm:
            run("R21 union S21")
        self.assertIn("attribute 1 is 'a' on the left and 'b' on the right",
                      cm.exception.message)

    def test_22_type_error(self):
        with self.assertRaises(RATypeError):
            run("select[Age>'30'](R22)")

    def test_23_project_removes_duplicates(self):
        r = run("project[DID](Employees)")
        self.assertEqual(sorted(r.rows), [("D1",), ("D2",)])

    def test_24_project_same_attribute_twice(self):
        with self.assertRaises(SchemaError) as cm:
            run("project[Name, Name](Employees)")
        self.assertIn("listed twice", cm.exception.message)

    def test_25_empty_result_prints_schema(self):
        out = format_relation(run("select[Age>100](Employees)"))
        self.assertEqual(out, "EID | Name | Age | DID\n"
                              "----+------+-----+----\n"
                              "(0 tuples)")


class AmbiguityInstances(unittest.TestCase):
    """The data instances promised in GRAMMAR.md 5.3."""

    def test_case_10_grouping_changes_the_answer(self):
        self.assertEqual(rows("A union B minus C"), [(2,)])         # our grammar
        self.assertEqual(rows("A union (B minus C)"), [(1,), (2,)])  # the other tree

    def test_case_11_associativity_changes_the_answer(self):
        self.assertEqual(rows("A2 minus B2 minus C2"), [(2,), (3,)])
        self.assertEqual(rows("A2 minus (B2 minus C2)"), [(1,), (2,), (3,)])


class ErrorCategories(unittest.TestCase):
    """Section 6.3: five categories, each with a message, never a stack trace."""

    def check_cli(self, query, category):
        code, out = cli("-d", "data/employees.ra", query)
        self.assertEqual(code, 1)
        self.assertTrue(out.startswith(category), out)
        self.assertNotIn("Traceback", out)

    def test_lexical(self):
        self.check_cli("select[Name='Bob](Employees)", "Lexical error at line 1, column 13")

    def test_syntax(self):
        self.check_cli("select[Age>30](Employees", "Syntax error at line 1, column 25")

    def test_syntax_missing_operand(self):
        self.check_cli("Employees union", "Syntax error")

    def test_name_relation(self):
        self.check_cli("Nope", "Name error at line 1, column 1")

    def test_name_attribute(self):
        self.check_cli("project[Salary](Employees)", "Name error at line 1, column 9")

    def test_name_ambiguous(self):
        self.check_cli("Emp join[DID=DID] Dept", "Name error")

    def test_schema(self):
        self.check_cli("Employees union Dept", "Schema error")

    def test_type(self):
        self.check_cli("select[Age>'30'](Employees)", "Type error")

    def test_type_attribute_vs_attribute(self):
        self.check_cli("select[Name<Age](Employees)", "Type error")

    def test_data_file_errors_name_the_file(self):
        with self.assertRaises(SchemaError):
            load_text("R(a, b) = {\n 1, 2, 3\n}")
        with self.assertRaises(SchemaError):
            load_text("R(a, a) = {\n}")
        with self.assertRaises(RATypeError):
            load_text("R(a) = {\n 1\n x\n}")
        with self.assertRaises(RANameError):
            load_text("R(a) = {\n}\nR(b) = {\n}")

    def test_no_traceback_when_output_is_cut_off(self):
        # like `python3 ra.py ... | head -1`: the reader closes the pipe early
        p = subprocess.Popen([sys.executable, os.path.join(ROOT, "ra.py"),
                              "-d", "data/employees.ra", "Emp times Emp times Dept"],
                             cwd=ROOT, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        p.stdout.readline()
        p.stdout.close()
        err = p.stderr.read().decode()
        p.stderr.close()
        p.wait()
        self.assertNotIn("Traceback", err)

    def test_missing_data_file(self):
        code, out = cli("-d", "no/such/file.ra", "R")
        self.assertEqual(code, 1)
        self.assertNotIn("Traceback", out)


class ExtraSemantics(unittest.TestCase):
    def test_duplicates_in_input_collapse(self):
        # 1,x / 1,x / 1,'x' / 1.0,x are all the same tuple under our equality
        self.assertEqual(rows("Dups"), [(1, "x"), (2, "y")])

    def test_select_counter_counts_every_tuple(self):
        op = plan(parse_query("select[Age>30](Employees)"), CAT)
        evaluate(op)
        self.assertEqual(op.evaluations, 3)

    def test_join_counter_is_n_times_m(self):
        op = plan(parse_query("Emp join[Emp.DID=Dept.DID] Dept"), CAT)
        evaluate(op)
        self.assertEqual(op.comparisons, 4 * 2)

    def test_join_equals_times_then_select(self):
        a = rows("Emp join[Emp.DID=Dept.DID and Age<40] Dept")
        b = rows("select[Emp.DID=Dept.DID and Age<40](Emp times Dept)")
        self.assertEqual(a, b)

    def test_times_size(self):
        self.assertEqual(len(run("Employees times Dept").rows), 3 * 2)

    def test_intersect_and_minus(self):
        self.assertEqual(rows("A2 intersect B2"), [(1,)])
        self.assertEqual(rows("A2 minus B2"), [(2,), (3,)])

    def test_union_removes_duplicates(self):
        self.assertEqual(rows("A2 union B2"), [(1,), (2,), (3,)])

    def test_set_op_schema_is_left(self):
        r = run("A union rename[Z](B)")
        self.assertEqual(r.attributes[0].qualifier, "A")

    def test_rename_then_qualified_select(self):
        self.assertEqual(len(run("select[E.Age>30](rename[E](Employees))").rows), 1)

    def test_old_name_gone_after_rename(self):
        with self.assertRaises(RANameError):
            run("select[Employees.Age>30](rename[E](Employees))")

    def test_empty_relation_has_unknown_types(self):
        # comparing an empty (untyped) column to a string is not a type error
        self.assertEqual(run("select[Age='x'](Nobody)").rows, [])
        self.assertEqual(run("Nobody union R22").attributes[1].type, "number")

    def test_not_or_semantics(self):
        self.assertEqual(rows("project[Name](select[not (Age<30) or DID='D2'](Employees))"),
                         [("Alice",), ("John",)])

    def test_string_comparison(self):
        self.assertEqual(rows("project[Name](select[Name<'C'](Employees))"),
                         [("Alice",), ("Bob",)])

    def test_negative_and_decimal_numbers(self):
        cat = load_text("T(v) = {\n -3\n 2.5\n 7\n}")
        self.assertEqual(rows_of("select[v>-30 and v<3](T)", cat), [(-3,), (2.5,)])

    def test_cli_empty_result(self):
        code, out = cli("-d", "data/employees.ra", "select[Age>100](Employees)")
        self.assertEqual(code, 0)
        self.assertIn("(0 tuples)", out)


def rows_of(q, cat):
    return sorted(run(q, cat).rows, key=repr)


if __name__ == "__main__":
    unittest.main()
