from __future__ import annotations

from .ast_nodes import Join, Project, RelationRef, Rename, Select, SetOp, Times, Distinct
from .errors import RANameError
from .operators import (JoinOp, Operator, ProjectOp, RenameOp, Scan, SelectOp,
                        SetOpOp, TimesOp, DistinctOp)
from .relation import Relation
from .conditions import qualify_condition, qualify_operand

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
        return SetOpOp(
            node.op,
            plan(node.left, catalog),
            plan(node.right, catalog),
            node.pos,
        )

    if isinstance(node, Distinct):
        return DistinctOp(plan(node.child, catalog))

    raise AssertionError(f"unknown node {node!r}")

def qualify(node, catalog):
    """Return a copy of the parse tree where every attribute is Table.column.

    Bare names are resolved against the input schema (an ambiguous or
    unknown name raises RANameError, as before). Planning a subtree reads no
    tuples, so using plan() here only to learn schemas is cheap.
    """
    if isinstance(node, RelationRef):
        return node
    if isinstance(node, Select):
        child = qualify(node.child, catalog)
        attrs = plan(child, catalog).attributes
        return Select(qualify_condition(node.cond, attrs), child, node.pos)
    if isinstance(node, Project):
        child = qualify(node.child, catalog)
        attrs = plan(child, catalog).attributes
        return Project([qualify_operand(a, attrs) for a in node.attrs],
                       child, node.pos)
    if isinstance(node, Rename):
        return Rename(node.new_name, qualify(node.child, catalog), node.pos)
    if isinstance(node, Distinct):
        return Distinct(qualify(node.child, catalog), node.pos)
    if isinstance(node, Times):
        return Times(qualify(node.left, catalog), qualify(node.right, catalog),
                     node.pos)
    if isinstance(node, Join):
        left = qualify(node.left, catalog)
        right = qualify(node.right, catalog)
        attrs = plan(left, catalog).attributes + plan(right, catalog).attributes
        return Join(qualify_condition(node.cond, attrs), left, right, node.pos)
    if isinstance(node, SetOp):
        return SetOp(node.op, qualify(node.left, catalog),
                     qualify(node.right, catalog), node.pos)
    raise AssertionError(f"unknown node {node!r}")


def evaluate(op: Operator) -> Relation:
    return Relation(op.name, op.attributes, op.execute())
