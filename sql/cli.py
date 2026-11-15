"""Command line (Module 3, Section 8).

    sql parse   --query "SELECT ..." [--explain]     parse tree, no catalog needed
    sql compile --db DIR --file q.sql [--json]       logical plan (explain tree by
                                                     default, JSON with --json)
    sql check   --db DIR --file q.sql                errors only; exit 0 or 1
    sql exec    --db DIR --file s.sql                apply CREATE TABLE / INSERT
                                                     to the database for real

Every command also takes --query TEXT in place of --file. Without installing,
`python3 -m sql ...` is the same command.

Output of `compile --json`: the plan object when the input holds one query,
a JSON array of plan objects (in file order) when it holds several. CREATE
TABLE and INSERT statements are checked, and a created table is visible to
the statements after it, but compile and check never change the database.

Errors are printed in the one format of Section 6,
`error: <category>: <message> at line L, column C`, and the exit code is 1.
check prints them on standard output (they are its output); the other
commands print them on standard error. Panic mode: the first error stops.
Problems outside the SQL text (a missing file or database directory) use the
same shape with the category `io` and no position.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from typing import List, Optional, TextIO

from . import ast as A
from . import logical as L
from .errors import SQLError
from .parser import parse_script
from .printer import format_tree
from .resolve import DictCatalog, OverlayCatalog, StorageCatalog
from .statements import check_create, check_insert
from .translate import translate


class IOProblem(Exception):
    def render(self) -> str:
        return f"error: io: {self}"


def _source(args) -> str:
    if getattr(args, "query", None) is not None:
        return args.query
    if getattr(args, "file", None) is None:
        raise IOProblem("give --query TEXT or --file FILE")
    try:
        with open(args.file, encoding="utf-8") as f:
            return f.read()
    except OSError as e:
        raise IOProblem(f"cannot read '{args.file}': {e.strerror}") from None
    except UnicodeDecodeError:
        raise IOProblem(f"'{args.file}' is not UTF-8 text") from None


def _catalog(db: Optional[str]):
    if db is None:
        return DictCatalog({})
    try:
        return StorageCatalog(db)
    except FileNotFoundError as e:
        raise IOProblem(str(e)) from None


def compile_script(text: str, catalog) -> List[L.Plan]:
    """Check every statement in order; return the plans of the queries."""
    overlay = OverlayCatalog(catalog)
    plans = []
    for stmt in parse_script(text):
        if isinstance(stmt, A.CreateTable):
            overlay.create(stmt.name, check_create(stmt, overlay))
        elif isinstance(stmt, A.Insert):
            check_insert(stmt, overlay)
        else:
            plans.append(translate(stmt, overlay))
    return plans


# ---- commands -------------------------------------------------------------------------
def cmd_parse(args, out: TextIO) -> None:
    stmts = parse_script(_source(args))
    if args.explain:
        out.write("\n\n".join(format_tree(s) for s in stmts) + "\n")
    else:
        out.write(f"ok: {len(stmts)} statement(s)\n")


def cmd_compile(args, out: TextIO) -> None:
    plans = compile_script(_source(args), _catalog(args.db))
    if args.json:
        doc = plans[0] if len(plans) == 1 else plans
        out.write(json.dumps(doc, indent=2) + "\n")
    else:
        out.write("\n\n".join(L.explain(p) for p in plans) + ("\n" if plans else ""))


def cmd_check(args, out: TextIO) -> None:
    compile_script(_source(args), _catalog(args.db))


def cmd_exec(args, out: TextIO) -> None:
    from storage.buffer import BufferPool
    from storage.catalog import Catalog
    from storage.record import Column, encode
    text = _source(args)
    stmts = parse_script(text)              # a syntax error changes nothing
    if not os.path.isdir(args.db):
        os.makedirs(args.db)
    cat = Catalog(args.db, BufferPool(16))
    overlay = OverlayCatalog(StorageCatalog(args.db))
    touched = set()
    try:
        for stmt in stmts:
            if isinstance(stmt, A.CreateTable):
                cols = check_create(stmt, overlay)
                cat.create_table(stmt.name, [Column(c.name, c.type, c.length) for c in cols])
                overlay.create(stmt.name, cols)
                out.write(f"created table {stmt.name}\n")
            elif isinstance(stmt, A.Insert):
                cols = check_insert(stmt, overlay)
                schema = [Column(c.name, c.type, c.length) for c in cols]
                cat.open_heap(stmt.table).insert(encode(schema, [v.value for v in stmt.values]))
                touched.add(stmt.table)
            else:
                out.write(L.explain(translate(stmt, overlay)) + "\n")
    finally:
        for t in touched:
            cat.refresh_counts(t)
        cat.flush()
    if touched:
        out.write(f"inserted rows into {', '.join(sorted(touched))}\n")


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="sql", description="Module 3 SQL front end")
    sub = p.add_subparsers(dest="command", required=True)

    s = sub.add_parser("parse", help="parse SQL and print its parse tree (no catalog)")
    s.add_argument("--query")
    s.add_argument("--file")
    s.add_argument("--explain", action="store_true", help="print the indented parse tree")

    s = sub.add_parser("compile", help="print the logical plan of each query")
    s.add_argument("--db", required=True, help="database directory (Module 1 catalog)")
    s.add_argument("--file")
    s.add_argument("--query")
    s.add_argument("--json", action="store_true", help="logical plan JSON")
    s.add_argument("--explain", action="store_true", help="indented tree (the default)")

    s = sub.add_parser("check", help="report the first error; exit 0 or 1")
    s.add_argument("--db", required=True)
    s.add_argument("--file")
    s.add_argument("--query")

    s = sub.add_parser("exec", help="apply CREATE TABLE and INSERT to the database")
    s.add_argument("--db", required=True)
    s.add_argument("--file")
    s.add_argument("--query")
    return p


COMMANDS = {"parse": cmd_parse, "compile": cmd_compile, "check": cmd_check, "exec": cmd_exec}


def main(argv: Optional[List[str]] = None, out: TextIO = sys.stdout,
         err: TextIO = sys.stderr) -> int:
    args = build_parser().parse_args(argv)
    try:
        COMMANDS[args.command](args, out)
    except (SQLError, IOProblem) as e:
        (out if args.command == "check" else err).write(e.render() + "\n")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())