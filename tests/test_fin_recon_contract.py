# -*- coding: utf-8 -*-
"""FIN-RECON-STORAGE-001: контракт snapshot.json (fin_recon/contract.py).

Синтетический снимок месяца (tests/fin_recon_fixture.py) годен; каждое
правило контракта проверено отрицательным контролем -- одной порчей этого
снимка, которая обязана дать проблему с узнаваемым текстом. Проверка,
которая молчит и на порченом снимке, проверкой не является.

ВСЕ ДАННЫЕ СИНТЕТИЧЕСКИЕ. Запуск:
  python -m unittest tests.test_fin_recon_contract -v
"""

import copy
import os
import sys
import unittest

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if REPO_ROOT not in sys.path:
    sys.path.insert(0, REPO_ROOT)

from fin_recon import contract  # noqa: E402
from tests import fin_recon_fixture as fx  # noqa: E402

BASE = fx.build_snapshot()


def broken(change):
    snap = copy.deepcopy(BASE)
    change(snap)
    return contract.validate(snap)


def metric(snap, key):
    return next(m for m in snap['metrics'] if m['key'] == key)


def work(snap, key):
    return next(w for w in snap['works'] if w['key'] == key)


def op(snap, key):
    return next(o for o in snap['operators'] if o['key'] == key)


class Valid(unittest.TestCase):

    def test_synthetic_month_is_valid(self):
        self.assertEqual(contract.validate(BASE), [])

    def test_shape_matches_a_real_month(self):
        c = contract.derived_checks(BASE)
        self.assertEqual((c['WORKS_COUNT'], c['OPERATORS_COUNT'],
                          c['ANOMALIES_COUNT']), (38, 7, 39))
        self.assertEqual(c['CROSS_LINKS_COUNT'], 2)
        self.assertEqual(c['UNKNOWN_SALARY_WORKS_COUNT'], 3)
        self.assertEqual(c['VS_MISSING'], 1)

    def test_content_hash_ignores_key_order_not_values(self):
        reordered = dict(reversed(list(copy.deepcopy(BASE).items())))
        self.assertEqual(contract.content_sha256(reordered),
                         contract.content_sha256(BASE))
        changed = copy.deepcopy(BASE)
        changed['works'][0]['farm'] += ' '
        self.assertNotEqual(contract.content_sha256(changed),
                            contract.content_sha256(BASE))


class Rules(unittest.TestCase):

    def expect(self, problems, *fragments):
        self.assertTrue(problems, 'the broken snapshot passed the contract')
        text = '\n'.join(problems)
        for fragment in fragments:
            self.assertIn(fragment, text)

    # ── неизвестное -- не ноль ───────────────────────────────────────
    def test_unknown_with_a_number_refused(self):
        def change(s):
            metric(s, 'UNKNOWN_SALARY_AMOUNT')['value'] = '0'
        self.expect(broken(change), 'unknown is not zero')

    def test_null_without_unknown_refused(self):
        def change(s):
            op(s, 'OP1')['accrued']['amount'] = None
        self.expect(broken(change), 'value null requires evidence UNKNOWN')

    def test_unknown_accrual_needs_null_difference(self):
        def change(s):
            o = op(s, 'OP5')
            o['accrued'] = {'amount': None, 'evidence': 'UNKNOWN',
                            'source_value': None, 'ref': None}
        self.expect(broken(change), 'must be null when the accrual is unknown')

    def test_money_as_json_number_refused(self):
        def change(s):
            s['payouts'][0]['amount'] = 1499900
        self.expect(broken(change), 'must be a decimal string')

    def test_too_many_decimal_places_refused(self):
        def change(s):
            op(s, 'OP1')['accrued']['amount'] = '1500000.001'
        self.expect(broken(change), 'decimal places')

    # ── состав ключей ───────────────────────────────────────────────
    def test_unexpected_key_refused(self):
        def change(s):
            s['works'][0]['paid'] = True
        self.expect(broken(change), "unexpected key(s) paid")

    def test_missing_key_refused(self):
        def change(s):
            del s['header']['owner_approved_at']
        self.expect(broken(change), 'missing key(s) owner_approved_at')

    # ── ложные суммы и счёт ─────────────────────────────────────────
    def test_false_accrual_total_refused(self):
        def change(s):
            metric(s, 'ACCRUED')['value'] = '8170000.58'
        self.expect(broken(change), 'metrics.ACCRUED', 'ACCRUED_SUM')

    def test_dropped_work_refused_even_with_fixed_count(self):
        def change(s):
            s['works'] = [w for w in s['works'] if w['key'] != 'W05']
            metric(s, 'WORKS')['value'] = '37'
        self.expect(broken(change), 'operators[2].area.total_ha')

    def test_false_operator_count_refused(self):
        def change(s):
            metric(s, 'OPERATORS')['value'] = '8'
        self.expect(broken(change), 'OPERATORS_COUNT')

    def test_false_anomaly_count_refused(self):
        def change(s):
            metric(s, 'ANOMALIES')['value'] = '38'
        self.expect(broken(change), 'ANOMALIES_COUNT')

    def test_required_control_missing_refused(self):
        def change(s):
            s['metrics'] = [m for m in s['metrics'] if m['key'] != 'DIFFERENCE']
        self.expect(broken(change), 'required control DIFFERENCE_SUM')

    def test_identity_mismatch_refused(self):
        def change(s):
            metric(s, 'SUMMARY_F27')['value'] = '50000001'
        self.expect(broken(change), 'its identity gives')

    def test_recognized_total_must_be_sum_of_payouts(self):
        def change(s):
            op(s, 'OP1')['recognized_total'] = '1500000.00'
        self.expect(broken(change), 'the payouts of the operator sum to')

    def test_difference_must_be_accrued_minus_recognized(self):
        def change(s):
            op(s, 'OP6')['difference'] = '0.00'
        self.expect(broken(change), 'accrued minus recognized')

    def test_operator_breakdown_must_match_works(self):
        def change(s):
            op(s, 'OP7')['area']['breakdown'][0]['area_ha'] = '1.00'
        self.expect(broken(change), 'does not match the works')

    # ── выплаты: текст книги против трактовки владельца ──────────────
    def test_owner_payout_shown_as_book_text_refused(self):
        def change(s):
            p = next(p for p in s['payouts'] if p['kind'] == 'OWNER_RECOGNIZED')
            p['evidence'] = 'BOOK_TEXT'
        self.expect(broken(change), 'requires evidence OWNER_INTERPRETATION')

    def test_owner_payout_without_decision_refused(self):
        def change(s):
            p = next(p for p in s['payouts'] if p['kind'] == 'OWNER_RECOGNIZED')
            p['decision'] = None
        self.expect(broken(change), 'must name an owner decision')

    def test_book_text_payout_without_text_refused(self):
        def change(s):
            s['payouts'][0]['text'] = None
        self.expect(broken(change), 'payouts[0].text')

    # ── межмесячные работы ──────────────────────────────────────────
    def test_other_month_work_without_link_refused(self):
        def change(s):
            s['cross_period_links'] = [x for x in s['cross_period_links']
                                       if x['work'] != 'W27']
        self.expect(broken(change), 'works.W27', 'no cross-period link')

    def test_link_with_another_area_refused(self):
        def change(s):
            s['cross_period_links'][0]['area_ha'] = '1.00'
        self.expect(broken(change), 'must equal the area of W10')

    def test_link_for_a_work_of_the_month_refused(self):
        def change(s):
            s['cross_period_links'][0]['work'] = 'W01'
        self.expect(broken(change), 'must equal the work month of W01')

    def test_work_month_must_be_a_month_of_its_dates(self):
        def change(s):
            work(s, 'W01')['work_month'] = '2031-02'
        self.expect(broken(change), 'is not the month of any work date')

    def test_ledger_month_is_the_period(self):
        def change(s):
            work(s, 'W02')['ledger_month'] = '2031-04'
        self.expect(broken(change), 'must equal header.period')

    # ── статус и утверждение ───────────────────────────────────────
    def test_approval_refused(self):
        def change(s):
            s['header']['owner_approved_at'] = '2031-10-13'
        self.expect(broken(change), 'approval is not part of this snapshot')

    def test_financial_close_refused(self):
        def change(s):
            s['header']['financially_closed_at'] = '2031-10-13'
        self.expect(broken(change), 'financial close is not part')

    def test_other_status_refused(self):
        def change(s):
            s['header']['status'] = 'FINANCIALLY_CLOSED'
        self.expect(broken(change), 'header.status')

    def test_anomaly_status_must_be_a_register_status(self):
        def change(s):
            s['anomalies'][0]['status'] = 'ЗАКРЫТО'
        self.expect(broken(change), 'is not one of the register statuses')

    def test_anomaly_id_unique(self):
        def change(s):
            s['anomalies'][1]['id'] = s['anomalies'][0]['id']
        self.expect(broken(change), 'duplicate id')

    # ── версии ─────────────────────────────────────────────────────
    def test_version_one_without_supersedes(self):
        def change(s):
            s['header']['supersedes'] = {'version': 0,
                                         'content_sha256': 'a' * 64}
        self.expect(broken(change), 'must be null for version 1')

    def test_version_two_needs_supersedes_and_reason(self):
        def change(s):
            s['header']['version'] = 2
        self.expect(broken(change), 'header.supersedes',
                    'header.revision_reason')

    def test_version_two_valid(self):
        v2 = fx.version_two(BASE, lambda s: None)
        self.assertEqual(contract.validate(v2), [])

    # ── DJI ────────────────────────────────────────────────────────
    def test_dji_accepted_must_be_raw_minus_excluded(self):
        def change(s):
            s['dji']['accepted_ha'] = '880.00350000'
        self.expect(broken(change), 'raw minus excluded')

    def test_dji_admin_exclusion_must_match_decisions(self):
        def change(s):
            s['dji']['decisions'][0]['accepted_after_ha'] = '0.0020'
        self.expect(broken(change), 'the decisions exclude')

    def test_dji_baskets_must_cover_raw(self):
        def change(s):
            s['dji']['unattributed_ha'] = '200.0000'
        self.expect(broken(change), 'attribution baskets sum to')

    # ── Vehicle Soft ───────────────────────────────────────────────
    def test_vs_counts_must_match_works(self):
        def change(s):
            s['vs']['equal'] = 33
        self.expect(broken(change), "vs.equal")

    def test_missing_row_has_no_db_area(self):
        def change(s):
            work(s, 'W27')['vs_db_area_ha'] = '1.00'
        self.expect(broken(change), 'missing in the database')

    # ── источники ──────────────────────────────────────────────────
    def test_work_must_cite_a_primary_book(self):
        def change(s):
            work(s, 'W01')['source'] = 'S09'
        self.expect(broken(change), 'must be a BOOK_PRIMARY source')

    def test_same_book_row_twice_refused(self):
        def change(s):
            w1, w2 = work(s, 'W01'), work(s, 'W02')
            w2['row'] = w1['row']
        self.expect(broken(change), 'the same source row is listed twice')

    def test_external_source_may_lack_a_hash_but_others_may_not(self):
        def change(s):
            s['sources'][0]['sha256'] = None
        self.expect(broken(change), 'sources[0].sha256')

        def external(s):
            ext = next(x for x in s['sources'] if x['role'] == 'EXTERNAL')
            ext['sha256'] = None
        self.assertEqual(broken(external), [])


if __name__ == '__main__':
    unittest.main()
