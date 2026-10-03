# -*- coding: utf-8 -*-
"""GPS A7: допуск сшивания участков (альфа) по машино-суткам. Только чтение.

ЗАЧЕМ
02.10.2026 владелец отверг ширину агрегата как понятие: какой агрегат шёл и
какой он ширины, в учёте не известно, и предел «ширина x скорость x время»
отчёта `tools/gps_implausible_sites.py` ведёт к недоразумениям. Невозможные
контуры при этом остались (МТЗ-80.1 80 239 NA за 26.09, объект 7286 за
13.09 -- клинья через сёла в Google Earth), и объяснять их надо без ширины.

Гипотеза сессии: причина -- в самом методе. Участки суток строятся одной
альфа-формой с допуском `max(10 м; 1,2 x шаг между проходами)`, а шаг
меряется по ВСЕМ медленным точкам суток (`gps.area.pass_spacing`). В сутки,
где медленной езды по дорогам больше, чем работы, «соседним проходом»
оказывается соседняя дорога в сотнях метров; допуск вырастает до сотен
метров, и форма затягивает всё между дорогами. Комментарий метода к
`PASS_SPACING_DETOUR_RATIO` об этом и предупреждает.

Проверить её можно по базе без геометрии: допуск и шаг записаны у каждого
участка (`alpha_used_m`, `pass_spacing_m`; допуск один на машино-сутки).
Если гипотеза верна, невозможные контуры лежат в сутках с допуском шире,
чем метод брал на любой работе с ручным замером (44,6 м = 1,2 x 37,2 м,
«5650 Гарден», опрыскивание, набор 27.07), а ответы операторов «работа» в
таких сутках редки.

ЧТО ПЕЧАТАЕТ -- только опубликованные сутки (их гектары идут в план-факт):
  1. гектары по корзинам допуска: 10 м (нижняя граница метода: шаг не
     измерен или не больше 8,3 м -- так у культивации), 10-20 (опрыскивание,
     шаг 13-14 м), 20-44,6 (до самого широкого шага с ручным замером),
     44,6-100, 100-300, больше 300 м (масштаб дорог и сёл). Объекты,
     исключённые из план-факта («не наша», непольевая категория), -- в
     отдельных столбцах: экран их скрывает, а строки в базе остаются;
  2. ответы операторов «работа» и «проезд» -- какой допуск был у этих суток;
  3. машино-сутки с самым широким допуском;
  4. `--find ТЕКСТ` -- машино-сутки периода, у которых имя машины (без учёта
     регистра и пробелов) или номер объекта содержит ТЕКСТ, с их участками;
  5. `--day ДАТА:ОБЪЕКТ` -- участки этих суток.

[REASON]: отчёт ничего не решает и ничего не пишет: база открывается
`mode=ro`. Корзины -- точки отсчёта из проверочных наборов, а не порог: что
считать невозможным, решает правка метода, объявленная письменно и
проверенная на ручных замерах владельца, а не этот отчёт.

Запуск (PowerShell, из C:\\gps-tools; геометрия не нужна -- системный Python):

    & "C:\\Program Files\\Python314\\python.exe" tools\\gps_alpha_report.py --db C:\\transport-report\\instance\\transport.db --since 2026-09-01 --find "80 080" --day 2026-09-26:393 --day 2026-09-13:7286

Отсутствующая база не создаётся (код 2). Вывод -- только ASCII: имена машин
транслитерируются.
"""

import argparse
import os
import statistics
import sys
from datetime import datetime

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if REPO_ROOT not in sys.path:
    sys.path.insert(0, REPO_ROOT)

from tools.gps_implausible_sites import kinds_of                    # noqa: E402
from tools.gps_track_only_days import (console, open_readonly,     # noqa: E402
                                       unit_names)

# [REASON]: нижняя граница допуска и множитель -- те же, что у метода
# (`gps.area.ALPHA_M`, `gps.area.ALPHA_SPACING_FACTOR`). `gps.area` тянет numpy
# и из системного Python не импортируется; тест сверяет числа с исходником.
ALPHA_FLOOR_M = 10.0
ALPHA_SPACING_FACTOR = 1.2
# [REASON]: самый широкий шаг между проходами, который метод измерил на работе
# с ручным замером владельца, -- 37,2 м («5650 Гарден», опрыскивание, таблица
# набора 27.07 в docs/GPS_PLAN_FAKT_VISION_ROADMAP.md; тест берёт максимум из
# этой таблицы). Шире этого допуска на подтверждённой работе метод не брал
# никогда, и это точка отсчёта, а не порог.
VALIDATED_MAX_SPACING_M = 37.2
VALIDATED_MAX_ALPHA_M = round(ALPHA_SPACING_FACTOR * VALIDATED_MAX_SPACING_M, 2)
# Верхние границы корзин, включительно; последняя корзина -- всё, что выше.
BUCKET_EDGES = (ALPHA_FLOOR_M, 20.0, VALIDATED_MAX_ALPHA_M, 100.0, 300.0)
# Допуск записан с тремя знаками; «ровно на границе» -- в пределах округления.
EDGE_TOLERANCE_M = 0.0005
UNKNOWN = 'unknown'
WORK, PASSAGE = 'работа', 'проезд'


def bucket_labels():
    labels = ['%g (floor)' % ALPHA_FLOOR_M]
    for low, high in zip(BUCKET_EDGES, BUCKET_EDGES[1:]):
        labels.append('%g-%g' % (low, high))
    labels.append('>%g' % BUCKET_EDGES[-1])
    labels.append(UNKNOWN)
    return labels


def bucket_of(alpha):
    """Корзина допуска: границы включаются в нижнюю корзину."""
    labels = bucket_labels()
    if alpha is None:
        return UNKNOWN
    for index, edge in enumerate(BUCKET_EDGES):
        if alpha <= edge + EDGE_TOLERANCE_M:
            return labels[index]
    return labels[len(BUCKET_EDGES)]


def machine_days(con, since, until=None):
    """Опубликованные машино-сутки с участками за период, по строке на сутки.

    Допуск и шаг метод пишет одинаковыми во все участки суток (одна
    альфа-форма на сутки); `mixed` -- признак, что это не так, и такие сутки
    отчёт называет, а не усредняет молча.
    """
    query = (
        'SELECT p.work_date, p.wialon_id, MIN(p.alpha_used_m), '
        'MAX(p.alpha_used_m), MIN(p.pass_spacing_m), MAX(p.pass_spacing_m), '
        'COUNT(*), COALESCE(SUM(p.area_ha), 0), COALESCE(MAX(p.area_ha), 0), '
        'COALESCE(SUM(p.minutes), 0), '
        'SUM(CASE WHEN p.operator_label = ? THEN 1 ELSE 0 END), '
        'SUM(CASE WHEN p.operator_label = ? THEN 1 ELSE 0 END), '
        'a.track_km, a.points_work '
        'FROM gps_work_polygons p '
        'JOIN gps_daily_aggregates a ON a.work_date = p.work_date '
        'AND a.wialon_id = p.wialon_id AND a.reason IS NULL '
        'WHERE p.work_date >= ?')
    args = [WORK, PASSAGE, since]
    if until is not None:
        query += ' AND p.work_date <= ?'
        args.append(until)
    query += ' GROUP BY p.work_date, p.wialon_id'
    rows = []
    for (day, unit, alpha_min, alpha_max, spacing_min, spacing_max, sites,
         area, largest, minutes, work, passage, km, points_work) \
            in con.execute(query, args):
        rows.append({
            'day': str(day), 'wialon_id': int(unit),
            'alpha': None if alpha_max is None else float(alpha_max),
            'mixed': (alpha_min != alpha_max
                      or spacing_min != spacing_max),
            'spacing': None if spacing_max is None else float(spacing_max),
            'sites': int(sites), 'ha': float(area), 'largest': float(largest),
            'minutes': float(minutes), 'work': int(work or 0),
            'passage': int(passage or 0), 'km': km, 'points_work': points_work,
        })
    return rows


def sites_of(con, day, unit):
    return [{'site': site, 'ha': float(area or 0.0), 'minutes': minutes,
             'contour': contour, 'alpha': alpha, 'spacing': spacing,
             'suggested': suggested, 'operator': operator}
            for site, area, minutes, contour, alpha, spacing, suggested,
            operator in con.execute(
                'SELECT site_number, area_ha, minutes, contour_id, '
                'alpha_used_m, pass_spacing_m, suggested_label, '
                'operator_label FROM gps_work_polygons '
                'WHERE work_date = ? AND wialon_id = ? ORDER BY site_number',
                (day, unit))]


def reason_of(con, day, unit):
    row = con.execute('SELECT reason FROM gps_daily_aggregates '
                      'WHERE work_date = ? AND wialon_id = ?',
                      (day, unit)).fetchone()
    if row is None:
        return None, False
    return row[0], True


def summarise(days, kinds):
    """Корзина -> счётчики; исключённые объекты -- в отдельных полях."""
    table = {label: {'days': 0, 'sites': 0, 'ha': 0.0, 'field_ha': 0.0,
                     'work': 0, 'passage': 0, 'hidden_days': 0,
                     'hidden_ha': 0.0}
             for label in bucket_labels()}
    for row in days:
        cell = table[bucket_of(row['alpha'])]
        if kinds.get(row['wialon_id']) == 'excluded':
            # [REASON]: экран такие объекты скрывает, в план-факт их гектары
            # не идут -- но это машины, которые только ездят по дорогам, и
            # их допуск показывает тот же механизм в чистом виде.
            cell['hidden_days'] += 1
            cell['hidden_ha'] += row['ha']
            continue
        cell['days'] += 1
        cell['sites'] += row['sites']
        cell['ha'] += row['ha']
        if kinds.get(row['wialon_id']) == 'field':
            cell['field_ha'] += row['ha']
        cell['work'] += row['work']
        cell['passage'] += row['passage']
    return table


def answers(days, kinds, label_key):
    """Допуск суток, в которых операторы ответили `label_key` (по участку)."""
    values = []
    for row in days:
        if kinds.get(row['wialon_id']) == 'excluded':
            continue
        values.extend([row['alpha']] * row[label_key])
    known = [value for value in values if value is not None]
    above = sum(1 for value in known if wider_than_validated(value))
    return {'sites': len(values), 'known': len(known),
            'median': statistics.median(known) if known else None,
            'max': max(known) if known else None, 'above': above}


def wider_than_validated(alpha):
    return alpha is not None and alpha > VALIDATED_MAX_ALPHA_M + EDGE_TOLERANCE_M


def squeeze(text):
    return ''.join(str(text).split()).lower()


def find_days(days, names, needle):
    """Машино-сутки, у которых имя машины или номер объекта содержит `needle`."""
    wanted = squeeze(needle)
    return [row for row in days
            if wanted and (wanted in squeeze(names.get(row['wialon_id']) or '')
                           or wanted in str(row['wialon_id']))]


def _num(value, spec):
    return '-' if value is None else spec % value


def print_detail(con, day, unit, kind, name, out=print):
    reason, present = reason_of(con, day, unit)
    sites = sites_of(con, day, unit)
    if not present:
        out('%s  wialon_id %d  -- no daily row for this machine-day'
            % (day, unit))
        return
    alpha = sites[0]['alpha'] if sites else None
    spacing = sites[0]['spacing'] if sites else None
    out('%s  wialon_id %d  %s  alpha %s m  spacing %s m  day %s  %s'
        % (day, unit, kind, _num(alpha, '%.2f'), _num(spacing, '%.2f'),
           reason or 'published', console(name or '')))
    if not sites:
        out('    no sites')
        return
    out('    %4s %9s %8s %8s %8s %8s %10s %9s'
        % ('site', 'ha', 'minutes', 'contour', 'alpha', 'spacing',
           'suggested', 'operator'))
    for site in sites:
        out('    %4s %9.4f %8s %8s %8s %8s %10s %9s'
            % (site['site'], site['ha'], _num(site['minutes'], '%.1f'),
               _num(site['contour'], '%d'), _num(site['alpha'], '%.2f'),
               _num(site['spacing'], '%.2f'),
               console(site['suggested'] or '-'),
               console(site['operator'] or '-')))


def report(con, days, kinds, names, since, until, top, finds, picks,
           out=print):
    counted = [row for row in days if kinds.get(row['wialon_id']) != 'excluded']
    out('period              : %s .. %s' % (since, until or 'latest'))
    out('published machine-days with sites: %d (%d sites, %.2f ha); '
        'excluded objects, hidden on the screen: %d'
        % (len(counted), sum(row['sites'] for row in counted),
           sum(row['ha'] for row in counted), len(days) - len(counted)))
    out('alpha (stitching tolerance) = max(%g m; %g x pass spacing), one per '
        'machine-day' % (ALPHA_FLOOR_M, ALPHA_SPACING_FACTOR))
    out('widest alpha the method took on work with a manual measurement: '
        '%g m (spacing %g m, 27.07 set)' % (VALIDATED_MAX_ALPHA_M,
                                            VALIDATED_MAX_SPACING_M))
    mixed = [row for row in days if row['mixed']]
    out('machine-days whose sites disagree on alpha or spacing: %d'
        % len(mixed))
    out('')
    table = summarise(days, kinds)
    total_ha = sum(cell['ha'] for cell in table.values())
    out('%-12s %6s %6s %10s %6s %10s %7s %7s %7s %10s'
        % ('alpha, m', 'days', 'sites', 'ha', 'ha%', 'field_ha', 'op_work',
           'op_pass', 'hid_day', 'hidden_ha'))
    for label in bucket_labels():
        cell = table[label]
        share = 100.0 * cell['ha'] / total_ha if total_ha else 0.0
        out('%-12s %6d %6d %10.2f %6.1f %10.2f %7d %7d %7d %10.2f'
            % (label, cell['days'], cell['sites'], cell['ha'], share,
               cell['field_ha'], cell['work'], cell['passage'],
               cell['hidden_days'], cell['hidden_ha']))
    out('%-12s %6d %6d %10.2f %6.1f %10.2f %7d %7d %7d %10.2f'
        % ('total', sum(c['days'] for c in table.values()),
           sum(c['sites'] for c in table.values()), total_ha,
           100.0 if total_ha else 0.0,
           sum(c['field_ha'] for c in table.values()),
           sum(c['work'] for c in table.values()),
           sum(c['passage'] for c in table.values()),
           sum(c['hidden_days'] for c in table.values()),
           sum(c['hidden_ha'] for c in table.values())))
    above = sum(row['ha'] for row in counted if wider_than_validated(row['alpha']))
    out('')
    out('hectares on machine-days with alpha above %g m: %.2f of %.2f (%.1f%%)'
        % (VALIDATED_MAX_ALPHA_M, above, total_ha,
           100.0 * above / total_ha if total_ha else 0.0))
    out('')
    out('operator answers, by site (alpha of its machine-day):')
    for title, key in (('work', 'work'), ('passage', 'passage')):
        found = answers(days, kinds, key)
        out('  %-8s: %d sites, alpha median %s m, max %s m, above %g m: %d'
            % (title, found['sites'], _num(found['median'], '%.2f'),
               _num(found['max'], '%.2f'), VALIDATED_MAX_ALPHA_M,
               found['above']))
    out('')
    # [REASON]: в списке -- только сутки, чьи гектары идут в план-факт.
    # Исключённые объекты (легковые, грузовики) только ездят по дорогам, их
    # допуск шире всех, и они вытеснили бы из списка трактор, у которого
    # такие сутки и есть вопрос; их счёт -- в столбцах hid_day и hidden_ha.
    ranked = sorted(counted, key=lambda row: (
        -(row['alpha'] if row['alpha'] is not None else -1.0), -row['ha'],
        row['day'], row['wialon_id']))[:max(0, top)]
    out('top %d counted machine-days by alpha:' % len(ranked))
    out('%-10s %9s %-10s %8s %8s %5s %9s %9s %7s  %s'
        % ('date', 'wialon_id', 'kind', 'alpha', 'spacing', 'sites', 'ha',
           'largest', 'km', 'name'))
    for row in ranked:
        out('%-10s %9d %-10s %8s %8s %5d %9.2f %9.2f %7s  %s'
            % (row['day'], row['wialon_id'], kinds.get(row['wialon_id'], '-'),
               _num(row['alpha'], '%.2f'), _num(row['spacing'], '%.2f'),
               row['sites'], row['ha'], row['largest'],
               _num(row['km'], '%.1f'),
               console(names.get(row['wialon_id']) or '')))
    for needle in finds:
        found = sorted(find_days(days, names, needle),
                       key=lambda row: (row['day'], row['wialon_id']))
        out('')
        out('find %s: %d machine-day(s)' % (console(repr(needle)), len(found)))
        for row in found:
            print_detail(con, row['day'], row['wialon_id'],
                         kinds.get(row['wialon_id'], '-'),
                         names.get(row['wialon_id']), out)
    for day, unit in picks:
        out('')
        out('day %s:%d' % (day, unit))
        print_detail(con, day, unit, kinds.get(unit, '-'), names.get(unit), out)


def _day(option, value):
    try:
        return datetime.strptime(value, '%Y-%m-%d').strftime('%Y-%m-%d')
    except ValueError:
        sys.stderr.write('ERROR: %s must look like YYYY-MM-DD\n' % option)
        return None


def _pick(value):
    day, _, unit = value.partition(':')
    try:
        return (datetime.strptime(day, '%Y-%m-%d').strftime('%Y-%m-%d'),
                int(unit))
    except ValueError:
        sys.stderr.write('ERROR: --day must look like YYYY-MM-DD:WIALON_ID, '
                         'got %s\n' % console(value))
        return None


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument('--db', required=True, help='path to transport.db')
    parser.add_argument('--since', required=True, help='first date, YYYY-MM-DD')
    parser.add_argument('--until', help='last date, YYYY-MM-DD (default: none)')
    parser.add_argument('--top', type=int, default=30,
                        help='how many machine-days to list by alpha')
    parser.add_argument('--find', action='append', default=[],
                        help='text in the machine name or the object number')
    parser.add_argument('--day', action='append', default=[],
                        help='DATE:WIALON_ID whose sites to print')
    args = parser.parse_args(argv)
    since = _day('--since', args.since)
    until = None if args.until is None else _day('--until', args.until)
    if since is None or (args.until is not None and until is None):
        return 2
    picks = [_pick(value) for value in args.day]
    if None in picks:
        return 2
    if not os.path.isfile(args.db):
        sys.stderr.write('ERROR: no database at %s\n' % args.db)
        return 2
    con = open_readonly(args.db)
    try:
        days = machine_days(con, since, until)
        units = sorted({row['wialon_id'] for row in days}
                       | {unit for _day_, unit in picks})
        kinds = kinds_of(con, units)
        names = unit_names(con, units)
        report(con, days, kinds, names, since, until, args.top, args.find,
               picks)
    finally:
        con.close()
    print('')
    print('nothing was written: the database was opened mode=ro')
    return 0


if __name__ == '__main__':
    sys.exit(main())
