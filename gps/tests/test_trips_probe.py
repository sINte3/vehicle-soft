# -*- coding: utf-8 -*-
"""tools/gps_trips_probe.py -- разведка метода «рейсы» (GPS-33), только чтение.

Окружение расчёта (геометрия). Держится то, на чём будет стоять разговор с
владельцем о правиле рейса:
  * заезд -- непрерывный ряд точек в одной зоне; въезд -- если перед рядом
    была точка вне этой зоны; ряд, с которого начались точки, -- не въезд;
  * заезды считаются по типам зон списка; «пункт приёма» -- ПТМ и
    хлопкозавод, от 5 минут (граница включительно);
  * берутся только заявки метода «рейсы» с окном внутри периода; без
    объекта Wialon и без точек -- названной причиной, а не нулями;
  * многодневное окно читается целиком, заезд через полночь -- один;
  * с --hauling -- заявки перевозок «ташиш» при любом методе (имя вида без
    учёта регистра), заявки «рейсы» не берутся; подтверждение -- остановка
    в пункте приёма, иначе пробег окна от 3 км, иначе «ничего»;
  * контроль (--control-csv, только с --hauling) -- те же машины по
    суткам периода с точками: сутки перевозки, сутки только другой заявки,
    сутки без заявок; окно заявки, начатой до периода, сутки периода
    покрывает; группа -- хлопок («пахта», «тола») или прочий груз;
  * ничего не пишется, кроме CSV; в консоль -- ASCII; Flask не
    импортируется; неверный ввод -- код 2 до чтения.

Запуск из корня репозитория окружением расчёта:
  python -m unittest gps.tests.test_trips_probe
"""
import contextlib
import csv
import hashlib
import io
import json
import os
import shutil
import sqlite3
import subprocess
import sys
import tempfile
import unittest
import unittest.mock

import shapely

import tools.gps_trips_probe as probe
from gps.territory import zone_polygon
from gps.tests.test_area import xy_to_lonlat
from gps.tests.test_edge_replay import stamped
from gps_collector import storage
from tests import agro_work_db as dbh

REPO = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
SINCE, UNTIL = '2026-09-01', '2026-09-30'

PTM, ZAVOD, AZS = 900, 901, 902
# квадраты 200 x 200 м в разные стороны от поля (0, 0): путь от поля к одной
# зоне другую не задевает
SQUARES = {PTM: (1000.0, 0.0), ZAVOD: (-2000.0, 0.0), AZS: (0.0, 2000.0)}
KINDS = {PTM: 'ptm', ZAVOD: 'pakhta_zavod', AZS: 'azs'}
FIELD = (0.0, 0.0)


def square(center, half=100.0):
    cx, cy = center
    ring = [xy_to_lonlat(cx + dx, cy + dy) for dx, dy in
            ((-half, -half), (half, -half), (half, half), (-half, half), (-half, -half))]
    return {'type': 'Polygon', 'coordinates': [[list(p) for p in ring]]}


def route(stops, step_s=30.0, speed_ms=6.0):
    """Трек (t, lon, lat, скорость) по остановкам [(x, y, секунд стоять)]:
    между остановками -- по прямой, точка раз в step_s."""
    track, t = [], 0.0
    x0, y0, _stay = stops[0]
    for x, y, stay in stops:
        distance = ((x - x0) ** 2 + (y - y0) ** 2) ** 0.5
        steps = max(int(distance / (speed_ms * step_s)), 1) if distance else 0
        for i in range(1, steps + 1):
            px = x0 + (x - x0) * i / steps
            py = y0 + (y - y0) * i / steps
            lon, lat = xy_to_lonlat(px, py)
            track.append((t, lon, lat, speed_ms * 3.6))
            t += step_s
        for _ in range(int(stay / step_s) + 1):
            lon, lat = xy_to_lonlat(x, y)
            track.append((t, lon, lat, 0.0))
            t += step_s
        x0, y0 = x, y
    return track


def at(zone, stay):
    x, y = SQUARES[zone]
    return (x, y, stay)


def field(stay=600):
    return (FIELD[0], FIELD[1], stay)


# Заявка A (объект 801): три заезда в ПТМ (10,5, 10,5 и 2 мин -- последний не
# остановка) и один в завод (6,5 мин); объём 3 -- совпадает с остановками в
# пунктах приёма (2 + 1). Минуты: точка прибытия и int(стоять / 30) + 1
# точек стоянки через 30 с.
TRACK_A = route([field(), at(PTM, 600), field(), at(PTM, 600), field(),
                 at(PTM, 90), field(), at(ZAVOD, 360), field()])
# Заявка B (объект 802): сутки начались в ПТМ (это не въезд), затем через АЗС
# и снова в ПТМ на 6 мин; объём 5.
TRACK_B = route([at(PTM, 1200), field(), at(AZS, 300), at(PTM, 360), field()])
# Заявка G (объект 805): окно двое суток, заезд в ПТМ через полночь.
TRACK_G1 = route([field(), at(PTM, 600)])
TRACK_G2 = route([at(PTM, 600), field(), at(PTM, 600), field()])
# Перевозки: K -- хлопок на ПТМ (остановка 10,5 мин, пробег около 2 км);
# L -- груз на 5 км к югу, мимо всех зон; M -- стоит весь день.
TRACK_K = route([field(), at(PTM, 600), field()])
TRACK_L = route([field(), (0.0, -5000.0, 300)])
TRACK_M = route([field(3600)])


def digest(path):
    with open(path, 'rb') as handle:
        return hashlib.sha256(handle.read()).hexdigest()


WORLD = {}


def write_track(folder, unit, day, track, hour=8):
    points = stamped(track, day, hour=hour)
    storage.write_points(folder, [(unit, t, lon, lat, speed, None, sats)
                                  for t, lon, lat, speed, sats in points])


def setUpModule():
    root = tempfile.mkdtemp()
    folder = os.path.join(root, 'points')
    os.mkdir(folder)
    db = dbh.make_db(root)
    con = sqlite3.connect(db)
    try:
        con.execute('DROP TABLE field_contours')
        con.execute('CREATE TABLE field_contours (id INTEGER PRIMARY KEY, '
                    'source TEXT, external_id TEXT, name TEXT, '
                    'geometry_geojson TEXT, is_active INTEGER)')
        for zone, center in sorted(SQUARES.items()):
            con.execute('INSERT INTO field_contours (source, external_id, name, '
                        'geometry_geojson, is_active) VALUES (?, ?, ?, ?, 1)',
                        ('wialon', str(zone), 'z%d' % zone,
                         json.dumps(square(center))))
        # Приманки позже настоящих строк: выключенная строка зеркала АЗС и
        # контур Дронов с тем же номером, что у ПТМ, -- оба далеко. Взятые,
        # они перекрыли бы настоящий контур.
        far = json.dumps(square((50000.0, 50000.0)))
        con.execute("INSERT INTO field_contours (source, external_id, name, "
                    "geometry_geojson, is_active) VALUES ('wialon', ?, 'old', ?, 0)",
                    (str(AZS), far))
        con.execute("INSERT INTO field_contours (source, external_id, name, "
                    "geometry_geojson, is_active) VALUES ('dji', ?, 'dji', ?, 1)",
                    (str(PTM), far))
        dbh.add_org(con, 1, 'Buxoro')
        for number, unit in enumerate((801, 802, 804, 805, 806, 807, 808, 809,
                                       810), start=1):
            dbh.add_equipment(con, number, '80 %03d EA' % number, org_id=1)
            dbh.add_mapping(con, number, unit, number)
            con.execute("INSERT INTO agro_work_transports (id, plate_number, "
                        "plate_norm, equipment_id, match_status, first_seen_at, "
                        "last_seen_at) VALUES (?, ?, ?, ?, 'auto', 't', 't')",
                        ('T%d' % unit, 'P%d' % unit, 'P%d' % unit, number))
        # 803 -- машина agro-work без связи с нашей техникой
        con.execute("INSERT INTO agro_work_transports (id, plate_number, "
                    "plate_norm, equipment_id, match_status, first_seen_at, "
                    "last_seen_at) VALUES ('T803', 'P803', 'P803', NULL, "
                    "'none', 't', 't')")
        for work_type, name, unit_name, method in (
                ('w-trip', 'Сут махсулотлари', 'TRIP', 'reysy'),
                ('w-ga', 'Чизеллаш', 'HECTARE', 'ga'),
                ('w-cotton', 'Пахта ташиш', 'TON', 'ne_sveryaetsya'),
                ('w-cargo', 'Юк ТАШИШ (метан)', 'TON', None)):
            con.execute("INSERT INTO agro_work_work_types (id, name, unit, method, "
                        "first_seen_at, last_seen_at) VALUES (?, ?, ?, ?, 't', 't')",
                        (work_type, name, unit_name, method))
        con.execute("INSERT INTO agro_work_import_runs (id, started_at, status, "
                    "tool_version) VALUES (1, 't', 'ok', 'v')")
        apps = [
            ('A', 801, 'w-trip', '3', '2026-09-10', '2026-09-10'),
            ('B', 802, 'w-trip', '5', '2026-09-11', '2026-09-11'),
            ('C', 803, 'w-trip', '2', '2026-09-12', '2026-09-12'),
            ('D', 804, 'w-trip', '4', '2026-09-12', '2026-09-12'),
            ('E', 801, 'w-ga', '7', '2026-09-13', '2026-09-13'),
            ('F', 806, 'w-trip', '1', '2026-10-05', '2026-10-05'),
            ('G', 805, 'w-trip', '2', '2026-09-14', '2026-09-15'),
            # окно началось до периода -- не его заявка, хоть и задевает
            ('H', 807, 'w-trip', '1', '2026-08-31', '2026-09-01'),
            ('K', 808, 'w-cotton', '12', '2026-09-20', '2026-09-20'),
            ('L', 809, 'w-cargo', '8', '2026-09-21', '2026-09-21'),
            ('M', 810, 'w-cotton', '10', '2026-09-22', '2026-09-22'),
            # контроль: другая заявка у машины перевозки и перевозка, начатая
            # до периода (в разведку не идёт, но 01.09 её сутки)
            ('P', 809, 'w-ga', '3', '2026-09-25', '2026-09-25'),
            ('Q', 810, 'w-cotton', '9', '2026-08-30', '2026-09-02'),
        ]
        for name, unit, work_type, volume, first, last in apps:
            con.execute(
                "INSERT INTO agro_work_applications (id, application_number, "
                "transport_id, work_type_id, unit, volume, status, created_at, "
                "updated_at, created_day, first_seen_run_id, last_seen_run_id, "
                "history_updated_at, initial_status, completed_day) VALUES (?, ?, "
                "?, ?, ?, ?, 'COMPLETED', ?, 'u', ?, 1, 1, 'u', 'IN_PROGRESS', ?)",
                ('app-' + name, 'N-' + name, 'T%d' % unit, work_type, 'TRIP',
                 volume, first + 'T07:00:00+05:00', first, last))
        con.commit()
    finally:
        con.close()
    write_track(folder, 801, '2026-09-10', TRACK_A)
    write_track(folder, 802, '2026-09-11', TRACK_B)
    write_track(folder, 806, '2026-10-05', TRACK_A)
    write_track(folder, 805, '2026-09-14', TRACK_G1, hour=23)
    write_track(folder, 805, '2026-09-15', TRACK_G2, hour=0)
    write_track(folder, 807, '2026-09-01', TRACK_A)
    write_track(folder, 808, '2026-09-20', TRACK_K)
    write_track(folder, 809, '2026-09-21', TRACK_L)
    write_track(folder, 810, '2026-09-22', TRACK_M)
    # контроль: 808 без заявок 23.09 стоит в ПТМ, 24.09 -- на поле; 809 в
    # сутки чизеля стоит; 810 возит хлопок по заявке Q 01.09
    write_track(folder, 808, '2026-09-23', TRACK_K)
    write_track(folder, 808, '2026-09-24', TRACK_M)
    write_track(folder, 809, '2026-09-25', TRACK_M)
    write_track(folder, 810, '2026-09-01', TRACK_K)
    zones_csv = os.path.join(root, 'zones.csv')
    with open(zones_csv, 'w', encoding='utf-8', newline='') as handle:
        writer = csv.writer(handle)
        writer.writerow(('zona_id', 'gruppa_id', 'tip', 'ploshchad_ga'))
        for zone in sorted(SQUARES):
            writer.writerow((zone, 16, KINDS[zone], 4.0))
        writer.writerow((999, 16, 'ptm', 1.0))        # зона без контура
    out_csv = os.path.join(root, 'apps.csv')
    visits_csv = os.path.join(root, 'visits.csv')
    before = {path: digest(path) for path in [db] + [
        os.path.join(folder, name) for name in os.listdir(folder)]}
    code, out = run_main('--db', db, '--dir', folder, '--from', SINCE,
                         '--to', UNTIL, '--csv', out_csv, '--visits-csv',
                         visits_csv, '--zones', zones_csv)
    hauling_csv = os.path.join(root, 'hauling.csv')
    hauling_visits = os.path.join(root, 'hauling_visits.csv')
    h_code, h_out = run_main('--db', db, '--dir', folder, '--from', SINCE,
                             '--to', UNTIL, '--csv', hauling_csv, '--visits-csv',
                             hauling_visits, '--zones', zones_csv, '--hauling')
    control_csv = os.path.join(root, 'control.csv')
    c_code, c_out = run_main('--db', db, '--dir', folder, '--from', SINCE,
                             '--to', UNTIL, '--csv', os.path.join(root, 'c.csv'),
                             '--visits-csv', os.path.join(root, 'cv.csv'),
                             '--zones', zones_csv, '--hauling',
                             '--control-csv', control_csv)
    WORLD.update(root=root, folder=folder, db=db, zones=zones_csv, code=code,
                 out=out, csv=out_csv, visits=visits_csv, before=before,
                 h_code=h_code, h_out=h_out, h_csv=hauling_csv,
                 c_code=c_code, c_out=c_out, c_csv=control_csv)


def tearDownModule():
    shutil.rmtree(WORLD['root'], True)


def run_main(*argv):
    lines = []
    err = io.StringIO()
    with contextlib.redirect_stderr(err):
        code = probe.main([str(arg) for arg in argv], out=lines.append)
    return code, '\n'.join(lines) + err.getvalue()


def read(path):
    with open(path, encoding='utf-8-sig', newline='') as handle:
        return list(csv.DictReader(handle, delimiter=';'))


def apps():
    return {row['nomer_zayavki']: row for row in read(WORLD['csv'])}


class Run(unittest.TestCase):

    def test_only_trip_applications_of_the_period(self):
        self.assertEqual(WORLD['code'], probe.EXIT_OK, WORLD['out'])
        self.assertEqual(sorted(apps()), ['N-A', 'N-B', 'N-C', 'N-D', 'N-G'])
        self.assertIn('trip applications in the period: 5', WORLD['out'])

    def test_entries_by_zone_type_and_stops(self):
        a = apps()['N-A']
        self.assertEqual((a['zaezdy_ptm'], a['zaezdy_ptm_5min'], a['zaezdy_zavod'],
                          a['zaezdy_zavod_5min'], a['zaezdy_priem_5min'],
                          a['zaezdy_prochie']), ('3', '2', '1', '1', '3', '0'))
        self.assertEqual((a['obem'], a['wialon_id'], a['okno_s'], a['okno_po'],
                          a['sutok_s_tochkami'], a['prichina']),
                         ('3.0', '801', '2026-09-10', '2026-09-10', '1', ''))
        self.assertEqual(a['vid_raboty'], 'Сут махсулотлари')

    def test_a_day_that_starts_inside_is_not_an_entry(self):
        b = apps()['N-B']
        self.assertEqual((b['zaezdy_ptm'], b['zaezdy_ptm_5min'], b['zaezdy_prochie']),
                         ('1', '1', '1'))
        visits = [v for v in read(WORLD['visits']) if v['nomer_zayavki'] == 'N-B']
        self.assertEqual([(v['tip'], v['vezd']) for v in visits],
                         [('ptm', 'net'), ('azs', 'da'), ('ptm', 'da')])

    def test_a_two_day_window_and_a_visit_across_midnight(self):
        g = apps()['N-G']
        self.assertEqual(g['sutok_s_tochkami'], '2')
        # въезд 14.10 в 23:..; стоянка переходит в 15.10 -- это один заезд;
        # второй -- после поля
        self.assertEqual((g['zaezdy_ptm'], g['zaezdy_ptm_5min']), ('2', '2'))

    def test_without_a_machine_or_points_the_reason_is_named(self):
        rows = apps()
        self.assertEqual(rows['N-C']['prichina'], probe.NO_MACHINE)
        self.assertEqual(rows['N-D']['prichina'], probe.NO_POINTS)
        self.assertEqual(rows['N-D']['zaezdy_ptm'], '')
        self.assertIn('without a Wialon object 1 | without points 1 | measured 3',
                      WORLD['out'])

    def test_the_summary_against_the_volume(self):
        # A: 3 при объёме 3; B: 1 при 5; G: 2 при 2
        self.assertIn('stops of 5+ min at receiving points vs the volume: equal 2 '
                      '| within 1 0 | more 0 | fewer 1 | no volume 0', WORLD['out'])
        self.assertIn('zones in the list 4, with an outline 3, without 1',
                      WORLD['out'])

    def test_visit_rows_carry_local_times_and_minutes(self):
        visits = [v for v in read(WORLD['visits']) if v['nomer_zayavki'] == 'N-A']
        self.assertEqual([v['tip'] for v in visits],
                         ['ptm', 'ptm', 'ptm', 'pakhta_zavod'])
        self.assertEqual([v['zona_id'] for v in visits], ['900', '900', '900', '901'])
        self.assertTrue(visits[0]['vhod'].startswith('2026-09-10 08:'))
        self.assertEqual([float(v['minut']) for v in visits], [10.5, 10.5, 2.0, 6.5])

    def test_nothing_is_written_but_the_csv_and_the_console_is_ascii(self):
        for path, value in WORLD['before'].items():
            self.assertEqual(digest(path), value, path)
        self.assertTrue(WORLD['out'].isascii())


class Hauling(unittest.TestCase):

    def rows(self):
        return {row['nomer_zayavki']: row for row in read(WORLD['h_csv'])}

    def test_only_hauling_work_types_whatever_the_method(self):
        self.assertEqual(WORLD['h_code'], probe.EXIT_OK, WORLD['h_out'])
        self.assertEqual(sorted(self.rows()), ['N-K', 'N-L', 'N-M'])
        self.assertIn('hauling applications in the period: 3', WORLD['h_out'])
        # и наоборот: прогон «рейсов» перевозок не берёт
        self.assertNotIn('N-K', apps())

    def test_the_evidence_of_each_haul(self):
        rows = self.rows()
        self.assertEqual(rows['N-K']['zaezdy_priem_5min'], '1')
        self.assertAlmostEqual(float(rows['N-K']['km']), 2.0, delta=0.1)
        self.assertAlmostEqual(float(rows['N-L']['km']), 5.0, delta=0.1)
        self.assertEqual(rows['N-L']['zaezdy_priem_5min'], '0')
        self.assertEqual(rows['N-M']['km'], '0.0')
        self.assertIn('evidence of hauling: receiving stop 1 | km only 1 | '
                      'neither 1', WORLD['h_out'])
        self.assertTrue(WORLD['h_out'].isascii())

    def test_evidence_order_and_the_three_kilometres(self):
        self.assertEqual(probe.evidence({'zaezdy_priem_5min': 1, 'km': 50.0}),
                         'receiving stop')
        self.assertEqual(probe.evidence({'zaezdy_priem_5min': 0, 'km': 3.0}),
                         'km only')
        self.assertEqual(probe.evidence({'zaezdy_priem_5min': 0, 'km': 2.99}),
                         'neither')

    def test_window_mileage_of_one_point_or_none_is_zero(self):
        self.assertEqual(probe.window_km([(0, 64.4, 39.8, 10.0, 9)]), 0.0)
        self.assertEqual(probe.window_km([]), 0.0)


class Control(unittest.TestCase):

    def rows(self):
        return {(row['wialon_id'], row['den']): row for row in read(WORLD['c_csv'])}

    def test_days_of_the_same_machines_by_class(self):
        self.assertEqual(WORLD['c_code'], probe.EXIT_OK, WORLD['c_out'])
        rows = self.rows()
        self.assertEqual(sorted(rows), [
            ('808', '2026-09-20'), ('808', '2026-09-23'), ('808', '2026-09-24'),
            ('809', '2026-09-21'), ('809', '2026-09-25'),
            ('810', '2026-09-01'), ('810', '2026-09-22')])
        self.assertEqual({key: (row['klass'], row['gruppa'])
                          for key, row in rows.items()}, {
            ('808', '2026-09-20'): ('hauling', 'cotton'),
            ('808', '2026-09-23'): ('no_app', 'cotton'),
            ('808', '2026-09-24'): ('no_app', 'cotton'),
            ('809', '2026-09-21'): ('hauling', 'other'),
            ('809', '2026-09-25'): ('other_app', 'other'),
            ('810', '2026-09-01'): ('hauling', 'cotton'),
            ('810', '2026-09-22'): ('hauling', 'cotton')})

    def test_a_window_begun_before_the_period_covers_its_days(self):
        row = self.rows()[('810', '2026-09-01')]
        self.assertEqual((row['zayavki'], row['vidy_rabot']), ('N-Q', 'Пахта ташиш'))
        self.assertEqual(row['zaezdy_priem_5min'], '1')
        # в разведку заявок Q не идёт: окно началось до периода
        self.assertIn('hauling applications in the period: 3', WORLD['c_out'])

    def test_a_stop_without_an_application_is_counted_as_such(self):
        row = self.rows()[('808', '2026-09-23')]
        self.assertEqual((row['zayavki'], row['zaezdy_priem_5min']), ('', '1'))
        self.assertAlmostEqual(float(row['km']), 2.0, delta=0.1)

    def test_the_console_table(self):
        out = WORLD['c_out']
        self.assertIn('control: machines 3 | machine-days 90 | without points 83 '
                      '| with points 7', out)
        for line in ('control cotton hauling: days 3 | receiving stop 2 | km 3+ 0',
                     'control cotton other_app: days 0 | receiving stop 0 | km 3+ 0',
                     'control cotton no_app: days 2 | receiving stop 1 | km 3+ 0',
                     'control other hauling: days 1 | receiving stop 0 | km 3+ 1',
                     'control other other_app: days 1 | receiving stop 0 | km 3+ 0',
                     'control other no_app: days 0 | receiving stop 0 | km 3+ 0'):
            self.assertIn(line, out)
        self.assertTrue(out.isascii())

    def test_without_hauling_the_control_is_refused(self):
        target = os.path.join(WORLD['root'], 'refused.csv')
        code, out = run_main('--db', WORLD['db'], '--dir', WORLD['folder'],
                             '--from', SINCE, '--to', UNTIL,
                             '--csv', os.path.join(WORLD['root'], 'r1.csv'),
                             '--visits-csv', os.path.join(WORLD['root'], 'r2.csv'),
                             '--zones', WORLD['zones'], '--control-csv', target)
        self.assertEqual(code, probe.EXIT_BAD_INPUT, out)
        self.assertIn('--control-csv needs --hauling', out)
        self.assertFalse(os.path.exists(target))


class ControlDays(unittest.TestCase):
    """Классы суток -- без базы и файлов."""

    @staticmethod
    def row(unit, work_type, first, last, number='N'):
        from datetime import date
        app = unittest.mock.Mock()
        app.number = number
        return {'wialon_id': unit, 'work_type': work_type,
                'window': (date.fromisoformat(first), date.fromisoformat(last)),
                'app': app}

    def classes(self, all_rows, picked, since='2026-09-01', until='2026-09-03'):
        return [(unit, day.isoformat(), kind, group)
                for unit, day, kind, group, _covering in
                probe.control_days(all_rows, picked, since, until)]

    def test_hauling_wins_over_another_application_on_the_same_day(self):
        haul = self.row(7, 'Юк ташиш', '2026-09-02', '2026-09-02')
        other = self.row(7, 'Чизеллаш', '2026-09-01', '2026-09-03')
        self.assertEqual(self.classes([haul, other], [haul]), [
            (7, '2026-09-01', 'other_app', 'other'),
            (7, '2026-09-02', 'hauling', 'other'),
            (7, '2026-09-03', 'other_app', 'other')])

    def test_the_cotton_group_follows_the_day_or_the_machine(self):
        cargo = self.row(7, 'юк ташиш (дизел)', '2026-09-01', '2026-09-01')
        lint = self.row(7, 'Тола ташиш (ички)', '2026-09-03', '2026-09-03')
        self.assertEqual(self.classes([cargo, lint], [cargo, lint]), [
            (7, '2026-09-01', 'hauling', 'other'),
            (7, '2026-09-02', 'no_app', 'cotton'),
            (7, '2026-09-03', 'hauling', 'cotton')])

    def test_cotton_picking_is_not_a_cotton_haul(self):
        # «пахта» в имени вида без «ташиш» -- уборка, не перевозка: ни сутки
        # перевозки груза, ни машину в группу хлопка она не переводит
        cargo = self.row(7, 'Юк ташиш', '2026-09-01', '2026-09-01')
        picking = self.row(7, 'Пахта териш', '2026-09-01', '2026-09-03')
        self.assertEqual(self.classes([cargo, picking], [cargo]), [
            (7, '2026-09-01', 'hauling', 'other'),
            (7, '2026-09-02', 'other_app', 'other'),
            (7, '2026-09-03', 'other_app', 'other')])

    def test_only_machines_of_picked_hauls_and_no_machine_is_skipped(self):
        haul = self.row(7, 'Пахта ташиш', '2026-09-01', '2026-09-01')
        stranger = self.row(8, 'Пахта ташиш', '2026-09-01', '2026-09-01')
        orphan = self.row(None, 'Пахта ташиш', '2026-09-01', '2026-09-01')
        days = self.classes([haul, stranger, orphan], [haul, orphan])
        self.assertEqual({unit for unit, *_rest in days}, {7})
        self.assertEqual(len(days), 3)

    def test_cotton_marks(self):
        self.assertTrue(probe.is_cotton('ПАХТА ташиш Мирзачўл'))
        self.assertTrue(probe.is_cotton('тола ташиш (кўча)'))
        self.assertFalse(probe.is_cotton('Юк ташиш (хар хил кўча)'))
        self.assertFalse(probe.is_cotton(None))


class Visits(unittest.TestCase):
    """Заезды по точкам -- без файлов."""

    def setUp(self):
        polygon = shapely.Polygon([(0, 0), (100, 0), (100, 100), (0, 100)])
        other = shapely.Polygon([(200, 0), (300, 0), (300, 100), (200, 100)])
        self.zones = [(1, 'ptm', polygon), (2, 'azs', other)]

    def points(self, xs, step=60):
        # координаты UTM -> градусы обратным счётом через to_utm не нужны:
        # подставляем зону в проекции, а точки -- в той же проекции
        return [(i * step, x, 50.0) for i, x in enumerate(xs)]

    def found(self, xs):
        with unittest.mock.patch.object(probe, 'to_utm', lambda a, b: (a, b)):
            return probe.visits(self.points(xs), self.zones)

    def test_runs_and_entries(self):
        found = self.found([-50, 50, 50, 150, 250, 250, 50, -50])
        self.assertEqual([(v['zone'], v['entered'], v['points']) for v in found],
                         [(1, True, 2), (2, True, 2), (1, True, 1)])

    def test_the_first_run_is_not_an_entry(self):
        found = self.found([50, 50, -50, 50])
        self.assertEqual([(v['zone'], v['entered']) for v in found],
                         [(1, False), (1, True)])

    def test_zone_to_zone_is_an_entry(self):
        found = self.found([-50, 250, 50])
        self.assertEqual([(v['zone'], v['entered']) for v in found],
                         [(2, True), (1, True)])

    def test_no_points_no_visits(self):
        self.assertEqual(probe.visits([], self.zones), [])

    def test_a_stop_from_five_minutes_inclusive(self):
        found = [{'kind': 'ptm', 't_in': 0, 't_out': probe.STOP_S, 'entered': True},
                 {'kind': 'ptm', 't_in': 0, 't_out': probe.STOP_S - 1, 'entered': True},
                 {'kind': 'ptm', 't_in': 0, 't_out': 900, 'entered': False},
                 {'kind': 'pakhta_zavod', 't_in': 0, 't_out': 600, 'entered': True},
                 {'kind': 'azs', 't_in': 0, 't_out': 600, 'entered': True}]
        self.assertEqual(probe.summarize(found), {
            'zaezdy_ptm': 2, 'zaezdy_ptm_5min': 1, 'zaezdy_zavod': 1,
            'zaezdy_zavod_5min': 1, 'zaezdy_priem_5min': 2, 'zaezdy_prochie': 1})

    def test_compare(self):
        self.assertEqual([probe.compare(v, h) for v, h in
                          ((3, 3), (3, 4), (3, 2), (3, 5), (3, 1), (None, 2))],
                         ['equal', 'within 1', 'within 1', 'more', 'fewer',
                          'no volume'])

    def test_zone_assignment_uses_the_projection(self):
        # настоящая проекция: контур -- как у правила территорий, точки -- в
        # градусах; центр квадрата ПТМ -- в зоне, 500 м восточнее -- нет
        center = SQUARES[PTM]
        zone = zone_polygon(json.dumps(square(center)))
        inside = xy_to_lonlat(*center)
        outside = xy_to_lonlat(center[0] + 500, center[1])
        where = probe.zone_of_points([(0,) + inside, (1,) + outside],
                                     [(PTM, 'ptm', zone)])
        self.assertEqual(list(where), [0, -1])


class Refusals(unittest.TestCase):

    def base(self, **changes):
        args = {'--db': WORLD['db'], '--dir': WORLD['folder'], '--from': SINCE,
                '--to': UNTIL, '--csv': os.path.join(WORLD['root'], 'x.csv'),
                '--visits-csv': os.path.join(WORLD['root'], 'y.csv'),
                '--zones': WORLD['zones']}
        args.update(changes)
        argv = []
        for key, value in args.items():
            argv += [key, value]
        return run_main(*argv)

    def test_bad_input_is_code_two_and_nothing_is_created(self):
        missing = os.path.join(WORLD['root'], 'nope.db')
        for changes in ({'--db': missing}, {'--from': '01.09.2026'},
                        {'--to': '2026-08-01'},
                        {'--dir': os.path.join(WORLD['root'], 'nodir')},
                        {'--zones': os.path.join(WORLD['root'], 'nozones.csv')},
                        {'--csv': os.path.join(WORLD['root'], 'nodir', 'a.csv')}):
            with self.subTest(changes=changes):
                code, out = self.base(**changes)
                self.assertEqual(code, probe.EXIT_BAD_INPUT, out)
                self.assertFalse(os.path.exists(os.path.join(WORLD['root'], 'x.csv')))
        self.assertFalse(os.path.exists(missing))

    def test_an_unwritable_control_csv_is_refused_before_reading(self):
        code, out = run_main('--db', WORLD['db'], '--dir', WORLD['folder'],
                             '--from', SINCE, '--to', UNTIL,
                             '--csv', os.path.join(WORLD['root'], 'x.csv'),
                             '--visits-csv', os.path.join(WORLD['root'], 'y.csv'),
                             '--zones', WORLD['zones'], '--hauling',
                             '--control-csv',
                             os.path.join(WORLD['root'], 'nodir', 'c.csv'))
        self.assertEqual(code, probe.EXIT_BAD_INPUT, out)
        self.assertFalse(os.path.exists(os.path.join(WORLD['root'], 'x.csv')))

    def test_a_zone_list_without_zones_is_refused(self):
        empty = os.path.join(WORLD['root'], 'empty.csv')
        with open(empty, 'w', encoding='utf-8') as handle:
            handle.write('zona_id,gruppa_id,tip,ploshchad_ga\n')
        code, out = self.base(**{'--zones': empty})
        self.assertEqual(code, probe.EXIT_BAD_INPUT)
        self.assertIn('lists no zones', out)


class Imports(unittest.TestCase):

    def test_flask_is_not_imported(self):
        code = ('import sys; import tools.gps_trips_probe; '
                'print("flask" in sys.modules, "models" in sys.modules)')
        out = subprocess.run([sys.executable, '-c', code], cwd=REPO,
                             capture_output=True, text=True, check=True).stdout
        self.assertEqual(out.strip(), 'False False')


if __name__ == '__main__':
    unittest.main()
