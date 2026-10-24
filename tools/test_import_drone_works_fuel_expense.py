#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""FIN-RECON-002: «Ёкилги ва бошка харажатлар» is an expense column.

The June 2026 Gijduvon book (sheet «свод ичи (Усмон)») carries it next to
«Бошка харажатлар». Before the phrase was added the importer summed only the
second one and reported the first as «header matching no known phrase».
Synthetic rows only; no customer records, no database.

Run: python -m unittest tools.test_import_drone_works_fuel_expense -v
"""
import os
import sys
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
for directory in (HERE, ROOT):
    if directory not in sys.path:
        sys.path.insert(0, directory)

import import_drone_works as imp  # noqa: E402

FUEL = 'Ёкилги ва бошка харажатлар'
# The header exactly as the book writes it: line breaks inside the cells.
HEADER = ['№', 'ФХ номи', 'Майдон (га)', 'Хизмат кўрсатиш суммаси',
          'Жами сумма\n(обьём)', 'Бошка харажатлар', 'Ёкилги \nва бошка харажатлар',
          'Кирим қилинган\n(Фактура)', 'Дрон \nишлаган сана', '*Изоҳ']


class SyntheticSheet:
    def __init__(self, rows):
        self.rows = rows

    def iter_rows(self, values_only=False):
        return iter(self.rows)


def parse():
    ws = SyntheticSheet([
        ('Справка (июнь ойи)',),
        tuple(HEADER),
        (1, 'Мисол фх', 10.0, 200000, 2000000, 250000, 305000, 0, '06.06.2026', None),
        (2, 'Бошқа фх', 5.0, 200000, 1000000, 250000, None, 0, '07.06.2026', None),
    ])
    rows, report = imp.parse_sheet(ws, 'Book Июнь.xlsx', 'свод ичи (Усмон)', '2026-06', None)
    return {r['customer_raw']: r for r in rows}, report


class without_the_phrase(object):
    """The importer as it was on 2026-10-22 -- for the negative controls."""

    def __enter__(self):
        self.saved = imp.EXPENSE_PHRASES
        imp.EXPENSE_PHRASES = tuple(p for p in self.saved if p != FUEL)
        imp.PHRASE_INDEX = imp._build_phrase_index()

    def __exit__(self, *exc):
        imp.EXPENSE_PHRASES = self.saved
        imp.PHRASE_INDEX = imp._build_phrase_index()


class FuelExpenseColumnTests(unittest.TestCase):

    def test_the_header_is_an_expense_column_and_not_unmatched(self):
        mapping = imp.map_columns(HEADER)
        self.assertEqual(len(mapping.expenses), 2)
        self.assertEqual(mapping.unmatched, [])

    def test_other_costs_is_the_sum_of_both_columns(self):
        rows, _report = parse()
        self.assertAlmostEqual(rows['Мисол фх']['other_costs'], 555000.0, places=2)

    def test_an_empty_fuel_cell_leaves_the_other_column(self):
        rows, _report = parse()
        self.assertAlmostEqual(rows['Бошқа фх']['other_costs'], 250000.0, places=2)

    def test_negative_control_without_the_phrase_the_costs_are_understated(self):
        with without_the_phrase():
            mapping = imp.map_columns(HEADER)
            rows, _report = parse()
        self.assertEqual(len(mapping.expenses), 1)
        self.assertEqual(len(mapping.unmatched), 1)
        self.assertAlmostEqual(rows['Мисол фх']['other_costs'], 250000.0, places=2)

    def test_the_shorter_phrase_does_not_swallow_the_longer_one(self):
        # «Бошка харажатлар» is a suffix of the new phrase; matching is exact,
        # so each header lands in its own column and nothing is counted twice.
        mapping = imp.map_columns(HEADER)
        self.assertEqual(sorted(i for i, _h in mapping.expenses), [5, 6])


if __name__ == '__main__':
    unittest.main()
