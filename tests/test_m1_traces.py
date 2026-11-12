"""Module 1 section 8: our own trace files (make_traces.py)."""
import os
import sys
import unittest

ROOT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..")
sys.path.insert(0, ROOT)

import make_traces  # noqa: E402
from storage.buffer import run_trace  # noqa: E402


class Traces(unittest.TestCase):
    def test_t1_is_the_5_3_trace(self):
        ids = make_traces.t1_worked()
        self.assertEqual(run_trace(ids, 3, "lru")["hits"], 1)
        self.assertEqual(run_trace(ids, 3, "clock")["hits"], 2)

    def test_t3_sequential_flooding(self):
        ids = make_traces.t3_scan80x10()
        self.assertEqual(len(ids), 800)
        self.assertEqual(run_trace(ids, 64, "lru")["hits"], 0)      # 80 pages > 64 frames
        self.assertEqual(run_trace(ids, 256, "lru")["hits"], 720)   # all fit: only 80 misses

    def test_traces_never_change(self):
        for make in make_traces.TRACES.values():
            self.assertEqual(make(), make())
        # pinned values: if these change, every number in REPORT_M1.md is stale
        self.assertEqual(make_traces.t2_hotcold()[:5], [10, 10, 19, 15, 14])
        self.assertEqual(make_traces.t4_uniform()[:5], [171, 3, 41, 73, 94])
        self.assertEqual(len(set(make_traces.t5_hot_plus_scan())), 510)