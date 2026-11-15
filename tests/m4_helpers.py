"""Shared helpers for the Module 4 tests."""
import json
import os
import tempfile

from optimizer.stats import ColStats, TableStats

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
QUERIES = os.path.join(ROOT, "queries", "m4")

_DB = None


def workload_db():
    """The gen_m4 database (built and analyzed once per test run)."""
    global _DB
    if _DB is None:
        import gen_m4
        tmp = tempfile.TemporaryDirectory()
        gen_m4.build(os.path.join(tmp.name, "m4"))
        _DB = (tmp, os.path.join(tmp.name, "m4"))
    return _DB[1]


def query_names():
    return sorted(f[:-4] for f in os.listdir(QUERIES) if f.endswith(".sql"))


def logical(name, db):
    from sql.cli import compile_script
    from sql.resolve import StorageCatalog
    with open(os.path.join(QUERIES, name + ".sql"), encoding="utf-8") as f:
        return compile_script(f.read(), StorageCatalog(db))[0]


def section2_stats():
    """Section 2's worked example: Emp 10,000 tuples on 200 pages, ndv(did)
    = 50, age 20..70 uniform; Dept 50 tuples, ndv(did) = 50."""
    emp = TableStats("Emp", 10000, 200, 40.96, {
        "eid": ColStats(10000, 1, 10000, 4.0), "name": ColStats(10000, "a", "z", 20.96),
        "age": ColStats(51, 20, 70, 4.0), "salary": ColStats(100, 1000, 100000, 4.0),
        "did": ColStats(50, 1, 50, 4.0), "budget": ColStats(1, 0, 0, 4.0)}, True)
    dept = TableStats("Dept", 50, 1, 40.0, {
        "did": ColStats(50, 1, 50, 4.0), "title": ColStats(50, "a", "z", 28.0),
        "budget": ColStats(50, 0, 5000, 4.0), "city": ColStats(5, "a", "z", 4.0)}, True)
    return {"Emp": emp, "Dept": dept}


def copy(plan):
    return json.loads(json.dumps(plan))