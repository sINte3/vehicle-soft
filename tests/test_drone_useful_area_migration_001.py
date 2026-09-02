# -*- coding: utf-8 -*-
"""DRONE-USEFUL-AREA-001: четыре пути migrate_drones_useful_area_001.py.

Чистая база, повтор, отсутствие базы, непройденное предусловие -- четыре
пути, которых CLAUDE.md требует от каждой миграции. Плюс два свойства, каждое
из которых ручная правка могла бы тихо развернуть:

* уникальность работы -- (`unit_key`, `work_date`, `group_index`), и
  `unit_key` НЕ NULL. Читается обратно через PRAGMA: это встроенное
  ограничение таблицы, и другого свидетеля у него нет;
* `drone_flights.area_ha` не тронута -- ни значение, ни `typeof`.

Всё идёт на выбрасываемых файлах SQLite во временном каталоге. Патчатся ОБА
глобальных `DB_PATH` -- модуля миграции и `migration_utils`, откуда читают
`record_migration()` и `is_migration_applied()`. Патч только первого молча
писал бы строку реестра в instance/transport.db.

Запуск:
  python -m unittest tests.test_drone_useful_area_migration_001 -v
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
import migrate_drones_useful_area_001 as mig  # noqa: E402

# Три REFERENCES-цели, без которых миграция отказывается запускаться.
PRECONDITION_DDL = {
    'drone_flights': ('CREATE TABLE drone_flights ('
                      ' id INTEGER PRIMARY KEY, dji_flight_id BIGINT,'
                      ' area_ha FLOAT)'),
    'drone_units': 'CREATE TABLE drone_units (id INTEGER PRIMARY KEY)',
    'field_contours': 'CREATE TABLE field_contours (id INTEGER PRIMARY KEY)',
}

ALL_PRECONDITIONS = ('drone_flights', 'drone_units', 'field_contours')

# Значение, за которым тест следит: миграция не имеет права его коснуться.
# SYNTHETIC / NOT-REAL, как и идентификатор вылета ниже: в этом файле нет ни
# одной настоящей строки production -- база строится с нуля во временном
# каталоге и удаляется в tearDown.
SENTINEL_AREA_HA = 1.2345
SYNTHETIC_FLIGHT_ID = 900001


def _make_db(path, tables, with_flight_row=True):
    con = sqlite3.connect(path)
    for table in tables:
        con.execute(PRECONDITION_DDL[table])
    if with_flight_row and 'drone_flights' in tables:
        con.execute('INSERT INTO drone_flights (id, dji_flight_id, area_ha) '
                    'VALUES (1, ?, ?)',
                    (SYNTHETIC_FLIGHT_ID, SENTINEL_AREA_HA))
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


class MigrationPaths(unittest.TestCase):
    MID = mig.MIGRATION_ID

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix='drone_useful_area_mig_')
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
        """Запустить. Возвращает (дошла_ли_до_конца, код выхода)."""
        try:
            mig.run()
            return True, 0
        except SystemExit as exc:
            return False, exc.code

    # ── Путь 1: чистая база ──────────────────────────────────────────────

    def test_path_1_clean_database_creates_everything_once(self):
        _make_db(self.db, ALL_PRECONDITIONS)
        ok, code = self.run_migration()

        self.assertTrue(ok)
        self.assertEqual(code, 0)
        self.assertEqual(_registry_rows(self.db, self.MID), 1)
        self.assertIn('drone_flight_routes', _tables(self.db))
        self.assertIn('drone_coverage_works', _tables(self.db))
        for name, _statement in mig.INDEXES:
            self.assertIn(name, _indexes(self.db))

    def test_path_1_every_declared_column_is_really_there(self):
        _make_db(self.db, ALL_PRECONDITIONS)
        self.assertTrue(self.run_migration()[0])

        for table, expected in (('drone_flight_routes', mig.ROUTE_COLUMNS),
                                ('drone_coverage_works', mig.WORK_COLUMNS)):
            actual = {row[1] for row in
                      _query(self.db, 'PRAGMA table_info(%s)' % table)}
            self.assertEqual(set(expected) - actual, set(),
                             'columns missing on %s' % table)

    def test_path_1_registry_row_carries_a_checksum_and_a_description(self):
        _make_db(self.db, ALL_PRECONDITIONS)
        self.assertTrue(self.run_migration()[0])
        checksum, description = _query(
            self.db, 'SELECT checksum, description FROM schema_migrations '
                     'WHERE name=?', (self.MID,))[0]
        self.assertEqual(len(checksum), 64)          # sha256 hex
        self.assertIn('DRONE-USEFUL-AREA-001', description)

    # ── Путь 2: повторный запуск ─────────────────────────────────────────

    def test_path_2_second_run_says_already_applied_and_adds_nothing(self):
        _make_db(self.db, ALL_PRECONDITIONS)
        self.assertTrue(self.run_migration()[0])
        tables_after_first = _tables(self.db)

        ok, code = self.run_migration()
        self.assertTrue(ok)
        self.assertEqual(code, 0)
        # Ровно одна строка реестра, а не вторая.
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
        # [REASON]: sqlite3.connect СОЗДАЁТ пустой файл. Проверка на отказ без
        # проверки на отсутствие файла зелёная и у миграции, которая завела
        # новую базу и отчиталась об успехе.
        self.assertFalse(os.path.exists(missing),
                         'the migration must never create the database file')

    # ── Путь 4: непройденное предусловие ─────────────────────────────────

    def test_path_4_missing_precondition_exits_1_and_leaves_nothing(self):
        # field_contours намеренно отсутствует.
        _make_db(self.db, ('drone_flights', 'drone_units'))
        before = _tables(self.db)

        ok, code = self.run_migration()
        self.assertFalse(ok)
        self.assertEqual(code, 1)
        # [REASON]: `schema_migrations` и `sqlite_sequence` исключены не
        # ради удобства. Первую заводит `ensure_schema_migrations_table()` ДО
        # открытия транзакции -- так делает каждая миграция проекта; вторую
        # SQLite создаёт сам под AUTOINCREMENT первой же такой таблицы.
        # Ни одна из них не является частичной схемой ЭТОЙ миграции, а её
        # собственные таблицы проверяются ниже поимённо.
        housekeeping = {'schema_migrations', 'sqlite_sequence'}
        self.assertEqual(_tables(self.db) - housekeeping, before,
                         'a failed precondition must leave no partial schema')
        self.assertNotIn('drone_flight_routes', _tables(self.db))
        self.assertNotIn('drone_coverage_works', _tables(self.db))
        self.assertEqual(_registry_rows(self.db, self.MID), 0,
                         'a rolled-back migration must not be recorded')

    def test_path_4_negative_control_the_same_database_works_once_fixed(self):
        """Отрицательный контроль к пути 4.

        [REASON]: «миграция отказала» -- ответ, который проверка выше получила
        бы и от миграции, отказывающей ВСЕГДА. Та же база с добавленной
        недостающей таблицей обязана пройти, иначе путь 4 ничего не доказывает.
        """
        _make_db(self.db, ('drone_flights', 'drone_units'))
        self.assertFalse(self.run_migration()[0])

        con = sqlite3.connect(self.db)
        con.execute(PRECONDITION_DDL['field_contours'])
        con.commit()
        con.close()

        ok, code = self.run_migration()
        self.assertTrue(ok)
        self.assertEqual(code, 0)
        self.assertEqual(_registry_rows(self.db, self.MID), 1)


class SchemaProperties(MigrationPaths):
    """Свойства, которые ручная правка могла бы тихо развернуть."""

    def test_work_identity_is_unique_and_unit_key_is_not_null(self):
        """(unit_key, work_date, group_index) уникальна; unit_key NOT NULL.

        [REASON]: если бы ключом была пара (drone_unit_id, work_date), то у
        работы неопознанной машины drone_unit_id был бы NULL, а SQLite считает
        NULL в UNIQUE различными -- пересчёт заводил бы вторую строку той же
        работы каждым прогоном, и сумма росла бы прогон за прогоном, а каждая
        строка выглядела бы правильной.
        """
        _make_db(self.db, ALL_PRECONDITIONS)
        self.assertTrue(self.run_migration()[0])

        columns = {row[1]: row for row in
                   _query(self.db, 'PRAGMA table_info(drone_coverage_works)')}
        self.assertEqual(columns['unit_key'][3], 1, 'unit_key must be NOT NULL')

        con = sqlite3.connect(self.db)
        try:
            insert = ('INSERT INTO drone_coverage_works '
                      '(unit_key, work_date, group_index, inputs_fingerprint, '
                      ' route_fingerprint, algorithm_version, params_json, '
                      ' quality_status, quality_reason, computed_at) '
                      "VALUES (?, '2026-06-05', 0, 'f', 'r', 'useful-area-v1',"
                      " '{}', 'READY_ESTIMATE', 'ALL_INPUTS_PRESENT', "
                      "'2026-06-05 00:00:00')")
            con.execute(insert, ('unit:6',))
            con.commit()
            with self.assertRaises(sqlite3.IntegrityError):
                con.execute(insert, ('unit:6',))
                con.commit()
            con.rollback()
            # Отрицательный контроль: другая машина в тот же день проходит.
            con.execute(insert, ('unit:7',))
            con.commit()
            # И NULL в unit_key отвергается, а не считается «ещё одной работой».
            with self.assertRaises(sqlite3.IntegrityError):
                con.execute(insert, (None,))
                con.commit()
        finally:
            con.close()

    def test_one_flight_can_hold_only_one_route(self):
        """dji_flight_id в drone_flight_routes уникален."""
        _make_db(self.db, ALL_PRECONDITIONS)
        self.assertTrue(self.run_migration()[0])

        con = sqlite3.connect(self.db)
        try:
            insert = ('INSERT INTO drone_flight_routes '
                      '(dji_flight_id, drone_flight_id, point_count, '
                      ' points_json, content_sha256, received_at, updated_at) '
                      "VALUES (?, 1, 2, '[]', 'sha', '2026-06-05 00:00:00', "
                      "'2026-06-05 00:00:00')")
            con.execute(insert, (900001,))
            con.commit()
            with self.assertRaises(sqlite3.IntegrityError):
                con.execute(insert, (900001,))
                con.commit()
            con.rollback()
            con.execute(insert, (900002,))     # отрицательный контроль
            con.commit()
        finally:
            con.close()

    def test_dji_area_ha_is_untouched_by_the_migration(self):
        """`drone_flights.area_ha` до и после -- то же значение и тот же тип."""
        _make_db(self.db, ALL_PRECONDITIONS)
        before = _query(self.db, 'SELECT id, area_ha, typeof(area_ha) '
                                 '  FROM drone_flights ORDER BY id')
        self.assertEqual(before, [(1, SENTINEL_AREA_HA, 'real')])

        self.assertTrue(self.run_migration()[0])

        after = _query(self.db, 'SELECT id, area_ha, typeof(area_ha) '
                                '  FROM drone_flights ORDER BY id')
        self.assertEqual(after, before,
                         'the migration must not touch drone_flights at all')


if __name__ == '__main__':
    unittest.main()
