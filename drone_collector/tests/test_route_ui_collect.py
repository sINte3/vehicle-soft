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
