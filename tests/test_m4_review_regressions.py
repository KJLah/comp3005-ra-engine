"""Regression cases found during the Module 4 review."""
import os
import unittest
from collections import Counter
from unittest.mock import patch

from m2_helpers import make_db
from optimizer.estimate import Estimator
from optimizer.planner import naive, optimize
from optimizer.runner import run
from optimizer.stats import ColStats, TableStats, analyze, load_all
from sql.cli import compile_script
from sql.resolve import StorageCatalog
from storage.buffer import BufferPool
from storage.catalog import Catalog
from storage.disk import DiskManager


class Module4ReviewRegressions(unittest.TestCase):
    def test_range_interpolation_includes_bucket_endpoints(self):
        column = ColStats(
            ndv=4, min=1, max=4, width=4.0,
            hist=[[1, 2, 2, 2], [3, 4, 2, 2]],
        )
        table = TableStats("T", 4, 1, 4.0, {"x": column}, True)

        # Expected values follow the documented continuous interpolation
        # model, rather than exact discrete integer selectivities.
        self.assertAlmostEqual(
            Estimator.side(column, table, 2, below=True), 0.5
        )
        self.assertAlmostEqual(
            Estimator.side(column, table, 3, below=False), 0.5
        )

    def test_false_filter_stays_above_global_count(self):
        with make_db({"A": ("x:INT", ["1", "2"])}) as db:
            stats = load_all(db)
            logical = {
                "op": "select",
                "condition": "1 = 0",
                "child": {
                    "op": "group_by",
                    "keys": [],
                    "aggregates": [["count", "*", "n"]],
                    "child": {"op": "scan", "table": "A"},
                },
            }
            baseline = run(db, naive(logical, stats, 8), 8).rows
            actual = run(db, optimize(logical, stats, 8), 8).rows

            self.assertEqual(baseline, [])
            self.assertEqual(actual, baseline)

    def test_three_relation_or_predicate_can_build_complete_plan(self):
        tables = {
            name: ("x:INT", ["1", "2"])
            for name in ("A", "B", "C")
        }
        with make_db(tables) as db:
            logical = compile_script(
                "SELECT A.x, B.x, C.x "
                "FROM A JOIN B ON 1 = 1 "
                "JOIN C ON A.x = C.x OR B.x = C.x;",
                StorageCatalog(db),
            )[0]
            stats = load_all(db)
            baseline = run(db, naive(logical, stats, 8), 8).rows
            actual = run(db, optimize(logical, stats, 8), 8).rows
            expected = Counter([
                (1, 1, 1), (1, 2, 1), (1, 2, 2),
                (2, 1, 1), (2, 1, 2), (2, 2, 2),
            ])

            self.assertEqual(Counter(baseline), expected)
            self.assertEqual(Counter(actual), expected)

    def test_escaped_varchar_statistics_round_trip(self):
        value = "\x01" * 255
        with make_db({"T": ("s:VARCHAR(255)", [value])}) as db:
            result = analyze(db, "T", buckets=20)
            reloaded = load_all(db)["T"]

            self.assertEqual(result.n_tuples, 1)
            self.assertEqual(reloaded.n_tuples, 1)
            self.assertEqual(reloaded.cols["s"].ndv, 1)
            self.assertEqual(reloaded.cols["s"].min, value)
            self.assertEqual(reloaded.cols["s"].max, value)
            self.assertEqual(
                reloaded.cols["s"].hist, [[value, value, 1, 1]]
            )

    def test_analyze_reads_original_data_page_once(self):
        with make_db({
            "T": ("a:INT,b:INT,c:INT", ["1,2,3", "4,5,6"])
        }) as db:
            info = Catalog(db, BufferPool(2)).get("T")
            # This small fixture has directory page 0 and data page 1.
            self.assertEqual(info.num_pages, 2)
            source_path = os.path.realpath(os.path.join(db, info.file))
            data_reads = []
            original_read = DiskManager.read_page

            def tracked_read(disk, page_id):
                if (
                    os.path.realpath(disk.path) == source_path
                    and page_id == 1
                ):
                    data_reads.append(page_id)
                return original_read(disk, page_id)

            with patch.object(DiskManager, "read_page", tracked_read):
                result = analyze(db, "T", buckets=2)

            self.assertEqual(result.n_tuples, 2)
            self.assertEqual(result.n_pages, 1)
            self.assertEqual(
                len(data_reads), 1,
                "ANALYZE must read the original data page once; "
                "per-column sorts should read temporary storage",
            )


if __name__ == "__main__":
    unittest.main()
