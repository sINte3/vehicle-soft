# -*- coding: utf-8 -*-
"""tools/gps_track_only_days.py -- сутки спецтехники по датам и причинам.

[REASON]: 30.09.2026 инвентарь после правила A1 показал 453 суток
спецтехники при 440, переведённых догоном: догон работает в окне до вчера,
инвентарь считает от даты без конца. Инструмент раскладывает сутки по датам,
и его итог «counted» обязан совпадать со столбцом `days_computed` инвентаря
-- иначе разбивка объясняла бы не то число. Это и проверяется сверкой с
`computed_activity` самого инвентаря, а не пересказом его правила.

`--date` раскладывает одни сутки по объектам (30.09: 301,18 га спецтехники за
28.09 до релиза A1). Сумма по объектам обязана совпасть со строкой той же
даты в таблице по датам, имя машины -- с именем на экране.
"""
import ast
import contextlib
import hashlib
import io
import os
import shutil
import sqlite3
import sys
import tempfile
import unittest

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if REPO_ROOT not in sys.path:
    sys.path.insert(0, REPO_ROOT)

import tools.gps_track_only_days as tod                             # noqa: E402
import tools.gps_units_inventory as inv                             # noqa: E402
from tests.test_gps_units_inventory import DDL                      # noqa: E402

LOADER, TRACTOR, MIXED, ALIEN = 419, 387, 553, 5001
# id меньше, чем у погрузчика: порядок «по номеру» и «по гектарам» различим
CRANE = 311


class World(unittest.TestCase):
    def setUp(self):
        self.folder = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.folder, True)
        self.db = os.path.join(self.folder, 'transport.db')
        con = sqlite3.connect(self.db)
        try:
            for statement in DDL:
                con.execute(statement)
            con.commit()
        finally:
            con.close()
        loader = self.equipment('Погрузчик 326 HA', 'special')
        tractor = self.equipment('МТЗ-80.1', 'mtz')
        self.mapping('Погрузчик 326 HA', LOADER, loader)
        self.mapping('МТЗ 261 EA', TRACTOR, tractor)
        # противоречие: спецтехника и трактор на одном объекте -- гектары
        # остаются, в список правила объект не входит
        self.mapping('Погрузчик 324 HA', MIXED, loader)
        self.mapping('МТЗ 324', MIXED, tractor)
        # «не наша» строка на спецтехнику: исключение сильнее правила
        self.mapping('Чужой кран', ALIEN, loader, skip=1)

    def sql(self, statement, args=()):
        con = sqlite3.connect(self.db)
        try:
            cursor = con.execute(statement, args)
            con.commit()
            return cursor.lastrowid
        finally:
            con.close()

    def equipment(self, name, category, plate=None):
        return self.sql('INSERT INTO equipment (name, plate, category, '
                        'organization_id, is_active) VALUES (?, ?, ?, 1, 1)',
                        (name, plate, category))

    def mapping(self, name, wialon_id, equipment_id, skip=0):
        return self.sql('INSERT INTO vialon_mappings (vialon_name, wialon_id, '
                        'equipment_id, skip) VALUES (?, ?, ?, ?)',
                        (name, wialon_id, equipment_id, skip))

    def day(self, work_date, unit, reason=None, hectares=None):
        self.sql('INSERT INTO gps_daily_aggregates (work_date, wialon_id, '
                 'points_total, track_km, reason) VALUES (?, ?, 100, 5.0, ?)',
                 (work_date, unit, reason))
        if hectares is not None:
            self.sql('INSERT INTO gps_work_polygons (work_date, wialon_id, '
                     'site_number, area_ha) VALUES (?, ?, 1, ?)',
                     (work_date, unit, hectares))

    def digest(self):
        with open(self.db, 'rb') as handle:
            return hashlib.sha256(handle.read()).hexdigest()

    def run_tool(self, since):
        con = tod.open_readonly(self.db)
        try:
            units, table = tod.days_by_date(con, since)
            lines = []
            counted, ha = tod.report(units, table, since, out=lines.append)
            return units, table, counted, ha, lines
        finally:
            con.close()


class ByDateAndReason(World):

    def test_the_total_is_the_inventory_s_own_days_computed(self):
        self.day('2026-09-26', LOADER, 'net_dvizheniya')
        self.day('2026-09-27', LOADER, 'spetstekhnika')
        # сутки ПОСЛЕ окна догона: правило их ещё не видело
        self.day('2026-09-28', LOADER, None)
        self.day('2026-09-25', LOADER, 'net_tochek')
        self.day('2026-09-24', LOADER, 'sbor_nepolnyy')
        self.day('2026-08-29', LOADER, 'spetstekhnika')     # ровно --since
        self.day('2026-08-28', LOADER, 'spetstekhnika')     # раньше --since
        units, table, counted, ha, _lines = self.run_tool('2026-08-29')
        self.assertEqual(units, [LOADER])
        self.assertEqual(table['2026-09-28']['published'], 1)
        self.assertEqual(table['2026-09-27']['spetstekhnika'], 1)
        self.assertEqual(table['2026-09-26']['other'], 1)
        self.assertEqual(table['2026-09-25']['net_tochek'], 1)
        self.assertEqual(table['2026-09-24']['sbor_nepolnyy'], 1)
        self.assertEqual(table['2026-08-29']['spetstekhnika'], 1)
        self.assertNotIn('2026-08-28', table)
        self.assertEqual(counted, 3)
        con = inv.open_readonly(self.db)
        try:
            activity = inv.computed_activity(con, '2026-08-29')
        finally:
            con.close()
        self.assertEqual(activity[LOADER]['days'], counted)
        self.assertEqual(activity[LOADER]['ha'], ha)

    def test_hectares_of_a_published_day_are_shown_not_hidden(self):
        """Отрицательный контроль: ноль гектаров -- вывод, а не константа."""
        self.day('2026-09-28', LOADER, None, hectares=3.5)
        self.day('2026-09-27', LOADER, 'spetstekhnika', hectares=8.0)
        _units, table, counted, ha, lines = self.run_tool('2026-08-29')
        # полигоны суток спецтехники остаются в базе, но гектаров не дают
        self.assertEqual(ha, 3.5)
        self.assertEqual(table['2026-09-28']['ha'], 3.5)
        self.assertEqual(counted, 2)
        self.assertTrue(any('3.50' in line for line in lines))

    def test_contradictions_excluded_and_field_machines_are_not_listed(self):
        for unit in (TRACTOR, MIXED, ALIEN):
            self.day('2026-09-27', unit, None, hectares=5.0)
        units, table, counted, ha, _lines = self.run_tool('2026-08-29')
        self.assertEqual(units, [LOADER])
        self.assertEqual((counted, ha), (0, 0.0))
        self.assertEqual(dict(table), {})

    def test_the_output_is_ascii(self):
        self.day('2026-09-27', LOADER, 'spetstekhnika')
        _units, _table, _counted, _ha, lines = self.run_tool('2026-08-29')
        self.assertTrue(all(line.isascii() for line in lines))


class ReadOnly(World):

    def test_the_database_is_not_changed_and_refuses_writes(self):
        self.day('2026-09-27', LOADER, 'spetstekhnika')
        before = self.digest()
        with contextlib.redirect_stdout(io.StringIO()) as out:
            code = tod.main(['--db', self.db, '--since', '2026-08-29'])
        self.assertEqual(code, 0)
        self.assertIn('counted days', out.getvalue())
        self.assertTrue(out.getvalue().isascii())
        after = self.digest()
        self.assertEqual(before, after)
        con = tod.open_readonly(self.db)
        try:
            with self.assertRaises(sqlite3.OperationalError):
                con.execute("UPDATE gps_daily_aggregates SET reason = NULL")
        finally:
            con.close()

    def test_a_missing_database_is_refused_and_not_created(self):
        missing = os.path.join(self.folder, 'nope.db')
        with contextlib.redirect_stderr(io.StringIO()) as err:
            code = tod.main(['--db', missing, '--since', '2026-08-29'])
        self.assertEqual(code, 2)
        self.assertIn('no database', err.getvalue())
        self.assertFalse(os.path.exists(missing))

    def test_since_must_be_a_date(self):
        with contextlib.redirect_stderr(io.StringIO()) as err:
            code = tod.main(['--db', self.db, '--since', '29.08.2026'])
        self.assertEqual(code, 2)
        self.assertIn('YYYY-MM-DD', err.getvalue())


class OneDateByObject(World):
    """`--date`: чьи гектары спецтехники легли на одни сутки."""

    def setUp(self):
        super().setUp()
        crane = self.equipment('Автокран', 'special', plate='725 KBA')
        # строка без машины раньше строки с машиной: имя всё равно машинное
        self.mapping('Кран, старый трекер', CRANE, None)
        self.mapping('Автокран 725 KBA', CRANE, crane)

    def site(self, work_date, unit, number, hectares):
        self.sql('INSERT INTO gps_work_polygons (work_date, wialon_id, '
                 'site_number, area_ha) VALUES (?, ?, ?, ?)',
                 (work_date, unit, number, hectares))

    def by_object(self, day):
        con = tod.open_readonly(self.db)
        try:
            units, table = tod.days_by_date(con, day)
            rows = tod.objects_on(con, day, units)
            names = tod.unit_names(con, [row['wialon_id'] for row in rows])
            return units, table, rows, names
        finally:
            con.close()

    def test_the_objects_of_a_date_add_up_to_its_row_in_the_table(self):
        self.day('2026-09-28', LOADER, None, hectares=3.5)
        self.site('2026-09-28', LOADER, 2, 1.25)
        self.day('2026-09-28', CRANE, 'net_dvizheniya')
        # соседние сутки той же машины в разбивку даты не входят
        self.day('2026-09-27', LOADER, None, hectares=8.0)
        self.day('2026-09-29', LOADER, None, hectares=2.0)
        # полевые, противоречие и исключённый -- не объекты правила
        for unit in (TRACTOR, MIXED, ALIEN):
            self.day('2026-09-28', unit, None, hectares=5.0)
        units, table, rows, _names = self.by_object('2026-09-28')
        self.assertEqual(sorted(units), [CRANE, LOADER])
        self.assertEqual([row['wialon_id'] for row in rows], [LOADER, CRANE])
        loader, crane = rows
        self.assertEqual((loader['reason'], loader['sites'], loader['ha'],
                          loader['largest']), (None, 2, 4.75, 3.5))
        self.assertEqual((crane['reason'], crane['sites'], crane['ha']),
                         ('net_dvizheniya', 0, 0.0))
        self.assertEqual(sum(row['ha'] for row in rows),
                         table['2026-09-28']['ha'])

    def test_a_kept_polygon_of_a_track_only_day_gives_no_hectares(self):
        # участок с ответом оператора переживает правило, но гектаров не даёт
        self.day('2026-09-28', LOADER, 'spetstekhnika', hectares=6.0)
        # у крана суток 28.09 нет: следующие сутки не делают его строкой даты
        self.day('2026-09-29', CRANE, None, hectares=1.0)
        _units, table, rows, _names = self.by_object('2026-09-28')
        self.assertEqual([row['wialon_id'] for row in rows], [LOADER])
        self.assertEqual((rows[0]['sites'], rows[0]['ha']), (0, 0.0))
        self.assertEqual(table['2026-09-28']['ha'], 0.0)

    def test_names_are_the_screen_s_model_and_plate(self):
        self.day('2026-09-28', LOADER, None, hectares=1.0)
        self.day('2026-09-28', CRANE, None, hectares=2.0)
        _units, _table, _rows, names = self.by_object('2026-09-28')
        self.assertEqual(names[CRANE], 'Автокран — 725 KBA')
        # без госномера -- одна модель, как на экране
        self.assertEqual(names[LOADER], 'Погрузчик 326 HA')

    def test_the_command_prints_the_day_in_ascii_and_writes_nothing(self):
        self.day('2026-09-28', CRANE, None, hectares=2.0)
        self.day('2026-09-28', LOADER, None, hectares=1.0)
        # исключённый и полевой в разбивку команды не попадают
        self.day('2026-09-28', ALIEN, None, hectares=5.0)
        self.day('2026-09-28', TRACTOR, None, hectares=5.0)
        before = self.digest()
        with contextlib.redirect_stdout(io.StringIO()) as out:
            code = tod.main(['--db', self.db, '--date', '2026-09-28'])
        text = out.getvalue()
        self.assertEqual(code, 0)
        self.assertTrue(text.isascii())
        self.assertIn('Avtokran - 725 KBA', text)
        self.assertIn('Pogruzchik 326 HA', text)
        self.assertRegex(text, r'hectares on published days\s+: 3\.00')
        self.assertNotIn('MISMATCH', text)
        self.assertEqual(before, self.digest())

    def test_a_mismatch_with_the_table_is_said_out_loud(self):
        """Отрицательный контроль сверки: она умеет сказать «не сходится»."""
        rows = [{'wialon_id': LOADER, 'reason': None, 'sites': 1, 'ha': 2.0,
                 'largest': 2.0, 'km': None, 'points_work': None,
                 'jumps': None}]
        lines = []
        total = tod.report_day('2026-09-28', [LOADER], rows, {}, 3.0,
                               out=lines.append)
        self.assertEqual(total, 2.0)
        self.assertTrue(any(line.startswith('MISMATCH') for line in lines))
        row_line = next(line for line in lines
                        if line.strip().startswith(str(LOADER)))
        # пустые измерения печатаются прочерком, а не нулём
        self.assertRegex(row_line, r'\s-\s+-\s+-\s')

    def test_since_and_date_are_one_or_the_other(self):
        for argv in (['--db', self.db],
                     ['--db', self.db, '--since', '2026-08-29',
                      '--date', '2026-09-28']):
            with self.subTest(argv=argv), \
                    contextlib.redirect_stderr(io.StringIO()):
                with self.assertRaises(SystemExit) as stop:
                    tod.main(argv)
                self.assertEqual(stop.exception.code, 2)

    def test_the_date_must_be_a_date(self):
        with contextlib.redirect_stderr(io.StringIO()) as err:
            code = tod.main(['--db', self.db, '--date', '28.09.2026'])
        self.assertEqual(code, 2)
        self.assertIn('--date must look like YYYY-MM-DD', err.getvalue())


class SameNamesAsTheEngine(unittest.TestCase):

    def test_reason_names_are_the_engine_s_own(self):
        # gps/daily.py тянет numpy и в этом окружении не импортируется --
        # имена причин читаются из его исходника.
        path = os.path.join(REPO_ROOT, 'gps', 'daily.py')
        with open(path, encoding='utf-8') as handle:
            tree = ast.parse(handle.read())
        values = {}
        for node in tree.body:
            if (isinstance(node, ast.Assign) and len(node.targets) == 1
                    and isinstance(node.targets[0], ast.Name)
                    and isinstance(node.value, ast.Constant)):
                values[node.targets[0].id] = node.value.value
        self.assertEqual(values['REASON_NO_POINTS'], tod.REASON_NO_POINTS)
        self.assertEqual(values['REASON_INCOMPLETE'], tod.REASON_INCOMPLETE)


if __name__ == '__main__':
    unittest.main()
