#!/bin/sh
# Module 3 completeness check. Run from the repo root:  sh check_m3.sh
# It only reads files; it changes nothing.
ok=0; bad=0
have() { if [ -e "$1" ]; then ok=$((ok+1)); else echo "MISSING  $1"; bad=$((bad+1)); fi; }
has()  { if grep -q "$2" "$1" 2>/dev/null; then ok=$((ok+1)); else echo "CHECK    $1: $3"; bad=$((bad+1)); fi; }

for f in __init__ __main__ ast cli errors lexer logical parser printer resolve \
         statements translate; do
  have "sql/$f.py"
done
for f in m3_helpers test_m3_lexer test_m3_parser test_m3_logical test_m3_resolve \
         test_m3_rules test_m3_translate test_m3_cli; do
  have "tests/$f.py"
done
have queries/setup.sql
n=$(ls queries/case*.sql 2>/dev/null | wc -l | tr -d ' ')
[ "$n" = "16" ] && ok=$((ok+1)) || { echo "CHECK    queries/: expected 16 case files, found $n"; bad=$((bad+1)); }
have GRAMMAR_SQL.md; have DESIGN_M3.md; have DESIGN_LOG.md

has pyproject.toml 'sql = "sql.cli:main"' "missing the sql script line"
has pyproject.toml '"sql\*"' "sql* not in the packages include list"

if python3 - <<'PY'
from ra_engine.lexer import tokenize
from ra_engine.parser import parse_query

assert any(t.text == "<>" for t in tokenize("a<>b"))
assert parse_query("select[a<>1](R)").cond.op == "!="
PY
then
  ok=$((ok+2))
else
  echo "CHECK    Bonus 1 <> tokenization or comparison parsing failed"
  bad=$((bad+1))
fi

has DESIGN_LOG.md "Module 3 step 1, the SQL scanner" "Module 3 entries not appended"
has README.md "## Module 3: SQL front end" "README has no Module 3 section"

echo "----"
echo "$ok checks passed, $bad problems"

[ "$bad" -eq 0 ]