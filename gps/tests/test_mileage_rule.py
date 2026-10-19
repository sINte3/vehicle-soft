# -*- coding: utf-8 -*-
"""Правило P -- пробег по точкам (предрегистрация 19.10.2026).

Run:
  python -m unittest gps.tests.test_mileage_rule
"""

import os
import sys
import unittest

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(
    os.path.abspath(__file__))))
if REPO_ROOT not in sys.path:
    sys.path.insert(0, REPO_ROOT)

from gps import mileage                                            # noqa: E402

LON, LAT = 64.4, 39.7
# Градус долготы на широте 39,7 -- около 85,6 км; 0,001 градуса -- около 85,6 м.
STEP = 0.001


def drive(n, step_s=30, speed=10.0, start=0):
    return [(start + i * step_s, LON + i * STEP, LAT, speed) for i in range(n)]


class RuleP(unittest.TestCase):
    def test_the_preregistered_numbers(self):
        self.assertEqual(mileage.MAX_SPEED_KMH, 150.0)
        self.assertEqual(mileage.EARTH_RADIUS_KM, 6371.0)

    def test_the_distance_is_a_great_circle_arc(self):
        # Градус меридиана -- около 111,19 км при радиусе 6 371 км.
        self.assertAlmostEqual(mileage.distance_km(64.0, 39.0, 64.0, 40.0),
                               111.195, places=2)

    def test_a_moving_track_is_summed(self):
        result = mileage.mileage(drive(11), 0, 10 * 30)
        expected = 10 * mileage.distance_km(LON, LAT, LON + STEP, LAT)
        self.assertAlmostEqual(result.km, expected, places=9)
        self.assertEqual((result.pairs, result.counted), (10, 10))

    def test_a_standing_tracker_adds_nothing_however_it_jitters(self):
        points = [(i * 30, LON + (STEP if i % 2 else 0), LAT, 0.0)
                  for i in range(100)]
        result = mileage.mileage(points, 0, 100 * 30)
        self.assertEqual((result.km, result.standing), (0.0, 99))

    def test_one_moving_end_is_enough(self):
        points = [(0, LON, LAT, 0.0), (30, LON + STEP, LAT, 12.0),
                  (60, LON + 2 * STEP, LAT, 0.0)]
        self.assertEqual(mileage.mileage(points, 0, 60).counted, 2)

    def test_unknown_speed_counts_as_standing(self):
        points = [(0, LON, LAT, None), (30, LON + STEP, LAT, None)]
        self.assertEqual(mileage.mileage(points, 0, 30).km, 0.0)

    def test_a_jump_faster_than_150_kmh_is_dropped(self):
        # 10 км за 60 с -- 600 км/ч.
        points = [(0, LON, LAT, 30.0), (60, LON + 10 / 85.6, LAT, 30.0),
                  (90, LON + 10 / 85.6 + STEP, LAT, 30.0)]
        result = mileage.mileage(points, 0, 90)
        self.assertEqual((result.jumps, result.counted), (1, 1))
        self.assertLess(result.km, 0.1)

    def test_just_under_the_limit_is_kept(self):
        # 2,4 км за 60 с -- 144 км/ч.
        step = mileage.distance_km(LON, LAT, LON + 0.028, LAT)
        self.assertLess(step / 60 * 3600, 150.0)
        points = [(0, LON, LAT, 50.0), (60, LON + 0.028, LAT, 50.0)]
        self.assertEqual(mileage.mileage(points, 0, 60).jumps, 0)

    def test_a_pause_does_not_cancel_the_pair(self):
        points = [(0, LON, LAT, 20.0), (1800, LON + 0.05, LAT, 20.0)]
        self.assertGreater(mileage.mileage(points, 0, 1800).km, 4.0)

    def test_pairs_reaching_outside_are_not_counted(self):
        points = drive(5)
        self.assertEqual(mileage.mileage(points, 30, 90).counted, 2)
        self.assertEqual(mileage.mileage([], 0, 10),
                         (0.0, 0, 0, 0, 0))


if __name__ == '__main__':
    unittest.main()
