"""The integrated program (Section 6).

    radb --db DIR [--frames 32]                    interactive prompt
    radb --db DIR [--frames 32] --file script.sql  run a script, then exit
    radb --db DIR [--frames 32] -c "SQL; SQL"      run the given statements

Without installing, `python3 -m shell ...` is the same program.

At the prompt a statement ends with ';' and may span several lines; \\q (or
end of input) quits and \\? lists the commands. An error is printed and the
prompt carries on. In a script the first error stops the run with exit code
1, as `sql exec` does.

Errors keep the format of the module that found them: Module 3's
`error: <category>: <message> at line L, column C`, and `error: <message>`
for the others.
"""
from __future__ import annotations

import argparse
import sys
from typing import List, Optional, TextIO

from .session import DEFAULT_FRAMES, MIN_FRAMES, Session
from .split import terminated


def render(e: Exception) -> str:
    r = getattr(e, "render", None)
    if r is not None:
        return r()
    msg = e.args[0] if isinstance(e, KeyError) and e.args else e
    return f"error: {msg}"


ERRORS = (Exception,)       # every module error is reported, never a traceback


def run_text(session: Session, text: str, err: TextIO) -> int:
    try:
        session.run_script(text)
    except ERRORS as e:
        err.write(render(e) + "\n")
        return 1
    return 0


def _complete(buf: str) -> bool:
    """A backslash command is complete at once; SQL when it ends with ';'."""
    return buf.strip().startswith("\\") or terminated(buf)


def repl(session: Session, inp: TextIO, out: TextIO, err: TextIO) -> int:
    interactive = inp.isatty()
    if interactive:
        try:
            import readline  # noqa: F401  (line editing and history)
        except ImportError:
            pass
        out.write(f"radb: database {session.db}, frames = {session.frames}. "
                  f"\\? for help, \\q to quit.\n")
    buf = ""
    while True:
        prompt = "radb> " if not buf else "  ... "
        try:
            line = input(prompt) if interactive else inp.readline()
        except EOFError:
            line = None
        except KeyboardInterrupt:
            out.write("\n")
            buf = ""
            continue
        if line is None or (not interactive and line == ""):
            if buf.strip():
                run_text(session, buf, err)
            if interactive:
                out.write("\n")
            return 0
        line = line.rstrip("\n")
        if not buf and line.strip() in ("\\q", "quit", "exit"):
            return 0
        buf += line + "\n"
        if _complete(buf):
            run_text(session, buf, err)
            buf = ""


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="radb", description="the integrated database engine")
    p.add_argument("--db", required=True, help="database directory")
    p.add_argument("--frames", type=int, default=DEFAULT_FRAMES,
                   help=f"memory budget B (default {DEFAULT_FRAMES})")
    p.add_argument("--file", help="run this SQL script and exit")
    p.add_argument("-c", dest="command", help="run these statements and exit")
    return p


def main(argv: Optional[List[str]] = None, inp: TextIO = sys.stdin, out: TextIO = sys.stdout,
         err: TextIO = sys.stderr) -> int:
    args = build_parser().parse_args(argv)
    if args.frames < MIN_FRAMES:
        err.write(f"error: shell: --frames must be at least {MIN_FRAMES}\n")
        return 1
    session = Session(args.db, args.frames, out)
    if args.command is not None:
        return run_text(session, args.command, err)
    if args.file is not None:
        try:
            with open(args.file, encoding="utf-8") as f:
                text = f.read()
        except OSError as e:
            err.write(f"error: io: cannot read '{args.file}': {e.strerror}\n")
            return 1
        return run_text(session, text, err)
    return repl(session, inp, out, err)


if __name__ == "__main__":
    sys.exit(main())