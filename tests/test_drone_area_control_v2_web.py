# -*- coding: utf-8 -*-
"""DRONE-AREA-CONTROL-V2-MEGA: экран, решения администратора, кнопка
«Обновить данные DJI» -- то, что существует только вместе с Flask.

Что держится здесь:

* прямой пункт меню «Контроль площади DJI» (RU/UZ), подсвеченный на экране;
* формула «DJI -> доказанное завышение -> принято» наверху и статус полноты;
* дерево Дрон -> День -> Вылет с кнопками «Развернуть всё / Свернуть всё»;
* A, B и C -- одинаковые ссылки DJI с местным временем UTC+5; отдельной
  загадочной кнопки «Открыть» больше нет;
* решение администратора меняет ЭФФЕКТИВНЫЕ итоги и разрез по дронам, не
  трогая ни одной колонки автоматического расчёта; история append-only;
  отмена -- новая строка; правка поверх чужой -- отказ;
* регрессионный кейс UAT (типа 714484527, синтетический): автомат оставил
  запись «требует проверки» по RAW -> администратор подтверждает полный
  фантом -> принято 0, исключено +RAW, запись среди подтверждённых;
* права: решение -- только администратор; обновление -- только с правом
  правки; CSRF на обоих POST;
* числа экрана и книги совпадают при том же периоде, времени и решениях;
* обновление: запрос возвращается сразу, второй запрос не создаёт второго
  прогона, команда запуска постоянна (аргументы формы в неё не попадают),
  секрет не доходит ни до журнала, ни до экрана, отказ дочернего процесса
  виден, мёртвый прогон виден как «Прервано» без записи из GET.

ВСЕ ДАННЫЕ СИНТЕТИЧЕСКИЕ. Нужен Flask: набор идёт локально и на сервере.
"""

import io
import json
import os
import re
import time
import unittest
from datetime import date, datetime, timedelta

from tests.harness import app, login, CSRF, TEST_DB_PATH
from tests.test_dji_area_control_web_001 import (Base as ControlBase, A, B, C,
                                                 PARTIAL_C, PENDING_C,
                                                 REVIEW_C, RULE_MISS, WINDOW)

from models import (db, DjiAreaCalculation, DroneFlight, User,
                    UserModulePermission, ROLE_OPERATOR, ROLE_VIEWER)

import drones
from dji_area import control_report as dji_control
from dji_area import control_store
from dji_area import decisions as dec
from dji_area import resolver as dji_resolver
from dji_area import store as dji_store
from drone_collector import runlock

# Синтетический аналог кейса UAT 714484527: плоский счётчик при наблюдённом
# распылении, резолвер записал corrected=0.0, учёт оставил REVIEW по RAW.
UAT_C, UAT_A, UAT_B = 900714, 900711, 900712
UAT_RAW = 60000.0


class Base(ControlBase):

    def seed(self):
        super(Base, self).seed()
        with app.app_context():
            obj = self.calc_object(
                UAT_C, dji_resolver.COUNTER_FLAT_RAW_OVERSTATED,
                dji_resolver.AGG_UNRESOLVED, raw_m2=UAT_RAW,
                corrected_m2=0.0, hardware_id=self.hw7(), minute=40,
                flags=['APPLICATION_WITH_FLAT_COUNTER'], delta_m2=0.0)
            obj.structural_candidate = True
            obj.scalar_source_check = True
            obj.candidate_base_flight_id = UAT_A
            obj.bridge_flight_ids_json = json.dumps([UAT_B])
            obj.v4_revision_id = 1
            obj.structural_rule_version = 'structural-retained-screen-frozen-1'
            db.session.add(obj)
            # A и B цепочки UAT лежат только в drone_flights (вне выборки
            # отчёта): их время берётся оттуда.
            for fid, minute in ((UAT_A, 30), (UAT_B, 35)):
                db.session.add(DroneFlight(
                    dji_flight_id=fid, drone_unit_id=self.unit7_id,
                    nickname_raw='SYNTHETIC-NICK-7',
                    started_at=datetime(2026, 6, 5, 3, minute),
                    area_ha=1.0, raw_json='{}'))
            db.session.commit()

    @staticmethod
    def hw7():
        from tests.test_dji_area_report_001 import HW7
        return HW7

    def make_user(self, username, role, has_drones=True, language='ru'):
        with app.app_context():
            user = User(username=username, role=role,
                        full_name='SYNTHETIC %s' % username, language=language)
            user.set_password('test-password')
            db.session.add(user)
            db.session.flush()
            if has_drones:
                db.session.add(UserModulePermission(
                    user_id=user.id, module_code='drones', has_access=True))
            db.session.commit()
            return user.id

    def calc_snapshot(self):
        cols = [c.name for c in DjiAreaCalculation.__table__.columns]
        return self.raw('SELECT %s FROM dji_area_calculations ORDER BY id'
                        % ', '.join(cols))

    def decide(self, flight_id, action, comment='Проверено визуально в DJI',
               confirm=True, override=False, expected=None, user_id=None,
               csrf=True, extra=None):
        client = self.client_as(user_id=user_id)
        data = {'action': action, 'comment': comment,
                'next': '/drones/area-control' + WINDOW}
        if csrf:
            data['csrf_token'] = CSRF
        if confirm:
            data['confirm'] = '1'
        if override:
            data['confirm_override'] = '1'
        if expected is not None:
            data['expected_decision_id'] = str(expected)
        data.update(extra or {})
        return client.post('/drones/area-control/flight/%d/decide'
                           % flight_id, data=data)

    def decisions(self):
        return self.raw('SELECT flight_id, chain_seq, decision_type, '
                        'supersedes_decision_id, auto_class, is_override, '
                        'effective_accepted_m2, effective_excluded_m2, '
                        'performed_by_name, comment FROM drone_area_decisions '
                        'ORDER BY id')

    def pure_total(self):
        """Итог чистого модуля на тех же строках и решениях -- эталон."""
        with app.app_context():
            filters = drones._drone_area_control_filters(
                _Args({'date_from': '2026-06-01', 'date_to': '2026-06-30'}))
            with app.test_request_context():
                report = drones._drone_area_control_report(filters)
        return report['total'], report['drones']


class _Args(dict):
    """Мини-заменитель request.args для прямого вызова построителя."""

    def get(self, key, default=None, type=None):  # noqa: A002
        value = dict.get(self, key, default)
        if type is not None and value is not None:
            try:
                return type(value)
            except (TypeError, ValueError):
                return default
        return value


# ─── 1. Меню, формула, дерево, ссылки ───────────────────────────────────────

class Screen(Base):

    def setUp(self):
        super(Screen, self).setUp()
        self.seed()

    def test_the_direct_menu_item_exists_and_is_active_here(self):
        client = self.client_as()
        works = client.get('/drones/works').get_data(as_text=True)
        self.assertIn('href="/drones/area-control"', works)
        self.assertIn('>Контроль площади DJI</a>', works)
        html = self.control_page()
        self.assertRegex(html, r'href="/drones/area-control"\s+class="vs-pill'
                               r' is-active"\s+aria-current="page"')

    def test_the_menu_item_is_uzbek_cyrillic_on_the_uzbek_page(self):
        html = self.control_page(language='uz')
        self.assertIn('>DJI майдони назорати</a>', html)
        self.assertNotIn('Контроль площади DJI', html)

    def test_the_formula_line_explains_the_result(self):
        html = self.control_page()
        # RAW 44.44 + UAT 6.00 = 50.44; исключено 22.34; принято 28.10.
        self.assertIn('Площадь по данным DJI', html)
        self.assertIn('Доказанное завышение', html)
        self.assertIn('Площадь, принятая программой', html)
        self.assertRegex(html, r'DJI: 50\.44 га</strong> − подтверждённо '
                               r'исключено: <strong>22\.34 га</strong> = '
                               r'принято: <strong>28\.10 га</strong>')
        # Спорная площадь сейчас ВНУТРИ принятого -- сказано словами.
        self.assertIn('сейчас ВХОДИТ в принятую по DJI RAW', html)
        self.assertIn('Требует проверки, га', html)
        self.assertIn('нужно решение человека', html)

    def test_the_tree_has_three_levels_and_expand_collapse_buttons(self):
        html = self.control_page()
        self.assertIn('data-tree-expand="all"', html)
        self.assertIn('>Развернуть всё<', html)
        self.assertIn('>Свернуть всё<', html)
        self.assertEqual(len(re.findall(r'<tr data-level="1"', html)), 2)
        self.assertEqual(len(re.findall(r'<tr class="vs-accord-l2" '
                                        r'data-level="2"', html)), 2)
        # Каждый день ссылается на свой дрон, каждый вылет -- на свой день.
        for node, parent in re.findall(
                r'data-level="2" data-node="([^"]+)" data-parent="([^"]+)"',
                html):
            self.assertTrue(node.startswith(parent + '-'), node)

    def test_a_b_c_are_all_links_with_local_times(self):
        html = self.control_page()
        for fid in (A, B, C):
            self.assertRegex(
                html, r'<a href="https://www\.djiag\.com/record/%d"[^>]*>%d'
                      r'</a>' % (fid, fid))
        # 03:00/03:05/03:10 UTC -> 08:00/08:05/08:10 UTC+5.
        self.assertRegex(html, r'>%d</a> <span class="vs-muted">08:00</span>'
                         % A)
        self.assertRegex(html, r'>%d</a> <span class="vs-muted">08:05</span>'
                         % B)
        self.assertRegex(html, r'>%d</a> <span class="vs-muted">08:10</span>'
                         % C)
        # A и B цепочки UAT -- вне выборки отчёта; время из drone_flights.
        self.assertRegex(html, r'>%d</a> <span class="vs-muted">08:30</span>'
                         % UAT_A)
        self.assertRegex(html, r'>%d</a> <span class="vs-muted">08:35</span>'
                         % UAT_B)
        # Серой «C 900203» и отдельной кнопки «Открыть» больше нет.
        self.assertNotIn('→ C %d\n' % C, html)
        self.assertNotIn('>Открыть</a>', html)

    def test_normal_flights_fold_into_the_rest_row_and_add_up(self):
        html = self.control_page()
        # A и B -- обычные записи дня машины 6: не поимённо, а в строке.
        self.assertIn('Остальные вылеты дня, не показанные поимённо: 2', html)
        html_all = self.control_page(WINDOW + '&flights=all')
        self.assertRegex(html_all, r'<td><a href="https://www\.djiag\.com/'
                                   r'record/%d"[^>]*>%d</a></td>' % (A, A))


# ─── 2. Решения администратора ─────────────────────────────────────────────

class Decisions(Base):

    def setUp(self):
        super(Decisions, self).setUp()
        self.seed()

    def test_uat_case_review_confirmed_as_full_phantom(self):
        before_calc = self.calc_snapshot()
        before = self.pure_total()[0]
        self.assertEqual(before['review_records'], 2)
        page = self.client_as().get('/drones/area-control/flight/%d' % UAT_C)
        self.assertEqual(page.status_code, 200)
        page_html = page.get_data(as_text=True)
        self.assertIn('value="%s"' % dec.CONFIRM_FULL_PHANTOM, page_html)

        response = self.decide(UAT_C, dec.CONFIRM_FULL_PHANTOM)
        self.assertEqual(response.status_code, 302)
        after, drones_after = self.pure_total()
        # Эффективно принято 0: исключено выросло ровно на RAW записи.
        self.assertAlmostEqual(after['excluded_m2'],
                               before['excluded_m2'] + UAT_RAW)
        self.assertAlmostEqual(after['after_m2'],
                               before['after_m2'] - UAT_RAW)
        self.assertEqual(after['excluded_records'],
                         before['excluded_records'] + 1)
        self.assertEqual(after['review_records'], 1)
        self.assertAlmostEqual(after['raw_m2'], before['raw_m2'])
        # Автоматический расчёт -- ни одной изменённой колонки.
        self.assertEqual(self.calc_snapshot(), before_calc)
        rows = self.decisions()
        self.assertEqual(len(rows), 1)
        flight, seq, kind, supersedes, auto_class, override, acc_m2, exc_m2, \
            by, comment = rows[0]
        self.assertEqual((flight, seq, kind, supersedes, auto_class),
                         (UAT_C, 1, dec.CONFIRM_FULL_PHANTOM, None, 'REVIEW'))
        self.assertEqual((acc_m2, exc_m2), (0.0, UAT_RAW))
        self.assertEqual(by, 'Test Admin')
        # Экран: среди подтверждённых корректировок, со своим статусом.
        confirmed = self.control_page(WINDOW + '&view=confirmed')
        self.assertIn('>%d<' % UAT_C, confirmed)
        self.assertIn('Подтверждённая корректировка (решение администратора)',
                      confirmed)
        review = self.control_page(WINDOW + '&view=review')
        self.assertNotIn('>%d<' % UAT_C, review)
        # Разрез по дронам: машина 7 потеряла RAW UAT из принятого.
        by_label = {d['machine_label']: d for d in drones_after}
        self.assertAlmostEqual(by_label['№ 7']['excluded_m2'],
                               75000.0 + UAT_RAW + 58400.0)

    def test_keep_raw_over_a_proven_correction_needs_the_override(self):
        before = self.pure_total()[0]
        refused = self.decide(PARTIAL_C, dec.KEEP_DJI_RAW, override=False)
        self.assertEqual(refused.status_code, 302)
        self.assertEqual(self.decisions(), [])
        self.decide(PARTIAL_C, dec.KEEP_DJI_RAW, override=True)
        after = self.pure_total()[0]
        self.assertAlmostEqual(after['excluded_m2'],
                               before['excluded_m2'] - 75000.0)
        self.assertEqual(after['override_records'], 1)
        rows = self.decisions()
        self.assertEqual(rows[0][2], dec.KEEP_DJI_RAW)
        self.assertEqual(rows[0][5], 1)
        html = self.control_page()
        self.assertIn('DJI RAW оставлен (решение администратора)', html)

    def test_accept_auto_result_closes_a_review_at_raw(self):
        before = self.pure_total()[0]
        self.decide(REVIEW_C, dec.ACCEPT_AUTO_RESULT)
        after = self.pure_total()[0]
        self.assertEqual(after['review_records'], before['review_records'] - 1)
        self.assertAlmostEqual(after['excluded_m2'], before['excluded_m2'])
        self.assertAlmostEqual(after['after_m2'], before['after_m2'])

    def test_needs_more_evidence_keeps_raw_and_the_dispute_open(self):
        before = self.pure_total()[0]
        self.decide(PENDING_C, dec.NEEDS_MORE_EVIDENCE)
        after = self.pure_total()[0]
        self.assertAlmostEqual(after['after_m2'], before['after_m2'])
        self.assertEqual(after['pending_records'],
                         before['pending_records'] - 1)
        self.assertEqual(after['review_records'],
                         before['review_records'] + 1)
        self.assertFalse(after['complete'])

    def test_revoke_is_a_new_row_and_restores_the_auto_result(self):
        before = self.pure_total()[0]
        self.decide(UAT_C, dec.CONFIRM_FULL_PHANTOM)
        first_id = self.raw('SELECT id FROM drone_area_decisions')[0][0]
        self.decide(UAT_C, dec.REVOKE, comment='Ошибся вылетом, отменяю',
                    expected=first_id)
        rows = self.decisions()
        self.assertEqual([r[1] for r in rows], [1, 2])
        self.assertEqual(rows[1][2], dec.REVOKE)
        self.assertEqual(rows[1][3], first_id)
        after = self.pure_total()[0]
        self.assertAlmostEqual(after['excluded_m2'], before['excluded_m2'])
        page = self.client_as().get('/drones/area-control/flight/%d'
                                    % UAT_C).get_data(as_text=True)
        self.assertIn('Решение отменено', page)
        self.assertIn('Полный фантом', page)

    def test_an_edit_over_someone_elses_edit_is_refused(self):
        self.decide(UAT_C, dec.CONFIRM_FULL_PHANTOM)
        # Форма, открытая ДО первого решения (expected пусто), устарела.
        self.decide(UAT_C, dec.KEEP_DJI_RAW, expected=None)
        self.assertEqual(len(self.decisions()), 1)

    def test_a_short_comment_or_a_missing_confirmation_is_refused(self):
        self.decide(UAT_C, dec.CONFIRM_FULL_PHANTOM, comment='ok')
        self.decide(UAT_C, dec.CONFIRM_FULL_PHANTOM, confirm=False)
        self.assertEqual(self.decisions(), [])

    def test_a_normal_record_cannot_be_decided(self):
        self.decide(A, dec.CONFIRM_FULL_PHANTOM)
        self.assertEqual(self.decisions(), [])

    def test_only_an_admin_decides(self):
        operator = self.make_user('area-operator', ROLE_OPERATOR)
        response = self.decide(UAT_C, dec.CONFIRM_FULL_PHANTOM,
                               user_id=operator)
        self.assertEqual(response.status_code, 403)
        self.assertEqual(self.decisions(), [])
        page = self.client_as(operator).get(
            '/drones/area-control/flight/%d' % UAT_C).get_data(as_text=True)
        self.assertNotIn('name="action"', page)

    def test_the_decision_post_needs_csrf(self):
        response = self.decide(UAT_C, dec.CONFIRM_FULL_PHANTOM, csrf=False)
        self.assertEqual(response.status_code, 400)
        self.assertEqual(self.decisions(), [])

    def test_decisions_are_append_only_under_the_migration(self):
        # Под миграцией UPDATE/DELETE отвергают триггеры (здесь база
        # создана create_all, поэтому проверяется писатель: он не пишет
        # ничего, кроме INSERT).
        import inspect
        source = inspect.getsource(control_store.record_decision)
        self.assertNotIn('UPDATE drone_area_decisions', source)
        self.assertNotIn('DELETE FROM drone_area_decisions', source)

    def test_the_next_parameter_cannot_leave_the_module(self):
        response = self.decide(UAT_C, dec.CONFIRM_FULL_PHANTOM,
                               extra={'next': 'https://evil.example/'})
        self.assertNotIn('evil.example', response.headers['Location'])


# ─── 3. Экран и книга: одни и те же числа ──────────────────────────────────

class Parity(Base):

    def setUp(self):
        super(Parity, self).setUp()
        self.seed()
        self.decide(UAT_C, dec.CONFIRM_FULL_PHANTOM)
        self.decide(PARTIAL_C, dec.KEEP_DJI_RAW, override=True)

    STRIP_RE = re.compile(r'<strong class="vs-kpi-strip-value[^"]*">'
                          r'([0-9 .\u00a0]+)</strong>')

    def html_figures(self, html):
        return [float(v.replace('\u00a0', '').replace(' ', ''))
                for v in self.STRIP_RE.findall(html)]

    def check_parity(self, query):
        html = self.control_page(query)
        _response, book = self.control_book(query)
        summary = {row[0].value: row for row in book['Сводка'].iter_rows()
                   if row[0].value}
        figures = self.html_figures(html)
        # raw, excluded, after, pending, review -- в этом порядке на экране.
        book_values = [summary['DJI RAW, га'][1].value,
                       summary['Подтверждённо исключено, га'][1].value,
                       summary['Площадь после подтверждённых корректировок, '
                               'га'][1].value,
                       summary['Ожидает доказательства / V4, га'][1].value,
                       summary['Требует проверки, га'][1].value]
        for screen, sheet in zip(figures[:5], book_values):
            self.assertAlmostEqual(screen, round(sheet, 2), places=2,
                                   msg=query)
        # По дронам и по дням: суммы книги равны итогу книги.
        drones_sheet = list(book['По_дронам'].iter_rows(min_row=2,
                                                        values_only=True))
        days_sheet = list(book['По_дням'].iter_rows(min_row=2,
                                                    values_only=True))
        self.assertAlmostEqual(sum(r[3] for r in drones_sheet),
                               book_values[1])
        self.assertAlmostEqual(sum(r[5] for r in days_sheet), book_values[2])
        return html, book

    def test_screen_and_book_agree_with_decisions(self):
        html, book = self.check_parity(WINDOW)
        # Реестр несёт решение: кто, когда, что, итоговый статус.
        sheet = book['Реестр']
        header = [c.value for c in sheet[1]]
        rows = {r[header.index('C Flight ID')]: r
                for r in sheet.iter_rows(min_row=2, values_only=True)}
        uat = rows[UAT_C]
        self.assertEqual(uat[header.index('Решение администратора')],
                         'Полный фантом')
        self.assertEqual(uat[header.index('Кто решил')], 'Test Admin')
        self.assertEqual(uat[header.index('Итоговый статус')],
                         'Подтверждённая корректировка (решение '
                         'администратора)')
        self.assertEqual(uat[header.index('Принято, га')], 0.0)
        self.assertEqual(uat[header.index('A Flight ID')], UAT_A)
        self.assertTrue(uat[header.index('A время (UTC+5)')].endswith(
            '08:30'))
        history = list(book['История_решений'].iter_rows(min_row=2,
                                                         values_only=True))
        self.assertEqual(len(history), 2)

    def test_screen_and_book_agree_under_a_minute_filter(self):
        # Период -- непрерывный интервал «с даты+времени по дату+время».
        # С 08:03: выпадает A (08:00), остаются B (08:05) и дальше.
        query = ('?date_from=2026-06-05&date_to=2026-06-05&time_from=08:03'
                 '&time_to=08:37')
        html, book = self.check_parity(query)
        self.assertNotIn('>%d</a></td>' % A, html)
        summary = {row[0].value: row[1].value
                   for row in book['Сводка'].iter_rows() if row[0].value}
        self.assertEqual(summary['Период: с'], '2026-06-05 08:03')
        self.assertEqual(summary['Период: по'], '2026-06-05 08:37')
        # 08:40 (UAT C) -- за границей «по 08:37».
        self.assertAlmostEqual(summary['DJI RAW, га'],
                               (6000 + 90000 + 80000 + 70000 + 50000
                                + 58400) / 1e4)


# ─── 4. Время в фильтре площади ────────────────────────────────────────────

class MinuteBoundaries(Base):

    def setUp(self):
        super(MinuteBoundaries, self).setUp()
        self.seed()

    def total_for(self, query):
        client = self.client_as()
        _r, book = self.control_book(query)
        summary = {row[0].value: row[2].value
                   for row in book['Сводка'].iter_rows() if row[0].value}
        return summary['DJI RAW, га']

    DAY_ONLY = '?date_from=2026-06-05&date_to=2026-06-05'

    def test_the_end_minute_is_inclusive_and_half_open(self):
        # C начинается ровно в 08:10:00; «по 08:10» его включает (граница
        # < 08:11), «по 08:09» -- нет (граница < 08:10).
        self.assertEqual(self.total_for(self.DAY_ONLY + '&time_to=08:10'), 3)
        self.assertEqual(self.total_for(self.DAY_ONLY + '&time_to=08:09'), 2)
        # Начало -- включительно: «с 08:10» оставляет C и всё позже.
        self.assertEqual(self.total_for(self.DAY_ONLY + '&time_from=08:10'),
                         6)
        self.assertEqual(self.total_for(self.DAY_ONLY + '&time_from=08:11'),
                         5)

    def test_a_cross_day_interval_spans_midnight(self):
        # С 5 июня 08:12 по 6 июня 00:00: C (08:10) уже нет, остальное есть.
        self.assertEqual(self.total_for(
            '?date_from=2026-06-05&date_to=2026-06-06&time_from=08:12'
            '&time_to=00:00'), 5)

    def test_default_times_equal_no_time_at_all(self):
        self.assertEqual(self.total_for(WINDOW),
                         self.total_for(WINDOW + '&time_from=00:00'
                                        '&time_to=23:59'))

    def test_an_inverted_range_is_empty_and_said_in_words(self):
        html = self.control_page('?date_from=2026-06-05&date_to=2026-06-05'
                                 '&time_from=10:00&time_to=09:00')
        self.assertIn('Начало периода позже его конца', html)
        self.assertIn('Нет данных', html)

    def test_an_invalid_time_falls_back_and_warns(self):
        html = self.control_page(WINDOW + '&time_from=25:99')
        self.assertIn('Время «с» указано неверно', html)
        self.assertIn('50.44', html)


# ─── 5. «Обновить данные DJI» ──────────────────────────────────────────────

class FakePopen(object):
    def __init__(self):
        self.calls = []

    def __call__(self, command, **kwargs):
        self.calls.append((list(command), kwargs))
        return self


class Refresh(Base):

    def setUp(self):
        super(Refresh, self).setUp()
        self.seed()
        self._saved = {k: app.config.get(k) for k in
                       ('DJI_REFRESH_LAUNCHER', 'DJI_REFRESH_TASK_NAME',
                        'DJI_COLLECTOR_PYTHON')}
        app.config['DJI_REFRESH_LAUNCHER'] = 'subprocess'
        app.config['DJI_COLLECTOR_PYTHON'] = 'C:/collector/python.exe'
        self.popen = FakePopen()
        drones._dji_refresh_popen = self.popen
        self.addCleanup(self._restore)
        self.lock_path = runlock.cycle_lock_path(TEST_DB_PATH)

    def _restore(self):
        for key, value in self._saved.items():
            app.config[key] = value
        drones._dji_refresh_popen = None
        drones._dji_refresh_run = None

    def post(self, user_id=None, csrf=True, extra=None):
        client = self.client_as(user_id=user_id)
        data = {'next': '/drones/area-control'}
        if csrf:
            data['csrf_token'] = CSRF
        data.update(extra or {})
        return client.post('/drones/dji-refresh', data=data)

    def runs(self):
        return self.raw('SELECT id, trigger_kind, status, active_slot, '
                        'requested_by_name, message FROM '
                        'drone_area_cycle_runs ORDER BY id')

    def store(self):
        con = dji_store.connect(TEST_DB_PATH)
        self.addCleanup(con.close)
        return con

    def test_the_request_returns_at_once_and_queues_one_run(self):
        started = time.monotonic()
        response = self.post(extra={'date_from': '--evil', 'args': '; rm -rf',
                                    'task': 'other'})
        self.assertLess(time.monotonic() - started, 5.0)
        self.assertEqual(response.status_code, 302)
        runs = self.runs()
        self.assertEqual(len(runs), 1)
        self.assertEqual(runs[0][1:4], ('MANUAL', 'QUEUED', 1))
        self.assertEqual(len(self.popen.calls), 1)
        command, kwargs = self.popen.calls[0]
        # Команда постоянна: ни одного значения из формы.
        self.assertIn('--run-queued', command)
        self.assertIn(os.path.abspath(TEST_DB_PATH), command)
        self.assertEqual(command[command.index('--collector-python') + 1],
                         'C:/collector/python.exe')
        for planted in ('--evil', 'rm -rf', 'other'):
            self.assertNotIn(planted, ' '.join(command))
        self.assertTrue(command[1].endswith(os.path.join(
            'tools', 'dji_area_daily.py')))

    def test_a_second_click_attaches_to_the_running_run(self):
        self.post()
        response = self.post()
        self.assertEqual(response.status_code, 302)
        self.assertEqual(len(self.runs()), 1)
        self.assertEqual(len(self.popen.calls), 1)
        html = self.control_page()
        self.assertIn('Ожидает запуска', html)
        self.assertIn('Сбор уже идёт', html)

    def test_status_transitions_and_the_panel_after_success(self):
        self.post()
        con = self.store()
        run = control_store.claim_queued(con)
        # RUNNING без блокировки -- мёртвый процесс: показывается
        # «прервано», активного прогона нет.
        status = self.client_as().get('/drones/dji-refresh/status').get_json()
        self.assertIsNone(status['active'])
        self.assertEqual(status['last']['status'], 'INTERRUPTED')
        lock = runlock.RunLock(self.lock_path, 'test-runner')
        self.assertTrue(lock.acquire())
        try:
            status = self.client_as().get(
                '/drones/dji-refresh/status').get_json()
            self.assertEqual(status['active']['status'], 'RUNNING')
            control_store.set_step(con, run['id'], control_store.STEP_SOURCES)
            html = self.control_page()
            self.assertIn('получение доказательств V4', html)
            control_store.finish(
                con, run['id'], control_store.STATUS_SUCCESS, exit_code=0,
                result={'outcome': 'SUCCESS',
                        'flights': {'seen': 12, 'new': 3, 'duplicates': 9,
                                    'unresolved': 0, 'errors': 0},
                        'manifest': {'candidates': 2, 'controls': 4},
                        'evidence_misses': {'candidates': [],
                                            'controls': []},
                        'recalc': {'flights_in_period': 120}})
        finally:
            lock.release()
        status = self.client_as().get('/drones/dji-refresh/status').get_json()
        self.assertIsNone(status['active'])
        self.assertEqual(status['last_success']['status'], 'SUCCESS')
        html = self.control_page()
        self.assertIn('Последнее успешное обновление', html)
        self.assertIn('вылетов получено: 12', html)
        self.assertIn('Test Admin', html)
        # Слот освобождён: следующий запуск возможен.
        self.post()
        self.assertEqual(len(self.runs()), 2)

    def test_a_dead_running_run_shows_interrupted_without_a_get_write(self):
        self.post()
        con = self.store()
        control_store.claim_queued(con)
        html = self.control_page()
        self.assertIn('Прервано', html)
        self.assertEqual(self.runs()[0][2], 'RUNNING')   # GET не писал
        self.post()
        runs = self.runs()
        self.assertEqual(runs[0][2], 'INTERRUPTED')      # закрыл POST
        self.assertEqual(runs[1][2], 'QUEUED')

    def test_a_child_failure_surfaces_and_secrets_never_leak(self):
        secret = 'SYNTHETIC-SECRET-VALUE-123456'
        os.environ['DRONE_API_TOKEN'] = secret
        self.addCleanup(os.environ.pop, 'DRONE_API_TOKEN', None)
        self.post()
        con = self.store()
        run = control_store.claim_queued(con)
        control_store.finish(con, run['id'], control_store.STATUS_FAILED,
                             exit_code=3, failed_step='SOURCES',
                             message='STOP: step SOURCES failed; body had '
                                     'token=%s' % secret)
        self.assertNotIn(secret, json.dumps(self.runs(), default=str))
        html = self.control_page()
        self.assertIn('Ошибка', html)
        self.assertIn('получение доказательств V4', html)
        self.assertNotIn(secret, html)
        status = self.client_as().get('/drones/dji-refresh/status')
        self.assertNotIn(secret, status.get_data(as_text=True))

    def test_a_launch_failure_is_recorded_and_frees_the_slot(self):
        def broken(command, **kwargs):
            raise OSError('SYNTHETIC cannot start')
        drones._dji_refresh_popen = broken
        self.post()
        runs = self.runs()
        self.assertEqual(runs[0][2:4], ('LAUNCH_FAILED', None))
        self.assertIn('Запуск не состоялся', self.control_page())

    def test_the_scheduler_launcher_runs_only_the_named_task(self):
        calls = []

        class Done(object):
            returncode = 0

        def fake_run(command, **kwargs):
            calls.append(list(command))
            return Done()
        app.config['DJI_REFRESH_LAUNCHER'] = 'schtasks'
        app.config['DJI_REFRESH_TASK_NAME'] = 'DjiAreaRefresh'
        drones._dji_refresh_run = fake_run
        self.post(extra={'task': 'Evil Task'})
        self.assertEqual(calls, [['schtasks', '/Run', '/TN',
                                  'DjiAreaRefresh']])

    def test_not_configured_means_no_run_and_an_honest_message(self):
        app.config['DJI_REFRESH_LAUNCHER'] = ''
        response = self.post()
        self.assertEqual(response.status_code, 302)
        self.assertEqual(self.runs(), [])
        html = self.control_page()
        self.assertIn('Ручное обновление на этом сервере не настроено', html)

    def test_rights_and_csrf(self):
        viewer = self.make_user('area-viewer', ROLE_VIEWER)
        self.assertEqual(self.post(user_id=viewer).status_code, 403)
        self.assertEqual(self.post(csrf=False).status_code, 400)
        self.assertEqual(self.runs(), [])
        operator = self.make_user('area-editor', ROLE_OPERATOR)
        self.assertEqual(self.post(user_id=operator).status_code, 302)
        self.assertEqual(len(self.runs()), 1)
        html = self.control_page(user_id=viewer)
        self.assertNotIn('action="/drones/dji-refresh"', html)

    def test_the_panel_shows_the_last_flight_intake(self):
        from models import DroneSyncLog
        html = self.control_page()
        self.assertIn('Последний приём вылетов: не было', html)
        with app.app_context():
            db.session.add(DroneSyncLog(
                kind='incremental', status='ok', records_seen=3,
                records_new=3, records_duplicate=0, records_unresolved=0,
                records_error=0, started_at=datetime(2026, 9, 23, 1, 0),
                finished_at=datetime(2026, 9, 23, 1, 2)))
            db.session.commit()
        html = self.control_page()
        # 01:02 UTC -> 06:02 UTC+5.
        self.assertIn('Последний приём вылетов: 23.09.2026 06:02', html)

    def test_the_uzbek_panel_is_cyrillic(self):
        html = self.control_page(language='uz')
        self.assertIn('DJI маълумотлари', html)
        self.assertIn('Сўнгги муваффақиятли янгилаш', html)
        self.assertIn('DJI маълумотларини янгилаш', html)
        self.assertNotIn('Обновить данные DJI', html)


# ─── 6. Узбекский в новых шаблонах -- кириллицей ───────────────────────────

class Bilingual(unittest.TestCase):

    ALLOWED = ('Excel', 'DJI', 'RAW', 'UTC', 'V4', 'Bridge',
               'migrate_drone_area_control_v2_001.py')
    TEMPLATES = ('area_control.html', 'area_decision.html',
                 '_dji_refresh.html')

    @staticmethod
    def uz_halves(src):
        return re.findall(r"if is_ru else '((?:[^'\\]|\\.)*)'", src) + \
            re.findall(r'if is_ru else "((?:[^"\\]|\\.)*)"', src)

    def latin(self, text):
        for word in self.ALLOWED:
            text = text.replace(word, '')
        return re.findall(r'[A-Za-z]{2,}', text)

    def test_every_uzbek_half_is_cyrillic(self):
        root = os.path.join(os.path.dirname(os.path.dirname(
            os.path.abspath(__file__))), 'templates', 'drones')
        total = 0
        for name in self.TEMPLATES:
            with io.open(os.path.join(root, name), encoding='utf-8') as fh:
                halves = self.uz_halves(fh.read())
            total += len(halves)
            for half in halves:
                self.assertEqual(self.latin(half), [], '%s: %s' % (name, half))
        self.assertGreater(total, 60)

    def test_the_scan_fires_on_a_planted_latin_word(self):
        self.assertTrue(self.latin("Qaror yozildi"))

    def test_decision_words_are_cyrillic(self):
        pairs = (list(dec.DECISION_LABELS.values())
                 + list(dec.DECISION_SHORT.values())
                 + list(dec.DECISION_HELP.values())
                 + list(dec.STATE_LABELS.values())
                 + list(dec.ERRORS.values())
                 + [dec.STALE_NOTE, dec.LAPSED_NOTE]
                 + list(control_store.STATUS_LABELS.values())
                 + list(control_store.STEP_LABELS.values())
                 + list(control_store.TRIGGER_LABELS.values()))
        for _ru, uz in pairs:
            self.assertEqual(self.latin(uz), [], uz)


if __name__ == '__main__':
    unittest.main()
