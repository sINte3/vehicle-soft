# -*- coding: utf-8 -*-
"""DJI-AREA-SIMPLIFY-001: учётные классы поверх решения резолвера.

Каждая проверка строит НАСТОЯЩЕЕ решение ``resolver.resolve_area`` из
синтетического V4 (строители взяты из матрицы приёмки) и только потом
раскладывает его в класс. Так тест ловит расхождение между резолвером и
отображением, а не сверяет отображение с самим собой.

Проверки парные: рядом с каждым «должно стать X» стоит случай, который при
неверной реализации стал бы X по ошибке. Главные из них -- запреты целевой
модели: кандидат экрана без проверенного интервала НЕ обнуляется, плоский
счётчик при наблюдённом применении НЕ считается доказанным нулём, неизвестный
статус НЕ становится NORMAL.

Чистые модули: без Flask и без базы.
"""

import json
import os
import sys
import unittest

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if REPO_ROOT not in sys.path:
    sys.path.insert(0, REPO_ROOT)

from dji_area import accounting as acc  # noqa: E402
from dji_area import resolver as rs  # noqa: E402
from tests.test_dji_area_core import counter_series, evidence  # noqa: E402

MATCH = {'applicable': True, 'candidate': True, 'reason': 'CANDIDATE',
         'base_flight_id': 1, 'bridge_flight_ids': [2],
         'boundary_gaps_s': [0.0, 0.0], 'scalar_source_check': True}
NO_SCALAR_MATCH = dict(MATCH, scalar_source_check=False)

RAW = 10000.0            # 15.0 му = 10 000 м²
FLAT = [17.15, 17.15, 17.15]
GROWS_TO_RAW = [0.0, 7.5, 15.0]
TINY = [20.10, 20.13, 20.16]          # +0.06 му = 40 м²
HALF = [0.0, 3.75, 7.5]               # 5 000 м² -- ни ноль, ни RAW


def decide(raw, frames=None, **kw):
    return rs.resolve_area(evidence(raw, frames, **kw)).as_dict()


def classify(raw, frames=None, **kw):
    return acc.classify(decide(raw, frames, **kw))


class NormalRecordsKeepTheirRaw(unittest.TestCase):

    def test_counter_corroborated_record_is_normal_with_raw(self):
        out = classify(RAW, counter_series(GROWS_TO_RAW))
        self.assertEqual(out['accounting_class'], acc.NORMAL)
        self.assertEqual(out['reason'], acc.R_COUNTER_CORROBORATED)
        self.assertEqual(out['accounted_area_m2'], RAW)
        self.assertIsNone(out['confirmed_overstatement_m2'])

    def test_record_without_v4_is_normal_but_named_unverified(self):
        out = classify(RAW, None)
        self.assertEqual(out['accounting_class'], acc.NORMAL)
        self.assertEqual(out['reason'], acc.R_RAW_UNVERIFIED_NO_V4)
        self.assertEqual(out['accounted_area_m2'], RAW)

    def test_zero_raw_is_normal_with_zero_exposure(self):
        out = classify(0.0, None)
        self.assertEqual(out['accounting_class'], acc.NORMAL)
        self.assertEqual(out['exposure_m2'], 0.0)


class ProvenNeedsAValidatedCounterInterval(unittest.TestCase):

    def test_flat_counter_on_a_structural_match_is_proven_zero(self):
        out = classify(RAW, counter_series(FLAT), structural=MATCH)
        self.assertEqual(out['accounting_class'], acc.PHANTOM_PROVEN)
        self.assertEqual(out['reason'], acc.R_RETAINED_VALIDATED)
        self.assertEqual(out['accounted_area_m2'], 0.0)
        self.assertEqual(out['validated_delta_m2'], 0.0)
        self.assertEqual(out['confirmed_overstatement_m2'], RAW)

    def test_tiny_positive_increment_survives_as_positive(self):
        out = classify(12413.0, counter_series(TINY), structural=MATCH)
        self.assertEqual(out['accounting_class'], acc.PHANTOM_PROVEN)
        self.assertGreater(out['accounted_area_m2'], 0.0)
        self.assertAlmostEqual(out['accounted_area_m2'], 40.0, delta=0.5)
        self.assertAlmostEqual(out['confirmed_overstatement_m2'],
                               12413.0 - out['accounted_area_m2'])

    def test_flat_counter_without_the_structural_screen_is_still_proven(self):
        # Скрытый фантом (ширина заполнена): экран молчит, счётчик -- нет.
        out = classify(RAW, counter_series(FLAT))
        self.assertEqual(out['accounting_class'], acc.PHANTOM_PROVEN)
        self.assertEqual(out['reason'],
                         acc.R_RETAINED_VALIDATED_NOT_STRUCTURAL)

    def test_flat_counter_with_observed_application_is_not_proven(self):
        # Отрицательный контроль к двум проверкам выше: те же кадры счётчика,
        # но применение наблюдалось. impl-3 оставляет запись нерешённой, и
        # «доказанный ноль» здесь был бы утверждением о земле, а не о счётчике.
        frames = counter_series(FLAT, spray_flag=1, flow=100)
        out = classify(RAW, frames, structural=MATCH)
        self.assertEqual(out['accounting_class'], acc.REVIEW)
        self.assertEqual(out['reason'], acc.R_APPLICATION_WITH_FLAT_COUNTER)
        self.assertIsNone(out['accounted_area_m2'])
        self.assertIsNone(out['confirmed_overstatement_m2'])
        self.assertEqual(out['exposure_m2'], RAW)


class NoAutoZeroWithoutV4(unittest.TestCase):

    def test_structural_match_without_v4_keeps_raw_as_unresolved(self):
        decision = decide(RAW, None, structural=MATCH)
        self.assertEqual(decision['area_status'], rs.UNKNOWN_SUSPECT)
        out = acc.classify(decision)
        self.assertEqual(out['accounting_class'], acc.PHANTOM_STRUCTURAL)
        self.assertEqual(out['exposure_m2'], RAW)
        # Ни нуля, ни «подтверждённого завышения»: только экспозиция.
        self.assertIsNone(out['accounted_area_m2'])
        self.assertIsNone(out['confirmed_overstatement_m2'])
        self.assertIsNone(out['validated_delta_m2'])

    def test_candidate_without_scalar_match_is_not_a_phantom_at_all(self):
        out = classify(RAW, None, structural=NO_SCALAR_MATCH)
        self.assertEqual(out['accounting_class'], acc.NORMAL)
        self.assertFalse(out['structural_match'])

    def test_counter_that_confirms_raw_beats_the_structural_screen(self):
        out = classify(RAW, counter_series(GROWS_TO_RAW), structural=MATCH)
        self.assertEqual(out['accounting_class'], acc.NORMAL)
        self.assertEqual(out['reason'], acc.R_CANDIDATE_REFUTED)
        self.assertEqual(out['accounted_area_m2'], RAW)

    def test_candidate_contradicted_both_ways_goes_to_review(self):
        # V4 есть и не даёт ни ноль, ни RAW: это не «ждёт подтверждения».
        decision = decide(RAW, counter_series(HALF), structural=MATCH)
        self.assertEqual(decision['area_status'],
                         rs.COUNTER_RELATIONSHIP_OUTLIER)
        out = acc.classify(decision)
        self.assertEqual(out['accounting_class'], acc.REVIEW)
        self.assertEqual(out['reason'], acc.R_COUNTER_RELATIONSHIP)


class ReviewIsNeverCorrectedAutomatically(unittest.TestCase):

    def test_interval_overlap(self):
        out = classify(RAW, counter_series(FLAT),
                       overlap={'group_id': 'overlap:1', 'conflict': True})
        self.assertEqual(out['accounting_class'], acc.REVIEW)
        self.assertEqual(out['reason'], acc.R_INTERVAL_OVERLAP)
        self.assertIsNone(out['accounted_area_m2'])

    def test_missing_raw(self):
        out = classify(None, None)
        self.assertEqual(out['accounting_class'], acc.REVIEW)
        self.assertEqual(out['exposure_m2'], 0.0)
        self.assertIsNone(out['raw_area_m2'])

    def test_application_without_measured_area(self):
        frames = counter_series([None, None, None], spray_flag=1, flow=100)
        decision = decide(0.0, frames)
        self.assertEqual(decision['area_status'],
                         rs.APPLICATION_WITHOUT_MEASURED_AREA)
        self.assertEqual(acc.classify(decision)['accounting_class'], acc.REVIEW)

    def test_unknown_status_fails_safe_into_review(self):
        for status in ('SOME_FUTURE_STATUS', None, ''):
            out = acc.classify({'area_status': status, 'raw_area_m2': RAW,
                                'aggregation_eligibility': rs.AGG_PROVISIONAL})
            self.assertEqual(out['accounting_class'], acc.REVIEW, status)
            self.assertEqual(out['reason'], acc.R_UNMAPPED_STATUS, status)


class EveryResolverStatusIsMappedExplicitly(unittest.TestCase):

    def test_no_known_status_falls_through_to_unmapped(self):
        for status in rs.AREA_STATUSES:
            for eligibility in (rs.AGG_CERTIFIED, rs.AGG_PROVISIONAL,
                                rs.AGG_UNRESOLVED, rs.AGG_EXCLUDED_OVERLAP):
                out = acc.classify({
                    'area_status': status, 'raw_area_m2': RAW,
                    'aggregation_eligibility': eligibility,
                    'corrected_recorded_area_m2': 0.0,
                    'controller_delta_area_m2': 0.0})
                self.assertIn(out['accounting_class'], acc.ACCOUNTING_CLASSES)
                self.assertNotEqual(out['reason'], acc.R_UNMAPPED_STATUS,
                                    '%s / %s' % (status, eligibility))

    def test_overstated_status_is_proven_only_when_certified(self):
        for status in (rs.COUNTER_FLAT_RAW_OVERSTATED,
                       rs.PARTIAL_RECORDED_OVERSTATEMENT):
            for eligibility, expected in (
                    (rs.AGG_CERTIFIED, acc.PHANTOM_PROVEN),
                    (rs.AGG_UNRESOLVED, acc.REVIEW),
                    (rs.AGG_PROVISIONAL, acc.REVIEW)):
                out = acc.classify({
                    'area_status': status, 'raw_area_m2': RAW,
                    'aggregation_eligibility': eligibility,
                    'corrected_recorded_area_m2': 0.0,
                    'controller_delta_area_m2': 0.0})
                self.assertEqual(out['accounting_class'], expected,
                                 '%s / %s' % (status, eligibility))


class DatabaseRowsAreReadTheSameWay(unittest.TestCase):
    """Строка `dji_area_calculations`: булевы -- 0/1, флаги -- JSON-текст."""

    def test_sqlite_integers_and_json_flags(self):
        frames = counter_series(FLAT, spray_flag=1, flow=100)
        decision = decide(RAW, frames, structural=MATCH)
        row = dict(decision)
        row['anomaly_flags_json'] = json.dumps(row.pop('anomaly_flags'))
        row['structural_candidate'] = 1
        row['scalar_source_check'] = 1
        out = acc.classify(row)
        self.assertEqual(out['accounting_class'], acc.REVIEW)
        self.assertEqual(out['reason'], acc.R_APPLICATION_WITH_FLAT_COUNTER)
        self.assertTrue(out['structural_match'])


class PeriodQuantitiesStaySeparate(unittest.TestCase):

    def setUp(self):
        self.rows = [
            dict(decide(RAW, counter_series(GROWS_TO_RAW)), hw='A'),
            dict(decide(RAW, None), hw='A'),
            dict(decide(RAW, counter_series(FLAT), structural=MATCH), hw='A'),
            dict(decide(12413.0, counter_series(TINY), structural=MATCH),
                 hw='B'),
            dict(decide(8000.0, None, structural=MATCH), hw='B'),
            dict(decide(RAW, counter_series(HALF)), hw='B'),
            dict(decide(None, None), hw='B'),
        ]
        self.summary = acc.summarize(self.rows, key_fn=lambda r: r['hw'])
        self.total = self.summary['total']

    def test_partition_holds_and_raw_is_untouched(self):
        t = self.total
        self.assertTrue(t['partition_holds'])
        self.assertEqual(t['records'], 7)
        self.assertEqual(t['raw_missing_records'], 1)
        self.assertEqual(t['raw_sum_m2'], RAW * 4 + 12413.0 + 8000.0)
        self.assertEqual(sum(t['class_records'].values()), 7)

    def test_each_quantity_is_its_own_number(self):
        t = self.total
        self.assertEqual(t['class_records'][acc.PHANTOM_PROVEN], 2)
        self.assertEqual(t['class_raw_m2'][acc.PHANTOM_PROVEN], RAW + 12413.0)
        self.assertAlmostEqual(t['proven_validated_delta_m2'], 40.0, delta=0.5)
        self.assertAlmostEqual(
            t['confirmed_overstatement_m2'],
            RAW + 12413.0 - t['proven_validated_delta_m2'])
        # Кандидат без V4: экспозиция есть, завышения НЕТ.
        self.assertEqual(t['class_raw_m2'][acc.PHANTOM_STRUCTURAL], 8000.0)
        self.assertEqual(t['class_raw_m2'][acc.REVIEW], RAW)
        self.assertEqual(t['unresolved_exposure_m2'], 8000.0 + RAW)
        self.assertAlmostEqual(
            t['raw_minus_confirmed_overstatement_m2'],
            t['raw_sum_m2'] - t['confirmed_overstatement_m2'])
        self.assertEqual(t['normal_counter_validated_records'], 1)
        self.assertEqual(t['normal_unverified_records'], 1)

    def test_certified_subtotal_is_the_existing_aggregators_number(self):
        # «Проверено» здесь и на странице /drones/area-evidence -- одно число.
        t = self.total
        self.assertEqual(t['certified_records'],
                         t['evidence']['certified_records'])
        self.assertEqual(t['certified_records'], 3)
        self.assertAlmostEqual(t['certified_sum_m2'],
                               RAW + 0.0 + t['proven_validated_delta_m2'])

    def test_per_key_buckets_sum_to_the_total(self):
        by_key = self.summary['by_key']
        self.assertEqual(sorted(by_key), ['A', 'B'])
        self.assertEqual(sum(b['records'] for b in by_key.values()), 7)
        self.assertAlmostEqual(
            sum(b['confirmed_overstatement_m2'] for b in by_key.values()),
            self.total['confirmed_overstatement_m2'])
        for bucket in by_key.values():
            self.assertTrue(bucket['partition_holds'])


if __name__ == '__main__':
    unittest.main()
