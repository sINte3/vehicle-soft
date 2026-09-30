# -*- coding: utf-8 -*-
"""tools/gps_track_only_days.py -- сутки спецтехники по датам и причинам.

[REASON]: 30.09.2026 инвентарь после правила A1 показал 453 суток
спецтехники при 440, переведённых догоном: догон работает в окне до вчера,
инвентарь считает от даты без конца. Инструмент раскладывает сутки по датам,
и его итог «counted» обязан совпадать со столбцом `days_computed` инвентаря
-- иначе разбивка объясняла бы не то число. Это и проверяется сверкой с
`computed_activity` самого инвентаря, а не пересказом его правила.
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

    def equipment(self, name, category):
        return self.sql('INSERT INTO equipment (name, category, '
                        'organization_id, is_active) VALUES (?, ?, 1, 1)',
                        (name, category))

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
