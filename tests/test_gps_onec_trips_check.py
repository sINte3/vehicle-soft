# -*- coding: utf-8 -*-
"""tools/gps_onec_trips_check.py -- проверка связки «рейс 1С -- наша машина» (GPS-34).

Держится то, что записано в предрегистрации 27.10 (трек §0.6, «Перевозки,
ступень 2»):
  * номер вывоза -- после свёртки; номер реестра ПК-17 -- серия переносится
    в конец; машина -- ровно одна строка техники и ровно один объект без
    пометки, иначе -- названная причина;
  * положение -- ближайшая точка в пределах ±10 минут включительно;
  * место пункта -- медиана, пункт с меньше чем 5 рейсами не считается;
    рядом -- не дальше 1 км включительно;
  * отрицательный контроль -- машина ближайшего по времени рейса с другим
    пунктом и другой машиной;
  * вердикт и его границы: 90 %, 200 рейсов, 20 % без GPS, контроль 50 %;
  * xlsx читается без сторонних библиотек: общие и встроенные строки,
    серийная дата Excel;
  * ничего не пишется, кроме CSV; в консоль -- ASCII; Flask не
    импортируется; неверный ввод -- код 2 до чтения.

Запуск из корня репозитория:
  python -m unittest tests.test_gps_onec_trips_check
"""
import contextlib
import csv
import hashlib
import io
import math
import os
import shutil
import sqlite3
import subprocess
import sys
import tempfile
import unittest
import unittest.mock
import zipfile
from datetime import datetime, timedelta
from xml.sax.saxutils import escape

import tools.gps_onec_trips_check as check
from gps_collector import config as collector_config
from gps_collector import storage
from tests import agro_work_db as dbh

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DAY = datetime(2026, 10, 10)
CENTER = (39.80, 64.40)
POINT_NAMES = ('Вобкент ПТМ', 'Варахшо птм', 'Харгуш ПТМ',
               'К-Бозор ПТМ', 'Челонгу ПТМ', 'Кумушкент ПТМ')


def place(i):
    """Шесть пунктов по кругу радиусом 15 км."""
    angle = math.radians(60 * i)
    lat = CENTER[0] + 15000 / 111320 * math.sin(angle)
    lon = CENTER[1] + 15000 / (111320 * math.cos(math.radians(CENTER[0]))) * math.cos(angle)
    return lat, lon


def unit_of(i, j):
    return 900 + i * 2 + j


def number_of(i, j):
    return 100 + i * 10 + j


def trip_times(i, j):
    return [DAY + timedelta(hours=8, minutes=(i * 13 + j * 41 + k * 97) % 600)
            for k in range(5)]


def location(i, j, when):
    """Машина на своём пункте только около своих рейсов (±15 мин), иначе --
    в поле в 5 км к северу: время рейса должно значить."""
    if any(abs((when - trip).total_seconds()) <= 900 for trip in trip_times(i, j)):
        return place(i)
    lat, lon = place(i)
    return lat + 0.045 + 0.01 * j, lon


def stamp(when):
    return int(when.replace(tzinfo=collector_config.TZ).timestamp())


# --- книга xlsx без сторонних библиотек --------------------------------------------

def _cell(ref, value, shared, inline):
    if isinstance(value, float):
        return '<c r="%s"><v>%r</v></c>' % (ref, value)
    if inline:
        return '<c r="%s" t="inlineStr"><is><t>%s</t></is></c>' % (ref, escape(value))
    if value not in shared:
        shared.append(value)
    return '<c r="%s" t="s"><v>%d</v></c>' % (ref, shared.index(value))


def write_xlsx(path, rows, inline=False):
    """Первая строка -- заголовок. None -- пустая ячейка (её нет в XML)."""
    shared = []
    body = []
    for r, row in enumerate(rows, start=1):
        cells = ''.join(_cell('%s%d' % (chr(65 + c), r), value, shared, inline)
                        for c, value in enumerate(row) if value is not None)
        body.append('<row r="%d">%s</row>' % (r, cells))
    main = 'http://schemas.openxmlformats.org/spreadsheetml/2006/main'
    rel = 'http://schemas.openxmlformats.org/officeDocument/2006/relationships'
    with zipfile.ZipFile(path, 'w') as book:
        book.writestr('[Content_Types].xml', '<?xml version="1.0"?><Types/>')
        book.writestr('xl/workbook.xml',
                      '<workbook xmlns="%s" xmlns:r="%s"><sheets>'
                      '<sheet name="TDSheet" sheetId="1" r:id="rId7"/></sheets>'
                      '</workbook>' % (main, rel))
        book.writestr('xl/_rels/workbook.xml.rels',
                      '<Relationships xmlns="http://schemas.openxmlformats.org/'
                      'package/2006/relationships"><Relationship Id="rId7" '
                      'Target="worksheets/data.xml" Type="ws"/></Relationships>')
        book.writestr('xl/worksheets/data.xml',
                      '<worksheet xmlns="%s"><sheetData>%s</sheetData></worksheet>'
                      % (main, ''.join(body)))
        if shared:
            book.writestr('xl/sharedStrings.xml', '<sst xmlns="%s">%s</sst>' % (
                main, ''.join('<si><t>%s</t></si>' % escape(s) for s in shared)))


def digest(path):
    with open(path, 'rb') as handle:
        return hashlib.sha256(handle.read()).hexdigest()


WORLD = {}


def setUpModule():
    root = tempfile.mkdtemp()
    folder = os.path.join(root, 'points')
    os.mkdir(folder)
    db = dbh.make_db(root)
    con = sqlite3.connect(db)
    try:
        dbh.add_org(con, 1, 'Buxoro')
        for i in range(6):
            for j in range(2):
                eq = 10 + i * 2 + j
                letters = 'HA' if i < 3 else 'DA'
                dbh.add_equipment(con, eq, '80 %03d %s' % (number_of(i, j), letters))
                dbh.add_mapping(con, eq, unit_of(i, j), eq)
        # две машины с одним номером; машина только с пометкой «не наша»;
        # машина с двумя объектами
        dbh.add_equipment(con, 60, '80 777 HA')
        dbh.add_equipment(con, 61, '80 777 HA')
        dbh.add_equipment(con, 62, '80 778 HA')
        dbh.add_mapping(con, 62, 980, 62, skip=1)
        dbh.add_equipment(con, 63, '80 779 HA')
        dbh.add_mapping(con, 63, 990, 63)
        dbh.add_mapping(con, 64, 991, 63)
        # заявки agro-work: хлопок у (0,0) 09--10.10 и у (0,1) 11.10, чизель у
        # (3,0), прочий груз у (1,0)
        con.execute("INSERT INTO agro_work_import_runs (id, started_at, status, "
                    "tool_version) VALUES (1, 't', 'ok', 'v')")
        for work_type, name in (('w-cotton', 'Пахта ташиш'), ('w-ga', 'Чизеллаш'),
                                ('w-cargo', 'Юк ташиш (дизел)')):
            con.execute("INSERT INTO agro_work_work_types (id, name, unit, method, "
                        "first_seen_at, last_seen_at) VALUES (?, ?, 'TON', NULL, "
                        "'t', 't')", (work_type, name))
        for name, (i, j), work_type, first, day in (
                ('X', (0, 0), 'w-cotton', '2026-10-09', '2026-10-10'),
                ('Y', (0, 1), 'w-cotton', '2026-10-11', '2026-10-11'),
                ('Z', (3, 0), 'w-ga', '2026-10-10', '2026-10-10'),
                ('W', (1, 0), 'w-cargo', '2026-10-10', '2026-10-10')):
            eq = 10 + i * 2 + j
            con.execute("INSERT INTO agro_work_transports (id, plate_number, "
                        "plate_norm, equipment_id, match_status, first_seen_at, "
                        "last_seen_at) VALUES (?, ?, ?, ?, 'auto', 't', 't')",
                        ('T' + name, 'P' + name, 'P' + name, eq))
            con.execute(
                "INSERT INTO agro_work_applications (id, application_number, "
                "transport_id, work_type_id, unit, volume, status, created_at, "
                "updated_at, created_day, first_seen_run_id, last_seen_run_id, "
                "history_updated_at, initial_status, completed_day) VALUES (?, ?, "
                "?, ?, 'TON', '7', 'COMPLETED', ?, 'u', ?, 1, 1, 'u', "
                "'IN_PROGRESS', ?)",
                ('app-' + name, 'N-' + name, 'T' + name, work_type,
                 first + 'T07:00:00+05:00', first, day))
        con.commit()
    finally:
        con.close()
    # точка раз в 10 минут с 7:00 до 19:00, место -- location()
    rows = []
    for i in range(6):
        for j in range(2):
            for minute in range(0, 12 * 60 + 1, 10):
                when = DAY + timedelta(hours=7, minutes=minute)
                lat, lon = location(i, j, when)
                rows.append((unit_of(i, j), stamp(when), lon, lat, 0.0, None, 9))
    storage.write_points(folder, rows)

    fmt = '%Y-%m-%d %H:%M:%S'
    vyvoz = [('Завод', 'Пункт приема', 'Товаровед', 'Гос номер (из названия)',
              'Время первого взвешивания', 'Вес нетто, кг')]
    for i in range(3):
        for j in range(2):
            for when in trip_times(i, j):
                vyvoz.append(('Z', POINT_NAMES[i], 'person', '80 %03d HA' % number_of(i, j),
                              when.strftime(fmt), '7000'))
    vyvoz += [
        # номер машины пункта 5 (i=4) записан на пункт 1: она там не стояла
        ('Z', POINT_NAMES[0], 'p', '80 %03d DA' % number_of(4, 0),
         (DAY + timedelta(hours=10)).strftime(fmt), '1'),
        # 11.10 точек нет вовсе -- без GPS
        ('Z', POINT_NAMES[0], 'p', '80 %03d HA' % number_of(0, 0),
         (DAY + timedelta(days=1, hours=8)).strftime(fmt), '1'),
        ('Z', POINT_NAMES[0], 'p', '80 999 ZZ', (DAY + timedelta(hours=9)).strftime(fmt), '1'),
        ('Z', POINT_NAMES[0], 'p', '80 777 HA', (DAY + timedelta(hours=9)).strftime(fmt), '1'),
        ('Z', POINT_NAMES[0], 'p', '80 778 HA', (DAY + timedelta(hours=9)).strftime(fmt), '1'),
        ('Z', POINT_NAMES[0], 'p', '80 779 HA', (DAY + timedelta(hours=9)).strftime(fmt), '1'),
        ('Z', POINT_NAMES[0], 'p', None, (DAY + timedelta(hours=9)).strftime(fmt), '1'),
        ('Z', POINT_NAMES[0], 'p', '80 100 HA', None, '1'),
        (None, None, None, None, None, None),
    ]
    reestr = [('Организация (завод)', 'Пункт приема', 'Фермер', 'Транспорт',
               'Дата брутто')]
    first = True
    for i in range(3, 6):
        for j in range(2):
            for when in trip_times(i, j):
                # одна дата -- серийным числом Excel, как бывает в выгрузках
                value = ((when - datetime(1899, 12, 30)).total_seconds() / 86400
                         if first else when.strftime('%d.%m.%Y %H:%M:%S'))
                first = False
                reestr.append(('Org', ' ' + POINT_NAMES[i] + '  ', 'farmer name',
                               '80 DA %03d' % number_of(i, j), value))
    vyvoz_path = os.path.join(root, 'vyvoz.xlsx')
    reestr_path = os.path.join(root, 'reestr.xlsx')
    write_xlsx(vyvoz_path, vyvoz)
    write_xlsx(reestr_path, reestr, inline=True)
    before = {path: digest(path) for path in [db, vyvoz_path, reestr_path] + [
        os.path.join(folder, name) for name in os.listdir(folder)]}
    out_csv = os.path.join(root, 'out.csv')
    argv = ['--db', db, '--dir', folder, '--vyvoz', vyvoz_path, '--reestr',
            reestr_path, '--out', out_csv]
    code, out = run_main(*argv)
    with unittest.mock.patch.object(check, 'MIN_COUNTED', 50):
        met_code, met_out = run_main(*argv)
    WORLD.update(root=root, folder=folder, db=db, vyvoz=vyvoz_path,
                 reestr=reestr_path, before=before, csv=out_csv, code=code,
                 out=out, met_code=met_code, met_out=met_out, argv=argv)


def tearDownModule():
    shutil.rmtree(WORLD['root'], True)


def run_main(*argv):
    lines = []
    err = io.StringIO()
    with contextlib.redirect_stderr(err):
        code = check.main([str(arg) for arg in argv], out=lines.append)
    return code, '\n'.join(lines) + err.getvalue()


def rows():
    with open(WORLD['csv'], encoding='utf-8-sig', newline='') as handle:
        return list(csv.DictReader(handle, delimiter=';'))


class Run(unittest.TestCase):

    def test_trips_and_reasons(self):
        self.assertEqual(WORLD['code'], check.EXIT_OK, WORLD['out'])
        out = WORLD['out']
        self.assertIn('trips read: 68 (removal 38, receiving 30) from 2 file(s)', out)
        self.assertIn('machine found: 62 | not ours 1 | several machines 1 | '
                      'no object 1 | several objects 1 | no plate 1 | no time 1', out)
        self.assertIn('without GPS within 10 min: 1 of 62', out)
        reasons = sorted(row['prichina'] for row in rows() if row['prichina'])
        self.assertEqual(reasons, sorted([check.NOT_OURS, check.MANY_MACHINES,
                                          check.NO_OBJECT, check.MANY_OBJECTS,
                                          check.NO_PLATE, check.NO_TIME,
                                          check.NO_GPS]))

    def test_c1_counts_the_wrong_machine_as_far(self):
        out = WORLD['out']
        self.assertIn('points with 5+ trips with GPS: 6 | trips counted: 61', out)
        self.assertIn('C1 (within 1000 m of the point): 60 of 61 = 98.4 %', out)
        wrong = [row for row in rows()
                 if row['gosnomer'] == '80 %03d DA' % number_of(4, 0)
                 and row['punkt'] == POINT_NAMES[0]]
        self.assertEqual(len(wrong), 1)
        self.assertGreater(int(wrong[0]['do_mesta_m']), 10000)

    def test_registry_plate_series_moves_to_the_end(self):
        reg = [row for row in rows() if row['vid'] == check.REESTR]
        self.assertEqual(len(reg), 30)
        self.assertTrue(all(row['wialon_id'] and not row['prichina'] for row in reg))
        self.assertEqual({row['gosnomer_svyortka'] for row in reg if row['wialon_id'] ==
                          str(unit_of(3, 0))}, {'80%03dDA' % number_of(3, 0)})
        # пункт сведён по пробелам, серийная дата Excel прочитана
        self.assertEqual({row['punkt'] for row in reg}, set(POINT_NAMES[3:]))
        self.assertTrue(all(row['vremya'].startswith('2026-10-10 ') for row in reg))

    def test_the_control_does_not_pass_and_the_verdict(self):
        self.assertIn('negative control C1: ', WORLD['out'])
        share = WORLD['out'].split('negative control C1: ')[1].split('\n')[0]
        near, whole = share.split(' = ')[0].split(' of ')
        self.assertLess(int(near) / int(whole), check.CONTROL_LIMIT)
        # 61 рейс в счёте меньше 200 -- не оценено; с порогом 50 -- MET
        self.assertIn('VERDICT ONEC JOIN: NOT EVALUATED', WORLD['out'])
        self.assertEqual(WORLD['met_code'], check.EXIT_OK, WORLD['met_out'])
        self.assertIn('VERDICT ONEC JOIN: MET', WORLD['met_out'])

    def test_reference_against_applications(self):
        out = WORLD['out']
        self.assertIn('reference: cotton hauling machine-days 2 | with a 1C trip 1', out)
        # 09.10 заявки X -- вне периода рейсов и не считается; «юк ташиш» --
        # заявка, но не хлопок
        self.assertIn('reference: removal trip machine-days 8 | without any '
                      'application 6', out)
        self.assertIn('reference: receiving trip machine-days 6 | without any '
                      'application 5', out)

    def test_nothing_is_written_but_the_csv_and_the_console_is_ascii(self):
        for path, value in WORLD['before'].items():
            self.assertEqual(digest(path), value, path)
        self.assertTrue(WORLD['out'].isascii())
        with open(WORLD['csv'], encoding='utf-8-sig') as handle:
            self.assertNotIn('farmer', handle.read())


class Parts(unittest.TestCase):

    def test_plate_key(self):
        self.assertEqual(check.plate_key(check.REESTR, '80 DA 609'), '80609DA')
        self.assertEqual(check.plate_key(check.REESTR, '80MA716'), '80716MA')
        self.assertEqual(check.plate_key(check.REESTR, '80 609 DA'), '80609DA')
        self.assertEqual(check.plate_key(check.REESTR, '01 A 123 BC'), '01A123BC')
        self.assertEqual(check.plate_key(check.VYVOZ, '80 DA 609'), '80DA609')
        # кириллические двойники сводятся до переноса серии
        self.assertEqual(check.plate_key(check.REESTR, '80 НА 609'), '80609HA')

    def test_parse_time(self):
        self.assertEqual(check.parse_time('2026-10-10 08:02:19'),
                         datetime(2026, 10, 10, 8, 2, 19))
        self.assertEqual(check.parse_time('10.10.2026 0:00:37'),
                         datetime(2026, 10, 10, 0, 0, 37))
        self.assertEqual(check.parse_time('46305.5'), datetime(2026, 10, 10, 12))
        for bad in ('', None, 'abc', '5', '99999'):
            self.assertIsNone(check.parse_time(bad))

    def test_position_window_is_inclusive(self):
        when = DAY + timedelta(hours=7, minutes=5)    # точки в 7:00 и 7:10
        where = check.position(WORLD['folder'], unit_of(0, 0), when, {})
        self.assertAlmostEqual(where[0], location(0, 0, when)[0], places=6)
        self.assertNotAlmostEqual(where[0], place(0)[0], places=3)
        far = DAY + timedelta(hours=19, minutes=10)   # последняя точка -- 19:00
        self.assertIsNotNone(check.position(WORLD['folder'], unit_of(0, 0), far, {}))
        beyond = far + timedelta(seconds=1)
        self.assertIsNone(check.position(WORLD['folder'], unit_of(0, 0), beyond, {}))

    def test_position_across_midnight(self):
        folder = os.path.join(WORLD['root'], 'midnight')
        os.makedirs(folder, exist_ok=True)
        late = DAY + timedelta(hours=23, minutes=56)
        storage.write_points(folder, [(777, stamp(late), 64.4, 39.8, 0.0, None, 9)])
        after = DAY + timedelta(days=1, minutes=4)
        self.assertEqual(check.position(folder, 777, after, {}), (39.8, 64.4))
        early = DAY + timedelta(days=1, minutes=3)
        storage.write_points(folder, [(778, stamp(early), 64.5, 39.9, 0.0, None, 9)])
        before = DAY + timedelta(hours=23, minutes=55)
        self.assertEqual(check.position(folder, 778, before, {}), (39.9, 64.5))

    def test_measure_needs_five_trips_and_one_kilometre_inclusive(self):
        base = (39.8, 64.4)
        far = (39.9, 64.4)                       # около 11 км к северу
        items = [('a', base)] * 4 + [('a', far)] + [('b', base)] * 4 + [('c', None)]
        counted, near, places, distances = check.measure(items)
        # у «b» четыре рейса -- пункт не считается; у «c» нет GPS
        self.assertEqual((counted, near, sorted(places)), (5, 4, ['a']))
        self.assertEqual(places['a'], base)
        self.assertGreater(distances[4], 10000)
        self.assertEqual(distances[5:], [None] * 5)
        # граница -- включительно; сама геометрия -- отдельным тестом
        for metres, expected in ((check.NEAR_M, 5), (check.NEAR_M + 0.001, 4)):
            with unittest.mock.patch.object(
                    check, 'distance_m',
                    lambda a, b, m=metres: 0.0 if a == b else m):
                counted, near, _p, _d = check.measure([('a', base)] * 4 + [('a', far)])
            self.assertEqual((counted, near), (5, expected))

    def test_distance(self):
        self.assertAlmostEqual(check.distance_m((39.8, 64.4), (39.9, 64.4)),
                               11119.5, delta=1.0)
        self.assertEqual(check.distance_m((39.8, 64.4), (39.8, 64.4)), 0.0)

    def test_control_takes_the_nearest_trip_at_another_point(self):
        t = DAY
        trips = [{'time': t, 'point_key': 'a', 'unit': 1},
                 {'time': t + timedelta(minutes=1), 'point_key': 'a', 'unit': 2},
                 {'time': t + timedelta(minutes=2), 'point_key': 'b', 'unit': 1},
                 {'time': t + timedelta(minutes=5), 'point_key': 'b', 'unit': 3},
                 {'time': t + timedelta(minutes=30), 'point_key': 'c', 'unit': 4}]
        self.assertEqual(check.control_units(trips), [3, 1, 2, 2, 3])
        # равные разрывы -- берётся более ранний рейс
        tie = [{'time': t, 'point_key': 'b', 'unit': 5},
               {'time': t + timedelta(minutes=10), 'point_key': 'a', 'unit': 1},
               {'time': t + timedelta(minutes=20), 'point_key': 'c', 'unit': 6}]
        self.assertEqual(check.control_units(tie)[1], 5)
        alone = [{'time': t, 'point_key': 'a', 'unit': 1},
                 {'time': t, 'point_key': 'b', 'unit': 1}]
        self.assertEqual(check.control_units(alone), [None, None])

    def test_judge_bounds(self):
        ok = dict(counted=200, near=180, with_machine=250, no_gps=50,
                  control_counted=100, control_near=49)
        self.assertEqual(check.judge(**ok)[0], check.MET)
        for changes, expected in (({'near': 179}, check.NOT_MET),
                                  ({'counted': 199, 'near': 199}, check.NOT_EVALUATED),
                                  ({'no_gps': 51}, check.NOT_EVALUATED),
                                  ({'control_near': 50}, check.NOT_EVALUATED),
                                  ({'control_counted': 0, 'control_near': 0},
                                   check.NOT_EVALUATED),
                                  ({'with_machine': 0}, check.NOT_EVALUATED)):
            with self.subTest(changes=changes):
                self.assertEqual(check.judge(**dict(ok, **changes))[0], expected)


class Refusals(unittest.TestCase):

    def base(self, **changes):
        args = dict(zip(WORLD['argv'][0::2], WORLD['argv'][1::2]))
        args['--out'] = os.path.join(WORLD['root'], 'refused.csv')
        args.update(changes)
        argv = []
        for key, value in args.items():
            argv += [key, value]
        return run_main(*argv)

    def test_bad_input_is_code_two_and_nothing_is_created(self):
        missing = os.path.join(WORLD['root'], 'nope.db')
        not_xlsx = os.path.join(WORLD['root'], 'plain.xlsx')
        with open(not_xlsx, 'w') as handle:
            handle.write('not a zip')
        wrong_columns = os.path.join(WORLD['root'], 'wrong.xlsx')
        write_xlsx(wrong_columns, [('Пункт приема', 'Транспорт'), ('a', 'b')])
        for changes in ({'--db': missing},
                        {'--dir': os.path.join(WORLD['root'], 'nodir')},
                        {'--vyvoz': os.path.join(WORLD['root'], 'nofile.xlsx')},
                        {'--vyvoz': not_xlsx},
                        {'--reestr': wrong_columns},
                        {'--out': os.path.join(WORLD['root'], 'nodir', 'a.csv')},
                        {'--out': WORLD['vyvoz']}):
            with self.subTest(changes=changes):
                code, out = self.base(**changes)
                self.assertEqual(code, check.EXIT_BAD_INPUT, out)
                self.assertFalse(os.path.exists(os.path.join(WORLD['root'], 'refused.csv')))
        self.assertFalse(os.path.exists(missing))

    def test_no_files_is_refused(self):
        code, out = run_main('--db', WORLD['db'], '--dir', WORLD['folder'],
                             '--out', os.path.join(WORLD['root'], 'x.csv'))
        self.assertEqual(code, check.EXIT_BAD_INPUT)
        self.assertIn('at least one', out)


class Imports(unittest.TestCase):

    def test_flask_and_geometry_are_not_imported(self):
        code = ('import sys; import tools.gps_onec_trips_check; '
                'print("flask" in sys.modules, "models" in sys.modules, '
                '"shapely" in sys.modules, "numpy" in sys.modules)')
        out = subprocess.run([sys.executable, '-c', code], cwd=REPO,
                             capture_output=True, text=True, check=True).stdout
        self.assertEqual(out.strip(), 'False False False False')


if __name__ == '__main__':
    unittest.main()
