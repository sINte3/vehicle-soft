# -*- coding: utf-8 -*-
"""Выбор машины на /gps/fact -- замечания владельца от 09.10.2026.

Владелец перед следующей порцией ответов «работа/проезд» (A4) попросил:

  «Окно Машина -- с возможностью набирать вручную номер машины, программа
  предлагает варианты, глазами долго искать нужную машину. Дополнительные
  окна фильтрации по организациям, видам техники итд. Список машин должен
  быть понятен человеку -- заменить id (четырёхзначные значения) на имя
  транспорта».

Что здесь проверяется и почему именно это:

1. **Имя вместо номера.** Объект без строки сопоставления называется именем
   из Wialon, которое записал коллектор, а если имени не знает никто --
   «Объект Wialon 3464», не голым числом. Файл коллектора читается только
   чтением и не создаётся. Машина из справочника по-прежнему важнее имени
   объекта.
2. **Поиск по номеру.** «80156ca» латиницей находит «80 156 СА», записанный
   русскими буквами-двойниками; пробелы не мешают. Таблица двойников одна и
   та же у сервера и у подсказок в браузере (static/js/vs-combobox.js).
3. **Фильтры** организации, вида техники и «только без ответа» сужают
   список; машина, открытая человеком, не выгоняется фильтром «без ответа»,
   когда он ответил на её последний участок.
4. **Соседи и возврат.** «Предыдущая/Следующая» идут по тому же списку, а
   ответ оператора возвращает к той же машине с теми же фильтрами и тем же
   видом карты -- иначе разметка подряд превращается в поиск заново.

Запуск:
  python -m unittest tests.test_gps_fact_picker -v
"""
import hashlib
import json
import os
import re
import shutil
import sqlite3
import unittest

from datetime import date, datetime

from tests.harness import app, db, reset_db, create_admin, login, CSRF
from models import (Equipment, GpsDailyAggregate, GpsWorkPolygon, Organization,
                    User, VialonMapping)
from gps_collector import storage

import gps_routes

DAY = date(2026, 7, 27)
REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SQUARE = json.dumps({"type": "Polygon", "coordinates": [[
    [64.550, 39.990], [64.560, 39.990], [64.560, 40.000],
    [64.550, 40.000], [64.550, 39.990]]]})


def aggregate(wialon_id, reason=None):
    return GpsDailyAggregate(
        work_date=DAY, wialon_id=wialon_id, points_total=100, points_work=50,
        track_km=5.0, interval_median_s=30.0, sats_median=14.0, motion_gaps=0,
        lost_seconds=0.0, gps_jumps=0, reason=reason,
        method_version='adaptive-alpha-2026-08-12',
        computed_at=datetime(2026, 7, 28, 3, 0, 0))


def site(wialon_id, number, label=None, area=1.5):
    return GpsWorkPolygon(
        work_date=DAY, wialon_id=wialon_id, site_number=number, area_ha=area,
        minutes=20.0, polygon_geojson=SQUARE, alpha_used_m=10.0,
        pass_spacing_m=5.0, suggested_label='работа', operator_label=label)


class Base(unittest.TestCase):
    """Сутки 27.07: четыре машины в справочнике, два объекта без машины.

    101 МТЗ-80Х — 80 613 EA      Бухоро Гарден, пропашной; участки 1 (без
                                 ответа) и 2 («работа»)
    102 New Holland — 80 156 СА  Когон ПТЗ, высокопроизводительная; один
                                 участок с ответом «проезд»; номер записан
                                 РУССКИМИ С и А
    104 МТЗ-80Х — 80 614 EA      Когон ПТЗ, пропашной; работы нет
    105 Погрузчик — 80 373 HA    Когон ПТЗ, спецтехника; старый участок без
                                 ответа, который экран не показывает
    103 «Камаз 80 777 KA»        строка сопоставления без машины
    3464                         объекта нет нигде, кроме данных GPS
    """

    def setUp(self):
        reset_db()
        self.admin_id = create_admin()
        self.folder = app.config['GPS_POINTS_DIR']
        shutil.rmtree(self.folder, ignore_errors=True)
        os.makedirs(self.folder)
        self.addCleanup(shutil.rmtree, self.folder, True)
        with app.app_context():
            User.query.get(self.admin_id).language = 'ru'
            garden = Organization(name='Бухоро Гарден', short_name='',
                                  sort_order=1)
            kogon = Organization(name='Когон ПТЗ МЧЖ', short_name='Когон ПТЗ',
                                 sort_order=2)
            db.session.add_all([garden, kogon])
            db.session.flush()
            self.garden_id, self.kogon_id = garden.id, kogon.id
            machines = [
                (101, 'МТЗ-80Х', '80 613 EA', 'mtz', garden.id),
                (102, 'New Holland 7060', '80 156 СА', 'yukori', kogon.id),
                (104, 'МТЗ-80Х', '80 614 EA', 'mtz', kogon.id),
                (105, 'Погрузчик Amkodor', '80 373 HA', 'special', kogon.id),
            ]
            for wialon_id, name, plate, category, org_id in machines:
                equipment = Equipment(name=name, plate=plate, category=category,
                                      organization_id=org_id)
                db.session.add(equipment)
                db.session.flush()
                db.session.add(VialonMapping(vialon_name='obj %d' % wialon_id,
                                             wialon_id=wialon_id,
                                             equipment_id=equipment.id,
                                             skip=False))
            db.session.add(VialonMapping(vialon_name='Камаз 80 777 KA',
                                         wialon_id=103, skip=False))
            for wialon_id in (101, 102, 103, 104, 3464):
                db.session.add(aggregate(wialon_id))
            db.session.add(aggregate(105, reason='spetstekhnika'))
            db.session.add_all([site(101, 1), site(101, 2, 'работа'),
                                site(102, 1, 'проезд'), site(105, 1)])
            db.session.commit()

    def page(self, query=''):
        client = app.test_client()
        login(client, self.admin_id)
        resp = client.get('/gps/fact?date=2026-07-27' + query)
        self.assertEqual(resp.status_code, 200, query)
        return resp.get_data(as_text=True)

    def listed(self, html):
        listbox = html.split('role="listbox"')[1].split('</ul>')[0]
        return [int(v) for v in re.findall(r'data-value="(\d+)"', listbox)]

    def opened(self, html):
        found = re.search(r'<input type="hidden" name="unit" value="(\d*)"', html)
        return int(found.group(1)) if found and found.group(1) else None

    def option_text(self, html, wialon_id):
        found = re.search(r'id="gps-fact-machine-%d".*?</li>' % wialon_id, html, re.S)
        return re.sub(r'\s+', ' ', re.sub(r'<[^>]+>', ' ', found.group(0))) if found else ''


class NamesInsteadOfNumbers(Base):
    """Пункт 1."""

    def test_an_object_without_a_mapping_is_named_by_the_collector(self):
        storage.write_unit_names(self.folder, [
            {'id': 3464, 'name': 'Т-28 80 990 HA'}])
        html = self.page()
        self.assertIn('Т-28 80 990 HA', self.option_text(html, 3464))
        self.assertFalse('Объект Wialon 3464' in html)

    def test_without_the_collector_file_the_number_is_said_to_be_a_wialon_object(self):
        # отрицательный контроль к тесту выше: имени нет нигде
        html = self.page()
        self.assertIn('Объект Wialon 3464', self.option_text(html, 3464))
        self.assertFalse(os.path.exists(storage.state_path(self.folder)),
                         'экран не создаёт файл коллектора')

    def test_the_uzbek_interface_names_the_object_in_uzbek(self):
        with app.app_context():
            User.query.get(self.admin_id).language = 'uz'
            db.session.commit()
        html = self.page()
        self.assertIn('Wialon объекти 3464', self.option_text(html, 3464))
        self.assertFalse('Объект Wialon' in html)

    def test_a_machine_from_the_directory_beats_the_wialon_name(self):
        storage.write_unit_names(self.folder, [
            {'id': 101, 'name': 'MTZ old tracker'}])
        html = self.page()
        self.assertIn('МТЗ-80Х — 80 613 EA', self.option_text(html, 101))
        self.assertFalse('MTZ old tracker' in html)

    def test_a_mapping_row_without_a_machine_keeps_its_name(self):
        storage.write_unit_names(self.folder, [{'id': 103, 'name': 'other'}])
        self.assertIn('Камаз 80 777 KA', self.option_text(self.page(), 103))

    def test_the_collector_file_is_only_read(self):
        storage.write_unit_names(self.folder, [
            {'id': 3464, 'name': 'Т-28 80 990 HA'}])
        path = storage.state_path(self.folder)

        def digest():
            with open(path, 'rb') as handle:
                return hashlib.sha256(handle.read()).hexdigest()

        before = digest()
        self.page()
        self.page('&unit=3464')
        self.assertEqual(digest(), before)

    def test_an_old_collector_file_without_names_does_not_take_the_page_down(self):
        storage.set_watermark(self.folder, 3464, 1000)    # таблицы имён нет
        self.assertIn('Объект Wialon 3464', self.option_text(self.page(), 3464))

    def test_a_broken_collector_file_does_not_take_the_page_down(self):
        with open(storage.state_path(self.folder), 'wb') as handle:
            handle.write(b'not a database at all')
        self.assertIn('Объект Wialon 3464', self.option_text(self.page(), 3464))

    def test_named_objects_sort_among_names_and_the_nameless_go_last(self):
        storage.write_unit_names(self.folder, [
            {'id': 3464, 'name': 'Т-28 80 990 HA'}])
        with app.app_context():
            db.session.add(aggregate(7001))                 # имени нет нигде
            db.session.commit()
        listed = self.listed(self.page())
        self.assertEqual(listed[-1], 7001)
        self.assertLess(listed.index(3464), listed.index(7001))


class SearchByNumber(Base):
    """Пункт 2."""

    def test_latin_letters_find_a_plate_written_in_cyrillic(self):
        self.assertEqual(gps_routes.search_key('80 156 СА'),
                         gps_routes.search_key('80156ca'))
        self.assertEqual(gps_routes.search_key('80 156 СА'), '80156ca')

    def test_spaces_case_and_dashes_do_not_matter(self):
        self.assertEqual(gps_routes.search_key('МТЗ-80Х — 80 613 EA'),
                         gps_routes.search_key('мтз80х80613ea'))

    def test_different_plates_stay_different(self):
        # отрицательный контроль: ключ не стирает различия номеров
        self.assertNotEqual(gps_routes.search_key('80 613 EA'),
                            gps_routes.search_key('80 614 EA'))

    def test_the_browser_table_is_the_server_table(self):
        with open(os.path.join(REPO_ROOT, 'static', 'js', 'vs-combobox.js'),
                  encoding='utf-8') as handle:
            source = handle.read()

        def js_string(name):
            found = re.search(r"var %s = '([^']*)';" % name, source)
            self.assertIsNotNone(found, name)
            return json.loads('"%s"' % found.group(1))

        self.assertEqual(js_string('LOOKALIKE_FROM'),
                         gps_routes.SEARCH_LOOKALIKE_FROM)
        self.assertEqual(js_string('LOOKALIKE_TO'),
                         gps_routes.SEARCH_LOOKALIKE_TO)
        keep = re.search(r'var KEEP = /\[([^\]]*)\]/;', source).group(1)
        self.assertEqual(json.loads('"%s"' % keep), gps_routes.SEARCH_KEEP)

    def test_a_typed_number_opens_its_machine_without_the_list(self):
        # без скрипта: набрал, нажал «Показать» -- ищет сервер
        html = self.page('&q=80156ca')
        self.assertEqual(self.opened(html), 102)
        self.assertFalse(re.search(r'По «[^»]*» (найдено|машин не найдено)', html))

    def test_several_matches_open_the_first_and_say_how_many(self):
        html = self.page('&q=МТЗ')
        self.assertEqual(self.opened(html), 101)
        self.assertTrue('По «МТЗ» найдено машин: 2' in html)

    def test_nothing_found_is_said_and_the_page_still_opens(self):
        html = self.page('&q=99999&unit=104')
        self.assertTrue('По «99999» машин не найдено' in html)
        self.assertEqual(self.opened(html), 104)

    def test_the_name_of_the_open_machine_is_not_a_search(self):
        # форма отправлена как есть: в поле имя той же машины
        html = self.page('&unit=104&q=МТЗ-80Х — 80 614 EA')
        self.assertEqual(self.opened(html), 104)
        self.assertFalse(re.search(r'По «[^»]*» (найдено|машин не найдено)', html))

    def test_a_filter_changed_without_the_script_is_not_a_failed_search(self):
        # Без скрипта сменили организацию и нажали «Показать»: в поле осталось
        # имя прежней машины чужой организации. Это не поиск, и «машин не
        # найдено» здесь было бы неправдой.
        html = self.page('&org=%d&unit=104&q=МТЗ-80Х — 80 614 EA' % self.garden_id)
        self.assertEqual(self.opened(html), 101)
        self.assertFalse(re.search(r'По «[^»]*» (найдено|машин не найдено)', html))


class Filters(Base):
    """Пункт 3."""

    def test_the_filter_lists_hold_only_what_this_day_has(self):
        html = self.page()
        org_select = html.split('name="org"')[1].split('</select>')[0]
        self.assertEqual(re.findall(r'<option value="([^"]*)"', org_select),
                         ['', str(self.garden_id), str(self.kogon_id), 'none'])
        self.assertIn('>Когон ПТЗ<', org_select)          # короткое имя
        cat_select = html.split('name="cat"')[1].split('</select>')[0]
        self.assertEqual(re.findall(r'<option value="([^"]*)"', cat_select),
                         ['', 'yukori', 'mtz', 'special'])
        self.assertIn('2. Тракторы (пропашные)', cat_select)

    def test_an_organisation_narrows_the_list_and_the_default(self):
        html = self.page('&org=%d' % self.kogon_id)
        self.assertEqual(sorted(self.listed(html)), [102, 104, 105])
        self.assertIn(self.opened(html), (102, 104, 105))

    def test_not_linked_lists_the_objects_without_a_machine(self):
        html = self.page('&org=none')
        self.assertEqual(sorted(self.listed(html)), [103, 3464])

    def test_a_category_narrows_the_list(self):
        html = self.page('&cat=mtz')
        self.assertEqual(sorted(self.listed(html)), [101, 104])

    def test_only_unanswered_lists_machines_with_a_site_left(self):
        # 105 -- спецтехника: её старый участок без ответа на экран не
        # выводится, и звать на него оператора нельзя
        html = self.page('&open=1')
        self.assertEqual(self.listed(html), [101])
        self.assertEqual(self.opened(html), 101)

    def test_the_open_machine_stays_when_its_last_site_is_answered(self):
        # 102 ответов не ждёт, но человек открыл её сам -- после ответа он
        # остаётся на ней, а список и «Следующая» идут по фильтру
        html = self.page('&open=1&unit=102')
        self.assertEqual(self.opened(html), 102)
        self.assertEqual(self.listed(html), [101])

    def test_a_machine_outside_the_organisation_is_replaced(self):
        html = self.page('&org=%d&unit=101' % self.kogon_id)
        self.assertIn(self.opened(html), (102, 104, 105))

    def test_unknown_filter_values_mean_no_filter(self):
        html = self.page('&cat=tanks&org=abc')
        self.assertEqual(len(self.listed(html)), 6)

    def test_nothing_matches_is_said_with_a_way_back(self):
        html = self.page('&org=%d&open=1' % self.kogon_id)
        self.assertTrue('По выбранным фильтрам машин нет' in html)
        self.assertTrue('href="/gps/fact?date=2026-07-27"' in html)
        self.assertIsNone(self.opened(html))

    def test_the_notes_count_sites_and_unanswered_ones(self):
        html = self.page()
        self.assertIn('Бухоро Гарден · участков: 2 · без ответа: 1',
                      self.option_text(html, 101))
        self.assertIn('нет площади', self.option_text(html, 105))
        self.assertIn('нет в справочнике техники', self.option_text(html, 3464))


class NeighboursAndReturn(Base):
    """Пункт 4."""

    def nav(self, html):
        head = html.split('class="gps-fact-head"')[1].split('</div>')[0]
        return {rel: href.replace('&amp;', '&') for href, rel in
                re.findall(r'href="([^"]+)" rel="(prev|next)"', head)}

    def test_neighbours_follow_the_filtered_list_and_carry_the_filters(self):
        html = self.page('&cat=mtz&unit=101')
        nav = self.nav(html)
        self.assertNotIn('prev', nav)
        self.assertEqual(nav['next'],
                         '/gps/fact?date=2026-07-27&unit=104&cat=mtz#gps-fact-work')

    def test_the_last_machine_has_no_next(self):
        nav = self.nav(self.page('&cat=mtz&unit=104'))
        self.assertNotIn('next', nav)
        self.assertIn('unit=101', nav['prev'])

    def test_an_answer_returns_to_the_machine_with_filters_and_map_view(self):
        with app.app_context():
            site_id = GpsWorkPolygon.query.filter_by(wialon_id=101,
                                                     site_number=1).one().id
        client = app.test_client()
        login(client, self.admin_id)
        resp = client.post('/gps/fact/answer', data={
            'csrf_token': CSRF, 'site_id': site_id, 'label': 'проезд',
            'org': str(self.garden_id), 'cat': 'mtz', 'open': '1',
            'view': '16.5/39.995000/64.555000'})
        self.assertEqual(resp.status_code, 302)
        self.assertEqual(
            resp.headers['Location'],
            '/gps/fact?date=2026-07-27&unit=101&org=%d&cat=mtz&open=1'
            '&view=16.5/39.995000/64.555000#gps-fact-work' % self.garden_id)

    def test_a_forged_view_is_dropped_not_passed_on(self):
        with app.app_context():
            site_id = GpsWorkPolygon.query.filter_by(wialon_id=101,
                                                     site_number=1).one().id
        client = app.test_client()
        login(client, self.admin_id)
        resp = client.post('/gps/fact/answer', data={
            'csrf_token': CSRF, 'site_id': site_id, 'label': 'работа',
            'view': '16/39.99/64.55"><script>'})
        self.assertEqual(resp.headers['Location'],
                         '/gps/fact?date=2026-07-27&unit=101#gps-fact-work')

    def test_the_answer_form_carries_the_filters(self):
        html = self.page('&cat=mtz&unit=101')
        form = html.split('action="/gps/fact/answer"')[1].split('</form>')[0]
        self.assertIn('<input type="hidden" name="cat" value="mtz">', form)
        self.assertIn('data-vs-map-view-of="gps-fact-map"', form)

    def test_the_map_opens_where_it_was_left(self):
        html = self.page('&unit=101&view=16.5/39.995000/64.555000')
        self.assertTrue('data-vs-map-view="16.5/39.995000/64.555000"' in html)
        clean = self.page('&unit=101&view=99/0/0')
        self.assertFalse('data-vs-map-view=' in clean)

    def test_the_map_buttons_speak_the_interface_language(self):
        html = self.page('&unit=101')
        data = json.loads(re.search(
            r'<script type="application/json" id="gps-fact-map">(.*?)</script>',
            html, re.S).group(1))
        self.assertEqual(data['ui']['fullscreen'], 'Развернуть карту на весь экран')
        with app.app_context():
            User.query.get(self.admin_id).language = 'uz'
            db.session.commit()
        html = self.page('&unit=101')
        data = json.loads(re.search(
            r'<script type="application/json" id="gps-fact-map">(.*?)</script>',
            html, re.S).group(1))
        self.assertEqual(data['ui']['fullscreen'], 'Харитани тўлиқ экранга ёйиш')


class ParseMapView(unittest.TestCase):
    def test_good_and_bad_views(self):
        self.assertEqual(gps_routes.parse_map_view('17/39.990000/64.550000'),
                         '17/39.990000/64.550000')
        for bad in ('', '17', '17/39/64', '23/39.9/64.5', '17/91.0/64.5',
                    '17/39.9/181.0', 'x/39.9/64.5', '17/39.9/64.5/1'):
            self.assertIsNone(gps_routes.parse_map_view(bad), bad)


if __name__ == '__main__':
    unittest.main()
