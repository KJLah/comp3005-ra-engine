"""Hand-written, character-by-character tokenizer (GRAMMAR.md section 5.1.1).

It uses no regular expressions. The scanner looks at the current character,
decides which kind of token is starting, and consumes characters until that
token ends. Maximal munch is done by peeking one character ahead (for
example ``>`` versus ``>=``).

There are two modes:
  * query mode (default)
  * value mode, between ``{`` and ``}`` of a relation definition. Here bare
    words are values and newlines separate tuples.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import List, Optional, Union

from .errors import LexicalError, Position

# ---- token kinds -----------------------------------------------------------
IDENT = "IDENT"
KEYWORD = "KEYWORD"
NUMBER = "NUMBER"
STRING = "STRING"
OP = "OP"            # = != < <= > >=
LBRACKET = "["
RBRACKET = "]"
LPAREN = "("
RPAREN = ")"
LBRACE = "{"
RBRACE = "}"
COMMA = ","
DOT = "."
NEWLINE = "NEWLINE"  # only emitted in value mode
EOF = "EOF"

# Soft keywords: reserved as relation names, allowed as attribute names.
OPERATOR_KEYWORDS = frozenset(
    ["select", "project", "rename", "union", "intersect", "minus", "times", "join"]
)
# Hard keywords: reserved everywhere.
CONDITION_KEYWORDS = frozenset(["and", "or", "not"])
KEYWORDS = OPERATOR_KEYWORDS | CONDITION_KEYWORDS

SINGLE_CHAR_TOKENS = {"[": LBRACKET, "]": RBRACKET, "(": LPAREN, ")": RPAREN,
                      ",": COMMA, ".": DOT}


@dataclass(frozen=True)
class Token:
    kind: str
    text: str                                   # exact source text
    pos: Position
    value: Union[int, float, str, None] = None  # for NUMBER / STRING

    def describe(self) -> str:
        """How the token is named in error messages."""
        if self.kind == EOF:
            return "end of input"
        if self.kind == NEWLINE:
            return "end of line"
        return f"'{self.text}'"


# ---- character classes (written out by hand) ---------------------------------
def is_letter(ch: str) -> bool:
    return ("a" <= ch <= "z") or ("A" <= ch <= "Z") or ch == "_"


def is_digit(ch: str) -> bool:
    return "0" <= ch <= "9"


def is_space(ch: str) -> bool:
    return ch in (" ", "\t", "\r", "\n")


def number_value(text: str) -> Optional[Union[int, float]]:
    """If ``text`` has the NUMBER shape  [-] digit+ [ . digit+ ], return its value.
    Otherwise return None. Used to classify bare values in value mode."""
    i = 0
    n = len(text)
    if i < n and text[i] == "-":
        i += 1
    start = i
    while i < n and is_digit(text[i]):
        i += 1
    if i == start:
        return None
    is_float = False
    if i < n and text[i] == ".":
        i += 1
        frac = i
        while i < n and is_digit(text[i]):
            i += 1
        if i == frac:
            return None
        is_float = True
    if i != n:
        return None
    return float(text) if is_float else int(text)


class Lexer:
    def __init__(self, text: str):
        self.text = text
        self.i = 0
        self.line = 1
        self.col = 1
        self.value_mode = False
        self.tokens: List[Token] = []

    # ---- low-level cursor -------------------------------------------------
    def peek(self, ahead: int = 0) -> str:
        j = self.i + ahead
        return self.text[j] if j < len(self.text) else ""  # "" means end of input

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
        self.tokens.append(Token(kind, self.text[start.offset:self.i], start, value))

    # ---- driver -------------------------------------------------------------
    def tokenize(self) -> List[Token]:
        while self.i < len(self.text):
            if self.value_mode:
                self.scan_value_mode()
            else:
                self.scan_query_mode()
        if self.value_mode:
            raise LexicalError("relation body is never closed; expected '}'", self.here())
        self.tokens.append(Token(EOF, "", self.here()))
        return self.tokens

    # ---- query mode: one token (or skipped whitespace/comment) per call -----
    def scan_query_mode(self) -> None:
        ch = self.peek()
        start = self.here()

        if is_space(ch):
            self.advance()
        elif ch == "/" and self.peek(1) == "/":
            self.skip_comment()
        elif is_letter(ch):
            self.scan_word(start)
        elif is_digit(ch) or (ch == "-" and is_digit(self.peek(1))):
            self.scan_number(start)
        elif ch == "'":
            self.scan_string(start)
        elif ch in ("<", ">"):
            # maximal munch: '<=' / '>=' are single tokens
            self.advance()
            if self.peek() == "=":
                self.advance()
            self.emit(OP, start)
        elif ch == "!":
            self.advance()
            if self.peek() != "=":
                raise LexicalError("'!' must be followed by '=' (the operator is '!=')", start)
            self.advance()
            self.emit(OP, start)
        elif ch == "=":
            self.advance()
            self.emit(OP, start)
        elif ch == "-":
            raise LexicalError("'-' is only allowed as the sign of a number, e.g. -30", start)
        elif ch == "{":
            self.advance()
            self.emit(LBRACE, start)
            self.value_mode = True
        elif ch in SINGLE_CHAR_TOKENS:
            self.advance()
            self.emit(SINGLE_CHAR_TOKENS[ch], start)
        else:
            raise LexicalError(f"unexpected character {ch!r}", start)

    def scan_word(self, start: Position) -> None:
        while is_letter(self.peek()) or is_digit(self.peek()):
            self.advance()
        word = self.text[start.offset:self.i]
        self.emit(KEYWORD if word in KEYWORDS else IDENT, start)

    def scan_number(self, start: Position) -> None:
        if self.peek() == "-":
            self.advance()
        while is_digit(self.peek()):
            self.advance()
        is_float = False
        # a '.' is part of the number only if a digit follows it
        if self.peek() == "." and is_digit(self.peek(1)):
            is_float = True
            self.advance()
            while is_digit(self.peek()):
                self.advance()
        if is_letter(self.peek()):
            while is_letter(self.peek()) or is_digit(self.peek()):
                self.advance()
            bad = self.text[start.offset:self.i]
            raise LexicalError(
                f"malformed number {bad!r} (a number cannot run into letters; "
                f"quote it if it is a string)", start)
        text = self.text[start.offset:self.i]
        self.emit(NUMBER, start, float(text) if is_float else int(text))

    def scan_string(self, start: Position) -> None:
        self.advance()  # opening quote
        chars: List[str] = []
        while True:
            ch = self.peek()
            if ch == "" or ch == "\n":
                raise LexicalError("unterminated string: no closing quote (') "
                                   "before the end of the line", start)
            if ch == "'":
                if self.peek(1) == "'":      # '' is one literal quote
                    self.advance()
                    self.advance()
                    chars.append("'")
                    continue
                self.advance()               # closing quote
                break
            chars.append(self.advance())
        self.emit(STRING, start, "".join(chars))

    def skip_comment(self) -> None:
        while self.peek() not in ("", "\n"):
            self.advance()

    # ---- value mode (inside { } of a relation definition) ------------------
    def scan_value_mode(self) -> None:
        ch = self.peek()
        start = self.here()

        if ch in (" ", "\t", "\r"):
            self.advance()
        elif ch == "\n":
            self.advance()
            # blank lines and comment lines collapse into one NEWLINE
            if self.tokens and self.tokens[-1].kind not in (NEWLINE, LBRACE):
                self.emit(NEWLINE, start)
        elif ch == "/" and self.peek(1) == "/":
            self.skip_comment()
        elif ch == ",":
            self.advance()
            self.emit(COMMA, start)
        elif ch == "}":
            self.advance()
            self.emit(RBRACE, start)
            self.value_mode = False
        elif ch == "'":
            self.scan_string(start)
        elif ch in ("(", ")", "{"):
            raise LexicalError(f"{ch!r} inside a value must be quoted, e.g. 'a{ch}b'", start)
        else:
            self.scan_bare_value(start)

    def scan_bare_value(self, start: Position) -> None:
        while True:
            ch = self.peek()
            if ch == "" or is_space(ch) or ch in (",", "'", "(", ")", "{", "}"):
                break
            self.advance()
        if self.peek() == "'":
            raise LexicalError("a value containing a quote must be written in quotes, "
                               "with the quote doubled, e.g. 'O''Brien'", self.here())
        text = self.text[start.offset:self.i]
        num = number_value(text)
        if num is not None:
            self.emit(NUMBER, start, num)
        else:
            self.emit(STRING, start, text)


def tokenize(text: str) -> List[Token]:
    return Lexer(text).tokenize()
