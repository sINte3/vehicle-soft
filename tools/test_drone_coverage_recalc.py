# -*- coding: utf-8 -*-
"""tools/test_drone_coverage_recalc.py -- переходы пересчёта полезной площади.

САМОСТОЯТЕЛЬНЫЙ НАБОР БЕЗ FLASK. Строит временную базу SQLite тем же DDL, что
и миграция, и водит `drone_coverage_recalc.recalculate()` напрямую.

ЗАЧЕМ ОТДЕЛЬНЫЙ НАБОР. Сквозной набор `tests/test_drone_useful_area_002.py`
поднимает приложение и потому требует Flask, а в GitHub Actions приложение не
устанавливается: там stdlib плюс jinja2 и openpyxl. Из-за этого зелёный CI не
проверял ни снятие устаревших строк, ни отпечаток, ни поведение вылета без
маршрута -- то есть ровно те правила, нарушение которых даёт неверное число, а
не падение. `drone_coverage_recalc` и `drone_useful_area` от Flask не зависят
намеренно, и этот набор пользуется тем, что они уже такие.

ЧТО ЗДЕСЬ ПРОВЕРЯЕТСЯ. Переходы: каждый тест делает `--apply`, меняет вход и
делает `--apply` ещё раз. Один прогон не ловит ничего из этого класса --
дефект в том, что ВТОРОЙ прогон объявляет строку `unchanged` и оставляет в
базе прежние значения.

DDL берётся из самой миграции, а не переписывается здесь. Копия разошлась бы с
оригиналом на первой же правке, и набор проверял бы схему, которой нет.

Ни одной настоящей координаты, ни одного настоящего идентификатора вылета:
поле -- квадрат вокруг круглых чисел, вылеты с 900001, всё помечено SYNTHETIC.

Запуск:
  python tools/test_drone_coverage_recalc.py
  python -m unittest tools.test_drone_coverage_recalc -v
"""

import json
import os
import shutil
import sqlite3
import sys
import tempfile
import unittest

from datetime import date, datetime

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

import drone_coverage_recalc as recalc  # noqa: E402
import drone_useful_area as ua  # noqa: E402
import migrate_drones_useful_area_001 as mig  # noqa: E402

# SYNTHETIC / NOT-REAL. Центр поля -- круглые числа, не координата настоящего.
LAT0 = 39.700000
LON0 = 64.400000
M_PER_DEG_LAT = 111320.0
M_PER_DEG_LON = 111320.0 * 0.7679

WORK_DAY = date(2026, 6, 5)
SYNTHETIC_UNIT_ID = 6


def at(east_m, north_m):
    return [LAT0 + north_m / M_PER_DEG_LAT, LON0 + east_m / M_PER_DEG_LON]


def square(half_m, east_m=0.0, north_m=0.0):
    corners = [at(east_m - half_m, north_m - half_m),
               at(east_m + half_m, north_m - half_m),
               at(east_m + half_m, north_m + half_m),
               at(east_m - half_m, north_m + half_m),
               at(east_m - half_m, north_m - half_m)]
    return {'type': 'Polygon',
            'coordinates': [[[point[1], point[0]] for point in corners]]}


def strip(east_centre, half_width_m, north_from, north_to):
    """Узкая полоса рядом с маршрутом.

    Её РАМКА достаёт до маршрута -- значит кандидат попадёт в короткий
    список, -- а сам полигон маршрута не содержит: доля точек внутри 0 %,
    ниже порога, и контур не назначается. Ровно тот вход, который переводит
    решение из CONTOUR_NOT_OFFERED в CONTOUR_NOT_MATCHED, не давая ни одному
    контуру победить.
    """
    corners = [at(east_centre - half_width_m, north_from),
               at(east_centre + half_width_m, north_from),
               at(east_centre + half_width_m, north_to),
               at(east_centre - half_width_m, north_to),
               at(east_centre - half_width_m, north_from)]
    return {'type': 'Polygon',
            'coordinates': [[[point[1], point[0]] for point in corners]]}


def pass_line(east_m, north_from, north_to, step_m=5.0):
    points = []
    north = north_from
    while north <= north_to + 1e-9:
        points.append(at(east_m, north))
        north += step_m
    return points


class RecalcCase(unittest.TestCase):
    """Временная база со схемой миграции и одной синтетической машиной."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix='drone_recalc_')
        self.db = os.path.join(self.tmp, 'throwaway.db')
        con = sqlite3.connect(self.db)
        try:
            con.executescript(
                'CREATE TABLE drone_units ('
                ' id INTEGER PRIMARY KEY, number INTEGER);'
                'CREATE TABLE drone_flights ('
                ' id INTEGER PRIMARY KEY AUTOINCREMENT,'
                ' dji_flight_id BIGINT, drone_unit_id INTEGER,'
                ' nickname_raw TEXT, started_at DATETIME, area_ha FLOAT);'
                'CREATE TABLE field_contours ('
                ' id INTEGER PRIMARY KEY AUTOINCREMENT, source TEXT,'
                ' external_id TEXT, name TEXT, geometry_geojson TEXT,'
                ' is_active BOOLEAN, bbox_min_lat FLOAT, bbox_max_lat FLOAT,'
                ' bbox_min_lng FLOAT, bbox_max_lng FLOAT);')
            # Схема расчёта -- из самой миграции, а не переписанная здесь.
            con.execute(mig.CREATE_DRONE_FLIGHT_ROUTES)
            con.execute(mig.CREATE_DRONE_COVERAGE_WORKS)
            for _name, statement in mig.INDEXES:
                con.execute(statement)
            con.execute('INSERT INTO drone_units (id, number) VALUES (?, ?)',
                        (SYNTHETIC_UNIT_ID, 6))
            con.commit()
        finally:
            con.close()

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    # ── Ввод ────────────────────────────────────────────────────────────────

    def add_flight(self, flight_id, area_ha=2.0, minute=0, mission=None):
        self.execute(
            'INSERT INTO drone_flights (dji_flight_id, drone_unit_id, '
            ' nickname_raw, started_at, area_ha) VALUES (?, ?, ?, ?, ?)',
            (flight_id, SYNTHETIC_UNIT_ID, 'SYNTHETIC-NICK',
             datetime(2026, 6, 5, 3, minute).strftime('%Y-%m-%d %H:%M:%S'),
             area_ha))
        if mission is not None:
            self.mission = mission
        return flight_id

    def add_route(self, flight_id, points, width=8.0, content=None,
                  mission_uuid=None):
        row_id = self.query('SELECT id FROM drone_flights '
                            ' WHERE dji_flight_id = ?', (flight_id,))[0][0]
        body = json.dumps(points, separators=(',', ':'))
        self.execute(
            'INSERT INTO drone_flight_routes (dji_flight_id, drone_flight_id,'
            ' point_count, points_json, spray_width_m, spray_width_recorded,'
            ' content_sha256, mission_uuid, source, received_at, updated_at,'
            ' ingest_count) VALUES (?, ?, ?, ?, ?, 1, ?, ?, ?, ?, ?, 1)',
            (flight_id, row_id, len(points), body, width,
             content or ('SYNTHETIC-CONTENT-%d' % flight_id), mission_uuid,
             'dji-ui-capture', '2026-06-05 03:00:00', '2026-06-05 03:00:00'))

    def add_contour(self, external_id, geojson):
        ring = geojson['coordinates'][0]
        lats = [point[1] for point in ring]
        lngs = [point[0] for point in ring]
        self.execute(
            'INSERT INTO field_contours (source, external_id, name,'
            ' geometry_geojson, is_active, bbox_min_lat, bbox_max_lat,'
            ' bbox_min_lng, bbox_max_lng) VALUES (?, ?, ?, ?, 1, ?, ?, ?, ?)',
            ('dji', external_id, 'SYNTHETIC field %s' % external_id,
             json.dumps(geojson), min(lats), max(lats), min(lngs), max(lngs)))

    # ── Доступ ──────────────────────────────────────────────────────────────

    def execute(self, sql, params=()):
        con = sqlite3.connect(self.db)
        try:
            con.execute(sql, params)
            con.commit()
        finally:
            con.close()

    def query(self, sql, params=()):
        con = sqlite3.connect(self.db)
        try:
            return con.execute(sql, params).fetchall()
        finally:
            con.close()

    def recalc(self, apply=True):
        return recalc.recalculate(self.db, WORK_DAY, WORK_DAY, apply=apply)

    def works(self):
        con = sqlite3.connect(self.db)
        con.row_factory = sqlite3.Row
        try:
            return con.execute(
                'SELECT * FROM drone_coverage_works '
                ' ORDER BY unit_key, group_index').fetchall()
        finally:
            con.close()

    def only_work(self):
        rows = self.works()
        self.assertEqual(len(rows), 1, 'expected one work, got %d' % len(rows))
        return rows[0]

    def summable_total(self):
        return sum(row['estimated_useful_area_ha'] or 0.0
                   for row in self.works()
                   if row['quality_status'] == ua.READY_ESTIMATE)


# ─── Семь обязательных переходов ─────────────────────────────────────────────

class Transitions(RecalcCase):
    """Каждый тест: --apply, изменение входа, ещё один --apply.

    [REASON]: дефект был именно во ВТОРОМ прогоне. Первый считал верно; второй
    объявлял строку `unchanged`, потому что отпечаток строился только по
    маршрутам, и в базе оставались прежние статус, счётчики и площадь.
    """

    def ready_single_work(self):
        self.add_contour('SYNTHETIC-CONTOUR-A', square(100.0))
        self.add_flight(900001, area_ha=2.0, minute=0)
        self.add_route(900001, pass_line(0.0, -80.0, 80.0))
        self.recalc()
        work = self.only_work()
        self.assertEqual(work['quality_status'], ua.READY_ESTIMATE)
        return work

    def test_1_ready_plus_a_new_unrouted_flight_becomes_partial(self):
        """READY + вылет без маршрута -> PARTIAL_DATA и вон из суммы."""
        before = self.ready_single_work()
        self.assertGreater(self.summable_total(), 0.0)
        fingerprint_before = before['inputs_fingerprint']

        self.add_flight(900002, area_ha=5.0, minute=1)   # маршрута нет
        summary = self.recalc()

        self.assertEqual(summary['updated'], 1,
                         'the work must be recomputed, not called unchanged')
        work = self.only_work()
        self.assertEqual(work['quality_status'], ua.PARTIAL_DATA)
        self.assertEqual(work['quality_reason'],
                         ua.REASON_SOME_FLIGHTS_WITHOUT_ROUTE)
        self.assertIsNone(work['estimated_useful_area_ha'])
        self.assertNotEqual(work['inputs_fingerprint'], fingerprint_before)
        self.assertEqual(self.summable_total(), 0.0,
                         'an incomplete work must not stay in the total')
        self.assertEqual(work['flights_total'], 2)
        self.assertEqual(work['flights_without_route'], 1)

    def test_2_partial_becomes_ready_once_the_route_arrives(self):
        """PARTIAL -> маршрут доехал -> READY_ESTIMATE."""
        self.add_contour('SYNTHETIC-CONTOUR-A', square(100.0))
        self.add_flight(900001, area_ha=2.0, minute=0)
        self.add_route(900001, pass_line(0.0, -80.0, 80.0))
        self.add_flight(900002, area_ha=2.0, minute=1)
        self.recalc()
        self.assertEqual(self.only_work()['quality_status'], ua.PARTIAL_DATA)

        self.add_route(900002, pass_line(4.0, -80.0, 80.0))
        summary = self.recalc()

        self.assertEqual(summary['updated'], 1)
        work = self.only_work()
        self.assertEqual(work['quality_status'], ua.READY_ESTIMATE)
        self.assertIsNotNone(work['estimated_useful_area_ha'])
        self.assertEqual(work['routes_total'], 2)
        self.assertEqual(work['flights_without_route'], 0)

    def test_3_data_unavailable_row_follows_its_flight_count(self):
        """DATA_UNAVAILABLE: второй безмаршрутный вылет двигает счётчики."""
        self.add_flight(900001, area_ha=2.0, minute=0)
        self.recalc()
        work = self.only_work()
        self.assertEqual(work['quality_status'], ua.DATA_UNAVAILABLE)
        self.assertEqual(work['flights_total'], 1)
        self.assertAlmostEqual(work['dji_area_ha'], 2.0, places=4)

        self.add_flight(900002, area_ha=3.0, minute=1)
        summary = self.recalc()

        self.assertEqual(summary['updated'], 1)
        work = self.only_work()
        self.assertEqual(work['flights_total'], 2)
        self.assertAlmostEqual(work['dji_area_ha'], 5.0, places=4)
        self.assertEqual(work['quality_status'], ua.DATA_UNAVAILABLE)

    def test_4_a_changed_dji_area_updates_the_stored_dji_area_only(self):
        """Изменилась только `area_ha` -> обновилась `dji_area_ha`.

        И полезная площадь при этом НЕ подменяется площадью DJI: это два
        разных показателя, и второй остаётся тем, чем был.
        """
        before = self.ready_single_work()
        useful_before = before['estimated_useful_area_ha']
        self.assertAlmostEqual(before['dji_area_ha'], 2.0, places=4)

        self.execute('UPDATE drone_flights SET area_ha = ? '
                     ' WHERE dji_flight_id = ?', (7.5, 900001))
        summary = self.recalc()

        self.assertEqual(summary['updated'], 1)
        work = self.only_work()
        self.assertAlmostEqual(work['dji_area_ha'], 7.5, places=4)
        self.assertAlmostEqual(work['estimated_useful_area_ha'],
                               useful_before, places=6,
                               msg='the useful area must not follow the DJI '
                                   'area -- they are two different figures')
        self.assertNotAlmostEqual(work['estimated_useful_area_ha'], 7.5,
                                  places=4)

    def test_5_a_corrupted_route_under_the_same_hash_becomes_route_invalid(self):
        """Испорченный `points_json` при ПРЕЖНЕМ `content_sha256`.

        [REASON]: самый неприятный случай. Хеш содержимого не изменился, и
        отпечаток по маршрутам остался прежним -- строка признавалась
        `unchanged` и оставалась READY_ESTIMATE, хотя геометрии в базе больше
        нет. Ловит его либо описание вылета с его СОСТОЯНИЕМ маршрута, либо
        сравнение сохранённых значений: обе сети закрывают этот переход.
        """
        self.ready_single_work()
        self.execute("UPDATE drone_flight_routes SET points_json = ? "
                     " WHERE dji_flight_id = ?", ('{not json', 900001))
        summary = self.recalc()

        self.assertEqual(summary['updated'], 1)
        work = self.only_work()
        self.assertEqual(work['quality_status'], ua.ROUTE_INVALID)
        self.assertNotEqual(work['quality_status'], ua.READY_ESTIMATE)
        self.assertIsNone(work['estimated_useful_area_ha'])
        self.assertEqual(self.summable_total(), 0.0)

    def test_5b_valid_but_different_geometry_under_the_same_hash(self):
        """Маршрут остался ГОДНЫМ, точки другие, `content_sha256` прежний.

        [REASON]: острее пятого. Там порча переводила маршрут в INVALID, то
        есть меняла СОСТАВ группы, и прежний узкий отпечаток это замечал. Тут
        не меняется ничего, что он видел: тот же вылет, то же состояние
        PRESENT, тот же хеш содержимого -- а геометрия другая, и площадь тоже.
        Строка признавалась `unchanged`, и в базе оставалась площадь,
        посчитанная по прежним точкам.

        Ловит это сравнение сохранённых значений: отпечаток по входам такой
        случай поймать не может по построению -- вход соврал о себе сам.
        """
        before = self.ready_single_work()
        area_before = before['estimated_useful_area_ha']
        self.assertIsNotNone(area_before)
        sha_before = self.query('SELECT content_sha256 FROM '
                                'drone_flight_routes WHERE dji_flight_id = ?',
                                (900001,))[0][0]

        longer = json.dumps(pass_line(0.0, -80.0, 200.0),
                            separators=(',', ':'))
        self.execute('UPDATE drone_flight_routes SET points_json = ? '
                     ' WHERE dji_flight_id = ?', (longer, 900001))
        # Хеш НАМЕРЕННО не трогаем: он лжёт о содержимом.
        self.assertEqual(
            self.query('SELECT content_sha256 FROM drone_flight_routes '
                       ' WHERE dji_flight_id = ?', (900001,))[0][0],
            sha_before)

        summary = self.recalc()

        self.assertEqual(summary['updated'], 1,
                         'the stored row disagrees with a fresh computation '
                         'and must be rewritten')
        work = self.only_work()
        self.assertGreater(work['estimated_useful_area_ha'], area_before,
                           'a longer work pass must give a larger area')

    def test_6_a_contour_status_change_with_no_chosen_uuid_is_saved(self):
        """NOT_OFFERED -> NOT_MATCHED: `contour_uuid` в обоих случаях None.

        [REASON]: отпечаток по одному ПОБЕДИТЕЛЮ такой переход не замечал --
        победителя нет ни там, ни там. Виден он только по входу: сколько
        контуров попало в короткий список.
        """
        self.add_flight(900001, area_ha=2.0, minute=0)
        self.add_route(900001, pass_line(0.0, -80.0, 80.0))
        self.recalc()
        work = self.only_work()
        self.assertEqual(work['quality_status'], ua.CONTOUR_NOT_MATCHED)
        self.assertEqual(work['quality_reason'],
                         ua.REASON_CONTOUR_NOT_OFFERED)
        self.assertIsNone(work['field_contour_id'])

        # Кандидат появляется, победителем не становится: рамка достаёт до
        # маршрута, полигон его не содержит (0 % точек внутри).
        self.add_contour('SYNTHETIC-CONTOUR-STRIP',
                         strip(50.0, 1.0, -200.0, 1500.0))
        summary = self.recalc()

        self.assertEqual(summary['updated'], 1,
                         'the candidate set changed; the row must follow')
        work = self.only_work()
        self.assertEqual(work['quality_reason'],
                         ua.REASON_CONTOUR_NOT_MATCHED)
        self.assertNotEqual(work['quality_reason'],
                            ua.REASON_CONTOUR_NOT_OFFERED)

    def test_7_an_unchanged_repeat_stays_unchanged(self):
        """Отрицательный контроль ко всем шести.

        Без него каждый из них был бы зелёным и у кода, который объявляет
        строку изменившейся ВСЕГДА, -- а такой код переписывал бы `computed_at`
        каждым прогоном и сделал бы `unchanged` бессмысленным.
        """
        self.ready_single_work()
        before = self.query('SELECT id, inputs_fingerprint, computed_at, '
                            '       quality_status, dji_area_ha '
                            '  FROM drone_coverage_works ORDER BY id')
        summary = self.recalc()

        self.assertEqual(summary['unchanged'], 1)
        self.assertEqual(summary['updated'], 0)
        self.assertEqual(summary['inserted'], 0)
        self.assertEqual(summary['deleted'], 0)
        self.assertEqual(
            self.query('SELECT id, inputs_fingerprint, computed_at, '
                       '       quality_status, dji_area_ha '
                       '  FROM drone_coverage_works ORDER BY id'), before)


# ─── Отпечаток сам по себе ───────────────────────────────────────────────────

class FingerprintInputs(unittest.TestCase):
    """Чистые проверки: что именно меняет отпечаток, а что нет."""

    def base(self, **overrides):
        entry = dict(flight_id=900001, route_state='PRESENT',
                     content_sha256='SYNTHETIC-CONTENT', area_ha=2.0,
                     mission_uuid=None)
        entry.update(overrides)
        return ua.flight_input(**entry)

    def fingerprint(self, entries, **kwargs):
        return ua.inputs_fingerprint(entries, **kwargs)

    def test_each_flight_field_changes_the_fingerprint(self):
        reference = self.fingerprint([self.base()])
        for field, value in (('flight_id', 900002),
                             ('route_state', 'ABSENT'),
                             ('route_state', 'INVALID'),
                             ('content_sha256', 'SYNTHETIC-OTHER'),
                             ('area_ha', 3.0),
                             ('mission_uuid', 'SYNTHETIC-MISSION')):
            self.assertNotEqual(
                self.fingerprint([self.base(**{field: value})]), reference,
                '%s=%r left the fingerprint unchanged' % (field, value))

    def test_adding_a_flight_changes_the_fingerprint(self):
        one = self.fingerprint([self.base()])
        two = self.fingerprint([self.base(),
                                self.base(flight_id=900002,
                                          route_state='ABSENT',
                                          content_sha256=None)])
        self.assertNotEqual(one, two)

    def test_the_order_of_flights_does_not_matter(self):
        first = self.base()
        second = self.base(flight_id=900002)
        self.assertEqual(self.fingerprint([first, second]),
                         self.fingerprint([second, first]))

    def test_an_integer_and_a_float_area_are_the_same_input(self):
        """SQLite возвращает REAL; 2 и 2.0 -- одно значение, не два."""
        self.assertEqual(self.fingerprint([self.base(area_ha=2)]),
                         self.fingerprint([self.base(area_ha=2.0)]))

    def test_the_candidate_set_changes_the_fingerprint(self):
        entries = [self.base()]
        none_offered = self.fingerprint(entries, contour_candidates=[])
        one_offered = self.fingerprint(
            entries, contour_candidates=[('SYNTHETIC-A', square(100.0))])
        two_offered = self.fingerprint(
            entries, contour_candidates=[('SYNTHETIC-A', square(100.0)),
                                         ('SYNTHETIC-B', square(100.0))])
        self.assertNotEqual(none_offered, one_offered)
        self.assertNotEqual(one_offered, two_offered)
        self.assertNotEqual(none_offered, two_offered)

    def test_a_candidate_geometry_change_changes_the_fingerprint(self):
        entries = [self.base()]
        first = self.fingerprint(
            entries, contour_candidates=[('SYNTHETIC-A', square(100.0))])
        second = self.fingerprint(
            entries, contour_candidates=[('SYNTHETIC-A', square(40.0))])
        self.assertNotEqual(first, second)

    def test_the_candidate_order_does_not_matter(self):
        entries = [self.base()]
        pair = [('SYNTHETIC-A', square(100.0)), ('SYNTHETIC-B', square(50.0))]
        self.assertEqual(self.fingerprint(entries, contour_candidates=pair),
                         self.fingerprint(entries,
                                          contour_candidates=pair[::-1]))

    def test_the_fingerprint_is_a_sha256_and_leaks_nothing(self):
        digest = self.fingerprint(
            [self.base(mission_uuid='SYNTHETIC-MISSION-VALUE')],
            contour_key='SYNTHETIC-CONTOUR-A',
            contour_geometry=square(100.0),
            contour_candidates=[('SYNTHETIC-CONTOUR-A', square(100.0))])
        self.assertEqual(len(digest), 64)
        self.assertTrue(all(char in '0123456789abcdef' for char in digest))
        for fragment in ('SYNTHETIC', '900001', str(LAT0), str(LON0),
                         '39.7', '64.4'):
            self.assertNotIn(fragment, digest)


if __name__ == '__main__':
    unittest.main(verbosity=2)
