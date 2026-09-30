# GRAMMAR.md — Relational Algebra Query Language

This file defines the language **before** the parser was written. The parser in
`ra_engine/parser.py` has one method per non-terminal below. If the two ever
disagree, the grammar is the specification and the parser is the bug.

Notation (ISO-style EBNF, as on the Wikipedia EBNF page):

| Notation      | Meaning                      |
|---------------|------------------------------|
| `a b`         | `a` followed by `b`          |
| `a \| b`      | `a` or `b`                   |
| `{ a }`       | zero or more `a`             |
| `[ a ]`       | optional `a`                 |
| `( a )`       | grouping                     |
| `"x"`         | the literal token `x`        |
| `UPPER`       | a token produced by the lexer |

---

## 5.1 The grammar

### 5.1.1 Lexical grammar (what the tokenizer produces)

The tokenizer reads one character at a time. It has two modes:

* **query mode**, the default, used for queries and for the header of a
  relation definition.
* **value mode**, entered on `{` and left on `}`. It is used only for the body
  of a relation definition, where values can be written bare (`John`, `D1`)
  and line breaks separate tuples.

```ebnf
(* ---------- shared by both modes ---------- *)
letter      = "A" | ... | "Z" | "a" | ... | "z" | "_" ;
digit       = "0" | ... | "9" ;
NUMBER      = [ "-" ] digit { digit } [ "." digit { digit } ] ;
STRING      = "'" { string_char | "''" } "'" ;      (* '' is one literal quote *)
string_char = ? any character except "'" and newline ? ;
comment     = "//" { ? any character except newline ? } ;   (* discarded *)

(* ---------- query mode ---------- *)
IDENT       = letter { letter | digit } ;           (* unless it is a KEYWORD *)
KEYWORD     = "select" | "project" | "rename"
            | "union" | "intersect" | "minus" | "times" | "join"
            | "and" | "or" | "not" ;
CMP_OP      = "=" | "!=" | "<" | "<=" | ">" | ">=" ;
punctuation = "[" | "]" | "(" | ")" | "," | "." | "{" ;
(* whitespace, including newlines, separates tokens and is otherwise ignored *)

(* ---------- value mode (between "{" and "}") ---------- *)
BARE        = bare_char { bare_char } ;             (* classified below *)
bare_char   = ? any character except whitespace , ' ( ) { } ? ;
NEWLINE     = newline { newline } ;                 (* blank / comment lines collapse *)
(* also: STRING, ",", "}" *)
```

Lexical rules that the EBNF alone does not say:

1. **Maximal munch.** At every point the scanner takes the longest token
   that can start there. On `>` it looks at the next character: `>=` is one
   token, and so is `<=`. `!` is legal only as part of `!=`.
2. **Negative numbers.** `-` followed by a digit starts a NUMBER. There is no
   minus operator in the language, so this never conflicts with anything. In
   `Age>-30` the tokens are `>` and `-30`: the scanner reads `>`, sees that the
   next character is `-` rather than `=`, stops, and then starts a new number
   token. It never produces a `>-` operator.
3. **Numbers must end cleanly.** A NUMBER directly followed by a letter
   (`3abc`) is a lexical error rather than two tokens.
4. **Strings.** A string runs from `'` to the next single `'`. Two quotes in a
   row inside a string mean one literal quote, so `'O''Brien'` has the value
   `O'Brien`. Commas, parentheses, brackets and spaces inside a string are
   ordinary characters. A string may not contain a newline. Reaching a newline
   or the end of input before the closing quote is a lexical error reported at
   the position of the *opening* quote.
5. **Bare values** (value mode only). A BARE run is classified after it is
   read. If it has the shape of NUMBER it becomes a NUMBER. Otherwise it
   becomes a STRING. So `32` and `-5` are numbers, and `E1`, `D1` and `x-ray`
   are strings. A `(` or `)` outside quotes in value mode is a lexical error,
   because the specification says such values must be quoted.
6. **Comments** start with `//` at the beginning of a token and run to the
   end of the line.
7. **Keywords are lowercase and case-sensitive.** `select` is a keyword and
   `Select` is an identifier.

### 5.1.2 Keyword rule (test case 8)

The words `select project rename union intersect minus times join` are
**hard-reserved as relation names** and **soft (non-reserved) as attribute
names**. The words `and or not` are **hard-reserved everywhere**.

The grammar expresses this with the `name` rule, which is used wherever an
attribute name can appear:

```ebnf
name         = IDENT | soft_keyword ;
soft_keyword = "select" | "project" | "rename"
             | "union" | "intersect" | "minus" | "times" | "join" ;
```

Why this is safe: inside `[ ... ]` there is no position where the grammar
could also expect `union`, `join` and so on as operators. In
`select[union=3](R)` the parser is at the start of an `operand` when it sees
`union`, so the only valid reading is "attribute called union". The words
`and`, `or` and `not` cannot be soft, because inside a condition they *are*
expected. For example, `select[not = 1](R)` would be ambiguous between
"attribute `not` equals 1" and "not (…)".

Relation names use plain `IDENT` because at the start of an `atom` both
readings are possible. `select` there must mean the operator.

### 5.1.3 Syntactic grammar — queries

```ebnf
query          = expr EOF ;

(* ---- binary operators, lowest precedence first ---- *)
expr           = union_expr ;
union_expr     = intersect_expr { ( "union" | "minus" ) intersect_expr } ;
intersect_expr = product_expr   { "intersect" product_expr } ;
product_expr   = atom           { ( "times" | "join" "[" condition "]" ) atom } ;

(* ---- unary operators and grouping: the tightest level ---- *)
atom           = "select"  "[" condition "]"  "(" expr ")"
               | "project" "[" attr_list "]"  "(" expr ")"
               | "rename"  "[" IDENT "]"      "(" expr ")"
               | "(" expr ")"
               | IDENT ;                                   (* a relation name *)

attr_list      = attr_ref { "," attr_ref } ;              (* at least one *)
attr_ref       = [ IDENT "." ] name ;                     (* Age  or  employeeTable2.DID *)
```

### 5.1.4 Syntactic grammar — conditions

```ebnf
condition      = or_cond ;
or_cond        = and_cond { "or"  and_cond } ;
and_cond       = not_cond { "and" not_cond } ;
not_cond       = "not" not_cond
               | cond_atom ;
cond_atom      = "(" condition ")"
               | comparison ;
comparison     = operand CMP_OP operand ;
operand        = NUMBER | STRING | attr_ref ;
```

In a condition, an unquoted word is **always an attribute** and never a
string. `select[A=B](R)` compares column A with column B (test case 18). A
string constant must be quoted: `select[DID='D1'](employeeTable1)`.

### 5.1.5 Syntactic grammar — relation definitions

```ebnf
definitions    = { relation_def } EOF ;
relation_def   = IDENT "(" name { "," name } ")" "=" "{" body "}" ;
body           = [ NEWLINE ] [ tuple { NEWLINE tuple } [ NEWLINE ] ] ;
tuple          = value { "," value } ;
value          = NUMBER | STRING ;          (* STRING includes classified BARE runs *)
```

These semantic checks are made after parsing, while the relation is loaded:

* every tuple has exactly as many values as the header has attributes
  (otherwise a schema error);
* attribute names in one header are distinct (otherwise a schema error);
* all non-empty columns hold a single type: every value is a number or every
  value is a string (otherwise a type error);
* duplicate tuples collapse to one, because a relation is a set;
* two definitions with the same relation name are a name error.

### 5.1.6 The language this grammar generates

`query` generates all finite strings built from relation names, the three
bracketed unary operators and the five infix binary operators, with balanced
parentheses and brackets. Each bracket holds a well-formed boolean
combination of comparisons (for `select` and `join`), a non-empty list of
possibly-qualified attribute names (for `project`), or a single identifier
(for `rename`). The grammar alone cannot express some rules: that names
exist, that schemas are union-compatible, and that comparisons are well-typed.
Those are *static semantic* checks, made after parsing and before any tuple
is touched.

---

## 5.2 Precedence and associativity

| Level (1 = binds tightest) | Operators | Associativity | Enforced by rule |
|---|---|---|---|
| 1 | `select[…](…)`, `project[…](…)`, `rename[…](…)`, `( … )` | n/a (the input is always parenthesised) | `atom` |
| 2 | `times`, `join[c]` | left | `product_expr` |
| 3 | `intersect` | left | `intersect_expr` |
| 4 | `union`, `minus` | left | `union_expr` |

| Level (1 = binds tightest) | Condition operators | Associativity | Enforced by rule |
|---|---|---|---|
| 1 | `=  !=  <  <=  >  >=` | none: `a < b < c` is a syntax error | `comparison` |
| 2 | `not` | prefix (right) | `not_cond` |
| 3 | `and` | left | `and_cond` |
| 4 | `or` | left | `or_cond` |

**How the grammar enforces this.** Each level is its own non-terminal, and
each level's operands come from the next-tighter level. A `union_expr` is a
list of `intersect_expr`s joined by `union`/`minus`, so an `intersect` can
only ever sit *inside* an operand of `union`, never above it. This is
*stratification*: precedence is fixed by which rule calls which, not by any
special case in the parser.

**How associativity is enforced.** Each rule is written with repetition,
`X { op X }`. The parser builds the tree while it loops: every new
operator takes the tree built so far as its left child. So
`A minus B minus C` becomes `(A minus B) minus C`. See section 5.4 for why this
is equivalent to the left-recursive form `union_expr = union_expr "minus"
intersect_expr | intersect_expr`, which gives the same associativity.

**Why these choices.**

* `times`/`join` are the product-like operators and `union` is the
  sum-like one. Giving product the tighter binding matches the convention
  from arithmetic, so `A union B times C`
  means `A union (B times C)`.
* `intersect` sits between them, in the same way that ∧ binds tighter than ∨ in
  logic. `A union B intersect C` means `A union (B intersect C)`.
* `union` and `minus` share a level, like `+` and `-`. Mixing them
  therefore groups purely **left to right**:
  * **Test 10:** `A union B minus C` ⇒ `(A union B) minus C`
  * **Test 11:** `A minus B minus C` ⇒ `(A minus B) minus C`
* Left associativity is the only sensible choice for `minus`, because it is
  what people expect from `a - b - c` in arithmetic.

---

## 5.3 Ambiguity demonstration

The deliberately naive grammar:

```ebnf
Expr ::= Expr "union" Expr
       | Expr "minus" Expr
       | "(" Expr ")"
       | IDENT
```

### Two parse trees for `A union B minus C`

**Tree 1**: the top rule is `Expr "minus" Expr`, and its left `Expr` expands
to `Expr "union" Expr`:

```
            Expr
         /   |    \
      Expr "minus" Expr
    /  |   \         |
 Expr "union" Expr  IDENT(C)
  |           |
IDENT(A)   IDENT(B)
```
This means **(A union B) minus C**.

**Tree 2**: the top rule is `Expr "union" Expr`, and its right `Expr` expands
to `Expr "minus" Expr`:

```
        Expr
      /   |    \
   Expr "union" Expr
    |          /  |   \
 IDENT(A)   Expr "minus" Expr
             |            |
          IDENT(B)     IDENT(C)
```
This means **A union (B minus C)**.

Both trees are valid derivations of the same token string, so the grammar is
**ambiguous**.

### A data instance where the trees disagree

All three relations have a single attribute `x`:

| A | B | C |
|---|---|---|
| x | x | x |
| 1 | 2 | 1 |

* **Tree 1:** (A ∪ B) − C = {1, 2} − {1} = **{2}**
* **Tree 2:** A ∪ (B − C) = {1} ∪ {2} = **{1, 2}**

The results differ, so the ambiguity is not just cosmetic. The choice of tree
changes the answer. (These relations are in `tests/data/ambiguity.ra` and the
test suite checks the result.)

### Test 11: `A minus B minus C`, where associativity matters

| A | B | C |
|---|---|---|
| x | x | x |
| 1 | 1 | 1 |
| 2 |   |   |
| 3 |   |   |

* **Left**, (A − B) − C = {2, 3} − {1} = **{2, 3}**. This is what our grammar produces.
* **Right**, A − (B − C) = {1,2,3} − {} = **{1, 2, 3}**

(In `tests/data/ambiguity.ra` these three are named `A2`, `B2` and `C2`.
`test_semantics.py` runs both groupings and checks both answers.)

### The stratified grammar that removes the ambiguity

```ebnf
Expr    = Primary { ( "union" | "minus" ) Primary } ;
Primary = "(" Expr ")" | IDENT ;
```

(This is our real `union_expr` with the intersect and product levels
omitted.) There is now exactly one way to derive `A union B minus C`: `Expr` is
a flat sequence `Primary op Primary op Primary`, and the rule is read as
"fold from the left". It **forces Tree 1**, `(A union B) minus C`. Tree 2
cannot be derived, because a `Primary` can only contain a `union` or `minus` if
that operator is wrapped in explicit parentheses. The user who wants Tree 2
writes `A union (B minus C)` (test 15).

Written in pure BNF without `{ }`, the same grammar is the left-recursive
`Expr ::= Expr "union" Primary | Expr "minus" Primary | Primary`. Its right
operand is a `Primary`, never an `Expr`, and that restriction is exactly what
kills Tree 2.

---

## 5.4 Parsing strategy

**Strategy: hand-written recursive descent (LL(1) with one spot of LL(2)).**
There is one method per non-terminal (`union_expr()`, `intersect_expr()`,
`product_expr()`, `atom()`, `or_cond()`, …). Each method decides what to do by
looking at the current token.

Why recursive descent:

* The grammar is small and already stratified, so the code mirrors it one
  to one. That makes it easy to check against this document and to explain.
* Error messages are easy to make precise. Each method knows exactly what it
  expected (for example, "expected ')' to close the '(' opened at column 15"),
  and each token carries its position.
* The assignment forbids parser generators, and a hand-written LR table would
  be much harder to get right and to explain.

**What left recursion does to it.** A rule like
`union_expr = union_expr "union" intersect_expr | intersect_expr` would be
coded as a method `union_expr()` whose first action is to call
`union_expr()` again, *without consuming a token*. The recursion never makes
progress, so Python hits its recursion limit (infinite recursion).

**Where we removed it.** Every binary level uses the repetition form
instead:

* `union_expr     = intersect_expr { ( "union" | "minus" ) intersect_expr }`
* `intersect_expr = product_expr   { "intersect" product_expr }`
* `product_expr   = atom           { ( "times" | "join" "[" condition "]" ) atom }`
* `or_cond        = and_cond       { "or" and_cond }`
* `and_cond       = not_cond       { "and" not_cond }`

This is the standard rewrite `A = A α | β` ⇒ `A = β { α }`. The `{ }`
becomes a `while` loop. The loop builds the tree left-deep (`left =
Node(op, left, right)`), which is why the operators stay **left-associative**
even though the grammar no longer *looks* left-recursive. `not_cond` is
right-recursive (`"not" not_cond`), which is harmless, because the method
consumes `not` before it recurses.

**Lookahead.** Every choice is decided by one token, except `attr_ref`. There
the parser needs to know whether an `IDENT` is followed by `.`, which is
LL(2) (a peek at the next token). `cond_atom` is LL(1) because an `operand`
can never start with `(`.

---

## 5.5 Sources

* Robert Nystrom, *Crafting Interpreters*, ch. 4 "Scanning" and ch. 6
  "Parsing Expressions". The scanner loop and the one-method-per-precedence-level
  structure follow these chapters.
* Wikipedia: "Extended Backus–Naur form", "Recursive descent parser",
  "Maximal munch", "Operator-precedence parser", "Left recursion".
* Aho, Lam, Sethi, Ullman, *Compilers* (Dragon Book), §2.2–2.4 (syntax
  definition, parse trees, ambiguity, associativity) and §4.4 (top-down
  parsing, eliminating left recursion).
* Relax, dbis-uibk.github.io/relax, used to compare behaviour on self joins
  and set operations.

### Where AI assistance was wrong

I used AI assistance (Claude) throughout. These are the places where it
was wrong, overconfident or incomplete. Each one has a dated entry in
`DESIGN_LOG.md`.

1. **Its runtime prediction was wrong.** From the 16000 run (about 110 ns
   per comparison), the AI predicted the 64000 join would take about
   7.5 minutes. It took 610.6 s (10.2 minutes), 35% longer, because the cost
   per comparison is not constant: it rises to 126 ns at 32000 and 149 ns at
   64000. I found this by running the benchmark. REPORT.md now extrapolates
   from the largest measured size and treats the million-tuple estimate as a
   lower bound.
2. **Its instructions were incomplete.** The checklist told me to run
   `python3 ra.py -d data/employees.ra` but did not say to `cd` into the
   project first. Run from my home folder, it failed with "can't open file".
   I found it by running the command, and the step is now in the checklist.
3. **Its UI text was misleading.** The interactive shell said "type
   '.tree <query>'". I typed `<query>` literally and got a syntax error. The
   banner now shows a concrete example.
4. **Its code could still show a stack trace.** When the output was piped
   into `head` and the pipe closed early, `ra.py` printed a `BrokenPipeError`
   traceback, which breaks section 6.3. The AI hit this during its own
   testing. It is now caught, and there is a test for it.
