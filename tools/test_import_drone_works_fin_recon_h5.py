#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""FIN-RECON: synthetic import safeguards; no customer records or live databases.

Run: python -m unittest tools.test_import_drone_works_fin_recon_h5 -v
"""
import datetime
import os
import sqlite3
import sys
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
for directory in (HERE, ROOT):
    if directory not in sys.path:
        sys.path.insert(0, directory)

import import_drone_works as imp


class SyntheticSheet:
    def __init__(self, rows):
        self.rows = rows

    def iter_rows(self, values_only=False):
        return iter(self.rows)


def example(operator='Operator Alpha', file='Team April.xlsx',
            sheet='свод ичи (Operator Alpha)'):
    return {
        'source_file': file, 'source_sheet': sheet, 'source_row': 4,
        'period_month': '2026-04', 'payment_type': 'cash',
        'operator_raw': operator, 'operator_source': 'sheet',
        'date_raw': '13.04.2026', 'note': None,
        'customer_raw': 'Example Farm', 'area_ha': 5.3,
        'work_date_from': datetime.date(2026, 4, 13),
        'amount': 1060000.0, 'other_costs': None,
        'received_amount': None, 'received_kind': None, 'price_per_ha': None,
    }


class ImportSafetyH5(unittest.TestCase):
    def test_work_note_with_wage_label_is_kept(self):
        book = SyntheticSheet([
            ['№', 'ФХ номи', 'Майдон (га)', 'Сана', 'Жами сумма', '*Изоҳ'],
            ['Нақд'],
            [1, 'Example Farm', 29.7, datetime.date(2026, 4, 13),
             5940000, 'Иш хаки for the team'],
            [2, 'Another Farm', 5.3, datetime.date(2026, 4, 13),
             1060000, None],
        ])
        rows, report = imp.parse_sheet(book, 'Team April.xlsx',
                                       'свод ичи (Operator Alpha)',
                                       '2026-04', 'Example unit')
        self.assertEqual(len(rows), 2)
        self.assertEqual(report.wage_rows, 0)
        self.assertEqual([r['area_ha'] for r in rows], [29.7, 5.3])

    def test_two_operators_do_not_merge(self):
        one = example()
        two = example(operator='Operator Beta', sheet='свод ичи (Operator Beta)')
        self.assertEqual(imp.duplicate_key(one), imp.duplicate_key(two))
        self.assertFalse(imp.proven_duplicate_work(one, two))

    def test_same_sheet_does_not_merge(self):
        one = example()
        two = example()
        two['source_row'] = 5
        self.assertFalse(imp.proven_duplicate_work(one, two))

    def test_mirrored_sheet_does_merge(self):
        one = example()
        two = example(sheet='свод ичи (mirror)')
        self.assertTrue(imp.proven_duplicate_work(one, two))

    def test_other_period_or_payment_never_merges(self):
        one = example()
        two = example(sheet='second')
        two['period_month'] = '2026-03'
        self.assertFalse(imp.proven_duplicate_work(one, two))
        two['period_month'] = '2026-04'
        two['payment_type'] = 'transfer'
        self.assertFalse(imp.proven_duplicate_work(one, two))

    def test_cross_file_requires_supporting_fields(self):
        one = example()
        two = example(file='Team_April.xlsx', sheet='mirror')
        self.assertTrue(imp.proven_duplicate_work(one, two))
        two['note'] = 'different supporting evidence'
        self.assertFalse(imp.proven_duplicate_work(one, two))

    def test_historical_wage_carryover_held(self):
        row = example()
        row['work_date_from'] = datetime.date(2025, 12, 28)
        row['note'] = 'Иш хаки not paid; carried to April'
        self.assertTrue(imp.is_crossperiod_wage_carryover(row))
        row['note'] = 'ordinary work description'
        self.assertFalse(imp.is_crossperiod_wage_carryover(row))
        row['work_date_from'] = datetime.date(2026, 4, 13)
        row['note'] = 'Иш хаки discussed'
        self.assertFalse(imp.is_crossperiod_wage_carryover(row))

    def test_filename_variants_are_only_a_safety_signal(self):
        self.assertEqual(imp.source_file_fold('Team_Drones_2026.xlsx'),
                         imp.source_file_fold('Team Drones 2026.xlsx'))
        connection = sqlite3.connect(':memory:')
        try:
            connection.execute(
                'CREATE TABLE drone_works (source_file TEXT, source_sheet TEXT, '
                'source_row TEXT, area_ha REAL)')
            connection.execute(
                'INSERT INTO drone_works VALUES (?, ?, ?, ?)',
                ('Team_Drones_2026.xlsx', 'detail', '4', 5.3))
            row = {'source_file': 'Team Drones 2026.xlsx',
                   'source_sheet': 'detail', 'source_row': 4, 'area_ha': 5.3}
            self.assertEqual(len(imp.filename_alias_conflicts(connection, [row])), 1)
            self.assertEqual(connection.execute(
                'SELECT COUNT(*) FROM drone_works').fetchone()[0], 1)
            row['source_file'] = 'Team_Drones_2026.xlsx'
            self.assertEqual(imp.filename_alias_conflicts(connection, [row]), [])
            row['source_file'] = 'Team Drones 2026.xlsx'
            row['area_ha'] = 8.1
            self.assertEqual(imp.filename_alias_conflicts(connection, [row]), [])
        finally:
            connection.close()


if __name__ == '__main__':
    unittest.main()
