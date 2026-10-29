#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""tools/drone_alias_decisions.py: only what the owner decided, all of it, atomically.

Synthetic book and database (the real drone_works / drone_customers DDL from
migrate_drones_works_001/002 and the payer_unidentified column). The book
carries every case of the owner's decisions of 27.10.2026:

  Маълум фх                       MATCHED -> 7          KEEP
  Тест ПТЗ ерлари↵(Корахони худуд) CREATE (2 rows)       GROUP_NEW «Тест ПТЗ», internal
  Тест ПТЗ Зарангари худуд        CREATE                GROUP_NEW «Тест ПТЗ» -- the same new card
  Тест ПТЗ ерлари↵(Зарангари худуд) MATCHED -> 11 (territory, has May)  REPOINT to «Тест ПТЗ»
  Янги фх                         CREATE                ALIAS 10 with its name as a guard
  Маълум фх.                      CREATE                NEW internal -- never linked to 7 by itself
  Фукаро (2 rows)                 UNIDENTIFIED (alias -> 12 bypassed)  UNIDENTIFIED
  Жамшид (фукаро)                 MATCHED -> 13         KEEP -- a named citizen is not blocked
  Чорва фх                        CREATE                NEW external (HOLD in the HOLD tests)

plus every refusal of the conflict matrix, the negative controls for «Фукаро»
and for PTZ-to-territory, the atomicity of the registry row and the recovery
of an interrupted run. May (cards 7, 11, 12) must stay byte-identical.

Run: python -m unittest tools.test_drone_alias_decisions -v
"""
import contextlib
import csv
import hashlib
import io
import json
import os
import sqlite3
import sys
import tempfile
import unittest
from unittest import mock

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
KORAKHONI = 'Тест ПТЗ ерлари\n(Корахони худуд)'
ZARANGARI_NEW = 'Тест ПТЗ Зарангари худуд'
ZARANGARI_OLD = 'Тест ПТЗ ерлари\n(Зарангари худуд)'
SIMILAR = 'Маълум фх.'            # one dot away from card 7 -- must NOT be linked to it by itself
GROUP = 'Тест ПТЗ'
ROWS = (('Маълум фх', 10.0, 2000000), (KORAKHONI, 3.5, 299715.5), (KORAKHONI, 4.1, 351095.3),
        ('Янги фх', 5.0, 1000000), (SIMILAR, 2.0, 400000), (ZARANGARI_NEW, 7.0, 599431.0),
        (ZARANGARI_OLD, 4.0, 342532.0), ('Фукаро', 5.6, 1120000), ('Фукаро', 6.0, 1200000),
        ('Жамшид (фукаро)', 3.0, 600000), ('Чорва фх', 2.5, 500000))
TERRITORY, GENERIC, NAMED = 11, 12, 13


def make_book(path):
    wb = Workbook()
    ws = wb.active
    ws.title = 'свод ичи (Тест)'
    ws.append(['Справка (июнь ойи)'])
    ws.append(['№', 'ФХ номи', 'Майдон (га)', 'Хизмат кўрсатиш суммаси', 'Жами сумма (обьём)',
               'Кирим қилинган', 'Дрон ишлаган сана'])
    for i, (farm, area, amount) in enumerate(ROWS, 1):
        ws.append([i, farm, area, round(amount / area, 2), amount, 0, '%02d.06.2026' % i])
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
    con.execute('ALTER TABLE drone_works ADD COLUMN payer_unidentified BOOLEAN')
    con.executemany('INSERT INTO drone_customers (id, name, customer_type, is_active) VALUES (?, ?, ?, ?)',
                    [(7, 'Маълум фх', 'external', 1), (8, 'Тест ПТЗ ерлари', 'external', 1),
                     (9, 'Ёпиқ фх', 'external', 0), (10, 'Янги фх', 'external', 1),
                     (TERRITORY, ZARANGARI_OLD, 'external', 1), (GENERIC, 'Фукаро', 'external', 1),
                     (NAMED, 'Жамшид (фукаро)', 'external', 1)])
    for raw, cid in (('Маълум фх', 7), (ZARANGARI_OLD, TERRITORY), ('Фукаро', GENERIC), ('Жамшид (фукаро)', NAMED)):
        con.execute('INSERT INTO drone_customer_aliases (raw_name, normalized_key, drone_customer_id, is_active) '
                    'VALUES (?, ?, ?, 1)', (raw, idw.customer_key(raw), cid))
    # May: closed month, must stay byte-identical through everything below
    may = [('2026-05', '2026-05-%02d' % d, 'Маълум фх', 7, 10.0 + d, 2000000 + d, 'Тест ПТЗ МАЙ.xlsx', d)
           for d in range(1, 6)]
    may += [('2026-05', '2026-05-20', ZARANGARI_OLD, TERRITORY, 8.0, 685064, 'Тест ПТЗ МАЙ.xlsx', 20),
            ('2026-05', '2026-05-21', 'Фукаро', GENERIC, 5.64, 1128000, 'Тест ПТЗ МАЙ.xlsx', 21)]
    con.executemany('INSERT INTO drone_works (period_month, work_date_from, customer_raw, drone_customer_id, area_ha, '
                    "amount, payment_type, source_file, source_sheet, source_row) VALUES (?,?,?,?,?,?,'transfer',?,"
                    "'свод ичи (Тест)',?)", may)
    con.commit()
    con.close()


def file_sha(path):
    with open(path, 'rb') as fh:
        return hashlib.sha256(fh.read()).hexdigest()


def good_decisions():
    return {
        'Маълум фх': {'DECISION': 'KEEP', 'CURRENT_CARD_ID': '7'},
        KORAKHONI: {'DECISION': 'GROUP_NEW', 'NEW_CARD_NAME': GROUP, 'NEW_CARD_TYPE': 'internal'},
        ZARANGARI_NEW: {'DECISION': 'GROUP_NEW', 'NEW_CARD_NAME': GROUP, 'NEW_CARD_TYPE': 'internal'},
        ZARANGARI_OLD: {'DECISION': 'REPOINT', 'CURRENT_CARD_ID': str(TERRITORY), 'NEW_CARD_NAME': GROUP,
                        'EXPECTED_SOURCE_ROWS': None},
        'Янги фх': {'DECISION': 'ALIAS', 'TARGET_CARD_ID': '10', 'TARGET_CARD_NAME': 'Янги фх'},
        SIMILAR: {'DECISION': 'NEW', 'NEW_CARD_TYPE': 'internal'},
        'Фукаро': {'DECISION': 'UNIDENTIFIED', 'CURRENT_CARD_ID': str(GENERIC), 'EXPECTED_SOURCE_ROWS': None},
        'Жамшид (фукаро)': {'DECISION': 'KEEP', 'CURRENT_CARD_ID': str(NAMED)},
        'Чорва фх': {'DECISION': 'NEW', 'NEW_CARD_TYPE': 'external'},
    }


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

    def table(self, changes=None, drop=(), extra=()):
        """The good table, with {spelling: {column: value}} changes, spellings dropped and extra raw lines.

        EXPECTED_SOURCE_ROWS None means «copy the candidates table's source_rows»."""
        decisions = good_decisions()
        for raw, cols in (changes or {}).items():
            decisions[raw] = cols
        self.n += 1
        path = os.path.join(self.tmp.name, 'decisions_%d.csv' % self.n)
        rows = []
        for r in self.template:
            raw = [s for s in decisions if self.key(s) == r['normalized_key'] and s not in drop]
            if raw:
                cols = dict(decisions[raw[0]])
                if 'EXPECTED_SOURCE_ROWS' in cols and cols['EXPECTED_SOURCE_ROWS'] is None:
                    cols['EXPECTED_SOURCE_ROWS'] = r['source_rows']
                rows.append(dict(r, **cols))
        rows += [dict({k: '' for k in self.template[0]}, **e) for e in extra]
        with open(path, 'w', encoding='utf-8-sig', newline='') as fh:
            w = csv.DictWriter(fh, fieldnames=list(self.template[0].keys()), delimiter=';')
            w.writeheader()
            w.writerows(rows)
        return path

    def run_tool(self, table, *extra):
        with contextlib.redirect_stdout(io.StringIO()) as buf:
            rc = tool.main(['--decisions', table, '--dir', self.books, '--manifest', self.manifest,
                            '--db', self.db] + list(extra))
        return rc, buf.getvalue()

    def dry(self, table, *extra):
        return self.run_tool(table, '--out', os.path.join(self.tmp.name, 'dry_%d' % self.n), *extra)

    def apply(self, table, *extra):
        return self.run_tool(table, '--apply', '--receipt', self.receipt, *extra)

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

    def aliases(self):
        con = sqlite3.connect(self.db)
        try:
            return dict(con.execute('SELECT normalized_key, drone_customer_id FROM drone_customer_aliases'))
        finally:
            con.close()

    def importer(self, *extra):
        with contextlib.redirect_stdout(io.StringIO()) as buf:
            rc = idw.main(['--dir', self.books, '--manifest', self.manifest, '--db', self.db,
                           '--report', os.path.join(self.tmp.name, 'rep_%d.txt' % len(os.listdir(self.tmp.name)))]
                          + list(extra))
        return rc, buf.getvalue()

    # ---- confirmed decisions ---------------------------------------------------

    def test_the_candidates_table_gives_every_spelling_a_status(self):
        status = {r['normalized_key']: r['import_status'] for r in self.template}
        self.assertEqual(status[self.key('Фукаро')], 'UNIDENTIFIED')
        self.assertEqual(status[self.key(ZARANGARI_OLD)], 'MATCHED')
        self.assertEqual(status[self.key('Жамшид (фукаро)')], 'MATCHED')
        self.assertEqual(status[self.key(KORAKHONI)], 'CREATE')
        self.assertEqual(len(status), 9)
        fukaro = [r for r in self.template if r['normalized_key'] == 'фукаро'][0]
        self.assertEqual(json.loads(fukaro['source_rows']), [[BOOK, 'свод ичи (Тест)', 10], [BOOK, 'свод ичи (Тест)', 11]])

    def test_every_decision_lands_exactly_as_decided(self):
        rc, out = self.dry(self.table())
        self.assertEqual(rc, 0, out)
        self.assertIn('RESULT: DRY RUN OK', out)
        rc, out = self.apply(self.table())
        self.assertEqual(rc, 0, out)
        self.assertIn('RESULT: APPLIED', out)
        con = sqlite3.connect(self.db)
        groups = con.execute('SELECT id, customer_type FROM drone_customers WHERE name = ?', (GROUP,)).fetchall()
        similar = con.execute('SELECT id, customer_type FROM drone_customers WHERE name = ?', (SIMILAR,)).fetchone()
        con.close()
        self.assertEqual(len(groups), 1, 'ONE card for the whole group')
        gid, gtype = groups[0]
        self.assertEqual(gtype, 'internal')
        aliases = self.aliases()
        for raw in (KORAKHONI, ZARANGARI_NEW, ZARANGARI_OLD):
            self.assertEqual(aliases[self.key(raw)], gid, raw)
        self.assertEqual(aliases[self.key('Янги фх')], 10)
        self.assertEqual(aliases[self.key(SIMILAR)], similar[0])
        self.assertEqual(similar[1], 'internal')
        self.assertEqual(aliases['фукаро'], GENERIC, 'the old alias is not touched, only bypassed')
        self.assertEqual(len(self.registry()), 1)
        rc, out = self.importer()
        self.assertIn('customers to be created       : 0', out)
        self.assertIn('payer a citizen, unidentified : 2 rows', out)

    def test_reimport_adds_nothing_may_stays_and_rows_land_as_decided(self):
        con = sqlite3.connect(self.db)
        may = con.execute("SELECT * FROM drone_works WHERE period_month = '2026-05' ORDER BY id").fetchall()
        con.close()
        self.assertEqual(self.apply(self.table())[0], 0)
        rc, out = self.importer('--apply', '--batch', 'T1')
        self.assertIn('WRITTEN: 0 customers created, 11 works inserted, 0 already present', out)
        rc, out = self.importer('--apply', '--batch', 'T2')
        self.assertIn('WRITTEN: 0 customers created, 0 works inserted, 11 already present', out)
        con = sqlite3.connect(self.db)
        try:
            self.assertEqual(con.execute("SELECT * FROM drone_works WHERE period_month = '2026-05' ORDER BY id")
                             .fetchall(), may)
            june = con.execute("SELECT customer_raw, drone_customer_id, payer_unidentified FROM drone_works "
                               "WHERE period_month = '2026-06'").fetchall()
            gid = con.execute('SELECT id FROM drone_customers WHERE name = ?', (GROUP,)).fetchone()[0]
        finally:
            con.close()
        on = {}
        for raw, cid, flag in june:
            on.setdefault(raw, set()).add((cid, flag))
        self.assertEqual(on[KORAKHONI], {(gid, None)})
        self.assertEqual(on[ZARANGARI_NEW], {(gid, None)})
        self.assertEqual(on[ZARANGARI_OLD], {(gid, None)})
        self.assertEqual(on['Фукаро'], {(None, 1)})
        self.assertEqual(on['Жамшид (фукаро)'], {(NAMED, None)})
        self.assertEqual(on['Маълум фх'], {(7, None)})

    def test_a_repoint_does_not_relink_the_stored_may_work(self):
        self.apply(self.table())
        con = sqlite3.connect(self.db)
        try:
            self.assertEqual(con.execute("SELECT drone_customer_id FROM drone_works WHERE period_month = '2026-05' "
                                         'AND source_row = 20').fetchone()[0], TERRITORY)
        finally:
            con.close()

    def test_a_similar_spelling_is_never_linked_to_the_similar_card_by_itself(self):
        self.apply(self.table())
        self.assertNotEqual(self.aliases()[self.key(SIMILAR)], 7)

    def test_dry_run_writes_nothing_and_writes_the_routing_matrix(self):
        before = file_sha(self.db)
        rc, out = self.dry(self.table())
        self.assertEqual(rc, 0, out)
        self.assertEqual(file_sha(self.db), before)
        self.assertEqual(self.registry(), [])
        with open(os.path.join(self.tmp.name, 'dry_%d' % self.n, 'KEY_ROUTING.csv'), encoding='utf-8-sig') as fh:
            routing = {r['normalized_key']: r for r in csv.DictReader(fh, delimiter=';')}
        self.assertEqual(len(routing), 9)
        self.assertEqual(routing['фукаро']['card_after'], 'UNIDENTIFIED')
        self.assertEqual(routing[self.key(ZARANGARI_OLD)]['card_before'], str(TERRITORY))
        self.assertEqual(routing[self.key(ZARANGARI_OLD)]['card_after'], routing[self.key(KORAKHONI)]['card_after'])
        self.assertIn('GROUP_NEW      1 card(s) for 2 spelling(s): 3 rows', out)

    def test_the_control_diff_lists_every_change(self):
        self.apply(self.table())
        with open(self.receipt + '.diff.csv', encoding='utf-8-sig') as fh:
            actions = sorted(r['action'] for r in csv.DictReader(fh, delimiter=';'))
        self.assertEqual(actions, ['CREATE_ALIAS'] * 5 + ['CREATE_CARD'] * 3 + ['REPOINT_ALIAS'])

    def test_a_second_run_of_the_same_table_is_already_applied(self):
        self.apply(self.table())
        state = self.directory_state()
        os.remove(self.receipt)
        rc, out = self.apply(self.table())
        self.assertEqual(rc, 0, out)
        self.assertIn('RESULT: ALREADY APPLIED', out)
        self.assertEqual(self.directory_state(), state)

    # ---- HOLD ----------------------------------------------------------------------

    def test_hold_is_reported_by_the_dry_run_and_blocks_apply(self):
        table = self.table({'Чорва фх': {'DECISION': 'HOLD'}})
        rc, out = self.dry(table)
        self.assertEqual(rc, 0, out)
        self.assertIn('IMPORT BLOCKED', out)
        state = self.directory_state()
        rc, out = self.apply(table)
        self.assertEqual(rc, 1, out)
        self.assertIn('RESULT: NOT APPLIED', out)
        self.assertEqual(self.directory_state(), state)
        self.assertEqual(self.registry(), [])

    # ---- the conflict matrix -----------------------------------------------------------

    def test_every_conflict_of_the_matrix_refuses_the_whole_table(self):
        wrong_rows = json.dumps([[BOOK, 'свод ичи (Тест)', 99]], ensure_ascii=False)
        cases = {
            'E_DECISION': self.table({'Янги фх': {'DECISION': 'MAYBE'}}),
            'E_KEY_MISSING': self.table(drop=('Чорва фх',)),
            'E_KEY_UNKNOWN': self.table(extra=[{'normalized_key': 'йўқ фх', 'DECISION': 'NEW',
                                                'NEW_CARD_TYPE': 'external'}]),
            'E_TARGET_MISSING': self.table({'Янги фх': {'DECISION': 'ALIAS', 'TARGET_CARD_ID': '99',
                                                        'TARGET_CARD_NAME': 'Янги фх'}}),
            'E_TARGET_INACTIVE': self.table({'Янги фх': {'DECISION': 'ALIAS', 'TARGET_CARD_ID': '9',
                                                         'TARGET_CARD_NAME': 'Ёпиқ фх'}}),
            'E_TARGET_NAME': self.table({'Янги фх': {'DECISION': 'ALIAS', 'TARGET_CARD_ID': '10',
                                                     'TARGET_CARD_NAME': 'Бошқа ном'}}),
            'E_TYPE': self.table({SIMILAR: {'DECISION': 'NEW', 'NEW_CARD_TYPE': ''}}),
            'E_EXTRA_FIELDS': self.table({'Янги фх': {'DECISION': 'ALIAS', 'TARGET_CARD_ID': '10',
                                                      'TARGET_CARD_NAME': 'Янги фх', 'NEW_CARD_TYPE': 'internal'}}),
            'E_NEW_NAME_TAKEN': self.table({'Янги фх': {'DECISION': 'NEW', 'NEW_CARD_TYPE': 'external'}}),
            'E_TARGET_ID_FORMAT': self.table({'Янги фх': {'DECISION': 'ALIAS', 'TARGET_CARD_ID': 'ten',
                                                          'TARGET_CARD_NAME': 'Янги фх'}}),
            'E_DECISION_FOR_STATUS': self.table({'Маълум фх': {'DECISION': 'NEW', 'NEW_CARD_TYPE': 'external'}}),
            'E_CURRENT_CARD': self.table({'Маълум фх': {'DECISION': 'KEEP', 'CURRENT_CARD_ID': '99'}}),
            'E_SOURCE_ROWS_DIFFER': self.table({'Фукаро': {'DECISION': 'UNIDENTIFIED', 'CURRENT_CARD_ID': '12',
                                                           'EXPECTED_SOURCE_ROWS': wrong_rows}}),
            'E_SOURCE_ROWS_FORMAT': self.table({ZARANGARI_OLD: {'DECISION': 'REPOINT', 'CURRENT_CARD_ID': '11',
                                                                'NEW_CARD_NAME': GROUP}}),
            'E_REPOINT_TARGET': self.table({ZARANGARI_OLD: {'DECISION': 'REPOINT', 'CURRENT_CARD_ID': '11',
                                                            'NEW_CARD_NAME': GROUP, 'TARGET_CARD_ID': '10',
                                                            'TARGET_CARD_NAME': 'Янги фх',
                                                            'EXPECTED_SOURCE_ROWS': None}}),
            'E_REPOINT_GROUP_UNKNOWN': self.table({ZARANGARI_OLD: {'DECISION': 'REPOINT', 'CURRENT_CARD_ID': '11',
                                                                   'NEW_CARD_NAME': 'Бошқа гуруҳ',
                                                                   'EXPECTED_SOURCE_ROWS': None}}),
            'E_GROUP_TYPE': self.table({ZARANGARI_NEW: {'DECISION': 'GROUP_NEW', 'NEW_CARD_NAME': GROUP,
                                                        'NEW_CARD_TYPE': 'external'}}),
            'E_SET_TYPE_KEY': self.table(extra=[{'normalized_key': 'янги фх', 'DECISION': 'SET_TYPE'}]),
        }
        state = self.directory_state()
        for code, table in cases.items():
            with self.subTest(code=code):
                rc, out = self.apply(table)
                self.assertEqual(rc, 1, out)
                self.assertIn(code, out)
                self.assertEqual(self.directory_state(), state)
                self.assertEqual(self.registry(), [])
        with self.subTest(code='dry run refuses too'):
            rc, out = self.dry(cases['E_TARGET_NAME'])
            self.assertEqual(rc, 1, out)

    def test_a_duplicated_decision_is_refused(self):
        path = self.table()
        with open(path, encoding='utf-8-sig') as fh:
            lines = fh.read().splitlines()
        with open(path, 'w', encoding='utf-8-sig') as fh:
            fh.write('\n'.join(lines + [lines[1]]) + '\n')
        rc, out = self.apply(path)
        self.assertEqual(rc, 1, out)
        self.assertIn('E_DUP_KEY', out)

    # ---- negative controls: «Фукаро» and PTZ-to-territory -----------------------------

    def test_fukaro_cannot_be_given_a_card_by_the_table(self):
        for d in ({'DECISION': 'ALIAS', 'TARGET_CARD_ID': '12', 'TARGET_CARD_NAME': 'Фукаро'},
                  {'DECISION': 'KEEP', 'CURRENT_CARD_ID': '12'},
                  {'DECISION': 'NEW', 'NEW_CARD_TYPE': 'external'}):
            with self.subTest(decision=d['DECISION']):
                rc, out = self.apply(self.table({'Фукаро': d}))
                self.assertEqual(rc, 1, out)
                self.assertIn('E_DECISION_FOR_STATUS', out)

    def test_negative_control_without_the_importer_rule_fukaro_is_matched_to_12(self):
        """The refusal above comes from the importer's list, not from the table's wording."""
        with mock.patch.object(idw, 'GENERIC_PAYER_KEYS', frozenset()):
            rc, out = self.apply(self.table())
        self.assertEqual(rc, 1, out)
        self.assertIn('E_DECISION_FOR_STATUS', out)
        self.assertIn('UNIDENTIFIED is not allowed for a MATCHED spelling', out)

    def test_a_named_citizen_must_be_kept_not_made_unidentified(self):
        rc, out = self.apply(self.table({'Жамшид (фукаро)': {'DECISION': 'UNIDENTIFIED', 'CURRENT_CARD_ID': '13',
                                                              'EXPECTED_SOURCE_ROWS': None}}))
        self.assertEqual(rc, 1, out)
        self.assertIn('E_DECISION_FOR_STATUS', out)

    def test_ptz_onto_a_territorial_card_is_refused_with_the_forbid_list(self):
        forbid = ('--forbid-cards', '%d,%d' % (GENERIC, TERRITORY))
        keep = self.table({ZARANGARI_OLD: {'DECISION': 'KEEP', 'CURRENT_CARD_ID': str(TERRITORY)}})
        alias = self.table({ZARANGARI_NEW: {'DECISION': 'ALIAS', 'TARGET_CARD_ID': str(TERRITORY),
                                            'TARGET_CARD_NAME': ZARANGARI_OLD.replace('\n', ' ↵ ')}})
        for name, table in (('KEEP on the territory', keep), ('ALIAS to the territory', alias)):
            with self.subTest(name):
                rc, out = self.apply(table, *forbid)
                self.assertEqual(rc, 1, out)
                self.assertIn('E_TARGET_FORBIDDEN', out)
        self.assertEqual(self.registry(), [])
        rc, out = self.dry(self.table(), *forbid)
        self.assertEqual(rc, 0, out)

    def test_negative_control_without_the_forbid_list_the_territory_is_accepted(self):
        """The forbid list is what discriminates: the same KEEP passes without it."""
        rc, out = self.dry(self.table({ZARANGARI_OLD: {'DECISION': 'KEEP', 'CURRENT_CARD_ID': str(TERRITORY)}}))
        self.assertEqual(rc, 0, out)

    def test_a_group_cannot_take_the_name_of_an_existing_card(self):
        rc, out = self.apply(self.table({KORAKHONI: {'DECISION': 'GROUP_NEW', 'NEW_CARD_NAME': 'Маълум фх',
                                                     'NEW_CARD_TYPE': 'internal'},
                                         ZARANGARI_NEW: {'DECISION': 'GROUP_NEW', 'NEW_CARD_NAME': 'Маълум фх',
                                                         'NEW_CARD_TYPE': 'internal'}}))
        self.assertEqual(rc, 1, out)
        self.assertIn('E_NEW_NAME_TAKEN', out)

    # ---- SET_TYPE ----------------------------------------------------------------

    def test_set_type_moves_only_the_type_of_the_named_card(self):
        table = self.table(extra=[{'DECISION': 'SET_TYPE', 'TARGET_CARD_ID': '8', 'NEW_CARD_TYPE': 'internal',
                                   'TARGET_CARD_NAME': 'Тест ПТЗ ерлари'}])

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

    def test_set_type_on_an_alias_target_and_a_no_op_set_type_are_idempotent(self):
        """[REASON]: release of 29.10.2026. The owner wants №137 and №155 internal;
        №137 is also the ALIAS target of the «Заминлари» spellings, and its type on
        production is not known in advance -- it may already be internal. So one
        table must retype an ALIAS target in the same transaction AND accept a
        SET_TYPE that changes nothing, and a second run must be ALREADY APPLIED.
        Card 10 here plays №137 (ALIAS target, external -> internal); card 7 plays
        a card already of the asked type, with May works that must not move."""
        table = self.table(extra=[{'DECISION': 'SET_TYPE', 'TARGET_CARD_ID': '10', 'NEW_CARD_TYPE': 'internal',
                                   'TARGET_CARD_NAME': 'Янги фх'},
                                  {'DECISION': 'SET_TYPE', 'TARGET_CARD_ID': '7', 'NEW_CARD_TYPE': 'external',
                                   'TARGET_CARD_NAME': 'Маълум фх'}])
        cards_before, _aliases, works_before = self.directory_state()
        rc, out = self.apply(table)
        self.assertEqual(rc, 0, out)
        cards, _aliases, works = self.directory_state()
        self.assertEqual(works, works_before, 'SET_TYPE moves no work, May included')
        before, after = {c[0]: c for c in cards_before}, {c[0]: c for c in cards}
        self.assertEqual(after[7], before[7], 'a SET_TYPE to the type the card has changes nothing')
        self.assertIn('internal', after[10])
        self.assertEqual([v for v in after[10] if v != 'internal'], [v for v in before[10] if v != 'external'])
        self.assertEqual(self.aliases()[self.key('Янги фх')], 10, 'the ALIAS still lands on the retyped card')
        with open(self.receipt + '.diff.csv', encoding='utf-8-sig') as fh:
            retyped = {r['card_id']: (r['type_before'], r['type_after'])
                       for r in csv.DictReader(fh, delimiter=';') if r['action'] == 'SET_TYPE'}
        self.assertEqual(retyped, {'7': ('external', 'external'), '10': ('external', 'internal')})
        state = self.directory_state()
        os.remove(self.receipt)
        rc, out = self.apply(table)
        self.assertEqual(rc, 0, out)
        self.assertIn('RESULT: ALREADY APPLIED', out)
        self.assertEqual(self.directory_state(), state)

    def test_set_type_with_a_wrong_name_is_refused(self):
        rc, out = self.apply(self.table(extra=[{'DECISION': 'SET_TYPE', 'TARGET_CARD_ID': '8',
                                                'NEW_CARD_TYPE': 'internal', 'TARGET_CARD_NAME': 'Бошқа ном'}]))
        self.assertEqual(rc, 1, out)
        self.assertIn('E_TARGET_NAME', out)

    # ---- postconditions and atomicity ---------------------------------------------

    def test_a_write_beyond_the_plan_is_caught_and_rolled_back(self):
        state = self.directory_state()
        real = tool.apply_plan

        def broken(conn, plan, tag):
            out = real(conn, plan, tag)
            conn.execute("UPDATE drone_works SET drone_customer_id = 10 WHERE period_month = '2026-05' AND source_row = 21")
            return out
        with mock.patch.object(tool, 'apply_plan', broken):
            rc, out = self.apply(self.table())
        self.assertEqual(rc, 1, out)
        self.assertIn('POSTCONDITION FAILED', out)
        self.assertEqual(self.directory_state(), state)
        self.assertEqual(self.registry(), [])

    def test_a_failing_registry_row_leaves_nothing(self):
        state = self.directory_state()

        def broken(*args):
            raise sqlite3.OperationalError('disk I/O error')
        with mock.patch.object(tool, 'record_in_transaction', broken):
            rc, out = self.apply(self.table())
        self.assertEqual(rc, 1, out)
        self.assertEqual(self.directory_state(), state)
        self.assertFalse(os.path.exists(self.receipt + '.pending'))

    def test_a_repoint_that_finds_the_alias_moved_rolls_everything_back(self):
        state = self.directory_state()
        real = tool.apply_plan

        def moved_under_us(conn, plan, tag):
            conn.execute('UPDATE drone_customer_aliases SET drone_customer_id = 7 WHERE normalized_key = ?',
                         (self.key(ZARANGARI_OLD),))
            return real(conn, plan, tag)
        with mock.patch.object(tool, 'apply_plan', moved_under_us):
            rc, out = self.apply(self.table())
        self.assertEqual(rc, 1, out)
        self.assertIn('expected to repoint 1 row', out)
        self.assertEqual(self.directory_state(), state)

    def test_a_crash_after_commit_is_completed_by_a_rerun(self):
        def killed(*args):
            raise SystemExit('killed')
        with mock.patch.object(tool, 'finalize_receipt', killed):
            with self.assertRaises(SystemExit):
                self.apply(self.table())
        self.assertTrue(os.path.exists(self.receipt + '.pending'))
        rc, out = self.apply(self.table())
        self.assertEqual(rc, 0, out)
        self.assertIn('receipt completed now', out)
        with open(self.receipt, encoding='utf-8') as fh:
            rec = json.load(fh)
        self.assertEqual(rec['status'], 'APPLIED')
        self.assertEqual(rec['unidentified'], ['фукаро'])

    def test_a_crash_before_commit_leaves_nothing_and_a_rerun_applies(self):
        state = self.directory_state()

        def killed(conn):
            raise SystemExit('killed')
        with mock.patch.object(tool, 'commit', killed):
            with self.assertRaises(SystemExit):
                self.apply(self.table())
        self.assertEqual(self.directory_state(), state)
        rc, out = self.apply(self.table())
        self.assertEqual(rc, 0, out)
        self.assertIn('stopped before COMMIT', out)

    def test_missing_input_existing_out_and_a_bad_forbid_list(self):
        self.assertEqual(self.run_tool(os.path.join(self.tmp.name, 'nope.csv'), '--out', 'x')[0], 2)
        os.makedirs(os.path.join(self.tmp.name, 'taken'))
        self.assertEqual(self.run_tool(self.table(), '--out', os.path.join(self.tmp.name, 'taken'))[0], 4)
        self.assertEqual(self.dry(self.table(), '--forbid-cards', 'six')[0], 1)
        self.assertEqual(tool.parse_forbid('589, 684-686,803'), {589, 684, 685, 686, 803})


if __name__ == '__main__':
    unittest.main()
