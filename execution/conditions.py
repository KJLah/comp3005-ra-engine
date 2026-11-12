"""Conditions for filter and the joins, reusing the Bonus 1 parser and
condition compiler (ra_engine/parser.py, ra_engine/conditions.py).

Bonus 1 already parses "R.a > 500 and R.b = 'cs'", resolves names and
rejects INT against VARCHAR before any tuple is read. Module 2 only adds the
Schema -> Attribute translation and turns Bonus 1 errors into BindError.
"""
from __future__ import annotations

from typing import Callable, List, Tuple

from ra_engine.ast_nodes import And, Attr, Compare
from ra_engine.conditions import compile_condition, resolve
from ra_engine.errors import RAError
from ra_engine.parser import parse_condition

from .errors import BindError
from .schema import Row, Schema, check_same_type


def _parse(text: str, where: str):
    try:
        return parse_condition(text)
    except RAError as e:
        raise BindError(f"{where}: bad condition {text!r}: {e.message}") from None


def row_predicate(text: str, schema: Schema, where: str) -> Callable[[Row], bool]:
    """filter: pred(row) -> bool."""
    cond = _parse(text, where)
    try:
        pred = compile_condition(cond, schema.attributes(), split=len(schema))
    except RAError as e:
        raise BindError(f"{where}: {e.message}") from None
    return lambda row: pred(row, ())


def join_predicate(text: str, left: Schema, right: Schema,
                   where: str) -> Callable[[Row, Row], bool]:
    """nested_loop_join: pred(left_row, right_row) -> bool. Any condition."""
    cond = _parse(text, where)
    try:
        return compile_condition(cond, left.attributes() + right.attributes(),
                                 split=len(left))
    except RAError as e:
        raise BindError(f"{where}: {e.message}") from None


def equi_keys(text: str, left: Schema, right: Schema,
              where: str) -> Tuple[List[int], List[int]]:
    """sort_merge_join and hash_join: equality conditions only.

    Accepts "L.x = R.y" or a conjunction of such equalities (a multi-column
    key), in either order of the two sides. Returns the key positions in the
    left schema and in the right schema, pairwise."""
    cond = _parse(text, where)
    attrs = left.attributes() + right.attributes()
    split = len(left)
    lk: List[int] = []
    rk: List[int] = []

    def walk(c):
        if isinstance(c, And):
            walk(c.left)
            walk(c.right)
            return
        if not (isinstance(c, Compare) and c.op == "="
                and isinstance(c.left, Attr) and isinstance(c.right, Attr)):
            raise BindError(f"{where}: this join takes only equalities between a "
                            f"left column and a right column (joined with 'and'); "
                            f"use nested_loop_join for {text!r}")
        try:
            a, b = resolve(attrs, c.left), resolve(attrs, c.right)
        except RAError as e:
            raise BindError(f"{where}: {e.message}") from None
        if (a < split) == (b < split):
            raise BindError(f"{where}: '{c.left.display()} = {c.right.display()}' "
                            f"compares two columns of the same input")
        if a > b:
            a, b = b, a
        check_same_type(left.cols[a], right.cols[b - split], where)
        lk.append(a)
        rk.append(b - split)

    walk(cond)
    return lk, rk
