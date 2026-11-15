# ARCHITECTURE.md: the whole system

This is a small relational database engine written from scratch in Python.
SQL text goes in and rows come out. Four modules do the work, one per
layer, and the integration (`shell/`) wires them into one program, `radb`.
Each module's own command line tool still works on its own.

```
  SQL text                                       radb  (shell/)
     |                                           EXPLAIN, EXPLAIN ANALYZE, ANALYZE,
     v                                           SET frames, LOAD, \d
  [ M3 ]  sql/         lexer -> parser -> semantic analysis -> translation
     |
     |  logical plan (JSON)
     v
  [ M4 ]  optimizer/   rewrites -> statistics -> cost model -> join order -> algorithm
     |
     |  physical plan (JSON)
     v
  [ M2 ]  execution/   iterator tree, open / next / close, memory budget B
     |
     |  page requests (fetch / unpin / new page)
     v
  [ M1 ]  storage/     buffer pool -> heap files -> 4 KB slotted pages on disk
                   ^
                   |
              catalog (catalog.db) and statistics (T.stats), read by M3 and M4
```

## 1. The modules

| Module | Package | What it owns | Its tool |
|---|---|---|---|
| M1 storage | `storage/` | 4 KB slotted pages, heap files, the buffer pool (LRU or clock), the catalog, the CSV loader | `storage` |
| M2 execution | `execution/` | the iterator operators (scan, filter, project, sort, group, distinct, limit, set operations, three joins), external sort, the frame budget, per-operator I/O counters | `exec` |
| M3 SQL | `sql/` | the SQL subset (GRAMMAR_SQL.md): lexer, recursive descent parser, name and type checks, translation to a logical plan | `sql` |
| M4 optimizer | `optimizer/` | ANALYZE and histograms, selectivity and cardinality estimates, rewrite rules 1-7, the I/O cost model, the Selinger join order DP | `opt` |
| Integration | `shell/` | the prompt, the shell commands, timing, estimated against actual rows | `radb` |

Bonus 1's relational algebra engine (`ra_engine/`, `ra.py`) is still there.
Module 3 reuses its character classes and Module 2 its condition grammar.

## 2. How one query runs

`radb` reads text up to a `;` (outside strings and comments), then for a
query:

1. **M3** `sql.cli.compile_script(text, StorageCatalog(db))` parses and
   checks the query against the catalog and returns the logical plan.
2. **M4** `optimizer.planner.Planner(load_all(db), B).plan(logical)` rewrites
   it, runs the join order DP and returns a physical plan. The planner's
   estimator gives the estimated rows at the root.
3. **M2** `execution.cli.PlanRun(db, plan, B)` builds the operator tree,
   opens it with B frames, streams the rows and closes it. It then checks
   that no frame is pinned and no temporary file is left.
4. **M1** serves every page through one buffer pool of B frames.

Steps 1 to 3 are timed separately. The reported page I/O is the sum of
every operator's reads and writes (Module 2's counters), so a number in
the report can always be traced to one operator with `EXPLAIN ANALYZE`.

The shell commands are recognised by their first words in `shell/split.py`
and never reach Module 3, so the Module 3 grammar did not change. Anything
else goes to Module 3 unchanged. Text before a statement (and a prefix such
as `EXPLAIN`) is replaced by spaces, so a Module 3 error still reports the
line and column in the whole script.

| Shell input | Wired to |
|---|---|
| `SELECT ...` | M3 compile, M4 plan, M2 run |
| `CREATE TABLE`, `INSERT` | M3 `check_create` / `check_insert`, then M1 `Catalog` (the same calls as `sql exec`) |
| `EXPLAIN q` | M3, M4, then `optimizer.explain.explain` |
| `EXPLAIN ANALYZE q` | M3, M4, M2, then every node's estimated rows next to its actual `tuples_out`, reads and writes |
| `ANALYZE [t, ...]` | `optimizer.stats.analyze` (every table when none is named) |
| `SET frames = n` | the B handed to M4 and M2 from then on |
| `LOAD t FROM 'f' [HEADER]` | `storage.csvload.load_csv` (Module 1) |

Nothing is cached between statements. The catalog and the statistics are
read again for every statement, so the effect of a `LOAD` or `ANALYZE` is
seen at once.

## 3. The interfaces between the modules

Every interface is plain data (JSON-shaped dicts, or bytes on pages), so
each module can be run and tested alone from files.

### M3 to M4: the logical plan

A nested dict with an `op` field (`sql/logical.py`): `scan`, `rename`,
`select`, `project` (with optional `as`), `join`, `cross`, `group_by`,
`distinct`, `sort`, `limit`, `union`, `union_all`, `intersect`, `except`.
Conditions are strings in the SQL condition syntax (`e.did = d.did AND
e.age > 60`). A logical plan never names an algorithm or a mode. M4 parses
the condition strings again (`optimizer/conds.py`). On disk this is the
output of `sql compile --json` and the input of `opt plan --logical`.

### M4 to M2: the physical plan

The Module 2 plan JSON (`execution/plan.py`) with the Module 2 Section 2
field names: `seqscan`, `filter`, `project`, `sort`, `group_by` (with
`mode`), `distinct` (with `mode`), `limit`, `nested_loop_join`,
`sort_merge_join`, `hash_join` and the set operations. Module 4 added four
forms so that every logical plan has a physical one: `rename`, `project`
with `as`, a `nested_loop_join` without a condition (a cross product) and
an output name as the third element of each aggregate. `validate` checks
the structure and `bind` checks names against the catalog before any tuple
is read. On disk this is the output of `opt plan` and the input of
`exec run --plan`.

### M2 to M1: pages and frames

Module 2 asks Module 1 for pages: `BufferPool.fetch_page`, `unpin_page`,
`new_page` and `flush_page`, through `HeapFile` for tables and through
temporary heap files for sort runs and hash partitions. One
`ExecContext` holds the single pool of exactly B frames and the frame
budget (which operator holds how many frames). Each operator opens its
files through its own `OwnedDisk`, so every page read and write is counted
on the operator that caused it, even when another operator's request
caused the eviction.

### M3 to M1: the catalog

`sql.resolve.StorageCatalog` reads table names, column names and types
from Module 1's catalog for the semantic checks. `sql exec` and the shell
create tables and insert rows through Module 1's `Catalog` and `HeapFile`.

### M4 to M1: statistics

ANALYZE stores a small summary in each table's catalog entry
(`TableInfo.stats`: analyzed, n_tuples, n_pages, width, buckets, the name
of the statistics file) and the per-column statistics (ndv, min, max,
width, the equi-depth histogram) as JSON records in a heap file `T.stats`.
Records longer than one page are split. The column values are sorted with
a Module 2 external sort plan, so ANALYZE also goes through M2. A table
that has not been analyzed gets documented default guesses
(`optimizer/stats.py defaults_for`).

### The shell and the modules

The shell only calls the functions named above. It does not change a
module's behaviour, and `storage`, `exec`, `sql` and `opt` give the same
output as before (`tests/test_integ_workload.py` runs the four of them in
a pipeline).

## 4. The demo

The instructor confirmed that no dataset or queries will be provided.
This submission uses our own dataset and 25-query workload.

```
radb --db data/demo --file demo_setup.sql            CREATE TABLE ..., LOAD ..., ANALYZE
python3 run_workload.py --db data/demo --queries demo_queries.sql --repeat 3 --out results/demo
python3 pg_tools.py export --db data/demo --out pg --queries demo_queries.sql
    copy pg/ to the OpenStack instance, then:
    cd pg
    psql -X -v ON_ERROR_STOP=1 -d DB -f schema.sql
    psql -X -v ON_ERROR_STOP=1 -d DB -f load.sql
    sh run.sh DB
python3 pg_tools.py compare --ours results/demo/rows --ref pg/rows
```

`run_workload.py` reports, for each query, the result row count, the page
I/O (reads + writes), the wall time (median of the runs) and the estimated
rows at the root against the actual rows, with the q-error. `EXPLAIN
ANALYZE` on any query explains its numbers node by node.

`pg_tools.py export` writes the rows held by our heap files, quoting every
CSV field so empty strings remain empty strings in PostgreSQL. VARCHAR
columns use COLLATE "C".

The comparator reads output types from schemas/<query>.json. VARCHAR and
integer values compare exactly; only REAL output uses four-decimal display
rounding. It requires a nonempty, successful workload in summary.csv and
the complete expected query-file set in both result directories.

The rehearsal (`gen_final.py`, `queries/final/`) runs the same steps on our
own data: 25 queries, all 25 answers equal to SQLite's over the same rows
(`pg_tools.py sqlite`). Its report is `results/final/summary.md`.

## 5. Integration assumptions

One line each, as the instructor asked.

- I1. The demo data arrives as CSV with a header line. `LOAD ... HEADER` skips it, and without HEADER every line is a row (Module 1's format).
- I2. The tables are created with `CREATE TABLE` before `LOAD`. LOAD does not guess a schema.
- I3. LOAD does not refresh statistics. The shell says when they are stale, and the setup script runs `ANALYZE` after the loads.
- I4. Page I/O is the sum of every operator's page reads and writes. Catalog and statistics reads are not counted, as in Module 2.
- I5. Wall time is compile + plan + execute, measured with `time.perf_counter`, the median of `--repeat` runs. Printing rows is not timed.
- I6. The integrated shell supports B >= 4, with default B = 32. SET frames and --frames change the budget, and each query's report records the B actually used. Standalone module tools retain their existing supported budgets.
- I7. Results are compared as bags by default. Use compare --ordered q01,q02 for queries whose ORDER BY requires an exact sequence. Do not require exact sequence equality when ties permit different valid orders.
- I8. Identifiers are case sensitive in our SQL (Module 3) and folded to lower case by PostgreSQL. `pg_tools` writes lower-case table names, so unquoted names work in both.
- I9. There are no NULLs (Module 1 has no NULL). A demo file with empty fields fails to load for an INT column and loads as an empty string for a VARCHAR column.
- I10. The instructor confirmed that no dataset or queries will be supplied, so this submission uses our own four-table dataset and 25-query rehearsal workload.

## 6. What I would do differently

**One column type and one expression tree, shared by every module.** A
column is described three times: `storage.record.Column`,
`sql.resolve.ColInfo` and `execution.schema.Schema`, with conversions at
each boundary. Conditions are worse. They travel between modules as
strings, and three parsers read them back: Module 3's parser, Module 2's
`execution/conditions.py` and Module 4's `optimizer/conds.py`. Each one had
its own small bugs, and a rewrite rule that changed a condition had to
print it back to text. I would define one `Column` and one expression AST
in a common package on the first day and serialise that AST in the plan
JSON.

**Design the physical plan format with the optimizer in mind.** Module 2's
plan format came from hand-written plans. Module 4 then needed four new
forms (`rename`, `project ... as`, a cross product, named aggregates) and
the optimizer had to look through projections to find join blocks. A
format designed for generated plans, with output column names on every
node, would have avoided both.

**Count CPU, not just pages.** The cost model counts page I/O only, as the
course does. In Python the comparisons dominate the wall time: in the
rehearsal q17 reads 149 pages and takes 610 ms, while q22 does about the
same I/O in 76 ms, because q17 is a nested loop join. A cost of
`I/O + w x comparisons` with a measured w would choose joins that are
faster in practice and would make the wall time explain itself.

**Keep statistics in step with the data.** Statistics are a snapshot. A
LOAD or INSERT after ANALYZE leaves them stale, and the shell can only
warn. I would keep a modification counter in each catalog entry and
re-analyze, or at least scale n_tuples, when it passes a threshold. I
would also store a few column-group ndvs, since correlated columns (q17,
the "where it breaks" study) are the largest estimation error we have.

**Check answers against a reference engine from the first module.** The
end-to-end check against SQLite only came with the integration. It would
have caught earlier the Module 4 bug where q17 lost its WHERE clause with
rule 2 off, which we only found while checking the report numbers. I would build the reference check
alongside Module 1's loader and run it on every query in the tests.

**Support NULL from the start.** Real datasets have missing values, and
adding NULL now would touch the record format, every comparison and every
aggregate. Planning for it in Module 1 costs one bitmap per record.

**Decide early what an error looks like across modules.** Module 3 has a
careful `error: <category>: <message> at line L, column C`. The other
modules print `error: <message>`. The shell has to keep both formats. One
error class with an optional position, shared by all modules, would have
given the user one format.
