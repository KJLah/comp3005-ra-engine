"""Integration (Section 6): the shell, its commands, and the wiring."""
import io
import os
import tempfile
import unittest

from shell.cli import main as radb
from shell.session import Session
from shell.split import ShellError, classify, split_statements, terminated

SCHEMA = """
CREATE TABLE Dept (did INT, dname VARCHAR(10));
CREATE TABLE Emp (eid INT, name VARCHAR(10), did INT, age INT);
"""
DEPT = "did,dname\n1,Sales\n2,Labs\n3,Ops\n"
EMP = "1,Ann,1,30\n2,Bob,1,45\n3,Cy,2,52\n4,Di,3,28\n5,Ed,2,61\n"


class Splitting(unittest.TestCase):
    def test_semicolons_in_strings_and_comments_do_not_split(self):
        text = "SELECT a FROM T WHERE b = 'x;y'; -- one; two\nSELECT 1x FROM T"
        parts = split_statements(text)
        self.assertEqual(len(parts), 2)
        self.assertIn("'x;y'", parts[0])

    def test_pieces_keep_line_and_column(self):
        text = "SET frames = 4;\n\n  SELECT a FROM T;"
        second = split_statements(text)[1]
        self.assertEqual(len(second), len(text) - 1)          # padded to its place
        self.assertEqual(second.split("\n")[2].index("SELECT"), 2)

    def test_empty_and_comment_only_pieces_are_dropped(self):
        self.assertEqual(split_statements(";; -- nothing\n;"), [])

    def test_terminated(self):
        self.assertTrue(terminated("SELECT a FROM T;  -- done"))
        self.assertFalse(terminated("SELECT a FROM T WHERE b = ';"))
        self.assertFalse(terminated("SELECT a\nFROM T"))


class Classify(unittest.TestCase):
    def test_kinds(self):
        self.assertEqual(classify("select a from T").kind, "sql")
        self.assertEqual(classify("explain SELECT a FROM T").kind, "explain")
        self.assertEqual(classify("EXPLAIN analyze SELECT a FROM T").kind, "explain_analyze")
        self.assertEqual(classify("ANALYZE Emp, Dept").tables, ("Emp", "Dept"))
        self.assertEqual(classify("analyze").tables, ())
        c = classify("set FRAMES to 8")
        self.assertEqual((c.kind, c.name, c.value), ("set", "frames", "8"))

    def test_explain_prefix_is_blanked_not_removed(self):
        c = classify("EXPLAIN SELECT a FROM T")
        self.assertEqual(c.sql, "        SELECT a FROM T")

    def test_load(self):
        c = classify("LOAD Emp FROM 'my data/emp.csv' HEADER")
        self.assertEqual((c.name, c.value, c.header), ("Emp", "my data/emp.csv", True))
        c = classify("load Emp from data/emp.csv")
        self.assertEqual((c.value, c.header), ("data/emp.csv", False))
        with self.assertRaises(ShellError):
            classify("LOAD Emp 'x.csv'")
        with self.assertRaises(ShellError):
            classify("LOAD Emp FROM 'x.csv' NOW")

    def test_bad_set(self):
        with self.assertRaises(ShellError):
            classify("SET frames")


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.db = os.path.join(self.tmp.name, "db")
        self.dept = os.path.join(self.tmp.name, "dept.csv")
        self.emp = os.path.join(self.tmp.name, "emp.csv")
        with open(self.dept, "w") as f:
            f.write(DEPT)
        with open(self.emp, "w") as f:
            f.write(EMP)
        self.out = io.StringIO()
        self.s = Session(self.db, 8, self.out)
        self.s.run_script(SCHEMA + f"LOAD Dept FROM '{self.dept}' HEADER;"
                          f"LOAD Emp FROM '{self.emp}';")

    def tearDown(self):
        self.tmp.cleanup()

    def said(self):
        text = self.out.getvalue()
        self.out.seek(0)
        self.out.truncate()
        return text


class Session_(Base):
    def test_query_rows_and_summary(self):
        self.said()
        r = self.s.execute("SELECT e.name, d.dname FROM Emp e JOIN Dept d ON e.did = d.did "
                           "WHERE e.age > 40 ORDER BY e.name;")
        self.assertEqual(r.rows, [("Bob", "Sales"), ("Cy", "Labs"), ("Ed", "Labs")])
        self.assertEqual(r.actual_rows, 3)
        self.assertGreater(r.page_reads, 0)
        text = self.said()
        self.assertIn("Bob | Sales", text)
        self.assertIn("(3 rows; est_rows=", text)

    def test_set_frames_reaches_the_plan(self):
        self.s.execute("SET frames = 4")
        self.assertEqual(self.s.frames, 4)
        r = self.s.execute("SELECT name FROM Emp ORDER BY age DESC")
        self.assertEqual([x[0] for x in r.rows], ["Ed", "Cy", "Bob", "Ann", "Di"])
        for bad in ("SET frames = 2", "SET frames = 3", "SET frames = x", "SET color = 3"):
            with self.assertRaises(ShellError):
                self.s.execute(bad)

    def test_insert_and_create(self):
        self.s.execute("INSERT INTO Dept VALUES (4, 'HR')")
        r = self.s.execute("SELECT COUNT(*) AS n FROM Dept")
        self.assertEqual(r.rows, [(4,)])

    def test_analyze_changes_the_estimate(self):
        from optimizer.stats import load_all
        self.assertFalse(load_all(self.db)["Emp"].analyzed)
        self.s.execute("ANALYZE Emp")
        st = load_all(self.db)["Emp"]
        self.assertTrue(st.analyzed)
        self.assertEqual((st.cols["age"].min, st.cols["age"].max), (28, 61))
        r = self.s.execute("SELECT eid FROM Emp WHERE age > 60")
        self.assertEqual((r.est_rows, r.actual_rows), (1, 1))
        self.said()
        self.s.execute("ANALYZE")
        self.assertIn("analyzed Dept", self.said())
        with self.assertRaises(ShellError):
            self.s.execute("ANALYZE Nope")

    def test_load_warns_when_statistics_are_stale(self):
        self.s.execute("ANALYZE Emp")
        self.said()
        self.s.execute(f"LOAD Emp FROM '{self.emp}'")
        text = self.said()
        self.assertIn("loaded 5 rows into Emp (10 rows", text)
        self.assertIn("run ANALYZE Emp", text)

    def test_bad_csv_leaves_the_table_unchanged(self):
        bad = os.path.join(self.tmp.name, "bad.csv")
        with open(bad, "w") as f:
            f.write("6,Fay,1,40\n7,Gus,one,50\n")
        with self.assertRaises(Exception):
            self.s.execute(f"LOAD Emp FROM '{bad}'")
        self.assertEqual(self.s.execute("SELECT COUNT(*) AS n FROM Emp").rows, [(5,)])

    def test_explain_and_explain_analyze(self):
        self.said()
        self.s.execute("EXPLAIN SELECT e.name FROM Emp e JOIN Dept d ON e.did = d.did")
        text = self.said()
        self.assertIn("seqscan Emp", text)
        self.assertIn("total est_cost=", text)
        r = self.s.execute("EXPLAIN ANALYZE SELECT e.name FROM Emp e JOIN Dept d "
                           "ON e.did = d.did WHERE d.dname = 'Labs'")
        text = self.said()
        self.assertEqual(r.actual_rows, 2)
        self.assertIn("root: est_rows=", text)
        self.assertIn("rows=2", text)
        self.assertIn("reads=", text)
        self.assertIn("time: compile", text)
        self.assertNotIn("Cy", text)                 # EXPLAIN ANALYZE prints no rows

    def test_module3_error_keeps_its_position_in_the_script(self):
        with self.assertRaises(Exception) as cm:
            self.s.run_script("SET frames = 4;\nSELECT nope\nFROM Emp;")
        self.assertIn("at line 2, column 8", cm.exception.render())
        with self.assertRaises(Exception) as cm:
            self.s.execute("EXPLAIN SELECT nope FROM Emp")
        self.assertIn("column 16", cm.exception.render())

    def test_describe(self):
        self.said()
        self.s.execute("\\d")
        self.assertIn("Emp  5 rows", self.said())
        self.s.execute("\\d Emp")
        self.assertIn("name VARCHAR(10)", self.said())

    def test_no_database_yet(self):
        s = Session(os.path.join(self.tmp.name, "none"), 8, io.StringIO())
        with self.assertRaises(ShellError):
            s.execute("SELECT a FROM T")


class Program(Base):
    def run_radb(self, args, stdin=""):
        out, err = io.StringIO(), io.StringIO()
        code = radb(["--db", self.db] + args, inp=io.StringIO(stdin), out=out, err=err)
        return code, out.getvalue(), err.getvalue()

    def test_prompt_reads_multi_line_statements_and_carries_on_after_errors(self):
        code, out, err = self.run_radb([], "SELECT name\n FROM Emp\n WHERE eid = 3;\n"
                                           "SELECT x FROM Emp;\nSET frames = 5;\n\\q\n")
        self.assertEqual(code, 0)
        self.assertIn("Cy", out)
        self.assertIn("frames = 5", out)
        self.assertIn("error: name:", err)

    def test_script_stops_at_the_first_error(self):
        code, out, err = self.run_radb(["-c", "SELECT x FROM Emp; SET frames = 5"])
        self.assertEqual(code, 1)
        self.assertNotIn("frames = 5", out)
        path = os.path.join(self.tmp.name, "s.sql")
        with open(path, "w") as f:
            f.write("SET frames = 4;\nSELECT COUNT(*) AS n FROM Dept;\n")
        code, out, _ = self.run_radb(["--file", path])
        self.assertEqual(code, 0)
        self.assertIn("\n3\n", out)

    def test_frames_flag(self):
        code, _, err = self.run_radb(["--frames", "2", "-c", "SET frames = 4"])
        self.assertEqual(code, 1)


if __name__ == "__main__":
    unittest.main()