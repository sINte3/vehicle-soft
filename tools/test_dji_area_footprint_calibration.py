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
import sys
import tempfile
import unittest
from contextlib import redirect_stdout

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from dji_area import pipeline as pl  # noqa: E402
from dji_area import store  # noqa: E402
from tests.test_dji_area_core import MS, frame, v4_bytes  # noqa: E402
from tests.test_dji_area_identity_001 import (  # noqa: E402
    BY_ID, DAY, LONE, MU, TARGET, Fixture, ts)
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
        code, _text = self.run_tool('--db', missing)
        self.assertEqual(code, tool.EXIT_NO_DATABASE)
        self.assertFalse(os.path.exists(missing))


if __name__ == '__main__':
    unittest.main()
