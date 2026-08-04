# -*- coding: utf-8 -*-
"""DRONE-WORKS-001: the four synthetic paths of migrate_drones_works_001.py.

Clean database, repeat run, missing database, failed precondition -- the four
paths CLAUDE.md requires of every migration, plus read-back checks of the two
properties a later hand-edit could quietly reverse: the uniqueness rules and
the ABSENCE of a machine column on drone_works.

Everything runs against throwaway SQLite files in a temp directory. Both
DB_PATH globals are patched -- the migration module's own, and the one inside
migration_utils, which is what record_migration() and is_migration_applied()
read. Patching only the first would silently write the registry row into
instance/transport.db.

Run:
  python -m unittest tests.test_drone_works_migration -v
"""
import os
import shutil
import sqlite3
import sys
import tempfile
import unittest

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if REPO_ROOT not in sys.path:
    sys.path.insert(0, REPO_ROOT)

import migration_utils  # noqa: E402
import migrate_drones_works_001 as works_mig  # noqa: E402

# The two REFERENCES targets the migration insists on before it will run.
PRECONDITION_DDL = {
    'users': 'CREATE TABLE users (id INTEGER PRIMARY KEY, username TEXT)',
    'drone_operators': ('CREATE TABLE drone_operators '
                        '(id INTEGER PRIMARY KEY, full_name TEXT NOT NULL)'),
}


def _make_db(path, tables):
    con = sqlite3.connect(path)
    for table in tables:
        con.execute(PRECONDITION_DDL[table])
    con.commit()
    con.close()


def _query(path, sql, args=()):
    con = sqlite3.connect(path)
    try:
        return con.execute(sql, args).fetchall()
    finally:
        con.close()


def _tables(path):
    return {r[0] for r in _query(
        path, "SELECT name FROM sqlite_master WHERE type='table'")}


def _registry_rows(path, migration_id):
    names = {r[0] for r in _query(
        path, "SELECT name FROM sqlite_master WHERE type='table' "
              "AND name='schema_migrations'")}
    if not names:
        return 0
    return _query(path, 'SELECT COUNT(*) FROM schema_migrations WHERE name=?',
                  (migration_id,))[0][0]


class DroneWorksMigrationTests(unittest.TestCase):
    MID = works_mig.MIGRATION_ID

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix='drone_works_mig_')
        self.db = os.path.join(self.tmp, 'throwaway.db')
        self._orig_module_db = works_mig.DB_PATH
        self._orig_utils_db = migration_utils.DB_PATH
        works_mig.DB_PATH = self.db
        migration_utils.DB_PATH = self.db

    def tearDown(self):
        works_mig.DB_PATH = self._orig_module_db
        migration_utils.DB_PATH = self._orig_utils_db
        shutil.rmtree(self.tmp, ignore_errors=True)

    def run_migration(self):
        """Run it; return (ok, exit_code). ok is False when it called exit()."""
        try:
            works_mig.run()
            return True, 0
        except SystemExit as exc:
            return False, exc.code

    # ── Path 1: clean database ────────────────────────────────────────────
    def test_clean_database_creates_everything_and_records_once(self):
        _make_db(self.db, ['users', 'drone_operators'])
        ok, _ = self.run_migration()
        self.assertTrue(ok)
        self.assertEqual(_registry_rows(self.db, self.MID), 1)
        for table in works_mig.NEW_TABLES:
            self.assertIn(table, _tables(self.db))
        for index in works_mig.EXPECTED_INDEXES:
            self.assertEqual(
                len(_query(self.db, "SELECT name FROM sqlite_master "
                                    "WHERE type='index' AND name=?", (index,))),
                1, index)

    def test_clean_database_leaves_a_checksum_on_the_registry_row(self):
        _make_db(self.db, ['users', 'drone_operators'])
        self.assertTrue(self.run_migration()[0])
        row = _query(self.db, 'SELECT checksum, description FROM '
                              'schema_migrations WHERE name=?', (self.MID,))[0]
        self.assertEqual(len(row[0]), 64)  # sha256 hex
        self.assertIn('DRONE-WORKS-001', row[1])

    def test_uniqueness_rules_are_read_back_from_the_database(self):
        """Both are inline table constraints, so PRAGMA is the only witness."""
        _make_db(self.db, ['users', 'drone_operators'])
        self.assertTrue(self.run_migration()[0])
        con = sqlite3.connect(self.db)
        try:
            cur = con.cursor()
            alias = works_mig._unique_index_column_sets(
                cur, 'drone_customer_aliases')
            works = works_mig._unique_index_column_sets(cur, 'drone_works')
        finally:
            con.close()
        self.assertIn(('normalized_key',), alias)
        self.assertIn(('source_file', 'source_sheet', 'source_row'), works)

    def test_provenance_triple_really_rejects_a_second_identical_row(self):
        """The index is only worth having if the database enforces it.

        [REASON]: a negative control on the rule that makes re-running the
        import a no-op. Asserting the index EXISTS proves the DDL ran;
        asserting the second INSERT raises proves the rule bites.
        """
        _make_db(self.db, ['users', 'drone_operators'])
        self.assertTrue(self.run_migration()[0])
        con = sqlite3.connect(self.db)
        try:
            insert = ("INSERT INTO drone_works (period_month, customer_raw, "
                      "area_ha, payment_type, source_file, source_sheet, "
                      "source_row) VALUES ('2026-04', 'X', 1.0, 'cash', "
                      "'f.xlsx', 's', 7)")
            con.execute(insert)
            con.commit()
            with self.assertRaises(sqlite3.IntegrityError):
                con.execute(insert)
                con.commit()
        finally:
            con.close()

    def test_manual_rows_without_provenance_are_not_blocked(self):
        """NULLs are distinct inside a SQLite unique index -- by design here."""
        _make_db(self.db, ['users', 'drone_operators'])
        self.assertTrue(self.run_migration()[0])
        con = sqlite3.connect(self.db)
        try:
            insert = ("INSERT INTO drone_works (period_month, customer_raw, "
                      "area_ha, payment_type) VALUES "
                      "('2026-04', 'Manual', 1.0, 'cash')")
            con.execute(insert)
            con.execute(insert)
            con.commit()
            self.assertEqual(
                con.execute('SELECT COUNT(*) FROM drone_works').fetchone()[0],
                2)
        finally:
            con.close()

    def test_drone_works_carries_no_machine_column(self):
        """The structural rule of the task, read back from the database."""
        _make_db(self.db, ['users', 'drone_operators'])
        self.assertTrue(self.run_migration()[0])
        columns = {r[1] for r in _query(self.db,
                                        'PRAGMA table_info(drone_works)')}
        for forbidden in works_mig.FORBIDDEN_WORK_COLUMNS:
            self.assertNotIn(forbidden, columns)
        self.assertIn('drone_operator_id', columns)

    def test_customers_table_is_not_touched(self):
        """`customers` is a different directory and must stay untouched."""
        _make_db(self.db, ['users', 'drone_operators'])
        con = sqlite3.connect(self.db)
        con.execute('CREATE TABLE customers (id INTEGER PRIMARY KEY, '
                    'name TEXT)')
        con.execute("INSERT INTO customers (name) VALUES ('Existing')")
        con.commit()
        con.close()
        self.assertTrue(self.run_migration()[0])
        self.assertEqual(_query(self.db, 'SELECT name FROM customers'),
                         [('Existing',)])

    # ── Path 2: repeat run ────────────────────────────────────────────────
    def test_rerun_is_a_clean_noop(self):
        _make_db(self.db, ['users', 'drone_operators'])
        self.assertTrue(self.run_migration()[0])
        self.assertTrue(self.run_migration()[0])
        self.assertEqual(_registry_rows(self.db, self.MID), 1)

    def test_rerun_after_a_lost_registry_row_still_does_not_explode(self):
        """CREATE TABLE IF NOT EXISTS carries the idempotency, not the registry."""
        _make_db(self.db, ['users', 'drone_operators'])
        self.assertTrue(self.run_migration()[0])
        con = sqlite3.connect(self.db)
        con.execute('DELETE FROM schema_migrations WHERE name=?', (self.MID,))
        con.commit()
        con.close()
        self.assertTrue(self.run_migration()[0])
        self.assertEqual(_registry_rows(self.db, self.MID), 1)

    # ── Path 3: missing database ──────────────────────────────────────────
    def test_missing_database_exits_2_and_creates_no_file(self):
        missing = os.path.join(self.tmp, 'nope.db')
        works_mig.DB_PATH = missing
        migration_utils.DB_PATH = missing
        ok, code = self.run_migration()
        self.assertFalse(ok)
        self.assertEqual(code, 2)
        self.assertFalse(os.path.exists(missing))

    # ── Path 4: failed precondition ───────────────────────────────────────
    def test_missing_drone_operators_exits_1_rolls_back_and_records_nothing(self):
        _make_db(self.db, ['users'])  # drone_operators absent
        ok, code = self.run_migration()
        self.assertFalse(ok)
        self.assertEqual(code, 1)
        self.assertEqual(_registry_rows(self.db, self.MID), 0)
        created = _tables(self.db) & set(works_mig.NEW_TABLES)
        self.assertEqual(created, set(), 'rollback left tables behind')

    def test_missing_users_exits_1_rolls_back_and_records_nothing(self):
        _make_db(self.db, ['drone_operators'])  # users absent
        ok, code = self.run_migration()
        self.assertFalse(ok)
        self.assertEqual(code, 1)
        self.assertEqual(_registry_rows(self.db, self.MID), 0)
        self.assertEqual(_tables(self.db) & set(works_mig.NEW_TABLES), set())


if __name__ == '__main__':
    unittest.main(verbosity=2)
