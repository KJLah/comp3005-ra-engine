"""Command line (Module 2, Section 4).

    exec run   --db DIR --plan plan.json --frames 32 [--json]
    exec trace --db DIR --plan plan.json --frames 32

Without installing, the same commands are `python3 -m execution run ...`.

`run --json` prints {"schema", "rows", "totals"}. totals.tuples_out is the
number of rows the plan returned (the root's tuples_out); comparisons,
page_reads and page_writes are summed over every operator. Rows are printed
as they are pulled from the root, never collected first.

After every run the plan is closed and two things are checked: no frame of
the pool is still pinned, and no temporary file remains (§1.1, §6). Either
one is reported as an error even when the tuples were right.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from typing import Dict, List, Optional, TextIO

from storage.catalog import CATALOG_FILE
from storage.errors import StorageError

from .base import Operator, rows_of
from .context import ExecContext
from .errors import ExecError
from .plan import load_plan, prepare
from .schema import REAL, Row, Schema


def format_value(v, typ: str) -> str:
    """Display form; REAL (AVG) is printed to four decimal places (§2.6)."""
    if typ == REAL:
        return f"{v:.4f}"
    return str(v)


def json_value(v, typ: str) -> str:
    if typ == REAL:
        return f"{v:.4f}"                  # a valid JSON number, 4 decimals
    return json.dumps(v, ensure_ascii=False)


class PlanRun:
    """Open a plan, stream its rows, close it, and check what it left behind."""

    def __init__(self, db: str, plan_text: str, frames: int, policy: str = "lru"):
        if not os.path.isfile(os.path.join(db, CATALOG_FILE)):
            raise ExecError(f"{db}: not a database directory (no {CATALOG_FILE})")
        self.ctx = ExecContext(db, frames, policy)
        self.root: Operator = prepare(load_plan(plan_text), self.ctx)
        self.schema: Schema = self.root.schema

    def rows(self):
        """Generator: open, yield every row, then close and check."""
        try:
            self.root.open(self.ctx.free())
            yield from rows_of(self.root)
        finally:
            self.root.close()
        self.check_clean()

    def check_clean(self) -> None:
        pinned = self.ctx.pool.pinned
        temps = self.ctx.leftover_temps()
        self.ctx.cleanup()
        if pinned:
            raise ExecError(f"plan finished with {pinned} pinned frame(s)")
        if temps:
            raise ExecError(f"plan finished with temporary files left: {', '.join(temps)}")

    def totals(self) -> Dict[str, int]:
        t = {"tuples_out": self.root.c.tuples_out, "comparisons": 0,
             "page_reads": 0, "page_writes": 0}
        for _, op in self.root.walk():
            t["comparisons"] += op.c.comparisons
            t["page_reads"] += op.c.page_reads
            t["page_writes"] += op.c.page_writes
        return t

    def trace_lines(self) -> List[str]:
        nodes = [(depth, op) for depth, op in self.root.walk()]
        width = max(len("  " * d + op.label()) for d, op in nodes) + 2
        out = []
        for d, op in nodes:
            c = op.c
            out.append(f"{('  ' * d + op.label()):<{width}}"
                       f"tuples_out={c.tuples_out:<8}comparisons={c.comparisons:<10}"
                       f"reads={c.page_reads:<8}writes={c.page_writes}")
        return out


def run(args, out: TextIO) -> None:
    pr = PlanRun(args.db, _read(args.plan), args.frames, args.policy)
    types = pr.schema.types()
    if args.json:
        out.write('{"schema": ' + json.dumps(pr.schema.to_json()) + ',\n "rows": [')
        first = True
        for r in pr.rows():
            out.write(("\n  " if first else ",\n  ")
                      + "[" + ",".join(json_value(v, t) for v, t in zip(r, types)) + "]")
            first = False
        out.write(("\n " if not first else "") + '],\n "totals": '
                  + json.dumps(pr.totals()) + "}\n")
    else:
        out.write(" | ".join(pr.schema.names()) + "\n")
        for r in pr.rows():
            out.write(" | ".join(format_value(v, t) for v, t in zip(r, types)) + "\n")
        t = pr.totals()
        out.write(f"({t['tuples_out']} rows; comparisons={t['comparisons']} "
                  f"reads={t['page_reads']} writes={t['page_writes']})\n")


def trace(args, out: TextIO) -> None:
    pr = PlanRun(args.db, _read(args.plan), args.frames, args.policy)
    for _ in pr.rows():
        pass
    for line in pr.trace_lines():
        out.write(line + "\n")
    out.write(f"peak pinned frames={pr.ctx.pool.peak_pinned} of B={args.frames}\n")


def _read(path: str) -> str:
    try:
        with open(path, encoding="utf-8") as f:
            return f.read()
    except OSError as e:
        raise ExecError(f"cannot read plan {path}: {e.strerror}") from None


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="exec", description="Module 2 execution engine")
    sub = p.add_subparsers(dest="command", required=True)
    for name, helptext in (("run", "run a plan and print its rows"),
                           ("trace", "run a plan and print per-operator counters")):
        s = sub.add_parser(name, help=helptext)
        s.add_argument("--db", required=True, help="database directory")
        s.add_argument("--plan", required=True, help="plan JSON file")
        s.add_argument("--frames", type=int, required=True, help="B, the frame budget")
        s.add_argument("--policy", default="lru", choices=("lru", "clock"))
        if name == "run":
            s.add_argument("--json", action="store_true")
    return p


def main(argv: Optional[List[str]] = None, out: TextIO = sys.stdout,
         err: TextIO = sys.stderr) -> int:
    args = build_parser().parse_args(argv)
    try:
        (run if args.command == "run" else trace)(args, out)
    except (ExecError, StorageError) as e:
        err.write(f"error: {e}\n")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
