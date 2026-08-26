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
import hashlib
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# [REASON]: декодер маршрутов НЕ импортируется на загрузке модуля.
# Режим --request-body разбирает только тело POST-запроса и в декодере не
# нуждается вовсе; из-за верхнеуровневого импорта он не работал там, где
# `drone_collector` не установлен -- например на машине владельца, куда
# приехал один текстовый файл и ничего больше. Импорт перенесён внутрь
# функции разбора capture, которой декодер действительно нужен.


def _load_route_decoder():
    """Ленивая загрузка декодера. Нужна только режиму --capture."""
    from drone_collector.route_decode import (
        RouteDecodeError, decode_route_response, implied_work_length_m,
        path_length_m)
    return (RouteDecodeError, decode_route_response, implied_work_length_m,
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
# Подписи английские: они уходят в консоль Windows, где кириллица по правилу
# устава не печатается, а «?????» вместо вопроса делает вывод бесполезным.
REQUEST_BODY_QUESTIONS = (
    ('flight ids', ('ids', 'flight_ids', 'record_ids',
                    'flight_record_ids', 'id_list')),
    ('period', ('start', 'end', 'from', 'to', 'begin_time', 'end_time',
                'timestamp_gteq', 'timestamp_lteq', 'date')),
    ('device', ('device', 'device_id', 'drone', 'drone_id', 'product_sn',
                'sn')),
    ('map parameters', ('bbox', 'bounds', 'zoom', 'viewport', 'level',
                        'north', 'south', 'east', 'west')),
    ('result count limit', ('limit', 'page_size', 'size', 'count',
                            'per_page', 'page')),
    # data_type подтверждён на настоящем теле запроса 2026-08-26. Значение
    # НЕ печатается -- только сам факт наличия ключа.
    ('response detail level', ('data_type', 'dataType', 'detail_type')),
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
    (RouteDecodeError, decode_route_response, implied_work_length_m,
     path_length_m) = _load_route_decoder()
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


def write_geojson(decoded_list, path):  # noqa: D401
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


def _collect_keys(node, prefix, seen, lines, depth=0):  # noqa: C901
    """Обход структуры: имена ключей, типы, размеры коллекций. Без значений.

    [REASON]: значения тела запроса не печатаются ВООБЩЕ -- ни строковые, ни
    числовые. Нам нужна форма запроса, а не идентификаторы вылетов; печатать
    «на всякий случай» -- ровно тот способ, которым утекают подписи и внутренние
    номера. Размер коллекции печатается: это структура, а не значение.
    """
    if depth > MAX_STRUCTURE_DEPTH:
        lines.append('  %-44s ... deeper levels not walked' % prefix)
        return
    if isinstance(node, dict):
        for key in sorted(node, key=str):
            value = node[key]
            name = '%s.%s' % (prefix, key) if prefix else str(key)
            seen.add(str(key))
            if isinstance(value, dict):
                lines.append('  %-44s object(%d keys)' % (name, len(value)))
                _collect_keys(value, name, seen, lines, depth + 1)
            elif isinstance(value, list):
                kinds = sorted({type(item).__name__ for item in value})
                lines.append('  %-44s array(%d) of %s'
                             % (name, len(value),
                                ', '.join(kinds) if kinds else 'empty'))
                for item in value[:1]:
                    if isinstance(item, (dict, list)):
                        _collect_keys(item, name + '[0]', seen, lines,
                                      depth + 1)
            elif value is None:
                lines.append('  %-44s null' % name)
            elif isinstance(value, bool):
                lines.append('  %-44s boolean' % name)
            elif isinstance(value, (int, float)):
                lines.append('  %-44s number' % name)
            else:
                lines.append('  %-44s string(len=%d)' % (name, len(str(value))))
    elif isinstance(node, list):
        kinds = sorted({type(item).__name__ for item in node})
        lines.append('  %-44s array(%d) of %s'
                     % (prefix or '(root)', len(node),
                        ', '.join(kinds) if kinds else 'empty'))
        for item in node[:1]:
            if isinstance(item, (dict, list)):
                _collect_keys(item, (prefix or '') + '[0]', seen, lines,
                              depth + 1)


# Предел размера файла тела запроса.
#
# [REASON]: тело POST-запроса маршрутов -- это список идентификаторов и пара
# полей; настоящее тело из снимка занимает 296 байт. Мегабайт с запасом
# покрывает пакет в десятки тысяч ID и при этом не даёт разобрать
# произвольный большой файл, поданный по ошибке или намеренно.
MAX_REQUEST_BODY_BYTES = 1024 * 1024

# Максимальная глубина вложенности, которую разрешено обходить.
#
# [REASON]: `bodyText` -- это JSON внутри JSON. Ограничение глубины отсекает
# как случайную рекурсию, так и намеренно глубокий документ: разбор такого
# съедает стек и время, а структуры запроса всё равно не показывает.
MAX_STRUCTURE_DEPTH = 12

# Ключи внешнего безопасного конверта, который формирует владелец из DevTools.
ENVELOPE_REQUIRED_KEYS = ('method', 'urlPath', 'mimeType', 'bodyText')


def _is_safe_envelope(document):
    """True, когда документ похож на безопасный конверт с bodyText."""
    return (isinstance(document, dict)
            and all(key in document for key in ENVELOPE_REQUIRED_KEYS)
            and isinstance(document.get('bodyText'), str))


def describe_request_body(path):
    """Что лежит в сохранённом теле POST-запроса маршрутов (вопрос В1).

    Печатает ТОЛЬКО структуру: имена ключей, типы, размеры коллекций. Ни одно
    значение -- ни строка, ни число -- в вывод не попадает. Тело запроса
    никуда не сохраняется: ни в отчёт, ни в фикстуры, ни в лог.
    """
    size = os.path.getsize(path)
    if size > MAX_REQUEST_BODY_BYTES:
        print('REFUSING: the file is %d bytes, the cap is %d.'
              % (size, MAX_REQUEST_BODY_BYTES))
        print('A route request body is a list of ids and two fields; a file')
        print('this large is not one, and nothing was read.')
        return 1

    with open(path, 'rb') as handle:
        blob = handle.read()
    text = blob.decode('utf-8-sig', errors='replace')
    found = sorted({marker for marker in SECRET_MARKERS
                    if marker.lower() in text.lower()})
    if found:
        print('REFUSING: the file contains %s.' % ', '.join(found))
        print('Save the request PAYLOAD only, never the headers.')
        return 3

    digest = hashlib.sha256(blob).hexdigest()

    try:
        document = json.loads(text)
    except ValueError:
        # [REASON]: первые байты не печатаются. У не-JSON тела содержимое
        # неизвестно по определению, и «показать начало» -- это показать
        # неизвестно что. Размера, хеша и предположения о типе достаточно,
        # чтобы назвать формат и запросить следующий шаг.
        print('The body is not JSON.')
        print('  bytes  : %d' % len(blob))
        print('  sha256 : %s' % digest)
        print('  guess  : %s' % _guess_binary_kind(blob))
        print('Report the format; do not guess a schema. Nothing was printed')
        print('from the content itself.')
        return 1

    envelope = None
    if _is_safe_envelope(document):
        # Безопасный конверт из DevTools: снаружи метод, путь и MIME, а само
        # тело лежит СТРОКОЙ в bodyText. Прежняя версия разбирала только
        # внешние ключи и о содержимом запроса не говорила ничего.
        envelope = document
        print('Safe envelope recognised.')
        print('  method    : %s' % envelope.get('method'))
        print('  urlPath   : %s' % envelope.get('urlPath'))
        print('  mimeType  : %s' % envelope.get('mimeType'))
        print('  bodyText  : string(len=%d)' % len(envelope['bodyText']))
        if len(envelope['bodyText']) > MAX_REQUEST_BODY_BYTES:
            print('REFUSING: bodyText is larger than the cap.')
            return 1
        try:
            document = json.loads(envelope['bodyText'])
        except ValueError as exc:
            print('REFUSING: bodyText is not valid JSON (%s).'
                  % type(exc).__name__)
            print('  bodyText sha256 : %s'
                  % hashlib.sha256(
                      envelope['bodyText'].encode('utf-8')).hexdigest())
            print('Nothing from the content itself was printed.')
            return 1
        if not isinstance(document, (dict, list)):
            print('REFUSING: bodyText decodes to %s, not to an object or an '
                  'array.' % type(document).__name__)
            return 1
        print('  bodyText parsed as JSON.')

    print('' if envelope else 'Request body parsed as JSON.')
    if not envelope:
        print('  bytes  : %d' % len(blob))
    print('  sha256 : %s' % digest)
    print('')
    print('Structure (names, types and sizes only -- no values):')

    seen = set()
    lines = []
    _collect_keys(document, '', seen, lines)
    for line in lines:
        print(line)

    print('')
    print('What the body appears to carry (question B1):')
    lowered = {key.lower() for key in seen}
    for question, keys in REQUEST_BODY_QUESTIONS:
        hits = sorted({key for key in keys if key.lower() in lowered})
        print('  %-28s %s' % (question,
                              ', '.join(hits) if hits else 'not found'))
    print('')
    print('A key being present names a CANDIDATE, not a proven meaning.')
    print('Confirm each one by changing it in the cabinet and watching the')
    print('response change -- never by the name alone.')
    return 0


def _guess_binary_kind(blob):
    """Предположение о типе двоичного тела по сигнатуре. Без вывода содержимого."""
    if blob[:4] == b'PK\x03\x04':
        return 'ZIP container'
    if blob[:2] == b'\x1f\x8b':
        return 'gzip stream'
    if blob[:1] in (b'<',):
        return 'XML or HTML'
    try:
        blob.decode('utf-8')
    except UnicodeDecodeError:
        return 'binary, not valid UTF-8'
    return 'text, but not JSON'


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
