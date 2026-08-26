# -*- coding: utf-8 -*-
"""Разбор бинарных маршрутов DJI из сохранённого сетевого снимка. Только чтение.

    python tools/drone_route_probe.py --capture DJI_2026-06-05_safe.json
    python tools/drone_route_probe.py --capture snapshot.json --geojson routes.json
    python tools/drone_route_probe.py --request-body body.json

Зачем. Кабинет SmartFarm в режиме карты забирает маршруты вылетов запросом

    POST https://kr-ag2-api.dji.com/api/web/v2/flight_datas/flight_records

и получает `application/octet-stream` -- protobuf без схемы. Разбор этого
ответа записан в `drone_collector/route_decode.py`; этот скрипт применяет его
к файлу, который снял владелец, и печатает то, что из ответа следует.

Скрипт НИЧЕГО не пишет в базу и НИКУДА не ходит по сети. Он не открывает
`instance/transport.db` вовсе: сравнивать пока не с чем, разбор самодостаточен.

БЕЗОПАСНОСТЬ. Скрипт проверяет входной файл на остатки секретов и говорит об
этом первой строкой. Снимок «обезличен» не значит «безопасен»: ответ GraphQL
`lands` несёт `geometry.storage.signedURL` -- подписанную ссылку на полигон
поля -- в ТЕЛЕ ответа, а не в заголовке, и очистка заголовков её не трогает.
Сам скрипт ни одной такой ссылки не печатает и никуда не сохраняет.

В консоль -- только ASCII (кодировка консоли Windows). Кириллица уходит
только в файл --geojson, и то лишь как название машины.
"""

import argparse
import base64
import binascii
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from drone_collector.route_decode import (  # noqa: E402
    RouteDecodeError, decode_route_response, implied_work_length_m,
    path_length_m)

# Маркер эндпоинта маршрутов. Совпадение по имени, а не по версии API: смена
# v2 на v3 не должна превращать прогон в молчаливый "маршрутов не найдено".
ROUTE_URL_MARKER = 'flight_datas/flight_records'

# Ключи, наличие которых в файле означает утечку. Проверяются по СЫРОМУ тексту
# файла, а не по разобранному JSON: ключ может лежать где угодно в дереве.
# [REASON]: маркеры выбраны так, чтобы не кричать на само ОПИСАНИЕ очистки.
# Файл владельца начинается словами "request headers, cookies ... removed", и
# проверка на голое слово "cookie" срабатывала бы на нём каждый раз. Проверка,
# которая всегда говорит "секрет найден", секретов не находит.
SECRET_MARKERS = ('signedURL', 'OSSAccessKeyId', 'Signature=', 'set-cookie',
                  '"cookies"', 'x-auth-token', 'storage_state', 'bearer ')

# Ключи тела POST-запроса маршрутов, о которых спрашивает вопрос В1
# (`docs/DRONE_COVERAGE_001_DISCOVERY.md` §9). Список -- это то, ЧТО МЫ ИЩЕМ,
# а не то, что мы утверждаем: назначение любого найденного ключа
# подтверждается только сверкой с тем, что вернул ответ.
REQUEST_BODY_QUESTIONS = (
    ('идентификаторы вылетов', ('ids', 'flight_ids', 'record_ids',
                                'flight_record_ids', 'id_list')),
    ('период', ('start', 'end', 'from', 'to', 'begin_time', 'end_time',
                'timestamp_gteq', 'timestamp_lteq', 'date')),
    ('устройство', ('device', 'device_id', 'drone', 'drone_id', 'product_sn',
                    'sn')),
    ('параметры карты', ('bbox', 'bounds', 'zoom', 'viewport', 'level',
                         'north', 'south', 'east', 'west')),
    ('ограничение количества', ('limit', 'page_size', 'size', 'count',
                                'per_page', 'page')),
)


def read_capture(path):
    """(список тел ответов маршрутов, найденные маркеры секретов).

    Понимает два вида входа: снимок сети в виде JSON со списком `entries` и
    сырой бинарный ответ, сохранённый в файл.
    """
    with open(path, 'rb') as handle:
        blob = handle.read()

    text = blob.decode('utf-8-sig', errors='replace')
    found = sorted({marker for marker in SECRET_MARKERS
                    if marker.lower() in text.lower()})

    try:
        document = json.loads(text)
    except ValueError:
        # Не JSON -- считаем файл сырым телом ответа.
        return [(os.path.basename(path), blob)], found

    entries = document.get('entries')
    if not isinstance(entries, list):
        raise SystemExit('ERROR: no "entries" list in %s' % path)

    bodies = []
    for index, entry in enumerate(entries):
        url = entry.get('url') or ''
        if ROUTE_URL_MARKER not in url:
            continue
        body = entry.get('responseBody')
        if not body:
            print('WARNING: entry %d has no responseBody, skipped' % index)
            continue
        encoding = (entry.get('encoding') or '').lower()
        if encoding == 'base64':
            try:
                raw = base64.b64decode(body)
            except (binascii.Error, ValueError) as exc:
                print('WARNING: entry %d is not valid base64 (%s), skipped'
                      % (index, exc))
                continue
        else:
            raw = body.encode('utf-8') if isinstance(body, str) else body
        bodies.append(('entry %d' % index, raw))
    return bodies, found


def dedupe(bodies):
    """[(метка, тело)] -> [(метка, тело, сколько раз встретилось)].

    Один и тот же ответ приходит в снимке несколько раз: карта перезапрашивает
    маршруты при каждом изменении вида. Хеш ответа -- готовый ключ
    дедупликации, и он же доказывает, что повторы БАЙТ В БАЙТ одинаковы, а не
    просто похожи.
    """
    import hashlib
    order = []
    seen = {}
    for label, raw in bodies:
        digest = hashlib.sha256(raw).hexdigest()
        if digest in seen:
            seen[digest][2] += 1
            continue
        seen[digest] = [label, raw, 1]
        order.append(digest)
    return [(seen[d][0], seen[d][1], seen[d][2], d) for d in order]


def report(raw, label, digest, repeats):
    try:
        decoded = decode_route_response(raw)
    except RouteDecodeError as exc:
        print('  DECODE FAILED: %s' % exc)
        return None

    print('  status=%s message=%r routes=%d points=%d'
          % (decoded.status, decoded.message, len(decoded.routes),
             decoded.point_count))
    if not decoded.is_ok:
        print('  in-body status is not 200 -- no route data expected')
        return decoded

    header = ('    %-11s %5s %10s %7s %9s %10s %9s  %s'
              % ('flight_id', 'pts', 'DJI m2', 'width', 'path m',
                 'needs m', 'ratio', 'note'))
    print(header)
    for route in decoded.routes:
        length = path_length_m(route.points)
        implied = implied_work_length_m(route)
        note = ''
        if not route.spray_width_known:
            note = 'no width in payload'
            width_text = '-'
            implied_text = '-'
            ratio_text = '-'
        else:
            width_text = '%.2f' % route.spray_width_m
            implied_text = '%.1f' % implied
            ratio_text = '%.3f' % (implied / length) if length > 0 else '-'
            if length > 0 and implied > length:
                note = 'AREA NEEDS MORE PATH THAN FLOWN'
        if length > 0 and route.work_area_m2:
            needed_width = route.work_area_m2 / length
            if not route.spray_width_known:
                note = 'width absent; area would need a %.1f m swath' % needed_width
        print('    %-11s %5d %10.1f %7s %9.1f %10s %9s  %s'
              % (route.flight_id, len(route.points), route.work_area_m2 or 0.0,
                 width_text, length, implied_text, ratio_text, note))

    total_area = sum(r.work_area_m2 or 0.0 for r in decoded.routes)
    print('    total DJI area: %.1f m2 = %.4f ha' % (total_area, total_area / 10000.0))
    print('    sha256=%s seen %d time(s) in the capture' % (digest[:16], repeats))
    return decoded


def write_geojson(decoded_list, path):
    """LineString на вылет плюс точка взлёта. Для просмотра, не для расчёта."""
    features = []
    for decoded in decoded_list:
        if decoded is None:
            continue
        for route in decoded.routes:
            if len(route.points) < 2:
                continue
            features.append({
                'type': 'Feature',
                'geometry': {
                    'type': 'LineString',
                    # GeoJSON -- это longitude, latitude. Порядок обратный
                    # тому, в котором точки лежат в ответе DJI.
                    'coordinates': [[lng, lat] for lat, lng in route.points],
                },
                'properties': {
                    'dji_flight_id': route.flight_id,
                    'nickname': route.nickname,
                    'hardware_id': route.hardware_id,
                    'dji_area_m2': route.work_area_m2,
                    'spray_width_m': (route.spray_width_m
                                      if route.spray_width_known else None),
                    'start_ms': route.start_ms,
                    'end_ms': route.end_ms,
                    'mode_name': route.mode_name,
                    'points': len(route.points),
                },
            })
    with open(path, 'w', encoding='utf-8') as handle:
        json.dump({'type': 'FeatureCollection', 'features': features},
                  handle, ensure_ascii=False, indent=1)
    print('GeoJSON written: %s (%d features)' % (path, len(features)))


def describe_request_body(path):
    """Что лежит в сохранённом теле POST-запроса маршрутов (вопрос В1).

    Печатает ТОЛЬКО структуру: имена ключей, типы, размеры списков и -- для
    чисел -- их значения. Строковые значения не печатаются вовсе: в теле
    запроса может оказаться то, чего мы не ждём, и «показать на всякий
    случай» -- ровно тот способ, которым утекают подписи.
    """
    with open(path, 'rb') as handle:
        blob = handle.read()
    text = blob.decode('utf-8-sig', errors='replace')
    found = sorted({marker for marker in SECRET_MARKERS
                    if marker.lower() in text.lower()})
    if found:
        print('REFUSING: the file contains %s.' % ', '.join(found))
        print('Save the request PAYLOAD only, never the headers.')
        return 3

    try:
        document = json.loads(text)
    except ValueError:
        print('The body is not JSON. First 40 bytes as hex:')
        print('  %s' % blob[:40].hex())
        print('Report the format; do not guess a schema.')
        return 1

    print('Request body parsed as JSON.')
    print('')

    def walk(node, prefix=''):
        if isinstance(node, dict):
            for key in sorted(node):
                value = node[key]
                name = '%s.%s' % (prefix, key) if prefix else key
                if isinstance(value, (dict, list)):
                    print('  %-40s %s(%d)' % (name, type(value).__name__,
                                              len(value)))
                    walk(value, name)
                elif isinstance(value, bool) or value is None:
                    print('  %-40s %s' % (name, value))
                elif isinstance(value, (int, float)):
                    print('  %-40s %s' % (name, value))
                else:
                    print('  %-40s str(len=%d)  [value not printed]'
                          % (name, len(str(value))))
        elif isinstance(node, list) and node:
            kinds = sorted({type(item).__name__ for item in node})
            print('  %-40s items: %s' % (prefix + '[]', ', '.join(kinds)))
            if isinstance(node[0], (dict, list)):
                walk(node[0], prefix + '[0]')

    walk(document)

    flat = json.dumps(document, ensure_ascii=False).lower()
    print('')
    print('What the body appears to carry (question B1):')
    for question, keys in REQUEST_BODY_QUESTIONS:
        hits = sorted({key for key in keys if ('"%s"' % key) in flat})
        print('  %-28s %s' % (
            question.encode('ascii', 'replace').decode('ascii'),
            ', '.join(hits) if hits else 'not found'))
    print('')
    print('A key being present names a CANDIDATE, not a proven meaning.')
    print('Confirm each one by changing it in the cabinet and watching the')
    print('response change -- never by the name alone.')
    return 0


def main(argv=None):
    parser = argparse.ArgumentParser(
        description='Decode DJI flight-route responses from a saved capture. '
                    'Read only: no network, no browser, no database.')
    parser.add_argument('--capture', default=None,
                        help='sanitised network capture (JSON with entries) '
                             'or a raw response body')
    parser.add_argument('--request-body', default=None,
                        help='a saved POST request body (payload only, never '
                             'headers) -- describes its structure for '
                             'question B1')
    parser.add_argument('--geojson', default=None,
                        help='optional output file for the decoded routes')
    args = parser.parse_args(argv)

    if args.request_body:
        if not os.path.exists(args.request_body):
            raise SystemExit('ERROR: file not found: %s' % args.request_body)
        return describe_request_body(args.request_body)

    if not args.capture:
        raise SystemExit('ERROR: pass --capture or --request-body')
    if not os.path.exists(args.capture):
        raise SystemExit('ERROR: file not found: %s' % args.capture)

    bodies, secrets = read_capture(args.capture)

    if secrets:
        print('=' * 72)
        print('SECURITY WARNING: this capture still contains %s.' % ', '.join(secrets))
        print('A signed URL is a credential: it grants read access to the field')
        print('geometry until it expires. Do NOT commit this file, do NOT paste')
        print('it into a chat and do NOT attach it to a report. Nothing below')
        print('prints or stores it.')
        print('=' * 72)
    else:
        print('No secret markers found in the capture.')

    if not bodies:
        print('No %s responses in the capture.' % ROUTE_URL_MARKER)
        return 0

    unique = dedupe(bodies)
    print('Route responses: %d in the file, %d distinct by sha256.'
          % (len(bodies), len(unique)))

    decoded_list = []
    for label, raw, repeats, digest in unique:
        print('')
        print('%s -- %d bytes' % (label, len(raw)))
        decoded_list.append(report(raw, label, digest, repeats))

    if args.geojson:
        write_geojson(decoded_list, args.geojson)
    return 0


if __name__ == '__main__':
    sys.exit(main())
