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
<!-- TODO: only real incidents, each with what the problem was and how you found it. -->

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
<!-- TODO -->
