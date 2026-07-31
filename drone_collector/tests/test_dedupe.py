# -*- coding: utf-8 -*-
"""In-run deduplication (section 7.5 of the DRONE-003 task).

A list containing the same id three times yields one flight and a reported
self-duplicate count of two.

This count is deliberately kept apart from the `duplicates` the ingest
endpoint reports. They answer different questions: the collector's own count
says "the browser handed me the same page twice", the endpoint's says "these
flights were already in the database". Adding them together would hide both.
"""

import ast
import unittest

from pathlib import Path

from drone_collector import browser as browser_module
from drone_collector.browser import (
    CapturedPage,
    count_unidentified,
    dedupe_flights,
    pages_seen,
)

from drone_collector.tests.support import list_url, load_fixture, make_flight


class DedupeTests(unittest.TestCase):

    def test_the_same_id_three_times_yields_one_flight_and_two_duplicates(self):
        flight = make_flight(900000001)
        unique, duplicates = dedupe_flights([flight, dict(flight), dict(flight)])
        self.assertEqual(len(unique), 1)
        self.assertEqual(duplicates, 2)
        self.assertEqual(unique[0]['id'], 900000001)

    def test_distinct_flights_are_all_kept(self):
        flights = [make_flight(i) for i in (1, 2, 3)]
        unique, duplicates = dedupe_flights(flights)
        self.assertEqual([f['id'] for f in unique], [1, 2, 3])
        self.assertEqual(duplicates, 0)

    def test_order_of_first_appearance_is_preserved(self):
        flights = [make_flight(3), make_flight(1), make_flight(3),
                   make_flight(2), make_flight(1)]
        unique, duplicates = dedupe_flights(flights)
        self.assertEqual([f['id'] for f in unique], [3, 1, 2])
        self.assertEqual(duplicates, 2)

    def test_empty_input(self):
        self.assertEqual(dedupe_flights([]), ([], 0))

    def test_string_ids_are_compared_as_numbers(self):
        # The payload is JSON from someone else's system; '900000001' and
        # 900000001 are the same flight.
        unique, duplicates = dedupe_flights(
            [make_flight(900000001), make_flight('900000001')])
        self.assertEqual(len(unique), 1)
        self.assertEqual(duplicates, 1)

    def test_flights_without_an_id_are_passed_through_not_dropped(self):
        # They cannot be deduplicated, but dropping them here would lose them
        # silently; the ingest counts them as errors and says so.
        no_id = make_flight(1)
        del no_id['id']
        unique, duplicates = dedupe_flights([no_id, make_flight(1), no_id])
        self.assertEqual(len(unique), 3)
        self.assertEqual(duplicates, 0)
        self.assertEqual(count_unidentified(unique), 2)

    def test_non_objects_are_ignored(self):
        unique, duplicates = dedupe_flights([make_flight(1), None, 'x', 42])
        self.assertEqual(len(unique), 1)
        self.assertEqual(duplicates, 0)

    def test_flights_from_two_captures_of_the_same_page(self):
        body = load_fixture('flight_list_page1.json')
        page_a = CapturedPage(list_url(page=1), body)
        page_b = CapturedPage(list_url(page=1), body)
        raw = page_a.flights + page_b.flights
        self.assertEqual(len(raw), 4)
        unique, duplicates = dedupe_flights(raw)
        self.assertEqual(len(unique), 2)
        self.assertEqual(duplicates, 2)


class SerialNumberIsNotAMachineKeyTests(unittest.TestCase):
    """serial_number is unique per FLIGHT, not per aircraft (verified on
    10 385 records). Treating it as a machine key is the mistake this pins."""

    def test_flights_sharing_a_serial_number_are_both_kept(self):
        # Two different flights that happen to carry the same serial: both are
        # real flights and both must survive.
        flights = [make_flight(1, serial_number='R0000000001'),
                   make_flight(2, serial_number='R0000000001')]
        unique, duplicates = dedupe_flights(flights)
        self.assertEqual(len(unique), 2)
        self.assertEqual(duplicates, 0)

    def test_one_flight_with_two_serial_numbers_is_still_one_flight(self):
        # Same id, different serial: deduplication is on id alone.
        flights = [make_flight(1, serial_number='R0000000001'),
                   make_flight(1, serial_number='R0000000002')]
        unique, duplicates = dedupe_flights(flights)
        self.assertEqual(len(unique), 1)
        self.assertEqual(duplicates, 1)

    def test_no_module_reads_the_serial_number_field(self):
        """Structural check: no *code* in the package names serial_number.

        Docstrings and comments are excluded on purpose -- saying "this is not
        a machine key" in prose is the point. What this forbids is a string
        literal in executable code, which is the only way a dict field can be
        read: flight['serial_number'], flight.get('serial_number'), a key in a
        mapping. Comments never reach the AST; docstrings are skipped
        explicitly.
        """
        package = Path(browser_module.__file__).resolve().parent
        offenders = []
        for source in sorted(package.glob('*.py')):
            for literal in _code_string_literals(source.read_text(encoding='utf-8')):
                if 'serial_number' in literal:
                    offenders.append('%s: %r' % (source.name, literal))
        self.assertEqual(offenders, [])


def _code_string_literals(source):
    """Every string constant in `source` except docstrings.

    Comments are absent from the AST altogether, so they need no handling.
    """
    tree = ast.parse(source)
    docstrings = set()
    for node in ast.walk(tree):
        body = getattr(node, 'body', None)
        if not isinstance(body, list) or not body:
            continue
        if not isinstance(node, (ast.Module, ast.ClassDef, ast.FunctionDef,
                                 ast.AsyncFunctionDef)):
            continue
        first = body[0]
        if (isinstance(first, ast.Expr)
                and isinstance(first.value, ast.Constant)
                and isinstance(first.value.value, str)):
            docstrings.add(id(first.value))
    return [node.value for node in ast.walk(tree)
            if isinstance(node, ast.Constant)
            and isinstance(node.value, str)
            and id(node) not in docstrings]


class PagesSeenTests(unittest.TestCase):

    def test_counts_distinct_page_numbers(self):
        body = load_fixture('flight_list_page1.json')
        pages = [
            CapturedPage(list_url(page=1), body),
            CapturedPage(list_url(page=1), body),
            CapturedPage(list_url(page=2),
                         dict(body, meta_data={'current_page': 2,
                                               'total_pages': 37})),
        ]
        self.assertEqual(pages_seen(pages), set([1, 2]))

    def test_pages_without_meta_data_are_not_counted(self):
        pages = [CapturedPage(list_url(), {'code': 200, 'data': []})]
        self.assertEqual(pages_seen(pages), set())


if __name__ == '__main__':
    unittest.main()
