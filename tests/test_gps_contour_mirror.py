# -*- coding: utf-8 -*-
"""GPS-4: зеркало контуров Wialon в field_contours.

Что проверяется и почему именно это:

1. **Порядок точек.** Сервер отдаёт точки то списком, то словарём с числовыми
   ключами. Сортировка ключей как строк ставит «10» перед «2» и выворачивает
   полигон наизнанку — поле остаётся «полигоном», площадь считается, и никто
   не узнает, что форма чужая.

2. **Ничего не удаляется.** Контур, пропавший из Wialon, обязан остаться у
   нас как был: на него могут ссылаться выставленные счета, а удаления в
   Wialon к нам не каскадируют (решение G8).

3. **Повторный прогон не плодит строк.** Уникальность (source, external_id)
   есть в схеме, но проверять надо поведение: вторая заливка обязана быть
   обновлением, а не вставкой.

4. **Дубли имён видны.** Справочник содержит их по конструкции, и это не
   ошибка импорта — по имени контур определять нельзя вовсе.

5. **Пустой ответ не выглядит пустым справочником.** Список зон непустой, а
   данных нет — однажды это дало файл на два байта, который приняли за
   успешную выгрузку.

Сети не требуется: сервер заменён консервами, код скрипта выполняется целиком.

Запуск:
  python -m unittest tests.test_gps_contour_mirror -v
"""
import io
import json
import os
import sqlite3
import sys
import tempfile
import unittest
import urllib.parse
import urllib.request

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if REPO_ROOT not in sys.path:
    sys.path.insert(0, REPO_ROOT)

import tools.gps_mirror_contours as mirror  # noqa: E402

RESOURCE = 285

FIELD_CONTOURS_DDL = (
    'CREATE TABLE field_contours (id INTEGER PRIMARY KEY AUTOINCREMENT, '
    'source VARCHAR(20) NOT NULL, external_id VARCHAR(100), '
    'name VARCHAR(300) NOT NULL, name_uz VARCHAR(300), '
    'geometry_geojson TEXT, area_ha FLOAT, customer_id INTEGER, '
    'season_year INTEGER, is_active BOOLEAN DEFAULT 1, '
    'created_at DATETIME, updated_at DATETIME, '
    'CONSTRAINT uq_field_contours_source_external UNIQUE (source, external_id))')

# Квадрат рядом с Бухарой. Точки идут по кругу; порядок и есть форма.
SQUARE = [{'x': 64.550, 'y': 39.990}, {'x': 64.560, 'y': 39.990},
          {'x': 64.560, 'y': 40.000}, {'x': 64.550, 'y': 40.000}]


def zone(zone_id, name, points=None, kind=2):
    return {'id': zone_id, 'n': name, 't': kind,
            'p': SQUARE if points is None else points}


class FakeResponse:
    def __init__(self, payload):
        self._raw = json.dumps(payload).encode('utf-8')

    def read(self):
        return self._raw

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


class FakeServer:
    def __init__(self, zones, listed_ids=None):
        self.zones = zones
        self.listed_ids = (listed_ids if listed_ids is not None
                           else [z['id'] for z in zones])
        self.asked = []

    def urlopen(self, request, timeout=None):
        query = urllib.parse.parse_qs(request.data.decode('utf-8'))
        svc = query['svc'][0]
        params = json.loads(query['params'][0])
        if svc == 'token/login':
            return FakeResponse({'eid': 'SESSION'})
        if svc == 'core/search_items':
            return FakeResponse({'items': [
                {'id': RESOURCE,
                 'zl': {str(i): {} for i in self.listed_ids}},
                {'id': 170, 'zl': {}}]})
        if svc == 'resource/get_zone_data':
            self.asked.append(list(params.get('col') or []))
            wanted = set(params.get('col') or [])
            return FakeResponse([z for z in self.zones if z['id'] in wanted])
        return FakeResponse({})


class Installed:
    def __init__(self, server):
        self.server = server

    def __enter__(self):
        self.original = urllib.request.urlopen
        urllib.request.urlopen = self.server.urlopen
        return self.server

    def __exit__(self, *exc):
        urllib.request.urlopen = self.original
        return False


class Geometry(unittest.TestCase):
    """Пункт 1."""

    def test_a_point_dict_keeps_the_order_of_the_field(self):
        as_list = mirror.ring_from_zone(zone(1, 'x', SQUARE))
        as_dict = mirror.ring_from_zone(zone(1, 'x', {
            str(i): point for i, point in enumerate(SQUARE)}))
        self.assertEqual(as_list, as_dict)

    def test_ten_does_not_come_before_two(self):
        # Одиннадцать точек: ключи «10» и «11» при строковой сортировке
        # встают между «1» и «2», и кольцо выворачивается.
        points = [{'x': 64.55 + i * 0.001, 'y': 39.99 + (i % 3) * 0.001}
                  for i in range(11)]
        ordered = mirror.ring_from_zone(zone(1, 'x', points))
        as_dict = mirror.ring_from_zone(zone(1, 'x', {
            str(i): point for i, point in enumerate(points)}))
        self.assertEqual(ordered, as_dict)
        # и это отличается от того, что дала бы строковая сортировка
        wrong = [[float(points[int(k)]['x']), float(points[int(k)]['y'])]
                 for k in sorted(str(i) for i in range(11))]
        wrong.append(list(wrong[0]))
        self.assertNotEqual(ordered, wrong)

    def test_the_ring_is_closed(self):
        ring = mirror.ring_from_zone(zone(1, 'x'))
        self.assertEqual(ring[0], ring[-1])
        self.assertEqual(len(ring), 5)

    def test_a_circle_or_a_line_is_stored_without_invented_geometry(self):
        self.assertIsNone(mirror.ring_from_zone(zone(1, 'krug', kind=3)))
        self.assertIsNone(mirror.ring_from_zone(zone(1, 'liniya', kind=1)))
        self.assertIsNone(mirror.geometry_geojson(zone(1, 'krug', kind=3)))

    def test_a_polygon_of_two_points_is_not_a_polygon(self):
        self.assertIsNone(mirror.ring_from_zone(zone(1, 'x', SQUARE[:2])))


class Mirror(unittest.TestCase):
    def setUp(self):
        self.folder = tempfile.mkdtemp()
        self.db = os.path.join(self.folder, 'transport.db')
        con = sqlite3.connect(self.db)
        con.execute(FIELD_CONTOURS_DDL)
        con.commit()
        con.close()
        os.environ['WIALON_TOKEN'] = 'test-token-not-a-real-one'

    def run_mirror(self, server, *extra):
        out = io.StringIO()
        saved = sys.stdout
        sys.stdout = out
        try:
            with Installed(server):
                code = mirror.main(['--db', self.db, '--pause', '0',
                                    '--duplicates-out',
                                    os.path.join(self.folder, 'dup.csv')]
                                   + list(extra))
        finally:
            sys.stdout = saved
        return code, out.getvalue()

    def rows(self):
        con = sqlite3.connect(self.db)
        try:
            con.row_factory = sqlite3.Row
            return {r['external_id']: dict(r) for r in con.execute(
                'SELECT * FROM field_contours')}
        finally:
            con.close()

    def test_a_first_run_fills_the_directory(self):
        server = FakeServer([zone(3207, '1508 Нурхон Бобохон'),
                             zone(3208, '1508 Нурхон Бобохон')])
        code, log = self.run_mirror(server)
        self.assertEqual(code, 0, log)
        rows = self.rows()
        self.assertEqual(sorted(rows), ['3207', '3208'])
        self.assertEqual(rows['3208']['source'], 'wialon')
        self.assertEqual(rows['3208']['name'], '1508 Нурхон Бобохон')
        self.assertEqual(json.loads(rows['3208']['geometry_geojson'])['type'],
                         'Polygon')
        # площадь намеренно не считается: её считает метод, а не этот скрипт
        self.assertIsNone(rows['3208']['area_ha'])
        self.assertEqual(rows['3208']['is_active'], 1)

    def test_a_second_run_updates_and_does_not_duplicate(self):
        first = FakeServer([zone(3208, 'Старое имя')])
        self.run_mirror(first)
        second = FakeServer([zone(3208, 'Новое имя')])
        code, log = self.run_mirror(second)
        self.assertEqual(code, 0, log)
        rows = self.rows()
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows['3208']['name'], 'Новое имя')

    def test_an_unchanged_zone_is_not_rewritten(self):
        server = FakeServer([zone(3208, 'Имя')])
        self.run_mirror(server)
        before = self.rows()['3208']['updated_at']
        _, log = self.run_mirror(FakeServer([zone(3208, 'Имя')]))
        self.assertIn('to add: 0 | to update: 0', log)
        self.assertEqual(self.rows()['3208']['updated_at'], before)

    def test_a_zone_gone_from_wialon_stays_in_our_directory(self):
        # Пункт 2: удаления в Wialon к нам не каскадируют (G8).
        self.run_mirror(FakeServer([zone(3207, 'A'), zone(3208, 'B')]))
        code, log = self.run_mirror(FakeServer([zone(3207, 'A')]))
        self.assertEqual(code, 0, log)
        rows = self.rows()
        self.assertIn('3208', rows)
        self.assertEqual(rows['3208']['is_active'], 1)
        self.assertIn('no longer in Wialon: 1', log)
        self.assertIn('LEFT AS IS', log)

    def test_repeated_names_are_counted_and_listed(self):
        server = FakeServer([zone(3207, '1508 Нурхон Бобохон'),
                             zone(3208, '1508 Нурхон Бобохон'),
                             zone(4305, 'Бобуршох')])
        code, log = self.run_mirror(server)
        self.assertEqual(code, 0, log)
        self.assertIn('names repeated in Wialon: 1 (2 zones)', log)
        with open(os.path.join(self.folder, 'dup.csv'),
                  encoding='utf-8-sig') as fh:
            body = fh.read()
        self.assertIn('1508 Нурхон Бобохон', body)
        self.assertIn('3207 3208', body)

    def test_a_dry_run_writes_nothing(self):
        code, log = self.run_mirror(FakeServer([zone(3208, 'A')]), '--dry-run')
        self.assertEqual(code, 0, log)
        self.assertIn('dry run: nothing was written', log)
        self.assertEqual(self.rows(), {})

    def test_zones_listed_but_not_downloaded_is_a_failure_not_an_empty_book(self):
        # Пункт 5: именно так однажды вышел файл на два байта.
        server = FakeServer([], listed_ids=[3207, 3208])
        code, log = self.run_mirror(server)
        self.assertEqual(code, 1)
        self.assertIn('NOT treating', log)
        self.assertEqual(self.rows(), {})

    def test_an_empty_zone_list_stops_before_writing(self):
        code, log = self.run_mirror(FakeServer([], listed_ids=[]))
        self.assertEqual(code, 1)
        self.assertEqual(self.rows(), {})

    def test_a_missing_database_is_refused_and_not_created(self):
        missing = os.path.join(self.folder, 'nope.db')
        saved, sys.stderr = sys.stderr, io.StringIO()
        try:
            code = mirror.main(['--db', missing])
        finally:
            sys.stderr = saved
        self.assertEqual(code, 2)
        self.assertFalse(os.path.exists(missing))

    def test_chunking_asks_for_every_id_exactly_once(self):
        zones = [zone(3200 + i, 'zona %d' % i) for i in range(7)]
        server = FakeServer(zones)
        code, log = self.run_mirror(server, '--chunk', '3')
        self.assertEqual(code, 0, log)
        self.assertEqual([len(batch) for batch in server.asked], [3, 3, 1])
        asked = [i for batch in server.asked for i in batch]
        self.assertEqual(sorted(asked), sorted(z['id'] for z in zones))
        self.assertEqual(len(self.rows()), 7)


if __name__ == '__main__':
    unittest.main()
