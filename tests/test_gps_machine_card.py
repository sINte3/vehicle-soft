# -*- coding: utf-8 -*-
"""GPS A3: карточка машины за период (/gps/machine).

План трека (раздел «План сессии GPS после 28.09»): «Карточка машины за
период, как у agro-work: период с быстрыми кнопками, трек/гектар, сводка
(км, время, га)».

Что проверяется и почему именно это:

1. **Период.** Быстрые кнопки дают ровно те сутки, что написаны на них;
   «по» по умолчанию -- вчера (факт считается ночью после суток); период
   длиннее 92 суток сокращается и экран это говорит.
2. **Гектары -- те же, что в сверке.** Только опубликованные сутки,
   «проезд» вычтен, участок без ответа входит и назван отдельно. Полигоны
   суток с причиной (редкая запись, спецтехника) расчёт не удаляет --
   сложи их карточка, она вернула бы гектары, которые правило не
   публикует. Отрицательный контроль: эти полигоны в фикстуре есть и
   крупнее остальных, поэтому ошибка сдвинула бы сумму заметно.
3. **Время в движении -- сумма того, что «Факт» показывает за каждые
   сутки**, а сутки, чьих точек на диске нет, -- «не посчитано», а не ноль.
4. **Сутки без расчёта видны**, а не пропадают из таблицы.
5. **Только чтение.** Файл точек побайтно тот же после страницы.
6. **Карта** -- в <script type="application/json">, трек по суткам,
   участки опубликованных суток без постоянных номеров.
7. **Права и меню** -- те же, что у «Факта»: модуль wialon.

Запуск:
  python -m unittest tests.test_gps_machine_card -v
"""
import hashlib
import json
import os
import re
import shutil
import unittest

from datetime import date, datetime, timedelta, timezone
from html import unescape

from tests.harness import app, db, reset_db, create_admin, login
from models import (Equipment, FieldContour, GpsDailyAggregate,
                    GpsWorkPolygon, Organization, User, UserModulePermission,
                    VialonMapping, ROLE_OPERATOR)
from gps_collector import storage

import gps_routes

TZ = timezone(timedelta(hours=5))
D1, D2, D3, D4 = (date(2026, 7, 27) + timedelta(days=i) for i in range(4))
UNIT = 101
SQUARE = json.dumps({"type": "Polygon", "coordinates": [[
    [64.5500001234, 39.9900004321], [64.560, 39.990], [64.560, 40.000],
    [64.550, 40.000], [64.5500001234, 39.9900004321]]]})
PERIOD = '?from=2026-07-27&to=2026-07-30'


def aggregate(day, wialon_id=UNIT, reason=None, km=10.0, points=100):
    return GpsDailyAggregate(
        work_date=day, wialon_id=wialon_id, points_total=points,
        points_work=50, track_km=km, interval_median_s=30.0, sats_median=14.0,
        motion_gaps=0, lost_seconds=0.0, gps_jumps=0, reason=reason,
        method_version='adaptive-alpha-2026-08-12',
        computed_at=datetime(2026, 8, 1, 3, 0, 0))


def site(day, number, area, label=None, wialon_id=UNIT, contour_id=None):
    return GpsWorkPolygon(
        work_date=day, wialon_id=wialon_id, site_number=number, area_ha=area,
        minutes=30.0, polygon_geojson=SQUARE, alpha_used_m=10.0,
        pass_spacing_m=5.0, suggested_label='работа', operator_label=label,
        contour_id=contour_id)


def run(day, start_s, count, step_s=30, speed=8.0):
    """Прямая езда: count точек через step_s секунд."""
    midnight = int(datetime(day.year, day.month, day.day,
                            tzinfo=TZ).timestamp())
    return [(midnight + start_s + i * step_s, 64.55 + i * 2e-4,
             39.99 + i * 6e-5, speed) for i in range(count)]


class Period(unittest.TestCase):
    """Пункт 1: быстрые кнопки и поля дат."""

    TODAY = date(2026, 10, 22)

    def period(self, today=TODAY, **args):
        return gps_routes.machine_period(args, today)

    def test_by_default_two_weeks_up_to_yesterday(self):
        self.assertEqual(self.period(),
                         (date(2026, 10, 8), date(2026, 10, 21), 'custom',
                          False))

    def test_quick_buttons_give_the_days_written_on_them(self):
        self.assertEqual(self.period(preset='yesterday')[:3],
                         (date(2026, 10, 21), date(2026, 10, 21), 'yesterday'))
        self.assertEqual(self.period(preset='week')[:3],
                         (date(2026, 10, 15), date(2026, 10, 21), 'week'))
        self.assertEqual(self.period(preset='this_month')[:3],
                         (date(2026, 10, 1), date(2026, 10, 21), 'this_month'))
        self.assertEqual(self.period(preset='last_month')[:3],
                         (date(2026, 9, 1), date(2026, 9, 30), 'last_month'))

    def test_last_month_in_january_is_december_of_the_year_before(self):
        self.assertEqual(self.period(date(2027, 1, 15), preset='last_month')[:2],
                         (date(2026, 12, 1), date(2026, 12, 31)))

    def test_on_the_first_the_current_month_is_today_not_the_last_month(self):
        # [REASON]: первого числа текущий месяц -- одни сегодняшние сутки;
        # подменить его прошлым значило бы показать под кнопкой «Текущий
        # месяц» чужой месяц.
        self.assertEqual(self.period(date(2026, 11, 1), preset='this_month')[:2],
                         (date(2026, 11, 1), date(2026, 11, 1)))

    def test_a_quick_button_wins_over_the_dates(self):
        self.assertEqual(
            self.period(preset='yesterday', **{'from': '2026-01-01',
                                               'to': '2026-01-05'})[:2],
            (date(2026, 10, 21), date(2026, 10, 21)))

    def test_dates_typed_backwards_are_swapped(self):
        self.assertEqual(self.period(**{'from': '2026-09-10',
                                        'to': '2026-09-01'})[:2],
                         (date(2026, 9, 1), date(2026, 9, 10)))

    def test_a_long_period_is_cut_to_92_days_up_to_the_end_date(self):
        found = self.period(**{'from': '2026-01-01', 'to': '2026-10-21'})
        self.assertEqual(found[1], date(2026, 10, 21))
        self.assertEqual((found[1] - found[0]).days + 1, 92)
        self.assertTrue(found[3])
        exact = self.period(**{'from': '2026-07-22', 'to': '2026-10-21'})
        self.assertEqual((exact[1] - exact[0]).days + 1, 92)
        self.assertFalse(exact[3])
        # граница: 93 суток -- уже сокращается
        over = self.period(**{'from': '2026-07-21', 'to': '2026-10-21'})
        self.assertEqual(over[0], date(2026, 7, 22))
        self.assertTrue(over[3])

    def test_garbage_is_the_default_and_an_unknown_button_is_ignored(self):
        self.assertEqual(self.period(preset='forever', **{'from': 'x',
                                                          'to': '31.12'}),
                         self.period())


class Base(unittest.TestCase):
    """Машина 101 (МТЗ-80Х — 80 613 EA, Бухоро Гарден) за 27–30.07:

    27.07  опубликованы: 2,0 га «работа», 3,0 га без ответа, 1,5 га «проезд»
    28.07  опубликованы, участков нет (работы нет)
    29.07  редкая запись; от расчёта остался полигон 40 га
    30.07  расчёта нет

    102 -- погрузчик (спецтехника), 103 -- помечен «не наша» (skip).
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
            db.session.add(garden)
            db.session.flush()
            self.org_id = garden.id
            contour = FieldContour(source='wialon', external_id='7',
                                   name='Поле 7 </script><b>',
                                   geometry_geojson=SQUARE)
            db.session.add(contour)
            db.session.flush()
            for wialon_id, name, plate, category, skip in (
                    (101, 'МТЗ-80Х', '80 613 EA', 'mtz', False),
                    (102, 'Погрузчик Amkodor', '80 373 HA', 'special', False),
                    (103, 'МТЗ-82', '80 999 EA', 'mtz', True)):
                equipment = Equipment(name=name, plate=plate,
                                      category=category,
                                      organization_id=garden.id)
                db.session.add(equipment)
                db.session.flush()
                db.session.add(VialonMapping(vialon_name='obj %d' % wialon_id,
                                             wialon_id=wialon_id,
                                             equipment_id=equipment.id,
                                             skip=skip))
            db.session.add_all([
                aggregate(D1, km=30.5), aggregate(D2, km=4.0),
                aggregate(D3, reason='redkaya_zapis', km=12.0),
                site(D1, 1, 2.0, 'работа', contour_id=contour.id),
                site(D1, 2, 3.0), site(D1, 3, 1.5, 'проезд'),
                site(D3, 1, 40.0),
                aggregate(D1, 102, reason='spetstekhnika', km=7.0),
                site(D1, 1, 25.0, wialon_id=102),
                aggregate(D1, 103, km=99.0), site(D1, 1, 50.0, wialon_id=103),
            ])
            db.session.commit()

    def write_track(self, rows, unit=UNIT):
        storage.write_points(self.folder, [
            (unit, t, lon, lat, speed, 90, 14) for t, lon, lat, speed in rows])

    def client(self, user_id=None):
        client = app.test_client()
        login(client, user_id or self.admin_id)
        return client

    def page(self, query=PERIOD + '&unit=%d' % UNIT):
        resp = self.client().get('/gps/machine' + query)
        self.assertEqual(resp.status_code, 200, query)
        return resp.get_data(as_text=True)

    def stat(self, html, label):
        found = re.search(r'<div class="vs-stat-label">%s</div>\s*'
                          r'<div class="vs-stat-value[^"]*">(.*?)</div>'
                          % re.escape(label), html, re.S)
        self.assertIsNotNone(found, label)
        return ' '.join(found.group(1).split())

    def rows(self, html):
        body = html.split('<tbody>')[-1].split('</tbody>')[0]
        return [re.sub(r'\s+', ' ', re.sub(r'<[^>]+>', ' ', unescape(row))).strip()
                for row in re.findall(r'<tr>(.*?)</tr>', body, re.S)]

    def map_data(self, html):
        found = re.search(r'<script type="application/json" '
                          r'id="gps-machine-map">(.*?)</script>', html, re.S)
        return json.loads(found.group(1)) if found else None

    def listed(self, html):
        listbox = html.split('role="listbox"')[1].split('</ul>')[0]
        return [int(v) for v in re.findall(r'data-value="(\d+)"', listbox)]


class Summary(Base):
    """Пункты 2 и 4: сводка и сутки подряд."""

    def test_hectares_are_published_days_without_passage(self):
        html = self.page()
        # 2,0 «работа» + 3,0 без ответа; «проезд» 1,5 вычтен; 40 га суток с
        # редкой записью не входят. Ошибка любой стороны дала бы 6,50 или
        # 45,00 / 46,50.
        self.assertEqual(self.stat(html, 'Обработано, га'), '5.00')
        self.assertEqual(self.stat(html, 'Суток с работой'), '1')
        self.assertEqual(self.stat(html, 'Участков, из них без ответа'),
                         '3 / 1')
        self.assertIn('Без ответа «работа/проезд» — участков: 1, 3.00 га',
                      unescape(html))

    def test_kilometres_are_every_computed_day_and_nothing_else(self):
        # 30,5 + 4,0 + 12,0: пробег измерен и у суток с редкой записью.
        html = self.page()
        self.assertEqual(self.stat(html, 'Пробег по треку, км'), '46.5')
        self.assertEqual(self.stat(html, 'Суток с расчётом, из них с площадью'),
                         '3 / 2')

    def test_every_day_of_the_period_has_a_row(self):
        rows = self.rows(self.page())
        self.assertEqual(len(rows), 4)
        self.assertTrue(rows[0].startswith('27.07.2026 площадь опубликована 5.00 3 / 1 30.5'), rows[0])
        self.assertTrue(rows[1].startswith('28.07.2026 площадь опубликована 0.00 0 / 0 4.0'), rows[1])
        self.assertTrue(rows[2].startswith('29.07.2026 Редкая запись трека'), rows[2])
        self.assertIn('— — 12.0', rows[2])
        self.assertEqual(rows[3], '30.07.2026 расчёта нет — — — — —')
        self.assertIn('Суток без расчёта: 1 из 4', unescape(self.page()))

    def test_a_day_links_to_the_fact_screen_of_the_same_machine(self):
        html = self.page()
        self.assertIn('href="/gps/fact?date=2026-07-27&amp;unit=101"', html)
        self.assertNotIn('href="/gps/fact?date=2026-07-30', html)

    def test_a_special_machine_shows_no_hectares_from_old_polygons(self):
        html = self.page(PERIOD + '&unit=102')
        self.assertEqual(self.stat(html, 'Обработано, га'), '—')
        self.assertIn('Спецтехника — гектары не считаются', html)
        self.assertEqual(self.stat(html, 'Пробег по треку, км'), '7.0')

    def test_a_period_where_area_was_never_published_says_so(self):
        html = self.page('?from=2026-07-29&to=2026-07-29&unit=101')
        self.assertEqual(self.stat(html, 'Обработано, га'), '—')
        self.assertIn('Площадь не публиковалась ни в одни сутки периода',
                      html)

    def test_the_summary_is_the_sum_of_the_days(self):
        with app.app_context():
            aggregates = {a.work_date: a for a in GpsDailyAggregate.query
                          .filter_by(wialon_id=UNIT)}
            sites = {}
            for s in GpsWorkPolygon.query.filter_by(wialon_id=UNIT):
                sites.setdefault(s.work_date, []).append(s)
            rows = gps_routes.machine_days(D1, D4, aggregates, sites,
                                           lambda day: [])
            summary = gps_routes.machine_summary(rows)
        self.assertEqual(summary['ha'],
                         sum(r['ha'] for r in rows if r['ha'] is not None))
        self.assertEqual([r['ha'] for r in rows], [5.0, 0.0, None, None])
        self.assertEqual(summary['days'], 4)


class TimeInMotion(Base):
    """Пункт 3: время -- сумма суток «Факта», неизвестное -- не ноль."""

    def test_moving_time_is_the_sum_of_what_fact_shows_per_day(self):
        first = run(D1, 6 * 3600, 121) + run(D1, 12 * 3600, 61)
        second = run(D2, 8 * 3600, 31)
        self.write_track(first + second)
        expected = (gps_routes.track_summary(first)['moving_s']
                    + gps_routes.track_summary(second)['moving_s'])
        self.assertEqual(expected, 3600 + 1800 + 900)
        html = self.page()
        self.assertEqual(self.stat(html, 'В движении'), '1 ч 45 мин')
        rows = self.rows(html)
        self.assertIn('1:30 06:00–12:30', rows[0])
        self.assertIn('0:15 08:00–08:15', rows[1])
        # и «Факт» за 27.07 показывает те же полтора часа
        fact = self.client().get('/gps/fact?date=2026-07-27&unit=101')
        self.assertIn('1 ч 30 мин', fact.get_data(as_text=True))

    def test_a_day_whose_points_are_not_on_disk_is_unknown_not_zero(self):
        self.write_track(run(D1, 6 * 3600, 121))
        html = self.page()
        rows = self.rows(html)
        self.assertIn('— —', rows[1].split('4.0', 1)[1])
        self.assertIn('Время в движении не посчитано у суток: 2', unescape(html))

    def test_a_day_without_points_by_the_calculation_is_zero_time(self):
        with app.app_context():
            GpsDailyAggregate.query.filter_by(wialon_id=UNIT, work_date=D2) \
                .update({'points_total': 0})
            db.session.commit()
        self.write_track(run(D1, 6 * 3600, 121))
        rows = self.rows(self.page())
        self.assertIn('0:00', rows[1])


class ReadOnly(Base):
    """Пункт 5: экран не пишет в файл коллектора."""

    def test_the_point_file_is_byte_identical_after_the_page(self):
        self.write_track(run(D1, 6 * 3600, 121) + run(D2, 8 * 3600, 31))
        path = storage.points_path(self.folder, '202607')
        before = hashlib.sha256(open(path, 'rb').read()).hexdigest()
        self.page()
        self.assertEqual(hashlib.sha256(open(path, 'rb').read()).hexdigest(),
                         before)

    def test_a_missing_month_file_is_not_created(self):
        self.page('?from=2026-08-01&to=2026-08-02&unit=101')
        self.assertFalse(os.path.exists(storage.points_path(self.folder,
                                                            '202608')))


class MapOfThePeriod(Base):
    """Пункт 6: карта периода."""

    def test_track_by_day_sites_of_published_days_and_their_contour(self):
        self.write_track(run(D1, 6 * 3600, 121) + run(D2, 8 * 3600, 31))
        data = self.map_data(self.page())
        tracks = [l for l in data['layers'] if l['kind'] == 'track']
        areas = [l for l in data['layers'] if l['kind'] == 'area']
        outlines = [l for l in data['layers'] if l['kind'] == 'outline']
        self.assertEqual([l['title'] for l in tracks],
                         ['27.07.2026', '28.07.2026'])
        # три участка 27.07, а не полигон 40 га суток с редкой записью
        self.assertEqual(len(areas), 3)
        self.assertTrue(all(l['title'].startswith('27.07.2026') for l in areas))
        self.assertEqual(sorted(l['tone'] for l in areas),
                         ['danger', 'primary', 'success'])
        self.assertTrue(all('label' not in l for l in areas))
        self.assertEqual(len(outlines), 1)

    def test_coordinates_are_rounded_on_the_map_and_not_in_the_base(self):
        data = self.map_data(self.page())
        first = [l for l in data['layers'] if l['kind'] == 'area'][0]
        self.assertEqual(first['geojson']['coordinates'][0][0],
                         [64.55, 39.99])
        with app.app_context():
            self.assertIn('64.5500001234',
                          GpsWorkPolygon.query.first().polygon_geojson)

    def test_a_contour_name_cannot_close_the_data_block(self):
        html = self.page()
        block = html.split('id="gps-machine-map">')[1].split('</script>')[0]
        self.assertNotIn('<b>', block)
        self.assertEqual(
            [l['title'] for l in self.map_data(html)['layers']
             if l['kind'] == 'outline'], ['Поле 7 </script><b>'])

    def test_a_period_with_nothing_to_draw_has_no_map_and_no_library(self):
        html = self.page('?from=2026-07-29&to=2026-07-30&unit=101')
        self.assertIsNone(self.map_data(html))
        self.assertNotIn('leaflet.js', html)


class Picker(Base):
    """Выбор машины: список периода, фильтры, поиск, быстрые кнопки."""

    def test_the_list_is_the_machines_of_the_period_without_excluded(self):
        html = self.page(PERIOD)
        self.assertEqual(sorted(self.listed(html)), [101, 102])
        # по умолчанию -- машина с площадью, а не погрузчик
        self.assertIn('<input type="hidden" name="unit" value="101"', html)

    def test_an_excluded_unit_in_the_address_is_not_opened(self):
        html = self.page(PERIOD + '&unit=103')
        self.assertIn('<input type="hidden" name="unit" value="101"', html)
        self.assertNotIn('99.0', html)

    def test_a_typed_plate_finds_the_machine(self):
        html = self.page(PERIOD + '&q=80373ha')
        self.assertIn('<input type="hidden" name="unit" value="102"', html)

    def test_the_category_filter_narrows_the_list(self):
        html = self.page(PERIOD + '&cat=special')
        self.assertEqual(self.listed(html), [102])
        self.assertIn('всего за период 2', html)

    def test_quick_buttons_keep_the_machine_and_the_filters(self):
        html = unescape(self.page(PERIOD + '&unit=101&org=%d' % self.org_id))
        found = re.search(r'href="([^"]*preset=week[^"]*)"', html)
        self.assertIsNotNone(found)
        self.assertIn('unit=101', found.group(1))
        self.assertIn('org=%d' % self.org_id, found.group(1))
        self.assertNotIn('from=', found.group(1))

    def test_a_too_long_period_is_named_on_the_screen(self):
        html = self.page('?from=2026-01-01&to=2026-07-30&unit=101')
        self.assertIn('Период сокращён до 92 суток', html)

    def test_a_period_without_any_calculation_says_so(self):
        html = self.page('?from=2026-09-01&to=2026-09-03')
        self.assertIn('За этот период расчёта нет ни по одной машине', html)


class AccessAndMenu(Base):
    """Пункт 7: те же права, что у «Факта»."""

    def test_the_menu_has_the_card_and_fact_links_to_it(self):
        fact = self.client().get('/gps/fact?date=2026-07-27&unit=101') \
            .get_data(as_text=True)
        self.assertIn('href="/gps/machine"', fact)
        # «За период» с «Факта» -- две недели по открытые сутки, та же машина
        link = re.search(r'href="(/gps/machine\?[^"]+)"', fact)
        self.assertIsNotNone(link)
        self.assertEqual(sorted(unescape(link.group(1)).split('?')[1].split('&')),
                         ['from=2026-07-14', 'to=2026-07-27', 'unit=101'])

    def test_without_the_wialon_module_it_is_403_and_not_in_the_menu(self):
        with app.app_context():
            operator = User(username='op', role=ROLE_OPERATOR, full_name='Op')
            operator.set_password('x')
            db.session.add(operator)
            db.session.flush()
            db.session.add(UserModulePermission(
                user_id=operator.id, module_code='fuel', has_access=True))
            db.session.commit()
            operator_id = operator.id
        client = self.client(operator_id)
        self.assertEqual(client.get('/gps/machine').status_code, 403)
        self.assertNotIn('/gps/machine',
                         client.get('/fuel/').get_data(as_text=True))

    def test_the_uzbek_interface_is_uzbek_in_cyrillic(self):
        with app.app_context():
            User.query.get(self.admin_id).language = 'uz'
            db.session.commit()
        html = self.page()
        for text in ('Машина давр бўйича', 'Ишланган, га', 'Ҳаракатда',
                     'Давр кунлари', 'ҳисоб йўқ', 'Охирги 7 кун'):
            self.assertIn(text, html)
        for text in ('Обработано', 'В движении', 'расчёта нет',
                     'Последние 7 суток'):
            self.assertNotIn(text, html)


if __name__ == '__main__':
    unittest.main()
