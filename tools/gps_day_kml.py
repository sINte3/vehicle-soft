# -*- coding: utf-8 -*-
"""GPS: сутки машины в KML -- участки и трек поверх снимка в Google Earth.

ЗАЧЕМ
30.09.2026 владелец открыл на экране «Факт по технике» два невозможных участка
из `tools/gps_implausible_sites.py` (МТЗ-80.1 80 239 NA за 26.09, объект 7286
за 13.09) и не смог решить, работа это или нет: на прежнем экране нет
спутниковой подложки, а в списке машин -- госномеров. Экран с картой и треком
(A2) придёт с релизом, а релиз ждёт чужую проверку. До тех пор -- этот файл:
те же участки и трек суток, которые Google Earth кладёт на снимок.

ЧТО В ФАЙЛЕ -- папка на каждую пару «сутки:объект»:
  * участки суток: площадь, минуты на участке из базы, ответ оператора;
    у неопубликованных суток (есть причина) участки тоже рисуются, а причина
    стоит в названии папки;
  * трек: отрезки в окне рабочей скорости метода (1-15 км/ч) -- зелёные,
    остальное -- серое; промежуток длиннее 300 с рвёт линию, как у времени
    участка.
Имя машины -- по правилу экрана («модель -- госномер»), подписи -- на русском
и узбекском.

[REASON]: только чтение, и файл не уходит никуда сам. База и файл точек
открываются `mode=ro`; KML пишется в указанный файл на компьютере владельца, а
открывать ли его в Google Earth -- решает владелец (трек -- данные холдинга).

Запуск (PowerShell, из C:\\gps-tools; геометрия не нужна -- системный Python):

    & "C:\\Program Files\\Python314\\python.exe" tools\\gps_day_kml.py --db C:\\transport-report\\instance\\transport.db --dir C:\\transport-report\\instance --day 2026-09-26:393 --day 2026-09-13:7286 --out C:\\gps-tools\\check\\sites.kml

Вывод в консоль -- ASCII.
"""

import argparse
import json
import os
import sys
from datetime import datetime
from xml.sax.saxutils import escape

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if REPO_ROOT not in sys.path:
    sys.path.insert(0, REPO_ROOT)

from tools.gps_implausible_sites import (SITE_GAP_CAP_S,           # noqa: E402
                                         SPEED_MAX_KMH, SPEED_MIN_KMH,
                                         read_day_readonly)
from tools.gps_track_only_days import (console, open_readonly,     # noqa: E402
                                       unit_names)

# KML: цвет -- aabbggrr.
STYLES = (
    ('site', '<LineStyle><color>ff0078ff</color><width>2</width></LineStyle>'
             '<PolyStyle><color>550078ff</color></PolyStyle>'),
    ('work', '<LineStyle><color>ff53c800</color><width>3</width></LineStyle>'),
    ('move', '<LineStyle><color>ff9e9e9e</color><width>2</width></LineStyle>'),
)


def number(value, digits):
    """Число с запятой, как на экране; None -- прочерк."""
    if value is None:
        return '—'
    return ('%.*f' % (digits, value)).replace('.', ',')


def parse_pair(text):
    """'ГГГГ-ММ-ДД:ID' -> (день, id) или None."""
    day, _, unit = text.partition(':')
    try:
        day = datetime.strptime(day, '%Y-%m-%d').strftime('%Y-%m-%d')
    except ValueError:
        return None
    if not unit.isdigit():
        return None
    return day, int(unit)


def track_pieces(points):
    """[(в работе ли, [(lon, lat), ...])] -- куски трека одного класса скорости.

    Смена класса начинает новый кусок с последней точки прежнего, чтобы линия
    не рвалась; промежуток длиннее `SITE_GAP_CAP_S` рвёт её. Кусок из одной
    точки линией не нарисовать -- он отбрасывается.
    """
    pieces, current, previous_t = [], None, None
    for t, lon, lat, speed, _sats in points:
        work = SPEED_MIN_KMH <= speed <= SPEED_MAX_KMH
        gap = previous_t is not None and t - previous_t > SITE_GAP_CAP_S
        if current is None or gap or current[0] != work:
            start = [] if (current is None or gap) else [current[1][-1]]
            current = (work, start + [(lon, lat)])
            pieces.append(current)
        else:
            current[1].append((lon, lat))
        previous_t = t
    return [piece for piece in pieces if len(piece[1]) >= 2]


def polygons_of(text):
    """Кольца многоугольников из GeoJSON участка: [[кольцо, дыра, ...], ...]."""
    try:
        geometry = json.loads(text or '')
    except ValueError:
        return []
    if not isinstance(geometry, dict):
        return []
    if geometry.get('type') == 'Polygon':
        return [geometry.get('coordinates') or []]
    if geometry.get('type') == 'MultiPolygon':
        return geometry.get('coordinates') or []
    return []


def _coordinates(ring):
    return ' '.join('%.6f,%.6f' % (float(pt[0]), float(pt[1])) for pt in ring)


def _polygon_kml(rings):
    if not rings:
        return ''
    inner = ''.join('<innerBoundaryIs><LinearRing><coordinates>%s</coordinates>'
                    '</LinearRing></innerBoundaryIs>' % _coordinates(hole)
                    for hole in rings[1:])
    return ('<Polygon><outerBoundaryIs><LinearRing><coordinates>%s'
            '</coordinates></LinearRing></outerBoundaryIs>%s</Polygon>'
            % (_coordinates(rings[0]), inner))


def read_day(con, folder, day, unit):
    """Всё, что нужно для папки одних суток одного объекта."""
    aggregate = con.execute(
        'SELECT reason FROM gps_daily_aggregates '
        'WHERE work_date = ? AND wialon_id = ?', (day, unit)).fetchone()
    sites = con.execute(
        'SELECT site_number, area_ha, minutes, polygon_geojson, operator_label '
        'FROM gps_work_polygons WHERE work_date = ? AND wialon_id = ? '
        'ORDER BY site_number', (day, unit)).fetchall()
    return {'day': day, 'unit': unit,
            'has_row': aggregate is not None,
            'reason': aggregate[0] if aggregate else None,
            'sites': sites,
            'points': read_day_readonly(folder, unit, day)}


def folder_kml(entry, name):
    day_label = datetime.strptime(entry['day'], '%Y-%m-%d').strftime('%d.%m.%Y')
    title = '%s · %s' % (name or entry['unit'], day_label)
    if not entry['has_row']:
        title += ' · нет суточной строки / кунлик қатор йўқ'
    elif entry['reason']:
        title += ' · не опубликованы / эълон қилинмаган: %s' % entry['reason']
    parts = ['<Folder><name>%s</name>' % escape(title)]
    for number_, area, minutes, text, label in entry['sites']:
        shapes = [_polygon_kml(rings) for rings in polygons_of(text)]
        shapes = [shape for shape in shapes if shape]
        if not shapes:
            continue
        geometry = (shapes[0] if len(shapes) == 1 else
                    '<MultiGeometry>%s</MultiGeometry>' % ''.join(shapes))
        answer = label or '—'
        parts.append(
            '<Placemark><name>%s</name><description>%s</description>'
            '<styleUrl>#site</styleUrl>%s</Placemark>'
            % (escape('Участок / участка %s — %s га'
                      % (number_, number(area, 2))),
               escape('Минут на участке (в базе) / участкадаги дақиқалар '
                      '(базада): %s. Ответ оператора / оператор жавоби: %s.'
                      % (number(minutes, 1), answer)),
               geometry))
    if entry['points'] is None:
        parts.append('<Placemark><name>%s</name></Placemark>' % escape(
            'Файла точек за этот месяц нет / бу ой учун нуқталар файли йўқ'))
    elif not entry['points']:
        parts.append('<Placemark><name>%s</name></Placemark>' % escape(
            'Точек за эти сутки нет / бу кун учун нуқталар йўқ'))
    else:
        # [REASON]: кусков у дня опрыскивания -- под двести; отдельными
        # метками они засыпали бы список Google Earth. Две метки на сутки --
        # «в работе» и «вне работы», -- каждая из своих кусков.
        pieces = track_pieces(entry['points'])
        for work, style, label in (
                (True, 'work', 'Трек в работе, 1–15 км/ч / иш тезлигидаги трек'),
                (False, 'move', 'Трек вне работы / ишдан ташқари трек')):
            lines = ''.join('<LineString><tessellate>1</tessellate>'
                            '<coordinates>%s</coordinates></LineString>'
                            % _coordinates(line)
                            for kind, line in pieces if kind is work)
            if lines:
                parts.append('<Placemark><name>%s</name><styleUrl>#%s'
                             '</styleUrl><MultiGeometry>%s</MultiGeometry>'
                             '</Placemark>' % (escape(label), style, lines))
    parts.append('</Folder>')
    return ''.join(parts)


def kml_document(entries, names):
    styles = ''.join('<Style id="%s">%s</Style>' % (key, body)
                     for key, body in STYLES)
    folders = ''.join(folder_kml(entry, names.get(entry['unit']))
                      for entry in entries)
    return ('<?xml version="1.0" encoding="UTF-8"?>\n'
            '<kml xmlns="http://www.opengis.net/kml/2.2"><Document>'
            '<name>%s</name>%s%s</Document></kml>\n'
            % (escape('GPS: участки и трек / участкалар ва трек'),
               styles, folders))


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument('--db', required=True, help='path to transport.db')
    parser.add_argument('--dir', required=True,
                        help='folder with the gps_points_YYYYMM.db files')
    parser.add_argument('--day', action='append', required=True,
                        help='YYYY-MM-DD:WIALON_ID, may repeat')
    parser.add_argument('--out', required=True, help='KML file to write')
    args = parser.parse_args(argv)
    pairs = [parse_pair(text) for text in args.day]
    if None in pairs:
        sys.stderr.write('ERROR: --day must look like YYYY-MM-DD:WIALON_ID\n')
        return 2
    if not os.path.isfile(args.db):
        sys.stderr.write('ERROR: no database at %s\n' % args.db)
        return 2
    if not os.path.isdir(args.dir):
        sys.stderr.write('ERROR: no folder %s\n' % args.dir)
        return 2
    con = open_readonly(args.db)
    try:
        entries = [read_day(con, args.dir, day, unit) for day, unit in pairs]
        names = unit_names(con, sorted({unit for _day, unit in pairs}))
    finally:
        con.close()
    with open(args.out, 'w', encoding='utf-8') as handle:
        handle.write(kml_document(entries, names))
    for entry in entries:
        pieces = track_pieces(entry['points'] or [])
        print('%s %9d  sites %d  points %s  work pieces %d  other pieces %d  %s'
              % (entry['day'], entry['unit'], len(entry['sites']),
                 'no file' if entry['points'] is None
                 else len(entry['points']),
                 sum(1 for work, _line in pieces if work),
                 sum(1 for work, _line in pieces if not work),
                 console(names.get(entry['unit']) or '')))
    print('written: %s' % console(args.out))
    print('nothing was written to the database or the point files: '
          'they were opened mode=ro')
    return 0


if __name__ == '__main__':
    sys.exit(main())
