Database: gen_m4.py (seed 3005), B = 8 unless stated.

## 1. Estimation quality

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

## 2. Optimized against unoptimized

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

## 3. Rule ablation (one rule off at a time)

| rule off | total I/O, all rules | total I/O without it | difference | comparisons, all rules | comparisons without it | I/O change per query |
|---|---|---|---|---|---|---|
| 1 | 1430 | 1618 | 188 | 4,447,689 | 6,484,088 | q10 160->348 |
| 2 | 1430 | 1874 | 444 | 4,447,689 | 17,830,248 | q09 164->339; q10 160->434; q17 250->245 |
| 3 | 1430 | 1430 | 0 | 4,447,689 | 4,454,645 | none |
| 4 | 1430 | 1430 | 0 | 4,447,689 | 4,447,689 | none |
| 5 | 1430 | 1430 | 0 | 4,447,689 | 4,461,926 | none |
| 6 | 1430 | 1786 | 356 | 4,447,689 | 4,452,610 | q09 164->280; q10 160->174; q11 48->226; q12 48->68; q19 50->78 |
| 7 | 1430 | 1430 | 0 | 4,447,689 | 4,450,780 | none |

## 4. Correlation

| B | q17 est rows | actual | join | est cost | actual I/O | city only: est rows | actual | join | est cost | actual I/O |
|---|---|---|---|---|---|---|---|---|---|---|
| 4 | 83 | 407 | BNL | 147 | 452 | 407 | 407 | hash | 183 | 271 |
| 6 | 83 | 407 | BNL | 147 | 250 | 407 | 407 | hash | 183 | 211 |
| 8 | 83 | 407 | BNL | 147 | 250 | 407 | 407 | BNL | 147 | 250 |
| 32 | 83 | 407 | BNL | 147 | 149 | 407 | 407 | BNL | 147 | 149 |

q18 filter: est 174, actual 407.


## 5. Join algorithms chosen at each B

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

## 6. DP table, q10 (four relations), B = 8

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
