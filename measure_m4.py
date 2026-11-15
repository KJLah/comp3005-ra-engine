"""Module 4 Section 7 measurements. Builds the gen_m4 database, runs the
twenty queries and writes results/m4/*.csv plus results/m4/report_tables.md
(the tables pasted into REPORT_M4.md).

    python3 measure_m4.py [--frames 8]        (about a minute)

Every plan's answer is checked against the unoptimized plan's (as a bag),
including every plan of the rule ablation. Every actual number comes from running the physical plan through the Module
2 executor (optimizer.runner.run, the same open/next/close as `exec run`),
so any row can be checked with `opt plan ... > p.json; exec trace ...`.
"""
import argparse
import csv
import json
import os
import statistics
import sys
import tempfile
from collections import Counter

import gen_m4
from optimizer.cost import CostModel
from optimizer.estimate import Estimator
from optimizer.explain import label
from optimizer.planner import Planner, naive
from optimizer.runner import preorder, run
from optimizer.stats import load_all

ROOT = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.join(ROOT, "results", "m4")
QDIR = os.path.join(ROOT, "queries", "m4")


def queries(db):
    from sql.cli import compile_script
    from sql.resolve import StorageCatalog
    out = []
    for f in sorted(os.listdir(QDIR)):
        if f.endswith(".sql"):
            with open(os.path.join(QDIR, f), encoding="utf-8") as fh:
                out.append((f[:-4], compile_script(fh.read(), StorageCatalog(db))[0]))
    return out


def qerr(e, a):
    e, a = max(e, 1), max(a, 1)
    return max(e, a) / min(e, a)


def write_csv(name, header, rows):
    with open(os.path.join(OUT, name), "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(header)
        w.writerows(rows)


def md(header, rows):
    out = ["| " + " | ".join(header) + " |", "|" + "---|" * len(header)]
    out += ["| " + " | ".join(str(x) for x in r) + " |" for r in rows]
    return "\n".join(out)


def joins_of(plan):
    short = {"nested_loop_join": "BNL", "sort_merge_join": "SMJ", "hash_join": "hash"}
    return "+".join(short[n["op"]] for n in preorder(plan) if n["op"] in short) or "-"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--frames", type=int, default=8)
    a = ap.parse_args()
    B = a.frames
    os.makedirs(OUT, exist_ok=True)
    tmp = tempfile.TemporaryDirectory()
    db = os.path.join(tmp.name, "m4")
    gen_m4.build(db)
    st = load_all(db)
    qs = queries(db)
    md_out = [f"Database: gen_m4.py (seed 3005), B = {B} unless stated.\n"]

    # ---- 1. estimation quality ------------------------------------------------------
    node_rows, per_query, opt_runs = [], [], {}
    for name, lg in qs:
        P = Planner(st, B)
        plan = P.plan(lg)
        r = run(db, plan, B)
        opt_runs[name] = (plan, r, P)
        errs = []
        for depth_node, c in r.nodes:
            e = P.est.rows(depth_node)
            q = qerr(e, c["tuples_out"])
            errs.append(q)
            node_rows.append([name, label(depth_node), e, c["tuples_out"], round(q, 3)])
        per_query.append([name, len(errs), round(statistics.median(errs), 2), round(max(errs), 2)])
    write_csv("estimates.csv", ["query", "node", "est_rows", "actual_rows", "q_error"], node_rows)
    allq = [r[4] for r in node_rows]
    worst = sorted(node_rows, key=lambda r: -r[4])[:6]
    md_out.append("## 1. Estimation quality\n")
    md_out.append(f"{len(allq)} nodes. Median q-error {statistics.median(allq):.2f}, "
                  f"90th percentile {sorted(allq)[int(0.9 * len(allq))]:.2f}, "
                  f"worst {max(allq):.2f}.\n")
    md_out.append(md(["query", "nodes", "median q-error", "worst q-error"], per_query))
    md_out.append("\nWorst nodes:\n")
    md_out.append(md(["query", "node", "est rows", "actual rows", "q-error"], worst))

    # ---- 2. does optimization pay ---------------------------------------------------
    pay = []
    for name, lg in qs:
        plan, r, P = opt_runs[name]
        un = naive(lg, st, B)
        ru = run(db, un, B)
        assert Counter(ru.rows) == Counter(r.rows), f"{name}: optimized answer differs"
        pay.append([name, joins_of(plan), round(P.cost.total(plan)), r.io, ru.io,
                    round(ru.io / r.io, 2)])
    write_csv("optimized_vs_unoptimized.csv",
              ["query", "joins", "est_cost", "opt_io", "unopt_io", "ratio"], pay)
    ratios = [p[5] for p in pay]
    md_out.append("\n## 2. Optimized against unoptimized\n")
    md_out.append(f"Ratio = unoptimized I/O / optimized I/O. Range {min(ratios)} to "
                  f"{max(ratios)}, geometric mean "
                  f"{statistics.geometric_mean(ratios):.2f}.\n")
    md_out.append(md(["query", "chosen joins", "est cost", "optimized I/O",
                      "unoptimized I/O", "ratio"], pay))

    # ---- 3. which rule mattered -------------------------------------------------------
    total_on = sum(r.io for _, r, _ in opt_runs.values())
    cmp_on = sum(sum(c["comparisons"] for _, c in r.nodes) for _, r, _ in opt_runs.values())
    abl = []
    for rule in range(1, 8):
        total, comps, changed = 0, 0, []
        for name, lg in qs:
            P = Planner(st, B, rules=set(range(1, 8)) - {rule})
            plan = P.plan(lg)
            rr = run(db, plan, B)
            assert Counter(rr.rows) == Counter(opt_runs[name][1].rows), \
                f"{name} without rule {rule}: answer differs"
            io = rr.io
            comps += sum(c["comparisons"] for _, c in rr.nodes)
            total += io
            if io != opt_runs[name][1].io:
                changed.append(f"{name[:3]} {opt_runs[name][1].io}->{io}")
        abl.append([rule, total_on, total, total - total_on, f"{cmp_on:,}", f"{comps:,}",
                    "; ".join(changed) or "none"])
    write_csv("rule_ablation.csv", ["rule_off", "io_all_rules", "io_without", "difference",
                                    "comparisons_all_rules", "comparisons_without",
                                    "queries_changed"], abl)
    md_out.append("\n## 3. Rule ablation (one rule off at a time)\n")
    md_out.append(md(["rule off", "total I/O, all rules", "total I/O without it",
                      "difference", "comparisons, all rules", "comparisons without it",
                      "I/O change per query"], abl))

    # ---- 4. where it breaks -------------------------------------------------------------
    md_out.append("\n## 4. Correlation\n")
    corr = []
    from sql.cli import compile_script
    from sql.resolve import StorageCatalog
    bad = dict(qs)["q17_correlated_join"]
    good = compile_script("SELECT e.name, e.note, w.hours FROM Emp e JOIN Works w "
                          "ON e.eid = w.eid WHERE e.city = 'Ottawa';", StorageCatalog(db))[0]
    for Bc in (4, 6, 8, 32):
        row = [Bc]
        for lg in (bad, good):
            P = Planner(st, Bc)
            plan = P.plan(lg)
            r = run(db, plan, Bc)
            filt = [(n, c) for n, c in r.nodes if n["op"] == "filter"][0]
            row += [P.est.rows(filt[0]), filt[1]["tuples_out"], joins_of(plan),
                    round(P.cost.total(plan)), r.io]
        corr.append(row)
    write_csv("correlation.csv", ["B", "q17_est_rows", "q17_actual_rows", "q17_join",
                                  "q17_est_cost", "q17_io", "city_only_est_rows",
                                  "city_only_actual_rows", "city_only_join",
                                  "city_only_est_cost", "city_only_io"], corr)
    md_out.append(md(["B", "q17 est rows", "actual", "join", "est cost", "actual I/O",
                      "city only: est rows", "actual", "join", "est cost", "actual I/O"], corr))
    q18 = opt_runs["q18_correlated_count"]
    f18 = [(n, c) for n, c in q18[1].nodes if n["op"] == "filter"][0]
    md_out.append(f"\nq18 filter: est {q18[2].est.rows(f18[0])}, actual {f18[1]['tuples_out']}.\n")

    # ---- 5. B changes the algorithm -----------------------------------------------------
    rows = []
    for name, lg in qs:
        algos = []
        for Bc in (3, 4, 8, 32):
            algos.append(joins_of(Planner(st, Bc).plan(lg)))
        if any(x != "-" for x in algos):
            rows.append([name] + algos)
    write_csv("algorithms_by_b.csv", ["query", "B=3", "B=4", "B=8", "B=32"], rows)
    md_out.append("\n## 5. Join algorithms chosen at each B\n")
    md_out.append(md(["query", "B=3", "B=4", "B=8", "B=32"], rows))

    # ---- 6. the four-relation DP table ---------------------------------------------------
    P = Planner(st, B)
    P.plan(dict(qs)["q10_four_way"])
    md_out.append(f"\n## 6. DP table, q10 (four relations), B = {B}\n")
    md_out.append("```\n" + P.dps[0].table() + "\n```")

    with open(os.path.join(OUT, "report_tables.md"), "w") as f:
        f.write("\n".join(md_out) + "\n")
    print("\n".join(md_out))
    tmp.cleanup()


if __name__ == "__main__":
    sys.exit(main())