# -*- coding: utf-8 -*-
"""GPS-16: пометка «не наша техника» по списку, утверждённому владельцем.

Что проверяется и почему именно это:

1. **Без `--apply` ничего не пишется** -- база побайтно та же. Инструмент
   пишет в боевой справочник, и сухой прогон -- то, что владелец смотрит
   перед записью.

2. **Запись делает то, ради чего её делают**: после неё правило расчёта
   `gps.exclusion.excluded_units` называет объект «не наша», а строка -- та
   же, что дал бы экран: имя от сборщика, `skip = 1`, машины нет.

3. **Объект, удалённый из Wialon, тоже помечается** -- ради него инструмент и
   написан: связка GPS-10 его уже не находит.

4. **Чужие строки не трогаются.** Строка с этим id, имя, занятое строкой без
   id (в том числе в другом регистре), два id одного списка с одним именем --
   всё это в CSV человеку, а не в базу. Код выхода 3 называет остаток.

5. **Уже исключённое решения не ждёт**: «не наша» и легковая с категорией --
   без новой строки и без кода 3.

6. **Ничего не удаляется**: посчитанные сутки объекта остаются в базе.

7. **Неверный ввод -- отказ до записи**: неизвестный id (опечатка), повтор id,
   отсутствующая база (и файл не создаётся).

8. **Всё или ничего**: если постусловие не сошлось, откатывается вся
   транзакция.

9. **Консоль ASCII** при кириллических именах.

Сети не требуется: инструмент в Wialon не ходит.

Запуск:
  python -m unittest tests.test_gps_mark_not_ours -v
"""
import csv
import hashlib
import io
import os
import sqlite3
import sys
import tempfile
import unittest

from unittest import mock

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if REPO_ROOT not in sys.path:
    sys.path.insert(0, REPO_ROOT)

import tools.gps_mark_not_ours as mark                              # noqa: E402
from gps_collector import storage                                   # noqa: E402
from gps.exclusion import (EXCLUDED_NON_FIELD, EXCLUDED_NOT_OURS,   # noqa: E402
                           excluded_units)

DDL = (
    'CREATE TABLE vialon_mappings (id INTEGER PRIMARY KEY AUTOINCREMENT, '
    'vialon_name VARCHAR(300) NOT NULL UNIQUE, wialon_id INTEGER, '
    'equipment_id INTEGER, skip BOOLEAN, created_by INTEGER, '
    'created_at DATETIME, updated_at DATETIME)',
    'CREATE INDEX ix_vialon_mappings_wialon_id ON vialon_mappings (wialon_id)',
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

# Фермерский трактор в Wialon; фермерский, из Wialon удалённый; объект со
# строкой, которую считают; уже «не наша»; легковая с категорией; трактор,
# чьё имя занято строкой без id; объект без имени у сборщика; два объекта
# одного имени.
FARMER, FARMER_GONE, COUNTED, ALIEN, CAR = 3557, 1935, 387, 5001, 6001
TAKEN, NAMELESS, TWIN_A, TWIN_B = 3537, 3123, 7001, 7002

TODAY = '2026-10-14 06:00:00'
EARLIER = '2026-10-12 06:00:00'


class Base(unittest.TestCase):
    def setUp(self):
        self.folder = tempfile.mkdtemp()
        self.db = os.path.join(self.folder, 'transport.db')
        self.ids = os.path.join(self.folder, 'ids.csv')
        self.out = os.path.join(self.folder, 'plan.csv')
        con = sqlite3.connect(self.db)
        try:
            for statement in DDL:
                con.execute(statement)
            con.commit()
        finally:
            con.close()

    # --- построение мира -----------------------------------------------------

    def sql(self, statement, params=()):
        con = sqlite3.connect(self.db)
        try:
            cursor = con.execute(statement, params)
            con.commit()
            return cursor.lastrowid
        finally:
            con.close()

    def names(self, *units):
        """Имена объектов так, как их пишет сборщик: (id, имя, когда видел)."""
        con = storage.open_units(self.folder)
        try:
            con.executemany('INSERT OR REPLACE INTO collector_units '
                            '(unit_id, name, seen_at) VALUES (?, ?, ?)', units)
            con.commit()
        finally:
            con.close()

    def day(self, unit_id, day, area=None, reason=None):
        self.sql('INSERT INTO gps_daily_aggregates (work_date, wialon_id, '
                 'points_total, track_km, reason, method_version) '
                 'VALUES (?, ?, 100, 12.5, ?, ?)', (day, unit_id, reason, 'test'))
        if area is not None:
            self.sql('INSERT INTO gps_work_polygons (work_date, wialon_id, '
                     'site_number, area_ha, minutes, polygon_geojson) '
                     'VALUES (?, ?, 1, ?, 30.0, ?)', (day, unit_id, area, '{}'))

    def mapping(self, name, wialon_id=None, equipment_id=None, skip=0):
        return self.sql('INSERT INTO vialon_mappings (vialon_name, wialon_id, '
                        'equipment_id, skip) VALUES (?, ?, ?, ?)',
                        (name, wialon_id, equipment_id, skip))

    def equipment(self, name, category):
        return self.sql('INSERT INTO equipment (name, category, '
                        'organization_id, is_active) VALUES (?, ?, 1, 1)',
                        (name, category))

    def listing(self, *rows, header='wialon_id,osnovanie'):
        with open(self.ids, 'w', encoding='utf-8', newline='') as handle:
            handle.write(header + '\n')
            for row in rows:
                handle.write(row + '\n')

    def farmer_world(self):
        """Два фермерских объекта: один в Wialon, другой уже удалён."""
        self.names((FARMER, '6070  Фермер ф/х ', TODAY),
                   (FARMER_GONE, 'New Holland 80 413 FA ф/х', EARLIER),
                   (COUNTED, 'МТЗ-80 292 HA', TODAY))
        self.day(FARMER, '2026-10-05', area=6.6)
        self.day(FARMER, '2026-10-06', area=1.4)
        self.day(FARMER, '2026-10-07', reason='redkaya_zapis')
        self.day(FARMER_GONE, '2026-10-12', area=3.1)

    # --- запуск ---------------------------------------------------------------

    def run_tool(self, *extra):
        out, err = io.StringIO(), io.StringIO()
        saved = sys.stdout, sys.stderr
        sys.stdout, sys.stderr = out, err
        try:
            code = mark.main(['--db', self.db, '--dir', self.folder,
                              '--ids', self.ids, '--out', self.out]
                             + list(extra))
        finally:
            sys.stdout, sys.stderr = saved
        return code, out.getvalue() + err.getvalue()

    def plan_rows(self):
        with open(self.out, encoding='utf-8-sig', newline='') as handle:
            return {int(row['wialon_id']): row
                    for row in csv.DictReader(handle, delimiter=';')}

    def rows_of(self, unit_id):
        con = sqlite3.connect(self.db)
        try:
            return con.execute('SELECT vialon_name, skip, equipment_id, '
                               'created_by, created_at FROM vialon_mappings '
                               'WHERE wialon_id = ?', (unit_id,)).fetchall()
        finally:
            con.close()

    def count_rows(self):
        con = sqlite3.connect(self.db)
        try:
            return con.execute('SELECT COUNT(*) FROM vialon_mappings').fetchone()[0]
        finally:
            con.close()

    def excluded(self):
        con = sqlite3.connect(self.db)
        try:
            return excluded_units(con)
        finally:
            con.close()

    def digest(self):
        with open(self.db, 'rb') as handle:
            return hashlib.sha256(handle.read()).hexdigest()


class DryRun(Base):
    """Пункт 1."""

    def test_the_database_is_byte_identical_after_a_dry_run(self):
        self.farmer_world()
        self.listing('%d,fx_01_10' % FARMER, '%d,vladelets_14_10' % FARMER_GONE)
        before = self.digest()
        code, output = self.run_tool()
        self.assertEqual(code, 0, output)
        self.assertEqual(self.digest(), before)
        self.assertIn('DRY RUN', output)
        plan = self.plan_rows()
        self.assertEqual(plan[FARMER]['status'], mark.MARK)
        self.assertEqual(plan[FARMER_GONE]['status'], mark.MARK)

    def test_the_plan_names_the_hectares_that_will_be_hidden(self):
        # Гектары -- только опубликованных суток: сутки с причиной на экране
        # не видны, и прятать там нечего.
        self.farmer_world()
        self.listing('%d,fx_01_10' % FARMER)
        self.run_tool()
        row = self.plan_rows()[FARMER]
        self.assertEqual(row['days_computed'], '2')
        self.assertEqual(float(row['ha_total']), 8.0)
        self.assertEqual(row['first_day'], '2026-10-05')
        self.assertEqual(row['last_day'], '2026-10-06')
        self.assertEqual(row['osnovanie'], 'fx_01_10')

    def test_a_dry_run_with_rows_left_to_a_person_says_so_by_its_code(self):
        self.farmer_world()
        self.mapping('МТЗ-80 292 HA', wialon_id=COUNTED)
        self.listing('%d,fx_01_10' % FARMER, '%d,fx_01_10' % COUNTED)
        code, output = self.run_tool()
        self.assertEqual(code, 3, output)
        self.assertEqual(self.plan_rows()[COUNTED]['status'], mark.HAS_ROW)


class Apply(Base):
    """Пункты 2, 3, 6."""

    def test_the_rule_of_the_computation_calls_the_marked_object_not_ours(self):
        self.farmer_world()
        self.listing('%d,fx_01_10' % FARMER)
        self.assertNotIn(FARMER, self.excluded())
        code, output = self.run_tool('--apply')
        self.assertEqual(code, 0, output)
        self.assertEqual(self.excluded().get(FARMER), EXCLUDED_NOT_OURS)

    def test_the_row_is_the_one_the_screen_would_create(self):
        self.farmer_world()
        self.listing('%d,fx_01_10' % FARMER)
        self.run_tool('--apply')
        rows = self.rows_of(FARMER)
        self.assertEqual(len(rows), 1)
        name, skip, equipment_id, created_by, created_at = rows[0]
        # Имя нормализовано так же, как его сохраняет экран сопоставления.
        self.assertEqual(name, '6070 Фермер ф/х')
        self.assertEqual(skip, 1)
        self.assertIsNone(equipment_id)
        self.assertIsNone(created_by)
        self.assertTrue(created_at)

    def test_an_object_removed_from_wialon_is_marked_too(self):
        self.farmer_world()
        self.listing('%d,vladelets_14_10' % FARMER_GONE)
        self.run_tool()
        self.assertEqual(self.plan_rows()[FARMER_GONE]['v_wialon'], 'net')
        code, output = self.run_tool('--apply')
        self.assertEqual(code, 0, output)
        self.assertEqual(self.excluded().get(FARMER_GONE), EXCLUDED_NOT_OURS)

    def test_an_object_still_in_wialon_is_named_so(self):
        self.farmer_world()
        self.listing('%d,fx_01_10' % FARMER)
        self.run_tool()
        self.assertEqual(self.plan_rows()[FARMER]['v_wialon'], 'da')

    def test_computed_days_stay_in_the_database(self):
        self.farmer_world()
        self.listing('%d,fx_01_10' % FARMER, '%d,vladelets_14_10' % FARMER_GONE)
        con = sqlite3.connect(self.db)
        try:
            before = con.execute('SELECT COUNT(*), SUM(area_ha) FROM '
                                 'gps_work_polygons').fetchone()
            days_before = con.execute(
                'SELECT COUNT(*) FROM gps_daily_aggregates').fetchone()
        finally:
            con.close()
        self.run_tool('--apply')
        con = sqlite3.connect(self.db)
        try:
            self.assertEqual(con.execute('SELECT COUNT(*), SUM(area_ha) FROM '
                                         'gps_work_polygons').fetchone(), before)
            self.assertEqual(con.execute(
                'SELECT COUNT(*) FROM gps_daily_aggregates').fetchone(),
                days_before)
        finally:
            con.close()

    def test_a_second_apply_writes_nothing_new(self):
        self.farmer_world()
        self.listing('%d,fx_01_10' % FARMER)
        self.run_tool('--apply')
        rows = self.count_rows()
        code, output = self.run_tool('--apply')
        self.assertEqual(code, 0, output)
        self.assertEqual(self.count_rows(), rows)
        self.assertEqual(self.plan_rows()[FARMER]['status'], mark.ALREADY)


class OtherRowsAreNotTouched(Base):
    """Пункты 4 и 5."""

    def test_an_object_with_a_counted_row_is_left_to_a_person(self):
        self.farmer_world()
        row_id = self.mapping('МТЗ-80 292 HA', wialon_id=COUNTED)
        self.listing('%d,fx_01_10' % COUNTED, '%d,fx_01_10' % FARMER)
        code, output = self.run_tool('--apply')
        self.assertEqual(code, 3, output)
        plan = self.plan_rows()
        self.assertEqual(plan[COUNTED]['status'], mark.HAS_ROW)
        self.assertEqual(plan[COUNTED]['mapping_id'], str(row_id))
        self.assertEqual(len(self.rows_of(COUNTED)), 1)
        self.assertNotIn(COUNTED, self.excluded())
        # Остальной список записан: остаток человеку не держит всё.
        self.assertEqual(self.excluded().get(FARMER), EXCLUDED_NOT_OURS)

    def test_a_name_taken_by_a_row_without_id_is_left_to_a_person(self):
        # Другой регистр -- то же имя для человека, а UNIQUE его пропустил бы.
        self.names((TAKEN, 'ARION 630 ф/х', TODAY))
        self.day(TAKEN, '2026-10-10', area=6.2)
        self.mapping('Arion 630 Ф/Х')
        self.listing('%d,fx_01_10' % TAKEN)
        rows = self.count_rows()
        code, output = self.run_tool('--apply')
        self.assertEqual(code, 3, output)
        self.assertEqual(self.plan_rows()[TAKEN]['status'], mark.NAME_TAKEN)
        self.assertEqual(self.count_rows(), rows)

    def test_two_objects_of_one_name_get_one_row_and_one_question(self):
        self.names((TWIN_A, 'DAMAS 80 977', TODAY),
                   (TWIN_B, 'DAMAS 80 977', TODAY))
        self.listing('%d,legkovaya_01_10' % TWIN_A, '%d,legkovaya_01_10' % TWIN_B)
        code, output = self.run_tool('--apply')
        self.assertEqual(code, 3, output)
        plan = self.plan_rows()
        self.assertEqual(plan[TWIN_A]['status'], mark.MARK)
        self.assertEqual(plan[TWIN_B]['status'], mark.NAME_TAKEN)
        self.assertEqual(self.excluded().get(TWIN_A), EXCLUDED_NOT_OURS)
        self.assertNotIn(TWIN_B, self.excluded())

    def test_an_object_without_a_name_is_left_to_a_person(self):
        # Строке нужно имя (NOT NULL, по нему ищет импорт); придумать его
        # значило бы завести строку, которую человек на экране не узнает.
        self.day(NAMELESS, '2026-10-10', area=1.3)
        self.listing('%d,vladelets_14_10' % NAMELESS)
        code, output = self.run_tool('--apply')
        self.assertEqual(code, 3, output)
        self.assertEqual(self.plan_rows()[NAMELESS]['status'], mark.NO_NAME)
        self.assertEqual(self.count_rows(), 0)

    def test_an_object_already_not_ours_needs_nothing(self):
        self.names((ALIEN, 'Chuzhoy kran', TODAY))
        self.mapping('Chuzhoy kran', wialon_id=ALIEN, skip=1)
        self.listing('%d,fx_01_10' % ALIEN)
        code, output = self.run_tool('--apply')
        self.assertEqual(code, 0, output)
        self.assertEqual(self.plan_rows()[ALIEN]['status'], mark.ALREADY)
        self.assertEqual(self.count_rows(), 1)

    def test_a_car_excluded_by_its_category_keeps_the_category(self):
        car = self.equipment('Nexia', 'passenger')
        self.names((CAR, 'Nexia 80 123 ABA', TODAY))
        self.mapping('Nexia 80 123 ABA', wialon_id=CAR, equipment_id=car)
        self.listing('%d,legkovaya_01_10' % CAR)
        code, output = self.run_tool('--apply')
        self.assertEqual(code, 0, output)
        self.assertEqual(self.plan_rows()[CAR]['status'], mark.NON_FIELD)
        self.assertEqual(self.excluded().get(CAR), EXCLUDED_NON_FIELD)
        self.assertEqual(self.rows_of(CAR)[0][1:3], (0, car))


class Refusals(Base):
    """Пункты 7 и 8."""

    def test_an_unknown_id_refuses_the_whole_run(self):
        self.farmer_world()
        self.listing('%d,fx_01_10' % FARMER, '999999,fx_01_10')
        before = self.digest()
        code, output = self.run_tool('--apply')
        self.assertEqual(code, 2, output)
        self.assertIn('999999', output)
        self.assertEqual(self.digest(), before)
        self.assertFalse(os.path.exists(self.out))

    def test_an_id_listed_twice_is_refused(self):
        self.farmer_world()
        self.listing('%d,fx_01_10' % FARMER, '%d,vladelets_14_10' % FARMER)
        code, output = self.run_tool('--apply')
        self.assertEqual(code, 2, output)
        self.assertEqual(self.count_rows(), 0)

    def test_a_list_without_the_column_is_refused(self):
        self.farmer_world()
        self.listing('%d' % FARMER, header='id')
        code, output = self.run_tool()
        self.assertEqual(code, 2, output)

    def test_a_missing_database_is_refused_and_not_created(self):
        self.listing('%d,fx_01_10' % FARMER)
        missing = os.path.join(self.folder, 'net', 'transport.db')
        self.db = missing
        code, output = self.run_tool('--apply')
        self.assertEqual(code, 2, output)
        self.assertFalse(os.path.exists(missing))

    def test_a_failed_postcondition_rolls_everything_back(self):
        # Правило расчёта «не видит» записанного -- значит, запись не сделала
        # того, ради чего её делали, и ни одной строки остаться не должно.
        self.farmer_world()
        self.listing('%d,fx_01_10' % FARMER, '%d,vladelets_14_10' % FARMER_GONE)
        before = self.count_rows()
        real = mark.excluded_units
        calls = []

        def blind_after_the_write(con):
            calls.append(1)
            return real(con) if len(calls) == 1 else {}

        with mock.patch.object(mark, 'excluded_units', blind_after_the_write):
            code, output = self.run_tool('--apply')
        self.assertEqual(code, 1, output)
        self.assertIn('rolled back', output)
        self.assertEqual(self.count_rows(), before)
        self.assertNotIn(FARMER, self.excluded())


class Console(Base):
    """Пункт 9."""

    def test_the_console_is_ascii_with_cyrillic_names(self):
        self.farmer_world()
        self.listing('%d,fx_01_10' % FARMER, '%d,vladelets_14_10' % FARMER_GONE)
        for extra in ((), ('--apply',)):
            code, output = self.run_tool(*extra)
            self.assertEqual(code, 0, output)
            output.encode('ascii')

    def test_the_name_survives_in_the_csv(self):
        self.farmer_world()
        self.listing('%d,fx_01_10' % FARMER)
        self.run_tool()
        self.assertEqual(self.plan_rows()[FARMER]['wialon_name'],
                         '6070 Фермер ф/х')
        with open(self.out, 'rb') as handle:
            self.assertTrue(handle.read().startswith(b'\xef\xbb\xbf'))


if __name__ == '__main__':
    unittest.main()
