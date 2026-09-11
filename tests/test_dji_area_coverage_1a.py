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
        # Повторное покрытие называется только тогда, когда разность S и U
        # выходит за дискретизационный шум растра.
        self.assertIsNotNone(r['repeated_application_ha'])
        self.assertGreater(r['repeated_application_ha'], 0.04)
        # Доля -- то, что отличает повтор от методического шума.
        self.assertGreater(r['repeated_share_of_unique'], 0.7)

    def test_control_a_single_pass_has_no_repeated_coverage(self):
        # Без этого контроля проверка выше прошла бы и у кода, который просто
        # всегда объявляет половину площади повторной.
        r = cov.coverage_from_v4(north_run(100.0, 21), CH)
        # Полоса УЖЕ порога решения 1.15, которым инструмент 1B помечает
        # различающие вылеты: контроль обязан быть строже решения, иначе он
        # пропускает ровно те значения, на которых решение меняется.
        self.assertLess(abs(r['s_over_u'] - 1.0), 0.14)
        # Разность методическая: доли процента от полосы, а не десятки.
        self.assertLess(r['repeated_share_of_unique'], 0.01)

    def test_control_two_parallel_passes_do_not_look_like_a_repeat(self):
        # Соседние проходы на расстоянии ровно ширины: перекрытия нет,
        # уникальная площадь вдвое больше одной полосы, S/U по-прежнему ~1.
        frames = (north_run(100.0, 21)
                  + north_run(100.0, 21, lon_offset_m=WIDTH, t0=30000))
        r = cov.coverage_from_v4(frames, CH)
        self.assertAlmostEqual(r['unique_application_ha'], 0.12, delta=0.012)
        self.assertLess(abs(r['s_over_u'] - 1.0), 0.14)
        self.assertLess(r['repeated_share_of_unique'], 0.01)


class FerryIsSeparatedFromApplication(unittest.TestCase):

    def test_flown_swath_exceeds_applied_swath_when_part_is_dry(self):
        frames = (north_run(100.0, 21)
                  + north_run(100.0, 21, lon_offset_m=WIDTH * 3, t0=30000,
                              spray=False))
        r = cov.coverage_from_v4(frames, CH)
        self.assertEqual(r['status'], 'ESTIMATE')
        self.assertAlmostEqual(r['unique_application_ha'], 0.06, delta=0.006)
        # Закрашено (работа + холостое) заметно больше, чем внесено.
        self.assertGreater(r['painted_swath_total_ha'],
                           r['unique_application_ha'])
        # И холостая часть названа отдельным числом, а не подписью на общей
        # величине: подпись «холостой пролёт» на объединении ВСЕГО
        # закрашенного врала -- на сплошь обработанном проходе обе величины
        # совпадают, и читатель заключил бы, что холостого хода было столько
        # же, сколько работы.
        self.assertIsNotNone(r['flown_not_applied_ha'])
        self.assertGreater(r['flown_not_applied_ha'], 0.01)
        self.assertGreater(r['segment_length_m'][cov.SEG_NO_APPLICATION], 90.0)

    def test_control_a_fully_sprayed_pass_has_no_idle_swath(self):
        # Именно этот случай ловил прежнюю подпись: работа = всё закрашенное.
        r = cov.coverage_from_v4(north_run(100.0, 21), CH)
        self.assertAlmostEqual(r['painted_swath_total_ha'],
                               r['unique_application_ha'], delta=0.002)
        self.assertLess(abs(r['flown_not_applied_ha']), 0.002)

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


class ThresholdsAreLoadBearingAndTested(unittest.TestCase):
    """Пороги, которые не проверены, можно менять как угодно."""

    def test_width_is_the_lesser_of_the_two_endpoints_not_the_greater(self):
        # Ширина падает на середине прохода. min даёт узкую полосу, max --
        # широкую; без этой проверки обе реализации проходят одинаково.
        # Ширина чередуется КАЖДЫЙ кадр, поэтому у каждого отрезка концы
        # разные и выбор min/max меняет ВСЕ отрезки, а не один.
        frames = north_run(100.0, 21)
        for i, f in enumerate(frames):
            f['width'] = WIDTH if i % 2 == 0 else WIDTH / 3.0
        r = cov.coverage_from_v4(frames, CH)
        # min -> 2 м на всех отрезках -> 100 x 2 = 200 м2 = 0.02 га.
        # max -> 6 м -> 600 м2 = 0.06 га. Полосы не пересекаются.
        self.assertEqual(r['widths_used_m'], [WIDTH / 3.0])
        self.assertAlmostEqual(r['swath_integral_ha'], 0.02, delta=0.004)

    def test_a_step_just_over_the_speed_gate_is_a_gap(self):
        # 21 м за секунду -- выше 20 м/с. Порог обязан сработать.
        frames = [{'t': 0, 'lat': LAT0, 'lng': LON0, 'width': WIDTH,
                   'spray_flag': 1, 'flow': 100},
                  {'t': 1000, 'lat': LAT0 + 21.0 / M_PER_DEG_LAT, 'lng': LON0,
                   'width': WIDTH, 'spray_flag': 1, 'flow': 100}]
        segs = cov.build_segments(frames, cov.plane_for(
            [(f['lat'], f['lng']) for f in frames]), CH)
        self.assertEqual([s.reason for s in segs], [cov.SEG_GAP])

    def test_control_a_step_just_under_the_speed_gate_is_work(self):
        frames = [{'t': 0, 'lat': LAT0, 'lng': LON0, 'width': WIDTH,
                   'spray_flag': 1, 'flow': 100},
                  {'t': 1000, 'lat': LAT0 + 19.0 / M_PER_DEG_LAT, 'lng': LON0,
                   'width': WIDTH, 'spray_flag': 1, 'flow': 100}]
        segs = cov.build_segments(frames, cov.plane_for(
            [(f['lat'], f['lng']) for f in frames]), CH)
        self.assertEqual([s.reason for s in segs], [cov.SEG_WORK])

    def test_a_hole_just_over_the_time_gate_is_a_gap(self):
        for dt_ms, expected in ((3100, cov.SEG_GAP), (2900, cov.SEG_WORK)):
            frames = [{'t': 0, 'lat': LAT0, 'lng': LON0, 'width': WIDTH,
                       'spray_flag': 1, 'flow': 100},
                      {'t': dt_ms, 'lat': LAT0 + 5.0 / M_PER_DEG_LAT,
                       'lng': LON0, 'width': WIDTH, 'spray_flag': 1,
                       'flow': 100}]
            segs = cov.build_segments(frames, cov.plane_for(
                [(f['lat'], f['lng']) for f in frames]), CH)
            self.assertEqual([s.reason for s in segs], [expected],
                             'dt=%d ms' % dt_ms)

    def test_a_frame_without_a_timestamp_cannot_be_painted_on_trust(self):
        # `t` кладётся только когда поле присутствует в protobuf. Без него
        # скорость не проверить, и длинный отрезок красить нельзя.
        frames = [{'lat': LAT0, 'lng': LON0, 'width': WIDTH,
                   'spray_flag': 1, 'flow': 100},
                  {'lat': LAT0 + 90.0 / M_PER_DEG_LAT, 'lng': LON0,
                   'width': WIDTH, 'spray_flag': 1, 'flow': 100}]
        segs = cov.build_segments(frames, cov.plane_for(
            [(f['lat'], f['lng']) for f in frames]), CH)
        self.assertEqual([s.reason for s in segs], [cov.SEG_GAP])


class TheApplicationRuleIsPinned(unittest.TestCase):
    """Мутанты, пережившие прежний набор."""

    def _one(self, **extra):
        base = {'t': 0, 'lat': LAT0, 'lng': LON0, 'width': WIDTH}
        second = dict(base, t=1000, lat=LAT0 + 5.0 / M_PER_DEG_LAT)
        base.update(extra)
        second.update(extra)
        segs = cov.build_segments([base, second], cov.plane_for(
            [(base['lat'], base['lng']), (second['lat'], second['lng'])]), CH)
        return segs[0].reason

    def test_flow_alone_without_a_spray_flag_is_application(self):
        # [REASON]: `spray_flag or flow`, не `and`. v4.py считает flag_frames
        # и flow_frames РАЗДЕЛЬНО именно потому, что на боевом корпусе они
        # расходятся. С `and` кадр с расходом и без флага перестал бы быть
        # работой, и площадь молча упала бы.
        self.assertEqual(self._one(flow=100), cov.SEG_WORK)

    def test_a_spray_flag_alone_without_flow_is_application(self):
        self.assertEqual(self._one(spray_flag=1), cov.SEG_WORK)

    def test_control_neither_flag_nor_flow_is_not_application(self):
        self.assertEqual(self._one(), cov.SEG_NO_APPLICATION)

    def test_a_zero_or_negative_or_nan_width_paints_nothing(self):
        for bad in (0.0, -6.0, float('nan'), float('inf'), True):
            self.assertEqual(self._one(spray_flag=1, width=bad),
                             cov.SEG_NO_WIDTH, 'width=%r' % (bad,))

    def test_two_frames_sharing_a_timestamp_are_a_gap_not_a_crash(self):
        # dt = 0: деление на ноль обязано не случиться, а отрезок -- стать
        # разрывом записи.
        a = {'t': 5000, 'lat': LAT0, 'lng': LON0, 'width': WIDTH,
             'spray_flag': 1}
        b = dict(a, lat=LAT0 + 5.0 / M_PER_DEG_LAT)
        segs = cov.build_segments([a, b], cov.plane_for(
            [(a['lat'], a['lng']), (b['lat'], b['lng'])]), CH)
        self.assertEqual([s.reason for s in segs], [cov.SEG_GAP])

    def test_a_backwards_timestamp_is_a_gap(self):
        a = {'t': 9000, 'lat': LAT0, 'lng': LON0, 'width': WIDTH,
             'spray_flag': 1}
        b = dict(a, t=1000, lat=LAT0 + 5.0 / M_PER_DEG_LAT)
        segs = cov.build_segments([a, b], cov.plane_for(
            [(a['lat'], a['lng']), (b['lat'], b['lng'])]), CH)
        self.assertEqual([s.reason for s in segs], [cov.SEG_GAP])

    def test_the_untimed_fallback_no_longer_trusts_the_v1_gap_distance(self):
        # Прежний запас 20 x 3 = 60 м -- это ровно `gap_m` из v1.
        a = {'lat': LAT0, 'lng': LON0, 'width': WIDTH, 'spray_flag': 1}
        for metres, expected in ((19.0, cov.SEG_WORK), (55.0, cov.SEG_GAP)):
            b = dict(a, lat=LAT0 + metres / M_PER_DEG_LAT)
            segs = cov.build_segments([a, b], cov.plane_for(
                [(a['lat'], a['lng']), (b['lat'], b['lng'])]), CH)
            self.assertEqual([s.reason for s in segs], [expected],
                             '%.0f m without a timestamp' % metres)

    def test_the_reason_names_what_actually_happened(self):
        # Жёсткое NO_APPLICATION_FRAMES врало, когда распыление шло на каждом
        # кадре, а полоса не строилась из-за отсутствующей ширины.
        frames = north_run(100.0, 21, width=None)
        r = cov.coverage_from_v4(frames, CH)
        self.assertEqual(r['status'], 'NO_CONFIRMED_APPLICATION')
        self.assertEqual(r['reason'], cov.SEG_NO_WIDTH)

    def test_the_thresholds_travel_with_the_numbers(self):
        r = cov.coverage_from_v4(north_run(100.0, 21), CH)
        self.assertEqual(r['thresholds']['max_frame_gap_s'],
                         cov.MAX_FRAME_GAP_S)
        self.assertEqual(r['thresholds']['max_ground_speed_mps'],
                         cov.MAX_GROUND_SPEED_MPS)
        self.assertIn('swath_work_ha', r['uncertainty_percent'])


if __name__ == '__main__':
    unittest.main()
