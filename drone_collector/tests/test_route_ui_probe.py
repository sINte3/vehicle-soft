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
import threading
import unittest

from pathlib import Path

from drone_collector.route_ui_probe import (
    DJI_ENVELOPE_STATUS_OK,
    MAX_OBSERVATIONS,
    MAX_PROCESSED_RESPONSE_BYTES,
    MAX_RESPONSE_BYTES,
    PROMPT_LINES,
    RouteUiProbe,
    compare_id_sets,
    describe_headers,
    probe_exit_code,
    is_ids_url,
    is_route_url,
    NOT_READ,
    observation_id,
    PAYLOAD_KIND_UNREADABLE,
    ProbeTimingError,
    pump_until,
    read_request_ids,
    start_operator_prompt,
    UNNAMED_EXCEPTION,
    validate_probe_timings,
    summarise_request_body,
    WITHHELD_DATA_TYPE,
    write_report,
)
from drone_collector.tests.test_route_decode import (FAKE_FLIGHT_ID, response,
                                                     route_record)

ROUTE_ORIGIN = 'https://kr-ag2-api.example.invalid'
ROUTE_URL = ROUTE_ORIGIN + '/api/web/v2/flight_datas/flight_records'
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
    """Запрос Playwright: у него есть URL и, после ответа, сам ответ."""

    def __init__(self, method='POST', headers=None, post_data=None,
                 url=ROUTE_URL):
        self.method = method
        self.url = url
        self._headers = dict(headers or {})
        self.post_data = post_data
        self.response_object = None

    def all_headers(self):
        return dict(self._headers)

    def response(self):
        return self.response_object


class _FakeResponse(object):
    def __init__(self, url, body=b'', status=200, request=None):
        self.url = url
        self.status = status
        self.request = request
        self._body = body
        if request is not None:
            request.response_object = self
            # В Playwright у запроса и ответа один URL. Фикстура держит это
            # соответствие: иначе тест «чужой хост» проверял бы не то.
            request.url = url

    def body(self):
        return self._body


def deliver(probe, response, request=None):
    """Провести обмен через ВЕСЬ жизненный цикл, как это делает Playwright.

    [REASON]: `response` в Playwright приходит на статусе и заголовках, а тело
    объявляется отдельным событием `requestfinished`. Тест, дёргающий один
    `note_response`, проверял бы модель, которой в жизни нет.
    """
    if request is None:
        request = getattr(response, 'request', None)
    if request is not None:
        probe.note_request(request)
    probe.note_response(response)
    if request is not None:
        probe.note_request_finished(request)
    return response


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


def ids_body(ids, data_type='simplified'):
    return json.dumps({'flight_record_ids': list(ids),
                       'data_type': data_type})


def route_record_without_id():
    """Маршрут без поля 2 -- он ни с каким вылетом не связывается."""
    from drone_collector.tests.test_routes import _varint
    record = route_record(flight_id=900000777)
    marker = b'\x10' + _varint(900000777)
    assert marker in record
    return record.replace(marker, b'')


def route_response(body=None, headers=None, post_data=None, status=200,
                   method='POST'):
    return _FakeResponse(
        ROUTE_URL,
        body if body is not None else response([route_record()]),
        status=status,
        request=_FakeRequest(method=method, headers=headers or HEADERS,
                             post_data=post_data if post_data is not None
                             else request_body(count=1)))


def confirmable(flight_id=900000001, status=200, method='POST',
                envelope_status=200):
    """Обмен, который проходит ВСЕ условия подтверждения.

    Меняя один аргумент, получаем ровно один отличающийся признак -- на этом
    строятся и отрицательные проверки, и положительные контроли.
    """
    return route_response(
        body=response([route_record(flight_id=flight_id)],
                      status=envelope_status),
        post_data=ids_body([flight_id]), status=status, method=method)


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

    def test_the_presence_of_a_credential_is_reported_separately(self):
        self.assertTrue(self.described['carries_credential_like_header'])

    def test_a_cookie_alone_does_not_mean_the_request_is_signed(self):
        """Отрицательный контроль разделения признаков.

        [REASON]: один список отвечал на оба вопроса, и `Cookie` -- обычное
        удостоверение сессии, которое есть у любого запроса, -- поднимал флаг
        «запрос подписан». Вопрос, ради которого probe существует, звучит
        иначе: несёт ли ШТАТНЫЙ запрос ту подпись, которой не было у нашего
        `fetch`.
        """
        cookie_only = describe_headers({'Cookie': FAKE_COOKIE,
                                        'Accept': '*/*'})
        self.assertFalse(cookie_only['carries_signature_like_header'])
        self.assertTrue(cookie_only['carries_credential_like_header'])
        self.assertEqual(cookie_only['sensitive_headers'],
                         [{'name': 'cookie',
                           'value_length': len(FAKE_COOKIE)}])

    def test_an_authorization_alone_does_not_mean_signed_either(self):
        described = describe_headers({'Authorization': 'Bearer NOT-REAL'})
        self.assertFalse(described['carries_signature_like_header'])
        self.assertTrue(described['carries_credential_like_header'])

    def test_a_signature_alone_does_not_mean_a_credential(self):
        described = describe_headers({'Signature': FAKE_SIGNATURE})
        self.assertTrue(described['carries_signature_like_header'])
        self.assertFalse(described['carries_credential_like_header'])

    def test_the_presence_of_a_timestamp_is_reported(self):
        self.assertTrue(self.described['carries_timestamp_like_header'])

    def test_an_ordinary_request_reports_neither(self):
        """Отрицательный контроль: флаги различают два случая."""
        plain = describe_headers({'Accept': '*/*',
                                  'Content-Type': 'application/json'})
        self.assertFalse(plain['carries_signature_like_header'])
        self.assertFalse(plain['carries_credential_like_header'])
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
            json.dumps({'flight_record_ids': [900000011, 900000012],
                        'data_type': 'simplified'}))
        text = json.dumps(summary)
        self.assertNotIn('900000011', text)
        self.assertNotIn('900000012', text)
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
        self.probe = RouteUiProbe(logger=self.log,
                                  expected_origin=ROUTE_ORIGIN)

    def everything_written(self):
        parts = [self.log.text(), json.dumps(self.probe.report(),
                                             ensure_ascii=False)]
        for path in self.root.rglob('*'):
            if path.is_file():
                parts.append(path.read_text(encoding='utf-8', errors='replace'))
        return '\n'.join(parts)


class TestObservation(ProbeTestCase):

    def test_a_route_response_is_observed(self):
        deliver(self.probe, route_response())
        self.assertEqual(len(self.probe.observations), 1)
        seen = self.probe.observations[0].as_dict()
        self.assertEqual(seen['method'], 'POST')
        self.assertEqual(seen['http_status'], 200)
        self.assertEqual(seen['path'],
                         '/api/web/v2/flight_datas/flight_records')

    def test_an_unrelated_response_is_ignored(self):
        """Отрицательный контроль: слушают не всё подряд."""
        deliver(self.probe, _FakeResponse(
            'https://example.invalid/api/web/v1/flight_records?page=1',
            b'{"code": 0}'))
        self.assertEqual(self.probe.observations, [])

    def test_the_preceding_only_all_ids_is_noticed(self):
        self.probe.note_request(IDS_URL)
        deliver(self.probe, route_response())
        self.assertTrue(
            self.probe.observations[0].preceded_by_only_all_ids)

    def test_without_it_the_flag_is_false(self):
        """Отрицательный контроль: флаг различает два случая."""
        deliver(self.probe, route_response())
        self.assertFalse(
            self.probe.observations[0].preceded_by_only_all_ids)

    def test_a_binary_payload_is_decoded_and_counted(self):
        body = response([route_record(flight_id=FAKE_FLIGHT_ID)])
        deliver(self.probe, route_response(
            body=body, post_data=ids_body([FAKE_FLIGHT_ID])))
        seen = self.probe.observations[0]
        self.assertEqual(seen.payload_kind, 'BINARY')
        self.assertEqual(seen.decoded_routes, 1)
        self.assertEqual(seen.returned_id_count, 1)
        self.assertTrue(seen.comparison['requested_and_returned_match'])

    def test_a_count_mismatch_is_reported_without_the_identifiers(self):
        body = response([route_record(flight_id=FAKE_FLIGHT_ID)])
        deliver(self.probe, route_response(
            body=body, post_data=request_body(count=9)))
        seen = self.probe.observations[0]
        self.assertFalse(seen.comparison['requested_and_returned_match'])
        self.assertEqual(seen.request['flight_id_count'], 9)
        self.assertEqual(seen.returned_id_count, 1)
        self.assertNotIn(str(FAKE_FLIGHT_ID),
                         json.dumps(seen.as_dict(), ensure_ascii=False))

    def test_a_vendor_refusal_is_named_as_such(self):
        refusal = json.dumps({'status': 408, 'code': 408,
                              'msg': '请求时间无效',
                              'request_id': FAKE_REQUEST_ID}).encode('utf-8')
        deliver(self.probe, route_response(body=refusal))
        seen = self.probe.observations[0]
        self.assertEqual(seen.payload_kind, 'JSON_VENDOR_ERROR')
        self.assertIsNone(seen.decoded_routes)

    def test_identical_repeats_are_deduplicated(self):
        """Карта перезапрашивает маршруты при каждом изменении вида."""
        for _ in range(3):
            deliver(self.probe, route_response())
        self.assertEqual(len(self.probe.observations), 1)
        self.assertEqual(self.probe.observations[0].repeats, 3)
        self.assertEqual(self.probe.route_responses, 3)

    def test_the_number_of_observations_is_capped(self):
        for index in range(MAX_OBSERVATIONS + 5):
            body = response([route_record(flight_id=900000000 + index)])
            deliver(self.probe, route_response(body=body))
        self.assertEqual(len(self.probe.observations), MAX_OBSERVATIONS)
        self.assertEqual(self.probe.skipped_over_cap, 5)

    def test_a_broken_response_object_does_not_raise(self):
        """Слушатель внутри цикла Playwright не имеет права падать."""
        class Hostile(object):
            url = ROUTE_URL
            status = 200
            request = _FakeRequest(headers=HEADERS,
                                   post_data=ids_body([900000001]))

            def body(self):
                raise RuntimeError('gone')

        hostile = Hostile()
        hostile.request.response_object = hostile
        deliver(self.probe, hostile)
        self.assertEqual(self.probe.route_responses, 1)
        self.assertEqual(self.probe.pending_route_requests, 0)


class TestIdSetsNotCounts(ProbeTestCase):
    """Совпадение количеств -- не совпадение множеств.

    [REASON]: `requested_and_returned_match` означал равенство ДЛИН. «Девять
    запросили, девять вернулось» объявлялось совпадением, даже если это девять
    ЧУЖИХ маршрутов -- а именно множества и решают, чьи маршруты приехали.
    """

    def test_two_different_sets_of_the_same_size_do_not_match(self):
        body = response([route_record(flight_id=900000001),
                         route_record(flight_id=900000002)])
        deliver(self.probe, route_response(
            body=body, post_data=ids_body([900000003, 900000004])))
        seen = self.probe.observations[0]
        self.assertEqual(seen.request['flight_id_count'], 2)
        self.assertEqual(seen.returned_id_count, 2)
        self.assertFalse(seen.comparison['requested_and_returned_match'],
                         'два РАЗНЫХ набора одного размера сочтены совпавшими')
        self.assertEqual(seen.comparison['missing_count'], 2)
        self.assertEqual(seen.comparison['extra_count'], 2)

    def test_the_same_set_matches(self):
        """Отрицательный контроль: сверка не отвергает совпавшее."""
        body = response([route_record(flight_id=900000001),
                         route_record(flight_id=900000002)])
        deliver(self.probe, route_response(
            body=body, post_data=ids_body([900000002, 900000001])))
        seen = self.probe.observations[0]
        self.assertTrue(seen.comparison['requested_and_returned_match'])
        self.assertEqual(seen.comparison['missing_count'], 0)
        self.assertEqual(seen.comparison['extra_count'], 0)

    def test_a_partial_overlap_is_counted_on_both_sides(self):
        body = response([route_record(flight_id=900000001),
                         route_record(flight_id=900000009)])
        deliver(self.probe, route_response(
            body=body, post_data=ids_body([900000001, 900000002])))
        comparison = self.probe.observations[0].comparison
        self.assertFalse(comparison['requested_and_returned_match'])
        self.assertEqual(comparison['missing_count'], 1)
        self.assertEqual(comparison['extra_count'], 1)

    def test_no_identifier_reaches_the_report_or_the_log(self):
        body = response([route_record(flight_id=900000001),
                         route_record(flight_id=900000002)])
        deliver(self.probe, route_response(
            body=body, post_data=ids_body([900000003, 900000004])))
        write_report(self.probe, self.root)
        haystack = self.everything_written()
        for value in ('900000001', '900000002', '900000003', '900000004'):
            self.assertNotIn(value, haystack)

    def test_the_report_carries_only_booleans_and_counts(self):
        body = response([route_record(flight_id=900000001)])
        deliver(self.probe, route_response(
            body=body, post_data=ids_body([900000003])))
        document = self.probe.observations[0].as_dict()
        for key in ('requested_and_returned_match',
                    'invalid_requested_id_count', 'requested_duplicate_count',
                    'returned_duplicate_count', 'route_without_id_count',
                    'missing_count', 'extra_count'):
            self.assertIn(key, document)
        self.assertIsInstance(document['requested_and_returned_match'], bool)

    def test_a_duplicate_in_the_response_is_never_a_match(self):
        """Дубль в ответе -- не совпадение.

        [REASON]: прежний тест прямо УТВЕРЖДАЛ обратное. Один маршрут,
        приехавший дважды, означает, что второе неизвестно чем является, и
        подтверждением такая картина быть не может.
        """
        comparison = compare_id_sets({1, 2}, [1, 2, 2])
        self.assertFalse(comparison['requested_and_returned_match'])
        self.assertEqual(comparison['returned_duplicate_count'], 1)

    def test_a_clean_unique_pair_is_the_positive_control(self):
        comparison = compare_id_sets({1, 2}, [1, 2])
        self.assertTrue(comparison['requested_and_returned_match'])
        self.assertEqual(comparison['returned_duplicate_count'], 0)

    def test_an_empty_request_never_counts_as_a_match(self):
        """Ничего не просили -- значит и сверять нечего."""
        self.assertFalse(
            compare_id_sets(set(), [])['requested_and_returned_match'])

    def test_read_request_ids_keeps_the_values_out_of_the_summary(self):
        requested = read_request_ids(ids_body([900000001, 900000002]))
        self.assertEqual(requested.ids, {900000001, 900000002})
        self.assertEqual(requested.total, 2)
        self.assertTrue(requested.is_clean)
        summary = summarise_request_body(ids_body([900000001, 900000002]))
        self.assertNotIn('900000001', json.dumps(summary))


class TestCleanUniqueSetRequired(ProbeTestCase):
    """Подтверждение требует чистой картины, а не только равенства множеств.

    [REASON]: повторы и непривязанные маршруты не запрещали подтверждение.
    Прежний тест прямо считал совпадением `({1, 2}, [1, 2, 2])`.
    """

    ID_A = 900000001
    ID_B = 900000002

    def confirmed(self):
        return self.probe.observations[0].confirmed

    def comparison(self):
        return self.probe.observations[0].comparison

    def test_a_duplicate_in_the_request_is_not_confirmed(self):
        """request [ID, ID], response [ID]."""
        body = response([route_record(flight_id=self.ID_A)])
        deliver(self.probe, route_response(
            body=body, post_data=ids_body([self.ID_A, self.ID_A])))
        self.assertFalse(self.confirmed())
        self.assertEqual(self.comparison()['requested_duplicate_count'], 1)

    def test_a_duplicate_in_the_response_is_not_confirmed(self):
        """request [ID], response -- два маршрута с тем же ID."""
        body = response([route_record(flight_id=self.ID_A),
                         route_record(flight_id=self.ID_A)])
        deliver(self.probe, route_response(
            body=body, post_data=ids_body([self.ID_A])))
        self.assertFalse(self.confirmed())
        self.assertEqual(self.comparison()['returned_duplicate_count'], 1)

    def test_a_route_without_an_id_is_not_confirmed(self):
        """request [ID], response -- один правильный и один без ID."""
        body = response([route_record(flight_id=self.ID_A),
                         route_record_without_id()])
        deliver(self.probe, route_response(
            body=body, post_data=ids_body([self.ID_A])))
        self.assertFalse(self.confirmed())
        self.assertEqual(self.comparison()['route_without_id_count'], 1)

    def test_an_invalid_value_beside_a_good_id_is_not_confirmed(self):
        """float, bool, строка и null рядом с правильным ID."""
        for junk in (1.5, True, '900000001', None):
            with self.subTest(repr(junk)):
                probe = RouteUiProbe(logger=_QuietLog(),
                                     expected_origin=ROUTE_ORIGIN)
                body = response([route_record(flight_id=self.ID_A)])
                deliver(probe, route_response(
                    body=body,
                    post_data=json.dumps(
                        {'flight_record_ids': [self.ID_A, junk],
                         'data_type': 'simplified'})))
                seen = probe.observations[0]
                self.assertFalse(seen.confirmed, repr(junk))
                self.assertEqual(seen.comparison['invalid_requested_id_count'],
                                 1, repr(junk))

    def test_two_different_sets_of_the_same_size_are_not_confirmed(self):
        body = response([route_record(flight_id=self.ID_A),
                         route_record(flight_id=self.ID_B)])
        deliver(self.probe, route_response(
            body=body, post_data=ids_body([900000003, 900000004])))
        self.assertFalse(self.confirmed())

    def test_a_clean_unique_set_is_the_positive_control(self):
        """Отрицательный контроль: строгость не гасит нормальный случай."""
        body = response([route_record(flight_id=self.ID_A),
                         route_record(flight_id=self.ID_B)])
        deliver(self.probe, route_response(
            body=body, post_data=ids_body([self.ID_B, self.ID_A])))
        seen = self.probe.observations[0]
        self.assertTrue(seen.confirmed, seen.not_confirmed_because)
        for key in ('invalid_requested_id_count', 'requested_duplicate_count',
                    'returned_duplicate_count', 'route_without_id_count',
                    'missing_count', 'extra_count'):
            self.assertEqual(seen.comparison[key], 0, key)

    def test_none_of_these_cases_prints_an_identifier(self):
        body = response([route_record(flight_id=self.ID_A),
                         route_record(flight_id=self.ID_A)])
        deliver(self.probe, route_response(
            body=body, post_data=ids_body([self.ID_A, self.ID_A])))
        write_report(self.probe, self.root)
        haystack = self.everything_written()
        self.assertNotIn(str(self.ID_A), haystack)


class TestExitCodeDecision(unittest.TestCase):
    """Решение о коде выхода -- чистая функция, и её видно."""

    def test_only_confirmed_is_success(self):
        self.assertEqual(probe_exit_code(observations=2, confirmed=2,
                                         skipped_over_cap=0), 0)

    def test_only_unconfirmed_is_thirteen(self):
        self.assertEqual(probe_exit_code(observations=2, confirmed=0,
                                         skipped_over_cap=0), 13)

    def test_a_mixed_result_is_thirteen(self):
        """[REASON]: смешанный результат давал ложный код 0.

        Один подтверждённый POST рядом с неподтверждённым ответом означает,
        что кабинет отвечал по-разному, и объявлять такой прогон успешным --
        это ровно тот класс ложного успеха, который здесь и разбирают.
        """
        self.assertEqual(probe_exit_code(observations=2, confirmed=1,
                                         skipped_over_cap=0), 13)

    def test_no_observations_is_six(self):
        self.assertEqual(probe_exit_code(observations=0, confirmed=0,
                                         skipped_over_cap=0), 6)

    def test_a_skipped_observation_is_thirteen_even_if_all_seen_are_confirmed(self):
        self.assertEqual(probe_exit_code(observations=2, confirmed=2,
                                         skipped_over_cap=1), 13)


class TestDeduplication(ProbeTestCase):

    def test_the_same_body_for_different_requests_is_not_merged(self):
        """Одинаковое тело при РАЗНЫХ наборах ID -- два наблюдения.

        [REASON]: ключ дедупликации был «путь + хеш тела». Два разных запроса,
        случайно получившие одинаковый ответ, схлопывались в одно наблюдение,
        и вопрос «на что именно ответил кабинет» терял ответ.
        """
        body = response([route_record(flight_id=FAKE_FLIGHT_ID)])
        deliver(self.probe, route_response(
            body=body, post_data=ids_body([900000001])))
        deliver(self.probe, route_response(
            body=body, post_data=ids_body([900000002])))
        self.assertEqual(len(self.probe.observations), 2)

    def test_the_same_body_for_the_same_request_is_merged(self):
        """Отрицательный контроль: настоящий повтор по-прежнему один."""
        body = response([route_record(flight_id=FAKE_FLIGHT_ID)])
        for _ in range(3):
            deliver(self.probe, route_response(
                body=body, post_data=ids_body([900000001])))
        self.assertEqual(len(self.probe.observations), 1)
        self.assertEqual(self.probe.observations[0].repeats, 3)


class TestOnlyAllIdsCountedOncePerExchange(ProbeTestCase):

    def test_a_request_and_its_response_count_once(self):
        """[REASON]: счётчик рос и на запросе, и на ответе -- один обмен
        считался дважды, и «два only_all_ids перед маршрутом» означало один.
        """
        self.probe.note_request(IDS_URL)
        deliver(self.probe, _FakeResponse(IDS_URL, b'{"code": 0}'))
        self.assertEqual(self.probe.saw_only_all_ids, 1)

    def test_two_exchanges_count_twice(self):
        """Отрицательный контроль: счётчик всё-таки считает."""
        for _ in range(2):
            self.probe.note_request(IDS_URL)
            deliver(self.probe, _FakeResponse(IDS_URL, b'{"code": 0}'))
        self.assertEqual(self.probe.saw_only_all_ids, 2)

    def test_the_neighbourhood_flag_still_works(self):
        self.probe.note_request(IDS_URL)
        deliver(self.probe, _FakeResponse(IDS_URL, b'{"code": 0}'))
        deliver(self.probe, route_response())
        self.assertTrue(
            self.probe.observations[0].preceded_by_only_all_ids)


class TestConfirmation(ProbeTestCase):
    """Код 0 разрешён только при полном успехе."""

    def confirmed_response(self):
        body = response([route_record(flight_id=FAKE_FLIGHT_ID)])
        return route_response(body=body,
                              post_data=ids_body([FAKE_FLIGHT_ID]))

    def test_a_full_success_is_confirmed(self):
        deliver(self.probe, self.confirmed_response())
        seen = self.probe.observations[0]
        self.assertTrue(seen.confirmed, seen.not_confirmed_because)
        self.assertEqual(seen.not_confirmed_because, [])
        self.assertEqual(len(self.probe.confirmed_observations), 1)

    def test_a_vendor_refusal_is_not_confirmed(self):
        refusal = json.dumps({'status': 408, 'code': 408}).encode('utf-8')
        deliver(self.probe, route_response(body=refusal))
        seen = self.probe.observations[0]
        self.assertFalse(seen.confirmed)
        self.assertIn('the payload is not a binary route payload',
                      seen.not_confirmed_because)

    def test_html_is_not_confirmed(self):
        deliver(self.probe, route_response(b'<html>502</html>'))
        self.assertFalse(self.probe.observations[0].confirmed)

    def test_an_undecodable_binary_is_not_confirmed(self):
        deliver(self.probe, route_response(b'\xff\xff\xff\xff'))
        seen = self.probe.observations[0]
        self.assertFalse(seen.confirmed)
        self.assertIn('the payload did not decode',
                      seen.not_confirmed_because)

    def test_a_non_2xx_status_is_not_confirmed(self):
        body = response([route_record(flight_id=FAKE_FLIGHT_ID)])
        deliver(self.probe, route_response(
            body=body, post_data=ids_body([FAKE_FLIGHT_ID]), status=500))
        seen = self.probe.observations[0]
        self.assertFalse(seen.confirmed)
        self.assertIn('the HTTP status is not 2xx',
                      seen.not_confirmed_because)

    def test_a_get_is_not_confirmed(self):
        body = response([route_record(flight_id=FAKE_FLIGHT_ID)])
        answer = route_response(body=body,
                                post_data=ids_body([FAKE_FLIGHT_ID]))
        answer.request.method = 'GET'
        deliver(self.probe, answer)
        seen = self.probe.observations[0]
        self.assertFalse(seen.confirmed)
        self.assertIn('the method is not POST', seen.not_confirmed_because)

    def test_another_host_is_not_confirmed(self):
        body = response([route_record(flight_id=FAKE_FLIGHT_ID)])
        answer = _FakeResponse(
            'https://elsewhere.invalid/api/web/v2/flight_datas/flight_records',
            body, request=_FakeRequest(
                headers=HEADERS, post_data=ids_body([FAKE_FLIGHT_ID])))
        deliver(self.probe, answer)
        seen = self.probe.observations[0]
        self.assertFalse(seen.confirmed)
        self.assertIn('the host is not the expected route API host',
                      seen.not_confirmed_because)

    def test_a_plain_http_origin_is_not_confirmed(self):
        probe = RouteUiProbe(logger=self.log,
                             expected_origin='http://kr-ag2-api.example.invalid')
        deliver(probe, self.confirmed_response())
        seen = probe.observations[0]
        self.assertFalse(seen.confirmed)
        self.assertIn('the expected origin is not https',
                      seen.not_confirmed_because)

    def test_a_different_path_on_the_right_host_is_not_confirmed(self):
        body = response([route_record(flight_id=FAKE_FLIGHT_ID)])
        answer = _FakeResponse(
            ROUTE_ORIGIN + '/api/web/v9/flight_datas/flight_records',
            body, request=_FakeRequest(
                headers=HEADERS, post_data=ids_body([FAKE_FLIGHT_ID])))
        deliver(self.probe, answer)
        seen = self.probe.observations[0]
        self.assertFalse(seen.confirmed)
        self.assertIn('the path is not the exact route endpoint',
                      seen.not_confirmed_because)

    def test_mismatched_id_sets_are_not_confirmed(self):
        body = response([route_record(flight_id=900000001)])
        deliver(self.probe, route_response(
            body=body, post_data=ids_body([900000002])))
        seen = self.probe.observations[0]
        self.assertFalse(seen.confirmed)
        self.assertIn('the requested and returned id sets do not match',
                      seen.not_confirmed_because)

    def test_the_report_counts_confirmed_observations(self):
        deliver(self.probe, self.confirmed_response())
        deliver(self.probe, route_response(b'<html>502</html>'))
        document = self.probe.report()
        self.assertEqual(document['route_observations'], 2)
        self.assertEqual(document['confirmed_route_posts'], 1)


class TestOversizedResponse(ProbeTestCase):
    """Слишком большой ответ: настоящий размер, никакого хеша пустоты."""

    def oversize(self):
        return b'\x08' * (MAX_PROCESSED_RESPONSE_BYTES + 16)

    def test_the_declared_size_refuses_before_the_body_is_requested(self):
        """Если ответ назвал Content-Length больше предела -- тело не берём.

        [REASON]: это единственный случай, когда наблюдатель может отказаться
        ДО чтения. Всё остальное Playwright отдаёт только целиком, и предел
        честно назван пределом обработки, а не чтения.
        """
        asked = []

        class _Declaring(object):
            url = ROUTE_URL
            status = 200
            request = _FakeRequest(headers=HEADERS,
                                   post_data=ids_body([900000001]))

            def all_headers(self):
                return {'Content-Length':
                        str(MAX_PROCESSED_RESPONSE_BYTES + 1)}

            def body(self):
                asked.append(True)
                raise AssertionError('the body was requested anyway')

        deliver(self.probe, _Declaring())
        seen = self.probe.observations[0]
        self.assertEqual(asked, [])
        self.assertEqual(seen.payload_kind, 'TOO_LARGE')
        self.assertIsNone(seen.response_sha256)
        self.assertFalse(seen.confirmed)

        # Заявленное -- в своё поле; фактического НЕТ, и поле пустое.
        self.assertEqual(seen.declared_response_bytes,
                         MAX_PROCESSED_RESPONSE_BYTES + 1)
        self.assertIsNone(seen.response_bytes)
        self.assertFalse(seen.body_was_read)

        document = seen.as_dict()
        self.assertIsNone(document['response_bytes'])
        self.assertEqual(document['declared_response_bytes'],
                         MAX_PROCESSED_RESPONSE_BYTES + 1)
        self.assertFalse(document['response_body_was_read'])
        # Деталь говорит о ЗАЯВЛЕННОМ размере и о том, что настоящий неизвестен.
        self.assertIn('declared', seen.payload_detail)
        self.assertIn('unknown', seen.payload_detail)

    def test_the_declared_size_never_becomes_the_measured_size(self):
        """Отличающая проверка вместо прежней, закреплявшей дефект.

        [REASON]: прежний тест требовал `response_bytes == Content-Length`, то
        есть утверждал ровно то, чего код знать не мог: тела в руках не было.
        `Content-Length` ставит отправитель.
        """
        declared = MAX_PROCESSED_RESPONSE_BYTES + 7

        class _Declaring(object):
            url = ROUTE_URL
            status = 200
            request = _FakeRequest(headers=HEADERS,
                                   post_data=ids_body([900000001]))

            def all_headers(self):
                return {'Content-Length': str(declared)}

            def body(self):
                raise AssertionError('the body was requested anyway')

        deliver(self.probe, _Declaring())
        seen = self.probe.observations[0]
        self.assertNotEqual(seen.response_bytes, declared)
        self.assertIsNone(seen.response_bytes)
        self.assertEqual(seen.declared_response_bytes, declared)

    def test_a_read_body_still_records_its_measured_size(self):
        """Положительный контроль: прочитанное тело меряется как раньше."""
        body = response([route_record(flight_id=900000001)])
        deliver(self.probe, route_response(
            body=body, post_data=ids_body([900000001])))
        seen = self.probe.observations[0]
        self.assertEqual(seen.response_bytes, len(body))
        self.assertTrue(seen.body_was_read)
        self.assertIsNone(seen.declared_response_bytes)

    def test_the_log_line_survives_a_missing_size(self):
        """`%d` на None уронил бы слушателя ровно там, где он обязан устоять."""
        from drone_collector.route_ui_probe import _safe_line

        class _Declaring(object):
            url = ROUTE_URL
            status = 200
            request = _FakeRequest(headers=HEADERS,
                                   post_data=ids_body([900000001]))

            def all_headers(self):
                return {'Content-Length':
                        str(MAX_PROCESSED_RESPONSE_BYTES + 1)}

            def body(self):
                raise AssertionError('the body was requested anyway')

        deliver(self.probe, _Declaring())
        self.assertEqual(self.probe.observation_errors, 0)
        line = _safe_line(self.probe.observations[0])
        self.assertIn('bytes=None', line)
        self.assertIn('body_read=False', line)

    def test_an_ordinary_declared_size_does_not_block_the_body(self):
        """Отрицательный контроль: обычный Content-Length ничего не ломает."""
        body = response([route_record(flight_id=900000001)])

        class _Declaring(object):
            url = ROUTE_URL
            status = 200
            request = _FakeRequest(headers=HEADERS,
                                   post_data=ids_body([900000001]))

            def all_headers(self):
                return {'Content-Length': str(len(body))}

            def body(self):
                return body

        deliver(self.probe, _Declaring())
        seen = self.probe.observations[0]
        self.assertEqual(seen.payload_kind, 'BINARY')
        self.assertEqual(seen.response_bytes, len(body))

    def test_a_nonsense_content_length_is_ignored(self):
        """Заголовок ставит отправитель; мусору в нём верить нечего."""
        body = response([route_record(flight_id=900000001)])

        class _Declaring(object):
            url = ROUTE_URL
            status = 200
            request = _FakeRequest(headers=HEADERS,
                                   post_data=ids_body([900000001]))

            def all_headers(self):
                return {'Content-Length': 'not-a-number'}

            def body(self):
                return body

        deliver(self.probe, _Declaring())
        self.assertEqual(self.probe.observations[0].response_bytes, len(body))

    def test_the_limit_is_named_a_processing_limit(self):
        """Имя предела и его описание не обещают того, чего код не делает."""
        import inspect

        import drone_collector.route_ui_probe as module
        source = inspect.getsource(module)
        self.assertIn('MAX_PROCESSED_RESPONSE_BYTES', source)
        self.assertNotIn('the body is not read', source)
        self.assertNotIn('the body was not read', source)

    def test_the_real_size_is_recorded(self):
        """[REASON]: тело подменялось пустым, и отчёт писал `response_bytes=0`
        -- то есть утверждал, что ответ был пуст, хотя он был огромен.
        """
        raw = self.oversize()
        deliver(self.probe, route_response(body=raw))
        seen = self.probe.observations[0]
        self.assertEqual(seen.response_bytes, len(raw))
        self.assertGreater(seen.response_bytes, MAX_RESPONSE_BYTES)

    def test_no_hash_of_an_empty_body_is_recorded(self):
        import hashlib
        empty_digest = hashlib.sha256(b'').hexdigest()
        deliver(self.probe, route_response(body=self.oversize()))
        seen = self.probe.observations[0]
        self.assertIsNone(seen.response_sha256)
        self.assertNotEqual(seen.response_sha256, empty_digest)

    def test_it_is_named_too_large_and_not_decoded(self):
        deliver(self.probe, route_response(body=self.oversize()))
        seen = self.probe.observations[0]
        self.assertEqual(seen.payload_kind, 'TOO_LARGE')
        self.assertIsNone(seen.decoded_routes)
        self.assertFalse(seen.confirmed)

    def test_an_ordinary_response_still_gets_its_hash(self):
        """Отрицательный контроль: обычный ответ хешируется как раньше."""
        deliver(self.probe, route_response())
        seen = self.probe.observations[0]
        self.assertEqual(len(seen.response_sha256), 64)


class TestDedupeKeepsDifferentAnswersApart(ProbeTestCase):
    """Повтор -- только семантически ОДИНАКОВЫЙ обмен.

    [REASON]: ключ дедупликации не знал ни метода, ни HTTP-статуса.
    Подтверждённый `200 POST`, а следом тот же запрос с тем же телом, но `500`
    или `GET`, схлопывались в повтор первого. Прогон, в котором кабинет
    ответил по-разному, выходил полностью подтверждённым с кодом 0.
    """

    def outcome(self):
        return probe_exit_code(
            observations=len(self.probe.observations),
            confirmed=len(self.probe.confirmed_observations),
            skipped_over_cap=self.probe.skipped_over_cap,
            observation_errors=self.probe.observation_errors)

    def test_the_same_body_with_http_500_is_not_a_repeat(self):
        deliver(self.probe, confirmable())
        deliver(self.probe, confirmable(status=500))
        self.assertEqual(len(self.probe.observations), 2)
        self.assertEqual(len(self.probe.confirmed_observations), 1)
        self.assertEqual(self.outcome(), 13)

    def test_the_same_body_as_a_get_is_not_a_repeat(self):
        deliver(self.probe, confirmable())
        deliver(self.probe, confirmable(method='GET'))
        self.assertEqual(len(self.probe.observations), 2)
        self.assertEqual(len(self.probe.confirmed_observations), 1)
        self.assertEqual(self.outcome(), 13)

    def test_two_identical_confirmed_exchanges_still_collapse(self):
        """Положительный контроль: настоящий повтор остаётся повтором."""
        for _ in range(3):
            deliver(self.probe, confirmable())
        self.assertEqual(len(self.probe.observations), 1)
        self.assertEqual(self.probe.observations[0].repeats, 3)
        self.assertTrue(self.probe.observations[0].confirmed)
        self.assertEqual(self.outcome(), 0)

    def test_a_different_internal_status_is_not_a_repeat(self):
        """Тот же HTTP-обмен, другой конверт DJI -- другое наблюдение."""
        deliver(self.probe, confirmable())
        deliver(self.probe, confirmable(envelope_status=101))
        self.assertEqual(len(self.probe.observations), 2)
        self.assertEqual(self.outcome(), 13)

    def test_a_different_payload_kind_is_not_a_repeat(self):
        """Двоичный ответ и JSON-отказ на тот же запрос -- два наблюдения."""
        deliver(self.probe, confirmable())
        deliver(self.probe, route_response(
            body=b'{"status": 408, "code": 408, "msg": "no"}',
            post_data=ids_body([900000001])))
        self.assertEqual(len(self.probe.observations), 2)
        self.assertEqual(self.outcome(), 13)


class TestObservationIdCoversEveryConfirmationInput(unittest.TestCase):
    """Ключ различает КАЖДЫЙ признак, от которого зависит подтверждение.

    [REASON]: проверка на уровне самого ключа, а не через наблюдение. Через
    наблюдение различимы только те признаки, которые меняют ещё и тело ответа
    (внутренний статус DJI лежит в теле, и его хеш меняется вместе с ним) --
    а метод, HTTP-статус и вердикт сверки тела не меняют вовсе, и прежний ключ
    их терял молча.
    """

    BASE = dict(host='kr-ag2-api.example.invalid',
                path='/api/web/v2/flight_datas/flight_records',
                method='POST', http_status=200,
                request_fingerprint='abc123', response_sha256='d' * 64,
                payload_kind='BINARY', dji_response_status=200,
                ids_match=True, body_was_read=True)

    def key(self, **changes):
        fields = dict(self.BASE)
        fields.update(changes)
        return observation_id(**fields)

    def test_the_same_exchange_gives_the_same_key(self):
        """Положительный контроль: ключ устойчив."""
        self.assertEqual(self.key(), self.key())

    def test_the_http_status_changes_the_key(self):
        self.assertNotEqual(self.key(), self.key(http_status=500))

    def test_the_method_changes_the_key(self):
        self.assertNotEqual(self.key(), self.key(method='GET'))

    def test_the_host_changes_the_key(self):
        self.assertNotEqual(self.key(), self.key(host='elsewhere.invalid'))

    def test_the_path_changes_the_key(self):
        self.assertNotEqual(self.key(), self.key(path='/api/web/v2/other'))

    def test_the_request_fingerprint_changes_the_key(self):
        self.assertNotEqual(self.key(),
                            self.key(request_fingerprint='zzz999'))

    def test_the_response_hash_changes_the_key(self):
        self.assertNotEqual(self.key(), self.key(response_sha256='e' * 64))

    def test_the_payload_kind_changes_the_key(self):
        self.assertNotEqual(self.key(), self.key(payload_kind='TOO_LARGE'))

    def test_the_internal_dji_status_changes_the_key(self):
        self.assertNotEqual(self.key(), self.key(dji_response_status=101))

    def test_the_id_comparison_result_changes_the_key(self):
        self.assertNotEqual(self.key(), self.key(ids_match=False))

    def test_an_unread_body_never_shares_a_key_with_a_read_one(self):
        """Непрочитанное тело хеша не имеет; двух таких не путать между собой.

        Иначе два разных ответа, тела которых не запрашивались, слились бы в
        одно наблюдение по одному только пути.
        """
        unread = self.key(body_was_read=False, response_sha256=None)
        self.assertNotEqual(unread, self.key())
        self.assertIn(NOT_READ, unread)
        other = self.key(body_was_read=False, response_sha256=None,
                         http_status=500)
        self.assertNotEqual(unread, other)

class TestOnlyAllIdsNeighbourhoodIsConsumed(ProbeTestCase):
    """Соседство с `only_all_ids` тратится КАЖДЫМ ответом маршрутов.

    [REASON]: флаг сбрасывался только на пути нового наблюдения. Ответ,
    выпавший по лимиту или схлопнувшийся в повтор, оставлял его поднятым, и
    следующее наблюдение получало чужое соседство -- отчёт утверждал, что
    перед ним шёл запрос идентификаторов, которого перед ним не было.
    """

    def test_the_flag_is_set_on_the_response_that_followed_the_ids(self):
        self.probe.note_request(IDS_URL)
        deliver(self.probe, confirmable(flight_id=900000001))
        self.assertTrue(self.probe.observations[0].preceded_by_only_all_ids)

    def test_the_next_response_does_not_inherit_it(self):
        self.probe.note_request(IDS_URL)
        deliver(self.probe, confirmable(flight_id=900000001))
        deliver(self.probe, confirmable(flight_id=900000002))
        self.assertTrue(self.probe.observations[0].preceded_by_only_all_ids)
        self.assertFalse(self.probe.observations[1].preceded_by_only_all_ids)

    def test_a_repeat_consumes_the_flag_too(self):
        """Повтор -- тоже ответ маршрутов, и соседство тратит он."""
        first = confirmable(flight_id=900000001)
        deliver(self.probe, first)
        self.assertFalse(self.probe.observations[0].preceded_by_only_all_ids)

        self.probe.note_request(IDS_URL)
        deliver(self.probe, confirmable(flight_id=900000001))
        self.assertEqual(len(self.probe.observations), 1)
        self.assertEqual(self.probe.observations[0].repeats, 2)

        deliver(self.probe, confirmable(flight_id=900000002))
        self.assertEqual(len(self.probe.observations), 2)
        self.assertFalse(self.probe.observations[1].preceded_by_only_all_ids)

    def test_an_observation_dropped_by_the_cap_consumes_it_too(self):
        for index in range(MAX_OBSERVATIONS):
            deliver(self.probe, confirmable(flight_id=900000001 + index))
        self.assertEqual(len(self.probe.observations), MAX_OBSERVATIONS)

        self.probe.note_request(IDS_URL)
        deliver(self.probe, confirmable(flight_id=900000900))
        self.assertEqual(self.probe.skipped_over_cap, 1)
        self.assertFalse(self.probe._ids_seen_recently)


class TestTheDjiEnvelopeStatusIsChecked(ProbeTestCase):
    """HTTP 200 в этом API не значит «успех».

    [REASON]: `decode_route_response` не поднимает не-OK статус намеренно --
    он ОТДАЁТ его вызывающему, и его докстринг прямо этого требует.
    `_decode_ids` состояние выбрасывал, и конверт `status=101` «подписи нет»,
    приехавший с маршрутами внутри и совпавшими идентификаторами, объявлялся
    подтверждением.
    """

    def outcome(self):
        return probe_exit_code(
            observations=len(self.probe.observations),
            confirmed=len(self.probe.confirmed_observations),
            skipped_over_cap=self.probe.skipped_over_cap,
            observation_errors=self.probe.observation_errors)

    def test_an_internal_101_with_routes_is_not_confirmed(self):
        deliver(self.probe, confirmable(envelope_status=101))
        seen = self.probe.observations[0]
        # Всё внешнее в порядке: 200, двоичное тело, маршруты, совпавшие ID.
        self.assertEqual(seen.http_status, 200)
        self.assertEqual(seen.payload_kind, 'BINARY')
        self.assertEqual(seen.decoded_routes, 1)
        self.assertTrue(seen.comparison['requested_and_returned_match'])
        # И всё-таки не подтверждено -- из-за внутреннего статуса.
        self.assertEqual(seen.dji_response_status, 101)
        self.assertFalse(seen.confirmed)
        self.assertIn('the DJI envelope status is not the success status',
                      seen.not_confirmed_because)
        self.assertEqual(self.outcome(), 13)

    def test_an_internal_200_is_the_positive_control(self):
        deliver(self.probe, confirmable(envelope_status=200))
        seen = self.probe.observations[0]
        self.assertEqual(seen.dji_response_status, 200)
        self.assertTrue(seen.confirmed)
        self.assertEqual(seen.not_confirmed_because, [])
        self.assertEqual(self.outcome(), 0)

    def test_an_unknown_internal_status_is_not_interpreted(self):
        """Незнакомый статус не толкуется: он просто не тот, что нужен."""
        deliver(self.probe, confirmable(envelope_status=4242))
        seen = self.probe.observations[0]
        self.assertEqual(seen.dji_response_status, 4242)
        self.assertFalse(seen.confirmed)

    def test_the_status_reaches_the_report_as_a_number(self):
        deliver(self.probe, confirmable(envelope_status=101))
        document = self.probe.observations[0].as_dict()
        self.assertEqual(document['dji_response_status'], 101)

    def test_the_ok_constant_matches_the_decoder(self):
        """Две константы не могут разойтись молча."""
        from drone_collector.route_decode import STATUS_OK
        self.assertEqual(DJI_ENVELOPE_STATUS_OK, STATUS_OK)

    def test_a_body_that_did_not_decode_is_not_blamed_on_the_envelope(self):
        """Отрицательный контроль: про конверт, которого не было, не врём."""
        deliver(self.probe, route_response(
            body=b'{"status": 408, "code": 408, "msg": "no"}',
            post_data=ids_body([900000001])))
        seen = self.probe.observations[0]
        self.assertIsNone(seen.dji_response_status)
        self.assertNotIn('the DJI envelope status is not the success status',
                         seen.not_confirmed_because)


class TestListenerErrorsAreCounted(ProbeTestCase):
    """Ответ, на котором слушатель споткнулся, -- не «ничего».

    [REASON]: `note_response` ловит любое исключение, чтобы не уронить цикл
    Playwright, и на этом всё заканчивалось: предупреждение уходило в лог, а
    решение о результате об ошибке не знало.
    """

    def broken(self):
        class _Broken(object):
            url = ROUTE_URL
            request = _FakeRequest(headers=HEADERS,
                                   post_data=ids_body([900000001]))

            def all_headers(self):
                return {}

            def body(self):
                return response([route_record(flight_id=900000001)])

            @property
            def status(self):
                raise RuntimeError('SYNTHETIC-LISTENER-FAILURE')

        return _Broken()

    def outcome(self):
        return probe_exit_code(
            observations=len(self.probe.observations),
            confirmed=len(self.probe.confirmed_observations),
            skipped_over_cap=self.probe.skipped_over_cap,
            observation_errors=self.probe.observation_errors)

    def test_the_error_is_counted_and_does_not_escape(self):
        deliver(self.probe, self.broken())
        self.assertEqual(self.probe.observation_errors, 1)
        self.assertEqual(self.probe.observations, [])

    def test_one_error_beside_a_confirmed_exchange_is_still_thirteen(self):
        deliver(self.probe, self.broken())
        deliver(self.probe, confirmable())
        self.assertEqual(len(self.probe.confirmed_observations), 1)
        self.assertEqual(self.probe.observation_errors, 1)
        self.assertEqual(self.outcome(), 13)

    def test_an_ordinary_confirmed_exchange_is_the_positive_control(self):
        deliver(self.probe, confirmable())
        self.assertEqual(self.probe.observation_errors, 0)
        self.assertEqual(self.outcome(), 0)

    def test_the_count_reaches_the_report(self):
        deliver(self.probe, self.broken())
        self.assertEqual(self.probe.report()['observation_errors'], 1)

    def test_only_the_exception_type_reaches_the_log(self):
        deliver(self.probe, self.broken())
        text = self.log.text()
        self.assertIn('RuntimeError', text)
        self.assertNotIn('SYNTHETIC-LISTENER-FAILURE', text)

    def test_the_pure_function_refuses_zero_on_an_error(self):
        self.assertEqual(probe_exit_code(2, 2, 0, observation_errors=1), 13)
        self.assertEqual(probe_exit_code(2, 2, 0, observation_errors=0), 0)

    def test_an_error_with_nothing_observed_is_not_called_empty(self):
        """Пришло что-то, прочитать не смогли -- это не «ничего не было»."""
        self.assertEqual(probe_exit_code(0, 0, 0, observation_errors=1), 13)
        self.assertEqual(probe_exit_code(0, 0, 0, observation_errors=0), 6)


class TestDataTypeNeverLeaks(ProbeTestCase):
    """`data_type` -- единственное поле тела, уходящее наружу значением.

    [REASON]: оно приходит из браузера, попадает в журнал сразу и в отчёт --
    до того, как отчёт проверят на секреты. Значение с маркером удостоверения
    или формой подписанной ссылки прошло бы в лог, откуда его уже не убрать.
    """

    LEAKY_BEARER = 'Bearer NOT-REAL-SECRET-0123456789'
    LEAKY_SIGNED = 'https://example.invalid/x?signature=NOT-REAL-0123456789'

    def observe(self, data_type):
        deliver(self.probe, route_response(
            post_data=ids_body([900000001], data_type=data_type)))
        write_report(self.probe, self.root)
        return self.probe.observations[0]

    def test_a_bearer_data_type_is_withheld(self):
        seen = self.observe(self.LEAKY_BEARER)
        self.assertEqual(seen.request['data_type'], WITHHELD_DATA_TYPE)
        self.assertTrue(seen.request['data_type_withheld'])
        self.assertNotIn(self.LEAKY_BEARER, self.everything_written())
        self.assertNotIn('NOT-REAL-SECRET', self.everything_written())

    def test_a_signed_url_data_type_is_withheld(self):
        seen = self.observe(self.LEAKY_SIGNED)
        self.assertEqual(seen.request['data_type'], WITHHELD_DATA_TYPE)
        self.assertTrue(seen.request['data_type_withheld'])
        self.assertNotIn(self.LEAKY_SIGNED, self.everything_written())
        self.assertNotIn('signature=', self.everything_written())

    def test_the_withheld_marker_names_no_marker(self):
        """Метка не должна ронять проверку отчёта о самой себе."""
        from drone_collector.outbox import find_secret_markers
        self.assertEqual(find_secret_markers(WITHHELD_DATA_TYPE), [])

    def test_an_ordinary_data_type_survives(self):
        """Положительный контроль: `simplified` доходит как есть."""
        seen = self.observe('simplified')
        self.assertEqual(seen.request['data_type'], 'simplified')
        self.assertFalse(seen.request['data_type_withheld'])
        self.assertIn('simplified', self.everything_written())

    def test_an_unknown_but_safe_data_type_survives_uninterpreted(self):
        """Незнакомое безобидное значение -- наблюдение, а не повод судить."""
        seen = self.observe('whatever-dji-asks-for-next')
        self.assertEqual(seen.request['data_type'],
                         'whatever-dji-asks-for-next')
        self.assertFalse(seen.request['data_type_withheld'])

    def test_a_leaky_value_never_reaches_the_log_line(self):
        from drone_collector.route_ui_probe import _safe_line
        seen = self.observe(self.LEAKY_BEARER)
        line = _safe_line(seen)
        self.assertNotIn('NOT-REAL-SECRET', line)
        self.assertIn('data_type_withheld=True', line)

    def test_the_check_looks_at_the_whole_value_before_truncating(self):
        """Маркер за 64-м символом не должен уезжать из проверки."""
        from drone_collector.route_ui_probe import safe_data_type
        value = 'x' * 80 + 'Bearer NOT-REAL'
        self.assertEqual(safe_data_type(value), (WITHHELD_DATA_TYPE, True))

    def test_a_control_character_is_still_dropped_whole(self):
        from drone_collector.route_ui_probe import safe_data_type
        self.assertEqual(safe_data_type('simpli\x00fied'), (None, True))


class _TargetClosedError(Exception):
    """Синтетический двойник playwright TargetClosedError."""


def unreadable_response(post_data=None, status=200, exc=None,
                        headers=None):
    """Ответ маршрутов, тело которого прочитать не удастся."""
    failure = exc or _TargetClosedError('SYNTHETIC-BROWSER-DETAIL')

    class _Unreadable(object):
        url = ROUTE_URL

        def __init__(self):
            self.status = status
            self.request = _FakeRequest(
                headers=headers or HEADERS,
                post_data=(post_data if post_data is not None
                           else ids_body([900000001])))

        def all_headers(self):
            return {}

        def body(self):
            raise failure

    return _Unreadable()


class TestUnreadableIsNotEmpty(ProbeTestCase):
    """Ответ, тела которого не прочитали, -- не пустой ответ.

    [REASON]: живой прогон 2026-08-27 получил `TargetClosedError` на всех пяти
    `response.body()`, а код подставлял `b''`. Отчёт написал
    `payload_kind=EMPTY`, `response_bytes=0`, sha256 ПУСТОГО тела,
    `response_body_was_read=true`, `observation_errors=0` и «запрошенные ID
    отсутствуют». Ни одно из этих утверждений не было правдой.
    """

    def outcome(self):
        return probe_exit_code(
            observations=len(self.probe.observations),
            confirmed=len(self.probe.confirmed_observations),
            skipped_over_cap=self.probe.skipped_over_cap,
            observation_errors=self.probe.observation_errors)

    def test_the_kind_is_unreadable_and_not_empty(self):
        deliver(self.probe, unreadable_response())
        seen = self.probe.observations[0]
        self.assertEqual(seen.payload_kind, PAYLOAD_KIND_UNREADABLE)
        self.assertNotEqual(seen.payload_kind, 'EMPTY')

    def test_no_size_and_no_hash_are_invented(self):
        deliver(self.probe, unreadable_response())
        seen = self.probe.observations[0]
        self.assertFalse(seen.body_was_read)
        self.assertIsNone(seen.response_bytes)
        self.assertIsNone(seen.response_sha256)

    def test_the_hash_is_not_the_hash_of_emptiness(self):
        """Отдельно: sha256(b'') -- самое убедительное из ложных чисел."""
        import hashlib
        deliver(self.probe, unreadable_response())
        self.assertNotEqual(self.probe.observations[0].response_sha256,
                            hashlib.sha256(b'').hexdigest())

    def test_nothing_is_claimed_about_the_routes(self):
        deliver(self.probe, unreadable_response())
        seen = self.probe.observations[0]
        self.assertIsNone(seen.decoded_routes)
        self.assertIsNone(seen.returned_id_count)
        self.assertIsNone(seen.dji_response_status)

    def test_the_comparison_is_marked_as_never_performed(self):
        deliver(self.probe, unreadable_response())
        comparison = self.probe.observations[0].comparison
        self.assertIs(comparison['id_comparison_performed'], False)
        self.assertIs(comparison['requested_and_returned_match'], False)

    def test_missing_and_extra_are_not_invented(self):
        """[REASON]: отчёт живого прогона написал «39 запрошенных отсутствуют».
        Кабинет их, возможно, вернул -- это МЫ не прочитали ответ.
        """
        deliver(self.probe, unreadable_response(
            post_data=ids_body([900000001, 900000002, 900000003])))
        comparison = self.probe.observations[0].comparison
        self.assertIsNone(comparison['missing_count'])
        self.assertIsNone(comparison['extra_count'])
        self.assertIsNone(comparison['returned_duplicate_count'])
        self.assertIsNone(comparison['route_without_id_count'])

    def test_the_request_side_counters_stay_real(self):
        """Тело ЗАПРОСА мы прочитали -- про него врать не нужно и незачем."""
        deliver(self.probe, unreadable_response(
            post_data=json.dumps({'flight_record_ids': [900000001, 'oops'],
                                  'data_type': 'simplified'})))
        comparison = self.probe.observations[0].comparison
        self.assertEqual(comparison['invalid_requested_id_count'], 1)
        self.assertEqual(comparison['requested_duplicate_count'], 0)

    def test_the_error_is_counted(self):
        deliver(self.probe, unreadable_response())
        self.assertEqual(self.probe.observation_errors, 1)

    def test_the_run_is_never_confirmed(self):
        deliver(self.probe, unreadable_response())
        seen = self.probe.observations[0]
        self.assertFalse(seen.confirmed)
        self.assertIn('the response body could not be read',
                      seen.not_confirmed_because)
        self.assertEqual(self.outcome(), 13)

    def test_no_misleading_reason_is_given(self):
        """Про тело, которого не было в руках, три прежних причины -- ложь."""
        deliver(self.probe, unreadable_response())
        reasons = self.probe.observations[0].not_confirmed_because
        self.assertNotIn('the payload did not decode', reasons)
        self.assertNotIn('the requested and returned id sets do not match',
                         reasons)
        self.assertNotIn('the payload is not a binary route payload', reasons)

    def test_the_detail_carries_only_the_exception_type_name(self):
        deliver(self.probe, unreadable_response())
        detail = self.probe.observations[0].payload_detail
        self.assertIn('_TargetClosedError', detail)
        self.assertNotIn('SYNTHETIC-BROWSER-DETAIL', detail)

    def test_a_hostile_type_name_is_not_echoed(self):
        """Класс можно объявить с любым `__name__`, в том числе из данных."""
        hostile = type('bad name; Authorization: NOT-REAL', (Exception,), {})
        deliver(self.probe, unreadable_response(exc=hostile('x')))
        detail = self.probe.observations[0].payload_detail
        self.assertIn(UNNAMED_EXCEPTION, detail)
        self.assertNotIn('Authorization', detail)
        self.assertNotIn('NOT-REAL', detail)

    def test_nothing_of_the_exception_reaches_the_log(self):
        deliver(self.probe, unreadable_response())
        self.assertNotIn('SYNTHETIC-BROWSER-DETAIL', self.log.text())

    def test_five_failures_are_counted_five_times(self):
        """Живой прогон: пять ответов, пять TargetClosedError."""
        for index in range(5):
            deliver(self.probe, unreadable_response(
                post_data=ids_body([900000001 + index])))
        self.assertEqual(self.probe.observation_errors, 5)
        self.assertEqual(self.probe.route_responses, 5)

    def test_a_repeated_unreadable_exchange_still_counts_every_failure(self):
        """Наблюдение может схлопнуться, ошибка чтения -- никогда.

        Живой прогон видел пять ответов и четыре наблюдения: один обмен
        повторился. Ошибок при этом было пять.
        """
        for _ in range(2):
            deliver(self.probe, unreadable_response(
                post_data=ids_body([900000001])))
        deliver(self.probe, unreadable_response(
            post_data=ids_body([900000002])))
        self.assertEqual(self.probe.route_responses, 3)
        self.assertEqual(len(self.probe.observations), 2)
        self.assertEqual(self.probe.observations[0].repeats, 2)
        self.assertEqual(self.probe.observation_errors, 3)

    def test_an_unreadable_response_never_shares_a_key_with_a_real_empty(self):
        """Ключ дедупликации различает UNREADABLE и настоящий пустой ответ."""
        deliver(self.probe, unreadable_response(
            post_data=ids_body([900000001])))
        deliver(self.probe, route_response(
            body=b'', post_data=ids_body([900000001])))
        self.assertEqual(len(self.probe.observations), 2)
        kinds = {item.payload_kind for item in self.probe.observations}
        self.assertEqual(kinds, {PAYLOAD_KIND_UNREADABLE, 'EMPTY'})

    def test_a_read_failure_does_not_carry_the_neighbourhood_forward(self):
        self.probe.note_request(IDS_URL)
        deliver(self.probe, unreadable_response(
            post_data=ids_body([900000001])))
        self.assertTrue(self.probe.observations[0].preceded_by_only_all_ids)
        deliver(self.probe, unreadable_response(
            post_data=ids_body([900000002])))
        self.assertFalse(self.probe.observations[1].preceded_by_only_all_ids)

    def test_an_error_before_the_url_is_read_clears_the_neighbourhood(self):
        """Соседство гасится и когда мы не поняли даже, что за ответ пришёл."""
        class _Hostile(object):
            @property
            def url(self):
                raise RuntimeError('SYNTHETIC')

        self.probe.note_request(IDS_URL)
        deliver(self.probe, _Hostile())
        self.assertEqual(self.probe.observation_errors, 1)
        deliver(self.probe, confirmable())
        self.assertFalse(self.probe.observations[0].preceded_by_only_all_ids)

    def test_nothing_of_the_request_leaks_into_the_report(self):
        deliver(self.probe, unreadable_response(
            post_data=ids_body([900000001])))
        write_report(self.probe, self.root)
        haystack = self.everything_written()
        for value in (FAKE_SIGNATURE, FAKE_COOKIE, FAKE_TOKEN,
                      FAKE_REQUEST_ID, '900000001'):
            self.assertNotIn(value, haystack)


class TestARealEmptyBodyStaysEmpty(ProbeTestCase):
    """Отрицательный контроль ко всему предыдущему классу.

    Настоящий пустой ответ ПРОЧИТАН, и врать про него в другую сторону тоже
    нельзя: он остаётся `EMPTY`, с нулём байт, хешем пустоты и без ошибки.
    """

    def test_a_real_empty_body_is_empty_and_was_read(self):
        import hashlib
        deliver(self.probe, route_response(
            body=b'', post_data=ids_body([900000001])))
        seen = self.probe.observations[0]
        self.assertEqual(seen.payload_kind, 'EMPTY')
        self.assertTrue(seen.body_was_read)
        self.assertEqual(seen.response_bytes, 0)
        self.assertEqual(seen.response_sha256, hashlib.sha256(b'').hexdigest())
        self.assertEqual(self.probe.observation_errors, 0)

    def test_a_real_empty_body_does_not_claim_the_ids_are_missing(self):
        """Тело прочитано и пусто -- но разобрать было нечего.

        [REASON]: `compare_id_sets` с пустым списком вернувшихся объявляла все
        запрошенные идентификаторы отсутствующими. Пустое тело -- это ответ, в
        котором маршрутов НЕТ; кабинет мог не вернуть их, а мог вернуть в
        форме, которой мы не поняли. Сверка делается только после успешного
        декодирования.
        """
        deliver(self.probe, route_response(
            body=b'', post_data=ids_body([900000001])))
        seen = self.probe.observations[0]
        self.assertTrue(seen.body_was_read)
        self.assertEqual(seen.payload_kind, 'EMPTY')
        self.assertIsNone(seen.decoded_routes)
        self.assertIsNone(seen.returned_id_count)
        comparison = seen.comparison
        self.assertIs(comparison['id_comparison_performed'], False)
        self.assertIsNone(comparison['missing_count'])
        self.assertIsNone(comparison['extra_count'])

    def test_a_real_binary_body_still_decodes(self):
        deliver(self.probe, confirmable(flight_id=900000001))
        seen = self.probe.observations[0]
        self.assertEqual(seen.payload_kind, 'BINARY')
        self.assertTrue(seen.body_was_read)
        self.assertEqual(seen.decoded_routes, 1)
        self.assertEqual(seen.dji_response_status, 200)
        self.assertTrue(seen.confirmed)
        self.assertEqual(self.probe.observation_errors, 0)
        self.assertIs(seen.comparison['id_comparison_performed'], True)


class TestOperatorWaitDoesNotBlockTheEventLoop(unittest.TestCase):
    """`pump_until` и опрос оператора -- без Playwright и без сна.

    [REASON]: корневая причина живого дефекта. Проверяется само свойство:
    ожидание отдаёт управление на каждом обороте и кончается по сроку, а не
    висит.
    """

    def test_it_returns_true_the_moment_the_condition_holds(self):
        pumps = []
        self.assertTrue(pump_until(pumps.append, lambda: True,
                                   lambda: 0, 1000, 10))
        self.assertEqual(pumps, [])

    def test_it_pumps_until_the_condition_holds(self):
        pumps = []
        state = {'left': 3}

        def done():
            if state['left'] <= 0:
                return True
            state['left'] -= 1
            return False

        self.assertTrue(pump_until(pumps.append, done, lambda: 0, 1000, 7))
        self.assertEqual(pumps, [7, 7, 7])

    def test_it_gives_up_on_the_deadline_instead_of_hanging(self):
        pumps = []
        ticks = iter(range(0, 10000, 100))

        self.assertFalse(pump_until(pumps.append, lambda: False,
                                    lambda: next(ticks), 500, 5))
        # Пять оборотов по 100 мс -- и выход. Не бесконечность.
        self.assertLessEqual(len(pumps), 6)

    def test_a_zero_deadline_still_checks_the_condition_first(self):
        self.assertTrue(pump_until(lambda _ms: None, lambda: True,
                                   lambda: 0, 0, 10))
        self.assertFalse(pump_until(lambda _ms: None, lambda: False,
                                    lambda: 0, 0, 10))

    def test_the_operator_prompt_does_not_block_the_caller(self):
        released = threading.Event()
        prompt = start_operator_prompt('x: ',
                                       reader=lambda _p: released.wait(5))
        # Управление вернулось немедленно, ответа ещё нет.
        self.assertFalse(prompt.done.is_set())
        released.set()
        self.assertTrue(prompt.done.wait(timeout=5))
        self.assertTrue(prompt.answered)
        self.assertFalse(prompt.failed)

    def test_a_reader_that_raises_is_not_an_answer(self):
        """[REASON]: событие ставилось в `finally`, и отказ ввода был
        неотличим от нажатого Enter -- прогон объявлял оператора ответившим,
        хотя тот ничего не нажимал.
        """
        def broken(_prompt):
            raise EOFError('SYNTHETIC-STDIN-DETAIL')

        prompt = start_operator_prompt('x: ', reader=broken)
        # Ожидание кончается СРАЗУ -- потолка в тридцать минут ждать не надо.
        self.assertTrue(prompt.done.wait(timeout=5))
        self.assertFalse(prompt.answered)
        self.assertTrue(prompt.failed)
        self.assertEqual(prompt.error_type, 'EOFError')

    def test_the_prompt_keeps_no_text_of_the_input_failure(self):
        def broken(_prompt):
            raise EOFError('SYNTHETIC-STDIN-DETAIL')

        prompt = start_operator_prompt('x: ', reader=broken)
        prompt.done.wait(timeout=5)
        self.assertNotIn('SYNTHETIC-STDIN-DETAIL', prompt.error_type)

    def test_a_hostile_input_exception_name_is_not_echoed(self):
        hostile = type('bad name; Authorization: NOT-REAL', (Exception,), {})

        def broken(_prompt):
            raise hostile('x')

        prompt = start_operator_prompt('x: ', reader=broken)
        prompt.done.wait(timeout=5)
        self.assertEqual(prompt.error_type, UNNAMED_EXCEPTION)

    def test_the_prompt_thread_is_a_daemon(self):
        """Молчащий человек не должен задерживать выход процесса."""
        before = {t.name for t in threading.enumerate()}
        never = threading.Event()
        prompt = start_operator_prompt('x: ', reader=lambda _p: never.wait(30))
        self.assertFalse(prompt.answered)
        try:
            new = [t for t in threading.enumerate()
                   if t.name not in before
                   and t.name == 'route-ui-probe-operator']
            self.assertTrue(new)
            self.assertTrue(all(t.daemon for t in new))
        finally:
            never.set()


class TestQuietDetection(ProbeTestCase):
    """Признак «ответы кончились», по которому решается drain."""

    def setUp(self):
        ProbeTestCase.setUp(self)
        self.now = {'ms': 1000}
        self.probe = RouteUiProbe(logger=self.log,
                                  expected_origin=ROUTE_ORIGIN,
                                  clock=lambda: self.now['ms'])

    def test_a_probe_that_saw_nothing_is_quiet(self):
        self.assertTrue(self.probe.is_quiet(self.now['ms'], 2000))

    def test_it_is_not_quiet_right_after_a_response(self):
        deliver(self.probe, confirmable())
        self.assertFalse(self.probe.is_quiet(self.now['ms'], 2000))

    def test_it_becomes_quiet_once_the_window_passes(self):
        deliver(self.probe, confirmable())
        self.assertTrue(self.probe.is_quiet(self.now['ms'] + 2000, 2000))

    def test_it_is_never_quiet_while_a_handler_is_running(self):
        """Закрывать браузер под работающим обработчиком -- и есть дефект."""
        seen = []

        class _Slow(object):
            url = ROUTE_URL
            status = 200
            request = _FakeRequest(post_data=ids_body([900000001]),
                                   headers=HEADERS)

            def all_headers(self):
                return {}

            def body(inner):
                seen.append(self.probe.is_quiet(self.now['ms'] + 10 ** 6,
                                                2000))
                return response([route_record(flight_id=900000001)])

        deliver(self.probe, _Slow())
        self.assertEqual(seen, [False])
        self.assertEqual(self.probe.responses_in_flight, 0)


class TestDrainWaitsForTheNetwork(ProbeTestCase):
    """Drain не заканчивается, пока сеть не замолчала ПО-НАСТОЯЩЕМУ.

    [REASON]: `pump_until` спрашивает условие первым, а `is_quiet` при
    `last_route_activity = None` отвечала «тихо» немедленно. Если Enter замечен
    раньше очередного обработчика, браузер закрывался, не прокачав событий ни
    разу, и событие, стоявшее в очереди, терялось. Отсутствие ЗАМЕЧЕННЫХ
    ответов не означает тишины: ответы могут быть в пути.
    """

    def setUp(self):
        ProbeTestCase.setUp(self)
        self.now = {'ms': 1000}
        self.probe = RouteUiProbe(logger=self.log,
                                  expected_origin=ROUTE_ORIGIN,
                                  clock=lambda: self.now['ms'])

    def clock(self):
        return self.now['ms']

    def drain(self, quiet_ms=100, drain_ms=1000, poll_ms=10, on_pump=None):
        """Прокачка, двигающая часы ровно на `poll_ms` за оборот."""
        pumps = []

        def pump(ms):
            pumps.append(ms)
            self.now['ms'] += ms
            if on_pump is not None:
                on_pump(len(pumps))

        self.probe.begin_drain(self.clock())
        completed = pump_until(
            pump, lambda: self.probe.is_quiet(self.clock(), quiet_ms),
            self.clock, drain_ms, poll_ms, min_pumps=1)
        return completed, pumps

    def test_a_probe_that_saw_nothing_still_pumps_the_quiet_window(self):
        completed, pumps = self.drain(quiet_ms=100, poll_ms=10)
        self.assertTrue(completed)
        # Сто миллисекунд тишины по десять за оборот -- десять прокачек.
        self.assertGreaterEqual(len(pumps), 10)

    def test_it_pumps_at_least_once_even_with_a_zero_quiet_window(self):
        """`min_pumps=1`: нулевое окно не должно закрывать браузер мгновенно."""
        completed, pumps = self.drain(quiet_ms=0, poll_ms=10)
        self.assertTrue(completed)
        self.assertGreaterEqual(len(pumps), 1)

    def test_an_event_queued_together_with_enter_is_still_handled(self):
        """Ответ, поставленный в очередь одновременно с Enter, обрабатывается.

        Первая же прокачка доставляет обмен -- он обязан попасть в наблюдения
        до того, как drain объявит тишину.
        """
        pending = [confirmable(flight_id=900000001)]

        def on_pump(count):
            # Событие доставляется НЕ на первом обороте: одной прокачки мало,
            # окно тишины должно выжидаться целиком.
            if count >= 3 and pending:
                deliver(self.probe, pending.pop())

        completed, pumps = self.drain(quiet_ms=100, poll_ms=10,
                                      on_pump=on_pump)
        self.assertTrue(completed)
        self.assertEqual(len(self.probe.observations), 1)
        self.assertTrue(self.probe.observations[0].confirmed)

    def test_an_unfinished_request_keeps_the_drain_open(self):
        """Запрос ушёл до Enter и ещё не завершился -- тишины нет."""
        request = _FakeRequest(headers=HEADERS,
                               post_data=ids_body([900000001]))
        self.probe.note_request(request)
        self.assertEqual(self.probe.pending_route_requests, 1)
        self.assertFalse(self.probe.is_quiet(self.clock() + 10 ** 6, 100))

    def test_a_request_that_finishes_after_enter_is_read_before_closing(self):
        """Тело, догрузившееся после Enter, прочитано до закрытия браузера."""
        request = _FakeRequest(headers=HEADERS,
                               post_data=ids_body([900000001]))
        answer = _FakeResponse(
            ROUTE_URL, response([route_record(flight_id=900000001)]),
            request=request)
        # До Enter: запрос ушёл, статус получен, тела ещё нет.
        self.probe.note_request(request)
        self.probe.note_response(answer)
        self.assertEqual(self.probe.observations, [])
        self.assertEqual(self.probe.pending_route_requests, 1)

        def on_pump(count):
            if count == 3:
                self.probe.note_request_finished(request)

        completed, pumps = self.drain(quiet_ms=100, poll_ms=10,
                                      on_pump=on_pump)
        self.assertTrue(completed)
        self.assertEqual(len(self.probe.observations), 1)
        self.assertTrue(self.probe.observations[0].confirmed)
        self.assertEqual(self.probe.pending_route_requests, 0)

    def test_a_request_that_never_finishes_times_the_drain_out(self):
        """Никогда не завершившийся запрос даёт срок, а не зависание."""
        request = _FakeRequest(headers=HEADERS,
                               post_data=ids_body([900000001]))
        self.probe.note_request(request)
        completed, pumps = self.drain(quiet_ms=100, drain_ms=500, poll_ms=10)
        self.assertFalse(completed)
        self.assertGreaterEqual(len(pumps), 1)
        self.assertLessEqual(len(pumps), 51)
        self.assertEqual(
            probe_exit_code(observations=0, confirmed=0, skipped_over_cap=0,
                            observation_errors=0, drain_timed_out=True), 13)

    def test_a_failed_request_releases_the_drain_and_is_counted(self):
        request = _FakeRequest(headers=HEADERS,
                               post_data=ids_body([900000001]))
        self.probe.note_request(request)
        self.probe.note_request_failed(request)
        self.assertEqual(self.probe.pending_route_requests, 0)
        self.assertEqual(self.probe.route_requests_failed, 1)
        self.assertEqual(self.probe.observation_errors, 1)
        completed, _pumps = self.drain(quiet_ms=100)
        self.assertTrue(completed)
        self.assertEqual(
            probe_exit_code(observations=0, confirmed=0, skipped_over_cap=0,
                            observation_errors=self.probe.observation_errors),
            13)

    def test_a_failed_request_prints_no_browser_reason(self):
        """`failure.error_text` не читается и не печатается."""
        class _Failed(object):
            url = ROUTE_URL

            class failure(object):
                error_text = 'SYNTHETIC-BROWSER-REASON'

        self.probe.note_request_failed(_Failed())
        self.assertNotIn('SYNTHETIC-BROWSER-REASON', self.log.text())
        self.assertEqual(self.probe.route_requests_failed, 1)

    def test_a_failed_request_reaches_the_report(self):
        request = _FakeRequest(post_data=ids_body([900000001]))
        self.probe.note_request(request)
        self.probe.note_request_failed(request)
        document = self.probe.report()
        self.assertEqual(document['route_requests_failed'], 1)
        self.assertEqual(document['route_requests_still_pending'], 0)

    def test_a_handler_in_flight_keeps_the_drain_open(self):
        seen = []

        class _Slow(object):
            url = ROUTE_URL
            status = 200

            def __init__(inner):
                inner.request = _FakeRequest(post_data=ids_body([900000001]),
                                             headers=HEADERS)
                inner.request.response_object = inner

            def all_headers(inner):
                return {}

            def body(inner):
                seen.append(self.probe.is_quiet(self.clock() + 10 ** 6, 100))
                return response([route_record(flight_id=900000001)])

        deliver(self.probe, _Slow())
        self.assertEqual(seen, [False])
        self.assertEqual(self.probe.responses_in_flight, 0)


class TestTheBodyIsReadOnRequestFinished(ProbeTestCase):
    """`response.body()` не зовётся из события `response`.

    [REASON]: Playwright отдаёт `response`, когда получены статус и заголовки;
    тело догружается позже и объявляется `requestfinished`. Чтение тела из
    события `response` -- это чтение того, чего может ещё не быть, и вдобавок
    блокировка внутри `body()` посреди прокачки событий.
    """

    def test_note_response_does_not_touch_the_body(self):
        calls = []

        class _Watched(object):
            url = ROUTE_URL
            status = 200

            def __init__(inner):
                inner.request = _FakeRequest(post_data=ids_body([900000001]),
                                             headers=HEADERS)
                inner.request.response_object = inner

            def all_headers(inner):
                return {}

            def body(inner):
                calls.append('body')
                return response([route_record(flight_id=900000001)])

        answer = _Watched()
        self.probe.note_request(answer.request)
        self.probe.note_response(answer)
        self.assertEqual(calls, [])
        self.assertEqual(self.probe.observations, [])

        self.probe.note_request_finished(answer.request)
        self.assertEqual(calls, ['body'])
        self.assertEqual(len(self.probe.observations), 1)

    def test_the_module_calls_body_from_exactly_one_place(self):
        """Структурная проверка по дереву разбора, а не по тексту.

        Ищутся настоящие вызовы `.body()`, а не упоминания в комментариях и
        докстрингах, и называется функция, из которой они сделаны.
        """
        import ast
        import inspect

        import drone_collector.route_ui_probe as module

        tree = ast.parse(inspect.getsource(module))
        callers = set()
        for node in ast.walk(tree):
            if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            for inner in ast.walk(node):
                if (isinstance(inner, ast.Call)
                        and isinstance(inner.func, ast.Attribute)
                        and inner.func.attr == 'body'):
                    callers.add(node.name)
        self.assertEqual(callers, {'_note_route_response'})
        self.assertNotIn('note_response', callers)

    def test_an_observation_needs_the_finished_event(self):
        """Один `note_response` наблюдения не создаёт."""
        answer = confirmable(flight_id=900000001)
        self.probe.note_request(answer.request)
        self.probe.note_response(answer)
        self.assertEqual(self.probe.observations, [])
        self.assertEqual(self.probe.pending_route_requests, 1)


class TestComparisonOnlyAfterDecoding(ProbeTestCase):
    """Сверка ID -- только после успешного декодирования маршрутов.

    [REASON]: `compare_id_sets` с пустым списком вернувшихся объявляла ВСЕ
    запрошенные идентификаторы отсутствующими -- при пустом теле, при теле
    сверх потолка, при JSON-отказе и при неразобранном protobuf. Кабинет,
    возможно, вернул их все: это МЫ ничего не разобрали.
    """

    def not_compared(self, seen):
        comparison = seen.comparison
        self.assertIs(comparison['id_comparison_performed'], False)
        self.assertIsNone(comparison['missing_count'])
        self.assertIsNone(comparison['extra_count'])
        self.assertIsNone(seen.returned_id_count)
        self.assertIsNone(seen.decoded_routes)
        self.assertIn('the requested and returned id sets were never compared',
                      seen.not_confirmed_because)
        self.assertNotIn('the requested and returned id sets do not match',
                         seen.not_confirmed_because)

    def test_a_vendor_refusal_does_not_claim_missing_ids(self):
        deliver(self.probe, route_response(
            body=b'{"status": 408, "code": 408, "msg": "no"}',
            post_data=ids_body([900000001, 900000002])))
        self.not_compared(self.probe.observations[0])

    def test_an_undecodable_binary_does_not_claim_missing_ids(self):
        deliver(self.probe, route_response(
            body=b'\x08\xff\xff', post_data=ids_body([900000001])))
        self.not_compared(self.probe.observations[0])

    def test_html_does_not_claim_missing_ids(self):
        deliver(self.probe, route_response(
            body=b'<html>nope</html>', post_data=ids_body([900000001])))
        self.not_compared(self.probe.observations[0])

    def test_an_oversized_body_does_not_claim_missing_ids(self):
        deliver(self.probe, route_response(
            body=b'\x08' * (MAX_PROCESSED_RESPONSE_BYTES + 16),
            post_data=ids_body([900000001])))
        seen = self.probe.observations[0]
        self.assertEqual(seen.payload_kind, 'TOO_LARGE')
        self.not_compared(seen)

    def test_a_declared_oversize_does_not_claim_missing_ids(self):
        class _Declaring(object):
            url = ROUTE_URL
            status = 200

            def __init__(inner):
                inner.request = _FakeRequest(
                    headers=HEADERS, post_data=ids_body([900000001]))
                inner.request.response_object = inner

            def all_headers(inner):
                return {'Content-Length':
                        str(MAX_PROCESSED_RESPONSE_BYTES + 1)}

            def body(inner):
                raise AssertionError('the body was requested anyway')

        deliver(self.probe, _Declaring())
        self.not_compared(self.probe.observations[0])

    def test_a_decoded_body_is_the_positive_control(self):
        """Разобранный ответ сверяется по-настоящему, как и раньше."""
        deliver(self.probe, route_response(
            body=response([route_record(flight_id=900000001)]),
            post_data=ids_body([900000001, 900000002])))
        comparison = self.probe.observations[0].comparison
        self.assertIs(comparison['id_comparison_performed'], True)
        self.assertEqual(comparison['missing_count'], 1)
        self.assertEqual(comparison['extra_count'], 0)


UNRELATED_URL = 'https://cdn.example.invalid/assets/logo.png'


def unrelated_request(url=UNRELATED_URL):
    """Картинка, шрифт, аналитика -- что угодно, кроме маршрутов."""
    return _FakeRequest(method='GET', post_data=None, url=url)


class TestIrrelevantTrafficIsIgnored(ProbeTestCase):
    """Фоновый трафик страницы не трогает таймер тишины МАРШРУТОВ.

    [REASON]: `_release()` и `_touch()` стояли в общем `finally`, то есть
    выполнялись и после раннего выхода по нерелевантному URL. Любая картинка,
    шрифт или запрос аналитики обновляли `last_route_activity`, и drain мог не
    закончиться никогда при том, что маршрутный трафик давно кончился.
    """

    def setUp(self):
        ProbeTestCase.setUp(self)
        self.now = {'ms': 1000}
        self.probe = RouteUiProbe(logger=self.log,
                                  expected_origin=ROUTE_ORIGIN,
                                  clock=lambda: self.now['ms'])

    def snapshot(self):
        return (self.probe.last_route_activity,
                self.probe.pending_route_requests,
                self.probe.route_requests_failed,
                self.probe.observation_errors,
                self.probe.route_responses,
                self.probe.saw_only_all_ids,
                self.probe._ids_seen_recently)

    def test_an_unrelated_request_finished_changes_nothing(self):
        before = self.snapshot()
        self.now['ms'] += 10 ** 6
        self.probe.note_request_finished(unrelated_request())
        self.assertEqual(self.snapshot(), before)
        self.assertIsNone(self.probe.last_route_activity)

    def test_an_unrelated_request_failed_changes_nothing(self):
        before = self.snapshot()
        self.now['ms'] += 10 ** 6
        self.probe.note_request_failed(unrelated_request())
        self.assertEqual(self.snapshot(), before)
        self.assertEqual(self.probe.route_requests_failed, 0)
        self.assertEqual(self.probe.observation_errors, 0)
        self.assertEqual(self.probe.pending_route_requests, 0)
        self.assertIsNone(self.probe.last_route_activity)

    def test_an_unrelated_request_does_not_move_a_real_activity_mark(self):
        """Настоящая отметка маршрутов не сдвигается посторонним трафиком."""
        deliver(self.probe, confirmable(flight_id=900000001))
        marked = self.probe.last_route_activity
        self.now['ms'] += 10 ** 6
        self.probe.note_request_finished(unrelated_request())
        self.probe.note_request_failed(unrelated_request(
            'https://analytics.example.invalid/collect'))
        self.assertEqual(self.probe.last_route_activity, marked)

    def test_unrelated_traffic_does_not_hold_the_drain_open(self):
        """Картинки, сыплющиеся во время drain, не продлевают окно тишины."""
        deliver(self.probe, confirmable(flight_id=900000001))
        self.probe.begin_drain(self.now['ms'])
        pumps = []

        def pump(ms):
            pumps.append(ms)
            self.now['ms'] += ms
            # На каждой прокачке страница догружает постороннее.
            self.probe.note_request_finished(unrelated_request())
            self.probe.note_request_failed(unrelated_request(
                'https://fonts.example.invalid/x.woff2'))

        completed = pump_until(
            pump, lambda: self.probe.is_quiet(self.now['ms'], 100),
            lambda: self.now['ms'], 5000, 10, min_pumps=1)
        self.assertTrue(completed)
        self.assertLessEqual(len(pumps), 12)
        self.assertEqual(self.probe.route_requests_failed, 0)
        self.assertEqual(self.probe.observation_errors, 0)

    def test_a_real_route_request_finished_does_hold_it_open(self):
        """Отрицательный контроль: настоящий маршрутный обмен окно продлевает."""
        self.probe.begin_drain(self.now['ms'])
        delivered = []
        pumps = []

        def pump(ms):
            pumps.append(ms)
            self.now['ms'] += ms
            if len(pumps) in (2, 5, 8) and len(delivered) < 3:
                delivered.append(deliver(
                    self.probe,
                    confirmable(flight_id=900000001 + len(delivered))))

        completed = pump_until(
            pump, lambda: self.probe.is_quiet(self.now['ms'], 100),
            lambda: self.now['ms'], 5000, 10, min_pumps=1)
        self.assertTrue(completed)
        self.assertEqual(len(delivered), 3)
        # Последняя доставка на восьмом обороте, дальше десять оборотов тишины.
        self.assertGreaterEqual(len(pumps), 18)

    def test_a_real_route_request_failed_releases_pending_and_marks_activity(self):
        request = _FakeRequest(headers=HEADERS,
                               post_data=ids_body([900000001]))
        self.probe.note_request(request)
        self.assertEqual(self.probe.pending_route_requests, 1)
        marked = self.probe.last_route_activity
        self.now['ms'] += 500
        self.probe.note_request_failed(request)
        self.assertEqual(self.probe.pending_route_requests, 0)
        self.assertEqual(self.probe.route_requests_failed, 1)
        self.assertEqual(self.probe.observation_errors, 1)
        self.assertGreater(self.probe.last_route_activity, marked)
        self.assertEqual(
            probe_exit_code(observations=0, confirmed=0, skipped_over_cap=0,
                            observation_errors=self.probe.observation_errors),
            13)

    def test_an_unreadable_url_is_not_turned_into_route_activity(self):
        """Адрес не прочитался -- ошибку считаем, активностью не называем."""
        class _Hostile(object):
            @property
            def url(self):
                raise RuntimeError('SYNTHETIC')

        self.probe.note_request_finished(_Hostile())
        self.assertEqual(self.probe.observation_errors, 1)
        self.assertIsNone(self.probe.last_route_activity)
        self.assertEqual(self.probe.pending_route_requests, 0)
        self.assertEqual(self.probe.route_requests_failed, 0)

    def test_the_only_all_ids_counter_grows_in_one_place_only(self):
        """Счётчик растёт в `note_request`; терминальные события его не трогают."""
        ids_request = _FakeRequest(method='GET', url=IDS_URL, post_data=None)
        self.probe.note_request(ids_request)
        self.assertEqual(self.probe.saw_only_all_ids, 1)
        self.probe.note_request_finished(ids_request)
        self.probe.note_request_failed(ids_request)
        self.assertEqual(self.probe.saw_only_all_ids, 1)
        # И времени маршрутов они не отмечают.
        self.assertIsNone(self.probe.last_route_activity)


class TestPumpUntilAtAZeroDeadline(unittest.TestCase):
    """Поведение при нулевом сроке оговорено однозначно.

    [REASON]: `min_pumps` и жёсткий срок -- свойства несовместимые. Обещать
    оба сразу нельзя, поэтому здесь ЯВНО зафиксировано, какое выигрывает:
    срок. Противоречивая конфигурация отвергается заранее, а не чинится тихой
    подменой смысла внутри цикла.
    """

    def test_a_zero_deadline_beats_min_pumps(self):
        pumps = []
        self.assertFalse(pump_until(pumps.append, lambda: False,
                                    lambda: 0, 0, 10, min_pumps=1))
        self.assertEqual(pumps, [])

    def test_a_zero_deadline_beats_min_pumps_even_when_already_quiet(self):
        pumps = []
        self.assertFalse(pump_until(pumps.append, lambda: True,
                                    lambda: 0, 0, 10, min_pumps=1))
        self.assertEqual(pumps, [])

    def test_without_min_pumps_a_zero_deadline_still_checks_the_condition(self):
        self.assertTrue(pump_until(lambda _ms: None, lambda: True,
                                   lambda: 0, 0, 10))

    def test_a_deadline_of_one_poll_allows_exactly_one_pump(self):
        pumps = []
        ticks = iter([0, 0, 10, 10, 10])
        self.assertFalse(pump_until(pumps.append, lambda: False,
                                    lambda: next(ticks), 10, 10, min_pumps=1))
        self.assertEqual(pumps, [10])


class TestProbeTimingValidation(unittest.TestCase):
    """Противоречивая настройка ожидания отвергается ДО браузера."""

    OK = dict(poll_ms=200, wait_ms=1800000, drain_ms=15000, quiet_ms=2000)

    def test_the_defaults_are_accepted(self):
        from drone_collector.config import (DEFAULT_ROUTE_PROBE_DRAIN_MS,
                                            DEFAULT_ROUTE_PROBE_POLL_MS,
                                            DEFAULT_ROUTE_PROBE_QUIET_MS,
                                            DEFAULT_ROUTE_PROBE_WAIT_MS)
        self.assertIsNone(validate_probe_timings(
            poll_ms=DEFAULT_ROUTE_PROBE_POLL_MS,
            wait_ms=DEFAULT_ROUTE_PROBE_WAIT_MS,
            drain_ms=DEFAULT_ROUTE_PROBE_DRAIN_MS,
            quiet_ms=DEFAULT_ROUTE_PROBE_QUIET_MS))

    def refuses(self, **changes):
        fields = dict(self.OK)
        fields.update(changes)
        with self.assertRaises(ProbeTimingError) as caught:
            validate_probe_timings(**fields)
        return str(caught.exception)

    def test_a_zero_drain_is_refused(self):
        message = self.refuses(drain_ms=0)
        self.assertIn('DJI_ROUTE_PROBE_DRAIN_MS', message)

    def test_a_drain_shorter_than_one_poll_is_refused(self):
        message = self.refuses(poll_ms=200, drain_ms=100, quiet_ms=0)
        self.assertIn('DJI_ROUTE_PROBE_POLL_MS', message)

    def test_a_drain_not_longer_than_the_quiet_window_is_refused(self):
        message = self.refuses(drain_ms=2000, quiet_ms=2000)
        self.assertIn('DJI_ROUTE_PROBE_QUIET_MS', message)

    def test_a_zero_poll_is_refused(self):
        self.assertIn('DJI_ROUTE_PROBE_POLL_MS', self.refuses(poll_ms=0))

    def test_a_zero_wait_is_refused(self):
        self.assertIn('DJI_ROUTE_PROBE_WAIT_MS', self.refuses(wait_ms=0))

    def test_a_drain_exactly_one_poll_long_is_accepted(self):
        """Граница включена: одной прокачки хватает."""
        self.assertIsNone(validate_probe_timings(
            poll_ms=200, wait_ms=1000, drain_ms=200, quiet_ms=0))

    def test_the_message_carries_only_names_and_numbers(self):
        from drone_collector.outbox import find_secret_markers
        message = self.refuses(poll_ms=0, drain_ms=0, quiet_ms=99999)
        self.assertEqual(find_secret_markers(message), [])
        for forbidden in ('http://', 'https://', '/home/', 'C:\\\\'):
            self.assertNotIn(forbidden, message)
        self.assertRegex(message, r'^[\x20-\x7e]+$')


def _rich_point(lat, lng, extra=b''):
    """Точка с координатами и, при желании, лишними полями."""
    from drone_collector.tests.test_route_decode import f_bytes, f_double
    return f_bytes(1, f_double(1, lat) + f_double(2, lng) + extra)


def route_record_with_third_field(flight_id=900000001, points=2):
    """Запись маршрута, точки которой несут третье неизвестное поле.

    Синтетика: живой прогон 2026-08-29 упёрся ровно в такую форму, но само
    тело ответа не сохранялось и в фикстуры не попадает.
    """
    from drone_collector.tests.test_route_decode import (
        FAKE_LAT, FAKE_LNG, f_varint)
    body = b''
    for index in range(points):
        body += _rich_point(FAKE_LAT + index * 0.001, FAKE_LNG,
                            f_varint(3, 1780670376 + index))
    return body + f_varint(2, flight_id)


class TestPointShapeCensus(ProbeTestCase):
    """Безопасная диагностика вариантов точек.

    [REASON]: живой прогон 2026-08-29 показал, что форма точки у DJI не одна.
    Чтобы следующий разговор шёл о фактах, probe считает варианты -- номера
    полей, wire types и количества, -- и НЕ выпускает ни координат, ни
    значений неизвестных полей, ни идентификаторов вылетов.
    """

    def census(self, body):
        deliver(self.probe, route_response(
            body=body, post_data=ids_body([900000001])))
        return self.probe.observations[0].as_dict()['point_shape_census']

    def test_the_old_two_field_point_is_one_clean_variant(self):
        census = self.census(response([route_record(flight_id=900000001)]))
        self.assertEqual(len(census['route_point_variants']), 1)
        variant = census['route_point_variants'][0]
        self.assertEqual(variant['fields'],
                         [{'number': 1, 'wire': 1, 'count': 1},
                          {'number': 2, 'wire': 1, 'count': 1}])
        self.assertEqual(census['route_points_with_unknown_fields'], 0)
        self.assertEqual(census['unknown_fields'], [])

    def test_a_third_field_is_counted_and_named_unknown(self):
        census = self.census(response([route_record_with_third_field()]))
        self.assertEqual(census['route_points_total'], 2)
        self.assertEqual(census['route_points_with_unknown_fields'], 2)
        self.assertEqual(len(census['unknown_fields']), 1)
        unknown = census['unknown_fields'][0]
        self.assertEqual(unknown['number'], 3)
        self.assertEqual(unknown['wire'], 0)
        self.assertEqual(unknown['points'], 2)
        self.assertEqual(unknown['occurrences'], 2)
        self.assertEqual(unknown['semantics'], 'UNKNOWN_SEMANTICS')
        self.assertEqual(unknown['site'], 'route_point')

    def test_two_shapes_in_one_body_are_two_variants(self):
        from drone_collector.tests.test_route_decode import (
            FAKE_LAT, FAKE_LNG, f_varint)
        body = (_rich_point(FAKE_LAT, FAKE_LNG)
                + _rich_point(FAKE_LAT, FAKE_LNG, f_varint(3, 1))
                + f_varint(2, 900000001))
        census = self.census(response([body]))
        self.assertEqual(len(census['route_point_variants']), 2)
        self.assertEqual(census['route_points_total'], 2)
        self.assertEqual(census['route_points_with_unknown_fields'], 1)

    def test_the_takeoff_is_counted_apart_from_the_route_points(self):
        from drone_collector.tests.test_route_decode import (
            FAKE_LAT, FAKE_LNG, f_bytes, f_double, f_varint)
        body = (_rich_point(FAKE_LAT, FAKE_LNG)
                + f_varint(2, 900000001)
                + f_bytes(7, f_double(1, FAKE_LAT) + f_double(2, FAKE_LNG)
                          + f_varint(4, 9)))
        census = self.census(response([body]))
        self.assertEqual(census['route_points_with_unknown_fields'], 0)
        self.assertEqual(census['takeoffs_total'], 1)
        self.assertEqual(census['takeoffs_with_unknown_fields'], 1)
        sites = {item['site'] for item in census['unknown_fields']}
        self.assertEqual(sites, {'takeoff'})

    def test_no_coordinate_reaches_the_census(self):
        from drone_collector.tests.test_route_decode import FAKE_LAT, FAKE_LNG
        census = self.census(response([route_record_with_third_field()]))
        text = json.dumps(census)
        for value in (FAKE_LAT, FAKE_LNG):
            self.assertNotIn(str(value), text)
            self.assertNotIn(str(value)[:7], text)

    def test_no_unknown_value_and_no_flight_id_reach_the_report(self):
        deliver(self.probe, route_response(
            body=response([route_record_with_third_field()]),
            post_data=ids_body([900000001])))
        write_report(self.probe, self.root)
        haystack = self.everything_written()
        # Значение третьего поля -- 1780670376 и 1780670377.
        self.assertNotIn('1780670376', haystack)
        self.assertNotIn('1780670377', haystack)
        self.assertNotIn('900000001', haystack)
        for value in (FAKE_SIGNATURE, FAKE_COOKIE, FAKE_TOKEN,
                      FAKE_REQUEST_ID):
            self.assertNotIn(value, haystack)

    def test_no_raw_body_reaches_the_report(self):
        body = response([route_record_with_third_field()])
        deliver(self.probe, route_response(
            body=body, post_data=ids_body([900000001])))
        write_report(self.probe, self.root)
        haystack = self.everything_written()
        self.assertNotIn(body.hex(), haystack)
        self.assertNotIn(repr(body)[2:40], haystack)

    def test_an_unreadable_body_carries_an_empty_census(self):
        deliver(self.probe, route_response(
            body=b'{"status": 408, "code": 408, "msg": "no"}',
            post_data=ids_body([900000001])))
        document = self.probe.observations[0].as_dict()
        self.assertEqual(document['point_shape_census'], {})


class TestTheProbeConfirmsAfterRouteDecodeTwo(ProbeTestCase):
    """Сверка ID делается ПОСЛЕ успешного route-decode-2.

    [REASON]: тело с третьим полем раньше не декодировалось вовсе, поэтому до
    сверки идентификаторов дело не доходило и код был 13 по причине «не
    разобралось». Теперь оно разбирается -- и все прежние условия
    подтверждения обязаны отработать в полном объёме.
    """

    def outcome(self):
        return probe_exit_code(
            observations=len(self.probe.observations),
            confirmed=len(self.probe.confirmed_observations),
            skipped_over_cap=self.probe.skipped_over_cap,
            observation_errors=self.probe.observation_errors)

    def test_a_body_with_third_fields_is_confirmed_when_the_ids_match(self):
        deliver(self.probe, route_response(
            body=response([route_record_with_third_field(900000001)]),
            post_data=ids_body([900000001])))
        seen = self.probe.observations[0]
        self.assertEqual(seen.payload_kind, 'BINARY')
        self.assertEqual(seen.decoded_routes, 1)
        self.assertEqual(seen.dji_response_status, 200)
        self.assertIs(seen.comparison['id_comparison_performed'], True)
        self.assertTrue(seen.confirmed, seen.not_confirmed_because)
        self.assertEqual(self.outcome(), 0)

    def test_a_missing_id_is_still_counted(self):
        deliver(self.probe, route_response(
            body=response([route_record_with_third_field(900000001)]),
            post_data=ids_body([900000001, 900000002])))
        comparison = self.probe.observations[0].comparison
        self.assertEqual(comparison['missing_count'], 1)
        self.assertEqual(comparison['extra_count'], 0)
        self.assertFalse(self.probe.observations[0].confirmed)
        self.assertEqual(self.outcome(), 13)

    def test_an_extra_id_is_still_counted(self):
        deliver(self.probe, route_response(
            body=response([route_record_with_third_field(900000001),
                           route_record_with_third_field(900000002)]),
            post_data=ids_body([900000001])))
        comparison = self.probe.observations[0].comparison
        self.assertEqual(comparison['extra_count'], 1)
        self.assertEqual(self.outcome(), 13)

    def test_a_duplicate_returned_id_is_still_refused(self):
        deliver(self.probe, route_response(
            body=response([route_record_with_third_field(900000001),
                           route_record_with_third_field(900000001)]),
            post_data=ids_body([900000001])))
        comparison = self.probe.observations[0].comparison
        self.assertEqual(comparison['returned_duplicate_count'], 1)
        self.assertFalse(self.probe.observations[0].confirmed)
        self.assertEqual(self.outcome(), 13)

    def test_a_route_without_an_id_is_still_refused(self):
        from drone_collector.tests.test_route_decode import (
            FAKE_LAT, FAKE_LNG, f_varint)
        body = _rich_point(FAKE_LAT, FAKE_LNG, f_varint(3, 1))
        deliver(self.probe, route_response(
            body=response([body]), post_data=ids_body([900000001])))
        comparison = self.probe.observations[0].comparison
        self.assertEqual(comparison['route_without_id_count'], 1)
        self.assertFalse(self.probe.observations[0].confirmed)

    def test_a_bad_internal_status_is_still_refused(self):
        deliver(self.probe, route_response(
            body=response([route_record_with_third_field(900000001)],
                          status=101),
            post_data=ids_body([900000001])))
        seen = self.probe.observations[0]
        self.assertEqual(seen.dji_response_status, 101)
        self.assertFalse(seen.confirmed)
        self.assertIn('the DJI envelope status is not the success status',
                      seen.not_confirmed_because)
        self.assertEqual(self.outcome(), 13)

    def test_a_corrupt_point_still_refuses_to_decode(self):
        """Отрицательный контроль: снисхождения к координатам не появилось."""
        from drone_collector.tests.test_route_decode import (
            FAKE_LNG, f_bytes, f_double, f_varint)
        broken = f_bytes(1, f_double(2, FAKE_LNG)) + f_varint(2, 900000001)
        deliver(self.probe, route_response(
            body=response([broken]), post_data=ids_body([900000001])))
        seen = self.probe.observations[0]
        self.assertIsNone(seen.decoded_routes)
        self.assertFalse(seen.confirmed)
        self.assertEqual(seen.point_census, {})
        self.assertEqual(self.outcome(), 13)


class TestNothingLeaks(ProbeTestCase):

    def test_no_header_value_reaches_the_log_or_the_report(self):
        deliver(self.probe, route_response())
        write_report(self.probe, self.root)
        haystack = self.everything_written()
        for value in (FAKE_SIGNATURE, FAKE_COOKIE, FAKE_TOKEN):
            self.assertNotIn(value, haystack)

    def test_no_request_id_reaches_the_log_or_the_report(self):
        refusal = json.dumps({'status': 408, 'code': 408,
                              'request_id': FAKE_REQUEST_ID}).encode('utf-8')
        deliver(self.probe, route_response(body=refusal))
        write_report(self.probe, self.root)
        haystack = self.everything_written()
        self.assertNotIn(FAKE_REQUEST_ID, haystack)
        self.assertNotIn('request_id', haystack)

    def test_no_response_body_reaches_the_report(self):
        body = response([route_record(flight_id=FAKE_FLIGHT_ID)])
        deliver(self.probe, route_response(body=body))
        target = write_report(self.probe, self.root)
        written = target.read_text(encoding='utf-8')
        self.assertNotIn(body.hex(), written)
        self.assertIn('response_sha256', written)

    def test_the_report_says_plainly_what_it_did_not_do(self):
        """И не утверждает того, чего код не доказывает.

        [REASON]: `no_request_was_made_by_this_tool` было НЕПРАВДОЙ -- probe
        открывает кабинет через `open_records()`, и это навигация, то есть
        запрос. Гарантия, которую он действительно даёт, уже: POST к эндпоинту
        маршрутов он не инициирует.
        """
        deliver(self.probe, route_response())
        document = json.loads(
            write_report(self.probe, self.root).read_text(encoding='utf-8'))
        self.assertTrue(document['nothing_was_queued'])
        self.assertTrue(document['nothing_was_sent_to_vehicle_soft'])
        self.assertTrue(document['no_route_post_was_initiated_by_probe'])
        self.assertNotIn('no_request_was_made_by_this_tool', document)

    def test_a_request_body_value_never_reaches_the_report_at_all(self):
        """Из тела берутся ЧИСЛА и имена ключей, значения остаются снаружи."""
        deliver(self.probe, route_response(
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
        deliver(self.probe, route_response(
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
        deliver(self.probe, route_response())
        written = write_report(self.probe, self.root).read_text(
            encoding='utf-8')
        self.assertIn('x-auth-token', written)
        self.assertNotIn(FAKE_TOKEN, written)

    def test_an_ordinary_report_is_written(self):
        """Отрицательный контроль: проверка не глушит нормальный отчёт."""
        deliver(self.probe, route_response())
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
        self.assertIn('never issues the route POST itself', text)
        self.assertIn('queues', text)
        self.assertNotIn('makes no request of its own', text,
                         'вернулось утверждение, которого код не доказывает')


if __name__ == '__main__':
    unittest.main(verbosity=2)
