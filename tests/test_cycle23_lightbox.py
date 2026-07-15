# -*- coding: utf-8 -*-
"""SPARE-PARTS-CYCLE-2-3 Part 4: accessible attachment lightbox."""
import os
import unittest

from tests.harness import app, db, reset_db, create_admin, create_org, login, TEST_UPLOAD_DIR
from models import SparePartRequest, SparePartRequestItem, SparePartAttachment
from datetime import date


class LightboxTests(unittest.TestCase):
    def setUp(self):
        reset_db()
        self.admin_id = create_admin()
        self.org_id = create_org()
        os.makedirs(TEST_UPLOAD_DIR, exist_ok=True)
        with app.app_context():
            req = SparePartRequest(request_date=date.today(),
                                   organization_id=self.org_id,
                                   status='draft', created_by=self.admin_id)
            db.session.add(req)
            db.session.flush()
            item = SparePartRequestItem(request_id=req.id, name='Фильтр',
                                        quantity=1, unit='dona')
            db.session.add(item)
            db.session.flush()
            # One attachment whose file exists, one whose file is missing.
            present = SparePartAttachment(
                item_id=item.id, file_path='present.jpg',
                original_filename='photo одна.jpg', file_size=123456,
                uploaded_by=self.admin_id)
            missing = SparePartAttachment(
                item_id=item.id, file_path='gone.jpg',
                original_filename='vanished.jpg', file_size=99,
                uploaded_by=self.admin_id)
            db.session.add_all([present, missing])
            db.session.commit()
            self.rid = req.id
            self.present_id = present.id
            self.missing_id = missing.id
        with open(os.path.join(TEST_UPLOAD_DIR, 'present.jpg'), 'wb') as fh:
            fh.write(b'\xff\xd8\xff' + b'0' * 10)

    def _detail_html(self):
        client = app.test_client()
        login(client, self.admin_id)
        resp = client.get('/spare-parts/{}'.format(self.rid))
        self.assertEqual(resp.status_code, 200)
        return resp.get_data(as_text=True)

    def test_present_attachment_gets_lightbox_trigger_with_metadata(self):
        html = self._detail_html()
        self.assertIn('class="sp-att-link"', html)
        self.assertIn('data-filename="photo одна.jpg"', html)
        self.assertIn('data-size="123456"', html)

    def test_lightbox_dialog_markup_present(self):
        html = self._detail_html()
        self.assertIn('id="spLightbox"', html)
        self.assertIn('role="dialog"', html)
        self.assertIn('aria-modal="true"', html)
        self.assertIn('id="spLightboxClose"', html)

    def test_missing_file_badge_is_not_interactive(self):
        html = self._detail_html()
        # The missing attachment renders as the RE-SP-001 badge, never as a
        # lightbox trigger or download link.
        self.assertIn('Файл мавжуд эмас', html)
        self.assertNotIn(
            '/spare-parts/attachments/{}"'.format(self.missing_id)
            + ' target="_blank" title="vanished.jpg"', html)
        badge_zone = html[html.index('vanished.jpg') - 400:
                          html.index('vanished.jpg') + 50]
        self.assertNotIn('sp-att-link', badge_zone)


if __name__ == '__main__':
    unittest.main()
