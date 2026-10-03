# -*- coding: utf-8 -*-
"""tools/gps_alpha_replay.py -- «было / стало» правила допуска A7.

Окружение расчёта (геометрия): прогон на настоящем движке. Здесь держится то,
что делает прогон доказательством, а не пересказом:
  * сутки ниже порога совпадают с действующим кодом бит в бит, сутки дорог
    срабатывают и теряют гектары, поле среди дорог остаётся полем;
  * срабатывание и инварианты судятся по сырым шагу и альфе, в том числе у
    суток, где правило не оставило ни одного участка;
  * каждый инвариант, нарушенный нарочно, даёт FAIL;
  * контроль прогона и сверка повтора с движком делают прогон
    недействительным, а не проваленным, и не судят строки старой версии;
  * условие 3 не выносит PASS, пока не пересчитаны все шесть названных
    суток, и проваливается, если сработавшие сутки не потеряли гектаров;
  * исключённые объекты проходят инварианты, но не входят в потери и
    план-факт;
  * наборы с ручными замерами: поле -- PASS, сутки дорог -- на глаз
    владельцу с названием контура, пустой ввод -- не проверено;
  * код выхода несёт вердикт; KML несёт «было» и «стало»;
  * ни база, ни файлы точек, ни треки не меняются;
  * шесть названных суток -- те, что записаны в предрегистрации.
"""
import contextlib
import hashlib
import io
import json
import os
import re
import shutil
import sqlite3
import tempfile
import unittest
import unittest.mock
import xml.etree.ElementTree as ET
from datetime import datetime

import migrate_gps_daily_001 as migration
import tools.gps_alpha_replay as replay
from gps.area import SPACING_CAP_M
from gps.daily import METHOD_VERSION, compute_day, write_day
from gps.tests.test_area import (TWO_ROADS, shuttle_track, slow_loop,
                                 xy_to_lonlat)
from gps_collector import config as collector_config
from gps_collector import storage
from tests.test_gps_units_inventory import DDL

ROADS, FIELD, MIXED, TAMPERED, NO_POINTS, OLD = 393, 387, 388, 389, 390, 391
EXCLUDED, WIDE, LATE, SPECIAL, EXCLUDED_WIDE = 392, 394, 395, 396, 397
ROADMAP = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(
    os.path.abspath(__file__)))), 'docs', 'GPS_PLAN_FAKT_VISION_ROADMAP.md')


def local_start(day):
    return int(datetime.strptime(day, '%Y-%m-%d')
               .replace(tzinfo=collector_config.TZ).timestamp())


def shifted(track, day, hour=8):
    start = local_start(day) + hour * 3600
    return [(start + int(t), lon, lat, speed, 10) for t, lon, lat, speed in track]


def field_and_roads():
    field = shuttle_track(100.0, 300.0, pass_spacing_m=6.0, point_step_m=100.0)
    roads = slow_loop([(e + 1000.0, n + 1000.0) for e, n in TWO_ROADS],
                      loops=6, start_time=field[-1][0] + 600)
    return field + roads


def row(area, alpha, spacing, polygon='p'):
    return {'area_ha': area, 'alpha_used_m': alpha, 'pass_spacing_m': spacing,
            'polygon_geojson': polygon}


class World(unittest.TestCase):
    def setUp(self):
        self.folder = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.folder, True)
        self.db = os.path.join(self.folder, 'transport.db')
        con = sqlite3.connect(self.db)
        try:
            for statement in DDL[:2]:   # vialon_mappings, equipment
                con.execute(statement)
            con.execute(migration.CREATE_DAILY_AGGREGATES)
            con.execute(migration.CREATE_WORK_POLYGONS)
            con.execute("INSERT INTO equipment (name, plate, category, "
                        "organization_id, is_active) VALUES "
                        "('МТЗ-80.1', '80 239 NA', 'mtz', 1, 1)")
            con.execute("INSERT INTO vialon_mappings (vialon_name, wialon_id, "
                        "equipment_id, skip) VALUES ('МТЗ 239', ?, 1, 0)",
                        (ROADS,))
            # «не наша»: человек пометил строку -- план-факт её не считает
            for unit in (EXCLUDED, EXCLUDED_WIDE):
                con.execute("INSERT INTO vialon_mappings (vialon_name, "
                            "wialon_id, equipment_id, skip) VALUES "
                            "(?, ?, NULL, 1)", ('Чужой %d' % unit, unit))
            con.commit()
            self.day(con, '2026-09-26', ROADS, slow_loop(TWO_ROADS))
            self.day(con, '2026-09-24', EXCLUDED, slow_loop(TWO_ROADS))
            self.day(con, '2026-09-27', FIELD,
                     shuttle_track(300.0, 300.0, pass_spacing_m=14.0))
            self.day(con, '2026-09-23', WIDE,
                     shuttle_track(300.0, 300.0, pass_spacing_m=40.0))
            # исключённый объект у порога: в отчёт условия 5 он идти не должен
            self.day(con, '2026-09-22', EXCLUDED_WIDE,
                     shuttle_track(300.0, 300.0, pass_spacing_m=40.0))
            # за границей периода и с причиной: в прогон не входят вовсе
            self.day(con, '2026-10-01', LATE, slow_loop(TWO_ROADS))
            self.day(con, '2026-09-21', SPECIAL, slow_loop(TWO_ROADS),
                     track_only=True)
            self.day(con, '2026-09-28', MIXED, field_and_roads())
            self.day(con, '2026-09-29', TAMPERED,
                     shuttle_track(300.0, 300.0, pass_spacing_m=6.0))
            con.execute("UPDATE gps_work_polygons SET area_ha = area_ha + 1 "
                        "WHERE wialon_id = ?", (TAMPERED,))
            con.commit()
            self.day(con, '2026-09-25', OLD,
                     shuttle_track(300.0, 300.0, pass_spacing_m=6.0))
            con.execute("UPDATE gps_daily_aggregates SET method_version = "
                        "'fixed-alpha-2026-07-29' WHERE wialon_id = ?", (OLD,))
            # опубликованная строка без точек на диске
            con.execute("INSERT INTO gps_daily_aggregates (work_date, wialon_id, "
                        "method_version, computed_at) VALUES "
                        "('2026-09-30', ?, ?, '2026-10-01 01:00:00')",
                        (NO_POINTS, METHOD_VERSION))
            con.commit()
        finally:
            con.close()

    def day(self, con, day, unit, track, track_only=False):
        points = shifted(track, day)
        storage.write_points(self.folder, [(unit, t, lon, lat, speed, None, sats)
                                           for t, lon, lat, speed, sats in points])
        write_day(con, day, unit, compute_day(points, track_only=track_only),
                  '2026-10-02 01:00:00')

    def untamper(self):
        con = sqlite3.connect(self.db)
        try:
            con.execute("DELETE FROM gps_daily_aggregates WHERE wialon_id = ?",
                        (TAMPERED,))
            con.commit()
        finally:
            con.close()

    def judged(self):
        con = sqlite3.connect('file:%s?mode=ro' % self.db, uri=True)
        try:
            rows = {}
            for day, unit, version in replay.published_days(
                    con, '2026-09-01', '2026-09-30'):
                points = replay.read_day_readonly(self.folder, unit, day)
                if not points:
                    continue
                today, capped = replay.replay_day(points, None)
                rows[unit] = replay.judge_day(
                    day, unit, version, replay.stored_sites(con, day, unit),
                    today, capped, METHOD_VERSION,
                    replay.measure([r[:4] for r in points]))
        finally:
            con.close()
        return rows

    def run_production(self, named=None, kml=None):
        out = []
        patch = (unittest.mock.patch.object(replay, 'NAMED_DAYS', named)
                 if named is not None else contextlib.nullcontext())
        with patch:
            verdicts = replay.replay_production(self.db, self.folder,
                                                '2026-09-01', '2026-09-30',
                                                kml, out=out.append)
        return verdicts, '\n'.join(out)

    def run_main(self, *argv):
        with contextlib.redirect_stdout(io.StringIO()) as out, \
                contextlib.redirect_stderr(io.StringIO()) as err:
            code = replay.main(list(argv))
        return code, out.getvalue(), err.getvalue()

    def digest(self, path):
        with open(path, 'rb') as handle:
            return hashlib.sha256(handle.read()).hexdigest()


class Production(World):

    def test_roads_fall_fields_stay_and_the_mixed_day_keeps_its_field(self):
        rows = self.judged()
        roads, field, mixed = rows[ROADS], rows[FIELD], rows[MIXED]
        self.assertTrue(roads['triggered'])
        self.assertAlmostEqual(roads['ha_today'], 60.0, delta=0.5)
        self.assertEqual(roads['ha_cap'], 0.0)
        self.assertFalse(field['triggered'])
        self.assertEqual(replay.full_key(field['today']),
                         replay.full_key(field['capped']))
        self.assertTrue(mixed['triggered'])
        self.assertLess(mixed['ha_cap'], mixed['ha_today'] - 1.0)
        self.assertAlmostEqual(mixed['ha_cap'], 2.88, delta=0.3)
        for unit in (ROADS, FIELD, MIXED, WIDE, EXCLUDED):
            self.assertEqual(rows[unit]['violations'], [], unit)
            self.assertTrue(rows[unit]['control'], unit)
        for unit in rows:
            self.assertTrue(rows[unit]['consistent'], unit)
        self.assertFalse(rows[TAMPERED]['control'])
        self.assertIsNone(rows[OLD]['control'])

    def test_a_day_left_without_sites_is_still_judged_by_its_raw_numbers(self):
        """The roads day: the rule leaves no site, so there is no row to read.

        Triggering and the alpha invariants come from the raw measure: today
        ~300 m and alpha ~360 m, with the cap no spacing and the fixed 10 m.
        Read from rows, this day would look untriggered with no alpha at all.
        """
        roads = self.judged()[ROADS]
        self.assertEqual(roads['capped'], [])
        self.assertAlmostEqual(roads['spacing'], 300.0, delta=1.0)
        self.assertAlmostEqual(roads['alpha_today'], 360.0, delta=1.2)
        self.assertIsNone(roads['spacing_cap'])
        self.assertEqual(roads['alpha_cap'], 10.0)
        self.assertGreater(roads['spacing'], SPACING_CAP_M)

    def test_the_measure_takes_only_the_work_window_like_the_engine(self):
        """Fast transit votes for nothing: the engine never sees it.

        A field in 6 m passes and six laps of the roads at 40 km/h. The engine
        takes the 1-15 km/h points only, so the spacing is the field's; a
        measure taking every point would hear the roads 300 m apart.
        """
        field = shuttle_track(300.0, 300.0, pass_spacing_m=6.0)
        roads = slow_loop([(e + 1000.0, n + 1000.0) for e, n in TWO_ROADS],
                          loops=6, speed=40.0, start_time=field[-1][0] + 600)
        track = field + roads
        sites, _quality = replay.work_sites(track)
        spacing, _capped, alpha, _alpha_cap = replay.measure(track)
        self.assertEqual(spacing, sites[0].pass_spacing_m)
        self.assertEqual(alpha, sites[0].alpha_used_m)
        self.assertLess(spacing, 7.0)

    def test_the_control_makes_the_run_invalid_not_failed(self):
        verdicts, out = self.run_production(named=(('2026-09-26', ROADS),
                                                   ('2026-09-28', MIXED)))
        self.assertEqual(verdicts, [replay.RUN_INVALID, replay.PASS])
        self.assertEqual(replay.verdict_code(verdicts), 3)
        self.assertIn('control mismatch 2026-09-29 %d' % TAMPERED, out)
        self.assertIn('CONDITION 2 (invariants on every machine-day): '
                      'RUN INVALID -- the control failed on 1 machine-day(s), '
                      'the measure on 0', out)
        self.assertIn('1 not checked (older method version)', out)
        self.assertIn('points gone from disk: 1', out)

    def test_a_replay_measuring_other_numbers_than_the_engine_is_invalid(self):
        """Negative control of `consistent`: a measure that lies is caught."""
        self.untamper()
        with unittest.mock.patch.object(replay, 'measure',
                                        return_value=(6.0, 6.0, 10.0, 10.0)):
            verdicts, out = self.run_production(named=(('2026-09-26', ROADS),))
        self.assertEqual(verdicts[0], replay.RUN_INVALID)
        self.assertIn('measure mismatch 2026-09-26 %d' % ROADS, out)

    def test_the_named_days_pass_only_when_all_are_replayed_and_fall(self):
        self.untamper()
        verdicts, out = self.run_production(named=(('2026-09-26', ROADS),
                                                   ('2026-09-28', MIXED)))
        self.assertEqual(verdicts, [replay.PASS, replay.PASS], out)
        self.assertEqual(replay.verdict_code(verdicts), 0)
        self.assertIn('CONDITION 2 (invariants on every machine-day): PASS', out)
        self.assertIn('all six triggered, hectares strictly fall): PASS', out)
        verdicts, out = self.run_production()   # the real six: none here
        self.assertEqual(verdicts, [replay.PASS, replay.NOT_EVALUATED])
        self.assertEqual(replay.verdict_code(verdicts), 3)
        self.assertIn('not replayed', out)
        # поле не срабатывает -- значит, и «упасть» не может
        verdicts, out = self.run_production(named=(('2026-09-27', FIELD),
                                                   ('2026-09-01', ROADS)))
        self.assertEqual(verdicts, [replay.PASS, replay.FAIL])
        self.assertEqual(replay.verdict_code(verdicts), 4)
        self.assertIn('triggered NO', out)
        self.assertIn('DID NOT FALL', out)

    def test_a_triggered_day_that_keeps_its_hectares_did_not_fall(self):
        """«Strictly fall»: triggering alone is not enough."""
        kept = {'day': '2026-09-26', 'unit': ROADS, 'triggered': True,
                'alpha_today': 360.0, 'alpha_cap': 10.0,
                'ha_today': 5.0, 'ha_cap': 5.0, 'control': True,
                'consistent': True}
        out = []
        with unittest.mock.patch.object(replay, 'NAMED_DAYS',
                                        (('2026-09-26', ROADS),)):
            verdict = replay.condition_3([kept], {}, out.append)
            self.assertEqual(verdict, replay.FAIL)
            self.assertIn('DID NOT FALL', '\n'.join(out))
            fell = dict(kept, ha_cap=5.0 - 1e-6)
            self.assertEqual(replay.condition_3([fell], {}, [].append),
                             replay.PASS)

    def test_excluded_objects_are_checked_but_not_counted(self):
        self.untamper()
        rows = self.judged()
        self.assertTrue(rows[EXCLUDED]['triggered'])
        verdicts, out = self.run_production(named=(('2026-09-26', ROADS),))
        self.assertEqual(verdicts, [replay.PASS, replay.PASS])
        self.assertIn('machine-days replayed: 7 (points gone from disk: 1); '
                      'counted 5, excluded objects 2', out)
        self.assertNotIn('2026-09-24 %6d' % EXCLUDED, out)
        self.assertIn('machine-days triggered: 2 of 5 counted', out)
        counted = [r for unit, r in rows.items()
                   if unit not in (EXCLUDED, EXCLUDED_WIDE)]
        today = sum(r['ha_today'] for r in counted)
        capped = sum(r['ha_cap'] for r in counted)
        self.assertIn('plan-fact of the period, counted objects: %.2f ha today '
                      '-> %.2f ha with the rule (%+.2f)'
                      % (today, capped, capped - today), out)
        everything = sum(r['ha_today'] for r in rows.values())
        self.assertGreater(everything, today + 50.0)   # the control has teeth

    def test_untouched_days_near_the_cap_are_reported(self):
        """Counted objects only: the excluded twin at 40 m stays out."""
        self.untamper()
        rows = self.judged()
        wide = rows[WIDE]
        self.assertFalse(wide['triggered'])
        self.assertGreater(wide['alpha_today'], SPACING_CAP_M)
        self.assertGreater(rows[EXCLUDED_WIDE]['alpha_today'], SPACING_CAP_M)
        _verdicts, out = self.run_production(named=(('2026-09-26', ROADS),))
        self.assertIn('hectares on untouched machine-days with alpha '
                      '44.64-53.568 m: %.2f (1 machine-days)' % wide['ha_today'],
                      out)

    def test_the_kml_carries_before_and_after_for_every_picked_day(self):
        kml = os.path.join(self.folder, 'a7.kml')
        self.run_production(named=(('2026-09-26', ROADS),), kml=kml)
        root = ET.parse(kml).getroot()
        ns = {'k': 'http://www.opengis.net/kml/2.2'}
        folders = root.findall('.//k:Folder', ns)
        names = [f.find('k:name', ns).text for f in folders]
        # шесть закреплены, одни есть; плюс сработавшее «смешанное» из потерь,
        # исключённый объект в потери не идёт
        self.assertEqual(len(folders), 2)
        self.assertIn('было / эди 60.00 га → стало / бўлди 0.00 га', names[0])
        def marks(folder):
            return [(p.find('k:name', ns).text, p.find('k:styleUrl', ns).text)
                    for p in folder.findall('k:Placemark', ns)]

        def sites(folder, prefix):
            return [(name.rsplit('— ', 1)[1], style)
                    for name, style in marks(folder) if name.startswith(prefix)]

        was, now = 'Было / эди: участок / участка', 'Стало / бўлди: участок / участка'
        # дороги: было -- один оранжевый участок 60 га, стало -- ничего
        self.assertEqual(len(sites(folders[0], was)), 1)
        self.assertRegex(sites(folders[0], was)[0][0], r'^60\.\d\d га$')
        self.assertEqual(sites(folders[0], was)[0][1], '#site')
        self.assertEqual(sites(folders[0], now), [])
        # смешанные сутки: было -- все участки сегодня, стало -- поле 2,88 га
        mixed = self.judged()[MIXED]
        self.assertEqual(sites(folders[1], was),
                         [('%.2f га' % r['area_ha'], '#site')
                          for r in mixed['today']])
        self.assertEqual(sites(folders[1], now),
                         [('%.2f га' % r['area_ha'], '#new')
                          for r in mixed['capped']])
        self.assertTrue(any(name.startswith('Трек в работе')
                            for name, _style in marks(folders[1])))

    def test_only_published_days_of_the_period_are_replayed(self):
        """The period and «reason is empty» are the declared population.

        October's row is outside --until, the special machine's row carries a
        reason: both have points on disk, neither may be replayed. Replayed
        without its category, the special machine would even get sites.
        """
        con = sqlite3.connect('file:%s?mode=ro' % self.db, uri=True)
        try:
            units = {unit for _day, unit, _version
                     in replay.published_days(con, '2026-09-01', '2026-09-30')}
            reasons = dict(con.execute(
                'SELECT wialon_id, reason FROM gps_daily_aggregates '
                'WHERE wialon_id IN (?, ?)', (LATE, SPECIAL)).fetchall())
        finally:
            con.close()
        self.assertEqual(reasons, {LATE: None, SPECIAL: 'spetstekhnika'})
        self.assertNotIn(LATE, units)
        self.assertNotIn(SPECIAL, units)
        self.assertTrue(replay.read_day_readonly(self.folder, LATE, '2026-10-01'))
        self.assertTrue(replay.read_day_readonly(self.folder, SPECIAL,
                                                 '2026-09-21'))
        self.assertEqual(units, {ROADS, EXCLUDED, FIELD, WIDE, EXCLUDED_WIDE,
                                 MIXED, TAMPERED, OLD, NO_POINTS})

    def test_a_violation_stands_even_when_another_day_fails_the_control(self):
        """The invariant is the replay's own: the database plays no part.

        A violation on a day the replay reproduces is a FAIL, whatever the
        control says about another day; a violation only on a day where the
        replay disagrees with the engine is not trusted.
        """
        broken = replay.judge_day('2026-09-10', 1, METHOD_VERSION,
                                  [(2.0, 360.0, 300.0)],
                                  [row(2.0, 360.0, 300.0)],
                                  [row(3.0, 10.0, 6.0)], METHOD_VERSION,
                                  (300.0, 6.0, 360.0, 10.0))
        stale = replay.judge_day('2026-09-11', 2, METHOD_VERSION,
                                 [(9.0, 10.0, 6.0)], [row(2.0, 10.0, 6.0)],
                                 [row(2.0, 10.0, 6.0)], METHOD_VERSION,
                                 (6.0, 6.0, 10.0, 10.0))
        self.assertTrue(broken['control'])
        self.assertFalse(stale['control'])
        out = []
        self.assertEqual(replay.condition_2([broken, stale], out.append),
                         replay.FAIL)
        self.assertIn('control mismatch 2026-09-11 2', '\n'.join(out))
        lying = dict(broken, consistent=False)
        self.assertEqual(replay.condition_2([lying, stale], [].append),
                         replay.RUN_INVALID)

    def test_a_named_day_the_replay_does_not_reproduce_is_not_judged(self):
        """Condition 3 on numbers the control rejected: neither PASS nor FAIL.

        The tampered day is a 6 m field: it does not trigger, so judged it
        would be DID NOT FALL and reject the rule. But its stored rows are
        not what the replay gives -- it is not the day that was named.
        """
        verdicts, out = self.run_production(named=(('2026-09-29', TAMPERED),))
        self.assertEqual(verdicts, [replay.RUN_INVALID, replay.RUN_INVALID])
        self.assertEqual(replay.verdict_code(verdicts), 3)
        self.assertIn('UNTRUSTED, the replay does not reproduce this day', out)
        self.assertNotIn('DID NOT FALL', out)

    def test_condition_4_shows_the_largest_losses_largest_first(self):
        def result(unit, loss, triggered=True):
            return {'day': '2026-09-%02d' % (unit % 28 + 1), 'unit': unit,
                    'triggered': triggered, 'ha_today': 100.0,
                    'ha_cap': 100.0 - loss}
        results = [result(1000 + i, 0.1 * (i + 1)) for i in range(10)]
        results += [result(2000, 60.0), result(2001, 40.0),
                    result(2002, 80.0, triggered=False), result(2003, 0.0),
                    result(2004, 90.0)]
        kinds = {2004: 'excluded'}
        with unittest.mock.patch.object(replay, 'TOP_LOSSES', 3), \
                unittest.mock.patch.object(replay, 'NAMED_DAYS', ()):
            picked = replay.top_losses(results, kinds)
        self.assertEqual([r['unit'] for r in picked], [2000, 2001, 1009])

    def test_nothing_is_written(self):
        points = storage.points_path(self.folder, '202609')
        before = (self.digest(self.db), self.digest(points))
        self.run_production()
        self.assertEqual((self.digest(self.db), self.digest(points)), before)

    def test_the_exit_code_carries_the_verdict(self):
        base = ('--db', self.db, '--dir', self.folder, '--since', '2026-09-01',
                '--until', '2026-09-30')
        code, out, _err = self.run_main(*base)
        self.assertEqual(code, 3, out)                       # tampered: invalid
        self.untamper()
        original = replay.judge_day

        def broken(*args):
            return dict(original(*args), violations=['injected'])

        with unittest.mock.patch.object(replay, 'NAMED_DAYS',
                                        (('2026-09-26', ROADS),)):
            self.assertEqual(self.run_main(*base)[0], 0)
            with unittest.mock.patch.object(replay, 'judge_day',
                                            side_effect=broken):
                self.assertEqual(self.run_main(*base)[0], 4)

    def test_a_missing_kml_folder_is_refused_before_anything_runs(self):
        with unittest.mock.patch.object(replay, 'replay_production',
                                        side_effect=AssertionError('ran')):
            code, _out, err = self.run_main(
                '--db', self.db, '--dir', self.folder, '--since', '2026-09-01',
                '--kml', os.path.join(self.folder, 'no', 'a7.kml'))
        self.assertEqual(code, 2)
        self.assertIn('--kml', err)


class Invariants(unittest.TestCase):
    """Each invariant of condition 2, broken on purpose, is caught."""

    def judge(self, measures, today, capped):
        return replay.judge_day('2026-09-26', ROADS, METHOD_VERSION, [], today,
                                capped, METHOD_VERSION, measures)['violations']

    def test_a_clean_triggered_day_has_no_violation(self):
        self.assertEqual(self.judge((300.0, 6.0, 360.0, 10.0),
                                    [row(60.0, 360.0, 300.0)],
                                    [row(2.0, 10.0, 6.0)]), [])

    def test_more_hectares_than_today(self):
        self.assertEqual(self.judge((300.0, 6.0, 360.0, 10.0),
                                    [row(2.0, 360.0, 300.0)],
                                    [row(3.0, 10.0, 6.0)]),
                         ['more hectares than today'])

    def test_a_wider_alpha_and_one_above_the_ceiling(self):
        self.assertEqual(self.judge((50.0, 60.0, 60.0, 72.0), [], []),
                         ['wider alpha than today', 'alpha above 53.568'])

    def test_an_alpha_above_the_ceiling_alone(self):
        self.assertEqual(self.judge((50.0, 45.0, 60.0, 54.0), [], []),
                         ['alpha above 53.568'])

    def test_a_change_below_the_cap(self):
        """Polygon, spacing or anything else: below the cap nothing moves."""
        self.assertEqual(self.judge((30.0, 30.0, 36.0, 36.0),
                                    [row(5.0, 36.0, 30.0, 'a')],
                                    [row(5.0, 36.0, 30.0, 'b')]),
                         ['changed below the cap'])
        self.assertEqual(self.judge((30.0, 29.0, 36.0, 36.0), [], []),
                         ['changed below the cap'])
        self.assertEqual(self.judge((SPACING_CAP_M, SPACING_CAP_M, 53.568,
                                     53.568), [], []), [])

    def test_a_violation_fails_condition_2(self):
        result = replay.judge_day('2026-09-26', ROADS, METHOD_VERSION, [],
                                  [row(2.0, 360.0, 300.0)],
                                  [row(3.0, 10.0, 6.0)], 'older',
                                  (300.0, 6.0, 360.0, 10.0))
        self.assertTrue(result['consistent'])
        self.assertEqual(replay.condition_2([result], [].append), replay.FAIL)
        self.assertEqual(replay.condition_2([], [].append), replay.NOT_CHECKED)

    def test_the_verdict_code(self):
        self.assertEqual(replay.verdict_code([replay.PASS, replay.PASS]), 0)
        # 1 -- код любого необработанного исключения Python: FAIL его не берёт
        self.assertEqual(replay.verdict_code([replay.PASS, replay.FAIL,
                                              replay.RUN_INVALID]), 4)
        for other in (replay.NOT_CHECKED, replay.RUN_INVALID,
                      replay.NOT_EVALUATED, replay.OWNER_CHECK):
            self.assertEqual(replay.verdict_code([replay.PASS, other]), 3, other)


class NamedDays(unittest.TestCase):

    def test_the_six_days_are_the_ones_the_preregistration_names(self):
        """NAMED_DAYS is pinned in roadmap 2.11, condition 3; so is it here."""
        with open(ROADMAP, encoding='utf-8') as handle:
            text = handle.read()
        found = re.search(r'Шесть суток с допуском 300–981 м\*\* \((.*?)\)',
                          text, re.S)
        self.assertIsNotNone(found)
        named = set()
        for part in ' '.join(found.group(1).split()).split(';'):
            match = re.match(r'\s*(?:объекты\s+)?([\d,\sи]+?)\s+за\s+'
                             r'(\d\d)\.(\d\d)', part)
            if match is None:
                continue
            for unit in re.findall(r'\d+', match.group(1)):
                named.add(('2026-%s-%s' % (match.group(3), match.group(2)),
                           int(unit)))
        self.assertEqual(len(named), 6)
        self.assertEqual(named, set(replay.NAMED_DAYS))


class Sets(unittest.TestCase):
    """Condition 1: both sets, the 12.08 works by name, the rest by contour."""

    WORK = (1, 'MTZ test', '2026-08-01')

    def setUp(self):
        self.folder = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.folder, True)
        patch = unittest.mock.patch.object(replay, 'WORK_DAYS_1208', (self.WORK,))
        patch.start()
        self.addCleanup(patch.stop)
        self.zones = self.write_zones({
            '101': ('Поле 1', [(-20, -20), (320, -20), (320, 320), (-20, 320)]),
            '102': ('Дорога 7', [(-50, -50), (2050, -50), (2050, 350),
                                 (-50, 350)])})

    def write_zones(self, zones, name='wialon_zones.json'):
        raw = {zone_id: {'type': 2, 'name': title,
                         'points': [{'x': lon, 'y': lat} for lon, lat in
                                    (xy_to_lonlat(e, n) for e, n in corners)]}
               for zone_id, (title, corners) in zones.items()}
        path = os.path.join(self.folder, name)
        with open(path, 'w', encoding='utf-8') as handle:
            json.dump(raw, handle, ensure_ascii=False)
        return path

    def write_1208(self, name, days):
        """The 12.08 layout (wialon_probe5_spraying.py): with a date."""
        path = os.path.join(self.folder, name)
        with open(path, 'w', encoding='utf-8-sig', newline='') as handle:
            handle.write('unit_id;date;time;lat;lon;speed;course;sats\n')
            for unit, day, track in days:
                for t, lon, lat, speed in track:
                    t = int(t) + 8 * 3600
                    handle.write('%d;%s;%02d:%02d:%02d;%.7f;%.7f;%.1f;0;10\n'
                                 % (unit, day, t // 3600, t % 3600 // 60, t % 60,
                                    lat, lon, speed))
        return path

    def write_0727(self, name, units, day='2026-07-27'):
        """The 27.07 set as the probes write it: dated 2026-07-27.

        `day=None` writes the same tracks without a date column -- a layout
        the tool still accepts as one day.
        """
        if day is not None:
            return self.write_1208(name, [(unit, day, track)
                                          for unit, track in units])
        path = os.path.join(self.folder, name)
        with open(path, 'w', encoding='utf-8-sig', newline='') as handle:
            handle.write('unit_id;time;lon;lat;speed\n')
            for unit, track in units:
                for t, lon, lat, speed in track:
                    t = int(t) + 8 * 3600
                    handle.write('%d;%02d:%02d:%02d;%.7f;%.7f;%.1f\n'
                                 % (unit, t // 3600, t % 3600 // 60, t % 60,
                                    lon, lat, speed))
        return path

    def field(self, spacing=6.0):
        return shuttle_track(300.0, 300.0, pass_spacing_m=spacing)

    def run_sets(self, *tracks, zones=None):
        argv = []
        for path in tracks:
            argv += ['--tracks', path]
        return self.run_main(*argv, '--zones', zones or self.zones)

    def digest(self, path):
        with open(path, 'rb') as handle:
            return hashlib.sha256(handle.read()).hexdigest()

    def run_main(self, *argv):
        with contextlib.redirect_stdout(io.StringIO()) as out, \
                contextlib.redirect_stderr(io.StringIO()) as err:
            code = replay.main(list(argv))
        return code, out.getvalue(), err.getvalue()

    def test_two_sets_of_field_work_pass(self):
        early = self.write_0727('verify_tracks.csv', [(11, self.field())])
        late = self.write_1208('verify2_tracks.csv',
                               [(1, '2026-08-01', self.field(37.2))])
        code, out, _err = self.run_sets(early, late)
        self.assertEqual(code, 0, out)
        points = len(self.field())
        self.assertIn('verify_tracks.csv: machine-days 1 (day rows), contours '
                      'entered 2 (zone rows), points %d, dates 2026-07-27; '
                      'triggered: 0 day row(s), 0 zone row(s); changed: 0 day '
                      'row(s), 0 zone row(s)' % points, out)
        self.assertIn('27.07 set: 1 machine-days, %d points (recorded: 7 '
                      'tractors, 17440 points)' % points, out)
        self.assertIn('12.08 set: all 1 works read, %d points (recorded: 27174)'
                      % len(self.field(37.2)), out)
        self.assertIn('CONDITION 1 (no row of the two sets changed or triggered, '
                      'so none of the 32 works did): PASS', out)
        self.assertNotIn('superset', out)

    def test_the_0727_set_is_known_by_its_date_not_by_a_missing_column(self):
        """03.10: the owner's verify_tracks.csv carries a date column.

        The first edition looked for a file WITHOUT dates, called the 27.07
        set unread and gave NOT CHECKED on a run where both sets were read in
        full. Dated 2026-07-27 or undated -- both are the 27.07 set; another
        day is not, and the log says which days it saw.
        """
        late = self.write_1208('verify2_tracks.csv',
                               [(1, '2026-08-01', self.field())])
        for day in ('2026-07-27', None):
            early = self.write_0727('verify_tracks.csv', [(11, self.field())],
                                    day=day)
            code, out, _err = self.run_sets(early, late)
            self.assertEqual(code, 0, (day, out))
            self.assertIn('27.07 set: 1 machine-days', out)
        early = self.write_0727('verify_tracks.csv', [(11, self.field())],
                                day='2026-07-26')
        code, out, _err = self.run_sets(early, late)
        self.assertEqual(code, 3, out)
        self.assertIn('not checked: the 27.07 set (17 works: tracks of '
                      '2026-07-27, 7 tractors, 17440 points) was not read; '
                      'dates seen: 2026-07-26, 2026-08-01', out)

    def test_a_changed_work_of_1208_fails_without_asking(self):
        """A 12.08 work is a whole machine-day: if the day changed, it did."""
        early = self.write_0727('verify_tracks.csv', [(11, self.field())])
        late = self.write_1208('verify2_tracks.csv',
                               [(1, '2026-08-01', field_and_roads())])
        code, out, _err = self.run_sets(early, late)
        self.assertEqual(code, 4, out)
        self.assertRegex(out, r'day\s+unit 1\s+day 2026-08-01 zone -\s+'
                              r'spacing \d+\.\d\d -> 6\.\d\d .*CHANGED\s+MTZ test')
        self.assertIn('CONDITION 1: FAIL -- 1 of the 15 works of 12.08 changed',
                      out)

    def test_other_rows_go_to_the_owner_with_names_alpha_and_step(self):
        early = self.write_0727('verify_tracks.csv', [(11, self.field())])
        late = self.write_1208('verify2_tracks.csv', [
            (1, '2026-08-01', self.field()),
            (5, '2026-08-01', slow_loop(TWO_ROADS))])
        code, out, _err = self.run_sets(early, late)
        self.assertEqual(code, 3, out)
        self.assertRegex(out, r'day\s+unit 5\s+day 2026-08-01 zone -\s+'
                              r'spacing 299\.\d\d -> -\s+ha 60\.\d+ -> 0\.0000\s+'
                              r'CHANGED')
        self.assertRegex(out, r'zone\s+unit 5\s+day 2026-08-01 zone 102\s+'
                              r'spacing 299\.\d\d -> -\s+ha 60\.\d+ -> 0\.\d+\s+'
                              r'CHANGED\s+Doroga 7')
        self.assertRegex(out, r'was: site 60\.\d+ ha\s+alpha 359\.\d\d\s+'
                              r'spacing 299\.\d\d\s+zone 102\s+Doroga 7')
        self.assertNotRegex(out, r'unit 1 ')
        self.assertIn('verify2_tracks.csv: machine-days 2 (day rows), contours '
                      'entered 4 (zone rows), points %d, dates 2026-08-01; '
                      'triggered: 1 day row(s), 1 zone row(s); changed: 1 day '
                      'row(s), 1 zone row(s)'
                      % (len(self.field()) + len(slow_loop(TWO_ROADS))), out)
        self.assertIn('CONDITION 1: OWNER CHECK -- 2 row(s) above changed or '
                      'triggered. A DAY row: the rule changed the step and alpha '
                      'of EVERY site of that machine-day', out)

    def test_the_now_line_names_the_work_contour(self):
        """A field between two roads: the work shows up only as «now».

        Today the day's one site is the whole corridor, named after the road
        contour; with the cap it is the field, named after the field. The
        field's own contour row does not change, so the «now» line is the only
        place its name appears.
        """
        zones = self.write_zones({
            '101': ('Поле 1', [(80, 80), (220, 80), (220, 220), (80, 220)]),
            '102': ('Дорога 7', [(-50, -50), (2050, -50), (2050, 350),
                                 (-50, 350)])}, name='corridor.json')
        work = shuttle_track(100.0, 100.0, pass_spacing_m=6.0)
        work = [(t, *xy_to_lonlat(*self.east_north(lon, lat, 100.0)), speed)
                for t, lon, lat, speed in work]
        roads = slow_loop(TWO_ROADS, loops=6, start_time=work[-1][0] + 600)
        early = self.write_0727('verify_tracks.csv', [(11, self.field())])
        late = self.write_1208('verify2_tracks.csv',
                               [(1, '2026-08-01', self.field()),
                                (5, '2026-08-01', work + roads)])
        _code, out, _err = self.run_sets(early, late, zones=zones)
        self.assertRegex(out, r'was: site \d+\.\d+ ha .*zone 102\s+Doroga 7')
        self.assertRegex(out, r'now: site 0\.\d+ ha\s+alpha 10\.00\s+'
                              r'spacing 6\.\d\d\s+zone 101\s+Pole 1')

    @staticmethod
    def east_north(lon, lat, shift):
        from gps.tests.test_area import BASE_LAT, BASE_LON, M_PER_DEG_LAT, \
            M_PER_DEG_LON
        return ((lon - BASE_LON) * M_PER_DEG_LON + shift,
                (lat - BASE_LAT) * M_PER_DEG_LAT + shift)

    def test_a_triggered_row_is_shown_even_when_nothing_changed(self):
        """The amendment: the run prints whether the rule triggered.

        A day with no site either way stays «same» while the rule fired on
        it; the owner still sees it.
        """
        quiet = {'set': 'x.csv', 'unit': 7, 'day': '-', 'path': 'day',
                 'zone': None, 'zone_name': '', 'ha_today': 0.0, 'ha_cap': 0.0,
                 'spacing': 60.0, 'spacing_cap': None, 'triggered': True,
                 'same': True, 'sites_today': [], 'sites_cap': []}
        work = dict(quiet, unit=1, day='2026-08-01', spacing=6.0,
                    spacing_cap=6.0, triggered=False)
        zone = dict(work, path='zone', zone=101, zone_name='Поле 1')
        out = []
        verdict = replay.report_sets([('x.csv', [quiet, work, zone])],
                                     {101: 'Поле 1'}, out.append)
        self.assertEqual(verdict, replay.OWNER_CHECK)
        text = '\n'.join(out)
        self.assertRegex(text, r'day\s+unit 7\s+day -\s+zone -\s+'
                               r'spacing 60\.00 -> -\s+ha 0\.0000 -> 0\.0000\s+same')
        self.assertIn('triggered: 1 day row(s), 0 zone row(s); changed: 0 day '
                      'row(s), 0 zone row(s)', text)
        self.assertIn('OWNER CHECK -- 1 row(s)', text)

    def test_one_set_alone_is_not_checked(self):
        early = self.write_0727('verify_tracks.csv', [(11, self.field())])
        code, out, _err = self.run_sets(early)
        self.assertEqual(code, 3)
        self.assertIn('not checked: 1 of the 1 works of 12.08 were not read: '
                      '1 2026-08-01', out)
        self.assertIn('CONDITION 1: NOT CHECKED', out)
        late = self.write_1208('verify2_tracks.csv',
                               [(1, '2026-08-01', self.field())])
        code, out, _err = self.run_sets(late)
        self.assertEqual(code, 3)
        self.assertIn('not checked: the 27.07 set (17 works: tracks of '
                      '2026-07-27, 7 tractors, 17440 points) was not read; '
                      'dates seen: 2026-08-01', out)

    def test_an_empty_set_next_to_a_good_one_is_not_checked(self):
        early = self.write_0727('verify_tracks.csv', [(11, self.field())])
        late = self.write_1208('verify2_tracks.csv', [])
        code, out, _err = self.run_sets(early, late)
        self.assertEqual(code, 3)
        self.assertIn('not checked: verify2_tracks.csv gave no machine-day', out)

    def test_a_set_that_never_enters_a_contour_is_not_checked(self):
        early = self.write_0727('verify_tracks.csv', [(11, self.field())])
        late = self.write_1208('verify2_tracks.csv', [
            (1, '2026-08-01', [(t, *xy_to_lonlat(e + 5000.0, n + 5000.0), v)
                               for t, (e, n), v in self.moved(self.field())])])
        code, out, _err = self.run_sets(early, late)
        self.assertEqual(code, 3, out)
        self.assertIn('not checked: verify2_tracks.csv entered no contour', out)

    def moved(self, track):
        return [(t, self.east_north(lon, lat, 0.0), v) for t, lon, lat, v in track]

    def test_a_file_without_a_needed_column_is_bad_input(self):
        early = self.write_0727('verify_tracks.csv', [(11, self.field())])
        broken = os.path.join(self.folder, 'verify2_tracks.csv')
        with open(broken, 'w', encoding='utf-8') as handle:
            handle.write('unit_id;date;time;lat;lon;course\n'
                         '1;2026-08-01;08:00:00;39.7;64.4;0\n')
        code, _out, err = self.run_sets(early, broken)
        self.assertEqual(code, 2)
        self.assertIn('verify2_tracks.csv has no column(s) speed', err)

    def test_an_unreadable_row_is_skipped_and_counted(self):
        """A blank or missing cell (Wialon gave none) must not kill the run."""
        late = self.write_1208('verify2_tracks.csv',
                               [(1, '2026-08-01', self.field())])
        with open(late, 'a', encoding='utf-8', newline='') as handle:
            handle.write('1;2026-08-01;12:00:00;39.7;64.4;;0;10\n')
            handle.write('1;2026-08-01;;39.7;64.4;5.0;0;10\n')
            handle.write('1;2026-08-01\n')
        days, skipped = replay.load_csv_days(late)
        self.assertEqual(skipped, 3)
        clean = replay.load_csv_days(self.write_1208(
            'clean.csv', [(1, '2026-08-01', self.field())]))
        self.assertEqual(clean[1], 0)
        self.assertEqual(days, clean[0])
        early = self.write_0727('verify_tracks.csv', [(11, self.field())])
        code, out, _err = self.run_sets(early, late)
        self.assertEqual(code, 0, out)
        self.assertIn('verify2_tracks.csv: unreadable rows skipped 3', out)

    def test_bad_input_is_refused_and_nothing_is_written(self):
        tracks = self.write_1208('fields.csv', [(1, '2026-08-01', self.field())])
        before = (self.digest(tracks), self.digest(self.zones))
        self.assertEqual(self.run_main('--tracks', tracks)[0], 2)          # no zones
        self.assertEqual(self.run_main('--tracks', tracks, '--zones',
                                       self.zones, '--db', 'x.db')[0], 2)  # both
        self.assertEqual(self.run_main('--db', os.path.join(self.folder, 'no.db'),
                                       '--dir', self.folder, '--since',
                                       '2026-09-01')[0], 2)
        self.assertFalse(os.path.exists(os.path.join(self.folder, 'no.db')))
        self.run_main('--tracks', tracks, '--zones', self.zones)
        self.assertEqual((self.digest(tracks), self.digest(self.zones)), before)


class WorkDays(unittest.TestCase):

    def test_the_1208_works_are_the_ones_the_probe_pulled(self):
        """WORK_DAYS_1208 is copied from wialon_probe5_spraying.UNIT_DAYS.

        Read from the probe's source as a literal, not imported: the probe
        is a network script, and the test needs only its list.
        """
        import ast
        path = os.path.join(os.path.dirname(ROADMAP), '..', 'tools',
                            'wialon_probe5_spraying.py')
        with open(path, encoding='utf-8') as handle:
            tree = ast.parse(handle.read())
        found = [node.value for node in tree.body
                 if isinstance(node, ast.Assign)
                 and any(getattr(t, 'id', '') == 'UNIT_DAYS' for t in node.targets)]
        self.assertEqual(len(found), 1)
        self.assertEqual(tuple(tuple(item) for item in ast.literal_eval(found[0])),
                         replay.WORK_DAYS_1208)
        self.assertEqual(len(replay.WORK_DAYS_1208), 15)


if __name__ == '__main__':
    unittest.main()
