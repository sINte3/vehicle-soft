# -*- coding: utf-8 -*-
"""Самопроверка tools/dji_area_retained_release_closeout.py.

DJI-AREA-RETAINED-RELEASE-CLOSEOUT-002. Нормализация отпечатка -- первая
запись в базу production, которую делает НЕ пересчёт по новому правилу, а
выравнивание происхождения. Поэтому каждая проверка здесь стоит парой:
строка, которую ворота обязаны пропустить, рядом со строкой, которую они
обязаны остановить, -- иначе «прошло» ничего не доказывает.

Фикстура -- одна машина, один день, всё через НАСТОЯЩИЙ конвейер:

  BASE -> BRIDGE -> TARGET   перенесённый скаляр с распылением на месте:
                             правило срабатывает (переход, как пять
                             сентябрьских записей production);
  LONE                       плоский счётчик и распыление без цепочки: обязан
                             остаться на проверке (как 714181711);
  BASE2 -> BRIDGE2 -> TARGET2  цепочка, доказанная счётчиком без правила,
                             форма 710001923 -> 710001925 -> 710001927;
  PLAIN                      RAW без V4;
  PLAIN2                     RAW, подтверждённый своим счётчиком.

Базовые строки пишет конвейер С ВЫКЛЮЧЕННЫМ правилом (так их записал код
production), затем приходит новая неизменяемая ревизия списка -- тот же
вылет, другое тело (страница живого захвата вместо записи форензик-импорта).

Stdlib sqlite3 + openpyxl, без Flask и сети.

Запуск:  python tools\\test_dji_area_retained_release_closeout.py
"""

import hashlib
import io
import json
import os
import re
import shutil
import sqlite3
import sys
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from datetime import date, datetime
from unittest import mock

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if REPO_ROOT not in sys.path:
    sys.path.insert(0, REPO_ROOT)

import dji_area  # noqa: E402
import migrate_drone_area_control_v2_001 as v2mig  # noqa: E402
from dji_area import accounting as acc  # noqa: E402
from dji_area import control_store as cs  # noqa: E402
from dji_area import decisions as dec  # noqa: E402
from dji_area import footprint as fpm  # noqa: E402
from dji_area import pipeline as pl  # noqa: E402
from dji_area import resolver as rs  # noqa: E402
from dji_area import store  # noqa: E402
from drone_collector import runlock  # noqa: E402
from tests.test_dji_area_core import frame, v4_bytes  # noqa: E402
from tools import dji_area_control_acceptance as acceptance  # noqa: E402
from tools import dji_area_retained_release_closeout as tool  # noqa: E402

EVIDENCE_MIGRATION = os.path.join(REPO_ROOT, 'migrate_dji_area_evidence_001.py')
ORIGINAL_ACCUMULATE = pl._accumulate

DAY = date(2026, 9, 2)
MU = 2000.0 / 3.0
NICK = 'SYNTHETIC-CLOSEOUT'
BODY_CODE = '64TBL-CLOSEOUT-NOT-REAL'

BASE, BRIDGE, TARGET = 950001, 950002, 950003
LONE = 950010
BASE2, BRIDGE2, TARGET2 = 950021, 950022, 950023
PLAIN, PLAIN2 = 950030, 950031

# (id, начало UTC, конец UTC, режим, ширина, RAW м²). День отчёта -- UTC+5.
FLIGHTS = (
    (BASE, '2026-09-02 05:00:00', '2026-09-02 05:07:00', 4, 6.0, 10000.0),
    (BRIDGE, '2026-09-02 05:07:00', '2026-09-02 05:07:20', 1, None, 500.0),
    (TARGET, '2026-09-02 05:07:20', '2026-09-02 05:08:30', 4, None, 10000.0),
    (LONE, '2026-09-02 06:00:00', '2026-09-02 06:08:00', 4, 6.0, 9000.0),
    (BASE2, '2026-09-02 07:00:00', '2026-09-02 07:07:00', 4, 6.0, 8000.0),
    (BRIDGE2, '2026-09-02 07:07:00', '2026-09-02 07:07:20', 1, None, 400.0),
    (TARGET2, '2026-09-02 07:07:20', '2026-09-02 07:08:30', 4, None, 8000.0),
    (PLAIN, '2026-09-02 08:00:00', '2026-09-02 08:06:00', 4, 6.0, 7000.0),
    (PLAIN2, '2026-09-02 09:00:00', '2026-09-02 09:06:00', 4, 6.0, 6000.0),
)
BY_ID = {row[0]: row for row in FLIGHTS}

# Время: захват источников, расчёт кодом production, поздний захват.
T0 = datetime(2026, 9, 19, 8, 0, 0)
T1 = datetime(2026, 9, 20, 8, 0, 0)
T2 = datetime(2026, 9, 25, 8, 0, 0)


def ts(text):
    return int((datetime.strptime(text, '%Y-%m-%d %H:%M:%S')
                - datetime(1970, 1, 1)).total_seconds())


def record(fid, raw=None, width='same'):
    row = BY_ID[fid]
    return {'id': fid, 'new_work_area': row[5] if raw is None else raw,
            'mode_name': row[3], 'manual_mode': row[3] != 4,
            'spray_width': row[4] if width == 'same' else width,
            'start_timestamp': ts(row[1]), 'end_timestamp': ts(row[2]),
            'nickname': NICK}


def _evidence_ddl():
    with open(EVIDENCE_MIGRATION, encoding='utf-8') as fh:
        text = fh.read()
    return re.findall(r'CREATE TABLE IF NOT EXISTS \w+ \(.*?\n    \)', text,
                      re.S)


def rule_off():
    """Конвейер без правила -- строки, какими их записал код production."""
    return mock.patch.object(rs, 'retained_negligible_footprint',
                             return_value=False)


class Fixture(object):

    def __init__(self):
        self.tmp = tempfile.mkdtemp(prefix='closeout_')
        self.db = os.path.join(self.tmp, 'instance', 'transport.db')
        os.makedirs(os.path.dirname(self.db))
        con = sqlite3.connect(self.db)
        con.execute('CREATE TABLE users (id INTEGER PRIMARY KEY, '
                    'username TEXT)')
        con.execute('CREATE TABLE drone_units (id INTEGER PRIMARY KEY, '
                    'hardware_id TEXT)')
        con.execute('CREATE TABLE drone_flights (id INTEGER PRIMARY KEY, '
                    'dji_flight_id BIGINT UNIQUE, started_at TEXT, '
                    'finished_at TEXT, raw_json TEXT, drone_unit_id INTEGER, '
                    'nickname_raw TEXT, area_ha FLOAT)')
        for stmt in _evidence_ddl():
            con.execute(stmt)
        for _name, ddl in v2mig.TABLES:
            con.execute(ddl)
        for _name, ddl in v2mig.INDEXES + v2mig.TRIGGERS:
            con.execute(ddl)
        con.execute('INSERT INTO drone_units VALUES (1, ?)', (BODY_CODE,))
        con.execute('INSERT INTO drone_units VALUES (2, ?)', (BODY_CODE,))
        for fid, start, end, _mode, _width, raw in FLIGHTS:
            con.execute(
                'INSERT INTO drone_flights (dji_flight_id, started_at, '
                'finished_at, raw_json, drone_unit_id, nickname_raw, area_ha) '
                'VALUES (?,?,?,?,?,?,?)',
                (fid, start, end, json.dumps(record(fid)), 1, NICK,
                 raw / 10000.0))
        con.commit()
        con.close()

    # ── источники ────────────────────────────────────────────────────────
    def _source(self, fid, source_type, body, now, **kw):
        con = store.connect(self.db)
        root = store.source_root(os.path.abspath(self.db))
        store.begin_immediate(con)
        store.upsert_source_revision(con, root, source_type, body,
                                     flight_id=fid, captured_at_utc=now,
                                     now=now, **kw)
        store.refresh_flight_evidence(con, root, fid, now=now)
        con.execute('COMMIT')
        con.close()

    def add_list_record(self, fid, now=T0, **changes):
        """Запись форензик-импорта: одна запись вылета."""
        body = json.dumps(record(fid, **changes), sort_keys=True).encode()
        self._source(fid, 'list', body, now,
                     schema_version='list-record-canonical-json',
                     is_evidence_import=True)

    def add_list_page(self, fid, now=T2, **changes):
        """Живой захват: страница ответа DJI, в которой есть и этот вылет."""
        other = BASE if fid != BASE else BRIDGE
        body = json.dumps({'code': 0, 'data': [record(fid, **changes),
                                                record(other)]},
                          sort_keys=True).encode()
        self._source(fid, 'list', body, now, schema_version='raw-http-body')

    def add_v4(self, fid, first_mu, last_mu, spray, now=T0):
        row = BY_ID[fid]
        t0, t1 = ts(row[1]), ts(row[2])
        n = t1 - t0
        frames = [frame((t0 + i) * 1000,
                        area=first_mu + (last_mu - first_mu) * i / float(n),
                        spray_flag=1 if spray else None,
                        flow=100 if spray else None)
                  for i in range(n + 1)]
        self._source(fid, 'v4', v4_bytes(frames), now)

    def build(self):
        """Состояние production до выпуска: строки кода без правила."""
        for fid in BY_ID:
            self.add_list_record(fid)
        self.add_v4(BASE, 0.0, 10000.0 / MU, spray=True)
        self.add_v4(BRIDGE, 0.0, 500.0 / MU, spray=True)
        self.add_v4(TARGET, 10000.0 / MU, 10000.0 / MU, spray=True)
        self.add_v4(LONE, 13.5, 13.5, spray=True)
        self.add_v4(BASE2, 0.0, 8000.0 / MU, spray=True)
        self.add_v4(BRIDGE2, 0.0, 400.0 / MU, spray=True)
        self.add_v4(TARGET2, 8000.0 / MU, 8000.0 / MU, spray=False)
        self.add_v4(PLAIN2, 0.0, 6000.0 / MU, spray=True)
        with rule_off():
            pl.recalculate(self.db, DAY, DAY, apply=True, now=T1)
        return self

    # ── чтение ───────────────────────────────────────────────────────────
    def query(self, sql, params=()):
        con = sqlite3.connect(self.db)
        con.row_factory = sqlite3.Row
        try:
            return [dict(r) for r in con.execute(sql, params)]
        finally:
            con.close()

    def current(self):
        return {r['flight_id']: r for r in self.query(
            'SELECT * FROM dji_area_calculations WHERE superseded_at IS NULL')}

    def execute(self, sql, params=()):
        con = sqlite3.connect(self.db)
        try:
            con.execute(sql, params)
            con.commit()
        finally:
            con.close()

    def digest(self):
        with open(self.db, 'rb') as fh:
            return hashlib.sha256(fh.read()).hexdigest()

    def close(self):
        shutil.rmtree(self.tmp, ignore_errors=True)


def observed_after_full_apply(fx):
    """Отчёт, который даст база после нормализации и перехода: настоящее
    применение с правилом на КОПИИ (у нормализации результат прежний)."""
    work = tempfile.mkdtemp(prefix='closeout_copy_')
    try:
        root = os.path.join(work, 'copy')
        shutil.copytree(fx.tmp, root)
        copy_db = os.path.join(root, 'instance', 'transport.db')
        pl.recalculate(copy_db, DAY, DAY, apply=True)
        con = sqlite3.connect(copy_db)
        con.row_factory = sqlite3.Row
        try:
            return acceptance.observe(acceptance.load_rows(con, DAY, DAY))[0]
        finally:
            con.close()
    finally:
        shutil.rmtree(work, ignore_errors=True)


def make_oracle(fx, path, expected_rewrites=(TARGET,), stay=(LONE,)):
    oracle = {
        'oracle': 'fixture-closeout',
        'period': [DAY.isoformat(), DAY.isoformat()],
        'rule': {'area_algorithm': dji_area.AREA_ALGORITHM_VERSION,
                 'rule_version': dji_area.RETAINED_FOOTPRINT_RULE_VERSION,
                 'footprint_to_raw_max': fpm.FOOTPRINT_TO_RAW_MAX,
                 'width_envelope_m': fpm.FOOTPRINT_WIDTH_ENVELOPE_M},
        'expected': observed_after_full_apply(fx),
        'transition': {
            'rule_flag': rs.F_RETAINED_NEGLIGIBLE_FOOTPRINT,
            'expected_rewrites': list(expected_rewrites),
            'expected_rewrites_raw_ha': sum(
                BY_ID[f][5] for f in expected_rewrites) / 10000.0,
            'must_stay_review': list(stay),
            'negative_controls': [BASE]}}
    with io.open(path, 'w', encoding='utf-8') as fh:
        json.dump(oracle, fh)
    return oracle


def quiet(_text=''):
    return None


class Base(unittest.TestCase):

    def setUp(self):
        self.fx = Fixture().build()
        self.addCleanup(self.fx.close)
        self.work = tempfile.mkdtemp(prefix='closeout_work_')
        self.addCleanup(shutil.rmtree, self.work, True)
        self.backups = os.path.join(self.work, 'backups')
        self.oracle_path = os.path.join(self.work, 'oracle.json')
        self.raw_snapshot = os.path.join(self.work, 'raw_before.json')
        self.runs = 0

    def out(self, name):
        self.runs += 1
        return os.path.join(self.work, '%s_%d' % (name, self.runs))

    def oracle(self, **kw):
        make_oracle(self.fx, self.oracle_path, **kw)
        return tool.load_oracle(self.oracle_path)

    def classify(self, **kw):
        oracle, period, transition = self.oracle(**kw)
        return tool.classify(self.fx.db, oracle, period, transition,
                             progress=quiet)

    def run_tool(self, *argv):
        out = io.StringIO()
        with redirect_stdout(out), redirect_stderr(io.StringIO()):
            code = tool.main(list(argv))
        return code, out.getvalue()

    def common(self, command, out):
        return [command, '--db', self.fx.db, '--oracle', self.oracle_path,
                '--out', out]

    def inspect(self):
        out = self.out('inspect')
        code, text = self.run_tool(*self.common('inspect', out))
        return code, text, out

    def normalize(self, *extra):
        out = self.out('normalize')
        code, text = self.run_tool(*(self.common('normalize', out)
                                     + ['--backup-dir', self.backups]
                                     + list(extra)))
        return code, text, out

    def r1(self, baseline=True):
        out = self.out('r1')
        argv = self.common('r1', out) + ['--backup-dir', self.backups,
                                         '--raw-snapshot', self.raw_snapshot]
        if baseline:
            argv += ['--baseline-root', REPO_ROOT]
        code, text = self.run_tool(*argv)
        return code, text, out

    def r2(self, r1_out):
        out = self.out('r2')
        code, text = self.run_tool(*(self.common('r2', out) + [
            '--backup-dir', self.backups, '--raw-snapshot', self.raw_snapshot,
            '--r1-verdict', os.path.join(r1_out, tool.VERDICT_FILE)]))
        return code, text, out

    def verdict(self, out):
        with io.open(os.path.join(out, tool.VERDICT_FILE),
                     encoding='utf-8') as fh:
            return json.load(fh)

    def record(self, result, fid):
        return result['records'].get(str(fid))

    def assert_stopped(self, result, needle):
        code, text = result[0], result[1]
        self.assertEqual(code, tool.EXIT_STOP, text)
        self.assertIn(needle, text)
        self.assertIn('VERDICT: STOP', text)

    def decide(self, fid, action):
        con = store.connect(self.fx.db)
        try:
            return cs.record_decision(con, fid, action, 'closeout self-test',
                                      True, True, None, None, 'tester')
        finally:
            con.close()


class TheFixtureIsTheProductionShape(Base):
    """Прежде чем проверять ворота -- что фикстура и есть случай production."""

    def test_the_rows_are_the_rows_of_the_code_without_the_rule(self):
        rows = self.fx.current()
        target = acc.classify(rows[TARGET])
        self.assertEqual((target['accounting_class'], target['reason']),
                         (acc.REVIEW, acc.R_APPLICATION_WITH_FLAT_COUNTER))
        self.assertEqual((rows[TARGET]['candidate_base_flight_id'],
                          rows[TARGET]['scalar_source_check']), (BASE, 1))
        self.assertEqual(acc.classify(rows[LONE])['reason'],
                         acc.R_APPLICATION_WITH_FLAT_COUNTER)
        self.assertIsNone(rows[LONE]['candidate_base_flight_id'])
        # Цепочка второй машины: C доказан счётчиком, база и мостик -- соседи.
        self.assertEqual(acc.classify(rows[TARGET2])['accounting_class'],
                         acc.PHANTOM_PROVEN)
        self.assertEqual(rows[TARGET2]['candidate_base_flight_id'], BASE2)
        self.assertEqual(json.loads(rows[TARGET2]['bridge_flight_ids_json']),
                         [BRIDGE2])
        self.assertEqual({r['calculated_at'] for r in rows.values()},
                         {'2026-09-20 08:00:00'})

    def test_without_drift_the_period_is_exactly_the_transition(self):
        result = self.classify()
        self.assertEqual(result['problems'], [])
        self.assertEqual(result['counts'], {
            tool.UNCHANGED: 8, tool.HASH_ONLY: 0, tool.TRANSITION: 1,
            tool.REFUSED: 0})
        self.assertEqual(result['transitions'], [TARGET])
        self.assertEqual(result['projected_pre_apply'],
                         {'unchanged': 8, 'would_write': 1})
        self.assertIn('must stay REVIEW %d: %s'
                      % (LONE, acc.R_APPLICATION_WITH_FLAT_COUNTER),
                      result['notes'])


class OwnSourceDrift(Base):
    """A. Своя новая неизменяемая ревизия списка, тот же RAW."""

    def setUp(self):
        super(OwnSourceDrift, self).setUp()
        self.fx.add_list_page(BASE2)
        self.result = self.classify()

    def test_it_is_a_safe_normalization_caused_by_its_own_source(self):
        rec = self.record(self.result, BASE2)
        self.assertEqual(rec['class'], tool.HASH_ONLY)
        self.assertEqual(rec['cause'], tool.CAUSE_OWN)
        self.assertEqual([c['source'] for c in rec['own_changes']], ['list'])
        self.assertNotEqual(rec['own_changes'][0]['at_calculation'],
                            rec['own_changes'][0]['current'])
        self.assertEqual(rec['raw_area_m2'], 8000.0)

    def test_the_counterfactual_reproduces_the_stored_fingerprint(self):
        rec = self.record(self.result, BASE2)
        self.assertNotEqual(rec['new_hash'], rec['stored_hash'])
        self.assertEqual(rec['counterfactual_hash'], rec['stored_hash'])

    def test_without_rolling_the_pointers_back_nothing_is_proven(self):
        # Отрицательный контроль к доказательству: «откат» к ТЕКУЩИМ
        # указателям. Если бы ворота пропускали строку и так, контрфакт был
        # бы декорацией.
        with mock.patch.object(tool.ProvenanceHistory, 'as_of',
                               lambda _self, fid, _moment: {}):
            result = self.classify()
        self.assertEqual(self.record(result, BASE2)['refusal'],
                         tool.R_UNEXPLAINED)
        self.assertEqual(result['counts'][tool.HASH_ONLY], 0)


class NeighbourDrift(Base):
    """B. Своих источников запись не меняла, сменился источник базы цепочки.

    Форма production: база цепочки получила новую ревизию списка, мостик не
    менялся, у цели отпечаток сдвинулся за базой."""

    def setUp(self):
        super(NeighbourDrift, self).setUp()
        self.fx.add_list_page(BASE2)

    def test_the_target_follows_its_base(self):
        rec = self.record(self.classify(), TARGET2)
        self.assertEqual(rec['class'], tool.HASH_ONLY)
        self.assertEqual(rec['cause'], tool.CAUSE_NEIGHBOUR)
        self.assertEqual(rec['own_changes'], [])
        self.assertEqual([n['flight_id'] for n in rec['neighbour_changes']],
                         [BASE2])
        self.assertEqual(rec['counterfactual_hash'], rec['stored_hash'])

    def test_the_bridge_does_not_depend_on_the_base(self):
        result = self.classify()
        self.assertIsNone(self.record(result, BRIDGE2))
        self.assertEqual(result['hash_only'], [BASE2, TARGET2])

    def test_it_passes_only_because_the_decision_is_the_same(self):
        # Тот же дрейф соседа, но у цели пришёл V4 с приростом: решение уже
        # другое, и никакая причина отпечатка его не оправдывает.
        self.fx.add_v4(TARGET2, 0.0, 8000.0 / MU, spray=True, now=T2)
        rec = self.record(self.classify(), TARGET2)
        self.assertEqual(rec['refusal'], tool.R_SEMANTIC)
        self.assertIn('area_status', rec['detail'])


class SemanticChangeStops(Base):
    """C. Отпечаток меняется вместе с решением -- отказ и остановка."""

    def setUp(self):
        super(SemanticChangeStops, self).setUp()
        self.fx.add_list_page(BASE2)
        self.fx.add_v4(PLAIN, 0.0, 7000.0 / MU, spray=True, now=T2)
        self.oracle()

    def test_the_record_is_refused_and_the_columns_are_named(self):
        rec = self.record(self.classify(), PLAIN)
        self.assertEqual(rec['refusal'], tool.R_SEMANTIC)
        for column in ('area_status', 'evidence_status', 'area_method'):
            self.assertIn(column, rec['detail'])
        self.assertNotIn('raw_area_m2', rec['detail'])

    def test_nothing_is_normalized_when_one_record_is_refused(self):
        before, digest = self.fx.current(), self.fx.digest()
        result = self.normalize()
        self.assert_stopped(result, 'refused SEMANTIC_CHANGE: 1 record(s): %d'
                            % PLAIN)
        self.assertIn('NOTHING WAS WRITTEN', result[1])
        self.assertEqual(self.fx.digest(), digest)
        self.assertEqual(self.fx.current()[BASE2]['id'], before[BASE2]['id'])
        self.assertFalse(os.path.exists(self.backups))


class RawChangeStops(Base):
    """D. Новая ревизия несёт другой RAW -- это не происхождение."""

    def setUp(self):
        super(RawChangeStops, self).setUp()
        self.fx.add_list_page(PLAIN, raw=7100.0)
        self.oracle()

    def test_raw_changed_is_refused(self):
        rec = self.record(self.classify(), PLAIN)
        self.assertEqual(rec['refusal'], tool.R_RAW_CHANGED)
        self.assertEqual(rec['detail'], {'stored': 7000.0, 'new': 7100.0})

    def test_r1_stops_before_any_write(self):
        digest = self.fx.digest()
        result = self.r1()
        self.assert_stopped(result, 'RAW_CHANGED')
        self.assertEqual(self.fx.digest(), digest)
        steps = [s['step'] for s in self.verdict(result[2])['steps']]
        self.assertNotIn('acceptance PRE-APPLY', steps)


class BillableStops(Base):
    """E. billable не пуст -- ни нормализации, ни выпуска."""

    def test_billable_on_a_drifted_record_is_refused(self):
        self.fx.add_list_page(BASE2)
        self.fx.execute('UPDATE dji_area_calculations SET billable_area_m2 = '
                        '1.0 WHERE flight_id = ? AND superseded_at IS NULL',
                        (BASE2,))
        result = self.classify()
        self.assertEqual(self.record(result, BASE2)['refusal'],
                         tool.R_BILLABLE)
        self.assertIn('billable: 1 row(s) of dji_area_calculations carry '
                      'billable_area_m2', result['problems'])

    def test_billable_anywhere_in_the_table_stops(self):
        self.fx.execute('UPDATE dji_area_calculations SET billable_area_m2 = '
                        '1.0 WHERE flight_id = ? AND superseded_at IS NULL',
                        (PLAIN2,))
        self.oracle()
        digest = self.fx.digest()
        self.assert_stopped(self.normalize(), 'billable: 1 row(s)')
        self.assertEqual(self.fx.digest(), digest)


class UnexplainedDriftStops(Base):
    """Решение то же, но изменился не источник, а вход расчёта: вылет
    перенесён на другую машину. Одни ворота семантики его бы пропустили --
    останавливает контрфакт."""

    def setUp(self):
        super(UnexplainedDriftStops, self).setUp()
        self.fx.execute('UPDATE drone_flights SET drone_unit_id = 2 WHERE '
                        'dji_flight_id = ?', (PLAIN2,))
        self.result = self.classify()

    def test_the_semantic_gate_alone_would_have_passed_it(self):
        _summary, captured = tool.dry_run(self.fx.db, (DAY, DAY))
        self.assertEqual(tool.result_diff(self.fx.current()[PLAIN2],
                                          captured[PLAIN2]['calc']), [])

    def test_the_counterfactual_refuses_it(self):
        rec = self.record(self.result, PLAIN2)
        self.assertEqual(rec['refusal'], tool.R_UNEXPLAINED)
        self.assertEqual(rec['counterfactual_hash'], rec['new_hash'])
        self.assertNotEqual(rec['counterfactual_hash'], rec['stored_hash'])
        self.assertIn('refused UNEXPLAINED_HASH_DRIFT: 1 record(s): %d'
                      % PLAIN2, self.result['problems'])


class AdminDecisions(Base):
    """Решение администратора привязано к отпечатку строки."""

    def setUp(self):
        super(AdminDecisions, self).setUp()
        self.fx.add_list_page(BASE2)

    def test_a_live_decision_on_a_drifted_record_stops(self):
        self.decide(TARGET2, dec.ACCEPT_AUTO_RESULT)
        rec = self.record(self.classify(), TARGET2)
        self.assertEqual(rec['refusal'], tool.R_DECISION)
        self.assertEqual(rec['detail']['decision_type'],
                         dec.ACCEPT_AUTO_RESULT)
        self.assertEqual((rec['detail']['live_before'],
                          rec['detail']['live_after']), (True, False))

    def test_an_already_stale_decision_does_not_stop(self):
        self.fx.execute(
            'INSERT INTO drone_area_decisions (flight_id, chain_seq, '
            'decision_type, area_algorithm_version, calculation_input_hash, '
            'is_override, comment, performed_at, decisions_version) VALUES '
            '(?,?,?,?,?,?,?,?,?)',
            (TARGET2, 1, dec.CONFIRM_FULL_PHANTOM,
             dji_area.AREA_ALGORITHM_VERSION, 'f' * 64, 1,
             'decided against an older calculation', '2026-09-21 10:00:00',
             dec.DECISIONS_VERSION))
        result = self.classify()
        self.assertEqual(self.record(result, TARGET2)['class'],
                         tool.HASH_ONLY)
        self.assertIn('decision %s on %d is already stale and stays so'
                      % (dec.CONFIRM_FULL_PHANTOM, TARGET2), result['notes'])

    def test_a_live_decision_on_a_transition_is_named(self):
        self.decide(TARGET, dec.KEEP_DJI_RAW)
        result = self.classify()
        self.assertEqual(result['transitions'], [TARGET])
        self.assertTrue(any(n.startswith('transition %d carries a live '
                                         'decision KEEP_DJI_RAW' % TARGET)
                            for n in result['notes']), result['notes'])


class FieldAttribution(Base):
    """То же применение переписывает привязку к полю -- она не должна
    сдвинуться."""

    def test_a_field_attribution_that_would_move_stops(self):
        self.fx.add_list_page(BASE2)
        self.fx.execute(
            "UPDATE dji_field_attributions SET field_attribution_tier = "
            "'TIER1_EXACT' WHERE flight_id = ? AND superseded_at IS NULL",
            (BASE2,))
        rec = self.record(self.classify(), BASE2)
        self.assertEqual(rec['refusal'], tool.R_FIELD)
        self.assertEqual(rec['detail'], ['field_attribution_tier'])


class Normalize(Base):
    """Запись: только доказанные строки, штатным путём, с проверкой после."""

    def setUp(self):
        super(Normalize, self).setUp()
        self.fx.add_list_page(BASE2)
        self.oracle()

    def test_the_proven_rows_are_rewritten_and_nothing_else(self):
        before = self.fx.current()
        area = self.fx.query('SELECT dji_flight_id, area_ha FROM '
                             'drone_flights ORDER BY dji_flight_id')
        code, text, out = self.normalize('--baseline-root', REPO_ROOT)
        self.assertEqual(code, tool.EXIT_PASS, text)
        verdict = self.verdict(out)
        self.assertEqual(verdict['normalization']['calc_writes'], {'new': 2})
        with io.open(os.path.join(out, 'normalize',
                                  'classification_before.json'),
                     encoding='utf-8') as fh:
            predicted = json.load(fh)['predicted_hash']
        after = self.fx.current()
        for fid in (BASE2, TARGET2):
            self.assertNotEqual(after[fid]['id'], before[fid]['id'])
            self.assertEqual(after[fid]['calculation_input_hash'],
                             predicted[str(fid)])
            self.assertEqual(tool.result_diff(before[fid], after[fid]), [])
            old = self.fx.query('SELECT superseded_at, supersede_reason FROM '
                                'dji_area_calculations WHERE id = ?',
                                (before[fid]['id'],))[0]
            self.assertIsNotNone(old['superseded_at'])
            self.assertEqual(old['supersede_reason'], 'INPUT_CHANGED')
        for fid in set(before) - {BASE2, TARGET2}:
            self.assertEqual(after[fid]['id'], before[fid]['id'], fid)
        self.assertEqual(self.fx.query('SELECT dji_flight_id, area_ha FROM '
                                       'drone_flights ORDER BY '
                                       'dji_flight_id'), area)
        self.assertEqual(self.fx.query(
            'SELECT COUNT(*) AS n FROM dji_area_calculations WHERE '
            'billable_area_m2 IS NOT NULL')[0]['n'], 0)

    def test_the_backup_is_the_state_before_the_write(self):
        rows_before = len(self.fx.query('SELECT id FROM '
                                        'dji_area_calculations'))
        code, text, out = self.normalize()
        self.assertEqual(code, tool.EXIT_PASS, text)
        backup = self.verdict(out)['backup']
        self.assertEqual(backup['integrity_check'], 'ok')
        self.assertTrue(backup['path'].startswith(os.path.abspath(
            self.backups)))
        con = sqlite3.connect(backup['path'])
        try:
            copied = con.execute('SELECT COUNT(*) FROM '
                                 'dji_area_calculations').fetchone()[0]
        finally:
            con.close()
        self.assertEqual(copied, rows_before)
        self.assertEqual(len(self.fx.query('SELECT id FROM '
                                           'dji_area_calculations')),
                         rows_before + 2)

    def test_the_code_production_runs_now_sees_them_unchanged(self):
        code, text, out = self.normalize('--baseline-root', REPO_ROOT)
        self.assertEqual(code, tool.EXIT_PASS, text)
        engine = self.verdict(out)['normalization']['baseline_engine']
        self.assertEqual(engine['calc_writes'], {'unchanged': 2})
        self.assertEqual(engine['flights_in_period'], 2)

    def test_a_production_code_that_disagrees_stops(self):
        fake = os.path.join(self.work, 'fake_production')
        os.makedirs(os.path.join(fake, 'tools'))
        with io.open(os.path.join(fake, 'tools', 'dji_area_recalc.py'), 'w',
                     encoding='utf-8') as fh:
            fh.write('import json, sys\n'
                     'args = sys.argv\n'
                     'n = args.count("--flight-id")\n'
                     'with open(args[args.index("--json") + 1], "w") as fh:\n'
                     '    json.dump({"flights_in_period": n,\n'
                     '               "calc_writes": {"would_write": n}}, fh)\n')
        self.assert_stopped(self.normalize('--baseline-root', fake),
                            'the production code does not see the 2 '
                            'normalized row(s) as unchanged')

    def test_after_the_normalization_the_dry_run_is_exactly_the_transition(self):
        # F. Переход снова равен ровно названному.
        self.assertEqual(self.normalize()[0], tool.EXIT_PASS)
        dry = pl.recalculate(self.fx.db, DAY, DAY, read_only=True,
                             collect_rows=True)
        self.assertEqual(dry['calc_writes'], {'unchanged': 8,
                                              'would_write': 1})
        stored = self.fx.current()
        rewrites = [line['flight_id'] for line in dry['flights']
                    if line['calculation_input_hash']
                    != stored[line['flight_id']]['calculation_input_hash']]
        self.assertEqual(rewrites, [TARGET])

    def test_a_second_normalization_writes_nothing(self):
        # I. Идемпотентность нормализации.
        self.assertEqual(self.normalize()[0], tool.EXIT_PASS)
        digest = self.fx.digest()
        code, text, out = self.normalize()
        self.assertEqual(code, tool.EXIT_PASS, text)
        self.assertEqual(self.verdict(out)['normalization']['planned'], 0)
        self.assertIn('nothing to normalize', text)
        self.assertEqual(self.fx.digest(), digest)

    def test_a_running_cycle_stops_it_before_anything(self):
        lock = runlock.RunLock(runlock.cycle_lock_path(self.fx.db),
                               purpose='dji-area-cycle')
        self.assertTrue(lock.acquire())
        self.addCleanup(lock.release)
        digest = self.fx.digest()
        self.assert_stopped(self.normalize(), 'cycle lock')
        self.assertEqual(self.fx.digest(), digest)
        self.assertFalse(os.path.exists(self.backups))

    def with_apply(self, change):
        """Подмена одного применения внутри нормализации (не сухих прогонов)."""
        original = pl.recalculate

        def recalculate(db_path, date_from, date_to, apply=False, **kw):
            if apply:
                return change(original, db_path, date_from, date_to, **kw)
            return original(db_path, date_from, date_to, apply=apply, **kw)
        return mock.patch.object(tool.pl, 'recalculate', recalculate)

    def test_a_written_row_that_differs_from_the_proof_is_caught(self):
        # Проверка ПОСЛЕ записи читает базу, а не верит плану: строка,
        # записанная не такой, какой её доказали, называется по колонке.
        original_row = pl._calc_row

        def tampered(original, db_path, date_from, date_to, **kw):
            def calc_row(*args):
                row = original_row(*args)
                row['area_confidence'] = 'TAMPERED'
                return row
            with mock.patch.object(pl, '_calc_row', calc_row):
                return original(db_path, date_from, date_to, apply=True, **kw)
        with self.with_apply(tampered):
            result = self.normalize()
        self.assert_stopped(result, 'differs from the row it replaced in '
                                    'area_confidence')

    def test_a_write_beyond_the_plan_is_caught(self):
        def everything(original, db_path, date_from, date_to, **kw):
            kw.pop('flight_ids', None)
            return original(db_path, date_from, date_to, apply=True, **kw)
        with self.with_apply(everything):
            result = self.normalize()
        self.assert_stopped(result, 'outside the plan changed: %d' % TARGET)

    def test_evidence_that_moves_during_the_write_is_caught(self):
        # Устойчивое состояние проверяется ЗАНОВО после записи, а не
        # выводится из плана: новый захват посреди окна остался бы дрейфом.
        def and_a_new_capture(original, db_path, date_from, date_to, **kw):
            done = original(db_path, date_from, date_to, apply=True, **kw)
            self.fx.add_list_page(PLAIN2, now=T2)
            return done
        with self.with_apply(and_a_new_capture):
            result = self.normalize()
        self.assert_stopped(result, 'after the normalization 1 hash-only and '
                                    '0 refused record(s) remain')

    def test_a_backup_of_another_state_is_refused(self):
        original = tool.db_signature

        def signature(path, immutable=False):
            found = original(path, immutable)
            if immutable:
                found['dji_area_calculations'][0] -= 1
            return found
        digest = self.fx.digest()
        with mock.patch.object(tool, 'db_signature', signature):
            result = self.normalize()
        self.assert_stopped(result, 'the backup does not match the live '
                                    'database')
        self.assertEqual(self.fx.digest(), digest)

    def test_no_backup_no_write(self):
        blocker = os.path.join(self.work, 'not_a_directory')
        with io.open(blocker, 'w', encoding='utf-8') as fh:
            fh.write('x')
        digest = self.fx.digest()
        code, text = self.run_tool(*(self.common(
            'normalize', self.out('normalize')) + ['--backup-dir', blocker]))
        self.assertEqual(code, tool.EXIT_STOP, text)
        self.assertIn('backup', text)
        self.assertEqual(self.fx.digest(), digest)


class ReleaseR1(Base):
    """R1 до деплоя: нормализация, оценщик, PRE-APPLY, сторож RAW."""

    def setUp(self):
        super(ReleaseR1, self).setUp()
        self.fx.add_list_page(BASE2)

    def test_r1_passes_with_exactly_the_transition(self):
        self.oracle()
        code, text, out = self.r1()
        self.assertEqual(code, tool.EXIT_PASS, text)
        verdict = self.verdict(out)
        self.assertEqual({s['step']: s['exit_code'] for s in verdict['steps']},
                         {'RAW snapshot': 0,
                          'retained-footprint evaluator': 0,
                          'recalc dry-run (acceptance)': 0,
                          'acceptance PRE-APPLY': 0, 'RAW guard': 0})
        self.assertEqual(verdict['pre_apply_calc_writes'],
                         {'unchanged': 8, 'would_write': 1})
        self.assertEqual(verdict['normalization']['planned'], 2)
        self.assertEqual(verdict['normalization']['baseline_engine'][
            'calc_writes'], {'unchanged': 2})
        with io.open(os.path.join(out, 'acceptance',
                                  'area_control_acceptance.json'),
                     encoding='utf-8') as fh:
            accepted = json.load(fh)
        self.assertEqual((accepted['verdict'], accepted['phase']),
                         ('PASS', 'pre-apply'))
        self.assertEqual(accepted['transition']['rewrites'], [TARGET])
        self.assertIn('must stay REVIEW %d: %s'
                      % (LONE, acc.R_APPLICATION_WITH_FLAT_COUNTER),
                      accepted['transition']['notes'])

    def test_a_second_r1_keeps_the_raw_snapshot_and_passes(self):
        # Снимок RAW снимается один раз -- до первой записи -- и дальше
        # только сверяется: повтор R1 не подменяет точку отсчёта.
        self.oracle()
        self.assertEqual(self.r1(baseline=False)[0], tool.EXIT_PASS)
        with io.open(self.raw_snapshot, 'rb') as fh:
            snapshot = fh.read()
        code, text, out = self.r1(baseline=False)
        self.assertEqual(code, tool.EXIT_PASS, text)
        steps = [s['step'] for s in self.verdict(out)['steps']]
        self.assertNotIn('RAW snapshot', steps)
        self.assertIn('RAW guard', steps)
        self.assertFalse(self.verdict(out)['raw_snapshot']['created_now'])
        self.assertEqual(self.verdict(out)['normalization']['planned'], 0)
        with io.open(self.raw_snapshot, 'rb') as fh:
            self.assertEqual(fh.read(), snapshot)

    def test_the_verdict_pins_the_model_and_the_oracle(self):
        from tools import dji_area_holdout as holdout
        self.oracle()
        _code, _text, out = self.r1(baseline=False)
        verdict = self.verdict(out)
        self.assertEqual(verdict['code_fingerprint'],
                         holdout.code_fingerprint())
        self.assertEqual(verdict['oracle_sha256'],
                         tool.lf_sha256(self.oracle_path))
        self.assertEqual(verdict['rule_version'],
                         dji_area.RETAINED_FOOTPRINT_RULE_VERSION)

    def test_an_unexpected_sixth_rewrite_stops(self):
        # G. Оракул не называет сработавшую запись.
        self.oracle(expected_rewrites=())
        before, digest = self.fx.current(), self.fx.digest()
        self.assert_stopped(self.r1(), 'refused UNEXPECTED_RULE_FIRING: 1 '
                                       'record(s): %d' % TARGET)
        self.assertEqual(self.fx.digest(), digest)
        self.assertEqual(self.fx.current()[BASE2]['id'], before[BASE2]['id'])

    def test_the_record_that_must_stay_in_review_is_never_a_rewrite(self):
        # H. Даже если оракул назовёт её переходом -- правило на ней не
        # срабатывает, её отпечаток не движется, и выпуск стоит.
        self.oracle(expected_rewrites=(TARGET, LONE))
        digest = self.fx.digest()
        self.assert_stopped(self.r1(), '1 expected rewrite(s) are not a '
                                       'clean transition: %d' % LONE)
        self.assertEqual(self.fx.digest(), digest)

    def test_the_rule_firing_on_the_record_that_must_stay_stops(self):
        # H, обратная сторона: правило сработало на записи, которую оракул
        # держит на проверке. Назови её оракул хоть переходом -- выпуск стоит.
        self.oracle(expected_rewrites=(TARGET,), stay=(TARGET,))
        result = tool.classify(self.fx.db, *tool.load_oracle(
            self.oracle_path), progress=quiet)
        self.assertIn('transition: %d must stay REVIEW, the dry run makes it '
                      '%s / %s' % (TARGET, acc.PHANTOM_PROVEN,
                                   acc.R_RETAINED_NEGLIGIBLE_FOOTPRINT),
                      result['problems'])

    def test_a_named_rewrite_that_does_not_fire_is_refused_by_name(self):
        # Та же ошибка оракула на записи, чей отпечаток всё же сдвинулся
        # (дрейф происхождения): переходом её не признать -- правило молчит.
        self.oracle(expected_rewrites=(TARGET, BASE2))
        result = tool.classify(self.fx.db, *tool.load_oracle(
            self.oracle_path), progress=quiet)
        self.assertEqual(self.record(result, BASE2)['refusal'],
                         tool.R_TRANSITION)
        self.assertIn('the rule does not fire',
                      self.record(result, BASE2)['detail'])


class ReleaseR2(Base):
    """R2 после деплоя: ровно переход, затем устойчивое состояние."""

    def setUp(self):
        super(ReleaseR2, self).setUp()
        self.fx.add_list_page(BASE2)
        self.oracle()
        code, text, self.r1_out = self.r1(baseline=False)
        self.assertEqual(code, tool.EXIT_PASS, text)

    def test_r2_applies_exactly_the_transition(self):
        area = self.fx.query('SELECT dji_flight_id, area_ha FROM '
                             'drone_flights ORDER BY dji_flight_id')
        code, text, out = self.r2(self.r1_out)
        self.assertEqual(code, tool.EXIT_PASS, text)
        verdict = self.verdict(out)
        self.assertEqual(verdict['apply_calc_writes'], {'new': 1})
        self.assertEqual(verdict['targeted_calc_writes'], {'unchanged': 1})
        self.assertEqual(verdict['second_calc_writes'], {'unchanged': 9})
        self.assertEqual(verdict['normalization']['planned'], 0)
        with io.open(os.path.join(out, 'post_apply',
                                  'area_control_acceptance.json'),
                     encoding='utf-8') as fh:
            accepted = json.load(fh)
        self.assertEqual((accepted['verdict'], accepted['phase']),
                         ('PASS', 'post-apply'))
        rows = self.fx.current()
        target = acc.classify(rows[TARGET])
        self.assertEqual((target['accounting_class'], target['reason']),
                         (acc.PHANTOM_PROVEN,
                          acc.R_RETAINED_NEGLIGIBLE_FOOTPRINT))
        lone = acc.classify(rows[LONE])
        self.assertEqual((lone['accounting_class'], lone['reason']),
                         (acc.REVIEW, acc.R_APPLICATION_WITH_FLAT_COUNTER))
        self.assertEqual(self.fx.query('SELECT dji_flight_id, area_ha FROM '
                                       'drone_flights ORDER BY '
                                       'dji_flight_id'), area)
        # I. Второй прогон после применения -- ничего.
        self.assertEqual(pl.recalculate(self.fx.db, DAY, DAY,
                                        read_only=True)['calc_writes'],
                         {'unchanged': 9})

    def test_the_rewrites_are_dry_run_again_by_name(self):
        # Применение, которое не записало переход, ловится повтором по
        # названным записям -- раньше, чем второй прогон всего периода.
        from tools import dji_area_recalc as recalc
        original = recalc.main

        def no_write(argv):
            if '--apply' not in argv:
                return original(argv)
            path = argv[argv.index('--json') + 1]
            with io.open(path, 'w', encoding='utf-8') as fh:
                json.dump({'flights_in_period': 1,
                           'calc_writes': {'new': 1}}, fh)
            return 0
        with mock.patch.object(tool.recalc, 'main', no_write):
            result = self.r2(self.r1_out)
        self.assert_stopped(result, 'the targeted dry run is {"would_write": '
                                    '1} over 1 flight(s), expected unchanged 1')
        steps = [s['step'] for s in self.verdict(result[2])['steps']]
        self.assertNotIn('acceptance POST-APPLY', steps)

    def test_a_second_r2_writes_nothing(self):
        self.assertEqual(self.r2(self.r1_out)[0], tool.EXIT_PASS)
        digest = self.fx.digest()
        result = self.r2(self.r1_out)
        self.assert_stopped(result, 'expected rewrite(s) are not a clean '
                                    'transition')
        self.assertEqual(self.fx.digest(), digest)

    def test_drift_after_r1_goes_through_the_same_gates(self):
        self.fx.add_list_page(PLAIN2)
        code, text, out = self.r2(self.r1_out)
        self.assertEqual(code, tool.EXIT_PASS, text)
        self.assertEqual(self.verdict(out)['normalization']['planned'], 1)

    def test_a_semantic_change_after_r1_stops_before_the_transition(self):
        self.fx.add_v4(PLAIN, 0.0, 7000.0 / MU, spray=True, now=T2)
        before = self.fx.current()
        self.assert_stopped(self.r2(self.r1_out), 'refused SEMANTIC_CHANGE')
        self.assertEqual(self.fx.current()[TARGET]['id'], before[TARGET]['id'])

    def test_it_needs_a_passed_r1_of_the_same_model_and_oracle(self):
        verdict = self.verdict(self.r1_out)
        for key, value in (('verdict', 'STOP'), ('phase', 'r2'),
                           ('code_fingerprint', '0' * 64),
                           ('oracle_sha256', '1' * 64)):
            fake = dict(verdict, **{key: value})
            fake_out = self.out('fake_r1')
            os.makedirs(fake_out)
            with io.open(os.path.join(fake_out, tool.VERDICT_FILE), 'w',
                         encoding='utf-8') as fh:
                json.dump(fake, fh)
            digest = self.fx.digest()
            self.assert_stopped(self.r2(fake_out), 'r1 verdict: %s' % key)
            self.assertEqual(self.fx.digest(), digest)
        self.assert_stopped(self.r2(os.path.join(self.work, 'absent')),
                            'cannot read the r1 verdict')


class Contract(Base):
    """Код возврата, чтение, консоль и то, чего в инструменте нет."""

    def test_exit_codes_are_the_literal_numbers_the_block_reads(self):
        self.assertEqual((tool.EXIT_PASS, tool.EXIT_USAGE,
                          tool.EXIT_NO_DATABASE, tool.EXIT_STOP), (0, 1, 2, 3))

    def test_a_missing_database_is_code_2_and_no_file_appears(self):
        self.oracle()
        ghost = os.path.join(self.work, 'nowhere', 'absent.db')
        code, _text = self.run_tool('inspect', '--db', ghost, '--oracle',
                                    self.oracle_path, '--out',
                                    self.out('inspect'))
        self.assertEqual(code, tool.EXIT_NO_DATABASE)
        self.assertFalse(os.path.exists(ghost))

    def test_arguments_and_oracles_that_are_refused(self):
        self.oracle()
        busy = self.out('busy')
        os.makedirs(busy)
        with io.open(os.path.join(busy, 'left.txt'), 'w',
                     encoding='utf-8') as fh:
            fh.write('an earlier run')
        self.assertEqual(self.run_tool(*self.common('inspect', busy))[0],
                         tool.EXIT_USAGE)
        plain = os.path.join(self.work, 'plain.json')
        with io.open(self.oracle_path, encoding='utf-8') as fh:
            doc = json.load(fh)
        for broken in (dict(doc, transition=None),
                       dict(doc, rule=dict(doc['rule'],
                                           footprint_to_raw_max=0.03))):
            with io.open(plain, 'w', encoding='utf-8') as fh:
                json.dump(broken, fh)
            code, _text = self.run_tool('inspect', '--db', self.fx.db,
                                        '--oracle', plain, '--out',
                                        self.out('inspect'))
            self.assertEqual(code, tool.EXIT_USAGE)
        self.assertEqual(self.run_tool('r2', '--db', self.fx.db)[0],
                         tool.EXIT_USAGE)

    def test_inspect_reads_only(self):
        self.fx.add_list_page(BASE2)
        self.oracle()
        digest = self.fx.digest()
        code, text, out = self.inspect()
        self.assertEqual(code, tool.EXIT_PASS, text)
        self.assertEqual(self.fx.digest(), digest)
        verdict = self.verdict(out)
        self.assertEqual(verdict['database_sha256']['before'],
                         verdict['database_sha256']['after'])
        self.assertEqual(verdict['classification']['hash_only'],
                         [BASE2, TARGET2])
        self.assertTrue(os.path.exists(os.path.join(out,
                                                    'classification.csv')))

    def test_console_output_is_ascii_only(self):
        self.fx.add_list_page(BASE2)
        self.oracle()
        for text in (self.inspect()[1], self.r1(baseline=False)[1]):
            self.assertTrue(all(ord(ch) < 128 for ch in text))

    def test_the_pipeline_is_restored_even_after_an_error(self):
        original = (pl.load_flights, pl._accumulate)
        with self.assertRaises(RuntimeError):
            with tool.instrumented_pipeline(lambda fid: {}):
                self.assertIsNot(pl.load_flights, original[0])
                raise RuntimeError('boom')
        self.assertEqual((pl.load_flights, pl._accumulate), original)

    def test_an_instrumentation_that_sees_nothing_is_an_error(self):
        # Если конвейер однажды перестанет звать `_accumulate`, снятие
        # вернуло бы пустоту -- а пустота прошла бы любые ворота.
        original_recalculate = pl._recalculate
        plain_accumulate = ORIGINAL_ACCUMULATE

        def bypass(*args, **kw):
            capture, pl._accumulate = pl._accumulate, plain_accumulate
            try:
                return original_recalculate(*args, **kw)
            finally:
                pl._accumulate = capture
        with mock.patch.object(pl, '_recalculate', bypass):
            with self.assertRaises(tool.CloseoutError):
                tool.dry_run(self.fx.db, (DAY, DAY))

    def test_every_target_is_seen_by_the_instrumentation(self):
        summary, captured = tool.dry_run(self.fx.db, (DAY, DAY))
        self.assertEqual(sorted(captured), sorted(BY_ID))
        self.assertEqual(summary['flights_in_period'], len(BY_ID))
        # Строка, какой её отдаст `insert_calculation`: все колонки, кроме
        # служебных дат, которые ставит хранилище.
        self.assertEqual(set(captured[BASE]['calc']),
                         set(store.CALC_COLUMNS)
                         - set(tool.BOOKKEEPING_COLUMNS))

    def test_the_result_is_every_stored_column_but_provenance(self):
        self.assertEqual(
            set(tool.RESULT_COLUMNS) | set(tool.PROVENANCE_COLUMNS)
            | set(tool.BOOKKEEPING_COLUMNS), set(store.CALC_COLUMNS))
        self.assertFalse(set(tool.RESULT_COLUMNS)
                         & set(tool.PROVENANCE_COLUMNS))
        for column in ('raw_area_m2', 'corrected_recorded_area_m2',
                       'area_status', 'anomaly_flags_json',
                       'bridge_flight_ids_json', 'aggregation_eligibility',
                       'billable_area_m2', 'hardware_id',
                       'application_activity', 'window_reasons_json'):
            self.assertIn(column, tool.RESULT_COLUMNS)
        self.assertEqual(set(tool.PROVENANCE_COLUMNS),
                         {'calculation_input_hash', 'v4_summary_id',
                          'list_revision_id', 'card_revision_id',
                          'route_revision_id', 'v4_revision_id'})

    def test_the_pointers_at_a_moment_follow_what_was_received(self):
        self.fx.add_list_page(BASE2)
        revisions = self.fx.query(
            "SELECT id, received_at FROM dji_source_revisions WHERE "
            "flight_id = ? AND source_type = 'list' ORDER BY id", (BASE2,))
        self.assertEqual(len(revisions), 2)
        con = store.connect(self.fx.db, read_only=True)
        try:
            history = tool.ProvenanceHistory(con, [])
            # До позднего захвата -- прежняя ревизия, после -- новая.
            self.assertEqual(history.as_of(BASE2, '2026-09-20 08:00:00')[
                'list_revision_id'], revisions[0]['id'])
            self.assertEqual(history.as_of(BASE2, '2026-09-26 00:00:00')[
                'list_revision_id'], revisions[1]['id'])
            # Вылет того же прогона -- ровно то, что записала его строка.
            same = tool.ProvenanceHistory(con, ['2026-09-20 08:00:00'])
            row = self.fx.current()[TARGET]
            self.assertEqual(same.as_of(TARGET, '2026-09-20 08:00:00'),
                             {k: row[k] for k in tool.REVISION_KEYS})
        finally:
            con.close()

    def test_no_production_flight_id_lives_in_the_tool(self):
        path = os.path.join(REPO_ROOT, 'docs',
                            'DJI_AREA_SEPTEMBER_2026_RETAINED_FOOTPRINT_'
                            'ORACLE.json')
        with io.open(path, encoding='utf-8') as fh:
            oracle = json.load(fh)
        ids = set(oracle['transition']['expected_rewrites']
                  + oracle['transition']['must_stay_review']
                  + oracle['transition']['negative_controls']
                  + oracle['production_evaluation']['final_candidates']
                  + oracle['production_evaluation']['remaining_review_b'])
        ids.update(int(k) for k in
                   oracle['production_evaluation']['named_controls'])
        ids.update((710001923, 710001925, 710001927))
        with io.open(tool.__file__, encoding='utf-8') as fh:
            source = fh.read()
        for fid in sorted(ids):
            self.assertNotIn(str(fid), source)


# ─── R3 (DJI-AREA-R3-HISTORICAL-CLOSEOUT-001) ────────────────────────────────

def evaluation_of(db):
    """Свежая оценка правила в том виде, в каком R3 читает её из файла."""
    with redirect_stdout(io.StringIO()):
        found = tool.evaluator.evaluate_rule(db)
    return json.loads(json.dumps(found, default=str))


class R3Cohorts(Base):
    """Сверка по когортам на НАСТОЯЩЕЙ оценке фикстуры.

    День фикстуры -- 2026-09-02, TARGET на нём -- кандидат правила. Граница
    когорты перед этим днём делает его «поздним», граница в этот день или
    позже -- историческим. Каждая пара тестов различается ровно одним
    фактом: записал ли правило штатный цикл.
    """

    LATER = date(2026, 9, 1)
    HISTORY = date(2026, 9, 27)

    def daily_cycle(self):
        """Ежедневный цикл на коде с правилом -- штатное применение дня."""
        pl.recalculate(self.fx.db, DAY, DAY, apply=True)

    def test_the_approved_history_passes(self):
        problems, summary = tool.r3_cohort_problems(
            evaluation_of(self.fx.db), [TARGET], self.HISTORY)
        self.assertEqual(problems, [])
        self.assertEqual(summary['historical'], [TARGET])
        self.assertEqual(summary['later_applied_by_the_cycle'], [])

    def test_a_later_candidate_the_cycle_applied_does_not_block(self):
        self.daily_cycle()
        problems, summary = tool.r3_cohort_problems(
            evaluation_of(self.fx.db), [], self.LATER)
        self.assertEqual(problems, [])
        self.assertEqual(summary['later_applied_by_the_cycle'], [TARGET])

    def test_a_later_candidate_the_cycle_did_not_apply_stops(self):
        problems, summary = tool.r3_cohort_problems(
            evaluation_of(self.fx.db), [], self.LATER)
        self.assertEqual(summary['later_not_applied'], [TARGET])
        self.assertTrue(any('not applied by the daily cycle' in p
                            for p in problems), problems)

    def test_a_later_candidate_with_raw_or_billable_touched_stops(self):
        self.daily_cycle()
        for key, value in (('raw_changed', True), ('dry_billable_m2', 1.0),
                           ('stored_reason', acc.R_APPLICATION_WITH_FLAT_COUNTER)):
            ev = evaluation_of(self.fx.db)
            for rec in ev['records']:
                if rec['flight_id'] == TARGET:
                    rec[key] = value
            problems, summary = tool.r3_cohort_problems(ev, [], self.LATER)
            self.assertEqual(summary['later_not_applied'], [TARGET], key)
            self.assertTrue(problems, key)

    def test_the_cut_day_itself_is_history(self):
        _problems, summary = tool.r3_cohort_problems(
            evaluation_of(self.fx.db), [TARGET], DAY)
        self.assertEqual(summary['historical'], [TARGET])

    def test_an_approved_record_that_stopped_firing_stops(self):
        problems, _summary = tool.r3_cohort_problems(
            evaluation_of(self.fx.db), [TARGET, LONE], self.HISTORY)
        self.assertTrue(any('no longer fire' in p and str(LONE) in p
                            for p in problems), problems)

    def test_a_historical_candidate_nobody_reviewed_stops(self):
        problems, _summary = tool.r3_cohort_problems(
            evaluation_of(self.fx.db), [], self.HISTORY)
        self.assertTrue(any('never reviewed' in p and str(TARGET) in p
                            for p in problems), problems)

    def test_an_inconsistent_evaluation_stops(self):
        ev = evaluation_of(self.fx.db)
        ev['final_candidates'] = []
        problems, _summary = tool.r3_cohort_problems(ev, [TARGET],
                                                     self.HISTORY)
        self.assertTrue(any('inconsistent' in p for p in problems), problems)


class ReleaseR3(Base):
    """R3 целиком: утверждённые исторические записи и ничего больше."""

    def setUp(self):
        super(ReleaseR3, self).setUp()
        self.approve([TARGET])

    def approve(self, approved, rewrites=(), with_list=True):
        make_oracle(self.fx, self.oracle_path, expected_rewrites=rewrites)
        with io.open(self.oracle_path, encoding='utf-8') as fh:
            doc = json.load(fh)
        if with_list:
            doc['production_evaluation'] = {'final_candidates': list(approved)}
        with io.open(self.oracle_path, 'w', encoding='utf-8') as fh:
            json.dump(doc, fh)
        self.r2_path = self.fake_r2()

    def fake_r2(self, **changes):
        """Вердикт r2 той же модели и того же оракула (сам r2 -- ReleaseR2)."""
        doc = {'phase': tool.PHASE_R2, 'verdict': 'PASS',
               'oracle_sha256': tool.lf_sha256(self.oracle_path),
               'code_fingerprint': tool.holdout.code_fingerprint()}
        doc.update(changes)
        path = self.out('r2_verdict') + '.json'
        tool.write_json(path, doc)
        return path

    def r3(self, through='2026-09-27', r2=None):
        out = self.out('r3')
        code, text = self.run_tool(*(self.common('r3', out) + [
            '--backup-dir', self.backups, '--r2-verdict', r2 or self.r2_path,
            '--historical-through', through]))
        return code, text, out

    def reason(self, row):
        return acc.classify(row)['reason']

    def test_r3_applies_exactly_the_approved_records(self):
        before = self.fx.current()
        code, text, out = self.r3()
        self.assertEqual(code, tool.EXIT_PASS, text)
        self.assertIn('CLOSEOUT R3 VERDICT: PASS', text)
        v = self.verdict(out)
        self.assertEqual(v['r3']['to_apply'], [TARGET])
        self.assertEqual(v['cohorts_before']['historical'], [TARGET])
        self.assertEqual(v['dry_calc_writes'], {'would_write': 1})
        self.assertEqual(v['apply_calc_writes'], {'new': 1})
        self.assertEqual(v['second_calc_writes'], {'unchanged': 1})
        self.assertTrue(os.path.exists(v['backup']['path']))
        after = self.fx.current()
        self.assertEqual(self.reason(after[TARGET]),
                         acc.R_RETAINED_NEGLIGIBLE_FOOTPRINT)
        self.assertEqual(after[TARGET]['raw_area_m2'],
                         before[TARGET]['raw_area_m2'])
        for fid in BY_ID:
            if fid != TARGET:
                self.assertEqual(after[fid]['calculation_input_hash'],
                                 before[fid]['calculation_input_hash'], fid)

    def test_a_second_r3_writes_nothing(self):
        self.assertEqual(self.r3()[0], tool.EXIT_PASS)
        digest = self.fx.digest()
        result = self.r3()
        self.assert_stopped(result, 'NOTHING WAS WRITTEN')
        self.assertEqual(self.fx.digest(), digest)

    def test_what_r2_applied_is_not_applied_again(self):
        # TARGET утверждён и уже записан «r2»: применять больше нечего.
        pl.recalculate(self.fx.db, DAY, DAY, apply=True)
        self.approve([TARGET], rewrites=(TARGET,))
        digest = self.fx.digest()
        self.assert_stopped(self.r3(), 'nothing is left to apply')
        self.assertEqual(self.fx.digest(), digest)
        self.assertFalse(os.path.exists(self.backups))

    def test_a_list_that_differs_from_the_evaluation_stops_before_a_write(self):
        self.approve([LONE])
        digest = self.fx.digest()
        result = self.r3()
        self.assert_stopped(result, 'NOTHING WAS WRITTEN')
        self.assertIn('never reviewed', result[1])
        self.assertEqual(self.fx.digest(), digest)

    def test_a_later_candidate_the_cycle_did_not_apply_stops(self):
        # Граница когорты до дня фикстуры: TARGET -- «поздний» и не записан.
        digest = self.fx.digest()
        result = self.r3(through='2026-09-01')
        self.assert_stopped(result, 'not applied by the daily cycle')
        self.assertEqual(self.fx.digest(), digest)

    def test_it_needs_a_passed_r2_of_the_same_model_and_oracle(self):
        digest = self.fx.digest()
        for change in ({'verdict': 'STOP'}, {'phase': tool.PHASE_R1},
                       {'oracle_sha256': '0' * 64},
                       {'code_fingerprint': '0' * 64}):
            result = self.r3(r2=self.fake_r2(**change))
            self.assert_stopped(result, 'r2 verdict')
        self.assert_stopped(self.r3(r2=os.path.join(self.work, 'absent.json')),
                            'cannot read the r2 verdict')
        self.assertEqual(self.fx.digest(), digest)
        self.assertFalse(os.path.exists(self.backups))

    def test_the_r2_rewrites_must_be_approved(self):
        self.approve([TARGET], rewrites=(LONE,))
        self.assert_stopped(self.r3(), 'not all among the approved')
        self.assertFalse(os.path.exists(self.backups))

    def test_an_oracle_without_the_approved_list_is_refused(self):
        self.approve([], with_list=False)
        self.assert_stopped(self.r3(), 'no production_evaluation')

    def test_a_running_cycle_stops_it_before_anything(self):
        lock = runlock.RunLock(runlock.cycle_lock_path(self.fx.db),
                               purpose='dji-area-cycle')
        self.assertTrue(lock.acquire())
        self.addCleanup(lock.release)
        digest = self.fx.digest()
        self.assert_stopped(self.r3(), 'cycle lock')
        self.assertEqual(self.fx.digest(), digest)
        self.assertFalse(os.path.exists(self.backups))

    def test_a_live_decision_on_an_approved_record_is_named(self):
        self.decide(TARGET, dec.CONFIRM_FULL_PHANTOM)
        code, text, out = self.r3()
        self.assertEqual(code, tool.EXIT_PASS, text)
        self.assertIn('live admin decisions on 1 record(s)', text)
        self.assertTrue(any(str(TARGET) in n and dec.CONFIRM_FULL_PHANTOM in n
                            for n in self.verdict(out)['notes']))

    def test_a_bad_cut_date_is_refused(self):
        self.assert_stopped(self.r3(through='27.09.2026'), 'STOP')


if __name__ == '__main__':
    unittest.main()
