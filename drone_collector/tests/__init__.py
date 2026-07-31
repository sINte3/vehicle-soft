# -*- coding: utf-8 -*-
"""Tests for the DRONE-003 collector.

Fixture-driven: no network, no browser, no database. They run in the
application's Python -- playwright, requests and python-dotenv are NOT needed,
because every module that uses them imports them lazily.

Run from the repository root:

    python -m unittest discover -s drone_collector/tests -t .
"""
