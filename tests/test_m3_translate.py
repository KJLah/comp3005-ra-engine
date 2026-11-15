"""Module 3 step 6: translation (Sections 4 and 5).

Every Section 5 example as exact JSON, required cases 4, 5, 8 and 15, the bag
semantics rules, and a check that plans run in the Module 2 executor once
their names are made physical."""
import unittest

from sql import logical as L
from sql.logical import check_plan, walk

from m3_helpers import compile_sql, rename, scan

PHYSICAL = {"seqscan", "filter", "nested_loop_join", "sort_merge_join", "hash_join"}


def ops(plan):
    return [n["op"] for n in walk(plan)]


class SectionFiveExamples(unittest.TestCase):
    def check(self, sql, expected):
        p = compile_sql(sql)
        self.assertEqual(p, expected)
        self.assertEqual(check_plan(p), [])
        self.assertFalse(PHYSICAL & set(ops(p)))

    def test_5_1_filter_and_projection(self):
        self.check("SELECT name, age FROM Emp WHERE age > 30;",
                   {"op": "project", "columns": ["Emp.name", "Emp.age"],
                    "child": {"op": "select", "condition": "Emp.age > 30",
                              "child": scan("Emp")}})

    def test_5_2_join_with_aliases(self):
        self.check("""SELECT e.name, d.title
                        FROM Emp e JOIN Dept d ON e.did = d.did
                       WHERE d.budget > 1000;""",
                   {"op": "project", "columns": ["e.name", "d.title"],
                    "child": {"op": "select", "condition": "d.budget > 1000",
                              "child": {"op": "join", "condition": "e.did = d.did",
                                        "left": rename("e", scan("Emp")),
                                        "right": rename("d", scan("Dept"))}}})

    def test_5_3_comma_join(self):
        p = compile_sql("SELECT * FROM Emp e, Dept d WHERE e.did = d.did;")
        self.assertEqual(p["op"], "project")
        self.assertEqual(p["child"]["op"], "select")
        self.assertEqual(p["child"]["condition"], "e.did = d.did")
        self.assertEqual(p["child"]["child"], {"op": "cross",
                                               "left": rename("e", scan("Emp")),
                                               "right": rename("d", scan("Dept"))})

    def test_5_4_grouping_with_having(self):
        self.check("""SELECT did, COUNT(*) AS n, AVG(salary) AS avg_sal
                        FROM Emp
                       WHERE age > 25
                       GROUP BY did
                      HAVING COUNT(*) > 5
                       ORDER BY n DESC
                       LIMIT 10;""",
                   {"op": "limit", "n": 10,
                    "child": {"op": "sort", "keys": [["n", "desc"]],
                     "child": {"op": "project", "columns": ["Emp.did", "n", "avg_sal"],
                      "child": {"op": "select", "condition": "n > 5",
                       "child": {"op": "group_by", "keys": ["Emp.did"],
                                 "aggregates": [["count", "*", "n"],
                                                ["avg", "Emp.salary", "avg_sal"]],
                        "child": {"op": "select", "condition": "Emp.age > 25",
                                  "child": scan("Emp")}}}}}})

    def test_5_5_self_join(self):
        p = compile_sql("SELECT e.name, m.name FROM Emp e JOIN Emp m ON e.mgr = m.eid;")
        self.assertEqual(p["child"], {"op": "join", "condition": "e.mgr = m.eid",
                                      "left": rename("e", scan("Emp")),
                                      "right": rename("m", scan("Emp"))})

    def test_5_6_subquery_in_from(self):
        self.check("""SELECT t.did, t.n
                        FROM (SELECT did, COUNT(*) AS n FROM Emp GROUP BY did) AS t
                       WHERE t.n > 3;""",
                   {"op": "project", "columns": ["t.did", "t.n"],
                    "child": {"op": "select", "condition": "t.n > 3",
                     "child": {"op": "rename", "to": "t",
                      "child": {"op": "project", "columns": ["Emp.did", "n"],
                       "child": {"op": "group_by", "keys": ["Emp.did"],
                                 "aggregates": [["count", "*", "n"]],
                                 "child": scan("Emp")}}}}})

    def test_5_7_the_two_unions(self):
        a = {"op": "project", "columns": ["Emp.city"], "child": scan("Emp")}
        b = {"op": "project", "columns": ["Dept.city"], "child": scan("Dept")}
        self.check("SELECT city FROM Emp UNION ALL SELECT city FROM Dept;",
                   {"op": "union_all", "left": a, "right": b})
        self.check("SELECT city FROM Emp UNION SELECT city FROM Dept;",
                   {"op": "union", "left": a, "right": b})


class RequiredCases(unittest.TestCase):
    def test_case_4_union_vs_union_all_explain(self):
        u = L.explain(compile_sql("SELECT city FROM Emp UNION SELECT city FROM Emp"))
        ua = L.explain(compile_sql("SELECT city FROM Emp UNION ALL SELECT city FROM Emp"))
        self.assertNotEqual(u, ua)
        self.assertEqual(u.splitlines()[0], "union")
        self.assertEqual(ua.splitlines()[0], "union_all")
        self.assertEqual(u.splitlines()[1:], ua.splitlines()[1:])

    def test_case_5_where_and_having_on_opposite_sides(self):
        p = compile_sql("SELECT did, MAX(age) AS oldest FROM Emp WHERE salary > 100 "
                        "GROUP BY did HAVING MAX(age) > 40")
        self.assertEqual(ops(p), ["project", "select", "group_by", "select", "scan"])
        having, where = p["child"], p["child"]["child"]["child"]
        self.assertEqual(having["condition"], "oldest > 40")
        self.assertEqual(where["condition"], "Emp.salary > 100")

    def test_case_8_subquery_joined_to_base_table(self):
        p = compile_sql("SELECT d.title, t.n FROM Dept d "
                        "JOIN (SELECT did, COUNT(*) AS n FROM Emp GROUP BY did) AS t "
                        "ON d.did = t.did")
        j = p["child"]
        self.assertEqual(j["op"], "join")
        self.assertEqual(j["condition"], "d.did = t.did")
        self.assertEqual(j["left"], rename("d", scan("Dept")))
        self.assertEqual(j["right"]["op"], "rename")
        self.assertEqual(j["right"]["to"], "t")
        self.assertEqual(ops(j["right"]), ["rename", "project", "group_by", "scan"])
        self.assertEqual(check_plan(p), [])

    COMMAS = ("SELECT e.name, p.pname FROM Emp e, Works w, Proj p, Dept d "
              "WHERE e.eid = w.eid AND w.pid = p.pid AND p.did = d.did AND d.budget > 10")
    JOINS = ("SELECT e.name, p.pname FROM Emp e JOIN Works w ON e.eid = w.eid "
             "JOIN Proj p ON w.pid = p.pid JOIN Dept d ON p.did = d.did "
             "WHERE d.budget > 10")

    def test_case_15_four_table_chain(self):
        c = compile_sql(self.COMMAS)
        j = compile_sql(self.JOINS)
        # commas: one select with the whole condition over three crosses
        self.assertEqual(ops(c), ["project", "select", "cross", "cross", "cross",
                                  "rename", "scan", "rename", "scan",
                                  "rename", "scan", "rename", "scan"])
        self.assertEqual(c["child"]["condition"],
                         "e.eid = w.eid and w.pid = p.pid and p.did = d.did and d.budget > 10")
        # JOIN ON: each condition on its join, WHERE stays above
        self.assertEqual(ops(j), ["project", "select", "join", "join", "join",
                                  "rename", "scan", "rename", "scan",
                                  "rename", "scan", "rename", "scan"])
        self.assertEqual([n["condition"] for n in walk(j) if n["op"] in ("select", "join")],
                         ["d.budget > 10", "p.did = d.did", "w.pid = p.pid", "e.eid = w.eid"])

        # the plans differ only in where the predicates sit
        def strip(n):
            n = dict(n)
            if n["op"] == "join":
                n["op"] = "cross"
            n.pop("condition", None)
            for f in ("child", "left", "right"):
                if f in n:
                    n[f] = strip(n[f])
            if n["op"] == "select":
                return n["child"]
            return n
        self.assertEqual(strip(c), strip(j))
        # both are left deep, in FROM order
        self.assertEqual([n["table"] for n in walk(c) if n["op"] == "scan"],
                         ["Emp", "Works", "Proj", "Dept"])


class BagSemantics(unittest.TestCase):
    def test_projection_never_adds_distinct(self):
        p = compile_sql("SELECT city FROM Emp")
        self.assertNotIn("distinct", ops(p))

    def test_select_distinct_above_project(self):
        p = compile_sql("SELECT DISTINCT city FROM Emp ORDER BY city LIMIT 3")
        self.assertEqual(ops(p), ["limit", "sort", "distinct", "project", "scan"])

    def test_union_all_alone_union_deduplicates(self):
        self.assertEqual(ops(compile_sql("SELECT city FROM Emp UNION ALL SELECT city FROM Dept"))[0],
                         "union_all")
        self.assertNotIn("distinct", ops(compile_sql(
            "SELECT city FROM Emp UNION SELECT city FROM Dept")))

    def test_intersect_except_no_extra_distinct(self):
        for op in ("INTERSECT", "EXCEPT"):
            p = compile_sql(f"SELECT city FROM Emp {op} SELECT city FROM Dept")
            self.assertEqual(p["op"], op.lower())
            self.assertNotIn("distinct", ops(p))

    def test_distinct_over_group_by_is_not_emitted(self):
        p = compile_sql("SELECT DISTINCT did, COUNT(*) AS n FROM Emp GROUP BY did")
        self.assertNotIn("distinct", ops(p))
        p = compile_sql("SELECT DISTINCT COUNT(*) FROM Emp")
        self.assertNotIn("distinct", ops(p))

    def test_distinct_kept_when_a_grouping_column_is_dropped(self):
        p = compile_sql("SELECT DISTINCT COUNT(*) AS n FROM Emp GROUP BY did")
        self.assertEqual(ops(p)[:2], ["distinct", "project"])

    def test_set_operators_left_associative_in_the_plan(self):
        p = compile_sql("SELECT city FROM Emp UNION SELECT city FROM Dept "
                        "EXCEPT SELECT name FROM Emp")
        self.assertEqual((p["op"], p["left"]["op"]), ("except", "union"))


class Predicates(unittest.TestCase):
    def test_where_condition_kept_whole_and_unmoved(self):
        p = compile_sql("SELECT e.name FROM Emp e JOIN Dept d ON e.did = d.did "
                        "WHERE e.age > 30 AND (d.city = 'Ottawa' OR NOT d.budget < 5)")
        self.assertEqual(p["child"]["condition"],
                         "e.age > 30 and (d.city = 'Ottawa' or not d.budget < 5)")
        self.assertEqual(p["child"]["child"]["condition"], "e.did = d.did")

    def test_strings_are_requoted(self):
        p = compile_sql("SELECT name FROM Emp WHERE city <> 'O''Hare'")
        self.assertEqual(p["child"]["condition"], "Emp.city <> 'O''Hare'")


# ---- the plans run in Module 2 once made physical --------------------------------
def to_physical(n):
    """A stand-in for Module 4, enough for plans without rename, cross or
    select-list aliases: rename the operators to Module 2's names and pick a
    mode. Aggregate names must be the defaults Module 2 also produces."""
    op = n["op"]
    if op == "scan":
        return {"op": "seqscan", "table": n["table"]}
    if op == "select":
        return {"op": "filter", "condition": n["condition"], "child": to_physical(n["child"])}
    if op == "project":
        assert "as" not in n
        return {"op": "project", "columns": n["columns"], "child": to_physical(n["child"])}
    if op == "group_by":
        for fn, col, name in n["aggregates"]:
            assert name == ("count_star" if col == "*" else f"{fn}_{col.replace('.', '_')}")
        return {"op": "group_by", "mode": "sort", "keys": n["keys"],
                "aggregates": [[fn, col] for fn, col, _ in n["aggregates"]],
                "child": to_physical(n["child"])}
    if op == "distinct":
        return {"op": "distinct", "mode": "hash", "child": to_physical(n["child"])}
    if op in ("sort", "limit"):
        out = {k: v for k, v in n.items() if k != "child"}
        out["child"] = to_physical(n["child"])
        return out
    if op == "join":
        return {"op": "nested_loop_join", "condition": n["condition"],
                "left": to_physical(n["left"]), "right": to_physical(n["right"])}
    if op in L.SET_OPS:
        return {"op": op, "left": to_physical(n["left"]), "right": to_physical(n["right"])}
    raise AssertionError(f"{op} needs Module 4")


class RunsInModuleTwo(unittest.TestCase):
    EMP = "eid:INT,name:VARCHAR(20),age:INT,salary:INT,did:INT,mgr:INT,city:VARCHAR(20)"
    DEPT = "did:INT,title:VARCHAR(20),budget:INT,city:VARCHAR(20)"
    ROWS = ["1,Ann,40,100,1,0,Ottawa", "2,Bob,25,80,1,1,Ottawa", "3,Cy,35,90,2,1,Toronto",
            "4,Di,35,90,2,1,Toronto", "5,Ed,50,120,3,0,Ottawa"]

    def run_sql(self, sql, rows):
        from m2_helpers import make_db, run_plan
        tmp = make_db({"Emp": (self.EMP, rows),
                       "Dept": (self.DEPT, ["1,Eng,500,Ottawa", "2,Ops,50,Toronto"])})
        try:
            return run_plan(tmp.name, to_physical(compile_sql(sql)), frames=8).rows
        finally:
            tmp.cleanup()

    def test_case_13_count_star_on_an_empty_table_is_one_row(self):
        self.assertEqual(self.run_sql("SELECT COUNT(*) FROM Emp", []), [(0,)])
        self.assertEqual(self.run_sql("SELECT COUNT(*) FROM Emp", self.ROWS), [(5,)])

    def test_bags_keep_duplicates(self):
        rows = self.run_sql("SELECT age, salary FROM Emp WHERE age > 30", self.ROWS)
        self.assertEqual(sorted(rows), [(35, 90), (35, 90), (40, 100), (50, 120)])
        rows = self.run_sql("SELECT DISTINCT age, salary FROM Emp WHERE age > 30", self.ROWS)
        self.assertEqual(sorted(rows), [(35, 90), (40, 100), (50, 120)])

    def test_union_vs_union_all_results(self):
        ua = self.run_sql("SELECT city FROM Emp UNION ALL SELECT city FROM Dept", self.ROWS)
        u = self.run_sql("SELECT city FROM Emp UNION SELECT city FROM Dept", self.ROWS)
        self.assertEqual(len(ua), 7)
        self.assertEqual(sorted(u), [("Ottawa",), ("Toronto",)])

    def test_not_equal_reaches_the_executor(self):
        rows = self.run_sql("SELECT name FROM Emp WHERE city <> 'Ottawa' ORDER BY name",
                            self.ROWS)
        self.assertEqual(rows, [("Cy",), ("Di",)])

    def test_grouping_with_having_and_order(self):
        rows = self.run_sql("SELECT did, COUNT(*), MAX(age) FROM Emp WHERE age > 20 "
                            "GROUP BY did HAVING COUNT(*) > 1 ORDER BY Emp.did DESC",
                            self.ROWS)
        self.assertEqual(rows, [(2, 2, 35), (1, 2, 40)])

    def test_join(self):
        rows = self.run_sql("SELECT Emp.name, Dept.title FROM Emp JOIN Dept "
                            "ON Emp.did = Dept.did WHERE Dept.budget > 100", self.ROWS)
        self.assertEqual(sorted(rows), [("Ann", "Eng"), ("Bob", "Eng")])


class BonusOneConditionParser(unittest.TestCase):
    def test_angle_brackets_mean_not_equal(self):
        from ra_engine.parser import parse_condition
        self.assertEqual(parse_condition("a <> 1"), parse_condition("a != 1"))


if __name__ == "__main__":
    unittest.main()