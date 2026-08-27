# -*- coding: utf-8 -*-
"""drone_collector/window.py -- reporting-window arithmetic.

Pure functions, no I/O, no imports from the rest of the collector. They carry
unit tests because everything downstream trusts them: the dates typed into the
SmartFarm date picker, the epoch-millisecond bounds the returned request URL
is verified against, and the period_from/period_to written into the ingest
body all come from here. An off-by-one here is an off-by-one in the database.

Timezone model. SmartFarm timestamps are UTC; the business reads flights in
UTC+5, which is also what drones.py uses for its day boundaries
(DRONE_DISPLAY_UTC_OFFSET). A window edge therefore means *local* midnight,
not UTC midnight -- 2026-07-01 00:00 in UTC+5 is 2026-06-30 19:00 UTC. Getting
this wrong shifts every window by five hours and silently drops the first
flights of the first day.
"""

import calendar

from datetime import date, datetime, time, timedelta, timezone

# One hour of tolerance is allowed when the site's own request URL is compared
# against these values; see browser.verify_period_in_url.
MS_PER_HOUR = 3600 * 1000

ISO_DATE_FORMAT = '%Y-%m-%d'


def local_today(tz_offset_hours, now_utc=None):
    """Today's date in local time. now_utc is injectable for tests.

    [REASON]: `datetime.utcnow()` объявлен устаревшим и снимается в будущих
    версиях Python. Заменён на timezone-aware время в UTC, приведённое
    обратно к наивному виду: `now_utc`, который передают тесты и остальной
    collector, наивный, и складывать наивное с timezone-aware Python не даёт.
    Поведение при этом прежнее -- то же мгновение в UTC.
    """
    now = (now_utc if now_utc is not None
           else datetime.now(timezone.utc).replace(tzinfo=None))
    return (now + timedelta(hours=tz_offset_hours)).date()


def compute_window(window_days, tz_offset_hours, today=None):
    """The last `window_days` days in local time, inclusive of today.

    Returns (date_from, date_to) as datetime.date. window_days=1 means today
    alone; window_days=30 means today and the 29 days before it. Inclusive on
    both ends -- the same convention the SmartFarm date picker uses and the
    same one the ingest body's period_from/period_to carry.
    """
    if window_days < 1:
        raise ValueError('window_days must be at least 1, got %r' % (window_days,))
    date_to = today if today is not None else local_today(tz_offset_hours)
    date_from = date_to - timedelta(days=window_days - 1)
    return date_from, date_to


def to_epoch_ms(day, end_of_day, tz_offset_hours):
    """Epoch milliseconds for a local day edge.

    end_of_day=False -> local 00:00:00.000 of `day`.
    end_of_day=True  -> local 23:59:59.999 of `day`.

    [REASON]: computed through calendar.timegm on the shifted naive datetime
    rather than through float arithmetic on a timestamp, so the millisecond is
    exact and does not depend on the machine's local timezone -- the server
    runs in UTC+5 but a test machine or a scheduler may not.
    """
    moment = time(23, 59, 59, 999000) if end_of_day else time(0, 0, 0, 0)
    local_naive = datetime.combine(day, moment)
    utc_naive = local_naive - timedelta(hours=tz_offset_hours)
    seconds = calendar.timegm(utc_naive.timetuple())
    return seconds * 1000 + utc_naive.microsecond // 1000


def window_bounds_ms(date_from, date_to, tz_offset_hours):
    """(from_ms, to_ms) for a window: local midnight to local 23:59:59.999."""
    return (to_epoch_ms(date_from, False, tz_offset_hours),
            to_epoch_ms(date_to, True, tz_offset_hours))


def split_by_calendar_year(date_from, date_to):
    """Split a period into per-calendar-year sub-windows, in order.

        2025-06-30 .. 2026-07-31  ->  [(2025-06-30, 2025-12-31),
                                       (2026-01-01, 2026-07-31)]

    [REASON]: the SmartFarm date picker RESETS a range that spans a year
    boundary -- a single request for 2025-06-30 .. 2026-07-31 does not produce
    that period. Splitting is not an optimisation, it is the only way to
    collect history at all. Each sub-window is then a full cycle of its own:
    set the period, verify it, paginate, capture, send.

    A period inside one year comes back as a single window, so the caller
    always iterates a list and never special-cases.
    """
    if date_from > date_to:
        raise ValueError('date_from %s is after date_to %s'
                         % (date_from, date_to))
    windows = []
    start = date_from
    while start.year < date_to.year:
        windows.append((start, date(start.year, 12, 31)))
        start = date(start.year + 1, 1, 1)
    windows.append((start, date_to))
    return windows


def format_date(day):
    """YYYY-MM-DD -- the format typed into the picker and sent to the ingest."""
    return day.strftime(ISO_DATE_FORMAT)


def parse_date(text):
    """YYYY-MM-DD -> date. Raises ValueError with the offending text."""
    if not text:
        raise ValueError('expected a date as YYYY-MM-DD, got an empty value')
    return datetime.strptime(text.strip(), ISO_DATE_FORMAT).date()
