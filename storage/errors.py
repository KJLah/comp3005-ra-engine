"""Errors raised by the storage layer. The CLI catches StorageError and
prints its message; the library itself never prints."""


class StorageError(Exception):
    """Base class for every Module 1 error."""


class RecordError(StorageError):
    """A value does not fit the schema (bad INT, VARCHAR too long, record too big)."""


class PageError(StorageError):
    """Bad slot number, dead slot, or a page that is full."""
