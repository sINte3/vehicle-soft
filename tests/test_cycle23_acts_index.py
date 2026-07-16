# -*- coding: utf-8 -*-
"""SPARE-PARTS-CYCLE-2-3 Part 5: standalone acts index page."""
import unittest
from datetime import date

from tests.harness import app, db, reset_db, create_admin, create_org, login
from models import (User, Organization, SparePartRequest, SparePartWriteOffAct,
                    SparePartWriteOffActItem, UserModulePermission,
                    ROLE_OPERATOR)


def _make_act(org_id, number, issued, created_by, totals=(100.0,)):
    req = SparePartRequest(request_date=issued, organization_id=org_id,
                           status='issued', created_by=created_by)
    db.session.add(req)
    db.session.flush()
    act = SparePartWriteOffAct(act_number=number, request_id=req.id,
                               organization_id=org_id, issued_date=issued,
                               issued_by=created_by)
    db.session.add(act)
    db.session.flush()
    for t in totals:
        db.session.add(SparePartWriteOffActItem(
            act_id=act.id, name='Деталь', quantity=1, unit='dona',
            price=t, total=t))
    return act


class ActsIndexTests(unittest.TestCase):
    def setUp(self):
        reset_db()
        self.admin_id = create_admin()
        self.org1 = create_org('Org One')
        self.org2 = create_org('Org Two')
        with app.app_context():
            _make_act(self.org1, 'SPW-2026-00001', date(2026, 7, 1),
                      self.admin_id, totals=(150.0, 50.0))
            _make_act(self.org2, 'SPW-2026-00002', date(2026, 7, 10),
                      self.admin_id)
            # Non-admin operator: org1 only, base module + acts permission.
            op = User(username='op', role=ROLE_OPERATOR, full_name='Op')
            op.set_password('x')
            op.organizations.append(Organization.query.get(self.org1))
            db.session.add(op)
            db.session.flush()
            db.session.add(UserModulePermission(
                user_id=op.id, module_code='spare_parts', has_access=True))
            db.session.add(UserModulePermission(
                user_id=op.id, module_code='spare_parts_acts', has_access=True))
            # Operator with base module but WITHOUT the acts permission.
            noacts = User(username='noacts', role=ROLE_OPERATOR)
            noacts.set_password('x')
            noacts.organizations.append(Organization.query.get(self.org1))
            db.session.add(noacts)
            db.session.flush()
            db.session.add(UserModulePermission(
                user_id=noacts.id, module_code='spare_parts', has_access=True))
            db.session.commit()
            self.op_id = op.id
            self.noacts_id = noacts.id

    def _get(self, user_id, path='/spare-parts/acts'):
        client = app.test_client()
        login(client, user_id)
        return client.get(path)

    def test_admin_sees_all_acts_with_totals(self):
        resp = self._get(self.admin_id)
        self.assertEqual(resp.status_code, 200)
        html = resp.get_data(as_text=True)
        self.assertIn('SPW-2026-00001', html)
        self.assertIn('SPW-2026-00002', html)
        self.assertIn('200', html)  # 150 + 50 grand total of act 1

    def test_requires_acts_permission_like_act_detail(self):
        self.assertEqual(self._get(self.noacts_id).status_code, 403)

    def test_org_scoping_hides_foreign_acts(self):
        resp = self._get(self.op_id)
        self.assertEqual(resp.status_code, 200)
        html = resp.get_data(as_text=True)
        self.assertIn('SPW-2026-00001', html)
        self.assertNotIn('SPW-2026-00002', html)

    def test_foreign_org_filter_forbidden(self):
        resp = self._get(self.op_id, '/spare-parts/acts?org_id={}'.format(self.org2))
        self.assertEqual(resp.status_code, 403)

    def test_date_filters(self):
        resp = self._get(self.admin_id,
                         '/spare-parts/acts?date_from=2026-07-05')
        html = resp.get_data(as_text=True)
        self.assertNotIn('SPW-2026-00001', html)
        self.assertIn('SPW-2026-00002', html)
        # Malformed date is ignored, never a 500.
        resp = self._get(self.admin_id, '/spare-parts/acts?date_from=garbage')
        self.assertEqual(resp.status_code, 200)


if __name__ == '__main__':
    unittest.main()
