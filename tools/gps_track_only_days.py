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

`--date` вместо `--since` раскладывает ОДНИ сутки по объектам: причина,
участки, гектары, крупнейший участок, пробег, точки в работе, прыжки, имя
машины -- то же, что показывает экран «Факт по технике».

[REASON]: 30.09.2026 проверка боевой базы до релиза дала спецтехнике 301,18 га
за одни сутки 28.09 при 0,3-23 га в любые другие. Правило A1 снимет эти
гектары первой же ночью после релиза, и если среди машин категории
«Спецтехника» окажется полевая, её работа пропадёт из план-факта молча.
Таблица по датам говорит «сколько», но не «чьи»: без разбивки по объектам
этого не решить, а решает владелец -- по именам машин.

Запуск (PowerShell, из C:\\gps-tools):

    & "C:\\Program Files\\Python314\\python.exe" tools\\gps_track_only_days.py --db C:\\gps-tools\\check\\transport_a1.db --since 2026-08-29

    & "C:\\Program Files\\Python314\\python.exe" tools\\gps_track_only_days.py --db C:\\gps-tools\\check\\transport_a1.db --date 2026-09-28

База открывается `mode=ro`: ничего не пишется, отсутствующая база не
создаётся (код 2). Вывод -- только ASCII: имена машин транслитерируются.
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
from gps_collector.config import ascii_only                        # noqa: E402

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


def unit_names(con, units):
    """wialon_id -> имя машины так, как его пишет экран «Факт по технике».

    [REASON]: правило -- копия `gps_routes._unit_names`: `equipment.name`
    вместе с госномером у первой строки сопоставления, где есть машина;
    строка без машины даёт имя объекта в Wialon. Экран -- модуль Flask, а
    инструмент, открывающий боевую базу только на чтение, приложение
    импортировать не может: `app` пишет в базу уже на импорте. Владелец ищет
    машину из вывода в списке экрана, и имя обязано совпадать.
    """
    if not units:
        return {}
    marks = ','.join('?' * len(units))
    names, from_machine = {}, set()
    for wialon_id, wialon_name, equipment_name, plate in con.execute(
            'SELECT m.wialon_id, m.vialon_name, e.name, e.plate '
            'FROM vialon_mappings m '
            'LEFT JOIN equipment e ON e.id = m.equipment_id '
            'WHERE m.wialon_id IN (%s) ORDER BY m.id' % marks, list(units)):
        if equipment_name:
            if wialon_id in from_machine:
                continue
            plate = (plate or '').strip()
            names[wialon_id] = ('%s — %s' % (equipment_name, plate) if plate
                                else equipment_name)
            from_machine.add(wialon_id)
        elif wialon_name and wialon_id not in names:
            names[wialon_id] = wialon_name
    return names


def objects_on(con, day, units):
    """Сутки `day` объектов правила: по строке на объект, у которого они есть.

    Участки, гектары и крупнейший участок -- только опубликованных суток
    (причина пуста) и тем же соединением, что у таблицы по датам: сумма по
    объектам обязана совпасть со строкой этой даты в таблице. Порядок --
    от больших гектаров к меньшим: смотреть начинают с верхней строки.
    """
    if not units:
        return []
    marks = ','.join('?' * len(units))
    rows = {}
    for wialon_id, reason, km, points_work, jumps in con.execute(
            'SELECT wialon_id, reason, track_km, points_work, gps_jumps '
            'FROM gps_daily_aggregates '
            'WHERE work_date = ? AND wialon_id IN (%s)' % marks,
            [day] + list(units)):
        rows[wialon_id] = {'wialon_id': wialon_id, 'reason': reason,
                           'km': km, 'points_work': points_work,
                           'jumps': jumps, 'sites': 0, 'ha': 0.0,
                           'largest': 0.0}
    for wialon_id, sites, area, largest in con.execute(
            'SELECT p.wialon_id, COUNT(*), COALESCE(SUM(p.area_ha), 0), '
            'COALESCE(MAX(p.area_ha), 0) '
            'FROM gps_work_polygons p '
            'JOIN gps_daily_aggregates a ON a.work_date = p.work_date '
            'AND a.wialon_id = p.wialon_id AND a.reason IS NULL '
            'WHERE p.work_date = ? AND p.wialon_id IN (%s) '
            'GROUP BY p.wialon_id' % marks, [day] + list(units)):
        rows[wialon_id].update(sites=int(sites), ha=float(area or 0.0),
                               largest=float(largest or 0.0))
    return sorted(rows.values(), key=lambda row: (-row['ha'], row['wialon_id']))


def console(text):
    """Имя машины для консоли: длинное тире -- дефис, кириллица -- латиница."""
    return ascii_only(str(text).replace('—', '-').replace('–', '-'))


def _cell(value, spec):
    return '-' if value is None else spec % value


def report_day(day, units, rows, names, table_ha, out=print):
    out('track-only objects : %d' % len(units))
    out('date               : %s (objects with a row on it: %d)'
        % (day, len(rows)))
    out('')
    out('%9s %-14s %5s %9s %10s %8s %7s %5s  %s'
        % ('wialon_id', 'reason', 'sites', 'ha', 'largest_ha', 'track_km',
           'p_work', 'jumps', 'name'))
    sites, total = 0, 0.0
    for row in rows:
        out('%9d %-14s %5d %9.2f %10.2f %8s %7s %5s  %s'
            % (row['wialon_id'], row['reason'] or 'published', row['sites'],
               row['ha'], row['largest'], _cell(row['km'], '%.1f'),
               _cell(row['points_work'], '%d'), _cell(row['jumps'], '%d'),
               console(names.get(row['wialon_id']) or '')))
        sites += row['sites']
        total += row['ha']
    out('%9s %-14s %5d %9.2f' % ('total', '', sites, total))
    out('')
    out('%-41s: %.2f  <- the row of %s in the table by date'
        % ('hectares on published days', total, day))
    # [REASON]: две выборки одного числа разными запросами. Если они
    # разошлись, разбивка объясняет не ту сумму, которую владелец видел в
    # таблице по датам, -- и говорить об этом надо в выводе, а не молчать.
    if abs(total - table_ha) > 0.005:
        out('MISMATCH: the table by date gives %.2f for %s' % (table_ha, day))
    return total


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
    when = parser.add_mutually_exclusive_group(required=True)
    when.add_argument('--since', help='first date, YYYY-MM-DD: days by date')
    when.add_argument('--date', help='one date, YYYY-MM-DD: that day by object')
    args = parser.parse_args(argv)
    option, value = ('--date', args.date) if args.date else ('--since', args.since)
    try:
        day = datetime.strptime(value, '%Y-%m-%d').strftime('%Y-%m-%d')
    except ValueError:
        sys.stderr.write('ERROR: %s must look like YYYY-MM-DD\n' % option)
        return 2
    if not os.path.isfile(args.db):
        sys.stderr.write('ERROR: no database at %s\n' % args.db)
        return 2
    con = open_readonly(args.db)
    try:
        units, table = days_by_date(con, day)
        if args.date:
            rows = objects_on(con, day, units)
            names = unit_names(con, [row['wialon_id'] for row in rows])
    finally:
        con.close()
    if args.date:
        report_day(day, units, rows, names,
                   table[day]['ha'] if day in table else 0.0)
    else:
        report(units, table, day)
    print('nothing was written to the database: it was opened mode=ro')
    return 0


if __name__ == '__main__':
    sys.exit(main())
