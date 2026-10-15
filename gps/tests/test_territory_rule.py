# -*- coding: utf-8 -*-
"""Правило территорий в движке (gps/territory.py, дорожная карта §2.14).

Что проверяется:
  1. разрез куска -- доля >= 0,5 уходит целиком, меньше -- вычитание, мелкие
     остатки отпадают, кусок вне территории не трогается;
  2. зоны из зеркала -- как у расчёта и у GPS-19: выключенная строка не
     контур, зона больше 50 га вне правила, чужой источник не берётся;
  3. список зон движка читается строго и совпадает с последним списком
     владельца в docs/;
  4. ночной расчёт: выключено -- ничего не читается и сутки те же, что
     были; включено -- только сутки с датой не раньше TERRITORY_FROM, с
     меткой правила, территория грузится один раз на прогон;
  5. движок и замер GPS-19 на одних и тех же участках дают одно и то же.

Запуск (геостек, см. gps/README.md):
  python -m unittest discover -s gps/tests -t .
"""

import csv
import glob
import io
import json
import os
import random
import re
import sqlite3
import sys
import tempfile
import unittest
from unittest import mock

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(
    os.path.abspath(__file__))))
if REPO_ROOT not in sys.path:
    sys.path.insert(0, REPO_ROOT)

import shapely                                                      # noqa: E402
from pyproj import Transformer                                      # noqa: E402
from shapely.geometry import box                                    # noqa: E402

import migration_utils                                             # noqa: E402
import migrate_gps_daily_001 as gps_mig                            # noqa: E402
from gps import daily                                              # noqa: E402
from gps import territory                                          # noqa: E402
from gps.area import (METHOD_VERSION, MIN_WORK_AREA_HA,             # noqa: E402
                      TERRITORY_RULE_VERSION, UTM_41N, method_version)
from gps.tests.test_daily import (FIELD_CONTOURS_DDL,               # noqa: E402
                                  read_fixture_track, read_fixture_zone,
                                  zone_geojson)
from gps_collector import storage                                  # noqa: E402

DAY = "2026-07-27"
UNIT = 3464
QUIET = lambda *a: None                                            # noqa: E731
_TO_WGS = Transformer.from_crs(UTM_41N, "EPSG:4326", always_xy=True)


def ha(geometry):
    return geometry.area / 10000.0


OWNER_LIST_NAME = re.compile(
    r"^gps_territory_zones_(\d{4}-\d{2}-\d{2})(?:_v(\d+))?\.csv$")


def owner_lists(folder=None):
    """Списки владельца: {(дата, номер): путь} по docs/gps_territory_zones_*.csv.

    Дата и номер берутся из имени, а не порядком строк: чужой файл с тем же
    началом имени (выгрузка зон GPS-19, «_v3x», «-копия») списком не
    считается. N в «_vN» -- сквозной номер версии списка: v1 -- 14.10, v2 --
    15.10 по ответу утром (без номера: первый за сутки), v3 -- 15.10 по
    ответу вечером. Списки одной даты упорядочены по N (без номера -- 0),
    поэтому следующий список той же даты обязан нести N больше всех уже
    взятых: следующий за 15.10 -- `_v4`. Два списка с одним ключом (`_v3` и
    `_v03`, без номера и `_v0`) -- ошибка.
    """
    found = {}
    folder = folder or os.path.join(REPO_ROOT, "docs")
    for path in glob.glob(os.path.join(folder, "gps_territory_zones_*.csv")):
        name = OWNER_LIST_NAME.match(os.path.basename(path))
        if name:
            key = (name.group(1), int(name.group(2) or 0))
            if key in found:
                raise AssertionError("two zone lists for %s v%d" % key)
            found[key] = path
    return found


def latest_owner_list(folder=None):
    lists = owner_lists(folder)
    return lists[max(lists)]


def label_of(path):
    """Метка, которую требует список: territory-<дата>[_vN] из его имени."""
    name = OWNER_LIST_NAME.match(os.path.basename(path))
    return "territory-" + name.group(1) + (
        "_v%d" % int(name.group(2)) if name.group(2) else "")


def square(x, y, side):
    return box(x, y, x + side, y + side)


def geojson_from_utm(polygon):
    """Многоугольник UTM -> GeoJSON в градусах, как его пишет зеркало."""
    xs, ys = polygon.exterior.coords.xy
    lons, lats = _TO_WGS.transform(list(xs), list(ys))
    return json.dumps({"type": "Polygon",
                       "coordinates": [[[lon, lat] for lon, lat in zip(lons, lats)]]})


# --- 1. разрез куска -----------------------------------------------------------

class Cut(unittest.TestCase):
    """Territory.cut -- правило п. 2 на одном куске."""

    def setUp(self):
        # двор 100 x 100 м (1 га) в начале координат
        self.yard = square(0, 0, 100)
        self.territory = territory.Territory({1: self.yard})

    def test_a_piece_away_from_the_yard_comes_back_untouched(self):
        piece = square(1000, 1000, 100)
        share, kept = self.territory.cut(piece)
        self.assertEqual(share, 0.0)
        self.assertEqual(len(kept), 1)
        self.assertIs(kept[0], piece)

    def test_a_piece_touching_the_yard_only_along_an_edge_is_untouched(self):
        piece = square(100, 0, 100)                 # общая сторона, площади 0
        share, kept = self.territory.cut(piece)
        self.assertEqual(share, 0.0)
        self.assertIs(kept[0], piece)

    def test_a_piece_on_the_yard_by_half_or_more_is_dropped_whole(self):
        piece = box(0, 0, 200, 100)                  # ровно половина на дворе
        share, kept = self.territory.cut(piece)
        self.assertEqual(share, 0.5)
        self.assertEqual(kept, [])

    def test_a_piece_inside_the_yard_is_dropped(self):
        share, kept = self.territory.cut(square(10, 10, 60))
        self.assertEqual(share, 1.0)
        self.assertEqual(kept, [])

    def test_below_half_the_yard_is_subtracted(self):
        piece = box(0, 0, 300, 100)                  # 3 га, 1 га на дворе
        share, kept = self.territory.cut(piece)
        self.assertAlmostEqual(share, 0.333, places=3)
        self.assertEqual(len(kept), 1)
        self.assertAlmostEqual(ha(kept[0]), 2.0, places=6)
        self.assertAlmostEqual(ha(kept[0].intersection(self.yard)), 0.0, places=9)

    def test_a_leftover_smaller_than_the_floor_is_passage_and_drops(self):
        # полоса поперёк двора: слева 0,12 га, справа 1,8 га, на дворе 0,6 га
        # (доля 0,238) -- левый обрезок меньше 0,3 га и отпадает
        share, kept = self.territory.cut(box(-20, 20, 400, 80))
        self.assertAlmostEqual(share, 0.238, places=3)
        self.assertEqual(len(kept), 1)
        self.assertAlmostEqual(ha(kept[0]), 1.8, places=6)

    def test_a_leftover_of_exactly_the_floor_is_kept(self):
        # как у GPS-19: остаток ровно 0,3 га -- участок (>=), а не проезд
        _share, kept = self.territory.cut(box(-30, 0, 270, 100))
        self.assertEqual(sorted(round(ha(part), 6) for part in kept), [0.3, 1.7])

    def test_the_floor_is_the_caller_s(self):
        _share, kept = self.territory.cut(box(-20, 20, 400, 80), min_area_ha=0.0)
        self.assertEqual(sorted(round(ha(part), 6) for part in kept), [0.12, 1.8])

    def test_a_piece_cut_in_two_by_the_yard_keeps_both_halves(self):
        yard = box(100, -50, 140, 150)              # полоса поперёк куска
        rule = territory.Territory({1: yard})
        share, kept = rule.cut(box(0, 0, 300, 100))
        self.assertLess(share, 0.5)
        self.assertEqual(len(kept), 2)
        self.assertAlmostEqual(sum(ha(part) for part in kept), 2.6, places=6)

    def test_the_share_is_compared_rounded_like_the_measurement(self):
        # доля 0,4996 округляется до 0,5 -- как столбец `dolya` GPS-19
        piece = box(0, 0, 200.16, 100)
        share, kept = self.territory.cut(piece)
        self.assertEqual(share, 0.5)
        self.assertEqual(kept, [])
        # 0,4994 -> 0,499: вычитание
        share, kept = self.territory.cut(box(0, 0, 200.25, 100))
        self.assertEqual(share, 0.499)
        self.assertEqual(len(kept), 1)

    def test_two_overlapping_zones_are_one_territory(self):
        rule = territory.Territory({1: box(0, 0, 100, 100),
                                    2: box(50, 0, 150, 100)})
        share, kept = rule.cut(box(0, 0, 400, 100))   # 1,5 га на 4 га
        self.assertAlmostEqual(share, 0.375, places=3)
        self.assertAlmostEqual(sum(ha(part) for part in kept), 2.5, places=6)

    def test_a_self_intersecting_piece_is_cut_on_its_repair(self):
        # «бабочка», на которой сырое пересечение с двором падает в GEOS
        # (TopologyException: side location conflict)
        bowtie = shapely.Polygon([(-80, -80), (150, 90), (150, -80), (-80, 90)])
        self.assertFalse(bowtie.is_valid)
        from shapely.errors import GEOSException
        with self.assertRaises(GEOSException):
            bowtie.difference(self.yard)
        share, kept = self.territory.cut(bowtie)       # не GEOSException
        self.assertGreater(share, 0.0)
        self.assertTrue(all(part.is_valid for part in kept))

    def test_an_empty_piece_is_nothing(self):
        self.assertEqual(self.territory.cut(shapely.Polygon()), (0.0, []))
        self.assertEqual(self.territory.cut(None), (0.0, []))

    def test_a_territory_without_zones_is_refused(self):
        with self.assertRaises(ValueError):
            territory.Territory({})


# --- 2. зоны из зеркала ---------------------------------------------------------

class LoadTerritory(unittest.TestCase):

    def setUp(self):
        self.con = sqlite3.connect(":memory:")
        self.con.execute(FIELD_CONTOURS_DDL)
        self.lines = []
        origin_x, origin_y = 300000.0, 4430000.0      # UTM 41N, у Бухары
        self.small = square(origin_x, origin_y, 100)                 # 1 га
        self.large = square(origin_x + 5000, origin_y, 800)          # 64 га

    def add(self, row_id, external, polygon, is_active=1, source="wialon"):
        self.con.execute(
            "INSERT INTO field_contours (id, source, external_id, name, "
            "geometry_geojson, is_active) VALUES (?, ?, ?, 'z', ?, ?)",
            (row_id, source, str(external),
             None if polygon is None else geojson_from_utm(polygon), is_active))

    def load(self, ids, pins=None):
        """Зоны с площадью при замере: по умолчанию -- их нынешние контуры."""
        areas = {501: 1.0, 502: 64.0, 503: 1.0, 999: 1.0}
        areas.update(pins or {})
        return territory.load_territory(
            self.con, {zone: areas[zone] for zone in ids},
            log=self.lines.append)

    def test_listed_zones_with_an_outline_make_the_territory(self):
        self.add(1, 501, self.small)
        rule = self.load([501])
        self.assertEqual(sorted(rule.zones), [501])
        self.assertAlmostEqual(ha(rule.union), 1.0, places=2)

    def test_a_zone_larger_than_50_ha_is_left_out_and_named(self):
        self.add(1, 501, self.small)
        self.add(2, 502, self.large)
        rule = self.load([501, 502])
        self.assertEqual(sorted(rule.zones), [501])
        self.assertIn(502, rule.left_out)
        self.assertTrue(any("502" in line for line in self.lines))

    def test_a_switched_off_row_is_not_an_outline(self):
        self.add(1, 501, self.small, is_active=0)
        self.assertIsNone(self.load([501]))

    def test_an_empty_is_active_counts_as_on(self):
        self.add(1, 501, self.small, is_active=None)
        self.assertEqual(sorted(self.load([501]).zones), [501])

    def test_another_source_is_not_a_wialon_zone(self):
        self.add(1, 501, self.small, source="manual")
        self.assertIsNone(self.load([501]))

    def test_a_zone_not_in_the_mirror_is_left_out_not_fatal(self):
        self.add(1, 501, self.small)
        rule = self.load([501, 999])
        self.assertEqual(sorted(rule.zones), [501])
        self.assertEqual(rule.left_out[999], "no outline")

    def test_no_zone_at_all_is_none_and_says_the_rule_is_not_applied(self):
        self.assertIsNone(self.load([999]))
        self.assertTrue(any("NOT applied" in line for line in self.lines))

    def test_an_unlisted_zone_is_not_territory(self):
        self.add(1, 501, self.small)
        self.add(2, 503, square(300000.0 + 2000, 4430000.0, 100))
        self.assertEqual(sorted(self.load([501]).zones), [501])

    def test_a_zone_gps19_did_not_measure_is_left_out(self):
        # 15.10: зона 18402 была в списке без контура -- условие п. 5 её не
        # мерило; контур, появившийся позже в зеркале, в правило не входит.
        self.add(1, 501, self.small)
        self.add(2, 503, square(300000.0 + 2000, 4430000.0, 100))
        rule = self.load([501, 503], pins={503: None})
        self.assertEqual(sorted(rule.zones), [501])
        self.assertEqual(rule.left_out[503], 'not measured by GPS-19')

    def test_an_outline_redrawn_since_the_measurement_is_left_out(self):
        # зеркало переписывает геометрию изменившихся зон: 1 га стал 1,21 га
        self.add(1, 501, square(300000.0, 4430000.0, 110))
        self.assertIsNone(self.load([501], pins={501: 1.0}))
        self.assertTrue(any('outline changed' in line for line in self.lines))

    def test_the_same_outline_within_the_tolerance_is_kept(self):
        self.add(1, 501, self.small)
        rule = self.load([501], pins={501: 1.0 + territory.PIN_TOLERANCE_HA / 2})
        self.assertEqual(sorted(rule.zones), [501])

    def test_a_self_intersecting_zone_is_repaired_not_fatal(self):
        # «бабочка», как рисуют мышкой: без починки union_all падает в GEOS
        x, y = 300000.0, 4430000.0
        bowtie = shapely.Polygon([(x, y), (x + 100, y + 100), (x + 100, y),
                                  (x, y + 100)])
        self.assertFalse(bowtie.is_valid)
        self.add(1, 501, None)
        self.con.execute("UPDATE field_contours SET geometry_geojson = ? "
                         "WHERE id = 1", (geojson_from_utm(bowtie),))
        repaired = territory.zone_polygon(geojson_from_utm(bowtie))
        rule = self.load([501], pins={501: ha(repaired)})
        self.assertEqual(sorted(rule.zones), [501])
        self.assertTrue(rule.union.is_valid)

    def test_a_zone_of_exactly_50_ha_is_in_the_rule(self):
        # п. 1: больше 50 га -- вне правила; ровно 50 -- в правиле (как GPS-19)
        exact = shapely.Polygon([(300000.0, 4430000.0), (300500.0, 4430000.0),
                                 (300500.0, 4431000.0), (300000.0, 4431000.0)])
        self.add(1, 501, exact)
        area = ha(territory.zone_polygon(geojson_from_utm(exact)))
        with mock.patch.object(territory, "LARGE_ZONE_HA", area):
            self.assertEqual(sorted(self.load([501], pins={501: area}).zones),
                             [501])
        with mock.patch.object(territory, "LARGE_ZONE_HA", area - 1e-6):
            self.assertIsNone(self.load([501], pins={501: area}))

    def test_the_pin_tolerance_is_0_01_ha(self):
        self.add(1, 501, self.small)
        area = ha(territory.zone_polygon(geojson_from_utm(self.small)))
        self.assertIsNotNone(self.load([501], pins={501: area + 0.009}))
        self.assertIsNone(self.load([501], pins={501: area + 0.011}))

    def test_the_zone_area_is_the_one_gps19_measures(self):
        # движок и замер читают контур одной обработкой: площади совпадают
        from gps.daily import _utm_polygon_from_geojson
        text = geojson_from_utm(square(300000.0, 4430000.0, 137.5))
        self.assertAlmostEqual(ha(territory.zone_polygon(text)),
                               ha(_utm_polygon_from_geojson(text)), places=9)


# --- 3. список зон ----------------------------------------------------------------

class ZoneList(unittest.TestCase):

    def write(self, text):
        handle = tempfile.NamedTemporaryFile("w", suffix=".csv", delete=False,
                                             encoding="utf-8")
        handle.write(text)
        handle.close()
        self.addCleanup(os.unlink, handle.name)
        return handle.name

    HEADER = "zona_id,gruppa_id,tip,ploshchad_ga\n"

    def test_a_good_list_reads(self):
        path = self.write(self.HEADER + "1,16,azs,16.7744\n18402,3,azs,\n")
        self.assertEqual(territory.read_zone_list(path),
                         {1: 16.7744, 18402: None})

    def test_a_repeated_zone_is_refused(self):
        path = self.write(self.HEADER + "1,16,azs,1.0\n1,3,azs,1.0\n")
        with self.assertRaises(territory.BadZoneList):
            territory.read_zone_list(path)

    def test_a_non_number_is_refused(self):
        for zone in ("x1", "0", "-3", ""):
            with self.assertRaises(territory.BadZoneList, msg=zone):
                territory.read_zone_list(
                    self.write(self.HEADER + "%s,16,azs,1.0\n" % zone))

    def test_a_list_saved_with_a_bom_reads(self):
        # «CSV UTF-8» из Excel пишет BOM -- ночной расчёт не должен падать
        path = self.write("\ufeff" + self.HEADER + "1,16,azs,1.5\r\n")
        self.assertEqual(territory.read_zone_list(path), {1: 1.5})

    def test_a_bad_area_is_refused(self):
        for area in ("abc", "0", "-1.5"):
            with self.assertRaises(territory.BadZoneList, msg=area):
                territory.read_zone_list(
                    self.write(self.HEADER + "1,16,azs,%s\n" % area))

    def test_an_empty_list_is_refused(self):
        with self.assertRaises(territory.BadZoneList):
            territory.read_zone_list(self.write(self.HEADER))

    def test_a_list_without_the_columns_is_refused(self):
        with self.assertRaises(territory.BadZoneList):
            territory.read_zone_list(self.write("id,group\n1,16\n"))
        # список владельца из docs/ без площадей движку не годится
        with self.assertRaises(territory.BadZoneList):
            territory.read_zone_list(self.write("zona_id,gruppa_id,tip\n1,16,azs\n"))

    def test_a_missing_file_is_refused(self):
        with self.assertRaises(territory.BadZoneList):
            territory.read_zone_list("/nonexistent/territory_zones.csv")

    def test_the_engine_list_is_the_owner_s_latest_list(self):
        # [REASON]: список движка -- последний список владельца в docs/ плюс
        # площадь каждой зоны при замере GPS-19. Разойдись они, правило
        # снимало бы гектары по одному списку, а замер и документы говорили
        # бы о другом.
        latest = latest_owner_list()
        with open(latest, encoding="utf-8") as owner:
            owner_rows = list(csv.DictReader(owner))
        with open(territory.ZONES_PATH, encoding="utf-8") as engine:
            engine_rows = list(csv.DictReader(engine))
        self.assertEqual(
            [(row["zona_id"], row["gruppa_id"], row["tip"]) for row in engine_rows],
            [(row["zona_id"], row["gruppa_id"], row["tip"]) for row in owner_rows],
            "gps/data/territory_zones.csv != %s" % latest)

    def test_the_engine_list_reads_and_most_zones_are_measured(self):
        zones = territory.read_zone_list()
        measured = [zone for zone, area in zones.items() if area is not None]
        self.assertGreater(len(measured), len(zones) // 2)

    def test_the_label_date_is_the_date_of_the_list(self):
        # [REASON]: новый список -- новая метка: сутки, порезанные разными
        # списками, не должны носить одну метку.
        self.assertEqual(TERRITORY_RULE_VERSION, label_of(latest_owner_list()))


class OwnerListNames(unittest.TestCase):
    """Какой список в docs/ последний и какую метку он требует."""

    def setUp(self):
        self.folder = tempfile.mkdtemp()

    def put(self, *names):
        for name in names:
            with open(os.path.join(self.folder, name), "w", encoding="utf-8") as f:
                f.write("zona_id,gruppa_id,tip\n")

    def latest(self):
        return os.path.basename(latest_owner_list(self.folder))

    def test_the_second_list_of_a_day_is_later_than_the_first(self):
        self.put("gps_territory_zones_2026-10-14.csv",
                 "gps_territory_zones_2026-10-15.csv",
                 "gps_territory_zones_2026-10-15_v3.csv")
        self.assertEqual(self.latest(), "gps_territory_zones_2026-10-15_v3.csv")

    def test_a_later_day_beats_a_numbered_list(self):
        self.put("gps_territory_zones_2026-10-15_v3.csv",
                 "gps_territory_zones_2026-10-16.csv")
        self.assertEqual(self.latest(), "gps_territory_zones_2026-10-16.csv")

    def test_numbers_compare_as_numbers(self):
        self.put("gps_territory_zones_2026-10-15_v9.csv",
                 "gps_territory_zones_2026-10-15_v10.csv")
        self.assertEqual(self.latest(), "gps_territory_zones_2026-10-15_v10.csv")

    def test_foreign_files_are_not_lists(self):
        # [REASON]: все чужие имена проходят маску glob и несут дату позже
        # 14.10 -- просочись одно через регулярное выражение, оно стало бы
        # «последним списком», и тест это увидит.
        self.put("gps_territory_zones_2026-10-14.csv",
                 "gps_territory_zones_2026-10-15_v3x.csv",
                 "gps_territory_zones_2026-10-15-kopiya.csv",
                 "gps_territory_zones_2026-10-15_v.csv",
                 "gps_territory_zones_2026-10-15.csv.csv",
                 "gps_territory_zones_20261016.csv",
                 "gps_territory_zones_2026-10-15-v4.csv")
        self.assertEqual(self.latest(), "gps_territory_zones_2026-10-14.csv")

    def test_two_lists_with_one_key_are_an_error(self):
        self.put("gps_territory_zones_2026-10-15_v3.csv",
                 "gps_territory_zones_2026-10-15_v03.csv")
        with self.assertRaises(AssertionError):
            owner_lists(self.folder)
        other = tempfile.mkdtemp()
        for name in ("gps_territory_zones_2026-10-15.csv",
                     "gps_territory_zones_2026-10-15_v0.csv"):
            with open(os.path.join(other, name), "w", encoding="utf-8") as f:
                f.write("zona_id,gruppa_id,tip\n")
        with self.assertRaises(AssertionError):
            owner_lists(other)

    def test_the_label_follows_the_name(self):
        self.assertEqual(label_of("docs/gps_territory_zones_2026-10-15.csv"),
                         "territory-2026-10-15")
        self.assertEqual(label_of("docs/gps_territory_zones_2026-10-15_v3.csv"),
                         "territory-2026-10-15_v3")
        self.assertEqual(label_of("gps_territory_zones_2026-10-15_v03.csv"),
                         "territory-2026-10-15_v3")


# --- 4. ночной расчёт -------------------------------------------------------------

class NightlyRule(unittest.TestCase):
    """Сутки 3464 за 27.07 (фикстура) против двора на месте зоны 3208."""

    def setUp(self):
        self.folder = tempfile.mkdtemp()
        self.db = os.path.join(self.folder, "transport.db")
        con = sqlite3.connect(self.db)
        con.execute(FIELD_CONTOURS_DDL)
        con.commit()
        con.close()
        self._saved = (gps_mig.DB_PATH, migration_utils.DB_PATH)
        gps_mig.DB_PATH = self.db
        migration_utils.DB_PATH = self.db
        gps_mig.run()
        self.track = read_fixture_track()
        storage.write_points(self.folder, [
            (UNIT, t, lon, lat, speed, 90, sats)
            for t, lon, lat, speed, sats in self.track])
        self.zones = os.path.join(self.folder, "zones.csv")
        self.pin(zone_geojson(read_fixture_zone()))
        self.patches = [mock.patch.object(territory, "ZONES_PATH", self.zones)]
        for patch in self.patches:
            patch.start()
            self.addCleanup(patch.stop)

    def tearDown(self):
        gps_mig.DB_PATH, migration_utils.DB_PATH = self._saved

    def pin(self, geometry_geojson):
        """Список движка: зона 3208 с площадью этого контура, как при замере."""
        area = ha(territory.zone_polygon(geometry_geojson))
        with open(self.zones, "w", encoding="utf-8") as handle:
            handle.write("zona_id,gruppa_id,tip,ploshchad_ga\n3208,16,ptm,%.4f\n"
                         % area)

    def mirror(self, geometry_geojson, external="3208", row_id=77):
        con = sqlite3.connect(self.db)
        try:
            con.execute("INSERT INTO field_contours (id, source, external_id, "
                        "name, geometry_geojson, is_active) VALUES "
                        "(?, 'wialon', ?, 'yard', ?, 1)",
                        (row_id, external, geometry_geojson))
            con.commit()
        finally:
            con.close()

    def stored(self):
        con = sqlite3.connect(self.db)
        try:
            aggregate = con.execute(
                "SELECT method_version, reason FROM gps_daily_aggregates "
                "WHERE work_date = ? AND wialon_id = ?", (DAY, UNIT)).fetchone()
            sites = con.execute(
                "SELECT area_ha FROM gps_work_polygons WHERE work_date = ? AND "
                "wialon_id = ? ORDER BY site_number", (DAY, UNIT)).fetchall()
        finally:
            con.close()
        return aggregate, [row[0] for row in sites]

    def run_day(self, **kwargs):
        return daily.run_day(DAY, UNIT, folder=self.folder, db_path=self.db,
                             log=QUIET, **kwargs)

    def test_switched_off_nothing_is_read_and_the_day_is_as_before(self):
        self.mirror(zone_geojson(read_fixture_zone()))
        with mock.patch.object(territory, "TERRITORY_FROM", None), \
                mock.patch.object(territory, "read_zone_list",
                                  side_effect=AssertionError("read")):
            result = self.run_day()
        self.assertIsNone(result.reason)
        aggregate, sites = self.stored()
        self.assertEqual(aggregate[0], METHOD_VERSION)
        self.assertEqual(len(sites), 1)
        self.assertAlmostEqual(sites[0], 8.77, places=2)

    def test_the_default_is_off(self):
        self.assertIsNone(territory.TERRITORY_FROM)

    def test_on_a_day_on_the_yard_has_no_hectares_and_says_why(self):
        self.mirror(zone_geojson(read_fixture_zone()))
        with mock.patch.object(territory, "TERRITORY_FROM", DAY):
            result = self.run_day()
        self.assertIsNone(result.reason)
        aggregate, sites = self.stored()
        self.assertEqual(aggregate[0], METHOD_VERSION + "+" + TERRITORY_RULE_VERSION)
        self.assertEqual(sites, [])

    def test_a_day_before_the_start_date_is_computed_without_the_rule(self):
        self.mirror(zone_geojson(read_fixture_zone()))
        with mock.patch.object(territory, "TERRITORY_FROM", "2026-07-28"):
            self.run_day()
        aggregate, sites = self.stored()
        self.assertEqual(aggregate[0], METHOD_VERSION)
        self.assertAlmostEqual(sites[0], 8.77, places=2)

    def test_the_start_date_itself_is_inside(self):
        self.assertTrue(territory.rule_applies(DAY, DAY))
        self.assertFalse(territory.rule_applies("2026-07-26", DAY))
        self.assertFalse(territory.rule_applies(DAY, ""))

    def test_dates_are_compared_as_dates_not_as_text(self):
        # «2026-07-5» как строка больше «2026-07-27», а как дата -- раньше
        self.assertFalse(territory.rule_applies("2026-07-5", DAY))
        self.assertTrue(territory.rule_applies("2026-08-1", DAY))

    def test_a_malformed_start_date_is_an_error_not_off(self):
        for start in ("27.07.2026", "2026-7-27x", "yesterday", "2026-7-27",
                      "2026-07-27 "):
            with self.assertRaises(ValueError, msg=start):
                territory.rule_applies(DAY, start)

    def test_the_start_date_in_the_code_is_off_or_a_real_date(self):
        if territory.TERRITORY_FROM is not None:
            territory.rule_applies(territory.TERRITORY_FROM)
            self.assertRegex(territory.TERRITORY_FROM, r"^\d{4}-\d{2}-\d{2}$")

    def test_a_day_without_motion_after_the_start_carries_no_label(self):
        # метка -- только у суток, чьи участки правило резало
        standing = [(t, lon, lat, 0.0, sats)
                    for t, lon, lat, _speed, sats in self.track[:50]]
        rule = territory.Territory({1: square(0, 0, 100)})
        result = daily.compute_day(standing, territory=rule)
        self.assertEqual(result.reason, daily.REASON_NO_MOTION)
        self.assertEqual(result.aggregate["method_version"], METHOD_VERSION)

    def test_part_of_the_site_on_a_yard_is_subtracted(self):
        # двор -- западная треть рамки участка: доля меньше половины
        result = daily.compute_day(self.track)
        site = shapely.geometry.shape(json.loads(result.sites[0]["polygon_geojson"]))
        minx, miny, maxx, maxy = site.bounds
        yard = shapely.geometry.box(minx - 0.001, miny - 0.001,
                                    minx + (maxx - minx) / 3, maxy + 0.001)
        self.mirror(json.dumps(shapely.geometry.mapping(yard)))
        self.pin(json.dumps(shapely.geometry.mapping(yard)))
        with mock.patch.object(territory, "TERRITORY_FROM", DAY):
            self.run_day()
        aggregate, sites = self.stored()
        self.assertTrue(aggregate[0].endswith("+" + TERRITORY_RULE_VERSION))
        self.assertGreater(sum(sites), 0.3)
        self.assertLess(sum(sites), 8.77 - 0.3)

    def test_a_day_far_from_every_yard_still_says_it_was_checked(self):
        # метка -- «посчитано с правилом», а не «правило что-то сняло»
        rule = territory.Territory({1: square(0, 0, 100)})
        result = daily.compute_day(self.track, territory=rule)
        self.assertEqual(result.aggregate["method_version"],
                         METHOD_VERSION + "+" + TERRITORY_RULE_VERSION)
        self.assertAlmostEqual(sum(site["area_ha"] for site in result.sites),
                               8.772, places=3)

    def site_piece(self):
        from gps.area import work_sites
        sites, _ = work_sites(self.track)
        self.assertEqual(len(sites), 1)
        return sites[0].polygon

    def expected(self, piece, yard):
        """Гибрид, посчитанный без Territory.cut: доля, вычитание, порог."""
        share = round(piece.intersection(yard).area / piece.area, 3)
        if share >= 0.5:
            return []
        rest = piece.difference(yard)
        parts = list(getattr(rest, "geoms", [rest]))
        return sorted(round(ha(part), 6) for part in parts
                      if ha(part) >= MIN_WORK_AREA_HA)

    def test_the_engine_keeps_exactly_the_hybrid(self):
        from gps.area import work_sites
        piece = self.site_piece()
        minx, miny, maxx, maxy = piece.bounds
        # двор -- западная треть участка
        yard = box(minx - 1, miny - 1, minx + (maxx - minx) / 3, maxy + 1)
        sites, _ = work_sites(self.track,
                              territory=territory.Territory({1: yard}))
        got = sorted(round(site.area_ha, 6) for site in sites)
        self.assertEqual(got, self.expected(piece, yard))
        self.assertTrue(all(site.method_version.endswith(TERRITORY_RULE_VERSION)
                            for site in sites))

    def test_a_yard_cutting_the_site_in_two_leaves_two_sites(self):
        from gps.area import work_sites
        piece = self.site_piece()
        minx, miny, maxx, maxy = piece.bounds
        middle = (minx + maxx) / 2
        yard = box(middle - 15, miny - 1, middle + 15, maxy + 1)   # полоса 30 м
        expected = self.expected(piece, yard)
        self.assertEqual(len(expected), 2, expected)
        sites, _ = work_sites(self.track,
                              territory=territory.Territory({1: yard}))
        self.assertEqual(sorted(round(site.area_ha, 6) for site in sites),
                         expected)

    def test_a_zone_that_is_not_listed_takes_nothing(self):
        self.mirror(zone_geojson(read_fixture_zone()), external="4444")
        with mock.patch.object(territory, "TERRITORY_FROM", DAY):
            self.run_day()
        aggregate, sites = self.stored()
        # правило включено, но зон с контуром нет -- не применено, без метки
        self.assertEqual(aggregate[0], METHOD_VERSION)
        self.assertAlmostEqual(sites[0], 8.77, places=2)

    def test_a_broken_list_stops_the_day_loudly(self):
        with open(self.zones, "w", encoding="utf-8") as handle:
            handle.write("zona_id,gruppa_id,tip,ploshchad_ga\n"
                         "3208,16,ptm,8.97\n3208,16,ptm,8.97\n")
        with mock.patch.object(territory, "TERRITORY_FROM", DAY):
            with self.assertRaises(territory.BadZoneList):
                self.run_day()

    def test_the_date_run_loads_the_territory_once(self):
        self.mirror(zone_geojson(read_fixture_zone()))
        calls = []
        real = territory.load_territory

        def counting(*args, **kwargs):
            calls.append(1)
            return real(*args, **kwargs)
        out, saved = io.StringIO(), sys.stdout
        sys.stdout = out
        try:
            with mock.patch.object(territory, "TERRITORY_FROM", DAY), \
                    mock.patch.object(territory, "load_territory", counting):
                code = daily.main(["--date", DAY, "--dir", self.folder,
                                   "--db", self.db])
        finally:
            sys.stdout = saved
        self.assertEqual(code, 0, out.getvalue())
        self.assertEqual(len(calls), 1)
        self.assertIn("territory rule %s" % TERRITORY_RULE_VERSION, out.getvalue())
        aggregate, sites = self.stored()
        self.assertEqual(sites, [])

    def test_the_date_run_before_the_start_does_not_read_the_list(self):
        self.mirror(zone_geojson(read_fixture_zone()))
        out, saved = io.StringIO(), sys.stdout
        sys.stdout = out
        try:
            with mock.patch.object(territory, "TERRITORY_FROM", "2026-08-01"), \
                    mock.patch.object(territory, "read_zone_list",
                                      side_effect=AssertionError("read")):
                code = daily.main(["--date", DAY, "--dir", self.folder,
                                   "--db", self.db])
        finally:
            sys.stdout = saved
        self.assertEqual(code, 0, out.getvalue())
        aggregate, sites = self.stored()
        self.assertEqual(aggregate[0], METHOD_VERSION)

    def test_catch_up_applies_the_rule_to_a_day_on_or_after_the_start(self):
        self.mirror(zone_geojson(read_fixture_zone()))
        with mock.patch.object(territory, "TERRITORY_FROM", DAY):
            code = daily.catch_up(self.folder, self.db, log=QUIET, window=1,
                                  until=DAY)
        self.assertEqual(code, 0)
        aggregate, sites = self.stored()
        self.assertTrue(aggregate[0].endswith("+" + TERRITORY_RULE_VERSION))
        self.assertEqual(sites, [])

    def test_catch_up_loads_the_territory_once_for_the_whole_run(self):
        self.mirror(zone_geojson(read_fixture_zone()))
        calls = []
        real = territory.load_territory

        def counting(*args, **kwargs):
            calls.append(1)
            return real(*args, **kwargs)
        with mock.patch.object(territory, "TERRITORY_FROM", DAY), \
                mock.patch.object(territory, "load_territory", counting), \
                mock.patch.object(daily, "run_day",
                                  wraps=daily.run_day) as run_day:
            daily.catch_up(self.folder, self.db, log=QUIET, window=1, until=DAY)
        self.assertEqual(len(calls), 1)
        self.assertIsInstance(run_day.call_args.kwargs["territory"],
                              territory.Territory)

    def test_catch_up_before_the_start_does_not_read_the_list(self):
        self.mirror(zone_geojson(read_fixture_zone()))
        with mock.patch.object(territory, "TERRITORY_FROM", "2026-07-28"), \
                mock.patch.object(territory, "read_zone_list",
                                  side_effect=AssertionError("read")):
            code = daily.catch_up(self.folder, self.db, log=QUIET, window=1,
                                  until=DAY)
        self.assertEqual(code, 0)

    def test_a_broken_list_refuses_catch_up_with_code_2(self):
        with open(self.zones, "w", encoding="utf-8") as handle:
            handle.write("zona_id;gruppa_id;tip;ploshchad_ga\n3208;16;ptm;8.97\n")
        lines = []
        with mock.patch.object(territory, "TERRITORY_FROM", DAY):
            code = daily.catch_up(self.folder, self.db, log=lines.append,
                                  window=1, until=DAY)
        self.assertEqual(code, 2)
        self.assertTrue(any("ERROR: territory rule" in line for line in lines))
        self.assertEqual(self.stored(), (None, []))

    def test_a_broken_list_refuses_the_date_run_with_code_2(self):
        with open(self.zones, "w", encoding="cp1251") as handle:
            handle.write("зона\n1\n")
        err, saved = io.StringIO(), sys.stderr
        sys.stderr = err
        try:
            with mock.patch.object(territory, "TERRITORY_FROM", DAY):
                code = daily.main(["--date", DAY, "--dir", self.folder,
                                   "--db", self.db])
        finally:
            sys.stderr = saved
        self.assertEqual(code, 2)
        self.assertIn("ERROR: territory rule", err.getvalue())
        self.assertEqual(self.stored(), (None, []))

    def test_a_malformed_start_date_refuses_the_date_run(self):
        err, saved = io.StringIO(), sys.stderr
        sys.stderr = err
        try:
            with mock.patch.object(territory, "TERRITORY_FROM", "27.07.2026"):
                code = daily.main(["--date", DAY, "--dir", self.folder,
                                   "--db", self.db])
        finally:
            sys.stderr = saved
        self.assertEqual(code, 2, err.getvalue())

    def test_catch_up_applies_the_rule_to_a_day_finished_by_the_collector(self):
        # проход «ждали сборщика»: сутки дособраны -- считаются с правилом
        self.mirror(zone_geojson(read_fixture_zone()))
        start, finish = daily.day_bounds(DAY)
        storage.set_watermark(self.folder, UNIT, finish - 3600)
        self.run_day()
        self.assertEqual(self.stored()[0][1], daily.REASON_INCOMPLETE)
        storage.set_watermark(self.folder, UNIT, finish)
        with mock.patch.object(territory, "TERRITORY_FROM", DAY):
            code = daily.catch_up(self.folder, self.db, log=QUIET, window=1,
                                  until=DAY)
        self.assertEqual(code, 0)
        aggregate, sites = self.stored()
        self.assertEqual(aggregate, (METHOD_VERSION + "+" + TERRITORY_RULE_VERSION,
                                     None))
        self.assertEqual(sites, [])

    def test_catch_up_applies_the_rule_to_a_day_back_to_hectares(self):
        # проход «разошлись с категорией»: сутки «спецтехники» у полевой
        # машины пересчитываются -- с правилом
        self.mirror(zone_geojson(read_fixture_zone()))
        con = sqlite3.connect(self.db)
        try:
            con.execute("INSERT INTO gps_daily_aggregates (work_date, wialon_id, "
                        "points_total, reason, method_version, computed_at) "
                        "VALUES (?, ?, 10, ?, ?, '2026-07-28 01:00:00')",
                        (DAY, UNIT, daily.REASON_TRACK_ONLY, METHOD_VERSION))
            con.commit()
        finally:
            con.close()
        with mock.patch.object(territory, "TERRITORY_FROM", DAY):
            code = daily.catch_up(self.folder, self.db, log=QUIET, window=1,
                                  until=DAY)
        self.assertEqual(code, 0)
        aggregate, sites = self.stored()
        self.assertEqual(aggregate, (METHOD_VERSION + "+" + TERRITORY_RULE_VERSION,
                                     None))
        self.assertEqual(sites, [])

    def test_catch_up_with_no_usable_zone_looks_once_not_per_day(self):
        # зон с контуром нет: None -- тоже ответ, по суткам его не ищут снова
        storage.write_points(self.folder, [
            (UNIT + 1, t, lon, lat, speed, 90, sats)
            for t, lon, lat, speed, sats in self.track])
        calls = []
        real = territory.load_territory

        def counting(*args, **kwargs):
            calls.append(1)
            return real(*args, **kwargs)
        with mock.patch.object(territory, "TERRITORY_FROM", DAY), \
                mock.patch.object(territory, "load_territory", counting):
            code = daily.catch_up(self.folder, self.db, log=QUIET, window=1,
                                  until=DAY)
        self.assertEqual(code, 0)
        self.assertEqual(len(calls), 1)

    def test_catch_up_switched_off_never_reads_the_list(self):
        with mock.patch.object(territory, "TERRITORY_FROM", None), \
                mock.patch.object(territory, "read_zone_list",
                                  side_effect=AssertionError("read")):
            code = daily.catch_up(self.folder, self.db, log=QUIET, window=1,
                                  until=DAY)
        self.assertEqual(code, 0)
        self.assertEqual(self.stored()[0][0], METHOD_VERSION)

    def test_work_sites_hands_its_floor_to_the_cut(self):
        from gps.area import work_sites
        floors = []

        class Spy:
            def cut(self, piece, min_area_ha=None):
                floors.append(min_area_ha)
                return 0.0, [piece]
        work_sites(self.track, min_area_ha=0.01, territory=Spy())
        self.assertTrue(floors)
        self.assertEqual(set(floors), {0.01})

    def test_catch_up_leaves_the_rule_off_before_the_start(self):
        self.mirror(zone_geojson(read_fixture_zone()))
        with mock.patch.object(territory, "TERRITORY_FROM", "2026-07-28"):
            code = daily.catch_up(self.folder, self.db, log=QUIET, window=1,
                                  until=DAY)
        self.assertEqual(code, 0)
        aggregate, sites = self.stored()
        self.assertEqual(aggregate[0], METHOD_VERSION)
        self.assertAlmostEqual(sites[0], 8.77, places=2)


class Labels(unittest.TestCase):

    def test_the_rule_is_named_after_the_base_method(self):
        self.assertEqual(method_version(True, territory=True),
                         METHOD_VERSION + "+" + TERRITORY_RULE_VERSION)
        self.assertEqual(method_version(True), METHOD_VERSION)

    def test_with_an_edge_rule_the_territory_comes_last(self):
        from gps.area import EDGE_SCALE_VERSION
        self.assertEqual(
            method_version(True, edge_rule=EDGE_SCALE_VERSION, territory=True),
            METHOD_VERSION + "+" + EDGE_SCALE_VERSION + "+"
            + TERRITORY_RULE_VERSION)

    def test_compute_day_without_territory_is_the_method_in_force(self):
        track = read_fixture_track()
        self.assertEqual(daily.compute_day(track).aggregate["method_version"],
                         METHOD_VERSION)


# --- 5. движок и замер GPS-19 -------------------------------------------------------

class SameAsTheMeasurement(unittest.TestCase):
    """Движок оставляет ровно то, что GPS-19 записал в гибрид.

    [REASON]: владелец решал о включении по числам GPS-19. Если движок
    режет иначе, чем мерили, условие п. 5 проверяло не то правило, которое
    включили.
    """

    def test_random_sites_against_random_yards(self):
        from shapely import affinity
        from tools import gps_territory_report as report
        rng = random.Random(20261015)
        seen = {"untouched": 0, "dropped": 0, "subtracted": 0, "parts": 0,
                "floor": 0, "near_half": 0}
        for case in range(1500):
            kind = case % 3
            x0, y0 = rng.uniform(0, 600), rng.uniform(0, 600)
            w, h = rng.uniform(60, 400), rng.uniform(60, 400)
            site = affinity.rotate(box(x0, y0, x0 + w, y0 + h),
                                   rng.uniform(0, 90))
            if kind == 0:     # дворы где угодно
                yards = [affinity.rotate(
                    square(rng.uniform(0, 600), rng.uniform(0, 600),
                           rng.uniform(40, 250)), rng.uniform(0, 90))
                    for _ in range(rng.randint(1, 3))]
            elif kind == 1:   # полоса поперёк участка: два остатка, мелкие
                cx = site.centroid.x + rng.uniform(-w / 2, w / 2)
                yards = [box(cx - rng.uniform(5, 40), y0 - 500,
                             cx + rng.uniform(5, 40), y0 + 900)]
            else:             # доля у порога 0,5
                c = site.centroid
                side = (site.area * rng.uniform(0.48, 0.52)) ** 0.5
                yards = [box(c.x - side / 2, c.y - side / 2,
                             c.x + side / 2, c.y + side / 2)]
            if ha(site) < MIN_WORK_AREA_HA:
                continue
            rule = territory.Territory({i: yard for i, yard in enumerate(yards)})
            share, kept = rule.cut(site)
            tree = shapely.STRtree(yards)
            measured = report.measure_site(site, yards, tree)
            if measured is None or not measured['overlaps']:
                self.assertEqual(kept, [site], case)
                seen["untouched"] += 1
                continue
            measured_share = round(measured['on_ha'] / measured['geom_ha'],
                                   report.SHARE_DIGITS)
            self.assertEqual(share, measured_share, case)
            hybrid = 0.0 if measured_share >= report.DROP_SHARE \
                else measured['kept_a']
            self.assertAlmostEqual(sum(ha(part) for part in kept), hybrid,
                                   places=6, msg=case)
            if abs(measured_share - 0.5) <= 0.01:
                seen["near_half"] += 1
            if measured_share >= report.DROP_SHARE:
                seen["dropped"] += 1
                continue
            seen["subtracted"] += 1
            pieces = report.polygon_parts(site.difference(
                shapely.union_all(yards)))
            seen["parts"] += len(kept) > 1
            seen["floor"] += any(ha(piece) < MIN_WORK_AREA_HA for piece in pieces)
        # [REASON]: проверка, которая не попала ни в один трудный случай,
        # проверкой не является -- каждый вид должен встретиться.
        for name, count in seen.items():
            self.assertGreaterEqual(count, 20, (name, seen))


if __name__ == "__main__":
    unittest.main()
