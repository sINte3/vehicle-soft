# -*- coding: utf-8 -*-
"""GPS A1: сутки спецтехники по датам и причинам. Только чтение.

Для объектов «след без гектаров» (`gps.exclusion.track_only_units` -- то же
правило, что у суточного расчёта и у инвентаря) печатает по каждой дате с
`--since`: сколько суточных строк с какой причиной и сколько гектаров на
опубликованных сутках (причина пуста). После правила A1 гектаров у этих
объектов быть не должно, а сутки со следом -- с причиной `spetstekhnika`.

[REASON]: догон (`gps.daily --catch-up`) приводит к правилу окно, которое
кончается вчера, а инвентарь (`tools/gps_units_inventory.py`) считает сутки
от даты БЕЗ конца. 30.09.2026 они разошлись на 13 суток (453 против 440), и
понять, где эти сутки лежат, можно было только разбивкой по датам. Итог
«counted» здесь -- ровно столбец `days_computed` инвентаря: опубликованные
сутки плюс `spetstekhnika`.

Запуск (PowerShell, из C:\\gps-tools):

    & "C:\\Program Files\\Python314\\python.exe" tools\\gps_track_only_days.py --db C:\\gps-tools\\check\\transport_a1.db --since 2026-08-29

База открывается `mode=ro`: ничего не пишется, отсутствующая база не
создаётся (код 2). Вывод -- только ASCII.
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

from gps.exclusion import REASON_TRACK_ONLY, excluded_units, track_only_units  # noqa: E402

REASON_NO_POINTS = 'net_tochek'
REASON_INCOMPLETE = 'sbor_nepolnyy'
# Столбцы вывода: опубликованные (причина пуста), след без гектаров, две
# причины, которые расчёт не трогает правилом, и все прочие отказы вместе.
COLUMNS = ('published', REASON_TRACK_ONLY, REASON_NO_POINTS,
           REASON_INCOMPLETE, 'other')


def open_readonly(path):
    """Соединение, которым нельзя писать; отсутствующая база не создаётся."""
    return sqlite3.connect('file:%s?mode=ro' % path, uri=True, timeout=30)


def column_of(reason):
    if reason is None:
        return 'published'
    if reason in (REASON_TRACK_ONLY, REASON_NO_POINTS, REASON_INCOMPLETE):
        return reason
    return 'other'


def days_by_date(con, since):
    """(units, table): объекты правила и {дата: {столбец: строк, 'ha': га}}.

    Объекты -- как у расчёта: «след без гектаров» за вычетом исключённых
    (исключение сильнее, и инвентарь такие объекты в `bez_ga` не относит).
    """
    excluded = excluded_units(con)
    units = sorted(unit for unit in track_only_units(con)
                   if unit not in excluded)
    table = defaultdict(lambda: defaultdict(float))
    if not units:
        return units, table
    marks = ','.join('?' * len(units))
    for day, reason, rows in con.execute(
            'SELECT work_date, reason, COUNT(*) FROM gps_daily_aggregates '
            'WHERE work_date >= ? AND wialon_id IN (%s) '
            'GROUP BY work_date, reason' % marks, [since] + units):
        table[str(day)][column_of(reason)] += rows
    for day, area in con.execute(
            'SELECT p.work_date, COALESCE(SUM(p.area_ha), 0) '
            'FROM gps_work_polygons p '
            'JOIN gps_daily_aggregates a ON a.work_date = p.work_date '
            'AND a.wialon_id = p.wialon_id AND a.reason IS NULL '
            'WHERE p.work_date >= ? AND p.wialon_id IN (%s) '
            'GROUP BY p.work_date' % marks, [since] + units):
        table[str(day)]['ha'] += float(area or 0.0)
    return units, table


def report(units, table, since, out=print):
    out('track-only objects : %d' % len(units))
    out('since              : %s (no end date, as the inventory counts)' % since)
    out('')
    out('%-12s %10s %14s %11s %14s %6s %9s'
        % (('date',) + COLUMNS + ('ha',)))
    totals = defaultdict(float)
    for day in sorted(table):
        row = table[day]
        out('%-12s %10d %14d %11d %14d %6d %9.2f'
            % ((day,) + tuple(int(row[c]) for c in COLUMNS) + (row['ha'],)))
        for key, value in row.items():
            totals[key] += value
    out('%-12s %10d %14d %11d %14d %6d %9.2f'
        % (('total',) + tuple(int(totals[c]) for c in COLUMNS)
           + (totals['ha'],)))
    counted = int(totals['published'] + totals[REASON_TRACK_ONLY])
    out('')
    out('%-41s: %d  <- days_computed of the inventory'
        % ('counted days (published + %s)' % REASON_TRACK_ONLY, counted))
    out('%-41s: %.2f  <- must be 0 after rule A1'
        % ('hectares on published days', totals['ha']))
    return counted, totals['ha']


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument('--db', required=True, help='path to transport.db')
    parser.add_argument('--since', required=True, help='first date, YYYY-MM-DD')
    args = parser.parse_args(argv)
    try:
        since = datetime.strptime(args.since, '%Y-%m-%d').strftime('%Y-%m-%d')
    except ValueError:
        sys.stderr.write('ERROR: --since must look like YYYY-MM-DD\n')
        return 2
    if not os.path.isfile(args.db):
        sys.stderr.write('ERROR: no database at %s\n' % args.db)
        return 2
    con = open_readonly(args.db)
    try:
        units, table = days_by_date(con, since)
    finally:
        con.close()
    report(units, table, since)
    print('nothing was written to the database: it was opened mode=ro')
    return 0


if __name__ == '__main__':
    sys.exit(main())
