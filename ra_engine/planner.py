from __future__ import annotations

from .ast_nodes import Join, Project, RelationRef, Rename, Select, SetOp, Times
from .errors import RANameError
from .operators import (JoinOp, Operator, ProjectOp, RenameOp, Scan, SelectOp,
                        SetOpOp, TimesOp)
from .relation import Relation

# ---- building the operator tree from the parse tree ------------------------------------
def plan(node, catalog) -> Operator:
    """Turn a parse tree into an operator tree, doing every static check on the way."""
    if isinstance(node, RelationRef):
        if node.name not in catalog:
            known = ", ".join(sorted(catalog)) or "(no relations loaded; use -d FILE)"
            raise RANameError(f"unknown relation '{node.name}'; known relations: {known}",
                              node.pos)
        return Scan(catalog[node.name])
    if isinstance(node, Select):
        return SelectOp(plan(node.child, catalog), node.cond,
                        label=f"select at column {node.pos.col}")
    if isinstance(node, Project):
        return ProjectOp(plan(node.child, catalog), node.attrs)
    if isinstance(node, Rename):
        return RenameOp(plan(node.child, catalog), node.new_name, node.pos)
    if isinstance(node, Times):
        return TimesOp(plan(node.left, catalog), plan(node.right, catalog),
                       node.pos)
    if isinstance(node, Join):
        return JoinOp(plan(node.left, catalog), plan(node.right, catalog),
                      node.cond, node.pos, label=f"join at column {node.pos.col}")
    if isinstance(node, SetOp):
        return SetOpOp(node.op, plan(node.left, catalog),
                       plan(node.right, catalog), node.pos)
    raise AssertionError(f"unknown node {node!r}")


def evaluate(op: Operator) -> Relation:
    return Relation(op.name, op.attributes, op.execute())
