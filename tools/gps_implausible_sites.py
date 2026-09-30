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
шириной `--width-m`, если бы всё время на участке (`minutes`: точки в окне
рабочей скорости внутри участка) шёл на верхней границе этого окна:

    предел, га = ширина, м * SPEED_MAX_KMH * минуты / 600

И участок с площадью, но без минут. Ширина по умолчанию 36 м -- допущение
отчёта, заведомо шире агрегатов парка; ключ её меняет.

[REASON]: отчёт называет участки и ничего не решает. Считать ли их работой --
решение владельца (порог годности суток -- вопрос V-3 README расчёта), и ни
одна строка базы здесь не меняется: база открывается `mode=ro`.

ВИДЫ ОБЪЕКТОВ -- по правилам расчёта (`gps.exclusion`):
  field       есть строка сопоставления с машиной, правило A1 его не берёт
  special     правило A1: след без гектаров
  no_machine  строки сопоставления есть, машины нет ни в одной
  no_mapping  строки сопоставления нет вовсе
  excluded    исключён: строка «не наша» или непольевая категория

Запуск (PowerShell, из C:\\gps-tools):

    & "C:\\Program Files\\Python314\\python.exe" tools\\gps_implausible_sites.py --db C:\\transport-report\\instance\\transport.db --since 2026-09-01

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
from tools.gps_track_only_days import (console, open_readonly,     # noqa: E402
                                       unit_names)

# [REASON]: верхняя граница окна рабочей скорости -- та же, что у метода
# (`gps.area.SPEED_MAX_KMH`): минуты участка считаются только по точкам в этом
# окне. `gps.area` тянет numpy и из системного Python не импортируется; тест
# сверяет число с исходником метода.
SPEED_MAX_KMH = 15.0
WIDTH_M = 36.0
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
    query = ('SELECT p.work_date, p.wialon_id, p.site_number, p.area_ha, '
             'p.minutes FROM gps_work_polygons p '
             'JOIN gps_daily_aggregates a ON a.work_date = p.work_date '
             'AND a.wialon_id = p.wialon_id AND a.reason IS NULL '
             'WHERE p.work_date >= ?')
    args = [since]
    if until is not None:
        query += ' AND p.work_date <= ?'
        args.append(until)
    return [{'day': str(day), 'wialon_id': int(unit), 'site': site,
             'ha': float(area or 0.0), 'minutes': minutes}
            for day, unit, site, area, minutes in con.execute(query, args)]


def impossible(sites, width_m):
    """Участки, которых не может быть: от самого большого превышения предела.

    Участок без минут при ненулевой площади идёт первым: для него предел --
    ноль, и превышение бесконечно.
    """
    out = []
    for site in sites:
        limit = limit_ha(site['minutes'], width_m)
        if site['ha'] <= limit:
            continue
        out.append(dict(site, limit=limit,
                        times=(site['ha'] / limit) if limit > 0 else None))
    return sorted(out, key=lambda s: (s['times'] is not None,
                                      -(s['times'] or 0.0), s['day'],
                                      s['wialon_id'], s['site'] or 0))


def report(sites, bad, kinds, names, since, until, width_m, top, out=print):
    out('period             : %s .. %s' % (since, until or '(no end)'))
    out('bound              : width %.0f m at %.0f km/h -> %.2f ha per minute '
        'on the site' % (width_m, SPEED_MAX_KMH, limit_ha(1.0, width_m)))
    total = sum(site['ha'] for site in sites)
    wrong = sum(site['ha'] for site in bad)
    out('published sites    : %d, %.2f ha' % (len(sites), total))
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
        out('%-10s %9d %-10s %4s %9.2f %8s %9.2f %7s  %s'
            % (site['day'], site['wialon_id'], kinds[site['wialon_id']],
               '-' if site['site'] is None else site['site'], site['ha'],
               '-' if site['minutes'] is None else '%.1f' % site['minutes'],
               site['limit'],
               'no time' if site['times'] is None else '%.1f' % site['times'],
               console(names.get(site['wialon_id']) or '')))
    return len(bad), wrong


def _day(option, value):
    try:
        return datetime.strptime(value, '%Y-%m-%d').strftime('%Y-%m-%d')
    except ValueError:
        sys.stderr.write('ERROR: %s must look like YYYY-MM-DD\n' % option)
        return None


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument('--db', required=True, help='path to transport.db')
    parser.add_argument('--since', required=True, help='first date, YYYY-MM-DD')
    parser.add_argument('--until', help='last date, YYYY-MM-DD (default: none)')
    parser.add_argument('--width-m', type=float, default=WIDTH_M,
                        help='implement width the bound assumes, metres')
    parser.add_argument('--top', type=int, default=40,
                        help='how many impossible sites to list')
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
    con = open_readonly(args.db)
    try:
        sites = published_sites(con, since, until)
        bad = impossible(sites, args.width_m)
        units = sorted({site['wialon_id'] for site in sites})
        kinds = kinds_of(con, units)
        names = unit_names(con, sorted({site['wialon_id']
                                        for site in bad[:args.top]}))
    finally:
        con.close()
    report(sites, bad, kinds, names, since, until, args.width_m, args.top)
    print('nothing was written to the database: it was opened mode=ro')
    return 0


if __name__ == '__main__':
    sys.exit(main())
