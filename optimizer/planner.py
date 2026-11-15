"""Logical plan in, physical plan out (Module 4). DESIGN_M4.md section 7.

    1. rewrite the logical plan (rules 1-7, each can be switched off)
    2. lower it bottom-up: scan -> seqscan, select -> filter, group_by and
       distinct get the cheaper mode, and every join block (a tree of join
       and cross nodes, looking through the projections rule 6 put between
       them) is handed to the Selinger DP, which picks the order and the
       algorithm of each join
    3. run rule 6 and 7 again on the physical tree, so the reordered joins
       get their projections too

The result is a Module 2 plan: it passes execution.plan.validate and runs
with `exec run` unedited. Only Module 2 field names are emitted (plus the
four Module 4 forms in execution/plan.py).

naive() is the "unoptimized plan" of the report: no rewrites, joins in
written order, every join a nested loop join.
"""
from __future__ import annotations

from typing import Dict, List, Optional

from . import conds as C
from .cost import CostModel, is_base
from .dp import ALGOS, MAX_RELATIONS, JoinDP, Pred, Rel
from .estimate import Estimator
from .rewrite import ALL_RULES, Rewriter, cols_of
from .stats import TableStats

BLOCK = ("join", "cross")


def tables_of(stats: Dict[str, TableStats]) -> Dict[str, List[str]]:
    return {t: list(s.cols) for t, s in stats.items()}


class Planner:
    def __init__(self, stats: Dict[str, TableStats], frames: int, rules=ALL_RULES,
                 algos=ALGOS, reorder: bool = True):
        self.stats = stats
        self.tables = tables_of(stats)
        self.B = frames
        self.rules = frozenset(rules)
        self.algos = algos
        self.reorder = reorder
        self.est = Estimator(stats)
        self.cost = CostModel(self.est, frames)
        self.dps: List[JoinDP] = []
        self.rewritten: Optional[dict] = None
        self.fired = set()

    def cols(self, n):
        return cols_of(n, self.tables)

    def plan(self, logical: dict) -> dict:
        rw = Rewriter(self.tables, self.rules)
        self.rewritten = rw.run(logical)
        phys = self.lower(self.rewritten)
        if 6 in self.rules or 7 in self.rules:
            post = Rewriter(self.tables, self.rules)
            if 6 in self.rules:
                phys = post.prune(phys, None)
            if 7 in self.rules:
                phys = post.drop_noop(phys)
            rw.fired |= post.fired
        self.fired = rw.fired
        return self.finish(phys)

    def finish(self, phys: dict) -> dict:
        """Choose group_by / distinct modes on the final tree."""
        for k in ("child", "left", "right"):
            if k in phys:
                self.finish(phys[k])
        if phys["op"] in ("group_by", "distinct") and phys.get("mode") is None:
            phys["mode"] = self.cost.best_mode(phys)
        return phys

    # ---- lowering ---------------------------------------------------------------------
    def lower(self, n: dict) -> dict:
        op = n["op"]
        if op == "scan":
            return {"op": "seqscan", "table": n["table"]}
        if op == "select":
            return {"op": "filter", "condition": n["condition"], "child": self.lower(n["child"])}
        if op in BLOCK:
            return self.join_block(n)
        if op == "group_by":
            return {"op": "group_by", "mode": None, "keys": n["keys"],
                    "aggregates": n["aggregates"], "child": self.lower(n["child"])}
        if op == "distinct":
            return {"op": "distinct", "mode": None, "child": self.lower(n["child"])}
        out = {k: v for k, v in n.items() if k not in ("child", "left", "right")}
        for k in ("child", "left", "right"):
            if k in n:
                out[k] = self.lower(n[k])
        return out

    def flatten(self, n: dict, leaves: list, preds: list) -> None:
        if n["op"] in BLOCK:
            self.flatten(n["left"], leaves, preds)
            self.flatten(n["right"], leaves, preds)
            if n.get("condition"):
                c = C.parse(n["condition"])
                # rule 1 off: a join condition stays one predicate
                preds += C.conjuncts(c) if 1 in self.rules else [c]
        elif (n["op"] == "project" and not any(n.get("as") or [])
              and n["child"]["op"] in BLOCK):
            self.flatten(n["child"], leaves, preds)
        else:
            leaves.append(n)

    def join_block(self, n: dict) -> dict:
        leaves, conds = [], []
        self.flatten(n, leaves, conds)
        if not self.reorder or len(leaves) > MAX_RELATIONS:
            return self.written_order(n)
        plans = [self.lower(leaf) for leaf in leaves]
        cols = [self.cols(p) for p in plans]
        multi, above = [], []
        for c in conds:
            owners = set()
            for ref in C.refs(c):
                for i, cs in enumerate(cols):
                    if C.match(ref, cs) is not None:
                        owners.add(i)
                        break
            if len(owners) > 1:
                multi.append(Pred(c, frozenset(owners)))
            elif len(owners) == 1 and 2 in self.rules:
                # a one-sided conjunct of a join condition (JOIN ON e.did = d.did
                # AND d.budget > 5): rule 2 filters that relation first
                i = owners.pop()
                plans[i] = {"op": "filter", "condition": C.to_text(c), "child": plans[i]}
            else:
                above.append(c)            # rule 2 off, or a constant: after the joins
        rels = []
        for i, (leaf, p) in enumerate(zip(leaves, plans)):
            props = self.est.props(p)
            rels.append(Rel(_label(leaf, i), p, props.cols, props.rows, props.pages,
                            self.cost.total(p), is_base(p), self.cost.scan_pages(p)))
        preds = multi
        dp = JoinDP(rels, preds, self.B, self.est, algos=self.algos)
        dp.run()
        self.dps.append(dp)
        tree = self.build(dp, frozenset(range(len(rels))))
        if above:
            tree = {"op": "filter", "condition": C.to_text(C.and_all(above)), "child": tree}
        want = self.cols(n)
        if self.cols(tree) != want:
            tree = {"op": "project", "columns": want, "child": tree}
        return tree

    def build(self, dp: JoinDP, S) -> dict:
        if len(S) == 1:
            return dp.rels[next(iter(S))].plan
        e = dp.best[S]
        left = self.build(dp, e.outer)
        right = dp.rels[e.inner].plan
        conds = [p.cond for p in e.preds]
        if not conds:
            return {"op": "nested_loop_join", "left": left, "right": right}
        if e.algo == "nested_loop_join":
            return {"op": e.algo, "condition": C.to_text(C.and_all(conds)),
                    "left": left, "right": right}
        lc, rc = self.cols(left), self.cols(right)
        equi = [c for c in conds if C.equi_pair(c, lc, rc)]
        rest = [c for c in conds if not C.equi_pair(c, lc, rc)]
        # write each equality left column first, as Module 2's equi_keys prefers
        keys = []
        for c in equi:
            a, b = C.equi_pair(c, lc, rc)
            keys.append(C.parse(f"{a} = {b}"))
        node = {"op": e.algo, "condition": C.to_text(C.and_all(keys)), "left": left, "right": right}
        if rest:
            node = {"op": "filter", "condition": C.to_text(C.and_all(rest)), "child": node}
        return node

    def written_order(self, n: dict, algo: str = "nested_loop_join") -> dict:
        l, r = self.lower(n["left"]), self.lower(n["right"])
        if n["op"] == "cross" or not n.get("condition"):
            return {"op": "nested_loop_join", "left": l, "right": r}
        return {"op": algo, "condition": n["condition"], "left": l, "right": r}


def _label(leaf: dict, i: int) -> str:
    n = leaf
    while n["op"] in ("select", "project"):
        n = n["child"]
    if n["op"] == "rename":
        return n["to"]
    if n["op"] == "scan":
        return n["table"]
    return f"R{i + 1}"


def optimize(logical: dict, stats: Dict[str, TableStats], frames: int, **kw) -> dict:
    return Planner(stats, frames, **kw).plan(logical)


def naive(logical: dict, stats: Dict[str, TableStats], frames: int) -> dict:
    """Unoptimized: no rewrite rules, joins as written, all nested loop."""
    p = Planner(stats, frames, rules=(), reorder=False)
    p.rewritten = logical
    phys = p.lower(logical)
    return p.finish(phys)