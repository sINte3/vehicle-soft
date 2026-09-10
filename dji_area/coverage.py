# -*- coding: utf-8 -*-
"""dji_area/coverage.py -- покрытие по ПОДТВЕРЖДЁННОМУ применению (DRONE-AREA-1A).

Что здесь нового по сравнению с `useful-area-v2`. Движок v2
(``drone_collector/area_study.py``) остаётся ровно тем же: та же проекция, тот
же растр, то же объединение, та же двойная сетка с погрешностью. Меняется
ТОЛЬКО источник ответа на вопрос «был ли этот отрезок работой».

    v2:  ответ выводится ГЕОМЕТРИЧЕСКИ -- по курсу, длине прохода и
         принадлежности контуру. Сам код v2 честно предупреждает, что
         состояние насоса при этом не доказано.
    1A:  ответ читается из кадра V4 -- флаг распыления и расход, записанные
         бортом примерно раз в секунду вместе с координатой и шириной.

ЧТО ЭТОТ МОДУЛЬ НЕ УТВЕРЖДАЕТ. Ни одно число здесь не является площадью к
счёту заказчику и не является доказательством того, что препарат физически
долетел до земли. Ширина берётся ЗАПИСАННАЯ бортом; соответствие записанной
ширины реальной полосе осаждения (NY/T 3213-2023: граница по плотности не
менее 20 капель/см²) этим модулем не проверяется и остаётся открытым
вопросом до полевой калибровки. Поэтому результат называется
``V4_APPLICATION_COVERAGE_ESTIMATE``, а не ``BILLABLE_AREA``.

Запасной ширины нет. Кадр без ширины в полосу не превращается вовсе.
"""

import math

from drone_collector.area_study import (DEFAULT_PARAMS, Segment, SEG_GAP,
                                        SEG_STANDSTILL, SEG_WORK,
                                        coverage_once,
                                        coverage_with_uncertainty, plane_for)

# [REASON]: у v2 разрыва по расстоянию НЕТ ВОВСЕ, и это правильно -- там точки
# суть вершины упрощённой ломаной, где прямой проход в триста метров записан
# двумя точками. Здесь источник другой: кадр V4 -- это телеметрия примерно раз
# в секунду. Поэтому сто метров между соседними кадрами -- не проход, а
# пропуск записи, и полоса на нём была бы выдумана ровно так же, как её
# выдумывал бы v1 на телеметрии. Гейт физический, а не подобранный: предел
# скорости взят с запасом вдвое от максимальной скорости полёта T40.
MAX_FRAME_GAP_S = 3.0
MAX_GROUND_SPEED_MPS = 20.0

# Причины, которых нет у v2: там «не работа» решала геометрия, здесь --
# наблюдённое состояние применения. Значения намеренно НЕ добавлены в
# ``area_study.SEGMENT_REASONS``: движок v2 трогать не нужно, он проверяет
# только ``is_work`` плюс два особых случая, а всё прочее красит как
# «пролетел, но не вносил».
SEG_NO_APPLICATION = 'NO_APPLICATION'
SEG_APPLICATION_EDGE = 'APPLICATION_EDGE'
SEG_APPLICATION_UNKNOWN = 'APPLICATION_UNKNOWN'
SEG_NO_WIDTH = 'NO_WIDTH'


class ApplicationSegment(Segment):
    """Отрезок v2 плюс ширина, записанная бортом на этом отрезке.

    ``Segment`` объявлен со ``__slots__``, поэтому ширину нельзя просто
    навесить атрибутом -- и это правильно: слот заставляет назвать поле явно.
    """

    __slots__ = ('width_m',)

    def __init__(self, index, ax, ay, bx, by, width_m=None):
        Segment.__init__(self, index, ax, ay, bx, by)
        self.width_m = width_m

# Состояние канала применения для борта в периоде. Совпадает по смыслу с
# ``resolver.CH_*`` и повторяется здесь, чтобы модуль не тянул резолвер.
CH_INFORMATIVE = 'INFORMATIVE'

# Кадр считается применяющим ровно по тому же правилу, по которому
# ``v4.summarize_v4`` считает ``application_frames``. Одно правило в двух
# местах разошлось бы молча.
def frame_applies(frame):
    return (frame.get('spray_flag') or 0) > 0 or (frame.get('flow') or 0) > 0


def frame_width(frame):
    width = frame.get('width')
    if width is None or not isinstance(width, (int, float)):
        return None
    if isinstance(width, bool) or not math.isfinite(width) or width <= 0:
        return None
    return float(width)


class CoverageUnavailable(Exception):
    """Покрытие не считается. Причина названа, число не выдумывается."""

    def __init__(self, reason):
        Exception.__init__(self, reason)
        self.reason = reason


def _positioned(frames):
    return [f for f in frames
            if isinstance(f.get('lat'), (int, float))
            and isinstance(f.get('lng'), (int, float))
            and not isinstance(f.get('lat'), bool)
            and math.isfinite(f['lat']) and math.isfinite(f['lng'])]


def _dt_seconds(a, b):
    ta, tb = a.get('t'), b.get('t')
    if not isinstance(ta, (int, float)) or not isinstance(tb, (int, float)):
        return None
    if isinstance(ta, bool) or isinstance(tb, bool):
        return None
    return (tb - ta) / 1000.0


def _is_gap(length_m, dt_s):
    """Разрыв записи: время между кадрами не подтверждает пройденный путь."""
    if dt_s is None:
        # Без отметки времени скорость не проверить. Молча красить нельзя.
        return length_m > MAX_GROUND_SPEED_MPS * MAX_FRAME_GAP_S
    if dt_s <= 0:
        return True
    if dt_s > MAX_FRAME_GAP_S:
        return True
    return length_m / dt_s > MAX_GROUND_SPEED_MPS


def build_segments(frames, plane, channel_quality):
    """Кадры V4 -> отрезки с причиной, плюс отчёт о том, что отброшено.

    Отрезок между соседними кадрами объявляется работой, только если
    применение наблюдалось на ОБОИХ концах. Отрезок, на котором применение
    включилось или выключилось, попадает в ``APPLICATION_EDGE`` и в работу не
    входит: его половину не приписать ни туда, ни сюда, а его суммарная
    величина показывается отдельно, чтобы читатель видел цену этого выбора.
    """
    pts = _positioned(frames)
    if len(pts) < 2:
        raise CoverageUnavailable('FEWER_THAN_2_POSITIONED_FRAMES')

    known_channel = channel_quality == CH_INFORMATIVE
    segments = []
    for i in range(1, len(pts)):
        a, b = pts[i - 1], pts[i]
        ax, ay = plane.xy(a['lat'], a['lng'])
        bx, by = plane.xy(b['lat'], b['lng'])
        seg = ApplicationSegment(i - 1, ax, ay, bx, by)
        wa, wb = frame_width(a), frame_width(b)
        dt_s = _dt_seconds(a, b)
        if _is_gap(seg.length, dt_s):
            seg.reason = SEG_GAP
        elif not known_channel:
            seg.reason = SEG_APPLICATION_UNKNOWN
        elif seg.length <= 0:
            seg.reason = SEG_STANDSTILL
        elif wa is None or wb is None:
            # [REASON]: ширина -- множитель площади. Без неё полоса была бы
            # выдумана, а подстановка медианы уже признана в этом проекте
            # научной ошибкой. Отрезок остаётся в маршруте и в отчёте, но
            # не красится.
            seg.reason = SEG_NO_WIDTH
        else:
            ap_a, ap_b = frame_applies(a), frame_applies(b)
            if ap_a and ap_b:
                seg.reason = SEG_WORK
            elif ap_a or ap_b:
                seg.reason = SEG_APPLICATION_EDGE
            else:
                seg.reason = SEG_NO_APPLICATION
        # Ширина отрезка -- меньшая из двух: полоса не шире того, что борт
        # записал на обоих концах.
        seg.width_m = min(w for w in (wa, wb) if w is not None) \
            if (wa is not None and wb is not None) else None
        segments.append(seg)
    return segments


def _tracks_by_width(segments):
    """[(отрезки, полуширина)] -- по одной дорожке на каждое значение ширины.

    ``coverage_once`` берёт ОДНУ полуширину на дорожку, а борт меняет ширину
    в течение вылета. Группировка по значению ширины -- единственный способ
    отдать движку правду, не усредняя её.
    """
    groups = {}
    for seg in segments:
        width = getattr(seg, 'width_m', None)
        if width is None:
            continue
        groups.setdefault(round(width, 3), []).append(seg)
    return [(segs, width / 2.0) for width, segs in sorted(groups.items())]


def swath_integral_m2(segments):
    """S = сумма(ширина x длина) по отрезкам с подтверждённым применением.

    Это НЕ площадь поверхности: перекрытие повторного прохода войдёт в S
    дважды. Величина нужна ровно затем, чтобы сравнить её с объединением U и
    увидеть долю повторного покрытия.
    """
    total = 0.0
    for seg in segments:
        if seg.reason == SEG_WORK and getattr(seg, 'width_m', None):
            total += seg.length * seg.width_m
    return total


def _reason_lengths(segments):
    out = {}
    for seg in segments:
        out[seg.reason] = out.get(seg.reason, 0.0) + seg.length
    return out


def coverage_from_v4(frames, channel_quality, rings=None,
                     params=DEFAULT_PARAMS):
    """Оценка уникального покрытия по подтверждённому применению.

    ``frames`` -- кадры из ``v4.decode_v4(...).frames``.
    ``channel_quality`` -- INFORMATIVE / UNRELIABLE / UNKNOWN для этого борта.
    ``rings`` -- контур поля в координатах плоскости, либо None.

    Возвращает словарь компонентов. Единого «одного числа» здесь нет
    намеренно: уникальное покрытие, повторное покрытие, вынос за контур,
    холостой пролёт и неизвестное показываются раздельно.
    """
    pts = _positioned(frames)
    if len(pts) < 2:
        raise CoverageUnavailable('FEWER_THAN_2_POSITIONED_FRAMES')
    plane = plane_for([(f['lat'], f['lng']) for f in pts])
    segments = build_segments(frames, plane, channel_quality)

    work = [s for s in segments if s.reason == SEG_WORK]
    if not work:
        # Не ошибка: вылет мог быть целиком холостым. Но и не «0 га
        # обработано» без оговорки -- причина названа.
        reason = (SEG_APPLICATION_UNKNOWN
                  if channel_quality != CH_INFORMATIVE else 'NO_APPLICATION_FRAMES')
        return {'status': 'NO_CONFIRMED_APPLICATION', 'reason': reason,
                'segment_length_m': _reason_lengths(segments),
                'plane': plane}

    tracks = _tracks_by_width(segments)
    work_tracks = [([s for s in segs if s.reason == SEG_WORK], half)
                   for segs, half in tracks]
    work_tracks = [(segs, half) for segs, half in work_tracks if segs]

    fine, coarse, uncertainty = coverage_with_uncertainty(
        work_tracks, rings, params)
    # Всё, что пролетели: работа плюс холостое, на ОДНОЙ сетке -- чтобы
    # разность «пролетел минус внёс» не зависела от двух разных рамок.
    flown = coverage_once(tracks, rings, params, params.cell_m)

    unique_total_m2 = (fine.swath_work_ha or 0.0) * 10000.0
    integral_m2 = swath_integral_m2(segments)
    inside = fine.clipped_work_ha
    outside = (None if inside is None
               else round((fine.swath_work_ha or 0.0) - inside, 4))

    return {
        'status': 'ESTIMATE',
        'metric_name': 'V4_APPLICATION_COVERAGE_ESTIMATE',
        # Уникальная поверхность под подтверждённым применением.
        'unique_application_ha': fine.swath_work_ha,
        'unique_application_inside_field_ha': inside,
        'application_outside_field_ha': outside,
        # S - U: сколько гектаров пришлось на повторный проход. При
        # междурядье, равном учётной ширине, эта величина близка к нулю;
        # заметная величина -- признак перекрытия или возобновления.
        'swath_integral_ha': round(integral_m2 / 10000.0, 4),
        'repeated_application_ha': round(
            (integral_m2 - unique_total_m2) / 10000.0, 4),
        's_over_u': (round(integral_m2 / unique_total_m2, 4)
                     if unique_total_m2 > 0 else None),
        # Пролетели, но не вносили: подлёт, возврат, перелёт между полями.
        'flown_swath_ha': flown.swath_all_ha,
        'contour_ha': fine.contour_ha,
        'segment_length_m': _reason_lengths(segments),
        'uncertainty_percent': uncertainty,
        'cell_m': fine.cell_m,
        'coarsened': fine.coarsened,
        'coarse': coarse.as_dict(),
        'widths_used_m': sorted({round(getattr(s, 'width_m'), 3)
                                 for s in work
                                 if getattr(s, 'width_m', None)}),
        # Отрезки подтверждённого применения -- сырьё для карты evidence.
        # Отдаются как есть, чтобы вызывающий не пересобирал геометрию заново
        # и не получил вторую, слегка другую.
        'work_segments': work,
        'plane': plane,
    }
