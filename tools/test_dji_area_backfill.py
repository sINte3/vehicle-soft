# -*- coding: utf-8 -*-
"""Самопроверка tools/dji_area_backfill.py -- исторического прогона по окнам.

Что здесь держится (DRONE-AREA-CONTROL-V2-MEGA, блок F):

* план показывает окна и их статус и не пишет ничего -- ни файла, ни
  каталога; базы ему не нужно;
* окна идут подряд от старого к новому и не пересекают границу месяца;
* каждое окно -- ТОТ ЖЕ ежедневный цикл (те же шаги, `--kind backfill`, окно
  в `--from/--to`), а не второй алгоритм;
* контрольная точка переписывается после каждого окна: `--max-windows`
  останавливает с кодом 8, та же команда продолжает; DONE не проходится
  снова; упавшее окно повторяется и доходит до DONE; после N неудач --
  GAVE_UP и пропуск до `--retry-gave-up`;
* занятая блокировка цикла -- код 7, окно помечено BUSY, продолжение позже;
* смена версии алгоритма проходит DONE заново; чужой диапазон -- отказ;
* потеря кандидата -- FAILED, потеря только контроля -- DONE с
  предупреждением;
* RAW и расчёты базы после прогона побайтно те же;
* итог по месяцам и список для ручной проверки считают ожидающие,
  «требует проверки» и «недостаточно доказательств» по синтетическим
  расчётам, а решённые записи не называют.

Команды не исполняются -- подставной исполнитель их записывает. Stdlib.

Запуск:  python tools\\test_dji_area_backfill.py
"""

import csv
import hashlib
import io
import json
import os
import shutil
import sqlite3
import sys
import tempfile
import unittest
from datetime import date, timedelta

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if REPO_ROOT not in sys.path:
    sys.path.insert(0, REPO_ROOT)

import dji_area  # noqa: E402
from dji_area import resolver as rs  # noqa: E402
from drone_collector import runlock  # noqa: E402
from tools import dji_area_backfill as tool  # noqa: E402
from tools import dji_area_daily as daily  # noqa: E402

TODAY = date(2026, 9, 23)
VERSION = dji_area.AREA_ALGORITHM_VERSION


def sha256(path):
    with open(path, 'rb') as fh:
        return hashlib.sha256(fh.read()).hexdigest()


class WindowRunner(object):
    """Подставной исполнитель цикла; поведение -- по окну.

    ``codes`` -- {начало окна: {шаг: код}}; ``capture``/``after`` --
    {начало окна: записи манифеста до/после сбора}.
    """

    def __init__(self, codes=None, capture=None, after=None):
        self.codes = codes or {}
        self.capture = capture or {}
        self.after = after or {}
        self.commands = []
        self.window = None

    def step_of(self, command):
        text = ' '.join(command)
        if 'area_manifest' in text:
            out = command[command.index('--out') + 1]
            return daily.STEP_VERIFY if os.path.basename(out) == \
                'area_ids_after.txt' else daily.STEP_MANIFEST
        if '--sources' in command:
            return daily.STEP_SOURCES
        if 'dji_area_recalc.py' in text:
            return daily.STEP_RECALC
        return daily.STEP_FLIGHTS

    def __call__(self, command, cwd):
        step = self.step_of(command)
        if step == daily.STEP_FLIGHTS:
            # Обход берётся на сутки шире окна.
            self.window = (date.fromisoformat(
                command[command.index('--from') + 1])
                + timedelta(days=1)).isoformat()
        elif '--from' in command:
            self.window = command[command.index('--from') + 1]
        self.commands.append((self.window, step, list(command)))
        code = self.codes.get(self.window, {}).get(step, 0)
        if step in (daily.STEP_MANIFEST, daily.STEP_VERIFY) and code in (0,
                                                                        22):
            entries = self.capture.get(self.window, [])
            if step == daily.STEP_VERIFY:
                entries = self.after.get(self.window, entries)
            with io.open(command[command.index('--summary') + 1], 'w',
                         encoding='utf-8') as fh:
                json.dump({'capture': entries, 'over_cap': False,
                           'counts': {'candidates_need_capture': sum(
                               1 for e in entries
                               if e.get('reason') != 'CONTROL'),
                               'capture_total': len(entries),
                               'candidates_no_v4_at_source': 0}}, fh)
            with io.open(command[command.index('--out') + 1], 'w',
                         encoding='utf-8') as fh:
                fh.write('\n'.join(str(e['flight_id']) for e in entries))
        if step == daily.STEP_RECALC and code == 0:
            with io.open(command[command.index('--json') + 1], 'w',
                         encoding='utf-8') as fh:
                json.dump({'flights_in_period': 5,
                           'structural_candidates': 0,
                           'calc_writes': {'unchanged': 5}}, fh)
        return code

    def windows(self, step=daily.STEP_RECALC):
        return [w for w, s, _c in self.commands if s == step]


class Base(unittest.TestCase):

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix='area_backfill_')
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        self.db = os.path.join(self.tmp, 'instance', 'transport.db')
        os.makedirs(os.path.dirname(self.db))
        self.make_db()
        self.checkpoint = os.path.join(self.tmp, 'bf', 'checkpoint.json')
        self.lines = []

    def make_db(self):
        con = sqlite3.connect(self.db)
        con.execute('CREATE TABLE drone_flights (id INTEGER PRIMARY KEY, '
                    'dji_flight_id BIGINT, area_ha FLOAT)')
        con.execute('CREATE TABLE dji_area_calculations (id INTEGER PRIMARY '
                    'KEY, flight_id BIGINT, area_algorithm_version TEXT, '
                    'superseded_at DATETIME, report_start_date DATE, '
                    'raw_area_m2 FLOAT, corrected_recorded_area_m2 FLOAT, '
                    'controller_delta_area_m2 FLOAT, area_status TEXT, '
                    'aggregation_eligibility TEXT, structural_candidate '
                    'BOOLEAN, scalar_source_check BOOLEAN, '
                    'anomaly_flags_json TEXT)')
        con.commit()
        con.close()

    def add_calc(self, flight_id, day, kind, superseded=False,
                 version=VERSION):
        """Синтетический расчёт заданного учётного класса."""
        row = {'raw_area_m2': 5000.0, 'corrected_recorded_area_m2': None,
               'controller_delta_area_m2': None,
               'aggregation_eligibility': rs.AGG_PROVISIONAL,
               'structural_candidate': 0, 'scalar_source_check': 0,
               'anomaly_flags_json': '[]',
               'area_status': rs.RAW_UNVERIFIED}
        if kind in ('pending', 'insufficient'):
            row.update(structural_candidate=1, scalar_source_check=1)
            if kind == 'insufficient':
                row['anomaly_flags_json'] = '["NO_V4_AT_SOURCE"]'
        elif kind == 'review':
            row['area_status'] = rs.BASELINE_UNKNOWN
        elif kind == 'proven':
            row.update(area_status=rs.COUNTER_FLAT_RAW_OVERSTATED,
                       aggregation_eligibility=rs.AGG_CERTIFIED,
                       corrected_recorded_area_m2=0.0,
                       controller_delta_area_m2=0.0,
                       structural_candidate=1, scalar_source_check=1)
        con = sqlite3.connect(self.db)
        con.execute(
            'INSERT INTO dji_area_calculations (flight_id, '
            'area_algorithm_version, superseded_at, report_start_date, '
            'raw_area_m2, corrected_recorded_area_m2, '
            'controller_delta_area_m2, area_status, aggregation_eligibility, '
            'structural_candidate, scalar_source_check, anomaly_flags_json) '
            'VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)',
            (flight_id, version, '2026-09-01 00:00:00' if superseded else None,
             day, row['raw_area_m2'], row['corrected_recorded_area_m2'],
             row['controller_delta_area_m2'], row['area_status'],
             row['aggregation_eligibility'], row['structural_candidate'],
             row['scalar_source_check'], row['anomaly_flags_json']))
        con.execute('INSERT INTO drone_flights (dji_flight_id, area_ha) '
                    'VALUES (?, ?)', (flight_id, 0.5))
        con.commit()
        con.close()

    def main(self, runner, *extra, **kwargs):
        argv = ['--checkpoint', self.checkpoint] + list(extra)
        if kwargs.get('db', True):
            argv = ['--db', self.db] + argv
        return tool.main(argv, runner=runner, out=self.lines.append,
                         today=TODAY)

    def load(self):
        with io.open(self.checkpoint, encoding='utf-8') as fh:
            return json.load(fh)

    def statuses(self):
        return {key: entry['status']
                for key, entry in self.load()['windows'].items()}


class Windows(unittest.TestCase):

    def test_windows_never_cross_a_month_boundary(self):
        windows = tool.build_windows(date(2026, 2, 27), date(2026, 3, 4), 3)
        self.assertEqual([tool.window_key(*w) for w in windows], [
            '2026-02-27..2026-02-28', '2026-03-01..2026-03-03',
            '2026-03-04..2026-03-04'])
        windows = tool.build_windows(date(2026, 3, 30), date(2026, 4, 2), 7)
        self.assertEqual([tool.window_key(*w) for w in windows], [
            '2026-03-30..2026-03-31', '2026-04-01..2026-04-02'])

    def test_day_windows_cover_the_whole_range_in_order(self):
        windows = tool.build_windows(date(2026, 3, 1), date(2026, 9, 23), 1)
        self.assertEqual(len(windows), (date(2026, 9, 23)
                                        - date(2026, 3, 1)).days + 1)
        self.assertEqual(windows[0], (date(2026, 3, 1), date(2026, 3, 1)))
        self.assertEqual(windows[-1], (date(2026, 9, 23), date(2026, 9, 23)))
        for (a, b), (c, _d) in zip(windows, windows[1:]):
            self.assertEqual(c, b + timedelta(days=1))

    def test_the_default_range_starts_on_the_first_of_march(self):
        args = tool.build_parser().parse_args([])
        self.assertEqual(args.date_from, '2026-03-01')
        self.assertEqual(args.window_days, 1)
        self.assertEqual(args.max_attempts, 3)


class Plan(Base):

    def test_the_plan_lists_the_windows_and_writes_nothing(self):
        runner = WindowRunner()
        before = sha256(self.db)
        code = self.main(runner, '--from', '2026-03-30', '--to', '2026-04-02',
                         '--window-days', '7', '--plan', db=False)
        self.assertEqual(code, tool.EXIT_OK)
        self.assertEqual(runner.commands, [])
        text = '\n'.join(self.lines)
        self.assertIn('2026-03-30..2026-03-31  PENDING', text)
        self.assertIn('2026-04-01..2026-04-02  PENDING', text)
        self.assertIn('2 window(s) would run', text)
        self.assertFalse(os.path.exists(os.path.dirname(self.checkpoint)))
        self.assertEqual(sha256(self.db), before)

    def test_dry_run_is_the_same_as_plan(self):
        runner = WindowRunner()
        self.assertEqual(self.main(runner, '--from', '2026-03-01', '--to',
                                   '2026-03-02', '--dry-run'), tool.EXIT_OK)
        self.assertEqual(runner.commands, [])
        self.assertFalse(os.path.exists(os.path.dirname(self.checkpoint)))

    def test_the_plan_shows_the_checkpoint_and_leaves_it_alone(self):
        self.main(WindowRunner(), '--from', '2026-03-01', '--to',
                  '2026-03-03', '--max-windows', '1')
        before = sha256(self.checkpoint)
        del self.lines[:]
        runner = WindowRunner()
        self.assertEqual(self.main(runner, '--from', '2026-03-01', '--to',
                                   '2026-03-03', '--plan'), tool.EXIT_OK)
        text = '\n'.join(self.lines)
        self.assertIn('2026-03-01..2026-03-01  DONE', text)
        self.assertIn('skip (done)', text)
        self.assertIn('2 window(s) would run', text)
        self.assertEqual(runner.commands, [])
        self.assertEqual(sha256(self.checkpoint), before)


class TheSameCycle(Base):

    def test_every_window_runs_the_daily_cycle_with_backfill_kind(self):
        runner = WindowRunner(capture={'2026-03-31': [
            {'flight_id': 11, 'reason': 'STRUCTURAL_CANDIDATE'}]})
        self.assertEqual(self.main(runner, '--from', '2026-03-31', '--to',
                                   '2026-04-01'), tool.EXIT_OK)
        steps = [s for w, s, _c in runner.commands if w == '2026-03-31']
        self.assertEqual(steps, [daily.STEP_FLIGHTS, daily.STEP_MANIFEST,
                                 daily.STEP_SOURCES, daily.STEP_RECALC])
        # Пустой манифест второго окна -- SOURCES пропущен, как и в
        # ежедневном цикле.
        steps = [s for w, s, _c in runner.commands if w == '2026-04-01']
        self.assertEqual(steps, [daily.STEP_FLIGHTS, daily.STEP_MANIFEST,
                                 daily.STEP_RECALC])
        self.assertEqual(runner.windows(), ['2026-03-31', '2026-04-01'])
        for window, step, command in runner.commands:
            if step == daily.STEP_FLIGHTS:
                self.assertEqual(command[command.index('--kind') + 1],
                                 'backfill')
            if step == daily.STEP_RECALC:
                self.assertEqual(command[command.index('--from') + 1], window)
                self.assertEqual(command[command.index('--to') + 1], window)
                self.assertEqual(command[command.index('--db') + 1], self.db)
            if step == daily.STEP_MANIFEST:
                self.assertEqual(
                    command[command.index('--stop-above') + 1], '60')

    def test_a_candidate_miss_fails_a_window_and_a_control_miss_does_not(
            self):
        cand = {'flight_id': 11, 'reason': 'STRUCTURAL_CANDIDATE'}
        ctrl = {'flight_id': 12, 'reason': 'CONTROL'}
        runner = WindowRunner(
            codes={'2026-03-01': {daily.STEP_SOURCES: 18},
                   '2026-03-02': {daily.STEP_SOURCES: 18}},
            capture={'2026-03-01': [cand, ctrl], '2026-03-02': [cand, ctrl]},
            after={'2026-03-01': [ctrl], '2026-03-02': [cand]})
        self.assertEqual(self.main(runner, '--from', '2026-03-01', '--to',
                                   '2026-03-02'), tool.EXIT_INCOMPLETE)
        windows = self.load()['windows']
        warned = windows['2026-03-01..2026-03-01']
        self.assertEqual(warned['status'], tool.STATUS_DONE)
        self.assertEqual(warned['outcome'], daily.OUTCOME_WARNINGS)
        self.assertEqual(warned['warnings'], [daily.WARNING_CONTROL_EVIDENCE])
        failed = windows['2026-03-02..2026-03-02']
        self.assertEqual(failed['status'], tool.STATUS_FAILED)
        self.assertEqual(failed['failure'], daily.FAILURE_CANDIDATE_EVIDENCE)
        self.assertEqual(failed['evidence_misses']['candidates'], [11])
        self.assertEqual(failed['last_exit'], 5)


class Resume(Base):

    RANGE = ('--from', '2026-03-01', '--to', '2026-03-05')

    def test_max_windows_stops_with_8_and_the_same_command_resumes(self):
        first = WindowRunner()
        self.assertEqual(self.main(first, *self.RANGE + ('--max-windows',
                                                         '2')),
                         tool.EXIT_STOPPED)
        self.assertEqual(first.windows(), ['2026-03-01', '2026-03-02'])
        self.assertEqual(set(self.statuses().values()), {tool.STATUS_DONE})
        self.assertEqual(len(self.statuses()), 2)
        second = WindowRunner()
        self.assertEqual(self.main(second, *self.RANGE), tool.EXIT_OK)
        # Законченные окна не проходятся заново.
        self.assertEqual(second.windows(), ['2026-03-03', '2026-03-04',
                                            '2026-03-05'])
        self.assertEqual(len(self.statuses()), 5)
        third = WindowRunner()
        self.assertEqual(self.main(third, *self.RANGE), tool.EXIT_OK)
        self.assertEqual(third.commands, [])

    def test_force_runs_done_windows_again(self):
        self.main(WindowRunner(), *self.RANGE)
        runner = WindowRunner()
        self.assertEqual(self.main(runner, *self.RANGE + ('--force',)),
                         tool.EXIT_OK)
        self.assertEqual(len(runner.windows()), 5)

    def test_a_failed_window_is_retried_on_resume_and_can_finish(self):
        broken = WindowRunner(codes={'2026-03-02': {daily.STEP_RECALC: 1}})
        self.assertEqual(self.main(broken, *self.RANGE),
                         tool.EXIT_INCOMPLETE)
        # Упавшее окно не останавливает проход.
        self.assertEqual(broken.windows(), ['2026-03-01', '2026-03-02',
                                            '2026-03-03', '2026-03-04',
                                            '2026-03-05'])
        entry = self.load()['windows']['2026-03-02..2026-03-02']
        self.assertEqual((entry['status'], entry['attempts'],
                          entry['last_exit']), (tool.STATUS_FAILED, 1, 3))
        fixed = WindowRunner()
        self.assertEqual(self.main(fixed, *self.RANGE), tool.EXIT_OK)
        self.assertEqual(fixed.windows(), ['2026-03-02'])
        entry = self.load()['windows']['2026-03-02..2026-03-02']
        self.assertEqual((entry['status'], entry['attempts']),
                         (tool.STATUS_DONE, 2))

    def test_a_window_gives_up_after_max_attempts_and_is_skipped(self):
        rng = ('--from', '2026-03-01', '--to', '2026-03-02',
               '--max-attempts', '2')
        broken = {'2026-03-02': {daily.STEP_MANIFEST: 23}}
        self.assertEqual(self.main(WindowRunner(codes=broken), *rng),
                         tool.EXIT_INCOMPLETE)
        self.assertEqual(self.statuses()['2026-03-02..2026-03-02'],
                         tool.STATUS_FAILED)
        self.assertEqual(self.main(WindowRunner(codes=broken), *rng),
                         tool.EXIT_INCOMPLETE)
        self.assertEqual(self.statuses()['2026-03-02..2026-03-02'],
                         tool.STATUS_GAVE_UP)
        skipped = WindowRunner()
        self.assertEqual(self.main(skipped, *rng), tool.EXIT_INCOMPLETE)
        self.assertEqual(skipped.commands, [])
        retried = WindowRunner()
        self.assertEqual(self.main(retried, *rng + ('--retry-gave-up',)),
                         tool.EXIT_OK)
        self.assertEqual(retried.windows(), ['2026-03-02'])
        self.assertEqual(self.statuses()['2026-03-02..2026-03-02'],
                         tool.STATUS_DONE)

    def test_a_new_algorithm_version_reruns_done_windows(self):
        self.main(WindowRunner(), *self.RANGE)
        saved = tool.algorithm_version
        tool.algorithm_version = lambda: VERSION + '-next'
        self.addCleanup(setattr, tool, 'algorithm_version', saved)
        del self.lines[:]
        self.main(WindowRunner(), *self.RANGE + ('--plan',))
        self.assertIn('run (algorithm changed)', '\n'.join(self.lines))
        runner = WindowRunner()
        self.assertEqual(self.main(runner, *self.RANGE), tool.EXIT_OK)
        self.assertEqual(len(runner.windows()), 5)
        self.assertEqual({e['area_algorithm_version']
                          for e in self.load()['windows'].values()},
                         {VERSION + '-next'})

    def test_a_checkpoint_of_another_range_is_refused_untouched(self):
        self.main(WindowRunner(), *self.RANGE)
        before = sha256(self.checkpoint)
        for extra in (('--from', '2026-03-01', '--to', '2026-03-06'),
                      self.RANGE + ('--window-days', '2')):
            runner = WindowRunner()
            del self.lines[:]
            self.assertEqual(self.main(runner, *extra), tool.EXIT_USAGE)
            self.assertEqual(runner.commands, [])
            self.assertIn('belongs to --from 2026-03-01 --to 2026-03-05',
                          '\n'.join(self.lines))
        self.assertEqual(sha256(self.checkpoint), before)

    def test_a_checkpoint_of_another_database_is_refused_untouched(self):
        # Окна, законченные на копии, не должны молча считаться
        # законченными на другой базе при том же пути контрольной точки.
        self.main(WindowRunner(), *self.RANGE)
        self.assertEqual(self.load()['database'], tool.database_key(self.db))
        before = sha256(self.checkpoint)
        other = os.path.join(self.tmp, 'copy', 'transport.db')
        os.makedirs(os.path.dirname(other))
        shutil.copyfile(self.db, other)
        runner = WindowRunner()
        del self.lines[:]
        code = tool.main(['--db', other, '--checkpoint', self.checkpoint]
                         + list(self.RANGE), runner=runner,
                         out=self.lines.append, today=TODAY)
        self.assertEqual(code, tool.EXIT_USAGE)
        self.assertEqual(runner.commands, [])
        self.assertIn('belongs to the database', '\n'.join(self.lines))
        self.assertEqual(sha256(self.checkpoint), before)
        # Отрицательный контроль: та же база другим написанием пути --
        # это продолжение, а не отказ.
        same = WindowRunner()
        spelled = os.path.join(os.path.dirname(self.db), '.',
                               os.path.basename(self.db))
        self.assertEqual(tool.main(['--db', spelled, '--checkpoint',
                                    self.checkpoint] + list(self.RANGE),
                                   runner=same, out=self.lines.append,
                                   today=TODAY), tool.EXIT_OK)
        self.assertEqual(same.commands, [])

    def test_a_checkpoint_without_a_database_is_bound_on_resume(self):
        self.main(WindowRunner(), *self.RANGE + ('--max-windows', '1'))
        document = self.load()
        del document['database']
        with io.open(self.checkpoint, 'w', encoding='utf-8') as fh:
            fh.write(json.dumps(document))
        runner = WindowRunner()
        self.assertEqual(self.main(runner, *self.RANGE), tool.EXIT_OK)
        self.assertEqual(len(runner.windows()), 4)
        self.assertEqual(self.load()['database'], tool.database_key(self.db))

    def test_an_unreadable_checkpoint_is_refused_untouched(self):
        os.makedirs(os.path.dirname(self.checkpoint))
        with open(self.checkpoint, 'w') as fh:
            fh.write('{half a file')
        runner = WindowRunner()
        self.assertEqual(self.main(runner, *self.RANGE), tool.EXIT_USAGE)
        self.assertEqual(runner.commands, [])
        with open(self.checkpoint) as fh:
            self.assertEqual(fh.read(), '{half a file')


class Busy(Base):

    RANGE = ('--from', '2026-03-01', '--to', '2026-03-03')

    def test_a_busy_cycle_lock_stops_with_7_and_marks_the_window(self):
        holder = runlock.RunLock(runlock.cycle_lock_path(self.db),
                                 purpose='scheduled-cycle')
        self.assertTrue(holder.acquire())
        runner = WindowRunner()
        try:
            self.assertEqual(self.main(runner, *self.RANGE), tool.EXIT_BUSY)
        finally:
            holder.release()
        self.assertEqual(runner.commands, [])
        entry = self.load()['windows']['2026-03-01..2026-03-01']
        self.assertEqual((entry['status'], entry['attempts'],
                          entry['last_exit']), (tool.STATUS_BUSY, 0, 7))
        self.assertIn('scheduled-cycle', '\n'.join(self.lines))
        resumed = WindowRunner()
        self.assertEqual(self.main(resumed, *self.RANGE), tool.EXIT_OK)
        self.assertEqual(resumed.windows(), ['2026-03-01', '2026-03-02',
                                             '2026-03-03'])

    def test_a_second_backfill_on_the_same_checkpoint_is_refused(self):
        guard = runlock.RunLock(os.path.abspath(self.checkpoint) + '.lock',
                                purpose='other-backfill')
        self.assertTrue(guard.acquire())
        self.addCleanup(guard.release)
        runner = WindowRunner()
        self.assertEqual(self.main(runner, *self.RANGE), tool.EXIT_BUSY)
        self.assertEqual(runner.commands, [])
        self.assertFalse(os.path.exists(self.checkpoint))


class Refusals(Base):

    def test_a_missing_database_is_code_2_and_creates_nothing(self):
        runner = WindowRunner()
        code = tool.main(['--db', os.path.join(self.tmp, 'absent.db'),
                          '--checkpoint', self.checkpoint, '--from',
                          '2026-03-01', '--to', '2026-03-02'], runner=runner,
                         out=self.lines.append, today=TODAY)
        self.assertEqual(code, tool.EXIT_NO_DATABASE)
        self.assertEqual(runner.commands, [])
        self.assertFalse(os.path.exists(os.path.dirname(self.checkpoint)))
        self.assertFalse(os.path.exists(os.path.join(self.tmp, 'absent.db')))

    def test_bad_arguments_are_code_1(self):
        for extra in (('--window-days', '8'), ('--window-days', '0'),
                      ('--from', '2026-03-05', '--to', '2026-03-01'),
                      ('--to', '2026-09-24'), ('--from', 'march'),
                      ('--max-attempts', '0'), ('--max-windows', '0'),
                      ('--lock-wait', '-1')):
            runner = WindowRunner()
            self.assertEqual(self.main(runner, *extra), tool.EXIT_USAGE,
                             extra)
            self.assertEqual(runner.commands, [])
        self.assertEqual(self.main(WindowRunner(), '--from', '2026-03-01',
                                   '--to', '2026-03-02', db=False),
                         tool.EXIT_USAGE)


class RawAndSummary(Base):
    """Синтетические расчёты: RAW не тронут, итог и список считаются верно."""

    def seed(self):
        self.add_calc(1001, '2026-03-01', 'pending')
        self.add_calc(1002, '2026-03-01', 'review')
        self.add_calc(1003, '2026-03-01', 'insufficient')
        self.add_calc(1004, '2026-03-01', 'normal')
        self.add_calc(1005, '2026-03-01', 'proven')
        self.add_calc(1006, '2026-03-01', 'pending', superseded=True)
        self.add_calc(1007, '2026-03-01', 'pending', version='old-version')
        self.add_calc(1010, '2026-03-02', 'review')
        self.add_calc(1020, '2026-04-01', 'insufficient')

    def dump(self):
        con = sqlite3.connect(self.db)
        try:
            return (con.execute('SELECT * FROM drone_flights ORDER BY id')
                    .fetchall(),
                    con.execute('SELECT * FROM dji_area_calculations ORDER '
                                'BY id').fetchall())
        finally:
            con.close()

    def test_the_backfill_never_mutates_raw_or_calculations(self):
        self.seed()
        before_rows, before_hash = self.dump(), sha256(self.db)
        self.assertEqual(self.main(WindowRunner(), '--from', '2026-03-01',
                                   '--to', '2026-04-01'), tool.EXIT_OK)
        self.assertEqual(self.dump(), before_rows)
        self.assertEqual(sha256(self.db), before_hash)
        # Отрицательный контроль: сравнение замечает изменение RAW.
        con = sqlite3.connect(self.db)
        con.execute('UPDATE drone_flights SET area_ha = area_ha + 1 '
                    'WHERE dji_flight_id = 1001')
        con.commit()
        con.close()
        self.assertNotEqual(self.dump(), before_rows)

    def test_the_summary_and_the_list_for_manual_review(self):
        self.seed()
        runner = WindowRunner(codes={'2026-03-03': {daily.STEP_RECALC: 1}})
        self.assertEqual(self.main(runner, '--from', '2026-03-01', '--to',
                                   '2026-04-01', '--max-attempts', '1'),
                         tool.EXIT_INCOMPLETE)
        entry = self.load()['windows']['2026-03-01..2026-03-01']
        unresolved = entry['unresolved']
        self.assertEqual((unresolved['pending'], unresolved['review'],
                          unresolved['no_v4_at_source']), (1, 1, 1))
        self.assertEqual(unresolved['flight_ids'], [1001, 1002, 1003])
        self.assertEqual(entry['area_algorithm_version'], VERSION)

        directory = os.path.dirname(self.checkpoint)
        with io.open(os.path.join(directory, tool.SUMMARY_FILE),
                     encoding='ascii') as fh:
            summary = json.load(fh)
        march, april = summary['months']['2026-03'], \
            summary['months']['2026-04']
        self.assertEqual(march, {'windows_total': 31, 'done': 30,
                                 'failed': 0, 'gave_up': 1, 'pending': 0,
                                 'records_pending': 1, 'records_review': 2,
                                 'records_insufficient': 1})
        self.assertEqual(april['records_insufficient'], 1)
        self.assertEqual(summary['totals']['windows_total'], 32)
        self.assertEqual(summary['totals']['records_review'], 2)

        with io.open(os.path.join(directory, tool.UNRESOLVED_FILE),
                     encoding='utf-8', newline='') as fh:
            rows = list(csv.reader(fh))
        self.assertEqual(rows[0], ['month', 'window', 'flight_id', 'class',
                                   'reason'])
        body = rows[1:]
        self.assertIn(['2026-03', '2026-03-01..2026-03-01', '1001',
                       tool.CLASS_PENDING,
                       'STRUCTURAL_MATCH_WITHOUT_VALIDATED_INTERVAL'], body)
        self.assertIn(['2026-03', '2026-03-01..2026-03-01', '1002',
                       tool.CLASS_REVIEW, 'BASELINE_UNKNOWN'], body)
        self.assertIn(['2026-03', '2026-03-01..2026-03-01', '1003',
                       tool.CLASS_INSUFFICIENT, 'NO_V4_AT_SOURCE'], body)
        self.assertIn(['2026-03', '2026-03-02..2026-03-02', '1010',
                       tool.CLASS_REVIEW, 'BASELINE_UNKNOWN'], body)
        self.assertIn(['2026-04', '2026-04-01..2026-04-01', '1020',
                       tool.CLASS_INSUFFICIENT, 'NO_V4_AT_SOURCE'], body)
        # Период, который не удалось закрыть, тоже в списке.
        self.assertIn(['2026-03', '2026-03-03..2026-03-03', '',
                       'WINDOW_GAVE_UP', 'STEP_FAILED exit 3'], body)
        named = {row[2] for row in body}
        # Решённые, замещённые и чужой версии -- не в списке.
        for flight_id in ('1004', '1005', '1006', '1007'):
            self.assertNotIn(flight_id, named)
        self.assertEqual(len(body), 6)

    def test_console_output_is_ascii_only(self):
        self.seed()
        self.main(WindowRunner(), '--from', '2026-03-01', '--to',
                  '2026-03-02')
        self.main(WindowRunner(), '--from', '2026-03-01', '--to',
                  '2026-03-02', '--plan')
        text = '\n'.join(self.lines)
        self.assertIn('BACKFILL SUMMARY', text)
        self.assertTrue(all(ord(ch) < 128 for ch in text), text)


if __name__ == '__main__':
    unittest.main()
