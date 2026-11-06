#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""FIN-RECON-003 read-only fingerprint export. Synthetic books and DB only.

The five synthetic books and the database come from the selective CLI's
end-to-end fixture, so an export can be fed straight back into the guarded
CLI: the proof that the fingerprints are the ones `--apply` accepts.
"""
import contextlib
import csv
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

import drone_works_fingerprint_export as fe  # noqa: E402
import drone_works_selective as sel  # noqa: E402
import import_drone_works as imp  # noqa: E402
import test_drone_works_selective as tsel  # noqa: E402


class FingerprintExportTests(unittest.TestCase):
    # [REASON]: the fixture of the selective CLI's end-to-end tests, borrowed
    # as plain functions. Binding that TestCase class to a module-level name
    # would make unittest collect and run its tests here a second time.
    setUp = tsel.SelectiveCliEndToEndTests.setUp
    parse = tsel.SelectiveCliEndToEndTests.parse
    allowlist = tsel.SelectiveCliEndToEndTests.allowlist
    cli = tsel.SelectiveCliEndToEndTests.cli
    counts = tsel.SelectiveCliEndToEndTests.counts
    db_sha = tsel.SelectiveCliEndToEndTests.db_sha

    def keys_file(self, keys, name='act_keys.csv', header=('source_key',)):
        path = os.path.join(self.tmp, name)
        with open(path, 'w', encoding='utf-8', newline='') as f:
            writer = csv.writer(f, lineterminator='\n')
            writer.writerow(header)
            for key in keys:
                writer.writerow((key,))
        return path

    def export(self, keys, out=None, manifest=None, db=None):
        path = keys if isinstance(keys, str) else self.keys_file(keys)
        out = out or os.path.join(self.tmp, 'export_%d.csv' % len(os.listdir(self.tmp)))
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            code = fe.main(['--dir', self.books, '--manifest', manifest or self.manifest,
                            '--db', db or self.db, '--keys', path, '--out', out])
        rows = None
        if os.path.exists(out):
            with open(out, encoding='utf-8-sig', newline='') as f:
                rows = {r['source_key']: r for r in csv.DictReader(f)}
        return code, buf.getvalue(), rows, out

    def test_ok_rows_carry_the_fingerprint_the_guarded_cli_accepts(self):
        keys = [tsel.K_NAMED, tsel.K_GENERIC, tsel.K_R01]
        before = self.db_sha()
        code, out, rows, path = self.export(keys)
        self.assertEqual(code, 0, out)
        self.assertIn('RESULT=EXPORT_WRITTEN_ALL_OK', out)
        self.assertIn('OK_ROWS=3 OK_AREA_HA=11.50', out)
        self.assertEqual(self.db_sha(), before)
        for key in keys:
            self.assertEqual(rows[key]['diagnostics'], 'OK')
            self.assertEqual(rows[key]['row_sha256'],
                             sel.row_fingerprint(self.rows[key]))
        self.assertEqual(rows[tsel.K_NAMED]['resolved_customer_id'], '1')
        self.assertEqual((rows[tsel.K_R01]['resolved_customer_id'],
                          rows[tsel.K_R01]['payer_unidentified']), ('', '1'))
        # The person who signed the act builds the two-column list by hand;
        # here the same step, then the guarded CLI: read-only, then write.
        data = tsel.allow_csv([(k, rows[k]['row_sha256']) for k in keys])
        allow = os.path.join(self.tmp, 'approved.csv')
        with open(allow, 'wb') as f:
            f.write(data)
        listed = (allow, hashlib.sha256(data).hexdigest())
        code, cli_out = self.cli(listed)
        self.assertEqual(code, 0, cli_out)
        self.assertIn('RESULT=READONLY_SUBSET_TECHNICAL_GATE_OK', cli_out)
        code, cli_out = self.cli(listed, '--apply', '--batch', 'export-e2e')
        self.assertEqual(code, 0, cli_out)
        self.assertEqual(self.counts(), (3, 2, 2))

    def test_the_export_itself_is_not_an_allowlist(self):
        _code, _out, _rows, path = self.export([tsel.K_NAMED])
        with open(path, 'rb') as f:
            data = f.read()
        with self.assertRaises(sel.SelectionError) as ctx:
            sel.load_allowlist(path, hashlib.sha256(data).hexdigest())
        self.assertIn('ALLOWLIST_COLUMNS', str(ctx.exception))

    def test_refused_keys_get_no_fingerprint(self):
        keys = [tsel.K_NAMED, tsel.K_NO_CARD, tsel.K_SUMMARY, tsel.R02_KEY]
        code, out, rows, _path = self.export(keys)
        self.assertEqual(code, 3, out)
        self.assertIn('RESULT=EXPORT_WRITTEN_WITH_REFUSED_KEYS', out)
        expected = {tsel.K_NAMED: 'OK', tsel.K_NO_CARD: 'NO_CUSTOMER_CARD',
                    tsel.K_SUMMARY: 'NOT_PARSED',
                    tsel.R02_KEY: 'EXCLUDED_SOURCE_KEY'}
        self.assertEqual({k: r['diagnostics'] for k, r in rows.items()}, expected)
        for key in expected:
            self.assertEqual(bool(rows[key]['row_sha256']), key == tsel.K_NAMED, key)
        self.assertIn('REFUSED NO_CUSTOMER_CARD=1', out)
        self.assertEqual(self.counts(), (0, 2, 2))  # no card made for it

    def test_an_excluded_key_is_never_resolved(self):
        # [REASON]: an excluded key is held out before resolution, so a
        # directory problem on its book can neither abort nor colour the
        # other rows. The fixture has no row at the real R02 address, so a
        # parsed key stands in for it; without this test, dropping that
        # earlier filter survived the mutation run (the verdict order alone
        # already hides the fingerprint).
        resolved, real_resolve = [], imp.resolve

        def spy(result, directory):
            resolved.extend(sel.source_key(r) for r in result.rows)
            return real_resolve(result, directory)

        excluded = frozenset(sel.EXCLUDED_SOURCE_KEYS | {tsel.K_GENERIC})
        with mock.patch.object(sel, 'EXCLUDED_SOURCE_KEYS', excluded), \
                mock.patch.object(imp, 'resolve', spy):
            code, out, rows, _path = self.export([tsel.K_NAMED, tsel.K_GENERIC])
        self.assertEqual(code, 3, out)
        self.assertEqual(resolved, [tsel.K_NAMED])
        self.assertEqual(rows[tsel.K_GENERIC]['diagnostics'], 'EXCLUDED_SOURCE_KEY')
        self.assertEqual(rows[tsel.K_GENERIC]['row_sha256'], '')
        self.assertEqual(rows[tsel.K_GENERIC]['resolved_customer_id'], '')

    def test_a_key_already_in_the_database_is_refused(self):
        code, cli_out = self.cli([tsel.K_NAMED], '--apply', '--batch', 'first')
        self.assertEqual(code, 0, cli_out)
        code, out, rows, _path = self.export([tsel.K_NAMED, tsel.K_GENERIC])
        self.assertEqual(code, 3, out)
        self.assertEqual(rows[tsel.K_NAMED]['diagnostics'], 'ALREADY_IN_DB')
        self.assertEqual(rows[tsel.K_NAMED]['row_sha256'], '')
        self.assertEqual(rows[tsel.K_GENERIC]['diagnostics'], 'OK')

    def test_a_held_spelling_and_an_unresolved_operator_are_refused(self):
        # Negative control of R01: without it the row is held again.
        without_r01 = frozenset(imp.GENERIC_PAYER_KEYS - {'когон фукаро'})
        with mock.patch.object(imp, 'GENERIC_PAYER_KEYS', without_r01):
            code, out, rows, _path = self.export([tsel.K_R01])
        self.assertEqual(code, 3, out)
        self.assertEqual(rows[tsel.K_R01]['diagnostics'], 'PAYER_DECISION_PENDING')

        original = imp.Directory.resolve_operator

        def lose_one(directory, raw, subdivision):
            found = original(directory, raw, subdivision)
            return (None, 'synthetic') if raw == 'Нурали' else found
        nurali = 'ИЮЛЬ Агрокластер.xlsx!свод ичи (Нурали)!3'
        with mock.patch.object(imp.Directory, 'resolve_operator', lose_one):
            code, out, rows, _path = self.export([nurali, tsel.K_NAMED])
        self.assertEqual(code, 3, out)
        self.assertEqual(rows[nurali]['diagnostics'], 'UNRESOLVED_OPERATOR')
        self.assertEqual(rows[tsel.K_NAMED]['diagnostics'], 'OK')

        # Every reason is listed, not only the first one met.
        def lose_shahzod(directory, raw, subdivision):
            found = original(directory, raw, subdivision)
            return (None, 'synthetic') if raw == 'Шахзод' else found
        with mock.patch.object(imp.Directory, 'resolve_operator', lose_shahzod):
            code, out, rows, _path = self.export([tsel.K_NO_CARD])
        self.assertEqual(rows[tsel.K_NO_CARD]['diagnostics'],
                         'NO_CUSTOMER_CARD+UNRESOLVED_OPERATOR')
        self.assertIn('REFUSED_ROWS=1', out)

    def test_unsafe_inputs_export_nothing_and_write_nothing(self):
        bad_manifest = os.path.join(self.tmp, 'dirty_manifest.txt')
        with open(self.manifest, encoding='utf-8') as f:
            text = f.read()
        with open(bad_manifest, 'w', encoding='utf-8') as f:
            f.write(text + 'this line has no month\n')
        taken = os.path.join(self.tmp, 'taken.csv')
        with open(taken, 'w') as f:
            f.write('x')
        inside_repo = os.path.join(HERE, 'fr3_export_must_not_exist.csv')
        cases = (
            ('wrong header', self.keys_file([tsel.K_NAMED], 'h.csv',
                                            ('source_key', 'row_sha256')), {},
             'KEYS_COLUMNS'),
            ('duplicate key', [tsel.K_NAMED, tsel.K_NAMED], {}, 'DUPLICATE_KEY'),
            ('empty list', [], {}, 'EMPTY_KEYS_FILE'),
            ('dirty manifest', [tsel.K_NAMED], {'manifest': bad_manifest},
             'MANIFEST_HAS_ERRORS'),
            ('output exists', [tsel.K_NAMED], {'out': taken}, 'OUT_ALREADY_EXISTS'),
            ('output inside the repository', [tsel.K_NAMED], {'out': inside_repo},
             'OUT_INSIDE_REPOSITORY'),
            ('database missing', [tsel.K_NAMED],
             {'db': os.path.join(self.tmp, 'nope.sqlite')}, 'database not found'),
        )
        before = self.db_sha()
        for label, keys, kwargs, error in cases:
            with self.subTest(label):
                out_path = kwargs.get('out') or os.path.join(self.tmp, 'x_%s.csv' % len(label))
                existed = os.path.exists(out_path)
                code, out, _rows, _path = self.export(
                    keys, out=out_path, manifest=kwargs.get('manifest'),
                    db=kwargs.get('db'))
                self.assertEqual(code, 2, out)
                self.assertIn(error, out)
                self.assertIn('NOTHING EXPORTED', out)
                self.assertEqual(os.path.exists(out_path), existed, label)
                self.assertEqual(self.db_sha(), before)
        self.assertFalse(os.path.exists(os.path.join(self.tmp, 'nope.sqlite')))
        self.assertFalse(os.path.exists(inside_repo))

    def test_a_sixth_workbook_exports_nothing(self):
        import shutil
        shutil.copy(os.path.join(self.books, tsel.GIJ),
                    os.path.join(self.books, 'Копия.xlsx'))
        code, out, rows, _path = self.export([tsel.K_NAMED])
        self.assertEqual(code, 2, out)
        self.assertIn('BOOK_SET_MISMATCH', out)
        self.assertIsNone(rows)

    def test_the_summary_is_ascii_without_names(self):
        code, out, _rows, _path = self.export(
            [tsel.K_NAMED, tsel.K_NO_CARD, tsel.K_R01])
        self.assertEqual(code, 3, out)
        out.encode('ascii')  # raises if any non-ASCII character leaked
        for line in ('DB_OPENED=mode=ro query_only', 'KEYS=3',
                     'IMPORT_AUTHORIZED=NO', 'DIRECTORY_SHA256='):
            self.assertIn(line, out)
        self.assertRegex(out, r'CODE_BLOB import_drone_works\.py=[0-9a-f]{40}')

    def test_the_code_blob_is_the_git_blob_id(self):
        path = os.path.join(self.tmp, 'blob.txt')
        with open(path, 'wb') as f:
            f.write(b'a\r\nb\n')
        # git hash-object of "a\nb\n" (autocrlf folds CRLF on add)
        self.assertEqual(fe.git_blob(path),
                         hashlib.sha1(b'blob 4\0a\nb\n').hexdigest())

    def test_a_directory_change_after_export_invalidates_the_fingerprint(self):
        keys = [tsel.K_NAMED]
        _code, _out, rows, _path = self.export(keys)
        con = sqlite3.connect(self.db)
        try:
            con.execute('UPDATE drone_customer_aliases SET drone_customer_id = 2 '
                        'WHERE normalized_key = ?', (imp.customer_key('Тест А фх'),))
            con.commit()
        finally:
            con.close()
        data = tsel.allow_csv([(k, rows[k]['row_sha256']) for k in keys])
        allow = os.path.join(self.tmp, 'approved_old.csv')
        with open(allow, 'wb') as f:
            f.write(data)
        code, cli_out = self.cli((allow, hashlib.sha256(data).hexdigest()), '--apply')
        self.assertEqual(code, 2, cli_out)
        self.assertIn('ROW_FINGERPRINT_MISMATCH', cli_out)
        self.assertEqual(self.counts(), (0, 2, 2))


class IsolatedModeTests(unittest.TestCase):
    """Both CLIs as the server runs them: `python -I`, a foreign current
    folder, so nothing but the script's own path makes the imports work."""

    def run_cli(self, script, *args):
        import subprocess
        with tempfile.TemporaryDirectory() as cwd:
            p = subprocess.run([sys.executable, '-I', '-X', 'utf8',
                                os.path.join(HERE, script)] + list(args),
                               capture_output=True, cwd=cwd)
        return p.returncode, p.stdout.decode('utf-8', 'replace') + p.stderr.decode(
            'utf-8', 'replace')

    def test_the_guarded_cli_refuses_cleanly_under_isolated_mode(self):
        code, out = self.run_cli('drone_works_selective.py', '--dir', 'no_such_dir',
                                 '--manifest', 'm', '--db', 'd', '--only-keys', 'k',
                                 '--only-keys-sha256', '0' * 64)
        self.assertNotIn('Traceback', out)
        self.assertEqual(code, 2, out)
        self.assertIn('BOOK_DIRECTORY_NOT_FOUND', out)

    def test_the_export_refuses_cleanly_under_isolated_mode(self):
        code, out = self.run_cli('drone_works_fingerprint_export.py', '--dir', 'x',
                                 '--manifest', 'm', '--db', 'd', '--keys', 'k',
                                 '--out', 'o.csv')
        self.assertNotIn('Traceback', out)
        self.assertEqual(code, 2, out)
        self.assertIn('NOTHING EXPORTED', out)


if __name__ == '__main__':
    unittest.main()
