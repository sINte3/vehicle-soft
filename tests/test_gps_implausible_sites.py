# -*- coding: utf-8 -*-
"""tools/gps_implausible_sites.py -- участки, которых не может быть.

[REASON]: 30.09.2026 один грузовик дал за сутки участок 230 га по 184 точкам в
работе. Правило A1 снимает гектары спецтехники, но у трактора и у объекта без
сопоставления такой выброс ушёл бы в план-факт работой. Отчёт называет участки,
площадь которых больше, чем покрыл бы агрегат заданной ширины за минуты на
участке на верхней скорости окна метода. Здесь держится: предел и граница
«ровно на пределе», только опубликованные сутки и только период, виды
объектов по правилам расчёта, порядок «худшие первыми», сводка, имена как на
экране, ASCII и то, что база не меняется.
"""
import ast
import contextlib
import hashlib
import io
import os
import re
import shutil
import sqlite3
import sys
import tempfile
import unittest

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if REPO_ROOT not in sys.path:
    sys.path.insert(0, REPO_ROOT)

import tools.gps_implausible_sites as gis                           # noqa: E402
from tests.test_gps_units_inventory import DDL                      # noqa: E402

FIELD, SPECIAL, NO_MACHINE, NO_MAPPING, EXCLUDED = 387, 319, 7001, 8854, 5001


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
        tractor = self.equipment('МТЗ-80.1', 'mtz', '25 GA 691')
        truck = self.equipment('Isuzu', 'special', '80 258 JAA')
        self.mapping('МТЗ 691', FIELD, tractor)
        self.mapping('Isuzu 258', SPECIAL, truck)
        self.mapping('Камаз 7001', NO_MACHINE, None)
        # «не наша» строка на спецтехнику: исключение сильнее правила A1
        self.mapping('Чужой грузовик', EXCLUDED, truck, skip=1)
        # NO_MAPPING строки нет вовсе
        # 36 м на 15 км/ч -- 0,9 га в минуту на участке
        self.day('2026-09-28', FIELD)
        self.site('2026-09-28', FIELD, 1, 2.0, 60.0)       # предел 54
        self.site('2026-09-28', FIELD, 2, 60.0, 60.0)      # x1.1
        self.day('2026-09-28', SPECIAL)
        self.site('2026-09-28', SPECIAL, 1, 230.23, 20.0)  # предел 18
        self.day('2026-09-27', NO_MACHINE)
        self.site('2026-09-27', NO_MACHINE, 1, 5.0, None)  # площадь без минут
        # ровно на границах периода -- читаются, и предел их не задевает
        self.day('2026-09-01', FIELD)
        self.site('2026-09-01', FIELD, 1, 1.0, 60.0)
        self.day('2026-09-30', FIELD)
        self.site('2026-09-30', FIELD, 1, 1.0, 60.0)
        self.day('2026-09-26', NO_MAPPING)
        self.site('2026-09-26', NO_MAPPING, 1, 40.0, 10.0)  # предел 9
        self.day('2026-09-26', EXCLUDED)
        self.site('2026-09-26', EXCLUDED, 1, 100.0, 1.0)    # предел 0,9
        # неопубликованные сутки и сутки вне периода не читаются
        self.day('2026-09-25', FIELD, 'sbor_nepolnyy')
        self.site('2026-09-25', FIELD, 1, 500.0, 1.0)
        self.day('2026-08-31', FIELD)
        self.site('2026-08-31', FIELD, 1, 500.0, 1.0)
        self.day('2026-10-01', FIELD)
        self.site('2026-10-01', FIELD, 1, 500.0, 1.0)

    def sql(self, statement, args=()):
        con = sqlite3.connect(self.db)
        try:
            cursor = con.execute(statement, args)
            con.commit()
            return cursor.lastrowid
        finally:
            con.close()

    def equipment(self, name, category, plate):
        return self.sql('INSERT INTO equipment (name, plate, category, '
                        'organization_id, is_active) VALUES (?, ?, ?, 1, 1)',
                        (name, plate, category))

    def mapping(self, name, wialon_id, equipment_id, skip=0):
        self.sql('INSERT INTO vialon_mappings (vialon_name, wialon_id, '
                 'equipment_id, skip) VALUES (?, ?, ?, ?)',
                 (name, wialon_id, equipment_id, skip))

    def day(self, work_date, unit, reason=None):
        self.sql('INSERT INTO gps_daily_aggregates (work_date, wialon_id, '
                 'points_total, track_km, reason) VALUES (?, ?, 100, 5.0, ?)',
                 (work_date, unit, reason))

    def site(self, work_date, unit, number, hectares, minutes):
        self.sql('INSERT INTO gps_work_polygons (work_date, wialon_id, '
                 'site_number, area_ha, minutes) VALUES (?, ?, ?, ?, ?)',
                 (work_date, unit, number, hectares, minutes))

    def read(self, since='2026-09-01', until='2026-09-30', width=36.0):
        con = gis.open_readonly(self.db)
        try:
            sites = gis.published_sites(con, since, until)
            bad = gis.impossible(sites, width)
            kinds = gis.kinds_of(con, sorted({s['wialon_id'] for s in sites}))
            names = gis.unit_names(con, sorted({s['wialon_id'] for s in bad}))
            return sites, bad, kinds, names
        finally:
            con.close()

    def digest(self):
        with open(self.db, 'rb') as handle:
            return hashlib.sha256(handle.read()).hexdigest()

    def run_main(self, *argv):
        with contextlib.redirect_stdout(io.StringIO()) as out, \
                contextlib.redirect_stderr(io.StringIO()) as err:
            code = gis.main(['--db', self.db] + list(argv))
        return code, out.getvalue(), err.getvalue()


class WhatCannotBe(World):

    def test_the_sites_that_cannot_be_are_named_worst_first(self):
        _sites, bad, _kinds, _names = self.read()
        self.assertEqual(
            [(s['day'], s['wialon_id'], s['site']) for s in bad],
            [('2026-09-27', NO_MACHINE, 1),      # площадь без минут
             ('2026-09-26', EXCLUDED, 1),        # x111
             ('2026-09-28', SPECIAL, 1),         # x12.8
             ('2026-09-26', NO_MAPPING, 1),      # x4.4
             ('2026-09-28', FIELD, 2)])          # x1.1
        special = bad[2]
        self.assertAlmostEqual(special['limit'], 18.0)
        self.assertAlmostEqual(special['times'], 230.23 / 18.0)
        self.assertIsNone(bad[0]['times'])

    def test_only_published_days_of_the_period_are_read(self):
        sites, _bad, _kinds, _names = self.read()
        days = {(s['day'], s['wialon_id']) for s in sites}
        self.assertNotIn(('2026-09-25', FIELD), days)   # sbor_nepolnyy
        self.assertNotIn(('2026-08-31', FIELD), days)   # до периода
        self.assertNotIn(('2026-10-01', FIELD), days)   # после периода
        self.assertIn(('2026-09-01', FIELD), days)      # ровно --since
        self.assertIn(('2026-09-30', FIELD), days)      # ровно --until
        self.assertEqual(len(sites), 8)
        # без конца периода поздние сутки читаются
        sites, bad, _kinds, _names = self.read(until=None)
        self.assertIn(('2026-10-01', FIELD), {(s['day'], s['wialon_id'])
                                              for s in sites})
        self.assertEqual(bad[1]['day'], '2026-10-01')

    def test_the_width_moves_the_bound_and_the_bound_itself_is_possible(self):
        # 40 м: предел трактора 60 га -- ровно его площадь, это ещё возможно
        _sites, bad, _kinds, _names = self.read(width=40.0)
        self.assertNotIn(FIELD, [s['wialon_id'] for s in bad])
        self.assertIn(SPECIAL, [s['wialon_id'] for s in bad])
        self.assertAlmostEqual(gis.limit_ha(60.0, 40.0), 60.0)

    def test_kinds_follow_the_engine_s_rules(self):
        _sites, _bad, kinds, _names = self.read()
        self.assertEqual(kinds, {FIELD: 'field', SPECIAL: 'special',
                                 NO_MACHINE: 'no_machine',
                                 NO_MAPPING: 'no_mapping',
                                 EXCLUDED: 'excluded'})

    def test_names_are_the_screen_s(self):
        _sites, _bad, _kinds, names = self.read()
        self.assertEqual(names[FIELD], 'МТЗ-80.1 — 25 GA 691')
        self.assertEqual(names[SPECIAL], 'Isuzu — 80 258 JAA')
        self.assertEqual(names[NO_MACHINE], 'Камаз 7001')
        self.assertNotIn(NO_MAPPING, names)


class TheReport(World):

    def test_the_summary_adds_up_per_kind(self):
        sites, bad, kinds, names = self.read()
        lines = []
        count, hectares = gis.report(sites, bad, kinds, names, '2026-09-01',
                                     '2026-09-30', 36.0, 40, out=lines.append)
        self.assertEqual(count, 5)
        self.assertAlmostEqual(hectares, 435.23)
        text = '\n'.join(lines)
        self.assertIn('published sites    : 8, 439.23 ha', text)
        self.assertIn('impossible sites   : 5, 435.23 ha (99.1% of the '
                      'hectares)', text)
        self.assertIn('0.90 ha per minute', text)
        expected = {'field': ('64.00', '1', '60.00', '1'),
                    'special': ('230.23', '1', '230.23', '1'),
                    'no_machine': ('5.00', '1', '5.00', '1'),
                    'no_mapping': ('40.00', '1', '40.00', '1'),
                    'excluded': ('100.00', '1', '100.00', '1')}
        for kind, numbers in expected.items():
            with self.subTest(kind=kind):
                row = next(line.split() for line in lines
                           if line.split() and line.split()[0] == kind)
                self.assertEqual(tuple(row[1:]), numbers)

    def test_the_command_lists_names_in_ascii_and_writes_nothing(self):
        before = self.digest()
        code, out, _err = self.run_main('--since', '2026-09-01',
                                        '--until', '2026-09-30')
        self.assertEqual(code, 0)
        self.assertTrue(out.isascii())
        self.assertIn('MTZ-80.1 - 25 GA 691', out)
        self.assertIn('Isuzu - 80 258 JAA', out)
        self.assertIn('Kamaz 7001', out)
        self.assertRegex(out, r'2026-09-27\s+7001\s+no_machine\s+1\s+5\.00\s+'
                              r'-\s+0\.00\s+no time')
        self.assertIn('nothing was written to the database', out)
        self.assertEqual(before, self.digest())

    def test_top_limits_the_list_not_the_counts(self):
        code, out, _err = self.run_main('--since', '2026-09-01', '--until',
                                        '2026-09-30', '--top', '2')
        self.assertEqual(code, 0)
        self.assertIn('impossible sites   : 5,', out)
        self.assertIn('(2 of 5)', out)
        listed = re.findall(r'^2026-\d\d-\d\d\s', out, re.M)
        self.assertEqual(len(listed), 2)

    def test_refusals(self):
        missing = os.path.join(self.folder, 'nope.db')
        for argv, needle in (
                (['--since', '01.09.2026'], '--since must look like'),
                (['--since', '2026-09-01', '--until', '30.09'],
                 '--until must look like'),
                (['--since', '2026-09-01', '--width-m', '0'],
                 '--width-m must be above zero')):
            with self.subTest(argv=argv):
                code, _out, err = self.run_main(*argv)
                self.assertEqual(code, 2)
                self.assertIn(needle, err)
        with contextlib.redirect_stderr(io.StringIO()) as err:
            code = gis.main(['--db', missing, '--since', '2026-09-01'])
        self.assertEqual(code, 2)
        self.assertIn('no database', err.getvalue())
        self.assertFalse(os.path.exists(missing))

    def test_the_database_refuses_writes(self):
        con = gis.open_readonly(self.db)
        try:
            with self.assertRaises(sqlite3.OperationalError):
                con.execute('UPDATE gps_work_polygons SET area_ha = 0')
        finally:
            con.close()


class SameBoundAsTheMethod(unittest.TestCase):

    def test_the_speed_bound_is_the_method_s_own(self):
        # gps/area.py тянет numpy -- число читается из исходника метода.
        path = os.path.join(REPO_ROOT, 'gps', 'area.py')
        with open(path, encoding='utf-8') as handle:
            tree = ast.parse(handle.read())
        values = {node.targets[0].id: node.value.value for node in tree.body
                  if isinstance(node, ast.Assign) and len(node.targets) == 1
                  and isinstance(node.targets[0], ast.Name)
                  and isinstance(node.value, ast.Constant)}
        self.assertEqual(values['SPEED_MAX_KMH'], gis.SPEED_MAX_KMH)


if __name__ == '__main__':
    unittest.main()
