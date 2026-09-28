# -*- coding: utf-8 -*-
"""GPS-12 -- инвентарь объектов Wialon: что это за объект и считает ли его план-факт.

ЗАЧЕМ
Объектов в Wialon 621, а трактористов на план-факте должно быть заметно меньше.
Владельцу нужно сказать, какие объекты нужны, а какие нет, и для этого надо
видеть по каждому: как объект зовётся в Wialon, какой нашей машине он
сопоставлен, в какой она категории, сколько объект дал точек и гектаров за
последние недели -- и попадает ли он сейчас в расчёт.

ЧТО ДЕЛАЕТ
Один запрос к Wialon за списком объектов (имя и время последнего сообщения,
сообщений не грузит), затем только чтение локальных файлов: строки
сопоставления и техника из `transport.db`, точки из `instance/gps_points_*.db`,
посчитанные сутки и участки из `gps_daily_aggregates` / `gps_work_polygons`.
Пишет `gps_units_inventory.csv` и сводку в консоль.

ЧЕГО НЕ ДЕЛАЕТ
  НИЧЕГО НЕ ПИШЕТ В БАЗУ. Ни одной команды записи: все базы открываются
      строкой `file:...?mode=ro`, и SQLite отвергает запись сам, а не по
      обещанию скрипта. Решения принимаются на существующих экранах --
      сопоставление Wialon (там же галочка «нет в системе») и карточка
      техники (там категория).
  Не ставит `VialonMapping.skip`. Этот признак значит «не наша техника» и
      выключает машину ещё и из импорта моточасов
      (`wialon_import.apply_mappings` кладёт помеченные строки в `skipped`) --
      то есть из учёта соседнего трека. Легковая машина наша, просто поля не
      пашет; её исключает категория, а не `skip`.
  Не заводит и не меняет категорию: какой категории машина, решает человек.
  Не угадывает, чей объект. Имя без строки сопоставления идёт в CSV как
      `bez_stroki` -- и продолжает считаться, потому что исключить по ошибке
      хуже, чем посчитать лишнее.

КАК ЧИТАТЬ CSV
`v_plan_fakte` -- считает ли объект суточный расчёт СЕЙЧАС; `pochemu_net` --
причина, если нет. Правило одно на расчёт и на этот инструмент:
`gps/exclusion.py`, оттуда и импортируется, копии здесь нет.
`status` -- что известно об объекте:

  polevaya        машина сопоставлена и в полевой категории -- считается
  ne_polevaya     категория непольевая (легковые) -- исключён
  ne_nasha        строка сопоставления помечена «не наша техника» -- исключён
  bez_mashiny     строка есть, машина к ней не привязана -- считается
  bez_stroki      строки сопоставления нет вовсе -- считается
  protivorechie   у одного id строки говорят противоположное -- считается,
                  разбирать человеку
  ischez          в строке стоит id, которого в Wialon больше нет

ТОЛЬКО STDLIB, как и `gps_link_mappings.py`: `app = create_app()` вызывает
`db.create_all()` на импорте, и любой скрипт с `from app import app`
становится писателем схемы.

ТОЛЬКО ЧТЕНИЕ на стороне Wialon: `token/login`, `core/search_items`,
`core/logout`.

Вывод в консоль -- ASCII.

Запуск (PowerShell, по одной команде на строку):

  cd C:\\transport-report

  & "C:\\Program Files\\Python314\\python.exe" tools\\gps_units_inventory.py

Окно активности по умолчанию -- 30 суток; другое окно:

  & "C:\\Program Files\\Python314\\python.exe" tools\\gps_units_inventory.py --window-days 60
"""

import argparse
import csv
import glob
import os
import re
import sqlite3
import sys

from collections import Counter, defaultdict
from datetime import datetime, timedelta

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from gps_collector import config, storage                           # noqa: E402
from gps_collector.wialon import Client, login_failure              # noqa: E402
from gps.exclusion import (EXCLUDED_NON_FIELD, EXCLUDED_NOT_OURS,   # noqa: E402
                           NON_FIELD_CATEGORIES, excluded_units)

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DB_PATH = os.path.join(ROOT, 'instance', 'transport.db')

WINDOW_DAYS = 30

FIELD = 'polevaya'
NON_FIELD = 'ne_polevaya'
NOT_OURS = 'ne_nasha'
NO_EQUIPMENT = 'bez_mashiny'
NO_ROW = 'bez_stroki'
CONTRADICTION = 'protivorechie'
GONE = 'ischez'

# Порядок в CSV: сначала то, что требует решения, потом справка.
CSV_ORDER = (CONTRADICTION, NO_ROW, NO_EQUIPMENT, FIELD, NON_FIELD, NOT_OURS,
             GONE)

CSV_COLUMNS = ('status', 'v_plan_fakte', 'pochemu_net', 'wialon_id',
               'wialon_name', 'equipment', 'plate', 'category', 'mapping_id',
               'vialon_name', 'skip', 'last_message', 'days_with_points',
               'points_total', 'first_point', 'last_point', 'days_computed',
               'ha_total', 'km_total')


def normalize_name(text):
    """Ключ совпадения имён.

    [REASON]: то же правило, каким имя нормализует и сам импорт
    (`_normalize_wialon_name` в wialon_import.py), и связка
    (`gps_link_mappings.normalize_name`), плюс регистр. Импортировать нельзя:
    первый тянет Flask, второй -- скрипт, а не модуль. Гомоглифы латиницы и
    кириллицы НЕ сводятся: «MT3» и «МТЗ» -- не одно имя.
    """
    return re.sub(r'\s+', ' ', (text or '').strip()).casefold()


def open_readonly(path):
    """Соединение, которым нельзя писать.

    [REASON]: `mode=ro` -- запрет на уровне SQLite, а не обещание скрипта:
    любая команда записи отвечает `attempt to write a readonly database`.
    Обычный `sqlite3.connect` вместо отсутствующей базы создал бы пустую, и
    пустая база вместо боевой -- это молчаливая потеря всего справочника.
    """
    return sqlite3.connect('file:%s?mode=ro' % path, uri=True, timeout=30)


def local_stamp(epoch, fmt='%Y-%m-%d %H:%M'):
    if epoch is None:
        return ''
    return datetime.fromtimestamp(int(epoch), config.TZ).strftime(fmt)


def mapping_rows(con):
    """Строки сопоставления с машиной и её категорией, где машина указана."""
    rows = con.execute(
        'SELECT m.id, m.vialon_name, m.wialon_id, m.equipment_id, m.skip, '
        'e.name, e.plate, e.category '
        'FROM vialon_mappings m '
        'LEFT JOIN equipment e ON e.id = m.equipment_id '
        'ORDER BY m.id').fetchall()
    return [{'id': row[0], 'vialon_name': row[1] or '', 'wialon_id': row[2],
             'equipment_id': row[3], 'skip': bool(row[4]),
             'equipment': row[5] or '', 'plate': row[6] or '',
             'category': row[7] or ''} for row in rows]


def points_activity(folder, since_t):
    """unit_id -> активность по помесячным файлам точек. Только чтение.

    Сутки считаются местные: смещение зоны прибавляется к метке и делится на
    86 400 нацело, поэтому «сутки» здесь -- те же сутки, что у расчёта.
    """
    offset = int(config.TZ.utcoffset(None).total_seconds())
    activity = defaultdict(lambda: {'days': 0, 'points': 0,
                                    'first': None, 'last': None})
    files = sorted(glob.glob(os.path.join(
        folder, '%s*.db' % storage.POINTS_PREFIX)))
    for path in files:
        con = open_readonly(path)
        try:
            rows = con.execute(
                'SELECT unit_id, COUNT(*), MIN(t), MAX(t), '
                'COUNT(DISTINCT (t + ?) / 86400) '
                'FROM points WHERE t >= ? GROUP BY unit_id',
                (offset, since_t)).fetchall()
        except sqlite3.OperationalError:
            continue
        finally:
            con.close()
        for unit_id, points, first_t, last_t, days in rows:
            item = activity[int(unit_id)]
            item['points'] += int(points)
            item['days'] += int(days)
            if item['first'] is None or first_t < item['first']:
                item['first'] = first_t
            if item['last'] is None or last_t > item['last']:
                item['last'] = last_t
    return activity, files


def computed_activity(con, since_day):
    """unit_id -> сколько суток посчитано, сколько гектаров и километров."""
    out = defaultdict(lambda: {'days': 0, 'ha': 0.0, 'km': 0.0})
    try:
        rows = con.execute(
            'SELECT wialon_id, COUNT(*), COALESCE(SUM(track_km), 0) '
            'FROM gps_daily_aggregates '
            'WHERE work_date >= ? AND reason IS NULL GROUP BY wialon_id',
            (since_day,)).fetchall()
    except sqlite3.OperationalError:
        return out
    for unit_id, days, km in rows:
        item = out[int(unit_id)]
        item['days'] = int(days)
        item['km'] = float(km or 0.0)
    for unit_id, area in con.execute(
            'SELECT wialon_id, COALESCE(SUM(area_ha), 0) FROM gps_work_polygons '
            'WHERE work_date >= ? GROUP BY wialon_id', (since_day,)).fetchall():
        out[int(unit_id)]['ha'] = float(area or 0.0)
    return out


def status_of(unit_id, rows_of_unit, excluded):
    """Что известно об объекте. `rows_of_unit` -- строки сопоставления этого id."""
    why = excluded.get(unit_id)
    if why == EXCLUDED_NOT_OURS:
        return NOT_OURS
    if why == EXCLUDED_NON_FIELD:
        return NON_FIELD
    if not rows_of_unit:
        return NO_ROW
    # Объект остался в расчёте, хотя строки о нём есть. Если строки спорят --
    # это надо назвать, а не спрятать за «считается».
    marked = [row for row in rows_of_unit if row['skip']]
    non_field = [row for row in rows_of_unit
                 if row['category'] in NON_FIELD_CATEGORIES]
    if marked or non_field:
        return CONTRADICTION
    if any(row['equipment_id'] for row in rows_of_unit):
        return FIELD
    return NO_EQUIPMENT


def inventory(units, rows, excluded, activity, computed):
    """Строки CSV и сводка. Ничего не пишет."""
    by_id = defaultdict(list)
    by_key = defaultdict(list)
    for row in rows:
        if row['wialon_id'] is not None:
            by_id[int(row['wialon_id'])].append(row)
        by_key[normalize_name(row['vialon_name'])].append(row)

    items = []
    for unit in units:
        unit_id = unit['id']
        # Строка находится по id; если id ещё не проставлен связкой -- по имени.
        rows_of_unit = by_id.get(unit_id) or by_key.get(
            normalize_name(unit['name']), [])
        rows_of_unit = [row for row in rows_of_unit
                        if row['wialon_id'] in (None, unit_id)]
        status = status_of(unit_id, by_id.get(unit_id, []), excluded)
        if status == NO_ROW and rows_of_unit:
            # Строка есть, но связка ей id ещё не поставила: расчёт объект
            # считает (он работает по id), и это надо показать как есть.
            status = NO_EQUIPMENT if not any(
                row['equipment_id'] for row in rows_of_unit) else FIELD
        why = excluded.get(unit_id, '')
        points = activity.get(unit_id) or {}
        done = computed.get(unit_id) or {}
        items.append({
            'status': status,
            'v_plan_fakte': 'net' if why else 'da',
            'pochemu_net': why,
            'wialon_id': unit_id,
            'wialon_name': unit['name'],
            'equipment': ' | '.join(sorted({row['equipment'] for row in rows_of_unit
                                            if row['equipment']})),
            'plate': ' | '.join(sorted({row['plate'] for row in rows_of_unit
                                        if row['plate']})),
            'category': ' | '.join(sorted({row['category'] for row in rows_of_unit
                                           if row['category']})),
            'mapping_id': ' '.join(str(row['id']) for row in rows_of_unit),
            'vialon_name': ' | '.join(row['vialon_name'] for row in rows_of_unit),
            'skip': 1 if any(row['skip'] for row in rows_of_unit) else '',
            'last_message': local_stamp(unit['last_t']),
            'days_with_points': points.get('days', 0),
            'points_total': points.get('points', 0),
            'first_point': local_stamp(points.get('first')),
            'last_point': local_stamp(points.get('last')),
            'days_computed': done.get('days', 0),
            'ha_total': round(done.get('ha', 0.0), 2),
            'km_total': round(done.get('km', 0.0), 1)})

    # Строки, чей id в Wialon больше не существует. Это не объект парка, но
    # владельцу надо знать: трек такой машины не соберётся никогда.
    live = {unit['id'] for unit in units}
    for unit_id, rows_of_unit in sorted(by_id.items()):
        if unit_id in live:
            continue
        items.append({column: '' for column in CSV_COLUMNS} | {
            'status': GONE, 'v_plan_fakte': 'net', 'pochemu_net': GONE,
            'wialon_id': unit_id, 'wialon_name': '',
            'equipment': ' | '.join(sorted({row['equipment'] for row in rows_of_unit
                                            if row['equipment']})),
            'mapping_id': ' '.join(str(row['id']) for row in rows_of_unit),
            'vialon_name': ' | '.join(row['vialon_name'] for row in rows_of_unit),
            'days_with_points': 0, 'points_total': 0, 'days_computed': 0,
            'ha_total': 0, 'km_total': 0})

    summary = Counter(item['status'] for item in items)
    summary['objects'] = len(units)
    summary['in_plan_fakt'] = sum(1 for item in items
                                  if item['v_plan_fakte'] == 'da'
                                  and item['status'] != GONE)
    return items, summary


def write_inventory(path, items):
    order = {status: index for index, status in enumerate(CSV_ORDER)}
    ordered = sorted(items, key=lambda item: (order.get(item['status'], 99),
                                              -(item['points_total'] or 0),
                                              str(item['wialon_name'])))
    with open(path, 'w', encoding='utf-8-sig', newline='') as fh:
        writer = csv.writer(fh, delimiter=';')
        writer.writerow(CSV_COLUMNS)
        for item in ordered:
            writer.writerow(['' if item.get(column) is None else item[column]
                             for column in CSV_COLUMNS])


def category_counts(items):
    """Сколько объектов в какой категории -- чтобы владелец мог назвать ещё."""
    counts = Counter()
    for item in items:
        for category in (item['category'] or '').split(' | '):
            counts[category.strip() or '(bez kategorii)'] += 1
    return counts


def report(items, summary, categories, log=print):
    log('')
    log('objects in Wialon        : %d' % summary['objects'])
    log('counted by plan-fact     : %d' % summary['in_plan_fakt'])
    for status in CSV_ORDER:
        if summary.get(status):
            log('  %-14s : %d' % (status, summary[status]))
    log('')
    log('by equipment category (a category nobody named is still counted):')
    for name, count in sorted(categories.items(), key=lambda p: (-p[1], p[0])):
        mark = ' <- not field' if name in NON_FIELD_CATEGORIES else ''
        log('  %-18s %4d%s' % (name, count, mark))


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument('--db', default=DB_PATH)
    parser.add_argument('--dir', default=None,
                        help='where the point files live (default: instance/)')
    parser.add_argument('--pause', type=float, default=config.PAUSE_S)
    parser.add_argument('--window-days', type=int, default=WINDOW_DAYS,
                        help='how far back the activity columns look '
                             '(default %d)' % WINDOW_DAYS)
    parser.add_argument('--out', default='gps_units_inventory.csv')
    args = parser.parse_args(argv)

    if args.window_days < 1:
        sys.stderr.write('ERROR: --window-days must be at least 1\n')
        return 2
    if not os.path.exists(args.db):
        sys.stderr.write('ERROR: database not found at %s - refusing to run\n'
                         % args.db)
        return 2
    folder = args.dir or config.points_dir()

    token = config.read_token()
    client = Client(pause=args.pause)
    try:
        client.login(token)
    except Exception as problem:                                   # noqa: BLE001
        sys.stderr.write('\nERROR: ne udalos voyti na %s (%s)\n'
                         % (config.BASE_URL, login_failure(problem)))
        return 3
    print('login OK')
    try:
        units = client.list_units()
    finally:
        client.logout()
    if not units:
        # [REASON]: пустой список объектов -- это отказ, а не парк без техники:
        # 621 объект не исчезает за ночь. Принять его за «нечего описывать»
        # значило бы выдать владельцу пустой инвентарь как факт.
        print('FAILED: Wialon returned no objects - NOT treating this as '
              'an empty fleet')
        return 3
    print('objects in Wialon: %d' % len(units))

    since_t = int((datetime.now(config.TZ)
                   - timedelta(days=args.window_days)).timestamp())
    since_day = datetime.fromtimestamp(since_t, config.TZ).strftime('%Y-%m-%d')
    activity, files = points_activity(folder, since_t)
    print('point files read : %d' % len(files))

    con = open_readonly(args.db)
    try:
        rows = mapping_rows(con)
        excluded = excluded_units(con)
        computed = computed_activity(con, since_day)
    finally:
        con.close()
    print('mapping rows     : %d' % len(rows))

    items, summary = inventory(units, rows, excluded, activity, computed)
    write_inventory(args.out, items)
    report(items, summary, category_counts(items))
    print('')
    print('window           : %s .. today (%d day(s))'
          % (since_day, args.window_days))
    print('written          : %s' % args.out)
    print('')
    print('nothing was written to the database: every file was opened mode=ro')
    return 0


if __name__ == '__main__':
    sys.exit(main())
