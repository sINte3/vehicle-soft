# -*- coding: utf-8 -*-
"""GPS-17: связка объектов Wialon с машинами по утверждённому списку.

Что проверяется и почему именно это:

1. **Без `--apply` ничего не пишется**, и соединение сухого прогона физически
   не может писать.

2. **Связка -- та же строка, что дал бы экран**: имя от сборщика, id объекта,
   машина, без пометки. Снятие пометки «не наша» -- только по явному действию
   списка и только у строки «не наша» без машины.

3. **Правила экрана повторены**: неактивная машина и машина, у которой уже
   есть строка без пометки, -- человеку, а не в базу.

4. **Имя, общее с другим объектом, имя, занятое строкой, объект без имени** --
   человеку: связь живёт в имени, по нему импорт моточасов ищет машину.

5. **Неверный ввод -- отказ до записи**: неизвестный объект или машина, повтор,
   неизвестное действие, кривые числа, отсутствующая база (не создаётся),
   --out на месте базы, недоступный отчёт.

6. **Всё или ничего**: постусловие, замок записи, строка, изменённая между
   планом и записью, -- откат целиком; номера строк в отчёт -- только после
   COMMIT.

7. **Консоль ASCII** при кириллических именах; метки времени -- UTC.

Запуск:
  python -m unittest tests.test_gps_link_by_list -v
"""
import codecs
import csv
import hashlib
import io
import os
import sqlite3
import sys
import tempfile
import threading
import time
import unittest

from datetime import datetime, timezone
from unittest import mock

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if REPO_ROOT not in sys.path:
    sys.path.insert(0, REPO_ROOT)

import tools.gps_link_by_list as link                               # noqa: E402
from gps_collector import storage                                   # noqa: E402
from gps.exclusion import (EXCLUDED_NON_FIELD, EXCLUDED_NOT_OURS,   # noqa: E402
                           excluded_units)
from tests.test_gps_mark_not_ours import DDL                        # noqa: E402

PICKER, MARKED, TRACTOR, LOADER, TWIN_A, TWIN_B = 8806, 1102, 957, 572, 7001, 7002
TODAY = '2026-10-14 06:00:00'


class Base(unittest.TestCase):
    def setUp(self):
        self.folder = tempfile.mkdtemp()
        self.db = os.path.join(self.folder, 'transport.db')
        self.list = os.path.join(self.folder, 'svyazki.csv')
        self.out = os.path.join(self.folder, 'plan.csv')
        con = sqlite3.connect(self.db)
        try:
            for statement in DDL:
                con.execute(statement)
            con.commit()
        finally:
            con.close()

    # --- мир ------------------------------------------------------------------

    def sql(self, statement, params=()):
        con = sqlite3.connect(self.db)
        try:
            cursor = con.execute(statement, params)
            con.commit()
            return cursor.lastrowid
        finally:
            con.close()

    def names(self, *units):
        con = storage.open_units(self.folder)
        try:
            con.executemany('INSERT OR REPLACE INTO collector_units '
                            '(unit_id, name, seen_at) VALUES (?, ?, ?)', units)
            con.commit()
        finally:
            con.close()

    def machine(self, name, category, plate='', active=1):
        return self.sql('INSERT INTO equipment (name, plate, category, '
                        'organization_id, is_active) VALUES (?, ?, ?, 1, ?)',
                        (name, plate, category, active))

    def mapping(self, name, wialon_id=None, equipment_id=None, skip=0):
        return self.sql('INSERT INTO vialon_mappings (vialon_name, wialon_id, '
                        'equipment_id, skip) VALUES (?, ?, ?, ?)',
                        (name, wialon_id, equipment_id, skip))

    def day(self, unit_id, day, area):
        self.sql('INSERT INTO gps_daily_aggregates (work_date, wialon_id, '
                 'points_total, track_km, method_version) '
                 "VALUES (?, ?, 100, 10.0, 'test')", (day, unit_id))
        self.sql('INSERT INTO gps_work_polygons (work_date, wialon_id, '
                 'site_number, area_ha, minutes, polygon_geojson) '
                 "VALUES (?, ?, 1, ?, 30.0, '{}')", (day, unit_id, area))

    def listing(self, *rows, header='wialon_id,equipment_id,deystvie,osnovanie'):
        with open(self.list, 'w', encoding='utf-8', newline='') as handle:
            handle.write(header + '\n')
            for row in rows:
                handle.write(row + '\n')

    def world(self):
        """Комбайн без строки и МТЗ, помеченный «не наша», -- оба наши."""
        self.picker = self.machine('John Deere 9970', 'combine', '80 543 JA')
        self.tractor = self.machine('МТЗ-80Х', 'mtz', '80 951 JA')
        self.names((PICKER, ' JohnDeere  80 543 JA', TODAY),
                   (MARKED, 'МТЗ-80 Х 951 JA', TODAY))
        self.marked_row = self.mapping('МТЗ-80 Х 951 JA', wialon_id=MARKED, skip=1)
        self.day(PICKER, '2026-10-05', 6.5)

    # --- запуск ----------------------------------------------------------------

    def run_tool(self, *extra):
        out, err = io.StringIO(), io.StringIO()
        saved = sys.stdout, sys.stderr
        sys.stdout, sys.stderr = out, err
        try:
            code = link.main(['--db', self.db, '--dir', self.folder,
                              '--list', self.list, '--out', self.out]
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
            return con.execute('SELECT id, vialon_name, equipment_id, skip, '
                               'created_by, created_at, updated_at '
                               'FROM vialon_mappings WHERE wialon_id = ? '
                               'ORDER BY id', (unit_id,)).fetchall()
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
    def test_the_database_is_byte_identical_after_a_dry_run(self):
        self.world()
        self.listing('%d,%d,svyazat,plate' % (PICKER, self.picker),
                     '%d,%d,snyat_pometku,plate' % (MARKED, self.tractor))
        before = self.digest()
        code, output = self.run_tool()
        self.assertEqual(code, 0, output)
        self.assertEqual(self.digest(), before)
        plan = self.plan_rows()
        self.assertEqual(plan[PICKER]['status'], link.CREATE)
        self.assertEqual(plan[MARKED]['status'], link.RELEASE)
        self.assertEqual(plan[PICKER]['machine'], 'John Deere 9970')
        self.assertEqual(float(plan[PICKER]['ha_total']), 6.5)

    def test_the_dry_run_connection_cannot_write(self):
        self.world()
        self.listing('%d,%d,svyazat,plate' % (PICKER, self.picker))
        real, seen = link.plan, []

        def probe(con, wanted, names):
            try:
                con.execute('CREATE TABLE probe (x)')
                seen.append('written')
            except sqlite3.OperationalError as problem:
                seen.append(str(problem))
            return real(con, wanted, names)

        with mock.patch.object(link, 'plan', probe):
            code, output = self.run_tool()
        self.assertEqual(code, 0, output)
        self.assertIn('readonly', seen[0])


class Apply(Base):
    def test_a_link_is_the_row_the_screen_would_create(self):
        self.world()
        self.listing('%d,%d,svyazat,plate' % (PICKER, self.picker))
        code, output = self.run_tool('--apply')
        self.assertEqual(code, 0, output)
        rows = self.rows_of(PICKER)
        self.assertEqual(len(rows), 1)
        _, name, equipment_id, skip, created_by, created, updated = rows[0]
        self.assertEqual(name, 'JohnDeere 80 543 JA')
        self.assertEqual(equipment_id, self.picker)
        self.assertEqual(skip, 0)
        self.assertIsNone(created_by)
        self.assertEqual(created, updated)
        self.assertNotIn(PICKER, self.excluded())

    def test_unmarking_needs_the_explicit_action_and_gives_the_machine(self):
        self.world()
        self.assertEqual(self.excluded().get(MARKED), EXCLUDED_NOT_OURS)
        self.listing('%d,%d,snyat_pometku,plate' % (MARKED, self.tractor))
        code, output = self.run_tool('--apply')
        self.assertEqual(code, 0, output)
        rows = self.rows_of(MARKED)
        self.assertEqual(rows[0][0], self.marked_row)
        self.assertEqual(rows[0][2:4], (self.tractor, 0))
        # Имя строки не меняется: по нему её знают импорт и экран.
        self.assertEqual(rows[0][1], 'МТЗ-80 Х 951 JA')
        self.assertNotIn(MARKED, self.excluded())
        row = self.plan_rows()[MARKED]
        self.assertEqual(row['bylo'], 'skip=1 equipment_id=')
        self.assertEqual(row['izmenena'], 'da')

    def test_a_marked_object_listed_only_for_linking_is_left_to_a_person(self):
        self.world()
        self.listing('%d,%d,svyazat,plate' % (MARKED, self.tractor))
        code, output = self.run_tool('--apply')
        self.assertEqual(code, 3, output)
        self.assertEqual(self.plan_rows()[MARKED]['status'], link.HAS_ROW)
        self.assertEqual(self.excluded().get(MARKED), EXCLUDED_NOT_OURS)

    def test_unmarking_a_row_that_already_has_a_machine_is_left_to_a_person(self):
        self.world()
        other = self.machine('МТЗ-80Х', 'mtz', '80 952 JA')
        self.sql('UPDATE vialon_mappings SET equipment_id = ? WHERE id = ?',
                 (other, self.marked_row))
        self.listing('%d,%d,snyat_pometku,plate' % (MARKED, self.tractor))
        code, output = self.run_tool('--apply')
        self.assertEqual(code, 3, output)
        self.assertEqual(self.plan_rows()[MARKED]['status'], link.HAS_ROW)

    def test_the_report_names_each_changed_row_by_its_own_id(self):
        self.world()
        self.mapping('Chuzhoy kran', wialon_id=5001, skip=1)
        self.listing('%d,%d,svyazat,plate' % (PICKER, self.picker),
                     '%d,%d,snyat_pometku,plate' % (MARKED, self.tractor))
        code, output = self.run_tool('--apply')
        self.assertEqual(code, 0, output)
        self.assertIn('written: 2 change(s)', output)
        plan = self.plan_rows()
        created = self.rows_of(PICKER)[0][0]
        self.assertEqual(plan[PICKER]['mapping_id'], str(created))
        self.assertEqual(plan[MARKED]['mapping_id'], str(self.marked_row))
        self.assertIn('changed mapping rows: %s' % ' '.join(
            str(i) for i in (created, self.marked_row)), output)
        self.assertEqual({plan[u]['izmenena'] for u in (PICKER, MARKED)}, {'da'})

    def test_a_second_run_changes_nothing(self):
        self.world()
        self.listing('%d,%d,svyazat,plate' % (PICKER, self.picker),
                     '%d,%d,snyat_pometku,plate' % (MARKED, self.tractor))
        self.run_tool('--apply')
        rows = self.count_rows()
        code, output = self.run_tool('--apply')
        self.assertEqual(code, 0, output)
        self.assertEqual(self.count_rows(), rows)
        plan = self.plan_rows()
        self.assertEqual({plan[u]['status'] for u in (PICKER, MARKED)}, {link.DONE})
        self.assertEqual({plan[u]['izmenena'] for u in (PICKER, MARKED)}, {''})
        self.assertNotIn('changed mapping rows', output)

    def test_the_category_decides_how_the_object_is_counted(self):
        car = self.machine('Nexia', 'passenger', '80 123 ABA')
        loader = self.machine('Pogruzchik', 'special', '80 805 FA')
        self.names((TRACTOR, 'Nexia 80 123 ABA', TODAY),
                   (LOADER, 'New Holland 805 FA', TODAY))
        self.listing('%d,%d,svyazat,plate' % (TRACTOR, car),
                     '%d,%d,svyazat,plate' % (LOADER, loader))
        code, output = self.run_tool('--apply')
        self.assertEqual(code, 0, output)
        plan = self.plan_rows()
        self.assertEqual(plan[TRACTOR]['v_plan_fakte'], 'net (ne_polevaya)')
        self.assertEqual(plan[LOADER]['v_plan_fakte'], 'sled_bez_ga')
        self.assertEqual(self.excluded().get(TRACTOR), EXCLUDED_NON_FIELD)
        self.assertIn('counted by plan-fact: net (ne_polevaya) 1, sled_bez_ga 1',
                      output)

    @unittest.skipUnless(hasattr(time, 'tzset'), 'needs time.tzset')
    def test_the_stamps_are_utc(self):
        self.world()
        self.listing('%d,%d,svyazat,plate' % (PICKER, self.picker))
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
        created = self.rows_of(PICKER)[0][5]
        moment = datetime.strptime(created, '%Y-%m-%d %H:%M:%S').replace(
            tzinfo=timezone.utc)
        self.assertLess(abs((datetime.now(timezone.utc) - moment)
                            .total_seconds()), 120)


class LeftToAPerson(Base):
    def test_an_inactive_machine_is_not_linked(self):
        self.world()
        off = self.machine('John Deere 9970', 'combine', '80 547 JA', active=0)
        self.listing('%d,%d,svyazat,plate' % (PICKER, off))
        code, output = self.run_tool('--apply')
        self.assertEqual(code, 3, output)
        self.assertEqual(self.plan_rows()[PICKER]['status'], link.MACHINE_OFF)
        self.assertEqual(self.rows_of(PICKER), [])

    def test_a_machine_with_its_own_row_is_left_to_a_person(self):
        # Строка машины без id -- путь GPS-10 --set, а не вторая строка.
        self.world()
        row_id = self.mapping('JD 543 staroe imya', equipment_id=self.picker)
        self.listing('%d,%d,svyazat,plate' % (PICKER, self.picker))
        rows = self.count_rows()
        code, output = self.run_tool('--apply')
        self.assertEqual(code, 3, output)
        row = self.plan_rows()[PICKER]
        self.assertEqual(row['status'], link.MACHINE_HAS_ROW)
        self.assertEqual(row['mapping_id'], str(row_id))
        self.assertIn('--set', row['primechanie'])
        self.assertEqual(self.count_rows(), rows)

    def test_a_marked_row_of_the_machine_does_not_block(self):
        self.world()
        self.mapping('Staryy treker', wialon_id=4444, equipment_id=None, skip=1)
        self.sql('UPDATE vialon_mappings SET equipment_id = ? WHERE wialon_id = 4444',
                 (self.picker,))
        self.listing('%d,%d,svyazat,plate' % (PICKER, self.picker))
        code, output = self.run_tool('--apply')
        self.assertEqual(code, 0, output)
        self.assertEqual(self.rows_of(PICKER)[0][2], self.picker)

    def test_a_name_shared_with_another_object_is_left_to_a_person(self):
        self.world()
        self.names((TWIN_A, 'JohnDeere 80 543 JA', TODAY))
        self.listing('%d,%d,svyazat,plate' % (PICKER, self.picker))
        code, output = self.run_tool('--apply')
        self.assertEqual(code, 3, output)
        self.assertEqual(self.plan_rows()[PICKER]['status'], link.NAME_SHARED)
        self.assertEqual(self.rows_of(PICKER), [])

    def test_a_name_taken_by_a_row_is_left_to_a_person(self):
        self.world()
        self.mapping('johndeere 80 543 ja', skip=1)
        self.listing('%d,%d,svyazat,plate' % (PICKER, self.picker))
        code, output = self.run_tool('--apply')
        self.assertEqual(code, 3, output)
        row = self.plan_rows()[PICKER]
        self.assertEqual(row['status'], link.NAME_TAKEN)
        self.assertTrue(row['primechanie'])

    def test_an_object_without_a_name_is_left_to_a_person(self):
        self.world()
        self.day(TRACTOR, '2026-10-05', 2.0)
        mtz = self.machine('МТЗ-80Х', 'mtz', '80 516 EA')
        self.listing('%d,%d,svyazat,plate' % (TRACTOR, mtz))
        code, output = self.run_tool('--apply')
        self.assertEqual(code, 3, output)
        self.assertEqual(self.plan_rows()[TRACTOR]['status'], link.NO_NAME)


class Refusals(Base):
    def refused(self, *extra, text=''):
        before = self.digest()
        code, output = self.run_tool(*extra)
        self.assertEqual(code, 2, output)
        if text:
            self.assertIn(text, output)
        self.assertEqual(self.digest(), before)
        return output

    def test_bad_lists_are_refused_before_anything_is_written(self):
        self.world()
        cases = (
            ('999999,%d,svyazat,x' % self.picker, 'unknown to the collector'),
            ('%d,99999,svyazat,x' % PICKER, 'not in the equipment table'),
            ('%d,%d,svyaz,x' % (PICKER, self.picker), 'deystvie must be one of'),
            ('abc,%d,svyazat,x' % self.picker, 'not a positive number'),
            ('%d,0,svyazat,x' % PICKER, 'not a positive number'),
        )
        for row, text in cases:
            for extra in ((), ('--apply',)):
                with self.subTest(row=row, extra=extra):
                    self.listing(row)
                    self.refused(*extra, text=text)

    def test_a_repeated_object_or_machine_is_refused(self):
        self.world()
        for rows in (('%d,%d,svyazat,x' % (PICKER, self.picker),
                      '%d,%d,snyat_pometku,x' % (PICKER, self.tractor)),
                     ('%d,%d,svyazat,x' % (PICKER, self.picker),
                      '%d,%d,snyat_pometku,x' % (MARKED, self.picker))):
            with self.subTest(rows=rows):
                self.listing(*rows)
                self.refused('--apply', text='listed twice')

    def test_a_list_without_columns_or_rows_is_refused(self):
        self.world()
        self.listing('%d,%d' % (PICKER, self.picker), header='wialon_id,equipment_id')
        self.refused(text='deystvie')
        self.listing()
        self.refused(text='lists no objects')

    def test_a_missing_database_is_refused_and_not_created(self):
        self.listing('1,1,svyazat,x')
        self.db = os.path.join(self.folder, 'transprot.db')
        for extra in ((), ('--apply',)):
            with self.subTest(extra=extra):
                code, output = self.run_tool(*extra)
                self.assertEqual(code, 2, output)
                self.assertFalse(os.path.exists(self.db))

    def test_an_out_on_the_database_or_the_list_is_refused(self):
        self.world()
        self.listing('%d,%d,svyazat,x' % (PICKER, self.picker))
        alias = os.path.join(self.folder, 'report.csv')
        os.link(self.db, alias)
        for target in (self.db, self.list, alias):
            for extra in ((), ('--apply',)):
                with self.subTest(target=os.path.basename(target), extra=extra):
                    self.out = target
                    self.refused(*extra, text='refusing to overwrite')

    def test_a_report_that_cannot_be_written_stops_before_the_database(self):
        self.world()
        self.listing('%d,%d,svyazat,x' % (PICKER, self.picker))
        self.out = os.path.join(self.folder, 'plan_dir')
        os.mkdir(self.out)
        for extra in ((), ('--apply',)):
            with self.subTest(extra=extra):
                self.refused(*extra, text='nothing was written')
        self.out = os.path.join(self.folder, 'net', 'plan.csv')
        self.refused('--apply', text='folder for --out does not exist')


class AllOrNothing(Base):
    def test_a_failed_postcondition_rolls_everything_back(self):
        self.world()
        self.listing('%d,%d,svyazat,x' % (PICKER, self.picker),
                     '%d,%d,snyat_pometku,x' % (MARKED, self.tractor))
        rows = self.count_rows()
        real = link.excluded_units

        def still_not_ours(con):
            out = real(con)
            out[PICKER] = EXCLUDED_NOT_OURS
            return out

        with mock.patch.object(link, 'excluded_units', still_not_ours):
            code, output = self.run_tool('--apply')
        self.assertEqual(code, 1, output)
        self.assertIn('rolled back', output)
        self.assertEqual(self.count_rows(), rows)
        self.assertEqual(self.excluded().get(MARKED), EXCLUDED_NOT_OURS)
        plan = self.plan_rows()
        self.assertEqual(plan[PICKER]['izmenena'], '')
        self.assertEqual(plan[PICKER]['mapping_id'], '')
        self.assertIn('откат', plan[PICKER]['primechanie'])

    def test_a_row_changed_between_plan_and_write_rolls_everything_back(self):
        # Условие плана повторено в WHERE: строку, которую тронули, UPDATE не
        # меняет, и постусловие откатывает и созданную строку.
        self.world()
        self.listing('%d,%d,svyazat,x' % (PICKER, self.picker),
                     '%d,%d,snyat_pometku,x' % (MARKED, self.tractor))
        real = link.plan
        other = self.machine('Chuzhaya', 'mtz', '80 000 AA')

        def plan_then_touch(con, wanted, names):
            items = real(con, wanted, names)
            con.execute('UPDATE vialon_mappings SET equipment_id = ? WHERE id = ?',
                        (other, self.marked_row))
            return items

        rows = self.count_rows()
        with mock.patch.object(link, 'plan', plan_then_touch):
            code, output = self.run_tool('--apply')
        self.assertEqual(code, 1, output)
        self.assertIn('written 1 of 2', output)
        self.assertEqual(self.count_rows(), rows)
        self.assertEqual(self.rows_of(MARKED)[0][2], None)

    def test_the_plan_is_built_under_the_write_lock(self):
        con = sqlite3.connect(self.db)
        try:
            con.execute('PRAGMA journal_mode=WAL')
        finally:
            con.close()
        self.world()
        self.listing('%d,%d,svyazat,x' % (PICKER, self.picker))
        real, seen = link.plan, []

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

        with mock.patch.object(link, 'plan', plan_then_probe):
            code, output = self.run_tool('--apply')
        self.assertEqual(code, 0, output)
        self.assertEqual(seen, ['database is locked'])

    def test_a_busy_database_is_refused_with_code_4(self):
        self.world()
        self.listing('%d,%d,svyazat,x' % (PICKER, self.picker))
        holder = sqlite3.connect(self.db, isolation_level=None)
        try:
            holder.execute('BEGIN IMMEDIATE')
            with mock.patch.object(link, 'BUSY_TIMEOUT_S', 0.1):
                code, output = self.run_tool('--apply')
        finally:
            holder.execute('ROLLBACK')
            holder.close()
        self.assertEqual(code, 4, output)
        self.assertEqual(self.rows_of(PICKER), [])

    def test_a_short_lock_is_waited_out(self):
        con = sqlite3.connect(self.db)
        try:
            con.execute('PRAGMA journal_mode=WAL')
        finally:
            con.close()
        self.world()
        self.listing('%d,%d,svyazat,x' % (PICKER, self.picker))
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

    def test_a_report_lost_after_the_write_is_code_5_with_the_rows_named(self):
        self.world()
        self.listing('%d,%d,svyazat,x' % (PICKER, self.picker))

        def disk_full(path, items):
            raise OSError('disk full')

        with mock.patch.object(link, 'write_plan', disk_full):
            code, output = self.run_tool('--apply')
        self.assertEqual(code, 5, output)
        self.assertIn('ARE written', output)
        self.assertIn('changed mapping rows: %d' % self.rows_of(PICKER)[0][0],
                      output)


class Console(Base):
    def test_the_console_is_ascii_with_cyrillic_names(self):
        self.world()
        self.listing('%d,%d,svyazat,x' % (PICKER, self.picker),
                     '%d,%d,snyat_pometku,x' % (MARKED, self.tractor))
        for extra in ((), ('--apply',)):
            code, output = self.run_tool(*extra)
            self.assertEqual(code, 0, output)
            output.encode('ascii')


class ScreenRulesOnEveryPath(Base):
    """Пункты 2-4 на пути снятия пометки и прочие развилки плана."""
    # 12: a marked row that already carries the list machine is not "done"
    def test_marked_row_with_the_same_machine_is_not_done(self):
        self.world()
        self.sql('UPDATE vialon_mappings SET equipment_id = ? WHERE id = ?',
                 (self.tractor, self.marked_row))
        for action in ('snyat_pometku', 'svyazat'):
            with self.subTest(action=action):
                self.listing('%d,%d,%s,x' % (MARKED, self.tractor, action))
                code, output = self.run_tool('--apply')
                self.assertEqual(code, 3, output)
                self.assertEqual(self.plan_rows()[MARKED]['status'], link.HAS_ROW)
                self.assertEqual(self.excluded().get(MARKED), EXCLUDED_NOT_OURS)

    # 11 + L53: one linked row plus a contradictory marked row
    def test_an_object_with_a_linked_and_a_marked_row_is_left(self):
        self.world()
        self.mapping('JohnDeere 80 543 JA', wialon_id=PICKER,
                     equipment_id=self.picker)
        self.mapping('JD staroe imya', wialon_id=PICKER, skip=1)
        self.listing('%d,%d,svyazat,x' % (PICKER, self.picker))
        code, output = self.run_tool('--apply')
        self.assertEqual(code, 3, output)
        self.assertEqual(self.plan_rows()[PICKER]['status'], link.HAS_ROW)

    # 13: unmarking onto an inactive machine
    def test_unmarking_onto_an_inactive_machine_is_left(self):
        self.world()
        off = self.machine('MTZ-80X', 'mtz', '80 959 JA', active=0)
        self.listing('%d,%d,snyat_pometku,x' % (MARKED, off))
        code, output = self.run_tool('--apply')
        self.assertEqual(code, 3, output)
        self.assertEqual(self.plan_rows()[MARKED]['status'], link.MACHINE_OFF)
        self.assertEqual(self.excluded().get(MARKED), EXCLUDED_NOT_OURS)

    # 14: unmarking onto a machine that already has an unmarked row
    def test_unmarking_onto_a_machine_with_its_own_row_is_left(self):
        self.world()
        self.mapping('MTZ 951 staroe imya', equipment_id=self.tractor)
        self.listing('%d,%d,snyat_pometku,x' % (MARKED, self.tractor))
        code, output = self.run_tool('--apply')
        self.assertEqual(code, 3, output)
        self.assertEqual(self.plan_rows()[MARKED]['status'], link.MACHINE_HAS_ROW)
        self.assertEqual(self.rows_of(MARKED)[0][2:4], (None, 1))

    # 15: two marked rows -> left to a person, the rest of the list is written
    def test_two_marked_rows_are_left_and_the_rest_is_written(self):
        self.world()
        self.mapping('MTZ 951 dubl', wialon_id=MARKED, skip=1)
        self.listing('%d,%d,svyazat,x' % (PICKER, self.picker),
                     '%d,%d,snyat_pometku,x' % (MARKED, self.tractor))
        code, output = self.run_tool('--apply')
        self.assertEqual(code, 3, output)
        self.assertEqual(self.plan_rows()[MARKED]['status'], link.HAS_ROW)
        self.assertEqual(len(self.rows_of(PICKER)), 1)

    # 16: an UNMARKED row without a machine is not "release"
    def test_unmarking_an_unmarked_row_is_left_and_the_rest_is_written(self):
        self.world()
        self.sql('UPDATE vialon_mappings SET skip = 0 WHERE id = ?',
                 (self.marked_row,))
        self.listing('%d,%d,svyazat,x' % (PICKER, self.picker),
                     '%d,%d,snyat_pometku,x' % (MARKED, self.tractor))
        code, output = self.run_tool('--apply')
        self.assertEqual(code, 3, output)
        self.assertEqual(self.plan_rows()[MARKED]['status'], link.HAS_ROW)
        self.assertEqual(len(self.rows_of(PICKER)), 1)

    # 09: an object known only by its marked mapping row is not "unknown"
    def test_an_object_known_only_by_its_mapping_row_is_not_refused(self):
        # Объект известен только своей строкой «не наша»: это не опечатка
        # (код 2), но имени у сборщика нет -- сверить имя строки не с чем,
        # и снятие пометки остаётся человеку.
        self.world()
        self.mapping('MTZ bez imeni', wialon_id=3333, skip=1)
        self.listing('3333,%d,snyat_pometku,x' % self.tractor)
        code, output = self.run_tool('--apply')
        self.assertEqual(code, 3, output)
        self.assertEqual(self.plan_rows()[3333]['status'], link.NAME_STALE)
        self.assertEqual(self.rows_of(3333)[0][2:4], (None, 1))

    # 34: dry run exit code 3 when something is left to a person
    def test_the_dry_run_exit_code_says_something_is_left(self):
        self.world()
        self.listing('%d,%d,svyazat,x' % (MARKED, self.tractor))
        code, output = self.run_tool()
        self.assertEqual(code, 3, output)

    # 40: --out on the -wal / -shm file
    def test_an_out_on_the_wal_or_shm_file_is_refused(self):
        self.world()
        self.listing('%d,%d,svyazat,x' % (PICKER, self.picker))
        db = self.db
        for target in (db + '-wal', db + '-shm'):
            for extra in ((), ('--apply',)):
                with self.subTest(target=os.path.basename(target), extra=extra):
                    self.out = target
                    code, output = self.run_tool(*extra)
                    self.assertEqual(code, 2, output)
                    self.assertIn('refusing to overwrite', output)

    # 45: a database without vialon_mappings is refused with code 2
    def test_a_database_without_the_mapping_table_is_refused(self):
        self.world()
        self.listing('%d,%d,svyazat,x' % (PICKER, self.picker))
        self.sql('DROP TABLE vialon_mappings')
        for extra in ((), ('--apply',)):
            with self.subTest(extra=extra):
                code, output = self.run_tool(*extra)
                self.assertEqual(code, 2, output)
                self.assertIn('vialon_mappings is missing', output)

    # 31 + L32: rollback columns of a created row
    def test_a_created_row_reports_what_was_before(self):
        self.world()
        self.listing('%d,%d,svyazat,x' % (PICKER, self.picker))
        code, output = self.run_tool('--apply')
        self.assertEqual(code, 0, output)
        row = self.plan_rows()[PICKER]
        self.assertEqual(row['bylo'], 'strok ne bylo')
        self.assertEqual(row['mapping_name'], 'JohnDeere 80 543 JA')

    # 47: the plan-fact summary counts only the writes
    def test_the_summary_counts_only_the_writes(self):
        self.world()
        self.listing('%d,%d,svyazat,x' % (PICKER, self.picker),
                     '%d,%d,svyazat,x' % (MARKED, self.tractor))
        code, output = self.run_tool()
        self.assertEqual(code, 3, output)
        self.assertIn('counted by plan-fact: da 1\n', output)

    # 48: a list saved with a BOM (Excel) is read
    def test_a_list_with_a_bom_is_read(self):
        self.world()
        with open(self.list, 'wb') as handle:
            handle.write(codecs.BOM_UTF8 + (
                'wialon_id,equipment_id,deystvie,osnovanie\n%d,%d,svyazat,x\n'
                % (PICKER, self.picker)).encode('utf-8'))
        code, output = self.run_tool()
        self.assertEqual(code, 0, output)

    # 19: a twin that differs only in letter case is still a shared name
    def test_a_name_shared_in_another_case_is_left(self):
        self.world()
        self.names((TWIN_A, 'JOHNDEERE 80 543 JA', TODAY))
        self.listing('%d,%d,svyazat,x' % (PICKER, self.picker))
        code, output = self.run_tool('--apply')
        self.assertEqual(code, 3, output)
        self.assertEqual(self.plan_rows()[PICKER]['status'], link.NAME_SHARED)


class ScreenParity(Base):
    # 05: the screen refuses a machine whose is_active is NULL
    def test_a_machine_with_null_is_active_is_not_linked(self):
        self.world()
        nul = self.machine('John Deere 9970', 'combine', '80 548 JA', active=None)
        self.listing('%d,%d,svyazat,x' % (PICKER, nul))
        code, output = self.run_tool('--apply')
        self.assertEqual(code, 3, output)
        self.assertEqual(self.plan_rows()[PICKER]['status'], link.MACHINE_OFF)



class Order(Base):
    # 49: within one status the CSV puts the larger hectares first
    def test_within_a_status_larger_hectares_come_first(self):
        self.world()
        self.names((TWIN_A, 'MTZ 80 777 JA', TODAY))
        mtz = self.machine('MTZ-80X', 'mtz', '80 777 JA')
        self.day(TWIN_A, '2026-10-06', 1.0)
        self.listing('%d,%d,svyazat,x' % (TWIN_A, mtz),
                     '%d,%d,svyazat,x' % (PICKER, self.picker))
        code, output = self.run_tool()
        self.assertEqual(code, 0, output)
        with open(self.out, encoding='utf-8-sig', newline='') as handle:
            order = [int(r['wialon_id']) for r in csv.DictReader(handle, delimiter=';')]
        self.assertEqual(order, [PICKER, TWIN_A])


class UnmarkNames(Base):
    """Снятая пометка оставляет строке её имя -- оно обязано быть именем объекта."""

    def test_a_stale_row_name_is_left_to_a_person(self):
        self.world()
        self.sql("UPDATE vialon_mappings SET vialon_name = 'MTZ 951 JA (prezhniy voditel)' "
                 'WHERE id = ?', (self.marked_row,))
        self.listing('%d,%d,snyat_pometku,x' % (MARKED, self.tractor))
        code, output = self.run_tool('--apply')
        self.assertEqual(code, 3, output)
        self.assertEqual(self.plan_rows()[MARKED]['status'], link.NAME_STALE)
        self.assertEqual(self.excluded().get(MARKED), EXCLUDED_NOT_OURS)

    def test_a_numbered_row_name_of_gps16_is_left_to_a_person(self):
        self.world()
        self.sql("UPDATE vialon_mappings SET vialon_name = ? WHERE id = ?",
                 ('МТЗ-80 Х 951 JA (wialon %d)' % MARKED, self.marked_row))
        self.listing('%d,%d,snyat_pometku,x' % (MARKED, self.tractor))
        code, output = self.run_tool('--apply')
        self.assertEqual(code, 3, output)
        self.assertEqual(self.plan_rows()[MARKED]['status'], link.NAME_STALE)

    def test_a_row_name_shared_with_another_object_is_left_to_a_person(self):
        self.world()
        self.names((TWIN_A, 'мтз-80 х 951 JA', TODAY))
        self.listing('%d,%d,snyat_pometku,x' % (MARKED, self.tractor))
        code, output = self.run_tool('--apply')
        self.assertEqual(code, 3, output)
        self.assertEqual(self.plan_rows()[MARKED]['status'], link.NAME_SHARED)
        self.assertEqual(self.excluded().get(MARKED), EXCLUDED_NOT_OURS)

    def test_a_row_name_differing_only_in_spaces_and_case_is_released(self):
        self.world()
        self.sql("UPDATE vialon_mappings SET vialon_name = 'мтз-80  х 951 ja' "
                 'WHERE id = ?', (self.marked_row,))
        self.listing('%d,%d,snyat_pometku,x' % (MARKED, self.tractor))
        code, output = self.run_tool('--apply')
        self.assertEqual(code, 0, output)
        self.assertEqual(self.plan_rows()[MARKED]['status'], link.RELEASE)


class CollectorFiles(Base):
    def test_an_out_on_the_collector_state_file_is_refused(self):
        # Файл отметок и имён сборщика -- тоже база; отчёт поверх него стёр бы
        # отметки сбора, даже в сухом прогоне.
        self.world()
        self.listing('%d,%d,svyazat,x' % (PICKER, self.picker))
        state = storage.state_path(self.folder)
        with open(state, 'rb') as handle:
            before = handle.read()
        for extra in ((), ('--apply',)):
            with self.subTest(extra=extra):
                self.out = state
                code, output = self.run_tool(*extra)
                self.assertEqual(code, 2, output)
                self.assertIn('refusing to overwrite', output)
                with open(state, 'rb') as handle:
                    self.assertEqual(handle.read(), before)


if __name__ == '__main__':
    unittest.main()
