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

from datetime import date, datetime, timedelta, timezone
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

from drone_collector.browser import CollectResult
from drone_collector.tests.support import make_flight
from drone_collector.sender import SendResult
from drone_collector.tests.test_sender import config

import tempfile

from drone_collector import lands as lands_module
from drone_collector.lands import LandResult
from drone_collector.main import EXIT_GEOMETRY_NOT_FOUND, EXIT_OK

MISSING_STATE = '/nonexistent/drone_collector/storage_state.json'

COLLECTOR_VARS = ('DJI_RECORDS_URL', 'DJI_STORAGE_STATE', 'DJI_HEADLESS',
                  'DJI_WINDOW_DAYS', 'DJI_TZ_OFFSET_HOURS',
                  'DJI_PAGE_TIMEOUT_MS', 'DJI_SETTLE_MS', 'DJI_MAX_PAGES',
                  'VEHICLE_SOFT_BASE_URL', 'DRONE_API_TOKEN',
                  'DRONE_BATCH_SIZE', 'DJI_ROUTE_API_ORIGIN',
                  'DRONE_OUTBOX_DIR', 'DJI_ROUTE_BATCH_SIZE',
                  'DJI_ROUTE_PAUSE_MS', 'DJI_GEOMETRY_PAUSE_MS',
                  'DJI_FIELDS_URL', 'DJI_MAX_LAND_PAGES',
                  'DJI_EXPECTED_REGION', 'DJI_ALLOW_EMPTY_WINDOW')


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
        # [REASON]: `datetime.utcnow()` объявлен устаревшим и на Python 3.14
        # печатает предупреждение. Тест проверяет ту самую функцию, из которой
        # он убран, и звать здесь устаревший вызов -- значит держать дефект в
        # проверке дефекта.
        expected_today = (datetime.now(timezone.utc).replace(tzinfo=None)
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
        os.environ['VEHICLE_SOFT_BASE_URL'] = 'http://10.103.25.14:5050'
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
        os.environ['VEHICLE_SOFT_BASE_URL'] = 'http://10.103.25.14:5050'
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


class EmptyWindowGuardTests(CliTestCase):
    """Guard A of the addendum, and the one that needs no selector.

    A session switched to another region returns zero rows with no error at
    all: the run looks successful and collects nothing. So zero flights is a
    failure unless it has been declared expected.
    """

    def setUp(self):
        CliTestCase.setUp(self)
        self.posted = []
        self._real_send = main_module.send

        def fake_send(flights, kind, period_from, period_to, cfg, **kwargs):
            self.posted.append((flights, kind, period_from, period_to))
            return SendResult().add({'log_id': 1, 'seen': len(flights),
                                     'new': len(flights)})

        main_module.send = fake_send

    def tearDown(self):
        main_module.send = self._real_send
        CliTestCase.tearDown(self)

    def account_for(self, result, cfg=None, dry_run=False):
        args = build_parser().parse_args(['--dry-run'] if dry_run else [])
        return main_module._account_for(result, args, 'incremental',
                                        cfg or config(), logging.getLogger('t'),
                                        {})

    def test_an_empty_window_exits_6_and_posts_nothing(self):
        self.assertEqual(self.account_for(collect_result([])),
                         main_module.EXIT_EMPTY)
        self.assertEqual(self.posted, [])

    def test_an_empty_window_is_allowed_when_declared_expected(self):
        cfg = config()
        cfg.allow_empty_window = True
        self.assertEqual(self.account_for(collect_result([]), cfg=cfg),
                         main_module.EXIT_OK)
        self.assertEqual(self.posted, [])

    def test_a_non_empty_window_is_sent(self):
        self.assertEqual(self.account_for(collect_result([make_flight(1)])),
                         main_module.EXIT_OK)
        self.assertEqual(len(self.posted), 1)

    def test_an_incomplete_walk_is_still_sent_but_exits_4(self):
        result = collect_result([make_flight(1)], complete=False)
        self.assertEqual(self.account_for(result), main_module.EXIT_PAGINATION)
        self.assertEqual(len(self.posted), 1)


class RegionGuardTests(unittest.TestCase):
    """Guard B: a wrong region blocks, a missing indicator only warns."""

    def test_a_region_mismatch_maps_to_exit_7(self):
        from drone_collector.browser import RegionMismatch
        code = main_module._exit_code_for(RegionMismatch('wrong region'),
                                          logging.getLogger('t'))
        self.assertEqual(code, main_module.EXIT_REGION)

    def test_the_other_browser_failures_keep_their_codes(self):
        from drone_collector.browser import (BrowserError,
                                             PeriodVerificationFailed,
                                             SessionExpired)
        log = logging.getLogger('t')
        cases = [
            (SessionExpired('x'), main_module.EXIT_SESSION),
            (PeriodVerificationFailed('x'), main_module.EXIT_PERIOD),
            (BrowserError('x'), main_module.EXIT_PAGINATION),
        ]
        for exc, expected in cases:
            self.assertEqual(main_module._exit_code_for(exc, log), expected)


class ExitCodeConstantsTests(unittest.TestCase):

    def test_the_documented_codes(self):
        self.assertEqual(
            (main_module.EXIT_OK, main_module.EXIT_CONFIG,
             main_module.EXIT_SESSION, main_module.EXIT_PERIOD,
             main_module.EXIT_PAGINATION, main_module.EXIT_INGEST,
             main_module.EXIT_EMPTY, main_module.EXIT_REGION,
             main_module.EXIT_ROUTE_REFUSED),
            (0, 1, 2, 3, 4, 5, 6, 7, 10))

    def test_the_route_code_does_not_collide_with_the_device_sweep(self):
        """8 и 9 заняты вторым входом пакета -- `drone_collector.devices`.

        [REASON]: одно число с двумя смыслами внутри одного пакета -- это
        код выхода, который оператор однажды прочтёт неверно, глядя в журнал
        планировщика, а не в исходник.
        """
        from drone_collector import devices as devices_module
        taken = {devices_module.EXIT_NO_DEVICES,
                 devices_module.EXIT_MISMATCH}
        self.assertNotIn(main_module.EXIT_ROUTE_REFUSED, taken)


class StageBUsageTests(CliTestCase):
    """Что командная строка этапа B обязана отвергнуть до чтения среды."""

    def parse(self, argv):
        return build_parser().parse_args(argv)

    def refuses(self, argv):
        with self.assertRaises(UsageError):
            main_module.check_usage(self.parse(argv))

    def accepts(self, argv):
        main_module.check_usage(self.parse(argv))

    def test_lands_and_routes_together_are_refused(self):
        self.refuses(['--lands', '--routes', '--ids-file', 'x.txt'])

    def test_with_geometry_without_lands_is_refused(self):
        self.refuses(['--with-geometry'])

    def test_ids_file_without_routes_is_refused(self):
        self.refuses(['--ids-file', 'x.txt'])

    def test_geometry_id_without_lands_and_geometry_is_refused(self):
        self.refuses(['--geometry-id', 'u1'])
        self.refuses(['--lands', '--geometry-id', 'u1'])
        self.refuses(['--routes', '--ids-file', 'x.txt',
                      '--geometry-id', 'u1'])

    def test_routes_without_a_period_or_an_ids_file_is_refused(self):
        """--routes должен знать, ЧЬИ маршруты просить."""
        self.refuses(['--routes'])

    def test_routes_with_a_kind_is_refused(self):
        self.refuses(['--routes', '--ids-file', 'x.txt', '--kind', 'backfill'])

    def test_lands_with_a_period_is_still_refused(self):
        """Прежняя проверка не потерялась при переносе в check_usage."""
        self.refuses(['--lands', '--from', '2026-07-01', '--to', '2026-07-31'])

    def test_the_valid_combinations_pass(self):
        """Отрицательный контроль: проверка не отвергает всё подряд."""
        for argv in (['--routes', '--from', '2026-06-01', '--to', '2026-06-30'],
                     ['--routes', '--ids-file', 'x.txt'],
                     ['--routes', '--ids-file', 'x.txt', '--dry-run'],
                     ['--lands'],
                     ['--lands', '--with-geometry'],
                     ['--lands', '--with-geometry', '--dry-run'],
                     ['--lands', '--with-geometry', '--geometry-id', 'u1'],
                     ['--lands', '--with-geometry', '--geometry-id', 'u1',
                      '--geometry-id', 'u2', '--dry-run'],
                     ['--from', '2026-07-01', '--to', '2026-07-31'],
                     []):
            with self.subTest(' '.join(argv) or '(no flags)'):
                self.accepts(argv)


class StageBNeedsNoIngestTokenTests(CliTestCase):
    """--routes ничего не отправляет, значит токен ему не нужен.

    [REASON]: этап B кладёт собранное в очередь на диске; приёмники -- этап C.
    Требовать DRONE_API_TOKEN у прогона, который не сделает ни одного запроса
    к Vehicle Soft, значило бы отказать по причине, которая к нему не
    относится.
    """

    def test_a_route_run_without_a_token_fails_on_the_session_not_the_token(self):
        os.environ['DJI_STORAGE_STATE'] = MISSING_STATE
        code = main(['--routes', '--ids-file', MISSING_STATE])
        self.assertEqual(code, EXIT_SESSION,
                         'прогон маршрутов потребовал токен приёмника')

    def test_a_sending_run_without_a_token_still_fails_on_the_token(self):
        """Отрицательный контроль: у отправляющих прогонов правило прежнее."""
        os.environ['DJI_STORAGE_STATE'] = MISSING_STATE
        self.assertEqual(main(['--from', '2026-07-01', '--to', '2026-07-31']),
                         EXIT_CONFIG)


class _FakeLandCollector(object):
    """Подставной обход справочника: браузера нет, узлы заданы тестом."""

    nodes = []

    def __init__(self, cfg, log):
        self.cfg = cfg
        self.log = log
        self.context = object()

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def collect(self):
        return LandResult(lands=list(self.nodes), pages_captured=1,
                          total_count=len(self.nodes),
                          nodes_captured=len(self.nodes), self_duplicates=0,
                          rejected={}, complete=True)


class GeometryRunReachesNoIngestTests(CliTestCase):
    """Этап B не имеет права писать в Vehicle Soft -- ни одним путём.

    [REASON]: это был живой дефект, а не гипотеза. `--lands --with-geometry`
    без `--dry-run` считался ОТПРАВЛЯЮЩИМ прогоном: он требовал
    DRONE_API_TOKEN и доходил до `send_lands`, то есть постил весь справочник
    в `/drones/api/land_sync` и писал `field_contours` и строку
    `drone_sync_logs` на боевой системе. Ни один тест этого не ловил, потому
    что проверка «токен не нужен» покрывала только `--routes`.
    """

    def setUp(self):
        CliTestCase.setUp(self)
        self._directory = tempfile.TemporaryDirectory()
        self.addCleanup(self._directory.cleanup)
        self.root = Path(self._directory.name)
        state_file = self.root / 'storage_state.json'
        state_file.write_text('{"cookies": [], "origins": []}',
                              encoding='utf-8')
        os.environ['DJI_STORAGE_STATE'] = str(state_file)
        os.environ['DRONE_OUTBOX_DIR'] = str(self.root / 'outbox')

        self.sent = []
        self.dumped = []
        self._real_send_lands = main_module.send_lands
        self._real_dump = main_module.write_lands_dry_run
        self._real_collector = lands_module.LandCollector
        self._real_geometry = main_module._run_geometry

        def fake_send_lands(lands, cfg, **kwargs):
            self.sent.append(list(lands))
            from drone_collector.sender import LandSendResult
            return LandSendResult()

        self.geometry_calls = []

        def fake_geometry(collector, result, args, cfg, log, state):
            self.geometry_calls.append(list(args.geometry_ids or []))
            return EXIT_OK

        def fake_dump(lands, out_dir, total_count=None):
            self.dumped.append(list(lands))
            return self.root / 'lands_dry_run.json'

        main_module.send_lands = fake_send_lands
        main_module.write_lands_dry_run = fake_dump
        main_module._run_geometry = fake_geometry
        lands_module.LandCollector = _FakeLandCollector
        _FakeLandCollector.nodes = [{'uuid': 'u1'}, {'uuid': 'u2'}]

    def tearDown(self):
        main_module.send_lands = self._real_send_lands
        main_module.write_lands_dry_run = self._real_dump
        main_module._run_geometry = self._real_geometry
        lands_module.LandCollector = self._real_collector
        CliTestCase.tearDown(self)

    def test_a_real_geometry_run_never_calls_send_lands(self):
        code = main(['--lands', '--with-geometry'])
        self.assertEqual(code, EXIT_OK)
        self.assertEqual(self.sent, [],
                         'прогон геометрии отправил справочник в Vehicle Soft')
        self.assertEqual(self.geometry_calls, [[]])

    def test_a_real_geometry_run_needs_no_base_url_and_no_token(self):
        """Ни VEHICLE_SOFT_BASE_URL, ни DRONE_API_TOKEN в среде нет."""
        self.assertIsNone(os.environ.get('VEHICLE_SOFT_BASE_URL'))
        self.assertIsNone(os.environ.get('DRONE_API_TOKEN'))
        self.assertEqual(main(['--lands', '--with-geometry']), EXIT_OK)

    def test_a_filtered_geometry_run_also_sends_nothing(self):
        code = main(['--lands', '--with-geometry', '--geometry-id', 'u1'])
        self.assertEqual(code, EXIT_OK)
        self.assertEqual(self.sent, [])
        self.assertEqual(self.geometry_calls, [['u1']])

    def test_a_dry_geometry_run_also_sends_nothing(self):
        self.assertEqual(main(['--lands', '--with-geometry', '--dry-run']),
                         EXIT_OK)
        self.assertEqual(self.sent, [])

    def test_a_geometry_run_writes_no_lands_dry_run_dump(self):
        """Дамп справочника несёт `geometry.storage.signedURL` дословно.

        [REASON]: `write_lands_dry_run` кладёт узлы КАК ЕСТЬ, вместе с
        подписанными ссылками. Писать его в режиме геометрии значило бы
        положить на диск ровно те ссылки, которые этот модуль из файлов и
        держит.
        """
        self.assertEqual(main(['--lands', '--with-geometry', '--dry-run']),
                         EXIT_OK)
        self.assertEqual(self.dumped, [])

    def test_a_plain_dry_lands_run_still_writes_its_dump(self):
        """Отрицательный контроль: у обычного `--lands --dry-run` дамп остался."""
        self.assertEqual(main(['--lands', '--dry-run']), EXIT_OK)
        self.assertEqual(len(self.dumped), 1)

    def test_the_geometry_exit_code_reaches_the_caller(self):
        """Ненайденный uuid доезжает до кода выхода процесса, а не тонет."""
        main_module._run_geometry = (
            lambda *a, **k: EXIT_GEOMETRY_NOT_FOUND)
        self.assertEqual(
            main(['--lands', '--with-geometry', '--geometry-id', 'nope']),
            EXIT_GEOMETRY_NOT_FOUND)
        self.assertEqual(self.sent, [])

    def test_plain_lands_still_sends_the_snapshot(self):
        """Отрицательный контроль: обычный `--lands` не ослаблен."""
        os.environ['VEHICLE_SOFT_BASE_URL'] = 'https://vehicle.invalid'
        os.environ['DRONE_API_TOKEN'] = 'token-for-the-test'
        code = main(['--lands'])
        self.assertEqual(code, EXIT_OK)
        self.assertEqual(len(self.sent), 1,
                         'обычный --lands перестал отправлять справочник')
        self.assertEqual(len(self.sent[0]), 2)

    def test_plain_lands_without_a_token_still_fails_on_the_token(self):
        """Второй отрицательный контроль: требование токена там, где оно есть."""
        self.assertEqual(main(['--lands']), EXIT_CONFIG)
        self.assertEqual(self.sent, [])

    def test_the_rule_itself_names_the_geometry_run(self):
        """Правило живёт в одном месте, и оно проверяемо напрямую."""
        parse = build_parser().parse_args
        self.assertTrue(main_module.needs_no_ingest(
            parse(['--lands', '--with-geometry'])))
        self.assertTrue(main_module.needs_no_ingest(
            parse(['--lands', '--with-geometry', '--dry-run'])))
        self.assertTrue(main_module.needs_no_ingest(parse(['--routes',
                                                           '--ids-file',
                                                           'x.txt'])))
        self.assertFalse(main_module.needs_no_ingest(parse(['--lands'])),
                         'обычный --lands объявлен неотправляющим')
        self.assertFalse(main_module.needs_no_ingest(parse([])))


class StageBSummaryKeysTests(unittest.TestCase):

    def test_each_mode_has_its_own_summary_template(self):
        """Три набора счётчиков, а не один на всех.

        [REASON]: у прогонов почти нет общих счётчиков, и печатать двадцать
        прочерков от чужого шаблона -- значит показывать оператору «ошибки» там,
        где их нет. Это уже было решено для --lands; --routes добавляет третий.
        """
        self.assertNotEqual(main_module.ROUTE_SUMMARY_KEYS,
                            main_module.FLIGHT_SUMMARY_KEYS)
        self.assertNotEqual(main_module.ROUTE_SUMMARY_KEYS,
                            main_module.LAND_SUMMARY_KEYS)
        for keys in (main_module.ROUTE_SUMMARY_KEYS,
                     main_module.LAND_SUMMARY_KEYS,
                     main_module.FLIGHT_SUMMARY_KEYS):
            self.assertEqual(keys[0], 'mode')
            self.assertEqual(keys[-1], 'exit')

    def test_the_route_summary_reports_every_bucket_of_the_invariant(self):
        for key in ('ids_requested', 'routes_new', 'routes_duplicates',
                    'routes_missing', 'routes_errors'):
            self.assertIn(key, main_module.ROUTE_SUMMARY_KEYS)



def collect_result(flights, complete=True):
    """A CollectResult as the browser would return it, without a browser."""
    return CollectResult(
        flights=flights, pages_captured=1, total_pages=1,
        flights_captured=len(flights), self_duplicates=0, unidentified=0,
        rejected={}, ignored_detail=0, clicks=0, complete=complete,
        page_size=100, date_from=date(2026, 7, 1), date_to=date(2026, 7, 31))


if __name__ == '__main__':
    unittest.main()
