#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""FIN-RECON-003: READ-ONLY row fingerprints for the keys of a signed act.

    python tools/drone_works_fingerprint_export.py --dir BOOKS --manifest MANIFEST
        --db instance/transport.db --keys ACT_KEYS.csv --out PRIVATE_EXPORT.csv

Input: the five pinned original July books and their manifest, the database
(opened file:...?mode=ro, PRAGMA query_only) and a PRIVATE CSV with exactly
one column, ``source_key`` -- the rows the authorized person accepted in the
signed act. Output: a PRIVATE CSV

    source_key,row_sha256,area_ha,amount,resolved_customer_id,
    resolved_operator_id,payer_unidentified,diagnostics

and an ASCII summary on stdout: counts, totals, code blobs and SHA-256
digests, no names. Only the summary may leave the server; the export holds
commercial figures and stays with the owner (never GitHub).

The fingerprint is drone_works_selective.row_fingerprint() of the row after
the SAME parse and resolution that ``drone_works_selective --apply`` performs,
so it stays valid only while the books, the customer/operator directory and
this code are unchanged -- the guarded CLI recomputes it before writing.

This tool never writes the database, never creates cards or aliases, and
never produces the approved two-column allowlist: a refused key gets no
fingerprint, and the person who signed the act builds the allowlist from the
OK rows; the owner pins that file's SHA-256 separately.
"""
import argparse
import csv
import hashlib
import io
import os
import sqlite3
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)

import drone_works_selective as sel  # noqa: E402

COLUMNS = ('source_key', 'row_sha256', 'area_ha', 'amount',
           'resolved_customer_id', 'resolved_operator_id',
           'payer_unidentified', 'diagnostics')
MAX_KEYS = 328
REPO_ROOT = os.path.dirname(HERE)
CODE_FILES = ('import_drone_works.py', 'drone_works_selective.py',
              'drone_works_fingerprint_export.py')


class ExportError(ValueError):
    """Nothing is exported; the database was not written either way."""


def read_keys(path):
    """The act's keys: one UTF-8 column `source_key`, unique, well formed."""
    with open(path, 'rb') as stream:
        raw = stream.read(sel.MAX_ALLOWLIST_BYTES + 1)
    if len(raw) > sel.MAX_ALLOWLIST_BYTES:
        raise ExportError('KEYS_FILE_TOO_LARGE')
    try:
        reader = csv.reader(io.StringIO(raw.decode('utf-8-sig'), newline=''))
        rows = list(reader)
    except (UnicodeError, csv.Error) as exc:
        raise ExportError('KEYS_FILE_NOT_UTF8_CSV') from exc
    if not rows or rows[0] != ['source_key']:
        raise ExportError('KEYS_COLUMNS: required exactly source_key')
    keys = []
    for lineno, record in enumerate(rows[1:], 2):
        if len(record) != 1 or not record[0]:
            raise ExportError('INVALID_KEYS_ROW: %d' % lineno)
        key = record[0]
        if (key != key.strip() or key.count('!') != 2
                or not key.rsplit('!', 1)[-1].isdigit()):
            raise ExportError('INVALID_SOURCE_KEY: %d' % lineno)
        if key in keys:
            raise ExportError('DUPLICATE_KEY: %d' % lineno)
        keys.append(key)
    if not keys:
        raise ExportError('EMPTY_KEYS_FILE')
    if len(keys) > MAX_KEYS:
        raise ExportError('KEYS_EXCEED_JULY_328')
    return keys, hashlib.sha256(raw).hexdigest()


def check_out_path(path):
    full = os.path.abspath(path)
    # [REASON]: the export carries per-row commercial figures and the repo
    # is public; a file written inside the working copy is one `git add`
    # away from GitHub. Refused, not warned.
    if os.path.commonpath([full, REPO_ROOT]) == REPO_ROOT:
        raise ExportError('OUT_INSIDE_REPOSITORY')
    if os.path.exists(full):
        raise ExportError('OUT_ALREADY_EXISTS')
    if not os.path.isdir(os.path.dirname(full)):
        raise ExportError('OUT_DIRECTORY_NOT_FOUND')
    return full


def git_blob(path):
    """Git's blob id of the file as committed (CRLF folded to LF)."""
    with open(path, 'rb') as stream:
        data = stream.read().replace(b'\r\n', b'\n')
    return hashlib.sha1(b'blob %d\0' % len(data) + data).hexdigest()


def directory_facts(con):
    n = lambda sql: con.execute(sql).fetchone()[0]
    lines = ['c\t%s\t%s\t%s\t%s' % r for r in con.execute(
        'SELECT id, name, customer_type, is_active FROM drone_customers')]
    lines += ['a\t%s\t%s\t%s' % r for r in con.execute(
        'SELECT normalized_key, drone_customer_id, is_active '
        'FROM drone_customer_aliases')]
    digest = hashlib.sha256('\n'.join(sorted(lines)).encode('utf-8')).hexdigest()
    return (('DB_CUSTOMERS', n('SELECT COUNT(*) FROM drone_customers')),
            ('DB_MAX_CUSTOMER_ID', n('SELECT MAX(id) FROM drone_customers')),
            ('DB_ALIASES', n('SELECT COUNT(*) FROM drone_customer_aliases')),
            ('DB_WORKS', n('SELECT COUNT(*) FROM drone_works')),
            ('DIRECTORY_SHA256', digest))


def diagnose(row, existing):
    """Every reason a parsed, resolved row must not be signed yet, joined
    by '+'; 'OK' if there is none. All of them, so that one decision does
    not uncover the next only on the following run."""
    reasons = []
    if row.get('period_month') != sel.PERIOD:
        reasons.append('NOT_JULY_PERIOD')
    if (row['source_file'], row['source_sheet'], row['source_row']) in existing:
        reasons.append('ALREADY_IN_DB')
    if not row.get('customer_raw'):
        reasons.append('EMPTY_CUSTOMER_REQUIRES_DECISION')
    elif row.get('payer_decision_pending'):
        reasons.append('PAYER_DECISION_PENDING')
    elif row.get('drone_customer_id') is None and not row.get('payer_unidentified'):
        reasons.append('NO_CUSTOMER_CARD')
    if row.get('date_kind') in ('none', 'unparsed'):
        reasons.append('DATE_REQUIRES_DECISION')
    if row.get('drone_operator_id') is None:
        reasons.append('UNRESOLVED_OPERATOR')
    return '+'.join(reasons) or 'OK'


def export(args, imp):
    keys, keys_sha = read_keys(args.keys)
    out_path = check_out_path(args.out)
    if not os.path.isdir(args.dir):
        raise ExportError('BOOK_DIRECTORY_NOT_FOUND')
    if not os.path.isfile(args.manifest):
        raise ExportError('MANIFEST_NOT_FOUND')
    manifest, manifest_errors = imp.read_manifest(args.manifest)
    if manifest_errors:
        raise ExportError('MANIFEST_HAS_ERRORS')
    sel.verify_books(args.dir, manifest)
    con = imp._connect(args.db, read_only=True)
    try:
        con.execute('PRAGMA query_only=ON')
        imp._require_tables(con, ('drone_works', 'drone_customers',
                                  'drone_customer_aliases', 'drone_operators'))
        imp._require_received_kind(con)
        imp._require_payer_unidentified(con)
        facts = directory_facts(con)
        directory = imp.Directory(con)
        result = imp.collect(args.dir, manifest, sorted(manifest), directory, None)
        if result.unparsed:
            raise ExportError('UNPARSED_JULY_ROWS: %d' % len(result.unparsed))
        available = {}
        for row in result.rows:
            if sel.protected(row):
                key = sel.source_key(row)
                if key in available:
                    raise ExportError('DUPLICATE_PARSED_SOURCE_KEY: %s' % key)
                available[key] = row
        # [REASON]: R02 is decided before anything else, so it can never get
        # a fingerprint even if a future book carried the same address.
        wanted = [k for k in keys if k not in sel.EXCLUDED_SOURCE_KEYS
                  and k in available]
        result.rows = [available[k] for k in wanted]
        imp.resolve(result, directory)
        if imp.filename_alias_conflicts(con, result.rows):
            raise ExportError('UNSAFE_FILENAME_ALIASES')
        existing = directory.existing_provenance
    finally:
        con.close()

    records, totals, refused = [], [0, 0.0, 0.0], {}
    for key in keys:
        row = available.get(key)
        resolved = row is not None and key not in sel.EXCLUDED_SOURCE_KEYS
        if key in sel.EXCLUDED_SOURCE_KEYS:
            verdict = 'EXCLUDED_SOURCE_KEY'
        elif row is None:
            verdict = 'NOT_PARSED'
        else:
            verdict = diagnose(row, existing)
        if verdict == 'OK':
            totals[0] += 1
            totals[1] += float(row['area_ha'] or 0)
            totals[2] += float(row['amount'] or 0)
        else:
            for reason in verdict.split('+'):
                refused[reason] = refused.get(reason, 0) + 1
        records.append((
            key,
            sel.row_fingerprint(row) if verdict == 'OK' else '',
            '' if row is None else '%.2f' % float(row['area_ha'] or 0),
            '' if row is None else '%.2f' % float(row['amount'] or 0),
            '' if not resolved or row.get('drone_customer_id') is None
            else str(row['drone_customer_id']),
            '' if not resolved or row.get('drone_operator_id') is None
            else str(row['drone_operator_id']),
            '' if not resolved else ('1' if row.get('payer_unidentified') else '0'),
            verdict))

    buffer = io.StringIO(newline='')
    writer = csv.writer(buffer, lineterminator='\r\n')
    writer.writerow(COLUMNS)
    writer.writerows(records)
    data = buffer.getvalue().encode('utf-8-sig')
    with open(out_path, 'xb') as stream:
        stream.write(data)
    refused_rows = sum(1 for r in records if r[-1] != 'OK')
    return keys_sha, facts, totals, refused, refused_rows, hashlib.sha256(data).hexdigest()


def main(argv=None):
    parser = argparse.ArgumentParser(
        description='FIN-RECON-003: read-only row fingerprints (NO IMPORT GO)')
    parser.add_argument('--dir', required=True)
    parser.add_argument('--manifest', required=True)
    parser.add_argument('--db', required=True)
    parser.add_argument('--keys', required=True,
                        help='PRIVATE CSV, one column source_key, from the signed act')
    parser.add_argument('--out', required=True,
                        help='PRIVATE CSV to create; outside the repository')
    args = parser.parse_args(argv)
    import import_drone_works as imp
    print('EXPORT=FIN_RECON_003_ROW_FINGERPRINTS_READONLY')
    for name in CODE_FILES:
        print('CODE_BLOB %s=%s' % (name, git_blob(os.path.join(HERE, name))))
    try:
        keys_sha, facts, totals, refused, refused_rows, out_sha = export(args, imp)
    except (ExportError, sel.SelectionError, imp.ImportPreconditionError,
            OSError, ValueError, sqlite3.Error) as exc:
        print('ERROR: %s; NOTHING EXPORTED' % imp._ascii(exc))
        print('IMPORT_AUTHORIZED=NO')
        return 2
    print('ORIGINAL_BOOK_HASHES=PASS (5)')
    print('DB_OPENED=mode=ro query_only')
    for key, value in facts:
        print('%s=%s' % (key, value))
    print('KEYS_FILE_SHA256=%s' % keys_sha)
    print('KEYS=%d' % (totals[0] + refused_rows))
    print('OK_ROWS=%d OK_AREA_HA=%.2f OK_AMOUNT_UZS=%.2f' % tuple(totals))
    print('REFUSED_ROWS=%d' % refused_rows)
    for code in sorted(refused):
        print('REFUSED %s=%d' % (code, refused[code]))
    print('EXPORT_SHA256=%s' % out_sha)
    print('RESULT=%s' % ('EXPORT_WRITTEN_ALL_OK' if not refused
                         else 'EXPORT_WRITTEN_WITH_REFUSED_KEYS'))
    print('IMPORT_AUTHORIZED=NO (fingerprints pin data; the signed act authorizes)')
    return 0 if not refused else 3


if __name__ == '__main__':
    sys.exit(main())
