# -*- coding: utf-8 -*-
"""DRONE-FIELD-PASSPORT-001: экраны «Поля DJI», карточка поля, паспорт вылета.

Сценарий -- тот же, что у ядра (`tests/test_drone_field_passport_core.py`):
журнал вылетов пишет ORM, слой доказательств DJI -- прямой SQL того же
посева. Здесь проверяется то, что видит человек:

  * итог поля на странице равен `accepted.summarize` по тем же вылетам, и
    подмена одного вылета эту сверку ломает (отрицательный контроль);
  * предположительные и кандидаты -- отдельной таблицей, не в гектарах;
  * общая граница двух записей не удваивает вылет;
  * паспорт открывается без расчёта: RAW виден, принятая -- «не рассчитано»;
  * августовский паспорт показывает августовскую границу, а не последнюю;
  * ни координат, ни тел источников, ни путей, ни подписанных ссылок, ни
    токенов в HTML нет, внешний текст экранирован;
  * пагинация, RU/UZ, права, 404, число запросов не растёт с вылетами.

ВСЕ ДАННЫЕ СИНТЕТИЧЕСКИЕ.
"""

import re
import sqlite3
import unittest
from datetime import datetime

from tests.harness import app, login, CSRF, TEST_DB_PATH
from tests.test_dji_area_report_001 import Base

from models import (db, DroneFlight, User, UserModulePermission,
                    ROLE_OPERATOR)

from dji_area import accepted as acc
from dji_area import control_store
from dji_area import field as fld
from dji_area import resolver as rs
from dji_area import store as dji_store
import drones
import tests.test_drone_field_passport_core as core

ALL = '?date_from=&date_to='

FORBIDDEN = (
    core.SECRET_TOKEN, core.SIGNED_URL, 'X-Amz-Signature', core.STORAGE_PATH,
    'dji_sources', 'SYNTHETIC-POLYGON-BODY', 'points_json', 'body_blob',
    'body_text', 'body_path', 'request_context', 'raw_json', 'contentMd5',
    'upperRight', 'signedUrl',
) + core.COORDINATES
# Любая «координата» района работ: широта 37..42 или долгота 63..66 с
# четырьмя и более знаками после точки.
COORD_RE = re.compile(r'(?<![\d.])(?:3[7-9]|4[0-2]|6[3-6])\.\d{4,}')

STAT_RE = re.compile(r'<div class="vs-stat-label">(.*?)</div>\s*'
                     r'<div class="vs-stat-value is-num">(.*?)</div>', re.S)
NUM_RE = re.compile(r'-?\d[\d\s  ]*\.\d+')


def number(text):
    match = NUM_RE.search(re.sub(r'<[^>]+>', ' ', text))
    if not match:
        return None
    return re.sub(r'[\s  ]', '', match.group(0))


def section(html, title):
    """HTML от заголовка карточки до следующего заголовка карточки."""
    marker = '<span class="vs-card-title">%s</span>' % title
    if marker not in html:
        return None
    return html.split(marker, 1)[1].split('<span class="vs-card-title">',
                                          1)[0]


def passports(fragment):
    return {int(i) for i in re.findall(r'/drones/flights/(\d+)/passport',
                                       fragment or '')}


def row_of(html, header):
    match = re.search(r'<th>%s</th><td>(.*?)</td></tr>' % re.escape(header),
                      html, re.S)
    return match.group(1) if match else None


class Seeded(Base):

    def setUp(self):
        super(Seeded, self).setUp()
        with app.app_context():
            for fid, area in core.RAW.items():
                db.session.add(self.journal(fid, core.START[fid], area))
            db.session.commit()
        con = sqlite3.connect(TEST_DB_PATH)
        try:
            self.seed = core.seed_scenario(con, with_flights=False)
        finally:
            con.close()

    def journal(self, fid, started, area_ha):
        return DroneFlight(
            dji_flight_id=fid, drone_unit_id=self.unit6_id,
            nickname_raw='SYNTHETIC-%d' % fid, started_at=started,
            work_seconds=600, area_ha=area_ha, spray_liters=1.0,
            usage_type=0, raw_json='{}')

    def get(self, path, language='ru', status=200):
        response = self.client_as(language=language).get(path)
        self.assertEqual(response.status_code, status, path)
        return response.get_data(as_text=True)

    def card(self, land, language='ru', query=ALL):
        return self.get('/drones/fields/%s%s' % (land, query), language)

    def passport(self, fid, language='ru'):
        return self.get('/drones/flights/%d/passport' % fid, language)

    def provider(self, flight_ids):
        """Итог провайдера по набору вылетов -- мимо страницы."""
        con = dji_store.connect(TEST_DB_PATH, read_only=True)
        try:
            raw = {fid: core.RAW[fid] * 10000.0 for fid in flight_ids}
            items = control_store.accepted_for(con, raw)
        finally:
            con.close()
        return acc.summarize(items.values())


# ─── Итог поля и членство ────────────────────────────────────────────────────

class FieldTotals(Seeded):

    def stats(self, html):
        out = {}
        for label, value in STAT_RE.findall(html):
            out[label.strip()] = number(value)
        return out

    def test_the_field_total_is_the_provider_total_of_its_confirmed_flights(self):
        html = self.card(core.LAND_A)
        confirmed = section(html, 'Подтверждённые работы')
        self.assertEqual(passports(confirmed), core.A_CONFIRMED)
        stats = self.stats(html)
        want = self.provider(core.A_CONFIRMED)
        self.assertEqual(stats['DJI RAW, га'], '%.2f' % (want['raw_m2'] / 1e4))
        self.assertEqual(stats['Принято, га'],
                         '%.2f' % (want['accepted_m2'] / 1e4))
        self.assertEqual(stats['Исключено, га'],
                         '%.2f' % (want['excluded_m2'] / 1e4))
        self.assertEqual(stats['Не рассчитано, DJI RAW га'], '0.25')
        self.assertEqual(stats['Открыто, DJI RAW га'], '0.50')
        # Числа сценария, записанные заранее, а не только «как у провайдера».
        self.assertEqual((stats['DJI RAW, га'], stats['Принято, га'],
                          stats['Исключено, га']), ('3.75', '1.50', '2.00'))
        # Принятая -- по рассчитанным, с пометкой «частично».
        self.assertIn('частично', html)

    def test_negative_control_one_swapped_flight_changes_the_total(self):
        html = self.card(core.LAND_A)
        stats = self.stats(html)
        swapped = (core.A_CONFIRMED - {core.F_A_EXACT}) | {core.F_A_PROBABLE}
        other = self.provider(swapped)
        self.assertNotEqual(stats['DJI RAW, га'],
                            '%.2f' % (other['raw_m2'] / 1e4))
        self.assertNotEqual(stats['Принято, га'],
                            '%.2f' % (other['accepted_m2'] / 1e4))

    def test_probable_and_candidates_are_shown_apart_and_not_counted(self):
        html = self.card(core.LAND_A)
        provisional = section(html,
                              'Предположительные / требуют проверки')
        self.assertEqual(passports(provisional),
                         {core.F_A_PROBABLE, core.F_A_CANDIDATE})
        confirmed = section(html, 'Подтверждённые работы')
        self.assertNotIn(core.F_A_PROBABLE, passports(confirmed))
        self.assertNotIn(core.F_A_CANDIDATE, passports(confirmed))
        # Ни замещённая привязка, ни неоднозначная в записи A не числятся.
        everything = passports(html)
        self.assertNotIn(core.F_A_SUPERSEDED, everything)
        self.assertNotIn(core.F_A_REACTIVATED, everything)

    def test_shared_geometry_is_diagnostic_and_not_doubled(self):
        html_b = self.card(core.LAND_B)
        self.assertEqual(passports(section(html_b, 'Подтверждённые работы')),
                         {core.F_B_EXACT})
        shared = section(html_b,
                         'Вылеты на тех же границах, учтённые в другой записи')
        self.assertIn(core.F_A_EXACT, passports(shared))
        self.assertEqual(self.stats(html_b)['DJI RAW, га'], '0.70')
        # Сумма двух записей -- сумма их подтверждённых, без повторов.
        html_a = self.card(core.LAND_A)
        a = passports(section(html_a, 'Подтверждённые работы'))
        b = passports(section(html_b, 'Подтверждённые работы'))
        self.assertFalse(a & b)
        both = self.provider(a | b)
        self.assertAlmostEqual(both['raw_m2'],
                               sum(core.RAW[f] for f in a | b) * 1e4)

    def test_boundary_versions_keep_their_flights(self):
        html = self.card(core.LAND_C)
        versions = section(html, 'Версии границы')
        self.assertIn(core.MD5_V1[:12], versions)
        self.assertIn(core.MD5_V2[:12], versions)
        html_a = self.card(core.LAND_A)
        self.assertIn('историческая граница не сохранена',
                      section(html_a, 'Версии границы'))


# ─── Паспорт вылета ──────────────────────────────────────────────────────────

class Passport(Seeded):

    def test_exact_flight_passport_has_all_five_sections(self):
        html = self.passport(core.F_A_EXACT)
        for title in ('Площадь', 'Почему', 'Поле', 'Решения администратора',
                      'Происхождение'):
            self.assertIsNotNone(section(html, title), title)
        self.assertIn('Подтверждено, граница сохранена', html)
        self.assertIn(core.LAND_A, html)
        self.assertEqual(number(row_of(html, 'Принято, га')), '1.0000')
        # Та же граница у B -- названа, но вылет учтён в одной записи.
        self.assertIn('Та же граница у других записей DJI', html)

    def test_identified_flight_says_the_historical_boundary_is_missing(self):
        html = self.passport(core.F_A_IDENT)
        self.assertIn('историческая граница не сохранена', html)
        self.assertNotIn('сохранена и сверена', html)
        self.assertIn(acc.STATUS_LABELS[acc.ST_CORRECTED][0],
                      row_of(html, 'Статус площади'))
        self.assertEqual(number(row_of(html, 'Исключено, га')), '2.0000')

    def test_a_passport_opens_without_a_calculation(self):
        html = self.passport(core.F_A_NOCALC)
        self.assertEqual(number(row_of(html, 'DJI RAW, га')), '0.2500')
        accepted = row_of(html, 'Принято, га')
        self.assertIn('не рассчитано', accepted)
        # Отрицательная сторона: RAW не подставлен вместо принятой.
        self.assertIsNone(number(accepted))
        self.assertIn('Area Control этот вылет не рассчитывал', html)

    def test_a_passport_opens_without_calculation_and_attribution(self):
        html = self.passport(core.F_NO_ATTR)
        self.assertIn('Привязка не рассчитана', html)
        self.assertIn('не рассчитано', row_of(html, 'Принято, га'))

    def test_review_semantics_come_from_the_provider(self):
        html = self.passport(core.F_A_REVIEW)
        status = row_of(html, 'Статус площади')
        self.assertIn(acc.STATUS_LABELS[acc.ST_NEEDS_DECISION][0], status)
        self.assertIn('открытая запись', status)
        # Открытая запись принята по RAW, пока человек не решил иначе.
        self.assertEqual(number(row_of(html, 'Принято, га')), '0.5000')

    def test_no_card_and_no_key_read_differently(self):
        no_card = self.passport(core.F_NO_CARD)
        no_key = self.passport(core.F_NO_KEY)
        manual = self.passport(core.F_NO_KEY_MANUAL)
        self.assertIn('карточка вылета не собрана', no_card)
        self.assertNotIn('в карточке нет ключа поля', no_card)
        self.assertIn('в карточке нет ключа поля', no_key)
        self.assertNotIn('карточка вылета не собрана', no_key)
        self.assertIn('ручной режим', manual)

    def test_the_august_passport_keeps_the_august_boundary(self):
        """§15: последняя ревизия записи C -- V2, вылет августа летал по V1."""
        aug = self.passport(core.F_C_AUG)
        self.assertIn(core.MD5_V1, aug)
        self.assertNotIn(core.MD5_V2, aug)
        # Отрицательный контроль: сентябрьский паспорт на той же записи
        # показывает V2 -- проверка различает версии, а не печатает одну.
        sep = self.passport(core.F_C_SEP)
        self.assertIn(core.MD5_V2, sep)
        self.assertNotIn(core.MD5_V1, sep)

    def test_provenance_is_hashes_and_times_not_bodies(self):
        html = self.passport(core.F_A_EXACT)
        provenance = section(html, 'Происхождение')
        # Положительный контроль: ревизия источника дошла до страницы...
        self.assertIn('f' * 16, provenance)
        self.assertIn('SYNTHETIC-PARSER-1', provenance)
        self.assertIn(dji_store.FIELD_RESOLVER_VERSION, provenance)
        self.assertIn(dji_store.AREA_ALGORITHM_VERSION, provenance)
        # ...а её тело, путь и контекст запроса -- нет.
        for marker in FORBIDDEN:
            self.assertNotIn(marker, html, marker)

    def test_decision_page_and_list_link_to_the_passport(self):
        decision = self.get('/drones/area-control/flight/%d' % core.F_A_EXACT)
        self.assertIn('/drones/flights/%d/passport' % core.F_A_EXACT,
                      decision)
        journal = self.get('/drones/?date_from=2026-09-01&date_to=2026-09-30')
        self.assertIn('/drones/flights/%d/passport' % core.F_A_EXACT, journal)
        reports = self.get('/drones/reports')
        self.assertIn('/drones/fields', reports)


# ─── Безопасность вывода ─────────────────────────────────────────────────────

class Privacy(Seeded):

    def pages(self):
        urls = ['/drones/fields' + ALL, '/drones/fields?q=SYNTHETIC']
        urls += ['/drones/fields/%s%s' % (land, ALL)
                 for land in (core.LAND_A, core.LAND_B, core.LAND_C,
                              core.LAND_D)]
        urls += ['/drones/flights/%d/passport' % fid for fid in core.RAW]
        return urls

    def test_no_coordinates_bodies_paths_urls_or_tokens(self):
        for language in ('ru', 'uz'):
            client = self.client_as(language=language)
            for url in self.pages():
                response = client.get(url)
                self.assertEqual(response.status_code, 200, url)
                html = response.get_data(as_text=True)
                for marker in FORBIDDEN:
                    self.assertNotIn(marker, html, (url, marker))
                self.assertEqual(COORD_RE.findall(html), [], url)

    def test_the_coordinate_check_can_see_a_coordinate(self):
        """Отрицательный контроль: детектор ловит координату посева."""
        con = sqlite3.connect(TEST_DB_PATH)
        try:
            raw = con.execute('SELECT raw_json FROM dji_land_revisions '
                              'LIMIT 1').fetchone()[0]
        finally:
            con.close()
        self.assertTrue(COORD_RE.findall(raw))

    def test_external_text_is_escaped(self):
        con = sqlite3.connect(TEST_DB_PATH)
        try:
            snap = con.execute('SELECT MAX(id) FROM dji_land_snapshots'
                               ).fetchone()[0]
            core.Seed(con).revision(
                core.LAND_D, core.MD5_D, '<script>alert("SYNTHETIC")</script>',
                snap, snap, serial='<b>SER-D</b>')
            con.commit()
        finally:
            con.close()
        for url in ('/drones/fields?q=alert', '/drones/fields/%s' % core.LAND_D):
            html = self.get(url)
            self.assertNotIn('<script>alert(', html, url)
            self.assertNotIn('<b>SER-D</b>', html, url)
            self.assertIn('&lt;script&gt;alert(', html, url)


# ─── Список, язык, права, 404, запросы ───────────────────────────────────────

class Listing(Seeded):

    def rows(self, html):
        body = section(html, 'Записи полей DJI')
        return re.findall(r'href="/drones/fields/([0-9a-f-]{36})', body or '')

    def test_list_counts_and_search(self):
        html = self.get('/drones/fields' + ALL)
        self.assertEqual(set(self.rows(html)),
                         {core.LAND_A, core.LAND_B, core.LAND_C, core.LAND_D})
        found = self.get('/drones/fields?q=SER-B')
        self.assertEqual(self.rows(found), [core.LAND_B])
        aug = self.get('/drones/fields?date_from=2026-08-01&date_to=2026-08-31'
                       '&flights=1')
        self.assertEqual(self.rows(aug), [core.LAND_C])

    def test_pagination(self):
        con = sqlite3.connect(TEST_DB_PATH)
        try:
            seed = core.Seed(con)
            snap = con.execute('SELECT MIN(id) FROM dji_land_snapshots'
                               ).fetchone()[0]
            for n in range(60):
                seed.revision('bbbbbbbb-0000-4000-8000-%012d' % n,
                              '%032x' % (0xabc000 + n), 'SYNTHETIC P%02d' % n,
                              snap, snap)
            con.commit()
        finally:
            con.close()
        first = self.get('/drones/fields' + ALL)
        second = self.get('/drones/fields?date_from=&date_to=&page=2')
        self.assertEqual(len(self.rows(first)), drones.DRONE_FIELDS_PAGE_SIZE)
        self.assertEqual(len(self.rows(second)),
                         64 - drones.DRONE_FIELDS_PAGE_SIZE)
        self.assertFalse(set(self.rows(first)) & set(self.rows(second)))
        self.assertIn('page=2', first)
        clamped = self.get('/drones/fields?date_from=&date_to=&page=99')
        self.assertEqual(self.rows(clamped), self.rows(second))

    def test_both_languages(self):
        pairs = (('/drones/fields' + ALL, 'Поля DJI', 'DJI далалари'),
                 ('/drones/fields/%s%s' % (core.LAND_A, ALL),
                  'Подтверждённые работы', 'Тасдиқланган ишлар'),
                 ('/drones/flights/%d/passport' % core.F_A_IDENT,
                  'Паспорт вылета', 'Парвоз паспорти'))
        for url, ru_text, uz_text in pairs:
            ru = self.get(url, 'ru')
            uz = self.get(url, 'uz')
            self.assertIn(ru_text, ru, url)
            self.assertIn(uz_text, uz, url)
            self.assertNotIn(ru_text, uz, url)
        self.assertIn('тарихий чегара сақланмаган', self.passport(
            core.F_A_IDENT, 'uz'))


class Access(Seeded):

    def make_operator(self, username, has_drones):
        with app.app_context():
            user = User(username=username, role=ROLE_OPERATOR,
                        full_name='SYNTHETIC operator', language='ru')
            user.set_password('test-password')
            db.session.add(user)
            db.session.flush()
            if has_drones:
                db.session.add(UserModulePermission(
                    user_id=user.id, module_code='drones', has_access=True))
            db.session.commit()
            return user.id

    URLS = ('/drones/fields', '/drones/fields/' + core.LAND_A,
            '/drones/flights/%d/passport' % core.F_A_EXACT)

    def test_the_drones_permission_decides(self):
        allowed = app.test_client()
        login(allowed, self.make_operator('fields-yes', True))
        denied = app.test_client()
        login(denied, self.make_operator('fields-no', False))
        anonymous = app.test_client()
        for url in self.URLS:
            self.assertEqual(allowed.get(url).status_code, 200, url)
            self.assertEqual(denied.get(url).status_code, 403, url)
            self.assertIn(anonymous.get(url).status_code, (302, 401, 403), url)

    def test_unknown_or_malformed_ids_are_404(self):
        client = self.client_as()
        for url in ('/drones/fields/aaaaaaaa-0000-4000-8000-000000000099',
                    '/drones/fields/' + 'a' * 41,
                    '/drones/fields/bad%24uuid',
                    '/drones/flights/123/passport',
                    '/drones/flights/abc/passport'):
            self.assertEqual(client.get(url).status_code, 404, url)

    def test_pages_are_get_only(self):
        client = self.client_as()
        for url in self.URLS:
            # С действующим CSRF-токеном: отказ -- от маршрута, а не от
            # защиты форм.
            self.assertEqual(client.post(url, data={'csrf_token': CSRF})
                             .status_code, 405, url)


LAND_E = 'aaaaaaaa-0000-4000-8000-000000000005'
MD5_E = '6' * 32


class QueryCount(Seeded):
    """Карточки двух записей одного устройства: 1 вылет и 150 вылетов.

    Обе записи -- со своей границей и без общих держателей, так что разница
    между ними -- только число вылетов. Число запросов обязано совпасть.
    """

    def setUp(self):
        super(QueryCount, self).setUp()
        plan = [(969999, LAND_E, MD5_E)]
        plan += [(fid, core.LAND_D, core.MD5_D)
                 for fid in range(970000, 970150)]
        with app.app_context():
            for fid, _land, _md5 in plan:
                db.session.add(self.journal(fid, core.SEP, 1.0))
            db.session.commit()
        con = sqlite3.connect(TEST_DB_PATH)
        try:
            seed = core.Seed(con)
            snap = con.execute('SELECT MAX(id) FROM dji_land_snapshots'
                               ).fetchone()[0]
            seed.revision(LAND_E, MD5_E, 'SYNTHETIC field E', snap, snap)
            seed.geometry(MD5_E)
            for fid, land, md5 in plan:
                seed.attribution(fid, fld.TIER1_EXACT,
                                 'PLAIN_MD5_GEOMETRY_OBJECT', land, md5, 1)
                seed.calc(fid, rs.RAW_CORROBORATED, rs.AGG_CERTIFIED, 10000.0,
                          10000.0, core.SEP)
            con.commit()
        finally:
            con.close()

    def statements(self, url):
        seen = []
        original = drones._drone_area_control_db

        def traced():
            con = original()
            if con is not None:
                con.set_trace_callback(seen.append)
            return con

        from sqlalchemy import event
        orm = []

        def on_execute(conn, cursor, statement, *args):
            orm.append(statement)

        client = self.client_as()
        with app.app_context():
            engine = db.engine
        event.listen(engine, 'before_cursor_execute', on_execute)
        drones._drone_area_control_db = traced
        try:
            response = client.get(url)
        finally:
            drones._drone_area_control_db = original
            event.remove(engine, 'before_cursor_execute', on_execute)
        self.assertEqual(response.status_code, 200, url)
        return len(seen), len(orm)

    def test_statements_do_not_grow_with_flights(self):
        one = self.statements('/drones/fields/%s%s' % (LAND_E, ALL))
        many = self.statements('/drones/fields/%s%s' % (core.LAND_D, ALL))
        # Счётчик видит запросы вообще, а карточка D действительно несёт 150
        # вылетов (иначе равенство ничего не значит).
        self.assertGreater(one[0], 5)
        self.assertIn('вылетов: 150', self.card(core.LAND_D))
        self.assertIn('вылетов: 1<', self.card(LAND_E))
        self.assertEqual(one, many)

    def test_the_count_sees_a_per_flight_loop(self):
        """Отрицательный контроль: чтение по вылету счётчик заметил бы."""
        from dji_area import field_store
        original = field_store.current_attributions

        def per_flight(con, flight_ids, version=None):
            out = {}
            for fid in flight_ids:
                out.update(original(con, [fid], version))
            return out

        field_store.current_attributions = per_flight
        try:
            few = self.statements('/drones/flights/%d/passport'
                                  % core.F_B_EXACT)
        finally:
            field_store.current_attributions = original
        self.assertGreater(few[0], 0)
        con = dji_store.connect(TEST_DB_PATH, read_only=True)
        seen = []
        con.set_trace_callback(seen.append)
        try:
            per_flight(con, list(range(970000, 970150)))
        finally:
            con.close()
        self.assertGreaterEqual(len(seen), 150)


if __name__ == '__main__':
    unittest.main()
