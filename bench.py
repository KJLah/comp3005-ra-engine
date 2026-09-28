#!/usr/bin/env python3
"""Performance study driver (sections 8.2–8.4).

    python bench.py                  # full run: 1000 .. 64000 (takes a while)
    python bench.py --max 8000       # quick run for testing

For each size it:
  1. writes R and S with gen_data.py (match rate 1),
  2. loads them with our own loader,
  3. runs  R join[R.b=S.b] S,  select[b<K](R)  and  project[b](R),
  4. records the counters the operators report and the wall time of execute()
     only. Parsing, planning and loading are not timed.

Results go to results/: CSV files, tables.md (ready to paste into REPORT.md),
machine.txt and, if matplotlib is installed, the plots. Every number in
REPORT.md should come from these files.
"""
from __future__ import annotations

import argparse
import csv
import math
import os
import platform
import statistics
import sys
import time

from gen_data import write_files
from ra_engine.loader import load_file
from ra_engine.operators import plan
from ra_engine.parser import parse_query

SIZES = [1000, 2000, 4000, 8000, 16000, 32000, 64000]
MATCH_RATES = [0.01, 0.1, 1, 10, 100, 500]
MATCH_N = 2000


def load(n, m, match, gen_dir):
    r_path, s_path = write_files(n, m, match, gen_dir)
    cat = {}
    load_file(r_path, cat)
    load_file(s_path, cat)
    return cat


def timed(query, cat, repeats):
    """Run query `repeats` times. Return (median seconds, operator, row count)."""
    times = []
    for _ in range(repeats):
        op = plan(parse_query(query), cat)       # fresh operator, so counters reset
        start = time.perf_counter()
        out = op.execute()
        times.append(time.perf_counter() - start)
    return statistics.median(times), op, len(out)


def slope(xs, ys):
    """Least-squares slope of log(y) against log(x), computed by hand."""
    lx = [math.log(x) for x in xs]
    ly = [math.log(y) for y in ys]
    mx, my = sum(lx) / len(lx), sum(ly) / len(ly)
    num = sum((a - mx) * (b - my) for a, b in zip(lx, ly))
    den = sum((a - mx) ** 2 for a in lx)
    return num / den


def machine_info():
    return (f"platform: {platform.platform()}\n"
            f"machine: {platform.machine()}\n"
            f"processor: {platform.processor() or 'unknown'}\n"
            f"python: {platform.python_implementation()} {platform.python_version()}\n")


def write_csv(path, header, rows):
    with open(path, "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(header)
        w.writerows(rows)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--max", type=int, default=SIZES[-1], help="largest n to run")
    ap.add_argument("--out", default="results")
    ap.add_argument("--gen", default="data/generated")
    ap.add_argument("--skip-match", action="store_true")
    args = ap.parse_args()
    os.makedirs(args.out, exist_ok=True)
    sizes = [n for n in SIZES if n <= args.max]

    with open(os.path.join(args.out, "machine.txt"), "w") as f:
        f.write(machine_info())
    print(machine_info())

    join_rows, unary_rows = [], []
    for n in sizes:
        cat = load(n, n, 1, args.gen)
        # the join takes minutes at 32000+, so it is run once there
        repeats = 3 if n <= 16000 else 1
        t, op, size = timed("R join[R.b=S.b] S", cat, repeats)
        join_rows.append([n, n, op.comparisons, f"{t:.6f}", size])
        print(f"join     n=m={n:>6}  comparisons={op.comparisons:>12}  "
              f"time={t:10.4f}s  output={size}", flush=True)

        # select keeps about half of R (b is uniform in 0..n-1)
        ts, sop, ssize = timed(f"select[b<{n // 2}](R)", cat, 5)
        tp, _, psize = timed("project[b](R)", cat, 5)
        unary_rows.append([n, sop.evaluations, f"{ts:.6f}", ssize, f"{tp:.6f}", psize])
        print(f"select   n={n:>6}  evaluated={sop.evaluations:>8}  time={ts:.6f}s  "
              f"output={ssize}   project time={tp:.6f}s output={psize}", flush=True)

    write_csv(os.path.join(args.out, "join.csv"),
              ["n", "m", "comparisons", "wall_time_s", "output_tuples"], join_rows)
    write_csv(os.path.join(args.out, "unary.csv"),
              ["n", "select_evaluated", "select_time_s", "select_output",
               "project_time_s", "project_output"], unary_rows)

    match_rows = []
    if not args.skip_match:
        for k in MATCH_RATES:
            cat = load(MATCH_N, MATCH_N, k, args.gen)
            t, op, size = timed("R join[R.b=S.b] S", cat, 3)
            match_rows.append([k, MATCH_N, MATCH_N, op.comparisons, f"{t:.6f}", size])
            print(f"match k={k:<6} comparisons={op.comparisons}  time={t:.4f}s  "
                  f"output={size}", flush=True)
        write_csv(os.path.join(args.out, "match.csv"),
                  ["match_rate", "n", "m", "comparisons", "wall_time_s", "output_tuples"],
                  match_rows)

    # ---- markdown tables and derived numbers --------------------------------------
    ns = [r[0] for r in join_rows]
    jt = [float(r[3]) for r in join_rows]
    lines = ["## Join: R join[R.b=S.b] S (match rate 1)", "",
             "| n | m | comparisons | n*m | wall time (s) | output tuples |",
             "|---|---|---|---|---|---|"]
    for n, m, c, t, o in join_rows:
        lines.append(f"| {n} | {m} | {c} | {n * m} | {t} | {o} |")
    lines += ["", "## select[b<n/2](R) and project[b](R)", "",
              "| n | select tuples evaluated | select time (s) | select output "
              "| project time (s) | project output |",
              "|---|---|---|---|---|---|"]
    for row in unary_rows:
        lines.append("| " + " | ".join(str(v) for v in row) + " |")
    if match_rows:
        lines += ["", f"## Match rate (n = m = {MATCH_N})", "",
                  "| match rate | comparisons | wall time (s) | output tuples |",
                  "|---|---|---|---|"]
        for k, _, _, c, t, o in match_rows:
            lines.append(f"| {k} | {c} | {t} | {o} |")
    if len(ns) >= 2:
        s_join = slope(ns, jt)
        s_sel = slope(ns, [float(r[2]) for r in unary_rows])
        s_proj = slope(ns, [float(r[4]) for r in unary_rows])
        per_pair = jt[-1] / (ns[-1] * ns[-1])
        predict = per_pair * 1_000_000 ** 2
        lines += ["", "## Derived numbers", "",
                  f"* log-log slope, join time vs n: {s_join:.3f}",
                  f"* log-log slope, select time vs n: {s_sel:.3f}",
                  f"* log-log slope, project time vs n: {s_proj:.3f}",
                  f"* time per comparison at n = {ns[-1]}: {per_pair * 1e9:.1f} ns",
                  f"* predicted join time at n = m = 1,000,000: "
                  f"{per_pair:.3e} s x 1e12 = {predict:,.0f} s "
                  f"= {predict / 3600:.1f} h"]
    with open(os.path.join(args.out, "tables.md"), "w") as f:
        f.write("\n".join(lines) + "\n")
    print("\n".join(lines))

    try:
        make_plots(args.out, join_rows, unary_rows)
    except ImportError:
        print("\nmatplotlib is not installed, so no plots were made "
              "(pip install matplotlib)")


def make_plots(out_dir, join_rows, unary_rows):
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    ns = [r[0] for r in join_rows]
    fig, ax = plt.subplots(figsize=(6, 4.5))
    ax.loglog(ns, [float(r[3]) for r in join_rows], "o-", label="join (nested loop)")
    ax.loglog(ns, [float(r[2]) for r in unary_rows], "s-", label="select")
    ax.loglog(ns, [float(r[4]) for r in unary_rows], "^-", label="project")
    ax.set_xlabel("n = m (tuples per relation)")
    ax.set_ylabel("wall time (s)")
    ax.set_title("Wall time vs input size (log-log)")
    ax.grid(True, which="both", alpha=0.3)
    ax.legend()
    fig.tight_layout()
    fig.savefig(os.path.join(out_dir, "loglog.png"), dpi=150)
    print(f"\nwrote {os.path.join(out_dir, 'loglog.png')}")


if __name__ == "__main__":
    sys.exit(main())
