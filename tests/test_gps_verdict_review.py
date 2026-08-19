# -*- coding: utf-8 -*-
"""GPS-6: журнал разбора вердиктов -- четыре пути миграции и поведение экрана.

Что проверяется и почему именно это:

1. **Журнал только пополняется.** Второй разбор того же наряда обязан
   ДОБАВИТЬ строку, а не заменить прежнюю. История и есть тот набор, по
   которому раз в сезон пересматриваются допуски; замена вместо добавления
   уничтожала бы его молча, и экран при этом выглядел бы правильно.

2. **Числа лежат снимком.** Пересчёт суток не имеет права переписать то, что
   было перед глазами человека в момент разбора. Проверяется прямо: сначала
   разбор, потом пересчёт с другой площадью, потом чтение журнала.

3. **Разбор не меняет ни одного числа.** Ни агрегат, ни полигоны, ни объём
   наряда после разбора не отличаются ни на бит.

4. **Возражение без объяснения не принимается.** Ради объяснений журнал и
   ведётся.

Запуск:
  python -m unittest tests.test_gps_verdict_review -v
"""
import os
import sqlite3
import sys
import tempfile
import unittest

from datetime import date, datetime

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if REPO_ROOT not in sys.path:
    sys.path.insert(0, REPO_ROOT)

from tests.harness import app, db, reset_db, create_admin, create_org, login, CSRF
from models import (Equipment, GpsDailyAggregate, GpsVerdict, GpsWorkPolygon,
                    Organization, User, UserModulePermission, VialonMapping,
                    WorkOrder, WO_STATUS_DONE, ROLE_OPERATOR)

import migration_utils
import migrate_gps_verdicts_001 as verdicts_mig

DAY = date(2026, 7, 27)
SQUARE = '{"type": "Polygon", "coordinates": [[[64.55, 39.99], [64.56, 39.99],' \
         ' [64.56, 40.0], [64.55, 40.0], [64.55, 39.99]]]}'

PRECONDITION_DDL = {
    'users': 'CREATE TABLE users (id INTEGER PRIMARY KEY, username TEXT)',
    'work_orders': 'CREATE TABLE work_orders (id INTEGER PRIMARY KEY, '
                   'number TEXT)',
}


def _query(path, sql, args=()):
    con = sqlite3.connect(path)
    try:
        return con.execute(sql, args).fetchall()
    finally:
        con.close()


def _tables(path):
    return {r[0] for r in _query(
        path, "SELECT name FROM sqlite_master WHERE type='table'")}


def _registry_rows(path, migration_id):
    present = _query(path, "SELECT name FROM sqlite_master WHERE type='table' "
                           "AND name='schema_migrations'")
    if not present:
        return 0
    return _query(path, 'SELECT COUNT(*) FROM schema_migrations WHERE name=?',
                  (migration_id,))[0][0]


class MigrationPaths(unittest.TestCase):
    """Четыре пути, которых устав требует от каждой миграции."""

    def setUp(self):
        self.folder = tempfile.mkdtemp()
        self.db = os.path.join(self.folder, 'transport.db')
        self._saved = (verdicts_mig.DB_PATH, migration_utils.DB_PATH)
        verdicts_mig.DB_PATH = self.db
        migration_utils.DB_PATH = self.db

    def tearDown(self):
        verdicts_mig.DB_PATH, migration_utils.DB_PATH = self._saved

    def _make_db(self, tables=('users', 'work_orders')):
        con = sqlite3.connect(self.db)
        for table in tables:
            con.execute(PRECONDITION_DDL[table])
        con.commit()
        con.close()

    def test_clean_database_creates_the_table_and_records_itself(self):
        self._make_db()
        verdicts_mig.run()
        self.assertIn('gps_verdicts', _tables(self.db))
        self.assertEqual(_registry_rows(self.db, verdicts_mig.MIGRATION_ID), 1)
        columns = [r[1] for r in _query(self.db,
                                        'PRAGMA table_info(gps_verdicts)')]
        self.assertEqual(sorted(columns),
                         sorted(verdicts_mig.EXPECTED_COLUMNS))

    def test_the_journal_carries_no_uniqueness_rule(self):
        # [REASON]: второй разбор того же наряда -- норма, ради него журнал и
        # заведён. UNIQUE здесь превратил бы историю в одну строку.
        self._make_db()
        verdicts_mig.run()
        unique = [r for r in _query(self.db, 'PRAGMA index_list(gps_verdicts)')
                  if r[2]]
        self.assertEqual(unique, [])

    def test_second_run_is_a_no_op(self):
        self._make_db()
        verdicts_mig.run()
        verdicts_mig.run()
        self.assertEqual(_registry_rows(self.db, verdicts_mig.MIGRATION_ID), 1)

    def test_missing_database_exits_with_code_2_and_creates_no_file(self):
        with self.assertRaises(SystemExit) as caught:
            verdicts_mig.run()
        self.assertEqual(caught.exception.code, 2)
        self.assertFalse(os.path.exists(self.db))

    def test_missing_work_orders_rolls_everything_back(self):
        self._make_db(tables=('users',))
        with self.assertRaises(SystemExit) as caught:
            verdicts_mig.run()
        self.assertEqual(caught.exception.code, 1)
        self.assertNotIn('gps_verdicts', _tables(self.db))
        self.assertEqual(_registry_rows(self.db, verdicts_mig.MIGRATION_ID), 0)

    def test_the_model_and_the_migration_describe_one_table(self):
        model = {c.name for c in GpsVerdict.__table__.columns}
        self.assertEqual(model, set(verdicts_mig.EXPECTED_COLUMNS))
        self.assertEqual(GpsVerdict.__tablename__, 'gps_verdicts')


class Review(unittest.TestCase):
    def setUp(self):
        reset_db()
        self.admin_id = create_admin()
        self.org_id = create_org()
        with app.app_context():
            User.query.get(self.admin_id).language = 'ru'
            equipment = Equipment(name='МТЗ 261 EA', category='tractor',
                                  organization_id=self.org_id)
            db.session.add(equipment)
            db.session.flush()
            self.equipment_id = equipment.id
            order = WorkOrder(
                number='WO-1', organization_id=self.org_id,
                equipment_id=self.equipment_id, created_by=self.admin_id,
                status=WO_STATUS_DONE, planned_date=DAY, actual_date=DAY,
                unit='ga', planned_quantity=20.0, actual_quantity=20.0,
                work_type_text='культивация')
            db.session.add(order)
            db.session.add(VialonMapping(vialon_name='МТЗ 261 EA',
                                         wialon_id=3464,
                                         equipment_id=self.equipment_id,
                                         skip=False))
            db.session.flush()
            self.order_id = order.id
            db.session.commit()
        self._compute(8.772)

    def _compute(self, area):
        """Переписать суточный расчёт так, как это сделал бы пересчёт."""
        with app.app_context():
            GpsDailyAggregate.query.delete()
            GpsWorkPolygon.query.delete()
            db.session.add(GpsDailyAggregate(
                work_date=DAY, wialon_id=3464, points_total=1598,
                points_work=990, track_km=32.1, interval_median_s=30.0,
                sats_median=14.0, motion_gaps=0, lost_seconds=0.0, gps_jumps=0,
                reason=None, method_version='adaptive-alpha-2026-08-12',
                computed_at=datetime(2026, 7, 28, 3, 0, 0)))
            db.session.add(GpsWorkPolygon(
                work_date=DAY, wialon_id=3464, site_number=1, area_ha=area,
                minutes=317.4, polygon_geojson=SQUARE,
                suggested_label='работа'))
            db.session.commit()

    def _post(self, decision, comment='', user_id=None, order_id=None):
        client = app.test_client()
        login(client, user_id or self.admin_id)
        return client.post('/gps/orders/review', data={
            'csrf_token': CSRF, 'order_id': order_id or self.order_id,
            'decision': decision, 'comment': comment})

    def _journal(self):
        with app.app_context():
            return GpsVerdict.query.order_by(GpsVerdict.id).all()

    def test_a_review_is_written_with_the_numbers_as_they_stood(self):
        self.assertEqual(self._post('подтверждён').status_code, 302)
        rows = self._journal()
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0].decision, 'подтверждён')
        self.assertEqual(rows[0].verdict, 'fail')
        self.assertAlmostEqual(rows[0].base_quantity, 20.0)
        self.assertAlmostEqual(rows[0].fact_ha, 8.772)
        self.assertAlmostEqual(rows[0].deviation, -11.228, places=3)
        self.assertEqual(rows[0].reviewed_by, self.admin_id)

    def test_a_second_review_is_added_not_substituted(self):
        self._post('подтверждён')
        self._post('оспорен', 'механик говорит, что поле обработано целиком')
        rows = self._journal()
        self.assertEqual(len(rows), 2)
        self.assertEqual([r.decision for r in rows],
                         ['подтверждён', 'оспорен'])

    def test_a_recomputation_does_not_rewrite_what_a_person_saw(self):
        self._post('подтверждён')
        self._compute(15.0)          # сутки пересчитаны, площадь другая
        rows = self._journal()
        self.assertAlmostEqual(rows[0].fact_ha, 8.772,
                               msg='пересчёт переписал снимок в журнале')
        self.assertAlmostEqual(rows[0].deviation, -11.228, places=3)

    def test_a_review_changes_no_number_anywhere(self):
        with app.app_context():
            before = (GpsDailyAggregate.query.one().track_km,
                      GpsWorkPolygon.query.one().area_ha,
                      WorkOrder.query.get(self.order_id).actual_quantity)
        self._post('оспорен', 'проверить по литрам препарата')
        with app.app_context():
            after = (GpsDailyAggregate.query.one().track_km,
                     GpsWorkPolygon.query.one().area_ha,
                     WorkOrder.query.get(self.order_id).actual_quantity)
        self.assertEqual(before, after)

    def test_a_dispute_without_a_reason_is_refused(self):
        response = self._post('оспорен', '')
        self.assertEqual(response.status_code, 302)
        self.assertEqual(self._journal(), [])

    def test_a_confirmation_needs_no_reason(self):
        self._post('подтверждён', '')
        self.assertEqual(len(self._journal()), 1)

    def test_a_decision_the_screen_never_offers_is_refused(self):
        self.assertEqual(self._post('не знаю').status_code, 400)
        self.assertEqual(self._journal(), [])

    def test_an_unknown_order_is_a_404(self):
        self.assertEqual(self._post('подтверждён', order_id=999999).status_code,
                         404)

    def test_another_organizations_order_cannot_be_reviewed(self):
        with app.app_context():
            other = Organization(name='Чужая организация')
            db.session.add(other)
            db.session.flush()
            operator = User(username='gps_reader', role=ROLE_OPERATOR,
                            language='ru')
            operator.set_password('x')
            operator.organizations.append(other)
            db.session.add(operator)
            db.session.flush()
            db.session.add(UserModulePermission(
                user_id=operator.id, module_code='wialon', has_access=True))
            db.session.commit()
            operator_id = operator.id
        self.assertEqual(self._post('подтверждён', user_id=operator_id
                                    ).status_code, 403)
        self.assertEqual(self._journal(), [])

    def test_the_screen_shows_the_latest_review_and_counts_the_rest(self):
        client = app.test_client()
        login(client, self.admin_id)
        html = client.get('/gps/orders?from=2026-07-01&to=2026-07-31'
                          ).get_data(as_text=True)
        self.assertIn('Ждут разбора', html)
        self.assertIn('Подтвердить', html)

        self._post('оспорен', 'спорная граница поля')
        html = client.get('/gps/orders?from=2026-07-01&to=2026-07-31'
                          ).get_data(as_text=True)
        self.assertIn('спорная граница поля', html)
        self.assertIn('оспорен', html)

    def test_the_unreviewed_filter_hides_what_is_already_looked_at(self):
        client = app.test_client()
        login(client, self.admin_id)
        self._post('подтверждён')
        html = client.get('/gps/orders?from=2026-07-01&to=2026-07-31'
                          '&unreviewed=1').get_data(as_text=True)
        self.assertNotIn('WO-1', html)

    def test_a_green_order_is_not_offered_for_review(self):
        # Разбирают исключения. Наряд в допуске в очередь не попадает.
        with app.app_context():
            WorkOrder.query.get(self.order_id).actual_quantity = 8.5
            db.session.commit()
        client = app.test_client()
        login(client, self.admin_id)
        html = client.get('/gps/orders?from=2026-07-01&to=2026-07-31'
                          ).get_data(as_text=True)
        self.assertIn('vs-badge-success', html)
        self.assertNotIn('Подтвердить', html)


if __name__ == '__main__':
    unittest.main()
