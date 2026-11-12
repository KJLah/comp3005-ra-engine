"""Errors raised by the execution engine. The CLI catches ExecError and
prints its message; the library itself never prints."""


class ExecError(Exception):
    """Base class for every Module 2 error."""


class PlanError(ExecError):
    """The plan JSON does not conform (Section 4). The message names the node."""


class BindError(ExecError):
    """Unknown table or column, duplicate column, type mismatch: found at
    open, before any tuple is read (Sections 1.3 and 4)."""


class BudgetError(ExecError):
    """An operator asked for more frames than the plan has left (Section 1.2)."""
