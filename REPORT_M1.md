# Module 1 report: measurements

All numbers come from `python3 measure_m1.py` (after `python3 make_traces.py`).
Every number can be checked with the command line, for example
`python3 -m storage trace --file traces/t3_scan80x10.txt --frames 64 --policy lru --json`.
The counters are exact and the same on every run and every machine.

## 1. Capacity: records per page

By hand (section 3): a page has 4096 − 8 = 4088 bytes after the header, and
each record costs its own bytes plus a 4-byte slot. So

    records per page = floor(4088 / (record bytes + 4))

Record bytes: INT = 4; VARCHAR = 2-byte length + the string's UTF-8 bytes.
Measured = `slot_count` of page 1 after loading 3 pages' worth of rows.

| Schema | Record bytes | Computed per page | Measured on page 1 |
|---|---|---|---|
| all INT `a:INT,b:INT,c:INT` | 12 | floor(4088 / 16) = 255 | 255 |
| short VARCHAR `id:INT,name:VARCHAR(8)` | 14 | floor(4088 / 18) = 227 | 227 |
| VARCHAR(200) `id:INT,note:VARCHAR(200)` | 206 | floor(4088 / 210) = 19 | 19 |

**Difference: none.** Every row in each test has the same length (the
VARCHAR rows use the full declared length), so first fit packs each page
completely before opening the next one. The values would differ for rows
of mixed lengths: a VARCHAR is stored at its real length, not its declared
one, so short strings fit more rows per page than the formula with the
declared length predicts. The leftover bytes per page are 4088 − 255×16 = 8,
4088 − 227×18 = 2, and 4088 − 19×210 = 98: too small for one more record.

## 2. Policies: hit rate

Our five traces (DESIGN_M1.md section 8):

| Trace | Fetches | Distinct pages | What it tests |
|---|---|---|---|
| t1_worked | 6 | 4 | the §5.3 example: 1 2 3 1 4 2 |
| t2_hotcold | 1000 | 141 | 80% of fetches go to 20 hot pages, 20% to 180 cold ones |
| t3_scan80x10 | 800 | 80 | pages 0–79 in order, 10 times (§8) |
| t4_uniform | 1000 | 200 | every one of 200 pages equally likely |
| t5_hot_plus_scan | 1000 | 510 | a 10-page hot set mixed with a one-time scan of 500 pages |

**LRU**

| Trace | Fetches | B | Hits | Misses | Evictions | Hit rate |
|---|---|---|---|---|---|---|
| t1_worked | 6 | 4 | 2 | 4 | 0 | 0.333 |
| t1_worked | 6 | 16 | 2 | 4 | 0 | 0.333 |
| t1_worked | 6 | 64 | 2 | 4 | 0 | 0.333 |
| t1_worked | 6 | 256 | 2 | 4 | 0 | 0.333 |
| t2_hotcold | 1000 | 4 | 139 | 861 | 857 | 0.139 |
| t2_hotcold | 1000 | 16 | 485 | 515 | 499 | 0.485 |
| t2_hotcold | 1000 | 64 | 824 | 176 | 112 | 0.824 |
| t2_hotcold | 1000 | 256 | 859 | 141 | 0 | 0.859 |
| t3_scan80x10 | 800 | 4 | 0 | 800 | 796 | 0.000 |
| t3_scan80x10 | 800 | 16 | 0 | 800 | 784 | 0.000 |
| t3_scan80x10 | 800 | 64 | 0 | 800 | 736 | 0.000 |
| t3_scan80x10 | 800 | 256 | 720 | 80 | 0 | 0.900 |
| t4_uniform | 1000 | 4 | 25 | 975 | 971 | 0.025 |
| t4_uniform | 1000 | 16 | 90 | 910 | 894 | 0.090 |
| t4_uniform | 1000 | 64 | 315 | 685 | 621 | 0.315 |
| t4_uniform | 1000 | 256 | 800 | 200 | 0 | 0.800 |
| t5_hot_plus_scan | 1000 | 4 | 0 | 1000 | 996 | 0.000 |
| t5_hot_plus_scan | 1000 | 16 | 0 | 1000 | 984 | 0.000 |
| t5_hot_plus_scan | 1000 | 64 | 490 | 510 | 446 | 0.490 |
| t5_hot_plus_scan | 1000 | 256 | 490 | 510 | 254 | 0.490 |

**CLOCK**

| Trace | Fetches | B | Hits | Misses | Evictions | Hit rate |
|---|---|---|---|---|---|---|
| t1_worked | 6 | 4 | 2 | 4 | 0 | 0.333 |
| t1_worked | 6 | 16 | 2 | 4 | 0 | 0.333 |
| t1_worked | 6 | 64 | 2 | 4 | 0 | 0.333 |
| t1_worked | 6 | 256 | 2 | 4 | 0 | 0.333 |
| t2_hotcold | 1000 | 4 | 141 | 859 | 855 | 0.141 |
| t2_hotcold | 1000 | 16 | 470 | 530 | 514 | 0.470 |
| t2_hotcold | 1000 | 64 | 814 | 186 | 122 | 0.814 |
| t2_hotcold | 1000 | 256 | 859 | 141 | 0 | 0.859 |
| t3_scan80x10 | 800 | 4 | 0 | 800 | 796 | 0.000 |
| t3_scan80x10 | 800 | 16 | 0 | 800 | 784 | 0.000 |
| t3_scan80x10 | 800 | 64 | 0 | 800 | 736 | 0.000 |
| t3_scan80x10 | 800 | 256 | 720 | 80 | 0 | 0.900 |
| t4_uniform | 1000 | 4 | 25 | 975 | 971 | 0.025 |
| t4_uniform | 1000 | 16 | 92 | 908 | 892 | 0.092 |
| t4_uniform | 1000 | 64 | 305 | 695 | 631 | 0.305 |
| t4_uniform | 1000 | 256 | 800 | 200 | 0 | 0.800 |
| t5_hot_plus_scan | 1000 | 4 | 0 | 1000 | 996 | 0.000 |
| t5_hot_plus_scan | 1000 | 16 | 0 | 1000 | 984 | 0.000 |
| t5_hot_plus_scan | 1000 | 64 | 479 | 521 | 457 | 0.479 |
| t5_hot_plus_scan | 1000 | 256 | 483 | 517 | 261 | 0.483 |

### Trace 3 under LRU at B = 64: hit rate 0 (0 hits out of 800)

**Mechanism.** The scan touches 80 pages in a fixed order, but only 64 fit,
so each new page evicts the least recently used one, which is always the
page the scan will need again soonest. By the time the scan comes back to
page 0 it was thrown out 16 fetches earlier, and the same is true for every
page after it, so every fetch is a miss (this is called sequential flooding).

CLOCK does no better here: every page is used once per pass, so all
reference bits are set, the hand clears them all, and CLOCK acts like FIFO,
which is the same as LRU on this trace. Only at B = 256, when all 80 pages
fit, do the last 9 passes hit (720 hits, 80 misses).

### Which policy we would ship: CLOCK

- **Hit rates are almost the same.** In all 20 rows the two policies are
  within 1.5 percentage points of each other. LRU is slightly ahead on t2
  (B = 16, 64), t4 (B = 64) and t5 (B = 64, 256); CLOCK is slightly ahead on
  t2 (B = 4) and t4 (B = 16).
- **CLOCK is cheaper on every hit.** A hit under CLOCK just sets one bit.
  Under LRU it moves the frame to the end of a list, and in a real
  multi-threaded system that list needs a lock on every single page access.
- **Neither one handles scans.** t3 and t5 show that both policies lose
  their useful pages when a big scan runs through the pool. The fix is not
  a better general policy but a separate small ring of frames for
  sequential scans; that is out of scope for Module 1.

## 3. Scan cost: a 500-page table

Table `Big(a INT, b INT, c INT)` with 500 × 255 = 127,500 rows: 1 directory
page and 500 data pages, 501 pages in all. Each run uses a fresh pool of B frames.

| B | Rows | Hits | Misses | Evictions | Reads | Writes |
|---|---|---|---|---|---|---|
| 2 | 127500 | 0 | 501 | 499 | 501 | 0 |
| 8 | 127500 | 0 | 501 | 493 | 501 | 0 |
| 64 | 127500 | 0 | 501 | 437 | 501 | 0 |

**Reads are 501 for every B,** that is the 500 data pages plus the one
directory page. The number does not fall as B grows because a scan reads
each page exactly once and never comes back, so a bigger pool has nothing
to reuse; it only means fewer evictions (501 − B, because the last B pages
read are still in the pool when the scan ends). The directory page is fetched
once and stays pinned while its 500 data pages are read, so it is never
re-read. Writes are 0 because a scan only reads. B = 2 is enough: the scan
never has more than 2 pages pinned (one directory page and one data page).
