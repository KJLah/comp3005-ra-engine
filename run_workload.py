#!/usr/bin/env python3
"""Run a query workload through the integrated engine and report it (Section 6).

    python3 run_workload.py --db DIR --queries DIR_OR_FILE [--frames 32]
                            [--repeat 3] [--out results/final]

--queries is a directory of .sql files (one query each, run in name order)
or one .sql file holding several queries separated by ';' (named q01, q02,
... in file order). Statements that are not queries (SET, ANALYZE, LOAD,
CREATE, INSERT) are run too, but only queries get a report line.

For each query the report gives the result row count, the page I/O (reads
and writes over the whole plan), the wall time (compile + plan + execute,
the median of --repeat runs), and Module 4's estimated rows at the root
against the actual rows, with the q-error. A query that fails is reported
with its error and the run carries on.

Written to --out:
    summary.md      the table for the report
    summary.csv     the same numbers, one row per query
    rows/<q>.csv    each query's result, header first, for compare with
                    PostgreSQL (pg_tools.py compare)
    plans/<q>.json  each query's physical plan
"""
from __future__ import annotations

import argparse
import csv
import json
import os
import statistics
import sys
from typing import List, Tuple

from execution.schema import REAL
from shell.cli import render
from shell.session import DEFAULT_FRAMES, Session
from shell.split import classify, split_statements

FIELDS = ["query", "rows", "page_reads", "page_writes", "page_io", "wall_ms",
          "compile_ms", "plan_ms", "exec_ms", "est_rows", "actual_rows", "q_error", "error"]


def workload(path: str) -> List[Tuple[str, str]]:
    """(name, text) for every statement, in run order."""
    if os.path.isdir(path):
        out = []
        for f in sorted(os.listdir(path)):
            if f.endswith(".sql"):
                with open(os.path.join(path, f), encoding="utf-8") as fh:
                    stmts = split_statements(fh.read())
                base = f[:-4]
                for i, s in enumerate(stmts):
                    out.append((base if len(stmts) == 1 else f"{base}_{i + 1}", s))
        return out
    with open(path, encoding="utf-8") as fh:
        stmts = split_statements(fh.read())
    out, q = [], 0
    for s in stmts:
        if _is_query(s):
            q += 1
            out.append((f"q{q:02d}", s))
        else:
            out.append(("", s))
    return out


def _is_query(stmt: str) -> bool:
    cmd = classify(stmt)
    if cmd.kind != "sql":
        return False
    head = stmt.lstrip().split(None, 1)[0].upper() if stmt.strip() else ""
    return head not in ("CREATE", "INSERT")


def cell(v, typ):
    return f"{v:.4f}" if typ == REAL else v


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--db", required=True)
    ap.add_argument("--queries", required=True)
    ap.add_argument("--frames", type=int, default=DEFAULT_FRAMES)
    ap.add_argument("--repeat", type=int, default=1, help="runs per query; wall time is the median")
    ap.add_argument("--out", default="results/final")
    a = ap.parse_args(argv)
    os.makedirs(os.path.join(a.out, "rows"), exist_ok=True)
    os.makedirs(os.path.join(a.out, "plans"), exist_ok=True)

    session = Session(a.db, a.frames, out=None, print_rows=False)
    lines = []
    for name, text in workload(a.queries):
        if not name or not _is_query(text):
            try:
                session.execute(text)          # SET / ANALYZE / LOAD / DDL
            except Exception as e:
                print(f"setup statement failed: {render(e)}", file=sys.stderr)
                return 1
            continue
        try:
            runs = [session.execute(text) for _ in range(max(1, a.repeat))]
        except Exception as e:
            msg = render(e)
            print(f"{name}: {msg}", file=sys.stderr)
            lines.append({"query": name, "error": msg})
            continue
        r = runs[0]
        wall = statistics.median(x.wall_ms for x in runs)
        lines.append({
            "query": name, "rows": len(r.rows), "page_reads": r.page_reads,
            "page_writes": r.page_writes, "page_io": r.io, "wall_ms": round(wall, 1),
            "compile_ms": round(statistics.median(x.compile_ms for x in runs), 1),
            "plan_ms": round(statistics.median(x.plan_ms for x in runs), 1),
            "exec_ms": round(statistics.median(x.exec_ms for x in runs), 1),
            "est_rows": round(r.est_rows, 1), "actual_rows": r.actual_rows,
            "q_error": round(r.q_error, 2), "error": ""})
        with open(os.path.join(a.out, "rows", f"{name}.csv"), "w", newline="",
                  encoding="utf-8") as f:
            w = csv.writer(f)
            w.writerow(r.names)
            for row in r.rows:
                w.writerow([cell(v, t) for v, t in zip(row, r.types)])
        with open(os.path.join(a.out, "plans", f"{name}.json"), "w", encoding="utf-8") as f:
            json.dump(r.plan, f, indent=2)
        print(f"{name}: {len(r.rows)} rows, I/O {r.io}, {wall:.1f} ms, "
              f"est {r.est_rows:.0f} vs actual {r.actual_rows} (q-error {r.q_error:.2f})")

    with open(os.path.join(a.out, "summary.csv"), "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, FIELDS)
        w.writeheader()
        for l in lines:
            w.writerow(l)
    with open(os.path.join(a.out, "summary.md"), "w", encoding="utf-8") as f:
        f.write(f"Database `{a.db}`, B = {a.frames} frames, wall time is the median of "
                f"{max(1, a.repeat)} run(s).\n\n")
        f.write("| query | rows | page I/O (reads + writes) | wall ms | est rows | actual rows "
                "| q-error |\n|---|---:|---:|---:|---:|---:|---:|\n")
        for l in lines:
            if l.get("error"):
                f.write(f"| {l['query']} | error: {l['error']} | | | | | |\n")
            else:
                f.write(f"| {l['query']} | {l['rows']} | {l['page_io']} "
                        f"({l['page_reads']} + {l['page_writes']}) | {l['wall_ms']} | "
                        f"{l['est_rows']:g} | {l['actual_rows']} | {l['q_error']} |\n")
    failed = sum(1 for l in lines if l.get("error"))
    print(f"{len(lines)} queries, {failed} failed; report in {a.out}/summary.md")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())