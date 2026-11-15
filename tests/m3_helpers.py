"""Shared helpers for the Module 3 tests: a test catalog and compile()."""
from sql.parser import parse_query
from sql.resolve import DictCatalog
from sql.translate import translate

TABLES = {
    "Emp": [("eid", "INT"), ("name", "VARCHAR", 20), ("age", "INT"), ("salary", "INT"),
            ("did", "INT"), ("mgr", "INT"), ("city", "VARCHAR", 20)],
    "Dept": [("did", "INT"), ("title", "VARCHAR", 20), ("budget", "INT"),
             ("city", "VARCHAR", 20)],
    "Proj": [("pid", "INT"), ("pname", "VARCHAR", 20), ("did", "INT")],
    "Works": [("eid", "INT"), ("pid", "INT"), ("hours", "INT")],
}

CATALOG = DictCatalog(TABLES)


def compile_sql(text, catalog=CATALOG):
    return translate(parse_query(text), catalog)


# shorthands for expected plans
def scan(t):
    return {"op": "scan", "table": t}


def rename(to, child):
    return {"op": "rename", "to": to, "child": child}