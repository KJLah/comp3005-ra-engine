"""Parse tree (abstract syntax tree) node classes.

Every node remembers the Position where it started, so later phases
(name resolution, type checking) can point at the exact place in the query.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import List, Optional, Union

from .errors import Position


# ---- operands inside conditions ---------------------------------------------
@dataclass
class Num:
    value: Union[int, float]
    pos: Position


@dataclass
class Str:
    value: str
    pos: Position


@dataclass
class Attr:
    name: str
    qualifier: Optional[str]  # "Emp" in Emp.DID, or None
    pos: Position

    def display(self) -> str:
        return f"{self.qualifier}.{self.name}" if self.qualifier else self.name


Operand = Union[Num, Str, Attr]


# ---- conditions ----------------------------------------------------------------
@dataclass
class Compare:
    op: str            # one of = != < <= > >=
    left: Operand
    right: Operand
    pos: Position


@dataclass
class And:
    left: "Condition"
    right: "Condition"
    pos: Position


@dataclass
class Or:
    left: "Condition"
    right: "Condition"
    pos: Position


@dataclass
class Not:
    operand: "Condition"
    pos: Position


Condition = Union[Compare, And, Or, Not]


# ---- relational expressions ------------------------------------------------------
@dataclass
class RelationRef:
    name: str
    pos: Position


@dataclass
class Select:
    cond: Condition
    child: "Expr"
    pos: Position


@dataclass
class Project:
    attrs: List[Attr]
    child: "Expr"
    pos: Position


@dataclass
class Rename:
    new_name: str
    child: "Expr"
    pos: Position


@dataclass
class Times:
    left: "Expr"
    right: "Expr"
    pos: Position


@dataclass
class Join:
    cond: Condition
    left: "Expr"
    right: "Expr"
    pos: Position


@dataclass
class SetOp:
    op: str            # "union" | "intersect" | "minus"
    left: "Expr"
    right: "Expr"
    pos: Position


Expr = Union[RelationRef, Select, Project, Rename, Times, Join, SetOp]


# ---- relation definitions (data files) -------------------------------------------
@dataclass
class RelationDef:
    name: str
    attrs: List[str]
    attr_positions: List[Position]
    rows: List[List[Union[int, float, str]]]
    row_positions: List[Position]
    pos: Position
