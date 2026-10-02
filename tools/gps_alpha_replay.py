# -*- coding: utf-8 -*-
"""GPS A7: «было / стало» предрегистрированного правила допуска. Только чтение.

ЗАЧЕМ
Правило «допуск при переполнении» (docs/GPS_PLAN_FAKT_VISION_ROADMAP.md,
2.11) записано 02.10.2026 до прогона на данных, вместе с условиями приёмки.
Этот инструмент и есть прогон: он считает каждые машино-сутки действующим
кодом и правилом (`overflow_cap=True` в `gps.area` / `gps.daily`) на одних и
тех же точках и печатает, выполнены ли условия, которые проверяются машиной.
Условия, которые решает глаз владельца (3 и 4), он готовит: выгружает KML,
где у каждых суток рядом лежат участки «было» и «стало» поверх трека.

ДВА РЕЖИМА
  --tracks ... --zones ...  наборы с ручными замерами (условие 1). Треки --
      CSV с разделителем «;» и столбцами unit_id, [date,] time, lat, lon,
      speed (как `verify_tracks.csv` 27.07 и `verify2_tracks.csv` 12.08);
      зоны -- `wialon_zones.json`. Каждые машино-сутки считаются по дню
      (`work_sites`) и по каждому контуру, куда машина заехала хотя бы 10
      точками в движении (`worked_area`). Условие выполнено, только если
      ВСЕ результаты совпали с действующим кодом бит в бит.
  --db ... --dir ...  production (условия 2, 3, 5 и выгрузка для 3 и 4).
      Опубликованные машино-сутки периода (причина пуста), чьи точки ещё на
      диске. Контроль прогона: действующий код, пересчитанный по точкам,
      обязан совпасть с базой; иначе виноват инструмент, а не правило.

[REASON]: база и файлы точек открываются `mode=ro`, KML пишется только в файл,
указанный ключом. Решения здесь нет: условия 3 и 4 предрегистрации решает
владелец по выгрузке, а включение правила в ночной расчёт -- отдельный шаг
после его решения.

Запуск -- из окружения расчёта (нужна геометрия), PowerShell, из C:\\gps-tools:

    & C:\\gps_venv\\Scripts\\python.exe tools\\gps_alpha_replay.py --tracks C:\\diag\\wialon\\verify_tracks.csv --tracks C:\\diag\\wialon\\verify2_tracks.csv --zones C:\\diag\\wialon\\wialon_zones.json

    & C:\\gps_venv\\Scripts\\python.exe tools\\gps_alpha_replay.py --db C:\\transport-report\\instance\\transport.db --dir C:\\transport-report\\instance --since 2026-09-01 --until 2026-09-30 --kml C:\\gps-tools\\check\\a7_replay.kml

Отсутствующие база, папка или файлы -- код 2. Вывод -- только ASCII.
"""

import argparse
import csv
import os
import sys
from collections import defaultdict
from datetime import datetime
from xml.sax.saxutils import escape

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if REPO_ROOT not in sys.path:
    sys.path.insert(0, REPO_ROOT)

from gps.area import (ALPHA_SPACING_FACTOR, SPACING_CAP_M,           # noqa: E402
                      candidate_contours, work_sites, worked_area)
from gps.daily import compute_day, load_contours                     # noqa: E402
from tools.gps_day_kml import (STYLES, _coordinates, _polygon_kml,   # noqa: E402
                               polygons_of, track_pieces)
from tools.gps_implausible_sites import kinds_of, read_day_readonly  # noqa: E402
from tools.gps_track_only_days import (console, open_readonly,      # noqa: E402
                                       unit_names)

ALPHA_CEILING_M = ALPHA_SPACING_FACTOR * SPACING_CAP_M      # 53.568
# [REASON]: шесть суток сентября с допуском 300-981 м, названные в условии 3
# предрегистрации (вывод `tools/gps_alpha_report.py` 02.10). Список закреплён
# в документе, поэтому и здесь он закреплён, а не вычисляется заново.
NAMED_DAYS = (('2026-09-26', 393), ('2026-09-26', 330), ('2026-09-26', 3188),
              ('2026-09-03', 411), ('2026-09-21', 10322), ('2026-09-05', 337))
TOP_LOSSES = 10
MIN_MOVING_POINTS_IN_ZONE = 10
EPSILON = 1e-9
NEW_STYLE = ('new', '<LineStyle><color>ffff6600</color><width>2</width>'
                    '</LineStyle><PolyStyle><color>55ff6600</color></PolyStyle>')


# --- наборы с ручными замерами (условие 1) ------------------------------------

def load_csv_days(path):
    """(unit_id, дата или '') -> [(t, lon, lat, speed)] по времени."""
    days = defaultdict(list)
    with open(path, encoding='utf-8-sig', newline='') as handle:
        for row in csv.DictReader(handle, delimiter=';'):
            day = (row.get('date') or '').strip()
            hh, mm, ss = row['time'].strip().split(':')
            t = int(hh) * 3600 + int(mm) * 60 + int(ss)
            if day:
                t += datetime.strptime(day, '%Y-%m-%d').toordinal() * 86400
            days[(int(row['unit_id']), day)].append(
                (t, float(row['lon']), float(row['lat']), float(row['speed'])))
    return {key: sorted(track) for key, track in days.items()}


def sites_key(sites):
    return [(site.area_ha, site.alpha_used_m, site.pass_spacing_m)
            for site in sites]


def compare_set(name, days, zones):
    """Строки сравнения: по дню и по каждому контуру с заездом."""
    rows = []
    for (unit, day), track in sorted(days.items()):
        today, _ = work_sites(track)
        capped, _ = work_sites(track, overflow_cap=True)
        spacing = today[0].pass_spacing_m if today else None
        rows.append({'set': name, 'unit': unit, 'day': day or '-',
                     'path': 'day', 'zone': None,
                     'ha_today': sum(s.area_ha for s in today),
                     'ha_cap': sum(s.area_ha for s in capped),
                     'spacing': spacing,
                     'triggered': spacing is not None and spacing > SPACING_CAP_M,
                     'same': sites_key(today) == sites_key(capped)})
        for zone_id, _inside in candidate_contours(
                track, zones, min_moving_points=MIN_MOVING_POINTS_IN_ZONE):
            one = worked_area(track, zones[zone_id], zone_id)
            two = worked_area(track, zones[zone_id], zone_id, overflow_cap=True)
            rows.append({'set': name, 'unit': unit, 'day': day or '-',
                         'path': 'zone', 'zone': zone_id,
                         'ha_today': one.area_ha, 'ha_cap': two.area_ha,
                         'spacing': one.pass_spacing_m,
                         'triggered': (one.pass_spacing_m is not None
                                       and one.pass_spacing_m > SPACING_CAP_M),
                         'same': ((one.area_ha, one.alpha_used_m,
                                   one.pass_spacing_m)
                                  == (two.area_ha, two.alpha_used_m,
                                      two.pass_spacing_m))})
    return rows


def report_sets(rows, out=print):
    differing = [row for row in rows if not row['same']]
    triggered = [row for row in rows if row['triggered']]
    for name in sorted({row['set'] for row in rows}):
        mine = [row for row in rows if row['set'] == name]
        out('%s: machine-days %d, zone works %d, triggered %d, different %d'
            % (console(name), sum(1 for r in mine if r['path'] == 'day'),
               sum(1 for r in mine if r['path'] == 'zone'),
               sum(1 for r in mine if r['triggered']),
               sum(1 for r in mine if not r['same'])))
    for row in triggered + [r for r in differing if r not in triggered]:
        out('  %-5s unit %-6d day %-10s zone %-6s spacing %s  ha %.4f -> %.4f  %s'
            % (row['path'], row['unit'], row['day'],
               '-' if row['zone'] is None else row['zone'],
               '-' if row['spacing'] is None else '%.2f' % row['spacing'],
               row['ha_today'], row['ha_cap'],
               'same' if row['same'] else 'DIFFERENT'))
    verdict = 'PASS' if not differing else 'FAIL'
    out('CONDITION 1 (hand-measured sets bit-identical): %s' % verdict)
    return not differing


# --- production (условия 2, 3, 5) ------------------------------------------------

def published_days(con, since, until):
    query = ('SELECT work_date, wialon_id, method_version FROM gps_daily_aggregates '
             'WHERE reason IS NULL AND work_date >= ?')
    args = [since]
    if until is not None:
        query += ' AND work_date <= ?'
        args.append(until)
    return [(str(day), int(unit), version) for day, unit, version
            in con.execute(query + ' ORDER BY work_date, wialon_id', args)]


def stored_sites(con, day, unit):
    return [(round(float(area), 4),
             None if alpha is None else round(float(alpha), 3),
             None if spacing is None else round(float(spacing), 3))
            for area, alpha, spacing in con.execute(
                'SELECT area_ha, alpha_used_m, pass_spacing_m '
                'FROM gps_work_polygons WHERE work_date = ? AND wialon_id = ? '
                'ORDER BY area_ha DESC, site_number', (day, unit))]


def row_key(rows):
    return sorted(((row['area_ha'], row['alpha_used_m'], row['pass_spacing_m'])
                   for row in rows), key=lambda item: -item[0])


def replay_day(points, contours):
    today = compute_day(points, contours=contours)
    capped = compute_day(points, contours=contours, overflow_cap=True)
    return today.sites, capped.sites


def first(rows, key):
    return rows[0][key] if rows else None


def judge_day(day, unit, version, stored, today, capped, current_version):
    """Одна строка прогона и её нарушения."""
    ha_today = sum(row['area_ha'] for row in today)
    ha_cap = sum(row['area_ha'] for row in capped)
    spacing = first(today, 'pass_spacing_m')
    alpha_today, alpha_cap = first(today, 'alpha_used_m'), first(capped, 'alpha_used_m')
    triggered = spacing is not None and spacing > SPACING_CAP_M
    violations = []
    if ha_cap > ha_today + EPSILON:
        violations.append('more hectares than today')
    if alpha_cap is not None and alpha_today is not None \
            and alpha_cap > alpha_today + EPSILON:
        violations.append('wider alpha than today')
    if alpha_cap is not None and alpha_cap > ALPHA_CEILING_M + 0.0005:
        violations.append('alpha above %.3f' % ALPHA_CEILING_M)
    if not triggered and row_key(today) != row_key(capped):
        violations.append('changed below the cap')
    control = None
    if version == current_version:
        control = row_key(today) == sorted(stored, key=lambda item: -item[0])
    return {'day': day, 'unit': unit, 'ha_today': ha_today, 'ha_cap': ha_cap,
            'spacing': spacing, 'spacing_cap': first(capped, 'pass_spacing_m'),
            'alpha_today': alpha_today, 'alpha_cap': alpha_cap,
            'triggered': triggered, 'violations': violations,
            'control': control, 'today': today, 'capped': capped}


def site_placemarks(rows, style, label):
    parts = []
    for number, row in enumerate(rows, 1):
        shapes = [_polygon_kml(rings) for rings in polygons_of(row['polygon_geojson'])]
        shapes = [shape for shape in shapes if shape]
        if not shapes:
            continue
        geometry = (shapes[0] if len(shapes) == 1 else
                    '<MultiGeometry>%s</MultiGeometry>' % ''.join(shapes))
        parts.append('<Placemark><name>%s</name><styleUrl>#%s</styleUrl>%s'
                     '</Placemark>' % (escape('%s %d — %.2f га'
                                              % (label, number, row['area_ha'])),
                                       style, geometry))
    return ''.join(parts)


def kml_folder(result, points, name):
    day = datetime.strptime(result['day'], '%Y-%m-%d').strftime('%d.%m.%Y')
    title = ('%s · %s · было / эди %.2f га → стало / бўлди %.2f га'
             % (name or result['unit'], day, result['ha_today'],
                result['ha_cap']))
    parts = ['<Folder><name>%s</name>' % escape(title),
             site_placemarks(result['today'], 'site', 'Было / эди: участок'),
             site_placemarks(result['capped'], 'new', 'Стало / бўлди: участок')]
    pieces = track_pieces(points or [])
    for work, style, label in (
            (True, 'work', 'Трек в работе, 1–15 км/ч / иш тезлигидаги трек'),
            (False, 'move', 'Трек вне работы / ишдан ташқари трек')):
        lines = ''.join('<LineString><tessellate>1</tessellate><coordinates>%s'
                        '</coordinates></LineString>' % _coordinates(line)
                        for kind, line in pieces if kind is work)
        if lines:
            parts.append('<Placemark><name>%s</name><styleUrl>#%s</styleUrl>'
                         '<MultiGeometry>%s</MultiGeometry></Placemark>'
                         % (escape(label), style, lines))
    parts.append('</Folder>')
    return ''.join(parts)


def kml_document(folders):
    styles = ''.join('<Style id="%s">%s</Style>' % (key, body)
                     for key, body in tuple(STYLES) + (NEW_STYLE,))
    return ('<?xml version="1.0" encoding="UTF-8"?>\n'
            '<kml xmlns="http://www.opengis.net/kml/2.2"><Document>'
            '<name>%s</name>%s%s</Document></kml>\n'
            % (escape('GPS A7: было и стало / эди ва бўлди'), styles,
               ''.join(folders)))


def replay_production(db, folder, since, until, kml_path, out=print,
                      progress=None):
    from gps.daily import METHOD_VERSION
    con = open_readonly(db)
    try:
        days = published_days(con, since, until)
        contours = load_contours(con)
        results, missing = [], 0
        for index, (day, unit, version) in enumerate(days, 1):
            points = read_day_readonly(folder, unit, day)
            if not points:
                missing += 1
                continue
            today, capped = replay_day(points, contours or None)
            results.append(judge_day(day, unit, version,
                                     stored_sites(con, day, unit), today,
                                     capped, METHOD_VERSION))
            if progress and index % 200 == 0:
                progress('  %d of %d machine-days' % (index, len(days)))
        units = sorted({row['unit'] for row in results})
        kinds = kinds_of(con, units)
        names = unit_names(con, units)
    finally:
        con.close()
    ok = report_production(results, missing, kinds, names, out)
    if kml_path:
        picked = pick_for_review(results, kinds)
        folders = []
        for result in picked:
            points = read_day_readonly(folder, result['unit'], result['day'])
            folders.append(kml_folder(result, points, names.get(result['unit'])))
        with open(kml_path, 'w', encoding='utf-8') as handle:
            handle.write(kml_document(folders))
        out('KML for conditions 3 and 4 (%d machine-days): %s'
            % (len(picked), console(kml_path)))
    return ok


def counted(results, kinds):
    return [row for row in results if kinds.get(row['unit']) != 'excluded']


def top_losses(results, kinds):
    named = set(NAMED_DAYS)
    rest = [row for row in counted(results, kinds)
            if row['triggered'] and (row['day'], row['unit']) not in named
            and row['ha_today'] - row['ha_cap'] > EPSILON]
    rest.sort(key=lambda row: (-(row['ha_today'] - row['ha_cap']), row['day'],
                               row['unit']))
    return rest[:TOP_LOSSES]


def pick_for_review(results, kinds):
    by_key = {(row['day'], row['unit']): row for row in results}
    named = [by_key[key] for key in NAMED_DAYS if key in by_key]
    return named + top_losses(results, kinds)


def report_production(results, missing, kinds, names, out=print):
    mine = counted(results, kinds)
    out('machine-days replayed: %d (points gone from disk: %d); counted %d, '
        'excluded objects %d' % (len(results), missing, len(mine),
                                 len(results) - len(mine)))
    checked = [row for row in results if row['control'] is not None]
    mismatched = [row for row in checked if not row['control']]
    out('control, today recomputed == stored: %d of %d same, %d different, '
        '%d not checked (older method version)'
        % (len(checked) - len(mismatched), len(checked), len(mismatched),
           len(results) - len(checked)))
    for row in mismatched[:20]:
        out('  control mismatch %s %d' % (row['day'], row['unit']))
    violations = [row for row in results if row['violations']]
    for row in violations[:20]:
        out('  VIOLATION %s %d: %s' % (row['day'], row['unit'],
                                       '; '.join(row['violations'])))
    triggered = [row for row in mine if row['triggered']]
    ha_today = sum(row['ha_today'] for row in mine)
    ha_cap = sum(row['ha_cap'] for row in mine)
    out('CONDITION 2 (invariants on every machine-day): %s -- %d violation(s)%s'
        % ('PASS' if not violations and not mismatched else 'FAIL',
           len(violations),
           '' if not mismatched else '; fix the replay first: the control '
                                     'failed on %d machine-day(s)' % len(mismatched)))
    out('')
    out('condition 3, the six named machine-days:')
    by_key = {(row['day'], row['unit']): row for row in results}
    named_ok = True
    for day, unit in NAMED_DAYS:
        row = by_key.get((day, unit))
        if row is None:
            named_ok = False
            out('  %s %6d  not replayed (no published row or no points)'
                % (day, unit))
            continue
        fell = row['triggered'] and row['ha_cap'] < row['ha_today'] - EPSILON
        named_ok = named_ok and fell
        out('  %s %6d  triggered %-3s  alpha %s -> %s  ha %.2f -> %.2f  %s  %s'
            % (day, unit, 'yes' if row['triggered'] else 'NO',
               '-' if row['alpha_today'] is None else '%.1f' % row['alpha_today'],
               '-' if row['alpha_cap'] is None else '%.1f' % row['alpha_cap'],
               row['ha_today'], row['ha_cap'], 'fell' if fell else 'DID NOT FALL',
               console(names.get(unit) or '')))
    out('CONDITION 3, machine part (all six triggered, hectares strictly fall): %s'
        % ('PASS' if named_ok else 'FAIL'))
    out('CONDITION 3, owner part: look at the six folders of the KML -- no '
        'remaining blue site may cover ground between different roads')
    out('')
    out('condition 4, the %d other triggered machine-days with the largest loss '
        '(owner looks at them in the KML):' % TOP_LOSSES)
    for row in top_losses(results, kinds):
        out('  %s %6d  alpha %.1f -> %s  ha %.2f -> %.2f  %s'
            % (row['day'], row['unit'], row['alpha_today'],
               '-' if row['alpha_cap'] is None else '%.1f' % row['alpha_cap'],
               row['ha_today'], row['ha_cap'],
               console(names.get(row['unit']) or '')))
    out('')
    untouched = [row for row in mine if not row['triggered']
                 and row['alpha_today'] is not None
                 and row['alpha_today'] > SPACING_CAP_M + 0.0005]
    out('condition 5 (report, no verdict):')
    out('  machine-days triggered: %d of %d counted' % (len(triggered), len(mine)))
    out('  hectares on untouched machine-days with alpha %.2f-%.3f m: %.2f '
        '(%d machine-days)' % (SPACING_CAP_M, ALPHA_CEILING_M,
                               sum(row['ha_today'] for row in untouched),
                               len(untouched)))
    out('  plan-fact of the period, counted objects: %.2f ha today -> %.2f ha '
        'with the rule (%+.2f)' % (ha_today, ha_cap, ha_cap - ha_today))
    return not violations and not mismatched and named_ok


def _day(option, value):
    try:
        return datetime.strptime(value, '%Y-%m-%d').strftime('%Y-%m-%d')
    except ValueError:
        sys.stderr.write('ERROR: %s must look like YYYY-MM-DD\n' % option)
        return None


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument('--tracks', action='append', default=[],
                        help='hand-measured set tracks csv, may repeat')
    parser.add_argument('--zones', help='wialon_zones.json for the sets')
    parser.add_argument('--db', help='path to transport.db (production mode)')
    parser.add_argument('--dir', help='folder with gps_points_YYYYMM.db files')
    parser.add_argument('--since', help='first date, YYYY-MM-DD')
    parser.add_argument('--until', help='last date, YYYY-MM-DD')
    parser.add_argument('--kml', help='KML file for the owner (production mode)')
    args = parser.parse_args(argv)
    if bool(args.tracks) == bool(args.db):
        sys.stderr.write('ERROR: give either --tracks/--zones or --db/--dir\n')
        return 2
    if args.tracks:
        missing = [path for path in args.tracks + [args.zones or '']
                   if not os.path.isfile(path)]
        if missing:
            sys.stderr.write('ERROR: not found: %s\n'
                             % ', '.join(console(m) for m in missing))
            return 2
        from tools.gps_area_method_repro import load_zones
        zones = {zone_id: polygon for zone_id, (_name, polygon)
                 in load_zones(args.zones).items()}
        rows = []
        for path in args.tracks:
            rows += compare_set(os.path.basename(path), load_csv_days(path),
                                zones)
        report_sets(rows)
        print('nothing was written: the tracks and zones were only read')
        return 0
    since = _day('--since', args.since or '')
    until = None if args.until is None else _day('--until', args.until)
    if since is None or (args.until is not None and until is None):
        return 2
    if not os.path.isfile(args.db) or not os.path.isdir(args.dir or ''):
        sys.stderr.write('ERROR: no database or no points folder\n')
        return 2
    replay_production(args.db, args.dir, since, until, args.kml,
                      progress=print)
    print('nothing was written to the database or the point files: they were '
          'opened mode=ro')
    return 0


if __name__ == '__main__':
    sys.exit(main())
