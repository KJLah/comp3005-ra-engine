"""Module 1 sections 2 and 3: record encoding and the slotted page."""
import unittest

from storage.errors import PageError, RecordError
from storage.page import PAGE_SIZE, SlottedPage
from storage.record import decode, encode, parse_schema

SCHEMA = parse_schema("a:INT,b:VARCHAR(5),c:INT")


def empty_page(page_id=0):
    return SlottedPage.init(bytearray(PAGE_SIZE), page_id)


def state(page):
    return page.slot_count, page.free_end, page.free_space()


class RecordFormat(unittest.TestCase):
    def test_section_2_example(self):
        rec = encode(SCHEMA, (42, "bob", 7))
        self.assertEqual(rec.hex(" "), "2a 00 00 00 03 00 62 6f 62 07 00 00 00")
        self.assertEqual(len(rec), 13)
        self.assertEqual(decode(SCHEMA, rec), (42, "bob", 7))

    def test_negative_int_and_utf8(self):
        s = parse_schema("x:INT,y:VARCHAR(10)")
        self.assertEqual(decode(s, encode(s, (-1, "café"))), (-1, "café"))

    def test_varchar_too_long(self):
        with self.assertRaises(RecordError):
            encode(SCHEMA, (1, "toolong", 2))

    def test_varchar_limit_is_255(self):
        with self.assertRaises(RecordError):
            parse_schema("a:VARCHAR(256)")


class SlottedPageExample(unittest.TestCase):
    def test_section_3_2_worked_example(self):
        p = empty_page()
        self.assertEqual(state(p), (0, 4096, 4088))
        self.assertEqual(p.insert(b"x" * 13), 0)
        self.assertEqual(state(p), (1, 4083, 4071))
        self.assertEqual(p.slot(0), (4083, 13))
        self.assertEqual(p.insert(b"y" * 20), 1)
        self.assertEqual(state(p), (2, 4063, 4047))
        self.assertEqual(p.slot(1), (4063, 20))
        self.assertEqual(p.insert(b"z" * 13), 2)
        self.assertEqual(state(p), (3, 4050, 4030))
        self.assertEqual(p.slot(2), (4050, 13))
        p.delete(1)
        self.assertEqual(state(p), (3, 4050, 4030))
        self.assertEqual(p.slot(1), (0, 0))

    def test_capacity_for_13_byte_records_is_240(self):
        p = empty_page()
        n = 0
        while p.insert(b"r" * 13) is not None:
            n += 1
        self.assertEqual(n, 4088 // (13 + 4))
        self.assertEqual(n, 240)

    def test_dead_slot_and_missing_slot_are_errors(self):
        p = empty_page(7)
        p.insert(b"abc")
        p.delete(0)
        with self.assertRaises(PageError) as cm:
            p.get(0)
        self.assertIn("(7, 0)", str(cm.exception))
        with self.assertRaises(PageError):
            p.delete(0)
        with self.assertRaises(PageError):
            p.get(5)

    def test_slots_are_never_reused(self):
        p = empty_page()
        p.insert(b"a")
        p.delete(0)
        self.assertEqual(p.insert(b"b"), 1)
        self.assertEqual(list(p.live_slots()), [1])

    def test_largest_record_fits_and_one_more_byte_does_not(self):
        p = empty_page()
        self.assertEqual(p.insert(b"m" * 4084), 0)
        self.assertEqual(p.free_space(), 0)
        self.assertIsNone(empty_page().insert(b"m" * 4085))

    def test_page_is_a_view_on_the_frame(self):
        frame = bytearray(PAGE_SIZE)
        SlottedPage.init(frame, 3).insert(b"hi")
        self.assertEqual(SlottedPage(frame).get(0), b"hi")
        self.assertEqual(frame[0:4], b"\x03\x00\x00\x00")

    def test_empty_page_header_bytes(self):
        frame = bytearray(PAGE_SIZE)
        SlottedPage.init(frame, 1)
        # page_id 1, slot_count 0, free_end 4096 = 0x1000
        self.assertEqual(frame[0:8].hex(" "), "01 00 00 00 00 00 00 10")
