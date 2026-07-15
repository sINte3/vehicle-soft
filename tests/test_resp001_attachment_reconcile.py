# -*- coding: utf-8 -*-
"""RE-SP-001: attachment reconciliation tooling + missing-file display tests.

Everything runs against throwaway temp directories and the disposable
harness SQLite database. No test touches real staging data, the real
uploads directory, or instance/transport.db.
"""
import io
import json
import os
import shutil
import sqlite3
import tempfile
import unittest
from datetime import date
from unittest import mock

from tests.harness import (app, db, reset_db, create_admin, create_org,
                           login, CSRF, TEST_UPLOAD_DIR)

import spare_parts
import spare_parts_attachment_reconcile as reconcile
from models import (SparePart, SparePartAttachment, SparePartRequest,
                    SparePartRequestItem)

JPEG_BYTES = b'\xff\xd8\xff\xe0' + b'\x00' * 60


def _make_throwaway_db(path, rows):
    """Minimal attachment schema for the standalone reconcile script."""
    con = sqlite3.connect(path)
    cur = con.cursor()
    cur.execute("""CREATE TABLE spare_part_requests (
        id INTEGER PRIMARY KEY, status TEXT)""")
    cur.execute("""CREATE TABLE spare_part_request_items (
        id INTEGER PRIMARY KEY, request_id INTEGER)""")
    cur.execute("""CREATE TABLE spare_part_attachments (
        id INTEGER PRIMARY KEY, item_id INTEGER, file_path TEXT,
        original_filename TEXT DEFAULT '', file_size INTEGER DEFAULT 0,
        uploaded_by INTEGER, uploaded_at TEXT DEFAULT '')""")
    for req_id, status, item_id, att in rows:
        cur.execute("INSERT OR IGNORE INTO spare_part_requests VALUES (?, ?)",
                    (req_id, status))
        cur.execute("INSERT OR IGNORE INTO spare_part_request_items "
                    "VALUES (?, ?)", (item_id, req_id))
        if att:
            cur.execute("INSERT INTO spare_part_attachments "
                        "(item_id, file_path, file_size) VALUES (?, ?, ?)",
                        (item_id, att[0], att[1]))
    con.commit()
    con.close()


class ReconcileScriptTestCase(unittest.TestCase):
    """4a + 4c: report classification and manifest apply, throwaway dirs."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix='resp001_')
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.uploads = os.path.join(self.tmp, 'uploads')
        os.makedirs(self.uploads)
        self.outdir = os.path.join(self.tmp, 'evidence')
        self.db_path = os.path.join(self.tmp, 'throwaway.db')

        # request 1 (draft): item 1 with a PRESENT file; request 4 (issued):
        # item 4 with a MISSING file; plus one orphan on disk.
        self.present_name = '1_aaaaaaaa.jpg'
        self.missing_name = '4_8f818b0c.jpg'
        self.orphan_name = '9_bbbbbbbb.jpg'
        with open(os.path.join(self.uploads, self.present_name), 'wb') as fh:
            fh.write(JPEG_BYTES)
        with open(os.path.join(self.uploads, self.orphan_name), 'wb') as fh:
            fh.write(JPEG_BYTES * 2)
        _make_throwaway_db(self.db_path, [
            (1, 'draft', 1, (self.present_name, len(JPEG_BYTES))),
            (4, 'issued', 4, (self.missing_name, 146)),
            (9, 'draft', 9, None),
        ])

    def _report(self):
        return reconcile.build_report(self.db_path, self.uploads)

    def test_report_classifies_present_missing_and_orphan(self):
        report = self._report()
        by_id = {a['attachment_id']: a for a in report['attachments']}
        self.assertEqual(by_id[1]['status'], 'present')
        self.assertEqual(by_id[1]['disk']['size'], len(JPEG_BYTES))
        self.assertEqual(by_id[2]['status'], 'missing')
        self.assertTrue(by_id[2]['issued_request'])
        self.assertEqual(len(report['orphans']), 1)
        orphan = report['orphans'][0]
        self.assertEqual(orphan['file_name'], self.orphan_name)
        self.assertEqual(orphan['inferred_item_id'], 9)
        self.assertEqual(orphan['inferred_request_id'], 9)
        self.assertFalse(orphan['known'])
        self.assertEqual(report['summary']['missing'], 1)
        self.assertEqual(report['summary']['missing_on_issued_requests'], 1)
        self.assertEqual(report['summary']['orphan_files'], 1)

    def test_report_mode_writes_nothing(self):
        before = sorted(os.listdir(self.uploads))
        db_mtime = os.path.getmtime(self.db_path)
        self._report()
        self.assertEqual(sorted(os.listdir(self.uploads)), before)
        self.assertEqual(os.path.getmtime(self.db_path), db_mtime)

    def test_out_path_inside_repo_is_refused(self):
        with self.assertRaises(SystemExit):
            reconcile.main(['--db', self.db_path, '--uploads', self.uploads,
                            '--out', os.path.join(reconcile.REPO_ROOT,
                                                  'evil_report.json')])

    def _manifest(self, actions):
        path = os.path.join(self.tmp, 'manifest.json')
        with open(path, 'w', encoding='utf-8') as fh:
            json.dump({'manifest_version': 1, 'actions': actions}, fh)
        return path

    def test_restore_refuses_size_mismatch(self):
        backup = os.path.join(self.tmp, 'wrong_size.jpg')
        with open(backup, 'wb') as fh:
            fh.write(b'x' * 999)  # row expects 146 bytes
        manifest = self._manifest([
            {'action': 'restore', 'attachment_id': 2, 'backup_path': backup}])
        result, code = reconcile.run_manifest(self.db_path, self.uploads,
                                              manifest, apply_mode=True)
        self.assertEqual(code, 1)
        self.assertFalse(result['applied'])
        self.assertFalse(os.path.exists(
            os.path.join(self.uploads, self.missing_name)))

    def test_restore_refuses_sha_mismatch(self):
        backup = os.path.join(self.tmp, 'right_size_wrong_hash.jpg')
        with open(backup, 'wb') as fh:
            fh.write(b'y' * 146)
        manifest = self._manifest([
            {'action': 'restore', 'attachment_id': 2, 'backup_path': backup,
             'sha256': '0' * 64}])
        result, code = reconcile.run_manifest(self.db_path, self.uploads,
                                              manifest, apply_mode=True)
        self.assertEqual(code, 1)
        self.assertFalse(result['applied'])

    def test_restore_from_matching_backup_applies(self):
        backup = os.path.join(self.tmp, 'good_backup.jpg')
        with open(backup, 'wb') as fh:
            fh.write(b'z' * 146)  # matches DB row file_size
        manifest = self._manifest([
            {'action': 'restore', 'attachment_id': 2, 'backup_path': backup}])
        result, code = reconcile.run_manifest(self.db_path, self.uploads,
                                              manifest, apply_mode=True)
        self.assertEqual(code, 0)
        self.assertTrue(result['applied'])
        restored = os.path.join(self.uploads, self.missing_name)
        self.assertTrue(os.path.isfile(restored))
        self.assertTrue(os.path.isfile(backup))  # copied, not moved
        # audit entry written
        audit = os.path.join(self.uploads, '_reconcile', 'apply_audit.jsonl')
        self.assertTrue(os.path.isfile(audit))

    def test_manifest_dry_run_changes_nothing(self):
        manifest = self._manifest([
            {'action': 'quarantine', 'file': self.orphan_name}])
        result, code = reconcile.run_manifest(self.db_path, self.uploads,
                                              manifest, apply_mode=False)
        self.assertEqual(code, 0)
        self.assertFalse(result['applied'])
        self.assertTrue(os.path.isfile(
            os.path.join(self.uploads, self.orphan_name)))

    def test_quarantine_moves_only_named_orphan_and_is_reversible(self):
        manifest = self._manifest([
            {'action': 'quarantine', 'file': self.orphan_name}])
        result, code = reconcile.run_manifest(self.db_path, self.uploads,
                                              manifest, apply_mode=True)
        self.assertEqual(code, 0)
        src = os.path.join(self.uploads, self.orphan_name)
        dest = os.path.join(self.uploads, '_quarantine', self.orphan_name)
        self.assertFalse(os.path.exists(src))
        self.assertTrue(os.path.isfile(dest))
        # the other (referenced) file is untouched
        self.assertTrue(os.path.isfile(
            os.path.join(self.uploads, self.present_name)))
        # reversible: move it back and the report is clean again
        shutil.move(dest, src)
        report = self._report()
        self.assertEqual(report['summary']['orphan_files'], 1)

    def test_quarantine_of_referenced_file_is_refused(self):
        # the present file has a DB row -> protected evidence, never movable
        manifest = self._manifest([
            {'action': 'quarantine', 'file': self.present_name}])
        result, code = reconcile.run_manifest(self.db_path, self.uploads,
                                              manifest, apply_mode=True)
        self.assertEqual(code, 1)
        self.assertFalse(result['applied'])
        self.assertTrue(os.path.isfile(
            os.path.join(self.uploads, self.present_name)))

    def test_retain_registers_known_orphan(self):
        manifest = self._manifest([
            {'action': 'retain', 'file': self.orphan_name,
             'note': 'kept per owner decision'}])
        result, code = reconcile.run_manifest(self.db_path, self.uploads,
                                              manifest, apply_mode=True)
        self.assertEqual(code, 0)
        # file not moved
        self.assertTrue(os.path.isfile(
            os.path.join(self.uploads, self.orphan_name)))
        report = self._report()
        self.assertEqual(report['summary']['known_orphans'], 1)
        self.assertEqual(report['summary']['unexpected_orphans'], 0)

    def test_unknown_action_refused_no_delete_path(self):
        manifest = self._manifest([
            {'action': 'delete', 'file': self.orphan_name}])
        result, code = reconcile.run_manifest(self.db_path, self.uploads,
                                              manifest, apply_mode=True)
        self.assertEqual(code, 1)
        self.assertFalse(result['applied'])
        self.assertTrue(os.path.isfile(
            os.path.join(self.uploads, self.orphan_name)))


class MissingFileDisplayTestCase(unittest.TestCase):
    """4b: detail page shows a bilingual 'file unavailable' status."""

    def setUp(self):
        reset_db()
        self.admin_id = create_admin()
        self.org_id = create_org()
        os.makedirs(TEST_UPLOAD_DIR, exist_ok=True)
        with app.app_context():
            part = SparePart(name='Filter', status='active', is_active=True)
            db.session.add(part)
            db.session.flush()
            req = SparePartRequest(request_date=date.today(),
                                   organization_id=self.org_id,
                                   status='issued',
                                   created_by=self.admin_id)
            db.session.add(req)
            db.session.flush()
            item = SparePartRequestItem(request_id=req.id, name='Filter',
                                        quantity=1, unit='dona',
                                        spare_part_id=part.id)
            db.session.add(item)
            db.session.flush()
            present = '{}_11111111.jpg'.format(item.id)
            with open(os.path.join(TEST_UPLOAD_DIR, present), 'wb') as fh:
                fh.write(JPEG_BYTES)
            db.session.add(SparePartAttachment(
                item_id=item.id, file_path=present, file_size=len(JPEG_BYTES)))
            db.session.add(SparePartAttachment(
                item_id=item.id, file_path='{}_22222222.jpg'.format(item.id),
                original_filename='gone.jpg', file_size=146))
            db.session.commit()
            self.req_id = req.id
        self.client = app.test_client()
        login(self.client, self.admin_id)

    def test_detail_shows_unavailable_status_without_touching_row(self):
        resp = self.client.get('/spare-parts/{}'.format(self.req_id))
        self.assertEqual(resp.status_code, 200)
        html = resp.get_data(as_text=True)
        self.assertIn('Файл мавжуд эмас', html)  # uz default
        with app.app_context():
            self.assertEqual(SparePartAttachment.query.count(), 2)


class UploadRollbackRegressionTestCase(unittest.TestCase):
    """4d: SPARE-PARTS-FULL Part 6 regression guard — a failed save must not
    leave orphan files behind."""

    def setUp(self):
        reset_db()
        self.admin_id = create_admin()
        os.makedirs(TEST_UPLOAD_DIR, exist_ok=True)

    def test_store_item_photo_cleans_up_on_failure(self):
        from werkzeug.datastructures import FileStorage
        fs = FileStorage(stream=io.BytesIO(JPEG_BYTES), filename='a.jpg')
        before = set(os.listdir(TEST_UPLOAD_DIR))
        with app.app_context():
            with mock.patch.object(spare_parts.db.session, 'add',
                                   side_effect=RuntimeError('boom')):
                with self.assertRaises(RuntimeError):
                    with app.test_request_context():
                        spare_parts._store_item_photo(fs, item_id=1, user_id=1)
        self.assertEqual(set(os.listdir(TEST_UPLOAD_DIR)), before)


if __name__ == '__main__':
    unittest.main()
