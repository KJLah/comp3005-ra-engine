# Design log

One short entry per working session: what I was trying to do, what I tried,
and what broke. At least three entries must describe a time AI assistance was
wrong, slow or incomplete: what the problem was and how I found it.

---

## 2026-09-28: grammar, tokenizer, parser

**Goal:** Decide the language, write GRAMMAR.md, and get test cases 1–17 passing.

**Decisions:**
- Precedence: unary/parentheses > times/join > intersect > union/minus. All
  binary operators are left-associative.
- Case 8 (`select[union=3](R)`): operator words are soft keywords for
  attribute names and reserved for relation names. `and`/`or`/`not` are
  reserved everywhere.
- The lexer has a "value mode" inside `{ }`, so bare values like `John` and
  `D1` work in relation files.

**What I tried / what broke:**
<!-- TODO: in your own words. For example: what you ran, what failed, what you changed. -->

**AI was wrong / slow / incomplete:**
- **It invented log entries.** The AI's first draft of GRAMMAR.md 5.5
  listed example "AI was wrong" incidents that had not happened. The AI
  flagged this itself when it reviewed the draft, and removed them before
  handing it over. From then on, I check every entry against what actually
  happened.
- **It made an unverified claim.** The first draft said our precedence
  matched "Relax and most textbooks". Nobody had checked Relax, so the claim
  was removed. GRAMMAR.md 5.2 now rests only on the arithmetic analogy.

---

## 2026-09-28 (later): operators, errors, generator

**Goal:** cases 18–25, all five error categories, data generator, counters.

**Decisions:**
- Two phases: `plan()` does all name, schema and type checks without
  touching tuples, and `execute()` runs bottom-up.
- Tuple equality is written down in `relation.py` (`value_key`). A number
  never equals a string, and 32 equals 32.0. All deduplication goes through it.
- `project[Name, Name]` is a schema error.
- Conditions become closures over (left_row, right_row), so the join does not
  build `l + r` just to test a pair.
- Generator: key space K = round(m / k), S.b = i mod K, and R.b is uniform in
  0..K-1. The expected join size is n·k.

**What I tried / what broke:**
<!-- TODO -->

**AI was wrong / slow / incomplete:**
<!-- TODO: add one if something here went wrong for you. -->

---

## 2026-09-28 (night): performance study on my Mac

**Goal:** run `bench.py` on my own machine and fill REPORT.md.

**What I tried / what broke:**
- I ran the full benchmark on my Mac (macOS 15.5, arm64, CPython 3.11.7).
  The 64000 join ran once and printed nothing for about 10 minutes, so it
  looked frozen. It was still running.
- Every comparison count was exactly n·m. Up to 16000 the cost per
  comparison was flat at about 104–110 ns, but it rose to 126 ns at 32000
  and 149 ns at 64000. So the log-log slope is 2.07, not exactly 2.

**AI was wrong / slow / incomplete:**
- **Its runtime prediction was wrong.** From the 16000 run, the AI
  predicted the 64000 join would take about 7.5 minutes (110 ns × 4.096 ×
  10⁹). It took 610.6 s (10.2 minutes), 35% longer. The prediction assumed a
  constant cost per comparison, and my measurements show it is not constant.
  I found this by running it. As a result, REPORT.md extrapolates the
  million-tuple join from the largest size and calls 41.4 h a lower bound.

---

## 2026-09-29: GitHub and trying the engine myself

**Goal:** push to GitHub and run queries by hand.

**What I tried / what broke:**
- `git push` succeeded, but the repo looked empty on GitHub. It is private,
  and I was looking at a page from before the push. Refreshing while signed
  in showed the files. I still need to make it visible to the professor.
- I checked the engine against the worked examples in Lecture 02. They are
  now 23 tests in `tests/test_lecture_examples.py`, and all of them pass. One
  real difference turned up: the set-operator slide intersects a relation
  with a `Dept` column and one with `Depar`, which the lecture treats as
  compatible. The assignment (4.3) requires identical names, so our engine
  rejects it. I kept the assignment's rule and documented the difference in
  README.md.
- <!-- TODO: add what you saw when you ran the queries and the error cases. -->

**AI was wrong / slow / incomplete:**
- **Its instructions were incomplete.** The AI's checklist said to run
  `python3 ra.py -d data/employees.ra` but did not say to `cd` into the
  project folder first. From my home folder it failed with "can't open file
  '/Users/khadija/ra.py'". I found it by running the command. The `cd` step
  is now the first item in the checklist.
- **Its UI text was misleading.** The shell banner said "type '.tree
  <query>'". I typed `<query>` literally and got "Syntax error at line 1,
  column 1". The error handling worked, but the banner read like literal
  text. It now shows a concrete example (`ra.py`, the `repl()` banner).
- **Its code printed a stack trace.** While testing the new banner, the
  output was piped into `head`. When `head` closed the pipe early, Python
  raised `BrokenPipeError`, and `ra.py` printed a full traceback. That breaks
  the "never show a stack trace" rule (section 6.3). The AI hit this during
  its own test run, not me. `main()` now catches `BrokenPipeError` and exits
  quietly, and `test_no_traceback_when_output_is_cut_off` checks it.

  ---

## 2026-11-08: prepare the engine as a reusable library

**Goal:** Complete the section 1.3 week-one preparation for Project 2.

**Changes made:**
- Kept the tokenizer, parser, AST and operators as separately importable
  modules. Moved AST-to-operator planning into `planner.py`. File access
  is handled by callers rather than the core library.
- Exposed `parse_condition(text)` for standalone conditions. It rejects
  trailing tokens. Added `predicate_for_row()` for evaluating a condition
  against one tuple while retaining the existing two-tuple compiler
  interface for joins.
- Retained qualified attribute resolution in conditions and projection.
  Bare references remain available for Bonus 1 compatibility and must
  resolve unambiguously.
- Added versioned JSON serialization and deserialization for AST nodes,
  including source positions, qualifiers and nested expressions.
- Introduced a tuple-source contract with schema metadata and `scan()`.
  Existing in-memory relations implement it, and scans no longer depend
  directly on a `.rows` list.
- Changed input loading and projection to preserve duplicate tuples.
  Added a separate `Distinct` AST node and execution operator, exposed
  through `distinct(expr)`.
- Kept union, intersection and difference distinct-valued even when
  their inputs contain duplicate tuples.

**What broke and how it was fixed:**
- An `Expr` union referenced classes before their definitions, causing
  a NameError during import. Removed the early definition and kept one
  union after the expression classes, including `Distinct`.
- `Distinct` existed as a node and operator but was not fully connected.
  Registered the keyword and added parser and planner handling.
- The tree printer's `Distinct` branch was after an unconditional
  assertion and could not run. Moved it before the assertion and added
  the missing import.
- The new library tests imported `plan` and `evaluate` from their old
  location. Updated them to import from `ra_engine.planner`.
- Intersection and difference filtered left tuples without removing
  duplicates. Wrapped their results in the existing `distinct()` helper
  to preserve their set semantics with bag-valued inputs.
- Some tests still expected implicit duplicate removal. Updated the
  expectations where bag semantics deliberately changed the result.

**Changed test meanings:**
- Required Bonus 1 case 23 now expects D1 twice and D2 once after
  projecting Employees.DID. A separate explicit-distinct test checks
  the original two-department result.
- Case 24 remains unchanged: duplicate column references are still
  invalid, independently of duplicate tuple values.
- The input-duplicate test now expects all five source tuples.
- The lecture course-name projection now expects three Alex tuples
  and one John tuple.
- Division retains its set result through the distinct-valued set
  operators.

**Verification:**
- Ran `python3 -m unittest discover -s tests -v` locally.
- Result: all 107 tests passed in 0.910 seconds.
- Coverage includes the original numbered cases with revised bag
  expectations, standalone conditions, qualified names, layer checks,
  JSON round trips, external tuple sources, bag joins, explicit
  duplicate removal and tree printing.

**Remaining execution limitation:**
- Tuple sources are independent of in-memory lists, but execution still
  materializes inputs and intermediate results. Module 2's iterator
  interface is future work.

**AI was wrong / slow / incomplete:**
- **It overclaimed.** It said moving `plan()` into `planner.py` made the
  operators independent of the parse tree. They still receive condition
  nodes from `ast_nodes` through `conditions.py`. `test_layers.py` only
  checks direct imports, so it passed anyway.
- **A key step was only described in words.** The fixes file gave
  `qualify()` as code but described "rename plan to _plan and wrap it" in
  one sentence. I added `qualify()` but nothing called it, so bare names
  still reached the plan and the JSON.
- **Its own code had a bug.** Its first `qualify()` resolved names before
  checking for duplicate columns, so `Emp join[Emp.MgrID=Emp.EID] Emp`
  would have raised a Name error ("ambiguous") instead of the Schema error
  that test 20b expects. I found this when re-checking the code
  before handing over the final `planner.py`. The fix runs
  `check_no_collisions` before qualifying the join condition.

  ---

## 2026-11-09: Module 1 step 1, records and the slotted page

**Goal:** implement record encoding (section 2) and the slotted page
(section 3), and reproduce the 3.2 worked example exactly.

**Decisions:**
- New package `storage/` next to `ra_engine/`: `errors.py`, `record.py`,
  `page.py`. `SlottedPage` works directly on a 4096-byte bytearray, so a
  buffer-pool frame can be used as a page without copying.
- **Largest record is 4084 bytes, not 4088.** Section 3.3 says a record
  longer than 4088 bytes cannot be stored, but every insert also needs a
  4-byte slot. On an empty page, free space is 4088, so the largest record
  that fits is 4088 − 4 = 4084 bytes. Records of 4085–4088 bytes pass the
  handout's rule but still do not fit. We reject anything over 4084 and
  explain this in DESIGN_M1.md. `test_largest_record_fits_and_one_more_byte_does_not`
  checks both sides.

**AI was wrong / slow / incomplete:**
- **It misjudged a field size.** The AI's first version of `page.py`
  claimed that `free_end`, a uint16, could not hold 4096 (the value on an
  empty page), and stored 0 as a stand-in. That was wrong: a uint16 holds
  values up to 65535. Left in, it would have made our page bytes differ
  from the handout's format and from the grader's files. The
  mistake was found before handing over the code and removed the workaround.
  `test_empty_page_header_bytes` now checks that an empty page starts with
  `01 00 00 00 00 00 00 10` (page_id 1, slot_count 0, free_end 4096).

**What I tried / what broke:**
- After adding step 2 the suite still showed 120 tests. `test_m1_buffer.py`
  had not been created in `tests/`, so there were no new tests to find.
- Once the test file was added, it failed to import:
  `ImportError: cannot import name 'BufferPool' from 'storage.buffer'`.
  I had pasted the disk manager code into `storage/buffer.py` by mistake
  and only changed its first line. Replacing the whole file with the
  buffer pool code fixed it.
- VS Code also reported "ENOSPC: no space left on device" when saving.
  My Mac's disk was full, so some edits were not being saved. I freed
  space and saved again.
- I had also saved DESIGN_M1.md inside `tests/` instead of the repo root,
  and moved it.
- Final run: 134 tests, OK.

--

## 2026-11-09: Module 1 step 2, disk manager and buffer pool

**Goal:** read and write 4096-byte pages through a buffer pool with LRU and
CLOCK, and reproduce the 5.3 trace exactly.

**Decisions:**
- Pages are keyed by (file, page id), because catalog.db and every table share one pool.
- Empty frames are filled first, lowest index first, before asking the policy for a victim.
- `new_page()` counts as no hit, miss, read or eviction; its write is counted when flushed or evicted.
- CLOCK advances the hand past every newly loaded page, including ones placed in empty frames. This is what reproduces 5.3.
- Result: LRU 1 hit / 5 misses / 2 evictions, CLOCK 2 / 4 / 1 at B = 3.

---

## 2026-11-09: Module 1 step 3, heap file

**Goal:** directory pages, first-fit insert, get, delete, and a scan that never pins more than one directory page and one data page.

**Decisions:**
- First fit, in directory order, using the free_bytes stored in each directory entry.
- No compaction: a delete marks the slot dead and the space is not reused, so rids never move.
- New pages (data or directory) are always appended at the end of the file.
- `scan()` is a generator with `finally` blocks, so stopping early still unpins.

---

## 2026-11-09: Module 1 step 4, catalog

**Goal:** store table metadata in catalog.db, read through the buffer pool.

**Decisions:**
- catalog.db is itself a heap file; each table is one record.
- Record has a version byte and a trailing JSON statistics field (`{}` now) so Module 4 can add stats without changing the format.
- `update()` deletes and reinserts the record.

---

## 2026-11-09: Module 1 step 5, CSV loader

**Goal:** load a CSV file into a table, with errors naming the line and column.

**Decisions:**
- Hand-written parser (no csv module), quoted fields and `""` supported.
- All or nothing: the whole file is checked before anything is inserted.
- VARCHAR length counted in UTF-8 bytes; INT may have surrounding spaces.

---

## 2026-11-09: Module 1 step 6, command line

**Goal:** `create-table`, `load`, `scan`, `layout`, `trace` with the exact JSON shapes.

**Decisions:**
- `scan --frames B` uses a fresh pool for the table only; the catalog gets its own 4-frame pool, so scan counters count only table pages.
- `scan` requires B ≥ 2.
- Errors print one line (`Error: ...`, exit 1), never a traceback.

---

## 2026-11-09: Module 1 step 7, measurements

**Goal:** fill REPORT_M1.md: capacity table, 5 traces × 2 policies × 4 frame sizes, 500-page scan at B = 2, 8, 64.

**Decisions:**
- Wrote our own five traces (the published ones were not released; professor's reply 2026-11-08).
- Random traces use a fixed LCG so every number is the same on every machine.
- We would ship CLOCK: hit rates within 1.5 points of LRU, and cheaper per hit.

---

## 2026-11-09: Module 1 step 8, querying stored tables with the engine

**Goal:** let the relational algebra engine from Bonus 1 query tables
stored by Module 1, so `project[b](select[a > 1](R))` reads `R.db`
through the buffer pool.

**Decisions:**
- New file `storage/source.py` with `HeapSource`, which implements the
  engine's `TupleSource` (name, attributes, `scan()`). `sources(catalog)`
  returns every table in the catalog as the dict that `plan()` expects.
- INT becomes the engine's number type and VARCHAR becomes string. The
  qualifier is the table name, so `R.a` works the same as for a relation
  loaded from a `.ra` file.
- `storage` imports `ra_engine`, never the other way round, so the engine
  still works without any storage.
- Every call to `scan()` starts a new heap scan instead of handing out
  one iterator that gets used up.

**AI was wrong / slow / incomplete:**
- **It gave the wrong reason for a decision.** In DESIGN_M1 section 11,
  the AI explained the "new heap scan on every `scan()`" decision by
  saying that a join reads its right side again for every row of the
  left side (a nested-loop join that re-reads its inner input). That is
  not how our engine works. I checked `JoinOp.execute()` in
  `ra_engine/operators.py`: it calls `self.left.execute()` and
  `self.right.execute()` once each, keeps both results in lists, and
  joins the lists in memory. Each table is scanned once per join.
- **How we fixed it:** the code was correct, so only the explanation
  changed. Decision 4 in section 11 now gives the real reasons:
  (1) the same table can appear twice in one query, for example a
  self-join with rename, which needs two separate scans of the same
  file, and (2) the same plan can be evaluated more than once and must
  return the same rows. If `scan()` returned a single iterator, the
  second scan would come back empty.
  `test_scan_returns_rows_and_can_run_twice` checks this.
- **Lesson:** a design reason has to match our own code, not how
  databases usually work. I now check claims like this against the
  source file before pasting them into the design document.

**What I tried / what broke:**
- At first my step 7 push was missing the 5 files in `traces/`. They were
  created by `make_traces.py` but not committed. I committed them with
  this step.
- Section 8 of DESIGN_M1 still had the old table with the ⏳ rows above
  the new one. I deleted it.
- Final run: 188 tests, OK.

---

## 2026-11-09: Module 1 failure recovery and scan-limit fixes

**Problems found in the AI-assisted implementation:**
- Failed page reads could leave the buffer pool's page table inconsistent.
- Failed allocation consumed a page ID before checking frame availability.
- Failed heap insertion left a directory page pinned.
- Limited scans requested an extra record and could read another page.
- The CLI collected the full scan result instead of streaming rows.
- CSV field-count errors omitted the column, and extremely long integers
  could raise ValueError instead of a contextual LoadError.

**Fixes applied:**
- Kept the old page mapping until the replacement page was available.
- Obtained a frame before allocating a page ID.
- Added finally-based cleanup for pins acquired by heap insertion.
- Applied scan limits before requesting another record and streamed output.
- Added column context to field-count errors and bounded integer conversion.

**Why the previous tests missed these problems:**
- They checked that exceptions occurred without checking recovery afterward.
- Limit tests checked returned row counts without checking page reads.
- CSV tests did not include extremely long integer strings.

**Verification:**
- Ran `python3 -m unittest discover -s tests -v`.
- All 194 tests passed, including the six new regression tests.
- Ran `python3 measure_m1.py`. All measurements matched REPORT_M1.md:
  capacities were 255, 227 and 19 records per page; both policy tables
  were unchanged; scanning 127,500 rows across 500 data pages plus one
  directory page required 501 reads at B = 2, 8 and 64, with zero writes.
- Installed the package with `python3 -m pip install -e .`.
- Verified that `storage --help` lists create-table, load, scan,
  layout and trace.

  ---

## 2026-11-11: deletion validation and failed-delete cleanup

**Problems found:**
- Failed deletion marked an unchanged page dirty, causing an extra write.
- Later directory pages passed the page-number range check and could
  be modified as though they were record pages.
- Negative slot numbers were accepted.

**Fixes:**
- Mark the data page dirty only after deletion succeeds.
- Locate the target's data-page directory entry before modifying it.
- Reject negative slot numbers.
- Add regression tests checking unchanged data, write counts and pin cleanup.

**Verification:**
- Full-suite command: python3 -m unittest discover -s tests -v
- Actual result: [paste your observed result]
- Measurement command: python3 measure_m1.py
- Actual result: [state whether it matches REPORT_M1.md]

## 2026-11-12: Module 2 step 1, foundations

**Goal:** the pieces every operator is built on: the iterator base class, schemas and comparisons, plan validation, the frame budget, counter attribution and temporary files.

**Decisions:**
- Working memory is pinned buffer pool frames; the pool has exactly B frames, so exceeding B is refused by the pool itself. `peak_pinned` is the evidence.
- I/O is attributed per operator through its own disk handle (`OwnedDisk`), not by diffing the pool's global counters.
- Temporary pages are written through and dropped from the pool, so measured I/O matches the formulas instead of benefiting from caching.
- Our own deterministic hash (FNV-1a), because Python's string hash changes between runs.
- Conditions reuse the Bonus 1 parser and compiler, so INT vs VARCHAR errors are identical to week one.

**AI was wrong:** the base class was first written as `execution/operator.py`. Compiling from inside the package directory failed with a circular import, because that file shadows Python's standard `operator` module. Renamed to `base.py`.

---

## 2026-11-12: Module 2 step 2, pipelined operators and the CLI

**Goal:** seqscan, filter, project, limit, union_all, and `exec run` / `exec trace`, so every later operator can be run end to end.

**Decisions:**
- seqscan walks the directory itself so it pins one page at a time (Module 1's heap scan pins two).
- limit closes its child after the n-th tuple, so it stops pulling and returns the child's frames.
- `run --json` totals: tuples_out of the root, other counters summed over all operators.

**AI was wrong (found by the first test run):** step 1 read the catalog through the plan's own pool of B frames. Module 1's heap scan pins 2 pages, so a plain seqscan showed `peak_pinned = 2` and B = 1 failed at bind with "no unpinned frame available". The catalog now has its own 2-frame pool; metadata lookups happen before the plan runs and never touch B. After the fix all 229 tests pass (Module 1, Bonus 1 and Module 2).

---

## 2026-11-12: Module 2 step 3, external sort

**Goal:** an external merge sort that reproduces the 2.5 example, matches the pass formula, and sorts 20 × B pages at B = 4.

**Decisions:**
- F (the sort's B) is the frames free after its child is open, fixed for the whole sort.
- A last group of one run is carried forward, not rewritten: 22 reads / 14 writes on the 2.5 example (23 with the heap's directory page).
- Merging stops when the runs left fit the frames the parent allows; a sort left 1 frame merges down to a single run.
- An input that fits in one run is never written.
- `ExternalSorter` is shared with distinct, group_by, the set operators and sort-merge join.

**Rebased onto main d6bc8b1** (heap delete fix, `page.get` bounds check): no Module 2 change needed; all 242 tests pass.

---

## 2026-11-12: Module 2 step 4, distinct, group_by, set operators

**Goal:** distinct and group_by in sort and hash modes, and union, intersect, except, all working beyond memory and at B = 4.

**Decisions:**
- distinct is group_by on every column with no aggregates; one implementation.
- Hash mode spills a partially aggregated table, then partitions state records, so raw tuples and partial results can share a partition.
- Set operators are sort-based.
- An operator whose parent leaves it too few frames to stream materialises its output to a temp file.

**AI was wrong (caught by the tests):**
- The set operators failed at B = 4 with "25 runs to merge but only 2 frames". The right side's sort got F = 2 (the left side held 1 frame, the right seqscan 1), and merging needs 3. Fix: when pass 0 had fewer than 3 frames, the sort takes back the frame its child releases for the merge passes.
- Hash distinct at B = 4 hit the sort fallback with only 2 frames, and a partition full of duplicates was re-partitioned needlessly because the code decided by page count. Fix: try folding a partition first (duplicates collapse), and give the fallback sort the reading frame back for merging.

---

## 2026-11-12: Module 2 step 5, block nested loop join

**Goal:** a block nested loop join with exactly |R| × |S| comparisons and page reads M + ceil(M/(B−2)) × N.

**Decisions:**
- K = B − 2 outer frames; the "output frame" reads the outer input, since output goes through `next()`.
- A pipelined inner is reopened per block; any other inner is materialised once.
- Measured reads match the formula plus one directory page per scan, with no cache hits at any B tested.

---

## 2026-11-12: Module 2 step 6, sort-merge join

**Goal:** a sort-merge join that handles duplicate keys on both sides and equal-key runs larger than memory.

**Decisions:**
- Both sorted inputs are merged down to one run each, so the merge uses exactly 3 frames: left run, right run, equal-key run.
- An equal-key run over one page spills to a temp file and is replayed per left tuple.
- The 2.8 example returns 4 rows; results match nested loop at B = 4, 5, 8, 16, 64.

---

## 2026-11-12: Module 2 step 7, Grace hash join

**Goal:** a Grace hash join with P = B − 1 partitions, required overflow handling, and correct results under heavy skew.

**Decisions:**
- Overflow: re-partition with a new seed up to 3 levels, then block nested loop on that pair. A sub-partition that did not shrink skips straight to the fallback.
- A partition pair with an empty side is skipped unread.

**AI was wrong (caught by the tests):** the first cost test expected probe reads to equal partition writes exactly. They were lower (78 vs 85) because pairs with an empty side are never read. That is correct behaviour; the test now checks reads against the pairs actually probed.

---

## 2026-11-12: Module 2 steps 8 and 9, measurements, docs, checklist

**Goal:** every §5 measurement, the full DESIGN_M2.md, and one test per §6 checklist line.

**Decisions:**
- `measure_m2.py` runs every measurement through the same code as `exec run`; numbers are counters, so they are identical on any machine (plots need matplotlib).
- `plans/` holds one sample plan per operator plus a 7-operator pipeline; the checklist tests run all of them through the CLI at B = 4, on a full and an empty database.
- Hash join keeps the handout's P = B − 1 even when that is wasteful (M = 16 at B = 256 writes 275 pages), so the report tests the handout's formula; the better choice of P is explained in the report instead.

**Result:** 287 tests pass (Module 1, Bonus 1 and Module 2).

---

## 2026-11-13: Module 2 review of nested plans, memory and counters

**Problems found in the AI-assisted implementation:**
- The original 287 tests passed, but several right-nested joins failed
  at B = 4.
- Hash join retained idle partition reservations while opening its
  right subtree.
- Sort-merge join retained its left streaming reservation while a
  blocking right subtree needed that memory.
- Nested-loop comparison counters charged a whole block before its
  conditions were evaluated. LIMIT could stop before those comparisons
  happened. The hash-join fallback had the same issue.
- Grouping retained a completed partition table while building another.
  A diagnostic run retained 19,910 bytes of encoded tuple payload and
  slots against a four-frame budget of 16,384 bytes.
- The skew generator used a probability of 0.4 rather than producing
  exactly 40% hot-key rows.
- The report incorrectly described an eight-page input as one page and
  overstated what pinned-frame counters prove about total memory.

**Additional issue found during follow-up review:**
- The join-specific fixes worked, but set operators still retained
  their left streaming reservation while opening a blocking right
  subtree. Valid union, intersect and except plans could fail at B = 4.
- Applied the same parking strategy to set operators and added one
  regression test covering all nine combinations with right-side joins.

**Fixes applied:**
- Release idle hash partition reservations during right-child open.
- Park the completed left sort result when needed for a nested right
  subtree at a small budget.
- Count each comparison immediately before it is evaluated.
- Clear each completed grouping partition table in a finally block.
- Generate exact 40% skew with equal-sized uniform comparison inputs.
- Correct the report and distinguish pinned-frame measurements from
  accounting for retained Python collections.

**Verification:**
- Full-suite command: python3 -m unittest discover -s tests -v
- Actual result: all 292 tests passed.
- Added tests for nested joins, partial-execution counters and
  partition-table lifetime.
- Measurement command: python3 measure_m2.py
- Updated skew results: uniform produced 755 rows with 207 reads and
  109 writes; exact 40% skew produced 117,892 rows with 366 reads and
  213 writes.

