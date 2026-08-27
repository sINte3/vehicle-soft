# -*- coding: utf-8 -*-
"""Тесты наблюдения за штатным запросом маршрутов.

Браузера здесь нет: запрос и ответ подставные, и это принципиально -- модуль
устроен так, чтобы всё, кроме самой подписки Playwright, проверялось
синтетикой.

Главное проверяемое свойство -- НЕГАТИВНОЕ: наблюдатель не делает собственного
запроса, ничего не ставит в очередь и не выпускает наружу ни одного значения
заголовка. Значения в фикстурах выдуманы и подобраны так, чтобы их легко было
найти в любом файле и логе.
"""

import json
import tempfile
import unittest

from pathlib import Path

from drone_collector.route_ui_probe import (
    MAX_OBSERVATIONS,
    PROMPT_LINES,
    RouteUiProbe,
    describe_headers,
    is_ids_url,
    is_route_url,
    summarise_request_body,
    write_report,
)
from drone_collector.tests.test_route_decode import (FAKE_FLIGHT_ID, response,
                                                     route_record)

ROUTE_URL = 'https://kr-ag2-api.example.invalid/api/web/v2/flight_datas/flight_records'
IDS_URL = 'https://kr-ag2-api.example.invalid/api/web/v1/flight_records/only_all_ids'

# Значения выдуманные и приметные: их ищут в отчёте и в логе.
FAKE_SIGNATURE = 'SIGNATUREVALUE-NOT-REAL-0123456789abcdef'
FAKE_COOKIE = 'sid=COOKIEVALUE-NOT-REAL'
FAKE_TOKEN = 'TOKENVALUE-NOT-REAL'
FAKE_REQUEST_ID = '00000000-0000-4000-8000-000000000000'

HEADERS = {
    'Content-Type': 'application/json',
    'Signature': FAKE_SIGNATURE,
    'Cookie': FAKE_COOKIE,
    'X-Auth-Token': FAKE_TOKEN,
    'X-Timestamp': '1790000000',
    'Accept': '*/*',
}


class _FakeRequest(object):
    def __init__(self, method='POST', headers=None, post_data=None):
        self.method = method
        self._headers = dict(headers or {})
        self.post_data = post_data

    def all_headers(self):
        return dict(self._headers)


class _FakeResponse(object):
    def __init__(self, url, body=b'', status=200, request=None):
        self.url = url
        self.status = status
        self.request = request
        self._body = body

    def body(self):
        return self._body


class _QuietLog(object):
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


def request_body(count=9, data_type='simplified'):
    return json.dumps({'flight_record_ids': list(range(1, count + 1)),
                       'data_type': data_type})


def route_response(body=None, headers=None, post_data=None, status=200):
    return _FakeResponse(
        ROUTE_URL,
        body if body is not None else response([route_record()]),
        status=status,
        request=_FakeRequest(headers=headers or HEADERS,
                             post_data=post_data if post_data is not None
                             else request_body(count=1)))


# ─── Отбор запросов ──────────────────────────────────────────────────────────

class TestUrlMatching(unittest.TestCase):

    def test_the_route_endpoint_matches_on_any_api_version(self):
        self.assertTrue(is_route_url(ROUTE_URL))
        self.assertTrue(is_route_url(ROUTE_URL.replace('/v2/', '/v3/')))

    def test_the_ids_endpoint_is_recognised(self):
        self.assertTrue(is_ids_url(IDS_URL))

    def test_an_unrelated_url_matches_nothing(self):
        other = 'https://example.invalid/api/web/v1/flight_records?page=1'
        self.assertFalse(is_route_url(other))
        self.assertFalse(is_ids_url(other))


# ─── Заголовки: имена и длины, но не значения ────────────────────────────────

class TestHeaderDescription(unittest.TestCase):

    def setUp(self):
        self.described = describe_headers(HEADERS)

    def test_no_header_value_survives(self):
        text = json.dumps(self.described)
        for value in (FAKE_SIGNATURE, FAKE_COOKIE, FAKE_TOKEN):
            self.assertNotIn(value, text)

    def test_the_names_are_kept(self):
        self.assertIn('signature', self.described['header_names'])
        self.assertIn('cookie', self.described['header_names'])

    def test_the_length_of_a_sensitive_value_is_kept(self):
        by_name = {item['name']: item['value_length']
                   for item in self.described['sensitive_headers']}
        self.assertEqual(by_name['signature'], len(FAKE_SIGNATURE))
        self.assertEqual(by_name['cookie'], len(FAKE_COOKIE))

    def test_the_presence_of_a_signature_is_reported(self):
        self.assertTrue(self.described['carries_signature_like_header'])

    def test_the_presence_of_a_timestamp_is_reported(self):
        self.assertTrue(self.described['carries_timestamp_like_header'])

    def test_an_ordinary_request_reports_neither(self):
        """Отрицательный контроль: флаги различают два случая."""
        plain = describe_headers({'Accept': '*/*',
                                  'Content-Type': 'application/json'})
        self.assertFalse(plain['carries_signature_like_header'])
        self.assertFalse(plain['carries_timestamp_like_header'])
        self.assertEqual(plain['sensitive_headers'], [])


# ─── Тело запроса: числа, а не идентификаторы ────────────────────────────────

class TestRequestBodySummary(unittest.TestCase):

    def test_the_count_and_the_data_type_are_taken(self):
        summary = summarise_request_body(request_body(count=9))
        self.assertTrue(summary['parsed'])
        self.assertEqual(summary['flight_id_count'], 9)
        self.assertEqual(summary['data_type'], 'simplified')

    def test_the_identifiers_themselves_are_not_taken(self):
        summary = summarise_request_body(
            json.dumps({'flight_record_ids': [622715275, 622715274],
                        'data_type': 'simplified'}))
        text = json.dumps(summary)
        self.assertNotIn('622715275', text)
        self.assertNotIn('622715274', text)
        self.assertEqual(summary['flight_id_count'], 2)

    def test_the_key_names_are_kept_as_the_shape_of_the_request(self):
        summary = summarise_request_body(request_body())
        self.assertEqual(summary['body_keys'],
                         ['data_type', 'flight_record_ids'])

    def test_an_absent_body_is_named(self):
        summary = summarise_request_body(None)
        self.assertFalse(summary['parsed'])
        self.assertIn('no body', summary['detail'])

    def test_broken_json_is_named(self):
        summary = summarise_request_body('{"flight_record_ids": [')
        self.assertFalse(summary['parsed'])
        self.assertIn('not readable JSON', summary['detail'])

    def test_an_enormous_body_is_not_parsed(self):
        summary = summarise_request_body('{"a":1}' + 'x' * (1024 * 1024))
        self.assertFalse(summary['parsed'])
        self.assertIn('the cap is', summary['detail'])

    def test_a_control_character_drops_the_data_type(self):
        summary = summarise_request_body(
            json.dumps({'flight_record_ids': [1], 'data_type': 'bad\x00type'}))
        self.assertIsNone(summary['data_type'])


# ─── Наблюдение целиком ──────────────────────────────────────────────────────

class ProbeTestCase(unittest.TestCase):

    def setUp(self):
        self._directory = tempfile.TemporaryDirectory()
        self.addCleanup(self._directory.cleanup)
        self.root = Path(self._directory.name)
        self.log = _QuietLog()
        self.probe = RouteUiProbe(logger=self.log)

    def everything_written(self):
        parts = [self.log.text(), json.dumps(self.probe.report(),
                                             ensure_ascii=False)]
        for path in self.root.rglob('*'):
            if path.is_file():
                parts.append(path.read_text(encoding='utf-8', errors='replace'))
        return '\n'.join(parts)


class TestObservation(ProbeTestCase):

    def test_a_route_response_is_observed(self):
        self.probe.note_response(route_response())
        self.assertEqual(len(self.probe.observations), 1)
        seen = self.probe.observations[0].as_dict()
        self.assertEqual(seen['method'], 'POST')
        self.assertEqual(seen['http_status'], 200)
        self.assertEqual(seen['path'],
                         '/api/web/v2/flight_datas/flight_records')

    def test_an_unrelated_response_is_ignored(self):
        """Отрицательный контроль: слушают не всё подряд."""
        self.probe.note_response(_FakeResponse(
            'https://example.invalid/api/web/v1/flight_records?page=1',
            b'{"code": 0}'))
        self.assertEqual(self.probe.observations, [])

    def test_the_preceding_only_all_ids_is_noticed(self):
        self.probe.note_request(IDS_URL)
        self.probe.note_response(route_response())
        self.assertTrue(
            self.probe.observations[0].preceded_by_only_all_ids)

    def test_without_it_the_flag_is_false(self):
        """Отрицательный контроль: флаг различает два случая."""
        self.probe.note_response(route_response())
        self.assertFalse(
            self.probe.observations[0].preceded_by_only_all_ids)

    def test_a_binary_payload_is_decoded_and_counted(self):
        body = response([route_record(flight_id=FAKE_FLIGHT_ID)])
        self.probe.note_response(route_response(
            body=body, post_data=request_body(count=1)))
        seen = self.probe.observations[0]
        self.assertEqual(seen.payload_kind, 'BINARY')
        self.assertEqual(seen.decoded_routes, 1)
        self.assertEqual(seen.returned_id_count, 1)
        self.assertTrue(seen.ids_match)

    def test_a_count_mismatch_is_reported_without_the_identifiers(self):
        body = response([route_record(flight_id=FAKE_FLIGHT_ID)])
        self.probe.note_response(route_response(
            body=body, post_data=request_body(count=9)))
        seen = self.probe.observations[0]
        self.assertFalse(seen.ids_match)
        self.assertEqual(seen.request['flight_id_count'], 9)
        self.assertEqual(seen.returned_id_count, 1)
        self.assertNotIn(str(FAKE_FLIGHT_ID),
                         json.dumps(seen.as_dict(), ensure_ascii=False))

    def test_a_vendor_refusal_is_named_as_such(self):
        refusal = json.dumps({'status': 408, 'code': 408,
                              'msg': '请求时间无效',
                              'request_id': FAKE_REQUEST_ID}).encode('utf-8')
        self.probe.note_response(route_response(body=refusal))
        seen = self.probe.observations[0]
        self.assertEqual(seen.payload_kind, 'JSON_VENDOR_ERROR')
        self.assertIsNone(seen.decoded_routes)

    def test_identical_repeats_are_deduplicated(self):
        """Карта перезапрашивает маршруты при каждом изменении вида."""
        for _ in range(3):
            self.probe.note_response(route_response())
        self.assertEqual(len(self.probe.observations), 1)
        self.assertEqual(self.probe.observations[0].repeats, 3)
        self.assertEqual(self.probe.route_responses, 3)

    def test_the_number_of_observations_is_capped(self):
        for index in range(MAX_OBSERVATIONS + 5):
            body = response([route_record(flight_id=900000000 + index)])
            self.probe.note_response(route_response(body=body))
        self.assertEqual(len(self.probe.observations), MAX_OBSERVATIONS)
        self.assertEqual(self.probe.skipped_over_cap, 5)

    def test_a_broken_response_object_does_not_raise(self):
        """Слушатель внутри цикла Playwright не имеет права падать."""
        class Hostile(object):
            url = ROUTE_URL
            status = 200
            request = None

            def body(self):
                raise RuntimeError('gone')

        self.probe.note_response(Hostile())
        self.assertEqual(self.probe.route_responses, 1)


class TestNothingLeaks(ProbeTestCase):

    def test_no_header_value_reaches_the_log_or_the_report(self):
        self.probe.note_response(route_response())
        write_report(self.probe, self.root)
        haystack = self.everything_written()
        for value in (FAKE_SIGNATURE, FAKE_COOKIE, FAKE_TOKEN):
            self.assertNotIn(value, haystack)

    def test_no_request_id_reaches_the_log_or_the_report(self):
        refusal = json.dumps({'status': 408, 'code': 408,
                              'request_id': FAKE_REQUEST_ID}).encode('utf-8')
        self.probe.note_response(route_response(body=refusal))
        write_report(self.probe, self.root)
        haystack = self.everything_written()
        self.assertNotIn(FAKE_REQUEST_ID, haystack)
        self.assertNotIn('request_id', haystack)

    def test_no_response_body_reaches_the_report(self):
        body = response([route_record(flight_id=FAKE_FLIGHT_ID)])
        self.probe.note_response(route_response(body=body))
        target = write_report(self.probe, self.root)
        written = target.read_text(encoding='utf-8')
        self.assertNotIn(body.hex(), written)
        self.assertIn('response_sha256', written)

    def test_the_report_says_plainly_what_it_did_not_do(self):
        self.probe.note_response(route_response())
        document = json.loads(
            write_report(self.probe, self.root).read_text(encoding='utf-8'))
        self.assertTrue(document['nothing_was_queued'])
        self.assertTrue(document['nothing_was_sent'])
        self.assertTrue(document['no_request_was_made_by_this_tool'])

    def test_a_request_body_value_never_reaches_the_report_at_all(self):
        """Из тела берутся ЧИСЛА и имена ключей, значения остаются снаружи."""
        self.probe.note_response(route_response(
            headers={'X-Note': 'x', 'Accept': '*/*'},
            post_data=json.dumps({'flight_record_ids': [1],
                                  'data_type': 'simplified',
                                  'note': 'https://oss.aliyuncs.com/x'
                                          '?OSSAccessKeyId=LTAISECRET'})))
        written = write_report(self.probe, self.root).read_text(
            encoding='utf-8')
        self.assertNotIn('LTAISECRET', written)
        self.assertNotIn('aliyuncs', written)
        self.assertIn('"note"', written)     # имя ключа -- это наблюдение

    def test_a_marker_in_a_key_name_is_refused(self):
        """Та же дисциплина, что у очереди: проверка стоит на будущее."""
        self.probe.note_response(route_response(
            headers={'Accept': '*/*'},
            post_data=json.dumps({
                'flight_record_ids': [1],
                'data_type': 'simplified',
                'https://oss.aliyuncs.com/x?OSSAccessKeyId=LTAI': 1})))
        with self.assertRaises(ValueError):
            write_report(self.probe, self.root)
        self.assertEqual(list(self.root.glob('*.json')), [])

    def test_the_header_names_do_not_trip_the_check(self):
        """Отрицательный контроль: имя заголовка -- не удостоверение.

        `cookie`, `authorization`, `x-auth-token` -- маркеры проверки и
        одновременно то, ради чего отчёт и пишется. Значения при этом не
        выходят наружу вовсе.
        """
        self.probe.note_response(route_response())
        written = write_report(self.probe, self.root).read_text(
            encoding='utf-8')
        self.assertIn('x-auth-token', written)
        self.assertNotIn(FAKE_TOKEN, written)

    def test_an_ordinary_report_is_written(self):
        """Отрицательный контроль: проверка не глушит нормальный отчёт."""
        self.probe.note_response(route_response())
        self.assertTrue(write_report(self.probe, self.root).is_file())


class TestTheProbeAsksNothing(ProbeTestCase):
    """Наблюдатель наблюдает. Ничего другого он не умеет."""

    def test_the_module_has_no_request_making_call(self):
        import inspect

        import drone_collector.route_ui_probe as module
        source = inspect.getsource(module)
        for forbidden in ('page.evaluate', 'fetch(', 'requests.post',
                          'context.request', 'urlopen', 'http.client'):
            self.assertNotIn(forbidden, source,
                             'the probe grew a way to make its own request: %s'
                             % forbidden)

    def test_the_module_never_touches_the_outbox(self):
        import inspect

        import drone_collector.route_ui_probe as module
        source = inspect.getsource(module)
        for forbidden in ('Outbox', 'enqueue', 'KIND_ROUTE'):
            self.assertNotIn(forbidden, source)

    def test_the_module_never_touches_vehicle_soft(self):
        import inspect

        import drone_collector.route_ui_probe as module
        source = inspect.getsource(module)
        for forbidden in ('send_lands', 'flight_sync', 'land_sync', 'sender'):
            self.assertNotIn(forbidden, source)

    def test_the_prompt_tells_the_operator_what_it_will_not_do(self):
        text = '\n'.join(PROMPT_LINES)
        self.assertIn('makes no request of its own', text)
        self.assertIn('queues nothing', text)


if __name__ == '__main__':
    unittest.main(verbosity=2)
