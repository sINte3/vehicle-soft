# -*- coding: utf-8 -*-
"""RE-SP-002: sku_save normalized-duplicate race handler tests.

All against the disposable harness SQLite database — never real data.
"""
import unittest
from unittest import mock

from tests.harness import (app, db, reset_db, create_admin, login, CSRF)
from sqlalchemy import text

import spare_parts
from models import SparePart, SparePartSku

# The exact partial unique index the production migration creates
# (db.create_all() cannot express it, so tests apply the migration's DDL).
import migrate_spare_parts_sku_uniqueness as sku_migration


def _flashed(client):
    with client.session_transaction() as sess:
        return ' '.join(str(m) for _, m in sess.get('_flashes', []))


class SkuRaceTestCase(unittest.TestCase):

    def setUp(self):
        reset_db()
        self.admin_id = create_admin()
        with app.app_context():
            part = SparePart(name='Test filter', status='active', is_active=True)
            db.session.add(part)
            db.session.commit()
            self.part_id = part.id
            db.session.execute(text(sku_migration.CREATE_INDEX))
            db.session.commit()
        self.client = app.test_client()
        login(self.client, self.admin_id)

    def _save_sku(self, brand='Bosch', article='F026402062', supplier='OOO Detal'):
        return self.client.post('/spare-parts/skus/save', data={
            'csrf_token': CSRF,
            'spare_part_id': str(self.part_id),
            'brand': brand,
            'article_number': article,
            'supplier': supplier,
        })

    def _active_sku_count(self):
        with app.app_context():
            return SparePartSku.query.filter_by(
                spare_part_id=self.part_id, is_active=True).count()

    def test_precheck_blocks_case_whitespace_duplicate(self):
        resp = self._save_sku()
        self.assertEqual(resp.status_code, 302)
        self.assertEqual(self._active_sku_count(), 1)
        resp = self._save_sku(brand='  bosch ', article=' f026402062',
                              supplier='ooo detal ')
        self.assertEqual(resp.status_code, 302)
        self.assertIn('артикул аллақачон мавжуд', _flashed(self.client))
        self.assertEqual(self._active_sku_count(), 1)

    def test_forced_race_gets_friendly_error_not_500(self):
        # Simulate the true race: both saves pass the pre-check, so the second
        # insert is rejected only by uq_spare_part_skus_normalized at flush().
        self.assertEqual(self._save_sku().status_code, 302)
        with mock.patch.object(spare_parts, '_sku_normalized_clash',
                               return_value=False):
            resp = self._save_sku()
        self.assertEqual(resp.status_code, 302)  # NOT a 500
        self.assertIn('артикул аллақачон мавжуд', _flashed(self.client))
        self.assertEqual(self._active_sku_count(), 1)

    def test_session_usable_after_handled_conflict(self):
        self.assertEqual(self._save_sku().status_code, 302)
        with mock.patch.object(spare_parts, '_sku_normalized_clash',
                               return_value=False):
            self._save_sku()  # handled IntegrityError, session rolled back
        # A subsequent normal save in the same process must succeed cleanly.
        resp = self._save_sku(brand='MANN', article='W914/2', supplier='')
        self.assertEqual(resp.status_code, 302)
        self.assertEqual(self._active_sku_count(), 2)
        # SKU-RENAME-001: the visible term is «Артикул» now (identifiers stay).
        self.assertIn('Артикул', _flashed(self.client))


if __name__ == '__main__':
    unittest.main()
