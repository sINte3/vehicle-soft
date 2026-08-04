# -*- coding: utf-8 -*-
"""DRONE-WORKS-001: the ledger, the directory, the reports and the workbooks.

The load-bearing assertions here are the reconciliations. Every cut of the
report is built by grouping, and a group-by silently drops NULL -- which is
exactly the rows nobody has resolved yet. That is how the previous drone
system lost 2 036 flights and 2 082 hectares into an invisible bucket. So each
cut is checked to sum back to the same grand total, and each workbook is
re-read with openpyxl and compared cell by cell against the numbers the screen
computed. A screenshot proves nothing and neither does the route returning 200.

The fixture is deliberately small and hand-checkable: 8 jobs, 100.50 ha,
21 700 000 so'm, of which 12 500 000 received. One unresolved customer, one
unresolved operator, one job with no date, one job whose own date falls
outside the month its book is filed under.

Run:
  python -m unittest tests.test_drone_works_screens -v
"""
import datetime
import io
import unittest

from tests.harness import app, reset_db, create_admin, login, CSRF
from models import (db, DroneCustomer, DroneCustomerAlias, DroneFlight,
                    DroneOperator, DroneOperatorAssignment, DroneUnit,
                    DroneWork, Organization, User, ROLE_OPERATOR)


def set_language(user_id, lang):
    """The module is bilingual and the sheet titles follow the user.

    [REASON]: the workbook sheet names ARE the language -- «По заказчикам» in
    Russian and «Буюртмачилар бўйича» in Uzbek. A test that did not pin the
    language would pass or fail depending on the default of the day.
    """
    with app.app_context():
        user = db.session.get(User, user_id)
        user.language = lang
        db.session.commit()

# (period, date_from, date_to, operator, customer, area, price, amount,
#  received, payment, subdivision)
FIXTURE = [
    ('2026-04', datetime.date(2026, 4, 5), 'op1', 'c1', 12.5, 200000,
     2500000, 2500000, 'cash', 'Гарден'),
    ('2026-04', datetime.date(2026, 4, 6), 'op1', 'c1', 10.0, 200000,
     2000000, 0, 'cash', 'Гарден'),
    ('2026-04', datetime.date(2026, 4, 7), 'op2', 'c2', 20.0, 150000,
     3000000, 1000000, 'transfer', 'Когон'),
    ('2026-04', datetime.date(2026, 4, 8), 'op2', None, 5.0, None,
     700000, 0, 'transfer', 'Когон'),
    ('2026-03', datetime.date(2026, 3, 20), None, 'c2', 30.0, 300000,
     9000000, 9000000, 'transfer', 'Когон'),
    ('2026-03', datetime.date(2026, 3, 12), 'op1', 'c3', 8.0, 85633,
     685064, 0, 'internal', None),
    # A book filed as 2026-03 whose own date is in April: the dated row keeps
    # its own date and must land in April, never in March.
    ('2026-03', datetime.date(2026, 4, 25), 'op1', 'c3', 9.0, 200000,
     1800000, 0, 'cash', 'Пешку'),
    # No date at all: it has to stay in the report, in its own service row.
    ('2026-03', None, 'op2', 'c3', 6.0, 200000, 2015000, 0, 'cash', 'Пешку'),
]

TOTAL_JOBS = 8
TOTAL_AREA = 100.5
TOTAL_AMOUNT = 21700064.0
TOTAL_RECEIVED = 12500000.0
TOTAL_OUTSTANDING = TOTAL_AMOUNT - TOTAL_RECEIVED


def seed():
    """Build the fixture. Returns {'op1': id, ..., 'c1': id, ...}."""
    reset_db()
    ids = {}
    with app.app_context():
        for key, name, subdivision in (
                ('op1', 'Файзуллаев Фурқат', 'Гарден'),
                ('op2', 'Хамроев Шохрух', 'Когон')):
            row = DroneOperator(full_name=name, subdivision_name=subdivision)
            db.session.add(row)
            db.session.flush()
            ids[key] = row.id
        for key, name in (('c1', 'Миробод АМТ'), ('c2', 'Дўстлик ФХ'),
                          ('c3', 'Пешку АМТ')):
            row = DroneCustomer(name=name)
            db.session.add(row)
            db.session.flush()
            ids[key] = row.id
            db.session.add(DroneCustomerAlias(
                raw_name=name, normalized_key=name.casefold(),
                drone_customer_id=row.id, is_active=True))
        for index, (period, date_from, operator, customer, area, price,
                    amount, received, payment, subdivision) in enumerate(
                        FIXTURE, 1):
            db.session.add(DroneWork(
                period_month=period,
                work_date_from=date_from,
                work_date_to=date_from,
                drone_operator_id=ids.get(operator),
                operator_raw=operator or 'Ким',
                drone_customer_id=ids.get(customer),
                customer_raw='Хозяйство %d' % index,
                area_ha=area, price_per_ha=price, amount=amount,
                received_amount=received, payment_type=payment,
                subdivision_name=subdivision,
                source_file='book%d.xlsx' % index, source_sheet='свод ичи',
                source_row=index, import_batch='fixture'))
        db.session.commit()
    return ids


def report_data(query='', lang='ru'):
    """The report structure the screen and the workbook are both built from.

    [REASON]: g.lang is set explicitly. Every service row label -- «Заказчик
    не определён», «Дата не указана» -- is language-dependent, and comparing a
    workbook produced for a Russian user against a structure computed with the
    module's Uzbek default would fail on the labels while the numbers agreed.
    """
    from flask import g
    import drones
    with app.test_request_context('/drones/works/reports?' + query):
        g.lang = lang
        filters = drones._drone_works_filters_from_args(
            _request_args(query))
        return drones._drone_works_report_data(
            drones._drone_work_conditions(filters)), filters


def _request_args(query):
    from werkzeug.datastructures import MultiDict
    from urllib.parse import parse_qsl
    return MultiDict(parse_qsl(query))


def sheet_rows(data, title):
    """{first cell: [rest of the row]} for one worksheet of a workbook."""
    from openpyxl import load_workbook
    wb = load_workbook(io.BytesIO(data))
    ws = wb[title]
    rows = list(ws.iter_rows(values_only=True))
    return rows


class DroneWorksScreenTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.ids = seed()
        cls.admin = create_admin('works_admin')
        set_language(cls.admin, 'ru')

    def setUp(self):
        self.client = app.test_client()
        login(self.client, self.admin)

    # ── The ledger ────────────────────────────────────────────────────────
    def test_the_ledger_opens_and_totals_the_whole_filtered_set(self):
        response = self.client.get('/drones/works')
        self.assertEqual(response.status_code, 200)
        body = response.data.decode('utf-8')
        self.assertIn('100.50', body)
        self.assertIn('Дата не указана', body)

    def test_the_period_filter_uses_the_jobs_own_date(self):
        """The April row of a March-labelled book belongs to April."""
        with app.app_context():
            import drones
            with app.test_request_context('/?period=2026-04'):
                filters = drones._drone_works_filters_from_args(
                    _request_args('period=2026-04'))
                conds = drones._drone_work_conditions(filters)
                april = drones._drone_works_totals(conds)
        self.assertEqual(april['jobs'], 5)
        self.assertAlmostEqual(april['area'], 56.5, places=2)

    def test_an_undated_job_falls_into_its_manifest_month(self):
        with app.app_context():
            import drones
            with app.test_request_context('/?period=2026-03'):
                filters = drones._drone_works_filters_from_args(
                    _request_args('period=2026-03'))
                march = drones._drone_works_totals(
                    drones._drone_work_conditions(filters))
        self.assertEqual(march['jobs'], 3)
        self.assertAlmostEqual(march['area'], 44.0, places=2)

    def test_the_two_month_buckets_add_up_to_everything(self):
        """Neither the undated job nor the out-of-month one is lost."""
        with app.app_context():
            import drones
            totals = drones._drone_works_totals([])
        self.assertEqual(totals['jobs'], TOTAL_JOBS)
        self.assertAlmostEqual(totals['area'], TOTAL_AREA, places=2)
        self.assertAlmostEqual(totals['amount'], TOTAL_AMOUNT, places=2)
        self.assertAlmostEqual(totals['received'], TOTAL_RECEIVED, places=2)

    def test_the_unresolved_filters_find_exactly_the_unresolved_rows(self):
        response = self.client.get('/drones/works?customer_id=-1')
        self.assertEqual(response.status_code, 200)
        with app.app_context():
            import drones
            with app.test_request_context('/?customer_id=-1'):
                filters = drones._drone_works_filters_from_args(
                    _request_args('customer_id=-1'))
                totals = drones._drone_works_totals(
                    drones._drone_work_conditions(filters))
        self.assertEqual(totals['jobs'], 1)
        self.assertAlmostEqual(totals['area'], 5.0, places=2)

    def test_a_manual_job_needs_a_period_when_it_has_no_date(self):
        before = self._count()
        response = self.client.post('/drones/works/add', data={
            'csrf_token': CSRF, 'customer_raw': 'Без даты и периода',
            'area_ha': '3', 'payment_type': 'cash'})
        self.assertEqual(response.status_code, 302)
        self.assertEqual(self._count(), before)

    def test_a_manual_job_derives_its_period_from_the_date(self):
        response = self.client.post('/drones/works/add', data={
            'csrf_token': CSRF, 'customer_raw': 'С датой',
            'work_date_from': '2026-05-04', 'area_ha': '3,5',
            'payment_type': 'cash'})
        self.assertEqual(response.status_code, 302)
        with app.app_context():
            row = DroneWork.query.filter_by(customer_raw='С датой').one()
            self.assertEqual(row.period_month, '2026-05')
            self.assertAlmostEqual(row.area_ha, 3.5, places=2)
            self.assertIsNone(row.price_per_ha)
            db.session.delete(row)
            db.session.commit()

    def test_a_blank_money_field_is_null_and_never_zero(self):
        self.client.post('/drones/works/add', data={
            'csrf_token': CSRF, 'customer_raw': 'Пустые деньги',
            'period_month': '2026-06', 'area_ha': '1',
            'payment_type': 'cash', 'price_per_ha': '', 'amount': '  '})
        with app.app_context():
            row = DroneWork.query.filter_by(customer_raw='Пустые деньги').one()
            self.assertIsNone(row.price_per_ha)
            self.assertIsNone(row.amount)
            db.session.delete(row)
            db.session.commit()

    def _count(self):
        with app.app_context():
            return DroneWork.query.count()

    # ── Reconciliation: the point of the whole report ─────────────────────
    def test_every_cut_reconciles_to_the_same_grand_total(self):
        data, _filters = report_data()
        for name in ('by_customer', 'by_operator', 'by_subdivision',
                     'by_month'):
            cut = data[name]
            self.assertTrue(cut['reconciled'], name)
            self.assertEqual(cut['total']['jobs'], TOTAL_JOBS, name)
            self.assertAlmostEqual(cut['total']['area'], TOTAL_AREA,
                                   places=2, msg=name)
            self.assertAlmostEqual(cut['total']['amount'], TOTAL_AMOUNT,
                                   places=2, msg=name)
            self.assertAlmostEqual(cut['total']['received'], TOTAL_RECEIVED,
                                   places=2, msg=name)

    def test_the_reconciliation_check_can_actually_fail(self):
        """A control that never fails is not a control.

        [REASON]: `reconciled` is computed from the same query as the total,
        so it would read True on an empty table and on a broken one alike.
        Feeding it a deliberately wrong grand total proves it discriminates.
        """
        import drones
        with app.test_request_context('/'):
            wrong = drones._drone_works_totals([])
            wrong['area'] += 1.0
            cut = drones._drone_work_cut(
                [], DroneWork.drone_customer_id, wrong, 'x')
        self.assertFalse(cut['reconciled'])

    def test_the_unresolved_rows_are_visible_and_counted_in_the_total(self):
        data, _filters = report_data()
        customer = data['by_customer']
        self.assertIsNotNone(customer['service'])
        self.assertEqual(customer['service']['jobs'], 1)
        self.assertAlmostEqual(customer['service']['area'], 5.0, places=2)
        # ... and the total INCLUDES it
        self.assertEqual(
            customer['total']['jobs'],
            sum(r['jobs'] for r in customer['rows'])
            + customer['service']['jobs'])

        operator = data['by_operator']
        self.assertIsNotNone(operator['service'])
        self.assertEqual(operator['service']['jobs'], 1)
        self.assertAlmostEqual(operator['service']['area'], 30.0, places=2)

    def test_the_month_cut_uses_the_jobs_own_date_and_shows_the_undated_row(self):
        data, _filters = report_data()
        months = {r['key']: r for r in data['by_month']['rows']}
        self.assertEqual(set(months), {'2026-03', '2026-04'})
        # 12.5 + 10 + 20 + 5 + 9 -- including the April row of the March book
        self.assertAlmostEqual(months['2026-04']['area'], 56.5, places=2)
        self.assertAlmostEqual(months['2026-03']['area'], 38.0, places=2)
        service = data['by_month']['service']
        self.assertIsNotNone(service)
        self.assertEqual(service['jobs'], 1)
        self.assertAlmostEqual(service['area'], 6.0, places=2)
        # ... and it says which book's month the undated job belongs to
        self.assertEqual(service['periods'],
                         [{'period': '2026-03', 'jobs': 1, 'area': 6.0}])

    def test_the_subdivision_cut_shows_the_row_with_no_subdivision(self):
        data, _filters = report_data()
        self.assertIsNotNone(data['by_subdivision']['service'])
        self.assertAlmostEqual(data['by_subdivision']['service']['area'], 8.0,
                               places=2)

    def test_the_debt_view_sums_back_to_the_grand_total(self):
        data, _filters = report_data()
        for name in ('debt_by_customer', 'debt_by_operator'):
            debt = data[name]
            rows = list(debt['rows']) + ([debt['rest']] if debt['rest']
                                         else [])
            self.assertAlmostEqual(
                sum(r['outstanding'] for r in rows), TOTAL_OUTSTANDING,
                places=2, msg=name)
            self.assertEqual(sum(r['jobs'] for r in rows), TOTAL_JOBS, name)
            self.assertAlmostEqual(debt['total']['outstanding'],
                                   TOTAL_OUTSTANDING, places=2, msg=name)
        # every listed row really does owe something
        for row in data['debt_by_customer']['rows']:
            self.assertGreater(row['outstanding'], 0)

    def test_a_filter_narrows_every_cut_consistently(self):
        data, _filters = report_data('payment=transfer')
        self.assertEqual(data['totals']['jobs'], 3)
        self.assertAlmostEqual(data['totals']['area'], 55.0, places=2)
        for name in ('by_customer', 'by_operator', 'by_subdivision',
                     'by_month'):
            self.assertTrue(data[name]['reconciled'], name)
            self.assertEqual(data[name]['total']['jobs'], 3, name)

    def test_the_reports_screen_opens(self):
        response = self.client.get('/drones/works/reports')
        self.assertEqual(response.status_code, 200)
        body = response.data.decode('utf-8')
        self.assertIn('Заказчик не определён', body)
        self.assertIn('Оператор не определён', body)
        self.assertIn('Дата не указана', body)
        self.assertIn('Подразделение не указано', body)

    # ── The workbooks, re-read with openpyxl ──────────────────────────────
    def test_the_report_workbook_matches_the_screen_cell_by_cell(self):
        response = self.client.get('/drones/works/reports.xlsx')
        self.assertEqual(response.status_code, 200)
        data, _filters = report_data()
        for title, cut_name in (('По заказчикам', 'by_customer'),
                                ('По операторам', 'by_operator'),
                                ('По подразделениям', 'by_subdivision'),
                                ('По месяцам', 'by_month')):
            rows = sheet_rows(response.data, title)
            cut = data[cut_name]
            expected = len(cut['rows']) + (1 if cut['service'] else 0) + 2
            self.assertEqual(len(rows), expected, title)
            body = rows[1:-1]
            screen = cut['rows'] + ([cut['service']] if cut['service']
                                    else [])
            for sheet_row, screen_row in zip(body, screen):
                self.assertEqual(sheet_row[0], screen_row['label'], title)
                self.assertEqual(sheet_row[1], screen_row['jobs'], title)
                self.assertAlmostEqual(sheet_row[2], screen_row['area'],
                                       places=2, msg=title)
                self.assertAlmostEqual(sheet_row[3], screen_row['amount'],
                                       places=2, msg=title)
                self.assertAlmostEqual(sheet_row[4], screen_row['received'],
                                       places=2, msg=title)
                self.assertAlmostEqual(sheet_row[5], screen_row['outstanding'],
                                       places=2, msg=title)
            total = rows[-1]
            self.assertEqual(total[1], TOTAL_JOBS, title)
            self.assertAlmostEqual(total[2], TOTAL_AREA, places=2, msg=title)
            self.assertAlmostEqual(total[3], TOTAL_AMOUNT, places=2,
                                   msg=title)
            self.assertAlmostEqual(total[4], TOTAL_RECEIVED, places=2,
                                   msg=title)

    def test_every_workbook_cut_reconciles_inside_the_file(self):
        """The file must stand on its own: sum the body, get the total row."""
        response = self.client.get('/drones/works/reports.xlsx')
        for title in ('По заказчикам', 'По операторам', 'По подразделениям',
                      'По месяцам'):
            rows = sheet_rows(response.data, title)
            body = rows[1:-1]
            total = rows[-1]
            self.assertAlmostEqual(sum(r[2] for r in body), total[2],
                                   places=2, msg=title)
            self.assertAlmostEqual(sum(r[3] for r in body), total[3],
                                   places=2, msg=title)
            self.assertEqual(sum(r[1] for r in body), total[1], title)

    def test_the_debt_workbook_matches_the_debt_view(self):
        response = self.client.get('/drones/works/debt.xlsx')
        self.assertEqual(response.status_code, 200)
        data, _filters = report_data()
        rows = sheet_rows(response.data, 'Долги — заказчики')
        body = rows[1:-1]
        screen = list(data['debt_by_customer']['rows'])
        if data['debt_by_customer']['rest']:
            screen.append(data['debt_by_customer']['rest'])
        self.assertEqual(len(body), len(screen))
        for sheet_row, screen_row in zip(body, screen):
            self.assertEqual(sheet_row[0], screen_row['label'])
            self.assertAlmostEqual(sheet_row[4], screen_row['outstanding'],
                                   places=2)
        self.assertAlmostEqual(rows[-1][4], TOTAL_OUTSTANDING, places=2)
        self.assertAlmostEqual(sum(r[4] for r in body), rows[-1][4], places=2)

    def test_the_flat_ledger_workbook_carries_every_row_and_its_provenance(self):
        response = self.client.get('/drones/works.xlsx')
        self.assertEqual(response.status_code, 200)
        rows = sheet_rows(response.data, 'Работы')
        self.assertEqual(len(rows), TOTAL_JOBS + 1)
        header = rows[0]
        self.assertIn('Файл-источник', header)
        self.assertIn('Заказчик — как написано', header)
        area_col = header.index('Гектары')
        self.assertAlmostEqual(sum(r[area_col] for r in rows[1:]), TOTAL_AREA,
                               places=2)
        # the unresolved rows arrive named, not blank
        customer_col = header.index('Заказчик')
        self.assertIn('Заказчик не определён',
                      {r[customer_col] for r in rows[1:]})

    def test_the_workbook_carries_the_filters_that_produced_it(self):
        response = self.client.get('/drones/works/reports.xlsx?period=2026-04')
        rows = sheet_rows(response.data, 'Сводка')
        values = {r[0]: r[1] for r in rows[1:]}
        self.assertEqual(values['Период'], '2026-04')
        self.assertEqual(values['Работ'], 5)
        self.assertAlmostEqual(values['Гектаров'], 56.5, places=2)

    def test_a_filtered_workbook_really_carries_fewer_rows(self):
        """The differential half: the filter has to reach the file."""
        everything = sheet_rows(
            self.client.get('/drones/works.xlsx').data, 'Работы')
        april = sheet_rows(
            self.client.get('/drones/works.xlsx?period=2026-04').data,
            'Работы')
        self.assertEqual(len(everything) - 1, TOTAL_JOBS)
        self.assertEqual(len(april) - 1, 5)


class DroneWorksPermissionTests(unittest.TestCase):
    """@module_required('drones') is enforced at the route, not at the link."""

    @classmethod
    def setUpClass(cls):
        seed()
        with app.app_context():
            user = User(username='no_drones', role=ROLE_OPERATOR,
                        full_name='No Drones')
            user.set_password('x')
            db.session.add(user)
            db.session.commit()
            cls.user_id = user.id

    def test_every_works_route_is_403_without_the_module(self):
        client = app.test_client()
        login(client, self.user_id)
        for url in ('/drones/works', '/drones/customers',
                    '/drones/works/reports', '/drones/works.xlsx',
                    '/drones/works/reports.xlsx', '/drones/works/debt.xlsx'):
            self.assertEqual(client.get(url).status_code, 403, url)

    def test_the_same_routes_are_200_for_an_admin(self):
        """The other half: a 403 everywhere is also what a broken route gives."""
        admin = create_admin('perm_admin')
        set_language(admin, 'ru')
        client = app.test_client()
        login(client, admin)
        for url in ('/drones/works', '/drones/customers',
                    '/drones/works/reports', '/drones/works.xlsx',
                    '/drones/works/reports.xlsx', '/drones/works/debt.xlsx'):
            self.assertEqual(client.get(url).status_code, 200, url)


class DroneWorksUzbekTests(unittest.TestCase):
    """Uzbek is CYRILLIC ONLY. Latin is allowed in product names alone."""

    ALLOWED_LATIN = ('Excel', 'DJI', 'xlsx', 'py', 'tools', 'import',
                     'drone', 'works', 'apply', 'RFID', 'PDF')

    @classmethod
    def setUpClass(cls):
        seed()
        cls.admin = create_admin('uz_admin')
        set_language(cls.admin, 'uz')

    def setUp(self):
        self.client = app.test_client()
        login(self.client, self.admin)

    def test_the_screens_render_in_uzbek(self):
        for url in ('/drones/works', '/drones/customers',
                    '/drones/works/reports'):
            response = self.client.get(url)
            self.assertEqual(response.status_code, 200, url)

    def test_the_service_row_labels_are_cyrillic(self):
        data, _filters = report_data(lang='uz')
        labels = [data[name]['service']['label']
                  for name in ('by_customer', 'by_operator', 'by_subdivision',
                               'by_month')]
        self.assertEqual(
            labels,
            ['Буюртмачи аниқланмаган', 'Оператор аниқланмаган',
             'Бўлинма кўрсатилмаган', 'Сана кўрсатилмаган'])
        for label in labels:
            self.assertFalse(any('a' <= ch.lower() <= 'z' for ch in label),
                             label)

    def test_the_workbook_sheet_names_are_cyrillic(self):
        from openpyxl import load_workbook
        response = self.client.get('/drones/works/reports.xlsx')
        self.assertEqual(response.status_code, 200)
        wb = load_workbook(io.BytesIO(response.data))
        self.assertEqual(
            wb.sheetnames,
            ['Жамланма', 'Буюртмачилар бўйича', 'Операторлар бўйича',
             'Бўлинмалар бўйича', 'Ойлар бўйича', 'Қарз — буюртмачилар',
             'Қарз — операторлар'])
        for name in wb.sheetnames:
            self.assertFalse(any('a' <= ch.lower() <= 'z' for ch in name),
                             name)

    def test_the_uzbek_workbook_carries_the_same_numbers(self):
        """Different words, identical arithmetic."""
        response = self.client.get('/drones/works/reports.xlsx')
        rows = sheet_rows(response.data, 'Буюртмачилар бўйича')
        self.assertEqual(rows[-1][1], TOTAL_JOBS)
        self.assertAlmostEqual(rows[-1][2], TOTAL_AREA, places=2)
        self.assertAlmostEqual(rows[-1][3], TOTAL_AMOUNT, places=2)


class DroneCustomerKeyTests(unittest.TestCase):
    """The screen and the import must agree on what a spelling normalises to.

    [REASON]: they are two implementations of one rule -- drones.py cannot
    import from tools/ at request time. If they drift, the alias the screen
    creates stops matching the alias the import looks up and every re-import
    quietly creates a second customer. This test is the pin.
    """

    def test_the_screen_and_the_import_normalise_identically(self):
        import drones
        import sys
        import os
        tools = os.path.join(os.path.dirname(os.path.dirname(
            os.path.abspath(__file__))), 'tools')
        if tools not in sys.path:
            sys.path.insert(0, tools)
        import import_drone_works as imp
        for value in ('Миробод АМТ', '  Миробод   АМТ  ', 'МИРОБОД амт',
                      'Фукаро', 'Ғиждувон ПТЗ- ФХ', '', 'a  b\tc\nd'):
            self.assertEqual(drones._drone_customer_key(value),
                             imp.customer_key(value), repr(value))


class DroneAssignmentHintTests(unittest.TestCase):
    """The hint screen: read-only, and honest about what it cannot know.

    The fixture is built so the right answer is knowable by hand. In 2026-04
    the ledger gives Fayzullaev 31.5 ha (12.5 + 10.0 + the 9.0 ha row whose
    book is filed as March but whose own date is 25 April) and Khamroev
    25.0 ha; the flights give machine No 1 22.4 ha, machine No 2 30.0 ha and
    5.0 ha with no machine at all.

    So Fayzullaev's closest is No 2 at -4.8 % and Khamroev's is No 1 at
    -10.4 % -- NOT No 2 at +20.0 %, which is the point: the ranking is by the
    absolute relative difference, and a bigger machine is not a better match
    just because it is bigger.
    """

    @classmethod
    def setUpClass(cls):
        cls.ids = seed()
        cls.admin = create_admin('hint_admin')
        set_language(cls.admin, 'ru')
        with app.app_context():
            org = Organization(name='Agrocluster')
            db.session.add(org)
            db.session.flush()
            units = {}
            for number in (1, 2):
                unit = DroneUnit(number=number, organization_id=org.id)
                db.session.add(unit)
                db.session.flush()
                units[number] = unit.id
            cls.units = units
            # April flights, stored UTC. 19:30 UTC on 31 March is already
            # 1 April for the operators, so the fixture keeps well inside the
            # month rather than testing the boundary twice.
            flights = [
                (1, datetime.datetime(2026, 4, 5, 6, 0), 12.4),
                (1, datetime.datetime(2026, 4, 6, 6, 0), 10.0),
                (2, datetime.datetime(2026, 4, 7, 6, 0), 30.0),
                (None, datetime.datetime(2026, 4, 8, 6, 0), 5.0),
                # March, must not leak into the April comparison
                (1, datetime.datetime(2026, 3, 5, 6, 0), 500.0),
            ]
            for index, (number, started, area) in enumerate(flights, 1):
                db.session.add(DroneFlight(
                    dji_flight_id=index,
                    drone_unit_id=units.get(number),
                    nickname_raw='n%d' % index,
                    started_at=started, area_ha=area,
                    raw_json='{}'))
            db.session.commit()

    def setUp(self):
        self.client = app.test_client()
        login(self.client, self.admin)

    def hints(self, month='2026-04'):
        import drones
        from flask import g
        with app.test_request_context('/?month=' + month):
            g.lang = 'ru'
            return drones._drone_assignment_hints(month)

    def test_the_closest_machine_is_the_closest_by_relative_difference(self):
        data = self.hints()
        by_name = {r['name']: r for r in data['rows']}
        faiz = by_name['Файзуллаев Фурқат']
        self.assertAlmostEqual(faiz['area'], 31.5, places=2)
        self.assertEqual(faiz['best']['number'], 2)
        self.assertAlmostEqual(faiz['best']['area'], 30.0, places=2)
        self.assertAlmostEqual(faiz['best']['delta'], -4.7619, places=3)
        self.assertEqual(faiz['second']['number'], 1)
        self.assertAlmostEqual(faiz['second']['delta'], -28.8889, places=3)

        khamroev = by_name['Хамроев Шохрух']
        self.assertAlmostEqual(khamroev['area'], 25.0, places=2)
        # -10.4 beats +20.0 on absolute relative difference, which is the
        # whole ranking rule -- a bigger machine is not a better match.
        self.assertEqual(khamroev['best']['number'], 1)
        self.assertAlmostEqual(khamroev['best']['delta'], -10.4, places=2)
        self.assertEqual(khamroev['second']['number'], 2)
        self.assertAlmostEqual(khamroev['second']['delta'], 20.0, places=2)

    def test_the_month_boundary_holds(self):
        """March's 500 ha must not appear anywhere in the April comparison."""
        data = self.hints()
        self.assertAlmostEqual(data['machine_area'], 57.4, places=2)
        for machine in data['pool']:
            self.assertLess(machine['area'], 100.0)

    def test_flights_with_no_machine_are_shown_and_never_a_candidate(self):
        data = self.hints()
        self.assertEqual(data['unattributed']['flights'], 1)
        self.assertAlmostEqual(data['unattributed']['area'], 5.0, places=2)
        self.assertEqual({m['number'] for m in data['pool']}, {1, 2})

    def test_a_machine_already_assigned_to_that_operator_is_excluded(self):
        """The differential half: it is a candidate until the assignment exists."""
        before = self.hints()
        faiz_id = self.ids['op1']
        self.assertEqual(
            [r for r in before['rows']
             if r['operator_id'] == faiz_id][0]['best']['number'], 2)
        with app.app_context():
            row = DroneOperatorAssignment(
                operator_id=faiz_id, drone_unit_id=self.units[2],
                date_from=datetime.date(2026, 4, 1),
                date_to=datetime.date(2026, 4, 30))
            db.session.add(row)
            db.session.commit()
            assignment_id = row.id
        try:
            after = self.hints()
            faiz = [r for r in after['rows']
                    if r['operator_id'] == faiz_id][0]
            self.assertEqual(faiz['best']['number'], 1)
            self.assertIsNone(faiz['second'])
            self.assertEqual(faiz['already'], [2])
            # ... and the machine is still a candidate for the OTHER operator,
            # marked with who holds it, because two operators genuinely share
            # a machine in the source data.
            khamroev = [r for r in after['rows']
                        if r['operator_id'] == self.ids['op2']][0]
            candidates = [c for c in (khamroev['best'], khamroev['second'])
                          if c]
            taken = [c for c in candidates if c['number'] == 2]
            self.assertEqual(len(taken), 1)
            self.assertEqual(taken[0]['taken_by'], ['Файзуллаев Фурқат'])
        finally:
            with app.app_context():
                db.session.delete(db.session.get(DroneOperatorAssignment,
                                                 assignment_id))
                db.session.commit()

    def test_works_with_no_operator_are_reported_not_hidden(self):
        data = self.hints('2026-03')
        self.assertEqual(data['unresolved']['jobs'], 1)
        self.assertAlmostEqual(data['unresolved']['area'], 30.0, places=2)

    def test_the_screen_opens_and_says_loudly_that_it_only_suggests(self):
        response = self.client.get('/drones/works/assignment-hints')
        self.assertEqual(response.status_code, 200)
        body = response.data.decode('utf-8')
        self.assertIn('ЭТО ПОДСКАЗКА, А НЕ НАЗНАЧЕНИЕ', body)
        self.assertIn('довод', body.lower())

    def test_the_screen_writes_nothing(self):
        """Read-only means read-only: nothing exists that was not there."""
        with app.app_context():
            before = (DroneOperatorAssignment.query.count(),
                      DroneWork.query.count())
        self.client.get('/drones/works/assignment-hints?month=2026-04')
        self.client.get('/drones/works/assignment-hints?month=2026-03')
        with app.app_context():
            after = (DroneOperatorAssignment.query.count(),
                     DroneWork.query.count())
        self.assertEqual(before, after)

    def test_it_is_403_without_the_module(self):
        with app.app_context():
            user = User(username='no_drones_hints', role=ROLE_OPERATOR,
                        full_name='No Drones')
            user.set_password('x')
            db.session.add(user)
            db.session.commit()
            user_id = user.id
        client = app.test_client()
        login(client, user_id)
        self.assertEqual(
            client.get('/drones/works/assignment-hints').status_code, 403)


if __name__ == '__main__':
    unittest.main(verbosity=2)
