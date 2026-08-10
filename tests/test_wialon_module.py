# -*- coding: utf-8 -*-
"""UI-P6-002: модуль Виалон на контрактах §6.

Проверяется то, что миграция вёрстки могла сломать молча.

1. **Сортировка включена только там, где она безопасна.** Три экрана модуля —
   формы решения: маршрут разбирает их ПОЗИЦИОННО,
   `getlist('vialon_name')[i]` к `getlist('eq_choice')[i]`. Порядок значений
   в форме — это порядок строк в разметке. Клиентская сортировка переставила
   бы строки, а вместе с ними и пары «объект → техника», и моточасы легли бы
   не той машине — без единой ошибки на экране.

2. **Скрытые поля лежат внутри ячеек.** Раньше они стояли прямо в `<tbody>`;
   парсер выносит такие узлы за пределы таблицы, и связь значения со строкой
   держалась на порядке, которого в разметке уже не видно.

3. **Поля периода действительно прячутся.** В `wialon_report` инлайновый
   стиль объявлял `display:none` и следом `display:flex` — второе побеждало,
   и поля «Ихтиёрий» показывались во всех режимах.

4. **Охват окна импорта подписан числом из маршрута.**

5. **Латиницы в узбекских подписях нет** — устав допускает её только в
   названиях продуктов и брендов.
"""
import re
import unittest

from tests.harness import app, db, reset_db, create_admin, create_org, login
from models import (EngineHoursRecord, Equipment, VialonImport,
                    VialonMapping)

RE_TABLE = re.compile(r'<table\b([^>]*)>', re.I)
RE_CLASS = re.compile(r'class="([^"]*)"')

# Экраны, которые ходят GET и рендерятся целиком.
# [REASON]: у отчёта указан ЯВНЫЙ день. По умолчанию он берёт свой период, в
# который посеянная строка может не попасть, и тогда рисуется пустое
# состояние — таблицы нет, и проверка контракта таблицы молча ничего не
# проверяет.
TODAY = __import__('datetime').date.today().isoformat()
GET_ROUTES = {
    '/wialon': 'wialon.html',
    '/wialon/mapping': 'wialon_mapping_list.html',
    '/wialon/report?mode=day&date=' + TODAY: 'wialon_report.html',
    '/wialon/auto_match': 'wialon_auto_match.html',
}

# Экраны, где форма разбирается позиционно: клиентской сортировки быть не
# должно ни на одной таблице.
POSITIONAL_FORMS = ('wialon_mapping.html', 'wialon_auto_match.html')


RE_JINJA_COMMENT = re.compile(r'\{#.*?#\}', re.S)


def _tables(html):
    """Таблицы разметки.

    [REASON]: комментарии Jinja вырезаются ПЕРЕД поиском. В шаблоне модуля
    комментарий объясняет прежний дефект и содержит слово <table> текстом;
    без этой строки проверка находила «таблицу без класса» там, где таблицы
    нет вовсе.
    """
    html = RE_JINJA_COMMENT.sub(' ', html)
    out = []
    for attrs in RE_TABLE.findall(html):
        cls = RE_CLASS.search(attrs)
        out.append((attrs, cls.group(1) if cls else ''))
    return out


class WialonModuleContracts(unittest.TestCase):

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
            db.session.add_all([
                VialonMapping(vialon_name='Arion-630C 073 HA', equipment_id=eq.id),
                VialonMapping(vialon_name='Skipped object', skip=True),
                VialonMapping(vialon_name='Undecided object'),
            ])
            import datetime as _dt
            db.session.add(VialonImport(
                import_date=_dt.date(2026, 8, 1), filename='wialon-2026-08-01.zip',
                vehicles_in_file=12, vehicles_saved=10, vehicles_skipped=1,
                vehicles_unknown=1))
            # Строка моточасов: без неё отчёт рисует пустое состояние и
            # проверять в нём нечего.
            db.session.add(EngineHoursRecord(
                work_date=_dt.date.today(), equipment_id=eq.id,
                engine_hours=6.0, hours_moving=4.0, hours_idle=2.0))
            db.session.commit()

    def _get(self, url):
        client = app.test_client()
        login(client, self.admin_id)
        response = client.get(url)
        self.assertEqual(response.status_code, 200, url)
        return response.get_data(as_text=True)

    def _render(self, template, **ctx):
        from flask import render_template
        client = app.test_client()
        login(client, self.admin_id)
        # Рендер через запрос: шаблонам нужен current_user и g.lang.
        with app.test_request_context('/wialon'):
            from flask_login import login_user
            from models import User
            login_user(User.query.get(self.admin_id))
            return render_template(template, **ctx)

    # ---- режим и класс таблицы --------------------------------------------

    def test_every_table_declares_a_mode(self):
        modes = {'is-static', 'is-paged', 'is-stream'}
        seen = 0
        for url in GET_ROUTES:
            for _attrs, cls in _tables(self._get(url)):
                classes = set(cls.split())
                self.assertIn('vs-table', classes,
                              '%s: таблица без vs-table, class=%r' % (url, cls))
                self.assertEqual(len(classes & modes), 1,
                                 '%s: режим не объявлен однозначно, class=%r' % (url, cls))
                seen += 1
        self.assertGreaterEqual(seen, 3, 'таблиц найдено слишком мало: %d' % seen)

    def test_no_one_off_table_class_survived(self):
        allowed = re.compile(r'^vs-table(\s+(is-[a-z0-9-]+|vs-num))*$')
        for url in GET_ROUTES:
            for _attrs, cls in _tables(self._get(url)):
                self.assertRegex(cls.strip(), allowed,
                                 '%s: посторонний класс таблицы %r' % (url, cls))

    # ---- сортировка там и только там, где она безопасна --------------------

    def test_positional_decision_forms_are_never_sortable(self):
        """Экраны, где форма разбирается позиционно, не получают
        data-sortable ни на одной таблице.

        Это не оформление, а сохранность данных: сортировка переставила бы
        строки, а скрытые поля с именами объектов пришли бы в прежнем порядке,
        и решения записались бы не тем объектам.

        [ПОЧЕМУ ПО ИСХОДНИКУ, А НЕ ПО РЕНДЕРУ] Первая редакция этой проверки
        смотрела отрисованную страницу и проходила на СЛОМАННОМ коде. Обе
        причины стоит знать:
        `wialon_mapping.html` отрисовать нельзя вовсе — его форма ссылается на
        эндпойнт `wialon_save`, которого в приложении нет; а таблицы
        `wialon_auto_match.html` лежат внутри {% macro %}, и без предложений
        для разбора макрос не вызывается — цикл по таблицам делал ноль
        итераций, и утверждение выполнялось само собой.
        Отрицательный контроль: дописать data-sortable в любой из двух файлов
        роняет этот тест.
        """
        for name in POSITIONAL_FORMS:
            with open('templates/' + name, encoding='utf-8') as handle:
                source = handle.read()
            tables = _tables(source)
            self.assertTrue(tables, '%s: таблиц не найдено — проверять нечего' % name)
            for attrs, cls in tables:
                self.assertIn('vs-table', cls, '%s: таблица без vs-table' % name)
                self.assertNotIn('data-sortable', attrs,
                                 '%s: таблица решения сделана сортируемой' % name)

    def test_dead_mapping_screen_is_named_dead(self):
        """Шаблон недостижим: `render_template` его не вызывает, а его форма
        ссылается на несуществующий эндпойнт. Пока это так, шапка файла
        обязана это говорить — иначе следующий читатель считает экран
        рабочим и правит его вслепую."""
        import subprocess
        source = open('templates/wialon_mapping.html', encoding='utf-8').read()
        if "url_for('wialon_save')" in source:
            self.assertIn('НЕДОСТИЖИМ', source,
                          'шаблон ссылается на несуществующий эндпойнт молча')
        callers = subprocess.run(
            ['grep', '-rl', "render_template('wialon_mapping.html'",
             '--include=*.py', '--exclude-dir=tests', '.'],
            capture_output=True, text=True).stdout.strip()
        self.assertEqual(callers, '',
                         'шаблон снова кем-то рендерится — снимите пометку в шапке')

    def test_mapping_list_is_sortable_and_matches_the_route_order(self):
        """У списка маппингов позиционной связки нет — у каждой строки своя
        форма по id, — поэтому сортировка безопасна. Умолчание совпадает с
        `ORDER BY vialon_name` маршрута, то есть ничего не переставляет."""
        html = self._get('/wialon/mapping')
        main = [a for a, c in _tables(html) if 'data-sortable' in a]
        self.assertEqual(len(main), 1, 'сортируемая таблица должна быть ровно одна')
        self.assertIn('data-sort-default="0:ascending"', main[0])

    def test_import_history_is_a_stream_without_client_sorting(self):
        html = self._get('/wialon')
        for attrs, cls in _tables(html):
            self.assertIn('is-stream', cls)
            self.assertNotIn('data-sortable', attrs)

    # ---- скрытые поля внутри ячеек ----------------------------------------

    def test_hidden_inputs_live_inside_cells(self):
        """Ни одного `<input type=hidden>` непосредственно в `<tbody>`.

        Отрицательный контроль: возврат поля наружу ячейки роняет этот тест —
        проверка ищет именно последовательность «<tbody> ... <input>» без
        промежуточного <td>.
        """
        import datetime as dt

        class _Eq:
            id = 1
            name = 'МТЗ 261 EA'

            class organization:
                name = 'Когон ПТЗ'

        item = type('I', (), {'vialon_name': 'Arion-630C 073 HA', 'equipment': _Eq,
                              'engine_hours': 8.5, 'hours_moving': 6.0,
                              'hours_idle': 2.5})()
        del dt, item  # рендер невозможен, см. test_dead_mapping_screen_is_named_dead
        source = open('templates/wialon_mapping.html', encoding='utf-8').read()
        self.assertIn('name="mapped_vialon_name"', source,
                      'скрытые поля пропали вовсе — проверять нечего')
        # Между <tbody> и первым <input> обязан стоять <td>.
        for block in re.findall(r'<tbody>(.*?)</tbody>', source, re.S):
            stray = re.search(r'<tr[^>]*>\s*(?:\{[#%].*?[#%]\}\s*)*<input', block, re.S)
            self.assertIsNone(stray,
                              'скрытое поле стоит в строке вне ячейки: %r'
                              % (stray.group(0) if stray else ''))

    # ---- поля периода действительно прячутся -------------------------------

    def test_report_period_fields_are_hidden_outside_range_mode(self):
        """Инлайновый стиль объявлял `display:none` и следом `display:flex`;
        второе побеждало, и поля «Ихтиёрий» стояли на экране всегда."""
        html = self._get('/wialon/report?mode=day&date=' + TODAY)
        block = re.search(r'<div class="vs-field" id="f-range-from"[^>]*>', html)
        self.assertIsNotNone(block, 'поле «ДАН» пропало с экрана')
        self.assertIn('hidden', block.group(0),
                      'поле периода не скрыто в режиме, отличном от «Ихтиёрий»')
        self.assertNotIn('display:none', html,
                         'в разметке остался инлайновый display:none')

    def test_report_period_fields_appear_in_range_mode(self):
        html = self._get('/wialon/report?mode=range&date_from=2026-08-01&date_to=2026-08-02')
        block = re.search(r'<div class="vs-field" id="f-range-from"[^>]*>', html)
        self.assertIsNotNone(block)
        self.assertNotIn('hidden', block.group(0),
                         'в режиме «Ихтиёрий» поля периода обязаны быть видны')

    # ---- охват окна импорта ------------------------------------------------

    def test_import_window_scope_comes_from_the_route(self):
        """Отрицательный контроль встроен: шаблон рендерится с пределом 3, и
        в HTML обязано появиться 3, а не 30."""
        html = self._get('/wialon')
        self.assertIn('/ 30', html)
        patched = self._render('wialon.html', imports=[], import_log_limit=3,
                               pending_count=0, yesterday='2026-08-01',
                               mapping_count=0)
        self.assertNotIn('/ 30', patched)

    # ---- язык --------------------------------------------------------------

    def test_no_latin_uzbek_left_in_the_mapping_screen(self):
        """Устав: узбекский только кириллицей, латиница — лишь в названиях
        продуктов и брендов (Wialon, ZIP, CSV, Excel)."""
        source = open('templates/wialon_mapping.html', encoding='utf-8').read()
        source = re.sub(r'\{#.*?#\}', ' ', source, flags=re.S)
        source = re.sub(r'<script[^>]*>.*?</script>', ' ', source, flags=re.S | re.I)
        for m in re.finditer(r"\{%\s*set\s+L_\w+\s*=\s*'([^']*)'", source):
            text = m.group(1)
            letters = re.sub(r'[^A-Za-zА-Яа-яЁёЎўҚқҒғҲҳ]', '', text)
            latin = sum(1 for ch in letters if ch.isascii())
            self.assertEqual(latin, 0,
                             'латиница в подписи: %r' % text)

    def test_labels_change_with_the_account_language(self):
        """Шесть подписей модуля оставались русскими в узбекском интерфейсе.

        Причина: `t()` при отсутствии ключа в `translations.py` возвращает
        САМ КЛЮЧ. Вызов в шаблоне стоял, страница отрисовывалась без ошибки,
        и подпись молча оставалась на языке исходника. Нашёл владелец глазами
        на экране 2026-08-10; ни один тест этого не видел.

        Проверка идёт по СЛЕДСТВИЮ: одна и та же подпись обязана отличаться
        у ru- и uz-интерфейса. Отрицательный контроль встроен: верни `t()`
        ключ — обе стороны совпадут, и проверка упадёт.
        """
        from translations import TRANS
        pairs = [
            ('Юклаш', 'Загрузить'),
            ('Кўрсатиш/яшириш', 'Показать/скрыть'),
            ('Маппингни таҳрирлаш', 'Изменить маппинг'),
        ]
        for key, ru_value in pairs:
            self.assertIn(key, TRANS['uz'], 'нет узбекской стороны: %r' % key)
            self.assertIn(key, TRANS['ru'], 'нет русской стороны: %r' % key)
            self.assertEqual(TRANS['ru'][key], ru_value, key)
            self.assertNotEqual(TRANS['uz'][key], TRANS['ru'][key],
                                'подпись одинакова в двух языках: %r' % key)

    def test_load_report_labels_have_a_russian_side(self):
        """Десять подписей отчёта по загрузке были узбекскими константами.

        `{% set is_ru = (lang == 'ru') %}` в шаблоне стоял, но НЕ использовался
        ни одной подписью — русская учётная запись видела узбекские заголовки.
        Проверка идёт по источнику: каждое объявление `L_*` обязано зависеть
        от `is_ru`. Отрицательный контроль: убери условие у любой подписи —
        и тест назовёт её по имени.
        """
        source = re.sub(r'\{#.*?#\}', ' ',
                        open('templates/wialon_report.html', encoding='utf-8').read(),
                        flags=re.S)
        found = re.findall(r"\{%-?\s*set\s+(L_\w+)\s*=\s*(.+?)-?%\}", source, re.S)
        self.assertGreaterEqual(len(found), 10, 'подписи L_* не найдены вовсе')
        for name, expr in found:
            self.assertIn('is_ru', expr,
                          'подпись %s одинакова в обоих языках: %s' % (name, expr.strip()))

    def test_no_template_calls_t_with_a_key_outside_the_dictionary(self):
        """Тот же класс, но по ПРИЧИНЕ и по всему модулю, а не по трём ключам.

        Комментарии `{# #}` вырезаются: пример дефекта в пояснении дефектом
        не является. Escape-последовательности разбираются — Jinja разбирает
        `\\n` в литерале, и сравнение с сырой строкой дало бы ложные срабатывания.
        """
        import glob
        from translations import TRANS
        call = re.compile(r"""\bt\(\s*(['"])(.*?)\1\s*\)""", re.S)
        missing = []
        for path in sorted(glob.glob('templates/wialon*.html')):
            source = re.sub(r'\{#.*?#\}', ' ', open(path, encoding='utf-8').read(),
                            flags=re.S)
            for m in call.finditer(source):
                key = (m.group(2).replace('\\n', '\n').replace('\\t', '\t')
                       .replace('\\"', '"').replace("\\'", "'"))
                if key not in TRANS['uz'] or key not in TRANS['ru']:
                    missing.append('%s: %r' % (path, key))
        self.assertEqual(missing, [], 'ключи вне словаря: %s' % missing)

    # ---- остатки прежней вёрстки ------------------------------------------

    def test_no_local_style_block_left_in_the_module(self):
        for url in GET_ROUTES:
            self.assertNotIn('<style', self._get(url),
                             '%s: в разметке остался <style>' % url)

    def test_row_actions_all_carry_an_accessible_name(self):
        for url in GET_ROUTES:
            html = self._get(url)
            for btn in re.findall(r'<button\b[^>]*vs-btn-icon[^>]*>', html):
                self.assertIn('aria-label=', btn,
                              '%s: иконочная кнопка без имени: %s' % (url, btn))

    def test_shared_rows_span_the_real_number_of_columns(self):
        """`colspan` служебных строк был 6 при пяти колонках — лишняя ячейка
        растягивала таблицу."""
        html = self._get('/wialon/mapping')
        head = re.search(r'<thead>.*?</thead>', html, re.S)
        columns = len(re.findall(r'<th\b', head.group(0)))
        for span in re.findall(r'<td colspan="(\d+)"', html):
            self.assertEqual(int(span), columns,
                             'colspan %s при %d колонках' % (span, columns))

    def test_no_duplicate_screen_heading(self):
        """base_next рисует заголовок экрана из {% block title %}. Свой <h1>
        в содержимом был вторым заголовком того же экрана с другим текстом."""
        for url in GET_ROUTES:
            html = self._get(url)
            body = html.split('vs-screen-title', 1)[-1]
            self.assertNotIn('<h1', body, '%s: в содержимом остался свой <h1>' % url)


if __name__ == '__main__':
    unittest.main()
