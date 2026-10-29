# -*- coding: utf-8 -*-
"""tools/fin_recon_002_release_check.py: the window changed what was decided, and nothing else.

A synthetic backup and the live database the window is supposed to leave,
with the real expectations patched down to the size of the fixture. The
correct window passes; each kind of damage fails on its own line, so the
check is shown able to tell the two apart (CLAUDE.md: a check that gives the
same answer on right and wrong code is not a check).

Run: python -m unittest tests.test_fin_recon_002_release_check -v
"""
import contextlib
import io
import os
import shutil
import sqlite3
import sys
import tempfile
import unittest
from unittest import mock

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
for _p in (ROOT, os.path.join(ROOT, 'tools')):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import fin_recon_002_release_check as rc  # noqa: E402
import fin_recon_002_staging_check as staging  # noqa: E402
import migrate_drones_works_001 as m1  # noqa: E402

CANON_A, CANON_B, TERRITORY, OLD = 20, 21, 30, 19
LINKS = {101: CANON_A, 102: CANON_A, 103: CANON_A}     # 101-102 empty links, 103 moved from OLD
REGISTRY = {'PAYER_X', 'LINK_JUNE_X', 'LINK_ANCHOR_X', 'DECISIONS_X'}
BATCH = 'FIN_RECON_002_JUNE_2026'


def work(con, wid, period, card, raw, area, amount, batch=None, day=None):
    con.execute('INSERT INTO drone_works (id, period_month, work_date_from, drone_customer_id, customer_raw, area_ha, '
                "amount, payment_type, import_batch) VALUES (?, ?, ?, ?, ?, ?, ?, 'cash', ?)",
                (wid, period, day or period + '-10', card, raw, area, amount, batch))


def make_backup(path):
    con = sqlite3.connect(path)
    for ddl in (m1.CREATE_DRONE_CUSTOMERS, m1.CREATE_DRONE_CUSTOMER_ALIASES, m1.CREATE_DRONE_WORKS):
        con.execute(ddl)
    con.execute('ALTER TABLE drone_works ADD COLUMN received_kind VARCHAR(20)')
    con.execute('CREATE TABLE schema_migrations (name TEXT PRIMARY KEY, applied_at TEXT, checksum TEXT)')
    con.execute("INSERT INTO schema_migrations VALUES ('OLD_ONE', '2026-08-01', 'x')")
    con.executemany('INSERT INTO drone_customers (id, name, customer_type, is_active) VALUES (?, ?, ?, 1)',
                    [(7, 'Маълум фх', 'external'), (OLD, 'Канон А эски', 'external'), (CANON_A, 'Канон А', 'external'),
                     (CANON_B, 'Канон Б', 'external'), (TERRITORY, 'Ҳудуд', 'external')])
    con.executemany('INSERT INTO drone_customer_aliases (id, raw_name, normalized_key, drone_customer_id) '
                    'VALUES (?, ?, ?, ?)', [(1, 'Маълум фх', 'маълум фх', 7), (2, 'Ҳудуд', 'ҳудуд', TERRITORY)])
    work(con, 1, '2026-05', 7, 'Маълум фх', 10.0, 2000000)
    work(con, 2, '2026-05', TERRITORY, 'Ҳудуд', 8.0, 685064)
    work(con, 101, '2026-06', None, '', 5.0, 400000)
    work(con, 102, '2026-06', None, '', 6.0, 500000)
    work(con, 103, '2026-06', OLD, 'Канон А эски', 7.0, 600000)
    work(con, 104, '2026-06', 7, 'Маълум фх', 3.0, 600000)
    con.commit()
    con.close()


def apply_window(path, skip=()):
    """What the window does to the live database; skip names a step to leave out or break."""
    con = sqlite3.connect(path)
    if 'payer' not in skip:
        con.execute('ALTER TABLE drone_works ADD COLUMN payer_unidentified BOOLEAN')
    for wid, card in LINKS.items():
        con.execute('UPDATE drone_works SET drone_customer_id = ? WHERE id = ?', (card, wid))
    for cid in (CANON_A, CANON_B):
        if not (cid == CANON_B and 'set_type' in skip):
            con.execute("UPDATE drone_customers SET customer_type = 'internal' WHERE id = ?", (cid,))
    con.execute("INSERT INTO drone_customers (id, name, customer_type, is_active) VALUES (40, ?, 'internal', 1)",
                (staging.PTZ_CARD_NAME,))
    con.execute("INSERT INTO drone_customers (id, name, customer_type, is_active) VALUES (41, 'Янги фх', 'external', 1)")
    con.executemany('INSERT INTO drone_customer_aliases (id, raw_name, normalized_key, drone_customer_id) '
                    'VALUES (?, ?, ?, ?)', [(3, 'Янги фх', 'янги фх', 41), (4, 'Канон', 'канон', CANON_A),
                                            (5, 'ПТЗ худуд', 'птз худуд', 40)])
    con.execute('UPDATE drone_customer_aliases SET drone_customer_id = 40 WHERE id = 2')
    work(con, 201, '2026-06', CANON_A, 'Канон', 4.0, 340000, BATCH)
    work(con, 202, '2026-06', 40, 'ПТЗ худуд', 2.0, 170000, BATCH)
    work(con, 203, '2026-06', 41 if 'forbidden' not in skip else TERRITORY, 'Янги фх', 1.5, 300000, BATCH)
    con.execute('INSERT INTO drone_works (id, period_month, work_date_from, drone_customer_id, customer_raw, area_ha, '
                "amount, payment_type, import_batch, payer_unidentified) VALUES (204, '2026-06', '2026-06-11', NULL, "
                "'Фукаро', 1.0, 200000, 'cash', ?, 1)", (BATCH,))
    for name in sorted(REGISTRY - ({'DECISIONS_X'} if 'registry' in skip else set())):
        con.execute("INSERT INTO schema_migrations VALUES (?, '2026-10-29', 'x')", (name,))
    if 'may' in skip:
        con.execute('UPDATE drone_works SET amount = amount + 1 WHERE id = 1')
    if 'extra' in skip:
        con.execute('UPDATE drone_works SET area_ha = 3.5 WHERE id = 104')
    con.commit()
    con.close()


class ReleaseCheckTest(unittest.TestCase):

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.base = os.path.join(self.tmp.name, 'backup.db')
        make_backup(self.base)
        self.patches = [
            mock.patch.object(rc, 'expected_links', lambda: dict(LINKS)),
            mock.patch.object(rc, 'expected_registry', lambda: set(REGISTRY)),
            mock.patch.object(rc, 'NEW_CARDS', {'external': 1, 'internal': 1}),
            mock.patch.object(rc, 'NEW_ALIASES', 3),
            mock.patch.object(rc, 'FORBID', str(TERRITORY)),
            mock.patch.object(staging, 'MAY', {'period': (2, 18.0), 'calendar': (2, 18.0)}),
            mock.patch.object(staging, 'EXPECT_IMPORT', (4, 8.5, 1010000.0)),
            mock.patch.object(staging, 'EXPECT_ROUTES', {str(CANON_A): (1, 4.0, 340000.0),
                                                         'PTZ': (1, 2.0, 170000.0),
                                                         'UNIDENTIFIED': (1, 1.0, 200000.0)}),
            mock.patch.object(staging, 'CANONICAL', {CANON_A: 'Канон А', CANON_B: 'Канон Б'}),
        ]
        for p in self.patches:
            p.start()

    def tearDown(self):
        for p in reversed(self.patches):
            p.stop()
        self.tmp.cleanup()

    def run_check(self, skip=(), apply=True):
        live = os.path.join(self.tmp.name, 'live_%s.db' % '_'.join(skip or ('ok',)))
        shutil.copy(self.base, live)
        if apply:
            apply_window(live, skip)
        with contextlib.redirect_stdout(io.StringIO()) as buf:
            code = rc.main(['--db', live, '--baseline', self.base])
        out = buf.getvalue()
        return code, out, [line for line in out.splitlines() if line.startswith('FAIL ')]

    def test_the_window_as_decided_passes(self):
        code, out, fails = self.run_check()
        self.assertEqual((code, fails), (0, []), out)
        self.assertIn('RESULT: RELEASE CHECK PASSED', out)

    def test_the_backup_itself_is_not_a_release(self):
        code, out, fails = self.run_check(apply=False)
        self.assertEqual(code, 1, out)
        self.assertEqual(len(fails), 1, out)
        self.assertIn('payer_unidentified', fails[0])
        self.assertNotIn('Traceback', out)

    def fails_on(self, skip, words):
        """The damage named by skip fails the check, and on the line that names it.

        Other lines may fail too -- a touched May row also shows in the works
        of the backup -- what must hold is that this one is among them."""
        code, out, fails = self.run_check(skip=(skip,))
        self.assertEqual(code, 1, out)
        self.assertTrue(any(words in f for f in fails), out)
        return fails

    def test_a_touched_may_row_fails(self):
        self.fails_on('may', 'May: every row byte-identical')

    def test_a_canonical_card_left_external_fails(self):
        self.assertEqual(len(self.fails_on('set_type', 'cards of the backup unchanged but the type')), 1)

    def test_a_change_beyond_the_link_rows_fails(self):
        self.fails_on('extra', 'works of the backup')

    def test_a_book_row_on_a_forbidden_card_fails(self):
        self.fails_on('forbidden', 'forbidden card')

    def test_a_missing_registry_row_fails(self):
        self.assertEqual(len(self.fails_on('registry', 'registry')), 1)

    def test_a_missing_database_is_exit_2(self):
        with contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(rc.main(['--db', os.path.join(self.tmp.name, 'nope.db'), '--baseline', self.base]), 2)

    def test_the_real_expectations_are_the_ones_of_the_release(self):
        """Unpatched: 32 link rows, 4 registry rows, the release table's name."""
        for p in self.patches:
            p.stop()
        try:
            links = rc.expected_links()
            self.assertEqual(len(links), 32)
            self.assertEqual(sorted(set(links.values())), [137, 138])
            self.assertEqual(sum(1 for c in links.values() if c == 137), 22)
            self.assertEqual(links[997], 137)
            self.assertIn('DRONE_ALIAS_DECISIONS_754DAB3571F4CAE5', rc.expected_registry())
            self.assertEqual(len(rc.expected_registry()), 4)
            self.assertEqual(rc.NEW_CARDS, {'external': 44, 'internal': 1})
        finally:
            for p in self.patches:
                p.start()


if __name__ == '__main__':
    unittest.main()
