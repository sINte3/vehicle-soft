# -*- coding: utf-8 -*-
"""Tests for drone_collector/route_decode.py.

No network, no browser, no database. The sample bodies are BUILT here rather
than committed as a binary blob, for two reasons: a real capture carries real
field coordinates and real flight ids, and a blob nobody can read is a fixture
nobody can check. The builder below writes protobuf wire format by hand, so
the test states the shape it expects instead of trusting the decoder to agree
with itself.

Every positive assertion has a negative control next to it: a test that a good
body decodes proves nothing on its own if a bad body would decode too.
"""

import json
import os
import struct
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(
    os.path.abspath(__file__)))))

from drone_collector.route_decode import (  # noqa: E402
    DECODER_VERSION, POINT_SITE_ROUTE, POINT_SITE_TAKEOFF, RouteDecodeError,
    UNKNOWN_SEMANTICS, decode_route_record, decode_route_response,
    implied_work_length_m, path_length_m, route_exceeds_path, walk)


# ─── A hand-rolled protobuf writer, so the fixtures are readable ─────────────

def varint(value):
    out = bytearray()
    while True:
        byte = value & 0x7F
        value >>= 7
        if value:
            out.append(byte | 0x80)
        else:
            out.append(byte)
            return bytes(out)


def tag(number, wire):
    return varint((number << 3) | wire)


def f_varint(number, value):
    return tag(number, 0) + varint(value)


def f_fixed64(number, raw):
    return tag(number, 1) + raw


def f_bytes(number, raw):
    if isinstance(raw, str):
        raw = raw.encode('utf-8')
    return tag(number, 2) + varint(len(raw)) + raw


def f_fixed32(number, value):
    return tag(number, 5) + struct.pack('<f', value)


def f_double(number, value):
    return f_fixed64(number, struct.pack('<d', value))


def point(lat, lng):
    return f_bytes(1, f_double(1, lat) + f_double(2, lng))


def named(ident, name, hardware=None):
    body = f_varint(1, ident) + f_bytes(2, name)
    if hardware is not None:
        body += f_bytes(3, hardware)
    return body


# Synthetic values throughout: the flight id, the hardware id and the
# coordinates are invented, and the coordinates sit in the Bukhara region only
# so that the metre conversions are exercised at a realistic latitude.
FAKE_FLIGHT_ID = 900000001
FAKE_HARDWARE_ID = 'FIXTURE0000000000000'
FAKE_LAT = 40.0800
FAKE_LNG = 64.6300


def route_record(flight_id=FAKE_FLIGHT_ID, points=None, area_m2=10000.0,
                 width=6.0, extra=b''):
    if points is None:
        points = [(FAKE_LAT, FAKE_LNG), (FAKE_LAT, FAKE_LNG + 0.0100)]
    body = b''.join(point(lat, lng) for lat, lng in points)
    body += f_varint(2, flight_id)
    if area_m2 is not None:
        body += f_fixed32(3, area_m2)
    body += f_varint(4, 500000)
    body += f_bytes(7, f_double(1, points[0][0]) + f_double(2, points[0][1]))
    body += f_bytes(8, named(1, 'FixtureFlyer'))
    body += f_bytes(9, named(2, '9 Fixture', FAKE_HARDWARE_ID))
    body += f_bytes(10, named(3, 'Бригада 1'))
    body += f_varint(11, 4)
    body += f_bytes(18, 'Bukhara Region, 200500, Uzbekistan')
    body += f_varint(22, 1780670376000)
    body += f_varint(23, 1780670876000)
    body += f_bytes(24, 'T40')
    body += f_bytes(25, '6.5.47')
    if width is not None:
        body += f_fixed32(26, width)
    return body + extra


def response(records, status=200, message='Success.'):
    payload = b''.join(f_bytes(1, r) for r in records)
    return f_varint(1, status) + f_bytes(2, message) + f_bytes(3, payload)


# ─── The envelope ────────────────────────────────────────────────────────────

class TestEnvelope(unittest.TestCase):

    def test_a_well_formed_response_decodes(self):
        decoded = decode_route_response(response([route_record()]))
        self.assertEqual(decoded.status, 200)
        self.assertEqual(decoded.message, 'Success.')
        self.assertTrue(decoded.is_ok)
        self.assertEqual(decoded.flight_ids, [FAKE_FLIGHT_ID])

    def test_a_non_ok_status_is_reported_not_raised(self):
        """The caller must be able to SEE what DJI answered.

        [REASON]: the collector already learned once that HTTP 200 says
        nothing about success in this API. Turning a bad in-body status into
        an exception here would hide it behind a generic decode failure.
        """
        decoded = decode_route_response(response([], status=101, message='no'))
        self.assertFalse(decoded.is_ok)
        self.assertEqual(decoded.status, 101)
        self.assertEqual(decoded.routes, [])

    def test_empty_body_is_refused(self):
        with self.assertRaises(RouteDecodeError):
            decode_route_response(b'')

    def test_a_body_without_a_status_is_refused(self):
        """Negative control for test_a_well_formed_response_decodes."""
        with self.assertRaises(RouteDecodeError):
            decode_route_response(f_bytes(2, 'Success.'))

    def test_json_is_refused_rather_than_half_read(self):
        """A JSON body must not decode into a confident empty result."""
        with self.assertRaises(RouteDecodeError):
            decode_route_response(b'{"status":200,"code":0,"data":[]}')

    def test_truncation_is_refused(self):
        whole = response([route_record()])
        with self.assertRaises(RouteDecodeError):
            decode_route_response(whole[:len(whole) - 20])


# ─── The load-bearing negative result: a point is two doubles ────────────────

class TestPointShape(unittest.TestCase):

    def test_a_two_field_point_decodes_to_lat_lng(self):
        record = decode_route_record(route_record(
            points=[(FAKE_LAT, FAKE_LNG)]))
        self.assertEqual(record.points, [(FAKE_LAT, FAKE_LNG)])

    def test_a_two_field_point_reports_no_unknown_fields(self):
        """Обратная совместимость: у доказанного варианта лишнего нет."""
        record = decode_route_record(route_record(
            points=[(FAKE_LAT, FAKE_LNG)]))
        shape = record.point_shapes[0]
        self.assertEqual(shape.site, POINT_SITE_ROUTE)
        self.assertEqual(shape.fields, ((1, 1, 1), (2, 1, 1)))
        self.assertFalse(shape.has_unknown_fields)
        self.assertEqual(shape.extras, ())
        self.assertEqual(record.points_with_unknown_fields, 0)

    def test_a_point_with_a_third_field_decodes_and_keeps_it_unknown(self):
        """Живой прогон 2026-08-29 упёрся здесь в route-decode-1.

        [REASON]: строгая проверка сработала как задумана -- находка «только
        позиция» была пересмотрена осознанно, а не протухла. route-decode-2
        точку принимает, координаты берёт как раньше, а третье поле сохраняет
        СТРУКТУРНО и не толкует: ни высоты, ни времени, ни насоса.
        """
        third = f_bytes(1, f_double(1, FAKE_LAT) + f_double(2, FAKE_LNG)
                        + f_varint(3, 1780670376))
        record = decode_route_record(third + f_varint(2, FAKE_FLIGHT_ID))
        self.assertEqual(record.points, [(FAKE_LAT, FAKE_LNG)])
        shape = record.point_shapes[0]
        self.assertTrue(shape.has_unknown_fields)
        self.assertEqual(len(shape.extras), 1)
        extra = shape.extras[0]
        self.assertEqual(extra.number, 3)
        self.assertEqual(extra.wire, 0)
        self.assertEqual(extra.count, 1)
        self.assertEqual(extra.semantics, 'UNKNOWN_SEMANTICS')
        self.assertEqual(record.points_with_unknown_fields, 1)

    def test_the_value_of_an_unknown_varint_is_not_kept(self):
        """У varint даже длина не сохраняется: она выдаёт порядок величины."""
        third = f_bytes(1, f_double(1, FAKE_LAT) + f_double(2, FAKE_LNG)
                        + f_varint(3, 1780670376))
        record = decode_route_record(third + f_varint(2, FAKE_FLIGHT_ID))
        extra = record.point_shapes[0].extras[0]
        self.assertIsNone(extra.value_bytes)
        self.assertNotIn('1780670376', repr(extra))
        self.assertNotIn('1780670376', json.dumps(extra.as_dict()))

    def test_a_point_missing_the_longitude_is_refused(self):
        one = f_bytes(1, f_double(1, FAKE_LAT))
        with self.assertRaises(RouteDecodeError):
            decode_route_record(one + f_varint(2, FAKE_FLIGHT_ID))

    def test_a_point_missing_the_latitude_is_refused(self):
        one = f_bytes(1, f_double(2, FAKE_LNG))
        with self.assertRaises(RouteDecodeError):
            decode_route_record(one + f_varint(2, FAKE_FLIGHT_ID))

    def test_a_repeated_coordinate_is_refused(self):
        """Повтор координаты -- отказ, а не «возьмём первую»."""
        twice = f_bytes(1, f_double(1, FAKE_LAT) + f_double(1, FAKE_LAT)
                        + f_double(2, FAKE_LNG))
        with self.assertRaises(RouteDecodeError):
            decode_route_record(twice + f_varint(2, FAKE_FLIGHT_ID))

    def test_a_coordinate_with_the_wrong_wire_type_is_refused(self):
        wrong = f_bytes(1, f_varint(1, 40) + f_double(2, FAKE_LNG))
        with self.assertRaises(RouteDecodeError):
            decode_route_record(wrong + f_varint(2, FAKE_FLIGHT_ID))

    def test_a_truncated_coordinate_is_refused(self):
        """Повреждённое значение координаты по-прежнему отказ."""
        body = f_double(1, FAKE_LAT) + f_double(2, FAKE_LNG)
        torn = f_bytes(1, body[:-3])
        with self.assertRaises(RouteDecodeError):
            decode_route_record(torn + f_varint(2, FAKE_FLIGHT_ID))

    def test_an_illegal_wire_type_inside_a_point_is_refused(self):
        """Wire type 3 -- устаревшая группа; читать её мы не беремся."""
        body = f_double(1, FAKE_LAT) + f_double(2, FAKE_LNG) + bytes([(4 << 3) | 3])
        with self.assertRaises(RouteDecodeError):
            decode_route_record(f_bytes(1, body) + f_varint(2, FAKE_FLIGHT_ID))

    def test_unknown_fields_of_every_legal_wire_type_are_kept(self):
        body = (f_double(1, FAKE_LAT) + f_double(2, FAKE_LNG)
                + f_varint(3, 7) + f_fixed64(4, b'\x01' * 8)
                + f_bytes(5, b'\x02\x03\x04') + f_fixed32(6, 1.5))
        record = decode_route_record(
            f_bytes(1, body) + f_varint(2, FAKE_FLIGHT_ID))
        extras = {item.number: item for item in record.point_shapes[0].extras}
        self.assertEqual(sorted(extras), [3, 4, 5, 6])
        self.assertEqual(extras[3].wire, 0)
        self.assertIsNone(extras[3].value_bytes)
        self.assertEqual((extras[4].wire, extras[4].value_bytes), (1, 8))
        self.assertEqual((extras[5].wire, extras[5].value_bytes), (2, 3))
        self.assertEqual((extras[6].wire, extras[6].value_bytes), (5, 4))

    def test_a_repeated_unknown_field_is_counted_not_collapsed(self):
        body = (f_double(1, FAKE_LAT) + f_double(2, FAKE_LNG)
                + f_varint(3, 7) + f_varint(3, 8) + f_varint(3, 9))
        record = decode_route_record(
            f_bytes(1, body) + f_varint(2, FAKE_FLIGHT_ID))
        shape = record.point_shapes[0]
        self.assertEqual(shape.fields, ((1, 1, 1), (2, 1, 1), (3, 0, 3)))
        self.assertEqual(shape.extras[0].count, 3)

    def test_a_repeated_unknown_field_is_a_different_variant(self):
        """Один раз и три раза -- разные структурные варианты."""
        once = decode_route_record(
            f_bytes(1, f_double(1, FAKE_LAT) + f_double(2, FAKE_LNG)
                    + f_varint(3, 7)) + f_varint(2, FAKE_FLIGHT_ID))
        thrice = decode_route_record(
            f_bytes(1, f_double(1, FAKE_LAT) + f_double(2, FAKE_LNG)
                    + f_varint(3, 7) + f_varint(3, 8) + f_varint(3, 9))
            + f_varint(2, FAKE_FLIGHT_ID))
        self.assertNotEqual(once.point_shapes[0].key,
                            thrice.point_shapes[0].key)

    def test_an_unknown_bytes_field_is_not_parsed_as_a_message(self):
        """Что лежит внутри неизвестного поля -- не наше дело.

        [REASON]: содержимое может быть сообщением, строкой или упакованным
        массивом. Разобрать его как сообщение значило бы выдумать структуру, а
        байты, не похожие на protobuf, дали бы ложный отказ на исправном теле.
        """
        garbage = b'\xff\xfe\xfd\xfc'
        record = decode_route_record(
            f_bytes(1, f_double(1, FAKE_LAT) + f_double(2, FAKE_LNG)
                    + f_bytes(9, garbage)) + f_varint(2, FAKE_FLIGHT_ID))
        extra = record.point_shapes[0].extras[0]
        self.assertEqual((extra.number, extra.wire, extra.value_bytes),
                         (9, 2, 4))

    def test_a_truncated_unknown_field_is_still_refused(self):
        """Отрицательный контроль: усечение ловит `walk`, а не снисхождение."""
        body = (f_double(1, FAKE_LAT) + f_double(2, FAKE_LNG)
                + f_bytes(9, b'\x01\x02\x03\x04'))
        with self.assertRaises(RouteDecodeError):
            decode_route_record(
                f_bytes(1, body[:-2]) + f_varint(2, FAKE_FLIGHT_ID))


class TestTakeoffIsCountedApart(unittest.TestCase):
    """Точка взлёта разбирается тем же кодом, но считается отдельно.

    [REASON]: одинаковый разбор не доказывает одинаковую структуру. Вариант,
    который встречается только у взлёта, в общей статистике маршрутных точек
    растворился бы, и вопрос «одинаковы ли они» остался бы без ответа.
    """

    def record(self, point_body, takeoff_body):
        return decode_route_record(
            f_bytes(1, point_body) + f_varint(2, FAKE_FLIGHT_ID)
            + f_bytes(7, takeoff_body))

    def test_the_takeoff_shape_is_kept_separately(self):
        plain = f_double(1, FAKE_LAT) + f_double(2, FAKE_LNG)
        record = self.record(plain, plain)
        self.assertEqual(record.takeoff, (FAKE_LAT, FAKE_LNG))
        self.assertEqual(record.takeoff_shape.site, POINT_SITE_TAKEOFF)
        self.assertEqual(record.point_shapes[0].site, POINT_SITE_ROUTE)
        self.assertNotEqual(record.takeoff_shape.key,
                            record.point_shapes[0].key)

    def test_a_takeoff_may_differ_from_the_route_points(self):
        plain = f_double(1, FAKE_LAT) + f_double(2, FAKE_LNG)
        richer = plain + f_varint(3, 1)
        record = self.record(plain, richer)
        self.assertFalse(record.point_shapes[0].has_unknown_fields)
        self.assertTrue(record.takeoff_shape.has_unknown_fields)
        self.assertEqual(record.points_with_unknown_fields, 0)

    def test_a_takeoff_missing_a_coordinate_is_refused(self):
        plain = f_double(1, FAKE_LAT) + f_double(2, FAKE_LNG)
        with self.assertRaises(RouteDecodeError):
            self.record(plain, f_double(1, FAKE_LAT))

    def test_a_takeoff_with_a_wrong_wire_type_is_refused(self):
        plain = f_double(1, FAKE_LAT) + f_double(2, FAKE_LNG)
        with self.assertRaises(RouteDecodeError):
            self.record(plain, f_varint(1, 40) + f_double(2, FAKE_LNG))

    def test_a_record_without_a_takeoff_has_no_takeoff_shape(self):
        record = decode_route_record(
            f_bytes(1, f_double(1, FAKE_LAT) + f_double(2, FAKE_LNG))
            + f_varint(2, FAKE_FLIGHT_ID))
        self.assertIsNone(record.takeoff)
        self.assertIsNone(record.takeoff_shape)


class TestDecoderVersion(unittest.TestCase):

    def test_the_version_is_route_decode_2(self):
        """[REASON]: версия едет с каждым сохранённым маршрутом. По ней потом
        видно, какими правилами число было получено.
        """
        self.assertEqual(DECODER_VERSION, 'route-decode-2')


# ─── Field mapping ───────────────────────────────────────────────────────────

class TestRouteFields(unittest.TestCase):

    def setUp(self):
        self.record = decode_route_record(route_record())

    def test_the_proved_fields_are_named(self):
        self.assertEqual(self.record.flight_id, FAKE_FLIGHT_ID)
        self.assertAlmostEqual(self.record.work_area_m2, 10000.0, places=3)
        self.assertEqual(self.record.duration_ms, 500000)
        self.assertEqual(self.record.flyer_name, 'FixtureFlyer')
        self.assertEqual(self.record.nickname, '9 Fixture')
        self.assertEqual(self.record.hardware_id, FAKE_HARDWARE_ID)
        self.assertEqual(self.record.team_name, 'Бригада 1')
        self.assertEqual(self.record.mode_name, 4)
        self.assertEqual(self.record.drone_type, 'T40')
        self.assertEqual(self.record.app_version, '6.5.47')
        self.assertAlmostEqual(self.record.spray_width_m, 6.0, places=5)
        self.assertAlmostEqual(self.record.area_ha, 1.0, places=5)

    def test_the_start_and_end_are_milliseconds_not_seconds(self):
        """The JSON endpoints round these to whole seconds; the route does not."""
        self.assertEqual(self.record.start_ms, 1780670376000)
        self.assertEqual(self.record.end_ms, 1780670876000)

    def test_an_unidentified_field_is_reported_never_dropped(self):
        record = decode_route_record(route_record(extra=f_bytes(27, 'file')))
        self.assertEqual([(n, w) for n, w, _ in record.unknown], [(27, 2)])

    def test_a_wire_type_that_contradicts_the_mapping_is_refused(self):
        """Negative control: the names are asserted, not assumed."""
        bad = route_record().replace(f_fixed32(3, 10000.0),
                                     f_varint(3, 10000))
        with self.assertRaises(RouteDecodeError):
            decode_route_record(bad)


class TestMissingSprayWidth(unittest.TestCase):
    """Two of the nine flights in the sample carry no width at all.

    DJI marks that with exactly -1.0, and the JSON card for the same flights
    has `spray_width: null`. Nothing may substitute a default silently.
    """

    def test_minus_one_reads_as_unknown_not_as_a_width(self):
        record = decode_route_record(route_record(width=-1.0))
        self.assertFalse(record.spray_width_known)

    def test_zero_reads_as_unknown_too(self):
        record = decode_route_record(route_record(width=0.0))
        self.assertFalse(record.spray_width_known)

    def test_a_real_width_reads_as_known(self):
        """Negative control for the two above."""
        record = decode_route_record(route_record(width=5.95))
        self.assertTrue(record.spray_width_known)

    def test_no_width_means_no_consistency_verdict(self):
        """None, and never False -- "cannot tell" is not "consistent"."""
        record = decode_route_record(route_record(width=-1.0))
        self.assertIsNone(implied_work_length_m(record))
        self.assertIsNone(route_exceeds_path(record))


# ─── Geometry helpers ────────────────────────────────────────────────────────

class TestGeometry(unittest.TestCase):

    def test_path_length_of_a_known_span(self):
        """0.01 degrees of longitude at latitude 40.08 is 852.6 m.

        Computed independently: 0.01 * (pi/180) * 6378137 * cos(40.08 deg).
        """
        length = path_length_m([(FAKE_LAT, FAKE_LNG), (FAKE_LAT, FAKE_LNG + 0.01)])
        self.assertAlmostEqual(length, 852.6, delta=1.0)

    def test_a_single_point_has_no_length(self):
        self.assertEqual(path_length_m([(FAKE_LAT, FAKE_LNG)]), 0.0)

    def test_area_needing_more_path_than_flown_is_flagged(self):
        """A 100 m route that DJI credits with 5 940 m2 at a 5.91 m swath
        would need 1 005 m of path, so the check fires.

        Synthetic on purpose. The real flight 622715275 of 2026-06-05 looks
        like this -- 108 m of route, 5 940 m2 from DJI -- but it carries NO
        recorded swath, so this check answers None for it, not True. What the
        real record supports is the inverse statement, which needs no swath at
        all: 5 940 m2 over 108 m would require a 55 m swath. Giving it 5.91 m
        here would be the substitution the owner forbade."""
        short = [(FAKE_LAT, FAKE_LNG), (FAKE_LAT, FAKE_LNG + 0.00117)]
        record = decode_route_record(route_record(points=short, area_m2=5940.0,
                                                  width=5.91))
        self.assertAlmostEqual(path_length_m(record.points), 100.0, delta=2.0)
        self.assertTrue(route_exceeds_path(record))

    def test_a_consistent_flight_is_not_flagged(self):
        """Negative control: the test above must be able to say False."""
        long_leg = [(FAKE_LAT, FAKE_LNG), (FAKE_LAT, FAKE_LNG + 0.0234)]
        record = decode_route_record(route_record(points=long_leg,
                                                  area_m2=5940.0, width=5.91))
        self.assertFalse(route_exceeds_path(record))


class TestWalkRefusesGroups(unittest.TestCase):

    def test_a_group_wire_type_is_refused(self):
        with self.assertRaises(RouteDecodeError):
            walk(tag(1, 3))

    def test_field_number_zero_is_refused(self):
        with self.assertRaises(RouteDecodeError):
            walk(tag(0, 0) + varint(1))


if __name__ == '__main__':
    unittest.main()
