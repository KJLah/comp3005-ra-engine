"""Cost model (Module 4, Section 4). DESIGN_M4.md section 5.

Cost is page I/O, reads plus writes. M and N are the estimated pages of the
inputs, B the frame budget (--frames). The Section 4 formulas:

    seqscan            M
    filter, project    0
    sort               2M(passes - 1) + M,  passes = 1 + ceil(log(ceil(M/B)) / log(B-1))
    distinct/group_by  sort mode: the sort; hash mode: 0 if the input fits in
                       B - 1 frames, else 2M (partitions written and read back)
    nested_loop_join   M + ceil(M/(B-2)) x N
    sort_merge_join    sort(M) + sort(N) + M + N
    hash_join          3(M + N)
    limit, rename      0
    union etc.         sorted like SMJ inputs: 2M(passes-1) + 2N(passes-1)

How a formula becomes the cost of one node (explain's est_cost). Every
formula above counts reading its inputs once (the +M of sort, the M of BNL,
the 3 of hash includes the first read). In our executor an input that is a
pipeline over a base table (seqscan, under filters, projects and renames) is
read by its own seqscan node, which already costs its table's pages, so that
read is not counted a second time on the parent. Any other input (another
join, a sort, a group_by) is charged once to the parent: this is the "+20"
for the intermediate result in Section 5.1. For BNL the inner is rescanned
ceil(M/(B-2)) times; a pipelined inner rescans its whole base table, and its
first scan is already its own seqscan's cost.

The total cost of a plan is the sum over its nodes, so the 5.1 numbers come
out exactly: {A,B'} = 10 + 1x100 = 110, {A,B',C} = 110 + 20 + 1x400 = 530.
"""
from __future__ import annotations

import math
from typing import Dict, List, Tuple

from .estimate import Estimator

PIPELINED = ("filter", "project", "rename")


def passes(M: float, B: int) -> int:
    if M <= B or B < 3:
        return 1
    return 1 + math.ceil(math.log(math.ceil(M / B)) / math.log(B - 1) - 1e-12)


def sort_cost(M: float, B: int) -> float:
    return 2 * M * (passes(M, B) - 1) + M


def blocks(M: float, B: int) -> int:
    return max(1, math.ceil(M / max(1, B - 2)))


def is_base(n: dict) -> bool:
    """A pipeline over one base table: read by its own seqscan."""
    if n["op"] == "seqscan":
        return True
    return n["op"] in PIPELINED and is_base(n["child"])


def join_terms(algo: str, M: float, N: float, B: int, outer_base: bool,
               inner_base: bool, inner_scan: float) -> List[Tuple[str, float]]:
    """The join's own cost as labelled terms (inputs' own costs excluded).
    inner_scan is what one rescan of the inner reads: its base table's pages
    when it is a pipeline, else its estimated pages."""
    t: List[Tuple[str, float]] = []
    if algo == "nested_loop_join":
        if not outer_base:
            t.append(("read outer", M))
        k = blocks(M, B)
        rescans = k - 1 if inner_base else k
        if rescans:
            t.append((f"{rescans}x{inner_scan:g} rescans", rescans * inner_scan))
        return t
    if algo == "hash_join":
        t.append(("partition", 2 * (M + N)))
    else:
        t.append(("sorts", 2 * M * (passes(M, B) - 1) + 2 * N * (passes(N, B) - 1)))
        t.append(("merge", M + N))
    if not outer_base:
        t.append(("read outer", M))
    if not inner_base:
        t.append(("read inner", N))
    return t


class CostModel:
    def __init__(self, est: Estimator, frames: int):
        self.est = est
        self.B = frames

    def pages(self, n: dict) -> float:
        return self.est.props(n).pages

    def scan_pages(self, n: dict) -> float:
        """Pages one rescan of n reads."""
        if is_base(n):
            while n["op"] != "seqscan":
                n = n["child"]
            return self.pages(n)
        return self.pages(n)

    def charge(self, child: dict) -> float:
        return 0 if is_base(child) else self.pages(child)

    def own(self, n: dict) -> float:
        op, B = n["op"], self.B
        if op == "seqscan":
            return self.pages(n)
        if op in PIPELINED or op in ("limit", "union_all"):
            return 0
        if op in ("nested_loop_join", "sort_merge_join", "hash_join"):
            l, r = n["left"], n["right"]
            return sum(v for _, v in join_terms(op, self.pages(l), self.pages(r), B,
                                                is_base(l), is_base(r), self.scan_pages(r)))
        if op == "sort" or (op in ("group_by", "distinct") and n["mode"] == "sort"):
            M = self.pages(n["child"])
            return 2 * M * (passes(M, B) - 1) + self.charge(n["child"])
        if op in ("group_by", "distinct"):
            M = self.pages(n["child"])
            return (0 if M <= B - 1 else 2 * M) + self.charge(n["child"])
        if op in ("union", "intersect", "except"):
            l, r = n["left"], n["right"]
            M, N = self.pages(l), self.pages(r)
            return (2 * M * (passes(M, B) - 1) + 2 * N * (passes(N, B) - 1)
                    + self.charge(l) + self.charge(r))
        raise ValueError(f"no cost formula for {op!r}")

    def total(self, n: dict) -> float:
        return self.own(n) + sum(self.total(n[k]) for k in ("child", "left", "right") if k in n)

    def best_mode(self, n: dict) -> str:
        """group_by / distinct: hash when it is strictly cheaper, else sort."""
        M = self.pages(n["child"])
        hash_c = 0 if M <= self.B - 1 else 2 * M
        sort_c = 2 * M * (passes(M, self.B) - 1)
        return "hash" if hash_c < sort_c else "sort"