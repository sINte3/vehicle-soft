# -*- coding: utf-8 -*-
"""GPS: участки, которых не может быть. Только чтение.

ЗАЧЕМ
30.09.2026 разбивка суток 28.09 (`tools/gps_track_only_days.py --date`)
показала: 281,92 из 301,18 га спецтехники за сутки дал один грузовик (Isuzu
80 258 JAA), и 230,23 га из них -- ОДИН участок по 184 точкам в работе за
весь день. У грузовика нет рабочей ширины: участок очертил его след, и правило
A1 такие гектары снимает. Но метод площади тот же у трактора и у объекта без
сопоставления, а там правило A1 выброс не снимет -- он уйдёт в план-факт
работой.

ЧТО ЗДЕСЬ «НЕ МОЖЕТ БЫТЬ»
Участок опубликованных суток, площадь которого больше, чем покрыл бы агрегат
шириной `--width-m`, если бы всё время на участке шёл на верхней границе окна
рабочей скорости метода:

    предел, га = ширина, м * SPEED_MAX_KMH * минуты / 600

Ширина по умолчанию 36 м -- допущение отчёта, заведомо шире агрегатов парка;
ключ её меняет.

ВРЕМЯ НА УЧАСТКЕ -- В ДВА ШАГА
[REASON]: столбец `minutes` участка -- признак замороженного правила «работа /
проезд»: точки в окне рабочей скорости СТРОГО внутри участка. Участок --
объединение треугольников Делоне по самим точкам, и его крайние точки лежат на
границе, а не внутри. У узкой полосы одного прохода на границе почти все
точки, и `minutes` у неё близки к нулю -- по одному этому столбцу обычный
проезд вдоль арыка выглядел бы «невозможным». Поэтому:
  1. `minutes` из базы -- нижняя граница времени: участок, который укладывается
     в предел уже по ней, возможен наверняка, и трек для него не читается;
  2. для остальных время считается заново по треку суток из файла точек --
     тем же правилом, что у метода, но по участку, расширенному на
     `BOUNDARY_M`, то есть вместе с граничными точками. Невозможным
     называется только участок, который не укладывается и в это время.
Участок, для которого трека нет (файл точек удалён по сроку хранения) или
контур не читается, -- «не оценён», а не «невозможен».

[REASON]: отчёт называет участки и ничего не решает. Считать ли их работой --
решение владельца (порог годности суток -- вопрос V-3 README расчёта), и ни
одна строка базы и файлов точек здесь не меняется: всё открывается `mode=ro`.

ВИДЫ ОБЪЕКТОВ -- по правилам расчёта (`gps.exclusion`):
  field       есть строка сопоставления с машиной, правило A1 его не берёт
  special     правило A1: след без гектаров
  no_machine  строки сопоставления есть, машины нет ни в одной
  no_mapping  строки сопоставления нет вовсе
  excluded    исключён: строка «не наша» или непольевая категория

Запуск -- из виртуального окружения расчёта (шаг 2 считает геометрию),
PowerShell, из C:\\gps-tools:

    & C:\\gps_venv\\Scripts\\python.exe tools\\gps_implausible_sites.py --db C:\\transport-report\\instance\\transport.db --dir C:\\transport-report\\instance --since 2026-09-01

Отсутствующая база не создаётся (код 2). Вывод -- только ASCII: имена машин
транслитерируются.
"""

import argparse
import os
import sqlite3
import sys
from collections import defaultdict
from datetime import datetime

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if REPO_ROOT not in sys.path:
    sys.path.insert(0, REPO_ROOT)

from gps.exclusion import excluded_units, track_only_units          # noqa: E402
from gps_collector import config as collector_config               # noqa: E402
from gps_collector import storage                                  # noqa: E402
from tools.gps_track_only_days import (console, open_readonly,     # noqa: E402
                                       unit_names)

# [REASON]: границы окна рабочей скорости и потолок промежутка -- те же, что у
# метода (`gps.area.SPEED_*_KMH`, `gps.daily.SITE_GAP_CAP_S`): время участка
# считается только по точкам в этом окне. `gps.area` и `gps.daily` тянут numpy
# и из системного Python не импортируются; тест сверяет числа с исходником.
SPEED_MIN_KMH = 1.0
SPEED_MAX_KMH = 15.0
SITE_GAP_CAP_S = 300.0
WIDTH_M = 36.0
# [REASON]: крайние точки участка лежат на его границе, а контур в базе
# округлён до 6 знаков (~0,1 м). Метр покрывает и то и другое и участок
# заметно не расширяет.
BOUNDARY_M = 1.0
KINDS = ('field', 'special', 'no_machine', 'no_mapping', 'excluded')


def limit_ha(minutes, width_m):
    """Сколько гектаров агрегат шириной `width_m` покрывает за `minutes`."""
    return width_m * SPEED_MAX_KMH * (minutes or 0.0) / 600.0


def kinds_of(con, units):
    """wialon_id -> вид объекта, по правилам расчёта."""
    excluded = excluded_units(con)
    special = track_only_units(con)
    mapped, with_machine = set(), set()
    for wialon_id, equipment_id in con.execute(
            'SELECT m.wialon_id, e.id FROM vialon_mappings m '
            'LEFT JOIN equipment e ON e.id = m.equipment_id '
            'WHERE m.wialon_id IS NOT NULL'):
        mapped.add(int(wialon_id))
        if equipment_id is not None:
            with_machine.add(int(wialon_id))
    out = {}
    for unit in units:
        if unit in excluded:
            out[unit] = 'excluded'
        elif unit in special:
            out[unit] = 'special'
        elif unit in with_machine:
            out[unit] = 'field'
        elif unit in mapped:
            out[unit] = 'no_machine'
        else:
            out[unit] = 'no_mapping'
    return out


def published_sites(con, since, until=None):
    """Участки опубликованных суток (причина пуста) за период."""
    query = ('SELECT p.id, p.work_date, p.wialon_id, p.site_number, '
             'p.area_ha, p.minutes FROM gps_work_polygons p '
             'JOIN gps_daily_aggregates a ON a.work_date = p.work_date '
             'AND a.wialon_id = p.wialon_id AND a.reason IS NULL '
             'WHERE p.work_date >= ?')
    args = [since]
    if until is not None:
        query += ' AND p.work_date <= ?'
        args.append(until)
    return [{'id': row_id, 'day': str(day), 'wialon_id': int(unit),
             'site': site, 'ha': float(area or 0.0), 'minutes': minutes}
            for row_id, day, unit, site, area, minutes
            in con.execute(query, args)]


def candidates(sites, width_m):
    """Участки, не уложившиеся в предел даже по нижней границе времени."""
    return [site for site in sites
            if site['ha'] > limit_ha(site['minutes'], width_m)]


def read_day_readonly(folder, unit_id, day):
    """Точки объекта за местные сутки -- как `storage.read_day`, но `mode=ro`.

    None -- файла точек за этот месяц нет (удалён по сроку хранения или не
    было вовсе): время участка не из чего посчитать.
    """
    start = int(datetime.strptime(day, '%Y-%m-%d')
                .replace(tzinfo=collector_config.TZ).timestamp())
    path = storage.points_path(folder, datetime.fromtimestamp(
        start, collector_config.TZ).strftime('%Y%m'))
    if not os.path.isfile(path):
        return None
    con = sqlite3.connect('file:%s?mode=ro' % path, uri=True, timeout=30)
    try:
        return [(int(t), float(lon), float(lat), float(speed),
                 -1 if sats is None else int(sats))
                for t, lon, lat, speed, sats in con.execute(
                    'SELECT t, lon, lat, speed, sats FROM points '
                    'WHERE unit_id = ? AND t >= ? AND t < ? ORDER BY t',
                    (int(unit_id), start, start + 86400))]
    finally:
        con.close()


def track_minutes(points, polygon_geojson, boundary_m=BOUNDARY_M):
    """Минуты в окне рабочей скорости на участке вместе с его границей.

    Правило то же, что у `gps.daily.site_minutes` (промежутки между соседними
    точками окна на участке, не длиннее `SITE_GAP_CAP_S`), но участок
    расширен на `boundary_m`. None -- контур не читается как многоугольник.
    Нужны numpy, shapely и pyproj: это окружение расчёта, `C:\\gps_venv`.
    """
    # [REASON]: чтение контура и проекция -- функции самого расчёта, а не
    # копия: иначе отчёт мерил бы участок не в тех координатах, в которых
    # его построил метод.
    import shapely
    from gps.area import to_utm
    from gps.daily import _utm_polygon_from_geojson
    polygon = _utm_polygon_from_geojson(polygon_geojson)
    if polygon is None:
        return None
    if not points:
        return 0.0
    area = polygon.buffer(boundary_m) if boundary_m else polygon
    xs, ys = to_utm([row[1] for row in points], [row[2] for row in points])
    inside = shapely.contains_xy(area, xs, ys)
    stamps = [row[0] for row, hit in zip(points, inside)
              if hit and SPEED_MIN_KMH <= row[3] <= SPEED_MAX_KMH]
    seconds = sum(b - a for a, b in zip(stamps, stamps[1:])
                  if b - a <= SITE_GAP_CAP_S)
    return seconds / 60.0


def judge(con, folder, suspects, width_m, measure=track_minutes):
    """(невозможные, не оценённые) среди подозрительных участков.

    `measure(points, polygon_geojson)` -- время участка по треку; подменяется
    в тестах без окружения расчёта.
    """
    by_day = defaultdict(list)
    for site in suspects:
        by_day[(site['wialon_id'], site['day'])].append(site)
    bad, unjudged = [], []
    for (unit, day), sites in sorted(by_day.items()):
        points = read_day_readonly(folder, unit, day)
        for site in sites:
            if points is None:
                unjudged.append(dict(site, why='no points file'))
                continue
            text = con.execute('SELECT polygon_geojson FROM gps_work_polygons '
                               'WHERE id = ?', (site['id'],)).fetchone()[0]
            minutes = measure(points, text) if text else None
            if minutes is None:
                unjudged.append(dict(site, why='contour not readable'))
                continue
            limit = limit_ha(minutes, width_m)
            if site['ha'] <= limit:
                continue
            bad.append(dict(site, track_minutes=minutes, limit=limit,
                            times=(site['ha'] / limit) if limit > 0 else None))
    bad.sort(key=lambda s: (s['times'] is not None, -(s['times'] or 0.0),
                            s['day'], s['wialon_id'], s['site'] or 0))
    return bad, unjudged


def report(sites, suspects, bad, unjudged, kinds, names, since, until,
           width_m, top, out=print):
    out('period             : %s .. %s' % (since, until or '(no end)'))
    out('bound              : width %.0f m at %.0f km/h -> %.2f ha per minute '
        'on the site' % (width_m, SPEED_MAX_KMH, limit_ha(1.0, width_m)))
    total = sum(site['ha'] for site in sites)
    wrong = sum(site['ha'] for site in bad)
    out('published sites    : %d, %.2f ha' % (len(sites), total))
    out('checked by track   : %d (over the bound by their stored minutes)'
        % len(suspects))
    out('not judged         : %d, %.2f ha (no track or no contour)'
        % (len(unjudged), sum(site['ha'] for site in unjudged)))
    out('impossible sites   : %d, %.2f ha (%.1f%% of the hectares)'
        % (len(bad), wrong, 100.0 * wrong / total if total else 0.0))
    out('')
    per_kind = defaultdict(lambda: {'ha': 0.0, 'bad_sites': 0, 'bad_ha': 0.0,
                                    'bad_objects': set()})
    for site in sites:
        per_kind[kinds[site['wialon_id']]]['ha'] += site['ha']
    for site in bad:
        row = per_kind[kinds[site['wialon_id']]]
        row['bad_sites'] += 1
        row['bad_ha'] += site['ha']
        row['bad_objects'].add(site['wialon_id'])
    out('%-11s %14s %17s %16s %15s'
        % ('kind', 'published_ha', 'impossible_sites', 'impossible_ha',
           'their_objects'))
    for kind in KINDS:
        row = per_kind.get(kind)
        if row is None:
            continue
        out('%-11s %14.2f %17d %16.2f %15d'
            % (kind, row['ha'], row['bad_sites'], row['bad_ha'],
               len(row['bad_objects'])))
    out('')
    shown = bad[:top]
    out('impossible sites, the largest excess first (%d of %d):'
        % (len(shown), len(bad)))
    out('%-10s %9s %-10s %4s %9s %8s %9s %7s  %s'
        % ('date', 'wialon_id', 'kind', 'site', 'ha', 'minutes', 'limit_ha',
           'x_limit', 'name'))
    for site in shown:
        out('%-10s %9d %-10s %4s %9.2f %8.1f %9.2f %7s  %s'
            % (site['day'], site['wialon_id'], kinds[site['wialon_id']],
               '-' if site['site'] is None else site['site'], site['ha'],
               site['track_minutes'], site['limit'],
               'no time' if site['times'] is None else '%.1f' % site['times'],
               console(names.get(site['wialon_id']) or '')))
    if unjudged:
        out('')
        out('not judged (%d):' % len(unjudged))
        for site in unjudged[:top]:
            out('%-10s %9d %-10s %4s %9.2f  %s'
                % (site['day'], site['wialon_id'], kinds[site['wialon_id']],
                   '-' if site['site'] is None else site['site'], site['ha'],
                   site['why']))
    return len(bad), wrong


def _day(option, value):
    try:
        return datetime.strptime(value, '%Y-%m-%d').strftime('%Y-%m-%d')
    except ValueError:
        sys.stderr.write('ERROR: %s must look like YYYY-MM-DD\n' % option)
        return None


def main(argv=None, measure=track_minutes):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument('--db', required=True, help='path to transport.db')
    parser.add_argument('--dir', required=True,
                        help='folder with the gps_points_YYYYMM.db files')
    parser.add_argument('--since', required=True, help='first date, YYYY-MM-DD')
    parser.add_argument('--until', help='last date, YYYY-MM-DD (default: none)')
    parser.add_argument('--width-m', type=float, default=WIDTH_M,
                        help='implement width the bound assumes, metres')
    parser.add_argument('--top', type=int, default=40,
                        help='how many sites to list')
    args = parser.parse_args(argv)
    since = _day('--since', args.since)
    until = None if args.until is None else _day('--until', args.until)
    if since is None or (args.until is not None and until is None):
        return 2
    if args.width_m <= 0:
        sys.stderr.write('ERROR: --width-m must be above zero\n')
        return 2
    if not os.path.isfile(args.db):
        sys.stderr.write('ERROR: no database at %s\n' % args.db)
        return 2
    if not os.path.isdir(args.dir):
        sys.stderr.write('ERROR: no folder %s\n' % args.dir)
        return 2
    con = open_readonly(args.db)
    try:
        sites = published_sites(con, since, until)
        suspects = candidates(sites, args.width_m)
        bad, unjudged = judge(con, args.dir, suspects, args.width_m, measure)
        kinds = kinds_of(con, sorted({site['wialon_id'] for site in sites}))
        names = unit_names(con, sorted({site['wialon_id']
                                        for site in bad[:args.top]}))
    finally:
        con.close()
    report(sites, suspects, bad, unjudged, kinds, names, since, until,
           args.width_m, args.top)
    print('nothing was written: the database and the point files were '
          'opened mode=ro')
    return 0


if __name__ == '__main__':
    sys.exit(main())
