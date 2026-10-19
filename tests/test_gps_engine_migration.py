# -*- coding: utf-8 -*-
"""Migration GPS_ENGINE_001 -- the four paths of the charter, and one more.

Clean database, repeat («Already applied»), missing database (code 2, no file
created), failed precondition (code 1, full rollback, empty registry) -- and a
table of the same name with other columns (code 1, left as it was). Stdlib
only, no Flask.

Run:
  python -m unittest tests.test_gps_engine_migration
"""

import io
import os
import sqlite3
import sys
import tempfile
import unittest
from contextlib import redirect_stdout

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if REPO_ROOT not in sys.path:
    sys.path.insert(0, REPO_ROOT)

import migration_utils                                             # noqa: E402
import migrate_gps_engine_001 as engine_mig                        # noqa: E402


def query(path, sql, args=()):
    con = sqlite3.connect(path)
    try:
        return con.execute(sql, args).fetchall()
    finally:
        con.close()


def tables(path):
    return {r[0] for r in query(
        path, "SELECT name FROM sqlite_master WHERE type='table'")}


class MigrationPaths(unittest.TestCase):
    def setUp(self):
        self.folder = tempfile.mkdtemp()
        self.db = os.path.join(self.folder, 'transport.db')
        self._saved = (engine_mig.DB_PATH, migration_utils.DB_PATH)
        engine_mig.DB_PATH = self.db
        migration_utils.DB_PATH = self.db

    def tearDown(self):
        engine_mig.DB_PATH, migration_utils.DB_PATH = self._saved

    def make_db(self, with_gps=True, extra=None):
        con = sqlite3.connect(self.db)
        if with_gps:
            con.execute('CREATE TABLE gps_daily_aggregates (id INTEGER PRIMARY '
                        'KEY, work_date DATE, wialon_id INTEGER)')
        else:
            con.execute('CREATE TABLE users (id INTEGER PRIMARY KEY)')
        if extra:
            con.execute(extra)
        con.commit()
        con.close()

    def run_migration(self):
        out = io.StringIO()
        with redirect_stdout(out):
            engine_mig.run()
        return out.getvalue()

    def registered(self):
        return query(self.db, 'SELECT COUNT(*) FROM schema_migrations WHERE '
                              'name = ?', (engine_mig.MIGRATION_ID,))[0][0]

    def test_clean_database_creates_the_table_with_its_key(self):
        self.make_db()
        out = self.run_migration()
        self.assertIn('Done.', out)
        self.assertTrue(out.isascii())
        columns = [r[1] for r in query(self.db,
                                       'PRAGMA table_info(gps_daily_engine)')]
        self.assertEqual(columns, list(engine_mig.EXPECTED_COLUMNS))
        self.assertEqual(self.registered(), 1)
        con = sqlite3.connect(self.db)
        try:
            con.execute("INSERT INTO gps_daily_engine (work_date, wialon_id, "
                        "method_version, computed_at) VALUES ('2026-10-01', 1, "
                        "'v', 't')")
            with self.assertRaises(sqlite3.IntegrityError):
                con.execute("INSERT INTO gps_daily_engine (work_date, "
                            "wialon_id, method_version, computed_at) VALUES "
                            "('2026-10-01', 1, 'v', 't')")
        finally:
            con.close()

    def test_a_repeat_says_already_applied_and_changes_nothing(self):
        self.make_db()
        self.run_migration()
        self.assertIn('Already applied. Nothing to do.', self.run_migration())
        self.assertEqual(self.registered(), 1)

    def test_a_missing_database_is_code_2_and_no_file_is_created(self):
        with self.assertRaises(SystemExit) as caught:
            self.run_migration()
        self.assertEqual(caught.exception.code, 2)
        self.assertFalse(os.path.exists(self.db))

    def test_without_the_gps_calculation_it_is_refused_and_rolled_back(self):
        self.make_db(with_gps=False)
        with self.assertRaises(SystemExit) as caught:
            self.run_migration()
        self.assertEqual(caught.exception.code, 1)
        self.assertNotIn('gps_daily_engine', tables(self.db))
        self.assertEqual(self.registered(), 0)

    def test_a_table_of_that_name_with_other_columns_is_left_as_it_was(self):
        self.make_db(extra='CREATE TABLE gps_daily_engine (id INTEGER PRIMARY '
                           'KEY, hours REAL)')
        with self.assertRaises(SystemExit) as caught:
            self.run_migration()
        self.assertEqual(caught.exception.code, 1)
        columns = [r[1] for r in query(self.db,
                                       'PRAGMA table_info(gps_daily_engine)')]
        self.assertEqual(columns, ['id', 'hours'])
        self.assertNotIn('ix_gps_daily_engine_unit_day',
                         {r[1] for r in query(
                             self.db, 'PRAGMA index_list(gps_daily_engine)')})
        self.assertEqual(self.registered(), 0)

    def test_a_table_without_the_day_unit_key_is_refused(self):
        self.make_db(extra=(
            'CREATE TABLE gps_daily_engine (id INTEGER PRIMARY KEY, work_date '
            'DATE, wialon_id INTEGER, on_seconds INTEGER, off_seconds INTEGER, '
            'unknown_seconds INTEGER, points_total INTEGER, points_with_ign '
            'INTEGER, method_version TEXT, computed_at TEXT)'))
        with self.assertRaises(SystemExit) as caught:
            self.run_migration()
        self.assertEqual(caught.exception.code, 1)
        self.assertEqual(self.registered(), 0)
        # [REASON]: индекс успевает создаться до проверки ключа; откат обязан
        # убрать и его -- одна транзакция, а не «что успело, то осталось».
        self.assertNotIn('ix_gps_daily_engine_unit_day',
                         {r[1] for r in query(
                             self.db, 'PRAGMA index_list(gps_daily_engine)')})


if __name__ == '__main__':
    unittest.main()
