# -*- coding: utf-8 -*-
"""Кратность прохода: признаки двойного прохода в одних машино-сутках.

ЗАЧЕМ
Владелец 13.10.2026 (дорожная карта, §2.14): сколько раз пройдено поле,
решает трек GPS, а не заявка; двойной проход в одни сутки -- гектары
обработки вдвое больше площади. Площадь A7 -- объединение проходов: второй
проход по той же земле её не меняет. Этот модуль меряет в треке след,
который двойной проход оставляет.

ЧЕМ ЭТО НЕ ЯВЛЯЕТСЯ
Признаки -- для разведки и отбора суток на глаз владельца, а не правило.
Кратность и гектары обработки отсюда не выводятся и никуда не пишутся;
площадь A7 не меняется. Правило -- порог и то, как доля превращается в
кратность, -- записывается до прогона приёмки отдельно (§2.14, «Следующий
шаг»). Ночной расчёт этот модуль не вызывает.

ПРИЗНАКИ -- по рабочим точкам суток (окно скорости движка, 1-15 км/ч) внутри
участков A7 (`gps.area.work_sites`). Отрезок -- две соседние рабочие точки
не дальше SEGMENT_MAX_GAP_S друг от друга по времени и длиной
SEGMENT_MIN_M..SEGMENT_MAX_M; отрезок принадлежит участку, если внутри
участка его середина.
  repeat     «повторный ход». Отрезки суток делятся на окна по WINDOW_S от
             первого; ход окна -- полоса ±STRIP_HALF_WIDTH_M вокруг его
             отрезков. Окно повторное, если не меньше REPEAT_MIN_SHARE его
             полосы лежит на земле, пройденной раньше в эти же сутки.
             Признак -- доля длины хода суток в повторных окнах.
  same_rows  «возврат по тем же рядам». Прогон -- отрезки подряд, курс
             которых отходит от среднего курса прогона не больше
             RUN_TURN_DEG, длиной не меньше RUN_MIN_M, середина -- внутри
             участка. Следующий прогон в обратную сторону (курсы расходятся
             не меньше чем на RETURN_MIN_DEG), начатый не позже
             RETURN_MAX_GAP_S после конца предыдущего и идущий рядом с ним
             не меньше RETURN_MIN_OVERLAP_M, -- возврат. По тем же рядам --
             если медиана поперечного расстояния его точек до линии
             предыдущего прогона меньше SAME_ROWS_M.
  two_dirs   «два направления». Курсы отрезков по модулю 180 -- в
             гистограмму по HEADING_BIN_DEG с весом длины. Первое семейство
             -- отрезки не дальше FAMILY_HALF_DEG от самого частого курса,
             второе -- так же от самого частого курса среди остальных, если
             он не ближе FAMILY_MIN_APART_DEG к первому. Признак -- доля
             площади участков, покрытая полосами ±FAMILY_BUFFER_M обоих
             семейств сразу.
  km_per_ha  длина рабочего хода внутри участков на гектар -- справочно.

Числа разведки 13.10 на стенде из 33 машино-суток -- в §2.14: повторный ход
у бороны вдоль и поперёк 57 %, у бухарских комбайнов 0-4 %, у
мирзачульских 0-14 %.

ПРАВИЛО K1 (`multiplicity`) -- предрегистрация 22.10.2026 (§2.14,
«Предрегистрация правила K»): свидетельство на крупнейшем участке, первая
ступень «работа рядами» (`rows` по прореженным точкам, порог T1 -- из
калибровки по стенду 13.10) и вторая -- повтор или два направления. Это
правило для приёмки (`tools/gps_multipass_acceptance.py`); ночной расчёт его
не вызывает, гектары и площадь A7 не меняются, пока приёмка не пройдена и
включение не выпущено.
"""

import math
import random
from collections import namedtuple

import numpy as np
from shapely.geometry import LineString, Point
from shapely.ops import unary_union
from shapely.prepared import prep

from gps.area import _moving_mask, to_utm, work_sites

SEGMENT_MAX_GAP_S = 60.0
SEGMENT_MIN_M = 0.5
SEGMENT_MAX_M = 80.0

WINDOW_S = 900.0
# [REASON]: полоса -- допуск на ошибку GPS, а не ширина агрегата: ширину
# агрегата владелец отверг 02.10, и метод её не знает. Полоса должна быть
# уже шага между проходами: шире -- и соседний ряд ложится на уже пройденную
# землю, повтор находится там, где его нет. Разведка 13.10: при ±2,5 м
# мирзачульский комбайн (шаг 3-3,5 м) получал до 34 % «повтора», при ±1,5 м
# -- не больше 14 %.
STRIP_HALF_WIDTH_M = 1.5
REPEAT_MIN_SHARE = 0.7

RUN_MIN_M = 80.0
RUN_TURN_DEG = 25.0
RETURN_MIN_DEG = 155.0
RETURN_MAX_GAP_S = 600.0
RETURN_MIN_OVERLAP_M = 40.0
SAME_ROWS_M = 1.5

HEADING_BIN_DEG = 10.0
FAMILY_HALF_DEG = 20.0
FAMILY_MIN_APART_DEG = 50.0
FAMILY_BUFFER_M = 2.0
FAMILY_MIN_SEGMENTS = 10

# [REASON]: числа правила K1 -- предрегистрация 22.10.2026 (§2.14). Их нельзя
# менять после прогона приёмки: правило с другими числами -- новое правило и
# новая предрегистрация на новых данных.
RULE_K1 = 'multipass-K1-2026-10-22'
EVIDENCE_MIN_HA = 1.0
EVIDENCE_MIN_KM = 2.0
# [REASON]: трекеры пишут точку раз в 2-3 с или раз в 10-30 с, и мера по
# курсам отрезков у частых трекеров «дрожит» (разведка 14.10: прямизна одних
# и тех же суток 0,37 на сырых точках и 0,81 после прореживания). Прореживание
# до 15 м делает меру первой ступени независимой от частоты записи.
THIN_STEP_M = 15.0
ROWS_PERCENTILE = 0.05
REPEAT_MIN_K = 0.20
TWO_DIRS_MIN_K = 0.20
CALIBRATION_GROUPS = ('B', 'M', 'L', 'C')
CALIBRATION_MIN_DAYS = 20

K_NOT_JUDGED = 'ne_suditsya'
K_NOT_ROWS = 'ne_ryadami'
K_REPEAT = 'povtor'
K_TWO_DIRS = 'dva_napravleniya'
K_ONE = 'odin_prohod'

# Синтетический двойной проход (условие P1 приёмки): тот же трек ещё раз,
# через полчаса после конца суток, с медленно плывущей ошибкой GPS.
SYNTH_GAP_S = 1800.0
SYNTH_AR = 0.9
M_PER_DEG_LAT = 110540.0
M_PER_DEG_LON_EQUATOR = 111320.0

Segment = namedtuple('Segment', 'line heading length time')


def axis_gap(first, second):
    """Угол между двумя курсами без учёта стороны движения: 0..90."""
    gap = abs(first - second) % 180.0
    return min(gap, 180.0 - gap)


def turn_angle(first, second):
    """Угол между двумя курсами с учётом стороны движения: 0..180."""
    gap = abs(first - second) % 360.0
    return min(gap, 360.0 - gap)


def heading(x0, y0, x1, y1):
    return math.degrees(math.atan2(y1 - y0, x1 - x0)) % 360.0


def work_track(points):
    """Рабочие точки суток: ([(x, y)] в UTM 41N, [t]), по времени.

    points -- [(t, lon, lat, speed, ...)]; рабочие -- в окне скорости
    движка (`gps.area._moving_mask`), те же, из которых строится A7.
    """
    points = sorted(points)
    if not points:
        return [], []
    mask = _moving_mask([row[3] for row in points])
    xs, ys = to_utm([row[1] for row in points], [row[2] for row in points])
    xs = np.asarray(xs, dtype=float)[mask]
    ys = np.asarray(ys, dtype=float)[mask]
    times = np.asarray([row[0] for row in points], dtype=float)[mask]
    return list(zip(xs.tolist(), ys.tolist())), times.tolist()


def _step(xy, times, i):
    """Отрезок от точки i-1 к точке i: (длина, годится ли)."""
    (x0, y0), (x1, y1) = xy[i - 1], xy[i]
    length = math.hypot(x1 - x0, y1 - y0)
    gap = times[i] - times[i - 1]
    return length, (0 < gap <= SEGMENT_MAX_GAP_S
                    and SEGMENT_MIN_M <= length <= SEGMENT_MAX_M)


def segments(xy, times, region=None):
    """Отрезки рабочего хода; с `region` -- только те, чья середина внутри."""
    inside = prep(region) if region is not None else None
    out = []
    for i in range(1, len(xy)):
        length, ok = _step(xy, times, i)
        if not ok:
            continue
        (x0, y0), (x1, y1) = xy[i - 1], xy[i]
        if inside is not None and not inside.contains(
                Point(0.5 * (x0 + x1), 0.5 * (y0 + y1))):
            continue
        out.append(Segment(LineString([(x0, y0), (x1, y1)]),
                           heading(x0, y0, x1, y1), length,
                           0.5 * (times[i] + times[i - 1])))
    return out


def repeat_share(segs):
    """(доля хода в повторных окнах, длина хода в метрах).

    Доля None -- хода нет. Первое окно суток повторным не бывает: до него
    пройденной земли нет.
    """
    if not segs:
        return None, 0.0
    ordered = sorted(segs, key=lambda seg: seg.time)
    start = ordered[0].time
    windows = {}
    for seg in ordered:
        windows.setdefault(int((seg.time - start) // WINDOW_S), []).append(seg)
    covered, total, repeated = None, 0.0, 0.0
    for key in sorted(windows):
        group = windows[key]
        # [REASON]: плоские торцы -- полоса не выходит за концы отрезка.
        # Круглый торец на каждом стыке отрезков одного прохода -- лишняя
        # земля, и на развороте она легла бы на соседний проход.
        strip = unary_union([seg.line.buffer(STRIP_HALF_WIDTH_M,
                                             cap_style='flat')
                             for seg in group])
        length = sum(seg.length for seg in group)
        total += length
        if covered is not None and strip.area > 0:
            if strip.intersection(covered).area >= REPEAT_MIN_SHARE * strip.area:
                repeated += length
        covered = strip if covered is None else covered.union(strip)
    return repeated / total, total


def runs(xy, times):
    """Прямые прогоны рабочего хода, по времени.

    Прогон -- словарь: points (точки), heading (средний курс 0..360),
    start, end (время), length (м).
    """
    out, current = [], None
    for i in range(1, len(xy)):
        length, ok = _step(xy, times, i)
        (x0, y0), (x1, y1) = xy[i - 1], xy[i]
        course = heading(x0, y0, x1, y1) if ok else None
        if ok and current is not None \
                and turn_angle(course, current['heading']) <= RUN_TURN_DEG:
            current['points'].append((x1, y1))
            current['length'] += length
            current['end'] = times[i]
            current['vx'] += x1 - x0
            current['vy'] += y1 - y0
            current['heading'] = heading(0.0, 0.0, current['vx'], current['vy'])
            continue
        if current is not None and current['length'] >= RUN_MIN_M:
            out.append(current)
        current = ({'points': [(x0, y0), (x1, y1)], 'heading': course,
                    'start': times[i - 1], 'end': times[i], 'length': length,
                    'vx': x1 - x0, 'vy': y1 - y0} if ok else None)
    if current is not None and current['length'] >= RUN_MIN_M:
        out.append(current)
    return out


def lateral_offset(first, second):
    """Медиана поперечного расстояния точек `second` до линии `first`.

    Меряется там, где прогоны идут рядом; None -- рядом они идут меньше
    RETURN_MIN_OVERLAP_M.
    """
    theta = math.radians(first['heading'])
    along = np.array([math.cos(theta), math.sin(theta)])
    across = np.array([-along[1], along[0]])
    p1 = np.asarray(first['points'], dtype=float)
    p2 = np.asarray(second['points'], dtype=float)
    a1, c1 = p1 @ along, p1 @ across
    a2, c2 = p2 @ along, p2 @ across
    low, high = max(a1.min(), a2.min()), min(a1.max(), a2.max())
    if high - low < RETURN_MIN_OVERLAP_M:
        return None
    near = (a2 >= low) & (a2 <= high)
    if not near.any():
        return None
    slope, intercept = np.polyfit(a1, c1, 1)
    return float(np.median(np.abs(c2[near] - (slope * a2[near] + intercept))))


def same_row_returns(xy, times, region):
    """(возвратов после разворота, из них по тем же рядам)."""
    inside = prep(region)
    kept = [run for run in runs(xy, times)
            if inside.contains(Point(run['points'][len(run['points']) // 2]))]
    returns = same = 0
    for first, second in zip(kept, kept[1:]):
        if turn_angle(first['heading'], second['heading']) < RETURN_MIN_DEG:
            continue
        if second['start'] - first['end'] > RETURN_MAX_GAP_S:
            continue
        offset = lateral_offset(first, second)
        if offset is None:
            continue
        returns += 1
        if offset < SAME_ROWS_M:
            same += 1
    return returns, same


def two_directions(segs, region):
    """(доля площади участков под обоими семействами курсов, угол между ними).

    Угол None -- второго семейства нет вовсе; доля 0 -- семейства слишком
    близки по курсу или во втором меньше FAMILY_MIN_SEGMENTS отрезков.
    """
    if len(segs) < 2 * FAMILY_MIN_SEGMENTS or region.area <= 0:
        return 0.0, None
    axis = np.array([seg.heading % 180.0 for seg in segs])
    weight = np.array([seg.length for seg in segs])
    bins = int(round(180.0 / HEADING_BIN_DEG))
    counts, _ = np.histogram(axis, bins=bins, range=(0.0, 180.0), weights=weight)
    first = (int(np.argmax(counts)) + 0.5) * HEADING_BIN_DEG
    in_first = np.array([axis_gap(value, first) <= FAMILY_HALF_DEG
                         for value in axis])
    rest = ~in_first
    if not rest.any():
        return 0.0, None
    counts, _ = np.histogram(axis[rest], bins=bins, range=(0.0, 180.0),
                             weights=weight[rest])
    second = (int(np.argmax(counts)) + 0.5) * HEADING_BIN_DEG
    apart = axis_gap(first, second)
    in_second = rest & np.array([axis_gap(value, second) <= FAMILY_HALF_DEG
                                 for value in axis])
    if apart < FAMILY_MIN_APART_DEG or int(in_second.sum()) < FAMILY_MIN_SEGMENTS:
        return 0.0, apart
    cover_first = unary_union([segs[i].line.buffer(FAMILY_BUFFER_M)
                               for i in np.nonzero(in_first)[0]])
    cover_second = unary_union([segs[i].line.buffer(FAMILY_BUFFER_M)
                                for i in np.nonzero(in_second)[0]])
    both = cover_first.intersection(cover_second).intersection(region).area
    return both / region.area, apart


def thinned_segments(xy, times, region, step_m=THIN_STEP_M):
    """Отрезки первой ступени K1: между точками, прореженными до step_m.

    Точка остаётся, если до предыдущей оставленной не меньше step_m. Отрезок
    -- между соседними оставленными точками, если между ними нет молчания
    дольше SEGMENT_MAX_GAP_S и длина не больше SEGMENT_MAX_M; в счёт идёт,
    если его середина внутри `region`.
    """
    inside = prep(region)
    out, last = [], None
    for i in range(len(xy)):
        if last is None or times[i] - times[i - 1] > SEGMENT_MAX_GAP_S:
            last = i
            continue
        (x0, y0), (x1, y1) = xy[last], xy[i]
        length = math.hypot(x1 - x0, y1 - y0)
        if length < step_m:
            continue
        if length <= SEGMENT_MAX_M and inside.contains(
                Point(0.5 * (x0 + x1), 0.5 * (y0 + y1))):
            out.append(Segment(LineString([(x0, y0), (x1, y1)]),
                               heading(x0, y0, x1, y1), length,
                               0.5 * (times[i] + times[last])))
        last = i
    return out


def row_share(segs):
    """Доля длины отрезков в двух семействах курсов -- мера `rows` K1.

    Курс -- по модулю 180 (туда и обратно по ряду -- одно семейство). Первое
    семейство -- не дальше FAMILY_HALF_DEG от середины самой тяжёлой по
    длине корзины HEADING_BIN_DEG, второе -- так же от самой тяжёлой среди
    остальных отрезков. None -- отрезков нет.
    """
    if not segs:
        return None
    axis = np.array([seg.heading % 180.0 for seg in segs])
    weight = np.array([seg.length for seg in segs])
    bins = int(round(180.0 / HEADING_BIN_DEG))
    counts, _ = np.histogram(axis, bins=bins, range=(0.0, 180.0), weights=weight)
    first = (int(np.argmax(counts)) + 0.5) * HEADING_BIN_DEG
    chosen = np.array([axis_gap(value, first) <= FAMILY_HALF_DEG
                       for value in axis])
    rest = ~chosen
    if rest.any():
        counts, _ = np.histogram(axis[rest], bins=bins, range=(0.0, 180.0),
                                 weights=weight[rest])
        second = (int(np.argmax(counts)) + 0.5) * HEADING_BIN_DEG
        chosen = chosen | (rest & np.array(
            [axis_gap(value, second) <= FAMILY_HALF_DEG for value in axis]))
    return float(weight[chosen].sum() / weight.sum())


def multiplicity(measured, t1):
    """(кратность, причина) правила K1 по признакам `measure_day` и T1.

    Порядок -- предрегистрация 22.10.2026 (§2.14): свидетельство на
    крупнейшем участке, работа рядами, повтор или два направления.
    """
    if (measured.get('largest_ha') or 0.0) < EVIDENCE_MIN_HA \
            or (measured.get('largest_km') or 0.0) < EVIDENCE_MIN_KM:
        return 1, K_NOT_JUDGED
    if measured.get('rows') is None or measured['rows'] < t1:
        return 1, K_NOT_ROWS
    if (measured.get('repeat') or 0.0) >= REPEAT_MIN_K:
        return 2, K_REPEAT
    if (measured.get('two_dirs') or 0.0) >= TWO_DIRS_MIN_K:
        return 2, K_TWO_DIRS
    return 1, K_ONE


def synthetic_double(points, sigma_m, seed):
    """Те же сутки, пройденные дважды: трек ещё раз, с плывущей ошибкой GPS.

    Копия идёт через SYNTH_GAP_S после последней точки суток -- окна и
    прогоны не перемешиваются с первым проходом. Ошибка -- авторегрессия
    SYNTH_AR с установившимся отклонением sigma_m по каждой оси, как в
    `gps/tests/test_multipass.py` (drifting_noise); метры переводятся в
    градусы у своей широты.
    """
    points = sorted(points)
    if not points:
        return []
    shift = points[-1][0] - points[0][0] + SYNTH_GAP_S
    rng = random.Random(seed)
    step = sigma_m * math.sqrt(1.0 - SYNTH_AR ** 2)
    dx = dy = 0.0
    copy = []
    for row in points:
        dx = SYNTH_AR * dx + rng.gauss(0.0, step)
        dy = SYNTH_AR * dy + rng.gauss(0.0, step)
        lat = row[2]
        lon_m = M_PER_DEG_LON_EQUATOR * math.cos(math.radians(lat))
        copy.append((row[0] + shift, row[1] + dx / lon_m,
                     lat + dy / M_PER_DEG_LAT) + tuple(row[3:]))
    return points + copy


def measure_day(points, sites=None):
    """Признаки одних машино-суток.

    points -- [(t, lon, lat, speed, ...)] одного объекта за местные сутки;
    sites -- его участки A7 (`gps.area.work_sites`), если уже посчитаны:
    иначе считаются здесь тем же способом, что в ночном расчёте.
    """
    points = sorted(points)
    if sites is None:
        sites, _ = work_sites(points)
    a7_ha = sum(site.area_ha for site in sites)
    result = {'a7_ha': round(a7_ha, 4), 'sites': len(sites), 'track_km': 0.0,
              'km_per_ha': None, 'repeat': None, 'returns': 0, 'same_rows': 0,
              'two_dirs': 0.0, 'two_dirs_angle': None, 'largest_ha': 0.0,
              'largest_km': 0.0, 'rows': None}
    if not sites:
        return result
    region = unary_union([site.polygon for site in sites])
    xy, times = work_track(points)
    segs = segments(xy, times, region)
    repeat, length = repeat_share(segs)
    returns, same = same_row_returns(xy, times, region)
    both, apart = two_directions(segs, region)
    biggest = max(sites, key=lambda site: site.area_ha)
    on_largest = segments(xy, times, biggest.polygon)
    rows = row_share(thinned_segments(xy, times, biggest.polygon))
    result.update(
        largest_ha=round(biggest.area_ha, 4),
        largest_km=round(sum(seg.length for seg in on_largest) / 1000.0, 3),
        rows=None if rows is None else round(rows, 4),
        track_km=round(length / 1000.0, 3),
        km_per_ha=round(length / 1000.0 / a7_ha, 3) if a7_ha > 0 else None,
        repeat=None if repeat is None else round(repeat, 4),
        returns=returns, same_rows=same, two_dirs=round(both, 4),
        two_dirs_angle=None if apart is None else round(apart, 1))
    return result
