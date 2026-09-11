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
        # [REASON]: прежний запас 20 x 3 = 60 м -- это ровно `gap_m` из v1,
        # то есть то самое правило, от которого модуль и уходит. Без отметки
        # времени скорость непроверяема, поэтому доверяется только шаг,
        # физически достижимый за ОДИН кадр номинальной секундной частоты.
        return length_m > MAX_GROUND_SPEED_MPS
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


# [REASON]: порога здесь нет НАМЕРЕННО, и это третья редакция этого места.
# Первая объявляла повтором любую положительную разность -- на поле без
# перекрытия она уходила в минус, и «повторное покрытие» получалось
# отрицательным. Вторая ввела оценку шума `длина x шаг сетки`; оппонент
# измерил, что она в десятки раз больше настоящей ошибки растра и ГЛУШИТ
# реальное перекрытие -- 0.23 га двойной обработки отдавались как `None`, --
# а сама формула тождественно равна подобранному правилу `S/U > 1 + cell/w`.
# Третья редакция признаёт причину: S считается аналитически (сумма
# длина x ширина), U -- по растру. Это РАЗНЫЕ методы, и их разность несёт
# методическую составляющую, величину которой мы честно не знаем. Поэтому
# отдаются обе величины, их разность и её доля от U, а решение «это
# перекрытие или это шум» принимает читатель, глядя на масштаб. Ради
# дискриминирующего опыта 1B это и нужно: там разница между 0.2 % и 100 %.


def _contour_status(rings, reasons):
    if rings:
        return 'CONTOUR_APPLIED'
    if reasons:
        return 'CONTOUR_REJECTED:' + ';'.join(reasons)
    return 'CONTOUR_ABSENT'


def _reason_lengths(segments):
    out = {}
    for seg in segments:
        out[seg.reason] = out.get(seg.reason, 0.0) + seg.length
    return out


def rings_for_plane(land_geometry_document, plane):
    """Контур поля в метрах ТОЙ ЖЕ плоскости, что и кадры.

    [REASON]: проекция строится по кадрам вылета, поэтому контур обязан
    проецироваться в неё же. Своя плоскость у контура дала бы фигуру,
    смещённую относительно полосы на десятки метров, и обрезка выдала бы
    уверенное неверное число. Годность полигона проверяет тот же код, что и
    приёмник контуров: самопересекающийся полигон молча не используется.
    """
    from drone_collector.area_study import rings_from_geojson
    return rings_from_geojson(land_geometry_document, plane)


def coverage_from_v4(frames, channel_quality, rings=None,
                     land_geometry_document=None, params=DEFAULT_PARAMS):
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
    contour_reasons = []
    contour_area_ha = None
    if rings is None and land_geometry_document is not None:
        rings, contour_area_ha, contour_reasons = rings_for_plane(
            land_geometry_document, plane)
    segments = build_segments(frames, plane, channel_quality)

    work = [s for s in segments if s.reason == SEG_WORK]
    if not work:
        # Не ошибка: вылет мог быть целиком холостым. Но и не «0 га
        # обработано» без оговорки -- причина названа.
        # [REASON]: причина обязана следовать из того, что произошло. Жёстко
        # написанное `NO_APPLICATION_FRAMES` врало, когда распыление шло на
        # каждом кадре, а полоса не строилась из-за отсутствующей ширины или
        # разрывов записи: forensic-разбор получал ложный след.
        lengths = _reason_lengths(segments)
        reason = (SEG_APPLICATION_UNKNOWN
                  if channel_quality != CH_INFORMATIVE
                  else max(lengths, key=lengths.get) if lengths
                  else 'NO_SEGMENTS')
        return {'status': 'NO_CONFIRMED_APPLICATION', 'reason': reason,
                'segment_length_m': lengths,
                'contour_status': _contour_status(rings, contour_reasons),
                'plane': plane}

    tracks = _tracks_by_width(segments)
    work_tracks = [([s for s in segs if s.reason == SEG_WORK], half)
                   for segs, half in tracks]
    work_tracks = [(segs, half) for segs, half in work_tracks if segs]

    fine, coarse, uncertainty = coverage_with_uncertainty(
        work_tracks, rings, params)
    # [REASON]: `coverage_once` строит рамку по тому, что закрашивает,
    # поэтому у `flown` она ШИРЕ, чем у `fine`, и шаг может быть огрублён
    # независимо. Значит «пролетел минус внёс» -- разность двух РАЗНЫХ
    # растров, и вычитать их напрямую нельзя. Обе величины отдаются
    # раздельно, разность здесь не считается.
    flown = coverage_once(tracks, rings, params, params.cell_m)

    # [REASON]: `or 0.0` превратил бы «не измерено» в «ноль». Если полоса не
    # посчитана, S/U и повтор не считаются вовсе.
    unique_total_m2 = (None if fine.swath_work_ha is None
                       else fine.swath_work_ha * 10000.0)
    integral_m2 = swath_integral_m2(segments)
    excess_m2 = (None if unique_total_m2 is None
                 else integral_m2 - unique_total_m2)
    # [REASON]: без контура «внутри» и «снаружи» не существует как величин.
    # Показать их нулями значило бы заявить, что весь вынос за поле равен
    # нулю, тогда как он просто не измерен.
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
        # S: аналитическая сумма длина x ширина по подтверждённым сегментам.
        'swath_integral_ha': round(integral_m2 / 10000.0, 4),
        # [REASON]: разность S и U на поле БЕЗ перекрытия уходит в минус
        # (измерено: -80 м2 на 12 проходах, -285 м2 на 94) -- методы разные.
        # Отрицательное «повторное покрытие» -- бессмыслица, показывать её
        # как величину нельзя. Поэтому разность отдаётся как есть под своим
        # именем, а «повторным покрытием» называется только её положительная
        # часть. Порога между шумом и перекрытием здесь НЕТ намеренно: см.
        # блок перед `_contour_status` о том, почему прежняя оценка
        # дискретизации была снята.
        's_minus_u_ha': (None if excess_m2 is None
                         else round(excess_m2 / 10000.0, 4)),
        # Положительная часть S - U. Ниже -- её доля от U: именно она
        # отличает методический шум (доли процента) от настоящего повторного
        # прохода (десятки процентов), и именно она читается, а не гектары.
        'repeated_application_ha': (
            None if excess_m2 is None or excess_m2 <= 0
            else round(excess_m2 / 10000.0, 4)),
        'repeated_share_of_unique': (
            None if not unique_total_m2 or excess_m2 is None
            else round(excess_m2 / unique_total_m2, 4)),
        'method_note': ('S is an analytic sum(width x length); U is a raster '
                        'union. Their difference carries a method component '
                        'of unknown size and is not gated here.'),
        's_over_u': (round(integral_m2 / unique_total_m2, 4)
                     if unique_total_m2 else None),
        # [REASON]: это объединение ВСЕГО закрашенного, работа включена, а
        # не «холостой пролёт». Прежняя подпись врала владельцу: на сплошь
        # обработанном проходе обе величины совпадают, и читатель заключил
        # бы, что холостого хода было столько же, сколько работы. Холостая
        # часть -- отдельным полем и только как разность на ОДНОЙ сетке.
        'painted_swath_total_ha': flown.swath_all_ha,
        'flown_not_applied_ha': (
            None if (flown.swath_all_ha is None or fine.swath_work_ha is None
                     or flown.cell_m != fine.cell_m)
            else round(flown.swath_all_ha - fine.swath_work_ha, 4)),
        'contour_ha': fine.contour_ha,
        'contour_area_ha_declared': contour_area_ha,
        'contour_status': _contour_status(rings, contour_reasons),
        'segment_length_m': _reason_lengths(segments),
        'cell_m': fine.cell_m,
        'coarsened': fine.coarsened,
        'coarse': coarse.as_dict(),
        'thresholds': {'max_frame_gap_s': MAX_FRAME_GAP_S,
                       'max_ground_speed_mps': MAX_GROUND_SPEED_MPS,
                       'cell_m': params.cell_m,
                       'width_rule': 'min_of_two_endpoints',
                       'work_rule': 'application_on_both_endpoints'},
        'uncertainty_percent': uncertainty,
        'widths_used_m': sorted({round(getattr(s, 'width_m'), 3)
                                 for s in work
                                 if getattr(s, 'width_m', None)}),
        # Отрезки подтверждённого применения -- сырьё для карты evidence.
        # Отдаются как есть, чтобы вызывающий не пересобирал геометрию заново
        # и не получил вторую, слегка другую.
        'work_segments': work,
        'plane': plane,
    }
