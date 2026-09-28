# -*- coding: utf-8 -*-
"""GPS-2: the four acceptance criteria of the daily computation.

Criteria (docs/GPS_PLAN_FAKT_VISION_ROADMAP.md section 5.1):
  1. the recorded fixture -- zone 3208, unit 3464, 27.07 -- comes out at the
     number gps/tests already pins;
  2. recomputing a day replaces its rows instead of adding more, and the
     operator's answer survives;
  3. a machine recording every 301 s gets an aggregate with a reason and zero
     polygons;
  4. a day without movement gets an aggregate too -- "we looked and there is
     no work" and "we did not look" are different facts.

ON CRITERION 1 AND THE NUMBER 8.51
That figure was measured by worked_area(), which keeps only the points INSIDE
the geozone and clips the result to it. The method adopted on 12.08 (roadmap
2.4/2.6) no longer does either: work_sites() finds the sites in the track and a
geozone only NAMES one. Clipping is what lost 31.77 ha of 159 on the spraying
set and produced two works of exactly zero while the machine stood in the field
all day, so GPS-2 must not clip -- and on this fixture it therefore publishes
8.77 ha, not 8.51.

Both numbers are pinned here, side by side, and the clipped control is still
computed from the same fixture. The 0.27 ha between them is the method change
of 12.08, not a regression -- and if either number ever moves, this test says
which one.

Run (needs the geo venv, see gps/README.md):
  python -m unittest discover -s gps/tests -t .
"""

import csv
import io
import json
import os
import sqlite3
import sys
import tempfile
import unittest
from datetime import datetime, timedelta, timezone

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(
    os.path.abspath(__file__))))
if REPO_ROOT not in sys.path:
    sys.path.insert(0, REPO_ROOT)

import migration_utils                                             # noqa: E402
import migrate_gps_daily_001 as gps_mig                            # noqa: E402
from gps import daily                                              # noqa: E402
from gps.area import polygon_from_wialon, worked_area              # noqa: E402
from gps_collector import storage                                  # noqa: E402

FIXTURES = os.path.join(os.path.dirname(os.path.abspath(__file__)), "fixtures")
TZ = timezone(timedelta(hours=5))

FIELD_CONTOURS_DDL = (
    'CREATE TABLE field_contours (id INTEGER PRIMARY KEY, source TEXT, '
    'external_id TEXT, name TEXT, geometry_geojson TEXT, area_ha REAL, '
    'is_active BOOLEAN DEFAULT 1)')


def read_fixture_track():
    """The recorded day of unit 3464: [(t, lon, lat, speed, sats)]."""
    points = []
    with open(os.path.join(FIXTURES, "track_3464_20260727.csv"),
              encoding="utf-8-sig") as fh:
        for row in csv.DictReader(fh, delimiter=";"):
            hours, minutes, seconds = row["time"].split(":")
            midnight = datetime.strptime(row["date"], "%Y-%m-%d") \
                .replace(tzinfo=TZ).timestamp()
            points.append((int(midnight) + int(hours) * 3600
                           + int(minutes) * 60 + int(seconds),
                           float(row["lon"]), float(row["lat"]),
                           float(row["speed"]), int(row["sats"] or -1)))
    return points


def read_fixture_zone():
    with open(os.path.join(FIXTURES, "zone_3208.json"), encoding="utf-8") as fh:
        return json.load(fh)


def zone_geojson(zone):
    ring = [[point["x"], point["y"]] for point in zone["points"]]
    if ring[0] != ring[-1]:
        ring.append(ring[0])
    return json.dumps({"type": "Polygon", "coordinates": [ring]})


def synthetic_day(day="2026-08-05", start_hour=8, count=200, step_s=30,
                  speed=8.0, sats=14, lon0=64.50, lat0=39.99):
    """A straight run of points, spaced as asked. Used for the refusal paths."""
    midnight = int(datetime.strptime(day, "%Y-%m-%d")
                   .replace(tzinfo=TZ).timestamp())
    return [(midnight + start_hour * 3600 + i * step_s,
             lon0 + i * 2e-5, lat0 + i * 2e-5, speed, sats)
            for i in range(count)]


class FixtureDay(unittest.TestCase):
    """Criterion 1."""

    @classmethod
    def setUpClass(cls):
        cls.track = read_fixture_track()
        cls.zone = read_fixture_zone()
        cls.contour = polygon_from_wialon(cls.zone["points"])
        cls.result = daily.compute_day(cls.track, contours={3208: cls.contour})

    def test_the_day_is_published_and_finds_one_site(self):
        self.assertIsNone(self.result.reason)
        self.assertEqual(len(self.result.sites), 1)

    def test_the_site_matches_the_recorded_measurements(self):
        site = self.result.sites[0]
        # Literals, not expressions over the module's own constants: an
        # expectation written through the constant it tests moves with a
        # mutation and passes on broken code (gps/README.md records two such).
        self.assertAlmostEqual(site["area_ha"], 8.772, delta=0.01)
        self.assertAlmostEqual(site["minutes"], 317.4, delta=0.5)
        self.assertEqual(site["points_inside"], 892)
        self.assertAlmostEqual(site["alpha_used_m"], 10.0, delta=0.01)
        self.assertAlmostEqual(site["pass_spacing_m"], 5.45, delta=0.02)

    def test_the_same_numbers_come_out_of_the_labelling_tool(self):
        """Differential control against a second, independent implementation.

        tools/gps_label_sites.py computes area, points inside and minutes on
        its own path and built the labelled corpus with them. Reproduced on
        2026-08-19:

          python tools/gps_label_sites.py \\
              --csv gps/tests/fixtures/track_3464_20260727.csv --min-ha 0.3

        gave  area_ha 8.772  points_inside 892  minutes 317.4  spacing 5.45.

        Those are the literals above. If the two ever disagree, the frozen
        work/transit rule would be judged on a feature it was never fitted on
        -- the failure the first round of labelling actually made.
        """
        site = self.result.sites[0]
        self.assertAlmostEqual(site["area_ha"], 8.772, delta=0.001)
        self.assertAlmostEqual(site["minutes"], 317.4, delta=0.05)
        self.assertEqual(site["points_inside"], 892)

    def test_the_geozone_names_the_site_and_does_not_clip_it(self):
        self.assertEqual(self.result.sites[0]["contour_id"], 3208)
        # 8.51 ha is the pre-12.08 figure: points filtered to the geozone and
        # the result clipped to it. Still reproducible, still not what GPS-2
        # publishes.
        clipped = worked_area(self.track, self.contour, contour_id=3208)
        self.assertAlmostEqual(clipped.area_ha, 8.51, delta=0.01)
        self.assertGreater(self.result.sites[0]["area_ha"], clipped.area_ha)

    def test_the_days_median_interval_sits_exactly_on_the_limit(self):
        # [REASON]: the fixture records every 30 s and MAX_INTERVAL_S is 30, so
        # the comparison being STRICTLY greater is load-bearing: turn it into
        # >= and this real, verified day stops being published. 24 percent of
        # the field fleet writes at exactly this interval (roadmap 2.10).
        self.assertEqual(self.result.aggregate["interval_median_s"], 30.0)
        self.assertIsNone(self.result.aggregate["reason"])

    def test_the_aggregate_carries_the_measured_day(self):
        aggregate = self.result.aggregate
        self.assertEqual(aggregate["points_total"], 1599)
        self.assertEqual(aggregate["points_work"], 990)
        self.assertAlmostEqual(aggregate["track_km"], 32.1, delta=0.2)
        self.assertEqual(aggregate["sats_median"], 14.0)
        self.assertEqual(aggregate["motion_gaps"], 0)
        self.assertEqual(aggregate["gps_jumps"], 0)
        self.assertEqual(aggregate["method_version"], "adaptive-alpha-2026-08-12")

    def test_the_polygon_is_stored_in_degrees_and_survives_the_round_trip(self):
        site = self.result.sites[0]
        geometry = json.loads(site["polygon_geojson"])
        self.assertEqual(geometry["type"], "Polygon")
        lon, lat = geometry["coordinates"][0][0]
        self.assertAlmostEqual(lon, 64.55, delta=0.05)
        self.assertAlmostEqual(lat, 40.00, delta=0.05)
        back = daily._utm_polygon_from_geojson(site["polygon_geojson"])
        self.assertAlmostEqual(back.area / 10000.0, site["area_ha"], delta=0.01)


class RefusalPaths(unittest.TestCase):
    """Criteria 3 and 4."""

    def test_a_machine_recording_every_301_s_publishes_nothing(self):
        # Doosan 80 553 HA, a real case from the fleet sweep of 18.08.
        result = daily.compute_day(synthetic_day(step_s=301))
        self.assertEqual(result.reason, "redkaya_zapis")
        self.assertEqual(result.sites, [])
        self.assertEqual(result.aggregate["interval_median_s"], 301.0)
        # the measurements are still recorded -- the refusal is about the area
        self.assertEqual(result.aggregate["points_total"], 200)
        self.assertGreater(result.aggregate["points_work"], 0)

    def test_a_day_without_movement_still_gets_an_aggregate(self):
        result = daily.compute_day(synthetic_day(speed=0.0))
        self.assertEqual(result.reason, "net_dvizheniya")
        self.assertEqual(result.sites, [])
        self.assertEqual(result.aggregate["points_total"], 200)
        self.assertEqual(result.aggregate["points_work"], 0)

    def test_a_day_with_no_points_at_all_gets_a_row_too(self):
        result = daily.compute_day([])
        self.assertEqual(result.reason, "net_tochek")
        self.assertEqual(result.aggregate["points_total"], 0)

    def test_a_day_of_one_single_message_is_a_refusal_and_not_a_crash(self):
        # The crash of --catch-up on 27.09.2026: `None > 30.0`. One message
        # yields no interval at all, and an object whose history starts a day
        # back has such days. The refusal is redkaya_zapis -- one message a day
        # is the rarest recording there is -- and the measurements still get a
        # row, because "we looked and there was one message" is a fact.
        result = daily.compute_day(synthetic_day(count=1))
        self.assertEqual(result.reason, "redkaya_zapis")
        self.assertEqual(result.sites, [])
        self.assertIsNone(result.aggregate["interval_median_s"])
        self.assertEqual(result.aggregate["points_total"], 1)
        self.assertEqual(result.aggregate["points_work"], 1)

    def test_two_messages_are_enough_and_are_not_refused_with_it(self):
        # The control that keeps `is None` from swallowing the working case:
        # two messages DO have an interval, and this day is not a refusal.
        result = daily.compute_day(synthetic_day(count=2))
        self.assertEqual(result.aggregate["interval_median_s"], 30.0)
        self.assertIsNone(result.reason)

    def test_one_standing_message_is_no_motion_not_rare(self):
        # The order of the two guards, pinned: a single message at 0 km/h never
        # reaches the interval at all, and "did not move" is the stronger fact.
        result = daily.compute_day(synthetic_day(count=1, speed=0.0))
        self.assertEqual(result.reason, "net_dvizheniya")

    def test_a_day_that_moved_but_worked_nowhere_is_published_as_empty(self):
        # Driving down a road at 8 km/h in a straight line: points in the work
        # window, but no patch reaches the area floor. That is "we looked and
        # there is no work", and it is NOT a refusal.
        result = daily.compute_day(synthetic_day(count=120, step_s=30))
        self.assertIsNone(result.reason)
        self.assertEqual(result.sites, [])


class FrozenRule(unittest.TestCase):
    def test_the_thresholds_are_the_referee_s_own(self):
        # tools/gps_label_evaluate.py is the referee of the pre-registered rule
        # and its thresholds are frozen. Pinning the two together means a
        # change in either is caught here rather than in a report months later.
        import importlib.util
        path = os.path.join(REPO_ROOT, "tools", "gps_label_evaluate.py")
        spec = importlib.util.spec_from_file_location("referee", path)
        referee = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(referee)
        self.assertEqual(daily.RULE_AREA_HA, referee.RULE_AREA_HA)
        self.assertEqual(daily.RULE_MINUTES, referee.RULE_MINUTES)
        self.assertEqual((daily.WORK, daily.PASSAGE),
                         (referee.WORK, referee.PASSAGE))

    def test_either_half_of_the_or_is_enough(self):
        self.assertEqual(daily.suggested_label(1.3, 0.0), "работа")
        self.assertEqual(daily.suggested_label(0.4, 25.0), "работа")
        self.assertEqual(daily.suggested_label(1.29, 24.9), "проезд")
        # the form is OR, not AND: an AND rule would call the first two transit
        self.assertNotEqual(daily.suggested_label(1.3, 0.0), "проезд")


class QualityFlag(unittest.TestCase):
    def test_a_clean_day_has_no_flag(self):
        result = daily.compute_day(read_fixture_track())
        self.assertIsNone(result.sites[0]["quality_flag"])

    def test_silence_on_the_move_is_reported_with_its_count(self):
        # Cut a quarter of an hour out of the day starting at a point where the
        # machine WAS moving. That, and only that, is a gap: silence beginning
        # at a standstill is a parked tractor, and counting it cost this track
        # three rewrites of the metric (gps/README.md).
        track = read_fixture_track()
        moving = next(row for row in track[len(track) // 3:] if row[3] >= 1.0)
        cut = [row for row in track
               if not (moving[0] < row[0] <= moving[0] + 900)]
        result = daily.compute_day(cut)
        self.assertTrue(result.sites)
        self.assertEqual(result.sites[0]["quality_flag"], "razryvy=1")


class Recomputation(unittest.TestCase):
    """Criterion 2, in a real database created by the real migration."""

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
            (3464, t, lon, lat, speed, 90, sats)
            for t, lon, lat, speed, sats in self.track])

    def tearDown(self):
        gps_mig.DB_PATH, migration_utils.DB_PATH = self._saved

    def rows(self, table):
        con = sqlite3.connect(self.db)
        try:
            con.row_factory = sqlite3.Row
            return [dict(r) for r in con.execute("SELECT * FROM %s" % table)]
        finally:
            con.close()

    def answer(self, label="проезд"):
        con = sqlite3.connect(self.db)
        try:
            con.execute("UPDATE gps_work_polygons SET operator_label = ?, "
                        "decided_at = '2026-08-19 10:00'", (label,))
            con.commit()
        finally:
            con.close()

    def test_the_stored_day_matches_the_computed_one(self):
        result = daily.run_day("2026-07-27", 3464, folder=self.folder,
                               db_path=self.db, log=lambda *a: None)
        self.assertIsNone(result.reason)
        polygons = self.rows("gps_work_polygons")
        aggregates = self.rows("gps_daily_aggregates")
        self.assertEqual(len(polygons), 1)
        self.assertEqual(len(aggregates), 1)
        self.assertAlmostEqual(polygons[0]["area_ha"], 8.772, delta=0.01)
        self.assertEqual(polygons[0]["suggested_label"], "работа")
        self.assertIsNone(polygons[0]["operator_label"])
        # 1598, not the fixture's 1599 rows: the day carries one message
        # repeated at the same second with the same position, and the store's
        # (unit_id, t) key -- the key that makes a re-collected interval free --
        # keeps one of the pair. Measured consequence: none. Area, minutes,
        # track length and median interval are identical with and without it,
        # which is why the assertions below are the same numbers as the
        # in-memory test above.
        self.assertEqual(aggregates[0]["points_total"], 1598)
        self.assertIsNone(aggregates[0]["reason"])
        self.assertAlmostEqual(aggregates[0]["track_km"], 32.1, delta=0.2)
        self.assertEqual(aggregates[0]["interval_median_s"], 30.0)
        self.assertAlmostEqual(polygons[0]["minutes"], 317.4, delta=0.5)

    def test_recompute_replaces_rows_and_keeps_the_operator_answer(self):
        daily.run_day("2026-07-27", 3464, folder=self.folder, db_path=self.db,
                      log=lambda *a: None)
        self.answer("проезд")
        daily.run_day("2026-07-27", 3464, folder=self.folder, db_path=self.db,
                      log=lambda *a: None)
        polygons = self.rows("gps_work_polygons")
        self.assertEqual(len(polygons), 1, "a recomputation added a second row")
        self.assertEqual(len(self.rows("gps_daily_aggregates")), 1)
        self.assertEqual(polygons[0]["operator_label"], "проезд")
        self.assertEqual(polygons[0]["decided_at"], "2026-08-19 10:00")
        # and the machine's own suggestion is recomputed, never merged with it
        self.assertEqual(polygons[0]["suggested_label"], "работа")

    def test_ten_recomputations_are_still_one_row(self):
        for _ in range(10):
            daily.run_day("2026-07-27", 3464, folder=self.folder,
                          db_path=self.db, log=lambda *a: None)
        self.assertEqual(len(self.rows("gps_work_polygons")), 1)
        self.assertEqual(len(self.rows("gps_daily_aggregates")), 1)

    def test_an_answer_whose_ground_is_gone_is_counted_not_lost_quietly(self):
        # The negative control of the carry-over: without it, "the label
        # survived" would also be true of code that copied every answer onto
        # whatever row happened to be first.
        daily.run_day("2026-07-27", 3464, folder=self.folder, db_path=self.db,
                      log=lambda *a: None)
        self.answer("работа")
        elsewhere = daily.compute_day(synthetic_day(day="2026-07-27"))
        con = sqlite3.connect(self.db)
        try:
            carried, dropped = daily.write_day(con, "2026-07-27", 3464,
                                               elsewhere)
        finally:
            con.close()
        self.assertEqual((carried, dropped), (0, 1))
        self.assertEqual(self.rows("gps_work_polygons"), [])

    def test_a_contour_in_the_directory_names_the_site(self):
        con = sqlite3.connect(self.db)
        try:
            con.execute("INSERT INTO field_contours (id, source, external_id, "
                        "name, geometry_geojson, is_active) VALUES "
                        "(77, 'wialon', '3208', '1508 Nurhon', ?, 1)",
                        (zone_geojson(read_fixture_zone()),))
            con.commit()
        finally:
            con.close()
        daily.run_day("2026-07-27", 3464, folder=self.folder, db_path=self.db,
                      log=lambda *a: None)
        self.assertEqual(self.rows("gps_work_polygons")[0]["contour_id"], 77)

    def broken_contour(self, contour_id=78):
        """Контур-«бабочка» в справочнике: ровно то, что рисуют мышкой в Wialon.

        Два узла кольца меняются местами -- получается самопересечение поверх
        той же земли, что и рабочий контур фикстуры.
        """
        ring = [[point["x"], point["y"]] for point in read_fixture_zone()["points"]]
        ring[1], ring[len(ring) // 2] = ring[len(ring) // 2], ring[1]
        if ring[0] != ring[-1]:
            ring.append(ring[0])
        con = sqlite3.connect(self.db)
        try:
            con.execute("INSERT INTO field_contours (id, source, external_id, "
                        "name, geometry_geojson, is_active) VALUES "
                        "(?, 'wialon', '3208', 'babochka', ?, 1)",
                        (contour_id,
                         json.dumps({"type": "Polygon", "coordinates": [ring]})))
            con.commit()
        finally:
            con.close()

    def test_a_self_intersecting_contour_names_the_site_instead_of_killing_it(self):
        # Живой отказ 28.09.2026: `piece.intersection(geom)` на таком контуре
        # поднимает TopologyException, и сутки не считались ни у этого объекта,
        # ни у всех после него. Проверено, что именно эта геометрия валит расчёт
        # без починки (мутация repair_polygon -> identity).
        self.broken_contour(78)
        result = daily.run_day("2026-07-27", 3464, folder=self.folder,
                               db_path=self.db, log=lambda *a: None)
        self.assertIsNone(result.reason)
        self.assertGreater(len(result.sites), 0)
        # и участок всё-таки назван: починка не выбрасывает контур, а чинит
        self.assertEqual(self.rows("gps_work_polygons")[0]["contour_id"], 78)

    def test_a_contour_that_cannot_be_repaired_is_dropped_and_counted(self):
        # Зона, схлопнутая в одну точку: площади нет, чинить не во что.
        # Проекция такое кольцо не «расправит» -- точки совпадают побайтно, в
        # отличие от почти коллинеарных, которые в UTM дают настоящие 183 кв. м.
        line = [[64.50, 39.99]] * 4
        con = sqlite3.connect(self.db)
        try:
            con.execute("INSERT INTO field_contours (id, source, external_id, "
                        "name, geometry_geojson, is_active) VALUES "
                        "(79, 'wialon', '1', 'otrezok', ?, 1)",
                        (json.dumps({"type": "Polygon", "coordinates": [line]}),))
            con.commit()
            said = []
            contours = daily.load_contours(con, log=said.append)
        finally:
            con.close()
        self.assertNotIn(79, contours)
        self.assertTrue(any("1 dropped as unusable" in line for line in said),
                        said)

    def test_a_good_contour_is_not_touched(self):
        # Отрицательный контроль починки: валидный контур возвращается ТЕМ ЖЕ
        # объектом, а не пересобранным. Иначе «починка» тихо меняла бы
        # измеренную землю на всём справочнике.
        con = sqlite3.connect(self.db)
        try:
            con.execute("INSERT INTO field_contours (id, source, external_id, "
                        "name, geometry_geojson, is_active) VALUES "
                        "(77, 'wialon', '3208', '1508 Nurhon', ?, 1)",
                        (zone_geojson(read_fixture_zone()),))
            con.commit()
            said = []
            contours = daily.load_contours(con, log=said.append)
        finally:
            con.close()
        self.assertTrue(contours[77].is_valid)
        self.assertEqual(said, [])

    def test_a_day_with_no_points_is_stored_as_a_row_with_a_reason(self):
        daily.run_day("2026-07-20", 3464, folder=self.folder, db_path=self.db,
                      log=lambda *a: None)
        aggregates = self.rows("gps_daily_aggregates")
        self.assertEqual(len(aggregates), 1)
        self.assertEqual(aggregates[0]["reason"], "net_tochek")
        self.assertEqual(self.rows("gps_work_polygons"), [])


class CollectionCompleteness(unittest.TestCase):
    """GPS-11: a day the collector has not finished is not published.

    [REASON]: the first live run of 07.09.2026 truncated 104 objects at 50 000
    messages over an 18-day backlog, and the day was computed on partial
    points and shown as a fact. The collector's watermark is the proof of how
    far the fetch got; the computation must read it.
    """

    DAY = "2026-07-27"
    UNIT = 3464

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
            (self.UNIT, t, lon, lat, speed, 90, sats)
            for t, lon, lat, speed, sats in self.track])
        self.start, self.finish = daily.day_bounds(self.DAY)

    def tearDown(self):
        gps_mig.DB_PATH, migration_utils.DB_PATH = self._saved

    def rows(self, table):
        con = sqlite3.connect(self.db)
        try:
            con.row_factory = sqlite3.Row
            return [dict(r) for r in con.execute(
                "SELECT * FROM %s ORDER BY id" % table)]
        finally:
            con.close()

    def answer(self, label="проезд"):
        con = sqlite3.connect(self.db)
        try:
            con.execute("UPDATE gps_work_polygons SET operator_label = ?, "
                        "decided_at = '2026-08-19 10:00'", (label,))
            con.commit()
        finally:
            con.close()

    def run_main(self, *argv):
        out, saved = io.StringIO(), sys.stdout
        sys.stdout = out
        try:
            code = daily.main(["--dir", self.folder, "--db", self.db] + list(argv))
        finally:
            sys.stdout = saved
        return code, out.getvalue()

    def test_a_day_the_collector_has_not_finished_is_not_published(self):
        storage.set_watermark(self.folder, self.UNIT, self.finish - 3600)
        result = daily.run_day(self.DAY, self.UNIT, folder=self.folder,
                               db_path=self.db, contours={})
        self.assertEqual(result.reason, daily.REASON_INCOMPLETE)
        self.assertEqual(result.sites, [])
        aggregates = self.rows("gps_daily_aggregates")
        self.assertEqual(len(aggregates), 1)
        self.assertEqual(aggregates[0]["reason"], "sbor_nepolnyy")
        # 1599 rows in the fixture, 1598 distinct seconds: the key swallows the
        # duplicate, and points_total counts what is stored
        self.assertEqual(aggregates[0]["points_total"],
                         len(storage.read_day(self.folder, self.UNIT, self.DAY)))
        self.assertEqual(self.rows("gps_work_polygons"), [])

    def test_a_watermark_past_the_day_publishes_it(self):
        storage.set_watermark(self.folder, self.UNIT, self.finish)
        result = daily.run_day(self.DAY, self.UNIT, folder=self.folder,
                               db_path=self.db, contours={})
        self.assertIsNone(result.reason)
        self.assertEqual(len(self.rows("gps_work_polygons")), len(result.sites))
        self.assertGreater(len(result.sites), 0)

    def test_no_watermark_at_all_means_the_day_is_computed(self):
        # Points that did not come through the collector -- a fixture, a hand
        # import -- have nothing to be compared against.
        self.assertFalse(os.path.exists(storage.state_path(self.folder)))
        result = daily.run_day(self.DAY, self.UNIT, folder=self.folder,
                               db_path=self.db, contours={})
        self.assertIsNone(result.reason)
        # and the reader did not create the collector's state file
        self.assertFalse(os.path.exists(storage.state_path(self.folder)))

    def test_an_earlier_answer_survives_the_incomplete_mark(self):
        storage.set_watermark(self.folder, self.UNIT, self.finish)
        daily.run_day(self.DAY, self.UNIT, folder=self.folder,
                      db_path=self.db, contours={})
        self.answer("проезд")
        before = self.rows("gps_work_polygons")
        # the guard arrives after the day was computed on partial points
        storage.set_watermark(self.folder, self.UNIT, self.finish - 3600)
        result = daily.run_day(self.DAY, self.UNIT, folder=self.folder,
                               db_path=self.db, contours={})
        self.assertEqual(result.reason, daily.REASON_INCOMPLETE)
        self.assertEqual(self.rows("gps_work_polygons"), before)
        self.assertEqual(self.rows("gps_daily_aggregates")[0]["reason"],
                         "sbor_nepolnyy")

    def test_catch_up_recomputes_once_the_watermark_has_passed(self):
        storage.set_watermark(self.folder, self.UNIT, self.finish)
        daily.run_day(self.DAY, self.UNIT, folder=self.folder,
                      db_path=self.db, contours={})
        self.answer("проезд")
        storage.set_watermark(self.folder, self.UNIT, self.finish - 3600)
        daily.run_day(self.DAY, self.UNIT, folder=self.folder,
                      db_path=self.db, contours={})
        storage.set_watermark(self.folder, self.UNIT, self.finish + 1800)

        code, log = self.run_main("--catch-up")
        self.assertEqual(code, 0, log)
        self.assertIn("catch-up: recomputed 1 | still waiting for the "
                      "collector: 0", log)
        aggregates = self.rows("gps_daily_aggregates")
        self.assertEqual(len(aggregates), 1)
        self.assertIsNone(aggregates[0]["reason"])
        polygons = self.rows("gps_work_polygons")
        self.assertGreater(len(polygons), 0)
        # the answer given on the partial polygon came along by overlap
        self.assertEqual([p["operator_label"] for p in polygons], ["проезд"])

    def test_catch_up_leaves_a_still_incomplete_day_alone(self):
        storage.set_watermark(self.folder, self.UNIT, self.finish - 3600)
        daily.run_day(self.DAY, self.UNIT, folder=self.folder,
                      db_path=self.db, contours={})
        stamp = self.rows("gps_daily_aggregates")[0]["computed_at"]
        code, log = self.run_main("--catch-up")
        self.assertEqual(code, 0, log)
        self.assertIn("recomputed 0 | still waiting for the collector: 1", log)
        row = self.rows("gps_daily_aggregates")[0]
        self.assertEqual(row["reason"], "sbor_nepolnyy")
        self.assertEqual(row["computed_at"], stamp)

    def test_catch_up_with_nothing_pending_says_so(self):
        code, log = self.run_main("--catch-up")
        self.assertEqual(code, 0, log)
        self.assertIn("nothing is waiting", log)

    def test_catch_up_refuses_a_date_next_to_it(self):
        err, saved = io.StringIO(), sys.stderr
        sys.stderr = err
        try:
            code, _ = self.run_main("--catch-up", "--date", self.DAY)
        finally:
            sys.stderr = saved
        self.assertEqual(code, 2)
        self.assertIn("--catch-up takes no --date", err.getvalue())

    def test_the_console_names_the_reason_and_what_to_do(self):
        storage.set_watermark(self.folder, self.UNIT, self.finish - 3600)
        code, log = self.run_main("--date", self.DAY)
        self.assertEqual(code, 0, log)
        self.assertIn("sbor_nepolnyy", log)
        self.assertIn("waiting for the collector: 1", log)
        self.assertIn("--catch-up", log)
        self.assertTrue(log.isascii(), log)


class CatchUpGaps(CollectionCompleteness):
    """GPS-11b: a day with points but no row is picked up by --catch-up.

    [REASON]: the second live run of 08.09.2026. An object whose tail was not
    fetched yet had no points for its later days; the computation never
    listed it, no row was written, and --catch-up had nothing to find. Once
    the tail arrived, nobody came back for those days.
    """

    def test_a_day_with_points_and_no_row_is_computed(self):
        storage.set_watermark(self.folder, self.UNIT, self.finish)
        self.assertEqual(self.rows("gps_daily_aggregates"), [])
        code, log = self.run_main("--catch-up", "--until", self.DAY,
                                  "--window-days", "3")
        self.assertEqual(code, 0, log)
        self.assertIn("days without a row: 1 -- computed 1, marked "
                      "sbor_nepolnyy 0", log)
        rows = self.rows("gps_daily_aggregates")
        self.assertEqual([(r["work_date"], r["wialon_id"], r["reason"])
                          for r in rows], [(self.DAY, self.UNIT, None)])
        self.assertGreater(len(self.rows("gps_work_polygons")), 0)

    def test_a_gap_day_the_collector_has_not_finished_is_marked_not_computed(self):
        storage.set_watermark(self.folder, self.UNIT, self.finish - 3600)
        code, log = self.run_main("--catch-up", "--until", self.DAY,
                                  "--window-days", "3")
        self.assertEqual(code, 0, log)
        self.assertIn("computed 0, marked sbor_nepolnyy 1", log)
        rows = self.rows("gps_daily_aggregates")
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["reason"], "sbor_nepolnyy")
        self.assertEqual(self.rows("gps_work_polygons"), [])
        # and the next catch-up, once the watermark has passed, publishes it
        storage.set_watermark(self.folder, self.UNIT, self.finish)
        code, log = self.run_main("--catch-up", "--until", self.DAY,
                                  "--window-days", "3")
        self.assertEqual(code, 0, log)
        self.assertIsNone(self.rows("gps_daily_aggregates")[0]["reason"])

    def test_a_gap_day_of_one_single_message_does_not_kill_the_catch_up(self):
        # The live crash of 27.09.2026: --catch-up walked the gap days, met an
        # object with ONE message in the day and died on `None > 30.0` -- after
        # the first loop had already reported "recomputed 124". Everything the
        # second loop had not reached yet stayed uncomputed, and the run left no
        # trace of why. The day gets a row with a reason; the neighbour on the
        # same day is computed in the same pass.
        lonely = 999001
        midnight, finish = daily.day_bounds(self.DAY)
        storage.write_points(self.folder,
                             [(lonely, midnight + 9 * 3600,
                               64.50, 39.99, 8.0, 90, 14)])
        storage.set_watermark(self.folder, self.UNIT, self.finish)
        storage.set_watermark(self.folder, lonely, self.finish)
        code, log = self.run_main("--catch-up", "--until", self.DAY,
                                  "--window-days", "3")
        self.assertEqual(code, 0, log)
        self.assertIn("days without a row: 2 -- computed 2, marked "
                      "sbor_nepolnyy 0", log)
        rows = {r["wialon_id"]: r for r in self.rows("gps_daily_aggregates")}
        self.assertEqual(rows[lonely]["reason"], "redkaya_zapis")
        self.assertEqual(rows[lonely]["points_total"], 1)
        self.assertIsNone(rows[lonely]["interval_median_s"])
        # and the object next to it in the same run is published as before
        self.assertIsNone(rows[self.UNIT]["reason"])
        self.assertGreater(len(self.rows("gps_work_polygons")), 0)

    def test_a_day_that_already_has_a_row_is_left_alone(self):
        storage.set_watermark(self.folder, self.UNIT, self.finish)
        daily.run_day(self.DAY, self.UNIT, folder=self.folder,
                      db_path=self.db, contours={})
        # a sentinel a recomputation would erase
        con = sqlite3.connect(self.db)
        try:
            con.execute("UPDATE gps_daily_aggregates SET reason = 'redkaya_zapis'")
            con.commit()
        finally:
            con.close()
        code, log = self.run_main("--catch-up", "--until", self.DAY,
                                  "--window-days", "3")
        self.assertEqual(code, 0, log)
        self.assertIn("nothing is waiting", log)
        self.assertEqual(self.rows("gps_daily_aggregates")[0]["reason"],
                         "redkaya_zapis")

    def test_the_window_is_inclusive_and_counted_in_days(self):
        storage.set_watermark(self.folder, self.UNIT, self.finish)
        self.assertEqual(daily.window_days("2026-07-27", 3),
                         ["2026-07-25", "2026-07-26", "2026-07-27"])
        self.assertEqual(daily.window_days("2026-07-27", 1), ["2026-07-27"])
        # a window that ends the day before the points sees nothing
        code, log = self.run_main("--catch-up", "--until", "2026-07-26",
                                  "--window-days", "1")
        self.assertEqual(code, 0, log)
        self.assertIn("nothing is waiting", log)
        self.assertEqual(self.rows("gps_daily_aggregates"), [])
        # the same window one day later picks the day up
        code, log = self.run_main("--catch-up", "--until", "2026-07-27",
                                  "--window-days", "1")
        self.assertEqual(code, 0, log)
        self.assertEqual(len(self.rows("gps_daily_aggregates")), 1)

    def test_bad_window_arguments_are_refused(self):
        err, saved = io.StringIO(), sys.stderr
        sys.stderr = err
        try:
            code, _ = self.run_main("--catch-up", "--window-days", "0")
            self.assertEqual(code, 2)
            code, _ = self.run_main("--catch-up", "--until", "vchera")
            self.assertEqual(code, 2)
        finally:
            sys.stderr = saved
        self.assertIn("--window-days", err.getvalue())
        self.assertIn("--until", err.getvalue())


class ExcludedObjects(CollectionCompleteness):
    """GPS-12: объект, которого на план-факте быть не должно, не считается.

    [REASON]: 27.09.2026 объектов в Wialon стало 621, и владелец попросил убрать
    легковые. Экран «Факт по технике» -- то место, где размечаются 40 участков
    и 15 проездов; лишние сто строк в списке машин делают эту работу дороже
    ровно там, где она и без того ручная.
    """

    MAPPING_DDL = (
        'CREATE TABLE vialon_mappings (id INTEGER PRIMARY KEY AUTOINCREMENT, '
        'vialon_name VARCHAR(300) NOT NULL UNIQUE, wialon_id INTEGER, '
        'equipment_id INTEGER, skip BOOLEAN, created_by INTEGER, '
        'created_at DATETIME, updated_at DATETIME)',
        'CREATE TABLE equipment (id INTEGER PRIMARY KEY AUTOINCREMENT, '
        'name VARCHAR(200) NOT NULL, plate VARCHAR(50), '
        'category VARCHAR(20) NOT NULL, eq_type VARCHAR(100), '
        'organization_id INTEGER NOT NULL, default_price FLOAT, '
        'default_unit VARCHAR(30), is_active BOOLEAN, model_id INTEGER)')

    CAR = 777001

    def setUp(self):
        super().setUp()
        con = sqlite3.connect(self.db)
        try:
            for statement in self.MAPPING_DDL:
                con.execute(statement)
            con.commit()
        finally:
            con.close()
        # Легковая машина рядом с трактором фикстуры, в те же сутки.
        midnight, _ = daily.day_bounds(self.DAY)
        storage.write_points(self.folder, [
            (self.CAR, midnight + 8 * 3600 + i * 30,
             64.40 + i * 2e-5, 39.80 + i * 2e-5, 8.0, 90, 14)
            for i in range(200)])

    def watermarks(self, *units):
        """Отметки, дошедшие до конца суток: без них сутки не публикуются.

        Ставятся в тестах, а НЕ в setUp: унаследованный
        test_no_watermark_at_all_means_the_day_is_computed проверяет, что файла
        состояния нет вовсе, и setUp его бы создал.
        """
        for unit_id in (units or (self.UNIT, self.CAR)):
            storage.set_watermark(self.folder, unit_id, self.finish)

    def mark_as_passenger(self):
        con = sqlite3.connect(self.db)
        try:
            cursor = con.execute(
                'INSERT INTO equipment (name, category, organization_id, '
                'is_active) VALUES (?, ?, 1, 1)', ('Nexia', 'passenger'))
            con.execute(
                'INSERT INTO vialon_mappings (vialon_name, wialon_id, '
                'equipment_id, skip) VALUES (?, ?, ?, 0)',
                ('Nexia 80 123 ABA', self.CAR, cursor.lastrowid))
            con.commit()
        finally:
            con.close()

    def test_without_a_mapping_the_car_is_computed_like_everything_else(self):
        self.watermarks()
        # Отрицательный контроль: до решения владельца объект считается, иначе
        # проверки ниже проходили бы и на неверном коде.
        code, log = self.run_main("--date", self.DAY)
        self.assertEqual(code, 0, log)
        self.assertEqual({r["wialon_id"] for r in self.rows("gps_daily_aggregates")},
                         {self.UNIT, self.CAR})

    def test_a_passenger_car_is_left_out_of_the_day(self):
        self.watermarks()
        self.mark_as_passenger()
        code, log = self.run_main("--date", self.DAY)
        self.assertEqual(code, 0, log)
        self.assertEqual({r["wialon_id"] for r in self.rows("gps_daily_aggregates")},
                         {self.UNIT})
        # и в консоли написано, сколько и почему -- молча считать меньше нельзя
        self.assertIn("left out -- ne_polevaya: 1", log)

    def test_an_explicit_unit_is_computed_even_when_excluded(self):
        self.watermarks()
        # [REASON]: «покажи мне этот объект» -- просьба человека, и отвечать на
        # неё тишиной нельзя: владелец решит, что расчёт сломан.
        self.mark_as_passenger()
        code, log = self.run_main("--date", self.DAY, "--unit", str(self.CAR))
        self.assertEqual(code, 0, log)
        self.assertEqual([r["wialon_id"] for r in self.rows("gps_daily_aggregates")],
                         [self.CAR])

    def test_catch_up_does_not_pick_up_an_excluded_object(self):
        self.watermarks()
        self.mark_as_passenger()
        code, log = self.run_main("--catch-up", "--until", self.DAY,
                                  "--window-days", "3")
        self.assertEqual(code, 0, log)
        self.assertIn("days without a row: 1 -- computed 1", log)
        self.assertEqual({r["wialon_id"] for r in self.rows("gps_daily_aggregates")},
                         {self.UNIT})

    def test_an_earlier_row_of_an_excluded_object_is_left_in_place(self):
        """Ничего не удаляется и задним числом не пересчитывается."""
        self.watermarks()
        self.run_main("--date", self.DAY)
        self.mark_as_passenger()
        con = sqlite3.connect(self.db)
        try:
            con.execute("UPDATE gps_daily_aggregates SET reason = ? "
                        "WHERE wialon_id = ?",
                        (daily.REASON_INCOMPLETE, self.CAR))
            con.commit()
        finally:
            con.close()
        code, log = self.run_main("--catch-up", "--until", self.DAY,
                                  "--window-days", "3")
        self.assertEqual(code, 0, log)
        self.assertIn("nothing is waiting", log)
        rows = {r["wialon_id"]: r for r in self.rows("gps_daily_aggregates")}
        self.assertIn(self.CAR, rows)
        self.assertEqual(rows[self.CAR]["reason"], daily.REASON_INCOMPLETE)

    def test_a_day_of_only_excluded_objects_says_so_and_writes_nothing(self):
        self.watermarks()
        self.mark_as_passenger()
        code, log = self.run_main("--date", self.DAY, "--unit", str(self.UNIT))
        self.assertEqual(code, 0, log)
        con = sqlite3.connect(self.db)
        try:
            con.execute("DELETE FROM gps_work_polygons")
            con.execute("DELETE FROM gps_daily_aggregates")
            con.commit()
        finally:
            con.close()
        # только легковая имеет точки в эти сутки
        storage.write_points(self.folder, [])
        other = "2026-07-28"
        midnight, _ = daily.day_bounds(other)
        storage.write_points(self.folder, [
            (self.CAR, midnight + 9 * 3600 + i * 30,
             64.40, 39.80, 8.0, 90, 14) for i in range(10)])
        code, log = self.run_main("--date", other)
        self.assertEqual(code, 0, log)
        self.assertIn("every one is excluded (ne_polevaya: 1)", log)
        self.assertEqual(self.rows("gps_daily_aggregates"), [])


class TrackWithoutHectares(unittest.TestCase):
    """A1: спецтехника -- след трека считается, гектары нет. Без базы.

    [REASON]: решение владельца 28.09.2026 -- «исключать нет, гектар по ней не
    считать», и прочтение «машина остаётся на экране со следом» подтверждено.
    Проверяется, что СЛЕД остаётся тем же самым: иначе «без гектаров» тихо
    превратилось бы в «без всего», и экран показывал бы погрузчик с нулями.
    """

    MEASURES = ("points_total", "points_work", "track_km", "interval_median_s",
                "sats_median", "motion_gaps", "lost_seconds", "gps_jumps")

    def test_the_track_is_measured_exactly_as_for_a_field_machine(self):
        track = read_fixture_track()
        field = daily.compute_day(track)
        special = daily.compute_day(track, track_only=True)
        # Отрицательный контроль: этот же трек у полевой машины даёт участок
        # 8,772 га -- без него «участков нет» было бы верно и на пустом треке.
        self.assertIsNone(field.reason)
        self.assertAlmostEqual(sum(s["area_ha"] for s in field.sites), 8.772,
                               delta=0.01)
        self.assertEqual(special.reason, "spetstekhnika")
        self.assertEqual(special.sites, [])
        for name in self.MEASURES:
            self.assertEqual(special.aggregate[name], field.aggregate[name], name)
        self.assertAlmostEqual(special.aggregate["track_km"], 32.1, delta=0.2)

    def test_the_category_speaks_before_the_measurements(self):
        # Погрузчик, простоявший день, и погрузчик с редкой записью -- одна
        # причина: ни тому, ни другому гектары не полагались бы и так.
        standing = daily.compute_day(synthetic_day(speed=0.0), track_only=True)
        rare = daily.compute_day(synthetic_day(step_s=301), track_only=True)
        self.assertEqual(standing.reason, "spetstekhnika")
        self.assertEqual(rare.reason, "spetstekhnika")
        self.assertEqual(standing.aggregate["points_work"], 0)
        self.assertEqual(rare.aggregate["interval_median_s"], 301.0)

    def test_a_day_without_points_is_still_no_points(self):
        # Строка «точек нет» правдива при любом правиле: без неё нельзя
        # отличить погрузчик без трекера от погрузчика без гектаров.
        self.assertEqual(daily.compute_day([], track_only=True).reason,
                         "net_tochek")


class SpecialMachinery(ExcludedObjects):
    """A1 в базе: правило по категории, catch-up в обе стороны, ответы целы.

    Фикстура 3464 (27.07, 8,772 га) -- машина, которую меняют между
    категориями; легковая соседка из ExcludedObjects здесь не участвует.
    """

    def set_category(self, category, unit=None):
        """Одна строка сопоставления на объект; категория меняется ей на месте."""
        unit = unit or self.UNIT
        con = sqlite3.connect(self.db)
        try:
            row = con.execute("SELECT equipment_id FROM vialon_mappings "
                              "WHERE wialon_id = ?", (unit,)).fetchone()
            if row is None:
                cursor = con.execute(
                    "INSERT INTO equipment (name, plate, category, "
                    "organization_id, is_active) VALUES (?, ?, ?, 1, 1)",
                    ("Pogruzchik", "80 373 HA", category))
                con.execute(
                    "INSERT INTO vialon_mappings (vialon_name, wialon_id, "
                    "equipment_id, skip) VALUES (?, ?, ?, 0)",
                    ("Unit %d" % unit, unit, cursor.lastrowid))
            else:
                con.execute("UPDATE equipment SET category = ? WHERE id = ?",
                            (category, row[0]))
            con.commit()
        finally:
            con.close()

    def day_row(self, unit=None):
        unit = unit or self.UNIT
        return next(r for r in self.rows("gps_daily_aggregates")
                    if r["wialon_id"] == unit)

    def polygons(self, unit=None):
        unit = unit or self.UNIT
        return [r for r in self.rows("gps_work_polygons")
                if r["wialon_id"] == unit]

    def catch_up(self):
        return self.run_main("--catch-up", "--until", self.DAY,
                             "--window-days", "3")

    def test_a_special_machine_gets_its_track_and_no_site(self):
        self.watermarks()
        self.set_category("special")
        code, log = self.run_main("--date", self.DAY)
        self.assertEqual(code, 0, log)
        row = self.day_row()
        self.assertEqual(row["reason"], "spetstekhnika")
        self.assertEqual(row["points_total"], 1598)
        self.assertAlmostEqual(row["track_km"], 32.1, delta=0.2)
        self.assertEqual(row["interval_median_s"], 30.0)
        self.assertEqual(self.polygons(), [])
        # и в консоли сказано, сколько таких и под каким словом
        self.assertIn("track only (spetstekhnika): 1", log)

    def test_a_field_machine_on_the_same_track_gets_its_hectares(self):
        """Отрицательный контроль: категория, а не трек, снимает гектары."""
        self.watermarks()
        self.set_category("mtz")
        code, log = self.run_main("--date", self.DAY)
        self.assertEqual(code, 0, log)
        self.assertIsNone(self.day_row()["reason"])
        self.assertEqual(len(self.polygons()), 1)
        self.assertNotIn("track only", log)

    def test_an_explicit_unit_does_not_bypass_the_rule(self):
        # «Покажи этот объект» считает его, но гектары от просьбы не появляются:
        # правило о публикации, а не о том, смотреть ли.
        self.watermarks()
        self.set_category("special")
        code, log = self.run_main("--date", self.DAY, "--unit", str(self.UNIT))
        self.assertEqual(code, 0, log)
        self.assertEqual(self.day_row()["reason"], "spetstekhnika")

    def test_catch_up_turns_old_hectares_into_the_track_and_keeps_the_answer(self):
        self.watermarks()
        self.run_main("--date", self.DAY)          # до решения: 8,772 га
        self.answer("работа")
        self.set_category("special")
        code, log = self.catch_up()
        self.assertEqual(code, 0, log)
        self.assertIn("category rule -- 1 day(s) now track only", log)
        self.assertEqual(self.day_row()["reason"], "spetstekhnika")
        # Полигон с ответом оператора НЕ удалён: ответ -- ручные данные.
        kept = self.polygons()
        self.assertEqual(len(kept), 1)
        self.assertEqual(kept[0]["operator_label"], "работа")
        # второй прогон ничего не делает: правило уже в силе
        code, log = self.catch_up()
        self.assertEqual(code, 0, log)
        self.assertIn("nothing is waiting", log)

    def test_back_to_a_field_category_returns_the_hectares_and_the_answer(self):
        """Решение обратимо: ответ, данный до него, переезжает на новый участок."""
        self.watermarks()
        self.run_main("--date", self.DAY)
        self.answer("проезд")
        self.set_category("special")
        self.catch_up()
        self.set_category("mtz")
        code, log = self.catch_up()
        self.assertEqual(code, 0, log)
        self.assertIn("1 day(s) back to hectares", log)
        self.assertIsNone(self.day_row()["reason"])
        polygons = self.polygons()
        self.assertEqual(len(polygons), 1)
        self.assertAlmostEqual(polygons[0]["area_ha"], 8.772, delta=0.01)
        self.assertEqual(polygons[0]["operator_label"], "проезд")
        self.assertNotIn("poteryano", log)

    def test_a_day_whose_points_are_gone_is_left_alone(self):
        # Сутки без точек на диске пересчитать нельзя: пересчёт из пустого
        # файла затёр бы измеренный трек словом net_tochek. Такие сутки
        # называются числом и остаются как были.
        self.watermarks()
        self.run_main("--date", self.DAY)
        con = sqlite3.connect(self.db)
        try:
            con.execute(
                "INSERT INTO gps_daily_aggregates (work_date, wialon_id, "
                "points_total, points_work, track_km, interval_median_s, "
                "sats_median, motion_gaps, lost_seconds, gps_jumps, reason, "
                "method_version, computed_at) VALUES ('2026-07-26', ?, 900, "
                "500, 20.5, 30.0, 12.0, 0, 0, 0, NULL, 'm', '2026-07-27 03:00')",
                (self.UNIT,))
            con.commit()
        finally:
            con.close()
        self.set_category("special")
        code, log = self.catch_up()
        self.assertEqual(code, 0, log)
        self.assertIn("1 day(s) now track only", log)
        self.assertIn("1 left alone: points no longer on disk", log)
        rows = {r["work_date"]: r for r in self.rows("gps_daily_aggregates")
                if r["wialon_id"] == self.UNIT}
        self.assertEqual(rows[self.DAY]["reason"], "spetstekhnika")
        self.assertIsNone(rows["2026-07-26"]["reason"])
        self.assertEqual(rows["2026-07-26"]["track_km"], 20.5)

    def test_an_excluded_object_is_not_recomputed_by_the_category_pass(self):
        # «Не наша» строка на погрузчик: объект исключён, и проход по категории
        # его не трогает -- экран его скрывает, задним числом ничего не
        # пересчитывается.
        self.watermarks()
        self.run_main("--date", self.DAY)
        self.set_category("special")
        con = sqlite3.connect(self.db)
        try:
            con.execute("UPDATE vialon_mappings SET skip = 1 WHERE wialon_id = ?",
                        (self.UNIT,))
            con.commit()
        finally:
            con.close()
        code, log = self.catch_up()
        self.assertEqual(code, 0, log)
        self.assertIn("nothing is waiting", log)
        self.assertIsNone(self.day_row()["reason"])

    def test_a_pending_day_of_a_special_machine_comes_back_as_track_only(self):
        # sbor_nepolnyy принадлежит первому проходу; когда коллектор дошёл,
        # сутки досчитываются уже по правилу категории.
        storage.set_watermark(self.folder, self.UNIT, self.finish - 3600)
        self.set_category("special")
        self.run_main("--date", self.DAY, "--unit", str(self.UNIT))
        self.assertEqual(self.day_row()["reason"], "sbor_nepolnyy")
        storage.set_watermark(self.folder, self.UNIT, self.finish)
        code, log = self.catch_up()
        self.assertEqual(code, 0, log)
        self.assertEqual(self.day_row()["reason"], "spetstekhnika")
        self.assertEqual(self.polygons(), [])

    def test_run_day_looks_the_rule_up_when_not_told(self):
        self.watermarks()
        self.set_category("special")
        result = daily.run_day(self.DAY, self.UNIT, folder=self.folder,
                               db_path=self.db, log=lambda *a: None)
        self.assertEqual(result.reason, "spetstekhnika")


class OneBadDayDoesNotKillTheRun(CollectionCompleteness):
    """GPS-13: сбой одних суток не уносит прогон по остальным объектам.

    [REASON]: дважды за два дня один объект убивал прогон по 400+ машинам.
    27.09 -- `None > 30.0` на сутках с единственной точкой; 28.09 --
    `TopologyException` на самопересекающемся контуре. Оба частных случая
    починены по отдельности, но форма отказа одна, и она повторится на
    следующей неизвестной причине. Проверяется именно ФОРМА: любое исключение
    на одних сутках должно быть названо, посчитано, оставить базу без строки --
    и не помешать соседу.
    """

    OTHER = 555001

    def setUp(self):
        super().setUp()
        midnight, _ = daily.day_bounds(self.DAY)
        storage.write_points(self.folder, [
            (self.OTHER, midnight + 9 * 3600 + i * 30,
             64.60 + i * 2e-5, 40.10 + i * 2e-5, 8.0, 90, 14)
            for i in range(200)])

    def ready(self):
        for unit_id in (self.UNIT, self.OTHER):
            storage.set_watermark(self.folder, unit_id, self.finish)

    def explode_on(self, target):
        """Подменить run_day так, чтобы он падал ровно на одном объекте.

        Синтетическое исключение, а не конкретный баг: проверяется охранник, а
        не очередная известная причина -- у следующей причины будет свой класс.
        """
        original = daily.run_day

        def patched(day, unit_id, **kwargs):
            if unit_id == target:
                raise RuntimeError("sintetichesky sboy rascheta")
            return original(day, unit_id, **kwargs)

        daily.run_day = patched
        self.addCleanup(setattr, daily, "run_day", original)
        return lambda: setattr(daily, "run_day", original)

    def units_with_rows(self):
        return sorted(r["wialon_id"] for r in self.rows("gps_daily_aggregates"))

    def test_the_neighbour_is_computed_and_the_failure_is_named(self):
        self.ready()
        self.explode_on(self.UNIT)          # падает ПЕРВЫЙ по номеру объект
        code, log = self.run_main("--date", self.DAY)
        self.assertEqual(code, daily.EXIT_SOME_DAYS_FAILED, log)
        self.assertIn("SBOY: RuntimeError", log)
        self.assertIn("NE POSCHITANO IZ-ZA SBOYA: 1", log)
        self.assertIn("sintetichesky sboy rascheta", log)
        self.assertEqual(self.units_with_rows(), [self.OTHER])

    def test_nothing_is_written_for_the_failing_day(self):
        """Сутки остаются «мы не смотрели» -- это правда, и это поправимо."""
        self.ready()
        self.explode_on(self.UNIT)
        self.run_main("--date", self.DAY)
        self.assertNotIn(self.UNIT, self.units_with_rows())
        self.assertEqual([r["wialon_id"] for r in self.rows("gps_work_polygons")],
                         [])

    def test_the_day_is_computed_once_the_cause_is_gone(self):
        """Строки нет -> --catch-up возьмёт сутки снова и досчитает сам."""
        self.ready()
        recover = self.explode_on(self.UNIT)
        self.run_main("--date", self.DAY)
        self.assertNotIn(self.UNIT, self.units_with_rows())
        recover()                           # причина ушла
        code, log = self.run_main("--catch-up", "--until", self.DAY,
                                  "--window-days", "3")
        self.assertEqual(code, 0, log)
        self.assertIn(self.UNIT, self.units_with_rows())

    def test_catch_up_survives_a_failing_day_too(self):
        self.ready()
        self.explode_on(self.UNIT)
        code, log = self.run_main("--catch-up", "--until", self.DAY,
                                  "--window-days", "3")
        self.assertEqual(code, daily.EXIT_SOME_DAYS_FAILED, log)
        self.assertIn("NE POSCHITANO IZ-ZA SBOYA: 1", log)
        self.assertEqual(self.units_with_rows(), [self.OTHER])

    def test_a_clean_run_says_nothing_about_failures_and_returns_zero(self):
        """Отрицательный контроль: без сбоя ни блока, ни кода 5."""
        self.ready()
        code, log = self.run_main("--date", self.DAY)
        self.assertEqual(code, 0, log)
        self.assertNotIn("SBOY", log)
        self.assertEqual(self.units_with_rows(), [self.UNIT, self.OTHER])

    def test_the_failure_report_stays_ascii(self):
        """Консоль -- ASCII: та самая буква U+04B2, что убила прогон 18.08."""
        self.ready()
        original = daily.run_day

        def patched(day, unit_id, **kwargs):
            if unit_id == self.UNIT:
                raise RuntimeError("Ҳосил йиғиш сорвался")
            return original(day, unit_id, **kwargs)

        daily.run_day = patched
        self.addCleanup(setattr, daily, "run_day", original)
        code, log = self.run_main("--date", self.DAY)
        self.assertEqual(code, daily.EXIT_SOME_DAYS_FAILED, log)
        log.encode("ascii")


class CommandLine(unittest.TestCase):
    """День по умолчанию считает сам расчёт, а не обёртка расписания.

    [REASON]: этот вход появился, когда ранбук выката потребовал посчитать
    «вчера» в .bat-файле Windows -- `for /f` вокруг PowerShell со вложенными
    кавычками. Там кавычка съедает половину команды, задача молча не
    запускается по расписанию, и узнаётся это через неделю по пустым суткам.
    День у ночного расчёта всегда один и тот же, и знать его должен он сам.
    """

    def test_without_a_date_it_takes_yesterday_local(self):
        out, saved = io.StringIO(), sys.stdout
        sys.stdout = out
        try:
            code = daily.main(["--dir", tempfile.mkdtemp(),
                               "--db", os.path.join(REPO_ROOT, "models.py")])
        finally:
            sys.stdout = saved
        self.assertEqual(code, 0)
        yesterday = (datetime.now(TZ) - timedelta(days=1)).strftime("%Y-%m-%d")
        self.assertIn(yesterday, out.getvalue())

    def test_a_date_that_is_not_a_date_is_refused(self):
        err, saved = io.StringIO(), sys.stderr
        sys.stderr = err
        try:
            code = daily.main(["--date", "vchera"])
        finally:
            sys.stderr = saved
        self.assertEqual(code, 2)
        self.assertIn("YYYY-MM-DD", err.getvalue())


if __name__ == "__main__":
    unittest.main()
