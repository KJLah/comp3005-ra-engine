"""Name resolution and condition compilation.

A condition AST is turned into a Python closure ``pred(left_row, right_row)
-> bool``. This does not use eval or exec: each AST node becomes a small
function that calls the functions of its children.

For ``select``, the row is passed as ``left_row`` and ``right_row`` is
unused. For ``join``, attribute indices below ``split`` read from the left
row and the rest read from the right row. The join therefore never has to
build the concatenated tuple just to test the condition.

All name and type checks happen here, once, before any tuple is read.
"""
from __future__ import annotations

import operator
from typing import Callable, List, Sequence

from .ast_nodes import And, Attr, Compare, Not, Num, Or, Str
from .errors import RANameError, RATypeError
from .relation import NUM, STR, Attribute, display_names

Predicate = Callable[[tuple, tuple], bool]

COMPARATORS = {
    "=": operator.eq, "!=": operator.ne,
    "<": operator.lt, "<=": operator.le,
    ">": operator.gt, ">=": operator.ge,
}


def resolve(attributes: Sequence[Attribute], ref: Attr) -> int:
    """Return the index of the attribute that ``ref`` names, or raise a name error.

    ``Emp.DID`` must match both the qualifier and the name. A bare ``DID`` must
    match exactly one attribute, otherwise it is ambiguous.
    """
    matches: List[int] = []
    for i, a in enumerate(attributes):
        if a.name == ref.name and (ref.qualifier is None or a.qualifier == ref.qualifier):
            matches.append(i)

    if len(matches) == 1:
        return matches[0]

    available = ", ".join(display_names(attributes)) or "(none)"
    if not matches:
        if ref.qualifier is not None and all(a.qualifier != ref.qualifier
                                             for a in attributes):
            quals = ", ".join(sorted({a.qualifier for a in attributes}))
            raise RANameError(f"unknown relation '{ref.qualifier}' in "
                              f"'{ref.display()}'; the input comes from: {quals}", ref.pos)
        raise RANameError(f"unknown attribute '{ref.display()}'; available "
                          f"attributes: {available}", ref.pos)
    options = " or ".join(attributes[i].qualified for i in matches)
    raise RANameError(f"ambiguous attribute '{ref.display()}': it could be {options}; "
                      f"qualify it with a relation name", ref.pos)


def compile_condition(cond, attributes: Sequence[Attribute], split: int) -> Predicate:
    if isinstance(cond, Compare):
        return _compile_compare(cond, attributes, split)
    if isinstance(cond, And):
        a = compile_condition(cond.left, attributes, split)
        b = compile_condition(cond.right, attributes, split)
        return lambda l, r: a(l, r) and b(l, r)
    if isinstance(cond, Or):
        a = compile_condition(cond.left, attributes, split)
        b = compile_condition(cond.right, attributes, split)
        return lambda l, r: a(l, r) or b(l, r)
    if isinstance(cond, Not):
        a = compile_condition(cond.operand, attributes, split)
        return lambda l, r: not a(l, r)
    raise AssertionError(f"unknown condition node {cond!r}")


def _compile_operand(node, attributes, split):
    """Return (getter, type, description) for one side of a comparison."""
    if isinstance(node, Num):
        v = node.value
        return (lambda l, r: v), NUM, f"the number {v}"
    if isinstance(node, Str):
        s = node.value
        return (lambda l, r: s), STR, "the string '" + s.replace("'", "''") + "'"
    i = resolve(attributes, node)
    attr = attributes[i]
    desc = f"attribute {node.display()}"
    if i < split:
        return (lambda l, r: l[i]), attr.type, desc
    j = i - split
    return (lambda l, r: r[j]), attr.type, desc


def _compile_compare(cond: Compare, attributes, split) -> Predicate:
    get_a, type_a, desc_a = _compile_operand(cond.left, attributes, split)
    get_b, type_b, desc_b = _compile_operand(cond.right, attributes, split)
    # A type of None means the column is empty, so no value will ever be compared.
    if type_a is not None and type_b is not None and type_a != type_b:
        raise RATypeError(f"cannot compare {desc_a} ({type_a}) with {desc_b} ({type_b})",
                          cond.pos)
    f = COMPARATORS[cond.op]
    return lambda l, r: f(get_a(l, r), get_b(l, r))
