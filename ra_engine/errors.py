"""Error types. Every problem the user can cause becomes one of these.

The CLI catches RAError and prints ``render()``, so a user never sees a
Python stack trace. Each error carries a category (the five required
categories), a message, and optionally a Position in the source text.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional


@dataclass(frozen=True)
class Position:
    offset: int  # 0-based index into the source text
    line: int    # 1-based
    col: int     # 1-based

    def __str__(self) -> str:
        return f"line {self.line}, column {self.col}"


class RAError(Exception):
    category = "Error"

    def __init__(self, message: str, pos: Optional[Position] = None):
        super().__init__(message)
        self.message = message
        self.pos = pos
        # Filled in by whoever knows which text was being processed
        # (the CLI knows whether it was a query or a data file).
        self.source: Optional[str] = None
        self.source_name: Optional[str] = None

    def attach_source(self, source: str, name: str) -> "RAError":
        if self.source is None:
            self.source = source
            self.source_name = name
        return self

    def render(self) -> str:
        where = ""
        if self.pos is not None:
            where = f" at {self.pos}"
            if self.source_name:
                where += f" of {self.source_name}"
        out = f"{self.category}{where}: {self.message}"
        if self.pos is not None and self.source is not None:
            lines = self.source.split("\n")
            if 1 <= self.pos.line <= len(lines):
                text = lines[self.pos.line - 1]
                out += "\n    " + text + "\n    " + " " * (self.pos.col - 1) + "^"
        return out


class LexicalError(RAError):
    category = "Lexical error"


class ParseError(RAError):
    category = "Syntax error"


class RANameError(RAError):
    category = "Name error"


class SchemaError(RAError):
    category = "Schema error"


class RATypeError(RAError):
    category = "Type error"
