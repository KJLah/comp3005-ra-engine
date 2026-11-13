import unittest
import weakref
from unittest.mock import patch

from execution.hashing import BucketTable
from m2_helpers import make_db, run_plan, scan


def join(op, left, right, condition):
    return {
        "op": op,
        "left": left,
        "right": right,
        "condition": condition,
    }


class ReviewRegressions(unittest.TestCase):
    def test_right_nested_joins_at_b4(self):
        tables = {
            t: (
                "a:INT,pad:VARCHAR(200)",
                [f"{i}," + t * 200 for i in range(n)],
            )
            for t, n in (("R", 120), ("S", 80), ("T", 60))
        }
        algorithms = (
            "nested_loop_join",
            "sort_merge_join",
            "hash_join",
        )

        with make_db(tables) as db:
            for outer in algorithms:
                for inner in algorithms:
                    with self.subTest(outer=outer, inner=inner):
                        right = join(
                            inner, scan("S"), scan("T"), "S.a=T.a"
                        )
                        plan = join(
                            outer, scan("R"), right, "R.a=S.a"
                        )
                        result = run_plan(db, plan, 4)

                        expected = [
                            (
                                i, "R" * 200,
                                i, "S" * 200,
                                i, "T" * 200,
                            )
                            for i in range(60)
                        ]
                        self.assertCountEqual(result.rows, expected)

    def test_limit_counts_only_executed_bnl_predicates(self):
        tables = {
            "R": ("a:INT", ["1"] * 5),
            "S": ("a:INT", ["1"]),
        }

        with make_db(tables) as db:
            child = join(
                "nested_loop_join",
                scan("R"), scan("S"), "R.a=S.a",
            )
            result = run_plan(
                db, {"op": "limit", "n": 1, "child": child}, 4
            )

            self.assertEqual(result.rows, [(1, 1)])
            self.assertEqual(
                result.node("plan.child")["comparisons"], 1
            )

    def test_limit_counts_only_executed_hash_fallback_comparisons(self):
        tables = {
            "R": (
                "a:INT,pad:VARCHAR(100)",
                ["1," + "r" * 100] * 300,
            ),
            "S": (
                "a:INT,pad:VARCHAR(100)",
                ["1," + "s" * 100] * 100,
            ),
        }

        with make_db(tables) as db:
            child = join(
                "hash_join",
                scan("R"), scan("S"), "R.a=S.a",
            )
            result = run_plan(
                db, {"op": "limit", "n": 1, "child": child}, 4
            )

            self.assertEqual(len(result.rows), 1)
            self.assertEqual(
                result.node("plan.child")["comparisons"], 1
            )

    def test_hash_distinct_releases_completed_partition_table(self):
        live = weakref.WeakSet()

        class CheckedTable(BucketTable):
            def __init__(self, *args, **kwargs):
                if any(t.used for t in live):
                    raise AssertionError(
                        "previous partition table still holds rows"
                    )
                super().__init__(*args, **kwargs)
                live.add(self)

        rows = [f"{i}," + "x" * 100 for i in range(300)]

        with make_db({"R": ("a:INT,pad:VARCHAR(100)", rows)}) as db:
            with patch(
                "execution.aggregate.BucketTable", CheckedTable
            ):
                result = run_plan(
                    db,
                    {
                        "op": "distinct",
                        "mode": "hash",
                        "child": scan("R"),
                    },
                    4,
                )

            self.assertCountEqual(
                result.rows,
                [(i, "x" * 100) for i in range(300)],
            )