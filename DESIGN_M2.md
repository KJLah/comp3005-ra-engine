# Module 2 design: execution engine

This document records the design of the execution engine (`execution/`) and
every choice the Module 2 specification leaves open. Section numbers in
brackets refer to the Module 2 document.


---

## 1. Layout of the code ✅

| File | Spec | Contents |
|---|---|---|
| `execution/errors.py` | §4 | `ExecError`, `PlanError`, `BindError`, `BudgetError` |
| `execution/schema.py` | §1.3 | `Col`, `Schema`, tuple encoding (with REAL and BIGINT), comparators, `stable_hash` |
| `execution/conditions.py` | §1.3 | filter and join conditions, reusing the Bonus 1 parser and compiler |
| `execution/context.py` | §1.2, §1.4 | `ExecContext` (pool of B frames, budget), `OwnedDisk`, `TempFile`, `RunWriter` |
| `execution/base.py` | §1.1 | `Operator` base class, materialising, `register`, `rows_of` |
| `execution/plan.py` | §4 | plan validation, building and binding the tree |
| `execution/scan.py` | §2.1–2.3, §2.10 | seqscan, filter, project, limit, union_all |
| `execution/sort.py` | §2.5 | `ExternalSorter`, `sort_child`, sort |
| `execution/hashing.py` | §2.4, §2.6, §2.9 | `BucketTable` (budgeted hash table), `Partitioner` |
| `execution/aggregate.py` | §2.4, §2.6 | distinct, group_by (sort and hash modes) |
| `execution/setops.py` | §2.10 | union, intersect, except |
| `execution/nlj.py` | §2.7 | nested_loop_join |
| `execution/smj.py` | §2.8 | sort_merge_join |
| `execution/hashjoin.py` | §2.9 | hash_join |
| `execution/cli.py` | §4 | `exec run`, `exec trace` |
| `plans/*.json` | §4 | one sample plan per operator, run by the checklist tests |
| `measure_m2.py` | §5 | every measurement in REPORT_M2.md |

The base class lives in `base.py`, not `operator.py`: a module called
`operator.py` shadows Python's standard `operator` module whenever a script
is run from inside the package directory (this happened while building
step 1).

## 2. The iterator interface [§1.1] ✅

`open(limit)`, `next()` (returns `None` for END), `close()`, `counters()`.
Before `open` there is `bind()`, which builds every output schema bottom-up
and does all name and type checks. It reads the catalog but no table data,
so an unknown table or column is rejected before any work happens [§4].

`close()` is idempotent, closes the children, deletes the operator's
temporary files, and releases its frames. `reset()` (close, then openable
again) is what lets a block nested loop join rescan a pipelined inner input.

## 3. Memory budget [§1.2] ✅

**Working memory is counted in frames, and the pool enforces B.** A plan
runs on one `BufferPool` with exactly B frames. Any tuples an operator
keeps are either in pinned frames as encoded records (a sort run being
filled, a block of the outer input, a partition being written, a temp page
being read), or in a structure charged by size against frames the operator
has reserved: a hash table charges each entry its encoded size plus a
4-byte slot and refuses entries beyond its pages (§11), and an equal-key
run in sort-merge join is capped at one page (§15). Decoded Python copies
only exist for pages held at that moment. Pinning more than B pages is
impossible, not just forbidden: the pool raises "no unpinned frame
available". The one place the language's sort runs is on a single run of
at most F pages, which §2.5 allows.

**The catalog has its own 2-frame pool.** Table names and columns are
looked up at bind, before the plan runs, through Module 1's catalog, whose
heap scan pins 2 pages. That lookup uses a separate `BufferPool(2)`, so
metadata never takes frames from the plan's B (and a single seqscan runs at
B = 1). The first test run caught this: with the catalog on the plan's pool,
`peak_pinned` was 2 for a plain seqscan and B = 1 failed at bind.

**Bookkeeping.** An operator reserves frames in the plan's budget
(`hold(n)`); asking for more than is free raises `BudgetError` naming every
holder. `BufferPool.peak_pinned` records the most frames ever pinned at once:
that is how we know the largest number of frames held [§5, "The budget"].

**Allocation rule.** `open(limit)`: `limit` is the most frames the subtree
may still hold once `open` returns and tuples start streaming. While `open`
runs, the subtree may also use, temporarily, every frame that is free in
the budget at that moment, which is when blocking work happens. A blocking
operator therefore gets every frame nobody else holds, gives the extra back
when `open` ends, and keeps at most `limit`. Open order is fixed by the
plan (children before the work of their parent), so the same plan at the
same B always gets the same shares and the same counters.

Pipelined operators count the frames they actually pin: seqscan 1; filter,
project and limit 0 (they never pin a page; §3's "1" is an upper bound).

## 4. Counters [§1.4] ✅

Module 1's pool counts I/O for the whole pool. Every file an operator
touches is opened through its own `OwnedDisk`, a `DiskManager` that also adds
each `read_page` / `write_page` call to that operator's counters: a seqscan
has its own handle on its table file, a sort owns its run files. A write
caused by evicting a dirty page of a sort's run is charged to the sort,
whoever caused the eviction; catalog reads are charged to nobody.

## 5. Temporary files ✅

A temporary file is a sequence of slotted pages with no directory page (it
is only read front to back or by page number). Files live in `DB/_tmp/` and
are deleted at `close`; `ExecContext.leftover_temps()` checks that none
remain [§6].

Temporary pages are **written through and never cached**: a page is written
the moment it is full and dropped from the pool; a page that was read is
dropped when it is unpinned. Otherwise a page still in the pool would give a
hit instead of a read, and a file deleted before its pages were evicted
would never be counted as written, so measured I/O would drift below the
formulas for reasons unrelated to the algorithm. This needed three small
Module 1 additions: `BufferPool.flush_page`, `discard_page` and `discard`
(drop every page of a file before deleting it, so no later eviction
re-creates it).

## 6. Tuples, schemas, comparisons [§1.3] ✅

- Column names are qualified (`R.a`); aggregate outputs (`count_star`) have
  no qualifier. Duplicate names in one schema are a `BindError` at bind.
- Types: INT and VARCHAR(n) from Module 1, plus **REAL**, which only AVG
  produces. It is encoded as an 8-byte double and only ever appears in
  temporary files, so a group_by can feed a sort.
- INT and REAL both count as numbers; INT against VARCHAR is an error for
  every operator, including equality.
- **VARCHAR byte order = Python string order.** UTF-8 preserves code point
  order, so `<` on `str` is byte-wise on UTF-8. Tested with non-ASCII strings.
- A multi-column sort or join key is **one comparison** per pair of tuples,
  the same way a compound condition is one evaluation in filter [§2.2].
- **Hashing is our own (FNV-1a with a seed).** Python's `hash()` of a string
  changes between runs (PYTHONHASHSEED), which would break "same plan, same
  B, same counters". Tested by hashing in a second process with another seed.

## 7. Plan JSON [§4] ✅

Field names are exactly those of §2. §2 shows no JSON for limit or the set
operators, so these shapes are ours:

    { "op": "limit", "n": 10, "child": { ... } }
    { "op": "union_all" | "union" | "intersect" | "except", "left": { ... }, "right": { ... } }

Every error names the node by its path from the root and its op, e.g.
`plan.right (filter): missing field "condition"`. Unknown fields are
rejected, so a typo like `"chlid"` is caught. Join conditions for
sort_merge_join and hash_join must be an equality between a left and a
right column, or several joined with `and` (a multi-column key);
nested_loop_join takes any condition.

## 8. Pipelined operators [§2.1–2.3, §2.10] ✅

- **seqscan holds 1 frame, not Module 1's 2.** `HeapFile.scan()` keeps a
  directory page and a data page pinned together. seqscan instead pins a
  directory page, copies its list of data page ids (at most 681 integers),
  unpins it, then pins each data page in turn, decodes its live tuples and
  unpins it. **Reads = M data pages + 1 per directory page** (+1 for any
  table up to 681 pages). Every cost table in REPORT_M2.md counts that page.
- Each seqscan opens its own handle on its table file and keeps it across
  rescans, so pages of the inner table left unpinned in the pool can give
  hits on a rescan (an expected gap against the BNL formula, §10).
- **filter** counts one comparison per tuple examined. **project** keeps
  duplicates. Neither pins a page.
- **limit** stops after n tuples and closes its child at once, so the
  child's frames come back and nothing more is read (tested: a limit 5 over
  a 2000-row table reads 2 pages).
- **union_all** opens the right input only after the left one is exhausted
  and closed, so it never holds both sides' frames.
- **Set operator schemas:** same arity and same types position by
  position; output takes the left names; for VARCHAR the larger length, so a
  right tuple always fits the output schema.

## 9. Command line [§4] ✅

`exec run|trace --db DIR --plan plan.json --frames B [--policy lru|clock]`,
installed as the `exec` console script (`python3 -m execution` without
installing).

- `run --json` streams rows as they come out of the root; nothing is
  collected first. **totals.tuples_out** is the number of rows the plan
  returned (the root's counter); comparisons, page_reads and page_writes are
  **summed over every operator**. (The two examples in §4 don't add up to
  the same totals, so we chose the definition that makes totals
  reproducible from the trace.)
- REAL values (AVG) print with exactly four decimals, in JSON too.
- `trace` prints one line per node in plan order, indented two spaces per
  depth, then `peak pinned frames=P of B=…`.
- After every plan: no pinned frame, no temporary file, otherwise an error
  even if the tuples were right.

## 10. External sort [§2.5] ✅

- **F, the sort's B.** The sort opens its child first (leaving at least 3
  frames free), then takes every frame still free: that is F, fixed for the
  whole sort. F plays the role of B in the handout's formulas. With
  `--frames 4` and a seqscan child, F = 3.
- **Pass 0** fills F frames with input tuples (encoded in slotted pages,
  so "F frames" means F pages of data), sorts them with Python's sort (the
  one place §2.5 allows it: one run that fits in memory), and writes the run
  one page at a time. The decoded run shrinks as it is written, so the data
  in memory never exceeds F pages.
- **Merge passes** merge F − 1 runs at a time, one frame per input run and
  one for output. **A last group of a single run is carried forward
  untouched**, not rewritten, so our 2.5 numbers are the handout's first
  variant.
- **Final pass** streams to the parent. Merge passes inside `open` stop as
  soon as the number of runs is at most min(F − 1, limit), where limit is
  the frames the parent leaves the sort while it streams. A sort under a
  parent that leaves it 1 frame therefore merges down to one run and streams
  it with 1 frame (it costs one extra read of the data, which is reported).
- **Input that fits in one run** is never written: it stays in memory
  (its F frames stay reserved) and the sort takes 1 pass, as the formula
  says for R0 = 1. If the parent cannot leave it that many frames, it is
  written and read back.
- **Comparisons**: every comparator call, in pass 0 and in the merges. A
  multi-column key is one comparison per pair. Equal keys keep their input
  order (Python's sort is stable; the merge breaks ties by run number), so
  output and counters are deterministic.
- **Attribution**: pass 0 reads belong to the child (a seqscan's reads land
  on the seqscan); every read and write of run files belongs to the sort.

**2.5 reproduced** (`test_worked_example_2_5`): 8 data pages, F = 3. The
sort reports 14 reads / 14 writes (pass 0 writes 8; pass 1 reads 6 and
writes 6, carrying the third run; pass 2 reads 8 and writes 0). The seqscan
reports 9 reads: the 8 data pages plus the heap's directory page. Plan
total 23 / 14 against the handout's 22 / 14; the difference is the
directory page (§8).

**Passes** match 1 + ceil(log(ceil(N/F)) / log(F − 1)) for N = 8, 80, 100
pages and plan B = 4, 5, 8, 12, 33 (`test_passes_match_formula_for_several_n_and_b`).
**20 × B at B = 4** (80 pages): 27 initial runs, 6 passes, peak pinned
frames ≤ 4, peak reserved exactly 4.

## 11. distinct and group_by [§2.4, §2.6] ✅

One machine for both: a group key, a per-group partial state, and a fold.
distinct is a grouping on every column with no aggregates.

- **Sort mode** sorts the input on the key with the external sort (§10),
  then folds adjacent tuples with equal keys (one comparison per adjacent
  pair).
- **Hash mode.** Phase 0 folds tuples into our own hash table of at most
  min(F − 1, S) pages, where F is the frames free when the blocking work
  starts and S what the operator may keep while streaming. If everything
  fits, the table is streamed: one pass, no I/O. **On overflow** the partial
  table is written to a spill file through one frame, then the rest of the
  input and the spill are hash-partitioned into P = F − 1 partitions.
  Partitions hold *state records* (key + partial state), so raw tuples and
  partial results can share a partition and still be folded. Each partition
  is then folded into a table of S − 1 pages (1 frame reads it). If its
  groups don't fit, it is partitioned again with the next hash seed; after 3
  levels it is sorted and folded instead.
- **Folding is tried before re-partitioning.** A partition of many pages
  may hold few distinct keys (duplicates collapse into one entry), so it is
  read once to try. If it doesn't fit, that read is wasted and counted.
- **The table respects its frames.** Each entry is charged the size of its
  encoded state record plus a 4-byte slot, as if it were stored in a slotted
  page, and the table refuses an entry that would take it past its pages.
  - **Partition lifetime.** After a partition's output is emitted, its
  hash table is cleared in a finally block before another table is built.
  Limiting each table separately is insufficient if the previous table
  remains referenced.
- **Hash comparisons.** The table is a dict from h2(key) to a bucket list;
  a lookup compares the key with every entry of its bucket, one comparison
  each. Partition routing compares nothing.
- **Aggregates.** COUNT(*) and COUNT(col) count tuples (there are no NULLs).
  SUM and AVG require INT (BindError otherwise). SUM's output is an INT that
  may exceed 32 bits, stored in 8 bytes in temp files (type BIGINT inside
  the engine, shown as INT in schemas). AVG is a REAL printed to 4 decimals.
  Output names: `count_star`, `count_R_a`, `sum_R_x`, `avg_R_salary`,
  `max_R_name`.
- **Edge cases.** No grouping columns: one running aggregate in both modes,
  nothing to sort or hash. Empty input with no grouping columns gives
  exactly one row with COUNT 0; any other aggregate requested then is an
  ExecError ("undefined"). Empty input with grouping columns gives zero rows.

## 12. union, intersect, except [§2.10] ✅

Sort-based. Each input is sorted on all its columns with the external sort,
then one merge pass with set semantics (each output tuple once). The left
input is sorted first and merged down so it streams with 1 frame; the right
input is sorted with every frame left. So these need 2 frames to stream.
Comparisons: both sorts, every merge comparison, and one per adjacent pair
checked when skipping duplicates.

## 13. Materialising when the parent leaves too few frames ✅

Every operator declares the frames it needs to stream (1 for most; 2 for
the set operators; 3 for hash-mode grouping, which may partition while
streaming). If its parent leaves fewer, for example a sort, which leaves
its child 1 frame at B = 4, the operator runs to completion inside `open`
with every free frame but one. It writes its output to a temp file through
that one frame, releases everything, and streams the file with 1 frame. The
extra writes and reads are its own and appear in its counters.

**Merging needs 3 frames.** A sort's F is otherwise fixed. But if pass 0
had fewer than 3 frames (the right input of a set operator at B = 4:
1 frame held by the left side, 1 by the right child's seqscan, F = 2), the
frame the child gives back when it closes is taken for the merge passes.

## 14. Block nested loop join [§2.7] ✅

- **Frames.** B is the frames the join may hold while streaming (its open
  limit). K = B − 2 pinned frames hold the outer block. 1 is the inner page
  (the inner child's own frame). The "output frame" reads the outer input:
  output tuples go to the parent through `next()`, so no output page is ever
  filled. At B = 4, K = 2.
- **Comparisons = |R| × |S| exactly.** The condition is evaluated for every
  (outer, inner) pair and counted once per pair, however compound it is,
  matched or not. Tested at B = 4, 5, 8, 16, 64: 700,000 for 1000 × 700.
- **Any condition**, including inequalities.
- **Rescanning the inner.** A pipelined inner (seqscan, filter, project) is
  reopened for each block. Anything else is run once inside `open` and
  written to a temp file, read once per block (N extra writes, reported).
- **An empty outer never scans the inner.**
- **Measured reads.** Outer M + 1, inner ceil(M/K) × (N + 1). The formula
  is M + ceil(M/K) × N, and the gap is exactly the heaps' directory pages:
  1 for the outer, and 1 more on every rescan of the inner. Pool caching
  gives no hits here: between rescans the inner's last pages are pushed out
  of the few unpinned frames before they are needed again (LRU).

## 15. Sort-merge join [§2.8] ✅

- **Sorting.** Both inputs are sorted on the join key with our external
  sort, the left first. Each is merged down to a single run so it streams
  with 1 frame, or kept in memory if it fits in one page. So cost =
  sort(M) + sort(N) + M + N, where sort(X) includes writing the final run.
- **Merge** follows the handout's pseudocode. The right side's run of equal
  keys is collected, then replayed ("rewind to mark") for every left tuple
  with that key. The 2.8 example returns four rows (tested).
- **Nested right inputs at small budgets.** When necessary, the completed
  left result is parked on disk and its streaming reservation released
  while the right subtree opens. The left-reader reservation is restored
  before merging. Parking a memory-resident run adds temporary I/O.
- **A run of equal keys larger than memory is spilled.** The run is kept in
  memory while it fits in one page (the third merge frame). Beyond that it
  is written to a temp file through the same frame and re-read for every
  left tuple of the run. Tested with one key on 500 left and 300 right
  tuples (about 21 KB) at B = 4: 150,000 rows, 1 spill.
- **Comparisons**: every key comparison in the merge (left vs right, and
  right vs right when collecting a run, and left vs the run when advancing
  through the left run), plus all comparisons inside both sorts.
- **Equality only**: `L.x = R.y`, or several joined with `and`, in either
  order. Anything else is a BindError pointing to nested_loop_join.

## 16. Grace hash join [§2.9] ✅

- **Partition phase.** The left child is opened with 1 frame (the input
  frame); every other free frame becomes a partition: P = B − 1. Left is
  partitioned by h1(key) mod P and closed, then the right with the same P.
  h1 at level d is our FNV hash with seed d; the in-memory table uses h2
  (seed 1000), so partitioning and bucketing never correlate.
- **Opening the right subtree.** After the left partition writers close,
  their frame reservations are released while the right child opens.
  The partition reservations are reacquired before consuming its output.
- **Probe phase** (in `next`). The join keeps S = min(B, limit) frames:
  a table of S − 1 pages and 1 frame to read R_i, then S_i. **A pair with
  an empty side is skipped unread**, since nothing can match. That is why
  measured probe reads can be below M + N.
- **Overflow: re-partition, then fall back to block nested loop.** If R_i
  does not fit in S − 1 pages, R_i and S_i are partitioned again with the
  next seed into S − 1 sub-partitions, up to 3 levels in all. A
  sub-partition that did not shrink at all is sent straight to the
  fallback, because another seed cannot split a single key. The fallback is
  a block nested loop on that one pair: K = S − 2 pages of R_i per block,
  S_i streamed past it, one comparison per pair.
- **Comparisons**: in the probe, each key comparison against an entry of
  the probed bucket; in the fallback, every pair.
- **Skew** (40 % of rows on key 0, both sides; 1500 × 1000 rows, B = 4,
  P = 3): partitions (11, 8), (4, 3), (4, 3) pages. The hot partition
  shrinks only as its other keys are split off (11 → 9 → 8 pages over two
  re-partitions), then falls back to nested loop at level 3. The two normal
  partitions each need one re-partition at B = 4. Result identical to the
  other two joins. Full cost comparison against the uniform case is in
  REPORT_M2.md.

## 17. Memory summary [§3] ✅

B below is the frames the operator may use: during `open`, every frame free
at that moment; while streaming, at most its `limit`.

| Operator | Blocking | Frames it holds |
|---|---|---|
| seqscan | no | 1 (one page pinned at a time) |
| filter, project, limit, union_all | no | 0 of their own (they pin nothing) |
| sort | yes | F = all free frames in `open`; then min(runs, limit) while streaming |
| distinct, group_by (sort) | yes | as sort |
| distinct, group_by (hash) | yes | F in `open`; S = min(F, limit) ≥ 3 while streaming |
| union, intersect, except | yes | both sorts, then 1 (left run) + up to limit − 1 (right) |
| nested_loop_join | no | B − 2 outer block, 1 inner page, 1 outer input |
| sort_merge_join | on open | its sorts, then 3: left run, right run, equal-key run |
| hash_join | on open | B − 1 partitions + 1 input; then S − 1 table + 1 input |

An operator whose parent leaves it fewer frames than it needs to stream
materialises its output (§13).

**How the budget is checked.** peak_pinned records pinned buffer frames,
and peak_reserved records declared frame reservations. Neither alone
detects tuple collections retained outside the pool. Encoded-size
accounting and lifetime tests are therefore also required. Regression
tests cover completed partition-table cleanup and nested joins at B = 4.
