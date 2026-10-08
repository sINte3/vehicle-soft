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
import unittest.mock

import gps.area as area
from gps.area import (ALPHA_M, ALPHA_SPACING_FACTOR, DENSIFY_MAX_SEG_M,
                      METHOD_VERSION, MOTION_GAP_SECONDS,
                      PREVIOUS_METHOD_VERSION, SPACING_CAP_M, SPEED_MAX_KMH,
                      WIDEST_VALIDATED_SPACING_M, alpha_shape,
                      candidate_contours, densify, joint_work_check,
                      pass_spacing, pass_spacing_on_overflow, pass_votes,
                      polygon_from_wialon, return_share, to_utm,
                      track_quality, work_sites, worked_area)

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
        # [REASON]: the same north grid on every pass, merely reversed. Built
        # from two independent ranges the points end up staggered by half a
        # step, and then the nearest point on the pass alongside is not the
        # pass spacing at all -- which made a synthetic field say 12 m where
        # it was built with 6.
        grid = frange(0.0, height_m, point_step_m)
        if grid[-1] < height_m - 1e-9:
            # the machine drives to the end of the pass, so the far end is a
            # point too; without it the field is short by up to one step
            grid = grid + [height_m]
        norths = grid if upward else list(reversed(grid))
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


class PassSpacingTests(unittest.TestCase):
    """The estimator that drives the adaptive alpha."""

    def _spacing_of(self, spacing_m, field_m=300.0):
        track = shuttle_track(field_m, field_m, pass_spacing_m=spacing_m)
        pts = [to_utm(lon, lat) for _, lon, lat, _ in track]
        return pass_spacing([(float(x), float(y)) for x, y in pts])

    def test_recovers_the_spacing_it_was_built_with(self):
        for built in (5.0, 6.0, 14.0, 20.0):
            got = self._spacing_of(built)
            self.assertIsNotNone(got, f"no spacing found for {built} m")
            self.assertAlmostEqual(got, built, delta=0.6,
                                   msg=f"built {built} m, measured {got}")

    def test_single_straight_run_has_no_spacing(self):
        """One pass is not a pattern; the caller must fall back, not guess."""
        line = [(float(i) * 40.0, 0.0) for i in range(40)]
        self.assertIsNone(pass_spacing(line))

    def test_too_few_points(self):
        self.assertIsNone(pass_spacing([(0.0, 0.0), (5.0, 0.0)]))


class ReturnShareTests(unittest.TestCase):
    """The measurement itself, on shapes whose answer is known by construction.

    These tests exist because the feature FAILED as a work/transit classifier
    on the labelled corpus (roadmap section 2.8), and a failed hypothesis is
    only worth recording if the instrument was working. The last test is the
    interesting one: it shows the confusion that killed the idea.
    """

    def test_single_straight_run_never_returns(self):
        line = [(float(i) * 10.0, 0.0) for i in range(100)]
        self.assertEqual(return_share(line), 0.0)

    def test_parallel_passes_return_almost_everywhere(self):
        track = shuttle_track(200.0, 200.0, pass_spacing_m=10.0)
        pts = [to_utm(lon, lat) for _, lon, lat, _ in track]
        share = return_share([(float(x), float(y)) for x, y in pts])
        self.assertGreater(share, 0.9, "worked ground must be covered twice over")

    def test_too_few_points(self):
        self.assertIsNone(return_share([(0.0, 0.0), (5.0, 0.0)]))

    def test_the_same_road_there_and_back_looks_like_work(self):
        """Why the feature does not separate: a transit driven twice scores 1.

        A machine that goes out along a road and comes back along the same road
        has, for every point, a neighbour it reached only after a long detour --
        exactly the signature of a second pass. On the labelled corpus this is
        not a corner case: the transits scored HIGHER than the works
        (median 0.98 against 0.96).
        """
        out = [(float(i) * 10.0, 0.0) for i in range(60)]
        back = [(x, 6.0) for x, _ in reversed(out)]
        # 0.88 in fact: only the points either side of the turnaround miss out,
        # because there the return is nearer than 150 m along the track.
        self.assertGreater(return_share(out + back), 0.85)


class AdaptiveAlphaTests(unittest.TestCase):
    """Introduced 2026-08-12 after an unseen set confirmed the rule."""

    def test_cultivation_spacing_leaves_alpha_untouched(self):
        """max(10; 1.2 x 6) is 10 -- the rule must be a no-op here.

        This is what makes the change safe: the July calibration, all of it
        cultivation, replays byte for byte.
        """
        contour = rectangle_contour(300.0, 300.0)
        track = shuttle_track(300.0, 300.0, pass_spacing_m=6.0)
        adaptive = worked_area(track, contour)
        fixed = worked_area(track, contour, alpha_m=ALPHA_M)
        self.assertAlmostEqual(adaptive.pass_spacing_m, 6.0, delta=0.6)
        self.assertEqual(adaptive.alpha_used_m, ALPHA_M)
        self.assertAlmostEqual(adaptive.area_ha, fixed.area_ha, places=6)

    def test_sprayer_spacing_raises_alpha(self):
        """14 m spacing x the 1.2 margin is 16.8 m.

        Written as the literal 16.8, NOT as ALPHA_SPACING_FACTOR * 14: an
        expectation spelled with the constant moves when the constant moves,
        and a mutation run caught exactly that -- dropping the margin to 1.0
        failed nothing.
        """
        contour = rectangle_contour(300.0, 300.0)
        track = shuttle_track(300.0, 300.0, pass_spacing_m=14.0)
        result = worked_area(track, contour)
        self.assertAlmostEqual(result.pass_spacing_m, 14.0, delta=0.6)
        self.assertAlmostEqual(result.alpha_used_m, 16.8, delta=0.8)
        self.assertGreater(ALPHA_SPACING_FACTOR, 1.0,
                           "the margin must exceed the spacing, not equal it")

    def test_alpha_and_spacing_are_recorded_on_the_result(self):
        """A hectare that reaches an invoice must be reproducible later."""
        contour = rectangle_contour(300.0, 300.0)
        result = worked_area(shuttle_track(300.0, 300.0, pass_spacing_m=14.0),
                             contour)
        self.assertGreater(result.alpha_used_m, ALPHA_M)
        self.assertIsNotNone(result.pass_spacing_m)
        self.assertEqual(result.method_version, "overflow-cap-2026-10-07")

    def test_hole_survives_the_adaptive_rule(self):
        """The danger of the rule, guarded explicitly.

        An unworked band is a large distance between the passes bracketing
        it. If the estimator let that raise alpha, the shape would bridge the
        band and bill ground nobody touched -- the exact failure the alpha
        shape exists to prevent. Measuring the spacing as the MEDIAN over the
        whole cloud keeps a single wide gap from moving it.
        """
        contour = rectangle_contour(300.0, 300.0)
        full = worked_area(shuttle_track(300.0, 300.0, pass_spacing_m=6.0),
                           contour)
        holed = worked_area(
            shuttle_track(300.0, 300.0, pass_spacing_m=6.0,
                          skip_band=(90.0, 180.0)), contour)
        self.assertAlmostEqual(holed.pass_spacing_m, 6.0, delta=0.6,
                               msg="the hole must not inflate the spacing")
        self.assertLess(holed.area_ha, full.area_ha * 0.75,
                        "a 90 m unworked band must stay out of the area")


def slow_loop(corners_m, loops=1, point_step_m=100.0, speed=12.0,
              start_time=0):
    """A machine driving a closed route slowly -- inside the work window.

    100 m between points is a tracker writing every 30 s at 12 km/h. Each
    lap starts again at the first corner, the way a machine shuttling between
    the same two places does.
    """
    track, t = [], start_time
    for _ in range(loops):
        for (ea, na), (eb, nb) in zip(corners_m, corners_m[1:]):
            length = math.hypot(eb - ea, nb - na)
            steps = max(1, int(length // point_step_m))
            for i in range(steps):
                f = i / steps
                lon, lat = xy_to_lonlat(ea + f * (eb - ea), na + f * (nb - na))
                track.append((t, lon, lat, speed))
                t += point_step_m / (speed / 3.6)
    return track


# Two straight roads 2 km long and 300 m apart, joined at both ends.
TWO_ROADS = [(0.0, 0.0), (2000.0, 0.0), (2000.0, 300.0), (0.0, 300.0),
             (0.0, 0.0)]


class SlowRoadsTests(unittest.TestCase):
    """A7, 2026-10-02: why some sites cover villages, without implement width.

    The owner rejected the implement width as a notion: which implement was
    used and how wide it is, nobody knows. The session's hypothesis puts the
    cause in the method instead -- the pass spacing is measured over ALL slow
    points of the day, so on a day of slow road driving the "pass alongside"
    is the next road, and alpha = 1.2 x spacing stitches everything between
    the roads. These two tests pin the mechanism on the engine itself.
    """

    def test_the_previous_method_measured_the_roads_distance_as_the_spacing(self):
        """What the method did until 2026-10-07: 300 m -> alpha 360 m -> 60 ha.

        The characterisation of the defect A7 removed, kept on the previous
        method (`overflow_cap=False`, which still reproduces it): a machine
        that never left the roads got the whole 2 km x 300 m between them as
        one work site.
        """
        track = slow_loop(TWO_ROADS)
        sites, _quality = work_sites(track, overflow_cap=False)
        self.assertEqual(len(sites), 1)
        self.assertAlmostEqual(sites[0].pass_spacing_m, 300.0, delta=1.0)
        self.assertAlmostEqual(sites[0].alpha_used_m, 360.0, delta=1.2)
        self.assertAlmostEqual(sites[0].area_ha, 60.0, delta=0.5)
        self.assertEqual(sites[0].method_version, "adaptive-alpha-2026-08-12")

    def test_field_work_on_the_same_day_keeps_the_field_spacing(self):
        """The control: where passes dominate, the median is the field's.

        A 300 x 300 m field worked in 6 m passes plus one lap of the roads:
        the spacing stays 6 m, alpha stays 10 m and the roads add nothing --
        the defect needs a day where slow road points outnumber the passes.
        """
        work = shuttle_track(300.0, 300.0, pass_spacing_m=6.0)
        roads = slow_loop([(e + 1000.0, n + 1000.0) for e, n in TWO_ROADS],
                          start_time=work[-1][0] + 600)
        sites, _quality = work_sites(work + roads)
        self.assertEqual(len(sites), 1)
        self.assertAlmostEqual(sites[0].pass_spacing_m, 6.0, delta=0.6)
        self.assertEqual(sites[0].alpha_used_m, ALPHA_M)
        self.assertAlmostEqual(sites[0].area_ha, 9.0, delta=0.3)

    def test_slow_roads_alone_are_not_a_work_site(self):
        """The target A7 was held to: no field, no hectares.

        Set on 2026-10-02 as an expected failure (60 ha, see above); met by
        the method itself since 2026-10-07, when the owner adopted the rule --
        the default, with no switch passed.
        """
        sites, _quality = work_sites(slow_loop(TWO_ROADS))
        self.assertEqual([site.area_ha for site in sites], [])


def work_points(track):
    """The work-window points work_sites hands to the spacing estimator."""
    xs, ys = to_utm([r[1] for r in track], [r[2] for r in track])
    return [(float(x), float(y)) for x, y, r in zip(xs, ys, track)
            if 1.0 <= r[3] <= SPEED_MAX_KMH]


class OverflowCapTests(unittest.TestCase):
    """A7 rule, pre-registered 2026-10-02 (roadmap 2.11), the method since 07.10.

    These tests hold what the rule promises by construction -- untouched below
    the cap, never more hectares, roads alone give nothing -- on the engine
    itself. «Today» in them is the PREVIOUS method, asked for explicitly with
    `overflow_cap=False`: compared with the default, the rule would only be
    compared with itself and every such test would pass on any code.
    """

    def test_the_rule_is_the_method_and_the_switch_reproduces_the_previous(self):
        """Default = the rule; False = the previous method, labelled as such."""
        track = slow_loop(TWO_ROADS)
        self.assertEqual(work_sites(track)[0], [])
        self.assertEqual(work_sites(track, overflow_cap=True)[0], [])
        previous, _ = work_sites(track, overflow_cap=False)
        self.assertEqual(len(previous), 1)
        contour = rectangle_contour(2000.0, 300.0, margin_m=50.0)
        self.assertEqual(worked_area(track, contour).method_version,
                         "overflow-cap-2026-10-07")
        self.assertEqual(worked_area(track, contour, overflow_cap=False)
                         .method_version, "adaptive-alpha-2026-08-12")
        self.assertEqual(METHOD_VERSION, "overflow-cap-2026-10-07")
        self.assertEqual(PREVIOUS_METHOD_VERSION, "adaptive-alpha-2026-08-12")

    def test_a_pinned_alpha_is_labelled_by_the_alpha_not_by_a_rule(self):
        """alpha_m runs neither adaptive method; its result must not pass for one."""
        track = shuttle_track(300.0, 300.0, pass_spacing_m=14.0)
        contour = rectangle_contour(300.0, 300.0)
        for cap in (True, False):
            fixed = worked_area(track, contour, alpha_m=ALPHA_M, overflow_cap=cap)
            self.assertEqual(fixed.method_version, "fixed-alpha-10m")
            self.assertIsNone(fixed.pass_spacing_m)
            sites, _ = work_sites(track, alpha_m=12.5, overflow_cap=cap)
            self.assertEqual({site.method_version for site in sites},
                             {"fixed-alpha-12.5m"})

    def test_the_cap_is_the_widest_validated_spacing_with_the_margin(self):
        """Literals on purpose: an expectation spelled with the constant
        moves when the constant moves."""
        self.assertEqual(WIDEST_VALIDATED_SPACING_M, 37.2)
        self.assertAlmostEqual(SPACING_CAP_M, 44.64, places=9)

    def test_the_votes_are_what_todays_spacing_takes_the_median_of(self):
        for spacing_m in (6.0, 14.0, 37.2):
            points = work_points(shuttle_track(300.0, 300.0,
                                               pass_spacing_m=spacing_m))
            votes = pass_votes(points)
            self.assertTrue(votes)
            ordered = sorted(votes)
            middle = len(ordered) // 2
            median = (ordered[middle] if len(ordered) % 2
                      else (ordered[middle - 1] + ordered[middle]) / 2.0)
            self.assertAlmostEqual(pass_spacing(points), median, places=9)
        self.assertEqual(pass_votes([(0.0, 0.0)] * 5), [])

    def test_below_the_cap_a_day_is_bit_identical_to_today(self):
        """Every hand-validated work has spacing <= 37.2 m: nothing moves."""
        contour = rectangle_contour(300.0, 300.0)
        for spacing_m in (6.0, 14.0, 37.2):
            track = shuttle_track(300.0, 300.0, pass_spacing_m=spacing_m)
            today, _ = work_sites(track, overflow_cap=False)
            capped, _ = work_sites(track, overflow_cap=True)
            self.assertEqual([(s.area_ha, s.alpha_used_m, s.pass_spacing_m,
                               s.polygon.wkb) for s in today],
                             [(s.area_ha, s.alpha_used_m, s.pass_spacing_m,
                               s.polygon.wkb) for s in capped], spacing_m)
            one = worked_area(track, contour, overflow_cap=False)
            two = worked_area(track, contour, overflow_cap=True)
            self.assertEqual((one.area_ha, one.alpha_used_m, one.pass_spacing_m),
                             (two.area_ha, two.alpha_used_m, two.pass_spacing_m))

    def test_slow_roads_alone_give_nothing_under_the_cap(self):
        """The A7 target, met by the rule: 60 ha today, none with the cap."""
        points = work_points(slow_loop(TWO_ROADS))
        self.assertAlmostEqual(pass_spacing(points), 300.0, delta=1.0)
        self.assertIsNone(pass_spacing_on_overflow(points))
        sites, _quality = work_sites(slow_loop(TWO_ROADS), overflow_cap=True)
        self.assertEqual([site.area_ha for site in sites], [])

    def test_on_a_road_dominated_day_the_field_keeps_its_own_spacing(self):
        """A 100 x 300 m field and six slow laps of the roads 1 km away.

        Today the road points outvote the passes, alpha balloons and the
        roads become a site of their own; under the cap the field is measured
        as if the roads were not there.
        """
        field = shuttle_track(100.0, 300.0, pass_spacing_m=6.0,
                              point_step_m=100.0)
        roads = slow_loop([(e + 1000.0, n + 1000.0) for e, n in TWO_ROADS],
                          loops=6, start_time=field[-1][0] + 600)
        alone, _ = work_sites(field)
        today, _ = work_sites(field + roads, overflow_cap=False)
        capped, _ = work_sites(field + roads, overflow_cap=True)
        self.assertGreater(today[0].alpha_used_m, 44.64)
        self.assertGreater(sum(s.area_ha for s in today),
                           sum(s.area_ha for s in alone) + 1.0)
        self.assertEqual(len(capped), 1)
        self.assertEqual(capped[0].alpha_used_m, ALPHA_M)
        self.assertAlmostEqual(capped[0].pass_spacing_m, 6.0, delta=0.6)
        self.assertAlmostEqual(capped[0].area_ha, alone[0].area_ha, delta=0.01)

    def test_the_cap_never_adds_alpha_or_hectares(self):
        days = [shuttle_track(300.0, 300.0, pass_spacing_m=s)
                for s in (6.0, 14.0, 37.2)]
        days.append(slow_loop(TWO_ROADS))
        days.append(shuttle_track(100.0, 300.0, pass_spacing_m=6.0,
                                  point_step_m=100.0)
                    + slow_loop(TWO_ROADS, loops=3, start_time=10 ** 5))
        for index, track in enumerate(days):
            today, _ = work_sites(track, overflow_cap=False)
            capped, _ = work_sites(track, overflow_cap=True)
            self.assertLessEqual(sum(s.area_ha for s in capped),
                                 sum(s.area_ha for s in today) + 1e-9, index)
            if capped and today:
                self.assertLessEqual(capped[0].alpha_used_m,
                                     today[0].alpha_used_m, index)
                self.assertLessEqual(capped[0].alpha_used_m, 53.568 + 1e-9)

    def test_the_contour_path_takes_the_switch_too(self):
        """worked_area -- the per-contour half of condition 1 -- obeys the cap.

        A contour drawn around the two roads: today the roads 300 m apart are
        the "passes" and the contour fills; with the cap no pass alongside is
        left, the spacing is gone and alpha falls back to the fixed 10 m.
        """
        track = slow_loop(TWO_ROADS)
        contour = rectangle_contour(2000.0, 300.0, margin_m=50.0)
        today = worked_area(track, contour, overflow_cap=False)
        capped = worked_area(track, contour, overflow_cap=True)
        self.assertGreater(today.alpha_used_m, 300.0)
        self.assertAlmostEqual(today.area_ha, 60.0, delta=0.5)
        self.assertIsNone(capped.pass_spacing_m)
        self.assertEqual(capped.alpha_used_m, ALPHA_M)
        self.assertLess(capped.area_ha, 0.3)

    def votes_give(self, votes):
        with unittest.mock.patch.object(area, 'pass_votes', return_value=votes):
            return pass_spacing_on_overflow([(0.0, 0.0)] * 30)

    def test_a_median_exactly_at_the_cap_is_not_an_overflow(self):
        """`today <= cap`, not `<`: the boundary belongs to today's answer.

        Votes 20 m (40), exactly the cap (40) and 300 m (30): the median is
        the cap itself. Treated as overflow, the median of the votes up to
        the cap would be (20 + cap) / 2 -- the boundary would move the day.
        """
        votes = [20.0] * 40 + [SPACING_CAP_M] * 40 + [300.0] * 30
        self.assertEqual(self.votes_give(votes), SPACING_CAP_M)

    def test_on_overflow_a_vote_exactly_at_the_cap_is_kept(self):
        """`vote <= cap`, not `<`: a pass exactly 44.64 m away still counts.

        Ten votes at the cap, thirty roads at 300 m: the day overflows and the
        ten survive. With a strict filter nothing would, and alpha would drop
        to the fixed 10 m.
        """
        self.assertEqual(self.votes_give([SPACING_CAP_M] * 10 + [300.0] * 30),
                         SPACING_CAP_M)
        just_above = math.nextafter(SPACING_CAP_M, math.inf)
        self.assertIsNone(self.votes_give([just_above] * 10 + [300.0] * 30))

    def test_on_overflow_the_kept_votes_give_their_median(self):
        """Step 3 says median: not the least, not the mean, not a quartile.

        A sprayer day with refill trips: retraces of one road (3 m), the
        field (14 m) and roads far apart (300 m). The median of the kept
        votes is the field's 14 m; the least would be 3, the mean 9.29, the
        lower quartile 3 -- each a different alpha and a different area.
        """
        votes = [3.0] * 30 + [14.0] * 40 + [300.0] * 100
        self.assertEqual(self.votes_give(votes), 14.0)
        self.assertEqual(self.votes_give([5.0, 7.0, 9.0, 40.0] + [300.0] * 10),
                         8.0)

    def test_on_overflow_there_is_no_other_threshold(self):
        """2.11, step 3: «других порогов нет» -- no floor, no minimum share.

        Retraces outnumber the field among the kept votes: the median is
        then the retraces' 3 m, as the rule is written. A floor that drops
        short votes would answer 14 m; a minimum count of kept votes would
        answer None for the two votes below.
        """
        self.assertEqual(self.votes_give([3.0] * 30 + [14.0] * 20
                                         + [300.0] * 100), 3.0)
        self.assertEqual(self.votes_give([20.0, 30.0] + [300.0] * 100), 25.0)

    def test_on_overflow_alpha_keeps_the_margin(self):
        """Alpha = 1.2 x the kept spacing, whenever that beats the 10 m floor.

        A field in 20 m passes and six laps of the roads 1 km away: today the
        roads give 100 m and alpha 120 m; with the cap the field keeps its own
        20 m, and alpha is 24 m -- not 20, not the 10 m floor.
        """
        field = shuttle_track(300.0, 300.0, pass_spacing_m=20.0,
                              point_step_m=100.0)
        roads = slow_loop([(e + 1000.0, n + 1000.0) for e, n in TWO_ROADS],
                          loops=6, start_time=field[-1][0] + 600)
        today, _ = work_sites(field + roads, overflow_cap=False)
        capped, _ = work_sites(field + roads, overflow_cap=True)
        self.assertGreater(today[0].pass_spacing_m, SPACING_CAP_M)
        self.assertAlmostEqual(capped[0].pass_spacing_m, 20.0, delta=0.5)
        self.assertEqual(capped[0].alpha_used_m,
                         ALPHA_SPACING_FACTOR * capped[0].pass_spacing_m)

    def test_the_daily_computation_uses_the_rule_and_names_its_method(self):
        """The nightly computation is the rule; a row names the method made it.

        A day recomputed with the previous method (`overflow_cap=False`) must
        not be stored as the new one -- that is what makes a hectare in the
        database reproducible after the method changed.
        """
        from gps.daily import compute_day
        points = [(t, lon, lat, speed, 10)
                  for t, lon, lat, speed in slow_loop(TWO_ROADS)]
        today = compute_day(points)
        self.assertEqual(today.sites, [])
        self.assertEqual(today.aggregate["method_version"],
                         "overflow-cap-2026-10-07")
        previous = compute_day(points, overflow_cap=False)
        self.assertEqual(len(previous.sites), 1)
        self.assertEqual(previous.aggregate["method_version"],
                         "adaptive-alpha-2026-08-12")


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

    def test_overnight_park_is_not_a_gap(self):
        """Stopped, engine off, tracker asleep until morning -- nothing lost.

        The machine's last message before the silence shows speed 0 and the
        first one after it shows motion. Counting that as lost data claimed
        708 minutes on unit 1729 (05.08) whose every gap began at a standstill.
        """
        ts = [0.0, 30.0, 60.0, 60.0 + 6 * 3600.0, 60.0 + 6 * 3600.0 + 30.0]
        speeds = [8.0, 4.0, 0.0, 3.0, 7.0]
        quality = track_quality(ts, speeds)
        self.assertEqual(quality.motion_gaps, 0)
        self.assertEqual(quality.lost_seconds, 0.0)

    def test_silence_while_moving_is_a_gap(self):
        silence = MOTION_GAP_SECONDS + 60.0        # one gap, this long
        ts = [0.0, 30.0, 30.0 + silence, 60.0 + silence]
        speeds = [8.0, 8.0, 8.0, 8.0]
        quality = track_quality(ts, speeds)
        self.assertEqual(quality.motion_gaps, 1)
        self.assertAlmostEqual(quality.lost_seconds, silence, places=3)

    def test_impossible_positions_are_counted(self):
        """A tracker that briefly loses the sky reports a place it never was.

        Operators call it "shooting stars", and one such day (MTZ 572 HA,
        19.06) was described as hell to measure by hand. The signature is a
        step the machine could not have taken: 2 km in 30 s while the tracker
        itself reports 8 km/h. Compared against the REPORTED speed, not a
        fixed limit, so the test stays valid for a lorry as well as a tractor.
        """
        timestamps = [0.0, 30.0, 60.0, 90.0]
        speeds = [8.0, 8.0, 8.0, 8.0]
        clean = [(0.0, 0.0), (60.0, 0.0), (120.0, 0.0), (180.0, 0.0)]
        starred = [(0.0, 0.0), (60.0, 0.0), (2000.0, 0.0), (2060.0, 0.0)]
        self.assertEqual(track_quality(timestamps, speeds,
                                       points_xy=clean).gps_jumps, 0)
        noisy = track_quality(timestamps, speeds, points_xy=starred)
        self.assertEqual(noisy.gps_jumps, 1)
        self.assertAlmostEqual(noisy.jump_share, 1 / 3, places=3)

    def test_two_messages_one_second_apart_are_not_a_jump(self):
        """A timestamp artefact is not a teleport, and this is what the first
        version of the check got wrong: on MTZ 572 HA it reported 295
        impossible transitions where only 2 were real. At 9 km/h a machine
        covers 2.5 m per second, so a 30 m step recorded "in 1 s" says the
        timestamps collided, not that the machine flew.
        """
        timestamps = [0.0, 1.0, 2.0]
        speeds = [9.0, 9.0, 9.0]
        points = [(0.0, 0.0), (30.0, 0.0), (60.0, 0.0)]
        self.assertEqual(track_quality(timestamps, speeds,
                                       points_xy=points).gps_jumps, 0)

    def test_a_lorry_at_speed_is_not_a_jump(self):
        """90 km/h is impossible for a tractor and normal for a lorry.

        The check must not fire on a machine that honestly reports being fast,
        or every delivery run would be flagged as a broken tracker.
        """
        timestamps = [0.0, 30.0, 60.0]
        speeds = [90.0, 90.0, 90.0]
        points = [(0.0, 0.0), (750.0, 0.0), (1500.0, 0.0)]
        self.assertEqual(track_quality(timestamps, speeds,
                                       points_xy=points).gps_jumps, 0)

    def test_real_tracks_the_naive_metric_got_wrong(self):
        """Regression on three real tracks, with the naive count as control.

        Units 3068 (23.07) and 7242 (19.07): counting every message pair
        gives 26 and 5 gaps -- the figures that once made healthy tracks look
        broken. Every one of them is a machine standing still.

        Unit 1729 on 08.08 is the opposite case and the reason this test can
        fail: of its 14 naive gaps exactly ONE began while the machine was
        moving, and that one is real lost data. A metric that simply returned
        zero would pass the first two lines and fail this one.
        """
        by_unit = {}
        path = os.path.join(FIXTURES, "quality_tracks.csv")
        with open(path, encoding="utf-8") as fh:
            for row in csv.DictReader(fh, delimiter=";"):
                hh, mm, ss = row["time"].split(":")
                by_unit.setdefault(row["unit_id"], []).append(
                    (int(hh) * 3600 + int(mm) * 60 + int(ss), float(row["speed"])))

        expected = {"3068": (26, 0), "7242": (5, 0), "1729": (14, 1)}
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


class WorkSiteTests(unittest.TestCase):
    """Measuring without a contour -- the primary path.

    Operators mostly do not register fields as geozones, so the method may not
    depend on one. These tests pin the behaviour that follows from that.
    """

    def test_two_fields_far_apart_are_two_sites(self):
        first = shuttle_track(200.0, 200.0, pass_spacing_m=6.0)
        second = [(t + 90000, lon + 2000.0 / M_PER_DEG_LON, lat, sp)
                  for t, lon, lat, sp in shuttle_track(150.0, 150.0,
                                                       pass_spacing_m=6.0)]
        sites, quality = work_sites(first + second)
        self.assertEqual(len(sites), 2)
        self.assertAlmostEqual(sites[0].area_ha, 4.0, delta=0.25)
        self.assertAlmostEqual(sites[1].area_ha, 2.25, delta=0.2)
        self.assertGreater(quality.points_used, 0)

    def test_work_without_any_geozone_is_still_measured(self):
        """The case that produced a silent zero before this existed."""
        sites, _ = work_sites(shuttle_track(200.0, 200.0, pass_spacing_m=6.0),
                              contours={})
        self.assertEqual(len(sites), 1)
        self.assertIsNone(sites[0].contour_id)
        self.assertAlmostEqual(sites[0].area_ha, 4.0, delta=0.25)

    def test_a_geozone_only_names_the_site_it_does_not_clip_it(self):
        """A contour covering a corner must not shrink the measured area."""
        track = shuttle_track(200.0, 200.0, pass_spacing_m=6.0)
        corner = rectangle_contour(60.0, 60.0, margin_m=0.0)
        sites, _ = work_sites(track, contours={"corner": corner})
        self.assertEqual(len(sites), 1)
        self.assertEqual(sites[0].contour_id, "corner")
        self.assertAlmostEqual(sites[0].area_ha, 4.0, delta=0.25,
                               msg="naming a site must not clip it")

    def test_small_patches_are_dropped_as_passage(self):
        track = shuttle_track(200.0, 200.0, pass_spacing_m=6.0)
        detour = [(t + 90000, lon + 2000.0 / M_PER_DEG_LON, lat, sp)
                  for t, lon, lat, sp in shuttle_track(30.0, 30.0,
                                                       pass_spacing_m=6.0)]
        sites, _ = work_sites(track + detour, min_area_ha=0.3)
        self.assertEqual(len(sites), 1, "0.09 ha is passage, not work")
        loose, _ = work_sites(track + detour, min_area_ha=0.05)
        self.assertEqual(len(loose), 2, "the floor must be the caller's call")

    def test_empty_track_yields_no_sites(self):
        sites, quality = work_sites([])
        self.assertEqual(sites, [])
        self.assertEqual(quality.points_used, 0)


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
