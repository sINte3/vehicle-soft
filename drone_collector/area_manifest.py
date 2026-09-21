# -*- coding: utf-8 -*-
"""drone_collector/area_manifest.py -- кому из свежих вылетов нужен адресный V4.

DJI-AREA-PRODUCTIONIZATION-001. Спрашивает у Vehicle Soft манифест и пишет
ids-файл, который затем берёт `--sources --ids-file`:

    python -m drone_collector.area_manifest --out C:\\...\\area_ids.txt
    python -m drone_collector.main --sources --ids-file C:\\...\\area_ids.txt --send-sources

К кабинету DJI этот модуль НЕ обращается и браузер не запускает: один
POST к своему же приёмнику. Отдельный модуль, а не ещё один флаг `main.py`,
намеренно: матрица режимов там держится тестами `test_cli`, а этому шагу не
нужен ни период обхода, ни сессия, ни Playwright.

[REASON]: «манифест не ответил» и «манифест пуст» -- РАЗНЫЕ исходы, и путать
их нельзя. Пустой манифест -- хорошая новость: захватывать нечего, ids-файл
пишется пустым, код 0. Недоступный манифест -- отказ с кодом 23: ids-файл НЕ
пишется вовсе, чтобы следующий шаг не принял вчерашний список за сегодняшний.

Коды возврата: 0 -- манифест получен, файл записан (возможно, пустой);
1 -- конфигурация либо аргументы; 22 -- идентификаторов больше `--stop-above`,
файл записан, но идти с ним в DJI нельзя, пока человек не посмотрел;
23 -- манифест недоступен, файл не записан.
"""

import argparse
import io
import json
import os
import sys
import time

from drone_collector import config as config_module
from drone_collector.config import ConfigError, load_config
from drone_collector.logging_setup import setup_logging
from drone_collector.sender import (IngestRejected, TransportError,
                                    _post_with_retries, _requests_post)

AREA_CAPTURE_MANIFEST_PATH = '/drones/api/area_capture_manifest'

EXIT_OK = 0
EXIT_CONFIG = 1
EXIT_MANIFEST_TOO_LARGE = 22
EXIT_MANIFEST_UNAVAILABLE = 23

# Ориентир разумного дневного объёма. На сентябрьских данных свежий день --
# от 6 до 42 идентификаторов при медиане 19.
DEFAULT_STOP_ABOVE = 50


class ManifestUnavailable(Exception):
    pass


def manifest_url(cfg):
    if not cfg.base_url:
        return None
    return cfg.base_url.rstrip('/') + AREA_CAPTURE_MANIFEST_PATH


def build_payload(token, date_from=None, date_to=None, max_ids=None):
    payload = {'token': token}
    if date_from:
        payload['date_from'] = date_from
    if date_to:
        payload['date_to'] = date_to
    if max_ids is not None:
        payload['max_ids'] = int(max_ids)
    return payload


def fetch_manifest(cfg, date_from=None, date_to=None, max_ids=None,
                   logger=None, post_fn=None, sleep_fn=None):
    """Манифест как словарь. `ManifestUnavailable`, если его не получить.

    Тело запроса несёт токен и в лог не пишется никогда.
    """
    url = manifest_url(cfg)
    if not url:
        raise ManifestUnavailable('no base URL is configured')
    payload = build_payload(cfg.api_token, date_from, date_to, max_ids)
    try:
        body = _post_with_retries(post_fn or _requests_post,
                                  sleep_fn or time.sleep, url, payload, 1, 1,
                                  logger)
    except (TransportError, IngestRejected) as exc:
        raise ManifestUnavailable(str(exc))
    except ImportError as exc:
        # [REASON]: у сборщика свой venv, и `requests` живёт там. Запуск общим
        # интерпретатором приложения -- ошибка окружения, а не манифеста, и
        # сказать это надо словами, а не трассировкой.
        raise ManifestUnavailable(
            '%s -- run this module with the collector interpreter '
            '(--collector-python)' % exc)
    if not isinstance(body, dict) or body.get('status') != 'ok' \
            or not isinstance(body.get('capture'), list):
        raise ManifestUnavailable('the receiver answered without a capture '
                                  'list')
    return body


def capture_ids(manifest):
    ids = []
    for entry in manifest.get('capture') or []:
        try:
            ids.append(int(entry['flight_id']))
        except (KeyError, TypeError, ValueError):
            raise ManifestUnavailable('a capture entry carries no flight id')
    return ids


def write_ids_file(path, manifest):
    """ids-файл для `--ids-file`: по одному в строке, `#` -- комментарий."""
    counts = manifest.get('counts') or {}
    lines = ['# area capture manifest %s .. %s'
             % (manifest.get('date_from'), manifest.get('date_to')),
             '# candidates %s, controls %s'
             % (counts.get('candidates_need_capture'),
                counts.get('capture_total', 0)
                - (counts.get('candidates_need_capture') or 0))]
    for entry in manifest.get('capture') or []:
        lines.append('%d  # %s %s' % (int(entry['flight_id']),
                                      entry.get('reason'),
                                      entry.get('control_kind') or ''))
    directory = os.path.dirname(os.path.abspath(path))
    os.makedirs(directory, exist_ok=True)
    # Сначала рядом, затем замена: оборванная запись не оставит пол-файла,
    # который `--ids-file` прочёл бы как законченный список.
    tmp = path + '.tmp'
    with io.open(tmp, 'w', encoding='utf-8', newline='\n') as handle:
        handle.write('\n'.join(lines) + '\n')
    os.replace(tmp, path)


def build_parser():
    parser = argparse.ArgumentParser(
        description='Ask Vehicle Soft which fresh flights need an addressed '
                    'source/V4 capture and write them to an ids file. Never '
                    'talks to DJI.')
    parser.add_argument('--out', required=True, metavar='PATH',
                        help='ids file for --sources --ids-file')
    parser.add_argument('--summary', metavar='PATH',
                        help='also write the whole manifest as JSON')
    parser.add_argument('--from', dest='date_from', metavar='YYYY-MM-DD')
    parser.add_argument('--to', dest='date_to', metavar='YYYY-MM-DD')
    parser.add_argument('--max-ids', dest='max_ids', type=int)
    parser.add_argument('--stop-above', dest='stop_above', type=int,
                        default=DEFAULT_STOP_ABOVE,
                        help='exit 22 when the manifest names more ids')
    return parser


def main(argv=None, post_fn=None, sleep_fn=None):
    log = setup_logging(config_module.PACKAGE_ROOT / 'logs')
    args = build_parser().parse_args(argv)
    if (args.date_from is None) != (args.date_to is None):
        log.error('Usage error: --from and --to are given together or not '
                  'at all')
        return EXIT_CONFIG
    try:
        cfg = load_config(require_ingest=True)
    except ConfigError as exc:
        log.error('Configuration error: %s', exc)
        return EXIT_CONFIG
    try:
        manifest = fetch_manifest(cfg, args.date_from, args.date_to,
                                  args.max_ids, logger=log, post_fn=post_fn,
                                  sleep_fn=sleep_fn)
        ids = capture_ids(manifest)
    except ManifestUnavailable as exc:
        log.error('The area capture manifest is unavailable: %s. No ids file '
                  'was written.', exc)
        return EXIT_MANIFEST_UNAVAILABLE

    write_ids_file(args.out, manifest)
    if args.summary:
        with io.open(args.summary, 'w', encoding='utf-8') as handle:
            json.dump(manifest, handle, ensure_ascii=False, indent=2,
                      sort_keys=True)
    counts = manifest.get('counts') or {}
    candidates = counts.get('candidates_need_capture') or 0
    log.info('AREA MANIFEST %s .. %s: ids=%d candidates=%d controls=%d '
             'no_v4_at_source=%d over_cap=%s',
             manifest.get('date_from'), manifest.get('date_to'), len(ids),
             candidates, len(ids) - candidates,
             counts.get('candidates_no_v4_at_source') or 0,
             manifest.get('over_cap'))
    if len(ids) > args.stop_above:
        log.error('The manifest names %d id(s), more than --stop-above %d. '
                  'The ids file was written, but do not take it to DJI until '
                  'a person has looked at it.', len(ids), args.stop_above)
        return EXIT_MANIFEST_TOO_LARGE
    return EXIT_OK


if __name__ == '__main__':
    sys.exit(main())
