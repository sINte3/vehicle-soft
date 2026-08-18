# -*- coding: utf-8 -*-
"""GPS_DAILY_001: the four synthetic paths CLAUDE.md requires of a migration.

Clean database, repeat run, missing database, failed precondition -- plus
read-back checks of the two properties a later hand-edit could quietly
reverse: the uniqueness rules that make a recomputation replace instead of
accumulate, and the fact that contour_id is NULLABLE (work on unregistered
ground is the normal case, not an error).

Everything runs against throwaway SQLite files in a temp directory. Both
DB_PATH globals are patched -- the migration module's own, and the one inside
migration_utils, which is what record_migration() and is_migration_applied()
read. Patching only the first would silently write the registry row into
instance/transport.db.

Run:
  python -m unittest tests.test_gps_daily_migration -v
"""
import os
import sqlite3
import sys
import tempfile
import unittest

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if REPO_ROOT not in sys.path:
    sys.path.insert(0, REPO_ROOT)

import migration_utils  # noqa: E402
import migrate_gps_daily_001 as gps_mig  # noqa: E402

FIELD_CONTOURS_DDL = (
    'CREATE TABLE field_contours (id INTEGER PRIMARY KEY, source TEXT, '
    'external_id TEXT, name TEXT)')


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
    present = _query(path, "SELECT name FROM sqlite_master WHERE type='table' "
                           "AND name='schema_migrations'")
    if not present:
        return 0
    return _query(path, 'SELECT COUNT(*) FROM schema_migrations WHERE name=?',
                  (migration_id,))[0][0]


class GpsDailyMigrationPaths(unittest.TestCase):
    def setUp(self):
        self.folder = tempfile.mkdtemp()
        self.db = os.path.join(self.folder, 'transport.db')
        self._saved = (gps_mig.DB_PATH, migration_utils.DB_PATH)
        gps_mig.DB_PATH = self.db
        migration_utils.DB_PATH = self.db

    def tearDown(self):
        gps_mig.DB_PATH, migration_utils.DB_PATH = self._saved

    def _make_db(self, with_contours=True):
        con = sqlite3.connect(self.db)
        if with_contours:
            con.execute(FIELD_CONTOURS_DDL)
        else:
            con.execute('CREATE TABLE users (id INTEGER PRIMARY KEY)')
        con.commit()
        con.close()

    # --- path 1: clean database -------------------------------------------

    def test_clean_database_creates_both_tables_and_records_itself(self):
        self._make_db()
        gps_mig.run()
        tables = _tables(self.db)
        self.assertIn('gps_daily_aggregates', tables)
        self.assertIn('gps_work_polygons', tables)
        self.assertEqual(_registry_rows(self.db, gps_mig.MIGRATION_ID), 1)

    def test_every_declared_column_is_really_there(self):
        self._make_db()
        gps_mig.run()
        for table, expected in (
                ('gps_daily_aggregates', gps_mig.EXPECTED_AGGREGATE_COLUMNS),
                ('gps_work_polygons', gps_mig.EXPECTED_POLYGON_COLUMNS)):
            got = [r[1] for r in _query(self.db, 'PRAGMA table_info(%s)' % table)]
            self.assertEqual(sorted(got), sorted(expected), table)

    def test_recomputation_cannot_accumulate_rows(self):
        # The UNIQUE rules are the whole reason a recomputed day replaces
        # itself instead of being counted twice.
        self._make_db()
        gps_mig.run()
        con = sqlite3.connect(self.db)
        try:
            con.execute("INSERT INTO gps_daily_aggregates (work_date, "
                        "wialon_id, method_version, computed_at) "
                        "VALUES ('2026-07-27', 3464, 'v', '2026-07-28')")
            con.commit()
            with self.assertRaises(sqlite3.IntegrityError):
                con.execute("INSERT INTO gps_daily_aggregates (work_date, "
                            "wialon_id, method_version, computed_at) "
                            "VALUES ('2026-07-27', 3464, 'v', '2026-07-28')")
            con.rollback()
            con.execute("INSERT INTO gps_work_polygons (work_date, wialon_id, "
                        "site_number, area_ha, minutes, polygon_geojson) "
                        "VALUES ('2026-07-27', 3464, 1, 8.51, 120, '{}')")
            con.commit()
            with self.assertRaises(sqlite3.IntegrityError):
                con.execute("INSERT INTO gps_work_polygons (work_date, "
                            "wialon_id, site_number, area_ha, minutes, "
                            "polygon_geojson) "
                            "VALUES ('2026-07-27', 3464, 1, 9.0, 130, '{}')")
        finally:
            con.close()

    def test_a_site_without_a_contour_is_storable(self):
        # Work on ground nobody registered as a geozone is the normal case:
        # requiring a contour lost 31.77 ha of 159 on the spraying set of 12.08.
        self._make_db()
        gps_mig.run()
        con = sqlite3.connect(self.db)
        try:
            con.execute('PRAGMA foreign_keys=ON')
            con.execute("INSERT INTO gps_work_polygons (work_date, wialon_id, "
                        "site_number, area_ha, minutes, polygon_geojson, "
                        "contour_id) VALUES ('2026-08-05', 1729, 1, 2.4, 61, "
                        "'{}', NULL)")
            con.commit()
            self.assertEqual(_query(self.db, 'SELECT COUNT(*) FROM '
                                             'gps_work_polygons')[0][0], 1)
        finally:
            con.close()

    # --- path 2: repeat run ------------------------------------------------

    def test_second_run_is_a_no_op_and_does_not_duplicate_the_registry_row(self):
        self._make_db()
        gps_mig.run()
        gps_mig.run()
        self.assertEqual(_registry_rows(self.db, gps_mig.MIGRATION_ID), 1)

    def test_second_run_keeps_rows_written_between_the_two(self):
        self._make_db()
        gps_mig.run()
        con = sqlite3.connect(self.db)
        con.execute("INSERT INTO gps_daily_aggregates (work_date, wialon_id, "
                    "method_version, computed_at) "
                    "VALUES ('2026-07-27', 3464, 'v', '2026-07-28')")
        con.commit()
        con.close()
        gps_mig.run()
        self.assertEqual(_query(self.db, 'SELECT COUNT(*) FROM '
                                         'gps_daily_aggregates')[0][0], 1)

    # --- path 3: missing database -----------------------------------------

    def test_missing_database_exits_with_code_2_and_creates_no_file(self):
        with self.assertRaises(SystemExit) as caught:
            gps_mig.run()
        self.assertEqual(caught.exception.code, 2)
        self.assertFalse(os.path.exists(self.db),
                         'the migration created the database it refused to run '
                         'against')

    # --- path 4: failed precondition --------------------------------------

    def test_missing_field_contours_rolls_everything_back(self):
        self._make_db(with_contours=False)
        with self.assertRaises(SystemExit) as caught:
            gps_mig.run()
        self.assertEqual(caught.exception.code, 1)
        tables = _tables(self.db)
        self.assertNotIn('gps_daily_aggregates', tables)
        self.assertNotIn('gps_work_polygons', tables)
        self.assertEqual(_registry_rows(self.db, gps_mig.MIGRATION_ID), 0)


if __name__ == '__main__':
    unittest.main()
