# -*- coding: utf-8 -*-
"""gps/multipass.py -- признаки двойного прохода в одних машино-сутках.

Синтетика, ответ известен по построению:
  * один проход поля (комбайн, проходы через 3,5 м) -- повтора нет, возврат
    после разворота идёт по соседним рядам, второго направления нет;
  * то же поле, пройденное второй раз через три часа с ошибкой GPS -- повтор
    около половины хода: так выглядят «те же места дважды за сутки»;
  * поле вдоль и поперёк -- два направления покрывают большую часть участка;
  * немедленный возврат по тем же рядам -- его ловит «возврат по тем же
    рядам», а повторный ход не видит (слепое пятно записано в §2.14);
  * густые соседние проходы (через 2 м) -- плотность сама по себе повтором не
    становится;
  * полоса шире шага между проходами -- соседние ряды становятся повтором:
    почему ширина полосы -- допуск на ошибку GPS, а не ширина агрегата.
Каждый положительный случай стоит рядом с отрицательным: признак, который
даёт одно и то же на обоих, проверкой не является.

Запуск из корня репозитория окружением расчёта:
  python -m unittest gps.tests.test_multipass
"""
import math
import random
import unittest
import unittest.mock

import gps.multipass as multipass
from gps.area import work_sites
from gps.tests.test_area import xy_to_lonlat

HOUR = 3600.0
# развороты на краю поля дают короткие отрезки поперёк -- доли процента
TURNS_ONLY = 0.03


def shuttle(width_m, height_m, spacing_m, along='north', step_m=10.0,
            speed_kmh=6.0, start=0.0, offsets=None, noise=None):
    """Проходы туда-обратно по прямоугольнику; [(t, lon, lat, speed)].

    along -- направление проходов: 'north' (проходы вдоль оси север, сдвиг
    на восток) или 'east'. offsets -- поперечные положения проходов вместо
    равного шага. noise -- функция (x, y, t) -> (dx, dy) ошибки GPS.
    """
    lanes = offsets if offsets is not None else [
        i * spacing_m for i in range(int(width_m / spacing_m + 1e-9) + 1)]
    seconds_per_step = step_m / (speed_kmh / 3.6)
    track, t, upward = [], start, True
    grid = [i * step_m for i in range(int(height_m / step_m + 1e-9) + 1)]
    for lane in lanes:
        for position in (grid if upward else list(reversed(grid))):
            east, north = (lane, position) if along == 'north' else (position, lane)
            if noise is not None:
                dx, dy = noise(east, north, t)
                east, north = east + dx, north + dy
            lon, lat = xy_to_lonlat(east, north)
            track.append((t, lon, lat, speed_kmh))
            t += seconds_per_step
        upward = not upward
        t += 20.0                                   # разворот
    return track


def drifting_noise(sigma_m, seed=7):
    """Ошибка GPS, которая плывёт медленно, как у настоящего трекера."""
    rng = random.Random(seed)
    state = {'dx': 0.0, 'dy': 0.0}

    def noise(_east, _north, _t):
        # авторегрессия: соседние точки ошибаются почти одинаково
        state['dx'] = 0.9 * state['dx'] + rng.gauss(0.0, sigma_m * math.sqrt(0.19))
        state['dy'] = 0.9 * state['dy'] + rng.gauss(0.0, sigma_m * math.sqrt(0.19))
        return state['dx'], state['dy']
    return noise


def day_of(*tracks):
    points = []
    for track in tracks:
        points.extend((t, lon, lat, speed, 10) for t, lon, lat, speed in track)
    return sorted(points)


def last_time(track):
    return track[-1][0]


def picker_field(start=0.0, noise=None):
    """Комбайн: поле 105 x 300 м, проходы через 3,5 м, точка каждые 10 м."""
    return shuttle(105.0, 300.0, 3.5, start=start, noise=noise)


def returns_on_same_rows(start=0.0):
    """Вверх по ряду и сразу вниз по тому же ряду (в 0,3 м), затем сдвиг на
    7 м: каждый ряд пройден дважды, сразу после разворота."""
    lanes = []
    for i in range(16):
        lanes += [i * 7.0, i * 7.0 + 0.3]
    return shuttle(105.0, 300.0, None, offsets=lanes, start=start)


class OnePass(unittest.TestCase):

    def setUp(self):
        self.day = day_of(picker_field())
        self.measured = multipass.measure_day(self.day)

    def test_a7_is_the_engine_area(self):
        sites, _ = work_sites(self.day)
        self.assertEqual(self.measured['sites'], len(sites))
        self.assertAlmostEqual(self.measured['a7_ha'],
                               round(sum(site.area_ha for site in sites), 4))
        self.assertGreater(self.measured['a7_ha'], 3.0)

    def test_no_repeat_no_same_rows_no_second_direction(self):
        self.assertLessEqual(self.measured['repeat'], 0.02)
        self.assertGreater(self.measured['returns'], 20)
        self.assertEqual(self.measured['same_rows'], 0)
        # развороты на краю поля -- короткие отрезки поперёк: доли процента
        self.assertLessEqual(self.measured['two_dirs'], TURNS_ONLY)

    def test_track_and_density_are_measured(self):
        # 31 проход по 300 м внутри участка -- около 9 км
        self.assertAlmostEqual(self.measured['track_km'], 9.3, delta=0.4)
        self.assertAlmostEqual(self.measured['km_per_ha'],
                               self.measured['track_km'] / self.measured['a7_ha'],
                               places=2)


class SameFieldTwiceInOneDay(unittest.TestCase):

    def test_the_second_pass_three_hours_later_is_about_half_the_work(self):
        first = picker_field()
        second = picker_field(start=last_time(first) + 3 * HOUR,
                              noise=drifting_noise(1.0))
        measured = multipass.measure_day(day_of(first, second))
        self.assertGreaterEqual(measured['repeat'], 0.4)
        self.assertLessEqual(measured['repeat'], 0.55)
        self.assertLessEqual(measured['two_dirs'], TURNS_ONLY)

    def test_two_metres_of_gps_error_still_shows_it(self):
        first = picker_field()
        second = picker_field(start=last_time(first) + 3 * HOUR,
                              noise=drifting_noise(2.0, seed=11))
        self.assertGreaterEqual(multipass.measure_day(day_of(first, second))['repeat'],
                                0.25)

    def test_two_different_fields_on_one_day_are_not_a_repeat(self):
        first = picker_field()
        second = [(t, lon + 0.01, lat, speed) for t, lon, lat, speed in
                  picker_field(start=last_time(first) + 3 * HOUR,
                               noise=drifting_noise(1.0))]
        measured = multipass.measure_day(day_of(first, second))
        self.assertEqual(measured['sites'], 2)
        self.assertLessEqual(measured['repeat'], 0.02)

    def test_a_window_is_not_compared_with_itself(self):
        """Окно сравнивается с землёй, пройденной до него: если бы окно
        сравнивалось и с собой, один проход давал бы повтор целиком, и даже
        при нулевом пороге первое окно суток повторным бы стало."""
        with unittest.mock.patch.object(multipass, 'REPEAT_MIN_SHARE', 0.0):
            # нулевой порог: повторно всё, кроме первого окна суток
            share, _ = multipass.repeat_share(multipass.segments(
                *multipass.work_track(day_of(picker_field()))))
        self.assertGreater(share, 0.8)
        self.assertLess(share, 1.0)


class AlongAndAcross(unittest.TestCase):
    """Поле вдоль и поперёк. Два направления видны при любом шаге; повторный
    ход видит проход поперёк, только если проходы первого направления гуще
    полосы / REPEAT_MIN_SHARE (около 4,3 м): иначе поперечный проход ложится
    на пройденную землю меньшей частью. Признаки дополняют друг друга."""

    @staticmethod
    def cross(spacing_m):
        along = shuttle(150.0, 150.0, spacing_m, along='north', speed_kmh=8.0)
        across = shuttle(150.0, 150.0, spacing_m, along='east', speed_kmh=8.0,
                         start=last_time(along) + 600.0)
        return along, across

    def test_dense_passes_both_ways_are_two_directions_and_repeat(self):
        measured = multipass.measure_day(day_of(*self.cross(3.5)))
        self.assertGreaterEqual(measured['two_dirs'], 0.6)
        self.assertAlmostEqual(measured['two_dirs_angle'], 90.0, delta=10.0)
        self.assertGreaterEqual(measured['repeat'], 0.3)

    def test_sparse_passes_both_ways_are_two_directions_only(self):
        measured = multipass.measure_day(day_of(*self.cross(6.0)))
        self.assertGreaterEqual(measured['two_dirs'], 0.3)
        self.assertLessEqual(measured['repeat'], 0.1)

    def test_one_direction_alone_is_neither(self):
        along, _ = self.cross(3.5)
        measured = multipass.measure_day(day_of(along))
        self.assertLessEqual(measured['two_dirs'], TURNS_ONLY)
        self.assertLessEqual(measured['repeat'], 0.02)


class ImmediateReturnOnTheSameRows(unittest.TestCase):

    def setUp(self):
        self.measured = multipass.measure_day(day_of(returns_on_same_rows()))

    def test_the_return_on_the_same_rows_is_caught(self):
        self.assertGreater(self.measured['returns'], 20)
        self.assertGreaterEqual(self.measured['same_rows'] / self.measured['returns'],
                                0.45)

    def test_the_repeat_share_does_not_see_it(self):
        """Слепое пятно повторного хода (§2.14): оба прохода в одном окне."""
        self.assertLessEqual(self.measured['repeat'], 0.1)


class DensityIsNotRepeat(unittest.TestCase):

    def test_dense_neighbouring_passes_are_not_a_repeat(self):
        dense = shuttle(60.0, 300.0, 2.0)
        measured = multipass.measure_day(day_of(dense))
        self.assertLessEqual(measured['repeat'], 0.02)
        self.assertEqual(measured['same_rows'], 0)

    def test_a_strip_wider_than_the_spacing_turns_neighbours_into_repeat(self):
        """Почему полоса -- допуск на ошибку GPS, а не ширина агрегата: при
        полосе шире шага соседние ряды ложатся на «пройденную» землю. Работа
        «загонками» -- сначала через ряд, потом между -- при полосе ±1,5 м
        повтора не даёт, при ±3 м даёт."""
        lanes = [i * 4.0 for i in range(16)] + [2.0 + i * 4.0 for i in range(15)]
        lands = shuttle(60.0, 300.0, None, offsets=lanes)
        narrow = multipass.measure_day(day_of(lands))
        with unittest.mock.patch.object(multipass, 'STRIP_HALF_WIDTH_M', 3.0):
            wide = multipass.measure_day(day_of(lands))
        self.assertLessEqual(narrow['repeat'], 0.05)
        self.assertGreaterEqual(wide['repeat'], 0.3)


class Pieces(unittest.TestCase):

    def test_no_sites_means_no_measures(self):
        drive = [(i * 10.0, *xy_to_lonlat(i * 30.0, 0.0), 11.0) for i in range(60)]
        measured = multipass.measure_day(day_of(drive))
        self.assertEqual(measured['sites'], 0)
        self.assertIsNone(measured['repeat'])
        self.assertEqual((measured['returns'], measured['two_dirs']), (0, 0.0))

    def test_given_sites_are_used_as_given(self):
        day = day_of(picker_field())
        sites, _ = work_sites(day)
        self.assertEqual(multipass.measure_day(day, sites), multipass.measure_day(day))

    def test_angles(self):
        self.assertEqual(multipass.axis_gap(10.0, 190.0), 0.0)
        self.assertEqual(multipass.axis_gap(0.0, 100.0), 80.0)
        self.assertEqual(multipass.turn_angle(10.0, 190.0), 180.0)
        self.assertEqual(multipass.turn_angle(350.0, 20.0), 30.0)


if __name__ == '__main__':
    unittest.main()
