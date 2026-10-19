# -*- coding: utf-8 -*-
"""GPS-25 -- проверка правила R против счётчика моточасов Wialon.

Синтетические файл отметок (снимки `collector_counters`) и файлы месяцев с
зажиганием. Держится предрегистрация (трек GPS, §0.6, 19.10.2026): окна
между соседними снимками, классы вне счёта, K1, K3, минимальные выборки,
вердикт последней строкой; и то, что инструмент ничего не пишет.

Run:
  python -m unittest tests.test_gps_engine_hours_check
"""

import csv
import hashlib
import io
import os
import sqlite3
import sys
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if REPO_ROOT not in sys.path:
    sys.path.insert(0, REPO_ROOT)

import tools.gps_engine_hours_check as check                       # noqa: E402
from gps_collector import storage                                  # noqa: E402
from gps_collector.tests.support import LAT, LON, epoch            # noqa: E402

START = epoch('2026-10-20 01:00')
DAY = 86400
CFL = 0x713   # 1811: биты 0x10 и 0x200 стоят, как у 368 объектов GPS-23


def track(unit_id, t_from, seconds_on, step=60, value=1.0, ign_from=None):
    """Points every `step` s from t_from: on for `seconds_on`, then off."""
    rows = []
    t = t_from
    end = t_from + seconds_on
    while t <= end + step:
        ign = value if t < end else 0.0
        if ign_from is not None:
            ign = ign_from(t)
        rows.append((unit_id, t, LON, LAT, 5.0, 90, 14, ign))
        t += step
    return rows


class Fleet:
    """A state file of counter snapshots and month files of points."""

    def __init__(self):
        self.folder = tempfile.mkdtemp()
        self.snapshots = []
        self.points = []

    def window(self, unit_id, t1, wialon_s, ours_on_s, cfl=CFL, cneh1=1000.0,
               ign_from=None, length=DAY):
        t2 = t1 + length
        self.snapshots += [(unit_id, t1, cneh1, cfl),
                           (unit_id, t2, cneh1 + wialon_s / 3600.0, cfl)]
        self.points += track(unit_id, t1 + 600, ours_on_s, ign_from=ign_from)
        return t2

    def write(self):
        con = storage.open_counters(self.folder)
        try:
            con.executemany(
                'INSERT INTO collector_counters (unit_id, last_t, cneh, cfl, '
                "taken_at) VALUES (?, ?, ?, ?, 't')", self.snapshots)
            con.commit()
        finally:
            con.close()
        if self.points:
            storage.write_points(self.folder, self.points)
        return self.folder


def run(folder, *extra):
    out_path = os.path.join(folder, 'check', 'engine_hours.csv')
    os.makedirs(os.path.dirname(out_path), exist_ok=True)
    out, err = io.StringIO(), io.StringIO()
    with redirect_stdout(out), redirect_stderr(err):
        code = check.main(['--dir', folder, '--out', out_path] + list(extra))
    return code, out.getvalue(), err.getvalue(), out_path


def rows_of(path):
    with open(path, encoding='utf-8-sig', newline='') as handle:
        return list(csv.DictReader(handle, delimiter=';'))


def digest(folder):
    """Hashes of the database files themselves.

    [REASON]: `-wal` и `-shm` -- служебные файлы режима WAL; SQLite создаёт их
    при любом чтении, в том числе `mode=ro`. Данные лежат в самом файле базы.
    """
    found = {}
    for name in sorted(os.listdir(folder)):
        path = os.path.join(folder, name)
        if os.path.isfile(path) and not name.endswith(('-wal', '-shm')):
            with open(path, 'rb') as handle:
                found[name] = hashlib.sha256(handle.read()).hexdigest()
    return found


def big_fleet(disagree=0, idle_false=0):
    """210 work windows (W = 1 h) and 60 idle ones (W = 0)."""
    fleet = Fleet()
    for n in range(210):
        fleet.window(1000 + n, START, 3600, 0 if n < disagree else 3600)
    for n in range(60):
        fleet.window(2000 + n, START, 0, 3600 if n < idle_false else 0)
    return fleet.write()


class Verdict(unittest.TestCase):
    def test_agreeing_windows_give_met_on_the_last_line(self):
        code, out, err, path = run(big_fleet())
        self.assertEqual(code, check.EXIT_OK, err)
        lines = out.strip().splitlines()
        self.assertEqual(lines[-1], 'VERDICT: MET')
        self.assertIn('K1  windows with W >= 1800 s: 210', out)
        self.assertIn('= 100.0 %', out)
        self.assertIn('K3  windows with W < 60 s: 60', out)
        self.assertTrue(out.isascii())
        self.assertEqual(len(rows_of(path)), 270)

    def test_one_k1_window_in_ten_off_is_still_met_and_more_is_not(self):
        # 21 из 210 мимо -- ровно 90 %: граница включительно.
        code, out, _, _ = run(big_fleet(disagree=21))
        self.assertEqual(out.strip().splitlines()[-1], 'VERDICT: MET')
        code, out, _, _ = run(big_fleet(disagree=22))
        self.assertEqual(out.strip().splitlines()[-1], 'VERDICT: NOT MET')

    def test_false_work_on_idle_windows_fails_k3(self):
        # 3 из 60 -- 95 %, ещё проходит; 4 -- уже нет.
        _, out, _, _ = run(big_fleet(idle_false=3))
        self.assertEqual(out.strip().splitlines()[-1], 'VERDICT: MET')
        _, out, _, _ = run(big_fleet(idle_false=4))
        self.assertEqual(out.strip().splitlines()[-1], 'VERDICT: NOT MET')

    def test_too_few_windows_is_not_evaluated(self):
        fleet = Fleet()
        for n in range(50):
            fleet.window(1000 + n, START, 3600, 3600)
        for n in range(60):
            fleet.window(2000 + n, START, 0, 0)
        _, out, _, _ = run(fleet.write())
        self.assertEqual(out.strip().splitlines()[-1], 'VERDICT: NOT EVALUATED')

    def test_a_failed_criterion_wins_over_a_small_sample(self):
        fleet = Fleet()
        for n in range(210):
            fleet.window(1000 + n, START, 3600, 0)
        _, out, _, _ = run(fleet.write())
        self.assertEqual(out.strip().splitlines()[-1], 'VERDICT: NOT MET')


class Tolerance(unittest.TestCase):
    def test_tolerance_is_15_percent_or_half_an_hour_whichever_is_larger(self):
        fleet = Fleet()
        # W = 10 ч: допуск 1,5 ч; W = 1 ч: допуск 0,5 ч.
        fleet.window(1, START, 36000, 36000 - 5400)
        fleet.window(2, START, 36000, 36000 - 5400 - 120, length=2 * DAY)
        fleet.window(3, START, 3600, 3600 - 1800)
        fleet.window(4, START, 3600, 3600 - 1800 - 120, length=2 * DAY)
        _, _, _, path = run(fleet.write())
        verdicts = {r['wialon_id']: r['v_dopuske'] for r in rows_of(path)}
        self.assertEqual(verdicts, {'1': 'da', '2': 'net', '3': 'da', '4': 'net'})


class Classes(unittest.TestCase):
    def test_each_excluded_window_is_counted_in_its_own_class(self):
        fleet = Fleet()
        fleet.window(1, START, 3600, 3600)
        fleet.window(2, START, 3600, 3600, cfl=0x713 & ~0x200)  # не авто
        fleet.window(3, START, -3600, 3600)                     # счётчик назад
        fleet.window(4, START, DAY + 61, 3600)                  # больше окна
        fleet.window(5, START, DAY + 59, 3600, length=DAY)      # ещё можно
        fleet.window(6, START, 3600, 3600, ign_from=lambda t: None)
        code, out, _, path = run(fleet.write())
        self.assertEqual(code, check.EXIT_OK)
        classes = {r['wialon_id']: r['klass'] for r in rows_of(path)}
        self.assertEqual(classes, {'1': 'v_schete', '2': 'bez_bitov',
                                   '3': 'schetchik_pravili',
                                   '4': 'schetchik_pravili', '5': 'v_schete',
                                   '6': 'net_zazhiganiya'})
        self.assertIn('windows: 6 | in the count: 2 | no cfl bits: 1 | '
                      'counter edited: 2 | no ignition values: 1', out)

    def test_windows_come_from_neighbouring_snapshots_only(self):
        fleet = Fleet()
        t2 = fleet.window(1, START, 3600, 3600)
        fleet.snapshots.append((1, t2 + DAY, 1000.0 + 1 + 7200 / 3600.0, CFL))
        fleet.points += track(1, t2 + 600, 7200)
        _, _, _, path = run(fleet.write())
        rows = rows_of(path)
        self.assertEqual([(r['wialon_s'], r['nashe_vkl_s']) for r in rows],
                         [('3600.0', '3600'), ('7200.0', '7200')])

    def test_since_drops_windows_that_start_earlier(self):
        fleet = Fleet()
        t2 = fleet.window(1, START, 3600, 3600)
        fleet.snapshots.append((1, t2 + DAY, 1001.0 + 1.0, CFL))
        _, _, _, path = run(fleet.write(), '--since', '2026-10-21')
        self.assertEqual(len(rows_of(path)), 1)


class ReadOnlyAndRefusals(unittest.TestCase):
    def test_nothing_is_written_to_the_state_or_the_point_files(self):
        folder = big_fleet()
        before = digest(folder)
        run(folder)
        self.assertEqual(digest(folder), before)
        self.assertFalse(os.path.exists(storage.points_path(folder, '202609')))

    def test_no_state_file_is_code_2(self):
        folder = tempfile.mkdtemp()
        code, _, err, _ = run(folder)
        self.assertEqual(code, check.EXIT_BAD_INPUT)
        self.assertIn('no state file', err)

    def test_a_collector_without_gps_25_is_code_2_not_an_empty_report(self):
        folder = tempfile.mkdtemp()
        storage.set_watermark(folder, 101, START)
        code, _, err, path = run(folder)
        self.assertEqual(code, check.EXIT_BAD_INPUT)
        self.assertIn('has not run with GPS-25', err)
        self.assertFalse(os.path.exists(path))

    def test_bad_since_and_missing_out_folder_are_code_2(self):
        folder = big_fleet()
        code, _, _, _ = run(folder, '--since', '20.10.2026')
        self.assertEqual(code, check.EXIT_BAD_INPUT)
        out, err = io.StringIO(), io.StringIO()
        with redirect_stdout(out), redirect_stderr(err):
            code = check.main(['--dir', folder, '--out',
                               os.path.join(folder, 'none', 'x.csv')])
        self.assertEqual(code, check.EXIT_BAD_INPUT)

    def test_the_csv_carries_numbers_and_wialon_ids_only(self):
        _, _, _, path = run(big_fleet())
        with open(path, encoding='utf-8-sig') as handle:
            header = handle.readline().strip().split(';')
        self.assertEqual(tuple(header), check.COLUMNS)


class CounterReading(unittest.TestCase):
    def test_snapshots_are_read_by_object_and_time(self):
        fleet = Fleet()
        fleet.snapshots = [(7, START + DAY, 2.0, CFL), (7, START, 1.0, CFL),
                           (3, START, 5.0, None)]
        folder = fleet.write()
        found = check.read_snapshots(storage.state_path(folder))
        self.assertEqual(found, {3: [(START, 5.0, None)],
                                 7: [(START, 1.0, CFL), (START + DAY, 2.0, CFL)]})
        self.assertEqual(check.read_snapshots(storage.state_path(folder),
                                              since=START + 1),
                         {3: [], 7: [(START + DAY, 2.0, CFL)]})


if __name__ == '__main__':
    unittest.main()
