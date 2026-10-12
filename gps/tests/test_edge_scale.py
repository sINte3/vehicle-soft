# -*- coding: utf-8 -*-
"""gps/edge_scale.py -- round 2 of the edge-of-field rule (candidate, roadmap 2.13).

What is held here, and why each case:
  * the switch: off is the method in force bit for bit; the round-2 version
    names itself after the base method; True still means round 1 (its run of
    10-11.10 must stay reproducible); an unknown version is an error, never
    "off";
  * a clean field is left exactly as it is;
  * the two kinds of ground the rule removes, each with a control that today's
    alpha really glues it: a road driven out and back beside a cultivation
    field (an empty gap and a narrow strip), and an out-and-back 16 m wide
    beside a ploughed field (V-5 row 5's shape: an empty gap);
  * the four places it must NOT act, each with a control that shows the
    guard is what keeps the ground: a sprayer field (its step sets alpha), a
    sprayer field whose median vote a retraced road dragged down (the upper
    quartile), three loosener passes side by side driven in one visit (turns
    split the stretch into legs), two sprayer passes in a dozen messages (no
    step can be measured);
  * it only removes ground, and returns flat polygons;
  * the evidence: removed pieces add up to what left the shape, sites carry
    the engine's numbers, a failure falls back to today's shape and says so;
  * the two real days of the repository: New Holland 80 080 HA on 01.10.2026
    (the owner measured its two fields at 7.805 ha) and unit 3464 on 27.07;
  * the dense points are the engine's own, a repeated position is not a gap,
    determinism, degenerate input, a pinned alpha.
"""
import csv
import os
import unittest
import unittest.mock

import numpy as np
import shapely

from gps import area, edge_scale
from gps.area import (EDGE_RULE_VERSION, EDGE_SCALE_VERSION, METHOD_VERSION,
                      alpha_shape, densify, method_version, to_utm, work_sites)
from gps.daily import compute_day
from gps.tests.test_area import FIXTURES, shuttle_track, xy_to_lonlat
from gps.tests.test_edge import NH_OWNER_HA, field_and_road, nh_points, pieces, road, total

SCALE = EDGE_SCALE_VERSION


def utm_points(track):
    """The work-window points of a track in UTM, as `work_sites` takes them."""
    xs, ys = to_utm([r[1] for r in track], [r[2] for r in track])
    mask = area._moving_mask([r[3] for r in track])
    return [(float(x), float(y)) for x, y, m in zip(xs, ys, mask) if m]


def run_rule(track):
    """(today, spacing, alpha, shape, evidence) for a track, the engine's way."""
    xy = utm_points(track)
    spacing, alpha = area._adaptive_alpha(xy, True)
    today = alpha_shape(densify(xy), alpha)
    shape, evidence = edge_scale.trim(xy, today, alpha, spacing)
    return today, spacing, alpha, shape, evidence


def plough_and_wide_outback():
    """A ploughed field (2.3 m passes, 60 x 200 m) and a run beside it out and
    back 16 m apart -- the shape of V-5 row 5 (New Holland 80 156 CA, 05.09)."""
    plough = shuttle_track(60.0, 200.0, pass_spacing_m=2.3, point_step_m=20.0,
                           speed=6.0)
    run, _ = road([(-150.0, -10.0), (250.0, -10.0), (250.0, -26.0), (-150.0, -26.0)],
                  start_time=plough[-1][0] + 600.0)
    return plough, run


def sprayer(start_time=0.0):
    return shuttle_track(216.0, 300.0, pass_spacing_m=18.0, point_step_m=20.0,
                         speed=10.0, start_time=start_time)


def retraced_road(start_time):
    """A refill road driven eight times along the sprayer field's south edge,
    each time within a metre or so of the last -- votes of 1-3 m."""
    track, t = [], start_time
    for k in range(8):
        seg, t = road([(-30.0, -8.0 - 0.4 * k), (250.0, -8.0 - 0.4 * k),
                       (-30.0, -9.0 - 0.4 * k)], start_time=t + 300.0, seed=k,
                      noise_m=0.5)
        track += seg
    return track


class Switch(unittest.TestCase):
    def test_off_is_the_method_in_force_bit_for_bit(self):
        field, lane = field_and_road()
        plain, _ = work_sites(field + lane)
        for off in (False, None):
            sites, _ = work_sites(field + lane, edge_rule=off)
            self.assertEqual([(s.area_ha, s.polygon.wkb) for s in plain],
                             [(s.area_ha, s.polygon.wkb) for s in sites])
        self.assertTrue(all(s.method_version == METHOD_VERSION for s in plain))

    def test_round_2_is_named_after_the_base_method(self):
        self.assertEqual(SCALE, "edge-scale-2026-10-11")
        self.assertEqual(method_version(True, edge_rule=SCALE),
                         METHOD_VERSION + "+" + SCALE)
        self.assertEqual(method_version(False, edge_rule=SCALE),
                         area.PREVIOUS_METHOD_VERSION + "+" + SCALE)
        self.assertEqual(method_version(True, alpha_m=10.0, edge_rule=SCALE),
                         "fixed-alpha-10m+" + SCALE)
        field, lane = field_and_road()
        sites, _ = work_sites(field + lane, edge_rule=SCALE)
        self.assertTrue(sites)
        self.assertTrue(all(s.method_version == METHOD_VERSION + "+" + SCALE
                            for s in sites))

    def test_true_still_means_round_1(self):
        self.assertEqual(method_version(True, edge_rule=True),
                         METHOD_VERSION + "+" + EDGE_RULE_VERSION)
        self.assertEqual(method_version(True, edge_rule=EDGE_RULE_VERSION),
                         METHOD_VERSION + "+" + EDGE_RULE_VERSION)
        field, lane = field_and_road()
        one, _ = work_sites(field + lane, edge_rule=True)
        two, _ = work_sites(field + lane, edge_rule=EDGE_RULE_VERSION)
        self.assertEqual([s.polygon.wkb for s in one], [s.polygon.wkb for s in two])

    def test_an_unknown_rule_is_refused_not_ignored(self):
        field, lane = field_and_road()
        for wrong in ("edge-scale", "edge-scale-2026-10-12", 1, "yes"):
            with self.assertRaises(ValueError):
                method_version(True, edge_rule=wrong)
            with self.assertRaises(ValueError):
                work_sites(field + lane, edge_rule=wrong)

    def test_the_daily_computation_passes_it_through(self):
        field, lane = field_and_road()
        points = [(int(t), lon, lat, speed, 12) for t, lon, lat, speed in field + lane]
        plain = compute_day(points)
        trimmed = compute_day(points, edge_rule=SCALE)
        self.assertEqual(plain.aggregate["method_version"], METHOD_VERSION)
        self.assertEqual(trimmed.aggregate["method_version"],
                         METHOD_VERSION + "+" + SCALE)
        self.assertLess(sum(r["area_ha"] for r in trimmed.sites),
                        sum(r["area_ha"] for r in plain.sites) - 0.1)


class Removes(unittest.TestCase):
    def test_a_clean_field_is_left_as_it_is(self):
        field = shuttle_track(150.0, 300.0, pass_spacing_m=6.0, point_step_m=20.0)
        today, _ = work_sites(field)
        trimmed, _ = work_sites(field, edge_rule=SCALE)
        self.assertEqual([(s.area_ha, s.polygon.wkb) for s in today],
                         [(s.area_ha, s.polygon.wkb) for s in trimmed])

    def test_a_road_out_and_back_beside_a_cultivation_field(self):
        field, lane = field_and_road()
        alone = total(work_sites(field)[0])
        today = total(work_sites(field + lane)[0])
        trimmed = total(work_sites(field + lane, edge_rule=SCALE)[0])
        # control: today's alpha really glues the road to the field
        self.assertGreater(today - alone, 0.2)
        self.assertLess(abs(trimmed - alone) / alone, 0.02,
                        "the field must come back to (about) its own area")
        _, _, _, _, evidence = run_rule(field + lane)
        self.assertEqual({r["kind"] for r in evidence["removed"]}, {"gap", "strip"})

    def test_a_wide_out_and_back_beside_a_ploughed_field(self):
        plough, run = plough_and_wide_outback()
        alone = total(work_sites(plough)[0])
        today = total(work_sites(plough + run)[0])
        trimmed = total(work_sites(plough + run, edge_rule=SCALE)[0])
        # control: two lines 16 m apart are bridged by A7 into a strip
        self.assertGreater(today - alone, 0.5)
        self.assertLess(trimmed - alone, 0.1)
        self.assertGreaterEqual(trimmed, alone - 1e-6, "the field itself stays whole")

    def test_two_traversals_7_m_apart_are_still_a_road(self):
        # out and back 7 m apart, wider than a lane: two traversals prove
        # nothing either way, and the strip goes (three would be passes)
        plough = shuttle_track(60.0, 200.0, pass_spacing_m=2.3, point_step_m=20.0,
                               speed=6.0)
        run, _ = road([(-450.0, -8.0), (300.0, -8.0), (300.0, -15.0), (-450.0, -15.0)],
                      start_time=plough[-1][0] + 600.0)
        alone = total(work_sites(plough)[0])
        today = total(work_sites(plough + run)[0])
        trimmed = total(work_sites(plough + run, edge_rule=SCALE)[0])
        self.assertGreater(today - alone, 0.5)
        self.assertLess(trimmed - alone, 0.15)

    def test_only_removes_and_returns_flat_polygons(self):
        for track in (sum(field_and_road(), []), sum(plough_and_wide_outback(), [])):
            today, _, _, shape, _ = run_rule(track)
            self.assertIn(shape.geom_type, ("Polygon", "MultiPolygon"))
            self.assertLess(shape.difference(today).area, 1e-6)
            self.assertLess(shape.area, today.area)


class LeavesAlone(unittest.TestCase):
    def test_a_sprayer_field_its_step_sets_alpha(self):
        lane, _ = road([(-200.0, -15.0), (400.0, -15.0), (-200.0, -13.0)],
                       start_time=200000.0)
        today, _ = work_sites(sprayer() + lane)
        trimmed, _ = work_sites(sprayer() + lane, edge_rule=SCALE)
        self.assertEqual([s.polygon.wkb for s in today], [s.polygon.wkb for s in trimmed])
        *_, evidence = run_rule(sprayer() + lane)
        self.assertEqual([s["off"] for s in evidence["sites"]], ["step sets alpha"])

    def test_a_sprayer_field_whose_median_a_retraced_road_dragged_down(self):
        track = sprayer() + retraced_road(200000.0)
        today, spacing, _alpha, shape, evidence = run_rule(track)
        site = evidence["sites"][0]
        # control: the retraces pull both the day's spacing and the site's
        # median vote under the step switch -- only the upper quartile is left
        self.assertLess(spacing, 8.0)
        self.assertLess(site["median"], 8.0)
        self.assertGreaterEqual(site["p75"], area.ALPHA_M)
        self.assertEqual(site["off"], "wide-pass work")
        self.assertEqual(shape.wkb, today.wkb)
        # control: were the median the measure, the rule would act on it
        with unittest.mock.patch.object(edge_scale, "WIDE_WORK_PERCENTILE", 50.0):
            _shape, ev = edge_scale.trim(utm_points(track), today, _alpha, spacing)
        self.assertTrue(ev["sites"][0]["on"])

    def test_three_loosener_passes_side_by_side(self):
        strip = shuttle_track(9.0, 600.0, pass_spacing_m=4.5, point_step_m=20.0, speed=7.0)
        today, _ = work_sites(strip)
        self.assertAlmostEqual(total(today), 0.54, delta=0.01)
        trimmed, _ = work_sites(strip, edge_rule=SCALE)
        self.assertAlmostEqual(total(trimmed), total(today), delta=1e-6)
        *_, evidence = run_rule(strip)
        self.assertEqual(len(evidence["passes"]), 1)
        # control: one visit is ONE stretch inside the strip -- unsplit at the
        # turns, it looks like a single traversal and the strip goes as road
        with unittest.mock.patch.object(edge_scale, "TURN_BACK_COS", -2.0):
            gone, _ = work_sites(strip, edge_rule=SCALE)
        self.assertEqual(total(gone), 0.0)

    def test_two_sprayer_passes_in_a_dozen_messages(self):
        few = shuttle_track(18.0, 300.0, pass_spacing_m=18.0, point_step_m=60.0,
                            speed=10.0)
        xy = utm_points(few)
        self.assertLess(len(xy), area.PASS_SPACING_MIN_POINTS)
        today = alpha_shape(densify(xy), area.ALPHA_M)
        self.assertGreater(today.area / 1e4, area.MIN_WORK_AREA_HA)
        shape, evidence = edge_scale.trim(xy, today, area.ALPHA_M, None)
        self.assertEqual(evidence["sites"][0]["off"], "no step")
        self.assertEqual(shape.wkb, today.wkb)
        # control: with a soil-scale step given, the two passes would be an
        # empty gap and would go
        cut, _ = edge_scale.trim(xy, today, area.ALPHA_M, 4.0)
        self.assertLess(0.0 if cut is None else cut.area, 0.1 * today.area)

    def test_a_site_left_alone_keeps_its_road_where_a_soil_site_touches_it(self):
        # two sites of today touching at one point: a sprayer field with its
        # refill road (4 m wide, driven out and back for 300 m), left alone,
        # and a soil site at its corner. The road is the sprayer's own: the
        # soil site's zone meets the sprayer site at the corner, and that must
        # not put the sprayer's road under the soil site's rule.
        field = shapely.union_all([shapely.box(0, 0, 100, 100),
                                   shapely.box(100, 48, 400, 52)])
        soil = shapely.box(-100, -100, 0, 0)
        shape = shapely.MultiPolygon([field, soil])
        self.assertTrue(shape.is_valid)
        out_x = np.arange(100.0, 400.0 + 1e-9, area.DENSIFY_STEP_M)
        P = np.concatenate([np.column_stack([out_x, np.full(len(out_x), 49.0)]),
                            np.column_stack([out_x[::-1], np.full(len(out_x), 51.0)])])
        step = np.hypot(*np.diff(P, axis=0).T)
        along = np.concatenate(([0.0], np.cumsum(step)))
        stretch = np.zeros(len(P), int)
        sites = [{"site": 1, "zone": field.buffer(0.01), "on": False},
                 {"site": 2, "zone": soil.buffer(0.01), "on": True}]
        self.assertTrue(sites[1]["zone"].intersects(field), "the zones meet")
        removed, passes = edge_scale._strips(shape, P, along, stretch, sites)
        self.assertEqual((removed, passes), ([], []))
        # control: were the sprayer site's own rule on, its road would go
        sites[0]["on"] = True
        removed, _ = edge_scale._strips(shape, P, along, stretch, sites)
        self.assertEqual([site for _part, site in removed], [0])
        self.assertGreater(removed[0][0].area, 1000.0)

    def test_a_band_with_a_track_running_through_it_is_not_empty(self):
        # two passes 12 and 24 m beyond a ploughed field: the bands between
        # them are wider than the field's reach, but a pass runs through the
        # middle of the gap -- it is not empty ground, and it stays
        plough = shuttle_track(60.0, 200.0, pass_spacing_m=2.3, point_step_m=20.0,
                               speed=6.0)
        t = plough[-1][0]
        extra = []
        for x, up in ((72.0, True), (84.0, False)):
            norths = [20.0 * i for i in range(11)]
            for north in (norths if up else norths[::-1]):
                t += 12.0
                extra.append((t,) + xy_to_lonlat(x, north) + (6.0,))
        alone = total(work_sites(plough)[0])
        today, _ = work_sites(plough + extra)
        self.assertGreater(total(today) - alone, 0.4)
        trimmed, _ = work_sites(plough + extra, edge_rule=SCALE)
        self.assertEqual([s.polygon.wkb for s in today], [s.polygon.wkb for s in trimmed])

    def test_the_output_is_flattened(self):
        square = shapely.box(0, 0, 10, 10)
        nested = shapely.GeometryCollection([
            shapely.MultiPolygon([square, shapely.box(20, 0, 30, 10)]),
            shapely.LineString([(0, 0), (50, 50)]), shapely.Point(5, 5)])
        flat = edge_scale._flat(nested)
        self.assertEqual(flat.geom_type, "MultiPolygon")
        self.assertEqual(len(flat.geoms), 2)
        self.assertEqual(edge_scale._flat(square).wkb, square.wkb)
        self.assertIsNone(edge_scale._flat(shapely.LineString([(0, 0), (1, 1)])))


class Evidence(unittest.TestCase):
    def test_removed_pieces_add_up_to_what_left_the_shape(self):
        for track in (sum(field_and_road(), []), sum(plough_and_wide_outback(), [])):
            today, _, _, shape, evidence = run_rule(track)
            removed = sum(r["area_m2"] for r in evidence["removed"])
            self.assertAlmostEqual(removed, today.area - shape.area, delta=1.0)
            for r in evidence["removed"]:
                self.assertIn(r["kind"], ("gap", "strip"))
                self.assertAlmostEqual(r["polygon"].area, r["area_m2"], delta=1e-6)

    def test_sites_carry_the_engines_numbers(self):
        points, _ = nh_points()
        spacing, alpha = area._adaptive_alpha(points, True)
        today = alpha_shape(densify(points), alpha)
        _shape, evidence = edge_scale.trim(points, today, alpha, spacing)
        numbers = [s["site"] for s in evidence["sites"]]
        areas = [s["area_m2"] for s in evidence["sites"]]
        self.assertEqual(numbers, [1, 2, 3])
        self.assertEqual(areas, sorted(areas, reverse=True))

    def test_a_failure_falls_back_to_today_and_says_so(self):
        field, lane = field_and_road()
        xy = utm_points(field + lane)
        spacing, alpha = area._adaptive_alpha(xy, True)
        today = alpha_shape(densify(xy), alpha)

        def broken(*_args, **_kwargs):
            raise RuntimeError("QH6154 qhull precision error")
        with unittest.mock.patch.object(edge_scale, "_triangles", broken):
            shape, evidence = edge_scale.trim(xy, today, alpha, spacing)
        self.assertTrue(evidence["fallback"])
        self.assertIn("QH6154", evidence["error"])
        self.assertEqual(evidence["removed"], [])
        self.assertAlmostEqual(shape.area, today.area, delta=1e-6)
        # control: without the failure the same day is trimmed
        shape2, evidence2 = edge_scale.trim(xy, today, alpha, spacing)
        self.assertFalse(evidence2["fallback"])
        self.assertLess(shape2.area, today.area - 1000.0)


class RealDays(unittest.TestCase):
    def test_new_holland_01_10(self):
        points, _groups = nh_points()
        spacing, alpha = area._adaptive_alpha(points, True)
        self.assertAlmostEqual(alpha, 10.0)
        today = alpha_shape(densify(points), alpha)
        before = pieces(today)
        self.assertEqual([round(g.area / 1e4, 4) for g in before],
                         [8.3296, 0.7311, 0.3839])
        shape, evidence = edge_scale.trim(points, today, alpha, spacing)
        after = pieces(shape)
        site1 = sum(g.intersection(before[0]).area for g in after) / 1e4
        # measured 11.10 on this fixture, before any run on the owner's data
        self.assertAlmostEqual(site1, 8.2267, delta=0.001)
        self.assertLess(abs(site1 - NH_OWNER_HA), abs(before[0].area / 1e4 - NH_OWNER_HA))
        # the two road sites of that day are driven five times over a wide
        # lane: kept as passes side by side -- a declared limit
        self.assertEqual([round(g.area / 1e4, 4) for g in after[1:]], [0.7311, 0.3839])
        self.assertEqual(sorted({p["site"] for p in evidence["passes"]}), [2, 3])

    def test_unit_3464_27_07(self):
        track = []
        with open(os.path.join(FIXTURES, "track_3464_20260727.csv"),
                  encoding="utf-8") as fh:
            for row in csv.DictReader(fh, delimiter=";"):
                hh, mm, ss = row["time"].split(":")
                track.append((int(hh) * 3600 + int(mm) * 60 + int(ss),
                              float(row["lon"]), float(row["lat"]),
                              float(row["speed"])))
        self.assertAlmostEqual(total(work_sites(track)[0]), 8.772, delta=0.001)
        self.assertAlmostEqual(total(work_sites(track, edge_rule=SCALE)[0]), 8.7091,
                               delta=0.001)


class Robustness(unittest.TestCase):
    def test_the_rule_sees_the_engines_own_dense_points(self):
        rng = np.random.default_rng(7)
        xy, x = [], 0.0
        for step in (3.0, 12.0, 40.0, 149.0, 151.0, 200.0, 7.5, 0.0, 22.0, 90.0, 5.0, 10.0):
            x += step
            xy.append((x, float(rng.uniform(-2.0, 2.0))))
        P, along, stretch = edge_scale._dense(np.asarray(xy))
        self.assertEqual([tuple(p) for p in P], [tuple(p) for p in densify(xy)])
        self.assertTrue(np.all(np.diff(along) >= 0))
        # only the two segments of 150 m and more break a stretch
        self.assertEqual(int(stretch[-1]), 2)

    def test_a_repeated_position_is_not_a_gap(self):
        field, lane = field_and_road()
        once = field + lane
        twice = [row for row in once for _ in range(2)]
        a = total(work_sites(once, edge_rule=SCALE)[0])
        b = total(work_sites(twice, edge_rule=SCALE)[0])
        self.assertAlmostEqual(a, b, delta=1e-6)
        self.assertLess(a, total(work_sites(once)[0]) - 0.1)

    def test_the_same_input_gives_the_same_result(self):
        track = sum(plough_and_wide_outback(), [])
        first = work_sites(track, edge_rule=SCALE)[0]
        second = work_sites(track, edge_rule=SCALE)[0]
        self.assertEqual([s.polygon.wkb for s in first], [s.polygon.wkb for s in second])

    def test_degenerate_input(self):
        three = [(0.0, 0.0), (1.0, 0.0), (2.0, 0.0)]
        self.assertEqual(edge_scale.trim(three, None, 10.0, None)[0], None)
        line = [(float(i) * 20.0, 0.0) for i in range(50)]
        self.assertIsNone(alpha_shape(densify(line), 10.0))
        shape, evidence = edge_scale.trim(line, None, 10.0, 2.0)
        self.assertIsNone(shape)
        self.assertFalse(evidence["fallback"])
        same = [(5.0, 5.0)] * 30
        self.assertIsNone(edge_scale.trim(same, None, 10.0, None)[0])

    def test_a_pinned_alpha_works_with_the_rule(self):
        field, lane = field_and_road()
        today, _ = work_sites(field + lane, alpha_m=10.0)
        trimmed, _ = work_sites(field + lane, alpha_m=10.0, edge_rule=SCALE)
        self.assertLess(total(trimmed), total(today))
        self.assertTrue(all(s.method_version == "fixed-alpha-10m+" + SCALE
                            for s in trimmed))
        self.assertTrue(all(isinstance(s.polygon, shapely.Polygon) for s in trimmed))


if __name__ == "__main__":
    unittest.main()
