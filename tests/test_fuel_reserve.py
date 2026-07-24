# -*- coding: utf-8 -*-
"""FUEL-RESERVE arithmetic tests (increment C of the AZS/fuel track).

Runs against the disposable-SQLite harness (see tests/harness.py); never
touches instance/transport.db. Exercises the acceptance criteria that can be
checked from the report/balance helpers directly:

  1  zero-sum: total expense across all warehouses is identical before/after
     marking (criterion 1);
  2  marking a 4240 L transaction moves exactly 4240 L of expense from the
     source to the reserve, and 4240 L of closing balance the other way
     (criterion 2);
  3  unmarking restores every figure exactly (criterion 3);
  4  a soft-deleted mark affects nothing (criterion 4);
  5  the same warehouse+period gives identical closing on /fuel/report,
     /fuel/balance-report and the dashboard balance map (criterion 5);
  9  an external-fuel transaction is absent from the reattribution maps even
     if a mark row exists (criterion 9);
  11 per-day columns sum to the period expense on both the source warehouse
     and the reserve (criterion 11).
"""
import os
import sys
import unittest
from datetime import date, datetime

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import tests.harness as harness  # noqa: E402
from tests.harness import app  # noqa: E402
from models import (  # noqa: E402
    db, Organization, FuelWarehouse, FuelStation2, FuelInitialBalance,
    FuelTransaction2, FuelTransactionReattribution,
)
import fuel_routes  # noqa: E402

PERIOD_FROM = date(2026, 5, 1)
PERIOD_TO = date(2026, 7, 31)


def _seed():
    """Pakhtasanoattrans (target station) + reserve, both with 2026-05-01
    openings, and a set of July Topaz issues on the Pakhta station including one
    4240 L issue and one external-card (3978) issue."""
    org = Organization(name='Пахтасаноаттранс')
    db.session.add(org)
    db.session.flush()

    pakhta = FuelWarehouse(name='Пахтасаноаттранс', organization_id=org.id)
    reserve = FuelWarehouse(name='Резерв', organization_id=org.id, show_in_ui=1)
    db.session.add_all([pakhta, reserve])
    db.session.flush()

    st = FuelStation2(name='AZS-1', topaz_id=811971, warehouse_id=pakhta.id)
    db.session.add(st)
    db.session.flush()

    db.session.add_all([
        FuelInitialBalance(warehouse_id=pakhta.id, fuel_type='ДТ',
                           quantity=50000, balance_date=PERIOD_FROM),
        FuelInitialBalance(warehouse_id=reserve.id, fuel_type='ДТ',
                           quantity=10429, balance_date=PERIOD_FROM),
    ])

    txns = [
        # (datetime, card, litres, topaz_txn_id)
        (datetime(2026, 7, 1, 8, 0), '100', 60, 'T1'),
        (datetime(2026, 7, 4, 10, 0), '198', 4240, 'T2'),   # the reserve issue
        (datetime(2026, 7, 6, 9, 0), '100', 1926.44, 'T3'),
        (datetime(2026, 7, 10, 12, 0), '100', 500, 'T4'),   # stays Pakhta's
        (datetime(2026, 7, 12, 12, 0), '3978', 300, 'T5'),  # external fuel
    ]
    made = {}
    for dt, card, litres, tid in txns:
        t = FuelTransaction2(station_id=st.id, txn_datetime=dt, card_number=card,
                             fuel_type='ДТ', quantity=litres, topaz_txn_id=tid)
        db.session.add(t)
        made[tid] = t
    db.session.commit()
    return {'pakhta': pakhta.id, 'reserve': reserve.id, 'station': st.id,
            'txns': {k: v.id for k, v in made.items()}}


def _mark(txn_id, target_wh_id, user_id=None):
    m = FuelTransactionReattribution(transaction_id=txn_id,
                                     target_warehouse_id=target_wh_id,
                                     note='test mark')
    db.session.add(m)
    db.session.commit()
    return m.id


def _report_totals():
    data = fuel_routes._collect_fuel_report_data(PERIOD_FROM, PERIOD_TO)
    by_wh = {r['warehouse'].id: r for r in data['warehouse_rows']}
    return data['totals'], by_wh


def _balance_rows():
    rows, date_items, totals = fuel_routes._fuel_report_build_rows(
        PERIOD_FROM, PERIOD_TO, show_zero=True, fuel_type='ДТ')
    by_wh = {r['warehouse_id']: r for r in rows}
    return totals, by_wh, date_items


class FuelReserveArithmeticTest(unittest.TestCase):

    def setUp(self):
        harness.reset_db()
        with app.app_context():
            self.ids = _seed()

    # ── criterion 1: zero-sum ────────────────────────────────────────────
    def test_zero_sum_total_expense_unchanged(self):
        with app.test_request_context('/'):
            t0, wh0 = _report_totals()
            bt0, bwh0, _ = _balance_rows()
            expense_before = round(t0['issued'] + t0['manual'], 2)
            bexpense_before = bt0['expenses']
            closing_before = round(t0['ending'], 2)
            bclosing_before = bt0['closing']

            _mark(self.ids['txns']['T2'], self.ids['reserve'])

            t1, wh1 = _report_totals()
            bt1, bwh1, _ = _balance_rows()
            self.assertAlmostEqual(round(t1['issued'] + t1['manual'], 2),
                                   expense_before, places=2)
            self.assertAlmostEqual(bt1['expenses'], bexpense_before, places=2)
            # closing totals conserved too
            self.assertAlmostEqual(round(t1['ending'], 2), closing_before, places=2)
            self.assertAlmostEqual(bt1['closing'], bclosing_before, places=2)

    # ── criterion 2: exact 4240 move ─────────────────────────────────────
    def test_marking_moves_exactly_4240(self):
        with app.test_request_context('/'):
            _t, wh0 = _report_totals()
            p0 = wh0[self.ids['pakhta']]
            r0 = wh0[self.ids['reserve']]

            _mark(self.ids['txns']['T2'], self.ids['reserve'])

            _t, wh1 = _report_totals()
            p1 = wh1[self.ids['pakhta']]
            r1 = wh1[self.ids['reserve']]

            # Pakhta expense (issued) falls by 4240; reserve expense rises by 4240.
            self.assertAlmostEqual(p0['issued'] - p1['issued'], 4240, places=2)
            self.assertAlmostEqual(r1['issued'] - r0['issued'], 4240, places=2)
            # Pakhta closing rises by 4240; reserve closing falls by 4240.
            self.assertAlmostEqual(p1['ending'] - p0['ending'], 4240, places=2)
            self.assertAlmostEqual(r0['ending'] - r1['ending'], 4240, places=2)
            # «Передано в резерв» reference column: 4240 on Pakhta, 0 on reserve.
            self.assertAlmostEqual(p1['reserve_out'], 4240, places=2)
            self.assertAlmostEqual(r1['reserve_out'], 0, places=2)

            # Same on the balance report.
            _bt, bwh1, _ = _balance_rows()
            self.assertAlmostEqual(bwh1[self.ids['pakhta']]['reserve_out'], 4240, places=2)
            self.assertAlmostEqual(bwh1[self.ids['reserve']]['reserve_out'], 0, places=2)

    # ── criterion 3: unmark restores exactly ─────────────────────────────
    def test_unmark_restores(self):
        with app.test_request_context('/'):
            _t, wh0 = _report_totals()
            snap0 = {k: (round(v['issued'], 2), round(v['ending'], 2), round(v['reserve_out'], 2))
                     for k, v in wh0.items()}

            mark_id = _mark(self.ids['txns']['T2'], self.ids['reserve'])
            m = db.session.get(FuelTransactionReattribution, mark_id)
            m.is_deleted = 1
            m.deleted_at = datetime.utcnow()
            db.session.commit()

            _t, wh1 = _report_totals()
            snap1 = {k: (round(v['issued'], 2), round(v['ending'], 2), round(v['reserve_out'], 2))
                     for k, v in wh1.items()}
            self.assertEqual(snap0, snap1)

    # ── criterion 4: soft-deleted mark affects nothing ───────────────────
    def test_soft_deleted_mark_is_noop(self):
        with app.test_request_context('/'):
            _t, wh0 = _report_totals()
            base = {k: round(v['ending'], 2) for k, v in wh0.items()}

            m = FuelTransactionReattribution(
                transaction_id=self.ids['txns']['T2'],
                target_warehouse_id=self.ids['reserve'],
                note='soft', is_deleted=1, deleted_at=datetime.utcnow())
            db.session.add(m)
            db.session.commit()

            _t, wh1 = _report_totals()
            after = {k: round(v['ending'], 2) for k, v in wh1.items()}
            self.assertEqual(base, after)

    # ── criterion 5: cross-surface closing consistency ───────────────────
    def test_closing_consistency_across_surfaces(self):
        with app.test_request_context('/'):
            _mark(self.ids['txns']['T2'], self.ids['reserve'])

            _t, wh = _report_totals()
            _bt, bwh, _ = _balance_rows()
            balance_map = fuel_routes._fuel_balance_map(
                [self.ids['pakhta'], self.ids['reserve']])

            for wh_id in (self.ids['pakhta'], self.ids['reserve']):
                report_closing = round(wh[wh_id]['ending'], 2)
                balance_closing = round(bwh[wh_id]['closing'], 2)
                dash_closing = round(balance_map[wh_id]['ДТ'], 2)
                self.assertAlmostEqual(report_closing, balance_closing, places=2,
                                       msg=f'report vs balance-report wh {wh_id}')
                self.assertAlmostEqual(report_closing, dash_closing, places=2,
                                       msg=f'report vs dashboard wh {wh_id}')

    # ── criterion 9: external fuel excluded from maps ────────────────────
    def test_external_fuel_excluded_from_maps(self):
        with app.test_request_context('/'):
            # Force a mark on the external-card transaction (bypassing the route).
            _mark(self.ids['txns']['T5'], self.ids['reserve'])
            out_map, in_map = fuel_routes._fuel_reattribution_maps(
                [self.ids['pakhta'], self.ids['reserve']],
                date_from=PERIOD_FROM, date_to=PERIOD_TO)
            # 300 L external issue must not appear on either side.
            self.assertEqual(out_map.get((self.ids['pakhta'], 'ДТ'), 0.0), 0.0)
            self.assertEqual(in_map.get((self.ids['reserve'], 'ДТ'), 0.0), 0.0)

    # ── criterion 11: per-day columns sum to period expense ──────────────
    def test_daily_columns_sum_to_period_expense(self):
        with app.test_request_context('/'):
            _mark(self.ids['txns']['T2'], self.ids['reserve'])
            _mark(self.ids['txns']['T3'], self.ids['reserve'])
            _bt, bwh, _ = _balance_rows()
            for wh_id in (self.ids['pakhta'], self.ids['reserve']):
                row = bwh[wh_id]
                daily_sum = round(sum(d['expenses'] for d in row['daily'].values()), 2)
                self.assertAlmostEqual(daily_sum, row['expenses'], places=2,
                                       msg=f'daily != period expense wh {wh_id}')


if __name__ == '__main__':
    unittest.main()
