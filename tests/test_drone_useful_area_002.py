# -*- coding: utf-8 -*-
"""DRONE-USEFUL-AREA-001, ремонтный проход: три дефекта расчёта.

Каждый из трёх давал НЕВЕРНОЕ ЧИСЛО, не падая и не выглядя подозрительно:

* устаревшая строка расчёта оставалась в базе после того, как две работы
  слились в одну, и продолжала попадать в сумму на `/drones/coverage`;
* вылет без маршрута произвольно приписывался ПЕРВОЙ работе машины за день,
  оставляя остальные в `READY_ESTIMATE` и объявляя их полными;
* отпечаток входа брал только `uuid` контура, поэтому исправленный полигон
  под тем же идентификатором не вызывал пересчёта, и в базе оставалась
  площадь, посчитанная по старой геометрии.

Геометрия синтетическая, как и в основном наборе: квадрат 200 x 200 м вокруг
круглых координат, вылеты с 900001. Ни одного настоящего значения.
"""

import json
import sqlite3
import unittest

from datetime import date, datetime

from tests.harness import app, reset_db, create_org, TEST_DB_PATH
from tests.test_drone_useful_area_001 import (LAT0, LON0, WORK_DAY, TOKEN,
                                              at, pass_line, route_body,
                                              square)

from models import db, DroneCoverageWork, DroneFlight, DroneUnit, FieldContour

import drone_coverage_recalc as recalc
import drone_useful_area as ua


def cross_line(north_m, east_from, east_to, step_m=5.0):
    """Проход ПОПЕРЁК поля. `pass_line` меняет только широту, поэтому
    маршрут-мост, который обязан пересечься с двумя разнесёнными по долготе
    работами, строится этой функцией."""
    points = []
    east = east_from
    while east <= east_to + 1e-9:
        points.append(at(east, north_m))
        east += step_m
    return points


class Base(unittest.TestCase):

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

    def add_flight(self, flight_id, area_ha=2.0, minute=0):
        with app.app_context():
            db.session.add(DroneFlight(
                dji_flight_id=flight_id, drone_unit_id=self.unit_id,
                nickname_raw='SYNTHETIC-NICK',
                started_at=datetime(2026, 6, 5, 3, minute),
                area_ha=area_ha, raw_json='{}'))
            db.session.commit()
        return flight_id

    def drop_flight(self, flight_id):
        with app.app_context():
            DroneFlight.query.filter_by(dji_flight_id=flight_id).delete()
            db.session.commit()

    def add_contour(self, external_id, geojson):
        ring = geojson['coordinates'][0]
        lats = [point[1] for point in ring]
        lngs = [point[0] for point in ring]
        with app.app_context():
            db.session.add(FieldContour(
                source='dji', external_id=external_id,
                name='SYNTHETIC field %s' % external_id,
                geometry_geojson=json.dumps(geojson), is_active=True,
                bbox_min_lat=min(lats), bbox_max_lat=max(lats),
                bbox_min_lng=min(lngs), bbox_max_lng=max(lngs)))
            db.session.commit()

    def post_routes(self, bodies):
        return self.client.post('/drones/api/route_sync',
                                json={'token': TOKEN, 'routes': bodies})

    def recalc(self, apply=True, date_from=None, date_to=None, params=None):
        return recalc.recalculate(TEST_DB_PATH, date_from or WORK_DAY,
                                  date_to or WORK_DAY, apply=apply,
                                  params=params)

    def works(self):
        with app.app_context():
            return DroneCoverageWork.query.order_by(
                DroneCoverageWork.work_date,
                DroneCoverageWork.unit_key,
                DroneCoverageWork.group_index).all()

    def raw(self, sql, params=()):
        con = sqlite3.connect(TEST_DB_PATH)
        try:
            return con.execute(sql, params).fetchall()
        finally:
            con.close()

    def summable_total(self):
        """Сумма ровно того, что страница положила бы в карточку итога."""
        return sum(work.estimated_useful_area_ha or 0.0
                   for work in self.works()
                   if work.quality_status == ua.READY_ESTIMATE)


# ─── D3: снятие устаревших строк ─────────────────────────────────────────────

class StaleRowsAreRemoved(Base):
    """Пересчёт -- СНИМОК периода, а не только вставка и обновление."""

    def setUp(self):
        Base.setUp(self)
        self.add_contour('SYNTHETIC-CONTOUR-A', square(400.0))

    def two_separate_works(self):
        """Два вылета в разных концах поля -- две пространственные работы.

        Разнесены по ДОЛГОТЕ на 600 м: рамки не пересекаются даже с запасом
        `margin_deg`. Проверяется отдельным утверждением, иначе тесты слияния
        ниже стояли бы на предположении.
        """
        self.add_flight(900001, minute=0)
        self.add_flight(900002, minute=1)
        self.post_routes([
            route_body(900001, pass_line(-300.0, -50.0, 50.0)),
            route_body(900002, pass_line(300.0, -50.0, 50.0))])

    def test_two_works_merging_into_one_removes_the_stale_row(self):
        """Слияние двух работ в одну: старая вторая строка удалена.

        [REASON]: без снятия она оставалась в базе и продолжала попадать в
        сумму. Итог завышался, и ни одна строка при этом не выглядела
        неправильной -- обе были посчитаны верно, просто одна описывала
        работу, которой больше нет.
        """
        self.two_separate_works()
        self.recalc()
        self.assertEqual(len(self.works()), 2,
                         'premise: the two routes must start as two works')
        inflated = self.summable_total()

        # Третий вылет идёт ПОПЕРЁК и пересекает обе: теперь это одна работа.
        self.add_flight(900003, minute=2)
        self.post_routes([route_body(900003,
                                     cross_line(0.0, -320.0, 320.0))])
        summary = self.recalc()

        self.assertEqual(summary['deleted'], 1)
        works = self.works()
        self.assertEqual(len(works), 1,
                         'the merged work must leave exactly one row')
        self.assertEqual(works[0].routes_total, 3)
        self.assertLess(self.summable_total(), inflated + works[0].dji_area_ha,
                        'the stale row must not still be adding to the total')
        self.assertEqual(len(self.raw('SELECT id FROM drone_coverage_works')), 1)

    def test_one_work_splitting_into_two_leaves_exactly_two_rows(self):
        self.add_flight(900001, minute=0)
        self.add_flight(900002, minute=1)
        self.add_flight(900003, minute=2)
        self.post_routes([
            route_body(900001, pass_line(-300.0, -50.0, 50.0)),
            route_body(900002, pass_line(300.0, -50.0, 50.0)),
            route_body(900003, cross_line(0.0, -320.0, 320.0))])
        self.recalc()
        self.assertEqual(len(self.works()), 1)

        # Мост убрали -- работа распалась обратно на две.
        self.drop_flight(900003)
        summary = self.recalc()
        self.assertEqual(len(self.works()), 2)
        self.assertEqual(summary['deleted'], 0,
                         'both identities still exist; nothing is stale')

    def test_a_day_whose_flights_are_gone_is_swept(self):
        """День без вылетов очищает свои прежние строки расчёта."""
        self.two_separate_works()
        self.recalc()
        self.assertEqual(len(self.works()), 2)

        self.drop_flight(900001)
        self.drop_flight(900002)
        summary = self.recalc()

        self.assertEqual(summary['deleted'], 2)
        self.assertEqual(self.works(), [])
        self.assertEqual(summary['days'], 0,
                         'the day itself no longer holds a flight')

    def test_a_dry_run_reports_the_sweep_but_changes_nothing(self):
        self.two_separate_works()
        self.recalc()
        before = self.raw('SELECT id, unit_key, work_date, group_index, '
                          '       inputs_fingerprint, computed_at '
                          '  FROM drone_coverage_works ORDER BY id')
        self.drop_flight(900002)

        summary = self.recalc(apply=False)
        self.assertEqual(summary['deleted'], 1)
        self.assertFalse(summary['applied'])
        after = self.raw('SELECT id, unit_key, work_date, group_index, '
                         '       inputs_fingerprint, computed_at '
                         '  FROM drone_coverage_works ORDER BY id')
        self.assertEqual(after, before,
                         'a dry run must not delete, insert or restamp a row')

    def test_rows_outside_the_period_are_never_touched(self):
        """Границы -- ровно запрошенные."""
        self.two_separate_works()
        self.recalc()
        # Строка соседнего дня, которую этот прогон не пересчитывал.
        with app.app_context():
            db.session.add(DroneCoverageWork(
                unit_key='unit:99', work_date=date(2026, 6, 4), group_index=0,
                inputs_fingerprint='f', route_fingerprint='r',
                algorithm_version=ua.ALGORITHM_VERSION, params_json='{}',
                quality_status=ua.READY_ESTIMATE,
                quality_reason=ua.REASON_OK,
                estimated_useful_area_ha=1.0,
                computed_at=datetime(2026, 6, 4)))
            db.session.commit()

        self.drop_flight(900001)
        self.drop_flight(900002)
        summary = self.recalc()

        self.assertEqual(summary['deleted'], 2)
        survivors = [work.unit_key for work in self.works()]
        self.assertEqual(survivors, ['unit:99'],
                         'a row outside the requested period must survive')

    def test_an_unchanged_recalculation_deletes_nothing_and_restamps_nothing(self):
        self.two_separate_works()
        self.recalc()
        before = self.raw('SELECT id, computed_at, inputs_fingerprint '
                          '  FROM drone_coverage_works ORDER BY id')
        summary = self.recalc()
        self.assertEqual(summary['deleted'], 0)
        self.assertEqual(summary['unchanged'], 2)
        self.assertEqual(summary['inserted'], 0)
        self.assertEqual(summary['updated'], 0)
        self.assertEqual(
            self.raw('SELECT id, computed_at, inputs_fingerprint '
                     '  FROM drone_coverage_works ORDER BY id'), before)


# ─── D4.2: вылет без маршрута не приписывается наугад ────────────────────────

class UnroutedFlights(Base):

    def setUp(self):
        Base.setUp(self)
        self.add_contour('SYNTHETIC-CONTOUR-A', square(400.0))

    def test_two_works_and_one_unrouted_flight_leave_nothing_summable(self):
        """Ни одна из работ не считается полной.

        [REASON]: к какой из двух пространственно разных работ относится вылет
        без маршрута, неизвестно и узнать неоткуда -- рамки у него нет.
        Приписка к первой оставляла ВТОРУЮ в READY_ESTIMATE и объявляла её
        полной, хотя недостающий вылет мог принадлежать именно ей.
        """
        self.add_flight(900001, area_ha=2.0, minute=0)
        self.add_flight(900002, area_ha=2.0, minute=1)
        self.add_flight(900003, area_ha=5.0, minute=2)   # без маршрута
        self.post_routes([
            route_body(900001, pass_line(-300.0, -50.0, 50.0)),
            route_body(900002, pass_line(300.0, -50.0, 50.0))])
        self.recalc()

        works = self.works()
        summable = [work for work in works
                    if work.quality_status == ua.READY_ESTIMATE]
        self.assertEqual(summable, [],
                         'no work may be summable while an unrouted flight of '
                         'the same machine and day is unassignable')
        self.assertEqual(self.summable_total(), 0.0)

        reasons = {work.quality_reason for work in works}
        self.assertIn(ua.REASON_UNROUTED_FLIGHT_NOT_ASSIGNABLE, reasons)

    def test_the_flight_and_its_dji_area_are_counted_exactly_once(self):
        """Счётчики вылетов и площадь DJI не удваиваются."""
        self.add_flight(900001, area_ha=2.0, minute=0)
        self.add_flight(900002, area_ha=3.0, minute=1)
        self.add_flight(900003, area_ha=5.0, minute=2)   # без маршрута
        self.post_routes([
            route_body(900001, pass_line(-300.0, -50.0, 50.0)),
            route_body(900002, pass_line(300.0, -50.0, 50.0))])
        self.recalc()

        works = self.works()
        self.assertEqual(sum(work.flights_total for work in works), 3,
                         'the unrouted flight must be counted once, not once '
                         'per work it might belong to')
        self.assertAlmostEqual(
            sum(work.dji_area_ha or 0.0 for work in works), 10.0, places=4)
        self.assertEqual(sum(work.routes_total for work in works), 2)

    def test_one_work_and_one_missing_route_is_partial_data(self):
        """При одной работе за день приписка ничего не выдумывает."""
        self.add_flight(900001, area_ha=2.0, minute=0)
        self.add_flight(900002, area_ha=5.0, minute=1)   # без маршрута
        self.post_routes([route_body(900001, pass_line(0.0, -80.0, 80.0))])
        self.recalc()

        work = self.works()
        self.assertEqual(len(work), 1)
        self.assertEqual(work[0].quality_status, ua.PARTIAL_DATA)
        self.assertEqual(work[0].quality_reason,
                         ua.REASON_SOME_FLIGHTS_WITHOUT_ROUTE)
        self.assertEqual(work[0].flights_total, 2)
        self.assertEqual(work[0].flights_without_route, 1)
        self.assertIsNone(work[0].estimated_useful_area_ha)

    def test_only_unrouted_flights_are_data_unavailable(self):
        self.add_flight(900001, area_ha=2.0, minute=0)
        self.recalc()
        work = self.works()
        self.assertEqual(len(work), 1)
        self.assertEqual(work[0].quality_status, ua.DATA_UNAVAILABLE)
        self.assertEqual(work[0].quality_reason, ua.REASON_NO_ROUTE)
        self.assertIsNone(work[0].estimated_useful_area_ha)

    def test_a_broken_stored_route_is_route_invalid_not_missing(self):
        """Битый `points_json` -- ROUTE_INVALID, а не «маршрута нет».

        [REASON]: нечитаемый JSON давал пустой список, пустой список отправлял
        вылет в корзину «без маршрута», и привезённая, но негодная геометрия
        молча превращалась в DATA_UNAVAILABLE. Это разные факты и разные
        действия: в первом случае сборщик ещё не привозил маршрут, во втором
        привёз то, что не читается, и смотреть надо на приём.
        """
        self.add_flight(900001, area_ha=2.0, minute=0)
        self.post_routes([route_body(900001, pass_line(0.0, -80.0, 80.0))])
        con = sqlite3.connect(TEST_DB_PATH)
        try:
            con.execute("UPDATE drone_flight_routes SET points_json = ? "
                        " WHERE dji_flight_id = 900001", ('{not json',))
            con.commit()
        finally:
            con.close()
        self.recalc()

        work = self.works()
        self.assertEqual(len(work), 1)
        self.assertEqual(work[0].quality_status, ua.ROUTE_INVALID)
        self.assertNotEqual(work[0].quality_status, ua.DATA_UNAVAILABLE)
        self.assertIsNone(work[0].estimated_useful_area_ha)

    def test_an_empty_stored_route_is_route_invalid_too(self):
        self.add_flight(900001, area_ha=2.0, minute=0)
        self.post_routes([route_body(900001, pass_line(0.0, -80.0, 80.0))])
        con = sqlite3.connect(TEST_DB_PATH)
        try:
            con.execute("UPDATE drone_flight_routes SET points_json = '[]' "
                        " WHERE dji_flight_id = 900001")
            con.commit()
        finally:
            con.close()
        self.recalc()
        self.assertEqual(self.works()[0].quality_status, ua.ROUTE_INVALID)

    def test_a_clean_two_work_day_is_still_fully_summable(self):
        """Отрицательный контроль: без вылета-сироты обе работы готовы.

        Без него всё выше было бы зелёным и у кода, который никогда никакую
        работу готовой не объявляет.
        """
        self.add_flight(900001, area_ha=2.0, minute=0)
        self.add_flight(900002, area_ha=2.0, minute=1)
        self.post_routes([
            route_body(900001, pass_line(-300.0, -50.0, 50.0)),
            route_body(900002, pass_line(300.0, -50.0, 50.0))])
        self.recalc()

        works = self.works()
        self.assertEqual(len(works), 2)
        self.assertTrue(all(work.quality_status == ua.READY_ESTIMATE
                            for work in works))
        self.assertGreater(self.summable_total(), 0.0)


# ─── D5: отпечаток учитывает геометрию контура ───────────────────────────────

class ContourGeometryInTheFingerprint(Base):

    def setUp(self):
        Base.setUp(self)
        self.add_contour('SYNTHETIC-CONTOUR-A', square(100.0))
        self.add_flight(900001, minute=0)
        self.post_routes([route_body(900001, pass_line(0.0, -80.0, 80.0))])

    def set_geometry(self, geojson):
        """Заменить полигон, НЕ трогая uuid."""
        ring = geojson['coordinates'][0]
        lats = [point[1] for point in ring]
        lngs = [point[0] for point in ring]
        with app.app_context():
            row = FieldContour.query.filter_by(
                external_id='SYNTHETIC-CONTOUR-A').one()
            row.geometry_geojson = json.dumps(geojson)
            row.bbox_min_lat, row.bbox_max_lat = min(lats), max(lats)
            row.bbox_min_lng, row.bbox_max_lng = min(lngs), max(lngs)
            db.session.commit()

    def test_a_corrected_polygon_under_the_same_uuid_recomputes(self):
        """Тот же uuid, другой полигон -> updated=1 и другая площадь.

        [REASON]: отпечаток брал только uuid, а uuid не меняется, когда
        полигон исправляют. Строка признавалась `unchanged`, и площадь,
        посчитанная по СТАРОЙ геометрии, оставалась в базе -- причины
        сомневаться в ней не было ни одной.
        """
        self.recalc()
        before = self.works()[0]
        area_before = before.estimated_useful_area_ha
        fingerprint_before = before.inputs_fingerprint
        self.assertIsNotNone(area_before)

        # Контур сузили: проход теперь выходит за него, площадь обязана упасть.
        self.set_geometry(square(40.0))
        summary = self.recalc()

        self.assertEqual(summary['updated'], 1)
        self.assertEqual(summary['unchanged'], 0)
        after = self.works()[0]
        self.assertNotEqual(after.inputs_fingerprint, fingerprint_before)
        self.assertLess(after.estimated_useful_area_ha, area_before,
                        'the narrower polygon must clip more away')

    def test_the_same_polygon_reformatted_stays_unchanged(self):
        """Порядок ключей и отступы отпечаток не меняют.

        Иначе каждый повторный снимок справочника объявлял бы все работы
        изменившимися, и `unchanged` перестал бы что-либо значить.
        """
        self.recalc()
        before = self.raw('SELECT inputs_fingerprint, computed_at '
                          '  FROM drone_coverage_works')

        same = square(100.0)
        reordered = {'coordinates': same['coordinates'], 'type': same['type']}
        with app.app_context():
            row = FieldContour.query.filter_by(
                external_id='SYNTHETIC-CONTOUR-A').one()
            row.geometry_geojson = json.dumps(reordered, indent=4,
                                              sort_keys=False)
            db.session.commit()

        summary = self.recalc()
        self.assertEqual(summary['unchanged'], 1)
        self.assertEqual(summary['updated'], 0)
        self.assertEqual(
            self.raw('SELECT inputs_fingerprint, computed_at '
                     '  FROM drone_coverage_works'), before)

    def test_a_different_uuid_still_changes_the_fingerprint(self):
        """Прежнее правило не потеряно."""
        first = ua.inputs_fingerprint([(1, 'sha')], contour_key='A',
                                      contour_geometry=square(100.0))
        second = ua.inputs_fingerprint([(1, 'sha')], contour_key='B',
                                       contour_geometry=square(100.0))
        self.assertNotEqual(first, second)

    def test_the_parameters_still_change_the_fingerprint(self):
        first = ua.inputs_fingerprint([(1, 'sha')], contour_key='A',
                                      contour_geometry=square(100.0))
        second = ua.inputs_fingerprint([(1, 'sha')], contour_key='A',
                                       contour_geometry=square(100.0),
                                       params=ua.StudyParams(min_pass_m=99.0))
        self.assertNotEqual(first, second)

    def test_the_geometry_hash_leaks_no_coordinate(self):
        digest = ua.geometry_fingerprint(square(100.0))
        self.assertEqual(len(digest), 64)
        for fragment in (str(LAT0), str(LON0), '39.7', '64.4'):
            self.assertNotIn(fragment, digest)
        self.assertIsNone(ua.geometry_fingerprint(None))


if __name__ == '__main__':
    unittest.main()
