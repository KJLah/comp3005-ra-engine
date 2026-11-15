# Module 4 report: the optimizer, measured

Every number comes from `python3 measure_m4.py` (about half a minute). It
builds the `gen_m4.py` database (our own, seed 3005, DESIGN_M4.md A1),
ANALYZEs it, plans the twenty queries in `queries/m4/`, and runs every
physical plan through the Module 2 executor. The CSV files are in
`results/m4/`. B = 8 unless stated. Every plan's answer, including every
plan of the rule ablation, is checked against the unoptimized plan's.

**Data.** Emp 2000 rows on 47 pages, Works 6000 rows on 100 pages, Proj
300 rows on 2 pages, Dept 40 rows on 1 page. Emp.city decides Emp.province
and Emp.area; cities are skewed (Zipf-like); did and pid are keys of Dept and
Proj. Measured I/O includes the heap's directory page on every scan
(REPORT_M2), so a plain scan of Emp measures 48 where the model says 47.

## 1. Estimation quality

Estimated rows (explain's est_rows) against the rows each operator actually
emitted (Module 2's tuples_out), at every node of the twenty chosen plans.
q-error = max(est, actual) / min(est, actual), both floored at 1.

137 nodes. Median q-error 1.00, 90th percentile 1.83, worst 45.70.

| query | nodes | median q-error | worst q-error |
|---|---|---|---|
| q01_eq | 3 | 1.0 | 1.0 |
| q02_range | 3 | 1.0 | 1.0 |
| q03_and | 3 | 1.17 | 1.17 |
| q04_varchar_range | 3 | 1.0 | 1.0 |
| q05_or | 3 | 1.29 | 1.29 |
| q06_not | 3 | 1.08 | 1.08 |
| q07_join_on | 10 | 1.0 | 1.0 |
| q08_join_commas | 10 | 1.0 | 1.0 |
| q09_three_way | 14 | 1.0 | 1.08 |
| q10_four_way | 20 | 1.02 | 1.83 |
| q11_group_having | 4 | 1.0 | 1.56 |
| q12_group_key_filter | 4 | 1.0 | 1.0 |
| q13_distinct | 3 | 1.0 | 4.0 |
| q14_order_limit | 5 | 1.03 | 45.7 |
| q15_union_pushdown | 8 | 1.0 | 1.0 |
| q16_self_join | 8 | 1.0 | 7.0 |
| q17_correlated_join | 9 | 2.0 | 4.9 |
| q18_correlated_count | 4 | 1.67 | 2.34 |
| q19_join_group | 10 | 1.0 | 1.1 |
| q20_works_proj | 10 | 1.0 | 1.21 |

Worst nodes:

| query | node | est rows | actual rows | q-error |
|---|---|---|---|---|
| q14_order_limit | sort [Emp.salary desc] | 457 | 10 | 45.7 |
| q16_self_join | project [a.dname, b.dname] | 14 | 98 | 7.0 |
| q16_self_join | nested_loop_join (a.city = b.city and a.did < b.did) | 14 | 98 | 7.0 |
| q17_correlated_join | project [e.eid, e.name, e.note] | 83 | 407 | 4.904 |
| q17_correlated_join | filter (e.city = 'Ottawa' and e.area = '201') | 83 | 407 | 4.904 |
| q17_correlated_join | project [e.name, e.note, w.hours] | 289 | 1240 | 4.291 |

**Reading it.** The median is 1.00: scans are exact (their rows are
statistics), and on single columns the histograms are good. q01 is the skew
test: `city = 'Toronto'` is estimated at 250 against 223 actual, where
1/ndv would have said 100 (q-error 2.2). The worst nodes are the interesting
part, and they have three different causes:

- **q14, 45.7, is not an estimation error.** The sort's estimate (457 rows)
  is right: 457 employees are under 30. But `LIMIT 10` stops pulling after
  ten rows, so the sort emitted only 10 (A24). An executor that counted the
  rows a blocking operator produced, not emitted, would show 1.0 here.
- **q16, 7.0: the 0.1 default.** `a.city = b.city AND a.did < b.did` is a
  self join. `a.did < b.did` gets the documented 0.1 for "any other join
  predicate", but in a self join an ordering predicate keeps about half of
  the non-identical pairs. The 0.1 rule is exactly the guess Section 2 says
  to state rather than hide, and this is what it costs.
- **q17 (4.9) and q13 (4.0): correlation.** Both multiply selectivities or
  distinct counts of columns that are not independent; section 4.

q12 was 4.4 before improvement 3 (DESIGN_M4 §3): `HAVING did < 10` pushed
below the group_by left ndv(did) at 40, so 40 groups were estimated for 9.
Scaling a filtered column's ndv by its selectivity fixed it (1.0 now).

## 2. Does optimization pay

Unoptimized = no rewrite rules, joins as written, every join a nested loop
join (A21). Both plans at B = 8; I/O is reads plus writes measured by the
executor.

Ratio = unoptimized I/O / optimized I/O. Range 1.0 to 14.12, geometric mean 1.73.

| query | chosen joins | est cost | optimized I/O | unoptimized I/O | ratio |
|---|---|---|---|---|---|
| q01_eq | - | 47 | 48 | 48 | 1.0 |
| q02_range | - | 47 | 48 | 48 | 1.0 |
| q03_and | - | 47 | 48 | 48 | 1.0 |
| q04_varchar_range | - | 47 | 48 | 48 | 1.0 |
| q05_or | - | 47 | 48 | 48 | 1.0 |
| q06_not | - | 47 | 48 | 48 | 1.0 |
| q07_join_on | BNL | 48 | 50 | 64 | 1.28 |
| q08_join_commas | BNL | 48 | 50 | 64 | 1.28 |
| q09_three_way | BNL+BNL | 152 | 164 | 1648 | 10.05 |
| q10_four_way | BNL+BNL+BNL | 154 | 160 | 2260 | 14.12 |
| q11_group_having | - | 47 | 48 | 226 | 4.71 |
| q12_group_key_filter | - | 47 | 48 | 226 | 4.71 |
| q13_distinct | - | 47 | 68 | 68 | 1.0 |
| q14_order_limit | - | 47 | 48 | 48 | 1.0 |
| q15_union_pushdown | - | 48 | 50 | 50 | 1.0 |
| q16_self_join | BNL | 2 | 4 | 4 | 1.0 |
| q17_correlated_join | BNL | 147 | 250 | 856 | 3.42 |
| q18_correlated_count | - | 47 | 48 | 48 | 1.0 |
| q19_join_group | BNL | 49 | 50 | 106 | 2.12 |
| q20_works_proj | BNL | 102 | 104 | 152 | 1.46 |

**Where it pays and why.**

- **Joins (q09 10×, q10 14×, q17 3.4×, q19 2.1×).** The unoptimized q09
  (`exec trace`) joins all of Emp to all of Works first: Emp's full records
  take 10 blocks, so Works is read 10 times (1010 reads, plus 236 pages of
  block spill), then the 6000-row result meets Proj in 40 blocks (118
  reads), and only then is Proj.budget filtered. The optimized plan
  filters Proj first (35 of 300 projects), starts the left-deep tree from
  it, and projects every input to the columns it needs, so each BNL outer
  fits in one block and every inner is read once (164 in total).
- **Group by (q11, q12 4.7×).** With rule 6, the group_by reads a
  projection of Emp to one or two INT columns (2000 rows, 2 to 4 pages),
  which fits in memory and costs nothing beyond the scan. The unoptimized
  group_by sorts whole 90-byte Emp records: 47 pages do not fit in B = 8,
  so the sort writes runs and reads them back, 178 extra pages.
- **No gain (ratio 1.0, eleven queries).** A single-table query has one
  access path, a scan, and the filter is pipelined either way: there is
  nothing to choose without indexes. q13's distinct already reads only the
  two columns it needs, q15's union_all is pipelined whether the filter is
  above or below it, and q16 is two one-page tables.

The range across the workload is 1.0 to 14.1; the geometric mean is 1.73
over all twenty, and 3.0 over the ten queries with a join or a GROUP BY
(q07 to q12, q16, q17, q19, q20).

## 3. Which rule mattered

One rule off at a time, everything else unchanged, total I/O over the
twenty queries at B = 8. Comparisons are summed over every operator, because
two rules change CPU work and not I/O.

| rule off | total I/O, all rules | total I/O without it | difference | comparisons, all rules | comparisons without it | I/O change per query |
|---|---|---|---|---|---|---|
| 1 | 1430 | 1618 | 188 | 4,447,812 | 6,484,211 | q10 160->348 |
| 2 | 1430 | 1874 | 444 | 4,447,812 | 17,830,371 | q09 164->339; q10 160->434; q17 250->245 |
| 3 | 1430 | 1430 | 0 | 4,447,812 | 4,454,768 | none |
| 4 | 1430 | 1430 | 0 | 4,447,812 | 4,447,812 | none |
| 5 | 1430 | 1430 | 0 | 4,447,812 | 4,462,080 | none |
| 6 | 1430 | 1786 | 356 | 4,447,812 | 4,452,765 | q09 164->280; q10 160->174; q11 48->226; q12 48->68; q19 50->78 |
| 7 | 1430 | 1430 | 0 | 4,447,812 | 4,450,903 | none |

- **Rule 2 (push selects down) matters most:** 444 pages and 4× the
  comparisons. Without it a filter stays above the join it could have
  shrunk, so the join reads and compares every tuple. q17 got slightly
  better without it (250 → 245): with the filter above the join the DP no
  longer sees the badly estimated 83-row input, chooses Works as the outer,
  and happens to avoid the plan that section 4 shows is wrong.
- **Rule 6 (projections) is second:** 356 pages. It is what makes the
  group_by inputs fit in memory (q11 48 → 226 without it) and keeps every
  BNL outer within B − 2 frames.
- **Rule 1 (split conjunctions)** matters only through rule 2: q10's
  `d.budget < 50000 AND w.hours > 30` uses two relations, so as one
  predicate it cannot be pushed to either (188 pages, all in q10).
- **Rules 3, 4, 5 and 7 save nothing on this data, and why:**
  - Rule 3 (cross + equality → join) fires only in q08, the comma version
    of q07. Without it q08 runs as a BNL cross product with a filter above.
    Our BNL compares every pair whatever the condition (Module 2 §2.7), and
    the filtered Dept fits in one block, so I/O is the same; only the
    filter's 6,956 extra comparisons show. It would matter where the cross
    feeds another join (its output is 40× larger) or where B is small enough
    that a hash join would win, because hash and SMJ need an equality
    condition.
  - Rule 4 (through rename and union) fires in q15, but union_all is
    pipelined and so is the filter: each row is read and tested once either
    way.
  - Rule 5 (through group_by) fires in q12, but with rule 6 the group_by
    input is 2 pages and fits in memory either way. It would pay when the
    group_by has to spill.
  - Rule 7 is cosmetic by design.

## 4. Where it breaks: correlated columns

**The query (q17).**

```sql
SELECT e.name, e.note, w.hours FROM Emp e JOIN Works w ON e.eid = w.eid
WHERE e.city = 'Ottawa' AND e.area = '201';
```

Area 201 is Ottawa's area code, so the second predicate removes nothing.
**The assumption that failed is independence** (Section 2's p AND q rule):
sel(city = 'Ottawa') = 0.20 and sel(area = '201') = 0.20 from their
histograms, both right (407 of 2000 rows), but their product 0.041 is not
the selectivity of the conjunction, which is 0.20 (area is a function of
city).

| B | q17 est rows | actual | join | est cost | actual I/O | city only: est rows | actual | join | est cost | actual I/O |
|---|---|---|---|---|---|---|---|---|---|---|
| 4 | 83 | 407 | BNL | 147 | 452 | 407 | 407 | hash | 183 | 271 |
| 6 | 83 | 407 | BNL | 147 | 250 | 407 | 407 | hash | 183 | 211 |
| 8 | 83 | 407 | BNL | 147 | 250 | 407 | 407 | BNL | 147 | 250 |
| 32 | 83 | 407 | BNL | 147 | 149 | 407 | 407 | BNL | 147 | 149 |

q18 filter: est 174, actual 407.

"city only" is the same query without the redundant predicate: the same
rows, now estimated correctly, so it shows the plan the optimizer would
have chosen with the right number.

- **Estimate 83 rows, actual 407** (q-error 4.9). The join's estimate is
  289 against 1240.
- **At B = 4 the bad plan costs 452 page I/Os against 271 for the better
  one (1.67×).** With 83 rows the filtered, projected Emp is estimated at 2
  pages, which fits in B − 2 = 2 frames, so a BNL with Emp outer looks like
  one scan of each table (47 + 100 = 147, the cheapest). In fact 407 rows
  are 7 pages, 4 blocks, and Works is read 4 times (the trace shows
  `nested_loop_join blocks=4` and 404 reads on Works). With the right
  estimate the optimizer picks a hash join, which reads Works once and
  partitions its two projected columns (12 pages).
- At B = 6 the same mistake costs 250 against 211 (1.18×); from B = 8 up,
  even 7 pages fit in one block and the mistake is harmless. Note that the
  "better" hash plan is itself under-predicted (183 estimated, 271 actual
  at B = 4): our hash join's partitions overflow at small B (REPORT_M2 §1).
- q18 shows the same failure without a join: city AND province is
  estimated at 174 rows against 407 (2.3×), and q13's distinct over
  (city, province) at 80 groups against 20, because the product of the two
  ndvs assumes every combination occurs.

**What statistic would have prevented it.** A multi-column statistic on
(city, area): the number of distinct combinations, ndv(city, area) = 20.
Since it equals ndv(city), area adds no information given city, and the
estimator could use sel(city) alone for the conjunction (this is what
PostgreSQL's functional-dependency statistics do). A two-dimensional
histogram or a list of the most common (city, area) pairs would also work
and would handle partial correlation, at a higher storage cost. ANALYZE
could compute ndv of a column pair with one more external sort on the pair,
and the same number fixes q13's distinct estimate.

## 5. B changes the join algorithm

Joins chosen for each query at four budgets (`results/m4/algorithms_by_b.csv`):

| query | B=3 | B=4 | B=8 | B=32 |
|---|---|---|---|---|
| q07_join_on | BNL | BNL | BNL | BNL |
| q08_join_commas | BNL | BNL | BNL | BNL |
| q09_three_way | hash+BNL | hash+BNL | BNL+BNL | BNL+BNL |
| q10_four_way | BNL+SMJ+BNL | BNL+BNL+BNL | BNL+BNL+BNL | BNL+BNL+BNL |
| q16_self_join | BNL | BNL | BNL | BNL |
| q17_correlated_join | hash | BNL | BNL | BNL |
| q19_join_group | BNL | BNL | BNL | BNL |
| q20_works_proj | BNL | BNL | BNL | BNL |

At B = 32 every outer fits in B − 2 frames, so a BNL is one pass over each
input and always wins. At B = 3 and 4, q09's first join (the filtered Proj
with Works) becomes a hash join; at B = 3, q10 picks a sort-merge join and
q17 a hash join. The checklist item is tested on q17
(`test_m4_planner…test_changing_b_changes_the_join_algorithm`).

## 6. DP table for a four-relation query

q10 joins Emp (e), Works (w), Proj (p) and Dept (d) with
`d.budget < 50000 AND w.hours > 30`. Printed by
`opt dp-table --db data/m4 --logical queries/m4/q10_four_way.json --frames 8`:

```
Subset     Best plan considered                                                     Cost
{e}        scan Emp                                                                 47
{w}        scan Works + filter                                                      100
{p}        scan Proj                                                                2
{d}        scan Dept + filter                                                       1
{e,w}      w outer, BNL with e inner, 100 + 1×47                                    147
{e,p}      no predicate, skipped                                                    -
{e,d}      d outer, BNL with e inner, 1 + 1×47                                      48
{w,p}      p outer, BNL with w inner, 2 + 1×100                                     102
{w,d}      no predicate, skipped                                                    -
{p,d}      no predicate, skipped                                                    -
{e,w,p}    {e,w} outer (10 pages), BNL with p inner, 147 + 10 + 2×2                 161
{e,w,d}    {e,d} outer (2 pages), BNL with w inner, 48 + 2 + 1×100                  150
{e,p,d}    no predicate, skipped                                                    -
{w,p,d}    no predicate, skipped                                                    -
{e,w,p,d}  {e,w,d} outer (2 pages), BNL with p inner, 150 + 2 + 1×2                 154
           alternative, {e,w,p} outer (15 pages), BNL with d inner, 161 + 15 + 3×1  179
```

The DP never pairs relations without a predicate ({e,p}, {w,d}, {p,d} and
the two triples built on them are skipped). It joins the filtered Dept
(4 of 40 rows) with Emp first, which shrinks Emp to 2 pages, then Works,
then Proj. The alternative that starts from Emp and Works carries 10 to 15
pages through the later joins and costs 179. The chosen plan's estimated
cost is 154 and its measured I/O 160 (`exec trace`): the four scans read
154 pages against 150 estimated (one directory page each), and the three
joins wrote and read back 6 block pages against the 4 pages the model
charges for reading the two intermediate outers.
