# -*- coding: utf-8 -*-
"""FIN-RECON-STORAGE-001: миграция migrate_fin_recon_storage_001.py.

Четыре пути устава: чистая база, повтор («Already applied»), нет базы (код 2,
файл не создан), непройденное предусловие (код 1, полный откат, реестр
пуст). Плюс то, что делает таблицы снимка снимком:

* UPDATE и DELETE запрещены на КАЖДОЙ таблице;
* в запечатанный снимок нельзя дописать строку ни одной дочерней таблицы;
* снимок с заполненным утверждением или закрытием не вставляется;
* версия N > 1 вставляется только со ссылкой на версию N-1 того же месяца
  и с её хешем;
* NULL ровно при UNKNOWN: ноль на месте неизвестного не помещается;
* чужие таблицы (drone_works) не тронуты ни значением, ни числом строк.

Всё на выбрасываемых файлах SQLite. Запуск:
  python -m unittest tests.test_fin_recon_migration -v
"""

import hashlib
import io
import os
import shutil
import sqlite3
import sys
import tempfile
import unittest

from contextlib import redirect_stdout

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if REPO_ROOT not in sys.path:
    sys.path.insert(0, REPO_ROOT)

import migration_utils  # noqa: E402
import migrate_fin_recon_storage_001 as mig  # noqa: E402
from fin_recon import schema  # noqa: E402

SHA = 'a' * 64
SHA2 = 'b' * 64


def file_sha(path):
    with open(path, 'rb') as fh:
        return hashlib.sha256(fh.read()).hexdigest()


def run_migration(path):
    out = io.StringIO()
    saved = migration_utils.DB_PATH
    try:
        with redirect_stdout(out):
            code = mig.run(path)
    finally:
        migration_utils.DB_PATH = saved
    return code, out.getvalue()


def make_db(path, with_drone_works=True):
    con = sqlite3.connect(path)
    if with_drone_works:
        con.execute('CREATE TABLE drone_works (id INTEGER PRIMARY KEY, '
                    'area_ha FLOAT, note TEXT)')
        con.execute("INSERT INTO drone_works VALUES (1, 12.34, 'synthetic')")
    con.commit()
    con.close()


def snapshot_row(period='2031-03', version=1, content=SHA, supersedes_id=None,
                 supersedes_sha=None, approved=None, closed=None):
    return {
        'period': period, 'version': version, 'supersedes_id': supersedes_id,
        'supersedes_content_sha256': supersedes_sha, 'revision_reason': None,
        'status': 'RECONCILED_WITH_EXCEPTIONS', 'owner_approved_at': approved,
        'financially_closed_at': closed, 'title': 't', 'currency': 'UZS',
        'compiled_at': '2031-10-12', 'producer': 'p',
        'normalization_version': 'n', 'schema_version': 'fin-recon-snapshot/1',
        'header_json': '{}', 'vs_json': None, 'content_sha256': content,
        'snapshot_file_sha256': SHA, 'manifest_sha256': SHA,
        'package_id': 'synthetic', 'snapshot_json': '{}',
        'imported_at': '2031-10-12T00:00:00Z', 'imported_by': 'test',
        'importer_version': 'test'}


def insert(con, table, row):
    cols = sorted(row)
    cur = con.execute('INSERT INTO %s (%s) VALUES (%s)'
                      % (table, ', '.join(cols), ', '.join('?' * len(cols))),
                      [row[c] for c in cols])
    return cur.lastrowid


def anomaly_row(snapshot_id, key='A01'):
    return {'snapshot_id': snapshot_id, 'position': 0, 'anomaly_key': key,
            'priority': 'P0', 'status': 'ОТКРЫТО', 'status_code': 'OPEN',
            'group_label': None, 'finding': 'f', 'rule': 'r',
            'operators_json': '[]', 'source_address': None}


class Base(unittest.TestCase):

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix='fin_recon_mig_')
        self.db = os.path.join(self.tmp, 'transport.db')

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def query(self, sql, args=()):
        con = sqlite3.connect(self.db)
        try:
            return con.execute(sql, args).fetchall()
        finally:
            con.close()

    def registry(self):
        if not self.query("SELECT name FROM sqlite_master WHERE type='table' "
                          "AND name='schema_migrations'"):
            return 0
        return self.query('SELECT COUNT(*) FROM schema_migrations WHERE '
                          'name = ?', (mig.MIGRATION_ID,))[0][0]


class FourPaths(Base):

    def test_clean_database(self):
        make_db(self.db)
        code, out = run_migration(self.db)
        self.assertEqual(code, 0, out)
        self.assertIn('Done.', out)
        tables = {r[0] for r in self.query(
            "SELECT name FROM sqlite_master WHERE type='table'")}
        for name in schema.TABLE_NAMES:
            self.assertIn(name, tables)
        self.assertEqual(self.registry(), 1)
        self.assertTrue(out.isascii())

    def test_repeat_is_already_applied(self):
        make_db(self.db)
        self.assertEqual(run_migration(self.db)[0], 0)
        before = file_sha(self.db)
        code, out = run_migration(self.db)
        self.assertEqual(code, 0)
        self.assertIn('Already applied. Nothing to do.', out)
        self.assertEqual(self.registry(), 1)
        self.assertEqual(before, file_sha(self.db))

    def test_missing_database_exit_2_and_nothing_created(self):
        code, out = run_migration(self.db)
        self.assertEqual(code, 2)
        self.assertIn('refusing to run', out)
        self.assertFalse(os.path.exists(self.db))

    def test_failed_precondition_exit_1_full_rollback(self):
        make_db(self.db)
        con = sqlite3.connect(self.db)
        con.execute('CREATE TABLE fin_recon_anomaly (id INTEGER PRIMARY KEY)')
        con.commit()
        con.close()
        code, out = run_migration(self.db)
        self.assertEqual(code, 1, out)
        self.assertIn('precondition failed', out)
        tables = {r[0] for r in self.query(
            "SELECT name FROM sqlite_master WHERE type='table'")}
        self.assertNotIn('fin_recon_period_snapshot', tables)
        self.assertEqual(self.query(
            "SELECT COUNT(*) FROM sqlite_master WHERE type='trigger'")[0][0], 0)
        self.assertEqual(self.registry(), 0)

    def test_columns_match_the_ddl(self):
        make_db(self.db)
        run_migration(self.db)
        for table, cols in schema.expected_columns().items():
            got = [r[1] for r in self.query('PRAGMA table_info(%s)' % table)]
            self.assertEqual(got, cols, table)

    def test_existing_tables_untouched(self):
        make_db(self.db)
        run_migration(self.db)
        self.assertEqual(self.query('SELECT id, area_ha, note, typeof(area_ha) '
                                    'FROM drone_works'),
                         [(1, 12.34, 'synthetic', 'real')])

    def test_empty_restore_database_is_enough(self):
        """Пустой файл (восстановление из пакета) -- годная база."""
        open(self.db, 'wb').close()
        code, out = run_migration(self.db)
        self.assertEqual(code, 0, out)
        self.assertEqual(self.registry(), 1)

    def test_drift_checker_sees_the_migration_id(self):
        """tools/check_migration_drift.py ищет литерал MIGRATION_ID."""
        import ast
        with open(mig.__file__, encoding='utf-8') as fh:
            tree = ast.parse(fh.read())
        names = [t.targets[0].id for t in tree.body
                 if isinstance(t, ast.Assign) and
                 isinstance(t.targets[0], ast.Name) and
                 isinstance(t.value, ast.Constant)]
        self.assertIn('MIGRATION_ID', names)


class Immutability(Base):

    def setUp(self):
        super(Immutability, self).setUp()
        make_db(self.db)
        self.assertEqual(run_migration(self.db)[0], 0)
        self.con = sqlite3.connect(self.db)

    def tearDown(self):
        self.con.close()
        super(Immutability, self).tearDown()

    def raises(self, sql, args=(), text=None):
        with self.assertRaises(sqlite3.DatabaseError) as ctx:
            self.con.execute(sql, args)
        if text:
            self.assertIn(text, str(ctx.exception))

    def sealed_snapshot(self):
        sid = insert(self.con, 'fin_recon_period_snapshot', snapshot_row())
        insert(self.con, 'fin_recon_anomaly', anomaly_row(sid))
        insert(self.con, 'fin_recon_snapshot_seal', {
            'snapshot_id': sid, 'sealed_at': 'now', 'content_sha256': SHA,
            'row_counts_json': '{}'})
        self.con.commit()
        return sid

    def test_update_and_delete_refused_on_every_table(self):
        # Триггер BEFORE UPDATE/DELETE срабатывает на строке, поэтому снимок
        # загружается целиком: строки есть в КАЖДОЙ таблице.
        from tests import fin_recon_fixture as fx
        from fin_recon import importer, package
        self.con.close()
        pkg = package.verify(fx.write_package(
            os.path.join(self.tmp, 'pkg'))['root'])
        importer.import_package(self.db, pkg, imported_by='test')
        self.con = sqlite3.connect(self.db)
        counts = {}
        for table in schema.TABLE_NAMES:
            counts[table] = self.con.execute(
                'SELECT COUNT(*) FROM %s' % table).fetchone()[0]
            self.assertGreater(counts[table], 0, table)
            key = 'snapshot_id' if table != schema.SNAPSHOT_TABLE else 'id'
            with self.subTest(table=table):
                self.raises('UPDATE %s SET %s = %s' % (table, key, key), (),
                            'immutable')
                self.raises('DELETE FROM %s' % table, (), 'immutable')
        for table in schema.TABLE_NAMES:
            self.assertEqual(self.con.execute(
                'SELECT COUNT(*) FROM %s' % table).fetchone()[0],
                counts[table], table)

    def test_sealed_snapshot_refuses_new_rows(self):
        sid = self.sealed_snapshot()
        with self.assertRaises(sqlite3.DatabaseError) as ctx:
            insert(self.con, 'fin_recon_anomaly', anomaly_row(sid, 'A02'))
        self.assertIn('sealed', str(ctx.exception))

    def test_unsealed_snapshot_accepts_rows(self):
        """Отрицательный контроль: без печати та же вставка проходит."""
        sid = insert(self.con, 'fin_recon_period_snapshot', snapshot_row())
        insert(self.con, 'fin_recon_anomaly', anomaly_row(sid, 'A02'))
        self.assertEqual(self.con.execute(
            'SELECT COUNT(*) FROM fin_recon_anomaly').fetchone()[0], 1)

    def test_approval_and_close_refused(self):
        for approved, closed in (('2031-10-13', None), (None, '2031-10-13')):
            with self.assertRaises(sqlite3.DatabaseError) as ctx:
                insert(self.con, 'fin_recon_period_snapshot',
                       snapshot_row(approved=approved, closed=closed))
            self.assertIn('approval', str(ctx.exception))

    def test_status_other_than_reconciled_refused(self):
        row = snapshot_row()
        row['status'] = 'FINANCIALLY_CLOSED'
        with self.assertRaises(sqlite3.DatabaseError):
            insert(self.con, 'fin_recon_period_snapshot', row)

    def test_version_chain(self):
        v1 = insert(self.con, 'fin_recon_period_snapshot', snapshot_row())
        # v2 без ссылки, со ссылкой на чужой хеш, на чужой месяц -- отказ.
        for kwargs in ({'supersedes_id': None, 'supersedes_sha': None},
                       {'supersedes_id': v1, 'supersedes_sha': SHA2},
                       {'supersedes_id': v1, 'supersedes_sha': SHA,
                        'period': '2031-04'}):
            with self.subTest(**{k: str(v) for k, v in kwargs.items()}):
                with self.assertRaises(sqlite3.DatabaseError) as ctx:
                    insert(self.con, 'fin_recon_period_snapshot',
                           snapshot_row(version=2, content=SHA2, **kwargs))
                self.assertIn('version chain', str(ctx.exception))
        insert(self.con, 'fin_recon_period_snapshot',
               snapshot_row(version=2, content=SHA2, supersedes_id=v1,
                            supersedes_sha=SHA))
        # v1 со ссылкой -- отказ.
        with self.assertRaises(sqlite3.DatabaseError):
            insert(self.con, 'fin_recon_period_snapshot',
                   snapshot_row(period='2031-05', content='c' * 64,
                                supersedes_id=v1, supersedes_sha=SHA))

    def test_unknown_must_be_null_and_null_must_be_unknown(self):
        sid = insert(self.con, 'fin_recon_period_snapshot', snapshot_row())
        base = {'snapshot_id': sid, 'position': 0, 'group_key': 'MONEY',
                'label': 'salary', 'semantic': 'SALARY_ACCRUAL', 'unit': 'UZS',
                'check_rule': None, 'identity_json': None, 'note': None}
        bad = ({'metric_key': 'A', 'value': '0', 'evidence': 'UNKNOWN'},
               {'metric_key': 'B', 'value': None, 'evidence': 'BOOK_TEXT'})
        for extra in bad:
            with self.subTest(**extra):
                with self.assertRaises(sqlite3.DatabaseError):
                    insert(self.con, 'fin_recon_metric', dict(base, **extra))
        insert(self.con, 'fin_recon_metric',
               dict(base, metric_key='C', value=None, evidence='UNKNOWN'))

    def test_payout_kind_fixes_its_evidence(self):
        sid = insert(self.con, 'fin_recon_period_snapshot', snapshot_row())
        row = {'snapshot_id': sid, 'position': 0, 'payout_key': 'P01',
               'operator_key': 'OP2', 'kind': 'OWNER_RECOGNIZED',
               'amount': '1.00', 'evidence': 'BOOK_TEXT', 'record_date': None,
               'text': None, 'ref_json': None, 'decision_key': 'R3',
               'note': None}
        with self.assertRaises(sqlite3.DatabaseError):
            insert(self.con, 'fin_recon_payout', row)
        insert(self.con, 'fin_recon_payout',
               dict(row, evidence='OWNER_INTERPRETATION'))

    def test_seal_must_match_its_snapshot(self):
        sid = insert(self.con, 'fin_recon_period_snapshot', snapshot_row())
        with self.assertRaises(sqlite3.DatabaseError):
            insert(self.con, 'fin_recon_snapshot_seal', {
                'snapshot_id': sid, 'sealed_at': 'now',
                'content_sha256': SHA2, 'row_counts_json': '{}'})


if __name__ == '__main__':
    unittest.main()
