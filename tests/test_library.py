import json
import unittest

from ra_engine import ast_nodes as A
from ra_engine.conditions import predicate_for_row
from ra_engine.errors import ParseError, Position, RANameError
from ra_engine.loader import load_text
from ra_engine.operators import evaluate, plan
from ra_engine.parser import parse_condition, parse_query
from ra_engine.relation import Attribute, NUM
from ra_engine.serialization import tree_from_json, tree_to_json


class LibraryTests(unittest.TestCase):
    def test_standalone_condition(self):
        schema = [
            Attribute("R", "b", NUM),
            Attribute("S", "b", NUM),
        ]
        predicate = predicate_for_row(
            parse_condition("R.b = S.b"), schema
        )
        self.assertTrue(predicate((7, 7)))
        self.assertFalse(predicate((7, 8)))

    def test_condition_precedence(self):
        predicate = predicate_for_row(
            parse_condition(
                "not R.b = 1 and S.b = 2 or R.b = 3"
            ),
            [Attribute("R", "b", NUM), Attribute("S", "b", NUM)],
        )
        self.assertTrue(predicate((3, 0)))
        self.assertTrue(predicate((0, 2)))
        self.assertFalse(predicate((1, 2)))
        self.assertFalse(predicate((0, 0)))

    def test_condition_rejects_trailing_tokens(self):
        with self.assertRaises(ParseError):
            parse_condition("R.b = S.b garbage")

    def test_wrong_qualifier_is_rejected(self):
        with self.assertRaises(RANameError):
            predicate_for_row(
                parse_condition("Missing.b = 1"),
                [Attribute("R", "b", NUM)],
            )

    def test_external_source_is_lazy_and_repeatable(self):
        class Source:
            name = "R"
            attributes = [Attribute("R", "a", NUM)]

            def __init__(self):
                self.calls = 0

            def scan(self):
                self.calls += 1
                yield (1,)
                yield (1,)
                yield (2,)

        source = Source()
        op = plan(parse_query("project[R.a](R)"), {"R": source})

        self.assertEqual(source.calls, 0)
        self.assertEqual(evaluate(op).rows, [(1,), (1,), (2,)])
        self.assertEqual(evaluate(op).rows, [(1,), (1,), (2,)])
        self.assertEqual(source.calls, 2)

    def test_bag_join_and_explicit_distinct(self):
        cat = load_text(
            "R(a) = {\n1\n1\n}\n"
            "S(b) = {\n1\n1\n}"
        )
        query = "project[R.a](R join[R.a=S.b] S)"
        self.assertEqual(
            evaluate(plan(parse_query(query), cat)).rows,
            [(1,), (1,), (1,), (1,)],
        )
        self.assertEqual(
            evaluate(plan(parse_query(f"distinct({query})"), cat)).rows,
            [(1,)],
        )

    def test_set_operations_remove_duplicates(self):
        cat = load_text(
            "R(a) = {\n1\n1\n2\n2\n}\n"
            "S(a) = {\n1\n1\n}"
        )
        for query, expected in (
            ("R union S", [(1,), (2,)]),
            ("R intersect S", [(1,)]),
            ("R minus S", [(2,)]),
        ):
            with self.subTest(query=query):
                self.assertEqual(
                    evaluate(plan(parse_query(query), cat)).rows,
                    expected,
                )

    def test_every_node_round_trips(self):
        p = Position(0, 1, 1)
        r = A.RelationRef("R", p)
        s = A.RelationRef("S", p)
        attr = A.Attr("a", "R", p)
        comparison = A.Compare("=", attr, A.Num(1, p), p)

        nodes = [
            A.Num(1, p),
            A.Num(1.5, p),
            A.Str("O'Brien", p),
            attr,
            A.Attr("a", None, p),
            comparison,
            A.And(comparison, comparison, p),
            A.Or(comparison, comparison, p),
            A.Not(comparison, p),
            r,
            A.Select(comparison, r, p),
            A.Project([attr], r, p),
            A.Rename("T", r, p),
            A.Times(r, s, p),
            A.Join(comparison, r, s, p),
            A.Distinct(r, p),
            A.RelationDef(
                "R", ["a"], [p], [[1], [1.5], ["x"]],
                [p, p, p], p,
            ),
        ]
        nodes += [
            A.SetOp(op, r, s, p)
            for op in ("union", "intersect", "minus")
        ]

        for node in nodes:
            with self.subTest(node=node):
                self.assertEqual(
                    tree_from_json(tree_to_json(node)), node
                )

    def test_restored_query_executes(self):
        cat = load_text("R(a) = {\n1\n1\n2\n}")
        original = parse_query(
            "distinct(project[R.a](select[R.a=1](R)))"
        )
        restored = tree_from_json(tree_to_json(original))
        self.assertEqual(
            evaluate(plan(restored, cat)).rows, [(1,)]
        )

    def test_unknown_json_node_is_rejected(self):
        payload = {
            "version": 1,
            "tree": {"node": "Unknown", "fields": {}},
        }
        with self.assertRaises(ValueError):
            tree_from_json(json.dumps(payload))

    def test_wrong_json_child_type_is_rejected(self):
        payload = json.loads(tree_to_json(parse_query("distinct(R)")))
        payload["tree"]["fields"]["child"] = 7

        with self.assertRaises(ValueError):
            tree_from_json(json.dumps(payload))