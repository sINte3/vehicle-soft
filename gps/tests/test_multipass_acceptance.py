# -*- coding: utf-8 -*-
"""tools/gps_multipass_acceptance.py -- приёмка правила K1 (§2.14, 22.10.2026).

Окружение расчёта (геометрия). Держится то, что делает прогон приёмкой
предрегистрированного правила, а не подбором:
  * окно приёмки не раньше 14.10 (всё до него разведка видела) и не позже
    вчерашнего дня -- иначе отказ кодом 2 до чтения;
  * T1 считается по записанной процедуре на сутках стенда групп B, M, L, C;
    чужие группы (X, LZ) в калибровку не идут;
  * правило K1 на сутках окна: один проход, повтор, два направления,
    беспорядок, «не судится»; «не наша» не берётся;
  * без калибровки порога нет -- первую ступень не проходят ни одни сутки;
  * P1 -- синтетические двойные из суток `odin_prohod`, σ 1 м -- в условие,
    σ 2 м -- справочно, зерно -- sha1 суток; N1 -- сутки заявок «гектары»
    с одним проходом по объёму, без «2 марта», без «не судится», с окном в
    одни сутки внутри окна приёмки (каждое условие -- отдельно); кандидаты
    -- справочно;
  * вердикт -- по записанным числам, границы включительно; мала выборка --
    «не оценено», код 4;
  * ни база, ни файлы точек не меняются; Flask не импортируется.

Запуск из корня репозитория окружением расчёта:
  python -m unittest gps.tests.test_multipass_acceptance
"""
import contextlib
import csv
import hashlib
import io
import os
import shutil
import sqlite3
import subprocess
import sys
import tempfile
import types
import unittest
import unittest.mock
from datetime import date

import tools.gps_multipass_acceptance as tool
from agro_work import reconcile as rc
from gps import multipass
from gps.daily import compute_day, write_day
from gps.tests.test_edge_replay import stamped
from gps.tests.test_multipass import (HOUR, drifting_noise, last_time,
                                      picker_field, shuttle)
from gps.tests.test_multipass_k1 import wander
from gps_collector import storage
from tests import agro_work_db as dbh

REPO = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
TODAY = date(2026, 10, 17)
SINCE, UNTIL = '2026-10-14', '2026-10-16'
W_GA, W_TWO, W_TIME = 'w-ga', 'w-two', 'w-time'

# объекты окна приёмки
(ONE, TWICE, CROSS, WANDER, SMALL, ALIEN, CALM, FAR) = range(801, 809)
# объекты стенда
BENCH = {(901, 'B'), (902, 'M'), (903, 'C'), (904, 'L'), (905, 'X'), (906, 'LZ')}


def twice_in_a_day(seed=7):
    first = picker_field()
    return first + picker_field(start=last_time(first) + 3 * HOUR,
                                noise=drifting_noise(1.0, seed=seed))


def along_and_across():
    along = shuttle(150.0, 150.0, 3.5, along='north', speed_kmh=8.0)
    return along + shuttle(150.0, 150.0, 3.5, along='east', speed_kmh=8.0,
                           start=last_time(along) + 600.0)


def digest(path):
    with open(path, 'rb') as handle:
        return hashlib.sha256(handle.read()).hexdigest()


WORLD = {}


def gps_ha(con, unit, day):
    return con.execute('SELECT COALESCE(SUM(area_ha), 0) FROM gps_work_polygons '
                       'WHERE wialon_id = ? AND work_date = ?', (unit, day)
                       ).fetchone()[0]


def setUpModule():
    root = tempfile.mkdtemp()
    folder = os.path.join(root, 'points')
    os.mkdir(folder)
    db = dbh.make_db(root)
    con = sqlite3.connect(db)
    try:
        dbh.add_org(con, 1, 'Buxoro')
        units = [ONE, TWICE, CROSS, WANDER, SMALL, ALIEN, CALM, FAR] + \
            sorted(unit for unit, _group in BENCH)
        for number, unit in enumerate(units, start=1):
            dbh.add_equipment(con, number, '80 %03d EA' % number, org_id=1)
            dbh.add_mapping(con, number, unit, number, skip=int(unit == ALIEN))
            con.execute("INSERT INTO agro_work_transports (id, plate_number, "
                        "plate_norm, equipment_id, match_status, first_seen_at, "
                        "last_seen_at) VALUES (?, ?, ?, ?, 'auto', 't', 't')",
                        ('T%d' % unit, 'P%d' % unit, 'P%d' % unit, number))
        for work_type, name, unit_name, method in (
                (W_GA, 'Чизеллаш', 'HECTARE', 'ga'),
                (W_TWO, 'Ер текислаш (Длина воз 2 марта утиш)', 'HECTARE', 'ga'),
                (W_TIME, 'Лазер килиш', 'HOUR', 'vremya')):
            con.execute("INSERT INTO agro_work_work_types (id, name, unit, method, "
                        "first_seen_at, last_seen_at) VALUES (?, ?, ?, ?, 't', 't')",
                        (work_type, name, unit_name, method))
        con.execute("INSERT INTO agro_work_import_runs (id, started_at, status, "
                    "tool_version) VALUES (1, 't', 'ok', 'v')")
        con.commit()
        days = [
            # окно приёмки
            ('2026-10-14', ONE, picker_field()),
            ('2026-10-14', TWICE, twice_in_a_day()),
            ('2026-10-15', CROSS, along_and_across()),
            ('2026-10-15', WANDER, wander()),
            ('2026-10-16', SMALL, shuttle(42.0, 150.0, 3.5)),
            ('2026-10-14', ALIEN, twice_in_a_day(seed=9)),
            ('2026-10-16', CALM, shuttle(120.0, 200.0, 4.0)),
            ('2026-10-20', FAR, twice_in_a_day(seed=5)),      # после окна
        ]
        # стенд: работа рядами B, M, C, L -- калибровка; X и LZ -- нет
        for unit, group in sorted(BENCH):
            track = wander(seed=unit) if group in ('X', 'LZ') else picker_field()
            days.append(('2026-10-0%d' % (unit - 900), unit, track))
        for day, unit, track in days:
            points = stamped(track, day)
            storage.write_points(folder, [(unit, t, lon, lat, speed, None, sats)
                                          for t, lon, lat, speed, sats in points])
            write_day(con, day, unit, compute_day(points), '2026-10-17 01:00:00')
        con.commit()
        number = 0
        for unit, day, work_type, factor in (
                (ONE, '2026-10-14', W_GA, 1.0),       # один проход по объёму
                (TWICE, '2026-10-14', W_GA, 1.0),     # ... а трек -- два
                (CROSS, '2026-10-15', W_GA, 2.0),     # кандидат
                (WANDER, '2026-10-15', W_TWO, 1.0),   # «2 марта» -- не в счёт
                (SMALL, '2026-10-16', W_GA, 1.0),     # не судится
                (CALM, '2026-10-16', W_TIME, 1.0)):   # не «гектары»
            number += 1
            volume = round(gps_ha(con, unit, day) * factor, 3)
            con.execute(
                "INSERT INTO agro_work_applications (id, application_number, "
                "transport_id, work_type_id, unit, volume, status, created_at, "
                "updated_at, created_day, first_seen_run_id, last_seen_run_id, "
                "history_updated_at, initial_status, completed_day) VALUES (?, ?, "
                "?, ?, ?, ?, 'COMPLETED', ?, 'u', ?, 1, 1, 'u', 'IN_PROGRESS', ?)",
                ('app-%d' % number, 'N-%d' % number, 'T%d' % unit, work_type,
                 'HOUR' if work_type == W_TIME else 'HECTARE', str(volume),
                 day + 'T08:00:00+05:00', day, day))
        con.commit()
    finally:
        con.close()
    bench = os.path.join(root, 'bench.csv')
    with open(bench, 'w', encoding='utf-8', newline='') as handle:
        writer = csv.writer(handle)
        writer.writerow(('day', 'wialon_id', 'group', 'wialon_name'))
        for unit, group in sorted(BENCH):
            writer.writerow(('2026-10-0%d' % (unit - 900), unit, group, 'x'))
    WORLD.update(root=root, folder=folder, db=db, bench=bench,
                 csv=os.path.join(root, 'k1.csv'))
    before = {path: digest(path) for path in (db,) + tuple(
        os.path.join(folder, name) for name in os.listdir(folder))}
    with unittest.mock.patch.object(multipass, 'CALIBRATION_MIN_DAYS', 3), \
            unittest.mock.patch.object(tool, 'SYNTH_MIN', 2), \
            unittest.mock.patch.object(tool, 'N1_MIN_DAYS', 2):
        code, out = run_main('--db', db, '--dir', folder, '--since', SINCE,
                             '--until', UNTIL, '--bench', bench, '--csv',
                             WORLD['csv'])
    WORLD.update(code=code, out=out, before=before)


def tearDownModule():
    shutil.rmtree(WORLD['root'], True)


def run_main(*argv, today=TODAY):
    lines = []
    with contextlib.redirect_stderr(io.StringIO()):
        code = tool.main([str(arg) for arg in argv], today=today,
                         out=lines.append)
    return code, '\n'.join(lines)


def csv_rows():
    with open(WORLD['csv'], encoding='utf-8', newline='') as handle:
        return {(row['day'], int(row['wialon_id'])): row
                for row in csv.DictReader(handle)}


class Run(unittest.TestCase):

    def test_the_rule_on_each_day_of_the_window(self):
        rows = csv_rows()
        self.assertEqual(rows[('2026-10-14', ONE)]['reason'], multipass.K_ONE)
        self.assertEqual(rows[('2026-10-14', TWICE)]['reason'], multipass.K_REPEAT)
        self.assertEqual(rows[('2026-10-15', CROSS)]['k'], '2')
        self.assertEqual(rows[('2026-10-15', WANDER)]['reason'], multipass.K_NOT_ROWS)
        self.assertEqual(rows[('2026-10-16', SMALL)]['reason'], multipass.K_NOT_JUDGED)
        self.assertEqual(rows[('2026-10-16', CALM)]['reason'], multipass.K_ONE)
        # «не наша» и сутки вне окна не берутся
        self.assertNotIn(('2026-10-14', ALIEN), rows)
        self.assertNotIn(('2026-10-20', FAR), rows)
        self.assertEqual(len(rows), 6)

    def test_calibration_uses_only_the_row_work_groups(self):
        # B, M, C, L -- четыре суток; X и LZ (беспорядок) -- не в счёт
        self.assertIn('calibration: bench days 4 | no points 0 | judged 4 |',
                      WORLD['out'])
        t1 = float(WORLD['out'].split('= ', 1)[1].split()[0])
        self.assertGreater(t1, 0.85)

    def test_p1_doubles_the_one_pass_days(self):
        rows = csv_rows()
        doubled = [row for row in rows.values() if row['synthetic_k_1m']]
        self.assertEqual(sorted((row['day'], int(row['wialon_id']))
                                for row in doubled),
                         [('2026-10-14', ONE), ('2026-10-16', CALM)])
        self.assertTrue(all(row['synthetic_k_1m'] == '2' for row in doubled))
        self.assertIn('P1 synthetic doubles, sigma 1.0 m: 2 of 2 two passes = 1.000',
                      WORLD['out'])

    def test_n1_counts_one_pass_claims_that_are_judged(self):
        # ONE (1 проход) и TWICE (трек -- 2) -- заявки «гектары», объём =
        # гектары GPS; WANDER -- «2 марта», SMALL -- не судится, CALM -- часы.
        self.assertIn('N1 one-pass claims (volume / GPS ha 0.80-1.25): 1 of 2 two '
                      'passes = 0.500', WORLD['out'])
        self.assertIn('candidates (volume / GPS ha >= 1.6, reference): 1 of 1 two '
                      'passes = 1.000', WORLD['out'])
        rows = csv_rows()
        self.assertEqual(rows[('2026-10-15', CROSS)]['claim_ratio'], '2.0')
        self.assertEqual(rows[('2026-10-15', WANDER)]['claim_ratio'], '')

    def test_the_verdict_and_its_exit_code(self):
        self.assertIn('VERDICT K1: NOT MET', WORLD['out'])
        self.assertEqual(WORLD['code'], tool.EXIT_EVALUATED)

    def test_nothing_is_written_but_the_csv(self):
        for path, value in WORLD['before'].items():
            self.assertEqual(digest(path), value, path)
        # SQLite, открывая файл в режиме WAL только на чтение, создаёт рядом
        # пустые служебные -wal и -shm (оговорка в докстринге инструмента);
        # новых файлов данных нет.
        data = [name for name in os.listdir(WORLD['folder'])
                if not name.endswith(('-wal', '-shm'))]
        self.assertEqual(sorted(data),
                         sorted(os.path.basename(path) for path in WORLD['before']
                                if path != WORLD['db']))

    def test_the_console_is_ascii(self):
        self.assertTrue(WORLD['out'].isascii())


class Verdict(unittest.TestCase):

    def test_the_numbers_are_the_preregistered_ones(self):
        self.assertEqual(tool.ACCEPTANCE_FROM, '2026-10-14')
        self.assertEqual((tool.SYNTH_MAX, tool.SYNTH_MIN, tool.SYNTH_SIGMA,
                          tool.SYNTH_SIGMA_REFERENCE), (100, 30, 1.0, 2.0))
        self.assertEqual((tool.P1_MIN_SHARE, tool.N1_MIN_DAYS, tool.N1_MAX_SHARE),
                         (0.90, 40, 0.05))
        self.assertEqual((tool.CLAIM_LOW, tool.CLAIM_HIGH, tool.CANDIDATE_FROM),
                         (0.80, 1.25, 1.6))
        self.assertEqual(tool.TWO_PASS_KIND, '2 марта')

    def test_boundaries_are_inclusive(self):
        self.assertEqual(tool.verdict(20, 100, 90, 100, 5), (tool.VERDICT_MET, []))
        self.assertEqual(tool.verdict(20, 100, 89, 100, 5)[0], tool.VERDICT_NOT_MET)
        self.assertEqual(tool.verdict(20, 100, 90, 100, 6)[0], tool.VERDICT_NOT_MET)

    def test_a_short_sample_is_not_evaluated_and_says_why(self):
        result, why = tool.verdict(19, 29, 29, 39, 0)
        self.assertEqual(result, tool.VERDICT_NOT_EVALUATED)
        self.assertEqual(why, ['calibration days 19 < 20', 'P1 days 29 < 30',
                               'N1 days 39 < 40'])
        self.assertEqual(tool.verdict(20, 30, 27, 40, 2)[0], tool.VERDICT_MET)


class Calibration(unittest.TestCase):
    """T1 -- 5-й процентиль (ближайший ранг) по суткам, прошедшим свидетельство."""

    def calibrate(self, measured):
        bench = [('2026-10-%02d' % (i % 28 + 1), 1000 + i, group)
                 for i, (group, _m) in enumerate(measured)]
        values = {unit: m for (_d, unit, _g), (_group, m) in zip(bench, measured)}
        with unittest.mock.patch.object(tool, 'measure',
                                        lambda _f, _d, unit: values[unit]):
            return tool.calibrate('x', bench)

    @staticmethod
    def day(rows, ha=3.0, km=9.0):
        return {'rows': rows, 'largest_ha': ha, 'largest_km': km}

    def test_the_fifth_percentile_by_nearest_rank(self):
        twenty = [('B', self.day(0.50 + i / 100.0)) for i in range(20)]
        self.assertEqual(self.calibrate(twenty)[0], 0.50)       # ранг 1 из 20
        forty = [('M', self.day(0.50 + i / 100.0)) for i in range(40)]
        self.assertEqual(self.calibrate(forty)[0], 0.51)        # ранг 2 из 40

    def test_unjudged_days_and_other_groups_do_not_count(self):
        days = [('B', self.day(0.90)), ('C', self.day(0.80)),
                ('L', self.day(0.10, ha=0.5)),     # не прошла свидетельство
                ('X', self.day(0.20)), ('LZ', self.day(0.05)),
                ('M', None)]                       # точек нет
        t1, values, missing = self.calibrate(days)
        self.assertEqual((t1, values, missing), (0.80, [0.80, 0.90], 1))

    def test_without_calibration_no_day_passes_the_first_stage(self):
        # стенд без групп работы рядами: T1 нет, и сутки ONE и TWICE, которые
        # с порогом -- «один проход» и «повтор», без него -- «не рядами»
        bench = os.path.join(WORLD['root'], 'bench_no_rows.csv')
        with open(bench, 'w', encoding='utf-8', newline='') as handle:
            writer = csv.writer(handle)
            writer.writerow(('day', 'wialon_id', 'group', 'wialon_name'))
            for unit, group in sorted(BENCH):
                if group in ('X', 'LZ'):
                    writer.writerow(('2026-10-0%d' % (unit - 900), unit, group, 'x'))
        code, out = run_main('--db', WORLD['db'], '--dir', WORLD['folder'],
                             '--since', SINCE, '--until', SINCE, '--bench', bench)
        self.assertEqual(code, tool.EXIT_NOT_EVALUATED)
        self.assertIn('calibration: bench days 0 | no points 0 | judged 0 |', out)
        self.assertIn('reasons: ne_suditsya 0 | ne_ryadami 2 | odin_prohod 0 | '
                      'povtor 0 | dva_napravleniya 0', out)
        self.assertIn('P1 synthetic doubles, sigma 1.0 m: 0 of 0', out)


class Synthetic(unittest.TestCase):

    def test_each_sigma_has_its_column_and_the_seed_is_the_days_sha1(self):
        calls = []

        def double(points, sigma, seed):
            calls.append((sigma, seed))
            return [sigma]

        rows = [{'day': '2026-10-15', 'wialon_id': ONE, 'reason': multipass.K_ONE}]
        with unittest.mock.patch.object(tool, 'read_day_readonly',
                                        lambda *_args: [(0, 64.4, 39.8, 6.0, 9)]), \
                unittest.mock.patch.object(multipass, 'synthetic_double', double), \
                unittest.mock.patch.object(multipass, 'measure_day',
                                           lambda points: points[0]), \
                unittest.mock.patch.object(
                    multipass, 'multiplicity',
                    lambda sigma, _t1: (2 if sigma == tool.SYNTH_SIGMA else 1, 'x')):
            used = tool.synthetic('x', rows, 0.6)
        seed = int(hashlib.sha1(('2026-10-15:%d' % ONE).encode()).hexdigest()[:8], 16)
        self.assertEqual(calls, [(1.0, seed), (2.0, seed)])
        self.assertEqual((used[0]['synthetic_k_1m'], used[0]['synthetic_k_2m']),
                         (2, 1))


class Claims(unittest.TestCase):
    """Какие заявки идут в N1 -- каждое условие предрегистрации отдельно.

    Строки сверки подставлены: настоящая сверка не даёт гектаров GPS заявке
    «время» и в мире прогона нет окон длиннее суток, так что условия
    «гектары» и «окно -- одни сутки» сквозной прогон по отдельности не держит.
    """

    def claims(self, **changes):
        app = types.SimpleNamespace(work_type_id=changes.pop('work_type', W_GA),
                                    row={})
        row = {'app': app, 'method': 'ga', 'verdict': rc.V_WORK,
               'volume_verdict': rc.VOL_OK, 'gps_ha': 4.0, 'volume': 6.0,
               'window': (date(2026, 10, 15), date(2026, 10, 15)),
               'wialon_id': ONE}
        row.update(changes)

        class Context:
            work_type_names = {W_GA: 'Чизеллаш',
                               W_TWO: 'Ер текислаш (Длина воз 2 марта утиш)'}

            def __init__(self, _con, _first, _last):
                pass

            def forward_rows(self):
                return [row]

        con = tool.open_readonly(WORLD['db'])
        try:
            with unittest.mock.patch.object(rc, 'Reconciliation', Context):
                return tool.claims(con, SINCE, UNTIL)
        finally:
            con.close()

    def test_a_one_day_hectare_claim_with_work_and_a_volume_verdict(self):
        self.assertEqual(self.claims(), {(ONE, '2026-10-15'): 1.5})

    def test_each_condition_drops_the_claim(self):
        for change in (dict(method='vremya'), dict(method=None),
                       dict(verdict=rc.V_NO_WORK), dict(volume_verdict=None),
                       dict(gps_ha=0.0), dict(work_type=W_TWO),
                       dict(window=(date(2026, 10, 15), date(2026, 10, 16))),
                       dict(window=(date(2026, 10, 13), date(2026, 10, 13))),
                       dict(window=(date(2026, 10, 17), date(2026, 10, 17)))):
            with self.subTest(change=change):
                self.assertEqual(self.claims(**change), {})


class Refusals(unittest.TestCase):

    def test_a_window_before_the_fourteenth_is_refused(self):
        code, out = run_main('--db', WORLD['db'], '--dir', WORLD['folder'],
                             '--since', '2026-10-13', '--until', UNTIL,
                             '--bench', WORLD['bench'])
        self.assertEqual(code, tool.EXIT_BAD_INPUT)
        self.assertIn('before 2026-10-14', out)

    def test_today_is_not_in_the_window(self):
        code, out = run_main('--db', WORLD['db'], '--dir', WORLD['folder'],
                             '--until', '2026-10-17', '--bench', WORLD['bench'])
        self.assertEqual(code, tool.EXIT_BAD_INPUT)
        self.assertIn('before today', out)

    def test_missing_files_are_refused_before_reading(self):
        code, out = run_main('--db', os.path.join(WORLD['root'], 'nope.db'),
                             '--dir', WORLD['folder'], '--until', UNTIL,
                             '--bench', WORLD['bench'])
        self.assertEqual(code, tool.EXIT_BAD_INPUT)
        self.assertFalse(os.path.exists(os.path.join(WORLD['root'], 'nope.db')))

    def test_a_short_sample_exits_four(self):
        code, out = run_main('--db', WORLD['db'], '--dir', WORLD['folder'],
                             '--since', SINCE, '--until', UNTIL,
                             '--bench', WORLD['bench'])
        self.assertEqual(code, tool.EXIT_NOT_EVALUATED)
        self.assertIn('NOT EVALUATED: calibration days 4 < 20', out)
        self.assertIn('VERDICT K1: NOT EVALUATED', out)


class Imports(unittest.TestCase):

    def test_flask_is_not_imported(self):
        code = ('import sys; import tools.gps_multipass_acceptance; '
                'print("flask" in sys.modules, "models" in sys.modules)')
        out = subprocess.run([sys.executable, '-c', code], cwd=REPO,
                             capture_output=True, text=True, check=True).stdout
        self.assertEqual(out.strip(), 'False False')


if __name__ == '__main__':
    unittest.main()
