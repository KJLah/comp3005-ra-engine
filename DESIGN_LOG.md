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
