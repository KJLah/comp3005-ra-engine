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
