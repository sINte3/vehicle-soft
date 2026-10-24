#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""tools/drone_alias_decisions.py: only what the owner decided, all of it, atomically.

Synthetic book and database (the real drone_works / drone_customers DDL from
migrate_drones_works_001/002). Cases asked for by the review of 23.10.2026:
alias confirmed, alias ambiguous / HOLD, no fuzzy pick, re-import +0, May
untouched -- plus every refusal of the conflict matrix, the atomicity of the
registry row and the recovery of an interrupted run.

Run: python -m unittest tools.test_drone_alias_decisions -v
"""
import contextlib
import csv
import hashlib
import io
import json
import os
import shutil
import sqlite3
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
for directory in (HERE, ROOT):
    if directory not in sys.path:
        sys.path.insert(0, directory)

import drone_alias_decisions as tool  # noqa: E402
import drone_import_alias_candidates as cand  # noqa: E402
import import_drone_works as idw  # noqa: E402
import migrate_drones_works_001 as m1  # noqa: E402
from openpyxl import Workbook  # noqa: E402

BOOK = 'Тест ПТЗ Июнь.xlsx'
MULTILINE = 'Тест ПТЗ ерлари\n(Корахони худуд)'
SIMILAR = 'Маълум фх.'            # one dot away from card 7 -- must NOT be linked to it by itself
ROWS = (('Маълум фх', 10.0, 2000000), (MULTILINE, 3.5, 299715.5), (MULTILINE, 4.1, 351095.3),
        ('Янги фх', 5.0, 1000000), (SIMILAR, 2.0, 400000))


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
    CREATE TABLE drone_operators (id INTEGER PRIMARY KEY, full_name VARCHAR(200), subdivision_name VARCHAR(120),
                                  is_active BOOLEAN DEFAULT 1);
    CREATE TABLE drone_units (id INTEGER PRIMARY KEY, subdivision_name VARCHAR(120));
    ''')
    for ddl in (m1.CREATE_DRONE_CUSTOMERS, m1.CREATE_DRONE_CUSTOMER_ALIASES, m1.CREATE_DRONE_WORKS):
        con.execute(ddl)
    con.execute('ALTER TABLE drone_works ADD COLUMN received_kind VARCHAR(20)')
    con.executemany('INSERT INTO drone_customers (id, name, customer_type, is_active) VALUES (?, ?, ?, ?)',
                    [(7, 'Маълум фх', 'external', 1), (8, 'Тест ПТЗ ерлари', 'external', 1),
                     (9, 'Ёпиқ фх', 'external', 0), (10, 'Янги фх', 'external', 1)])
    con.execute('INSERT INTO drone_customer_aliases (raw_name, normalized_key, drone_customer_id, is_active) '
                'VALUES (?, ?, 7, 1)', ('Маълум фх', idw.customer_key('Маълум фх')))
    # May: closed month, must stay byte-identical through everything below
    con.executemany('INSERT INTO drone_works (period_month, work_date_from, customer_raw, drone_customer_id, area_ha, '
                    'amount, payment_type, source_file, source_sheet, source_row) VALUES (?,?,?,?,?,?,?,?,?,?)',
                    [('2026-05', '2026-05-%02d' % d, 'Маълум фх', 7, 10.0 + d, 2000000 + d, 'transfer',
                      'Тест ПТЗ МАЙ.xlsx', 'свод ичи (Тест)', d) for d in range(1, 6)])
    con.commit()
    con.close()


def file_sha(path):
    with open(path, 'rb') as fh:
        return hashlib.sha256(fh.read()).hexdigest()


class AliasDecisionsTest(unittest.TestCase):

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        t = self.tmp.name
        self.books = os.path.join(t, 'books')
        os.makedirs(self.books)
        make_book(os.path.join(self.books, BOOK))
        self.manifest = os.path.join(t, 'manifest.txt')
        with open(self.manifest, 'w', encoding='utf-8') as fh:
            fh.write('%s    2026-06\n' % BOOK)
        self.db = os.path.join(t, 'transport.db')
        make_db(self.db)
        with contextlib.redirect_stdout(io.StringIO()):
            cand.main(['--dir', self.books, '--manifest', self.manifest, '--db', self.db,
                       '--out', os.path.join(t, 'cand')])
        with open(os.path.join(t, 'cand', 'NEW_CUSTOMER_DECISIONS.csv'), encoding='utf-8-sig') as fh:
            self.template = list(csv.DictReader(fh, delimiter=';'))
        self.receipt = os.path.join(t, 'receipt.json')
        self.n = 0

    def tearDown(self):
        self.tmp.cleanup()

    def key(self, raw):
        return idw.customer_key(raw)

    def table(self, decisions, extra=()):
        """decisions: {raw spelling: (DECISION, TARGET_CARD_ID, NEW_CARD_TYPE)}"""
        self.n += 1
        path = os.path.join(self.tmp.name, 'decisions_%d.csv' % self.n)
        rows = []
        for r in self.template:
            raw = [s for s in decisions if self.key(s) == r['normalized_key']]
            if raw:
                d, tid, ctype = decisions[raw[0]]
                rows.append(dict(r, DECISION=d, TARGET_CARD_ID=tid, NEW_CARD_TYPE=ctype))
        rows += [dict({k: '' for k in self.template[0]}, **e) for e in extra]
        with open(path, 'w', encoding='utf-8-sig', newline='') as fh:
            w = csv.DictWriter(fh, fieldnames=list(self.template[0].keys()), delimiter=';')
            w.writeheader()
            w.writerows(rows)
        return path

    def good(self):
        return self.table({MULTILINE: ('ALIAS', '8', ''), 'Янги фх': ('ALIAS', '10', ''),
                           SIMILAR: ('NEW', '', 'internal')})

    def run_tool(self, table, *extra):
        with contextlib.redirect_stdout(io.StringIO()) as buf:
            rc = tool.main(['--decisions', table, '--dir', self.books, '--manifest', self.manifest,
                            '--db', self.db] + list(extra))
        return rc, buf.getvalue()

    def dry(self, table):
        return self.run_tool(table, '--out', os.path.join(self.tmp.name, 'dry_%d' % self.n))

    def apply(self, table):
        return self.run_tool(table, '--apply', '--receipt', self.receipt)

    def directory_state(self):
        con = sqlite3.connect(self.db)
        try:
            return (con.execute('SELECT * FROM drone_customers ORDER BY id').fetchall(),
                    con.execute('SELECT * FROM drone_customer_aliases ORDER BY id').fetchall(),
                    con.execute('SELECT * FROM drone_works ORDER BY id').fetchall())
        finally:
            con.close()

    def registry(self):
        con = sqlite3.connect(self.db)
        try:
            if not con.execute("SELECT 1 FROM sqlite_master WHERE name = 'schema_migrations'").fetchone():
                return []
            return [r[0] for r in con.execute('SELECT name FROM schema_migrations')]
        finally:
            con.close()

    def importer(self, *extra):
        with contextlib.redirect_stdout(io.StringIO()) as buf:
            rc = idw.main(['--dir', self.books, '--manifest', self.manifest, '--db', self.db,
                           '--report', os.path.join(self.tmp.name, 'rep_%d.txt' % len(os.listdir(self.tmp.name)))]
                          + list(extra))
        return rc, buf.getvalue()

    # ---- confirmed decisions ---------------------------------------------------

    def test_confirmed_aliases_and_a_new_card_land_exactly_as_decided(self):
        rc, out = self.dry(self.good())
        self.assertEqual(rc, 0, out)
        self.assertIn('RESULT: DRY RUN OK', out)
        rc, out = self.apply(self.good())
        self.assertEqual(rc, 0, out)
        self.assertIn('RESULT: APPLIED', out)
        con = sqlite3.connect(self.db)
        aliases = dict(con.execute('SELECT normalized_key, drone_customer_id FROM drone_customer_aliases'))
        new_card = con.execute('SELECT id, customer_type FROM drone_customers WHERE name = ?', (SIMILAR,)).fetchone()
        con.close()
        self.assertEqual(aliases[self.key(MULTILINE)], 8)
        self.assertEqual(aliases[self.key('Янги фх')], 10)
        self.assertEqual(aliases[self.key(SIMILAR)], new_card[0])
        self.assertEqual(new_card[1], 'internal')
        self.assertEqual(len(self.registry()), 1)
        rc, out = self.importer()
        self.assertIn('customers to be created       : 0', out)

    def test_a_similar_spelling_is_never_linked_to_the_similar_card_by_itself(self):
        self.apply(self.good())
        con = sqlite3.connect(self.db)
        target = con.execute('SELECT drone_customer_id FROM drone_customer_aliases WHERE normalized_key = ?',
                             (self.key(SIMILAR),)).fetchone()[0]
        con.close()
        self.assertNotEqual(target, 7)

    def test_dry_run_writes_nothing_to_the_database(self):
        before = file_sha(self.db)
        rc, out = self.dry(self.good())
        self.assertEqual(rc, 0, out)
        self.assertEqual(file_sha(self.db), before)
        self.assertEqual(self.registry(), [])

    def test_the_control_diff_lists_every_change(self):
        self.apply(self.good())
        with open(self.receipt + '.diff.csv', encoding='utf-8-sig') as fh:
            actions = sorted(r['action'] for r in csv.DictReader(fh, delimiter=';'))
        self.assertEqual(actions, ['CREATE_ALIAS', 'CREATE_ALIAS', 'CREATE_ALIAS', 'CREATE_CARD'])

    def test_a_second_run_of_the_same_table_is_already_applied(self):
        self.apply(self.good())
        state = self.directory_state()
        os.remove(self.receipt)
        rc, out = self.apply(self.good())
        self.assertEqual(rc, 0, out)
        self.assertIn('RESULT: ALREADY APPLIED', out)
        self.assertEqual(self.directory_state(), state)

    # ---- HOLD and ambiguity ------------------------------------------------------

    def test_hold_is_reported_by_the_dry_run_and_blocks_apply(self):
        table = self.table({MULTILINE: ('HOLD', '', ''), 'Янги фх': ('ALIAS', '10', ''),
                            SIMILAR: ('NEW', '', 'external')})
        rc, out = self.dry(table)
        self.assertEqual(rc, 0, out)
        self.assertIn('IMPORT BLOCKED', out)
        state = self.directory_state()
        rc, out = self.apply(table)
        self.assertEqual(rc, 1, out)
        self.assertIn('RESULT: NOT APPLIED', out)
        self.assertEqual(self.directory_state(), state)
        self.assertEqual(self.registry(), [])

    def test_every_conflict_of_the_matrix_refuses_the_whole_table(self):
        cases = {
            'E_DECISION': self.table({MULTILINE: ('MAYBE', '', ''), 'Янги фх': ('ALIAS', '10', ''),
                                      SIMILAR: ('NEW', '', 'external')}),
            'E_KEY_MISSING': self.table({MULTILINE: ('ALIAS', '8', ''), 'Янги фх': ('ALIAS', '10', '')}),
            'E_KEY_UNKNOWN': self.table({MULTILINE: ('ALIAS', '8', ''), 'Янги фх': ('ALIAS', '10', ''),
                                         SIMILAR: ('NEW', '', 'external')},
                                        extra=[{'normalized_key': 'маълум фх', 'DECISION': 'ALIAS',
                                                'TARGET_CARD_ID': '7'}]),
            'E_TARGET_MISSING': self.table({MULTILINE: ('ALIAS', '99', ''), 'Янги фх': ('ALIAS', '10', ''),
                                            SIMILAR: ('NEW', '', 'external')}),
            'E_TARGET_INACTIVE': self.table({MULTILINE: ('ALIAS', '9', ''), 'Янги фх': ('ALIAS', '10', ''),
                                             SIMILAR: ('NEW', '', 'external')}),
            'E_TYPE': self.table({MULTILINE: ('ALIAS', '8', ''), 'Янги фх': ('ALIAS', '10', ''),
                                  SIMILAR: ('NEW', '', '')}),
            'E_EXTRA_FIELDS': self.table({MULTILINE: ('ALIAS', '8', 'internal'), 'Янги фх': ('ALIAS', '10', ''),
                                          SIMILAR: ('NEW', '', 'external')}),
            'E_NEW_NAME_TAKEN': self.table({MULTILINE: ('ALIAS', '8', ''), 'Янги фх': ('NEW', '', 'external'),
                                            SIMILAR: ('NEW', '', 'external')}),
            'E_TARGET_ID_FORMAT': self.table({MULTILINE: ('ALIAS', 'eight', ''), 'Янги фх': ('ALIAS', '10', ''),
                                              SIMILAR: ('NEW', '', 'external')}),
            'E_SET_TYPE_GUARD': self.table({MULTILINE: ('ALIAS', '8', ''), 'Янги фх': ('ALIAS', '10', ''),
                                            SIMILAR: ('NEW', '', 'external')},
                                           extra=[{'DECISION': 'SET_TYPE', 'TARGET_CARD_ID': '8',
                                                   'NEW_CARD_TYPE': 'internal',
                                                   'new_card_name_as_imported': 'Бошқа ном'}]),
        }
        state = self.directory_state()
        for code, table in cases.items():
            with self.subTest(code=code):
                rc, out = self.apply(table)
                self.assertEqual(rc, 1, out)
                self.assertIn(code, out)
                self.assertEqual(self.directory_state(), state)
                self.assertEqual(self.registry(), [])

    def test_a_duplicated_decision_is_refused(self):
        path = self.good()
        with open(path, encoding='utf-8-sig') as fh:
            lines = fh.read().splitlines()
        with open(path, 'w', encoding='utf-8-sig') as fh:
            fh.write('\n'.join(lines + [lines[1]]) + '\n')
        rc, out = self.apply(path)
        self.assertEqual(rc, 1, out)
        self.assertIn('E_DUP_KEY', out)

    # ---- SET_TYPE ----------------------------------------------------------------

    def test_set_type_moves_only_the_type_of_the_named_card(self):
        table = self.table({MULTILINE: ('ALIAS', '8', ''), 'Янги фх': ('ALIAS', '10', ''),
                            SIMILAR: ('NEW', '', 'external')},
                           extra=[{'DECISION': 'SET_TYPE', 'TARGET_CARD_ID': '8', 'NEW_CARD_TYPE': 'internal',
                                   'new_card_name_as_imported': 'Тест ПТЗ ерлари'}])
        def card_8():
            con = sqlite3.connect(self.db)
            try:
                return (con.execute('SELECT * FROM drone_customers WHERE id = 8').fetchone(),
                        [r[1] for r in con.execute('PRAGMA table_info(drone_customers)')])
            finally:
                con.close()
        before, cols = card_8()
        rc, out = self.apply(table)
        self.assertEqual(rc, 0, out)
        after, _cols = card_8()
        i = cols.index('customer_type')
        self.assertEqual(after[i], 'internal')
        self.assertEqual(before[:i] + before[i + 1:], after[:i] + after[i + 1:])

    # ---- the import afterwards ------------------------------------------------------

    def test_reimport_adds_nothing_and_may_stays_byte_identical(self):
        con = sqlite3.connect(self.db)
        may = con.execute("SELECT * FROM drone_works WHERE period_month = '2026-05' ORDER BY id").fetchall()
        con.close()
        self.apply(self.good())
        rc, out = self.importer('--apply', '--batch', 'T1')
        self.assertIn('WRITTEN: 0 customers created, 5 works inserted, 0 already present', out)
        rc, out = self.importer('--apply', '--batch', 'T2')
        self.assertIn('WRITTEN: 0 customers created, 0 works inserted, 5 already present', out)
        con = sqlite3.connect(self.db)
        self.assertEqual(con.execute("SELECT * FROM drone_works WHERE period_month = '2026-05' ORDER BY id").fetchall(),
                         may)
        linked = dict(con.execute("SELECT customer_raw, drone_customer_id FROM drone_works "
                                  "WHERE period_month = '2026-06'"))
        con.close()
        self.assertEqual(linked['Янги фх'], 10)
        self.assertEqual(linked[MULTILINE], 8)
        self.assertEqual(linked['Маълум фх'], 7)

    # ---- atomicity and recovery --------------------------------------------------------

    def test_a_failing_registry_row_leaves_nothing(self):
        state = self.directory_state()

        def broken(*args):
            raise sqlite3.OperationalError('disk I/O error')
        saved = tool.record_in_transaction
        tool.record_in_transaction = broken
        try:
            rc, out = self.apply(self.good())
        finally:
            tool.record_in_transaction = saved
        self.assertEqual(rc, 1, out)
        self.assertEqual(self.directory_state(), state)
        self.assertFalse(os.path.exists(self.receipt + '.pending'))

    def test_a_crash_after_commit_is_completed_by_a_rerun(self):
        saved = tool.finalize_receipt

        def killed(*args):
            raise SystemExit('killed')
        tool.finalize_receipt = killed
        try:
            with self.assertRaises(SystemExit):
                self.apply(self.good())
        finally:
            tool.finalize_receipt = saved
        self.assertTrue(os.path.exists(self.receipt + '.pending'))
        rc, out = self.apply(self.good())
        self.assertEqual(rc, 0, out)
        self.assertIn('receipt completed now', out)
        with open(self.receipt, encoding='utf-8') as fh:
            self.assertEqual(json.load(fh)['status'], 'APPLIED')

    def test_a_crash_before_commit_leaves_nothing_and_a_rerun_applies(self):
        state = self.directory_state()
        saved = tool.commit

        def killed(conn):
            raise SystemExit('killed')
        tool.commit = killed
        try:
            with self.assertRaises(SystemExit):
                self.apply(self.good())
        finally:
            tool.commit = saved
        self.assertEqual(self.directory_state(), state)
        rc, out = self.apply(self.good())
        self.assertEqual(rc, 0, out)
        self.assertIn('stopped before COMMIT', out)

    def test_missing_input_and_existing_out(self):
        self.assertEqual(self.run_tool(os.path.join(self.tmp.name, 'nope.csv'), '--out', 'x')[0], 2)
        os.makedirs(os.path.join(self.tmp.name, 'taken'))
        self.assertEqual(self.run_tool(self.good(), '--out', os.path.join(self.tmp.name, 'taken'))[0], 4)


if __name__ == '__main__':
    unittest.main()
