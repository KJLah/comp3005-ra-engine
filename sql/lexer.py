"""Hand-written SQL scanner (Module 3, Section 1; GRAMMAR_SQL.md section 1).

One character at a time, no regular expressions, no generated code. The
character classes come from the Bonus 1 lexer so both front ends agree on
what a letter and a digit are.

    keywords     case insensitive: SELECT, select and SeLeCt are one token.
                 The token keeps the keyword in upper case as its value.
    identifiers  case sensitive: letter or underscore, then letters, digits,
                 underscores. Emp and emp are different names.
    strings      single quoted; '' inside is one literal quote.
    numbers      integers only, optionally signed (-5, +5). A sign is part of
                 the number only when a digit follows it directly.
    operators    maximal munch: <=, >= and <> are single tokens.
    comments     -- to the end of the line.
    statements   separated by ';'.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import List, Optional, Union

from ra_engine.errors import Position
from ra_engine.lexer import is_digit, is_letter, is_space

from .errors import LexicalError

# token kinds
KEYWORD = "KEYWORD"
IDENT = "IDENT"
INT = "INT"
STRING = "STRING"
OP = "OP"            # = <> < <= > >=
LPAREN = "("
RPAREN = ")"
COMMA = ","
DOT = "."
SEMI = ";"
STAR = "*"
EOF = "EOF"

KEYWORDS = frozenset("""
    SELECT DISTINCT FROM WHERE GROUP BY HAVING ORDER ASC DESC LIMIT AS JOIN ON
    AND OR NOT UNION ALL INTERSECT EXCEPT
    CREATE TABLE INSERT INTO VALUES INT VARCHAR
    COUNT SUM AVG MIN MAX
""".split())

AGGREGATES = ("COUNT", "SUM", "AVG", "MIN", "MAX")

PUNCT = {"(": LPAREN, ")": RPAREN, ",": COMMA, ".": DOT, ";": SEMI, "*": STAR}


@dataclass(frozen=True)
class Token:
    kind: str
    text: str                       # the source text of the token
    value: Union[str, int, None]    # keyword in upper case, int value, string body
    pos: Position

    def is_kw(self, *words: str) -> bool:
        return self.kind == KEYWORD and self.value in words

    def describe(self) -> str:
        if self.kind == EOF:
            return "end of input"
        if self.kind == STRING:
            return f"string {self.text}"
        if self.kind == KEYWORD:
            return f"keyword {self.value}"
        return f"'{self.text}'"


class Lexer:
    def __init__(self, text: str):
        self.text = text
        self.i = 0
        self.line = 1
        self.col = 1
        self.tokens: List[Token] = []

    def peek(self, ahead: int = 0) -> str:
        j = self.i + ahead
        return self.text[j] if j < len(self.text) else ""

    def advance(self) -> str:
        ch = self.text[self.i]
        self.i += 1
        if ch == "\n":
            self.line += 1
            self.col = 1
        else:
            self.col += 1
        return ch

    def here(self) -> Position:
        return Position(self.i, self.line, self.col)

    def emit(self, kind: str, start: Position, value=None) -> None:
        self.tokens.append(Token(kind, self.text[start.offset:self.i], value, start))

    def tokenize(self) -> List[Token]:
        while self.i < len(self.text):
            self.scan_one()
        self.tokens.append(Token(EOF, "", None, self.here()))
        return self.tokens

    def scan_one(self) -> None:
        ch = self.peek()
        start = self.here()
        if is_space(ch):
            self.advance()
        elif ch == "-" and self.peek(1) == "-":
            while self.i < len(self.text) and self.peek() != "\n":
                self.advance()
        elif is_letter(ch):
            self.scan_word(start)
        elif is_digit(ch) or (ch in "+-" and is_digit(self.peek(1))):
            self.scan_number(start)
        elif ch == "'":
            self.scan_string(start)
        elif ch == "<":
            self.advance()
            if self.peek() in ("=", ">"):        # maximal munch: <= and <>
                self.advance()
            self.emit(OP, start, self.text[start.offset:self.i])
        elif ch == ">":
            self.advance()
            if self.peek() == "=":
                self.advance()
            self.emit(OP, start, self.text[start.offset:self.i])
        elif ch == "=":
            self.advance()
            self.emit(OP, start, "=")
        elif ch in PUNCT:
            self.advance()
            self.emit(PUNCT[ch], start)
        elif ch in "+-":
            raise LexicalError(f"'{ch}' is only allowed as the sign of an integer, "
                               f"as in {ch}30", start)
        elif ch == "!":
            raise LexicalError("unexpected character '!'; the not-equal operator "
                               "is '<>'", start)
        elif ch == '"':
            raise LexicalError("strings use single quotes, as in 'Ottawa'", start)
        else:
            raise LexicalError(f"unexpected character {ch!r}", start)

    def scan_word(self, start: Position) -> None:
        while is_letter(self.peek()) or is_digit(self.peek()):
            self.advance()
        word = self.text[start.offset:self.i]
        upper = word.upper()
        if upper in KEYWORDS:
            self.emit(KEYWORD, start, upper)
        else:
            self.emit(IDENT, start, word)

    def scan_number(self, start: Position) -> None:
        if self.peek() in "+-":
            self.advance()
        while is_digit(self.peek()):
            self.advance()
        if self.peek() == ".":
            raise LexicalError("only integers are allowed; "
                               f"'{self.text[start.offset:self.i]}.' starts a decimal",
                               start)
        if is_letter(self.peek()):
            raise LexicalError(f"a name cannot start with a digit "
                               f"('{self.text[start.offset:self.i]}{self.peek()}...')",
                               start)
        try:
            value = int(self.text[start.offset:self.i])
        except ValueError:
            raise LexicalError(
                "integer literal is too large to parse",
                start,
            ) from None

        self.emit(INT, start, value)

    def scan_string(self, start: Position) -> None:
        self.advance()                         # opening quote
        chars: List[str] = []
        while True:
            if self.i >= len(self.text):
                raise LexicalError("string is never closed; expected a closing \"'\"",
                                   start)
            ch = self.peek()
            if ch == "\n":
                raise LexicalError("string is not closed before the end of the line",
                                   start)
            if ch == "'":
                if self.peek(1) == "'":        # '' is one literal quote
                    self.advance()
                    self.advance()
                    chars.append("'")
                    continue
                self.advance()
                break
            chars.append(self.advance())
        self.emit(STRING, start, "".join(chars))


def tokenize(text: str) -> List[Token]:
    return Lexer(text).tokenize()


def quote(s: str) -> str:
    """The SQL spelling of a string literal: the inverse of scan_string."""
    return "'" + s.replace("'", "''") + "'"