# -*- coding: utf-8 -*-
"""Самотест tools/dji_area_footprint_calibration.py (DJI-AREA-FOOTPRINT-CALIBRATION-001).

Держит три вещи: арифметику следа (путь x ширина только на кадрах
применения), происхождение ширины и то, что инструмент ничего не пишет в базу
и не создаёт её. Синтетика: V4 собирается строителями матрицы приёмки, база --
фикстурой проверки идентичности. Stdlib, без Flask и без сети.
"""

import csv
import hashlib
import io
import json
import math
import os
import shutil
import sqlite3
import sys
import tempfile
import unittest
from contextlib import redirect_stdout
from unittest import mock

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from dji_area import pipeline as pl  # noqa: E402
from dji_area import resolver as rs  # noqa: E402
from dji_area import store  # noqa: E402
from tests.test_dji_area_core import MS, frame, v4_bytes  # noqa: E402
from tests.test_dji_area_identity_001 import (  # noqa: E402
    BASE, BY_ID, DAY, LONE, MU, TARGET, Fixture, ts)
from tools import dji_area_footprint_calibration as tool  # noqa: E402

M_PER_DEG_LAT = math.pi * 6371000.0 / 180.0


def track(n, speed=0.0, spray=range(0), width=6.0, step_ms=100,
          flow_only=False, **kw):
    """``n`` кадров на север со скоростью ``speed``; применение в ``spray``."""
    out = []
    for i in range(n):
        on = i in spray
        out.append(frame(MS + i * step_ms,
                         lat=39.9 + speed * i * step_ms / 1000.0 / M_PER_DEG_LAT,
                         width=width, vx=0.0, vy=speed,
                         spray_flag=None if (flow_only or not on) else 1,
                         flow=900 if on else None, **kw))
    return out


def features(frames, list_width=None):
    from dji_area import v4
    return tool.application_features(v4.decode_v4(v4_bytes(frames)).frames,
                                     list_width)


class FootprintArithmetic(unittest.TestCase):

    def test_path_times_width_on_application_frames_only(self):
        # 5 м/с, 10 шагов по 0,1 с из кадров применения -> 5 м x 6 м = 30 м2.
        # Кадры 10..19 летят без применения и в след не входят.
        f = features(track(20, speed=5.0, spray=range(10)))
        self.assertEqual(f['application_frames'], 10)
        self.assertAlmostEqual(f['application_path_m'], 5.0, 3)
        self.assertAlmostEqual(f['footprint_m2'], 30.0, 2)
        self.assertAlmostEqual(f['application_duration_s'], 1.0, 6)
        self.assertEqual(f['width_source'], 'frames_application')
        self.assertEqual(f['application_bursts'], 1)

    def test_flow_without_the_flag_is_application(self):
        f = features(track(20, speed=5.0, spray=range(10), flow_only=True))
        self.assertAlmostEqual(f['footprint_m2'], 30.0, 2)

    def test_standing_application_leaves_no_footprint(self):
        f = features(track(20, speed=0.0, spray=range(20)))
        self.assertEqual(f['application_frames'], 20)
        self.assertEqual(f['footprint_m2'], 0.0)

    def test_no_application_means_zero_footprint_not_unknown(self):
        f = features(track(20, speed=5.0))
        self.assertEqual(f['application_frames'], 0)
        self.assertEqual(f['footprint_m2'], 0.0)

    def test_width_falls_back_to_the_list_and_then_to_nothing(self):
        frames = track(20, speed=5.0, spray=range(10), width=None)
        listed = features(frames, list_width=7.0)
        self.assertEqual(listed['width_source'], 'list')
        self.assertAlmostEqual(listed['footprint_m2'], 35.0, 2)
        blind = features(frames)
        self.assertIsNone(blind['footprint_m2'])
        self.assertAlmostEqual(blind['application_path_m'], 5.0, 3)

    def test_a_step_across_a_long_gap_is_skipped(self):
        frames = track(12, speed=5.0, spray=range(12), step_ms=2000)
        f = features(frames)
        self.assertEqual(f['steps_counted'], 0)
        self.assertEqual(f['steps_skipped'], 11)
        self.assertEqual(f['footprint_m2'], 0.0)


def add_v4(fx, flight_id, first_mu, last_mu, spray_seconds, speed=5.0):
    """V4 записи с шагом 1 с: применение первые ``spray_seconds`` секунд."""
    row = BY_ID[flight_id]
    t0, t1 = ts(row[1]), ts(row[2])
    n = t1 - t0
    frames = []
    for i in range(n + 1):
        on = i < spray_seconds
        frames.append(frame(
            (t0 + i) * 1000, area=first_mu + (last_mu - first_mu) * i / float(n),
            lat=39.9 + speed * i / M_PER_DEG_LAT, width=6.0, vx=0.0,
            vy=speed, spray_flag=1 if on else None, flow=900 if on else None))
    con = store.connect(fx.db)
    root = store.source_root(os.path.abspath(fx.db))
    store.begin_immediate(con)
    store.upsert_source_revision(con, root, 'v4', v4_bytes(frames),
                                 flight_id=flight_id)
    store.refresh_flight_evidence(con, root, flight_id)
    con.execute('COMMIT')
    con.close()


def sha256(path):
    with open(path, 'rb') as fh:
        return hashlib.sha256(fh.read()).hexdigest()


class ReadOnlyCalibration(unittest.TestCase):

    def setUp(self):
        self.fx = Fixture()
        self.addCleanup(self.fx.close)
        # Настоящая работа: счётчик вырос на весь RAW, распыление весь полёт.
        add_v4(self.fx, LONE, 0.0, 9000.0 / MU, spray_seconds=10 ** 6)
        # Повтор: счётчик плоский, распыление 3 с в полёте.
        add_v4(self.fx, TARGET, 15.0, 15.0, spray_seconds=3)
        pl.recalculate(self.fx.db, DAY, DAY, apply=True)
        self.out = tempfile.mkdtemp(prefix='footprint_calib_')
        self.addCleanup(shutil.rmtree, self.out, True)

    def run_tool(self, *args):
        buf = io.StringIO()
        with redirect_stdout(buf):
            code = tool.main(list(args))
        return code, buf.getvalue()

    def test_groups_ratios_and_nothing_written(self):
        before = sha256(self.fx.db)
        code, text = self.run_tool('--db', self.fx.db, '--out', self.out,
                                   '--flight-id', str(TARGET))
        self.assertEqual(code, tool.EXIT_OK, text)
        self.assertEqual(sha256(self.fx.db), before)
        self.assertTrue(text.isascii())
        with open(os.path.join(self.out, 'footprint_calibration.json'),
                  encoding='utf-8') as fh:
            report = json.load(fh)
        self.assertEqual(report['groups']['A1']['with_application'], 1)
        self.assertEqual(report['groups']['B']['with_application'], 1)
        with open(os.path.join(self.out, 'footprint_calibration.csv'),
                  encoding='utf-8-sig') as fh:
            rows = {int(r['flight_id']): r for r in csv.DictReader(fh)}
        real, repeat = rows[LONE], rows[TARGET]
        self.assertEqual(real['group'], 'A1')
        self.assertEqual(repeat['group'], 'B')
        self.assertEqual(repeat['named'], 'True')
        # 480 с x 5 м/с x 6 м против 9000 м2 и 3 с x 5 м/с x 6 м против 10000.
        self.assertAlmostEqual(float(real['footprint_to_raw']), 1.6, 2)
        self.assertAlmostEqual(float(repeat['footprint_to_raw']), 0.009, 3)
        cut = report['candidate_cuts'][0]
        self.assertEqual(cut['b_flagged'], 1)
        self.assertEqual(cut['a_false_positives'], 0)

    def test_a_missing_database_is_refused_and_not_created(self):
        missing = os.path.join(self.out, 'nope', 'transport.db')
        for extra in ((), ('--evaluate-rule',)):
            code, _text = self.run_tool('--db', missing, *extra)
            self.assertEqual(code, tool.EXIT_NO_DATABASE)
            self.assertFalse(os.path.exists(missing))


class ReadOnlyRuleEvaluation(unittest.TestCase):
    """--evaluate-rule (DJI-AREA-RETAINED-FOOTPRINT-001): сухой прогон
    настоящего конвейера по группе B и контрольным; база не меняется.

    TARGET -- перенесённый скаляр (RAW = RAW базы цепочки), плоский счётчик,
    2 м распыления. LONE -- такой же плоский счётчик и такой же малый след, но
    без цепочки: «ниже порога, структурного совпадения нет». Строки записаны
    кодом БЕЗ правила -- так выглядит production до слияния.
    """

    def setUp(self):
        self.fx = Fixture()
        self.addCleanup(self.fx.close)
        add_v4(self.fx, BASE, 0.0, 10000.0 / MU, spray_seconds=400)
        add_v4(self.fx, TARGET, 15.0, 15.0, spray_seconds=2, speed=1.0)
        add_v4(self.fx, LONE, 13.5, 13.5, spray_seconds=2, speed=1.0)
        self.baseline(rule=False)
        self.out = tempfile.mkdtemp(prefix='footprint_rule_')
        self.addCleanup(shutil.rmtree, self.out, True)

    def baseline(self, rule):
        if rule:
            pl.recalculate(self.fx.db, DAY, DAY, apply=True)
            return
        with mock.patch.object(rs, 'retained_negligible_footprint',
                               return_value=False):
            pl.recalculate(self.fx.db, DAY, DAY, apply=True)

    def evaluate(self, *extra):
        buf = io.StringIO()
        with redirect_stdout(buf):
            code = tool.main(['--db', self.fx.db, '--out', self.out,
                              '--evaluate-rule'] + list(extra))
        with open(os.path.join(self.out, 'retained_footprint_evaluation.json'),
                  encoding='utf-8') as fh:
            return code, buf.getvalue(), json.load(fh)

    def test_candidates_mismatches_and_invariants(self):
        before = sha256(self.fx.db)
        code, text, ev = self.evaluate()
        self.assertEqual(code, tool.EXIT_OK, text)
        self.assertEqual(sha256(self.fx.db), before)
        self.assertTrue(text.isascii())
        self.assertNotIn('--apply', text)
        self.assertEqual(ev['b_total'], 2)
        self.assertEqual(ev['b_passes_footprint'], 2)
        self.assertEqual(ev['b_passes_footprint_structural_match'], 1)
        self.assertEqual(ev['below_cut_structural_mismatch'], [LONE])
        self.assertEqual(ev['final_candidates'], [TARGET])
        self.assertAlmostEqual(ev['final_candidates_raw_ha'], 1.0, 6)
        self.assertEqual(ev['remaining_review_b'], [LONE])
        self.assertEqual(ev['review_total_after'],
                         ev['review_total_before'] - 1)
        # Переписалась бы только строка, где правило сработало.
        self.assertEqual(ev['dry_run_calc_writes'],
                         {'unchanged': 1, 'would_write': 1})
        self.assertEqual(ev['raw_changed'], 0)
        self.assertEqual(ev['billable_non_null_table'], 0)
        self.assertEqual(ev['billable_non_null_dry_run'], 0)
        self.assertEqual(ev['violations'], [])
        target = {r['flight_id']: r for r in ev['records']}[TARGET]
        self.assertAlmostEqual(target['conservative_footprint_to_raw'],
                               2.0 * 12.0 / 10000.0, 4)
        self.assertEqual(target['dry_reason'],
                         'RETAINED_SCALAR_WITH_NEGLIGIBLE_FOOTPRINT')
        self.assertEqual(target['accepted_after_m2'], 0.0)
        # День фикстуры (02.09) внутри периода сентябрьского оракула: такая
        # запись заранее остановит блок A ранбука, и оценка её называет.
        self.assertEqual(ev['oracle_period'], ['2026-09-01', '2026-09-18'])
        self.assertEqual(ev['oracle_period_candidates'], [TARGET])
        self.assertIn('inside the September oracle period', text)
        self.assertIsNone(tool.oracle_period(os.path.join(self.out, 'no.json')))
        # Контрольные production в синтетике отсутствуют -- и так и названы.
        self.assertTrue(all(r.get('absent') for r in ev['named']))
        self.assertTrue(all(r.get('absent') for r in ev['negative_controls']))
        self.assertTrue(os.path.exists(os.path.join(
            self.out, 'retained_footprint_evaluation.csv')))

    def test_a_negative_control_that_fires_is_a_violation(self):
        code, text, ev = self.evaluate('--negative-control', str(TARGET))
        self.assertEqual(code, tool.EXIT_CONTROL_VIOLATED, text)
        self.assertIn('NEGATIVE_CONTROL_FIRED:%d' % TARGET, ev['violations'])
        self.assertIn('VIOLATION NEGATIVE_CONTROL_FIRED', text)
        # Та же запись, названная просто для отчёта, нарушением не является.
        code, text, ev = self.evaluate('--flight-id', str(TARGET))
        self.assertEqual(code, tool.EXIT_OK, text)

    def test_after_apply_the_evaluation_repeats_without_rewrites(self):
        self.baseline(rule=True)
        code, text, ev = self.evaluate()
        self.assertEqual(code, tool.EXIT_OK, text)
        self.assertEqual(ev['final_candidates'], [TARGET])
        self.assertEqual(ev['dry_run_calc_writes'], {'unchanged': 2})

    def test_a_rewrite_where_the_rule_does_not_fire_is_a_violation(self):
        # Отрицательный контроль самой проверки: строка LONE посчитана «не
        # этим кодом» -- её отпечаток другой, и применение переписало бы её.
        con = sqlite3.connect(self.fx.db)
        con.execute("UPDATE dji_area_calculations SET calculation_input_hash="
                    "'stale' WHERE flight_id=? AND superseded_at IS NULL",
                    (LONE,))
        con.commit()
        con.close()
        code, text, ev = self.evaluate()
        self.assertEqual(code, tool.EXIT_CONTROL_VIOLATED, text)
        self.assertEqual(ev['violations'], ['UNEXPECTED_REWRITE:%d' % LONE])


if __name__ == '__main__':
    unittest.main()
