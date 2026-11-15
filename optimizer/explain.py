"""explain output (Module 4, Section 6): one line per physical node, the
node's label, then est_rows and est_cost (the node's own cost; the plan's
total is printed last). Also the side-by-side logical trees before and after
the rewrites."""
from __future__ import annotations

from typing import List

from .cost import CostModel


def label(n: dict) -> str:
    op = n["op"]
    if op == "seqscan":
        return f"seqscan {n['table']}"
    if op == "filter":
        return f"filter ({n['condition']})"
    if op in ("nested_loop_join", "sort_merge_join", "hash_join"):
        return f"{op} ({n.get('condition', 'cross')})"
    if op == "project":
        names = n.get("as") or [None] * len(n["columns"])
        cols = [c if a is None else f"{c} as {a}" for c, a in zip(n["columns"], names)]
        return f"project [{', '.join(cols)}]"
    if op == "rename":
        return f"rename {n['to']}"
    if op == "sort":
        return "sort [" + ", ".join(f"{c} {d}" for c, d in n["keys"]) + "]"
    if op == "group_by":
        aggs = []
        for a in n["aggregates"]:
            text = f"{a[0]}({a[1]})"
            aggs.append(text + (f" as {a[2]}" if len(a) == 3 else ""))
        return f"group_by {n['mode']} [{', '.join(n['keys'])}] [{', '.join(aggs)}]"
    if op == "distinct":
        return f"distinct {n['mode']}"
    if op == "limit":
        return f"limit {n['n']}"
    return op


def explain(plan: dict, cost: CostModel) -> str:
    lines = []

    def go(n, depth):
        lines.append(("  " * depth + label(n), cost.est.props(n).rows, cost.own(n)))
        for k in ("child", "left", "right"):
            if k in n:
                go(n[k], depth + 1)
    go(plan, 0)
    w = max(len(a) for a, _, _ in lines) + 2
    wr = max(len(f"est_rows={_n(r)}") for _, r, _ in lines) + 2
    out = [f"{a:<{w}}{'est_rows=' + _n(r):<{wr}}est_cost={_n(c)}" for a, r, c in lines]
    out.append(f"total est_cost={_n(cost.total(plan))}  (B={cost.B})")
    return "\n".join(out)


def _n(v) -> str:
    return f"{v:.0f}" if abs(v - round(v)) < 1e-9 else f"{v:.1f}"


def logical_lines(n: dict, depth: int = 0) -> List[str]:
    """Compact logical tree, Section 3 style: a rename of a scan on one line."""
    op = n["op"]
    pad = "  " * depth
    if op == "rename" and n["child"]["op"] == "scan":
        return [f"{pad}rename {n['to']} (scan {n['child']['table']})"]
    if op == "scan":
        text = f"scan {n['table']}"
    elif op in ("select", "join"):
        text = f"{op} {n['condition']}"
    elif op == "project":
        names = n.get("as") or [None] * len(n["columns"])
        text = "project [" + ",".join(c if a is None else f"{c} as {a}"
                                      for c, a in zip(n["columns"], names)) + "]"
    elif op == "group_by":
        text = f"group_by [{','.join(n['keys'])}] " + str([list(a) for a in n["aggregates"]])
    elif op == "sort":
        text = "sort [" + ",".join(f"{c} {d}" for c, d in n["keys"]) + "]"
    elif op == "rename":
        text = f"rename {n['to']}"
    elif op == "limit":
        text = f"limit {n['n']}"
    else:
        text = op
    out = [pad + text]
    for k in ("child", "left", "right"):
        if k in n:
            out += logical_lines(n[k], depth + 1)
    return out


def side_by_side(before: dict, after: dict) -> str:
    a, b = logical_lines(before), logical_lines(after)
    w = max(len(x) for x in a + ["before"]) + 4
    rows = [f"{'before':<{w}}after"]
    for i in range(max(len(a), len(b))):
        rows.append(f"{a[i] if i < len(a) else '':<{w}}{b[i] if i < len(b) else ''}".rstrip())
    return "\n".join(rows)