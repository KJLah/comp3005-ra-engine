"""Loads relation definition files into an in-memory catalog.

Parsing is done by the same tokenizer and parser used for queries (see
GRAMMAR.md 5.1.5). This module makes the semantic checks that the grammar
cannot express.
"""
from __future__ import annotations

from typing import Dict, Optional

from .errors import RAError, RANameError, RATypeError, SchemaError
from .parser import parse_definitions
from .relation import Attribute, Relation, distinct, value_type

Catalog = Dict[str, Relation]


def load_text(text: str, catalog: Optional[Catalog] = None,
              source_name: str = "definitions") -> Catalog:
    catalog = {} if catalog is None else catalog
    try:
        for d in parse_definitions(text):
            if d.name in catalog:
                raise RANameError(f"relation '{d.name}' is defined twice", d.pos)

            # attribute names must be distinct
            seen = {}
            for name, pos in zip(d.attrs, d.attr_positions):
                if name in seen:
                    raise SchemaError(f"attribute '{name}' appears twice in the "
                                      f"header of {d.name}", pos)
                seen[name] = pos

            # every tuple has one value per attribute; each column has one type
            types = [None] * len(d.attrs)
            for row, pos in zip(d.rows, d.row_positions):
                if len(row) != len(d.attrs):
                    raise SchemaError(f"this tuple has {len(row)} values but {d.name} "
                                      f"has {len(d.attrs)} attributes", pos)
                for i, v in enumerate(row):
                    t = value_type(v)
                    if types[i] is None:
                        types[i] = t
                    elif types[i] != t:
                        raise RATypeError(
                            f"column '{d.attrs[i]}' of {d.name} mixes numbers and "
                            f"strings (value {v!r} is a {t}, earlier values are "
                            f"{types[i]}s); quote numbers that should be strings", pos)

            attributes = [Attribute(d.name, n, t) for n, t in zip(d.attrs, types)]
            rows = distinct(tuple(r) for r in d.rows)  # a relation is a set
            catalog[d.name] = Relation(d.name, attributes, rows)
    except RAError as e:
        raise e.attach_source(text, source_name)
    return catalog


def load_file(path: str, catalog: Optional[Catalog] = None) -> Catalog:
    try:
        with open(path, encoding="utf-8") as f:
            text = f.read()
    except OSError as e:
        raise RANameError(f"cannot read data file '{path}': {e.strerror}")
    return load_text(text, catalog, path)
