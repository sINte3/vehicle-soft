# -*- coding: utf-8 -*-
"""DRONE-ANALYTICS-001 -- four new reports, the prefill link and four defects.

Section per commit, named as the acceptance list in the task:

  A1  spray usage        litres per hectare over usage_type == 0 only
  A2  debt aging         buckets by age, undated and unrecorded kept apart
  A3  reconcile          ledger hectares against flight hectares by month
  A4  flight calendar    machine x day, UTC+5, status overlay
  A5  prefill link       the hints screen still creates nothing
  A6  the four defects   CSS, the summary sheet, the amount script, vs_num
  X   cross-cutting      AST freeze, tile icons, literal offsets, Uzbek

What these assertions can and cannot prove:

  * Every negative control here BREAKS the real code -- by monkeypatching the
    module, or by running the same fixture through the wrong expression -- and
    asserts the failure, so each check is known to be able to fail. A check
    that passes against both the right and the wrong code is not a check, and
    two of the ones drafted for this task were deleted for that reason.
  * Nothing here was run against production. The figures quoted in the task
    (3.54..38.53 l/ha, 753 439 010 so'm, April 6 336.15 vs 6 379.24 ha) are
    the task's numbers; this branch has no access to that database. A3.4 is
    explicitly the owner's to verify.

Run:
  python -m unittest tests.test_drone_analytics_001 -v
"""
import ast
import io
import os
import re
import unittest
from datetime import date, datetime, timedelta

from tests.harness import app, reset_db, create_admin, login
from models import db, DroneFlight, DroneUnit, DroneOperator, DroneWork, \
    DroneCustomer, DroneUnitStatusLog, Organization, User, \
    DRONE_STATUS_SERVICEABLE, DRONE_STATUS_UNSERVICEABLE

import drones

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DASH = '—'
NBSP = ' '


def _flight(unit_id, started_at, area, usage_type=0, liters=None,
            sow_kg=None, dji_id=None, seconds=3600):
    """One DroneFlight. started_at is UTC, as the column is."""
    _flight.counter = getattr(_flight, 'counter', 0) + 1
    return DroneFlight(
        dji_flight_id=dji_id if dji_id is not None else 900000 + _flight.counter,
        drone_unit_id=unit_id,
        nickname_raw='fixture',
        started_at=started_at,
        work_seconds=seconds,
        area_ha=area,
        spray_liters=liters,
        sow_kg=sow_kg,
        usage_type=usage_type,
        # NOT NULL on the table: the ingest keeps the DJI payload verbatim.
        raw_json='{}',
    )


def set_language(user_id, lang):
    """[REASON]: the page language comes from users.language through g.lang,
    NOT from a session key. A test that plants sess['lang'] renders Uzbek
    under both settings and its «both languages» assertion measures nothing.
    """
    with app.app_context():
        user = User.query.get(user_id)
        user.language = lang
        db.session.commit()


def _units(numbers):
    """Machines, with the organization drone_units.organization_id requires."""
    org = Organization.query.first()
    if org is None:
        org = Organization(name='Fixture Holding')
        db.session.add(org)
        db.session.flush()
    ids = {}
    for number in numbers:
        unit = DroneUnit(number=number, organization_id=org.id)
        db.session.add(unit)
        db.session.flush()
        ids[number] = unit.id
    return ids


# ══ A1  spray usage ══════════════════════════════════════════════════════════

class TestA1SprayUsage(unittest.TestCase):
    """Litres per hectare, spray flights only, NULL kept apart from zero."""

    @classmethod
    def setUpClass(cls):
        reset_db()
        cls.user_id = create_admin('a1admin')
        with app.app_context():
            ids = _units([1, 2, 3])
            cls.u1, cls.u2, cls.u3 = ids[1], ids[2], ids[3]
            # Machine 1, April: 2 spray flights, 100 ha, 2 000 l -> 20.00 l/ha
            db.session.add(_flight(cls.u1, datetime(2026, 4, 5, 6), 60.0,
                                   0, 1200.0))
            db.session.add(_flight(cls.u1, datetime(2026, 4, 6, 6), 40.0,
                                   0, 800.0))
            # Machine 2, April: 3 spray flights, area > 0, litres ALL NULL.
            # A1.3 -- the cell must be an em dash, not 0.00, not coloured.
            for day in (7, 8, 9):
                db.session.add(_flight(cls.u2, datetime(2026, 4, day, 6),
                                       10.0, 0, None))
            # Machine 3, April: 1 spray flight far outside any corridor.
            db.session.add(_flight(cls.u3, datetime(2026, 4, 10, 6), 10.0,
                                   0, 600.0))          # 60.00 l/ha
            db.session.commit()

    def _get(self, client, query=''):
        return client.get('/drones/reports/spray' + query)

    def test_a1_1_renders_in_both_languages(self):
        seen = {}
        with app.test_client() as client:
            login(client, self.user_id)
            for lang in ('ru', 'uz'):
                set_language(self.user_id, lang)
                resp = self._get(client,
                                 '?date_from=2026-04-01&date_to=2026-04-30')
                self.assertEqual(resp.status_code, 200)
                html = resp.get_data(as_text=True)
                seen[lang] = re.search(r'<title>(.*?)</title>', html,
                                       re.S).group(1).strip()
        self.assertIn('расход рабочего раствора', seen['ru'])
        self.assertIn('иш эритмаси сарфи', seen['uz'])
        # Guards against the language switch silently not switching -- which
        # it did on the first draft of this test, planting sess['lang'].
        self.assertNotEqual(seen['ru'], seen['uz'])

    def test_a1_2_area_reconciles(self):
        with app.app_context():
            conds = drones._drone_flight_conditions(
                {'date_from': date(2026, 4, 1), 'date_to': date(2026, 4, 30),
                 'unit_id': None, 'region': ''})
            data = drones._drone_spray_usage_data(conds, 30)
        self.assertTrue(data['reconciled'])
        self.assertAlmostEqual(
            sum(r['area_spray'] + r['area_other'] for r in data['rows']),
            data['grand']['area'], delta=0.005)

    def test_a1_3_all_null_litres_is_a_dash_not_zero(self):
        """A1.3 positive: machine 2 has 3 spray flights, all litres NULL."""
        with app.app_context():
            conds = drones._drone_flight_conditions(
                {'date_from': date(2026, 4, 1), 'date_to': date(2026, 4, 30),
                 'unit_id': None, 'region': ''})
            data = drones._drone_spray_usage_data(conds, 30)
        row = [r for r in data['rows'] if r['unit_id'] == self.u2][0]
        self.assertEqual(row['spray_flights'], 3)
        self.assertEqual(row['no_liters'], 3)
        self.assertGreater(row['area_spray'], 0.0)
        self.assertIsNone(row['rate'], 'all-NULL litres must not produce a rate')
        self.assertEqual(row['flag'], '', 'an unknown rate must not be coloured')

    def test_a1_3_negative_control_coalescing_null_to_zero(self):
        """A1.3 negative: coalesce NULL litres to 0 and the cell breaks.

        The break is applied to the SAME fixture through the same helper: the
        rate is recomputed the way a coalescing implementation would, and the
        assertion of the positive test above is re-run against it.
        """
        with app.app_context():
            conds = drones._drone_flight_conditions(
                {'date_from': date(2026, 4, 1), 'date_to': date(2026, 4, 30),
                 'unit_id': None, 'region': ''})
            data = drones._drone_spray_usage_data(conds, 30)
        row = [r for r in data['rows'] if r['unit_id'] == self.u2][0]

        # The broken rule: no no_liters counter, NULL summed as 0.0.
        broken_rate = drones._drone_rate(row['liters'], row['area_spray'])
        median = data['median']
        broken_flag = ''
        if median is not None and broken_rate is not None:
            if (broken_rate < median * (1 - 0.6)
                    or broken_rate > median * (1 + 0.6)):
                broken_flag = 'is-danger-row'
            elif (broken_rate < median * 0.7 or broken_rate > median * 1.3):
                broken_flag = 'is-warning-row'

        self.assertEqual(broken_rate, 0.0,
                         'the broken rule reports 0.00 l/ha over 30 ha')
        self.assertEqual(broken_flag, 'is-danger-row',
                         'and then paints that invented zero red')
        # The failure the positive assertion would report:
        with self.assertRaises(AssertionError) as caught:
            self.assertIsNone(broken_rate,
                              'all-NULL litres must not produce a rate')
        self.assertIn('not None', str(caught.exception))

    # [REASON]: A1.4 gets its OWN machine, created inside the test, and the
    # sow flight is added inside the same method. unittest runs methods in
    # alphabetical order, so a positive test that mutates the fixture and a
    # negative test that reads the mutation pass or fail depending on their
    # names -- which is a check that measures the alphabet, not the code.
    def _sow_fixture(self, number):
        """A machine with 100 ha / 2 000 l sprayed, and no sow flight yet."""
        ids = _units([number])
        unit_id = ids[number]
        db.session.add(_flight(unit_id, datetime(2026, 4, 5, 6), 60.0,
                               0, 1200.0))
        db.session.add(_flight(unit_id, datetime(2026, 4, 6, 6), 40.0,
                               0, 800.0))
        db.session.commit()
        return unit_id

    @staticmethod
    def _april(unit_id):
        return drones._drone_flight_conditions(
            {'date_from': date(2026, 4, 1), 'date_to': date(2026, 4, 30),
             'unit_id': unit_id, 'region': ''})

    def test_a1_4_sow_flight_does_not_move_the_rate(self):
        """A1.4 positive: adding a sow flight leaves l/ha alone."""
        with app.app_context():
            unit_id = self._sow_fixture(41)
            before = drones._drone_spray_usage_data(self._april(unit_id), 30)
            rate_before = before['rows'][0]['rate']

            db.session.add(_flight(unit_id, datetime(2026, 4, 11, 6), 10.0,
                                   usage_type=1, liters=None, sow_kg=250.0))
            db.session.commit()
            after = drones._drone_spray_usage_data(self._april(unit_id), 30)
            row = after['rows'][0]

        self.assertAlmostEqual(rate_before, 20.0, delta=0.0001)
        self.assertAlmostEqual(row['rate'], 20.0, delta=0.0001,
                               msg='a sow flight must not change l/ha')
        self.assertAlmostEqual(row['area_other'], 10.0, delta=0.0001,
                               msg='the sow area belongs in the other column')
        self.assertAlmostEqual(row['area_spray'], 100.0, delta=0.0001)

    def test_a1_4_negative_control_without_the_usage_type_filter(self):
        """A1.4 negative: drop `usage_type == 0` and the rate moves."""
        with app.app_context():
            unit_id = self._sow_fixture(42)
            db.session.add(_flight(unit_id, datetime(2026, 4, 11, 6), 10.0,
                                   usage_type=1, liters=None, sow_kg=250.0))
            db.session.commit()
            data = drones._drone_spray_usage_data(self._april(unit_id), 30)
            row = data['rows'][0]
            # What the summary page's liters_per_ha does: total area, sow
            # flights included -- i.e. the filter this report exists to add.
            unfiltered = drones._drone_rate(row['liters'], row['area_total'])

        self.assertAlmostEqual(row['rate'], 20.0, delta=0.0001)
        self.assertAlmostEqual(unfiltered, 18.1818, delta=0.001)
        with self.assertRaises(AssertionError) as caught:
            self.assertAlmostEqual(unfiltered, 20.0, delta=0.0001,
                                   msg='a sow flight must not change l/ha')
        self.assertIn('must not change', str(caught.exception))

    def test_a1_5_band_argument_bounds(self):
        cases = {'abc': 30, '0': 30, '999': 30, '': 30, '5': 5, '100': 100,
                 '45': 45, '4': 30, '101': 30}
        with app.test_request_context('/'):
            pass
        for raw, expected in cases.items():
            with app.test_request_context('/drones/reports/spray?band=%s' % raw):
                from flask import request as rq
                self.assertEqual(drones._drone_spray_band(rq.args), expected,
                                 'band=%r' % raw)

    def test_a1_6_excel_dash_cell_is_empty(self):
        from openpyxl import load_workbook
        with app.test_client() as client:
            login(client, self.user_id)
            resp = client.get('/drones/reports/spray.xlsx'
                              '?date_from=2026-04-01&date_to=2026-04-30')
            self.assertEqual(resp.status_code, 200)
            wb = load_workbook(io.BytesIO(resp.data))
        ws = wb.worksheets[1]
        # Column 1 machine, 9 rate. Find machine 2's row: 3 spray flights, all
        # litres NULL -> both the litres cell and the rate cell must be empty.
        target = None
        for row in ws.iter_rows(min_row=2, values_only=True):
            if row[2] == 3 and row[3] == 3:
                target = row
        self.assertIsNotNone(target, 'the all-NULL machine-month must be there')
        self.assertIsNone(target[8], 'the l/ha cell must be EMPTY, not 0')
        self.assertIsNone(target[7], 'the litres cell must be EMPTY, not 0')


class TestA17TooSmallToJudge(unittest.TestCase):
    """A1.7 -- a machine-month with almost no area is shown but not judged.

    [REASON]: measured on staging 2026-08-07, machine No 9 flew 25 flights
    totalling 0.25 ha and its rate comes out at 141.91 l/ha. That is 35 litres
    over a quarter of a hectare, not a machine over-dosing five-fold. Grouped
    per month, as this report is, such cells are common rather than rare.
    """

    @classmethod
    def setUpClass(cls):
        reset_db()
        cls.user_id = create_admin('a17admin')
        with app.app_context():
            ids = _units([21, 22, 23])
            cls.big_normal = ids[21]     # 200 ha at 25 l/ha
            cls.big_low = ids[22]        # 200 ha at  3 l/ha
            cls.tiny = ids[23]           # 0.2 ha at 140 l/ha
            db.session.add(_flight(cls.big_normal, datetime(2026, 4, 5, 6),
                                   200.0, 0, 5000.0))
            db.session.add(_flight(cls.big_low, datetime(2026, 4, 6, 6),
                                   200.0, 0, 600.0))
            db.session.add(_flight(cls.tiny, datetime(2026, 4, 7, 6),
                                   0.2, 0, 28.0))
            db.session.commit()

    @staticmethod
    def _data(min_area):
        with app.app_context():
            conds = drones._drone_flight_conditions(
                {'date_from': date(2026, 4, 1), 'date_to': date(2026, 4, 30),
                 'unit_id': None, 'region': ''})
            return drones._drone_spray_usage_data(conds, 30, min_area)

    def _row(self, data, unit_id):
        return [r for r in data['rows'] if r['unit_id'] == unit_id][0]

    def test_a1_7_the_tiny_cell_is_shown_but_not_judged(self):
        data = self._data(drones.DRONE_SPRAY_MIN_AREA_HA)
        big = self._row(data, self.big_normal)
        low = self._row(data, self.big_low)
        tiny = self._row(data, self.tiny)

        self.assertAlmostEqual(big['rate'], 25.0, delta=0.005)
        self.assertAlmostEqual(low['rate'], 3.0, delta=0.005)
        self.assertAlmostEqual(tiny['rate'], 140.0, delta=0.005)

        # The median is taken over the first two only: (25 + 3) / 2 = 14.
        self.assertAlmostEqual(data['median'], 14.0, delta=0.005)
        self.assertEqual(data['rated_cells'], 2)
        self.assertEqual(data['unjudged_cells'], 1)

        # The tiny cell still SHOWS its rate, is uncoloured and says why.
        self.assertFalse(tiny['judged'])
        self.assertEqual(tiny['flag'], '')
        self.assertIn(tiny['note'], ('мало площади', 'майдон кам'))
        self.assertIsNotNone(tiny['rate'], 'the rate is shown, not hidden')

        # And the genuinely low machine IS judged and IS coloured.
        self.assertTrue(low['judged'])
        self.assertEqual(low['flag'], 'is-danger-row')
        self.assertEqual(low['note'], '')

    def test_a1_7_min_area_zero_judges_everything(self):
        data = self._data(0.0)
        tiny = self._row(data, self.tiny)
        # median([3, 25, 140]) = 25
        self.assertAlmostEqual(data['median'], 25.0, delta=0.005)
        self.assertEqual(data['rated_cells'], 3)
        self.assertEqual(data['unjudged_cells'], 0)
        self.assertTrue(tiny['judged'])
        self.assertEqual(tiny['note'], '')
        self.assertEqual(tiny['flag'], 'is-danger-row')

    def test_a1_7_min_area_argument_bounds(self):
        cases = {'abc': 10.0, '-1': 10.0, '1001': 10.0, '': 10.0,
                 '0': 0.0, '0.5': 0.5, '250': 250.0, '1000': 1000.0}
        for raw, expected in cases.items():
            with app.test_request_context(
                    '/drones/reports/spray?min_area=%s' % raw):
                from flask import request as rq
                self.assertEqual(drones._drone_spray_min_area(rq.args),
                                 expected, 'min_area=%r' % raw)

    def test_a1_7_negative_control_judging_everything(self):
        """Drop the threshold and the median moves onto a 0.2 ha cell."""
        strict = self._data(drones.DRONE_SPRAY_MIN_AREA_HA)
        loose = self._data(0.0)
        self.assertAlmostEqual(strict['median'], 14.0, delta=0.005)
        self.assertAlmostEqual(loose['median'], 25.0, delta=0.005)
        with self.assertRaises(AssertionError) as caught:
            self.assertAlmostEqual(
                loose['median'], strict['median'], delta=0.005,
                msg='a 0.2 ha cell must not set the fleet standard')
        self.assertIn('must not set the fleet standard',
                      str(caught.exception))


class TestA17UnattributedNeverVotes(unittest.TestCase):
    """The NULL-machine line is excluded from the median and never coloured.

    Its own class, with its own fixture: adding the unattributed flight to the
    class above would move that class's median and make its assertions depend
    on unittest's alphabetical method order.
    """

    @classmethod
    def setUpClass(cls):
        reset_db()
        create_admin('a17uadmin')
        with app.app_context():
            ids = _units([31, 32])
            cls.a, cls.b = ids[31], ids[32]
            db.session.add(_flight(cls.a, datetime(2026, 4, 5, 6),
                                   200.0, 0, 5000.0))          # 25 l/ha
            db.session.add(_flight(cls.b, datetime(2026, 4, 6, 6),
                                   200.0, 0, 600.0))           # 3 l/ha
            # 500 ha at 100 l/ha on a nickname that resolved to no machine.
            db.session.add(_flight(None, datetime(2026, 4, 8, 6),
                                   500.0, 0, 50000.0))
            db.session.commit()

    def test_the_unattributed_line_is_shown_but_never_votes(self):
        with app.app_context():
            conds = drones._drone_flight_conditions(
                {'date_from': date(2026, 4, 1), 'date_to': date(2026, 4, 30),
                 'unit_id': None, 'region': ''})
            data = drones._drone_spray_usage_data(
                conds, 30, drones.DRONE_SPRAY_MIN_AREA_HA)
        unattr = [r for r in data['rows'] if r['unit_id'] is None][0]
        self.assertAlmostEqual(unattr['rate'], 100.0, delta=0.005)
        self.assertFalse(unattr['judged'], 'the unattributed line never votes')
        self.assertEqual(unattr['flag'], '', 'and is never coloured')
        self.assertIn(unattr['note'], ('не распознано', 'аниқланмаган'))
        # 500 ha at 100 l/ha did NOT move the median off (25 + 3) / 2.
        self.assertAlmostEqual(data['median'], 14.0, delta=0.005)
        self.assertEqual(data['rated_cells'], 2)

    def test_negative_control_letting_it_vote(self):
        """If it voted, the median would be median([3, 25, 100]) = 25."""
        with app.app_context():
            conds = drones._drone_flight_conditions(
                {'date_from': date(2026, 4, 1), 'date_to': date(2026, 4, 30),
                 'unit_id': None, 'region': ''})
            data = drones._drone_spray_usage_data(
                conds, 30, drones.DRONE_SPRAY_MIN_AREA_HA)
        with_unattr = drones._drone_median(
            [r['rate'] for r in data['rows'] if r['rate'] is not None])
        self.assertAlmostEqual(with_unattr, 25.0, delta=0.005)
        with self.assertRaises(AssertionError) as caught:
            self.assertAlmostEqual(
                with_unattr, data['median'], delta=0.005,
                msg='a bucket of unknowns must not set the standard')
        self.assertIn('must not set the standard', str(caught.exception))
