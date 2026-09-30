# -*- coding: utf-8 -*-
"""tools/gps_implausible_sites.py -- the time on a site, measured on the track.

The report calls a site impossible when its area is more than an implement of
the given width could cover in the time spent on the site at the top of the
method's work speed window. Everything that needs no geometry is pinned in
tests/test_gps_implausible_sites.py. Here, in the geo venv, is the part that
does:

  * with no boundary tolerance the report's time rule IS the engine's
    `site_minutes` -- measured on the recorded day of unit 3464 against the
    engine's own sites;
  * that recorded field day is possible;
  * a thin single-pass strip keeps almost no STORED minutes (its points are
    the polygon's own vertices, on the boundary, and `site_minutes` counts
    points strictly inside) -- the pitfall is pinned here, and so is the
    remedy: the track time with the boundary makes the strip possible;
  * a sparse ring enclosing a square is impossible;
  * the command, end to end over a database and a point file, names the ring
    and only the ring, and writes nothing.

Run (needs the geo venv, see gps/README.md):
  python -m unittest discover -s gps/tests -t .
"""
import contextlib
import hashlib
import io
import os
import shutil
import sqlite3
import sys
import tempfile
import unittest
from datetime import datetime

import numpy as np
from pyproj import Transformer

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(
    os.path.abspath(__file__))))
if REPO_ROOT not in sys.path:
    sys.path.insert(0, REPO_ROOT)

import tools.gps_implausible_sites as gis                           # noqa: E402
from gps import daily                                              # noqa: E402
from gps.area import UTM_41N, alpha_shape, to_utm                  # noqa: E402
from gps.tests.test_daily import TZ, read_fixture_track            # noqa: E402
from gps_collector import storage                                  # noqa: E402
from tests.test_gps_units_inventory import DDL                      # noqa: E402

TO_DEGREES = Transformer.from_crs(UTM_41N, "EPSG:4326", always_xy=True)
DAY = "2026-07-27"            # the recorded day of unit 3464
TRACTOR, STRIP, RING = 3464, 9001, 9002


def track_from_utm(xs, ys, start, step_s, speed):
    """[(t, lon, lat, speed, sats)] through the given UTM points."""
    lons, lats = TO_DEGREES.transform(np.asarray(xs), np.asarray(ys))
    return [(start + i * step_s, float(lon), float(lat), speed, 12)
            for i, (lon, lat) in enumerate(zip(lons, lats))]


def local_start(hour):
    return int(datetime.strptime(DAY, "%Y-%m-%d").replace(tzinfo=TZ)
               .timestamp()) + hour * 3600


def shape_of(points, alpha_m):
    """The site the way the engine builds it: from the track projected ONCE.

    [REASON]: the engine projects the day's track once and builds the sites
    and their `minutes` from those same coordinates, so a vertex IS the point
    and falls exactly on the boundary. Building from the designed metres and
    measuring after a round trip through degrees would put half the vertices
    a nanometre inside by chance -- and hide the very pitfall this pins.
    """
    xs, ys = to_utm([p[1] for p in points], [p[2] for p in points])
    return alpha_shape(np.column_stack([xs, ys]), alpha_m=alpha_m)


def thin_strip():
    """One pass, 1 km, zig-zagging 3 m either side: every point a vertex."""
    x0, y0 = to_utm([64.50], [39.99])
    xs = x0[0] + np.arange(46) * 22.0
    ys = y0[0] + np.where(np.arange(46) % 2 == 0, 3.0, -3.0)
    points = track_from_utm(xs, ys, local_start(9), 10, 8.0)
    return shape_of(points, 60.0), points


def sparse_ring():
    """A 1 km square walked once round its edge at 5 km/h, points 10 s apart."""
    x0, y0 = to_utm([64.60], [40.05])
    side, step = 1000.0, 5.0 / 3.6 * 10.0
    along = np.arange(0.0, 4 * side, step)
    xs, ys = [], []
    for s in along:
        edge, offset = int(s // side), s % side
        dx, dy = [(offset, 0.0), (side, offset), (side - offset, side),
                  (0.0, side - offset)][edge]
        xs.append(x0[0] + dx)
        ys.append(y0[0] + dy)
    points = track_from_utm(xs, ys, local_start(13), 10, 5.0)
    return shape_of(points, 800.0), points


def strict_minutes(points, polygon):
    """The engine's stored `minutes`: points strictly inside the site."""
    xs, ys = to_utm([p[1] for p in points], [p[2] for p in points])
    return daily.site_minutes(points, xs, ys, polygon)[0]


class TheEngineRule(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        cls.points = read_fixture_track()
        cls.sites = daily.compute_day(cls.points).sites

    def test_without_a_tolerance_the_time_is_the_engine_s_site_minutes(self):
        self.assertTrue(self.sites)
        for site in self.sites:
            with self.subTest(site=site["site_number"]):
                polygon = daily._utm_polygon_from_geojson(
                    site["polygon_geojson"])
                ours = gis.track_minutes(self.points, site["polygon_geojson"],
                                         boundary_m=0)
                self.assertAlmostEqual(
                    ours, strict_minutes(self.points, polygon), places=6)

    def test_the_stored_minutes_are_a_lower_bound_of_the_track_time(self):
        for site in self.sites:
            with self.subTest(site=site["site_number"]):
                ours = gis.track_minutes(self.points, site["polygon_geojson"])
                self.assertGreaterEqual(ours + 1e-9, site["minutes"])

    def test_the_recorded_field_day_is_possible(self):
        for site in self.sites:
            with self.subTest(site=site["site_number"]):
                minutes = gis.track_minutes(self.points,
                                            site["polygon_geojson"])
                self.assertLessEqual(site["area_ha"],
                                     gis.limit_ha(minutes, gis.WIDTH_M))


class ThinStripAndSparseRing(unittest.TestCase):

    def test_a_thin_strip_has_no_stored_time_but_is_possible(self):
        polygon, points = thin_strip()
        self.assertGreater(polygon.area / 10000.0, 0.1)
        # the pitfall: every point is a vertex, none is strictly inside
        self.assertLess(strict_minutes(points, polygon), 1.0)
        text = daily.polygon_geojson(polygon)
        minutes = gis.track_minutes(points, text)
        self.assertGreater(minutes, 7.0)
        self.assertLessEqual(polygon.area / 10000.0,
                             gis.limit_ha(minutes, gis.WIDTH_M))

    def test_a_sparse_ring_enclosing_a_square_is_impossible(self):
        polygon, points = sparse_ring()
        area_ha = polygon.area / 10000.0
        self.assertGreater(area_ha, 90.0)
        minutes = gis.track_minutes(points, daily.polygon_geojson(polygon))
        self.assertAlmostEqual(minutes, 48.0, delta=1.0)
        self.assertGreater(area_ha, 2 * gis.limit_ha(minutes, gis.WIDTH_M))


class TheCommand(unittest.TestCase):

    def setUp(self):
        self.folder = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.folder, True)
        self.db = os.path.join(self.folder, "transport.db")
        con = sqlite3.connect(self.db)
        try:
            for statement in DDL:
                con.execute(statement)
            field = read_fixture_track()
            strip, strip_points = thin_strip()
            ring, ring_points = sparse_ring()
            rows = [(TRACTOR, site["site_number"], site["area_ha"],
                     site["minutes"], site["polygon_geojson"])
                    for site in daily.compute_day(field).sites]
            rows.append((STRIP, 1, strip.area / 10000.0,
                         strict_minutes(strip_points, strip),
                         daily.polygon_geojson(strip)))
            rows.append((RING, 1, ring.area / 10000.0,
                         strict_minutes(ring_points, ring),
                         daily.polygon_geojson(ring)))
            for unit in (TRACTOR, STRIP, RING):
                con.execute("INSERT INTO gps_daily_aggregates (work_date, "
                            "wialon_id, points_total, track_km, reason) "
                            "VALUES (?, ?, 100, 5.0, NULL)", (DAY, unit))
            con.executemany("INSERT INTO gps_work_polygons (work_date, "
                            "wialon_id, site_number, area_ha, minutes, "
                            "polygon_geojson) VALUES (?, ?, ?, ?, ?, ?)",
                            [(DAY,) + row for row in rows])
            con.commit()
        finally:
            con.close()
        storage.write_points(self.folder, [
            (unit, t, lon, lat, speed, 0, sats)
            for unit, points in ((TRACTOR, field), (STRIP, strip_points),
                                 (RING, ring_points))
            for t, lon, lat, speed, sats in points])
        self.points = storage.points_path(self.folder, "202607")

    def digest(self, path):
        with open(path, "rb") as handle:
            return hashlib.sha256(handle.read()).hexdigest()

    def test_the_ring_and_only_the_ring_is_named_and_nothing_is_written(self):
        before = (self.digest(self.db), self.digest(self.points))
        with contextlib.redirect_stdout(io.StringIO()) as out:
            code = gis.main(["--db", self.db, "--dir", self.folder,
                             "--since", DAY, "--until", DAY])
        text = out.getvalue()
        self.assertEqual(code, 0, text)
        self.assertIn("impossible sites   : 1,", text)
        self.assertIn("not judged         : 0,", text)
        listed = [line for line in text.splitlines()
                  if line.startswith(DAY)]
        self.assertEqual(len(listed), 1, text)
        self.assertEqual(listed[0].split()[1], str(RING))
        self.assertTrue(text.isascii())
        self.assertEqual(before, (self.digest(self.db),
                                  self.digest(self.points)))


if __name__ == "__main__":
    unittest.main()
