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
        Numbers are compared after rounding to 4 decimals, since AVG is
        printed with 4 decimals by us and with more by PostgreSQL. Column
        names are not compared (PostgreSQL drops the e. prefix).

Our string order is byte order. PostgreSQL's default collation is not, so
pg/schema.sql declares every VARCHAR column COLLATE "C"; otherwise a
comparison such as city < 'L' or an ORDER BY on text could differ.
Query names follow run_workload.py: q01, q02, ... in file order.
"""
from __future__ import annotations

import argparse
import csv
import os
import sqlite3
import sys
from collections import Counter
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


def cmd_sqlite(a) -> None:
    con = sqlite3.connect(":memory:")
    for info, rows in _tables(a.db):
        con.execute(_ddl(info, collate=False))
        marks = ",".join("?" * len(info.columns))
        con.executemany(f"INSERT INTO {info.name.lower()} VALUES ({marks})", rows)
    os.makedirs(os.path.join(a.out, "rows"), exist_ok=True)
    for name, text in _queries(a.queries):
        try:
            cur = con.execute(text.strip().rstrip(";"))
        except sqlite3.Error as e:
            print(f"{name}: sqlite error: {e}")
            continue
        with open(os.path.join(a.out, "rows", f"{name}.csv"), "w", newline="",
                  encoding="utf-8") as f:
            w = csv.writer(f)
            w.writerow([d[0] for d in cur.description])
            w.writerows(cur.fetchall())
    print(f"wrote {a.out}/rows")


def _norm(v: str) -> str:
    try:
        x = float(v)
    except ValueError:
        return v
    return f"{round(x, 4):.4f}"


def _read(path: str):
    with open(path, newline="", encoding="utf-8") as f:
        rows = list(csv.reader(f))
    return [tuple(_norm(v) for v in r) for r in rows[1:]]


def cmd_compare(a) -> int:
    ours = sorted(f for f in os.listdir(a.ours) if f.endswith(".csv"))
    bad = 0
    for f in ours:
        ref = os.path.join(a.ref, f)
        name = f[:-4]
        if not os.path.exists(ref):
            print(f"{name}: no reference answer")
            bad += 1
            continue
        x, y = _read(os.path.join(a.ours, f)), _read(ref)
        if Counter(x) == Counter(y):
            order = "same order" if x == y else "different order"
            print(f"{name}: match ({len(x)} rows, {order})")
        else:
            bad += 1
            only_x = list((Counter(x) - Counter(y)).elements())[:3]
            only_y = list((Counter(y) - Counter(x)).elements())[:3]
            print(f"{name}: DIFFERENT: ours {len(x)} rows, reference {len(y)} rows; "
                  f"only ours e.g. {only_x}; only reference e.g. {only_y}")
    print(f"{len(ours) - bad} of {len(ours)} match")
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
    a = ap.parse_args(argv)
    if a.cmd == "export":
        cmd_export(a)
    elif a.cmd == "sqlite":
        cmd_sqlite(a)
    else:
        return cmd_compare(a)
    return 0


if __name__ == "__main__":
    sys.exit(main())