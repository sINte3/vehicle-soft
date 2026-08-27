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

import sys
import tempfile
import types

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

    def test_a_route_run_without_a_token_stops_on_the_transport_not_the_token(self):
        """Сбор маршрутов закрыт -- но НЕ из-за отсутствия токена приёмника.

        [REASON]: до правки этот тест ждал кода 2 (нет сессии) и этим
        доказывал, что токен не требуется. Теперь `--routes` останавливается
        РАНЬШЕ -- на опровергнутом транспорте, код 12, -- и утверждение о
        токене доказывается тем же способом: до конфигурации приёмника дело не
        доходит вовсе.
        """
        os.environ['DJI_STORAGE_STATE'] = MISSING_STATE
        code = main(['--routes', '--ids-file', MISSING_STATE])
        self.assertEqual(code, main_module.EXIT_ROUTE_TRANSPORT_DISABLED,
                         'сбор маршрутов не назвал причину остановки')

    def test_the_probe_without_a_token_fails_on_the_session_not_the_token(self):
        """Наблюдение токена приёмника тоже не требует."""
        os.environ['DJI_STORAGE_STATE'] = MISSING_STATE
        self.assertEqual(main(['--route-ui-probe']), EXIT_SESSION,
                         'наблюдение потребовало токен приёмника')

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
        # [REASON]: состояние с cookie, а не `{"cookies": [], "origins": []}`.
        # Пустое состояние -- это ровно то, что теперь отвергает
        # `inspect_session`, и фикстура, притворяющаяся сессией, должна
        # притворяться убедительно. Значение выдуманное.
        state_file.write_text(
            '{"cookies": [{"name": "sid", "value": "SYNTHETIC-NOT-REAL", '
            '"domain": ".example.invalid", "path": "/"}], "origins": []}',
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


class _FakePlaywrightModule(object):
    """Ровно та часть playwright, которой пользуется save_session_interactive."""

    def __init__(self, state_text, landed_url):
        self.state_text = state_text
        self.landed_url = landed_url
        outer = self

        class _Page(object):
            url = landed_url

            def set_default_timeout(self, ms):
                pass

            def goto(self, url, **kwargs):
                pass

        class _Context(object):
            def storage_state(self, path):
                Path(path).write_text(outer.state_text, encoding='utf-8')

            def close(self):
                pass

        class _Browser(object):
            def new_context(self, **kwargs):
                return _Context()

            def close(self):
                pass

        class _Chromium(object):
            def launch(self, **kwargs):
                return _Browser()

        class _Playwright(object):
            chromium = _Chromium()

            def __enter__(self):
                return self

            def __exit__(self, *exc):
                return False

        _Browser.new_page = lambda self, **kw: _Page()
        _Context.new_page = lambda self, **kw: _Page()
        self._playwright = _Playwright()

    def sync_playwright(self):
        return self._playwright


class SaveSessionExitCodeTests(CliTestCase):
    """`--save-session` обязан отличать сохранённую сессию от тридцати байт.

    [REASON]: первый живой пилот получил код 0 и слова «Session saved» при
    файле `{"cookies": [], "origins": []}`. Проверялся размер, а размер у него
    ненулевой.
    """

    def setUp(self):
        CliTestCase.setUp(self)
        self._directory = tempfile.TemporaryDirectory()
        self.addCleanup(self._directory.cleanup)
        self.root = Path(self._directory.name)
        self.target = self.root / 'storage_state.json'
        os.environ['DJI_STORAGE_STATE'] = str(self.target)
        self._saved_module = sys.modules.get('playwright.sync_api')
        self._saved_pkg = sys.modules.get('playwright')

    def tearDown(self):
        for name, value in (('playwright.sync_api', self._saved_module),
                            ('playwright', self._saved_pkg)):
            if value is None:
                sys.modules.pop(name, None)
            else:
                sys.modules[name] = value
        CliTestCase.tearDown(self)

    def install_playwright(self, state_text,
                           landed='https://www.djiag.com/records/list'):
        fake = _FakePlaywrightModule(state_text, landed)
        sys.modules['playwright'] = types.ModuleType('playwright')
        sys.modules['playwright.sync_api'] = fake
        # save_session_interactive делает `from playwright.sync_api import
        # sync_playwright`, поэтому имя должно лежать атрибутом модуля.
        fake.sync_playwright = fake.sync_playwright

    def run_save(self):
        import builtins
        real_input = builtins.input
        builtins.input = lambda prompt='': ''
        try:
            return main(['--save-session'])
        finally:
            builtins.input = real_input

    def test_an_empty_state_exits_non_zero_and_writes_no_session(self):
        self.install_playwright('{"cookies": [], "origins": []}')
        code = self.run_save()
        self.assertNotEqual(code, 0)
        self.assertEqual(code, EXIT_SESSION)
        self.assertFalse(self.target.exists(),
                         'пустое состояние всё-таки легло на место сессии')

    def test_a_usable_state_exits_zero_and_writes_the_session(self):
        """Отрицательный контроль: строгость не гасит нормальное сохранение."""
        self.install_playwright(
            '{"cookies": [{"name": "sid", "value": "SYNTHETIC-NOT-REAL"}], '
            '"origins": []}')
        self.assertEqual(self.run_save(), EXIT_OK)
        self.assertTrue(self.target.is_file())

    def test_an_empty_state_leaves_a_previous_session_alone(self):
        self.target.write_text(
            '{"cookies": [{"name": "sid", "value": "SYNTHETIC-OLD"}], '
            '"origins": []}', encoding='utf-8')
        before = self.target.read_text(encoding='utf-8')
        self.install_playwright('{"cookies": [], "origins": []}')
        self.assertEqual(self.run_save(), EXIT_SESSION)
        self.assertEqual(self.target.read_text(encoding='utf-8'), before)

    def test_no_partial_file_survives_a_refusal(self):
        self.install_playwright('{"cookies": [], "origins": []}')
        self.run_save()
        self.assertEqual(list(self.root.glob('*.partial')), [])

    # --- где оказался браузер ---------------------------------------------

    POPULATED_STATE = ('{"cookies": [{"name": "sid", "value": '
                       '"SYNTHETIC-FROM-THE-LOGIN-PAGE"}], "origins": []}')

    def test_the_login_page_is_refused_even_with_a_populated_state(self):
        """Страница входа ставит свои cookie -- и прошла бы структурную проверку.

        [REASON]: до правки несовпадение посадки только ПРЕДУПРЕЖДАЛО, и
        сохранение шло дальше. `/login` не пустая: у неё есть и cookie, и
        localStorage, поэтому состояние формы входа затёрло бы рабочую сессию,
        пройдя обе проверки содержимого.
        """
        self.install_playwright(self.POPULATED_STATE,
                                landed='https://www.djiag.com/login')
        self.assertEqual(self.run_save(), EXIT_SESSION)
        self.assertFalse(self.target.exists(),
                         'состояние страницы входа легло на место сессии')

    def test_another_host_is_refused_even_with_a_populated_state(self):
        self.install_playwright(self.POPULATED_STATE,
                                landed='https://elsewhere.invalid/records/list')
        self.assertEqual(self.run_save(), EXIT_SESSION)
        self.assertFalse(self.target.exists())

    def test_no_url_at_all_is_refused(self):
        self.install_playwright(self.POPULATED_STATE, landed='')
        self.assertEqual(self.run_save(), EXIT_SESSION)
        self.assertFalse(self.target.exists())

    def test_the_login_page_leaves_a_working_session_byte_for_byte(self):
        self.target.write_text(
            '{"cookies": [{"name": "sid", "value": "SYNTHETIC-OLD"}], '
            '"origins": []}', encoding='utf-8')
        before = self.target.read_bytes()
        self.install_playwright(self.POPULATED_STATE,
                                landed='https://www.djiag.com/login')
        self.assertEqual(self.run_save(), EXIT_SESSION)
        self.assertEqual(self.target.read_bytes(), before)
        self.assertEqual(list(self.root.glob('*.partial')), [])

    def test_another_host_leaves_a_working_session_byte_for_byte(self):
        self.target.write_text(
            '{"cookies": [{"name": "sid", "value": "SYNTHETIC-OLD"}], '
            '"origins": []}', encoding='utf-8')
        before = self.target.read_bytes()
        self.install_playwright(self.POPULATED_STATE,
                                landed='https://elsewhere.invalid/records/list')
        self.assertEqual(self.run_save(), EXIT_SESSION)
        self.assertEqual(self.target.read_bytes(), before)
        self.assertEqual(list(self.root.glob('*.partial')), [])

    def test_the_mission_page_is_refused_even_with_a_populated_state(self):
        """Другая страница того же кабинета тоже ставит свои cookie."""
        self.install_playwright(self.POPULATED_STATE,
                                landed='https://www.djiag.com/mission')
        self.assertEqual(self.run_save(), EXIT_SESSION)
        self.assertFalse(self.target.exists())

    def test_plain_http_on_the_right_host_is_refused(self):
        self.install_playwright(self.POPULATED_STATE,
                                landed='http://www.djiag.com/records/list')
        self.assertEqual(self.run_save(), EXIT_SESSION)
        self.assertFalse(self.target.exists())

    def test_the_mission_page_leaves_a_working_session_byte_for_byte(self):
        self.target.write_text(
            '{"cookies": [{"name": "sid", "value": "SYNTHETIC-OLD"}], '
            '"origins": []}', encoding='utf-8')
        before = self.target.read_bytes()
        self.install_playwright(self.POPULATED_STATE,
                                landed='https://www.djiag.com/mission')
        self.assertEqual(self.run_save(), EXIT_SESSION)
        self.assertEqual(self.target.read_bytes(), before)
        self.assertEqual(list(self.root.glob('*.partial')), [])

    def test_plain_http_leaves_a_working_session_byte_for_byte(self):
        self.target.write_text(
            '{"cookies": [{"name": "sid", "value": "SYNTHETIC-OLD"}], '
            '"origins": []}', encoding='utf-8')
        before = self.target.read_bytes()
        self.install_playwright(self.POPULATED_STATE,
                                landed='http://www.djiag.com/records/list')
        self.assertEqual(self.run_save(), EXIT_SESSION)
        self.assertEqual(self.target.read_bytes(), before)
        self.assertEqual(list(self.root.glob('*.partial')), [])

    def test_the_records_page_remains_the_successful_control(self):
        """Отрицательный контроль: правильная посадка по-прежнему сохраняет."""
        self.install_playwright(self.POPULATED_STATE,
                                landed='https://www.djiag.com/records/list')
        self.assertEqual(self.run_save(), EXIT_OK)
        self.assertTrue(self.target.is_file())


class RouteProbeExitCodeTests(CliTestCase):
    """Фактические коды выхода `--route-ui-probe`, а не только чистая функция.

    [REASON]: подтверждение проверялось на `confirmation_failures()`, а сам
    процесс мог вернуть что угодно. Здесь `main()` вызывается целиком, с
    подставными браузером и наблюдателем.
    """

    def setUp(self):
        CliTestCase.setUp(self)
        self._directory = tempfile.TemporaryDirectory()
        self.addCleanup(self._directory.cleanup)
        self.root = Path(self._directory.name)
        state_file = self.root / 'storage_state.json'
        state_file.write_text(
            '{"cookies": [{"name": "sid", "value": "SYNTHETIC-NOT-REAL"}], '
            '"origins": []}', encoding='utf-8')
        os.environ['DJI_STORAGE_STATE'] = str(state_file)
        self._real_collector = None
        self._real_probe = None

    def tearDown(self):
        from drone_collector import browser as browser_module
        from drone_collector import route_ui_probe as probe_module
        if self._real_collector is not None:
            browser_module.FlightCollector = self._real_collector
        if self._real_probe is not None:
            probe_module.RouteUiProbe = self._real_probe
        CliTestCase.tearDown(self)

    def install(self, observations, confirmed, skipped=0):
        """Подставные браузер и наблюдатель с заданным исходом."""
        from drone_collector import browser as browser_module
        from drone_collector import route_ui_probe as probe_module

        self._real_collector = browser_module.FlightCollector
        self._real_probe = probe_module.RouteUiProbe

        class _Page(object):
            def on(self, _event, _handler):
                pass

        class _Collector(object):
            def __init__(self, cfg, log):
                self.page = _Page()
                self.context = object()

            def __enter__(self):
                return self

            def __exit__(self, *exc):
                return False

            def open_records(self):
                pass

            def check_region(self, _expected):
                return 'Uzbekistan'

        class _Seen(object):
            def __init__(self, confirmed):
                self.confirmed = confirmed
                self.not_confirmed_because = ([] if confirmed
                                              else ['the payload did not '
                                                    'decode'])

            def as_dict(self):
                return {'confirmed_route_post': self.confirmed,
                        'not_confirmed_because':
                            list(self.not_confirmed_because)}

        class _Probe(object):
            def __init__(self, logger=None, expected_origin=None):
                self.observations = [_Seen(index < confirmed)
                                     for index in range(observations)]
                self.route_responses = observations
                self.saw_only_all_ids = 0
                self.skipped_over_cap = skipped

            @property
            def confirmed_observations(self):
                return [item for item in self.observations if item.confirmed]

            def note_request(self, _url):
                pass

            def note_response(self, _response):
                pass

            def report(self):
                return {'probe': 'route-ui',
                        'route_observations': len(self.observations),
                        'confirmed_route_posts':
                            len(self.confirmed_observations),
                        'skipped_over_cap': self.skipped_over_cap,
                        'observations': [item.as_dict()
                                         for item in self.observations],
                        'nothing_was_queued': True,
                        'nothing_was_sent_to_vehicle_soft': True,
                        'no_route_post_was_initiated_by_probe': True}

        browser_module.FlightCollector = _Collector
        probe_module.RouteUiProbe = _Probe

    def run_probe(self):
        import builtins
        real_input = builtins.input
        builtins.input = lambda prompt='': ''
        try:
            return main(['--route-ui-probe'])
        finally:
            builtins.input = real_input

    def test_every_observation_confirmed_exits_zero(self):
        self.install(observations=2, confirmed=2)
        self.assertEqual(self.run_probe(), EXIT_OK)

    def test_nothing_observed_exits_six(self):
        self.install(observations=0, confirmed=0)
        self.assertEqual(self.run_probe(), main_module.EXIT_EMPTY)

    def test_nothing_confirmed_exits_thirteen(self):
        self.install(observations=2, confirmed=0)
        self.assertEqual(self.run_probe(),
                         main_module.EXIT_ROUTE_PROBE_UNCONFIRMED)

    def test_a_mixed_result_exits_thirteen(self):
        """Один подтверждённый рядом с неподтверждённым -- не успех."""
        self.install(observations=2, confirmed=1)
        self.assertEqual(self.run_probe(),
                         main_module.EXIT_ROUTE_PROBE_UNCONFIRMED)

    def test_a_dropped_observation_exits_thirteen(self):
        """Про пропущенное по лимиту не известно ничего."""
        self.install(observations=2, confirmed=2, skipped=1)
        self.assertEqual(self.run_probe(),
                         main_module.EXIT_ROUTE_PROBE_UNCONFIRMED)


class RouteProbeHelpTextTests(unittest.TestCase):
    """`--help` не должен обещать того, чего код не доказывает."""

    def help_text(self):
        import io
        import contextlib
        buffer = io.StringIO()
        with contextlib.redirect_stdout(buffer):
            try:
                build_parser().parse_args(['--help'])
            except SystemExit:
                pass
        return buffer.getvalue()

    def test_the_broad_claim_is_gone(self):
        text = self.help_text()
        self.assertNotIn('Makes no request of its own', text)
        self.assertNotIn('makes no request of its own', text.lower())

    def test_the_proved_claim_is_there(self):
        text = self.help_text()
        self.assertIn('does not initiate the', text)
        self.assertIn('route POST', text)


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
