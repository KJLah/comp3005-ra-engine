"""Module 3: the SQL front end. SQL text goes in, a logical plan comes out.

    lexer      hand-written scanner (no regular expressions)
    parser     recursive descent over GRAMMAR_SQL.md, producing sql.ast nodes
    resolve    scopes, name resolution, aggregation and type rules
    translate  canonical bottom-to-top translation to a logical plan
    logical    logical plan nodes: JSON, explain tree, checklist validator
    cli        sql parse | compile | check | exec
"""