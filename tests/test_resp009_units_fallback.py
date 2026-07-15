# -*- coding: utf-8 -*-
"""RE-SP-009: units deploy/rollback safety tests.

Simulates the transitional windows (code deployed before
migrate_spare_parts_units.py, and the documented rollback order) against the
disposable harness database. Never touches real data.
"""
import unittest
from unittest import mock

from sqlalchemy import text
from sqlalchemy.exc import OperationalError

from tests.harness import app, db, reset_db, create_admin, create_org, login

import spare_parts
from models import SparePartUnit


class UnitsFallbackTestCase(unittest.TestCase):

    def setUp(self):
        reset_db()
        self.admin_id = create_admin()
        create_org()
        self.client = app.test_client()
        login(self.client, self.admin_id)

    def _drop_units_table(self):
        with app.app_context():
            db.session.execute(text('DROP TABLE IF EXISTS spare_part_units'))
            db.session.commit()

    def test_missing_table_routes_do_not_500(self):
        # code-before-migration simulation: DB without spare_part_units
        self._drop_units_table()
        with app.app_context():
            self.assertEqual(spare_parts._active_units(), [])
        for url in ('/spare-parts/new', '/spare-parts/catalog'):
            resp = self.client.get(url)
            self.assertEqual(resp.status_code, 200,
                             '{} must fall back, not 500'.format(url))

    def test_missing_table_fallback_is_narrow(self):
        # Any OperationalError OTHER than "no such table" must still surface.
        self._drop_units_table()
        with app.app_context():
            with mock.patch.object(
                    spare_parts.SparePartUnit, 'query') as q:
                q.filter_by.side_effect = OperationalError(
                    'SELECT 1', {}, Exception('database is locked'))
                with self.assertRaises(OperationalError):
                    spare_parts._active_units()

    def test_empty_directory_behavior_unchanged(self):
        # table present but no rows -> empty list (legacy free-text path)
        with app.app_context():
            self.assertEqual(SparePartUnit.query.count(), 0)
            self.assertEqual(spare_parts._active_units(), [])
        self.assertEqual(self.client.get('/spare-parts/new').status_code, 200)
        self.assertEqual(self.client.get('/spare-parts/catalog').status_code, 200)

    def test_rollback_rehearsal_no_route_500s(self):
        # Steady state: table present with a row.
        with app.app_context():
            db.session.add(SparePartUnit(code='dona', name_ru='шт',
                                         name_uz='дона', sort_order=10))
            db.session.commit()
        self.assertEqual(self.client.get('/spare-parts/new').status_code, 200)
        # Documented rollback order, step 1: code that tolerates a missing
        # table is already deployed (this codebase). Step 2: drop the table.
        self._drop_units_table()
        for url in ('/spare-parts/new', '/spare-parts/catalog'):
            self.assertEqual(self.client.get(url).status_code, 200)


if __name__ == '__main__':
    unittest.main()
