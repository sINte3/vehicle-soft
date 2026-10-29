# -*- coding: utf-8 -*-
"""FIN-RECON-002: «Фукаро» is a citizen, not one counterparty (owner, 27.10.2026).

The dispatchers write «Фукаро» for any client without a legal entity, so one
spelling stands for different people. Two halves are pinned here.

THE IMPORT. A row whose whole normalised name is in GENERIC_PAYER_KEYS is
stored with drone_customer_id NULL and payer_unidentified = 1 -- even though
the production directory holds an alias «фукаро» -> #589 -- and no card is
created for it. Named citizens («Жамшид (фукаро)», «Фукаро (Халимов Ж)») are
NOT generic and resolve through their aliases as before. Re-import adds 0.

THE REPORTS. The flagged works are one visible service row whose outstanding
is the sum of each work's own unpaid balance: an overpayment by one citizen
does not reduce another's debt. The collection worklist names each such work
separately. The May work on #589 stays on #589. Every figure still reconciles
with the grand total.

Negative controls: with GENERIC_PAYER_KEYS emptied the same row lands on
#589 again (the import test can tell the two cases apart); and the netted
figure amount - received of the same service row differs from the per-work
outstanding the report shows (the report test can tell them apart).

Run:
  python -m unittest tests.test_drone_payer_unidentified -v
"""
import datetime
import os
import shutil
import sqlite3
import sys
import tempfile
import unittest
from unittest import mock

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TOOLS = os.path.join(REPO_ROOT, 'tools')
for _p in (REPO_ROOT, TOOLS):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import drone_works_fixtures as fx  # noqa: E402
import import_drone_works as idw  # noqa: E402
from tests.harness import app, reset_db, create_admin, login, CSRF  # noqa: E402
from models import db, DroneCustomer, DroneCustomerAlias, DroneWork  # noqa: E402

BOOK = 'Шофиркон ПТЗ Дрон маълумот Июнь.xlsx'
SHEET = 'свод ичи (Тест)'
D = datetime.date(2026, 6, 12)


def work_row(n, raw, amount, received):
    return {
        'period_month': '2026-06', 'work_date_from': D, 'work_date_to': D,
        'date_raw': None, 'operator_raw': 'Тест', 'subdivision_name': None,
        'customer_raw': raw, 'area_ha': 5.0, 'price_per_ha': amount / 5.0,
        'amount': amount, 'other_costs': None, 'received_amount': received,
        'received_kind': 'received', 'payment_type': 'cash',
        'source_file': BOOK, 'source_sheet': SHEET, 'source_row': n, 'note': None,
    }


ROWS = (
    (4, 'Фукаро', 1000000.0, 0.0),             # citizen A
    (5, 'Фукаро', 1000000.0, 1000000.0),       # citizen B, same spelling, paid
    (6, 'Ромитан т. Фукаро', 1190000.0, 0.0),  # owner's second generic example
    (7, ' ФУКАРО ', 600000.0, 0.0),            # same key after the importer's fold
    (8, 'Жамшид (фукаро)', 500000.0, 0.0),     # named citizen, alias exists
    (9, 'Фукаро (Халимов Ж)', 400000.0, 0.0),  # named citizen, alias exists
    (10, 'Ахмад (фукаро)', 300000.0, 0.0),     # named citizen, no alias yet
)


class ImportTests(unittest.TestCase):

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix='payer_unidentified_')
        self.db = fx.build_database(os.path.join(self.tmp, 'books.db'))
        con = sqlite3.connect(self.db)
        for cid, name, key in ((589, 'Фукаро', 'фукаро'), (593, 'Фукаро (Халимов Ж)', 'фукаро (халимов ж)'),
                               (798, 'Жамшид (фукаро)', 'жамшид (фукаро)')):
            con.execute("INSERT INTO drone_customers (id, name, customer_type, is_active) VALUES (?, ?, 'external', 1)",
                        (cid, name))
            con.execute('INSERT INTO drone_customer_aliases (raw_name, normalized_key, drone_customer_id, is_active) '
                        'VALUES (?, ?, ?, 1)', (name, key, cid))
        # The closed May: one work already on the generic card. Must not move.
        con.execute("INSERT INTO drone_works (period_month, customer_raw, drone_customer_id, area_ha, amount, "
                    "payment_type, source_file, source_sheet, source_row) VALUES ('2026-05', 'Фукаро', 589, 5.64, "
                    "1128000, 'cash', 'may.xlsx', 'свод ичи (Май)', 3)")
        con.commit()
        con.close()

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def run_import(self, apply=True):
        con = sqlite3.connect(self.db)
        try:
            idw._require_payer_unidentified(con)
            result = idw.RunResult()
            result.rows = [work_row(*r) for r in ROWS]
            idw.resolve(result, idw.Directory(con))
            counts = idw.apply_rows(con, result, 'test') if apply else None
            return result, counts
        finally:
            con.close()

    def stored(self):
        con = sqlite3.connect(self.db)
        try:
            return {r[0]: r[1:] for r in con.execute(
                'SELECT source_row, drone_customer_id, payer_unidentified, amount, received_amount '
                "FROM drone_works WHERE source_file = ?", (BOOK,))}
        finally:
            con.close()

    def test_generic_rows_get_no_card_and_the_flag(self):
        result, (created, inserted, skipped) = self.run_import()
        rows = self.stored()
        for n in (4, 5, 6, 7):
            self.assertIsNone(rows[n][0], 'row %d' % n)
            self.assertEqual(rows[n][1], 1, 'row %d' % n)
        self.assertEqual(result.payer_unidentified_rows, 4)
        self.assertEqual(inserted, len(ROWS))
        self.assertEqual(skipped, 0)

    def test_different_citizens_keep_their_own_money(self):
        self.run_import()
        rows = self.stored()
        self.assertEqual(rows[4][2:], (1000000.0, 0.0))
        self.assertEqual(rows[5][2:], (1000000.0, 1000000.0))

    def test_named_citizens_are_not_blocked(self):
        result, (created, _i, _s) = self.run_import()
        rows = self.stored()
        self.assertEqual(rows[8][:2], (798, None))
        self.assertEqual(rows[9][:2], (593, None))
        # A named spelling with no alias is an ordinary new spelling: a card, no flag.
        self.assertIsNotNone(rows[10][0])
        self.assertIsNone(rows[10][1])
        self.assertEqual([k for k, _n in result.created_customers], ['ахмад (фукаро)'])
        self.assertEqual(created, 1)

    def test_no_card_is_created_for_a_generic_spelling(self):
        self.run_import()
        con = sqlite3.connect(self.db)
        try:
            names = [r[0] for r in con.execute('SELECT name FROM drone_customers ORDER BY id')]
            aliases = [r[0] for r in con.execute('SELECT normalized_key FROM drone_customer_aliases ORDER BY id')]
        finally:
            con.close()
        self.assertNotIn('Ромитан т. Фукаро', names)
        self.assertNotIn('ромитан т. фукаро', aliases)
        self.assertEqual(names.count('Фукаро'), 1)

    def test_the_may_work_on_589_is_not_touched(self):
        self.run_import()
        con = sqlite3.connect(self.db)
        try:
            row = con.execute("SELECT drone_customer_id, payer_unidentified, amount FROM drone_works "
                              "WHERE source_file = 'may.xlsx'").fetchone()
        finally:
            con.close()
        self.assertEqual(row, (589, None, 1128000.0))

    def test_reimport_adds_nothing(self):
        self.run_import()
        before = self.stored()
        _result, (created, inserted, skipped) = self.run_import()
        self.assertEqual((created, inserted, skipped), (0, 0, len(ROWS)))
        self.assertEqual(self.stored(), before)

    def test_negative_control_without_the_list_the_row_lands_on_589(self):
        """The import test discriminates: the same data, list emptied, gives #589."""
        with mock.patch.object(idw, 'GENERIC_PAYER_KEYS', frozenset()):
            result, _counts = self.run_import(apply=False)
        by_row = {r['source_row']: r['drone_customer_id'] for r in result.rows}
        self.assertEqual(by_row[4], 589)
        self.assertEqual(by_row[7], 589)
        self.assertIn('ромитан т. фукаро', [k for k, _n in result.created_customers])

    def test_the_list_is_exact_keys_only(self):
        self.assertTrue(idw.is_generic_payer('Фукаро'))
        self.assertTrue(idw.is_generic_payer('  фукаро\n'))
        self.assertTrue(idw.is_generic_payer('Ромитан т. Фукаро'))
        for named in ('Жамшид (фукаро)', 'Фукаро (Халимов Ж)', 'Халкобод (фукаро)', 'Ромитан Фукаро',
                      'Фукаро (Шофиркон т.)', 'Фукарою', ''):
            self.assertFalse(idw.is_generic_payer(named), named)

    def test_a_database_without_the_column_is_refused_up_front(self):
        con = sqlite3.connect(':memory:')
        try:
            con.execute('CREATE TABLE drone_works (id INTEGER, received_kind TEXT)')
            with self.assertRaises(idw.ImportPreconditionError) as raised:
                idw._require_payer_unidentified(con)
        finally:
            con.close()
        self.assertEqual(str(raised.exception),
                         'ERROR: drone_works.payer_unidentified is missing. Run '
                         'migrate_drones_works_payer_unidentified_001.py first.')


# ─── Reports ─────────────────────────────────────────────────────────────────

# (raw, card key, flagged, amount, received, period)
LEDGER = (
    ('Фукаро', None, True, 1000000.0, 0.0, '2026-06'),           # A owes 1 000 000
    ('Фукаро', None, True, 1000000.0, 1200000.0, '2026-06'),     # B overpaid 200 000
    ('Фукаро', None, True, 400000.0, 100000.0, '2026-06'),       # C owes 300 000
    ('Ромитан т. Фукаро', None, True, 1190000.0, 1190000.0, '2026-06'),
    ('Жамшид (фукаро)', 'jamshid', False, 500000.0, 0.0, '2026-06'),
    ('Фукаро', 'generic', False, 1128000.0, 0.0, '2026-05'),     # closed May on #589
    ('Янги фх', None, False, 300000.0, 0.0, '2026-06'),          # an alias to add
)
UNIDENTIFIED_OUTSTANDING = 1300000.0   # 1 000 000 + 300 000, per work
UNIDENTIFIED_NETTED = 1100000.0        # what amount - received over the group would say


def seed():
    reset_db()
    with app.app_context():
        cards = {'generic': DroneCustomer(name='Фукаро', customer_type='external', is_active=True),
                 'jamshid': DroneCustomer(name='Жамшид (фукаро)', customer_type='external', is_active=True)}
        db.session.add_all(cards.values())
        db.session.flush()
        db.session.add(DroneCustomerAlias(raw_name='Фукаро', normalized_key='фукаро',
                                          drone_customer_id=cards['generic'].id, is_active=True))
        ids = []
        for i, (raw, card, flag, amount, received, period) in enumerate(LEDGER, 1):
            day = datetime.date(int(period[:4]), int(period[5:]), 12)
            w = DroneWork(period_month=period, work_date_from=day, work_date_to=day,
                          drone_customer_id=cards[card].id if card else None, customer_raw=raw,
                          payer_unidentified=True if flag else None, area_ha=5.0, amount=amount,
                          received_amount=received, payment_type='cash', source_file='b.xlsx',
                          source_sheet=SHEET, source_row=i)
            db.session.add(w)
            db.session.flush()
            ids.append(w.id)
        db.session.commit()
        return {k: c.id for k, c in cards.items()}, ids


class ReportTests(unittest.TestCase):

    def setUp(self):
        self.cards, self.ids = seed()

    def report(self, conds=()):
        import drones
        with app.test_request_context('/'):
            return drones._drone_works_report_data(list(conds)), drones._drone_works_debt_aging_data(list(conds))

    def test_one_service_row_with_debt_per_work(self):
        report, _aging = self.report()
        cut = report['by_customer']
        row = {s['key']: s for s in cut['services']}['unidentified']
        self.assertEqual(row['jobs'], 4)
        self.assertEqual(row['amount'], 3590000.0)
        self.assertEqual(row['received'], 2490000.0)
        self.assertEqual(row['outstanding'], UNIDENTIFIED_OUTSTANDING)
        self.assertEqual(row['overpaid'], 200000.0)
        self.assertIn('200 000', row['note'])
        self.assertTrue(cut['reconciled'])

    def test_negative_control_the_netted_figure_is_different(self):
        """The report test discriminates: amount - received of the same row nets."""
        report, _aging = self.report()
        row = {s['key']: s for s in report['by_customer']['services']}['unidentified']
        self.assertEqual(row['amount'] - row['received'], UNIDENTIFIED_NETTED)
        self.assertNotEqual(UNIDENTIFIED_NETTED, UNIDENTIFIED_OUTSTANDING)

    def test_the_generic_card_keeps_only_its_may_work(self):
        report, _aging = self.report()
        rows = {r['key']: r for r in report['by_customer']['rows']}
        self.assertEqual(rows[str(self.cards['generic'])]['jobs'], 1)
        self.assertEqual(rows[str(self.cards['generic'])]['amount'], 1128000.0)
        self.assertEqual(rows[str(self.cards['jamshid'])]['outstanding'], 500000.0)

    def test_unresolved_is_only_the_spelling_to_alias(self):
        report, _aging = self.report()
        row = {s['key']: s for s in report['by_customer']['services']}['unresolved']
        self.assertEqual((row['jobs'], row['amount']), (1, 300000.0))

    def test_the_debt_table_shows_the_per_work_figure(self):
        report, _aging = self.report()
        debt = {r.get('key'): r for r in report['debt_by_customer']['rows']}
        self.assertEqual(debt['unidentified']['outstanding'], UNIDENTIFIED_OUTSTANDING)

    def test_the_worklist_names_each_work_not_one_person(self):
        _report, aging = self.report()
        lines = [e for e in aging['worklist'] if e['customer_id'] is None and 'Фукаро' in e['label']]
        self.assertEqual(sorted(round(e['outstanding']) for e in lines), [300000, 1000000])
        self.assertTrue(all('№%d' % self.ids[0] in e['label'] or '№%d' % self.ids[2] in e['label']
                            for e in lines))
        self.assertTrue(aging['reconciled'])

    def test_ledger_filters_split_the_three_null_meanings(self):
        import drones
        from werkzeug.datastructures import MultiDict
        with app.test_request_context('/'):
            def ids_for(value):
                conds = drones._drone_work_conditions(
                    drones._drone_works_filters_from_args(MultiDict({'customer_id': str(value)})))
                return sorted(w.id for w in DroneWork.query.filter(*conds).all())
            self.assertEqual(ids_for(drones.DRONE_WORK_CUSTOMER_UNIDENTIFIED_FILTER), self.ids[0:4])
            self.assertEqual(ids_for(drones.DRONE_WORK_UNRESOLVED), [self.ids[6]])
            self.assertEqual(ids_for(drones.DRONE_WORK_CUSTOMER_UNSTATED_FILTER), [])

    def test_ledger_and_customers_pages_render_the_label(self):
        admin = create_admin('payer_admin')
        with app.app_context():
            from models import User
            db.session.get(User, admin).language = 'ru'
            db.session.commit()
        client = app.test_client()
        login(client, admin)
        page = client.get('/drones/works?customer_id=-3').get_data(as_text=True)
        self.assertIn('Плательщик-гражданин не установлен', page)
        customers = client.get('/drones/customers').get_data(as_text=True)
        self.assertIn('Написание для них не добавляется', customers)

    def test_choosing_the_person_on_the_work_ends_not_established(self):
        admin = create_admin('payer_editor')
        client = app.test_client()
        login(client, admin)
        work_id = self.ids[0]
        response = client.post('/drones/works/%d/update' % work_id, data={
            'csrf_token': CSRF, 'customer_raw': 'Фукаро', 'period_month': '2026-06',
            'work_date_from': '2026-06-12', 'area_ha': '5', 'amount': '1000000', 'received_amount': '0',
            'payment_type': 'cash', 'drone_customer_id': str(self.cards['jamshid'])})
        self.assertIn(response.status_code, (302, 303))
        with app.app_context():
            w = db.session.get(DroneWork, work_id)
            self.assertEqual((w.drone_customer_id, w.payer_unidentified), (self.cards['jamshid'], None))
            other = db.session.get(DroneWork, self.ids[1])
            self.assertTrue(other.payer_unidentified)


if __name__ == '__main__':
    unittest.main(verbosity=2)
