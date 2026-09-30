#!/usr/bin/env python3
"""Command-line entry point.

    python ra.py --tree "project[Name](select[Age>30](employeeTable1))"
    python ra.py -d data/employees.ra "project[Name](employeeTable1)"
    python ra.py -d data/employees.ra --stats "employeeTable2 join[employeeTable2.DID=departmentTable.DID] departmentTable"
    python ra.py -d data/employees.ra            (interactive mode)

A Python stack trace is never shown. Language errors (RAError) are printed
with their category and position. Anything unexpected is reported as an
internal error in one line.
"""
from __future__ import annotations

import argparse
import os
import sys
import time

from ra_engine.errors import RAError
from ra_engine.loader import load_file
from ra_engine.operators import evaluate, plan
from ra_engine.parser import parse_query
from ra_engine.printer import format_tree
from ra_engine.relation import format_relation


def run_one(query: str, catalog, tree_only: bool, stats: bool) -> int:
    try:
        tree = parse_query(query)
        if tree_only:
            print(format_tree(tree))
            return 0
        op = plan(tree, catalog)             # all static checks happen here
        start = time.perf_counter()
        result = evaluate(op)
        elapsed = time.perf_counter() - start
    except RAError as e:
        print(e.attach_source(query, "query").render())
        return 1

    print(format_relation(result))
    if stats:
        for label, value in op.counters():
            print(f"  {label}: {value}")
        print(f"  wall time: {elapsed:.6f} s")
    return 0


def repl(catalog, stats: bool) -> int:
    print("Relational algebra shell. Relations: " + (", ".join(sorted(catalog)) or "none"))
    print("Type a query, e.g.  project[Name](employeeTable1)")
    print("Put .tree in front of a query to see its parse tree, e.g.  .tree A union B minus C")
    print("Type .quit to leave.")
    while True:
        try:
            line = input("ra> ").strip()
        except EOFError:
            print()
            return 0
        if not line:
            continue
        if line in (".quit", ".exit"):
            return 0
        if line.startswith(".tree "):
            run_one(line[len(".tree "):], catalog, True, stats)
        else:
            run_one(line, catalog, False, stats)


def run(argv) -> int:
    ap = argparse.ArgumentParser(description="Relational algebra query processor")
    ap.add_argument("query", nargs="?",
                    help="the query (quote it in the shell); omit for interactive mode")
    ap.add_argument("--tree", action="store_true",
                    help="print the parse tree and do not execute")
    ap.add_argument("-d", "--data", action="append", default=[], metavar="FILE",
                    help="relation definition file (repeatable)")
    ap.add_argument("--stats", action="store_true",
                    help="print operator counters and wall time after the result")
    args = ap.parse_args(argv)

    catalog = {}
    if not args.tree:
        try:
            for path in args.data:
                load_file(path, catalog)
        except RAError as e:
            print(e.render())
            return 1

    if args.query is None:
        return repl(catalog, args.stats)
    return run_one(args.query, catalog, args.tree, args.stats)


def main() -> None:
    try:
        sys.exit(run(sys.argv[1:]))
    except KeyboardInterrupt:
        print("\ninterrupted")
        sys.exit(130)
    except BrokenPipeError:
        # the reader went away (e.g. output piped into `head`): stop quietly
        devnull = os.open(os.devnull, os.O_WRONLY)
        os.dup2(devnull, sys.stdout.fileno())
        sys.exit(1)
    except RecursionError:
        print("Error: the query is nested too deeply to process")
        sys.exit(1)
    except Exception as e:  # last line of defence: never show a stack trace
        print(f"Internal error: {type(e).__name__}: {e}")
        sys.exit(2)


if __name__ == "__main__":
    main()
