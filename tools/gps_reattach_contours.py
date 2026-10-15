# -*- coding: utf-8 -*-
"""GPS-22 -- поле посчитанного участка заново: контур по нынешнему зеркалу. Сухой прогон по умолчанию.

ЗАЧЕМ
Участок (`gps_work_polygons`) хранит `contour_id` -- номер строки
`field_contours`, чей контур перекрывал его больше всех, когда сутки
считались. Экран «Факт по технике» называет поле участка и рисует его контур
по этой строке, какой она стала с тех пор. 15.10 зеркало контуров (GPS-4) на
сервере переписало 16 097 строк, и под теми же номерами строк легли другие
зоны: GPS-21 показал, что у 16 089 строк контур другой и сдвинулся на 100 м
и больше, и имя другое. У участков, посчитанных до этой записи, экран теперь
показывает чужое поле. Гектары не меняются: `contour_id` участок только
называет.

ЧТО ДЕЛАЕТ
По каждому участку (все сутки или окно --since/--until) выбирает поле так
же, как ночной расчёт (`gps.area.work_sites`): среди строк `field_contours`
с контуром, которые ночной расчёт берёт (`gps.daily.load_contours`: строка
не выключена, контур читается и чинится), -- строку с наибольшим
перекрытием участка. Не перекрывает ни одна -- поля нет. Участок читается из
сохранённого многоугольника вместе с дырами, как у GPS-19
(`site_polygon`).

Отличие от ночного расчёта одно: тот выбирал по многоугольнику до записи, а
здесь -- по записанному (градусы с 6 знаками, ~0,1 м). При двух почти равных
перекрытиях выбор может разойтись; тест сверяет выбор с ночным расчётом на
фикстуре.

План пишется в CSV (`;`, UTF-8 с BOM): по участку -- было и стало (номер
строки и имя, как оно есть в строке СЕЙЧАС -- его и показывает экран),
перекрытие, есть ли ответ оператора. Имена -- только в CSV: имена полей --
часто имена людей. В консоли -- числа ASCII: сколько участков сменят поле,
получат его, потеряют, не изменятся, по месяцам.

СУХОЙ ПРОГОН ПО УМОЛЧАНИЮ. Без --apply база открывается `mode=ro`, ничего не
пишется. С --apply план считается так же (без блокировки: расчёт идёт
минуты, а программа в это время пишет ответы операторов), затем одна
транзакция `BEGIN IMMEDIATE`:
  - справочник контуров сверяется с тем, по которому считался план (число
    строк, наибольший `updated_at`, суммарная длина геометрии); разошёлся --
    откат, ничего не записано: зеркало прогнали между планом и записью;
  - у каждого участка меняется ТОЛЬКО `contour_id`, и только если строка
    участка та же, что в плане: тот же номер строки, тот же многоугольник,
    тот же прежний `contour_id`. Участок, который ночной расчёт успел
    пересчитать, пропускается и называется;
  - постусловие до фиксации: у каждого записанного участка `contour_id` --
    плановый.
Площадь, многоугольник, ответ оператора и всё остальное не трогаются.

ЧЕГО НЕ ДЕЛАЕТ
Не пересчитывает гектары и не трогает справочник контуров. Не решает, верно
ли нарисована зона в Wialon. Отменить запись можно тем же инструментом после
новой записи зеркала или по CSV плана (столбец `contour_bylo`).

Коды выхода:
  0 -- готово (сухой прогон или запись без остатка);
  3 -- записано, но часть участков пропущена: их строку успели пересчитать;
  1 -- постусловие не сошлось или справочник изменился -- откат, ничего не
       записано; справочник с контурами не прочитался -- ничего не
       посчитано; или инструмент упал;
  2 -- неверный вход, ничего не записано: нет базы (не создаётся), нет
       таблицы или столбца, неверная дата, в пути к базе `#`, `?` или `%`,
       --out на месте базы, её -wal/-shm или любого файла SQLite, папки
       нет, --out не открывается на запись (открыт в Excel);
  4 -- база занята дольше 30 с, ничего не записано;
  5 -- ЗАПИСАНО, но CSV отчёта записать не удалось.

Запуск -- окружением расчёта (нужна геометрия), PowerShell, по одной
команде на строку. Сначала сухой прогон:

  & C:\\gps_venv\\Scripts\\python.exe C:\\gps-tools\\tools\\gps_reattach_contours.py --db C:\\transport-report\\instance\\transport.db --out C:\\gps-tools\\check\\reattach_plan.csv

Запись -- только после копии базы (`backup_transport_db.py`):

  & C:\\gps_venv\\Scripts\\python.exe C:\\gps-tools\\tools\\gps_reattach_contours.py --db C:\\transport-report\\instance\\transport.db --out C:\\gps-tools\\check\\reattach_apply.csv --apply
"""

import argparse
import csv
import os
import sqlite3
import sys

from collections import Counter
from datetime import datetime

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import numpy as np                                                  # noqa: E402
import shapely                                                      # noqa: E402
from shapely.errors import GEOSException                            # noqa: E402

from gps.daily import load_contours                                # noqa: E402
from tools.gps_mark_not_ours import out_clash, out_writable        # noqa: E402
from tools.gps_territory_report import site_polygon                # noqa: E402

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DB_PATH = os.path.join(ROOT, 'instance', 'transport.db')
BUSY_TIMEOUT_S = 30
EXIT_OK, EXIT_FAILED, EXIT_BAD_INPUT = 0, 1, 2
EXIT_SKIPPED, EXIT_BUSY, EXIT_REPORT_FAILED = 3, 4, 5

SAME, CHANGE, GAIN, LOSE = 'bez_izmeneniy', 'smenit', 'poluchit', 'poteryaet'
UNREADABLE, GEOS = 'ne_chitaetsya', 'geos'
WRITES = (CHANGE, GAIN, LOSE)
STATUS_TEXT = [(SAME, 'same field'), (CHANGE, 'another field'),
               (GAIN, 'gets a field'), (LOSE, 'loses its field'),
               (UNREADABLE, 'outline unreadable (kept)'),
               (GEOS, 'geometry failed (kept)')]

CSV_COLUMNS = ['row_id', 'work_date', 'wialon_id', 'site_number', 'area_ha',
               'status', 'contour_bylo', 'imya_bylo_seychas', 'contour_stalo',
               'imya_stalo', 'perekrytie_ga', 'otvet_operatora', 'primechanie']

REQUIRED = {
    'gps_work_polygons': ('id', 'work_date', 'wialon_id', 'site_number',
                          'area_ha', 'polygon_geojson', 'contour_id',
                          'operator_label'),
    'field_contours': ('id', 'name', 'geometry_geojson', 'is_active',
                       'updated_at'),
}


class BadInput(Exception):
    """Неверный вход: отказ с кодом 2 до того, как что-то записано."""


def schema_problem(con):
    """'' или чего не хватает в базе."""
    for table, columns in REQUIRED.items():
        present = {row[1] for row in con.execute('PRAGMA table_info(%s)' % table)}
        if not present:
            return 'table %s is missing - wrong --db?' % table
        for column in columns:
            if column not in present:
                return 'column %s.%s is missing' % (table, column)
    return ''


USABLE = ("geometry_geojson IS NOT NULL AND geometry_geojson != ''")


def directory_fingerprint(con):
    """Чем справочник контуров был, когда считался план.

    [REASON]: план считается без блокировки, минуты. Прогони кто-нибудь
    зеркало за это время -- номера строк остались бы, а контуры под ними
    нет, и запись поставила бы участкам поля по справочнику, которого уже
    нет. Число строк, наибольший `updated_at` (зеркало ставит его при
    каждой правке), суммарная длина геометрии и сумма номеров включённых
    строк ловят правку, добавление и выключение. Считаются только строки с
    контуром -- те, что читает `load_contours`: земли DJI без контура
    переписываются сбором дронов, и их отметка времени поле не меняет.
    """
    return tuple(con.execute(
        'SELECT COUNT(*), MAX(updated_at), TOTAL(LENGTH(geometry_geojson)), '
        'TOTAL(CASE WHEN is_active IS NULL OR is_active = 1 THEN id ELSE 0 END) '
        'FROM field_contours WHERE ' + USABLE).fetchone())


def best_contour(probe, contours, tree, named):
    """(номер строки, перекрытие га) или (None, 0.0) -- как в `work_sites`.

    [REASON]: строго больший побеждает, порядок перебора -- порядок дерева,
    построенного из `load_contours`, как у ночного расчёта: при равных
    перекрытиях выбор тот же.
    """
    best, chosen = 0.0, None
    if tree is None:
        return None, 0.0
    for position in np.atleast_1d(tree.query(probe)):
        contour_id = named[int(position)]
        overlap = probe.intersection(contours[contour_id]).area
        if overlap > best:
            best, chosen = overlap, contour_id
    return chosen, best / 10000.0


def plan(con, since=None, until=None, log=print):
    """Строки плана по участкам окна и сводка."""
    contours = load_contours(con, log=log)
    if not contours and con.execute(
            'SELECT COUNT(*) FROM field_contours WHERE ' + USABLE + ' AND '
            '(is_active IS NULL OR is_active = 1)').fetchone()[0]:
        # [REASON]: `load_contours` молча отдаёт пустой справочник, если
        # чтение упало (база занята, ошибка ввода-вывода). Пустой справочник
        # здесь значит «у всех участков поля нет» -- и запись сняла бы поле
        # у каждого посчитанного участка разом.
        raise RuntimeError('field_contours has outlines but none could be '
                           'read - nothing was planned, run again later')
    named = list(contours.keys())
    tree = shapely.STRtree([contours[i] for i in named]) if named else None
    names = {row[0]: row[1] for row in con.execute(
        'SELECT id, name FROM field_contours')}
    query = ('SELECT id, work_date, wialon_id, site_number, area_ha, '
             'polygon_geojson, contour_id, operator_label '
             'FROM gps_work_polygons')
    where, params = [], []
    if since:
        where.append('work_date >= ?')
        params.append(since)
    if until:
        where.append('work_date <= ?')
        params.append(until)
    if where:
        query += ' WHERE ' + ' AND '.join(where)
    query += ' ORDER BY work_date, wialon_id, site_number'
    items = []
    for (row_id, day, unit, number, area, text, stored,
         label) in con.execute(query, params).fetchall():
        item = {'row_id': row_id, 'work_date': day, 'wialon_id': unit,
                'site_number': number, 'area_ha': area, 'polygon': text,
                'contour_bylo': stored, 'imya_bylo_seychas': names.get(stored, ''),
                'contour_stalo': stored, 'imya_stalo': names.get(stored, ''),
                'perekrytie_ga': '', 'otvet_operatora': 'da' if label else '',
                'primechanie': ''}
        probe = site_polygon(text)
        if probe is None:
            item['status'] = UNREADABLE
            items.append(item)
            continue
        try:
            chosen, overlap = best_contour(probe, contours, tree, named)
        except GEOSException:
            item['status'] = GEOS
            items.append(item)
            continue
        item['contour_stalo'] = chosen
        item['imya_stalo'] = names.get(chosen, '') if chosen is not None else ''
        item['perekrytie_ga'] = round(overlap, 4) if chosen is not None else ''
        if chosen == stored:
            item['status'] = SAME
        elif stored is None:
            item['status'] = GAIN
        elif chosen is None:
            item['status'] = LOSE
        else:
            item['status'] = CHANGE
        items.append(item)
    return items


def report(items, log=print):
    counts = Counter(item['status'] for item in items)
    log('sites scanned              : %d' % len(items))
    for status, text in STATUS_TEXT:
        log('  %-25s: %d' % (text, counts[status]))
    answered = sum(1 for item in items
                   if item['status'] in WRITES and item['otvet_operatora'])
    log('  of the changes, sites with an operator answer: %d' % answered)
    months = sorted({str(item['work_date'])[:7] for item in items})
    if months:
        log('by month (scanned / to change, gain, lose):')
        for month in months:
            inside = [item for item in items
                      if str(item['work_date'])[:7] == month]
            log('  %s : %6d / %6d' % (month, len(inside), sum(
                1 for item in inside if item['status'] in WRITES)))
    return counts


def write_plan(path, items):
    with open(path, 'w', encoding='utf-8-sig', newline='') as handle:
        writer = csv.writer(handle, delimiter=';')
        writer.writerow(CSV_COLUMNS)
        for item in items:
            writer.writerow(['' if item.get(column) is None else item.get(column, '')
                             for column in CSV_COLUMNS])


def apply(con, items, fingerprint):
    """Пишет `contour_id` участков плана. (записано, пропущено) или ошибка.

    Транзакцию открывает и закрывает вызывающий; здесь -- только записи и
    проверки внутри неё.
    """
    if directory_fingerprint(con) != fingerprint:
        raise RuntimeError('field_contours changed since the plan was made '
                           '(the mirror ran?) - run the plan again')
    written, skipped = [], []
    for item in items:
        if item['status'] not in WRITES:
            continue
        cursor = con.execute(
            'UPDATE gps_work_polygons SET contour_id = ? WHERE id = ? '
            'AND contour_id IS ? AND polygon_geojson = ?',
            (item['contour_stalo'], item['row_id'], item['contour_bylo'],
             item['polygon']))
        if cursor.rowcount == 1:
            written.append(item)
        else:
            item['primechanie'] = 'propushchen: uchastok pereschitan posle plana'
            skipped.append(item)
    for item in written:
        row = con.execute('SELECT contour_id FROM gps_work_polygons WHERE id = ?',
                          (item['row_id'],)).fetchone()
        if row is None or row[0] != item['contour_stalo']:
            raise RuntimeError('postcondition failed on site row %s'
                               % item['row_id'])
    return written, skipped


def check_inputs(args):
    if any(mark in args.db for mark in '#?%'):
        raise BadInput('the database path contains #, ? or % - move the file')
    if not os.path.isfile(args.db):
        raise BadInput('database not found at %s - refusing to run' % args.db)
    for value, flag in ((args.since, '--since'), (args.until, '--until')):
        if value is None:
            continue
        try:
            if datetime.strptime(value, '%Y-%m-%d').strftime('%Y-%m-%d') != value:
                raise ValueError(value)
        except ValueError:
            raise BadInput('%s must be YYYY-MM-DD, got %r' % (flag, value))
    if args.since and args.until and args.since > args.until:
        raise BadInput('--since is after --until')
    folder = os.path.dirname(os.path.abspath(args.out))
    if not os.path.isdir(folder):
        raise BadInput('folder for --out does not exist: %s' % folder)
    clash = out_clash(args.out, (args.db, args.db + '-wal', args.db + '-shm'))
    if clash:
        raise BadInput('--out points at %s - refusing to overwrite it' % clash)
    why = out_writable(args.out)
    if why:
        raise BadInput('cannot write --out %s (%s) - close it in Excel; '
                       'nothing was written' % (args.out, why))


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument('--db', default=DB_PATH)
    parser.add_argument('--since', help='first day, YYYY-MM-DD (default: all)')
    parser.add_argument('--until', help='last day, YYYY-MM-DD (default: all)')
    parser.add_argument('--out', default='gps_reattach_plan.csv')
    parser.add_argument('--apply', action='store_true',
                        help='write contour_id; without it nothing is written')
    args = parser.parse_args(argv)
    try:
        check_inputs(args)
    except BadInput as problem:
        sys.stderr.write('ERROR: %s\n' % problem)
        return EXIT_BAD_INPUT

    con = sqlite3.connect('file:%s?mode=ro' % args.db, uri=True,
                          timeout=BUSY_TIMEOUT_S)
    try:
        problem = schema_problem(con)
        if problem:
            sys.stderr.write('ERROR: %s\n' % problem)
            return EXIT_BAD_INPUT
        fingerprint = directory_fingerprint(con)
        try:
            items = plan(con, args.since, args.until)
        except RuntimeError as problem:
            print('FAILED: %s' % problem)
            return EXIT_FAILED
    finally:
        con.close()

    if not args.apply:
        write_plan(args.out, items)
        counts = report(items)
        print('')
        print('DRY RUN - nothing was written to the database (opened mode=ro)')
        print('changes that --apply would make: %d'
              % sum(counts[status] for status in WRITES))
        print('plan: %s' % args.out)
        return EXIT_OK

    con = sqlite3.connect(args.db, timeout=BUSY_TIMEOUT_S, isolation_level=None)
    try:
        try:
            con.execute('BEGIN IMMEDIATE')
        except sqlite3.OperationalError as problem:
            print('FAILED: database is busy (%s) - nothing was written, run '
                  'the same command again later' % problem)
            return EXIT_BUSY
        try:
            written, skipped = apply(con, items, fingerprint)
        except RuntimeError as problem:
            con.execute('ROLLBACK')
            report(items)
            print('')
            print('FAILED: %s' % problem)
            print('rolled back - nothing was written')
            return EXIT_FAILED
        except Exception:
            con.execute('ROLLBACK')
            raise
        con.execute('COMMIT')
    finally:
        con.close()
    report(items)
    print('')
    print('written: %d site(s) got their field again, one transaction; '
          'skipped: %d' % (len(written), len(skipped)))
    try:
        write_plan(args.out, items)
    except OSError as problem:
        print('report CSV could not be written (%s) - the rows ARE written'
              % problem.__class__.__name__)
        return EXIT_REPORT_FAILED
    print('report : %s' % args.out)
    return EXIT_SKIPPED if skipped else EXIT_OK


if __name__ == '__main__':
    sys.exit(main())
