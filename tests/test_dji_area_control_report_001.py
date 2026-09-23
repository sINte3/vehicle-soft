# -*- coding: utf-8 -*-
"""DJI-AREA-PRODUCTIONIZATION-001: отчёт контроля площади DJI (без Flask).

Продуктовая семантика, которую легко сломать и трудно заметить:

* PHANTOM_PROVEN исправляется на ПРОВЕРЕННЫЙ ПРИРОСТ счётчика, а не на ноль:
  частичная запись сохраняет свой малый настоящий прирост;
* PHANTOM_STRUCTURAL и REVIEW остаются по RAW -- автоматического вычитания нет;
* мостик B не вычитается никогда, даже в цепочке доказанного фантома;
* RAW не меняется: сумма RAW отчёта равна сумме RAW строк;
* формулы периода сходятся: RAW - исключено = после корректировок, а разрез по
  дронам в сумме даёт итог;
* «после корректировок» не называется точной или финальной площадью, пока
  есть нерешённые записи;
* причина -- слова для человека, а не enum резолвера; узбекский -- кириллицей;
* книга: пустая ячейка вместо NULL, рабочие ссылки DJI, четыре листа.

Stdlib + openpyxl. Приложение не импортируется.
"""

import io
import json
import os
import re
import sys
import unittest
from datetime import date

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if REPO_ROOT not in sys.path:
    sys.path.insert(0, REPO_ROOT)

from dji_area import accounting as acc  # noqa: E402
from dji_area import control_report as cr  # noqa: E402
from dji_area import resolver as rs  # noqa: E402

DAY = date(2026, 9, 2)
A, B, C = 980001, 980002, 980003
PARTIAL_C = 980013
PENDING_C = 980023
REVIEW_C = 980033
RULE_MISS = 980040
NO_V4_C = 980053


def row(flight_id, status, eligibility, raw, corrected=None, delta=None,
        drone='DRONE-1', candidate=False, match=None, base=None, bridges=(),
        flags=(), v4=True):
    return {
        'flight_id': flight_id, 'report_start_date': DAY,
        'machine_key': drone, 'machine_label': drone,
        'area_status': status, 'aggregation_eligibility': eligibility,
        'raw_area_m2': raw, 'corrected_recorded_area_m2': corrected,
        'controller_delta_area_m2': delta,
        'structural_candidate': candidate, 'scalar_source_check': match,
        'candidate_base_flight_id': base,
        'bridge_flight_ids_json': json.dumps(list(bridges)),
        'anomaly_flags_json': json.dumps(list(flags)),
        'v4_revision_id': 1 if v4 else None,
        'structural_rule_version': 'structural-retained-screen-frozen-1',
        'area_algorithm_version': 'impl-test',
    }


def fixture():
    return [
        # Цепочка A -> B -> C: база и мостик обычные, цель -- доказанный фантом.
        row(A, rs.RAW_CORROBORATED, rs.AGG_CERTIFIED, 9000.0, 9000.0, 9000.0),
        row(B, rs.RAW_CORROBORATED, rs.AGG_CERTIFIED, 600.0, 600.0, 600.0),
        row(C, rs.COUNTER_FLAT_RAW_OVERSTATED, rs.AGG_CERTIFIED, 9000.0, 0.0,
            0.0, candidate=True, match=True, base=A, bridges=[B]),
        # Частичная: малый настоящий прирост сохраняется.
        row(PARTIAL_C, rs.PARTIAL_RECORDED_OVERSTATEMENT, rs.AGG_CERTIFIED,
            8000.0, 6.6667, 6.6667, drone='DRONE-2', candidate=True,
            match=True, base=980011, bridges=[980012]),
        # Кандидат без V4: ждёт доказательства, RAW остаётся.
        row(PENDING_C, rs.RAW_UNVERIFIED, rs.AGG_PROVISIONAL, 7000.0, 7000.0,
            drone='DRONE-2', candidate=True, match=True, base=980021,
            bridges=[980022], v4=False),
        # Плоский счётчик при наблюдённом применении: человеку.
        row(REVIEW_C, rs.COUNTER_FLAT_RAW_OVERSTATED, rs.AGG_UNRESOLVED,
            5000.0, None, 0.0, drone='DRONE-3', candidate=True, match=True,
            base=980031, bridges=[980032],
            flags=['APPLICATION_WITH_FLAT_COUNTER']),
        # Пропуск правила, пойманный контрольной V4-выборкой.
        row(RULE_MISS, rs.COUNTER_FLAT_RAW_OVERSTATED, rs.AGG_CERTIFIED,
            5840.0, 0.0, 0.0, drone='DRONE-3'),
        # Кандидат, у которого DJI сам не хранит V4.
        row(NO_V4_C, rs.RAW_UNVERIFIED, rs.AGG_PROVISIONAL, 4000.0, 4000.0,
            drone='DRONE-1', candidate=True, match=True, base=980051,
            bridges=[980052], flags=['NO_V4_AT_SOURCE'], v4=False),
    ]


def by_id(report):
    return {r['flight_id']: r for r in report['register']}


class RecordSemantics(unittest.TestCase):

    def setUp(self):
        self.report = cr.build(fixture(), 'ru')
        self.rows = by_id(self.report)

    def test_a_proven_phantom_is_corrected_to_the_validated_delta(self):
        item = self.rows[C]
        self.assertEqual(item['accounting_class'], acc.PHANTOM_PROVEN)
        self.assertEqual(item['raw_m2'], 9000.0)
        self.assertEqual(item['accepted_m2'], 0.0)
        self.assertEqual(item['excluded_m2'], 9000.0)

    def test_a_partial_keeps_its_real_growth_and_is_not_zeroed(self):
        item = self.rows[PARTIAL_C]
        self.assertEqual(item['accounting_class'], acc.PHANTOM_PROVEN)
        self.assertAlmostEqual(item['accepted_m2'], 6.6667)
        self.assertAlmostEqual(item['excluded_m2'], 8000.0 - 6.6667)
        self.assertNotEqual(item['accepted_m2'], 0.0)
        self.assertIn('0.0007', item['reason_text'])

    def test_a_structural_candidate_without_v4_stays_at_raw(self):
        item = self.rows[PENDING_C]
        self.assertEqual(item['accounting_class'], acc.PHANTOM_STRUCTURAL)
        self.assertEqual(item['accepted_m2'], 7000.0)
        self.assertEqual(item['excluded_m2'], 0.0)

    def test_review_stays_at_raw(self):
        item = self.rows[REVIEW_C]
        self.assertEqual(item['accounting_class'], acc.REVIEW)
        self.assertEqual(item['accepted_m2'], 5000.0)
        self.assertEqual(item['excluded_m2'], 0.0)
        self.assertEqual(item['reason_code'],
                         acc.R_APPLICATION_WITH_FLAT_COUNTER)

    def test_the_rule_miss_is_visible_as_a_control_confirmed_overstatement(self):
        item = self.rows[RULE_MISS]
        self.assertEqual(item['accounting_class'], acc.PHANTOM_PROVEN)
        self.assertEqual(item['reason_code'], cr.EXPLAIN_CONTROL)
        self.assertIn('контрольной проверкой', item['reason_text'])
        self.assertIsNone(item['base_flight_id'])
        self.assertEqual(item['bridge_flight_ids'], [])

    def test_no_v4_at_source_is_said_in_words(self):
        item = self.rows[NO_V4_C]
        self.assertEqual(item['accounting_class'], acc.PHANTOM_STRUCTURAL)
        self.assertEqual(item['reason_code'], cr.EXPLAIN_PENDING_NO_V4)
        self.assertEqual(item['evidence_state'], cr.EVIDENCE_NO_V4_AT_SOURCE)
        self.assertEqual(item['accepted_m2'], 4000.0)

    def test_the_chain_is_shown_and_the_bridge_is_not_a_correction(self):
        item = self.rows[C]
        self.assertEqual(item['base_flight_id'], A)
        self.assertEqual(item['bridge_flight_ids'], [B])
        # База и мостик -- обычные записи: в реестр корректировок не входят.
        self.assertNotIn(A, self.rows)
        self.assertNotIn(B, self.rows)

    def test_every_row_links_to_its_own_dji_record(self):
        for flight_id, item in self.rows.items():
            self.assertEqual(item['dji_url'],
                             'https://www.djiag.com/record/%d' % flight_id)


class PeriodFormulas(unittest.TestCase):

    def setUp(self):
        self.rows = fixture()
        self.total = cr.build(self.rows, 'ru')['total']

    def test_raw_is_the_plain_sum_and_is_never_rewritten(self):
        before = json.dumps(self.rows, default=str, sort_keys=True)
        cr.build(self.rows, 'ru')
        self.assertEqual(json.dumps(self.rows, default=str, sort_keys=True),
                         before)
        self.assertAlmostEqual(self.total['raw_m2'],
                               sum(r['raw_area_m2'] for r in self.rows))

    def test_the_bridge_is_not_subtracted(self):
        """Исключено ровно три записи; 600 м2 мостика в исключённом нет."""
        expected = 9000.0 + (8000.0 - 6.6667) + 5840.0
        self.assertAlmostEqual(self.total['excluded_m2'], expected, places=4)
        self.assertEqual(self.total['excluded_records'], 3)
        # Отрицательный контроль: с мостиком число было бы другим.
        self.assertNotAlmostEqual(self.total['excluded_m2'], expected + 600.0,
                                  places=4)

    def test_after_equals_raw_minus_confirmed(self):
        self.assertAlmostEqual(self.total['after_m2'],
                               self.total['raw_m2'] - self.total['excluded_m2'])

    def test_pending_and_review_are_exposures_not_subtractions(self):
        self.assertAlmostEqual(self.total['pending_m2'], 7000.0 + 4000.0)
        self.assertEqual(self.total['pending_records'], 2)
        self.assertAlmostEqual(self.total['review_m2'], 5000.0)
        self.assertEqual(self.total['review_records'], 1)
        # Они сидят ВНУТРИ «после корректировок» по своему RAW.
        self.assertGreater(self.total['after_m2'],
                           self.total['pending_m2'] + self.total['review_m2'])

    def test_the_drones_add_up_to_the_total(self):
        report = cr.build(self.rows, 'ru')
        for key in ('raw_m2', 'excluded_m2', 'after_m2', 'pending_m2',
                    'review_m2'):
            self.assertAlmostEqual(sum(d[key] for d in report['drones']),
                                   report['total'][key], places=6, msg=key)
        self.assertEqual(sum(d['records'] for d in report['drones']),
                         report['total']['records'])

    def test_drones_are_ordered_by_confirmed_exclusion(self):
        drones = cr.build(self.rows, 'ru')['drones']
        excluded = [d['excluded_m2'] for d in drones]
        self.assertEqual(excluded, sorted(excluded, reverse=True))
        self.assertEqual(drones[0]['machine_label'], 'DRONE-1')

    def test_the_period_is_open_while_anything_is_unresolved(self):
        self.assertFalse(self.total['complete'])
        self.assertIn('учтены по DJI RAW', self.total['status_text'])

    def test_the_period_closes_only_when_nothing_is_unresolved(self):
        rows = [r for r in self.rows
                if r['flight_id'] not in (PENDING_C, REVIEW_C, NO_V4_C)]
        total = cr.build(rows, 'ru')['total']
        self.assertTrue(total['complete'])
        self.assertIn('разрешены', total['status_text'])

    def test_the_partition_holds(self):
        self.assertTrue(self.total['partition_holds'])


class Views(unittest.TestCase):

    def test_each_view_keeps_its_own_class(self):
        rows = fixture()
        self.assertEqual(set(by_id(cr.build(rows, 'ru', cr.VIEW_CONFIRMED))),
                         {C, PARTIAL_C, RULE_MISS})
        self.assertEqual(set(by_id(cr.build(rows, 'ru', cr.VIEW_PENDING))),
                         {PENDING_C, NO_V4_C})
        self.assertEqual(set(by_id(cr.build(rows, 'ru', cr.VIEW_REVIEW))),
                         {REVIEW_C})

    def test_the_view_filters_the_register_and_not_the_totals(self):
        rows = fixture()
        full = cr.build(rows, 'ru', cr.VIEW_ALL)['total']
        narrow = cr.build(rows, 'ru', cr.VIEW_REVIEW)['total']
        self.assertEqual(full['raw_m2'], narrow['raw_m2'])
        self.assertEqual(full['excluded_m2'], narrow['excluded_m2'])

    def test_an_unknown_view_falls_back_to_all(self):
        self.assertEqual(cr.build(fixture(), 'ru', 'nonsense')['view'],
                         cr.VIEW_ALL)


class WordsForPeople(unittest.TestCase):

    LATIN = re.compile(r'[A-Za-z]{2,}')
    ALLOWED = {'DJI', 'RAW', 'Auto', 'Bridge', 'IDs', 'Flight', 'ID'}

    def test_no_resolver_enum_reaches_the_reader(self):
        for lang in ('ru', 'uz'):
            for item in cr.build(fixture(), lang)['register']:
                for enum in (rs.COUNTER_FLAT_RAW_OVERSTATED,
                             rs.PARTIAL_RECORDED_OVERSTATEMENT,
                             acc.PHANTOM_PROVEN, acc.PHANTOM_STRUCTURAL):
                    self.assertNotIn(enum, item['reason_text'])
                    self.assertNotIn(enum, item['class_label'])

    def test_every_reason_the_accounting_can_give_has_words(self):
        for name in dir(acc):
            if name.startswith('R_'):
                code = getattr(acc, name)
                if code in (acc.R_COUNTER_CORROBORATED,
                            acc.R_COUNTER_CORROBORATED_SUBWINDOW,
                            acc.R_RAW_UNVERIFIED_NO_V4, acc.R_ZERO_RECORDED,
                            acc.R_CANDIDATE_REFUTED, acc.R_RETAINED_VALIDATED,
                            acc.R_RETAINED_VALIDATED_NOT_STRUCTURAL,
                            acc.R_STRUCTURAL_NO_INTERVAL):
                    continue   # обычные записи и причины с отдельным текстом
                self.assertIn(code, cr.EXPLANATIONS, code)

    def test_uzbek_is_cyrillic(self):
        pairs = list(cr.EXPLANATIONS.values()) + list(cr.CLASS_LABELS.values())
        pairs += list(cr.EVIDENCE_LABELS.values()) + list(cr.TOOLTIPS.values())
        pairs += [cr.BRIDGE_NOTE, cr.STATUS_COMPLETE, cr.STATUS_OPEN,
                  cr.SHEET_SUMMARY, cr.SHEET_DRONES, cr.SHEET_CORRECTIONS,
                  cr.SHEET_REVIEW, cr.SHEET_DAYS, cr.SHEET_REGISTER,
                  cr.SHEET_HISTORY, cr.NORMAL_EXPLANATION, cr.OPEN_INSIDE,
                  cr.OPEN_NONE, cr.REST_LABEL]
        for _ru, uz in pairs:
            words = set(self.LATIN.findall(uz)) - self.ALLOWED
            self.assertEqual(words, set(), uz)

    def test_the_cyrillic_scan_fires_on_a_planted_latin_word(self):
        # Отрицательный контроль: проверка выше обязана уметь падать.
        words = set(self.LATIN.findall('Tasdiqlangan tuzatish')) - self.ALLOWED
        self.assertTrue(words)

    def test_the_after_figure_is_never_called_exact_or_final(self):
        text = json.dumps([cr.TOOLTIPS, cr.STATUS_OPEN, cr.STATUS_COMPLETE],
                          ensure_ascii=False).lower()
        for word in ('точная площадь', 'финальная площадь', 'итоговая площадь'):
            self.assertNotIn(word, text)

    def test_the_bridge_note_says_it_is_not_excluded(self):
        self.assertIn('не исключается', cr.BRIDGE_NOTE[0])


class Workbook(unittest.TestCase):

    def setUp(self):
        from openpyxl import load_workbook
        report = cr.build(fixture(), 'ru')
        book = cr.build_workbook(
            report, 'ru', period=('2026-09-01', '2026-09-18'),
            versions={'area_algorithm': 'impl-test',
                      'structural_rule': 'structural-retained-screen-frozen-1',
                      'accounting_classes': acc.ACCOUNTING_CLASSES_VERSION,
                      'report': cr.REPORT_VERSION})
        buffer = io.BytesIO()
        book.save(buffer)
        buffer.seek(0)
        self.book = load_workbook(buffer)
        self.report = report

    def sheet_rows(self, name):
        return [[c.value for c in row] for row in self.book[name].iter_rows()]

    def test_the_four_sheets(self):
        # DRONE-AREA-CONTROL-V2-MEGA: прежние четыре листа стоят первыми и
        # в прежнем порядке -- их читают бухгалтерия и сверочные скрипты;
        # три новых листа только добавлены после них.
        self.assertEqual(self.book.sheetnames,
                         ['Сводка', 'По_дронам', 'Корректировки',
                          'Требует_проверки', 'По_дням', 'Реестр',
                          'История_решений'])

    def test_the_summary_carries_the_five_figures_and_the_versions(self):
        rows = {r[0]: r for r in self.sheet_rows('Сводка')}
        total = self.report['total']
        self.assertAlmostEqual(rows['DJI RAW, га'][1], total['raw_m2'] / 1e4)
        self.assertAlmostEqual(rows['Подтверждённо исключено, га'][1],
                               total['excluded_m2'] / 1e4)
        self.assertAlmostEqual(
            rows['Площадь после подтверждённых корректировок, га'][1],
            total['after_m2'] / 1e4)
        self.assertEqual(rows['Ожидает доказательства / V4, га'][2], 2)
        self.assertEqual(rows['Требует проверки, га'][2], 1)
        self.assertEqual(rows['Структурное правило'][1],
                         'structural-retained-screen-frozen-1')
        self.assertEqual(rows['Период: с'][1], '2026-09-01')

    def test_corrections_hold_values_and_a_working_link(self):
        sheet = self.book['Корректировки']
        header = [c.value for c in sheet[1]]
        ids = header.index('C Flight ID') + 1
        link = header.index('Ссылка DJI') + 1
        found = {}
        for r in range(2, sheet.max_row + 1):
            fid = sheet.cell(row=r, column=ids).value
            found[fid] = r
            self.assertEqual(sheet.cell(row=r, column=link).hyperlink.target,
                             'https://www.djiag.com/record/%d' % fid)
        self.assertEqual(set(found), {C, PARTIAL_C, RULE_MISS})
        r = found[C]
        self.assertEqual(sheet.cell(row=r, column=header.index(
            'A Flight ID') + 1).value, A)
        self.assertEqual(sheet.cell(row=r, column=header.index(
            'Bridge IDs (не корректируются)') + 1).value, str(B))
        self.assertAlmostEqual(sheet.cell(row=r, column=header.index(
            'Исключено, га') + 1).value, 0.9)
        r = found[PARTIAL_C]
        self.assertAlmostEqual(sheet.cell(row=r, column=header.index(
            'Принято, га') + 1).value, 6.6667 / 1e4)

    def test_the_review_sheet_holds_pending_and_review_with_raw(self):
        sheet = self.book['Требует_проверки']
        header = [c.value for c in sheet[1]]
        ids = header.index('C Flight ID') + 1
        found = {sheet.cell(row=r, column=ids).value
                 for r in range(2, sheet.max_row + 1)}
        self.assertEqual(found, {PENDING_C, REVIEW_C, NO_V4_C})

    def test_a_missing_chain_is_an_empty_cell_not_a_zero(self):
        sheet = self.book['Корректировки']
        header = [c.value for c in sheet[1]]
        ids = header.index('C Flight ID') + 1
        for r in range(2, sheet.max_row + 1):
            if sheet.cell(row=r, column=ids).value == RULE_MISS:
                self.assertIsNone(sheet.cell(row=r, column=header.index(
                    'A Flight ID') + 1).value)
                return
        self.fail('rule miss row not found')

    def test_the_uzbek_book_has_its_own_sheet_names(self):
        book = cr.build_workbook(cr.build(fixture(), 'uz'), 'uz')
        self.assertEqual(book.sheetnames,
                         [cr.SHEET_SUMMARY[1], cr.SHEET_DRONES[1],
                          cr.SHEET_CORRECTIONS[1], cr.SHEET_REVIEW[1],
                          cr.SHEET_DAYS[1], cr.SHEET_REGISTER[1],
                          cr.SHEET_HISTORY[1]])


if __name__ == '__main__':
    unittest.main()
