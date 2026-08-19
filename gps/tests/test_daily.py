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

    def test_a_day_with_no_points_is_stored_as_a_row_with_a_reason(self):
        daily.run_day("2026-07-20", 3464, folder=self.folder, db_path=self.db,
                      log=lambda *a: None)
        aggregates = self.rows("gps_daily_aggregates")
        self.assertEqual(len(aggregates), 1)
        self.assertEqual(aggregates[0]["reason"], "net_tochek")
        self.assertEqual(self.rows("gps_work_polygons"), [])


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
