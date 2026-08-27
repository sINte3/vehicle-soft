# -*- coding: utf-8 -*-
"""Тесты классификации тела ответа маршрутов.

Первый живой прогон этапа B (2026-08-27) получил на все девятнадцать пакетов
одинаковый JSON-отказ кабинета и записал девятнадцать «повреждённых тел» в
двоичный карантин. Здесь проверяется, что такое тело опознаётся ДО декодера,
называется отказом поставщика и не ложится на диск.

Все фикстуры синтетические. `request_id` в них -- нулевой UUID, и половина
тестов посвящена тому, что он никуда не попадает.
"""

import json
import unittest

from drone_collector.route_payload import (
    MAX_JSON_BYTES,
    MAX_MESSAGE_CHARS,
    MAX_PAYLOAD_BYTES,
    PAYLOAD_BINARY,
    PAYLOAD_EMPTY,
    PAYLOAD_JSON_UNKNOWN,
    PAYLOAD_JSON_UNREADABLE,
    PAYLOAD_SECRET,
    PAYLOAD_TEXT_UNKNOWN,
    PAYLOAD_TOO_LARGE,
    PAYLOAD_VENDOR_ERROR,
    PAYLOAD_VENDOR_OK,
    READ_KEYS,
    WRITABLE_KINDS,
    classify_payload,
    safe_message,
)

# Синтетический UUID. Настоящий request_id владельцем не передавался.
SYNTHETIC_REQUEST_ID = '00000000-0000-4000-8000-000000000000'

# Форма живого отказа, дословно по структуре и значениям, кроме request_id.
VENDOR_REFUSAL = {
    'status': 408,
    'code': 408,
    'msg': '请求时间无效',
    'message': '请求时间无效',
    'request_id': SYNTHETIC_REQUEST_ID,
}


def blob(document):
    return json.dumps(document, ensure_ascii=False).encode('utf-8')


class TestTheLiveRefusal(unittest.TestCase):
    """Точная форма, полученная 2026-08-27."""

    def setUp(self):
        self.raw = blob(VENDOR_REFUSAL)
        self.verdict = classify_payload(self.raw)

    def test_it_is_a_vendor_refusal_and_not_a_broken_protobuf(self):
        self.assertEqual(self.verdict.kind, PAYLOAD_VENDOR_ERROR)
        self.assertTrue(self.verdict.is_vendor_refusal)
        self.assertFalse(self.verdict.is_binary)

    def test_the_numbers_are_kept_as_safe_diagnostics(self):
        self.assertEqual(self.verdict.status, 408)
        self.assertEqual(self.verdict.code, 408)
        self.assertEqual(self.verdict.message, '请求时间无效')

    def test_the_body_is_never_written(self):
        self.assertFalse(self.verdict.body_may_be_written)

    def test_the_request_id_is_nowhere_in_the_verdict(self):
        """Он не читается вовсе -- значит и напечатать его нечем."""
        haystack = ' '.join([
            json.dumps(self.verdict.as_dict(), ensure_ascii=False),
            self.verdict.describe(),
            repr(self.verdict),
            str(self.verdict.detail),
        ])
        self.assertNotIn(SYNTHETIC_REQUEST_ID, haystack)
        self.assertNotIn('request_id', haystack)

    def test_the_reader_names_the_keys_it_takes(self):
        """Список исчерпывающий, и request_id в него не входит."""
        self.assertEqual(READ_KEYS, ('status', 'code', 'msg', 'message'))
        self.assertNotIn('request_id', READ_KEYS)

    def test_the_hash_identifies_the_body_without_keeping_it(self):
        self.assertEqual(len(self.verdict.sha256), 64)
        self.assertEqual(self.verdict.bytes, len(self.raw))


class TestOtherVendorCodes(unittest.TestCase):
    """Решение принимается по `code != 0`, а не по одному числу 408."""

    def test_code_101_is_a_refusal_too(self):
        verdict = classify_payload(blob({'status': 101, 'code': 101,
                                         'msg': 'signature missing'}))
        self.assertEqual(verdict.kind, PAYLOAD_VENDOR_ERROR)
        self.assertEqual(verdict.code, 101)

    def test_an_unseen_code_is_a_refusal_too(self):
        verdict = classify_payload(blob({'status': 500, 'code': 500,
                                         'msg': 'whatever'}))
        self.assertEqual(verdict.kind, PAYLOAD_VENDOR_ERROR)

    def test_code_zero_is_not_a_refusal_but_is_not_a_route_payload_either(self):
        """Отрицательный контроль: успех отличается от отказа.

        И при этом JSON с `code 0` НЕ считается маршрутами: маршруты приходят
        двоичными, и догадываться о новой форме нельзя.
        """
        verdict = classify_payload(blob({'status': 200, 'code': 0,
                                         'message': 'OK', 'data': []}))
        self.assertEqual(verdict.kind, PAYLOAD_VENDOR_OK)
        self.assertFalse(verdict.is_vendor_refusal)
        self.assertFalse(verdict.is_binary)
        self.assertFalse(verdict.body_may_be_written)

    def test_a_boolean_code_is_not_a_number(self):
        """`True` -- не 1. Иначе `code: true` прочиталось бы как отказ."""
        verdict = classify_payload(blob({'status': 200, 'code': True}))
        self.assertEqual(verdict.kind, PAYLOAD_JSON_UNKNOWN)


class TestOtherShapes(unittest.TestCase):

    def test_a_real_binary_payload_stays_binary(self):
        """Отрицательный контроль: настоящий protobuf идёт прежним путём."""
        verdict = classify_payload(b'\x08\x01\x12\x07Success')
        self.assertEqual(verdict.kind, PAYLOAD_BINARY)
        self.assertTrue(verdict.is_binary)
        self.assertTrue(verdict.body_may_be_written)

    def test_a_json_array_is_unknown_not_binary(self):
        verdict = classify_payload(b'[1, 2, 3]')
        self.assertEqual(verdict.kind, PAYLOAD_JSON_UNKNOWN)
        self.assertFalse(verdict.body_may_be_written)

    def test_html_is_named_and_never_written(self):
        verdict = classify_payload(b'<!doctype html><html><body>502</body>')
        self.assertEqual(verdict.kind, PAYLOAD_TEXT_UNKNOWN)
        self.assertFalse(verdict.body_may_be_written)

    def test_broken_json_is_named(self):
        verdict = classify_payload(b'{"status": 408, "code":')
        self.assertEqual(verdict.kind, PAYLOAD_JSON_UNREADABLE)
        self.assertFalse(verdict.body_may_be_written)

    def test_an_object_without_a_numeric_code_is_unknown(self):
        verdict = classify_payload(b'{"hello": "world"}')
        self.assertEqual(verdict.kind, PAYLOAD_JSON_UNKNOWN)

    def test_an_empty_body_is_named(self):
        self.assertEqual(classify_payload(b'').kind, PAYLOAD_EMPTY)

    def test_a_body_over_the_cap_is_not_parsed(self):
        verdict = classify_payload(b'x' * 64, max_bytes=32)
        self.assertEqual(verdict.kind, PAYLOAD_TOO_LARGE)
        self.assertIn('the cap is 32', verdict.detail)

    def test_json_shaped_but_enormous_is_not_parsed_as_json(self):
        raw = b'{' + b'"a":1,' * (MAX_JSON_BYTES // 6 + 8) + b'"b":2}'
        self.assertGreater(len(raw), MAX_JSON_BYTES)
        verdict = classify_payload(raw)
        self.assertEqual(verdict.kind, PAYLOAD_JSON_UNKNOWN)
        self.assertIn('JSON cap', verdict.detail)

    def test_a_leading_byte_order_mark_does_not_hide_the_json(self):
        verdict = classify_payload(b'\xef\xbb\xbf' + blob(VENDOR_REFUSAL))
        self.assertEqual(verdict.kind, PAYLOAD_VENDOR_ERROR)

    def test_leading_whitespace_does_not_hide_the_json(self):
        verdict = classify_payload(b'\n\n  ' + blob(VENDOR_REFUSAL))
        self.assertEqual(verdict.kind, PAYLOAD_VENDOR_ERROR)


class TestOnlyBinaryMayBeWritten(unittest.TestCase):
    """Разрешение на запись -- положительный список из одной строки.

    [REASON]: прежний список был ЗАПРЕЩАЮЩИМ, и в него не попали `EMPTY` и
    `TOO_LARGE`. Пустое тело и тело сверх потолка молча получали право лечь на
    диск как «непонятый protobuf». Ошибка ровно того класса, ради которого
    карантин и заведён.
    """

    def test_the_allowlist_holds_exactly_one_kind(self):
        self.assertEqual(WRITABLE_KINDS, (PAYLOAD_BINARY,))

    def test_an_empty_body_may_not_be_written(self):
        self.assertFalse(classify_payload(b'').body_may_be_written)

    def test_a_body_over_the_cap_may_not_be_written(self):
        verdict = classify_payload(b'x' * 64, max_bytes=32)
        self.assertEqual(verdict.kind, PAYLOAD_TOO_LARGE)
        self.assertFalse(verdict.body_may_be_written)

    def test_json_shaped_but_not_utf8_is_unreadable_json_not_binary(self):
        """Заявка на JSON, которая не декодируется, -- нечитаемый JSON.

        [REASON]: прежняя редакция возвращала здесь `BINARY`, то есть выдавала
        испорченному тексту право лечь на диск и отправляла его в декодер,
        который всё равно откажется.
        """
        raw = b'{"msg": "' + b'\xff\xfe\xfd' + b'"}'
        verdict = classify_payload(raw)
        self.assertEqual(verdict.kind, PAYLOAD_JSON_UNREADABLE)
        self.assertFalse(verdict.body_may_be_written)
        self.assertIn('UTF-8', verdict.detail)

    def test_a_json_array_shaped_but_not_utf8_is_also_unreadable(self):
        verdict = classify_payload(b'[\xff\xfe]')
        self.assertEqual(verdict.kind, PAYLOAD_JSON_UNREADABLE)
        self.assertFalse(verdict.body_may_be_written)

    def test_no_non_binary_kind_may_be_written(self):
        """Сплошной проход по всем видам, которые умеет выдавать классификатор."""
        cases = {
            PAYLOAD_EMPTY: b'',
            PAYLOAD_VENDOR_ERROR: blob(VENDOR_REFUSAL),
            PAYLOAD_VENDOR_OK: blob({'status': 200, 'code': 0}),
            PAYLOAD_JSON_UNKNOWN: b'[1, 2, 3]',
            PAYLOAD_JSON_UNREADABLE: b'{"a":',
            PAYLOAD_TEXT_UNKNOWN: b'<html>502</html>',
        }
        for kind, raw in cases.items():
            verdict = classify_payload(raw)
            self.assertEqual(verdict.kind, kind, repr(raw))
            self.assertFalse(verdict.body_may_be_written, kind)

    def test_binary_is_the_positive_control(self):
        """Отрицательный контроль: карантин двоичного тела не отменён."""
        verdict = classify_payload(b'\x08\x01\x12\x07Success')
        self.assertEqual(verdict.kind, PAYLOAD_BINARY)
        self.assertTrue(verdict.body_may_be_written)


class TestSecretsBlockTheBody(unittest.TestCase):

    def test_a_marker_in_a_json_body_blocks_the_write(self):
        raw = blob({'status': 408, 'code': 408,
                    'msg': 'see https://oss.aliyuncs.com/x?OSSAccessKeyId=LTAI'})
        verdict = classify_payload(raw)
        self.assertEqual(verdict.kind, PAYLOAD_SECRET)
        self.assertFalse(verdict.body_may_be_written)
        self.assertTrue(verdict.secret_markers)

    def test_the_marker_is_named_but_the_value_is_not(self):
        raw = blob({'code': 408,
                    'msg': 'https://oss.aliyuncs.com/x?OSSAccessKeyId=LTAISECRET'})
        verdict = classify_payload(raw)
        self.assertNotIn('LTAISECRET', verdict.describe())
        self.assertNotIn('LTAISECRET',
                         json.dumps(verdict.as_dict(), ensure_ascii=False))

    def test_an_ordinary_body_is_not_called_a_secret(self):
        """Отрицательный контроль: проверка не блокирует нормальный отказ."""
        self.assertEqual(classify_payload(blob(VENDOR_REFUSAL)).kind,
                         PAYLOAD_VENDOR_ERROR)


class TestSafeMessage(unittest.TestCase):

    def test_a_normal_message_survives(self):
        self.assertEqual(safe_message('请求时间无效'), '请求时间无效')

    def test_control_characters_drop_the_whole_text(self):
        """Не «чистится», а отвергается целиком.

        [REASON]: подчищенный до читаемости текст выглядит безобиднее, чем он
        есть, и попадает в лог как обычное сообщение поставщика.
        """
        self.assertIsNone(safe_message('bad\x00message'))
        self.assertIsNone(safe_message('bad\x1bmessage'))

    def test_a_long_message_is_truncated(self):
        long_text = 'x' * (MAX_MESSAGE_CHARS + 50)
        self.assertEqual(len(safe_message(long_text)), MAX_MESSAGE_CHARS)

    def test_a_non_string_is_not_a_message(self):
        for value in (None, 42, [], {}, True):
            self.assertIsNone(safe_message(value), repr(value))

    def test_the_cap_is_far_above_the_real_message(self):
        self.assertGreater(MAX_MESSAGE_CHARS, len('请求时间无效'))
        self.assertLess(MAX_JSON_BYTES, MAX_PAYLOAD_BYTES)


if __name__ == '__main__':
    unittest.main(verbosity=2)
