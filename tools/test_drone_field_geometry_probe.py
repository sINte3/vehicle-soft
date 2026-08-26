# -*- coding: utf-8 -*-
"""Тесты tools/drone_field_geometry_probe.py. Без сети.

    python tools/test_drone_field_geometry_probe.py

Проверяется главное свойство инструмента: он ОПРЕДЕЛЯЕТ формат, а не
предполагает его. Поэтому у каждого распознавания есть отрицательный
контроль -- файл другого формата, который не должен опознаться так же.

Площадь считается на настоящем прямоугольнике с известной стороной, а не
сверяется сама с собой.
"""

import json
import math
import os
import struct
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from tools.drone_field_geometry_probe import (  # noqa: E402
    DEFAULT_AREA_TOLERANCE_PERCENT, analyse_file, coordinate_problems,
    describe_shapes, detect_format, distinct_vertex_count, looks_like_wgs84,
    main, md5_of, ring_area_m2, ring_self_intersects, rings_from_geojson,
    rings_from_kml, validate_shapes)

LAT = 40.0827
LON = 64.6329


def write(directory, name, blob):
    path = os.path.join(directory, name)
    mode = 'wb' if isinstance(blob, bytes) else 'w'
    with open(path, mode, **({} if isinstance(blob, bytes)
                             else {'encoding': 'utf-8'})) as handle:
        handle.write(blob)
    return path


def rectangle(width_m=100.0, height_m=200.0):
    """Замкнутое кольцо-прямоугольник заданного размера в метрах."""
    d_lat = height_m / 111320.0
    d_lon = width_m / (111320.0 * math.cos(math.radians(LAT)))
    return [[LON, LAT], [LON + d_lon, LAT], [LON + d_lon, LAT + d_lat],
            [LON, LAT + d_lat], [LON, LAT]]


GEOJSON = json.dumps({
    'type': 'FeatureCollection',
    'features': [{'type': 'Feature',
                  'geometry': {'type': 'Polygon',
                               'coordinates': [rectangle()]},
                  'properties': {}}]})

KML = ("""<?xml version="1.0" encoding="UTF-8"?>
<kml xmlns="http://www.opengis.net/kml/2.2"><Document><Placemark><Polygon>
<outerBoundaryIs><LinearRing><coordinates>%s</coordinates></LinearRing>
</outerBoundaryIs></Polygon></Placemark></Document></kml>"""
       % ' '.join('%s,%s,0' % (point[0], point[1]) for point in rectangle()))


class TestFormatDetection(unittest.TestCase):

    def test_geojson_is_recognised(self):
        kind, _note = detect_format(GEOJSON.encode('utf-8'))
        self.assertEqual(kind, 'GEOJSON')

    def test_kml_is_recognised(self):
        kind, _note = detect_format(KML.encode('utf-8'))
        self.assertEqual(kind, 'KML')

    def test_zip_is_recognised_as_kmz(self):
        kind, _note = detect_format(b'PK\x03\x04rest of a zip file')
        self.assertEqual(kind, 'KMZ')

    def test_gzip_is_recognised(self):
        kind, _note = detect_format(b'\x1f\x8b\x08\x00payload')
        self.assertEqual(kind, 'GZIP')

    def test_protobuf_is_recognised_as_protobuf_like(self):
        blob = (b'\x08\xc8\x01'                       # field 1 varint 200
                + b'\x12\x08Success.')                # field 2 bytes
        kind, _note = detect_format(blob)
        self.assertEqual(kind, 'PROTOBUF_LIKE')

    def test_plain_json_that_is_not_geojson_is_told_apart(self):
        """Отрицательный контроль к распознаванию GeoJSON."""
        kind, _note = detect_format(b'{"totalArea": 105.6, "uuid": "x"}')
        self.assertEqual(kind, 'JSON_OTHER')

    def test_random_bytes_stay_unknown(self):
        """Отрицательный контроль ко всем распознаваниям.

        Инструмент обязан уметь сказать «не знаю». Детектор, который всегда
        что-то называет, форматом не занимается.
        """
        kind, _note = detect_format(b'\xff\xfe\xfd\xfc\xfb not a format at all')
        self.assertEqual(kind, 'UNKNOWN')


class TestSecretRefusal(unittest.TestCase):

    def test_a_file_with_a_signed_url_is_refused(self):
        with tempfile.TemporaryDirectory() as directory:
            leaky = json.dumps({'signedURL':
                                'https://example.invalid/x?OSSAccessKeyId=A'
                                '&Signature=B'})
            path = write(directory, 'leaky.json', leaky)
            result = analyse_file(path)
            self.assertIn('signedURL', result['secrets'])
            import contextlib, io
            with contextlib.redirect_stdout(io.StringIO()):
                code = main(['--file', path])
            self.assertEqual(code, 3)

    def test_a_clean_geometry_file_is_not_refused(self):
        """Отрицательный контроль: на чистом файле проверка молчит."""
        with tempfile.TemporaryDirectory() as directory:
            path = write(directory, 'clean.json', GEOJSON)
            self.assertEqual(analyse_file(path)['secrets'], [])


class TestGeometry(unittest.TestCase):

    def test_area_of_a_known_rectangle(self):
        """100 x 200 м = 2 га. Считается на фигуре, размер которой задан."""
        area = abs(ring_area_m2(rectangle(100.0, 200.0)))
        self.assertAlmostEqual(area / 10000.0, 2.0, delta=0.02)

    def test_a_different_rectangle_gives_a_different_area(self):
        """Отрицательный контроль: формула зависит от входа."""
        small = abs(ring_area_m2(rectangle(50.0, 50.0)))
        self.assertAlmostEqual(small / 10000.0, 0.25, delta=0.01)

    def test_geojson_rings_are_parsed(self):
        shapes = rings_from_geojson(json.loads(GEOJSON))
        self.assertEqual(len(shapes), 1)
        self.assertEqual(shapes[0][0], 'Polygon')

    def test_kml_rings_are_parsed(self):
        shapes = rings_from_kml(KML)
        self.assertEqual(len(shapes), 1)
        described = describe_shapes(shapes)
        self.assertAlmostEqual(described[0]['area_ha'], 2.0, delta=0.02)

    def test_multipolygon_and_holes_are_seen(self):
        document = {'type': 'Feature', 'geometry': {
            'type': 'MultiPolygon',
            'coordinates': [[rectangle(100.0, 200.0), rectangle(20.0, 20.0)],
                            [rectangle(50.0, 50.0)]]}}
        shapes = rings_from_geojson(document)
        self.assertEqual(len(shapes), 2)
        described = describe_shapes(shapes)
        self.assertEqual(described[0]['holes'], 1)
        self.assertEqual(described[1]['holes'], 0)

    def test_a_closed_ring_is_reported_closed(self):
        described = describe_shapes([('Polygon', rectangle(), [])])
        self.assertTrue(described[0]['closed'])

    def test_an_open_ring_is_reported_open(self):
        """Отрицательный контроль к предыдущему."""
        described = describe_shapes([('Polygon', rectangle()[:-1], [])])
        self.assertFalse(described[0]['closed'])

    def test_a_self_intersecting_ring_is_flagged(self):
        bowtie = [[0.0, 0.0], [1.0, 1.0], [1.0, 0.0], [0.0, 1.0], [0.0, 0.0]]
        described = describe_shapes([('Polygon', bowtie, [])])
        self.assertTrue(described[0]['self_intersects'])

    def test_a_simple_ring_is_not_flagged(self):
        """Отрицательный контроль к предыдущему."""
        described = describe_shapes([('Polygon', rectangle(), [])])
        self.assertFalse(described[0]['self_intersects'])

    def test_coordinates_outside_wgs84_are_rejected(self):
        bad = [[64.6, 100.0], [64.7, 100.0], [64.7, 100.1], [64.6, 100.0]]
        described = describe_shapes([('Polygon', bad, [])])
        ok, note = looks_like_wgs84(described)
        self.assertFalse(ok)
        self.assertIn('широта', note)
        self.assertTrue(validate_shapes([('Polygon', bad, [])], described))

    def test_valid_coordinates_are_accepted(self):
        """Отрицательный контроль к предыдущему."""
        described = describe_shapes([('Polygon', rectangle(), [])])
        ok, _note = looks_like_wgs84(described)
        self.assertTrue(ok)
        self.assertEqual(validate_shapes([('Polygon', rectangle(), [])],
                                         described), [])

    def test_non_numeric_and_non_finite_coordinates_are_caught(self):
        for bad_point in (['x', 40.0], [None, 40.0], [float('nan'), 40.0],
                          [float('inf'), 40.0], [True, 40.0]):
            ring = [bad_point, [64.7, 40.0], [64.7, 40.1], bad_point]
            self.assertTrue(coordinate_problems(ring),
                            'not caught: %r' % (bad_point,))

    def test_a_clean_ring_has_no_coordinate_problems(self):
        """Отрицательный контроль к предыдущему."""
        self.assertEqual(coordinate_problems(rectangle()), [])

    def test_fewer_than_three_distinct_vertices_is_rejected(self):
        degenerate = [[64.6, 40.0], [64.7, 40.0], [64.6, 40.0]]
        described = describe_shapes([('Polygon', degenerate, [])])
        reasons = validate_shapes([('Polygon', degenerate, [])], described)
        self.assertTrue(any('различных вершин' in reason for reason in reasons))
        self.assertEqual(distinct_vertex_count(degenerate), 2)

    def test_an_unclosed_ring_is_rejected(self):
        ring = rectangle()[:-1]
        described = describe_shapes([('Polygon', ring, [])])
        reasons = validate_shapes([('Polygon', ring, [])], described)
        self.assertTrue(any('не замкнуто' in reason for reason in reasons))

    def test_a_self_intersection_on_the_closing_segment_is_caught(self):
        """Незамкнутое кольцо, чьё замыкающее ребро пересекает другое.

        Валидатор отвергает такое кольцо и как незамкнутое, и как
        самопересекающееся -- вторая проверка не должна молчать только
        потому, что последняя вершина не повторяет первую.
        """
        bowtie_open = [[0.0, 0.0], [1.0, 1.0], [1.0, 0.0], [0.0, 1.0]]
        self.assertTrue(ring_self_intersects(bowtie_open))


class TestEndToEnd(unittest.TestCase):

    def test_main_on_geojson_reports_area_and_writes_geojson(self):
        import contextlib, io
        with tempfile.TemporaryDirectory() as directory:
            path = write(directory, 'field.json', GEOJSON)
            out = os.path.join(directory, 'out.geojson')
            buffer = io.StringIO()
            with contextlib.redirect_stdout(buffer):
                code = main(['--file', path, '--geojson', out,
                             '--expect-area-mu', '30.0'])
            self.assertEqual(code, 0)
            text = buffer.getvalue()
            self.assertIn('GEOJSON', text)
            self.assertIn('rings parsed : 1', text)
            self.assertTrue(os.path.exists(out))

    def test_main_on_an_unreadable_format_says_so_and_stops(self):
        import contextlib, io
        with tempfile.TemporaryDirectory() as directory:
            path = write(directory, 'blob.bin', b'\xff\xfe\xfd nonsense')
            with contextlib.redirect_stdout(io.StringIO()) as buffer:
                code = main(['--file', path])
            self.assertEqual(code, 1)

    def test_main_refuses_a_missing_file(self):
        import contextlib, io
        with contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(main(['--file', '/nonexistent/x.bin']), 2)


class TestStrictValidation(unittest.TestCase):
    """Пункт 5 ревью: провал валидации -> ненулевой код и НЕТ GeoJSON."""

    def run_main(self, blob, name='field.json', extra=()):
        import contextlib, io
        directory = tempfile.mkdtemp()
        path = write(directory, name, blob)
        out = os.path.join(directory, 'out.geojson')
        buffer = io.StringIO()
        with contextlib.redirect_stdout(buffer):
            code = main(['--file', path, '--geojson', out] + list(extra))
        return code, buffer.getvalue(), out, path

    def test_a_good_polygon_passes_and_writes_geojson(self):
        code, text, out, _path = self.run_main(GEOJSON)
        self.assertEqual(code, 0)
        self.assertIn('VALIDATION PASSED', text)
        self.assertTrue(os.path.exists(out))

    def test_md5_mismatch_fails_and_writes_nothing(self):
        code, text, out, _path = self.run_main(
            GEOJSON, extra=('--expect-md5', '0' * 32))
        self.assertEqual(code, 4)
        self.assertIn('MISMATCH', text)
        self.assertFalse(os.path.exists(out))

    def test_md5_match_passes(self):
        """Отрицательный контроль к предыдущему."""
        directory = tempfile.mkdtemp()
        path = write(directory, 'field.json', GEOJSON)
        out = os.path.join(directory, 'out.geojson')
        import contextlib, io
        with contextlib.redirect_stdout(io.StringIO()) as buffer:
            code = main(['--file', path, '--geojson', out,
                         '--expect-md5', md5_of(path)])
        self.assertEqual(code, 0)
        self.assertIn('MATCH', buffer.getvalue())
        self.assertTrue(os.path.exists(out))

    def test_an_unclosed_ring_fails_and_writes_nothing(self):
        blob = json.dumps({'type': 'Polygon',
                           'coordinates': [rectangle()[:-1]]})
        code, text, out, _path = self.run_main(blob)
        self.assertEqual(code, 4)
        self.assertIn('VALIDATION FAILED', text)
        self.assertFalse(os.path.exists(out))

    def test_a_self_intersecting_ring_fails(self):
        bowtie = [[0.0, 0.0], [1.0, 1.0], [1.0, 0.0], [0.0, 1.0], [0.0, 0.0]]
        blob = json.dumps({'type': 'Polygon', 'coordinates': [bowtie]})
        code, text, out, _path = self.run_main(blob)
        self.assertEqual(code, 4)
        self.assertFalse(os.path.exists(out))

    def test_coordinates_outside_wgs84_fail(self):
        bad = [[64.6, 100.0], [64.7, 100.0], [64.7, 100.1], [64.6, 100.0]]
        blob = json.dumps({'type': 'Polygon', 'coordinates': [bad]})
        code, _text, out, _path = self.run_main(blob)
        self.assertEqual(code, 4)
        self.assertFalse(os.path.exists(out))

    def test_a_non_finite_coordinate_fails(self):
        blob = '{"type": "Polygon", "coordinates": [[[64.6, 40.0], ' \
               '[NaN, 40.0], [64.7, 40.1], [64.6, 40.0]]]}'
        code, _text, out, _path = self.run_main(blob)
        self.assertEqual(code, 4)
        self.assertFalse(os.path.exists(out))

    def test_a_degenerate_ring_fails(self):
        blob = json.dumps({'type': 'Polygon', 'coordinates': [
            [[64.6, 40.0], [64.7, 40.0], [64.6, 40.0]]]})
        code, _text, out, _path = self.run_main(blob)
        self.assertEqual(code, 4)
        self.assertFalse(os.path.exists(out))

    def test_an_area_beyond_the_tolerance_fails(self):
        """Прямоугольник 100x200 м = 2 га = 30 му. Заявим 60 му."""
        code, text, out, _path = self.run_main(
            GEOJSON, extra=('--expect-area-mu', '60.0'))
        self.assertEqual(code, 4)
        self.assertIn('tolerance', text)
        self.assertFalse(os.path.exists(out))

    def test_an_area_within_the_tolerance_passes(self):
        """Отрицательный контроль к предыдущему."""
        code, _text, out, _path = self.run_main(
            GEOJSON, extra=('--expect-area-mu', '30.0'))
        self.assertEqual(code, 0)
        self.assertTrue(os.path.exists(out))

    def test_the_tolerance_is_configurable_and_defaults_to_one_percent(self):
        self.assertEqual(DEFAULT_AREA_TOLERANCE_PERCENT, 1.0)
        # 30 му против прямоугольника 2 га -- расхождение около 0.03 %.
        # Сузим допуск до нуля: тот же файл обязан провалиться.
        code, _text, out, _path = self.run_main(
            GEOJSON, extra=('--expect-area-mu', '30.5',
                            '--area-tolerance-percent', '0.001'))
        self.assertEqual(code, 4)
        self.assertFalse(os.path.exists(out))

    def test_an_unreadable_format_is_recon_not_validation(self):
        code, text, out, _path = self.run_main(b'\xff\xfe\xfd nonsense',
                                               name='blob.bin')
        self.assertEqual(code, 1)
        self.assertIn('RECONNAISSANCE RESULT', text)
        self.assertIn('B2 stays OPEN', text)
        self.assertFalse(os.path.exists(out))

    def test_a_secret_bearing_file_writes_nothing(self):
        blob = json.dumps({'signedURL': 'https://example.invalid/x'
                                        '?OSSAccessKeyId=A&Signature=B',
                           'type': 'Polygon',
                           'coordinates': [rectangle()]})
        code, _text, out, _path = self.run_main(blob)
        self.assertEqual(code, 3)
        self.assertFalse(os.path.exists(out))


if __name__ == '__main__':
    unittest.main()
