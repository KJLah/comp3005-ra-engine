import io
import unittest

from m2_helpers import make_db
from sql.cli import compile_script, main
from sql.errors import SchemaError
from sql.resolve import DictCatalog


class ReviewRegressions(unittest.TestCase):
    def setUp(self):
        self.tmp = make_db({
            "Emp": ("salary:INT", ["10", "20"]),
        })

    def tearDown(self):
        self.tmp.cleanup()

    def check_error(self, query, category):
        out = io.StringIO()
        err = io.StringIO()

        code = main(
            [
                "check",
                "--db", self.tmp.name,
                "--query", query,
            ],
            out,
            err,
        )

        self.assertEqual(code, 1)
        self.assertTrue(
            out.getvalue().startswith(f"error: {category}: ")
        )
        self.assertIn(" at line 1, column ", out.getvalue())
        self.assertNotIn(
            "Traceback", out.getvalue() + err.getvalue()
        )

    def test_only_count_accepts_star(self):
        for fn in ("SUM", "AVG", "MIN", "MAX"):
            with self.subTest(fn=fn):
                self.check_error(
                    f"SELECT {fn}(*) FROM Emp",
                    "aggregate",
                )

    def test_int_real_set_operands_are_rejected(self):
        for op in ("UNION", "UNION ALL", "INTERSECT", "EXCEPT"):
            with self.subTest(op=op):
                self.check_error(
                    f"SELECT salary FROM Emp {op} "
                    "SELECT AVG(salary) FROM Emp",
                    "schema",
                )

    def test_huge_integer_returns_sql_error(self):
        self.check_error(
            "SELECT salary FROM Emp WHERE salary > "
            + "9" * 5000,
            "lexical",
        )

    def test_record_capacity_checked_before_storage(self):
        fields = ",".join(
            f"v{i} VARCHAR(255)" for i in range(16)
        )
        values = ",".join(
            "'" + "x" * 255 + "'" for _ in range(16)
        )
        text = (
            f"CREATE TABLE W ({fields}); "
            f"INSERT INTO W VALUES ({values})"
        )

        with self.assertRaises(SchemaError):
            compile_script(text, DictCatalog({}))

        out = io.StringIO()
        err = io.StringIO()

        code = main(
            ["exec", "--db", self.tmp.name, "--query", text],
            out,
            err,
        )

        self.assertEqual(code, 1)
        self.assertTrue(
            err.getvalue().startswith("error: schema: ")
        )
        self.assertIn(" at line 1, column ", err.getvalue())