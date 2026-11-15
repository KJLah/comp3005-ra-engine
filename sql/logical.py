"""Logical plans (Module 3, Section 5): JSON shape, explain tree, validator.

A logical plan is plain JSON-compatible data: nested dicts with an "op" field,
built by the constructors below so every node has the same key order. Field
names follow the handout's Section 5 examples where it shows a node, and the
Module 2 document's Section 2 where it does not (DESIGN_M3.md section 5):

    {"op":"scan",      "table":T}
    {"op":"rename",    "to":A,                          "child":P}
    {"op":"select",    "condition":C,                   "child":P}
    {"op":"project",   "columns":[...], ["as":[...]],   "child":P}
    {"op":"join",      "condition":C,                   "left":P, "right":P}
    {"op":"cross",                                      "left":P, "right":P}
    {"op":"group_by",  "keys":[...], "aggregates":[[fn,col,name],...], "child":P}
    {"op":"distinct",                                   "child":P}
    {"op":"sort",      "keys":[[col,"asc"|"desc"],...], "child":P}
    {"op":"limit",     "n":N,                           "child":P}
    {"op":"union"|"union_all"|"intersect"|"except",     "left":P, "right":P}

"as" appears on a project only when the select list gave a plain column an
alias; it is parallel to "columns" with null where no alias was written.
A logical plan never names a physical algorithm and never carries a mode.
"""
from __future__ import annotations

import json
from typing import Dict, List, Optional, Set

LOGICAL_OPS = ("scan", "select", "project", "join", "cross", "rename", "group_by",
               "distinct", "sort", "limit", "union", "union_all", "intersect", "except")
SET_OPS = ("union", "union_all", "intersect", "except")
PHYSICAL_OPS = ("seqscan", "filter", "nested_loop_join", "sort_merge_join",
                "hash_join", "index_scan")

Plan = Dict


# ---- constructors ---------------------------------------------------------------
def scan(table: str) -> Plan:
    return {"op": "scan", "table": table}


def rename(to: str, child: Plan) -> Plan:
    return {"op": "rename", "to": to, "child": child}


def select(condition: str, child: Plan) -> Plan:
    return {"op": "select", "condition": condition, "child": child}


def project(columns: List[str], child: Plan, aliases: Optional[List[Optional[str]]] = None) -> Plan:
    node = {"op": "project", "columns": list(columns)}
    if aliases is not None and any(a is not None for a in aliases):
        node["as"] = list(aliases)
    node["child"] = child
    return node


def join(condition: str, left: Plan, right: Plan) -> Plan:
    return {"op": "join", "condition": condition, "left": left, "right": right}


def cross(left: Plan, right: Plan) -> Plan:
    return {"op": "cross", "left": left, "right": right}


def group_by(keys: List[str], aggregates: List[List[str]], child: Plan) -> Plan:
    return {"op": "group_by", "keys": list(keys),
            "aggregates": [list(a) for a in aggregates], "child": child}


def distinct(child: Plan) -> Plan:
    return {"op": "distinct", "child": child}


def sort(keys: List[List[str]], child: Plan) -> Plan:
    return {"op": "sort", "keys": [list(k) for k in keys], "child": child}


def limit(n: int, child: Plan) -> Plan:
    return {"op": "limit", "n": n, "child": child}


def setop(op: str, left: Plan, right: Plan) -> Plan:
    assert op in SET_OPS, op
    return {"op": op, "left": left, "right": right}


def children(node: Plan) -> List[Plan]:
    return [node[f] for f in ("child", "left", "right") if f in node]


def walk(node: Plan):
    yield node
    for c in children(node):
        yield from walk(c)


def to_json(plan: Plan, indent: Optional[int] = 2) -> str:
    return json.dumps(plan, indent=indent)


# ---- explain ---------------------------------------------------------------------
def _j(v) -> str:
    return json.dumps(v)


def explain(plan: Plan) -> str:
    """Indented tree, two spaces per level, one node per line, root first.

    Every node shows its op name, so union and union_all (and distinct) are
    visibly different lines."""
    out: List[str] = []

    def go(n: Plan, depth: int) -> None:
        op = n["op"]
        if op == "scan":
            text = f"scan {n['table']}"
        elif op == "rename":
            text = f"rename {n['to']}"
        elif op in ("select", "join"):
            text = f"{op} {_j(n['condition'])}"
        elif op == "project":
            text = f"project {_j(n['columns'])}"
            if "as" in n:
                text += f" as {_j(n['as'])}"
        elif op == "group_by":
            text = f"group_by keys {_j(n['keys'])} aggregates {_j(n['aggregates'])}"
        elif op == "sort":
            text = f"sort {_j(n['keys'])}"
        elif op == "limit":
            text = f"limit {n['n']}"
        else:
            text = op
        out.append("  " * depth + text)
        for c in children(n):
            go(c, depth + 1)

    go(plan, 0)
    return "\n".join(out)


# ---- checklist validator -----------------------------------------------------------
def _condition_columns(text: str):
    from . import ast as A
    from .parser import parse_condition
    cond = parse_condition(text)
    for o in A.condition_operands(cond):
        if isinstance(o, A.AggCall):
            yield None, o.text()
        elif isinstance(o, A.ColumnRef):
            yield o.table, o.name


def _produced(node: Plan) -> Set[str]:
    """Unqualified output names created somewhere in this subtree: aggregate
    outputs of group_by and select-list aliases on project."""
    names: Set[str] = set()
    for n in walk(node):
        if n["op"] == "group_by":
            names.update(a[2] for a in n["aggregates"])
        if n["op"] == "project" and "as" in n:
            names.update(a for a in n["as"] if a is not None)
    return names


def check_plan(plan: Plan) -> List[str]:
    """Return the checklist violations of a plan (empty list: none).

    * every op is a logical op (so no physical operator name appears),
    * every column reference is fully qualified, except output names that a
      group_by or project in the subtree below created (aggregate outputs and
      select-list aliases),
    * no aggregate call survives inside a condition string.
    """
    problems: List[str] = []

    def ref_ok(name: str, below: Set[str]) -> bool:
        return "." in name or name in below

    def go(n: Plan, path: str) -> None:
        op = n.get("op")
        if op not in LOGICAL_OPS:
            problems.append(f"{path}: {op!r} is not a logical operator")
            return
        below = set()
        for c in children(n):
            below |= _produced(c)
        cols: List[str] = []
        if op == "project":
            cols = n["columns"]
        elif op == "sort":
            cols = [k[0] for k in n["keys"]]
        elif op == "group_by":
            cols = list(n["keys"]) + [a[1] for a in n["aggregates"] if a[1] != "*"]
        for c in cols:
            if not ref_ok(c, below):
                problems.append(f"{path} ({op}): unqualified column {c!r}")
        if op in ("select", "join"):
            for q, name in _condition_columns(n["condition"]):
                if q is None and "(" in name:
                    problems.append(f"{path} ({op}): aggregate {name} in a condition")
                elif q is None and name not in below:
                    problems.append(f"{path} ({op}): unqualified column {name!r}")
        for f in ("child", "left", "right"):
            if f in n:
                go(n[f], f"{path}.{f}")

    go(plan, "plan")
    return problems