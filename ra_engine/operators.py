"""The six core operators (plus join), built as a tree of operator objects.

Building the tree (``plan``) does every static check: names, schemas and
types. No tuple is read during this step. The output schema of every operator
is known before execution starts, and a bad query fails fast even on
huge inputs.

``execute()`` then evaluates the tree bottom-up. Each operator executes its
children first, then produces its own list of rows. The tree is run exactly
as the user wrote it, with no optimisation or rewriting.
"""
from __future__ import annotations

from typing import List, Optional, Sequence

from .ast_nodes import Join, Project, RelationRef, Rename, Select, SetOp, Times
from .conditions import compile_condition, resolve
from .errors import RANameError, SchemaError
from .relation import Attribute, Relation, Row, distinct, tuple_key


class Operator:
    name: Optional[str] = None           # relation name, when there is one
    attributes: List[Attribute]
    children: List["Operator"] = []

    def execute(self) -> List[Row]:
        raise NotImplementedError

    def counters(self):
        """(label, value) pairs for the instrumentation counters, whole subtree."""
        out = []
        for c in self.children:
            out.extend(c.counters())
        return out


# ---- leaves -----------------------------------------------------------------------
class Scan(Operator):
    def __init__(self, relation: Relation):
        self.relation = relation
        self.name = relation.name
        self.attributes = relation.attributes
        self.children = []

    def execute(self) -> List[Row]:
        return self.relation.rows


# ---- unary operators ------------------------------------------------------------------
class SelectOp(Operator):
    """Output schema = input schema. Keeps the rows where the condition holds."""

    def __init__(self, child: Operator, cond, label: str):
        self.child = child
        self.children = [child]
        self.name = child.name
        self.attributes = child.attributes
        self.label = label
        # split = all attributes: every attribute reference reads the one row
        self.pred = compile_condition(cond, self.attributes, split=len(self.attributes))
        self.evaluations = 0

    def execute(self) -> List[Row]:
        pred = self.pred
        out = []
        count = 0
        for row in self.child.execute():
            count += 1                    # one per tuple the condition is evaluated on
            if pred(row, None):
                out.append(row)
        self.evaluations = count
        return out                        # a subset of a set is a set: no dedup needed

    def counters(self):
        return super().counters() + [(f"{self.label}: tuples evaluated", self.evaluations)]


class ProjectOp(Operator):
    """Output schema = the listed attributes, in the listed order. Duplicates are removed."""

    def __init__(self, child: Operator, attrs):
        self.child = child
        self.children = [child]
        self.name = child.name
        self.indices: List[int] = []
        for ref in attrs:
            i = resolve(child.attributes, ref)
            if i in self.indices:
                raise SchemaError(f"attribute '{ref.display()}' is listed twice in "
                                  f"project; each attribute may appear only once "
                                  f"(an output schema cannot have two columns with "
                                  f"the same name)", ref.pos)
            self.indices.append(i)
        self.attributes = [child.attributes[i] for i in self.indices]

    def execute(self) -> List[Row]:
        idx = self.indices
        return distinct(tuple(row[i] for i in idx) for row in self.child.execute())


class RenameOp(Operator):
    """Same attributes and rows, known under a new relation name."""

    def __init__(self, child: Operator, new_name: str, pos):
        self.child = child
        self.children = [child]
        self.name = new_name
        self.attributes = [Attribute(new_name, a.name, a.type) for a in child.attributes]
        check_no_collisions(self.attributes, pos, "rename")

    def execute(self) -> List[Row]:
        return self.child.execute()


# ---- product and join ------------------------------------------------------------------
def check_no_collisions(attributes: Sequence[Attribute], pos, op: str) -> None:
    seen = set()
    for a in attributes:
        if a.qualified in seen:
            if op == "rename":
                msg = (f"rename would give two attributes the same name "
                       f"'{a.qualified}'; project away one of the '{a.name}' "
                       f"columns first")
            else:
                msg = (f"both inputs of {op} have an attribute called "
                       f"'{a.qualified}', so the output schema would have two "
                       f"columns with the same name; rename one input first, "
                       f"e.g. rename[{a.qualifier}2]({a.qualifier})")
            raise SchemaError(msg, pos)
        seen.add(a.qualified)


class TimesOp(Operator):
    """Output schema = all attributes of left, then all of right, each still
    qualified by the relation it came from."""

    def __init__(self, left: Operator, right: Operator, pos):
        self.left, self.right = left, right
        self.children = [left, right]
        self.attributes = left.attributes + right.attributes
        check_no_collisions(self.attributes, pos, "times")

    def execute(self) -> List[Row]:
        lrows = self.left.execute()
        rrows = self.right.execute()
        return [l + r for l in lrows for r in rrows]   # set x set is already a set


class JoinOp(Operator):
    """Theta join: defined as times followed by select[cond]. Same output schema
    as times. It is a nested-loop join that compares every pair once."""

    def __init__(self, left: Operator, right: Operator, cond, pos, label: str):
        self.left, self.right = left, right
        self.children = [left, right]
        self.attributes = left.attributes + right.attributes
        check_no_collisions(self.attributes, pos, "join")
        self.label = label
        self.pred = compile_condition(cond, self.attributes, split=len(left.attributes))
        self.comparisons = 0

    def execute(self) -> List[Row]:
        lrows = self.left.execute()
        rrows = self.right.execute()
        pred = self.pred
        out = []
        append = out.append
        count = 0
        for l in lrows:
            for r in rrows:
                count += 1                # one per pair the condition is evaluated on
                if pred(l, r):
                    append(l + r)
        self.comparisons = count
        return out

    def counters(self):
        return super().counters() + [(f"{self.label}: pairs compared", self.comparisons)]


# ---- set operators ----------------------------------------------------------------------
class SetOpOp(Operator):
    """union / intersect / minus. Output schema = the left input's schema.
    Both inputs must be union compatible."""

    def __init__(self, op: str, left: Operator, right: Operator, pos):
        self.op = op
        self.left, self.right = left, right
        self.children = [left, right]
        self.name = left.name
        la, ra = left.attributes, right.attributes
        lnames = "(" + ", ".join(a.name for a in la) + ")"
        rnames = "(" + ", ".join(a.name for a in ra) + ")"
        if len(la) != len(ra):
            raise SchemaError(f"{op} needs union-compatible inputs, but the left has "
                              f"{len(la)} attributes {lnames} and the right has "
                              f"{len(ra)} {rnames}", pos)
        merged = []
        for k, (a, b) in enumerate(zip(la, ra), start=1):
            if a.name != b.name:
                raise SchemaError(f"{op} needs union-compatible inputs, but attribute "
                                  f"{k} is '{a.name}' on the left and '{b.name}' on the "
                                  f"right: left {lnames}, right {rnames}", pos)
            if a.type is not None and b.type is not None and a.type != b.type:
                raise SchemaError(f"{op} needs union-compatible inputs, but attribute "
                                  f"'{a.name}' is a {a.type} on the left and a {b.type} "
                                  f"on the right", pos)
            merged.append(Attribute(a.qualifier, a.name, a.type or b.type))
        self.attributes = merged

    def execute(self) -> List[Row]:
        lrows = self.left.execute()
        rrows = self.right.execute()
        if self.op == "union":
            return distinct(lrows + rrows)
        right_keys = {tuple_key(r) for r in rrows}
        if self.op == "intersect":
            return [l for l in lrows if tuple_key(l) in right_keys]
        return [l for l in lrows if tuple_key(l) not in right_keys]   # minus


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
