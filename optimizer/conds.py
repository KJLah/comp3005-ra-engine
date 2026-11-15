"""Conditions as trees: parse with the Bonus 1 parser, print back to the text
Module 2 accepts, split conjunctions, list and rename the columns used.

A logical plan carries conditions as strings (Module 3). The optimizer
parses them once with ra_engine.parser.parse_condition (no regular
expressions, no eval) and prints them back with to_text.
"""
from __future__ import annotations

from typing import Callable, List, Optional, Sequence

from ra_engine.ast_nodes import And, Attr, Compare, Not, Num, Or, Str
from ra_engine.parser import parse_condition

FLIP = {"=": "=", "!=": "!=", "<": ">", ">": "<", "<=": ">=", ">=": "<="}


def parse(text: str):
    return parse_condition(text)


def _operand(o) -> str:
    if isinstance(o, Attr):
        return o.display()
    if isinstance(o, Str):
        return "'" + o.value.replace("'", "''") + "'"
    return str(o.value)


def to_text(c) -> str:
    """Print with lower-case keywords and only the parentheses needed."""
    def go(c, level: int) -> str:
        if isinstance(c, Compare):
            return f"{_operand(c.left)} {c.op} {_operand(c.right)}"
        if isinstance(c, Not):
            return f"not {go(c.operand, 3)}"
        if isinstance(c, And):
            s, mine = f"{go(c.left, 2)} and {go(c.right, 2)}", 2
        else:
            s, mine = f"{go(c.left, 1)} or {go(c.right, 1)}", 1
        return f"({s})" if mine < level else s
    return go(c, 0)


def conjuncts(c) -> List:
    if isinstance(c, And):
        return conjuncts(c.left) + conjuncts(c.right)
    return [c]


def and_all(parts: Sequence):
    out = None
    for p in parts:
        out = p if out is None else And(out, p, p.pos)
    return out


def refs(c) -> List[str]:
    """Column names used by a condition, in order, as written."""
    if isinstance(c, Compare):
        return [o.display() for o in (c.left, c.right) if isinstance(o, Attr)]
    if isinstance(c, Not):
        return refs(c.operand)
    return refs(c.left) + refs(c.right)


def map_refs(c, fn: Callable[[str], str]):
    """A copy of the condition with every column name replaced by fn(name)."""
    def op(o):
        if isinstance(o, Attr):
            new = fn(o.display())
            q, _, n = new.rpartition(".")
            return Attr(n, q or None, o.pos)
        return o
    if isinstance(c, Compare):
        return Compare(c.op, op(c.left), op(c.right), c.pos)
    if isinstance(c, Not):
        return Not(map_refs(c.operand, fn), c.pos)
    return type(c)(map_refs(c.left, fn), map_refs(c.right, fn), c.pos)


def short(name: str) -> str:
    return name.rpartition(".")[2]


def match(ref: str, cols: Sequence[str]) -> Optional[int]:
    """Position of the column a reference names, or None. "e.age" must match
    exactly; a bare "age" matches "age" or any "X.age" (the first one)."""
    if ref in cols:
        return cols.index(ref)
    if "." not in ref:
        for i, c in enumerate(cols):
            if short(c) == ref:
                return i
    return None


def covers(c, cols: Sequence[str]) -> bool:
    """Every column the condition uses is one of cols."""
    return all(match(r, cols) is not None for r in refs(c))


def equi_pair(c, left: Sequence[str], right: Sequence[str]):
    """(left column, right column) if c is "a = b" with one side in each
    input, else None."""
    if not (isinstance(c, Compare) and c.op == "=" and isinstance(c.left, Attr)
            and isinstance(c.right, Attr)):
        return None
    a, b = c.left.display(), c.right.display()
    if match(a, left) is not None and match(b, right) is not None:
        return a, b
    if match(b, left) is not None and match(a, right) is not None:
        return b, a
    return None