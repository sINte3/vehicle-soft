#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""FIN-RECON-003: exact-source-key, row-fingerprint guarded July import.

A SHA-256 is an integrity pin, NOT proof that the owner approved the rows.
The owner's signed decision is a separate precondition outside this tool.
No credentials, customer names or approved row list are committed here.
"""
import argparse
import csv
import datetime
import hashlib
import hmac
import io
import json
import os
import re
import sqlite3
import sys

# These are the five immutable ORIGINAL July books, not summary books.
JULY_BOOK_SHA256 = {
    'Дрон Ғиждувон ПТЗ Июль.xlsx': '7ea4867901a522761ffbc6ccf93c36249ee7d685658bb67e02d599fb029c2349',
    'ИЮЛЬ Агрокластер.xlsx': '7ffd1e5ebeb55469292374725838d6b4c02706c5a93339ea2b6bb05078fa2510',
    'Когон ПТЗ ИЮЛЬ.xlsx': 'e609ecc74ff7d68d38d3b605c069b79693a322d9a11becd5356fbb2f05887194',
    'Сервис Дрон маълумот Июль.xlsx': '5740e08d3f5dddd4ab3bb8d5f856d492ad444bc3f75cd2e36064bfcbc4f29fb3',
    'Шофиркон ПТЗ Дрон маълумот Июль.xlsx': 'ca547ab75525d70ada9bbcc946bfa466d953193c3a5112c4c295152ec0e2bd9e',
}

PERIOD = '2026-07'
MAX_ALLOWLIST_BYTES = 1024 * 1024
# [REASON]: FIN-RECON-003 R02, confirmed 05.11.2026: this physical row is a
# second entry of the work recorded on the previous row of the same sheet
# (same farm, same area and amount, the next day). It is EXCLUDED, not held:
# no allowlist may ever carry it, whatever its row hash. A hash proves only
# that the bytes did not change, so a list built by mistake from the whole
# cohort would otherwise write the duplicate. Grows only by a confirmed
# decision and a test, like GENERIC_PAYER_KEYS in the importer.
EXCLUDED_SOURCE_KEYS = frozenset((
    'Дрон Ғиждувон ПТЗ Июль.xlsx!свод ичи (Усмон)!11',
))
HASH_RE = re.compile(r'^[0-9a-f]{64}$')
# A row hash pins the facts that would be written, not just its Excel address.
ROW_FIELDS = (
    'source_file', 'source_sheet', 'source_row', 'period_month',
    'work_date_from', 'work_date_to', 'date_raw', 'date_kind',
    'operator_raw', 'customer_raw', 'subdivision_name',
    'area_ha', 'price_per_ha', 'amount', 'other_costs',
    'received_amount', 'received_kind', 'payment_type', 'note',
    'drone_operator_id', 'drone_customer_id', 'payer_unidentified',
)


class SelectionError(ValueError):
    """Unsafe or unapproved selection; no database writes allowed."""


def source_key(row):
    return '%s!%s!%d' % (
        row['source_file'], row['source_sheet'], int(row['source_row']))


def protected(row):
    """Known original book provenance always stays protected."""
    return row.get('source_file') in JULY_BOOK_SHA256


def july_work(row):
    """Protect July-2026 WORKS by their actual dates, not an Excel wrapper.

    A renamed/repacked XLSX has different bytes and name, but keeps
    its work dates and derived period_month. Checking every accepted row
    at the shared pre-write boundary covers both legacy CLI and web upload.
    """
    if row.get('period_month') == PERIOD:
        return True
    for field in ('work_date_from', 'work_date_to'):
        value = row.get(field)
        if isinstance(value, (datetime.date, datetime.datetime)):
            if value.year == 2026 and value.month == 7:
                return True
        elif isinstance(value, str) and value.startswith('2026-07'):
            return True
    return False


def _canonical(value):
    if isinstance(value, (datetime.date, datetime.datetime)):
        return value.isoformat()
    if isinstance(value, (int, float, str, bool)) or value is None:
        return value
    raise SelectionError('Unsupported row fingerprint field type: %s'
                         % type(value).__name__)


def row_fingerprint(row):
    payload = {name: _canonical(row.get(name)) for name in ROW_FIELDS}
    encoded = json.dumps(payload, ensure_ascii=False, sort_keys=True,
                         separators=(',', ':'), allow_nan=False).encode('utf-8')
    return hashlib.sha256(encoded).hexdigest()


def _sha256_file(path):
    digest = hashlib.sha256()
    with open(path, 'rb') as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b''):
            digest.update(block)
    return digest.hexdigest()


def verify_books(directory, manifest):
    """Require exactly the five approved ORIGINAL files and their bytes."""
    expected = set(JULY_BOOK_SHA256)
    actual = {n for n in os.listdir(directory)
              if n.lower().endswith('.xlsx') and not n.startswith('~$')}
    if actual != expected or set(manifest) != expected:
        raise SelectionError(
            'BOOK_SET_MISMATCH: exactly five pinned July books and five '
            'manifest entries are required; missing=%d extra=%d'
            % (len(expected - actual), len(actual - expected)))
    if any(manifest[name] != PERIOD for name in expected):
        raise SelectionError('MANIFEST_MONTH_MISMATCH: must be 2026-07')
    for name, expected_hash in sorted(JULY_BOOK_SHA256.items()):
        path = os.path.join(directory, name)
        if not os.path.isfile(path) or os.path.islink(path):
            raise SelectionError('BOOK_NOT_REGULAR_FILE: %s' % name)
        if not hmac.compare_digest(_sha256_file(path), expected_hash):
            raise SelectionError('ORIGINAL_BOOK_SHA256_MISMATCH: %s' % name)


def load_allowlist(path, pinned_sha256):
    """Return {source_key: row_hash} from a byte-pinned two-column CSV."""
    expected = pinned_sha256.lower()
    if not HASH_RE.fullmatch(expected):
        raise SelectionError('INVALID_ALLOWLIST_SHA256')
    with open(path, 'rb') as stream:
        raw = stream.read(MAX_ALLOWLIST_BYTES + 1)
    if len(raw) > MAX_ALLOWLIST_BYTES:
        raise SelectionError('ALLOWLIST_TOO_LARGE')
    if not hmac.compare_digest(hashlib.sha256(raw).hexdigest(), expected):
        raise SelectionError('ALLOWLIST_SHA256_MISMATCH')
    try:
        decoded = raw.decode('utf-8-sig')
        reader = csv.DictReader(io.StringIO(decoded, newline=''))
        if reader.fieldnames != ['source_key', 'row_sha256']:
            raise SelectionError(
                'ALLOWLIST_COLUMNS: required source_key,row_sha256')
        rows = {}
        for lineno, record in enumerate(reader, 2):
            if (None in record or not record.get('source_key')
                    or not record.get('row_sha256')):
                raise SelectionError('INVALID_ALLOWLIST_ROW: %d' % lineno)
            key = record['source_key']
            fingerprint = record['row_sha256']
            if (key != key.strip() or key.count('!') != 2
                    or not key.rsplit('!', 1)[-1].isdigit()
                    or not HASH_RE.fullmatch(fingerprint)):
                raise SelectionError('INVALID_KEY_OR_ROW_HASH: %d' % lineno)
            if key in rows:
                raise SelectionError('DUPLICATE_ALLOWLIST_KEY: %d' % lineno)
            if key in EXCLUDED_SOURCE_KEYS:
                raise SelectionError('EXCLUDED_SOURCE_KEY: %d' % lineno)
            rows[key] = fingerprint
        if not rows:
            raise SelectionError('EMPTY_ALLOWLIST')
        if len(rows) > 328:
            raise SelectionError('ALLOWLIST_EXCEEDS_JULY_328')
        return rows
    except UnicodeError as exc:
        raise SelectionError('ALLOWLIST_NOT_UTF8') from exc
    except csv.Error as exc:
        raise SelectionError('ALLOWLIST_INVALID_CSV') from exc


def select_rows(full_rows, approved, check_hashes=True):
    """Require 1:1 identity; optionally defer hashes until AFTER resolution."""
    available = {}
    for row in full_rows:
        if not protected(row):
            continue
        key = source_key(row)
        if key in available:
            raise SelectionError('DUPLICATE_PARSED_SOURCE_KEY: %s' % key)
        available[key] = row
    unknown = sorted(set(approved) - set(available))
    if unknown:
        raise SelectionError('APPROVED_KEYS_NOT_PARSED: %d, first=%s'
                             % (len(unknown), unknown[0]))
    selected = [row for row in full_rows if source_key(row) in approved]
    if check_hashes:
        validate_row_hashes(selected, approved)
    return selected


def validate_row_hashes(rows, approved):
    """Hard gate for apply_rows, including callers other than this CLI."""
    if approved is None:
        if any(july_work(row) for row in rows):
            raise SelectionError('JULY_2026_WORK_REQUIRES_ALLOWLIST')
        if any(protected(row) for row in rows):
            raise SelectionError('JULY_ALLOWLIST_REQUIRED')
        return
    if not isinstance(approved, dict) or not approved:
        raise SelectionError('ALLOWLIST_EMPTY_OR_INVALID')
    # Checked here as well as in load_allowlist(): apply_rows() callers
    # other than the CLI pass a dict that never went through the CSV reader.
    excluded = sorted(EXCLUDED_SOURCE_KEYS & set(approved))
    if excluded:
        raise SelectionError('EXCLUDED_SOURCE_KEY: %s' % excluded[0])
    keys = [source_key(row) for row in rows]
    if len(keys) != len(set(keys)):
        raise SelectionError('DUPLICATE_SELECTED_SOURCE_KEY')
    if set(keys) != set(approved):
        raise SelectionError('SELECTED_KEYS_DIFFER_FROM_ALLOWLIST')
    for row in rows:
        key = source_key(row)
        if not protected(row) or row.get('period_month') != PERIOD:
            raise SelectionError('NON_JULY_SOURCE_IN_ALLOWLIST: %s' % key)
        expected = approved[key]
        if not HASH_RE.fullmatch(expected):
            raise SelectionError('INVALID_ROW_HASH: %s' % key)
        if not hmac.compare_digest(row_fingerprint(row), expected):
            raise SelectionError('ROW_FINGERPRINT_MISMATCH: %s' % key)


def verify_apply_preconditions(result, approved):
    """Check *before* BEGIN, and refuse all unsanctioned customer creation.

    Legacy callers cannot evade the July guard by changing the manifest
    month, or by renaming an unmodified original XLSX. collect() records
    source-book digests independently of the dispatcher-provided filename.
    """
    source_hashes = getattr(result, 'source_book_hashes', None) or {}
    pinned_hashes = set(JULY_BOOK_SHA256.values())
    if approved is None and any(
            h in pinned_hashes for h in source_hashes.values()):
        raise SelectionError('ORIGINAL_JULY_BOOK_BYTES_REQUIRE_ALLOWLIST')
    validate_row_hashes(result.rows, approved)
    if approved is None:
        return
    if getattr(result, 'created_customers', None):
        raise SelectionError(
            'UNRESOLVED_CUSTOMERS: no auto-created cards or aliases allowed')
    if getattr(result, 'unresolved_operators', None):
        raise SelectionError('UNRESOLVED_OPERATORS')
    if getattr(result, 'already_in_db', 0):
        raise SelectionError('SELECTED_WORK_ALREADY_IN_DB')
    if getattr(result, 'manifest_problems', None):
        raise SelectionError('MANIFEST_HAS_ERRORS')
    if getattr(result, 'files_skipped_no_manifest', None):
        raise SelectionError('FILES_SKIPPED_NO_MANIFEST')
    if getattr(result, 'filename_alias_conflicts', None):
        raise SelectionError('UNSAFE_FILENAME_ALIASES')
    for row in result.rows:
        if not row.get('customer_raw'):
            raise SelectionError('EMPTY_CUSTOMER_REQUIRES_DECISION: '
                                 + source_key(row))
        if row.get('date_kind') in ('none', 'unparsed'):
            raise SelectionError('DATE_REQUIRES_DECISION: ' + source_key(row))


def cli(argv=None):
    """Separate, guarded CLI; never reuses the unrestricted legacy --apply."""
    parser = argparse.ArgumentParser(
        description='FIN-RECON-003: pinned, selective July import (NO IMPORT GO)')
    parser.add_argument('--dir', required=True)
    parser.add_argument('--manifest', required=True)
    parser.add_argument('--db', required=True)
    parser.add_argument('--only-keys', required=True,
                        help='PRIVATE CSV: source_key,row_sha256')
    parser.add_argument('--only-keys-sha256', required=True,
                        help='SHA-256 of exact CSV bytes, separately approved')
    parser.add_argument('--apply', action='store_true',
                        help='WRITE ONLY after separate owner/release approval')
    parser.add_argument('--batch', default=None)
    args = parser.parse_args(argv)

    # [REASON]: under `python -I tools/drone_works_selective.py` isolated
    # mode leaves the script's folder off sys.path, and the import below died
    # with a traceback (exit 1) before any check ran. Added here, in the CLI
    # only: the web app imports this module as tools.* and must not get
    # tools/ on its path.
    here = os.path.dirname(os.path.abspath(__file__))
    if here not in sys.path:
        sys.path.insert(0, here)
    # Lazy import avoids a circular dependency when the production importer
    # calls verify_apply_preconditions().
    import import_drone_works as imp

    try:
        if not os.path.isdir(args.dir):
            raise SelectionError('BOOK_DIRECTORY_NOT_FOUND')
        if not os.path.isfile(args.manifest):
            raise SelectionError('MANIFEST_NOT_FOUND')
        manifest, manifest_errors = imp.read_manifest(args.manifest)
        if manifest_errors:
            raise SelectionError('MANIFEST_HAS_ERRORS')
        verify_books(args.dir, manifest)
        approved = load_allowlist(args.only_keys, args.only_keys_sha256)
        con = imp._connect(args.db, read_only=not args.apply)
        try:
            imp._require_tables(con, ('drone_works', 'drone_customers',
                                      'drone_customer_aliases', 'drone_operators'))
            imp._require_received_kind(con)
            imp._require_payer_unidentified(con)
            directory = imp.Directory(con)
            all_rows = imp.collect(args.dir, manifest, sorted(manifest),
                                   directory, None)
            if all_rows.unparsed:
                raise SelectionError('UNPARSED_JULY_ROWS: %d'
                                     % len(all_rows.unparsed))
            # Select exact provenance first, then resolve customer/operator
            # IDs; the signed row hash also pins the resolved IDs. It is
            # checked by verify_apply_preconditions() after resolve().
            all_rows.rows = select_rows(all_rows.rows, approved,
                                        check_hashes=False)
            all_rows.manifest_problems = manifest_errors
            imp.resolve(all_rows, directory)
            all_rows.filename_alias_conflicts = (
                imp.filename_alias_conflicts(con, all_rows.rows))
            verify_apply_preconditions(all_rows, approved)
            blockers = imp.apply_blockers(all_rows, approved_rows=approved)
            if blockers:
                raise SelectionError('APPLY_GATE: ' + '; '.join(blockers))
            stats = imp.summarize(all_rows)
            print('ALLOWLIST_SHA256=PASS')
            print('ORIGINAL_BOOK_HASHES=PASS (5)')
            print('SOURCE_KEYS_AND_ROW_DIGESTS=PASS (%d)' % len(approved))
            print('ROWS=%d AREA_HA=%.2f AMOUNT_UZS=%.2f'
                  % (stats['rows'], stats['area'], stats['amount']))
            print('CREATED_CUSTOMERS=0')
            if args.apply:
                try:
                    created, inserted, skipped = imp.apply_rows(
                        con, all_rows,
                        args.batch or ('july_approved_%s' %
                                       datetime.datetime.now().strftime('%Y%m%d_%H%M%S')),
                        approved_rows=approved)
                except BaseException:
                    # [REASON]: apply_rows() leaves its open transaction to
                    # the caller, and the legacy CLI and the upload screen
                    # both roll back explicitly. Relying on close() alone
                    # would make "nothing written" depend on SQLite's
                    # behaviour at close instead of on this code.
                    if con.in_transaction:
                        con.rollback()
                    raise
                print('RESULT=APPROVED_SUBSET_APPLIED created=%d inserted=%d skipped=%d'
                      % (created, inserted, skipped))
            else:
                print('RESULT=READONLY_SUBSET_TECHNICAL_GATE_OK')
                print('OWNER_APPROVAL_AND_RELEASE_GATE_STILL_REQUIRED')
        finally:
            con.close()
        return 0
    except (SelectionError, imp.ImportPreconditionError,
            OSError, ValueError, sqlite3.Error) as exc:
        # Windows redirection commonly uses cp1251. The filename from an
        # SHA256 mismatch may contain Ғ, which cp1251 cannot encode.
        # A refusal must always be printed safely with exit status 2.
        print('ERROR: %s; NO IMPORT' % imp._ascii(exc))
        return 2


if __name__ == '__main__':
    sys.exit(cli())
