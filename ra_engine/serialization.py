"""Versioned JSON encoding for AST nodes and source positions."""
import json
from dataclasses import fields, is_dataclass
from typing import Union, get_args, get_origin, get_type_hints

from . import ast_nodes as A
from .errors import Position


_CLASSES = (
    Position,
    A.Num, A.Str, A.Attr,
    A.Compare, A.And, A.Or, A.Not,
    A.RelationRef, A.Select, A.Project, A.Rename,
    A.Times, A.Join, A.SetOp, A.Distinct,
    A.RelationDef,
)
_TYPES = {cls.__name__: cls for cls in _CLASSES}


def _validate(value, expected):
    """Validate decoded values against the AST's type annotations."""
    origin = get_origin(expected)

    if origin is Union:
        for option in get_args(expected):
            try:
                _validate(value, option)
                return
            except ValueError:
                pass
        raise ValueError("value does not match any allowed type")

    if origin is list:
        if not isinstance(value, list):
            raise ValueError("expected a list")
        item_type = get_args(expected)[0]
        for item in value:
            _validate(item, item_type)
        return

    if expected in (int, float, str, type(None)):
        # Exact type checks prevent True from being treated as integer 1.
        valid = (
            type(value) in (int, float)
            if expected is float
            else type(value) is expected
        )
        if not valid:
            raise ValueError(f"expected {expected.__name__}")
        return

    if type(value) is not expected:
        raise ValueError(f"expected {expected.__name__}")


def _encode(value):
    if is_dataclass(value) and not isinstance(value, type):
        if type(value) not in _CLASSES:
            raise ValueError("unsupported dataclass")
        return {
            "node": type(value).__name__,
            "fields": {
                field.name: _encode(getattr(value, field.name))
                for field in fields(value)
            },
        }

    if isinstance(value, list):
        return [_encode(item) for item in value]

    if value is None or type(value) in (int, float, str):
        return value

    raise ValueError("unsupported JSON value")


def _decode(value):
    if isinstance(value, list):
        return [_decode(item) for item in value]

    if not isinstance(value, dict):
        if value is None or type(value) in (int, float, str):
            return value
        raise ValueError("unsupported JSON value")

    if set(value) != {"node", "fields"}:
        raise ValueError("invalid node envelope")

    tag = value["node"]
    if not isinstance(tag, str) or tag not in _TYPES:
        raise ValueError("unknown node type")

    cls = _TYPES[tag]
    payload = value["fields"]
    expected_fields = {field.name for field in fields(cls)}

    if not isinstance(payload, dict) or set(payload) != expected_fields:
        raise ValueError(f"invalid fields for {tag}")

    values = {
        name: _decode(item)
        for name, item in payload.items()
    }
    hints = get_type_hints(cls)
    for name, item in values.items():
        _validate(item, hints[name])

    node = cls(**values)

    if isinstance(node, A.Compare):
        if node.op not in {"=", "!=", "<", "<=", ">", ">="}:
            raise ValueError("invalid comparison operator")

    if isinstance(node, A.SetOp):
        if node.op not in {"union", "intersect", "minus"}:
            raise ValueError("invalid set operator")

    return node


def _reject_constant(value):
    raise ValueError(f"invalid JSON numeric constant: {value}")


def tree_to_json(tree):
    if type(tree) not in _CLASSES or isinstance(tree, Position):
        raise ValueError("expected an AST node")

    # Validate manually constructed nodes before exporting them too.
    encoded = _encode(tree)
    _decode(encoded)

    return json.dumps(
        {"version": 1, "tree": encoded},
        allow_nan=False,
    )


def tree_from_json(text):
    document = json.loads(text, parse_constant=_reject_constant)

    if not isinstance(document, dict):
        raise ValueError("expected a JSON object")
    if set(document) != {"version", "tree"}:
        raise ValueError("invalid document envelope")
    if type(document["version"]) is not int or document["version"] != 1:
        raise ValueError("unsupported tree format version")

    tree = _decode(document["tree"])
    if type(tree) not in _CLASSES or isinstance(tree, Position):
        raise ValueError("expected an AST node")

    return tree