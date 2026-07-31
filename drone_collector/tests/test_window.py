# -*- coding: utf-8 -*-
"""Window arithmetic and epoch conversion (section 7.1 of the DRONE-003 task).

Every case pins `today` explicitly -- a test that reads the clock passes today
and fails on the 1st of a month.
"""

import unittest

from datetime import date, datetime, timedelta

from drone_collector.window import (
    compute_window,
    format_date,
    local_today,
    parse_date,
    split_by_calendar_year,
    to_epoch_ms,
    window_bounds_ms,
)

MS_PER_HOUR = 3600 * 1000
MS_PER_DAY = 24 * MS_PER_HOUR


class ComputeWindowTests(unittest.TestCase):

    def test_thirty_day_window_is_inclusive_of_today(self):
        # 30 days INCLUSIVE of today: 2026-07-02 .. 2026-07-31 is 30 dates.
        date_from, date_to = compute_window(30, 5, today=date(2026, 7, 31))
        self.assertEqual(date_from, date(2026, 7, 2))
        self.assertEqual(date_to, date(2026, 7, 31))
        self.assertEqual((date_to - date_from).days + 1, 30)

    def test_single_day_window_is_today_alone(self):
        date_from, date_to = compute_window(1, 5, today=date(2026, 7, 31))
        self.assertEqual(date_from, date(2026, 7, 31))
        self.assertEqual(date_to, date(2026, 7, 31))

    def test_across_a_month_boundary(self):
        # 2026-03-05 back 9 days lands in February of a non-leap year.
        date_from, date_to = compute_window(10, 5, today=date(2026, 3, 5))
        self.assertEqual(date_from, date(2026, 2, 24))
        self.assertEqual(date_to, date(2026, 3, 5))
        self.assertEqual((date_to - date_from).days + 1, 10)

    def test_across_a_year_boundary(self):
        # The SmartFarm calendar resets a range that crosses the year
        # boundary (docs/tracks/drones.md section 6). The arithmetic still has
        # to be right -- the verification step is what catches the site's
        # refusal, not a fudged window.
        date_from, date_to = compute_window(30, 5, today=date(2026, 1, 15))
        self.assertEqual(date_from, date(2025, 12, 17))
        self.assertEqual(date_to, date(2026, 1, 15))
        self.assertEqual((date_to - date_from).days + 1, 30)

    def test_across_a_leap_day(self):
        date_from, date_to = compute_window(5, 5, today=date(2028, 3, 1))
        self.assertEqual(date_from, date(2028, 2, 26))
        self.assertEqual(date_to, date(2028, 3, 1))

    def test_zero_window_is_rejected(self):
        with self.assertRaises(ValueError):
            compute_window(0, 5)

    def test_default_today_comes_from_the_local_clock(self):
        # 2026-07-31 21:30 UTC is already 2026-08-01 in UTC+5.
        now = datetime(2026, 7, 31, 21, 30)
        self.assertEqual(local_today(5, now_utc=now), date(2026, 8, 1))
        self.assertEqual(local_today(0, now_utc=now), date(2026, 7, 31))


class EpochConversionTests(unittest.TestCase):

    def test_start_of_day_at_utc_plus_5_is_local_midnight_not_utc_midnight(self):
        # Local midnight on 2026-07-01 in UTC+5 == 2026-06-30T19:00:00Z.
        got = to_epoch_ms(date(2026, 7, 1), False, 5)
        utc_midnight = to_epoch_ms(date(2026, 7, 1), False, 0)
        self.assertEqual(got, utc_midnight - 5 * MS_PER_HOUR)
        # And the absolute value, computed independently of this module.
        expected = calendar_timegm(2026, 6, 30, 19, 0, 0) * 1000
        self.assertEqual(got, expected)

    def test_end_of_day_is_local_235959_999(self):
        got = to_epoch_ms(date(2026, 7, 31), True, 5)
        expected = calendar_timegm(2026, 7, 31, 18, 59, 59) * 1000 + 999
        self.assertEqual(got, expected)

    def test_a_full_day_spans_one_day_minus_one_millisecond(self):
        start = to_epoch_ms(date(2026, 7, 1), False, 5)
        end = to_epoch_ms(date(2026, 7, 1), True, 5)
        self.assertEqual(end - start, MS_PER_DAY - 1)

    def test_negative_offset(self):
        # UTC-3: local midnight is three hours AFTER UTC midnight.
        got = to_epoch_ms(date(2026, 7, 1), False, -3)
        utc_midnight = to_epoch_ms(date(2026, 7, 1), False, 0)
        self.assertEqual(got, utc_midnight + 3 * MS_PER_HOUR)

    def test_half_hour_offset(self):
        got = to_epoch_ms(date(2026, 7, 1), False, 5.5)
        utc_midnight = to_epoch_ms(date(2026, 7, 1), False, 0)
        self.assertEqual(got, utc_midnight - 5 * MS_PER_HOUR - MS_PER_HOUR // 2)

    def test_window_bounds_ms_pairs_the_two_edges(self):
        date_from, date_to = compute_window(30, 5, today=date(2026, 7, 31))
        from_ms, to_ms = window_bounds_ms(date_from, date_to, 5)
        self.assertEqual(from_ms, to_epoch_ms(date(2026, 7, 2), False, 5))
        self.assertEqual(to_ms, to_epoch_ms(date(2026, 7, 31), True, 5))
        self.assertEqual(to_ms - from_ms, 30 * MS_PER_DAY - 1)


class SplitByCalendarYearTests(unittest.TestCase):
    """The picker resets a range that crosses a year boundary, so history can
    only be collected one calendar year at a time."""

    def test_a_range_inside_one_year_returns_one_window(self):
        self.assertEqual(
            split_by_calendar_year(date(2026, 1, 1), date(2026, 7, 31)),
            [(date(2026, 1, 1), date(2026, 7, 31))])

    def test_a_range_spanning_two_years_returns_two(self):
        self.assertEqual(
            split_by_calendar_year(date(2025, 6, 30), date(2026, 7, 31)),
            [(date(2025, 6, 30), date(2025, 12, 31)),
             (date(2026, 1, 1), date(2026, 7, 31))])

    def test_a_range_spanning_three_years_returns_three(self):
        self.assertEqual(
            split_by_calendar_year(date(2024, 5, 1), date(2026, 3, 1)),
            [(date(2024, 5, 1), date(2024, 12, 31)),
             (date(2025, 1, 1), date(2025, 12, 31)),
             (date(2026, 1, 1), date(2026, 3, 1))])

    def test_a_range_starting_and_ending_on_1_january(self):
        self.assertEqual(
            split_by_calendar_year(date(2025, 1, 1), date(2026, 1, 1)),
            [(date(2025, 1, 1), date(2025, 12, 31)),
             (date(2026, 1, 1), date(2026, 1, 1))])

    def test_a_single_day(self):
        self.assertEqual(
            split_by_calendar_year(date(2026, 1, 1), date(2026, 1, 1)),
            [(date(2026, 1, 1), date(2026, 1, 1))])

    def test_a_whole_year_exactly(self):
        self.assertEqual(
            split_by_calendar_year(date(2025, 1, 1), date(2025, 12, 31)),
            [(date(2025, 1, 1), date(2025, 12, 31))])

    def test_the_windows_are_contiguous_and_lose_no_day(self):
        windows = split_by_calendar_year(date(2024, 5, 1), date(2026, 3, 1))
        days = sum((end - start).days + 1 for start, end in windows)
        self.assertEqual(days, (date(2026, 3, 1) - date(2024, 5, 1)).days + 1)
        for earlier, later in zip(windows, windows[1:]):
            self.assertEqual(later[0] - earlier[1], timedelta(days=1))

    def test_a_reversed_range_is_rejected(self):
        with self.assertRaises(ValueError):
            split_by_calendar_year(date(2026, 7, 31), date(2026, 1, 1))

    def test_the_rolling_window_across_new_year_splits_in_two(self):
        # The scheduled case on 15 January: the 30-day window reaches back
        # into December and must not be requested as one range.
        date_from, date_to = compute_window(30, 5, today=date(2026, 1, 15))
        self.assertEqual(split_by_calendar_year(date_from, date_to),
                         [(date(2025, 12, 17), date(2025, 12, 31)),
                          (date(2026, 1, 1), date(2026, 1, 15))])


class DateFormatTests(unittest.TestCase):

    def test_format_matches_what_the_picker_and_the_ingest_expect(self):
        self.assertEqual(format_date(date(2026, 7, 1)), '2026-07-01')

    def test_parse_round_trip(self):
        self.assertEqual(parse_date('2026-07-01'), date(2026, 7, 1))
        self.assertEqual(parse_date('  2026-07-01 '), date(2026, 7, 1))

    def test_parse_rejects_rubbish(self):
        for bad in ('', '01.07.2026', '2026-13-01', 'yesterday'):
            with self.assertRaises(ValueError):
                parse_date(bad)


def calendar_timegm(year, month, day, hour, minute, second):
    """Independent UTC epoch-seconds helper, so the tests do not verify
    window.to_epoch_ms with window.to_epoch_ms."""
    import calendar
    return calendar.timegm((year, month, day, hour, minute, second, 0, 0, 0))


if __name__ == '__main__':
    unittest.main()
