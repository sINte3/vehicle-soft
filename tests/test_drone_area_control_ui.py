# -*- coding: utf-8 -*-
"""DRONE-AREA-CONTROL-V2 · UX: контракт экрана «Контроль площади DJI» без
Flask -- шаблон и примитивы design-system.css как исходный текст.

Идёт в CI (stdlib). Отрисованную страницу проверяют Flask-тесты
`tests/test_drone_area_control_v2_web.py` (класс TreeLayout) тем же
разборщиком `parse_html`; поведение скрипта и ширину на 1366/1440/1920 --
`tools/ux/check_area_control.mjs` в настоящем браузере.

Что держится здесь:

* реестр -- шесть колонок, прежних одиннадцати нет; ширины -- <colgroup>;
* уровни L1/L2/L3/детали помечены классами и data-level; всё ниже дронов
  отдано сервером скрытым (по умолчанию -- только дроны);
* четыре команды дерева живут в thead, а thead -- липкий;
* длинные тексты только в строке деталей на всю ширину (colspan="6");
* версии правил -- в свёрнутом <details> внизу;
* каждый класс шаблона определён в CSS; в новом блоке CSS нет обрезки
  (overflow: hidden) и нет запрета переноса, кроме чисел.

У каждой проверки -- отрицательный контроль на фикстуре прежней формы.
"""

import io
import os
import re
import sys
import unittest
from html.parser import HTMLParser

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TEMPLATE = os.path.join(REPO_ROOT, 'templates', 'drones', 'area_control.html')
CSS = os.path.join(REPO_ROOT, 'static', 'css', 'design-system.css')

COMMANDS = ('drones', 'problems', 'all', 'none')


# ─── Разборщик: небольшое дерево узлов поверх html.parser ─────────────────

VOID = frozenset(('area', 'base', 'br', 'col', 'embed', 'hr', 'img', 'input',
                  'link', 'meta', 'source', 'track', 'wbr'))


class Node(object):
    __slots__ = ('tag', 'attrs', 'children', 'parent')

    def __init__(self, tag, attrs, parent):
        self.tag = tag
        self.attrs = dict(attrs)
        self.children = []
        self.parent = parent

    def text(self):
        return ''.join(c if isinstance(c, str) else c.text()
                       for c in self.children)

    def classes(self):
        return (self.attrs.get('class') or '').split()

    def walk(self):
        yield self
        for child in self.children:
            if not isinstance(child, str):
                for node in child.walk():
                    yield node

    def find_all(self, tag=None, cls=None, **attrs):
        out = []
        for node in self.walk():
            if node is self:
                continue
            if tag and node.tag != tag:
                continue
            if cls and cls not in node.classes():
                continue
            if any(node.attrs.get(k.replace('_', '-')) != v
                   for k, v in attrs.items()):
                continue
            out.append(node)
        return out

    def find(self, tag=None, cls=None, **attrs):
        found = self.find_all(tag, cls, **attrs)
        return found[0] if found else None

    def ancestors(self):
        node = self.parent
        while node is not None:
            yield node
            node = node.parent


class _Builder(HTMLParser):

    def __init__(self):
        HTMLParser.__init__(self, convert_charrefs=True)
        self.root = Node('#root', [], None)
        self.current = self.root

    def handle_starttag(self, tag, attrs):
        node = Node(tag, attrs, self.current)
        self.current.children.append(node)
        if tag not in VOID:
            self.current = node

    def handle_startendtag(self, tag, attrs):
        self.current.children.append(Node(tag, attrs, self.current))

    def handle_endtag(self, tag):
        node = self.current
        while node is not None and node.tag != tag:
            node = node.parent
        if node is not None and node.parent is not None:
            self.current = node.parent

    def handle_data(self, data):
        self.current.children.append(data)


def parse_html(html):
    builder = _Builder()
    builder.feed(html)
    builder.close()
    return builder.root


def tree_table(root):
    """Таблица-дерево экрана (атрибут data-area-tree) либо None."""
    for node in root.find_all('table'):
        if 'data-area-tree' in node.attrs:
            return node
    return None


def column_heads(table):
    thead = table.find('thead')
    return [th.text().strip() for th in thead.find_all('th')] if thead else []


def tree_rows(table):
    tbody = table.find('tbody')
    return [tr for tr in (tbody.find_all('tr') if tbody else [])
            if 'data-level' in tr.attrs]


# ─── Исходный текст ───────────────────────────────────────────────────────

def read(path):
    with io.open(path, encoding='utf-8') as handle:
        return handle.read()


def strip_jinja(source):
    """Разметка без Jinja: теги {% %} и {# #} убраны, {{ }} -> 'X'."""
    source = re.sub(r'\{#.*?#\}', '', source, flags=re.S)
    source = re.sub(r'\{%.*?%\}', '', source, flags=re.S)
    return re.sub(r'\{\{.*?\}\}', 'X', source, flags=re.S)


def css_block(css, start, end):
    """Кусок CSS между двумя метками (включая первую)."""
    begin = css.index(start)
    return css[begin:css.index(end, begin)]


# Прежняя форма реестра (сокращённо) -- отрицательный контроль.
OLD_SHAPE = """
<table class="vs-table is-static" data-area-tree data-expand="1">
  <thead><tr>
    <th>+/−</th><th>Дрон / день / вылет</th><th>Время (UTC+5)</th>
    <th class="right">DJI RAW, га</th><th class="right">Принято автоматически, га</th>
    <th class="right">Принято, га</th><th class="right">Исключено, га</th>
    <th>Статус</th><th>Причина</th><th>Цепочка A → B → C</th>
    <th>Решение администратора</th>
  </tr></thead>
  <tbody>
    <tr data-level="1" data-node="d1"><td>x</td></tr>
    <tr class="vs-accord-l2" data-level="2" data-node="d1-1" data-parent="d1"><td>x</td></tr>
    <tr class="vs-accord-l3" data-level="3" data-parent="d1-1"><td>Повтор</td></tr>
  </tbody>
</table>
"""


class Parser(unittest.TestCase):

    def test_the_parser_sees_nesting_attributes_and_text(self):
        root = parse_html('<div class="a b"><p>x<b>y</b></p><col>'
                          '<tr hidden data-level="2"><td>z</td></tr></div>')
        div = root.find('div')
        self.assertEqual(div.classes(), ['a', 'b'])
        self.assertEqual(div.text(), 'xyz')
        row = root.find('tr')
        self.assertIn('hidden', row.attrs)
        self.assertEqual(row.attrs['data-level'], '2')
        self.assertIs(root.find('td').parent, row)


class TemplateShape(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        cls.source = read(TEMPLATE)
        cls.root = parse_html(strip_jinja(cls.source))
        cls.table = tree_table(cls.root)

    def test_six_columns_not_the_old_eleven(self):
        heads = column_heads(self.table)
        self.assertEqual(len(heads), 6, heads)
        self.assertEqual(len(self.table.find('colgroup').find_all('col')), 6)
        for old in ('Принято автоматически', 'Причина', 'Цепочка',
                    'Решение администратора', 'Время (UTC+5)', '+/−'):
            self.assertFalse(any(old in h for h in heads), old)
        # Отрицательный контроль: прежняя форма -- одиннадцать, со старыми.
        old = tree_table(parse_html(OLD_SHAPE))
        self.assertEqual(len(column_heads(old)), 11)
        self.assertTrue(any('Причина' in h for h in column_heads(old)))

    def test_levels_carry_classes_and_everything_below_drones_is_hidden(self):
        rows = tree_rows(self.table)
        levels = {}
        for row in rows:
            levels.setdefault(row.attrs['data-level'], []).append(row)
        self.assertEqual(sorted(levels), ['1', '2', '3', '4'])
        expected_class = {'1': 'vs-tree-l1', '2': 'vs-tree-l2',
                          '3': 'vs-tree-l3', '4': 'vs-tree-detail'}
        for level, found in levels.items():
            for row in found:
                self.assertIn(expected_class[level], row.classes(), level)
                self.assertEqual('hidden' in row.attrs, level != '1', level)
        # Отрицательный контроль: в прежней форме L1 без класса и всё
        # раскрыто скриптом.
        old_rows = tree_rows(tree_table(parse_html(OLD_SHAPE)))
        self.assertNotIn('vs-tree-l1', old_rows[0].classes())
        self.assertFalse(any('hidden' in r.attrs for r in old_rows))

    def test_the_four_commands_live_in_the_head(self):
        thead = self.table.find('thead')
        buttons = [b for b in thead.find_all('button')
                   if 'data-tree-expand' in b.attrs]
        self.assertEqual([b.attrs['data-tree-expand'] for b in buttons],
                         list(COMMANDS))
        self.assertTrue(all(b.attrs.get('type') == 'button'
                            for b in buttons))
        self.assertEqual(self.table.attrs.get('data-tree-default'), 'drones')
        # Скрипт знает все четыре команды -- и только их.
        script = self.source[self.source.rindex('<script>'):]
        for command in COMMANDS:
            self.assertRegex(script, r'\b%s: function' % command)
        # Отрицательный контроль: в прежней форме команд в thead нет.
        old = tree_table(parse_html(OLD_SHAPE)).find('thead')
        self.assertEqual(old.find_all('button'), [])

    def test_long_texts_only_in_the_full_width_detail_row(self):
        details = [r for r in tree_rows(self.table)
                   if r.attrs['data-level'] == '4']
        self.assertEqual(len(details), 1)
        (cell,) = details[0].find_all('td')
        self.assertEqual(cell.attrs.get('colspan'), '6')
        text = cell.text()
        for label in ('Причина', 'Доказательство', 'Автоматический результат',
                      'Цепочка A → B → C', 'Решение администратора'):
            self.assertIn(label, self.source)
            self.assertIn(label, self._detail_source(), label)
        self.assertTrue(text.strip())
        # Строка вылета -- ровно шесть ячеек, без длинных полей.
        (flight,) = [r for r in tree_rows(self.table)
                     if r.attrs['data-level'] == '3'
                     and 'data-flight' in r.attrs]
        self.assertEqual(len(flight.find_all('td')), 6)
        for label in ('reason_text', 'evidence_label', 'chain'):
            self.assertNotIn(label, self._flight_row_source(), label)
        # Все colspan реестра -- шесть (детали, команды, пустое состояние).
        self.assertEqual(set(re.findall(r'colspan="(\d+)"', self.source)),
                         {'6'})

    def _flight_row_source(self):
        start = self.source.index('<tr class="vs-tree-l3" data-level="3" '
                                  'data-node=')
        return self.source[start:self.source.index('</tr>', start)]

    def _detail_source(self):
        start = self.source.index('<tr class="vs-tree-detail"')
        return self.source[start:self.source.index('</tr>', start)]

    def test_versions_are_in_a_closed_technical_block_at_the_bottom(self):
        details = self.root.find('details', cls='vs-tech-details')
        self.assertIsNotNone(details)
        self.assertNotIn('open', details.attrs)
        start = self.source.index('<details class="vs-tech-details')
        for name in ('rule_version', 'algorithm_version', 'classes_version',
                     'decisions_version'):
            self.assertEqual(self.source.count('{{ %s }}' % name), 1, name)
            self.assertGreater(self.source.index('{{ %s }}' % name), start)
        self.assertGreater(start, self.source.index('</table>'))

    def test_no_inline_style_in_the_redesigned_blocks(self):
        begin = self.source.index('<section class="vs-formula')
        tail = self.source[begin:]
        self.assertNotIn('style=', tail)
        self.assertNotIn('<style', self.source)

    def test_every_class_of_the_template_is_defined_in_the_css(self):
        css = read(CSS)
        selectors = set(re.findall(r'\.([a-zA-Z][\w-]*)', re.sub(
            r'/\*.*?\*/', '', css, flags=re.S)))
        used = set()
        for value in re.findall(r'class="([^"]*)"', strip_jinja(self.source)):
            used.update(t for t in value.split() if t != 'X')
        missing = sorted(used - selectors - {'right'})
        self.assertEqual(missing, [])
        self.assertGreater(len(used), 40)
        # Отрицательный контроль: выдуманный класс ловится.
        self.assertNotIn('vs-tree-nonexistent', selectors)


class Primitives(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        css = read(CSS)
        cls.block = css_block(css, '.vs-tree-panel {', '.vs-tech-list')

    def rule(self, selector):
        match = re.search(re.escape(selector) + r'\s*\{([^}]*)\}', self.block)
        self.assertIsNotNone(match, selector)
        return match.group(1)

    def test_the_head_sticks_under_the_top_bar(self):
        self.assertIn('position: sticky', self.rule('.vs-table.is-tree thead'))
        self.assertIn('top: var(--vs-top-h)',
                      self.rule('.vs-table.is-tree thead'))

    def test_the_panel_clips_without_becoming_a_scroll_container(self):
        # overflow: hidden сделал бы панель контейнером прокрутки, и
        # липкая шапка прилипала бы к панели, а не к окну.
        panel = self.rule('.vs-tree-panel')
        self.assertIn('overflow: clip', panel)
        self.assertNotIn('overflow: hidden', self.block)

    def test_horizontal_scroll_is_only_a_narrow_fallback(self):
        self.assertNotIn('overflow-x', self.block.split('@media')[0])
        narrow = self.block[self.block.index('@media (max-width: 1024px)'):]
        self.assertIn('.vs-tree-scroll { overflow-x: auto; }', narrow)
        for width in re.findall(r'@media \(max-width: (\d+)px\)',
                                self.block):
            self.assertIn(width, ('1280', '1024', '768', '480'))

    def test_only_numbers_refuse_to_wrap(self):
        nowrap = [m.start() for m in re.finditer(r'white-space: nowrap',
                                                 self.block)]
        self.assertEqual(len(nowrap), 1)
        self.assertIn('white-space: nowrap',
                      self.rule('.vs-table.is-tree td.right'))
        self.assertIn('white-space: normal',
                      self.rule('.vs-table.is-tree .vs-badge'))
        self.assertIn('table-layout: fixed', self.rule('.vs-table.is-tree'))

    def test_levels_are_visibly_different(self):
        l1 = self.rule('.vs-table.is-tree tr.vs-tree-l1 td')
        self.assertIn('background: var(--vs-primary-soft)', l1)
        self.assertIn('font-weight: 700', l1)
        l2 = self.rule('.vs-table.is-tree tr.vs-tree-l2 td.vs-tree-label')
        l3 = self.rule('.vs-table.is-tree tr.vs-tree-l3 td.vs-tree-label')
        pad = lambda text: int(re.search(r'padding-left: (\d+)px',  # noqa
                                         text).group(1))
        self.assertLess(pad(l2), pad(l3))
        self.assertIn('border-left: 3px solid var(--vs-primary)',
                      self.rule('.vs-tree-detail-body'))

    def test_the_block_uses_tokens_only(self):
        self.assertEqual(re.findall(r'#[0-9a-fA-F]{3,6}\b|rgba?\(',
                                    re.sub(r'/\*.*?\*/', '', self.block,
                                           flags=re.S)), [])


if __name__ == '__main__':
    unittest.main()
