"""Module 2 section 5 measurements. Writes results/m2/*.csv and *.png and
prints the Markdown tables for REPORT_M2.md.

    python3 measure_m2.py              # everything (a few minutes)
    python3 measure_m2.py --plot-only  # redraw the plots from the CSVs

Every number comes from running a plan through the same code `exec run` uses
(execution.plan.prepare + open/next/close), so any row can be checked with
`exec trace`. Page counts M and N are data pages (the heap's directory page
is not counted in M or N, but its read is in the measured numbers).
"""
import argparse
import csv
import math
import os
import random
import sys
import tempfile

from storage.buffer import BufferPool
from storage.catalog import Catalog
from storage.csvload import load_csv
from storage.record import parse_schema

from execution.base import rows_of
from execution.context import ExecContext
from execution.plan import prepare

ROOT = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.join(ROOT, "results", "m2")
JOINS = ["nested_loop_join", "sort_merge_join", "hash_join"]
SHORT = {"nested_loop_join": "BNL", "sort_merge_join": "SMJ", "hash_join": "Hash"}
PAD = "p" * 200        # 4 + 4 + 2 + 200 = 210-byte records -> 19 per page
PER_PAGE = 19
KEYS = 1000            # join key domain


# ---- data -------------------------------------------------------------------------
class DB:
    def __init__(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.dir = self.tmp.name
        self.cat = Catalog(self.dir, BufferPool(16))

    def table(self, name, pages, keyfn, seed):
        rnd = random.Random(seed)
        n = pages * PER_PAGE
        rows = [f"{i},{keyfn(i, rnd)},{PAD}" for i in range(n)]
        rnd.shuffle(rows)
        self.cat.create_table(name, parse_schema("id:INT,k:INT,pad:VARCHAR(200)"))
        load_csv(self.cat, name, rows)
        self.cat.flush()
        return self.cat.get(name).num_pages - 1        # data pages

    def run(self, plan, frames):
        ctx = ExecContext(self.dir, frames)
        root = prepare(plan, ctx)
        n = 0
        try:
            root.open(ctx.free())
            for _ in rows_of(root):
                n += 1
        finally:
            root.close()
        assert ctx.pool.pinned == 0 and not ctx.leftover_temps()
        ctx.cleanup()
        t = {"rows": n, "reads": 0, "writes": 0, "comparisons": 0,
             "peak_pinned": ctx.pool.peak_pinned, "peak_reserved": ctx.peak_reserved}
        for _, op in root.walk():
            t["reads"] += op.c.page_reads
            t["writes"] += op.c.page_writes
            t["comparisons"] += op.c.comparisons
        return t, root


def scan(t):
    return {"op": "seqscan", "table": t}


def join(op, l, r):
    return {"op": op, "condition": f"{l}.k = {r}.k", "left": scan(l), "right": scan(r)}


def uniform(i, rnd):
    return rnd.randrange(KEYS)


# ---- formulas (section 2, with the handout's B) ------------------------------------
def passes(x, b):
    r0 = math.ceil(x / b)
    return 1 if r0 <= 1 else 1 + math.ceil(math.log(r0) / math.log(b - 1))


def sort_io(x, b):
    """External sort that writes its final run (as a join input): 2X per pass."""
    return 2 * x * passes(x, b)


def formula(op, m, n, b):
    """(reads, writes) predicted by section 2."""
    if op == "nested_loop_join":
        return m + math.ceil(m / (b - 2)) * n, 0
    if op == "sort_merge_join":
        pm, pn = passes(m, b), passes(n, b)
        return m * pm + n * pn + m + n, m * pm + n * pn
    return 2 * (m + n), m + n


def write_csv(name, rows):
    os.makedirs(OUT, exist_ok=True)
    with open(os.path.join(OUT, name), "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=list(rows[0]))
        w.writeheader()
        w.writerows(rows)


def read_csv(name):
    with open(os.path.join(OUT, name)) as f:
        return list(csv.DictReader(f))


# ---- 1. cost against formula ---------------------------------------------------------
def cost_vs_formula(db, sizes):
    rows = []
    print("\n### Cost against formula (reads / writes)\n")
    print("| Join | M | N | B | formula R | measured R | formula W | measured W | comparisons |")
    print("|---|---|---|---|---|---|---|---|---|")
    for (l, r, m, n) in sizes:
        for b in (4, 16, 64, 256):
            for op in JOINS:
                t, root = db.run(join(op, l, r), b)
                fr, fw = formula(op, m, n, b)
                rows.append({"join": op, "M": m, "N": n, "B": b, "formula_reads": fr,
                             "reads": t["reads"], "formula_writes": fw,
                             "writes": t["writes"], "comparisons": t["comparisons"],
                             "rows": t["rows"], "peak_pinned": t["peak_pinned"]})
                print(f"| {SHORT[op]} | {m} | {n} | {b} | {fr} | {t['reads']} | {fw} | "
                      f"{t['writes']} | {t['comparisons']:,} |")
    write_csv("cost.csv", rows)


# ---- 2. sort passes ---------------------------------------------------------------------
def sort_passes(db, tables):
    rows = []
    print("\n### Sort passes\n")
    print("| N | plan B | sort F | initial runs | formula(N, F) | measured | sort reads | sort writes |")
    print("|---|---|---|---|---|---|---|---|")
    for name, n in tables:
        for b in (4, 5, 8, 16, 33):
            plan = {"op": "sort", "keys": [[f"{name}.k", "asc"], [f"{name}.id", "asc"]],
                    "child": scan(name)}
            t, root = db.run(plan, b)
            s = root.sorter
            rows.append({"N": n, "B": b, "F": s.F, "initial_runs": s.initial_runs,
                         "formula": passes(n, s.F), "passes": s.passes,
                         "sort_reads": root.c.page_reads, "sort_writes": root.c.page_writes,
                         "peak_pinned": t["peak_pinned"]})
            print(f"| {n} | {b} | {s.F} | {s.initial_runs} | {passes(n, s.F)} | {s.passes} | "
                  f"{root.c.page_reads} | {root.c.page_writes} |")
    write_csv("sort_passes.csv", rows)


# ---- 3. crossover -----------------------------------------------------------------------
def crossover(db, fixed, frames_list, size_tables, fixed_b):
    rows = []
    l, r, m, n = fixed
    for b in frames_list:
        for op in JOINS:
            t, _ = db.run(join(op, l, r), b)
            rows.append({"vary": "B", "B": b, "M": m, "N": n, "join": op,
                         "io": t["reads"] + t["writes"], "comparisons": t["comparisons"]})
    for (l, r, m, n) in size_tables:
        for op in JOINS:
            t, _ = db.run(join(op, l, r), fixed_b)
            rows.append({"vary": "size", "B": fixed_b, "M": m, "N": n, "join": op,
                         "io": t["reads"] + t["writes"], "comparisons": t["comparisons"]})
    write_csv("crossover.csv", rows)
    print_crossover(rows)


def print_crossover(rows):
    for vary, col in (("B", "B"), ("size", "M")):
        sel = [x for x in rows if x["vary"] == vary]
        print(f"\n### Crossover, varying {vary} (total page I/O; cheapest in bold)\n")
        print(f"| {col} | BNL | SMJ | Hash | cheapest |")
        print("|---|---|---|---|---|")
        for key in sorted({int(x[col]) for x in sel}):
            here = {x["join"]: int(x["io"]) for x in sel if int(x[col]) == key}
            best = min(here, key=here.get)
            cells = [f"**{here[j]}**" if j == best else str(here[j]) for j in JOINS]
            print(f"| {key} | " + " | ".join(cells) + f" | {SHORT[best]} |")


def plot():
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    rows = read_csv("crossover.csv")
    for vary, col, xlabel, fname in (
            ("B", "B", "B (frames)", "crossover_B.png"),
            ("size", "M", "M = outer pages (N = M / 2)", "crossover_size.png")):
        sel = [x for x in rows if x["vary"] == vary]
        fig, ax = plt.subplots(figsize=(6.5, 4.2))
        for j, style in zip(JOINS, ("o-", "s-", "^-")):
            pts = sorted((int(x[col]), int(x["io"])) for x in sel if x["join"] == j)
            ax.plot([p[0] for p in pts], [p[1] for p in pts], style, label=SHORT[j])
        ax.set_xscale("log", base=2)
        ax.set_yscale("log")
        ax.set_xlabel(xlabel)
        ax.set_ylabel("page I/O (reads + writes)")
        fixed = sel[0]
        ax.set_title(f"M = {fixed['M']}, N = {fixed['N']}" if vary == "B"
                     else f"B = {fixed['B']}")
        ax.grid(True, which="both", alpha=0.3)
        ax.legend()
        fig.tight_layout()
        fig.savefig(os.path.join(OUT, fname), dpi=150)
        plt.close(fig)


# ---- 4. skew ------------------------------------------------------------------------------
def skew(db, uni, sk, b):
    print(f"\n### Skew (hash join, B = {b})\n")
    rows = []
    for label, (l, r) in (("uniform", uni), ("40% one key", sk)):
        t, root = db.run(join("hash_join", l, r), b)
        rows.append({"data": label, "rows": t["rows"], "reads": t["reads"],
                     "writes": t["writes"], "comparisons": t["comparisons"],
                     "partition_pages": root.partition_pages, "events": root.events})
        print(f"**{label}**: {t['rows']:,} rows, reads {t['reads']}, writes {t['writes']}, "
              f"comparisons {t['comparisons']:,}")
        print(f"- level-1 partitions (R pages, S pages): {root.partition_pages}")
        for e in root.events:
            print(f"- {e}")
        print()
    write_csv("skew.csv", rows)


# ---- 5. the budget ---------------------------------------------------------------------------
def budget(db, name, n):
    t, root = db.run({"op": "sort", "keys": [[f"{name}.k", "asc"]], "child": scan(name)}, 4)
    print("\n### The budget\n")
    print(f"sort of N = {n} pages (20 x B) at B = 4: {t['rows']:,} rows, "
          f"{root.sorter.initial_runs} initial runs, {root.sorter.passes} passes, "
          f"peak pinned frames = {t['peak_pinned']}, peak reserved = {t['peak_reserved']}")
    write_csv("budget.csv", [{"N": n, "B": 4, **t, "passes": root.sorter.passes}])


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--plot-only", action="store_true")
    args = ap.parse_args()
    if args.plot_only:
        plot()
        return
    db = DB()
    sizes = []
    for m, seed in ((16, 1), (64, 2), (256, 3)):
        n = m // 2
        a = db.table(f"R{m}", m, uniform, seed)
        b = db.table(f"S{m}", n, uniform, seed + 100)
        sizes.append((f"R{m}", f"S{m}", a, b))
    cost_vs_formula(db, sizes)

    for n in (8, 50, 200):
        db.table(f"T{n}", n, uniform, 500 + n)
    db.table("T80", 80, uniform, 580)
    sort_passes(db, [("T8", 8), ("T50", 50), ("T80", 80), ("T200", 200)])

    size_tables = list(sizes)
    for m, seed in ((8, 7), (32, 8), (128, 9)):
        a = db.table(f"R{m}", m, uniform, seed)
        b = db.table(f"S{m}", m // 2, uniform, seed + 100)
        size_tables.append((f"R{m}", f"S{m}", a, b))
    size_tables.sort(key=lambda x: x[2])
    crossover(db, sizes[1], [4, 5, 6, 8, 12, 16, 24, 34, 48, 66, 96, 128], size_tables, 16)
    plot()

    hot = lambda i, rnd: 0 if rnd.random() < 0.4 else 1 + rnd.randrange(KEYS)
    db.table("KL", 64, hot, 11)
    db.table("KR", 32, hot, 12)
    skew(db, ("R64", "S64"), ("KL", "KR"), 16)

    budget(db, "T80", 80)
    db.tmp.cleanup()


if __name__ == "__main__":
    sys.exit(main())
