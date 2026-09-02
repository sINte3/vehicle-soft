# -*- coding: utf-8 -*-
"""Тесты drone_collector/area_study.py -- DJI-AREA-48H.

Ни сети, ни браузера, ни базы. Проверок намеренно немного и каждая отвечает
на один вопрос задания; главный результат спринта -- живой разбор, а не число
тестов.

**У каждого ключевого правила есть отрицательный контроль.** Проверка,
дающая одинаковый ответ при верном и неверном коде, проверкой не является, и
поэтому здесь рядом с «полоса 100 x 10 даёт 0.1 га» стоит «круглые торцы дали
бы 0.1079, и метод их не рисует», а рядом с «в отчёте нет приватного» --
«подложенный настоящий ID отчёт заворачивает».
"""

import json
import math
import os
import struct
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(
    os.path.abspath(__file__)))))

from drone_collector.area_study import (  # noqa: E402
    DATA_UNAVAILABLE, DISPROVED, F_LARGER_WORK, F_NO_WIDTH, F_OVERLAP,
    CONTOUR_AMBIGUOUS, CONTOUR_MATCHED, CONTOUR_NOT_MATCHED,
    CONTOUR_NOT_OFFERED, EXIT_STUDY_EMPTY, EXIT_STUDY_OK,
    EXIT_STUDY_UNCONFIRMED, F_REPEAT_WITHIN_WORK, GROUP_SINGLE, GROUP_SPATIAL,
    Grid, LocalPlane, MISSION_ABSENT, MISSION_SHARED, NOT_PROVEN, PROVEN,
    PATTERN_CONSTANT, PATTERN_SWITCHING, SEG_GAP, SEG_OUTSIDE,
    SEG_WORK, SOURCE_INSUFFICIENT, ShareableLeak, StudyParams, SUPPORTED,
    USE_IN_CONTOUR_WORK_PASS_UNION, USE_SPRAY_STATE_CLIPPED_UNION,
    WIDTH_OK, assert_shareable, candidate_contours, choose_contour,
    classify_segments, choose_status, coverage_once, coverage_with_uncertainty,
    day_of, describe_series, group_flights, live_run_verdict, mission_state,
    plane_for, private_strings, render_markdown, rings_from_geojson, run_study,
    segment_totals, split_by_day, study_exit_code, unknown_point_values,
    write_reports)

# Синтетика: координаты выдуманы и лежат в районе работ только затем, чтобы
# перевод в метры шёл на настоящей широте.
LAT0 = 40.0800
LNG0 = 64.6300
FAKE_FLIGHT_ID = 900000001
FINE = StudyParams(cell_m=0.1, min_pass_m=10.0)


# ─── Помощники ───────────────────────────────────────────────────────────────

def line(x0, y0, x1, y1, step=10.0):
    """Ломаная из точек в метрах, шаг `step`."""
    count = max(1, int(round(math.hypot(x1 - x0, y1 - y0) / step)))
    return [(x0 + (x1 - x0) * index / count,
             y0 + (y1 - y0) * index / count) for index in range(count + 1)]


def square(x0, y0, side):
    return [(x0, y0), (x0 + side, y0), (x0 + side, y0 + side), (x0, y0 + side)]


def swath_ha(tracks, rings=None, params=FINE):
    return coverage_once(tracks, rings, params, params.cell_m).swath_all_ha


def segs(points, params=FINE, rings=None):
    return classify_segments(points, params, rings)


def latlon_line(plane, x0, y0, x1, y1, step=10.0):
    return [plane.latlon(x, y) for x, y in line(x0, y0, x1, y1, step)]


def geojson_square(plane, x0, y0, side):
    ring = [plane.latlon(x, y) for x, y in square(x0, y0, side)]
    ring.append(ring[0])
    return {'type': 'FeatureCollection', 'features': [{
        'type': 'Feature', 'properties': {'funcType': 'PlantZone'},
        'geometry': {'type': 'Polygon',
                     'coordinates': [[[lon, lat] for lat, lon in ring]]}}]}


def flight(flight_id, points, width=10.0, area_m2=1000.0, mission=None,
           nickname='9 Fixture', day='2026-06-05', start_ms=1780670376000,
           candidates=None, unknown=None):
    return {'flight_id': flight_id, 'points': [list(p) for p in points],
            'spray_width_m': width, 'work_area_m2': area_m2,
            'mission_uuid': mission, 'nickname': nickname, 'day': day,
            'start_ms': start_ms, 'end_ms': start_ms + 500000,
            'contour_candidates': list(candidates or ()),
            'unknown_values': unknown or {}}


# ─── Геометрия ───────────────────────────────────────────────────────────────

class TestStraightSwath(unittest.TestCase):
    """Прямая 100 м при ширине 10 м -- ровно 0.1 га, и ни метром больше."""

    def test_hundred_by_ten_is_a_tenth_of_a_hectare(self):
        self.assertEqual(swath_ha([(segs(line(0, 0, 100, 0)), 5.0)]), 0.1)

    def test_round_caps_would_have_added_area_and_do_not(self):
        # Отрицательный контроль на плоские торцы. Круглые дорисовали бы два
        # полукруга радиусом 5 м -- 78.54 м2, то есть 0.1079 га. Если метод
        # когда-нибудь вернётся к капсулам, эта проверка упадёт.
        round_caps = (1000.0 + math.pi * 25.0) / 10000.0
        self.assertAlmostEqual(round_caps, 0.1079, places=4)
        self.assertNotAlmostEqual(swath_ha([(segs(line(0, 0, 100, 0)), 5.0)]),
                                  round_caps, places=3)

    def test_area_is_not_computed_in_degrees(self):
        # Отрицательный контроль на проекцию: та же полоса, заданная в
        # градусах, дала бы число, отличающееся на четыре порядка.
        plane = LocalPlane(LAT0, LNG0)
        metres = plane.xy(LAT0, LNG0 + 0.001)[0]
        self.assertGreater(metres, 50.0)
        self.assertLess(metres, 120.0)


class TestUnionNotSum(unittest.TestCase):

    def test_two_identical_passes_do_not_double_the_area(self):
        track = (segs(line(0, 0, 100, 0)), 5.0)
        self.assertEqual(swath_ha([track]), 0.1)
        self.assertEqual(swath_ha([track, track]), 0.1)

    def test_partly_overlapping_passes_are_united_geometrically(self):
        first = (segs(line(0, 0, 100, 0)), 5.0)
        second = (segs(line(0, 5, 100, 5)), 5.0)
        # Полосы по 10 м с шагом 5 м: объединение это лента 15 м, а не 20.
        self.assertEqual(swath_ha([first, second]), 0.15)
        self.assertNotEqual(swath_ha([first, second]), 0.2)


class TestContourClipping(unittest.TestCase):

    def setUp(self):
        self.field = [square(0.0, -20.0, 40.0)]

    def test_a_pass_outside_the_field_is_excluded(self):
        outside = (segs(line(200, 200, 300, 200)), 5.0)
        result = coverage_once([outside], self.field, FINE, FINE.cell_m)
        self.assertEqual(result.clipped_all_ha, 0.0)

    def test_entering_and_leaving_the_field_is_clipped(self):
        crossing = (segs(line(-50, 0, 150, 0)), 5.0)
        result = coverage_once([crossing], self.field, FINE, FINE.cell_m)
        self.assertEqual(result.swath_all_ha, 0.2)     # 200 м x 10 м
        self.assertEqual(result.clipped_all_ha, 0.04)  # 40 м x 10 м

    def test_a_purely_ferry_flight_yields_zero_useful_area(self):
        ferry = segs(line(500, 500, 900, 500), FINE, self.field)
        result = coverage_once([(ferry, 5.0)], self.field, FINE, FINE.cell_m)
        self.assertEqual(result.clipped_work_ha, 0.0)
        self.assertTrue(all(segment.reason == SEG_OUTSIDE
                            for segment in ferry))

    def test_a_broken_polygon_is_not_used_silently(self):
        plane = LocalPlane(LAT0, LNG0)
        # Несимметричная восьмёрка: площадь по формуле положительна и неверна.
        ring = [plane.latlon(0, 0), plane.latlon(100, 100),
                plane.latlon(100, 0), plane.latlon(0, 60)]
        ring.append(ring[0])
        document = {'type': 'Polygon',
                    'coordinates': [[[lon, lat] for lat, lon in ring]]}
        rings, area, reasons = rings_from_geojson(document, plane)
        self.assertIsNone(rings)
        self.assertIsNone(area)
        self.assertTrue(reasons)


class TestRasterAgreesWithTheProjectSphericalFormula(unittest.TestCase):
    """Растр против `geometry.ring_area_m2` -- независимый контроль.

    [REASON]: это самая сильная проверка во всём наборе, потому что две
    величины считаются РАЗНЫМ кодом, написанным для разных задач и разными
    людьми: сферическая формула по избытку на сфере, живущая в приёмнике
    контуров, и растровая заливка в местной касательной плоскости, написанная
    здесь. Ошибка в проекции, в порядке широты и долготы, в радиусе Земли, в
    заливке многоугольника или в размере клетки сдвинула бы одну из них и не
    сдвинула бы другую. Совпадение до сотых долей процента ошибкой быть не
    может, а расхождение сразу показывает, какая из двух неверна.
    """

    def test_the_two_areas_agree_to_a_hundredth_of_a_percent(self):
        plane = LocalPlane(LAT0, LNG0)
        document = geojson_square(plane, -20.0, -20.0, 440.0)
        rings, spherical_ha, reasons = rings_from_geojson(document, plane)
        self.assertEqual(reasons, [])
        raster = coverage_once([], rings, FINE, FINE.cell_m).contour_ha
        self.assertAlmostEqual(raster, spherical_ha, places=2)
        self.assertLess(abs(raster - spherical_ha) / spherical_ha * 100.0, 0.01)

    def test_a_wrong_earth_radius_would_have_shown_up(self):
        # Отрицательный контроль: если бы плоскость считала метры по другому
        # радиусу, согласия бы не было. Проверяется тем, что расхождение
        # ЧУВСТВИТЕЛЬНО -- сдвиг радиуса на процент сдвигает площадь заметно.
        plane = LocalPlane(LAT0, LNG0)
        document = geojson_square(plane, -20.0, -20.0, 440.0)
        rings, spherical_ha, _reasons = rings_from_geojson(document, plane)
        stretched = [[(x * 1.01, y * 1.01) for x, y in ring] for ring in rings]
        wrong = coverage_once([], stretched, FINE, FINE.cell_m).contour_ha
        self.assertGreater(abs(wrong - spherical_ha) / spherical_ha * 100.0,
                           1.0)


class TestSegmentRules(unittest.TestCase):

    def test_a_recording_gap_never_becomes_a_swath(self):
        points = [(0.0, 0.0), (500.0, 0.0)]
        segments = segs(points)
        self.assertEqual([segment.reason for segment in segments], [SEG_GAP])
        self.assertEqual(swath_ha([(segments, 5.0)]), 0.0)

    def test_a_short_connector_is_not_a_work_pass(self):
        params = StudyParams(cell_m=0.2, min_pass_m=50.0)
        segments = classify_segments(line(0, 0, 30, 0), params)
        self.assertTrue(all(not segment.is_work for segment in segments))

    def test_turns_break_a_run_and_the_totals_add_up(self):
        points = line(0, 0, 100, 0) + line(100, 0, 100, 100, 10.0)[1:]
        segments = segs(points)
        totals = segment_totals(segments)
        self.assertEqual(sum(bucket['segments'] for bucket in totals.values()),
                         len(segments))
        self.assertGreater(totals[SEG_WORK]['length_m'], 150.0)


class TestCoordinateOrder(unittest.TestCase):

    def test_latitude_and_longitude_are_not_swapped(self):
        plane = LocalPlane(LAT0, LNG0)
        east = plane.xy(LAT0, LNG0 + 0.01)
        north = plane.xy(LAT0 + 0.01, LNG0)
        self.assertGreater(east[0], 0.0)
        self.assertAlmostEqual(east[1], 0.0, places=6)
        self.assertGreater(north[1], 0.0)
        self.assertAlmostEqual(north[0], 0.0, places=6)
        # На широте Бухары градус долготы КОРОЧЕ градуса широты; перестановка
        # даёт другую фигуру, и это видно числом, а не на глаз.
        self.assertLess(east[0], north[1])

    def test_a_swapped_route_gives_a_different_area(self):
        plane = LocalPlane(LAT0, LNG0)
        straight = latlon_line(plane, 0, 0, 300, 0)
        swapped = [(lon, lat) for lat, lon in straight]
        honest = swath_ha([(segs(plane.project(straight)), 5.0)])
        wrong = swath_ha([(segs(plane.project(swapped)), 5.0)])
        self.assertNotAlmostEqual(honest, wrong, places=3)


class TestUncertaintyIsPublished(unittest.TestCase):

    def test_the_two_grids_agree_within_a_percent_on_a_healthy_shape(self):
        track = (segs(line(0, 0, 400, 0)), 3.5)
        _fine, _coarse, uncertainty = coverage_with_uncertainty(
            [track], None, StudyParams(cell_m=0.25))
        self.assertLess(uncertainty['swath_all_ha'], 1.0)


# ─── Ширина ──────────────────────────────────────────────────────────────────

class TestMissingWidth(unittest.TestCase):

    def _group(self, width):
        plane = LocalPlane(LAT0, LNG0)
        flights = [flight(FAKE_FLIGHT_ID, latlon_line(plane, 0, 0, 200, 0),
                          width=width)]
        _private, shareable = run_study({'flights': flights}, FINE)
        return shareable['works'][0]['rows'][0]

    def test_absent_width_is_reported_and_never_substituted(self):
        for width in (None, -1.0, 0.0, float('nan'), float('inf')):
            row = self._group(width)
            self.assertEqual(row['width_status'], DATA_UNAVAILABLE,
                             'width %r must not be replaced' % (width,))
            self.assertIsNone(row['spray_width_m'])
            self.assertIsNone(row['own_route_swath_ha'])

    def test_a_usable_width_is_used(self):
        row = self._group(10.0)
        self.assertEqual(row['width_status'], WIDTH_OK)
        self.assertEqual(row['spray_width_m'], 10.0)
        self.assertGreater(row['own_route_swath_ha'], 0.0)

    def test_a_neighbour_width_is_not_borrowed(self):
        plane = LocalPlane(LAT0, LNG0)
        first = flight(1, latlon_line(plane, 0, 0, 200, 0), width=10.0)
        second = flight(2, latlon_line(plane, 0, 50, 200, 50), width=None)
        _private, shareable = run_study({'flights': [first, second]}, FINE)
        rows = {row['flight']: row for work in shareable['works']
                for row in work['rows']}
        statuses = sorted(row['width_status'] for row in rows.values())
        self.assertEqual(statuses, [DATA_UNAVAILABLE, WIDTH_OK])


# ─── Группировка ─────────────────────────────────────────────────────────────

class TestGrouping(unittest.TestCase):

    def test_a_shared_mission_uuid_does_not_join_distant_fields(self):
        # Блокер 3. Раньше `mission_uuid` был самым сильным правилом, и два
        # маршрута в разных концах района складывались в одну полезную
        # площадь, которой не существует.
        plane = LocalPlane(LAT0, LNG0)
        parts = [flight(1, latlon_line(plane, 0, 0, 200, 0), mission='M-1'),
                 flight(2, latlon_line(plane, 20000, 20000, 20200, 20000),
                        mission='M-1')]
        groups = group_flights(parts)
        self.assertEqual(len(groups), 2)
        for basis, _members in groups:
            self.assertEqual(basis, GROUP_SINGLE)

    def test_a_shared_mission_uuid_on_overlapping_routes_is_only_an_attribute(self):
        plane = LocalPlane(LAT0, LNG0)
        parts = [flight(1, latlon_line(plane, 0, 0, 200, 0), mission='M-1'),
                 flight(2, latlon_line(plane, 0, 8, 200, 8), mission='M-1')]
        groups = group_flights(parts)
        self.assertEqual(len(groups), 1)
        # Основание группы -- пространственное, а не идентификатор задания.
        self.assertEqual(groups[0][0], GROUP_SPATIAL)
        self.assertEqual(mission_state(groups[0][1]), MISSION_SHARED)

    def test_same_machine_day_and_overlapping_routes_join(self):
        plane = LocalPlane(LAT0, LNG0)
        parts = [flight(1, latlon_line(plane, 0, 0, 200, 0)),
                 flight(2, latlon_line(plane, 0, 8, 200, 8))]
        groups = group_flights(parts)
        self.assertEqual(len(groups), 1)
        self.assertEqual(groups[0][0], GROUP_SPATIAL)
        self.assertEqual(mission_state(groups[0][1]), MISSION_ABSENT)

    def test_distant_routes_stay_apart(self):
        plane = LocalPlane(LAT0, LNG0)
        parts = [flight(1, latlon_line(plane, 0, 0, 200, 0)),
                 flight(2, latlon_line(plane, 20000, 20000, 20200, 20000))]
        self.assertEqual(len(group_flights(parts)), 2)

    # ── Работа -- связная компонента, а не «первый подошедший кластер» ──────

    def _bridge_parts(self):
        """A внизу, B вверху, между собой НЕ пересекаются; C накрывает обоих."""
        plane = LocalPlane(LAT0, LNG0)
        return {
            'A': flight(1, latlon_line(plane, 0, 0, 200, 0)),
            'B': flight(2, latlon_line(plane, 2000, 0, 2200, 0)),
            'C': flight(3, latlon_line(plane, 100, 0, 2100, 0)),
        }

    def _ids(self, groups):
        return sorted(sorted(member['flight_id'] for member in members)
                      for _basis, members in groups)

    def test_a_and_b_alone_are_two_works(self):
        """Предпосылка моста. Без неё следующая проверка ничего не значит."""
        parts = self._bridge_parts()
        self.assertEqual(self._ids(group_flights([parts['A'], parts['B']])),
                         [[1], [2]])

    def test_a_bridging_route_merges_two_existing_clusters(self):
        """C пересекает и A, и B -- значит это ОДНА работа A+B+C.

        [REASON]: прежний алгоритм клал маршрут в ПЕРВЫЙ пересечённый кластер
        и останавливался, поэтому два уже заведённых кластера не сливались
        никогда: ответом было [[1, 3], [2]]. Перекрытие A и C при этом
        считалось дважды -- ровно та ошибка, ради которой группировка и
        делается.
        """
        parts = self._bridge_parts()
        groups = group_flights([parts['A'], parts['B'], parts['C']])
        self.assertEqual(self._ids(groups), [[1, 2, 3]])
        self.assertEqual(len(groups), 1)
        self.assertEqual(groups[0][0], GROUP_SPATIAL)

    def test_the_grouping_does_not_depend_on_the_input_order(self):
        import itertools
        parts = self._bridge_parts()
        seen = set()
        for order in itertools.permutations(['A', 'B', 'C']):
            groups = group_flights([parts[name] for name in order])
            seen.add(repr(self._ids(groups)))
        self.assertEqual(len(seen), 1,
                         'the grouping changed with the input order: %s'
                         % sorted(seen))
        self.assertEqual(seen.pop(), repr([[1, 2, 3]]))

    def test_a_bridge_does_not_join_genuinely_distant_routes(self):
        """Отрицательный контроль: связная компонента не значит «все вместе».

        Без неё проверка моста прошла бы и на коде, складывающем в одну работу
        вообще всё, что случилось в один день.
        """
        plane = LocalPlane(LAT0, LNG0)
        parts = self._bridge_parts()
        far = flight(4, latlon_line(plane, 40000, 40000, 40200, 40000))
        groups = group_flights([parts['A'], parts['B'], parts['C'], far])
        self.assertEqual(self._ids(groups), [[1, 2, 3], [4]])

    def test_mission_uuid_still_takes_no_part_in_the_grouping(self):
        """Мост не протаскивает `mission_uuid` обратно в правило."""
        plane = LocalPlane(LAT0, LNG0)
        # Один и тот же mission_uuid на двух разнесённых маршрутах.
        parts = [flight(1, latlon_line(plane, 0, 0, 200, 0), mission='M-1'),
                 flight(2, latlon_line(plane, 40000, 40000, 40200, 40000),
                        mission='M-1')]
        self.assertEqual(self._ids(group_flights(parts)), [[1], [2]])
        # И наоборот: разные mission_uuid не разрывают одну работу.
        joined = [flight(1, latlon_line(plane, 0, 0, 200, 0), mission='M-1'),
                  flight(2, latlon_line(plane, 0, 8, 200, 8), mission='M-2')]
        self.assertEqual(self._ids(group_flights(joined)), [[1, 2]])

    def test_the_parts_of_one_work_are_measured_once_not_added_up(self):
        plane = LocalPlane(LAT0, LNG0)
        # Две одинаковые половины одной работы: сумма полос была бы вдвое
        # больше объединения.
        path = latlon_line(plane, 0, 0, 200, 0)
        parts = [flight(1, path, mission='M-1', area_m2=2000.0),
                 flight(2, path, mission='M-1', area_m2=2000.0)]
        _private, shareable = run_study({'flights': parts}, FINE)
        work = shareable['works'][0]
        self.assertEqual(work['flight_count'], 2)
        self.assertEqual(work['whole_route_swath_union_ha'], 0.2)
        self.assertEqual(work['sum_of_independent_flight_swaths_ha'], 0.4)
        self.assertEqual(work['dji_row_area_sum_ha'], 0.4)


# ─── Неизвестные поля ────────────────────────────────────────────────────────

def _varint(value):
    out = bytearray()
    while True:
        byte = value & 0x7F
        value >>= 7
        if value:
            out.append(byte | 0x80)
        else:
            out.append(byte)
            return bytes(out)


def _tag(number, wire):
    return _varint((number << 3) | wire)


def _bytes_field(number, raw):
    return _tag(number, 2) + _varint(len(raw)) + raw


def _point(lat, lng, third=None):
    body = (_tag(1, 1) + struct.pack('<d', lat)
            + _tag(2, 1) + struct.pack('<d', lng))
    if third is not None:
        body += _tag(3, 0) + _varint(third)
    return _bytes_field(1, body)


def _body(points, flight_id=FAKE_FLIGHT_ID):
    record = b''.join(_point(*item) for item in points)
    record += _tag(2, 0) + _varint(flight_id)
    payload = _bytes_field(1, record)
    return (_tag(1, 0) + _varint(200)
            + _bytes_field(2, b'Success.')
            + _bytes_field(3, payload))


class TestUnknownPointField(unittest.TestCase):

    def test_the_values_are_read_but_only_into_the_private_layer(self):
        body = _body([(LAT0, LNG0, 1), (LAT0, LNG0 + 0.001, 1),
                      (LAT0, LNG0 + 0.002, 0)])
        values = unknown_point_values(body)
        self.assertEqual(values[FAKE_FLIGHT_ID]['3:0'], [1, 1, 0])

    def test_the_shape_of_the_series_is_described_without_its_values(self):
        described = describe_series([1, 1, 1, 1])
        self.assertEqual(described['pattern'], PATTERN_CONSTANT)
        self.assertFalse(described['changes'])
        described = describe_series([0, 1, 0, 1, 0, 1])
        self.assertEqual(described['pattern'], PATTERN_SWITCHING)
        self.assertEqual(described['distinct'], 2)
        self.assertNotIn('values', described)
        self.assertNotIn('min', described)

    def test_an_unknown_field_does_not_change_the_area(self):
        plane = LocalPlane(LAT0, LNG0)
        path = latlon_line(plane, 0, 0, 200, 0)
        plain = flight(1, path)
        marked = flight(1, path, unknown={'3:0': [1] * len(path)})
        first = run_study({'flights': [plain]}, FINE)[1]
        second = run_study({'flights': [marked]}, FINE)[1]
        self.assertEqual(first['works'][0]['whole_route_swath_union_ha'],
                         second['works'][0]['whole_route_swath_union_ha'])

    def test_the_semantics_stay_unknown(self):
        plane = LocalPlane(LAT0, LNG0)
        path = latlon_line(plane, 0, 0, 200, 0)
        marked = flight(1, path, unknown={'3:0': [1] * len(path)})
        _private, shareable = run_study({'flights': [marked]}, FINE)
        field = shareable['works'][0]['rows'][0]['unknown_point_fields']
        self.assertEqual(field['field_3_wire_0']['semantics'],
                         'UNKNOWN_SEMANTICS')
        self.assertEqual(shareable['spray_state_proof'], 'NOT_ESTABLISHED')


# ─── Уровни файлов ───────────────────────────────────────────────────────────

class TestShareableCarriesNothingPrivate(unittest.TestCase):

    def setUp(self):
        plane = LocalPlane(LAT0, LNG0)
        self.capture = {
            'day': '2026-06-05',
            'decoder_version': 'route-decode-2',
            'flights': [flight(574320663, latlon_line(plane, 0, 0, 200, 0),
                               mission='11111111-2222-3333-4444-555555555555',
                               nickname='8 GardenU',
                               unknown={'3:0': [1] * 21})],
            'contours': [],
        }
        self.private, self.shareable = run_study(self.capture, FINE)

    def test_the_report_passes_its_own_leak_guard(self):
        self.assertTrue(assert_shareable(self.shareable,
                                         private_strings(self.capture)))

    def test_no_real_flight_id_uuid_or_coordinate_is_present(self):
        text = json.dumps(self.shareable, ensure_ascii=False)
        self.assertNotIn('574320663', text)
        self.assertNotIn('11111111-2222', text)
        self.assertNotIn('8 GardenU', text)
        self.assertNotIn('40.08', text)
        self.assertNotIn('64.63', text)
        self.assertIn('FLIGHT-001', text)

    def test_a_planted_real_identifier_is_rejected(self):
        # Отрицательный контроль: без него проверка выше проходила бы и на
        # отчёте, который течёт.
        leaking = dict(self.shareable)
        leaking['works'] = [dict(self.shareable['works'][0],
                                 note='flight 574320663')]
        with self.assertRaises(ShareableLeak):
            assert_shareable(leaking, private_strings(self.capture))

    def test_a_planted_coordinate_is_rejected(self):
        leaking = dict(self.shareable, note='took off at 40.080000')
        with self.assertRaises(ShareableLeak):
            assert_shareable(leaking, private_strings(self.capture))

    def test_a_secret_marker_is_rejected(self):
        for poison in ({'link': 'https://x.invalid/a?signature=abc'},
                       {'header': 'Authorization: Bearer x'},
                       {'file': 'storage_state'},
                       {'cookie': 'Set-Cookie: a=b'}):
            with self.assertRaises(ShareableLeak):
                assert_shareable(dict(self.shareable, **poison), ())

    def test_an_identifier_sized_integer_is_rejected(self):
        with self.assertRaises(ShareableLeak):
            assert_shareable(dict(self.shareable, seen=1780670376000), ())

    def test_the_markdown_carries_the_same_numbers_and_no_private_value(self):
        text = render_markdown(self.shareable)
        self.assertIn('FLIGHT-001', text)
        self.assertIn(self.shareable['final_status'], text)
        for secret in private_strings(self.capture):
            self.assertNotIn(secret, text)

    def test_the_private_document_keeps_what_the_report_may_not(self):
        text = json.dumps(self.private, ensure_ascii=False)
        self.assertIn('574320663', text)
        self.assertTrue(self.private['never_share_this_file'])


class TestDeterminism(unittest.TestCase):

    def test_a_second_run_gives_the_same_result(self):
        plane = LocalPlane(LAT0, LNG0)
        capture = {'day': '2026-06-05', 'flights': [
            flight(1, latlon_line(plane, 0, 0, 200, 0), mission='M-1'),
            flight(2, latlon_line(plane, 0, 8, 200, 8), mission='M-1'),
            flight(3, latlon_line(plane, 900, 900, 1100, 900))]}
        first = run_study(capture, FINE)[1]
        second = run_study(capture, FINE)[1]
        self.assertEqual(json.dumps(first, sort_keys=True, ensure_ascii=False),
                         json.dumps(second, sort_keys=True, ensure_ascii=False))
        self.assertEqual(render_markdown(first), render_markdown(second))

    def test_writing_the_reports_produces_both_files(self):
        plane = LocalPlane(LAT0, LNG0)
        capture = {'day': '2026-06-05',
                   'flights': [flight(1, latlon_line(plane, 0, 0, 200, 0))]}
        private, shareable = run_study(capture, FINE)
        with tempfile.TemporaryDirectory() as folder:
            written = write_reports(folder, capture, private, shareable)
            for key in ('private', 'json', 'md'):
                self.assertTrue(os.path.exists(written[key]))
            self.assertIn('private', written['private'])


# ─── Итоговый статус и выводы ────────────────────────────────────────────────

class TestFindingsAndStatus(unittest.TestCase):

    def _capture(self):
        plane = LocalPlane(LAT0, LNG0)
        contour = geojson_square(plane, -10.0, -30.0, 260.0)
        flights = []
        for index in range(3):
            flights.append(flight(
                index + 1, latlon_line(plane, 0, index * 8.0, 240, index * 8.0),
                width=10.0, area_m2=4000.0, mission='M-1',
                candidates=['C-1'],
                start_ms=1780670376000 + index * 600000))
        return {'day': '2026-06-05', 'flights': flights,
                'contours': [{'uuid': 'C-1', 'name': 'Fixture field',
                              'geojson': contour}]}

    def test_a_repeated_value_is_a_fact_but_the_task_link_is_not_proven(self):
        # Блокер 3. Повтор значения наблюдается и потому PROVEN; утверждение
        # «это части одного задания» выводится из группировки и выше
        # SUPPORTED подняться не может.
        _private, shareable = run_study(self._capture(), FINE)
        found = {item['id']: item for item in shareable['findings']}
        finding = found[F_REPEAT_WITHIN_WORK]
        self.assertEqual(finding['status'], SUPPORTED)
        self.assertEqual(finding['evidence']['value_repetition_is_observed_fact'],
                         PROVEN)
        self.assertEqual(finding['evidence']['mission_uuid_semantics'],
                         NOT_PROVEN)
        self.assertEqual(finding['evidence']['repeated_rows_inside_works'], 2)
        self.assertEqual(shareable['works'][0]['dji_row_area_repeated_values'],
                         2)
        self.assertEqual(shareable['works'][0]['mission_identifier_semantics'],
                         NOT_PROVEN)

    def test_the_overlap_of_swaths_is_measured_not_assumed(self):
        _private, shareable = run_study(self._capture(), FINE)
        work = shareable['works'][0]
        # Три полосы по 10 м с шагом 8 м: объединение это лента 26 м.
        self.assertEqual(work['whole_route_swath_union_ha'], 0.624)
        self.assertEqual(work['sum_of_independent_flight_swaths_ha'], 0.72)
        found = {item['id']: item for item in shareable['findings']}
        self.assertIn(found[F_OVERLAP]['status'],
                      ('SUPPORTED', 'NOT_PROVEN', 'DISPROVED'))

    def test_the_status_is_the_estimate_when_width_and_contour_are_present(self):
        _private, shareable = run_study(self._capture(), FINE)
        self.assertEqual(shareable['final_status'],
                         USE_IN_CONTOUR_WORK_PASS_UNION)

    def test_without_width_the_source_is_declared_insufficient(self):
        capture = self._capture()
        for item in capture['flights']:
            item['spray_width_m'] = None
        _private, shareable = run_study(capture, FINE)
        self.assertEqual(shareable['final_status'], SOURCE_INSUFFICIENT)
        found = {item['id']: item for item in shareable['findings']}
        self.assertEqual(found[F_NO_WIDTH]['status'], 'SUPPORTED')

    def test_without_a_contour_the_source_is_declared_insufficient(self):
        capture = self._capture()
        capture['contours'] = []
        for item in capture['flights']:
            item['contour_candidates'] = []
        _private, shareable = run_study(capture, FINE)
        self.assertEqual(shareable['final_status'], SOURCE_INSUFFICIENT)

    def test_the_spray_status_is_never_chosen_by_the_tool_alone(self):
        capture = self._capture()
        groups_status = run_study(capture, FINE)[1]['final_status']
        self.assertNotEqual(groups_status, USE_SPRAY_STATE_CLIPPED_UNION)
        # И выбирается только при ЯВНО переданном доказательстве.
        self.assertEqual(
            choose_status([], [], spray_state_proved=True)[0],
            USE_SPRAY_STATE_CLIPPED_UNION)

    def test_a_row_area_above_its_own_route_is_flagged(self):
        capture = self._capture()
        for item in capture['flights']:
            item['work_area_m2'] = 90000.0
        _private, shareable = run_study(capture, FINE)
        found = {item['id']: item for item in shareable['findings']}
        self.assertEqual(found[F_LARGER_WORK]['status'], 'PROVEN')

    def test_a_row_area_matching_its_own_route_is_not_flagged(self):
        # Отрицательный контроль к предыдущей проверке.
        capture = self._capture()
        for item in capture['flights']:
            item['work_area_m2'] = 2400.0
        _private, shareable = run_study(capture, FINE)
        found = {item['id']: item for item in shareable['findings']}
        self.assertEqual(found[F_LARGER_WORK]['status'], DISPROVED)


# ─── Мелочи, которые уже кусали трек ─────────────────────────────────────────

class TestPrivateDirectoryIsIgnoredByGit(unittest.TestCase):
    """Блокер 1. Приватный каталог обязан быть невидим для Git.

    [REASON]: отчёт УТВЕРЖДАЛ, что каталог исключён, и это было верно -- но
    держалось на одном правиле в `drone_collector/.gitignore`, то есть на
    файле, который лежит ВНУТРИ защищаемого каталога и исчезает вместе с ним.
    А `git status --untracked-files=no`, которым проверялось дерево, к тому же
    объявил бы дерево чистым при настоящих координатах в рабочей копии. Здесь
    проверяется сам факт, а не намерение.
    """

    def _root(self):
        return os.path.dirname(os.path.dirname(os.path.dirname(
            os.path.abspath(__file__))))

    def _ignored(self, path, cwd):
        import subprocess
        return subprocess.run(['git', 'check-ignore', '-q', path], cwd=cwd,
                              stdout=subprocess.DEVNULL,
                              stderr=subprocess.DEVNULL).returncode == 0

    def test_both_layers_of_the_study_output_are_ignored(self):
        root = self._root()
        for path in ('drone_collector/out/area_48h/private/capture.json',
                     'drone_collector/out/area_48h/private/analysis.json',
                     'drone_collector/out/area_48h/DJI_AREA_48H_SHAREABLE.json',
                     'drone_collector/out/area_48h/DJI_AREA_48H_SHAREABLE.md'):
            self.assertTrue(self._ignored(path, root),
                            '%s is NOT ignored by git' % path)

    def test_source_files_are_not_ignored(self):
        # Отрицательный контроль: без него проверка выше прошла бы и на
        # `.gitignore`, исключающем вообще всё.
        root = self._root()
        for path in ('drone_collector/area_study.py',
                     'tools/dji_area_48h.py'):
            self.assertFalse(self._ignored(path, root),
                             '%s must stay visible to git' % path)

    def test_the_repository_root_rule_alone_is_enough(self):
        """Корневое правило работает БЕЗ вложенного `.gitignore`.

        Проверяется на отдельном пустом репозитории, куда кладётся только
        корневой файл исключений: если правило держится лишь на вложенном
        файле, здесь это видно сразу.
        """
        import subprocess
        import tempfile
        root = self._root()
        with open(os.path.join(root, '.gitignore'), encoding='utf-8') as handle:
            rules = handle.read()
        self.assertIn('/drone_collector/out/area_48h/', rules)
        with tempfile.TemporaryDirectory() as folder:
            subprocess.run(['git', 'init', '-q'], cwd=folder, check=True)
            with open(os.path.join(folder, '.gitignore'), 'w',
                      encoding='utf-8') as handle:
                handle.write(rules)
            self.assertTrue(
                self._ignored('drone_collector/out/area_48h/private/x.json',
                              folder))
            self.assertTrue(
                self._ignored(
                    'drone_collector/out/area_48h/DJI_AREA_48H_SHAREABLE.md',
                    folder))
            self.assertFalse(
                self._ignored('drone_collector/area_study.py', folder))


class TestContourIsChosenByThePolygonNotTheBox(unittest.TestCase):
    """Блокер 2. Рамка отбирает кандидатов, полигон принимает решение."""

    def setUp(self):
        self.plane = LocalPlane(LAT0, LNG0)
        # Маршрут целиком лежит в квадрате 0..200 по обеим осям.
        self.points = []
        for offset in range(0, 200, 20):
            self.points.extend(latlon_line(self.plane, 10, offset + 10,
                                           190, offset + 10))

    def _candidate(self, uuid, x0, y0, side):
        return {'uuid': uuid,
                'geojson': geojson_square(self.plane, x0, y0, side)}

    def _l_shaped(self, uuid):
        """Полигон, чья РАМКА накрывает весь маршрут, а сам он -- ничего.

        Тонкий угол вдоль двух сторон квадрата 0..200: bbox тот же, что у
        настоящего поля, а внутри полигона нет ни одной точки маршрута.
        """
        corners = [(0.0, 0.0), (200.0, 0.0), (200.0, 5.0), (5.0, 5.0),
                   (5.0, 200.0), (0.0, 200.0)]
        ring = [self.plane.latlon(x, y) for x, y in corners]
        ring.append(ring[0])
        return {'uuid': uuid,
                'geojson': {'type': 'Polygon',
                            'coordinates': [[[lon, lat]
                                             for lat, lon in ring]]}}

    def _node(self, uuid, x0, y0, x1, y1):
        low = self.plane.latlon(x0, y0)
        high = self.plane.latlon(x1, y1)
        return {'uuid': uuid,
                'bbox': {'downLeft': {'lat': low[0], 'lng': low[1]},
                         'upperRight': {'lat': high[0], 'lng': high[1]}}}

    def test_the_first_uuid_loses_when_its_polygon_is_the_wrong_field(self):
        # Отрицательный контроль на сам дефект. У обоих кандидатов ОДНА И ТА ЖЕ
        # рамка, поэтому отбор по рамке их не различает и прежнее правило
        # «первый из списка» брало кандидата, чей полигон маршрута не содержит
        # вовсе. Площадь считалась бы по чужому полю -- уверенно и неверно.
        wrong = self._l_shaped('aaaa-first')
        right = self._candidate('bbbb-second', 0.0, 0.0, 200.0)

        nodes = [self._node('aaaa-first', 0.0, 0.0, 200.0, 200.0),
                 self._node('bbbb-second', 0.0, 0.0, 200.0, 200.0)]
        by_box = candidate_contours(nodes, self.points)
        self.assertEqual([uuid for uuid, _share in by_box],
                         ['aaaa-first', 'bbbb-second'])
        self.assertEqual(by_box[0][1], by_box[1][1])

        choice = choose_contour(self.plane, self.points, [wrong, right])
        self.assertEqual(choice['status'], CONTOUR_MATCHED)
        self.assertEqual(choice['uuid'], 'bbbb-second')
        self.assertTrue(choice['unambiguous'])
        scores = {item['uuid']: item['share'] for item in choice['scores']}
        self.assertEqual(scores['aaaa-first'], 0.0)
        self.assertEqual(scores['bbbb-second'], 1.0)

    def test_the_length_tiebreak_cannot_promote_a_candidate_below_the_floor(self):
        # Вторичный признак не имеет права протащить кандидата, которого
        # первичный уже отверг.
        from drone_collector.area_study import CONTOUR_MIN_POINT_SHARE
        self.assertGreater(CONTOUR_MIN_POINT_SHARE, 0.0)
        outside = self._candidate('aaaa-outside', 400.0, 400.0, 200.0)
        choice = choose_contour(self.plane, self.points, [outside])
        self.assertEqual(choice['status'], CONTOUR_NOT_MATCHED)

    def test_two_equally_fitting_polygons_are_ambiguous(self):
        first = self._candidate('aaaa-one', -5.0, -5.0, 215.0)
        second = self._candidate('bbbb-two', -6.0, -6.0, 217.0)
        choice = choose_contour(self.plane, self.points, [first, second])
        self.assertEqual(choice['status'], CONTOUR_AMBIGUOUS)
        self.assertIsNone(choice['uuid'])
        self.assertIsNone(choice['rings'])
        self.assertFalse(choice['unambiguous'])

    def test_a_box_that_fits_but_a_polygon_that_does_not_matches_nothing(self):
        # Полигон рядом с маршрутом: рамка накрыла бы, геометрия -- нет.
        near = self._candidate('aaaa-near', 400.0, 400.0, 200.0)
        choice = choose_contour(self.plane, self.points, [near])
        self.assertEqual(choice['status'], CONTOUR_NOT_MATCHED)
        self.assertIsNone(choice['uuid'])

    def test_a_broken_candidate_does_not_stop_the_next_one(self):
        ring = [self.plane.latlon(0, 0), self.plane.latlon(200, 200),
                self.plane.latlon(200, 0), self.plane.latlon(0, 120)]
        ring.append(ring[0])
        broken = {'uuid': 'aaaa-broken',
                  'geojson': {'type': 'Polygon',
                              'coordinates': [[[lon, lat]
                                               for lat, lon in ring]]}}
        good = self._candidate('bbbb-good', -5.0, -5.0, 215.0)
        choice = choose_contour(self.plane, self.points, [broken, good])
        self.assertEqual(choice['status'], CONTOUR_MATCHED)
        self.assertEqual(choice['uuid'], 'bbbb-good')
        self.assertEqual(choice['candidates_offered'], 2)
        self.assertEqual(choice['candidates_usable'], 1)

    def test_no_candidate_at_all_is_its_own_answer(self):
        choice = choose_contour(self.plane, self.points, [])
        self.assertEqual(choice['status'], CONTOUR_NOT_OFFERED)

    def test_an_ambiguous_contour_leaves_the_clipped_area_uncomputed(self):
        first = self._candidate('aaaa-one', -5.0, -5.0, 215.0)
        second = self._candidate('bbbb-two', -6.0, -6.0, 217.0)
        capture = {'day': '2026-06-05', 'flights': [
            flight(1, self.points, candidates=['aaaa-one', 'bbbb-two'])],
            'contours': [first, second]}
        _private, shareable = run_study(capture, FINE)
        work = shareable['works'][0]
        self.assertEqual(work['contour_status'], CONTOUR_AMBIGUOUS)
        self.assertIsNone(work['work_pass_union_clipped_to_contour_ha'])
        self.assertIsNone(work['contour_area_ha_raster'])
        self.assertGreater(work['whole_route_swath_union_ha'], 0.0)

    def test_no_uuid_or_coordinate_reaches_the_shareable_report(self):
        first = self._candidate('11111111-2222-3333-4444-555555555555',
                                -5.0, -5.0, 215.0)
        capture = {'day': '2026-06-05', 'flights': [
            flight(574320663, self.points,
                   candidates=['11111111-2222-3333-4444-555555555555'])],
            'contours': [first]}
        private, shareable = run_study(capture, FINE)
        self.assertTrue(assert_shareable(shareable, private_strings(capture)))
        text = json.dumps(shareable, ensure_ascii=False)
        self.assertNotIn('11111111-2222', text)
        self.assertNotIn('574320663', text)
        self.assertIn('contour_point_share_inside', text)
        # Приватный слой, наоборот, обязан помнить и кандидатов, и основание.
        self.assertIn('11111111-2222',
                      json.dumps(private, ensure_ascii=False))


class TestLiveRunVerdict(unittest.TestCase):
    """Блокер 4. Неполный живой захват не имеет права дать код 0."""

    CLEAN = dict(operator_answered=True, drain_completed=True, observations=2,
                 confirmed=2, skipped_over_cap=0, observation_errors=0,
                 capture_errors=0, pending_route_requests=0,
                 route_requests_failed=0, id_sets_matched=True,
                 flights_of_study_day=9, flights_rejected_wrong_day=0,
                 study_day='2026-06-05')

    def _verdict(self, **overrides):
        return live_run_verdict(**dict(self.CLEAN, **overrides))

    def test_a_fully_clean_run_is_confirmed_and_exits_zero(self):
        verdict = self._verdict()
        self.assertTrue(verdict['confirmed'])
        self.assertEqual(verdict['reasons'], [])
        self.assertEqual(study_exit_code(True, verdict), EXIT_STUDY_OK)

    def test_a_drain_timeout_is_not_a_pass(self):
        verdict = self._verdict(drain_completed=False)
        self.assertFalse(verdict['confirmed'])
        self.assertEqual(study_exit_code(True, verdict),
                         EXIT_STUDY_UNCONFIRMED)

    def test_a_listener_error_is_not_a_pass(self):
        verdict = self._verdict(observation_errors=1)
        self.assertEqual(study_exit_code(True, verdict),
                         EXIT_STUDY_UNCONFIRMED)

    def test_one_unconfirmed_response_beside_a_confirmed_one_is_not_a_pass(self):
        verdict = self._verdict(observations=2, confirmed=1)
        self.assertFalse(verdict['confirmed'])
        self.assertEqual(study_exit_code(True, verdict),
                         EXIT_STUDY_UNCONFIRMED)

    def test_every_single_guard_can_fail_the_run_on_its_own(self):
        # Отрицательный контроль на набор целиком: если какое-то условие
        # перестанет проверяться, эта проверка упадёт именно на нём.
        for override in (dict(operator_answered=False),
                         dict(drain_completed=False),
                         dict(observation_errors=1),
                         dict(capture_errors=1),
                         dict(pending_route_requests=1),
                         dict(route_requests_failed=1),
                         dict(skipped_over_cap=1),
                         dict(confirmed=0),
                         dict(id_sets_matched=False),
                         dict(flights_of_study_day=0)):
            verdict = self._verdict(**override)
            self.assertFalse(verdict['confirmed'], repr(override))
            self.assertTrue(verdict['reasons'], repr(override))

    def test_nothing_captured_at_all_is_its_own_code(self):
        verdict = self._verdict(flights_of_study_day=0)
        self.assertEqual(study_exit_code(False, verdict), EXIT_STUDY_EMPTY)

    def test_the_reasons_carry_no_identifier(self):
        verdict = self._verdict(operator_answered=False, observation_errors=3,
                                flights_of_study_day=0)
        text = ' '.join(verdict['reasons'])
        self.assertNotIn('574320663', text)
        self.assertIn('2026-06-05', text)

    def test_the_exit_codes_agree_with_the_cli(self):
        from drone_collector.main import (EXIT_EMPTY, EXIT_OK,
                                          EXIT_ROUTE_PROBE_UNCONFIRMED)
        self.assertEqual(EXIT_STUDY_OK, EXIT_OK)
        self.assertEqual(EXIT_STUDY_EMPTY, EXIT_EMPTY)
        self.assertEqual(EXIT_STUDY_UNCONFIRMED, EXIT_ROUTE_PROBE_UNCONFIRMED)


class TestOnlyTheStudyDayIsAnalysed(unittest.TestCase):
    """Блокер 4. Дни не смешиваются."""

    def _flights(self):
        plane = LocalPlane(LAT0, LNG0)
        return [flight(1, latlon_line(plane, 0, 0, 200, 0), day='2026-06-05'),
                flight(2, latlon_line(plane, 0, 8, 200, 8), day='2026-06-05'),
                flight(3, latlon_line(plane, 0, 16, 200, 16),
                       day='2026-06-04')]

    def test_routes_of_another_day_are_left_out_and_counted(self):
        kept, rejected = split_by_day(self._flights(), '2026-06-05')
        self.assertEqual(len(kept), 2)
        self.assertEqual(rejected, 1)

    def test_only_the_wrong_day_leaves_nothing_to_analyse(self):
        wrong = [item for item in self._flights()
                 if item['day'] != '2026-06-05']
        kept, rejected = split_by_day(wrong, '2026-06-05')
        self.assertEqual(kept, [])
        self.assertEqual(rejected, 1)
        verdict = live_run_verdict(
            operator_answered=True, drain_completed=True, observations=1,
            confirmed=1, skipped_over_cap=0, observation_errors=0,
            capture_errors=0, pending_route_requests=0,
            route_requests_failed=0, id_sets_matched=True,
            flights_of_study_day=0, flights_rejected_wrong_day=rejected,
            study_day='2026-06-05')
        self.assertEqual(study_exit_code(True, verdict),
                         EXIT_STUDY_UNCONFIRMED)

    def test_a_mixed_capture_analyses_only_the_study_day(self):
        kept, rejected = split_by_day(self._flights(), '2026-06-05')
        capture = {'day': '2026-06-05', 'study_day_requested': '2026-06-05',
                   'flights': kept, 'flights_rejected_wrong_day': rejected,
                   'contours': []}
        _private, shareable = run_study(capture, FINE)
        self.assertEqual(shareable['flights_total'], 2)
        self.assertEqual(shareable['flights_rejected_wrong_day'], 1)
        self.assertTrue(any('another day' in note
                            for note in shareable['notes']))

    def test_an_unconfirmed_run_carries_its_caveat_into_the_report(self):
        kept, _rejected = split_by_day(self._flights(), '2026-06-05')
        capture = {'day': '2026-06-05', 'flights': kept, 'contours': [],
                   'live_run': {'confirmed': False,
                                'reasons': ['the operator never confirmed '
                                            'the map view']}}
        _private, shareable = run_study(capture, FINE)
        self.assertFalse(shareable['live_run_confirmed'])
        self.assertTrue(any('LIVE RUN NOT CONFIRMED' in note
                            for note in shareable['notes']))
        self.assertIn('НЕ подтверждён', render_markdown(shareable))


class TestCandidateContours(unittest.TestCase):

    def test_only_boxes_that_cover_the_route_are_candidates(self):
        nodes = [
            {'uuid': 'near', 'bbox': {'upperRight': {'lat': LAT0 + 0.01,
                                                     'lng': LNG0 + 0.01},
                                      'downLeft': {'lat': LAT0 - 0.01,
                                                   'lng': LNG0 - 0.01}}},
            {'uuid': 'far', 'bbox': {'upperRight': {'lat': LAT0 + 5.0,
                                                    'lng': LNG0 + 5.0},
                                     'downLeft': {'lat': LAT0 + 4.0,
                                                  'lng': LNG0 + 4.0}}},
        ]
        chosen = candidate_contours(nodes, [(LAT0, LNG0), (LAT0, LNG0 + 0.001)])
        self.assertEqual([uuid for uuid, _share in chosen], ['near'])

    def test_a_node_without_a_box_is_skipped_not_guessed(self):
        self.assertEqual(candidate_contours([{'uuid': 'x'}], [(LAT0, LNG0)]),
                         [])


class TestLocalDay(unittest.TestCase):

    def test_an_evening_flight_keeps_its_local_day(self):
        # 2026-06-05 21:30 по Бухаре -- это 16:30 UTC того же дня.
        self.assertEqual(day_of(1780673400000), '2026-06-05')

    def test_a_missing_timestamp_is_not_invented(self):
        self.assertIsNone(day_of(None))


class TestGridGuards(unittest.TestCase):

    def test_grids_of_different_extents_refuse_to_be_intersected(self):
        first = Grid(0, 0, 10, 10, 1.0)
        second = Grid(0, 0, 20, 20, 1.0)
        with self.assertRaises(Exception):
            first.intersected(second)

    def test_an_empty_point_set_has_no_projection(self):
        with self.assertRaises(Exception):
            plane_for([])


class _FakePage(object):
    """Страница Playwright ровно в том объёме, в каком её трогает прогон."""

    def __init__(self):
        self.handlers = {}

    def on(self, event, handler):
        self.handlers[event] = handler

    def wait_for_timeout(self, _ms):
        return None


class _FakeCollector(object):
    """FlightCollector без браузера. Сети не касается."""

    def __init__(self, _cfg, _log):
        self.page = _FakePage()

    def __enter__(self):
        return self

    def __exit__(self, *_exc):
        return False

    def open_records(self):
        return None

    def check_region(self, _expected):
        return 'UZ'


class _FakeObservation(object):
    """Наблюдение probe: сверка ID сошлась."""

    comparison = {'requested_and_returned_match': True}


class _FakePrompt(object):
    answered = True
    failed = False
    error_type = ''

    class done(object):
        @staticmethod
        def is_set():
            return True


class _FakeCapture(object):
    """AreaCapture с уже захваченными маршрутами. Ничего не слушает."""

    def __init__(self, flights):
        self._flights = flights
        self.observations = [_FakeObservation(), _FakeObservation()]
        self.route_responses = 2
        self.observation_errors = 0
        self.capture_errors = 0
        self.pending_route_requests = 0
        self.route_requests_failed = 0
        self.skipped_over_cap = 0

    @property
    def confirmed_observations(self):
        return list(self.observations)

    def captured_flights(self):
        return list(self._flights)

    def begin_drain(self, _now):
        return None

    def is_quiet(self, _now, _quiet):
        return True

    def note_request(self, _item):
        return None

    note_response = note_request
    note_request_finished = note_request
    note_request_failed = note_request


class _Args(object):
    def __init__(self, no_contours=False):
        self.area_48h_no_contours = no_contours


class _Config(object):
    """Настройки в объёме, который читает прогон исследования."""

    def __init__(self, out_dir):
        self.out_dir = out_dir
        self.storage_state = 'unused-in-this-test'
        self.route_api_origin = 'https://kr-ag2-api.dji.com'
        self.expected_region = None
        self.route_probe_poll_ms = 200
        self.route_probe_wait_ms = 1800000
        self.route_probe_drain_ms = 15000
        self.route_probe_quiet_ms = 2000


class TestRunAreaStudyReachesTheReport(unittest.TestCase):
    """Регрессия: успешный живой прогон обязан ДОЙТИ до отчёта.

    [REASON]: живой прогон 2026-08-30 собрал 168 маршрутов и 116 контуров и
    упал на `NameError: name 'notes' is not defined` в `run_study`. Список
    оговорок использовался в трёх местах и не заводился ни в одном. Ни один
    прежний тест этого не ловил: все они звали `run_study` напрямую, минуя
    `_run_area_48h`, а сам `_run_area_48h` не проверялся вовсе -- он открывает
    браузер. Отсюда и проверка: не «считает ли формула», а «проходит ли путь
    от захваченных маршрутов до записанного отчёта».

    Браузера, сети и кабинета DJI здесь нет: подменены `FlightCollector`,
    `AreaCapture`, ожидание оператора и сбор контуров.
    """

    def setUp(self):
        import tempfile

        from drone_collector import area_study, browser, main, route_ui_probe

        self.main = main
        self.area_study = area_study
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)

        plane = LocalPlane(LAT0, LNG0)
        self.contour = {'uuid': 'aaaa-field', 'field_serial': 'P00000000',
                        'name': 'Fixture field', 'total_area_mu': 100.0,
                        'geojson': geojson_square(plane, -20.0, -20.0, 260.0)}
        self.flights = [
            flight(1, latlon_line(plane, 0, 0, 220, 0),
                   candidates=['aaaa-field']),
            flight(2, latlon_line(plane, 0, 8, 220, 8),
                   candidates=['aaaa-field'], start_ms=1780670876000)]

        self.saved = {}
        self._swap(main, 'require_session', lambda _path: _path)
        self._swap(browser, 'FlightCollector', _FakeCollector)
        self._swap(route_ui_probe, 'start_operator_prompt',
                   lambda _text: _FakePrompt())
        self._swap(route_ui_probe, 'pump_until',
                   lambda *_args, **_kwargs: True)
        self._swap(area_study, 'AreaCapture',
                   lambda **_kwargs: _FakeCapture(self.flights))

        self.run_study_calls = []
        real_run_study = area_study.run_study

        def spy(capture, params=None, notes=None, spray_state_proved=False):
            self.run_study_calls.append({'notes': notes})
            if params is None:
                return real_run_study(capture, notes=notes,
                                      spray_state_proved=spray_state_proved)
            return real_run_study(capture, params, notes,
                                  spray_state_proved)

        self._swap(area_study, 'run_study', spy)

        self.write_reports_calls = []
        real_write_reports = area_study.write_reports

        def report_spy(out_dir, capture, private, shareable):
            self.write_reports_calls.append(out_dir)
            return real_write_reports(out_dir, capture, private, shareable)

        self._swap(area_study, 'write_reports', report_spy)

    def _swap(self, module, name, value):
        saved = getattr(module, name)
        setattr(module, name, value)
        self.addCleanup(setattr, module, name, saved)

    def _attach_ok(self, capture, _cfg, _log, _state):
        capture['contours'].append(self.contour)

    def _attach_boom(self, _capture, _cfg, _log, _state):
        raise RuntimeError('the directory walk failed')

    def _run(self, no_contours=False, attach=None):
        self._swap(self.main, '_attach_contours',
                   attach or self._attach_ok)
        import logging
        state = {}
        code = self.main._run_area_48h(
            _Args(no_contours), _Config(self.directory.name),
            logging.getLogger('test-area-48h'), state)
        return code, state

    def test_the_success_path_reaches_the_report_and_exits_zero(self):
        code, state = self._run()
        self.assertEqual(code, 0)
        self.assertTrue(state['area_live_confirmed'])
        self.assertEqual(state['area_flights_captured'], 2)
        # Именно то, чего не хватало: список оговорок существует и доезжает
        # до `run_study` пустым.
        self.assertEqual(len(self.run_study_calls), 1)
        self.assertEqual(self.run_study_calls[0]['notes'], [])
        # И отчёт действительно записан, а не только посчитан.
        self.assertEqual(len(self.write_reports_calls), 1)
        study = os.path.join(self.directory.name, 'area_48h')
        for name in ('DJI_AREA_48H_SHAREABLE.json',
                     'DJI_AREA_48H_SHAREABLE.md'):
            self.assertTrue(os.path.exists(os.path.join(study, name)), name)
        self.assertTrue(os.path.exists(
            os.path.join(study, 'private', 'capture.json')))

    def test_the_contour_switch_adds_its_caveat_without_a_name_error(self):
        code, _state = self._run(no_contours=True)
        self.assertEqual(code, 0)
        notes = self.run_study_calls[0]['notes']
        self.assertEqual(len(notes), 1)
        self.assertIn('area-48h-no-', notes[0])

    def test_a_failed_contour_walk_adds_its_caveat_without_a_name_error(self):
        code, _state = self._run(attach=self._attach_boom)
        self.assertEqual(code, 0)
        notes = self.run_study_calls[0]['notes']
        self.assertEqual(len(notes), 1)
        self.assertIn('RuntimeError', notes[0])
        self.assertIn('unclipped', notes[0])


class TestCommandLineWiring(unittest.TestCase):
    """--area-48h должен быть подключён так, чтобы им нельзя было случайно
    отправить что-нибудь в Vehicle Soft."""

    def _args(self, argv):
        from drone_collector.main import build_parser
        return build_parser().parse_args(argv)

    def test_the_study_run_needs_no_ingest_credentials(self):
        from drone_collector.main import needs_no_ingest
        self.assertTrue(needs_no_ingest(self._args(['--area-48h'])))
        # Отрицательный контроль: обычный сбор вылетов их требует.
        self.assertFalse(needs_no_ingest(self._args([])))

    def test_the_study_refuses_to_share_a_run_with_another_walk(self):
        from drone_collector.main import UsageError, check_usage
        for argv in (['--area-48h', '--lands'],
                     ['--area-48h', '--routes'],
                     ['--area-48h', '--route-ui-probe'],
                     ['--area-48h', '--from', '2026-06-05',
                      '--to', '2026-06-05']):
            with self.assertRaises(UsageError, msg=' '.join(argv)):
                check_usage(self._args(argv))

    def test_the_study_run_gets_its_own_summary_keys(self):
        # [REASON]: отрицательный контроль к дефекту, который иначе не видно.
        # Без своего набора сводка печаталась бы ключами сбора вылетов, и
        # успешный разбор выглядел бы как прогон, не собравший ничего.
        from drone_collector.main import (AREA_SUMMARY_KEYS,
                                          FLIGHT_SUMMARY_KEYS, MODE_AREA_48H)
        self.assertNotEqual(AREA_SUMMARY_KEYS, FLIGHT_SUMMARY_KEYS)
        for key in ('area_flights_captured', 'area_works', 'area_status',
                    'mode', 'exit'):
            self.assertIn(key, AREA_SUMMARY_KEYS)
        self.assertEqual(MODE_AREA_48H, 'area-48h')

    def test_the_contour_switch_needs_the_study(self):
        from drone_collector.main import UsageError, check_usage
        with self.assertRaises(UsageError):
            check_usage(self._args(['--area-48h-no-contours']))
        check_usage(self._args(['--area-48h', '--area-48h-no-contours']))


def _load_tool():
    """tools/dji_area_48h.py как модуль. Это скрипт, а не пакет."""
    import importlib.util
    root = os.path.dirname(os.path.dirname(os.path.dirname(
        os.path.abspath(__file__))))
    path = os.path.join(root, 'tools', 'dji_area_48h.py')
    spec = importlib.util.spec_from_file_location('dji_area_48h_tool', path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class TestPreflightRefusesAnIngestCapableEnvironment(unittest.TestCase):

    def test_a_leftover_ingest_variable_stops_the_run(self):
        tool = _load_tool()
        saved = {name: os.environ.get(name) for name in tool.INGEST_VARIABLES}
        try:
            os.environ['DRONE_API_TOKEN'] = 'x' * 8
            self.assertEqual(tool._preflight(), tool.EXIT_PREFLIGHT)
        finally:
            for name, value in saved.items():
                if value is None:
                    os.environ.pop(name, None)
                else:
                    os.environ[name] = value

    def test_exactly_one_mode_is_accepted(self):
        tool = _load_tool()
        self.assertEqual(tool.main(['--preflight', '--verify', 'x.json']),
                         tool.EXIT_USAGE)
        self.assertEqual(tool.main([]), tool.EXIT_USAGE)


if __name__ == '__main__':
    unittest.main()
