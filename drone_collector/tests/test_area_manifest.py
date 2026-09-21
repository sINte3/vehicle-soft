# -*- coding: utf-8 -*-
"""DJI-AREA-PRODUCTIONIZATION-001: клиент манифеста адресного захвата.

Что здесь держится:

* «манифест пуст» и «манифест недоступен» -- РАЗНЫЕ исходы: пустой даёт
  пустой ids-файл и код 0, недоступный -- код 23 и НИКАКОГО файла, чтобы
  следующий шаг не принял вчерашний список за сегодняшний;
* слишком большой манифест -- код 22: файл записан, но в DJI с ним идти нельзя;
* токен уходит в теле запроса и не попадает ни в файл, ни в сводку;
* ids-файл читается тем же `read_ids_file`, что и `--ids-file`.

Сеть не трогается: `post_fn` подставной. Stdlib.
"""

import io
import json
import os
import shutil
import tempfile
import unittest

from drone_collector import area_manifest as tool
from drone_collector.routes import read_ids_file
from drone_collector.sender import TransportError

TOKEN = 'SYNTHETIC-collector-token-NOT-REAL'


class FakeConfig(object):
    base_url = 'http://staging.invalid:5051/'
    api_token = TOKEN


def manifest(ids):
    return {'status': 'ok', 'date_from': '2026-09-16',
            'date_to': '2026-09-18', 'over_cap': False,
            'counts': {'candidates_need_capture': min(1, len(ids)),
                       'capture_total': len(ids),
                       'candidates_no_v4_at_source': 0},
            'capture': [{'flight_id': i, 'reason': 'STRUCTURAL_CANDIDATE'
                         if n == 0 else 'CONTROL',
                         'control_kind': None if n == 0 else 'RANDOM'}
                        for n, i in enumerate(ids)]}


class Post(object):

    def __init__(self, status=200, body=None, error=None):
        self.status, self.body, self.error = status, body, error
        self.calls = []

    def __call__(self, url, payload, timeout_s):
        self.calls.append((url, payload))
        if self.error:
            raise self.error
        return self.status, self.body


class Base(unittest.TestCase):

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix='area_manifest_')
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        self.out = os.path.join(self.tmp, 'ids', 'area_ids.txt')
        self.summary = os.path.join(self.tmp, 'manifest.json')
        self._load = tool.load_config
        tool.load_config = lambda require_ingest=True: FakeConfig()
        self.addCleanup(setattr, tool, 'load_config', self._load)

    def run_tool(self, post, *extra):
        return tool.main(['--out', self.out, '--summary', self.summary]
                         + list(extra), post_fn=post,
                         sleep_fn=lambda seconds: None)


class Outcomes(Base):

    def test_the_ids_reach_the_file_the_collector_reads(self):
        post = Post(body=manifest([701, 702, 703]))
        self.assertEqual(self.run_tool(post), tool.EXIT_OK)
        self.assertEqual(sorted(read_ids_file(self.out)), [701, 702, 703])

    def test_the_request_goes_to_the_manifest_endpoint_with_the_window(self):
        post = Post(body=manifest([701]))
        self.run_tool(post, '--from', '2026-09-16', '--to', '2026-09-18',
                      '--max-ids', '30')
        url, payload = post.calls[0]
        self.assertEqual(url, 'http://staging.invalid:5051'
                              '/drones/api/area_capture_manifest')
        self.assertEqual(payload, {'token': TOKEN, 'date_from': '2026-09-16',
                                   'date_to': '2026-09-18', 'max_ids': 30})

    def test_an_empty_manifest_is_good_news_not_a_failure(self):
        post = Post(body=manifest([]))
        self.assertEqual(self.run_tool(post), tool.EXIT_OK)
        self.assertTrue(os.path.exists(self.out))
        self.assertEqual(list(read_ids_file(self.out)), [])

    def test_an_unavailable_manifest_writes_no_file_at_all(self):
        post = Post(error=TransportError('connection refused'))
        self.assertEqual(self.run_tool(post), tool.EXIT_MANIFEST_UNAVAILABLE)
        self.assertFalse(os.path.exists(self.out))
        self.assertFalse(os.path.exists(self.summary))

    def test_a_refused_token_is_unavailable_not_empty(self):
        post = Post(status=401, body={'error': 'unauthorized'})
        self.assertEqual(self.run_tool(post), tool.EXIT_MANIFEST_UNAVAILABLE)
        self.assertFalse(os.path.exists(self.out))

    def test_an_answer_without_a_capture_list_is_unavailable(self):
        post = Post(body={'status': 'ok'})
        self.assertEqual(self.run_tool(post), tool.EXIT_MANIFEST_UNAVAILABLE)
        self.assertFalse(os.path.exists(self.out))

    def test_a_manifest_above_the_threshold_is_code_22(self):
        post = Post(body=manifest(list(range(700, 760))))
        self.assertEqual(self.run_tool(post, '--stop-above', '50'),
                         tool.EXIT_MANIFEST_TOO_LARGE)
        # Файл записан -- человеку есть на что посмотреть.
        self.assertEqual(len(read_ids_file(self.out)), 60)

    def test_exactly_the_threshold_still_passes(self):
        # Отрицательный контроль к проверке выше.
        post = Post(body=manifest(list(range(700, 750))))
        self.assertEqual(self.run_tool(post, '--stop-above', '50'),
                         tool.EXIT_OK)


class Secrets(Base):

    def test_the_token_reaches_neither_file(self):
        post = Post(body=manifest([701, 702]))
        self.run_tool(post)
        for path in (self.out, self.summary):
            with io.open(path, encoding='utf-8') as fh:
                self.assertNotIn(TOKEN, fh.read(), path)
        # Отрицательный контроль: токен действительно ушёл -- в теле запроса.
        self.assertEqual(post.calls[0][1]['token'], TOKEN)

    def test_the_summary_is_the_manifest_as_received(self):
        body = manifest([701])
        self.run_tool(Post(body=body))
        with io.open(self.summary, encoding='utf-8') as fh:
            self.assertEqual(json.load(fh), body)


class Usage(Base):

    def test_half_a_window_is_a_usage_error(self):
        post = Post(body=manifest([701]))
        self.assertEqual(self.run_tool(post, '--from', '2026-09-16'),
                         tool.EXIT_CONFIG)
        self.assertEqual(post.calls, [])


if __name__ == '__main__':
    unittest.main()
