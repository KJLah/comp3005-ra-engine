# Module 4 design: query optimizer

This document records the design of the optimizer (`optimizer/`) and every
choice the Module 4 document leaves open. Section numbers in brackets refer
to the Module 4 document. The measurements are in [REPORT_M4.md](REPORT_M4.md).

The instructor confirmed that the twenty-query workload and the dataset will
not be published, and that any reasonable assumption is fine if it is stated
in one line. The assumptions are listed first; the sections after them
explain the design.

---

## 0. Assumptions (one line each)

| # | Assumption |
|---|---|
| A1 | The dataset and the twenty queries are our own: `gen_m4.py` (seed 3005) and `queries/m4/q01..q20.sql`, built to contain the correlated columns §7 asks for. |
| A2 | The input of `opt` is the Module 3 logical plan JSON (`sql compile --json`); `--sql q.sql` compiles the SQL first as a convenience. |
| A3 | `n_pages` counts data pages only (directory entries), as §1 says; Module 1's catalog `num_pages` keeps counting the directory page and is not changed. |
| A4 | Without ANALYZE, a VARCHAR(n) column is assumed half full (2 + n/2 bytes) and an INT is 4 bytes. |
| A5 | Without ANALYZE, ndv = n/10, c = v has selectivity 1/10 and a range 1/3 (no min/max is known). |
| A6 | An equality with no histogram bucket containing v (v falls between buckets, or outside [min, max]) has selectivity 0, then the row floor of 1 applies. |
| A7 | Inside a bucket and between min and max, values are treated as continuous, so `age > 60` over 20..70 is 10/50 exactly as in §2's example. |
| A8 | VARCHAR ranges are interpolated by reading the first 8 characters as a base-256 number (improvement 1). |
| A9 | Histogram cuts never split equal values, so a bucket can be larger than n/k and there can be fewer than k buckets. |
| A10 | ANALYZE reads the table once for n_tuples, n_pages and width, and sorts each column with the Module 2 external sort (a sort-over-project-over-seqscan plan), which reads the table once more per column. |
| A11 | Column statistics live in a heap file `T.stats` (a catalog record is limited to 4084 bytes); the catalog's statistics field holds the table-level numbers and the file name. |
| A12 | Rule 4 does not push a select between a rename and its scan ("rename e (scan Emp)" is one access path); this is also what the §3 example shows. |
| A13 | Rule 3 also adds a two-sided predicate that sits directly on a join to that join's condition (extension); it still converts a cross only for an equality, as §3 says. |
| A14 | Rule 6 inserts projections below joins, crosses, group_bys and sorts (where narrower input saves I/O), never between a rename and its scan. |
| A15 | The cost of an intermediate input is charged once to the operator that consumes it (the "+20" of §5.1); an input that is a pipeline over a base table is read by its own seqscan and not charged again. |
| A16 | §5.1's alternative row prints 550 using 1 block for 40 pages; with B − 2 = 30 frames 40 pages are 2 blocks, so our table prints 650. The winner is the same. |
| A17 | Set operators are costed as sorts of both inputs (our Module 2 implements them by sorting); `union_all`, `limit` and `rename` cost 0. |
| A18 | group_by and distinct use hash mode only when its formula is strictly cheaper than sort mode; on a tie, sort mode (ordered output). |
| A19 | The DP handles up to 6 relations per join block; a larger block keeps the written order with nested loop joins. |
| A20 | Ties in the DP are broken by generation order (inner relation in FROM order, then BNL, SMJ, hash), so plans are deterministic. |
| A21 | The "unoptimized plan" of §7 has no rewrite rules, joins in written order, every join (and cross) a nested loop join, and the same group_by mode rule as the optimized plan. |
| A22 | Estimated cost and measured I/O are compared at the same B; the measured I/O includes the heap's directory page on every scan (Module 2 report), so a plain scan measures M + 1. |
| A23 | The physical plan may use the four Module 4 forms added to the executor (rename, project "as", cross as a nested loop join without condition, named aggregates); they are optional fields, so every Module 2 plan is still valid. |
| A24 | Rows of a node under a limit are compared with what the node actually emitted before the limit stopped pulling, so a sort under LIMIT 10 shows 10 actual rows (REPORT_M4 §1 discusses it). |
| A25 | A one-sided conjunct inside a join condition (`JOIN … ON e.did = d.did AND d.budget > 5`) becomes a filter on that relation when rule 2 is on, and a filter above the joins when it is off; with rule 1 off, a join condition stays one predicate. |

---

## 1. Executor additions [§6 "runs unedited"]

A Module 3 plan uses four things Module 2 could not run: rename, cross,
column aliases and aggregate output names. Each is an optional addition,
validated in `execution/plan.py`:

| Form | Meaning |
|---|---|
| `{"op":"rename","to":"e","child":…}` | pipelined; every output column becomes `e.<short name>` |
| `"as": [null, "who"]` on project | output names for the projected columns (null keeps the name) |
| `nested_loop_join` without `condition` | a cross product (every pair) |
| `["count","*","n"]` in aggregates | third element: the output column's name |

Two fixes to Module 2 were needed to run the workload at small B:
the nested loop join now keeps its inner frame reserved for the whole join
(a sort above a join used to take that frame between two rescans of the
inner, and the next rescan failed with a budget error), and a seqscan whose
open failed no longer crashes in close.

## 2. Statistics layout [§1]

```
catalog record, statistics field (JSON, Module 1 left room for it):
  {"analyzed": true, "n_tuples": 2000, "n_pages": 47, "width": 89.829,
   "buckets": 20, "stats_file": "Emp.stats"}

Emp.stats, a heap file, one record per column (JSON):
  {"col": "age", "ndv": 46, "min": 20, "max": 65, "width": 4.0,
   "hist": [[20, 22, 135, 3], [23, 25, 131, 3], ...]}     # [lo, hi, count, ndv]
```

- A record holds at most 4084 bytes. A column whose histogram does not fit
  (long VARCHAR bounds, many buckets) is split into records with the same
  `col` and `part` 0, 1, …; the reader joins them in order. Tested with
  100 buckets of 150-character strings.
- **ANALYZE** (`opt analyze`, `optimizer/stats.py`): one scan of the
  original table counts live tuples and record bytes while materializing
  live records in temporary storage. n_pages counts the original table's
  directory entries. Per-column statistics use Module 2 external sort
  over the temporary copy. The original table is not rescanned for each
  column, and the full table is never collected in a Python list.
- **Equi-depth histogram**: target size ceil(n/k); a bucket closes once it
  holds at least that many tuples and the next value is different. The
  per-bucket ndv therefore add up to the column's ndv, and every value is in
  exactly one bucket.
- **Idempotent**: T.stats is deleted and rewritten from the data, and the
  catalog record is rewritten only if its statistics changed. Running
  ANALYZE twice leaves every file byte-for-byte identical (tested).

## 3. Selectivity and cardinality [§2]

| Predicate | Selectivity |
|---|---|
| c = v | bucket holding v: count / (n_tuples × ndv(bucket)); no histogram: 1/ndv; no bucket holds v: 0 |
| c < v, c > v | whole buckets on that side plus a linear fraction of v's bucket; no histogram: linear between min and max |
| c <= v, c >= v | the range plus sel(c = v) |
| c <> v | 1 − sel(c = v) |
| p AND q | sel(p) × sel(q) |
| p OR q | sel(p) + sel(q) − sel(p) × sel(q) |
| NOT p | 1 − sel(p) |
| R.a = S.b (join) | 1 / max(ndv(R.a), ndv(S.b)), so rows = n(R) × n(S) / max(ndv) |
| any other two-column predicate | 0.1 |
| an aggregate's output (HAVING) | c = v 0.1, ranges 1/3 |

Output rows are ceil(input × selectivity), never below 1; a base table's
rows and pages are its statistics. Output pages are ceil(rows × width /
4096), where width is the sum of the output columns' average widths (each
column's width is measured by ANALYZE), so a projection reduces the pages of
everything above it. A join's rows are n(L) × n(R) × the product of its
conjuncts' selectivities. group_by: the product of the keys' ndv, capped by
the input rows; distinct: the product over all columns, capped likewise.

**Improvements over the required formulas, and why:**

1. **VARCHAR ranges** are interpolated (A8). The required formula needs a
   linear fraction, which has no meaning for strings without a mapping;
   1/3 would ignore the data.
2. **Same-table column equality** (`T.a = T.b` in a select) uses
   1/max(ndv), the join formula, instead of a constant.
3. **ndv after a filter**: a column compared with a constant keeps
   ndv × sel distinct values (1 for c = v). Without it, `GROUP BY did HAVING
   did < 10` was estimated at 40 groups instead of 9 (q12, q-error 4.4 → 1.0).

The §2 worked example reproduces exactly (`tests/test_m4_estimate.py`):
0.02 → 200 rows, 0.20 → 2000, 0.004 → 40, and the foreign-key join 10,000.

## 4. Rewrite rules [§3]

Applied in this order: rule 1 everywhere; one bottom-up pass in which each
select sinks as far as rules 2 to 5 allow; rule 7 collapses adjacent
selects; rule 6 inserts projections top-down; rule 7 removes projections
that drop nothing and merges a projection into the one above it. Every rule
can be switched off (`opt … --off 2,6`).

| # | Rule | Precondition, as implemented |
|---|---|---|
| 1 | split `select[p AND q]` | none |
| 2 | push a select below a join or cross | every column it uses is an output of one side (`conds.covers`) |
| 3 | cross + select → join | the select uses both sides and is `a = b` with a in one side and b in the other (A13 for the extension) |
| 4 | push below rename / union / union_all | rename: names rewritten by position through the rename, not when the rename's child is a scan (A12); unions: into both branches, names rewritten to the right branch's columns by position |
| 5 | push below group_by | every column used is a grouping key; a predicate on an aggregate never moves |
| 6 | insert projections | keep what the ancestors need plus what the operator uses itself (join condition, grouping keys and aggregate inputs, sort keys); a column used by a predicate above is always kept |
| 7 | tidy | a projection that keeps every column in order is removed; adjacent selects become one |

The §3 example reproduces line for line (`opt explain --rewrites` prints both
trees side by side; `tests/test_m4_rewrite.py` checks the tree and that
exactly rules 1, 2, 3 and 6 fired). The negative tests apply each rule where
its precondition fails and check that nothing moves: a two-sided predicate
stays above the join, a non-equality stays a select over the cross, a
HAVING on COUNT(*) stays above the group_by.

## 5. Cost model [§4]

The formulas are §4's exactly, with B the plan's frames:

```
seqscan M          filter, project, rename, limit, union_all 0
sort               2M(passes − 1) + M,   passes = 1 + ceil(log(ceil(M/B)) / log(B − 1))
group_by/distinct  sort mode: the sort; hash mode: 0 if M ≤ B − 1, else 2M
nested_loop_join   M + ceil(M/(B − 2)) × N
sort_merge_join    sort(M) + sort(N) + M + N
hash_join          3(M + N)
union etc.         2M(passes − 1) + 2N(passes − 1)
```

**One node's cost** (explain's est_cost) is its formula minus the reads its
input already paid for (A15): an input that is a pipeline over a base table
is read by its own seqscan node, which costs the table's pages, so the
parent's formula does not count that read again. Any other input is charged
once. For BNL, a pipelined inner is rescanned ceil(M/(B − 2)) times and each
rescan reads its whole base table (the filter is above the scan); the first
scan is the inner seqscan's own cost. The plan's cost is the sum over its
nodes. With this, §5.1 reproduces: {A,B′} = 10 + 1×100 = 110 and
{A,B′,C} = 110 + 20 + 1×400 = 530.

**Agreement with Module 2.** These are the formulas REPORT_M2 measured. The
known gaps are on the executor side and are left as they are: every scan
reads one directory page more than M (A22), our sort runs with F = B − 1
frames because its child holds one, and hash join partitions overflow at
small B (REPORT_M2 §1). The optimizer therefore under-predicts hash join at
small B: on q17 at B = 4 it predicts 183 and the executor reads and writes
271 pages (REPORT_M4 §4). We kept §4's formulas rather than fitting
our executor, because the report must show where the model and the machine
disagree, and fitting would hide that.

## 6. Join enumeration [§5]

`optimizer/dp.py`. Relations are the leaves of a join block (a tree of join
and cross nodes, seen through the projections rule 6 put between them);
each leaf is planned first, so its access path already has its filters and
projection. Predicates are the conjuncts of the join conditions; each knows
which relations it uses.

```
best[{r}] = r's access path
for k = 2 .. n, every subset S of size k, every r in S (r is the inner):
    links = predicates between S − {r} and r
    skip if links is empty, unless the join graph is disconnected
    candidates: BNL always; SMJ and hash when a link is a cross-side equality
best[S] = cheapest candidate
```
If the first enumeration cannot construct a complete plan, enumeration
is retried with Cartesian combinations allowed. This handles predicates
that require three or more relations before they can be evaluated.

Conjuncts that use one relation only (from a JOIN ON) are not join
predicates; see A25. Sizes come from §2: rows(S) is the product of the relations' rows and the
selectivities of the predicates inside S, independent of the order, so every
candidate for S agrees on its output size. SMJ and hash get the equalities
as their condition (written left column first); other predicates of the
same join go in a filter directly above it. The final column order is
restored by a projection, so a reordered join is invisible to the operators
above it.

**What left-deep gives up and what it buys.** With n relations there are n!
left-deep orders but (2(n − 1))!/(n − 1)! bushy trees: for 6 relations, 720
against 30,240, and the DP's table has 2ⁿ entries instead of 3ⁿ subset
pairs to consider. Every inner is a base relation, so a nested loop join can
rescan its inner cheaply from the table (no materialization), and the outer
side is a single pipeline from the first scan to the last join, which never
has to be written to disk. What it gives up are plans that join two
intermediate results, for example (A ⋈ B) ⋈ (C ⋈ D) when both pairs are
small and selective; a left-deep plan must carry one of them through every
later join. We did not measure bushy plans, so we cannot say what that
costs on our workload.

`opt dp-table` prints one line per subset (best plan, its cost expression
and its cost; "no predicate, skipped" for subsets without a connecting
predicate) and, for the full set, the best plan of every other split as an
alternative. The §5.1 table reproduces with §5.1's sizes
(`tests/test_m4_cost_dp.py`), apart from A16.

## 7. Planner and interface [§6]

`optimizer/planner.py`: rewrite → lower (scan → seqscan, select → filter,
join blocks → DP, group_by/distinct modes chosen by cost) → rules 6 and 7
once more on the physical tree, so the reordered joins get their
projections. Every plan passes `execution.plan.validate` and runs; the tests
run all twenty queries at B = 4, 8 and 32 and compare the result bag with
the unoptimized plan's.

```
opt analyze  --db DIR --table R [--buckets 20]      (no --table: every table)
opt plan     --db DIR --logical q.json --frames 32 --json
opt explain  --db DIR --logical q.json --frames 32 [--rewrites]
opt dp-table --db DIR --logical q.json --frames 32
             [--off 2,6] switches rules off; [--sql q.sql] instead of --logical
```

explain prints one line per physical node with est_rows and the node's own
est_cost, then the plan's total. Checklist items and where they are tested:

| §8 item | Test |
|---|---|
| §3 rewrite example reproduces | `test_m4_rewrite.Section3Example` |
| §5.1 DP table reproduces | `test_m4_cost_dp.Section51` |
| every physical plan runs unedited | `test_m4_planner.Workload.test_every_plan_validates_and_runs…`, `CommandLine.test_plan_output_runs_in_exec` |
| commas and JOIN ON give the same plan | `test_m4_planner…test_commas_and_join_on…` (q07, q08 at B = 4, 8, 32) |
| B changes the join algorithm | `test_m4_planner…test_changing_b…` (q17: hash at B = 3, BNL at 32); REPORT_M4 §5 |
| ANALYZE twice changes nothing | `test_m4_stats.Analyze.test_analyze_twice_changes_nothing` |
| no statistics still gives a valid plan | `test_m4_planner.NoStatistics` |
