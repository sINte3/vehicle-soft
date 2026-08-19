# -*- coding: utf-8 -*-
"""DRONE-LANDS-001: сшивка вылетов с контурами по рамке.

Проверяется не «работает ли скрипт», а способен ли он различить два случая.
Поэтому у каждой проверки есть отрицательный контроль, а два инварианта
проверяются отдельно, потому что ошибка в них невидима в итоговых числах:

  * вылет, попавший сразу в две рамки, НЕ раздаётся молча одной из них.
    Смежные поля перекрываются рамками всегда; тихое присвоение дало бы
    красивую стопроцентную сшивку и неверные гектары у обоих заказчиков;
  * ведро по градусу широты, которым срезается перебор, не имеет права дать
    ложный промах на контуре, лежащем на границе градуса.

Скрипт открывает базу в режиме только чтения, поэтому здесь же проверяется,
что запись через его соединение невозможна.
"""

import os
import sqlite3
import sys
import tempfile
import unittest

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if REPO_ROOT not in sys.path:
    sys.path.insert(0, REPO_ROOT)

from tools import drone_contours_report as report  # noqa: E402

SCHEMA = """
CREATE TABLE field_contours (
    id INTEGER PRIMARY KEY AUTOINCREMENT, source VARCHAR(20) NOT NULL,
    external_id VARCHAR(100), name VARCHAR(300) NOT NULL, area_ha FLOAT,
    serial_number VARCHAR(50), bbox_min_lat FLOAT, bbox_min_lng FLOAT,
    bbox_max_lat FLOAT, bbox_max_lng FLOAT, center_lat FLOAT, center_lng FLOAT,
    source_created_at DATETIME);
CREATE TABLE drone_units (id INTEGER PRIMARY KEY, number INTEGER);
CREATE TABLE drone_operators (id INTEGER PRIMARY KEY, full_name VARCHAR(200));
CREATE TABLE drone_operator_assignments (
    id INTEGER PRIMARY KEY, drone_unit_id INTEGER, operator_id INTEGER,
    date_from DATE, date_to DATE);
CREATE TABLE drone_flights (
    id INTEGER PRIMARY KEY, drone_unit_id INTEGER, started_at DATETIME,
    area_ha FLOAT, lat FLOAT, lng FLOAT, location_text VARCHAR(500));
"""


class Fixture(object):
    """Синтетическая база: две машины, два оператора, контуры по заказу."""

    def __init__(self):
        self.dir = tempfile.mkdtemp(prefix='contours_')
        self.path = os.path.join(self.dir, 'transport.db')
        con = sqlite3.connect(self.path)
        con.executescript(SCHEMA)
        con.execute('INSERT INTO drone_units (id, number) VALUES (1, 4), '
                    '(2, 3)')
        con.execute("INSERT INTO drone_operators (id, full_name) VALUES "
                    "(1, 'Анваров Усмон'), (2, 'Холмуродов Шахзод')")
        con.commit()
        self.con = con
        self._flight_id = 0

    def contour(self, name, min_lat, min_lng, max_lat, max_lng, area_ha=3.0,
                serial='P1'):
        has_box = None not in (min_lat, min_lng, max_lat, max_lng)
        center_lat = (min_lat + max_lat) / 2 if has_box else None
        center_lng = (min_lng + max_lng) / 2 if has_box else None
        self.con.execute(
            'INSERT INTO field_contours (source, external_id, name, area_ha, '
            'serial_number, bbox_min_lat, bbox_min_lng, bbox_max_lat, '
            'bbox_max_lng, center_lat, center_lng) '
            "VALUES ('dji', ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (name, name, area_ha, serial, min_lat, min_lng, max_lat, max_lng,
             center_lat, center_lng))
        self.con.commit()

    def flight(self, started_at, unit_id=1, area_ha=1.0, lat=None, lng=None,
               address='Bukhara Region'):
        self._flight_id += 1
        self.con.execute(
            'INSERT INTO drone_flights (id, drone_unit_id, started_at, '
            'area_ha, lat, lng, location_text) VALUES (?, ?, ?, ?, ?, ?, ?)',
            (self._flight_id, unit_id, started_at, area_ha, lat, lng, address))
        self.con.commit()

    def assign(self, unit_id, operator_id, date_from, date_to):
        self.con.execute(
            'INSERT INTO drone_operator_assignments (drone_unit_id, '
            'operator_id, date_from, date_to) VALUES (?, ?, ?, ?)',
            (unit_id, operator_id, date_from, date_to))
        self.con.commit()

    def run(self, date_from='2025-09-01', date_to='2025-09-30'):
        conn = report.open_readonly(self.path)
        try:
            return report.build(conn, date_from, date_to)
        finally:
            conn.close()

    def close(self):
        self.con.close()
        import shutil
        shutil.rmtree(self.dir, ignore_errors=True)


class Base(unittest.TestCase):

    def setUp(self):
        self.fx = Fixture()

    def tearDown(self):
        self.fx.close()


class Matching(Base):
    """Проверка 1: точка внутри рамки -- попадание, снаружи -- нет."""

    def test_1_point_inside_the_box_matches(self):
        self.fx.contour('Аваз Исматов 1', 39.97, 64.36, 39.98, 64.37)
        self.fx.flight('2025-09-20T05:00:00', lat=39.975, lng=64.365,
                       area_ha=2.5)
        stats, per_contour, no_contour, disputed, _unused = self.fx.run()
        self.assertEqual(stats['matched_one'], 1)
        self.assertEqual(stats['matched_none'], 0)
        self.assertEqual(no_contour, [])
        self.assertEqual(disputed, [])
        self.assertAlmostEqual(list(per_contour.values())[0]['ha'], 2.5)

    def test_1a_negative_a_point_outside_matches_nothing(self):
        """Тот же контур, точка за рамкой -- иначе проверка выше прошла бы
        при любой реализации, которая всегда говорит «попал»."""
        self.fx.contour('Аваз Исматов 1', 39.97, 64.36, 39.98, 64.37)
        self.fx.flight('2025-09-20T05:00:00', lat=39.99, lng=64.365)
        stats, per_contour, no_contour, _disputed, _unused = self.fx.run()
        self.assertEqual(stats['matched_one'], 0)
        self.assertEqual(stats['matched_none'], 1)
        self.assertEqual(per_contour, {})
        self.assertEqual(no_contour[0][-1],
                         'ни одна рамка не накрывает точку')

    def test_1b_the_edge_of_the_box_counts_as_inside(self):
        self.fx.contour('Край', 39.97, 64.36, 39.98, 64.37)
        self.fx.flight('2025-09-20T05:00:00', lat=39.97, lng=64.36)
        stats, _pc, _nc, _d, _u = self.fx.run()
        self.assertEqual(stats['matched_one'], 1)

    def test_1c_a_flight_without_coordinates_is_counted_apart(self):
        self.fx.contour('Поле', 39.97, 64.36, 39.98, 64.37)
        self.fx.flight('2025-09-20T05:00:00', lat=None, lng=None)
        stats, _pc, no_contour, _d, _u = self.fx.run()
        self.assertEqual(stats['flights_without_coords'], 1)
        self.assertEqual(no_contour[0][-1], 'нет координат')

    def test_1d_a_contour_without_a_box_never_participates(self):
        self.fx.contour('Без рамки', None, None, None, None)
        self.fx.flight('2025-09-20T05:00:00', lat=39.975, lng=64.365)
        stats, _pc, _nc, _d, _u = self.fx.run()
        self.assertEqual(stats['contours_total'], 1)
        self.assertEqual(stats['contours_usable'], 0)
        self.assertEqual(stats['contours_without_box'], 1)
        self.assertEqual(stats['matched_none'], 1)


class Disputed(Base):
    """Проверка 2: перекрытие рамок видно, а не сглажено."""

    def setUp(self):
        Base.setUp(self)
        # Два смежных поля с перекрывающимися рамками -- обычное дело.
        self.fx.contour('Розия Бекова', 39.970, 64.360, 39.980, 64.370)
        self.fx.contour('Ғиждувон ПТЗ', 39.975, 64.365, 39.985, 64.375)

    def test_2_a_point_in_two_boxes_is_reported_as_disputed(self):
        self.fx.flight('2025-09-20T05:00:00', lat=39.9775, lng=64.3675,
                       area_ha=4.0)
        stats, _pc, _nc, disputed, _u = self.fx.run()
        self.assertEqual(stats['matched_many'], 1)
        self.assertEqual(stats['matched_one'], 0)
        self.assertAlmostEqual(stats['ha_matched_many'], 4.0)
        self.assertAlmostEqual(stats['ha_matched_one'], 0.0)
        self.assertEqual(disputed[0][6], 2)
        self.assertIn('Розия Бекова', disputed[0][7])
        self.assertIn('Ғиждувон ПТЗ', disputed[0][7])

    def test_2a_negative_a_point_in_only_one_box_is_not_disputed(self):
        """Тот же набор контуров, точка вне зоны перекрытия."""
        self.fx.flight('2025-09-20T05:00:00', lat=39.9715, lng=64.3615,
                       area_ha=4.0)
        stats, _pc, _nc, disputed, _u = self.fx.run()
        self.assertEqual(stats['matched_one'], 1)
        self.assertEqual(stats['matched_many'], 0)
        self.assertEqual(disputed, [])

    def test_2b_disputed_hectares_are_not_counted_as_unambiguous(self):
        """[REASON]: полнота сшивки оценивается по однозначным попаданиям.
        Если спорные гектары попадут в ту же корзину, отчёт покажет 100 %
        на данных, где половина полей перепутана."""
        self.fx.flight('2025-09-20T05:00:00', lat=39.9775, lng=64.3675,
                       area_ha=4.0)
        self.fx.flight('2025-09-20T06:00:00', lat=39.9715, lng=64.3615,
                       area_ha=6.0)
        stats, _pc, _nc, _d, _u = self.fx.run()
        self.assertAlmostEqual(stats['ha_total'], 10.0)
        self.assertAlmostEqual(stats['ha_matched_one'], 6.0)
        self.assertAlmostEqual(stats['ha_matched_many'], 4.0)

    def test_2c_the_roll_up_still_names_one_contour_and_marks_the_dispute(self):
        self.fx.flight('2025-09-20T05:00:00', lat=39.9775, lng=64.3675,
                       area_ha=4.0)
        _stats, per_contour, _nc, _d, _u = self.fx.run()
        self.assertEqual(len(per_contour), 1)
        bucket = list(per_contour.values())[0]
        self.assertEqual(bucket['disputed'], 1)


class LatitudeBuckets(Base):
    """Проверка 3: ускорение перебора не имеет права терять попадания."""

    def test_3_a_contour_spanning_a_whole_degree_still_matches(self):
        """[REASON]: контур кладётся во ВСЕ вёдра, которые задевает. Если бы
        он попадал только в ведро своего минимума, точка над границей градуса
        не нашла бы его -- и промах выглядел бы как «поля нет в справочнике»."""
        self.fx.contour('Через градус', 39.995, 64.36, 40.005, 64.37)
        self.fx.flight('2025-09-20T05:00:00', lat=40.001, lng=64.365)
        stats, _pc, _nc, _d, _u = self.fx.run()
        self.assertEqual(stats['matched_one'], 1)

    def test_3a_negative_a_point_beyond_that_contour_still_misses(self):
        self.fx.contour('Через градус', 39.995, 64.36, 40.005, 64.37)
        self.fx.flight('2025-09-20T05:00:00', lat=40.05, lng=64.365)
        stats, _pc, _nc, _d, _u = self.fx.run()
        self.assertEqual(stats['matched_none'], 1)

    def test_3b_bucketing_agrees_with_a_brute_force_scan(self):
        """Отрицательный контроль на само ускорение: тот же ответ, что и
        перебор всех контуров подряд, на сетке точек."""
        for index in range(12):
            base = 39.5 + index * 0.1
            self.fx.contour('Поле %d' % index, base, 64.0, base + 0.05, 64.05)
        conn = report.open_readonly(self.fx.path)
        try:
            contours, _total, _no_box = report.load_contours(conn)
        finally:
            conn.close()
        buckets = report.index_by_latitude(contours)
        for step in range(60):
            lat = 39.45 + step * 0.02
            lng = 64.02
            fast = {c.id for c in report.match(buckets.get(int(lat // 1), ()),
                                               lat, lng)}
            slow = {c.id for c in report.match(contours, lat, lng)}
            self.assertEqual(fast, slow, 'lat=%r' % lat)


class Days(Base):
    """Проверка 4: день считается в UTC+5, а не в UTC."""

    def test_4_an_evening_flight_belongs_to_the_local_day(self):
        """19:30 UTC 19 сентября -- это 00:30 20 сентября в Ташкенте."""
        self.fx.contour('Поле', 39.97, 64.36, 39.98, 64.37)
        self.fx.flight('2025-09-19T19:30:00', lat=39.975, lng=64.365)
        stats, per_contour, _nc, _d, _u = self.fx.run('2025-09-20',
                                                      '2025-09-20')
        self.assertEqual(stats['flights'], 1)
        self.assertEqual(list(per_contour.values())[0]['days'], {'2025-09-20'})

    def test_4a_negative_the_same_flight_is_outside_the_previous_day(self):
        self.fx.contour('Поле', 39.97, 64.36, 39.98, 64.37)
        self.fx.flight('2025-09-19T19:30:00', lat=39.975, lng=64.365)
        stats, _pc, _nc, _d, _u = self.fx.run('2025-09-19', '2025-09-19')
        self.assertEqual(stats['flights'], 0)


class Operators(Base):
    """Проверка 5: оператор выводится назначением, спор -- не выводится."""

    def test_5_a_single_assignment_names_the_operator(self):
        self.fx.contour('Поле', 39.97, 64.36, 39.98, 64.37)
        self.fx.assign(1, 1, '2025-09-17', '2025-09-29')
        self.fx.flight('2025-09-20T05:00:00', unit_id=1, lat=39.975,
                       lng=64.365)
        _stats, per_contour, _nc, _d, _u = self.fx.run()
        self.assertEqual(list(per_contour.values())[0]['operators'],
                         {'Анваров Усмон'})

    def test_5a_negative_two_overlapping_assignments_name_nobody(self):
        """[REASON]: та же гвардия, что в отчётах приложения -- вылет,
        покрытый двумя назначениями, не отдаётся одному из них молча."""
        self.fx.contour('Поле', 39.97, 64.36, 39.98, 64.37)
        self.fx.assign(1, 1, '2025-09-17', '2025-09-29')
        self.fx.assign(1, 2, '2025-09-18', '2025-09-25')
        self.fx.flight('2025-09-20T05:00:00', unit_id=1, lat=39.975,
                       lng=64.365)
        _stats, per_contour, _nc, _d, _u = self.fx.run()
        self.assertEqual(list(per_contour.values())[0]['operators'],
                         {'не определён'})

    def test_5b_a_day_outside_the_assignment_names_nobody(self):
        self.fx.contour('Поле', 39.97, 64.36, 39.98, 64.37)
        self.fx.assign(1, 1, '2025-09-01', '2025-09-05')
        self.fx.flight('2025-09-20T05:00:00', unit_id=1, lat=39.975,
                       lng=64.365)
        _stats, per_contour, _nc, _d, _u = self.fx.run()
        self.assertEqual(list(per_contour.values())[0]['operators'],
                         {'не определён'})


class ReadOnly(Base):
    """Проверка 6: скрипт не может изменить базу, даже если захочет."""

    def test_6_the_connection_refuses_a_write(self):
        conn = report.open_readonly(self.fx.path)
        try:
            with self.assertRaises(sqlite3.OperationalError):
                conn.execute("INSERT INTO drone_units (id, number) "
                             "VALUES (99, 99)")
        finally:
            conn.close()

    def test_6a_negative_the_same_statement_works_on_a_writable_handle(self):
        """Иначе проверка выше прошла бы и на сломанном INSERT."""
        con = sqlite3.connect(self.fx.path)
        try:
            con.execute("INSERT INTO drone_units (id, number) VALUES (99, 99)")
            con.commit()
        finally:
            con.close()

    def test_6b_a_missing_database_exits_2_and_creates_no_file(self):
        missing = os.path.join(self.fx.dir, 'nope.db')
        with self.assertRaises(SystemExit) as caught:
            report.open_readonly(missing)
        self.assertEqual(caught.exception.code, 2)
        self.assertFalse(os.path.exists(missing))


class Unused(Base):
    """Проверка 7: контуры без вылетов за период перечислены, а не потеряны."""

    def test_7_a_contour_with_no_flights_is_listed(self):
        self.fx.contour('Сентябрьское', 39.97, 64.36, 39.98, 64.37)
        self.fx.contour('Никем не тронутое', 41.0, 66.0, 41.01, 66.01)
        self.fx.flight('2025-09-20T05:00:00', lat=39.975, lng=64.365)
        _stats, _pc, _nc, _d, unused = self.fx.run()
        self.assertEqual([c.name for c in unused], ['Никем не тронутое'])

    def test_7a_negative_a_contour_that_was_flown_is_not_listed(self):
        self.fx.contour('Сентябрьское', 39.97, 64.36, 39.98, 64.37)
        self.fx.flight('2025-09-20T05:00:00', lat=39.975, lng=64.365)
        _stats, _pc, _nc, _d, unused = self.fx.run()
        self.assertEqual(unused, [])


if __name__ == '__main__':
    unittest.main()
