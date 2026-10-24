# -*- coding: utf-8 -*-
"""Moving a spelling to another customer does not move the jobs already stored.

FIN-RECON-002 audit, 24.10.2026: the screen promised the stored jobs would
«перепривяжутся при следующем импорте». The importer skips every row it
already has, so they never do. The test pins the behaviour (the job keeps its
customer) and the sentence that now describes it -- in both languages.

Run:
  python -m unittest tests.test_drone_alias_move_does_not_relink -v
"""
import datetime
import unittest

from tests.harness import app, reset_db, create_admin, login, CSRF
from models import db, DroneCustomer, DroneCustomerAlias, DroneWork, User


class AliasMoveTest(unittest.TestCase):

    def setUp(self):
        reset_db()
        self.admin = create_admin('alias_admin')
        with app.app_context():
            a = DroneCustomer(name='Биринчи фх', customer_type='external', is_active=True)
            b = DroneCustomer(name='Иккинчи фх', customer_type='external', is_active=True)
            db.session.add_all([a, b])
            db.session.flush()
            alias = DroneCustomerAlias(raw_name='Биринчи', normalized_key='биринчи', drone_customer_id=a.id,
                                       is_active=True)
            db.session.add(alias)
            db.session.add(DroneWork(period_month='2026-06', work_date_from=datetime.date(2026, 6, 1),
                                     drone_customer_id=a.id, customer_raw='Биринчи', area_ha=5.0, amount=1000000.0,
                                     payment_type='transfer', source_file='b.xlsx', source_sheet='свод ичи (Т)',
                                     source_row=4))
            db.session.commit()
            self.a, self.b, self.alias = a.id, b.id, alias.id
        self.client = app.test_client()
        login(self.client, self.admin)

    def move(self, lang):
        with app.app_context():
            db.session.get(User, self.admin).language = lang
            db.session.commit()
        response = self.client.post('/drones/customers/aliases/%d/update' % self.alias, data={
            'csrf_token': CSRF, 'drone_customer_id': self.b, 'is_active': 'on'}, follow_redirects=True)
        self.assertEqual(response.status_code, 200)
        return response.get_data(as_text=True)

    def test_the_stored_job_keeps_its_customer_and_the_screen_says_so(self):
        body = self.move('ru')
        with app.app_context():
            self.assertEqual(DroneCustomerAlias.query.get(self.alias).drone_customer_id, self.b)
            self.assertEqual(DroneWork.query.one().drone_customer_id, self.a)
        self.assertIn('повторным импортом не перепривязываются', body)
        self.assertNotIn('перепривяжутся при следующем импорте', body)

    def test_the_uzbek_sentence_says_the_same(self):
        body = self.move('uz')
        self.assertIn('қайта импортда ҳам қайта боғланмайди', body)
        self.assertNotIn('келгуси импортда қайта боғланади', body)


if __name__ == '__main__':
    unittest.main()
