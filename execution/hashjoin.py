"""Grace hash join (Module 2, Section 2.9). Equality conditions only; the
left input is the build side.

    partition  P = B - 1 partitions (1 frame each) + 1 input frame.
               Scan R, write each tuple to partition h1(key) mod P; same for S.
    probe      for each i: build a table on R_i (h2), scan S_i, emit matches.

    cost = 2(M + N) partitioning + (M + N) probing = 3(M + N)

B is the frames free when the join's blocking work starts. While probing, the
join holds S = min(B, limit) frames: a table of S - 1 pages and 1 frame to read
S_i (or R_i while building).

Partition overflow (decision 4, DESIGN_M2.md). If R_i does not fit in S - 1
pages, R_i and S_i are partitioned again with the next hash seed into S - 1
sub-partitions, up to MAX_DEPTH levels in all. A sub-partition that did not
shrink at all (every tuple went to the same place, which is what one very
common key does: every copy of a key always hashes alike) is not partitioned
again: it goes straight to the fallback. The fallback is a block nested loop
join on that one pair of partitions: K = S - 2 pages of R_i at a time, S_i
streamed past each block, every pair compared.
"""
from __future__ import annotations

from typing import Iterator, List, Optional, Tuple

from .base import Operator, register, rows_of
from .conditions import equi_keys
from .context import TempFile
from .errors import BudgetError
from .hashing import MAX_DEPTH, PAGE_BYTES, BucketTable, Partitioner
from .schema import Row, Schema, encode

SLOT = 4


@register("hash_join")
class HashJoin(Operator):
    stream_need = 3

    def _bind(self) -> Schema:
        left, right = self.children[0].schema, self.children[1].schema
        where = f"{self.path} (hash_join)"
        out = left.concat(right, where)
        self.lk, self.rk = equi_keys(self.node["condition"], left, right, where)
        return out

    def label(self) -> str:
        if not hasattr(self, "events"):
            return "hash_join"
        rp = sum(1 for e in self.events if e[0] == "repartition")
        fb = sum(1 for e in self.events if e[0] == "fallback")
        return f"hash_join P={self.P} repartitions={rp} fallbacks={fb}"

    def lkey(self, row: Row) -> Tuple:
        return tuple(row[i] for i in self.lk)

    def rkey(self, row: Row) -> Tuple:
        return tuple(row[j] for j in self.rk)

    def _open(self, limit: int) -> None:
        left, right = self.children
        self.events: List[tuple] = []          # for the report: what overflow did
        left.open(1)
        self.P = self.ctx.free()               # B - 1: the child holds the input frame
        if self.P < 2:
            raise BudgetError(f"{self.who}: needs at least 3 frames to partition, "
                              f"has {self.P + 1}")
        self.hold(self.P)
        lp = Partitioner(self, left.schema, self.lkey, self.P, 0, "L")
        for r in rows_of(left):
            lp.add(r)
        left.close()
        lfiles = lp.close()

        # Left partition writers are closed. Let a blocking right
        # subtree use these frames during its open().
        self.unhold(self.P)
        right.open(1)
        self.hold(self.P)

        rp = Partitioner(
            self, right.schema, self.rkey, self.P, 0, "R"
        )
        for s in rows_of(right):
            rp.add(s)
        right.close()
        rfiles = rp.close()
        self.partition_pages = [(a.pages, b.pages) for a, b in zip(lfiles, rfiles)]
        B = self.P + 1
        self.S = min(B, limit)
        self.unhold(self.held)
        self.hold(self.S)
        self.it = self._probe([(a, b, 1) for a, b in zip(lfiles, rfiles)])

    # ---- probe phase -----------------------------------------------------------------
    def _probe(self, pending: List[Tuple[TempFile, TempFile, int]]) -> Iterator[Row]:
        S = self.S
        lschema = self.children[0].schema
        while pending:
            rf, sf, depth = pending.pop(0)
            if rf.pages == 0 or sf.pages == 0:      # nothing can match
                self.drop_temp(rf)
                self.drop_temp(sf)
                continue
            table = BucketTable(S - 1, self.count)
            fits = True
            for r in rf.rows():
                if not table.add(self.lkey(r), r, len(encode(lschema, r))):
                    fits = False
                    break
            if fits:
                for s in sf.rows():
                    for e in table.matches(self.rkey(s)):
                        yield e[1] + s
                self.drop_temp(rf)
                self.drop_temp(sf)
                continue
            table.clear()
            if depth < MAX_DEPTH:
                subs = self._repartition(rf, sf, depth)
                pending[0:0] = subs
            else:
                self.events.append(("fallback", depth, rf.pages, sf.pages))
                yield from self._block_nested_loop(rf, sf)
                self.drop_temp(rf)
                self.drop_temp(sf)

    def _repartition(self, rf: TempFile, sf: TempFile, depth: int):
        parts = self.S - 1
        lp = Partitioner(self, rf.schema, self.lkey, parts, depth, "L")
        for r in rf.rows():
            lp.add(r)
        lfiles = lp.close()
        rp = Partitioner(self, sf.schema, self.rkey, parts, depth, "R")
        for s in sf.rows():
            rp.add(s)
        rfiles = rp.close()
        before = rf.pages
        self.drop_temp(rf)
        self.drop_temp(sf)
        self.events.append(("repartition", depth, before,
                            [(a.pages, b.pages) for a, b in zip(lfiles, rfiles)]))
        out = []
        for a, b in zip(lfiles, rfiles):
            # a sub-partition that did not shrink will not shrink with another
            # seed either (one key): send it straight to the fallback
            nxt = MAX_DEPTH if a.pages >= before else depth + 1
            out.append((a, b, nxt))
        return out

    def _block_nested_loop(self, rf: TempFile, sf: TempFile) -> Iterator[Row]:
        """Fallback on one partition pair: K = S - 2 pages of R_i per block
        (1 frame reads R_i, 1 reads S_i)."""
        cap = (self.S - 2) * PAGE_BYTES
        lschema = rf.schema
        rows = rf.rows()
        carry = None
        while True:
            block, used = [], 0
            nxt = carry if carry is not None else next(rows, None)
            carry = None
            while nxt is not None:
                size = len(encode(lschema, nxt)) + SLOT
                if used + size > cap and block:
                    carry = nxt
                    break
                block.append((self.lkey(nxt), nxt))
                used += size
                nxt = next(rows, None)
            if not block:
                return
            for s in sf.rows():
                k = self.rkey(s)
                for key, r in block:
                    self.c.comparisons += 1
                    if key == k:
                        yield r + s
            if carry is None:
                return

    def _next(self) -> Optional[Row]:
        return next(self.it, None)

    def _close(self) -> None:
        it = getattr(self, "it", None)
        if it is not None:
            it.close()