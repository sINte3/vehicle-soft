# -*- coding: utf-8 -*-
"""SPARE-PARTS-CYCLE-2-3 Part 8: forward-going audit event completion.

Creates the audit_logs table in the disposable test DB (same DDL as the
SEC-003A migration) so log_audit actually writes rows here.
"""
import unittest
from datetime import date

from sqlalchemy import text

from tests.harness import app, db, reset_db, create_admin, create_org, login, CSRF
from models import (User, Organization, SparePartRequest, SparePartRequestItem,
                    SparePartWriteOffAct, UserModulePermission, ROLE_OPERATOR)

AUDIT_DDL = """
    CREATE TABLE IF NOT EXISTS audit_logs (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        created_at TEXT NOT NULL,
        user_id INTEGER, username_snapshot TEXT, full_name_snapshot TEXT,
        role_snapshot TEXT, action TEXT NOT NULL, entity_type TEXT,
        entity_id INTEGER, entity_label TEXT, module TEXT, route TEXT,
        method TEXT, ip_address TEXT, user_agent TEXT, before_json TEXT,
        after_json TEXT, changes_json TEXT, status TEXT DEFAULT 'ok',
        description TEXT
    )
"""


class AuditCompletionTests(unittest.TestCase):
    def setUp(self):
        reset_db()
        self.admin_id = create_admin()
        self.org_id = create_org()
        with app.app_context():
            db.session.execute(text(AUDIT_DDL))
            # audit_logs is not a model — drop_all/create_all never resets it,
            # so clear it explicitly to isolate each test.
            db.session.execute(text("DELETE FROM audit_logs"))
            req = SparePartRequest(request_date=date.today(),
                                   organization_id=self.org_id,
                                   status='submitted', created_by=self.admin_id)
            db.session.add(req)
            db.session.flush()
            item = SparePartRequestItem(request_id=req.id, name='Фильтр',
                                        quantity=1, unit='dona')
            db.session.add(item)
            db.session.flush()
            act = SparePartWriteOffAct(act_number='SPW-2026-00009',
                                       request_id=req.id,
                                       organization_id=self.org_id,
                                       issued_date=date.today(),
                                       issued_by=self.admin_id)
            db.session.add(act)
            # Operator with base module access only — no approve/price/acts.
            op = User(username='op', role=ROLE_OPERATOR)
            op.set_password('x')
            op.organizations.append(Organization.query.get(self.org_id))
            db.session.add(op)
            db.session.flush()
            db.session.add(UserModulePermission(
                user_id=op.id, module_code='spare_parts', has_access=True))
            db.session.commit()
            self.rid, self.item_id, self.act_id, self.op_id = \
                req.id, item.id, act.id, op.id

    def _rows(self, action):
        with app.app_context():
            return db.session.execute(
                text("SELECT action, status, description FROM audit_logs "
                     "WHERE action = :a"), {'a': action}).fetchall()

    def _client(self, user_id):
        client = app.test_client()
        login(client, user_id)
        return client

    def test_price_save_already_audited_contract_holds(self):
        client = self._client(self.admin_id)
        resp = client.post(
            '/spare-parts/{}/items/{}/price/set'.format(self.rid, self.item_id),
            data={'csrf_token': CSRF, 'price': '1500'})
        self.assertEqual(resp.status_code, 302)
        self.assertEqual(len(self._rows('spare_part_item_price_set')), 1)

    def test_rejection_audited_contract_holds(self):
        client = self._client(self.admin_id)
        resp = client.post('/spare-parts/{}/reject'.format(self.rid),
                           data={'csrf_token': CSRF, 'review_comment': 'нет'})
        self.assertEqual(resp.status_code, 302)
        rows = self._rows('spare_part_request_status_changed')
        self.assertTrue(any('rejected' in (r[2] or '') for r in rows))

    def test_denied_approve_logged(self):
        client = self._client(self.op_id)
        resp = client.post('/spare-parts/{}/approve'.format(self.rid),
                           data={'csrf_token': CSRF})
        self.assertEqual(resp.status_code, 403)
        rows = self._rows('spare_parts_access_denied')
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0][1], 'denied')
        self.assertIn('approve request', rows[0][2])

    def test_denied_act_view_logged(self):
        client = self._client(self.op_id)
        resp = client.get('/spare-parts/acts/{}'.format(self.act_id))
        self.assertEqual(resp.status_code, 403)
        rows = self._rows('spare_parts_access_denied')
        self.assertTrue(any('view act' in (r[2] or '') for r in rows))

    def test_denied_catalog_mutation_logged(self):
        client = self._client(self.op_id)
        resp = client.post('/spare-parts/catalog/save',
                           data={'csrf_token': CSRF, 'name': 'X', 'unit': 'dona'})
        self.assertEqual(resp.status_code, 403)
        rows = self._rows('spare_parts_access_denied')
        self.assertTrue(any('save catalog part' in (r[2] or '') for r in rows))

    def test_denied_price_action_logged(self):
        client = self._client(self.op_id)
        resp = client.post(
            '/spare-parts/{}/items/{}/price/confirm'.format(self.rid, self.item_id),
            data={'csrf_token': CSRF})
        self.assertEqual(resp.status_code, 403)
        rows = self._rows('spare_parts_access_denied')
        self.assertTrue(any('price action' in (r[2] or '') for r in rows))

    def test_permitted_actions_do_not_write_denial_rows(self):
        client = self._client(self.admin_id)
        client.get('/spare-parts/acts/{}'.format(self.act_id))
        client.post('/spare-parts/{}/items/{}/price/set'.format(self.rid, self.item_id),
                    data={'csrf_token': CSRF, 'price': '10'})
        self.assertEqual(self._rows('spare_parts_access_denied'), [])


if __name__ == '__main__':
    unittest.main()
