"""Errors raised by the storage layer. The CLI catches StorageError and
prints its message; the library itself never prints."""


class StorageError(Exception):
    """Base class for every Module 1 error."""


class RecordError(StorageError):
    """A value does not fit the schema (bad INT, VARCHAR too long, record too big)."""


class PageError(StorageError):
    """Bad slot number, dead slot, or a page that is full."""


class BufferPoolError(StorageError):
    """Buffer-pool misuse: no unpinned frame, unpin at pin count 0, page not resident."""


class CatalogError(StorageError):
    """Unknown table, duplicate table, or a bad table name."""


class LoadError(StorageError):
    """A CSV line that cannot be loaded. The message names the line and column."""
