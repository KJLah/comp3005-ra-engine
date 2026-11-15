"""Cutting a script into statements, and reading the shell's own commands.

The shell commands (EXPLAIN, EXPLAIN ANALYZE, ANALYZE, SET, LOAD) are
recognised here, by their first words, and are not added to the Module 3
grammar: integration means wiring, not rewriting. Everything that is not a
shell command is handed to Module 3 unchanged.

Every statement keeps its place in the input: the text before it is
replaced by spaces (newlines kept), so a Module 3 error still reports the
line and column of the whole script. The same is done to a prefix such as
EXPLAIN before the query goes to Module 3.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import List, Optional, Tuple


class ShellError(Exception):
    def render(self) -> str:
        return f"error: shell: {self}"


def blank(text: str) -> str:
    """Spaces in place of every character except newlines."""
    return "".join(ch if ch == "\n" else " " for ch in text)


def split_statements(text: str) -> List[str]:
    """Split on ';' outside strings and comments. Each piece is padded so
    that its characters keep their line and column. Empty pieces (only
    blanks and comments) are dropped. A final statement without ';' counts."""
    out: List[str] = []
    start, i, n = 0, 0, len(text)
    while i < n:
        ch = text[i]
        if ch == "'":
            i += 1
            while i < n and text[i] != "'" and text[i] != "\n":
                i += 1
            i += 1                       # closing quote ('' is two strings in a row)
            continue
        if ch == "-" and text.startswith("--", i):
            while i < n and text[i] != "\n":
                i += 1
            continue
        if ch == ";":
            _keep(out, text, start, i)
            start = i + 1
        i += 1
    _keep(out, text, start, n)
    return out


def _keep(out: List[str], text: str, start: int, end: int) -> None:
    piece = blank(text[:start]) + text[start:end]
    if _first_word(piece, 0) is not None:
        out.append(piece)


def _skip(text: str, i: int) -> int:
    """Skip blanks and -- comments."""
    n = len(text)
    while i < n:
        if text[i] in " \t\r\n":
            i += 1
        elif text.startswith("--", i):
            while i < n and text[i] != "\n":
                i += 1
        else:
            break
    return i


def _first_word(text: str, i: int) -> Optional[Tuple[str, int, int]]:
    """The next token (a word, a quoted string, or one other character):
    (token, start, end), or None at the end."""
    i = _skip(text, i)
    n = len(text)
    if i >= n:
        return None
    j = i
    if text[i] == "'":
        j += 1
        buf = []
        while True:
            if j >= n or text[j] == "\n":
                raise ShellError("unterminated string")
            if text[j] == "'":
                if j + 1 < n and text[j + 1] == "'":
                    buf.append("'")
                    j += 2
                    continue
                return ("'" + "".join(buf), i, j + 1)
            buf.append(text[j])
            j += 1
    if text[i].isalnum() or text[i] in "_\\":
        j += 1
        while j < n and (text[j].isalnum() or text[j] == "_"):
            j += 1
        return (text[i:j], i, j)
    return (text[i], i, i + 1)


def words(text: str, limit: int = 8) -> List[Tuple[str, int, int]]:
    out, i = [], 0
    while len(out) < limit:
        w = _first_word(text, i)
        if w is None:
            break
        out.append(w)
        i = w[2]
    return out


@dataclass
class Command:
    kind: str                 # sql, explain, explain_analyze, analyze, set, load, meta
    sql: str = ""             # the query text, padded to keep positions
    tables: Tuple[str, ...] = ()
    name: str = ""            # SET name / LOAD table / meta command
    value: str = ""           # SET value / LOAD path
    header: bool = False      # LOAD ... HEADER


def classify(stmt: str) -> Command:
    """Decide what one statement is. Shell keywords are case insensitive,
    like SQL keywords; table names keep their case."""
    ws = words(stmt)
    if not ws:
        raise ShellError("empty statement")
    head = ws[0][0].upper()

    if head == "EXPLAIN":
        if len(ws) > 1 and ws[1][0].upper() == "ANALYZE":
            return Command("explain_analyze", blank(stmt[:ws[1][2]]) + stmt[ws[1][2]:])
        return Command("explain", blank(stmt[:ws[0][2]]) + stmt[ws[0][2]:])

    if head == "ANALYZE":
        rest = words(stmt, 1000)[1:]
        names = [w for w, _, _ in rest if w != ","]
        for w in names:
            if not (w[0].isalpha() or w[0] == "_"):
                raise ShellError(f"ANALYZE takes table names, found {w!r}")
        return Command("analyze", tables=tuple(names))

    if head == "SET":
        rest = [w for w, _, _ in words(stmt)[1:]]
        if len(rest) == 3 and (rest[1] == "=" or rest[1].upper() == "TO"):
            return Command("set", name=rest[0].lower(), value=rest[2])
        raise ShellError("expected SET frames = n")

    if head == "LOAD":
        return _load(stmt, ws)

    if head.startswith("\\"):
        return Command("meta", name=head.lower(),
                       tables=tuple(w for w, _, _ in ws[1:]))

    return Command("sql", stmt)


def _load(stmt: str, ws) -> Command:
    """LOAD table FROM 'path' [HEADER]. The path may also be written
    without quotes when it has no spaces."""
    if len(ws) < 4 or ws[2][0].upper() != "FROM":
        raise ShellError("expected LOAD table FROM 'file' [HEADER]")
    table = ws[1][0]
    tok, start, end = ws[3]
    if tok.startswith("'"):
        path = tok[1:]
    else:                                    # bare path: up to the next blank
        j = start
        while j < len(stmt) and stmt[j] not in " \t\r\n":
            j += 1
        path, end = stmt[start:j], j
    tail = [w for w, _, _ in words(stmt[end:])]
    header = False
    if tail:
        if len(tail) == 1 and tail[0].upper() == "HEADER":
            header = True
        else:
            raise ShellError(f"unexpected {tail[0]!r} after the file name "
                             f"(only HEADER may follow)")
    return Command("load", name=table, value=path, header=header)


def terminated(text: str) -> bool:
    """True when the last thing in the text, outside strings and comments,
    is a ';' (the prompt then runs what it has). An open string is never
    terminated."""
    last, i, n = "", 0, len(text)
    while i < n:
        ch = text[i]
        if ch == "'":
            j = i + 1
            while j < n and text[j] != "'" and text[j] != "\n":
                j += 1
            if j >= n or text[j] == "\n":
                return False
            last, i = "'", j + 1
            continue
        if text.startswith("--", i):
            while i < n and text[i] != "\n":
                i += 1
            continue
        if ch not in " \t\r\n":
            last = ch
        i += 1
    return last == ";"