# -*- coding: utf-8 -*-
"""tools/gps_day_kml.py -- сутки машины в KML для Google Earth.

[REASON]: 30.09.2026 владелец не смог решить по прежнему экрану, работа ли
невозможный участок: нет спутниковой подложки и госномеров. Файл кладёт те же
участки и трек на снимок. Здесь держится то, от чего картинка солгала бы:
порядок координат (KML -- долгота, широта, как GeoJSON; перепутанный порядок
увёл бы участок в другую часть света), куски трека по окну скорости без
разрывов линии и с разрывом на промежутке больше 300 с, дыры многоугольника,
пометка неопубликованных суток и суток без файла точек, имя как на экране,
экранирование XML и то, что ни база, ни файл точек не меняются.
"""
import contextlib
import hashlib
import io
import json
import os
import shutil
import sqlite3
import sys
import tempfile
import unittest
import xml.etree.ElementTree as ET
from datetime import datetime

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if REPO_ROOT not in sys.path:
    sys.path.insert(0, REPO_ROOT)

import tools.gps_day_kml as kml                                     # noqa: E402
from gps_collector import config as collector_config               # noqa: E402
from gps_collector import storage                                  # noqa: E402
from tests.test_gps_units_inventory import DDL                      # noqa: E402

NS = {'k': 'http://www.opengis.net/kml/2.2'}
TRACTOR, CAR = 393, 7286
DAY = '2026-09-26'


def midnight(day):
    return int(datetime.strptime(day, '%Y-%m-%d').replace(
        tzinfo=collector_config.TZ).timestamp())


class World(unittest.TestCase):
    def setUp(self):
        self.folder = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.folder, True)
        self.db = os.path.join(self.folder, 'transport.db')
        self.out = os.path.join(self.folder, 'sites.kml')
        con = sqlite3.connect(self.db)
        try:
            for statement in DDL:
                con.execute(statement)
            con.execute("INSERT INTO equipment (name, plate, category, "
                        "organization_id, is_active) VALUES "
                        "('МТЗ-80.1', '80 239 NA', 'mtz', 1, 1)")
            con.execute("INSERT INTO vialon_mappings (vialon_name, wialon_id, "
                        "equipment_id, skip) VALUES ('МТЗ 239 & Co <1>', ?, 1, 0)",
                        (TRACTOR,))
            con.execute("INSERT INTO gps_daily_aggregates (work_date, wialon_id, "
                        "points_total, track_km, reason) VALUES (?, ?, 10, 1.0, "
                        "NULL)", (DAY, TRACTOR))
            # участок с дырой: внешнее кольцо и одна дыра, [долгота, широта]
            ring = [[64.50, 39.90], [64.52, 39.90], [64.52, 39.92],
                    [64.50, 39.92], [64.50, 39.90]]
            hole = [[64.505, 39.905], [64.51, 39.905], [64.51, 39.91],
                    [64.505, 39.905]]
            con.execute("INSERT INTO gps_work_polygons (work_date, wialon_id, "
                        "site_number, area_ha, minutes, polygon_geojson, "
                        "operator_label) VALUES (?, ?, 2, 68.73, 4.6, ?, NULL)",
                        (DAY, TRACTOR, json.dumps({'type': 'Polygon',
                                                   'coordinates': [ring, hole]})))
            # машина без строки сопоставления, сутки не опубликованы
            con.execute("INSERT INTO gps_daily_aggregates (work_date, wialon_id, "
                        "points_total, track_km, reason) VALUES (?, ?, 5, 0.5, "
                        "'sbor_nepolnyy')", ('2026-09-13', CAR))
            con.commit()
        finally:
            con.close()
        start = midnight(DAY) + 8 * 3600
        # работа, работа, переезд, переезд, работа; затем пауза 400 с
        self.speeds = [8.0, 8.0, 30.0, 30.0, 8.0, 8.0, 8.0]
        self.times = [0, 10, 20, 30, 40, 440, 450]
        storage.write_points(self.folder, [
            (TRACTOR, start + dt, 64.50 + i * 1e-3, 39.90 + i * 1e-3, speed, 0, 12)
            for i, (dt, speed) in enumerate(zip(self.times, self.speeds))])
        self.points_file = storage.points_path(self.folder, '202609')

    def digest(self, path):
        with open(path, 'rb') as handle:
            return hashlib.sha256(handle.read()).hexdigest()

    def run_main(self, *pairs):
        argv = ['--db', self.db, '--dir', self.folder, '--out', self.out]
        for pair in pairs:
            argv += ['--day', pair]
        with contextlib.redirect_stdout(io.StringIO()) as out, \
                contextlib.redirect_stderr(io.StringIO()) as err:
            code = kml.main(argv)
        return code, out.getvalue(), err.getvalue()

    def document(self):
        return ET.parse(self.out).getroot()


class TheTrack(unittest.TestCase):

    def test_pieces_follow_the_speed_window_without_gaps_in_the_line(self):
        points = [(t, float(i), 0.0, speed, 12) for i, (t, speed) in enumerate(
            zip([0, 10, 20, 30, 40], [8.0, 8.0, 30.0, 30.0, 8.0]))]
        pieces = kml.track_pieces(points)
        self.assertEqual([work for work, _line in pieces], [True, False, True])
        # переезд начинается там, где кончилась работа, и наоборот
        self.assertEqual([len(line) for _work, line in pieces], [2, 3, 2])
        self.assertEqual(pieces[1][1][0], pieces[0][1][-1])
        self.assertEqual(pieces[2][1][0], pieces[1][1][-1])

    def test_a_gap_longer_than_the_cap_breaks_the_line(self):
        points = [(0, 0.0, 0.0, 8.0, 12), (10, 1.0, 0.0, 8.0, 12),
                  (311, 2.0, 0.0, 8.0, 12), (321, 3.0, 0.0, 8.0, 12)]
        pieces = kml.track_pieces(points)
        self.assertEqual([line for _work, line in pieces],
                         [[(0.0, 0.0), (1.0, 0.0)], [(2.0, 0.0), (3.0, 0.0)]])
        # ровно на потолке линия не рвётся
        points[2] = (310, 2.0, 0.0, 8.0, 12)
        self.assertEqual(len(kml.track_pieces(points)), 1)

    def test_a_lone_point_draws_nothing(self):
        self.assertEqual(kml.track_pieces([(0, 0.0, 0.0, 8.0, 12)]), [])


class TheFile(World):

    def test_the_site_keeps_lon_lat_order_and_its_hole(self):
        code, _out, _err = self.run_main('%s:%d' % (DAY, TRACTOR))
        self.assertEqual(code, 0)
        root = self.document()
        polygon = root.find('.//k:Polygon', NS)
        outer = polygon.find('k:outerBoundaryIs/k:LinearRing/k:coordinates',
                             NS).text.split()
        self.assertEqual(outer[0], '64.500000,39.900000')
        self.assertEqual(outer[1], '64.520000,39.900000')
        self.assertEqual(len(polygon.findall('k:innerBoundaryIs', NS)), 1)

    def test_the_folder_names_the_machine_as_the_screen_does(self):
        self.run_main('%s:%d' % (DAY, TRACTOR))
        folder = self.document().find('.//k:Folder', NS)
        self.assertEqual(folder.find('k:name', NS).text,
                         'МТЗ-80.1 — 80 239 NA · 26.09.2026')
        names = [p.find('k:name', NS).text
                 for p in folder.findall('k:Placemark', NS)]
        self.assertIn('Участок / участка 2 — 68,73 га', names)
        description = folder.find('k:Placemark/k:description', NS).text
        self.assertIn('4,6', description)

    def test_the_track_is_drawn_in_pieces_and_breaks_on_the_pause(self):
        self.run_main('%s:%d' % (DAY, TRACTOR))
        folder = self.document().find('.//k:Folder', NS)
        tracks = {p.find('k:styleUrl', NS).text:
                  len(p.findall('k:MultiGeometry/k:LineString', NS))
                  for p in folder.findall('k:Placemark', NS)
                  if p.find('k:MultiGeometry', NS) is not None}
        # работа, переезд, работа; пауза 400 с -- ещё кусок работы
        self.assertEqual(tracks, {'#work': 3, '#move': 1})

    def test_an_unpublished_day_without_points_is_named_so(self):
        code, out, _err = self.run_main('2026-09-13:%d' % CAR)
        self.assertEqual(code, 0)
        folder = self.document().find('.//k:Folder', NS)
        title = folder.find('k:name', NS).text
        self.assertTrue(title.startswith('7286 · 13.09.2026'))
        self.assertIn('sbor_nepolnyy', title)
        names = [p.find('k:name', NS).text
                 for p in folder.findall('k:Placemark', NS)]
        # файл за сентябрь есть, точек этой машины за эти сутки в нём нет
        self.assertEqual(names, ['Точек за эти сутки нет / бу кун учун '
                                 'нуқталар йўқ'])
        self.assertIn('points 0', out)

    def test_a_month_without_a_point_file_is_named_so(self):
        code, out, _err = self.run_main('2026-10-01:%d' % TRACTOR)
        self.assertEqual(code, 0)
        names = [p.find('k:name', NS).text
                 for p in self.document().findall('.//k:Placemark', NS)]
        self.assertEqual(names, ['Файла точек за этот месяц нет / бу ой учун '
                                 'нуқталар файли йўқ'])
        self.assertIn('no file', out)
        self.assertFalse(os.path.exists(
            storage.points_path(self.folder, '202610')))

    def test_a_day_without_a_row_is_named_so(self):
        self.run_main('2026-09-25:%d' % TRACTOR)
        title = self.document().find('.//k:Folder/k:name', NS).text
        self.assertIn('нет суточной строки', title)

    def test_xml_special_characters_are_escaped(self):
        con = sqlite3.connect(self.db)
        try:
            con.execute("UPDATE equipment SET name = 'A&B <МТЗ>' WHERE id = 1")
            con.commit()
        finally:
            con.close()
        code, _out, _err = self.run_main('%s:%d' % (DAY, TRACTOR))
        self.assertEqual(code, 0)
        title = self.document().find('.//k:Folder/k:name', NS).text
        self.assertTrue(title.startswith('A&B <МТЗ> — 80 239 NA'))

    def test_two_days_make_two_folders_and_nothing_is_written(self):
        before = (self.digest(self.db), self.digest(self.points_file))
        code, out, _err = self.run_main('%s:%d' % (DAY, TRACTOR),
                                        '2026-09-13:%d' % CAR)
        self.assertEqual(code, 0)
        self.assertEqual(len(self.document().findall('.//k:Folder', NS)), 2)
        self.assertTrue(out.isascii())
        self.assertIn('MTZ-80.1 - 80 239 NA', out)
        self.assertEqual(before, (self.digest(self.db),
                                  self.digest(self.points_file)))

    def test_refusals(self):
        for pair in ('26.09.2026:393', '2026-09-26', '2026-09-26:abc'):
            with self.subTest(pair=pair):
                code, _out, err = self.run_main(pair)
                self.assertEqual(code, 2)
                self.assertIn('--day must look like', err)
        self.assertFalse(os.path.exists(self.out))
        missing = os.path.join(self.folder, 'nope.db')
        with contextlib.redirect_stderr(io.StringIO()) as err:
            code = kml.main(['--db', missing, '--dir', self.folder,
                             '--day', '%s:%d' % (DAY, TRACTOR),
                             '--out', self.out])
        self.assertEqual(code, 2)
        self.assertIn('no database', err.getvalue())
        self.assertFalse(os.path.exists(missing))


if __name__ == '__main__':
    unittest.main()
