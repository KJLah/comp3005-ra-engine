"""Module 3 step 7: command line, CREATE TABLE / INSERT, one error format."""
import io
import json
import os
import tempfile
import unittest

from sql.cli import main


def run(*argv):
    out, err = io.StringIO(), io.StringIO()
    code = main(list(argv), out, err)
    return code, out.getvalue(), err.getvalue()


SETUP = """
CREATE TABLE Emp (eid INT, name VARCHAR(20), age INT, salary INT, did INT,
                  mgr INT, city VARCHAR(20));
CREATE TABLE Dept (did INT, title VARCHAR(20), budget INT, city VARCHAR(20));
INSERT INTO Emp VALUES (1, 'Ann', 40, 100, 1, 0, 'Ottawa');
INSERT INTO Emp VALUES (2, 'O''Hare', 25, 80, 1, 1, 'Ottawa');
INSERT INTO Dept VALUES (1, 'Eng', 500, 'Ottawa');
"""


class WithDatabase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.db = os.path.join(self.tmp.name, "db")
        code, out, err = run("exec", "--db", self.db, "--query", SETUP)
        self.assertEqual(code, 0, err)

    def tearDown(self):
        self.tmp.cleanup()

    def write(self, name, text):
        path = os.path.join(self.tmp.name, name)
        with open(path, "w", encoding="utf-8") as f:
            f.write(text)
        return path

    def test_exec_stores_rows_through_module_1(self):
        from storage.cli import main as storage_main
        import contextlib
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            storage_main(["scan", "--db", self.db, "--table", "Emp", "--json"])
        rows = json.loads(buf.getvalue())["rows"]
        self.assertEqual([r[1] for r in rows], ["Ann", "O'Hare"])

    def test_compile_json_single_query(self):
        q = self.write("q.sql", "SELECT name, age FROM Emp WHERE age > 30;")
        code, out, err = run("compile", "--db", self.db, "--file", q, "--json")
        self.assertEqual(code, 0, err)
        self.assertEqual(json.loads(out),
                         {"op": "project", "columns": ["Emp.name", "Emp.age"],
                          "child": {"op": "select", "condition": "Emp.age > 30",
                                    "child": {"op": "scan", "table": "Emp"}}})

    def test_compile_json_several_queries_is_a_list(self):
        q = self.write("q.sql", "SELECT city FROM Emp UNION SELECT city FROM Dept;\n"
                                "SELECT city FROM Emp UNION ALL SELECT city FROM Dept;")
        code, out, _ = run("compile", "--db", self.db, "--file", q, "--json")
        plans = json.loads(out)
        self.assertEqual([p["op"] for p in plans], ["union", "union_all"])

    def test_compile_explain_is_the_default(self):
        code, out, _ = run("compile", "--db", self.db, "--query",
                           "SELECT city FROM Emp UNION SELECT city FROM Dept")
        self.assertEqual(out.splitlines()[0], "union")

    def test_compile_sees_tables_created_earlier_but_does_not_store_them(self):
        q = self.write("q.sql", "CREATE TABLE T (x INT); INSERT INTO T VALUES (3);"
                                "SELECT x FROM T")
        code, out, err = run("compile", "--db", self.db, "--file", q, "--json")
        self.assertEqual(code, 0, err)
        self.assertEqual(json.loads(out)["columns"], ["T.x"])
        code, out, _ = run("check", "--db", self.db, "--query", "SELECT x FROM T")
        self.assertEqual(code, 1)

    def test_check_ok_prints_nothing(self):
        q = self.write("q.sql", "SELECT name FROM Emp; SELECT title FROM Dept;")
        self.assertEqual(run("check", "--db", self.db, "--file", q), (0, "", ""))

    def test_check_reports_first_error_only(self):
        q = self.write("q.sql", "SELECT name FROM Emp;\n"
                                "SELECT did FROM Emp, Dept;\n"
                                "SELECT nope FROM Emp;")
        code, out, err = run("check", "--db", self.db, "--file", q)
        self.assertEqual(code, 1)
        self.assertEqual(out.strip().splitlines(),
                         ["error: ambiguity: column 'did' is ambiguous: it could be "
                          "Emp.did, Dept.did; qualify it at line 2, column 8"])

    def test_every_category_uses_one_format(self):
        cases = {
            "lexical": "SELECT name FROM Emp WHERE name = \"x\"",
            "syntax": "SELECT name Emp",
            "name": "SELECT name FROM Nope",
            "ambiguity": "SELECT city FROM Emp, Dept",
            "type": "SELECT name FROM Emp WHERE age > '30'",
            "aggregate": "SELECT name, COUNT(*) FROM Emp",
            "schema": "SELECT name FROM Emp UNION SELECT budget FROM Dept",
        }
        for cat, q in cases.items():
            code, out, err = run("check", "--db", self.db, "--query", q)
            self.assertEqual(code, 1, cat)
            line = out.strip()
            self.assertTrue(line.startswith(f"error: {cat}: "), line)
            self.assertIn(" at line 1, column ", line)
            self.assertNotIn("Traceback", out + err)

    def test_compile_error_goes_to_stderr(self):
        code, out, err = run("compile", "--db", self.db, "--query", "SELECT x FROM Emp")
        self.assertEqual((code, out), (1, ""))
        self.assertTrue(err.startswith("error: name: "))

    def test_insert_checks(self):
        bad = {
            "INSERT INTO Dept VALUES (1, 'Eng', 5)": "schema",
            "INSERT INTO Dept VALUES ('1', 'Eng', 5, 'x')": "type",
            "INSERT INTO Dept VALUES (1, 'Eng', 5, 'abcdefghijklmnopqrstuvwxyz')": "type",
            "INSERT INTO Dept VALUES (99999999999, 'Eng', 5, 'x')": "type",
            "INSERT INTO Nope VALUES (1)": "name",
            "CREATE TABLE Emp (a INT)": "schema",
            "CREATE TABLE T (a INT, a INT)": "schema",
            "CREATE TABLE T (a VARCHAR(0))": "schema",
            "CREATE TABLE T (a VARCHAR(256))": "schema",
        }
        for q, cat in bad.items():
            code, out, _ = run("check", "--db", self.db, "--query", q)
            self.assertEqual(code, 1, q)
            self.assertTrue(out.startswith(f"error: {cat}: "), (q, out))

    def test_exec_error_keeps_earlier_statements(self):
        code, _, err = run("exec", "--db", self.db, "--query",
                           "INSERT INTO Dept VALUES (2, 'Ops', 50, 'Hull'); "
                           "INSERT INTO Dept VALUES (3)")
        self.assertEqual(code, 1)
        self.assertIn("error: schema:", err)
        from sql.resolve import StorageCatalog
        from storage.buffer import BufferPool
        from storage.catalog import Catalog
        info = Catalog(self.db, BufferPool(4)).get("Dept")
        self.assertEqual(info.num_tuples, 2)

    def test_missing_database(self):
        code, out, _ = run("check", "--db", os.path.join(self.tmp.name, "nope"),
                           "--query", "SELECT a FROM R")
        self.assertEqual(code, 1)
        self.assertTrue(out.startswith("error: io: "))


class Parse(unittest.TestCase):
    def test_parse_explain_needs_no_catalog(self):
        code, out, _ = run("parse", "--query", "SELECT a FROM R UNION ALL SELECT a FROM S",
                           "--explain")
        self.assertEqual(code, 0)
        self.assertEqual(out.splitlines()[0], "UNION ALL")

    def test_parse_syntax_error(self):
        code, _, err = run("parse", "--query", "SELECT FROM R")
        self.assertEqual(code, 1)
        self.assertEqual(err.strip(),
                         "error: syntax: expected a column, an aggregate or '*', found "
                         "keyword FROM at line 1, column 8")



class QueryFiles(unittest.TestCase):
    """queries/: every required case as a file; errors exactly where expected."""
    ERRORS = {"case03": "ambiguity", "case06": "aggregate", "case07": "aggregate",
              "case10": "type", "case11a": "name", "case11b": "name", "case12": "name",
              "case14": "schema"}

    def test_all_case_files(self):
        here = os.path.join(os.path.dirname(__file__), "..", "queries")
        with tempfile.TemporaryDirectory() as tmp:
            db = os.path.join(tmp, "db")
            code, _, err = run("exec", "--db", db, "--file", os.path.join(here, "setup.sql"))
            self.assertEqual(code, 0, err)
            files = sorted(f for f in os.listdir(here) if f.startswith("case"))
            self.assertEqual(len(files), 16)
            for f in files:
                code, out, _ = run("check", "--db", db, "--file", os.path.join(here, f))
                expected = self.ERRORS.get(f.split("_")[0])
                if expected is None:
                    self.assertEqual((code, out), (0, ""), f)
                else:
                    self.assertEqual(code, 1, f)
                    self.assertTrue(out.startswith(f"error: {expected}: "), (f, out))


if __name__ == "__main__":
    unittest.main()