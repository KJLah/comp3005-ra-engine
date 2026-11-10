"""Command line interface (Module 1, section 7). Run from the repo root:

    python3 -m storage create-table --db DIR --name R --schema "a:INT,b:VARCHAR(20),c:INT"
    python3 -m storage load         --db DIR --table R --file r.csv
    python3 -m storage scan         --db DIR --table R --frames 8 [--limit N] --json
    python3 -m storage layout       --db DIR --table R --page 3 [--hex]
    python3 -m storage trace        --db DIR --file trace.txt --frames 16 --policy lru|clock --json

This is the only module in storage/ that prints or reads a user's file.
Every StorageError becomes a one-line "Error: ..." and exit code 1; a user
never sees a Python traceback.
"""
from __future__ import annotations

import argparse
import json
import os
from typing import List, Optional

from .buffer import BufferPool, run_trace
from .catalog import CATALOG_FILE, Catalog
from .csvload import load_csv
from .disk import DiskManager
from .errors import StorageError
from .heap import NO_PAGE, DirectoryPage, HeapFile
from .page import HEADER_SIZE, PAGE_SIZE, SlottedPage
from .record import decode, parse_schema
import sys
from itertools import islice

# The catalog is read with its own small pool, so the counters printed by
# `scan` measure only the table's pages (see DESIGN_M1.md section 10).
CATALOG_FRAMES = 4


def read_text(path: str) -> str:
    try:
        with open(path, encoding="utf-8") as f:
            return f.read()
    except OSError as e:
        raise StorageError(f"cannot read '{path}': {e.strerror}")
    except UnicodeDecodeError:
        raise StorageError(f"'{path}' is not UTF-8 text")


def open_catalog(db: str, create: bool = False) -> Catalog:
    if not os.path.isdir(db):
        if not create:
            raise StorageError(f"database directory '{db}' does not exist "
                               f"(create-table makes it)")
        os.makedirs(db)
    return Catalog(db, BufferPool(CATALOG_FRAMES))


# ---- commands ----------------------------------------------------------------------
def cmd_create_table(a) -> str:
    cat = open_catalog(a.db, create=True)
    info = cat.create_table(a.name, parse_schema(a.schema))
    cat.flush()
    return f"created table {info.name} in {os.path.join(a.db, info.file)}"


def cmd_load(a) -> str:
    cat = open_catalog(a.db)
    n = load_csv(cat, a.table, read_text(a.file).split("\n"))
    info = cat.get(a.table)
    return (f"loaded {n} rows into {a.table} "
            f"({info.num_tuples} rows, {info.num_pages} pages)")


def cmd_scan(a) -> None:
    if a.limit is not None and a.limit < 0:
        raise StorageError("--limit must be 0 or more")

    if a.frames < 2:
        raise StorageError(
            "scan needs --frames 2 or more: one for the directory page, "
            "one for the data page"
        )

    info = open_catalog(a.db).get(a.table)
    pool = BufferPool(a.frames, a.policy)
    heap = HeapFile(
        pool,
        DiskManager(os.path.join(a.db, info.file)),
        info.name,
    )

    it = heap.scan()
    selected = it if a.limit is None else islice(it, a.limit)
    count = 0

    if a.json:
        schema = [c.to_json() for c in info.columns]
        sys.stdout.write(
            '{"table":' + json.dumps(info.name)
            + ',"schema":' + json.dumps(schema)
            + ',"rows":['
        )
    else:
        print(" | ".join(c.name for c in info.columns))

    try:
        for _, rec in selected:
            row = decode(info.columns, rec)

            if a.json:
                if count:
                    sys.stdout.write(",")
                sys.stdout.write(json.dumps(list(row)))
            else:
                print(" | ".join(str(value) for value in row))

            count += 1
    finally:
        it.close()

    if a.json:
        sys.stdout.write(
            '],"stats":' + json.dumps(pool.stats()) + '}\n'
        )
    else:
        print(
            f"({count} rows)  "
            + "  ".join(
                f"{key}={value}"
                for key, value in pool.stats().items()
            )
        )


def cmd_layout(a) -> str:
    if a.table == "catalog":
        path = os.path.join(a.db, CATALOG_FILE)
    else:
        path = os.path.join(a.db, open_catalog(a.db).get(a.table).file)
    disk = DiskManager(path)
    if not 0 <= a.page < disk.num_pages:
        raise StorageError(f"{path} has pages 0..{disk.num_pages - 1}; there is no page {a.page}")
    pool = BufferPool(1)
    data = pool.fetch_page(disk, a.page)
    try:
        lines = [f"file {path}, page {a.page}"]
        if a.page == 0 or _is_directory(disk, a.page):
            d = DirectoryPage(data)
            nxt = "none" if d.next_dir == NO_PAGE else str(d.next_dir)
            lines.append(f"directory page: page_id={d.page_id} next_dir_page={nxt} "
                         f"entry_count={d.entry_count}")
            for i in range(d.entry_count):
                pid, free = d.entry(i)
                lines.append(f"  entry {i}: data_page={pid} free_bytes={free}")
        else:
            p = SlottedPage(data)
            lines.append(f"data page: page_id={p.page_id} slot_count={p.slot_count} "
                         f"free_end={p.free_end} free_space={p.free_space()}")
            for n, off, length in p.directory():
                state = "dead" if off == 0 else "live"
                lines.append(f"  slot {n}: ({off},{length}) {state}")
        if a.hex:
            lines.append(hex_dump(bytes(data)))
        return "\n".join(lines)
    finally:
        pool.unpin_page(disk, a.page, False)


def _is_directory(disk, page_id: int) -> bool:
    """Directory pages after page 0 are found by following the chain."""
    pool = BufferPool(1)
    d = 0
    while d != NO_PAGE:
        nxt = DirectoryPage(pool.fetch_page(disk, d)).next_dir
        pool.unpin_page(disk, d, False)
        if d == page_id:
            return True
        d = nxt
    return False


def hex_dump(data: bytes) -> str:
    """Offsets in decimal, like section 3.2. All-zero lines after the header are skipped."""
    out = []
    for off in range(0, PAGE_SIZE, 16):
        chunk = data[off:off + 16]
        if off >= HEADER_SIZE and not any(chunk):
            continue
        out.append(f"  {off:04d}  " + " ".join(f"{b:02x}" for b in chunk))
    return "\n".join(out)


def cmd_trace(a) -> str:
    ids: List[int] = []
    for n, line in enumerate(read_text(a.file).split("\n"), start=1):
        t = line.strip()
        if not t:
            continue
        if not t.isdigit():
            raise StorageError(f"{a.file}, line {n}: '{t}' is not a page id")
        ids.append(int(t))
    result = run_trace(ids, a.frames, a.policy)
    if a.json:
        return json.dumps(result)
    hits, total = result["hits"], len(ids)
    rate = hits / total if total else 0.0
    return (f"policy={a.policy} frames={a.frames} fetches={total} hits={hits} "
            f"misses={result['misses']} evictions={result['evictions']} "
            f"reads={result['reads']} writes={result['writes']} hit_rate={rate:.4f}")


# ---- argument parsing ------------------------------------------------------------
def build_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(prog="python3 -m storage",
                                 description="Module 1 storage engine")
    sub = ap.add_subparsers(dest="command", required=True)

    p = sub.add_parser("create-table")
    p.add_argument("--db", required=True)
    p.add_argument("--name", required=True)
    p.add_argument("--schema", required=True)
    p.set_defaults(func=cmd_create_table)

    p = sub.add_parser("load")
    p.add_argument("--db", required=True)
    p.add_argument("--table", required=True)
    p.add_argument("--file", required=True)
    p.set_defaults(func=cmd_load)

    p = sub.add_parser("scan")
    p.add_argument("--db", required=True)
    p.add_argument("--table", required=True)
    p.add_argument("--frames", type=int, default=8)
    p.add_argument("--policy", choices=["lru", "clock"], default="lru")
    p.add_argument("--limit", type=int)
    p.add_argument("--json", action="store_true")
    p.set_defaults(func=cmd_scan)

    p = sub.add_parser("layout")
    p.add_argument("--db", required=True)
    p.add_argument("--table", required=True)
    p.add_argument("--page", type=int, required=True)
    p.add_argument("--hex", action="store_true")
    p.set_defaults(func=cmd_layout)

    p = sub.add_parser("trace")
    p.add_argument("--db")                     # accepted for the spec's syntax; unused
    p.add_argument("--file", required=True)
    p.add_argument("--frames", type=int, required=True)
    p.add_argument("--policy", choices=["lru", "clock"], required=True)
    p.add_argument("--json", action="store_true")
    p.set_defaults(func=cmd_trace)
    return ap


def main(argv: Optional[List[str]] = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        result = args.func(args)
        if result is not None:
            print(result)
        return 0
    except StorageError as e:
        print(f"Error: {e}")
        return 1
    except Exception as e:                    # last line of defence: no tracebacks
        print(f"Internal error: {type(e).__name__}: {e}")
        return 2