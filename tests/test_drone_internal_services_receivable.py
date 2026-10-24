# -*- coding: utf-8 -*-
"""FIN-RECON-002: paid internal services stay receivable (owner decision 23.10.2026).

Drone work on the holding's own fields is a PAID service between
subdivisions: the subdivision signs a «справка» and transfers the money to
Agrocluster. So nothing may drop such a job out of what is to be collected,
and a signed «справка» is not money received.

These tests pin that on the real report functions of drones.py, with a
fixture small enough to check by hand:

  internal card, Справка, nothing received          1 000 000 to collect
  internal card, Справка, received 0 (signed only)    800 000 to collect
  external card, cash, half paid                    2 000 000 - 500 000
  «Тизим корхонаси» row (payment_type internal)       685 064 to collect
  external card, transfer, operator_due 300 000     1 200 000 - 300 000 (memo)

and prove the NEGATIVE as well: switching a card between 'internal' and
'external' moves no total, no debt row and no ageing bucket -- the card type
is a label, not a filter. If somebody later wires customer_type or
payment_type into the debt sums, these tests say so.

Run:
  python -m unittest tests.test_drone_internal_services_receivable -v
"""
import datetime
import unittest

from tests.harness import app, reset_db
from models import (db, DRONE_RECEIVED_KIND_OPERATOR_DUE, DRONE_RECEIVED_KIND_RECEIVED,
                    DroneCustomer, DroneWork)

D = datetime.date(2026, 6, 10)
# (card, payment_type, amount, received, received_kind)
JOBS = (
    ('int', 'transfer', 1000000.0, None, None),
    ('int', 'transfer', 800000.0, 0.0, DRONE_RECEIVED_KIND_RECEIVED),
    ('ext', 'cash', 2000000.0, 500000.0, DRONE_RECEIVED_KIND_RECEIVED),
    (None, 'internal', 685064.0, None, None),
    ('ext', 'transfer', 1200000.0, 300000.0, DRONE_RECEIVED_KIND_OPERATOR_DUE),
)
TOTAL_AMOUNT = 5685064.0
TOTAL_RECEIVED = 800000.0
TOTAL_OUTSTANDING = 4885064.0


def seed():
    reset_db()
    with app.app_context():
        cards = {'int': DroneCustomer(name='Ички бўлинма', customer_type='internal', is_active=True),
                 'ext': DroneCustomer(name='Ташқи фх', customer_type='external', is_active=True)}
        db.session.add_all(cards.values())
        db.session.flush()
        for i, (card, pay, amount, received, kind) in enumerate(JOBS, 1):
            db.session.add(DroneWork(
                period_month='2026-06', work_date_from=D, work_date_to=D,
                drone_customer_id=cards[card].id if card else None,
                customer_raw=cards[card].name if card else 'Тизим корхонаси', area_ha=10.0,
                price_per_ha=amount / 10.0, amount=amount, received_amount=received, received_kind=kind,
                payment_type=pay, source_file='book.xlsx', source_sheet='свод ичи (Тест)', source_row=i))
        db.session.commit()
        return {k: c.id for k, c in cards.items()}


def figures():
    """Every number a person sees about money owed, from the real functions."""
    import drones
    with app.test_request_context('/'):
        totals = drones._drone_works_totals([])
        report = drones._drone_works_report_data([])
        aging = drones._drone_works_debt_aging_data([])
        channels = drones._drone_debt_channels([])
    return {
        'totals': (totals['amount'], totals['received'], totals['outstanding']),
        'debt_rows': sorted((r['key'], round(r['outstanding'], 2)) for r in report['debt_by_customer']['rows']),
        'aging': sorted((str(e['customer_id']), round(e['outstanding'], 2)) for e in aging['worklist']),
        'aging_buckets': [round(b['outstanding'], 2) for b in aging['buckets']],
        'channels': sorted((r['payment'], round(r['outstanding'], 2)) for r in channels['rows']),
        'operator_due_memo': report['operator_due']['amount'],
    }


class PaidInternalServicesTest(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        cls.ids = seed()

    def setUp(self):
        self.f = figures()

    def test_everything_charged_is_to_be_collected(self):
        self.assertEqual(self.f['totals'], (TOTAL_AMOUNT, TOTAL_RECEIVED, TOTAL_OUTSTANDING))
        self.assertAlmostEqual(sum(self.f['aging_buckets']), TOTAL_OUTSTANDING, places=2)

    def test_internal_paid_customer_with_a_balance_owes_it(self):
        rows = dict(self.f['debt_rows'])
        self.assertEqual(rows[str(self.ids['int'])], 1800000.0)
        self.assertEqual(dict(self.f['aging'])[str(self.ids['int'])], 1800000.0)

    def test_a_signed_spravka_with_no_money_is_not_paid(self):
        # 800 000 with received 0 and 1 000 000 with received NULL are both owed in full.
        self.assertIn(('transfer', 1800000.0 + 900000.0), self.f['channels'])

    def test_a_partly_paid_job_owes_the_rest(self):
        self.assertEqual(dict(self.f['debt_rows'])[str(self.ids['ext'])], 1500000.0 + 900000.0)
        self.assertIn(('cash', 1500000.0), self.f['channels'])

    def test_a_tizim_korkhonasi_row_stays_in_the_sums(self):
        self.assertIn(('internal', 685064.0), self.f['channels'])

    def test_operator_due_is_subtracted_and_shown_as_a_memo(self):
        # Existing rule, documented in _drone_works_report_data: «получено» holds both kinds;
        # the memo says how much of it is the operator's declaration, not a collection.
        self.assertEqual(self.f['operator_due_memo'], 300000.0)

    def test_card_type_is_a_label_not_a_filter(self):
        """Negative proof: flipping both cards' type moves no money figure at all."""
        with app.app_context():
            for card in DroneCustomer.query.all():
                card.customer_type = 'external' if card.customer_type == 'internal' else 'internal'
            db.session.commit()
        try:
            self.assertEqual(figures(), self.f)
        finally:
            with app.app_context():
                for card in DroneCustomer.query.all():
                    card.customer_type = 'external' if card.customer_type == 'internal' else 'internal'
                db.session.commit()


if __name__ == '__main__':
    unittest.main()
