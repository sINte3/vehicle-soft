# -*- coding: utf-8 -*-
"""Shared helpers for the collector tests: fixture loading and URL building.

The URLs below are built from the same epoch values the tests assert on, so a
fixture and its expectation cannot drift apart silently.
"""

import json

from pathlib import Path

FIXTURES_DIR = Path(__file__).resolve().parent / 'fixtures'

# The live host, so the URL markers are exercised against a realistic string.
API_BASE = 'https://www.djiag.com/api/web/v1'

# 2026-07-01 00:00:00.000 and 2026-07-31 23:59:59.999, local time at UTC+5.
PERIOD_JULY_FROM_MS = 1782846000000
PERIOD_JULY_TO_MS = 1785524399999


def load_fixture(name):
    """Parsed JSON fixture from drone_collector/tests/fixtures/."""
    with (FIXTURES_DIR / name).open(encoding='utf-8') as handle:
        return json.load(handle)


def list_url(page=1, from_ms=PERIOD_JULY_FROM_MS, to_ms=PERIOD_JULY_TO_MS,
             page_size=30, encoded=True):
    """A flight-list request URL as the site produces it.

    encoded=True percent-encodes the brackets of filters[...], which is how
    they actually arrive; encoded=False is the readable spelling, and both
    must parse identically.
    """
    if encoded:
        from_key, to_key = 'filters%5Btimestamp_gteq%5D', 'filters%5Btimestamp_lteq%5D'
    else:
        from_key, to_key = 'filters[timestamp_gteq]', 'filters[timestamp_lteq]'
    parts = ['page=%d' % page, 'page_size=%d' % page_size]
    if from_ms is not None:
        parts.append('%s=%d' % (from_key, from_ms))
    if to_ms is not None:
        parts.append('%s=%d' % (to_key, to_ms))
    return '%s/flight_records?%s' % (API_BASE, '&'.join(parts))


def detail_url(flight_id=574320663):
    """A single-flight URL, which must never be captured."""
    return '%s/flight_records/%d' % (API_BASE, flight_id)


def make_flight(flight_id, **overrides):
    """The fixture flight with a different id (and anything else overridden)."""
    flight = load_fixture('flight.json')
    flight['id'] = flight_id
    flight.update(overrides)
    return flight
