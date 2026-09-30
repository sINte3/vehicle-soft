# -*- coding: utf-8 -*-
"""AGRO_WORK_001: четыре пути миграции, которых требует устав.

Чистая база, повтор («Already applied»), отсутствие базы (код 2, файл не
создан), непройденное предусловие (код 1, полный откат, в реестре пусто) --
плюс свойства, которые поздняя правка могла бы тихо развернуть:

  * журнал и история статусов не принимают UPDATE и DELETE -- это говорит
    база, а не обещание импорта;
  * у машины agro-work не больше одной живой связки владельца, а снятая
    связка остаётся в таблице;
  * метод сверки принимает только четыре значения плана;
  * ни у одной таблицы нет колонки для персонального поля.

Всё -- на временных файлах SQLite. Подменяются ОБА DB_PATH: самой миграции и
внутри migration_utils, иначе строка реестра молча легла бы в
instance/transport.db.

Запуск: python -m unittest tests.test_agro_work_migration -v
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

import migration_utils  # noqa: E402
import migrate_agro_work_001 as mig  # noqa: E402

EQUIPMENT_DDL = ('CREATE TABLE equipment (id INTEGER PRIMARY KEY, '
                 'name TEXT, plate TEXT)')


def _query(path, sql, args=()):
    con = sqlite3.connect(path)
    try:
        return con.execute(sql, args).fetchall()
    finally:
        con.close()


def _tables(path):
    return {r[0] for r in _query(
        path, "SELECT name FROM sqlite_master WHERE type='table'")}


def _registry_rows(path):
    if 'schema_migrations' not in _tables(path):
        return 0
    return _query(path, 'SELECT COUNT(*) FROM schema_migrations WHERE name=?',
                  (mig.MIGRATION_ID,))[0][0]


class MigrationPaths(unittest.TestCase):
    def setUp(self):
        self.folder = tempfile.mkdtemp()
        self.db = os.path.join(self.folder, 'transport.db')
        self._saved = (mig.DB_PATH, migration_utils.DB_PATH)
        mig.DB_PATH = self.db
        migration_utils.DB_PATH = self.db

    def tearDown(self):
        mig.DB_PATH, migration_utils.DB_PATH = self._saved

    def _make_db(self, with_equipment=True):
        con = sqlite3.connect(self.db)
        con.execute(EQUIPMENT_DDL if with_equipment
                    else 'CREATE TABLE users (id INTEGER PRIMARY KEY)')
        con.commit()
        con.close()

    def _run(self):
        out = io.StringIO()
        with redirect_stdout(out):
            mig.run()
        return out.getvalue()

    # --- путь 1: чистая база ------------------------------------------------

    def test_clean_database_creates_seven_tables_and_records_itself(self):
        self._make_db()
        text = self._run()
        self.assertTrue(set(mig.EXPECTED_COLUMNS) <= _tables(self.db))
        self.assertEqual(_registry_rows(self.db), 1)
        self.assertIn('Done. 7 agro_work tables', text)
        self.assertTrue(text.isascii())

    def test_every_declared_column_is_really_there_and_nothing_else(self):
        self._make_db()
        self._run()
        for table, expected in mig.EXPECTED_COLUMNS.items():
            got = [r[1] for r in _query(self.db, 'PRAGMA table_info(%s)' % table)]
            self.assertEqual(sorted(got), sorted(expected), table)

    def test_no_table_has_a_column_for_a_personal_field(self):
        self._make_db()
        self._run()
        for table in mig.EXPECTED_COLUMNS:
            got = {r[1] for r in _query(self.db, 'PRAGMA table_info(%s)' % table)}
            self.assertFalse(got & set(mig.FORBIDDEN_COLUMNS), table)
            for column in got:
                for word in ('owner', 'phone', 'driver', 'comment', 'farm_name',
                             '_by_name'):
                    self.assertNotIn(word, column, '%s.%s' % (table, column))

    def test_journal_and_history_refuse_update_and_delete(self):
        self._make_db()
        self._run()
        con = sqlite3.connect(self.db)
        try:
            con.execute("INSERT INTO agro_work_changes (source, entity, "
                        "entity_id, field, old_value, new_value, changed_at) "
                        "VALUES ('import', 'application', 'x', 'volume', '5', "
                        "'7', '2026-09-28 10:00:00')")
            con.execute("INSERT INTO agro_work_import_runs (started_at, status, "
                        "tool_version) VALUES ('2026-09-28 10:00:00', 'ok', 'v')")
            con.execute("INSERT INTO agro_work_applications (id, "
                        "application_number, status, created_at, updated_at, "
                        "created_day, first_seen_run_id, last_seen_run_id) "
                        "VALUES ('a', 'N', 'COMPLETED', 't', 't', '2026-09-10', "
                        "1, 1)")
            con.execute("INSERT INTO agro_work_status_events (application_id, "
                        "action, changed_at, fetched_at) VALUES ('a', "
                        "'created', '2026-09-10T08:00:00+05:00', 'now')")
            con.commit()
            for sql in ("UPDATE agro_work_changes SET new_value = '9'",
                        'DELETE FROM agro_work_changes',
                        "UPDATE agro_work_status_events SET action = 'x'",
                        'DELETE FROM agro_work_status_events'):
                with self.assertRaises(sqlite3.DatabaseError, msg=sql):
                    con.execute(sql)
            # Отрицательный контроль: соседняя таблица без триггера правится.
            con.execute("UPDATE agro_work_applications SET volume = '1'")
        finally:
            con.close()

    def test_history_merge_cannot_duplicate_an_event(self):
        self._make_db()
        self._run()
        con = sqlite3.connect(self.db)
        try:
            sql = ("INSERT OR IGNORE INTO agro_work_status_events "
                   "(application_id, action, old_status, new_status, "
                   "changed_at, fetched_at) VALUES ('a', 'status_changed', "
                   "'IN_PROGRESS', 'COMPLETED', '2026-09-11T17:40:00+05:00', ?)")
            con.execute(sql, ('first',))
            con.execute(sql, ('second',))
            con.commit()
            self.assertEqual(con.execute('SELECT COUNT(*) FROM '
                                         'agro_work_status_events').fetchone()[0], 1)
        finally:
            con.close()

    def test_one_live_owner_link_per_machine_and_unlinked_rows_stay(self):
        self._make_db()
        self._run()
        con = sqlite3.connect(self.db)
        try:
            insert = ("INSERT INTO agro_work_transport_links (agro_transport_id, "
                      "equipment_id, plate_at_link, linked_at, unlinked_at) "
                      "VALUES ('t1', ?, 'P', 'now', ?)")
            con.execute(insert, (1, None))
            with self.assertRaises(sqlite3.IntegrityError):
                con.execute(insert, (2, None))
            con.execute("UPDATE agro_work_transport_links SET unlinked_at = 'x'")
            con.execute(insert, (2, None))
            con.commit()
            self.assertEqual(con.execute('SELECT COUNT(*) FROM '
                                         'agro_work_transport_links').fetchone()[0], 2)
        finally:
            con.close()

    def test_method_accepts_only_the_four_values_of_the_plan(self):
        self._make_db()
        self._run()
        con = sqlite3.connect(self.db)
        try:
            con.execute("INSERT INTO agro_work_work_types (id, name, "
                        "first_seen_at, last_seen_at) VALUES ('w', 'n', 't', 't')")
            for method in mig.METHODS + (None,):
                con.execute('UPDATE agro_work_work_types SET method = ?',
                            (method,))
            for bad in ('га', 'hectare', '', 'GA'):
                with self.assertRaises(sqlite3.IntegrityError, msg=bad):
                    con.execute('UPDATE agro_work_work_types SET method = ?',
                                (bad,))
        finally:
            con.close()

    # --- путь 2: повтор -----------------------------------------------------

    def test_second_run_says_already_applied_and_changes_nothing(self):
        self._make_db()
        self._run()
        before = _query(self.db, 'SELECT type, name, sql FROM sqlite_master '
                                 'ORDER BY name')
        text = self._run()
        self.assertIn('Already applied. Nothing to do.', text)
        self.assertEqual(_registry_rows(self.db), 1)
        self.assertEqual(_query(self.db, 'SELECT type, name, sql FROM '
                                         'sqlite_master ORDER BY name'), before)

    # --- путь 3: базы нет ---------------------------------------------------

    def test_missing_database_exits_2_and_creates_no_file(self):
        with self.assertRaises(SystemExit) as caught:
            self._run()
        self.assertEqual(caught.exception.code, 2)
        self.assertFalse(os.path.exists(self.db))

    # --- путь 4: предусловие не выполнено ----------------------------------

    def test_failed_precondition_exits_1_rolls_back_and_records_nothing(self):
        self._make_db(with_equipment=False)
        with self.assertRaises(SystemExit) as caught:
            self._run()
        self.assertEqual(caught.exception.code, 1)
        self.assertFalse([t for t in _tables(self.db) if t.startswith('agro_work')])
        self.assertEqual(_registry_rows(self.db), 0)


if __name__ == '__main__':
    unittest.main()
