"""Module 1 section 6: CSV loader."""
import tempfile
import unittest

from storage.buffer import BufferPool
from storage.catalog import Catalog
from storage.csvload import load_csv, split_line
from storage.errors import LoadError
from storage.record import decode, parse_schema

SCHEMA = parse_schema("a:INT,b:VARCHAR(10),c:INT")


class SplitLine(unittest.TestCase):
    def test_plain(self):
        self.assertEqual(split_line("42,bob,7", 1), ["42", "bob", "7"])

    def test_quoted_comma_and_doubled_quote(self):
        self.assertEqual(split_line('1,"Smith, J",2', 1), ["1", "Smith, J", "2"])
        self.assertEqual(split_line('1,"say ""hi""",2', 1), ["1", 'say "hi"', "2"])

    def test_empty_fields(self):
        self.assertEqual(split_line("1,,2", 1), ["1", "", "2"])
        self.assertEqual(split_line('1,"",2', 1), ["1", "", "2"])

    def test_unterminated_quote(self):
        with self.assertRaises(LoadError) as cm:
            split_line('1,"abc,2', 5)
        self.assertIn("line 5", str(cm.exception))


class Loading(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.cat = Catalog(self.tmp.name, BufferPool(4))
        self.cat.create_table("R", SCHEMA)

    def tearDown(self):
        self.tmp.cleanup()

    def rows(self):
        return [decode(SCHEMA, r) for _, r in self.cat.open_heap("R").scan()]

    def test_load_and_counts(self):
        n = load_csv(self.cat, "R", ["42,bob,7", '43,"amy, k",-9', ""])
        self.assertEqual(n, 2)
        self.assertEqual(self.rows(), [(42, "bob", 7), (43, "amy, k", -9)])
        self.assertEqual(self.cat.get("R").num_tuples, 2)

    def test_crlf_and_spaces_around_int(self):
        load_csv(self.cat, "R", ["1 , x ,2\r"])
        self.assertEqual(self.rows(), [(1, " x ", 2)])

    def test_bad_int_names_line_and_column_and_loads_nothing(self):
        with self.assertRaises(LoadError) as cm:
            load_csv(self.cat, "R", ["1,a,1", "2,b,2", "3,c,x7"])
        self.assertIn("line 3", str(cm.exception))
        self.assertIn("'c'", str(cm.exception))
        self.assertEqual(self.rows(), [])

    def test_varchar_too_long(self):
        with self.assertRaises(LoadError) as cm:
            load_csv(self.cat, "R", ["1,abcdefghijk,1"])
        self.assertIn("line 1, column 'b'", str(cm.exception))

    def test_varchar_length_is_in_bytes(self):
        load_csv(self.cat, "R", ["1,éééé,1"])            # 8 bytes: fits VARCHAR(10)
        with self.assertRaises(LoadError):
            load_csv(self.cat, "R", ["1,ééééé é,1"])     # 11 bytes

    def test_int_out_of_range(self):
        with self.assertRaises(LoadError):
            load_csv(self.cat, "R", ["2147483648,x,1"])
        load_csv(self.cat, "R", ["-2147483648,x,2147483647"])

    def test_wrong_number_of_fields(self):
        with self.assertRaises(LoadError) as cm:
            load_csv(self.cat, "R", ["1,x"])
        self.assertIn("expected 3 fields, found 2", str(cm.exception))

    def test_empty_line_in_the_middle_is_an_error(self):
        with self.assertRaises(LoadError) as cm:
            load_csv(self.cat, "R", ["1,x,1", "", "2,y,2"])
        self.assertIn("line 2", str(cm.exception))

    def test_record_too_long_names_table_and_line(self):
        wide = parse_schema(",".join(f"c{i}:VARCHAR(255)" for i in range(16)))
        self.cat.create_table("W", wide)
        line = ",".join(["x" * 255] * 16)               # 16 * 257 = 4112 bytes
        with self.assertRaises(LoadError) as cm:
            load_csv(self.cat, "W", [line])
        self.assertIn("table W, line 1", str(cm.exception))

    def test_large_load_spans_many_pages(self):
        load_csv(self.cat, "R", [f"{i},name{i},{i * 2}" for i in range(5000)])
        info = self.cat.get("R")
        self.assertEqual(info.num_tuples, 5000)
        self.assertGreater(info.num_pages, 10)
        self.assertEqual(self.rows()[4999], (4999, "name4999", 9998))
