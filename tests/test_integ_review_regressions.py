"""Regression coverage for the integration review corrections."""
import contextlib
import csv
import io
import json
import os
import tempfile
import unittest
from unittest.mock import patch

import pg_tools
import run_workload
from m2_helpers import make_db
from shell.cli import main as radb
from shell.session import Session
from shell.split import ShellError


class IntegrationReviewRegressions(unittest.TestCase):
    def compare(self, ours_rows, ref_rows, types, ordered=""):
        with tempfile.TemporaryDirectory() as t:
            ours = os.path.join(t, "rows")
            ref = os.path.join(t, "reference")
            schemas = os.path.join(t, "schemas")
            for d in (ours, ref, schemas):
                os.makedirs(d)
            for directory, rows in ((ours, ours_rows), (ref, ref_rows)):
                with open(os.path.join(directory, "q01.csv"), "w", newline="") as f:
                    w = csv.writer(f)
                    w.writerow(["v"] * len(types))
                    w.writerows(rows)
            with open(os.path.join(schemas, "q01.json"), "w") as f:
                json.dump(types, f)
            with open(os.path.join(t, "summary.csv"), "w", newline="") as f:
                w = csv.DictWriter(f, ["query", "rows", "actual_rows", "error"])
                w.writeheader()
                w.writerow({"query": "q01", "rows": len(ours_rows),
                            "actual_rows": len(ours_rows), "error": ""})
            args = ["compare", "--ours", ours, "--ref", ref]
            if ordered:
                args += ["--ordered", ordered]
            with contextlib.redirect_stdout(io.StringIO()):
                return pg_tools.main(args)

    def test_varchar_values_are_not_normalized_as_numbers(self):
        self.assertEqual(self.compare([["001"]], [["1"]], ["VARCHAR"]), 1)

    def test_bigints_are_compared_without_float_precision_loss(self):
        self.assertEqual(self.compare([["9007199254740992"]],
                                      [["9007199254740993"]], ["BIGINT"]), 1)

    def test_real_values_use_documented_four_decimal_precision(self):
        self.assertEqual(self.compare([["0.3333"]], [["0.3333333333"]], ["REAL"]), 0)

    def test_real_display_rounding_matches_executor_at_a_halfway_value(self):
        # Python's REAL output for 1 / 20000 is formatted as 0.0001.
        self.assertEqual(self.compare([[format(1 / 20000, ".4f")]],
                                      [["0.00005"]], ["REAL"]), 0)

    def test_requested_order_is_checked(self):
        self.assertEqual(self.compare([["1"], ["2"]], [["2"], ["1"]],
                                      ["INT"], "q01"), 1)
        self.assertEqual(self.compare([["1"], ["2"]], [["2"], ["1"]], ["INT"]), 0)

    def test_missing_our_query_file_is_rejected(self):
        with tempfile.TemporaryDirectory() as t:
            ours, ref = os.path.join(t, "rows"), os.path.join(t, "ref")
            os.makedirs(ours)
            os.makedirs(ref)
            with open(os.path.join(t, "summary.csv"), "w") as f:
                f.write("query,rows,actual_rows,error\nq01,1,1,\n")
            with open(os.path.join(ref, "q01.csv"), "w") as f:
                f.write("x\n1\n")
            with contextlib.redirect_stdout(io.StringIO()):
                code = pg_tools.main(["compare", "--ours", ours, "--ref", ref])
            self.assertEqual(code, 1)

    def test_empty_workload_cannot_report_success(self):
        with tempfile.TemporaryDirectory() as t:
            ours, ref = os.path.join(t, "rows"), os.path.join(t, "ref")
            os.makedirs(ours)
            os.makedirs(ref)
            with open(os.path.join(t, "summary.csv"), "w") as f:
                f.write("query,rows,actual_rows,error\n")
            with contextlib.redirect_stdout(io.StringIO()):
                code = pg_tools.main(["compare", "--ours", ours, "--ref", ref])
            self.assertEqual(code, 1)

    def test_headerless_reference_file_is_rejected(self):
        with tempfile.TemporaryDirectory() as t:
            p = os.path.join(t, "q.csv")
            open(p, "w").close()
            with self.assertRaises(ValueError):
                pg_tools._read(p, ["INT"])

    def test_comments_before_ddl_and_changed_frames_are_reported(self):
        with tempfile.TemporaryDirectory() as t:
            q, db, out = (os.path.join(t, p) for p in ("q.sql", "db", "out"))
            with open(q, "w") as f:
                f.write("-- setup\nCREATE TABLE T (x INT); "
                        "INSERT INTO T VALUES (1); SET frames = 4; SELECT x FROM T;")
            with contextlib.redirect_stdout(io.StringIO()):
                code = run_workload.main(["--db", db, "--queries", q,
                                          "--frames", "8", "--out", out])
            self.assertEqual(code, 0)
            with open(os.path.join(out, "summary.csv")) as f:
                report = list(csv.DictReader(f))
            self.assertEqual(len(report), 1)
            self.assertEqual(report[0]["frames"], "4")
            with open(os.path.join(out, "schemas", "q01.json")) as f:
                self.assertEqual(json.load(f), ["INT"])

    def test_sqlite_query_failure_returns_nonzero_and_removes_stale_answer(self):
        with make_db({"T": ("x:INT", ["1"])}) as db, tempfile.TemporaryDirectory() as t:
            q = os.path.join(t, "q.sql")
            with open(q, "w") as f:
                f.write("SELECT x FROM Missing;")
            os.makedirs(os.path.join(t, "rows"))
            stale = os.path.join(t, "rows", "q01.csv")
            with open(stale, "w") as f:
                f.write("x\n1\n")
            with contextlib.redirect_stdout(io.StringIO()):
                code = pg_tools.main(["sqlite", "--db", db, "--queries", q, "--out", t])
            self.assertEqual(code, 1)
            self.assertFalse(os.path.exists(stale))

    def test_postgresql_export_preserves_empty_strings_and_stops_on_errors(self):
        with make_db({"T": ("x:INT,s:VARCHAR(10)", ['1,""'])}) as db, tempfile.TemporaryDirectory() as t:
            q = os.path.join(t, "q.sql")
            with open(q, "w") as f:
                f.write("SELECT s FROM T;")
            with contextlib.redirect_stdout(io.StringIO()):
                pg_tools.main(["export", "--db", db, "--out", t, "--queries", q])
            with open(os.path.join(t, "data", "T.csv")) as f:
                self.assertEqual(f.read().strip(), '"1",""')
            with open(os.path.join(t, "run.sh")) as f:
                script = f.read()
            self.assertIn("set -eu", script)
            self.assertIn("-v ON_ERROR_STOP=1", script)

    def test_row_printing_is_excluded_from_execution_time(self):
        with make_db({"T": ("x:INT", ["1"])}) as db:
            clock = [0.0]

            class SlowOutput(io.StringIO):
                def write(self, text):
                    clock[0] += 1.0
                    return super().write(text)

            s = Session(db, 8, SlowOutput())
            with patch("shell.session.time.perf_counter", side_effect=lambda: clock[0]):
                result = s.execute("SELECT x FROM T")
            self.assertEqual(result.exec_ms, 0.0)
            self.assertGreater(clock[0], 0)

    def test_shell_rejects_unsupported_three_frame_budget(self):
        with tempfile.TemporaryDirectory() as db:
            with self.assertRaises(ShellError):
                Session(db, 3)
            s = Session(db, 4)
            with self.assertRaises(ShellError):
                s.execute("SET frames = 3")
            code = radb(["--db", db, "--frames", "3", "-c", "SET frames = 4"],
                        out=io.StringIO(), err=io.StringIO())
            self.assertEqual(code, 1)


if __name__ == "__main__":
    unittest.main()
