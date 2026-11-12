"""Importing this module registers every operator in base.OPERATORS."""
from . import scan  # noqa: F401  seqscan, filter, project, limit, union_all
from . import sort  # noqa: F401  sort
from . import aggregate  # noqa: F401  distinct, group_by
from . import setops  # noqa: F401  union, intersect, except
from . import nlj  # noqa: F401  nested_loop_join
from . import smj  # noqa: F401  sort_merge_join
from . import hashjoin  # noqa: F401  hash_join
