# -*- coding: utf-8 -*-
"""ADAPT-1: один источник вторичной навигации на три модуля.

Проверяется то, что переезд мог сломать молча:

1. **Права.** Пункт, который пользователю не открыть, не показывается вовсе.
   Гейты переписаны из трёх исходников в один словарь, и ошибка в
   транскрипции дала бы либо пилюлю, ведущую в 403, либо пропавший раздел.
   Ни то, ни другое не видно на экране администратора — а разработчик
   смотрит именно на него.

2. **Два потребителя — один набор.** Полоса внутри страницы и вложенный
   уровень в сайдбаре обязаны показывать одно и то же. Разойдись они — и
   вернётся ровно та беда, ради которой ADAPT-1 делался.

3. **Страница входа.** Набор строится при импорте и обращается к правам;
   у анонимного пользователя такого метода нет. Проверка держит вход.
"""
import re
import unittest

from tests.harness import app, db, reset_db, create_admin, create_org, login
from models import Organization, User, UserModulePermission, ROLE_OPERATOR


def _sidebar_items(html):
    return [x.strip() for x in re.findall(
        r'class="vs-side-sublink[^"]*"[^>]*>([^<]+)<', html)]


def _sidebar_hrefs(html):
    return sorted(set(re.findall(
        r'<a [^>]*?href="([^"]+)"[^>]*class="vs-side-sublink', html)))


def _strip_hrefs(html):
    """Адреса из полосы разделов.

    [REASON]: сравниваются АДРЕСА, а не подписи. У полосы есть заголовок
    группы «Справочники» — он не ссылка и в сайдбаре не появляется вовсе,
    потому что вложенный уровень разворачивает группу в плоский список.
    Сравнение подписей спотыкалось бы об этот заголовок и требовало бы
    исключений, то есть проверяло бы вёрстку вместо состава.
    """
    block = re.search(r'<nav class="vs-pills vs-modulenav vs-mb".*?</nav>', html, re.S)
    if not block:
        return []
    # [REASON]: порядок атрибутов у пилюли и у пункта выпадающего списка
    # разный — href идёт то до class, то после. Выражение, привязанное к
    # порядку, молча теряло четыре ссылки справочников, и тест сообщал о
    # расхождении, которого в коде нет.
    return sorted(set(re.findall(r'<a [^>]*?href="([^"]+)"', block.group(0))))


class ModuleNavPermissionTests(unittest.TestCase):
    def setUp(self):
        reset_db()
        self.admin_id = create_admin()
        self.org_id = create_org()
        with app.app_context():
            User.query.get(self.admin_id).language = 'ru'
            op = User(username='fuel_op', role=ROLE_OPERATOR, language='ru')
            op.set_password('x')
            op.organizations.append(Organization.query.get(self.org_id))
            db.session.add(op)
            db.session.flush()
            db.session.add(UserModulePermission(
                user_id=op.id, module_code='fuel', has_access=True))
            db.session.commit()
            self.op_id = op.id

    def _get(self, user_id, url):
        client = app.test_client()
        login(client, user_id)
        resp = client.get(url)
        self.assertEqual(resp.status_code, 200, url)
        return resp.get_data(as_text=True)

    # ─── права ──────────────────────────────────────────────────────────

    def test_admin_sees_the_administrative_fuel_items(self):
        html = self._get(self.admin_id, '/fuel/transactions')
        items = _sidebar_items(html)
        for label in ('Ручной расход', 'Резерв', 'АЗС', 'Склады',
                      'Начальные остатки'):
            self.assertIn(label, items, label)

    def test_non_admin_sees_none_of_them(self):
        # [REASON]: у модуля АЗС нет гранулярных прав — пять административных
        # страниц закрыты @admin_required_fuel, который проверяет ровно
        # is_admin. Оператор с доступом к модулю не должен видеть ни одной.
        html = self._get(self.op_id, '/fuel/transactions')
        items = _sidebar_items(html)
        for label in ('Ручной расход', 'Резерв', 'Склады', 'Начальные остатки'):
            self.assertNotIn(label, items, label)
        # ...но общедоступные разделы на месте, включая «Карты» в справочниках.
        for label in ('Остатки', 'Выдачи', 'Предупреждения', 'Карты'):
            self.assertIn(label, items, label)

    def test_spare_parts_items_follow_their_own_permissions(self):
        html = self._get(self.admin_id, '/spare-parts/')
        items = _sidebar_items(html)
        for label in ('Рабочий стол', 'Заявки', 'Склад', 'Закупка', 'Акты',
                      'Пора менять', 'Отчёты', 'Каталог', 'Артикул'):
            self.assertIn(label, items, label)

    # ─── два потребителя, один набор ────────────────────────────────────

    def test_sidebar_and_strip_show_the_same_set(self):
        for url in ('/fuel/transactions', '/spare-parts/', '/drones/works'):
            html = self._get(self.admin_id, url)
            self.assertEqual(_sidebar_hrefs(html), _strip_hrefs(html), url)
            self.assertTrue(_sidebar_hrefs(html), url)

    def test_the_same_holds_for_a_restricted_user(self):
        html = self._get(self.op_id, '/fuel/transactions')
        self.assertEqual(_sidebar_hrefs(html), _strip_hrefs(html))

    # ─── границы ────────────────────────────────────────────────────────

    def test_the_nested_level_appears_only_under_the_open_module(self):
        html = self._get(self.admin_id, '/fuel/transactions')
        self.assertIn('Остатки', _sidebar_items(html))
        # На экране вне модулей вложенного уровня нет вовсе.
        self.assertEqual(_sidebar_items(self._get(self.admin_id, '/report')), [])

    def test_login_page_survives_the_anonymous_user(self):
        # Набор строится при импорте и трогает права; у анонима их нет.
        # Падение здесь означало бы, что в систему нельзя войти.
        resp = app.test_client().get('/login')
        self.assertEqual(resp.status_code, 200)

    def test_the_three_partials_carry_no_markup_of_their_own(self):
        # Смысл ADAPT-1: три реализации свелись к одной. Разметка,
        # вернувшаяся в партиал, — регрессия, а не мелочь.
        import os
        root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        for rel in ('templates/_spare_nav.html',
                    'templates/fuel/_fuel_nav.html',
                    'templates/drones/_drones_nav.html'):
            with open(os.path.join(root, rel), encoding='utf-8') as fh:
                body = fh.read()
            self.assertNotIn('<style>', body, rel)
            self.assertNotIn('<script>', body, rel)
            self.assertNotIn('<a ', body, rel)
            self.assertIn('module_nav(', body, rel)


if __name__ == '__main__':
    unittest.main()
