"""Module 4 end to end: every physical plan runs unedited in Module 2, and
the Section 8 checklist items that need a real database."""
import io
import json
import os
import tempfile
import unittest
from collections import Counter

import gen_m4
from execution.plan import validate
from optimizer import cli
from optimizer.planner import Planner, naive, optimize
from optimizer.runner import preorder, run
from optimizer.stats import load_all

from m4_helpers import logical, query_names, workload_db

FAST = [q for q in query_names() if not q.startswith(("q09", "q10", "q17"))]


class Workload(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.db = workload_db()
        cls.stats = load_all(cls.db)

    def test_every_plan_validates_and_runs_with_the_unoptimized_answer(self):
        for q in query_names():
            lg = logical(q, self.db)
            want = None
            for B in (4, 8, 32):
                with self.subTest(query=q, B=B):
                    plan = optimize(lg, self.stats, B)
                    validate(plan)
                    got = Counter(run(self.db, plan, B).rows)
                    if want is None:
                        base = naive(lg, self.stats, 32) if q in FAST else optimize(
                            lg, self.stats, 32, rules=())
                        want = Counter(run(self.db, base, 32).rows)
                    self.assertEqual(got, want)

    def test_order_by_is_kept(self):
        lg = logical("q14_order_limit", self.db)
        rows = run(self.db, optimize(lg, self.stats, 8), 8).rows
        self.assertEqual([r[1] for r in rows], sorted((r[1] for r in rows), reverse=True))

    def test_commas_and_join_on_give_the_same_physical_plan(self):
        for B in (4, 8, 32):
            a = optimize(logical("q07_join_on", self.db), self.stats, B)
            b = optimize(logical("q08_join_commas", self.db), self.stats, B)
            self.assertEqual(a, b)

    def test_changing_b_changes_the_join_algorithm(self):
        def algos(B):
            p = optimize(logical("q17_correlated_join", self.db), self.stats, B)
            return [n["op"] for n in preorder(p) if n["op"].endswith("join")]
        self.assertEqual(algos(32), ["nested_loop_join"])
        self.assertEqual(algos(3), ["hash_join"])

    def test_joins_are_left_deep(self):
        for q in ("q09_three_way", "q10_four_way"):
            p = optimize(logical(q, self.db), self.stats, 8)
            for n in preorder(p):
                if n["op"].endswith("join"):
                    self.assertFalse(any(m["op"].endswith("join") for m in preorder(n["right"])))

    def test_estimates_are_exact_for_a_plain_scan(self):
        P = Planner(self.stats, 8)
        p = P.plan(logical("q01_eq", self.db))
        r = run(self.db, p, 8)
        for node, c in r.nodes:
            if node["op"] == "seqscan":
                self.assertEqual(P.est.rows(node), c["tuples_out"])


class NoStatistics(unittest.TestCase):
    def test_plans_are_valid_without_analyze(self):
        tmp = tempfile.TemporaryDirectory()
        try:
            db = os.path.join(tmp.name, "m4")
            gen_m4.build(db, analyze_too=False)
            st = load_all(db)
            self.assertFalse(any(t.analyzed for t in st.values()))
            for q in FAST:
                plan = optimize(logical(q, db), st, 8)
                validate(plan)
                run(db, plan, 8)
        finally:
            tmp.cleanup()


class CommandLine(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.db = workload_db()
        cls.tmp = tempfile.TemporaryDirectory()
        cls.q = os.path.join(cls.tmp.name, "q.json")
        with open(cls.q, "w") as f:
            json.dump(logical("q09_three_way", cls.db), f)

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    def opt(self, *argv):
        out, err = io.StringIO(), io.StringIO()
        code = cli.main(list(argv), out, err)
        return code, out.getvalue(), err.getvalue()

    def test_plan_output_runs_in_exec(self):
        from execution import cli as exec_cli
        code, out, _ = self.opt("plan", "--db", self.db, "--logical", self.q, "--frames", "8", "--json")
        self.assertEqual(code, 0)
        path = os.path.join(self.tmp.name, "p.json")
        with open(path, "w") as f:
            f.write(out)
        buf = io.StringIO()
        self.assertEqual(exec_cli.main(["run", "--db", self.db, "--plan", path,
                                        "--frames", "8", "--json"], buf, io.StringIO()), 0)
        self.assertGreater(len(json.loads(buf.getvalue())["rows"]), 0)

    def test_explain_lines(self):
        code, out, _ = self.opt("explain", "--db", self.db, "--logical", self.q, "--frames", "8",
                                "--rewrites")
        self.assertEqual(code, 0)
        self.assertIn("before", out.splitlines()[0])
        self.assertIn("est_rows=", out)
        self.assertIn("est_cost=", out)
        self.assertTrue(out.rstrip().splitlines()[-1].startswith("total est_cost="))

    def test_dp_table(self):
        code, out, _ = self.opt("dp-table", "--db", self.db, "--logical", self.q, "--frames", "8")
        self.assertEqual(code, 0)
        self.assertEqual(out.splitlines()[0].split()[:2], ["Subset", "Best"])
        self.assertIn("{e,w,p}", out)

    def test_analyze_and_errors(self):
        code, out, _ = self.opt("analyze", "--db", self.db, "--table", "Dept")
        self.assertEqual(code, 0)
        self.assertIn("n_tuples=40", out)
        code, _, err = self.opt("plan", "--db", self.db, "--logical", "nope.json", "--frames", "8")
        self.assertEqual(code, 1)
        self.assertTrue(err.startswith("error:"))
        code, _, err = self.opt("explain", "--db", self.db, "--logical", self.q, "--frames", "8",
                                "--off", "9")
        self.assertEqual(code, 1)


if __name__ == "__main__":
    unittest.main()


class OneSidedJoinConditions(unittest.TestCase):
    """A one-sided conjunct inside a join condition must never be lost,
    with or without rule 2 (found by the rule ablation, DESIGN_LOG)."""

    @classmethod
    def setUpClass(cls):
        cls.db = workload_db()
        cls.stats = load_all(cls.db)

    def check(self, sql, rules):
        from sql.cli import compile_script
        from sql.resolve import StorageCatalog
        lg = compile_script(sql, StorageCatalog(self.db))[0]
        want = Counter(run(self.db, naive(lg, self.stats, 32), 32).rows)
        got = Counter(run(self.db, optimize(lg, self.stats, 8, rules=rules), 8).rows)
        self.assertEqual(got, want)

    def test_join_on_with_a_filter_inside(self):
        sql = ("SELECT e.name, d.dname FROM Emp e JOIN Dept d "
               "ON e.did = d.did AND d.budget > 150000 AND e.age > 60")
        for rules in (range(1, 8), {1, 3, 4, 5, 6, 7}, ()):
            self.check(sql, set(rules))

    def test_every_single_rule_off_keeps_the_answer(self):
        lg = logical("q17_correlated_join", self.db)
        want = Counter(run(self.db, optimize(lg, self.stats, 32), 32).rows)
        for off in range(1, 8):
            with self.subTest(rule_off=off):
                plan = optimize(lg, self.stats, 8, rules=set(range(1, 8)) - {off})
                self.assertEqual(Counter(run(self.db, plan, 8).rows), want)