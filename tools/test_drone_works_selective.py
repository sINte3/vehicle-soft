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


def row(n=11):
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
                         'Дрон Ғиждувон ПТЗ Июль.xlsx!свод ичи (Усмон)!11')

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
        a, b = row(11), row(35)
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
                                        'JULY_ALLOWLIST_REQUIRED'):
                imp.apply_rows(con, r, 'test')
            self.assertFalse(con.in_transaction)
        finally:
            con.close()

    def test_unrelated_old_import_not_blocked_by_july_guard(self):
        r = run_result([dict(row(), source_file='Older.xlsx',
                             period_month='2026-06')])
        self.assertFalse(imp.apply_blockers(r))

    def test_approved_list_must_match_all_rows_before_begin(self):
        a, b = row(11), row(35)
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

    def test_allowlist_only_covers_exact_july_five_books(self):
        other = dict(row(), source_file='SomeOtherBook.xlsx')
        with self.assertRaisesRegex(sel.SelectionError,
                                    'NON_JULY_SOURCE_IN_ALLOWLIST'):
            sel.validate_row_hashes([other], approved([other]))


if __name__ == '__main__':
    unittest.main()
