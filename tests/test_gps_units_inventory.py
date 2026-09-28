# -*- coding: utf-8 -*-
"""GPS-12: инвентарь объектов Wialon для решения владельца.

Что проверяется и почему именно это:

1. **НИЧЕГО НЕ ПИШЕТ.** Инструмент создан для того, чтобы владелец решал сам, и
   любая его запись в базу была бы решением за владельца. Проверяется двумя
   способами: база после прогона побайтно та же, и `open_readonly` физически
   отвергает запись -- запрет SQLite, а не обещание скрипта.

2. **Колонка «считает ли план-факт» берётся из правила, а не пересказывает
   его.** `gps/exclusion.py` -- один модуль на расчёт и на инструмент; если бы
   инструмент завёл свою копию, он бы врал владельцу про то, что считается.

3. **Статус называет, что известно об объекте**, включая три неудобных случая:
   строки нет вовсе, строка без машины, и строки об одном id спорят.

4. **Активность считается по местным суткам** -- тем же суткам, что у расчёта,
   иначе число в колонке нельзя сверить с экраном.

5. **Пустой парк -- отказ, а не пустой инвентарь.** 621 объект не исчезает за
   ночь, и пустой CSV владелец принял бы за факт.

6. **Отсутствующая база -- отказ, и файл не создаётся.** `sqlite3.connect`
   создал бы пустую базу; пустая вместо боевой -- молчаливая потеря справочника.

7. **Консоль ASCII** при узбекских именах -- та самая буква U+04B2, что убила
   прогон 18.08 на cp1251.

Сети не требуется: сервер заменён консервами.

Запуск:
  python -m unittest tests.test_gps_units_inventory -v
"""
import csv
import hashlib
import io
import json
import os
import sqlite3
import sys
import tempfile
import unittest
import urllib.parse
import urllib.request

from datetime import datetime, timedelta

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if REPO_ROOT not in sys.path:
    sys.path.insert(0, REPO_ROOT)

import tools.gps_units_inventory as inv                             # noqa: E402
from gps_collector import config, storage                           # noqa: E402
from gps.exclusion import excluded_units                            # noqa: E402
from tests.test_gps_link_mappings import FakeServer, Installed      # noqa: E402

DDL = (
    'CREATE TABLE vialon_mappings (id INTEGER PRIMARY KEY AUTOINCREMENT, '
    'vialon_name VARCHAR(300) NOT NULL UNIQUE, wialon_id INTEGER, '
    'equipment_id INTEGER, skip BOOLEAN, created_by INTEGER, '
    'created_at DATETIME, updated_at DATETIME)',
    'CREATE TABLE equipment (id INTEGER PRIMARY KEY AUTOINCREMENT, '
    'name VARCHAR(200) NOT NULL, plate VARCHAR(50), '
    'category VARCHAR(20) NOT NULL, eq_type VARCHAR(100), '
    'organization_id INTEGER NOT NULL, default_price FLOAT, '
    'default_unit VARCHAR(30), is_active BOOLEAN, model_id INTEGER)',
    'CREATE TABLE gps_daily_aggregates (id INTEGER PRIMARY KEY AUTOINCREMENT, '
    'work_date DATE NOT NULL, wialon_id INTEGER NOT NULL, '
    'points_total INTEGER, points_work INTEGER, track_km FLOAT, '
    'interval_median_s FLOAT, sats_median FLOAT, motion_gaps INTEGER, '
    'lost_seconds FLOAT, gps_jumps INTEGER, reason VARCHAR(40), '
    'method_version VARCHAR(50), computed_at DATETIME)',
    'CREATE TABLE gps_work_polygons (id INTEGER PRIMARY KEY AUTOINCREMENT, '
    'work_date DATE NOT NULL, wialon_id INTEGER NOT NULL, '
    'site_number INTEGER, area_ha FLOAT, minutes FLOAT, '
    'polygon_geojson TEXT, contour_id INTEGER, alpha_used_m FLOAT, '
    'pass_spacing_m FLOAT, quality_flag VARCHAR(40), '
    'suggested_label VARCHAR(20), operator_label VARCHAR(20), '
    'decided_at DATETIME)',
)

# Трактор, легковая, помеченный «не наша», спорный, без строки.
TRACTOR, CAR, ALIEN, DISPUTED, ORPHAN = 387, 6001, 5001, 8001, 7001


class Base(unittest.TestCase):
    def setUp(self):
        self.folder = tempfile.mkdtemp()
        self.db = os.path.join(self.folder, 'transport.db')
        self.out = os.path.join(self.folder, 'inventory.csv')
        con = sqlite3.connect(self.db)
        try:
            for statement in DDL:
                con.execute(statement)
            con.commit()
        finally:
            con.close()
        os.environ['WIALON_TOKEN'] = 'test-token-not-a-real-one'
        self.today = datetime.now(config.TZ)

    # --- построение мира -----------------------------------------------------

    def equipment(self, name, category, plate=''):
        con = sqlite3.connect(self.db)
        try:
            cursor = con.execute(
                'INSERT INTO equipment (name, plate, category, '
                'organization_id, is_active) VALUES (?, ?, ?, 1, 1)',
                (name, plate, category))
            con.commit()
            return cursor.lastrowid
        finally:
            con.close()

    def mapping(self, name, wialon_id=None, equipment_id=None, skip=0):
        con = sqlite3.connect(self.db)
        try:
            cursor = con.execute(
                'INSERT INTO vialon_mappings (vialon_name, wialon_id, '
                'equipment_id, skip) VALUES (?, ?, ?, ?)',
                (name, wialon_id, equipment_id, skip))
            con.commit()
            return cursor.lastrowid
        finally:
            con.close()

    def points(self, unit_id, days_ago, count=10, step_s=30):
        """count точек в один местный день, days_ago суток назад."""
        start = int((self.today - timedelta(days=days_ago))
                    .replace(hour=9, minute=0, second=0, microsecond=0)
                    .timestamp())
        storage.write_points(self.folder, [
            (unit_id, start + i * step_s, 64.5, 40.0, 8.0, 90, 14)
            for i in range(count)])

    def aggregate(self, unit_id, days_ago, reason=None, km=1.5, area=None):
        day = (self.today - timedelta(days=days_ago)).strftime('%Y-%m-%d')
        con = sqlite3.connect(self.db)
        try:
            con.execute(
                'INSERT INTO gps_daily_aggregates (work_date, wialon_id, '
                'points_total, points_work, track_km, reason, method_version, '
                'computed_at) VALUES (?, ?, 10, 9, ?, ?, ?, ?)',
                (day, unit_id, km, reason, 'test', '2026-09-27 03:00:00'))
            if area is not None:
                con.execute(
                    'INSERT INTO gps_work_polygons (work_date, wialon_id, '
                    'site_number, area_ha, minutes, polygon_geojson) '
                    'VALUES (?, ?, 1, ?, 30.0, ?)',
                    (day, unit_id, area, '{}'))
            con.commit()
        finally:
            con.close()

    def world(self):
        """Пять объектов, каждый со своей историей. Возвращает FakeServer."""
        car = self.equipment('Nexia', 'passenger', '80 123 ABA')
        tractor = self.equipment('MTZ 892', 'mtz', '80 292 HA')
        self.mapping('MTZ 292 HA', wialon_id=TRACTOR, equipment_id=tractor)
        self.mapping('Nexia 80 123 ABA', wialon_id=CAR, equipment_id=car)
        self.mapping('Chuzhoy kran', wialon_id=ALIEN, skip=1)
        self.mapping('Staryy treker', wialon_id=DISPUTED, skip=1)
        self.mapping('Novyy treker', wialon_id=DISPUTED, skip=0)
        last = int(self.today.timestamp()) - 600
        return FakeServer([(TRACTOR, 'MTZ 292 HA', last),
                           (CAR, 'Nexia 80 123 ABA', last),
                           (ALIEN, 'Chuzhoy kran', last),
                           (DISPUTED, 'Novyy treker', last),
                           (ORPHAN, 'Neizvestnyy obekt', last)])

    # --- запуск -------------------------------------------------------------

    def run_tool(self, server, *extra):
        out = io.StringIO()
        saved = sys.stdout
        sys.stdout = out
        try:
            with Installed(server):
                code = inv.main(['--db', self.db, '--dir', self.folder,
                                 '--out', self.out, '--pause', '0'] + list(extra))
        finally:
            sys.stdout = saved
        return code, out.getvalue()

    def csv_rows(self):
        with open(self.out, encoding='utf-8-sig', newline='') as fh:
            return list(csv.DictReader(fh, delimiter=';'))

    def by_id(self):
        return {int(row['wialon_id']): row for row in self.csv_rows()
                if row['wialon_id']}


class WritesNothing(Base):
    """Пункт 1."""

    def digest(self, path):
        with open(path, 'rb') as fh:
            return hashlib.sha256(fh.read()).hexdigest()

    def test_the_database_is_byte_identical_after_a_run(self):
        server = self.world()
        self.points(TRACTOR, 2, count=50)
        before = self.digest(self.db)
        code, log = self.run_tool(server)
        self.assertEqual(code, 0, log)
        self.assertEqual(self.digest(self.db), before)

    def test_a_readonly_connection_refuses_to_write(self):
        # [REASON]: отрицательный контроль самого запрета. Без него «ничего не
        # пишет» -- обещание: тот же тест прошёл бы и на обычном connect.
        con = inv.open_readonly(self.db)
        try:
            with self.assertRaises(sqlite3.OperationalError) as caught:
                con.execute('DELETE FROM vialon_mappings')
            self.assertIn('readonly', str(caught.exception))
        finally:
            con.close()

    def test_the_point_files_are_opened_readonly_too(self):
        self.points(TRACTOR, 1, count=5)
        path = [p for p in os.listdir(self.folder)
                if p.startswith(storage.POINTS_PREFIX)][0]
        con = inv.open_readonly(os.path.join(self.folder, path))
        try:
            with self.assertRaises(sqlite3.OperationalError):
                con.execute('DELETE FROM points')
        finally:
            con.close()


class Statuses(Base):
    """Пункты 2 и 3."""

    def test_every_object_gets_exactly_one_row(self):
        code, log = self.run_tool(self.world())
        self.assertEqual(code, 0, log)
        rows = self.by_id()
        self.assertEqual(set(rows), {TRACTOR, CAR, ALIEN, DISPUTED, ORPHAN})

    def test_the_tractor_is_counted(self):
        self.run_tool(self.world())
        row = self.by_id()[TRACTOR]
        self.assertEqual(row['status'], inv.FIELD)
        self.assertEqual(row['v_plan_fakte'], 'da')
        self.assertEqual(row['pochemu_net'], '')
        self.assertEqual(row['equipment'], 'MTZ 892')
        self.assertEqual(row['category'], 'mtz')

    def test_the_passenger_car_is_not_counted_and_the_reason_is_the_category(self):
        self.run_tool(self.world())
        row = self.by_id()[CAR]
        self.assertEqual(row['status'], inv.NON_FIELD)
        self.assertEqual(row['v_plan_fakte'], 'net')
        self.assertEqual(row['pochemu_net'], 'ne_polevaya')
        # и в строке НЕ стоит skip: моточасы легковой по-прежнему импортируются
        self.assertEqual(row['skip'], '')

    def test_a_row_marked_not_ours_is_named_so(self):
        self.run_tool(self.world())
        row = self.by_id()[ALIEN]
        self.assertEqual(row['status'], inv.NOT_OURS)
        self.assertEqual(row['pochemu_net'], 'ne_nasha')
        self.assertEqual(row['skip'], '1')

    def test_rows_that_disagree_are_named_a_contradiction_and_still_counted(self):
        self.run_tool(self.world())
        row = self.by_id()[DISPUTED]
        self.assertEqual(row['status'], inv.CONTRADICTION)
        self.assertEqual(row['v_plan_fakte'], 'da')
        self.assertEqual(len(row['mapping_id'].split()), 2)

    def test_an_object_without_a_mapping_row_is_counted(self):
        self.run_tool(self.world())
        row = self.by_id()[ORPHAN]
        self.assertEqual(row['status'], inv.NO_ROW)
        self.assertEqual(row['v_plan_fakte'], 'da')
        self.assertEqual(row['equipment'], '')

    def test_a_row_without_equipment_is_named_so(self):
        server = self.world()
        self.mapping('Neizvestnyy obekt', wialon_id=ORPHAN)
        self.run_tool(server)
        self.assertEqual(self.by_id()[ORPHAN]['status'], inv.NO_EQUIPMENT)

    def test_a_mapping_pointing_at_a_vanished_object_is_reported(self):
        server = self.world()
        tractor = self.equipment('MTZ 80', 'mtz')
        self.mapping('Snyatyy s ucheta', wialon_id=999999,
                     equipment_id=tractor)
        self.run_tool(server)
        row = self.by_id()[999999]
        self.assertEqual(row['status'], inv.GONE)
        self.assertEqual(row['wialon_name'], '')

    def test_the_column_agrees_with_the_shared_rule(self):
        """Пункт 2: колонка не пересказывает правило, а берёт его."""
        self.run_tool(self.world())
        con = sqlite3.connect(self.db)
        try:
            expected = excluded_units(con)
        finally:
            con.close()
        rows = self.by_id()
        for unit_id in (TRACTOR, CAR, ALIEN, DISPUTED, ORPHAN):
            with self.subTest(unit=unit_id):
                self.assertEqual(rows[unit_id]['pochemu_net'],
                                 expected.get(unit_id, ''))


class Activity(Base):
    """Пункт 4."""

    def test_points_are_counted_by_local_day(self):
        server = self.world()
        self.points(TRACTOR, 2, count=40)
        self.points(TRACTOR, 3, count=60)
        self.run_tool(server)
        row = self.by_id()[TRACTOR]
        self.assertEqual(row['days_with_points'], '2')
        self.assertEqual(row['points_total'], '100')
        self.assertTrue(row['first_point'])
        self.assertTrue(row['last_point'])
        self.assertLess(row['first_point'], row['last_point'])

    def test_points_outside_the_window_are_not_counted(self):
        server = self.world()
        self.points(TRACTOR, 2, count=40)
        self.points(TRACTOR, 25, count=99)
        self.run_tool(server, '--window-days', '5')
        row = self.by_id()[TRACTOR]
        self.assertEqual(row['days_with_points'], '1')
        self.assertEqual(row['points_total'], '40')

    def test_computed_days_hectares_and_kilometres_come_from_the_tables(self):
        server = self.world()
        self.aggregate(TRACTOR, 2, km=12.5, area=3.25)
        self.aggregate(TRACTOR, 3, km=7.5, area=1.75)
        self.run_tool(server)
        row = self.by_id()[TRACTOR]
        self.assertEqual(row['days_computed'], '2')
        self.assertEqual(float(row['ha_total']), 5.0)
        self.assertEqual(float(row['km_total']), 20.0)

    def test_a_refused_day_is_not_counted_as_computed(self):
        # [REASON]: сутки с причиной -- это «не посчитано», и показать их как
        # посчитанные значило бы завысить активность объекта в глазах владельца
        # ровно там, где он решает, нужен объект или нет.
        server = self.world()
        self.aggregate(TRACTOR, 2, reason='redkaya_zapis', km=99.0)
        self.run_tool(server)
        row = self.by_id()[TRACTOR]
        self.assertEqual(row['days_computed'], '0')
        self.assertEqual(float(row['km_total']), 0.0)

    def test_an_object_with_no_history_shows_zeroes_not_blanks(self):
        self.run_tool(self.world())
        row = self.by_id()[ORPHAN]
        self.assertEqual(row['days_with_points'], '0')
        self.assertEqual(row['points_total'], '0')
        self.assertEqual(row['first_point'], '')


class Refusals(Base):
    """Пункты 5 и 6."""

    def test_an_empty_fleet_is_refused(self):
        code, log = self.run_tool(FakeServer([]))
        self.assertEqual(code, 3)
        self.assertIn('NOT treating this as an empty fleet', log)
        self.assertFalse(os.path.exists(self.out))

    def test_a_failed_login_names_the_code(self):
        code, saved = 0, sys.stderr
        sys.stderr = io.StringIO()
        try:
            code, log = self.run_tool(FakeServer([], login_error=8))
            problem = sys.stderr.getvalue()
        finally:
            sys.stderr = saved
        self.assertEqual(code, 3)
        self.assertIn('error 8', problem)
        self.assertFalse(os.path.exists(self.out))

    def test_a_missing_database_is_refused_and_not_created(self):
        missing = os.path.join(self.folder, 'nope.db')
        saved, sys.stderr = sys.stderr, io.StringIO()
        try:
            code = inv.main(['--db', missing, '--dir', self.folder,
                             '--out', self.out])
            problem = sys.stderr.getvalue()
        finally:
            sys.stderr = saved
        self.assertEqual(code, 2)
        self.assertIn('database not found', problem)
        self.assertFalse(os.path.exists(missing))

    def test_a_window_of_zero_days_is_refused(self):
        saved, sys.stderr = sys.stderr, io.StringIO()
        try:
            code = inv.main(['--db', self.db, '--dir', self.folder,
                             '--out', self.out, '--window-days', '0'])
        finally:
            sys.stderr = saved
        self.assertEqual(code, 2)


class Console(Base):
    """Пункт 7."""

    def test_the_console_is_ascii_with_uzbek_names(self):
        # U+04B2 «Ҳ» -- та самая буква, что убила прогон 18.08 на cp1251.
        server = FakeServer([(TRACTOR, 'Ҳосил йиғиш 80 292 HA', None)])
        code, log = self.run_tool(server)
        self.assertEqual(code, 0, log)
        log.encode('ascii')  # падает, если в консоль попало не-ASCII

    def test_the_uzbek_name_survives_in_the_csv(self):
        server = FakeServer([(TRACTOR, 'Ҳосил йиғиш 80 292 HA', None)])
        self.run_tool(server)
        self.assertEqual(self.by_id()[TRACTOR]['wialon_name'],
                         'Ҳосил йиғиш 80 292 HA')

    def test_the_csv_opens_in_excel(self):
        # BOM и точка с запятой -- то же, что у gps_link_plan.csv.
        self.run_tool(self.world())
        with open(self.out, 'rb') as fh:
            head = fh.read(3)
        self.assertEqual(head, b'\xef\xbb\xbf')
        with open(self.out, encoding='utf-8-sig') as fh:
            self.assertEqual(fh.readline().strip().split(';'),
                             list(inv.CSV_COLUMNS))

    def test_the_summary_counts_what_is_counted(self):
        code, log = self.run_tool(self.world())
        self.assertEqual(code, 0, log)
        self.assertIn('objects in Wialon        : 5', log)
        # трактор, спорный и без строки -- три
        self.assertIn('counted by plan-fact     : 3', log)
        self.assertIn('passenger', log)
        self.assertIn('not field', log)

    def test_the_categories_nobody_named_are_shown_with_their_counts(self):
        # [REASON]: владелец назвал легковые. Чтобы он мог назвать ещё --
        # мотоциклы, грузовые, -- ему надо видеть, сколько их.
        bike = self.equipment('Honda', 'motorcycle')
        server = self.world()
        self.mapping('Honda 80 777 AAA', wialon_id=ORPHAN, equipment_id=bike)
        code, log = self.run_tool(server)
        self.assertEqual(code, 0, log)
        self.assertIn('motorcycle', log)
        # и мотоцикл ПОСЧИТАН: его никто не исключал
        self.assertEqual(self.by_id()[ORPHAN]['v_plan_fakte'], 'da')


if __name__ == '__main__':
    unittest.main()
