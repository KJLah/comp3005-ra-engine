"""distinct and group_by, sort-based and hash-based (Module 2, Sections 2.4, 2.6).

Both are the same machine: a group key, a per-group state, and a way to fold
a tuple into a state. distinct is a group_by whose key is the whole tuple and
whose state is empty.

Sort mode sorts the input on the key with the external sort, then folds
adjacent tuples with equal keys (one comparison per adjacent pair).

Hash mode (decision 4 of the plan, DESIGN_M2.md):
  phase 0   fold tuples into a BucketTable of at most min(F-1, S) pages.
            If the whole input fits, stream the table: one pass, no I/O.
  overflow  write the table out (it is already partially aggregated) to a
            spill file through one frame, then hash-partition the rest of
            the input and the spill into P = F-1 partitions. A partition
            holds state records (key + partial state), so a partition can
            mix raw tuples and partial results and still be folded.
  per partition, while streaming
            if it fits in S-1 pages (1 frame to read it), fold it into a
            table and emit; otherwise partition it again with the next hash
            seed; after MAX_DEPTH levels, sort that one partition and fold
            adjacent keys instead.
F is the frames free when the operator's blocking work starts; S is what it
may keep while streaming (min(F, limit)).
"""
from __future__ import annotations

from typing import Iterator, List, Optional, Sequence, Tuple

from storage.record import INT, VARCHAR

from .base import Operator, register, rows_of
from .context import TempFile
from .errors import BindError, BudgetError, ExecError
from .hashing import MAX_DEPTH, BucketTable, Partitioner
from .schema import BIGINT, REAL, Col, KeyComparator, Row, Schema, encode
from .sort import ExternalSorter, open_child_for_blocking, sort_child


# ---- the group-and-fold specification ------------------------------------------
class Agg:
    """One aggregate: its function, input column (None for *), output column,
    and the columns its partial state takes in a state record."""

    def __init__(self, fn: str, idx: Optional[int], col: Optional[Col]):
        self.fn, self.idx, self.col = fn, idx, col
        if fn == "count":
            self.out = Col("count_star" if col is None else f"count_{col.name.replace('.', '_')}", INT)
            self.state = [Col(f"_{self.out.name}", BIGINT)]
        else:
            name = f"{fn}_{col.name.replace('.', '_')}"
            if fn == "sum":
                self.out = Col(name, BIGINT)
                self.state = [Col(f"_{name}", BIGINT)]
            elif fn == "avg":
                self.out = Col(name, REAL)
                self.state = [Col(f"_{name}_s", BIGINT), Col(f"_{name}_n", BIGINT)]
            else:                               # min, max
                self.out = Col(name, col.type, col.length)
                self.state = [Col(f"_{name}", col.type, col.length)]

    def init(self, row: Row) -> list:
        if self.fn == "count":
            return [1]
        v = row[self.idx]
        return [v, 1] if self.fn == "avg" else [v]

    def combine(self, st: list, pos: int, other: Sequence) -> None:
        """Fold another partial state (other[pos:...]) into st[pos:...]."""
        if self.fn in ("count", "sum"):
            st[pos] += other[pos]
        elif self.fn == "avg":
            st[pos] += other[pos]
            st[pos + 1] += other[pos + 1]
        elif self.fn == "min":
            if other[pos] < st[pos]:
                st[pos] = other[pos]
        else:
            if other[pos] > st[pos]:
                st[pos] = other[pos]

    def final(self, st: Sequence, pos: int):
        if self.fn == "avg":
            return st[pos] / st[pos + 1]
        return st[pos]


class Spec:
    def __init__(self, child: Schema, key_idx: List[int], aggs: List[Agg], where: str):
        self.key_idx = key_idx
        self.k = len(key_idx)
        self.aggs = aggs
        key_cols = [child.cols[i] for i in key_idx]
        self.state_schema = Schema(key_cols + [c for a in aggs for c in a.state], where)
        self.out_schema = Schema(key_cols + [a.out for a in aggs], where)
        self.offsets = []
        pos = 0
        for a in aggs:
            self.offsets.append(pos)
            pos += len(a.state)

    def record(self, row: Row) -> Row:
        """A raw input tuple as a state record: key, then initial states."""
        st: list = []
        for a in self.aggs:
            st.extend(a.init(row))
        return tuple(row[i] for i in self.key_idx) + tuple(st)

    def fold(self, st: list, rec: Sequence) -> None:
        other = rec[self.k:]
        for a, pos in zip(self.aggs, self.offsets):
            a.combine(st, pos, other)

    def final(self, key: Tuple, st: Sequence) -> Row:
        return tuple(key) + tuple(a.final(st, pos) for a, pos in zip(self.aggs, self.offsets))

    def size(self, rec: Row) -> int:
        return len(encode(self.state_schema, rec))


# ---- the shared operator ---------------------------------------------------------
class _Grouping(Operator):
    """Base of distinct and group_by. Subclasses build self.spec in _bind."""

    def label(self) -> str:
        extra = ""
        if getattr(self, "partitions_made", 0):
            extra = f" partitions={self.partitions_made} max_depth={self.max_depth}"
        return f"{self.op} {self.node['mode']}{extra}"

    def _setup_stream_need(self) -> None:
        # hash mode partitions while streaming: 1 frame to read + P >= 2
        self.stream_need = 3 if self.node["mode"] == "hash" and self.spec.k else 1

    def _open(self, limit: int) -> None:
        self.partitions_made = 0
        self.max_depth = 0
        child = self.children[0]
        if self.spec.k == 0:
            # no grouping columns: one running aggregate, nothing to sort or hash
            child.open(limit)
            self.it = self._single_group(rows_of(child))
        elif self.node["mode"] == "sort":
            keys = [(i, True) for i in self.spec.key_idx]
            self.sorter = sort_child(self, child, keys, limit)
            self.it = self._fold_sorted(self.spec.record(r) for r in self.sorter.stream())
        else:
            self.it = self._hash_open(limit)

    def _next(self) -> Optional[Row]:
        return next(self.it, None)

    def _close(self) -> None:
        it = getattr(self, "it", None)
        if it is not None:
            it.close()

    # ---- no keys -------------------------------------------------------------------
    def _single_group(self, rows) -> Iterator[Row]:
        st = None
        for r in rows:
            rec = self.spec.record(r)
            if st is None:
                st = list(rec)          # no key: the record is all state
            else:
                self.spec.fold(st, rec)
        if st is None:
            # §2.6: empty input, no grouping columns -> exactly one row, COUNT 0;
            # any other aggregate is undefined, which is an error if requested.
            bad = [a.out.name for a in self.spec.aggs if a.fn != "count"]
            if bad:
                raise ExecError(f"{self.who}: {', '.join(bad)} of an empty input is "
                                f"undefined (§2.6)")
            yield tuple(0 for _ in self.spec.aggs)
            return
        yield self.spec.final((), st)

    # ---- sort mode -----------------------------------------------------------------
    def _fold_sorted(self, recs) -> Iterator[Row]:
        """Fold runs of equal keys in key-sorted state records; one comparison
        per adjacent pair."""
        k = self.spec.k
        cmp = KeyComparator([(i, True) for i in range(k)], self.count)
        prev = None             # the previous record (for its key)
        st = None               # the running state of its group
        for rec in recs:
            if prev is not None and cmp(prev, rec) == 0:
                self.spec.fold(st, rec)
                continue
            if prev is not None:
                yield self.spec.final(prev[:k], st)
            prev, st = rec, list(rec[k:])
        if prev is not None:
            yield self.spec.final(prev[:k], st)

    # ---- hash mode -----------------------------------------------------------------
    def _hash_open(self, limit: int) -> Iterator[Row]:
        spec, k = self.spec, self.spec.k
        child = self.children[0]
        open_child_for_blocking(self, child)
        F = self.ctx.free()
        if F < 3:
            raise BudgetError(f"{self.who}: hash mode needs at least 3 frames, has {F}")
        self.hold(F)
        S = min(F, limit)
        table = BucketTable(min(F - 1, S), self.count)
        overflow_rec = None
        rows = rows_of(child)
        for r in rows:
            rec = spec.record(r)
            e = table.find(rec[:k])
            if e is not None:
                spec.fold(e[1], rec)
                continue
            if not table.add(rec[:k], list(rec[k:]), spec.size(rec)):
                overflow_rec = rec
                break

        if overflow_rec is None:
            child.close()
            self.unhold(F - max(1, table.pages_used()))
            return self._emit_table(table)

        # overflow: spill the partial table through one frame ...
        spill = self.new_temp("spill", spec.state_schema)
        w = spill.writer()
        for key, st in table.items():
            w.add(tuple(key) + tuple(st))
        w.close()
        table.clear()
        # ... then partition the rest of the input and the spill, P = F - 1
        part = Partitioner(self, spec.state_schema, lambda rec: rec[:k], F - 1, 0, "part")
        part.add(overflow_rec)
        for r in rows:
            part.add(spec.record(r))
        child.close()
        for rec in spill.rows():
            part.add(rec)
        self.drop_temp(spill)
        files = part.close()
        self.partitions_made += len(files)
        self.max_depth = 1
        self.unhold(F - S)
        return self._emit_partitions([(f, 1) for f in files], S)

    def _emit_table(self, table: BucketTable) -> Iterator[Row]:
        for key, st in table.items():
            yield self.spec.final(key, st)

    def _try_fold(self, f: TempFile, pages: int) -> Optional[BucketTable]:
        """Fold a partition into a table of `pages` pages, reading it with one
        frame. None if the groups do not fit (the partial table is dropped and
        the partition is partitioned again). A partition of many pages can
        still fit when it holds few distinct keys, so this is tried first."""
        spec, k = self.spec, self.spec.k
        table = BucketTable(pages, self.count)
        for rec in f.rows():
            e = table.find(rec[:k])
            if e is not None:
                spec.fold(e[1], rec)
            elif not table.add(rec[:k], list(rec[k:]), spec.size(rec)):
                self.overflows = getattr(self, "overflows", 0) + 1
                return None
        return table

    def _emit_partitions(self, pending: List[Tuple[TempFile, int]], S: int) -> Iterator[Row]:
        spec, k = self.spec, self.spec.k
        while pending:
            f, depth = pending.pop(0)
            if f.pages == 0:
                self.drop_temp(f)
                continue
            table = self._try_fold(f, S - 1)
            if table is not None:
                self.drop_temp(f)
                yield from self._emit_table(table)
            elif depth < MAX_DEPTH:
                part = Partitioner(self, spec.state_schema, lambda rec: rec[:k],
                                   S - 1, depth, "part")
                for rec in f.rows():
                    part.add(rec)
                self.drop_temp(f)
                subs = part.close()
                self.partitions_made += len(subs)
                self.max_depth = max(self.max_depth, depth + 1)
                pending[0:0] = [(g, depth + 1) for g in subs]
            else:
                # fallback after MAX_DEPTH levels: sort this partition (1 frame
                # reads it, the sort gets the other S - 1) and fold adjacent keys
                self.fallbacks = getattr(self, "fallbacks", 0) + 1
                cmp = KeyComparator([(i, True) for i in range(k)], self.count)
                sorter = ExternalSorter(self, spec.state_schema, cmp, S - 1, "fallback")
                sorter.build_runs(f.rows(), keep_limit=S - 1)
                self.drop_temp(f)
                sorter.F = S              # the reading frame is free again
                sorter.merge_until(S - 1)
                yield from self._fold_sorted(sorter.stream())
                for t in sorter.runs:
                    self.drop_temp(t)


@register("distinct")
class Distinct(_Grouping):
    """Duplicate removal: a grouping on every column with no aggregates."""

    def _bind(self) -> Schema:
        child = self.children[0].schema
        self.spec = Spec(child, list(range(len(child))), [], f"{self.path} (distinct)")
        self._setup_stream_need()
        return child


@register("group_by")
class GroupBy(_Grouping):
    def _bind(self) -> Schema:
        child = self.children[0].schema
        where = f"{self.path} (group_by)"
        key_idx = [child.index(name, where) for name in self.node["keys"]]
        aggs = []
        for fn, col in self.node["aggregates"]:
            fn = fn.lower()
            if col == "*":
                aggs.append(Agg("count", None, None))
                continue
            i = child.index(col, where)
            c = child.cols[i]
            if fn in ("sum", "avg") and c.type not in (INT, BIGINT):
                raise BindError(f"{where}: {fn.upper()} requires an INT column, "
                                f"{c.name} is {c.type}")
            aggs.append(Agg(fn, i, c))
        self.spec = Spec(child, key_idx, aggs, where)
        self._setup_stream_need()
        return self.spec.out_schema
