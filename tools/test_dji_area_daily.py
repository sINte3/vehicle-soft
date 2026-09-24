# -*- coding: utf-8 -*-
"""Самопроверка tools/dji_area_daily.py -- ежедневного цикла одной командой.

Что здесь держится:

* порядок шагов определён зависимостями: FLIGHTS -> MANIFEST -> SOURCES ->
  RECALC, и пересчёт стоит ПОСЛЕ захвата;
* источники запрашиваются только по ids-файлу манифеста -- никакого сбора по
  всему парку;
* слишком большой манифест останавливает цикл ДО обращения к DJI;
* упавший FLIGHTS или MANIFEST останавливает цикл; упавший SOURCES -- нет:
  пересчёт идёт по уже сохранённым доказательствам, а цикл всё равно FAILED
  (блок E DRONE-AREA-CONTROL-V2-MEGA);
* неполный сбор (код 18) проверяется повторным манифестом VERIFY: кандидат
  без V4 -- FAILED, код 5; только контроль без V4 -- SUCCESS_WITH_WARNINGS,
  код 0; контроль, срезанный лимитом до сбора, потерей не считается;
  непроверенное -- не успех;
* итог каждого прогона ложится в last_cycle.json одной схемой;
* блокировка цикла: занято -- код 7 и ни одного шага; журнал прогонов на
  настоящей SQLite: очередь -> RUNNING -> итог, «нечего делать», BUSY,
  секрет не доходит до журнала, повтор безопасен;
* вчерашний ids-файл не переживает сегодняшний отказ манифеста;
* `--no-dji` не запускает ни одного шага, обращающегося к кабинету;
* повторный прогон на тех же уликах проверяем машинно: `--expect-unchanged`.

Команды не исполняются -- подставной исполнитель их записывает. Stdlib.

Запуск:  python tools\\test_dji_area_daily.py
"""

import contextlib
import io
import json
import os
import shutil
import sqlite3
import sys
import tempfile
import threading
import time
import unittest
from datetime import date, datetime, timedelta, timezone

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if REPO_ROOT not in sys.path:
    sys.path.insert(0, REPO_ROOT)

from tools import dji_area_daily as tool  # noqa: E402
from drone_collector import runlock  # noqa: E402

TODAY = date(2026, 9, 18)


def read_bytes(path):
    with open(path, 'rb') as fh:
        return fh.read()


def cand(flight_id):
    return {'flight_id': flight_id, 'reason': 'STRUCTURAL_CANDIDATE'}


def ctrl(flight_id):
    return {'flight_id': flight_id, 'reason': 'CONTROL',
            'control_kind': 'RANDOM'}


class FakeRunner(object):
    """Записывает команды и играет роли сборщика и пересчёта.

    ``capture`` -- записи манифеста шага 2 (по умолчанию ``ids`` без
    причины, как прежде); ``after`` -- записи повторного манифеста VERIFY
    (по умолчанию те же: ничего не доехало); строка ``'garbage'`` пишет
    нечитаемый файл. ``writes`` -- словарь или список словарей по прогонам
    пересчёта. ``hooks`` -- {шаг: функция(команда)} до ответа шага.
    """

    def __init__(self, ids=(701, 702, 703), codes=None, writes=None,
                 capture=None, after=None, hooks=None, no_v4=(),
                 no_v4_after=None):
        self.ids = list(ids)
        self.capture = capture
        self.after = after
        # Кандидаты окна, для которых DJI V4 не хранит: в манифесте шага 2 и
        # в повторном (по умолчанию -- те же).
        self.no_v4 = list(no_v4)
        self.no_v4_after = (list(no_v4_after) if no_v4_after is not None
                            else self.no_v4)
        self.codes = codes or {}
        self.writes = writes or {'new': 12}
        self.hooks = hooks or {}
        self.commands = []
        self.recalcs = 0

    def step_of(self, command):
        text = ' '.join(command)
        if 'area_manifest' in text:
            out = command[command.index('--out') + 1]
            if os.path.basename(out) == 'area_ids_after.txt':
                return tool.STEP_VERIFY
            return tool.STEP_MANIFEST
        if '--sources' in command:
            return tool.STEP_SOURCES
        if 'dji_area_recalc.py' in text:
            return tool.STEP_RECALC
        return tool.STEP_FLIGHTS

    def entries(self, step):
        if self.capture is not None:
            before = [dict(e) for e in self.capture]
        else:
            before = [{'flight_id': i} for i in self.ids]
        if step == tool.STEP_VERIFY and self.after is not None:
            return self.after
        return before

    def __call__(self, command, cwd):
        step = self.step_of(command)
        self.commands.append((step, list(command)))
        if step in self.hooks:
            self.hooks[step](command)
        code = self.codes.get(step, 0)
        if step in (tool.STEP_MANIFEST, tool.STEP_VERIFY) and code in (0, 22):
            entries = self.entries(step)
            out = command[command.index('--out') + 1]
            summary = command[command.index('--summary') + 1]
            with io.open(summary, 'w', encoding='utf-8') as fh:
                if entries == 'garbage':
                    fh.write('{not json')
                    entries = []
                else:
                    candidates = (1 if self.capture is None else sum(
                        1 for e in entries if e.get('reason') != 'CONTROL'))
                    no_v4 = (self.no_v4_after if step == tool.STEP_VERIFY
                             else self.no_v4)
                    json.dump({'capture': entries, 'over_cap': False,
                               'counts': {'candidates_need_capture':
                                          candidates,
                                          'capture_total': len(entries),
                                          'candidates_no_v4_at_source':
                                          len(no_v4)},
                               'no_v4_at_source': [
                                   dict(cand(i), v4_state='NO_V4_AT_SOURCE')
                                   for i in no_v4]},
                              fh)
            with io.open(out, 'w', encoding='utf-8') as fh:
                fh.write('\n'.join(str(e['flight_id']) for e in entries)
                         + '\n')
        if step == tool.STEP_RECALC and code == 0:
            writes = self.writes
            if isinstance(writes, list):
                writes = writes[min(self.recalcs, len(writes) - 1)]
            self.recalcs += 1
            path = command[command.index('--json') + 1]
            with io.open(path, 'w', encoding='utf-8') as fh:
                json.dump({'flights_in_period': 12,
                           'structural_candidates': 1,
                           'calc_writes': writes}, fh)
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

    def cycle_result(self, runner, *extra):
        result = {}
        args = tool.build_parser().parse_args(
            ['--db', self.db, '--work-dir', self.work] + list(extra))
        code = tool.execute(args, runner=runner, today=TODAY,
                            out=self.lines.append, result=result)
        return code, result


class TheOrder(Base):

    def test_the_steps_run_in_dependency_order(self):
        # Прежнее имя: test_the_four_steps_run_in_dependency_order. Когда
        # манифест назвал кандидатов, после полного сбора идёт VERIFY --
        # узнать, для кого из них DJI V4 не хранит; без кандидатов шагов
        # по-прежнему четыре.
        runner = FakeRunner()
        self.assertEqual(self.run_cycle(runner), tool.EXIT_OK)
        self.assertEqual(runner.steps(), [tool.STEP_FLIGHTS,
                                          tool.STEP_MANIFEST,
                                          tool.STEP_SOURCES,
                                          tool.STEP_VERIFY,
                                          tool.STEP_RECALC])
        runner = FakeRunner(capture=[ctrl(702), ctrl(703)])
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

    def test_the_flight_walk_kind_is_incremental_unless_asked(self):
        runner = FakeRunner()
        self.run_cycle(runner)
        flights = dict(runner.commands)[tool.STEP_FLIGHTS]
        self.assertEqual(flights[flights.index('--kind') + 1], 'incremental')
        runner = FakeRunner()
        self.run_cycle(runner, '--flights-kind', 'backfill')
        flights = dict(runner.commands)[tool.STEP_FLIGHTS]
        self.assertEqual(flights[flights.index('--kind') + 1], 'backfill')

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
        self.assertNotIn(tool.STEP_RECALC, runner.steps())

    def test_a_failed_flight_walk_stops_everything(self):
        runner = FakeRunner(codes={tool.STEP_FLIGHTS: 2})
        self.assertEqual(self.run_cycle(runner), tool.EXIT_STEP_FAILED)
        self.assertEqual(runner.steps(), [tool.STEP_FLIGHTS])

    def test_incomplete_sources_still_recalculate_and_say_so(self):
        # Повторный манифест называет те же записи без причины -- осторожно
        # считаются кандидатами, и цикл честно говорит 5.
        runner = FakeRunner(codes={tool.STEP_SOURCES: 18})
        self.assertEqual(self.run_cycle(runner), tool.EXIT_SOURCES_INCOMPLETE)
        self.assertIn(tool.STEP_RECALC, runner.steps())

    def test_another_sources_failure_fails_but_still_recalculates(self):
        # Прежнее имя: test_another_sources_failure_does_stop. Блок E
        # DRONE-AREA-CONTROL-V2-MEGA поменял смысл намеренно: сбой SOURCES
        # по-прежнему код 3 и FAILED (отрицательный контроль -- снисхождение
        # в виде VERIFY только к коду 18), но пересчёт теперь идёт по уже
        # сохранённым доказательствам: база от упавшего сбора не испорчена,
        # а V4, доехавшие прежними прогонами, иначе ждали бы ещё сутки.
        runner = FakeRunner(codes={tool.STEP_SOURCES: 19})
        code, result = self.cycle_result(runner)
        self.assertEqual(code, tool.EXIT_STEP_FAILED)
        self.assertIn(tool.STEP_RECALC, runner.steps())
        self.assertNotIn(tool.STEP_VERIFY, runner.steps())
        self.assertEqual(result['outcome'], tool.OUTCOME_FAILED)
        self.assertEqual(result['failed_step'], tool.STEP_SOURCES)
        self.assertEqual(result['failure'], tool.FAILURE_STEP)

    def test_a_busy_collector_is_named_as_such(self):
        for step in (tool.STEP_FLIGHTS, tool.STEP_SOURCES):
            del self.lines[:]
            runner = FakeRunner(codes={step: tool.COLLECTOR_BUSY})
            code, result = self.cycle_result(runner)
            self.assertEqual(code, tool.EXIT_STEP_FAILED, step)
            self.assertEqual(result['failure'], tool.FAILURE_COLLECTOR_BUSY)
            self.assertTrue(any('collector lock' in line
                                for line in self.lines), step)
            self.assertEqual(tool.STEP_RECALC in runner.steps(),
                             step == tool.STEP_SOURCES, step)

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


class EvidenceSemantics(Base):
    """Блок E: потеря кандидата -- провал, потеря контроля -- предупреждение."""

    def incomplete(self, capture, after, **codes):
        runner = FakeRunner(capture=capture, after=after,
                            codes=dict({tool.STEP_SOURCES: 18}, **codes))
        code, result = self.cycle_result(runner)
        return code, result, runner

    def test_a_control_only_miss_is_a_warning_not_a_failure(self):
        code, result, runner = self.incomplete(
            [cand(701), ctrl(702), ctrl(703)], [ctrl(702)])
        self.assertEqual(code, tool.EXIT_OK)
        self.assertEqual(result['outcome'], tool.OUTCOME_WARNINGS)
        self.assertEqual(result['warnings'], [tool.WARNING_CONTROL_EVIDENCE])
        self.assertIsNone(result['failure'])
        self.assertEqual(result['evidence_misses'],
                         {'candidates': [], 'controls': [702]})
        self.assertEqual(runner.steps(), [
            tool.STEP_FLIGHTS, tool.STEP_MANIFEST, tool.STEP_SOURCES,
            tool.STEP_VERIFY, tool.STEP_RECALC])
        self.assertIn('  verify    candidates_missing=0 controls_missing=1',
                      self.lines)

    def test_a_candidate_miss_fails_the_cycle_after_recalculating(self):
        code, result, runner = self.incomplete(
            [cand(701), ctrl(702)], [cand(701), ctrl(702)])
        self.assertEqual(code, tool.EXIT_CANDIDATE_EVIDENCE_MISSING)
        self.assertEqual(result['outcome'], tool.OUTCOME_FAILED)
        self.assertEqual(result['failure'], tool.FAILURE_CANDIDATE_EVIDENCE)
        self.assertEqual(result['failed_step'], tool.STEP_SOURCES)
        self.assertEqual(result['evidence_misses'],
                         {'candidates': [701], 'controls': [702]})
        # Предупреждение о контроле не теряется рядом с провалом.
        self.assertIn(tool.WARNING_CONTROL_EVIDENCE, result['warnings'])
        self.assertIn(tool.STEP_RECALC, runner.steps())

    def test_a_missing_candidate_is_never_a_pass(self):
        # Все контрольные доехали, один кандидат -- нет: ни при каком раскладе
        # это не код 0.
        code, result, _runner = self.incomplete(
            [cand(701), ctrl(702), ctrl(703)], [cand(701)])
        self.assertEqual(code, tool.EXIT_CANDIDATE_EVIDENCE_MISSING)
        self.assertEqual(result['outcome'], tool.OUTCOME_FAILED)

    def test_an_entry_without_a_reason_counts_as_a_candidate(self):
        code, result, _runner = self.incomplete(
            [cand(701)], [{'flight_id': 709}])
        self.assertEqual(code, tool.EXIT_CANDIDATE_EVIDENCE_MISSING)
        self.assertEqual(result['evidence_misses']['candidates'], [709])

    def test_controls_dropped_by_the_cap_are_not_misses(self):
        # До сбора лимит пропустил один контроль; после сбора освободилось
        # место, и манифест называет 703 и 704, которых не запрашивали.
        code, result, _runner = self.incomplete(
            [cand(701), ctrl(702)], [ctrl(703), ctrl(704)])
        self.assertEqual(code, tool.EXIT_OK)
        self.assertEqual(result['outcome'], tool.OUTCOME_SUCCESS)
        self.assertEqual(result['evidence_misses'],
                         {'candidates': [], 'controls': []})
        self.assertEqual(result['warnings'], [])

    def test_everything_delivered_after_code_18_is_a_clean_success(self):
        code, result, _runner = self.incomplete([cand(701), ctrl(702)], [])
        self.assertEqual(code, tool.EXIT_OK)
        self.assertEqual(result['outcome'], tool.OUTCOME_SUCCESS)

    def test_an_unavailable_verify_is_a_failure_not_a_pass(self):
        code, result, runner = self.incomplete(
            [cand(701)], [], **{tool.STEP_VERIFY: 23})
        self.assertEqual(code, tool.EXIT_CANDIDATE_EVIDENCE_MISSING)
        self.assertEqual(result['failure'], tool.FAILURE_VERIFY_UNAVAILABLE)
        self.assertEqual(result['failed_step'], tool.STEP_VERIFY)
        self.assertIn(tool.STEP_RECALC, runner.steps())

    def test_an_unreadable_verify_file_is_a_failure(self):
        code, result, _runner = self.incomplete([cand(701)], 'garbage')
        self.assertEqual(code, tool.EXIT_CANDIDATE_EVIDENCE_MISSING)
        self.assertEqual(result['failure'], tool.FAILURE_VERIFY_UNAVAILABLE)

    def test_yesterdays_verify_file_cannot_turn_a_failure_into_a_pass(self):
        # Отрицательный контроль к предыдущему: вчерашний «всё доехало»
        # лежит на месте, сегодняшний VERIFY отказал. Если бы файл пережил
        # отказ, цикл прочёл бы его и сказал 0.
        os.makedirs(self.work)
        with io.open(os.path.join(self.work, 'area_manifest_after.json'),
                     'w', encoding='utf-8') as fh:
            json.dump({'capture': []}, fh)
        code, _result, _runner = self.incomplete(
            [cand(701)], [], **{tool.STEP_VERIFY: 23})
        self.assertEqual(code, tool.EXIT_CANDIDATE_EVIDENCE_MISSING)

    def test_verify_accepts_the_size_stop_code_because_the_file_is_written(
            self):
        code, result, _runner = self.incomplete(
            [cand(701), ctrl(702)], [ctrl(702)], **{tool.STEP_VERIFY: 22})
        self.assertEqual(code, tool.EXIT_OK)
        self.assertEqual(result['outcome'], tool.OUTCOME_WARNINGS)

    def test_a_complete_sources_run_rereads_but_judges_no_miss(self):
        # Прежнее имя: test_a_complete_sources_run_needs_no_verify. Полный
        # сбор теперь перечитывает манифест -- только ради списка «DJI не
        # хранит V4». Потери после полного сбора не судятся: повторный
        # манифест здесь нарочно тот же, что до сбора, и это всё равно успех.
        runner = FakeRunner(capture=[cand(701), ctrl(702)])
        code, result = self.cycle_result(runner)
        self.assertEqual(code, tool.EXIT_OK)
        self.assertEqual(result['outcome'], tool.OUTCOME_SUCCESS)
        self.assertEqual(result['warnings'], [])
        self.assertEqual(result['evidence_misses'],
                         {'candidates': [], 'controls': []})
        self.assertIn(tool.STEP_VERIFY, runner.steps())
        # Без кандидатов в захвате перечитывать нечего.
        runner = FakeRunner(capture=[ctrl(702)])
        code, result = self.cycle_result(runner)
        self.assertEqual(code, tool.EXIT_OK)
        self.assertNotIn(tool.STEP_VERIFY, runner.steps())

    def test_verify_is_the_same_manifest_into_other_files_and_never_dji(self):
        _code, _result, runner = self.incomplete([cand(701)], [])
        commands = dict(runner.commands)
        manifest, verify = commands[tool.STEP_MANIFEST], \
            commands[tool.STEP_VERIFY]
        self.assertIn('drone_collector.area_manifest', verify)
        self.assertNotIn('drone_collector.main', verify)
        for flag in ('--from', '--to', '--stop-above'):
            self.assertEqual(manifest[manifest.index(flag) + 1],
                             verify[verify.index(flag) + 1])
        for flag in ('--out', '--summary'):
            self.assertNotEqual(manifest[manifest.index(flag) + 1],
                                verify[verify.index(flag) + 1])

    def test_the_words_of_no_v4_are_the_ledgers_own(self):
        from dji_area import control_store as cs
        self.assertEqual(tool.WARNING_CANDIDATE_NO_V4,
                         cs.WARNING_CANDIDATE_NO_V4)
        self.assertEqual(tool.WARNING_NO_V4_CHECK_UNAVAILABLE,
                         cs.WARNING_NO_V4_CHECK)

    def test_the_worst_problem_names_the_exit_code(self):
        # Кандидат не доехал (5), а затем упал пересчёт (3): код -- 3, шаг --
        # RECALC. Кандидат не доехал, а пересчёт что-то записал при
        # --expect-unchanged: 6, как и до блока E.
        code, result, _runner = self.incomplete(
            [cand(701)], [cand(701)], **{tool.STEP_RECALC: 1})
        self.assertEqual(code, tool.EXIT_STEP_FAILED)
        self.assertEqual(result['failed_step'], tool.STEP_RECALC)
        runner = FakeRunner(capture=[cand(701)], after=[cand(701)],
                            codes={tool.STEP_SOURCES: 18})
        self.assertEqual(self.run_cycle(runner, '--expect-unchanged'),
                         tool.EXIT_NOT_IDEMPOTENT)


class ResultDocument(Base):

    KEYS = {'window', 'outcome', 'exit_code', 'steps', 'failed_step',
            'flights', 'manifest', 'evidence_misses',
            'candidates_no_v4_at_source', 'recalc', 'warnings', 'failure'}

    def test_the_result_is_written_as_json_with_the_schema(self):
        runner = FakeRunner(capture=[cand(701), ctrl(702)], after=[ctrl(702)],
                            codes={tool.STEP_SOURCES: 18})
        code, result = self.cycle_result(runner)
        with io.open(os.path.join(self.work, 'last_cycle.json'),
                     encoding='ascii') as fh:
            written = json.load(fh)
        self.assertEqual(written, json.loads(json.dumps(result)))
        self.assertEqual(set(written), self.KEYS)
        self.assertEqual(written['window'], {'from': '2026-09-16',
                                             'to': '2026-09-18'})
        self.assertEqual(written['exit_code'], code)
        self.assertEqual(written['outcome'], 'SUCCESS_WITH_WARNINGS')
        self.assertEqual(written['steps'], {'FLIGHTS': 0, 'MANIFEST': 0,
                                            'SOURCES': 18, 'VERIFY': 0,
                                            'RECALC': 0})
        self.assertEqual(set(written['manifest']),
                         {'ids', 'candidates', 'controls', 'no_v4_at_source',
                          'over_cap'})
        self.assertEqual(written['manifest']['ids'], 2)
        self.assertEqual(written['manifest']['candidates'], 1)
        self.assertEqual(written['manifest']['controls'], 1)
        self.assertEqual(written['recalc'], {'flights_in_period': 12,
                                             'structural_candidates': 1,
                                             'calc_writes': {'new': 12}})
        self.assertIsNone(written['failed_step'])
        # Не SQLite -- статистики обхода нет, и это None, а не нули.
        self.assertIsNone(written['flights'])

    def test_only_the_steps_that_ran_are_listed(self):
        runner = FakeRunner(codes={tool.STEP_FLIGHTS: 2})
        _code, result = self.cycle_result(runner)
        self.assertEqual(result['steps'], {'FLIGHTS': 2})
        self.assertEqual(result['failed_step'], 'FLIGHTS')
        self.assertIsNone(result['recalc'])

    def test_a_usage_error_writes_no_result_file(self):
        runner = FakeRunner()
        code, result = self.cycle_result(runner, '--days', '30')
        self.assertEqual(code, tool.EXIT_USAGE)
        self.assertEqual(result['failure'], tool.FAILURE_USAGE)
        self.assertFalse(os.path.exists(os.path.join(self.work,
                                                     'last_cycle.json')))


class FlightStatistics(unittest.TestCase):
    """Сводка обхода -- из `drone_sync_logs`, открытых ЗА ВРЕМЯ шага FLIGHTS."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix='area_daily_sync_')
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        self.db = os.path.join(self.tmp, 'transport.db')
        con = sqlite3.connect(self.db)
        con.execute('CREATE TABLE drone_sync_logs (id INTEGER PRIMARY KEY, '
                    'kind VARCHAR(20), started_at DATETIME, records_seen '
                    'INTEGER, records_new INTEGER, records_duplicate INTEGER, '
                    'records_unresolved INTEGER, records_error INTEGER)')
        # Час назад -- не этот обход.
        old = datetime.now(timezone.utc).replace(tzinfo=None) \
            - timedelta(hours=1)
        con.execute('INSERT INTO drone_sync_logs VALUES (NULL, ?, ?, 99, 99, '
                    '0, 0, 0)', ('incremental', old.isoformat(sep=' ')))
        con.commit()
        con.close()
        self.work = os.path.join(self.tmp, 'work')

    def insert_during_the_walk(self, command):
        now = datetime.now(timezone.utc).replace(tzinfo=None)
        con = sqlite3.connect(self.db)
        # 26 символов, как пишет SQLAlchemy.
        con.execute('INSERT INTO drone_sync_logs VALUES (NULL, ?, ?, 10, 4, '
                    '5, 1, 1)', ('incremental', now.isoformat(sep=' ')))
        # Та же минута, другой вид -- чужой сборщик, не этот цикл.
        con.execute('INSERT INTO drone_sync_logs VALUES (NULL, ?, ?, 7, 7, '
                    '0, 0, 0)', ('backfill', now.isoformat(sep=' ')))
        con.commit()
        con.close()

    def test_only_the_rows_of_this_walk_are_summed(self):
        runner = FakeRunner(hooks={tool.STEP_FLIGHTS:
                                   self.insert_during_the_walk})
        result = {}
        args = tool.build_parser().parse_args(['--db', self.db, '--work-dir',
                                               self.work])
        code = tool.execute(args, runner=runner, today=TODAY,
                            out=lambda line: None, result=result)
        self.assertEqual(code, tool.EXIT_OK)
        self.assertEqual(result['flights'], {
            'seen': 10, 'new': 4, 'duplicates': 5, 'unresolved': 1,
            'errors': 1, 'sync_runs': 1})

    def test_the_reader_never_writes_the_database(self):
        before = read_bytes(self.db)
        stats = tool.flight_stats(self.db, datetime(2020, 1, 1),
                                  datetime(2030, 1, 1))
        self.assertEqual(stats['sync_runs'], 1)
        self.assertEqual(read_bytes(self.db), before)
        self.assertIsNone(tool.flight_stats(
            os.path.join(self.tmp, 'absent.db'), datetime(2020, 1, 1),
            datetime(2030, 1, 1)))
        self.assertFalse(os.path.exists(os.path.join(self.tmp, 'absent.db')))


class NoV4AtSource(Base):
    """Кандидат, для которого DJI V4 не хранит: успех с предупреждением.

    Не провал -- сборщик получил честный ответ «V4 нет», повтор ничего не
    даст. Не чистый успех -- корректировка невозможна, запись остаётся по
    RAW как «недостаточно доказательств» (как в backfill).
    """

    def test_a_known_no_v4_candidate_is_a_warning_not_a_failure(self):
        runner = FakeRunner(capture=[cand(701)], after=[], no_v4=[705])
        code, result = self.cycle_result(runner)
        self.assertEqual(code, tool.EXIT_OK)
        self.assertEqual(result['outcome'], tool.OUTCOME_WARNINGS)
        self.assertEqual(result['warnings'], [tool.WARNING_CANDIDATE_NO_V4])
        self.assertIsNone(result['failure'])
        self.assertEqual(result['candidates_no_v4_at_source'], [705])
        self.assertEqual(result['manifest']['no_v4_at_source'], 1)
        self.assertIn(tool.STEP_RECALC, runner.steps())

    def test_a_candidate_that_turned_no_v4_during_sources_is_caught(self):
        # До сбора список пуст; сбор полный (код 0), а по кандидату 701 DJI
        # ответил «V4 нет» -- это видно только в повторном манифесте.
        runner = FakeRunner(capture=[cand(701), ctrl(702)], after=[],
                            no_v4=[], no_v4_after=[701])
        code, result = self.cycle_result(runner)
        self.assertEqual(code, tool.EXIT_OK)
        self.assertEqual(result['outcome'], tool.OUTCOME_WARNINGS)
        self.assertEqual(result['warnings'], [tool.WARNING_CANDIDATE_NO_V4])
        self.assertEqual(result['candidates_no_v4_at_source'], [701])
        self.assertIn('  verify    candidates_no_v4_at_source=1', self.lines)
        # Отрицательный контроль: тот же прогон, V4 пришёл -- чистый успех.
        runner = FakeRunner(capture=[cand(701), ctrl(702)], after=[],
                            no_v4=[], no_v4_after=[])
        code, result = self.cycle_result(runner)
        self.assertEqual((code, result['outcome'], result['warnings']),
                         (tool.EXIT_OK, tool.OUTCOME_SUCCESS, []))
        self.assertEqual(result['candidates_no_v4_at_source'], [])

    def test_a_confirmed_descriptor_absence_is_a_warning_not_a_failure(self):
        """A single candidate whose absence is proven, and one whose is not.

        Formerly `test_the_live_case_715984635_is_a_warning_not_a_failure`.
        [REASON]: renamed after staging, 24.09.2026. The old name asserted a
        live outcome that did not happen: the direct request for 715984635
        answered HTTP 200 with a non-descriptor, no 404, no control, and the
        cycle ended FAILED -- the negative control below. The first half is
        SYNTHETIC: what the cycle does IF the direct request is answered 404
        and a control confirms it (collector 0, the re-read manifest names
        the candidate no-V4-at-source: SUCCESS_WITH_WARNINGS).
        """
        live = 715984635
        runner = FakeRunner(capture=[cand(live)], after=[], no_v4=[],
                            no_v4_after=[live])
        code, result = self.cycle_result(runner)
        self.assertEqual(code, tool.EXIT_OK)
        self.assertEqual(result['outcome'], tool.OUTCOME_WARNINGS)
        self.assertEqual(result['warnings'], [tool.WARNING_CANDIDATE_NO_V4])
        self.assertIsNone(result['failure'])
        self.assertEqual(result['candidates_no_v4_at_source'], [live])
        self.assertFalse((result.get('evidence_misses') or {})
                         .get('candidates'))
        self.assertIn(tool.STEP_RECALC, runner.steps())
        # The next day the manifest no longer asks for it: no DJI visit.
        runner = FakeRunner(capture=[], no_v4=[live])
        code, result = self.cycle_result(runner)
        self.assertEqual((code, result['outcome'], result['warnings']),
                         (tool.EXIT_OK, tool.OUTCOME_WARNINGS,
                          [tool.WARNING_CANDIDATE_NO_V4]))
        self.assertNotIn(tool.STEP_SOURCES, runner.steps())
        # NEGATIVE CONTROL -- the incident as it was, and exactly what staging
        # produced on 24.09.2026 with the fix in place: the collector ended
        # in 18 and the candidate was still asked for.
        runner = FakeRunner(capture=[cand(live)], after=[cand(live)],
                            no_v4=[], no_v4_after=[],
                            codes={tool.STEP_SOURCES: 18})
        code, result = self.cycle_result(runner)
        self.assertEqual(code, tool.EXIT_CANDIDATE_EVIDENCE_MISSING)
        self.assertEqual(result['outcome'], tool.OUTCOME_FAILED)
        self.assertEqual(result['evidence_misses']['candidates'], [live])

    def test_no_v4_joins_the_verdicts_of_an_incomplete_capture(self):
        runner = FakeRunner(capture=[cand(701), ctrl(702)], after=[ctrl(702)],
                            no_v4=[], no_v4_after=[701],
                            codes={tool.STEP_SOURCES: 18})
        code, result = self.cycle_result(runner)
        self.assertEqual(code, tool.EXIT_OK)
        self.assertEqual(result['warnings'], [tool.WARNING_CONTROL_EVIDENCE,
                                              tool.WARNING_CANDIDATE_NO_V4])
        self.assertEqual(result['evidence_misses'],
                         {'candidates': [], 'controls': [702]})
        # Кандидат, оставшийся без V4 из-за сбоя сбора, -- по-прежнему
        # провал; предупреждение о «V4 нет» у другого стоит рядом.
        runner = FakeRunner(capture=[cand(701), cand(703)], after=[cand(703)],
                            no_v4=[], no_v4_after=[701],
                            codes={tool.STEP_SOURCES: 18})
        code, result = self.cycle_result(runner)
        self.assertEqual(code, tool.EXIT_CANDIDATE_EVIDENCE_MISSING)
        self.assertEqual(result['outcome'], tool.OUTCOME_FAILED)
        self.assertIn(tool.WARNING_CANDIDATE_NO_V4, result['warnings'])
        self.assertEqual(result['evidence_misses']['candidates'], [703])

    def test_an_unavailable_recheck_after_a_complete_capture_is_a_warning(
            self):
        runner = FakeRunner(capture=[cand(701)],
                            codes={tool.STEP_VERIFY: 23})
        code, result = self.cycle_result(runner)
        self.assertEqual(code, tool.EXIT_OK)
        self.assertEqual(result['outcome'], tool.OUTCOME_WARNINGS)
        self.assertEqual(result['warnings'],
                         [tool.WARNING_NO_V4_CHECK_UNAVAILABLE])
        self.assertIsNone(result['failure'])
        self.assertIn(tool.STEP_RECALC, runner.steps())
        # Отрицательный контроль: после НЕПОЛНОГО сбора недоступный VERIFY
        # остаётся провалом, как и был.
        runner = FakeRunner(capture=[cand(701)], after=[],
                            codes={tool.STEP_SOURCES: 18,
                                   tool.STEP_VERIFY: 23})
        code, result = self.cycle_result(runner)
        self.assertEqual(code, tool.EXIT_CANDIDATE_EVIDENCE_MISSING)

    def test_without_sources_the_manifest_list_still_warns(self):
        # --no-dji: сбора нет, список шага 2 -- последний известный.
        runner = FakeRunner(no_v4=[705])
        code, result = self.cycle_result(runner, '--no-dji')
        self.assertEqual((code, result['outcome']),
                         (tool.EXIT_OK, tool.OUTCOME_WARNINGS))
        self.assertEqual(result['candidates_no_v4_at_source'], [705])
        # Пустой захват: SOURCES пропущен, предупреждение то же.
        runner = FakeRunner(ids=(), no_v4=[705])
        code, result = self.cycle_result(runner)
        self.assertEqual((code, result['outcome']),
                         (tool.EXIT_OK, tool.OUTCOME_WARNINGS))
        self.assertNotIn(tool.STEP_SOURCES, runner.steps())

    def test_the_ledger_line_and_last_cycle_name_them(self):
        runner = FakeRunner(capture=[cand(701)], after=[], no_v4=[705],
                            no_v4_after=[705, 701])
        code, result = self.cycle_result(runner)
        self.assertEqual(result['candidates_no_v4_at_source'], [705, 701])
        message = tool.summary_message(code, result)
        self.assertIn('candidates_no_v4_at_source=2', message)
        self.assertIn('warnings CANDIDATE_NO_V4_AT_SOURCE', message)
        with io.open(os.path.join(self.work, 'last_cycle.json'),
                     encoding='ascii') as fh:
            written = json.load(fh)
        self.assertEqual(written['candidates_no_v4_at_source'], [705, 701])
        self.assertEqual(written['outcome'], 'SUCCESS_WITH_WARNINGS')


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

    def test_skip_recalc_runs_the_collector_steps_and_needs_no_db(self):
        # Прежнее имя:
        # test_skip_recalc_runs_the_three_collector_steps_and_needs_no_db.
        # К трём шагам сборщика добавился VERIFY после полного сбора с
        # кандидатами -- он тоже только читает манифест.
        runner = FakeRunner()
        self.assertEqual(self.run_without_db(runner, '--skip-recalc'),
                         tool.EXIT_OK)
        self.assertEqual(runner.steps(), [tool.STEP_FLIGHTS,
                                          tool.STEP_MANIFEST,
                                          tool.STEP_SOURCES,
                                          tool.STEP_VERIFY])

    def test_skip_recalc_still_verifies_an_incomplete_capture(self):
        runner = FakeRunner(capture=[cand(701), ctrl(702)], after=[ctrl(702)],
                            codes={tool.STEP_SOURCES: 18})
        self.assertEqual(self.run_without_db(runner, '--skip-recalc'),
                         tool.EXIT_OK)
        self.assertEqual(runner.steps()[-1], tool.STEP_VERIFY)
        runner = FakeRunner(capture=[cand(701)], after=[cand(701)],
                            codes={tool.STEP_SOURCES: 18})
        self.assertEqual(self.run_without_db(runner, '--skip-recalc'),
                         tool.EXIT_CANDIDATE_EVIDENCE_MISSING)

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
             tool.EXIT_SOURCES_INCOMPLETE, tool.EXIT_NOT_IDEMPOTENT,
             tool.EXIT_BUSY),
            (0, 1, 2, 3, 4, 5, 6, 7))
        # Новое имя пятого кода -- то же число, а не новый код.
        self.assertEqual(tool.EXIT_CANDIDATE_EVIDENCE_MISSING,
                         tool.EXIT_SOURCES_INCOMPLETE)

    def test_the_collector_codes_are_the_collectors_own(self):
        # [REASON]: цикл не импортирует сборщик -- это отдельный процесс со
        # своим venv, -- поэтому его коды записаны здесь числами. Разойдись
        # они с настоящими, остановка «манифест слишком велик» молча
        # превратилась бы в обычный сбой шага, а снисхождение к неполному
        # сбору -- в остановку цикла. Сверяет их только тест.
        from drone_collector import area_manifest, main as collector_main
        self.assertEqual(tool.COLLECTOR_MANIFEST_TOO_LARGE,
                         area_manifest.EXIT_MANIFEST_TOO_LARGE)
        self.assertEqual(tool.COLLECTOR_SOURCES_INCOMPLETE,
                         collector_main.EXIT_SOURCES_INCOMPLETE)
        self.assertEqual(tool.COLLECTOR_BUSY,
                         collector_main.EXIT_COLLECTOR_BUSY)
        self.assertEqual(tool.DEFAULT_STOP_ABOVE, 50)

    def test_the_words_are_the_ledgers_and_the_manifests_own(self):
        # Экран журнала сравнивает эти строки со своими; манифест пишет
        # причину, по которой VERIFY отличает контроль от кандидата.
        from dji_area import capture_manifest
        from dji_area import control_store as cs
        self.assertEqual(tool.MANIFEST_REASON_CONTROL,
                         capture_manifest.REASON_CONTROL)
        self.assertEqual(tool.FAILURE_CANDIDATE_EVIDENCE,
                         cs.FAILURE_CANDIDATE_EVIDENCE)
        self.assertEqual(tool.WARNING_CONTROL_EVIDENCE,
                         cs.WARNING_CONTROL_EVIDENCE)
        self.assertEqual(tool.STEP_VERIFY, cs.STEP_VERIFY)
        for outcome in (tool.OUTCOME_SUCCESS, tool.OUTCOME_WARNINGS,
                        tool.OUTCOME_FAILED, tool.OUTCOME_BUSY):
            self.assertIn(tool.status_for(outcome), cs.STATUSES)
            self.assertEqual(tool.status_for(outcome), outcome)


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
        self.run_cycle(FakeRunner(capture=[cand(701), ctrl(702)],
                                  after=[ctrl(702)],
                                  codes={tool.STEP_SOURCES: 18}))
        text = '\n'.join(self.lines)
        self.assertTrue(text)
        self.assertTrue(all(ord(ch) < 128 for ch in text), text)


# ─── Блокировка цикла ───────────────────────────────────────────────────────

class CycleLock(Base):
    """Код 7: другой цикл держит блокировку -- ни одного шага."""

    def main(self, runner, *extra):
        return tool.main(['--db', self.db, '--work-dir', self.work]
                         + list(extra), runner=runner, today=TODAY,
                         out=self.lines.append)

    def hold(self, path, purpose='holder-in-test'):
        holder = runlock.RunLock(path, purpose=purpose)
        self.assertTrue(holder.acquire())
        self.addCleanup(holder.release)
        return holder

    def test_a_held_cycle_lock_is_code_7_and_no_step_runs(self):
        self.hold(runlock.cycle_lock_path(self.db, self.work))
        runner = FakeRunner()
        started = time.monotonic()
        self.assertEqual(self.main(runner), tool.EXIT_BUSY)
        self.assertLess(time.monotonic() - started, 5.0)
        self.assertEqual(runner.commands, [])
        text = '\n'.join(self.lines)
        self.assertIn('BUSY', text)
        self.assertIn('holder-in-test', text)
        self.assertIn('pid=%d' % os.getpid(), text)
        self.assertTrue(all(ord(ch) < 128 for ch in text), text)
        # Рабочий каталог чужого цикла не тронут.
        self.assertFalse(os.path.exists(os.path.join(self.work,
                                                     'last_cycle.json')))

    def test_the_lock_is_held_for_every_step_and_released_after(self):
        path = runlock.cycle_lock_path(self.db, self.work)
        held = []

        def probe(_command):
            held.append((runlock.is_held(path),
                         (runlock.owner(path) or {}).get('purpose')))

        runner = FakeRunner(hooks={step: probe for step in (
            tool.STEP_FLIGHTS, tool.STEP_MANIFEST, tool.STEP_SOURCES,
            tool.STEP_RECALC)})
        self.assertEqual(self.main(runner), tool.EXIT_OK)
        self.assertEqual(held, [(True, tool.CYCLE_LOCK_PURPOSE)] * 4)
        self.assertFalse(runlock.is_held(path))

    def test_without_a_database_the_lock_lives_in_the_work_directory(self):
        self.hold(runlock.cycle_lock_path(None, self.work))
        runner = FakeRunner()
        code = tool.main(['--work-dir', self.work, '--skip-recalc'],
                         runner=runner, today=TODAY, out=self.lines.append)
        self.assertEqual(code, tool.EXIT_BUSY)
        self.assertEqual(runner.commands, [])

    def test_a_waiting_cycle_runs_once_the_holder_leaves(self):
        holder = self.hold(runlock.cycle_lock_path(self.db, self.work))
        saved = tool.LOCK_POLL_S
        tool.LOCK_POLL_S = 0.05
        self.addCleanup(setattr, tool, 'LOCK_POLL_S', saved)
        timer = threading.Timer(0.3, holder.release)
        timer.start()
        self.addCleanup(timer.cancel)
        runner = FakeRunner()
        self.assertEqual(self.main(runner, '--lock-wait', '10'), tool.EXIT_OK)
        self.assertIn(tool.STEP_RECALC, runner.steps())

    def test_a_refused_command_creates_no_lock_and_no_directory(self):
        absent_dir = os.path.join(self.tmp, 'no', 'such')
        runner = FakeRunner()
        code = tool.main(['--db', os.path.join(absent_dir, 'transport.db'),
                          '--work-dir', self.work], runner=runner,
                         today=TODAY, out=self.lines.append)
        self.assertEqual(code, tool.EXIT_NO_DATABASE)
        self.assertFalse(os.path.exists(absent_dir))
        self.assertEqual(runner.commands, [])

    def test_a_negative_wait_is_a_usage_error(self):
        self.assertEqual(self.main(FakeRunner(), '--lock-wait', '-1'),
                         tool.EXIT_USAGE)

    def test_a_fake_database_runs_without_a_ledger(self):
        # Не SQLite: журнал не пишется, цикл идёт, база не тронута.
        before = read_bytes(self.db)
        runner = FakeRunner()
        self.assertEqual(self.main(runner), tool.EXIT_OK)
        self.assertEqual(read_bytes(self.db), before)
        self.assertTrue(any('ledger    not recorded' in line
                            for line in self.lines))


# ─── Журнал прогонов на настоящей SQLite ────────────────────────────────────

class LedgerBase(unittest.TestCase):
    """Временная база, прошедшая миграцию DRONE_AREA_CONTROL_V2_001."""

    SECRET = 'Zq7-very-secret-token-value'

    def setUp(self):
        import migrate_drone_area_control_v2_001 as mig
        import migration_utils

        self.tmp = tempfile.mkdtemp(prefix='area_daily_ledger_')
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        self.db = os.path.join(self.tmp, 'transport.db')
        self.work = os.path.join(self.tmp, 'work')
        con = sqlite3.connect(self.db)
        con.execute('CREATE TABLE users (id INTEGER PRIMARY KEY, username '
                    'VARCHAR(80))')
        con.execute('CREATE TABLE dji_area_calculations (id INTEGER PRIMARY '
                    'KEY, flight_id BIGINT)')
        con.execute("INSERT INTO users VALUES (1, 'admin')")
        con.commit()
        con.close()
        saved = (mig.DB_PATH, migration_utils.DB_PATH)
        mig.DB_PATH = migration_utils.DB_PATH = self.db

        def restore():
            mig.DB_PATH, migration_utils.DB_PATH = saved

        self.addCleanup(restore)
        with contextlib.redirect_stdout(io.StringIO()):
            mig.run()
        self.lines = []
        from dji_area import control_store, store
        self.cs = control_store
        self.con = store.connect(self.db)
        self.addCleanup(self.con.close)

    def main(self, runner, *extra):
        return tool.main(['--db', self.db, '--work-dir', self.work]
                         + list(extra), runner=runner, today=TODAY,
                         out=self.lines.append)

    def rows(self):
        return self.cs.latest_runs(self.con, limit=50)[::-1]

    def enqueue(self, now=None):
        return self.cs.enqueue_manual(self.con, 1, 'Админ', now=now)

    def lock_path(self):
        return runlock.cycle_lock_path(self.db, self.work)


class QueuedRuns(LedgerBase):
    """`--run-queued` -- исполнитель кнопки «Обновить данные DJI»."""

    def test_a_queued_run_goes_running_then_success_with_its_result(self):
        queued = self.enqueue()
        seen = []

        def during(_command):
            row = self.cs.get_run(self.con, queued['id'])
            seen.append((row['status'], row['current_step'],
                         row['window_from'], row['window_to']))

        runner = FakeRunner(hooks={tool.STEP_FLIGHTS: during,
                                   tool.STEP_RECALC: during})
        self.assertEqual(self.main(runner, '--run-queued'), tool.EXIT_OK)
        self.assertEqual(seen, [
            ('RUNNING', 'FLIGHTS', '2026-09-16', '2026-09-18'),
            ('RUNNING', 'RECALC', '2026-09-16', '2026-09-18')])
        row = self.cs.get_run(self.con, queued['id'])
        self.assertEqual(row['status'], self.cs.STATUS_SUCCESS)
        self.assertEqual(row['exit_code'], 0)
        self.assertIsNone(row['active_slot'])
        self.assertIsNotNone(row['started_at'])
        self.assertIsNotNone(row['finished_at'])
        self.assertEqual(row['pid'], os.getpid())
        self.assertEqual(row['result']['outcome'], 'SUCCESS')
        # VERIFY -- повторный манифест после полного сбора с кандидатами.
        self.assertEqual(row['result']['steps'], {
            'FLIGHTS': 0, 'MANIFEST': 0, 'SOURCES': 0, 'VERIFY': 0,
            'RECALC': 0})
        self.assertEqual(row['result']['recalc']['calc_writes'], {'new': 12})
        self.assertTrue(all(ord(ch) < 128 for ch in row['message']))
        self.assertFalse(runlock.is_held(self.lock_path()))
        # Ровно одна строка: кнопка не породила второй прогон.
        self.assertEqual(len(self.rows()), 1)

    def test_a_control_only_miss_ends_with_warnings(self):
        queued = self.enqueue()
        runner = FakeRunner(capture=[cand(701), ctrl(702)], after=[ctrl(702)],
                            codes={tool.STEP_SOURCES: 18})
        self.assertEqual(self.main(runner, '--run-queued'), tool.EXIT_OK)
        row = self.cs.get_run(self.con, queued['id'])
        self.assertEqual(row['status'], self.cs.STATUS_WARNINGS)
        self.assertEqual(row['result']['warnings'],
                         [self.cs.WARNING_CONTROL_EVIDENCE])

    def test_a_candidate_miss_is_failed_and_names_the_step(self):
        queued = self.enqueue()
        runner = FakeRunner(capture=[cand(701)], after=[cand(701)],
                            codes={tool.STEP_SOURCES: 18})
        self.assertEqual(self.main(runner, '--run-queued'),
                         tool.EXIT_CANDIDATE_EVIDENCE_MISSING)
        row = self.cs.get_run(self.con, queued['id'])
        self.assertEqual(row['status'], self.cs.STATUS_FAILED)
        self.assertEqual(row['failed_step'], 'SOURCES')
        self.assertEqual(row['exit_code'], 5)
        self.assertEqual(row['result']['failure'],
                         self.cs.FAILURE_CANDIDATE_EVIDENCE)
        self.assertIn('candidates_missing=1', row['message'])

    def test_nothing_queued_changes_nothing_and_takes_no_lock(self):
        runner = FakeRunner()
        self.assertEqual(self.main(runner, '--run-queued'), tool.EXIT_OK)
        self.assertIn('nothing queued', self.lines)
        self.assertEqual(runner.commands, [])
        self.assertEqual(self.rows(), [])
        self.assertFalse(os.path.exists(self.lock_path()))

    def test_a_busy_lock_closes_the_queued_request_as_busy(self):
        queued = self.enqueue()
        holder = runlock.RunLock(self.lock_path(), purpose='scheduled-cycle')
        self.assertTrue(holder.acquire())
        self.addCleanup(holder.release)
        runner = FakeRunner()
        self.assertEqual(self.main(runner, '--run-queued', '--lock-wait', '0'),
                         tool.EXIT_BUSY)
        self.assertEqual(runner.commands, [])
        row = self.cs.get_run(self.con, queued['id'])
        self.assertEqual(row['status'], self.cs.STATUS_BUSY)
        self.assertEqual(row['exit_code'], 7)
        self.assertIn('scheduled-cycle', row['message'])
        # Слот освобождён: следующий клик ставится в очередь.
        self.assertIsNotNone(self.enqueue())

    def test_an_old_queued_request_that_waited_for_the_lock_is_still_run(self):
        # [REASON]: запрос ждал за плановым циклом дольше пятнадцати минут.
        # Если бы исполнитель сначала чистил журнал, а потом забирал,
        # reconcile назвал бы такой запрос «несостоявшимся запуском».
        hour_ago = datetime.now(timezone.utc).replace(tzinfo=None) \
            - timedelta(hours=1)
        queued = self.enqueue(now=hour_ago)
        self.assertEqual(self.main(FakeRunner(), '--run-queued'), tool.EXIT_OK)
        self.assertEqual(self.cs.get_run(self.con, queued['id'])['status'],
                         self.cs.STATUS_SUCCESS)

    def test_the_queued_run_waits_for_the_holder_then_runs(self):
        queued = self.enqueue()
        holder = runlock.RunLock(self.lock_path(), purpose='scheduled-cycle')
        self.assertTrue(holder.acquire())
        self.addCleanup(holder.release)
        saved = tool.LOCK_POLL_S
        tool.LOCK_POLL_S = 0.05
        self.addCleanup(setattr, tool, 'LOCK_POLL_S', saved)
        timer = threading.Timer(0.3, holder.release)
        timer.start()
        self.addCleanup(timer.cancel)
        self.assertEqual(self.main(FakeRunner(), '--run-queued'), tool.EXIT_OK)
        self.assertEqual(self.cs.get_run(self.con, queued['id'])['status'],
                         self.cs.STATUS_SUCCESS)

    def test_a_secret_in_an_unexpected_failure_never_reaches_the_ledger(self):
        saved = os.environ.get('DRONE_API_TOKEN')
        os.environ['DRONE_API_TOKEN'] = self.SECRET

        def restore():
            if saved is None:
                os.environ.pop('DRONE_API_TOKEN', None)
            else:
                os.environ['DRONE_API_TOKEN'] = saved

        self.addCleanup(restore)
        queued = self.enqueue()

        def explode(_command):
            # Текст без «token=» -- ловит только замена значений окружения.
            raise RuntimeError('the collector said %s and died' % self.SECRET)

        runner = FakeRunner(hooks={tool.STEP_SOURCES: explode})
        self.assertEqual(self.main(runner, '--run-queued'),
                         tool.EXIT_STEP_FAILED)
        row = self.cs.get_run(self.con, queued['id'])
        self.assertEqual(row['status'], self.cs.STATUS_FAILED)
        self.assertEqual(row['failed_step'], 'SOURCES')
        self.assertIsNone(row['active_slot'])
        self.assertNotIn(self.SECRET, row['message'])
        self.assertNotIn(self.SECRET, row['result_json'] or '')
        # Отрицательный контроль: секрет в тексте был и замаскирован.
        self.assertIn('***', row['message'])
        self.assertIn('RuntimeError', row['message'])
        console = '\n'.join(self.lines)
        self.assertNotIn(self.SECRET, console)
        self.assertTrue(all(ord(ch) < 128 for ch in console))
        self.assertFalse(runlock.is_held(self.lock_path()))

    def test_run_queued_takes_no_window_and_no_mode(self):
        for extra in (('--from', '2026-09-01', '--to', '2026-09-02'),
                      ('--recalc-only',), ('--no-dji',), ('--skip-recalc',)):
            runner = FakeRunner()
            self.assertEqual(self.main(runner, '--run-queued', *extra),
                             tool.EXIT_USAGE, extra)
            self.assertEqual(runner.commands, [])
        runner = FakeRunner()
        self.assertEqual(tool.main(['--work-dir', self.work, '--run-queued'],
                                   runner=runner, today=TODAY,
                                   out=self.lines.append), tool.EXIT_USAGE)


class ScheduledRuns(LedgerBase):

    def test_a_plain_run_is_recorded_as_scheduled(self):
        self.assertEqual(self.main(FakeRunner()), tool.EXIT_OK)
        (row,) = self.rows()
        self.assertEqual(row['trigger_kind'], self.cs.TRIGGER_SCHEDULED)
        self.assertEqual(row['status'], self.cs.STATUS_SUCCESS)
        self.assertEqual((row['window_from'], row['window_to']),
                         ('2026-09-16', '2026-09-18'))

    def test_a_second_run_on_the_same_inputs_is_idempotent_and_safe(self):
        runner = FakeRunner(writes=[{'new': 12}, {'unchanged': 12}])
        self.assertEqual(self.main(runner), tool.EXIT_OK)
        self.assertEqual(self.main(runner, '--expect-unchanged'),
                         tool.EXIT_OK)
        first, second = self.rows()
        self.assertEqual(first['result']['recalc']['calc_writes'],
                         {'new': 12})
        self.assertEqual(second['result']['recalc']['calc_writes'],
                         {'unchanged': 12})
        self.assertEqual([r['status'] for r in (first, second)],
                         [self.cs.STATUS_SUCCESS] * 2)
        self.assertEqual(self.cs.active_runs(self.con), [])
        self.assertFalse(runlock.is_held(self.lock_path()))

    def test_a_busy_scheduled_run_is_recorded_as_busy(self):
        holder = runlock.RunLock(self.lock_path(), purpose='manual-cycle')
        self.assertTrue(holder.acquire())
        self.addCleanup(holder.release)
        runner = FakeRunner()
        self.assertEqual(self.main(runner), tool.EXIT_BUSY)
        self.assertEqual(runner.commands, [])
        (row,) = self.rows()
        self.assertEqual(row['status'], self.cs.STATUS_BUSY)
        self.assertEqual(row['result']['outcome'], 'BUSY')

    def test_partial_modes_are_not_recorded(self):
        # «Последнее успешное обновление» -- это свежие данные DJI; пересчёт
        # без сбора ими не является.
        for extra in (('--recalc-only',), ('--no-dji',)):
            self.assertEqual(self.main(FakeRunner(), *extra), tool.EXIT_OK)
        self.assertEqual(self.rows(), [])

    def test_a_dead_running_row_is_closed_by_the_next_cycle(self):
        dead = self.cs.start_scheduled(self.con, TODAY, TODAY, pid=999999)
        self.assertEqual(self.main(FakeRunner()), tool.EXIT_OK)
        self.assertEqual(self.cs.get_run(self.con, dead['id'])['status'],
                         self.cs.STATUS_INTERRUPTED)
        self.assertEqual(self.rows()[-1]['status'], self.cs.STATUS_SUCCESS)

    def test_a_live_queue_is_left_to_its_executor(self):
        # Плановый прогон не закрывает чужую очередь: её исполнитель может
        # как раз ждать эту блокировку.
        hour_ago = datetime.now(timezone.utc).replace(tzinfo=None) \
            - timedelta(hours=1)
        queued = self.enqueue(now=hour_ago)
        self.assertEqual(self.main(FakeRunner()), tool.EXIT_OK)
        self.assertEqual(self.cs.get_run(self.con, queued['id'])['status'],
                         self.cs.STATUS_QUEUED)

    def test_a_ledger_that_cannot_be_written_never_stops_the_cycle(self):
        def locked(*_args, **_kwargs):
            raise sqlite3.OperationalError('database is locked')

        for name in ('start_scheduled', 'set_step'):
            saved = getattr(self.cs, name)
            setattr(self.cs, name, locked)
            try:
                runner = FakeRunner()
                self.assertEqual(self.main(runner), tool.EXIT_OK, name)
                # Пять: с VERIFY после полного сбора с кандидатами.
                self.assertEqual(len(runner.commands), 5, name)
            finally:
                setattr(self.cs, name, saved)
        self.assertTrue(any('ledger row was not opened' in line
                            for line in self.lines))
        self.assertTrue(any('run ledger was not updated' in line
                            for line in self.lines))
        # Строка, открытая до сбоя шага, всё равно закрыта итогом.
        (row,) = self.rows()
        self.assertEqual(row['status'], self.cs.STATUS_SUCCESS)

    def test_without_the_tables_the_cycle_runs_and_says_so(self):
        bare = os.path.join(self.tmp, 'bare.db')
        con = sqlite3.connect(bare)
        con.execute('CREATE TABLE users (id INTEGER PRIMARY KEY)')
        con.commit()
        con.close()
        runner = FakeRunner()
        code = tool.main(['--db', bare, '--work-dir', self.work],
                         runner=runner, today=TODAY, out=self.lines.append)
        self.assertEqual(code, tool.EXIT_OK)
        self.assertEqual(len(runner.commands), 5)
        self.assertEqual(sum('ledger    not recorded' in line
                             for line in self.lines), 1)


if __name__ == '__main__':
    unittest.main()
