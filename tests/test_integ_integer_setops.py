"""Integer set operations across the SQL and execution type boundary."""
import sqlite3
import unittest
from collections import Counter

from execution.errors import BindError
from execution.schema import BIGINT
from m2_helpers import make_db, run_plan
from shell.session import Session


class IntegerSetOperations(unittest.TestCase):
    def test_sum_and_base_integer_set_operations_in_both_orders(self):
        with make_db({'T': ('x:INT', ['1', '2'])}) as db:
            session = Session(db, 4, out=None, print_rows=False)
            with sqlite3.connect(':memory:') as ref:
                ref.execute('CREATE TABLE T (x INTEGER)')
                ref.executemany('INSERT INTO T VALUES (?)', [(1,), (2,)])
                for op in ('UNION', 'UNION ALL', 'INTERSECT', 'EXCEPT'):
                    for left, right in (
                        ('SELECT SUM(x) AS n FROM T', 'SELECT x AS n FROM T'),
                        ('SELECT x AS n FROM T', 'SELECT SUM(x) AS n FROM T'),
                    ):
                        query = f'{left} {op} {right}'
                        with self.subTest(query=query):
                            actual = session.execute(query)
                            self.assertEqual(Counter(actual.rows), Counter(ref.execute(query)))
                            self.assertEqual(actual.types, [BIGINT])

    def test_sum_and_count_union_all(self):
        with make_db({'T': ('x:INT', ['1', '2'])}) as db:
            result = Session(db, 4, out=None, print_rows=False).execute(
                'SELECT SUM(x) AS n FROM T UNION ALL SELECT COUNT(*) AS n FROM T'
            )
            self.assertEqual(Counter(result.rows), Counter([(3,), (2,)]))
            self.assertEqual(result.types, [BIGINT])

    def test_wide_sum_survives_sort_above_integer_union(self):
        maximum = 2147483647
        with make_db({'T': ('x:INT', [str(maximum), str(maximum)])}) as db:
            result = Session(db, 4, out=None, print_rows=False).execute(
                'SELECT u.n FROM (SELECT x AS n FROM T UNION ALL '
                'SELECT SUM(x) AS n FROM T) u ORDER BY u.n'
            )
            self.assertEqual(result.rows, [(maximum,), (maximum,), (maximum * 2,)])
            self.assertEqual(result.types, [BIGINT])

    def test_int_and_real_are_still_rejected_by_executor(self):
        with make_db({'T': ('x:INT', ['1', '2'])}) as db:
            plan = {
                'op': 'union_all',
                'left': {'op': 'seqscan', 'table': 'T'},
                'right': {'op': 'group_by', 'mode': 'sort', 'keys': [],
                          'aggregates': [['avg', 'T.x']],
                          'child': {'op': 'seqscan', 'table': 'T'}},
            }
            with self.assertRaises(BindError):
                run_plan(db, plan, 4)


if __name__ == '__main__':
    unittest.main()
