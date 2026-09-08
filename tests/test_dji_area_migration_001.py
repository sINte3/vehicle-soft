# -*- coding: utf-8 -*-
"""DJI-AREA-EVIDENCE-001: четыре пути migrate_dji_area_evidence_001.py.

Чистая база, повтор, отсутствие базы (код 2, файл не создан), непройденное
предусловие (код 1, полный откат, реестр пуст) -- четыре пути, которых
CLAUDE.md требует от каждой миграции. Плюс:

* одна транзакция: отказ записи реестра откатывает таблицы и индексы;
* `drone_flights.area_ha` не тронута -- ни значение, ни `typeof`;
* DDL миграции и ORM-модели совпадают по именам и типам колонок --
  сравнение PRAGMA table_info мигрированной базы и db.create_all() базы.
  db.create_all() дрейф не ловит: он молча создаёт таблицу без колонки.

Всё идёт на выбрасываемых файлах SQLite во временном каталоге. Патчатся ОБА
глобальных `DB_PATH` -- модуля миграции и `migration_utils`.

Запуск:
  python -m unittest tests.test_dji_area_migration_001 -v
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
import migrate_dji_area_evidence_001 as mig  # noqa: E402

SENTINEL_AREA_HA = 1.2345
SYNTHETIC_FLIGHT_ID = 900001

PRECONDITION_DDL = {
    'drone_flights': ('CREATE TABLE drone_flights ('
                      ' id INTEGER PRIMARY KEY, dji_flight_id BIGINT,'
                      ' area_ha FLOAT)'),
}


def _make_db(path, tables=('drone_flights',), with_flight_row=True):
    con = sqlite3.connect(path)
    for table in tables:
        con.execute(PRECONDITION_DDL[table])
    if with_flight_row and 'drone_flights' in tables:
        con.execute('INSERT INTO drone_flights (id, dji_flight_id, area_ha) '
                    'VALUES (1, ?, ?)', (SYNTHETIC_FLIGHT_ID, SENTINEL_AREA_HA))
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


def _indexes(path):
    return {r[0] for r in _query(
        path, "SELECT name FROM sqlite_master WHERE type='index'")}


def _registry_rows(path, migration_id):
    if not _query(path, "SELECT name FROM sqlite_master WHERE type='table' "
                        "AND name='schema_migrations'"):
        return 0
    return _query(path, 'SELECT COUNT(*) FROM schema_migrations WHERE name=?',
                  (migration_id,))[0][0]


def _table_info(path, table):
    """{column: (type, notnull)} через PRAGMA."""
    rows = _query(path, 'PRAGMA table_info(%s)' % table)
    return {r[1]: (r[2].upper(), int(r[3])) for r in rows}


HOUSEKEEPING = {'schema_migrations', 'sqlite_sequence'}


class MigrationPaths(unittest.TestCase):
    MID = mig.MIGRATION_ID

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix='dji_area_mig_')
        self.db = os.path.join(self.tmp, 'throwaway.db')
        self._orig_module_db = mig.DB_PATH
        self._orig_utils_db = migration_utils.DB_PATH
        mig.DB_PATH = self.db
        migration_utils.DB_PATH = self.db

    def tearDown(self):
        mig.DB_PATH = self._orig_module_db
        migration_utils.DB_PATH = self._orig_utils_db
        shutil.rmtree(self.tmp, ignore_errors=True)

    def run_migration(self):
        try:
            mig.run()
            return True, 0
        except SystemExit as exc:
            return False, exc.code

    # ── Путь 1: чистая база ──────────────────────────────────────────────

    def test_path_1_clean_database_creates_everything_once(self):
        _make_db(self.db)
        ok, code = self.run_migration()
        self.assertTrue(ok)
        self.assertEqual(code, 0)
        for table in mig.TABLE_NAMES:
            self.assertIn(table, _tables(self.db))
        for name, _stmt in mig.INDEXES:
            self.assertIn(name, _indexes(self.db))
        self.assertEqual(_registry_rows(self.db, self.MID), 1)

    def test_path_1_every_declared_column_is_really_there(self):
        _make_db(self.db)
        self.assertTrue(self.run_migration()[0])
        expected = mig.expected_columns()
        for table, columns in expected.items():
            info = _table_info(self.db, table)
            for column in columns:
                self.assertIn(column, info, '%s.%s' % (table, column))
        # Ключевые NULL-контракты: NULL никогда не подменяется нулём.
        calc = _table_info(self.db, 'dji_area_calculations')
        for column in ('corrected_recorded_area_m2',
                       'controller_delta_area_m2', 'raw_area_m2',
                       'billable_area_m2', 'customer_id',
                       'application_without_area'):
            self.assertEqual(calc[column][1], 0,
                             '%s must be nullable' % column)
        self.assertEqual(calc['area_status'][1], 1)
        self.assertEqual(calc['aggregation_eligibility'][1], 1)

    def test_path_1_registry_row_carries_checksum_and_description(self):
        _make_db(self.db)
        self.assertTrue(self.run_migration()[0])
        row = _query(self.db, 'SELECT checksum, description FROM '
                              'schema_migrations WHERE name=?', (self.MID,))[0]
        self.assertEqual(len(row[0]), 64)
        self.assertIn('dji_area_calculations', row[1])

    # ── Путь 2: повтор ───────────────────────────────────────────────────

    def test_path_2_second_run_says_already_applied_and_adds_nothing(self):
        _make_db(self.db)
        self.assertTrue(self.run_migration()[0])
        tables_after_first = _tables(self.db)
        ok, code = self.run_migration()
        self.assertTrue(ok)
        self.assertEqual(code, 0)
        self.assertEqual(_registry_rows(self.db, self.MID), 1)
        self.assertEqual(_tables(self.db), tables_after_first)

    # ── Путь 3: базы нет ─────────────────────────────────────────────────

    def test_path_3_missing_database_exits_2_and_creates_no_file(self):
        missing = os.path.join(self.tmp, 'not-there.db')
        mig.DB_PATH = missing
        migration_utils.DB_PATH = missing
        ok, code = self.run_migration()
        self.assertFalse(ok)
        self.assertEqual(code, 2)
        self.assertFalse(os.path.exists(missing))

    # ── Путь 4: непройденное предусловие ─────────────────────────────────

    def test_path_4_missing_precondition_exits_1_and_leaves_nothing(self):
        con = sqlite3.connect(self.db)
        con.execute('CREATE TABLE unrelated (id INTEGER PRIMARY KEY)')
        con.commit()
        con.close()
        before = _tables(self.db)
        ok, code = self.run_migration()
        self.assertFalse(ok)
        self.assertEqual(code, 1)
        self.assertEqual(_tables(self.db) - HOUSEKEEPING, before)
        for table in mig.TABLE_NAMES:
            self.assertNotIn(table, _tables(self.db))
        self.assertEqual(_registry_rows(self.db, self.MID), 0)

    def test_path_4_negative_control_the_same_database_works_once_fixed(self):
        con = sqlite3.connect(self.db)
        con.execute('CREATE TABLE unrelated (id INTEGER PRIMARY KEY)')
        con.commit()
        con.close()
        self.assertFalse(self.run_migration()[0])
        con = sqlite3.connect(self.db)
        con.execute(PRECONDITION_DDL['drone_flights'])
        con.commit()
        con.close()
        ok, code = self.run_migration()
        self.assertTrue(ok)
        self.assertEqual(code, 0)
        self.assertEqual(_registry_rows(self.db, self.MID), 1)


class SchemaProperties(MigrationPaths):

    def test_identities_are_unique(self):
        _make_db(self.db)
        self.assertTrue(self.run_migration()[0])
        con = sqlite3.connect(self.db)
        try:
            con.execute("INSERT INTO dji_source_revisions (provider_account_id,"
                        " flight_id, scope_key, source_type, sha256, size_bytes,"
                        " captured_at_utc, storage_kind, received_at) VALUES"
                        " ('p', 1, '1', 'card', 'x', 1, '2026-01-01', 'inline',"
                        " '2026-01-01')")
            with self.assertRaises(sqlite3.IntegrityError):
                con.execute("INSERT INTO dji_source_revisions (provider_account_id,"
                            " flight_id, scope_key, source_type, sha256, size_bytes,"
                            " captured_at_utc, storage_kind, received_at) VALUES"
                            " ('p', 1, '1', 'card', 'x', 1, '2026-01-02', 'inline',"
                            " '2026-01-02')")
            con.execute("INSERT INTO dji_area_calculations (flight_id,"
                        " provider_account_id, area_algorithm_version,"
                        " calculation_input_hash, calculated_at, start_at_utc,"
                        " report_timezone, report_start_date, area_status,"
                        " aggregation_eligibility) VALUES (1, 'p', 'v', 'h',"
                        " '2026-01-01', '2026-01-01', 'Asia/Tashkent',"
                        " '2026-01-01', 'RAW_UNVERIFIED', 'PROVISIONAL')")
            with self.assertRaises(sqlite3.IntegrityError):
                con.execute("INSERT INTO dji_area_calculations (flight_id,"
                            " provider_account_id, area_algorithm_version,"
                            " calculation_input_hash, calculated_at, start_at_utc,"
                            " report_timezone, report_start_date, area_status,"
                            " aggregation_eligibility) VALUES (1, 'p', 'v', 'h',"
                            " '2026-01-02', '2026-01-01', 'Asia/Tashkent',"
                            " '2026-01-01', 'RAW_UNVERIFIED', 'PROVISIONAL')")
            with self.assertRaises(sqlite3.IntegrityError):
                con.execute("INSERT INTO dji_flight_evidence (flight_id,"
                            " provider_account_id, updated_at) VALUES (1, 'p',"
                            " '2026-01-01')")
                con.execute("INSERT INTO dji_flight_evidence (flight_id,"
                            " provider_account_id, updated_at) VALUES (1, 'p',"
                            " '2026-01-01')")
        finally:
            con.close()

    def test_a_failing_registry_write_rolls_back_tables_and_indexes(self):
        _make_db(self.db)
        before = _tables(self.db)
        original = mig._record_in_transaction

        def refuse(*_args, **_kwargs):
            raise RuntimeError('SYNTHETIC registry failure')

        mig._record_in_transaction = refuse
        try:
            ok, code = self.run_migration()
        finally:
            mig._record_in_transaction = original
        self.assertFalse(ok)
        self.assertEqual(code, 1)
        for table in mig.TABLE_NAMES:
            self.assertNotIn(table, _tables(self.db))
        for name, _stmt in mig.INDEXES:
            self.assertNotIn(name, _indexes(self.db))
        self.assertEqual(_registry_rows(self.db, self.MID), 0)
        self.assertEqual(_tables(self.db) - HOUSEKEEPING, before)
        ok, code = self.run_migration()
        self.assertTrue(ok)
        self.assertEqual(_registry_rows(self.db, self.MID), 1)

    def test_dji_area_ha_is_untouched_by_the_migration(self):
        _make_db(self.db)
        before = _query(self.db, 'SELECT area_ha, typeof(area_ha) FROM '
                                 'drone_flights WHERE id=1')
        self.assertTrue(self.run_migration()[0])
        after = _query(self.db, 'SELECT area_ha, typeof(area_ha) FROM '
                                'drone_flights WHERE id=1')
        self.assertEqual(before, after)
        self.assertEqual(after[0][0], SENTINEL_AREA_HA)

    def test_migration_ddl_matches_the_orm_models(self):
        """PRAGMA table_info мигрированной базы == db.create_all() базы.

        [REASON]: единственная проверка, способная поймать разошедшуюся
        колонку: create_all() на свежей установке и миграция на живой базе
        должны дать одну схему, иначе staging и dev работают на разных
        таблицах, и тест на одной из них ничего не доказывает о другой.
        """
        try:
            from sqlalchemy import create_engine
            import models as m  # noqa: E402
        except ImportError:  # pragma: no cover - CI has no SQLAlchemy
            self.skipTest('SQLAlchemy/Flask not installed: ORM parity runs '
                          'locally with the application dependencies')
        _make_db(self.db)
        self.assertTrue(self.run_migration()[0])

        orm_db = os.path.join(self.tmp, 'orm.db')
        engine = create_engine('sqlite:///' + orm_db)
        wanted = [m.DjiSourceRevision, m.DjiFlightEvidence, m.DjiV4Summary,
                  m.DjiAreaCalculation, m.DjiFieldAttribution,
                  m.DjiLandSnapshot, m.DjiLandRevision, m.DjiLandGeometry]
        m.db.metadata.create_all(engine, tables=[w.__table__ for w in wanted]
                                 + [m.DroneFlight.__table__,
                                    m.DroneUnit.__table__,
                                    m.DroneSyncLog.__table__,
                                    m.Organization.__table__])
        engine.dispose()

        for model in wanted:
            table = model.__tablename__
            migrated = _table_info(self.db, table)
            fresh = _table_info(orm_db, table)
            self.assertEqual(sorted(migrated), sorted(fresh),
                             'column set differs on %s' % table)
            for column, (mtype, mnotnull) in migrated.items():
                ftype, fnotnull = fresh[column]
                self.assertEqual(mtype, ftype,
                                 '%s.%s type %s vs %s' % (table, column,
                                                          mtype, ftype))
                # [REASON]: `id INTEGER PRIMARY KEY AUTOINCREMENT` (migration)
                # and `id INTEGER NOT NULL, PRIMARY KEY (id)` (SQLAlchemy)
                # differ only in the PRAGMA notnull flag of the rowid alias;
                # every existing drone table carries the same difference.
                if column == 'id':
                    continue
                self.assertEqual(mnotnull, fnotnull,
                                 '%s.%s notnull differs' % (table, column))
            migrated_ix = {r[1] for r in _query(
                self.db, 'PRAGMA index_list(%s)' % table)}
            fresh_ix = {r[1] for r in _query(
                orm_db, 'PRAGMA index_list(%s)' % table)}
            self.assertEqual({i for i in migrated_ix if i.startswith('ix_')},
                             {i for i in fresh_ix if i.startswith('ix_')},
                             'index names differ on %s' % table)


if __name__ == '__main__':
    unittest.main()
