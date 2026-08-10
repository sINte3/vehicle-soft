# -*- coding: utf-8 -*-
"""UI-P6-003: модуль нарядов на контрактах §6.

Проверяется то, что миграция вёрстки могла сломать молча.

1. **Статус — один компонент, семантика параметром (DD-032).** Раньше цвет
   приходил из Python значением `var(--text2)`, `var(--info)`… и подставлялся
   инлайновым `style="color:…"`. Теперь маршрут отдаёт ИМЯ модификатора, и
   палитра остаётся одна на продукт.

2. **Список показан дважды — таблицей и карточками.** Ниже 768 px виден один
   вид, выше — другой. Данные обязаны совпадать: разойдись они, и оператор на
   телефоне увидел бы не тот список.

3. **Латиницы в узбекских подписях нет.** Устав допускает её только в
   названиях продуктов и брендов. Здесь была латиница во всех четырёх
   шаблонах — в отличие от Виалона, русская сторона у этих подписей есть,
   поэтому переписан только узбекский алфавит.

4. **Свой `<h1>` убран.** `base_next` уже рисует заголовок экрана из
   `{% block title %}`.
"""
import re
import unittest
from datetime import date, timedelta

from tests.harness import app, db, reset_db, create_admin, create_org, login
from models import Equipment, WorkOrder

RE_TABLE = re.compile(r'<table\b([^>]*)>', re.I)
RE_CLASS = re.compile(r'class="([^"]*)"')
RE_JINJA_COMMENT = re.compile(r'\{#.*?#\}', re.S)

MODULE_TEMPLATES = ('work_orders_list.html', 'work_order_detail.html',
                    'work_order_form.html', 'work_order_close.html')


def _tables(html):
    html = RE_JINJA_COMMENT.sub(' ', html)
    out = []
    for attrs in RE_TABLE.findall(html):
        cls = RE_CLASS.search(attrs)
        out.append((attrs, cls.group(1) if cls else ''))
    return out


class WorkOrdersModuleContracts(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        reset_db()
        cls.admin_id = create_admin()
        cls.org_id = create_org('Когон ПТЗ')
        with app.app_context():
            eq = Equipment(organization_id=cls.org_id, category='mtz',
                           eq_type='Трактор МТЗ', name='МТЗ 261 EA',
                           plate='80 561 EA', default_unit='га',
                           default_price=50, is_active=True)
            db.session.add(eq)
            db.session.flush()
            cls.eq_id = eq.id
            for n, status in (('WO-0001', 'draft'), ('WO-0002', 'in_progress'),
                              ('WO-0003', 'done')):
                db.session.add(WorkOrder(
                    number=n, status=status, organization_id=cls.org_id,
                    equipment_id=eq.id, planned_date=date.today() - timedelta(days=1),
                    work_type_text='Шудгор', unit='га', planned_quantity=10,
                    price=1000, created_by=cls.admin_id))
            db.session.commit()
            cls.wo_id = WorkOrder.query.filter_by(number='WO-0001').first().id

    def _get(self, url):
        client = app.test_client()
        login(client, self.admin_id)
        response = client.get(url)
        self.assertEqual(response.status_code, 200, url)
        return response.get_data(as_text=True)

    # ---- таблица -----------------------------------------------------------

    def test_list_table_declares_a_mode_and_carries_no_one_off_class(self):
        allowed = re.compile(r'^vs-table(\s+(is-[a-z0-9-]+|vs-num))*$')
        tables = _tables(self._get('/work-orders?status=all'))
        self.assertEqual(len(tables), 1, 'таблица списка должна быть одна')
        attrs, cls = tables[0]
        self.assertRegex(cls.strip(), allowed, 'посторонний класс таблицы %r' % cls)
        self.assertIn('is-static', cls)
        self.assertIn('data-sortable', attrs)

    def test_list_default_sort_does_not_override_the_route_order(self):
        """Маршрут отдаёт свой порядок; клиентское умолчание по одной колонке
        заменило бы его при каждой загрузке."""
        attrs, _cls = _tables(self._get('/work-orders?status=all'))[0]
        self.assertNotIn('data-sort-default', attrs)

    # ---- два вида одного списка -------------------------------------------

    def test_table_and_cards_show_the_same_orders(self):
        """Ниже 768 px виден карточный вид, выше — таблица. Разойдись наборы —
        и оператор на телефоне увидел бы не тот список."""
        html = self._get('/work-orders?status=all')
        table_part = html.split('vs-list-cards', 1)[0]
        cards_part = html.split('vs-list-cards', 1)[1]
        in_table = set(re.findall(r'<strong>(WO-\d+)</strong>', table_part))
        in_cards = set(re.findall(r'<strong class="vs-nowrap">(WO-\d+)</strong>', cards_part))
        self.assertTrue(in_table, 'в таблице нет ни одного наряда')
        self.assertEqual(in_table, in_cards,
                         'таблица и карточки показывают разные наряды: %r vs %r'
                         % (sorted(in_table), sorted(in_cards)))

    # ---- статус ------------------------------------------------------------

    def test_status_is_one_component_with_semantics_as_a_parameter(self):
        """Ни одного инлайнового `style="color:` на статусе, и у каждого
        бейджа — модификатор из набора дизайн-системы."""
        html = self._get('/work-orders?status=all')
        self.assertNotIn('style="color:', html,
                         'статус снова красится инлайновым стилем')
        badges = re.findall(r'<span class="vs-badge([^"]*)"', html)
        self.assertTrue(badges, 'бейджей статуса не найдено')
        allowed = {'', 'vs-badge-info', 'vs-badge-warning', 'vs-badge-success',
                   'vs-badge-danger'}
        for mod in badges:
            self.assertIn(mod.strip(), allowed, 'посторонний тон бейджа %r' % mod)

    def test_status_tones_cover_every_status(self):
        from work_orders import WO_STATUS_TONES, _wo_status_labels
        with app.test_request_context():
            labels = _wo_status_labels()
        self.assertEqual(set(WO_STATUS_TONES), set(labels),
                         'набор тонов и набор подписей разошлись')

    # ---- язык --------------------------------------------------------------

    def test_no_latin_uzbek_left_in_the_module(self):
        """Устав: узбекский только кириллицей. Латиница допустима лишь в
        названиях продуктов и брендов — в этих четырёх шаблонах их нет."""
        for name in MODULE_TEMPLATES:
            with open('templates/' + name, encoding='utf-8') as handle:
                source = handle.read()
            source = RE_JINJA_COMMENT.sub(' ', source)
            source = re.sub(r'<script[^>]*>.*?</script>', ' ', source, flags=re.S | re.I)
            for text in re.findall(r"else '([^']*)'", source):
                letters = re.sub(r'[^A-Za-zА-Яа-яЁёЎўҚқҒғҲҳ]', '', text)
                latin = sum(1 for ch in letters if ch.isascii())
                self.assertEqual(latin, 0, '%s: латиница в подписи %r' % (name, text))

    # ---- остатки прежней вёрстки ------------------------------------------

    def test_no_local_style_block_and_no_own_heading(self):
        for url in ('/work-orders?status=all',
                    '/work-orders/%d' % self.wo_id,
                    '/work-orders/new'):
            html = self._get(url)
            self.assertNotIn('<style', html, '%s: остался <style>' % url)
            body = html.split('vs-screen-title', 1)[-1]
            self.assertNotIn('<h1', body, '%s: остался свой <h1>' % url)

    def test_legacy_scope_classes_are_gone(self):
        dead = ('wo-scope', 'wod-scope', 'wof-scope', 'woc-scope',
                'wo-counter', 'wo-badge', 'wod-card', 'wod-grid', 'history-entry')
        for name in MODULE_TEMPLATES:
            with open('templates/' + name, encoding='utf-8') as handle:
                source = handle.read()
            for cls in dead:
                self.assertNotIn('"%s' % cls, source,
                                 '%s: остался прежний класс %r' % (name, cls))

    def test_form_error_box_is_toggled_by_class_not_inline_style(self):
        """Инлайновый `style.display` возвращает нас к правилу, которое
        перебивается каскадом; переключение классом видно в разметке."""
        with open('templates/work_order_form.html', encoding='utf-8') as handle:
            source = handle.read()
        self.assertIn("classList.add('is-visible')", source)
        self.assertNotIn("box.style.display", source)


if __name__ == '__main__':
    unittest.main()
