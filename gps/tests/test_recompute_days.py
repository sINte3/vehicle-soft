# -*- coding: utf-8 -*-
"""tools/gps_recompute_days.py -- пересчёт прошедших суток действующим методом.

Окружение расчёта: пересчёт идёт настоящим `gps.daily`. Здесь держится то,
что делает пересчёт безопасным для рабочей базы:
  * план ничего не пишет -- ни в базу, ни в файл точек;
  * запись пересчитывает считаемые объекты правилом и метит строки новой
    версией; сутки ниже порога выходят бит в бит прежними;
  * строки исключённых объектов и дни вне окна не трогаются;
  * ответ оператора в окне -- отказ без единой записи; ответ, появившийся
    посреди пересчёта, или сообщение расчёта о потерянном ответе --
    остановка с кодом 3 до следующих суток;
  * сбой или падение одних суток не обрывает остальные и даёт код 5;
  * окно не заходит в сегодня, неверный ввод и папка без точек -- код 2
    без записи;
  * строки, оставшиеся прежним методом, названы поимённо; повтор после
    прерванного прогона говорит, что часть окна уже пересчитана;
  * вывод -- только ASCII, в том числе кириллические ответы операторов.
"""
import contextlib
import hashlib
import io
import os
import shutil
import sqlite3
import tempfile
import unittest
import unittest.mock
from datetime import datetime

import migrate_gps_daily_001 as migration
import migrate_gps_verdicts_001 as verdicts_migration
import tools.gps_recompute_days as recompute
from gps import daily
from gps.area import METHOD_VERSION, PREVIOUS_METHOD_VERSION
from gps.daily import compute_day, write_day
from gps.tests.test_area import TWO_ROADS, shuttle_track, slow_loop
from gps_collector import config as collector_config
from gps_collector import storage
from tests.test_gps_units_inventory import DDL

ROADS, FIELD, EXCLUDED, GONE = 393, 387, 392, 395
TODAY = '2026-10-07'


def shifted(track, day, hour=8):
    start = int(datetime.strptime(day, '%Y-%m-%d')
                .replace(tzinfo=collector_config.TZ).timestamp()) + hour * 3600
    return [(start + int(t), lon, lat, speed, 10) for t, lon, lat, speed in track]


class Window(unittest.TestCase):
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
            con.execute(verdicts_migration.CREATE_GPS_VERDICTS)
            con.execute("INSERT INTO vialon_mappings (vialon_name, wialon_id, "
                        "equipment_id, skip) VALUES ('Чужой', ?, NULL, 1)",
                        (EXCLUDED,))
            con.commit()
            # база, какой она была до 07.10: прежний метод и его версия
            self.day(con, '2026-09-26', ROADS, slow_loop(TWO_ROADS))
            self.day(con, '2026-09-27', FIELD,
                     shuttle_track(300.0, 300.0, pass_spacing_m=14.0))
            self.day(con, '2026-09-24', EXCLUDED, slow_loop(TWO_ROADS))
            self.day(con, '2026-08-31', ROADS, slow_loop(TWO_ROADS))
            # строка есть, а точек на диске уже нет
            self.day(con, '2026-09-15', GONE, slow_loop(TWO_ROADS),
                     with_points=False)
        finally:
            con.close()

    def day(self, con, day, unit, track, with_points=True):
        points = shifted(track, day)
        if with_points:
            storage.write_points(self.folder,
                                 [(unit, t, lon, lat, speed, None, sats)
                                  for t, lon, lat, speed, sats in points])
        write_day(con, day, unit, compute_day(points, overflow_cap=False),
                  '2026-10-01 01:00:00')

    def run_tool(self, *argv, today=TODAY):
        out = []
        with contextlib.redirect_stdout(io.StringIO()) as printed, \
                contextlib.redirect_stderr(io.StringIO()) as err:
            code = recompute.main(list(argv) + ['--db', self.db, '--dir',
                                                self.folder],
                                  out=out.append, today=today)
        return code, '\n'.join(out), printed.getvalue(), err.getvalue()

    def rows(self, unit, day):
        con = sqlite3.connect(self.db)
        try:
            aggregate = con.execute(
                'SELECT reason, method_version FROM gps_daily_aggregates '
                'WHERE wialon_id = ? AND work_date = ?', (unit, day)).fetchone()
            sites = con.execute(
                'SELECT area_ha, polygon_geojson, alpha_used_m FROM '
                'gps_work_polygons WHERE wialon_id = ? AND work_date = ? '
                'ORDER BY site_number', (unit, day)).fetchall()
        finally:
            con.close()
        return aggregate, sites

    def digest(self, path):
        with open(path, 'rb') as handle:
            return hashlib.sha256(handle.read()).hexdigest()

    def files(self):
        return (self.digest(self.db),
                self.digest(storage.points_path(self.folder, '202609')))


class Plan(Window):

    def test_the_plan_writes_nothing_and_describes_the_window(self):
        before = self.files()
        code, out, _printed, _err = self.run_tool('--since', '2026-09-01',
                                                  '--until', '2026-09-30')
        self.assertEqual(code, 0, out)
        self.assertEqual(self.files(), before)
        roads_ha = sum(area for area, _g, _a in self.rows(ROADS, '2026-09-26')[1])
        field_ha = sum(area for area, _g, _a in self.rows(FIELD, '2026-09-27')[1])
        self.assertIn('method of this code: overflow-cap-2026-10-07 (previous: '
                      'adaptive-alpha-2026-08-12)', out)
        self.assertIn('window: 2026-09-01 .. 2026-09-30, 30 day(s)', out)
        self.assertIn('2026-09: rows 4; published at counted objects 3, %.2f ha; '
                      'rows of excluded objects 1 (left as they are); method '
                      'versions: adaptive-alpha-2026-08-12 4'
                      % (roads_ha + field_ha
                         + sum(a for a, _g, _x in self.rows(GONE, '2026-09-15')[1])),
                      out)
        self.assertIn('operator answers (work/passage) in the window: 0', out)
        self.assertIn('work-order reviews in the window: 0', out)
        self.assertIn('days with points in %s: 3 of 30' % self.folder, out)
        self.assertIn('PLAN ONLY: nothing was written', out)

    def test_a_folder_without_points_is_refused_not_a_success(self):
        """A wrong --dir must not end in «recomputed, nothing changed»."""
        empty = tempfile.mkdtemp(dir=self.folder)
        before = self.files()
        for extra in ([], ['--apply']):
            out = []
            with contextlib.redirect_stdout(io.StringIO()) as printed:
                code = recompute.main(['--since', '2026-09-01', '--until',
                                       '2026-09-30', '--db', self.db, '--dir',
                                       empty] + extra, out=out.append,
                                      today=TODAY)
            self.assertEqual(code, 2, extra)
            self.assertIn('ERROR: no point file holds any day of the window',
                          '\n'.join(out))
            self.assertEqual(printed.getvalue(), '')
        self.assertEqual(self.files(), before)

    def test_operator_answers_reach_the_console_in_ascii(self):
        """The app stores «работа»/«проезд»; the console takes ASCII only."""
        con = sqlite3.connect(self.db)
        try:
            con.execute("UPDATE gps_work_polygons SET operator_label = 'проезд' "
                        "WHERE wialon_id = ? AND work_date = '2026-09-26'",
                        (ROADS,))
            con.commit()
        finally:
            con.close()
        code, out, _p, err = self.run_tool('--since', '2026-09-01',
                                           '--until', '2026-09-30')
        self.assertEqual(code, 0)
        self.assertIn('answer 2026-09-26 unit %d site 1: proezd' % ROADS, out)
        self.assertTrue(out.isascii())
        self.assertTrue(err.isascii())

    def test_the_window_ends_yesterday_and_never_reaches_today(self):
        code, out, _p, _e = self.run_tool('--since', '2026-09-01',
                                          today='2026-09-27')
        self.assertEqual(code, 0)
        self.assertIn('window: 2026-09-01 .. 2026-09-26, 26 day(s)', out)
        for until in ('2026-09-27', '2026-09-28'):
            code, _out, _p, err = self.run_tool('--since', '2026-09-01',
                                                '--until', until,
                                                today='2026-09-27')
            self.assertEqual(code, 2, until)
            self.assertIn('is not in the past', err)

    def test_bad_input_is_refused_and_nothing_is_created(self):
        self.assertEqual(self.run_tool('--since', '2026-09-30',
                                       '--until', '2026-09-01')[0], 2)
        self.assertEqual(self.run_tool('--since', '2026-9-1x')[0], 2)
        missing = os.path.join(self.folder, 'none', 'transport.db')
        with contextlib.redirect_stderr(io.StringIO()):
            code = recompute.main(['--since', '2026-09-01', '--db', missing,
                                   '--dir', self.folder], out=[].append,
                                  today=TODAY)
        self.assertEqual(code, 2)
        self.assertFalse(os.path.exists(missing))


class Apply(Window):

    def test_counted_days_are_recomputed_by_the_rule(self):
        roads_before = self.rows(ROADS, '2026-09-26')
        field_before = self.rows(FIELD, '2026-09-27')
        excluded_before = self.rows(EXCLUDED, '2026-09-24')
        outside_before = self.rows(ROADS, '2026-08-31')
        self.assertEqual(roads_before[0], (None, PREVIOUS_METHOD_VERSION))
        self.assertAlmostEqual(sum(a for a, _g, _x in roads_before[1]), 60.0,
                               delta=0.5)
        code, out, printed, _err = self.run_tool('--since', '2026-09-01',
                                                 '--until', '2026-09-30',
                                                 '--apply')
        self.assertEqual(code, 0, out + printed)
        # дороги: правило убирает фантом, строка помечена новой версией
        self.assertEqual(self.rows(ROADS, '2026-09-26'),
                         ((None, METHOD_VERSION), []))
        # поле ниже порога: те же участки бит в бит, новая версия
        field_after = self.rows(FIELD, '2026-09-27')
        self.assertEqual(field_after[0], (None, METHOD_VERSION))
        self.assertEqual(field_after[1], field_before[1])
        # исключённый объект и день вне окна не тронуты
        self.assertEqual(self.rows(EXCLUDED, '2026-09-24'), excluded_before)
        self.assertEqual(self.rows(ROADS, '2026-08-31'), outside_before)
        field_ha = sum(a for a, _g, _x in field_before[1])
        gone_ha = sum(a for a, _g, _x in self.rows(GONE, '2026-09-15')[1])
        roads_ha = sum(a for a, _g, _x in roads_before[1])
        self.assertIn('2026-09: %.2f ha -> %.2f ha (%+.2f); published '
                      'machine-days 3 -> 3'
                      % (roads_ha + field_ha + gone_ha, field_ha + gone_ha,
                         -roads_ha), out)
        self.assertIn('machine-days with more hectares than before: 0', out)
        self.assertIn('rows of counted objects still not on '
                      'overflow-cap-2026-10-07: 1', out)
        self.assertIn('  2026-09-15 unit %d: adaptive-alpha-2026-08-12' % GONE,
                      out)
        self.assertIn('RESULT: RECOMPUTED 30 day(s) by overflow-cap-2026-10-07; '
                      'rows of counted objects left on another method: 1', out)
        self.assertIn('days without points (nothing to recompute there): 27', out)
        self.assertNotIn('already on overflow-cap', out)
        self.assertTrue(out.isascii())

    def test_an_operator_answer_in_the_window_refuses_without_writing(self):
        con = sqlite3.connect(self.db)
        try:
            con.execute("UPDATE gps_work_polygons SET operator_label = 'work', "
                        "decided_at = '2026-10-01 10:00:00' WHERE wialon_id = ? "
                        "AND work_date = '2026-09-27'", (FIELD,))
            con.commit()
        finally:
            con.close()
        before = self.files()
        code, out, printed, _err = self.run_tool('--since', '2026-09-01',
                                                 '--until', '2026-09-30',
                                                 '--apply')
        self.assertEqual(code, 3)
        self.assertEqual(self.files(), before)
        self.assertIn('answer 2026-09-27 unit %d site 1: work' % FIELD, out)
        self.assertIn('REFUSED: 1 operator answer(s)', out)
        self.assertEqual(printed, '')            # gps.daily never ran

    def test_an_answer_outside_the_window_does_not_refuse(self):
        con = sqlite3.connect(self.db)
        try:
            con.execute("UPDATE gps_work_polygons SET operator_label = 'work' "
                        "WHERE work_date = '2026-08-31'")
            con.execute("INSERT INTO gps_verdicts (work_order_id, work_date, "
                        "unit, verdict, decision, reviewed_at) VALUES "
                        "(1, '2026-09-27', 'ha', 'green', 'confirmed', "
                        "'2026-10-01 10:00:00')")
            con.commit()
        finally:
            con.close()
        code, out, _p, _e = self.run_tool('--since', '2026-09-01',
                                          '--until', '2026-09-30', '--apply')
        self.assertEqual(code, 0, out)
        self.assertIn('work-order reviews in the window: 1', out)

    def test_an_answer_given_during_the_run_stops_before_its_day(self):
        """The program keeps running; an operator may answer mid-window.

        The answer appears while 2026-09-01 is being computed; by 26.09 the
        roads are recomputed, at 27.09 the check before the day finds it and
        the run stops -- that day and the rest keep the previous method.
        """
        real = daily.main
        calls = []

        def answering(argv):
            if not calls:
                con = sqlite3.connect(self.db)
                try:
                    con.execute("UPDATE gps_work_polygons SET operator_label = "
                                "'работа' WHERE wialon_id = ? AND work_date = "
                                "'2026-09-27'", (FIELD,))
                    con.commit()
                finally:
                    con.close()
            calls.append(argv[1])
            return real(argv)

        with unittest.mock.patch.object(recompute.daily, 'main',
                                        side_effect=answering):
            code, out, _p, _e = self.run_tool('--since', '2026-09-01',
                                              '--until', '2026-09-30', '--apply')
        self.assertEqual(code, 3, out)
        self.assertIn('REFUSED: 1 operator answer(s) appeared on 2026-09-27 '
                      'while the window was being recomputed', out)
        self.assertEqual(calls[-1], '2026-09-26')
        self.assertEqual(self.rows(ROADS, '2026-09-26')[0][1], METHOD_VERSION)
        self.assertEqual(self.rows(FIELD, '2026-09-27')[0][1],
                         PREVIOUS_METHOD_VERSION)

    def test_an_answer_the_computation_reports_lost_stops_the_run(self):
        """daily's own line about a lost answer is a stop, not a log line."""
        real = daily.main

        def losing(argv):
            code = real(argv)
            if argv[1] == '2026-09-26':
                print('    VNIMANIE: otvetov operatora poteryano pri pereschete: 1')
            return code

        with unittest.mock.patch.object(recompute.daily, 'main',
                                        side_effect=losing):
            code, out, _p, _e = self.run_tool('--since', '2026-09-01',
                                              '--until', '2026-09-30', '--apply')
        self.assertEqual(code, 3, out)
        self.assertIn('the computation reports operator answers lost on '
                      '2026-09-26', out)
        self.assertIn('otvetov operatora poteryano', out)       # passed through
        self.assertEqual(self.rows(FIELD, '2026-09-27')[0][1],
                         PREVIOUS_METHOD_VERSION)

    def test_a_crash_of_one_day_is_a_failure_not_an_abort(self):
        real = daily.main

        def crashing(argv):
            if argv[1] == '2026-09-26':
                raise RuntimeError('boom')
            return real(argv)

        with unittest.mock.patch.object(recompute.daily, 'main',
                                        side_effect=crashing):
            code, out, _p, _e = self.run_tool('--since', '2026-09-01',
                                              '--until', '2026-09-30', '--apply')
        self.assertEqual(code, 5, out)
        self.assertIn('2026-09-26 (exit crash: RuntimeError)', out)
        self.assertIn('counted objects, published machine-days', out)
        self.assertEqual(self.rows(FIELD, '2026-09-27')[0][1], METHOD_VERSION)

    def test_a_rerun_says_part_of_the_window_was_already_recomputed(self):
        real = daily.main

        def flaky(argv):
            return 5 if argv[1] == '2026-09-26' else real(argv)

        with unittest.mock.patch.object(recompute.daily, 'main',
                                        side_effect=flaky):
            self.assertEqual(self.run_tool('--since', '2026-09-01', '--until',
                                           '2026-09-30', '--apply')[0], 5)
        code, out, _p, _e = self.run_tool('--since', '2026-09-01',
                                          '--until', '2026-09-30', '--apply')
        self.assertEqual(code, 0, out)
        self.assertIn('rows of counted objects already on overflow-cap-2026-10-07 '
                      'before this run: 1 -- this run continues an earlier one',
                      out)
        self.assertEqual(self.rows(ROADS, '2026-09-26')[0][1], METHOD_VERSION)

    def test_a_failed_day_does_not_stop_the_others(self):
        real = daily.main

        def flaky(argv):
            if argv[1] == '2026-09-26':
                return 5
            return real(argv)

        with unittest.mock.patch.object(recompute.daily, 'main',
                                        side_effect=flaky):
            code, out, _p, _e = self.run_tool('--since', '2026-09-01',
                                              '--until', '2026-09-30', '--apply')
        self.assertEqual(code, 5)
        self.assertIn('days that did not compute completely: 2026-09-26 (exit 5)',
                      out)
        self.assertIn('RESULT: RECOMPUTED WITH FAILURES', out)
        self.assertEqual(self.rows(ROADS, '2026-09-26')[0][1],
                         PREVIOUS_METHOD_VERSION)
        self.assertEqual(self.rows(FIELD, '2026-09-27')[0][1], METHOD_VERSION)


if __name__ == '__main__':
    unittest.main()
