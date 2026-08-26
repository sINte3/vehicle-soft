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
    analyse_file, detect_format, looks_like_wgs84, main, describe_shapes,
    rings_from_geojson, rings_from_kml, ring_area_m2)

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

    def test_swapped_coordinates_are_suspected(self):
        """lat/lon вместо lon/lat: широта 40 сойдёт за долготу, а долгота 64
        за широту НЕ сойдёт только если выйдет за 90. Здесь берётся точка,
        на которой перестановка видна."""
        swapped = [[100.0, 64.6], [100.1, 64.6], [100.1, 64.7], [100.0, 64.6]]
        ok, _note = looks_like_wgs84(describe_shapes([('Polygon', swapped, [])]))
        self.assertTrue(ok)   # 100 -- законная долгота, 64 -- законная широта
        bad = [[64.6, 100.0], [64.7, 100.0], [64.7, 100.1], [64.6, 100.0]]
        ok, note = looks_like_wgs84(describe_shapes([('Polygon', bad, [])]))
        self.assertFalse(ok)
        self.assertIn('порядок', note)


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


if __name__ == '__main__':
    unittest.main()
