# GRAMMAR_SQL.md: the SQL subset of Module 3

This is the grammar `sql/lexer.py` and `sql/parser.py` implement. The parser
is recursive descent with one method per non-terminal below, so the method
names in the code match the rule names here.

Notation: `{ x }` is zero or more, `[ x ]` is optional, `|` separates
alternatives, `"x"` is a literal token, UPPER CASE words are keywords.

## 1. Lexical rules

| Token | Rule |
|---|---|
| keyword | One of the words in the keyword list below, in any letter case. `SELECT`, `select` and `SeLeCt` are the same token. |
| ident | `letter { letter | digit }` where letter is `a-z`, `A-Z` or `_`. Case sensitive: `Emp` and `emp` differ. A word in the keyword list is never an identifier. |
| int | `[ "+" | "-" ] digit { digit }`. The sign belongs to the number only when a digit follows it at once. There are no decimals. |
| string | `'` any characters except newline `'`. Two quotes inside (`''`) are one literal quote. |
| op | `=`, `<>`, `<`, `<=`, `>`, `>=`, by maximal munch: `<=`, `>=` and `<>` are single tokens. |
| punctuation | `(` `)` `,` `.` `;` `*` |
| comment | `--` to the end of the line, skipped. |
| whitespace | space, tab, carriage return, newline, skipped. |

Keywords: SELECT DISTINCT FROM WHERE GROUP BY HAVING ORDER ASC DESC LIMIT AS
JOIN ON AND OR NOT UNION ALL INTERSECT EXCEPT CREATE TABLE INSERT INTO VALUES
INT VARCHAR COUNT SUM AVG MIN MAX.

Any other character is a lexical error, with a hint for the common slips
(`!=` instead of `<>`, double quotes instead of single quotes).

## 2. EBNF

```ebnf
script       ::= [ statement ] { ";" [ statement ] } EOF
statement    ::= create | insert | query

create       ::= CREATE TABLE ident "(" coldef { "," coldef } ")"
coldef       ::= ident ( INT | VARCHAR "(" int ")" )
insert       ::= INSERT INTO ident VALUES "(" literal { "," literal } ")"
literal      ::= int | string

query        ::= select_block { setop select_block }
setop        ::= UNION [ ALL ] | INTERSECT | EXCEPT

select_block ::= SELECT [ DISTINCT ] select_list
                 FROM from_list
                 [ WHERE condition ]
                 [ GROUP BY col_list [ HAVING condition ] ]
                 [ ORDER BY sort_list ]
                 [ LIMIT int ]
               | "(" query ")"

select_list  ::= "*" | select_item { "," select_item }
select_item  ::= ( column | aggregate ) [ AS ident ]
aggregate    ::= ( COUNT | SUM | AVG | MIN | MAX ) "(" ( column | "*" ) ")"

from_list    ::= table_ref { "," table_ref }
               | table_ref JOIN table_ref ON condition { JOIN table_ref ON condition }
table_ref    ::= ident [ [ AS ] ident ]
               | "(" query ")" [ AS ] ident

col_list     ::= column { "," column }
sort_list    ::= sort_item { "," sort_item }
sort_item    ::= column [ ASC | DESC ]

condition    ::= and_cond { OR and_cond }
and_cond     ::= not_cond { AND not_cond }
not_cond     ::= NOT not_cond | atom
atom         ::= "(" condition ")" | comparison
comparison   ::= operand compop operand
compop       ::= "=" | "<>" | "<" | "<=" | ">" | ">="
operand      ::= column | int | string | aggregate
column       ::= ident [ "." ident ]
```

## 3. Precedence and associativity

| Level (tightest first) | Operators | Associativity | Where the grammar enforces it |
|---|---|---|---|
| 1 | comparison `= <> < <= > >=` | none (exactly one per comparison) | `comparison` takes one operator between two operands |
| 2 | `NOT` | prefix, nests (`NOT NOT c`) | `not_cond ::= NOT not_cond \| atom` |
| 3 | `AND` | left | `and_cond ::= not_cond { AND not_cond }` |
| 4 | `OR` | left | `condition ::= and_cond { OR and_cond }` |
| 5 | `UNION`, `UNION ALL`, `INTERSECT`, `EXCEPT` | left, all one level | `query ::= select_block { setop select_block }` |

So `NOT a = 1 AND b = 2 OR c = 3` is `((NOT a = 1) AND b = 2) OR c = 3`,
and `A UNION B EXCEPT C` is `(A UNION B) EXCEPT C`. Parentheses override
both: `atom ::= "(" condition ")"` and `select_block ::= "(" query ")"`.

Each `{ ... }` repetition is consumed by a loop that folds to the left
(`left = Node(left, right)`), which is what makes AND, OR and the set
operators left associative. No precedence table or special case exists in
the parser code.

## 4. Where this grammar differs from the sketch in Section 2, and why

1. **`script`, `literal`, `col_list`, `sort_list`, `compop` are written out.**
   The sketch uses them without defining them. `sort_item` defaults to ASC.
2. **`AS` is optional in `table_ref`.** The handout's own examples write
   `FROM Emp e` and `Emp e JOIN Dept d`, which the sketch (`ident [ AS ident ]`)
   would reject. A subquery still must have an alias; only the word AS is
   optional. `AS` stays required in `select_item`, so `SELECT a b FROM R` is
   a syntax error that points at `b` instead of silently renaming `a`.
3. **`operand` includes `aggregate`.** Example 5.4 writes `HAVING COUNT(*) > 5`,
   which the sketch's `operand ::= column | int | string` cannot parse. The
   grammar accepts an aggregate anywhere a column can appear in a condition,
   and the resolver rejects it in WHERE and in JOIN ... ON with an aggregate
   error (required case 7). Rejecting it in the parser would give a syntax
   error instead of the aggregate error the handout asks for.
4. **`condition` is spelled out as four rules.** The sketch says "disjunction
   of conjunctions of comparisons, with NOT and parentheses"; the four rules
   are that sentence with the precedence built in.
5. **Commas and JOIN do not mix in one FROM list**, exactly as in the sketch.
   Mixing them gets a syntax error that says so.
6. **ORDER BY and LIMIT belong to the block they follow.** This follows from
   the sketch: in `SELECT ... UNION SELECT ... ORDER BY a` the ORDER BY is
   part of the second block. To sort a whole set operation, put it in a
   subquery: `SELECT * FROM (... UNION ...) AS u ORDER BY u.a`.
7. **HAVING without GROUP BY is a syntax error**, as in the sketch; the
   parser says so in the message.
8. **Aggregate names are reserved words.** `COUNT`, `SUM`, `AVG`, `MIN` and
   `MAX` are keywords, so they cannot be column or table names. This keeps the
   grammar LL(1): after an operand begins, one token decides the rule.
9. **Empty statements are allowed** (`;;` and a trailing `;`), so a file can
   end with or without a semicolon.

## 5. Not in the subset

Arithmetic, `NULL`, `IN`, `LIKE`, `BETWEEN`, `OUTER JOIN`, subqueries outside
FROM, `SELECT t.*`, column aliases without `AS`, `ORDER BY` on an aggregate
call (use its alias instead), and decimals.
