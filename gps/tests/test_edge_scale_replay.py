# -*- coding: utf-8 -*-
"""tools/gps_edge_scale_replay.py -- «A7 / правило края поля, второй круг» на одних точках.

Окружение расчёта (геометрия): прогон на настоящем движке и настоящем
`gps.edge_scale.trim`. Здесь держится то, что делает прогон доказательством,
а не пересказом:
  * повтор пути движка даёт ровно участки `compute_day` -- и A7, и правила;
    чистое поле правило не трогает, дорогу у края срезает и раскладывает
    убранное на промежутки, полосы и остаток ниже порога, участок
    опрыскивателя оставляет и называет причину, три прохода рядом сохраняет
    как проходы, участок одной дороги удаляет целиком -- и инструмент
    называет именно это;
  * повтор, разошедшийся с движком (другая альфа, доказательства чужого
    участка), делает прогон недействительным, а не проваленным; контроль
    (A7 по точкам == строки базы) -- тоже; строку другой версии метода он не
    судит;
  * каждый инвариант, нарушенный нарочно подменённым `gps.edge_scale.trim`,
    даёт FAIL (форма снаружи, форма больше, форма не плоская, тронут участок,
    на котором правило выключено), а откат правила к форме A7 -- NOT CHECKED
    и в production, и в строке В-6;
  * набор В-6: строка находится по имени ключом экрана, ничья двух объектов
    одного имени решается суммой гектаров; строка V6EDGE ровно в формате;
  * New Holland 01.10: воспроизведение стенда -- в обе стороны по допуску;
  * ответы операторов: «проезд», который правило убрало, и «работа»,
    срезанная больше допуска В-2, -- владельцу;
  * KML: у изменённого участка -- было, стало, из чего убрано, отметки В-2 и
    «удалён целиком»; убранные куски и полосы-проходы -- своими слоями;
  * код выхода несёт вердикт; неверный ввод отказывается до расчёта;
  * ни база, ни файлы точек не меняются; Flask и `models` не импортируются;
  * причины «правило выключено» в KML -- те же строки, что в движке; узбекский
    текст KML -- кириллицей.

Запуск из корня репозитория окружением расчёта:
  python -m unittest gps.tests.test_edge_scale_replay
"""
import contextlib
import io
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

import shapely
from shapely.geometry import box
from shapely.ops import unary_union

import gps.edge_scale as edge_scale
import migrate_gps_daily_001 as migration
import tools.gps_edge_replay as round1
import tools.gps_edge_scale_replay as replay
from gps.area import METHOD_VERSION, PREVIOUS_METHOD_VERSION
from gps.daily import PASSAGE, WORK, compute_day, write_day
from gps.tests.test_area import shuttle_track
from gps.tests.test_edge import road, shifted
from gps.tests.test_edge_replay import digest, field, field_with_road, stamped
from gps.tests.test_edge_scale import retraced_road, sprayer
from gps_collector import storage
from tests.test_gps_units_inventory import DDL

REPO = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
ROADMAP = os.path.join(REPO, 'docs', 'GPS_PLAN_FAKT_VISION_ROADMAP.md')
V6_SET = os.path.join(REPO, 'docs', 'gps_v6_acceptance_set_2026-10-11.csv')

CLEAN, ROAD, TAMPERED, EXCLUDED, SPRAY, WIDE, PASSES, LABELS, ROADONLY = (
    601, 602, 603, 604, 605, 606, 607, 608, 609)
V6A, V6B1, V6B2, NO_POINTS, LATE, OWNER = 611, 612, 613, 620, 630, 1273
NS = {'k': 'http://www.opengis.net/kml/2.2'}


# --- синтетические сутки --------------------------------------------------------

def sprayer_and_lane():
    """Опрыскиватель (проходы через 18 м) и дорога в 15 м от края: шаг
    задаёт альфу, правило участок не трогает."""
    work = sprayer()
    lane, _ = road([(-200.0, -15.0), (400.0, -15.0), (-200.0, -13.0)],
                   start_time=work[-1][0] + 600.0)
    return work + lane


def sprayer_and_retraced_road():
    """Тот же опрыскиватель и дорога к заправке, пройденная восемь раз:
    медиана шага участка падает, верхняя четверть -- нет."""
    work = sprayer()
    return work + retraced_road(work[-1][0] + 600.0)


def loosener_strip():
    """Три прохода рыхлителя через 4,5 м за один заезд, 600 м: узкая полоса,
    но проходы рядом -- правило её оставляет."""
    return shuttle_track(9.0, 600.0, pass_spacing_m=4.5, point_step_m=20.0,
                         speed=7.0)


def road_only():
    """Дорога туда и обратно в 7 м, 750 м: участок 0,514 га, это дорога, и
    правило удаляет его целиком."""
    lane, _ = road([(-450.0, -8.0), (300.0, -8.0), (300.0, -15.0), (-450.0, -15.0)])
    return lane


def field_and_far_road():
    """Поле 100 x 200 м и в 500 м от него дорога туда и обратно в 7 м: два
    участка, второй правило удаляет."""
    work = field(100.0, 200.0)
    lane, _ = road([(0.0, 0.0), (600.0, 0.0), (600.0, 7.0), (0.0, 7.0)],
                   start_time=work[-1][0] + 600.0)
    return work + shifted(lane, 0.0, 500.0)


def run_main(*argv):
    with contextlib.redirect_stdout(io.StringIO()) as out, \
            contextlib.redirect_stderr(io.StringIO()) as err:
        code = replay.main([str(arg) for arg in argv])
    return code, out.getvalue(), err.getvalue()


# --- мир: база и файлы точек, общие для модуля ----------------------------------

WORLD = {}

V6_HEADER = 'n,group,day,machine,org,application,work_type,gps_ha_a7\n'


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
        machines = (('МТЗ-80Х', '80 261 EA'), ('New Holland 7060', '80 605 EA'),
                    ('Т-28', '01 020 AA'), ('New Holland 7060', '80 080 HA'),
                    ('МТЗ-82', '80 502 AA'), ('МТЗ-80.1', '80 506 BA'))
        for name, plate in machines:
            con.execute("INSERT INTO equipment (name, plate, category, "
                        "organization_id, is_active) VALUES (?, ?, 'mtz', 1, 1)",
                        (name, plate))
        # два объекта одной машины (смена трекера): одно имя на экране
        for title, unit, machine, skip in (
                ('МТЗ 261', V6A, 1, 0), ('NH 605 old', V6B1, 2, 0),
                ('NH 605 new', V6B2, 2, 0), ('Т-28 020', NO_POINTS, 3, 0),
                ('NH 080', OWNER, 4, 0), ('МТЗ 502', ROAD, 5, 0),
                ('МТЗ 506', LABELS, 6, 0), ('Чужой 604', EXCLUDED, None, 1)):
            con.execute("INSERT INTO vialon_mappings (vialon_name, wialon_id, "
                        "equipment_id, skip) VALUES (?, ?, ?, ?)",
                        (title, unit, machine, skip))
        con.commit()
        for day, unit, track in (
                ('2026-09-10', CLEAN, field()),
                ('2026-09-11', ROAD, field_with_road()),
                ('2026-09-12', TAMPERED, field()),
                ('2026-09-13', EXCLUDED, field_with_road()),
                ('2026-09-14', SPRAY, sprayer_and_lane()),
                ('2026-09-15', WIDE, sprayer_and_retraced_road()),
                ('2026-09-16', PASSES, loosener_strip()),
                ('2026-09-17', LABELS, field_and_far_road()),
                ('2026-09-18', ROADONLY, road_only()),
                ('2026-10-01', OWNER, field_with_road()),
                ('2026-10-03', V6A, field(100.0, 200.0)),
                ('2026-10-05', V6B1, field(100.0, 200.0)),
                ('2026-10-05', V6B2, field_with_road()),
                ('2026-10-12', LATE, field_with_road())):
            points = stamped(track, day)
            storage.write_points(folder, [(unit, t, lon, lat, speed, None, sats)
                                          for t, lon, lat, speed, sats in points])
            write_day(con, day, unit, compute_day(points), '2026-10-11 01:00:00')
        con.execute("UPDATE gps_work_polygons SET area_ha = area_ha + 1 "
                    "WHERE wialon_id = ?", (TAMPERED,))
        # опубликованная строка без точек на диске
        con.execute("INSERT INTO gps_daily_aggregates (work_date, wialon_id, "
                    "method_version, computed_at) VALUES ('2026-09-20', ?, ?, "
                    "'2026-09-21 01:00:00')", (NO_POINTS, METHOD_VERSION))
        for unit, site, label in ((LABELS, 1, WORK), (LABELS, 2, PASSAGE),
                                  (ROADONLY, 1, WORK)):
            con.execute("UPDATE gps_work_polygons SET operator_label = ?, "
                        "decided_at = '2026-10-11 10:00:00' WHERE wialon_id = ? "
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

    def v6(self, *rows, name='v6.csv'):
        return self.write(name, V6_HEADER + ''.join(row + '\n' for row in rows))

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


def patched_trim(change):
    """`gps.edge_scale.trim` whose (shape, evidence) pass through `change`."""
    real = edge_scale.trim

    def trim(points_xy, today, alpha, spacing, min_area_ha=0.3):
        shape, info = real(points_xy, today, alpha, spacing, min_area_ha)
        return change(shape, info, today)
    return unittest.mock.patch.object(edge_scale, 'trim', trim)


def fallen(shape, info, today):
    """What `trim` returns when its rule raised: today's shape, said so."""
    return today, dict(info, fallback=True, error='RuntimeError: provoked',
                       removed=[], passes=[])


# --- повтор движка и сутки ----------------------------------------------------------

class Replay(World):

    def test_a_clean_field_is_the_same_under_both(self):
        record = self.record('2026-09-10', CLEAN)
        self.assertTrue(record['consistent'])
        self.assertTrue(record['control'])
        self.assertFalse(record['changed'])
        self.assertFalse(record['fallback'])
        self.assertEqual(record['a7_ha'], record['rule_ha'])
        self.assertEqual(record['violations'], [])
        site = record['sites'][0]
        self.assertTrue(site['on'])
        self.assertAlmostEqual(site['g'], 6.0, delta=0.1)
        self.assertEqual(replay.site_line(site), 'site 1: 4.500 -> 4.500 ha, unchanged')
        self.assertNotIn('removed', replay.evidence_text(site))

    def test_a_road_along_the_headland_is_cut_and_its_pieces_add_up(self):
        record = self.record('2026-09-11', ROAD)
        self.assertTrue(record['consistent'])
        self.assertTrue(record['control'])
        self.assertTrue(record['changed'])
        self.assertAlmostEqual(record['a7_ha'], 4.7562, places=4)
        self.assertAlmostEqual(record['rule_ha'], 4.5624, places=4)
        site = record['sites'][0]
        self.assertGreater(site['gap'], 0.1)
        self.assertGreater(site['strip'], 0.01)
        self.assertAlmostEqual(site['gap'] + site['strip'] + site['below_floor'],
                               site['removed'], places=4)
        self.assertEqual(site['rule_pieces'], 1)
        self.assertEqual(replay.site_line(site),
                         'site 1: 4.756 -> 4.562 ha, removed 0.194 ha (4.1%)')
        self.assertTrue(replay.evidence_text(site).startswith(
            'removed: gaps 0.168 ha, strips 0.026 ha, below the floor 0.000 ha; '
            'in 1 rule site(s); step g 6.01 m'), replay.evidence_text(site))

    def test_sprayer_sites_are_left_alone_and_say_why(self):
        for day, unit, reason in (('2026-09-14', SPRAY, 'step sets alpha'),
                                  ('2026-09-15', WIDE, 'wide-pass work')):
            record = self.record(day, unit)
            self.assertTrue(record['consistent'], unit)
            self.assertFalse(record['changed'], unit)
            self.assertEqual(record['violations'], [], unit)
            site = record['sites'][0]
            self.assertIs(site['on'], False)
            self.assertEqual(site['off'], reason)
            self.assertTrue(replay.evidence_text(site).startswith(
                'left alone: %s; step g ' % reason), replay.evidence_text(site))

    def test_three_passes_side_by_side_are_kept_and_named(self):
        record = self.record('2026-09-16', PASSES)
        self.assertFalse(record['changed'])
        site = record['sites'][0]
        self.assertTrue(site['on'])
        self.assertEqual(site['passes'], 1)
        self.assertAlmostEqual(site['passes_ha'], 0.54, delta=0.005)
        self.assertIn('kept as passes: 1 strip(s), 0.54', replay.evidence_text(site))

    def test_a_road_only_site_is_deleted_whole(self):
        record = self.record('2026-09-18', ROADONLY)
        self.assertTrue(record['consistent'])
        self.assertEqual((record['a7_sites'], record['rule_sites']), (1, 0))
        self.assertEqual(record['rule_ha'], 0.0)
        site = record['sites'][0]
        self.assertTrue(replay.site_deleted(site))
        self.assertEqual(replay.site_line(site), 'site 1: 0.514 -> 0.000 ha, removed '
                         '0.514 ha (100.0%) [beyond B-2] [deleted]')
        self.assertAlmostEqual(site['strip'], 0.5144, places=4)

    def test_a_repetition_that_drifts_from_compute_day_is_caught(self):
        """Negative control of `consistent`: the evidence path sees another alpha."""
        points = replay.read_day_readonly(self.folder, ROAD, '2026-09-11')
        with unittest.mock.patch.object(replay, '_adaptive_alpha',
                                        return_value=(6.0, 14.0)):
            played = replay.replay(points, None)
        self.assertFalse(replay.judge('2026-09-11', ROAD, played)['consistent'])
        self.assertTrue(replay.judge('2026-09-11', ROAD,
                                     replay.replay(points, None))['consistent'])

    def test_a_rule_shape_the_repetition_does_not_reproduce_is_caught(self):
        """Negative control of `consistent` on the rule's side: `compute_day`
        and the repetition get different shapes from `trim`."""
        calls = []

        def every_other(shape, info, today):
            calls.append(1)
            if len(calls) % 2:
                return shape, info
            x0, y0, _x1, _y1 = today.bounds
            return shape.difference(box(x0 - 1, y0 - 1, x0 + 40, y0 + 40)), info
        points = replay.read_day_readonly(self.folder, CLEAN, '2026-09-10')
        with patched_trim(every_other):
            played = replay.replay(points, None)
        self.assertEqual(len(calls), 2)
        self.assertFalse(replay.judge('2026-09-10', CLEAN, played)['consistent'])

    def test_the_repetition_asks_trim_exactly_what_the_engine_asks(self):
        """Same points, today's shape, alpha, the day's spacing and the floor:
        a repetition that called `trim` otherwise would explain a rule the
        engine never ran -- on days where the two calls happen to agree, the
        areas could not tell."""
        calls = []
        real = edge_scale.trim

        def recorded(points_xy, today, alpha, spacing, min_area_ha=0.3):
            calls.append((tuple(map(tuple, points_xy)), today.wkb, alpha, spacing,
                          min_area_ha))
            return real(points_xy, today, alpha, spacing, min_area_ha)
        points = replay.read_day_readonly(self.folder, ROAD, '2026-09-11')
        with unittest.mock.patch.object(edge_scale, 'trim', recorded):
            replay.replay(points, None)
        self.assertEqual(len(calls), 2)                 # compute_day, repetition
        self.assertEqual(calls[0], calls[1])
        self.assertIsNotNone(calls[0][3])               # the day's spacing

    def test_evidence_of_another_site_is_a_replay_mismatch(self):
        """`trim`'s site numbers are only trusted where the areas agree."""
        def renumbered(shape, info, today):
            sites = [dict(site, area_m2=site['area_m2'] + 5.0) for site in info['sites']]
            return shape, dict(info, sites=sites)
        points = replay.read_day_readonly(self.folder, ROAD, '2026-09-11')
        with patched_trim(renumbered):
            played = replay.replay(points, None)
        record = replay.judge('2026-09-11', ROAD, played)
        self.assertFalse(record['consistent'])
        self.assertEqual(record['violations'], [])
        self.assertEqual(replay.evidence_text(record['sites'][0]),
                         'evidence not available')

    def test_the_control_judges_rows_of_the_method_in_force_only(self):
        tampered = self.record('2026-09-12', TAMPERED)
        self.assertFalse(tampered['control'])
        older = self.copy_db(("UPDATE gps_daily_aggregates SET method_version = ? "
                              "WHERE wialon_id = ?", (PREVIOUS_METHOD_VERSION, CLEAN)))
        record = self.record('2026-09-10', CLEAN, db=older)
        self.assertIsNone(record['control'])
        self.assertIn('the row was written by %s' % PREVIOUS_METHOD_VERSION,
                      replay._control_word(record))

    def test_a_fallback_is_recorded_and_breaks_no_invariant(self):
        points = replay.read_day_readonly(self.folder, ROAD, '2026-09-11')
        with patched_trim(fallen):
            record = replay.judge('2026-09-11', ROAD, replay.replay(points, None))
        self.assertTrue(record['fallback'])
        self.assertTrue(record['consistent'])
        self.assertEqual(record['violations'], [])
        self.assertFalse(record['changed'])
        self.assertEqual(record['error'], 'RuntimeError: provoked')


# --- часть 1: набор В-6 -----------------------------------------------------------

class V6(World):

    def rows(self):
        a7 = self.record('2026-10-05', V6B2)['a7_ha']
        return self.v6('1,O,2026-10-03,МТЗ-80Х 80 261 EA,Тест,APP-1,Бороналаш,1.920',
                       '2,P,2026-10-05,New Holland 7060 80 605 EA,Тест,APP-2,'
                       'Шудгорлаш,%.3f' % a7)

    def test_two_rows_one_by_name_one_by_the_total(self):
        code, out, _err = run_main('--db', self.db, '--dir', self.folder,
                                   '--v6', self.rows())
        self.assertEqual(code, 0, out)
        lines = [line for line in out.splitlines() if line.startswith('V6EDGE')]
        self.assertEqual(lines, [
            'V6EDGE n=1 unit=%d day=2026-10-03 a7=1.920 rule=1.920 control=ok' % V6A,
            'V6EDGE n=2 unit=%d day=2026-10-05 a7=4.756 rule=4.562 control=ok' % V6B2])
        self.assertIn('PART 1 (V-6 rows resolved and reproduced; hectares are not '
                      'judged here): PASS -- all 2 row(s) resolved, control ok', out)
        self.assertIn('part 1, V-6 numbers (2.13 cond. 3)     PASS', out)

    def test_a_fallback_row_gives_no_number_for_the_csv(self):
        with patched_trim(fallen):
            code, out, _err = run_main('--db', self.db, '--dir', self.folder,
                                       '--v6', self.rows())
        self.assertEqual(code, 3, out)
        self.assertIn('V6EDGE n=2 unit=%d day=2026-10-05 a7=4.756 rule=4.756 '
                      'control=FALLBACK' % V6B2, out)
        self.assertIn('FALLBACK: the rule failed (RuntimeError: provoked) and the '
                      'day kept the A7 shape', out)
        self.assertIn('NOT CHECKED -- 2 of 2 row(s) could not be checked: FALLBACK 2',
                      out)

    def test_a_control_mismatch_makes_the_part_invalid(self):
        tampered = self.copy_db(("UPDATE gps_work_polygons SET area_ha = area_ha + 0.5 "
                                 "WHERE wialon_id = ?", (V6A,)))
        path = self.v6('1,O,2026-10-03,МТЗ-80Х 80 261 EA,Тест,APP-1,Бороналаш,2.420')
        code, out, _err = run_main('--db', tampered, '--dir', self.folder, '--v6', path)
        self.assertEqual(code, 3, out)
        self.assertIn('control=MISMATCH', out)
        self.assertIn('PART 1 (V-6 rows resolved and reproduced; hectares are not '
                      'judged here): RUN INVALID', out)

    def test_points_gone_or_another_version_give_no_number(self):
        gone = self.v6('1,O,2026-09-20,Т-28 01 020 AA,Тест,APP-1,Бороналаш,0.000')
        code, out, _err = run_main('--db', self.db, '--dir', self.folder, '--v6', gone)
        self.assertEqual(code, 3, out)
        self.assertIn('points of this day are gone from disk: nothing to replay', out)
        self.assertIn('V6EDGE n=1 unit=%d day=2026-09-20 a7=- rule=- '
                      'control=UNRESOLVED' % NO_POINTS, out)
        older = self.copy_db(("UPDATE gps_daily_aggregates SET method_version = ? "
                              "WHERE wialon_id = ?", (PREVIOUS_METHOD_VERSION, V6A)))
        path = self.v6('1,O,2026-10-03,МТЗ-80Х 80 261 EA,Тест,APP-1,Бороналаш,1.920')
        code, out, _err = run_main('--db', older, '--dir', self.folder, '--v6', path)
        self.assertEqual(code, 3, out)
        self.assertIn('the row was written by %s' % PREVIOUS_METHOD_VERSION, out)
        self.assertIn('V6EDGE n=1 unit=%d day=2026-10-03 a7=1.920 rule=1.920 '
                      'control=UNRESOLVED' % V6A, out)
        self.assertIn('NOT CHECKED -- 1 of 1 row(s) could not be checked: '
                      'UNRESOLVED 1', out)

    def test_an_unknown_machine_is_unresolved(self):
        path = self.v6('1,O,2026-10-03,Беларус 99 999 ZZ,Тест,APP-1,Бороналаш,7.000')
        code, out, _err = run_main('--db', self.db, '--dir', self.folder, '--v6', path)
        self.assertEqual(code, 3, out)
        self.assertIn('UNRESOLVED: no unit published on that day carries this name', out)
        self.assertIn('V6EDGE n=1 unit=- day=2026-10-03 a7=- rule=- control=UNRESOLVED',
                      out)

    def test_the_real_set_loads(self):
        rows = replay.load_v5(V6_SET, 'V-6')
        self.assertEqual([row['n'] for row in rows], list(range(1, 15)))
        groups = [row['group'] for row in rows]
        self.assertEqual((groups.count('P'), groups.count('O'), groups.count('S')),
                         (5, 5, 4))
        broken = self.write('broken.csv', V6_HEADER + 'x,P,2026-10-01,A,B,C,D,1.0\n')
        with self.assertRaisesRegex(replay.BadInput, 'line 2 is not a V-6 row'):
            replay.load_v5(broken, 'V-6')


# --- часть 2: сутки New Holland -----------------------------------------------------

class OwnerDay(World):

    def owner(self, *extra):
        return run_main('--db', self.db, '--dir', self.folder, '--day',
                        '2026-10-01:%d' % OWNER, *extra)

    def test_the_bench_number_reproduced_passes_and_another_is_for_the_owner(self):
        inside = self.record('2026-10-01', OWNER)['sites'][0]['inside']
        with unittest.mock.patch.object(replay, 'BENCH_NH_RULE_HA', round(inside, 4)):
            code, out, _err = self.owner()
        self.assertEqual(code, 0, out)
        self.assertRegex(out, r'REPRODUCTION 2026-10-01:1273: PASS -- rule 4\.5624 ha')
        # this world's day is not the bench's: the real constant does not match
        code, out, _err = self.owner()
        self.assertEqual(code, 3, out)
        self.assertIn('REPRODUCTION 2026-10-01:1273: OWNER CHECK', out)
        self.assertIn('the server does not compute what the bench did', out)

    def test_the_tolerance_is_one_unit_of_the_third_decimal(self):
        stored = {'has_row': True, 'reason': None}

        def verdict(inside):
            record = {'sites': [{'inside': inside, 'area': 8.3287}],
                      'control': True, 'consistent': True}
            return replay.owner_day_report(record, stored, [].append)[0][1]
        bench = replay.BENCH_NH_RULE_HA
        self.assertEqual(verdict(bench + 0.001), replay.PASS)
        self.assertEqual(verdict(bench - 0.001), replay.PASS)
        self.assertEqual(verdict(bench + 0.0011), replay.OWNER_CHECK)
        self.assertEqual(verdict(bench - 0.0011), replay.OWNER_CHECK)

    def test_an_unpublished_or_unreproduced_day_is_not_judged(self):
        out = []
        self.assertEqual(replay.owner_day_report(None, {'has_row': False}, out.append),
                         [(replay.LABEL_NH, replay.NOT_CHECKED)])
        record = {'sites': [{'inside': 8.2265, 'area': 8.3287}], 'control': False,
                  'consistent': True}
        self.assertEqual(replay.owner_day_report(record, {'has_row': True,
                                                          'reason': None}, out.append),
                         [(replay.LABEL_NH, replay.RUN_INVALID)])

    def test_the_bench_number_is_the_preregistered_one(self):
        with open(ROADMAP, encoding='utf-8') as handle:
            text = handle.read()
        section = text[text.index('### 2.13'):text.index('### 2.9 ')]
        self.assertIn(('%.4f' % replay.BENCH_NH_RULE_HA).replace('.', ','), section)
        self.assertEqual(replay.OWNER_DAY, ('2026-10-01', 1273))


# --- часть 3: production -----------------------------------------------------------

class Production(World):

    def test_the_period_with_a_tampered_row_is_invalid_not_failed(self):
        code, out, _err = self.period('2026-09-01', '2026-09-30')
        self.assertEqual(code, 3, out)
        self.assertIn('published machine-days of the period: 10; excluded objects: 1 '
                      '(counted as a number, not replayed); points gone from disk: 1; '
                      'replayed: 8', out)
        self.assertIn('control, A7 recomputed == stored rows (rows of %s only): 7 of '
                      '8 same, 1 different, 0 not checked' % METHOD_VERSION, out)
        self.assertIn('  control mismatch 2026-09-12 %d' % TAMPERED, out)
        self.assertIn('never changes a site it leaves alone; and it ran on every '
                      'day): RUN INVALID', out)
        for unit in (EXCLUDED, LATE, OWNER):
            self.assertNotIn('unit %d' % unit, out)

    def test_the_report_of_the_period(self):
        code, out, _err = self.period('2026-09-01', '2026-09-30', db=self.untampered())
        self.assertEqual(code, 3, out)                   # the KML is the owner's
        self.assertIn('INVARIANTS (the rule never adds hectares, never leaves the A7 '
                      'sites by more than 1 m2, returns flat polygons, never changes a '
                      'site it leaves alone; and it ran on every day): PASS -- no '
                      'invariant broken on 7 machine-days, the rule ran on all of '
                      'them', out)
        self.assertIn('  machine-days changed: 3 of 7', out)
        self.assertIn('  plan-fact of the period, counted objects: A7 26.28 ha -> '
                      'rule 25.16 ha (-1.12 ha, -4.25%)', out)
        self.assertIn('  A7 sites: 8; the rule acts on 6 (12.64 ha); left alone: step '
                      'sets alpha 1 (6.88 ha), wide-pass work 1 (6.75 ha), no step 0 '
                      '(0.00 ha)', out)
        self.assertIn('  A7 sites changed: 3 (removed 1.12 ha); of them deleted whole: '
                      '2 (0.92 ha)', out)
        self.assertIn('  narrow strips kept as passes: 1 (0.54 ha)', out)
        self.assertIn('  removed share per machine-day: 0 4, <1% 0, 1-5% 1, 5-10% 0, '
                      '10-25% 1, >25% 1; without an A7 site: 0', out)
        self.assertIn('  the rule fell back to A7: 0 machine-day(s)', out)
        found = re.search(r'removed: empty gaps at the edge (\d+\.\d\d) ha, narrow '
                          r'strips (\d+\.\d\d) ha, left under the work floor '
                          r'(\d+\.\d\d) ha', out)
        self.assertAlmostEqual(sum(float(x) for x in found.groups()), 1.12, delta=0.015)
        self.assertRegex(out, r'seconds per machine-day: A7 median \d+\.\d\d max '
                              r'\d+\.\d\d; rule median \d+\.\d\d max \d+\.\d\d')

    def test_a_clean_period_passes_with_exit_0(self):
        code, out, _err = self.period('2026-09-14', '2026-09-16')
        self.assertEqual(code, 0, out)
        self.assertIn('machine-days changed: 0 of 3', out)

    def provoke(self, change, day='2026-09-11'):
        with patched_trim(change):
            return self.period(day, day)

    def test_the_real_rule_breaks_nothing_on_the_provoked_days(self):
        """Negative control of the four below: the same days, the real `trim`."""
        for day in ('2026-09-11', '2026-09-14'):
            code, out, _err = self.provoke(lambda shape, info, today: (shape, info), day)
            self.assertIn('): PASS -- no invariant broken on 1 machine-days', out)

    def test_a_rule_shape_outside_the_a7_sites_fails(self):
        def outside(shape, info, today):
            x0, y0, x1, y1 = today.bounds
            return (unary_union([shape, box(x1 + 500, y0, x1 + 600, y0 + 100)]),
                    info)
        code, out, _err = self.provoke(outside)
        self.assertEqual(code, 4, out)
        self.assertIn('rule site 2 lies 10000.0 m2 outside the A7 sites', out)
        self.assertIn('more hectares than A7', out)
        self.assertIn('): FAIL -- 1 of 1 machine-days break an invariant', out)

    def test_a_shape_that_is_not_flat_fails(self):
        def nested(shape, info, today):
            parts = edge_scale._polys(shape) + [shifted_box(today, 500.0)]
            return shapely.GeometryCollection([shapely.MultiPolygon(parts)]), info
        code, out, _err = self.provoke(nested)
        self.assertEqual(code, 4, out)
        self.assertIn('the rule shape is not flat polygons: GeometryCollection of '
                      'MultiPolygon', out)

    def test_a_site_the_rule_leaves_alone_must_not_change(self):
        def bitten(shape, info, today):
            x0, y0, _x1, _y1 = today.bounds
            return shape.difference(box(x0 - 1, y0 - 1, x0 + 30, y0 + 30)), info
        code, out, _err = self.provoke(bitten, '2026-09-14')
        self.assertEqual(code, 4, out)
        self.assertRegex(out, r'A7 site 1 is left alone \(step sets alpha\) yet '
                              r'changed by \d+\.\d m2')

    def test_a_fallback_is_not_checked(self):
        code, out, _err = self.provoke(fallen)
        self.assertEqual(code, 3, out)
        self.assertIn('  FALLBACK 2026-09-11 %d: RuntimeError: provoked' % ROAD, out)
        self.assertIn('): NOT CHECKED -- the rule failed on 1 machine-day(s) and left '
                      'them as A7: not checked there', out)
        self.assertIn('  the rule fell back to A7: 1 machine-day(s)', out)

    def test_the_kml_carries_the_numbers_and_the_removed_pieces(self):
        kml = os.path.join(self.scratch, 'edge.kml')
        code, out, _err = self.period('2026-09-01', '2026-09-30', '--kml', kml,
                                      db=self.untampered())
        self.assertEqual(code, 3, out)
        folders = ET.parse(kml).getroot().findall('.//k:Folder', NS)
        titles = [f.find('k:name', NS).text for f in folders]
        # the largest removal first: the road-only day, the far road, the headland
        self.assertEqual(len(folders), 3)
        self.assertTrue(titles[0].startswith('%d · 18.09.2026 · A7 0,514 га → '
                                             'правило / қоида 0,000 га' % ROADONLY),
                        titles[0])

        def placemarks(folder):
            return [(p.find('k:styleUrl', NS).text, p.find('k:name', NS).text,
                     getattr(p.find('k:description', NS), 'text', None))
                    for p in folder.findall('k:Placemark', NS)]
        road_only_marks = placemarks(folders[0])
        self.assertIn(('#site', 'Участок / участка 1: 0,514 -> 0,000 га, убрано / олиб '
                       'ташланди 0,514 га (100,0 %), больше допуска В-2 / В-2 йўл '
                       'қўйилишидан ташқари, удалён целиком / бутунлай олиб '
                       'ташланди'), [m[:2] for m in road_only_marks])
        strips = [m for m in road_only_marks if m[0] == '#strip']
        self.assertEqual(len(strips), 1)
        self.assertTrue(strips[0][1].startswith(
            'Убрано: узкая полоса, по которой ездили вдоль / олиб ташланди: бўйлаб '
            'юрилган тор йўлак, 0,514 га (участок / участка 1)'), strips[0][1])
        headland = [f for f, t in zip(folders, titles) if '11.09.2026' in t][0]
        marks = placemarks(headland)
        site = [m for m in marks if m[0] == '#site'][0]
        self.assertEqual(site[1], 'Участок / участка 1: 4,756 -> 4,562 га, убрано / '
                         'олиб ташланди 0,194 га (4,1 %)')
        self.assertIn('пустые промежутки у края 0,168 га, узкие полосы, по которым '
                      'ездили вдоль, 0,026 га, остаток меньше 0,3 га 0,000 га', site[2])
        self.assertIn('дала четидаги бўш оралиқлар 0,168 га', site[2])
        self.assertTrue(any(m[0] == '#gap' for m in marks))
        self.assertTrue(any(m[0] == '#new' and m[1].startswith(
            'Правило края поля, второй круг / дала чети қоидаси, иккинчи босқич: '
            'участок / участка 1 — 4,562 га') for m in marks))
        self.assertTrue(any(m[0] == '#work' for m in marks))
        far = [f for f, t in zip(folders, titles) if '17.09.2026' in t][0]
        names = [m[1] for m in placemarks(far)]
        self.assertIn('Участок / участка 1: 1,920 га, без изменений / ўзгаришсиз', names)
        # every style the folders use is defined in the document
        styles = {s.get('id') for s in ET.parse(kml).getroot().iter(
            '{http://www.opengis.net/kml/2.2}Style')}
        used = {m[0][1:] for f in folders for m in placemarks(f)}
        self.assertLessEqual(used, styles)
        # the same line in the console, ASCII
        self.assertIn('    site 1: 0.514 -> 0.000 ha, removed 0.514 ha (100.0%) '
                      '[beyond B-2] [deleted]', out)
        out.encode('ascii')

    def test_strips_kept_as_passes_have_their_own_layer(self):
        points = replay.read_day_readonly(self.folder, PASSES, '2026-09-16')
        played = replay.replay(points, None)
        record = replay.judge('2026-09-16', PASSES, played)
        root = ET.fromstring(replay.kml_document(
            [replay.kml_folder(record, played, points, 'Рыхлитель')]).encode('utf-8'))
        marks = [(p.find('k:styleUrl', NS).text, p.find('k:name', NS).text)
                 for p in root.iter('{http://www.opengis.net/kml/2.2}Placemark')]
        self.assertIn(('#passes', 'Оставлено: проходы рядом друг с другом / '
                       'қолдирилди: ёнма-ён ўтишлар, 0,540 га (участок / участка 1)'),
                      marks)
        self.assertIn(('#site', 'Участок / участка 1: 0,540 га, без изменений / '
                       'ўзгаришсиз'), marks)

    def test_a_violation_where_the_replay_drifted_is_invalid_not_failed(self):
        """A broken promise counts only on days the repetition reproduces."""
        def row(consistent, violations):
            return {'day': '2026-09-11', 'unit': ROAD, 'control': True,
                    'version': METHOD_VERSION, 'consistent': consistent,
                    'violations': violations, 'fallback': False, 'error': None,
                    'changed': False, 'a7_ha': 1.0, 'rule_ha': 1.0, 'sites': [],
                    'seconds_a7': 0.1, 'seconds_rule': 0.1}
        self.assertEqual(replay.report_period([row(False, ['x'])], 1, 0, 0, [].append),
                         replay.RUN_INVALID)
        self.assertEqual(replay.report_period([row(True, ['x'])], 1, 0, 0, [].append),
                         replay.FAIL)
        self.assertEqual(replay.report_period([row(True, [])], 1, 0, 0, [].append),
                         replay.PASS)

    def test_a_left_alone_site_tells_why_in_the_kml(self):
        site = {'number': 1, 'area': 6.8823, 'inside': 6.8823, 'removed': 0.0,
                'on': False, 'off': 'step sets alpha', 'rule_pieces': 1}
        name, description = replay.site_label(site)
        self.assertEqual(name, 'Участок / участка 1: 6,882 га, без изменений / '
                         'ўзгаришсиз')
        self.assertIn('Правило на этом участке не действует: шаг проходов не меньше '
                      'альфы — опрыскивание или другая работа широкими проходами. / '
                      'Қоида бу участкада ишламайди: ўтишлар оралиғи альфадан кам '
                      'эмас — пуркаш ёки бошқа кенг ўтишли иш.', description)

    def test_progress_every_200_machine_days(self):
        lines = []
        with unittest.mock.patch.object(replay, 'PROGRESS_EVERY', 2):
            con = replay.open_readonly(self.db)
            try:
                replay.part_period(con, self.folder, None, '2026-09-10', '2026-09-14',
                                   None, [].append, lines.append)
            finally:
                con.close()
        self.assertEqual(lines, ['  2 of 4 machine-days', '  4 of 4 machine-days'])
        self.assertEqual(replay.PROGRESS_EVERY, 200)


def shifted_box(today, east):
    x0, y0, x1, y1 = today.bounds
    return box(x1 + east, y0, x1 + east + 100.0, y0 + 100.0)


# --- часть 4: ответы операторов ------------------------------------------------------

class Labels(World):

    def test_passage_removed_and_work_cut_goes_to_the_owner(self):
        code, out, _err = run_main('--db', self.db, '--dir', self.folder, '--labels')
        self.assertEqual(code, 3, out)
        self.assertIn('labelled sites: 3 in 2 machine-day(s); compared 3; not '
                      'compared: none', out)
        # measured inside the STORED polygons (six decimals): a few square
        # metres short of the sites themselves
        found = re.search(r'rabota   sites 2: A7 (\d+\.\d+) ha -> rule (\d+\.\d+) ha',
                          out)
        self.assertAlmostEqual(float(found.group(1)), 1.920 + 0.514, delta=0.005)
        self.assertAlmostEqual(float(found.group(2)), 1.920, delta=0.005)
        self.assertRegex(out, r'proezd   sites 1: A7 0\.40\d ha -> rule 0\.000 ha '
                              r'inside the labelled polygons, removed 0\.40\d ha '
                              r'\(100\.0%\), kept 0\.0%')
        self.assertIn('rabota cut beyond B-2: 2026-09-18 unit %d site 1:' % ROADONLY,
                      out)
        self.assertNotIn('unit %d site 1: A7' % LABELS, out)   # kept whole
        self.assertIn('share of proezd hectares the rule removes: 100.0%', out)
        self.assertIn('PART 4 (labels, no FAIL here): OWNER CHECK -- 1 rabota site(s) '
                      'cut beyond B-2', out)

    def test_inside_the_band_the_part_passes(self):
        out = []
        con = replay.open_readonly(self.db)
        try:
            verdicts = replay.part_labels(con, self.folder, None, '2026-09-17',
                                          '2026-09-17', out.append)
        finally:
            con.close()
        self.assertEqual(verdicts, [(replay.LABEL_LABELS, replay.PASS)], out)

    def test_no_labels_is_not_checked_and_a_mismatch_is_invalid(self):
        con = replay.open_readonly(self.db)
        try:
            self.assertEqual(replay.part_labels(con, self.folder, None, '2026-09-01',
                                                '2026-09-10', [].append),
                             [(replay.LABEL_LABELS, replay.NOT_CHECKED)])
        finally:
            con.close()
        tampered = self.copy_db(("UPDATE gps_work_polygons SET area_ha = 9.0 "
                                 "WHERE wialon_id = ? AND site_number = 1", (LABELS,)))
        con = replay.open_readonly(tampered)
        try:
            self.assertEqual(replay.part_labels(con, self.folder, None, '2026-09-17',
                                                '2026-09-17', [].append),
                             [(replay.LABEL_LABELS, replay.RUN_INVALID)])
        finally:
            con.close()


# --- запуск, отказы, «ничего не пишет» ------------------------------------------------

class Main(World):

    def test_bad_input_is_refused_before_anything_is_computed(self):
        missing = os.path.join(self.scratch, 'no.db')
        bad_csv = self.write('bad.csv', 'n,group,day\n1,P,2026-10-01\n')
        cases = [
            (),
            ('--db', missing, '--dir', self.folder, '--since', '2026-09-01'),
            ('--db', self.db, '--dir', os.path.join(self.scratch, 'nowhere'),
             '--labels'),
            ('--since', '2026-09-01'),
            ('--db', self.db, '--dir', self.folder, '--since', '2026-09-01',
             '--kml', os.path.join(self.scratch, 'no', 'x.kml')),
            ('--db', self.db, '--dir', self.folder, '--kml',
             os.path.join(self.scratch, 'x.kml'), '--labels'),
            ('--db', self.db, '--dir', self.folder, '--day', '2026-10-01'),
            ('--db', self.db, '--dir', self.folder, '--since', '2026-13-01'),
            ('--db', self.db, '--dir', self.folder, '--since', '2026-09-30',
             '--until', '2026-09-01'),
            ('--db', self.db, '--dir', self.folder, '--day', '2026-09-11:%d' % ROAD,
             '--until', '2026-09-30'),
            ('--db', self.db, '--dir', self.folder, '--v6',
             os.path.join(self.scratch, 'no.csv')),
            ('--db', self.db, '--dir', self.folder, '--v6', bad_csv),
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
        v6 = self.v6('1,O,2026-10-03,МТЗ-80Х 80 261 EA,Тест,APP-1,Бороналаш,1.920')
        with unittest.mock.patch.object(sqlite3, 'connect', watched):
            code, out, _err = run_main('--db', self.db, '--dir', self.folder, '--v6',
                                       v6, '--day', '2026-09-11:%d' % ROAD, '--since',
                                       '2026-09-17', '--until', '2026-09-18',
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
        v6 = self.v6('1,O,2026-10-03,МТЗ-80Х 80 261 EA,Тест,APP-1,Бороналаш,1.920')
        points = sorted(os.listdir(self.folder))
        before = [digest(self.db)] + [digest(os.path.join(self.folder, name))
                                       for name in points]
        listing = (sorted(os.listdir(os.path.dirname(self.db))), points)
        code, out, err = run_main('--db', self.db, '--dir', self.folder, '--v6', v6,
                                  '--day', '2026-10-01:%d' % OWNER, '--since',
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
        for part in ('=== PART 1', '=== PART 2', '=== PART 3', '=== PART 4',
                     '=== SUMMARY ==='):
            self.assertIn(part, out)
        self.assertIn("A7: compute_day, method %s; rule: compute_day(edge_rule="
                      "'edge-scale-2026-10-11'), method %s+edge-scale-2026-10-11"
                      % (METHOD_VERSION, METHOD_VERSION), out)
        out.encode('ascii')


class InvariantBounds(unittest.TestCase):
    """The four promises at their exact bounds, on boxes (metres)."""

    A7 = box(0, 0, 100, 100)                                   # 1 ha

    @staticmethod
    def site(on, inside_ha, off='step sets alpha'):
        return {'number': 1, 'area': 1.0, 'inside': inside_ha, 'on': on, 'off': off}

    def test_a_rule_equal_to_a7_breaks_nothing(self):
        self.assertEqual(replay.invariant_violations([self.A7], [self.A7], self.A7,
                                                     [self.site(False, 1.0)]), [])

    def test_one_square_metre_outside_is_noise_and_more_is_not(self):
        inside = box(0, 0, 100, 99)
        within = unary_union([inside, box(100, 50, 100.9, 51)])  # 0.9 m2 out
        beyond = unary_union([inside, box(100, 50, 101.5, 51)])  # 1.5 m2 out
        self.assertEqual(replay.invariant_violations([self.A7], [within], within, []),
                         [])
        self.assertEqual(replay.invariant_violations([self.A7], [beyond], beyond, []),
                         ['rule site 1 lies 1.5 m2 outside the A7 sites'])

    def test_more_hectares_by_more_than_the_float_noise(self):
        bigger = box(0, 0, 100, 100.01)                        # +1 m2: 1e-4 ha
        found = replay.invariant_violations([self.A7], [bigger], bigger, [])
        self.assertIn('more hectares than A7: 1.0001 > 1.0000', found)
        same = box(0, 0, 100, 100.000001)                      # +1e-4 m2
        self.assertEqual(replay.invariant_violations([self.A7], [same], same, []), [])

    def test_only_flat_polygons(self):
        nested = shapely.GeometryCollection([shapely.MultiPolygon(
            [box(0, 0, 50, 100), box(60, 0, 100, 100)])])
        self.assertEqual(replay.invariant_violations([self.A7], [], nested, []),
                         ['the rule shape is not flat polygons: GeometryCollection '
                          'of MultiPolygon'])
        flat = shapely.MultiPolygon([box(0, 0, 50, 100), box(60, 0, 100, 100)])
        self.assertEqual(replay.invariant_violations([self.A7], list(flat.geoms), flat,
                                                     []), [])
        self.assertEqual(replay.invariant_violations([self.A7], [], None, []), [])

    def test_a_site_left_alone_may_not_change_by_more_than_a_square_metre(self):
        self.assertEqual(replay.invariant_violations(
            [self.A7], [self.A7], self.A7, [self.site(False, 1.0 - 0.00009)]), [])
        self.assertEqual(replay.invariant_violations(
            [self.A7], [self.A7], self.A7, [self.site(False, 1.0 - 0.00015)]),
            ['A7 site 1 is left alone (step sets alpha) yet changed by 1.5 m2'])
        # the rule acting on a site may remove from it -- that is its job
        self.assertEqual(replay.invariant_violations(
            [self.A7], [self.A7], self.A7, [self.site(True, 0.5)]), [])

    def test_a_site_counts_as_changed_above_one_square_metre(self):
        self.assertTrue(replay.site_changed({'removed': 0.00011}))
        self.assertFalse(replay.site_changed({'removed': 0.00009}))
        self.assertFalse(replay.site_changed({'removed': -2e-15}))


class Sources(unittest.TestCase):
    """What the tool copies or reuses is what its source says."""

    def test_the_left_alone_reasons_are_the_engines(self):
        with open(os.path.join(REPO, 'gps', 'edge_scale.py'), encoding='utf-8') as handle:
            engine = set(re.findall(r'off="([^"]+)"', handle.read()))
        self.assertEqual(engine, set(replay.OFF_TEXT))

    def test_the_uzbek_half_of_the_kml_is_cyrillic(self):
        texts = [replay.KML_RULE, replay.KML_B2, replay.KML_DELETED, replay.KML_GAP,
                 replay.KML_STRIP, replay.KML_PASSES]
        uzbek = [text.split(' / ', 1)[1] for text in texts]
        uzbek += [uz for _ru, uz in replay.OFF_TEXT.values()]
        for text in uzbek:
            self.assertIsNone(re.search('[A-Za-z]', text), text)
            self.assertTrue(re.search('[Ѐ-ӿ]', text), text)

    def test_the_round_1_helpers_are_reused_not_copied(self):
        for name in ('resolve_v5', 'load_v5', 'control_of', 'stored_day',
                     'pick_for_kml', 'labelled_sites', 'utm_of_geojson', 'beyond_b2'):
            self.assertIs(getattr(replay, name), getattr(round1, name), name)

    def test_the_tool_imports_neither_flask_nor_the_application(self):
        code = ('import sys; sys.path.insert(0, %r); import tools.gps_edge_scale_replay; '
                'print(sorted(m for m in ("flask", "flask_sqlalchemy", "app", '
                '"models") if m in sys.modules))' % REPO)
        done = subprocess.run([sys.executable, '-c', code], capture_output=True,
                              text=True, cwd=REPO, timeout=120)
        self.assertEqual(done.returncode, 0, done.stderr)
        self.assertEqual(done.stdout.strip(), '[]')


if __name__ == '__main__':
    unittest.main()
