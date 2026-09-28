# -*- coding: utf-8 -*-
"""GPS A2: карта суток на /gps/fact -- трек, участки, контуры их полей.

Что проверяется и почему именно это:

1. **Трек читается ТОЛЬКО чтением.** Экран открывает помесячный файл
   коллектора; писатель в файле точек -- это порча данных, которые больше
   нигде не лежат. Проверяется побайтно и тем, что отсутствующий файл не
   создаётся.

2. **Трек не рисует того, чего не было.** Молчание трекера дольше 5 минут --
   разрез, а не прямая поперёк поля; стоянка не превращается в клубок
   вершин; плотный трекер не превращает страницу в мегабайт.

3. **Время в движении -- по тем же определениям, что у расчёта.** Пороги
   продублированы (служба Flask не тянет numpy) и закреплены против
   gps/area.py чтением его текста.

4. **Данные карты -- в <script type="application/json">, и имя из Wialon не
   может выйти из него.** Имя контура рисует кто угодно; `</script>` в нём не
   должен закрыть блок.

5. **Спутник -- только с ключом.** Без ключа -- OSM и объяснение на экране;
   ключ, положенный в файл, попадает в адрес плиток и никуда больше.

Запуск:
  python -m unittest tests.test_gps_fact_map -v
"""
import ast
import hashlib
import json
import os
import re
import shutil
import unittest

from datetime import date, datetime, timedelta, timezone

from tests.harness import app, db, reset_db, create_admin, login
from models import (FieldContour, GpsDailyAggregate, GpsWorkPolygon, User)
from gps_collector import storage

import gps_routes
import vs_map

TZ = timezone(timedelta(hours=5))
DAY = date(2026, 7, 27)
UNIT = 3464
MIDNIGHT = int(datetime(2026, 7, 27, tzinfo=TZ).timestamp())

SQUARE = json.dumps({"type": "Polygon", "coordinates": [[
    [64.550, 39.990], [64.560, 39.990], [64.560, 40.000],
    [64.550, 40.000], [64.550, 39.990]]]})


def run(start_s, count, step_s=30, speed=8.0, lon0=64.55, lat0=39.99,
        d=2e-4):
    """Прямая езда: count точек через step_s секунд, ~20 м между точками."""
    return [(MIDNIGHT + start_s + i * step_s, lon0 + i * d, lat0 + i * d * 0.3,
             speed) for i in range(count)]


def aggregate(reason=None, wialon_id=UNIT):
    return GpsDailyAggregate(
        work_date=DAY, wialon_id=wialon_id, points_total=1598,
        points_work=990, track_km=32.1, interval_median_s=30.0,
        sats_median=14.0, motion_gaps=0, lost_seconds=0.0, gps_jumps=0,
        reason=reason, method_version='adaptive-alpha-2026-08-12',
        computed_at=datetime(2026, 7, 28, 3, 0, 0))


def site(number=1, label=None, contour_id=None, geojson=SQUARE):
    return GpsWorkPolygon(
        work_date=DAY, wialon_id=UNIT, site_number=number, area_ha=8.772,
        minutes=317.4, polygon_geojson=geojson, alpha_used_m=10.0,
        pass_spacing_m=5.45, suggested_label='работа', operator_label=label,
        contour_id=contour_id)


class Base(unittest.TestCase):
    def setUp(self):
        reset_db()
        self.admin_id = create_admin()
        with app.app_context():
            User.query.get(self.admin_id).language = 'ru'
            db.session.commit()
        self.folder = app.config['GPS_POINTS_DIR']
        shutil.rmtree(self.folder, ignore_errors=True)
        os.makedirs(self.folder)
        self.key_file = app.config['MAP_ESRI_KEY_FILE']
        if os.path.exists(self.key_file):
            os.remove(self.key_file)
        self.addCleanup(shutil.rmtree, self.folder, True)

    def write_track(self, rows, unit=UNIT):
        storage.write_points(self.folder, [
            (unit, t, lon, lat, speed, 90, 14) for t, lon, lat, speed in rows])

    def page(self, url='/gps/fact?date=2026-07-27&unit=%d' % UNIT):
        client = app.test_client()
        login(client, self.admin_id)
        resp = client.get(url)
        self.assertEqual(resp.status_code, 200, url)
        return resp.get_data(as_text=True)

    def map_data(self, html):
        found = re.search(r'<script type="application/json" id="gps-fact-map">'
                          r'(.*?)</script>', html, re.S)
        return json.loads(found.group(1)) if found else None

    def kinds(self, data):
        return [layer['kind'] for layer in data['layers']]


class ReadOnlyTrack(Base):
    """Пункт 1."""

    def test_only_the_asked_unit_and_local_day_are_read(self):
        self.write_track(run(9 * 3600, 5))
        self.write_track(run(9 * 3600, 7), unit=999)            # сосед
        self.write_track([(MIDNIGHT - 60, 64.5, 39.9, 5.0)])     # прошлые сутки
        self.write_track([(MIDNIGHT + 86400, 64.5, 39.9, 5.0)])  # следующие
        with app.app_context():
            points = gps_routes._day_points(UNIT, DAY)
        self.assertEqual(len(points), 5)
        self.assertEqual(points[0][0], MIDNIGHT + 9 * 3600)

    def test_the_point_file_is_byte_identical_after_the_page(self):
        self.write_track(run(9 * 3600, 50))
        path = storage.points_path(self.folder, '202607')
        with open(path, 'rb') as fh:
            before = hashlib.sha256(fh.read()).hexdigest()
        with app.app_context():
            db.session.add(aggregate())
            db.session.commit()
        self.page()
        with open(path, 'rb') as fh:
            self.assertEqual(hashlib.sha256(fh.read()).hexdigest(), before)

    def test_the_file_is_opened_read_only_and_refuses_writes(self):
        """Запрет записи -- SQLite, а не обещание кода.

        [REASON]: побайтная сверка выше не отличит чтение через обычное
        соединение от чтения через `mode=ro`: код, который только читает,
        файл не меняет ни так, ни так. Поэтому здесь видно само соединение,
        и запись через точно такое же отвергается.
        """
        self.write_track(run(9 * 3600, 5))
        seen = []
        original = gps_routes.sqlite3.connect

        def spy(target, *args, **kwargs):
            seen.append((target, kwargs.get('uri')))
            return original(target, *args, **kwargs)

        gps_routes.sqlite3.connect = spy
        self.addCleanup(setattr, gps_routes.sqlite3, 'connect', original)
        with app.app_context():
            gps_routes._day_points(UNIT, DAY)
        gps_routes.sqlite3.connect = original
        self.assertEqual(len(seen), 1)
        target, uri = seen[0]
        self.assertTrue(uri)
        self.assertTrue(target.endswith('?mode=ro'), target)
        con = original(target, uri=True)
        try:
            with self.assertRaises(Exception) as caught:
                con.execute('DELETE FROM points')
            self.assertIn('readonly', str(caught.exception))
        finally:
            con.close()

    def test_a_missing_file_is_no_track_and_is_not_created(self):
        with app.app_context():
            db.session.add(aggregate())
            db.session.commit()
            self.assertEqual(gps_routes._day_points(UNIT, DAY), [])
        html = self.page()
        self.assertEqual(os.listdir(self.folder), [])
        self.assertIn('Пробег по треку, км', html)      # страница открылась

    def test_a_broken_file_does_not_take_the_page_down(self):
        with open(storage.points_path(self.folder, '202607'), 'w') as fh:
            fh.write('not a database at all')
        with app.app_context():
            db.session.add(aggregate())
            db.session.commit()
            self.assertEqual(gps_routes._day_points(UNIT, DAY), [])
        self.page()


class TrackShape(unittest.TestCase):
    """Пункт 2, без базы и без Flask."""

    def test_silence_longer_than_five_minutes_cuts_the_line(self):
        before = run(9 * 3600, 10)
        after = run(9 * 3600 + 10 * 30 + 301, 10, lon0=64.60)
        segments = gps_routes.track_segments(before + after)
        self.assertEqual([len(s) for s in segments], [10, 10])

    def test_exactly_five_minutes_is_still_one_line(self):
        # Граница как у расчёта: разрыв -- СТРОГО больше 300 с.
        before = run(9 * 3600, 10)
        after = run(9 * 3600 + 9 * 30 + 300, 10, lon0=before[-1][1] + 2e-4)
        self.assertEqual(len(gps_routes.track_segments(before + after)), 1)

    def test_a_parked_machine_is_one_vertex_not_a_tangle(self):
        # Стоянка: 60 сообщений в одной точке с дрожанием в метр.
        parked = [(MIDNIGHT + 9 * 3600 + i * 30, 64.55 + (i % 2) * 1e-5, 39.99,
                   0.0) for i in range(60)]
        drive = run(9 * 3600 + 60 * 30, 10, lon0=64.5501)
        segments = gps_routes.track_segments(parked + drive)
        self.assertEqual(sum(len(s) for s in segments), 1 + 10)

    def test_a_dense_tracker_is_capped_and_keeps_both_ends(self):
        dense = run(0, 20000, step_s=2)
        segments = gps_routes.track_segments(dense, max_vertices=8000)
        total = sum(len(s) for s in segments)
        self.assertLessEqual(total, 8001)
        self.assertGreater(total, 6000)
        self.assertEqual(segments[0][0], [round(dense[0][2], 5),
                                          round(dense[0][1], 5)])
        self.assertEqual(segments[-1][-1], [round(dense[-1][2], 5),
                                            round(dense[-1][1], 5)])

    def test_coordinates_are_lat_lon_for_leaflet(self):
        segment = gps_routes.track_segments(run(9 * 3600, 3))[0]
        self.assertAlmostEqual(segment[0][0], 39.99, places=4)   # широта
        self.assertAlmostEqual(segment[0][1], 64.55, places=4)   # долгота


class TimeInMotion(unittest.TestCase):
    """Пункт 3."""

    def test_moving_time_counts_driving_and_not_parking_or_silence(self):
        drive = run(8 * 3600, 121)                      # 08:00-09:00, 60 мин
        parked = run(8 * 3600 + 120 * 30 + 30, 20, speed=0.0, lon0=64.60)
        # стоянка 09:00:30-09:10:00: скорость 0 в начале каждого промежутка
        resumed = run(9 * 3600 + 10 * 60 + 30, 11, lon0=64.61)
        # 09:10:30-09:15:30 едет, затем трекер молчит 20 минут НА ХОДУ --
        # разрыв, который расчёт считает потерянным временем, а не ездой
        after_silence = run(9 * 3600 + 15 * 60 + 30 + 1200, 11, lon0=64.62)
        summary = gps_routes.track_summary(drive + parked + resumed
                                           + after_silence)
        # 60 мин езды + 30 с (последняя точка езды -> первая стоящая: в начале
        # промежутка машина ехала) + 10 x 30 с + 10 x 30 с. Не входят: стоянка
        # (в начале промежутков скорость 0) и 20 минут молчания на ходу.
        # Отрицательный контроль -- мутация N7 (молчание считается ездой)
        # даёт на 1200 с больше и этим тестом ловится.
        self.assertEqual(summary['moving_s'], 60 * 60 + 30 + 300 + 300)
        self.assertEqual(summary['first_move'].strftime('%H:%M'), '08:00')
        self.assertEqual(summary['last_move'].strftime('%H:%M'), '09:40')

    def test_a_day_without_motion_has_no_time_and_no_window(self):
        summary = gps_routes.track_summary(run(9 * 3600, 30, speed=0.0))
        self.assertEqual(summary['moving_s'], 0)
        self.assertIsNone(summary['first_move'])
        self.assertIsNone(summary['last_move'])

    def test_the_thresholds_are_the_engine_s_own(self):
        """Дубль порогов закреплён против gps/area.py, прочитанного как текст.

        [REASON]: gps/area.py импортирует numpy первой строкой, и в службе
        Flask его не импортировать. Литерал из исходника -- тот же контроль,
        что импорт, без стека.
        """
        path = os.path.join(os.path.dirname(os.path.dirname(
            os.path.abspath(__file__))), 'gps', 'area.py')
        with open(path, encoding='utf-8') as fh:
            tree = ast.parse(fh.read())
        values = {}
        for node in tree.body:
            if isinstance(node, ast.Assign) and len(node.targets) == 1 \
                    and isinstance(node.targets[0], ast.Name) \
                    and isinstance(node.value, ast.Constant):
                values[node.targets[0].id] = node.value.value
        self.assertEqual(gps_routes.MOTION_MIN_KMH, values['SPEED_MIN_KMH'])
        self.assertEqual(gps_routes.MOTION_GAP_S, values['MOTION_GAP_SECONDS'])


class MapOnThePage(Base):
    """Пункт 4 и то, что видит человек."""

    def test_a_published_day_has_track_sites_and_their_contour(self):
        self.write_track(run(9 * 3600, 40))
        with app.app_context():
            contour = FieldContour(source='wialon', external_id='3208',
                                   name='1508 Нурхон Бобохон',
                                   geometry_geojson=SQUARE)
            db.session.add(contour)
            db.session.flush()
            db.session.add(aggregate())
            db.session.add(site(1, contour_id=contour.id))
            db.session.add(site(2, label='проезд'))
            db.session.commit()
        html = self.page()
        data = self.map_data(html)
        self.assertIsNotNone(data)
        # снизу вверх: контур, трек, участки
        self.assertEqual(self.kinds(data), ['outline', 'track', 'area', 'area'])
        self.assertEqual(data['layers'][0]['title'], '1508 Нурхон Бобохон')
        self.assertEqual(sum(len(s) for s in data['layers'][1]['segments']), 40)
        areas = data['layers'][2:]
        self.assertEqual([a['label'] for a in areas], ['1', '2'])
        self.assertEqual([a['tone'] for a in areas], ['primary', 'danger'])
        self.assertIn('8.77 га', areas[0]['title'])
        self.assertIn('проезд', areas[1]['title'])
        # библиотека и компонент подключены из репозитория, не из CDN
        self.assertIn('/static/vendor/leaflet/leaflet.js', html)
        self.assertIn('/static/js/vs-map.js', html)
        self.assertIn('/static/css/vs-map.css', html)
        # ни одного скрипта и стиля извне: всё on-premises
        assets = re.findall(r'(?:src|href)="([^"]+\.(?:js|css)(?:\?[^"]*)?)"', html)
        self.assertTrue(assets)
        self.assertEqual([a for a in assets if not a.startswith('/static/')], [])
        # запасная картинка на месте -- внутри карты
        self.assertIn('vs-map-fallback', html)
        self.assertIn('<polygon', html)

    def test_a_special_machine_has_its_track_on_the_map_and_no_sites(self):
        self.write_track(run(9 * 3600, 25))
        with app.app_context():
            db.session.add(aggregate(reason='spetstekhnika'))
            db.session.add(site(1))                  # остаток до решения 28.09
            db.session.commit()
        data = self.map_data(self.page())
        self.assertEqual(self.kinds(data), ['track'])

    def test_moving_time_is_shown_from_the_track(self):
        self.write_track(run(8 * 3600, 121))        # час езды, 08:00-09:00
        with app.app_context():
            db.session.add(aggregate(reason='spetstekhnika'))
            db.session.commit()
        html = self.page()
        self.assertIn('1 ч 00 мин', html)
        self.assertIn('08:00–09:00', html)

    def test_without_a_track_and_sites_there_is_no_map_and_no_library(self):
        with app.app_context():
            db.session.add(aggregate(reason='net_dvizheniya'))
            db.session.commit()
        html = self.page()
        self.assertIsNone(self.map_data(html))
        self.assertNotIn('leaflet.js', html)
        self.assertNotIn('data-vs-map=', html)

    def test_a_contour_name_cannot_close_the_data_block(self):
        self.write_track(run(9 * 3600, 5))
        evil = '</script><script>window.pwned=1</script>'
        with app.app_context():
            contour = FieldContour(source='wialon', external_id='1',
                                   name=evil, geometry_geojson=SQUARE)
            db.session.add(contour)
            db.session.flush()
            db.session.add(aggregate())
            db.session.add(site(1, contour_id=contour.id))
            db.session.commit()
        html = self.page()
        self.assertNotIn('<script>window.pwned', html)
        data = self.map_data(html)
        self.assertEqual(data['layers'][0]['title'], evil)   # текст цел

    def test_broken_geometry_is_skipped_not_fatal(self):
        self.write_track(run(9 * 3600, 5))
        with app.app_context():
            db.session.add(aggregate())
            db.session.add(site(1, geojson='not json'))
            db.session.add(site(2))
            db.session.commit()
        data = self.map_data(self.page())
        self.assertEqual(self.kinds(data), ['track', 'area'])

    def test_the_uzbek_interface_labels_the_map_in_uzbek(self):
        self.write_track(run(9 * 3600, 5))
        with app.app_context():
            User.query.get(self.admin_id).language = 'uz'
            db.session.add(aggregate())
            db.session.add(site(1, label='работа'))
            db.session.commit()
        html = self.page()
        data = self.map_data(html)
        self.assertEqual(data['base'][0]['title'], 'Харита')
        self.assertEqual(data['layers'][0]['title'], 'Кунлик трек')
        self.assertIn('иш', data['layers'][1]['title'])
        self.assertIn('Спутник қатлами уланмаган', html)
        self.assertNotIn('Спутниковая подложка', html)


class BaseLayers(Base):
    """Пункт 5."""

    def test_without_a_key_the_map_is_osm_and_the_screen_says_why(self):
        self.write_track(run(9 * 3600, 5))
        with app.app_context():
            db.session.add(aggregate())
            db.session.commit()
        html = self.page()
        data = self.map_data(html)
        self.assertEqual([b['key'] for b in data['base']], ['map'])
        self.assertIn('tile.openstreetmap.org', data['base'][0]['url'])
        self.assertIn('OpenStreetMap', data['base'][0]['attribution'])
        self.assertIn('Спутниковая подложка не подключена', html)
        self.assertIn('esri_api_key.txt', html)       # администратору -- как

    def test_with_a_key_the_satellite_comes_first_and_carries_it(self):
        key = 'AAPK-test-key-0123456789'
        with open(self.key_file, 'w', encoding='utf-8-sig') as fh:
            fh.write('\n' + key + '\n')
        self.write_track(run(9 * 3600, 5))
        with app.app_context():
            db.session.add(aggregate())
            db.session.commit()
        html = self.page()
        data = self.map_data(html)
        self.assertEqual([b['key'] for b in data['base']], ['satellite', 'map'])
        self.assertTrue(data['base'][0]['url'].startswith(
            'https://ibasemaps-api.arcgis.com/'))
        self.assertTrue(data['base'][0]['url'].endswith('token=' + key))
        self.assertIn('Esri', data['base'][0]['attribution'])
        self.assertNotIn('Спутниковая подложка не подключена', html)
        # ключ -- только в адресе плиток внутри блока данных карты
        block = re.search(r'id="gps-fact-map">(.*?)</script>', html, re.S)
        self.assertEqual(block.group(1).count(key), 1)
        self.assertEqual(html.count(key), 1)

    def test_a_key_file_with_garbage_does_not_turn_the_satellite_on(self):
        for garbage in ('два слова', 'key with spaces', '', '   \n  '):
            with self.subTest(garbage=garbage):
                with open(self.key_file, 'w', encoding='utf-8') as fh:
                    fh.write(garbage)
                self.assertEqual(vs_map.esri_key(self.key_file), '')
                self.assertEqual([b['key'] for b in vs_map.base_layers(
                    True, self.key_file)], ['map'])

    def test_no_file_at_all_is_no_key(self):
        self.assertEqual(vs_map.esri_key(self.key_file + '.missing'), '')


if __name__ == '__main__':
    unittest.main()
