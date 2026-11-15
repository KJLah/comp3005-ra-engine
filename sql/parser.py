"""Recursive-descent parser for the SQL subset (GRAMMAR_SQL.md section 2).

One method per non-terminal. Precedence and associativity live in the
grammar's shape, not in special cases here:

  * condition -> or_cond -> and_cond -> not_cond -> atom, so NOT binds
    tightest, then AND, then OR. AND and OR loops build left-deep trees.
  * query is select_block { setop select_block }, consumed by one loop that
    folds to the left, so A UNION B EXCEPT C is (A UNION B) EXCEPT C, with
    all set operators at one precedence.

The parser only checks syntax. It never looks at the catalog.
"""
from __future__ import annotations

from typing import List, Optional

from . import ast as A
from . import lexer as L
from .errors import SyntaxError_


class Parser:
    def __init__(self, tokens: List[L.Token]):
        self.toks = tokens
        self.i = 0

    # ---- token helpers ---------------------------------------------------------
    @property
    def tok(self) -> L.Token:
        return self.toks[self.i]

    def peek(self, ahead: int = 1) -> L.Token:
        return self.toks[min(self.i + ahead, len(self.toks) - 1)]

    def next(self) -> L.Token:
        t = self.toks[self.i]
        if t.kind != L.EOF:
            self.i += 1
        return t

    def error(self, expected: str, tok: Optional[L.Token] = None) -> SyntaxError_:
        tok = tok or self.tok
        return SyntaxError_(f"expected {expected}, found {tok.describe()}", tok.pos)

    def at_kw(self, *words: str) -> bool:
        return self.tok.is_kw(*words)

    def accept_kw(self, *words: str) -> Optional[L.Token]:
        if self.at_kw(*words):
            return self.next()
        return None

    def expect_kw(self, word: str, expected: Optional[str] = None) -> L.Token:
        if not self.at_kw(word):
            raise self.error(expected or word)
        return self.next()

    def accept(self, kind: str) -> Optional[L.Token]:
        if self.tok.kind == kind:
            return self.next()
        return None

    def expect(self, kind: str, expected: Optional[str] = None) -> L.Token:
        if self.tok.kind != kind:
            raise self.error(expected or f"'{kind}'")
        return self.next()

    def ident(self, what: str = "a name") -> L.Token:
        if self.tok.kind == L.IDENT:
            return self.next()
        if self.tok.kind == L.KEYWORD:
            raise SyntaxError_(f"expected {what}, found keyword {self.tok.value} "
                               f"(keywords are reserved)", self.tok.pos)
        raise self.error(what)

    # ---- script := [ statement ] { ";" [ statement ] } EOF ----------------------
    def script(self) -> List[A.Statement]:
        stmts: List[A.Statement] = []
        while True:
            while self.accept(L.SEMI):
                pass
            if self.tok.kind == L.EOF:
                return stmts
            stmts.append(self.statement())
            if self.tok.kind == L.EOF:
                return stmts
            if self.tok.kind != L.SEMI:
                raise self.error("';' or end of input")

    # ---- statement := create | insert | query -------------------------------------
    def statement(self) -> A.Statement:
        if self.at_kw("CREATE"):
            return self.create()
        if self.at_kw("INSERT"):
            return self.insert()
        if self.at_kw("SELECT") or self.tok.kind == L.LPAREN:
            return self.query()
        raise self.error("SELECT, CREATE TABLE or INSERT INTO")

    # create := CREATE TABLE ident "(" coldef { "," coldef } ")"
    def create(self) -> A.CreateTable:
        start = self.expect_kw("CREATE")
        self.expect_kw("TABLE")
        name = self.ident("a table name")
        self.expect(L.LPAREN, "'(' before the column list")
        cols = [self.coldef()]
        while self.accept(L.COMMA):
            cols.append(self.coldef())
        self.expect(L.RPAREN, "',' or ')' after a column definition")
        return A.CreateTable(name.value, cols, start.pos)

    # coldef := ident ( INT | VARCHAR "(" int ")" )
    def coldef(self) -> A.ColumnDef:
        name = self.ident("a column name")
        if self.accept_kw("INT"):
            return A.ColumnDef(name.value, "INT", None, name.pos)
        if self.accept_kw("VARCHAR"):
            self.expect(L.LPAREN, "'(' after VARCHAR")
            n = self.expect(L.INT, "the VARCHAR length")
            self.expect(L.RPAREN, "')' after the VARCHAR length")
            return A.ColumnDef(name.value, "VARCHAR", n.value, name.pos)
        raise self.error("a column type (INT or VARCHAR(n))")

    # insert := INSERT INTO ident VALUES "(" literal { "," literal } ")"
    def insert(self) -> A.Insert:
        start = self.expect_kw("INSERT")
        self.expect_kw("INTO")
        table = self.ident("a table name")
        self.expect_kw("VALUES")
        self.expect(L.LPAREN, "'(' before the values")
        vals = [self.literal()]
        while self.accept(L.COMMA):
            vals.append(self.literal())
        self.expect(L.RPAREN, "',' or ')' after a value")
        return A.Insert(table.value, vals, start.pos, table.pos)

    def literal(self):
        t = self.tok
        if t.kind == L.INT:
            self.next()
            return A.IntLit(t.value, t.pos)
        if t.kind == L.STRING:
            self.next()
            return A.StrLit(t.value, t.pos)
        raise self.error("an integer or a quoted string")

    # ---- query := select_block { setop select_block } (left associative) ----------
    def query(self) -> A.Query:
        left = self.select_block()
        while self.at_kw("UNION", "INTERSECT", "EXCEPT"):
            op_tok = self.next()
            op = op_tok.value.lower()
            if op == "union" and self.accept_kw("ALL"):
                op = "union_all"
            right = self.select_block()
            left = A.SetOp(op, left, right, op_tok.pos)
        return left

    # select_block := SELECT ... | "(" query ")"
    def select_block(self) -> A.Query:
        if self.tok.kind == L.LPAREN:
            self.next()
            q = self.query()
            self.expect(L.RPAREN, "')' to close the parenthesised query")
            return q
        start = self.expect_kw("SELECT", "SELECT or '('")
        distinct = self.accept_kw("DISTINCT") is not None
        items = self.select_list()
        self.expect_kw("FROM", "',' or FROM")
        first, steps = self.from_list()
        where = None
        if self.accept_kw("WHERE"):
            where = self.condition()
        group_by: List[A.ColumnRef] = []
        having = None
        group_pos = having_pos = None
        g = self.accept_kw("GROUP")
        if g:
            group_pos = g.pos
            self.expect_kw("BY", "BY after GROUP")
            group_by = self.col_list()
            h = self.accept_kw("HAVING")
            if h:
                having_pos = h.pos
                having = self.condition()
        elif self.at_kw("HAVING"):
            raise SyntaxError_("HAVING needs a GROUP BY clause before it", self.tok.pos)
        order_by: List[A.OrderItem] = []
        if self.accept_kw("ORDER"):
            self.expect_kw("BY", "BY after ORDER")
            order_by = self.sort_list()
        limit = None
        if self.accept_kw("LIMIT"):
            n = self.expect(L.INT, "an integer after LIMIT")
            if n.value < 0:
                raise SyntaxError_("LIMIT must not be negative", n.pos)
            limit = n.value
        return A.SelectBlock(distinct, items, first, steps, where, group_by, having,
                             order_by, limit, start.pos, group_pos, having_pos)

    # select_list := "*" | select_item { "," select_item }
    def select_list(self):
        star = self.accept(L.STAR)
        if star:
            return A.Star(star.pos)
        items = [self.select_item()]
        while self.accept(L.COMMA):
            items.append(self.select_item())
        return items

    # select_item := ( column | aggregate ) [ AS ident ]
    def select_item(self) -> A.SelectItem:
        start = self.tok.pos
        if self.at_kw(*L.AGGREGATES):
            expr = self.aggregate()
        elif self.tok.kind == L.IDENT:
            expr = self.column()
        else:
            raise self.error("a column, an aggregate or '*'")
        alias = alias_pos = None
        if self.accept_kw("AS"):
            a = self.ident("an alias after AS")
            alias, alias_pos = a.value, a.pos
        return A.SelectItem(expr, alias, start, alias_pos)

    # aggregate := ( COUNT | SUM | AVG | MIN | MAX ) "(" ( column | "*" ) ")"
    def aggregate(self) -> A.AggCall:
        fn = self.next()
        self.expect(L.LPAREN, f"'(' after {fn.value}")
        if self.accept(L.STAR):
            arg = None
        else:
            if self.tok.kind != L.IDENT:
                raise self.error(f"a column or '*' inside {fn.value}( )")
            arg = self.column()
        self.expect(L.RPAREN, f"')' to close {fn.value}(")
        return A.AggCall(fn.value.lower(), arg, fn.pos)

    # column := ident [ "." ident ]
    def column(self) -> A.ColumnRef:
        first = self.ident("a column name")
        if self.accept(L.DOT):
            second = self.ident("a column name after '.'")
            return A.ColumnRef(first.value, second.value, first.pos)
        return A.ColumnRef(None, first.value, first.pos)

    def col_list(self) -> List[A.ColumnRef]:
        cols = [self.column()]
        while self.accept(L.COMMA):
            cols.append(self.column())
        return cols

    # sort_list := column [ ASC | DESC ] { "," column [ ASC | DESC ] }
    def sort_list(self) -> List[A.OrderItem]:
        items = []
        while True:
            col = self.column()
            d = self.accept_kw("ASC", "DESC")
            items.append(A.OrderItem(col, d.value.lower() if d else "asc"))
            if not self.accept(L.COMMA):
                return items

    # from_list := table_ref { "," table_ref }
    #            | table_ref JOIN table_ref ON condition { JOIN table_ref ON condition }
    def from_list(self):
        first = self.table_ref()
        steps: List[A.JoinStep] = []
        if self.at_kw("JOIN"):
            while self.at_kw("JOIN"):
                j = self.next()
                ref = self.table_ref()
                self.expect_kw("ON", "ON after the joined table")
                steps.append(A.JoinStep("join", ref, self.condition(), j.pos))
            if self.tok.kind == L.COMMA:
                raise SyntaxError_("a FROM list uses either commas or JOIN ... ON, "
                                   "not both", self.tok.pos)
        else:
            while self.tok.kind == L.COMMA:
                c = self.next()
                steps.append(A.JoinStep("cross", self.table_ref(), None, c.pos))
            if self.at_kw("JOIN"):
                raise SyntaxError_("a FROM list uses either commas or JOIN ... ON, "
                                   "not both", self.tok.pos)
        return first, steps

    # table_ref := ident [ [ AS ] ident ] | "(" query ")" [ AS ] ident
    def table_ref(self) -> A.FromItem:
        if self.tok.kind == L.LPAREN:
            start = self.next()
            q = self.query()
            self.expect(L.RPAREN, "')' to close the subquery")
            self.accept_kw("AS")
            if self.tok.kind != L.IDENT:
                raise SyntaxError_("a subquery in FROM must have an alias, as in "
                                   "(SELECT ...) AS t", self.tok.pos)
            a = self.next()
            return A.SubqueryRef(q, a.value, start.pos, a.pos)
        name = self.ident("a table name or '('")
        alias = alias_pos = None
        if self.accept_kw("AS"):
            a = self.ident("an alias after AS")
            alias, alias_pos = a.value, a.pos
        elif self.tok.kind == L.IDENT:
            a = self.next()
            alias, alias_pos = a.value, a.pos
        return A.TableRef(name.value, alias, name.pos, alias_pos)

    # ---- conditions --------------------------------------------------------------
    # condition := and_cond { OR and_cond }
    def condition(self) -> A.Condition:
        left = self.and_cond()
        while self.at_kw("OR"):
            t = self.next()
            left = A.Or(left, self.and_cond(), t.pos)
        return left

    # and_cond := not_cond { AND not_cond }
    def and_cond(self) -> A.Condition:
        left = self.not_cond()
        while self.at_kw("AND"):
            t = self.next()
            left = A.And(left, self.not_cond(), t.pos)
        return left

    # not_cond := NOT not_cond | atom
    def not_cond(self) -> A.Condition:
        if self.at_kw("NOT"):
            t = self.next()
            return A.Not(self.not_cond(), t.pos)
        return self.atom()

    # atom := "(" condition ")" | comparison
    def atom(self) -> A.Condition:
        if self.tok.kind == L.LPAREN:
            self.next()
            c = self.condition()
            self.expect(L.RPAREN, "')' to close the condition")
            return c
        return self.comparison()

    # comparison := operand compop operand
    def comparison(self) -> A.Compare:
        left = self.operand()
        if self.tok.kind != L.OP:
            raise self.error("a comparison operator (=, <>, <, <=, >, >=)")
        op = self.next()
        right = self.operand()
        return A.Compare(op.value, left, right, op.pos)

    # operand := column | int | string | aggregate
    def operand(self) -> A.Operand:
        t = self.tok
        if t.kind == L.INT:
            self.next()
            return A.IntLit(t.value, t.pos)
        if t.kind == L.STRING:
            self.next()
            return A.StrLit(t.value, t.pos)
        if t.is_kw(*L.AGGREGATES):
            return self.aggregate()
        if t.kind == L.IDENT:
            return self.column()
        raise self.error("a column, an integer, a string or an aggregate")


# ---- entry points ------------------------------------------------------------------
def parse_script(text: str) -> List[A.Statement]:
    return Parser(L.tokenize(text)).script()


def parse_query(text: str) -> A.Query:
    """Exactly one query, optionally followed by ';'."""
    p = Parser(L.tokenize(text))
    q = p.query()
    p.accept(L.SEMI)
    if p.tok.kind != L.EOF:
        raise p.error("end of input")
    return q


def parse_condition(text: str) -> A.Condition:
    """A condition string on its own, as stored in a logical plan."""
    p = Parser(L.tokenize(text))
    c = p.condition()
    if p.tok.kind != L.EOF:
        raise p.error("AND, OR or end of condition")
    return c