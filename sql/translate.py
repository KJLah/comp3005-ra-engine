"""SQL to logical plan (Module 3, Sections 4 and 5).

Each select block becomes nodes in the canonical bottom-to-top order:

    1 FROM      scan per table, rename per alias, cross (comma) or join (ON),
                left to right
    2 WHERE     one select node with the whole condition
    3 GROUP BY  group_by node
    4 HAVING    select node above the group_by
    5 SELECT    project node, then distinct if DISTINCT was written
    6 ORDER BY  sort node
    7 LIMIT     limit node
    8 set ops   between whole blocks, left associative (as parsed)

The translator makes no optimisation decisions: no predicate moves, no join
is reordered, a comma never becomes a join. Duplicate elimination happens in
exactly four places (DESIGN_M3.md section 3): an explicit DISTINCT, UNION,
INTERSECT/EXCEPT, and group_by by construction. project never removes
duplicates.

One case leaves the canonical order, because the handout requires it to work
(required case 9): ORDER BY on a column that is not in the select list. Above
the project that column no longer exists, so the sort goes directly below the
project instead, sorting on the pre-projection names. The project keeps the
order (it is a pipelined operator in Module 2).
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple

from . import ast as A
from . import logical as L
from .errors import AggregateError, NameError_, SchemaError
from .printer import format_condition
from .resolve import (ColInfo, Entry, Scope, agg_type, check_comparison,
                      check_set_operands, default_agg_name, literal_type,
                      reject_aggregates)


@dataclass
class OutCol:
    """One output column of a query: its name in the plan, its short name
    (what a subquery alias exposes) and its type."""
    plan_name: str        # "Emp.did", "n", or a select-list alias
    short: str            # "did", "n"
    info: ColInfo


@dataclass
class Translated:
    plan: L.Plan
    columns: List[OutCol]


def translate(q: A.Query, catalog) -> L.Plan:
    return translate_query(q, catalog).plan


def translate_query(q: A.Query, catalog) -> Translated:
    if isinstance(q, A.SetOp):
        left = translate_query(q.left, catalog)
        right = translate_query(q.right, catalog)
        check_set_operands(q, [c.info for c in left.columns], [c.info for c in right.columns],
                           [c.plan_name for c in left.columns],
                           [c.plan_name for c in right.columns])
        return Translated(L.setop(q.op, left.plan, right.plan), left.columns)
    return _Block(q, catalog).translate()


def _short(name: str) -> str:
    return name.rpartition(".")[2]


@dataclass
class _Agg:
    fn: str
    arg: Optional[str]            # qualified column, None for *
    name: str
    info: ColInfo


class _Block:
    def __init__(self, q: A.SelectBlock, catalog):
        self.q = q
        self.catalog = catalog
        self.scope = Scope()
        self.aggs: List[_Agg] = []

    # ---- 1 FROM ----------------------------------------------------------------
    def from_item(self, item: A.FromItem) -> L.Plan:
        if isinstance(item, A.TableRef):
            cols = self.catalog.columns(item.name)
            if cols is None:
                known = ", ".join(sorted(self.catalog.names())) or "(none)"
                raise NameError_(f"unknown table '{item.name}' (tables: {known})", item.pos)
            self.scope.add(Entry(item.key, list(cols), item.alias_pos or item.pos), item.name)
            plan = L.scan(item.name)
            return L.rename(item.alias, plan) if item.alias is not None else plan
        sub = translate_query(item.query, self.catalog)
        seen: Dict[str, str] = {}
        for c in sub.columns:
            if c.short in seen:
                raise SchemaError(f"subquery {item.alias} has two output columns named "
                                  f"'{c.short}' ({seen[c.short]} and {c.plan_name}); give "
                                  f"one an alias with AS", item.alias_pos or item.pos)
            seen[c.short] = c.plan_name
        cols = [ColInfo(c.short, c.info.type, c.info.length) for c in sub.columns]
        self.scope.add(Entry(item.alias, cols, item.alias_pos or item.pos))
        return L.rename(item.alias, sub.plan)

    def from_clause(self) -> L.Plan:
        plan = self.from_item(self.q.first)
        for step in self.q.steps:
            right = self.from_item(step.ref)
            if step.kind == "cross":
                plan = L.cross(plan, right)
            else:
                reject_aggregates(step.on, "JOIN ... ON", "")
                plan = L.join(self.condition(step.on), plan, right)
        return plan

    # ---- conditions --------------------------------------------------------------
    def operand(self, o, having: bool) -> Tuple[str, ColInfo]:
        if isinstance(o, (A.IntLit, A.StrLit)):
            return literal_type(o)
        if isinstance(o, A.AggCall):
            agg = self.aggregate(o, alias=None, from_having=True)
            return agg.name, agg.info
        name, info = self.scope.lookup(o)
        if having and name not in self.keys:
            raise AggregateError(f"HAVING may use only grouping columns and aggregates; "
                                 f"{name} is neither", o.pos)
        return name, info

    def condition(self, c: A.Condition, having: bool = False) -> str:
        names: Dict[int, str] = {}

        def visit(c):
            if isinstance(c, A.Compare):
                left = self.operand(c.left, having)
                right = self.operand(c.right, having)
                check_comparison(c, left, right)
                names[id(c.left)], names[id(c.right)] = left[0], right[0]
            elif isinstance(c, A.Not):
                visit(c.operand)
            else:
                visit(c.left)
                visit(c.right)

        visit(c)
        return format_condition(c, lambda o: names[id(o)])

    # ---- aggregates ----------------------------------------------------------------
    def aggregate(self, call: A.AggCall, alias: Optional[str], from_having: bool) -> _Agg:
        arg_name = arg_info = None
        if call.arg is not None:
            arg_name, arg_info = self.scope.lookup(call.arg)
        info = agg_type(call, arg_info, arg_name)
        if from_having:
            for a in self.aggs:               # reuse one the select list computes
                if (a.fn, a.arg) == (call.fn, arg_name):
                    return a
        name = alias if alias is not None else default_agg_name(call.fn, arg_name)
        for a in self.aggs:
            if a.name == name:
                if (a.fn, a.arg) == (call.fn, arg_name) and alias is None and not from_having:
                    raise SchemaError(f"{call.text()} is selected twice under the same name "
                                      f"'{name}'; give one an alias with AS", call.pos)
                raise SchemaError(f"two aggregates are named '{name}'; give one a "
                                  f"different alias", call.pos)
        agg = _Agg(call.fn, arg_name, name, info)
        self.aggs.append(agg)
        return agg

    # ---- the block ------------------------------------------------------------------
    def translate(self) -> Translated:
        q = self.q
        plan = self.from_clause()                                     # 1 FROM

        if q.where is not None:                                       # 2 WHERE
            reject_aggregates(q.where, "WHERE",
                              ", because WHERE runs before grouping (use HAVING)")
            plan = L.select(self.condition(q.where), plan)

        # select list: (expr, plan name, output name, alias given, info)
        items = self.select_items()
        aggregating = bool(q.group_by) or any(isinstance(e, A.AggCall) for e, *_ in items)
        self.aggregating = aggregating
        self.keys: List[str] = []

        if aggregating:                                               # 3 GROUP BY
            for c in q.group_by:
                name, _ = self.scope.lookup(c)
                if name not in self.keys:
                    self.keys.append(name)
            outputs = []
            for expr, pname, oname, alias, info, pos in items:
                if isinstance(expr, A.AggCall):
                    agg = self.aggregate(expr, alias, from_having=False)
                    outputs.append((agg.name, None, agg.name, agg.info))
                else:
                    if pname not in self.keys:
                        why = ("GROUP BY" if q.group_by else
                               "a GROUP BY clause (the query aggregates)")
                        raise AggregateError(f"{pname} must appear in {why} or be inside "
                                             f"an aggregate", pos)
                    outputs.append((pname, alias, oname, info))
            having = None
            if q.having is not None:                                  # 4 HAVING
                having = self.condition(q.having, having=True)
            plan = L.group_by(self.keys, [[a.fn, a.arg or "*", a.name] for a in self.aggs],
                              plan)
            if having is not None:
                plan = L.select(having, plan)
        else:
            outputs = [(pname, alias, oname, info)
                       for expr, pname, oname, alias, info, pos in items]

        seen = set()
        for pname, alias, oname, info in outputs:
            if oname in seen:
                raise SchemaError(f"two output columns are named '{oname}'; give one an "
                                  f"alias with AS", q.pos)
            seen.add(oname)

        sort_keys, hidden = self.order_by(outputs)
        if hidden:                                                    # case 9
            plan = L.sort(sort_keys, plan)

        plan = L.project([p for p, *_ in outputs], plan,              # 5 SELECT
                         [a for _, a, *_ in outputs])
        if q.distinct and not self.distinct_is_redundant(outputs, aggregating):
            plan = L.distinct(plan)

        if sort_keys and not hidden:                                  # 6 ORDER BY
            plan = L.sort(sort_keys, plan)
        if q.limit is not None:                                       # 7 LIMIT
            plan = L.limit(q.limit, plan)

        cols = [OutCol(oname, _short(oname) if alias is None else alias, info)
                for pname, alias, oname, info in outputs]
        return Translated(plan, cols)

    def select_items(self):
        q = self.q
        out = []
        if isinstance(q.items, A.Star):
            for name, info in self.scope.expand_star():
                ref = A.ColumnRef(*name.split(".", 1), q.items.pos)
                out.append((ref, name, name, None, info, q.items.pos))
            return out
        for it in q.items:
            if isinstance(it.expr, A.AggCall):
                out.append((it.expr, None, None, it.alias, None, it.pos))
            else:
                name, info = self.scope.lookup(it.expr)
                out.append((it.expr, name, it.alias or name, it.alias, info, it.pos))
        return out

    def distinct_is_redundant(self, outputs, aggregating: bool) -> bool:
        """Section 4: group_by already returns one row per group. DISTINCT above
        it is redundant when every grouping column is selected (then no two
        output rows can be equal), and always when there are no grouping
        columns (one row). Otherwise it is kept: dropping a grouping column from
        the select list can make two groups print the same row."""
        if not aggregating:
            return False
        selected = {p for p, alias, o, i in outputs}
        return all(k in selected for k in self.keys)

    def order_by(self, outputs) -> Tuple[List[List[str]], bool]:
        """Section 3: ORDER BY resolves first against the select list's output
        names (aliases, aggregate names), then against the FROM scope.

        Returns the sort keys and whether any key is not in the select list.
        Keys are output names when every key is selected (sort above project),
        and pre-projection names otherwise (sort below project)."""
        q = self.q
        if not q.order_by:
            return [], False
        by_output = {o: (p, alias) for p, alias, o, i in outputs}
        source_alias = {p: o for p, alias, o, i in outputs if alias is not None}
        selected_plain = {p for p, alias, o, i in outputs if alias is None}
        resolved = []          # (output name or None, pre-projection name, direction, ref)
        for item in q.order_by:
            ref = item.column
            if ref.table is None and ref.name in by_output:
                pname, _ = by_output[ref.name]
                resolved.append((ref.name, pname, item.direction, ref))
                continue
            name, _ = self.scope.lookup(ref)
            if name in selected_plain:
                out = name
            else:
                out = source_alias.get(name)
            if out is None and self.aggregating and name not in self.keys:
                raise AggregateError(f"ORDER BY {name}: in an aggregating query a sort "
                                     f"column must be a grouping column or an output of "
                                     f"the select list", ref.pos)
            resolved.append((out, name, item.direction, ref))
        hidden = any(out is None for out, *_ in resolved)
        if hidden and q.distinct:
            out, name, d, ref = next(r for r in resolved if r[0] is None)
            raise SchemaError(f"with SELECT DISTINCT, the ORDER BY column {name} must "
                              f"appear in the select list", ref.pos)
        if hidden:
            return [[pre, d] for out, pre, d, ref in resolved], True
        return [[out, d] for out, pre, d, ref in resolved], False