# -*- coding: utf-8 -*-
"""Тесты tools/drone_route_probe.py. Без сети, без браузера, без базы.

    python tools/test_drone_route_probe.py

Главное, что здесь проверяется, -- предупреждение о секретах. Проверка,
которая срабатывает всегда, ничего не находит: у неё есть отрицательный
контроль на файле, где секретов нет, и он обязан молчать.
"""

import json
import os
import struct
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from tools.drone_route_probe import dedupe, read_capture  # noqa: E402


def varint(value):
    out = bytearray()
    while True:
        byte = value & 0x7F
        value >>= 7
        if value:
            out.append(byte | 0x80)
        else:
            out.append(byte)
            return bytes(out)


def field_bytes(number, raw):
    if isinstance(raw, str):
        raw = raw.encode('utf-8')
    return varint((number << 3) | 2) + varint(len(raw)) + raw


def field_varint(number, value):
    return varint((number << 3) | 0) + varint(value)


def tiny_response():
    """Минимальный правдоподобный ответ: статус, сообщение, один пустой блок."""
    point = field_bytes(1, struct.pack('<d', 40.08).join(
        [varint((1 << 3) | 1), b'']) + varint((2 << 3) | 1)
        + struct.pack('<d', 64.63))
    record = point + field_varint(2, 900000001)
    return field_varint(1, 200) + field_bytes(2, 'Success.') \
        + field_bytes(3, field_bytes(1, record))


def write_capture(directory, name, entries, source_note):
    path = os.path.join(directory, name)
    with open(path, 'w', encoding='utf-8') as handle:
        json.dump({'generatedAt': '2026-08-25T00:00:00',
                   'source': source_note,
                   'entries': entries}, handle, ensure_ascii=False)
    return path


class TestSecretDetection(unittest.TestCase):

    def test_a_signed_url_in_a_response_body_is_reported(self):
        with tempfile.TemporaryDirectory() as directory:
            body = json.dumps({'data': {'lands': {'edges': [{'node': {
                'geometry': {'storage': {
                    'signedURL': 'https://example.invalid/x?OSSAccessKeyId=A'
                                 '&Signature=B&Expires=1'}}}}]}}})
            path = write_capture(directory, 'leaky.json', [
                {'url': 'https://example.invalid/graphql?name=lands',
                 'responseBody': body, 'encoding': None}],
                'headers removed')
            _bodies, secrets = read_capture(path)
        self.assertIn('signedURL', secrets)
        self.assertIn('OSSAccessKeyId', secrets)

    def test_a_clean_capture_reports_nothing(self):
        """Отрицательный контроль.

        Описание очистки в самом файле упоминает cookies -- на этом слове
        проверка срабатывать НЕ должна, иначе она кричит на каждом файле и
        перестаёт что-либо значить.
        """
        with tempfile.TemporaryDirectory() as directory:
            path = write_capture(directory, 'clean.json', [
                {'url': 'https://example.invalid/api/web/v1/flight_records',
                 'responseBody': '{"status":200,"code":0,"data":[]}',
                 'encoding': None}],
                'network capture, request headers, cookies and query strings '
                'removed')
            _bodies, secrets = read_capture(path)
        self.assertEqual(secrets, [])


class TestRouteExtraction(unittest.TestCase):

    def test_only_the_route_endpoint_is_taken(self):
        import base64
        with tempfile.TemporaryDirectory() as directory:
            raw = base64.b64encode(tiny_response()).decode('ascii')
            path = write_capture(directory, 'mixed.json', [
                {'url': 'https://example.invalid/api/web/v1/flight_records',
                 'responseBody': '{"status":200}', 'encoding': None},
                {'url': 'https://example.invalid/api/web/v2/flight_datas/'
                        'flight_records',
                 'responseBody': raw, 'encoding': 'base64'}],
                'clean')
            bodies, _secrets = read_capture(path)
        self.assertEqual(len(bodies), 1)
        self.assertEqual(bodies[0][1], tiny_response())

    def test_a_raw_body_file_is_accepted_too(self):
        with tempfile.TemporaryDirectory() as directory:
            path = os.path.join(directory, 'body.bin')
            with open(path, 'wb') as handle:
                handle.write(tiny_response())
            bodies, secrets = read_capture(path)
        self.assertEqual(len(bodies), 1)
        self.assertEqual(bodies[0][1], tiny_response())
        self.assertEqual(secrets, [])


class TestDedupe(unittest.TestCase):

    def test_identical_bodies_collapse_and_are_counted(self):
        body = tiny_response()
        unique = dedupe([('a', body), ('b', body), ('c', body)])
        self.assertEqual(len(unique), 1)
        self.assertEqual(unique[0][2], 3)

    def test_a_different_body_does_not_collapse(self):
        """Отрицательный контроль к тесту выше."""
        unique = dedupe([('a', tiny_response()),
                         ('b', tiny_response() + b'\x00')])
        self.assertEqual(len(unique), 2)


if __name__ == '__main__':
    unittest.main()
