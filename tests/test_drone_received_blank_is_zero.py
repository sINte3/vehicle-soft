# -*- coding: utf-8 -*-
"""DRONE-RECEIVED-BLANK-IS-ZERO-001 -- «Получено» stops having an unknown.

THE OWNER'S DECISION, 2026-08-07, not a fact derived from the data: in the
dispatcher sheets an empty «олинмаган пуллар» cell means NOTHING WAS
COLLECTED, not «nobody wrote it down». DRONE-ZERO-VS-UNKNOWN-001 had treated
it like a blank «Сумма» and printed a dash, which produced live rows saying
two things at once:

    Бухоро Сервис Агрокластер    3    55 429 140    —    55 429 140

«collected: unknown; uncollected: all of it.» So the dash leaves «Получено».

What does NOT change, and is asserted here to make sure it did not:

  * «Сумма» keeps its dash and its «сумма не указана: N» counter.
  * «Не получено» keeps its dash, still keyed on no_amount.
  * The three-bucket split on the debts page.
  * Every piece of arithmetic, including the column SUM -- an empty cell
    already counts as zero inside SUM, so only the statement changes.

What these assertions can and cannot prove:

  * The negative controls load drones.py out of git at THIS task's base,
    de97c8d, and run it against the same fixture. Where git cannot produce
    that commit they skip loudly; a skipped control is not a passed one.
  * Nothing here was run against staging. The reviewer's blank counts (81 on
    «Долги — заказчики», 94 on «По заказчикам», column sum 1 904 558 438.98)
    were measured on staging workbooks and cannot be reproduced from this
    branch. The counts asserted below are this fixture's own.

Run:
  python -m unittest tests.test_drone_received_blank_is_zero -v
"""
import io
import re
import unittest

from tests.harness import app, create_admin, login
from tests.test_drone_zero_vs_unknown import (
    ALL_NULL, CUT_MONEY, CUT_SHEETS, DEBT_MONEY, DEBT_SHEETS, MIXED,
    NONE_NULL, NO_RECEIVED, PARTIAL, TOUCHED_TEMPLATES, UNKNOWN_LABEL_RU,
    UNSTATED_LABEL_RU, XLSX_ROUTES, by_label, cut_cells, pre_commit_drones,
    read_template, report_data, seed, set_language, text_of)

# The base this task was written against: the merge of PR #48.
BASE_COMMIT = 'de97c8d'


def money_cells(row):
    import drones
    return drones._drone_work_money_cells(row)


def every_row(data):
    """Every dict a workbook writer will hand to _drone_work_money_cells()."""
    rows = []
    for name in ('by_customer', 'by_operator', 'by_subdivision', 'by_month',
                 'by_payment'):
        cut = data[name]
        rows.extend(cut['rows'])
        rows.extend(cut['services'])
        rows.append(cut['total'])
    for name in ('debt_by_customer', 'debt_by_operator'):
        debt = data[name]
        rows.extend(debt['rows'])
        for bucket in (debt['unknown'], debt['rest']):
            if bucket is not None:
                rows.append(bucket)
        rows.append(debt['total'])
    return rows


class PreCommitMixin(object):
    """This task's base, not the previous task's."""

    def pre_commit(self):
        module = pre_commit_drones(BASE_COMMIT)
        if module is None:
            self.skipTest('git cannot produce %s:drones.py -- the negative '
                          'control did NOT run' % BASE_COMMIT)
        return module


# ---------------------------------------------------------------------------
# A  commit 1 -- drones.py
# ---------------------------------------------------------------------------

class TestA1TheMiddleCellIsPlain(unittest.TestCase):
    """A-1: an all-NULL-received group gets 0, not None."""

    @classmethod
    def setUpClass(cls):
        seed()

    def setUp(self):
        self.rows = by_label(report_data()['by_customer'])

    def test_a1_all_null_received_and_all_null_amount(self):
        """ALL_NULL: nothing priced, nothing collected.

        Sum unknown, debt unknown, collected ZERO -- the three columns are
        now three different rules and this row shows all three.
        """
        amount, received, outstanding = money_cells(self.rows[ALL_NULL])
        self.assertIsNone(amount)
        self.assertEqual(0, received)
        self.assertIsNone(outstanding)

    def test_a1_all_null_received_with_amounts_known(self):
        """NO_RECEIVED: 13 000 000 charged, nothing collected, all of it due."""
        row = self.rows[NO_RECEIVED]
        self.assertEqual(2, row['no_received'])
        self.assertEqual(row['jobs'], row['no_received'])
        amount, received, outstanding = money_cells(row)
        self.assertEqual(13000000.0, amount)
        self.assertEqual(0, received)
        self.assertEqual(13000000.0, outstanding)

    def test_a1_the_service_row_too(self):
        amount, received, outstanding = money_cells(
            self.rows[UNSTATED_LABEL_RU])
        self.assertIsNone(amount)
        self.assertEqual(0, received)
        self.assertIsNone(outstanding)

    def test_a1_a_group_that_did_collect_is_untouched(self):
        amount, received, outstanding = money_cells(self.rows[NONE_NULL])
        self.assertEqual(7000000.0, amount)
        self.assertEqual(7000000.0, received)
        self.assertEqual(0.0, outstanding)

    def test_a1_a_partial_group_is_untouched(self):
        amount, received, outstanding = money_cells(self.rows[MIXED])
        self.assertEqual(5000000.0, amount)
        self.assertEqual(3000000.0, received)
        self.assertEqual(2000000.0, outstanding)

    def test_a1_the_returned_value_is_the_rows_own_received(self):
        """Plain pass-through: no rounding, no coalescing, no rule."""
        for label, row in self.rows.items():
            with self.subTest(group=label):
                self.assertEqual(row['received'], money_cells(row)[1])


class TestA2TheCounterSurvives(unittest.TestCase):
    """A-2: no_received is still computed and still correct.

    Coverage is deliberately NOT duplicated here. TestA1Counters and
    TestA2CountersAddUp in tests/test_drone_zero_vs_unknown.py already assert
    no_received on ordinary rows, on both service rows, on the operator
    partition, on `rest`, on every cut total, and that each total equals the
    sum over its groups. Those tests are untouched by this task and still
    pass; this class only pins the two things they cannot know: that the
    counter is still there AFTER the column stopped using it, and that
    nothing renders it.
    """

    @classmethod
    def setUpClass(cls):
        seed()

    def test_a2_every_row_still_carries_the_counter(self):
        for row in every_row(report_data()):
            with self.subTest(label=row.get('label')):
                self.assertIn('no_received', row)
                self.assertIsInstance(row['no_received'], int)

    def test_a2_the_counter_is_still_correct(self):
        rows = by_label(report_data()['by_customer'])
        self.assertEqual(2, rows[ALL_NULL]['no_received'])
        self.assertEqual(0, rows[MIXED]['no_received'])
        self.assertEqual(0, rows[NONE_NULL]['no_received'])
        self.assertEqual(2, rows[NO_RECEIVED]['no_received'])
        self.assertEqual(6, report_data()['by_customer']['total']
                         ['no_received'])

    def test_a2_the_deliberate_non_use_is_written_down(self):
        """A counter computed and never shown needs a reason IN THE CODE.

        Without it the next reader deletes it as dead weight, or restores the
        dash believing they are fixing an oversight. Both were named as the
        risk when the decision was taken.
        """
        import inspect
        import drones
        for func in (drones._drone_work_cut, drones._drone_work_bucket_row,
                     drones._drone_work_money_cells):
            with self.subTest(function=func.__name__):
                source = inspect.getsource(func)
                self.assertIn('DRONE-RECEIVED-BLANK-IS-ZERO-001', source)
                self.assertIn('2026-08-07', source)


class TestA3TheMiddleCellIsNeverNone(unittest.TestCase):
    """A-3: the check that fails if somebody reinstates the blank."""

    @classmethod
    def setUpClass(cls):
        seed()

    def test_a3_no_row_anywhere_produces_a_blank_received(self):
        rows = every_row(report_data())
        self.assertGreater(len(rows), 20, 'the sweep read almost nothing')
        for row in rows:
            with self.subTest(label=row.get('label')):
                self.assertIsNotNone(money_cells(row)[1])

    def test_a3_including_the_rows_whose_other_columns_are_blank(self):
        """The case that matters: blank neighbours, numeric middle."""
        blanks = [row for row in every_row(report_data())
                  if money_cells(row)[0] is None]
        self.assertTrue(blanks, 'no unpriced row in the fixture at all')
        for row in blanks:
            with self.subTest(label=row.get('label')):
                amount, received, outstanding = money_cells(row)
                self.assertIsNone(amount)
                self.assertIsNone(outstanding)
                self.assertIsNotNone(received)
                self.assertEqual(0, received)

    def test_a3_the_check_can_fail(self):
        """Negative control for the control: a planted blank is caught."""
        import drones
        planted = {'label': 'planted', 'jobs': 2, 'area': 0.0,
                   'amount': 0.0, 'received': 0.0, 'outstanding': 0.0,
                   'no_amount': 2, 'no_received': 2}
        self.assertEqual(0.0, drones._drone_work_money_cells(planted)[1])
        # what the pre-commit rule would have produced for the same dict
        self.assertIsNone(
            drones._drone_money_or_blank(planted, 'received', 'no_received'),
            'the old rule no longer blanks -- then A-3 proves nothing')


class TestA4NegativeControl(PreCommitMixin, unittest.TestCase):
    """A-4: at de97c8d the same fixture returns None in the middle."""

    @classmethod
    def setUpClass(cls):
        seed()

    def test_a4_pre_commit_blanks_the_received_cell(self):
        module = self.pre_commit()
        row = by_label(report_data()['by_customer'])[ALL_NULL]
        before = module._drone_work_money_cells(row)
        self.assertIsNone(before[1],
                          'the control is measuring nothing: de97c8d already '
                          'returns a number here')
        self.assertIsNone(before[0])
        self.assertIsNone(before[2])

    def test_a4_pre_commit_blanks_it_for_no_received_too(self):
        module = self.pre_commit()
        row = by_label(report_data()['by_customer'])[NO_RECEIVED]
        before = module._drone_work_money_cells(row)
        self.assertEqual(13000000.0, before[0])
        self.assertIsNone(before[1])
        self.assertEqual(13000000.0, before[2])

    def test_a4_and_this_commit_does_not(self):
        rows = by_label(report_data()['by_customer'])
        for label in (ALL_NULL, NO_RECEIVED):
            with self.subTest(group=label):
                self.assertEqual(0, money_cells(rows[label])[1])

    def test_a4_the_control_is_reading_the_other_module(self):
        import drones
        module = self.pre_commit()
        self.assertIsNot(module, drones)
        self.assertNotEqual(module.__file__, drones.__file__)


if __name__ == '__main__':
    unittest.main(verbosity=2)
