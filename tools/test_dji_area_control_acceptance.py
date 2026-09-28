# -*- coding: utf-8 -*-
"""Самопроверка tools/dji_area_control_acceptance.py.

Приёмка, которая не умеет падать, ничего не принимает. Поэтому каждая
проверка здесь стоит парой: верный оракул -- PASS, тот же оракул с одним
испорченным числом -- FAIL с названием именно этого числа.

Данные строит НАСТОЯЩИЙ конвейер на синтетической базе: цепочка
база -> мостик -> цель, у цели V4 с неподвижным счётчиком. Отдельно лежит
сентябрьский оракул репозитория: его арифметика обязана сходиться сама с
собой, а якоря -- совпадать с числами постановки.

Переход модели (DJI-AREA-RETAINED-FOOTPRINT-001) держится так же парами: на
базе со строками ПРЕЖНЕГО кода проекция сухого прогона обязана совпасть с
отчётом, который даёт настоящее применение на копии, а каждая порча -- лишняя
или недостающая перезапись, фантом там, где его быть не должно, RAW, billable,
мостик в корректировках -- валит приёмку со своим названием. Исторический
сентябрьский оракул закреплён хешем содержимого: он не меняется.

Stdlib sqlite3, без Flask.

Запуск:  python tools\\test_dji_area_control_acceptance.py
"""

import copy
import hashlib
import io
import json
import os
import shutil
import sqlite3
import sys
import tempfile
import unittest
from contextlib import redirect_stdout
from unittest import mock

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if REPO_ROOT not in sys.path:
    sys.path.insert(0, REPO_ROOT)

import dji_area  # noqa: E402
from dji_area import accounting as acc  # noqa: E402
from dji_area import footprint as fpm  # noqa: E402
from dji_area import pipeline as pl  # noqa: E402
from dji_area import resolver as rs  # noqa: E402
from tests.test_dji_area_identity_001 import (  # noqa: E402
    BASE, BRIDGE, DAY, Fixture, LONE, MU, TARGET)
from tools import dji_area_control_acceptance as tool  # noqa: E402

ORACLE = os.path.join(REPO_ROOT, 'docs', 'DJI_AREA_SEPTEMBER_2026_ORACLE.json')
RETAINED_ORACLE = os.path.join(
    REPO_ROOT, 'docs', 'DJI_AREA_SEPTEMBER_2026_RETAINED_FOOTPRINT_ORACLE.json')
# SHA-256 содержимого исторического оракула с переводами строк LF -- как оно
# лежит в git с коммита 779a8b7 (DJI-AREA-PRODUCTIONIZATION-001).
HISTORICAL_ORACLE_SHA256 = ('c695c4a58c169c1425dfe5087b8902bf'
                            'ae41260d8682724f8aa34509ebfb54e8')


class Base(unittest.TestCase):

    def setUp(self):
        self.fx = Fixture()
        self.addCleanup(self.fx.close)
        # База доказывает, что канал борта информативен; мостик льёт; у цели
        # счётчик стоит на значении базы -- retained-запись.
        self.fx.add_v4(BASE, 0.0, 10000.0 / MU, spray=True)
        self.fx.add_v4(BRIDGE, 0.0, 500.0 / MU, spray=True)
        self.fx.add_v4(TARGET, 10000.0 / MU, 10000.0 / MU, spray=False)
        pl.recalculate(self.fx.db, DAY, DAY, apply=True)
        self.oracle_path = os.path.join(self.fx.tmp, 'oracle.json')
        self.expected = self.observed()
        self.write_oracle(self.expected)

    def observed(self):
        import sqlite3
        con = sqlite3.connect(self.fx.db)
        con.row_factory = sqlite3.Row
        try:
            return tool.observe(tool.load_rows(con, DAY, DAY))[0]
        finally:
            con.close()

    def write_oracle(self, expected):
        with io.open(self.oracle_path, 'w', encoding='utf-8') as fh:
            json.dump({'period': [DAY.isoformat(), DAY.isoformat()],
                       'expected': expected}, fh)

    def run_tool(self, *extra):
        out = io.StringIO()
        with redirect_stdout(out):
            code = tool.main(['--db', self.fx.db, '--oracle',
                              self.oracle_path] + list(extra))
        return code, out.getvalue()


class TheFixtureIsARealCase(Base):

    def test_the_target_is_a_proven_phantom_and_the_bridge_is_not(self):
        self.assertEqual(self.expected['structural_candidates'], 1)
        self.assertEqual(self.expected['excluded_records'], 1)
        self.assertAlmostEqual(self.expected['excluded_ha'], 1.0)
        self.assertEqual(self.expected['chains_shown'], 1)
        self.assertEqual(self.expected['bridges_excluded'], 0)
        # RAW: база 1.0 + мостик 0.05 + цель 1.0 + одиночный 0.9 га.
        self.assertAlmostEqual(self.expected['raw_ha'], 2.95)
        self.assertAlmostEqual(self.expected['after_ha'], 1.95)


class PassAndFail(Base):

    def test_the_right_oracle_passes(self):
        code, text = self.run_tool()
        self.assertEqual(code, tool.EXIT_PASS, text)
        self.assertIn('VERDICT: PASS', text)

    def test_one_wrong_total_fails_and_is_named(self):
        wrong = copy.deepcopy(self.expected)
        wrong['excluded_ha'] = wrong['excluded_ha'] + 0.5
        self.write_oracle(wrong)
        code, text = self.run_tool()
        self.assertEqual(code, tool.EXIT_FAIL)
        self.assertIn('MISMATCH: excluded_ha', text)
        self.assertIn('VERDICT: FAIL', text)

    def test_one_wrong_drone_figure_fails_even_when_the_total_is_right(self):
        wrong = copy.deepcopy(self.expected)
        name = sorted(wrong['by_drone'])[0]
        wrong['by_drone'][name]['raw_ha'] += 0.25
        self.write_oracle(wrong)
        code, text = self.run_tool()
        self.assertEqual(code, tool.EXIT_FAIL)
        self.assertIn('by_drone[', text)

    def test_a_missing_drone_is_a_mismatch(self):
        wrong = copy.deepcopy(self.expected)
        wrong['by_drone']['DRONE-THAT-IS-NOT-THERE'] = {
            'records': 1, 'raw_ha': 1.0, 'excluded_ha': 0.0, 'after_ha': 1.0,
            'pending_ha': 0.0, 'review_ha': 0.0}
        self.write_oracle(wrong)
        self.assertEqual(self.run_tool()[0], tool.EXIT_FAIL)

    def test_a_wrong_count_fails(self):
        wrong = copy.deepcopy(self.expected)
        wrong['review_application_with_flat_counter'] = 4
        self.write_oracle(wrong)
        code, text = self.run_tool()
        self.assertEqual(code, tool.EXIT_FAIL)
        self.assertIn('review_application_with_flat_counter', text)

    def test_a_tiny_rounding_difference_is_tolerated(self):
        # Отрицательный контроль к допуску: он узкий, а не «что угодно».
        near = copy.deepcopy(self.expected)
        near['raw_ha'] += 0.00004
        self.write_oracle(near)
        self.assertEqual(self.run_tool()[0], tool.EXIT_PASS)
        near['raw_ha'] += 0.001
        self.write_oracle(near)
        self.assertEqual(self.run_tool()[0], tool.EXIT_FAIL)


class ReadOnly(Base):

    def digest(self):
        with open(self.fx.db, 'rb') as fh:
            return hashlib.sha256(fh.read()).hexdigest()

    def test_the_database_is_byte_identical_after_the_run(self):
        before = self.digest()
        code, text = self.run_tool()
        self.assertEqual(code, tool.EXIT_PASS)
        self.assertEqual(self.digest(), before)
        self.assertIn('database sha256 unchanged: yes', text)

    def test_a_missing_database_is_code_2_and_no_file_appears(self):
        ghost = os.path.join(self.fx.tmp, 'nowhere', 'absent.db')
        out = io.StringIO()
        with redirect_stdout(out):
            code = tool.main(['--db', ghost, '--oracle', self.oracle_path])
        self.assertEqual(code, tool.EXIT_NO_DATABASE)
        self.assertFalse(os.path.exists(ghost))

    def test_console_output_is_ascii_only(self):
        _code, text = self.run_tool()
        self.assertTrue(all(ord(ch) < 128 for ch in text), text)

    def test_the_out_directory_gets_the_verdict_and_the_workbook(self):
        from openpyxl import load_workbook
        out_dir = os.path.join(self.fx.tmp, 'out')
        self.run_tool('--out', out_dir)
        with io.open(os.path.join(out_dir, 'area_control_acceptance.json'),
                     encoding='utf-8') as fh:
            self.assertEqual(json.load(fh)['verdict'], 'PASS')
        # Через открытый дескриптор: иначе openpyxl оставляет файл на сборщик
        # мусора, и прогон шумит ResourceWarning.
        with open(os.path.join(out_dir, 'area_control_report.xlsx'),
                  'rb') as fh:
            book = load_workbook(fh)
        # DRONE-AREA-CONTROL-V2-MEGA: книга выросла с четырёх листов до
        # семи; прежние четыре -- первыми и в прежнем порядке.
        self.assertEqual(book.sheetnames[:4], ['Сводка', 'По_дронам',
                                               'Корректировки',
                                               'Требует_проверки'])
        self.assertEqual(len(book.sheetnames), 7)


class TheDryRunRecalculation(Base):
    """Нынешний код не хочет переписать ни одной строки периода."""

    def summary(self, document):
        path = os.path.join(self.fx.tmp, 'dry.json')
        with io.open(path, 'w', encoding='utf-8') as fh:
            if isinstance(document, str):
                fh.write(document)
            else:
                json.dump(document, fh)
        return path

    def records(self):
        return self.expected['records']

    def test_a_real_dry_run_over_the_same_evidence_passes(self):
        # Не подставная сводка: её пишет сам пересчёт на той же базе.
        summary = pl.recalculate(self.fx.db, DAY, DAY, apply=False)
        summary.pop('flights', None)
        code, text = self.run_tool('--recalc-summary', self.summary(summary))
        self.assertEqual(code, tool.EXIT_PASS, text)
        self.assertIn('recalc dry-run calc_writes', text)

    def test_a_summary_that_wants_to_write_fails_and_says_what(self):
        n = self.records()
        code, text = self.run_tool('--recalc-summary', self.summary(
            {'flights_in_period': n,
             'calc_writes': {'unchanged': n - 1, 'new': 1}}))
        self.assertEqual(code, tool.EXIT_FAIL)
        self.assertIn('would write {"new": 1}', text)

    def test_another_period_is_not_accepted_as_this_one(self):
        n = self.records()
        code, text = self.run_tool('--recalc-summary', self.summary(
            {'flights_in_period': n + 5, 'calc_writes': {'unchanged': n + 5}}))
        self.assertEqual(code, tool.EXIT_FAIL)
        self.assertIn('flights_in_period', text)

    def test_an_empty_summary_proves_nothing(self):
        code, text = self.run_tool('--recalc-summary', self.summary(
            {'flights_in_period': self.records()}))
        self.assertEqual(code, tool.EXIT_FAIL)
        self.assertIn('calc_writes is missing', text)

    def test_unchanged_must_cover_every_record(self):
        n = self.records()
        code, _text = self.run_tool('--recalc-summary', self.summary(
            {'flights_in_period': n, 'calc_writes': {'unchanged': n - 1}}))
        self.assertEqual(code, tool.EXIT_FAIL)

    def test_an_unreadable_summary_is_a_usage_error_not_a_pass(self):
        code, _text = self.run_tool('--recalc-summary',
                                    self.summary('{not json'))
        self.assertEqual(code, tool.EXIT_USAGE)
        code, _text = self.run_tool('--recalc-summary', os.path.join(
            self.fx.tmp, 'absent.json'))
        self.assertEqual(code, tool.EXIT_USAGE)

    def test_without_the_flag_nothing_about_the_recalc_is_claimed(self):
        _code, text = self.run_tool()
        self.assertNotIn('recalc dry-run', text)


class TheContractWithTheOwnerBlocks(Base):

    def test_exit_codes_are_the_literal_numbers_the_runbook_reads(self):
        # [REASON]: блоки PowerShell сравнивают $LASTEXITCODE с ЧИСЛОМ. Пока
        # тесты сверяли код только с именем константы, FAIL, ставший нулём,
        # проходил бы их все -- и блок принял бы провал за приёмку.
        self.assertEqual((tool.EXIT_PASS, tool.EXIT_USAGE,
                          tool.EXIT_NO_DATABASE, tool.EXIT_FAIL), (0, 1, 2, 3))

    def test_a_database_that_changed_under_the_reader_fails(self):
        digests = iter(['a' * 64, 'b' * 64])
        original = tool.file_sha256
        tool.file_sha256 = lambda _path: next(digests)
        self.addCleanup(setattr, tool, 'file_sha256', original)
        code, text = self.run_tool()
        self.assertEqual(code, 3)
        self.assertIn('read-only: the database changed', text)
        self.assertIn('database sha256 unchanged: NO', text)


class TheSeptemberOracle(unittest.TestCase):
    """Оракул репозитория: якоря постановки и внутренняя арифметика."""

    def setUp(self):
        with io.open(ORACLE, encoding='utf-8') as fh:
            self.oracle = json.load(fh)
        self.expected = self.oracle['expected']

    def test_the_anchors_are_the_ones_the_owner_named(self):
        self.assertEqual(self.oracle['period'], ['2026-09-01', '2026-09-18'])
        self.assertEqual(self.expected['records'], 4623)
        self.assertEqual(self.expected['raw_missing_records'], 0)
        self.assertEqual(self.expected['structural_candidates'], 233)
        self.assertAlmostEqual(self.expected['raw_ha'], 4413.2825, places=4)
        self.assertAlmostEqual(self.expected['excluded_ha'], 167.8088,
                               places=4)
        self.assertAlmostEqual(self.expected['after_ha'], 4245.4737, places=4)

    def test_the_four_flat_counter_cases_stay_in_review(self):
        self.assertEqual(
            self.expected['review_application_with_flat_counter'], 4)
        self.assertEqual(self.expected['proven_structural'], 229)
        self.assertEqual(self.expected['proven_by_control_only'], 1)
        self.assertEqual(self.expected['pending_records'], 0)
        self.assertFalse(self.expected['complete'])

    def test_the_oracle_adds_up_by_itself(self):
        self.assertEqual(tool.formula_problems(self.expected), [])

    def test_no_flight_id_is_hard_coded_in_the_product_code(self):
        # Сентябрьские идентификаторы живут в документах, не в коде.
        for rel in ('dji_area/control_report.py', 'dji_area/accounting.py',
                    'dji_area/capture_manifest.py',
                    'tools/dji_area_control_acceptance.py'):
            with io.open(os.path.join(REPO_ROOT, rel),
                         encoding='utf-8') as fh:
                text = fh.read()
            for flight_id in ('701661028', '702797709', '703830892',
                              '703847599'):
                self.assertNotIn(flight_id, text, rel)


def lf_sha256(path):
    with open(path, 'rb') as fh:
        return hashlib.sha256(fh.read().replace(b'\r\n', b'\n')).hexdigest()


class TheHistoricalOracleIsKept(unittest.TestCase):
    """Сентябрьский оракул -- запись алгоритма ДО правила; не переписывается."""

    def test_the_file_is_byte_identical(self):
        self.assertEqual(lf_sha256(ORACLE), HISTORICAL_ORACLE_SHA256)

    def test_it_describes_a_steady_state_not_a_transition(self):
        with io.open(ORACLE, encoding='utf-8') as fh:
            oracle = json.load(fh)
        self.assertNotIn('transition', oracle)
        self.assertEqual(oracle['oracle'], 'september-2026')

    def test_the_new_oracle_names_it_as_its_history(self):
        with io.open(RETAINED_ORACLE, encoding='utf-8') as fh:
            oracle = json.load(fh)
        self.assertEqual(oracle['historical_oracle'],
                         'docs/DJI_AREA_SEPTEMBER_2026_ORACLE.json')
        self.assertNotEqual(lf_sha256(RETAINED_ORACLE),
                            HISTORICAL_ORACLE_SHA256)


class TheRetainedFootprintOracle(unittest.TestCase):
    """Новый оракул: числа проекции production 28.09.2026 и сам переход."""

    def setUp(self):
        with io.open(RETAINED_ORACLE, encoding='utf-8') as fh:
            self.oracle = json.load(fh)
        self.expected = self.oracle['expected']
        self.transition = self.oracle['transition']

    def test_the_new_numbers(self):
        e = self.expected
        self.assertEqual(self.oracle['period'], ['2026-09-01', '2026-09-18'])
        self.assertEqual((e['records'], e['raw_missing_records']), (4887, 0))
        self.assertAlmostEqual(e['raw_ha'], 4687.1675, places=4)
        self.assertAlmostEqual(e['excluded_ha'], 175.8333, places=4)
        self.assertEqual(e['excluded_records'], 241)
        self.assertAlmostEqual(e['after_ha'], 4511.3342, places=4)
        self.assertEqual((e['pending_ha'], e['pending_records']), (0.0, 0))
        self.assertAlmostEqual(e['review_ha'], 4.7646, places=4)
        self.assertEqual(e['review_records'], 7)
        self.assertEqual((e['structural_candidates'], e['chains_shown']),
                         (240, 240))
        self.assertEqual((e['proven_structural'],
                          e['proven_by_control_only']), (235, 1))
        self.assertEqual(e['review_application_with_flat_counter'], 1)
        self.assertEqual(e['bridges_excluded'], 0)
        self.assertEqual(len(e['by_drone']), 11)
        self.assertEqual(sum(d['records'] for d in e['by_drone'].values()),
                         4887)

    def test_it_adds_up_by_itself(self):
        # Разбиение: RAW = исключено + принято, дроны складываются в итог,
        # мостиков в корректировках нет.
        self.assertEqual(tool.formula_problems(self.expected), [])

    def test_the_transition_is_five_rewrites_and_one_that_stays(self):
        t = self.transition
        self.assertEqual(t['expected_rewrites'], [701661028, 702797709,
                                                  703830892, 703847599,
                                                  714181794])
        self.assertAlmostEqual(t['expected_rewrites_raw_ha'], 3.4106,
                               places=4)
        self.assertEqual(t['must_stay_review'], [714181711])
        self.assertEqual(t['negative_controls'], [668794098, 669810485,
                                                  685273720, 706410605])
        self.assertEqual(t['rule_flag'],
                         rs.F_RETAINED_NEGLIGIBLE_FOOTPRINT)

    def test_the_rule_is_the_one_the_code_runs(self):
        rule = self.oracle['rule']
        self.assertEqual(rule['rule_version'],
                         dji_area.RETAINED_FOOTPRINT_RULE_VERSION)
        self.assertEqual(rule['footprint_to_raw_max'], 0.027)
        self.assertEqual(rule['width_envelope_m'], 12.0)
        self.assertEqual(rule['area_algorithm'],
                         dji_area.AREA_ALGORITHM_VERSION)
        self.assertEqual(tool.rule_problems(self.oracle), [])

    def test_the_production_evaluation_it_follows(self):
        ev = self.oracle['production_evaluation']
        self.assertEqual(ev['b_total'], 44)
        self.assertEqual((ev['b_passes_footprint'],
                          ev['b_passes_footprint_structural_match']), (31, 28))
        self.assertEqual(ev['below_cut_structural_mismatch'],
                         [646731005, 696203208, 714181711])
        self.assertEqual(len(ev['final_candidates']), 28)
        self.assertAlmostEqual(ev['final_candidates_raw_ha'], 19.274,
                               places=4)
        self.assertEqual(len(ev['remaining_review_b']), 16)
        candidates = set(ev['final_candidates'])
        review = set(ev['remaining_review_b'])
        self.assertFalse(candidates & review)
        self.assertEqual(len(candidates | review), 44)
        self.assertTrue(set(self.transition['expected_rewrites'])
                        <= candidates)
        self.assertTrue(set(self.transition['must_stay_review']) <= review)
        self.assertEqual((ev['review_total_before'],
                          ev['review_total_after']), (88, 60))
        self.assertEqual((ev['raw_changed'], ev['billable_non_null'],
                          ev['exit_code']), (0, 0, 0))

    def test_the_provenance_names_its_sources(self):
        text = self.oracle['provenance']
        for needle in ('PRODUCTION', '2026-09-28', 'read-only',
                       '--evaluate-rule', 'september-2026',
                       'DJI_AREA_SEPTEMBER_2026_ORACLE.json',
                       'does not replace it'):
            self.assertIn(needle, text)
        self.assertEqual(self.oracle['evaluated_on'], '2026-09-28')

    def test_it_is_refused_without_a_phase(self):
        fx = Fixture()
        self.addCleanup(fx.close)
        out = io.StringIO()
        with redirect_stdout(out):
            code = tool.main(['--db', fx.db, '--oracle', RETAINED_ORACLE])
        self.assertEqual(code, tool.EXIT_USAGE)
        self.assertIn('describes a transition', out.getvalue())


def rule_off():
    """Конвейер без правила -- строки, какими их записал прежний код."""
    return mock.patch.object(rs, 'retained_negligible_footprint',
                             return_value=False)


def dump(path, document):
    with io.open(path, 'w', encoding='utf-8') as fh:
        json.dump(document, fh, default=str)
    return path


class Transition(unittest.TestCase):
    """База площадки ДО правила: база цепочки, мостик, перенесённый скаляр с
    распылением на месте (правило обязано сработать) и одиночная запись с тем
    же нулевым следом, но без цепочки (обязана остаться на проверке -- как
    714181711). Оракул перехода берёт числа у НАСТОЯЩЕГО применения на копии
    базы, поэтому PASS проекции значит «проекция = то, что будет записано»."""

    def setUp(self):
        self.fx = Fixture()
        self.addCleanup(self.fx.close)
        self.fx.add_v4(BASE, 0.0, 10000.0 / MU, spray=True)
        self.fx.add_v4(BRIDGE, 0.0, 500.0 / MU, spray=True)
        self.fx.add_v4(TARGET, 10000.0 / MU, 10000.0 / MU, spray=True)
        self.fx.add_v4(LONE, 13.5, 13.5, spray=True)
        with rule_off():
            pl.recalculate(self.fx.db, DAY, DAY, apply=True)
        self.work = tempfile.mkdtemp(prefix='transition_')
        self.addCleanup(shutil.rmtree, self.work, True)
        self.expected = self.after_the_apply_on_a_copy()
        self.oracle_path = os.path.join(self.work, 'oracle.json')
        self.oracle = {
            'oracle': 'fixture-transition',
            'period': [DAY.isoformat(), DAY.isoformat()],
            'rule': {'area_algorithm': dji_area.AREA_ALGORITHM_VERSION,
                     'rule_version': dji_area.RETAINED_FOOTPRINT_RULE_VERSION,
                     'footprint_to_raw_max': fpm.FOOTPRINT_TO_RAW_MAX,
                     'width_envelope_m': fpm.FOOTPRINT_WIDTH_ENVELOPE_M},
            'expected': self.expected,
            'transition': {
                'rule_flag': rs.F_RETAINED_NEGLIGIBLE_FOOTPRINT,
                'expected_rewrites': [TARGET],
                'expected_rewrites_raw_ha': 1.0,
                'must_stay_review': [LONE],
                # 999 -- контрольная вне периода: заметка, не провал.
                'negative_controls': [BASE, 999]}}
        dump(self.oracle_path, self.oracle)

    def after_the_apply_on_a_copy(self):
        copy_root = os.path.join(self.work, 'copy')
        shutil.copytree(self.fx.tmp, copy_root)
        copy_db = os.path.join(copy_root, 'instance', 'test.db')
        pl.recalculate(copy_db, DAY, DAY, apply=True)
        con = sqlite3.connect(copy_db)
        con.row_factory = sqlite3.Row
        try:
            return tool.observe(tool.load_rows(con, DAY, DAY))[0]
        finally:
            con.close()

    def dry_run(self):
        summary = pl.recalculate(self.fx.db, DAY, DAY, apply=False,
                                 collect_rows=True)
        rows = summary.pop('flights')
        return summary, rows

    def run_tool(self, *args):
        out = io.StringIO()
        with redirect_stdout(out):
            code = tool.main(['--db', self.fx.db, '--oracle',
                              self.oracle_path] + list(args))
        return code, out.getvalue()

    def pre_apply(self, summary=None, rows=None):
        real_summary, real_rows = self.dry_run()
        summary = real_summary if summary is None else summary
        rows = real_rows if rows is None else rows
        return self.run_tool(
            '--phase', 'pre-apply',
            '--recalc-summary', dump(os.path.join(self.work, 's.json'),
                                     summary),
            '--recalc-rows', dump(os.path.join(self.work, 'r.json'), rows))

    def tampered(self, flight_id, **changes):
        summary, rows = self.dry_run()
        for row in rows:
            if row['flight_id'] == flight_id:
                row.update(changes)
        return summary, rows

    def assert_fails_with(self, result, needle):
        code, text = result
        self.assertEqual(code, tool.EXIT_FAIL, text)
        self.assertIn(needle, text)
        self.assertIn('VERDICT: FAIL', text)

    def digest(self):
        with open(self.fx.db, 'rb') as fh:
            return hashlib.sha256(fh.read()).hexdigest()


class PreApply(Transition):

    def test_exactly_the_expected_transition_passes(self):
        before = self.digest()
        code, text = self.pre_apply()
        self.assertEqual(code, tool.EXIT_PASS, text)
        self.assertIn('rewrites of the dry run                1  [%d]'
                      % TARGET, text)
        self.assertIn('must stay REVIEW %d' % LONE, text)
        self.assertIn('negative control 999: outside the period', text)
        # Ничего не записано.
        self.assertEqual(self.digest(), before)

    def test_the_projection_is_the_report_the_apply_will_write(self):
        out_dir = os.path.join(self.work, 'out')
        summary, rows = self.dry_run()
        code, _text = self.run_tool(
            '--phase', 'pre-apply', '--out', out_dir,
            '--recalc-summary', dump(os.path.join(self.work, 's.json'),
                                     summary),
            '--recalc-rows', dump(os.path.join(self.work, 'r.json'), rows))
        self.assertEqual(code, tool.EXIT_PASS)
        with io.open(os.path.join(out_dir, 'area_control_acceptance.json'),
                     encoding='utf-8') as fh:
            verdict = json.load(fh)
        self.assertEqual(verdict['phase'], 'pre-apply')
        self.assertEqual(verdict['observed'], self.expected)
        self.assertEqual(verdict['transition']['rewrites'], [TARGET])
        self.assertEqual(self.expected['bridges_excluded'], 0)
        self.assertTrue(self.expected['partition_holds'])
        self.assertTrue(os.path.exists(os.path.join(
            out_dir, 'area_control_report_projected.xlsx')))

    def test_one_extra_would_write_fails(self):
        summary, rows = self.tampered(BRIDGE, calculation_input_hash='x')
        summary['calc_writes'] = {'unchanged': 2, 'would_write': 2}
        self.assert_fails_with(self.pre_apply(summary, rows),
                               'unexpected would_write: %d' % BRIDGE)

    def test_a_summary_that_disagrees_with_its_rows_fails(self):
        summary, rows = self.dry_run()
        summary['calc_writes'] = {'unchanged': 2, 'would_write': 2}
        self.assert_fails_with(self.pre_apply(summary, rows),
                               'would_write is 2, the transition names '
                               'exactly 1')

    def test_one_missing_expected_candidate_fails(self):
        con = sqlite3.connect(self.fx.db)
        stored = con.execute(
            'SELECT calculation_input_hash FROM dji_area_calculations WHERE '
            'flight_id=? AND superseded_at IS NULL', (TARGET,)).fetchone()[0]
        con.close()
        summary, rows = self.tampered(TARGET, calculation_input_hash=stored)
        self.assert_fails_with(self.pre_apply(summary, rows),
                               "expected rewrite(s) would not be written: %d"
                               % TARGET)

    def test_the_record_that_must_stay_in_review_turning_phantom_fails(self):
        summary, rows = self.tampered(
            LONE, calculation_input_hash='y',
            anomaly_flags=[rs.F_RETAINED_NEGLIGIBLE_FOOTPRINT],
            aggregation_eligibility=rs.AGG_CERTIFIED)
        self.assert_fails_with(self.pre_apply(summary, rows),
                               'transition: %d must stay REVIEW, it is '
                               'PHANTOM_PROVEN' % LONE)

    def test_a_negative_control_turning_phantom_fails(self):
        summary, rows = self.tampered(
            BASE, calculation_input_hash='z',
            area_status=rs.COUNTER_FLAT_RAW_OVERSTATED,
            corrected_recorded_area_m2=0.0,
            aggregation_eligibility=rs.AGG_CERTIFIED)
        self.assert_fails_with(self.pre_apply(summary, rows),
                               'negative control %d becomes PHANTOM_PROVEN'
                               % BASE)

    def test_a_raw_change_fails(self):
        summary, rows = self.tampered(TARGET, raw_area_m2=10001.0)
        self.assert_fails_with(self.pre_apply(summary, rows),
                               'would change RAW of 1 row(s): %d' % TARGET)

    def test_a_billable_value_fails(self):
        summary, rows = self.tampered(TARGET, billable_area_m2=10000.0)
        self.assert_fails_with(self.pre_apply(summary, rows),
                               'billable: 1 row(s) carry billable_area_m2')
        con = sqlite3.connect(self.fx.db)
        con.execute('UPDATE dji_area_calculations SET billable_area_m2=1.0 '
                    'WHERE flight_id=? AND superseded_at IS NULL', (BASE,))
        con.commit()
        con.close()
        self.assert_fails_with(self.pre_apply(),
                               'billable: 1 stored row(s) carry')

    def test_a_bridge_among_the_corrections_fails(self):
        summary, rows = self.tampered(
            BRIDGE, calculation_input_hash='b',
            area_status=rs.COUNTER_FLAT_RAW_OVERSTATED,
            corrected_recorded_area_m2=0.0,
            aggregation_eligibility=rs.AGG_CERTIFIED)
        self.assert_fails_with(self.pre_apply(summary, rows),
                               'bridge: 1 bridge record(s) appear among the '
                               'corrections')

    def test_a_kept_fingerprint_with_another_decision_fails(self):
        summary, rows = self.tampered(BRIDGE, area_status=rs.UNKNOWN_SUSPECT)
        self.assert_fails_with(self.pre_apply(summary, rows),
                               'flight %d keeps its fingerprint but its '
                               'decision would change: area_status' % BRIDGE)

    def test_a_wrong_projected_total_fails(self):
        self.oracle['expected']['excluded_ha'] += 0.5
        dump(self.oracle_path, self.oracle)
        self.assert_fails_with(self.pre_apply(), 'MISMATCH: excluded_ha')

    def test_rows_of_another_run_fail(self):
        summary, rows = self.dry_run()
        self.assert_fails_with(self.pre_apply(summary, rows[:-1]),
                               'not one run')

    def test_an_oracle_of_another_rule_fails(self):
        self.oracle['rule']['footprint_to_raw_max'] = 0.03
        dump(self.oracle_path, self.oracle)
        self.assert_fails_with(self.pre_apply(),
                               'rule: the oracle names footprint_to_raw_max')

    def test_the_phase_is_chosen_explicitly(self):
        summary, rows = self.dry_run()
        s = dump(os.path.join(self.work, 's.json'), summary)
        r = dump(os.path.join(self.work, 'r.json'), rows)
        for args in ((), ('--recalc-summary', s),
                     ('--phase', 'pre-apply', '--recalc-summary', s),
                     ('--phase', 'post-apply', '--recalc-rows', r,
                      '--recalc-summary', s),
                     ('--phase', 'pre-apply', '--recalc-summary', s,
                      '--recalc-rows', r, '--apply-summary', s)):
            self.assertEqual(self.run_tool(*args)[0], tool.EXIT_USAGE, args)
        # И наоборот: фаза с оракулом без перехода -- тоже ошибка.
        plain = dump(os.path.join(self.work, 'plain.json'),
                     {'period': self.oracle['period'],
                      'expected': self.expected})
        out = io.StringIO()
        with redirect_stdout(out):
            code = tool.main(['--db', self.fx.db, '--oracle', plain,
                              '--phase', 'post-apply', '--recalc-summary', s])
        self.assertEqual(code, tool.EXIT_USAGE)

    def test_unreadable_rows_are_a_usage_error(self):
        summary, _rows = self.dry_run()
        bad = os.path.join(self.work, 'bad.json')
        with io.open(bad, 'w', encoding='utf-8') as fh:
            fh.write('{"not": "a list"}')
        code, _text = self.run_tool(
            '--phase', 'pre-apply', '--recalc-summary',
            dump(os.path.join(self.work, 's.json'), summary),
            '--recalc-rows', bad)
        self.assertEqual(code, tool.EXIT_USAGE)


class PostApply(Transition):

    def apply(self):
        summary = pl.recalculate(self.fx.db, DAY, DAY, apply=True,
                                 flight_ids=[TARGET])
        return dump(os.path.join(self.work, 'apply.json'), summary)

    def post_apply(self, second=None, apply_path=None):
        if second is None:
            second, _rows = self.dry_run()
        args = ['--phase', 'post-apply', '--recalc-summary',
                dump(os.path.join(self.work, 'second.json'), second)]
        if apply_path:
            args += ['--apply-summary', apply_path]
        return self.run_tool(*args)

    def test_the_steady_state_after_the_apply_passes(self):
        applied = self.apply()
        code, text = self.post_apply(apply_path=applied)
        self.assertEqual(code, tool.EXIT_PASS, text)
        self.assertIn('recalc dry-run calc_writes', text)
        self.assertIn('{"unchanged": 4}', text)
        self.assertIn('apply calc_writes', text)

    def test_any_write_in_the_second_dry_run_fails(self):
        self.apply()
        for kind in ('would_write', 'new', 'reactivated'):
            self.assert_fails_with(
                self.post_apply({'flights_in_period': 4,
                                 'calc_writes': {'unchanged': 3, kind: 1}}),
                'would write {"%s": 1}' % kind)

    def test_before_the_apply_the_steady_state_is_refused(self):
        # Строки ещё прежние: правило в базе не стоит, отчёт не тот.
        with rule_off():
            second, _rows = self.dry_run()
        self.assert_fails_with(self.post_apply(second),
                               'transition: %d is not proven by the rule'
                               % TARGET)

    def test_an_apply_that_wrote_something_else_fails(self):
        self.apply()
        for writes, needle in (
                ({'new': 2, 'unchanged': 2}, '2 new row(s)'),
                ({'new': 1, 'reactivated': 1}, 'unexpected writes '
                                               '{"reactivated": 1}'),
                ({'unchanged': 1}, '0 new row(s)')):
            path = dump(os.path.join(self.work, 'odd.json'),
                        {'flights_in_period': sum(writes.values()),
                         'calc_writes': writes})
            code, text = self.post_apply(apply_path=path)
            self.assertEqual(code, tool.EXIT_FAIL, (writes, text))
            self.assertIn('MISMATCH: apply: ' + needle, text)
        # Счёт, который не сходится с числом записей периода, -- тоже провал.
        path = dump(os.path.join(self.work, 'odd.json'),
                    {'flights_in_period': 4, 'calc_writes': {'new': 1}})
        code, text = self.post_apply(apply_path=path)
        self.assertEqual(code, tool.EXIT_FAIL, text)
        self.assertIn('do not add up to flights_in_period 4', text)

    def test_billable_in_the_database_fails(self):
        self.apply()
        con = sqlite3.connect(self.fx.db)
        con.execute('UPDATE dji_area_calculations SET billable_area_m2=1.0 '
                    'WHERE flight_id=? AND superseded_at IS NULL', (TARGET,))
        con.commit()
        con.close()
        self.assert_fails_with(self.post_apply(),
                               'billable: 1 row(s) carry billable_area_m2')

    def test_the_rewritten_record_carries_the_rule_after_the_apply(self):
        self.apply()
        con = sqlite3.connect(self.fx.db)
        con.row_factory = sqlite3.Row
        try:
            rows = {r['flight_id']: r for r in tool.load_rows(con, DAY, DAY)}
        finally:
            con.close()
        target = acc.classify(rows[TARGET])
        self.assertEqual((target['accounting_class'], target['reason']),
                         (acc.PHANTOM_PROVEN,
                          acc.R_RETAINED_NEGLIGIBLE_FOOTPRINT))
        self.assertEqual(acc.classify(rows[LONE])['reason'],
                         acc.R_APPLICATION_WITH_FLAT_COUNTER)


if __name__ == '__main__':
    unittest.main()
