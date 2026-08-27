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
    MAX_PROCESSED_RESPONSE_BYTES,
    MAX_RESPONSE_BYTES,
    PROMPT_LINES,
    RouteUiProbe,
    compare_id_sets,
    describe_headers,
    probe_exit_code,
    is_ids_url,
    is_route_url,
    read_request_ids,
    summarise_request_body,
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
            body=body, post_data=ids_body([FAKE_FLIGHT_ID])))
        seen = self.probe.observations[0]
        self.assertEqual(seen.payload_kind, 'BINARY')
        self.assertEqual(seen.decoded_routes, 1)
        self.assertEqual(seen.returned_id_count, 1)
        self.assertTrue(seen.comparison['requested_and_returned_match'])

    def test_a_count_mismatch_is_reported_without_the_identifiers(self):
        body = response([route_record(flight_id=FAKE_FLIGHT_ID)])
        self.probe.note_response(route_response(
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


class TestIdSetsNotCounts(ProbeTestCase):
    """Совпадение количеств -- не совпадение множеств.

    [REASON]: `requested_and_returned_match` означал равенство ДЛИН. «Девять
    запросили, девять вернулось» объявлялось совпадением, даже если это девять
    ЧУЖИХ маршрутов -- а именно множества и решают, чьи маршруты приехали.
    """

    def test_two_different_sets_of_the_same_size_do_not_match(self):
        body = response([route_record(flight_id=900000001),
                         route_record(flight_id=900000002)])
        self.probe.note_response(route_response(
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
        self.probe.note_response(route_response(
            body=body, post_data=ids_body([900000002, 900000001])))
        seen = self.probe.observations[0]
        self.assertTrue(seen.comparison['requested_and_returned_match'])
        self.assertEqual(seen.comparison['missing_count'], 0)
        self.assertEqual(seen.comparison['extra_count'], 0)

    def test_a_partial_overlap_is_counted_on_both_sides(self):
        body = response([route_record(flight_id=900000001),
                         route_record(flight_id=900000009)])
        self.probe.note_response(route_response(
            body=body, post_data=ids_body([900000001, 900000002])))
        comparison = self.probe.observations[0].comparison
        self.assertFalse(comparison['requested_and_returned_match'])
        self.assertEqual(comparison['missing_count'], 1)
        self.assertEqual(comparison['extra_count'], 1)

    def test_no_identifier_reaches_the_report_or_the_log(self):
        body = response([route_record(flight_id=900000001),
                         route_record(flight_id=900000002)])
        self.probe.note_response(route_response(
            body=body, post_data=ids_body([900000003, 900000004])))
        write_report(self.probe, self.root)
        haystack = self.everything_written()
        for value in ('900000001', '900000002', '900000003', '900000004'):
            self.assertNotIn(value, haystack)

    def test_the_report_carries_only_booleans_and_counts(self):
        body = response([route_record(flight_id=900000001)])
        self.probe.note_response(route_response(
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
        self.probe.note_response(route_response(
            body=body, post_data=ids_body([self.ID_A, self.ID_A])))
        self.assertFalse(self.confirmed())
        self.assertEqual(self.comparison()['requested_duplicate_count'], 1)

    def test_a_duplicate_in_the_response_is_not_confirmed(self):
        """request [ID], response -- два маршрута с тем же ID."""
        body = response([route_record(flight_id=self.ID_A),
                         route_record(flight_id=self.ID_A)])
        self.probe.note_response(route_response(
            body=body, post_data=ids_body([self.ID_A])))
        self.assertFalse(self.confirmed())
        self.assertEqual(self.comparison()['returned_duplicate_count'], 1)

    def test_a_route_without_an_id_is_not_confirmed(self):
        """request [ID], response -- один правильный и один без ID."""
        body = response([route_record(flight_id=self.ID_A),
                         route_record_without_id()])
        self.probe.note_response(route_response(
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
                probe.note_response(route_response(
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
        self.probe.note_response(route_response(
            body=body, post_data=ids_body([900000003, 900000004])))
        self.assertFalse(self.confirmed())

    def test_a_clean_unique_set_is_the_positive_control(self):
        """Отрицательный контроль: строгость не гасит нормальный случай."""
        body = response([route_record(flight_id=self.ID_A),
                         route_record(flight_id=self.ID_B)])
        self.probe.note_response(route_response(
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
        self.probe.note_response(route_response(
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
        self.probe.note_response(route_response(
            body=body, post_data=ids_body([900000001])))
        self.probe.note_response(route_response(
            body=body, post_data=ids_body([900000002])))
        self.assertEqual(len(self.probe.observations), 2)

    def test_the_same_body_for_the_same_request_is_merged(self):
        """Отрицательный контроль: настоящий повтор по-прежнему один."""
        body = response([route_record(flight_id=FAKE_FLIGHT_ID)])
        for _ in range(3):
            self.probe.note_response(route_response(
                body=body, post_data=ids_body([900000001])))
        self.assertEqual(len(self.probe.observations), 1)
        self.assertEqual(self.probe.observations[0].repeats, 3)


class TestOnlyAllIdsCountedOncePerExchange(ProbeTestCase):

    def test_a_request_and_its_response_count_once(self):
        """[REASON]: счётчик рос и на запросе, и на ответе -- один обмен
        считался дважды, и «два only_all_ids перед маршрутом» означало один.
        """
        self.probe.note_request(IDS_URL)
        self.probe.note_response(_FakeResponse(IDS_URL, b'{"code": 0}'))
        self.assertEqual(self.probe.saw_only_all_ids, 1)

    def test_two_exchanges_count_twice(self):
        """Отрицательный контроль: счётчик всё-таки считает."""
        for _ in range(2):
            self.probe.note_request(IDS_URL)
            self.probe.note_response(_FakeResponse(IDS_URL, b'{"code": 0}'))
        self.assertEqual(self.probe.saw_only_all_ids, 2)

    def test_the_neighbourhood_flag_still_works(self):
        self.probe.note_request(IDS_URL)
        self.probe.note_response(_FakeResponse(IDS_URL, b'{"code": 0}'))
        self.probe.note_response(route_response())
        self.assertTrue(
            self.probe.observations[0].preceded_by_only_all_ids)


class TestConfirmation(ProbeTestCase):
    """Код 0 разрешён только при полном успехе."""

    def confirmed_response(self):
        body = response([route_record(flight_id=FAKE_FLIGHT_ID)])
        return route_response(body=body,
                              post_data=ids_body([FAKE_FLIGHT_ID]))

    def test_a_full_success_is_confirmed(self):
        self.probe.note_response(self.confirmed_response())
        seen = self.probe.observations[0]
        self.assertTrue(seen.confirmed, seen.not_confirmed_because)
        self.assertEqual(seen.not_confirmed_because, [])
        self.assertEqual(len(self.probe.confirmed_observations), 1)

    def test_a_vendor_refusal_is_not_confirmed(self):
        refusal = json.dumps({'status': 408, 'code': 408}).encode('utf-8')
        self.probe.note_response(route_response(body=refusal))
        seen = self.probe.observations[0]
        self.assertFalse(seen.confirmed)
        self.assertIn('the payload is not a binary route payload',
                      seen.not_confirmed_because)

    def test_html_is_not_confirmed(self):
        self.probe.note_response(route_response(b'<html>502</html>'))
        self.assertFalse(self.probe.observations[0].confirmed)

    def test_an_undecodable_binary_is_not_confirmed(self):
        self.probe.note_response(route_response(b'\xff\xff\xff\xff'))
        seen = self.probe.observations[0]
        self.assertFalse(seen.confirmed)
        self.assertIn('the payload did not decode',
                      seen.not_confirmed_because)

    def test_a_non_2xx_status_is_not_confirmed(self):
        body = response([route_record(flight_id=FAKE_FLIGHT_ID)])
        self.probe.note_response(route_response(
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
        self.probe.note_response(answer)
        seen = self.probe.observations[0]
        self.assertFalse(seen.confirmed)
        self.assertIn('the method is not POST', seen.not_confirmed_because)

    def test_another_host_is_not_confirmed(self):
        body = response([route_record(flight_id=FAKE_FLIGHT_ID)])
        answer = _FakeResponse(
            'https://elsewhere.invalid/api/web/v2/flight_datas/flight_records',
            body, request=_FakeRequest(
                headers=HEADERS, post_data=ids_body([FAKE_FLIGHT_ID])))
        self.probe.note_response(answer)
        seen = self.probe.observations[0]
        self.assertFalse(seen.confirmed)
        self.assertIn('the host is not the expected route API host',
                      seen.not_confirmed_because)

    def test_a_plain_http_origin_is_not_confirmed(self):
        probe = RouteUiProbe(logger=self.log,
                             expected_origin='http://kr-ag2-api.example.invalid')
        probe.note_response(self.confirmed_response())
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
        self.probe.note_response(answer)
        seen = self.probe.observations[0]
        self.assertFalse(seen.confirmed)
        self.assertIn('the path is not the exact route endpoint',
                      seen.not_confirmed_because)

    def test_mismatched_id_sets_are_not_confirmed(self):
        body = response([route_record(flight_id=900000001)])
        self.probe.note_response(route_response(
            body=body, post_data=ids_body([900000002])))
        seen = self.probe.observations[0]
        self.assertFalse(seen.confirmed)
        self.assertIn('the requested and returned id sets do not match',
                      seen.not_confirmed_because)

    def test_the_report_counts_confirmed_observations(self):
        self.probe.note_response(self.confirmed_response())
        self.probe.note_response(route_response(b'<html>502</html>'))
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

        self.probe.note_response(_Declaring())
        seen = self.probe.observations[0]
        self.assertEqual(asked, [])
        self.assertEqual(seen.payload_kind, 'TOO_LARGE')
        self.assertEqual(seen.response_bytes,
                         MAX_PROCESSED_RESPONSE_BYTES + 1)
        self.assertIsNone(seen.response_sha256)
        self.assertFalse(seen.confirmed)

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

        self.probe.note_response(_Declaring())
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

        self.probe.note_response(_Declaring())
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
        self.probe.note_response(route_response(body=raw))
        seen = self.probe.observations[0]
        self.assertEqual(seen.response_bytes, len(raw))
        self.assertGreater(seen.response_bytes, MAX_RESPONSE_BYTES)

    def test_no_hash_of_an_empty_body_is_recorded(self):
        import hashlib
        empty_digest = hashlib.sha256(b'').hexdigest()
        self.probe.note_response(route_response(body=self.oversize()))
        seen = self.probe.observations[0]
        self.assertIsNone(seen.response_sha256)
        self.assertNotEqual(seen.response_sha256, empty_digest)

    def test_it_is_named_too_large_and_not_decoded(self):
        self.probe.note_response(route_response(body=self.oversize()))
        seen = self.probe.observations[0]
        self.assertEqual(seen.payload_kind, 'TOO_LARGE')
        self.assertIsNone(seen.decoded_routes)
        self.assertFalse(seen.confirmed)

    def test_an_ordinary_response_still_gets_its_hash(self):
        """Отрицательный контроль: обычный ответ хешируется как раньше."""
        self.probe.note_response(route_response())
        seen = self.probe.observations[0]
        self.assertEqual(len(seen.response_sha256), 64)


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
        """И не утверждает того, чего код не доказывает.

        [REASON]: `no_request_was_made_by_this_tool` было НЕПРАВДОЙ -- probe
        открывает кабинет через `open_records()`, и это навигация, то есть
        запрос. Гарантия, которую он действительно даёт, уже: POST к эндпоинту
        маршрутов он не инициирует.
        """
        self.probe.note_response(route_response())
        document = json.loads(
            write_report(self.probe, self.root).read_text(encoding='utf-8'))
        self.assertTrue(document['nothing_was_queued'])
        self.assertTrue(document['nothing_was_sent_to_vehicle_soft'])
        self.assertTrue(document['no_route_post_was_initiated_by_probe'])
        self.assertNotIn('no_request_was_made_by_this_tool', document)

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
        self.assertIn('never issues the route POST itself', text)
        self.assertIn('queues', text)
        self.assertNotIn('makes no request of its own', text,
                         'вернулось утверждение, которого код не доказывает')


if __name__ == '__main__':
    unittest.main(verbosity=2)
