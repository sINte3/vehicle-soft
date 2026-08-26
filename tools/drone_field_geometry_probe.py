# -*- coding: utf-8 -*-
"""DRONE-COVERAGE-001, этап A3: определение формата полигона поля DJI.

    python tools/drone_field_geometry_probe.py --file karvon_geometry.bin
    python tools/drone_field_geometry_probe.py --file karvon.bin ^
        --expect-md5 caba64102d3ab796434e599348a5738f --expect-area-mu 105.6552 ^
        --geojson karvon.geojson

Отвечает на вопрос В2 из `docs/DRONE_COVERAGE_001_DISCOVERY.md` §9: что
фактически лежит по ссылке `geometry.storage.signedURL` в ответе `lands`.

ФОРМАТ НЕ ПРЕДПОЛАГАЕТСЯ. Скрипт определяет его по содержимому файла --
GeoJSON, KML, ZIP/KMZ, protobuf-подобный бинарный объект или неизвестное, --
и НЕ выдаёт догадку за ответ: если распознать не удалось, так и написано.

БЕЗ СЕТИ. Скрипт ничего не скачивает. Подписанная ссылка -- это временное
учётное свидетельство: её нельзя коммитить, печатать, писать в лог, класть в
отчёт или отправлять в PR. Владелец скачивает файл сам, своим браузером, и
присылает ТОЛЬКО содержимое. Порядок -- в §9 DISCOVERY, вопрос В2.

Скрипт проверяет, что переданный файл сам не содержит остатков ссылки, и
отказывается работать, если содержит: файл с подписью внутри нельзя ни
приложить к отчёту, ни положить в фикстуры.

Вывод в консоль -- только ASCII. Разбор геометрии уходит в --geojson.
"""

import argparse
import hashlib
import json
import math
import os
import re
import sys

# Маркеры остатков подписанной ссылки. Проверяются по сырому тексту файла.
SECRET_MARKERS = ('signedURL', 'OSSAccessKeyId', 'Signature=', 'X-Amz-Signature',
                  'set-cookie', 'x-auth-token', 'storage_state', 'bearer ')

# Сигнатуры форматов. Порядок важен: ZIP проверяется раньше текста, потому что
# KMZ -- это ZIP, внутри которого лежит KML.
ZIP_MAGIC = b'PK\x03\x04'
GZIP_MAGIC = b'\x1f\x8b'

# 1 гектар = 15 му ровно. Установлено на живых ответах DJI и записано в
# docs/tracks/drones.md §3. Нужно, чтобы сверить площадь полигона с totalArea.
MU_PER_HECTARE = 15.0

# Допуск сверки площади с `totalArea` DJI, проценты.
#
# [REASON]: это ТЕХНИЧЕСКИЙ допуск проверки формата, а НЕ допуск коммерческого
# расчёта. Он отвечает на вопрос «мы прочитали файл правильно?», и один процент
# для этого с запасом: разбор в другой системе координат или с перепутанным
# порядком координат промахивается в разы, а не на проценты. К тому, какое
# расхождение приемлемо в учёте гектаров, это число отношения не имеет.
DEFAULT_AREA_TOLERANCE_PERCENT = 1.0

EXIT_OK = 0
EXIT_UNKNOWN_FORMAT = 1
EXIT_NO_FILE = 2
EXIT_SECRET_FOUND = 3
EXIT_VALIDATION_FAILED = 4


def sha256_of(path):
    digest = hashlib.sha256()
    with open(path, 'rb') as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b''):
            digest.update(chunk)
    return digest.hexdigest()


def md5_of(path):
    """MD5 файла -- для сверки с `geometry.storage.contentMd5` от DJI.

    [REASON]: MD5 здесь НЕ криптография, а сверка с чужим полем. DJI
    публикует contentMd5, и совпадение доказывает, что скачан именно тот
    объект, на который ссылался справочник. Собственная целостность считается
    в sha256 рядом.
    """
    digest = hashlib.md5()
    with open(path, 'rb') as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b''):
            digest.update(chunk)
    return digest.hexdigest()


def find_secret_markers(blob):
    text = blob.decode('utf-8', errors='replace').lower()
    return sorted({marker for marker in SECRET_MARKERS
                   if marker.lower() in text})


# ─── Определение формата ─────────────────────────────────────────────────────

def detect_format(blob):
    """Формат по содержимому. Возвращает (код, пояснение).

    Коды: GEOJSON, JSON_OTHER, KML, KMZ, GZIP, PROTOBUF_LIKE, UNKNOWN.
    """
    if blob[:4] == ZIP_MAGIC:
        return 'KMZ', 'ZIP-контейнер (KMZ обычно содержит doc.kml)'
    if blob[:2] == GZIP_MAGIC:
        return 'GZIP', 'gzip-поток, внутри может быть что угодно'

    head = blob[:4096].lstrip()
    if head[:1] in (b'{', b'['):
        try:
            document = json.loads(blob.decode('utf-8'))
        except (UnicodeDecodeError, ValueError):
            return 'UNKNOWN', 'начинается как JSON, но не разбирается'
        if isinstance(document, dict) and document.get('type') in (
                'FeatureCollection', 'Feature', 'Polygon', 'MultiPolygon',
                'GeometryCollection'):
            return 'GEOJSON', 'GeoJSON, тип %s' % document.get('type')
        return 'JSON_OTHER', 'JSON, но не GeoJSON по полю type'

    lowered = head.lower()
    if b'<kml' in lowered or b'<?xml' in lowered and b'kml' in lowered:
        return 'KML', 'XML с корнем kml'
    if lowered[:5] == b'<?xml':
        return 'UNKNOWN', 'XML, но не KML'

    # Protobuf wire format: первый байт -- тег. Разбор на пробу, без схемы.
    try:
        fields = _walk_protobuf(blob)
    except ValueError:
        fields = None
    if fields:
        return 'PROTOBUF_LIKE', ('разбирается как protobuf wire format, '
                                 'поля верхнего уровня: %s'
                                 % ', '.join(str(number)
                                             for number, _w, _v in fields))
    return 'UNKNOWN', 'ни одна известная сигнатура не подошла'


def _read_varint(buf, pos, end):
    result = 0
    shift = 0
    while True:
        if pos >= end:
            raise ValueError('varint truncated')
        byte = buf[pos]
        pos += 1
        result |= (byte & 0x7F) << shift
        if not byte & 0x80:
            return result, pos
        shift += 7
        if shift > 63:
            raise ValueError('varint too long')


def _walk_protobuf(buf):
    """Пробный разбор как protobuf. Тот же приём, что в route_decode.walk.

    Здесь он нужен только чтобы ОТЛИЧИТЬ формат, а не чтобы прочитать
    геометрию: без схемы координаты из неизвестного сообщения доставать
    нельзя, и скрипт этого не делает.
    """
    pos, end, fields = 0, len(buf), []
    while pos < end:
        key, pos = _read_varint(buf, pos, end)
        number, wire = key >> 3, key & 7
        if number == 0:
            raise ValueError('field 0')
        if wire == 0:
            value, pos = _read_varint(buf, pos, end)
        elif wire == 1:
            if pos + 8 > end:
                raise ValueError('fixed64 truncated')
            value, pos = buf[pos:pos + 8], pos + 8
        elif wire == 2:
            length, pos = _read_varint(buf, pos, end)
            if pos + length > end:
                raise ValueError('bytes truncated')
            value, pos = buf[pos:pos + length], pos + length
        elif wire == 5:
            if pos + 4 > end:
                raise ValueError('fixed32 truncated')
            value, pos = buf[pos:pos + 4], pos + 4
        else:
            raise ValueError('wire type %d' % wire)
        fields.append((number, wire, value))
    return fields


# ─── Разбор геометрии ────────────────────────────────────────────────────────

def rings_from_geojson(document):
    """Все внешние и внутренние кольца документа GeoJSON.

    Возвращает список (тип, внешнее кольцо, [внутренние кольца]).
    """
    shapes = []

    def take(geometry):
        if not isinstance(geometry, dict):
            return
        kind = geometry.get('type')
        coordinates = geometry.get('coordinates')
        if kind == 'Polygon' and isinstance(coordinates, list) and coordinates:
            shapes.append(('Polygon', coordinates[0], coordinates[1:]))
        elif kind == 'MultiPolygon' and isinstance(coordinates, list):
            for polygon in coordinates:
                if polygon:
                    shapes.append(('MultiPolygon', polygon[0], polygon[1:]))
        elif kind == 'GeometryCollection':
            for child in geometry.get('geometries') or []:
                take(child)

    if document.get('type') == 'FeatureCollection':
        for feature in document.get('features') or []:
            take((feature or {}).get('geometry'))
    elif document.get('type') == 'Feature':
        take(document.get('geometry'))
    else:
        take(document)
    return shapes


def rings_from_kml(text):
    """Кольца из KML. Координаты KML -- «lon,lat[,alt]» через пробел."""
    shapes = []
    for match in re.finditer(
            r'<Polygon\b.*?</Polygon>', text, re.S | re.I):
        block = match.group(0)
        outer = re.search(
            r'<outerBoundaryIs\b.*?<coordinates>(.*?)</coordinates>',
            block, re.S | re.I)
        inners = re.findall(
            r'<innerBoundaryIs\b.*?<coordinates>(.*?)</coordinates>',
            block, re.S | re.I)
        if not outer:
            continue
        shapes.append(('Polygon', _kml_ring(outer.group(1)),
                       [_kml_ring(text_) for text_ in inners]))
    return shapes


def _kml_ring(text):
    ring = []
    for token in text.split():
        parts = token.split(',')
        if len(parts) < 2:
            continue
        try:
            ring.append([float(parts[0]), float(parts[1])])
        except ValueError:
            continue
    return ring


def ring_area_m2(ring):
    """Площадь кольца на сфере, м2. Знак говорит о направлении обхода.

    Формула сферического многоугольника (сумма по рёбрам). Для поля в
    несколько гектаров совпадает с плоским расчётом до долей процента, а
    зависимости от проекции не имеет вовсе. Проверено на рамке контура
    `Karvon`: 13.5011 га против 13.5011 га у независимого расчёта через
    равнопромежуточную проекцию.
    """
    if len(ring) < 3:
        return 0.0
    radius = 6378137.0
    total = 0.0
    for index in range(len(ring)):
        lon1, lat1 = ring[index][0], ring[index][1]
        lon2, lat2 = ring[(index + 1) % len(ring)][0], ring[(index + 1) % len(ring)][1]
        total += (math.radians(lon2 - lon1)
                  * (2 + math.sin(math.radians(lat1))
                     + math.sin(math.radians(lat2))))
    return total * radius * radius / 2.0


def coordinate_problems(ring):
    """Список претензий к координатам кольца. Пустой список -- всё в порядке.

    [REASON]: проверяется КАЖДАЯ вершина, а не первая и последняя. Одна
    нечисловая или бесконечная координата посреди контура даёт NaN в площади,
    и дальше все сравнения с `totalArea` становятся ложными молча -- NaN не
    равен ничему, включая себя.
    """
    problems = []
    for index, point in enumerate(ring):
        if not isinstance(point, (list, tuple)) or len(point) < 2:
            problems.append('вершина %d не пара координат' % index)
            continue
        lon, lat = point[0], point[1]
        for name, value in (('долгота', lon), ('широта', lat)):
            if isinstance(value, bool) or not isinstance(value, (int, float)):
                problems.append('вершина %d: %s не число' % (index, name))
            elif not math.isfinite(value):
                problems.append('вершина %d: %s не конечна' % (index, name))
        if _finite(lon) and not -180.0 <= lon <= 180.0:
            problems.append('вершина %d: долгота %s вне [-180, 180]'
                            % (index, lon))
        if _finite(lat) and not -90.0 <= lat <= 90.0:
            problems.append('вершина %d: широта %s вне [-90, 90]'
                            % (index, lat))
        if len(problems) > 20:
            problems.append('... и другие')
            return problems
    return problems


def _finite(value):
    return (not isinstance(value, bool) and isinstance(value, (int, float))
            and math.isfinite(value))


def distinct_vertex_count(ring):
    """Число различных вершин. Замыкающая повторная вершина не считается."""
    return len({(point[0], point[1]) for point in ring
                if isinstance(point, (list, tuple)) and len(point) >= 2
                and _finite(point[0]) and _finite(point[1])})


def segments_intersect(a, b, c, d):
    def cross(o, p, q):
        return ((p[0] - o[0]) * (q[1] - o[1])
                - (p[1] - o[1]) * (q[0] - o[0]))
    d1, d2 = cross(c, d, a), cross(c, d, b)
    d3, d4 = cross(a, b, c), cross(a, b, d)
    return ((d1 > 0) != (d2 > 0)) and ((d3 > 0) != (d4 > 0))


def ring_self_intersects(ring):
    """Грубая проверка самопересечения. O(n^2), для контура поля достаточно.

    Работает по ЗАМКНУТОМУ кольцу: если последняя вершина не повторяет первую,
    замыкающее ребро добавляется здесь. Иначе самопересечение с участием этого
    ребра осталось бы незамеченным -- а незамкнутое кольцо валидатор всё равно
    отвергает раньше, так что это страховка, а не основной путь.
    """
    points = [point for point in ring
              if isinstance(point, (list, tuple)) and len(point) >= 2
              and _finite(point[0]) and _finite(point[1])]
    if len(points) < 3:
        return False
    if points[0] != points[-1]:
        points.append(points[0])
    count = len(points)
    for i in range(count - 1):
        for j in range(i + 2, count - 1):
            if i == 0 and j == count - 2:
                continue          # смежные через замыкание
            if segments_intersect(points[i], points[i + 1],
                                  points[j], points[j + 1]):
                return True
    return False


def describe_shapes(shapes):
    report = []
    for kind, outer, inners in shapes:
        closed = (len(outer) > 2 and outer[0] == outer[-1])
        problems = coordinate_problems(outer)
        for inner in inners:
            problems.extend(coordinate_problems(inner))
        area = abs(ring_area_m2(outer)) if not problems else 0.0
        holes = [abs(ring_area_m2(inner)) for inner in inners] \
            if not problems else []
        lons = [point[0] for point in outer if _finite(point[0])]
        lats = [point[1] for point in outer if _finite(point[1])]
        report.append({
            'type': kind,
            'points': len(outer),
            'closed': closed,
            'inner_rings_closed': all(
                len(inner) > 2 and inner[0] == inner[-1] for inner in inners),
            'distinct_vertices': distinct_vertex_count(outer),
            'clockwise': (ring_area_m2(outer) < 0) if not problems else None,
            'self_intersects': ring_self_intersects(outer),
            'inner_self_intersects': any(ring_self_intersects(inner)
                                         for inner in inners),
            'coordinate_problems': problems,
            'area_m2': area,
            'area_ha': area / 10000.0,
            'holes': len(inners),
            'holes_area_ha': sum(holes) / 10000.0,
            'lon_range': (min(lons), max(lons)) if lons else (None, None),
            'lat_range': (min(lats), max(lats)) if lats else (None, None),
        })
    return report


def looks_like_wgs84(shapes):
    """Похожи ли координаты на градусы WGS84 в порядке longitude, latitude.

    [REASON]: перепутанный порядок -- самая частая и самая тихая ошибка в
    геоданных. Для Бухарской области долгота около 64.6, широта около 40.1;
    обе в диапазоне градусов, поэтому перестановка не выйдет за границы и
    молча даст поле в другом полушарии. Здесь проверяется только то, что можно
    проверить без внешнего знания: обе величины в градусах своих диапазонов.
    Совпадение диапазонов НЕ доказывает правильный порядок -- оно лишь
    исключает грубый случай.
    """
    for shape in shapes:
        if shape['coordinate_problems']:
            return False, shape['coordinate_problems'][0]
    return True, 'обе координаты в допустимых диапазонах градусов'


def validate_shapes(shapes, described):
    """Список причин, по которым геометрию нельзя принимать. Пустой -- годна.

    Все проверки здесь ВНУТРЕННИЕ: они не зависят от того, передал ли
    пользователь ожидаемые значения. Сверка с contentMd5 и totalArea живёт
    отдельно -- её нельзя выполнить, не получив ожидание.
    """
    reasons = []
    if not shapes:
        reasons.append('полигон не разобран')
        return reasons
    for index, shape in enumerate(described):
        prefix = 'кольцо %d: ' % index
        if shape['coordinate_problems']:
            reasons.append(prefix + '; '.join(shape['coordinate_problems'][:5]))
            continue
        if not shape['closed']:
            reasons.append(prefix + 'внешнее кольцо не замкнуто')
        if not shape['inner_rings_closed']:
            reasons.append(prefix + 'внутреннее кольцо не замкнуто')
        if shape['distinct_vertices'] < 3:
            reasons.append(prefix + 'меньше трёх различных вершин (%d)'
                           % shape['distinct_vertices'])
        if shape['self_intersects']:
            reasons.append(prefix + 'внешнее кольцо самопересекается')
        if shape['inner_self_intersects']:
            reasons.append(prefix + 'внутреннее кольцо самопересекается')
    total = sum(shape['area_ha'] - shape['holes_area_ha']
                for shape in described)
    if not math.isfinite(total):
        reasons.append('итоговая площадь не конечна')
    elif total <= 0:
        reasons.append('итоговая площадь нулевая или отрицательная (%.6f га)'
                       % total)
    return reasons


# ─── Главное ─────────────────────────────────────────────────────────────────

def analyse_file(path):
    with open(path, 'rb') as handle:
        blob = handle.read()
    secrets = find_secret_markers(blob)
    kind, note = detect_format(blob)
    shapes = []
    if kind == 'GEOJSON':
        shapes = rings_from_geojson(json.loads(blob.decode('utf-8')))
    elif kind == 'KML':
        shapes = rings_from_kml(blob.decode('utf-8', errors='replace'))
    return {
        'bytes': len(blob),
        'secrets': secrets,
        'format': kind,
        'format_note': note,
        'shapes': describe_shapes(shapes),
        'raw_shapes': shapes,
    }


def main(argv=None):
    parser = argparse.ArgumentParser(
        description='DRONE-COVERAGE-001 stage A3: identify and VALIDATE the '
                    'DJI field geometry file. No network, no guessing.')
    parser.add_argument('--file', required=True,
                        help='the downloaded geometry body, WITHOUT the URL')
    parser.add_argument('--expect-md5', default=None,
                        help='geometry.storage.contentMd5 from the lands '
                             'response, to prove the right object was saved')
    parser.add_argument('--expect-area-mu', type=float, default=None,
                        help='totalArea from the lands response, in mu')
    parser.add_argument('--area-tolerance-percent', type=float,
                        default=DEFAULT_AREA_TOLERANCE_PERCENT,
                        help='how far the parsed area may differ from '
                             '--expect-area-mu before the run FAILS. Default '
                             '%.1f%%. This is a technical tolerance of the '
                             'format check, NOT a tolerance of the commercial '
                             'calculation.' % DEFAULT_AREA_TOLERANCE_PERCENT)
    parser.add_argument('--geojson', default=None,
                        help='write the parsed rings as GeoJSON. Written ONLY '
                             'when every validation passed.')
    args = parser.parse_args(argv)

    if not os.path.exists(args.file):
        print('ERROR: file not found: %s' % args.file)
        return EXIT_NO_FILE

    result = analyse_file(args.file)

    print('=' * 72)
    print('A3 field geometry probe')
    print('file   : %s' % args.file)
    print('bytes  : %d' % result['bytes'])
    print('sha256 : %s' % sha256_of(args.file))
    print('md5    : %s' % md5_of(args.file))
    print('=' * 72)

    if result['secrets']:
        print('REFUSING: the file still contains %s.'
              % ', '.join(result['secrets']))
        print('A signed URL is a credential. Save the RESPONSE BODY only --')
        print('DevTools -> Network -> the oss request -> Save response as...')
        print('Nothing was parsed and nothing was written.')
        return EXIT_SECRET_FOUND

    print('format : %s  (%s)' % (result['format'], result['format_note']))

    failures = []

    if args.expect_md5:
        actual = md5_of(args.file)
        if actual.lower() != args.expect_md5.lower().strip():
            failures.append('contentMd5 не совпал: ожидалось %s, получено %s'
                            % (args.expect_md5, actual))
            print('contentMd5 from DJI : MISMATCH')
            print('  a mismatch means this is not the object the directory')
            print('  pointed at, or it was transformed on the way.')
        else:
            print('contentMd5 from DJI : MATCH')

    if not result['shapes']:
        print('')
        print('No polygon rings were parsed.')
        print('That is a RECONNAISSANCE RESULT, not a validated polygon: the')
        print('format is named above and question B2 stays OPEN.')
        if failures:
            print('Additionally: %s' % _ascii(failures[0]))
        print('No GeoJSON was written.')
        return EXIT_UNKNOWN_FORMAT

    described = result['shapes']
    failures.extend(validate_shapes(result['raw_shapes'], described))

    wgs_ok, wgs_note = looks_like_wgs84(described)
    print('')
    print('rings parsed : %d' % len(described))
    print('coordinates  : %s (%s)'
          % ('degrees, lon/lat' if wgs_ok else 'REJECTED', _ascii(wgs_note)))
    total_ha = 0.0
    for index, shape in enumerate(described):
        total_ha += shape['area_ha'] - shape['holes_area_ha']
        print('  ring %d: type=%s points=%d distinct=%d closed=%s holes=%d '
              'self_intersects=%s area=%.4f ha'
              % (index, shape['type'], shape['points'],
                 shape['distinct_vertices'], shape['closed'], shape['holes'],
                 shape['self_intersects'], shape['area_ha']))
    print('total area (rings minus holes) : %.4f ha' % total_ha)

    if args.expect_area_mu is not None:
        expected_ha = args.expect_area_mu / MU_PER_HECTARE
        delta = total_ha - expected_ha
        share = (100.0 * delta / expected_ha) if expected_ha else float('inf')
        print('DJI totalArea  : %.4f mu = %.4f ha' % (args.expect_area_mu,
                                                      expected_ha))
        print('difference     : %+.4f ha (%+.2f%%), tolerance %.2f%%'
              % (delta, share, args.area_tolerance_percent))
        if not math.isfinite(share) or abs(share) > args.area_tolerance_percent:
            failures.append(
                'площадь разошлась с totalArea на %.2f%% при допуске %.2f%%'
                % (share, args.area_tolerance_percent))

    if failures:
        print('')
        print('VALIDATION FAILED (%d):' % len(failures))
        for reason in failures:
            print('  - %s' % _ascii(reason))
        print('No GeoJSON was written. Question B2 stays OPEN.')
        return EXIT_VALIDATION_FAILED

    print('')
    print('VALIDATION PASSED: the polygon is readable and consistent.')

    if args.geojson:
        features = []
        for kind, outer, inners in result['raw_shapes']:
            features.append({
                'type': 'Feature',
                'geometry': {'type': 'Polygon',
                             'coordinates': [outer] + list(inners)},
                'properties': {'source_format': result['format']},
            })
        with open(args.geojson, 'w', encoding='utf-8') as handle:
            json.dump({'type': 'FeatureCollection', 'features': features},
                      handle, ensure_ascii=False)
        print('geojson written: %s' % args.geojson)
    return EXIT_OK


def _ascii(text):
    """Кириллица в консоль Windows не идёт -- заменяем, не роняя прогон."""
    if text is None:
        return ''
    return str(text).encode('ascii', 'replace').decode('ascii')


if __name__ == '__main__':
    sys.exit(main())
