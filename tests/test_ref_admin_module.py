# -*- coding: utf-8 -*-
"""UI-P6-001: модуль справочников и администрирования на контрактах §6.

Проверяется то, что миграция вёрстки могла сломать молча.

1. **Режим таблицы объявлен.** DD-028 требует, чтобы происхождение
   сортировки и охват итогов были видны из разметки. Таблица без режима
   выглядит нормально и врёт молча.

2. **Сортировка по умолчанию не подменяет порядок сервера.** Три экрана
   отдают осмысленный серверный порядок (`sort_order` у организаций,
   трёхуровневый у техники). Клиентский `data-sort-default` переставил бы
   строки при каждой загрузке, и заметить это можно было бы только зная,
   каким порядок был.

3. **Охват окна на `/admin/audit` подписан числом ИЗ МАРШРУТА.** В журнале
   3436 записей, маршрут отдаёт 300. Раньше страница показывала «300» рядом
   со словами «Последние действия», и это читалось как итог.

4. **Ссылки выгрузок Excel целы.** Файлы уходят в бухгалтерию; кнопка
   переехала в другое место разметки, и потерянный параметр фильтра дал бы
   выгрузку не того объёма — без единой ошибки на экране.

5. **Правило удаления/отключения техники перенесено дословно.** Ветвление
   `can_delete / can_deactivate / is_disabled / иначе` — бизнес-правило, а не
   оформление.
"""
import re
import unittest

from sqlalchemy import text

from tests.harness import app, db, reset_db, create_admin, create_org, login
from models import (AppModule, Customer, Equipment, User, WorkType,
                    ROLE_OPERATOR)

# [REASON]: audit_logs -- ne model SQLAlchemy, a tablica iz migracii SEC-003A.
# db.create_all() ee ne sozdaet, i marshrut /admin/audit upal by na
# "no such table". DDL sovpadaet s toy zhe migraciey; tot zhe priem ispolzuet
# tests/test_cycle23_audit.py.
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

MODULE_ROUTES = {
    '/ref/customers': 'ref_customers.html',
    '/ref/organizations': 'ref_organizations.html',
    '/ref/work_types': 'ref_work_types.html',
    '/ref/equipment': 'ref_equipment.html',
    '/admin/users': 'admin_users.html',
    '/admin/permissions': 'admin_permissions.html',
    '/admin/audit': 'audit_logs.html',
}

RE_TABLE = re.compile(r'<table\b([^>]*)>', re.I)
RE_CLASS = re.compile(r'class="([^"]*)"')


def _tables(html):
    """(атрибуты, классы) каждой таблицы страницы."""
    out = []
    for attrs in RE_TABLE.findall(html):
        cls = RE_CLASS.search(attrs)
        out.append((attrs, cls.group(1) if cls else ''))
    return out


class RefAdminModuleContracts(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        reset_db()
        cls.admin_id = create_admin()
        cls.org_id = create_org('Когон ПТЗ')
        with app.app_context():
            db.session.add_all([
                Customer(name='Фермер А', customer_type='external'),
                Customer(name='Фермер Б', customer_type='internal'),
                WorkType(name='Шудгор', default_unit='га', default_price=100),
                WorkType(name='Чигит экиш', default_unit='', default_price=0),
                Equipment(organization_id=cls.org_id, category='mtz',
                          eq_type='Трактор МТЗ', name='МТЗ 261 EA',
                          plate='80 561 EA', default_unit='га',
                          default_price=50, is_active=True),
                Equipment(organization_id=cls.org_id, category='mtz',
                          eq_type='Трактор МТЗ', name='МТЗ 999 ZZ',
                          plate='80 999 ZZ', default_unit='',
                          default_price=0, is_active=False),
            ])
            operator = User(username='oper1', role=ROLE_OPERATOR,
                            full_name='Оператор Один')
            operator.set_password('x' * 10)
            db.session.add(operator)
            db.session.add_all([
                AppModule(code='transport', name_uz='Транспорт', name_ru='Транспорт'),
                AppModule(code='fuel', name_uz='Ёқилғи', name_ru='Топливо'),
            ])
            db.session.execute(text(AUDIT_DDL))
            db.session.execute(text(
                "INSERT INTO audit_logs (created_at, action, username_snapshot,"
                " module, status, description, ip_address)"
                " VALUES ('2026-08-01T10:00:00', 'login', 'admin', 'auth', 'ok',"
                " 'вход в систему', '10.0.0.1')"))
            db.session.commit()

    def _get(self, url):
        client = app.test_client()
        login(client, self.admin_id)
        response = client.get(url)
        self.assertEqual(response.status_code, 200, url)
        return response.get_data(as_text=True)

    # ---- 1. режим объявлен -------------------------------------------------

    def test_every_table_declares_a_mode(self):
        """DD-028: у каждой таблицы модуля есть vs-table и ровно один режим."""
        modes = {'is-static', 'is-paged', 'is-stream'}
        seen = 0
        for url in MODULE_ROUTES:
            html = self._get(url)
            tables = _tables(html)
            self.assertTrue(tables, 'на %s не найдено ни одной таблицы' % url)
            for attrs, cls in tables:
                classes = set(cls.split())
                self.assertIn('vs-table', classes,
                              '%s: таблица без vs-table, class=%r' % (url, cls))
                declared = classes & modes
                self.assertEqual(len(declared), 1,
                                 '%s: режимов объявлено %d, class=%r'
                                 % (url, len(declared), cls))
                seen += 1
        self.assertEqual(seen, 7, 'таблиц модуля должно быть семь, а не %d' % seen)

    def test_no_one_off_table_class_survived(self):
        """Одноразовых классов таблиц в модуле нет: только vs-table и его
        модификаторы."""
        allowed = re.compile(r'^vs-table(\s+(is-[a-z0-9-]+|vs-num))*$')
        for url in MODULE_ROUTES:
            for _attrs, cls in _tables(self._get(url)):
                self.assertRegex(cls.strip(), allowed,
                                 '%s: посторонний класс таблицы %r' % (url, cls))

    # ---- 2. сортировка -----------------------------------------------------

    def test_static_tables_are_sortable_and_stream_is_not(self):
        """DD-029: клиентская сортировка включается ТОЛЬКО в is-static.

        В is-stream на странице лежит окно, а не выборка; клиентская
        сортировка переставила бы строки внутри окна и выглядела бы как
        сортировка всего журнала.
        """
        for url in MODULE_ROUTES:
            for attrs, cls in _tables(self._get(url)):
                if 'is-static' in cls:
                    self.assertIn('data-sortable', attrs,
                                  '%s: is-static без data-sortable' % url)
                else:
                    self.assertNotIn('data-sortable', attrs,
                                     '%s: не-static с data-sortable' % url)

    def test_client_default_sort_never_overrides_the_server_order(self):
        """Экраны с осмысленным серверным порядком НЕ объявляют
        data-sort-default.

        `/ref/organizations` отдаёт ORDER BY sort_order — порядок,
        расставленный владельцем руками. `/ref/equipment` отдаёт
        «организация -> категория -> название». Клиентская сортировка по одной
        колонке при загрузке стёрла бы и то, и другое.
        """
        for url in ('/ref/organizations', '/ref/equipment'):
            html = self._get(url)
            for attrs, _cls in _tables(html):
                self.assertNotIn('data-sort-default', attrs,
                                 '%s: серверный порядок подменён клиентским' % url)

        # Обратная сторона: там, где серверный порядок И ЕСТЬ одна колонка,
        # умолчание объявлено явно, а не угадывается.
        for url, expected in (('/ref/customers', '1:ascending'),
                              ('/ref/work_types', '0:ascending'),
                              ('/admin/users', '0:ascending'),
                              ('/admin/permissions', '0:ascending')):
            html = self._get(url)
            self.assertIn('data-sort-default="%s"' % expected, html,
                          '%s: умолчание сортировки не объявлено' % url)

    # ---- 3. охват окна -----------------------------------------------------

    def test_audit_window_scope_comes_from_the_route(self):
        """Предел выборки печатается из значения маршрута, а не константой в
        шаблоне.

        Отрицательный контроль встроен: предел подменяется на 7, и в HTML
        обязано появиться именно 7. Если бы шаблон писал 300 своей рукой,
        подмена ничего не изменила бы, и проверка не отличала бы верный код
        от неверного.
        """
        html = self._get('/admin/audit')
        self.assertIn('/ 300', html)

        with app.test_request_context():
            from flask import render_template
            patched = render_template(
                'audit_logs.html', logs=[], users=[], actions=[], modules=[],
                date_from='', date_to='', selected_user_id=None,
                selected_action='', selected_module='', audit_log_limit=7)
        self.assertIn('/ 7', patched)
        self.assertNotIn('/ 300', patched)

    def _render_audit(self, rows, limit, lang):
        from flask import g, render_template
        with app.test_request_context():
            g.lang = lang
            return render_template(
                'audit_logs.html', logs=rows, users=[], actions=[], modules=[],
                date_from='', date_to='', selected_user_id=None,
                selected_action='', selected_module='', audit_log_limit=limit)

    def test_audit_says_the_window_is_a_window_only_when_it_is_full(self):
        """Подпись про окно появляется, лишь когда окно набрано целиком:
        на трёх записях из пяти она была бы неправдой.

        Проверяется на ОБОИХ языках: подпись двуязычная, и совпадение только
        по русской строке пропустило бы поломку узбекской.
        """
        row = {'action': 'a', 'status': 'ok', 'created_at': '2026-01-01'}
        for lang, phrase in (('ru', 'журнал длиннее'),
                             ('uz', 'журнал узунроқ')):
            short = self._render_audit([row] * 3, 5, lang)
            full = self._render_audit([row] * 5, 5, lang)
            self.assertNotIn(phrase, short, lang)
            self.assertIn(phrase, full, lang)

    def test_audit_labels_are_the_owner_terms_in_both_languages(self):
        """Термины владельца (2026-08-10): Фильтр, Объект, Тавсиф/Описание.

        Отрицательный контроль встроен в саму пару: если бы t() возвращал
        ключ (именно так шесть подписей Виалон оставались русскими), узбекская
        сторона показала бы «Описание», и вторая половина проверки упала бы.
        """
        row = {'action': 'a', 'status': 'ok', 'created_at': '2026-01-01'}
        ru_html = self._render_audit([row], 5, 'ru')
        uz_html = self._render_audit([row], 5, 'uz')
        for html in (ru_html, uz_html):
            self.assertIn('>Фильтр<', html)
            self.assertIn('>Объект</th>', html)
        self.assertIn('>Описание</th>', ru_html)
        self.assertIn('>Тавсиф</th>', uz_html)
        self.assertNotIn('>Тавсиф</th>', ru_html)
        self.assertNotIn('>Описание</th>', uz_html)

    # ---- 4. выгрузки -------------------------------------------------------

    def test_export_links_survived_the_migration(self):
        """Кнопки Excel переехали в другое место разметки; адрес и параметры
        обязаны остаться прежними."""
        html = self._get('/ref/equipment?org_id=%d&status=active&q=МТЗ' % self.org_id)
        hrefs = re.findall(r'href="(/ref/equipment/export[^"]*)"', html)
        self.assertEqual(len(hrefs), 1, 'ссылка на выгрузку техники не одна')
        href = hrefs[0]
        for token in ('org_id=%d' % self.org_id, 'status=active', 'q='):
            self.assertIn(token, href, 'из ссылки выгрузки пропало %r' % token)

    def test_diagnostic_export_buttons_are_still_on_their_pages(self):
        for url, endpoint in (('/ref/customers', 'customers'),
                              ('/ref/work_types', 'work_types')):
            html = self._get(url)
            self.assertRegex(
                html, r'href="[^"]*%s[^"]*"' % endpoint,
                '%s: кнопка Excel-диагностики пропала' % url)

    # ---- 5. бизнес-правила -------------------------------------------------

    def test_inactive_equipment_row_is_marked_and_readable(self):
        html = self._get('/ref/equipment')
        self.assertIn('vs-row-muted', html,
                      'отключённая техника ничем не отмечена')
        self.assertIn('МТЗ 999 ZZ', html,
                      'отключённая техника пропала со страницы целиком')

    def test_row_actions_all_carry_an_accessible_name(self):
        """DD-031: у иконочной кнопки нет текста. Без aria-label она
        озвучивается как «кнопка» и неотличима от соседней."""
        for url in MODULE_ROUTES:
            html = self._get(url)
            for btn in re.findall(r'<button\b[^>]*vs-btn-icon[^>]*>', html):
                self.assertIn('aria-label=', btn,
                              '%s: иконочная кнопка без имени: %s' % (url, btn))

    def test_permission_checkboxes_carry_an_accessible_name(self):
        html = self._get('/admin/permissions')
        boxes = re.findall(r'<input type="checkbox"[^>]*name="perm_[^>]*>', html)
        self.assertTrue(boxes, 'матрица прав пуста — проверять нечего')
        for box in boxes:
            self.assertIn('aria-label=', box, 'флажок прав без имени: %s' % box)

    # ---- 6. остатки прежней вёрстки ---------------------------------------

    def test_no_local_style_block_left_in_the_module(self):
        for url, template in MODULE_ROUTES.items():
            html = self._get(url)
            # <style> базового шаблона нет вовсе; любой найденный принадлежит
            # странице.
            self.assertNotIn('<style', html,
                             '%s (%s): в разметке остался <style>' % (url, template))

    def test_legacy_badge_classes_are_gone_from_the_module(self):
        dead = ('role-badge', 'idle-badge', 'working-badge', 'ref-stat-card',
                'ref-stats-grid', 'ref-quality-list', 'ref-eq-inactive')
        for url in MODULE_ROUTES:
            html = self._get(url)
            for name in dead:
                self.assertNotIn(name, html,
                                 '%s: остался прежний класс %r' % (url, name))

    def test_work_types_defines_edit_handler_exactly_once(self):
        """В шаблоне лежали ДВА объявления editWt: первое обращалось к
        элементам `wt-edit-<id>`, которых в разметке нет вовсе, и работало
        только потому, что второе его перекрывало. Мёртвый код, обещавший
        другое устройство страницы."""
        html = self._get('/ref/work_types')
        self.assertEqual(len(re.findall(r'function\s+editWt\s*\(', html)), 1)
        self.assertNotIn('wt-edit-', html)
        self.assertNotIn('cancelWtEditLegacy', html)


if __name__ == '__main__':
    unittest.main()
