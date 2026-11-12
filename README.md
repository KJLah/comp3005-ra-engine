# Relational Algebra Query Processor

COMP 3005 (Fall 2026). Originally developed for Bonus Project 1, this
relational algebra engine is now being prepared as a reusable library
for Project 2. It includes a hand-written tokenizer, recursive-descent
parser, AST, condition compiler, operator execution, JSON tree
serialization, command-line interface, and performance study.

The week-one refactor introduces bag semantics: input loading and
projection preserve duplicate tuples. Duplicate removal is available
through the explicit `distinct(expr)` operator.

It needs Python 3.8 or newer and no third-party packages. matplotlib is
optional and is used only by `bench.py` to draw the plot for the report.
## Module 1: storage engine and buffer pool

Module 1 implements record encoding, slotted pages, heap files, a
persistent catalog, CSV loading, and a buffer pool with LRU and CLOCK
replacement. Stored tables can also be queried through the relational
algebra engine.

### Install the storage command

From the repository root, run:

```bash
python3 -m pip install -e .
storage --help
```

The command provides `create-table`, `load`, `scan`, `layout` and `trace`.
Without installation, the same CLI is available as `python3 -m storage`.

### Tests and measurements

```bash
python3 -m unittest discover -s tests -v
python3 measure_m1.py
```

The verified suite contains 194 tests. The measurements cover record
capacity, replacement-policy hit rates, and scan cost.

### Module 1 documentation

- [DESIGN_M1.md](DESIGN_M1.md): page and catalog formats, free-space
  strategy, pinning, failure recovery, and implementation decisions.
- [REPORT_M1.md](REPORT_M1.md): capacity, replacement-policy and
  scan-cost measurements.
- [DESIGN_LOG.md](DESIGN_LOG.md): development history, problems found
  in the AI-assisted implementation, fixes, and verification results.

Scan counters currently measure table-page accesses only. Catalog
accesses use a separate four-frame pool, as documented in DESIGN_M1.md.

## How to run

```bash
# print the parse tree only (no data needed)
python3 ra.py --tree "project[Name](select[Age>30](Employees))"

# run a query against one or more relation files
python3 ra.py -d data/employees.ra "project[DID](Employees)"

# also show the operator counters and the wall time
python3 ra.py -d data/employees.ra --stats "Emp join[Emp.DID=Dept.DID] Dept"

# interactive shell (type .tree <query> for a tree, .quit to leave)
python3 ra.py -d data/employees.ra

# tests: all 25 required cases, the Lecture 02 examples, and our own
python3 -m unittest discover -s tests -v

# performance study
python3 gen_data.py --n 1000 --m 1000 --match 1 --out data/generated
python3 bench.py            # full run, 1000..64000, writes results/
python3 bench.py --max 8000 # quick run
```

## Repository layout

| Path | What it is |
|---|---|
| `GRAMMAR.md` | The language: EBNF, precedence, the ambiguity demo, the parsing strategy |
| `REPORT.md` | The performance study |
| `DESIGN_LOG.md` | A dated diary of the work |
| `ra.py` | Command-line interface: loads data, parses, prints the tree or the result |
| `ra_engine/lexer.py` | Hand-written character-by-character tokenizer |
| `ra_engine/parser.py` | Recursive-descent parser, one method per grammar rule |
| `ra_engine/ast_nodes.py` | Parse tree node classes |
| `ra_engine/printer.py` | Parse tree printer (`--tree`) |
| `ra_engine/relation.py` | Schemas, tuple equality, result table printing |
| `ra_engine/conditions.py` | Name resolution; compiles conditions to closures (no `eval`) |
| `ra_engine/loader.py` | Parses relation text, validates schemas and types, and preserves duplicate tuples |
| `ra_engine/operators.py` | Operator implementations, schema/type checks, and execution |
| `ra_engine/planner.py` | Builds an operator tree from the AST; provides `plan()` and `evaluate()` |
| `ra_engine/sources.py` | Tuple-source contract for supplied data sources |
| `ra_engine/serialization.py` | Versioned JSON serialization and deserialization of AST nodes |
| `ra_engine/errors.py` | The five error categories, rendered with a position |
| `gen_data.py` | Data generator for R(a, b) and S(b, c) |
| `bench.py` | Runs the experiment and writes `results/` |
| `tests/` | Bonus 1 cases, lecture examples, layer checks, and library integration tests |
| `data/employees.ra` | Example relations (Employees from section 4.1, Emp, Dept) |

## Pipeline

```
query text ──lexer──▶ tokens ──parser──▶ parse tree ──plan()──▶ operator tree ──execute()──▶ rows
                                             │                     │
                                          --tree              name / schema /
                                                              type checks
```

`plan()` makes every name, schema and type check before a single tuple is
read. `execute()` then evaluates the operator tree bottom-up, exactly as the
user wrote it. There is no optimisation or rewriting.

## What is supported

* **Relation files**: `Name(a, b, ...) = { ... }`, one tuple per line.
  Values can be bare (`John`, `D1`, `32`) or quoted (`'O''Brien, Pat'`).
  `//` comments and blank lines are allowed. Duplicate tuples are
  preserved. A number column must hold only numbers and a string column
  only strings.
* **Unary operators**: `select[cond](e)`, `project[a, b, ...](e)`,
  `rename[N](e)`, and `distinct(e)`.
* **Binary operators**: `union`, `intersect`, `minus`, `times`, `join[cond]`.
  They are all left-associative. Precedence, tightest first: unary and
  parentheses, then `times`/`join`, then `intersect`, then `union`/`minus`.
* **Conditions**: `= != < <= > >=`, combined with `not`, `and` and `or` (in
  that order of precedence) and parentheses. An operand is a number, a quoted
  string, or an attribute (`Age` or `Emp.DID`).

## Library interfaces

The tokenizer, parser, AST and operators can be imported independently.
They do not print, read interactive input, or open data files. File access
and console output are handled by the CLI or other callers.

### Standalone conditions

`parse_condition(text)` parses one complete condition and rejects trailing
tokens. `predicate_for_row(condition, attributes)` compiles the tree into
a predicate for a tuple with the supplied schema.

```python
from ra_engine.conditions import predicate_for_row
from ra_engine.parser import parse_condition
from ra_engine.relation import Attribute, NUM

schema = [
    Attribute("R", "b", NUM),
    Attribute("S", "b", NUM),
]

condition = parse_condition("R.b = S.b")
matches = predicate_for_row(condition, schema)

assert matches((7, 7))
assert not matches((7, 8))
```

`compile_condition(condition, attributes, split)` also supports separate
left and right tuples, as used by joins.

Qualified references such as `R.b` are supported in conditions and
projection. Bare references remain supported for Bonus 1 compatibility
and must resolve to exactly one column. Renaming changes the qualifier;
products and joins retain the qualifiers of both inputs.

### Planning and tuple sources

Import `plan()` and `evaluate()` from `ra_engine.planner`.

A supplied tuple source exposes:

- `name`: its relation name.
- `attributes`: its schema, available without reading tuples.
- `scan()`: a fresh iterable of tuples for each execution.

The existing in-memory `Relation` implements this contract. Callers may
also supply generator-backed sources without exposing a `.rows` list.
Planning uses schema metadata without consuming tuples.

Execution currently materializes inputs and intermediate results.
This interface does not yet implement Module 2's iterator model.

### JSON trees

`tree_to_json(tree)` and `tree_from_json(text)` are provided by
`ra_engine.serialization`.

```python
from ra_engine.parser import parse_query
from ra_engine.serialization import tree_from_json, tree_to_json

tree = parse_query("distinct(project[R.a](R))")
restored = tree_from_json(tree_to_json(tree))

assert restored == tree
```

The version 1 JSON document contains `version` and `tree`. Nodes record
their type and fields, including nested nodes, qualified references and
source positions. The decoder validates node tags, fields and types.

The serialized representation is the declarative AST. Operator objects
and compiled Python predicates are rebuilt after loading.


## Semantic decisions

These are the "decide and document" points from the handout:

* **Tuple equality** (`relation.py: value_key`). A number never equals a
  string. Numbers compare by value, so `32` equals `32.0`. Strings are
  case-sensitive. All deduplication goes through this definition.
* **Keywords as attribute names** (case 8). The operator words are allowed as
  attribute names but not as relation names. `and`, `or` and `not` are
  reserved everywhere. See GRAMMAR.md 5.1.2.
* **Unquoted words in a condition are attributes.** `select[A=B](R)` compares
  two columns. The string must be written `'B'` (case 18).
* **`project[Name, Name]`** (case 24) is a schema error. An output schema
  cannot contain the same column twice. This also catches
  `project[Name, Employees.Name]`, which names the same column in two ways.
* **Qualified names.** Every attribute carries the name of the relation it
  came from. `times` and `join` keep both qualifiers, so after
  `Emp join[Emp.DID=Dept.DID] Dept` the output has `Emp.DID` and `Dept.DID`
  (case 19). The result table shows the qualified name only when a bare name
  would be ambiguous. A bare attribute name in a condition must match exactly
  one column, otherwise it is an "ambiguous attribute" name error.
* **Union compatibility.** The inputs need the same number of attributes, the
  same bare names in the same order, and the same types. A column of an empty
  relation has no known type and is compatible with either type. The output
  uses the left input's schema.
* **Type errors** are detected statically, from the column types, before
  execution. So `select[Age>'30'](R)` fails even when R is empty, as long as
  the type of Age is known.
* **Bags and duplicate removal.** Loading, selection, projection and rename
  preserve tuple multiplicity. Products and joins preserve the multiplicity
  of matching combinations. `distinct(e)` removes duplicate tuples using
  `tuple_key`. The existing `union`, `intersect` and `minus` operators
  return distinct tuples even when their inputs contain duplicates.
  SQL-style `ALL` variants are not implemented.

  Required case 23 now expects duplicate department values after
  projection. Duplicate removal is tested separately with `distinct`.
  Case 24 is unchanged: projecting the same column twice remains a
  schema error.

### Why the self join needs `rename` (case 20)

`Emp join[Emp.MgrID=Emp.EID] Emp` cannot be answered, for two reasons:

1. **The schema.** `join` is `times` followed by `select`, and `times`
   qualifies every attribute by relation name. Both inputs are called `Emp`,
   so the product would have two attributes called `Emp.EID`, two called
   `Emp.MgrID`, and so on. Qualification cannot tell them apart, so this is a
   schema error.
2. **The condition.** Even if that were allowed, `Emp.MgrID = Emp.EID` could
   not say "the employee's MgrID equals the *other copy's* EID". Both names
   refer to the same relation, so at best the condition compares a tuple with
   itself, which finds people who manage themselves.

`rename[E2](Emp)` gives the second copy a new name, so its attributes become
`E2.EID`, `E2.MgrID` and so on. Now the product has distinct names, and
`Emp.MgrID = E2.EID` pairs each employee with their manager:

```bash
python3 ra.py -d data/employees.ra \
  "project[Emp.Name, E2.Name](rename[E2](Emp) join[Emp.MgrID=E2.EID] Emp)"
```

## How this relates to Lecture 02 (Relational Algebra)

`tests/test_lecture_examples.py` checks the lecture examples against
`tests/data/lecture.ra`. Selection, joins and set-operation examples
retain their expected results. Projection now uses bag semantics, so
examples that require a set-valued projection need explicit `distinct`.

* σ, π and their combinations, including the two "is it commutative?"
  slides. σ commutes, and two σ's equal one σ with `and`. π does not
  commute, and `select[id>=2](project[name, email](Employee))` is a name
  error because `id` has been projected away.
* × followed by σ equals ⨝ (the "Inner Join = ⨉ with σ" slide).
* ∪, ∩ and − on compatible relations. − is not commutative, and projecting
  away the extra `Salary` column fixes the compatibility problem.
* The "Examples" section: students in Ottawa, Makela's email, names with
  titles and marks (a two-join query), and students who take no courses. The
  course-name projection produces `{Alex, Alex, Alex, John}` as a bag.
  Wrapping that projection in `distinct(...)` produces `{Alex, John}`.
* **Division** is not one of our operators, but the "students who studied
  ALL courses" slide can be written with the core ones:
  `π_sid(R) − π_sid((π_sid(R) × π_cname(Course)) − R)`.

Where this project deliberately differs from the slides, it follows the
assignment handout:

| In the lecture | Here | Why |
|---|---|---|
| Natural join `Employee ⨝ Department` on same-named columns | only theta join `join[c]` | Section 4.3: "a theta join, not a natural join" |
| Outer joins ⟕ ⟖ ⟗ (they produce NULLs) | not supported | Section 3: nulls are out of scope |
| Division ÷ | not an operator (it can be written with − and ×, see above) | not in section 4.2 |
| Set operators need the same arity and types (the slide pairs `Dept` with `Depar`) | the same attribute **names** in the same order are also required | Section 4.3 defines union compatibility this way |
| RelaX-style definitions `Student = { id, name, ... }` | `Student (id, name, ...) = { ... }` | Section 4.1 syntax |
| Symbols σ π ρ ⨝ | ASCII keywords `select`, `project`, `rename`, `join` | Section 4: the ASCII form is what is tested |

## Errors

Every error names its category and the problem. Errors in the query or in a
data file also show the line and column, with a caret under the spot. A
Python stack trace is never shown.

```
$ python3 ra.py "select[Age>30](R"
Syntax error at line 1, column 17 of query: missing ')' to close the '(' opened at column 15; found end of input
    select[Age>30](R
                    ^
```

| Category | Examples |
|---|---|
| Lexical | unterminated string, `!` without `=`, `3abc`, an unquoted `(` in a data value |
| Syntax | unbalanced parentheses, missing operand, empty `project[]`, chained `a<b<c` |
| Name | unknown relation or attribute, ambiguous bare attribute, relation defined twice |
| Schema | union of incompatible relations, `times` name collision, wrong tuple arity, `project[Name, Name]` |
| Type | number compared with string, a column mixing numbers and strings in a data file |

## Known limitations

* Only the ASCII syntax is accepted. There are no Unicode operator symbols (σ, π, ⋈, …).
* Keywords are lowercase and case-sensitive: `Select` is an identifier.
* A comment starts with `//` only at the beginning of a token, so a bare
  value like `a//b` stays one value.
* There are no nulls, which is out of scope. Every attribute has a value.
* Joins are nested loops (O(n·m)), as the handout asks. There are no indexes,
  hash joins or sort-merge joins. See REPORT.md for what this costs.
* Sources may generate tuples, but execution still materializes inputs
  and intermediate results in memory.
* A deeply nested query (hundreds of levels) hits Python's recursion limit
  and gets a clean error message instead of a result.
