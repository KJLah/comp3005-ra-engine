#!/usr/bin/env python3
"""Checking our answers against a reference database (Section 6).

The demo checks correctness by running the same queries on PostgreSQL over
the same data. These commands prepare that run and compare the answers.

    python3 pg_tools.py export  --db DIR --out pg
        Writes pg/schema.sql (CREATE TABLE in PostgreSQL types), one CSV per
        table read back from our heap files (so PostgreSQL gets exactly the
        rows we hold), pg/load.sql (\\copy each CSV in) and pg/run.sh, which
        runs every query of a workload through psql into pg/rows/<q>.csv.

    python3 pg_tools.py sqlite  --db DIR --queries workload.sql --out ref
        The same check offline: loads our rows into SQLite (Python's own
        sqlite3) and writes ref/rows/<q>.csv. Useful before the demo when
        PostgreSQL is not at hand.

    python3 pg_tools.py compare --ours results/final/rows --ref pg/rows
        Compares every query's answer as a bag of rows (duplicates count).
        Output types are read from schemas/<q>.json next to summary.csv.
        Strings and integers compare exactly; only REAL uses four-decimal
        display rounding. The nonempty query set must match summary.csv.
        Column names are not compared (PostgreSQL drops the e. prefix).
        Add --ordered q01,q02 for queries requiring exact sequence equality;
        use this only when ORDER BY ties cannot permit a different sequence.

Our string order is byte order. PostgreSQL's default collation is not, so
pg/schema.sql declares every VARCHAR column COLLATE "C"; otherwise a
comparison such as city < 'L' or an ORDER BY on text could differ.
Query names follow run_workload.py: q01, q02, ... in file order.
"""
from __future__ import annotations

import argparse
import csv
import json
import os
import sqlite3
import sys
from collections import Counter
from decimal import Decimal, InvalidOperation
from typing import Dict, List

from storage.buffer import BufferPool
from storage.catalog import Catalog
from storage.record import INT, decode


def _tables(db: str):
    cat = Catalog(db, BufferPool(16))
    for info in cat.tables():
        rows = [decode(info.columns, rec) for _, rec in cat.open_heap(info.name).scan()]
        yield info, rows


def _ddl(info, collate: bool) -> str:
    cols = []
    for c in info.columns:
        if c.type == INT:
            cols.append(f"{c.name.lower()} INTEGER")
        else:
            cols.append(f"{c.name.lower()} VARCHAR({c.length})" + (' COLLATE "C"' if collate else ""))
    return f"CREATE TABLE {info.name.lower()} ({', '.join(cols)});"


def _queries(path: str) -> List[tuple]:
    from run_workload import workload, _is_query
    return [(n, s) for n, s in workload(path) if n and _is_query(s)]


def cmd_export(a) -> None:
    os.makedirs(os.path.join(a.out, "data"), exist_ok=True)
    schema, load = [], []
    for info, rows in _tables(a.db):
        schema.append(f"DROP TABLE IF EXISTS {info.name.lower()};")
        schema.append(_ddl(info, collate=True))
        path = os.path.join("data", f"{info.name}.csv")
        with open(os.path.join(a.out, path), "w", newline="", encoding="utf-8") as f:
            csv.writer(f, quoting=csv.QUOTE_ALL).writerows(rows)
        load.append(f"\\copy {info.name.lower()} FROM '{path}' WITH (FORMAT csv)")
        load.append(f"ANALYZE {info.name.lower()};")
        print(f"exported {info.name}: {len(rows)} rows")
    with open(os.path.join(a.out, "schema.sql"), "w", encoding="utf-8") as f:
        f.write("\n".join(schema) + "\n")
    with open(os.path.join(a.out, "load.sql"), "w", encoding="utf-8") as f:
        f.write("\n".join(load) + "\n")
    lines = [
        "#!/bin/sh",
        "set -eu",
        "# usage: cd pg && sh run.sh DBNAME",
        'DB="${1:?give the PostgreSQL database name}"',
        "mkdir -p rows",
    ]
    if a.queries:
        os.makedirs(os.path.join(a.out, "queries"), exist_ok=True)
        for name, text in _queries(a.queries):
            with open(os.path.join(a.out, "queries", f"{name}.sql"), "w", encoding="utf-8") as f:
                f.write(text.strip() + "\n")
            lines.append(
                f'psql -X -q -v ON_ERROR_STOP=1 -d "$DB" '
                f'--csv -f queries/{name}.sql -o rows/{name}.csv'
            )
    with open(os.path.join(a.out, "run.sh"), "w", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")
    print(f"wrote {a.out}/schema.sql, load.sql and run.sh")


def cmd_sqlite(a) -> int:
    con = sqlite3.connect(":memory:")

    for info, rows in _tables(a.db):
        con.execute(_ddl(info, collate=False))
        marks = ",".join("?" * len(info.columns))
        con.executemany(
            f"INSERT INTO {info.name.lower()} VALUES ({marks})",
            rows,
        )

    os.makedirs(os.path.join(a.out, "rows"), exist_ok=True)
    failed = 0

    for name, text in _queries(a.queries):
        path = os.path.join(a.out, "rows", f"{name}.csv")
        if os.path.exists(path):
            os.remove(path)
        try:
            cur = con.execute(text.strip().rstrip(";"))
        except sqlite3.Error as e:
            print(f"{name}: sqlite error: {e}")
            failed += 1
            continue

        path = os.path.join(a.out, "rows", f"{name}.csv")
        with open(path, "w", newline="", encoding="utf-8") as f:
            w = csv.writer(f)
            w.writerow([d[0] for d in cur.description])
            w.writerows(cur.fetchall())

    con.close()
    print(f"wrote {a.out}/rows; {failed} query error(s)")
    return 1 if failed else 0


def _norm(v: str, typ: str):
    if typ == "VARCHAR":
        return v
    if typ not in ("INT", "BIGINT", "REAL"):
        raise ValueError(f"unsupported result type {typ!r}")
    value = Decimal(v)
    if not value.is_finite():
        raise ValueError(f"non-finite numeric result {v!r}")
    if typ in ("INT", "BIGINT"):
        if value != value.to_integral_value():
            raise ValueError(f"non-integral result {v!r} for {typ}")
        return int(value)
    # REAL is the executor's floating-point AVG. Use its display rounding
    # for both sides, without ever converting INT/BIGINT or VARCHAR.
    rounded = Decimal(format(float(value), ".4f"))
    if not rounded.is_finite():
        raise ValueError(f"REAL result is out of range: {v!r}")
    return rounded


def _read(path: str, types):
    with open(path, newline="", encoding="utf-8") as f:
        rows = list(csv.reader(f))
    if not rows or len(rows[0]) != len(types):
        raise ValueError(f"{path}: missing header or wrong column count")
    result = []
    for line, row in enumerate(rows[1:], 2):
        if len(row) != len(types):
            raise ValueError(f"{path}: wrong field count at CSV record {line}")
        result.append(tuple(_norm(v, t) for v, t in zip(row, types)))
    return result


def cmd_compare(a) -> int:
    try:
        report_dir = os.path.dirname(os.path.abspath(a.ours))
        with open(os.path.join(report_dir, "summary.csv"), newline="",
                  encoding="utf-8") as f:
            report = list(csv.DictReader(f))
        names = [row["query"] for row in report]
        if not names or len(set(names)) != len(names):
            raise ValueError("summary.csv must list a nonempty, unique query set")
        failed = [row["query"] for row in report if row.get("error")]
        if failed:
            raise ValueError("workload queries failed: " + ", ".join(failed))
        expected = {name + ".csv" for name in names}
        for directory, label in ((a.ours, "ours"), (a.ref, "reference")):
            actual = {f for f in os.listdir(directory) if f.endswith(".csv")}
            if actual != expected:
                raise ValueError(
                    f"{label} query files differ from summary.csv: "
                    f"missing={sorted(expected - actual)}, "
                    f"extra={sorted(actual - expected)}"
                )
        ordered = {n.strip() for n in getattr(a, "ordered", "").split(",") if n.strip()}
        if not ordered <= set(names):
            raise ValueError("--ordered contains unknown query names")
    except (OSError, ValueError, KeyError, csv.Error) as e:
        print(f"comparison failed: {e}")
        return 1

    bad = 0
    for row in report:
        name = row["query"]
        try:
            with open(os.path.join(report_dir, "schemas", name + ".json"),
                      encoding="utf-8") as f:
                types = json.load(f)
            if not isinstance(types, list) or not types or any(
                t not in ("VARCHAR", "INT", "BIGINT", "REAL") for t in types
            ):
                raise ValueError("invalid output schema")
            x = _read(os.path.join(a.ours, name + ".csv"), types)
            y = _read(os.path.join(a.ref, name + ".csv"), types)
            if len(x) != int(row["rows"]) or len(x) != int(row["actual_rows"]):
                raise ValueError("our row file disagrees with summary.csv")
            if Counter(x) != Counter(y):
                raise ValueError(f"different values: ours {len(x)} rows, reference {len(y)} rows")
            if name in ordered and x != y:
                raise ValueError("same bag of rows, but incorrect required order")
        except (OSError, ValueError, KeyError, InvalidOperation, csv.Error) as e:
            bad += 1
            print(f"{name}: DIFFERENT or invalid: {e}")
            continue
        order = "same order" if x == y else "different order"
        print(f"{name}: match ({len(x)} rows, {order})")
    print(f"{len(names) - bad} of {len(names)} match")
    return 1 if bad else 0


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="reference-database checks")
    sub = ap.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("export")
    s.add_argument("--db", required=True)
    s.add_argument("--out", default="pg")
    s.add_argument("--queries", help="workload whose queries run.sh should run")
    s = sub.add_parser("sqlite")
    s.add_argument("--db", required=True)
    s.add_argument("--queries", required=True)
    s.add_argument("--out", default="ref")
    s = sub.add_parser("compare")
    s.add_argument("--ours", required=True)
    s.add_argument("--ref", required=True)
    s.add_argument("--ordered", default="",
                   help="comma-separated query names requiring exact row order; use when ORDER BY has no ties")
    a = ap.parse_args(argv)
    if a.cmd == "export":
        cmd_export(a)
    elif a.cmd == "sqlite":
        return cmd_sqlite(a)
    else:
        return cmd_compare(a)
    return 0


if __name__ == "__main__":
    sys.exit(main())