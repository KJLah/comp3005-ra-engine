"""Run a physical plan with the Module 2 executor and pair every plan node
with its operator's counters, so estimates can be compared with actuals."""
from __future__ import annotations

import json
from dataclasses import dataclass
from typing import List, Tuple

from execution.base import rows_of
from execution.context import ExecContext
from execution.plan import prepare


@dataclass
class Run:
    rows: list
    names: List[str]
    nodes: List[Tuple[dict, dict]]      # (plan node, counters), pre-order

    @property
    def io(self) -> int:
        return sum(c["page_reads"] + c["page_writes"] for _, c in self.nodes)


def preorder(n: dict) -> List[dict]:
    out = [n]
    for k in ("child", "left", "right"):
        if k in n:
            out += preorder(n[k])
    return out


def run(db_dir: str, plan: dict, frames: int) -> Run:
    ctx = ExecContext(db_dir, frames)
    root = prepare(json.loads(json.dumps(plan)), ctx)
    rows = []
    try:
        root.open(ctx.free())
        rows = list(rows_of(root))
    finally:
        root.close()
        ctx.cleanup()
    ops = [op for _, op in root.walk()]
    nodes = preorder(plan)
    if len(ops) != len(nodes):
        raise AssertionError("operator tree and plan differ in shape")
    return Run(rows, root.schema.names(), [(n, op.counters()) for n, op in zip(nodes, ops)])