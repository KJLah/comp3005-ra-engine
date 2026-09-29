# Performance study

**Machine:** «MacBook model, chip (e.g. Apple M2) and RAM, from  → About This Mac»,
arm64
**OS:** macOS 15.5 (`macOS-15.5-arm64-arm-64bit`)
**Language:** CPython 3.11.7
**Command:** `python3 bench.py` (data from `gen_data.py`, match rate 1, seed 42)

Every number below comes from `results/tables.md`, `results/join.csv`,
`results/unary.csv`, `results/match.csv` or `results/machine.txt`, all written
by `bench.py` in a single run. The derived numbers (ratios, ns per comparison,
the slopes over part of the range) were computed from those files.

## Instrumentation (8.2)

* **Join counter** (`JoinOp.execute` in `ra_engine/operators.py`). It adds 1
  inside the inner loop, before the condition is evaluated, so it counts
  every pair the condition is evaluated on, match or not.
* **Select counter** (`SelectOp.execute`). It adds 1 for every input tuple
  the condition is evaluated on.

`ra.py --stats` prints both after a query runs. Wall time is measured with
`time.perf_counter()` around `execute()` only, so it excludes loading,
parsing and planning. Up to n = 16000 each join is run 3 times and the
median is reported. At 32000 and 64000 each join is run once because it takes
minutes. Select and project use the median of 5 runs.

## Results (8.3): `R join[R.b=S.b] S`

| n | m | comparisons | wall time (s) | output tuples | time / comparison |
|---|---|---|---|---|---|
| 1000 | 1000 | 1,000,000 | 0.106996 | 1000 | 107.0 ns |
| 2000 | 2000 | 4,000,000 | 0.423060 | 2000 | 105.8 ns |
| 4000 | 4000 | 16,000,000 | 1.660555 | 4000 | 103.8 ns |
| 8000 | 8000 | 64,000,000 | 6.883813 | 8000 | 107.6 ns |
| 16000 | 16000 | 256,000,000 | 28.173209 | 16000 | 110.1 ns |
| 32000 | 32000 | 1,024,000,000 | 129.156523 | 32000 | 126.1 ns |
| 64000 | 64000 | 4,096,000,000 | 610.572866 | 64000 | 149.1 ns |

![log-log plot of wall time against n](results/loglog.png)

## Answers (8.4)

### 1. The relationship between n, m and the comparison count

**comparisons = n × m, exactly.** In all seven rows the measured counter
equals n·m to the last digit, from 1,000,000 at n = 1000 to 4,096,000,000 at
n = 64000. There is no discrepancy.

This is what the algorithm guarantees. The join is a nested loop: for each of
the n tuples of R, it walks all m tuples of S and evaluates `R.b = S.b` once
per pair. It never stops early, because a pair's match can't be known without
comparing it. The counter is incremented inside the inner loop, so it is
counted, not computed from n and m.

(n and m are the sizes *after* loading. The generator gives every tuple a
unique `a` or `c`, so nothing collapses as duplicates. If the input files had
duplicate tuples, they would collapse when loaded, and the count would be
the product of the deduplicated sizes.)

### 2. Log-log slope of time against n

The least-squares slope of log(time) against log(n) over all seven sizes is
**2.07** (`bench.py: slope()`). A slope of s on a log-log plot means
time ∝ nˢ. Here n = m, so the work is n·m = n², and the prediction is a
slope of exactly 2. The measurement is close to 2: each doubling of n
multiplies the time by 3.95, 3.93, 4.15, 4.09, 4.58 and 4.73. This is the
signature of a **quadratic nested-loop algorithm**. The number of
comparisons grows as n², and each comparison costs roughly the same.

The slope is a little **above** 2, and the table shows why. The cost per
comparison is flat, about 104–110 ns, from 1000 to 16000, where the slope is
2.01. It then rises to 126 ns at 32000 and 149 ns at 64000, where the slope
is 2.22. So the constant factor is not quite constant at the largest sizes.
Two explanations fit the data. We did not run the extra experiments needed to
separate them.

* **Memory hierarchy.** The inner loop walks the whole of S once per R tuple.
  S is a Python list of tuple objects, each pointing to separate int
  objects, so it grows to several megabytes at n = 64000. At small n it fits
  comfortably in the CPU caches. As it grows, more of each pass has to come
  from slower levels of memory, and each comparison waits a little longer.
* **Sustained load.** The 32000 and 64000 joins ran once each, for about 2 and
  10 minutes of continuous single-core work. Those long runs are more exposed
  to thermal limits and background activity than the short runs, which are
  also medians of 3.

Either way, it is the cost per comparison that changes, not the number of
comparisons. The counter stays exactly n² throughout.

### 3. `select` and `project` at the same sizes

| n | select evaluated | select time (s) | project time (s) | join time (s) |
|---|---|---|---|---|
| 1000 | 1000 | 0.000117 | 0.000638 | 0.106996 |
| 2000 | 2000 | 0.000216 | 0.001166 | 0.423060 |
| 4000 | 4000 | 0.000431 | 0.002380 | 1.660555 |
| 8000 | 8000 | 0.000917 | 0.005265 | 6.883813 |
| 16000 | 16000 | 0.001770 | 0.010505 | 28.173209 |
| 32000 | 32000 | 0.003668 | 0.021705 | 129.156523 |
| 64000 | 64000 | 0.009836 | 0.054802 | 610.572866 |

Both are **linear**, with log-log slopes of **1.05** (select) and **1.07**
(project), compared with 2.07 for the join. Doubling n roughly doubles
their time. The select counter equals n exactly, because the condition is
evaluated once per tuple.

Why they differ from the join: select and project each make **one pass**
over their single input and do a constant amount of work per tuple, so
their cost is O(n). The join does a full pass over S for every tuple of R,
so its cost is O(n·m). The gap therefore grows with n. At n = 64000, the join
takes 610.6 s and select takes 0.0098 s, a factor of about **62,000**. Most
of that factor is simply n² / n = n = 64,000.

`project` is about **5.6×** slower than `select` at the same n (0.0548 s
versus 0.0098 s at 64000, or 856 ns versus 154 ns per tuple). Select only
evaluates one comparison per tuple. Project builds a new tuple for every row,
then computes its equality key (`tuple_key` in `relation.py`) and looks it up
in the deduplication table. That is more work per tuple, but still a
constant amount, so the curve is still linear.

### 4. Predicting n = m = 1,000,000 (not run)

The join does n·m comparisons, so scale the largest measured run by the
growth in n²:

```
t(1,000,000) = t(64,000) × (1,000,000 / 64,000)²
             = 610.57 s × 15.625²
             = 610.57 s × 244.14
             = 149,066 s  ≈  41.4 hours
```

The same number from the cost per comparison: 610.57 s / 4.096 × 10⁹ =
149.1 ns per comparison. Multiplying by 10¹² comparisons gives
149,066 s ≈ **41.4 hours**.

**Why this is probably an underestimate.** Question 2 showed the cost per
comparison rising at the top of the range (110 → 126 → 149 ns). If it keeps
rising, the true time is higher. Using the fitted power law instead of an
exact square gives 610.57 s × 15.625^2.07 ≈ **50.5 hours**. So the
prediction is "roughly two days, at least 41 hours".

**A check we did on our own data.** Before the 64000 run finished, we
predicted it from the flat ~110 ns per comparison seen at 16000:
28.17 s × 4² ≈ 451 s (7.5 minutes). It took 611 s, 35% longer. This is the
same effect, and it is why we take the 64000 point, not a smaller one, as
the basis for extrapolation.

### 5. Does the match rate change the comparison count? The wall time?

n = m = 2000, varying the match rate:

| match rate | comparisons | wall time (s) | output tuples |
|---|---|---|---|
| 0.01 | 4,000,000 | 0.429759 | 15 |
| 0.1 | 4,000,000 | 0.425233 | 175 |
| 1 | 4,000,000 | 0.424591 | 2000 |
| 10 | 4,000,000 | 0.424234 | 20000 |
| 100 | 4,000,000 | 0.437013 | 200000 |
| 500 | 4,000,000 | 0.507971 | 1000000 |

**The comparison count does not change.** It is 4,000,000 = 2000 × 2000 at
every match rate. The nested loop compares every pair whether it matches or
not, so the count depends only on n and m.

**The wall time changes, but only when the output gets large.** From match
rate 0.01 to 10 (15 to 20,000 output tuples), the time stays flat at
0.424–0.430 s. The spread is about 1%, which is run-to-run noise. At rate 100
it rises 3%, and at rate 500 it rises to 0.508 s, **20% more** than at
rate 1.

The two answers differ because the counter and the clock measure different
things. The counter measures the comparisons, and those are fixed. Wall time
also includes producing the output. Every matching pair makes the join build
a new concatenated tuple (`l + r`) and append it to the output list.
Between rate 1 and rate 500, the extra 998,000 output tuples cost
0.0834 s, about **84 ns per output tuple**. That is comparable to the cost of
one comparison, but there are always 4,000,000 comparisons and at most
1,000,000 outputs. So output only becomes visible when it is a sizeable
fraction of the comparisons.

### 6. What it would take to make the million-tuple join feasible

The problem is the algorithm, not the language. Any nested loop does 10¹²
comparisons, so even an implementation 50× faster than ours would still take
most of an hour. The fix is to stop comparing every pair. For an equality
condition like `R.b = S.b`:

* A **hash join** makes one pass over S to build a hash table keyed on S.b,
  then one pass over R, looking up each R.b. The cost is O(n + m + output),
  about 2 × 10⁶ steps instead of 10¹². Our `project` already does comparable
  per-tuple work (build a key, look it up in a table) at about 0.86 µs per
  tuple. At that rate the two passes are about 2 × 10⁶ × 0.86 µs ≈ 1.7 s,
  plus the cost of building the 10⁶ output tuples. That is **seconds instead
  of about 41 hours**.
* A **sort-merge join**, O(n log n + m log m), or an **index nested-loop
  join** with a B⁺-tree on S.b would also work.

To use any of them, the engine would need to look at the join condition,
recognise that it is an equality between one attribute from each side, and
choose the algorithm accordingly. This is exactly what the later cost-model
project adds.
