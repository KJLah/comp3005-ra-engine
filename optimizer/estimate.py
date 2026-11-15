"""Selectivity and cardinality estimates (Module 4, Section 2). DESIGN_M4.md section 3.

The Estimator annotates any plan node, logical (scan, select, join, cross)
or physical (seqscan, filter, the three joins), with a Props:

    cols    output column names, in order
    origin  output column -> (base table, base column), for statistics
    ndv     output column -> estimated distinct values (never above rows)
    colw    output column -> average bytes
    rows    estimated output tuples
    pages   estimated output pages

Rules (Section 2): rows = ceil(input rows x selectivity), never below 1;
pages = ceil(rows x width / 4096). A base table's rows and pages are its
statistics, not the formula.

Selectivities implemented:
    c = v        histogram bucket holding v: count / (n_tuples x ndv(bucket));
                 no histogram: 1/ndv; v outside [min, max]: 0
    c < v, c > v whole buckets on that side plus a linear fraction of the
                 bucket holding v; <= and >= add sel(c = v). No histogram:
                 linear between min and max. VARCHAR values are interpolated
                 by mapping a string to a number (improvement 1)
    c <> v       1 - sel(c = v)
    p and q      sel(p) x sel(q)            (independence)
    p or q       sel(p) + sel(q) - sel(p) x sel(q)
    not p        1 - sel(p)
    a = b        two columns, in a select or a join: 1 / max(ndv(a), ndv(b))
                 (improvement 2: also used when both are of the same table)
Distinct values after a select (improvement 3): a column compared with a
constant keeps ndv x sel of that comparison (1 for c = v), so a group_by or
a join above a range filter sees fewer distinct values.
    other        two columns with any other comparison: 0.1
Defaults without statistics: ndv = n/10 (stats.defaults_for); c = v is then
1/10 and a range 1/3 when min and max are unknown, and 0.1 for anything the
rules above do not cover (an aggregate's output, a constant comparison).
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple

from ra_engine.ast_nodes import And, Attr, Compare, Not, Or

from . import conds as C
from .stats import ColStats, TableStats

PAGE = 4096
DEFAULT_EQ = 0.1
DEFAULT_RANGE = 1 / 3
DEFAULT_OTHER = 0.1

SCANS = ("scan", "seqscan")
SELECTS = ("select", "filter")
JOINS = ("join", "cross", "nested_loop_join", "sort_merge_join", "hash_join")


@dataclass
class Props:
    cols: List[str]
    origin: Dict[str, Tuple[str, str]]
    ndv: Dict[str, float]
    colw: Dict[str, float]
    rows: float
    pages: int = 0
    sel: Optional[float] = None          # of a select/filter or a join's condition

    @property
    def width(self) -> float:
        return sum(self.colw[c] for c in self.cols)


def card(x: float) -> int:
    """Rows of a derived node: rounded up, never below 1."""
    return max(1, math.ceil(x - 1e-9))


def pages_for(rows: float, width: float) -> int:
    return math.ceil(rows * width / PAGE - 1e-9) if rows > 0 else 0


def str_num(v: str) -> float:
    """A string as a number that keeps the string order (first 8 characters,
    base 256), so VARCHAR ranges can be interpolated like INT ones."""
    x, scale = 0.0, 1.0
    for ch in v[:8]:
        x += min(ord(ch), 255) * scale
        scale /= 256
    return x


def num(v) -> float:
    return float(v) if isinstance(v, (int, float)) else str_num(v)


def _less(a, b) -> bool:
    if isinstance(a, str) != isinstance(b, str):
        return num(a) < num(b)
    return a < b


class Estimator:
    def __init__(self, stats: Dict[str, TableStats]):
        self.stats = stats
        # id(node) -> (node, props). Keeping the node itself in the memo keeps
        # it alive, so its id cannot be reused by a later node.
        self.memo: Dict[int, Tuple[dict, Props]] = {}

    # ---- plan nodes -------------------------------------------------------------
    def props(self, n: dict) -> Props:
        hit = self.memo.get(id(n))
        if hit is None or hit[0] is not n:
            hit = (n, self._props(n))
            self.memo[id(n)] = hit
        return hit[1]

    def rows(self, n: dict) -> int:
        return self.props(n).rows

    def _props(self, n: dict) -> Props:
        op = n["op"]
        if op in SCANS:
            ts = self.stats[n["table"]]
            cols = [f"{ts.name}.{c}" for c in ts.cols]
            p = Props(cols, {f"{ts.name}.{c}": (ts.name, c) for c in ts.cols},
                      {f"{ts.name}.{c}": min(cs.ndv, max(ts.n_tuples, 1)) for c, cs in ts.cols.items()},
                      {f"{ts.name}.{c}": cs.width for c, cs in ts.cols.items()},
                      ts.n_tuples, ts.n_pages)
            return p
        if op == "rename":
            ch = self.props(n["child"])
            new = [f"{n['to']}.{C.short(c)}" for c in ch.cols]
            m = dict(zip(ch.cols, new))
            return Props(new, {m[k]: v for k, v in ch.origin.items()},
                         {m[k]: v for k, v in ch.ndv.items()},
                         {m[k]: v for k, v in ch.colw.items()}, ch.rows, ch.pages)
        if op in SELECTS:
            ch = self.props(n["child"])
            cond = C.parse(n["condition"])
            s = self.sel(cond, ch)
            p = self._derive(ch, ch.cols, card(ch.rows * s))
            for c in C.conjuncts(cond):
                # improvement 3: a column compared with a constant keeps only
                # its share of distinct values (c = v leaves exactly one)
                if isinstance(c, Compare) and isinstance(c.left, Attr) != isinstance(c.right, Attr):
                    a = c.left if isinstance(c.left, Attr) else c.right
                    i = C.match(a.display(), p.cols)
                    if i is not None:
                        col = p.cols[i]
                        keep = 1 if c.op == "=" else ch.ndv[col] * self._compare(c, ch)
                        p.ndv[col] = max(1, min(p.ndv[col], math.ceil(keep)))
            p.sel = s
            return p
        if op == "project":
            ch = self.props(n["child"])
            src = [ch.cols[self._idx(c, ch)] for c in n["columns"]]
            names = n.get("as") or [None] * len(src)
            out = [a or s for s, a in zip(src, names)]
            p = Props(out, {o: ch.origin[s] for o, s in zip(out, src) if s in ch.origin},
                      {o: ch.ndv[s] for o, s in zip(out, src)},
                      {o: ch.colw[s] for o, s in zip(out, src)}, ch.rows)
            p.pages = pages_for(p.rows, p.width)
            return p
        if op in JOINS:
            return self._join(n)
        if op == "group_by":
            ch = self.props(n["child"])
            keys = [ch.cols[self._idx(k, ch)] for k in n["keys"]]
            groups = 1.0
            for k in keys:
                groups *= ch.ndv[k]
            rows = card(min(groups, ch.rows)) if keys else 1
            p = Props(list(keys), {k: ch.origin[k] for k in keys if k in ch.origin},
                      {k: min(ch.ndv[k], rows) for k in keys}, {k: ch.colw[k] for k in keys}, rows)
            for agg in n["aggregates"]:
                fn, col = agg[0].lower(), agg[1]
                name = agg[2] if len(agg) == 3 else (
                    "count_star" if col == "*" else f"{fn}_{col.replace('.', '_')}")
                p.cols.append(name)
                p.ndv[name] = rows
                if fn in ("min", "max") and col != "*":
                    src = ch.cols[self._idx(col, ch)]
                    p.colw[name] = ch.colw[src]
                else:
                    p.colw[name] = 8.0 if fn in ("avg", "sum") else 4.0
            p.pages = pages_for(p.rows, p.width)
            return p
        if op == "distinct":
            ch = self.props(n["child"])
            groups = 1.0
            for c in ch.cols:
                groups *= ch.ndv[c]
            return self._derive(ch, ch.cols, card(min(groups, ch.rows)))
        if op == "sort":
            ch = self.props(n["child"])
            return self._derive(ch, ch.cols, ch.rows, keep_pages=True)
        if op == "limit":
            ch = self.props(n["child"])
            return self._derive(ch, ch.cols, max(0, min(n["n"], ch.rows)))
        if op in ("union_all", "union", "intersect", "except"):
            l, r = self.props(n["left"]), self.props(n["right"])
            rows = {"union_all": l.rows + r.rows, "union": l.rows + r.rows,
                    "intersect": min(l.rows, r.rows), "except": l.rows}[op]
            p = self._derive(l, l.cols, rows)
            for a, b in zip(l.cols, r.cols):
                p.ndv[a] = min(rows, l.ndv[a] + (r.ndv[b] if op.startswith("union") else 0)) or 1
            return p
        raise ValueError(f"cannot estimate op {op!r}")

    def _idx(self, ref: str, p: Props) -> int:
        i = C.match(ref, p.cols)
        if i is None:
            raise ValueError(f"column {ref!r} is not among {p.cols}")
        return i

    def _derive(self, ch: Props, cols, rows, keep_pages=False) -> Props:
        p = Props(list(cols), dict(ch.origin), {c: min(ch.ndv[c], max(rows, 1)) for c in cols},
                  {c: ch.colw[c] for c in cols}, rows)
        p.pages = ch.pages if keep_pages else pages_for(rows, p.width)
        return p

    def _join(self, n: dict) -> Props:
        l, r = self.props(n["left"]), self.props(n["right"])
        both = Props(l.cols + r.cols, {**l.origin, **r.origin}, {**l.ndv, **r.ndv},
                     {**l.colw, **r.colw}, l.rows * r.rows)
        s = 1.0
        if n.get("condition"):
            s = self.join_sel(C.parse(n["condition"]), l, r)
        rows = card(l.rows * r.rows * s) if l.rows and r.rows else 0
        p = self._derive(both, both.cols, rows)
        p.sel = s
        return p

    # ---- selectivity --------------------------------------------------------------
    def join_sel(self, cond, l: Props, r: Props) -> float:
        """Each conjunct of a join condition: a = b across the inputs is
        1/max(ndv); a one-sided conjunct uses the select formulas; any
        other two-sided conjunct is 0.1."""
        both = Props(l.cols + r.cols, {**l.origin, **r.origin}, {**l.ndv, **r.ndv},
                     {**l.colw, **r.colw}, l.rows * r.rows)
        s = 1.0
        for c in C.conjuncts(cond):
            s *= self.sel(c, both)
        return s

    def col_stats(self, ref: str, p: Props) -> Tuple[Optional[str], Optional[ColStats], Optional[TableStats]]:
        i = C.match(ref, p.cols)
        if i is None:
            return None, None, None
        name = p.cols[i]
        if name not in p.origin:
            return name, None, None
        t, c = p.origin[name]
        ts = self.stats[t]
        return name, ts.cols[c], ts

    def sel(self, c, p: Props) -> float:
        if isinstance(c, And):
            return self.sel(c.left, p) * self.sel(c.right, p)
        if isinstance(c, Or):
            a, b = self.sel(c.left, p), self.sel(c.right, p)
            return a + b - a * b
        if isinstance(c, Not):
            return 1 - self.sel(c.operand, p)
        return self._compare(c, p)

    def _compare(self, c: Compare, p: Props) -> float:
        la, ra = isinstance(c.left, Attr), isinstance(c.right, Attr)
        if la and ra:
            if c.op == "=":
                na = p.ndv.get(p.cols[self._idx(c.left.display(), p)], 10)
                nb = p.ndv.get(p.cols[self._idx(c.right.display(), p)], 10)
                return 1 / max(na, nb, 1)
            if c.op == "!=":
                return 1 - self._compare(Compare("=", c.left, c.right, c.pos), p)
            return DEFAULT_OTHER
        if not la and not ra:
            return DEFAULT_OTHER
        if la:
            ref, op, v = c.left.display(), c.op, c.right.value
        else:
            ref, op, v = c.right.display(), C.FLIP[c.op], c.left.value
        name, cs, ts = self.col_stats(ref, p)
        if cs is None:                     # an aggregate or computed column
            return {"=": DEFAULT_EQ, "!=": 1 - DEFAULT_EQ}.get(op, DEFAULT_RANGE)
        if op == "=":
            return self.eq(cs, ts, v)
        if op == "!=":
            return 1 - self.eq(cs, ts, v)
        if op in ("<", "<="):
            s = self.side(cs, ts, v, below=True)
        else:
            s = self.side(cs, ts, v, below=False)
        if op in ("<=", ">="):
            s += self.eq(cs, ts, v)
        return min(1.0, max(0.0, s))

    @staticmethod
    def eq(cs: ColStats, ts: TableStats, v) -> float:
        if not cs.exact:
            return DEFAULT_EQ
        if cs.min is not None and (_less(v, cs.min) or _less(cs.max, v)):
            return 0.0
        if cs.hist and ts.n_tuples:
            for lo, hi, cnt, nd in cs.hist:
                if not _less(v, lo) and not _less(hi, v):
                    return cnt / (ts.n_tuples * nd)
            return 0.0                     # between two buckets: no such value
        return 1 / max(cs.ndv, 1)

    @staticmethod
    def side(cs: ColStats, ts: TableStats, v, below: bool) -> float:
        """Fraction of tuples with c < v (below) or c > v, treating values as
        continuous inside a bucket (or between min and max), as Section 2's
        example does: age > 60 over 20..70 is 10/50."""
        if cs.min is None:
            return DEFAULT_RANGE
        x = num(v)
        def part(lo, hi):              # share of [lo, hi] on the wanted side of v
            a, b = num(lo), num(hi)
            if b <= a:
                return 0.0
            f = (x - a) / (b - a)
            return f if below else 1 - f
        if below and not _less(cs.min, v) or not below and not _less(v, cs.max):
            return 0.0
        if below and _less(cs.max, v) or not below and _less(v, cs.min):
            return 1.0
        if not cs.hist or not ts.n_tuples:
            return part(cs.min, cs.max)
        total = 0.0
        for lo, hi, cnt, nd in cs.hist:
            if (_less(hi, v) if below else _less(v, lo)):
                total += cnt
            elif _less(lo, v) and _less(v, hi):        # v strictly inside
                total += cnt * part(lo, hi)
        return total / ts.n_tuples