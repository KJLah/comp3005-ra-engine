"""Text forms of parse trees: the condition strings stored in logical plans,
and the indented parse tree printed by `sql parse --explain`."""
from __future__ import annotations

from typing import Callable, List

from . import ast as A
from .lexer import quote

# binding strength, tightest last
_LEVEL = {A.Or: 1, A.And: 2, A.Not: 3, A.Compare: 4}


def format_operand(o: A.Operand, name: Callable[[A.Operand], str]) -> str:
    if isinstance(o, A.IntLit):
        return str(o.value)
    if isinstance(o, A.StrLit):
        return quote(o.value)
    return name(o)


def format_condition(c: A.Condition,
                     name: Callable[[A.Operand], str] = None) -> str:
    """Print a condition with the fewest parentheses that keep its meaning.

    `name` maps a column or aggregate operand to its text; the translator
    passes one that prints fully qualified names and aggregate output names.
    Keywords are printed in lower case (and, or, not), which is also what the
    Bonus 1 condition parser behind the Module 2 executor accepts.
    """
    if name is None:
        name = lambda o: o.text()

    def go(c, parent_level: int) -> str:
        lvl = _LEVEL[type(c)]
        if isinstance(c, A.Compare):
            s = f"{format_operand(c.left, name)} {c.op} {format_operand(c.right, name)}"
        elif isinstance(c, A.Not):
            s = f"not {go(c.operand, lvl)}"
        elif isinstance(c, A.And):
            s = f"{go(c.left, lvl)} and {go(c.right, lvl + 0.5)}"
        else:
            s = f"{go(c.left, lvl)} or {go(c.right, lvl + 0.5)}"
        return f"({s})" if lvl < parent_level else s

    return go(c, 0)


def format_tree(stmt: A.Statement) -> str:
    """Indented parse tree, two spaces per level (no catalog needed)."""
    out: List[str] = []

    def line(depth: int, text: str) -> None:
        out.append("  " * depth + text)

    def from_item(item, depth):
        if isinstance(item, A.TableRef):
            line(depth, f"table {item.name}" + (f" AS {item.alias}" if item.alias else ""))
        else:
            line(depth, f"subquery AS {item.alias}")
            query(item.query, depth + 1)

    def query(q, depth):
        if isinstance(q, A.SetOp):
            line(depth, q.op.upper().replace("_", " "))
            query(q.left, depth + 1)
            query(q.right, depth + 1)
            return
        line(depth, "SELECT DISTINCT" if q.distinct else "SELECT")
        if isinstance(q.items, A.Star):
            line(depth + 1, "*")
        else:
            for it in q.items:
                line(depth + 1, it.expr.text() + (f" AS {it.alias}" if it.alias else ""))
        line(depth, "FROM")
        from_item(q.first, depth + 1)
        for st in q.steps:
            if st.kind == "join":
                line(depth + 1, f"JOIN ON {format_condition(st.on)}")
            else:
                line(depth + 1, "CROSS (comma)")
            from_item(st.ref, depth + 2)
        if q.where is not None:
            line(depth, f"WHERE {format_condition(q.where)}")
        if q.group_by:
            line(depth, "GROUP BY " + ", ".join(c.text() for c in q.group_by))
        if q.having is not None:
            line(depth, f"HAVING {format_condition(q.having)}")
        if q.order_by:
            line(depth, "ORDER BY " + ", ".join(f"{o.column.text()} {o.direction.upper()}"
                                                for o in q.order_by))
        if q.limit is not None:
            line(depth, f"LIMIT {q.limit}")

    if isinstance(stmt, A.CreateTable):
        line(0, f"CREATE TABLE {stmt.name}")
        for c in stmt.columns:
            line(1, f"{c.name} {c.type}" + (f"({c.length})" if c.length is not None else ""))
    elif isinstance(stmt, A.Insert):
        line(0, f"INSERT INTO {stmt.table}")
        line(1, "VALUES " + ", ".join(format_operand(v, None) for v in stmt.values))
    else:
        query(stmt, 0)
    return "\n".join(out)