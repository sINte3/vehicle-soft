# -*- coding: utf-8 -*-
"""fin_recon/importer.py -- единственный писатель таблиц `fin_recon_*`.

Принимает только ПРОВЕРЕННЫЙ пакет (`package.verify`): хеши всех файлов,
манифест и контракт снимка проверены до того, как открыта транзакция.

Поведение по ключу (месяц, версия, хеш содержимого):

* такой снимок уже есть с тем же хешем -- ALREADY_IMPORTED: ни одной новой
  строки, база не пишется вовсе;
* та же версия месяца с ДРУГИМ хешем -- конфликт, отказ: изменённый v1 не
  может тихо стать «тем же v1»;
* версия N > 1 -- только если в базе есть версия N-1 того же месяца ровно с
  тем хешем, на который ссылается снимок; старая версия не меняется;
* иначе -- одна транзакция: строка снимка, все дочерние строки, печать.
  До коммита снимок собирается обратно из строк базы и его хеш сверяется с
  хешем пакета; любое расхождение или сбой -- откат целиком, в базе ничего.

Работает через stdlib sqlite3 без Flask.
"""

import getpass
import socket
import sqlite3

from datetime import datetime, timezone

import fin_recon as fr
from fin_recon import contract
from fin_recon import schema
from fin_recon import store

RESULT_IMPORTED = 'IMPORTED'
RESULT_ALREADY = 'ALREADY_IMPORTED'


class SchemaMissing(Exception):
    """Таблиц FIN_RECON_STORAGE_001 в базе нет -- миграция не применена."""


class ImportConflict(Exception):
    """Снимок противоречит уже загруженному (та же версия, другой хеш, и т.п.)."""


class ImportFailed(Exception):
    """Сбой внутри транзакции; база откачена."""


def default_actor():
    try:
        user = getpass.getuser()
    except Exception:  # noqa: BLE001 -- имя пользователя ОС не обязательно
        user = 'unknown'
    return '%s@%s' % (user, socket.gethostname())


def _now():
    return datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')


def _insert(cur, table, row):
    columns = sorted(row)
    cur.execute('INSERT INTO %s (%s) VALUES (%s)'
                % (table, ', '.join(columns), ', '.join('?' * len(columns))),
                [row[c] for c in columns])
    return cur.lastrowid


def connect_rw(db_path):
    con = sqlite3.connect(db_path, timeout=30, isolation_level=None)
    con.execute('PRAGMA foreign_keys = ON')
    return con


def existing(con, period, version):
    row = con.execute('SELECT id, content_sha256 FROM '
                      'fin_recon_period_snapshot WHERE period = ? AND '
                      'version = ?', (period, version)).fetchone()
    return row


def import_package(db_path, pkg, imported_by=None, fail_after=None):
    """Загрузить проверенный пакет. Вернуть словарь с результатом.

    fail_after -- только для тестов атомарности: имя таблицы, после вставки
    строк которой бросается исключение внутри транзакции.
    """
    snapshot = contract.require_valid(pkg.snapshot)
    header = snapshot['header']
    period, version = header['period'], header['version']
    con = connect_rw(db_path)
    try:
        missing = schema.missing_tables(con)
        if missing:
            raise SchemaMissing('tables missing: %s (apply migration %s)'
                                % (', '.join(missing), fr.MIGRATION_ID))
        row = existing(con, period, version)
        if row is not None:
            if row[1] == pkg.content_sha256:
                return {'result': RESULT_ALREADY, 'snapshot_id': row[0],
                        'period': period, 'version': version,
                        'content_sha256': pkg.content_sha256,
                        'rows_written': 0}
            raise ImportConflict(
                '%s v%d is already imported with content %s; this package has '
                '%s. A changed snapshot is a NEW version (v%d) that supersedes '
                'v%d, never the same version.'
                % (period, version, row[1], pkg.content_sha256, version + 1,
                   version))
        supersedes_id = None
        if version > 1:
            prev = existing(con, period, version - 1)
            wanted = header['supersedes']['content_sha256']
            if prev is None:
                raise ImportConflict('%s v%d supersedes v%d, which is not in '
                                     'the database' % (period, version,
                                                       version - 1))
            if prev[1] != wanted:
                raise ImportConflict(
                    '%s v%d supersedes v%d with content %s, the database holds '
                    'v%d with content %s' % (period, version, version - 1,
                                             wanted, version - 1, prev[1]))
            supersedes_id = prev[0]
        else:
            other = con.execute('SELECT version FROM fin_recon_period_snapshot '
                                'WHERE period = ? ORDER BY version',
                                (period,)).fetchall()
            if other:
                raise ImportConflict('%s already has version(s) %s; version 1 '
                                     'cannot be added again'
                                     % (period, [r[0] for r in other]))

        children = store.rows_for_snapshot(snapshot)
        expected_counts = {t: len(rows) for t, rows in children.items()}
        head = store.header_row(snapshot)
        head.update({
            'supersedes_id': supersedes_id,
            'content_sha256': pkg.content_sha256,
            'snapshot_file_sha256': pkg.snapshot_file_sha256,
            'manifest_sha256': pkg.manifest_sha256,
            'package_id': pkg.package_id,
            'imported_at': _now(),
            'imported_by': (imported_by or default_actor())[:200],
            'importer_version': fr.IMPORTER_VERSION,
        })
        cur = con.cursor()
        cur.execute('BEGIN IMMEDIATE')
        try:
            snapshot_id = _insert(cur, 'fin_recon_period_snapshot', head)
            for table in schema.CHILD_TABLES:
                for row in children[table]:
                    row = dict(row)
                    row['snapshot_id'] = snapshot_id
                    _insert(cur, table, row)
                if fail_after == table:
                    raise RuntimeError('injected failure after %s' % table)
            counts = store.row_counts(con, snapshot_id)
            if counts != expected_counts:
                raise ImportFailed('row counts after insert %r differ from the '
                                   'snapshot %r' % (counts, expected_counts))
            rebuilt = store.load_snapshot(con, snapshot_id)
            rebuilt_sha = contract.content_sha256(rebuilt)
            if rebuilt_sha != pkg.content_sha256:
                raise ImportFailed('the snapshot rebuilt from the rows has '
                                   'content %s, the package %s'
                                   % (rebuilt_sha, pkg.content_sha256))
            _insert(cur, schema.SEAL_TABLE, {
                'snapshot_id': snapshot_id, 'sealed_at': _now(),
                'content_sha256': pkg.content_sha256,
                'row_counts_json': contract.canonical_bytes(
                    counts).decode('utf-8')})
            cur.execute('COMMIT')
        except Exception:
            cur.execute('ROLLBACK')
            raise
        return {'result': RESULT_IMPORTED, 'snapshot_id': snapshot_id,
                'period': period, 'version': version,
                'content_sha256': pkg.content_sha256,
                'rows_written': 1 + sum(expected_counts.values()) + 1,
                'row_counts': expected_counts}
    finally:
        con.close()
