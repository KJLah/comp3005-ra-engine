"""Command line (Module 4, Section 6).

    opt analyze  --db DIR --table R [--buckets 20]
    opt plan     --db DIR --logical q.json --frames 32 --json    physical plan
    opt explain  --db DIR --logical q.json --frames 32 [--rewrites]
    opt dp-table --db DIR --logical q.json --frames 32

Without installing, `python3 -m optimizer ...` is the same command.

--logical takes a Module 3 logical plan (the output of `sql compile --json`).
--sql FILE may be given instead: the SQL is compiled with Module 3 first.
--off 2,6 switches rewrite rules off (for the rule ablation in the report).
explain --rewrites also prints the logical tree before and after the rewrite
rules, side by side (Section 3). analyze without --table analyzes every table.
Errors are printed as `error: <message>` on standard error, exit code 1.
"""
from __future__ import annotations

import argparse
import json
import sys
from typing import List, Optional, TextIO

from .explain import explain, side_by_side
from .planner import Planner
from .rewrite import ALL_RULES
from .stats import DEFAULT_BUCKETS, analyze, load_all


class OptError(Exception):
    pass


def _logical(args) -> dict:
    if args.sql:
        from sql.cli import compile_script
        from sql.resolve import StorageCatalog
        with open(args.sql, encoding="utf-8") as f:
            plans = compile_script(f.read(), StorageCatalog(args.db))
        if len(plans) != 1:
            raise OptError(f"{args.sql} must hold exactly one query, found {len(plans)}")
        return plans[0]
    if not args.logical:
        raise OptError("give --logical q.json (or --sql q.sql)")
    with open(args.logical, encoding="utf-8") as f:
        return json.load(f)


def _rules(args):
    off = set()
    if args.off:
        for part in args.off.split(","):
            if not part.strip().isdigit() or int(part) not in ALL_RULES:
                raise OptError(f"--off takes rule numbers 1-7, got {part!r}")
            off.add(int(part))
    return ALL_RULES - off


def _planner(args):
    if args.frames < 3:
        raise OptError("--frames must be at least 3")
    p = Planner(load_all(args.db), args.frames, rules=_rules(args))
    logical = _logical(args)
    return p, logical, p.plan(logical)


def cmd_analyze(args, out: TextIO) -> None:
    from storage.buffer import BufferPool
    from storage.catalog import Catalog
    names = [args.table] if args.table else [t.name for t in Catalog(args.db, BufferPool(2)).tables()]
    for name in names:
        ts = analyze(args.db, name, args.buckets)
        out.write(f"analyzed {name}: n_tuples={ts.n_tuples} n_pages={ts.n_pages} "
                  f"width={ts.width:g}\n")
        for c, cs in ts.cols.items():
            out.write(f"  {c}: ndv={cs.ndv} min={cs.min!r} max={cs.max!r} "
                      f"buckets={len(cs.hist)}\n")


def cmd_plan(args, out: TextIO) -> None:
    _, _, phys = _planner(args)
    out.write(json.dumps(phys, indent=2) + "\n")


def cmd_explain(args, out: TextIO) -> None:
    p, logical, phys = _planner(args)
    if args.rewrites:
        out.write(side_by_side(logical, p.rewritten) + "\n")
        out.write(f"rules fired: {', '.join(map(str, sorted(p.fired))) or 'none'}\n\n")
    out.write(explain(phys, p.cost) + "\n")


def cmd_dp_table(args, out: TextIO) -> None:
    p, _, _ = _planner(args)
    if not p.dps:
        out.write("no join in this query: nothing to enumerate\n")
    for i, dp in enumerate(p.dps):
        if i:
            out.write("\n")
        out.write(dp.table() + "\n")


def build_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(prog="opt", description="Module 4 query optimizer")
    sub = ap.add_subparsers(dest="command", required=True)
    s = sub.add_parser("analyze", help="compute statistics for a table")
    s.add_argument("--db", "--catalog", dest="db", default=".")
    s.add_argument("--table")
    s.add_argument("--buckets", type=int, default=DEFAULT_BUCKETS)
    for name, extra in (("plan", True), ("explain", False), ("dp-table", False)):
        s = sub.add_parser(name)
        s.add_argument("--db", "--catalog", dest="db", default=".")
        s.add_argument("--logical")
        s.add_argument("--sql")
        s.add_argument("--frames", type=int, default=32)
        s.add_argument("--off", help="rewrite rules to switch off, e.g. 2,6")
        if name == "plan":
            s.add_argument("--json", action="store_true", help="(the default output)")
        if name == "explain":
            s.add_argument("--rewrites", action="store_true")
    return ap


COMMANDS = {"analyze": cmd_analyze, "plan": cmd_plan, "explain": cmd_explain,
            "dp-table": cmd_dp_table}


def main(argv: Optional[List[str]] = None, out: TextIO = sys.stdout,
         err: TextIO = sys.stderr) -> int:
    args = build_parser().parse_args(argv)
    try:
        COMMANDS[args.command](args, out)
    except (OptError, OSError, ValueError, KeyError) as e:
        msg = e.args[0] if isinstance(e, KeyError) and e.args else e
        err.write(f"error: {msg}\n")
        return 1
    except Exception as e:                      # storage / sql / execution errors
        render = getattr(e, "render", None)
        err.write((render() if render else f"error: {e}") + "\n")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())