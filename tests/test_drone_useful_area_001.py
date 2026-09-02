# -*- coding: utf-8 -*-
"""DRONE-USEFUL-AREA-001: полезная площадь от приёма маршрута до страницы.

Здесь три вещи, которые проект просит доказывать отдельно и числами:

1. **Исходная площадь DJI не изменилась.** `drone_flights.area_ha` читается
   ЧЕРЕЗ stdlib `sqlite3`, до и после приёма и пересчёта, и сравнивается на
   уровне SQLite-значения -- не через ORM, который мог бы вернуть своё
   представление вместо хранимого.
2. **Неполные данные не суммируются.** Работа со статусом, отличным от
   `READY_ESTIMATE`, в карточку итога не попадает вовсе, а её собственное
   число равно `None`, а не нулю.
3. **Перекрытие, холостой ход и движение вне контура исключены.** Проверяется
   не «функция что-то вернула», а НАЗВАННОЕ ЧИСЛО на синтетической геометрии,
   у которой площадь известна из школьной формулы.

Каждая критическая проверка различает исправный и сломанный код: рядом с ней
стоит отрицательный контроль -- второе число, которое получилось бы, если бы
правило убрали. Тест, остающийся зелёным после удаления проверяемого правила,
проверкой не является.

ГЕОМЕТРИЯ ЗДЕСЬ СИНТЕТИЧЕСКАЯ. Ни одной настоящей координаты, ни одного
настоящего идентификатора вылета, ни одного UUID из кабинета. Поле -- квадрат
со стороной 200 м вокруг точки, выбранной как круглое число; вылеты
пронумерованы с 900001. Всё помечено SYNTHETIC.
"""

import json
import re
import sqlite3
import unittest

from datetime import date, datetime

from tests.harness import (app, reset_db, create_admin, create_org, login,
                           TEST_DB_PATH)

from models import (db, DroneCoverageWork, DroneFlight, DroneFlightRoute,
                    DroneUnit, FieldContour, User)

import drone_coverage_recalc as recalc
import drone_useful_area as ua

TOKEN = 'SYNTHETIC-route-sync-token-NOT-REAL'

# Центр синтетического поля. Круглые числа: это НЕ координата настоящего поля.
LAT0 = 39.700000
LON0 = 64.400000
M_PER_DEG_LAT = 111320.0
# cos(39.7 deg) с точностью, достаточной для метровой сетки.
M_PER_DEG_LON = 111320.0 * 0.7679

WORK_DAY = date(2026, 6, 5)


def at(east_m, north_m):
    """Точка в метрах от центра синтетического поля -> [lat, lng]."""
    return [LAT0 + north_m / M_PER_DEG_LAT, LON0 + east_m / M_PER_DEG_LON]


def square(half_m, east_m=0.0, north_m=0.0):
    """Квадратный полигон GeoJSON со стороной 2*half_m."""
    corners = [at(east_m - half_m, north_m - half_m),
               at(east_m + half_m, north_m - half_m),
               at(east_m + half_m, north_m + half_m),
               at(east_m - half_m, north_m + half_m),
               at(east_m - half_m, north_m - half_m)]
    return {'type': 'Polygon',
            'coordinates': [[[point[1], point[0]] for point in corners]]}


def pass_line(east_m, north_from, north_to, step_m=5.0):
    """Прямой проход вдоль меридиана. Шаг 5 м -- меньше gap_m и больше
    min_step_m, поэтому отрезки классифицируются как рабочие."""
    points = []
    north = north_from
    while north <= north_to + 1e-9:
        points.append(at(east_m, north))
        north += step_m
    return points


def ferry_line():
    """Перелёт: шаги по 90 м, то есть длиннее gap_m (60 м).

    [REASON]: разрыв записи НИКОГДА не становится полосой. Между двумя
    точками в девяноста метрах друг от друга дрон, возможно, и работал, но МЫ
    этого не знаем.
    """
    return [at(-90.0, -90.0), at(-90.0, 0.0), at(-90.0, 90.0)]


def route_body(flight_id, points, width=8.0, recorded=True):
    """Тело маршрута в той же форме, в какой его строит сборщик."""
    body = {
        'dji_flight_id': flight_id,
        'data_type': 'simplified',
        'collector_version': 'routes-1',
        'decoder_version': 'route-decode-2',
        'points': points,
        'point_count': len(points),
        'takeoff': None,
        'spray_width_m': width,
        'spray_width_recorded': recorded,
        'point_shape_census': {'route_points_total': len(points),
                               'route_points_with_unknown_fields': 0},
    }
    return body


class Base(unittest.TestCase):
    """Общая синтетическая площадка: одна машина, один день, одно поле."""

    contours = ('SYNTHETIC-CONTOUR-A',)

    def setUp(self):
        reset_db()
        app.config['DRONE_API_TOKEN'] = TOKEN
        self.client = app.test_client()
        org_id = create_org('SYNTHETIC Org')
        with app.app_context():
            unit = DroneUnit(number=6, organization_id=org_id)
            db.session.add(unit)
            db.session.flush()
            self.unit_id = unit.id
            db.session.commit()
        self.flight_ids = []

    def add_flight(self, flight_id, area_ha=2.0, minute=0, unit=True):
        with app.app_context():
            db.session.add(DroneFlight(
                dji_flight_id=flight_id,
                drone_unit_id=self.unit_id if unit else None,
                nickname_raw='SYNTHETIC-NICK',
                started_at=datetime(2026, 6, 5, 3, minute),
                area_ha=area_ha, raw_json='{}'))
            db.session.commit()
        self.flight_ids.append(flight_id)
        return flight_id

    def add_contour(self, external_id, geojson, name=None):
        ring = geojson['coordinates'][0]
        lats = [point[1] for point in ring]
        lngs = [point[0] for point in ring]
        with app.app_context():
            db.session.add(FieldContour(
                source='dji', external_id=external_id,
                name=name or ('SYNTHETIC field %s' % external_id),
                geometry_geojson=json.dumps(geojson), is_active=True,
                bbox_min_lat=min(lats), bbox_max_lat=max(lats),
                bbox_min_lng=min(lngs), bbox_max_lng=max(lngs)))
            db.session.commit()

    def post_routes(self, bodies, token=TOKEN):
        payload = {'routes': bodies}
        if token is not None:
            payload['token'] = token
        return self.client.post('/drones/api/route_sync', json=payload)

    def recalc(self, apply=True):
        return recalc.recalculate(TEST_DB_PATH, WORK_DAY, WORK_DAY,
                                  apply=apply)

    def works(self):
        with app.app_context():
            return DroneCoverageWork.query.order_by(
                DroneCoverageWork.group_index).all()

    def only_work(self):
        rows = self.works()
        self.assertEqual(len(rows), 1,
                         'expected exactly one work, got %d' % len(rows))
        return rows[0]

    # ── Доступ к SQLite в обход ORM ──────────────────────────────────────────

    def raw(self, sql, params=()):
        """[REASON]: читаем ХРАНИМОЕ значение, а не то, что вернёт ORM."""
        con = sqlite3.connect(TEST_DB_PATH)
        try:
            return con.execute(sql, params).fetchall()
        finally:
            con.close()

    def stored_areas(self):
        return self.raw('SELECT dji_flight_id, area_ha, typeof(area_ha) '
                        '  FROM drone_flights ORDER BY dji_flight_id')


# ─── 1-4: геометрия. Перекрытие, вне контура, смешанный, холостой ────────────

class Geometry(Base):
    """Числа проверяются против школьной формулы, а не против самих себя."""

    def setUp(self):
        Base.setUp(self)
        self.add_contour('SYNTHETIC-CONTOUR-A', square(100.0))

    def test_01_overlap_is_counted_once(self):
        """Два прохода в 4 м друг от друга при ширине 8 м.

        Полосы перекрываются наполовину. Объединение -- лента шириной 12 м и
        длиной 160 м = 1920 м2 = 0.192 га. Сумма независимых полос дала бы
        2 * 160 * 8 = 2560 м2 = 0.256 га, то есть посчитала бы перекрытие
        дважды. Оба числа хранятся, и разница между ними -- это и есть
        отрицательный контроль.
        """
        self.add_flight(900001, minute=0)
        self.add_flight(900002, minute=1)
        self.post_routes([route_body(900001, pass_line(0.0, -80.0, 80.0)),
                          route_body(900002, pass_line(4.0, -80.0, 80.0))])
        self.recalc()
        work = self.only_work()

        self.assertEqual(work.quality_status, ua.READY_ESTIMATE)
        self.assertAlmostEqual(work.estimated_useful_area_ha, 0.192, places=3)
        # Отрицательный контроль: перекрытие, посчитанное дважды.
        self.assertAlmostEqual(work.sum_independent_swaths_ha, 0.256,
                               places=3)
        self.assertLess(work.estimated_useful_area_ha,
                        work.sum_independent_swaths_ha,
                        'the union must be SMALLER than the plain sum, or '
                        'the overlap is being counted twice')

    def test_02_part_outside_the_contour_is_excluded(self):
        """Проход выходит за контур: наружная часть в площадь не входит.

        Проход от -80 до +300 м; контур кончается на +100 м. Внутри 180 м из
        380, и полоса внутри -- около 0.144 га против 0.304 га всей полосы.
        """
        self.add_flight(900001)
        self.post_routes([route_body(900001, pass_line(0.0, -80.0, 300.0))])
        self.recalc()
        work = self.only_work()

        self.assertEqual(work.quality_status, ua.READY_ESTIMATE)
        # Вся полоса маршрута -- отрицательный контроль: столько получилось
        # бы без обрезки контуром.
        self.assertAlmostEqual(work.swath_union_ha, 0.304, places=2)
        self.assertLess(work.estimated_useful_area_ha, 0.16)
        self.assertGreater(work.estimated_useful_area_ha, 0.13)
        self.assertLess(work.estimated_useful_area_ha, work.swath_union_ha,
                        'movement outside the contour must not be counted')

    def test_02b_the_band_is_clipped_at_the_edge_not_only_the_segments(self):
        """Обрезка контуром режет ПОЛОСУ, а не только отрезки.

        [REASON]: отрезок вне контура и так отбрасывается классификацией
        (SEG_OUTSIDE), поэтому на маршруте в середине поля «обрезать по
        полигону» и «не обрезать» дают почти одно число, и проверка на таком
        маршруте не различает два случая. Здесь проход идёт в 5 м от края при
        ширине захвата 40 м: отрезки внутри целиком, а полоса свисает наружу
        на 15 м из 40. Без обрезки площадь была бы в полтора раза больше.
        """
        self.add_flight(900001)
        self.post_routes([route_body(900001, pass_line(95.0, -80.0, 80.0),
                                     width=40.0)])
        self.recalc()
        work = self.only_work()

        self.assertEqual(work.quality_status, ua.READY_ESTIMATE)
        # Полоса целиком -- отрицательный контроль: столько получилось бы,
        # если бы обрезка по полигону не выполнялась.
        self.assertAlmostEqual(work.swath_union_ha, 0.726, places=2)
        self.assertAlmostEqual(work.estimated_useful_area_ha, 0.458, places=2)
        self.assertLess(work.estimated_useful_area_ha,
                        work.swath_union_ha * 0.75,
                        'the swath must be clipped by the field polygon, not '
                        'merely filtered by which segments are inside it')

    def test_03_mixed_flight_counts_only_the_work_pass(self):
        """Один вылет несёт и рабочий проход, и перелёт.

        Перелёт -- шаги по 400 и 500 м, длиннее gap_m. В площадь входит
        только проход: 160 м * 8 м = 0.128 га.
        """
        self.add_flight(900001)
        points = pass_line(0.0, -80.0, 80.0) + [at(400.0, 400.0),
                                                at(900.0, 900.0)]
        self.post_routes([route_body(900001, points)])
        self.recalc()
        work = self.only_work()

        self.assertEqual(work.quality_status, ua.READY_ESTIMATE)
        self.assertAlmostEqual(work.estimated_useful_area_ha, 0.128, places=3)

    def test_04_fully_idle_flight_makes_no_positive_area(self):
        """Полностью холостой вылет не создаёт положительной площади."""
        self.add_flight(900001)
        self.post_routes([route_body(900001, ferry_line())])
        self.recalc()
        work = self.only_work()

        self.assertEqual(work.work_segments, 0)
        self.assertEqual(work.estimated_useful_area_ha, 0.0)
        self.assertFalse(work.estimated_useful_area_ha > 0,
                         'an idle route must never produce a positive useful '
                         'area')

    def test_04b_idle_flight_without_width_invents_nothing(self):
        """Холостой вылет БЕЗ ширины: ни площади, ни превращения нуля в плюс.

        [REASON]: ширина «нужна» ровно там, где есть что закрашивать. У
        маршрута без единого рабочего прохода рабочей полосы нет ни при какой
        ширине, поэтому её отсутствие не делает число неполным -- и не даёт
        права выдумать площадь.
        """
        self.add_flight(900001)
        self.post_routes([route_body(900001, ferry_line(), width=None,
                                     recorded=False)])
        self.recalc()
        work = self.only_work()

        self.assertEqual(work.flights_without_width, 1)
        self.assertEqual(work.flights_without_width_on_work, 0)
        self.assertEqual(work.work_segments, 0)
        self.assertEqual(work.estimated_useful_area_ha, 0.0)


# ─── 5-7: контур и ширина. Площадь NULL, а не ноль ───────────────────────────

class NullNeverZero(Base):

    def test_05_two_equally_good_contours_give_null(self):
        """Два одинаково подходящих контура: CONTOUR_AMBIGUOUS, площадь NULL."""
        self.add_contour('SYNTHETIC-CONTOUR-A', square(100.0))
        self.add_contour('SYNTHETIC-CONTOUR-B', square(100.0))
        self.add_flight(900001)
        self.post_routes([route_body(900001, pass_line(0.0, -80.0, 80.0))])
        self.recalc()
        work = self.only_work()

        self.assertEqual(work.quality_status, ua.CONTOUR_AMBIGUOUS)
        self.assertIsNone(work.estimated_useful_area_ha)
        self.assertNotEqual(work.estimated_useful_area_ha, 0.0)
        # Проверка ХРАНИМОГО значения: NULL, а не 0.0.
        stored = self.raw('SELECT estimated_useful_area_ha, '
                          '       typeof(estimated_useful_area_ha) '
                          '  FROM drone_coverage_works')
        self.assertEqual(stored[0][1], 'null',
                         'an ambiguous contour must store NULL, not 0.0')

    def test_06_no_contour_matched_gives_null(self):
        """Контур не найден: CONTOUR_NOT_MATCHED, площадь NULL."""
        self.add_contour('SYNTHETIC-CONTOUR-FAR',
                         square(50.0, east_m=5000.0, north_m=5000.0))
        self.add_flight(900001)
        self.post_routes([route_body(900001, pass_line(0.0, -80.0, 80.0))])
        self.recalc()
        work = self.only_work()

        self.assertEqual(work.quality_status, ua.CONTOUR_NOT_MATCHED)
        self.assertIsNone(work.estimated_useful_area_ha)
        stored = self.raw('SELECT typeof(estimated_useful_area_ha) '
                          '  FROM drone_coverage_works')
        self.assertEqual(stored[0][0], 'null')

    def test_07_work_pass_without_width_is_not_ready(self):
        """Рабочий проход без ширины -- не READY_ESTIMATE и не число."""
        self.add_contour('SYNTHETIC-CONTOUR-A', square(100.0))
        self.add_flight(900001)
        self.post_routes([route_body(900001, pass_line(0.0, -80.0, 80.0),
                                     width=None, recorded=False)])
        self.recalc()
        work = self.only_work()

        self.assertEqual(work.quality_status, ua.PARTIAL_DATA)
        self.assertEqual(work.quality_reason,
                         ua.REASON_WIDTH_MISSING_ON_WORK_PASS)
        self.assertIsNone(work.estimated_useful_area_ha)
        self.assertGreater(work.work_segments, 0,
                           'the pass itself must still be classified as work '
                           '-- the missing width is about the BAND, not the '
                           'classification')
        # [REASON]: вылет без ширины не превращается в полосу ВОВСЕ -- ни
        # своей, ни соседской. Без этой проверки подстановка медианы или
        # паспортной ширины прошла бы незамеченной: статус остался бы
        # PARTIAL_DATA, площадь -- None, а объясняющие числа молча стали бы
        # выдуманными. Мутация «half = 4.0 вместо None» ловится здесь.
        self.assertIsNone(work.swath_union_ha,
                          'a flight whose width DJI never recorded must '
                          'produce no band at all -- no median, no passport '
                          'width, no neighbour value')
        self.assertIsNone(work.clipped_all_ha)

    def test_07b_no_route_at_all_is_data_unavailable(self):
        """Вылет есть, маршрута нет: DATA_UNAVAILABLE, площадь NULL."""
        self.add_contour('SYNTHETIC-CONTOUR-A', square(100.0))
        self.add_flight(900001)
        self.recalc()
        work = self.only_work()

        self.assertEqual(work.quality_status, ua.DATA_UNAVAILABLE)
        self.assertEqual(work.quality_reason, ua.REASON_NO_ROUTE)
        self.assertIsNone(work.estimated_useful_area_ha)
        self.assertEqual(work.flights_without_route, 1)


# ─── 8-9: идемпотентность и отпечаток входа ──────────────────────────────────

class Idempotence(Base):

    def setUp(self):
        Base.setUp(self)
        self.add_contour('SYNTHETIC-CONTOUR-A', square(100.0))
        self.add_flight(900001)
        self.body = route_body(900001, pass_line(0.0, -80.0, 80.0))

    def test_08_repeating_the_batch_changes_nothing(self):
        """Тот же пакет второй раз: ни дублей, ни изменения результата."""
        first = self.post_routes([self.body]).get_json()
        self.assertEqual((first['new'], first['unchanged']), (1, 0))
        self.recalc()
        before = self.only_work()
        area_before = before.estimated_useful_area_ha
        computed_before = before.computed_at

        second = self.post_routes([self.body]).get_json()
        self.assertEqual((second['new'], second['updated'],
                          second['unchanged']), (0, 0, 1))
        summary = self.recalc()

        self.assertEqual(len(self.raw('SELECT id FROM drone_flight_routes')), 1)
        self.assertEqual(summary['unchanged'], 1)
        self.assertEqual(summary['inserted'], 0)
        self.assertEqual(summary['updated'], 0)
        after = self.only_work()
        self.assertEqual(after.estimated_useful_area_ha, area_before)
        # Повторный --apply не двигает даже отметку времени расчёта.
        self.assertEqual(after.computed_at, computed_before)
        self.assertEqual(len(self.works()), 1)

    def test_09_changing_the_route_recomputes(self):
        """Изменившийся маршрут меняет отпечаток и пересчитывает результат."""
        self.post_routes([self.body])
        self.recalc()
        before = self.only_work()
        fingerprint_before = before.inputs_fingerprint
        area_before = before.estimated_useful_area_ha

        longer = route_body(900001, pass_line(0.0, -80.0, 120.0))
        answer = self.post_routes([longer]).get_json()
        self.assertEqual(answer['updated'], 1)
        summary = self.recalc()

        after = self.only_work()
        self.assertEqual(summary['updated'], 1)
        self.assertNotEqual(after.inputs_fingerprint, fingerprint_before)
        self.assertGreater(after.estimated_useful_area_ha, area_before,
                           'a longer work pass must give a larger area')
        # По-прежнему одна строка: пересчёт обновляет, а не накапливает.
        self.assertEqual(len(self.works()), 1)

    def test_09b_changing_the_algorithm_parameters_recomputes(self):
        """Изменение параметров даёт новый расчёт, а не устаревшее число."""
        self.post_routes([self.body])
        self.recalc()
        fingerprint_before = self.only_work().inputs_fingerprint

        wider = ua.StudyParams(min_pass_m=500.0)
        summary = recalc.recalculate(TEST_DB_PATH, WORK_DAY, WORK_DAY,
                                     apply=True, params=wider)
        self.assertEqual(summary['updated'], 1)
        after = self.only_work()
        self.assertNotEqual(after.inputs_fingerprint, fingerprint_before)
        # Порог прохода в 500 м отвергает проход длиной 160 м: он становится
        # соединением, и полезная площадь честно падает до нуля.
        self.assertEqual(after.work_segments, 0)
        self.assertEqual(after.estimated_useful_area_ha, 0.0)
        self.assertEqual(json.loads(after.params_json)['min_pass_m'], 500.0)


# ─── 10: исходная площадь DJI неприкосновенна ────────────────────────────────

class DjiAreaUntouched(Base):

    def test_10_dji_area_is_byte_identical_before_and_after(self):
        """`drone_flights.area_ha` до и после приёма и пересчёта.

        Сравнение на уровне SQLite-значения: и число, и его `typeof`. ORM тут
        не участвует -- он мог бы вернуть своё представление вместо хранимого.
        """
        self.add_contour('SYNTHETIC-CONTOUR-A', square(100.0))
        self.add_flight(900001, area_ha=1.2345, minute=0)
        self.add_flight(900002, area_ha=0.9876, minute=1)
        before = self.stored_areas()
        self.assertEqual([row[1] for row in before], [1.2345, 0.9876])

        self.post_routes([route_body(900001, pass_line(0.0, -80.0, 80.0)),
                          route_body(900002, pass_line(4.0, -80.0, 80.0))])
        self.assertEqual(self.stored_areas(), before,
                         'the route ingest must not touch drone_flights')

        self.recalc()
        self.assertEqual(self.stored_areas(), before,
                         'the recalculation must not touch drone_flights')

        # И ещё раз, после повторного приёма изменившегося маршрута.
        self.post_routes([route_body(900001, pass_line(0.0, -80.0, 140.0))])
        self.recalc()
        self.assertEqual(self.stored_areas(), before)

        # Новый показатель при этом ЕСТЬ и он другой -- иначе проверка выше
        # была бы зелёной и на коде, который вообще ничего не считает.
        work = self.only_work()
        self.assertIsNotNone(work.estimated_useful_area_ha)
        self.assertNotEqual(work.estimated_useful_area_ha, 1.2345)


# ─── 11-13: страница ─────────────────────────────────────────────────────────

class CoveragePage(Base):
    """Страница /drones/coverage: суммы, права и приватность."""

    def setUp(self):
        Base.setUp(self)
        self.add_contour('SYNTHETIC-CONTOUR-A', square(100.0))
        self.admin_id = create_admin('coverage-admin')

    def as_admin(self, language='ru'):
        with app.app_context():
            user = User.query.get(self.admin_id)
            user.language = language
            db.session.commit()
        client = app.test_client()
        login(client, self.admin_id)
        return client

    def build_ready_and_partial(self):
        """Одна готовая работа и одна неполная, у РАЗНЫХ машин."""
        org_id = create_org('SYNTHETIC Org 2')
        with app.app_context():
            other = DroneUnit(number=7, organization_id=org_id)
            db.session.add(other)
            db.session.flush()
            other_id = other.id
            db.session.add(DroneFlight(
                dji_flight_id=900009, drone_unit_id=other_id,
                nickname_raw='SYNTHETIC-NICK-2',
                started_at=datetime(2026, 6, 5, 4, 0),
                area_ha=5.0, raw_json='{}'))
            db.session.commit()
        self.add_flight(900001, area_ha=2.0)
        self.post_routes([
            route_body(900001, pass_line(0.0, -80.0, 80.0)),
            # Второй вылет: рабочий проход БЕЗ ширины -> PARTIAL_DATA.
            route_body(900009, pass_line(20.0, -80.0, 80.0), width=None,
                       recorded=False)])
        self.recalc()
        statuses = {work.unit_key: work.quality_status
                    for work in self.works()}
        self.assertIn(ua.READY_ESTIMATE, statuses.values())
        self.assertIn(ua.PARTIAL_DATA, statuses.values())

    def test_11_cards_sum_only_ready_estimates(self):
        """Карточка итога складывает только READY_ESTIMATE."""
        self.build_ready_and_partial()
        client = self.as_admin()
        page = client.get('/drones/coverage?date_from=2026-06-01'
                          '&date_to=2026-06-30')
        self.assertEqual(page.status_code, 200)
        html = page.get_data(as_text=True)

        ready = [w for w in self.works()
                 if w.quality_status == ua.READY_ESTIMATE]
        self.assertEqual(len(ready), 1)
        self.assertAlmostEqual(ready[0].estimated_useful_area_ha, 0.128,
                               places=3)
        # В карточке ровно площадь готовой работы, 0.13 после округления.
        # Разделитель дробной части -- ТОЧКА: `vs_num` группирует только
        # целую часть неразрывным пробелом и запятую не ставит.
        self.assertIn('0.13', html)
        # Отрицательный контроль: если бы неполная работа попала в сумму,
        # там стояло бы 0.26 -- её полоса такая же.
        self.assertNotIn('0.26', html)
        # И счётчики: одна готовая, одна нет.
        self.assertIn('>1<', html.replace(' ', ''))

    def test_11b_partial_work_shows_no_number_in_its_row(self):
        """У неполной работы в колонке площади «Нет данных», а не 0.00.

        Проверяется ИМЕННО ЕЁ строка, а не страница целиком: на странице есть
        и готовая работа, у которой ноль в колонке погрешности законный --
        обе сетки сошлись точно. Проверка «нигде нет 0.00» запретила бы
        честный ноль и молча ловила бы не то.
        """
        self.build_ready_and_partial()
        client = self.as_admin()
        html = client.get('/drones/coverage?date_from=2026-06-01'
                          '&date_to=2026-06-30').get_data(as_text=True)

        rows = re.findall(r'<tr>(.*?)</tr>', html, re.S)
        partial = [row for row in rows if 'Неполные данные' in row]
        ready = [row for row in rows if 'Расчёт готов' in row]
        self.assertEqual(len(partial), 1)
        self.assertEqual(len(ready), 1)

        # Неполная работа: два прочерка подряд -- площадь и погрешность.
        self.assertEqual(partial[0].count('Нет данных'), 2)
        self.assertNotIn('0.00', partial[0])
        self.assertNotIn('0.13', partial[0])

        # Отрицательный контроль: у готовой работы число ЕСТЬ. Иначе проверка
        # выше была бы зелёной и на странице, не печатающей площадь вовсе.
        self.assertIn('0.13', ready[0])
        self.assertNotIn('Нет данных', ready[0])

    def test_12_permission_is_enforced_at_the_route(self):
        """Без права `drones` -- 403; с правом -- страница."""
        with app.app_context():
            plain = User(username='no-drones', role='user',
                         full_name='SYNTHETIC user')
            plain.set_password('test-password')
            db.session.add(plain)
            db.session.commit()
            plain_id = plain.id

        denied = app.test_client()
        login(denied, plain_id)
        self.assertEqual(denied.get('/drones/coverage').status_code, 403)

        allowed = self.as_admin()
        self.assertEqual(allowed.get('/drones/coverage').status_code, 200)

    def test_13_no_coordinates_or_secrets_reach_the_page(self):
        """В HTML нет координат, неизвестных полей protobuf и токена."""
        self.build_ready_and_partial()
        client = self.as_admin()
        html = client.get('/drones/coverage?date_from=2026-06-01'
                          '&date_to=2026-06-30').get_data(as_text=True)

        # Координаты синтетического поля -- ни в каком написании.
        for fragment in ('39.7', '64.4', '39,70', '64,40', str(LAT0),
                         str(LON0)):
            self.assertNotIn(fragment, html,
                             'coordinate fragment %r reached the page'
                             % fragment)
        # Ни тела маршрута, ни переписи форм точек.
        for fragment in ('points_json', 'point_shape_census',
                         'route_point_variants', 'unknown_fields',
                         'coordinates'):
            self.assertNotIn(fragment, html)
        # Ни токена, ни настоящего идентификатора вылета.
        self.assertNotIn(TOKEN, html)
        self.assertNotIn('900001', html)

        # Отрицательный контроль: страница ВООБЩЕ что-то показала, иначе
        # проверки выше были бы зелёными и на пустой странице.
        self.assertIn('Расчётная полезная площадь', html)
        self.assertIn('0.13', html)

    def test_13b_default_period_is_bounded(self):
        """Без параметров период ограничен, а не «за всё время»."""
        client = self.as_admin()
        html = client.get('/drones/coverage').get_data(as_text=True)
        today = recalc.local_day(datetime.utcnow())
        self.assertIn(str(today.year), html)
        # Поле «Дата с» заполнено -- значит граница есть.
        self.assertRegex(html, r'name="date_from"[^>]*value="\d{4}-\d{2}-\d{2}"')


# ─── 14: приёмник отказывает до записи ───────────────────────────────────────

class IngestGuards(Base):

    def setUp(self):
        Base.setUp(self)
        self.add_flight(900001)
        self.good = route_body(900001, pass_line(0.0, -80.0, 80.0))

    def rows(self):
        return len(self.raw('SELECT id FROM drone_flight_routes'))

    def test_14_refusals_happen_before_any_write(self):
        """Нет токена, неверный токен, слишком большое тело, неверная схема."""
        self.assertEqual(self.post_routes([self.good], token=None).status_code,
                         401)
        self.assertEqual(self.rows(), 0)

        self.assertEqual(
            self.post_routes([self.good], token='SYNTHETIC-wrong').status_code,
            401)
        self.assertEqual(self.rows(), 0)

        oversized = self.client.post('/drones/api/route_sync',
                                     json={'token': TOKEN,
                                           'routes': [self.good] * 501})
        self.assertEqual(oversized.status_code, 413)
        self.assertEqual(self.rows(), 0)

        bad_schema = self.client.post('/drones/api/route_sync',
                                      json={'token': TOKEN,
                                            'routes': 'not-a-list'})
        self.assertEqual(bad_schema.status_code, 400)
        self.assertEqual(self.rows(), 0)

        # Отрицательный контроль: годный пакет с тем же токеном ПРОХОДИТ,
        # иначе проверки выше были бы зелёными и на сломанном приёмнике.
        self.assertEqual(self.post_routes([self.good]).status_code, 200)
        self.assertEqual(self.rows(), 1)

    def test_14b_counters_add_up_and_orphans_are_not_stored(self):
        """seen = new + updated + unchanged + errors + unlinked."""
        answer = self.post_routes([
            self.good,
            route_body(900777, pass_line(0.0, -80.0, 80.0)),   # нет вылета
            {'dji_flight_id': 900001, 'points': [[91.0, 64.4], [39.7, 64.4]]},
        ]).get_json()

        self.assertEqual(answer['seen'], 3)
        self.assertEqual(answer['seen'],
                         answer['new'] + answer['updated']
                         + answer['unchanged'] + answer['errors']
                         + answer['unlinked'])
        self.assertEqual(answer['unlinked'], 1)
        self.assertEqual(answer['errors'], 1)
        self.assertEqual(answer['new'], 1)
        # Сирота НЕ сохранён и ни к чему не прикреплён догадкой.
        self.assertEqual(
            self.raw('SELECT count(*) FROM drone_flight_routes '
                     ' WHERE dji_flight_id = 900777')[0][0], 0)
        # Один плохой маршрут не откатил корректный.
        self.assertEqual(self.rows(), 1)

    def test_14c_a_route_shorter_than_two_points_is_refused(self):
        """Маршрут без геометрии не сохраняется и не даёт 0.00 га."""
        answer = self.post_routes([
            {'dji_flight_id': 900001, 'points': [at(0.0, 0.0)]}]).get_json()
        self.assertEqual(answer['errors'], 1)
        self.assertEqual(self.rows(), 0)


# ─── Сквозной путь ───────────────────────────────────────────────────────────

class EndToEnd(Base):
    """route_sync -> сохранённые маршруты -> пересчёт -> работа -> страница."""

    def test_the_whole_vertical_on_a_temporary_database(self):
        self.add_contour('SYNTHETIC-CONTOUR-A', square(100.0))
        self.add_flight(900001, area_ha=2.0, minute=0)
        self.add_flight(900002, area_ha=2.0, minute=1)

        # 1. Приём.
        answer = self.post_routes([
            route_body(900001, pass_line(0.0, -80.0, 80.0)),
            route_body(900002, pass_line(4.0, -80.0, 80.0))]).get_json()
        self.assertEqual((answer['seen'], answer['new']), (2, 2))

        # 2. Сохранённые маршруты.
        with app.app_context():
            routes = DroneFlightRoute.query.all()
            self.assertEqual(len(routes), 2)
            self.assertTrue(all(row.drone_flight_id is not None
                                for row in routes))
            self.assertTrue(all(row.spray_width_m == 8.0 for row in routes))

        # 3. Сухой прогон ничего не пишет.
        dry = self.recalc(apply=False)
        self.assertEqual(dry[ua.READY_ESTIMATE], 1)
        self.assertEqual(len(self.works()), 0,
                         'a dry run must write nothing at all')

        # 4. Пересчёт.
        summary = self.recalc()
        self.assertEqual(summary['inserted'], 1)
        work = self.only_work()
        self.assertEqual(work.quality_status, ua.READY_ESTIMATE)
        self.assertEqual(work.algorithm_version, ua.ALGORITHM_VERSION)
        self.assertEqual(work.routes_total, 2)
        self.assertEqual(work.flights_total, 2)
        self.assertAlmostEqual(work.dji_area_ha, 4.0, places=4)
        self.assertAlmostEqual(work.estimated_useful_area_ha, 0.192, places=3)
        # Параметры лежат рядом с числом: числа без объяснимой версии правил
        # в этой таблице не бывает.
        self.assertEqual(json.loads(work.params_json),
                         ua.algorithm_params())

        # 5. Страница.
        admin_id = create_admin('e2e-admin')
        with app.app_context():
            user = User.query.get(admin_id)
            user.language = 'ru'
            db.session.commit()
        client = app.test_client()
        login(client, admin_id)
        page = client.get('/drones/coverage?date_from=2026-06-01'
                          '&date_to=2026-06-30')
        self.assertEqual(page.status_code, 200)
        html = page.get_data(as_text=True)
        self.assertIn('0.19', html)      # расчётная полезная площадь
        self.assertIn('4.00', html)      # исходная площадь DJI, рядом и цела
        self.assertIn('Расчёт готов', html)


class Bilingual(Base):
    """Русский и узбекский кириллицей -- обе подписи, латиницы нет."""

    def test_both_languages_render(self):
        self.add_contour('SYNTHETIC-CONTOUR-A', square(100.0))
        self.add_flight(900001)
        self.post_routes([route_body(900001, pass_line(0.0, -80.0, 80.0))])
        self.recalc()
        admin_id = create_admin('lang-admin')

        seen = {}
        for language, marker in (('ru', 'Расчётная полезная площадь'),
                                 ('uz', 'Ҳисобий фойдали майдон')):
            with app.app_context():
                user = User.query.get(admin_id)
                user.language = language
                db.session.commit()
            client = app.test_client()
            login(client, admin_id)
            html = client.get('/drones/coverage').get_data(as_text=True)
            self.assertIn(marker, html,
                          'the %s heading is missing' % language)
            seen[language] = html

        # Узбекская страница НЕ несёт русского заголовка и наоборот: иначе
        # проверка выше проходила бы на шаблоне, печатающем оба сразу.
        self.assertNotIn('Расчётная полезная площадь', seen['uz'])
        self.assertNotIn('Ҳисобий фойдали майдон', seen['ru'])


if __name__ == '__main__':
    unittest.main()


class CollectorToPage(Base):
    """Вертикаль целиком: браузер -> очередь -> отправка -> база -> страница.

    Единственное, что здесь подделано, -- браузер и сокет. Всё между ними
    настоящее: декодер protobuf, сборка тела маршрута, файловая очередь,
    `sender.send_routes` со своей нарезкой и счётчиками, HTTP-контракт
    приёмника, запись в SQLite, пересчёт и рендеринг страницы.

    [REASON]: тест, который зовёт `route_sync` словарём, собранным вручную,
    не заметил бы расхождения между тем, что кладёт в очередь сборщик, и тем,
    что читает приёмник. Ровно это расхождение уже стоило треку одного
    живого прогона (перепись форм точек умирала на сериализации).
    """

    def test_from_a_fake_browser_response_to_the_rendered_page(self):
        import functools
        import shutil
        import tempfile
        from pathlib import Path

        from drone_collector.outbox import Outbox
        from drone_collector.route_ui_collect import (drain_route_outbox,
                                                      enqueue_routes,
                                                      route_bodies)
        from drone_collector.sender import send_routes
        from drone_collector.tests.test_route_decode import (response,
                                                             route_record)
        from drone_collector.tests.test_route_ui_probe import (
            ROUTE_ORIGIN, _QuietLog, deliver, ids_body,
            route_response)
        from drone_collector.route_ui_collect import RouteQueueCapture

        # 1. Синтетический ответ кабинета: два перекрывающихся прохода.
        flight_a, flight_b = 900001, 900002
        self.add_contour('SYNTHETIC-CONTOUR-A', square(100.0))
        self.add_flight(flight_a, area_ha=2.0, minute=0)
        self.add_flight(flight_b, area_ha=2.0, minute=1)

        body = response([
            route_record(flight_id=flight_a,
                         points=[tuple(point)
                                 for point in pass_line(0.0, -80.0, 80.0)],
                         width=8.0),
            route_record(flight_id=flight_b,
                         points=[tuple(point)
                                 for point in pass_line(4.0, -80.0, 80.0)],
                         width=8.0)])

        # 2. Полный жизненный цикл Playwright, как его видит наблюдатель.
        capture = RouteQueueCapture(logger=_QuietLog(),
                                    expected_origin=ROUTE_ORIGIN)
        deliver(capture, route_response(
            body=body, post_data=ids_body([flight_a, flight_b])))
        self.assertEqual(capture.bodies_captured, 1)
        self.assertEqual(capture.decode_failures, 0)
        records = capture.captured_records()
        self.assertEqual(sorted(r.flight_id for r in records),
                         [flight_a, flight_b])

        # 3. Очередь на диске.
        tmp = tempfile.mkdtemp(prefix='e2e_route_')
        self.addCleanup(shutil.rmtree, tmp, True)
        outbox = Outbox(Path(tmp) / 'outbox').prepare()
        bodies = route_bodies(records, 'simplified', 'route-decode-2')
        queued, duplicates = enqueue_routes(outbox, bodies)
        self.assertEqual((queued, duplicates), (2, 0))

        # 4. Отправка настоящим `send_routes` в настоящий endpoint. Подделан
        #    только сокет: транспорт зовёт тестовый клиент Flask.
        def post_through_flask(url, payload, _timeout):
            self.assertTrue(url.endswith('/drones/api/route_sync'))
            answer = self.client.post('/drones/api/route_sync', json=payload)
            return answer.status_code, answer.get_json()

        class _Cfg(object):
            api_token = TOKEN
            route_batch_size = 500
            route_sync_url = ('https://vehicle-soft.example.invalid'
                              '/drones/api/route_sync')

        result = drain_route_outbox(
            outbox, _Cfg(), _QuietLog(),
            send_fn=functools.partial(send_routes,
                                      post_fn=post_through_flask))

        self.assertEqual(result.sent, 2)
        self.assertEqual(len(outbox.pending()), 0)
        counters = result.counters
        self.assertEqual((counters.seen, counters.new), (2, 2))
        self.assertEqual(counters.unlinked, 0)
        self.assertTrue(counters.counters_agree)

        # 5. Маршруты в базе, с шириной, которую записал DJI.
        with app.app_context():
            stored = DroneFlightRoute.query.order_by(
                DroneFlightRoute.dji_flight_id).all()
            self.assertEqual([row.dji_flight_id for row in stored],
                             [flight_a, flight_b])
            self.assertTrue(all(row.spray_width_m == 8.0 for row in stored))
            self.assertTrue(all(row.spray_width_recorded for row in stored))
            self.assertTrue(all(row.decoder_version == 'route-decode-2'
                                for row in stored))

        # 6. Пересчёт: перекрытие посчитано один раз.
        self.recalc()
        work = self.only_work()
        self.assertEqual(work.quality_status, ua.READY_ESTIMATE)
        self.assertAlmostEqual(work.estimated_useful_area_ha, 0.192, places=3)
        self.assertAlmostEqual(work.sum_independent_swaths_ha, 0.256, places=3)

        # 7. Страница.
        admin_id = create_admin('collector-e2e-admin')
        with app.app_context():
            user = User.query.get(admin_id)
            user.language = 'ru'
            db.session.commit()
        browser = app.test_client()
        login(browser, admin_id)
        html = browser.get('/drones/coverage?date_from=2026-06-01'
                           '&date_to=2026-06-30').get_data(as_text=True)
        self.assertIn('0.19', html)
        self.assertIn('4.00', html)
        self.assertIn('Расчёт готов', html)

        # 8. Повторный прогон той же очереди: ни дублей, ни изменения числа.
        again = drain_route_outbox(
            outbox, _Cfg(), _QuietLog(),
            send_fn=functools.partial(send_routes,
                                      post_fn=post_through_flask))
        self.assertEqual(again.envelopes, 0,
                         'entries already in sent/ must not be re-sent')
        summary = self.recalc()
        self.assertEqual(summary['unchanged'], 1)
        self.assertEqual(summary['updated'], 0)
