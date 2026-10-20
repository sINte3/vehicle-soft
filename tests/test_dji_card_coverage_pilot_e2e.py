# -*- coding: utf-8 -*-
"""DRONE-CARD-COVERAGE-001 сквозь настоящий путь: приёмник и пересчёт.

Каталог полей -- через `/drones/api/land_snapshot_sync`, карточки -- через
`/drones/api/source_sync`, привязки -- настоящим `tools/dji_area_recalc.py
--apply --flight-id`. Инструмент пилота только читает: план на копии базы
ДО сбора, сверка на базе ПОСЛЕ. Так проверяется, что `measure` видит
EXACT / NO_KEY / отказ сбора ровно так, как их видит штатный путь, а
ворота неизменности ловят изменение RAW и чужих вылетов.

Требует Flask (как все тесты экранов) -- идёт в полном прогоне
`python -m unittest discover -s tests`, не в CI-шаге инструмента.
"""

import base64
import csv
import hashlib
import io
import json
import os
import shutil
import sqlite3
import subprocess
import sys
import tempfile
import unittest

from datetime import datetime, timezone

from tests.harness import TEST_DB_PATH
from tests.test_dji_area_ingest_001 import Base, card_json, source

from tools import dji_card_coverage_pilot as pilot

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
RECALC = os.path.join(ROOT, 'tools', 'dji_area_recalc.py')
LAND = '11111111-2222-3333-4444-555555555555'
GEO = (b'{"type":"FeatureCollection","features":[{"type":"Feature",'
       b'"properties":{"funcType":"PlantZone"},"geometry":{"type":"Polygon",'
       b'"coordinates":[[[64.4,39.7,0],[64.41,39.7,0],[64.41,39.71,0],'
       b'[64.4,39.7,0]]]}}]}')
MD5 = hashlib.md5(GEO).hexdigest()
SEP = int(datetime(2026, 9, 12, 6, 0, tzinfo=timezone.utc).timestamp())
FLIGHTS = list(range(900101, 900109))     # 900108 -- контроль с карточкой


def run_tool(argv):
    out = io.StringIO()
    code = pilot.main(argv, out=out)
    return code, out.getvalue()


class CardCoverageEndToEnd(Base):

    def setUp(self):
        super(CardCoverageEndToEnd, self).setUp()
        self.dir = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.dir, True)
        node = {'uuid': LAND, 'externalId': 'ext-1', 'name': 'SYNTHETIC field',
                'totalArea': 44.5, 'workArea': 40.0, 'totalObstacleArea': 0,
                'landType': 'PLANT_LAND',
                'createdAt': '2026-08-01T10:00:00+08:00',
                'updatedAt': '2026-08-01T10:00:00+08:00',
                'position': {'lat': 39.7, 'lng': 64.4},
                'geometry': {'storage': {'uuid': 'g-1', 'contentMd5': MD5}},
                'serialNumber': 'P1'}
        answer = self.client.post('/drones/api/land_snapshot_sync', json={
            'token': 'SYNTHETIC-source-sync-token-NOT-REAL',
            'snapshot': {'capture_run_id': 'SNAP-1', 'final': True,
                         'complete': True, 'expected_count': 1},
            'lands': [node],
            'geometries': [{'content_md5': MD5,
                            'body_b64': base64.b64encode(GEO).decode()}]})
        self.assertEqual(answer.status_code, 200, answer.get_json())
        for i, fid in enumerate(FLIGHTS):
            self.add_flight(fid, start=SEP + 600 * i)
        # Контроль: карточка с ключом уже была до пилота.
        self.card(900108, key=True)
        self.recalc_period()
        self.snapshot = os.path.join(self.dir, 'snapshot.db')
        self.copy_db(self.snapshot)
        self.plan_dir = os.path.join(self.dir, 'plan')
        code, text = run_tool(['plan', '--db', self.snapshot, '--out-dir',
                               self.plan_dir, '--cap', '6', '--canary', '3'])
        self.assertEqual(code, 0, text)
        code, text = run_tool(['fingerprint', '--db', self.snapshot, '--out',
                               os.path.join(self.plan_dir,
                                            pilot.FINGERPRINT_BEFORE)])
        self.assertEqual(code, 0, text)
        with open(os.path.join(self.plan_dir, pilot.MANIFEST_NAME)) as handle:
            self.manifest = [int(r['flight_id']) for r in csv.DictReader(handle)]
        self.canary = self.manifest[:3]

    def copy_db(self, target):
        src = sqlite3.connect(TEST_DB_PATH)
        dst = sqlite3.connect(target)
        try:
            src.backup(dst)
        finally:
            dst.close()
            src.close()

    def card(self, fid, key):
        md5 = (LAND + '__' + MD5) if key else ''
        answer = self.post_sources([source(fid, 'card',
                                           card_json(fid, md5=md5,
                                                     start=SEP))])
        self.assertEqual(answer.status_code, 200, answer.get_json())

    def recalc(self, *extra):
        proc = subprocess.run(
            [sys.executable, RECALC, '--from', '2026-09-12', '--to',
             '2026-09-12', '--db', TEST_DB_PATH, '--quiet', '--apply']
            + list(extra), capture_output=True, text=True, cwd=ROOT)
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)

    def recalc_period(self):
        self.recalc()

    def recalc_flights(self, fids):
        extra = []
        for fid in fids:
            extra += ['--flight-id', str(fid)]
        self.recalc(*extra)

    def measure(self, stage):
        out = os.path.join(self.dir, 'measure_' + stage)
        code, text = run_tool(['measure', '--db', TEST_DB_PATH, '--plan-dir',
                               self.plan_dir, '--stage', stage, '--out-dir',
                               out])
        with open(os.path.join(out, 'measure_%s.json' % stage)) as handle:
            return code, text, json.load(handle)

    def test_baseline_cohort_is_no_card_and_control_is_exact(self):
        with open(os.path.join(self.plan_dir, pilot.PLAN_NAME)) as handle:
            plan = json.load(handle)
        self.assertEqual(plan['cohort']['no_card'], 7)
        self.assertEqual(plan['control']['distribution'], {'EXACT': 1})
        self.assertEqual(len(self.manifest), 6)
        self.assertNotIn(900108, self.manifest)

    def test_canary_through_the_real_receiver_and_recalc(self):
        self.card(self.canary[0], key=True)
        self.card(self.canary[1], key=True)
        self.card(self.canary[2], key=False)
        self.recalc_flights(self.canary)
        code, text, res = self.measure('canary')
        self.assertEqual(code, 0, text)
        self.assertEqual((res['fetched'], res['confirmed']), (3, 2))
        self.assertEqual(res['outcomes_among_fetched'],
                         {'EXACT': 2, 'NO_KEY': 1})
        self.assertTrue(all(g['pass'] for g in res['gates']), res['gates'])
        self.assertEqual(sorted(res['cases']['new_exact']),
                         sorted(self.canary[:2]))
        self.assertEqual(res['cases']['no_key'], [self.canary[2]])

    def test_card_without_recalc_is_not_counted_as_confirmed(self):
        # Карточка пришла, пересчёта не было: ключ есть, привязка старая --
        # это KEY_AFTER_RESOLUTION, а не подтверждение.
        self.card(self.canary[0], key=True)
        code, text, res = self.measure('canary')
        self.assertEqual(res['confirmed'], 0)
        self.assertEqual(res['outcomes_among_fetched'],
                         {'KEY_AFTER_RESOLUTION': 1})

    def test_failure_stays_in_the_denominator(self):
        for fid in self.manifest[:-1]:
            self.card(fid, key=True)
        self.recalc_flights(self.manifest[:-1])
        code, text, res = self.measure('pilot')
        self.assertEqual(code, 0, text)
        self.assertEqual(res['manifest_flights_in_stage'], 6)
        self.assertEqual((res['fetched'], res['fetch_failed']), (5, 1))
        self.assertEqual(res['cases']['fetch_failure'], [self.manifest[-1]])
        self.assertAlmostEqual(res['conversion_among_manifest']['rate'], 5 / 6.0)

    def test_negative_control_a_flight_outside_the_stage_changed(self):
        outside = [f for f in FLIGHTS
                   if f not in self.canary and f != 900108][0]
        self.card(self.canary[0], key=True)
        self.card(outside, key=True)
        self.recalc_flights([self.canary[0], outside])
        code, text, res = self.measure('canary')
        self.assertEqual(code, pilot.EXIT_GATE, text)
        failed = [g['gate'] for g in res['gates'] if not g['pass']]
        self.assertEqual(failed, ['flights outside the manifest untouched '
                                  '(attribution, calculation, card)'])

    def test_negative_control_raw_changed(self):
        con = sqlite3.connect(TEST_DB_PATH)
        con.execute('UPDATE drone_flights SET area_ha = area_ha * 2 '
                    'WHERE dji_flight_id = ?', (self.canary[0],))
        con.commit()
        con.close()
        code, text, res = self.measure('canary')
        self.assertEqual(code, pilot.EXIT_GATE, text)
        self.assertIn('GATE FAIL RAW area_ha of every flight unchanged', text)


if __name__ == '__main__':
    unittest.main()
