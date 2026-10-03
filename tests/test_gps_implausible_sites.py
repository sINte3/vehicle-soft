# -*- coding: utf-8 -*-
"""tools/gps_implausible_sites.py -- участки, которых не может быть.

[REASON]: 30.09.2026 один грузовик дал за сутки участок 230 га по 184 точкам в
работе. Правило A1 снимает гектары спецтехники, но у трактора и у объекта без
сопоставления такой выброс ушёл бы в план-факт работой. Отчёт называет
участки, площадь которых больше, чем покрыл бы агрегат заданной ширины за
время на участке на верхней скорости окна метода.

Здесь -- всё, что не требует геометрии: предел и граница «ровно на пределе»,
два шага времени (минуты из базы -- только отбор, решает время по треку),
узкая полоса без минут в базе -- НЕ невозможна, участок без трека или контура
-- «не оценён», только опубликованные сутки и только период, виды объектов,
порядок, сводка, имена как на экране, ASCII и то, что ни база, ни файлы точек
не меняются. Время по треку здесь подменено (`measure`); настоящее считается
в `gps/tests/test_implausible_sites.py` в окружении расчёта.
"""
import ast
import contextlib
import hashlib
import io
import json
import os
import re
import shutil
import sqlite3
import sys
import tempfile
import unittest
import unittest.mock
from datetime import datetime

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if REPO_ROOT not in sys.path:
    sys.path.insert(0, REPO_ROOT)

import tools.gps_implausible_sites as gis                           # noqa: E402
from gps_collector import config as collector_config               # noqa: E402
from gps_collector import storage                                  # noqa: E402
from tests.test_gps_units_inventory import DDL                      # noqa: E402

FIELD, SPECIAL, NO_MACHINE, NO_MAPPING, EXCLUDED = 387, 319, 7001, 8854, 5001


def fake_measure(calls=None):
    """Время по треку из контура-заглушки: {"minutes": N}; без ключа -- None."""
    def measure(points, text):
        if calls is not None:
            calls.append(text)
        return json.loads(text).get('minutes')
    return measure


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
        # файл точек за сентябрь есть, за октябрь -- нет (срок хранения)
        storage.open_points(self.folder, '202609').close()
        self.points = storage.points_path(self.folder, '202609')
        tractor = self.equipment('МТЗ-80.1', 'mtz', '25 GA 691')
        truck = self.equipment('Isuzu', 'special', '80 258 JAA')
        self.mapping('МТЗ 691', FIELD, tractor)
        self.mapping('Isuzu 258', SPECIAL, truck)
        self.mapping('Камаз 7001', NO_MACHINE, None)
        # «не наша» строка на спецтехнику: исключение сильнее правила A1
        self.mapping('Чужой грузовик', EXCLUDED, truck, skip=1)
        # NO_MAPPING строки нет вовсе
        # 36 м на 15 км/ч -- 0,9 га в минуту; (площадь, минуты в базе, по треку)
        self.day('2026-09-28', FIELD)
        self.site('2026-09-28', FIELD, 1, 2.0, 60.0, 60.0)       # предел 54
        self.site('2026-09-28', FIELD, 2, 60.0, 60.0, 60.0)      # x1.1
        self.day('2026-09-28', SPECIAL)
        self.site('2026-09-28', SPECIAL, 1, 230.23, 20.0, 20.0)  # x12.8
        self.day('2026-09-27', NO_MACHINE)
        self.site('2026-09-27', NO_MACHINE, 1, 5.0, None, 0.0)   # без времени
        self.day('2026-09-26', NO_MAPPING)
        self.site('2026-09-26', NO_MAPPING, 1, 40.0, 10.0, 10.0)  # x4.4
        self.day('2026-09-26', EXCLUDED)
        self.site('2026-09-26', EXCLUDED, 1, 100.0, 1.0, 1.0)     # x111
        # узкая полоса: в базе 0 минут (точки на границе), по треку -- 15
        self.day('2026-09-29', FIELD)
        self.site('2026-09-29', FIELD, 1, 0.6, 0.0, 15.0)
        # контур не читается: не оценён, а не невозможен
        self.site('2026-09-29', FIELD, 2, 3.0, 0.0, None)
        # по треку ровно на пределе: 10 минут -- 9 га, это ещё возможно
        self.site('2026-09-29', FIELD, 3, 9.0, 0.0, 10.0)
        # ровно на границах периода; укладываются уже по минутам из базы
        self.day('2026-09-01', FIELD)
        self.site('2026-09-01', FIELD, 1, 1.0, 60.0, 60.0)
        self.day('2026-09-30', FIELD)
        self.site('2026-09-30', FIELD, 1, 1.0, 60.0, 60.0)
        # неопубликованные сутки и сутки вне периода не читаются
        self.day('2026-09-25', FIELD, 'sbor_nepolnyy')
        self.site('2026-09-25', FIELD, 1, 500.0, 1.0, 1.0)
        self.day('2026-08-31', FIELD)
        self.site('2026-08-31', FIELD, 1, 500.0, 1.0, 1.0)
        # октябрь: файла точек нет -- не оценён
        self.day('2026-10-01', FIELD)
        self.site('2026-10-01', FIELD, 1, 500.0, 1.0, 1.0)

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

    def site(self, work_date, unit, number, hectares, minutes, by_track):
        contour = json.dumps({} if by_track is None else {'minutes': by_track})
        self.sql('INSERT INTO gps_work_polygons (work_date, wialon_id, '
                 'site_number, area_ha, minutes, polygon_geojson) '
                 'VALUES (?, ?, ?, ?, ?, ?)',
                 (work_date, unit, number, hectares, minutes, contour))

    def read(self, since='2026-09-01', until='2026-09-30', width=36.0,
             calls=None):
        con = gis.open_readonly(self.db)
        try:
            sites = gis.published_sites(con, since, until)
            suspects = gis.candidates(sites, width)
            bad, unjudged = gis.judge(con, self.folder, suspects, width,
                                      fake_measure(calls))
            kinds = gis.kinds_of(con, sorted({s['wialon_id'] for s in sites}))
            names = gis.unit_names(con, sorted({s['wialon_id'] for s in bad}))
            return sites, suspects, bad, unjudged, kinds, names
        finally:
            con.close()

    def digest(self, path):
        with open(path, 'rb') as handle:
            return hashlib.sha256(handle.read()).hexdigest()

    def run_main(self, *argv):
        with contextlib.redirect_stdout(io.StringIO()) as out, \
                contextlib.redirect_stderr(io.StringIO()) as err:
            code = gis.main(['--db', self.db, '--dir', self.folder]
                            + list(argv), measure=fake_measure())
        return code, out.getvalue(), err.getvalue()


def keys(sites):
    return [(s['day'], s['wialon_id'], s['site']) for s in sites]


class WhatCannotBe(World):

    def test_the_sites_that_cannot_be_are_named_worst_first(self):
        _sites, _suspects, bad, _unjudged, _kinds, _names = self.read()
        self.assertEqual(keys(bad), [
            ('2026-09-27', NO_MACHINE, 1),      # по треку ни минуты
            ('2026-09-26', EXCLUDED, 1),        # x111
            ('2026-09-28', SPECIAL, 1),         # x12.8
            ('2026-09-26', NO_MAPPING, 1),      # x4.4
            ('2026-09-28', FIELD, 2)])          # x1.1
        special = bad[2]
        self.assertAlmostEqual(special['limit'], 18.0)
        self.assertAlmostEqual(special['times'], 230.23 / 18.0)
        self.assertIsNone(bad[0]['times'])

    def test_a_thin_strip_with_no_stored_minutes_is_not_impossible(self):
        """Минуты базы -- строго внутри; у полосы все точки на границе."""
        _sites, suspects, bad, unjudged, _kinds, _names = self.read()
        for strip in (('2026-09-29', FIELD, 1), ('2026-09-29', FIELD, 3)):
            with self.subTest(site=strip):
                self.assertIn(strip, keys(suspects))
                self.assertNotIn(strip, keys(bad))
                self.assertNotIn(strip, keys(unjudged))

    def test_without_a_track_or_a_contour_a_site_is_not_judged(self):
        _sites, _suspects, bad, unjudged, _kinds, _names = self.read(
            until=None)
        self.assertEqual([(s['day'], s['wialon_id'], s['site'], s['why'])
                          for s in unjudged],
                         [('2026-09-29', FIELD, 2, 'contour not readable'),
                          ('2026-10-01', FIELD, 1, 'no points file')])
        self.assertNotIn(('2026-10-01', FIELD, 1), keys(bad))
        # отсутствующий файл точек не создаётся
        self.assertFalse(os.path.exists(
            storage.points_path(self.folder, '202610')))

    def test_stored_minutes_only_select_and_the_track_decides(self):
        calls = []
        _sites, suspects, _bad, _unjudged, _kinds, _names = self.read(
            calls=calls)
        # укладывающиеся в предел уже по базе трек не читают
        self.assertNotIn(('2026-09-28', FIELD, 1), keys(suspects))
        self.assertNotIn(('2026-09-01', FIELD, 1), keys(suspects))
        self.assertEqual(len(suspects), 8)
        self.assertEqual(len(calls), 8)

    def test_only_published_days_of_the_period_are_read(self):
        sites, *_rest = self.read()
        days = {(s['day'], s['wialon_id']) for s in sites}
        self.assertNotIn(('2026-09-25', FIELD), days)   # sbor_nepolnyy
        self.assertNotIn(('2026-08-31', FIELD), days)   # до периода
        self.assertNotIn(('2026-10-01', FIELD), days)   # после периода
        self.assertIn(('2026-09-01', FIELD), days)      # ровно --since
        self.assertIn(('2026-09-30', FIELD), days)      # ровно --until
        self.assertEqual(len(sites), 11)

    def test_the_width_moves_the_bound_and_the_bound_itself_is_possible(self):
        # 40 м: предел участка трактора 60 га -- ровно его площадь
        _sites, suspects, bad, *_rest = self.read(width=40.0)
        self.assertNotIn(('2026-09-28', FIELD, 2), keys(suspects))
        self.assertNotIn(FIELD, [s['wialon_id'] for s in bad])
        self.assertIn(SPECIAL, [s['wialon_id'] for s in bad])
        self.assertAlmostEqual(gis.limit_ha(60.0, 40.0), 60.0)

    def test_kinds_follow_the_engine_s_rules(self):
        *_rest, kinds, _names = self.read()
        self.assertEqual(kinds, {FIELD: 'field', SPECIAL: 'special',
                                 NO_MACHINE: 'no_machine',
                                 NO_MAPPING: 'no_mapping',
                                 EXCLUDED: 'excluded'})

    def test_names_are_the_screen_s(self):
        *_rest, names = self.read()
        self.assertEqual(names[FIELD], 'МТЗ-80.1 — 25 GA 691')
        self.assertEqual(names[SPECIAL], 'Isuzu — 80 258 JAA')
        self.assertEqual(names[NO_MACHINE], 'Камаз 7001')
        self.assertNotIn(NO_MAPPING, names)


class TheTrack(World):

    def test_a_day_is_read_as_the_collector_reads_it(self):
        start = int(datetime(2026, 9, 28).replace(
            tzinfo=collector_config.TZ).timestamp())    # местная полночь
        rows = [(FIELD, start - 1, 64.5, 40.0, 8.0, 0, 12),       # 27.09
                (FIELD, start, 64.5, 40.0, 8.0, 0, 12),           # полночь
                (FIELD, start + 600, 64.6, 40.1, 20.0, 0, None),
                (FIELD, start + 86399, 64.7, 40.2, 5.0, 0, 9),
                (FIELD, start + 86400, 64.8, 40.3, 5.0, 0, 9),    # 29.09
                (SPECIAL, start + 60, 64.9, 40.4, 3.0, 0, 7)]
        storage.write_points(self.folder, rows)
        before = self.digest(self.points)
        ours = gis.read_day_readonly(self.folder, FIELD, '2026-09-28')
        self.assertEqual(ours, storage.read_day(self.folder, FIELD,
                                                '2026-09-28'))
        self.assertEqual([row[0] for row in ours],
                         [start, start + 600, start + 86399])
        self.assertEqual(ours[1][4], -1)
        self.assertEqual(before, self.digest(self.points))

    def test_the_point_file_is_opened_read_only(self):
        opened = []
        real = sqlite3.connect

        def spy(target, *args, **kwargs):
            opened.append((target, kwargs.get('uri')))
            return real(target, *args, **kwargs)

        with unittest.mock.patch.object(gis.sqlite3, 'connect', spy):
            self.assertEqual(
                gis.read_day_readonly(self.folder, FIELD, '2026-09-28'), [])
        self.assertEqual(len(opened), 1)
        target, uri = opened[0]
        self.assertTrue(uri)
        self.assertTrue(target.startswith('file:'))
        self.assertTrue(target.endswith('?mode=ro'))


class TheReport(World):

    def test_the_summary_adds_up_per_kind(self):
        sites, suspects, bad, unjudged, kinds, names = self.read()
        lines = []
        count, hectares = gis.report(sites, suspects, bad, unjudged, kinds,
                                     names, '2026-09-01', '2026-09-30', 36.0,
                                     40, out=lines.append)
        self.assertEqual(count, 5)
        self.assertAlmostEqual(hectares, 435.23)
        text = '\n'.join(lines)
        self.assertIn('published sites    : 11, 451.83 ha', text)
        self.assertIn('checked by track   : 8', text)
        self.assertIn('not judged         : 1, 3.00 ha', text)
        self.assertIn('impossible sites   : 5, 435.23 ha (96.3% of the '
                      'hectares)', text)
        self.assertIn('0.90 ha per minute', text)
        expected = {'field': ('76.60', '1', '60.00', '1'),
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
        before = (self.digest(self.db), self.digest(self.points))
        code, out, _err = self.run_main('--since', '2026-09-01',
                                        '--until', '2026-09-30')
        self.assertEqual(code, 0)
        self.assertTrue(out.isascii())
        # --until дошёл до выборки: октябрьского участка в счёте нет
        self.assertIn('published sites    : 11,', out)
        self.assertIn('not judged         : 1,', out)
        self.assertIn('MTZ-80.1 - 25 GA 691', out)
        self.assertIn('Isuzu - 80 258 JAA', out)
        self.assertIn('Kamaz 7001', out)
        self.assertRegex(out, r'2026-09-27\s+7001\s+no_machine\s+1\s+5\.00\s+'
                              r'0\.0\s+0\.00\s+no time')
        self.assertRegex(out, r'2026-09-29\s+387\s+field\s+2\s+3\.00\s+'
                              r'contour not readable')
        self.assertIn('nothing was written', out)
        self.assertEqual(before, (self.digest(self.db),
                                  self.digest(self.points)))

    def test_top_limits_the_list_not_the_counts(self):
        code, out, _err = self.run_main('--since', '2026-09-01', '--until',
                                        '2026-09-30', '--top', '2')
        self.assertEqual(code, 0)
        self.assertIn('impossible sites   : 5,', out)
        self.assertIn('(2 of 5)', out)
        listed = re.findall(r'^2026-\d\d-\d\d\s+\d+\s+\w+\s+\S+\s+[\d.]+\s+'
                            r'[\d.]+\s+[\d.]+\s', out, re.M)
        self.assertEqual(len(listed), 2)

    def test_refusals(self):
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
        missing = os.path.join(self.folder, 'nope.db')
        with contextlib.redirect_stderr(io.StringIO()) as err:
            code = gis.main(['--db', missing, '--dir', self.folder,
                             '--since', '2026-09-01'])
        self.assertEqual(code, 2)
        self.assertIn('no database', err.getvalue())
        self.assertFalse(os.path.exists(missing))
        with contextlib.redirect_stderr(io.StringIO()) as err:
            code = gis.main(['--db', self.db, '--dir',
                             os.path.join(self.folder, 'nope'),
                             '--since', '2026-09-01'])
        self.assertEqual(code, 2)
        self.assertIn('no folder', err.getvalue())

    def test_the_database_refuses_writes(self):
        con = gis.open_readonly(self.db)
        try:
            with self.assertRaises(sqlite3.OperationalError):
                con.execute('UPDATE gps_work_polygons SET area_ha = 0')
        finally:
            con.close()


class SameNumbersAsTheMethod(unittest.TestCase):

    def constants(self, *parts):
        # gps/area.py и gps/daily.py тянут numpy -- числа читаются из исходника
        with open(os.path.join(REPO_ROOT, *parts), encoding='utf-8') as handle:
            tree = ast.parse(handle.read())
        return {node.targets[0].id: node.value.value for node in tree.body
                if isinstance(node, ast.Assign) and len(node.targets) == 1
                and isinstance(node.targets[0], ast.Name)
                and isinstance(node.value, ast.Constant)}

    def test_the_speed_window_and_the_gap_cap_are_the_method_s_own(self):
        area = self.constants('gps', 'area.py')
        daily = self.constants('gps', 'daily.py')
        self.assertEqual(area['SPEED_MIN_KMH'], gis.SPEED_MIN_KMH)
        self.assertEqual(area['SPEED_MAX_KMH'], gis.SPEED_MAX_KMH)
        self.assertEqual(daily['SITE_GAP_CAP_S'], gis.SITE_GAP_CAP_S)


if __name__ == '__main__':
    unittest.main()
