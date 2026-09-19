# -*- coding: utf-8 -*-
"""tools/test_dji_area_holdout.py -- самотест слепого holdout.

Синтетическая база, настоящий тракт: таблицы `dji_*` берутся из САМОЙ миграции,
тела V4 кладутся настоящим `store.upsert_source_revision`, строки улик собирает
настоящий `store.refresh_flight_evidence`, а решения выносит настоящий
`dji_area.pipeline`. Ни одна запись здесь не является настоящим вылетом.

Сценарий построен как РАСХОЖДЕНИЕ: рядом с кандидатом замороженного экрана
стоят записи, которые на него похожи и им не являются -- настоящий повтор
площади, авто-запись без ширины вне цепочки, скрытый фантом с заполненной
шириной. Приёмка проверяется в шести исходах (PASS, FAIL по опровержению,
FAIL по доле, FAIL по контрольным воротам в двух вариантах, INCONCLUSIVE),
чтобы ни один из них не оказался недостижимым.

Отдельно держатся два свойства пред-live ужесточения: правка САМОГО
инструмента между plan и report -- отказ с кодом 4, а не пометка; и
систематический пропуск правила в контроле валит общий holdout, не трогая
приёмку кандидатов.

Запуск:  python tools\\test_dji_area_holdout.py
"""

import hashlib
import io
import json
import os
import re
import shutil
import sqlite3
import subprocess
import sys
import tempfile
import unittest
from contextlib import redirect_stdout
from datetime import datetime

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from dji_area import accounting as acc  # noqa: E402
from dji_area import store  # noqa: E402
from tests.test_dji_area_core import frame, v4_bytes  # noqa: E402
from tools import dji_area_holdout as tool  # noqa: E402

TOOL = os.path.join(ROOT, 'tools', 'dji_area_holdout.py')
MIGRATION = os.path.join(ROOT, 'migrate_dji_area_evidence_001.py')
HW1, HW2 = 'SYNTHETIC-HW-1-NOT-REAL', 'SYNTHETIC-HW-2-NOT-REAL'
MU = 2000.0 / 3.0

# Идентификаторы сценария. День отчёта -- 2026-09-02 (UTC+5).
A1, B1, C1 = 1001, 1002, 1003          # база -> ручной мостик -> КАНДИДАТ
REAL, REPEAT, HIDDEN = 1004, 1005, 1006  # обычный, честный повтор, скрытый
P1, P2, MANUAL, P4 = 2001, 2002, 2003, 2004
A2, B2, C2 = 2005, 2006, 2007          # кандидат БЕЗ совпадения скаляра
LONE_NO_WIDTH = 2010                   # авто без ширины вне цепочки
NEXT_DAY = 3001                        # делает 02.09 «полным» днём


def ts(text):
    return int((datetime.strptime(text, '%Y-%m-%d %H:%M:%S')
                - datetime(1970, 1, 1)).total_seconds())


def _ddl():
    with open(MIGRATION, encoding='utf-8') as fh:
        text = fh.read()
    return re.findall(r'CREATE TABLE IF NOT EXISTS \w+ \(.*?\n    \)', text,
                      re.S)


# (id, борт, начало, конец, режим, ширина, RAW м²)
FLIGHTS = (
    (A1, 1, '2026-09-02 10:00:00', '2026-09-02 10:07:00', 4, 6.0, 10000.0),
    (B1, 1, '2026-09-02 10:07:00', '2026-09-02 10:07:20', 1, None, 500.0),
    (C1, 1, '2026-09-02 10:07:20', '2026-09-02 10:08:30', 4, None, 10000.0),
    (REAL, 1, '2026-09-02 10:20:00', '2026-09-02 10:27:00', 4, 6.0, 9000.0),
    (REPEAT, 1, '2026-09-02 10:30:00', '2026-09-02 10:37:00', 4, 6.0, 9000.0),
    (HIDDEN, 1, '2026-09-02 10:37:00', '2026-09-02 10:37:50', 4, 6.0, 9000.0),
    (P1, 2, '2026-09-02 11:00:00', '2026-09-02 11:08:00', 4, 6.2, 11000.0),
    (P2, 2, '2026-09-02 11:09:00', '2026-09-02 11:16:00', 4, 6.2, 11200.0),
    (MANUAL, 2, '2026-09-02 11:30:00', '2026-09-02 11:35:00', 1, None, 3000.0),
    (P4, 2, '2026-09-02 12:00:00', '2026-09-02 12:08:00', 4, 6.2, 10500.0),
    (A2, 2, '2026-09-02 13:00:00', '2026-09-02 13:07:00', 4, 6.2, 8000.0),
    (B2, 2, '2026-09-02 13:07:00', '2026-09-02 13:07:10', 1, None, 100.0),
    (C2, 2, '2026-09-02 13:07:10', '2026-09-02 13:08:00', 4, None, 7000.0),
    (LONE_NO_WIDTH, 2, '2026-09-02 15:00:00', '2026-09-02 15:06:00', 4, None,
     1500.0),
    (NEXT_DAY, 2, '2026-09-03 09:00:00', '2026-09-03 09:08:00', 4, 6.2,
     9900.0),
)
BY_ID = {f[0]: f for f in FLIGHTS}


def build(db_path):
    os.makedirs(os.path.dirname(db_path), exist_ok=True)
    con = sqlite3.connect(db_path)
    con.execute('CREATE TABLE drone_units (id INTEGER PRIMARY KEY, '
                'hardware_id TEXT)')
    con.execute('CREATE TABLE drone_flights (id INTEGER PRIMARY KEY, '
                'dji_flight_id BIGINT, started_at TEXT, finished_at TEXT, '
                'raw_json TEXT, drone_unit_id INTEGER, nickname_raw TEXT)')
    for stmt in _ddl():
        con.execute(stmt)
    con.execute('INSERT INTO drone_units VALUES (1, ?)', (HW1,))
    con.execute('INSERT INTO drone_units VALUES (2, ?)', (HW2,))
    for fid, unit, start, end, mode, width, raw in FLIGHTS:
        record = {'id': fid, 'new_work_area': raw, 'mode_name': mode,
                  'manual_mode': mode != 4, 'spray_width': width,
                  'start_timestamp': ts(start), 'end_timestamp': ts(end),
                  'nickname': 'SYNTHETIC-%d' % unit}
        con.execute('INSERT INTO drone_flights (dji_flight_id, started_at, '
                    'finished_at, raw_json, drone_unit_id, nickname_raw) '
                    'VALUES (?,?,?,?,?,?)',
                    (fid, start, end, json.dumps(record), unit,
                     'SYNTHETIC-%d' % unit))
    con.commit()
    con.close()


def add_v4(db_path, fid, first_mu, last_mu, spray=False):
    """Тело V4 с кадром в секунду на весь интервал записи (окно GOOD)."""
    _fid, _unit, start, end = BY_ID[fid][:4]
    t0, t1 = ts(start), ts(end)
    n = t1 - t0
    frames = [frame((t0 + i) * 1000,
                    area=first_mu + (last_mu - first_mu) * i / float(n),
                    spray_flag=1 if spray else None,
                    flow=100 if spray else None)
              for i in range(n + 1)]
    con = store.connect(db_path)
    root = store.source_root(os.path.abspath(db_path))
    store.begin_immediate(con)
    store.upsert_source_revision(con, root, 'v4', v4_bytes(frames),
                                 flight_id=fid)
    store.refresh_flight_evidence(con, root, fid)
    con.execute('COMMIT')
    con.close()


def add_normal_v4(db_path, fid):
    add_v4(db_path, fid, 0.0, BY_ID[fid][6] / MU)


def run(*argv):
    out = io.StringIO()
    with redirect_stdout(out):
        code = tool.main(list(argv))
    return code, out.getvalue()


def sha(path):
    with open(path, 'rb') as fh:
        return hashlib.sha256(fh.read()).hexdigest()


class Base(unittest.TestCase):

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix='holdout_')
        self.db = os.path.join(self.tmp, 'instance', 'transport.db')
        build(self.db)
        self.plan_dir = os.path.join(self.tmp, 'plan')
        self.plan_path = os.path.join(self.plan_dir, 'plan.json')

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def plan(self, *extra):
        return run('plan', '--db', self.db, '--from', '2026-09-01', '--to',
                   'auto', '--out', self.plan_dir, '--quiet', *extra)

    def report(self, name='report'):
        out_dir = os.path.join(self.tmp, name)
        code, text = run('report', '--db', self.db, '--plan', self.plan_path,
                         '--out', out_dir, '--quiet')
        data = None
        path = os.path.join(out_dir, 'holdout_report.json')
        if os.path.exists(path):
            with open(path, encoding='utf-8') as fh:
                data = json.load(fh)
        return code, text, data

    def locked(self):
        with open(self.plan_path, encoding='utf-8') as fh:
            return json.load(fh)

    def capture_everything_normal(self, skip=()):
        """V4 всем отобранным записям; счётчик подтверждает RAW."""
        for fid in self.locked()['locked']['capture_ids']:
            if fid not in skip:
                add_normal_v4(self.db, fid)


class ThePlanIsLockedBeforeAnyV4(Base):

    def test_candidates_come_from_the_frozen_screen_and_nothing_else(self):
        code, _text = self.plan()
        self.assertEqual(code, tool.EXIT_OK)
        locked = self.locked()['locked']
        got = {c['flight_id']: c['scalar_source_check']
               for c in locked['candidates']}
        # C1 -- цепочка и равный скаляр; C2 -- цепочка, скаляр другой.
        self.assertEqual(got, {C1: True, C2: False})
        # Похожие, но не кандидаты: ширина заполнена / цепочки нет.
        for fid in (HIDDEN, REPEAT, LONE_NO_WIDTH):
            self.assertNotIn(fid, got)
        for c in locked['candidates']:
            self.assertEqual(c['prediction'], tool.PRED_RETAINED)

    def test_period_ends_on_the_last_complete_day(self):
        self.plan()
        locked = self.locked()['locked']
        self.assertEqual(locked['period'], {'from': '2026-09-01',
                                            'to': '2026-09-02'})
        ids = {c['flight_id'] for c in locked['controls']}
        self.assertNotIn(NEXT_DAY, ids)

    def test_each_control_lands_in_the_first_matching_stratum(self):
        self.plan()
        strata = {c['flight_id']: c['stratum']
                  for c in self.locked()['locked']['controls']}
        # HIDDEN и повторяет RAW, и идёт встык, но «короткая быстрая» -- раньше.
        self.assertEqual(strata[HIDDEN], tool.S_SHORT)
        self.assertEqual(strata[LONE_NO_WIDTH], tool.S_NO_WIDTH)
        self.assertEqual(strata[REPEAT], tool.S_EQUAL)
        self.assertEqual(strata[P2], tool.S_CONTIG)
        for fid in (A1, B1, REAL, P1, MANUAL, P4, A2, B2):
            self.assertEqual(strata[fid], tool.S_RANDOM, fid)
        self.assertNotIn(C1, strata)
        self.assertNotIn(C2, strata)

    def test_capture_list_is_exactly_the_selected_records_without_v4(self):
        add_normal_v4(self.db, REAL)
        self.plan()
        locked = self.locked()['locked']
        selected = {c['flight_id'] for c in locked['candidates']} | \
            {c['flight_id'] for c in locked['controls']}
        self.assertEqual(set(locked['capture_ids']), selected - {REAL})
        real = [c for c in locked['controls'] if c['flight_id'] == REAL][0]
        self.assertTrue(real['v4_present_at_plan'])
        with open(os.path.join(self.plan_dir, 'capture_ids.txt'),
                  encoding='ascii') as fh:
            ids = [int(x) for x in fh.read().split('\n')
                   if x and not x.startswith('#')]
        self.assertEqual(ids, locked['capture_ids'])

    def test_same_input_gives_the_same_plan_and_the_same_hash(self):
        self.plan('--random-n', '3')
        first = self.locked()
        other = os.path.join(self.tmp, 'plan_again')
        code, _ = run('plan', '--db', self.db, '--from', '2026-09-01', '--to',
                      'auto', '--out', other, '--quiet', '--random-n', '3')
        self.assertEqual(code, tool.EXIT_OK)
        with open(os.path.join(other, 'plan.json'), encoding='utf-8') as fh:
            second = json.load(fh)
        self.assertEqual(first['locked_sha256'], second['locked_sha256'])
        picked = [c for c in first['locked']['controls']
                  if c['stratum'] == tool.S_RANDOM]
        # Полы слоёв (по 6) вместе больше цели 3 -- цель всё равно не превышена.
        self.assertEqual(len(picked), 3)

    def test_a_second_plan_is_never_written_over_the_first(self):
        self.plan()
        before = sha(self.plan_path)
        code, text = self.plan('--seed', '7')
        self.assertEqual(code, tool.EXIT_USAGE)
        self.assertIn('refusing to overwrite', text)
        self.assertEqual(sha(self.plan_path), before)

    def test_plan_and_report_never_write_the_database(self):
        before = sha(self.db)
        self.plan()
        self.assertEqual(sha(self.db), before)
        self.capture_everything_normal()
        loaded = sha(self.db)
        self.report()
        self.assertEqual(sha(self.db), loaded)


class AcceptanceHasFourReachableOutcomes(Base):

    def test_pass_when_the_candidate_counter_is_flat(self):
        self.plan()
        self.capture_everything_normal(skip=(C1, HIDDEN))
        add_v4(self.db, C1, 17.15, 17.15)
        add_v4(self.db, HIDDEN, 13.5, 13.5)
        code, _text, data = self.report()
        self.assertEqual(code, tool.EXIT_OK)
        self.assertEqual(data['verdict'], 'PASS')
        c = data['candidates']
        self.assertEqual((c['scored'], c['hit'], c['miss'], c['refuted'],
                          c['not_evaluable']), (1, 1, 0, 0, 0))
        self.assertEqual(c['not_scored_scalar_mismatch'], 1)
        # Одно попадание из одного -- это НЕ 99 %: граница обязана это сказать.
        self.assertLess(c['hit_share_lower_bound_95'], 0.06)

    def test_fail_when_the_counter_confirms_the_whole_raw(self):
        # Отрицательный контроль к PASS: тот же кандидат, но счётчик вырос
        # ровно на RAW. Правило ошиблось -- и holdout обязан это назвать.
        self.plan()
        self.capture_everything_normal(skip=(HIDDEN,))
        add_v4(self.db, HIDDEN, 13.5, 13.5)
        code, _text, data = self.report()
        self.assertEqual(code, tool.EXIT_ACCEPTANCE_FAIL)
        self.assertEqual(data['verdict_reason'],
                         'CANDIDATE_REFUTED_BY_COUNTER')
        self.assertEqual(data['candidates']['refuted'], 1)
        line = [r for r in data['candidate_records']
                if r['flight_id'] == C1][0]
        self.assertEqual(line['accounting_class'], acc.NORMAL)
        self.assertEqual(line['accounting_reason'], acc.R_CANDIDATE_REFUTED)

    def test_fail_when_the_increment_is_real_but_above_one_percent(self):
        self.plan()
        self.capture_everything_normal(skip=(C1,))
        add_v4(self.db, C1, 17.15, 17.15 + 500.0 / MU)     # 5 % RAW
        code, _text, data = self.report()
        self.assertEqual(code, tool.EXIT_ACCEPTANCE_FAIL)
        self.assertEqual(data['verdict_reason'], 'HIT_SHARE_BELOW_THRESHOLD')
        self.assertEqual(data['candidates']['miss'], 1)

    def test_inconclusive_when_the_candidate_has_no_v4(self):
        self.plan()
        self.capture_everything_normal(skip=(C1,))
        code, _text, data = self.report()
        self.assertEqual(code, tool.EXIT_INCONCLUSIVE)
        self.assertEqual(data['candidates']['not_evaluable'], 1)
        # И никакого auto-zero: RAW кандидата остаётся нерешённой экспозицией.
        t = data['period_totals']
        self.assertEqual(t['class_records'][acc.PHANTOM_STRUCTURAL], 1)
        self.assertEqual(t['class_raw_m2'][acc.PHANTOM_STRUCTURAL], 10000.0)
        self.assertEqual(t['confirmed_overstatement_m2'], 0.0)

    def test_flat_counter_with_application_is_a_hit_but_not_proven(self):
        self.plan()
        self.capture_everything_normal(skip=(C1,))
        add_v4(self.db, C1, 17.15, 17.15, spray=True)
        code, _text, data = self.report()
        self.assertEqual(code, tool.EXIT_OK)
        line = [r for r in data['candidate_records']
                if r['flight_id'] == C1][0]
        self.assertEqual(line['outcome'], 'HIT')
        self.assertEqual(line['accounting_class'], acc.REVIEW)
        self.assertEqual(
            data['period_totals']['class_records'][acc.PHANTOM_PROVEN], 0)


class TheControlFindsWhatTheRuleMisses(Base):

    def test_hidden_phantom_is_reported_as_a_rule_miss(self):
        self.plan()
        self.capture_everything_normal(skip=(C1, HIDDEN))
        add_v4(self.db, C1, 17.15, 17.15)
        add_v4(self.db, HIDDEN, 13.5, 13.5)
        code, _text, data = self.report()
        k = data['controls']
        self.assertEqual(k['rule_misses'], 1)
        self.assertEqual(k['by_stratum'][tool.S_SHORT]['RULE_MISS'], 1)
        # Одиночная несистематическая находка ВИДНА, но holdout не валит.
        self.assertEqual(k['gate'], 'PASS')
        self.assertEqual(data['control_gate'], 'PASS')
        self.assertEqual(data['verdict'], 'PASS')
        self.assertEqual(code, tool.EXIT_OK)
        self.assertEqual(k['rule_miss_signatures'],
                         {'mode=4|width=Y|gap=0-1s|equal=Y': 1})
        self.assertFalse(k['systematic_miss_suspected'])
        # Честный повтор площади пропуском НЕ считается.
        repeat = [r for r in data['control_records']
                  if r['flight_id'] == REPEAT][0]
        self.assertEqual(repeat['outcome'], 'CORROBORATED')

    def test_period_quantities_are_separate_and_add_up(self):
        self.plan()
        self.capture_everything_normal(skip=(C1, HIDDEN))
        add_v4(self.db, C1, 17.15, 17.15)
        add_v4(self.db, HIDDEN, 13.5, 13.5)
        _code, _text, data = self.report()
        t = data['period_totals']
        raw_total = sum(f[6] for f in FLIGHTS if f[0] != NEXT_DAY)
        self.assertEqual(t['records'], len(FLIGHTS) - 1)
        self.assertEqual(t['raw_sum_m2'], raw_total)
        self.assertTrue(t['partition_holds'])
        self.assertEqual(t['class_records'][acc.PHANTOM_PROVEN], 2)
        self.assertEqual(t['class_raw_m2'][acc.PHANTOM_PROVEN], 19000.0)
        self.assertEqual(t['proven_validated_delta_m2'], 0.0)
        self.assertEqual(t['confirmed_overstatement_m2'], 19000.0)
        self.assertEqual(t['raw_minus_confirmed_overstatement_m2'],
                         raw_total - 19000.0)
        by_hw = data['by_aircraft']
        self.assertEqual(sorted(by_hw), [HW1, HW2])
        self.assertEqual(by_hw[HW1]['confirmed_overstatement_m2'], 19000.0)
        self.assertEqual(by_hw[HW2]['confirmed_overstatement_m2'], 0.0)
        self.assertEqual(by_hw[HW1]['nickname'], 'SYNTHETIC-1')

    def test_three_misses_of_one_signature_raise_the_systematic_flag(self):
        controls = [{'flight_id': 9000 + i, 'stratum': tool.S_EQUAL,
                     'mode_name': 4, 'width_present': True, 'gap_prev_s': 0,
                     'equal_recent_lag': 2} for i in range(3)]
        rows = [{'flight_id': c['flight_id'], 'report_day': '2026-09-02',
                 'area_status': 'COUNTER_FLAT_RAW_OVERSTATED',
                 'aggregation_eligibility': 'CERTIFIED', 'raw_area_m2': 5000.0,
                 'corrected_recorded_area_m2': 0.0,
                 'controller_delta_area_m2': 0.0, 'hardware_id': HW1}
                for c in controls]
        document = {'locked_sha256': 'x', 'locked': {
            'period': {'from': '2026-09-01', 'to': '2026-09-02'},
            'candidates': [], 'controls': controls}}
        report = tool.build_report(document, rows, {}, [])
        self.assertTrue(report['controls']['systematic_miss_suspected'])
        self.assertEqual(report['control_gate'], 'FAIL')
        self.assertEqual(report['control_gate_reason'],
                         'SYSTEMATIC_MISS_BY_SIGNATURE')
        # Три одинаковые сигнатуры валят ОБЩИЙ вердикт...
        self.assertEqual(report['verdict'], 'FAIL')
        self.assertEqual(report['verdict_reason'],
                         'SYSTEMATIC_MISS_BY_SIGNATURE')
        # ...не трогая приёмку кандидатов: их тут просто нет.
        self.assertEqual(report['candidate_verdict'], 'INCONCLUSIVE')
        report = tool.build_report(document, rows[:2], {}, [])
        self.assertFalse(report['controls']['systematic_miss_suspected'])
        self.assertEqual(report['control_gate'], 'PASS')


class TheLockCannotBeBypassed(Base):

    def test_an_edited_plan_is_refused(self):
        self.plan()
        document = self.locked()
        document['locked']['candidates'] = []
        with open(self.plan_path, 'w', encoding='utf-8') as fh:
            json.dump(document, fh)
        code, text, data = self.report()
        self.assertEqual(code, tool.EXIT_INTEGRITY)
        self.assertIn('edited after it was locked', text)
        self.assertIsNone(data)

    def test_changed_frozen_code_invalidates_the_report(self):
        # Кто-то «поправил» резолвер после плана и честно пересчитал хеш плана.
        self.plan()
        document = self.locked()
        document['locked']['frozen']['files']['dji_area/resolver.py'] = '0' * 64
        document['locked_sha256'] = tool.locked_sha(document['locked'])
        with open(self.plan_path, 'w', encoding='utf-8') as fh:
            json.dump(document, fh)
        code, text, data = self.report()
        self.assertEqual(code, tool.EXIT_INTEGRITY)
        self.assertEqual(data['verdict'], 'INVALID')
        self.assertIn('dji_area/resolver.py', text)

    def test_changed_acceptance_constants_invalidate_the_report(self):
        self.plan()
        document = self.locked()
        document['locked']['acceptance']['candidate_pass_share'] = 0.5
        document['locked_sha256'] = tool.locked_sha(document['locked'])
        with open(self.plan_path, 'w', encoding='utf-8') as fh:
            json.dump(document, fh)
        code, _text, data = self.report()
        self.assertEqual(code, tool.EXIT_INTEGRITY)
        self.assertIn('acceptance constants differ from the plan',
                      data['frozen_problems'])

    def test_report_directory_is_never_silently_mixed(self):
        self.plan()
        self.capture_everything_normal()
        self.report()
        code, text, _ = self.report()
        self.assertEqual(code, tool.EXIT_USAGE)
        self.assertIn('not empty', text)


class TheControlGateBindsTheOverallVerdict(Base):
    """Пороги те же, что и прежде; изменилось последствие (PREREG, доп. 13)."""

    def test_two_random_stratum_misses_fail_the_holdout_end_to_end(self):
        self.plan()
        # P1 и P4 лежат в случайной страте и кандидатами не являются. Плоский
        # счётчик при положительном RAW делает каждую из них пропуском правила.
        self.capture_everything_normal(skip=(C1, P1, P4))
        add_v4(self.db, C1, 17.15, 17.15)
        add_v4(self.db, P1, 9.0, 9.0)
        add_v4(self.db, P4, 11.0, 11.0)
        code, text, data = self.report()
        self.assertEqual(data['controls']['by_stratum'][tool.S_RANDOM]
                         ['RULE_MISS'], 2)
        self.assertEqual(data['control_gate'], 'FAIL')
        self.assertEqual(data['control_gate_reason'],
                         'SYSTEMATIC_MISS_IN_RANDOM_STRATUM')
        self.assertIn('threshold 2', data['control_gate_detail'])
        # Приёмка КАНДИДАТОВ не изменилась -- изменился общий вердикт.
        self.assertEqual(data['candidate_verdict'], 'PASS')
        self.assertEqual(data['verdict'], 'FAIL')
        self.assertEqual(code, tool.EXIT_ACCEPTANCE_FAIL)
        self.assertNotEqual(code, tool.EXIT_OK)
        self.assertIn('SYSTEMATIC_MISS_IN_RANDOM_STRATUM', text)

    def test_one_random_stratum_miss_leaves_the_holdout_passing(self):
        # Отрицательный контроль к проверке выше: тот же путь, одна находка.
        self.plan()
        self.capture_everything_normal(skip=(C1, P1))
        add_v4(self.db, C1, 17.15, 17.15)
        add_v4(self.db, P1, 9.0, 9.0)
        code, _text, data = self.report()
        self.assertEqual(data['controls']['by_stratum'][tool.S_RANDOM]
                         ['RULE_MISS'], 1)
        self.assertEqual(data['control_gate'], 'PASS')
        self.assertEqual(data['verdict'], 'PASS')
        self.assertEqual(code, tool.EXIT_OK)

    def test_the_gate_is_a_pure_function_of_the_two_thresholds(self):
        # Границы обоих порогов, включая случай «много разных сигнатур по две».
        self.assertEqual(tool.control_gate_of(0, {})[0], 'PASS')
        self.assertEqual(tool.control_gate_of(1, {'a': 1})[0], 'PASS')
        self.assertEqual(tool.control_gate_of(2, {})[0], 'FAIL')
        self.assertEqual(tool.control_gate_of(0, {'a': 2, 'b': 2})[0], 'PASS')
        self.assertEqual(tool.control_gate_of(0, {'a': 3})[0], 'FAIL')
        self.assertEqual(tool.control_gate_of(2, {})[1],
                         'SYSTEMATIC_MISS_IN_RANDOM_STRATUM')
        self.assertEqual(tool.control_gate_of(0, {'a': 3})[1],
                         'SYSTEMATIC_MISS_BY_SIGNATURE')
        self.assertEqual(tool.control_gate_of(1, {'a': 1})[1],
                         'NO_SYSTEMATIC_MISS')
        self.assertIsNone(tool.control_gate_of(0, {})[2])

    def test_a_candidate_failure_outranks_the_gate_in_the_reason(self):
        # Оба отказа дают FAIL; называется тот, что о самом правиле.
        self.plan()
        self.capture_everything_normal(skip=(P1, P4))
        add_v4(self.db, P1, 9.0, 9.0)
        add_v4(self.db, P4, 11.0, 11.0)
        code, _text, data = self.report()
        self.assertEqual(data['candidate_verdict'], 'FAIL')
        self.assertEqual(data['control_gate'], 'FAIL')
        self.assertEqual(data['verdict'], 'FAIL')
        self.assertEqual(data['verdict_reason'],
                         'CANDIDATE_REFUTED_BY_COUNTER')
        self.assertEqual(code, tool.EXIT_ACCEPTANCE_FAIL)


class TheToolItselfIsFrozen(Base):
    """Файл несёт scoring и вердикт, поэтому его правка -- отказ, не пометка."""

    def test_the_tool_is_in_the_frozen_set_and_in_the_plan(self):
        self.plan()
        frozen = self.locked()['locked']['frozen']['files']
        self.assertIn(tool.TOOL_FILE, frozen)
        self.assertEqual(frozen[tool.TOOL_FILE],
                         tool.lf_sha256(os.path.join(ROOT, 'tools',
                                                     'dji_area_holdout.py')))
        self.assertEqual(len(frozen), len(tool.FROZEN_FILES))

    def test_a_changed_tool_sha_refuses_the_report(self):
        self.plan()
        self.capture_everything_normal()
        document = self.locked()
        document['locked']['frozen']['files'][tool.TOOL_FILE] = '0' * 64
        document['locked_sha256'] = tool.locked_sha(document['locked'])
        with open(self.plan_path, 'w', encoding='utf-8') as fh:
            json.dump(document, fh)
        code, text, data = self.report()
        self.assertEqual(code, tool.EXIT_INTEGRITY)
        self.assertEqual(data['verdict'], 'INVALID')
        self.assertEqual(data['verdict_reason'],
                         'FROZEN_CODE_OR_CONSTANTS_CHANGED')
        self.assertIn(tool.TOOL_FILE, text)
        self.assertTrue(any(tool.TOOL_FILE in p
                            for p in data['frozen_problems']))

    def test_a_plan_of_the_previous_protocol_version_is_refused(self):
        self.plan()
        document = self.locked()
        document['locked']['protocol_version'] = 1
        document['locked_sha256'] = tool.locked_sha(document['locked'])
        with open(self.plan_path, 'w', encoding='utf-8') as fh:
            json.dump(document, fh)
        code, text, _data = self.report()
        self.assertEqual(code, tool.EXIT_INTEGRITY)
        self.assertIn('another protocol version', text)

    def test_the_fingerprint_covers_every_frozen_file(self):
        first = tool.code_fingerprint()
        self.assertEqual(first, tool.code_fingerprint())
        state = tool.frozen_state()
        self.assertEqual(sorted(state['files']), sorted(tool.FROZEN_FILES))
        # Отпечаток обязан двигаться от содержимого ЛЮБОГО замороженного файла.
        for rel in tool.FROZEN_FILES:
            spoiled = dict(state, files=dict(state['files'], **{rel: '0' * 64}))
            self.assertNotEqual(
                hashlib.sha256(tool.canonical(spoiled)).hexdigest(), first, rel)

    def test_the_fingerprint_command_prints_it_in_ascii(self):
        result = subprocess.run([sys.executable, TOOL, 'fingerprint'],
                                capture_output=True)
        self.assertEqual(result.returncode, tool.EXIT_OK, result.stderr)
        self.assertTrue(all(b < 128 for b in result.stdout))
        self.assertIn(tool.code_fingerprint().encode('ascii'), result.stdout)
        self.assertIn(b'CODE FINGERPRINT', result.stdout)


class ThePlanCanBeBuiltWhereTheCollectorLives(Base):
    """`list-db`: списочная база из дампа сборщика, хронология по нику."""

    def dump(self, name, flights):
        path = os.path.join(self.tmp, name)
        records = []
        for fid, unit, start, end, mode, width, raw in flights:
            records.append({'id': fid, 'new_work_area': raw, 'mode_name': mode,
                            'manual_mode': mode != 4, 'spray_width': width,
                            'start_timestamp': ts(start),
                            'end_timestamp': ts(end),
                            'nickname': 'SYNTHETIC-%d' % unit})
        with open(path, 'w', encoding='utf-8') as fh:
            json.dump({'kind': 'backfill', 'count': len(records),
                       'flights': records}, fh)
        return path

    def list_db(self, *dumps):
        target = os.path.join(self.tmp, 'list', 'holdout_list.db')
        argv = ['list-db', '--db', target]
        for path in dumps:
            argv += ['--list-json', path]
        code, text = run(*argv)
        return code, text, target

    def test_it_flags_the_same_candidates_as_the_hardware_keyed_database(self):
        # Чужой борт вклинивается по времени ровно между мостиком и кандидатом.
        # Хронология по нику обязана его не заметить; общая на всех -- порвала бы
        # цепочку, и C1 перестал бы быть кандидатом.
        intruder = (2099, 2, '2026-09-02 10:07:10', '2026-09-02 10:07:30', 4,
                    6.2, 700.0)
        code, _text, target = self.list_db(
            self.dump('dump.json', FLIGHTS + (intruder,)))
        self.assertEqual(code, tool.EXIT_OK)
        out = os.path.join(self.tmp, 'plan_from_list')
        code, _ = run('plan', '--db', target, '--from', '2026-09-01', '--to',
                      'auto', '--out', out, '--quiet')
        self.assertEqual(code, tool.EXIT_OK)
        with open(os.path.join(out, 'plan.json'), encoding='utf-8') as fh:
            locked = json.load(fh)['locked']
        self.assertEqual({c['flight_id']: c['scalar_source_check']
                          for c in locked['candidates']},
                         {C1: True, C2: False})
        self.assertEqual(locked['population']['flights_without_hardware'], 0)
        self.assertEqual({c['hardware_id'] for c in locked['candidates']},
                         {'NICK:SYNTHETIC-1', 'NICK:SYNTHETIC-2'})
        # Всё отобранное идёт в сбор: V4 в списочной базе не бывает.
        self.assertEqual(len(locked['capture_ids']),
                         len(locked['candidates']) + len(locked['controls']))

    def test_two_dumps_are_merged_by_flight_id(self):
        first = self.dump('a.json', FLIGHTS[:9])
        second = self.dump('b.json', FLIGHTS[6:])
        code, text, target = self.list_db(first, second)
        self.assertEqual(code, tool.EXIT_OK)
        con = sqlite3.connect(target)
        n = con.execute('SELECT COUNT(*) FROM drone_flights').fetchone()[0]
        con.close()
        self.assertEqual(n, len(FLIGHTS))
        self.assertIn('flights           : %d' % len(FLIGHTS), text)

    def test_it_never_takes_the_application_database_name(self):
        path = self.dump('dump.json', FLIGHTS)
        target = os.path.join(self.tmp, 'fresh', 'transport.db')
        code, text = run('list-db', '--db', target, '--list-json', path)
        self.assertEqual(code, tool.EXIT_USAGE)
        self.assertIn('refusing the application database name', text)
        self.assertFalse(os.path.exists(target))

    def test_it_never_overwrites_and_rejects_what_is_not_a_dump(self):
        path = self.dump('dump.json', FLIGHTS)
        code, _text, target = self.list_db(path)
        self.assertEqual(code, tool.EXIT_OK)
        before = sha(target)
        code, text, _ = self.list_db(path)
        self.assertEqual(code, tool.EXIT_USAGE)
        self.assertIn('refusing to overwrite', text)
        self.assertEqual(sha(target), before)
        bogus = os.path.join(self.tmp, 'bogus.json')
        with open(bogus, 'w', encoding='utf-8') as fh:
            json.dump({'rows': []}, fh)
        code, text = run('list-db', '--db', os.path.join(self.tmp, 'x.db'),
                         '--list-json', bogus)
        self.assertEqual(code, tool.EXIT_USAGE)
        self.assertIn('not a collector list dump', text)

    def test_a_planned_candidate_the_database_no_longer_flags_is_named(self):
        self.plan()
        # Мостик «уехал» на другой борт: цепочка A1 -> B1 -> C1 в этой базе
        # больше не существует, и экран C1 кандидатом не назовёт.
        con = sqlite3.connect(self.db)
        con.execute('UPDATE drone_flights SET drone_unit_id=2 '
                    'WHERE dji_flight_id=?', (B1,))
        con.commit()
        con.close()
        _code, text, data = self.report()
        self.assertEqual(data['candidates']['planned_but_not_flagged_now'],
                         [C1])
        self.assertIn('1 planned candidate(s) not flagged now', text)


class ExitCodesAndConsole(Base):

    def test_missing_database_is_code_2_and_no_file_appears(self):
        ghost = os.path.join(self.tmp, 'nowhere', 'transport.db')
        code, _ = run('plan', '--db', ghost, '--from', '2026-09-01', '--out',
                      os.path.join(self.tmp, 'p2'), '--quiet')
        self.assertEqual(code, tool.EXIT_NO_DATABASE)
        self.assertFalse(os.path.exists(ghost))
        self.assertFalse(os.path.exists(os.path.join(self.tmp, 'p2')))

    def test_database_without_the_evidence_tables_is_refused_by_name(self):
        bare = os.path.join(self.tmp, 'bare.db')
        con = sqlite3.connect(bare)
        con.execute('CREATE TABLE drone_flights (id INTEGER PRIMARY KEY, '
                    'dji_flight_id BIGINT, started_at TEXT)')
        con.execute("INSERT INTO drone_flights VALUES (1, 1, "
                    "'2026-09-05 10:00:00')")
        con.commit()
        con.close()
        code, text = run('plan', '--db', bare, '--from', '2026-09-01', '--out',
                         os.path.join(self.tmp, 'p3'), '--quiet')
        self.assertEqual(code, tool.EXIT_USAGE)
        self.assertIn('migrate_dji_area_evidence_001.py', text)

    def test_period_without_a_complete_day_is_refused(self):
        code, text = run('plan', '--db', self.db, '--from', '2026-09-03',
                         '--out', os.path.join(self.tmp, 'p4'), '--quiet')
        self.assertEqual(code, tool.EXIT_USAGE)
        self.assertIn('no complete report day', text)

    def test_console_output_is_ascii_only(self):
        result = subprocess.run(
            [sys.executable, TOOL, 'plan', '--db', self.db, '--from',
             '2026-09-01', '--out', self.plan_dir], capture_output=True)
        self.assertEqual(result.returncode, tool.EXIT_OK, result.stderr)
        self.assertTrue(all(b < 128 for b in result.stdout))
        self.assertIn(b'PLAN SHA256', result.stdout)


class PureHelpers(unittest.TestCase):

    def test_clopper_pearson_matches_the_closed_forms(self):
        # k == n: нижняя граница = alpha ** (1/n); k == 0: верхняя = 1 - то же.
        self.assertAlmostEqual(tool.clopper_pearson_lower(150, 150),
                               0.05 ** (1 / 150.0), places=6)
        self.assertAlmostEqual(tool.clopper_pearson_upper(0, 300),
                               1 - 0.05 ** (1 / 300.0), places=6)
        self.assertEqual(tool.clopper_pearson_lower(0, 10), 0.0)
        mid = tool.clopper_pearson_lower(95, 100)
        self.assertTrue(0.89 < mid < 0.95, mid)

    def test_allocation_respects_floor_target_and_availability(self):
        cells = {'a|AUTO': list(range(1000)), 'b|AUTO': list(range(100)),
                 'c|MANUAL': list(range(3))}
        take = tool.allocate_random(cells, 300, 6)
        self.assertEqual(sum(take.values()), 300)
        self.assertEqual(take['c|MANUAL'], 3)
        self.assertGreaterEqual(take['b|AUTO'], 6)
        self.assertGreater(take['a|AUTO'], take['b|AUTO'])
        self.assertEqual(take, tool.allocate_random(cells, 300, 6))
        small = tool.allocate_random({'a': [1, 2], 'b': [3]}, 300, 6)
        self.assertEqual(small, {'a': 2, 'b': 1})
        self.assertEqual(sum(tool.allocate_random(cells, 0, 6).values()), 0)

    def test_frozen_hash_ignores_the_line_ending_style(self):
        tmp = tempfile.mkdtemp(prefix='holdout_lf_')
        try:
            lf, crlf = os.path.join(tmp, 'lf'), os.path.join(tmp, 'crlf')
            with open(lf, 'wb') as fh:
                fh.write(b'a = 1\nb = 2\n')
            with open(crlf, 'wb') as fh:
                fh.write(b'a = 1\r\nb = 2\r\n')
            self.assertEqual(tool.lf_sha256(lf), tool.lf_sha256(crlf))
            with open(crlf, 'wb') as fh:
                fh.write(b'a = 1\r\nb = 3\r\n')
            self.assertNotEqual(tool.lf_sha256(lf), tool.lf_sha256(crlf))
        finally:
            shutil.rmtree(tmp, ignore_errors=True)

    def test_every_frozen_file_exists(self):
        for rel in tool.FROZEN_FILES + (tool.TOOL_FILE,):
            self.assertTrue(os.path.exists(os.path.join(ROOT, rel)), rel)


if __name__ == '__main__':
    unittest.main()
