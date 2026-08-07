# -*- coding: utf-8 -*-
"""DRONE-ZERO-VS-UNKNOWN-001 -- «ноль» и «не записано» перестают быть одним
пикселем в трёх колонках и в третьей корзине долгов.

  A  the counters              _drone_work_cut() also counts missing received
  B  the shared macro          one money_cell(), two templates, `with context`
  C  the two screens           three columns obey the rule of §2
  D  the third bucket          «долг неизвестен» leaves «долга нет»
  E  the workbooks             an unknown figure is an EMPTY cell, never 0

What these assertions can and cannot prove:

  * Every negative control here re-runs the REAL pre-commit code, loaded out
    of git at BASE_COMMIT as a second module object, against the SAME fixture
    rows -- not a paraphrase of it. Where git cannot produce that commit the
    control skips loudly instead of passing quietly; a skipped control is not
    a passed one and the PR body says so.
  * B-2 is the `with context` check. It is written so that deleting
    `with context` from either import makes it fail, and that was verified by
    deleting it once -- see the PR body for the raw output.
  * Nothing here was run against production. The production figures quoted in
    the task (904 jobs, 852 with a received figure, 52 without) are the task's
    numbers; this branch has no access to that database.

Run:
  python -m unittest tests.test_drone_zero_vs_unknown -v
"""
import importlib.util
import io
import os
import re
import subprocess
import sys
import tempfile
import unittest

from tests.harness import app, reset_db, create_admin, login
from models import db, DroneCustomer, DroneCustomerAlias, DroneOperator, \
    DroneWork, User

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# The merge base this task was written against, named in the task itself.
# The negative controls load drones.py OUT OF THIS COMMIT and run it against
# the same fixture, so «the pre-commit tree renders 0» is measured and not
# asserted from memory.
BASE_COMMIT = '116e15a'


# ── the fixture ──────────────────────────────────────────────────────────────
# Six customer groups, chosen so that every case the three columns and the
# three buckets have to tell apart is present exactly once.
#
#   group          jobs  amount           received        no_amount no_received
#   ALL_NULL         2   NULL, NULL       NULL, NULL          2         2
#   MIXED            2   NULL, 5 000 000  1 000 000,          1         0
#                                          2 000 000
#   NONE_NULL        2   3 000 000,       3 000 000,          0         0
#                        4 000 000        4 000 000
#   NO_RECEIVED      2   6 000 000,       NULL, NULL          0         2
#                        7 000 000
#   UNSTATED  (svc) 1    NULL             NULL                1         1
#   UNRESOLVED(svc) 1    1 000 000        NULL                0         1
ALL_NULL = 'Бухоро Агрокластер Заминлари МЧЖ'
MIXED = 'Дўстлик ФХ'
NONE_NULL = 'Пешку АМТ'
NO_RECEIVED = 'Миробод АМТ'
UNSTATED_LABEL_RU = 'Заказчик не указан'
UNRESOLVED_LABEL_RU = 'Заказчик не определён'
UNSTATED_LABEL_UZ = 'Буюртмачи кўрсатилмаган'
UNRESOLVED_LABEL_UZ = 'Буюртмачи аниқланмаган'

# (amount, received) per job, in group order.
GROUPS = (
    (ALL_NULL, ((None, None), (None, None))),
    (MIXED, ((None, 1000000.0), (5000000.0, 2000000.0))),
    (NONE_NULL, ((3000000.0, 3000000.0), (4000000.0, 4000000.0))),
    (NO_RECEIVED, ((6000000.0, None), (7000000.0, None))),
)

EXPECTED = {
    #             jobs amount     received   no_amount no_received
    ALL_NULL: (2, 0.0, 0.0, 2, 2),
    MIXED: (2, 5000000.0, 3000000.0, 1, 0),
    NONE_NULL: (2, 7000000.0, 7000000.0, 0, 0),
    NO_RECEIVED: (2, 13000000.0, 0.0, 0, 2),
    UNSTATED_LABEL_RU: (1, 0.0, 0.0, 1, 1),
    UNRESOLVED_LABEL_RU: (1, 1000000.0, 0.0, 0, 1),
}

TOTAL_JOBS = 10
TOTAL_AMOUNT = 26000000.0
TOTAL_RECEIVED = 10000000.0
TOTAL_OUTSTANDING = 16000000.0
TOTAL_NO_AMOUNT = 4
TOTAL_NO_RECEIVED = 6


def seed():
    """Build the fixture in the disposable test database."""
    reset_db()
    with app.app_context():
        operators = {}
        for key, name in (('op1', 'Файзуллаев Фурқат'),
                          ('op2', 'Хамроев Шохрух')):
            row = DroneOperator(full_name=name, subdivision_name='Гарден')
            db.session.add(row)
            db.session.flush()
            operators[key] = row.id

        def add(amount, received, customer_id, customer_raw, operator):
            db.session.add(DroneWork(
                period_month='2026-04',
                drone_customer_id=customer_id,
                customer_raw=customer_raw,
                drone_operator_id=operators[operator],
                operator_raw=operator,
                area_ha=10.0, amount=amount, received_amount=received,
                payment_type='cash', subdivision_name='Гарден',
                source_file='fixture.xlsx', source_sheet='свод ичи',
                import_batch='zero-vs-unknown'))

        for index, (name, jobs) in enumerate(GROUPS):
            customer = DroneCustomer(name=name)
            db.session.add(customer)
            db.session.flush()
            db.session.add(DroneCustomerAlias(
                raw_name=name, normalized_key=name.casefold(),
                drone_customer_id=customer.id, is_active=True))
            operator = 'op1' if index < 2 else 'op2'
            for amount, received in jobs:
                add(amount, received, customer.id, name, operator)

        # [REASON]: the two service rows are DIFFERENT facts and the cut keeps
        # them apart -- «имени в ведомости не было» (the holding's own land,
        # customer_raw = '') versus «написание есть, алиаса нет». Both have to
        # be in the fixture, because both are ordinary groups as far as the
        # three columns and the three buckets are concerned, and a fixture
        # that only exercised named customers would never render them.
        add(None, None, None, '', 'op2')
        add(1000000.0, None, None, 'Хўжалик номаълум', 'op2')
        db.session.commit()


def set_language(user_id, lang):
    with app.app_context():
        user = db.session.get(User, user_id)
        user.language = lang
        db.session.commit()


def report_data(lang='ru'):
    """The structure both screens and both workbooks are built from."""
    import drones
    from flask import g
    from werkzeug.datastructures import MultiDict
    with app.test_request_context('/drones/works/reports'):
        g.lang = lang
        return drones._drone_works_report_data(
            drones._drone_work_conditions(
                drones._drone_works_filters_from_args(MultiDict())))


def by_label(cut):
    """{label: row} over the ordinary rows AND the service rows of a cut."""
    return {r['label']: r for r in cut['rows'] + list(cut['services'])}


# ── the pre-commit tree, loaded out of git ───────────────────────────────────
_PRE_COMMIT_CACHE = {}


def pre_commit_drones():
    """drones.py as of BASE_COMMIT, imported as a SECOND module object.

    [REASON]: a negative control that quotes the old code into the test file
    proves only that the quote differs. This loads the real pre-commit module
    and runs it against the same rows in the same session, so «the pre-commit
    tree renders 0 here» is a measurement.

    The module builds its own Blueprint('drones') object at import time; it is
    never registered on the app, so the live blueprint is untouched. models.db
    is shared, which is exactly what makes the comparison meaningful.

    Returns None when git cannot produce the blob (a shallow clone, a tarball
    export). Callers skip in that case -- loudly, never silently passing.
    """
    if 'module' in _PRE_COMMIT_CACHE:
        return _PRE_COMMIT_CACHE['module']
    _PRE_COMMIT_CACHE['module'] = None
    try:
        source = subprocess.check_output(
            ['git', 'show', '%s:drones.py' % BASE_COMMIT],
            cwd=REPO_ROOT, stderr=subprocess.DEVNULL)
    except (OSError, subprocess.CalledProcessError):
        return None
    handle, path = tempfile.mkstemp(prefix='drones_pre_', suffix='.py')
    with os.fdopen(handle, 'wb') as fh:
        fh.write(source)
    spec = importlib.util.spec_from_file_location('drones_pre_commit', path)
    module = importlib.util.module_from_spec(spec)
    sys.modules['drones_pre_commit'] = module
    spec.loader.exec_module(module)
    _PRE_COMMIT_CACHE['module'] = module
    return module


class PreCommitMixin(object):
    def pre_commit(self):
        module = pre_commit_drones()
        if module is None:
            self.skipTest('git cannot produce %s:drones.py -- the negative '
                          'control did NOT run' % BASE_COMMIT)
        return module

    def pre_commit_report_data(self, lang='ru'):
        module = self.pre_commit()
        from flask import g
        from werkzeug.datastructures import MultiDict
        with app.test_request_context('/drones/works/reports'):
            g.lang = lang
            return module._drone_works_report_data(
                module._drone_work_conditions(
                    module._drone_works_filters_from_args(MultiDict())))


# ---------------------------------------------------------------------------
# A  commit 1 -- the counters
# ---------------------------------------------------------------------------

class TestA1Counters(unittest.TestCase):
    """A-1: no_amount and no_received on every row, service row and total."""

    @classmethod
    def setUpClass(cls):
        seed()

    def setUp(self):
        self.cut = report_data()['by_customer']
        self.rows = by_label(self.cut)

    def test_a1_every_group_carries_both_counters(self):
        for label, (jobs, amount, received, no_amount, no_received) in \
                EXPECTED.items():
            with self.subTest(group=label):
                row = self.rows[label]
                self.assertEqual(jobs, row['jobs'])
                self.assertAlmostEqual(amount, row['amount'], places=2)
                self.assertAlmostEqual(received, row['received'], places=2)
                self.assertEqual(no_amount, row['no_amount'])
                self.assertEqual(no_received, row['no_received'])

    def test_a1_the_four_cases_really_are_four_different_cases(self):
        """A fixture where two cases coincide proves nothing about either."""
        shapes = {(self.rows[label]['no_amount'] == self.rows[label]['jobs'],
                   self.rows[label]['no_received'] == self.rows[label]['jobs'],
                   bool(self.rows[label]['no_amount']))
                  for label in (ALL_NULL, MIXED, NONE_NULL, NO_RECEIVED)}
        self.assertEqual(4, len(shapes), shapes)

    def test_a1_the_service_rows_carry_them_too(self):
        services = {r['label']: r for r in self.cut['services']}
        self.assertEqual({UNSTATED_LABEL_RU, UNRESOLVED_LABEL_RU},
                         set(services))
        self.assertEqual(1, services[UNSTATED_LABEL_RU]['no_received'])
        self.assertEqual(1, services[UNSTATED_LABEL_RU]['no_amount'])
        self.assertEqual(1, services[UNRESOLVED_LABEL_RU]['no_received'])
        self.assertEqual(0, services[UNRESOLVED_LABEL_RU]['no_amount'])

    def test_a1_the_total_carries_them(self):
        total = self.cut['total']
        self.assertEqual(TOTAL_JOBS, total['jobs'])
        self.assertEqual(TOTAL_NO_AMOUNT, total['no_amount'])
        self.assertEqual(TOTAL_NO_RECEIVED, total['no_received'])


class TestA2CountersAddUp(unittest.TestCase):
    """A-2: a counter that does not add up is measuring something else."""

    CUTS = ('by_customer', 'by_operator', 'by_subdivision', 'by_month',
            'by_payment')

    @classmethod
    def setUpClass(cls):
        seed()

    def setUp(self):
        self.data = report_data()

    def test_a2_each_total_equals_the_sum_over_its_groups(self):
        for name in self.CUTS:
            with self.subTest(cut=name):
                cut = self.data[name]
                everything = cut['rows'] + list(cut['services'])
                self.assertEqual(cut['total']['no_amount'],
                                 sum(r['no_amount'] for r in everything))
                self.assertEqual(cut['total']['no_received'],
                                 sum(r['no_received'] for r in everything))

    def test_a2_every_cut_agrees_on_the_two_counts(self):
        counts = {name: (self.data[name]['total']['no_amount'],
                         self.data[name]['total']['no_received'])
                  for name in self.CUTS}
        self.assertEqual(
            {name: (TOTAL_NO_AMOUNT, TOTAL_NO_RECEIVED) for name in counts},
            counts)

    def test_a2_the_money_totals_are_untouched_by_the_new_column(self):
        """Adding a counter must not disturb the arithmetic it sits beside."""
        for name in self.CUTS:
            with self.subTest(cut=name):
                total = self.data[name]['total']
                self.assertAlmostEqual(TOTAL_AMOUNT, total['amount'], places=2)
                self.assertAlmostEqual(TOTAL_RECEIVED, total['received'],
                                       places=2)
                self.assertAlmostEqual(TOTAL_OUTSTANDING,
                                       total['outstanding'], places=2)
                self.assertTrue(self.data[name]['reconciled'], name)

    def test_a2_rest_carries_both_counters(self):
        """`rest` is assembled by naming its keys, so a counter reaches it
        only if somebody named it. no_amount never was."""
        rest = self.data['debt_by_customer']['rest']
        self.assertIsNotNone(rest)
        self.assertIn('no_amount', rest)
        self.assertIn('no_received', rest)


class TestA3NegativeControl(PreCommitMixin, unittest.TestCase):
    """A-3: the same assertions fail against the real pre-commit module."""

    @classmethod
    def setUpClass(cls):
        seed()

    def test_a3_pre_commit_rows_have_no_no_received(self):
        cut = self.pre_commit_report_data()['by_customer']
        row = by_label(cut)[ALL_NULL]
        self.assertEqual(2, row['no_amount'], 'the fixture is the same one')
        with self.assertRaises(KeyError):
            row['no_received']

    def test_a3_pre_commit_total_has_no_no_received(self):
        cut = self.pre_commit_report_data()['by_customer']
        with self.assertRaises(KeyError):
            cut['total']['no_received']

    def test_a3_pre_commit_rest_drops_the_counter(self):
        rest = self.pre_commit_report_data()['debt_by_customer']['rest']
        self.assertIsNotNone(rest, 'the settled bucket exists pre-commit')
        self.assertNotIn('no_amount', rest)
        self.assertNotIn('no_received', rest)

    def test_a3_the_control_is_reading_the_other_module(self):
        """Guard against the control silently importing the live module."""
        import drones
        module = self.pre_commit()
        self.assertIsNot(module, drones)
        self.assertNotEqual(module.__file__, drones.__file__)


if __name__ == '__main__':
    unittest.main(verbosity=2)
