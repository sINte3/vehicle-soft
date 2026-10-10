# -*- coding: utf-8 -*-
"""tools/gps_edge_replay.py -- «A7 / правило края поля» на одних и тех же точках.

Окружение расчёта (геометрия): прогон на настоящем движке и настоящем
`gps.edge.trim`. Здесь держится то, что делает прогон доказательством, а не
пересказом:
  * повтор пути движка даёт ровно участки `compute_day` -- и A7, и правила;
    чистое поле правило не трогает, дорогу вдоль края срезает, участок без
    сердцевины оставляет, участок, от которого не осталось рабочего куска,
    охрана возвращает -- и инструмент называет именно его;
  * контроль (A7 по точкам == строки базы) делает прогон недействительным, а
    не проваленным; строку другой версии метода он не судит;
  * каждый инвариант, нарушенный нарочно подменённым `gps.edge.trim`, даёт
    FAIL (форма снаружи, форма больше, удалённый участок);
  * исключённые объекты и сутки вне периода не пересчитываются;
  * набор В-5: строка находится по имени ключом экрана, ничья двух объектов
    одного имени решается суммой гектаров; строка V5EDGE ровно в формате;
  * условие 1273 -- в обе стороны, и пол 90%; сверка с предсказанием KML --
    OWNER CHECK, не FAIL;
  * ручные замеры 27.07: правило сопоставления по словам (сокращение,
    «-2» против «-21», дубль зоны, строка «+», неоднозначная строка) и
    условие в обе стороны по каждому пункту; 12.08 -- глазу владельца;
  * ответы операторов: «работа», срезанная больше допуска В-2, -- владельцу;
  * KML: у изменённого участка -- было, стало, убрано и отметка В-2;
    выборка «по sha1» воспроизводима;
  * код выхода несёт вердикт; неверный ввод отказывается до расчёта;
  * ни база, ни файлы точек не меняются; Flask и `models` не импортируются;
  * постоянные, переписанные из других мест, совпадают с источниками.

Запуск из корня репозитория окружением расчёта:
  python -m unittest gps.tests.test_edge_replay
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
import subprocess
import sys
import tempfile
import unittest
import unittest.mock
import xml.etree.ElementTree as ET
from datetime import datetime

from shapely.geometry import box
from shapely.ops import unary_union

import gps.edge as edge
import migrate_gps_daily_001 as migration
import tools.gps_alpha_replay as alpha_replay
import tools.gps_edge_replay as replay
from gps.area import METHOD_VERSION, PREVIOUS_METHOD_VERSION
from gps.daily import PASSAGE, WORK, compute_day, write_day
from gps.tests.test_area import shuttle_track, xy_to_lonlat
from gps.tests.test_edge import field_and_road, road, shifted
from gps_collector import config as collector_config
from gps_collector import storage
from tests.test_gps_units_inventory import DDL

REPO = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
ROADMAP = os.path.join(REPO, 'docs', 'GPS_PLAN_FAKT_VISION_ROADMAP.md')
V5_SET = os.path.join(REPO, 'docs', 'gps_v5_acceptance_set_2026-10-09.csv')

CLEAN, ROAD, TAMPERED, EXCLUDED, LATE, LABELS, SMALL = 501, 502, 503, 504, 505, 506, 507
V5A, V5B1, V5B2, NO_POINTS, OWNER = 511, 512, 513, 520, 1273
NS = {'k': 'http://www.opengis.net/kml/2.2'}


# --- синтетические сутки --------------------------------------------------------

def field(width=150.0, height=300.0):
    """Культивация, проходы через 6 м, точка каждые 20 м."""
    return shuttle_track(width, height, pass_spacing_m=6.0, point_step_m=20.0)


def field_with_road():
    """Поле 150 x 300 м и дорога вдоль края в 15 м: сегодня приклеена (4,756
    га), правило её срезает (4,500 га -- само поле)."""
    work, lane = field_and_road()
    return work + lane


def wide_field_and_strip():
    """Поле 300 x 100 м с дорогой вдоль длинного края -- правило срезает 13,9%,
    больше допуска В-2, -- и в 600 м полоса «туда и обратно» в 8 м: участок
    без сердцевины, правило его не трогает."""
    work = field(300.0, 100.0)
    lane, end = road([(-200.0, -15.0), (500.0, -15.0), (-200.0, -14.0)],
                     start_time=work[-1][0] + 600.0)
    strip, _ = road([(0.0, 0.0), (500.0, 0.0), (500.0, 8.0), (0.0, 8.0)],
                    start_time=end + 600.0)
    return work + lane + shifted(strip, 0.0, 600.0)


def small_field_with_road():
    """Поле 48 x 50 м с приклеенной дорогой -- 0,33 га: обрезанное, оно ушло
    бы под 0,3 га, и охрана `trim` возвращает его целиком."""
    work = field(48.0, 50.0)
    lane, _ = road([(-100.0, -15.0), (148.0, -15.0), (-100.0, -14.0)],
                   start_time=work[-1][0] + 600.0)
    return work + lane


def local_start(day):
    return int(datetime.strptime(day, '%Y-%m-%d')
               .replace(tzinfo=collector_config.TZ).timestamp())


def stamped(track, day, hour=8):
    start = local_start(day) + hour * 3600
    return [(start + int(t), lon, lat, speed, 10) for t, lon, lat, speed in track]


def run_main(*argv):
    with contextlib.redirect_stdout(io.StringIO()) as out, \
            contextlib.redirect_stderr(io.StringIO()) as err:
        code = replay.main([str(arg) for arg in argv])
    return code, out.getvalue(), err.getvalue()


def digest(path):
    with open(path, 'rb') as handle:
        return hashlib.sha256(handle.read()).hexdigest()


# --- мир: база и файлы точек, общие для модуля ----------------------------------

WORLD = {}

V5_HEADER = 'n,group,day,machine,org,application,work_type,gps_ha_a7\n'


def setUpModule():
    root = tempfile.mkdtemp()
    folder = os.path.join(root, 'points')
    os.mkdir(folder)
    db = os.path.join(root, 'transport.db')
    con = sqlite3.connect(db)
    try:
        for statement in DDL[:2]:                 # vialon_mappings, equipment
            con.execute(statement)
        con.execute(migration.CREATE_DAILY_AGGREGATES)
        con.execute(migration.CREATE_WORK_POLYGONS)
        machines = (('МТЗ-80Х', '80 613 EA'), ('New Holland 7060', '80 156 СА'),
                    ('Т-28', '01 020 AA'), ('New Holland 7060', '80 080 HA'),
                    ('МТЗ-82', '80 502 AA'), ('МТЗ-80.1', '80 506 BA'))
        for name, plate in machines:
            con.execute("INSERT INTO equipment (name, plate, category, "
                        "organization_id, is_active) VALUES (?, ?, 'mtz', 1, 1)",
                        (name, plate))
        # два объекта одной машины (смена трекера): одно имя на экране
        for title, unit, machine, skip in (
                ('МТЗ 613', V5A, 1, 0), ('NH 156 old', V5B1, 2, 0),
                ('NH 156 new', V5B2, 2, 0), ('Т-28 020', NO_POINTS, 3, 0),
                ('NH 080', OWNER, 4, 0), ('МТЗ 502', ROAD, 5, 0),
                ('МТЗ 506', LABELS, 6, 0), ('Чужой 504', EXCLUDED, None, 1)):
            con.execute("INSERT INTO vialon_mappings (vialon_name, wialon_id, "
                        "equipment_id, skip) VALUES (?, ?, ?, ?)",
                        (title, unit, machine, skip))
        con.commit()
        for day, unit, track in (
                ('2026-09-05', V5B1, field(100.0, 200.0)),
                ('2026-09-05', V5B2, field_with_road()),
                ('2026-09-10', CLEAN, field()),
                ('2026-09-11', ROAD, field_with_road()),
                ('2026-09-12', TAMPERED, field()),
                ('2026-09-13', EXCLUDED, field_with_road()),
                ('2026-09-14', LABELS, wide_field_and_strip()),
                ('2026-09-15', SMALL, small_field_with_road()),
                ('2026-09-16', V5A, field(100.0, 200.0)),
                ('2026-10-01', OWNER, field_with_road()),
                ('2026-10-02', LATE, field_with_road())):
            points = stamped(track, day)
            storage.write_points(folder, [(unit, t, lon, lat, speed, None, sats)
                                          for t, lon, lat, speed, sats in points])
            write_day(con, day, unit, compute_day(points), '2026-10-09 01:00:00')
        con.execute("UPDATE gps_work_polygons SET area_ha = area_ha + 1 "
                    "WHERE wialon_id = ?", (TAMPERED,))
        # опубликованная строка без точек на диске
        con.execute("INSERT INTO gps_daily_aggregates (work_date, wialon_id, "
                    "method_version, computed_at) VALUES ('2026-09-20', ?, ?, "
                    "'2026-09-21 01:00:00')", (NO_POINTS, METHOD_VERSION))
        for unit, site, label in ((ROAD, 1, WORK), (LABELS, 1, WORK),
                                  (LABELS, 2, PASSAGE)):
            con.execute("UPDATE gps_work_polygons SET operator_label = ?, "
                        "decided_at = '2026-10-09 10:00:00' WHERE wialon_id = ? "
                        "AND site_number = ?", (label, unit, site))
        con.commit()
    finally:
        con.close()
    WORLD.update(root=root, folder=folder, db=db)


def tearDownModule():
    shutil.rmtree(WORLD['root'], True)


class World(unittest.TestCase):

    def setUp(self):
        self.folder, self.db = WORLD['folder'], WORLD['db']
        self.scratch = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.scratch, True)

    def copy_db(self, *statements):
        """A copy of the world's database with these statements applied."""
        path = os.path.join(self.scratch, 'transport.db')
        shutil.copyfile(self.db, path)
        con = sqlite3.connect(path)
        try:
            for statement, args in statements:
                con.execute(statement, args)
            con.commit()
        finally:
            con.close()
        return path

    def untampered(self):
        return self.copy_db(("DELETE FROM gps_daily_aggregates WHERE wialon_id = ?",
                             (TAMPERED,)))

    def write(self, name, text):
        path = os.path.join(self.scratch, name)
        with open(path, 'w', encoding='utf-8') as handle:
            handle.write(text)
        return path

    def v5(self, *rows, name='v5.csv'):
        return self.write(name, V5_HEADER + ''.join(row + '\n' for row in rows))

    def record(self, day, unit, db=None):
        con = replay.open_readonly(db or self.db)
        try:
            points = replay.read_day_readonly(self.folder, unit, day)
            return replay.judge(day, unit, replay.replay(points, None),
                                replay.stored_day(con, day, unit))
        finally:
            con.close()

    def period(self, since, until, *extra, db=None):
        return run_main('--db', db or self.db, '--dir', self.folder,
                        '--since', since, '--until', until, *extra)


# --- повтор движка и сутки ----------------------------------------------------------

class Replay(World):

    def test_a_clean_field_is_the_same_under_both(self):
        record = self.record('2026-09-10', CLEAN)
        self.assertTrue(record['consistent'])
        self.assertTrue(record['control'])
        self.assertFalse(record['changed'])
        self.assertEqual(record['a7_ha'], record['rule_ha'])
        self.assertEqual(record['violations'], [])
        self.assertEqual(replay.site_line(record['sites'][0]),
                         'site 1: 4.500 -> 4.500 ha, unchanged')

    def test_a_road_along_the_headland_is_cut_off(self):
        record = self.record('2026-09-11', ROAD)
        self.assertTrue(record['consistent'])
        self.assertTrue(record['control'])
        self.assertTrue(record['changed'])
        self.assertAlmostEqual(record['a7_ha'], 4.7562, places=4)
        self.assertAlmostEqual(record['rule_ha'], 4.5002, places=4)
        site = record['sites'][0]
        self.assertGreater(site['core_points'], 0)
        self.assertLess(site['kept'], site['points'])
        self.assertFalse(site['restored'])
        self.assertEqual(replay.site_line(site),
                         'site 1: 4.756 -> 4.500 ha, removed 0.256 ha (5.4%)')

    def test_a_site_without_a_core_stays_and_a_wide_cut_is_beyond_b2(self):
        record = self.record('2026-09-14', LABELS)
        first, strip = record['sites']
        self.assertEqual(replay.site_line(first), 'site 1: 3.483 -> 3.000 ha, '
                         'removed 0.483 ha (13.9%) [beyond B-2]')
        self.assertEqual(strip['core_points'], 0)
        self.assertEqual(strip['kept'], strip['points'])
        self.assertEqual(replay.site_line(strip), 'site 2: 0.388 -> 0.388 ha, '
                         'unchanged')
        self.assertIn('no core: left as it is', replay.evidence_text(strip))

    def test_the_guard_puts_back_the_very_site_trim_counted(self):
        """0.33 ha trimmed below the floor comes back whole -- and is named."""
        record = self.record('2026-09-15', SMALL)
        self.assertEqual(record['restored'], 1)
        self.assertTrue(record['consistent'])
        site = record['sites'][0]
        self.assertTrue(site['restored'])
        self.assertGreater(site['core_points'], 0)
        self.assertLess(site['kept'], site['points'])     # the rule did cut it
        self.assertAlmostEqual(site['inside'], site['area'], places=9)
        self.assertIn('put back by the guard: YES', replay.evidence_text(site))

    def test_a_guard_count_the_repetition_cannot_find_is_a_replay_mismatch(self):
        """Negative control of `guard`: trim says 2, the sites say 1."""
        real = edge.trim

        def overcounted(*args):
            shape, info = real(*args)
            return shape, dict(info, restored=2)
        points = replay.read_day_readonly(self.folder, SMALL, '2026-09-15')
        with unittest.mock.patch.object(edge, 'trim', overcounted):
            played = replay.replay(points, None)
        self.assertFalse(played['found']['guard'])
        self.assertFalse(replay.judge('2026-09-15', SMALL, played)['consistent'])

    def test_a_repetition_that_drifts_from_compute_day_is_caught(self):
        """Negative control of `consistent`: the evidence path sees another alpha."""
        points = replay.read_day_readonly(self.folder, ROAD, '2026-09-11')
        with unittest.mock.patch.object(replay, '_adaptive_alpha',
                                        return_value=(6.0, 14.0)):
            played = replay.replay(points, None)
        self.assertFalse(replay.judge('2026-09-11', ROAD, played)['consistent'])
        self.assertTrue(replay.judge('2026-09-11', ROAD,
                                     replay.replay(points, None))['consistent'])

    def test_the_control_judges_rows_of_the_method_in_force_only(self):
        tampered = self.record('2026-09-12', TAMPERED)
        self.assertFalse(tampered['control'])
        older = self.copy_db(("UPDATE gps_daily_aggregates SET method_version = ? "
                              "WHERE wialon_id = ?", (PREVIOUS_METHOD_VERSION, CLEAN)))
        record = self.record('2026-09-10', CLEAN, db=older)
        self.assertIsNone(record['control'])
        self.assertIn('the row was written by %s' % PREVIOUS_METHOD_VERSION,
                      replay._control_word(record))


# --- часть 1: набор В-5 -----------------------------------------------------------

class V5(World):

    def test_two_rows_one_by_name_one_by_the_total(self):
        path = self.v5('1,S,2026-09-16,МТЗ-80Х 80 613 EA,Тест,APP-1,Дефоляция,1.920',
                       '2,П,2026-09-05,New Holland 7060 80 156 CA,Тест,APP-2,'
                       'Шудгорлаш,4.756')
        code, out, err = run_main('--db', self.db, '--dir', self.folder, '--v5', path)
        self.assertEqual(code, 0, out + err)
        out.encode('ascii')                     # a Cyrillic group cell too
        self.assertIn('V5EDGE n=1 unit=%d day=2026-09-16 a7=1.920 edge=1.920 '
                      'control=ok' % V5A, out.splitlines())
        # «CA» латиницей в наборе, «СА» кириллицей в справочнике; два объекта
        # одного имени, гектары набора -- у второго
        self.assertIn('V5EDGE n=2 unit=%d day=2026-09-05 a7=4.756 edge=4.500 '
                      'control=ok' % V5B2, out.splitlines())
        self.assertIn('PART 1 (V-5 rows resolved and reproduced; hectares are not '
                      'judged here): PASS', out)
        self.assertIn('core ', out)                          # evidence is printed

    def test_a_tie_the_total_does_not_break_is_unresolved(self):
        path = self.v5('2,P,2026-09-05,New Holland 7060 80 156 CA,Тест,APP-2,'
                       'Шудгорлаш,3.000')
        code, out, _err = run_main('--db', self.db, '--dir', self.folder, '--v5', path)
        self.assertEqual(code, 3, out)
        self.assertIn('UNRESOLVED: 2 unit(s) carry this name, 0 of them with the '
                      'csv hectares', out)
        self.assertNotIn('matched by hectares only', out)
        self.assertIn('candidate unit %d stored 1.9201 ha' % V5B1, out)
        self.assertIn('candidate unit %d stored 4.7562 ha' % V5B2, out)
        self.assertIn('V5EDGE n=2 unit=- day=2026-09-05 a7=- edge=- '
                      'control=UNRESOLVED', out.splitlines())
        self.assertIn('PART 1 (V-5 rows resolved and reproduced; hectares are not '
                      'judged here): NOT CHECKED', out)

    def test_an_unknown_name_with_one_unit_by_hectares_is_replayed(self):
        """The book and the directory spell the machine differently: the day's
        hectares point at one unit, its numbers are printed, the row stays
        UNRESOLVED until the name is confirmed."""
        path = self.v5('1,S,2026-09-16,МТЗ-80Х 80 999 EA,Тест,APP-1,Дефоляция,1.920')
        code, out, _err = run_main('--db', self.db, '--dir', self.folder, '--v5', path)
        self.assertEqual(code, 3, out)
        self.assertIn('UNRESOLVED: no unit published on that day carries this name',
                      out)
        self.assertIn('candidate unit %d stored 1.9201 ha  MTZ-80H - 80 613 EA' % V5A,
                      out)
        self.assertIn('  matched by hectares only -- confirm the machine: unit %d '
                      'MTZ-80H - 80 613 EA' % V5A, out.splitlines())
        self.assertIn('control (A7 recomputed == stored rows: area, alpha, '
                      'spacing): ok', out)
        self.assertIn('V5EDGE n=1 unit=%d day=2026-09-16 a7=1.920 edge=1.920 '
                      'control=UNRESOLVED' % V5A, out.splitlines())
        self.assertIn('judged here): NOT CHECKED', out)

    def test_two_units_with_the_csv_hectares_give_no_numbers(self):
        twin = self.copy_db(
            ("INSERT INTO gps_daily_aggregates (work_date, wialon_id, method_version, "
             "computed_at) VALUES ('2026-09-05', 530, ?, '2026-10-09 01:00:00')",
             (METHOD_VERSION,)),
            ("INSERT INTO gps_work_polygons (work_date, wialon_id, site_number, "
             "area_ha, minutes, polygon_geojson) VALUES ('2026-09-05', 530, 1, "
             "4.7562, 10, '{}')", ()))
        path = self.v5('2,P,2026-09-05,Беларус 80 999 EA,Тест,APP-2,Шудгорлаш,4.756')
        with unittest.mock.patch.object(replay, 'replay',
                                        side_effect=AssertionError('replayed')):
            code, out, _err = run_main('--db', twin, '--dir', self.folder, '--v5',
                                       path)
        self.assertEqual(code, 3, out)
        self.assertIn('candidate unit %d stored 4.7562 ha' % V5B2, out)
        self.assertIn('candidate unit 530 stored 4.7562 ha', out)
        self.assertNotIn('matched by hectares only', out)
        self.assertIn('V5EDGE n=2 unit=- day=2026-09-05 a7=- edge=- '
                      'control=UNRESOLVED', out.splitlines())

    def test_a_control_mismatch_makes_the_part_invalid(self):
        tampered = self.copy_db(("UPDATE gps_work_polygons SET area_ha = 1.9301 "
                                 "WHERE wialon_id = ?", (V5A,)))
        path = self.v5('1,S,2026-09-16,МТЗ-80Х 80 613 EA,Тест,APP-1,Дефоляция,1.930')
        code, out, _err = run_main('--db', tampered, '--dir', self.folder, '--v5', path)
        self.assertEqual(code, 3, out)
        self.assertIn('V5EDGE n=1 unit=%d day=2026-09-16 a7=1.920 edge=1.920 '
                      'control=MISMATCH' % V5A, out.splitlines())
        self.assertIn('NOTE: A7 recomputed 1.9201 ha differs from the csv '
                      'gps_ha_a7 1.930', out)
        self.assertIn('judged here): RUN INVALID', out)

    def test_points_gone_or_another_version_are_not_checked(self):
        path = self.v5('3,O,2026-09-20,Т-28 01 020 AA,Тест,APP-3,Чизел,0.000')
        code, out, _err = run_main('--db', self.db, '--dir', self.folder, '--v5', path)
        self.assertEqual(code, 3, out)
        self.assertIn('V5EDGE n=3 unit=%d day=2026-09-20 a7=- edge=- '
                      'control=UNRESOLVED' % NO_POINTS, out.splitlines())
        older = self.copy_db(("UPDATE gps_daily_aggregates SET method_version = ? "
                              "WHERE wialon_id = ?", (PREVIOUS_METHOD_VERSION, V5A)))
        path = self.v5('1,S,2026-09-16,МТЗ-80Х 80 613 EA,Тест,APP-1,Дефоляция,1.920')
        code, out, _err = run_main('--db', older, '--dir', self.folder, '--v5', path)
        self.assertEqual(code, 3, out)
        self.assertIn('V5EDGE n=1 unit=%d day=2026-09-16 a7=1.920 edge=1.920 '
                      'control=UNRESOLVED' % V5A, out.splitlines())
        self.assertIn('not checked: the row was written by %s' % PREVIOUS_METHOD_VERSION,
                      out)

    def test_a_file_without_a_column_is_bad_input(self):
        path = self.write('v5.csv', 'n,day,machine,gps_ha_a7\n1,2026-09-16,X,1.0\n')
        with unittest.mock.patch.object(replay, 'replay',
                                        side_effect=AssertionError('ran')):
            code, _out, err = run_main('--db', self.db, '--dir', self.folder,
                                       '--v5', path)
        self.assertEqual(code, 2)
        self.assertIn('has no column(s) group, org, application, work_type', err)

    def test_the_real_set_loads(self):
        rows = replay.load_v5(V5_SET)
        self.assertGreaterEqual(len(rows), 12)
        self.assertEqual(len({row['n'] for row in rows}), len(rows))
        self.assertEqual(rows[0]['machine'], 'МТЗ-80Х 80 613 EA')
        self.assertEqual(rows[0]['gps_ha_a7'], 1.759)


# --- часть 2: сутки по просьбе и условие 1273 ----------------------------------------

class OwnerDay(World):

    def run_owner(self, **patches):
        with contextlib.ExitStack() as stack:
            for name, value in patches.items():
                stack.enter_context(unittest.mock.patch.object(replay, name, value))
            return run_main('--db', self.db, '--dir', self.folder,
                            '--day', '2026-10-01:%d' % OWNER)

    def test_the_rule_closer_to_the_owner_passes(self):
        code, out, _err = self.run_owner(OWNER_HA=4.5)
        self.assertIn('CONDITION 2026-10-01:1273 (owner\'s hand measurement 4.500 ha): '
                      'PASS -- rule 4.5002 ha inside the largest A7 site (A7 4.7562 '
                      'ha)', out)
        # 7,90 по KML -- не эти сутки: глазу владельца, не отказ
        self.assertIn('REPRODUCTION 2026-10-01:1273: OWNER CHECK', out)
        self.assertEqual(code, 3, out)
        code, out, _err = self.run_owner(OWNER_HA=4.5, PREDICTED_HA=4.50)
        self.assertIn('REPRODUCTION 2026-10-01:1273: PASS', out)
        self.assertEqual(code, 0, out)

    def test_the_rule_farther_from_the_owner_fails(self):
        code, out, _err = self.run_owner(OWNER_HA=4.8, PREDICTED_HA=4.50)
        self.assertIn('owner\'s hand measurement 4.800 ha): FAIL', out)
        self.assertIn('NOT <', out)
        self.assertEqual(code, 4, out)

    def test_closer_is_not_enough_below_the_floor(self):
        with unittest.mock.patch.object(replay, 'OWNER_HA', 5.0):
            self.assertEqual(replay.judge_owner(10.0, 4.6)[0], replay.PASS)
            verdict, note = replay.judge_owner(10.0, 2.0)
            self.assertEqual(verdict, replay.FAIL)          # 3 < 5, but 2 < 4.5
            self.assertIn('BROKEN', note)
            # strictly closer: the rule that changed nothing did not get closer
            self.assertEqual(replay.judge_owner(4.8, 4.8)[0], replay.FAIL)

    def test_the_owner_constants(self):
        self.assertEqual(replay.OWNER_DAY, ('2026-10-01', 1273))
        self.assertEqual(replay.OWNER_HA, 7.805)
        self.assertEqual((replay.PREDICTED_HA, replay.PREDICTED_TOLERANCE_HA,
                          replay.OWNER_FLOOR_SHARE), (7.90, 0.05, 0.9))

    def test_a_day_the_replay_does_not_reproduce_is_not_judged(self):
        tampered = self.copy_db(("UPDATE gps_work_polygons SET area_ha = 9.0 "
                                 "WHERE wialon_id = ?", (OWNER,)))
        with unittest.mock.patch.object(replay, 'OWNER_HA', 4.5):
            code, out, _err = run_main('--db', tampered, '--dir', self.folder,
                                       '--day', '2026-10-01:%d' % OWNER)
        self.assertEqual(code, 3, out)
        self.assertIn('4.500 ha): RUN INVALID -- the replay does not reproduce', out)
        self.assertNotIn('REPRODUCTION', out)

    def test_an_owner_day_without_points_is_not_checked(self):
        with unittest.mock.patch.object(replay, 'OWNER_DAY', ('2026-09-20', NO_POINTS)):
            code, out, _err = run_main('--db', self.db, '--dir', self.folder,
                                       '--day', '2026-09-20:%d' % NO_POINTS)
        self.assertEqual(code, 3, out)
        self.assertIn('NOT CHECKED -- the day is not published, has no A7 site or '
                      'its points are gone', out)

    def test_other_days_are_printed_without_a_verdict(self):
        code, out, _err = run_main('--db', self.db, '--dir', self.folder,
                                   '--day', '2026-09-11:%d' % ROAD)
        self.assertEqual(code, 0, out)
        self.assertIn('--- 2026-09-11 unit %d MTZ-82 - 80 502 AA' % ROAD, out)
        self.assertIn('site 1: 4.756 -> 4.500 ha, removed 0.256 ha (5.4%); core ', out)
        self.assertIn('control (A7 recomputed == stored rows: area, alpha, '
                      'spacing): ok', out)
        self.assertIn('(no verdict:', out)


# --- часть 3: наборы с ручными замерами ---------------------------------------------

def pair(unit, zone, a7=1.0, rule=0.9, points=50, day='2026-07-27'):
    return {'unit': unit, 'zone': zone, 'a7': a7, 'rule': rule, 'points': points,
            'day': day}


class Matcher(unittest.TestCase):
    """The 27.07 rule, word by word, on the cases the pre-registration names."""

    NAMES = {3207: '1508 Нурхон Бобохон', 3208: '1508 Нурхон Бобохон',
             7001: '8696-21 пахта 2026', 7002: '8696-2 пахта 2026',
             3304: '3304 Маруф', 3303: '3303 Маруф', 5650: '5650 Гарден',
             5651: '5650 Гарден Янги', 1580: '1580 Маликова'}

    def match(self, manual, entered):
        return replay.manual_matches(manual, entered, self.NAMES)

    def test_an_abbreviation_matches_and_the_duplicate_zone_entered_wins(self):
        [row] = self.match((('1508 Нурхон', 8.587),),
                           [pair(3464, 3208, a7=8.5, rule=8.4)])
        self.assertEqual(row['status'], replay.MATCHED)
        self.assertEqual([p['zone'] for p in row['pairs']], [3208])
        self.assertEqual((row['a7'], row['rule']), (8.5, 8.4))

    def test_a_suffix_must_be_whole(self):
        [row] = self.match((('8696-2', 2.249),), [pair(1, 7001)])
        self.assertEqual(row['status'], replay.UNMATCHED)
        [row] = self.match((('8696-2', 2.249),), [pair(1, 7001), pair(1, 7002)])
        self.assertEqual(row['status'], replay.MATCHED)
        self.assertEqual([p['zone'] for p in row['pairs']], [7002])

    def test_the_number_alone_is_not_the_name(self):
        """«1580 Маликова» does not fit «1580 Бозоров»: every word counts."""
        names = dict(self.NAMES)
        names[1581] = '1580 Бозоров'
        [row] = replay.manual_matches((('1580 Маликова', 5.711),),
                                      [pair(1, 1581)], names)
        self.assertEqual(row['status'], replay.UNMATCHED)

    def test_a_later_word_starts_the_contours_word(self):
        """«Гарден» fits «Гарденлар» (the start of the word), not «Богарден»."""
        names = dict(self.NAMES)
        names.update({5635: '5634 Богарден', 5636: '5634 Гарденлар'})
        [row] = replay.manual_matches((('5634 Гарден', 2.085),),
                                      [pair(1, 5635)], names)
        self.assertEqual(row['status'], replay.UNMATCHED)
        [row] = replay.manual_matches((('5634 Гарден', 2.085),),
                                      [pair(1, 5635), pair(1, 5636)], names)
        self.assertEqual(row['status'], replay.MATCHED)
        self.assertEqual([p['zone'] for p in row['pairs']], [5636])

    def test_the_plus_row_is_one_unit_on_both_contours(self):
        rows = self.match((('3304+3303', 2.936),),
                          [pair(9, 3304, a7=1.5, rule=1.4),
                           pair(9, 3303, a7=1.4, rule=1.3), pair(8, 3303)])
        self.assertEqual(rows[0]['status'], replay.MATCHED)
        self.assertEqual(sorted(p['zone'] for p in rows[0]['pairs']), [3303, 3304])
        self.assertAlmostEqual(rows[0]['a7'], 2.9)
        self.assertAlmostEqual(rows[0]['rule'], 2.7)
        # one contour only -- the work was not done on both
        [row] = self.match((('3304+3303', 2.936),), [pair(9, 3304), pair(8, 3303)])
        self.assertEqual(row['status'], replay.UNMATCHED)
        # two units on both -- whose work it was is not known
        [row] = self.match((('3304+3303', 2.936),),
                           [pair(9, 3304), pair(9, 3303), pair(8, 3304),
                            pair(8, 3303)])
        self.assertEqual(row['status'], replay.AMBIGUOUS)

    def test_an_ambiguous_row_is_left_out_of_the_condition(self):
        rows = self.match((('5650 Гарден', 0.602), ('1580 Маликова', 5.711)),
                          [pair(1, 5650), pair(1, 5651), pair(2, 1580)])
        self.assertEqual([row['status'] for row in rows],
                         [replay.AMBIGUOUS, replay.MATCHED])
        self.assertEqual(len(rows[0]['pairs']), 2)          # the candidates
        with unittest.mock.patch.object(replay, 'MIN_MATCHED_0727', 2):
            out = []
            self.assertEqual(replay.condition_0727(rows, out.append),
                             replay.NOT_CHECKED)
        self.assertIn('1 of 2 rows matched', '\n'.join(out))


def work(name, manual, a7, rule):
    return {'name': name, 'manual': manual, 'status': replay.MATCHED, 'pairs': [],
            'a7': a7, 'rule': rule}


class Condition0727(unittest.TestCase):

    def judge(self, rows, minimum=2):
        out = []
        with unittest.mock.patch.object(replay, 'MIN_MATCHED_0727', minimum):
            verdict = replay.condition_0727(rows, out.append)
        return verdict, '\n'.join(out)

    def test_closer_works_pass(self):
        verdict, out = self.judge([work('a', 4.5, 4.756, 4.500),
                                   work('b', 2.0, 2.1, 2.0)])
        self.assertEqual(verdict, replay.PASS, out)

    def test_a_work_leaving_the_green_band_fails(self):
        # A7 0.3 ha over: inside max(10%, 0.3); the rule 0.556 under: outside
        verdict, out = self.judge([work('a', 5.056, 4.756, 4.500),
                                   work('b', 2.0, 2.0, 2.0)])
        self.assertEqual(verdict, replay.FAIL, out)
        self.assertIn('out of the green band under the rule: a manual 5.056', out)
        # the same undercount from a work already outside the band under A7;
        # the second work keeps the rule's median below A7's
        verdict, out = self.judge([work('a', 5.4, 4.756, 4.500),
                                   work('b', 2.0, 2.6, 2.0)])
        self.assertEqual(verdict, replay.PASS, out)

    def test_exactly_the_band_is_inside_it(self):
        """B-2 says «not more than»: 0.3 ha exactly is green (gps.tolerance)."""
        others = [work('b', 2.0, 2.4, 2.0), work('c', 2.0, 2.4, 2.0)]
        verdict, out = self.judge([work('a', 2.0, 2.0, 1.7)] + others)
        self.assertEqual(verdict, replay.PASS, out)
        verdict, out = self.judge([work('a', 2.0, 2.0, 1.69)] + others)
        self.assertEqual(verdict, replay.FAIL, out)
        self.assertIn('1 work(s) leave the green band', out)

    def test_a_larger_median_deviation_fails(self):
        verdict, out = self.judge([work('a', 4.756, 4.756, 4.500),
                                   work('b', 2.0, 2.0, 1.9)])
        self.assertEqual(verdict, replay.FAIL, out)
        self.assertIn('the median |deviation| grows: A7 0.00% -> rule', out)

    def test_fewer_matched_rows_than_the_minimum_are_not_checked(self):
        rows = [work('a', 5.056, 4.756, 4.500)] + [
            dict(work('x%d' % i, 1.0, 1.0, 1.0), status=replay.UNMATCHED)
            for i in range(16)]
        verdict, out = self.judge(rows, minimum=10)
        self.assertEqual(verdict, replay.NOT_CHECKED, out)
        self.assertIn('1 of 17 rows matched, at least 10 are needed', out)
        # the fall out of the band is printed even so
        self.assertIn('out of the green band under the rule: a', out)

    def test_the_manual_table_is_the_roadmap_table(self):
        """MANUAL_0727 is section 2.2 of the roadmap; the document wins."""
        with open(ROADMAP, encoding='utf-8') as handle:
            text = handle.read()
        start = text.index('| Контур | Вид | Шаг, м | Ручной, га |')
        end = text.index('Итог по 17 строкам', start)
        rows = []
        for line in text[start:end].splitlines()[2:]:
            cells = [cell.strip() for cell in line.lstrip('> ').split('|')]
            if len(cells) > 4 and cells[1]:
                rows.append((cells[1], float(cells[4].replace(',', '.'))))
        self.assertEqual(len(rows), 17)
        self.assertEqual(tuple(rows), replay.MANUAL_0727)
        self.assertEqual(replay.MIN_MATCHED_0727, 10)


class Sets(unittest.TestCase):
    """Part 3 through main(): the files as the probes write them."""

    def setUp(self):
        self.folder = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.folder, True)
        # два поля с дорогой вдоль края -- зоны с запасом 30 м берут дорогу внутрь
        self.zones = self.write_zones({
            '9101': ('1580 Маликова Ферма', (-30, -30, 180, 330)),
            '9102': ('1508 Нурхон Бобохон', (970, -30, 1180, 330)),
            '9103': ('1508 Нурхон Бобохон', (5000, 5000, 5200, 5300)),
            '9104': ('5650 Гарден', (8000, 8000, 8100, 8100))})
        patch = unittest.mock.patch.object(replay, 'WORK_DAYS_1208',
                                           ((7, 'MTZ test', '2026-08-01'),))
        patch.start()
        self.addCleanup(patch.stop)

    def write_zones(self, zones):
        raw = {}
        for zone, (title, (w, s, e, n)) in zones.items():
            corners = [(w, s), (e, s), (e, n), (w, n)]
            raw[zone] = {'type': 2, 'name': title,
                         'points': [{'x': lon, 'y': lat} for lon, lat in
                                    (xy_to_lonlat(x, y) for x, y in corners)]}
        path = os.path.join(self.folder, 'wialon_zones.json')
        with open(path, 'w', encoding='utf-8') as handle:
            json.dump(raw, handle, ensure_ascii=False)
        return path

    def write_tracks(self, name, days):
        path = os.path.join(self.folder, name)
        with open(path, 'w', encoding='utf-8-sig', newline='') as handle:
            handle.write('unit_id;date;time;lat;lon;speed;course;sats\n')
            for unit, day, track in days:
                for t, lon, lat, speed in track:
                    t = int(t) + 6 * 3600
                    handle.write('%d;%s;%02d:%02d:%02d;%.7f;%.7f;%.1f;0;10\n'
                                 % (unit, day, t // 3600, t % 3600 // 60, t % 60,
                                    lat, lon, speed))
        return path

    def two_fields(self):
        first = field_with_road()
        second = shifted(field_with_road(), 1000.0)
        offset = first[-1][0] + 1800.0
        return first + [(t + offset, lon, lat, v) for t, lon, lat, v in second]

    def run_sets(self, manual, *tracks, minimum=2):
        argv = []
        for path in tracks:
            argv += ['--tracks', path]
        with unittest.mock.patch.object(replay, 'MANUAL_0727', manual), \
                unittest.mock.patch.object(replay, 'MIN_MATCHED_0727', minimum):
            return run_main(*argv, '--zones', self.zones)

    def both_sets(self):
        early = self.write_tracks('verify_tracks.csv',
                                  [(11, '2026-07-27', self.two_fields())])
        late = self.write_tracks('verify2_tracks.csv', [(7, '2026-08-01', field())])
        return early, late

    def test_the_rule_closer_to_the_hand_passes(self):
        early, late = self.both_sets()
        code, out, _err = self.run_sets((('1580 Маликова', 4.5), ('1508 Нурхон', 4.5),
                                         ('8693', 3.637), ('5650 Гарден', 0.602)),
                                        early, late)
        self.assertEqual(code, 3, out)                  # 12.08 is the owner's
        # the zone holds the field and the glued road but not the road's tip:
        # 4.741 of the 4.756 ha site lie inside it
        self.assertRegex(out, r'unit 11\s+zone 9101\s+pts\s+\d+\s+A7 4\.741 -> rule '
                              r'4\.500 ha\s+manual 4\.500 \(1580 Malikova\)\s+'
                              r'1580 Malikova Ferma')
        self.assertRegex(out, r'1508 Nurhon\s+4\.500\s+matched: unit 11 zone\(s\) '
                              r'9102 -- A7 4\.741 \(\+5\.4%\), rule 4\.500 \(\+0\.0%\)')
        # 1508: two zones of that name, only 9102 entered -- matched to it
        self.assertRegex(out, r'8693\s+3\.637  UNMATCHED: no entered contour fits '
                              r'the name; no zone of the directory fits it either')
        self.assertRegex(out, r'5650 Garden\s+0\.602  UNMATCHED: no entered contour '
                              r'fits the name; in the directory, not entered: 9104 '
                              r'\(5650 Garden\)')
        self.assertIn('matched 2 of 4 rows', out)
        self.assertIn('CONDITION 27.07: PASS', out)
        self.assertIn('12.08 SET: OWNER CHECK -- all 1 works read', out)
        out.encode('ascii')                     # zone names are transliterated
        self.assertRegex(out, r'12\.08 unit 7\s+day 2026-08-01 MTZ test\s+points\s+'
                              r'\d+: A7 4\.500 -> rule 4\.500 ha')

    def test_a_work_falling_out_of_the_band_fails(self):
        early, late = self.both_sets()
        code, out, _err = self.run_sets((('1580 Маликова', 5.056),
                                         ('1508 Нурхон', 4.5)), early, late)
        self.assertEqual(code, 4, out)
        self.assertIn('CONDITION 27.07: FAIL -- 1 work(s) leave the green band', out)

    def test_too_few_rows_matched_is_not_checked(self):
        early, late = self.both_sets()
        code, out, _err = self.run_sets((('1580 Маликова', 4.5), ('1508 Нурхон', 4.5)),
                                        early, late, minimum=10)
        self.assertEqual(code, 3, out)
        self.assertIn('CONDITION 27.07: NOT CHECKED -- 2 of 2 rows matched, at '
                      'least 10 are needed', out)

    def test_missing_sets_are_not_checked(self):
        other = self.write_tracks('other.csv', [(11, '2026-07-26', field())])
        code, out, _err = self.run_sets((('1580 Маликова', 4.5),), other)
        self.assertEqual(code, 3, out)
        self.assertIn('the 27.07 set (7 tractors, 17440 points, tracks of 2026-07-27) '
                      'was not read', out)
        self.assertIn('CONDITION 27.07: NOT CHECKED', out)
        self.assertIn('12.08 SET: NOT CHECKED -- 1 of the 1 works were not read: '
                      '7 2026-08-01', out)
        self.assertRegex(out, r'other unit 11\s+day 2026-07-26')

    def test_tracks_without_zones_and_a_broken_file_are_refused(self):
        early, _late = self.both_sets()
        self.assertEqual(run_main('--tracks', early)[0], 2)
        broken = os.path.join(self.folder, 'broken.csv')
        with open(broken, 'w', encoding='utf-8') as handle:
            handle.write('unit_id;date;time;lat;lon\n1;2026-07-27;08:00:00;39.9;64.4\n')
        code, _out, err = run_main('--tracks', broken, '--zones', self.zones)
        self.assertEqual(code, 2)
        self.assertIn('broken.csv has no column(s) speed', err)


# --- часть 4: production ------------------------------------------------------------

class Production(World):

    def test_the_month_with_a_tampered_row_is_invalid_not_failed(self):
        code, out, _err = self.period('2026-09-01', '2026-09-30')
        self.assertEqual(code, 3, out)
        self.assertIn('published machine-days of the period: 10; excluded objects: 1 '
                      '(counted as a number, not replayed); points gone from disk: 1; '
                      'replayed: 8', out)
        self.assertIn('control, A7 recomputed == stored rows (rows of %s only): 7 of '
                      '8 same, 1 different, 0 not checked' % METHOD_VERSION, out)
        self.assertIn('  control mismatch 2026-09-12 %d' % TAMPERED, out)
        self.assertIn('INVARIANTS (the rule never adds hectares, never leaves the A7 '
                      'sites by more than 1 m2, never deletes an A7 site): RUN '
                      'INVALID', out)
        for unit in (EXCLUDED, LATE, OWNER):
            self.assertNotIn('unit %d' % unit, out)

    def test_the_report_of_the_period(self):
        code, out, _err = self.period('2026-09-01', '2026-09-30', db=self.untampered())
        self.assertEqual(code, 3, out)                   # the KML is the owner's
        self.assertIn('INVARIANTS (the rule never adds hectares, never leaves the A7 '
                      'sites by more than 1 m2, never deletes an A7 site): PASS -- no '
                      'invariant broken on 7 machine-days', out)
        self.assertIn('  machine-days changed: 3 of 7', out)
        self.assertIn('  plan-fact of the period, counted objects: A7 22.05 ha -> '
                      'rule 21.06 ha (-0.99 ha, -4.51%)', out)
        self.assertIn('  A7 sites: 8; trimmed: 3 (removed 0.99 ha); without a core, '
                      'left as they are: 1 (0.39 ha); put back whole by the guard: 1 '
                      '(0.33 ha)', out)
        self.assertIn('  removed share per machine-day: 0 4, <1% 0, 1-5% 0, 5-10% 2, '
                      '10-25% 1, >25% 0; without an A7 site: 0', out)
        self.assertRegex(out, r'seconds per machine-day: A7 median \d+\.\d\d max '
                              r'\d+\.\d\d; rule median \d+\.\d\d max \d+\.\d\d')
        self.assertIn('rows by method version, counted objects: %s 7' % METHOD_VERSION,
                      out)

    def test_a_clean_period_passes_with_exit_0(self):
        code, out, _err = self.period('2026-09-10', '2026-09-10')
        self.assertEqual(code, 0, out)
        self.assertIn('machine-days changed: 0 of 1', out)

    def test_another_method_version_is_not_checked_but_counted(self):
        older = self.copy_db(("UPDATE gps_daily_aggregates SET method_version = ? "
                              "WHERE wialon_id = ?", (PREVIOUS_METHOD_VERSION, CLEAN)))
        code, out, _err = self.period('2026-09-10', '2026-09-11', db=older)
        self.assertIn('1 of 1 same, 0 different, 1 not checked (another method '
                      'version)', out)
        self.assertIn('rows by method version, counted objects: %s 1, %s 1'
                      % (PREVIOUS_METHOD_VERSION, METHOD_VERSION), out)
        self.assertEqual(code, 3, out)

    def provoke(self, change):
        """ROAD's day with `trim` returning change(shape, today) instead."""
        real = edge.trim

        def broken(points_xy, today, alpha, min_area_ha=0.3):
            shape, info = real(points_xy, today, alpha, min_area_ha)
            return change(shape, today), info
        with unittest.mock.patch.object(edge, 'trim', broken):
            return self.period('2026-09-11', '2026-09-11')

    def test_a_rule_shape_outside_the_a7_sites_fails(self):
        def outside(shape, today):
            x0, y0, x1, y1 = today.bounds
            return unary_union([shape, box(x1 + 500, y0, x1 + 600, y0 + 100)])
        code, out, _err = self.provoke(outside)
        self.assertEqual(code, 4, out)
        self.assertIn('rule site 2 lies 10000.0 m2 outside the A7 sites', out)
        self.assertIn('more hectares than A7', out)
        self.assertIn('never deletes an A7 site): FAIL -- 1 of 1 machine-days break '
                      'an invariant', out)

    def test_a_bigger_rule_shape_fails(self):
        code, out, _err = self.provoke(lambda shape, today: shape.buffer(3.0))
        self.assertEqual(code, 4, out)
        self.assertIn('more hectares than A7', out)
        self.assertRegex(out, r'rule site 1 lies \d+\.\d m2 outside the A7 sites')

    def test_a_deleted_site_fails(self):
        code, out, _err = self.provoke(
            lambda shape, today: shape.difference(today.buffer(1.0)))
        self.assertEqual(code, 4, out)
        self.assertIn('A7 site 1 (4.7562 ha) deleted: 0.0000 ha of it left in the '
                      'rule sites', out)

    def test_the_kml_carries_the_numbers_of_every_changed_site(self):
        kml = os.path.join(self.scratch, 'edge.kml')
        code, out, _err = self.period('2026-09-01', '2026-09-30', '--kml', kml,
                                      db=self.untampered())
        self.assertEqual(code, 3, out)
        folders = ET.parse(kml).getroot().findall('.//k:Folder', NS)
        titles = [f.find('k:name', NS).text for f in folders]
        # the largest removal first, then the two ties by date; nothing by sha1
        self.assertEqual(len(folders), 3)
        self.assertTrue(titles[0].startswith('МТЗ-80.1 — 80 506 BA · 14.09.2026 · '
                                             'A7 3,871 га → правило / қоида 3,388 га'),
                        titles[0])

        def names(folder):
            return [p.find('k:name', NS).text for p in folder.findall('k:Placemark', NS)]
        labels = names(folders[0])
        self.assertIn('Участок / участка 1: 3,483 -> 3,000 га, убрано / олиб '
                      'ташланди 0,483 га (13,9 %), больше допуска В-2 / В-2 йўл '
                      'қўйилишидан ташқари', labels)
        self.assertIn('Участок / участка 2: 0,388 га, без изменений / ўзгаришсиз',
                      labels)
        road_folder = [f for f, t in zip(folders, titles) if '11.09.2026' in t][0]
        self.assertIn('Участок / участка 1: 4,756 -> 4,500 га, убрано / олиб '
                      'ташланди 0,256 га (5,4 %)', names(road_folder))
        self.assertFalse(any('В-2' in name for name in names(road_folder)))
        description = [p.find('k:description', NS).text
                       for p in folders[0].findall('k:Placemark', NS)
                       if p.find('k:description', NS) is not None][0]
        self.assertIn('Убрано 0,483 га (13,9 %); больше допуска В-2 (10 % или 0,3 га)',
                      description)
        self.assertTrue(any(name.startswith('Правило края поля / дала чети қоидаси: '
                                            'участок / участка 1 — 3,000 га')
                            for name in labels))
        self.assertTrue(any(name.startswith('Трек в работе') for name in labels))
        # the same line in the console, ASCII
        self.assertIn('    site 1: 3.483 -> 3.000 ha, removed 0.483 ha (13.9%) '
                      '[beyond B-2]', out)
        self.assertIn('    site 1: 4.756 -> 4.500 ha, removed 0.256 ha (5.4%)\n', out)
        out.encode('ascii')

    def test_the_sha1_pick_is_deterministic_and_skips_the_largest(self):
        def rec(day, unit, removed, changed=True):
            return {'day': day, 'unit': unit, 'a7_ha': 10.0,
                    'rule_ha': 10.0 - removed, 'changed': changed, 'sites': []}
        records = [rec('2026-09-%02d' % (i % 28 + 1), 100 + i, 0.01 * (i + 1))
                   for i in range(30)]
        records.append(rec('2026-09-02', 999, 50.0, changed=False))
        largest, rest = replay.pick_for_kml(list(reversed(records)))
        self.assertEqual([row['unit'] for row in largest], list(range(129, 119, -1)))
        expected = sorted(
            (hashlib.sha1(('%s:%d' % (row['day'], row['unit'])).encode('utf-8'))
             .hexdigest(), row['unit']) for row in records[:20])[:10]
        self.assertEqual([row['unit'] for row in rest], [unit for _key, unit in expected])
        self.assertEqual(replay.sha1_key('2026-09-11', 502),
                         hashlib.sha1(b'2026-09-11:502').hexdigest())
        self.assertNotIn(999, [row['unit'] for row in largest + rest])

    def test_progress_every_200_machine_days(self):
        lines = []
        with unittest.mock.patch.object(replay, 'PROGRESS_EVERY', 2):
            con = replay.open_readonly(self.db)
            try:
                replay.part_period(con, self.folder, None, '2026-09-01', '2026-09-12',
                                   None, [].append, lines.append)
            finally:
                con.close()
        self.assertEqual(lines, ['  2 of 5 machine-days', '  4 of 5 machine-days'])
        self.assertEqual(replay.PROGRESS_EVERY, 200)


# --- часть 5: ответы операторов -----------------------------------------------------

class Labels(World):

    def test_a_work_cut_beyond_b2_goes_to_the_owner(self):
        code, out, _err = run_main('--db', self.db, '--dir', self.folder, '--labels')
        self.assertEqual(code, 3, out)
        self.assertIn('labelled sites: 3 in 2 machine-day(s); compared 3; not '
                      'compared: none', out)
        # measured inside the STORED polygons (six decimals), so a few square
        # metres short of the sites themselves: 4.756 + 3.483 and 4.500 + 3.000
        found = re.search(r'rabota   sites 2: A7 (\d+\.\d+) ha -> rule (\d+\.\d+) ha',
                          out)
        self.assertAlmostEqual(float(found.group(1)), 8.239, delta=0.005)
        self.assertAlmostEqual(float(found.group(2)), 7.500, delta=0.005)
        found = re.search(r'rabota cut beyond B-2: 2026-09-14 unit 506 site 1: A7 '
                          r'(\d+\.\d+) -> rule (\d+\.\d+) ha, removed \d+\.\d+ ha '
                          r'\(13\.\d%\)  MTZ-80\.1 - 80 506 BA', out)
        self.assertAlmostEqual(float(found.group(1)), 3.483, delta=0.005)
        self.assertAlmostEqual(float(found.group(2)), 3.000, delta=0.005)
        self.assertNotIn('unit 502 site 1: A7', out)          # 5.4%: inside B-2
        self.assertIn('share of proezd hectares the rule removes: 0.0%', out)
        self.assertIn('PART 5 (labels, no FAIL here): OWNER CHECK -- 1 rabota site(s) '
                      'cut beyond B-2', out)

    def test_inside_the_band_the_part_passes(self):
        out = []
        con = replay.open_readonly(self.db)
        try:
            verdicts = replay.part_labels(con, self.folder, None, '2026-09-11',
                                          '2026-09-11', out.append)
        finally:
            con.close()
        self.assertEqual(verdicts, [(replay.LABEL_LABELS, replay.PASS)], out)

    def test_no_labels_is_not_checked_and_a_mismatch_is_invalid(self):
        out = []
        con = replay.open_readonly(self.db)
        try:
            self.assertEqual(replay.part_labels(con, self.folder, None, '2026-09-01',
                                                '2026-09-10', out.append),
                             [(replay.LABEL_LABELS, replay.NOT_CHECKED)])
        finally:
            con.close()
        tampered = self.copy_db(("UPDATE gps_work_polygons SET area_ha = 9.0 "
                                 "WHERE wialon_id = ? AND site_number = 1", (ROAD,)))
        con = replay.open_readonly(tampered)
        try:
            self.assertEqual(replay.part_labels(con, self.folder, None, '2026-09-11',
                                                '2026-09-11', [].append),
                             [(replay.LABEL_LABELS, replay.RUN_INVALID)])
        finally:
            con.close()

    def test_a_stored_polygon_keeps_its_hole_and_parts(self):
        ring = [(64.40, 39.99), (64.41, 39.99), (64.41, 40.0), (64.40, 40.0),
                (64.40, 39.99)]
        hole = [(64.402, 39.992), (64.404, 39.992), (64.404, 39.994),
                (64.402, 39.994), (64.402, 39.992)]
        solid = replay.utm_of_geojson(json.dumps({'type': 'Polygon',
                                                  'coordinates': [ring]}))
        holed = replay.utm_of_geojson(json.dumps({'type': 'Polygon',
                                                  'coordinates': [ring, hole]}))
        self.assertGreater(solid.area - holed.area, 30000.0)
        moved = [(lon + 0.05, lat) for lon, lat in ring]
        both = replay.utm_of_geojson(json.dumps({
            'type': 'MultiPolygon', 'coordinates': [[ring], [moved]]}))
        self.assertAlmostEqual(both.area, 2 * solid.area, delta=solid.area * 0.01)
        self.assertIsNone(replay.utm_of_geojson('{"type": "Point", '
                                                '"coordinates": [64.4, 39.9]}'))
        self.assertIsNone(replay.utm_of_geojson('not json'))


# --- запуск, отказы, «ничего не пишет» ------------------------------------------------

class Main(World):

    def test_bad_input_is_refused_before_anything_is_computed(self):
        missing = os.path.join(self.scratch, 'no.db')
        cases = [
            (),
            ('--db', missing, '--dir', self.folder, '--since', '2026-09-01'),
            ('--db', self.db, '--dir', os.path.join(self.scratch, 'nowhere'),
             '--labels'),
            ('--db', self.db, '--dir', self.folder, '--since', '2026-09-01',
             '--kml', os.path.join(self.scratch, 'no', 'x.kml')),
            ('--db', self.db, '--dir', self.folder, '--kml',
             os.path.join(self.scratch, 'x.kml')),
            ('--db', self.db, '--dir', self.folder, '--day', '2026-10-01'),
            ('--db', self.db, '--dir', self.folder, '--since', '2026-13-01'),
            ('--db', self.db, '--dir', self.folder, '--since', '2026-09-30',
             '--until', '2026-09-01'),
            # --until beside another part would be ignored without a word
            ('--db', self.db, '--dir', self.folder, '--day', '2026-09-11:%d' % ROAD,
             '--until', '2026-09-30'),
            ('--db', self.db, '--dir', self.folder, '--v5',
             os.path.join(self.scratch, 'no.csv')),
            ('--zones', os.path.join(self.scratch, 'zones.json')),
        ]
        with unittest.mock.patch.object(replay, 'replay',
                                        side_effect=AssertionError('ran')), \
                unittest.mock.patch.object(replay, 'load_contours',
                                           side_effect=AssertionError('opened')):
            for argv in cases:
                code, out, err = run_main(*argv)
                self.assertEqual(code, 2, (argv, out, err))
                self.assertIn('ERROR', err, argv)
        self.assertFalse(os.path.exists(missing))

    def test_every_connection_is_read_only(self):
        """Digests cannot tell a connection that could write from one that can't."""
        real, seen = sqlite3.connect, []

        def watched(target, *args, **kwargs):
            seen.append((str(target), kwargs.get('uri')))
            return real(target, *args, **kwargs)
        v5 = self.v5('1,S,2026-09-16,МТЗ-80Х 80 613 EA,Тест,APP-1,Дефоляция,1.920')
        with unittest.mock.patch.object(sqlite3, 'connect', watched):
            code, out, _err = run_main('--db', self.db, '--dir', self.folder, '--v5',
                                       v5, '--day', '2026-09-11:%d' % ROAD, '--since',
                                       '2026-09-10', '--until', '2026-09-11',
                                       '--labels')
        self.assertEqual(code, 3, out)
        self.assertGreater(len(seen), 5)
        for target, uri in seen:
            self.assertTrue(target.startswith('file:') and target.endswith('?mode=ro')
                            and uri, target)

    def test_the_exit_code_carries_the_worst_verdict(self):
        self.assertEqual(replay.verdict_code([replay.PASS, replay.PASS]), 0)
        self.assertEqual(replay.verdict_code([replay.PASS, replay.FAIL,
                                              replay.RUN_INVALID]), 4)
        for other in (replay.NOT_CHECKED, replay.RUN_INVALID, replay.OWNER_CHECK):
            self.assertEqual(replay.verdict_code([replay.PASS, other]), 3, other)

    def test_nothing_is_written_by_a_run_of_every_part(self):
        kml = os.path.join(self.scratch, 'edge.kml')
        v5 = self.v5('1,S,2026-09-16,МТЗ-80Х 80 613 EA,Тест,APP-1,Дефоляция,1.920')
        points = sorted(os.listdir(self.folder))
        before = [digest(self.db)] + [digest(os.path.join(self.folder, name))
                                       for name in points]
        listing = (sorted(os.listdir(os.path.dirname(self.db))), points)
        with unittest.mock.patch.object(replay, 'OWNER_HA', 4.5):
            code, out, err = run_main('--db', self.db, '--dir', self.folder, '--v5',
                                      v5, '--day', '2026-10-01:%d' % OWNER, '--since',
                                      '2026-09-01', '--until', '2026-09-30', '--kml',
                                      kml, '--labels')
        self.assertEqual(code, 3, out + err)
        after = [digest(self.db)] + [digest(os.path.join(self.folder, name))
                                      for name in points]
        self.assertEqual(after, before)
        # [the point files run in WAL mode: SQLite reading one mode=ro leaves an
        # empty -wal and a -shm beside it -- the caveat of the docstring. Nothing
        # else may appear, and nothing may be written into a -wal.]
        for folder, names in zip((os.path.dirname(self.db), self.folder), listing):
            for name in sorted(set(os.listdir(folder)) - set(names)):
                base, _dash, kind = name.rpartition('-')
                self.assertIn(kind, ('wal', 'shm'), name)
                self.assertIn(base, names, name)
                if kind == 'wal':
                    self.assertEqual(os.path.getsize(os.path.join(folder, name)), 0)
        self.assertTrue(os.path.isfile(kml))
        self.assertIn('nothing was written to the database or the point files: they '
                      'were opened mode=ro; the only file written is the KML', out)
        for part in ('=== PART 1', '=== PART 2', '=== PART 4', '=== PART 5',
                     '=== SUMMARY ==='):
            self.assertIn(part, out)
        self.assertIn('A7: compute_day, method %s; rule: compute_day(edge_rule=True), '
                      'method %s+edge-core-2026-10-09' % (METHOD_VERSION,
                                                          METHOD_VERSION), out)
        out.encode('ascii')


class InvariantBounds(unittest.TestCase):
    """The three promises at their exact bounds, on boxes (metres)."""

    A7 = box(0, 0, 100, 100)                                   # 1 ha

    def test_a_rule_equal_to_a7_breaks_nothing(self):
        self.assertEqual(replay.invariant_violations([self.A7], [self.A7]), [])

    def test_one_square_metre_outside_is_noise_and_more_is_not(self):
        inside = box(0, 0, 100, 99)
        within = unary_union([inside, box(100, 50, 100.9, 51)])  # 0.9 m2 out
        beyond = unary_union([inside, box(100, 50, 101.5, 51)])  # 1.5 m2 out
        self.assertEqual(replay.invariant_violations([self.A7], [within]), [])
        self.assertEqual(replay.invariant_violations([self.A7], [beyond]),
                         ['rule site 1 lies 1.5 m2 outside the A7 sites'])

    def test_more_hectares_by_more_than_the_float_noise(self):
        bigger = box(0, 0, 100, 100.01)                        # +1 m2: 1e-4 ha
        found = replay.invariant_violations([self.A7], [bigger])
        self.assertIn('more hectares than A7: 1.0001 > 1.0000', found)
        same = box(0, 0, 100, 100.000001)                      # +1e-4 m2
        self.assertEqual(replay.invariant_violations([self.A7], [same]), [])

    def test_a_site_counts_as_changed_above_one_square_metre(self):
        """The KML marks and the «trimmed» count start at 1 m2, not at 100 m2."""
        self.assertTrue(replay.site_changed({'removed': 0.00011}))
        self.assertFalse(replay.site_changed({'removed': 0.00009}))
        self.assertFalse(replay.site_changed({'removed': -2e-15}))

    def test_a_site_left_below_the_floor_is_deleted(self):
        kept = box(0, 0, 30, 100)                              # 0.30 ha kept
        self.assertEqual(replay.invariant_violations([self.A7], [kept]), [])
        less = box(0, 0, 29.99, 100)
        self.assertEqual(replay.invariant_violations([self.A7], [less]),
                         ['A7 site 1 (1.0000 ha) deleted: 0.2999 ha of it left in '
                          'the rule sites'])
        self.assertEqual(replay.invariant_violations([self.A7], []),
                         ['A7 site 1 (1.0000 ha) deleted: 0.0000 ha of it left in '
                          'the rule sites'])


class Sources(unittest.TestCase):
    """What the tool copies or reuses is what its source says."""

    def test_the_search_key_is_the_screens(self):
        with open(os.path.join(REPO, 'gps_routes.py'), encoding='utf-8') as handle:
            tree = ast.parse(handle.read())
        found = {}
        for node in tree.body:
            if isinstance(node, ast.Assign):
                for target in node.targets:
                    if getattr(target, 'id', '') in ('SEARCH_LOOKALIKE_FROM',
                                                     'SEARCH_LOOKALIKE_TO',
                                                     'SEARCH_KEEP'):
                        found[target.id] = ast.literal_eval(node.value)
        self.assertEqual(found, {'SEARCH_LOOKALIKE_FROM': replay.SEARCH_LOOKALIKE_FROM,
                                 'SEARCH_LOOKALIKE_TO': replay.SEARCH_LOOKALIKE_TO,
                                 'SEARCH_KEEP': replay.SEARCH_KEEP})
        self.assertEqual(replay.search_key('80 156 СА'), replay.search_key('80156ca'))
        self.assertEqual(replay.tokens('8696-2'), ['86962'])

    def test_the_sets_are_the_a7_replays(self):
        self.assertIs(replay.WORK_DAYS_1208, alpha_replay.WORK_DAYS_1208)
        self.assertIs(replay.load_csv_days, alpha_replay.load_csv_days)

    def test_the_tool_imports_neither_flask_nor_the_application(self):
        code = ('import sys; sys.path.insert(0, %r); import tools.gps_edge_replay; '
                'print(sorted(m for m in ("flask", "flask_sqlalchemy", "app", '
                '"models") if m in sys.modules))' % REPO)
        done = subprocess.run([sys.executable, '-c', code], capture_output=True,
                              text=True, cwd=REPO, timeout=120)
        self.assertEqual(done.returncode, 0, done.stderr)
        self.assertEqual(done.stdout.strip(), '[]')


if __name__ == '__main__':
    unittest.main()
