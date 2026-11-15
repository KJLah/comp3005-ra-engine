# Design Log

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

---

## 2026-11-13: Module 3 plan, starting point

**Goal:** check main before building the SQL front end.

**State:** main at 4118bc0, 292 tests pass. The instructor confirmed no logical
plan schema or sample catalog will be published; names come from the Module 3
examples and the Module 2 document's Section 2, and open points are decided in
DESIGN_M3.md.

**Found while planning:** the Bonus 1 condition parser, which the Module 2
executor uses for every filter and join condition, rejected both `<>` and
upper-case `AND` (`parse_condition("a <> 'x'")` failed on main). The first
plan printed SQL conditions with upper-case keywords; that would have produced
plans the executor cannot load. Conditions are now printed with lower-case
`and`/`or`/`not`, and the Bonus 1 lexer reads `<>` as `!=` (step 6).

---

## 2026-11-13: Module 3 step 1, the SQL scanner

**Goal:** a hand-written scanner for Section 1: case-insensitive keywords,
case-sensitive identifiers, `''` escapes, signed integers, maximal munch for
`<=`, `>=`, `<>`, `--` comments, several statements per file.

**Decisions:**
- Character classes are imported from the Bonus 1 lexer, so both front ends
  agree on letters and digits.
- A sign is part of a number only when a digit follows at once; there is no
  arithmetic, so a lone `-` is a lexical error with a hint.
- Aggregate names are keywords (GRAMMAR_SQL.md §4.8).
- Tokens keep their source text and their value (keyword in upper case).

**Verification:** 19 tests, including a check that `sql/` uses no regular
expressions, `eval` or `exec`.

---

## 2026-11-13: Module 3 step 2, grammar and parser

**Goal:** GRAMMAR_SQL.md and a recursive-descent parser with one method per
rule, precedence and associativity enforced by the grammar's shape.

**Decisions:**
- condition -> and_cond -> not_cond -> atom gives NOT > AND > OR; each `{ }`
  loop folds left, which also makes the set operators left associative at
  one level.
- Differences from the sketch, each documented in GRAMMAR_SQL.md §4: AS
  optional on table aliases (the handout's own examples omit it), aggregates
  allowed as condition operands (so HAVING COUNT(*) > 5 parses and an
  aggregate in WHERE is an aggregate error, not a syntax error).
- `printer.format_condition` prints a condition with the fewest parentheses
  that keep its tree; a test parses every printed condition back to the same
  tree.

**AI was wrong:** one syntax-error test expected column 13 for the `FROM` in
`SELECT count FROM R`; the token is at column 14. The parser was right, the
test's hand-counted column was not.

**Verification:** 27 tests.

---

## 2026-11-13: Module 3 step 3, logical plans

**Goal:** plan node constructors with the handout's field names, an explain
tree, and a validator for the checklist rules.

**Decisions:**
- Plans are plain dicts built by constructors, so key order (and printed JSON)
  matches the handout.
- `explain` prints one node per line with its op name, so union and union_all
  differ visibly (§5.7).
- `check_plan` rejects physical operator names, aggregate calls in
  conditions, and unqualified names except output names created below the
  node (aggregate outputs, select-list aliases). Condition strings are checked
  by parsing them with the Module 3 condition parser.

**Verification:** 11 tests.

---

## 2026-11-13: Module 3 step 4, scopes and name resolution

**Goal:** Section 3: scope from FROM, duplicate aliases, qualified and
unqualified lookup, SELECT * expansion, ORDER BY aliases first, subqueries.

**Decisions:**
- Resolution happens while translating (`translate.py` calls `resolve.py`),
  so the translator was written in this step; steps 5 and 6 test its rules
  and its plan shapes.
- The scope grows as FROM is read: ON sees the tables joined so far.
- A subquery is translated with its own empty scope; its output short names
  become the alias's columns, and two equal short names are a schema error.
- ORDER BY on a column that is not selected puts the sort directly below the
  project (DESIGN_M3.md §6).

**Verification:** 25 tests, covering required cases 1, 2, 3, 9, 11, 12.

---

## 2026-11-13: Module 3 step 5, aggregation and type rules

**Goal:** Sections 3.1 and 3.2.

**Decisions:**
- HAVING reuses an aggregate the select list already computes; one only in
  HAVING gets the default name.
- Default aggregate names are Module 2's (`count_star`, `avg_Emp_salary`).
- AVG is REAL and compares with INT (both numbers), as in Module 2.

**AI was wrong:** the first ORDER BY check for aggregating queries tested
`self.aggs is not None and self.keys_known()`, a roundabout condition that
depended on when HAVING aggregates were added. Replaced with a flag set once
the block is known to be aggregating.

**Verification:** 21 tests, covering required cases 6, 7, 10, 13, 14.

---

## 2026-11-13: Module 3 step 6, translation

**Goal:** canonical plan order, every Section 5 example as exact JSON, bag
semantics, required cases 4, 5, 8 and 15.

**Decisions:**
- `distinct` is emitted only for SELECT DISTINCT, and dropped above a
  group_by when every grouping column is selected (DESIGN_M3.md §1).
- The Bonus 1 lexer now reads `<>` as `!=`; the parse tree keeps `!=`, so
  nothing else in Bonus 1 or Module 2 changed. All 292 earlier tests still
  pass.
- A test-only `to_physical` stands in for Module 4 and runs translated plans
  in the Module 2 executor: COUNT(*) on an empty table gives one row,
  duplicates survive projection, UNION and UNION ALL give different results.

**Verification:** 27 tests; every Section 5 example matches.

---

## 2026-11-13: Module 3 step 7, command line and statements

**Goal:** `sql parse`, `sql compile`, `sql check` (Section 8), one error
format (Section 6), CREATE TABLE and INSERT.

**Decisions:**
- compile and check never change the database; CREATE TABLE goes into an
  in-memory overlay so later statements can use it. `sql exec` applies CREATE
  and INSERT through Module 1 (used by `queries/setup.sql`).
- compile --json prints one plan object for one query, an array for several.
- check prints the error on stdout; the other commands use stderr.
- `queries/` holds one SQL file per required case; a test checks each one.

**AI was wrong (twice in one test, once in a message):**
- The step 1 banned-tools test searched for the text `exec(` and failed on
  the new `cmd_exec(` function.
- The first fix inverted the check, so it would have passed a real bare
  `eval(...)` and failed only on longer names. Caught on re-reading the
  assertion; now a call counts only when no letter, digit, `_` or `.` comes
  before it.
- A type error printed the literal `'30'` as `VARCHAR(2)`. Literals now print
  as plain `VARCHAR`.

**Verification:** 15 tests.

---

## 2026-11-13: Module 3 step 8, documents

**Goal:** DESIGN_M3.md (bag semantics first, scope algorithm, aggregate
naming), GRAMMAR_SQL.md, README section.

**Verification:**
- Full-suite command: `python3 -m unittest discover -s tests -v`
- Result: all 437 tests pass (292 from Bonus 1, Module 1 and Module 2, 145
  from Module 3), Python 3.13.
- `sh check_m3.sh` reports every Module 3 file present.


---

## 2026-11-14: Module 3 error handling and set-type review

**Problems found in the AI-assisted implementation:**
- Non-COUNT aggregates with * reached an assertion and produced a traceback.
- Set operations treated INT and REAL as the same type, although the
  Module 2 executor rejects that combination.
- Extremely long integer literals leaked Python's ValueError.
- INSERT checked individual fields but not the complete record size,
  allowing an oversized row to reach storage and produce a traceback.
- The completeness checker searched for a particular source spelling
  of <> and falsely reported that working functionality was missing.

**Fixes applied:**
- Reject invalid star aggregates with a positioned AggregateError.
- Require matching types in each set-operation column position.
- Convert failed integer parsing into a positioned LexicalError.
- Check complete record size before calling storage.
- Replace source-text checks with functional tokenization/parsing checks
  and return a failing checker status when problems are found.

**Verification:**
- Full-suite command: python3 -m unittest discover -s tests -v
- Actual result: [record your result]
- Completeness command: sh check_m3.sh
- Actual result: [record your result]

---

## 2026-11-15: Module 4 step 1, executor additions

**Goal:** every Module 3 logical plan has a Module 2 physical equivalent.

**Decisions:**
- Four optional plan forms (DESIGN_M4 §1): `rename`, `project` with `as`,
  `nested_loop_join` without a condition (cross), and a third element in an
  aggregate naming its output column. All optional, so every Module 2 plan
  is still valid and the 441 earlier tests are unchanged.
- No dataset or workload will be published (instructor's email); we build
  our own and state every assumption in one line in DESIGN_M4 §0.

**Verification:** 10 tests (`tests/test_m4_executor.py`), including a self
join through two renames.

---

## 2026-11-15: Module 4 steps 2 to 9, the optimizer

**Goal:** statistics, estimates, rewrites, cost model, Selinger DP,
planner, `opt` command, dataset and twenty queries.

**Decisions:**
- Column statistics in a `T.stats` heap file, one JSON record per column,
  split into parts when a histogram does not fit in 4084 bytes; the catalog
  record keeps only table-level numbers.
- ANALYZE sorts each column with an ordinary Module 2 plan, so the external
  sort is reused unchanged.
- Join cost: an input that is a pipeline over a base table is not charged
  twice; any other input is charged once. This reproduces §5.1 exactly
  (110, 410, 530).
- The dataset hides two correlations on purpose (city → province, city →
  area) and skews the cities.

**AI was wrong (found and fixed):**
- The estimator memoised on `id(node)`. Short-lived nodes were garbage
  collected and a new node reused an old id, so a test got another node's
  estimate (1000 rows for a filter that keeps none). The memo now keeps the
  node alive and checks identity.
- §5.1's alternative row says 410 + 40 + 1×100 = 550. 40 pages at
  B − 2 = 30 frames are 2 blocks, so it is 650. Our table prints 650 and the
  design document says why (A16).
- The rule ablation found a real bug: with rule 2 off, rule 3's extension
  moved a one-sided select into the join condition, and the DP dropped
  predicates that used a single relation, so q17 lost its WHERE clause and
  returned too many rows. Fixed twice over: rule 3 now merges only two-sided
  predicates, and the planner turns one-sided join conjuncts into filters.
  measure_m4.py now checks every plan's answer, ablations included.
- Module 2 bug found by the planner: a sort above a nested loop join took
  the frame the join needed to reopen its inner, and the next rescan failed
  with a budget error at B = 8. The join now keeps that frame reserved. A
  seqscan whose open failed also crashed in close; fixed.

**Verification:** 61 more tests in `tests/test_m4_*.py` (71 for Module 4
with step 1's). The §2 example, the §3
rewrite and the §5.1 table reproduce; all twenty plans run at B = 4, 8 and
32 with the unoptimized plan's answer.

---

## 2026-11-15: Module 4 step 10, measurements and documents

**Goal:** REPORT_M4.md (q-error, optimized against unoptimized, rule
ablation, correlation failure), DESIGN_M4.md, the 4-relation DP table.

**Findings:**
- Median q-error 1.00; the worst real errors are the 0.1 default for a
  self-join ordering predicate (7.0) and the correlated predicates (4.9).
- Optimization pays 3× on average where there is a join or a GROUP BY, up
  to 14× (q10), and nothing on single-table queries (no indexes).
- Rule 2 and rule 6 carry almost all of the gain. Rules 3, 4, 5 save no
  I/O on this data, and the report says why for each.
- Correlation: q17 is estimated at 83 rows for 407, and at B = 4 the
  optimizer picks a nested loop join that costs 452 page I/Os where a hash
  join costs 271. A column-group ndv(city, area) would have prevented it.
- Improvement 3 (ndv after a filter) took q12's group estimate from 40 to
  9 (actual 9).

**Verification:**
- Full-suite command: `python3 -m unittest discover -s tests`
- Result: all tests pass, Python 3.13.
- `python3 measure_m4.py` regenerates every table in REPORT_M4.md.

### Module 4 review corrections

The AI-assisted implementation missed several edge cases. Histogram
interpolation excluded bucket endpoints, and selection pushdown could
change the result of an empty-key aggregate. Both conditions were
corrected.

DP enumeration could reject every starting pair for a predicate requiring
three relations. It now retries with Cartesian combinations allowed when
the first enumeration cannot construct a complete plan.

ANALYZE previously rescanned the original table for every column. It now
copies live records during one source-table scan and externally sorts
columns from temporary storage.

Statistics splitting previously allowed metadata plus the first bucket
to exceed the record limit. Split records now store metadata separately
and check each encoded record's size.

The endpoint correction changed q05's estimated rows from 350 to 476,
against 451 actual rows. Generated measurements and report text were
refreshed.

Verification:
- Full suite: 517 tests passed, including all five Module 4
  review regression tests.
- python3 measure_m4.py completed successfully.
- Regenerated estimation, optimization, rule-ablation, correlation,
  algorithm-selection and four-relation DP results.
- After the histogram endpoint correction, q05's median and worst
  q-error are 1.06.


## 2026-11-15: Integration, one program over four modules

**Goal:** Section 6: a single program with a prompt that takes the SQL
subset plus EXPLAIN, EXPLAIN ANALYZE, ANALYZE, SET frames and LOAD; a
report of row count, page I/O, wall time and estimated against actual rows
for an unseen workload; ARCHITECTURE.md.

**Decisions:**
- Wiring, not rewriting. The shell commands are recognised by their first
  words in `shell/split.py` and never reach Module 3, so the Module 3
  grammar and all four module tools are unchanged. Everything else goes
  to Module 3 as it is.
- A statement keeps its place in the script: the text before it, and a
  prefix such as EXPLAIN, is replaced by spaces. Module 3 errors therefore
  still give the line and column in the whole script.
- Nothing is cached between statements, so a LOAD or ANALYZE is visible to
  the next query. LOAD does not re-analyze; the shell says the statistics
  are stale.
- Page I/O in the report is Module 2's per-operator counters summed, so
  any number can be traced to an operator with EXPLAIN ANALYZE.
- Correctness before the demo: `pg_tools.py sqlite` runs the same queries
  on SQLite (Python's standard library) over the rows read back from our
  heap files. `pg_tools.py export` prepares the same check for PostgreSQL,
  with VARCHAR columns `COLLATE "C"` because our string order is byte
  order.
- Integration assumptions I1 to I9 are listed in ARCHITECTURE.md
  section 5.

**Findings (rehearsal, B = 32, our data loaded from CSV through the shell):**
- 25 queries (the 20 Module 4 queries and 5 more for INTERSECT, EXCEPT,
  an aggregate in a derived table, a three-way join with SUM and MAX, and
  SELECT *). All 25 answers equal SQLite's as bags, and the ordered ones
  in the same order.
- The largest root q-errors are q22 (EXCEPT, 7.3: the estimate is the left
  input, 2000, for 275), q16 (the 0.1 default for a.did < b.did, 7.0), q17
  (correlated city and area, 4.3) and q13 (DISTINCT over correlated
  columns, 4.0). Each one is explained in REPORT_M4.md or ARCHITECTURE.md.
- Wall time follows comparisons more than pages: q17 does 149 page I/Os in
  610 ms (a nested loop join), q22 does 157 in 76 ms.

**Verification:**
- Full-suite command: `python3 -m unittest discover -s tests`
- Result: 543 tests pass (26 new in `tests/test_integ_*.py`), Python 3.13.
- `python3 run_workload.py --db data/finaldb --queries
  queries/final/workload.sql --repeat 3` writes results/final.

### Integration review corrections

The AI-assisted integration implementation passed its original tests,
but additional review found gaps in correctness checking and reporting.

Already corrected before this review:
- Comments before CREATE statements are handled by the shell tokenizer.
- PostgreSQL export quotes CSV fields, preserving empty strings.
- PostgreSQL scripts stop on SQL errors.
- SQLite query failures produce a nonzero command exit status.

Further corrections:
- Result comparison now uses output-column types. VARCHAR and integer
  values compare exactly; only REAL uses four-decimal display rounding.
  The previous float conversion could hide different strings such as
  "001" and "1", and different large integers.
- Comparison checks the successful query list in summary.csv and requires
  all expected result files. Empty or incomplete workloads cannot pass.
- Missing CSV headers and incorrect field counts are rejected.
- Output schemas are saved for each query.
- Reports record the actual frame budget after SET frames.
- Printing rows is excluded from execution timing.
- The integrated shell's supported minimum is B=4, matching the grading
  minimum. Standalone module tools retain their existing budgets.
- Added 13 integration regression tests.

Verification:
- Reviewer tested the proposed replacements in an isolated copy:
  556 tests passed.
- Integration regression suite: 13 tests passed.
- Full suite: 556 tests passed.
- Rehearsal comparison: 25 of 25 answers matched SQLite.
- Official demo validation is pending because the instructor has not
  released the dataset and 25 queries. PostgreSQL/OpenStack results
  will be recorded when that validation is performed.

  ### Final assignment checklist review

Checked the implementation and submitted documents against the complete
Project 2 overview. The review identified missing source-reference
sections, missing published checkpoint tags, an inaccurate BNL cache-hit
explanation, and an outdated M3 integration paragraph.

Documentation and checkpoint-tag status:
[Record the changes actually completed.]

Official workload validation remains pending release of the instructor's
dataset and queries.
