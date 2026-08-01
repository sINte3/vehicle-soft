# -*- coding: utf-8 -*-
"""Period verification (section 7.2 of the DRONE-003 task).

Given an intended window and a URL string: accept a match, accept a match
within the one-hour tolerance, reject a one-day-off URL, reject a URL with no
filter parameters.

This is the check that stands between the operator and a run that silently
harvests the wrong month. The known way for it to fire is real: the SmartFarm
calendar resets a range that crosses a year boundary
(docs/tracks/drones.md, section 6).

VerifyPeriodTests below cover DRONE-PERIOD-RACE-001, which is the opposite
failure: the check firing on a run that is perfectly fine. The picker issues a
flight-list request once the START date is typed, while the end date is still
the previous one; that response is a well-formed flight list for a period
nobody asked for. Deciding acceptance from the LAST capture turned it into a
false alarm that aborted the run with exit code 3, and it grew with the weight
of the intermediate request -- a 965-page window failed where a 175-page window
passed. These tests are pure: no browser, no network.
"""

import unittest

from datetime import date

from drone_collector.browser import (
    PERIOD_TOLERANCE_MS,
    CapturedPage,
    FlightCollector,
    PeriodVerificationFailed,
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

from drone_collector.tests.test_sender import config

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


class FakePage(object):
    """Stands in for the Playwright page inside _wait_for_matching_capture.

    Only wait_for_timeout is exercised. `on_wait` is called with the 1-based
    call number and may append to the collector's capture list -- which is how
    a response arriving mid-wait is simulated without a browser.

    [REASON]: the collector must poll through the page's own wait_for_timeout
    rather than time.sleep(), because a plain sleep stops Playwright pumping
    its event loop and the response listener never runs. Counting the calls
    here is also what proves the fast path does not wait at all.
    """

    def __init__(self, on_wait=None):
        self.waits = 0
        self.wait_args = []
        self._on_wait = on_wait

    def wait_for_timeout(self, milliseconds):
        self.waits += 1
        self.wait_args.append(milliseconds)
        if self._on_wait is not None:
            self._on_wait(self.waits)


def collector(captured=(), on_wait=None):
    """A FlightCollector wired to a fake page, with no browser started.

    __init__ touches nothing but plain attributes, so the object can be built
    directly; start() is what would launch Chromium and is never called here.
    """
    instance = FlightCollector(config())
    instance._page = FakePage(on_wait)
    instance._captured = list(captured)
    return instance


def page_for(from_ms, to_ms):
    return CapturedPage(list_url(from_ms=from_ms, to_ms=to_ms),
                        {'code': 0, 'data': []})


class VerifyPeriodTests(unittest.TestCase):
    """DRONE-PERIOD-RACE-001: acceptance comes from the capture's own URL."""

    def setUp(self):
        self.expected_from, self.expected_to = window_bounds_ms(
            date(2026, 7, 1), date(2026, 7, 31), 5)
        # The half-applied period: the START date has been typed, the end date
        # is still the previous one. A real, well-formed flight list -- for a
        # window nobody asked for.
        self.half_applied = page_for(self.expected_from,
                                     self.expected_to + 90 * MS_PER_DAY)
        self.matching = page_for(self.expected_from, self.expected_to)

    def test_the_race_is_survived_when_the_match_arrives_while_waiting(self):
        # The defect reproduced: the half-applied capture is already present,
        # so the old code skipped the wait entirely, read this URL as "the
        # last capture" and aborted the run.
        collect = collector([self.half_applied])

        def deliver_on_the_second_poll(call_number):
            if call_number == 2:
                collect._captured.append(self.matching)

        collect._page = FakePage(deliver_on_the_second_poll)

        self.assertTrue(collect.verify_period(self.expected_from,
                                              self.expected_to))
        # The half-applied page is gone: it must not contribute flights.
        self.assertEqual(collect._captured, [self.matching])
        self.assertGreaterEqual(collect._page.waits, 2)

    def test_a_genuine_mismatch_is_still_fatal(self):
        # A year-crossing range the calendar reset -- the failure this check
        # exists for. Nothing matching ever arrives.
        wrong_from, wrong_to = window_bounds_ms(
            date(2025, 7, 1), date(2025, 7, 31), 5)
        collect = collector([page_for(wrong_from, wrong_to)])

        with self.assertRaises(PeriodVerificationFailed) as caught:
            collect.verify_period(self.expected_from, self.expected_to,
                                  timeout_ms=5)

        message = str(caught.exception)
        self.assertIn(format_ms(self.expected_from, 5), message)
        self.assertIn(format_ms(wrong_from, 5), message)
        self.assertIn('intended', message)
        self.assertIn('observed', message)

    def test_the_distinct_observed_periods_are_all_reported(self):
        # Two captures of the half-applied window and one of a third period:
        # the message names two distinct periods, not three lines and not one.
        other = page_for(self.expected_from - MS_PER_DAY,
                         self.expected_to - MS_PER_DAY)
        collect = collector([self.half_applied, self.half_applied, other])

        with self.assertRaises(PeriodVerificationFailed) as caught:
            collect.verify_period(self.expected_from, self.expected_to,
                                  timeout_ms=0)

        message = str(caught.exception)
        self.assertIn('3 capture(s)', message)
        self.assertIn('2 distinct period(s)', message)

    def test_no_captures_at_all_says_so(self):
        collect = collector([])
        collect._rejected = {'code-101': 4}

        with self.assertRaises(PeriodVerificationFailed) as caught:
            collect.verify_period(self.expected_from, self.expected_to,
                                  timeout_ms=0)

        message = str(caught.exception)
        self.assertIn('no flight-list request was captured', message)
        self.assertIn('code-101', message)

    def test_a_match_already_present_costs_no_wait(self):
        collect = collector([self.matching])

        self.assertTrue(collect.verify_period(self.expected_from,
                                              self.expected_to))
        self.assertEqual(collect._page.waits, 0)

    def test_a_match_is_accepted_from_anywhere_in_the_capture_list(self):
        # The matching page arrived FIRST and the half-applied one after it,
        # so "the last capture" is the wrong one. Order must not decide.
        collect = collector([self.matching, self.half_applied])

        self.assertTrue(collect.verify_period(self.expected_from,
                                              self.expected_to))
        self.assertEqual(collect._captured, [self.matching])
        self.assertEqual(collect._page.waits, 0)


if __name__ == '__main__':
    unittest.main()
