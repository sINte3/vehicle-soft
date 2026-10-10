# -*- coding: utf-8 -*-
"""gps/edge.py -- driving at work speed beside a field is not work (candidate).

What is held here, and why each case:
  * the switch: off is the method in force bit for bit; on is labelled with
    its own version after the base method's;
  * a clean field is left exactly as it is;
  * a road along the headland, which today's alpha glues to the field, is
    taken out, and the field comes back to its own area (with a control that
    the glue is real -- otherwise the test could not fail);
  * the rule only removes ground: every site of the rule lies inside today's;
  * scales are measured per site: a day with a plough field and a spray field
    keeps both (a day-level scale cuts the spray field's outer passes);
  * a site without an inside (two spray passes) stays as it is;
  * the rule never deletes a whole site: a site whose remainder would fall
    below the work floor is put back;
  * the two real days: New Holland 80 080 HA on 01.10.2026 (from the owner's
    KML; he measured the two fields by hand at 7.805 ha, today's site 8.33 ha)
    and unit 3464 on 27.07.2026;
  * determinism and degenerate input.
"""
import csv
import math
import os
import random
import unittest
import unittest.mock

import numpy as np
import shapely

from gps import area, edge
from gps.area import (EDGE_RULE_VERSION, METHOD_VERSION, alpha_shape, densify,
                      method_version, to_utm, work_sites)
from gps.daily import compute_day
from gps.tests.test_area import FIXTURES, shuttle_track, xy_to_lonlat

NH_FIXTURE = os.path.join(FIXTURES, "nh_80080ha_20261001_work.csv")
NH_OWNER_HA = 7.805            # 6.364 + 1.441, the owner's hand measurement


def road(corners_m, speed=10.0, step_m=20.0, start_time=0.0, laps=1,
         noise_m=0.0, seed=0):
    """Driving along a polyline in the work window, one point every 20 m (the
    fleet's trackers write about every 20 m: 3464 on 27.07, New Holland 01.10)."""
    rng = random.Random(seed)
    track, t = [], start_time
    for _ in range(laps):
        for (ea, na), (eb, nb) in zip(corners_m, corners_m[1:]):
            length = math.hypot(eb - ea, nb - na)
            steps = max(1, int(length // step_m))
            for i in range(steps):
                f = i / steps
                e = ea + f * (eb - ea) + rng.gauss(0.0, noise_m)
                n = na + f * (nb - na) + rng.gauss(0.0, noise_m)
                lon, lat = xy_to_lonlat(e, n)
                track.append((t, lon, lat, speed))
                t += step_m / (speed / 3.6)
    return track, t


def shifted(track, east_m, north_m=0.0):
    """The same track moved east/north (in the local frame of xy_to_lonlat)."""
    out = []
    for t, lon, lat, speed in track:
        e, n = lonlat_to_xy(lon, lat)
        lon2, lat2 = xy_to_lonlat(e + east_m, n + north_m)
        out.append((t, lon2, lat2, speed))
    return out


def lonlat_to_xy(lon, lat):
    from gps.tests.test_area import BASE_LAT, BASE_LON, M_PER_DEG_LAT, M_PER_DEG_LON
    return (lon - BASE_LON) * M_PER_DEG_LON, (lat - BASE_LAT) * M_PER_DEG_LAT


def total(sites):
    return sum(s.area_ha for s in sites)


def field_and_road():
    """A cultivation field (6 m passes, 150 x 300 m) and a field road along its
    south end 15 m beyond the pass ends, driven there and back at 10 km/h --
    within the 2 x 10 m that today's alpha bridges."""
    field = shuttle_track(150.0, 300.0, pass_spacing_m=6.0, point_step_m=20.0)
    lane, _ = road([(-200.0, -15.0), (350.0, -15.0), (-200.0, -14.0)],
                   start_time=field[-1][0] + 600.0)
    return field, lane


def nh_points():
    points, groups = [], []
    with open(NH_FIXTURE, encoding="utf-8") as fh:
        for row in csv.DictReader(fh, delimiter=";"):
            points.append((float(row["x"]), float(row["y"])))
            groups.append(row["group"])
    return points, groups


def pieces(shape, floor=area.MIN_WORK_AREA_HA):
    if shape is None or shape.is_empty:
        return []
    return sorted((g for g in getattr(shape, "geoms", [shape])
                   if g.geom_type == "Polygon" and g.area / 1e4 >= floor),
                  key=lambda g: -g.area)


class Switch(unittest.TestCase):
    def test_off_is_the_method_in_force_bit_for_bit(self):
        field, lane = field_and_road()
        plain, _ = work_sites(field + lane)
        off, _ = work_sites(field + lane, edge_rule=False)
        self.assertEqual([(s.area_ha, s.polygon.wkb) for s in plain],
                         [(s.area_ha, s.polygon.wkb) for s in off])
        self.assertTrue(all(s.method_version == METHOD_VERSION for s in plain))

    def test_on_is_named_after_the_base_method(self):
        self.assertEqual(method_version(True, edge_rule=True),
                         METHOD_VERSION + "+" + EDGE_RULE_VERSION)
        self.assertEqual(method_version(False, edge_rule=True),
                         area.PREVIOUS_METHOD_VERSION + "+" + EDGE_RULE_VERSION)
        self.assertEqual(method_version(True, alpha_m=10.0, edge_rule=True),
                         "fixed-alpha-10m+" + EDGE_RULE_VERSION)
        field, lane = field_and_road()
        sites, _ = work_sites(field + lane, edge_rule=True)
        self.assertTrue(sites)
        self.assertTrue(all(s.method_version == method_version(True, edge_rule=True)
                            for s in sites))

    def test_the_daily_computation_passes_it_through(self):
        field, lane = field_and_road()
        points = [(int(t), lon, lat, speed, 12) for t, lon, lat, speed in field + lane]
        plain = compute_day(points)
        trimmed = compute_day(points, edge_rule=True)
        self.assertEqual(plain.aggregate["method_version"], METHOD_VERSION)
        self.assertEqual(trimmed.aggregate["method_version"],
                         METHOD_VERSION + "+" + EDGE_RULE_VERSION)
        self.assertLess(sum(r["area_ha"] for r in trimmed.sites),
                        sum(r["area_ha"] for r in plain.sites))


class Behaviour(unittest.TestCase):
    def test_a_clean_field_is_left_as_it_is(self):
        field = shuttle_track(150.0, 300.0, pass_spacing_m=6.0, point_step_m=20.0)
        today, _ = work_sites(field)
        trimmed, _ = work_sites(field, edge_rule=True)
        self.assertEqual([(s.area_ha, s.polygon.wkb) for s in today],
                         [(s.area_ha, s.polygon.wkb) for s in trimmed])

    def test_a_road_along_the_headland_is_taken_out(self):
        field, lane = field_and_road()
        alone, _ = work_sites(field)
        today, _ = work_sites(field + lane)
        trimmed, _ = work_sites(field + lane, edge_rule=True)
        # control: today's alpha really glues the road to the field
        self.assertGreater(total(today) - total(alone), 0.2)
        self.assertLess(abs(total(trimmed) - total(alone)), 0.01 * total(alone),
                        "the field must come back to its own area")

    def test_only_removes_ground(self):
        field, lane = field_and_road()
        today, _ = work_sites(field + lane)
        trimmed, _ = work_sites(field + lane, edge_rule=True)
        union = shapely.union_all([s.polygon for s in today])
        for site in trimmed:
            self.assertLess(site.polygon.difference(union).area, 1e-6)
        self.assertLessEqual(total(trimmed), total(today))

    def test_scales_are_measured_per_site(self):
        # A plough field (2.3 m passes) and a spray field (18 m passes) on one
        # day, 500 m apart. With one scale for the day, the spray field's
        # outer passes lie beyond a margin sized by the plough.
        plough = shuttle_track(100.0, 200.0, pass_spacing_m=2.3, point_step_m=20.0,
                               speed=6.0)
        spray = shuttle_track(216.0, 300.0, pass_spacing_m=18.0, point_step_m=20.0,
                              speed=10.0, start_time=plough[-1][0] + 1800.0)
        day = plough + shifted(spray, 500.0)
        today, _ = work_sites(day)
        trimmed, _ = work_sites(day, edge_rule=True)
        self.assertEqual(len(today), 2)
        self.assertEqual(len(trimmed), 2)
        for before, after in zip(sorted(today, key=lambda s: s.area_ha),
                                 sorted(trimmed, key=lambda s: s.area_ha)):
            self.assertGreater(after.area_ha, 0.99 * before.area_ha)

    def test_a_site_without_an_inside_stays(self):
        # two spray passes: each has a pass beside it on one side only
        strip = shuttle_track(18.0, 300.0, pass_spacing_m=18.0, point_step_m=20.0,
                              speed=10.0)
        today, _ = work_sites(strip)
        self.assertTrue(today, "control: two passes 18 m apart make a site today")
        trimmed, _ = work_sites(strip, edge_rule=True)
        self.assertEqual([s.polygon.wkb for s in today],
                         [s.polygon.wkb for s in trimmed])

    def test_a_site_is_never_deleted(self):
        # Whatever the mask says, a site whose remainder falls below the work
        # floor comes back whole: here the mask keeps four points of it.
        field = shuttle_track(150.0, 300.0, pass_spacing_m=6.0, point_step_m=20.0)
        xs, ys = to_utm([r[1] for r in field], [r[2] for r in field])
        points = list(zip(xs, ys))
        alpha = max(area.ALPHA_M, area.ALPHA_SPACING_FACTOR * area.pass_spacing(points))
        today = alpha_shape(densify(points), alpha)

        def almost_nothing(points_xy, *_args, **_kwargs):
            mask = np.zeros(len(points_xy), bool)
            mask[:4] = True
            return mask
        with unittest.mock.patch.object(edge, "keep_mask", almost_nothing):
            shape, info = edge.trim(points, today, alpha)
        self.assertEqual(info["restored"], 1)
        self.assertAlmostEqual(shape.area, today.area, delta=1e-6)


class RealDays(unittest.TestCase):
    def test_new_holland_01_10(self):
        points, groups = nh_points()
        spacing, alpha = area._adaptive_alpha(points, True)
        self.assertAlmostEqual(alpha, 10.0)
        today = alpha_shape(densify(points), alpha)
        before = pieces(today)
        self.assertEqual([round(g.area / 1e4, 4) for g in before],
                         [8.3296, 0.7311, 0.3839])
        shape, info = edge.trim(points, today, alpha)
        after = pieces(shape)
        site1 = sum(g.intersection(before[0]).area for g in after) / 1e4
        # measured 09.10 on this fixture, before any run on the owner's data
        self.assertAlmostEqual(site1, 7.9040, delta=0.001)
        self.assertLess(abs(site1 - NH_OWNER_HA) / NH_OWNER_HA, 0.02)
        # the two road "sites" of that day have no inside to trim around and
        # stay as they are (the rule never deletes a site)
        self.assertEqual([round(g.area / 1e4, 4) for g in after[1:]], [0.7311, 0.3839])
        # only removes, by construction: rebuilt from fewer points the shape
        # pokes out of today's by about 2 m2 on this day (control), and the
        # intersection takes that back
        self.assertGreater(edge.shape_of_kept(points, info["keep"], alpha)
                           .difference(today).area, 1.0)
        self.assertLess(shape.difference(today).area, 1e-6)
        keep = info["keep"]
        far = [k for k, g in zip(keep, groups) if g == "FAR"]
        late = [k for k, g in zip(keep, groups) if g == "EE_late"]
        self.assertLess(sum(far), 0.25 * len(far))
        self.assertLess(sum(late), 0.4 * len(late))

    def test_unit_3464_27_07(self):
        track = []
        with open(os.path.join(FIXTURES, "track_3464_20260727.csv"),
                  encoding="utf-8") as fh:
            for row in csv.DictReader(fh, delimiter=";"):
                hh, mm, ss = row["time"].split(":")
                track.append((int(hh) * 3600 + int(mm) * 60 + int(ss),
                              float(row["lon"]), float(row["lat"]),
                              float(row["speed"])))
        today, _ = work_sites(track)
        trimmed, _ = work_sites(track, edge_rule=True)
        self.assertAlmostEqual(total(today), 8.772, delta=0.001)
        # the arrival and the departure along the north edge go; the passes stay
        self.assertAlmostEqual(total(trimmed), 8.5003, delta=0.001)


class Robustness(unittest.TestCase):
    def test_the_rule_sees_the_engines_own_dense_points(self):
        # [the rule finds passes on the same 5 m points the engine builds the
        # shape from: segments of 150 m and longer are gaps for both]
        rng = random.Random(7)
        xy, x = [], 0.0
        for step in (3.0, 12.0, 40.0, 149.0, 151.0, 200.0, 7.5, 0.0, 22.0, 90.0):
            x += step
            xy.append((x, rng.uniform(-2.0, 2.0)))
        P = edge._dense_track(np.asarray(xy))[0]
        self.assertEqual([tuple(p) for p in P], [tuple(p) for p in densify(xy)])

    def test_the_same_input_gives_the_same_mask(self):
        points, _ = nh_points()
        _, alpha = area._adaptive_alpha(points, True)
        today = alpha_shape(densify(points), alpha)
        first = edge.keep_mask(points, today, alpha)
        second = edge.keep_mask(points, today, alpha)
        self.assertTrue(np.array_equal(first, second))

    def test_degenerate_input(self):
        three = [(0.0, 0.0), (1.0, 0.0), (2.0, 0.0)]
        self.assertTrue(edge.keep_mask(three, None, 10.0).all())
        line = [(float(i) * 20.0, 0.0) for i in range(50)]
        self.assertIsNone(alpha_shape(densify(line), 10.0))
        self.assertEqual(edge.trim(line, None, 10.0), (None, None))
        same = [(5.0, 5.0)] * 30
        self.assertTrue(edge.keep_mask(same, None, 10.0).all())

    def test_a_pinned_alpha_works_with_the_rule(self):
        field, lane = field_and_road()
        today, _ = work_sites(field + lane, alpha_m=10.0)
        trimmed, _ = work_sites(field + lane, alpha_m=10.0, edge_rule=True)
        self.assertLess(total(trimmed), total(today))
        self.assertTrue(all(s.method_version == "fixed-alpha-10m+" + EDGE_RULE_VERSION
                            for s in trimmed))


if __name__ == "__main__":
    unittest.main()
