# -*- coding: utf-8 -*-
"""DRONE-USEFUL-AREA-001: операторский сбор маршрутов и отправка очереди.

Три свойства, которые дёшево сломать и дорого заметить:

1. **Наблюдение живёт по полному жизненному циклу.** Тело ответа объявляется
   не событием `response`, а `requestfinished`; наблюдатель, дёрнутый одним
   `note_response`, проверял бы модель, которой в жизни нет. Фикстура
   `deliver` проводит обмен через все четыре события, как это делает
   Playwright.
2. **Неполный набор в очередь не попадает вовсе.** Тело, которое не
   разобралось, или разошедшиеся множества идентификаторов дают ненулевой код
   и пустую очередь. Частично собранный день, попавший в базу, неотличим от
   полного -- работа получит меньше маршрутов, чем было, и посчитает
   полезную площадь как по полному входу.
3. **Отправленное помечается ПОСЛЕ ответа приёмника, а не до.** Обрыв связи
   оставляет записи в `pending/`, и следующий прогон их доотправит; обратный
   порядок терял бы маршруты ровно в том случае, ради которого очередь и
   существует.

Ни одного настоящего значения: хост `.invalid`, идентификаторы вылетов из
диапазона фикстур, подпись и cookie помечены NOT-REAL. Сети здесь нет --
транспорт внедряется.
"""

import json
import shutil
import tempfile
import unittest

from pathlib import Path

from drone_collector.outbox import KIND_ROUTE, Outbox
from drone_collector.route_ui_collect import (COLLECT_MODE_VERSION,
                                              RouteQueueCapture,
                                              content_sha256,
                                              drain_route_outbox,
                                              enqueue_routes, route_bodies)
from drone_collector.tests.test_route_decode import (FAKE_FLIGHT_ID, response,
                                                     route_record)
from drone_collector.tests.test_route_ui_probe import (HEADERS, ROUTE_ORIGIN,
                                                       _QuietLog, deliver,
                                                       ids_body,
                                                       route_response)

DECODER_VERSION = 'route-decode-2'


class _Cfg(object):
    """Ровно те поля конфигурации, которые читает отправка."""

    def __init__(self, url='https://vehicle-soft.example.invalid'):
        self.base_url = url
        self.api_token = 'SYNTHETIC-TOKEN-NOT-REAL'
        self.route_batch_size = 500

    @property
    def route_sync_url(self):
        return self.base_url.rstrip('/') + '/drones/api/route_sync'


def probe_with(bodies, origin=ROUTE_ORIGIN):
    """Наблюдатель, которому скормили перечисленные тела ответов."""
    capture = RouteQueueCapture(logger=_QuietLog(), expected_origin=origin)
    for body, ids in bodies:
        deliver(capture, route_response(body=body,
                                        post_data=ids_body(ids)))
    return capture


# ─── Захват ──────────────────────────────────────────────────────────────────

class Capture(unittest.TestCase):

    def test_a_well_formed_body_is_captured_through_the_full_lifecycle(self):
        capture = probe_with([(response([route_record()]), [FAKE_FLIGHT_ID])])

        self.assertEqual(capture.bodies_captured, 1)
        self.assertEqual(capture.decode_failures, 0)
        self.assertEqual(capture.capture_errors, 0)
        records = capture.captured_records()
        self.assertEqual([record.flight_id for record in records],
                         [FAKE_FLIGHT_ID])

    def test_the_same_flight_in_a_second_response_is_not_a_second_flight(self):
        capture = probe_with([
            (response([route_record()]), [FAKE_FLIGHT_ID]),
            (response([route_record()]), [FAKE_FLIGHT_ID])])
        self.assertEqual(capture.bodies_captured, 2)
        self.assertEqual(len(capture.captured_records()), 1)

    def test_a_body_that_does_not_decode_is_counted_not_swallowed(self):
        """Отказ декодера -- это ФАКТ, а не тишина.

        [REASON]: пропущенное молча тело означало бы, что день собран не
        полностью, и никто бы об этом не узнал. Причина при этом не
        печатается: она пришла бы вместе с содержимым тела.
        """
        capture = probe_with([(response([], status=101, message='no'),
                               [FAKE_FLIGHT_ID])])
        self.assertEqual(capture.decode_failures, 1)
        self.assertEqual(capture.bodies_captured, 0)
        self.assertEqual(capture.captured_records(), [])

    def test_a_capture_failure_never_takes_the_observation_down(self):
        """Сбой захвата считается и называется по типу; наблюдение живёт."""
        capture = RouteQueueCapture(logger=_QuietLog(),
                                    expected_origin=ROUTE_ORIGIN)

        def explode(_raw):
            raise RuntimeError('SYNTHETIC failure')

        capture._stash = explode
        deliver(capture, route_response(post_data=ids_body([FAKE_FLIGHT_ID])))

        self.assertEqual(capture.capture_errors, 1)
        # Наблюдение состоялось: ответ маршрута замечен.
        self.assertEqual(capture.route_responses, 1)
        self.assertEqual(len(capture.observations), 1)


# ─── Тела для очереди ────────────────────────────────────────────────────────

class Bodies(unittest.TestCase):

    def bodies(self):
        capture = probe_with([(response([route_record()]), [FAKE_FLIGHT_ID])])
        return route_bodies(capture.captured_records(), 'simplified',
                            DECODER_VERSION)

    def test_the_body_is_the_shape_the_endpoint_reads(self):
        body = self.bodies()[0]
        for key in ('dji_flight_id', 'points', 'point_count', 'spray_width_m',
                    'spray_width_recorded', 'decoder_version',
                    'collector_version', 'point_shape_census'):
            self.assertIn(key, body)
        self.assertEqual(body['dji_flight_id'], FAKE_FLIGHT_ID)
        self.assertEqual(body['point_count'], len(body['points']))
        self.assertEqual(body['decoder_version'], DECODER_VERSION)

    def test_no_secret_and_no_raw_body_travels_with_the_route(self):
        """Ни подписи, ни cookie, ни токена, ни сырого тела в записи."""
        text = json.dumps(self.bodies(), ensure_ascii=False).lower()
        for marker in ('signature', 'cookie', 'authorization', 'bearer',
                       'x-auth-token', 'set-cookie', 'raw_body',
                       'response_body'):
            self.assertNotIn(marker, text,
                             '%r must never travel with a route' % marker)

    def test_the_content_hash_ignores_nothing_that_matters(self):
        """Одно и то же тело -- один хеш; другое -- другой."""
        first = self.bodies()[0]
        self.assertEqual(content_sha256(first), content_sha256(dict(first)))
        changed = dict(first, points=first['points'][:1])
        self.assertNotEqual(content_sha256(first), content_sha256(changed))


# ─── Очередь ─────────────────────────────────────────────────────────────────

class Queue(unittest.TestCase):

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix='route_collect_')
        self.outbox = Outbox(Path(self.tmp) / 'outbox').prepare()
        capture = probe_with([(response([route_record()]), [FAKE_FLIGHT_ID])])
        self.bodies = route_bodies(capture.captured_records(), 'simplified',
                                   DECODER_VERSION)

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_queueing_twice_creates_one_entry(self):
        queued, duplicates = enqueue_routes(
            self.outbox, self.bodies,
            diagnostics={'mode_version': COLLECT_MODE_VERSION})
        self.assertEqual((queued, duplicates), (1, 0))

        queued, duplicates = enqueue_routes(
            self.outbox, self.bodies,
            diagnostics={'mode_version': COLLECT_MODE_VERSION})
        self.assertEqual((queued, duplicates), (0, 1))
        self.assertEqual(len(self.outbox.pending()), 1)

    def test_a_changed_route_is_a_new_entry(self):
        enqueue_routes(self.outbox, self.bodies)
        changed = [dict(self.bodies[0],
                        points=self.bodies[0]['points'] + [[40.0, 64.0]])]
        queued, duplicates = enqueue_routes(self.outbox, changed)
        self.assertEqual((queued, duplicates), (1, 0))
        self.assertEqual(len(self.outbox.pending()), 2)

    def test_the_entry_is_a_route_envelope_and_carries_its_version(self):
        enqueue_routes(self.outbox, self.bodies,
                       diagnostics={'mode_version': COLLECT_MODE_VERSION})
        envelope = self.outbox.read(self.outbox.pending()[0])
        self.assertEqual(envelope['kind'], KIND_ROUTE)
        self.assertEqual(envelope['identity'], FAKE_FLIGHT_ID)
        self.assertEqual(envelope['diagnostics']['mode_version'],
                         COLLECT_MODE_VERSION)


# ─── Отправка ────────────────────────────────────────────────────────────────

class Drain(unittest.TestCase):

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix='route_drain_')
        self.outbox = Outbox(Path(self.tmp) / 'outbox').prepare()
        self.log = _QuietLog()
        self.cfg = _Cfg()
        capture = probe_with([(response([route_record()]), [FAKE_FLIGHT_ID])])
        bodies = route_bodies(capture.captured_records(), 'simplified',
                              DECODER_VERSION)
        enqueue_routes(self.outbox, bodies)

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_an_accepted_batch_moves_the_entries_to_sent(self):
        seen = {}

        def fake_send(bodies, cfg, logger=None):
            seen['bodies'] = bodies
            seen['url'] = cfg.route_sync_url
            from drone_collector.sender import RouteSendResult
            return RouteSendResult().add(
                {'seen': 1, 'new': 1, 'updated': 0, 'unchanged': 0,
                 'errors': 0, 'unlinked': 0})

        result = drain_route_outbox(self.outbox, self.cfg, self.log,
                                    send_fn=fake_send)

        self.assertEqual(result.envelopes, 1)
        self.assertEqual(result.sent, 1)
        self.assertEqual(len(self.outbox.pending()), 0)
        self.assertEqual(len(self.outbox.sent()), 1)
        self.assertEqual(seen['url'],
                         'https://vehicle-soft.example.invalid'
                         '/drones/api/route_sync')
        self.assertEqual(seen['bodies'][0]['dji_flight_id'], FAKE_FLIGHT_ID)

    def test_a_transport_failure_leaves_the_entries_pending(self):
        """[REASON]: `mark_sent` только ПОСЛЕ ответа приёмника, никогда до.

        Обратный порядок терял бы маршруты ровно в том случае, ради которого
        файловая очередь и заведена: оборвалась сеть -- сходить в кабинет
        второй раз.
        """
        from drone_collector.sender import TransportError

        def failing_send(_bodies, _cfg, logger=None):
            raise TransportError('SYNTHETIC connection refused')

        with self.assertRaises(TransportError):
            drain_route_outbox(self.outbox, self.cfg, self.log,
                               send_fn=failing_send)

        self.assertEqual(len(self.outbox.pending()), 1)
        self.assertEqual(len(self.outbox.sent()), 0)

    def test_an_unreadable_entry_is_quarantined_and_the_rest_still_go(self):
        target = self.outbox.pending_dir / 'route_SYNTHETIC_broken.json'
        target.write_text('{not json', encoding='utf-8')

        def fake_send(bodies, _cfg, logger=None):
            from drone_collector.sender import RouteSendResult
            return RouteSendResult().add(
                {'seen': len(bodies), 'new': len(bodies), 'updated': 0,
                 'unchanged': 0, 'errors': 0, 'unlinked': 0})

        result = drain_route_outbox(self.outbox, self.cfg, self.log,
                                    send_fn=fake_send)

        self.assertEqual(result.corrupt, 1)
        self.assertEqual(result.sent, 1)
        self.assertEqual(len(self.outbox.corrupt()), 1)
        self.assertEqual(len(self.outbox.sent()), 1)

    def test_only_route_envelopes_are_drained(self):
        """Записи геометрии поля этот тракт не трогает: у них свой получатель."""
        from drone_collector.outbox import KIND_FIELD_GEOMETRY
        self.outbox.enqueue(KIND_FIELD_GEOMETRY, 'SYNTHETIC-CONTOUR',
                            {'geojson': {}}, 'a' * 64)

        def fake_send(bodies, _cfg, logger=None):
            from drone_collector.sender import RouteSendResult
            self.assertEqual(len(bodies), 1)
            return RouteSendResult().add(
                {'seen': 1, 'new': 1, 'updated': 0, 'unchanged': 0,
                 'errors': 0, 'unlinked': 0})

        result = drain_route_outbox(self.outbox, self.cfg, self.log,
                                    send_fn=fake_send)
        self.assertEqual(result.sent, 1)
        # Запись геометрии осталась в очереди нетронутой.
        self.assertEqual(len(self.outbox.pending()), 1)


# ─── Вердикт и коды возврата ─────────────────────────────────────────────────

class Verdict(unittest.TestCase):

    def call(self, **overrides):
        from drone_collector.main import collect_verdict
        kwargs = dict(operator_answered=True, drain_completed=True,
                      observations=1, confirmed=1, observation_errors=0,
                      capture_errors=0, pending_route_requests=0,
                      route_requests_failed=0, id_sets_matched=True,
                      decode_failures=0, routes_captured=3)
        kwargs.update(overrides)
        return collect_verdict(**kwargs)

    def code(self, **overrides):
        from drone_collector.main import collect_exit_code
        verdict = self.call(**overrides)
        return collect_exit_code(
            verdict,
            overrides.get('decode_failures', 0),
            overrides.get('id_sets_matched', True),
            overrides.get('routes_captured', 3))

    def test_a_clean_run_is_confirmed(self):
        self.assertTrue(self.call()['confirmed'])
        from drone_collector.main import EXIT_OK
        self.assertEqual(self.code(), EXIT_OK)

    def test_each_failure_names_itself(self):
        """Один общий отказ заставлял бы оператора гадать, что чинить."""
        cases = {
            'operator_answered': 'never confirmed',
            'drain_completed': 'had not settled',
        }
        for field, fragment in cases.items():
            verdict = self.call(**{field: False})
            self.assertFalse(verdict['confirmed'])
            self.assertTrue(any(fragment in reason
                                for reason in verdict['reasons']),
                            '%s did not name itself: %s'
                            % (field, verdict['reasons']))

    def test_nothing_captured_and_incomplete_traffic_get_different_codes(self):
        """[REASON]: «кабинет не довели до карты» и «трафик пришёл, но неполон»
        -- два разных действия оператора, и планировщик обязан их различать."""
        from drone_collector.main import (EXIT_COLLECT_INCOMPLETE,
                                          EXIT_COLLECT_UNCONFIRMED, EXIT_EMPTY)
        self.assertEqual(self.code(routes_captured=0), EXIT_EMPTY)
        self.assertEqual(self.code(decode_failures=1), EXIT_COLLECT_INCOMPLETE)
        self.assertEqual(self.code(id_sets_matched=False),
                         EXIT_COLLECT_INCOMPLETE)
        self.assertEqual(self.code(operator_answered=False),
                         EXIT_COLLECT_UNCONFIRMED)
        self.assertEqual(self.code(route_requests_failed=1),
                         EXIT_COLLECT_UNCONFIRMED)


# ─── Правила командной строки ────────────────────────────────────────────────

class Usage(unittest.TestCase):

    def parse(self, argv):
        from drone_collector.main import build_parser
        return build_parser().parse_args(argv)

    def refuses(self, argv):
        from drone_collector.main import UsageError, check_usage
        with self.assertRaises(UsageError, msg='%s was accepted' % argv):
            check_usage(self.parse(argv))

    def accepts(self, argv):
        from drone_collector.main import check_usage
        check_usage(self.parse(argv))

    def test_the_mode_stands_alone(self):
        self.accepts(['--route-ui-collect'])
        self.accepts(['--route-ui-collect', '--dry-run'])
        self.accepts(['--route-ui-collect', '--send-routes'])
        self.refuses(['--route-ui-collect', '--routes'])
        self.refuses(['--route-ui-collect', '--lands'])
        self.refuses(['--route-ui-collect', '--area-48h'])
        self.refuses(['--route-ui-collect', '--from', '2026-06-05',
                      '--to', '2026-06-05'])

    def test_sending_is_off_unless_asked_for_explicitly(self):
        """[REASON]: без флага режим не обращается к Vehicle Soft вовсе."""
        from drone_collector.main import needs_no_ingest
        self.assertTrue(needs_no_ingest(self.parse(['--route-ui-collect'])))
        self.assertFalse(needs_no_ingest(
            self.parse(['--route-ui-collect', '--send-routes'])))

    def test_send_routes_alone_and_with_dry_run_are_both_refused(self):
        self.refuses(['--send-routes'])
        # Сухой прогон ничего не ставит в очередь, значит отправлять нечего.
        self.refuses(['--route-ui-collect', '--dry-run', '--send-routes'])


if __name__ == '__main__':
    unittest.main()


# ─── Ремонт: пакет, принятый НЕ ПОЛНОСТЬЮ, не покидает очередь ───────────────

def counters_of(**overrides):
    """RouteSendResult с заданными счётчиками."""
    from drone_collector.sender import RouteSendResult
    body = {'seen': 1, 'new': 1, 'updated': 0, 'unchanged': 0, 'errors': 0,
            'unlinked': 0}
    body.update(overrides)
    return RouteSendResult().add(body)


class PartialAcceptance(unittest.TestCase):
    """Конверт переезжает в `sent/` ТОЛЬКО при полном принятии пакета.

    [REASON]: прежняя редакция переносила весь пакет при любом HTTP 200. Совет
    «синхронизировать вылеты и повторно отправить маршруты» становился
    невыполнимым: к моменту, когда оператор его читал, конвертов в `pending/`
    уже не было, и вернуть маршруты можно было только вторым походом в
    кабинет. Ровно та потеря, ради предотвращения которой очередь и лежит на
    диске.
    """

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix='route_partial_')
        self.outbox = Outbox(Path(self.tmp) / 'outbox').prepare()
        self.log = _QuietLog()
        self.cfg = _Cfg()
        capture = probe_with([(response([route_record()]), [FAKE_FLIGHT_ID])])
        bodies = route_bodies(capture.captured_records(), 'simplified',
                              DECODER_VERSION)
        enqueue_routes(self.outbox, bodies)
        self.assertEqual(len(self.outbox.pending()), 1)

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def drain_with(self, **overrides):
        def fake_send(_bodies, _cfg, logger=None):
            return counters_of(**overrides)
        return drain_route_outbox(self.outbox, self.cfg, self.log,
                                  send_fn=fake_send)

    def assert_kept(self, result, fragment):
        self.assertFalse(result.accepted)
        self.assertEqual(result.sent, 0)
        self.assertEqual(len(self.outbox.sent()), 0,
                         'nothing may reach sent/ on a partial acceptance')
        self.assertEqual(len(self.outbox.pending()), 1,
                         'the envelope must stay available for a repeat run')
        self.assertEqual(result.left_pending, 1)
        self.assertTrue(any(fragment in reason
                            for reason in result.refusal_reasons),
                        'no reason named %r: %s'
                        % (fragment, result.refusal_reasons))

    def test_an_unlinked_route_keeps_the_whole_batch(self):
        self.assert_kept(self.drain_with(seen=1, new=0, unlinked=1),
                         'flight Vehicle Soft does not have')

    def test_a_rejected_route_keeps_the_whole_batch(self):
        self.assert_kept(self.drain_with(seen=1, new=0, errors=1),
                         'rejected by the endpoint')

    def test_counters_that_do_not_add_up_keep_the_whole_batch(self):
        self.assert_kept(self.drain_with(seen=5, new=1),
                         'do not add up')

    def test_a_seen_below_the_sent_count_keeps_the_whole_batch(self):
        # Счётчики сходятся между собой, но приёмник увидел меньше, чем
        # отправлено: часть пакета до него не доехала, и какая -- не видно.
        self.assert_kept(self.drain_with(seen=0, new=0),
                         'saw 0 route(s) of the 1')

    def test_a_clean_full_acceptance_moves_every_envelope(self):
        """Отрицательный контроль. Без него всё выше было бы зелёным и у
        кода, который не переносит НИЧЕГО и никогда."""
        result = self.drain_with(seen=1, new=1)
        self.assertTrue(result.accepted)
        self.assertEqual(result.refusal_reasons, [])
        self.assertEqual(result.sent, 1)
        self.assertEqual(len(self.outbox.sent()), 1)
        self.assertEqual(len(self.outbox.pending()), 0)

    def test_a_repeat_send_of_the_kept_batch_is_accepted(self):
        """Повтор всего пакета безопасен: приёмник идемпотентен."""
        self.assert_kept(self.drain_with(seen=1, new=0, unlinked=1),
                         'flight Vehicle Soft does not have')
        # Вылеты синхронизировали -- тот же пакет уходит целиком и принимается.
        again = self.drain_with(seen=1, new=0, unchanged=1)
        self.assertTrue(again.accepted)
        self.assertEqual(again.sent, 1)
        self.assertEqual(len(self.outbox.pending()), 0)

    def test_the_refusal_rules_are_stated_once_and_checked_directly(self):
        from drone_collector.route_ui_collect import batch_refusal_reasons
        self.assertEqual(batch_refusal_reasons(counters_of(), 1), [])
        self.assertTrue(batch_refusal_reasons(counters_of(unlinked=1, new=0), 1))
        self.assertTrue(batch_refusal_reasons(counters_of(errors=1, new=0), 1))
        self.assertTrue(batch_refusal_reasons(counters_of(), 2))
        self.assertTrue(batch_refusal_reasons(None, 1))


# ─── Ремонт: смешанный живой сбор -- не успех ────────────────────────────────

class _FakePage(object):
    """Страница Playwright ровно в той части, которой пользуется режим.

    Ответы доставляются на ПЕРВОЙ прокачке событий, как это и происходит в
    жизни: тела приходят между сигналом оператора и концом drain.
    """

    def __init__(self, deliveries):
        self.deliveries = list(deliveries)
        self.handlers = {}
        self.pumps = 0

    def on(self, event, handler):
        self.handlers[event] = handler

    def wait_for_timeout(self, _ms):
        self.pumps += 1
        if self.pumps == 1:
            for deliver_one in self.deliveries:
                deliver_one(self.handlers)


class _FakeCollector(object):
    def __init__(self, page):
        self.page = page

    def __enter__(self):
        return self

    def __exit__(self, *_exc):
        return False

    def open_records(self):
        return None

    def check_region(self, _expected):
        return 'SYNTHETIC-REGION'


class _FakePrompt(object):
    """Оператор, который уже ответил. Настоящий читает stdin."""

    class _Done(object):
        @staticmethod
        def is_set():
            return True

    def __init__(self, answered=True):
        self.done = self._Done()
        self.answered = answered
        self.failed = False
        self.error_type = None


class _CollectCfg(object):
    """Конфигурация ровно в той части, которую читает режим."""

    def __init__(self, out_dir, outbox_dir):
        self.storage_state = 'SYNTHETIC-session.json'
        self.route_api_origin = ROUTE_ORIGIN
        self.expected_region = 'SYNTHETIC-REGION'
        self.out_dir = out_dir
        self.outbox_dir = outbox_dir
        self.route_probe_poll_ms = 10
        self.route_probe_wait_ms = 100
        self.route_probe_drain_ms = 100
        self.route_probe_quiet_ms = 10
        self.base_url = 'https://vehicle-soft.example.invalid'
        self.api_token = 'SYNTHETIC-TOKEN-NOT-REAL'
        self.route_batch_size = 500

    @property
    def route_sync_url(self):
        return self.base_url.rstrip('/') + '/drones/api/route_sync'


class RealCollectRun(unittest.TestCase):
    """Тесты водят НАСТОЯЩИЙ `_run_route_ui_collect`, а не чистую функцию.

    [REASON]: вердикт можно вызвать напрямую и получить верный ответ, а режим
    при этом передавать в него не тот счётчик — что и произошло:
    `skipped_over_cap` не доезжал вовсе. Проверка чистой функции такого
    расхождения не видит по построению.
    """

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix='route_run_')
        self.out_dir = Path(self.tmp) / 'out'
        self.outbox_dir = Path(self.tmp) / 'outbox'
        self.cfg = _CollectCfg(self.out_dir, self.outbox_dir)
        self.state = {}
        self._patched = []

    def tearDown(self):
        for module, name, original in reversed(self._patched):
            setattr(module, name, original)
        shutil.rmtree(self.tmp, ignore_errors=True)

    def patch(self, module, name, value):
        self._patched.append((module, name, getattr(module, name)))
        setattr(module, name, value)

    def run_collect(self, deliveries, send_routes=False, dry_run=False):
        import sys
        import types

        from drone_collector import main as main_module
        from drone_collector import route_ui_probe as probe_module

        page = _FakePage(deliveries)

        fake_browser = types.ModuleType('drone_collector.browser')
        fake_browser.BrowserError = type('BrowserError', (Exception,), {})
        fake_browser.PeriodVerificationFailed = type(
            'PeriodVerificationFailed', (Exception,), {})
        fake_browser.RegionMismatch = type('RegionMismatch', (Exception,), {})
        fake_browser.SessionExpired = type('SessionExpired', (Exception,), {})
        fake_browser.FlightCollector = lambda _cfg, _log: _FakeCollector(page)

        original_browser = sys.modules.get('drone_collector.browser')
        sys.modules['drone_collector.browser'] = fake_browser
        # Сессии на диске нет, и ходить за ней некуда.
        self.patch(main_module, 'require_session', lambda _path: _path)
        self.patch(probe_module, 'start_operator_prompt',
                   lambda _text: _FakePrompt())
        try:
            args = types.SimpleNamespace(dry_run=dry_run,
                                         send_routes=send_routes)
            code = main_module._run_route_ui_collect(args, self.cfg,
                                                     _QuietLog(), self.state)
        finally:
            if original_browser is None:
                sys.modules.pop('drone_collector.browser', None)
            else:
                sys.modules['drone_collector.browser'] = original_browser
        return code, page

    def queued(self):
        if not self.outbox_dir.exists():
            return []
        return Outbox(self.outbox_dir).prepare().pending()

    @staticmethod
    def confirmed_delivery(flight_id=FAKE_FLIGHT_ID):
        def deliver_one(handlers):
            resp = route_response(body=response([route_record(flight_id=flight_id)]),
                                  post_data=ids_body([flight_id]))
            request = resp.request
            handlers['request'](request)
            handlers['response'](resp)
            handlers['requestfinished'](request)
        return deliver_one

    @staticmethod
    def unconfirmed_delivery(flight_id=FAKE_FLIGHT_ID + 500):
        """Тот же адрес, но HTTP 500: наблюдение есть, подтверждения нет.

        [REASON]: именно этот вид опаснее всего и потому взят в тест. Тело
        разбирается, маршрут ЗАХВАТЫВАЕТСЯ — и при прежнем вердикте уезжал бы
        в очередь только потому, что рядом оказался подтверждённый ответ.
        Ответ на чужом хосте для этой проверки не годится: он отбрасывается
        раньше и наблюдением не становится вовсе.
        """
        def deliver_one(handlers):
            resp = route_response(
                body=response([route_record(flight_id=flight_id)]),
                post_data=ids_body([flight_id]), status=500)
            request = resp.request
            handlers['request'](request)
            handlers['response'](resp)
            handlers['requestfinished'](request)
        return deliver_one

    # ── Проверки ────────────────────────────────────────────────────────────

    def test_a_clean_fully_confirmed_run_queues_and_exits_zero(self):
        """Отрицательный контроль ко всем остальным: успех вообще достижим."""
        from drone_collector.main import EXIT_OK
        code, _page = self.run_collect([self.confirmed_delivery()])
        self.assertEqual(code, EXIT_OK)
        self.assertTrue(self.state['collect_live_confirmed'])
        self.assertEqual(self.state['collect_routes_queued'], 1)
        self.assertEqual(len(self.queued()), 1)

    def test_one_confirmed_and_one_unconfirmed_response_is_not_a_success(self):
        """Смешанный прогон: код не 0 и очередь ПУСТА.

        [REASON]: прежний вердикт отказывал только при `confirmed == 0`, и
        один подтверждённый POST рядом с неподтверждённым ответом объявлялся
        успехом. Данные из неподтверждённого ответа попадали бы в базу оттого,
        что рядом оказался подтверждённый.
        """
        from drone_collector.main import EXIT_COLLECT_UNCONFIRMED
        code, _page = self.run_collect([self.confirmed_delivery(),
                                        self.unconfirmed_delivery()])
        self.assertEqual(code, EXIT_COLLECT_UNCONFIRMED)
        self.assertFalse(self.state['collect_live_confirmed'])
        self.assertEqual(self.queued(), [],
                         'an unconfirmed observation must leave the queue '
                         'untouched, even beside a confirmed one')
        self.assertGreater(self.state['probe_observations'], 1)
        # И маршрут из неподтверждённого ответа БЫЛ захвачен -- то есть без
        # этой проверки он уехал бы в очередь.
        self.assertGreater(self.state['collect_routes_captured'], 1)

    def test_an_observation_skipped_over_the_cap_is_not_a_success(self):
        """Про выпавшее по лимиту наблюдение не известно ничего."""
        from drone_collector.main import EXIT_COLLECT_UNCONFIRMED
        from drone_collector.route_ui_probe import MAX_OBSERVATIONS

        deliveries = [self.confirmed_delivery(FAKE_FLIGHT_ID + index)
                      for index in range(MAX_OBSERVATIONS + 1)]
        code, _page = self.run_collect(deliveries)
        self.assertEqual(self.state['probe_skipped_over_cap'], 1)
        self.assertEqual(code, EXIT_COLLECT_UNCONFIRMED)
        self.assertEqual(self.queued(), [])

    def test_the_mode_passes_the_cap_counter_to_the_verdict(self):
        """Счётчик доезжает до решения, а не теряется по дороге."""
        from drone_collector.route_ui_probe import MAX_OBSERVATIONS
        deliveries = [self.confirmed_delivery(FAKE_FLIGHT_ID + index)
                      for index in range(MAX_OBSERVATIONS + 1)]
        self.run_collect(deliveries)
        self.assertIn('probe_skipped_over_cap', self.state)
        self.assertEqual(self.state['probe_skipped_over_cap'], 1)

    def test_a_dry_run_queues_nothing_even_when_fully_confirmed(self):
        from drone_collector.main import EXIT_OK
        code, _page = self.run_collect([self.confirmed_delivery()],
                                       dry_run=True)
        self.assertEqual(code, EXIT_OK)
        self.assertEqual(self.state['collect_routes_queued'], 0)
        self.assertEqual(self.queued(), [])


class VerdictAgreesWithTheProbe(unittest.TestCase):
    """Решение сбора не имеет права быть слабее решения наблюдения."""

    def observation_half(self, **overrides):
        from drone_collector.main import collect_verdict
        kwargs = dict(operator_answered=True, drain_completed=True,
                      observations=2, confirmed=2, skipped_over_cap=0,
                      observation_errors=0, capture_errors=0,
                      pending_route_requests=0, route_requests_failed=0,
                      id_sets_matched=True, decode_failures=0,
                      routes_captured=3)
        kwargs.update(overrides)
        return collect_verdict(**kwargs)

    def test_every_observation_shape_matches_probe_exit_code(self):
        from drone_collector.route_ui_probe import (PROBE_EXIT_OK,
                                                    probe_exit_code)
        shapes = [
            {}, {'confirmed': 1}, {'confirmed': 0},
            {'skipped_over_cap': 1}, {'observation_errors': 1},
            {'observations': 0, 'confirmed': 0}, {'operator_answered': False},
            {'drain_completed': False},
        ]
        for shape in shapes:
            verdict = self.observation_half(**shape)
            expected = probe_exit_code(
                observations=shape.get('observations', 2),
                confirmed=shape.get('confirmed', 2),
                skipped_over_cap=shape.get('skipped_over_cap', 0),
                observation_errors=shape.get('observation_errors', 0),
                drain_timed_out=not shape.get('drain_completed', True),
                operator_answered=shape.get('operator_answered', True))
            if expected != PROBE_EXIT_OK:
                self.assertFalse(verdict['confirmed'],
                                 'probe refuses %s but collect accepted it'
                                 % shape)
            self.assertEqual(verdict['probe_exit'], expected, shape)
