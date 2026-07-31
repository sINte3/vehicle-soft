# -*- coding: utf-8 -*-
"""Period verification (section 7.2 of the DRONE-003 task).

Given an intended window and a URL string: accept a match, accept a match
within the one-hour tolerance, reject a one-day-off URL, reject a URL with no
filter parameters.

This is the check that stands between the operator and a run that silently
harvests the wrong month. The known way for it to fire is real: the SmartFarm
calendar resets a range that crosses a year boundary
(docs/tracks/drones.md, section 6).
"""

import unittest

from datetime import date

from drone_collector.browser import (
    PERIOD_TOLERANCE_MS,
    format_ms,
    parse_period_from_url,
    period_matches,
)

from drone_collector.window import window_bounds_ms

from drone_collector.tests.support import (
    PERIOD_JULY_FROM_MS,
    PERIOD_JULY_TO_MS,
    list_url,
)

MS_PER_HOUR = 3600 * 1000
MS_PER_DAY = 24 * MS_PER_HOUR


class ParsePeriodTests(unittest.TestCase):

    def test_percent_encoded_brackets(self):
        got = parse_period_from_url(list_url(encoded=True))
        self.assertEqual(got, (PERIOD_JULY_FROM_MS, PERIOD_JULY_TO_MS))

    def test_readable_brackets_parse_identically(self):
        self.assertEqual(parse_period_from_url(list_url(encoded=False)),
                         parse_period_from_url(list_url(encoded=True)))

    def test_url_without_filters(self):
        url = 'https://www.djiag.com/api/web/v1/flight_records?page=1&page_size=30'
        self.assertEqual(parse_period_from_url(url), (None, None))

    def test_non_numeric_values_are_none(self):
        url = ('https://www.djiag.com/api/web/v1/flight_records?'
               'filters%5Btimestamp_gteq%5D=&filters%5Btimestamp_lteq%5D=abc')
        self.assertEqual(parse_period_from_url(url), (None, None))

    def test_empty_url(self):
        self.assertEqual(parse_period_from_url(''), (None, None))
        self.assertEqual(parse_period_from_url(None), (None, None))


class PeriodMatchesTests(unittest.TestCase):

    def setUp(self):
        # The intended window comes from window.py, not from a literal, so a
        # change in the window arithmetic surfaces here too.
        self.expected_from, self.expected_to = window_bounds_ms(
            date(2026, 7, 1), date(2026, 7, 31), 5)
        self.assertEqual(self.expected_from, PERIOD_JULY_FROM_MS)
        self.assertEqual(self.expected_to, PERIOD_JULY_TO_MS)

    def test_exact_match_is_accepted(self):
        self.assertTrue(period_matches(list_url(), self.expected_from,
                                       self.expected_to))

    def test_match_within_the_one_hour_tolerance_is_accepted(self):
        url = list_url(from_ms=self.expected_from + 59 * 60 * 1000,
                       to_ms=self.expected_to - 59 * 60 * 1000)
        self.assertTrue(period_matches(url, self.expected_from,
                                       self.expected_to))

    def test_exactly_one_hour_off_is_still_accepted(self):
        url = list_url(from_ms=self.expected_from - MS_PER_HOUR,
                       to_ms=self.expected_to + MS_PER_HOUR)
        self.assertTrue(period_matches(url, self.expected_from,
                                       self.expected_to))

    def test_one_millisecond_beyond_the_tolerance_is_rejected(self):
        url = list_url(from_ms=self.expected_from - MS_PER_HOUR - 1,
                       to_ms=self.expected_to)
        self.assertFalse(period_matches(url, self.expected_from,
                                        self.expected_to))

    def test_one_day_off_is_rejected(self):
        url = list_url(from_ms=self.expected_from - MS_PER_DAY,
                       to_ms=self.expected_to - MS_PER_DAY)
        self.assertFalse(period_matches(url, self.expected_from,
                                        self.expected_to))

    def test_one_day_off_on_a_single_edge_is_rejected(self):
        url = list_url(from_ms=self.expected_from,
                       to_ms=self.expected_to + MS_PER_DAY)
        self.assertFalse(period_matches(url, self.expected_from,
                                        self.expected_to))

    def test_url_with_no_filter_parameters_is_rejected(self):
        url = 'https://www.djiag.com/api/web/v1/flight_records?page=1&page_size=30'
        self.assertFalse(period_matches(url, self.expected_from,
                                        self.expected_to))

    def test_url_with_only_one_bound_is_rejected(self):
        self.assertFalse(period_matches(list_url(to_ms=None),
                                        self.expected_from, self.expected_to))
        self.assertFalse(period_matches(list_url(from_ms=None),
                                        self.expected_from, self.expected_to))

    def test_a_wrong_year_is_rejected(self):
        # The calendar resetting a year-crossing range is the documented
        # failure this check exists for.
        wrong_from, wrong_to = window_bounds_ms(
            date(2025, 7, 1), date(2025, 7, 31), 5)
        url = list_url(from_ms=wrong_from, to_ms=wrong_to)
        self.assertFalse(period_matches(url, self.expected_from,
                                        self.expected_to))

    def test_tolerance_is_one_hour(self):
        self.assertEqual(PERIOD_TOLERANCE_MS, MS_PER_HOUR)


class FormatMsTests(unittest.TestCase):

    def test_renders_the_number_and_the_local_time(self):
        text = format_ms(PERIOD_JULY_FROM_MS, 5)
        self.assertIn(str(PERIOD_JULY_FROM_MS), text)
        self.assertIn('2026-07-01 00:00:00', text)
        self.assertIn('+5', text)

    def test_end_of_day(self):
        self.assertIn('2026-07-31 23:59:59', format_ms(PERIOD_JULY_TO_MS, 5))

    def test_absent_value(self):
        self.assertEqual(format_ms(None, 5), 'absent')

    def test_negative_offset(self):
        self.assertIn('-3', format_ms(PERIOD_JULY_FROM_MS, -3))


if __name__ == '__main__':
    unittest.main()
