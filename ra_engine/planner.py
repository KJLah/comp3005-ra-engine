from __future__ import annotations

from .ast_nodes import Join, Project, RelationRef, Rename, Select, SetOp, Times, Distinct
from .errors import RANameError
from .operators import (JoinOp, Operator, ProjectOp, RenameOp, Scan, SelectOp,
                        SetOpOp, TimesOp, DistinctOp, check_no_collisions)
from .relation import Relation
from .conditions import qualify_condition, qualify_operand

# ---- building the operator tree from the parse tree ------------------------------------
def _plan(node, catalog) -> Operator:
    """Turn a parse tree into an operator tree, doing every static check on the way."""
    if isinstance(node, RelationRef):
        if node.name not in catalog:
            known = ", ".join(sorted(catalog)) or "(no relations loaded; use -d FILE)"
            raise RANameError(f"unknown relation '{node.name}'; known relations: {known}",
                              node.pos)
        return Scan(catalog[node.name])
    if isinstance(node, Select):
        return SelectOp(_plan(node.child, catalog), node.cond,
                        label=f"select at column {node.pos.col}")
    if isinstance(node, Project):
        return ProjectOp(_plan(node.child, catalog), node.attrs)
    if isinstance(node, Rename):
        return RenameOp(_plan(node.child, catalog), node.new_name, node.pos)
    if isinstance(node, Times):
        return TimesOp(_plan(node.left, catalog), _plan(node.right, catalog),
                       node.pos)
    if isinstance(node, Join):
        return JoinOp(_plan(node.left, catalog), _plan(node.right, catalog),
                      node.cond, node.pos, label=f"join at column {node.pos.col}")
    if isinstance(node, SetOp):
        return SetOpOp(
            node.op,
            _plan(node.left, catalog),
            _plan(node.right, catalog),
            node.pos,
        )

    if isinstance(node, Distinct):
        return DistinctOp(_plan(node.child, catalog))

    raise AssertionError(f"unknown node {node!r}")

def qualify(node, catalog):
    """Return a copy of the parse tree where every attribute is Table.column.

    Bare names are resolved against the input schema (an ambiguous or
    unknown name raises RANameError, as before). Planning a subtree reads no
    tuples, so using _plan() here only to learn schemas is cheap.
    """
    if isinstance(node, RelationRef):
        return node
    if isinstance(node, Select):
        child = qualify(node.child, catalog)
        attrs = _plan(child, catalog).attributes
        return Select(qualify_condition(node.cond, attrs), child, node.pos)
    if isinstance(node, Project):
        child = qualify(node.child, catalog)
        attrs = _plan(child, catalog).attributes
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
        attrs = _plan(left, catalog).attributes + _plan(right, catalog).attributes
        # Same check JoinOp does, done first so a self-join without rename is
        # still a Schema error and not an "ambiguous attribute" Name error.
        check_no_collisions(attrs, node.pos, "join")
        return Join(qualify_condition(node.cond, attrs), left, right, node.pos)
    if isinstance(node, SetOp):
        return SetOp(node.op, qualify(node.left, catalog),
                     qualify(node.right, catalog), node.pos)
    raise AssertionError(f"unknown node {node!r}")


def plan(node, catalog) -> Operator:
    """Qualify every column name, then build the operator tree."""
    return _plan(qualify(node, catalog), catalog)


def evaluate(op: Operator) -> Relation:
    return Relation(op.name, op.attributes, op.execute())
