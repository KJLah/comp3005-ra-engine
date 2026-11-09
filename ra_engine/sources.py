"""Tuple sources supplied by callers; no file or console access."""
from typing import Iterable, Optional, Protocol, Sequence

from .relation import Attribute, Row


class TupleSource(Protocol):
    name: Optional[str]
    attributes: Sequence[Attribute]

    def scan(self) -> Iterable[Row]:
        """Return a fresh iterable for every execution."""
        ...