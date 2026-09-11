# -*- coding: utf-8 -*-
"""Числа в BLOCK1B_RESULTS.md совпадают с таблицами прогона.

[REASON]: устав проекта -- «отчёт не является доказательством, принимается
дифф и артефакт». Документ с числами, не привязанный к данным, расходится с
ними молча: таблицы лежат рядом, и ничто не мешает поправить одно без
другого. Здесь документ и таблицы связаны, и расхождение падает.

ЗАПУСК
  python tools/test_dji_area_block1b_evidence.py
"""

import csv
import json
import os
import re
import statistics
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
EVID = os.path.join(ROOT, 'docs', 'area-evidence')
DATA = os.path.join(EVID, 'block1b-2026-08-18')
DOC = os.path.join(EVID, 'BLOCK1B_RESULTS.md')

N5 = '1581F574B2387001009R'
N6 = '1581F574B235W00100Q5'


def num(x):
    return float(x) if x not in (None, '', 'None') else None


def table(name):
    with open(os.path.join(DATA, name), encoding='utf-8') as fh:
        return list(csv.DictReader(fh))


def doc_text():
    with open(DOC, encoding='utf-8') as fh:
        return fh.read()


class TheHeadlineNumbersComeFromTheTables(unittest.TestCase):

    def setUp(self):
        self.flights = table('flights.csv')
        self.cov = {r['flight_id']: r
                    for r in table('coverage.csv')}
        self.doc = doc_text()

    def total(self, hardware, column):
        """Сумма по ИЗМЕРЕННЫМ значениям. NULL не ноль (правило проекта)."""
        vals = [num(r[column]) for r in self.flights
                if r['hardware_id'] == hardware]
        return sum(v for v in vals if v is not None) / 10000.0

    def assertInDoc(self, needle):
        self.assertIn(needle, self.doc, '%r нет в документе' % needle)

    def test_n5_raw_and_corrected_and_their_exact_difference(self):
        raw = self.total(N5, 'raw_area_m2')
        cor = self.total(N5, 'corrected_recorded_area_m2')
        self.assertAlmostEqual(raw, 36.3977, places=4)
        self.assertAlmostEqual(cor, 32.8385, places=4)
        flat = sum(num(r['raw_area_m2']) for r in self.flights
                   if r['area_status'] == 'COUNTER_FLAT_RAW_OVERSTATED')
        self.assertEqual(len([r for r in self.flights
                              if r['area_status']
                              == 'COUNTER_FLAT_RAW_OVERSTATED']), 4)
        self.assertAlmostEqual(flat / 10000.0, 3.5592, places=4)
        # Разность точная, а не «примерно».
        self.assertAlmostEqual(raw - flat / 10000.0, cor, places=4)
        for s in ('36.3977', '32.8385', '3.5592'):
            self.assertInDoc(s)

    def test_n6_raw_matches_and_two_flights_have_no_measured_area(self):
        self.assertAlmostEqual(self.total(N6, 'raw_area_m2'), 28.7469,
                               places=4)
        unmeasured = [r for r in self.flights if r['hardware_id'] == N6
                      and num(r['corrected_recorded_area_m2']) is None]
        self.assertEqual(len(unmeasured), 2)
        # Оба с нулевым raw -- иначе сумма выше была бы неполной.
        for r in unmeasured:
            self.assertEqual(num(r['raw_area_m2']), 0.0)
        self.assertInDoc('28.7469')

    def test_every_flight_lacks_proven_historical_geometry(self):
        self.assertEqual(len(self.flights), 83)
        self.assertTrue(all(r['historical_geometry_available'] == '0'
                            for r in self.flights))
        self.assertTrue(all(c['contour_status'] == 'CONTOUR_ABSENT'
                            for c in self.cov.values()))
        with open(os.path.join(DATA, 'land_geometries.json'),
                  encoding='utf-8') as fh:
            geoms = json.load(fh)
        self.assertEqual(len(geoms), 20)
        self.assertTrue(all(g['status'] == 'NOT_CAPTURED' for g in geoms))
        self.assertInDoc('83/83')

    def test_not_one_flight_discriminates_between_the_counter_hypotheses(self):
        self.assertTrue(all(c['discriminating'] == 'False'
                            for c in self.cov.values()))
        ratios = [num(c['s_over_u']) for c in self.cov.values()
                  if num(c.get('s_over_u')) is not None]
        self.assertTrue(ratios)
        # Контроль: порог не «не сработал», а НЕ ДОСТИГНУТ с большим запасом.
        self.assertLess(max(ratios), 1.15)
        self.assertIn('A10', self.doc)

    def test_the_live_data_confirms_the_broken_source_chain(self):
        flagged = [r for r in self.flights
                   if 'LIST_FROM_MUTABLE_RAW_JSON' in r['anomaly_flags_json']]
        self.assertEqual(len(flagged), len(self.flights))
        self.assertInDoc('83 из 83')


class TheMethodBiasIsLargerThanTheRasterNoise(unittest.TestCase):
    """A15: вывод документа держится на соотношении двух величин."""

    def setUp(self):
        self.cov = table('coverage.csv')

    def test_the_engines_own_uncertainty_is_an_order_smaller(self):
        unc = []
        for r in self.cov:
            v = json.loads(r.get('uncertainty_percent') or '{}').get(
                'swath_work_ha')
            if isinstance(v, (int, float)):
                unc.append(v)
        share = [num(r.get('repeated_share_of_unique')) for r in self.cov]
        share = [s for s in share if s is not None]
        self.assertTrue(unc and share)
        noise = statistics.median(unc) / 100.0        # проценты -> доля
        method = abs(statistics.median(share))
        # Вывод документа: методическая составляющая на порядок больше шума.
        self.assertGreater(method, noise * 10,
                           'method=%.5f noise=%.5f' % (method, noise))

    def test_s_minus_u_is_systematically_negative_not_scattered(self):
        vals = [num(r.get('s_minus_u_ha')) for r in self.cov]
        vals = [v for v in vals if v is not None]
        negative = sum(1 for v in vals if v < 0)
        # Не «примерно поровну»: перекос на сторону U и есть находка.
        self.assertGreater(negative, 0.7 * len(vals),
                           '%d of %d negative' % (negative, len(vals)))


class TheProvenanceIsPinned(unittest.TestCase):

    def test_the_manifest_names_the_commit_the_document_quotes(self):
        with open(os.path.join(DATA, 'manifest.json'), encoding='utf-8') as fh:
            m = json.load(fh)
        self.assertIn(m['repo_commit'], doc_text())
        self.assertEqual(m['counts']['flights'], 83)
        self.assertEqual(m['counts']['discriminating'], 0)
        self.assertEqual(m['counts']
                         ['flights_without_proven_historical_geometry'], 83)
        self.assertTrue(m['not_billable'])

    def test_the_second_apply_wrote_nothing(self):
        with open(os.path.join(DATA, 'apply2.json'), encoding='utf-8') as fh:
            s = json.load(fh)
        self.assertEqual(s['calc_writes'], {'unchanged': 226})
        self.assertEqual(s['field_writes'], {'unchanged': 226})
        self.assertEqual(s['flights_in_period'], 226)
        # Ключа, по которому ворота считали ДО правки, в файле нет.
        self.assertNotIn('flights', s)

    def test_the_document_does_not_claim_a_billable_number(self):
        doc = doc_text()
        self.assertIn('ОТКРЫТ', doc)      # физическая ширина
        self.assertIn('not_billable', doc)
        self.assertIn('NY/T 3213', doc)


if __name__ == '__main__':
    unittest.main()
