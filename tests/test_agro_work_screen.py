# -*- coding: utf-8 -*-
"""agro-work B3: экран сверки на настоящем приложении.

Что проверяется и почему:

1. **Модуль не установлен -- страница, а не 500.** Таблицы agro_work_*
   создаёт только миграция (ORM-моделей у них нет намеренно), и экран
   откроется на сервере раньше, чем её применят.
2. **Числа экрана -- числа ядра.** Экран ничего не считает сам: свод,
   списки и карточка машины показывают то, что вернула
   `agro_work.reconcile`. Проверяется на одной синтетической базе.
3. **Нарушения видно.** «Работы не было» и «работа без заявки» стоят в
   списках первыми и ведут из свода ссылкой.
4. **Права и охват.** Без права `wialon` -- 403 и нет пункта в сайдбаре;
   не-администратор видит только технику своих организаций, а заявки
   несвязанных машин не видит вовсе.
5. **Экран только читает.** Байты базы до и после обхода всех страниц
   одинаковы.
6. **Копия подписей категорий** в `agro_work/labels.py` совпадает с
   `models.CATEGORIES` -- отчёт xlsx не может импортировать models.

Запуск: python -m unittest tests.test_agro_work_screen -v
"""

import hashlib
import re
import sqlite3
import unittest

from tests.harness import (app, db, reset_db, create_admin, create_org, login,
                           TEST_DB_PATH)
from models import (CATEGORIES, Equipment, Organization, User,
                    UserModulePermission, VialonMapping, ROLE_OPERATOR)

import migrate_agro_work_001 as agro_mig
from agro_work import labels

TODAY_RANGE = {'from': '2026-09-10', 'to': '2026-09-20'}


def _sql(statements, args=()):
    con = sqlite3.connect(TEST_DB_PATH)
    try:
        for sql in statements:
            con.execute(sql, args)
        con.commit()
    finally:
        con.close()


def drop_agro_tables():
    con = sqlite3.connect(TEST_DB_PATH)
    try:
        for table in list(agro_mig.EXPECTED_COLUMNS)[::-1]:
            con.execute('DROP TABLE IF EXISTS %s' % table)
        con.commit()
    finally:
        con.close()


def install_agro_tables():
    drop_agro_tables()
    con = sqlite3.connect(TEST_DB_PATH)
    try:
        for _, ddl in agro_mig.TABLES:
            con.execute(ddl)
        for _, ddl, _ in agro_mig.INDEXES:
            con.execute(ddl)
        for _, ddl in agro_mig.TRIGGERS:
            con.execute(ddl)
        con.commit()
    finally:
        con.close()


def sha():
    with open(TEST_DB_PATH, 'rb') as fh:
        return hashlib.sha256(fh.read()).hexdigest()


class ScreenCase(unittest.TestCase):
    def setUp(self):
        reset_db()
        self.admin_id = create_admin()
        with app.app_context():
            self.org1 = Organization(name='Buxoro agroklaster')
            self.org2 = Organization(name='Jizzax filial')
            db.session.add_all([self.org1, self.org2])
            db.session.commit()
            self.org1_id, self.org2_id = self.org1.id, self.org2.id
            self.eq1 = Equipment(name='МТЗ-80.1', plate='80 001 EA',
                                 category='mtz', organization_id=self.org1_id)
            self.eq2 = Equipment(name='МТЗ-82', plate='80 002 EA',
                                 category='mtz', organization_id=self.org1_id)
            self.eq3 = Equipment(name='Case', plate='25 003 GA',
                                 category='yukori', organization_id=self.org2_id)
            db.session.add_all([self.eq1, self.eq2, self.eq3])
            db.session.commit()
            self.eq1_id, self.eq2_id, self.eq3_id = (self.eq1.id, self.eq2.id,
                                                     self.eq3.id)
            db.session.add_all([
                VialonMapping(vialon_name='u1', wialon_id=1001,
                              equipment_id=self.eq1_id, skip=False),
                VialonMapping(vialon_name='u2', wialon_id=1002,
                              equipment_id=self.eq2_id, skip=False),
                VialonMapping(vialon_name='u3', wialon_id=1003,
                              equipment_id=self.eq3_id, skip=False)])
            db.session.commit()
        install_agro_tables()
        _sql(["INSERT INTO agro_work_import_runs (id, started_at, finished_at, "
              "status, tool_version, rows_seen, rows_new, history_pending) "
              "VALUES (1, '2026-09-21 03:00:00', '2026-09-21 03:05:00', 'ok', "
              "'agro-work-import-1', 4, 4, 0)"])
        transports = (('T1', 'P1', self.eq1_id, 'auto'),
                      ('T2', 'P2', self.eq2_id, 'auto'),
                      ('T3', 'P3', self.eq3_id, 'auto'),
                      ('T8', 'ALFAKLAS12', None, 'none'))
        for transport_id, plate, eq_id, status in transports:
            _sql(["INSERT INTO agro_work_transports (id, plate_number, "
                  "plate_norm, equipment_id, match_status, first_seen_at, "
                  "last_seen_at, company_name) VALUES (?, ?, ?, ?, ?, 't', 't', "
                  "'Buxoro agroklaster')"], (transport_id, plate, plate, eq_id,
                                              status))
        _sql(["INSERT INTO agro_work_work_types (id, name, unit, method, "
              "first_seen_at, last_seen_at) VALUES ('W1', 'Култивация', "
              "'HECTARE', 'ga', 't', 't')"])
        # T1: работа 10-го и заявка 10..11 -> «работа была»;
        # T1: заявка 12..13 без работы -> «работы не было»;
        # T2: работа 13-го без заявки -> «работа без заявки»;
        # T8: машина не связана -> «без вердикта», видна только админу;
        # T3 (организация 2): работа 15-го и заявка 15..15.
        for number, transport, created, completed in (
                (1, 'T1', 10, 11), (2, 'T1', 12, 13), (3, 'T8', 10, 11),
                (4, 'T3', 15, 15)):
            _sql(["INSERT INTO agro_work_applications (id, application_number, "
                  "transport_id, work_type_id, work_type_name, unit, status, "
                  "created_at, updated_at, created_day, first_seen_run_id, "
                  "last_seen_run_id, history_updated_at, initial_status, "
                  "completed_day, plate_number) VALUES (?, ?, ?, 'W1', "
                  "'Култивация', 'HECTARE', 'COMPLETED', ?, 'u', ?, 1, 1, 'u', "
                  "'IN_PROGRESS', ?, ?)"],
                 ('A%d' % number, 'APP-TEST-%03d' % number, transport,
                  '2026-09-%02dT08:00:00+05:00' % created,
                  '2026-09-%02d' % created, '2026-09-%02d' % completed,
                  'P' + transport))
        con = sqlite3.connect(TEST_DB_PATH)
        try:
            for unit, day, area in ((1001, 10, 2.5), (1002, 13, 3.0),
                                    (1003, 15, 7.0)):
                con.execute("INSERT INTO gps_daily_aggregates (work_date, "
                            "wialon_id, points_total, points_work, track_km, "
                            "motion_gaps, lost_seconds, gps_jumps, "
                            "method_version, computed_at) VALUES (?, ?, 100, "
                            "50, 1.0, 0, 0, 0, 'v', '2026-09-21 03:00:00')",
                            ('2026-09-%02d' % day, unit))
                con.execute("INSERT INTO gps_work_polygons (work_date, "
                            "wialon_id, site_number, area_ha, minutes, "
                            "polygon_geojson) VALUES (?, ?, 1, ?, 60, '{}')",
                            ('2026-09-%02d' % day, unit, area))
            for unit, day in ((1001, 11), (1001, 12), (1001, 13)):
                con.execute("INSERT INTO gps_daily_aggregates (work_date, "
                            "wialon_id, points_total, points_work, track_km, "
                            "motion_gaps, lost_seconds, gps_jumps, "
                            "method_version, computed_at) VALUES (?, ?, 100, "
                            "50, 1.0, 0, 0, 0, 'v', 'now')",
                            ('2026-09-%02d' % day, unit))
            con.commit()
        finally:
            con.close()
        self.client = app.test_client()

    def get(self, url, user_id=None, lang=None, **params):
        login(self.client, user_id or self.admin_id)
        if lang:
            with app.app_context():
                user = db.session.get(User, user_id or self.admin_id)
                user.language = lang
                db.session.commit()
        query = dict(TODAY_RANGE)
        query.update(params)
        return self.client.get(url, query_string=query)

    def operator(self, org_ids, wialon=True):
        with app.app_context():
            user = User(username='op%d' % len(org_ids), role=ROLE_OPERATOR,
                        full_name='Operator')
            user.set_password('x')
            user.organizations = [db.session.get(Organization, i) for i in org_ids]
            db.session.add(user)
            db.session.commit()
            if wialon:
                db.session.add(UserModulePermission(user_id=user.id,
                                                    module_code='wialon',
                                                    has_access=True))
                db.session.commit()
            return user.id


class NotInstalled(ScreenCase):
    def test_every_page_says_not_installed_instead_of_failing(self):
        drop_agro_tables()
        for url in ('/agro-work/', '/agro-work/applications',
                    '/agro-work/work-days', '/agro-work/import',
                    '/agro-work/machine/%d' % 1):
            response = self.get(url, lang='ru')
            self.assertEqual(response.status_code, 200, url)
            self.assertIn('ещё не установлен', response.get_data(as_text=True), url)


class Numbers(ScreenCase):
    def test_dashboard_shows_both_violations_and_links_to_them(self):
        html = self.get('/agro-work/', lang='ru').get_data(as_text=True)
        self.assertIn('Заявки agro-work против работы по GPS', html)
        self.assertIn('Buxoro agroklaster', html)
        self.assertIn('Jizzax filial', html)
        self.assertIn('verdict=rabota_net', html)
        self.assertIn('coverage=bez_zayavki', html)
        self.assertIn('Машина agro-work не связана с нашей техникой', html)

    def test_applications_list_puts_the_violation_first(self):
        html = self.get('/agro-work/applications', lang='ru').get_data(as_text=True)
        order = [m for m in re.findall(r'APP-TEST-\d{3}', html)]
        self.assertEqual(order[0], 'APP-TEST-002')
        self.assertIn('Работы по GPS не было', html)
        self.assertIn('Машина agro-work не связана с нашей техникой', html)
        only = self.get('/agro-work/applications', verdict='rabota_net')
        self.assertEqual(re.findall(r'APP-TEST-\d{3}', only.get_data(as_text=True)),
                         ['APP-TEST-002'])

    def test_work_days_list_names_the_uncovered_day(self):
        html = self.get('/agro-work/work-days', lang='ru',
                        coverage='bez_zayavki').get_data(as_text=True)
        self.assertIn('13.09.2026', html)
        self.assertIn('Работа без заявки', html)
        self.assertIn('/gps/fact?date=2026-09-13', html)
        self.assertNotIn('10.09.2026', html)

    def test_machine_page_shows_days_and_applications(self):
        html = self.get('/agro-work/machine/%d' % self.eq1_id,
                        lang='ru').get_data(as_text=True)
        self.assertIn('80 001 EA', html)
        self.assertIn('APP-TEST-001', html)
        self.assertIn('APP-TEST-002', html)
        self.assertIn('работы нет', html)
        self.assertIn('2.5', html)
        self.assertEqual(self.get('/agro-work/machine/99999').status_code, 404)

    def test_import_page_lists_runs_unmatched_and_work_types(self):
        html = self.get('/agro-work/import', lang='ru').get_data(as_text=True)
        self.assertIn('Прогоны импорта', html)
        self.assertIn('21.09.2026 08:00', html)       # 03:00 UTC -> UTC+5
        self.assertIn('ALFAKLAS12', html)
        self.assertIn('Култивация', html)
        self.assertIn('гектары', html)

    def test_uzbek_interface_has_no_russian_headings(self):
        html = self.get('/agro-work/', lang='uz').get_data(as_text=True)
        self.assertIn('agro-work буюртмалари GPS бўйича ишга қарши', html)
        self.assertNotIn('Заявки agro-work против работы по GPS', html)

    def test_the_screen_writes_nothing(self):
        before = sha()
        for url in ('/agro-work/', '/agro-work/applications',
                    '/agro-work/work-days', '/agro-work/import',
                    '/agro-work/machine/%d' % self.eq1_id):
            self.assertEqual(self.get(url).status_code, 200, url)
        self.assertEqual(sha(), before)


class Access(ScreenCase):
    def test_without_the_wialon_permission_every_page_is_403(self):
        user_id = self.operator([self.org1_id], wialon=False)
        for url in ('/agro-work/', '/agro-work/applications',
                    '/agro-work/work-days', '/agro-work/import',
                    '/agro-work/machine/%d' % self.eq1_id):
            self.assertEqual(self.get(url, user_id=user_id).status_code, 403, url)
        html = self.client.get('/').get_data(as_text=True)
        self.assertNotIn('/agro-work/', html)

    def test_an_operator_sees_only_his_organisation(self):
        user_id = self.operator([self.org2_id])
        html = self.get('/agro-work/applications', user_id=user_id).get_data(
            as_text=True)
        self.assertEqual(re.findall(r'APP-TEST-\d{3}', html), ['APP-TEST-004'])
        html = self.get('/agro-work/', user_id=user_id).get_data(as_text=True)
        self.assertNotIn('Buxoro agroklaster', html)
        self.assertEqual(self.get('/agro-work/machine/%d' % self.eq1_id,
                                  user_id=user_id).status_code, 403)
        html = self.get('/agro-work/import', user_id=user_id).get_data(
            as_text=True)
        self.assertNotIn('ALFAKLAS12', html)          # несвязанные -- админу

    def test_the_sidebar_link_and_the_strip_show_the_same_pages(self):
        html = self.get('/agro-work/').get_data(as_text=True)
        strip = re.search(r'<nav class="vs-pills vs-modulenav vs-mb".*?</nav>',
                          html, re.S).group(0)
        strip_hrefs = sorted(set(re.findall(r'href="([^"]+)"', strip)))
        side_hrefs = sorted(set(re.findall(
            r'<a [^>]*?href="([^"]+)"[^>]*class="vs-side-sublink', html)))
        self.assertEqual(strip_hrefs, side_hrefs)
        self.assertEqual(len(strip_hrefs), 4)


class Copies(unittest.TestCase):
    def test_category_labels_match_models(self):
        self.assertEqual(labels.CATEGORIES, CATEGORIES)


if __name__ == '__main__':
    unittest.main()
