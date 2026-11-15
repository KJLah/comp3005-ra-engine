"""Module 4 Section 1: ANALYZE and the statistics layout."""
import hashlib
import os
import unittest

from storage.buffer import BufferPool
from storage.catalog import Catalog

from optimizer import stats as S

from m2_helpers import make_db


def digest(d):
    out = {}
    for f in sorted(os.listdir(d)):
        with open(os.path.join(d, f), "rb") as fh:
            out[f] = hashlib.md5(fh.read()).hexdigest()
    return out


class Histogram(unittest.TestCase):
    def test_equi_depth_cuts_at_every_n_over_k(self):
        h = S.build_histogram(range(100), 100, 4)
        self.assertEqual(h, [[0, 24, 25, 25], [25, 49, 25, 25], [50, 74, 25, 25], [75, 99, 25, 25]])

    def test_equal_values_never_straddle_buckets(self):
        vals = sorted([1] * 10 + [2] * 10 + [3] * 50 + [4] * 30)
        h = S.build_histogram(vals, 100, 4)
        self.assertEqual(sum(b[2] for b in h), 100)
        self.assertEqual(sum(b[3] for b in h), 4)          # bucket ndv add up to ndv
        for a, b in zip(h, h[1:]):
            self.assertLess(a[1], b[0])

    def test_dense_regions_get_narrow_buckets(self):
        vals = sorted(list(range(0, 10)) * 9 + list(range(10, 1000, 99)))
        h = S.build_histogram(vals, len(vals), 5)
        widths = [b[1] - b[0] for b in h]
        self.assertLess(widths[0], widths[-1])


class Analyze(unittest.TestCase):
    ROWS = [f"{i},{i % 7},{['Ottawa', 'Hull', 'Toronto'][i % 3]}{'x' * (i % 5)}" for i in range(1, 1201)]

    def setUp(self):
        self.tmp = make_db({"T": ("a:INT,b:INT,c:VARCHAR(20)", self.ROWS)})
        self.db = self.tmp.name

    def tearDown(self):
        self.tmp.cleanup()

    def test_exact_values(self):
        ts = S.analyze(self.db, "T", buckets=10)
        self.assertEqual(ts.n_tuples, 1200)
        info = Catalog(self.db, BufferPool(2)).get("T")
        self.assertEqual(ts.n_pages, info.num_pages - 1)     # directory page excluded
        total = sum(4 + 4 + 2 + len(r.split(",")[2]) for r in self.ROWS)
        self.assertAlmostEqual(ts.width, total / 1200, places=2)
        a, b, c = ts.cols["a"], ts.cols["b"], ts.cols["c"]
        self.assertEqual((a.ndv, a.min, a.max), (1200, 1, 1200))
        self.assertEqual((b.ndv, b.min, b.max), (7, 0, 6))
        self.assertEqual(c.ndv, 15)
        self.assertEqual(len(a.hist), 10)
        self.assertTrue(all(bk[2] == 120 for bk in a.hist))
        self.assertEqual(sum(bk[2] for bk in b.hist), 1200)

    def test_analyze_twice_changes_nothing(self):
        S.analyze(self.db, "T")
        first = digest(self.db)
        S.analyze(self.db, "T")
        self.assertEqual(digest(self.db), first)

    def test_external_sort_with_few_frames_gives_the_same_statistics(self):
        big = S.analyze(self.db, "T", frames=16)
        small = S.analyze(self.db, "T", frames=3)       # many runs, merge passes
        self.assertEqual(big.cols["c"].hist, small.cols["c"].hist)

    def test_stored_in_catalog_and_stats_file(self):
        S.analyze(self.db, "T", buckets=8)
        info = Catalog(self.db, BufferPool(2)).get("T")
        self.assertEqual(info.stats["buckets"], 8)
        self.assertTrue(os.path.exists(os.path.join(self.db, "T.stats")))
        again = S.load_all(self.db)["T"]
        self.assertTrue(again.analyzed)
        self.assertEqual(again.cols["b"].ndv, 7)

    def test_defaults_without_analyze(self):
        ts = S.load_all(self.db)["T"]
        self.assertFalse(ts.analyzed)
        self.assertEqual(ts.n_tuples, 1200)
        self.assertEqual(ts.cols["a"].ndv, 120)                 # n / 10
        self.assertIsNone(ts.cols["a"].min)

    def test_a_column_too_big_for_one_record_is_split(self):
        rows = [f"{i},{'v%03d' % i + 'y' * 150}" for i in range(400)]
        tmp = make_db({"W": ("a:INT,s:VARCHAR(200)", rows)})
        try:
            ts = S.analyze(tmp.name, "W", buckets=100)
            back = S.load_all(tmp.name)["W"]
            self.assertEqual(back.cols["s"].hist, ts.cols["s"].hist)
            self.assertEqual(len(back.cols["s"].hist), 100)
        finally:
            tmp.cleanup()


if __name__ == "__main__":
    unittest.main()