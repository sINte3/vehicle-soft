# -*- coding: utf-8 -*-
"""DRONES_WORKS_JUN2026/MAY2026_MERGED_CUSTOMER_LINK_001: заказчик из
объединённой ячейки.

Правка ставит drone_customer_id 31 июньской строке (и, отдельной миграцией, 8
майским) -- карточку первой строки того же объединённого диапазона. Меняется
одно поле; всё остальное обязано остаться побайтно прежним.

Главный риск -- ЛИСТ-ТЁЗКА: «свод ичи (Сайфулло)» есть и в майской, и в
июньской книге Агрокластера, и майские строки тоже пустые и без заказчика.
Отрицательный контроль: июньская правка обязана оставить их как были.

Четыре пути устава и отрицательные контроли на каждую сеть. Проверка, дающая
одинаковый результат на верной и на испорченной базе, проверкой не является.

Run:
  python -m unittest tests.test_drone_works_merged_customer_link_001 -v
"""
import contextlib
import hashlib
import io
import json
import os
import sqlite3
import subprocess
import sys
import tempfile
import unittest

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SCRIPT = os.path.join(REPO_ROOT, 'migrate_drones_works_merged_customer_link_001.py')
sys.path.insert(0, REPO_ROOT)

import migrate_drones_works_merged_customer_link_001 as migration  # noqa: E402

SCHEMA = """
CREATE TABLE drone_customers (id INTEGER PRIMARY KEY, name TEXT NOT NULL,
  customer_type TEXT, is_active BOOLEAN DEFAULT 1);
CREATE TABLE drone_works (id INTEGER PRIMARY KEY AUTOINCREMENT,
  period_month TEXT NOT NULL, work_date_from TEXT, drone_operator_id INTEGER,
  drone_customer_id INTEGER, customer_raw TEXT NOT NULL, area_ha NUMERIC NOT NULL,
  price_per_ha NUMERIC, amount NUMERIC, other_costs NUMERIC, received_amount NUMERIC,
  payment_type TEXT NOT NULL, source_file TEXT, source_sheet TEXT, source_row INTEGER,
  import_batch TEXT, UNIQUE (source_file, source_sheet, source_row));
"""


def build_db(path, mutate=None):
    """Production as measured on 2026-10-22, for BOTH scopes, plus neighbours."""
    conn = sqlite3.connect(path)
    conn.executescript(SCHEMA)
    names = {}
    for scope in migration.SCOPES.values():
        for g in scope['groups']:
            names[g[2]] = g[3]
    for cid, name in names.items():
        conn.execute('INSERT INTO drone_customers VALUES (?, ?, ?, 1)', (cid, name, 'external'))
    conn.execute("INSERT INTO drone_customers VALUES (500, 'Чужое ФХ', 'external', 1)")
    for scope in migration.SCOPES.values():
        for source_file, sheet, cid, name, anchor, _cell, targets in scope['groups']:
            period = targets[0][2]
            conn.execute('INSERT INTO drone_works (id, period_month, work_date_from, drone_customer_id, '
                         'customer_raw, area_ha, price_per_ha, amount, payment_type, source_file, '
                         'source_sheet, source_row, import_batch) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)',
                         (anchor[0], period, period + '-05', cid, name, 20.0, 85632.59, 1712651.8,
                          'transfer', source_file, sheet, anchor[1], 'b'))
            for work_id, row_no, per, area, amount in targets:
                conn.execute('INSERT INTO drone_works (id, period_month, work_date_from, drone_customer_id, '
                             "customer_raw, area_ha, price_per_ha, amount, payment_type, source_file, "
                             'source_sheet, source_row, import_batch) VALUES (?,?,?,NULL,?,?,?,?,?,?,?,?,?)',
                             (work_id, per, per + '-10', '', area, 85632.59, amount, 'transfer',
                              source_file, sheet, row_no, 'b'))
            # a neighbour of the same sheet with its OWN farm -- must stay as it is
            conn.execute('INSERT INTO drone_works (period_month, drone_customer_id, customer_raw, area_ha, '
                         'amount, other_costs, received_amount, payment_type, source_file, source_sheet, '
                         'source_row) VALUES (?, 500, ?, 7.5, 1500000, 50000, 1000000, ?, ?, ?, ?)',
                         (period, 'Чужое ФХ', 'cash', source_file, sheet, 200))
    # an unrelated book of June with an empty customer -- must stay unlinked
    conn.execute("INSERT INTO drone_works (period_month, customer_raw, area_ha, amount, payment_type, "
                 "source_file, source_sheet, source_row) VALUES ('2026-06', '', 3.3, 660000, 'cash', "
                 "'Другая книга Июнь.xlsx', 'свод ичи (Сайфулло)', 13)")
    if mutate:
        mutate(conn)
    conn.commit()
    conn.close()


def data_hash(path):
    conn = sqlite3.connect(path)
    try:
        h = hashlib.sha256()
        for table in ('drone_customers', 'drone_works'):
            for row in conn.execute('SELECT * FROM %s ORDER BY id' % table):
                h.update(repr(row).encode('utf-8'))
        return h.hexdigest()
    finally:
        conn.close()


def registry(path):
    conn = sqlite3.connect(path)
    try:
        if not conn.execute("SELECT 1 FROM sqlite_master WHERE name = 'schema_migrations'").fetchone():
            return []
        return [r[0] for r in conn.execute('SELECT name FROM schema_migrations ORDER BY name')]
    finally:
        conn.close()


JUNE_ID = migration.SCOPES['june']['migration_id']
MAY_ID = migration.SCOPES['may']['migration_id']


class MergedCustomerLinkTest(unittest.TestCase):

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.db = os.path.join(self.tmp.name, 'transport.db')
        self.receipt = os.path.join(self.tmp.name, 'receipt.json')

    def tearDown(self):
        self.tmp.cleanup()

    def run_main(self, *args):
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            rc = migration.main(['--db', self.db] + list(args))
        return rc, buf.getvalue()

    def apply(self, scope='june'):
        return self.run_main('--scope', scope, '--apply', '--receipt', self.receipt)

    def links(self, ids):
        conn = sqlite3.connect(self.db)
        try:
            return {r[0]: r[1] for r in conn.execute(
                'SELECT id, drone_customer_id FROM drone_works WHERE id IN (%s)'
                % ','.join(str(i) for i in ids))}
        finally:
            conn.close()

    # ---- the four paths -----------------------------------------------------------

    def test_dry_run_writes_nothing(self):
        build_db(self.db)
        before = data_hash(self.db)
        rc, out = self.run_main()
        self.assertEqual(rc, 0)
        self.assertIn('DRY RUN', out)
        self.assertEqual(data_hash(self.db), before)
        self.assertEqual(registry(self.db), [])

    def test_apply_links_exactly_the_31_rows_to_the_card_of_their_range(self):
        build_db(self.db)
        rc, out = self.apply()
        self.assertEqual(rc, 0, out)
        for _f, _s, cid, _n, _a, _c, targets in migration.SCOPES['june']['groups']:
            got = self.links([t[0] for t in targets])
            self.assertEqual(set(got.values()), {cid})
        self.assertEqual(registry(self.db), [JUNE_ID])
        self.assertIn('31 rows / 900.30 ha / 77095080.10', out)

    def test_second_apply_is_a_no_op(self):
        build_db(self.db)
        self.apply()
        after_first = data_hash(self.db)
        os.remove(self.receipt)
        rc, out = self.apply()
        self.assertEqual(rc, 0)
        self.assertIn('already applied', out)
        self.assertEqual(data_hash(self.db), after_first)

    def test_missing_database_gives_code_two_and_creates_nothing(self):
        r = subprocess.run([sys.executable, SCRIPT, '--db', self.db], capture_output=True, text=True)
        self.assertEqual(r.returncode, 2)
        self.assertFalse(os.path.exists(self.db))

    def test_failed_precondition_gives_code_one_and_changes_nothing(self):
        build_db(self.db, lambda c: c.execute('UPDATE drone_works SET drone_customer_id = 500 WHERE id = 1005'))
        before = data_hash(self.db)
        rc, out = self.apply()
        self.assertEqual(rc, 1)
        self.assertIn('PRECONDITION FAILED', out)
        self.assertEqual(data_hash(self.db), before)
        self.assertEqual(registry(self.db), [])
        self.assertFalse(os.path.exists(self.receipt))

    # ---- what must NOT move ------------------------------------------------------

    def test_the_may_namesake_sheet_is_not_touched_by_the_june_fix(self):
        # Negative control: the May rows satisfy "NULL card and empty customer"
        # in a sheet of the same name -- only the file name and the id list
        # keep them out.
        build_db(self.db)
        may_ids = [t[0] for g in migration.SCOPES['may']['groups'] for t in g[6]]
        self.apply()
        self.assertEqual(set(self.links(may_ids).values()), {None})

    def test_unrelated_empty_customer_rows_and_neighbours_stay_as_they_were(self):
        build_db(self.db)
        self.apply()
        conn = sqlite3.connect(self.db)
        try:
            other = conn.execute("SELECT drone_customer_id FROM drone_works WHERE source_file = "
                                 "'Другая книга Июнь.xlsx'").fetchone()[0]
            neighbours = {r[0] for r in conn.execute('SELECT drone_customer_id FROM drone_works '
                                                     'WHERE source_row = 200')}
        finally:
            conn.close()
        self.assertIsNone(other)
        self.assertEqual(neighbours, {500})

    def test_money_area_and_every_other_field_do_not_move(self):
        build_db(self.db)
        conn = sqlite3.connect(self.db)
        cols = [r[1] for r in conn.execute('PRAGMA table_info(drone_works)') if r[1] != 'drone_customer_id']
        before = conn.execute('SELECT %s FROM drone_works ORDER BY id' % ','.join(cols)).fetchall()
        conn.close()
        self.apply()
        conn = sqlite3.connect(self.db)
        after = conn.execute('SELECT %s FROM drone_works ORDER BY id' % ','.join(cols)).fetchall()
        conn.close()
        self.assertEqual(before, after)

    def test_may_is_its_own_migration_and_needs_its_own_run(self):
        build_db(self.db)
        self.apply('june')
        self.assertEqual(registry(self.db), [JUNE_ID])
        os.remove(self.receipt)
        rc, out = self.apply('may')
        self.assertEqual(rc, 0, out)
        self.assertEqual(registry(self.db), [JUNE_ID, MAY_ID])
        self.assertIn('8 rows / 216.00 ha / 18496719.36', out)

    # ---- every precondition net, each with its own failing database -----------

    def assert_refused(self, mutate, needle):
        build_db(self.db, mutate)
        before = data_hash(self.db)
        rc, out = self.apply()
        self.assertEqual(rc, 1, out)
        self.assertIn(needle, out)
        self.assertEqual(data_hash(self.db), before)
        self.assertEqual(registry(self.db), [])

    def test_area_drift_on_one_row_is_refused(self):
        self.assert_refused(lambda c: c.execute('UPDATE drone_works SET area_ha = 38.9 WHERE id = 998'),
                            'id 998: area or amount differs')

    def test_amount_drift_on_one_row_is_refused(self):
        self.assert_refused(lambda c: c.execute('UPDATE drone_works SET amount = amount + 1 WHERE id = 1050'),
                            'id 1050: area or amount differs')

    def test_a_reimported_book_with_shifted_ids_is_refused(self):
        self.assert_refused(lambda c: c.execute('UPDATE drone_works SET source_row = 99 WHERE id = 1043'),
                            'id 1043: key or period differs')

    def test_an_extra_unlinked_row_in_the_same_sheet_is_refused(self):
        self.assert_refused(
            lambda c: c.execute("INSERT INTO drone_works (period_month, customer_raw, area_ha, amount, "
                                "payment_type, source_file, source_sheet, source_row) VALUES ('2026-06', '', "
                                "1.0, 85632.59, 'transfer', 'ИЮНЬ Агрокластер.xlsx', 'свод ичи (Сайфулло)', 34)"),
            'other unlinked row(s)')

    def test_an_unlinked_first_row_of_the_range_is_refused(self):
        self.assert_refused(lambda c: c.execute('UPDATE drone_works SET drone_customer_id = NULL WHERE id = 1042'),
                            'first row of the range')

    def test_a_renamed_customer_card_is_refused(self):
        self.assert_refused(lambda c: c.execute("UPDATE drone_customers SET name = 'Сервис' WHERE id = 138"),
                            'customer card 138')

    def test_a_row_with_a_customer_spelling_is_refused(self):
        self.assert_refused(lambda c: c.execute("UPDATE drone_works SET customer_raw = 'X' WHERE id = 1001"),
                            'id 1001: customer_raw is not empty')

    # ---- every postcondition net, proven by breaking the write ------------------

    def assert_post_refused(self, extra_sql):
        build_db(self.db)
        before = data_hash(self.db)
        original = migration.apply_changes

        def broken(conn, scope):
            rows = original(conn, scope)
            conn.execute(extra_sql)
            return rows
        migration.apply_changes = broken
        try:
            rc, out = self.apply()
        finally:
            migration.apply_changes = original
        self.assertEqual(rc, 1, out)
        self.assertIn('POSTCONDITION FAILED', out)
        self.assertEqual(data_hash(self.db), before)
        self.assertEqual(registry(self.db), [])
        self.assertFalse(os.path.exists(self.receipt))
        return out

    def test_postcondition_sees_another_field_of_a_target_row_moving(self):
        out = self.assert_post_refused('UPDATE drone_works SET payment_type = \'cash\' WHERE id = 1010')
        self.assertIn('a field other than drone_customer_id moved', out)

    def test_postcondition_sees_a_row_outside_the_list_changing(self):
        out = self.assert_post_refused("UPDATE drone_works SET drone_customer_id = 136 WHERE source_file = "
                                       "'Другая книга Июнь.xlsx'")
        self.assertIn('a row outside the target list changed', out)

    def test_postcondition_sees_a_target_row_relinked_elsewhere(self):
        out = self.assert_post_refused('UPDATE drone_works SET drone_customer_id = 500 WHERE id = 1052')
        self.assertIn('id 1052: not linked to card 138', out)

    def test_postcondition_is_silent_on_a_correct_apply(self):
        build_db(self.db)
        rc, out = self.apply()
        self.assertEqual(rc, 0)
        self.assertNotIn('FAILED', out)

    # ---- history and rollback -----------------------------------------------------

    def test_apply_without_a_receipt_is_refused(self):
        build_db(self.db)
        before = data_hash(self.db)
        rc, out = self.run_main('--apply')
        self.assertEqual(rc, 1)
        self.assertEqual(data_hash(self.db), before)
        open(self.receipt, 'w').close()
        rc, out = self.apply()
        self.assertEqual(rc, 1)
        self.assertEqual(data_hash(self.db), before)

    def test_an_unwritable_receipt_commits_nothing(self):
        build_db(self.db)
        before = data_hash(self.db)
        self.receipt = os.path.join(self.tmp.name, 'no_such_dir', 'receipt.json')
        rc, out = self.apply()
        self.assertEqual(rc, 1, out)
        self.assertIn('RESULT: NOT APPLIED', out)
        self.assertEqual(data_hash(self.db), before)
        self.assertEqual(registry(self.db), [])

    def test_receipt_holds_every_row_before_and_after_and_the_rollback(self):
        build_db(self.db)
        self.apply()
        with open(self.receipt, encoding='utf-8') as fh:
            rec = json.load(fh)
        self.assertEqual(rec['migration_id'], JUNE_ID)
        self.assertEqual(rec['status'], 'APPLIED')
        self.assertFalse(os.path.exists(self.receipt + '.pending'))
        self.assertEqual(len(rec['rows']), 31)
        self.assertTrue(all(r['drone_customer_id_before'] is None for r in rec['rows']))
        self.assertEqual({r['drone_customer_id_after'] for r in rec['rows']}, {136, 138})
        self.assertEqual(len(rec['rollback_sql']), 3)

    def test_printed_rollback_restores_the_database_exactly(self):
        build_db(self.db)
        before = data_hash(self.db)
        rc, out = self.apply()
        self.assertEqual(rc, 0)
        self.assertNotEqual(data_hash(self.db), before)
        sql = [line.strip() for line in out.splitlines() if line.strip().startswith(('UPDATE', 'DELETE'))]
        conn = sqlite3.connect(self.db)
        for statement in sql:
            conn.execute(statement)
        conn.commit()
        conn.close()
        self.assertEqual(data_hash(self.db), before)
        self.assertEqual(registry(self.db), [])

    # ---- atomicity, interruption and recovery (ChatGPT review of 23.10.2026) ----

    def state(self):
        """(links of the 31 June rows to their card, registry names)."""
        want = {t[0]: g[2] for g in migration.SCOPES['june']['groups'] for t in g[6]}
        got = self.links(list(want))
        return sum(1 for i, c in want.items() if got.get(i) == c), registry(self.db)

    def patched(self, **funcs):
        saved = {name: getattr(migration, name) for name in funcs}
        for name, func in funcs.items():
            setattr(migration, name, func)
        self.addCleanup(lambda: [setattr(migration, n, f) for n, f in saved.items()])

    def test_dry_run_creates_no_registry_table_and_no_file(self):
        build_db(self.db)
        with open(self.db, 'rb') as fh:
            file_before = hashlib.sha256(fh.read()).hexdigest()
        rc, out = self.run_main('--receipt', self.receipt)
        self.assertEqual(rc, 0, out)
        self.assertIn('RESULT: DRY RUN OK', out)
        with open(self.db, 'rb') as fh:
            self.assertEqual(hashlib.sha256(fh.read()).hexdigest(), file_before)
        conn = sqlite3.connect(self.db)
        try:
            self.assertIsNone(conn.execute("SELECT 1 FROM sqlite_master WHERE name = 'schema_migrations'").fetchone())
        finally:
            conn.close()
        self.assertEqual(sorted(os.listdir(self.tmp.name)), ['transport.db'])

    def test_a_failing_registry_helper_cannot_split_links_from_the_registry(self):
        # The defect ChatGPT reproduced: registry written by migration_utils
        # after COMMIT, failing -> 31 links, no registry row. Whatever the old
        # helper does now, links and registry row must land together.
        build_db(self.db)

        def broken(*args, **kwargs):
            raise RuntimeError('registry unavailable')
        saved = migration.migration_utils.record_migration
        migration.migration_utils.record_migration = broken
        self.addCleanup(setattr, migration.migration_utils, 'record_migration', saved)
        self.apply()
        self.assertIn(self.state(), ((31, [JUNE_ID]), (0, [])))

    def test_registry_insert_failing_inside_the_transaction_leaves_nothing(self):
        build_db(self.db)
        before = data_hash(self.db)

        def broken(*args, **kwargs):
            raise sqlite3.OperationalError('disk I/O error')
        self.patched(_record_in_transaction=broken)
        rc, out = self.apply()
        self.assertEqual(rc, 1, out)
        self.assertIn('RESULT: NOT APPLIED', out)
        self.assertEqual(data_hash(self.db), before)
        self.assertEqual(self.state(), (0, []))
        self.assertFalse(os.path.exists(self.receipt))
        self.assertFalse(os.path.exists(self.receipt + '.pending'))

    def test_commit_failing_before_it_happens_leaves_nothing_and_says_so(self):
        build_db(self.db)
        before = data_hash(self.db)

        def broken(conn):
            raise sqlite3.OperationalError('database is locked')
        self.patched(_commit=broken)
        rc, out = self.apply()
        self.assertEqual(rc, 1, out)
        self.assertIn('RESULT: NOT APPLIED', out)
        self.assertEqual(data_hash(self.db), before)
        self.assertEqual(self.state(), (0, []))
        self.assertFalse(os.path.exists(self.receipt))
        self.assertFalse(os.path.exists(self.receipt + '.pending'))
        self.assertTrue(any('.aborted-' in f for f in os.listdir(self.tmp.name)))

    def test_commit_raising_after_it_happened_is_reported_as_applied(self):
        build_db(self.db)
        real = migration._commit

        def committed_then_raised(conn):
            real(conn)
            raise sqlite3.OperationalError('error reported after commit')
        self.patched(_commit=committed_then_raised)
        rc, out = self.apply()
        self.assertEqual(rc, 0, out)
        self.assertIn('RESULT: APPLIED', out)
        self.assertEqual(self.state(), (31, [JUNE_ID]))
        with open(self.receipt, encoding='utf-8') as fh:
            self.assertEqual(json.load(fh)['status'], 'APPLIED')

    def test_crash_after_commit_before_the_receipt_is_recovered_by_a_rerun(self):
        build_db(self.db)
        real = migration._finalize_receipt

        def killed(*args):
            raise SystemExit('process killed')
        migration._finalize_receipt = killed
        try:
            with self.assertRaises(SystemExit):
                self.apply()
        finally:
            migration._finalize_receipt = real
        self.assertEqual(self.state(), (31, [JUNE_ID]))
        self.assertTrue(os.path.exists(self.receipt + '.pending'))
        self.assertFalse(os.path.exists(self.receipt))
        rc, out = self.apply()
        self.assertEqual(rc, 0, out)
        self.assertIn('RESULT: APPLIED', out)
        self.assertFalse(os.path.exists(self.receipt + '.pending'))
        with open(self.receipt, encoding='utf-8') as fh:
            rec = json.load(fh)
        self.assertEqual(rec['status'], 'APPLIED')
        self.assertIn('recovered_at_utc', rec)
        self.assertEqual(self.state(), (31, [JUNE_ID]))

    def test_crash_before_commit_leaves_nothing_and_a_rerun_applies_cleanly(self):
        build_db(self.db)
        before = data_hash(self.db)
        real = migration._commit

        def killed(conn):
            raise SystemExit('process killed')
        migration._commit = killed
        try:
            with self.assertRaises(SystemExit):
                self.apply()
        finally:
            migration._commit = real
        self.assertEqual(data_hash(self.db), before)
        self.assertEqual(self.state(), (0, []))
        self.assertTrue(os.path.exists(self.receipt + '.pending'))
        rc, out = self.apply()
        self.assertEqual(rc, 0, out)
        self.assertIn('stopped before COMMIT', out)
        self.assertEqual(self.state(), (31, [JUNE_ID]))
        self.assertTrue(any('.aborted-' in f for f in os.listdir(self.tmp.name)))
        with open(self.receipt, encoding='utf-8') as fh:
            self.assertEqual(json.load(fh)['status'], 'APPLIED')

    def test_registry_row_without_links_is_needs_recovery(self):
        build_db(self.db)
        migration.migration_utils.DB_PATH = self.db
        migration.migration_utils.record_migration(JUNE_ID, description='hand-made', checksum='x')
        before = data_hash(self.db)
        for args in ((), ('--apply', '--receipt', self.receipt)):
            rc, out = self.run_main(*args)
            self.assertEqual(rc, 3, out)
            self.assertIn('RESULT: NEEDS_RECOVERY', out)
        self.assertEqual(data_hash(self.db), before)

    def test_links_without_registry_row_is_needs_recovery(self):
        build_db(self.db, lambda c: c.execute('UPDATE drone_works SET drone_customer_id = 136 WHERE id = 998'))
        before = data_hash(self.db)
        rc, out = self.apply()
        self.assertEqual(rc, 3, out)
        self.assertIn('RESULT: NEEDS_RECOVERY', out)
        self.assertEqual(data_hash(self.db), before)
        self.assertEqual(registry(self.db), [])

    def test_a_receipt_of_a_change_the_database_does_not_hold_is_needs_recovery(self):
        build_db(self.db)
        with open(self.receipt, 'w', encoding='utf-8') as fh:
            json.dump({'migration_id': JUNE_ID, 'status': 'APPLIED'}, fh)
        before = data_hash(self.db)
        rc, out = self.apply()
        self.assertEqual(rc, 3, out)
        self.assertEqual(data_hash(self.db), before)

    def test_preconditions_are_checked_again_under_the_write_lock(self):
        build_db(self.db)
        seen = []
        real = migration.check_precondition

        def spy(conn, scope):
            seen.append(conn.in_transaction)
            return real(conn, scope)
        self.patched(check_precondition=spy)
        rc, out = self.apply()
        self.assertEqual(rc, 0, out)
        self.assertEqual(seen, [True])

    def test_the_console_never_leaks_cyrillic(self):
        build_db(self.db, lambda c: c.execute('UPDATE drone_works SET area_ha = 1 WHERE id = 998'))
        r = subprocess.run([sys.executable, SCRIPT, '--db', self.db], capture_output=True)
        self.assertEqual(r.returncode, 1)
        r.stdout.decode('ascii')
        build_db_ok = os.path.join(self.tmp.name, 'ok.db')
        build_db(build_db_ok)
        r = subprocess.run([sys.executable, SCRIPT, '--db', build_db_ok], capture_output=True)
        self.assertEqual(r.returncode, 0)
        r.stdout.decode('ascii')


if __name__ == '__main__':
    unittest.main()
