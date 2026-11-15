"""One shell session: a database directory and a frame budget B.

Each statement is wired through the modules without changing them:

    SELECT ...            M3 compile_script -> M4 Planner.plan -> M2 PlanRun
    EXPLAIN q             M3 -> M4, then the Module 4 explain tree
    EXPLAIN ANALYZE q     M3 -> M4 -> M2, then every node's estimated and
                          actual rows and its page reads and writes
    ANALYZE [t, ...]      optimizer.stats.analyze (every table when none named)
    SET frames = n        the B handed to M4 and M2 from now on
    LOAD t FROM 'f'       storage.csvload.load_csv (Module 1)
    CREATE TABLE, INSERT  checked by M3 (sql.statements), applied by M1

Nothing is cached between statements: the catalog and the statistics are
read again for every statement, so a LOAD or ANALYZE is seen at once.
"""
from __future__ import annotations

import json
import os
import time
from dataclasses import dataclass, field
from typing import List, Optional, TextIO

from execution.cli import PlanRun, format_value
from optimizer.explain import explain, label
from optimizer.planner import Planner
from optimizer.runner import preorder
from optimizer.stats import DEFAULT_BUCKETS, analyze, load_all
from sql import ast as A
from sql.cli import compile_script
from sql.parser import parse_script
from sql.resolve import OverlayCatalog, StorageCatalog
from sql.statements import check_create, check_insert
from storage.buffer import BufferPool
from storage.catalog import CATALOG_FILE, Catalog
from storage.csvload import load_csv
from storage.record import Column, encode

from .split import Command, ShellError, classify, split_statements

DEFAULT_FRAMES = 32
MIN_FRAMES = 4
CATALOG_FRAMES = 16


@dataclass
class QueryResult:
    """What the workload report needs from one query."""
    names: List[str]
    types: List[str]
    rows: list
    est_rows: float           # Module 4's estimate at the root
    actual_rows: int          # the root operator's tuples_out
    page_reads: int
    page_writes: int
    comparisons: int
    compile_ms: float
    plan_ms: float
    exec_ms: float
    plan: dict = field(default_factory=dict)

    @property
    def io(self) -> int:
        return self.page_reads + self.page_writes

    @property
    def wall_ms(self) -> float:
        return self.compile_ms + self.plan_ms + self.exec_ms

    @property
    def q_error(self) -> float:
        e, a = max(self.est_rows, 1.0), max(self.actual_rows, 1)
        return max(e / a, a / e)


def _ms(t0: float) -> float:
    return (time.perf_counter() - t0) * 1000.0


class Session:
    def __init__(self, db: str, frames: int = DEFAULT_FRAMES, out: Optional[TextIO] = None,
                 print_rows: bool = True):
        if frames < MIN_FRAMES:
            raise ShellError(f"frames must be at least {MIN_FRAMES}")
        self.db = db
        self.frames = frames
        self.out = out
        self.print_rows = print_rows
        self.buckets = DEFAULT_BUCKETS

    # ---- output -------------------------------------------------------------------------
    def say(self, text: str) -> None:
        if self.out is not None:
            self.out.write(text + "\n")

    # ---- entry points -------------------------------------------------------------------
    def run_script(self, text: str) -> None:
        """Run every statement in order. The first error stops the script
        (panic mode, as in Module 3) and is raised to the caller."""
        for stmt in split_statements(text):
            self.execute(stmt)

    def execute(self, stmt: str) -> Optional[QueryResult]:
        cmd = classify(stmt)
        if cmd.kind == "sql":
            return self.sql(cmd.sql)
        if cmd.kind == "explain":
            self.explain(cmd.sql)
        elif cmd.kind == "explain_analyze":
            return self.explain_analyze(cmd.sql)
        elif cmd.kind == "analyze":
            self.analyze(cmd.tables)
        elif cmd.kind == "set":
            self.set(cmd.name, cmd.value)
        elif cmd.kind == "load":
            self.load(cmd.name, cmd.value, cmd.header)
        elif cmd.kind == "meta":
            self.meta(cmd)
        return None

    # ---- helpers ------------------------------------------------------------------------
    def need_db(self) -> None:
        if not os.path.isfile(os.path.join(self.db, CATALOG_FILE)):
            raise ShellError(f"'{self.db}' is not a database yet (no {CATALOG_FILE}); "
                             f"CREATE TABLE makes one")

    def catalog(self) -> Catalog:
        os.makedirs(self.db, exist_ok=True)
        return Catalog(self.db, BufferPool(CATALOG_FRAMES))

    def compile_one(self, text: str) -> dict:
        """Module 3: the logical plan of exactly one query."""
        self.need_db()
        plans = compile_script(text, StorageCatalog(self.db))
        if len(plans) != 1:
            raise ShellError("expected exactly one query here")
        return plans[0]

    def optimize(self, logical: dict):
        """Module 4: the physical plan, with the planner kept for its estimator."""
        p = Planner(load_all(self.db), self.frames)
        return p, p.plan(logical)

    # ---- SQL ----------------------------------------------------------------------------
    def sql(self, text: str) -> Optional[QueryResult]:
        stmts = parse_script(text)           # one statement: the splitter cut on ';'
        if not stmts:
            return None
        stmt = stmts[0]
        if isinstance(stmt, (A.CreateTable, A.Insert)):
            self.ddl(stmt)
            return None
        return self.query(text)

    def ddl(self, stmt) -> None:
        """CREATE TABLE and INSERT: Module 3 checks, Module 1 applies (the
        same calls as `sql exec`)."""
        overlay = OverlayCatalog(StorageCatalog(self.db)) if os.path.isfile(
            os.path.join(self.db, CATALOG_FILE)) else OverlayCatalog(_Empty())
        if isinstance(stmt, A.CreateTable):
            cols = check_create(stmt, overlay)
            cat = self.catalog()
            cat.create_table(stmt.name, [Column(c.name, c.type, c.length) for c in cols])
            cat.flush()
            self.say(f"created table {stmt.name}")
            return
        cols = check_insert(stmt, overlay)
        schema = [Column(c.name, c.type, c.length) for c in cols]
        cat = self.catalog()
        cat.open_heap(stmt.table).insert(encode(schema, [v.value for v in stmt.values]))
        cat.refresh_counts(stmt.table)
        cat.flush()
        self.say(f"inserted 1 row into {stmt.table}")

    def query(self, text: str) -> QueryResult:
        t0 = time.perf_counter()
        logical = self.compile_one(text)
        compile_ms = _ms(t0)

        t0 = time.perf_counter()
        planner, phys = self.optimize(logical)
        est = planner.est.props(phys).rows
        plan_ms = _ms(t0)

        t0 = time.perf_counter()
        pr = PlanRun(self.db, json.dumps(phys), self.frames)
        types = pr.schema.types()
        names = pr.schema.names()
        rows = list(pr.rows())
        exec_ms = _ms(t0)
        if self.print_rows:
            self.say(" | ".join(names))
            for r in rows:
                self.say(" | ".join(format_value(v, t) for v, t in zip(r, types)))
        t = pr.totals()
        res = QueryResult(names, types, rows, est, t["tuples_out"], t["page_reads"],
                          t["page_writes"], t["comparisons"], compile_ms, plan_ms,
                          exec_ms, phys)
        self.say(f"({res.actual_rows} rows; est_rows={_num(est)}; "
                 f"reads={res.page_reads} writes={res.page_writes}; "
                 f"time={res.wall_ms:.1f} ms; B={self.frames})")
        return res

    # ---- EXPLAIN ------------------------------------------------------------------------
    def explain(self, text: str) -> None:
        planner, phys = self.optimize(self.compile_one(text))
        self.say(explain(phys, planner.cost))

    def explain_analyze(self, text: str) -> QueryResult:
        t0 = time.perf_counter()
        logical = self.compile_one(text)
        compile_ms = _ms(t0)
        t0 = time.perf_counter()
        planner, phys = self.optimize(logical)
        plan_ms = _ms(t0)

        t0 = time.perf_counter()
        pr = PlanRun(self.db, json.dumps(phys), self.frames)
        rows = list(pr.rows())
        exec_ms = _ms(t0)

        nodes = preorder(phys)
        ops = [op for _, op in pr.root.walk()]
        if len(nodes) != len(ops):
            raise ShellError("operator tree and plan differ in shape")
        depth = {}
        lines = []

        def go(n, d):
            depth[id(n)] = d
            for k in ("child", "left", "right"):
                if k in n:
                    go(n[k], d + 1)
        go(phys, 0)
        for n, op in zip(nodes, ops):
            c = op.c
            lines.append(("  " * depth[id(n)] + label(n),
                          f"est_rows={_num(planner.est.props(n).rows)}",
                          f"rows={c.tuples_out}",
                          f"est_cost={_num(planner.cost.own(n))}",
                          f"reads={c.page_reads}", f"writes={c.page_writes}"))
        widths = [max(len(l[i]) for l in lines) + 2 for i in range(6)]
        for l in lines:
            self.say("".join(f"{x:<{w}}" for x, w in zip(l, widths)).rstrip())
        t = pr.totals()
        est = planner.est.props(phys).rows
        res = QueryResult(pr.schema.names(), pr.schema.types(), rows, est, t["tuples_out"], t["page_reads"],
                          t["page_writes"], t["comparisons"], compile_ms, plan_ms,
                          exec_ms, phys)
        self.say(f"total est_cost={_num(planner.cost.total(phys))}  "
                 f"actual I/O={res.io} (reads={res.page_reads} writes={res.page_writes})  "
                 f"B={self.frames}")
        self.say(f"root: est_rows={_num(est)} rows={res.actual_rows} "
                 f"q-error={res.q_error:.2f}")
        self.say(f"time: compile {compile_ms:.1f} ms, plan {plan_ms:.1f} ms, "
                 f"execute {exec_ms:.1f} ms, total {res.wall_ms:.1f} ms")
        return res

    # ---- shell commands -----------------------------------------------------------------
    def analyze(self, tables) -> None:
        self.need_db()
        names = list(tables) or [t.name for t in self.catalog().tables()]
        known = {t.name for t in self.catalog().tables()}
        for name in names:
            if name not in known:
                raise ShellError(f"unknown table '{name}' (tables: "
                                 f"{', '.join(sorted(known)) or '(none)'})")
        for name in names:
            ts = analyze(self.db, name, self.buckets)
            self.say(f"analyzed {name}: {ts.n_tuples} rows, {ts.n_pages} pages")

    def set(self, name: str, value: str) -> None:
        if name != "frames":
            raise ShellError(f"unknown setting '{name}' (only frames)")
        if not value.isdigit() or int(value) < MIN_FRAMES:
            raise ShellError(f"frames must be a whole number of at least {MIN_FRAMES}, got {value!r}")
        self.frames = int(value)
        self.say(f"frames = {self.frames}")

    def load(self, table: str, path: str, header: bool) -> None:
        self.need_db()
        try:
            with open(path, encoding="utf-8") as f:
                lines = f.read().split("\n")
        except OSError as e:
            raise ShellError(f"cannot read '{path}': {e.strerror}") from None
        except UnicodeDecodeError:
            raise ShellError(f"'{path}' is not UTF-8 text") from None
        if header and lines:
            lines = lines[1:]
        cat = self.catalog()
        n = load_csv(cat, table, lines)
        cat.flush()
        info = cat.get(table)
        stale = " (statistics are now stale: run ANALYZE " + table + ")" if (
            info.stats or {}).get("analyzed") else ""
        self.say(f"loaded {n} rows into {table} ({info.num_tuples} rows, "
                 f"{info.num_pages} pages){stale}")

    def meta(self, cmd: Command) -> None:
        if cmd.name in ("\\d", "\\dt"):
            self.need_db()
            cat = self.catalog()
            if cmd.tables:
                for name in cmd.tables:
                    info = cat.get(name)
                    st = info.stats or {}
                    self.say(f"{info.name}: {info.num_tuples} rows, {info.num_pages} pages, "
                             f"{'analyzed' if st.get('analyzed') else 'not analyzed'}")
                    for c in info.columns:
                        t = c.type if c.type == "INT" else f"VARCHAR({c.length})"
                        self.say(f"  {c.name} {t}")
            else:
                for info in cat.tables():
                    st = info.stats or {}
                    self.say(f"{info.name}  {info.num_tuples} rows  {info.num_pages} pages  "
                             f"{'analyzed' if st.get('analyzed') else 'not analyzed'}")
            return
        if cmd.name in ("\\?", "\\h", "\\help"):
            self.say(HELP)
            return
        raise ShellError(f"unknown command {cmd.name} (\\? lists them)")


class _Empty:
    """A catalog with no tables, for CREATE TABLE in a new directory."""
    def columns(self, name):
        return None

    def names(self):
        return []


def _num(v) -> str:
    return f"{v:.0f}" if abs(v - round(v)) < 1e-9 else f"{v:.1f}"


HELP = """\
Statements end with ';' and may span lines.
  SELECT ...                     run a query (Module 3 -> 4 -> 2 -> 1)
  CREATE TABLE t (...) / INSERT INTO t VALUES (...)
  EXPLAIN SELECT ...             the chosen physical plan, estimates and costs
  EXPLAIN ANALYZE SELECT ...     run it; estimated against actual rows per node
  ANALYZE [t, ...]               compute statistics (all tables when none named)
  SET frames = n                 the memory budget B (at least 4)
  LOAD t FROM 'file.csv' [HEADER]  bulk load a CSV file into an existing table
  \\d [t]                         list tables, or describe one
  \\? help    \\q quit"""