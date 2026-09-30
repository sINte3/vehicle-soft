# -*- coding: utf-8 -*-
"""DJI-AREA-ACCEPTED-PROPAGATION-001: семантика принятой площади и пакетное
чтение -- без Flask. Набор идёт в CI (`.github/workflows/checks.yml`).

Что держится здесь (номера -- этап F задания):

 1-9. Каждый учётный случай даёт ту принятую площадь и тот статус, которые
      уже определены `accounting.classify` + `decisions.effective`: NORMAL,
      PHANTOM_PROVEN (полный и частичный), REVIEW, PENDING, четыре решения
      администратора и устаревание решения -- СТРОГО как в `decisions.py`.
      Каждый кейс сверяется ещё и с экраном контроля (`control_report.
      record_view`): расхождение значило бы второй способ считать.
 10.  Нет расчёта -- RAW есть, принятая пуста, статус «не рассчитано», а
      итог не считает пустое нулём и не подставляет RAW (с отрицательным
      контролем наивной подстановки).
 15.  Пакетное чтение на НАСТОЯЩЕМ DDL миграций: число SQL-запросов зависит
      от числа кусков по 400, а не от числа вылетов (с отрицательным
      контролем по-вылетного чтения).

Отчёты на базе приложения (11-14) -- `tests/test_dji_area_accepted_propagation_001.py`.

ВСЕ ДАННЫЕ СИНТЕТИЧЕСКИЕ: вылеты 930001.., 940000.., ничего настоящего.
"""

import json
import math
import os
import re
import shutil
import sqlite3
import sys
import tempfile
import unittest
from datetime import date

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if REPO_ROOT not in sys.path:
    sys.path.insert(0, REPO_ROOT)

from dji_area import accepted as acc  # noqa: E402
from dji_area import accounting  # noqa: E402
from dji_area import control_report  # noqa: E402
from dji_area import control_store  # noqa: E402
from dji_area import decisions as dec  # noqa: E402
from dji_area import resolver as rs  # noqa: E402
from dji_area import store as dji_store  # noqa: E402

JUNE = date(2026, 6, 5)
COMMENT = 'Проверено визуально в DJI'

# (flight_id, код, статус резолвера, право, RAW м2, оценка м2, кандидат,
#  флаги, решение). Решение применяется ПОСЛЕ посева; «stale» -- решение,
#  после которого расчёт переписан новой строкой с новым отпечатком.
N, P, PP, R, PD, CF, KR, NE, AA, AR, ST, SC = range(930001, 930013)
FLAT = ['APPLICATION_WITH_FLAT_COUNTER']
CASES = (
    (N, 'NORMAL', rs.RAW_CORROBORATED, rs.AGG_CERTIFIED, 10000.0, 10000.0,
     False, None, None),
    (P, 'PHANTOM_PROVEN', rs.COUNTER_FLAT_RAW_OVERSTATED, rs.AGG_CERTIFIED,
     9000.0, 0.0, True, None, None),
    (PP, 'PHANTOM_PROVEN_PARTIAL', rs.PARTIAL_RECORDED_OVERSTATEMENT,
     rs.AGG_CERTIFIED, 8000.0, 500.0, False, None, None),
    (R, 'REVIEW', rs.COUNTER_FLAT_RAW_OVERSTATED, rs.AGG_UNRESOLVED, 7000.0,
     0.0, True, FLAT, None),
    (PD, 'PENDING', rs.RAW_UNVERIFIED, rs.AGG_PROVISIONAL, 6000.0, 6000.0,
     True, None, None),
    (CF, 'CONFIRM_FULL_PHANTOM', rs.COUNTER_FLAT_RAW_OVERSTATED,
     rs.AGG_UNRESOLVED, 5000.0, 0.0, True, FLAT, dec.CONFIRM_FULL_PHANTOM),
    (KR, 'KEEP_DJI_RAW', rs.COUNTER_FLAT_RAW_OVERSTATED, rs.AGG_CERTIFIED,
     4000.0, 0.0, True, None, dec.KEEP_DJI_RAW),
    (NE, 'NEEDS_MORE_EVIDENCE', rs.COUNTER_FLAT_RAW_OVERSTATED,
     rs.AGG_UNRESOLVED, 3000.0, 0.0, True, FLAT, dec.NEEDS_MORE_EVIDENCE),
    (AA, 'ACCEPT_AUTO_ON_PROVEN', rs.COUNTER_FLAT_RAW_OVERSTATED,
     rs.AGG_CERTIFIED, 2500.0, 0.0, True, None, dec.ACCEPT_AUTO_RESULT),
    (AR, 'ACCEPT_AUTO_ON_REVIEW', rs.COUNTER_FLAT_RAW_OVERSTATED,
     rs.AGG_UNRESOLVED, 2000.0, 0.0, True, FLAT, dec.ACCEPT_AUTO_RESULT),
    (ST, 'STALE_ACCEPT_AUTO', rs.COUNTER_FLAT_RAW_OVERSTATED,
     rs.AGG_UNRESOLVED, 1500.0, 0.0, True, FLAT, dec.ACCEPT_AUTO_RESULT),
    (SC, 'STALE_FULL_PHANTOM', rs.COUNTER_FLAT_RAW_OVERSTATED,
     rs.AGG_UNRESOLVED, 1000.0, 0.0, True, FLAT, dec.CONFIRM_FULL_PHANTOM),
)
STALE = (ST, SC)

# Ожидаемое: (принято м2, исключено м2, короткий статус, открыто).
EXPECTED = {
    N: (10000.0, 0.0, acc.ST_ACCEPTED, False),
    P: (0.0, 9000.0, acc.ST_CORRECTED, False),
    PP: (500.0, 7500.0, acc.ST_CORRECTED, False),
    R: (7000.0, 0.0, acc.ST_NEEDS_DECISION, True),
    PD: (6000.0, 0.0, acc.ST_PENDING, True),
    CF: (0.0, 5000.0, acc.ST_CORRECTED, False),
    KR: (4000.0, 0.0, acc.ST_ACCEPTED, False),
    NE: (3000.0, 0.0, acc.ST_NEEDS_DECISION, True),
    AA: (0.0, 2500.0, acc.ST_CORRECTED, False),
    AR: (2000.0, 0.0, acc.ST_ACCEPTED, False),
    # «Принять автомат» против прежнего расчёта теряет силу -> снова спор.
    ST: (1500.0, 0.0, acc.ST_NEEDS_DECISION, True),
    # «Полный фантом» от автомата не зависит -> действует, с пометкой.
    SC: (0.0, 1000.0, acc.ST_CORRECTED, False),
}


def calc_row(status, eligibility, raw, corrected, candidate=False,
             flags=None, flight_id=900001, input_hash='H-1'):
    """Строка расчёта для чистых тестов -- только колонки таблицы."""
    return {
        'id': flight_id, 'flight_id': flight_id,
        'report_start_date': JUNE,
        'area_algorithm_version': 'SYNTHETIC-VERSION',
        'calculation_input_hash': input_hash,
        'raw_area_m2': raw, 'corrected_recorded_area_m2': corrected,
        'controller_delta_area_m2': corrected,
        'area_status': status, 'aggregation_eligibility': eligibility,
        'anomaly_flags_json': json.dumps(list(flags or [])),
        'structural_candidate': 1 if candidate else None,
        'scalar_source_check': 1 if candidate else None,
    }


def decision_for(row, kind, stale=False):
    return {'id': 7, 'decision_type': kind,
            'area_algorithm_version': row['area_algorithm_version'],
            'calculation_input_hash': ('OLD' if stale
                                       else row['calculation_input_hash'])}


# ─── 1-10: семантика одной записи, без Flask и базы ─────────────────────────

class PerFlightSemantics(unittest.TestCase):

    def check(self, flight_id, item):
        accepted, excluded, status, is_open = EXPECTED[flight_id]
        self.assertAlmostEqual(item['accepted_m2'], accepted, places=6)
        self.assertAlmostEqual(item['excluded_m2'], excluded, places=6)
        self.assertEqual(item['status'], status)
        self.assertEqual(item['is_open'], is_open)
        self.assertTrue(item['calculated'])

    def evaluate_case(self, spec):
        fid, _code, status, elig, raw, corr, cand, flags, kind = spec
        row = calc_row(status, elig, raw, corr, cand, flags, flight_id=fid)
        decision = decision_for(row, kind, stale=fid in STALE) if kind \
            else None
        return row, decision, acc.for_flight(raw, row, decision)

    def test_every_case_gives_the_owner_semantics(self):
        for spec in CASES:
            with self.subTest(case=spec[1]):
                _row, _decision, item = self.evaluate_case(spec)
                self.check(spec[0], item)
                # RAW вылета проходит насквозь и не переписывается.
                self.assertEqual(item['raw_m2'], spec[4])
                self.assertFalse(item['raw_mismatch'])

    def test_the_auto_classes_are_the_accounting_classes(self):
        """1-4: класс берётся у `accounting.classify`, а не выводится здесь."""
        want = {N: accounting.NORMAL, P: accounting.PHANTOM_PROVEN,
                PP: accounting.PHANTOM_PROVEN, R: accounting.REVIEW,
                PD: accounting.PHANTOM_STRUCTURAL}
        for spec in CASES:
            if spec[0] in want:
                _row, _d, item = self.evaluate_case(spec)
                self.assertEqual(item['auto_class'], want[spec[0]], spec[1])

    def test_the_state_is_strictly_decisions_effective(self):
        """9: устаревание и применение -- ровно `decisions.effective`."""
        for spec in CASES:
            row, decision, item = self.evaluate_case(spec)
            auto, auto_acc, auto_exc = acc.auto_figures(row)
            stale = dec.decision_is_stale(decision, row)
            state, accepted, excluded, applied = dec.effective(
                auto['accounting_class'], auto['raw_area_m2'], auto_acc,
                auto_exc, decision, stale=stale)
            self.assertEqual((item['state'], item['accepted_m2'],
                              item['excluded_m2'], item['decision_applied']),
                             (state, accepted, excluded, applied), spec[1])

    def test_stale_accept_lapses_and_stale_phantom_stays(self):
        by_id = {spec[0]: self.evaluate_case(spec)[2] for spec in CASES}
        self.assertTrue(by_id[ST]['decision_stale'])
        self.assertFalse(by_id[ST]['decision_applied'])
        self.assertEqual(by_id[ST]['state'], dec.S_NEEDS_DECISION)
        self.assertTrue(by_id[SC]['decision_stale'])
        self.assertTrue(by_id[SC]['decision_applied'])
        self.assertEqual(by_id[SC]['state'], dec.S_ADMIN_PHANTOM)
        # Отрицательный контроль: те же решения НЕ устаревшими действуют
        # иначе -- проверка различает два случая.
        spec = [s for s in CASES if s[0] == ST][0]
        row = calc_row(spec[2], spec[3], spec[4], spec[5], spec[6], spec[7])
        fresh = acc.for_flight(spec[4], row, decision_for(row, spec[8]))
        self.assertEqual(fresh['status'], acc.ST_ACCEPTED)

    def test_the_area_control_screen_reads_the_same_numbers(self):
        """Паритет с `control_report.record_view` -- одна связка, не две."""
        for spec in CASES:
            row, decision, item = self.evaluate_case(spec)
            view = control_report.record_view(dict(row), 'ru', decision)
            self.assertEqual(view['state'], item['state'], spec[1])
            self.assertEqual(view['accepted_m2'], item['accepted_m2'],
                             spec[1])
            self.assertEqual(view['excluded_m2'], item['excluded_m2'],
                             spec[1])
            self.assertEqual(view['is_open'], item['is_open'], spec[1])

    def test_no_calculation_is_not_accepted(self):
        """10: RAW есть, принятой нет, статус -- «не рассчитано»."""
        item = acc.for_flight(12345.0, None)
        self.assertFalse(item['calculated'])
        self.assertEqual(item['raw_m2'], 12345.0)
        self.assertIsNone(item['accepted_m2'])
        self.assertIsNone(item['excluded_m2'])
        self.assertEqual(item['status'], acc.ST_NOT_CALCULATED)
        self.assertEqual(acc.label(item, 'ru'), 'не рассчитано')
        self.assertEqual(acc.label(item, 'uz'), 'ҳисобланмаган')

    def test_the_total_never_counts_null_as_zero_or_as_raw(self):
        """10: итог группы со смесью рассчитанных и нерассчитанных."""
        spec = CASES[0]
        calculated = acc.for_flight(spec[4], calc_row(*spec[2:6]))
        missing = acc.for_flight(12345.0, None)
        totals = acc.summarize([calculated, missing])
        self.assertEqual(totals['coverage'], acc.COVERAGE_PARTIAL)
        self.assertEqual(totals['records'], 2)
        self.assertEqual(totals['not_calculated_records'], 1)
        self.assertAlmostEqual(totals['not_calculated_raw_m2'], 12345.0)
        self.assertAlmostEqual(totals['accepted_m2'], 10000.0)
        self.assertIsNone(totals['accepted_full_m2'])
        self.assertFalse(totals['complete'])
        # Отрицательный контроль: «accepted = raw, если расчёта нет» дал бы
        # 22 345, а не 10 000; «NULL = 0» дал бы полный итог 10 000 без
        # признака неполноты. Ни то ни другое не то, что считает модуль.
        naive = sum((i['accepted_m2'] if i['calculated'] else i['raw_m2'])
                    for i in (calculated, missing))
        self.assertAlmostEqual(naive, 22345.0)
        self.assertNotAlmostEqual(naive, totals['accepted_m2'])
        none = acc.summarize([missing])
        self.assertEqual(none['coverage'], acc.COVERAGE_NONE)
        self.assertEqual(none['calculated_records'], 0)
        full = acc.summarize([calculated])
        self.assertEqual(full['coverage'], acc.COVERAGE_FULL)
        self.assertAlmostEqual(full['accepted_full_m2'], 10000.0)
        empty = acc.summarize([])
        self.assertEqual(empty['coverage'], acc.COVERAGE_EMPTY)

    def test_every_decisions_state_maps_to_one_of_five_statuses(self):
        for state in dec.STATES:
            self.assertIn(acc.short_status(state), acc.SHORT_STATUSES)
        self.assertEqual(acc.short_status(None), acc.ST_NOT_CALCULATED)
        # Неизвестное состояние -- спор, а не «принято».
        self.assertEqual(acc.short_status('SOMETHING_NEW'),
                         acc.ST_NEEDS_DECISION)
        for status in acc.SHORT_STATUSES:
            ru, uz = acc.STATUS_LABELS[status]
            self.assertTrue(ru and uz)
            self.assertFalse(re.search('[A-Za-z]', uz), uz)

    def test_a_raw_disagreement_is_flagged_not_hidden(self):
        spec = CASES[0]
        item = acc.for_flight(10500.0, calc_row(*spec[2:6]))
        self.assertTrue(item['raw_mismatch'])
        self.assertAlmostEqual(item['accepted_m2'], 10000.0)
        totals = acc.summarize([item])
        self.assertEqual(totals['raw_mismatch_records'], 1)
        # Погрешность обратного умножения га -> м2 -- не расхождение.
        same = acc.for_flight((10000.0 / 10000.0) * 10000.0 + 1e-9,
                              calc_row(*spec[2:6]))
        self.assertFalse(same['raw_mismatch'])


class ColumnSufficiency(unittest.TestCase):
    """Пакетный читатель берёт только `accepted.CALC_COLUMNS`.

    [REASON]: если `accounting.classify` начнёт читать ещё одну колонку
    таблицы, узкий SELECT молча отдаст ей None. Тест записывает, какие ключи
    строки реально читаются на всей матрице статусов, и требует, чтобы каждая
    прочитанная КОЛОНКА таблицы была в списке.
    """

    class Recording(dict):
        def __init__(self, *args, **kwargs):
            dict.__init__(self, *args, **kwargs)
            self.seen = set()

        def get(self, key, default=None):
            self.seen.add(key)
            return dict.get(self, key, default)

        def __getitem__(self, key):
            self.seen.add(key)
            return dict.__getitem__(self, key)

    def read_keys(self):
        seen = set()
        for status in rs.AREA_STATUSES:
            for elig in (rs.AGG_CERTIFIED, rs.AGG_PROVISIONAL,
                         rs.AGG_UNRESOLVED, rs.AGG_EXCLUDED_OVERLAP):
                for cand in (False, True):
                    for flags in (None, FLAT,
                                  [rs.F_RETAINED_NEGLIGIBLE_FOOTPRINT]):
                        row = self.Recording(calc_row(status, elig, 5000.0,
                                                      0.0, cand, flags))
                        for kind in (None,) + dec.DECISION_TYPES:
                            decision = decision_for(row, kind) if kind \
                                else None
                            acc.for_flight(5000.0, row, decision)
                        seen |= row.seen
        return seen

    def test_every_column_read_is_selected(self):
        table_columns = set(dji_store.CALC_COLUMNS) | {'id'}
        read_columns = self.read_keys() & table_columns
        self.assertEqual(read_columns - set(acc.CALC_COLUMNS), set())
        # Отрицательный контроль: без флагов классификация читает меньше, и
        # проверка это видит -- она не тавтология.
        self.assertIn('anomaly_flags_json', read_columns)
        self.assertIn('calculation_input_hash', read_columns)



class BatchReads(unittest.TestCase):
    """15: запросов -- по кускам, а не по вылетам. DDL -- из миграций."""

    def setUp(self):
        import migrate_dji_area_evidence_001 as m_evidence
        import migrate_drone_area_control_v2_001 as m_control
        self.tmp = tempfile.mkdtemp(prefix='accepted_batch_')
        self.path = os.path.join(self.tmp, 'batch.db')
        con = sqlite3.connect(self.path)
        con.execute('CREATE TABLE users (id INTEGER PRIMARY KEY)')
        for module in (m_evidence, m_control):
            for _name, ddl in module.TABLES:
                con.execute(ddl)
            for _name, ddl in module.INDEXES:
                con.execute(ddl)
        for _name, ddl in m_control.TRIGGERS:
            con.execute(ddl)
        cols = list(dji_store.CALC_COLUMNS)
        rows = []
        for i in range(900):
            row = dict.fromkeys(cols)
            row.update(
                flight_id=940000 + i, provider_account_id='SYNTHETIC',
                area_algorithm_version=dji_store.AREA_ALGORITHM_VERSION,
                calculation_input_hash='SYNTHETIC-%d' % i,
                calculated_at='2026-09-30 00:00:00',
                start_at_utc='2026-06-05 03:00:00',
                report_timezone='Asia/Tashkent', report_start_date='2026-06-05',
                raw_area_m2=100.0, corrected_recorded_area_m2=100.0,
                area_status=rs.RAW_CORROBORATED,
                aggregation_eligibility=rs.AGG_CERTIFIED,
                anomaly_flags_json='[]')
            rows.append([row[c] for c in cols])
        con.executemany(
            'INSERT INTO dji_area_calculations (%s) VALUES (%s)'
            % (', '.join(cols), ', '.join('?' * len(cols))), rows)
        con.commit()
        con.close()

    def tearDown(self):
        shutil.rmtree(self.tmp, True)

    def statements(self, raw_by_flight):
        con = dji_store.connect(self.path, read_only=True)
        seen = []
        con.set_trace_callback(seen.append)
        try:
            result = control_store.accepted_for(con, raw_by_flight)
        finally:
            con.close()
        return result, [s for s in seen
                        if s.lstrip().upper().startswith('SELECT')]

    def test_statements_depend_on_chunks_not_on_flights(self):
        few, few_sql = self.statements({940000 + i: 100.0 for i in range(10)})
        many_ids = {940000 + i: 100.0 for i in range(900)}
        many, many_sql = self.statements(many_ids)
        self.assertEqual(len(few), 10)
        self.assertEqual(len(many), 900)
        self.assertTrue(all(i['calculated'] for i in many.values()))
        self.assertTrue(all(i['status'] == acc.ST_ACCEPTED
                            for i in many.values()))
        chunks = int(math.ceil(900 / 400.0))
        # Две таблицы на кусок (расчёты и решения) + проверки sqlite_master.
        self.assertLessEqual(len(many_sql), 2 * chunks + 4, many_sql[:6])
        self.assertLessEqual(len(few_sql), 2 + 4)
        # Отрицательный контроль: по-вылетное чтение (`current_calculation`
        # в цикле) на тех же 900 вылетах -- 900 запросов.
        con = dji_store.connect(self.path, read_only=True)
        naive = []
        con.set_trace_callback(naive.append)
        try:
            for fid in many_ids:
                control_store.current_calculation(con, fid)
        finally:
            con.close()
        self.assertGreaterEqual(len(naive), 900)
        self.assertGreater(len(naive), 10 * len(many_sql))

    def test_a_flight_without_calculation_stays_not_calculated(self):
        items = control_store.accepted_for(
            dji_store.connect(self.path, read_only=True),
            {940000: 100.0, 999999: 5000.0})
        self.assertEqual(items[940000]['status'], acc.ST_ACCEPTED)
        self.assertEqual(items[999999]['status'], acc.ST_NOT_CALCULATED)
        self.assertIsNone(items[999999]['accepted_m2'])
        self.assertEqual(items[999999]['raw_m2'], 5000.0)

    def test_the_active_decision_is_read_and_applied(self):
        """Решение, записанное штатным писателем, меняет принятую площадь."""
        spec = [c for c in CASES if c[0] == R][0]
        cols = list(dji_store.CALC_COLUMNS)
        row = dict.fromkeys(cols)
        row.update(calc_row(*spec[2:8], flight_id=R))
        row.pop('id')
        row.update(provider_account_id='SYNTHETIC',
                   area_algorithm_version=dji_store.AREA_ALGORITHM_VERSION,
                   calculated_at='2026-09-30 00:00:00',
                   start_at_utc='2026-06-05 03:00:00',
                   report_timezone='Asia/Tashkent',
                   report_start_date='2026-06-05')
        con = dji_store.connect(self.path)
        try:
            con.execute('INSERT INTO users (id) VALUES (1)')
            con.execute('INSERT INTO dji_area_calculations (%s) VALUES (%s)'
                        % (', '.join(cols), ', '.join('?' * len(cols))),
                        [row[c] for c in cols])
            before = control_store.accepted_for(con, {R: spec[4]})[R]
            control_store.record_decision(con, R, dec.CONFIRM_FULL_PHANTOM,
                                          COMMENT, True, True, None, 1,
                                          'SYNTHETIC admin')
        finally:
            con.close()
        ro = dji_store.connect(self.path, read_only=True)
        try:
            after = control_store.accepted_for(ro, {R: spec[4]})[R]
        finally:
            ro.close()
        # Отрицательный контроль: до решения -- спор по RAW.
        self.assertEqual(before['status'], acc.ST_NEEDS_DECISION)
        self.assertEqual(before['accepted_m2'], spec[4])
        self.assertEqual(after['status'], acc.ST_CORRECTED)
        self.assertEqual(after['accepted_m2'], 0.0)
        self.assertEqual(after['excluded_m2'], spec[4])
        self.assertEqual(after['decision_type'], dec.CONFIRM_FULL_PHANTOM)

    def test_the_reader_writes_nothing(self):
        before = open(self.path, 'rb').read()
        con = dji_store.connect(self.path, read_only=True)
        try:
            control_store.accepted_for(con, {940000 + i: 100.0
                                             for i in range(900)})
        finally:
            con.close()
        self.assertEqual(before, open(self.path, 'rb').read())


if __name__ == '__main__':
    unittest.main()
