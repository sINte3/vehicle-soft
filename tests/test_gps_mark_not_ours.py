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

4. **Чужие строки не трогаются.** Строка с этим id и имя, занятое строкой
   сопоставления (в том числе в другом регистре и с пробелами), -- в CSV
   человеку, а не в базу. Код выхода 3 называет остаток. Имя, которое у
   сборщика носит ещё один объект, пишется с номером: строка «не наша» под
   общим именем объявила бы чужим и второй объект.

5. **Уже исключённое решения не ждёт**: «не наша» и легковая с категорией --
   без новой строки и без кода 3.

6. **Ничего не удаляется**: посчитанные сутки объекта остаются в базе.

7. **Неверный ввод -- отказ до записи**: неизвестный id (опечатка), повтор id,
   отсутствующая база (и файл не создаётся).

8. **Всё или ничего**: если постусловие не сошлось, откатывается вся
   транзакция; план собирается под замком записи; занятая база и файл
   отчёта, открытый в Excel, -- отказ до записи; отчёт, не записанный после
   записи, называется своим кодом.

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
import time
import unittest

from datetime import datetime, timezone

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

    def test_the_dry_run_connection_cannot_write(self):
        # Побайтное равенство базы не отличит mode=ro от обычного соединения:
        # код в сухом прогоне и так только читает. Здесь -- отличает.
        self.farmer_world()
        self.listing('%d,fx_01_10' % FARMER)
        real, seen = mark.plan, []

        def plan_after_probe(con, wanted, names):
            try:
                con.execute('CREATE TABLE probe (x)')
                seen.append('written')
            except sqlite3.OperationalError as problem:
                seen.append(str(problem))
            return real(con, wanted, names)

        with mock.patch.object(mark, 'plan', plan_after_probe):
            code, output = self.run_tool()
        self.assertEqual(code, 0, output)
        self.assertEqual(len(seen), 1)
        self.assertIn('readonly', seen[0])

    def test_hectares_of_a_day_with_a_reason_are_not_summed(self):
        self.farmer_world()
        self.day(FARMER, '2026-10-08', area=50.0, reason='redkaya_zapis')
        self.listing('%d,fx_01_10' % FARMER)
        self.run_tool()
        self.assertEqual(float(self.plan_rows()[FARMER]['ha_total']), 8.0)

    def test_the_hectares_to_hide_are_those_of_the_marked_objects_only(self):
        self.farmer_world()
        self.mapping('МТЗ-80 292 HA', wialon_id=COUNTED)
        self.day(COUNTED, '2026-10-05', area=4.0)
        self.listing('%d,fx_01_10' % FARMER, '%d,fx_01_10' % COUNTED)
        code, output = self.run_tool()
        self.assertEqual(code, 3, output)
        self.assertIn('hectares to hide    : 8.00 on 2 computed day(s)', output)

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

    def test_the_report_names_each_inserted_row_by_its_own_id(self):
        # Чужая строка заранее: номер новой строки не совпадает ни с числом
        # записанных, ни с единицей.
        self.farmer_world()
        self.names((ALIEN, 'Chuzhoy kran', TODAY))
        self.mapping('Chuzhoy kran', wialon_id=ALIEN, skip=1)
        self.listing('%d,fx_01_10' % FARMER, '%d,vladelets_14_10' % FARMER_GONE,
                     '%d,fx_01_10' % ALIEN)
        code, output = self.run_tool('--apply')
        self.assertEqual(code, 0, output)
        self.assertIn('written: 2 row(s)', output)
        plan = self.plan_rows()
        con = sqlite3.connect(self.db)
        try:
            created = []
            for unit in (FARMER, FARMER_GONE):
                row_id, name = con.execute(
                    'SELECT id, vialon_name FROM vialon_mappings WHERE '
                    'wialon_id = ?', (unit,)).fetchone()
                created.append(str(row_id))
                self.assertEqual(plan[unit]['mapping_id'], str(row_id))
                self.assertEqual(plan[unit]['mapping_name'], name)
                self.assertEqual(plan[unit]['skip'], '1')
                self.assertEqual(plan[unit]['sozdana'], 'da')
        finally:
            con.close()
        # Чужая строка -- не создана этим прогоном, и это видно.
        self.assertEqual(plan[ALIEN]['sozdana'], '')
        self.assertIn('created mapping rows: %s' % ' '.join(sorted(created, key=int)),
                      output)

    def test_a_second_run_does_not_claim_the_rows_of_the_first(self):
        self.farmer_world()
        self.listing('%d,fx_01_10' % FARMER)
        self.run_tool('--apply')
        code, output = self.run_tool('--apply')
        self.assertEqual(code, 0, output)
        row = self.plan_rows()[FARMER]
        self.assertEqual(row['status'], mark.ALREADY)
        self.assertEqual(row['sozdana'], '')
        self.assertNotIn('created mapping rows', output)

    @unittest.skipUnless(hasattr(time, 'tzset'), 'needs time.tzset')
    def test_the_stamps_are_utc_and_both_set(self):
        # Модель пишет datetime.utcnow; сервер -- в Ташкенте (+5).
        self.farmer_world()
        self.listing('%d,fx_01_10' % FARMER)
        saved = os.environ.get('TZ')
        os.environ['TZ'] = 'Asia/Tashkent'
        time.tzset()
        try:
            self.run_tool('--apply')
        finally:
            if saved is None:
                os.environ.pop('TZ', None)
            else:
                os.environ['TZ'] = saved
            time.tzset()
        con = sqlite3.connect(self.db)
        try:
            created, updated = con.execute(
                'SELECT created_at, updated_at FROM vialon_mappings '
                'WHERE wialon_id = ?', (FARMER,)).fetchone()
        finally:
            con.close()
        self.assertEqual(created, updated)
        moment = datetime.strptime(created, '%Y-%m-%d %H:%M:%S').replace(
            tzinfo=timezone.utc)
        self.assertLess(abs((datetime.now(timezone.utc) - moment)
                            .total_seconds()), 120)

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

    def test_two_objects_of_one_name_get_rows_named_by_their_ids(self):
        self.names((TWIN_A, 'DAMAS 80 977', TODAY),
                   (TWIN_B, 'DAMAS 80 977', TODAY))
        self.listing('%d,legkovaya_01_10' % TWIN_A, '%d,legkovaya_01_10' % TWIN_B)
        code, output = self.run_tool('--apply')
        self.assertEqual(code, 0, output)
        self.assertEqual(self.rows_of(TWIN_A)[0][0], 'DAMAS 80 977 (wialon 7001)')
        self.assertEqual(self.rows_of(TWIN_B)[0][0], 'DAMAS 80 977 (wialon 7002)')
        self.assertEqual(self.excluded().get(TWIN_A), EXCLUDED_NOT_OURS)
        self.assertEqual(self.excluded().get(TWIN_B), EXCLUDED_NOT_OURS)

    def test_names_that_differ_only_in_case_are_one_name(self):
        self.names((TWIN_A, 'DAMAS 80 977', TODAY),
                   (TWIN_B, 'Damas  80 977', TODAY))
        self.listing('%d,legkovaya_01_10' % TWIN_A, '%d,legkovaya_01_10' % TWIN_B)
        code, output = self.run_tool('--apply')
        self.assertEqual(code, 0, output)
        self.assertEqual(self.rows_of(TWIN_A)[0][0], 'DAMAS 80 977 (wialon 7001)')
        self.assertEqual(self.rows_of(TWIN_B)[0][0], 'Damas 80 977 (wialon 7002)')
        self.assertEqual(self.count_rows(), 2)

    def test_a_row_holding_the_plain_shared_name_does_not_block_the_numbered_row(self):
        self.names((FARMER, 'MTZ-80', TODAY), (TWIN_A, 'MTZ-80', TODAY))
        self.day(FARMER, '2026-10-05', area=2.0)
        plain = self.mapping('MTZ-80')
        self.listing('%d,fx_01_10' % FARMER)
        code, output = self.run_tool('--apply')
        self.assertEqual(code, 0, output)
        self.assertEqual(self.rows_of(FARMER)[0][0], 'MTZ-80 (wialon %d)' % FARMER)
        self.assertEqual(self.count_rows(), 2)
        con = sqlite3.connect(self.db)
        try:
            self.assertEqual(con.execute(
                'SELECT vialon_name, wialon_id, skip FROM vialon_mappings '
                'WHERE id = ?', (plain,)).fetchone(), ('MTZ-80', None, 0))
        finally:
            con.close()

    def test_a_numbered_name_already_taken_is_left_to_a_person_with_both_notes(self):
        # Строка с именем-номером могла остаться после `--unset`: вторую
        # строку о той же машине заводить нельзя.
        self.names((FARMER, 'MTZ-80', TODAY), (TWIN_A, 'MTZ-80', TODAY))
        self.day(FARMER, '2026-10-05', area=2.0)
        row_id = self.mapping('mtz-80 (wialon %d)' % FARMER, skip=1)
        self.listing('%d,fx_01_10' % FARMER)
        rows = self.count_rows()
        code, output = self.run_tool('--apply')
        self.assertEqual(code, 3, output)
        row = self.plan_rows()[FARMER]
        self.assertEqual(row['status'], mark.NAME_TAKEN)
        self.assertEqual(row['mapping_id'], str(row_id))
        self.assertIn(str(TWIN_A), row['primechanie'])
        self.assertIn(mark.NEXT_STEP[mark.NAME_TAKEN], row['primechanie'])
        self.assertEqual(self.count_rows(), rows)

    def test_left_to_a_person_rows_have_guidance_and_no_row_name(self):
        self.farmer_world()
        self.mapping('МТЗ-80 292 HA', wialon_id=COUNTED)
        self.mapping('new holland 80 413 fa ф/х')
        self.listing('%d,fx_01_10' % COUNTED, '%d,vladelets_14_10' % FARMER_GONE,
                     '%d,fx_01_10' % FARMER)
        code, output = self.run_tool()
        self.assertEqual(code, 3, output)
        plan = self.plan_rows()
        self.assertEqual(plan[COUNTED]['status'], mark.HAS_ROW)
        self.assertEqual(plan[FARMER_GONE]['status'], mark.NAME_TAKEN)
        for unit in (COUNTED, FARMER_GONE):
            self.assertEqual(plan[unit]['row_name'], '')
            self.assertTrue(plan[unit]['primechanie'])
        self.assertEqual(plan[FARMER]['row_name'], '6070 Фермер ф/х')
        self.assertEqual(plan[FARMER]['primechanie'], '')

    def test_a_name_shared_with_an_unlisted_object_is_not_claimed(self):
        # Второй объект с тем же именем -- не в списке, возможно наш. Строка
        # под общим именем увела бы его в «не наша» в импорте моточасов, а его
        # добавление на экране легло бы на строку с чужим id.
        self.names((FARMER, 'MTZ-80', TODAY), (TWIN_A, 'MTZ-80', TODAY))
        self.day(FARMER, '2026-10-05', area=2.0)
        self.listing('%d,fx_01_10' % FARMER)
        code, output = self.run_tool('--apply')
        self.assertEqual(code, 0, output)
        row = self.plan_rows()[FARMER]
        self.assertEqual(row['row_name'], 'MTZ-80 (wialon %d)' % FARMER)
        self.assertIn(str(TWIN_A), row['primechanie'])
        con = sqlite3.connect(self.db)
        try:
            self.assertEqual(con.execute(
                "SELECT COUNT(*) FROM vialon_mappings WHERE vialon_name = 'MTZ-80'"
            ).fetchone()[0], 0)
        finally:
            con.close()
        self.assertNotIn(TWIN_A, self.excluded())

    def test_a_name_taken_by_a_row_of_another_id_is_left_to_a_person(self):
        self.names((FARMER_GONE, 'New Holland 80 413 FA ф/х', EARLIER))
        self.day(FARMER_GONE, '2026-10-12', area=3.1)
        self.mapping(' New Holland 80 413 FA Ф/Х', wialon_id=9999)
        self.listing('%d,vladelets_14_10' % FARMER_GONE)
        rows = self.count_rows()
        code, output = self.run_tool('--apply')
        self.assertEqual(code, 3, output)
        row = self.plan_rows()[FARMER_GONE]
        self.assertEqual(row['status'], mark.NAME_TAKEN)
        self.assertTrue(row['primechanie'])
        self.assertEqual(self.count_rows(), rows)

    def test_an_id_known_only_by_its_mapping_row_is_not_refused(self):
        self.mapping('Chuzhoy kran', wialon_id=ALIEN, skip=1)
        self.listing('%d,fx_01_10' % ALIEN)
        code, output = self.run_tool()
        self.assertEqual(code, 0, output)
        self.assertEqual(self.plan_rows()[ALIEN]['status'], mark.ALREADY)

    def test_an_object_without_a_name_is_left_to_a_person(self):
        # Строке нужно имя (NOT NULL, по нему ищет импорт); придумать его
        # значило бы завести строку, которую человек на экране не узнает.
        self.day(NAMELESS, '2026-10-10', area=1.3)
        self.listing('%d,vladelets_14_10' % NAMELESS)
        code, output = self.run_tool('--apply')
        self.assertEqual(code, 3, output)
        row = self.plan_rows()[NAMELESS]
        self.assertEqual(row['status'], mark.NO_NAME)
        # Сборщик объекта не видел: «удалён из Wialon» было бы неправдой.
        self.assertEqual(row['v_wialon'], '')
        self.assertTrue(row['primechanie'])
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
        for extra in ((), ('--apply',)):
            with self.subTest(extra=extra):
                code, output = self.run_tool(*extra)
                self.assertEqual(code, 2, output)
                self.assertIn('999999', output)
                self.assertEqual(self.digest(), before)
                self.assertFalse(os.path.exists(self.out))

    def test_ids_that_are_not_positive_numbers_are_refused(self):
        self.farmer_world()
        for bad in ('abc,x', ',x', '0,x', '-5,x'):
            with self.subTest(bad=bad):
                self.listing('%d,fx_01_10' % FARMER, bad)
                code, output = self.run_tool('--apply')
                self.assertEqual(code, 2, output)
                self.assertIn('not a positive number', output)
                self.assertEqual(self.count_rows(), 0)

    def test_a_list_without_objects_is_refused(self):
        self.farmer_world()
        self.listing()
        code, output = self.run_tool()
        self.assertEqual(code, 2, output)
        self.assertIn('lists no objects', output)

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
        self.assertIn('no column wialon_id', output)

    def test_a_database_without_the_columns_is_refused(self):
        con = sqlite3.connect(self.db)
        try:
            con.execute('DROP TABLE vialon_mappings')
            con.execute('CREATE TABLE vialon_mappings (id INTEGER PRIMARY KEY, '
                        'vialon_name VARCHAR(300) NOT NULL UNIQUE, '
                        'equipment_id INTEGER)')
            con.commit()
        finally:
            con.close()
        self.farmer_world()
        self.listing('%d,fx_01_10' % FARMER)
        for extra in ((), ('--apply',)):
            with self.subTest(extra=extra):
                code, output = self.run_tool(*extra)
                self.assertEqual(code, 2, output)
                self.assertIn('migrate_core_foundation_001', output)

    def test_a_missing_database_is_refused_and_not_created(self):
        # Опечатка в имени файла в существующей папке -- там SQLite создал бы
        # пустую базу; папка, которой нет, этого не проверила бы.
        self.listing('%d,fx_01_10' % FARMER)
        self.db = os.path.join(self.folder, 'transprot.db')
        for extra in ((), ('--apply',)):
            with self.subTest(extra=extra):
                code, output = self.run_tool(*extra)
                self.assertEqual(code, 2, output)
                self.assertIn('database not found', output)
                self.assertFalse(os.path.exists(self.db))

    def test_a_missing_out_folder_is_refused_before_the_write(self):
        self.farmer_world()
        self.listing('%d,fx_01_10' % FARMER)
        self.out = os.path.join(self.folder, 'net', 'plan.csv')
        for extra in ((), ('--apply',)):
            with self.subTest(extra=extra):
                code, output = self.run_tool(*extra)
                self.assertEqual(code, 2, output)
                self.assertIn('folder for --out does not exist', output)
                self.assertEqual(self.count_rows(), 0)

    def test_a_report_file_that_cannot_be_written_stops_before_the_write(self):
        # Каталог вместо файла -- то же, что CSV, открытый в Excel на Windows:
        # открыть на запись нельзя, и база не должна быть тронута.
        self.farmer_world()
        self.listing('%d,fx_01_10' % FARMER)
        self.out = os.path.join(self.folder, 'plan_dir')
        os.mkdir(self.out)
        for extra in ((), ('--apply',)):
            with self.subTest(extra=extra):
                code, output = self.run_tool(*extra)
                self.assertEqual(code, 2, output)
                self.assertIn('nothing was written', output)
                self.assertEqual(self.count_rows(), 0)

    def test_an_out_that_is_the_database_or_the_list_is_refused(self):
        # Опечатка: --out на месте базы заменила бы боевую базу CSV-файлом.
        self.farmer_world()
        self.listing('%d,fx_01_10' % FARMER)
        before = self.digest()
        for target in (self.db, self.ids, self.db + '-wal'):
            for extra in ((), ('--apply',)):
                with self.subTest(target=os.path.basename(target), extra=extra):
                    self.out = target
                    code, output = self.run_tool(*extra)
                    self.assertEqual(code, 2, output)
                    self.assertIn('refusing to overwrite', output)
                    self.assertEqual(self.digest(), before)
        with open(self.ids, encoding='utf-8') as handle:
            self.assertIn('wialon_id', handle.read())

    def test_a_short_lock_held_by_the_service_is_waited_out(self):
        # Служба пишет в базу секунды; ждать её -- обещание кода 4 («дольше
        # 30 с»), а не отказ при первой же занятости.
        import threading
        con = sqlite3.connect(self.db)
        try:
            con.execute('PRAGMA journal_mode=WAL')
        finally:
            con.close()
        self.farmer_world()
        self.listing('%d,fx_01_10' % FARMER)
        holder = sqlite3.connect(self.db, isolation_level=None,
                                 check_same_thread=False)
        holder.execute('BEGIN IMMEDIATE')
        timer = threading.Timer(0.5, lambda: holder.execute('ROLLBACK'))
        timer.start()
        try:
            code, output = self.run_tool('--apply')
        finally:
            timer.join()
            holder.close()
        self.assertEqual(code, 0, output)
        self.assertEqual(self.excluded().get(FARMER), EXCLUDED_NOT_OURS)

    def test_a_previous_report_survives_a_refused_check(self):
        # Проверка записи не обрезает прежний отчёт и не оставляет пустых
        # файлов там, где отчёта не было.
        self.farmer_world()
        self.listing('%d,fx_01_10' % FARMER)
        self.run_tool()
        with open(self.out, 'rb') as handle:
            before = handle.read()
        self.listing('%d,fx_01_10' % FARMER, '999999,fx_01_10')
        code, _ = self.run_tool('--apply')
        self.assertEqual(code, 2)
        with open(self.out, 'rb') as handle:
            self.assertEqual(handle.read(), before)

    def test_a_busy_database_is_refused_with_its_own_code(self):
        self.farmer_world()
        self.listing('%d,fx_01_10' % FARMER)
        holder = sqlite3.connect(self.db, isolation_level=None)
        try:
            holder.execute('BEGIN IMMEDIATE')
            with mock.patch.object(mark, 'BUSY_TIMEOUT_S', 0.1):
                code, output = self.run_tool('--apply')
        finally:
            holder.execute('ROLLBACK')
            holder.close()
        self.assertEqual(code, 4, output)
        self.assertIn('database is busy', output)
        self.assertEqual(self.count_rows(), 0)

    def test_a_report_lost_after_the_write_says_the_rows_are_written(self):
        self.farmer_world()
        self.listing('%d,fx_01_10' % FARMER)

        def disk_full(path, items):
            raise OSError('disk full')

        with mock.patch.object(mark, 'write_plan', disk_full):
            code, output = self.run_tool('--apply')
        self.assertEqual(code, 5, output)
        self.assertIn('written: 1 row(s)', output)
        self.assertIn('ARE written', output)
        self.assertEqual(self.excluded().get(FARMER), EXCLUDED_NOT_OURS)

    def test_the_plan_is_built_under_the_write_lock(self):
        # Между сухим прогоном и записью человек на экране может добавить
        # строку. План пересобирается внутри BEGIN IMMEDIATE: пока он
        # строится, чужая запись невозможна. WAL -- как на сервере.
        con = sqlite3.connect(self.db)
        try:
            con.execute('PRAGMA journal_mode=WAL')
        finally:
            con.close()
        self.farmer_world()
        self.listing('%d,fx_01_10' % FARMER)
        real, seen = mark.plan, []

        def plan_then_probe(con, wanted, names):
            items = real(con, wanted, names)
            other = sqlite3.connect(self.db, timeout=0)
            try:
                other.execute("INSERT INTO vialon_mappings (vialon_name) "
                              "VALUES ('probe')")
                other.commit()
                seen.append('written')
            except sqlite3.OperationalError as problem:
                seen.append(str(problem))
            finally:
                other.close()
            return items

        with mock.patch.object(mark, 'plan', plan_then_probe):
            code, output = self.run_tool('--apply')
        self.assertEqual(code, 0, output)
        self.assertEqual(seen, ['database is locked'])

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
        # Отчёт отката не называет номеров: строк нет, а SQLite отдаст эти
        # номера следующим строкам.
        plan = self.plan_rows()
        for unit in (FARMER, FARMER_GONE):
            self.assertEqual(plan[unit]['mapping_id'], '')
            self.assertEqual(plan[unit]['sozdana'], '')
            self.assertIn('откат', plan[unit]['primechanie'])


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
