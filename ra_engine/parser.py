"""Recursive-descent parser. There is one method per non-terminal in GRAMMAR.md.

Precedence comes from which method calls which (stratification). Associativity
comes from the while-loops, which build the tree left-deep. There is no left
recursion: see GRAMMAR.md section 5.4.
"""
from __future__ import annotations

from typing import List, Optional

from . import lexer as L
from .ast_nodes import (And, Attr, Compare, Condition, Expr, Join, Not, Num, Or,
                        Project, RelationDef, RelationRef, Rename, Select, SetOp,
                        Str, Times)
from .errors import ParseError
from .lexer import Token


class Parser:
    def __init__(self, tokens: List[Token]):
        self.tokens = tokens
        self.k = 0  # index of the current token

    # ---- helpers ---------------------------------------------------------------
    def peek(self, ahead: int = 0) -> Token:
        j = min(self.k + ahead, len(self.tokens) - 1)  # the last token is always EOF
        return self.tokens[j]

    def advance(self) -> Token:
        tok = self.tokens[self.k]
        if tok.kind != L.EOF:
            self.k += 1
        return tok

    def at(self, kind: str, text: Optional[str] = None) -> bool:
        tok = self.peek()
        return tok.kind == kind and (text is None or tok.text == text)

    def at_keyword(self, *words: str) -> bool:
        tok = self.peek()
        return tok.kind == L.KEYWORD and tok.text in words

    def expect(self, kind: str, what: str, text: Optional[str] = None) -> Token:
        if self.at(kind, text):
            return self.advance()
        tok = self.peek()
        raise ParseError(f"expected {what}, found {tok.describe()}", tok.pos)

    def expect_close(self, kind: str, opener: Token) -> Token:
        """Expect ')' or ']' and name the bracket that it closes."""
        if self.at(kind):
            return self.advance()
        tok = self.peek()
        raise ParseError(
            f"missing '{kind}' to close the '{opener.text}' opened at column "
            f"{opener.pos.col}; found {tok.describe()}", tok.pos)

    # ---- entry points ----------------------------------------------------------
    def parse_query(self) -> Expr:
        tree = self.expr()
        if not self.at(L.EOF):
            tok = self.peek()
            if tok.kind == L.RPAREN:
                raise ParseError("unmatched ')': there is no '(' for it to close", tok.pos)
            raise ParseError(
                f"unexpected {tok.describe()} after a complete expression "
                f"(missing operator?)", tok.pos)
        return tree

    def parse_definitions(self) -> List[RelationDef]:
        defs = []
        while not self.at(L.EOF):
            defs.append(self.relation_def())
        return defs

    # ---- expressions: lowest precedence first ------------------------------------
    # expr = union_expr
    def expr(self) -> Expr:
        return self.union_expr()

    # union_expr = intersect_expr { ( "union" | "minus" ) intersect_expr }
    def union_expr(self) -> Expr:
        left = self.intersect_expr()
        while self.at_keyword("union", "minus"):
            op = self.advance()
            right = self.intersect_expr()
            left = SetOp(op.text, left, right, op.pos)   # left-deep => left-assoc
        return left

    # intersect_expr = product_expr { "intersect" product_expr }
    def intersect_expr(self) -> Expr:
        left = self.product_expr()
        while self.at_keyword("intersect"):
            op = self.advance()
            right = self.product_expr()
            left = SetOp("intersect", left, right, op.pos)
        return left

    # product_expr = atom { ( "times" | "join" "[" condition "]" ) atom }
    def product_expr(self) -> Expr:
        left = self.atom()
        while self.at_keyword("times", "join"):
            op = self.advance()
            if op.text == "times":
                right = self.atom()
                left = Times(left, right, op.pos)
            else:
                opener = self.expect(L.LBRACKET, "'[' after 'join' (join needs a condition)")
                cond = self.condition()
                self.expect_close(L.RBRACKET, opener)
                right = self.atom()
                left = Join(cond, left, right, op.pos)
        return left

    # atom = select[..](expr) | project[..](expr) | rename[..](expr) | "(" expr ")" | IDENT
    def atom(self) -> Expr:
        tok = self.peek()

        if self.at_keyword("select"):
            self.advance()
            opener = self.expect(L.LBRACKET, "'[' after 'select'")
            cond = self.condition()
            self.expect_close(L.RBRACKET, opener)
            child = self.parenthesised_input("select")
            return Select(cond, child, tok.pos)

        if self.at_keyword("project"):
            self.advance()
            opener = self.expect(L.LBRACKET, "'[' after 'project'")
            attrs = self.attr_list()
            self.expect_close(L.RBRACKET, opener)
            child = self.parenthesised_input("project")
            return Project(attrs, child, tok.pos)

        if self.at_keyword("rename"):
            self.advance()
            opener = self.expect(L.LBRACKET, "'[' after 'rename'")
            name_tok = self.relation_name("the new relation name")
            self.expect_close(L.RBRACKET, opener)
            child = self.parenthesised_input("rename")
            return Rename(name_tok.text, child, tok.pos)

        if tok.kind == L.LPAREN:
            opener = self.advance()
            inner = self.expr()
            self.expect_close(L.RPAREN, opener)
            return inner

        if tok.kind == L.IDENT:
            self.advance()
            return RelationRef(tok.text, tok.pos)

        # everything below is an error: say what was expected and what was found
        expected = "a relation name, '(' or select/project/rename"
        if tok.kind == L.KEYWORD and tok.text in L.OPERATOR_KEYWORDS:
            raise ParseError(f"missing operand: expected {expected} before "
                             f"'{tok.text}'", tok.pos)
        if tok.kind == L.KEYWORD:
            raise ParseError(f"'{tok.text}' can only be used inside a condition", tok.pos)
        if tok.kind == L.EOF:
            raise ParseError(f"missing operand: expected {expected}, "
                             f"found end of input", tok.pos)
        raise ParseError(f"expected {expected}, found {tok.describe()}", tok.pos)

    def parenthesised_input(self, op_name: str) -> Expr:
        opener = self.expect(L.LPAREN, f"'(' with the input of {op_name} after ']'")
        child = self.expr()
        self.expect_close(L.RPAREN, opener)
        return child

    def relation_name(self, what: str) -> Token:
        tok = self.peek()
        if tok.kind == L.IDENT:
            return self.advance()
        if tok.kind == L.KEYWORD:
            raise ParseError(f"'{tok.text}' is a keyword and cannot be used as "
                             f"{what}", tok.pos)
        raise ParseError(f"expected {what}, found {tok.describe()}", tok.pos)

    # ---- attribute names ------------------------------------------------------------
    # name = IDENT | soft_keyword        (GRAMMAR.md 5.1.2)
    def is_name(self, tok: Token) -> bool:
        return tok.kind == L.IDENT or (tok.kind == L.KEYWORD and
                                       tok.text in L.OPERATOR_KEYWORDS)

    def name(self, what: str = "an attribute name") -> Token:
        tok = self.peek()
        if self.is_name(tok):
            return self.advance()
        if tok.kind == L.KEYWORD:  # and / or / not
            raise ParseError(f"'{tok.text}' is reserved and cannot be used as "
                             f"{what}", tok.pos)
        raise ParseError(f"expected {what}, found {tok.describe()}", tok.pos)

    # attr_ref = [ IDENT "." ] name          (needs 2 tokens of lookahead)
    def attr_ref(self) -> Attr:
        first = self.name()
        if self.at(L.DOT):
            if first.kind != L.IDENT:
                raise ParseError(f"'{first.text}' is a keyword and cannot be a "
                                 f"relation name in a qualified attribute", first.pos)
            self.advance()
            attr = self.name("an attribute name after '.'")
            return Attr(attr.text, first.text, first.pos)
        return Attr(first.text, None, first.pos)

    # attr_list = attr_ref { "," attr_ref }
    def attr_list(self) -> List[Attr]:
        if self.at(L.RBRACKET):
            raise ParseError("empty attribute list: project needs at least one attribute",
                             self.peek().pos)
        attrs = [self.attr_ref()]
        while self.at(L.COMMA):
            self.advance()
            attrs.append(self.attr_ref())
        return attrs

    # ---- conditions -------------------------------------------------------------
    # condition = or_cond
    def condition(self) -> Condition:
        if self.at(L.RBRACKET):
            raise ParseError("empty condition", self.peek().pos)
        return self.or_cond()

    # or_cond = and_cond { "or" and_cond }
    def or_cond(self) -> Condition:
        left = self.and_cond()
        while self.at_keyword("or"):
            op = self.advance()
            left = Or(left, self.and_cond(), op.pos)
        return left

    # and_cond = not_cond { "and" not_cond }
    def and_cond(self) -> Condition:
        left = self.not_cond()
        while self.at_keyword("and"):
            op = self.advance()
            left = And(left, self.not_cond(), op.pos)
        return left

    # not_cond = "not" not_cond | cond_atom
    def not_cond(self) -> Condition:
        if self.at_keyword("not"):
            op = self.advance()
            return Not(self.not_cond(), op.pos)
        return self.cond_atom()

    # cond_atom = "(" condition ")" | comparison
    def cond_atom(self) -> Condition:
        if self.at(L.LPAREN):
            opener = self.advance()
            inner = self.condition()
            self.expect_close(L.RPAREN, opener)
            return inner
        return self.comparison()

    # comparison = operand CMP_OP operand
    def comparison(self) -> Condition:
        left = self.operand()
        if not self.at(L.OP):
            tok = self.peek()
            raise ParseError(f"expected a comparison operator (=, !=, <, <=, >, >=) "
                             f"after {self.describe_operand(left)}, found "
                             f"{tok.describe()}", tok.pos)
        op = self.advance()
        right = self.operand()
        if self.at(L.OP):
            raise ParseError("comparisons cannot be chained; use 'and', "
                             "e.g. a < b and b < c", self.peek().pos)
        return Compare(op.text, left, right, op.pos)

    # operand = NUMBER | STRING | attr_ref
    def operand(self):
        tok = self.peek()
        if tok.kind == L.NUMBER:
            self.advance()
            return Num(tok.value, tok.pos)
        if tok.kind == L.STRING:
            self.advance()
            return Str(tok.value, tok.pos)
        if self.is_name(tok):
            return self.attr_ref()
        if tok.kind == L.KEYWORD:
            raise ParseError(f"missing operand before '{tok.text}' "
                             f"('{tok.text}' is reserved)", tok.pos)
        raise ParseError(f"expected a number, a quoted string or an attribute name, "
                         f"found {tok.describe()}", tok.pos)

    @staticmethod
    def describe_operand(node) -> str:
        if isinstance(node, Attr):
            return f"'{node.display()}'"
        if isinstance(node, Str):
            return "a string"
        return "a number"

    # ---- relation definitions -------------------------------------------------------
    # relation_def = IDENT "(" name { "," name } ")" "=" "{" body "}"
    def relation_def(self) -> RelationDef:
        name_tok = self.relation_name("a relation name")
        opener = self.expect(L.LPAREN, f"'(' and the attribute list of {name_tok.text}")
        attrs = [self.name()]
        while self.at(L.COMMA):
            self.advance()
            attrs.append(self.name())
        self.expect_close(L.RPAREN, opener)
        self.expect(L.OP, "'=' after the attribute list", text="=")
        brace = self.expect(L.LBRACE, "'{' to start the tuples")
        rows, row_positions = self.body()
        self.expect_close(L.RBRACE, brace)
        return RelationDef(name_tok.text, [a.text for a in attrs], [a.pos for a in attrs],
                           rows, row_positions, name_tok.pos)

    # body = [ NEWLINE ] [ tuple { NEWLINE tuple } [ NEWLINE ] ]
    def body(self):
        rows, positions = [], []
        if self.at(L.NEWLINE):
            self.advance()
        while not self.at(L.RBRACE) and not self.at(L.EOF):
            positions.append(self.peek().pos)
            rows.append(self.tuple_())
            if self.at(L.NEWLINE):
                self.advance()
            elif not self.at(L.RBRACE):
                tok = self.peek()
                raise ParseError(f"expected ',' or a new line after a value, "
                                 f"found {tok.describe()}", tok.pos)
        return rows, positions

    # tuple = value { "," value }
    def tuple_(self):
        values = [self.value()]
        while self.at(L.COMMA):
            self.advance()
            values.append(self.value())
        return values

    def value(self):
        tok = self.peek()
        if tok.kind in (L.NUMBER, L.STRING):
            self.advance()
            return tok.value
        raise ParseError(f"expected a value, found {tok.describe()}", tok.pos)


def parse_query(text: str) -> Expr:
    return Parser(L.tokenize(text)).parse_query()


def parse_definitions(text: str) -> List[RelationDef]:
    return Parser(L.tokenize(text)).parse_definitions()
