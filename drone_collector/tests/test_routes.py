# -*- coding: utf-8 -*-
"""Тесты сбора маршрутов (`drone_collector/routes.py`).

Ни сети, ни браузера. Транспорт подставной, ответы собираются тем же
самодельным писателем protobuf, которым проверяется декодер, -- так фикстура
и ожидание не могут разъехаться молча.

Терминология проверок намеренно нейтральная: `route`, `геометрический
маршрут`, `кандидат покрытия`. Ни один тест не называет маршрут работой или
подтверждённым опрыскиванием.
"""

import json
import tempfile
import unittest

from pathlib import Path

from drone_collector.outbox import (KIND_ROUTE, Outbox, SecretInEnvelope)
from drone_collector.routes import (
    DEFAULT_DATA_TYPE, MAX_RESPONSE_BYTES, MAX_ROUTE_BATCH_SIZE,
    OBSERVED_DATA_TYPES, PAGE_FETCH_JS, Reconciliation, RouteFetch,
    RouteRequestRefused, RouteRun, RouteRunError, assert_data_type,
    build_request_body, chunk_ids, content_sha256, is_route_url,
    normalise_ids, parse_request_body, read_ids_file, route_body,
    write_dry_run)
from drone_collector.route_decode import decode_route_response

from drone_collector.tests.test_route_decode import (
    FAKE_FLIGHT_ID, f_bytes, f_fixed32, response, route_record)


def build_response(flight_ids, width=6.0, status=200, message='Success.'):
    """Ответ маршрутов для перечисленных идентификаторов."""
    records = [route_record(flight_id=value, width=width)
               for value in flight_ids]
    return response(records, status=status, message=message)


class FakeTransport(object):
    """Подставной транспорт: отдаёт заранее подготовленные ответы."""

    def __init__(self, *answers):
        self.answers = list(answers)
        self.calls = []

    def __call__(self, flight_ids, data_type):
        self.calls.append((list(flight_ids), data_type))
        answer = (self.answers.pop(0) if self.answers
                  else build_response(flight_ids))
        if isinstance(answer, Exception):
            raise answer
        if callable(answer):
            answer = answer(flight_ids)
        return RouteFetch(flight_ids, data_type, answer)


class RoutesTestCase(unittest.TestCase):

    def setUp(self):
        self._directory = tempfile.TemporaryDirectory()
        self.addCleanup(self._directory.cleanup)
        self.root = Path(self._directory.name)
        self.outbox = Outbox(self.root / 'outbox').prepare()
        self.slept = []

    def run_for(self, ids, transport=None, **kwargs):
        kwargs.setdefault('batch_pause_s', 0)
        run = RouteRun(self.outbox, transport or FakeTransport(),
                       logger=_QuietLog(), sleep_fn=self.slept.append,
                       **kwargs)
        return run, run.collect(ids)


class _QuietLog(object):
    """Логгер, который ничего не печатает, но помнит уровни сообщений."""

    def __init__(self):
        self.records = []

    def _note(self, level, message, *args):
        self.records.append((level, message % args if args else message))

    def info(self, message, *args):
        self._note('info', message, *args)

    def warning(self, message, *args):
        self._note('warning', message, *args)

    def error(self, message, *args):
        self._note('error', message, *args)

    def text(self):
        return '\n'.join(text for _level, text in self.records)


# ─── Эндпоинт и data_type ────────────────────────────────────────────────────

class TestEndpointMatching(unittest.TestCase):

    def test_the_route_endpoint_matches_on_any_api_version(self):
        for url in ('https://kr-ag2-api.dji.com/api/web/v2/flight_datas/'
                    'flight_records',
                    'https://kr-ag2-api.dji.com/api/web/v3/flight_datas/'
                    'flight_records'):
            self.assertTrue(is_route_url(url), url)

    def test_the_flight_list_endpoint_is_not_the_route_endpoint(self):
        """Отрицательный контроль: похожий путь не должен совпадать."""
        self.assertFalse(is_route_url(
            'https://www.djiag.com/api/web/v1/flight_records?page=1'))
        self.assertFalse(is_route_url(
            'https://kr-ag2-api.dji.com/ag-plot/api/graphql?name=lands'))
        self.assertFalse(is_route_url(None))


class TestDataTypeIsObservedNotGuessed(unittest.TestCase):

    def test_the_observed_value_is_accepted(self):
        self.assertEqual(assert_data_type('simplified'), 'simplified')

    def test_the_default_is_an_observed_value(self):
        self.assertIn(DEFAULT_DATA_TYPE, OBSERVED_DATA_TYPES)

    def test_an_unobserved_value_is_refused(self):
        for candidate in ('full', 'detailed', 'raw', 'complete', ''):
            with self.subTest(candidate):
                with self.assertRaises(RouteRunError):
                    assert_data_type(candidate)

    def test_the_refusal_says_why_and_forbids_trying_values(self):
        with self.assertRaises(RouteRunError) as caught:
            assert_data_type('full')
        self.assertIn('never by trying values', str(caught.exception))

    def test_only_values_actually_seen_in_traffic_are_listed(self):
        """Список наблюдений, а не перечень догадок.

        Единственное значение снято с живого запроса кабинета и записано как
        `V1_REQUEST_BODY_CONFIRMED`. Тест падает, если кто-то добавит второе
        значение, не сопроводив его наблюдением, -- это и есть напоминание.
        """
        self.assertEqual(OBSERVED_DATA_TYPES, ('simplified',))

    def test_a_run_refuses_an_unobserved_data_type_before_any_request(self):
        transport = FakeTransport()
        with self.assertRaises(RouteRunError):
            RouteRun(None, transport, logger=_QuietLog(), data_type='full')
        self.assertEqual(transport.calls, [],
                         'запрос ушёл на неподтверждённом data_type')


# ─── Тело запроса ────────────────────────────────────────────────────────────

class TestRequestBody(unittest.TestCase):

    CONFIRMED_IDS = [622715275, 622715274, 622715273, 622712504, 622708921,
                     622704107, 622696758, 622689299, 622683215]

    def test_the_body_has_exactly_the_confirmed_shape(self):
        body = build_request_body(self.CONFIRMED_IDS)
        self.assertEqual(sorted(body.keys()),
                         ['data_type', 'flight_record_ids'])
        self.assertEqual(body['data_type'], 'simplified')
        self.assertEqual(body['flight_record_ids'],
                         sorted(self.CONFIRMED_IDS))

    def test_the_body_carries_no_period_and_no_device(self):
        """Подтверждённое наблюдение: ни периода, ни устройства в теле нет."""
        body = build_request_body(self.CONFIRMED_IDS)
        for absent in ('period_from', 'period_to', 'start', 'end', 'device_id',
                       'device_sn', 'timestamp'):
            self.assertNotIn(absent, body)

    def test_an_empty_list_is_refused(self):
        with self.assertRaises(RouteRunError):
            build_request_body([])

    def test_ids_are_deduplicated_and_ordered(self):
        self.assertEqual(normalise_ids([3, 1, 2, 1]), [1, 2, 3])

    def test_a_non_numeric_id_is_refused(self):
        for bad in ('abc', None, 1.5, True, -1, 0):
            with self.subTest(repr(bad)):
                with self.assertRaises(RouteRunError):
                    normalise_ids([bad])

    def test_a_float_that_is_whole_is_still_refused(self):
        """Отрицательный контроль: 1.0 не «почти целое», а другой тип."""
        self.assertEqual(normalise_ids([5]), [5])
        with self.assertRaises(RouteRunError):
            normalise_ids([5.5])


class TestParseRequestBody(unittest.TestCase):

    def test_a_plain_body_parses(self):
        ids, data_type = parse_request_body(
            '{"flight_record_ids":[2,1],"data_type":"simplified"}')
        self.assertEqual(ids, [1, 2])
        self.assertEqual(data_type, 'simplified')

    def test_a_devtools_envelope_parses(self):
        envelope = json.dumps({
            'method': 'POST',
            'urlPath': '/api/web/v2/flight_datas/flight_records',
            'mimeType': 'application/json',
            'bodyText': '{"flight_record_ids":[7],"data_type":"simplified"}'})
        ids, data_type = parse_request_body(envelope)
        self.assertEqual(ids, [7])
        self.assertEqual(data_type, 'simplified')

    def test_garbage_is_refused(self):
        with self.assertRaises(RouteRunError):
            parse_request_body('{not json')


# ─── Пакеты ──────────────────────────────────────────────────────────────────

class TestBatching(unittest.TestCase):

    def test_the_batch_size_is_honoured(self):
        self.assertEqual(chunk_ids([1, 2, 3, 4, 5], 2),
                         [[1, 2], [3, 4], [5]])

    def test_the_batch_size_is_clamped_from_above(self):
        ids = list(range(1, MAX_ROUTE_BATCH_SIZE + 50))
        batches = chunk_ids(ids, 10_000)
        self.assertTrue(all(len(b) <= MAX_ROUTE_BATCH_SIZE for b in batches))

    def test_a_batch_size_below_one_becomes_one(self):
        self.assertEqual(chunk_ids([1, 2], 0), [[1], [2]])

    def test_an_empty_list_gives_no_batches(self):
        self.assertEqual(chunk_ids([], 5), [])


# ─── Сверка запрошенного с возвращённым ──────────────────────────────────────

class TestReconciliation(unittest.TestCase):

    def test_a_full_match_is_trustworthy(self):
        rec = Reconciliation([1, 2], [2, 1])
        self.assertEqual(rec.matched, [1, 2])
        self.assertEqual(rec.missing, [])
        self.assertEqual(rec.unexpected, [])
        self.assertTrue(rec.is_trustworthy)

    def test_a_missing_id_is_reported_but_still_trustworthy(self):
        rec = Reconciliation([1, 2, 3], [1, 3])
        self.assertEqual(rec.missing, [2])
        self.assertTrue(rec.is_trustworthy,
                        'отсутствующий маршрут -- не ошибка связки')

    def test_an_unexpected_id_breaks_trust(self):
        rec = Reconciliation([1, 2], [1, 2, 99])
        self.assertEqual(rec.unexpected, [99])
        self.assertFalse(rec.is_trustworthy)

    def test_a_none_id_does_not_count_as_returned(self):
        rec = Reconciliation([1], [None, 1])
        self.assertEqual(rec.returned, [1])
        self.assertTrue(rec.is_trustworthy)


# ─── Разбор маршрута в тело записи ───────────────────────────────────────────

class TestRouteBody(unittest.TestCase):

    def decode_one(self, width=6.0):
        decoded = decode_route_response(build_response([FAKE_FLIGHT_ID],
                                                       width=width))
        return decoded.routes[0]

    def test_a_recorded_width_is_kept(self):
        body = route_body(self.decode_one(width=6.0), DEFAULT_DATA_TYPE)
        self.assertAlmostEqual(body['spray_width_m'], 6.0, places=5)
        self.assertTrue(body['spray_width_recorded'])

    def test_a_missing_width_stays_null_and_is_never_substituted(self):
        """Решение владельца 2026-08-25: подстановка ширины запрещена."""
        body = route_body(self.decode_one(width=-1.0), DEFAULT_DATA_TYPE)
        self.assertIsNone(body['spray_width_m'])
        self.assertFalse(body['spray_width_recorded'])

    def test_a_zero_width_stays_null_too(self):
        body = route_body(self.decode_one(width=0.0), DEFAULT_DATA_TYPE)
        self.assertIsNone(body['spray_width_m'])

    def test_the_hardware_id_travels_with_the_route(self):
        """Побочная ценность: борта нет на строке вылета, а здесь он есть."""
        body = route_body(self.decode_one(), DEFAULT_DATA_TYPE)
        self.assertTrue(body['hardware_id'])

    def test_points_are_rounded_but_not_reordered(self):
        record = self.decode_one()
        body = route_body(record, DEFAULT_DATA_TYPE)
        self.assertEqual(body['point_count'], len(record.points))
        for stored, original in zip(body['points'], record.points):
            self.assertAlmostEqual(stored[0], original[0], places=6)
            self.assertAlmostEqual(stored[1], original[1], places=6)

    def test_latitude_and_longitude_do_not_swap(self):
        """Отрицательный контроль перепутанного порядка координат.

        Широта около 40, долгота около 64 -- заведомо разные, поэтому
        перестановка видна. Симметричная точка этот тест бы не различила.
        """
        body = route_body(self.decode_one(), DEFAULT_DATA_TYPE)
        latitude, longitude = body['points'][0]
        self.assertLess(latitude, 50.0)
        self.assertGreater(longitude, 60.0)

    def test_an_unknown_field_is_preserved_never_dropped(self):
        raw = build_response([FAKE_FLIGHT_ID])
        record = decode_route_response(
            response([route_record(flight_id=FAKE_FLIGHT_ID,
                                   extra=f_bytes(77, b'\x01\x02\x03'))])
        ).routes[0]
        self.assertTrue(record.unknown)
        body = route_body(record, DEFAULT_DATA_TYPE)
        entry = body['unknown_fields'][0]
        self.assertEqual(entry['field'], 77)
        self.assertEqual(entry['hex'], '010203')
        self.assertIn('sha256', entry)
        self.assertTrue(raw)

    def test_a_long_unknown_field_keeps_its_hash_but_not_its_bytes(self):
        record = decode_route_response(
            response([route_record(flight_id=FAKE_FLIGHT_ID,
                                   extra=f_bytes(78, b'z' * 2000))])
        ).routes[0]
        entry = route_body(record, DEFAULT_DATA_TYPE)['unknown_fields'][0]
        self.assertTrue(entry['truncated'])
        self.assertNotIn('hex', entry)
        self.assertEqual(entry['bytes'], 2000)

    def test_the_observed_data_type_is_stored_verbatim(self):
        body = route_body(self.decode_one(), 'simplified')
        self.assertEqual(body['data_type'], 'simplified')


class TestContentHash(unittest.TestCase):

    def test_the_same_body_hashes_the_same(self):
        body = {'a': 1, 'b': [1, 2]}
        self.assertEqual(content_sha256(body), content_sha256(dict(body)))

    def test_key_order_does_not_change_the_hash(self):
        self.assertEqual(content_sha256({'a': 1, 'b': 2}),
                         content_sha256({'b': 2, 'a': 1}))

    def test_a_changed_value_changes_the_hash(self):
        """Отрицательный контроль: хеш обязан различать разное."""
        self.assertNotEqual(content_sha256({'a': 1}), content_sha256({'a': 2}))


# ─── Прогон целиком ──────────────────────────────────────────────────────────

class TestHappyPath(RoutesTestCase):

    def test_every_requested_route_is_queued_once(self):
        ids = [900000001, 900000002, 900000003]
        _, result = self.run_for(ids, batch_size=2)
        self.assertEqual(result.requested, 3)
        self.assertEqual(result.new, 3)
        self.assertEqual(result.duplicates, 0)
        self.assertEqual(result.errors, 0)
        self.assertEqual(len(self.outbox.pending()), 3)

    def test_the_counter_invariant_holds(self):
        _, result = self.run_for([900000001, 900000002])
        self.assertTrue(result.invariant_holds, result.as_dict())

    def test_the_queued_envelope_is_a_route_and_names_its_flight(self):
        _, _result = self.run_for([900000007])
        envelope = self.outbox.read(self.outbox.pending()[0])
        self.assertEqual(envelope['kind'], KIND_ROUTE)
        self.assertEqual(envelope['identity'], '900000007')
        self.assertEqual(envelope['body']['dji_flight_id'], 900000007)

    def test_the_response_hash_travels_in_the_diagnostics(self):
        _, _result = self.run_for([900000008])
        envelope = self.outbox.read(self.outbox.pending()[0])
        self.assertEqual(len(envelope['diagnostics']['response_sha256']), 64)
        self.assertEqual(envelope['diagnostics']['observed_data_type'],
                         'simplified')

    def test_batching_splits_the_requests(self):
        transport = FakeTransport()
        self.run_for([1, 2, 3, 4, 5], transport=transport, batch_size=2)
        self.assertEqual([len(ids) for ids, _t in transport.calls], [2, 2, 1])

    def test_flights_without_a_recorded_width_are_counted(self):
        transport = FakeTransport(
            lambda ids: build_response(ids, width=-1.0))
        _, result = self.run_for([900000010], transport=transport)
        self.assertEqual(result.without_width, 1)
        envelope = self.outbox.read(self.outbox.pending()[0])
        self.assertIsNone(envelope['body']['spray_width_m'])


class TestIdempotenceAndResume(RoutesTestCase):

    def test_a_second_identical_run_queues_nothing_new(self):
        ids = [900000001, 900000002]
        self.run_for(ids)
        transport = FakeTransport()
        _, second = self.run_for(ids, transport=transport)
        self.assertEqual(second.new, 0)
        self.assertEqual(second.duplicates, 2)
        self.assertEqual(len(self.outbox.pending()), 2)
        self.assertEqual(transport.calls, [],
                         'возобновление снова запросило уже собранное')

    def test_a_run_stopped_midway_asks_only_for_what_is_left(self):
        self.run_for([900000001])
        transport = FakeTransport()
        _, result = self.run_for([900000001, 900000002], transport=transport)
        self.assertEqual([ids for ids, _t in transport.calls], [[900000002]])
        self.assertEqual(result.new, 1)
        self.assertEqual(result.duplicates, 1)
        self.assertTrue(result.invariant_holds)

    def test_an_already_sent_route_is_not_requested_again(self):
        self.run_for([900000001])
        self.outbox.mark_sent(self.outbox.pending()[0])
        transport = FakeTransport()
        _, result = self.run_for([900000001], transport=transport)
        self.assertEqual(transport.calls, [])
        self.assertEqual(result.duplicates, 1)

    def test_resume_can_be_switched_off(self):
        """Отрицательный контроль: без возобновления запрос уходит снова."""
        self.run_for([900000001])
        transport = FakeTransport()
        run = RouteRun(self.outbox, transport, logger=_QuietLog(),
                       sleep_fn=self.slept.append, batch_pause_s=0)
        result = run.collect([900000001], resume=False)
        self.assertEqual(len(transport.calls), 1)
        self.assertEqual(result.duplicates, 1, 'очередь всё равно не удвоилась')
        self.assertEqual(len(self.outbox.pending()), 1)

    def test_a_corrupt_queue_element_does_not_break_resume(self):
        self.run_for([900000001])
        broken = self.outbox.pending_dir / 'route_broken_0000000000000001.json'
        broken.write_text('{', encoding='utf-8')
        transport = FakeTransport()
        _, result = self.run_for([900000001], transport=transport)
        self.assertEqual(transport.calls, [])
        self.assertEqual(result.duplicates, 1)


class TestRetryAndBackoff(RoutesTestCase):

    def test_a_transport_failure_is_retried_and_then_succeeds(self):
        transport = FakeTransport(OSError('connection reset'),
                                  build_response([900000001]))
        _, result = self.run_for([900000001], transport=transport)
        self.assertEqual(len(transport.calls), 2)
        self.assertEqual(result.new, 1)
        self.assertEqual(result.errors, 0)
        self.assertEqual(self.slept, [2])

    def test_the_backoff_grows_and_then_gives_up(self):
        transport = FakeTransport(OSError('a'), OSError('b'), OSError('c'))
        _, result = self.run_for([900000001], transport=transport)
        self.assertEqual(len(transport.calls), 3, 'попыток не ровно три')
        self.assertEqual(self.slept, [2, 4], 'отступ не 2 с, затем 4 с')
        self.assertEqual(result.errors, 1)
        self.assertEqual(result.new, 0)
        self.assertTrue(result.invariant_holds)

    def test_retries_are_bounded_and_do_not_loop_forever(self):
        transport = FakeTransport(*[OSError('x') for _ in range(50)])
        self.run_for([900000001], transport=transport)
        self.assertEqual(len(transport.calls), 3)

    def test_an_empty_body_is_retried_not_accepted(self):
        transport = FakeTransport(b'', build_response([900000001]))
        _, result = self.run_for([900000001], transport=transport)
        self.assertEqual(result.new, 1)
        self.assertEqual(len(transport.calls), 2)

    def test_a_refusal_is_not_retried(self):
        """Отказ в доступе повторять бессмысленно: он не пройдёт трижды."""
        transport = FakeTransport(
            RouteRequestRefused('the cabinet said no'),
            build_response([900000001]))
        with self.assertRaises(RouteRequestRefused):
            self.run_for([900000001], transport=transport)
        self.assertEqual(len(transport.calls), 1)

    def test_batches_are_paced(self):
        run = RouteRun(self.outbox, FakeTransport(), logger=_QuietLog(),
                       sleep_fn=self.slept.append, batch_size=1,
                       batch_pause_s=0.5)
        run.collect([1, 2, 3])
        self.assertEqual(self.slept, [0.5, 0.5],
                         'пауза между пакетами не выдержана')


class TestResponseIsChecked(RoutesTestCase):

    def test_a_response_carrying_unrequested_routes_is_rejected_whole(self):
        transport = FakeTransport(build_response([900000001, 999999999]))
        _, result = self.run_for([900000001], transport=transport)
        self.assertEqual(result.new, 0)
        self.assertEqual(result.errors, 1)
        self.assertEqual(self.outbox.pending(), [],
                         'из недоверенного пакета что-то попало в очередь')

    def test_the_rejection_does_not_print_the_foreign_ids(self):
        logger = _QuietLog()
        run = RouteRun(self.outbox,
                       FakeTransport(build_response([900000001, 999999999])),
                       logger=logger, sleep_fn=self.slept.append,
                       batch_pause_s=0)
        run.collect([900000001])
        self.assertNotIn('999999999', logger.text())

    def test_a_missing_route_is_counted_as_missing_not_as_an_error(self):
        transport = FakeTransport(build_response([900000001]))
        _, result = self.run_for([900000001, 900000002], transport=transport)
        self.assertEqual(result.new, 1)
        self.assertEqual(result.missing, 1)
        self.assertEqual(result.errors, 0)
        self.assertTrue(result.invariant_holds)

    def test_a_non_ok_dji_status_stops_the_run_and_names_the_reason(self):
        transport = FakeTransport(
            build_response([900000001], status=401, message='Unauthorized'))
        with self.assertRaises(RouteRequestRefused) as caught:
            self.run_for([900000001], transport=transport)
        self.assertIn('401', str(caught.exception))
        self.assertEqual(self.outbox.pending(), [])

    def test_an_aborted_run_still_balances_its_counters(self):
        transport = FakeTransport(
            build_response([900000001], status=401, message='Unauthorized'))
        run = RouteRun(self.outbox, transport, logger=_QuietLog(),
                       sleep_fn=self.slept.append, batch_size=1,
                       batch_pause_s=0)
        with self.assertRaises(RouteRequestRefused):
            run.collect([900000001, 900000002, 900000003])
        # Счётчики не проверить снаружи иначе, чем через сам объект прогона:
        # исключение унесло результат. Достаточно того, что очередь пуста.
        self.assertEqual(self.outbox.pending(), [])

    def test_an_oversized_response_is_refused_without_being_decoded(self):
        transport = FakeTransport(b'x' * (MAX_RESPONSE_BYTES + 16))
        _, result = self.run_for([900000001], transport=transport)
        self.assertEqual(result.errors, 1)
        self.assertEqual(result.new, 0)
        self.assertEqual(self.outbox.pending(), [])

    def test_a_healthy_response_of_normal_size_is_accepted(self):
        """Отрицательный контроль к потолку размера."""
        _, result = self.run_for([900000001])
        self.assertEqual(result.new, 1)


class TestQuarantine(RoutesTestCase):

    def quarantine_dir(self):
        return self.root / 'quarantine'

    def test_an_undecodable_body_is_quarantined_and_the_run_continues(self):
        transport = FakeTransport(b'\xff\xff\xff\xff',
                                  build_response([900000002]))
        _, result = self.run_for([900000001, 900000002], transport=transport,
                                 batch_size=1,
                                 quarantine_dir=self.quarantine_dir())
        self.assertEqual(result.quarantined, 1)
        self.assertEqual(result.errors, 1)
        self.assertEqual(result.new, 1, 'прогон не продолжился после карантина')
        self.assertTrue(result.invariant_holds)

    def test_the_quarantined_body_is_named_by_its_hash(self):
        transport = FakeTransport(b'\xff\xff\xff\xff')
        self.run_for([900000001], transport=transport,
                     quarantine_dir=self.quarantine_dir())
        notes = sorted(self.quarantine_dir().glob('*.json'))
        bodies = sorted(self.quarantine_dir().glob('*.bin'))
        self.assertEqual(len(notes), 1)
        self.assertEqual(len(bodies), 1)
        self.assertEqual(notes[0].stem, bodies[0].stem)
        note = json.loads(notes[0].read_text(encoding='utf-8'))
        self.assertEqual(len(note['response_sha256']), 64)
        self.assertTrue(note['body_written'])

    def test_an_undecodable_body_carrying_a_secret_is_never_written(self):
        """Тело, которого мы не поняли, не кладётся на диск вслепую."""
        poisoned = b'\xff\xffhttps://oss.aliyuncs.com/f?OSSAccessKeyId=LTAI\xff'
        transport = FakeTransport(poisoned)
        self.run_for([900000001], transport=transport,
                     quarantine_dir=self.quarantine_dir())
        self.assertEqual(list(self.quarantine_dir().glob('*.bin')), [],
                         'подозрительное тело всё-таки записано')
        note = json.loads(
            sorted(self.quarantine_dir().glob('*.json'))[0]
            .read_text(encoding='utf-8'))
        self.assertFalse(note['body_written'])
        self.assertTrue(note['secret_markers'])
        self.assertNotIn('LTAI', json.dumps(note))

    def test_without_a_quarantine_directory_nothing_is_written(self):
        transport = FakeTransport(b'\xff\xff\xff\xff')
        _, result = self.run_for([900000001], transport=transport)
        self.assertEqual(result.errors, 1)
        self.assertFalse(self.quarantine_dir().exists())


class TestSecretsNeverReachTheQueue(RoutesTestCase):

    def test_a_signed_url_inside_the_payload_stops_the_record(self):
        """Полный цикл: подпись в поле маршрута -- отказ, а не запись.

        Поле `location` -- строка от DJI. Если бы в неё когда-нибудь попала
        подписанная ссылка, запись обязана не состояться.
        """
        # Запись собирается ЗАНОВО с отравленным `location`, а не правится
        # подменой байтов: у поля protobuf есть префикс длины, и подмена
        # строки на строку другой длины дала бы просто нечитаемое тело --
        # тест прошёл бы, ничего не проверив.
        transport = FakeTransport(response([_record_with_location(
            900000001,
            'https://oss.aliyuncs.com/x?OSSAccessKeyId=LTAI&Signature=zz')]))
        with self.assertRaises(SecretInEnvelope):
            self.run_for([900000001], transport=transport)
        self.assertEqual(self.outbox.pending(), [])

    def test_an_ordinary_route_is_not_refused(self):
        """Отрицательный контроль: проверка секретов пропускает нормальное."""
        _, result = self.run_for([900000001])
        self.assertEqual(result.new, 1)

    def test_no_queued_file_contains_a_secret_marker(self):
        self.run_for([900000001, 900000002])
        for path in self.outbox.pending():
            text = path.read_text(encoding='utf-8').lower()
            for marker in ('signedurl', 'ossaccesskeyid', 'authorization',
                           'set-cookie', 'storage_state', 'bearer '):
                self.assertNotIn(marker, text)


def _record_with_location(flight_id, location):
    """Запись маршрута с заданной строкой `location` (поле 18)."""
    from drone_collector.tests.test_route_decode import (
        FAKE_LAT, FAKE_LNG, f_double, f_varint, point)
    body = point(FAKE_LAT, FAKE_LNG) + point(FAKE_LAT, FAKE_LNG + 0.01)
    body += f_varint(2, flight_id)
    body += f_fixed32(3, 10000.0)
    body += f_bytes(7, f_double(1, FAKE_LAT) + f_double(2, FAKE_LNG))
    body += f_bytes(18, location)
    body += f_fixed32(26, 6.0)
    return body


class TestUnlinkedRoute(RoutesTestCase):

    def test_a_route_without_a_flight_id_is_counted_and_not_queued(self):
        headless = route_record(flight_id=900000001)
        # Убираем поле 2 (идентификатор вылета) из готовой записи.
        marker = b'\x10' + _varint(900000001)
        self.assertIn(marker, headless)
        transport = FakeTransport(response([headless.replace(marker, b'')]))
        _, result = self.run_for([900000001], transport=transport)
        self.assertEqual(result.unlinked, 1)
        self.assertEqual(result.new, 0)
        self.assertEqual(self.outbox.pending(), [])


def _varint(value):
    out = bytearray()
    while True:
        byte = value & 0x7F
        value >>= 7
        if value:
            out.append(byte | 0x80)
        else:
            out.append(byte)
            return bytes(out)


class TestDryRun(RoutesTestCase):

    def test_a_dry_run_queues_nothing(self):
        run, result = self.run_for([900000001, 900000002], dry_run=True)
        self.assertEqual(result.new, 2)
        self.assertEqual(self.outbox.pending(), [],
                         'сухой прогон что-то положил в очередь')
        self.assertEqual(len(run.prepared_bodies), 2)

    def test_the_dry_run_file_says_plainly_that_nothing_was_queued(self):
        run, result = self.run_for([900000001], dry_run=True)
        target = write_dry_run(result, run.prepared_bodies, self.root / 'out')
        document = json.loads(target.read_text(encoding='utf-8'))
        self.assertTrue(document['dry_run'])
        self.assertTrue(document['nothing_was_queued'])
        self.assertEqual(len(document['routes']), 1)

    def test_the_dry_run_file_carries_no_secret_marker(self):
        run, result = self.run_for([900000001], dry_run=True)
        target = write_dry_run(result, run.prepared_bodies, self.root / 'out')
        text = target.read_text(encoding='utf-8').lower()
        for marker in ('signedurl', 'ossaccesskeyid', 'authorization',
                       'storage_state', 'bearer '):
            self.assertNotIn(marker, text)


class TestIdsFile(RoutesTestCase):

    def write(self, text, name='ids.txt', encoding='utf-8'):
        path = self.root / name
        path.write_text(text, encoding=encoding)
        return path

    def test_one_id_per_line_with_comments(self):
        path = self.write('# заголовок\n900000002\n900000001  # первый\n\n')
        self.assertEqual(read_ids_file(path), [900000001, 900000002])

    def test_a_file_written_by_powershell_with_a_bom_reads(self):
        path = self.write('900000001\n', encoding='utf-8-sig')
        self.assertEqual(read_ids_file(path), [900000001])

    def test_a_bad_line_names_its_number(self):
        path = self.write('900000001\nnot-an-id\n')
        with self.assertRaises(RouteRunError) as caught:
            read_ids_file(path)
        self.assertIn('line 2', str(caught.exception))


class TestPageTransportScript(unittest.TestCase):

    def test_the_page_script_carries_no_credential_of_ours(self):
        lowered = PAGE_FETCH_JS.lower()
        for marker in ('token', 'signature', 'cookie', 'authorization',
                       'bearer', 'secret'):
            self.assertNotIn(marker, lowered)

    def test_the_page_script_lets_the_site_send_its_own_credentials(self):
        """Подпись, если она есть, ставит сайт. Мы её не формируем."""
        self.assertIn("credentials: 'include'", PAGE_FETCH_JS)
        self.assertIn('arrayBuffer', PAGE_FETCH_JS)

    def test_the_body_is_read_as_bytes_not_as_text(self):
        """Ответ -- octet-stream; чтение строкой испортило бы данные."""
        self.assertIn('Uint8Array', PAGE_FETCH_JS)
        self.assertNotIn('response.text()', PAGE_FETCH_JS)


if __name__ == '__main__':
    unittest.main()
