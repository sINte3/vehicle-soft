#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""FIN-RECON-003 selective import negative controls. No real DB or books."""
import csv
import datetime
import hashlib
import io
import os
import sqlite3
import sys
import tempfile
import unittest
from unittest import mock

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)

import drone_works_selective as sel
import import_drone_works as imp


def row(n=12):
    # [REASON]: a synthetic row. Row 11 of this sheet is the real R02
    # duplicate, which EXCLUDED_SOURCE_KEYS refuses in every allowlist; the
    # fixture used to sit on it and the positive test wrote it.
    return {
        'source_file': 'Дрон Ғиждувон ПТЗ Июль.xlsx',
        'source_sheet': 'свод ичи (Усмон)',
        'source_row': n,
        'period_month': '2026-07',
        'work_date_from': datetime.date(2026, 7, 11),
        'work_date_to': datetime.date(2026, 7, 11),
        'date_raw': '2026-07-11', 'date_kind': 'date',
        'operator_raw': 'Усмон', 'customer_raw': 'Гарден',
        'subdivision_name': 'Гиждувон', 'area_ha': 13.53,
        'price_per_ha': 85633.0, 'amount': 1158614.49,
        'other_costs': 0.0, 'received_amount': 0.0,
        'received_kind': 'received', 'payment_type': 'transfer',
        'note': 'treatment 2',
    }


def _directory(db):
    con = imp._connect(db, read_only=True)
    try:
        # Importer Directory caches the directory mapping in memory.
        return imp.Directory(con)
    finally:
        con.close()


def approved(rows):
    return {sel.source_key(r): sel.row_fingerprint(r) for r in rows}


def run_result(rows, new_cards=None):
    result = imp.RunResult()
    result.rows = rows
    result.created_customers = new_cards or []
    return result


def allow_csv(rows):
    mem = io.StringIO(newline='')
    writer = csv.writer(mem, lineterminator='\n')
    writer.writerow(('source_key', 'row_sha256'))
    for k, v in rows:
        writer.writerow((k, v))
    return mem.getvalue().encode('utf-8')


class SelectiveImportTests(unittest.TestCase):
    def test_source_key_uses_physical_excel_row(self):
        self.assertEqual(sel.source_key(row()),
                         'Дрон Ғиждувон ПТЗ Июль.xlsx!свод ичи (Усмон)!12')

    def test_row_hash_pins_financial_date_customer_and_channel(self):
        original = row()
        digest = sel.row_fingerprint(original)
        for field, changed in (
                ('area_ha', 13.54), ('amount', 1158614.50),
                ('received_amount', 10), ('price_per_ha', 90000),
                ('customer_raw', 'Иное хозяйство'),
                ('operator_raw', 'Другой оператор'),
                ('payment_type', 'cash'),
                ('work_date_from', datetime.date(2026, 7, 12)),
                ('note', 'changed note')):
            changed_row = dict(original, **{field: changed})
            with self.subTest(field=field):
                self.assertNotEqual(sel.row_fingerprint(changed_row), digest)

    def test_resolved_alias_identity_drift_is_a_hard_refusal(self):
        original = dict(row(), drone_operator_id=14,
                        drone_customer_id=155, payer_unidentified=False)
        signed = approved([original])
        for field, changed in (
                ('drone_customer_id', 903),
                ('drone_operator_id', 13),
                ('payer_unidentified', True)):
            with self.subTest(field=field):
                altered = dict(original, **{field: changed})
                self.assertEqual(
                    len(sel.select_rows([altered], signed, check_hashes=False)),
                    1)
                with self.assertRaisesRegex(sel.SelectionError,
                                            'ROW_FINGERPRINT_MISMATCH'):
                    sel.verify_apply_preconditions(
                        run_result([altered]), signed)

    def test_exact_list_pinned_by_bytes_and_duplicate_rejected(self):
        a = approved([row()])
        data = allow_csv(list(a.items()))
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, 'keys.csv')
            with open(path, 'wb') as f:
                f.write(data)
            self.assertEqual(sel.load_allowlist(
                path, hashlib.sha256(data).hexdigest()), a)
            with self.assertRaisesRegex(sel.SelectionError,
                                        'ALLOWLIST_SHA256_MISMATCH'):
                sel.load_allowlist(path, '0' * 64)
            duplicate = allow_csv(list(a.items()) * 2)
            with open(path, 'wb') as f:
                f.write(duplicate)
            with self.assertRaisesRegex(sel.SelectionError,
                                        'DUPLICATE_ALLOWLIST_KEY'):
                sel.load_allowlist(path, hashlib.sha256(duplicate).hexdigest())

    def test_empty_bad_header_and_extra_column_rejected(self):
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, 'keys.csv')
            for data, error in (
                    (b'source_key,row_sha256\n', 'EMPTY_ALLOWLIST'),
                    (b'key,hash\none,two\n', 'ALLOWLIST_COLUMNS'),
                    (b'source_key,row_sha256\nx,y,z\n',
                     'INVALID_ALLOWLIST_ROW')):
                with self.subTest(error=error):
                    with open(path, 'wb') as f:
                        f.write(data)
                    with self.assertRaisesRegex(sel.SelectionError, error):
                        sel.load_allowlist(
                            path, hashlib.sha256(data).hexdigest())

    def test_select_exact_subset_and_no_silent_missing(self):
        a, b = row(12), row(35)
        selection = approved([a])
        self.assertEqual([sel.source_key(r) for r in sel.select_rows(
            [a, b], selection)], [sel.source_key(a)])
        with self.assertRaisesRegex(sel.SelectionError,
                                    'APPROVED_KEYS_NOT_PARSED'):
            sel.select_rows([b], selection)

    def test_selected_row_change_refused(self):
        a = row()
        selection = approved([a])
        changed = dict(a, area_ha=13.54)
        with self.assertRaisesRegex(sel.SelectionError,
                                    'ROW_FINGERPRINT_MISMATCH'):
            sel.select_rows([changed], selection)

    def test_duplicate_source_key_in_parsed_rows_refused(self):
        a = row()
        with self.assertRaisesRegex(sel.SelectionError,
                                    'DUPLICATE_PARSED_SOURCE_KEY'):
            sel.select_rows([a, dict(a)], approved([a]))

    def test_all_book_hashes_and_manifest_are_mandatory(self):
        with tempfile.TemporaryDirectory() as d:
            hashes = {}
            for i, name in enumerate(sel.JULY_BOOK_SHA256):
                content = ('test book %d' % i).encode()
                with open(os.path.join(d, name), 'wb') as f:
                    f.write(content)
                hashes[name] = hashlib.sha256(content).hexdigest()
            manifest = {name: sel.PERIOD for name in hashes}
            with mock.patch.dict(sel.JULY_BOOK_SHA256, hashes, clear=True):
                sel.verify_books(d, manifest)
                with self.assertRaisesRegex(sel.SelectionError,
                                            'MANIFEST_MONTH_MISMATCH'):
                    sel.verify_books(
                        d, dict(manifest, **{next(iter(manifest)): '2026-06'}))
                with open(os.path.join(d, next(iter(manifest))), 'ab') as f:
                    f.write(b'!')
                with self.assertRaisesRegex(sel.SelectionError,
                                            'ORIGINAL_BOOK_SHA256_MISMATCH'):
                    sel.verify_books(d, manifest)
                os.unlink(os.path.join(d, next(iter(manifest))))
                with self.assertRaisesRegex(sel.SelectionError,
                                            'BOOK_SET_MISMATCH'):
                    sel.verify_books(d, manifest)

    def test_unrestricted_legacy_apply_refused_before_begin(self):
        r = run_result([row()])
        con = sqlite3.connect(':memory:')
        try:
            self.assertFalse(con.in_transaction)
            with self.assertRaisesRegex(imp.ImportPreconditionError,
                                        'JULY_2026_WORK_REQUIRES_ALLOWLIST'):
                imp.apply_rows(con, r, 'test')
            self.assertFalse(con.in_transaction)
        finally:
            con.close()

    def test_unrelated_old_import_not_blocked_by_july_guard(self):
        r = run_result([dict(
            row(), source_file='Older.xlsx',
            period_month='2026-06',
            work_date_from=datetime.date(2026, 6, 11),
            work_date_to=datetime.date(2026, 6, 11),
            date_raw='2026-06-11')])
        self.assertFalse(imp.apply_blockers(r))

    def test_approved_list_must_match_all_rows_before_begin(self):
        a, b = row(12), row(35)
        r = run_result([a, b])
        con = sqlite3.connect(':memory:')
        try:
            with self.assertRaisesRegex(imp.ImportPreconditionError,
                                        'SELECTED_KEYS_DIFFER_FROM_ALLOWLIST'):
                imp.apply_rows(con, r, 'test', approved_rows=approved([a]))
            self.assertFalse(con.in_transaction)
        finally:
            con.close()

    def test_edited_amount_refused_before_begin(self):
        a = row()
        original_list = approved([a])
        r = run_result([dict(a, amount=1.0)])
        con = sqlite3.connect(':memory:')
        try:
            with self.assertRaisesRegex(imp.ImportPreconditionError,
                                        'ROW_FINGERPRINT_MISMATCH'):
                imp.apply_rows(con, r, 'test', approved_rows=original_list)
            self.assertFalse(con.in_transaction)
        finally:
            con.close()

    def test_no_auto_created_cards_even_with_matching_hashes(self):
        a = row()
        r = run_result([a], new_cards=[('garden', 'Гарден')])
        with self.assertRaisesRegex(sel.SelectionError, 'UNRESOLVED_CUSTOMERS'):
            sel.verify_apply_preconditions(r, approved([a]))

    def test_unresolved_operator_refused(self):
        a = row()
        r = run_result([a])
        r.unresolved_operators[('Усмон', 'unknown')] = 1
        with self.assertRaisesRegex(sel.SelectionError, 'UNRESOLVED_OPERATORS'):
            sel.verify_apply_preconditions(r, approved([a]))

    def test_no_silent_reimport_and_no_missing_customer_or_date(self):
        a = row()
        r = run_result([a])
        r.already_in_db = 1
        with self.assertRaisesRegex(sel.SelectionError,
                                    'SELECTED_WORK_ALREADY_IN_DB'):
            sel.verify_apply_preconditions(r, approved([a]))
        missing = dict(a, customer_raw='')
        with self.assertRaisesRegex(sel.SelectionError,
                                    'EMPTY_CUSTOMER_REQUIRES_DECISION'):
            sel.verify_apply_preconditions(
                run_result([missing]), approved([missing]))
        undated = dict(a, date_kind='none', work_date_from=None,
                       work_date_to=None)
        with self.assertRaisesRegex(sel.SelectionError,
                                    'DATE_REQUIRES_DECISION'):
            sel.verify_apply_preconditions(
                run_result([undated]), approved([undated]))

    def test_held_payer_still_blocked_by_shared_gate(self):
        a = row()
        r = run_result([a], new_cards=[('когон фукаро', 'Когон Фукаро')])
        reasons = imp.apply_blockers(r, approved_rows=approved([a]))
        self.assertTrue(any('PAYER_DECISION_REQUIRED' in reason
                            for reason in reasons))
        self.assertTrue(any('UNRESOLVED_CUSTOMERS' in reason
                            for reason in reasons))

    def test_exactly_one_approved_row_is_written_on_synthetic_sqlite(self):
        # This is an in-memory test, NOT a production/backup database.
        a = dict(row(), drone_operator_id=14, drone_customer_id=155,
                 payer_unidentified=False)
        b = dict(row(35), source_row=35, drone_operator_id=14,
                 drone_customer_id=155, payer_unidentified=False)
        selection = approved([a])
        selected = sel.select_rows([a, b], selection)
        r = run_result(selected)
        con = sqlite3.connect(':memory:')
        try:
            columns = [
                'period_month', 'work_date_from', 'work_date_to', 'date_raw',
                'drone_operator_id', 'operator_raw', 'drone_customer_id',
                'customer_raw', 'area_ha', 'price_per_ha', 'amount',
                'other_costs', 'received_amount', 'received_kind',
                'payment_type', 'subdivision_name', 'source_file',
                'source_sheet', 'source_row', 'import_batch', 'note',
                'created_at', 'payer_unidentified']
            con.execute('CREATE TABLE drone_works (id INTEGER PRIMARY KEY, '
                        + ', '.join(name + ' TEXT' for name in columns) + ')')
            created, inserted, skipped = imp.apply_rows(
                con, r, 'synthetic-test', approved_rows=selection)
            self.assertEqual((created, inserted, skipped), (0, 1, 0))
            stored = list(con.execute(
                'SELECT source_row, area_ha, amount, drone_customer_id '
                'FROM drone_works'))
            self.assertEqual(len(stored), 1)
            self.assertEqual(int(stored[0][0]), 12)
            self.assertAlmostEqual(float(stored[0][1]), 13.53)
            self.assertAlmostEqual(float(stored[0][2]), 1158614.49)
            self.assertEqual(int(stored[0][3]), 155)
        finally:
            con.close()

    def test_original_july_filename_with_false_manifest_month_is_held(self):
        r = run_result([dict(row(), period_month='2026-06')])
        con = sqlite3.connect(':memory:')
        try:
            with self.assertRaisesRegex(imp.ImportPreconditionError,
                                        'JULY_2026_WORK_REQUIRES_ALLOWLIST'):
                imp.apply_rows(con, r, 'wrong-month')
            self.assertFalse(con.in_transaction)
        finally:
            con.close()

    def test_renamed_original_xlsx_hash_is_held_before_begin(self):
        # Same original workbook bytes, fake filename and false month.
        renamed = dict(row(), source_file='NewName.xlsx',
                       period_month='2026-06')
        r = run_result([renamed])
        r.source_book_hashes['NewName.xlsx'] = next(
            iter(sel.JULY_BOOK_SHA256.values()))
        con = sqlite3.connect(':memory:')
        try:
            with self.assertRaisesRegex(
                    imp.ImportPreconditionError,
                    'ORIGINAL_JULY_BOOK_BYTES_REQUIRE_ALLOWLIST'):
                imp.apply_rows(con, r, 'renamed-original-book')
            self.assertFalse(con.in_transaction)
        finally:
            con.close()

    def test_allowlist_with_false_manifest_month_is_rejected(self):
        mismonth = dict(row(), period_month='2026-06')
        with self.assertRaisesRegex(sel.SelectionError,
                                    'NON_JULY_SOURCE_IN_ALLOWLIST'):
            sel.verify_apply_preconditions(
                run_result([mismonth]), approved([mismonth]))

    def test_collect_records_actual_workbook_digest_not_just_filename(self):
        from openpyxl import Workbook
        with tempfile.TemporaryDirectory() as d:
            filename = 'MislabeledJune.xlsx'
            path = os.path.join(d, filename)
            wb = Workbook()
            wb.active.title = 'OTHER SHEET'
            wb.active.cell(1, 1).value = 'No real jobs in this fixture'
            wb.save(path)
            expected = hashlib.sha256(open(path, 'rb').read()).hexdigest()
            class DirectoryStub:
                def subdivision_for_file(self, file_name):
                    return None
            result = imp.collect(d, {filename: '2026-06'}, [filename],
                                 DirectoryStub(), None)
            self.assertEqual(result.source_book_hashes[filename], expected)
            self.assertEqual(result.rows, [])
            # If this file is one of the pinned originals, even its
            # deliberate rename/mislabeling must be held by shared gate.
            with mock.patch.dict(sel.JULY_BOOK_SHA256,
                                 {'OriginalJuly.xlsx': expected}, clear=True):
                self.assertTrue(any(
                    'ORIGINAL_JULY_BOOK_BYTES_REQUIRE_ALLOWLIST' in line
                    for line in imp.apply_blockers(result)))

    def test_late_duplicate_is_error_not_partial_success(self):
        a = dict(row(), drone_operator_id=14, drone_customer_id=155,
                 payer_unidentified=False)
        r = run_result([a])
        con = sqlite3.connect(':memory:')
        try:
            con.execute('CREATE TABLE drone_works (id INTEGER PRIMARY KEY, '
                        'source_file TEXT, source_sheet TEXT, source_row INTEGER)')
            con.execute('INSERT INTO drone_works '
                        '(source_file, source_sheet, source_row) VALUES (?,?,?)',
                        (a['source_file'], a['source_sheet'], a['source_row']))
            con.commit()
            with self.assertRaisesRegex(imp.ImportPreconditionError,
                                        'SELECTED_WORK_ALREADY_IN_DB_DURING_APPLY'):
                imp.apply_rows(con, r, 'late-duplicate', approved_rows=approved([a]))
            con.rollback()
            self.assertEqual(con.execute(
                'SELECT COUNT(*) FROM drone_works').fetchone()[0], 1)
        finally:
            con.close()

    def test_renamed_and_repacked_july_xlsx_refused_by_cli_and_web(self):
        # Codex P1 reproduction: ZIP metadata changes without changing
        # parsed work rows; byte hash and filename both cease to match.
        import contextlib
        import zipfile
        from tools import drone_works_fixtures as fx
        from drone_works_upload import run_import
        from tools import import_drone_works as web_imp

        with tempfile.TemporaryDirectory() as folder:
            name = 'ResavedElsewhere.xlsx'
            label = 'PinnedOriginalJuly.xlsx'
            sheet = 'свод ичи (Усмон)'
            header = ('№', 'ФХ номи ', 'Майдон (га)',
                      'Хизмат кўрсатиш суммаси', 'Жами сумма',
                      'Бошка харажатлар', 'Оператор топшириши керак',
                      'Дрон ишлаган сана', '*Изоҳ')
            jobs = [
                ('Нақд (Июль ойи)',), header,
                (1, 'Тест А фх', 2.5, 200000, 500000, None, None,
                 datetime.datetime(2026, 7, 11), None),
                (2, 'Тест Б фх', 3.0, 200000, 600000, None, None,
                 datetime.datetime(2026, 7, 12), None),
            ]
            directory, manifest = fx.build(
                os.path.join(folder, 'books'),
                books=((name, '2026-07', ((sheet, jobs),)),))
            db = fx.build_database(os.path.join(folder, 'db.sqlite'))
            book = os.path.join(directory, name)
            original = hashlib.sha256(open(book, 'rb').read()).hexdigest()
            pinned = {label: original}
            with mock.patch.dict(sel.JULY_BOOK_SHA256, pinned, clear=True):
                # The same original bytes under a different name are held.
                parsed = imp.collect(
                    directory, {name: '2026-07'}, [name], _directory(db),
                    None)
                self.assertEqual(len(parsed.rows), 2)
                self.assertIn(
                    'SOURCE_KEY_GUARD: ORIGINAL_JULY_BOOK_BYTES_REQUIRE_ALLOWLIST',
                    imp.apply_blockers(parsed))

                with zipfile.ZipFile(book, 'a') as z:
                    z.comment = b'only-xlsx-archive-comment-changed'
                self.assertNotEqual(
                    hashlib.sha256(open(book, 'rb').read()).hexdigest(),
                    original)
                parsed_repacked = imp.collect(
                    directory, {name: '2026-07'}, [name], _directory(db),
                    None)
                self.assertEqual(
                    [(r['customer_raw'], r['area_ha']) for r in parsed.rows],
                    [(r['customer_raw'], r['area_ha']) for r in
                     parsed_repacked.rows])
                self.assertEqual(len(parsed_repacked.rows), 2)
                self.assertIn(
                    'SOURCE_KEY_GUARD: JULY_2026_WORK_REQUIRES_ALLOWLIST',
                    imp.apply_blockers(parsed_repacked))
                report = os.path.join(folder, 'legacy-report.txt')
                out = io.StringIO()
                with contextlib.redirect_stdout(out):
                    exit_code = imp.main([
                        '--dir', directory, '--manifest', manifest,
                        '--db', db, '--report', report, '--apply'])
                self.assertEqual(exit_code, 2, out.getvalue())
                self.assertIn('JULY_2026_WORK_REQUIRES_ALLOWLIST',
                              out.getvalue())
                with self.assertRaisesRegex(
                        web_imp.ImportPreconditionError,
                        'JULY_2026_WORK_REQUIRES_ALLOWLIST'):
                    run_import(
                        db, directory, [{'name': name}], '2026-07', True,
                        'synthetic-web', os.path.join(folder, 'web-report.txt'))
                con = sqlite3.connect(db)
                try:
                    for table in ('drone_works', 'drone_customers',
                                  'drone_customer_aliases'):
                        self.assertEqual(con.execute(
                            'SELECT COUNT(*) FROM ' + table).fetchone()[0], 0)
                finally:
                    con.close()

    def test_july_actual_date_cannot_be_relabelled_as_june(self):
        r = dict(row(), source_file='UnpinnedResaved.xlsx',
                 period_month='2026-06')
        self.assertFalse(sel.protected(r))
        self.assertTrue(sel.july_work(r))
        self.assertIn(
            'SOURCE_KEY_GUARD: JULY_2026_WORK_REQUIRES_ALLOWLIST',
            imp.apply_blockers(run_result([r])))

    def test_cp1251_error_output_is_ascii_safe_and_status_2(self):
        import contextlib
        from unittest import mock
        with tempfile.TemporaryDirectory() as folder:
            # A matching filename whose original XLSX digest differs;
            # only the refusal output is under test, no actual imports.
            name = 'Дрон Ғиждувон ПТЗ Июль.xlsx'
            manifest = os.path.join(folder, 'manifest.txt')
            with open(manifest, 'w', encoding='utf-8') as out:
                out.write(name + ' 2026-07\n')
            with open(os.path.join(folder, name), 'wb') as out:
                out.write(b'different-byte-content')
            db = os.path.join(folder, 'db.sqlite')
            with open(db, 'wb') as out:
                out.write(b'not-used-since-verify-books-fails-first')
            approved_csv = os.path.join(folder, 'approved.csv')
            with open(approved_csv, 'wb') as out:
                out.write(b'source_key,row_sha256\nkey,hash\n')
            raw = io.BytesIO()
            encoded = io.TextIOWrapper(raw, encoding='cp1251',
                                       errors='strict', write_through=True)
            with mock.patch.dict(
                    sel.JULY_BOOK_SHA256, {name: '0' * 64}, clear=True):
                with contextlib.redirect_stdout(encoded):
                    code = sel.cli([
                        '--dir', folder, '--manifest', manifest, '--db', db,
                        '--only-keys', approved_csv,
                        '--only-keys-sha256', hashlib.sha256(
                            open(approved_csv, 'rb').read()).hexdigest()])
                    encoded.flush()
            self.assertEqual(code, 2)
            self.assertIn(b'ORIGINAL_BOOK_SHA256_MISMATCH',
                          raw.getvalue())
            self.assertNotIn(b'Traceback', raw.getvalue())

    def test_allowlist_only_covers_exact_july_five_books(self):
        other = dict(row(), source_file='SomeOtherBook.xlsx')
        with self.assertRaisesRegex(sel.SelectionError,
                                    'NON_JULY_SOURCE_IN_ALLOWLIST'):
            sel.validate_row_hashes([other], approved([other]))


# ─── FIN-RECON-003 readiness, 05.11.2026 ─────────────────────────────────────

R02_KEY = 'Дрон Ғиждувон ПТЗ Июль.xlsx!свод ичи (Усмон)!11'


def resolved(n, **changes):
    base = dict(row(n), drone_operator_id=14, drone_customer_id=155,
                payer_unidentified=False)
    base.update(changes)
    return base


def works_table(con):
    columns = ['period_month', 'work_date_from', 'work_date_to', 'date_raw',
               'drone_operator_id', 'operator_raw', 'drone_customer_id',
               'customer_raw', 'area_ha', 'price_per_ha', 'amount',
               'other_costs', 'received_amount', 'received_kind',
               'payment_type', 'subdivision_name', 'source_file',
               'source_sheet', 'source_row', 'import_batch', 'note',
               'created_at', 'payer_unidentified']
    con.execute('CREATE TABLE drone_works (id INTEGER PRIMARY KEY, '
                + ', '.join(name + ' TEXT' for name in columns) + ')')


class ExcludedDuplicateAndFingerprintTests(unittest.TestCase):

    def test_r02_is_refused_even_with_a_valid_row_hash(self):
        r02 = resolved(11)
        self.assertEqual(sel.source_key(r02), R02_KEY)
        self.assertIn(R02_KEY, sel.EXCLUDED_SOURCE_KEYS)
        data = allow_csv(list(approved([r02]).items()))
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, 'keys.csv')
            with open(path, 'wb') as f:
                f.write(data)
            with self.assertRaisesRegex(sel.SelectionError,
                                        'EXCLUDED_SOURCE_KEY: 2'):
                sel.load_allowlist(path, hashlib.sha256(data).hexdigest())
        # A caller that hands apply_rows() a dict directly is refused too,
        # before BEGIN.
        con = sqlite3.connect(':memory:')
        try:
            works_table(con)
            with self.assertRaisesRegex(imp.ImportPreconditionError,
                                        'EXCLUDED_SOURCE_KEY'):
                imp.apply_rows(con, run_result([r02]), 'r02',
                               approved_rows=approved([r02]))
            self.assertFalse(con.in_transaction)
            self.assertEqual(con.execute(
                'SELECT COUNT(*) FROM drone_works').fetchone()[0], 0)
        finally:
            con.close()

    def test_r02_negative_control_and_its_twin_is_not_excluded(self):
        # Without the exclusion the very same row and list ARE written, so
        # the refusal above is the rule at work and not some other guard.
        r02 = resolved(11)
        con = sqlite3.connect(':memory:')
        try:
            works_table(con)
            with mock.patch.object(sel, 'EXCLUDED_SOURCE_KEYS', frozenset()):
                self.assertEqual(imp.apply_rows(
                    con, run_result([r02]), 'control',
                    approved_rows=approved([r02])), (0, 1, 0))
        finally:
            con.close()
        # The exclusion is the exact key: row 10, the original entry with
        # the same farm, area and amount, is not excluded by it (it is HELD
        # for the owner's acceptance like every other row).
        twin = resolved(10, work_date_from=datetime.date(2026, 7, 10),
                        work_date_to=datetime.date(2026, 7, 10),
                        date_raw='2026-07-10')
        sel.validate_row_hashes([twin], approved([twin]))

    def test_generic_payer_row_is_written_with_null_customer(self):
        payer = resolved(30, customer_raw='Фукаро', drone_customer_id=None,
                         payer_unidentified=True)
        con = sqlite3.connect(':memory:')
        try:
            works_table(con)
            self.assertEqual(imp.apply_rows(
                con, run_result([payer]), 'p01',
                approved_rows=approved([payer])), (0, 1, 0))
            self.assertEqual(list(con.execute(
                'SELECT drone_customer_id, payer_unidentified, customer_raw '
                'FROM drone_works')), [(None, '1', 'Фукаро')])
        finally:
            con.close()

    def test_every_fingerprint_field_is_pinned(self):
        base = resolved(12)
        digest = sel.row_fingerprint(base)

        def other(value):
            if isinstance(value, bool):
                return not value
            if isinstance(value, (int, float)):
                return value + 1
            if isinstance(value, datetime.date):
                return value + datetime.timedelta(days=1)
            if value is None:
                return 'x'
            return value + 'x'

        for field in sel.ROW_FIELDS:
            with self.subTest(field=field):
                self.assertNotEqual(sel.row_fingerprint(
                    dict(base, **{field: other(base.get(field))})), digest)

    def test_dirty_manifest_unlisted_files_and_alias_conflicts_refused(self):
        a = resolved(12)
        for attribute, value, error in (
                ('manifest_problems', ['line 3: no month'],
                 'MANIFEST_HAS_ERRORS'),
                ('files_skipped_no_manifest', ['Extra.xlsx'],
                 'FILES_SKIPPED_NO_MANIFEST'),
                ('filename_alias_conflicts', [('a', 'b')],
                 'UNSAFE_FILENAME_ALIASES')):
            with self.subTest(error=error):
                r = run_result([a])
                setattr(r, attribute, value)
                with self.assertRaisesRegex(sel.SelectionError, error):
                    sel.verify_apply_preconditions(r, approved([a]))

    def test_a_failed_insert_leaves_the_whole_batch_unwritten(self):
        a, b = resolved(12), resolved(13)
        with tempfile.TemporaryDirectory() as d:
            db = os.path.join(d, 'works.sqlite')
            con = sqlite3.connect(db)
            works_table(con)
            con.execute("CREATE TRIGGER fail_second BEFORE INSERT ON "
                        "drone_works WHEN CAST(NEW.source_row AS INTEGER) = 13 "
                        "BEGIN SELECT RAISE(ABORT, 'synthetic failure'); END")
            con.commit()
            try:
                with self.assertRaises(sqlite3.IntegrityError):
                    imp.apply_rows(con, run_result([a, b]), 'fail',
                                   approved_rows=approved([a, b]))
            finally:
                con.close()     # no commit, no explicit rollback
            con = sqlite3.connect(db)
            try:
                self.assertEqual(con.execute(
                    'SELECT COUNT(*) FROM drone_works').fetchone()[0], 0)
            finally:
                con.close()


JULY_HEADER = ('№', 'ФХ номи ', 'Майдон (га)', 'Хизмат кўрсатиш суммаси',
               'Жами сумма', 'Бошка харажатлар', 'Оператор топшириши керак',
               'Дрон ишлаган сана', '*Изоҳ')
CASH = ('Нақд (Июль ойи)',)


def _job(n, farm, area, day):
    return (n, farm, area, 200000, round(area * 200000, 2), None, None,
            datetime.datetime(2026, 7, day), None)


GIJ = 'Дрон Ғиждувон ПТЗ Июль.xlsx'
K_NAMED = GIJ + '!свод ичи (Шахзод)!3'
K_GENERIC = GIJ + '!свод ичи (Шахзод)!4'
K_R01 = GIJ + '!свод ичи (Шахзод)!5'
K_NO_CARD = GIJ + '!свод ичи (Шахзод)!6'
K_SUMMARY = GIJ + '!ОБШИЙ СВОД!3'


class SelectiveCliEndToEndTests(unittest.TestCase):
    """The guarded CLI from argv to the database, on five SYNTHETIC books
    saved under the five pinned names; their hashes are patched in. The
    allowlists are computed at run time and deleted with the folder."""

    def setUp(self):
        from tools import drone_works_fixtures as fx
        self.tmp = tempfile.mkdtemp(prefix='fr3_sel_e2e_')
        self.addCleanup(__import__('shutil').rmtree, self.tmp, True)
        sheets = {
            GIJ: (
                ('свод ичи (Шахзод)', [
                    CASH, JULY_HEADER,
                    _job(1, 'Тест А фх', 2.5, 11),      # row 3: has an alias
                    _job(2, 'Фукаро', 3.0, 12),         # row 4: generic payer
                    _job(3, 'Когон Фукаро', 6.0, 23),   # row 5: R01
                    _job(4, 'Гоу  бобо', 1.5, 14)]),    # row 6: no card
                ('ОБШИЙ СВОД', [('ЖАМИ',), JULY_HEADER,
                                _job(1, 'Тест А фх', 2.5, 11)])),
            'ИЮЛЬ Агрокластер.xlsx': (('свод ичи (Нурали)', [
                CASH, JULY_HEADER, _job(1, 'Тест Б фх', 1.0, 5)]),),
            'Когон ПТЗ ИЮЛЬ.xlsx': (('свод ичи (Шохрух)', [
                CASH, JULY_HEADER, _job(1, 'Тест Б фх', 1.5, 6)]),),
            'Сервис Дрон маълумот Июль.xlsx': (('свод ичи (Шахзод)', [
                CASH, JULY_HEADER, _job(1, 'Тест Б фх', 2.0, 7)]),),
            'Шофиркон ПТЗ Дрон маълумот Июль.xlsx': (('свод ичи (Туйғун)', [
                CASH, JULY_HEADER, _job(1, 'Тест Б фх', 2.5, 8)]),),
        }
        self.assertEqual(set(sheets), set(sel.JULY_BOOK_SHA256))
        self.books, self.manifest = fx.build(
            os.path.join(self.tmp, 'books'),
            books=tuple((name, '2026-07', sheets[name])
                        for name in sorted(sheets)))
        pinned = {}
        for name in sheets:
            with open(os.path.join(self.books, name), 'rb') as f:
                pinned[name] = hashlib.sha256(f.read()).hexdigest()
        patcher = mock.patch.dict(sel.JULY_BOOK_SHA256, pinned, clear=True)
        patcher.start()
        self.addCleanup(patcher.stop)
        self.db = fx.build_database(os.path.join(self.tmp, 'db.sqlite'))
        con = sqlite3.connect(self.db)
        try:
            for cid, name in ((1, 'Тест А фх'), (2, 'Тест Б фх')):
                con.execute('INSERT INTO drone_customers (id, name, '
                            "customer_type, is_active) VALUES (?, ?, "
                            "'external', 1)", (cid, name))
                con.execute('INSERT INTO drone_customer_aliases (raw_name, '
                            'normalized_key, drone_customer_id, is_active) '
                            'VALUES (?, ?, ?, 1)',
                            (name, imp.customer_key(name), cid))
            con.commit()
        finally:
            con.close()
        self.rows = self.parse()

    def parse(self):
        manifest, _problems = imp.read_manifest(self.manifest)
        con = imp._connect(self.db, read_only=True)
        try:
            directory = imp.Directory(con)
            result = imp.collect(self.books, manifest, sorted(manifest),
                                 directory, None)
            imp.resolve(result, directory)
        finally:
            con.close()
        return {sel.source_key(r): r for r in result.rows}

    def allowlist(self, keys, name='keys.csv'):
        data = allow_csv([(k, sel.row_fingerprint(self.rows[k])
                           if k in self.rows else '0' * 64) for k in keys])
        path = os.path.join(self.tmp, name)
        with open(path, 'wb') as f:
            f.write(data)
        return path, hashlib.sha256(data).hexdigest()

    def cli(self, keys, *extra, manifest=None, books=None):
        import contextlib
        path, digest = keys if isinstance(keys, tuple) else self.allowlist(keys)
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            code = sel.cli(['--dir', books or self.books,
                            '--manifest', manifest or self.manifest,
                            '--db', self.db, '--only-keys', path,
                            '--only-keys-sha256', digest] + list(extra))
        return code, out.getvalue()

    def counts(self):
        con = sqlite3.connect(self.db)
        try:
            return tuple(con.execute('SELECT COUNT(*) FROM ' + t).fetchone()[0]
                         for t in ('drone_works', 'drone_customers',
                                   'drone_customer_aliases'))
        finally:
            con.close()

    def db_sha(self):
        with open(self.db, 'rb') as f:
            return hashlib.sha256(f.read()).hexdigest()

    def test_the_synthetic_books_classify_as_intended(self):
        self.assertEqual(len(self.rows), 8)
        self.assertEqual(self.rows[K_NAMED]['drone_customer_id'], 1)
        for key in (K_GENERIC, K_R01):
            self.assertIsNone(self.rows[key]['drone_customer_id'], key)
            self.assertTrue(self.rows[key]['payer_unidentified'], key)
        self.assertTrue(self.rows[K_NO_CARD]['customer_is_new'])
        self.assertNotIn(K_SUMMARY, self.rows)
        self.assertTrue(all(r['drone_operator_id'] for r in self.rows.values()))

    def test_read_only_then_exact_write_then_second_apply_refused(self):
        keys = [K_NAMED, K_GENERIC, K_R01]
        before = self.db_sha()
        code, out = self.cli(keys)
        self.assertEqual(code, 0, out)
        self.assertIn('RESULT=READONLY_SUBSET_TECHNICAL_GATE_OK', out)
        self.assertEqual(self.db_sha(), before)

        code, out = self.cli(keys, '--apply', '--batch', 'e2e')
        self.assertEqual(code, 0, out)
        self.assertIn('created=0 inserted=3 skipped=0', out)
        self.assertEqual(self.counts(), (3, 2, 2))
        con = sqlite3.connect(self.db)
        try:
            stored = {(r[0], r[1]): (r[2], r[3]) for r in con.execute(
                'SELECT source_sheet, source_row, drone_customer_id, '
                'payer_unidentified FROM drone_works')}
        finally:
            con.close()
        self.assertEqual(stored, {('свод ичи (Шахзод)', 3): (1, None),
                                  ('свод ичи (Шахзод)', 4): (None, 1),
                                  ('свод ичи (Шахзод)', 5): (None, 1)})

        written = self.db_sha()
        code, out = self.cli(keys, '--apply', '--batch', 'e2e-again')
        self.assertEqual(code, 2, out)
        self.assertIn('SELECTED_WORK_ALREADY_IN_DB', out)
        self.assertIn('NO IMPORT', out)
        self.assertEqual(self.counts(), (3, 2, 2))
        self.assertEqual(self.db_sha(), written)

    def test_refusals_write_nothing(self):
        bad_manifest = os.path.join(self.tmp, 'dirty_manifest.txt')
        with open(self.manifest, encoding='utf-8') as f:
            text = f.read()
        with open(bad_manifest, 'w', encoding='utf-8') as f:
            f.write(text + 'this line has no month\n')
        wrong_sha = (self.allowlist([K_NAMED], 'w.csv')[0], 'f' * 64)
        cases = (
            ('no card for the spelling', [K_NAMED, K_NO_CARD], {},
             'UNRESOLVED_CUSTOMERS'),
            ('summary-only key', [K_NAMED, K_SUMMARY], {},
             'APPROVED_KEYS_NOT_PARSED'),
            ('excluded duplicate R02', [K_NAMED, R02_KEY], {},
             'EXCLUDED_SOURCE_KEY'),
            ('dirty manifest', [K_NAMED], {'manifest': bad_manifest},
             'MANIFEST_HAS_ERRORS'),
            ('list bytes differ from the approved SHA', wrong_sha, {},
             'ALLOWLIST_SHA256_MISMATCH'),
        )
        before = self.db_sha()
        for label, keys, kwargs, error in cases:
            with self.subTest(label):
                code, out = self.cli(keys, '--apply', **kwargs)
                self.assertEqual(code, 2, out)
                self.assertIn(error, out)
                self.assertIn('NO IMPORT', out)
                self.assertEqual(self.db_sha(), before)

    def test_a_sixth_workbook_in_the_folder_is_refused(self):
        import shutil
        shutil.copy(os.path.join(self.books, GIJ),
                    os.path.join(self.books, 'Копия.xlsx'))
        code, out = self.cli([K_NAMED], '--apply')
        self.assertEqual(code, 2, out)
        self.assertIn('BOOK_SET_MISMATCH', out)
        self.assertEqual(self.counts(), (0, 2, 2))

    def test_alias_moved_after_signing_is_refused(self):
        keys = self.allowlist([K_NAMED])
        con = sqlite3.connect(self.db)
        try:
            con.execute("UPDATE drone_customer_aliases SET "
                        "drone_customer_id = 2 WHERE normalized_key = ?",
                        (imp.customer_key('Тест А фх'),))
            con.commit()
        finally:
            con.close()
        code, out = self.cli(keys, '--apply')
        self.assertEqual(code, 2, out)
        self.assertIn('ROW_FINGERPRINT_MISMATCH', out)
        self.assertEqual(self.counts(), (0, 2, 2))

    def test_a_failed_write_rolls_the_cli_batch_back(self):
        con = sqlite3.connect(self.db)
        try:
            con.execute("CREATE TRIGGER fail_r01 BEFORE INSERT ON drone_works "
                        "WHEN NEW.source_row = 5 "
                        "BEGIN SELECT RAISE(ABORT, 'synthetic failure'); END")
            con.commit()
        finally:
            con.close()
        keys = [K_NAMED, K_GENERIC, K_R01]
        code, out = self.cli(keys, '--apply')
        self.assertEqual(code, 2, out)
        self.assertIn('NO IMPORT', out)
        self.assertNotIn('Traceback', out)
        self.assertEqual(self.counts(), (0, 2, 2))
        # Negative control: the trigger, and nothing else, stopped it.
        con = sqlite3.connect(self.db)
        try:
            con.execute('DROP TRIGGER fail_r01')
            con.commit()
        finally:
            con.close()
        code, out = self.cli(keys, '--apply')
        self.assertEqual(code, 0, out)
        self.assertEqual(self.counts(), (3, 2, 2))

    def test_a_row_written_between_preflight_and_apply_fails_the_batch(self):
        keys = [K_NAMED, K_GENERIC, K_R01]
        original = imp.apply_rows

        def interleaved(con, result, batch, approved_rows=None):
            other = sqlite3.connect(self.db)
            try:
                other.execute(
                    'INSERT INTO drone_works (period_month, customer_raw, '
                    'area_ha, payment_type, source_file, source_sheet, '
                    "source_row) VALUES ('2026-07', 'x', 1, 'cash', ?, "
                    "'свод ичи (Шахзод)', 4)", (GIJ,))
                other.commit()
            finally:
                other.close()
            return original(con, result, batch, approved_rows=approved_rows)

        with mock.patch.object(imp, 'apply_rows', interleaved):
            code, out = self.cli(keys, '--apply')
        self.assertEqual(code, 2, out)
        self.assertIn('SELECTED_WORK_ALREADY_IN_DB_DURING_APPLY', out)
        # Only the interleaved row exists: row 3, inserted before row 4 was
        # met, was rolled back with the rest of the batch.
        self.assertEqual(self.counts(), (1, 2, 2))


if __name__ == '__main__':
    unittest.main()
