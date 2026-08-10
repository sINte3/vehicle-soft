# -*- coding: utf-8 -*-
"""UI-P6-004: модуль запчастей на контрактах §6.

Модуль пришёл в лучшем состоянии, чем предыдущие три: ноль хардкод-цветов,
ноль одноразовых классов таблиц, все таблицы уже на `vs-table`. Разрыв был в
другом, и он не виден чекеру:

1. **Ни одна из 24 таблиц не объявляла режим (DD-028).** `vs-table` без
   `is-static`/`is-paged`/`is-stream` проходит проверку по классам и молчит о
   том, откуда берётся сортировка и что считает итог.

2. **Цвет статуса приходил из Python** — `STATUS_COLORS` и
   `PRICE_STATUS_COLORS` отдавали `var(--text2)`, `var(--accent)`… и шаблон
   красил инлайновым `style="color:"`.

3. **Форма заявки разбирается позиционно** — `getlist('item_name')[i]` к
   `getlist('item_quantity')[i]`. Клиентская сортировка переставила бы строки,
   и количество одной позиции ушло бы к другой.
"""
import re
import unittest
import glob

RE_TABLE = re.compile(r'<table\b([^>]*)>', re.I)
RE_CLASS = re.compile(r'class="([^"]*)"')
RE_JINJA_COMMENT = re.compile(r'\{#.*?#\}', re.S)
RE_GLYPH = re.compile('[\U0001F000-\U0001FAFF☀-➿⬀-⯿]')

MODULE = sorted(glob.glob('templates/spare_part*.html'))
POSITIONAL = 'templates/spare_part_form.html'


def _source(path):
    with open(path, encoding='utf-8') as handle:
        return RE_JINJA_COMMENT.sub(' ', handle.read())


def _tables(html):
    out = []
    for attrs in RE_TABLE.findall(html):
        cls = RE_CLASS.search(attrs)
        out.append((attrs, cls.group(1) if cls else ''))
    return out


class SparePartsModuleContracts(unittest.TestCase):

    def test_the_module_is_not_empty(self):
        """Проверки ниже ходят по маске файлов. Опустей она — все они пройдут
        сами собой и ничего не проверят."""
        self.assertGreaterEqual(len(MODULE), 16, 'шаблонов модуля найдено %d' % len(MODULE))

    def test_every_table_declares_a_mode(self):
        seen = 0
        for path in MODULE:
            for _attrs, cls in _tables(_source(path)):
                classes = set(cls.split())
                self.assertIn('vs-table', classes,
                              '%s: таблица без vs-table, class=%r' % (path, cls))
                declared = classes & {'is-static', 'is-paged', 'is-stream'}
                self.assertEqual(len(declared), 1,
                                 '%s: режим не объявлен однозначно, class=%r' % (path, cls))
                seen += 1
        self.assertGreaterEqual(seen, 24, 'таблиц найдено %d, ожидалось не меньше 24' % seen)

    def test_the_positional_request_form_is_never_sortable(self):
        """Строки позиций заявки разбираются по порядку; сортировка на клиенте
        отдала бы количество одной позиции другой — молча."""
        tables = _tables(_source(POSITIONAL))
        self.assertTrue(tables, 'таблицы позиций не найдено — проверять нечего')
        for attrs, _cls in tables:
            self.assertNotIn('data-sortable', attrs,
                             'таблица позиций заявки сделана сортируемой')

    def test_other_tables_are_sortable(self):
        """Везде, где позиционной связки нет, заголовки обязаны быть
        кнопками — иначе контракт DD-029 объявлен и не выполнен."""
        sortable = 0
        for path in MODULE:
            if path == POSITIONAL:
                continue
            for attrs, _cls in _tables(_source(path)):
                self.assertIn('data-sortable', attrs,
                              '%s: is-static без data-sortable' % path)
                sortable += 1
        self.assertGreaterEqual(sortable, 20)

    def test_no_client_default_sort_overrides_the_route_order(self):
        """Умолчание сортировки не объявляется нигде: маршруты модуля отдают
        свой порядок, и переставлять его при загрузке нельзя."""
        for path in MODULE:
            for attrs, _cls in _tables(_source(path)):
                self.assertNotIn('data-sort-default', attrs,
                                 '%s: серверный порядок подменён клиентским' % path)

    def test_status_semantics_come_as_a_tone_not_a_colour(self):
        from spare_parts import STATUS_TONES, PRICE_STATUS_TONES, STATUS_LABELS, PRICE_STATUS_LABELS
        allowed = {'', 'vs-badge-info', 'vs-badge-warning', 'vs-badge-success',
                   'vs-badge-danger', 'vs-badge-purple'}
        for name, tones, labels in (('STATUS', STATUS_TONES, STATUS_LABELS),
                                    ('PRICE_STATUS', PRICE_STATUS_TONES, PRICE_STATUS_LABELS)):
            self.assertEqual(set(tones), set(labels),
                             '%s: набор тонов разошёлся с набором подписей' % name)
            for status, tone in tones.items():
                self.assertIn(tone, allowed,
                              '%s[%s] = %r — не модификатор дизайн-системы'
                              % (name, status, tone))
        # SP-CANCEL-008: отмена и отказ намеренно делят тон, подпись их различает.
        self.assertEqual(STATUS_TONES['cancelled'], STATUS_TONES['rejected'])
        self.assertNotEqual(STATUS_TONES['cancelled'], STATUS_TONES['draft'])

    def test_no_inline_colour_and_no_local_style_left(self):
        for path in MODULE:
            source = _source(path)
            self.assertNotIn('style="color:', source, '%s: инлайновый цвет' % path)
            self.assertNotIn('<style', source, '%s: локальный <style>' % path)

    def test_no_emoji_left_in_the_module(self):
        for path in MODULE:
            found = RE_GLYPH.findall(_source(path))
            self.assertEqual(found, [], '%s: эмодзи в разметке: %r' % (path, found))

    def test_icons_built_by_javascript_come_from_the_set(self):
        """Две кнопки формы строит скрипт в шаблонной строке — макрос там не
        работает. SVG отрисован в константу ОДИН раз, а не вписан второй
        копией, которая разойдётся с набором."""
        source = _source(POSITIONAL)
        self.assertIn("const ICON_ATTACH = {{ icon('attach', 14)", source)
        self.assertIn("const ICON_CLOSE  = {{ icon('close', 14)", source)
        self.assertIn('${ICON_ATTACH}', source)
        self.assertIn('${ICON_CLOSE}', source)


if __name__ == '__main__':
    unittest.main()
