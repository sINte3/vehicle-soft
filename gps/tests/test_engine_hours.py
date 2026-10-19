# -*- coding: utf-8 -*-
"""GPS-26 -- суточные часы зажигания в gps_daily_engine.

Держится: строка на объект-сутки по правилу R; повторный прогон ничего не
пересчитывает; дозаполнение зажигания, поздняя точка и новая версия правила
пересчитывают ровно свои сутки; сутки без точек на диске сохраняют строку;
пара через полночь не идёт ни в одни сутки; сломанный объект-сутки не уносит
прогон; коды выхода.

Run:
  python -m unittest gps.tests.test_engine_hours
"""

import io
import os
import sqlite3
import sys
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from unittest import mock

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(
    os.path.abspath(__file__))))
if REPO_ROOT not in sys.path:
    sys.path.insert(0, REPO_ROOT)

import migrate_gps_engine_001 as engine_mig                        # noqa: E402
from gps import engine_hours                                       # noqa: E402
from gps_collector import storage                                  # noqa: E402
from gps_collector.tests.support import LAT, LON, epoch            # noqa: E402

DAY = '2026-10-15'
QUIET = lambda *args, **kwargs: None                               # noqa: E731


def point(unit_id, stamp, ign=None):
    row = (unit_id, stamp, LON, LAT, 5.0, 90, 14)
    return row if ign is None else row + (ign,)


def run_of(unit_id, start, minutes, ign, step=60):
    return [point(unit_id, start + step * i, ign)
            for i in range(int(minutes * 60 / step))]


class Base(unittest.TestCase):
    def setUp(self):
        self.folder = tempfile.mkdtemp()
        self.db = os.path.join(self.folder, 'transport.db')
        con = sqlite3.connect(self.db)
        try:
            con.execute(engine_mig.CREATE_GPS_DAILY_ENGINE)
            for _, statement in engine_mig.INDEXES:
                con.execute(statement)
            con.commit()
        finally:
            con.close()

    def run_days(self, days=(DAY,)):
        return engine_hours.run_days(self.folder, self.db, list(days), log=QUIET)

    def rows(self, day=DAY):
        con = sqlite3.connect(self.db)
        try:
            return {r[0]: r[1:] for r in con.execute(
                'SELECT wialon_id, on_seconds, off_seconds, unknown_seconds, '
                'points_total, points_with_ign, method_version, computed_at '
                'FROM gps_daily_engine WHERE work_date = ?', (day,))}
        finally:
            con.close()


class DailyRows(Base):
    def setUp(self):
        super().setUp()
        start = epoch(DAY + ' 08:00')
        # 101: час включено (шаг 60 с), затем полчаса выключено;
        # 102: точки без зажигания (лягли до GPS-24).
        storage.write_points(self.folder,
                             run_of(101, start, 60, 1.0)
                             + run_of(101, start + 3600, 30, 0.0)
                             + run_of(102, start, 20, None))

    def test_one_row_per_object_day_by_rule_r(self):
        summary, failures = self.run_days()
        self.assertEqual(failures, [])
        rows = self.rows()
        self.assertEqual(sorted(rows), [101, 102])
        on, off, unknown, n, n_ign, version, _ = rows[101]
        self.assertEqual((on, off, unknown, n, n_ign), (3600, 29 * 60, 0, 90, 90))
        self.assertEqual(version, engine_hours.METHOD_VERSION)
        self.assertEqual(rows[102][:5], (0, 0, 19 * 60, 20, 0))
        self.assertEqual((summary.recomputed, summary.with_ignition), (2, 1))

    def test_a_second_run_recomputes_nothing(self):
        self.run_days()
        before = self.rows()
        summary, _ = self.run_days()
        self.assertEqual((summary.recomputed, summary.unchanged), (0, 2))
        self.assertEqual(self.rows(), before)

    def test_refilled_ignition_recomputes_that_object_day_only(self):
        self.run_days()
        before = self.rows()
        start = epoch(DAY + ' 08:00')
        storage.fill_ignition(self.folder, [(102, start + 60 * i, 1.0)
                                            for i in range(20)])
        summary, _ = self.run_days()
        self.assertEqual(summary.recomputed, 1)
        after = self.rows()
        self.assertEqual(after[102][:5], (19 * 60, 0, 0, 20, 20))
        self.assertEqual(after[101], before[101])

    def test_a_late_point_recomputes_its_day(self):
        self.run_days()
        storage.write_points(self.folder,
                             [point(101, epoch(DAY + ' 23:00'), 1.0)])
        summary, _ = self.run_days()
        self.assertEqual(summary.recomputed, 1)
        self.assertEqual(self.rows()[101][3], 91)

    def test_a_new_rule_version_recomputes_every_day_of_the_window(self):
        self.run_days()
        with mock.patch.object(engine_hours, 'METHOD_VERSION', 'ignition-R600-x'):
            summary, _ = self.run_days()
        self.assertEqual(summary.recomputed, 2)
        self.assertEqual({r[5] for r in self.rows().values()},
                         {'ignition-R600-x'})

    def test_a_day_whose_points_are_gone_keeps_its_row(self):
        self.run_days()
        before = self.rows()
        for suffix in ('', '-wal', '-shm'):
            path = storage.points_path(self.folder, '202610') + suffix
            if os.path.exists(path):
                os.remove(path)
        summary, _ = self.run_days()
        self.assertEqual(summary.objects, 0)
        self.assertEqual(self.rows(), before)

    def test_one_broken_object_day_does_not_stop_the_others(self):
        real = engine_hours.compute

        def broken(folder, unit_id, day):
            if unit_id == 101:
                raise ValueError('bad points')
            return real(folder, unit_id, day)

        with mock.patch.object(engine_hours, 'compute', broken):
            summary, failures = self.run_days()
        self.assertEqual(failures, [(DAY, 101, 'ValueError')])
        self.assertEqual(sorted(self.rows()), [102])
        self.assertEqual(summary.failed, 1)


class Mileage(Base):
    """GPS-29: пробег суток по правилу P -- по нему решается, работал ли
    грузовик (решение владельца 19.10)."""

    def km(self, unit, day=DAY):
        con = sqlite3.connect(self.db)
        try:
            return con.execute('SELECT track_km FROM gps_daily_engine WHERE '
                               'work_date = ? AND wialon_id = ?',
                               (day, unit)).fetchone()[0]
        finally:
            con.close()

    def test_a_moving_machine_gets_its_kilometres(self):
        from gps import mileage
        start = epoch(DAY + ' 08:00')
        rows = [(101, start + 60 * i, LON + 0.001 * i, LAT, 30.0, 90, 14, 1.0)
                for i in range(61)]
        storage.write_points(self.folder, rows)
        self.run_days()
        expected = 60 * mileage.distance_km(LON, LAT, LON + 0.001, LAT)
        self.assertAlmostEqual(self.km(101), round(expected, 3), places=3)
        self.assertGreater(self.km(101), 3.0)

    def test_a_standing_machine_has_none(self):
        start = epoch(DAY + ' 08:00')
        rows = [(102, start + 60 * i, LON + (0.0001 if i % 2 else 0), LAT, 0.0,
                 90, 14, 1.0) for i in range(600)]
        storage.write_points(self.folder, rows)
        self.run_days()
        self.assertEqual(self.km(102), 0.0)

    def test_the_version_names_both_rules(self):
        self.assertEqual(engine_hours.METHOD_VERSION,
                         'ignition-R300-P150-2026-10-19')


class DayBoundary(Base):
    def test_a_pair_across_midnight_belongs_to_no_day(self):
        midnight = epoch('2026-10-16 00:00')
        storage.write_points(self.folder, [
            point(101, midnight - 120, 1.0), point(101, midnight - 60, 1.0),
            point(101, midnight + 60, 1.0), point(101, midnight + 120, 1.0)])
        self.run_days(('2026-10-15', '2026-10-16'))
        self.assertEqual(self.rows('2026-10-15')[101][0], 60)
        self.assertEqual(self.rows('2026-10-16')[101][0], 60)

    def test_days_without_points_get_no_row(self):
        summary, _ = self.run_days(('2026-10-01', '2026-10-02'))
        self.assertEqual((summary.days, summary.objects), (2, 0))
        self.assertEqual(self.rows('2026-10-01'), {})


class Command(Base):
    def run_main(self, *argv, db=None):
        out, err = io.StringIO(), io.StringIO()
        with redirect_stdout(out), redirect_stderr(err):
            code = engine_hours.main(['--dir', self.folder, '--db', db or self.db]
                                     + list(argv))
        return code, out.getvalue(), err.getvalue()

    def test_the_default_window_is_thirty_days_up_to_the_last_one(self):
        storage.write_points(self.folder,
                             run_of(101, epoch(DAY + ' 08:00'), 10, 1.0))
        code, out, err = self.run_main('--to', '2026-10-19')
        self.assertEqual(code, engine_hours.EXIT_OK, err)
        self.assertIn('engine hours 2026-09-20 -> 2026-10-19 (30 days)', out)
        self.assertIn('object-days with points: 1 | recomputed: 1', out)
        self.assertTrue(out.isascii())

    def test_bad_input_is_code_2(self):
        for argv in (('--from', '15.10.2026'), ('--from', '2026-10-20', '--to',
                                                '2026-10-19')):
            code, _, err = self.run_main(*argv)
            self.assertEqual(code, engine_hours.EXIT_BAD_INPUT, argv)
            self.assertTrue(err.startswith('ERROR'))
        code, _, err = self.run_main(db=os.path.join(self.folder, 'none.db'))
        self.assertEqual(code, engine_hours.EXIT_BAD_INPUT)
        self.assertFalse(os.path.exists(os.path.join(self.folder, 'none.db')))

    def test_without_the_migration_it_is_code_2(self):
        bare = os.path.join(self.folder, 'bare.db')
        sqlite3.connect(bare).close()
        code, _, err = self.run_main(db=bare)
        self.assertEqual(code, engine_hours.EXIT_BAD_INPUT)
        self.assertIn('GPS_ENGINE_001 is not applied', err)

    def test_a_failed_object_day_is_code_5_and_named(self):
        storage.write_points(self.folder,
                             run_of(101, epoch(DAY + ' 08:00'), 10, 1.0))
        with mock.patch.object(engine_hours, 'compute',
                               side_effect=ValueError('x')):
            code, out, _ = self.run_main('--from', DAY, '--to', DAY)
        self.assertEqual(code, engine_hours.EXIT_PARTIAL)
        self.assertIn('FAILED 2026-10-15 101: ValueError', out)


if __name__ == '__main__':
    unittest.main()
