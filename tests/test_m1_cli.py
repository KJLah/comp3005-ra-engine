"""Module 1 section 7: the command line, run as a user would run it."""
import json
import os
import subprocess
import sys
import tempfile
import unittest

ROOT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..")


def run(*args):
    p = subprocess.run([sys.executable, "-m", "storage", *args],
                       cwd=ROOT, capture_output=True, text=True)
    return p.returncode, p.stdout + p.stderr


class CLI(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.db = os.path.join(self.tmp.name, "db")

    def tearDown(self):
        self.tmp.cleanup()

    def write(self, name, text):
        path = os.path.join(self.tmp.name, name)
        with open(path, "w", encoding="utf-8") as f:
            f.write(text)
        return path

    def make_r(self, rows):
        code, out = run("create-table", "--db", self.db, "--name", "R",
                        "--schema", "a:INT,b:VARCHAR(20),c:INT")
        self.assertEqual(code, 0, out)
        csv = self.write("r.csv", "".join(f"{a},{b},{c}\n" for a, b, c in rows))
        code, out = run("load", "--db", self.db, "--table", "R", "--file", csv)
        self.assertEqual(code, 0, out)

    def test_scan_json_has_the_required_shape(self):
        self.make_r([(42, "bob", 7), (43, "amy", 9)])
        code, out = run("scan", "--db", self.db, "--table", "R", "--frames", "8", "--json")
        self.assertEqual(code, 0, out)
        doc = json.loads(out)
        self.assertEqual(doc["table"], "R")
        self.assertEqual(doc["schema"], [["a", "INT"], ["b", "VARCHAR", 20], ["c", "INT"]])
        self.assertEqual(doc["rows"], [[42, "bob", 7], [43, "amy", 9]])
        self.assertEqual(set(doc["stats"]), {"hits", "misses", "evictions", "reads", "writes"})

    def test_scan_limit(self):
        self.make_r([(i, "x", i) for i in range(10)])
        _, out = run("scan", "--db", self.db, "--table", "R", "--limit", "3", "--json")
        self.assertEqual(len(json.loads(out)["rows"]), 3)

    def test_scan_counters_are_repeatable_and_ignore_the_catalog(self):
        self.make_r([(i, "name", i) for i in range(2000)])    # 1 dir + 9 data pages
        outs = [run("scan", "--db", self.db, "--table", "R", "--frames", str(b),
                    "--json")[1] for b in (2, 2, 8, 64)]
        stats = [json.loads(o)["stats"] for o in outs]
        self.assertEqual(stats[0], stats[1])
        for s in stats:
            self.assertEqual(s["reads"], 10)

    def test_trace_reproduces_5_3(self):
        trace = self.write("t.txt", "1\n2\n3\n1\n4\n2\n")
        _, out = run("trace", "--db", self.db, "--file", trace, "--frames", "3",
                     "--policy", "lru", "--json")
        self.assertEqual(json.loads(out), {"policy": "lru", "frames": 3, "hits": 1,
                                           "misses": 5, "evictions": 2, "reads": 5,
                                           "writes": 0})
        _, out = run("trace", "--db", self.db, "--file", trace, "--frames", "3",
                     "--policy", "clock", "--json")
        self.assertEqual(json.loads(out)["hits"], 2)
        self.assertEqual(json.loads(out)["evictions"], 1)

    def test_layout_shows_header_and_slots(self):
        self.make_r([(42, "bob", 7)])                  # 4 + 5 + 4 = 13 bytes
        code, out = run("layout", "--db", self.db, "--table", "R", "--page", "1")
        self.assertEqual(code, 0, out)
        self.assertIn("slot_count=1 free_end=4083 free_space=4071", out)
        self.assertIn("slot 0: (4083,13) live", out)
        _, out = run("layout", "--db", self.db, "--table", "R", "--page", "0")
        self.assertIn("entry 0: data_page=1 free_bytes=4071", out)

    def test_errors_are_one_line_and_exit_1(self):
        self.make_r([(1, "x", 1)])
        bad = self.write("bad.csv", "1,x,1\n2,y,zz\n")
        for args in (["scan", "--db", self.db, "--table", "Nope"],
                     ["load", "--db", self.db, "--table", "R", "--file", bad],
                     ["layout", "--db", self.db, "--table", "R", "--page", "99"],
                     ["scan", "--db", os.path.join(self.tmp.name, "missing"), "--table", "R"]):
            code, out = run(*args)
            self.assertEqual(code, 1, out)
            self.assertTrue(out.startswith("Error: "), out)
            self.assertNotIn("Traceback", out)
        _, out = run("load", "--db", self.db, "--table", "R", "--file", bad)
        self.assertIn("line 2, column 'c'", out)