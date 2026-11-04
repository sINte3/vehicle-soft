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
import os
import re
import sqlite3
import unittest
from unittest import mock
from datetime import date

from tests.harness import (app, db, reset_db, create_admin, create_org, login,
                           TEST_DB_PATH)
from models import (CATEGORIES, Equipment, Organization, User,
                    UserModulePermission, VialonMapping, ROLE_OPERATOR)

import migrate_agro_work_001 as agro_mig
from agro_work import labels

TODAY_RANGE = {'from': '2026-09-10', 'to': '2026-09-20'}
REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
with open(os.path.join(REPO_ROOT, 'static', 'css', 'design-system.css'),
          encoding='utf-8') as _fh:
    DESIGN_SYSTEM = _fh.read()


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


def attention(html, title):
    """Число строки «Требует внимания» с этим заголовком или None."""
    match = re.search(r'<span class="aw-attn-num[^"]*">([^<]+)</span>\s*<span>\s*'
                      r'<span class="aw-attn-title">' + re.escape(title) + '</span>',
                      html)
    return match.group(1) if match else None


def legend(html, label):
    """Число в подписи полосы «Итогов периода» у этой подписи или None."""
    match = re.search(r'>' + re.escape(label) + r'</a><b>([^<]+)</b>', html)
    return match.group(1) if match else None


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
                (4, 'T3', 15, 15), (5, 'T1', 16, 16)):
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
        # A5 удалена в agro-work (ответ владельца 11).
        _sql(["UPDATE agro_work_applications SET gone_at = "
              "'2026-09-21 03:00:00' WHERE id = 'A5'"])
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


class AnonymousVisitor(ScreenCase):
    # Дымовая проверка шага 3 ранбука выпуска (docs/AGRO_WORK_RELEASE_RUNBOOK.md)
    # открывает /agro-work/ без входа и ждёт форму входа: так видно, что раздел
    # зарегистрирован (иначе 404), но без входа ничего не показывает.
    def test_an_anonymous_visitor_is_sent_to_the_login_form(self):
        client = app.test_client()
        response = client.get('/agro-work/')
        self.assertEqual(response.status_code, 302)
        self.assertIn('/login', response.headers['Location'])
        page = client.get('/agro-work/', follow_redirects=True)
        self.assertEqual(page.status_code, 200)
        self.assertIn('vs-login-form', page.get_data(as_text=True))

    def test_the_login_page_carries_the_marker_the_smoke_check_reads(self):
        page = app.test_client().get('/login')
        self.assertEqual(page.status_code, 200)
        self.assertIn('vs-login-form', page.get_data(as_text=True))


class Numbers(ScreenCase):
    def test_dashboard_shows_both_violations_and_links_to_them(self):
        html = self.get('/agro-work/', lang='ru').get_data(as_text=True)
        self.assertIn('Требует внимания', html)
        self.assertIn('Buxoro agroklaster', html)
        self.assertIn('Jizzax filial', html)
        self.assertIn('verdict=rabota_net', html)
        self.assertIn('coverage=bez_zayavki', html)
        self.assertIn('Машина agro-work не связана с нашей техникой', html)
        # A2 закрыта без работы, 13-го T2 работала без заявки: обе -- первыми.
        self.assertEqual(attention(html, 'Заявка закрыта, а работы по GPS не было'),
                         '1')
        self.assertEqual(attention(html, 'Дни работы машин без заявки'), '1')

    def test_the_dashboard_says_time_applications_are_reconciled(self):
        # [REASON]: вердикты «время» включены 29.10 (проверка часов по
        # счётчику Wialon -- MET); примечание экрана не должно говорить, что
        # время трек GPS не публикует.
        ru = self.get('/agro-work/', lang='ru').get_data(as_text=True)
        self.assertIn('Сверяются заявки видов работ с методами «гектары» и «время» '
                      '(часы зажигания по GPS): рейсы трек GPS пока не публикует.', ru)
        self.assertNotIn('время и рейсы трек GPS пока не публикует', ru)
        uz = self.get('/agro-work/', lang='uz').get_data(as_text=True)
        self.assertIn('«Гектарлар» ва «вақт» (GPS бўйича ўт олдириш соатлари) усулидаги '
                      'иш турлари буюртмалари солиштирилади: рейсларни GPS трек ҳали '
                      'эълон қилмайди.', uz)
        self.assertNotIn('вақт ва рейсларни', uz)

    def test_applications_list_puts_the_violation_first(self):
        html = self.get('/agro-work/applications', lang='ru').get_data(as_text=True)
        order = [m for m in re.findall(r'APP-TEST-\d{3}', html)]
        self.assertEqual(order[0], 'APP-TEST-002')
        self.assertIn('Работы по GPS не было', html)
        self.assertIn('Машина agro-work не связана с нашей техникой', html)
        self.assertIn('удалена в agro-work', html)
        self.assertIn('Заявка удалена в agro-work — работу не покрывает', html)
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
        for heading in ('Эътибор талаб қилади', 'Давр якунлари',
                        'Ташкилотлар бўйича', 'Қандай ҳисобланади'):
            self.assertIn(heading, html)
        for heading in ('Требует внимания', 'Итоги периода', 'По организациям',
                        'Как считается'):
            self.assertNotIn(heading, html)

    def test_the_screen_writes_nothing(self):
        before = sha()
        for url in ('/agro-work/', '/agro-work/applications',
                    '/agro-work/work-days', '/agro-work/import',
                    '/agro-work/machine/%d' % self.eq1_id):
            self.assertEqual(self.get(url).status_code, 200, url)
        self.assertEqual(sha(), before)


class LateAndOpen(ScreenCase):
    """B4: поздняя заявка задним числом и открытые заявки на экранах."""

    def setUp(self):
        super().setUp()
        # A6: T2 заведена сразу «выполненной» 18-го -- для работы 13-го это
        # опоздание 5 суток при N = 2; A7: T1 открыта с 10-го.
        _sql(["INSERT INTO agro_work_applications (id, application_number, "
              "transport_id, work_type_id, work_type_name, unit, status, "
              "created_at, updated_at, created_day, first_seen_run_id, "
              "last_seen_run_id, history_updated_at, initial_status, "
              "completed_day, plate_number) VALUES ('A6', 'APP-TEST-006', "
              "'T2', 'W1', 'Култивация', 'HECTARE', 'COMPLETED', "
              "'2026-09-18T08:00:00+05:00', 'u', '2026-09-18', 1, 1, 'u', "
              "'COMPLETED', '2026-09-18', 'PT2')",
              "INSERT INTO agro_work_applications (id, application_number, "
              "transport_id, work_type_id, work_type_name, unit, status, "
              "created_at, updated_at, created_day, first_seen_run_id, "
              "last_seen_run_id, plate_number) VALUES ('A7', 'APP-TEST-007', "
              "'T1', 'W1', 'Култивация', 'HECTARE', 'IN_PROGRESS', "
              "'2026-09-10T08:00:00+05:00', 'u', '2026-09-10', 1, 1, 'PT1')"])
        self.days_open = (date.today() - date(2026, 9, 10)).days

    def test_the_uncovered_day_names_the_late_application(self):
        html = self.get('/agro-work/work-days', lang='ru',
                        coverage='bez_zayavki').get_data(as_text=True)
        self.assertIn('заявка APP-TEST-006 заведена задним числом через 5 сут.',
                      html)
        self.assertIn('позже, чем через 2 сут.', html)
        html = self.get('/agro-work/work-days', lang='uz',
                        coverage='bez_zayavki').get_data(as_text=True)
        self.assertIn('APP-TEST-006 буюртма 5 кундан кейин орқа сана билан '
                      'киритилган', html)
        html = self.get('/agro-work/machine/%d' % self.eq2_id,
                        lang='ru').get_data(as_text=True)
        self.assertIn('заявка APP-TEST-006 заведена задним числом через 5 сут.',
                      html)

    def test_the_dashboard_counts_it_and_lists_who_keeps_applications_open(self):
        html = self.get('/agro-work/', lang='ru').get_data(as_text=True)
        self.assertIn('Позже заведена заявка задним числом: 1', html)
        self.assertIn('after=zadnim_chislom_pozzhe', html)
        # Открытые заявки: строка «Требует внимания» называет, у кого их
        # больше, и самую старую; таблица -- сколько у каждой организации.
        self.assertEqual(attention(html, 'Заявки не закрыты операторами'), '1')
        self.assertIn('Buxoro agroklaster — 1', html)
        self.assertIn('Самая старая открыта %d сут.' % self.days_open, html)
        row = html[html.index('<td>Buxoro agroklaster</td>'):]
        row = row[:row.index('</tr>')]
        self.assertIn('reason=otkryta', row)
        self.assertIn('org=%d' % self.org1_id, row.replace('&amp;', '&'))
        self.assertIn('до %d сут.' % self.days_open, row)
        html = self.get('/agro-work/', lang='uz').get_data(as_text=True)
        self.assertEqual(attention(html, 'Операторлар ёпмаган буюртмалар'), '1')
        self.assertIn('Энг эскиси %d кундан бери очиқ.' % self.days_open, html)

    def test_an_open_application_shows_how_long_it_is_open(self):
        html = self.get('/agro-work/applications', lang='ru',
                        reason='otkryta').get_data(as_text=True)
        self.assertIn('APP-TEST-007', html)
        self.assertIn('открыта %d сут. с ввода' % self.days_open, html)
        self.assertNotIn('APP-TEST-001', html)

    @staticmethod
    def row_of(html, number, last=False):
        # last -- строка таблицы заявок на странице машины: номер выше
        # встречается ещё и в таблице суток
        at = html.rindex(number) if last else html.index(number)
        start = html.rindex('<tr', 0, at)
        return html[start:html.index('</tr>', start)]

    def test_an_open_application_is_red_and_others_are_not(self):
        # Решение владельца 09.10: открытая заявка -- бессрочно, но как
        # нарушение, красным; вердикт прежний -- «без вердикта».
        html = self.get('/agro-work/applications', lang='ru').get_data(as_text=True)
        row = self.row_of(html, 'APP-TEST-007')
        self.assertTrue(row.startswith('<tr class="is-danger-row">'))
        self.assertIn('<span class="vs-badge vs-badge-danger">Без вердикта</span>', row)
        self.assertIn('Заявка открыта', row)
        # отрицательный контроль: выполненная заявка с вердиктом и закрытая
        # без него -- не красные строки
        for number in ('APP-TEST-001', 'APP-TEST-006'):
            self.assertTrue(self.row_of(html, number).startswith('<tr>'), number)

    def test_the_machine_page_shows_it_red_with_its_age(self):
        for lang, age in (('ru', 'открыта %d сут. с ввода'),
                          ('uz', 'киритилгандан бери %d кун очиқ')):
            html = self.get('/agro-work/machine/%d' % self.eq1_id,
                            lang=lang).get_data(as_text=True)
            row = self.row_of(html, 'APP-TEST-007', last=True)
            self.assertTrue(row.startswith('<tr class="is-danger-row">'), lang)
            self.assertIn('vs-badge vs-badge-danger', row)
            self.assertIn(age % self.days_open, row)
            self.assertTrue(self.row_of(html, 'APP-TEST-001', last=True)
                            .startswith('<tr>'))

    def test_the_dashboard_card_calls_it_a_violation(self):
        html = self.get('/agro-work/', lang='ru').get_data(as_text=True)
        rules = html[html.index('<details class="aw-rules">'):]
        self.assertIn('Открытая заявка — нарушение', rules)
        self.assertNotIn('решает владелец', html)
        # Красное число -- нарушение: и в «Требует внимания», и в таблице.
        row = html[html.index('<td>Buxoro agroklaster</td>'):]
        row = row[:row.index('</tr>')]
        self.assertRegex(row, r'reason=otkryta[^"]*" class="aw-num-bad">1</a>')
        html = self.get('/agro-work/', lang='uz').get_data(as_text=True)
        self.assertIn('Очиқ буюртма — қоидабузарлик', html)


class WhatCameAfterOnScreen(ScreenCase):
    """B5: у суток «без заявки» -- что было потом, на трёх экранах."""

    def add_ordinary(self):
        # A8: T2 открыта 16-го -- для работы 13-го это обычная заявка через
        # 3 суток; сутки до её создания она не покрывает.
        _sql(["INSERT INTO agro_work_applications (id, application_number, "
              "transport_id, work_type_id, work_type_name, unit, status, "
              "created_at, updated_at, created_day, first_seen_run_id, "
              "last_seen_run_id, history_updated_at, initial_status, "
              "plate_number) VALUES ('A8', 'APP-TEST-008', 'T2', 'W1', "
              "'Култивация', 'HECTARE', 'IN_PROGRESS', "
              "'2026-09-16T08:00:00+05:00', 'u', '2026-09-16', 1, 1, 'u', "
              "'PENDING', 'PT2')"])

    def test_without_a_later_application_the_day_says_not_entered(self):
        html = self.get('/agro-work/work-days', lang='ru',
                        coverage='bez_zayavki').get_data(as_text=True)
        self.assertIn('заявка не заведена совсем: позже у машины новых заявок '
                      'нет', html)
        html = self.get('/agro-work/', lang='ru').get_data(as_text=True)
        self.assertIn('Заявка не заведена совсем: 1', html)
        # Пустые части «что было потом» не показываются вовсе.
        self.assertNotIn('Позже заведена обычная заявка:', html)
        self.assertNotIn('Позже заведена заявка задним числом:', html)
        self.assertNotIn('порядок ввода неизвестен:', html)

    def test_a_later_ordinary_application_is_named_on_every_screen(self):
        self.add_ordinary()
        expected = ('заявка APP-TEST-008 заведена обычным порядком через 3 сут., '
                    'ещё открыта')
        html = self.get('/agro-work/work-days', lang='ru',
                        coverage='bez_zayavki').get_data(as_text=True)
        self.assertIn(expected, html)
        self.assertIn('Работа без заявки', html)
        html = self.get('/agro-work/machine/%d' % self.eq2_id,
                        lang='ru').get_data(as_text=True)
        self.assertIn(expected, html)
        html = self.get('/agro-work/work-days', lang='uz',
                        coverage='bez_zayavki').get_data(as_text=True)
        self.assertIn('APP-TEST-008 буюртма 3 кундан кейин оддий тартибда '
                      'киритилган, ҳали очиқ', html)
        html = self.get('/agro-work/', lang='ru').get_data(as_text=True)
        self.assertIn('Позже заведена обычная заявка: 1', html)
        self.assertNotIn('Заявка не заведена совсем:', html)
        self.assertIn('after=obychnaya_pozzhe', html)
        html = self.get('/agro-work/', lang='uz').get_data(as_text=True)
        self.assertIn('Кейин оддий тартибда буюртма киритилган: 1', html)

    def test_the_list_filters_by_what_came_after(self):
        self.add_ordinary()
        html = self.get('/agro-work/work-days', lang='ru',
                        after='obychnaya_pozzhe').get_data(as_text=True)
        self.assertIn('APP-TEST-008', html)
        self.assertIn('Без заявки: что было потом', html)
        html = self.get('/agro-work/work-days', lang='ru',
                        after='ne_zavedena').get_data(as_text=True)
        self.assertNotIn('APP-TEST-008', html)
        self.assertIn('Суток с работой по GPS с такими условиями нет', html)



def row_of(html, number):
    """Строка таблицы (<tr>...</tr>) заявки: номер -- в её первой ячейке.

    Номер встречается и в чужой строке («заявки: ...» у соседа), поэтому
    ищется начало строки, а не вхождение.
    """
    body = html[html.index('<tbody>'):html.index('</tbody>')]
    rows = [r for r in re.findall(r'<tr>.*?</tr>', body, re.S)
            if re.match(r'<tr>\s*<td>%s\s' % re.escape(number), r)]
    assert len(rows) == 1, (number, len(rows))
    return ' '.join(rows[0].split())


class VolumeOnScreen(ScreenCase):
    """U2: объём заявки против гектаров GPS -- «Свод» и «Заявка -> работа».

    В основе: A1 (T1, 10..11, по GPS 2,5 га 10-го) и A4 (T3, 15..15, 7,0
    га) -- «работа была»; A2 -- «работы не было», объём не сравнивается.
    """

    def set_volume(self, app_id, volume):
        _sql(["UPDATE agro_work_applications SET volume = ? WHERE id = ?"],
             (volume, app_id))

    def add_overlapping(self):
        # A6: T1, окно 10..10 -- пересекается с окном A1 (10..11).
        _sql(["INSERT INTO agro_work_applications (id, application_number, "
              "transport_id, work_type_id, work_type_name, unit, volume, "
              "status, created_at, updated_at, created_day, first_seen_run_id, "
              "last_seen_run_id, history_updated_at, initial_status, "
              "completed_day, plate_number) VALUES ('A6', 'APP-TEST-006', "
              "'T1', 'W1', 'Култивация', 'HECTARE', '1.00', 'COMPLETED', "
              "'2026-09-10T09:00:00+05:00', 'u', '2026-09-10', 1, 1, 'u', "
              "'IN_PROGRESS', '2026-09-10', 'PT1')"])

    def test_the_list_shows_the_colour_and_the_numbers(self):
        self.set_volume('A1', '2.50')
        self.set_volume('A4', '10.00')
        html = self.get('/agro-work/applications', lang='ru').get_data(
            as_text=True)
        self.assertIn('Объём против GPS', html)
        ok = row_of(html, 'APP-TEST-001')
        self.assertIn('В допуске', ok)
        self.assertIn('в заявке 2.50 га · по GPS 2.50 га', ok)
        self.assertIn('+0.00 га (0.0%)', ok)
        fail = row_of(html, 'APP-TEST-004')
        self.assertIn('Вне допуска', fail)
        self.assertIn('в заявке 10.00 га · по GPS 7.00 га', fail)
        self.assertIn('-3.00 га (-30.0%)', fail)
        # Вне допуска -- раньше, чем в допуске.
        self.assertLess(html.index('APP-TEST-004'), html.index('APP-TEST-001'))
        no_work = row_of(html, 'APP-TEST-002')
        self.assertNotIn('Сверить нельзя', no_work)
        self.assertNotIn('в заявке', no_work)
        self.assertTrue(no_work.endswith('<td> — </td> </tr>'), no_work)

    def test_the_list_filters_by_colour(self):
        self.set_volume('A1', '2.50')
        self.set_volume('A4', '10.00')
        html = self.get('/agro-work/applications', lang='ru',
                        volume='fail').get_data(as_text=True)
        self.assertIn('<select name="volume"', html)
        self.assertIn('APP-TEST-004', html)
        self.assertNotIn('APP-TEST-001', html)
        html = self.get('/agro-work/applications', lang='ru',
                        volume='ok').get_data(as_text=True)
        self.assertIn('APP-TEST-001', html)
        self.assertNotIn('APP-TEST-004', html)
        html = self.get('/agro-work/applications', lang='ru',
                        volume='sverit_nelzya').get_data(as_text=True)
        self.assertNotIn('APP-TEST-001', html)
        self.assertNotIn('APP-TEST-002', html)

    def test_the_dashboard_card_counts_and_links(self):
        html = self.get('/agro-work/', lang='ru').get_data(as_text=True)
        self.assertIn('Работа была: объём против GPS', html)
        self.assertEqual(legend(html, 'Сверить нельзя'), '2')  # объёма нет у обеих
        self.assertIsNone(attention(html, 'Объём в заявке вне допуска'))
        self.set_volume('A1', '2.50')
        self.set_volume('A4', '10.00')
        html = self.get('/agro-work/', lang='ru').get_data(as_text=True)
        for label, count in (('В допуске', '1'), ('На грани', '0'),
                             ('Вне допуска', '1'), ('Сверить нельзя', '0')):
            self.assertEqual(legend(html, label), count, label)
        self.assertEqual(attention(html, 'Объём в заявке вне допуска'), '1')
        self.assertIn('volume=fail', html)
        self.assertIn('verdict=rabota_est', html)

    def test_a_shared_window_names_the_reason_the_neighbour_and_the_sum(self):
        self.set_volume('A1', '2.00')
        self.add_overlapping()
        html = self.get('/agro-work/applications', lang='ru').get_data(
            as_text=True)
        mine = row_of(html, 'APP-TEST-001')
        self.assertIn('Сверить нельзя', mine)
        self.assertIn('Окно пересекается с окном другой заявки этой машины', mine)
        self.assertIn('заявки: APP-TEST-006', mine)
        self.assertIn('вместе за 10.09 — 11.09: в заявках 3.00 га · по GPS '
                      '2.50 га', mine)
        self.assertNotIn('В допуске', mine)
        self.assertNotIn('Вне допуска', mine)

    def test_both_languages(self):
        self.set_volume('A1', '2.50')
        html = self.get('/agro-work/applications', lang='uz').get_data(
            as_text=True)
        self.assertIn('Ҳажм GPS га қарши', html)
        mine = row_of(html, 'APP-TEST-001')
        self.assertIn('Йўл қўйилишда', mine)
        self.assertIn('буюртмада 2.50 га · GPS бўйича 2.50 га', mine)
        self.assertNotIn('в заявке', mine)
        html = self.get('/agro-work/', lang='uz').get_data(as_text=True)
        self.assertIn('Иш бўлган: ҳажм GPS га қарши', html)
        self.assertEqual(legend(html, 'Йўл қўйилишда'), '1')
        for table in (labels.VOLUME, labels.VOLUME_REASONS):
            for _ru, uz in table.values():
                self.assertNotRegex(uz.replace('GPS', ''), '[A-Za-z]', uz)

class TimeOnScreen(ScreenCase):
    """Метод «время»: часы зажигания за окно вместо гектаров (GPS-26).

    A7: T2, окно 10..11, единица «час», объём 3,00; по GPS включено 2 ч 10-го
    и 1 ч 11-го. Таблицу часов создаёт миграция GPS_ENGINE_001, ORM-модели у
    неё нет -- reset_db её не трогает, поэтому тест убирает её сам.
    """

    def setUp(self):
        super().setUp()
        # Вердикты «время» публикуются (проверка по счётчику 29.10 -- MET);
        # выключатель задан явно, выключенный -- последним тестом.
        from agro_work import reconcile
        patcher = mock.patch.object(reconcile, 'TIME_VERDICTS_PUBLISHED', True)
        patcher.start()
        self.addCleanup(patcher.stop)
        import migrate_gps_engine_001 as engine_mig
        _sql([engine_mig.CREATE_GPS_DAILY_ENGINE])
        self.addCleanup(_sql, ['DROP TABLE IF EXISTS gps_daily_engine'])
        _sql(["INSERT INTO agro_work_work_types (id, name, unit, method, "
              "first_seen_at, last_seen_at) VALUES ('W2', 'Хар хил иш', "
              "'HOUR', 'vremya', 't', 't')"])
        _sql(["INSERT INTO agro_work_applications (id, application_number, "
              "transport_id, work_type_id, work_type_name, unit, volume, "
              "status, created_at, updated_at, created_day, first_seen_run_id, "
              "last_seen_run_id, history_updated_at, initial_status, "
              "completed_day, plate_number) VALUES ('A7', 'APP-TEST-007', "
              "'T2', 'W2', 'Хар хил иш', 'HOUR', '3.00', 'COMPLETED', "
              "'2026-09-10T08:00:00+05:00', 'u', '2026-09-10', 1, 1, 'u', "
              "'IN_PROGRESS', '2026-09-11', 'PT2')"])
        for day, on in ((10, 7200), (11, 3600)):
            _sql(["INSERT INTO gps_daily_engine (work_date, wialon_id, "
                  "on_seconds, off_seconds, unknown_seconds, points_total, "
                  "points_with_ign, method_version, computed_at) VALUES (?, "
                  "1002, ?, ?, 0, 100, 100, 'ignition-R300-2026-10-19', 'now')"],
                 ('2026-09-%02d' % day, on, 86400 - on))

    def test_the_list_shows_hours_not_hectares(self):
        html = self.get('/agro-work/applications', lang='ru').get_data(
            as_text=True)
        self.assertIn('Га или часы по GPS в окне', html)
        row = row_of(html, 'APP-TEST-007')
        self.assertIn('3.00 ч', row)
        self.assertIn('В допуске', row)
        self.assertIn('в заявке 3.00 ч · по GPS 3.00 ч', row)
        self.assertIn('+0.00 ч (0.0%)', row)
        self.assertNotIn(' га', row)
        # Гектарная заявка рядом -- по-прежнему в гектарах.
        self.assertIn('2.5', row_of(html, 'APP-TEST-001'))

    def test_the_list_explains_the_time_method(self):
        # [REASON]: с 29.10 вердикты «время» публикуются; пояснение списка
        # называет их правило -- числа те же, что в коде (порог 30 минут,
        # допуск В-2 по часам, пробег грузовика от 3 км).
        from agro_work import reconcile
        from gps import tolerance
        self.assertEqual((reconcile.TIME_WORK_SECONDS, reconcile.TRUCK_WORK_KM),
                         (1800, 3.0))
        self.assertEqual(tolerance.TOLERANCE_HOURS_OK, (0.15, 0.5))
        self.assertEqual(tolerance.TOLERANCE_HOURS_WARN, 0.25)
        ru = self.get('/agro-work/applications', lang='ru').get_data(as_text=True)
        self.assertIn('У заявок метода «время» вместо гектаров — часы с включённым '
                      'зажиганием по GPS. Работа была — включено не меньше 30 минут '
                      'за окно', ru)
        self.assertIn('в допуске при расхождении не больше 15% или 0,5 ч, на грани '
                      'до 25%', ru)
        self.assertIn('решает пробег окна — от 3 км', ru)
        uz = self.get('/agro-work/applications', lang='uz').get_data(as_text=True)
        self.assertIn('«Вақт» усулидаги буюртмаларда гектар ўрнига — GPS бўйича ўт '
                      'олдириш ёқилган соатлар.', uz)
        self.assertNotIn('У заявок метода «время»', uz)

    def test_the_uzbek_list_says_hours_in_cyrillic(self):
        html = self.get('/agro-work/applications', lang='uz').get_data(
            as_text=True)
        row = row_of(html, 'APP-TEST-007')
        self.assertIn('3.00 соат', row)
        self.assertIn('буюртмада 3.00 соат · GPS бўйича 3.00 соат', row)

    def test_while_unpublished_the_list_keeps_the_old_reason(self):
        from agro_work import reconcile
        with mock.patch.object(reconcile, 'TIME_VERDICTS_PUBLISHED', False):
            html = self.get('/agro-work/applications', lang='ru').get_data(
                as_text=True)
        row = row_of(html, 'APP-TEST-007')
        self.assertIn('Метод «время»: часы зажигания по GPS ещё не готовы', row)
        self.assertNotIn('по GPS 3.00 ч', row)
        self.assertNotIn('В допуске', row)


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
        self.assertEqual(strip_hrefs, GPS_MODULE_PAGES)


# Раздел модуля «GPS план-факт» после сведения сверок (владелец, 08.10);
# «Заявка -> работа» и «Работа -> заявка» с 19.10 -- вкладки «Сверки», а не
# свои пункты (просьба владельца «объединить в один пункт»).
GPS_MODULE_PAGES = sorted(['/gps/fact', '/gps/machine', '/agro-work/',
                           '/agro-work/import', '/gps/sync'])


def _strip(html):
    return re.search(r'<nav class="vs-pills vs-modulenav vs-mb".*?</nav>',
                     html, re.S).group(0)


def _hrefs(fragment):
    return sorted(set(re.findall(r'href="([^"]+)"', fragment)))


class OneReconciliationSection(ScreenCase):
    """Решение владельца 08.10: «Сверку нарядов» и «Сверку agro-work» свести.

    Наряды в программе не ведутся (за 01.07–30.09 их 0), поэтому сверка --
    одна, по заявкам agro-work, и живёт внутри модуля «GPS план-факт».
    Маршруты остались прежними; меняется только место в меню. «Сверка
    нарядов» из меню уходит, её маршрут цел -- на будущее, если наряды
    начнут вести в программе.
    """

    def test_the_reconciliation_is_a_section_of_the_gps_module(self):
        html = self.get('/agro-work/').get_data(as_text=True)
        # один пункт модуля в сайдбаре, он раскрыт и подсвечен
        gps_link = re.search(r'<a href="/gps/fact" class="vs-side-link([^"]*)"',
                             html)
        self.assertIsNotNone(gps_link)
        self.assertIn('is-active', gps_link.group(1))
        self.assertNotIn('Сверка agro-work', html)
        self.assertNotIn('href="/agro-work/" class="vs-side-link', html)
        side = sorted(set(re.findall(
            r'<a [^>]*?href="([^"]+)"[^>]*class="vs-side-sublink', html)))
        self.assertEqual(side, GPS_MODULE_PAGES)
        self.assertEqual(_hrefs(_strip(html)), GPS_MODULE_PAGES)

    def test_the_gps_pages_show_the_same_set(self):
        html = self.get('/gps/fact').get_data(as_text=True)
        self.assertEqual(_hrefs(_strip(html)), GPS_MODULE_PAGES)
        side = sorted(set(re.findall(
            r'<a [^>]*?href="([^"]+)"[^>]*class="vs-side-sublink', html)))
        self.assertEqual(side, GPS_MODULE_PAGES)

    def test_the_orders_page_is_off_the_menu_but_still_answers(self):
        for url in ('/agro-work/', '/gps/fact'):
            html = self.get(url).get_data(as_text=True)
            with self.subTest(url=url):
                self.assertNotIn('/gps/orders', _strip(html))
                self.assertNotIn('Сверка нарядов', html)
        self.assertEqual(self.get('/gps/orders').status_code, 200)

    def test_the_reconciliation_pill_stays_current_on_the_machine_page(self):
        html = self.get('/agro-work/machine/%d' % self.eq1_id).get_data(
            as_text=True)
        current = re.findall(r'<a href="([^"]+)"\s+class="vs-pill is-active"',
                             _strip(html))
        self.assertEqual(current, ['/agro-work/'])

    def test_both_languages(self):
        for lang, labels, title in (
                # Названия пунктов с 04.11.2026 -- решение владельца.
                ('ru', ['Работа за день', 'История машины', 'Заявки и GPS',
                        'Импорт заявок', 'Приём данных'],
                 'Заявки и GPS'),
                ('uz', ['Кунлик иш', 'Машина тарихи', 'Буюртмалар ва GPS',
                        'Буюртмалар импорти', 'Маълумот қабули'],
                 'Буюртмалар ва GPS')):
            html = self.get('/agro-work/', lang=lang).get_data(as_text=True)
            pills = [x.strip() for x in re.findall(r'>([^<>]+)</a>',
                                                   _strip(html))]
            with self.subTest(lang=lang):
                self.assertEqual(pills, labels)
                self.assertIn(title, html)
                if lang == 'uz':
                    # узбекский -- только кириллицей; GPS -- название
                    self.assertFalse(any(re.search('[A-Za-z]',
                                                   x.replace('GPS', ''))
                                         for x in labels))


class OneScreen(ScreenCase):
    """Просьба владельца 19.10: «Заявка → работа» и «Работа → заявка» -- в
    одном пункте, сверху фильтры по организации, статусу и типу оплаты,
    выгрузка в Excel.

    Держится: вкладки на трёх страницах «Сверки» и ссылки с фильтрами; одно
    определение фильтра на свод, оба списка и книгу; книга -- та же, что у
    консольного отчёта, с подмножеством строк и строкой фильтров; охват
    организаций и в книге; экран и книга ничего не пишут.
    """

    def setUp(self):
        super().setUp()
        # Оплата: A1 и A4 -- наличные, A2 -- перечисление, A3 и A5 -- нет.
        # A2 -- в процессе: открытая заявка.
        for app_id, pay in (('A1', 'CASH'), ('A2', 'TRANSFER'), ('A4', 'CASH')):
            _sql(["UPDATE agro_work_applications SET payment_type = ? "
                  "WHERE id = ?"], (pay, app_id))
        _sql(["UPDATE agro_work_applications SET status = 'IN_PROGRESS', "
              "completed_day = NULL WHERE id = 'A2'"])

    def numbers(self, url, **params):
        response = self.get(url, **params)
        self.assertEqual(response.status_code, 200)
        return sorted(set(re.findall(r'APP-TEST-\d{3}',
                                     response.get_data(as_text=True))))

    def book(self, user_id=None, **params):
        from openpyxl import load_workbook
        import io
        response = self.get('/agro-work/export', user_id=user_id, **params)
        self.assertEqual(response.status_code, 200)
        self.assertIn('spreadsheetml', response.headers['Content-Type'])
        self.assertIn('attachment', response.headers['Content-Disposition'])
        return load_workbook(io.BytesIO(response.data))

    @staticmethod
    def column(sheet, index=0):
        return sorted({row[index] for row in sheet.iter_rows(min_row=2,
                                                             values_only=True)
                       if row and row[index]})

    def test_three_tabs_on_every_reconciliation_page(self):
        for url, active in (('/agro-work/', '/agro-work/'),
                            ('/agro-work/applications', '/agro-work/applications'),
                            ('/agro-work/work-days', '/agro-work/work-days')):
            html = self.get(url, lang='ru', pay='CASH').get_data(as_text=True)
            tabs = re.search(r'<nav class="vs-segmented vs-mb"[^>]*>(.*?)</nav>',
                             html, re.S)
            with self.subTest(url=url):
                self.assertIsNotNone(tabs)
                links = re.findall(r'<a href="([^"]+)"', tabs.group(1))
                self.assertEqual([link.split('?')[0] for link in links],
                                 ['/agro-work/', '/agro-work/applications',
                                  '/agro-work/work-days'])
                for link in links:            # вкладка не теряет фильтр
                    self.assertIn('pay=CASH', link)
                    self.assertIn('from=2026-09-10', link)
                current = re.findall(r'<a href="([^"?]+)[^"]*" class="is-active"'
                                     r' aria-current="page"', tabs.group(1))
                self.assertEqual(current, [active])
                labels_shown = re.findall(r'>([^<>]+)</a>', tabs.group(1))
                self.assertEqual(labels_shown, ['Свод', 'Заявка → работа',
                                                'Работа → заявка'])

    def test_the_common_filters_are_on_top_of_every_tab(self):
        for url in ('/agro-work/', '/agro-work/applications',
                    '/agro-work/work-days'):
            html = self.get(url, lang='ru').get_data(as_text=True)
            with self.subTest(url=url):
                for name in ('org', 'cat', 'status', 'pay'):
                    self.assertIn('<select name="%s"' % name, html)
                self.assertIn('Скачать Excel', html)
                self.assertIn('href="/agro-work/export?', html)

    def test_payment_filter_on_the_applications_tab(self):
        self.assertEqual(self.numbers('/agro-work/applications', pay='CASH'),
                         ['APP-TEST-001', 'APP-TEST-004'])
        self.assertEqual(self.numbers('/agro-work/applications', pay='TRANSFER'),
                         ['APP-TEST-002'])
        # отрицательный контроль: без фильтра -- все пять
        self.assertEqual(len(self.numbers('/agro-work/applications')), 5)
        # чужое значение -- не фильтр, а «все»
        self.assertEqual(len(self.numbers('/agro-work/applications',
                                          pay='BOGUS')), 5)

    def test_status_filter_on_the_applications_tab(self):
        self.assertEqual(self.numbers('/agro-work/applications',
                                      status='IN_PROGRESS'), ['APP-TEST-002'])
        self.assertEqual(self.numbers('/agro-work/applications',
                                      status='CANCELLED'), [])

    def test_organization_filter_is_a_list_and_keeps_the_scope(self):
        html = self.get('/agro-work/applications', lang='ru').get_data(as_text=True)
        options = re.search(r'<select name="org".*?</select>', html, re.S).group(0)
        self.assertIn('Buxoro agroklaster', options)
        self.assertIn('Jizzax filial', options)
        self.assertIn('value="none"', options)
        self.assertEqual(self.numbers('/agro-work/applications',
                                      org=str(self.org2_id)), ['APP-TEST-004'])
        self.assertEqual(self.numbers('/agro-work/applications', org='none'),
                         ['APP-TEST-003'])
        # оператор своей организации: в списке только она, несвязанных нет
        op = self.operator([self.org1_id])
        html = self.get('/agro-work/applications', user_id=op,
                        lang='ru').get_data(as_text=True)
        options = re.search(r'<select name="org".*?</select>', html, re.S).group(0)
        self.assertIn('Buxoro agroklaster', options)
        self.assertNotIn('Jizzax filial', options)
        self.assertNotIn('value="none"', options)

    def test_work_days_follow_the_application_filters(self):
        html = self.get('/agro-work/work-days', lang='ru',
                        pay='CASH').get_data(as_text=True)
        self.assertIn('10.09.2026', html)            # покрыта A1, наличные
        self.assertIn('15.09.2026', html)            # покрыта A4, наличные
        self.assertNotIn('13.09.2026', html)         # без заявки -- скрыта
        self.assertIn('Сутки без заявки при этих фильтрах не показываются', html)
        plain = self.get('/agro-work/work-days', lang='ru').get_data(as_text=True)
        self.assertIn('13.09.2026', plain)           # отрицательный контроль
        self.assertNotIn('Сутки без заявки при этих фильтрах', plain)

    def test_summary_counts_and_links_follow_the_filters(self):
        html = self.get('/agro-work/', lang='ru', pay='CASH').get_data(as_text=True)
        first = re.search(r'Заявки за период</div>\s*<div class="aw-card-value">'
                          r'(\d+)</div>', html)
        self.assertEqual(first.group(1), '2')
        for link in re.findall(r'href="(/agro-work/(?:applications|work-days)'
                               r'\?[^"]+)"', html):
            self.assertIn('pay=CASH', link.replace('&amp;', '&'))
        plain = self.get('/agro-work/', lang='ru').get_data(as_text=True)
        first = re.search(r'Заявки за период</div>\s*<div class="aw-card-value">'
                          r'(\d+)</div>', plain)
        self.assertEqual(first.group(1), '5')

    def test_the_book_is_the_console_book_with_the_filtered_rows(self):
        book = self.book()
        self.assertEqual(book.sheetnames, ['Свод', 'Заявка-работа',
                                           'Работа-заявка', 'Открытые заявки',
                                           'Причины', 'Замер N'])
        self.assertEqual(self.column(book['Заявка-работа']),
                         ['APP-TEST-001', 'APP-TEST-002', 'APP-TEST-003',
                          'APP-TEST-004', 'APP-TEST-005'])
        notes = [row[0] for row in book['Свод'].iter_rows(values_only=True)
                 if row and isinstance(row[0], str)]
        self.assertFalse([n for n in notes if 'Фильтры экрана' in n])
        cash = self.book(pay='CASH')
        self.assertEqual(self.column(cash['Заявка-работа']),
                         ['APP-TEST-001', 'APP-TEST-004'])
        days = [row[0] for row in cash['Работа-заявка'].iter_rows(
            min_row=2, values_only=True) if row and row[0]]
        self.assertEqual(sorted(d.isoformat()[:10] for d in days),
                         ['2026-09-10', '2026-09-15'])
        notes = [row[0] for row in cash['Свод'].iter_rows(values_only=True)
                 if row and isinstance(row[0], str)]
        self.assertTrue([n for n in notes
                         if 'Фильтры экрана: тип оплаты: Наличные' in n], notes)
        # Открытые заявки -- тем же фильтром: A2 открыта, но оплата другая.
        self.assertEqual(self.column(cash['Открытые заявки'], 2), [])
        self.assertEqual(self.column(self.book(pay='TRANSFER')['Открытые заявки'],
                                     2), ['APP-TEST-002'])

    def test_the_book_keeps_the_organization_scope(self):
        op = self.operator([self.org1_id])
        book = self.book(user_id=op)
        numbers = self.column(book['Заявка-работа'])
        self.assertNotIn('APP-TEST-004', numbers)    # организация 2
        self.assertNotIn('APP-TEST-003', numbers)    # машина не связана
        self.assertIn('APP-TEST-001', numbers)

    def test_the_export_writes_nothing(self):
        before = sha()
        self.book(pay='CASH')
        self.book()
        self.assertEqual(sha(), before)

    def test_uzbek_filter_labels_are_cyrillic(self):
        html = self.get('/agro-work/applications', lang='uz').get_data(as_text=True)
        pay = re.search(r'<select name="pay".*?</select>', html, re.S).group(0)
        status = re.search(r'<select name="status".*?</select>', html, re.S).group(0)
        texts = re.findall(r'>([^<>]+)</option>', pay + status)
        self.assertIn('Нақд пул', texts)
        self.assertIn('Бажарилди', texts)
        for text in texts:
            self.assertNotRegex(text, '[A-Za-z]', text)
        self.assertIn('Excel юклаб олиш', html)
        self.assertIn('Тўлов тури', html)


class DashboardForPeople(ScreenCase):
    """Решение владельца 04.11.2026: «Свод» читают люди, а не проверяющие.

    Сначала -- что требует действия (строка с числом, фразой и списком),
    потом итоги периода тремя полосами, потом одна таблица по организациям;
    правила -- один раз, внизу. Числа те же, что у списков и книги: здесь
    держится, что новые места показывают одни и те же счётчики, а пустое
    нарушение не показывается вовсе.
    """

    def test_the_attention_numbers_are_the_card_and_table_numbers(self):
        html = self.get('/agro-work/', lang='ru').get_data(as_text=True)
        self.assertEqual(attention(html, 'Заявка закрыта, а работы по GPS не было'),
                         legend(html, 'Работы по GPS не было'))
        self.assertEqual(attention(html, 'Дни работы машин без заявки'),
                         legend(html, 'Работа без заявки'))
        # «Итого» таблицы по организациям -- те же числа, что у карточек.
        total = html[html.index('<td><b>Итого</b></td>'):]
        # [Итого, машин, заявок, работа была, не закрыто, работы не было,
        #  вне допуска, дней без заявки]
        total = re.findall(r'<b>([^<]+)</b>', total[:total.index('</tr>')])
        self.assertEqual(total[2], re.search(
            r'Заявки за период</div>\s*<div class="aw-card-value">([^<]+)</div>',
            html).group(1))
        self.assertEqual(total[3], legend(html, 'Работа была'))
        self.assertEqual(total[5], legend(html, 'Работы по GPS не было'))
        self.assertEqual(total[7], legend(html, 'Работа без заявки'))
        # Строки по организациям складываются в «Итого».
        body = html[html.index('<div class="vs-table-wrap vs-mb aw-org">'):]
        body = body[:body.index('<td><b>Итого</b></td>')]
        body = body[body.index('<tbody>'):]
        apps = []
        for row in re.findall(r'<tr>(.*?)</tr>', body, re.S):
            cells = re.findall(r'<td[^>]*>(.*?)</td>', row, re.S)
            apps.append(int(re.sub(r'<[^>]+>', '', cells[2]).strip()))
        self.assertEqual(len(apps), 3)       # две организации и «не связаны»
        self.assertEqual(sum(apps), int(total[2]))

    def test_an_empty_violation_is_not_shown(self):
        # Вторая организация: одна заявка, работа была, объём не сравнить --
        # нарушать нечего, и экран так и говорит.
        html = self.get('/agro-work/', lang='ru',
                        org=self.org2_id).get_data(as_text=True)
        self.assertNotIn('class="aw-attn-num', html)
        self.assertIn('За период нарушений нет.', html)
        html = self.get('/agro-work/', lang='uz',
                        org=self.org2_id).get_data(as_text=True)
        self.assertIn('Давр учун қоидабузарликлар йўқ.', html)

    def test_a_zero_line_is_left_out_of_the_list(self):
        # В основе нарушения есть (A2 без работы, 13-е без заявки), а открытых
        # заявок, объёма вне допуска и видов работ без способа нет: их строк
        # в списке нет, нулей «Требует внимания» не показывает.
        html = self.get('/agro-work/', lang='ru').get_data(as_text=True)
        self.assertEqual(html.count('class="aw-attn-num'), 2)
        for title in ('Заявки не закрыты операторами', 'Объём в заявке вне допуска',
                      'Заявки нельзя проверить: у вида работ не выбран способ '
                      'проверки'):
            self.assertIsNone(attention(html, title), title)
        self.assertNotIn('За период нарушений нет.', html)

    def test_applications_without_a_method_are_their_own_line(self):
        _sql(["INSERT INTO agro_work_work_types (id, name, unit, method, "
              "first_seen_at, last_seen_at) VALUES ('W2', 'Хар хил ишлар', "
              "'HOUR', NULL, 't', 't')",
              "INSERT INTO agro_work_applications (id, application_number, "
              "transport_id, work_type_id, work_type_name, unit, status, "
              "created_at, updated_at, created_day, first_seen_run_id, "
              "last_seen_run_id, history_updated_at, initial_status, "
              "completed_day, plate_number) VALUES ('A9', 'APP-TEST-009', "
              "'T1', 'W2', 'Хар хил ишлар', 'HOUR', 'COMPLETED', "
              "'2026-09-14T08:00:00+05:00', 'u', '2026-09-14', 1, 1, 'u', "
              "'IN_PROGRESS', '2026-09-14', 'PT1')"])
        html = self.get('/agro-work/', lang='ru').get_data(as_text=True)
        title = 'Заявки нельзя проверить: у вида работ не выбран способ проверки'
        self.assertEqual(attention(html, title), '1')
        # Это не нарушение оператора -- число серое, а не красное.
        self.assertIn('<span class="aw-attn-num is-muted">1</span>', html)
        self.assertIn('reason=metod_ne_razmechen', html)

    def test_the_rules_are_kept_word_for_word_once_at_the_bottom(self):
        html = self.get('/agro-work/', lang='ru').get_data(as_text=True)
        rules = html[html.index('<details class="aw-rules">'):]
        for text in ('В обе стороны: была ли работа по заявке — и была ли заявка '
                     'на работу.',
                     'Окно обычной заявки — с дня создания по день «Выполнено» '
                     'включительно.',
                     'Сверяются заявки видов работ с методами «гектары» и «время»'):
            self.assertIn(text, rules)
            self.assertEqual(html.count(text), 1, text)

    def test_no_wall_of_text_above_the_numbers(self):
        # Вместо карточки с абзацами правил -- одна строка под вкладками.
        for url in ('/agro-work/', '/agro-work/applications',
                    '/agro-work/work-days'):
            html = self.get(url, lang='ru').get_data(as_text=True)
            with self.subTest(url=url):
                self.assertNotIn('class="vs-hero', html)
                self.assertEqual(html.count('<p class="aw-lead">'), 1)
                self.assertEqual(html.count('<details class="aw-rules">'), 1)
                self.assertIn("css/agro-work.css", html)

    def test_the_bars_add_up(self):
        html = self.get('/agro-work/', lang='ru').get_data(as_text=True)
        for bar in re.findall(r'<div class="aw-bar"[^>]*>(.*?)</div>', html, re.S):
            widths = [float(x) for x in re.findall(r'width: ([\d.]+)%', bar)]
            if widths:
                self.assertAlmostEqual(sum(widths), 100.0, places=2)

    def test_thousands_are_grouped(self):
        from agro_work_routes import _num
        self.assertEqual(_num(4274), '4\u00a0274')
        self.assertEqual(_num(0), '0')
        self.assertEqual(_num(None), '0')

    def test_the_stylesheet_uses_only_tokens(self):
        # Цвета -- только токенами дизайн-системы: файл модуля не заводит
        # своих оттенков.
        with open(os.path.join(REPO_ROOT, 'static', 'css', 'agro-work.css'),
                  encoding='utf-8') as fh:
            css = fh.read()
        self.assertNotRegex(css, r'#[0-9a-fA-F]{3,8}\b')
        self.assertNotRegex(css, r'\brgba?\(')
        for token in set(re.findall(r'var\((--[a-z0-9-]+)\)', css)):
            with self.subTest(token=token):
                self.assertRegex(DESIGN_SYSTEM, r'(?<![\w-])' + re.escape(token)
                                 + r'\s*:')


class Copies(unittest.TestCase):
    def test_category_labels_match_models(self):
        self.assertEqual(labels.CATEGORIES, CATEGORIES)


if __name__ == '__main__':
    unittest.main()
