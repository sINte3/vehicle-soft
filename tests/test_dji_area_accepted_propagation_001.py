# -*- coding: utf-8 -*-
"""DJI-AREA-ACCEPTED-PROPAGATION-001: принятая площадь -- один источник для
рабочих отчётов модуля Дроны.

Что держится здесь (номера -- этап F задания):

 1-9. Каждый учётный случай даёт ту принятую площадь и тот статус, которые
      уже определены `accounting.classify` + `decisions.effective`: NORMAL,
      PHANTOM_PROVEN (полный и частичный), REVIEW, PENDING, четыре решения
      администратора и устаревание решения -- СТРОГО как в `decisions.py`.
      Каждый кейс сверяется ещё и с экраном контроля (`control_report.
      record_view`): расхождение значило бы второй способ считать.
 10.  Нет расчёта -- RAW есть, принятая пуста, статус «не рассчитано», а
      итог не считает пустое нулём и не подставляет RAW (с отрицательным
      контролем: наивная подстановка дала бы другое число).
 11.  Один вылет даёт одну принятую площадь в списке, его Excel, сводке,
      сверке с ведомостями, календаре, расходе раствора и на экране контроля.
 12.  Excel совпадает с экраном (список и сводка).
 13.  RAW не меняется: снимок `drone_flights` и `dji_area_calculations` до и
      после обхода всех страниц и книг побайтно равен.
 14.  `billable_area_m2` пуст везде.
 15.  Пакетное чтение: число SQL-запросов зависит от числа кусков по 400, а
      не от числа вылетов (с отрицательным контролем по-вылетного чтения).

ВСЕ ДАННЫЕ СИНТЕТИЧЕСКИЕ: вылеты 930001.., борта SYNTHETIC-HW-...-NOT-REAL.
"""

import io
import json
import math
import re
import sqlite3
import unittest
from datetime import date, datetime, timedelta

from tests.harness import app, TEST_DB_PATH
from tests.test_dji_area_report_001 import Base as ReportBase, HW6

from models import (db, DjiAreaCalculation, DroneCustomer, DroneFlight,
                    DroneWork)

import drones
from dji_area import accepted as acc
from dji_area import accounting
from dji_area import control_report
from dji_area import control_store
from dji_area import decisions as dec
from dji_area import resolver as rs
from dji_area import store as dji_store

NBSP = ' '
JUNE = date(2026, 6, 5)
JULY = date(2026, 7, 10)
NOV = date(2025, 11, 15)
WINDOW_ALL = 'date_from=2025-11-01&date_to=2026-07-31'
WINDOW_JUNE = 'date_from=2026-06-01&date_to=2026-06-30'
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
JUNE_RAW = sum(c[4] for c in CASES)                       # 59 000 м2
JUNE_ACCEPTED = sum(v[0] for v in EXPECTED.values())      # 34 000 м2
JUNE_EXCLUDED = sum(v[1] for v in EXPECTED.values())      # 25 000 м2

# Июль: один рассчитанный вылет и один без расчёта -> «частично».
JULY_CALC, JULY_NOCALC = 930020, 930021
JULY_CALC_RAW, JULY_NOCALC_HA = 20000.0, 1.2345
# Ноябрь 2025: до запуска контроля площади -- расчётов нет вовсе.
NOV_FLIGHTS = ((930030, 0.5), (930031, 0.7))
LITERS = 10.0


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


# ─── 11-15: отчёты на синтетической базе ─────────────────────────────────────

class Seeded(ReportBase):
    """Июнь 2026 -- двенадцать случаев, июль -- частично, ноябрь 2025 --
    до запуска контроля площади."""

    def setUp(self):
        super(Seeded, self).setUp()
        with app.app_context():
            for n, spec in enumerate(CASES):
                fid, _code, status, elig, raw, corr, cand, flags, _k = spec
                obj = self.calc_object(fid, status, elig, raw_m2=raw,
                                       corrected_m2=corr, day=JUNE,
                                       hardware_id=HW6, minute=n * 5,
                                       flags=flags, delta_m2=corr)
                obj.structural_candidate = True if cand else None
                obj.scalar_source_check = True if cand else None
                db.session.add(obj)
                db.session.add(self.flight(fid, JUNE, n * 5, raw / 10000.0))
            db.session.add(self.calc_object(
                JULY_CALC, rs.RAW_CORROBORATED, rs.AGG_CERTIFIED,
                raw_m2=JULY_CALC_RAW, corrected_m2=JULY_CALC_RAW, day=JULY))
            db.session.add(self.flight(JULY_CALC, JULY, 0,
                                       JULY_CALC_RAW / 10000.0))
            db.session.add(self.flight(JULY_NOCALC, JULY, 30, JULY_NOCALC_HA))
            for i, (fid, area) in enumerate(NOV_FLIGHTS):
                db.session.add(self.flight(fid, NOV, i * 10, area))
            customer = DroneCustomer(name='SYNTHETIC customer')
            db.session.add(customer)
            db.session.flush()
            # Ведомость июня ровно на принятую площадь: покрытие 100 % по
            # принятой и 57.6 % по RAW -- два основания различимы.
            db.session.add(DroneWork(
                period_month='2026-06', work_date_from=JUNE,
                work_date_to=JUNE, drone_customer_id=customer.id,
                customer_raw='SYNTHETIC customer',
                area_ha=JUNE_ACCEPTED / 10000.0, amount=0,
                received_amount=0, payment_type='cash'))
            # Май: ведомость есть, вылетов нет -- «—», а не «не рассчитано».
            db.session.add(DroneWork(
                period_month='2026-05', work_date_from=date(2026, 5, 10),
                work_date_to=date(2026, 5, 10),
                drone_customer_id=customer.id,
                customer_raw='SYNTHETIC customer', area_ha=1.0, amount=0,
                received_amount=0, payment_type='cash'))
            db.session.commit()
        con = dji_store.connect(TEST_DB_PATH)
        try:
            for spec in CASES:
                if spec[8]:
                    # Подтверждение переопределения нужно решению по
                    # доказанной корректировке (KR, AA) и безвредно прочим.
                    control_store.record_decision(
                        con, spec[0], spec[8], COMMENT, True, True, None,
                        self.admin_id, 'SYNTHETIC admin')
        finally:
            con.close()
        # Пересчёт после решения: новая строка с тем же статусом и новым
        # отпечатком. Автомат не изменился, но решение принято против
        # прежнего расчёта.
        with app.app_context():
            for fid in STALE:
                spec = [s for s in CASES if s[0] == fid][0]
                for old in DjiAreaCalculation.query.filter_by(
                        flight_id=fid, superseded_at=None):
                    old.superseded_at = datetime(2026, 9, 9, 0, 0)
                obj = self.calc_object(fid, spec[2], spec[3], raw_m2=spec[4],
                                       corrected_m2=spec[5], day=JUNE,
                                       hardware_id=HW6,
                                       minute=CASES.index(spec) * 5,
                                       flags=spec[7], delta_m2=spec[5])
                obj.structural_candidate = True
                obj.scalar_source_check = True
                db.session.add(obj)
            db.session.commit()

    def flight(self, fid, day, minute, area_ha):
        return DroneFlight(
            dji_flight_id=fid, drone_unit_id=self.unit6_id,
            nickname_raw='SYNTHETIC-%d' % fid,
            started_at=datetime(day.year, day.month, day.day, 3, 0)
            + timedelta(minutes=minute),
            work_seconds=600, area_ha=area_ha, spray_liters=LITERS,
            usage_type=0, raw_json='{}')

    def get(self, path, language='ru'):
        response = self.client_as(language=language).get(path)
        self.assertEqual(response.status_code, 200, path)
        return response

    def book(self, path):
        from openpyxl import load_workbook
        return load_workbook(io.BytesIO(self.get(path).data))

    @staticmethod
    def num(text):
        text = re.sub(r'<[^>]+>', ' ', text).replace(NBSP, '').strip()
        match = re.match(r'-?[\d ]+\.\d+|-?\d+', text)
        return float(match.group(0).replace(' ', '')) if match else None

    @staticmethod
    def stat(html, label):
        match = re.search(r'<div class="vs-stat-label">%s</div>\s*'
                          r'<div class="vs-stat-value[^"]*">(.*?)</div>'
                          % re.escape(label), html, re.S)
        if not match:
            raise AssertionError('stat %r not found' % label)
        return match.group(1)

    def list_rows(self, html):
        """{flight_id: (RAW, принято-текст, статус)} по нику строки."""
        out = {}
        for row in re.findall(r'<tr>(.*?)</tr>', html, re.S):
            nick = re.search(r'SYNTHETIC-(\d+)', row)
            if not nick:
                continue
            right = re.findall(r'<td class="right"[^>]*>(.*?)</td>', row,
                               re.S)
            status = re.search(r'<span class="vs-badge vs-badge-sm[^"]*">'
                               r'(.*?)</span>', row, re.S)
            out[int(nick.group(1))] = (self.num(right[0]), right[1],
                                       status.group(1).strip())
        return out

    def snapshot(self):
        con = sqlite3.connect(TEST_DB_PATH)
        try:
            cols = [c.name for c in DjiAreaCalculation.__table__.columns]
            return (con.execute('SELECT id, dji_flight_id, area_ha, raw_json '
                                'FROM drone_flights ORDER BY id').fetchall(),
                    con.execute('SELECT %s FROM dji_area_calculations ORDER '
                                'BY id' % ', '.join(cols)).fetchall())
        finally:
            con.close()


class Reports(Seeded):

    def provider(self):
        with app.app_context():
            pairs = [(f.dji_flight_id, f.area_ha)
                     for f in DroneFlight.query.all()]
            with app.test_request_context():
                return drones._drone_accepted_by_dji(pairs)

    def test_the_provider_gives_every_case_on_the_real_tables(self):
        items = self.provider()
        for fid, (accepted, excluded, status, is_open) in EXPECTED.items():
            item = items[fid]
            self.assertAlmostEqual(item['accepted_m2'], accepted, places=4,
                                   msg=fid)
            self.assertAlmostEqual(item['excluded_m2'], excluded, places=4,
                                   msg=fid)
            self.assertEqual(item['status'], status, fid)
            self.assertEqual(item['is_open'], is_open, fid)
        self.assertEqual(items[JULY_NOCALC]['status'], acc.ST_NOT_CALCULATED)
        for fid, _area in NOV_FLIGHTS:
            self.assertEqual(items[fid]['status'], acc.ST_NOT_CALCULATED)
            self.assertIsNone(items[fid]['accepted_m2'])

    def test_the_flight_list_shows_raw_accepted_and_status(self):
        html = self.get('/drones/?' + WINDOW_ALL).get_data(as_text=True)
        rows = self.list_rows(html)
        labels = {s: acc.STATUS_LABELS[s][0] for s in acc.SHORT_STATUSES}
        for spec in CASES:
            fid = spec[0]
            raw_ha, accepted_text, status = rows[fid]
            self.assertAlmostEqual(raw_ha, spec[4] / 10000.0, places=2)
            self.assertAlmostEqual(self.num(accepted_text),
                                   EXPECTED[fid][0] / 10000.0, places=2)
            self.assertEqual(status, labels[EXPECTED[fid][2]], fid)
        raw_ha, accepted_text, status = rows[JULY_NOCALC]
        self.assertAlmostEqual(raw_ha, JULY_NOCALC_HA, places=2)
        self.assertIn('не рассчитано', accepted_text)
        self.assertIsNone(self.num(accepted_text))
        self.assertEqual(status, 'не рассчитано')

    def test_the_flights_book_equals_the_list(self):
        """12: Excel = экран, по каждому вылету; RAW-колонка прежняя."""
        html = self.get('/drones/?' + WINDOW_ALL).get_data(as_text=True)
        rows = self.list_rows(html)
        ws = self.book('/drones/flights.xlsx?' + WINDOW_ALL).worksheets[0]
        head = [c.value for c in ws[1]]
        self.assertEqual(head[5], 'Гектары')
        self.assertEqual(head[14:17], ['Принято, га', 'Исключено, га',
                                       'Статус площади (контроль площади '
                                       'DJI)'])
        seen = 0
        for values in ws.iter_rows(min_row=2, values_only=True):
            fid = values[10]
            raw_ha, accepted_text, status = rows[fid]
            self.assertAlmostEqual(values[5], raw_ha, places=2)
            expected = self.num(accepted_text)
            if expected is None:
                self.assertIsNone(values[14], fid)
                self.assertIsNone(values[15], fid)
            else:
                self.assertAlmostEqual(values[14], expected, places=2)
            self.assertEqual(values[16], status, fid)
            seen += 1
        self.assertEqual(seen, len(CASES) + 2 + len(NOV_FLIGHTS))

    def test_the_summary_and_its_book_agree(self):
        """12: сводка -- экран и книга, итог и месяцы."""
        html = self.get('/drones/summary?' + WINDOW_ALL).get_data(
            as_text=True)
        accepted_ha = (JUNE_ACCEPTED + JULY_CALC_RAW) / 10000.0
        self.assertAlmostEqual(self.num(self.stat(html, 'Принято, га')),
                               accepted_ha, places=2)
        self.assertIn('частично', self.stat(html, 'Принято, га'))
        self.assertAlmostEqual(self.num(self.stat(html, 'Исключено, га')),
                               JUNE_EXCLUDED / 10000.0, places=2)
        self.assertAlmostEqual(
            self.num(self.stat(html, 'Не рассчитано, DJI RAW га')),
            JULY_NOCALC_HA + sum(a for _f, a in NOV_FLIGHTS), places=2)
        self.assertIn('вылетов: 3', html)
        self.assertAlmostEqual(self.num(self.stat(html,
                                                  'Открыто, DJI RAW га')),
                               17500.0 / 10000.0, places=2)
        book = self.book('/drones/summary.xlsx?' + WINDOW_ALL)
        pairs = {row[0]: row[1] for row in
                 book.worksheets[0].iter_rows(min_row=2, values_only=True)}
        self.assertAlmostEqual(pairs['Принято (по рассчитанным вылетам), га'],
                               accepted_ha, places=6)
        self.assertAlmostEqual(pairs['Исключено, га'],
                               JUNE_EXCLUDED / 10000.0, places=6)
        self.assertEqual(pairs['Не рассчитано вылетов'], 3)
        self.assertAlmostEqual(pairs['Не рассчитано, DJI RAW га'],
                               JULY_NOCALC_HA + sum(a for _f, a in
                                                    NOV_FLIGHTS), places=6)
        self.assertEqual(pairs['Открыто (ожидает доказательства или '
                               'решения), вылетов'], 4)
        # Прежние строки -- на месте и с прежним смыслом (DJI RAW).
        self.assertAlmostEqual(pairs['Гектаров'], (JUNE_RAW + JULY_CALC_RAW)
                               / 10000.0 + JULY_NOCALC_HA
                               + sum(a for _f, a in NOV_FLIGHTS), places=6)
        months = {row[0]: row for row in
                  book['По месяцам'].iter_rows(min_row=2, values_only=True)}
        self.assertAlmostEqual(months['2026-06'][4], JUNE_ACCEPTED / 10000.0,
                               places=6)
        self.assertEqual(months['2026-06'][6], 0)
        self.assertEqual(months['2026-06'][9], 'рассчитано полностью')
        self.assertAlmostEqual(months['2026-07'][4], JULY_CALC_RAW / 10000.0,
                               places=6)
        self.assertEqual(months['2026-07'][6], 1)
        self.assertEqual(months['2026-07'][9], 'рассчитано частично')
        self.assertIsNone(months['2025-11'][4],
                          'до запуска контроля -- пусто, а не 0 и не RAW')
        self.assertEqual(months['2025-11'][6], 2)
        self.assertEqual(months['2025-11'][9], 'не рассчитано')

    def test_reconcile_controls_by_accepted_only_in_a_full_month(self):
        with app.app_context():
            with app.test_request_context():
                data = drones._drone_works_flights_reconcile_data()
        rows = {r['month']: r for r in data['rows']}
        june, july, nov = rows['2026-06'], rows['2026-07'], rows['2025-11']
        self.assertEqual(june['control_basis'], 'accepted')
        self.assertAlmostEqual(june['control_area'], JUNE_ACCEPTED / 10000.0)
        self.assertAlmostEqual(june['control_coverage'], 100.0, places=6)
        self.assertEqual(june['control_flag'], '')
        # Отрицательный контроль: по RAW тот же месяц -- 57.6 % и жёлтый.
        self.assertAlmostEqual(june['coverage'],
                               JUNE_ACCEPTED * 100.0 / JUNE_RAW, places=6)
        self.assertEqual(june['flag'], 'is-warning-row')
        # RAW-поля прежние: их читает «Кто не сдал ведомость».
        self.assertAlmostEqual(june['flight_area'], JUNE_RAW / 10000.0)
        self.assertEqual(july['control_basis'], 'raw')
        self.assertEqual(nov['control_basis'], 'raw')
        self.assertEqual(july['acc']['coverage'], acc.COVERAGE_PARTIAL)
        self.assertEqual(nov['acc']['coverage'], acc.COVERAGE_NONE)
        self.assertIsNone(nov['acc']['accepted_ha'])
        may = rows['2026-05']
        self.assertEqual(may['acc']['coverage'], acc.COVERAGE_EMPTY)
        html = self.get('/drones/reports/reconcile').get_data(as_text=True)
        self.assertIn('по принятой', html)
        self.assertIn('по DJI RAW', html)
        may_row = [r for r in re.findall(r'<tr[^>]*>(.*?)</tr>', html, re.S)
                   if '2026-05' in r][0]
        self.assertNotIn('не рассчитано', may_row,
                         'месяц без вылетов -- не «не рассчитано»')
        july_row = [r for r in re.findall(r'<tr[^>]*>(.*?)</tr>', html,
                                          re.S) if '2026-07' in r][0]
        self.assertIn('частично', july_row)
        nov_row = [r for r in re.findall(r'<tr[^>]*>(.*?)</tr>', html, re.S)
                   if '2025-11' in r][0]
        self.assertIn('не рассчитано', nov_row)

    def test_the_calendar_works_on_accepted_and_marks_raw(self):
        with app.app_context():
            with app.test_request_context():
                june = drones._drone_flight_calendar_data('2026-06')
                july = drones._drone_flight_calendar_data('2026-07')
                nov = drones._drone_flight_calendar_data('2025-11')
        row = [r for r in june['rows'] if r['unit_id'] == self.unit6_id][0]
        cell = [c for c in row['cells'] if c['day'] == JUNE][0]
        self.assertEqual(cell['basis'], 'accepted')
        self.assertAlmostEqual(cell['shown'], JUNE_ACCEPTED / 10000.0)
        self.assertAlmostEqual(cell['area'], JUNE_RAW / 10000.0)
        self.assertTrue(cell['open'])
        self.assertEqual(june['total']['basis'], 'accepted')
        jrow = [r for r in july['rows'] if r['unit_id'] == self.unit6_id][0]
        jcell = [c for c in jrow['cells'] if c['day'] == JULY][0]
        self.assertEqual(jcell['basis'], 'raw')
        self.assertAlmostEqual(jcell['shown'], JULY_CALC_RAW / 10000.0
                               + JULY_NOCALC_HA)
        self.assertEqual(nov['total']['basis'], 'raw')
        html = self.get('/drones/reports/calendar?month=2026-07').get_data(
            as_text=True)
        self.assertIn('%.2f*' % (JULY_CALC_RAW / 10000.0 + JULY_NOCALC_HA),
                      html)
        html = self.get('/drones/reports/calendar?month=2026-06').get_data(
            as_text=True)
        self.assertIn('%.2f?' % (JUNE_ACCEPTED / 10000.0), html)

    def test_the_spray_rate_uses_accepted_only_where_calculated(self):
        with app.app_context():
            with app.test_request_context():
                data = drones._drone_spray_usage_data(
                    drones._drone_flight_conditions(drones._drone_filters_from_args(
                        _Args({'date_from': '2025-11-01',
                               'date_to': '2026-07-31'}),
                        default_current_month=False)), [], 30)
        rows = {r['month']: r for r in data['rows']}
        june = rows['2026-06']
        liters = LITERS * len(CASES)
        # Прежний расход -- по RAW, как и был.
        self.assertAlmostEqual(june['rate'], liters / (JUNE_RAW / 10000.0))
        self.assertAlmostEqual(june['rate_accepted'],
                               liters / (JUNE_ACCEPTED / 10000.0))
        self.assertEqual(june['rate_accepted_reason'], 'ok')
        self.assertIsNone(rows['2026-07']['rate_accepted'])
        self.assertEqual(rows['2026-07']['rate_accepted_reason'], 'partial')
        self.assertIsNone(rows['2025-11']['rate_accepted'])
        self.assertEqual(rows['2025-11']['rate_accepted_reason'],
                         'not_calculated')
        self.assertIsNone(data['total']['rate_accepted'])

    def test_one_flight_one_accepted_area_everywhere(self):
        """11: вылет и месяц -- одно число во всех подключённых отчётах."""
        items = self.provider()
        # Экран контроля площади за июнь -- тот же итог принятой.
        with app.app_context():
            filters = drones._drone_area_control_filters(_Args({
                'date_from': '2026-06-01', 'date_to': '2026-06-30'}))
            with app.test_request_context():
                report = drones._drone_area_control_report(filters)
                summary = drones._drone_summary_data(
                    drones._drone_flight_conditions(
                        drones._drone_filters_from_args(_Args({
                            'date_from': '2026-06-01',
                            'date_to': '2026-06-30'}),
                            default_current_month=False)))
                reconcile = drones._drone_works_flights_reconcile_data()
                calendar = drones._drone_flight_calendar_data('2026-06')
        by_control = {i['flight_id']: i for i in report['items']}
        for fid in EXPECTED:
            self.assertEqual(by_control[fid]['accepted_m2'],
                             items[fid]['accepted_m2'], fid)
            self.assertEqual(by_control[fid]['state'], items[fid]['state'])
        june_ha = JUNE_ACCEPTED / 10000.0
        self.assertAlmostEqual(report['total']['accepted_m2'] / 10000.0,
                               june_ha, places=6)
        self.assertAlmostEqual(summary['accepted']['accepted_ha'], june_ha,
                               places=6)
        month = [r for r in reconcile['rows'] if r['month'] == '2026-06'][0]
        self.assertAlmostEqual(month['acc']['accepted_ha'], june_ha, places=6)
        self.assertAlmostEqual(calendar['accepted']['accepted_ha'], june_ha,
                               places=6)
        self.assertAlmostEqual(summary['by_machine']['rows'][0]['acc']
                               ['accepted_ha'], june_ha, places=6)
        self.assertEqual(summary['accepted']['open_records'],
                         report['total']['open_records'])

    def test_raw_is_untouched_and_billable_stays_empty(self):
        """13, 14: обход всех подключённых страниц и книг ничего не пишет."""
        before = self.snapshot()
        for path in ('/drones/?' + WINDOW_ALL,
                     '/drones/flights.xlsx?' + WINDOW_ALL,
                     '/drones/summary?' + WINDOW_ALL,
                     '/drones/summary.xlsx?' + WINDOW_ALL,
                     '/drones/reports/reconcile',
                     '/drones/reports/calendar?month=2026-06',
                     '/drones/reports/spray?' + WINDOW_ALL,
                     '/drones/reports/spray.xlsx?' + WINDOW_ALL,
                     '/drones/area-control?' + WINDOW_JUNE):
            for language in ('ru', 'uz'):
                self.get(path, language=language)
        self.assertEqual(before, self.snapshot())
        con = sqlite3.connect(TEST_DB_PATH)
        try:
            filled = con.execute('SELECT COUNT(*) FROM dji_area_calculations '
                                 'WHERE billable_area_m2 IS NOT NULL'
                                 ).fetchone()[0]
            total = con.execute('SELECT COUNT(*) FROM dji_area_calculations'
                                ).fetchone()[0]
        finally:
            con.close()
        self.assertEqual(filled, 0)
        self.assertGreater(total, len(CASES))

    def test_the_uzbek_pages_speak_cyrillic(self):
        html = self.get('/drones/?' + WINDOW_ALL, language='uz').get_data(
            as_text=True)
        self.assertIn('ҳисобланмаган', html)
        self.assertIn('қарор талаб қилинади', html)
        html = self.get('/drones/summary?' + WINDOW_ALL,
                        language='uz').get_data(as_text=True)
        self.assertIn('DJI майдони назорати:', html)
        self.assertNotIn('Area Control', html)


class BatchReads(Seeded):
    """15: запросов -- по кускам, а не по вылетам."""

    def statements(self, raw_by_flight):
        con = dji_store.connect(TEST_DB_PATH, read_only=True)
        seen = []
        con.set_trace_callback(seen.append)
        try:
            result = control_store.accepted_for(con, raw_by_flight)
        finally:
            con.close()
        return result, [s for s in seen if s.lstrip().upper()
                        .startswith('SELECT')]

    def test_statements_depend_on_chunks_not_on_flights(self):
        with app.app_context():
            db.session.add_all([self.calc_object(
                940000 + i, rs.RAW_CORROBORATED, rs.AGG_CERTIFIED,
                raw_m2=100.0, corrected_m2=100.0, minute=i % 600)
                for i in range(900)])
            db.session.commit()
        few, few_sql = self.statements({fid: 1.0 for fid in EXPECTED})
        many_ids = {940000 + i: 100.0 for i in range(900)}
        many, many_sql = self.statements(many_ids)
        self.assertEqual(len(few), len(EXPECTED))
        self.assertEqual(len(many), 900)
        self.assertTrue(all(i['calculated'] for i in many.values()))
        chunks = int(math.ceil(900 / 400.0))
        # Две таблицы на кусок (расчёты и решения) + проверки sqlite_master.
        self.assertLessEqual(len(many_sql), 2 * chunks + 4, many_sql[:6])
        self.assertLessEqual(len(few_sql), 2 + 4)
        # Отрицательный контроль: по-вылетное чтение (`current_calculation`
        # в цикле) на тех же 900 вылетах -- 900 запросов.
        con = dji_store.connect(TEST_DB_PATH, read_only=True)
        naive = []
        con.set_trace_callback(naive.append)
        try:
            for fid in many_ids:
                control_store.current_calculation(con, fid)
        finally:
            con.close()
        self.assertGreaterEqual(len(naive), 900)
        self.assertGreater(len(naive), 10 * len(many_sql))


class _Args(dict):
    """Мини-заменитель request.args для прямого вызова построителей."""

    def get(self, key, default=None, type=None):  # noqa: A002
        value = dict.get(self, key, default)
        if type is not None and value is not None:
            try:
                return type(value)
            except (TypeError, ValueError):
                return default
        return value


if __name__ == '__main__':
    unittest.main()
