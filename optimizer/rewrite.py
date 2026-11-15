"""Rewrite rules on the logical plan (Module 4, Section 3). DESIGN_M4.md section 4.

    1  split a conjunction into stacked selects                  (always safe)
    2  push a select below a join or cross, to the one side it uses
    3  cross + two-sided equality select -> join. Extension: a two-sided
       select directly above a join is added to the join's condition
    4  push a select below a rename (names rewritten through it) and into
       both branches of a union / union_all (names rewritten for the right)
    5  push a select below a group_by when it uses grouping keys only
    6  insert projections below joins, crosses, group_bys and sorts, keeping
       what the ancestors need plus what the operator itself uses
    7  remove projections that drop nothing and merge a projection into the
       one above it; collapse adjacent selects

Every rule can be switched off (rewrite(..., rules={...})). Rules work on
column names: a reference "e.age" must name an output column exactly, a bare
"age" names the first column whose last part is age (conds.match).

Choices (DESIGN_M4.md assumptions): rule 4 leaves a select above a rename
whose child is a plain scan, because "rename e (scan Emp)" is one access path
and pushing through it gains nothing (this is also what the Section 3
example shows). Rule 6 does not insert a projection between a rename and its
scan for the same reason.
"""
from __future__ import annotations

import copy
from typing import Dict, List, Optional, Sequence, Set

from . import conds as C

ALL_RULES = frozenset(range(1, 8))
JOINISH = ("join", "cross", "nested_loop_join", "sort_merge_join", "hash_join")
SETOPS = ("union", "union_all", "intersect", "except")


def agg_name(agg) -> str:
    if len(agg) == 3:
        return agg[2]
    fn, col = agg[0].lower(), agg[1]
    return "count_star" if col == "*" else f"{fn}_{col.replace('.', '_')}"


def resolve(ref: str, cols: Sequence[str]) -> str:
    i = C.match(ref, cols)
    if i is None:
        raise ValueError(f"column {ref!r} is not among {list(cols)}")
    return cols[i]


def cols_of(n: dict, tables: Dict[str, List[str]]) -> List[str]:
    """Output column names of a logical (or physical) node."""
    op = n["op"]
    if op in ("scan", "seqscan"):
        return [f"{n['table']}.{c}" for c in tables[n["table"]]]
    if op == "rename":
        return [f"{n['to']}.{C.short(c)}" for c in cols_of(n["child"], tables)]
    if op == "project":
        ch = cols_of(n["child"], tables)
        names = n.get("as") or [None] * len(n["columns"])
        return [a or resolve(c, ch) for c, a in zip(n["columns"], names)]
    if op in JOINISH:
        return cols_of(n["left"], tables) + cols_of(n["right"], tables)
    if op == "group_by":
        ch = cols_of(n["child"], tables)
        return [resolve(k, ch) for k in n["keys"]] + [agg_name(a) for a in n["aggregates"]]
    if op in SETOPS:
        return cols_of(n["left"], tables)
    return cols_of(n["child"], tables)


def children(n: dict) -> List[dict]:
    return [n[k] for k in ("child", "left", "right") if k in n]


def _with(n: dict, **kw) -> dict:
    out = dict(n)
    out.update(kw)
    return out


def select(cond, child) -> dict:
    return {"op": "select", "condition": cond if isinstance(cond, str) else C.to_text(cond),
            "child": child}


class Rewriter:
    def __init__(self, tables: Dict[str, List[str]], rules=ALL_RULES):
        self.tables = tables
        self.rules = frozenset(rules)
        self.fired: Set[int] = set()

    def cols(self, n):
        return cols_of(n, self.tables)

    def run(self, plan: dict) -> dict:
        p = copy.deepcopy(plan)
        if 1 in self.rules:
            p = self.split(p)
        p = self.push(p)
        if 7 in self.rules:
            p = self.collapse(p)
        if 6 in self.rules:
            p = self.prune(p, None)
        if 7 in self.rules:
            p = self.drop_noop(p)
        return p

    # ---- rule 1 ------------------------------------------------------------------
    def split(self, n: dict) -> dict:
        n = self._map_children(n, self.split)
        if n["op"] == "select":
            parts = C.conjuncts(C.parse(n["condition"]))
            if len(parts) > 1:
                self.fired.add(1)
                child = n["child"]
                for c in reversed(parts):       # first conjunct ends up on top
                    child = select(c, child)
                return child
        return n

    @staticmethod
    def _map_children(n: dict, fn) -> dict:
        out = dict(n)
        for k in ("child", "left", "right"):
            if k in n:
                out[k] = fn(n[k])
        return out

    # ---- rules 2 to 5: one bottom-up pass, each select sinks as far as it can --------
    def push(self, n: dict) -> dict:
        n = self._map_children(n, self.push)
        if n["op"] == "select":
            return self.sink(C.parse(n["condition"]), n["child"])
        return n

    def sink(self, cond, child: dict) -> dict:
        op = child["op"]
        r = self.rules
        if op in ("join", "cross"):
            lc, rc = self.cols(child["left"]), self.cols(child["right"])
            if 2 in r and C.covers(cond, lc):
                self.fired.add(2)
                return _with(child, left=self.sink(cond, child["left"]))
            if 2 in r and C.covers(cond, rc):
                self.fired.add(2)
                return _with(child, right=self.sink(cond, child["right"]))
            two_sided = not C.covers(cond, lc) and not C.covers(cond, rc)
            if 3 in r and two_sided and C.covers(cond, lc + rc):
                if op == "cross" and C.equi_pair(cond, lc, rc):
                    self.fired.add(3)
                    return {"op": "join", "condition": C.to_text(cond),
                            "left": child["left"], "right": child["right"]}
                if op == "join":
                    self.fired.add(3)
                    return _with(child, condition=C.to_text(
                        C.and_all([C.parse(child["condition"]), cond])))
        elif op == "rename" and 4 in r and child["child"]["op"] != "scan":
            out = self.cols(child)
            inner = self.cols(child["child"])
            mapped = C.map_refs(cond, lambda ref: inner[C.match(ref, out)])
            self.fired.add(4)
            return _with(child, child=self.sink(mapped, child["child"]))
        elif op in ("union", "union_all") and 4 in r:
            lc, rc = self.cols(child["left"]), self.cols(child["right"])
            mapped = C.map_refs(cond, lambda ref: rc[C.match(ref, lc)])
            self.fired.add(4)
            return _with(child, left=self.sink(cond, child["left"]),
                         right=self.sink(mapped, child["right"]))
        elif op == "group_by" and 5 in r:
            out = self.cols(child)
            keys = out[:len(child["keys"])]
            if keys and all(
                C.match(ref, out) is not None
                and out[C.match(ref, out)] in keys
                for ref in C.refs(cond)
            ):
                self.fired.add(5)
                return _with(child, child=self.sink(cond, child["child"]))
        elif op == "select":
            below = self.sink(cond, child["child"])
            if not (below["op"] == "select" and below["child"] is child["child"]):
                return _with(child, child=below)      # moved past the other select
        return select(cond, child)

    # ---- rule 7 -------------------------------------------------------------------
    def collapse(self, n: dict) -> dict:
        n = self._map_children(n, self.collapse)
        if n["op"] == "select" and n["child"]["op"] == "select":
            self.fired.add(7)
            inner = n["child"]
            return select(C.and_all([C.parse(n["condition"]), C.parse(inner["condition"])]),
                          inner["child"])
        return n

    def drop_noop(self, n: dict) -> dict:
        n = self._map_children(n, self.drop_noop)
        if n["op"] == "project" and not any(n.get("as") or []):
            if self.cols(n) == self.cols(n["child"]):
                self.fired.add(7)
                return n["child"]
        if (n["op"] == "project" and n["child"]["op"] == "project"
                and not any(n["child"].get("as") or [])):
            self.fired.add(7)                   # project over project: keep the outer
            return _with(n, child=n["child"]["child"])
        return n

    # ---- rule 6 -------------------------------------------------------------------
    def _need(self, refs, cols) -> Set[str]:
        out = set()
        for ref in refs:
            i = C.match(ref, cols)
            if i is not None:
                out.add(cols[i])
        return out

    def wrap(self, child: dict, need: Set[str]) -> dict:
        """Put a projection on child keeping only `need` (in child order)."""
        cols = self.cols(child)
        keep = [c for c in cols if c in need]
        if len(keep) == len(cols) or not keep:
            return child
        self.fired.add(6)
        if child["op"] == "project" and not any(child.get("as") or []):
            return _with(child, columns=keep)
        return {"op": "project", "columns": keep, "child": child}

    def prune(self, n: dict, need: Optional[Set[str]]) -> dict:
        op = n["op"]
        out = self.cols(n)
        if need is None:
            need = set(out)
        if op in ("scan", "seqscan"):
            return n
        if op == "project":
            ch = self.cols(n["child"])
            pairs = list(zip(n["columns"], n.get("as") or [None] * len(n["columns"]), out))
            if any(o in need for _, _, o in pairs):
                pairs = [p for p in pairs if p[2] in need]
            node = _with(n, columns=[c for c, _, _ in pairs])
            if "as" in n:
                node["as"] = [a for _, a, _ in pairs]
            return _with(node, child=self.prune(n["child"], self._need(node["columns"], ch)))
        if op in ("select", "filter"):
            ch = self.cols(n["child"])
            return _with(n, child=self.prune(n["child"], need | self._need(
                C.refs(C.parse(n["condition"])), ch)))
        if op == "rename":
            ch = self.cols(n["child"])
            return _with(n, child=self.prune(n["child"], {ch[out.index(c)] for c in need}))
        if op in JOINISH:
            lc, rc = self.cols(n["left"]), self.cols(n["right"])
            used = set(need)
            if n.get("condition"):
                used |= self._need(C.refs(C.parse(n["condition"])), lc + rc)
            ln, rn = used & set(lc), used & set(rc)
            return _with(n, left=self.wrap(self.prune(n["left"], ln), ln),
                         right=self.wrap(self.prune(n["right"], rn), rn))
        if op == "group_by":
            ch = self.cols(n["child"])
            cn = self._need(n["keys"], ch) | self._need(
                [a[1] for a in n["aggregates"] if a[1] != "*"], ch)
            if not cn:                         # COUNT(*) alone: keep one column
                cn = {ch[0]}
            return _with(n, child=self.wrap(self.prune(n["child"], cn), cn))
        if op == "sort":
            ch = self.cols(n["child"])
            cn = need | self._need([k[0] for k in n["keys"]], ch)
            return _with(n, child=self.wrap(self.prune(n["child"], cn), cn))
        if op == "limit":
            return _with(n, child=self.prune(n["child"], need))
        # distinct and set operators need every column of their inputs
        return self._map_children(n, lambda c: self.prune(c, None))


def rewrite(plan: dict, tables: Dict[str, List[str]], rules=ALL_RULES) -> dict:
    return Rewriter(tables, rules).run(plan)