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
  * ничего не пишется, кроме двух CSV; в консоль -- ASCII; Flask не
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
        for number, unit in enumerate((801, 802, 804, 805, 806, 807), start=1):
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
                ('w-trip', 'Пахта ташиш', 'TRIP', 'reysy'),
                ('w-ga', 'Чизеллаш', 'HECTARE', 'ga')):
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
    WORLD.update(root=root, folder=folder, db=db, zones=zones_csv, code=code,
                 out=out, csv=out_csv, visits=visits_csv, before=before)


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
        self.assertEqual(a['vid_raboty'], 'Пахта ташиш')

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
