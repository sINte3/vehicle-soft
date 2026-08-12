# -*- coding: utf-8 -*-
"""Tests for the frozen area engine.

Two kinds, and both are needed.

SYNTHETIC -- the answer is known exactly because the input was constructed.
A 100x100 m square driven in passes must give 1.00 ha; a field with an
unworked middle must keep the hole; a track torn in two must not be sewn
across the tear; a machine that merely drove through must give nothing.
These are the cases where a real measurement cannot tell right from wrong,
because no hand measurement of a hypothetical field exists.

REAL FIXTURES -- the engine must reproduce figures already recorded and
independently verified: 8.51 ha on zone 3208, and the gap metric on two
tracks where the naive count is demonstrably wrong.

Every test that asserts a good result is paired with something that would
fail if the engine silently degraded. A test that passes on both correct and
broken code is not a test.

Run (PowerShell, one command per line):
  cd C:\\transport-report
  & C:\\gps_venv\\Scripts\\python.exe -m unittest discover -s gps/tests -t .
"""

import csv
import json
import math
import os
import unittest

from gps.area import (ALPHA_M, DENSIFY_MAX_SEG_M, MOTION_GAP_SECONDS,
                      SPEED_MAX_KMH, alpha_shape, candidate_contours, densify,
                      joint_work_check, polygon_from_wialon, to_utm,
                      track_quality, worked_area)

FIXTURES = os.path.join(os.path.dirname(os.path.abspath(__file__)), "fixtures")

# A patch of flat ground near Bukhara, where the real fields are.
BASE_LON, BASE_LAT = 64.40, 39.99
# Metres per degree at this latitude, used only to build synthetic input.
M_PER_DEG_LAT = 111132.0
M_PER_DEG_LON = 111320.0 * math.cos(math.radians(BASE_LAT))


def xy_to_lonlat(east_m, north_m):
    return (BASE_LON + east_m / M_PER_DEG_LON,
            BASE_LAT + north_m / M_PER_DEG_LAT)


def rectangle_contour(width_m, height_m, margin_m=20.0):
    """A rectangular contour around the synthetic working area."""
    pts = [(-margin_m, -margin_m), (width_m + margin_m, -margin_m),
           (width_m + margin_m, height_m + margin_m), (-margin_m, height_m + margin_m)]
    return polygon_from_wialon([{"x": lon, "y": lat}
                                for lon, lat in (xy_to_lonlat(e, n) for e, n in pts)])


def shuttle_track(width_m, height_m, pass_spacing_m=6.0, point_step_m=40.0,
                  speed=8.0, start_time=0, skip_band=None):
    """A tractor working in parallel passes, the way the real ones do.

    `skip_band` is an (from_m, to_m) band across the field left unworked, to
    build a field with a hole in it.
    """
    track, t = [], start_time
    east = 0.0
    upward = True
    while east <= width_m + 1e-9:
        if skip_band and skip_band[0] <= east <= skip_band[1]:
            east += pass_spacing_m
            continue
        norths = ([n for n in frange(0.0, height_m, point_step_m)] if upward
                  else [n for n in frange(height_m, 0.0, -point_step_m)])
        for north in norths:
            lon, lat = xy_to_lonlat(east, north)
            track.append((t, lon, lat, speed))
            t += point_step_m / (speed / 3.6)
        upward = not upward
        east += pass_spacing_m
    return track


def frange(start, stop, step):
    n = int(abs(stop - start) / abs(step)) + 1
    return [start + i * step for i in range(n)]


class SyntheticAreaTests(unittest.TestCase):
    """Cases whose correct answer is known by construction."""

    def test_full_square_is_one_hectare(self):
        """100x100 m worked in passes = 1.00 ha.

        Pass spacing is 5 m here on purpose, so the passes land exactly on
        0 and 100 m and the cloud spans the full square. Measured: 0.9875 ha,
        1.25 percent below the geometric extent. That shortfall is the alpha
        shape's boundary: the outermost triangles stop at the outermost
        points, so the swath of ground worked by the outer half of the
        implement is not counted. It is a property of the method, not an
        error, and it points the same way as the field result (mean -4.0
        percent against manual measurement).
        """
        contour = rectangle_contour(100.0, 100.0)
        track = shuttle_track(100.0, 100.0, pass_spacing_m=5.0)
        result = worked_area(track, contour, contour_id="square")
        self.assertAlmostEqual(result.area_ha, 1.0, delta=0.02,
                               msg="a fully worked 1 ha square must measure 1 ha")
        self.assertTrue(result.has_data)

    def test_unworked_middle_is_not_filled_in(self):
        """A skipped band must stay out of the area.

        This is the case a convex hull gets wrong, and it is the whole reason
        an alpha shape is used: a hull would report the field as complete.
        """
        contour = rectangle_contour(100.0, 100.0)
        worked = worked_area(shuttle_track(100.0, 100.0), contour)
        holed = worked_area(
            shuttle_track(100.0, 100.0, skip_band=(30.0, 60.0)), contour)
        self.assertLess(holed.area_ha, worked.area_ha * 0.80,
                        "a 30 m unworked band must cut the area, not vanish")
        # Negative control: the convex hull of the same points does NOT see it.
        from shapely.geometry import MultiPoint
        pts = [to_utm(lon, lat) for _, lon, lat, _ in
               shuttle_track(100.0, 100.0, skip_band=(30.0, 60.0))]
        hull_ha = MultiPoint(pts).convex_hull.intersection(contour).area / 10000.0
        self.assertGreater(hull_ha, holed.area_ha * 1.25,
                           "if the hull did not overstate this, the test could "
                           "not tell an alpha shape from a hull at all")

    def test_gap_in_the_track_is_not_bridged(self):
        """Two patches far apart must not be joined into one field."""
        contour = rectangle_contour(300.0, 100.0, margin_m=30.0)
        left = shuttle_track(60.0, 100.0)
        right = shuttle_track(60.0, 100.0, start_time=100000)
        shifted = [(t, lon + 240.0 / M_PER_DEG_LON, lat, sp)
                   for t, lon, lat, sp in right]
        both = worked_area(left + shifted, contour)
        alone = worked_area(left, contour)
        self.assertLess(both.area_ha, alone.area_ha * 2.35,
                        "the 240 m empty strip between the patches must not "
                        "be counted as worked")

    def test_driving_through_produces_no_area(self):
        """A single fast pass is passage, not work.

        The speed is the literal 30 km/h, NOT SPEED_MAX_KMH + something: a
        test written against the constant moves with it, so widening the
        window would leave this test still passing. It was written that way
        first, and a mutation run caught it.
        """
        contour = rectangle_contour(100.0, 100.0)
        track = [(i * 10, *xy_to_lonlat(0.0, i * 50.0), 30.0) for i in range(20)]
        self.assertGreater(30.0, SPEED_MAX_KMH, "30 km/h must be above the window")
        result = worked_area(track, contour)
        self.assertEqual(result.area_ha, 0.0)
        self.assertEqual(result.quality.points_used, 0,
                         "points above the work speed window must be dropped")

    def test_result_never_extends_beyond_the_contour(self):
        """The clip is what keeps a bridged notch out of the bill.

        The contour has a 3 m notch cut 80 m into it -- a ditch, a pole line,
        a neighbour's strip. No pass falls inside it, so the alpha shape
        bridges it from the passes on either side 5 m apart, and without the
        final clip that ground would be billed. Compare with a plain
        rectangle: the notch must actually cost area, otherwise this test
        would pass on a shape that never reached the notch at all.
        """
        outline = [(0.0, 0.0), (100.0, 0.0), (100.0, 100.0), (49.0, 100.0),
                   (49.0, 20.0), (46.0, 20.0), (46.0, 100.0), (0.0, 100.0)]
        notched = polygon_from_wialon(
            [{"x": lon, "y": lat}
             for lon, lat in (xy_to_lonlat(e, n) for e, n in outline)])
        track = shuttle_track(100.0, 100.0, pass_spacing_m=5.0)
        result = worked_area(track, notched)
        self.assertIsNotNone(result.polygon)
        self.assertTrue(notched.buffer(0.1).contains(result.polygon),
                        "the measured polygon must not leave the contour")
        plain = worked_area(track, rectangle_contour(100.0, 100.0))
        self.assertLess(result.area_ha, plain.area_ha - 0.015,
                        "the notch must actually be excluded from the area")

    def test_one_straight_pass_has_no_area(self):
        """A degenerate cloud must return zero, not crash."""
        contour = rectangle_contour(100.0, 100.0)
        track = [(i * 10, *xy_to_lonlat(50.0, i * 5.0), 8.0) for i in range(20)]
        result = worked_area(track, contour)
        self.assertEqual(result.area_ha, 0.0)

    def test_points_outside_the_contour_are_ignored(self):
        """Work on the neighbouring field must not be billed to this one."""
        contour = rectangle_contour(100.0, 100.0)
        inside = shuttle_track(100.0, 100.0)
        outside = [(t + 50000, lon + 500.0 / M_PER_DEG_LON, lat, sp)
                   for t, lon, lat, sp in shuttle_track(100.0, 100.0)]
        only_inside = worked_area(inside, contour)
        with_neighbour = worked_area(inside + outside, contour)
        self.assertAlmostEqual(with_neighbour.area_ha, only_inside.area_ha,
                               delta=0.01)

    def test_empty_track_is_no_data_not_zero_work(self):
        contour = rectangle_contour(100.0, 100.0)
        result = worked_area([], contour)
        self.assertFalse(result.has_data)
        self.assertIsNone(result.polygon)


class DensifyTests(unittest.TestCase):

    def test_short_segment_is_filled(self):
        out = densify([(0.0, 0.0), (20.0, 0.0)], step_m=5.0)
        self.assertEqual(len(out), 5)          # 0, 5, 10, 15 and the endpoint

    def test_long_segment_is_left_alone(self):
        """A gap of DENSIFY_MAX_SEG_M or more is missing data, not a path.

        Both real endpoints are kept -- they are measurements. What must not
        appear is anything between them: interpolating across a track gap
        would invent work over ground nothing was recorded on.
        """
        far = DENSIFY_MAX_SEG_M + 10.0
        out = densify([(0.0, 0.0), (far, 0.0)], step_m=5.0)
        self.assertEqual(out, [(0.0, 0.0), (far, 0.0)],
                         "no point may be invented inside a track gap")


class AlphaShapeTests(unittest.TestCase):

    def test_too_few_points(self):
        self.assertIsNone(alpha_shape([(0.0, 0.0), (1.0, 0.0), (0.0, 1.0)]))

    def test_scattered_points_give_no_shape(self):
        """Points farther apart than alpha cannot form any triangle."""
        far = ALPHA_M * 20
        pts = [(0.0, 0.0), (far, 0.0), (0.0, far), (far, far)]
        self.assertIsNone(alpha_shape(pts))


class QualityMetricTests(unittest.TestCase):
    """The gap metric, on synthetic input and on the tracks that broke it."""

    def test_parking_is_not_a_gap(self):
        """A machine reporting every 30 min while parked is not a loss."""
        ts = [0.0, 30.0, 60.0]
        ts += [60.0 + 1800.0 * i for i in range(1, 5)]   # parked, 30 min apart
        speeds = [8.0, 8.0, 0.0, 0.0, 0.0, 0.0, 0.0]
        quality = track_quality(ts, speeds)
        self.assertEqual(quality.motion_gaps, 0,
                         "stationary messages 30 min apart are how the tracker "
                         "is configured to behave when parked")

    def test_silence_while_moving_is_a_gap(self):
        silence = MOTION_GAP_SECONDS + 60.0        # one gap, this long
        ts = [0.0, 30.0, 30.0 + silence, 60.0 + silence]
        speeds = [8.0, 8.0, 8.0, 8.0]
        quality = track_quality(ts, speeds)
        self.assertEqual(quality.motion_gaps, 1)
        self.assertAlmostEqual(quality.lost_seconds, silence, places=3)

    def test_real_tracks_the_naive_metric_got_wrong(self):
        """Regression on two real tracks, with the naive count as control.

        Unit 3068 on 2026-07-23: counting every message pair gives 26 gaps --
        the figure that once made a healthy track look broken. All 26 are
        parking. Unit 7242 on 2026-07-19 has 3 genuine losses, and they must
        survive: a metric that simply returned zero would pass the first case
        and fail here.
        """
        by_unit = {}
        path = os.path.join(FIXTURES, "quality_tracks.csv")
        with open(path, encoding="utf-8") as fh:
            for row in csv.DictReader(fh, delimiter=";"):
                hh, mm, ss = row["time"].split(":")
                by_unit.setdefault(row["unit_id"], []).append(
                    (int(hh) * 3600 + int(mm) * 60 + int(ss), float(row["speed"])))

        expected = {"3068": (26, 0), "7242": (5, 3)}
        for unit, (naive_expected, correct_expected) in expected.items():
            rows = by_unit[unit]
            ts = [t for t, _ in rows]
            naive = sum(1 for a, b in zip(ts[:-1], ts[1:])
                        if b - a > MOTION_GAP_SECONDS)
            self.assertEqual(naive, naive_expected,
                             "fixture changed: the control count moved")
            quality = track_quality(ts, [s for _, s in rows])
            self.assertEqual(quality.motion_gaps, correct_expected,
                             f"unit {unit}: gap count regressed")


class RealFixtureTests(unittest.TestCase):
    """Reproduce the recorded figure of the verification set.

    Zone 3208 is the duplicate-name case: "1508 Нурхон Бобохон" exists twice,
    and the exact-name lookup chose zone 3207, which the tractor never
    entered. Identified by track, the work measures 8.51 ha against the
    owner's manual 8.587 -- a deviation of -0.9 percent.

    The track fixture is trimmed to the neighbourhood of the contour to keep
    the repository small. Points outside a contour cannot be inside it, so the
    area is unchanged by the trim -- but the QUALITY figures of this fixture
    are not meaningful, which is why they are asserted on quality_tracks.csv
    instead.
    """

    def setUp(self):
        with open(os.path.join(FIXTURES, "zone_3208.json"), encoding="utf-8") as fh:
            self.zone = json.load(fh)
        self.contour = polygon_from_wialon(self.zone["points"])
        self.track = []
        with open(os.path.join(FIXTURES, "track_3464_20260727.csv"),
                  encoding="utf-8") as fh:
            for row in csv.DictReader(fh, delimiter=";"):
                hh, mm, ss = row["time"].split(":")
                self.track.append((int(hh) * 3600 + int(mm) * 60 + int(ss),
                                   float(row["lon"]), float(row["lat"]),
                                   float(row["speed"])))

    def test_contour_area_from_geometry(self):
        self.assertAlmostEqual(self.contour.area / 10000.0, 8.97, delta=0.01)

    def test_recorded_worked_area_is_reproduced(self):
        result = worked_area(self.track, self.contour, contour_id=3208)
        self.assertAlmostEqual(result.area_ha, 8.51, delta=0.01,
                               msg="the recorded verification figure moved")
        self.assertEqual(result.quality.points_used, 939,
                         "the point count behind the figure moved")

    def test_contour_is_found_by_track(self):
        found = candidate_contours(self.track, {3208: self.contour})
        self.assertEqual(found, [(3208, 939)])

    def test_wrong_contour_yields_nothing(self):
        """Negative control for identification by track.

        Zone 3207 carries the SAME name and is the one an exact-name lookup
        picks. The tractor was never in it, so it must produce no work at all
        -- otherwise identification by track would be no better than by name.
        """
        moved = [{"x": p["x"] + 0.02, "y": p["y"] + 0.02}
                 for p in self.zone["points"]]
        elsewhere = polygon_from_wialon(moved)
        self.assertEqual(candidate_contours(self.track, {9999: elsewhere}), [])
        self.assertEqual(worked_area(self.track, elsewhere).area_ha, 0.0)


class JointWorkTests(unittest.TestCase):
    """Two machines on one contour: bill the sum, but show the overlap."""

    def test_separate_halves_have_no_overlap(self):
        contour = rectangle_contour(200.0, 100.0, margin_m=30.0)
        left = worked_area(shuttle_track(90.0, 100.0), contour)
        right_track = [(t, lon + 110.0 / M_PER_DEG_LON, lat, sp)
                       for t, lon, lat, sp in shuttle_track(90.0, 100.0)]
        right = worked_area(right_track, contour)
        check = joint_work_check([left, right])
        self.assertEqual(check.machines, 2)
        self.assertFalse(check.has_overlap)
        self.assertAlmostEqual(check.sum_ha, check.union_ha, delta=0.01)

    def test_same_ground_twice_is_flagged(self):
        """Double-counting is what the safeguard exists to make visible."""
        contour = rectangle_contour(100.0, 100.0)
        first = worked_area(shuttle_track(100.0, 100.0), contour)
        second = worked_area(shuttle_track(100.0, 100.0, start_time=90000), contour)
        check = joint_work_check([first, second])
        self.assertTrue(check.has_overlap)
        self.assertAlmostEqual(check.overlap_ha, first.area_ha, delta=0.05,
                               msg="both machines covered the same field")


if __name__ == "__main__":
    unittest.main()
