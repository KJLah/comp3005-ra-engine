"""Plan JSON: validation and building the operator tree (Module 2, Section 4).

The field names are exactly those of the JSON shown for each operator in
Section 2. Section 2 shows no JSON for limit or the set operators, so those
shapes are ours (DESIGN_M2.md):

    { "op": "limit", "n": 10, "child": { ... } }
    { "op": "union_all" | "union" | "intersect" | "except",
      "left": { ... }, "right": { ... } }

Every problem names the offending node by its path from the root (for
example `plan.left.child`) and its op. Unknown fields are rejected too, so a
typo such as "chlid" is caught instead of silently ignored.

Validation here is structural only. Names of tables and columns are checked
by bind(), which needs the catalog; it still runs before any tuple is read.
"""
from __future__ import annotations

import json
from typing import Any, Dict, List, Tuple

from .context import ExecContext
from .errors import PlanError
from .base import OPERATORS, Operator

JOIN_OPS = ("nested_loop_join", "sort_merge_join", "hash_join")
SET_OPS = ("union_all", "union", "intersect", "except")
AGGREGATES = ("count", "sum", "avg", "min", "max")

# op -> (required fields, optional fields); "child"/"left"/"right" are subplans
FIELDS: Dict[str, Tuple[Tuple[str, ...], Tuple[str, ...]]] = {
    "seqscan": (("table",), ()),
    "filter": (("condition", "child"), ()),
    "project": (("columns", "child"), ()),
    "distinct": (("mode", "child"), ()),
    "sort": (("keys", "child"), ()),
    "group_by": (("mode", "keys", "aggregates", "child"), ()),
    "limit": (("n", "child"), ()),
    **{j: (("condition", "left", "right"), ()) for j in JOIN_OPS},
    **{s: (("left", "right"), ()) for s in SET_OPS},
}


def load_plan(text: str) -> Any:
    try:
        return json.loads(text)
    except json.JSONDecodeError as e:
        raise PlanError(f"plan is not valid JSON: {e.msg} at line {e.lineno}, "
                        f"column {e.colno}") from None


def _fail(path: str, op: str, msg: str) -> PlanError:
    where = f"{path} ({op})" if op else path
    return PlanError(f"{where}: {msg}")


def _is_str_list(v) -> bool:
    return isinstance(v, list) and all(isinstance(x, str) for x in v)


def validate(node: Any, path: str = "plan") -> None:
    """Raise PlanError naming the first offending node; return None if the
    whole tree conforms."""
    if not isinstance(node, dict):
        raise _fail(path, "", f"expected an object with an \"op\" field, "
                              f"found {type(node).__name__}")
    op = node.get("op")
    if op is None:
        raise _fail(path, "", "missing field \"op\"")
    if not isinstance(op, str) or op not in FIELDS:
        known = ", ".join(FIELDS)
        raise _fail(path, "", f"unknown op {json.dumps(op)}; expected one of: {known}")
    required, optional = FIELDS[op]
    for f in required:
        if f not in node:
            raise _fail(path, op, f"missing field \"{f}\"")
    extra = [k for k in node if k != "op" and k not in required and k not in optional]
    if extra:
        raise _fail(path, op, f"unknown field(s) {', '.join(json.dumps(k) for k in extra)}"
                              f"; {op} takes {', '.join(required + optional)}")

    def bad(field: str, expected: str):
        return _fail(path, op, f"field \"{field}\" must be {expected}, "
                               f"found {json.dumps(node[field])}")

    if op == "seqscan":
        if not isinstance(node["table"], str) or not node["table"]:
            raise bad("table", "a table name")
    if op in ("filter",) + JOIN_OPS:
        if not isinstance(node["condition"], str) or not node["condition"].strip():
            raise bad("condition", "a non-empty condition string")
    if op == "project":
        if not _is_str_list(node["columns"]) or not node["columns"]:
            raise bad("columns", "a non-empty list of column names")
    if op in ("distinct", "group_by"):
        if node["mode"] not in ("sort", "hash"):
            raise bad("mode", "\"sort\" or \"hash\"")
    if op == "sort":
        keys = node["keys"]
        if (not isinstance(keys, list) or not keys or not all(
                isinstance(k, list) and len(k) == 2 and isinstance(k[0], str)
                and k[1] in ("asc", "desc") for k in keys)):
            raise bad("keys", "a non-empty list of [column, \"asc\"|\"desc\"] pairs")
    if op == "group_by":
        if not _is_str_list(node["keys"]):
            raise bad("keys", "a list of column names (possibly empty)")
        aggs = node["aggregates"]
        if not isinstance(aggs, list) or not all(
                isinstance(a, list) and len(a) == 2 and isinstance(a[0], str)
                and a[0].lower() in AGGREGATES and isinstance(a[1], str) for a in aggs):
            raise bad("aggregates", "a list of [function, column] pairs with function "
                                    "one of count, sum, avg, min, max")
        for fn, col in aggs:
            if col == "*" and fn.lower() != "count":
                raise _fail(path, op, f"{fn}(*) is not allowed; only count takes *")
        if not node["keys"] and not aggs:
            raise _fail(path, op, "a group_by needs at least one key or aggregate")
    if op == "limit":
        n = node["n"]
        if type(n) is not int or n < 0:
            raise bad("n", "a non-negative integer")

    for f in ("child", "left", "right"):
        if f in node:
            validate(node[f], f"{path}.{f}")


def build(node: dict, ctx: ExecContext, path: str = "plan") -> Operator:
    """Turn a validated plan into an operator tree (not yet bound or open)."""
    op = node["op"]
    if op not in OPERATORS:
        raise _fail(path, op, "this operator is not implemented yet")
    children: List[Operator] = []
    for f in ("child", "left", "right"):
        if f in node:
            children.append(build(node[f], ctx, f"{path}.{f}"))
    return OPERATORS[op](ctx, path, node, children)


def prepare(plan: Any, ctx: ExecContext) -> Operator:
    """Validate, build and bind: everything that can fail before any work."""
    import execution.operators  # noqa: F401  (registers every operator)
    validate(plan)
    root = build(plan, ctx)
    root.bind()
    return root
