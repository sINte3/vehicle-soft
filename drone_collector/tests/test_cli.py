# -*- coding: utf-8 -*-
"""CLI wiring: period resolution and the exit codes of the acceptance criteria.

Two of these are acceptance criteria of the task and are worth an automated
check rather than a note in a runbook:

  * a dry run with no session file exits 2 and names the missing file;
  * a sending run with no DRONE_API_TOKEN exits 1, naming the variable, and
    never gets as far as a request.

No browser is launched in either case, because both fail before that point.
"""

import logging
import os
import unittest

from datetime import date, datetime, timedelta
from pathlib import Path

from drone_collector import config as config_module
from drone_collector import main as main_module
from drone_collector.main import (
    EXIT_CONFIG,
    EXIT_SESSION,
    UsageError,
    build_parser,
    main,
    resolve_period,
)

from drone_collector.tests.test_sender import config

MISSING_STATE = '/nonexistent/drone_collector/storage_state.json'

COLLECTOR_VARS = ('DJI_RECORDS_URL', 'DJI_STORAGE_STATE', 'DJI_HEADLESS',
                  'DJI_WINDOW_DAYS', 'DJI_TZ_OFFSET_HOURS',
                  'DJI_PAGE_TIMEOUT_MS', 'DJI_SETTLE_MS', 'DJI_MAX_PAGES',
                  'VEHICLE_SOFT_BASE_URL', 'DRONE_API_TOKEN',
                  'DRONE_BATCH_SIZE')


class CliTestCase(unittest.TestCase):
    """Isolates the environment: no .env of the machine leaks into a test."""

    def setUp(self):
        self._saved_env = {name: os.environ.get(name)
                           for name in COLLECTOR_VARS}
        for name in COLLECTOR_VARS:
            os.environ.pop(name, None)
        self._saved_dotenv = config_module.DOTENV_PATH
        config_module.DOTENV_PATH = Path(MISSING_STATE)

    def tearDown(self):
        config_module.DOTENV_PATH = self._saved_dotenv
        for name, value in self._saved_env.items():
            if value is None:
                os.environ.pop(name, None)
            else:
                os.environ[name] = value
        # main() reconfigures the root logger; put it back so the rest of the
        # suite does not inherit a file handler.
        root = logging.getLogger()
        for handler in list(root.handlers):
            root.removeHandler(handler)


class ResolvePeriodTests(CliTestCase):

    def parse(self, argv):
        return build_parser().parse_args(argv)

    def test_explicit_dates_default_to_backfill(self):
        args = self.parse(['--from', '2026-07-01', '--to', '2026-07-31'])
        date_from, date_to, kind = resolve_period(args, config())
        self.assertEqual((date_from, date_to),
                         (date(2026, 7, 1), date(2026, 7, 31)))
        self.assertEqual(kind, 'backfill')

    def test_rolling_window_defaults_to_incremental(self):
        args = self.parse([])
        cfg = config()
        date_from, date_to, kind = resolve_period(args, cfg)
        self.assertEqual(kind, 'incremental')
        self.assertEqual((date_to - date_from).days + 1, cfg.window_days)
        # The window ends today in local time.
        expected_today = (datetime.utcnow()
                          + timedelta(hours=cfg.tz_offset_hours)).date()
        self.assertEqual(date_to, expected_today)

    def test_explicit_kind_wins(self):
        args = self.parse(['--from', '2026-07-01', '--to', '2026-07-31',
                           '--kind', 'replay'])
        self.assertEqual(resolve_period(args, config())[2], 'replay')

    def test_from_without_to_is_a_usage_error(self):
        with self.assertRaises(UsageError):
            resolve_period(self.parse(['--from', '2026-07-01']), config())
        with self.assertRaises(UsageError):
            resolve_period(self.parse(['--to', '2026-07-01']), config())

    def test_reversed_period_is_a_usage_error(self):
        args = self.parse(['--from', '2026-07-31', '--to', '2026-07-01'])
        with self.assertRaises(UsageError):
            resolve_period(args, config())

    def test_unparsable_date_is_a_usage_error(self):
        args = self.parse(['--from', '01.07.2026', '--to', '2026-07-31'])
        with self.assertRaises(UsageError):
            resolve_period(args, config())


class ExitCodeTests(CliTestCase):

    def test_dry_run_without_a_session_exits_2(self):
        os.environ['DJI_STORAGE_STATE'] = MISSING_STATE
        code = main(['--dry-run', '--from', '2026-07-01', '--to', '2026-07-07'])
        self.assertEqual(code, EXIT_SESSION)

    def test_missing_token_exits_1_before_anything_else(self):
        os.environ['VEHICLE_SOFT_BASE_URL'] = 'http://10.103.25.200:5050'
        os.environ['DJI_STORAGE_STATE'] = MISSING_STATE
        # No DRONE_API_TOKEN: the run must stop at configuration, i.e. BEFORE
        # the session check that would otherwise report 2.
        self.assertEqual(main(['--from', '2026-07-01', '--to', '2026-07-07']),
                         EXIT_CONFIG)

    def test_missing_base_url_exits_1(self):
        os.environ['DRONE_API_TOKEN'] = 'test-token'
        os.environ['DJI_STORAGE_STATE'] = MISSING_STATE
        self.assertEqual(main([]), EXIT_CONFIG)

    def test_unparsable_setting_exits_1(self):
        os.environ['DRONE_API_TOKEN'] = 'test-token'
        os.environ['VEHICLE_SOFT_BASE_URL'] = 'http://10.103.25.200:5050'
        os.environ['DJI_WINDOW_DAYS'] = 'thirty'
        self.assertEqual(main([]), EXIT_CONFIG)

    def test_bad_flag_exits_1_not_2(self):
        # argparse's own convention is status 2, which is this program's
        # "session missing"; a typo must not read as an expired session.
        self.assertEqual(main(['--no-such-flag']), EXIT_CONFIG)

    def test_help_exits_0(self):
        with self.assertRaises(SystemExit) as caught:
            build_parser().parse_args(['--help'])
        self.assertEqual(caught.exception.code, 0)


class ExitCodeConstantsTests(unittest.TestCase):

    def test_the_documented_codes(self):
        self.assertEqual(
            (main_module.EXIT_OK, main_module.EXIT_CONFIG,
             main_module.EXIT_SESSION, main_module.EXIT_PERIOD,
             main_module.EXIT_PAGINATION, main_module.EXIT_INGEST),
            (0, 1, 2, 3, 4, 5))


if __name__ == '__main__':
    unittest.main()
