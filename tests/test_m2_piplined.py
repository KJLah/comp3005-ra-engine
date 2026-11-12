"""Module 2 step 2: seqscan, filter, project, limit, union_all and the CLI."""
import io
import json
import os
import unittest

from execution.cli import main
from execution.errors import BindError, PlanError

from m2_helpers import make_db, run_plan, scan

R_ROWS = [f"{i},{'cs' if i % 3 == 0 else 'math'},{i % 7}" for i in range(1, 2001)]


class Pipelined(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = make_db({
            "R": ("a:INT,b:VARCHAR(20),c:INT", R_ROWS),
            "S": ("x:INT,y:VARCHAR(20),z:INT", ["1,cs,5", "2,math,6"]),
            "E": ("a:INT,b:VARCHAR(20),c:INT", []),
        })
        cls.db = cls.tmp.name

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    def test_seqscan_returns_every_tuple_in_heap_order_one_frame(self):
        res = run_plan(self.db, scan("R"), 4)
        self.assertEqual(len(res.rows), 2000)
        self.assertEqual(res.rows[0], (1, "math", 1))
        c = res.node("plan")
        self.assertEqual(c["comparisons"], 0)
        self.assertEqual(c["tuples_out"], 2000)
        self.assertLessEqual(res.ctx.pool.peak_pinned, 1)
        # M data pages + 1 directory page, all reads on a cold pool
        info = res.ctx.catalog.get("R")
        self.assertEqual(c["page_reads"], info.num_pages)

    def test_seqscan_works_at_b_1(self):
        self.assertEqual(len(run_plan(self.db, scan("R"), 1).rows), 2000)

    def test_filter_counts_one_comparison_per_tuple(self):
        res = run_plan(self.db, {"op": "filter", "condition": "R.a > 500 and R.b = 'cs'",
                                 "child": scan("R")}, 4)
        self.assertEqual(len(res.rows), len([i for i in range(501, 2001) if i % 3 == 0]))
        self.assertEqual(res.node("plan")["comparisons"], 2000)
        self.assertEqual(res.node("plan")["page_reads"], 0)

    def test_project_keeps_duplicates_and_order(self):
        res = run_plan(self.db, {"op": "project", "columns": ["R.c", "R.b"],
                                 "child": scan("R")}, 4)
        self.assertEqual(len(res.rows), 2000)
        self.assertEqual(res.rows[0], (1, "math"))
        self.assertEqual(res.root.schema.names(), ["R.c", "R.b"])

    def test_limit_actually_stops_pulling(self):
        res = run_plan(self.db, {"op": "limit", "n": 5, "child": scan("R")}, 4)
        self.assertEqual(len(res.rows), 5)
        self.assertEqual(res.node("plan.child")["tuples_out"], 5)
        self.assertEqual(res.node("plan.child")["page_reads"], 2)   # dir + first page
        self.assertEqual(run_plan(self.db, {"op": "limit", "n": 0,
                                            "child": scan("R")}, 4).rows, [])

    def test_union_all_concatenates_and_takes_left_names(self):
        res = run_plan(self.db, {"op": "union_all", "left": scan("S"),
                                 "right": {"op": "limit", "n": 2, "child": scan("R")}}, 4)
        self.assertEqual(res.rows, [(1, "cs", 5), (2, "math", 6),
                                    (1, "math", 1), (2, "math", 2)])
        self.assertEqual(res.root.schema.names(), ["S.x", "S.y", "S.z"])

    def test_union_all_type_mismatch(self):
        plan = {"op": "union_all", "left": scan("S"),
                "right": {"op": "project", "columns": ["R.b", "R.a", "R.c"],
                          "child": scan("R")}}
        with self.assertRaises(BindError):
            run_plan(self.db, plan, 4)

    def test_empty_input_through_every_pipelined_operator(self):
        for plan in [scan("E"),
                     {"op": "filter", "condition": "E.a = 1", "child": scan("E")},
                     {"op": "project", "columns": ["E.b"], "child": scan("E")},
                     {"op": "limit", "n": 3, "child": scan("E")},
                     {"op": "union_all", "left": scan("E"), "right": scan("E")}]:
            self.assertEqual(run_plan(self.db, plan, 4).rows, [], plan["op"])

    def test_unknown_table_and_column_rejected_before_any_read(self):
        with self.assertRaises(BindError) as cm:
            run_plan(self.db, {"op": "filter", "condition": "R.a = 1",
                               "child": scan("Nope")}, 4)
        self.assertIn("plan.child", str(cm.exception))
        with self.assertRaises(BindError):
            run_plan(self.db, {"op": "project", "columns": ["R.q"], "child": scan("R")}, 4)

    def test_self_join_schema_needs_distinct_names(self):
        with self.assertRaises(BindError):
            run_plan(self.db, {"op": "union_all", "left": scan("R"),
                               "right": {"op": "project", "columns": ["R.a", "R.a", "R.c"],
                                         "child": scan("R")}}, 4)

    def test_same_plan_same_counters(self):
        plan = {"op": "filter", "condition": "R.c = 3", "child": scan("R")}
        a = run_plan(self.db, plan, 4)
        b = run_plan(self.db, plan, 4)
        self.assertEqual([op.counters() for _, op in a.root.walk()],
                         [op.counters() for _, op in b.root.walk()])


class CommandLine(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = make_db({"R": ("a:INT,b:VARCHAR(20)", ["42,bob", "7,amy"])})
        cls.plan = os.path.join(cls.tmp.name, "p.json")
        with open(cls.plan, "w") as f:
            json.dump({"op": "filter", "condition": "R.a > 10", "child": scan("R")}, f)

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    def cli(self, *args):
        out, err = io.StringIO(), io.StringIO()
        code = main(list(args), out, err)
        return code, out.getvalue(), err.getvalue()

    def test_run_json_shape(self):
        code, out, _ = self.cli("run", "--db", self.tmp.name, "--plan", self.plan,
                                "--frames", "4", "--json")
        self.assertEqual(code, 0)
        doc = json.loads(out)
        self.assertEqual(doc["schema"], [["R.a", "INT"], ["R.b", "VARCHAR", 20]])
        self.assertEqual(doc["rows"], [[42, "bob"]])
        self.assertEqual(doc["totals"], {"tuples_out": 1, "comparisons": 2,
                                         "page_reads": 2, "page_writes": 0})

    def test_trace_one_line_per_node_indented(self):
        code, out, _ = self.cli("trace", "--db", self.tmp.name, "--plan", self.plan,
                                "--frames", "4")
        self.assertEqual(code, 0)
        lines = out.splitlines()
        self.assertTrue(lines[0].startswith("filter "))
        self.assertTrue(lines[1].startswith("  seqscan R "))
        self.assertIn("comparisons=2", lines[0])

    def test_bad_plan_names_the_node(self):
        bad = os.path.join(self.tmp.name, "bad.json")
        with open(bad, "w") as f:
            json.dump({"op": "filter", "condition": "R.a > 1", "child": {"op": "sacn"}}, f)
        code, _, err = self.cli("run", "--db", self.tmp.name, "--plan", bad, "--frames", "4")
        self.assertEqual(code, 1)
        self.assertIn("plan.child", err)


if __name__ == "__main__":
    unittest.main()
