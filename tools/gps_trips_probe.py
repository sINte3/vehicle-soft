# -*- coding: utf-8 -*-
"""GPS-33 -- разведка метода «рейсы»: заезды в пункты приёма против объёма заявок. Только чтение.

ЗАЧЕМ
Заявки agro-work с методом «рейсы» сверка пока не судит (этап 5 трека GPS): в
сентябре 73 заявки «Рейс» остались без вердикта. Правило владельца 19.10:
«если рейсы -- сверяем рейсы по посещениям геозон». Чем в треке выглядит
один рейс, ещё не видно. Маршрута у заявки нет -- только машина, окно и
число рейсов. Заявки «рейсы» -- в основном перевозка хлопка (разбор края поля
13.10: «перевозка хлопка (рейсы)», МТЗ с прицепом); пункты приёма хлопка
(ПТМ) и хлопкозаводы есть в списке территорий `gps/data/territory_zones.csv`.

Этот инструмент только показывает, сколько раз машина заезжала в каждую зону
списка в окне заявки и сколько там пробыла, рядом с объёмом заявки. Правила
рейса здесь нет: что считать рейсом -- решает владелец, после этого --
предрегистрация и проверка на сутках, которых разведка не видела.

РЕЖИМ --hauling (с 27.10). Прогон 23.10 показал: заявок «рейсы» почти нет, а
перевозки «ташиш» (пахта, тола, юк) идут в тоннах и не сверяются. Владелец
27.10 принял план ступеней: сначала GPS отвечает, была ли перевозка --
заезды в ПТМ и на хлопкозаводы у хлопка, пробег у прочих грузов; тонны --
позже, по взвешиванию из 1С. С ключом --hauling берутся заявки, у вида
работы которых в имени есть «ташиш», при любом методе; к заездам добавлен
пробег окна по правилу P (`gps/mileage.py`).

КОНТРОЛЬ --control-csv (с 27.10, только вместе с --hauling). Прогон 27.10:
у 653 из 658 заявок «пахта ташиш» есть остановка в ПТМ или на заводе. Это
ещё не признак перевозки: трактор может стоять при ПТМ и без рейса, и
тогда остановка нашлась бы и в сутки без заявки. Контроль берёт объекты
заявок перевозок периода и каждые их сутки периода с точками:
  hauling    -- сутки внутри окна заявки «ташиш» этой машины;
  other_app  -- внутри окна только других заявок этой машины;
  no_app     -- ни одной заявки.
Группа суток: cotton -- у перевозки суток в имени вида «пахта» или «тола»;
у суток без перевозки -- если у машины в периоде есть такая перевозка;
иначе other. Окно заявки, начатой до периода, сутки периода тоже
покрывает. По суткам -- остановки в пунктах приёма от 5 минут и пробег.
В консоль -- по группе и классу: суток, с остановкой, с пробегом от 3 км.
Признак различает перевозку, только если в сутки no_app он встречается
заметно реже, чем в сутки hauling. Условие чтения предрегистрировано в
треке до прогона (§0.6, «Перевозки, ступень 1»); по этим числам оно не
двигается.

ЧТО ДЕЛАЕТ
  1. Заявки периода (как «Сверка»: `agro_work.reconcile`) с методом «рейсы»,
     их машина, объект Wialon и окно.
  2. Зоны списка территорий с типом (`tip`): ПТМ, хлопкозавод, АЗС, ...;
     контур -- из зеркала `field_contours`, как у правила территорий.
  3. Точки объекта за сутки окна -- с диска, `mode=ro`.
  4. Заезд -- непрерывный ряд точек внутри одной зоны. Заездом с въездом
     считается ряд, перед которым была точка вне этой зоны; ряд, с которого
     окно началось, -- «уже был внутри».
  5. CSV по заявкам: объём, заезды в ПТМ и хлопкозаводы (все и от 5 минут),
     в прочие зоны; CSV по заездам: зона, тип, вход, выход, минуты.
  6. В консоль -- только числа: сколько заявок, у скольких есть точки, и как
     число заездов в пункты приёма от 5 минут соотносится с объёмом (с
     --hauling -- у скольких есть остановка в пункте приёма, у скольких
     только пробег от 3 км, у скольких ни того ни другого).

ЧЕГО НЕ ДЕЛАЕТ
  НИЧЕГО НЕ ПИШЕТ: база и файлы точек открываются `mode=ro`; пишутся только
      два названных CSV (с --control-csv -- три). Оговорка та же, что у
      других инструментов: SQLite, открывая файл в режиме WAL только на
      чтение, может создать рядом пустые служебные `-wal` и `-shm`; данные
      не меняются.
  Не судит заявки и не меняет сверку.
  Не печатает имён в консоль: имена видов работ и номера заявок -- только в
      CSV.

КОДЫ ВЫХОДА
  0  готово, CSV записаны;
  2  неверный ввод: нет базы, папки точек, списка зон, даты; ничего не
     считалось;
  1  инструмент упал.

Запуск -- окружением расчёта (нужна геометрия), PowerShell, из C:\\gps-tools:

    & C:\\gps_venv\\Scripts\\python.exe -u tools\\gps_trips_probe.py --db C:\\transport-report\\instance\\transport.db --dir C:\\transport-report\\instance --from 2026-09-01 --to 2026-09-30 --csv C:\\gps-tools\\check\\trips_probe.csv --visits-csv C:\\gps-tools\\check\\trips_visits.csv *> C:\\gps-tools\\check\\trips_probe.log

Перевозки «ташиш» -- тот же запуск с ключом --hauling; контроль -- ещё и
--control-csv C:\\gps-tools\\check\\hauling_control.csv.
"""

import argparse
import csv
import os
import sys
from collections import Counter, defaultdict
from datetime import date, datetime, timedelta

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if REPO_ROOT not in sys.path:
    sys.path.insert(0, REPO_ROOT)

import numpy                                                        # noqa: E402
import shapely                                                      # noqa: E402

from agro_work import methods, store                                # noqa: E402
from agro_work import reconcile as rc                               # noqa: E402
from gps import mileage                                             # noqa: E402
from gps.area import to_utm                                         # noqa: E402
from gps.territory import SOURCE, ZONES_PATH, zone_polygon          # noqa: E402
from gps_collector import config as collector_config                # noqa: E402
from tools.gps_implausible_sites import read_day_readonly           # noqa: E402
from tools.gps_track_only_days import open_readonly                 # noqa: E402

EXIT_OK = 0
EXIT_CRASHED = 1
EXIT_BAD_INPUT = 2

# [REASON]: пункты приёма хлопка -- типы списка территорий (GPS-19): ПТМ и
# хлопкозавод. Это кандидат для разговора с владельцем, а не правило рейса:
# инструмент печатает заезды во все зоны списка, по типам.
RECEIVING_KINDS = ('ptm', 'pakhta_zavod')
# Заезд от 5 минут -- остановка, а не проезд через зону; число для разведки,
# не порог правила.
STOP_S = 300
# [REASON]: перевозки -- виды работ «... ташиш» (agro-work, §9.4 п. 4: пахта
# ташиш, юк ташиш, тола ташиш...). Метод у них «не сверяется» или не
# размечен, единица -- тонны, поэтому отбор по имени вида, а не по методу.
HAULING_MARK = 'ташиш'
# Порог «работала» у транспорта -- 3 км (владелец 09.10, §9.4 п. 1).
WORK_KM = 3.0
# [REASON]: ступень 1 перевозок (владелец 27.10, трек §0.7): у хлопка --
# «пахта» и «тола» -- перевозку подтверждают заезды в ПТМ и на хлопкозаводы,
# у прочих грузов -- пробег. Группа контроля делится по этому же слову.
COTTON_MARKS = ('пахта', 'тола')

HAULING_DAY = 'hauling'
OTHER_APP_DAY = 'other_app'
NO_APP_DAY = 'no_app'
DAY_CLASSES = (HAULING_DAY, OTHER_APP_DAY, NO_APP_DAY)
COTTON = 'cotton'
OTHER_CARGO = 'other'

APP_COLUMNS = ('nomer_zayavki', 'vid_raboty', 'wialon_id', 'kategoriya',
               'okno_s', 'okno_po', 'obem', 'edinica', 'sutok_s_tochkami',
               'tochek', 'zaezdy_ptm', 'zaezdy_ptm_5min', 'zaezdy_zavod',
               'zaezdy_zavod_5min', 'zaezdy_priem_5min', 'zaezdy_prochie',
               'km', 'prichina')
VISIT_COLUMNS = ('nomer_zayavki', 'wialon_id', 'zona_id', 'tip', 'vhod',
                 'vyhod', 'minut', 'tochek', 'vezd')
CONTROL_COLUMNS = ('wialon_id', 'den', 'klass', 'gruppa', 'vidy_rabot',
                   'zayavki', 'tochek', 'zaezdy_ptm_5min', 'zaezdy_zavod_5min',
                   'zaezdy_priem_5min', 'zaezdy_prochie', 'km')

NO_MACHINE = 'net_obekta_wialon'
NO_POINTS = 'net_tochek'


class BadInput(Exception):
    """Ключи нельзя выполнить -- код 2, ничего не считалось."""


def read_zone_kinds(path):
    """{id зоны: тип} списка территорий."""
    if not os.path.isfile(path):
        raise BadInput('zone list not found: %s' % path)
    kinds = {}
    with open(path, encoding='utf-8-sig', newline='') as handle:
        reader = csv.DictReader(handle)
        if 'zona_id' not in (reader.fieldnames or []):
            raise BadInput('%s has no zona_id column' % path)
        for row in reader:
            value = (row.get('zona_id') or '').strip()
            if value.isdigit():
                kinds[int(value)] = (row.get('tip') or '').strip() or '-'
    if not kinds:
        raise BadInput('%s lists no zones' % path)
    return kinds


def load_zones(con, kinds):
    """([(id, тип, многоугольник UTM)], [id зон без пригодного контура])."""
    ids = sorted(kinds)
    marks = ','.join('?' * len(ids))
    outlines = {}
    for external, geometry in con.execute(
            'SELECT external_id, geometry_geojson FROM field_contours '
            'WHERE source = ? AND (is_active IS NULL OR is_active = 1) '
            'AND external_id IN (%s) ORDER BY id' % marks,
            [SOURCE] + [str(zone) for zone in ids]):
        outlines[str(external)] = geometry
    zones, missing = [], []
    for zone in ids:
        geometry = outlines.get(str(zone))
        polygon = zone_polygon(geometry) if geometry else None
        if polygon is None:
            missing.append(zone)
            continue
        shapely.prepare(polygon)
        zones.append((zone, kinds[zone], polygon))
    return zones, missing


def zone_of_points(points, zones):
    """Индекс зоны (в списке zones) у каждой точки, -1 -- вне всех зон."""
    lons = numpy.array([p[1] for p in points], dtype=float)
    lats = numpy.array([p[2] for p in points], dtype=float)
    xs, ys = to_utm(lons, lats)
    where = numpy.full(len(points), -1, dtype=int)
    for index, (_zone, _kind, polygon) in enumerate(zones):
        inside = shapely.contains_xy(polygon, xs, ys) & (where < 0)
        where[inside] = index
    return where


def visits(points, zones):
    """[{zone, kind, t_in, t_out, points, entered}] по точкам, отсортированным
    по времени.

    Заезд -- непрерывный ряд точек внутри одной зоны. `entered` -- перед рядом
    была точка вне этой зоны; ряд, с которого начались точки, -- не въезд:
    машина могла стоять там с прошлых суток.
    """
    if not points:
        return []
    where = zone_of_points(points, zones)
    found = []
    current = None
    for position, index in enumerate(where):
        t = points[position][0]
        if current is not None and index == current['index']:
            current['t_out'] = t
            current['points'] += 1
            continue
        if current is not None:
            found.append(current)
            current = None
        if index >= 0:
            zone, kind, _polygon = zones[index]
            current = {'index': int(index), 'zone': zone, 'kind': kind,
                       't_in': t, 't_out': t, 'points': 1,
                       'entered': position > 0}
    if current is not None:
        found.append(current)
    for item in found:
        del item['index']
    return found


def summarize(found):
    """Числа заявки по её заездам (только с въездом)."""
    entered = [v for v in found if v['entered']]

    def count(kinds, stop=False):
        return sum(1 for v in entered if v['kind'] in kinds
                   and (not stop or v['t_out'] - v['t_in'] >= STOP_S))

    return {'zaezdy_ptm': count(('ptm',)),
            'zaezdy_ptm_5min': count(('ptm',), True),
            'zaezdy_zavod': count(('pakhta_zavod',)),
            'zaezdy_zavod_5min': count(('pakhta_zavod',), True),
            'zaezdy_priem_5min': count(RECEIVING_KINDS, True),
            'zaezdy_prochie': sum(1 for v in entered
                                  if v['kind'] not in RECEIVING_KINDS)}


def window_points(folder, unit, window):
    """(точки окна по времени, сутки с точками)."""
    first, last = window
    points, days = [], 0
    day = first
    while day <= last:
        found = read_day_readonly(folder, unit, day.isoformat()) or []
        if found:
            days += 1
            points.extend(found)
        day += timedelta(days=1)
    points.sort()
    return points, days


def local_time(stamp):
    return datetime.fromtimestamp(int(stamp), collector_config.TZ).strftime(
        '%Y-%m-%d %H:%M')


def is_hauling(name):
    return HAULING_MARK in (name or '').casefold()


def is_cotton(name):
    folded = (name or '').casefold()
    return any(mark in folded for mark in COTTON_MARKS)


def period_rows(con, since, until):
    """Строки сверки всех заявок, чьё окно задевает период, с видом работы
    и категорией машины."""
    first, last = date.fromisoformat(since), date.fromisoformat(until)
    ctx = rc.Reconciliation(con, first, last)
    rows = []
    for row in ctx.forward_rows():
        if not row['window']:
            continue
        row['work_type'] = (ctx.work_type_names.get(row['app'].work_type_id)
                            or row['app'].row.get('work_type_name') or '')
        equipment = ctx.equipment.get(row['equipment_id']) or {}
        row['category'] = equipment.get('category', '')
        rows.append(row)
    return rows


def pick_rows(rows, since, until, hauling=False):
    """Заявки с окном, начатым внутри периода: метода «рейсы», а с
    hauling -- перевозок «ташиш» при любом методе."""
    first, last = date.fromisoformat(since), date.fromisoformat(until)
    picked = []
    for row in rows:
        if hauling:
            if not is_hauling(row['work_type']):
                continue
        elif row['method'] != methods.METHOD_TRIPS:
            continue
        if first <= row['window'][0] <= last:
            picked.append(row)
    return picked


def trip_rows(con, since, until, hauling=False):
    return pick_rows(period_rows(con, since, until), since, until, hauling)


def control_days(all_rows, picked, since, until):
    """[(объект, сутки, класс, группа, заявки суток)] -- каждые сутки
    периода у каждого объекта заявок picked.

    [REASON]: сутки покрывает окно ЛЮБОЙ заявки машины, задевающей период,
    в том числе начатой до него: иначе сутки такой заявки ушли бы в no_app,
    и контроль сравнивал бы перевозку с перевозкой.
    """
    first, last = date.fromisoformat(since), date.fromisoformat(until)
    units = sorted({row['wialon_id'] for row in picked
                    if row['wialon_id'] is not None})
    by_unit = defaultdict(list)
    for row in all_rows:
        if row['wialon_id'] in units:
            by_unit[row['wialon_id']].append(row)
    cotton_units = {unit for unit, rows in by_unit.items()
                    if any(is_hauling(row['work_type']) and is_cotton(row['work_type'])
                           for row in rows)}
    days = []
    for unit in units:
        day = first
        while day <= last:
            covering = [row for row in by_unit[unit]
                        if row['window'][0] <= day <= row['window'][1]]
            hauls = [row for row in covering if is_hauling(row['work_type'])]
            if hauls:
                kind = HAULING_DAY
                cotton = any(is_cotton(row['work_type']) for row in hauls)
            else:
                kind = OTHER_APP_DAY if covering else NO_APP_DAY
                cotton = unit in cotton_units
            days.append((unit, day, kind, COTTON if cotton else OTHER_CARGO,
                         covering))
            day += timedelta(days=1)
    return days


def window_km(points):
    """Пробег окна по правилу P; без точек -- 0."""
    if not points:
        return 0.0
    track = [(p[0], p[1], p[2], p[3]) for p in points]
    return mileage.mileage(track, points[0][0], points[-1][0]).km


def worked(km):
    """Пробег, при котором транспорт «работал»: от 3 км включительно."""
    return km >= WORK_KM


def evidence(line):
    """Чем подтверждена перевозка (для разведки, не вердикт)."""
    if line['zaezdy_priem_5min']:
        return 'receiving stop'
    if worked(line['km']):
        return 'km only'
    return 'neither'


def compare(volume, hits):
    if volume is None:
        return 'no volume'
    if hits == volume:
        return 'equal'
    if abs(hits - volume) <= 1:
        return 'within 1'
    return 'more' if hits > volume else 'fewer'


def check_inputs(args):
    for option, value in (('--from', args.since), ('--to', args.until)):
        try:
            date.fromisoformat(value)
        except ValueError:
            raise BadInput('%s %s is not YYYY-MM-DD' % (option, value))
    if args.until < args.since:
        raise BadInput('--to is before --from')
    if not os.path.isfile(args.db):
        raise BadInput('database not found: %s' % args.db)
    if not os.path.isdir(args.dir):
        raise BadInput('points folder not found: %s' % args.dir)
    if args.control_csv and not args.hauling:
        raise BadInput('--control-csv needs --hauling')
    for path in (args.csv, args.visits_csv, args.control_csv):
        if not path:
            continue
        folder = os.path.dirname(os.path.abspath(path))
        if not os.path.isdir(folder) or os.path.isdir(path):
            raise BadInput('cannot write %s' % path)
    return read_zone_kinds(args.zones)


def run(args, out=print):
    kinds = check_inputs(args)
    con = open_readonly(args.db)
    try:
        if store.missing_tables(con):
            raise BadInput('agro-work tables are missing in %s' % args.db)
        zones, missing = load_zones(con, kinds)
        all_rows = period_rows(con, args.since, args.until)
        rows = pick_rows(all_rows, args.since, args.until, args.hauling)
    finally:
        con.close()
    by_kind = Counter(kind for _zone, kind, _polygon in zones)
    out('period %s .. %s | zones in the list %d, with an outline %d, without %d'
        % (args.since, args.until, len(kinds), len(zones), len(missing)))
    out('zones by type: ' + ', '.join('%s %d' % item for item in sorted(by_kind.items())))
    out('%s applications in the period: %d'
        % ('hauling' if args.hauling else 'trip', len(rows)))
    outcome, reasons, kinds_seen, proof = Counter(), Counter(), Counter(), Counter()
    app_lines, visit_lines = [], []
    for row in rows:
        app = row['app']
        line = {'nomer_zayavki': app.number, 'vid_raboty': row['work_type'],
                'wialon_id': row['wialon_id'] or '', 'kategoriya': row['category'],
                'okno_s': row['window'][0].isoformat(),
                'okno_po': row['window'][1].isoformat(),
                'obem': '' if row['volume'] is None else row['volume'],
                'edinica': app.row.get('unit') or '', 'sutok_s_tochkami': 0,
                'tochek': 0, 'prichina': ''}
        if row['wialon_id'] is None:
            line['prichina'] = NO_MACHINE
            reasons[NO_MACHINE] += 1
            app_lines.append(line)
            continue
        points, days = window_points(args.dir, row['wialon_id'], row['window'])
        line['sutok_s_tochkami'], line['tochek'] = days, len(points)
        if not points:
            line['prichina'] = NO_POINTS
            reasons[NO_POINTS] += 1
            app_lines.append(line)
            continue
        found = visits(points, zones)
        line.update(summarize(found))
        line['km'] = round(window_km(points), 2)
        proof[evidence(line)] += 1
        app_lines.append(line)
        for item in found:
            kinds_seen[item['kind']] += item['entered']
            visit_lines.append({
                'nomer_zayavki': app.number, 'wialon_id': row['wialon_id'],
                'zona_id': item['zone'], 'tip': item['kind'],
                'vhod': local_time(item['t_in']), 'vyhod': local_time(item['t_out']),
                'minut': round((item['t_out'] - item['t_in']) / 60.0, 1),
                'tochek': item['points'], 'vezd': 'da' if item['entered'] else 'net'})
        volume = None if row['volume'] is None else round(row['volume'])
        outcome[compare(volume, line['zaezdy_priem_5min'])] += 1
    write_csv(args.csv, APP_COLUMNS, app_lines)
    write_csv(args.visits_csv, VISIT_COLUMNS, visit_lines)
    out('without a Wialon object %d | without points %d | measured %d'
        % (reasons[NO_MACHINE], reasons[NO_POINTS], sum(outcome.values())))
    out('entries by zone type (with an entry): ' + (', '.join(
        '%s %d' % item for item in sorted(kinds_seen.items())) or '-'))
    if args.hauling:
        out('evidence of hauling: %s' % ' | '.join('%s %d' % (name, proof[name])
                                                  for name in ('receiving stop',
                                                               'km only',
                                                               'neither')))
    else:
        out('stops of %d+ min at receiving points vs the volume: %s' % (
            STOP_S // 60, ' | '.join('%s %d' % (name, outcome[name]) for name in (
                'equal', 'within 1', 'more', 'fewer', 'no volume'))))
    out('csv: %s' % args.csv)
    out('csv: %s' % args.visits_csv)
    if args.control_csv:
        run_control(args, all_rows, rows, zones, out)
    out('READ ONLY: nothing was written but the CSV files; this is not a verdict')
    return EXIT_OK


def run_control(args, all_rows, picked, zones, out):
    """Те же машины по суткам: перевозка, другая заявка, ничего."""
    days = control_days(all_rows, picked, args.since, args.until)
    lines = []
    tally = defaultdict(Counter)
    without = 0
    for unit, day, kind, group, covering in days:
        points = sorted(read_day_readonly(args.dir, unit, day.isoformat()) or [])
        if not points:
            without += 1
            continue
        line = {'wialon_id': unit, 'den': day.isoformat(), 'klass': kind,
                'gruppa': group,
                'vidy_rabot': ' | '.join(sorted({row['work_type'] for row in covering})),
                'zayavki': ' '.join(sorted(row['app'].number for row in covering)),
                'tochek': len(points)}
        line.update(summarize(visits(points, zones)))
        line['km'] = round(window_km(points), 2)
        lines.append(line)
        counts = tally[(group, kind)]
        counts['days'] += 1
        counts['stop'] += line['zaezdy_priem_5min'] > 0
        counts['km'] += worked(line['km'])
    write_csv(args.control_csv, CONTROL_COLUMNS, lines)
    out('control: machines %d | machine-days %d | without points %d | with points %d'
        % (len({unit for unit, *_rest in days}), len(days), without, len(lines)))
    for group in (COTTON, OTHER_CARGO):
        for kind in DAY_CLASSES:
            counts = tally[(group, kind)]
            out('control %s %s: days %d | receiving stop %d | km %g+ %d'
                % (group, kind, counts['days'], counts['stop'], WORK_KM,
                   counts['km']))
    out('csv: %s' % args.control_csv)


def write_csv(path, columns, lines):
    with open(path, 'w', encoding='utf-8-sig', newline='') as handle:
        writer = csv.DictWriter(handle, fieldnames=columns, delimiter=';',
                                extrasaction='ignore')
        writer.writeheader()
        writer.writerows(lines)


def main(argv=None, out=print):
    parser = argparse.ArgumentParser(
        description=collector_config.ascii_only(__doc__.splitlines()[0]))
    parser.add_argument('--db', required=True)
    parser.add_argument('--dir', required=True, help='folder of the point files')
    parser.add_argument('--from', dest='since', required=True)
    parser.add_argument('--to', dest='until', required=True)
    parser.add_argument('--csv', required=True, help='CSV by application')
    parser.add_argument('--visits-csv', required=True, help='CSV by zone visit')
    parser.add_argument('--zones', default=ZONES_PATH,
                        help='zone list with types (default: the territory list)')
    parser.add_argument('--hauling', action='store_true',
                        help='hauling work types (name has the transport mark) '
                             'instead of the trips method')
    parser.add_argument('--control-csv', default=None,
                        help='with --hauling: the same machines day by day '
                             '(hauling, another application, none)')
    args = parser.parse_args(argv)
    try:
        return run(args, out)
    except BadInput as problem:
        sys.stderr.write('ERROR: %s\n' % collector_config.ascii_only(str(problem)))
        return EXIT_BAD_INPUT
    except Exception as problem:                                  # noqa: BLE001
        sys.stderr.write('ERROR: %s: %s\n' % (
            type(problem).__name__, collector_config.ascii_only(str(problem))))
        return EXIT_CRASHED


if __name__ == '__main__':
    sys.exit(main())
