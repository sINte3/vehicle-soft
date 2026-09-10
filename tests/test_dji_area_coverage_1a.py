# -*- coding: utf-8 -*-
"""DRONE-AREA-1A: покрытие по подтверждённому применению (dji_area/coverage.py).

Движок v2 здесь не переписывается и не проверяется заново -- у него свой
набор. Проверяется РОВНО то, что добавил этот инкремент: работой становится
отрезок с наблюдённым применением, а не отрезок правдоподобной геометрии.

Каждая проверка построена как РАЗЛИЧЕНИЕ двух случаев, отличающихся ровно
одним признаком, и рядом стоит отрицательный контроль. Главный тест --
намеренное стопроцентное перекрытие: он единственный отличает километражный
интеграл (S) от уникального объединения (U), и на обычных параллельных
проходах такое различение невозможно в принципе.

Stdlib, без Flask и без базы. Кадры синтетические.
"""

import math
import os
import sys
import unittest

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if REPO_ROOT not in sys.path:
    sys.path.insert(0, REPO_ROOT)

from dji_area import coverage as cov  # noqa: E402

LAT0, LON0 = 39.90000, 64.40000
M_PER_DEG_LAT = 6371000.0 * math.pi / 180.0
WIDTH = 6.0
CH = cov.CH_INFORMATIVE


def north_run(length_m, n, lon_offset_m=0.0, spray=True, width=WIDTH,
              t0=0, step_ms=1000):
    """Прямой проход на север: n кадров на length_m метров."""
    dlat = length_m / M_PER_DEG_LAT
    dlon = lon_offset_m / (M_PER_DEG_LAT * math.cos(math.radians(LAT0)))
    out = []
    for i in range(n):
        f = {'t': t0 + i * step_ms,
             'lat': LAT0 + dlat * i / float(n - 1),
             'lng': LON0 + dlon,
             'vx': 0.0, 'vy': length_m / float(n - 1)}
        if width is not None:
            f['width'] = width
        if spray:
            f['spray_flag'] = 1
            f['flow'] = 100
        out.append(f)
    return out


class ApplicationStateDecidesWork(unittest.TestCase):

    def test_a_sprayed_pass_produces_the_geometric_swath(self):
        # 100 м x 6 м = 600 м2 = 0.06 га, плюс полукруги на концах.
        r = cov.coverage_from_v4(north_run(100.0, 21), CH)
        self.assertEqual(r['status'], 'ESTIMATE')
        self.assertEqual(r['metric_name'], 'V4_APPLICATION_COVERAGE_ESTIMATE')
        self.assertAlmostEqual(r['unique_application_ha'], 0.06, delta=0.006)

    def test_control_the_same_pass_with_spray_off_is_not_work(self):
        # Отличается РОВНО состоянием применения. Геометрия та же.
        r = cov.coverage_from_v4(north_run(100.0, 21, spray=False), CH)
        self.assertEqual(r['status'], 'NO_CONFIRMED_APPLICATION')
        self.assertNotIn('unique_application_ha', r)
        self.assertGreater(r['segment_length_m'][cov.SEG_NO_APPLICATION], 90.0)

    def test_unreliable_channel_yields_no_number_at_all(self):
        r = cov.coverage_from_v4(north_run(100.0, 21), 'UNRELIABLE')
        self.assertEqual(r['status'], 'NO_CONFIRMED_APPLICATION')
        self.assertEqual(r['reason'], cov.SEG_APPLICATION_UNKNOWN)

    def test_missing_width_paints_nothing_and_invents_no_area(self):
        r = cov.coverage_from_v4(north_run(100.0, 21, width=None), CH)
        self.assertEqual(r['status'], 'NO_CONFIRMED_APPLICATION')
        self.assertGreater(r['segment_length_m'][cov.SEG_NO_WIDTH], 90.0)


class OverlapIsNotCountedTwice(unittest.TestCase):
    """Единственный тест, различающий одометр и объединение."""

    def test_the_same_ground_flown_twice_gives_S_over_U_near_two(self):
        frames = north_run(100.0, 21) + north_run(100.0, 21, t0=30000)
        r = cov.coverage_from_v4(frames, CH)
        # Уникальная поверхность -- одна полоса, не две.
        self.assertAlmostEqual(r['unique_application_ha'], 0.06, delta=0.006)
        # Километражный интеграл видит обе.
        self.assertAlmostEqual(r['swath_integral_ha'], 0.12, delta=0.006)
        self.assertGreater(r['s_over_u'], 1.7)
        self.assertGreater(r['repeated_application_ha'], 0.04)

    def test_control_a_single_pass_has_no_repeated_coverage(self):
        # Без этого контроля проверка выше прошла бы и у кода, который просто
        # всегда объявляет половину площади повторной.
        r = cov.coverage_from_v4(north_run(100.0, 21), CH)
        self.assertLess(abs(r['s_over_u'] - 1.0), 0.25)
        self.assertLess(abs(r['repeated_application_ha']), 0.015)

    def test_control_two_parallel_passes_do_not_look_like_a_repeat(self):
        # Соседние проходы на расстоянии ровно ширины: перекрытия нет,
        # уникальная площадь вдвое больше одной полосы, S/U по-прежнему ~1.
        frames = (north_run(100.0, 21)
                  + north_run(100.0, 21, lon_offset_m=WIDTH, t0=30000))
        r = cov.coverage_from_v4(frames, CH)
        self.assertAlmostEqual(r['unique_application_ha'], 0.12, delta=0.012)
        self.assertLess(abs(r['s_over_u'] - 1.0), 0.25)


class FerryIsSeparatedFromApplication(unittest.TestCase):

    def test_flown_swath_exceeds_applied_swath_when_part_is_dry(self):
        frames = (north_run(100.0, 21)
                  + north_run(100.0, 21, lon_offset_m=WIDTH * 3, t0=30000,
                              spray=False))
        r = cov.coverage_from_v4(frames, CH)
        self.assertEqual(r['status'], 'ESTIMATE')
        self.assertAlmostEqual(r['unique_application_ha'], 0.06, delta=0.006)
        # Пролетели заметно больше, чем внесли.
        self.assertGreater(r['flown_swath_ha'], r['unique_application_ha'])
        self.assertGreater(r['segment_length_m'][cov.SEG_NO_APPLICATION], 90.0)

    def test_switching_spray_on_mid_pass_lands_in_the_edge_bucket(self):
        frames = north_run(100.0, 21, spray=False)
        for f in frames[10:]:
            f['spray_flag'] = 1
            f['flow'] = 100
        r = cov.coverage_from_v4(frames, CH)
        self.assertIn(cov.SEG_APPLICATION_EDGE, r['segment_length_m'])
        # Ровно один отрезок перехода, и он не отнесён к работе.
        self.assertLess(r['segment_length_m'][cov.SEG_APPLICATION_EDGE], 6.0)
        self.assertAlmostEqual(r['unique_application_ha'], 0.03, delta=0.006)


class RecordingGapsArePaintedByNobody(unittest.TestCase):
    """Кадр V4 -- телеметрия, а не вершина упрощённой ломаной."""

    def test_a_jump_between_consecutive_frames_is_a_gap_not_a_pass(self):
        # Два прохода подряд: возврат из конца первого в начало второго --
        # сто метров за одну секунду. Такой полосы не было.
        frames = north_run(100.0, 21) + north_run(100.0, 21, t0=21000)
        r = cov.coverage_from_v4(frames, CH)
        self.assertIn(cov.SEG_GAP, r['segment_length_m'])
        self.assertAlmostEqual(r['swath_integral_ha'], 0.12, delta=0.006)

    def test_control_a_normal_step_at_the_same_speed_is_not_a_gap(self):
        # Тот же шаг 5 м, но за секунду -- это 5 м/с, нормальный проход.
        r = cov.coverage_from_v4(north_run(100.0, 21), CH)
        self.assertNotIn(cov.SEG_GAP, r['segment_length_m'])

    def test_a_time_hole_makes_a_gap_even_at_a_plausible_distance(self):
        frames = north_run(100.0, 21)
        for f in frames[11:]:
            f['t'] += 60000          # минута без записи
        r = cov.coverage_from_v4(frames, CH)
        self.assertIn(cov.SEG_GAP, r['segment_length_m'])


if __name__ == '__main__':
    unittest.main()
