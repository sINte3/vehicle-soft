#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""tools/fin_recon_storage.py -- FIN-RECON-STORAGE-001: пакет снимка, импорт,
сверка, восстановление в пустую базу.

Команды (вывод в консоль только ASCII, последняя строка -- RESULT=...):

  check-package --package DIR [--expect FILE]
      проверить пакет целиком: SHA256SUMS, манифест, контракт снимка,
      принадлежность книг исходному архиву; ожидания -- против самого
      снимка. В базу не ходит.

  import --db FILE --package DIR [--imported-by NAME]
      загрузить снимок одной транзакцией. Повтор того же пакета -- ничего не
      пишет (ALREADY_IMPORTED). Та же версия с другим содержимым -- отказ.

  verify --db FILE --package DIR [--expect FILE]
      только чтение (mode=ro): база против пакета -- хеши, печать, строки,
      снимок, собранный обратно из строк, статус; и приватные ожидания.

  restore-test --package DIR --workdir DIR [--expect FILE]
      восстановление из пакета в ПУСТУЮ базу в новом каталоге: миграция,
      импорт, сверка, повторный импорт без единой записи (хеш файла базы до
      и после). Ни одной чужой базы не касается.

  status --db FILE
      только чтение: какие снимки лежат в базе.

Коды выхода: 0 -- PASS; 1 -- непредвиденная ошибка; 2 -- аргументы или нет
файла базы/каталога; 3 -- конфликт версий; 4 -- пакет или снимок негоден;
5 -- миграция не применена; 6 -- сверка не сошлась.

Без Flask: `import app` вызвал бы db.create_all() и сделал бы инструмент
писателем схемы.
"""

import argparse
import os
import sqlite3
import sys

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if REPO_ROOT not in sys.path:
    sys.path.insert(0, REPO_ROOT)

import fin_recon as fr  # noqa: E402
from fin_recon import contract, importer, package, schema, store  # noqa: E402
from fin_recon import verify as fverify  # noqa: E402

EXIT_OK = 0
EXIT_UNEXPECTED = 1
EXIT_USAGE = 2
EXIT_CONFLICT = 3
EXIT_INVALID = 4
EXIT_SCHEMA = 5
EXIT_MISMATCH = 6

# Контроли, которые печатаются после импорта, сверки и восстановления: их
# видит владелец и сверяет с приватным списком ожиданий месяца.
SHOWN_CONTROLS = (
    'WORKS_COUNT', 'WORKS_PERIOD_COUNT', 'WORKS_OTHER_MONTHS_COUNT',
    'OPERATORS_COUNT', 'ANOMALIES_COUNT', 'ANOMALIES_OPEN_COUNT',
    'CROSS_LINKS_COUNT', 'BOOK_AREA_SUM', 'BOOK_AREA_PERIOD_SUM',
    'BOOK_AREA_OTHER_MONTHS_SUM', 'ACCRUED_SUM', 'PAYOUT_TEXT_SUM',
    'PAYOUT_OWNER_SUM', 'PAYOUT_RECOGNIZED_SUM', 'DIFFERENCE_SUM',
    'UNKNOWN_SALARY_AREA_SUM', 'UNKNOWN_SALARY_WORKS_COUNT', 'VS_DB_ROWS',
    'VS_EQUAL', 'VS_EXPECTED', 'VS_UNEXPECTED', 'VS_MISSING',
    'VS_DB_AREA_SUM', 'DJI_FLIGHTS', 'DJI_RAW', 'DJI_ACCEPTED',
    'DJI_EXCLUDED', 'DJI_OPEN_REVIEW', 'DJI_UNATTRIBUTED',
    'DJI_ADMIN_DECISIONS')


def out(line=''):
    print(str(line).encode('ascii', 'backslashreplace').decode('ascii'))


def fail(code, message):
    out('ERROR: %s' % message)
    out('RESULT=FAIL code=%d' % code)
    return code


def _problems(title, problems, limit=60):
    out('%s: %d problem(s)' % (title, len(problems)))
    for line in problems[:limit]:
        out('  - %s' % line)
    if len(problems) > limit:
        out('  ... %d more' % (len(problems) - limit))


def _load_package(path):
    try:
        return package.verify(path), None
    except package.PackageError as exc:
        _problems('PACKAGE INVALID', exc.problems)
        return None, EXIT_INVALID


def _load_expect(path):
    if not path:
        return None, None
    if not os.path.isfile(path):
        return None, fail(EXIT_USAGE, 'expectations file not found: %s' % path)
    try:
        return package.load_expectations(path), None
    except package.PackageError as exc:
        _problems('EXPECTATIONS INVALID', exc.problems)
        out('RESULT=FAIL code=%d' % EXIT_INVALID)
        return None, EXIT_INVALID


def _print_package(pkg):
    out('package_id=%s' % pkg.package_id)
    out('period=%s version=%d' % (pkg.period, pkg.version))
    out('content_sha256=%s' % pkg.content_sha256)
    out('snapshot_file_sha256=%s' % pkg.snapshot_file_sha256)
    out('manifest_sha256=%s' % pkg.manifest_sha256)
    out('files=%d blobs=%d' % (len(pkg.files), len(pkg.manifest['blobs'])))


def _print_controls(controls):
    for name in SHOWN_CONTROLS:
        value = controls.get(name)
        out('CONTROL %s=%s' % (name, '-' if value is None
                               else contract.decimal_text(value)))


def _print_expectations(rows):
    bad = 0
    for name, want, got, ok in rows:
        if not ok:
            bad += 1
        out('EXPECT %s %s expected=%s actual=%s'
            % ('OK  ' if ok else 'FAIL', name, fverify._text(want),
               fverify._text(got)))
    out('EXPECTATIONS %d checked, %d failed' % (len(rows), bad))
    return bad


def cmd_check_package(args):
    pkg, code = _load_package(args.package)
    if pkg is None:
        out('RESULT=FAIL code=%d' % code)
        return code
    expectations, code = _load_expect(args.expect)
    if code:
        return code
    _print_package(pkg)
    _print_controls(contract.derived_checks(pkg.snapshot))
    if expectations is not None:
        bad = _print_expectations(fverify.compare_expectations(
            pkg.snapshot, expectations['expect']))
        if bad:
            out('RESULT=FAIL code=%d' % EXIT_MISMATCH)
            return EXIT_MISMATCH
    out('RESULT=PASS package=%s content_sha256=%s'
        % (pkg.package_id, pkg.content_sha256))
    return EXIT_OK


def _db_must_exist(path):
    if not os.path.isfile(path):
        return fail(EXIT_USAGE, 'database not found: %s (nothing was created)'
                    % path)
    return None


def cmd_import(args):
    code = _db_must_exist(args.db)
    if code:
        return code
    pkg, code = _load_package(args.package)
    if pkg is None:
        out('RESULT=FAIL code=%d' % code)
        return code
    _print_package(pkg)
    try:
        result = importer.import_package(args.db, pkg,
                                         imported_by=args.imported_by)
    except importer.SchemaMissing as exc:
        return fail(EXIT_SCHEMA, str(exc))
    except importer.ImportConflict as exc:
        return fail(EXIT_CONFLICT, 'CONFLICT: %s' % exc)
    except (importer.ImportFailed, sqlite3.DatabaseError) as exc:
        return fail(EXIT_UNEXPECTED, 'import rolled back: %s' % exc)
    out('import=%s snapshot_id=%d rows_written=%d'
        % (result['result'], result['snapshot_id'], result['rows_written']))
    if result.get('row_counts'):
        for table in schema.CHILD_TABLES:
            out('  %s=%d' % (table, result['row_counts'][table]))
    _print_controls(contract.derived_checks(pkg.snapshot))
    out('RESULT=PASS import=%s period=%s version=%d content_sha256=%s'
        % (result['result'], pkg.period, pkg.version, pkg.content_sha256))
    return EXIT_OK


def _verify(db_path, pkg, expectations):
    con = store.connect_ro(db_path)
    try:
        report = fverify.verify_db(con, pkg, expectations)
    finally:
        con.close()
    for name, want, got, ok in report['checks']:
        if ok:
            out('CHECK OK   %s' % name)
        else:
            out('CHECK FAIL %s expected=%s actual=%s'
                % (name, fverify._text(want), fverify._text(got)))
    if report['controls']:
        _print_controls(report['controls'])
    bad = 0
    if report.get('expectations'):
        bad = _print_expectations(report['expectations'])
    return report, bad


def cmd_verify(args):
    code = _db_must_exist(args.db)
    if code:
        return code
    pkg, code = _load_package(args.package)
    if pkg is None:
        out('RESULT=FAIL code=%d' % code)
        return code
    expectations, code = _load_expect(args.expect)
    if code:
        return code
    _print_package(pkg)
    try:
        report, _bad = _verify(args.db, pkg, expectations)
    except fverify.VerifySchemaMissing as exc:
        return fail(EXIT_SCHEMA, str(exc))
    if not report['ok']:
        out('RESULT=FAIL code=%d' % EXIT_MISMATCH)
        return EXIT_MISMATCH
    out('RESULT=PASS verify period=%s version=%d content_sha256=%s'
        % (pkg.period, pkg.version, pkg.content_sha256))
    return EXIT_OK


def _sha_file(path):
    return package.sha256_file(path)


def cmd_restore_test(args):
    pkg, code = _load_package(args.package)
    if pkg is None:
        out('RESULT=FAIL code=%d' % code)
        return code
    expectations, code = _load_expect(args.expect)
    if code:
        return code
    workdir = os.path.abspath(args.workdir)
    if os.path.exists(workdir) and os.listdir(workdir):
        return fail(EXIT_USAGE, 'workdir %s exists and is not empty -- the '
                    'restore test needs a NEW empty database' % workdir)
    os.makedirs(workdir, exist_ok=True)
    db_path = os.path.join(workdir, 'restore_test.db')
    # [REASON]: здесь пустая база создаётся НАМЕРЕННО и только здесь: новый
    # файл в новом каталоге -- весь смысл проверки восстановления. Миграция
    # сама по-прежнему отказывается создавать базу (код 2).
    sqlite3.connect(db_path).close()
    _print_package(pkg)
    out('restore database=%s' % db_path)
    import migrate_fin_recon_storage_001 as migration
    code = migration.run(db_path)
    if code != 0:
        return fail(EXIT_UNEXPECTED, 'migration exit %d on the empty database'
                    % code)
    try:
        first = importer.import_package(db_path, pkg,
                                        imported_by='restore-test')
    except (importer.SchemaMissing, importer.ImportConflict,
            importer.ImportFailed, sqlite3.DatabaseError) as exc:
        return fail(EXIT_UNEXPECTED, 'import into the empty database: %s' % exc)
    out('import #1=%s rows_written=%d' % (first['result'],
                                          first['rows_written']))
    if first['result'] != importer.RESULT_IMPORTED:
        return fail(EXIT_UNEXPECTED, 'the empty database already had it?')
    report, _bad = _verify(db_path, pkg, expectations)
    sha_before = _sha_file(db_path)
    second = importer.import_package(db_path, pkg, imported_by='restore-test')
    sha_after = _sha_file(db_path)
    out('import #2=%s rows_written=%d' % (second['result'],
                                          second['rows_written']))
    out('db sha256 before repeat=%s after=%s unchanged=%s'
        % (sha_before, sha_after, sha_before == sha_after))
    idempotent = (second['result'] == importer.RESULT_ALREADY and
                  second['rows_written'] == 0 and sha_before == sha_after)
    if not report['ok'] or not idempotent:
        out('RESULT=FAIL code=%d' % EXIT_MISMATCH)
        return EXIT_MISMATCH
    out('RESULT=PASS restore-test period=%s version=%d content_sha256=%s db=%s'
        % (pkg.period, pkg.version, pkg.content_sha256, db_path))
    return EXIT_OK


def cmd_status(args):
    code = _db_must_exist(args.db)
    if code:
        return code
    con = store.connect_ro(args.db)
    try:
        missing = schema.missing_tables(con)
        registered = None
        try:
            registered = con.execute(
                'SELECT applied_at FROM schema_migrations WHERE name = ?',
                (fr.MIGRATION_ID,)).fetchone()
        except sqlite3.OperationalError:
            registered = None
        out('migration %s registered=%s tables_missing=%d'
            % (fr.MIGRATION_ID, 'yes' if registered else 'no', len(missing)))
        if missing:
            out('RESULT=PASS status snapshots=0 (tables not installed)')
            return EXIT_OK
        rows = store.list_snapshots(con)
    finally:
        con.close()
    for r in rows:
        counts = r['row_counts']
        out('SNAPSHOT %s v%d status=%s sealed=%s works=%s operators=%s '
            'anomalies=%s content_sha256=%s superseded_by=%s'
            % (r['period'], r['version'], r['status'],
               'yes' if r['sealed_at'] else 'NO',
               counts.get('fin_recon_work_row', '-'),
               counts.get('fin_recon_operator_row', '-'),
               counts.get('fin_recon_anomaly', '-'), r['content_sha256'],
               r['superseded_by'] or '-'))
    out('RESULT=PASS status snapshots=%d' % len(rows))
    return EXIT_OK


def build_parser():
    # [REASON]: allow_abbrev=False -- флаг нельзя сократить. Сокращённое
    # `--pack` молча читалось бы как `--package`, и опечатка в ранбуке
    # превратилась бы в другой смысл без единой ошибки.
    parser = argparse.ArgumentParser(
        prog='fin_recon_storage.py', allow_abbrev=False,
        description='FIN-RECON-STORAGE-001 snapshot package tool.')
    sub = parser.add_subparsers(dest='command')

    def add(name):
        return sub.add_parser(name, allow_abbrev=False)

    p = add('check-package')
    p.add_argument('--package', required=True)
    p.add_argument('--expect')
    p = add('import')
    p.add_argument('--db', required=True)
    p.add_argument('--package', required=True)
    p.add_argument('--imported-by')
    p = add('verify')
    p.add_argument('--db', required=True)
    p.add_argument('--package', required=True)
    p.add_argument('--expect')
    p = add('restore-test')
    p.add_argument('--package', required=True)
    p.add_argument('--workdir', required=True)
    p.add_argument('--expect')
    p = add('status')
    p.add_argument('--db', required=True)
    return parser


COMMANDS = {'check-package': cmd_check_package, 'import': cmd_import,
            'verify': cmd_verify, 'restore-test': cmd_restore_test,
            'status': cmd_status}


def main(argv=None):
    parser = build_parser()
    try:
        args = parser.parse_args(argv)
    except SystemExit as exc:
        return EXIT_USAGE if exc.code else EXIT_OK
    if not args.command:
        parser.print_usage()
        return EXIT_USAGE
    try:
        return COMMANDS[args.command](args)
    except Exception as exc:  # noqa: BLE001 -- the console gets a code, not a trace
        return fail(EXIT_UNEXPECTED, '%s: %s' % (type(exc).__name__, exc))


if __name__ == '__main__':
    sys.exit(main())
