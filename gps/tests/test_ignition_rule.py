# -*- coding: utf-8 -*-
"""Правило R метода «время» (gps/ignition.py) -- предрегистрация 19.10.2026.

Проверяется ровно то, что записано в треке GPS (§0.6) до прогона: состояние
пары -- по первой точке, 300 с -- граница часов и «неизвестно», пара через
край промежутка не считается.

Run:
  python -m unittest gps.tests.test_ignition_rule
"""

import os
import sys
import unittest

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(
    os.path.abspath(__file__))))
if REPO_ROOT not in sys.path:
    sys.path.insert(0, REPO_ROOT)

from gps import ignition                                           # noqa: E402


class RuleR(unittest.TestCase):
    def test_the_preregistered_gap_is_300_seconds(self):
        # [REASON]: число предрегистрации. Меняется только новой записью в
        # треке -- тест держит, чтобы правка константы не прошла молча.
        self.assertEqual(ignition.MAX_GAP_S, 300)

    def test_pairs_with_ignition_on_count_their_whole_interval(self):
        points = [(0, 1.0), (30, 1.0), (60, 1.0), (90, 1.0)]
        parts = ignition.split(points, 0, 90)
        self.assertEqual((parts.on_s, parts.off_s, parts.unknown_s), (90, 0, 0))
        self.assertEqual((parts.pairs, parts.points, parts.with_ign), (3, 4, 4))

    def test_the_state_of_a_pair_is_that_of_its_first_point(self):
        parts = ignition.split([(0, 1.0), (30, 0.0), (90, 1.0), (100, 1.0)],
                               0, 100)
        # 0->30 включено (до выключения), 30->90 выключено, 90->100 включено
        self.assertEqual((parts.on_s, parts.off_s), (40, 60))

    def test_300_seconds_is_still_hours_and_301_is_unknown(self):
        self.assertEqual(ignition.split([(0, 1.0), (300, 1.0)], 0, 300).on_s, 300)
        parts = ignition.split([(0, 1.0), (301, 1.0)], 0, 301)
        self.assertEqual((parts.on_s, parts.unknown_s), (0, 301))

    def test_a_long_pause_with_ignition_off_is_simply_off(self):
        parts = ignition.split([(0, 0.0), (3600, 1.0), (3630, 0.0)], 0, 3630)
        self.assertEqual((parts.on_s, parts.off_s, parts.unknown_s),
                         (30, 3600, 0))

    def test_a_point_without_a_value_makes_its_pair_unknown(self):
        parts = ignition.split([(0, None), (30, 1.0), (60, 1.0)], 0, 60)
        self.assertEqual((parts.on_s, parts.unknown_s, parts.with_ign),
                         (30, 30, 2))

    def test_any_non_zero_raw_value_is_on(self):
        for value in (1.0, 2.0, 220.0, -1.0, 0.5):
            self.assertEqual(ignition.split([(0, value), (30, 0.0)], 0, 30).on_s,
                             30, value)

    def test_a_pair_reaching_outside_the_span_is_not_counted(self):
        points = [(-30, 1.0), (0, 1.0), (30, 1.0), (60, 1.0), (90, 1.0)]
        parts = ignition.split(points, 0, 60)
        self.assertEqual((parts.on_s, parts.points), (60, 3))
        # обе границы -- включительно
        self.assertEqual(ignition.split(points, 30, 30).on_s, 0)

    def test_nothing_to_pair_is_zero_not_an_error(self):
        self.assertEqual(ignition.split([], 0, 100),
                         (0, 0, 0, 0, 0, 0))
        self.assertEqual(ignition.split([(5, 1.0)], 0, 100).pairs, 0)


if __name__ == '__main__':
    unittest.main()
