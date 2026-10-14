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

8. **Объект, удалённый из Wialon, не пропадает из инвентаря**, пока у него
   есть сутки в окне: 14.10 владелец назвал четыре фермерских объекта,
   которых «сейчас нет в Виалоне», а их гектары считались -- и инвентарь их
   не видел.

9. **Машины без объекта Wialon видны со своей стороны** -- с кандидатами по
   госномеру: «связать которые не связаны» (14.10) иначе не из чего
   предложить.

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

    def test_a_special_row_next_to_a_field_row_is_a_contradiction(self):
        # Строка на погрузчик и строка на трактор у одного id: расчёт считает
        # гектары (противоречие не решается за человека), и инвентарь обязан
        # это назвать, а не показать «полевая».
        server = self.world()
        loader = self.equipment('Pogruzchik', 'special')
        tractor = self.equipment('MTZ 80', 'mtz')
        self.mapping('Pogruzchik 373 HA', wialon_id=ORPHAN, equipment_id=loader)
        self.mapping('MTZ 80 373 HA', wialon_id=ORPHAN, equipment_id=tractor)
        self.run_tool(server)
        row = self.by_id()[ORPHAN]
        self.assertEqual(row['status'], inv.CONTRADICTION)
        self.assertEqual(row['v_plan_fakte'], 'da')

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

    def test_a_special_machine_counts_its_track_days_and_no_hectares(self):
        """A1: у спецтехники сутки со следом посчитаны, гектаров ноль.

        [REASON]: полигон с 2,0 га лежит в базе нарочно -- расчёт не удаляет
        полигоны, оставшиеся от счёта до решения 28.09. Сумма по всем полигонам
        показала бы владельцу 2,0 га, которых нет ни на экране, ни в сверке.
        """
        server = self.world()
        loader = self.equipment('Pogruzchik', 'special', '80 373 HA')
        self.mapping('Pogruzchik 373 HA', wialon_id=ORPHAN, equipment_id=loader)
        self.aggregate(ORPHAN, 2, reason='spetstekhnika', km=4.5, area=2.0)
        self.run_tool(server)
        row = self.by_id()[ORPHAN]
        self.assertEqual(row['status'], inv.TRACK_ONLY)
        self.assertEqual(row['v_plan_fakte'], 'da')
        self.assertEqual(row['pochemu_net'], '')
        self.assertEqual(row['days_computed'], '1')
        self.assertEqual(float(row['km_total']), 4.5)
        self.assertEqual(float(row['ha_total']), 0.0)

    def test_hectares_of_a_day_with_a_reason_are_not_summed(self):
        # sbor_nepolnyy тоже оставляет старые полигоны. Отрицательный контроль
        # рядом: опубликованные сутки того же объекта свои гектары дают.
        server = self.world()
        self.aggregate(TRACTOR, 2, reason='sbor_nepolnyy', area=9.0)
        self.aggregate(TRACTOR, 3, km=7.5, area=1.75)
        self.run_tool(server)
        self.assertEqual(float(self.by_id()[TRACTOR]['ha_total']), 1.75)

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

    def test_the_special_category_is_marked_track_only_in_the_summary(self):
        loader = self.equipment('Pogruzchik', 'special')
        server = self.world()
        self.mapping('Pogruzchik 373 HA', wialon_id=ORPHAN, equipment_id=loader)
        code, log = self.run_tool(server)
        self.assertEqual(code, 0, log)
        self.assertIn('bez_ga', log)
        self.assertIn('track only, no hectares', log)
        log.encode('ascii')



class GoneFromWialon(Base):
    """Пункт 8."""

    FARMER = 3557

    def collector_name(self, unit_id, name):
        con = storage.open_units(self.folder)
        try:
            con.execute('INSERT INTO collector_units (unit_id, name, seen_at) '
                        'VALUES (?, ?, ?)', (unit_id, name, '2026-10-12 06:00:00'))
            con.commit()
        finally:
            con.close()

    def test_an_object_with_days_but_no_place_in_wialon_is_listed(self):
        server = self.world()
        self.collector_name(self.FARMER, 'Arion 630 ф/х')
        self.aggregate(self.FARMER, 3, area=6.2)
        self.points(self.FARMER, 3)
        code, log = self.run_tool(server)
        self.assertEqual(code, 0, log)
        row = self.by_id()[self.FARMER]
        self.assertEqual(row['v_wialon'], 'net')
        self.assertEqual(row['status'], inv.NO_ROW)
        # Сутки его на экране, значит считается -- и это надо сказать прямо.
        self.assertEqual(row['v_plan_fakte'], 'da')
        self.assertEqual(row['wialon_name'], 'Arion 630 ф/х')
        self.assertEqual(float(row['ha_total']), 6.2)
        self.assertIn('of them not in Wialon  : 1', log)

    def test_an_object_gone_long_ago_without_days_in_the_window_is_not_listed(self):
        # Отрицательный контроль окна: иначе в инвентарь попадала бы вся
        # история парка.
        server = self.world()
        self.collector_name(self.FARMER, 'Arion 630 ф/х')
        self.aggregate(self.FARMER, inv.WINDOW_DAYS + 5, area=6.2)
        self.run_tool(server)
        self.assertNotIn(self.FARMER, self.by_id())

    def test_objects_in_wialon_say_so(self):
        self.run_tool(self.world())
        self.assertEqual({row['v_wialon'] for row in self.csv_rows()}, {'da'})

    def test_an_object_marked_not_ours_is_listed_once_as_gone(self):
        server = self.world()
        self.collector_name(self.FARMER, 'Arion 630 ф/х')
        self.aggregate(self.FARMER, 3, area=6.2)
        self.mapping('Arion 630 ф/х', wialon_id=self.FARMER, skip=1)
        self.run_tool(server)
        rows = [row for row in self.csv_rows()
                if row['wialon_id'] == str(self.FARMER)]
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]['status'], inv.GONE)
        self.assertEqual(rows[0]['v_plan_fakte'], 'net')

    def test_a_live_object_without_a_row_but_with_days_is_listed_once(self):
        # Самый частый случай парка: объект в Wialon, строки нет, сутки есть.
        server = self.world()
        self.points(ORPHAN, 2)
        self.aggregate(ORPHAN, 2, area=1.0)
        code, log = self.run_tool(server)
        self.assertEqual(code, 0, log)
        rows = [row for row in self.csv_rows() if row['wialon_id'] == str(ORPHAN)]
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]['v_wialon'], 'da')
        self.assertIn('of them not in Wialon  : 0', log)

    def test_an_object_gone_from_wialon_with_points_only_is_listed(self):
        # Точки есть, опубликованных суток нет (все отказаны) -- объект всё
        # равно должен быть виден: его трек ещё собирается.
        server = self.world()
        self.collector_name(self.FARMER, 'Arion 630 ф/х')
        self.points(self.FARMER, 3)
        self.aggregate(self.FARMER, 3, reason='redkaya_zapis')
        self.run_tool(server)
        self.assertEqual(self.by_id()[self.FARMER]['v_wialon'], 'net')

    def test_a_vanished_mapping_row_is_not_in_wialon_and_not_counted(self):
        server = self.world()
        tractor = self.equipment('MTZ 80', 'mtz')
        self.mapping('Snyatyy s ucheta', wialon_id=999999, equipment_id=tractor)
        code, log = self.run_tool(server)
        self.assertEqual(code, 0, log)
        self.assertEqual(self.by_id()[999999]['v_wialon'], 'net')
        self.assertIn('of them not in Wialon  : 0', log)

    def test_the_summary_with_a_gone_object(self):
        # «Объектов в Wialon» -- только живые; удалённый, но считаемый -- в
        # «считается», и он же -- в «из них нет в Wialon».
        server = self.world()
        self.collector_name(self.FARMER, 'Arion 630 ф/х')
        self.aggregate(self.FARMER, 3, area=6.2)
        code, log = self.run_tool(server)
        self.assertEqual(code, 0, log)
        self.assertIn('objects in Wialon        : 5', log)
        self.assertIn('counted by plan-fact     : 4', log)
        self.assertIn('of them not in Wialon  : 1', log)

    def test_a_row_found_by_name_is_shown_for_an_object_gone_from_wialon(self):
        # Строка есть, id ей не поставлен (связка ищет только живые объекты):
        # объект считается, и инвентарь обязан показать, чья это строка.
        server = self.world()
        self.collector_name(self.FARMER, 'Arion 630 ф/х')
        self.aggregate(self.FARMER, 3, area=6.2)
        row_id = self.mapping('Arion 630 ф/х')
        self.run_tool(server)
        row = self.by_id()[self.FARMER]
        self.assertEqual(row['status'], inv.NO_EQUIPMENT)
        self.assertEqual(row['mapping_id'], str(row_id))



class UnlinkedEquipment(Base):
    """Пункт 9."""

    PICKER = 8806

    def equipment_rows(self):
        path = os.path.join(self.folder, 'gps_equipment_unlinked.csv')
        with open(path, encoding='utf-8-sig', newline='') as fh:
            return {int(row['equipment_id']): row
                    for row in csv.DictReader(fh, delimiter=';')}

    def test_a_machine_without_an_object_is_listed_with_its_plate_candidate(self):
        server = self.world()
        picker = self.equipment('John Deere 9970', 'kombayn', '80 543 JA')
        last = int(self.today.timestamp()) - 600
        server.units.append((self.PICKER, 'JohnDeere 80 543 JA', last))
        code, log = self.run_tool(server)
        self.assertEqual(code, 0, log)
        row = self.equipment_rows()[picker]
        self.assertIn('%d [%s] JohnDeere 80 543 JA' % (self.PICKER, inv.NO_ROW),
                      row['kandidaty'])
        self.assertIn('machines without a Wialon object : ', log)

    def test_a_linked_machine_is_not_listed(self):
        server = self.world()
        code, log = self.run_tool(server)
        self.assertEqual(code, 0, log)
        tractor_ids = {int(row['equipment_id']) for row in self.equipment_rows()
                       .values() if row['name'] == 'MTZ 892'}
        self.assertEqual(tractor_ids, set())

    def test_a_row_without_an_id_does_not_count_as_a_link(self):
        # Строка по имени без wialon_id расчёту ничего не даёт: машина без
        # связи, и строку надо показать -- её и связывать.
        server = self.world()
        picker = self.equipment('John Deere 9970', 'kombayn', '80 543 JA')
        row_id = self.mapping('JohnDeere 80 543 JA', equipment_id=picker)
        self.run_tool(server)
        row = self.equipment_rows()[picker]
        self.assertEqual(row['mapping_id'], str(row_id))
        self.assertEqual(row['vialon_name'], 'JohnDeere 80 543 JA')

    def test_a_machine_without_a_plate_has_no_candidates(self):
        server = self.world()
        loader = self.equipment('Pogruzchik', 'special', '')
        self.run_tool(server)
        self.assertEqual(self.equipment_rows()[loader]['kandidaty'], '')

    def test_a_candidate_gone_from_wialon_says_so(self):
        # Связка GPS-10 удалённый объект не найдёт: подсказка обязана это
        # сказать.
        server = self.world()
        picker = self.equipment('John Deere 9970', 'kombayn', '80 543 JA')
        con = storage.open_units(self.folder)
        try:
            con.execute('INSERT INTO collector_units (unit_id, name, seen_at) '
                        "VALUES (?, 'JohnDeere 80 543 JA', '2026-10-01 06:00:00')",
                        (self.PICKER,))
            con.commit()
        finally:
            con.close()
        self.aggregate(self.PICKER, 3, area=4.0)
        self.run_tool(server)
        self.assertIn('%d [%s, net v Wialon] JohnDeere 80 543 JA'
                      % (self.PICKER, inv.NO_ROW),
                      self.equipment_rows()[picker]['kandidaty'])

    def test_the_summary_line_counts_machines_and_candidates(self):
        server = self.world()
        self.equipment('John Deere 9970', 'kombayn', '80 543 JA')
        self.equipment('Pogruzchik', 'special', '')
        last = int(self.today.timestamp()) - 600
        server.units.append((self.PICKER, 'JohnDeere 80 543 JA', last))
        code, log = self.run_tool(server)
        self.assertEqual(code, 0, log)
        self.assertIn('machines without a Wialon object : 2 (with a plate '
                      'candidate: 1)', log)

    def test_the_equipment_query_runs_on_a_readonly_connection(self):
        server = self.world()
        real, seen = inv.unlinked_equipment, []

        def probe(con, items):
            try:
                con.execute('CREATE TABLE probe (x)')
                seen.append('written')
            except sqlite3.OperationalError as problem:
                seen.append(str(problem))
            return real(con, items)

        saved = inv.unlinked_equipment
        inv.unlinked_equipment = probe
        try:
            code, log = self.run_tool(server)
        finally:
            inv.unlinked_equipment = saved
        self.assertEqual(code, 0, log)
        self.assertEqual(len(seen), 1)
        self.assertIn('readonly', seen[0])

    def test_the_equipment_csv_has_a_bom(self):
        self.run_tool(self.world())
        with open(os.path.join(self.folder, 'gps_equipment_unlinked.csv'),
                  'rb') as fh:
            self.assertEqual(fh.read(3), b'\xef\xbb\xbf')

    def test_equipment_out_is_honoured(self):
        server = self.world()
        picker = self.equipment('John Deere 9970', 'kombayn', '80 543 JA')
        target = os.path.join(self.folder, 'mashiny.csv')
        self.run_tool(server, '--equipment-out', target)
        self.assertFalse(os.path.exists(
            os.path.join(self.folder, 'gps_equipment_unlinked.csv')))
        with open(target, encoding='utf-8-sig', newline='') as fh:
            ids = {int(row['equipment_id'])
                   for row in csv.DictReader(fh, delimiter=';')}
        self.assertIn(picker, ids)

    def test_an_inactive_machine_says_so(self):
        server = self.world()
        old = self.equipment('MTZ 50', 'mtz', '80 001 AA')
        con = sqlite3.connect(self.db)
        try:
            con.execute('UPDATE equipment SET is_active = 0 WHERE id = ?', (old,))
            con.commit()
        finally:
            con.close()
        self.run_tool(server)
        self.assertEqual(self.equipment_rows()[old]['is_active'], '0')


if __name__ == '__main__':
    unittest.main()
