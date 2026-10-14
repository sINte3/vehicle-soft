# -*- coding: utf-8 -*-
"""tools/gps_territory_report.py -- участки на территориях подразделений.

Окружение расчёта (геометрия). Синтетическая база в UTM-метрах около Бухары
(64,40; 39,80), переведённых в градусы pyproj без округления, -- площади
точные, и каждое число ниже посчитано руками. Держится:
  * участок целиком на территории -- доля 1, оба варианта снимают его весь;
  * участок наполовину -- доля 0,5, вариант Б его снимает; вариант А
    оставляет внешнюю половину, если она не меньше 0,3 га, и снимает, если
    меньше;
  * участок вне зон в CSV не попадает, но просмотрен и посчитан; участок,
    чья рамка задевает зону, а сам он нет, -- тоже;
  * две наложенные зоны не считаются дважды: на территории не больше
    участка, а строка каждой зоны меряет саму зону;
  * зоны -- от большего перекрытия к меньшему, не больше пяти;
  * сутки с причиной (`spetstekhnika`) не берутся; окно --since/--until
    включает обе границы;
  * зона-«бабочка» чинится, а не роняет прогон; зона без контура (круг в
    зеркале или нет в зеркале) называется, код 0; строка `field_contours`
    другого источника с тем же external_id не подменяет зону;
  * дыра участка остаётся дырой: зона в дыре -- не на участке;
  * доля -- от площади многоугольника, а не от `area_ha` строки;
  * объект: «не наша» по строке skip=1, машина/категория -- по
    непомеченной строке, ответ оператора, contour_id на зону -- da;
  * CSV суток: каждые опубликованные сутки с участком, и без перекрытия
    тоже; участок вне зон и неизмеренный участок остаются в обоих
    вариантах целиком; доля суток -- от многоугольников измеренных
    участков; сутки без участков и сутки с причиной не попадают;
  * база побайтно та же, соединение не может писать, отсутствующая база не
    создаётся; три отчёта попарно разные и не ложатся на базу, её
    -wal/-shm и список зон -- иначе отказ с кодом 2; кривой список зон и
    даты -- код 2;
  * консоль -- ASCII и без имён машин; CSV -- UTF-8 с BOM и `;`;
  * нарушенный инвариант -- код 1 и ни одного CSV; сбой GEOS на участке --
    код 3, участок назван;
  * Flask и `models` не импортируются.

Запуск из корня репозитория окружением расчёта:
  python -m unittest gps.tests.test_territory_report -v
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
from unittest import mock

from pyproj import Transformer
from shapely.errors import GEOSException

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(
    os.path.abspath(__file__))))
if REPO_ROOT not in sys.path:
    sys.path.insert(0, REPO_ROOT)

import tools.gps_territory_report as terr                           # noqa: E402
from gps.area import UTM_41N, to_utm                                # noqa: E402

TO_DEGREES = Transformer.from_crs(UTM_41N, 'EPSG:4326', always_xy=True)
_X, _Y = to_utm(64.40, 39.80)
X0, Y0 = round(float(_X)), round(float(_Y))

D0, D1, D2, D_SPEC = '2026-10-01', '2026-10-05', '2026-10-10', '2026-10-06'
U_MTZ, U_ALIEN, U_FREE, U_COMB = 3001, 3002, 3003, 3004
U_FIELD, U_EMPTY = 3005, 3006   # сутки без перекрытия; сутки без участков
MTZ_NAME = 'Трактор МТЗ-80 Ҳамид'
WORK, PASSAGE = 'работа', 'проезд'

DDL = (
    'CREATE TABLE gps_daily_aggregates (id INTEGER PRIMARY KEY AUTOINCREMENT, '
    'work_date DATE NOT NULL, wialon_id INTEGER NOT NULL, reason VARCHAR(40), '
    'method_version VARCHAR(60), UNIQUE (work_date, wialon_id))',
    'CREATE TABLE gps_work_polygons (id INTEGER PRIMARY KEY AUTOINCREMENT, '
    'work_date DATE NOT NULL, wialon_id INTEGER NOT NULL, '
    'site_number INTEGER NOT NULL, area_ha REAL NOT NULL, minutes REAL, '
    'polygon_geojson TEXT NOT NULL, contour_id INTEGER, '
    'operator_label VARCHAR(20), UNIQUE (work_date, wialon_id, site_number))',
    'CREATE TABLE field_contours (id INTEGER PRIMARY KEY AUTOINCREMENT, '
    'source VARCHAR(20) NOT NULL, external_id VARCHAR(100), '
    'name VARCHAR(300) NOT NULL, geometry_geojson TEXT, is_active BOOLEAN, '
    'UNIQUE (source, external_id))',
    'CREATE TABLE vialon_mappings (id INTEGER PRIMARY KEY AUTOINCREMENT, '
    'vialon_name VARCHAR(300) NOT NULL UNIQUE, wialon_id INTEGER, '
    'equipment_id INTEGER, skip BOOLEAN)',
    'CREATE TABLE equipment (id INTEGER PRIMARY KEY AUTOINCREMENT, '
    'name VARCHAR(200) NOT NULL, plate VARCHAR(50), '
    'category VARCHAR(20) NOT NULL)',
)


# --- геометрия: метры от начала -> GeoJSON в градусах ------------------------

def ring(points):
    """Замкнутое кольцо [[lon, lat], ...] через точки в метрах от (X0, Y0)."""
    out = []
    for x, y in list(points) + [points[0]]:
        lon, lat = TO_DEGREES.transform(X0 + x, Y0 + y)
        out.append([lon, lat])
    return out


def rect(x1, y1, x2, y2):
    return [(x1, y1), (x2, y1), (x2, y2), (x1, y2)]


def polygon(*rings):
    return json.dumps({'type': 'Polygon',
                       'coordinates': [ring(points) for points in rings]})


# Зоны списка: id -> (группа, тип, контур в метрах или None)
SIX = (4443, 4446, 4449, 4450, 4451, 4452)
ZONES = {
    520: (16, 'ptm', rect(0, 0, 200, 200)),                     # 4 га
    28: (3, 'azs', rect(1000, 0, 1200, 200)),                   # 4 га
    699: (16, 'mtp', rect(2000, 0, 2200, 200)),                 # 4 га,
    700: (16, 'mtp', rect(2100, 0, 2300, 200)),                 # наложены
    # «бабочка»: два треугольника по 1 га, сходятся в (3100, 100)
    542: (16, 'pakhta_zavod', [(3000, 0), (3200, 200), (3200, 0), (3000, 200)]),
    4442: (16, 'ptm', rect(4110, 110, 4190, 190)),              # в дыре участка
    7345: (16, 'ptm', rect(5000, 0, 5050, 100)),                # 0,5 га
    17922: (16, 'pakhta_zavod', rect(10000, 0, 10800, 800)),    # 64 га
    18402: (3, 'azs', None),            # в зеркале без контура (круг)
    18389: (3, 'azs', None),            # в зеркале нет вовсе
}
# шесть зон шириной 10..60 м и высотой 40 м под одним участком
for _k, _zone in enumerate(SIX):
    _x = 6000 + 100 * _k + 20
    ZONES[_zone] = (16, 'z%d' % _k, rect(_x, 0, _x + 10 * (_k + 1), 40))
NOT_MIRRORED = 18389
NO_OUTLINE = 18402
FIELD_EXTERNAL = 9999          # зона поля, не территория


def write_zones_list(path, zone_ids=None, text=None):
    with open(path, 'w', encoding='utf-8', newline='') as handle:
        if text is not None:
            handle.write(text)
            return
        handle.write('zona_id,gruppa_id,tip\n')
        for zone in zone_ids or ZONES:
            group, tip, _shape = ZONES[zone]
            handle.write('%d,%d,%s\n' % (zone, group, tip))


def build_world(root):
    """База мира теста; возвращает {имя участка: (дата, объект, номер)}."""
    db = os.path.join(root, 'transport.db')
    con = sqlite3.connect(db)
    for statement in DDL:
        con.execute(statement)
    contour_ids = {}
    for zone, (_group, _tip, shape) in ZONES.items():
        if zone == NOT_MIRRORED:
            continue
        cursor = con.execute(
            'INSERT INTO field_contours (source, external_id, name, '
            'geometry_geojson, is_active) VALUES (?, ?, ?, ?, 1)',
            ('wialon', str(zone), 'Зона %d' % zone,
             polygon(shape) if shape else None))
        contour_ids[zone] = cursor.lastrowid
    cursor = con.execute(
        'INSERT INTO field_contours (source, external_id, name, '
        'geometry_geojson, is_active) VALUES (?, ?, ?, ?, 1)',
        ('wialon', str(FIELD_EXTERNAL), 'Поле фермера',
         polygon(rect(1250, 0, 1400, 200))))
    contour_ids[FIELD_EXTERNAL] = cursor.lastrowid
    # Тот же external_id у другого источника, далеко от зоны 520: без
    # фильтра по источнику он подменил бы её контур.
    con.execute('INSERT INTO field_contours (source, external_id, name, '
                'geometry_geojson, is_active) VALUES (?, ?, ?, ?, 1)',
                ('dji', '520', 'DJI', polygon(rect(90000, 0, 90200, 200))))

    con.execute("INSERT INTO equipment (id, name, plate, category) "
                "VALUES (1, ?, '80 123 ABC', 'mtz')", (MTZ_NAME,))
    con.execute("INSERT INTO equipment (id, name, plate, category) "
                "VALUES (2, 'Комбайн Case', '', 'combine')")
    con.execute("INSERT INTO equipment (id, name, plate, category) "
                "VALUES (3, 'Чужой комбайн', '', 'combine')")
    con.execute('INSERT INTO vialon_mappings (vialon_name, wialon_id, '
                'equipment_id, skip) VALUES (?, ?, 1, 0)', ('MTZ', U_MTZ))
    con.execute('INSERT INTO vialon_mappings (vialon_name, wialon_id, '
                'equipment_id, skip) VALUES (?, ?, 2, NULL)', ('CASE', U_COMB))
    con.execute('INSERT INTO vialon_mappings (vialon_name, wialon_id, '
                'equipment_id, skip) VALUES (?, ?, 3, 1)', ('ALIEN', U_ALIEN))

    sites = {}

    def day(date, unit, reason=None):
        con.execute('INSERT INTO gps_daily_aggregates (work_date, wialon_id, '
                    'reason, method_version) VALUES (?, ?, ?, ?)',
                    (date, unit, reason, 'test'))

    def site(name, date, unit, number, area, text, contour=None, label=None):
        con.execute('INSERT INTO gps_work_polygons (work_date, wialon_id, '
                    'site_number, area_ha, minutes, polygon_geojson, '
                    'contour_id, operator_label) VALUES (?, ?, ?, ?, 0, ?, ?, ?)',
                    (date, unit, number, area, text,
                     None if contour is None else contour_ids[contour], label))
        sites[name] = (date, unit, number)

    for date in (D0, D1, D2):
        day(date, U_MTZ)
    day(D_SPEC, U_MTZ, 'spetstekhnika')
    for unit in (U_COMB, U_FREE, U_ALIEN, U_FIELD, U_EMPTY):
        day(D1, unit)

    inside = polygon(rect(50, 50, 150, 150))                      # 1 га
    site('in_d0', D0, U_MTZ, 1, 1.0, inside)
    site('in', D1, U_MTZ, 1, 1.0, inside, contour=520, label=WORK)
    site('in_d2', D2, U_MTZ, 1, 1.0, inside)
    # поле в те же сутки, вдали от зон: 1,5 га
    site('field_d2', D2, U_MTZ, 2, 1.5, polygon(rect(600, 800, 750, 900)))
    site('overlap', D1, U_MTZ, 2, 2.0, polygon(rect(2020, 50, 2220, 150)),
         label=PASSAGE)
    site('spec', D_SPEC, U_MTZ, 1, 1.0, inside)
    site('half_big', D1, U_COMB, 1, 2.0, polygon(rect(1100, 50, 1300, 150)),
         contour=FIELD_EXTERNAL)
    site('half_small', D1, U_COMB, 2, 0.4, polygon(rect(1160, 150, 1240, 200)))
    site('holed', D1, U_COMB, 3, 8.0,
         polygon(rect(4000, 0, 4300, 300), rect(4100, 100, 4200, 200)))
    site('outside', D1, U_FREE, 1, 1.0, polygon(rect(600, 600, 700, 700)))
    # треугольник у угла зоны 520: рамки пересекаются, участки -- нет
    site('near', D1, U_FREE, 2, 1.125,
         polygon([(150, 300), (300, 150), (300, 300)]))
    site('bowtie', D1, U_FREE, 3, 0.36, polygon(rect(3005, 70, 3065, 130)))
    # area_ha строки нарочно не равна площади многоугольника (1 га)
    site('stored', D1, U_FREE, 4, 4.0, polygon(rect(5000, 0, 5100, 100)))
    # многоугольник 2,4 га, строка -- 2,5: вариант Б оставляет area_ha строки
    site('six', D1, U_FREE, 5, 2.5, polygon(rect(6000, 0, 6600, 40)))
    site('unreadable', D1, U_FREE, 6, 0.7,
         json.dumps({'type': 'Point', 'coordinates': [64.4, 39.8]}))
    site('alien', D1, U_ALIEN, 1, 0.4, polygon(rect(10, 155, 110, 195)))
    # сутки, ни один участок которых зон не касается: 3 га; ответ «работа»
    # -- он входит в знаменатель условия включения, хоть и не на территории
    site('field_only', D1, U_FIELD, 1, 3.0, polygon(rect(700, 600, 1000, 700)),
         label=WORK)
    con.commit()
    con.close()
    return db, sites


def run_main(*argv):
    with contextlib.redirect_stdout(io.StringIO()) as out, \
            contextlib.redirect_stderr(io.StringIO()) as err:
        code = terr.main([str(arg) for arg in argv])
    return code, out.getvalue() + err.getvalue()


def digest(path):
    with open(path, 'rb') as handle:
        return hashlib.sha256(handle.read()).hexdigest()


def read_csv(path):
    with open(path, encoding='utf-8-sig', newline='') as handle:
        return list(csv.DictReader(handle, delimiter=';'))


def console_row(output, label):
    """Числа строки разбивки консоли: [участков, stored, on, -A, -B]."""
    for line in output.splitlines():
        if line.startswith('  ') and line[2:].startswith(label + ' '):
            return [float(value) for value in line[2 + len(label):].split()]
    raise AssertionError('no console row %r' % label)


class World(unittest.TestCase):
    """Один прогон по всему миру; тесты читают его CSV и консоль."""

    @classmethod
    def setUpClass(cls):
        cls.root = tempfile.mkdtemp()
        cls.db, cls.sites = build_world(cls.root)
        cls.zones = os.path.join(cls.root, 'zones.csv')
        write_zones_list(cls.zones)
        cls.out = os.path.join(cls.root, 'sites.csv')
        cls.zones_out = os.path.join(cls.root, 'zones_out.csv')
        cls.days_out = os.path.join(cls.root, 'days_out.csv')
        cls.before = digest(cls.db)
        cls.code, cls.output = run_main(
            '--db', cls.db, '--zones', cls.zones, '--out', cls.out,
            '--zones-out', cls.zones_out, '--days-out', cls.days_out)
        cls.after = digest(cls.db)
        cls.rows = read_csv(cls.out)
        cls.zone_rows = {int(row['zona_id']): row for row in read_csv(cls.zones_out)}
        cls.day_rows = read_csv(cls.days_out)

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.root, ignore_errors=True)

    def row(self, name):
        date, unit, number = self.sites[name]
        found = [row for row in self.rows if row['work_date'] == date
                 and int(row['wialon_id']) == unit
                 and int(row['site_number']) == number]
        self.assertLessEqual(len(found), 1)
        return found[0] if found else None

    def number(self, name, column):
        return float(self.row(name)[column])

    # --- исход прогона ---

    def test_the_run_succeeds(self):
        self.assertEqual(self.code, 0, self.output)

    def test_the_database_is_byte_identical(self):
        self.assertEqual(self.before, self.after)

    # --- доля и варианты ---

    def test_a_site_fully_inside_a_territory(self):
        self.assertEqual(self.number('in', 'dolya'), 1.0)
        self.assertAlmostEqual(self.number('in', 'ga_na_territorii'), 1.0, 4)
        self.assertEqual(self.number('in', 'ga_posle_vychitaniya'), 0.0)
        self.assertEqual(self.number('in', 'ga_posle_otbrosa'), 0.0)
        self.assertEqual(self.row('in')['zony'], '520')
        self.assertEqual(self.row('in')['tipy'], 'ptm')

    def test_half_inside_with_a_large_outside_half(self):
        self.assertEqual(self.number('half_big', 'dolya'), 0.5)
        self.assertAlmostEqual(self.number('half_big', 'ga_na_territorii'), 1.0, 4)
        # А оставляет внешнюю половину (1 га >= 0,3), Б снимает весь участок
        self.assertAlmostEqual(self.number('half_big', 'ga_posle_vychitaniya'),
                               1.0, 4)
        self.assertEqual(self.number('half_big', 'ga_posle_otbrosa'), 0.0)

    def test_half_inside_with_a_small_outside_half(self):
        self.assertEqual(self.number('half_small', 'dolya'), 0.5)
        self.assertAlmostEqual(self.number('half_small', 'ga_na_territorii'),
                               0.2, 4)
        # внешняя половина 0,2 га < 0,3 -- А её отбрасывает
        self.assertEqual(self.number('half_small', 'ga_posle_vychitaniya'), 0.0)
        self.assertEqual(self.number('half_small', 'ga_posle_otbrosa'), 0.0)

    def test_a_share_below_half_is_kept_by_b_and_cut_into_pieces_by_a(self):
        # 2,4 га под шестью зонами: на территории 0,84; между зонами куски
        # 0,36 0,32 0,28 0,24 0,20 и по краям 0,08 -- остаются два первых
        self.assertEqual(self.number('six', 'dolya'), 0.35)
        self.assertAlmostEqual(self.number('six', 'ga_na_territorii'), 0.84, 4)
        self.assertAlmostEqual(self.number('six', 'ga_posle_vychitaniya'), 0.68, 4)
        # Б оставляет участок с долей < 0,5 целиком -- area_ha строки
        self.assertEqual(self.number('six', 'ga_posle_otbrosa'), 2.5)

    def test_zones_are_listed_by_overlap_and_at_most_five(self):
        self.assertEqual(self.row('six')['zony'],
                         ' | '.join(str(zone) for zone in reversed(SIX[1:])))
        self.assertEqual(self.row('six')['tipy'], 'z5 | z4 | z3 | z2 | z1')
        self.assertEqual(self.row('overlap')['zony'], '699 | 700')

    def test_the_share_is_taken_from_the_outline_not_the_stored_area(self):
        # многоугольник 1 га, строка говорит 4 га, зона накрывает половину
        self.assertEqual(self.number('stored', 'dolya'), 0.5)
        self.assertAlmostEqual(self.number('stored', 'ga_na_territorii'), 0.5, 4)
        self.assertAlmostEqual(self.number('stored', 'ga_posle_vychitaniya'),
                               0.5, 4)
        self.assertEqual(self.number('stored', 'area_ha'), 4.0)
        self.assertEqual(self.number('stored', 'ga_posle_otbrosa'), 0.0)

    def test_overlapping_zones_are_not_counted_twice(self):
        # с 699 -- 1,8 га, с 700 -- 1,2 га, участок -- 2 га
        self.assertAlmostEqual(self.number('overlap', 'ga_na_territorii'), 2.0, 4)
        self.assertEqual(self.number('overlap', 'dolya'), 1.0)
        self.assertAlmostEqual(float(self.zone_rows[699]['ga_na_zone']), 1.8, 4)
        self.assertAlmostEqual(float(self.zone_rows[700]['ga_na_zone']), 1.2, 4)

    def test_every_row_keeps_the_invariants(self):
        for row in self.rows:
            with self.subTest(row=(row['work_date'], row['wialon_id'],
                                   row['site_number'])):
                outline = float(row['area_ha'])
                if row is self.row('stored'):
                    outline = 1.0
                elif row is self.row('six'):
                    outline = 2.4
                on = float(row['ga_na_territorii'])
                kept = float(row['ga_posle_vychitaniya'])
                self.assertGreater(on, 0.0)
                self.assertLessEqual(on, outline + 1e-6)
                self.assertGreaterEqual(kept, 0.0)
                self.assertLessEqual(kept, outline - on + 1e-4)
                self.assertTrue(0.0 <= float(row['dolya']) <= 1.0)

    # --- что не попадает в CSV ---

    def test_a_site_outside_every_zone_is_scanned_but_not_listed(self):
        self.assertIsNone(self.row('outside'))
        self.assertIsNone(self.row('near'))
        self.assertIn('sites scanned      : 16,', self.output)
        self.assertIn('far from any zone: 3 ', self.output)
        self.assertIn('near, no overlap : 2 ', self.output)
        self.assertIn('on territory     : 10 ', self.output)
        self.assertIn('outline unreadable: 1, 0.70 ha', self.output)
        self.assertEqual(len(self.rows), 10)

    def test_a_day_with_a_reason_is_ignored(self):
        self.assertIsNone(self.row('spec'))
        self.assertNotIn(D_SPEC, {row['work_date'] for row in self.rows})
        self.assertIn('published days     : 8 machine-days', self.output)

    def test_a_hole_of_the_site_is_not_the_site(self):
        # зона 4442 целиком в дыре участка: пересечения нет
        self.assertIsNone(self.row('holed'))
        self.assertEqual(int(self.zone_rows[4442]['uchastkov']), 0)

    # --- зоны ---

    def test_a_bow_tie_zone_is_repaired(self):
        self.assertEqual(self.number('bowtie', 'dolya'), 1.0)
        self.assertAlmostEqual(self.number('bowtie', 'ga_na_territorii'), 0.36, 4)
        self.assertEqual(self.zone_rows[542]['geometriya'], 'da')
        self.assertAlmostEqual(float(self.zone_rows[542]['ploshchad_ga']), 2.0, 4)

    def test_zones_without_an_outline_are_named_and_the_run_goes_on(self):
        self.assertEqual(self.code, 0)
        for zone in (NOT_MIRRORED, NO_OUTLINE):
            with self.subTest(zone=zone):
                self.assertEqual(self.zone_rows[zone]['geometriya'], 'net')
                self.assertEqual(self.zone_rows[zone]['ploshchad_ga'], '')
                self.assertEqual(self.zone_rows[zone]['uchastkov'], '0')
        self.assertIn('without outline  : %d %d' % (NO_OUTLINE, NOT_MIRRORED),
                      self.output)
        self.assertIn('%d listed, %d with a usable outline, 2 without'
                      % (len(ZONES), len(ZONES) - 2), self.output)

    def test_another_source_does_not_replace_the_zone(self):
        self.assertAlmostEqual(float(self.zone_rows[520]['ploshchad_ga']), 4.0, 4)
        self.assertEqual(self.number('in', 'dolya'), 1.0)

    def test_the_zone_row_measures_its_own_sites(self):
        row = self.zone_rows[520]
        # in_d0, in, in_d2 по 1 га и alien 0,4 га; два объекта, три даты
        self.assertEqual(row['uchastkov'], '4')
        self.assertAlmostEqual(float(row['ga_na_zone']), 3.4, 4)
        self.assertEqual(row['ob_ektov'], '2')
        self.assertEqual(row['dney'], '3')
        self.assertEqual((row['gruppa_id'], row['tip']), ('16', 'ptm'))

    def test_large_zones_are_visible(self):
        self.assertIn('larger than 50 ha: 17922', self.output)
        self.assertIn('largest zones    : 17922 pakhta_zavod 64.00 ha',
                      self.output)

    # --- объект ---

    def test_unit_context(self):
        row = self.row('in')
        self.assertEqual((row['machine'], row['plate'], row['category']),
                         (MTZ_NAME, '80 123 ABC', 'mtz'))
        self.assertEqual(row['isklyuchen'], '')
        self.assertEqual(row['operator_label'], WORK)
        self.assertEqual(self.row('overlap')['operator_label'], PASSAGE)
        self.assertEqual(self.row('half_big')['operator_label'], '')
        self.assertEqual(self.row('half_big')['category'], 'combine')

    def test_an_excluded_unit_is_flagged_and_gets_no_machine(self):
        row = self.row('alien')
        self.assertEqual(row['isklyuchen'], 'ne_nasha')
        self.assertEqual((row['machine'], row['category']), ('', ''))
        self.assertEqual(self.row('bowtie')['isklyuchen'], '')

    def test_contour_pointing_at_a_territory(self):
        self.assertEqual(self.row('in')['kontur_territoriya'], 'da')
        self.assertEqual(self.row('half_big')['kontur_territoriya'], 'net')
        self.assertNotEqual(self.row('half_big')['contour_id'], '')
        self.assertEqual(self.row('half_small')['contour_id'], '')
        self.assertEqual(self.row('half_small')['kontur_territoriya'], 'net')

    # --- порядок и формат ---

    def test_sites_are_sorted_by_hectares_on_territory(self):
        keys = [(-float(row['ga_na_territorii']), row['work_date'],
                 int(row['wialon_id']), int(row['site_number']))
                for row in self.rows]
        self.assertEqual(keys, sorted(keys))
        self.assertEqual(self.rows[0]['zony'], '699 | 700')

    def test_zones_are_sorted_by_hectares_on_the_zone(self):
        values = [float(row['ga_na_zone']) for row in read_csv(self.zones_out)]
        self.assertEqual(values, sorted(values, reverse=True))
        self.assertEqual(len(values), len(ZONES))

    def test_csv_files_are_utf8_with_bom_and_semicolons(self):
        for path, columns in ((self.days_out, terr.DAY_CSV_COLUMNS),
                              (self.out, terr.SITE_CSV_COLUMNS),
                              (self.zones_out, terr.ZONE_CSV_COLUMNS)):
            with self.subTest(path=os.path.basename(path)):
                with open(path, 'rb') as handle:
                    raw = handle.read()
                self.assertTrue(raw.startswith(b'\xef\xbb\xbf'))
                header = raw[3:].split(b'\r\n')[0].decode('utf-8')
                self.assertEqual(header, ';'.join(columns))
        with open(self.out, 'rb') as handle:
            self.assertIn(MTZ_NAME.encode('utf-8'), handle.read())

    def test_site_columns_are_exactly_the_agreed_ones(self):
        self.assertEqual(terr.SITE_CSV_COLUMNS, (
            'work_date', 'wialon_id', 'site_number', 'machine', 'plate',
            'category', 'isklyuchen', 'area_ha', 'dolya', 'ga_na_territorii',
            'ga_posle_vychitaniya', 'ga_posle_otbrosa', 'zony', 'tipy',
            'contour_id', 'kontur_territoriya', 'operator_label'))

    # --- консоль ---

    def test_console_is_ascii_and_names_no_machine(self):
        self.assertTrue(self.output.isascii())
        for word in ('Traktor', 'Hamid', 'Kombajn', 'Case', 'Chuzhoj'):
            self.assertNotIn(word, self.output)
        self.assertIn('nothing was written to the database: it was opened '
                      'mode=ro', self.output)

    def test_console_totals(self):
        # на территории 8,30; А снимает 9,38 (многоугольник минус
        # оставленное); Б снимает 12,16 (area_ha строк с долей >= 0,5)
        self.assertIn('totals             : 8.30 ha on territory', self.output)
        self.assertIn('removes 9.38 ha; variant B', self.output)
        self.assertIn('>= 0.5) removes 12.16 ha', self.output)

    def test_console_splits(self):
        self.assertEqual(console_row(self.output, 'ne_nasha'),
                         [1, 0.40, 0.40, 0.40, 0.40])
        self.assertEqual(console_row(self.output, 'counted')[0], 9)
        self.assertEqual(console_row(self.output, 'mtz'),
                         [4, 5.0, 5.0, 5.0, 5.0])
        self.assertEqual(console_row(self.output, 'combine')[0], 2)
        self.assertEqual(console_row(self.output, '(no machine)')[0], 4)
        self.assertEqual(console_row(self.output, 'rabota'),
                         [1, 1.0, 1.0, 1.0, 1.0])
        self.assertEqual(console_row(self.output, 'proezd')[0], 1)
        self.assertEqual(console_row(self.output, '(no answer)')[0], 8)

    def test_answers_are_totalled_over_every_scanned_site(self):
        # «работа» -- участок на территории (1 га) и поле без перекрытия
        # (3 га); «проезд» -- один участок 2 га; день с причиной не в счёт.
        self.assertIn('answered rabota   : 2, 4.00 ha (all scanned sites',
                      self.output)
        self.assertIn('answered proezd   : 1, 2.00 ha', self.output)

    def test_console_share_bins(self):
        self.assertEqual(console_row(self.output, '[0.9,1]')[:2], [6, 5.76])
        self.assertEqual(console_row(self.output, '[0.5,0.9)')[:2], [3, 6.4])
        self.assertEqual(console_row(self.output, '[0.1,0.5)')[:2], [1, 2.5])
        self.assertEqual(console_row(self.output, '(0,0.1)')[:2], [0, 0.0])

    # --- сутки ---

    def day(self, date, unit):
        found = [row for row in self.day_rows
                 if (row['work_date'], int(row['wialon_id'])) == (date, unit)]
        self.assertLessEqual(len(found), 1)
        return found[0] if found else None

    def test_a_day_with_a_territory_site_and_a_field_site(self):
        # на территории 1 га (доля 1,0), поле 1,5 га вне зон
        row = self.day(D2, U_MTZ)
        self.assertEqual(row['uchastkov'], '2')
        self.assertAlmostEqual(float(row['ga_vsego']), 2.5, 4)
        self.assertAlmostEqual(float(row['ga_na_territorii']), 1.0, 4)
        self.assertEqual(float(row['dolya_dnya']), 0.4)
        # оба варианта снимают участок на территории, поле остаётся
        self.assertAlmostEqual(float(row['ga_posle_vychitaniya']), 1.5, 4)
        self.assertAlmostEqual(float(row['ga_posle_otbrosa']), 1.5, 4)
        self.assertEqual((row['machine'], row['category'], row['isklyuchen']),
                         (MTZ_NAME, 'mtz', ''))

    def test_a_day_without_overlap_is_listed_whole(self):
        row = self.day(D1, U_FIELD)
        self.assertIsNotNone(row)
        self.assertEqual(row['uchastkov'], '1')
        self.assertEqual(float(row['ga_vsego']), 3.0)
        self.assertEqual(float(row['ga_na_territorii']), 0.0)
        self.assertEqual(float(row['dolya_dnya']), 0.0)
        self.assertEqual(float(row['ga_posle_vychitaniya']), 3.0)
        self.assertEqual(float(row['ga_posle_otbrosa']), 3.0)
        self.assertEqual(row['machine'], '')

    def test_a_mixed_day_with_an_unreadable_outline(self):
        # U_FREE 05.10: outside 1, near 1,125, bowtie 0,36 (на территории),
        # stored 4,0 (многоугольник 1, на территории 0,5), six 2,5
        # (многоугольник 2,4, на территории 0,84), unreadable 0,7.
        row = self.day(D1, U_FREE)
        self.assertEqual(row['uchastkov'], '6')
        self.assertAlmostEqual(float(row['ga_vsego']), 9.685, 4)
        self.assertAlmostEqual(float(row['ga_na_territorii']), 1.7, 4)
        # доля -- от многоугольников измеренных участков: 1,7 / 5,885
        self.assertEqual(float(row['dolya_dnya']), 0.289)
        # А: 1 + 1,125 + 0 + 0,5 + 0,68 + 0,7; Б: 1 + 1,125 + 0 + 0 + 2,5 + 0,7
        self.assertAlmostEqual(float(row['ga_posle_vychitaniya']), 4.005, 4)
        self.assertAlmostEqual(float(row['ga_posle_otbrosa']), 5.325, 4)

    def test_days_cover_every_published_day_with_a_site_and_only_those(self):
        keys = [(row['work_date'], int(row['wialon_id'])) for row in self.day_rows]
        self.assertEqual(keys, sorted(keys))
        self.assertEqual(keys, [(D0, U_MTZ), (D1, U_MTZ), (D1, U_ALIEN),
                                (D1, U_FREE), (D1, U_COMB), (D1, U_FIELD),
                                (D2, U_MTZ)])
        self.assertIsNone(self.day(D1, U_EMPTY))
        self.assertIsNone(self.day(D_SPEC, U_MTZ))
        self.assertIn('with a site      : 7, of them with a site on territory: 6',
                      self.output)

    def test_days_add_up_to_the_sites(self):
        on_days = sum(float(row['ga_na_territorii']) for row in self.day_rows)
        on_sites = sum(float(row['ga_na_territorii']) for row in self.rows)
        self.assertAlmostEqual(on_days, on_sites, 3)
        self.assertEqual(sum(int(row['uchastkov']) for row in self.day_rows), 16)
        self.assertEqual(self.day(D1, U_ALIEN)['isklyuchen'], 'ne_nasha')


class Window(unittest.TestCase):

    def setUp(self):
        self.root = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.root, True)
        self.db, self.sites = build_world(self.root)
        self.zones = os.path.join(self.root, 'zones.csv')
        write_zones_list(self.zones)
        self.out = os.path.join(self.root, 'sites.csv')
        self.zones_out = os.path.join(self.root, 'zones_out.csv')
        self.days_out = os.path.join(self.root, 'days_out.csv')

    def run_window(self, *extra):
        code, output = run_main('--db', self.db, '--zones', self.zones,
                                '--out', self.out, '--zones-out', self.zones_out,
                                '--days-out', self.days_out,
                                *extra)
        self.assertEqual(code, 0, output)
        return {row['work_date'] for row in read_csv(self.out)}, output

    def test_both_bounds_are_inclusive(self):
        dates, output = self.run_window('--since', D1, '--until', D1)
        self.assertEqual(dates, {D1})
        self.assertEqual({row['work_date'] for row in read_csv(self.days_out)},
                         {D1})
        self.assertIn('window covered     : %s .. %s' % (D1, D1), output)
        dates, _ = self.run_window('--since', D0, '--until', D1)
        self.assertEqual(dates, {D0, D1})
        dates, _ = self.run_window('--since', D1)
        self.assertEqual(dates, {D1, D2})

    def test_no_window_takes_every_day(self):
        dates, output = self.run_window()
        self.assertEqual(dates, {D0, D1, D2})
        self.assertIn('window covered     : %s .. %s' % (D0, D2), output)


class ReadOnly(unittest.TestCase):

    def setUp(self):
        self.root = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.root, True)
        self.db, _ = build_world(self.root)
        self.zones = os.path.join(self.root, 'zones.csv')
        write_zones_list(self.zones)
        self.out = os.path.join(self.root, 'sites.csv')
        self.zones_out = os.path.join(self.root, 'zones_out.csv')
        self.days_out = os.path.join(self.root, 'days_out.csv')

    def run_tool(self, **change):
        args = {'--db': self.db, '--zones': self.zones, '--out': self.out,
                '--zones-out': self.zones_out, '--days-out': self.days_out}
        args.update(change)
        argv = []
        for key, value in args.items():
            if value is not None:
                argv += [key, value]
        return run_main(*argv)

    def test_the_connection_cannot_write(self):
        # Побайтное равенство базы не отличит mode=ro от обычного соединения:
        # инструмент и так только читает. Здесь -- отличает.
        real, seen = terr.load_zones, []

        def probe(con, listed):
            try:
                con.execute('CREATE TABLE probe (x)')
                seen.append('written')
            except sqlite3.OperationalError as problem:
                seen.append(str(problem))
            return real(con, listed)

        with mock.patch.object(terr, 'load_zones', probe):
            code, output = self.run_tool()
        self.assertEqual(code, 0, output)
        self.assertEqual(len(seen), 1)
        self.assertIn('readonly', seen[0])

    def test_a_missing_database_is_refused_and_not_created(self):
        missing = os.path.join(self.root, 'transprot.db')
        code, output = self.run_tool(**{'--db': missing})
        self.assertEqual(code, 2, output)
        self.assertIn('database not found', output)
        self.assertFalse(os.path.exists(missing))
        self.assertFalse(os.path.exists(self.out))

    def test_outputs_clashing_with_protected_files_are_refused(self):
        before = digest(self.db)
        zones_before = digest(self.zones)
        cases = {
            'out on the db': {'--out': self.db},
            'out on the db wal': {'--out': self.db + '-wal'},
            'zones-out on the db': {'--zones-out': self.db},
            'out on the zones list': {'--out': self.zones},
            'zones-out on the zones list': {'--zones-out': self.zones},
            'out equals zones-out': {'--zones-out': self.out},
            'days-out equals out': {'--days-out': self.out},
            'days-out equals zones-out': {'--days-out': self.zones_out},
            'days-out on the db': {'--days-out': self.db},
            'days-out on the db shm': {'--days-out': self.db + '-shm'},
            'days-out on the zones list': {'--days-out': self.zones},
        }
        for name, change in cases.items():
            with self.subTest(case=name):
                code, output = self.run_tool(**change)
                self.assertEqual(code, 2, output)
                self.assertIn('refusing to overwrite', output)
        self.assertEqual(digest(self.db), before)
        self.assertEqual(digest(self.zones), zones_before)
        self.assertFalse(os.path.exists(self.out))

    def test_an_output_over_another_sqlite_file_is_refused(self):
        other = os.path.join(self.root, 'gps_collector_state.db')
        con = sqlite3.connect(other)
        con.execute('CREATE TABLE t (x)')
        con.commit()
        con.close()
        before = digest(other)
        code, output = self.run_tool(**{'--zones-out': other})
        self.assertEqual(code, 2, output)
        self.assertEqual(digest(other), before)

    def test_an_output_that_cannot_be_written_is_refused(self):
        folder = os.path.join(self.root, 'a_folder')
        os.mkdir(folder)
        code, output = self.run_tool(**{'--out': folder})
        self.assertEqual(code, 2, output)
        self.assertIn('nothing was written', output)
        code, output = self.run_tool(
            **{'--out': os.path.join(self.root, 'no', 'such', 'x.csv')})
        self.assertEqual(code, 2, output)
        self.assertIn('folder for --out does not exist', output)

    def test_bad_dates_are_refused(self):
        for change in ({'--since': '05.10.2026'}, {'--until': '2026-13-01'},
                       {'--since': D2, '--until': D1}):
            with self.subTest(change=change):
                code, output = self.run_tool(**change)
                self.assertEqual(code, 2, output)
                self.assertFalse(os.path.exists(self.out))

    def test_a_bad_zones_list_is_refused(self):
        cases = {
            'missing file': None,
            'semicolons': 'zona_id;gruppa_id;tip\n520;16;ptm\n',
            'no tip column': 'zona_id,gruppa_id\n520,16\n',
            'not a number': 'zona_id,gruppa_id,tip\n52O,16,ptm\n',
            'bad group': 'zona_id,gruppa_id,tip\n520,,ptm\n',
            'listed twice': 'zona_id,gruppa_id,tip\n520,16,ptm\n520,3,azs\n',
            'empty': 'zona_id,gruppa_id,tip\n',
        }
        for number, (name, text) in enumerate(cases.items()):
            with self.subTest(case=name):
                path = os.path.join(self.root, 'list_%d.csv' % number)
                if text is not None:
                    write_zones_list(path, text=text)
                code, output = self.run_tool(**{'--zones': path})
                self.assertEqual(code, 2, output)
                self.assertFalse(os.path.exists(self.out))

    def test_a_missing_table_or_column_is_refused(self):
        con = sqlite3.connect(self.db)
        con.execute('DROP TABLE field_contours')
        con.commit()
        con.close()
        code, output = self.run_tool()
        self.assertEqual(code, 2, output)
        self.assertIn('table field_contours is missing', output)

        root = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, root, True)
        db, _ = build_world(root)
        con = sqlite3.connect(db)
        con.execute('ALTER TABLE gps_work_polygons RENAME COLUMN operator_label '
                    'TO label')
        con.commit()
        con.close()
        code, output = self.run_tool(**{'--db': db})
        self.assertEqual(code, 2, output)
        self.assertIn('gps_work_polygons.operator_label', output)

    def test_a_broken_invariant_fails_and_writes_nothing(self):
        real = terr.measure_site

        def broken(site, territories, tree):
            measured = real(site, territories, tree)
            if measured and measured['overlaps']:
                measured['on_ha'] = measured['geom_ha'] * 1.5
            return measured

        with mock.patch.object(terr, 'measure_site', broken):
            code, output = self.run_tool()
        self.assertEqual(code, 1, output)
        self.assertIn('invariant broken', output)
        self.assertFalse(os.path.exists(self.out))
        self.assertFalse(os.path.exists(self.zones_out))
        self.assertFalse(os.path.exists(self.days_out))

    def test_the_invariants_reject_impossible_numbers(self):
        good = {'geom_ha': 2.0, 'on_ha': 1.0, 'kept_a': 1.0, 'overlaps': [(0, 1.0)]}
        terr.check_invariants('ok', good)
        for change in ({'kept_a': -0.1}, {'kept_a': 2.5}, {'on_ha': 2.1},
                       {'kept_a': 1.5}):
            with self.subTest(change=change):
                with self.assertRaises(terr.InvariantBroken):
                    terr.check_invariants('bad', dict(good, **change))

    def test_a_geometry_failure_names_the_site_and_returns_3(self):
        real = terr.measure_site
        calls = []

        def failing(site, territories, tree):
            calls.append(1)
            if len(calls) == 1:
                raise GEOSException('TopologyException: side location conflict')
            return real(site, territories, tree)

        with mock.patch.object(terr, 'measure_site', failing):
            code, output = self.run_tool()
        self.assertEqual(code, 3, output)
        self.assertIn('geometry failed  : 1 ', output)
        self.assertIn('%s unit %d site 1: TopologyException' % (D0, U_MTZ), output)
        self.assertEqual(len(read_csv(self.out)), 9)
        # неизмеренный участок остаётся в сутках целиком и не входит в долю
        first = read_csv(self.days_out)[0]
        self.assertEqual((first['work_date'], first['wialon_id']), (D0, str(U_MTZ)))
        self.assertEqual(float(first['ga_posle_vychitaniya']), 1.0)
        self.assertEqual(float(first['ga_posle_otbrosa']), 1.0)
        self.assertEqual(float(first['dolya_dnya']), 0.0)


class NoFlask(unittest.TestCase):

    def test_the_tool_imports_neither_flask_nor_models(self):
        code = ('import sys; sys.path.insert(0, "."); '
                'import tools.gps_territory_report; '
                'print(sorted(m for m in ("flask", "models", "app") '
                'if m in sys.modules))')
        result = subprocess.run([sys.executable, '-c', code], cwd=REPO_ROOT,
                                capture_output=True, text=True, timeout=120)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout.strip(), '[]')


if __name__ == '__main__':
    unittest.main()
