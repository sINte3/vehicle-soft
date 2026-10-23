# -*- coding: utf-8 -*-
"""Правило K1 кратности прохода -- предрегистрация 22.10.2026 (§2.14).

Что проверяется и почему именно это:

1. **Прореживание -- как записано**: точки ближе 15 м отрезка не дают,
   молчание дольше минуты режет трек. Сравнение «одна работа при разной
   частоте записи» здесь не держится: две семьи курсов гасят дрожание и без
   прореживания (проверено: 1,0 и 1,0 с прореживанием и без), и такой тест
   ничего бы не различал.
2. **Работа рядами отличается от беспорядочного движения**, а проходы
   вдоль и поперёк (борона) -- работа рядами: два семейства курсов.
3. **Правило K1 -- в предрегистрированном порядке и с её числами**:
   свидетельство (1 га и 2 км на крупнейшем участке), первая ступень, повтор
   или два направления. Границы порогов -- включительно, как записано.
4. **Синтетический двойной проход** -- то, на чём меряется условие P1:
   копия идёт после конца суток, ошибка плывёт со своим σ, зерно даёт тот же
   результат, и сутки одного прохода после удвоения правило видит как два.

Запуск:
  python -m unittest gps.tests.test_multipass_k1
"""
import math
import random
import unittest

import gps.multipass as multipass
from gps.tests.test_area import xy_to_lonlat
from gps.tests.test_multipass import (HOUR, day_of, drifting_noise, last_time,
                                      picker_field, shuttle)

# T1 для синтетики: доля работы рядами у одного прохода -- около 0,9 и выше
T1 = 0.6


def wander(seconds=4 * HOUR, step_s=3.0, seed=5, box=250.0):
    """Беспорядочное движение по участку: курс меняется на каждом шаге."""
    rng = random.Random(seed)
    x = y = box / 2
    course = 0.0
    track, t = [], 0.0
    while t < seconds:
        course += rng.uniform(-60.0, 60.0)
        step = 6.0 / 3.6 * step_s
        x = min(max(x + step * math.cos(math.radians(course)), 0.0), box)
        y = min(max(y + step * math.sin(math.radians(course)), 0.0), box)
        lon, lat = xy_to_lonlat(x, y)
        track.append((t, lon, lat, 6.0))
        t += step_s
    return track


def rows_of(track):
    return multipass.measure_day(day_of(track))['rows']


class Thinning(unittest.TestCase):

    def test_points_closer_than_the_step_make_no_segment(self):
        xy = [(0.0, 0.0), (5.0, 0.0), (10.0, 0.0), (16.0, 0.0), (31.0, 0.0)]
        times = [0.0, 1.0, 2.0, 3.0, 4.0]
        from shapely.geometry import box
        segs = multipass.thinned_segments(xy, times, box(-10, -10, 100, 10))
        self.assertEqual([round(seg.length, 1) for seg in segs], [16.0, 15.0])

    def test_silence_longer_than_a_minute_cuts_the_thinned_track(self):
        xy = [(0.0, 0.0), (20.0, 0.0), (40.0, 0.0), (60.0, 0.0)]
        times = [0.0, 5.0, 70.0, 75.0]
        from shapely.geometry import box
        segs = multipass.thinned_segments(xy, times, box(-10, -10, 100, 10))
        self.assertEqual(len(segs), 2)            # 0-20 и 40-60, без 20-40


class RowWork(unittest.TestCase):

    def test_a_shuttle_is_row_work(self):
        self.assertGreaterEqual(rows_of(picker_field()), 0.85)

    def test_wandering_is_not_row_work(self):
        self.assertLess(rows_of(wander()), 0.5)

    def test_along_and_across_is_two_families_of_rows(self):
        along = shuttle(150.0, 150.0, 3.5, along='north', speed_kmh=8.0)
        across = shuttle(150.0, 150.0, 3.5, along='east', speed_kmh=8.0,
                         start=last_time(along) + 600.0)
        self.assertGreaterEqual(rows_of(along + across), 0.85)

    def test_no_segments_no_measure(self):
        self.assertIsNone(multipass.row_share([]))

    def test_the_largest_site_is_measured(self):
        small = [(t, lon + 0.01, lat, v) for t, lon, lat, v in
                 shuttle(60.0, 120.0, 3.5, start=0.0)]
        big = picker_field(start=last_time(small) + HOUR)
        measured = multipass.measure_day(day_of(small, big))
        self.assertEqual(measured['sites'], 2)
        self.assertGreater(measured['largest_ha'], 2.5)
        self.assertLess(measured['largest_ha'], measured['a7_ha'])
        self.assertAlmostEqual(measured['largest_km'], 9.3, delta=0.5)


class RuleK1(unittest.TestCase):
    """Числа -- предрегистрация 22.10.2026; границы включительно."""

    def measured(self, **changes):
        value = {'largest_ha': 3.0, 'largest_km': 9.0, 'rows': 0.9,
                 'repeat': 0.0, 'two_dirs': 0.0}
        value.update(changes)
        return value

    def test_the_numbers_are_the_preregistered_ones(self):
        self.assertEqual(multipass.RULE_K1, 'multipass-K1-2026-10-22')
        self.assertEqual((multipass.EVIDENCE_MIN_HA, multipass.EVIDENCE_MIN_KM),
                         (1.0, 2.0))
        self.assertEqual(multipass.THIN_STEP_M, 15.0)
        self.assertEqual(multipass.ROWS_PERCENTILE, 0.05)
        self.assertEqual((multipass.REPEAT_MIN_K, multipass.TWO_DIRS_MIN_K),
                         (0.20, 0.20))
        self.assertEqual(multipass.CALIBRATION_GROUPS, ('B', 'M', 'L', 'C'))
        self.assertEqual(multipass.CALIBRATION_MIN_DAYS, 20)

    def test_one_pass_is_the_default(self):
        self.assertEqual(multipass.multiplicity(self.measured(), T1),
                         (1, multipass.K_ONE))

    def test_a_small_or_short_largest_site_is_not_judged(self):
        for change in (dict(largest_ha=0.99), dict(largest_km=1.99),
                       dict(largest_ha=0.5, repeat=0.9)):
            with self.subTest(change=change):
                self.assertEqual(multipass.multiplicity(self.measured(**change), T1),
                                 (1, multipass.K_NOT_JUDGED))
        self.assertEqual(multipass.multiplicity(
            self.measured(largest_ha=1.0, largest_km=2.0, repeat=0.5), T1),
            (2, multipass.K_REPEAT))

    def test_disorder_gets_no_multiplicity_however_much_it_repeats(self):
        self.assertEqual(multipass.multiplicity(
            self.measured(rows=0.59, repeat=0.9, two_dirs=0.9), T1),
            (1, multipass.K_NOT_ROWS))
        self.assertEqual(multipass.multiplicity(
            self.measured(rows=None, repeat=0.9), T1), (1, multipass.K_NOT_ROWS))
        self.assertEqual(multipass.multiplicity(
            self.measured(rows=0.6, repeat=0.9), T1), (2, multipass.K_REPEAT))

    def test_repeat_or_two_directions_from_a_fifth(self):
        rule = multipass.multiplicity
        self.assertEqual(rule(self.measured(repeat=0.20), T1), (2, multipass.K_REPEAT))
        self.assertEqual(rule(self.measured(repeat=0.1999), T1), (1, multipass.K_ONE))
        self.assertEqual(rule(self.measured(two_dirs=0.20), T1),
                         (2, multipass.K_TWO_DIRS))
        self.assertEqual(rule(self.measured(two_dirs=0.1999), T1), (1, multipass.K_ONE))
        self.assertEqual(rule(self.measured(repeat=0.3, two_dirs=0.3), T1),
                         (2, multipass.K_REPEAT))
        self.assertEqual(rule(self.measured(repeat=None), T1), (1, multipass.K_ONE))


class SyntheticDouble(unittest.TestCase):

    def test_the_copy_follows_the_day_after_half_an_hour(self):
        day = day_of(picker_field())
        doubled = multipass.synthetic_double(day, 1.0, seed=1)
        self.assertEqual(len(doubled), 2 * len(day))
        self.assertEqual(doubled[:len(day)], day)
        first_copy = doubled[len(day)]
        self.assertAlmostEqual(first_copy[0] - day[-1][0],
                               multipass.SYNTH_GAP_S, places=6)
        self.assertEqual([row[3:] for row in doubled[len(day):]],
                         [row[3:] for row in day])

    def test_the_error_drifts_with_its_sigma_and_the_seed_repeats_it(self):
        day = day_of(picker_field())
        doubled = multipass.synthetic_double(day, 2.0, seed=3)
        lat = day[0][2]
        lon_m = multipass.M_PER_DEG_LON_EQUATOR * math.cos(math.radians(lat))
        dx = [(c[1] - o[1]) * lon_m for o, c in zip(day, doubled[len(day):])]
        spread = math.sqrt(sum(v * v for v in dx) / len(dx))
        self.assertGreater(spread, 1.0)
        self.assertLess(spread, 3.5)
        # соседние точки ошибаются почти одинаково -- ошибка плывёт
        jumps = [abs(b - a) for a, b in zip(dx, dx[1:])]
        self.assertLess(sum(jumps) / len(jumps), spread)
        self.assertEqual(doubled, multipass.synthetic_double(day, 2.0, seed=3))
        self.assertNotEqual(doubled, multipass.synthetic_double(day, 2.0, seed=4))

    def test_one_pass_doubled_is_two_passes_for_the_rule(self):
        day = day_of(picker_field())
        single = multipass.measure_day(day)
        self.assertEqual(multipass.multiplicity(single, T1), (1, multipass.K_ONE))
        for sigma in (1.0, 2.0):
            with self.subTest(sigma=sigma):
                doubled = multipass.measure_day(
                    multipass.synthetic_double(day, sigma, seed=7))
                self.assertEqual(multipass.multiplicity(doubled, T1)[0], 2)

    def test_nothing_doubles_into_nothing(self):
        self.assertEqual(multipass.synthetic_double([], 1.0, seed=1), [])


if __name__ == '__main__':
    unittest.main()
