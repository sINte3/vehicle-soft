# -*- coding: utf-8 -*-
"""Тесты сбора полных контуров полей (`drone_collector/geometry.py`).

Ни сети, ни браузера: скачивание подставное. Главная проверяемая величина --
не геометрия, а то, что подписанная ссылка не переживает вызова: её нет ни в
очереди, ни в логе, ни в исключении, ни в сухом прогоне, ни в объекте
справочника после того, как её один раз взяли.

Фикстура `field_geometry_polygon.json` повторяет ФОРМУ подтверждённого файла
контура `P03335975` -- 22 различные вершины, замкнутое кольцо,
`funcType=PlantZone`, `parameters.offset` из 22 значений, пустой `MultiPoint`
с `funcType=ReferencePoint`.

**Про координаты.** Долгота сдвинута на ровные -64 градуса, и контур лежит в
открытом море, а не на поле. Сдвиг по долготе выбран не случайно: сферическая
формула площади зависит от РАЗНОСТИ долгот соседних вершин и от синуса широты,
поэтому такой сдвиг не меняет площадь вовсе -- 7.0596 га против 105.661703 му
DJI (те же +0.22 %), на которых стоят проверки формата. Прежняя редакция
фикстуры называла свои координаты вымышленными, будучи при этом в реальном
районе работ; committed-фикстура настоящих координат нести не должна.
"""

import hashlib
import json
import tempfile
import unittest

from pathlib import Path

from drone_collector.outbox import KIND_FIELD_GEOMETRY, Outbox
from drone_collector.geometry import (
    AREA_TOLERANCE_PERCENT, ContourSource, GeometryError, GeometryRun,
    MAX_GEOMETRY_BYTES, MU_PER_HECTARE, STATUS_AREA_MISMATCH,
    STATUS_DOWNLOAD_FAILED, STATUS_INVALID_GEOMETRY, STATUS_MD5_MISMATCH,
    STATUS_NO_GEOMETRY, STATUS_OK, STATUS_SECRET_IN_PAYLOAD, STATUS_TOO_LARGE,
    STATUS_UNCHANGED, STATUS_UNPARSEABLE, URL_PLACEHOLDER, contour_from_node,
    describe_geometry, extract_shapes, ring_self_intersects, scrub,
    select_nodes, write_dry_run)
from drone_collector.tests.support import FIXTURES_DIR, load_fixture


# Ссылка, которой в реальности не существует, но формы настоящей подписанной:
# именно её тесты ищут во всём, что прогон записал.
FAKE_SIGNED_URL = ('https://ag-oss.example-not-real.invalid/geometry/'
                   'P03335975.json?OSSAccessKeyId=LTAIFAKEKEYFAKEKEY'
                   '&Expires=1790000000&Signature=FAKESIGNATUREVALUE%3D')

FAKE_UUID = '5648f5b3-d69c-42e7-8067-ccd33049f0c1'
FAKE_SERIAL = 'P03335975'


def polygon_document():
    with (FIXTURES_DIR / 'field_geometry_polygon.json').open(
            encoding='utf-8') as handle:
        return json.load(handle)


def blob_of(document):
    return json.dumps(document, ensure_ascii=False, indent=1).encode('utf-8')


def node_for(blob, uuid=FAKE_UUID, total_area_mu=105.661703,
             signed_url=FAKE_SIGNED_URL, content_md5=None):
    """Узел справочника `lands`, как его отдаёт кабинет."""
    return {
        'uuid': uuid,
        'serialNumber': FAKE_SERIAL,
        'name': 'Karvon',
        'totalArea': total_area_mu,
        'geometry': {'storage': {
            'signedURL': signed_url,
            'contentMd5': (content_md5 if content_md5 is not None
                           else hashlib.md5(blob).hexdigest()),
        }},
    }


class _QuietLog(object):
    def __init__(self):
        self.records = []

    def _note(self, level, message, *args):
        self.records.append((level, message % args if args else message))

    def info(self, message, *args):
        self._note('info', message, *args)

    def warning(self, message, *args):
        self._note('warning', message, *args)

    def error(self, message, *args):
        self._note('error', message, *args)

    def text(self):
        return '\n'.join(text for _level, text in self.records)


class GeometryTestCase(unittest.TestCase):

    def setUp(self):
        self._directory = tempfile.TemporaryDirectory()
        self.addCleanup(self._directory.cleanup)
        self.root = Path(self._directory.name)
        self.outbox = Outbox(self.root / 'outbox').prepare()
        self.slept = []
        self.log = _QuietLog()
        self.downloads = []

    def downloader(self, *answers):
        queue = list(answers)

        def download(url):
            self.downloads.append(url)
            answer = queue.pop(0) if queue else blob_of(polygon_document())
            if isinstance(answer, Exception):
                raise answer
            return answer
        return download

    def run_on(self, nodes, download=None, only_uuids=None, **kwargs):
        kwargs.setdefault('pause_s', 0)
        run = GeometryRun(self.outbox, download or self.downloader(),
                          logger=self.log, sleep_fn=self.slept.append,
                          **kwargs)
        return run, run.collect(nodes, only_uuids=only_uuids)

    def everything_written(self):
        """Весь текст, который прогон оставил на диске и в логе."""
        parts = [self.log.text()]
        for directory in (self.outbox.pending_dir, self.outbox.sent_dir,
                          self.outbox.corrupt_dir):
            for path in directory.glob('*'):
                parts.append(path.read_text(encoding='utf-8', errors='replace'))
        for path in self.root.rglob('*.json'):
            parts.append(path.read_text(encoding='utf-8', errors='replace'))
        return '\n'.join(parts)


# ─── Подписанная ссылка ──────────────────────────────────────────────────────

class TestSignedUrlNeverEscapes(GeometryTestCase):

    def test_the_link_is_handed_over_once_and_then_gone(self):
        source = ContourSource(FAKE_UUID, signed_url=FAKE_SIGNED_URL)
        self.assertTrue(source.has_link)
        self.assertEqual(source.take_link(), FAKE_SIGNED_URL)
        self.assertFalse(source.has_link)
        self.assertIsNone(source.take_link())

    def test_repr_never_shows_the_link(self):
        source = ContourSource(FAKE_UUID, signed_url=FAKE_SIGNED_URL)
        self.assertNotIn('Signature', repr(source))
        self.assertNotIn('OSSAccessKeyId', repr(source))

    def test_describe_never_shows_the_link(self):
        source = ContourSource(FAKE_UUID, signed_url=FAKE_SIGNED_URL)
        self.assertNotIn('Signature', json.dumps(source.describe()))

    def test_a_full_cycle_writes_the_link_nowhere(self):
        """Главная проверка §9 архитектуры: полный цикл на подложном ответе."""
        blob = blob_of(polygon_document())
        _, result = self.run_on([node_for(blob)], download=self.downloader(blob))
        self.assertEqual(result.queued, 1)
        written = self.everything_written()
        self.assertNotIn(FAKE_SIGNED_URL, written)
        for fragment in ('OSSAccessKeyId', 'LTAIFAKEKEYFAKEKEY', 'Signature=',
                         'FAKESIGNATUREVALUE', 'Expires='):
            self.assertNotIn(fragment, written,
                             'в записанном найдено %r' % fragment)

    def test_the_downloader_really_did_receive_the_link(self):
        """Отрицательный контроль к тесту выше.

        Без него «ссылки нигде нет» прошло бы и в случае, когда скачивание
        вообще не состоялось.
        """
        blob = blob_of(polygon_document())
        self.run_on([node_for(blob)], download=self.downloader(blob))
        self.assertEqual(self.downloads, [FAKE_SIGNED_URL])

    def test_a_failure_message_from_the_library_is_scrubbed(self):
        blob = blob_of(polygon_document())
        failing = self.downloader(
            OSError('connection to %s failed' % FAKE_SIGNED_URL),
            OSError('connection to %s failed' % FAKE_SIGNED_URL),
            OSError('connection to %s failed' % FAKE_SIGNED_URL))
        _, result = self.run_on([node_for(blob)], download=failing)
        self.assertEqual(result.by_status[STATUS_DOWNLOAD_FAILED], 1)
        written = self.everything_written()
        self.assertNotIn(FAKE_SIGNED_URL, written)
        self.assertNotIn('FAKESIGNATUREVALUE', written)

    def test_scrub_removes_any_url_and_keeps_the_rest(self):
        cleaned = scrub('timeout while fetching https://x.invalid/a?sig=1 now')
        self.assertNotIn('https://', cleaned)
        self.assertIn(URL_PLACEHOLDER, cleaned)
        self.assertIn('timeout while fetching', cleaned)

    def test_scrub_drops_the_message_entirely_when_a_marker_survives(self):
        cleaned = scrub('header was Authorization: Bearer abcdef')
        self.assertNotIn('abcdef', cleaned)
        self.assertIn('dropped entirely', cleaned)

    def test_scrub_leaves_an_ordinary_message_alone(self):
        """Отрицательный контроль: чистка не съедает всё подряд."""
        self.assertEqual(scrub('HTTP 403'), 'HTTP 403')

    def test_a_payload_carrying_a_signature_is_refused_not_stored(self):
        poisoned = json.dumps({'type': 'FeatureCollection', 'features': [],
                               'note': FAKE_SIGNED_URL}).encode('utf-8')
        _, result = self.run_on([node_for(poisoned)],
                                download=self.downloader(poisoned))
        self.assertEqual(result.by_status[STATUS_SECRET_IN_PAYLOAD], 1)
        self.assertEqual(self.outbox.pending(), [])
        self.assertNotIn('FAKESIGNATUREVALUE', self.everything_written())


# ─── contentMd5 и sha256 ─────────────────────────────────────────────────────

class TestHashes(GeometryTestCase):

    def test_a_matching_content_md5_is_accepted(self):
        blob = blob_of(polygon_document())
        _, result = self.run_on([node_for(blob)], download=self.downloader(blob))
        self.assertEqual(result.by_status.get(STATUS_OK), 1)

    def test_a_mismatched_content_md5_is_refused(self):
        blob = blob_of(polygon_document())
        node = node_for(blob, content_md5='0' * 32)
        _, result = self.run_on([node], download=self.downloader(blob))
        self.assertEqual(result.by_status[STATUS_MD5_MISMATCH], 1)
        self.assertEqual(self.outbox.pending(), [],
                         'полигон чужого поля попал в очередь')

    def test_a_truncated_body_is_caught_by_the_md5(self):
        """Обрезанное тело -- второй случай, который ловит та же сверка."""
        blob = blob_of(polygon_document())
        node = node_for(blob)
        _, result = self.run_on([node], download=self.downloader(blob[:-20]))
        self.assertEqual(result.by_status[STATUS_MD5_MISMATCH], 1)

    def test_the_md5_comparison_ignores_letter_case(self):
        blob = blob_of(polygon_document())
        node = node_for(blob)
        node['geometry']['storage']['contentMd5'] = \
            hashlib.md5(blob).hexdigest().upper()
        _, result = self.run_on([node], download=self.downloader(blob))
        self.assertEqual(result.by_status.get(STATUS_OK), 1)

    def test_our_own_sha256_is_computed_and_stored(self):
        blob = blob_of(polygon_document())
        self.run_on([node_for(blob)], download=self.downloader(blob))
        envelope = self.outbox.read(self.outbox.pending()[0])
        self.assertEqual(envelope['body']['sha256'],
                         hashlib.sha256(blob).hexdigest())
        self.assertEqual(envelope['content_sha256'],
                         hashlib.sha256(blob).hexdigest())


# ─── Версии контура ──────────────────────────────────────────────────────────

class TestVersioning(GeometryTestCase):

    def test_the_same_version_is_not_downloaded_twice(self):
        blob = blob_of(polygon_document())
        node = node_for(blob)
        self.run_on([node_for(blob)], download=self.downloader(blob))
        self.downloads = []
        _, result = self.run_on([node], download=self.downloader(blob))
        self.assertEqual(self.downloads, [],
                         'та же версия скачана второй раз')
        self.assertEqual(result.by_status[STATUS_UNCHANGED], 1)
        self.assertEqual(len(self.outbox.pending()), 1)

    def test_a_new_version_lands_beside_the_old_one(self):
        first = blob_of(polygon_document())
        changed = polygon_document()
        changed['features'][0]['properties']['parameters']['offset'] = \
            [3.0] * 22
        second = blob_of(changed)
        self.run_on([node_for(first)], download=self.downloader(first))
        _, result = self.run_on([node_for(second)],
                                download=self.downloader(second))
        self.assertEqual(result.by_status.get(STATUS_OK), 1)
        self.assertEqual(len(self.outbox.pending()), 2,
                         'новая версия перезаписала старую')

    def test_the_old_version_keeps_its_content(self):
        first = blob_of(polygon_document())
        changed = polygon_document()
        changed['features'][0]['properties']['parameters']['offset'] = \
            [3.0] * 22
        self.run_on([node_for(first)], download=self.downloader(first))
        self.run_on([node_for(blob_of(changed))],
                    download=self.downloader(blob_of(changed)))
        offsets = sorted(
            self.outbox.read(path)['body']['geometry_geojson']['features'][0]
            ['properties']['parameters']['offset'][0]
            for path in self.outbox.pending())
        self.assertEqual(offsets, [2.5, 3.0])

    def test_a_version_already_sent_is_not_downloaded_again(self):
        blob = blob_of(polygon_document())
        self.run_on([node_for(blob)], download=self.downloader(blob))
        self.outbox.mark_sent(self.outbox.pending()[0])
        self.downloads = []
        _, result = self.run_on([node_for(blob)], download=self.downloader(blob))
        self.assertEqual(self.downloads, [])
        self.assertEqual(result.by_status[STATUS_UNCHANGED], 1)


# ─── Что именно сохраняется ──────────────────────────────────────────────────

class TestWhatIsStored(GeometryTestCase):

    def stored_body(self, document=None):
        blob = blob_of(document if document is not None
                       else polygon_document())
        self.run_on([node_for(blob)], download=self.downloader(blob))
        return self.outbox.read(self.outbox.pending()[0])['body']

    def test_the_whole_feature_collection_is_kept_verbatim(self):
        document = polygon_document()
        self.assertEqual(self.stored_body(document)['geometry_geojson'],
                         document)

    def test_func_type_survives(self):
        body = self.stored_body()
        properties = body['geometry_geojson']['features'][0]['properties']
        self.assertEqual(properties['funcType'], 'PlantZone')
        self.assertEqual(body['summary']['func_types'], ['PlantZone'])

    def test_parameters_offset_survives_with_every_value(self):
        body = self.stored_body()
        offsets = (body['geometry_geojson']['features'][0]['properties']
                   ['parameters']['offset'])
        self.assertEqual(len(offsets), 22)
        self.assertEqual(set(offsets), {2.5})

    def test_an_empty_reference_point_is_recorded_as_present(self):
        """Пустой `ReferencePoint` -- наблюдение, а не отсутствие данных."""
        body = self.stored_body()
        self.assertEqual(body['summary']['reference_points'], 1)
        self.assertIn('MultiPoint', body['summary']['other_geometry_types'])

    def test_unknown_properties_are_not_dropped(self):
        document = polygon_document()
        document['features'][0]['properties']['somethingDjiAddedLater'] = \
            {'nested': [1, 2, 3]}
        body = self.stored_body(document)
        self.assertEqual(
            body['geometry_geojson']['features'][0]['properties']
            ['somethingDjiAddedLater'], {'nested': [1, 2, 3]})

    def test_the_field_serial_and_name_travel_along(self):
        body = self.stored_body()
        self.assertEqual(body['field_serial'], FAKE_SERIAL)
        self.assertEqual(body['external_id'], FAKE_UUID)

    def test_the_envelope_kind_is_field_geometry(self):
        blob = blob_of(polygon_document())
        self.run_on([node_for(blob)], download=self.downloader(blob))
        envelope = self.outbox.read(self.outbox.pending()[0])
        self.assertEqual(envelope['kind'], KIND_FIELD_GEOMETRY)


# ─── Разбор геометрии ────────────────────────────────────────────────────────

class TestGeometryParsing(unittest.TestCase):

    def test_the_confirmed_shape_parses_with_no_complaints(self):
        described, reasons = describe_geometry(polygon_document())
        self.assertEqual(reasons, [])
        self.assertEqual(described['shapes'][0]['distinct_vertices'], 22)
        self.assertAlmostEqual(described['area_ha'], 7.0596, places=3)

    def test_a_multipolygon_is_supported(self):
        document = polygon_document()
        ring = document['features'][0]['geometry']['coordinates'][0]
        shifted = [[point[0] + 0.02, point[1]] for point in ring]
        document['features'][0]['geometry'] = {
            'type': 'MultiPolygon', 'coordinates': [[ring], [shifted]]}
        described, reasons = describe_geometry(document)
        self.assertEqual(reasons, [])
        self.assertEqual(len(described['shapes']), 2)
        self.assertAlmostEqual(described['area_ha'], 2 * 7.0596, places=2)

    def test_a_hole_is_subtracted_not_added(self):
        document = polygon_document()
        ring = document['features'][0]['geometry']['coordinates'][0]
        centre_lon = sum(p[0] for p in ring[:-1]) / (len(ring) - 1)
        centre_lat = sum(p[1] for p in ring[:-1]) / (len(ring) - 1)
        hole = [[centre_lon + (p[0] - centre_lon) * 0.3,
                 centre_lat + (p[1] - centre_lat) * 0.3] for p in ring]
        document['features'][0]['geometry']['coordinates'] = [ring, hole]
        with_hole, _ = describe_geometry(document)
        whole, _ = describe_geometry(polygon_document())
        self.assertLess(with_hole['area_ha'], whole['area_ha'])

    def test_an_unclosed_ring_is_refused(self):
        document = polygon_document()
        document['features'][0]['geometry']['coordinates'][0].pop()
        _described, reasons = describe_geometry(document)
        self.assertTrue(any('not closed' in reason for reason in reasons))

    def test_a_coordinate_out_of_range_is_refused(self):
        document = polygon_document()
        document['features'][0]['geometry']['coordinates'][0][3] = [999.0, 40.0]
        _described, reasons = describe_geometry(document)
        self.assertTrue(reasons)

    def test_a_document_without_a_polygon_is_refused(self):
        _described, reasons = describe_geometry(
            {'type': 'FeatureCollection', 'features': []})
        self.assertTrue(reasons)

    def test_a_multipoint_alone_is_not_a_polygon(self):
        """Отрицательный контроль: `ReferencePoint` полигоном не считается."""
        document = {'type': 'FeatureCollection', 'features': [
            {'type': 'Feature', 'properties': {'funcType': 'ReferencePoint'},
             'geometry': {'type': 'MultiPoint', 'coordinates': []}}]}
        self.assertEqual(extract_shapes(document), [])

    def test_a_bare_geometry_without_a_feature_parses(self):
        document = polygon_document()['features'][0]['geometry']
        described, reasons = describe_geometry(document)
        self.assertEqual(reasons, [])
        self.assertAlmostEqual(described['area_ha'], 7.0596, places=3)


    def test_a_self_intersecting_ring_is_refused(self):
        """Восьмёрка -- не контур поля, и площадь у неё неверная.

        [REASON]: доли самопересечения входят в формулу со знаком и гасят друг
        друга. У НЕСИММЕТРИЧНОЙ восьмёрки они гасятся не в ноль, а в
        положительное, но неверное число -- проверка «площадь не положительна»
        такую не ловит. Сверка с `totalArea` тоже не страхует: DJI считает по
        тем же вершинам. До этой проверки кольцо ниже принималось с площадью
        0.9520 га.
        """
        bowtie = [[64.4000, 39.8000], [64.4030, 39.8010], [64.4000, 39.8010],
                  [64.4010, 39.8000], [64.4000, 39.8000]]
        _described, reasons = describe_geometry(
            {'type': 'Polygon', 'coordinates': [bowtie]})
        self.assertTrue(any('intersects itself' in reason
                            for reason in reasons), reasons)

    def test_a_self_intersecting_hole_is_refused_too(self):
        outer = [[64.40, 39.80], [64.41, 39.80], [64.41, 39.81],
                 [64.40, 39.81], [64.40, 39.80]]
        bowtie = [[64.402, 39.802], [64.406, 39.804], [64.402, 39.804],
                  [64.404, 39.802], [64.402, 39.802]]
        _described, reasons = describe_geometry(
            {'type': 'Polygon', 'coordinates': [outer, bowtie]})
        self.assertTrue(any('intersects itself' in reason
                            for reason in reasons), reasons)

    def test_an_ordinary_ring_is_not_called_self_intersecting(self):
        """Отрицательный контроль: проверка обязана различать два случая."""
        _described, reasons = describe_geometry(load_fixture(
            'field_geometry_polygon.json'))
        self.assertEqual(reasons, [])

    def test_the_confirmed_contour_passes_the_same_check(self):
        """И на настоящей форме контура проверка молчит."""
        shapes = extract_shapes(load_fixture('field_geometry_polygon.json'))
        self.assertFalse(ring_self_intersects(shapes[0][1]))


# ─── Сверка площади как контроль формата ─────────────────────────────────────

class TestAreaCrossCheck(GeometryTestCase):

    def test_a_matching_area_passes(self):
        blob = blob_of(polygon_document())
        self.run_on([node_for(blob)], download=self.downloader(blob))
        body = self.outbox.read(self.outbox.pending()[0])['body']
        self.assertAlmostEqual(body['area_ha_computed'], 7.0596, places=3)
        self.assertLess(abs(body['area_difference_percent']),
                        AREA_TOLERANCE_PERCENT)

    def test_swapped_coordinates_are_caught_by_the_area(self):
        """Отрицательный контроль порядка координат.

        Перепутанные широта и долгота остаются в допустимых диапазонах
        градусов и грубой проверкой не ловятся -- но площадь при этом уезжает
        в разы, потому что косинус широты другой. Это тот самый случай, ради
        которого сверка с `totalArea` вообще существует.
        """
        document = polygon_document()
        ring = document['features'][0]['geometry']['coordinates'][0]
        document['features'][0]['geometry']['coordinates'][0] = \
            [[point[1], point[0]] for point in ring]
        blob = blob_of(document)
        _, result = self.run_on([node_for(blob)], download=self.downloader(blob))
        self.assertEqual(result.by_status[STATUS_AREA_MISMATCH], 1)
        self.assertEqual(self.outbox.pending(), [])

    def test_a_contour_without_a_total_area_still_passes(self):
        """Сверять не с чем -- это не повод отказать."""
        blob = blob_of(polygon_document())
        node = node_for(blob)
        node['totalArea'] = None
        _, result = self.run_on([node], download=self.downloader(blob))
        self.assertEqual(result.by_status.get(STATUS_OK), 1)

    def test_mu_are_converted_at_fifteen_to_the_hectare(self):
        source = ContourSource(FAKE_UUID, total_area_mu=150.0)
        self.assertAlmostEqual(source.area_ha_dji, 150.0 / MU_PER_HECTARE)
        self.assertEqual(MU_PER_HECTARE, 15.0)


# ─── Статусы ошибок ──────────────────────────────────────────────────────────

class TestErrorStatuses(GeometryTestCase):

    def test_a_contour_without_geometry_is_named_not_silently_skipped(self):
        node = node_for(b'x', signed_url=None)
        _, result = self.run_on([node])
        self.assertEqual(result.by_status[STATUS_NO_GEOMETRY], 1)
        self.assertEqual(self.downloads, [])

    def test_a_contour_without_a_content_md5_is_named_too(self):
        blob = blob_of(polygon_document())
        node = node_for(blob)
        node['geometry']['storage']['contentMd5'] = None
        _, result = self.run_on([node], download=self.downloader(blob))
        self.assertEqual(result.by_status[STATUS_NO_GEOMETRY], 1)

    def test_an_unparseable_body_is_named(self):
        blob = b'this is not json at all'
        _, result = self.run_on([node_for(blob)], download=self.downloader(blob))
        self.assertEqual(result.by_status[STATUS_UNPARSEABLE], 1)

    def test_a_valid_json_that_is_not_a_polygon_is_named(self):
        blob = json.dumps({'type': 'FeatureCollection',
                           'features': []}).encode('utf-8')
        _, result = self.run_on([node_for(blob)], download=self.downloader(blob))
        self.assertEqual(result.by_status[STATUS_INVALID_GEOMETRY], 1)

    def test_an_oversized_body_is_named_and_not_parsed(self):
        blob = b'{' + b'x' * (MAX_GEOMETRY_BYTES + 16)
        _, result = self.run_on([node_for(blob)], download=self.downloader(blob))
        self.assertEqual(result.by_status[STATUS_TOO_LARGE], 1)
        self.assertEqual(self.outbox.pending(), [])

    def test_every_contour_receives_exactly_one_status(self):
        blob = blob_of(polygon_document())
        nodes = [node_for(blob, uuid='u1'),
                 node_for(b'garbage', uuid='u2'),
                 node_for(blob, uuid='u3', signed_url=None)]
        download = self.downloader(blob, b'garbage')
        _, result = self.run_on(nodes, download=download)
        self.assertEqual(result.seen, 3)
        self.assertTrue(result.invariant_holds, result.as_dict())

    def test_one_failing_contour_does_not_stop_the_others(self):
        blob = blob_of(polygon_document())
        nodes = [node_for(b'garbage', uuid='u1'), node_for(blob, uuid='u2')]
        _, result = self.run_on(nodes,
                                download=self.downloader(b'garbage', blob))
        self.assertEqual(result.by_status[STATUS_UNPARSEABLE], 1)
        self.assertEqual(result.by_status[STATUS_OK], 1)
        self.assertEqual(len(self.outbox.pending()), 1)


    def test_a_document_nested_too_deep_is_named_not_fatal(self):
        """Один патологический файл не имеет права унести весь проход.

        [REASON]: разбор глубоко вложенного JSON поднимает `RecursionError`, а
        это `RuntimeError`, не `ValueError`. До правки он уходил из `_one`
        наружу, и прогон терял ВСЕ оставшиеся контуры, отчитавшись голым
        трейсбеком с кодом 1 -- при том, что модуль обещает каждому контуру
        именованный статус.
        """
        deep = ('[' * 100000 + ']' * 100000).encode('utf-8')
        good = blob_of(polygon_document())
        nodes = [node_for(deep, uuid='u1'), node_for(good, uuid='u2')]
        _, result = self.run_on(nodes, download=self.downloader(deep, good))
        self.assertEqual(result.by_status[STATUS_UNPARSEABLE], 1)
        self.assertEqual(result.by_status[STATUS_OK], 1)
        self.assertTrue(result.invariant_holds, result.as_dict())

    def test_a_contour_the_queue_refuses_is_named_not_fatal(self):
        """Отказ очереди -- статус ОДНОГО контура, а не конец прогона.

        Имя контура приходит из справочника и в теле никем не проверялось;
        маркер ловит только очередь, последней.
        """
        good = blob_of(polygon_document())
        poisoned = node_for(good, uuid='u1')
        poisoned['name'] = 'Karvon?Signature=abc'
        nodes = [poisoned, node_for(good, uuid='u2')]
        _, result = self.run_on(nodes, download=self.downloader(good, good))
        self.assertEqual(result.by_status[STATUS_SECRET_IN_PAYLOAD], 1)
        self.assertEqual(result.by_status[STATUS_OK], 1)
        self.assertEqual(len(self.outbox.pending()), 1)
        self.assertNotIn('Signature=abc', self.everything_written())


# ─── Повторы, темп, сухой прогон ─────────────────────────────────────────────

class TestRetryAndPacing(GeometryTestCase):

    def test_a_failed_download_is_retried_and_then_succeeds(self):
        blob = blob_of(polygon_document())
        download = self.downloader(OSError('reset'), blob)
        _, result = self.run_on([node_for(blob)], download=download)
        self.assertEqual(result.by_status.get(STATUS_OK), 1)
        self.assertEqual(len(self.downloads), 2)
        self.assertIn(2, self.slept)

    def test_the_retries_are_bounded(self):
        blob = blob_of(polygon_document())
        download = self.downloader(*[OSError('x') for _ in range(20)])
        _, result = self.run_on([node_for(blob)], download=download)
        self.assertEqual(len(self.downloads), 3)
        self.assertEqual(result.by_status[STATUS_DOWNLOAD_FAILED], 1)

    def test_an_empty_body_is_retried_not_accepted(self):
        blob = blob_of(polygon_document())
        _, result = self.run_on([node_for(blob)],
                                download=self.downloader(b'', blob))
        self.assertEqual(result.by_status.get(STATUS_OK), 1)
        self.assertEqual(len(self.downloads), 2)

    def test_downloads_are_paced(self):
        blob = blob_of(polygon_document())
        run = GeometryRun(self.outbox, self.downloader(blob, blob),
                          logger=self.log, sleep_fn=self.slept.append,
                          pause_s=0.25)
        run.collect([node_for(blob, uuid='u1'), node_for(blob, uuid='u2')])
        self.assertIn(0.25, self.slept)

    def test_a_skipped_contour_costs_no_pause(self):
        """Отрицательный контроль темпа: не скачивали -- не ждём."""
        blob = blob_of(polygon_document())
        run = GeometryRun(self.outbox, self.downloader(), logger=self.log,
                          sleep_fn=self.slept.append, pause_s=0.25)
        run.collect([node_for(blob, uuid='u1', signed_url=None),
                     node_for(blob, uuid='u2', signed_url=None)])
        self.assertEqual(self.slept, [])


class TestDryRun(GeometryTestCase):

    def test_a_dry_run_queues_nothing(self):
        blob = blob_of(polygon_document())
        run, result = self.run_on([node_for(blob)],
                                  download=self.downloader(blob), dry_run=True)
        self.assertEqual(result.by_status.get(STATUS_OK), 1)
        self.assertEqual(self.outbox.pending(), [])
        self.assertEqual(len(run.prepared_bodies), 1)

    def test_the_dry_run_file_says_plainly_that_nothing_was_queued(self):
        blob = blob_of(polygon_document())
        run, result = self.run_on([node_for(blob)],
                                  download=self.downloader(blob), dry_run=True)
        target = write_dry_run(result, run.prepared_bodies, self.root / 'out')
        document = json.loads(target.read_text(encoding='utf-8'))
        self.assertTrue(document['dry_run'])
        self.assertTrue(document['nothing_was_queued'])
        self.assertEqual(len(document['contours']), 1)

    def test_the_dry_run_file_carries_no_link(self):
        blob = blob_of(polygon_document())
        run, result = self.run_on([node_for(blob)],
                                  download=self.downloader(blob), dry_run=True)
        write_dry_run(result, run.prepared_bodies, self.root / 'out')
        self.assertNotIn(FAKE_SIGNED_URL, self.everything_written())
        self.assertNotIn('OSSAccessKeyId', self.everything_written())


# ─── Точный отбор контуров по uuid ───────────────────────────────────────────

class TestGeometrySelection(GeometryTestCase):
    """`--geometry-id` берёт РОВНО названное и ничего больше.

    [REASON]: без отбора первый живой прогон качает все 5 489 контуров, то
    есть предъявляет пять с половиной тысяч подписанных ссылок ради одной
    проверки формата. Отбор идёт по сырому узлу справочника, поэтому ссылки
    невыбранных контуров не попадают даже в память процесса.
    """

    def three_nodes(self, blob):
        return [node_for(blob, uuid='u1'), node_for(blob, uuid='u2'),
                node_for(blob, uuid='u3')]

    def test_only_the_named_contour_is_downloaded(self):
        blob = blob_of(polygon_document())
        run, result = self.run_on(self.three_nodes(blob),
                                  download=self.downloader(blob),
                                  only_uuids=['u2'])
        self.assertEqual(len(self.downloads), 1)
        self.assertEqual(result.seen, 1)
        self.assertEqual(result.by_status[STATUS_OK], 1)
        self.assertEqual(len(self.outbox.pending()), 1)
        envelope = self.outbox.read(self.outbox.pending()[0])
        self.assertEqual(envelope['identity'], 'u2')

    def test_without_the_filter_every_contour_is_downloaded(self):
        """Отрицательный контроль: убери фильтр -- качается лишнее."""
        blob = blob_of(polygon_document())
        _, result = self.run_on(self.three_nodes(blob),
                                download=self.downloader(blob, blob, blob))
        self.assertEqual(len(self.downloads), 3)
        self.assertEqual(result.seen, 3)

    def test_two_ids_take_exactly_two(self):
        blob = blob_of(polygon_document())
        _, result = self.run_on(self.three_nodes(blob),
                                download=self.downloader(blob, blob),
                                only_uuids=['u1', 'u3'])
        self.assertEqual(len(self.downloads), 2)
        self.assertEqual(result.seen, 2)
        names = sorted(self.outbox.read(path)['identity']
                       for path in self.outbox.pending())
        self.assertEqual(names, ['u1', 'u3'])

    def test_a_repeated_id_does_not_download_twice(self):
        blob = blob_of(polygon_document())
        _, result = self.run_on(self.three_nodes(blob),
                                download=self.downloader(blob),
                                only_uuids=['u2', 'u2', 'u2'])
        self.assertEqual(len(self.downloads), 1)
        self.assertEqual(result.seen, 1)
        self.assertEqual(result.requested_uuids, ['u2'])

    def test_an_unknown_id_is_reported_and_nothing_is_downloaded(self):
        blob = blob_of(polygon_document())
        _, result = self.run_on(self.three_nodes(blob),
                                download=self.downloader(),
                                only_uuids=['not-in-the-directory'])
        self.assertEqual(self.downloads, [])
        self.assertEqual(result.seen, 0)
        self.assertEqual(result.missing_uuids, ['not-in-the-directory'])

    def test_a_known_id_beside_an_unknown_one_still_reports_the_unknown(self):
        blob = blob_of(polygon_document())
        _, result = self.run_on(self.three_nodes(blob),
                                download=self.downloader(blob),
                                only_uuids=['u1', 'ghost'])
        self.assertEqual(len(self.downloads), 1)
        self.assertEqual(result.missing_uuids, ['ghost'])

    def test_the_dry_run_selects_the_same_set(self):
        blob = blob_of(polygon_document())
        _, dry = self.run_on(self.three_nodes(blob),
                             download=self.downloader(blob),
                             only_uuids=['u2'], dry_run=True)
        self.assertEqual(self.outbox.pending(), [])
        self.assertEqual(dry.seen, 1)
        self.downloads = []
        _, real = self.run_on(self.three_nodes(blob),
                              download=self.downloader(blob),
                              only_uuids=['u2'])
        self.assertEqual(real.seen, dry.seen)
        self.assertEqual(len(self.outbox.pending()), 1)

    def test_the_link_of_an_unselected_contour_is_never_used(self):
        blob = blob_of(polygon_document())
        nodes = self.three_nodes(blob)
        nodes[0]['geometry']['storage']['signedURL'] = (
            'https://oss.aliyuncs.com/u1?OSSAccessKeyId=LTAI&Signature=NOPE')
        nodes[2]['geometry']['storage']['signedURL'] = (
            'https://oss.aliyuncs.com/u3?OSSAccessKeyId=LTAI&Signature=NOPE')
        self.run_on(nodes, download=self.downloader(blob), only_uuids=['u2'])
        for url in self.downloads:
            self.assertNotIn('NOPE', url)
        self.assertNotIn('NOPE', self.everything_written())
        self.assertNotIn('OSSAccessKeyId', self.everything_written())

    def test_the_report_carries_uuids_and_statuses_only(self):
        blob = blob_of(polygon_document())
        _, result = self.run_on(self.three_nodes(blob),
                                download=self.downloader(blob),
                                only_uuids=['u2', 'ghost'])
        report = result.as_dict()
        self.assertEqual(report['requested_uuids'], ['u2', 'ghost'])
        self.assertEqual(report['missing_uuids'], ['ghost'])
        self.assertNotIn('signedURL', str(report))
        self.assertNotIn('Signature', str(report))


class TestSelectNodes(unittest.TestCase):
    """Отбор сам по себе, без прогона."""

    NODES = [{'uuid': 'a'}, {'uuid': 'b'}, {'uuid': 'a'}, 'not a dict',
             {'no_uuid': True}]

    def test_none_means_the_whole_directory(self):
        chosen, requested, missing = select_nodes(self.NODES, None)
        self.assertEqual(len(chosen), 4)
        self.assertIsNone(requested)
        self.assertEqual(missing, [])

    def test_the_match_is_exact(self):
        """Отрицательный контроль: не префикс, не подстрока, не регистр."""
        for probe in ('A', 'aa', 'b/a', 'a-'):
            chosen, _requested, missing = select_nodes(self.NODES, [probe])
            self.assertEqual(chosen, [], probe)
            self.assertEqual(missing, [probe], probe)

    def test_surrounding_whitespace_is_tolerated(self):
        """Uuid, вставленный из документа, часто приходит с пробелом.

        Обрезаются только края; после обрезки сравнение по-прежнему точное.
        """
        chosen, requested, missing = select_nodes(self.NODES, ['  a\n'])
        self.assertEqual(len(chosen), 1)
        self.assertEqual(requested, ['a'])
        self.assertEqual(missing, [])

    def test_a_duplicate_node_is_taken_once(self):
        chosen, _requested, missing = select_nodes(self.NODES, ['a'])
        self.assertEqual(len(chosen), 1)
        self.assertEqual(missing, [])

    def test_the_requested_order_is_kept_in_missing(self):
        _chosen, requested, missing = select_nodes(self.NODES,
                                                   ['z', 'a', 'y'])
        self.assertEqual(requested, ['z', 'a', 'y'])
        self.assertEqual(missing, ['z', 'y'])


# ─── Узел справочника ────────────────────────────────────────────────────────

class TestContourFromNode(unittest.TestCase):

    def test_the_real_directory_node_shape_is_understood(self):
        """Разбирается настоящая фикстура справочника, а не выдуманная."""
        page = load_fixture('lands_page1.json')
        node = page['data']['lands']['edges'][0]['node']
        source = contour_from_node(node)
        self.assertEqual(source.uuid, node['uuid'])
        self.assertEqual(source.field_serial, node['serialNumber'])
        self.assertEqual(source.content_md5,
                         node['geometry']['storage']['contentMd5'])
        self.assertTrue(source.has_link)

    def test_a_node_without_a_uuid_is_skipped(self):
        self.assertIsNone(contour_from_node({'serialNumber': 'P1'}))
        self.assertIsNone(contour_from_node('not a node'))

    def test_a_node_without_geometry_has_no_link(self):
        source = contour_from_node({'uuid': 'u1'})
        self.assertFalse(source.has_link)


class TestFixtureItself(unittest.TestCase):

    def test_the_fixture_is_not_in_the_real_operating_area(self):
        """Committed-фикстура не несёт настоящих координат.

        [REASON]: парк работает в Бухарской области -- примерно 63..66 в.д.
        при 39..41 с.ш. Прежняя редакция этой фикстуры лежала ровно там и при
        этом объявляла свои координаты вымышленными: её площадь совпадала с
        настоящим контуром `P03335975` до 0.0001 га, а `totalArea` -- до
        шестого знака. Проверяется долгота: сдвиг по ней площадь не меняет,
        поэтому анонимизация ничего не стоила проверкам формата.
        """
        shapes = extract_shapes(load_fixture('field_geometry_polygon.json'))
        self.assertTrue(shapes)
        for _kind, outer, _holes, _properties in shapes:
            for position in outer:
                self.assertFalse(
                    63.0 <= position[0] <= 66.0,
                    'долгота %r лежит в реальном районе работ' % position[0])


    def test_the_geometry_fixture_carries_no_secret(self):
        """Фикстура уходит в git -- в ней не должно быть ничего подписанного."""
        from drone_collector.outbox import find_secret_markers
        text = (FIXTURES_DIR / 'field_geometry_polygon.json').read_text(
            encoding='utf-8')
        self.assertEqual(find_secret_markers(text), [])

    def test_the_lands_fixture_carries_no_real_signed_url(self):
        text = (FIXTURES_DIR / 'lands_page1.json').read_text(encoding='utf-8')
        self.assertIn('PLACEHOLDER-NOT-A-REAL-URL', text)
        self.assertNotIn('aliyuncs.com', text)


if __name__ == '__main__':
    unittest.main()
