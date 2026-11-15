"""Statistics and ANALYZE (Module 4, Section 1). DESIGN_M4.md section 2.

Layout. Table-level numbers go in the catalog record's statistics field
(Module 1 left it for this), which is small and fixed in size:

    {"analyzed": true, "n_tuples": N, "n_pages": P, "width": W,
     "buckets": k, "stats_file": "T.stats"}

Column statistics do not fit there: a catalog record holds at most 4084
bytes, and 20 buckets of VARCHAR bounds for a few columns are more. They go
in a heap file of their own, T.stats, one JSON record per column:

    {"col": "age", "ndv": 51, "min": 20, "max": 70, "width": 4,
     "hist": [[lo, hi, count, ndv], ...]}

A column whose record would not fit in a page is split into several records
with the same "col" and a "part" number; the histogram parts are joined in
order when read back.

ANALYZE scans the original table once, counting live tuples and record
bytes while copying live records to temporary storage. n_pages counts
the original table's data pages, excluding directory pages. Each column
is externally sorted from the temporary copy using Module 2, so large
columns do not need to fit in memory. Temporary storage is removed on
completion or failure. Statistics are exact, with no sampling, and
running ANALYZE twice over unchanged data produces unchanged statistics.
"""
from __future__ import annotations

import json
import math
import os
import tempfile
from dataclasses import dataclass, field
from typing import Dict, List, Optional

from storage.buffer import BufferPool
from storage.catalog import Catalog, TableInfo
from storage.disk import DiskManager
from storage.heap import NO_PAGE, DirectoryPage, HeapFile
from storage.page import MAX_RECORD
from storage.record import INT

DEFAULT_BUCKETS = 20
SORT_FRAMES = 16


# ---- what the estimator reads ------------------------------------------------------
@dataclass
class ColStats:
    ndv: float
    min: object = None
    max: object = None
    width: float = 4.0
    hist: List[list] = field(default_factory=list)    # [lo, hi, count, ndv]
    exact: bool = True                                 # False: a default guess


@dataclass
class TableStats:
    name: str
    n_tuples: int
    n_pages: int
    width: float
    cols: Dict[str, ColStats]
    analyzed: bool


def default_width(col) -> float:
    """Record bytes of a column when nothing is known: INT is 4, VARCHAR(n)
    is assumed half full (2 + n/2). DESIGN_M4.md assumption A4."""
    return 4.0 if col.type == INT else 2 + col.length / 2


def defaults_for(info: TableInfo) -> TableStats:
    """No ANALYZE yet: Module 1's own counts, and documented guesses for the
    columns (ndv = n/10, no min/max, no histogram). See estimate.py for the
    selectivities used when a column has no min/max."""
    n = info.num_tuples
    cols = {c.name: ColStats(ndv=max(1.0, n / 10), width=default_width(c), exact=False)
            for c in info.columns}
    width = sum(cs.width for cs in cols.values())
    pages = max(0, info.num_pages - 1)       # Module 1 counts the directory page
    return TableStats(info.name, n, pages, width, cols, False)


def stats_for(db_dir: str, info: TableInfo, pool: Optional[BufferPool] = None) -> TableStats:
    st = info.stats or {}
    if not st.get("analyzed"):
        return defaults_for(info)
    cols = {c.name: ColStats(ndv=1.0, width=default_width(c)) for c in info.columns}
    path = os.path.join(db_dir, st["stats_file"])
    if os.path.exists(path):
        heap = HeapFile(pool or BufferPool(2), DiskManager(path), st["stats_file"])
        for _, rec in heap.scan():
            d = json.loads(rec.decode("utf-8"))
            cs = cols.get(d["col"])
            if cs is None:
                continue
            if d.get("part", 0) == 0:
                cs.ndv, cs.min, cs.max, cs.width = d["ndv"], d["min"], d["max"], d["width"]
            cs.hist.extend(d["hist"])
    return TableStats(info.name, st["n_tuples"], st["n_pages"], st["width"], cols, True)


def load_all(db_dir: str) -> Dict[str, TableStats]:
    """Statistics of every table in the database (defaults where not analyzed)."""
    pool = BufferPool(2)
    cat = Catalog(db_dir, pool)
    return {t.name: stats_for(db_dir, t, pool) for t in cat.tables()}


# ---- building a histogram from a sorted stream --------------------------------------
def build_histogram(sorted_values, n: int, k: int) -> List[list]:
    """Equi-depth: cut after every ceil(n/k)-th value, but never between two
    equal values, so that each value lives in exactly one bucket and the
    per-bucket ndv add up to the column's ndv. A very frequent value can make
    a bucket larger than n/k; there are then fewer than k buckets."""
    target = max(1, math.ceil(n / k)) if n else 1
    out: List[list] = []
    cur = None
    for v in sorted_values:
        if cur is not None and cur[2] >= target and v != cur[1]:
            out.append(cur)
            cur = None
        if cur is None:
            cur = [v, v, 0, 0]
        if cur[3] == 0 or v != cur[1]:
            cur[3] += 1
        cur[1] = v
        cur[2] += 1
    if cur is not None:
        out.append(cur)
    return out


def _value_width(v) -> int:
    return 4 if isinstance(v, int) else 2 + len(v.encode("utf-8"))


# ---- ANALYZE --------------------------------------------------------------------------
def _data_pages(cat: Catalog, info: TableInfo) -> int:
    """Data pages only: the number of entries over all directory pages."""
    disk = cat.disk(info.file)
    dir_id, total = 0, 0
    while dir_id != NO_PAGE:
        d = DirectoryPage(cat.pool.fetch_page(disk, dir_id))
        try:
            total += d.entry_count
            nxt = d.next_dir
        finally:
            cat.pool.unpin_page(disk, dir_id, False)
        dir_id = nxt
    return total


def _sorted_column(db_dir: str, table: str, col: str, frames: int):
    """Stream one column in ascending order through the Module 2 executor's
    external sort (plan: sort over project over seqscan)."""
    from execution.base import rows_of
    from execution.context import ExecContext
    from execution.plan import prepare
    name = f"{table}.{col}"
    plan = {"op": "sort", "keys": [[name, "asc"]],
            "child": {"op": "project", "columns": [name],
                      "child": {"op": "seqscan", "table": table}}}
    ctx = ExecContext(db_dir, frames)
    root = prepare(plan, ctx)
    try:
        root.open(ctx.free())
        for row in rows_of(root):
            yield row[0]
    finally:
        root.close()
        ctx.cleanup()


def _records(d: dict) -> List[bytes]:
    """Encode statistics, splitting metadata and histogram if necessary."""
    def encode(record):
        return json.dumps(record, sort_keys=True).encode("utf-8")

    def checked(record):
        raw = encode(record)
        if len(raw) > MAX_RECORD:
            raise ValueError(
                "A statistics component exceeds the record-size limit"
            )
        return raw

    whole = encode(d)
    if len(whole) <= MAX_RECORD:
        return [whole]

    head = {k: v for k, v in d.items() if k != "hist"}
    out = [checked(dict(head, part=0, hist=[]))]
    part, chunk = 1, []

    for bucket in d["hist"]:
        trial = {
            "col": d["col"],
            "part": part,
            "hist": chunk + [bucket],
        }

        if len(encode(trial)) > MAX_RECORD:
            if not chunk:
                raise ValueError(
                    "A histogram bucket exceeds the record-size limit"
                )

            out.append(checked({
                "col": d["col"],
                "part": part,
                "hist": chunk,
            }))

            part += 1
            chunk = []

            checked({
                "col": d["col"],
                "part": part,
                "hist": [bucket],
            })

        chunk.append(bucket)

    if chunk:
        out.append(checked({
            "col": d["col"],
            "part": part,
            "hist": chunk,
        }))

    return out


def analyze(db_dir: str, table: str, buckets: int = DEFAULT_BUCKETS,
            frames: int = SORT_FRAMES) -> TableStats:
    if buckets < 1:
        raise ValueError("--buckets must be at least 1")
    pool = BufferPool(4)
    cat = Catalog(db_dir, pool)
    info = cat.get(table)
    heap = cat.open_heap(table)

    # one scan: live tuples and average record width
    n, total = 0, 0
    col_dicts = []

    with tempfile.TemporaryDirectory(prefix="m4-analyze-") as work_dir:
        work_cat = Catalog(work_dir, BufferPool(4))
        work_cat.create_table(table, info.columns)
        work_heap = work_cat.open_heap(table)

        # Read the original table once and materialize its live records.
        source = heap.scan()
        try:
            for _, rec in source:
                n += 1
                total += len(rec)
                work_heap.insert(rec)
        finally:
            source.close()

        work_cat.flush()

        # Count pages in the original table, not the temporary copy.
        n_pages = _data_pages(cat, info)
        width = round(total / n, 3) if n else 0.0

        for c in info.columns:
            widths, count = 0, 0

            def stream():
                nonlocal widths, count

                # Sort columns from the temporary copy.
                values = _sorted_column(
                    work_dir, table, c.name, frames
                )
                try:
                    for v in values:
                        widths += _value_width(v)
                        count += 1
                        yield v
                finally:
                    values.close()

            hist = build_histogram(stream(), n, buckets)

            col_dicts.append({
                "col": c.name,
                "ndv": sum(b[3] for b in hist),
                "min": hist[0][0] if hist else None,
                "max": hist[-1][1] if hist else None,
                "width": (
                    round(widths / count, 3)
                    if count else default_width(c)
                ),
                "hist": hist,
            })

    # replace the statistics file
    stats_file = f"{table}.stats"
    path = os.path.join(db_dir, stats_file)
    if os.path.exists(path):
        os.remove(path)
    sdisk = DiskManager(path)
    spool = BufferPool(4)
    sheap = HeapFile.create(spool, sdisk, stats_file)
    for d in col_dicts:
        for rec in _records(d):
            sheap.insert(rec)
    spool.flush_all()

    new = {"analyzed": True, "n_tuples": n, "n_pages": n_pages, "width": width,
           "buckets": buckets, "stats_file": stats_file}
    if info.stats != new:            # unchanged: leave catalog.db byte for byte
        info.stats = new
        cat.update(info)
    cat.flush()
    return stats_for(db_dir, cat.get(table))