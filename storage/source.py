"""A stored table as a TupleSource, so the relational algebra engine can query it.

    cat = Catalog("db", BufferPool(8))
    op = plan(parse_query("project[R.b](R)"), sources(cat))
    evaluate(op).rows

The engine never sees pages, frames or files; it only calls scan(). Every
call to scan() starts a new heap scan through the catalog's buffer pool, so
the scan pins at most 2 pages and unpins them when it ends (DESIGN_M1.md
section 11).
"""
from __future__ import annotations

from typing import Dict, Iterator, List

from ra_engine.relation import NUM, STR, Attribute, Row

from .catalog import Catalog
from .record import INT, decode


class HeapSource:
    """One table of the catalog, seen as a TupleSource (ra_engine/sources.py)."""

    def __init__(self, catalog: Catalog, table: str):
        info = catalog.get(table)                    # CatalogError if unknown
        self.catalog = catalog
        self.name = info.name
        self.columns = info.columns
        self.attributes: List[Attribute] = [
            Attribute(info.name, c.name, NUM if c.type == INT else STR)
            for c in info.columns]

    def scan(self) -> Iterator[Row]:
        for _, rec in self.catalog.open_heap(self.name).scan():
            yield tuple(decode(self.columns, rec))


def sources(catalog: Catalog) -> Dict[str, HeapSource]:
    """Every table of the catalog, keyed by name: the engine's catalog dict."""
    return {t.name: HeapSource(catalog, t.name) for t in catalog.tables()}