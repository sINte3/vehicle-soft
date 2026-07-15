# -*- coding: utf-8 -*-
"""RE-SP-011: migration prerequisite/postcondition hardening tests.

Runs the two hardened migration scripts against throwaway SQLite files in a
temp directory (DB_PATH monkeypatched per test). Never touches
instance/transport.db or any real data.
"""
import os
import sqlite3
import tempfile
import unittest

import migrate_spare_parts_acts_permission as acts_mig
import migrate_spare_parts_sku_uniqueness as sku_mig


def _make_db(path, tables):
    con = sqlite3.connect(path)
    cur = con.cursor()
    ddl = {
        'app_modules': """
            CREATE TABLE app_modules (
                id INTEGER PRIMARY KEY, code TEXT UNIQUE NOT NULL,
                name_uz TEXT, name_ru TEXT, is_active INTEGER DEFAULT 1)""",
        'user_module_permissions': """
            CREATE TABLE user_module_permissions (
                id INTEGER PRIMARY KEY, user_id INTEGER NOT NULL,
                module_code TEXT NOT NULL, has_access INTEGER DEFAULT 0,
                UNIQUE(user_id, module_code))""",
        'spare_part_write_off_acts': """
            CREATE TABLE spare_part_write_off_acts (
                id INTEGER PRIMARY KEY, request_id INTEGER NOT NULL)""",
        'spare_part_skus': """
            CREATE TABLE spare_part_skus (
                id INTEGER PRIMARY KEY, spare_part_id INTEGER NOT NULL,
                brand TEXT DEFAULT '', article_number TEXT DEFAULT '',
                supplier TEXT DEFAULT '', is_active INTEGER DEFAULT 1)""",
    }
    for t in tables:
        cur.execute(ddl[t])
    if 'spare_part_write_off_acts' in tables:
        cur.execute("CREATE INDEX idx_spare_part_write_off_acts_request_id "
                    "ON spare_part_write_off_acts(request_id)")
    con.commit()
    con.close()


def _query(path, sql, args=()):
    con = sqlite3.connect(path)
    try:
        return con.execute(sql, args).fetchall()
    finally:
        con.close()


def _registry_rows(path, migration_id):
    return _query(path,
                  "SELECT COUNT(*) FROM schema_migrations WHERE name=?",
                  (migration_id,))[0][0]


class MigrationHardeningBase(unittest.TestCase):
    module = None  # set in subclasses

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix='resp011_')
        self.db = os.path.join(self.tmp, 'throwaway.db')
        self._orig_db_path = self.module.DB_PATH
        self.module.DB_PATH = self.db

    def tearDown(self):
        self.module.DB_PATH = self._orig_db_path

    def run_migration(self):
        """Run the migration; returns True on success, False if it exited."""
        try:
            self.module.run()
            return True
        except SystemExit:
            return False


class ActsPermissionMigrationTests(MigrationHardeningBase):
    module = acts_mig
    MID = acts_mig.MIGRATION_ID

    def test_missing_prerequisite_fails_without_recording(self):
        _make_db(self.db, ['app_modules'])  # two prerequisite tables missing
        self.assertFalse(self.run_migration())
        self.assertEqual(_registry_rows(self.db, self.MID), 0)

    def test_partial_state_empty_tables_applies_cleanly(self):
        _make_db(self.db, ['app_modules', 'user_module_permissions',
                           'spare_part_write_off_acts'])
        self.assertTrue(self.run_migration())
        self.assertEqual(_registry_rows(self.db, self.MID), 1)
        self.assertEqual(_query(self.db, "SELECT COUNT(*) FROM app_modules "
                                "WHERE code='spare_parts_acts'")[0][0], 1)

    def test_successful_apply_grants_and_records_once(self):
        _make_db(self.db, ['app_modules', 'user_module_permissions',
                           'spare_part_write_off_acts'])
        con = sqlite3.connect(self.db)
        con.execute("INSERT INTO user_module_permissions "
                    "(user_id, module_code, has_access) VALUES (7, 'spare_parts_issue', 1)")
        con.execute("INSERT INTO spare_part_write_off_acts (request_id) VALUES (1)")
        con.commit()
        con.close()
        self.assertTrue(self.run_migration())
        self.assertEqual(_registry_rows(self.db, self.MID), 1)
        # auto-grant postcondition really holds
        self.assertEqual(_query(self.db, "SELECT has_access FROM "
                                "user_module_permissions WHERE user_id=7 AND "
                                "module_code='spare_parts_acts'")[0][0], 1)
        # index is UNIQUE
        idx = {r[1]: r[2] for r in _query(
            self.db, "PRAGMA index_list(spare_part_write_off_acts)")}
        self.assertEqual(idx.get('idx_spare_part_write_off_acts_request_id'), 1)

    def test_rerun_is_clean_noop(self):
        _make_db(self.db, ['app_modules', 'user_module_permissions',
                           'spare_part_write_off_acts'])
        self.assertTrue(self.run_migration())
        self.assertTrue(self.run_migration())
        self.assertEqual(_registry_rows(self.db, self.MID), 1)
        self.assertEqual(_query(self.db, "SELECT COUNT(*) FROM app_modules "
                                "WHERE code='spare_parts_acts'")[0][0], 1)


class SkuUniquenessMigrationTests(MigrationHardeningBase):
    module = sku_mig
    MID = sku_mig.MIGRATION_ID

    def test_missing_prerequisite_fails_without_recording(self):
        _make_db(self.db, [])  # spare_part_skus absent
        self.assertFalse(self.run_migration())
        self.assertEqual(_registry_rows(self.db, self.MID), 0)

    def test_duplicate_data_fails_without_recording(self):
        _make_db(self.db, ['spare_part_skus'])
        con = sqlite3.connect(self.db)
        for _ in range(2):
            con.execute("INSERT INTO spare_part_skus "
                        "(spare_part_id, brand, article_number, supplier, is_active) "
                        "VALUES (1, 'Bosch', 'A1', 'S', 1)")
        con.commit()
        con.close()
        self.assertFalse(self.run_migration())
        self.assertEqual(_registry_rows(self.db, self.MID), 0)
        idx = {r[1] for r in _query(self.db, "PRAGMA index_list(spare_part_skus)")}
        self.assertNotIn('uq_spare_part_skus_normalized', idx)

    def test_successful_apply_creates_unique_index_and_records_once(self):
        _make_db(self.db, ['spare_part_skus'])
        self.assertTrue(self.run_migration())
        self.assertEqual(_registry_rows(self.db, self.MID), 1)
        idx = {r[1]: r[2] for r in _query(self.db, "PRAGMA index_list(spare_part_skus)")}
        self.assertEqual(idx.get('uq_spare_part_skus_normalized'), 1)

    def test_rerun_is_clean_noop(self):
        _make_db(self.db, ['spare_part_skus'])
        self.assertTrue(self.run_migration())
        self.assertTrue(self.run_migration())
        self.assertEqual(_registry_rows(self.db, self.MID), 1)


if __name__ == '__main__':
    unittest.main()
