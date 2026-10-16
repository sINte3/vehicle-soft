# -*- coding: utf-8 -*-
"""DRONE-CARD-COVERAGE-001: инструмент пилота -- выборка, отпечатки, сверка.

Без Flask: синтетическая база строится теми же DDL миграций и тем же
`Seed`, что у тестов паспорта поля. Сквозной путь через настоящий приёмник
`/drones/api/source_sync` и настоящий пересчёт -- в
`tests/test_dji_card_coverage_pilot_e2e.py` (там нужен Flask).

Что держится здесь:
* выборка детерминирована: тот же вход -- те же байты манифеста, порядок
  вставки строк в базу на неё не влияет;
* размещение пропорционально и не раздувает маленькие слои; канарейка --
  префикс манифеста и сама распределена по слоям;
* потолок 500 не обходится ни аргументом, ни опечаткой;
* замороженный манифест не переписывается другим входом;
* production-копию кода инструмент не открывает, отсутствующую базу не
  создаёт, базу не меняет (mode=ro);
* счётчики инструмента сверяются с переписью -- расхождение даёт код 5;
* журнал сборщика: статусы, время, признаки «остановиться» (код 6), в
  выводе только номера и статусы;
* интервал Уилсона и ворота неизменности -- с отрицательными контролями.

Запуск: python -m unittest tests.test_dji_card_coverage_pilot -v
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

from datetime import datetime, timedelta

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if REPO_ROOT not in sys.path:
    sys.path.insert(0, REPO_ROOT)

from dji_area import field as fld  # noqa: E402
from dji_area import field_view as fv  # noqa: E402
from tests.test_drone_field_passport_core import Seed, make_schema  # noqa: E402
from tools import dji_card_coverage_pilot as pilot  # noqa: E402

T5 = fld.TIER5_UNKNOWN
SEP_FIRST = datetime(2026, 9, 1, 3, 0)     # UTC; местное 08:00 1 сентября
SECRET = 'SYNTHETIC-TOKEN-NOT-REAL-7f3a'
SIGNED = 'https://synthetic.invalid/v4?Expires=1&Signature=NOT-REAL'


def run(argv):
    out = io.StringIO()
    code = pilot.main(argv, out=out)
    return code, out.getvalue()


def build_db(path, order=1, no_card=60, units=(6, 7, 9)):
    """Сентябрь: вылеты без карточки по трём бортам и пяти неделям плюс
    контроль с карточкой (EXACT, NO_KEY, NOT_IN_CATALOG), вылет без привязки
    и вылет августа. ``order`` -- -1 вставляет строки в обратном порядке."""
    con = make_schema(path)
    con.execute('CREATE TABLE schema_migrations (id INTEGER PRIMARY KEY, '
                'name TEXT, applied_at TEXT)')
    con.execute("INSERT INTO schema_migrations (name, applied_at) VALUES "
                "('SYNTHETIC_MIGRATION', '2026-09-01 00:00:00')")
    s = Seed(con)
    unit_ids = [s.unit(number) for number in units]
    plan = []
    for i in range(no_card):
        started = SEP_FIRST + timedelta(hours=11 * i)    # до 27 сентября
        plan.append((800000 + i, started, unit_ids[i % len(unit_ids)]))
    for fid, started, unit in plan[::order]:
        s.flight(fid, started, 1.5, unit_id=unit)
        s.attribution(fid, T5, 'AUTO_NO_KEY')
    # Контроль: карточка уже есть.
    s.flight(810001, SEP_FIRST + timedelta(days=3), 2.0, unit_id=unit_ids[0])
    s.attribution(810001, fld.TIER1_EXACT, 'PLAIN_MD5_GEOMETRY_OBJECT',
                  'aaaaaaaa-0000-4000-8000-0000000000aa', 'a' * 32, 1)
    s.evidence(810001, card_rev=s.source(810001), card_key='a' * 32)
    s.flight(810002, SEP_FIRST + timedelta(days=4), 2.0, unit_id=unit_ids[1])
    s.attribution(810002, T5, 'AUTO_NO_KEY')
    s.evidence(810002, card_rev=s.source(810002))
    s.flight(810003, SEP_FIRST + timedelta(days=5), 2.0, unit_id=unit_ids[2])
    s.attribution(810003, T5, 'PLAIN_MD5_NOT_IN_CATALOG', md5='b' * 32)
    s.evidence(810003, card_rev=s.source(810003), card_key='b' * 32)
    # Без привязки вовсе и вылет вне периода.
    s.flight(810004, SEP_FIRST + timedelta(days=6), 2.0, unit_id=unit_ids[0])
    s.flight(810005, datetime(2026, 8, 20, 6, 0), 2.0, unit_id=unit_ids[0])
    s.attribution(810005, T5, 'AUTO_NO_KEY')
    con.commit()
    con.close()


class Temp(unittest.TestCase):

    def setUp(self):
        self.dir = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.dir, True)
        self.db = os.path.join(self.dir, 'snapshot.db')
        build_db(self.db)

    def plan(self, out='plan', *extra, db=None):
        target = os.path.join(self.dir, out)
        code, text = run(['plan', '--db', db or self.db, '--out-dir', target]
                         + list(extra))
        return code, text, target

    def read(self, path, name):
        with open(os.path.join(path, name), 'rb') as handle:
            return handle.read()


class Plan(Temp):

    def test_plan_counts_match_the_census_and_cohort(self):
        code, text, out = self.plan('plan', '--cap', '20', '--canary', '6')
        self.assertEqual(code, 0, text)
        plan = json.loads(self.read(out, pilot.PLAN_NAME))
        self.assertEqual(plan['census']['flights'], 64)
        self.assertEqual(plan['census']['reasons']['NO_CARD'], 60)
        self.assertEqual(plan['cohort']['no_card'], 60)
        self.assertEqual(plan['control']['with_card'], 3)
        self.assertEqual(plan['control']['confirmed'], 1)
        self.assertEqual(plan['control']['distribution'],
                         {'EXACT': 1, 'NOT_IN_CATALOG': 1, 'NO_KEY': 1})
        self.assertEqual(plan['sample']['size'], 20)
        self.assertEqual(plan['sample']['canary'], 6)
        self.assertIn('NO_CARD            60', text)

    def test_same_input_same_bytes_and_insert_order_does_not_matter(self):
        _c, _t, first = self.plan('a', '--cap', '20')
        other = os.path.join(self.dir, 'reversed.db')
        build_db(other, order=-1)
        _c, _t, second = self.plan('b', '--cap', '20', db=other)
        for name in (pilot.MANIFEST_NAME, pilot.CANARY_IDS, pilot.PILOT_IDS,
                     pilot.COHORT_NAME):
            self.assertEqual(self.read(first, name), self.read(second, name),
                             name)

    def test_selection_is_the_lowest_keys_inside_each_stratum(self):
        _c, _t, out = self.plan('plan', '--cap', '20')
        with open(os.path.join(out, pilot.COHORT_NAME)) as handle:
            cohort = list(csv.DictReader(handle))
        by = {}
        for row in cohort:
            by.setdefault(row['stratum'], []).append(row)
        for stratum, rows in by.items():
            rows.sort(key=lambda r: r['selection_key'])
            chosen = [r['selected'] for r in rows]
            # Выбранные -- префикс по ключу: после первого «0» единиц нет.
            self.assertEqual(chosen, sorted(chosen, reverse=True), stratum)
        key = pilot.selection_key(800001)
        self.assertEqual(key, hashlib.sha256(
            b'DRONE-CARD-COVERAGE-001|800001').hexdigest())

    def test_strata_keep_cyrillic_nicknames_apart(self):
        first = pilot.stratum_of(None, 'Нурмат 1', 37)
        second = pilot.stratum_of(None, 'Нурмат 2', 37)
        self.assertNotEqual(first, second)
        self.assertTrue(first.isascii())
        self.assertEqual(pilot.stratum_of(6, 'Нурмат 1', 37), 'U006|W37')
        self.assertEqual(pilot.stratum_of(None, None, 37), 'UNKNOWN|W37')

    def test_allocation_is_proportional_and_never_inflates(self):
        alloc = pilot.allocate({'A': 10, 'B': 5, 'C': 1}, 8)
        self.assertEqual(sum(alloc.values()), 8)
        self.assertEqual(alloc, {'A': 5, 'B': 3, 'C': 0})
        alloc = pilot.allocate({'A': 2, 'B': 1}, 10)
        self.assertEqual(alloc, {'A': 2, 'B': 1})

    def test_canary_is_a_prefix_spread_over_strata(self):
        _c, _t, out = self.plan('plan', '--cap', '30', '--canary', '15')
        with open(os.path.join(out, pilot.MANIFEST_NAME)) as handle:
            manifest = list(csv.DictReader(handle))
        canary = [m for m in manifest if m['canary'] == '1']
        self.assertEqual([m['manifest_order'] for m in canary],
                         [str(i) for i in range(1, 16)])
        units = {m['unit_number'] for m in canary}
        self.assertEqual(units, {'6', '7', '9'})
        ids = pilot.read_ids(os.path.join(out, pilot.CANARY_IDS))
        self.assertEqual(ids, [int(m['flight_id']) for m in canary])

    def test_cap_cannot_be_raised(self):
        for cap in ('501', '6067'):
            code, text, out = self.plan('x' + cap, '--cap', cap)
            self.assertEqual(code, pilot.EXIT_REFUSED, text)
            self.assertFalse(os.path.exists(out))
        self.assertEqual(pilot.HARD_CAP, 500)
        code, _t, out = self.plan('all')        # потолок больше когорты
        self.assertEqual(code, 0)
        plan = json.loads(self.read(out, pilot.PLAN_NAME))
        self.assertEqual(plan['sample']['size'], 60)

    def test_frozen_manifest_is_never_replaced(self):
        code, _t, out = self.plan('plan', '--cap', '20')
        before = self.read(out, pilot.MANIFEST_NAME)
        code, text, _o = self.plan('plan', '--cap', '20')
        self.assertEqual(code, 0)
        self.assertIn('ALREADY FROZEN', text)
        code, text, _o = self.plan('plan', '--cap', '21')
        self.assertEqual(code, pilot.EXIT_REFUSED, text)
        self.assertEqual(self.read(out, pilot.MANIFEST_NAME), before)

    def test_production_folder_is_refused_staging_is_not(self):
        prod = os.path.join(self.dir, 'transport-report', 'instance')
        os.makedirs(prod)
        shutil.copy(self.db, os.path.join(prod, 'transport.db'))
        code, text, out = self.plan('p', db=os.path.join(prod, 'transport.db'))
        self.assertEqual(code, pilot.EXIT_REFUSED, text)
        self.assertFalse(os.path.exists(out))
        staging = os.path.join(self.dir, 'transport-report-staging')
        os.makedirs(staging)
        shutil.copy(self.db, os.path.join(staging, 'transport.db'))
        code, text, _o = self.plan('s', db=os.path.join(staging,
                                                        'transport.db'))
        self.assertEqual(code, 0, text)
        self.assertTrue(pilot.is_production_path('C:\\transport-report\\'
                                                 'instance\\transport.db'))
        self.assertFalse(pilot.is_production_path(
            'D:\\transport-report-backups\\staging\\card_pilot\\s.db'))

    def test_missing_database_is_not_created_and_db_is_not_changed(self):
        missing = os.path.join(self.dir, 'nope.db')
        code, _t, _o = self.plan('m', db=missing)
        self.assertEqual(code, pilot.EXIT_NO_DATABASE)
        self.assertFalse(os.path.exists(missing))
        digest = pilot.sha256_file(self.db)
        self.plan('plan', '--cap', '20')
        run(['fingerprint', '--db', self.db, '--out',
             os.path.join(self.dir, 'fp.json')])
        self.assertEqual(pilot.sha256_file(self.db), digest)

    def test_census_mismatch_is_a_gate_failure(self):
        # Отрицательный контроль: перепись говорит одно, построчная
        # классификация -- другое. Инструмент обязан остановиться.
        real = pilot.fs.flight_census

        def lying(*a, **k):
            total, months = real(*a, **k)
            total['reasons']['NO_CARD'] -= 1
            return total, months
        pilot.fs.flight_census = lying
        try:
            code, text, out = self.plan('lie', '--cap', '20')
        finally:
            pilot.fs.flight_census = real
        self.assertEqual(code, pilot.EXIT_GATE, text)
        self.assertFalse(os.path.exists(os.path.join(out, pilot.MANIFEST_NAME)))

    def test_outputs_carry_no_body_path_or_secret(self):
        _c, text, out = self.plan('plan', '--cap', '20')
        blob = text
        for name in os.listdir(out):
            blob += self.read(out, name).decode('utf-8')
        for bait in ('SYNTHETIC-POLYGON-BODY', 'signedUrl', 'authorization',
                     'C:\\SYNTHETIC', '64.36347'):
            self.assertNotIn(bait, blob)


class Fingerprint(Temp):

    def fp(self, db, name):
        path = os.path.join(self.dir, name)
        code, text = run(['fingerprint', '--db', db, '--out', path])
        self.assertEqual(code, 0, text)
        with open(path) as handle:
            return json.load(handle)

    def test_raw_change_moves_the_raw_fingerprint(self):
        first = self.fp(self.db, 'a.json')
        con = sqlite3.connect(self.db)
        con.execute('UPDATE drone_flights SET area_ha = area_ha + 0.01 '
                    'WHERE dji_flight_id = 800003')
        con.commit()
        con.close()
        second = self.fp(self.db, 'b.json')
        self.assertNotEqual(first['raw_area_ha'], second['raw_area_ha'])
        self.assertEqual(first['source_revisions'], second['source_revisions'])

    def test_gates_pass_on_identical_and_fail_on_each_change(self):
        before = self.fp(self.db, 'a.json')
        names = [g for g, ok, _d in pilot.gates(before, before, [])]
        self.assertTrue(all(ok for _g, ok, _d in pilot.gates(before, before,
                                                             [])))
        self.assertEqual(len(names), 5)
        after = json.loads(json.dumps(before))
        after['period_current']['800001'] = [999, 999, 999]
        result = dict((g, ok) for g, ok, _d in pilot.gates(before, after, []))
        self.assertFalse(result['flights outside the manifest untouched '
                                '(attribution, calculation, card)'])
        result = dict((g, ok) for g, ok, _d in pilot.gates(before, after,
                                                           [800001]))
        self.assertTrue(all(result.values()))
        after = json.loads(json.dumps(before))
        after['drone_area_decisions']['rows'] += 1
        self.assertFalse(dict((g, ok) for g, ok, _d in pilot.gates(
            before, after, []))['drone_area_decisions unchanged'])


LOG = [
    '2026-10-05 10:00:00,001 INFO drone_collector: Read 4 flight id(s) from ids.txt',
    '2026-10-05 10:00:12,001 INFO drone_collector: Flight 800001: V4 (airlines, card, route, v4)',
    '2026-10-05 10:00:24,001 INFO drone_collector: Flight 800002: NO_V4_URL (airlines, card, route)',
    '2026-10-05 10:00:44,001 ERROR drone_collector: Flight 800003: the record page did not open (TimeoutError)',
    '2026-10-05 10:00:50,001 INFO drone_collector: Flight 999999: V4 (airlines, card, route, v4)',
    '2026-10-05 10:00:51,001 INFO drone_collector: RUN SUMMARY mode=sources sources_visited=4 exit=0',
]


class CollectorStats(Temp):

    def stats(self, lines, ids=(800001, 800002, 800003, 800004)):
        log = os.path.join(self.dir, 'collector.log')
        with open(log, 'w') as handle:
            handle.write('\n'.join(lines) + '\n')
        ids_path = os.path.join(self.dir, 'ids.txt')
        with open(ids_path, 'w') as handle:
            handle.write('# ids\n' + '\n'.join(str(i) for i in ids) + '\n')
        out = os.path.join(self.dir, 'stats.json')
        code, text = run(['collector-stats', '--log', log, '--ids', ids_path,
                          '--out', out])
        with open(out) as handle:
            return code, text, json.load(handle)

    def test_visits_statuses_and_time(self):
        code, text, st = self.stats(LOG)
        self.assertEqual(code, 0, text)
        self.assertEqual(st['visited'], 3)
        self.assertEqual(st['not_visited'], [800004])
        self.assertEqual(st['card_captured'], 2)
        self.assertEqual(st['statuses'], {'NO_V4_URL': 1, 'PAGE_ERROR': 1,
                                          'V4': 1})
        self.assertEqual(st['visits_measured'], 3)
        self.assertEqual(st['seconds_per_visit_median'], 12.0)
        self.assertEqual(st['summaries'][0]['sources_visited'], '4')

    def test_stop_markers_stop_the_pilot(self):
        for line, marker in (
                ('ERROR drone_collector: HTTP 429 Too Many Requests', 'HTTP_429'),
                ('ERROR drone_collector: the saved session is no longer signed in', 'SESSION'),
                ('WARNING drone_collector: captcha challenge on the page', 'CAPTCHA'),
                ('ERROR drone_collector: Three record pages in a row did not open; the browser is not usable.', 'BROWSER_DEAD')):
            code, text, st = self.stats(LOG + ['2026-10-05 10:01:00,001 ' + line])
            self.assertEqual(code, pilot.EXIT_COLLECTOR_STOP, line)
            self.assertIn(marker, st['stop'])
            self.assertIn('COLLECTOR VERDICT    STOP', text)

    def test_only_ids_and_statuses_leave_the_log(self):
        code, text, st = self.stats(LOG + [
            '2026-10-05 10:02:00,001 DEBUG x: token=%s url=%s' % (SECRET, SIGNED)])
        blob = text + json.dumps(st)
        self.assertNotIn(SECRET, blob)
        self.assertNotIn('Signature', blob)


class Statistics(unittest.TestCase):

    def test_wilson_known_values(self):
        lo, hi = pilot.wilson(5, 10)
        self.assertAlmostEqual(lo, 0.2366, places=3)
        self.assertAlmostEqual(hi, 0.7634, places=3)
        lo, hi = pilot.wilson(0, 50)
        self.assertEqual(lo, 0.0)
        self.assertAlmostEqual(hi, 0.0713, places=3)
        self.assertEqual(pilot.wilson(0, 0), (None, None))

    def test_post_stratified_weights_follow_the_cohort(self):
        rows = ([{'stratum': 'A', 'fetched': True, 'confirmed_after': True}] * 2
                + [{'stratum': 'B', 'fetched': True, 'confirmed_after': False}] * 2
                + [{'stratum': 'B', 'fetched': False, 'confirmed_after': False}])
        ps = pilot.post_stratified(rows, {'A': 30, 'B': 10, 'C': 60})
        self.assertAlmostEqual(ps['estimate'], 0.75)
        self.assertAlmostEqual(ps['cohort_share_covered'], 0.4)


if __name__ == '__main__':
    unittest.main()
