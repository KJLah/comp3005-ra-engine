"""Module 1 section 8 measurements. Prints the tables for REPORT_M1.md.

    python3 make_traces.py      # once, writes traces/
    python3 measure_m1.py

Uses the same code the command line uses (run_trace, HeapFile.scan), so every
number here can be checked with `python3 -m storage ...`.
"""
import os
import tempfile

from storage.buffer import BufferPool, run_trace
from storage.catalog import Catalog
from storage.csvload import load_csv
from storage.page import HEADER_SIZE, PAGE_SIZE, SLOT_SIZE, SlottedPage
from storage.record import encode, parse_schema

ROOT = os.path.dirname(os.path.abspath(__file__))
TRACES = ["t1_worked", "t2_hotcold", "t3_scan80x10", "t4_uniform", "t5_hot_plus_scan"]
FRAMES = [4, 16, 64, 256]


def capacity():
    """Records per page: by hand from section 3, then measured on page 1."""
    cases = [("all INT", "a:INT,b:INT,c:INT", lambda i: f"{i},{i},{i}"),
             ("short VARCHAR", "id:INT,name:VARCHAR(8)", lambda i: f"{i},name{i % 10000:04d}"),
             ("VARCHAR(200)", "id:INT,note:VARCHAR(200)", lambda i: f"{i},{'x' * 200}")]
    print("| Schema | Record bytes | Computed per page | Measured on page 1 |")
    print("|---|---|---|---|")
    for label, schema, row in cases:
        cols = parse_schema(schema)
        sample = row(0).split(",")
        size = len(encode(cols, [int(sample[0])] + [int(v) if c.type == "INT" else v
                                                     for c, v in zip(cols[1:], sample[1:])]))
        computed = (PAGE_SIZE - HEADER_SIZE) // (size + SLOT_SIZE)
        with tempfile.TemporaryDirectory() as d:
            cat = Catalog(d, BufferPool(16))
            cat.create_table("T", cols)
            load_csv(cat, "T", [row(i) for i in range(3 * computed)])
            disk = cat.disk("T.db")
            pool = BufferPool(1)
            measured = SlottedPage(pool.fetch_page(disk, 1)).slot_count
            pool.unpin_page(disk, 1, False)
        print(f"| {label} `{schema}` | {size} | floor(4088 / {size + 4}) = {computed} | {measured} |")


def policies():
    for policy in ("lru", "clock"):
        print(f"\n**{policy.upper()}**\n")
        print("| Trace | Fetches | B | Hits | Misses | Evictions | Hit rate |")
        print("|---|---|---|---|---|---|---|")
        for name in TRACES:
            with open(os.path.join(ROOT, "traces", name + ".txt"), encoding="utf-8") as f:
                ids = [int(t) for t in f.read().split()]
            for b in FRAMES:
                r = run_trace(ids, b, policy)
                print(f"| {name} | {len(ids)} | {b} | {r['hits']} | {r['misses']} | "
                      f"{r['evictions']} | {r['hits'] / len(ids):.3f} |")


def scan_cost():
    per_page = (PAGE_SIZE - HEADER_SIZE) // (12 + SLOT_SIZE)        # 255 all-INT rows
    with tempfile.TemporaryDirectory() as d:
        cat = Catalog(d, BufferPool(64))
        cat.create_table("Big", parse_schema("a:INT,b:INT,c:INT"))
        load_csv(cat, "Big", [f"{i},{i},{i}" for i in range(500 * per_page)])
        info = cat.get("Big")
        print(f"\nTable Big: {info.num_tuples} rows, {info.num_pages} pages "
              f"(1 directory + {info.num_pages - 1} data)\n")
        print("| B | Rows | Hits | Misses | Evictions | Reads | Writes |")
        print("|---|---|---|---|---|---|---|")
        for b in (2, 8, 64):
            pool = BufferPool(b)
            heap = cat.open_heap("Big")
            heap.pool = pool
            rows = sum(1 for _ in heap.scan())
            s = pool.stats()
            print(f"| {b} | {rows} | {s['hits']} | {s['misses']} | {s['evictions']} | "
                  f"{s['reads']} | {s['writes']} |")


if __name__ == "__main__":
    print("## Capacity\n")
    capacity()
    print("\n## Policies")
    policies()
    print("\n## Scan cost")
    scan_cost()