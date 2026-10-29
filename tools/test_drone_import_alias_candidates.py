#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""tools/drone_import_alias_candidates.py: the decision table adds up.

Synthetic book and database only. The case that broke the first version: a
farm name with a line break inside the cell. The importer keys it with «\\n»;
a table keyed on the register's « / » spelling lost its 2 jobs (review of
23.10.2026). The negative control re-creates that keying and the invariant
must refuse it.

Run: python -m unittest tools.test_drone_import_alias_candidates -v
"""
import contextlib
import csv
import hashlib
import io
import os
import sqlite3
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
for directory in (HERE, ROOT):
    if directory not in sys.path:
        sys.path.insert(0, directory)

import drone_import_alias_candidates as tool  # noqa: E402
import import_drone_works as idw  # noqa: E402
from openpyxl import Workbook  # noqa: E402

BOOK = 'Тест ПТЗ Июнь.xlsx'
MULTILINE = 'Тест ПТЗ ерлари\n(Корахони худуд)'
ROWS = (  # (farm, hectares, amount)
    ('Маълум фх', 10.0, 2000000),            # alias exists -> linked
    (MULTILINE, 3.5, 299715.5),             # new, line break inside the cell
    (MULTILINE, 4.1, 351095.3),
    ('Янги фх', 5.0, 1000000),               # new
)


def make_book(path):
    wb = Workbook()
    ws = wb.active
    ws.title = 'свод ичи (Тест)'
    ws.append(['Справка (июнь ойи)'])
    ws.append(['№', 'ФХ номи', 'Майдон (га)', 'Хизмат кўрсатиш суммаси', 'Жами сумма (обьём)',
               'Кирим қилинган', 'Дрон ишлаган сана'])
    for i, (farm, area, amount) in enumerate(ROWS, 1):
        ws.append([i, farm, area, round(amount / area, 2), amount, 0, '0%d.06.2026' % i])
    wb.save(path)


def make_db(path):
    con = sqlite3.connect(path)
    con.executescript('''
    CREATE TABLE drone_operators (id INTEGER PRIMARY KEY, full_name TEXT, subdivision_name TEXT);
    CREATE TABLE drone_units (id INTEGER PRIMARY KEY, subdivision_name TEXT);
    CREATE TABLE drone_customers (id INTEGER PRIMARY KEY, name TEXT);
    CREATE TABLE drone_customer_aliases (id INTEGER PRIMARY KEY, raw_name TEXT, normalized_key TEXT,
                                         drone_customer_id INTEGER, is_active BOOLEAN DEFAULT 1);
    CREATE TABLE drone_works (id INTEGER PRIMARY KEY, source_file TEXT, source_sheet TEXT, source_row INTEGER);
    ''')
    con.execute("INSERT INTO drone_customers VALUES (7, 'Маълум фх')")
    con.execute('INSERT INTO drone_customer_aliases (raw_name, normalized_key, drone_customer_id) VALUES (?, ?, 7)',
                ('Маълум фх', idw.customer_key('Маълум фх')))
    con.commit()
    con.close()


class AliasCandidatesTest(unittest.TestCase):

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.books = os.path.join(self.tmp.name, 'books')
        os.makedirs(self.books)
        make_book(os.path.join(self.books, BOOK))
        self.manifest = os.path.join(self.tmp.name, 'manifest.txt')
        with open(self.manifest, 'w', encoding='utf-8') as fh:
            fh.write('%s    2026-06\n' % BOOK)
        self.db = os.path.join(self.tmp.name, 'transport.db')
        make_db(self.db)
        self.out = os.path.join(self.tmp.name, 'out')

    def tearDown(self):
        self.tmp.cleanup()

    def run_tool(self, *extra):
        with contextlib.redirect_stdout(io.StringIO()) as buf:
            rc = tool.main(['--dir', self.books, '--manifest', self.manifest, '--db', self.db,
                            '--out', self.out] + list(extra))
        return rc, buf.getvalue()

    def decisions(self):
        with open(os.path.join(self.out, 'NEW_CUSTOMER_DECISIONS.csv'), encoding='utf-8-sig') as fh:
            return {r['new_card_name_as_imported']: r for r in csv.DictReader(fh, delimiter=';')}

    def test_the_table_adds_up_and_keeps_the_multiline_farm(self):
        rc, out = self.run_tool('--expect-rows', '4', '--expect-area', '22.6', '--expect-amount', '3650810.8')
        self.assertEqual(rc, 0, out)
        self.assertIn('INVARIANTS: OK', out)
        self.assertIn('linked to existing cards: 1 rows / 10.00 ha', out)
        self.assertIn('cards to create: 3 rows / 12.60 ha / 1650810.80 (2 cards)', out)
        rows = self.decisions()
        multi = [r for name, r in rows.items() if 'Корахони' in name][0]
        self.assertEqual((multi['rows'], multi['area_ha'], multi['line_break_in_name']), ('2', '7.6', 'YES'))
        self.assertEqual(multi['hint'], 'HOLDING_ENTITY?')

    def test_negative_control_register_style_keys_break_the_invariant(self):
        real = tool.group_key
        tool.group_key = lambda row: idw.customer_key(row['customer_raw'].replace('\n', ' / '))
        try:
            rc, out = self.run_tool()
        finally:
            tool.group_key = real
        self.assertEqual(rc, 1, out)
        self.assertIn('INVARIANTS: FAILED', out)

    def test_a_wrong_expected_total_is_refused(self):
        rc, out = self.run_tool('--expect-rows', '132')
        self.assertEqual(rc, 1)
        self.assertIn('run rows 4, expected 132', out)

    def test_the_database_is_not_written(self):
        with open(self.db, 'rb') as fh:
            before = hashlib.sha256(fh.read()).hexdigest()
        self.run_tool()
        with open(self.db, 'rb') as fh:
            self.assertEqual(hashlib.sha256(fh.read()).hexdigest(), before)

    def test_existing_out_and_missing_input_are_refused(self):
        os.makedirs(self.out)
        self.assertEqual(self.run_tool()[0], 3)
        os.rmdir(self.out)
        os.remove(self.db)
        self.assertEqual(self.run_tool()[0], 2)

    def test_a_generic_citizen_is_its_own_group_not_a_card_to_create(self):
        """FIN-RECON-002, 27.10.2026: «Фукаро» rows are counted, flagged and never offered as a new card."""
        wb_path = os.path.join(self.books, 'Фукаро Июнь.xlsx')
        wb = Workbook()
        ws = wb.active
        ws.title = 'свод ичи (Тест)'
        ws.append(['Справка (июнь ойи)'])
        ws.append(['№', 'ФХ номи', 'Майдон (га)', 'Хизмат кўрсатиш суммаси', 'Жами сумма (обьём)',
                   'Кирим қилинган', 'Дрон ишлаган сана'])
        ws.append([1, 'Фукаро', 5.0, 200000, 1000000, 0, '05.06.2026'])
        ws.append([2, 'Ромитан т. Фукаро', 5.95, 200000, 1190000, 0, '06.06.2026'])
        wb.save(wb_path)
        with open(self.manifest, 'a', encoding='utf-8') as fh:
            fh.write('Фукаро Июнь.xlsx    2026-06' + chr(10))
        rc, out = self.run_tool()
        self.assertEqual(rc, 0, out)
        self.assertIn('payer a citizen, unidentified (no card): 2 rows / 10.95 ha / 2190000.00 (2 spellings)', out)
        self.assertIn('cards to create: 3 rows / 12.60 ha / 1650810.80 (2 cards)', out)
        status = {name: r['import_status'] for name, r in self.decisions().items()}
        self.assertEqual(status['Фукаро'], 'UNIDENTIFIED')
        self.assertEqual(status['Ромитан т. Фукаро'], 'UNIDENTIFIED')
        self.assertEqual(status['Маълум фх'], 'MATCHED')

        # Negative control: the stage-4 partition, which had no such group, breaks the invariant.
        def old_partition(result):
            linked, new, empty, _unidentified = real(result)
            for key, rows in _unidentified.items():
                new.setdefault(key, []).extend(rows)
            return linked, new, empty, {}
        real = tool.partition
        tool.partition = old_partition
        try:
            import shutil
            shutil.rmtree(self.out)
            rc, out = self.run_tool()
        finally:
            tool.partition = real
        self.assertEqual(rc, 1, out)
        self.assertIn('INVARIANTS: FAILED', out)


if __name__ == '__main__':
    unittest.main()
