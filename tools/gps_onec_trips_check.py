# -*- coding: utf-8 -*-
"""GPS-34 -- проверка связки «рейс 1С -- наша машина» по трекам. Только чтение.

ЗАЧЕМ
GPS сам перевозку хлопка не подтверждает: контроль 27.10 (трек §0.6) -- NOT
MET, хлопковые тракторы бывают в ПТМ почти каждый рабочий день, с заявкой и
без. Второй источник -- 1С: в отчёте «Вывоз по дате взвешивания брутто»
(рейсы ПТМ -> завод) и в подробном реестре ПК-17 (приём хлопка на ПТМ)
у рейса есть госномер машины. Разведка 10--12.10 (журнал трека 27.10, «1С:
где машина») нашла по этим номерам наши тракторы. Прежде чем сверка возьмёт
рейс 1С доказательством перевозки, надо проверить саму связку: верно ли
госномер из 1С находит нашу машину и её объект Wialon.

ЧТО ДЕЛАЕТ (по предрегистрации 27.10, трек §0.6, «Перевозки, ступень 2»)
  1. Читает выгрузки 1С (xlsx, первый лист, заголовок -- первая строка):
     --vyvoz -- «Пункт приема», «Гос номер (из названия)», «Время первого
     взвешивания»; --reestr -- «Пункт приема», «Транспорт», «Дата брутто».
  2. Госномер -> `plate_norm.normalize_plate`. В реестре номер вида «2
     цифры, 1--3 буквы, 3 цифры» -- серия впереди («80 DA 609») -- буквы
     переносятся в конец («80609DA»). Машина -- ровно одна наша строка
     техники с этим номером и ровно один её объект Wialon (строка
     сопоставления без пометки «не наша»); иначе рейс исключён с причиной.
  3. Положение машины в момент рейса -- её ближайшая точка в пределах ±10
     минут, с диска, `mode=ro`. Нет точки -- рейс «без GPS».
  4. Место пункта -- медиана положений его рейсов (широта и долгота
     порознь); пункт с меньше чем 5 рейсами с GPS не считается.
     C1 -- доля рейсов не дальше 1 км от места своего пункта.
  5. Отрицательный контроль -- та же мера, когда каждому рейсу дана машина
     ближайшего по времени рейса с другим пунктом и другой машиной.
  6. Справочно -- машино-сутки заявок «пахта ташиш» с рейсом в 1С (только
     сутки, которые выгрузки покрывают: сутки реестра ПК-17), машино-сутки
     рейсов 1С без единой заявки и C1 по видам рейсов -- ни одно из этого
     не условие вердикта.
  7. CSV по рейсам и вердикт строкой `VERDICT ONEC JOIN: MET | NOT MET |
     NOT EVALUATED`.

ВЕРДИКТ
  MET            -- C1 >= 90 %;
  NOT MET        -- C1 < 90 %;
  NOT EVALUATED  -- рейсов в счёте меньше 200, или «без GPS» больше 20 %
                    рейсов с машиной, или контроль даёт C1 >= 50 % (мера не
                    отличает верную связку от неверной).

ЧЕГО НЕ ДЕЛАЕТ
  НИЧЕГО НЕ ПИШЕТ, кроме CSV: база и файлы точек открываются `mode=ro`.
      SQLite, открывая файл в режиме WAL только на чтение, может создать
      рядом пустые служебные `-wal` и `-shm`; данные не меняются.
  Не судит заявки и не меняет сверку.
  Не читает из 1С ничего, кроме трёх колонок: фермер, товаровед и прочие
      колонки выгрузки не открываются и в CSV не попадают.
  Не печатает в консоль ни названий, ни номеров -- только числа.
  В Wialon не ходит.

КОДЫ ВЫХОДА
  0  готово, CSV записан, вердикт напечатан;
  2  неверный ввод: нет базы, папки точек, файла, колонки; ничего не
     считалось;
  1  инструмент упал.

ТОЛЬКО STDLIB, системный Python, без Flask. Запуск (PowerShell, по одной
команде на строку, из C:\\gps-tools):

  & "C:\\Program Files\\Python314\\python.exe" tools\\gps_onec_trips_check.py --db C:\\transport-report\\instance\\transport.db --dir C:\\transport-report\\instance --vyvoz C:\\gps-tools\\check\\onec_vyvoz_po_brutto_2026-10-01_03.xlsx --reestr C:\\gps-tools\\check\\onec_pk17_reestr_2026-10-01_03.xlsx --out C:\\gps-tools\\check\\onec_trips_check.csv *> C:\\gps-tools\\check\\onec_trips_check.log

Ключи --vyvoz и --reestr повторяются: несколько файлов -- одна проверка.
"""

import argparse
import bisect
import csv
import math
import os
import re
import sqlite3
import statistics
import sys
import xml.etree.ElementTree as ET
import zipfile
from collections import Counter, defaultdict
from datetime import datetime, timedelta

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if REPO_ROOT not in sys.path:
    sys.path.insert(0, REPO_ROOT)

from agro_work import reconcile as rc                               # noqa: E402
from agro_work import store                                         # noqa: E402
from gps.hauling import is_cotton, is_hauling                       # noqa: E402
from gps_collector import config as collector_config                # noqa: E402
from plate_norm import normalize_plate                              # noqa: E402
from tools.gps_implausible_sites import read_day_readonly           # noqa: E402
from tools.gps_track_only_days import open_readonly                 # noqa: E402

EXIT_OK = 0
EXIT_CRASHED = 1
EXIT_BAD_INPUT = 2

# Числа предрегистрации 27.10 (трек §0.6, «Перевозки, ступень 2»). По
# результатам проверки не двигаются.
WINDOW_S = 600
NEAR_M = 1000.0
MIN_TRIPS_PER_POINT = 5
BOUND = 0.90
MIN_COUNTED = 200
MAX_NO_GPS = 0.20
CONTROL_LIMIT = 0.50

MET = 'MET'
NOT_MET = 'NOT MET'
NOT_EVALUATED = 'NOT EVALUATED'

VYVOZ = 'vyvoz'
REESTR = 'reestr'
COLUMNS = {
    VYVOZ: ('Пункт приема', 'Гос номер (из названия)', 'Время первого взвешивания'),
    REESTR: ('Пункт приема', 'Транспорт', 'Дата брутто'),
}

NO_TIME = 'net_vremeni'
NO_PLATE = 'net_nomera'
NOT_OURS = 'ne_nasha'
MANY_MACHINES = 'neskolko_mashin'
NO_OBJECT = 'net_obekta'
MANY_OBJECTS = 'neskolko_obektov'
NO_GPS = 'bez_gps'

CSV_COLUMNS = ('vid', 'fail', 'stroka', 'punkt', 'gosnomer', 'gosnomer_svyortka',
               'vremya', 'wialon_id', 'prichina', 'shirota', 'dolgota',
               'do_mesta_m', 'kontrol_wialon_id', 'kontrol_do_mesta_m')

SHEET_NS = '{http://schemas.openxmlformats.org/spreadsheetml/2006/main}'
REL_NS = '{http://schemas.openxmlformats.org/officeDocument/2006/relationships}'
PKG_NS = '{http://schemas.openxmlformats.org/package/2006/relationships}'

TIME_FORMATS = ('%Y-%m-%d %H:%M:%S', '%Y-%m-%d %H:%M', '%d.%m.%Y %H:%M:%S',
                '%d.%m.%Y %H:%M')


class BadInput(Exception):
    """Ключи нельзя выполнить -- код 2, ничего не считалось."""


# --- xlsx без сторонних библиотек ------------------------------------------------

def _column_index(ref):
    letters = re.match(r'[A-Z]+', ref or '')
    if not letters:
        return None
    index = 0
    for char in letters.group(0):
        index = index * 26 + ord(char) - 64
    return index - 1


def _first_sheet(book):
    """Путь первого листа внутри книги."""
    workbook = ET.fromstring(book.read('xl/workbook.xml'))
    sheet = next(workbook.iter(SHEET_NS + 'sheet'), None)
    if sheet is not None and 'xl/_rels/workbook.xml.rels' in book.namelist():
        wanted = sheet.get(REL_NS + 'id')
        rels = ET.fromstring(book.read('xl/_rels/workbook.xml.rels'))
        for rel in rels.iter(PKG_NS + 'Relationship'):
            if rel.get('Id') == wanted:
                target = rel.get('Target').lstrip('/')
                return target if target.startswith('xl/') else 'xl/' + target
    return 'xl/worksheets/sheet1.xml'


def read_xlsx(path):
    """Строки первого листа как словари по заголовку первой строки.

    [REASON]: openpyxl есть не в каждом окружении сервера, а инструмент
    запускается системным Python; книга xlsx -- zip с XML, и трёх колонок
    из неё хватает стандартной библиотеки.
    """
    try:
        book = zipfile.ZipFile(path)
    except (zipfile.BadZipFile, OSError) as problem:
        raise BadInput('%s is not an xlsx file (%s)'
                       % (os.path.basename(path), type(problem).__name__))
    with book:
        shared = []
        if 'xl/sharedStrings.xml' in book.namelist():
            for item in ET.fromstring(book.read('xl/sharedStrings.xml')).iter(
                    SHEET_NS + 'si'):
                shared.append(''.join(t.text or '' for t in item.iter(SHEET_NS + 't')))
        sheet = ET.fromstring(book.read(_first_sheet(book)))
    table = []
    for row in sheet.iter(SHEET_NS + 'row'):
        cells, position = {}, -1
        for cell in row.iter(SHEET_NS + 'c'):
            index = _column_index(cell.get('r'))
            position = position + 1 if index is None else index
            kind = cell.get('t')
            if kind == 'inlineStr':
                value = ''.join(t.text or '' for t in cell.iter(SHEET_NS + 't'))
            else:
                found = cell.find(SHEET_NS + 'v')
                value = None if found is None else found.text
                if kind == 's' and value is not None:
                    value = shared[int(value)]
            cells[position] = value
        table.append(cells)
    if not table:
        return [], []
    header = {index: (name or '').strip() for index, name in table[0].items()}
    rows = [{header[index]: value for index, value in cells.items()
             if index in header} for cells in table[1:]]
    return [name for _index, name in sorted(header.items())], rows


def parse_time(value):
    """Местное время рейса или None.

    Текст -- как его пишет 1С или копия отчёта; число -- серийная дата Excel.
    """
    text = (value or '').strip()
    for spec in TIME_FORMATS:
        try:
            return datetime.strptime(text, spec)
        except ValueError:
            pass
    try:
        serial = float(text)
    except ValueError:
        return None
    if not 20000 < serial < 80000:
        return None
    return datetime(1899, 12, 30) + timedelta(seconds=round(serial * 86400))


# --- связка номера ----------------------------------------------------------------

def plate_key(kind, raw):
    """Номер для сравнения с нашим справочником по правилу предрегистрации."""
    key = normalize_plate(raw)
    if kind == REESTR:
        # [REASON]: реестр ПК-17 пишет серию перед цифрами («80 DA 609»), наш
        # справочник -- после («80 609 DA»). Без переноса разведка 10--12.10
        # нашла 8 наших машин из 94.
        found = re.fullmatch(r'(\d{2})([A-Z]{1,3})(\d{3})', key)
        if found:
            return found.group(1) + found.group(3) + found.group(2)
    return key


def machine_index(con):
    """({номер: {id техники}}, {id техники: {объект Wialon}})."""
    by_plate = defaultdict(set)
    for equipment_id, plate in con.execute('SELECT id, plate FROM equipment'):
        key = normalize_plate(plate)
        if key:
            by_plate[key].add(equipment_id)
    units = defaultdict(set)
    for equipment_id, wialon_id in con.execute(
            'SELECT equipment_id, wialon_id FROM vialon_mappings '
            'WHERE equipment_id IS NOT NULL AND wialon_id IS NOT NULL '
            'AND (skip IS NULL OR skip = 0)'):
        units[equipment_id].add(int(wialon_id))
    return by_plate, units


def resolve(plate, by_plate, units):
    """(объект Wialon, причина). Причина '' -- машина найдена."""
    if not plate:
        return None, NO_PLATE
    machines = by_plate.get(plate, set())
    if not machines:
        return None, NOT_OURS
    if len(machines) > 1:
        return None, MANY_MACHINES
    objects = units.get(next(iter(machines)), set())
    if not objects:
        return None, NO_OBJECT
    if len(objects) > 1:
        return None, MANY_OBJECTS
    return next(iter(objects)), ''


def read_trips(path, kind):
    header, rows = read_xlsx(path)
    missing = [name for name in COLUMNS[kind] if name not in header]
    if missing:
        raise BadInput('%s: %d expected column(s) missing'
                       % (os.path.basename(path), len(missing)))
    point_column, plate_column, time_column = COLUMNS[kind]
    trips = []
    for number, row in enumerate(rows, start=2):
        if not any((value or '').strip() for value in row.values()):
            continue
        point = ' '.join((row.get(point_column) or '').split())
        raw = (row.get(plate_column) or '').strip()
        trips.append({'kind': kind, 'file': os.path.basename(path), 'row': number,
                      'point': point, 'point_key': point.casefold(),
                      'plate_raw': raw, 'plate': plate_key(kind, raw),
                      'time': parse_time(row.get(time_column))})
    return trips


# --- положение и мера ---------------------------------------------------------------

def local_day(stamp):
    return datetime.fromtimestamp(stamp, collector_config.TZ).strftime('%Y-%m-%d')


def position(folder, unit, when, cache):
    """(широта, долгота) ближайшей точки объекта в пределах ±10 минут или None."""
    stamp = int(when.replace(tzinfo=collector_config.TZ).timestamp())
    best = None
    for day in sorted({local_day(stamp - WINDOW_S), local_day(stamp + WINDOW_S)}):
        key = (unit, day)
        if key not in cache:
            found = read_day_readonly(folder, unit, day) or []
            cache[key] = ([point[0] for point in found], found)
        stamps, found = cache[key]
        at = bisect.bisect_left(stamps, stamp)
        for index in (at - 1, at):
            if 0 <= index < len(found):
                gap = abs(stamps[index] - stamp)
                if gap <= WINDOW_S and (best is None or gap < best[0]):
                    best = (gap, found[index][2], found[index][1])
    return None if best is None else (best[1], best[2])


def distance_m(a, b):
    """Расстояние по дуге большого круга, метры."""
    lat1, lon1, lat2, lon2 = map(math.radians, (a[0], a[1], b[0], b[1]))
    h = (math.sin((lat2 - lat1) / 2) ** 2
         + math.cos(lat1) * math.cos(lat2) * math.sin((lon2 - lon1) / 2) ** 2)
    return 2 * 6371000.0 * math.asin(math.sqrt(h))


def is_near(metres):
    """Рейс у своего пункта: не дальше 1 км включительно."""
    return metres <= NEAR_M


def measure(items):
    """items -- [(пункт, положение или None)].

    (в счёте, из них рядом, {пункт: место}, [расстояние или None по items]).
    """
    by_point = defaultdict(list)
    for key, where in items:
        if where is not None:
            by_point[key].append(where)
    places = {key: (statistics.median(p[0] for p in found),
                    statistics.median(p[1] for p in found))
              for key, found in by_point.items()
              if len(found) >= MIN_TRIPS_PER_POINT}
    counted = near = 0
    distances = []
    for key, where in items:
        if where is None or key not in places:
            distances.append(None)
            continue
        metres = distance_m(where, places[key])
        distances.append(metres)
        counted += 1
        near += is_near(metres)
    return counted, near, places, distances


def control_units(trips):
    """Машина ближайшего по времени рейса с другим пунктом и другой машиной.

    [REASON]: не «следующий рейс отчёта» -- строки вывоза идут по пунктам, и
    соседняя машина стоит в той же очереди: контроль прошёл бы и при неверной
    связке (предрегистрация 27.10).
    """
    order = sorted(range(len(trips)), key=lambda i: trips[i]['time'])
    out = [None] * len(trips)
    for place, i in enumerate(order):
        trip = trips[i]
        best = None
        for step in (-1, 1):
            j = place + step
            while 0 <= j < len(order):
                other = trips[order[j]]
                if (other['point_key'] != trip['point_key']
                        and other['unit'] != trip['unit']):
                    gap = abs((other['time'] - trip['time']).total_seconds())
                    if best is None or gap < best[0]:
                        best = (gap, other['unit'])
                    break
                j += step
        out[i] = None if best is None else best[1]
    return out


def judge(counted, near, with_machine, no_gps, control_counted, control_near):
    """(вердикт, C1, контроль C1)."""
    share = near / counted if counted else None
    control = control_near / control_counted if control_counted else None
    if (counted < MIN_COUNTED or not with_machine
            or no_gps / with_machine > MAX_NO_GPS
            or control is None or control >= CONTROL_LIMIT):
        return NOT_EVALUATED, share, control
    return (MET if share >= BOUND else NOT_MET), share, control


# --- справочно: заявки agro-work --------------------------------------------------

def covered_days(trips):
    """Сутки, которые выгрузки покрывают целиком.

    [REASON]: два периода с разрывом (01--03.10 и 13--15.10) -- не один
    период 01--15.10. Прогон 27.10 считал заявки и за 04--12.10, когда
    рейсов 1С никто не выгружал, и «с рейсом 1С» вышло 202 из 700. Реестр
    ПК-17 выгружается ровно по «Дате брутто» периода, поэтому его сутки и
    есть покрытые; отчёт вывоза отбирает по второму взвешиванию, и первое
    задевает канун (30.09) -- его сутки берутся, только если реестра нет.
    """
    days = {trip['time'].date() for trip in trips
            if trip['time'] and trip['kind'] == REESTR}
    return days or {trip['time'].date() for trip in trips if trip['time']}


def application_days(con, days):
    """({(объект, сутки)} заявок «пахта ташиш», {(объект, сутки)} любых
    заявок) -- только в сутки `days`."""
    first, last = min(days), max(days)
    ctx = rc.Reconciliation(con, first, last)
    cotton, any_app = set(), set()
    for row in ctx.forward_rows():
        if not row['window'] or row['wialon_id'] is None:
            continue
        name = (ctx.work_type_names.get(row['app'].work_type_id)
                or row['app'].row.get('work_type_name') or '')
        day, end = row['window']
        while day <= end:
            if day in days:
                any_app.add((row['wialon_id'], day))
                if is_hauling(name) and is_cotton(name):
                    cotton.add((row['wialon_id'], day))
            day += timedelta(days=1)
    return cotton, any_app


# --- прогон -------------------------------------------------------------------------

def check_inputs(args):
    if not os.path.isfile(args.db):
        raise BadInput('database not found: %s' % args.db)
    if not os.path.isdir(args.dir):
        raise BadInput('points folder not found: %s' % args.dir)
    files = [(path, VYVOZ) for path in args.vyvoz] + [(path, REESTR) for path in args.reestr]
    if not files:
        raise BadInput('give at least one --vyvoz or --reestr file')
    for path, _kind in files:
        if not os.path.isfile(path):
            raise BadInput('file not found: %s' % path)
    out = os.path.abspath(args.out)
    if (not os.path.isdir(os.path.dirname(out)) or os.path.isdir(out)
            or out in {os.path.abspath(p) for p, _k in files}
            or out == os.path.abspath(args.db)):
        raise BadInput('cannot write %s' % args.out)
    return files


def run(args, out=print):
    files = check_inputs(args)
    trips = []
    for path, kind in files:
        trips.extend(read_trips(path, kind))
    con = open_readonly(args.db)
    try:
        by_plate, units = machine_index(con)
        days = covered_days(trips)
        apps = None
        if days and not store.missing_tables(con):
            apps = application_days(con, days)
    finally:
        con.close()

    reasons = Counter()
    for trip in trips:
        if trip['time'] is None:
            trip['unit'], trip['reason'] = None, NO_TIME
        else:
            trip['unit'], trip['reason'] = resolve(trip['plate'], by_plate, units)
        reasons[trip['reason'] or 'ok'] += 1
    resolved = [trip for trip in trips if trip['unit'] is not None]
    cache = {}
    for trip in resolved:
        trip['where'] = position(args.dir, trip['unit'], trip['time'], cache)
        if trip['where'] is None:
            trip['reason'] = NO_GPS
    no_gps = sum(1 for trip in resolved if trip['where'] is None)
    counted, near, places, distances = measure(
        [(trip['point_key'], trip['where']) for trip in resolved])
    for trip, metres in zip(resolved, distances):
        trip['metres'] = metres

    controls = control_units(resolved)
    control_items = []
    for trip, unit in zip(resolved, controls):
        trip['control_unit'] = unit
        where = None if unit is None else position(args.dir, unit, trip['time'], cache)
        control_items.append((trip['point_key'], where))
    control_counted, control_near, _cp, control_distances = measure(control_items)
    for trip, metres in zip(resolved, control_distances):
        trip['control_metres'] = metres

    verdict, share, control = judge(counted, near, len(resolved), no_gps,
                                    control_counted, control_near)
    write_csv(args.out, trips)

    by_kind = Counter(trip['kind'] for trip in trips)
    out('trips read: %d (removal %d, receiving %d) from %d file(s)'
        % (len(trips), by_kind[VYVOZ], by_kind[REESTR], len(files)))
    out('machine found: %d | not ours %d | several machines %d | no object %d '
        '| several objects %d | no plate %d | no time %d'
        % (len(resolved), reasons[NOT_OURS], reasons[MANY_MACHINES],
           reasons[NO_OBJECT], reasons[MANY_OBJECTS], reasons[NO_PLATE],
           reasons[NO_TIME]))
    out('without GPS within %d min: %d of %d' % (WINDOW_S // 60, no_gps, len(resolved)))
    out('points with %d+ trips with GPS: %d | trips counted: %d'
        % (MIN_TRIPS_PER_POINT, len(places), counted))
    out('C1 (within %d m of the point): %s' % (NEAR_M, _share(near, counted)))
    out('negative control C1: %s' % _share(control_near, control_counted))
    # справочно, не условие: из какого отчёта рейсы ложатся дальше
    for kind, label in ((VYVOZ, 'removal'), (REESTR, 'receiving')):
        kinds = [metres for trip, metres in zip(resolved, distances)
                 if trip['kind'] == kind and metres is not None]
        out('C1 by kind, %s: %s' % (label, _share(
            sum(1 for metres in kinds if is_near(metres)), len(kinds))))
    if apps is not None:
        cotton, any_app = apps
        out('reference days (covered by the exports): %d' % len(days))
        trip_days = {(trip['unit'], trip['time'].date()) for trip in resolved}
        out('reference: cotton hauling machine-days %d | with a 1C trip %d'
            % (len(cotton), len(cotton & trip_days)))
        for kind, label in ((VYVOZ, 'removal'), (REESTR, 'receiving')):
            kind_days = {(trip['unit'], trip['time'].date()) for trip in resolved
                         if trip['kind'] == kind}
            out('reference: %s trip machine-days %d | without any application %d'
                % (label, len(kind_days), len(kind_days - any_app)))
    out('csv: %s' % args.out)
    out('VERDICT ONEC JOIN: %s' % verdict)
    out('READ ONLY: nothing was written but the CSV file')
    return EXIT_OK


def _share(part, whole):
    if not whole:
        return '-'
    return '%d of %d = %.1f %%' % (part, whole, 100.0 * part / whole)


def _metres(value):
    return '' if value is None else round(value)


def write_csv(path, trips):
    with open(path, 'w', encoding='utf-8-sig', newline='') as handle:
        writer = csv.writer(handle, delimiter=';')
        writer.writerow(CSV_COLUMNS)
        for trip in trips:
            where = trip.get('where')
            writer.writerow((
                trip['kind'], trip['file'], trip['row'], trip['point'],
                trip['plate_raw'], trip['plate'],
                trip['time'].strftime('%Y-%m-%d %H:%M:%S') if trip['time'] else '',
                trip['unit'] if trip['unit'] is not None else '', trip['reason'],
                '' if where is None else round(where[0], 6),
                '' if where is None else round(where[1], 6),
                _metres(trip.get('metres')),
                trip.get('control_unit') if trip.get('control_unit') is not None else '',
                _metres(trip.get('control_metres'))))


def main(argv=None, out=print):
    parser = argparse.ArgumentParser(
        description=collector_config.ascii_only(__doc__.splitlines()[0]))
    parser.add_argument('--db', required=True)
    parser.add_argument('--dir', required=True, help='folder of the point files')
    parser.add_argument('--vyvoz', action='append', default=[],
                        help='1C report of removal trips by gross weighing date')
    parser.add_argument('--reestr', action='append', default=[],
                        help='1C detailed PK-17 register')
    parser.add_argument('--out', required=True, help='CSV by trip')
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
