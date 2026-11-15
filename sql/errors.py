"""One error format for the whole front end (Module 3, Section 6).

    error: <category>: <message> at line L, column C

Every error is raised as an SQLError carrying a category, a message and the
position of the token it is about. The CLI prints render() and nothing else,
so no stack trace is ever shown. Panic mode: the first error stops the run.
"""
from __future__ import annotations

from typing import Optional

from ra_engine.errors import Position

CATEGORIES = ("lexical", "syntax", "name", "ambiguity", "type", "aggregate", "schema")


class SQLError(Exception):
    category = "error"

    def __init__(self, message: str, pos: Optional[Position] = None):
        super().__init__(message)
        self.message = message
        self.pos = pos

    def render(self) -> str:
        where = f" at {self.pos}" if self.pos is not None else ""
        return f"error: {self.category}: {self.message}{where}"

    def __str__(self) -> str:
        return self.render()


class LexicalError(SQLError):
    category = "lexical"


class SyntaxError_(SQLError):
    """Named with a trailing underscore so it never shadows Python's SyntaxError."""
    category = "syntax"


class NameError_(SQLError):
    category = "name"


class AmbiguityError(SQLError):
    category = "ambiguity"


class TypeError_(SQLError):
    category = "type"


class AggregateError(SQLError):
    category = "aggregate"


class SchemaError(SQLError):
    category = "schema"