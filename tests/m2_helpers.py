"""Shared helpers for the Module 2 tests: build a database, run a plan."""
import json
import tempfile

from storage.buffer import BufferPool
from storage.catalog import Catalog
from storage.csvload import load_csv
from storage.record import parse_schema

from execution.base import rows_of
from execution.context import ExecContext
from execution.plan import prepare


def make_db(tables):
    """tables: {name: (schema text, [csv lines])}. Returns a TemporaryDirectory."""
    tmp = tempfile.TemporaryDirectory()
    cat = Catalog(tmp.name, BufferPool(8))
    for name, (schema, lines) in tables.items():
        cat.create_table(name, parse_schema(schema))
        if lines:
            load_csv(cat, name, lines)
    cat.flush()
    return tmp


def scan(t):
    return {"op": "seqscan", "table": t}


class Result:
    def __init__(self, ctx, root, rows):
        self.ctx, self.root, self.rows = ctx, root, rows

    def node(self, path):
        for _, op in self.root.walk():
            if op.path == path:
                return op.counters()
        raise KeyError(path)


def run_plan(db_dir, plan, frames, max_rows=None):
    """Open, pull every row (or max_rows), close; check nothing is left behind."""
    ctx = ExecContext(db_dir, frames)
    root = prepare(json.loads(json.dumps(plan)), ctx)
    rows = []
    try:
        root.open(ctx.free())
        for r in rows_of(root):
            rows.append(r)
            if max_rows is not None and len(rows) >= max_rows:
                break
    finally:
        root.close()
    assert ctx.pool.pinned == 0, f"{ctx.pool.pinned} frames still pinned"
    assert ctx.leftover_temps() == [], ctx.leftover_temps()
    assert ctx.reserved == 0, ctx.holders
    assert ctx.pool.peak_pinned <= frames
    ctx.cleanup()
    return Result(ctx, root, rows)
