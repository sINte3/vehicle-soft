# -*- coding: utf-8 -*-
"""Самопроверка tools/dji_area_daily.py -- ежедневного цикла одной командой.

Что здесь держится:

* порядок шагов определён зависимостями: FLIGHTS -> MANIFEST -> SOURCES ->
  RECALC, и пересчёт стоит ПОСЛЕ захвата;
* источники запрашиваются только по ids-файлу манифеста -- никакого сбора по
  всему парку;
* слишком большой манифест останавливает цикл ДО обращения к DJI;
* упавший шаг останавливает цикл, а неполный сбор (код 18) -- нет: пересчёт
  выполняется, и следующий прогон добирает недостающее;
* вчерашний ids-файл не переживает сегодняшний отказ манифеста;
* `--no-dji` не запускает ни одного шага, обращающегося к кабинету;
* повторный прогон на тех же уликах проверяем машинно: `--expect-unchanged`.

Команды не исполняются -- подставной исполнитель их записывает. Stdlib.

Запуск:  python tools\\test_dji_area_daily.py
"""

import io
import json
import os
import shutil
import sys
import tempfile
import unittest
from datetime import date

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if REPO_ROOT not in sys.path:
    sys.path.insert(0, REPO_ROOT)

from tools import dji_area_daily as tool  # noqa: E402

TODAY = date(2026, 9, 18)


class FakeRunner(object):
    """Записывает команды и играет роли сборщика и пересчёта."""

    def __init__(self, ids=(701, 702, 703), codes=None, writes=None):
        self.ids = list(ids)
        self.codes = codes or {}
        self.writes = writes or {'new': 12}
        self.commands = []

    def step_of(self, command):
        text = ' '.join(command)
        if 'area_manifest' in text:
            return tool.STEP_MANIFEST
        if '--sources' in command:
            return tool.STEP_SOURCES
        if 'dji_area_recalc.py' in text:
            return tool.STEP_RECALC
        return tool.STEP_FLIGHTS

    def __call__(self, command, cwd):
        step = self.step_of(command)
        self.commands.append((step, list(command)))
        code = self.codes.get(step, 0)
        if step == tool.STEP_MANIFEST and code in (0, 22):
            out = command[command.index('--out') + 1]
            summary = command[command.index('--summary') + 1]
            with io.open(out, 'w', encoding='utf-8') as fh:
                fh.write('\n'.join(str(i) for i in self.ids) + '\n')
            with io.open(summary, 'w', encoding='utf-8') as fh:
                json.dump({'capture': [{'flight_id': i} for i in self.ids],
                           'counts': {'candidates_need_capture': 1,
                                      'capture_total': len(self.ids),
                                      'candidates_no_v4_at_source': 0}}, fh)
        if step == tool.STEP_RECALC and code == 0:
            path = command[command.index('--json') + 1]
            with io.open(path, 'w', encoding='utf-8') as fh:
                json.dump({'flights_in_period': 12,
                           'structural_candidates': 1,
                           'calc_writes': self.writes}, fh)
        return code

    def steps(self):
        return [step for step, _cmd in self.commands]


class Base(unittest.TestCase):

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix='area_daily_')
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        self.db = os.path.join(self.tmp, 'transport.db')
        with open(self.db, 'wb') as fh:
            fh.write(b'not a real database; the runner is fake')
        self.work = os.path.join(self.tmp, 'work')
        self.lines = []

    def run_cycle(self, runner, *extra):
        args = tool.build_parser().parse_args(
            ['--db', self.db, '--work-dir', self.work] + list(extra))
        return tool.execute(args, runner=runner, today=TODAY,
                            out=self.lines.append)


class TheOrder(Base):

    def test_the_four_steps_run_in_dependency_order(self):
        runner = FakeRunner()
        self.assertEqual(self.run_cycle(runner), tool.EXIT_OK)
        self.assertEqual(runner.steps(), [tool.STEP_FLIGHTS,
                                          tool.STEP_MANIFEST,
                                          tool.STEP_SOURCES,
                                          tool.STEP_RECALC])

    def test_the_window_is_the_last_three_report_days(self):
        runner = FakeRunner()
        self.run_cycle(runner)
        manifest = dict(runner.commands)[tool.STEP_MANIFEST]
        self.assertEqual(manifest[manifest.index('--from') + 1], '2026-09-16')
        self.assertEqual(manifest[manifest.index('--to') + 1], '2026-09-18')
        recalc = dict(runner.commands)[tool.STEP_RECALC]
        self.assertEqual(recalc[recalc.index('--from') + 1], '2026-09-16')
        self.assertIn('--apply', recalc)

    def test_the_flight_walk_is_a_day_wider_than_the_window(self):
        runner = FakeRunner()
        self.run_cycle(runner)
        flights = dict(runner.commands)[tool.STEP_FLIGHTS]
        self.assertEqual(flights[flights.index('--from') + 1], '2026-09-15')

    def test_sources_are_addressed_by_the_manifest_file_only(self):
        runner = FakeRunner()
        self.run_cycle(runner)
        sources = dict(runner.commands)[tool.STEP_SOURCES]
        manifest = dict(runner.commands)[tool.STEP_MANIFEST]
        self.assertEqual(sources[sources.index('--ids-file') + 1],
                         manifest[manifest.index('--out') + 1])
        self.assertIn('--send-sources', sources)
        # Сбора по всему парку нет: источникам период не передаётся.
        self.assertNotIn('--from', sources)
        self.assertNotIn('--to', sources)


class Stops(Base):

    def test_a_manifest_that_is_too_large_stops_before_dji(self):
        runner = FakeRunner(codes={tool.STEP_MANIFEST: 22})
        self.assertEqual(self.run_cycle(runner), tool.EXIT_MANIFEST_TOO_LARGE)
        self.assertNotIn(tool.STEP_SOURCES, runner.steps())
        self.assertNotIn(tool.STEP_RECALC, runner.steps())
        self.assertTrue(any('NOT contacted' in line for line in self.lines))

    def test_an_unavailable_manifest_stops_the_cycle(self):
        runner = FakeRunner(codes={tool.STEP_MANIFEST: 23})
        self.assertEqual(self.run_cycle(runner), tool.EXIT_STEP_FAILED)
        self.assertNotIn(tool.STEP_SOURCES, runner.steps())

    def test_a_failed_flight_walk_stops_everything(self):
        runner = FakeRunner(codes={tool.STEP_FLIGHTS: 2})
        self.assertEqual(self.run_cycle(runner), tool.EXIT_STEP_FAILED)
        self.assertEqual(runner.steps(), [tool.STEP_FLIGHTS])

    def test_incomplete_sources_still_recalculate_and_say_so(self):
        runner = FakeRunner(codes={tool.STEP_SOURCES: 18})
        self.assertEqual(self.run_cycle(runner), tool.EXIT_SOURCES_INCOMPLETE)
        self.assertIn(tool.STEP_RECALC, runner.steps())

    def test_another_sources_failure_does_stop(self):
        # Отрицательный контроль: снисхождение есть только к коду 18.
        runner = FakeRunner(codes={tool.STEP_SOURCES: 19})
        self.assertEqual(self.run_cycle(runner), tool.EXIT_STEP_FAILED)
        self.assertNotIn(tool.STEP_RECALC, runner.steps())

    def test_an_empty_manifest_skips_sources_and_still_recalculates(self):
        runner = FakeRunner(ids=())
        self.assertEqual(self.run_cycle(runner), tool.EXIT_OK)
        self.assertNotIn(tool.STEP_SOURCES, runner.steps())
        self.assertIn(tool.STEP_RECALC, runner.steps())

    def test_a_stale_ids_file_does_not_survive_a_failed_manifest(self):
        os.makedirs(self.work)
        stale = os.path.join(self.work, 'area_ids.txt')
        with open(stale, 'w') as fh:
            fh.write('999\n')
        runner = FakeRunner(codes={tool.STEP_MANIFEST: 23})
        self.run_cycle(runner)
        self.assertFalse(os.path.exists(stale))

    def test_a_missing_database_is_code_2_and_nothing_runs(self):
        runner = FakeRunner()
        args = tool.build_parser().parse_args(
            ['--db', os.path.join(self.tmp, 'absent.db'), '--work-dir',
             self.work])
        self.assertEqual(tool.execute(args, runner=runner, today=TODAY,
                                      out=self.lines.append),
                         tool.EXIT_NO_DATABASE)
        self.assertEqual(runner.commands, [])


class NoDji(Base):

    def test_no_dji_runs_only_the_steps_that_stay_at_home(self):
        runner = FakeRunner()
        self.assertEqual(self.run_cycle(runner, '--no-dji'), tool.EXIT_OK)
        self.assertEqual(runner.steps(), [tool.STEP_MANIFEST,
                                          tool.STEP_RECALC])


class CollectorHostWithoutTheDatabase(Base):
    """На площадке сборщик стоит на рабочей машине, а база -- на сервере."""

    def run_without_db(self, runner, *extra):
        args = tool.build_parser().parse_args(
            ['--work-dir', self.work] + list(extra))
        return tool.execute(args, runner=runner, today=TODAY,
                            out=self.lines.append)

    def test_skip_recalc_runs_the_three_collector_steps_and_needs_no_db(self):
        runner = FakeRunner()
        self.assertEqual(self.run_without_db(runner, '--skip-recalc'),
                         tool.EXIT_OK)
        self.assertEqual(runner.steps(), [tool.STEP_FLIGHTS,
                                          tool.STEP_MANIFEST,
                                          tool.STEP_SOURCES])

    def test_without_the_flag_a_missing_db_argument_is_a_usage_error(self):
        runner = FakeRunner()
        self.assertEqual(self.run_without_db(runner), tool.EXIT_USAGE)
        self.assertEqual(runner.commands, [])

    def test_the_size_guard_still_stops_before_sources(self):
        runner = FakeRunner(codes={tool.STEP_MANIFEST: 22})
        self.assertEqual(self.run_without_db(runner, '--skip-recalc'),
                         tool.EXIT_MANIFEST_TOO_LARGE)
        self.assertNotIn(tool.STEP_SOURCES, runner.steps())

    def test_skipping_both_halves_is_refused(self):
        runner = FakeRunner()
        self.assertEqual(
            self.run_without_db(runner, '--skip-recalc', '--no-dji'),
            tool.EXIT_USAGE)
        self.assertEqual(runner.commands, [])


class DatabaseHostWithoutTheCollector(Base):
    """Серверная половина: только пересчёт, тем же окном, что и сборщик."""

    def test_recalc_only_runs_the_recalculation_and_nothing_else(self):
        runner = FakeRunner()
        self.assertEqual(self.run_cycle(runner, '--recalc-only'),
                         tool.EXIT_OK)
        self.assertEqual(runner.steps(), [tool.STEP_RECALC])

    def test_both_halves_agree_on_the_window(self):
        collector, server = FakeRunner(), FakeRunner()
        args = tool.build_parser().parse_args(
            ['--work-dir', self.work, '--skip-recalc'])
        tool.execute(args, runner=collector, today=TODAY,
                     out=self.lines.append)
        self.run_cycle(server, '--recalc-only')
        manifest = dict(collector.commands)[tool.STEP_MANIFEST]
        recalc = dict(server.commands)[tool.STEP_RECALC]
        for flag in ('--from', '--to'):
            self.assertEqual(manifest[manifest.index(flag) + 1],
                             recalc[recalc.index(flag) + 1])

    def test_recalc_only_still_refuses_a_missing_database(self):
        runner = FakeRunner()
        args = tool.build_parser().parse_args(
            ['--db', os.path.join(self.tmp, 'absent.db'), '--work-dir',
             self.work, '--recalc-only'])
        self.assertEqual(tool.execute(args, runner=runner, today=TODAY,
                                      out=self.lines.append),
                         tool.EXIT_NO_DATABASE)
        self.assertEqual(runner.commands, [])

    def test_recalc_only_honours_expect_unchanged(self):
        runner = FakeRunner(writes={'unchanged': 11, 'new': 1})
        self.assertEqual(
            self.run_cycle(runner, '--recalc-only', '--expect-unchanged'),
            tool.EXIT_NOT_IDEMPOTENT)

    def test_recalc_only_leaves_the_collector_files_alone(self):
        os.makedirs(self.work)
        kept = os.path.join(self.work, 'area_ids.txt')
        with open(kept, 'w') as fh:
            fh.write('701\n')
        self.run_cycle(FakeRunner(), '--recalc-only')
        self.assertTrue(os.path.exists(kept))

    def test_two_mode_flags_are_refused(self):
        for pair in (('--recalc-only', '--skip-recalc'),
                     ('--recalc-only', '--no-dji')):
            runner = FakeRunner()
            self.assertEqual(self.run_cycle(runner, *pair), tool.EXIT_USAGE)
            self.assertEqual(runner.commands, [])


class TheContractBetweenTheProcesses(unittest.TestCase):

    def test_exit_codes_are_the_literal_numbers_the_runbook_reads(self):
        # [REASON]: блоки PowerShell сравнивают $LASTEXITCODE с ЧИСЛОМ.
        self.assertEqual(
            (tool.EXIT_OK, tool.EXIT_USAGE, tool.EXIT_NO_DATABASE,
             tool.EXIT_STEP_FAILED, tool.EXIT_MANIFEST_TOO_LARGE,
             tool.EXIT_SOURCES_INCOMPLETE, tool.EXIT_NOT_IDEMPOTENT),
            (0, 1, 2, 3, 4, 5, 6))

    def test_the_collector_codes_are_the_collectors_own(self):
        # [REASON]: цикл не импортирует сборщик -- это отдельный процесс со
        # своим venv, -- поэтому два его кода записаны здесь числами. Разойдись
        # они с настоящими, остановка «манифест слишком велик» молча
        # превратилась бы в обычный сбой шага, а снисхождение к неполному
        # сбору -- в остановку цикла. Сверяет их только тест.
        from drone_collector import area_manifest, main as collector_main
        self.assertEqual(tool.COLLECTOR_MANIFEST_TOO_LARGE,
                         area_manifest.EXIT_MANIFEST_TOO_LARGE)
        self.assertEqual(tool.COLLECTOR_SOURCES_INCOMPLETE,
                         collector_main.EXIT_SOURCES_INCOMPLETE)
        self.assertEqual(tool.DEFAULT_STOP_ABOVE, 50)


class Idempotence(Base):

    def test_a_second_run_that_writes_nothing_passes(self):
        runner = FakeRunner(ids=(), writes={'unchanged': 12})
        self.assertEqual(self.run_cycle(runner, '--expect-unchanged'),
                         tool.EXIT_OK)

    def test_a_second_run_that_writes_is_caught(self):
        runner = FakeRunner(ids=(), writes={'unchanged': 11, 'new': 1})
        self.assertEqual(self.run_cycle(runner, '--expect-unchanged'),
                         tool.EXIT_NOT_IDEMPOTENT)

    def test_without_the_flag_a_writing_run_is_a_normal_run(self):
        # Отрицательный контроль: первый прогон обязан иметь право писать.
        runner = FakeRunner(writes={'new': 12})
        self.assertEqual(self.run_cycle(runner), tool.EXIT_OK)


class Window(Base):

    def test_a_window_longer_than_the_cap_is_refused(self):
        runner = FakeRunner()
        self.assertEqual(self.run_cycle(runner, '--days', '30'),
                         tool.EXIT_USAGE)
        self.assertEqual(runner.commands, [])

    def test_an_explicit_window_is_passed_through(self):
        runner = FakeRunner()
        self.run_cycle(runner, '--from', '2026-09-01', '--to', '2026-09-03')
        recalc = dict(runner.commands)[tool.STEP_RECALC]
        self.assertEqual(recalc[recalc.index('--to') + 1], '2026-09-03')

    def test_console_output_is_ascii_only(self):
        self.run_cycle(FakeRunner())
        text = '\n'.join(self.lines)
        self.assertTrue(text)
        self.assertTrue(all(ord(ch) < 128 for ch in text), text)


if __name__ == '__main__':
    unittest.main()
