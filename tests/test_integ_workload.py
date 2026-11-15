"""Integration (Section 6): the rehearsal workload end to end, the reference
check, and the four module command line tools still working."""
import contextlib
import csv
import io
import json
import os
import tempfile
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


class Rehearsal(unittest.TestCase):
    """CSV files -> setup.sql through the shell -> 25 queries -> same answers
    as an independent SQL engine (SQLite) over the same rows."""

    @classmethod
    def setUpClass(cls):
        import gen_final
        import pg_tools
        import run_workload
        from shell.session import Session
        cls.tmp = tempfile.TemporaryDirectory()
        t = cls.tmp.name
        quiet = io.StringIO()
        with contextlib.redirect_stdout(quiet):
            gen_final.main(["--out", os.path.join(t, "csv")])
        with open(os.path.join(ROOT, "queries", "final", "setup.sql")) as f:
            setup = f.read().replace("data/final/", os.path.join(t, "csv") + "/")
        cls.db = os.path.join(t, "db")
        Session(cls.db, 8, quiet).run_script(setup)
        cls.out = os.path.join(t, "wl")
        work = os.path.join(ROOT, "queries", "final", "workload.sql")
        with contextlib.redirect_stdout(quiet):
            cls.code = run_workload.main(["--db", cls.db, "--queries", work, "--frames", "8",
                                          "--out", cls.out])
            pg_tools.main(["sqlite", "--db", cls.db, "--queries", work,
                           "--out", os.path.join(t, "ref")])
            cls.cmp = pg_tools.main(["compare", "--ours", os.path.join(cls.out, "rows"),
                                     "--ref", os.path.join(t, "ref", "rows")])

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    def test_every_query_runs(self):
        self.assertEqual(self.code, 0)
        with open(os.path.join(self.out, "summary.csv")) as f:
            rows = list(csv.DictReader(f))
        self.assertEqual(len(rows), 25)
        for r in rows:
            self.assertEqual(r["error"], "")
            self.assertEqual(int(r["rows"]), int(r["actual_rows"]))
            self.assertGreater(int(r["page_io"]), 0)

    def test_answers_match_the_reference_engine(self):
        self.assertEqual(self.cmp, 0)

    def test_report_files(self):
        with open(os.path.join(self.out, "summary.md")) as f:
            self.assertIn("| q25 |", f.read())
        with open(os.path.join(self.out, "plans", "q09.json")) as f:
            self.assertIn("op", json.load(f))

    def test_export_for_postgresql(self):
        import pg_tools
        out = os.path.join(self.tmp.name, "pg")
        with contextlib.redirect_stdout(io.StringIO()):
            pg_tools.main(["export", "--db", self.db, "--out", out, "--queries",
                           os.path.join(ROOT, "queries", "final", "workload.sql")])
        with open(os.path.join(out, "schema.sql")) as f:
            self.assertIn('COLLATE "C"', f.read())
        with open(os.path.join(out, "data", "Emp.csv")) as f:
            self.assertEqual(sum(1 for _ in f), 2000)
        with open(os.path.join(out, "run.sh")) as f:
            self.assertEqual(f.read().count("psql"), 25)


class ModuleToolsStillWork(unittest.TestCase):
    """Integration is wiring: storage, exec, sql and opt answer as before."""

    def setUp(self):
        import gen_m4
        self.tmp = tempfile.TemporaryDirectory()
        self.db = os.path.join(self.tmp.name, "m4")
        gen_m4.build(self.db)

    def tearDown(self):
        self.tmp.cleanup()

    def call(self, main, argv):
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = main(argv)
        return code, out.getvalue()

    def test_pipeline_of_the_four_tools(self):
        from execution.cli import main as exec_main
        from optimizer.cli import main as opt_main
        from sql.cli import main as sql_main
        from storage.cli import main as storage_main
        q = os.path.join(self.tmp.name, "q.json")
        p = os.path.join(self.tmp.name, "p.json")
        code, text = self.call(storage_main, ["scan", "--db", self.db, "--table", "Dept",
                                              "--limit", "2"])
        self.assertEqual(code, 0)
        out = io.StringIO()
        self.assertEqual(sql_main(["compile", "--db", self.db, "--json", "--query",
                                   "SELECT name FROM Emp WHERE eid = 7"], out=out), 0)
        with open(q, "w") as f:
            f.write(out.getvalue())
        out = io.StringIO()
        self.assertEqual(opt_main(["plan", "--db", self.db, "--logical", q, "--frames", "8"],
                                  out=out), 0)
        with open(p, "w") as f:
            f.write(out.getvalue())
        out = io.StringIO()
        self.assertEqual(exec_main(["run", "--db", self.db, "--plan", p, "--frames", "8"],
                                   out=out), 0)
        self.assertIn("Emp00007", out.getvalue())


if __name__ == "__main__":
    unittest.main()