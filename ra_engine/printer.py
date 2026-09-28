"""Draws a parse tree as text, without executing it (``ra.py --tree``).

Each node is first turned into (label, [children]). One generic function then
draws the box-drawing branches. A simple comparison is shown inline, as in
``Select(cond=Gt(Attr(Age), Num(30)))``. A compound condition (and/or/not) is
drawn as its own subtree, so precedence (test cases 12 and 13) is visible.
"""
from __future__ import annotations

from typing import List, Tuple

from .ast_nodes import (And, Attr, Compare, Join, Not, Num, Or, Project,
                        RelationRef, Rename, Select, SetOp, Str, Times)

CMP_NAMES = {"=": "Eq", "!=": "Ne", "<": "Lt", "<=": "Le", ">": "Gt", ">=": "Ge"}
SETOP_NAMES = {"union": "Union", "intersect": "Intersect", "minus": "Minus"}

Tree = Tuple[str, List["Tree"]]


def operand_label(node) -> str:
    if isinstance(node, Num):
        return f"Num({node.value})"
    if isinstance(node, Str):
        return "Str('" + node.value.replace("'", "''") + "')"
    if isinstance(node, Attr):
        return f"Attr({node.display()})"
    raise AssertionError(f"unknown operand {node!r}")


def compare_label(node: Compare) -> str:
    return f"{CMP_NAMES[node.op]}({operand_label(node.left)}, {operand_label(node.right)})"


def condition_tree(node) -> Tree:
    if isinstance(node, Compare):
        return (compare_label(node), [])
    if isinstance(node, And):
        return ("And", [condition_tree(node.left), condition_tree(node.right)])
    if isinstance(node, Or):
        return ("Or", [condition_tree(node.left), condition_tree(node.right)])
    if isinstance(node, Not):
        return ("Not", [condition_tree(node.operand)])
    raise AssertionError(f"unknown condition {node!r}")


def with_condition(name: str, cond, inputs: List[Tree]) -> Tree:
    """Simple comparison: inline label. Compound: a 'condition:' child subtree."""
    if isinstance(cond, Compare):
        return (f"{name}(cond={compare_label(cond)})", inputs)
    label, kids = condition_tree(cond)
    return (name, [("condition: " + label, kids)] + inputs)


def expr_tree(node) -> Tree:
    if isinstance(node, RelationRef):
        return (f"Relation({node.name})", [])
    if isinstance(node, Select):
        return with_condition("Select", node.cond, [expr_tree(node.child)])
    if isinstance(node, Project):
        names = ", ".join(a.display() for a in node.attrs)
        return (f"Project(attrs=[{names}])", [expr_tree(node.child)])
    if isinstance(node, Rename):
        return (f"Rename(to={node.new_name})", [expr_tree(node.child)])
    if isinstance(node, Times):
        return ("Times", [expr_tree(node.left), expr_tree(node.right)])
    if isinstance(node, Join):
        return with_condition("Join", node.cond,
                              [expr_tree(node.left), expr_tree(node.right)])
    if isinstance(node, SetOp):
        return (SETOP_NAMES[node.op], [expr_tree(node.left), expr_tree(node.right)])
    raise AssertionError(f"unknown expression node {node!r}")


def draw(tree: Tree) -> str:
    lines: List[str] = []

    def walk(t: Tree, prefix: str, is_last: bool, is_root: bool) -> None:
        label, kids = t
        if is_root:
            lines.append(label)
            child_prefix = ""
        else:
            lines.append(prefix + ("└── " if is_last else "├── ") + label)
            child_prefix = prefix + ("    " if is_last else "│   ")
        for i, kid in enumerate(kids):
            walk(kid, child_prefix, i == len(kids) - 1, False)

    walk(tree, "", True, True)
    return "\n".join(lines)


def format_tree(expr) -> str:
    return draw(expr_tree(expr))
