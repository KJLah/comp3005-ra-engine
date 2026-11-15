"""Join enumeration: Selinger-style dynamic programming over left-deep trees
(Module 4, Section 5). DESIGN_M4.md section 6.

    best[{r}] = r's access path (already planned: scan, filters, projections)
    for k = 2 .. n, for every subset S with |S| = k:
        best[S] = min over r in S and over each algorithm A of
                  cost(best[S - {r}]  A  r)        r is always the inner (right)
        a pairing with no predicate between S - {r} and r is skipped, unless
        the query's join graph is disconnected (then a cross product is the
        only way to finish, and unconnected pairings are allowed)

Algorithms: nested_loop_join (any predicate, or none), and sort_merge_join
and hash_join when at least one predicate between the two sides is an
equality across them. Predicates the equi-join cannot evaluate go in a filter
directly above it.

Sizes. rows(S) = product of the relations' rows x the selectivity of every
predicate inside S (estimate.Estimator.sel), pages(S) = ceil(rows x width /
4096) with width the sum of the relations' widths. A test can replace
size(S) to reproduce the Section 5.1 table, which gives those sizes.
Ties are broken by the order the candidates are generated in (inner r in
relation order, then nested loop, sort-merge, hash), so the result is
deterministic and does not depend on how the query wrote its FROM list
beyond the order of the relations.
Up to MAX_RELATIONS relations; a larger block keeps the written order.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from itertools import combinations
from typing import Callable, Dict, FrozenSet, List, Optional, Tuple

from . import conds as C
from .cost import blocks, join_terms
from .estimate import Estimator, Props, card, pages_for

MAX_RELATIONS = 6
ALGOS = ("nested_loop_join", "sort_merge_join", "hash_join")
SHORT = {"nested_loop_join": "BNL", "sort_merge_join": "SMJ", "hash_join": "hash"}


@dataclass
class Rel:
    name: str              # label for the table: the alias or table name
    plan: dict             # physical access path
    cols: List[str]
    rows: float
    pages: float
    cost: float            # total cost of the access path
    base: bool             # a pipeline over one base table
    scan: float            # pages one rescan reads


@dataclass
class Pred:
    cond: object           # condition tree
    rels: FrozenSet[int]


@dataclass
class Entry:
    cost: float
    rows: float
    pages: float
    outer: Optional[FrozenSet[int]] = None
    inner: Optional[int] = None
    algo: Optional[str] = None
    expr: str = ""
    preds: List[Pred] = field(default_factory=list)


class JoinDP:
    def __init__(self, rels: List[Rel], preds: List[Pred], frames: int,
                 est: Optional[Estimator] = None,
                 size: Optional[Callable[[FrozenSet[int]], Tuple[float, float]]] = None,
                 algos=ALGOS):
        self.rels, self.preds, self.B = rels, preds, frames
        self.est = est
        self.size = size or self._size
        self.algos = algos
        self.best: Dict[FrozenSet[int], Entry] = {}
        self.cands: Dict[FrozenSet[int], List[Entry]] = {}
        self.skipped: List[FrozenSet[int]] = []

    # ---- sizes -----------------------------------------------------------------------
    def _size(self, S: FrozenSet[int]) -> Tuple[float, float]:
        rows, width = 1.0, 0.0
        for i in sorted(S):
            r = self.rels[i]
            p = self.est.props(r.plan)
            rows *= p.rows
            width += p.width
        if len(S) == 1:
            i = next(iter(S))
            return self.rels[i].rows, self.rels[i].pages
        both = self._props(S)
        for p in self.preds:
            if p.rels <= S:
                rows *= self.est.sel(p.cond, both)
        rows = card(rows)
        return rows, pages_for(rows, width)

    def _props(self, S) -> Props:
        ps = [self.est.props(self.rels[i].plan) for i in sorted(S)]
        out = Props([], {}, {}, {}, 1)
        for p in ps:
            out.cols += p.cols
            out.origin.update(p.origin)
            out.ndv.update(p.ndv)
            out.colw.update(p.colw)
        return out

    # ---- the enumeration ----------------------------------------------------------------
    def connected(self) -> bool:
        n = len(self.rels)
        seen, todo = {0}, [0]
        while todo:
            i = todo.pop()
            for p in self.preds:
                if i in p.rels:
                    for j in p.rels:
                        if j not in seen:
                            seen.add(j)
                            todo.append(j)
        return len(seen) == n

    def linking(self, S: FrozenSet[int], r: int) -> List[Pred]:
        """Predicates evaluable at the join of S with r, and not before."""
        return [p for p in self.preds if r in p.rels and p.rels <= S | {r}
                and len(p.rels) > 1]

    def run(self) -> Entry:
        n = len(self.rels)
        allow_cross = not self.connected()
        for i, r in enumerate(self.rels):
            S = frozenset([i])
            self.best[S] = Entry(r.cost, r.rows, r.pages)
        for k in range(2, n + 1):
            for combo in combinations(range(n), k):
                S = frozenset(combo)
                cands = []
                for r in combo:
                    outer = S - {r}
                    if outer not in self.best:
                        continue
                    links = self.linking(outer, r)
                    if not links and not allow_cross:
                        continue
                    cands += self._candidates(S, outer, r, links)
                if not cands:
                    self.skipped.append(S)
                    continue
                self.cands[S] = cands
                self.best[S] = min(cands, key=lambda e: e.cost)
        return self.best[frozenset(range(n))]

    def _candidates(self, S, outer, r, links) -> List[Entry]:
        o, rel = self.best[outer], self.rels[r]
        rows, pages = self.size(S)
        outer_base = len(outer) == 1 and self.rels[next(iter(outer))].base
        has_equi = any(self._equi(p, outer, r) for p in links)
        out = []
        for algo in self.algos:
            if algo != "nested_loop_join" and not has_equi:
                continue
            own = join_terms(algo, o.pages, rel.pages, self.B, outer_base, rel.base, rel.scan)
            cost = o.cost + sum(v for _, v in own) + rel.cost
            parts = [_fmt(o.cost)]
            if algo == "nested_loop_join" and rel.base and rel.cost == rel.scan:
                # Section 5.1 style: outer + [intermediate pages] + blocks x inner
                if not outer_base:
                    parts.append(_fmt(o.pages))
                parts.append(f"{blocks(o.pages, self.B)}\u00d7{_fmt(rel.scan)}")
            else:
                parts += [_fmt(v) for _, v in own if v] + [_fmt(rel.cost)]
            out.append(Entry(cost, rows, pages, outer, r, algo, " + ".join(parts), links))
        return out

    def _equi(self, p: Pred, outer, r) -> bool:
        lc = [c for i in outer for c in self.rels[i].cols]
        return C.equi_pair(p.cond, lc, self.rels[r].cols) is not None

    # ---- printing ---------------------------------------------------------------------
    def label(self, S) -> str:
        return "{" + ",".join(self.rels[i].name for i in sorted(S)) + "}"

    def describe(self, e: Entry) -> str:
        o = self.label(e.outer)
        o = o[1:-1] if len(e.outer) == 1 else o
        pages = "" if len(e.outer) == 1 else f" ({_fmt(self.best[e.outer].pages)} pages)"
        return f"{o} outer{pages}, {SHORT[e.algo]} with {self.rels[e.inner].name} inner, {e.expr}"

    def table(self) -> str:
        """The dp-table output: one line per subset, smallest first, the best
        plan and its cost; skipped subsets show a dash. For the full set, the
        best plan of every other split is listed as an alternative."""
        rows = [("Subset", "Best plan considered", "Cost")]
        n = len(self.rels)
        for k in range(1, n + 1):
            for combo in combinations(range(n), k):
                S = frozenset(combo)
                if k == 1:
                    r = self.rels[combo[0]]
                    rows.append((self.label(S), _access(r), _fmt(r.cost)))
                elif S in self.best:
                    e = self.best[S]
                    rows.append((self.label(S), self.describe(e), _fmt(e.cost)))
                    if k == n:
                        seen = {e.outer}
                        for alt in sorted(self.cands[S], key=lambda x: x.cost):
                            if alt.outer not in seen:
                                seen.add(alt.outer)
                                rows.append(("", "alternative, " + self.describe(alt),
                                             _fmt(alt.cost)))
                else:
                    rows.append((self.label(S), "no predicate, skipped", "-"))
        w0 = max(len(r[0]) for r in rows)
        w1 = max(len(r[1]) for r in rows)
        return "\n".join(f"{a:<{w0}}  {b:<{w1}}  {c}".rstrip() for a, b, c in rows)


def _fmt(v: float) -> str:
    return f"{v:.0f}" if abs(v - round(v)) < 1e-9 else f"{v:.1f}"


def _access(r: Rel) -> str:
    ops = []
    n = r.plan
    while True:
        if n["op"] == "seqscan":
            ops.append(f"scan {n['table']}")
            break
        if n["op"] in ("filter",):
            ops.append("filter")
        if "child" not in n:
            break
        n = n["child"]
        if n["op"] not in ("filter", "project", "rename", "seqscan"):
            ops.append(n["op"])
            break
    return " + ".join(reversed(ops)) if len(ops) > 1 else ops[0] if ops else r.plan["op"]