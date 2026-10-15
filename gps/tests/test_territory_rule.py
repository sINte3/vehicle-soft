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

import glob
import io
import json
import os
import random
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

    def load(self, ids):
        return territory.load_territory(self.con, ids, log=self.lines.append)

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


# --- 3. список зон ----------------------------------------------------------------

class ZoneList(unittest.TestCase):

    def write(self, text):
        handle = tempfile.NamedTemporaryFile("w", suffix=".csv", delete=False,
                                             encoding="utf-8")
        handle.write(text)
        handle.close()
        self.addCleanup(os.unlink, handle.name)
        return handle.name

    def test_a_good_list_reads(self):
        path = self.write("zona_id,gruppa_id,tip\n1,16,azs\n351,3,azs\n")
        self.assertEqual(territory.read_zone_ids(path), [1, 351])

    def test_a_repeated_zone_is_refused(self):
        path = self.write("zona_id,gruppa_id,tip\n1,16,azs\n1,3,azs\n")
        with self.assertRaises(territory.BadZoneList):
            territory.read_zone_ids(path)

    def test_a_non_number_is_refused(self):
        path = self.write("zona_id,gruppa_id,tip\nx1,16,azs\n")
        with self.assertRaises(territory.BadZoneList):
            territory.read_zone_ids(path)

    def test_an_empty_list_is_refused(self):
        with self.assertRaises(territory.BadZoneList):
            territory.read_zone_ids(self.write("zona_id,gruppa_id,tip\n"))

    def test_a_list_without_the_column_is_refused(self):
        with self.assertRaises(territory.BadZoneList):
            territory.read_zone_ids(self.write("id,group\n1,16\n"))

    def test_a_missing_file_is_refused(self):
        with self.assertRaises(territory.BadZoneList):
            territory.read_zone_ids("/nonexistent/territory_zones.csv")

    def test_the_engine_list_is_the_owner_s_latest_list(self):
        # [REASON]: список движка -- копия последнего списка владельца в
        # docs/. Разойдись они, правило снимало бы гектары по одному списку,
        # а замер GPS-19 и документы говорили бы о другом.
        latest = sorted(glob.glob(os.path.join(
            REPO_ROOT, "docs", "gps_territory_zones_*.csv")))[-1]
        with open(territory.ZONES_PATH, encoding="utf-8") as engine, \
                open(latest, encoding="utf-8") as owner:
            self.assertEqual(engine.read(), owner.read(),
                             "gps/data/territory_zones.csv != %s" % latest)

    def test_the_engine_list_reads(self):
        ids = territory.read_zone_ids()
        self.assertGreater(len(ids), 0)


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
        with open(self.zones, "w", encoding="utf-8") as handle:
            handle.write("zona_id,gruppa_id,tip\n3208,16,ptm\n")
        self.patches = [mock.patch.object(territory, "ZONES_PATH", self.zones)]
        for patch in self.patches:
            patch.start()
            self.addCleanup(patch.stop)

    def tearDown(self):
        gps_mig.DB_PATH, migration_utils.DB_PATH = self._saved

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
                mock.patch.object(territory, "read_zone_ids",
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

    def test_part_of_the_site_on_a_yard_is_subtracted(self):
        # двор -- западная треть рамки участка: доля меньше половины
        result = daily.compute_day(self.track)
        site = shapely.geometry.shape(json.loads(result.sites[0]["polygon_geojson"]))
        minx, miny, maxx, maxy = site.bounds
        yard = shapely.geometry.box(minx - 0.001, miny - 0.001,
                                    minx + (maxx - minx) / 3, maxy + 0.001)
        self.mirror(json.dumps(shapely.geometry.mapping(yard)))
        with mock.patch.object(territory, "TERRITORY_FROM", DAY):
            self.run_day()
        aggregate, sites = self.stored()
        self.assertTrue(aggregate[0].endswith("+" + TERRITORY_RULE_VERSION))
        self.assertGreater(sum(sites), 0.3)
        self.assertLess(sum(sites), 8.77 - 0.3)

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
            handle.write("zona_id,gruppa_id,tip\n3208,16,ptm\n3208,16,ptm\n")
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
                    mock.patch.object(territory, "read_zone_ids",
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
        from tools import gps_territory_report as report
        rng = random.Random(20261015)
        for case in range(300):
            yards = [square(rng.uniform(0, 600), rng.uniform(0, 600),
                            rng.uniform(40, 250)) for _ in range(rng.randint(1, 3))]
            x0, y0 = rng.uniform(0, 600), rng.uniform(0, 600)
            site = box(x0, y0, x0 + rng.uniform(60, 400), y0 + rng.uniform(60, 400))
            if ha(site) < MIN_WORK_AREA_HA:
                continue
            rule = territory.Territory({i: yard for i, yard in enumerate(yards)})
            share, kept = rule.cut(site)
            tree = shapely.STRtree(yards)
            measured = report.measure_site(site, yards, tree)
            if measured is None or not measured['overlaps']:
                self.assertEqual(kept, [site], case)
                continue
            measured_share = round(measured['on_ha'] / measured['geom_ha'],
                                   report.SHARE_DIGITS)
            self.assertEqual(share, measured_share, case)
            hybrid = 0.0 if measured_share >= report.DROP_SHARE \
                else measured['kept_a']
            self.assertAlmostEqual(sum(ha(part) for part in kept), hybrid,
                                   places=6, msg=case)


if __name__ == "__main__":
    unittest.main()
