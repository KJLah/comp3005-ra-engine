# Module 3 design: SQL front end

This document records the design of the SQL front end (`sql/`) and every
choice the Module 3 specification leaves open. Section numbers in brackets
refer to the Module 3 document. The grammar itself, with its precedence
table and the places it differs from the handout's sketch, is in
[GRAMMAR_SQL.md](GRAMMAR_SQL.md).

The instructor confirmed that no logical plan JSON schema or sample catalog
will be published: node and field names are taken from the Module 3
examples (§5) where a node is shown, and from the Module 2 document's §2
where it is not (section 5 below).

---

## 1. Where duplicate elimination happens, and why [§4]

SQL works on bags. The translator removes duplicates in exactly four
places, and nowhere else:

| SQL | Plan | Why it removes duplicates |
|---|---|---|
| `SELECT DISTINCT` | a `distinct` node directly above the block's `project` | the user asked for it |
| `UNION` | a `union` node | SQL's UNION is set union; the node deduplicates |
| `INTERSECT`, `EXCEPT` | `intersect` / `except` nodes | SQL defines both with set semantics, so the node's output has no duplicates even if its inputs did; no `distinct` is added below or above them |
| `GROUP BY` (or an aggregate with no GROUP BY) | a `group_by` node | one output row per distinct group, by construction |

Everything else keeps duplicates:

- **scan** returns every stored row, identical rows included.
- **project never deduplicates.** Dropping columns never emits a `distinct`.
  This is the one place the module deliberately differs from Bonus Project
  1, whose projection removed duplicates because relational algebra is
  defined on sets (that code was already changed in the week-one refactor:
  `ra_engine` projection keeps duplicates and `distinct(...)` is explicit).
- **select, join, cross, rename, sort, limit** keep duplicates.
- **`UNION ALL`** is a `union_all` node alone.

**DISTINCT over GROUP BY.** The handout says a `distinct` above a
`group_by` is redundant and must not be emitted. It is redundant only while
every grouping column is in the select list: then no two output rows can be
equal, because they differ in the group key. So the translator drops the
`distinct` when every grouping key is selected (including the case of no
grouping keys, which yields one row), and keeps it when a grouping column
is left out, because `SELECT DISTINCT COUNT(*) FROM Emp GROUP BY did` can
print the same count for two departments. Code: `_Block.distinct_is_redundant`
in `sql/translate.py`; tests in `tests/test_m3_translate.py` (`BagSemantics`).

The tests that pin this down, besides the plan-shape tests, run plans in the
Module 2 executor: `SELECT age, salary ... WHERE age > 30` returns the two
identical (35, 90) rows, and `SELECT DISTINCT` returns one
(`RunsInModuleTwo.test_bags_keep_duplicates`).

---

## 2. Layout of the code

| File | Spec | Contents |
|---|---|---|
| `sql/lexer.py` | §1 | hand-written scanner, `Token`, `tokenize`, `quote` |
| `sql/ast.py` | §2 | parse tree dataclasses, each with a source position |
| `sql/parser.py` | §2 | recursive descent, one method per grammar rule; `parse_script`, `parse_query`, `parse_condition` |
| `sql/printer.py` | §5 | condition strings for plans, the indented parse tree |
| `sql/resolve.py` | §3 | catalogs, `Scope`, name lookup, type rules, aggregate naming |
| `sql/translate.py` | §4, §5 | canonical translation of blocks and set operations |
| `sql/logical.py` | §5 | plan node constructors, `explain`, `check_plan` (the checklist validator) |
| `sql/statements.py` | §2 | CREATE TABLE and INSERT checks |
| `sql/errors.py` | §6 | `SQLError` and the seven categories |
| `sql/cli.py` | §8 | `sql parse`, `compile`, `check`, `exec` |
| `queries/` | §7 | `setup.sql` (test catalog) and one file per required case |
| `tests/test_m3_*.py` | §7, §9 | 145 tests, including all fifteen required cases |

Nothing in `sql/` prints except `cli.py`. No regular expressions, parser
generators or `eval` are used; `tests/test_m3_lexer.py` checks this.

---

## 3. Scope and resolution algorithm [§3]

**Building the scope.** `_Block.from_clause` walks the FROM list left to
right. Each table reference adds one `Entry(key, columns)` to the block's
`Scope`, where the key is the alias if one is written and the table name
otherwise. A base table's columns come from the catalog; a subquery is first
translated on its own (with an empty scope of its own, so it cannot see the
outer query) and its output columns, by short name, become the entry. Adding
a key that is already present is a `name` error ("duplicate alias"), so
`FROM Emp, Emp` fails and `FROM Emp e, Emp m` works. An alias hides the table
name: after `FROM Emp e`, `Emp.name` is an unknown alias.

**When names become visible.** The scope grows as FROM is read, so a JOIN's
ON condition can use the tables joined so far and the one being joined, but
not tables further right. WHERE, GROUP BY, HAVING, the select list and ORDER
BY see the whole scope.

**Resolving one column** (`Scope.lookup`):

1. `t.c`: find the entry with key `t`, else "unknown table or alias 't'";
   find column `c` in it, else "'t' has no column 'c'". Both are `name`
   errors, and the message says which part failed.
2. `c`: collect the entries that have a column `c`. None: `name` error
   "unknown column". Two or more: `ambiguity` error naming every candidate
   (`Emp.did, Dept.did`). Exactly one: that entry.
3. The result is always `key.c`, the fully qualified name used in the plan.

**SELECT \*** expands (`Scope.expand_star`) to every column of every entry,
FROM order then schema order, each qualified, so shared names are not an
error. The project lists them all explicitly.

**ORDER BY** resolves an unqualified name first against the select list's
output names (aliases written with AS, and aggregate output names), then
against the FROM scope. A FROM-scope column that is selected under an alias
is sorted by that alias.

**Where no unqualified name survives.** Every base column in every plan
field and condition string is `key.column`. The only unqualified names are
output names the plan itself creates: aggregate outputs (from `group_by`)
and select-list aliases (from `project`'s `as`), exactly as in the handout's
example 5.4 (`select "n > 5"`, `sort [["n","desc"]]`). `logical.check_plan`
enforces this: an unqualified name is accepted only above the node that
creates it.

---

## 4. Aggregate naming rule [§5.4]

An aggregate's output name is its AS alias if one is written. Otherwise:

| Aggregate | Name |
|---|---|
| `COUNT(*)` | `count_star` |
| `fn(t.c)` | `fn_t_c`, with the qualified column's dot replaced by `_`: `AVG(salary)` over `FROM Emp` is `avg_Emp_salary`; over `FROM Emp e` it is `avg_e_salary` |

This is the same rule the Module 2 `group_by` uses for its output schema, so
a plan with default names runs in the executor without renaming.

Details:

- An aggregate in HAVING that the select list also computes (same function,
  same resolved column) reuses that entry and its name, so
  `HAVING COUNT(*) > 5` with `COUNT(*) AS n` becomes `select "n > 5"` over a
  single `["count","*","n"]` (example 5.4). An aggregate only in HAVING is
  added to the `group_by` under its default name and dropped by the project.
- Two aggregates with one name (`SELECT COUNT(*), COUNT(*)`, or two equal
  aliases) is a `schema` error asking for an alias, because the output
  schema would have two columns with one name.

---

## 5. Logical plan JSON [§5]

| Node | Fields | Source of the names |
|---|---|---|
| `scan` | `table` | §5.1 |
| `rename` | `to`, `child` | §5.2 |
| `select` | `condition`, `child` | §5.1 |
| `project` | `columns`, optional `as`, `child` | §5.1; `as` is ours |
| `join` | `condition`, `left`, `right` | §5.2 |
| `cross` | `left`, `right` | as join, without a condition |
| `group_by` | `keys`, `aggregates` as `[fn, column or "*", name]`, `child` | §5.4 |
| `distinct` | `child` | Module 2 §2.4 without `mode` |
| `sort` | `keys` as `[column, "asc" or "desc"]`, `child` | §5.4 and Module 2 §2.5 |
| `limit` | `n`, `child` | Module 2's limit |
| `union`, `union_all`, `intersect`, `except` | `left`, `right` | Module 2's set operators |

A logical node never carries `mode` or an algorithm name: the physical
choices are Module 4's. `project.as` appears only when a plain column was
given an alias with AS: it is parallel to `columns`, with `null` for columns
without an alias (`{"columns":["Emp.name","Emp.age"],"as":["who",null]}`).
Without it the alias would be lost, and a subquery `(SELECT name AS who ...)
AS t` could not expose `t.who`. Aggregate aliases need no `as`, because the
`group_by` already names the aggregate.

**Condition strings** are printed from the resolved parse tree with every
column qualified, aggregates replaced by their output names, string literals
re-quoted (`'O''Hare'`), and the fewest parentheses that keep the tree's
shape (an OR inside an AND is parenthesised; so is a right-nested chain the
user wrote with parentheses). Keywords are printed in lower case
(`and`, `or`, `not`), matching the Module 2 document's examples and the Bonus
1 condition parser the executor uses. Operators are printed as written,
including `<>`; Module 3 taught the Bonus 1 lexer to read `<>` as `!=` (the
only change outside `sql/`), so every condition string loads in the
executor. The condition is otherwise the user's: WHERE becomes one `select`
node carrying the whole condition, ON stays on its join, nothing is split or
moved.

---

## 6. Translation order and the one exception [§5]

Blocks are built bottom to top in the canonical order: FROM (scans, a
rename per alias, `cross` for a comma and `join` for JOIN ON, left deep in
FROM order), WHERE, GROUP BY, HAVING, project, distinct, sort, limit; set
operators combine whole blocks left associatively, as parsed.

**ORDER BY a column that is not selected** (required case 9). Above the
project that column no longer exists, so a sort placed in canonical position
could not run. When at least one ORDER BY key is not an output of the select
list, the sort is placed directly below the project instead, with every key
in its pre-projection name:

```
SELECT name FROM Emp ORDER BY age      project ["Emp.name"]
                                         sort [["Emp.age","asc"]]
                                           scan Emp
```

The project preserves order (it is pipelined in Module 2), so the result is
sorted. Alternatives considered: widening the project with the hidden column
and adding a second project above the sort (more nodes, and a project that
is not what the user wrote), or rejecting the query (the handout requires it
to work). When every key is selected, the sort stays in its canonical place
above the project. Two related rules: with `SELECT DISTINCT` an ORDER BY
column must be selected (`schema` error, as in PostgreSQL, because one
distinct row can stand for rows with different sort values), and in an
aggregating query a hidden sort column must be a grouping column
(`aggregate` error).

---

## 7. Aggregation and type rules [§3.1, §3.2]

- A block is aggregating if it has GROUP BY or an aggregate in its select
  list. Then each selected plain column (and each column of `*`) must be a
  grouping column, else an `aggregate` error at that column.
- HAVING may use grouping columns and aggregates only. WHERE and JOIN ... ON
  may not contain an aggregate (`aggregate` error at the aggregate). The
  grammar accepts aggregates in conditions so that this is an aggregate
  error, not a syntax error (GRAMMAR_SQL.md §4.3).
- Comparisons need the same type on both sides, equality included; this
  covers column against column, column against literal and literal against
  literal (`type` error at the operator). AVG's output type is REAL; it
  compares with INT, since both are numbers (the Module 2 rule).
- SUM and AVG need an INT column (`type` error); COUNT takes anything; MIN
  and MAX take either type and return it.
- Set operators need the same column count and the same type in each
  position (`schema` error at the operator). The output names are the left
  side's.
- `SELECT COUNT(*) FROM Emp` with no GROUP BY is a `group_by` with
  `keys: []`; Module 2 returns one row (0) on an empty table
  (`RunsInModuleTwo.test_case_13_...`).

---

## 8. Errors [§6]

`error: <category>: <message> at line L, column C`, categories lexical,
syntax, name, ambiguity, type, aggregate, schema. Every parse-tree node keeps
the position of its first token, and each error points at the token it is
about: the bad character, the unexpected token, the unresolved name, the
comparison operator, the aggregate, the set operator. Panic mode: the first
error stops. `sql check` prints the error on standard output (it is the
command's output); the other commands print it on standard error. Problems
outside the SQL text (missing file, not a database directory) use the same
shape with category `io` and no position. No stack trace is ever printed.

---

## 9. CREATE TABLE, INSERT and the catalog

`compile` and `check` never change the database. A CREATE TABLE in the file
is checked (the table must not exist, column names distinct, VARCHAR length
1 to 255) and recorded in an in-memory overlay so later statements can use
the table; an INSERT is checked against the table's columns (count, type,
VARCHAR byte length, 32-bit INT range). `sql exec` does the same checks and
then applies them through Module 1 (`Catalog.create_table`, `HeapFile.insert`,
`refresh_counts`), which is how `queries/setup.sql` builds the test catalog.
Statements before an error stay applied (each is its own unit, as in a
shell); a syntax error anywhere stops the file before anything is applied.

---

## 10. From logical to physical

The Module 3 tests retain a small logical-to-physical adapter for isolated
translation tests. The completed system uses Module 4's planner to produce
physical plans that run directly in Module 2.

The executor now supports rename, projection output aliases, named
aggregates, and cross products through nested_loop_join without a
condition. These additions support the logical forms emitted by Module 3.
The integrated pipeline is exercised by tests/test_integ_workload.py.

## References and connection to the design

1. Nystrom, R. Crafting Interpreters. Chapters on expression parsing and
   recursive descent.
   https://craftinginterpreters.com/parsing-expressions.html

   This source helped me validate the handwritten parser structure.
   Separate parsing functions represent precedence levels, while loops
   implement left-associative operators. This makes precedence explicit
   without regular expressions or a parser generator.

2. PostgreSQL Global Development Group. PostgreSQL documentation:
   SELECT.
   https://www.postgresql.org/docs/current/sql-select.html

   This source helped me validate the SQL translation and bag-semantics
   decisions. WHERE filters input rows before grouping, HAVING filters
   groups, and duplicate elimination is explicit for SELECT DISTINCT
   and UNION. UNION ALL preserves duplicates.
