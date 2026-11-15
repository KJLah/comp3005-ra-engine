"""Parse tree for the SQL subset (GRAMMAR_SQL.md). Every node keeps the
position of its first token so later passes can point errors at it.

The parser builds these and nothing else; names are not resolved here.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import List, Optional, Tuple, Union

from ra_engine.errors import Position


# ---- operands and conditions ---------------------------------------------------
@dataclass
class ColumnRef:
    table: Optional[str]          # qualifier as written, or None
    name: str
    pos: Position

    def text(self) -> str:
        return f"{self.table}.{self.name}" if self.table else self.name


@dataclass
class IntLit:
    value: int
    pos: Position


@dataclass
class StrLit:
    value: str
    pos: Position


@dataclass
class AggCall:
    fn: str                       # count, sum, avg, min, max (lower case)
    arg: Optional[ColumnRef]      # None means *
    pos: Position

    def text(self) -> str:
        return f"{self.fn.upper()}({self.arg.text() if self.arg else '*'})"


Operand = Union[ColumnRef, IntLit, StrLit, AggCall]


@dataclass
class Compare:
    op: str                       # = <> < <= > >=
    left: Operand
    right: Operand
    pos: Position                 # position of the operator


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


# ---- queries ---------------------------------------------------------------------
@dataclass
class SelectItem:
    expr: Union[ColumnRef, AggCall]
    alias: Optional[str]
    pos: Position
    alias_pos: Optional[Position] = None


@dataclass
class Star:
    pos: Position


@dataclass
class TableRef:
    name: str
    alias: Optional[str]
    pos: Position
    alias_pos: Optional[Position] = None

    @property
    def key(self) -> str:
        return self.alias if self.alias is not None else self.name


@dataclass
class SubqueryRef:
    query: "Query"
    alias: str
    pos: Position
    alias_pos: Optional[Position] = None

    @property
    def key(self) -> str:
        return self.alias


FromItem = Union[TableRef, SubqueryRef]


@dataclass
class JoinStep:
    """One `JOIN table_ref ON condition`, or one `, table_ref` (on is None)."""
    kind: str                     # "join" or "cross"
    ref: FromItem
    on: Optional[Condition]
    pos: Position


@dataclass
class OrderItem:
    column: ColumnRef
    direction: str                # "asc" or "desc"


@dataclass
class SelectBlock:
    distinct: bool
    items: Union[Star, List[SelectItem]]
    first: FromItem
    steps: List[JoinStep]
    where: Optional[Condition]
    group_by: List[ColumnRef]
    having: Optional[Condition]
    order_by: List[OrderItem]
    limit: Optional[int]
    pos: Position
    group_pos: Optional[Position] = None
    having_pos: Optional[Position] = None


@dataclass
class SetOp:
    op: str                       # union, union_all, intersect, except
    left: "Query"
    right: "Query"
    pos: Position


Query = Union[SelectBlock, SetOp]


# ---- other statements -------------------------------------------------------------
@dataclass
class ColumnDef:
    name: str
    type: str                     # INT or VARCHAR
    length: Optional[int]
    pos: Position


@dataclass
class CreateTable:
    name: str
    columns: List[ColumnDef]
    pos: Position


@dataclass
class Insert:
    table: str
    values: List[Union[IntLit, StrLit]]
    pos: Position
    table_pos: Optional[Position] = None


Statement = Union[CreateTable, Insert, SelectBlock, SetOp]


# ---- walking ------------------------------------------------------------------------
def condition_operands(c: Condition):
    """Every operand of a condition, left to right."""
    if isinstance(c, Compare):
        yield c.left
        yield c.right
    elif isinstance(c, (And, Or)):
        yield from condition_operands(c.left)
        yield from condition_operands(c.right)
    elif isinstance(c, Not):
        yield from condition_operands(c.operand)