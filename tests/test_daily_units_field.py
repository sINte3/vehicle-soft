# -*- coding: utf-8 -*-
"""DD-004: поле единицы измерения в дневном вводе — выбор, а не свободный текст.

Проверяется ровно то, что заявлено шагом трека UI:
  * в форме не осталось ни одного текстового поля единицы;
  * сохранение пишет `unit_code` кодом и `unit` каноническим русским ярлыком;
  * нераспознанная историческая единица не стирается пересохранением;
  * подсунутый мимо интерфейса код отвергается, а не сохраняется молча;
  * карта приведения в приложении и в миграции — одна и та же.

Работает на одноразовой SQLite через общий harness: боевых данных не касается.
"""
import re
import unittest
from datetime import date

from tests.harness import app, db, reset_db, create_admin, create_org, login, CSRF
from models import DailyRecord, DailyRecordUnit, Equipment, WorkType, User

import daily_units
import migrate_daily_units_001


UNIT_ROWS = [
    ('ga', 'га', 'га', 10),
    ('soat', 'час', 'соат', 20),
    ('m_soat', 'моточас', 'мотосоат', 30),
    ('reys', 'рейс', 'рейс', 40),
    ('tn_km', 'тн/км', 'тн/км', 50),
    ('km', 'км', 'км', 60),
]

# Та самая запись из PR-050: в поле единицы вписан абзац инструкции.
PARAGRAPH = ('м-соат будем считать с 08:00-18:00 если техника работала весь '
             'день то ставим 10 моточасов а если меньше то пишем сколько '
             'фактически отработала по путевому листу и сверяем с виалоном')


class AliasMapParityTests(unittest.TestCase):
    """Карта одна на проект; расхождение двух копий должно быть громким."""

    def test_alias_map_matches_the_migration(self):
        self.assertEqual(daily_units.UNIT_ALIASES,
                         migrate_daily_units_001.ALIASES)

    def test_length_ceiling_matches_the_migration(self):
        self.assertEqual(daily_units.MAX_UNIT_LEN,
                         migrate_daily_units_001.MAX_UNIT_LEN)

    def test_every_alias_points_at_a_directory_code(self):
        codes = {row[0] for row in UNIT_ROWS}
        self.assertEqual(set(daily_units.UNIT_ALIASES.values()), codes)


class ResolveUnitCodeTests(unittest.TestCase):
    CODES = {row[0] for row in UNIT_ROWS}

    def test_known_spellings_collapse_onto_one_code(self):
        for raw in ('га', 'Га', ' ГА ', 'гектар', 'ga'):
            self.assertEqual(daily_units.resolve_unit_code(raw, self.CODES), 'ga', raw)
        for raw in ('м-соат', 'Мотосоат', 'моточас', 'M-soat'):
            self.assertEqual(daily_units.resolve_unit_code(raw, self.CODES), 'm_soat', raw)

    def test_paragraph_is_not_a_unit(self):
        # [REASON]: отрицательный контроль на самый опасный случай. Префиксное
        # сравнение сопоставило бы этот абзац с m_soat — он начинается со слова
        # «м-соат». Точное сравнение обязано вернуть None.
        self.assertGreater(len(PARAGRAPH), daily_units.MAX_UNIT_LEN)
        self.assertIsNone(daily_units.resolve_unit_code(PARAGRAPH, self.CODES))

    def test_unknown_and_empty_are_unrecognised(self):
        for raw in ('', None, '   ', 'литр', 'штука'):
            self.assertIsNone(daily_units.resolve_unit_code(raw, self.CODES), raw)

    def test_code_absent_from_the_directory_is_unrecognised(self):
        # Строку справочника можно выключить; после этого её код не подставляем.
        self.assertIsNone(daily_units.resolve_unit_code('км', {'ga', 'soat'}))

    def test_raw_sentinel_round_trips(self):
        value = daily_units.RAW_PREFIX + PARAGRAPH
        self.assertTrue(daily_units.is_raw_unit(value))
        self.assertEqual(daily_units.raw_unit_value(value), PARAGRAPH)
        self.assertFalse(daily_units.is_raw_unit('ga'))
        self.assertEqual(daily_units.raw_unit_value('ga'), '')

    def test_sentinel_can_never_collide_with_a_code(self):
        for code in (row[0] for row in UNIT_ROWS):
            self.assertFalse(daily_units.is_raw_unit(code), code)


class DailyEntryUnitFieldTests(unittest.TestCase):
    """Поверхность: маршрут, разметка формы и обработчик сохранения."""

    def setUp(self):
        reset_db()
        self.admin_id = create_admin()
        self.org_id = create_org()
        self.work_date = date(2026, 8, 1)
        with app.app_context():
            for code, ru, uz, order in UNIT_ROWS:
                db.session.add(DailyRecordUnit(code=code, name_ru=ru, name_uz=uz,
                                               is_active=True, sort_order=order))
            eq = Equipment(name='МТЗ-82', plate='01A123AA', category='mtz',
                           organization_id=self.org_id, is_active=True,
                           default_unit='Га', default_price=100000)
            db.session.add(eq)
            # Вид работ хранит единицу свободным текстом — «м-соат», не код.
            db.session.add(WorkType(name='Шудгорлаш', default_unit='м-соат',
                                    default_price=200000))
            # Язык берётся из учётной записи, а не из адреса: параметр в URL
            # приложение игнорирует. Ставим явно, чтобы проверка ярлыков не
            # зависела от значения по умолчанию.
            User.query.get(self.admin_id).language = 'ru'
            db.session.commit()
            self.eq_id = eq.id
        self.client = app.test_client()
        login(self.client, self.admin_id)

    def _set_lang(self, lang):
        with app.app_context():
            User.query.get(self.admin_id).language = lang
            db.session.commit()

    def _get_form(self):
        resp = self.client.get(f'/entry?date={self.work_date.isoformat()}'
                               f'&org_id={self.org_id}')
        self.assertEqual(resp.status_code, 200)
        return resp.get_data(as_text=True)

    def _save(self, unit_value, extra=None):
        form = {
            'csrf_token': CSRF,
            'org_id': str(self.org_id),
            'work_date': self.work_date.isoformat(),
            f'eq_{self.eq_id}_status': 'working',
            f'eq_{self.eq_id}_valid_lines': '0',
            f'eq_{self.eq_id}_line_0_work_type': 'Шудгорлаш',
            f'eq_{self.eq_id}_line_0_unit': unit_value,
            f'eq_{self.eq_id}_line_0_quantity': '12.5',
            f'eq_{self.eq_id}_line_0_price': '200000',
            f'eq_{self.eq_id}_line_0_payment_type': 'internal',
        }
        if extra:
            form.update(extra)
        return self.client.post('/entry/save', data=form, follow_redirects=False)

    def _records(self):
        with app.app_context():
            return DailyRecord.query.filter_by(work_date=self.work_date).all()

    # ─── разметка ───────────────────────────────────────────────────────

    def test_no_free_text_unit_field_is_left_in_the_form(self):
        html = self._get_form()
        # Оба места: серверная разметка и JS-шаблон добавления строки.
        # [REASON]: правка только одного оставила бы форму наполовину свободной,
        # и глазами это не видно — второе поле появляется лишь по кнопке.
        leftovers = re.findall(r'<input[^>]*name="[^"]*_unit"', html)
        self.assertEqual(leftovers, [], 'остались текстовые поля единицы')
        leftovers_js = re.findall(r'<input[^>]*name="eq_\$\{eqId\}[^"]*_unit"', html)
        self.assertEqual(leftovers_js, [], 'осталось текстовое поле в JS-шаблоне')

    def test_both_sites_render_a_select_over_the_directory(self):
        html = self._get_form()
        server_side = re.search(
            r'<select name="eq_%d_line_0_unit".*?</select>' % self.eq_id,
            html, re.S)
        self.assertIsNotNone(server_side, 'нет серверного <select> единицы')
        js_side = re.search(
            r'<select name="eq_\$\{eqId\}_line_\$\{idx\}_unit".*?</select>',
            html, re.S)
        self.assertIsNotNone(js_side, 'нет <select> единицы в JS-шаблоне')
        for block in (server_side.group(0), js_side.group(0)):
            for code, *_ in UNIT_ROWS:
                self.assertIn('value="%s"' % code, block)

    def test_work_type_option_carries_the_code_not_free_text(self):
        html = self._get_form()
        # Автоподстановка присваивает select.value; там обязан быть код.
        self.assertIn('data-unit="m_soat"', html)
        self.assertNotIn('data-unit="м-соат"', html)

    def test_equipment_default_preselects_the_matching_code(self):
        html = self._get_form()
        block = re.search(r'<select name="eq_%d_line_0_unit".*?</select>' % self.eq_id,
                          html, re.S).group(0)
        self.assertRegex(block, r'<option value="ga"\s+selected>')

    def test_labels_follow_the_interface_language(self):
        block_ru = re.search(r'<select name="eq_%d_line_0_unit".*?</select>' % self.eq_id,
                             self._get_form(), re.S).group(0)
        self.assertIn('>час<', block_ru)
        self.assertIn('>моточас<', block_ru)
        self._set_lang('uz')
        block_uz = re.search(r'<select name="eq_%d_line_0_unit".*?</select>' % self.eq_id,
                             self._get_form(), re.S).group(0)
        self.assertIn('>соат<', block_uz)
        self.assertIn('>мотосоат<', block_uz)

    # ─── сохранение ─────────────────────────────────────────────────────

    def test_code_is_saved_as_code_and_canonical_russian_label(self):
        self._save('ga')
        recs = self._records()
        self.assertEqual(len(recs), 1)
        self.assertEqual(recs[0].unit_code, 'ga')
        # [REASON]: в `unit` идёт «га», а не «ga»: это поле читают выгрузки
        # Excel и отчёты, а деловое содержание выгрузок менять нельзя.
        self.assertEqual(recs[0].unit, 'га')

    def test_label_stays_russian_even_on_the_uzbek_interface(self):
        self._set_lang('uz')
        self._save('soat')
        recs = self._records()
        self.assertEqual(recs[0].unit_code, 'soat')
        self.assertEqual(recs[0].unit, 'час')

    def test_empty_unit_stays_legal(self):
        # 15 630 строк истории без единицы: у простоя работы нет.
        self._save('')
        recs = self._records()
        self.assertEqual(len(recs), 1)
        self.assertEqual(recs[0].unit, '')
        self.assertIsNone(recs[0].unit_code)

    def test_forged_code_is_rejected_and_nothing_is_written(self):
        resp = self._save('litr')
        self.assertEqual(resp.status_code, 302)
        self.assertEqual(self._records(), [])

    def test_unrecognised_history_survives_a_resave_untouched(self):
        with app.app_context():
            db.session.add(DailyRecord(work_date=self.work_date, equipment_id=self.eq_id,
                                       status='working', work_type='Шудгорлаш',
                                       unit=PARAGRAPH, unit_code=None,
                                       quantity=1, price=1, line_index=0))
            db.session.commit()
        html = self._get_form()
        # Значение показано как есть и помечено, а не подменено ближайшим.
        self.assertIn('не распознано', html)
        self.assertIn('value="%s%s' % (daily_units.RAW_PREFIX, PARAGRAPH[:20]), html)
        # Показан обрезанным, чтобы абзац не растянул строку формы.
        self.assertNotIn(PARAGRAPH[:60] + '</option>', html)

        self._save(daily_units.RAW_PREFIX + PARAGRAPH)
        recs = self._records()
        self.assertEqual(len(recs), 1)
        self.assertEqual(recs[0].unit, PARAGRAPH)
        self.assertIsNone(recs[0].unit_code)

    def test_existing_recognised_row_preselects_its_code(self):
        with app.app_context():
            db.session.add(DailyRecord(work_date=self.work_date, equipment_id=self.eq_id,
                                       status='working', work_type='Шудгорлаш',
                                       unit='Га', unit_code=None,
                                       quantity=1, price=1, line_index=0))
            db.session.commit()
        # unit_code пуст (строка создана до перехода), но «Га» приводится к ga.
        block = re.search(r'<select name="eq_%d_line_0_unit".*?</select>' % self.eq_id,
                          self._get_form(), re.S).group(0)
        self.assertRegex(block, r'<option value="ga"\s+selected>')
        self.assertNotIn('не распознано', block)

    def test_copy_previous_day_carries_the_code_along(self):
        with app.app_context():
            db.session.add(DailyRecord(work_date=self.work_date, equipment_id=self.eq_id,
                                       status='working', work_type='Шудгорлаш',
                                       unit='га', unit_code='ga',
                                       quantity=1, price=1, line_index=0))
            db.session.commit()
        nxt = date(2026, 8, 2)
        resp = self.client.post('/entry/copy_prev', data={
            'csrf_token': CSRF, 'work_date': nxt.isoformat(),
            'org_id': str(self.org_id)})
        self.assertEqual(resp.status_code, 302)
        with app.app_context():
            copied = DailyRecord.query.filter_by(work_date=nxt).one()
            self.assertEqual(copied.unit_code, 'ga')
            self.assertEqual(copied.unit, 'га')


if __name__ == '__main__':
    unittest.main()
