# -*- coding: utf-8 -*-
"""DRONE-CLOSE-001: who has not handed in their book.

The load-bearing assertion in this file is the RECONCILIATION INVARIANT of
section 4: summed across subdivisions, every month on the closing screen must
equal the same month in `_drone_works_flights_reconcile_data()` -- works,
ledger hectares, flights and flight hectares, all four. Two screens quoting
different totals for the same month destroy both.

**The fixture is built so a naive implementation fails it.** It carries a work
whose operator is unresolved, a work with neither operator nor subdivision, a
work whose operator exists but carries no subdivision of their own, and a
flight with no machine. An implementation that inner-joins operators and
drops the unmatched rows renders a plausible-looking screen and fails here by
exactly those rows -- which is the only way anybody would ever find it.

Run:
  python -m unittest tests.test_drone_closing_001 -v
"""
import datetime
import os
import re
import sys
import unittest
from html.parser import HTMLParser

from tests.harness import app, reset_db, create_admin, create_org, login
from models import (db, DroneFlight, DroneOperator, DroneUnit, DroneWork,
                    User, ROLE_OPERATOR)

import drones

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

KOGON = 'Когон ПТЗ'
PESHKU = 'Пешку ПТЗ'
GIJDUVON = 'Ғиждувон ПТЗ'


def set_language(user_id, lang):
    """The page language comes from users.language through g.lang."""
    with app.app_context():
        user = db.session.get(User, user_id)
        user.language = lang
        db.session.commit()


class Fixture(object):
    """Ids of everything the fixture planted, filled by build_fixture()."""


def build_fixture():
    """Several subdivisions, both unresolved buckets, and all four states.

    Hand-checkable on purpose. The month totals it must reproduce are:

      2026-06   0 works /    0.00 ha | 2 flights / 500.00 ha
      2026-05   1 work  /   20.00 ha | 0 flights /   0.00 ha
      2026-04   5 works /  177.00 ha | 4 flights / 173.00 ha
      2026-03   1 work  /   12.00 ha | 1 flight  /  11.00 ha
    """
    reset_db()
    fixture = Fixture()
    fixture.org = create_org('Бухоро Агрокластер')
    fixture.admin = create_admin('closing_admin')
    set_language(fixture.admin, 'ru')
    with app.app_context():
        operators = [
            DroneOperator(full_name='Хамроев Шохрух',
                          subdivision_name=KOGON),
            DroneOperator(full_name='Имомов Бехзод',
                          subdivision_name=PESHKU),
            # An operator the fleet knows but whose subdivision was never
            # filled in. Criterion: the work's own subdivision must answer.
            DroneOperator(full_name='Оператор без бўлими',
                          subdivision_name=None),
        ]
        units = [
            DroneUnit(number=1, organization_id=fixture.org,
                      subdivision_name=KOGON),
            DroneUnit(number=2, organization_id=fixture.org,
                      subdivision_name=PESHKU),
            # A machine whose own subdivision is blank: «unknown», never
            # «no machine» -- the machine is known and the fleet card is not.
            DroneUnit(number=3, organization_id=fixture.org,
                      subdivision_name=None),
        ]
        db.session.add_all(operators + units)
        db.session.commit()
        fixture.op_kogon, fixture.op_peshku, fixture.op_nosub = [
            o.id for o in operators]
        fixture.unit_kogon, fixture.unit_peshku, fixture.unit_nosub = [
            u.id for u in units]

        counter = [0]

        def work(month, area, operator=None, subdivision=None, day=None):
            counter[0] += 1
            db.session.add(DroneWork(
                period_month=month,
                work_date_from=(datetime.date(int(month[:4]), int(month[5:]),
                                              day) if day else None),
                drone_operator_id=operator, subdivision_name=subdivision,
                customer_raw='ФХ %d' % counter[0], area_ha=area,
                payment_type='cash', source_file='книга.xlsx',
                source_sheet='свод ичи', source_row=counter[0]))

        flights = [0]

        def flight(when, area, unit=None):
            flights[0] += 1
            db.session.add(DroneFlight(
                dji_flight_id=900000 + flights[0], drone_unit_id=unit,
                nickname_raw='fixture', started_at=when, area_ha=area,
                raw_json='{}'))

        # 2026-04: every attribution path at once.
        work('2026-04', 100.0, operator=fixture.op_kogon, day=5)
        # Operator UNRESOLVED, subdivision on the work row -> Пешку.
        work('2026-04', 50.0, operator=None, subdivision=PESHKU, day=6)
        # NEITHER -> the unknown bucket.
        work('2026-04', 10.0, operator=None, subdivision=None, day=7)
        # Operator resolved but with no subdivision; the work row has one.
        work('2026-04', 7.0, operator=fixture.op_nosub,
             subdivision=KOGON, day=8)
        # A work with no date at all: it belongs to its period_month.
        work('2026-04', 10.0, operator=fixture.op_peshku)
        flight(datetime.datetime(2026, 4, 5, 3), 100.0, fixture.unit_kogon)
        flight(datetime.datetime(2026, 4, 6, 3), 40.0, fixture.unit_peshku)
        # No machine -> its own bucket row.
        flight(datetime.datetime(2026, 4, 7, 3), 33.0, None)
        # Machine known, its subdivision blank -> the unknown bucket.
        flight(datetime.datetime(2026, 4, 8, 3), 0.0, fixture.unit_nosub)

        # 2026-05: a book with no flights behind it -- the fourth state.
        work('2026-05', 20.0, operator=fixture.op_kogon, day=2)

        # 2026-06: flew and handed in nothing. The point of the screen.
        flight(datetime.datetime(2026, 6, 1, 3), 400.0, fixture.unit_kogon)
        flight(datetime.datetime(2026, 6, 2, 3), 100.0, fixture.unit_peshku)

        # 2026-03: an ordinary month that agrees, so «covered» is not rare.
        work('2026-03', 12.0, operator=fixture.op_peshku, day=11)
        flight(datetime.datetime(2026, 3, 11, 3), 11.0, fixture.unit_peshku)
        db.session.commit()
    return fixture


def row_by_label(data, label):
    for row in data['rows']:
        if row['label'] == label:
            return row
    return None


def cell_of(data, label, month):
    row = row_by_label(data, label)
    if row is None:
        return None
    for cell in row['cells']:
        if cell['month'] == month:
            return cell
    return None


# ─── 1. The invariant ────────────────────────────────────────────────────────

class DroneClosingReconciliationTests(unittest.TestCase):
    """Section 4. The only thing that makes the screen quotable."""

    @classmethod
    def setUpClass(cls):
        cls.fixture = build_fixture()

    def data(self, window=0):
        with app.test_request_context('/drones/reports/closing'):
            return drones._drone_closing_data(window)

    def reconcile(self):
        with app.test_request_context('/drones/reports/reconcile'):
            return drones._drone_works_flights_reconcile_data()

    def test_every_month_equals_the_reconcile_report(self):
        """All four numbers, month by month, against the existing helper."""
        closing = self.data()
        reference = dict((row['month'], row)
                         for row in self.reconcile()['rows'])
        self.assertEqual(sorted(closing['months']), sorted(reference))
        self.assertGreaterEqual(len(closing['months']), 4)
        for month in closing['months']:
            mine = closing['month_totals'][month]
            theirs = reference[month]
            self.assertEqual(mine['ledger_jobs'], theirs['ledger_jobs'],
                             '%s works' % month)
            self.assertAlmostEqual(mine['ledger_area'], theirs['ledger_area'],
                                   places=6, msg='%s ledger ha' % month)
            self.assertEqual(mine['flights'], theirs['flights'],
                             '%s flights' % month)
            self.assertAlmostEqual(mine['flight_area'], theirs['flight_area'],
                                   places=6, msg='%s flight ha' % month)

    def test_the_fixture_actually_contains_the_rows_that_break_a_naive_join(self):
        """A control: the invariant above is only worth anything on this data.

        [REASON]: if the fixture had no unresolved work and no machineless
        flight, an implementation that inner-joins operators and units would
        pass every assertion in this file. These counts are what make it fail.
        """
        with app.app_context():
            unresolved = DroneWork.query.filter(
                DroneWork.drone_operator_id.is_(None)).count()
            orphan = DroneFlight.query.filter(
                DroneFlight.drone_unit_id.is_(None)).count()
            no_subdivision_operator = DroneOperator.query.filter(
                DroneOperator.subdivision_name.is_(None)).count()
        self.assertEqual(unresolved, 2)
        self.assertEqual(orphan, 1)
        self.assertEqual(no_subdivision_operator, 1)

    def test_an_inner_join_would_lose_exactly_those_rows(self):
        """The negative control, computed rather than asserted in prose.

        The naive query is written out here and run against the same fixture;
        it must disagree with the reconcile report, and it must disagree by
        the unresolved rows. If it ever agreed, the invariant test above
        would be proving nothing.
        """
        from sqlalchemy import func
        with app.test_request_context('/drones/reports/closing'):
            month_expr = drones._drone_work_month_expr()
            naive = dict(
                (row[0], (row[1], float(row[2] or 0.0)))
                for row in db.session.query(
                    month_expr, func.count(DroneWork.id),
                    func.coalesce(func.sum(DroneWork.area_ha), 0.0))
                .join(DroneOperator,
                      DroneWork.drone_operator_id == DroneOperator.id)
                .group_by(month_expr).all())
            reference = dict((row['month'], row)
                             for row in self.reconcile()['rows'])
        differing = [m for m in reference
                     if naive.get(m, (0, 0.0))[0] != reference[m]['ledger_jobs']]
        self.assertNotEqual(differing, [], 'the naive join agreed -- the '
                                           'fixture no longer discriminates')
        self.assertIn('2026-04', differing)

    def test_the_rows_sum_to_the_month_totals(self):
        """Internal consistency: the matrix adds up to its own footer."""
        closing = self.data()
        for month in closing['months']:
            for field in ('ledger_jobs', 'ledger_area', 'flights',
                          'flight_area'):
                summed = sum(cell[field] for row in closing['rows']
                             for cell in row['cells']
                             if cell['month'] == month)
                self.assertAlmostEqual(summed,
                                       closing['month_totals'][month][field],
                                       places=6, msg='%s %s' % (month, field))

    def test_the_grand_total_is_the_sum_of_the_month_totals(self):
        closing = self.data()
        for field in ('ledger_jobs', 'ledger_area', 'flights', 'flight_area'):
            self.assertAlmostEqual(
                sum(closing['month_totals'][m][field]
                    for m in closing['months']),
                closing['total'][field], places=6, msg=field)

    def test_the_month_totals_are_the_hand_computed_numbers(self):
        """The fixture's own arithmetic, so a silent shift is visible."""
        closing = self.data()
        expected = {
            '2026-06': (0, 0.00, 2, 500.00),
            '2026-05': (1, 20.00, 0, 0.00),
            '2026-04': (5, 177.00, 4, 173.00),
            '2026-03': (1, 12.00, 1, 11.00),
        }
        self.assertEqual(sorted(closing['months']), sorted(expected))
        for month, (jobs, ledger, flights, flight_area) in expected.items():
            total = closing['month_totals'][month]
            self.assertEqual(total['ledger_jobs'], jobs, month)
            self.assertAlmostEqual(total['ledger_area'], ledger, places=2,
                                   msg=month)
            self.assertEqual(total['flights'], flights, month)
            self.assertAlmostEqual(total['flight_area'], flight_area,
                                   places=2, msg=month)


# ─── 2. Attribution ──────────────────────────────────────────────────────────

class DroneClosingAttributionTests(unittest.TestCase):
    """Which bucket each row lands in, and why."""

    @classmethod
    def setUpClass(cls):
        cls.fixture = build_fixture()

    def data(self, window=0):
        with app.test_request_context('/drones/reports/closing'):
            return drones._drone_closing_data(window)

    def labels(self):
        with app.test_request_context('/drones/reports/closing'):
            return drones._drone_closing_labels()

    def test_a_work_resolved_through_its_operator_lands_there(self):
        cell = cell_of(self.data(), KOGON, '2026-04')
        self.assertIsNotNone(cell)
        # 100.00 from the resolved operator plus 7.00 from the operator with
        # no subdivision whose work row names Когон.
        self.assertAlmostEqual(cell['ledger_area'], 107.00, places=2)

    def test_an_unresolved_operator_falls_back_to_the_works_subdivision(self):
        """Criterion 3: NOT «не определено» when the work row knows."""
        cell = cell_of(self.data(), PESHKU, '2026-04')
        self.assertIsNotNone(cell)
        # 50.00 from the unresolved-operator work plus 10.00 from the undated
        # work of the Пешку operator.
        self.assertAlmostEqual(cell['ledger_area'], 60.00, places=2)
        self.assertEqual(cell['ledger_jobs'], 2)
        unknown = self.labels()[drones.DRONE_CLOSING_SUBDIVISION_UNKNOWN]
        self.assertAlmostEqual(
            cell_of(self.data(), unknown, '2026-04')['ledger_area'],
            10.00, places=2)

    def test_a_work_with_neither_lands_in_the_unknown_bucket(self):
        """Criterion 4, and it is COUNTED there, not merely displayed."""
        unknown = self.labels()[drones.DRONE_CLOSING_SUBDIVISION_UNKNOWN]
        cell = cell_of(self.data(), unknown, '2026-04')
        self.assertIsNotNone(cell)
        self.assertEqual(cell['ledger_jobs'], 1)
        self.assertAlmostEqual(cell['ledger_area'], 10.00, places=2)
        row = row_by_label(self.data(), unknown)
        self.assertTrue(row['is_bucket'])

    def test_an_operator_without_a_subdivision_uses_the_works_own(self):
        """The case the task's wording leaves open, pinned deliberately.

        «Fall back only when the operator is unresolved» would send this work
        to «не определено» even though its own row names Когон ПТЗ. The
        implementation COALESCEs instead, so no subdivision the data knows is
        thrown away. On the real fleet all 17 operators carry a subdivision,
        so the two readings differ on no production row -- but they differ
        here, and the choice is written down rather than left to be
        rediscovered.
        """
        cell = cell_of(self.data(), KOGON, '2026-04')
        self.assertEqual(cell['ledger_jobs'], 2)
        unknown = self.labels()[drones.DRONE_CLOSING_SUBDIVISION_UNKNOWN]
        self.assertEqual(
            cell_of(self.data(), unknown, '2026-04')['ledger_jobs'], 1)

    def test_flights_with_no_machine_get_their_own_row(self):
        """Criterion 5: their own row, and inside the totals."""
        no_machine = self.labels()[drones.DRONE_CLOSING_NO_MACHINE]
        row = row_by_label(self.data(), no_machine)
        self.assertIsNotNone(row, 'the no-machine row is missing')
        self.assertTrue(row['is_bucket'])
        cell = cell_of(self.data(), no_machine, '2026-04')
        self.assertEqual(cell['flights'], 1)
        self.assertAlmostEqual(cell['flight_area'], 33.00, places=2)
        # Included in the month total, not counted beside it.
        self.assertEqual(
            self.data()['month_totals']['2026-04']['flights'], 4)

    def test_a_machine_with_no_subdivision_is_unknown_not_no_machine(self):
        """The two buckets are different facts and stay apart."""
        labels = self.labels()
        unknown = cell_of(self.data(), labels[
            drones.DRONE_CLOSING_SUBDIVISION_UNKNOWN], '2026-04')
        no_machine = cell_of(self.data(), labels[
            drones.DRONE_CLOSING_NO_MACHINE], '2026-04')
        self.assertEqual(unknown['flights'], 1)
        self.assertEqual(no_machine['flights'], 1)
        self.assertAlmostEqual(unknown['flight_area'], 0.00, places=2)
        self.assertAlmostEqual(no_machine['flight_area'], 33.00, places=2)

    def test_the_buckets_sort_last(self):
        """A bucket is not a person and must not head the list of people."""
        data = self.data()
        labels = set(self.labels().values())
        keys = [row['label'] for row in data['rows']]
        real = [i for i, label in enumerate(keys) if label not in labels]
        bucket = [i for i, label in enumerate(keys) if label in labels]
        self.assertTrue(real and bucket)
        self.assertLess(max(real), min(bucket), keys)


# ─── 3. Cell states ──────────────────────────────────────────────────────────

class DroneClosingCellStateTests(unittest.TestCase):
    """Criterion 2: three states the task names, plus the fourth."""

    @classmethod
    def setUpClass(cls):
        cls.fixture = build_fixture()

    def data(self, window=0):
        with app.test_request_context('/drones/reports/closing'):
            return drones._drone_closing_data(window)

    def test_flew_and_billed_is_a_coverage_figure(self):
        cell = cell_of(self.data(), KOGON, '2026-04')
        self.assertEqual(cell['state'], drones.DRONE_CLOSING_COVERED)
        self.assertAlmostEqual(cell['coverage'], 107.00, places=2)

    def test_flew_and_handed_in_nothing_is_its_own_state(self):
        cell = cell_of(self.data(), KOGON, '2026-06')
        self.assertEqual(cell['state'], drones.DRONE_CLOSING_NO_BOOK)
        # NEVER a coverage of 0.0: that reads as a measurement, not an absence.
        self.assertIsNone(cell['coverage'])
        self.assertEqual(cell['ledger_jobs'], 0)
        self.assertAlmostEqual(cell['flight_area'], 400.00, places=2)

    def test_did_not_fly_is_neither(self):
        cell = cell_of(self.data(), PESHKU, '2026-05')
        self.assertEqual(cell['state'], drones.DRONE_CLOSING_IDLE)
        self.assertIsNone(cell['coverage'])
        self.assertEqual(cell['flights'], 0)
        self.assertEqual(cell['ledger_jobs'], 0)

    def test_billed_with_no_flights_is_the_fourth_state(self):
        cell = cell_of(self.data(), KOGON, '2026-05')
        self.assertEqual(cell['state'], drones.DRONE_CLOSING_BOOK_ONLY)
        self.assertIsNone(cell['coverage'])
        self.assertEqual(cell['ledger_jobs'], 1)
        self.assertEqual(cell['flights'], 0)

    def test_all_four_states_occur_in_this_fixture(self):
        """Otherwise the four assertions above could be testing three."""
        seen = set(cell['state'] for row in self.data()['rows']
                   for cell in row['cells'])
        self.assertEqual(seen, {drones.DRONE_CLOSING_COVERED,
                                drones.DRONE_CLOSING_NO_BOOK,
                                drones.DRONE_CLOSING_BOOK_ONLY,
                                drones.DRONE_CLOSING_IDLE})

    def test_the_colour_is_the_reconcile_reports_rule(self):
        """Not «a rule like it» -- the same function, on the same bands."""
        self.assertEqual(drones._drone_coverage_flag(49.9), 'is-danger-row')
        # 50.0 is not below 50, so it is not danger -- but it is below 90,
        # so it is still a warning. The bands are half-open and stay so.
        self.assertEqual(drones._drone_coverage_flag(50.0), 'is-warning-row')
        self.assertEqual(drones._drone_coverage_flag(89.9), 'is-warning-row')
        self.assertEqual(drones._drone_coverage_flag(100.0), '')
        self.assertEqual(drones._drone_coverage_flag(110.0), '')
        self.assertEqual(drones._drone_coverage_flag(110.1), 'is-warning-row')
        self.assertEqual(drones._drone_coverage_flag(200.1), 'is-danger-row')
        self.assertEqual(drones._drone_coverage_flag(None), '')
        self.assertEqual(drones.DRONE_COVERAGE_DANGER_LOW, 50.0)
        self.assertEqual(drones.DRONE_COVERAGE_DANGER_HIGH, 200.0)
        self.assertEqual(drones.DRONE_COVERAGE_WARNING_LOW, 90.0)
        self.assertEqual(drones.DRONE_COVERAGE_WARNING_HIGH, 110.0)

    def test_the_reconcile_report_calls_the_same_function(self):
        """A copy of the thresholds would drift; this proves there is none."""
        import inspect
        source = inspect.getsource(drones._drone_works_flights_reconcile_data)
        self.assertIn('_drone_coverage_flag', source)
        for literal in ('50.0', '200.0', '90.0', '110.0'):
            self.assertNotIn(literal, source, literal)

    def test_the_flew_no_book_list_is_the_no_book_cells_by_hectares(self):
        data = self.data()
        no_book = [(row['label'], cell['month'], cell['flight_area'])
                   for row in data['rows'] for cell in row['cells']
                   if cell['state'] == drones.DRONE_CLOSING_NO_BOOK]
        self.assertEqual(len(data['missing']), len(no_book))
        areas = [item['flight_area'] for item in data['missing']]
        self.assertEqual(areas, sorted(areas, reverse=True))
        self.assertAlmostEqual(data['missing'][0]['flight_area'], 400.00,
                               places=2)
        self.assertAlmostEqual(data['missing_area'], sum(areas), places=6)


# ─── 4. Empty data and the window ────────────────────────────────────────────

class DroneClosingEmptyTests(unittest.TestCase):
    """Criterion 6: no traceback and no division by zero, anywhere."""

    @classmethod
    def setUpClass(cls):
        reset_db()
        cls.admin = create_admin('closing_empty_admin')
        set_language(cls.admin, 'ru')

    def setUp(self):
        self.client = app.test_client()
        login(self.client, self.admin)

    def test_the_data_function_on_a_completely_empty_database(self):
        with app.test_request_context('/drones/reports/closing'):
            data = drones._drone_closing_data(0)
        self.assertEqual(data['months'], [])
        self.assertEqual(data['rows'], [])
        self.assertEqual(data['missing'], [])
        self.assertIsNone(data['total']['coverage'])
        self.assertEqual(data['total']['flight_area'], 0.0)
        self.assertEqual(data['months_available'], 0)

    def test_the_screen_renders_on_an_empty_database(self):
        for window in ('', '12', '24', '0', 'сентябрь', '-3'):
            response = self.client.get('/drones/reports/closing?window=%s'
                                       % window)
            self.assertEqual(response.status_code, 200, window)
            body = response.data.decode('utf-8')
            self.assertIn('Ни ведомостей, ни вылетов', body)
            self.assertIn('Пропусков нет', body)

    def test_a_month_with_neither_side_divides_by_nothing(self):
        """A subdivision row that is idle in a month, computed not rendered."""
        cell = drones._drone_closing_cell(0, 0.0, 0, 0.0)
        self.assertEqual(cell['state'], drones.DRONE_CLOSING_IDLE)
        self.assertIsNone(cell['coverage'])
        self.assertEqual(cell['flag'], '')
        # Flights present, ledger empty: the divisor is fine, the numerator
        # is the absence -- and it still must not become 0.0 %.
        cell = drones._drone_closing_cell(0, 0.0, 3, 12.5)
        self.assertEqual(cell['state'], drones.DRONE_CLOSING_NO_BOOK)
        self.assertIsNone(cell['coverage'])
        # Ledger present, flights empty: the divisor IS zero. No exception.
        cell = drones._drone_closing_cell(2, 8.0, 0, 0.0)
        self.assertEqual(cell['state'], drones.DRONE_CLOSING_BOOK_ONLY)
        self.assertIsNone(cell['coverage'])

    def test_an_unknown_window_falls_back_to_the_default(self):
        for value in (None, '', 'все', '7', '-1', '1000', 'NaN'):
            self.assertEqual(drones._drone_closing_window(value),
                             drones.DRONE_CLOSING_DEFAULT_WINDOW, repr(value))
        for value in ('12', '24', '0', 12, 24, 0):
            self.assertIn(drones._drone_closing_window(value),
                          drones.DRONE_CLOSING_WINDOWS, repr(value))


class DroneClosingWindowTests(unittest.TestCase):
    """The window truncates months and says by how much."""

    @classmethod
    def setUpClass(cls):
        cls.fixture = build_fixture()

    def data(self, window):
        with app.test_request_context('/drones/reports/closing'):
            return drones._drone_closing_data(window)

    def test_all_months_are_shown_when_the_window_is_zero(self):
        data = self.data(0)
        self.assertEqual(len(data['months']), 4)
        self.assertEqual(data['months_hidden'], 0)
        self.assertEqual(data['months_available'], 4)

    def test_a_short_window_hides_the_oldest_and_counts_them(self):
        data = self.data(12)
        self.assertEqual(len(data['months']), 4)
        self.assertEqual(data['months_hidden'], 0)
        with app.test_request_context('/drones/reports/closing'):
            narrow = drones._drone_closing_data(2)
        # 2 is not an offered window; the helper is called directly here to
        # prove the truncation itself works.
        self.assertEqual(narrow['months'], ['2026-06', '2026-05'])
        self.assertEqual(narrow['months_hidden'], 2)
        self.assertEqual(narrow['months_available'], 4)

    def test_the_newest_month_is_first(self):
        months = self.data(0)['months']
        self.assertEqual(months, sorted(months, reverse=True))


# ─── 5. The screen ───────────────────────────────────────────────────────────

VOID_TAGS = frozenset(('area', 'base', 'br', 'col', 'embed', 'hr', 'img',
                       'input', 'link', 'meta', 'param', 'source', 'track',
                       'wbr'))


class TagReader(HTMLParser):
    """A real HTML parse. html.parser never refuses anything, so what is
    checked is that every div closes and that attribute NAMES are still
    names -- the signature of a value that terminated its attribute early."""

    def __init__(self):
        HTMLParser.__init__(self, convert_charrefs=True)
        self.divs_opened = 0
        self.divs_closed = 0
        self.attribute_names = set()
        self.stack = []
        self.mismatches = []

    def handle_starttag(self, tag, attrs):
        for name, _value in attrs:
            self.attribute_names.add(name)
        if tag in VOID_TAGS:
            return
        if tag == 'div':
            self.divs_opened += 1
        self.stack.append(tag)

    def handle_endtag(self, tag):
        if tag in VOID_TAGS:
            return
        if tag == 'div':
            self.divs_closed += 1
        if tag in self.stack:
            while self.stack and self.stack.pop() != tag:
                pass
        else:
            self.mismatches.append(tag)


class DroneClosingScreenTests(unittest.TestCase):
    """Criteria 2 and 7 on the rendered page."""

    TEMPLATE = 'works_closing.html'
    ALLOWED_LATIN = ('Excel', 'DJI', 'RFID')

    @classmethod
    def setUpClass(cls):
        cls.fixture = build_fixture()

    def setUp(self):
        self.client = app.test_client()
        login(self.client, self.fixture.admin)

    def body(self, lang='ru', query=''):
        set_language(self.fixture.admin, lang)
        response = self.client.get('/drones/reports/closing' + query)
        self.assertEqual(response.status_code, 200)
        return response.data.decode('utf-8')

    def source(self):
        with open(os.path.join(REPO_ROOT, 'templates', 'drones',
                               self.TEMPLATE), encoding='utf-8') as handle:
            return handle.read()

    def cells(self, body, state):
        return re.findall(
            r'<td class="right" data-state="%s">(.*?)</td>' % state,
            body, re.S)

    def test_the_three_states_render_distinguishably_and_are_labelled(self):
        body = self.body('ru', '?window=0')
        covered = self.cells(body, drones.DRONE_CLOSING_COVERED)
        no_book = self.cells(body, drones.DRONE_CLOSING_NO_BOOK)
        idle = self.cells(body, drones.DRONE_CLOSING_IDLE)
        self.assertTrue(covered and no_book and idle)
        # Flew and billed: a percentage.
        self.assertIn('%', covered[0])
        self.assertIn('vs-badge', covered[0])
        # Flew and handed in nothing: words, not a bland zero.
        self.assertIn('НЕ СДАНА', no_book[0])
        self.assertIn('vs-badge-danger', no_book[0])
        self.assertNotIn('0.0 %', no_book[0])
        self.assertNotIn('%', no_book[0])
        # Did not fly: a dash, named on hover, and no badge at all.
        self.assertIn('—', idle[0])
        self.assertIn('ни гектаров, ни ведомостей', idle[0])
        self.assertNotIn('vs-badge', idle[0])
        # And the three markups are actually different from one another.
        self.assertEqual(len({covered[0], no_book[0], idle[0]}), 3)

    def test_the_fourth_state_is_labelled_too(self):
        body = self.body('ru', '?window=0')
        book_only = self.cells(body, drones.DRONE_CLOSING_BOOK_ONLY)
        self.assertTrue(book_only)
        self.assertIn('гектаров вылетов нет', book_only[0])
        self.assertNotIn('%', book_only[0])

    def test_a_no_book_cell_is_not_rendered_as_a_zero_anywhere(self):
        """The strongest signal on the page must not read as a measurement."""
        for lang in ('ru', 'uz'):
            for cell in self.cells(self.body(lang, '?window=0'),
                                   drones.DRONE_CLOSING_NO_BOOK):
                self.assertNotIn('0.0 %', cell, lang)
                self.assertNotIn('0,0 %', cell, lang)

    def test_the_flew_no_book_list_is_on_the_page_and_links_to_the_month(self):
        body = self.body('ru', '?window=0')
        self.assertIn('Летали, книги нет', body)
        self.assertIn('/drones/works?period=2026-06', body)
        # The link carries the month ONLY: the ledger's subdivision filter
        # uses drone_works.subdivision_name, a different attribution rule.
        self.assertNotIn('period=2026-06&amp;subdivision=', body)

    def test_the_screen_has_no_excel_export_and_says_why(self):
        """Section 3.4: absent on purpose, and the screen says so once."""
        for lang, sentence in (('ru', 'Выгрузки в Excel нет намеренно'),
                               ('uz', 'Excel юклаб олиш атайлаб йўқ')):
            body = self.body(lang, '?window=0')
            self.assertNotIn('.xlsx', body)
            self.assertNotIn('drones.works_closing_xlsx', body)
            self.assertIn(sentence, body, lang)

    def test_no_xlsx_route_exists_for_this_screen(self):
        """«No button» is not «no route»; both have to be true."""
        rules = [str(rule) for rule in app.url_map.iter_rules()]
        self.assertIn('/drones/reports/closing', rules)
        self.assertNotIn('/drones/reports/closing.xlsx', rules)

    def test_the_page_renders_and_parses_in_both_languages(self):
        seen = 0
        for lang in ('ru', 'uz'):
            for query in ('', '?window=0', '?window=24', '?window=мусор'):
                body = self.body(lang, query)
                reader = TagReader()
                reader.feed(body)
                where = '%s %s' % (lang, query)
                self.assertEqual(reader.mismatches, [], where)
                self.assertEqual(reader.divs_opened, reader.divs_closed, where)
                self.assertGreater(reader.divs_opened, 20, where)
                pattern = re.compile(r'^[A-Za-z_:@\-][-A-Za-z0-9_:.]*$')
                for name in sorted(reader.attribute_names):
                    self.assertRegex(name, pattern, '%s: %r' % (where, name))
                seen += 1
        self.assertEqual(seen, 8)

    def test_the_reader_would_notice_a_broken_attribute(self):
        """The control: an attribute cut short by a quote from tojson."""
        reader = TagReader()
        reader.feed('<div data-rows="{"Отчёт": 1}"><span>x</span></div>')
        pattern = re.compile(r'^[A-Za-z_:@\-][-A-Za-z0-9_:.]*$')
        self.assertNotEqual(
            [n for n in reader.attribute_names if not pattern.match(n)], [])

    def test_the_reader_would_notice_an_unclosed_div(self):
        reader = TagReader()
        reader.feed('<div><div></div>')
        self.assertNotEqual(reader.divs_opened, reader.divs_closed)

    def test_no_tojson_sits_in_a_double_quoted_attribute(self):
        for match in re.finditer(r'="[^"]*\|\s*tojson', self.source()):
            self.fail(match.group(0))

    def test_every_uzbek_branch_is_cyrillic(self):
        offenders = []
        halves = re.findall(r"if is_ru else '((?:[^'\\]|\\.)*)'",
                            self.source())
        for text in halves:
            stripped = text
            for word in self.ALLOWED_LATIN:
                stripped = stripped.replace(word, ' ')
            runs = re.findall(r'[A-Za-z]+', stripped)
            if runs:
                offenders.append((text, runs))
        self.assertEqual(offenders, [])
        self.assertGreater(len(halves), 25,
                           'only %d uzbek halves found' % len(halves))

    def test_the_uzbek_scan_fires_on_a_planted_latin_string(self):
        stripped = 'Ким ведомост topshirmagan'
        for word in self.ALLOWED_LATIN:
            stripped = stripped.replace(word, ' ')
        self.assertEqual(re.findall(r'[A-Za-z]+', stripped), ['topshirmagan'])

    def test_the_uzbek_page_really_renders_in_uzbek(self):
        body = self.body('uz', '?window=0')
        self.assertIn('Ким ведомост топширмаган', body)
        self.assertIn('ТОПШИРИЛМАГАН', body)
        self.assertNotIn('НЕ СДАНА', body)

    def test_the_new_uzbek_words_are_cyrillic_code_points(self):
        self.assertEqual([ord(c) for c in 'Ўқиш'],
                         [0x040E, 0x049B, 0x0438, 0x0448])
        self.assertEqual(ord('Ғ'), 0x0492)
        self.assertIn('Ким ведомост топширмаган', self.source())

    def test_the_matrix_scrolls_inside_itself_on_a_narrow_screen(self):
        """The decision, asserted: the matrix scrolls, the list does not.

        [REASON]: a wide matrix on a phone is a real problem and this is the
        answer to it -- the matrix is an overview inside a horizontal
        scroller, and the actionable content is repeated underneath as a
        narrow linear list. If somebody removes the scroller the page starts
        forcing a document-level horizontal scrollbar instead.
        """
        body = self.body('ru', '?window=0')
        self.assertGreaterEqual(body.count('vs-table-scroll'), 2)
        matrix = body.split('Покрытие по подразделениям')[1]
        self.assertIn('vs-table-scroll', matrix.split('</table>')[0])

    def test_the_period_column_headers_are_not_grouped_as_numbers(self):
        """«2026-04» is a period, and the grouping filter on it is a defect."""
        body = self.body('ru', '?window=0')
        self.assertIn('<th class="right">2026-04</th>', body)

    def test_the_month_and_totals_agree_with_the_data_function(self):
        """The rendered footer is the computed footer, not a second sum."""
        with app.test_request_context('/drones/reports/closing'):
            data = drones._drone_closing_data(0)
        body = self.body('ru', '?window=0')
        self.assertIn('%.2f' % data['total']['flight_area'], body)
        self.assertIn('%.1f' % data['month_totals']['2026-04']['coverage'],
                      body)

    def test_it_is_403_without_the_module(self):
        with app.app_context():
            user = User(username='no_drones_closing', role=ROLE_OPERATOR,
                        full_name='No Drones')
            user.set_password('x')
            db.session.add(user)
            db.session.commit()
            user_id = user.id
        client = app.test_client()
        login(client, user_id)
        self.assertEqual(
            client.get('/drones/reports/closing').status_code, 403)


if __name__ == '__main__':
    unittest.main(verbosity=2)
