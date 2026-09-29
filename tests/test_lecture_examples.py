"""The worked examples from Lecture 02 (Relational Algebra), run through our
engine. The data is in tests/data/lecture.ra. Each test names the slide it
comes from and checks the answer shown in class.

    python -m unittest discover -s tests -v
"""
import os
import sys
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, ".."))

from ra_engine.errors import RANameError, SchemaError  # noqa: E402
from ra_engine.loader import load_file                 # noqa: E402
from ra_engine.operators import evaluate, plan         # noqa: E402
from ra_engine.parser import parse_query               # noqa: E402

CAT = load_file(os.path.join(HERE, "data", "lecture.ra"))


def rows(q):
    return sorted(evaluate(plan(parse_query(q), CAT)).rows, key=repr)


class Selection(unittest.TestCase):
    def test_salary_at_least_1000(self):
        # σ salary>=1000 (Employee)
        self.assertEqual([r[0] for r in rows("select[salary>=1000](Employee)")], [1, 3])

    def test_selection_is_commutative_and_equals_and(self):
        # σ name=Alex(σ salary>=1000(E)) = σ salary>=1000(σ name=Alex(E)) = σ name=Alex ∧ salary>=1000(E)
        a = rows("select[name='Alex'](select[salary>=1000](Employee))")
        b = rows("select[salary>=1000](select[name='Alex'](Employee))")
        c = rows("select[name='Alex' and salary>=1000](Employee)")
        self.assertEqual(a, b)
        self.assertEqual(b, c)
        self.assertEqual(len(a), 1)


class Projection(unittest.TestCase):
    def test_name_and_email(self):
        # π name,email (Employee)
        self.assertEqual(rows("project[name, email](Employee)"),
                         [("Alex", "a@c"), ("John", "j@c"), ("Mo", "m@c")])

    def test_nested_projection(self):
        # π name(π name,email(E)) = π name(E)
        self.assertEqual(rows("project[name](project[name, email](Employee))"),
                         rows("project[name](Employee)"))

    def test_projection_is_not_commutative(self):
        # π name,email(π name(E)) is invalid: email is gone after π name
        with self.assertRaises(RANameError):
            rows("project[name, email](project[name](Employee))")


class SelectionAndProjection(unittest.TestCase):
    def test_id_at_least_2(self):
        # π name,email (σ id>=2 (Employee))
        self.assertEqual(rows("project[name, email](select[id>=2](Employee))"),
                         [("John", "j@c"), ("Mo", "m@c")])

    def test_order_cannot_be_swapped(self):
        # "Can we change the order?"  σ id>=2 (π name,email (E)): no, id was projected away
        with self.assertRaises(RANameError):
            rows("select[id>=2](project[name, email](Employee))")


class ProductAndJoin(unittest.TestCase):
    def test_product_size(self):
        self.assertEqual(len(rows("EmpDept times Department")), 9)

    def test_join_equals_select_over_product(self):
        # σ Employee.Dept=Department.name (Employee ⨉ Department) = Employee ⨝ Department
        a = rows("select[EmpDept.Dept=Department.name](EmpDept times Department)")
        b = rows("EmpDept join[EmpDept.Dept=Department.name] Department")
        self.assertEqual(a, b)
        self.assertEqual(len(a), 3)

    def test_bare_name_is_ambiguous_after_product(self):
        # both inputs have an attribute called name
        with self.assertRaises(RANameError):
            rows("EmpDept join[Dept=name] Department")


class SetOperators(unittest.TestCase):
    def test_intersect(self):
        self.assertEqual(rows("EmpSet intersect GradStudent"), [("Alex", "a@c", "Sales")])

    def test_union_removes_duplicates(self):
        self.assertEqual(len(rows("EmpSet union GradStudent")), 5)   # Alex once

    def test_minus_is_not_commutative(self):
        self.assertEqual(rows("EmpSet minus GradStudent"),
                         [("John", "j@c", "Finance"), ("Mo", "m@c", "HR")])
        self.assertEqual(rows("GradStudent minus EmpSet"),
                         [("Go", "g@c", "HR"), ("Max", "x@c", "Finance")])

    def test_compatibility_problem_and_fix(self):
        # Employee has an extra Salary column: not compatible ...
        with self.assertRaises(SchemaError):
            rows("EmpSalary intersect GradStudent")
        # ... until it is projected away, as on the slide
        self.assertEqual(rows("project[name, email, Dept](EmpSalary) intersect GradStudent"),
                         [("Alex", "a@c", "Sales")])

    def test_different_attribute_name_is_rejected(self):
        # The slide pairs Dept with Depar. The assignment's rule (4.3) requires the
        # same names in the same order, so this is a schema error here.
        with self.assertRaises(SchemaError):
            rows("EmpSet intersect GradStudentDepar")


class LectureExamples(unittest.TestCase):
    def test_students_in_ottawa(self):
        # π name,email (σ city='Ottawa' (Student))
        self.assertEqual(rows("project[name, email](select[city='Ottawa'](Student))"),
                         [("Alex", "al@c.ca"), ("John", "jo@c.ca")])

    def test_makela_email(self):
        # π email (σ name='Makela' (Student))
        self.assertEqual(rows("project[email](select[name='Makela'](Student))"), [("ma@c.ca",)])

    def test_names_titles_marks(self):
        # π name,title,mark ((Student ⨝ Student.id=Takes.sid Takes) ⨝ cid=Course.id Course)
        self.assertEqual(
            rows("project[name, title, mark]((Student join[Student.id=Takes.sid] Takes) "
                 "join[cid=Course.id] Course)"),
            [("Alex", "DBMS", 8), ("Alex", "Math", 9), ("Alex", "Physics", 10),
             ("John", "Math", 8)])

    def test_names_that_take_courses_has_no_duplicates(self):
        # "{Alex, Alex, Alex, John} will be {Alex, John} only"
        self.assertEqual(rows("project[name](Student join[Student.id=Takes.sid] Takes)"),
                         [("Alex",), ("John",)])

    def test_names_that_take_no_courses(self):
        # π name(Student) − π name(Student ⨝ Takes)
        self.assertEqual(rows("project[name](Student) minus "
                              "project[name](Student join[Student.id=Takes.sid] Takes)"),
                         [("Makela",)])

    def test_relax_example(self):
        # (Student) ⨝ id=sid (takes)
        self.assertEqual(len(rows("Student join[id=sid] Takes")), 4)


class Division(unittest.TestCase):
    """Division is not one of our operators, but it can be written with the
    six core ones:  R ÷ S = π_x(R) − π_x((π_x(R) × S) − R)."""

    ALL_COURSES = ("project[sid](Stud_Course) minus "
                   "project[sid]((project[sid](Stud_Course) times project[cname](Course2)) "
                   "minus project[sid, cname](Stud_Course))")

    def test_students_who_took_all_courses(self):
        self.assertEqual(rows(self.ALL_COURSES), [(1,), (2,)])

    def test_joined_back_to_student(self):
        q = f"project[name](Student join[id=sid] ({self.ALL_COURSES}))"
        self.assertEqual(rows(q), [("Alex",), ("John",)])


if __name__ == "__main__":
    unittest.main()
