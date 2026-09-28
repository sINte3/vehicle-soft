# -*- coding: utf-8 -*-
"""dji_area/footprint.py -- след распыления записи: только для доказательства.

DJI-AREA-RETAINED-FOOTPRINT-001. След -- путь борта на кадрах ПРИМЕНЕНИЯ,
умноженный на ширину полосы. Здесь он служит одному: независимо подтвердить,
что собственная работа записи НЕ объясняет её RAW. Площадью обработки он не
является и в `corrected_recorded_area_m2` не пишется никогда.

[REASON]: путь x ширина -- не уникальная площадь. Калибровка
(`docs/DJI_AREA_FOOTPRINT_CALIBRATION_001.md`) нашла настоящую работу, у
которой след в 77 раз больше RAW: борт повторно поливал уже учтённую землю, а
счётчик DJI повтор не считает. Поэтому след годится только как верхняя оценка
того, что запись МОГЛА обработать, а решение принимается по консервативному
следу с фиксированной огибающей ширины, а не по ширине самой записи.

Правила чтения, все в сторону отказа:

* кадр применения -- флаг распыления (7.1) ИЛИ положительный расход (7.2),
  тот же признак, что `application_frames` сводки V4;
* весь маршрут не читается: в след входит шаг ``k-1 -> k`` только если в
  кадре ``k-1`` шло применение;
* каждый такой шаг обязан быть наблюдаем: время и координаты обоих кадров
  закодированы, 0 < dt <= WINDOW_MAX_DT_S. Иначе шаг ``unobserved``, и
  решение по записи не принимается (fail closed). Кадр применения, последний
  в записи, шага не требует: после него записи нет;
* ширина для РЕШЕНИЯ -- фиксированная огибающая FOOTPRINT_WIDTH_ENVELOPE_M.
  Пропавшая ширина решения не меняет. Наблюдённая положительная ширина выше
  огибающей (или не число) -- отказ: огибающая больше не огибает, нужна
  перекалибровка, а не автоматическое решение. Фактические ширины
  возвращаются отдельно, только для диагностики.

Здесь нет ввода-вывода, Flask и базы.
"""

import math

from dji_area import RETAINED_FOOTPRINT_RULE_VERSION
from dji_area.v4 import WINDOW_MAX_DT_S, _distance_m

# Замороженные параметры правила (production-калибровка 27.09.2026): порог
# настоящих работ A1 при самой узкой ширине 0,274644, одна десятая -- 0,0275;
# принят более строгий 0,027. Наибольшая наблюдённая ширина парка -- 11,2 м;
# огибающая 12,0 м. Динамически из базы НЕ вычисляются.
FOOTPRINT_TO_RAW_MAX = 0.027
FOOTPRINT_WIDTH_ENVELOPE_M = 12.0


def is_application_frame(frame):
    """Флаг распыления ИЛИ положительный расход -- один признак применения."""
    return (frame.get('spray_flag') or 0) > 0 or (frame.get('flow') or 0) > 0


def _step(frames, k):
    """(метры, наблюдаем ли) шага ``k-1 -> k``."""
    prev, cur = frames[k - 1], frames[k]
    if prev.get('t') is None or cur.get('t') is None:
        return None, False
    dt = (cur['t'] - prev['t']) / 1000.0
    if dt <= 0 or dt > WINDOW_MAX_DT_S:
        return None, False
    distance = _distance_m(prev, cur)
    if distance is None:
        return None, False
    return distance, True


def application_footprint(frames):
    """След применения одной записи. Чистая функция, детерминирована.

    ``frames`` -- кадры ``v4.decode_v4(...).frames``. Возвращает словарь:
    ``application_frames``, ``steps_needed``, ``steps_unobserved``,
    ``application_path_m``, ``conservative_footprint_m2`` (путь x огибающая;
    None, если есть ненаблюдаемый шаг), ``width_over_envelope`` (наблюдалась
    положительная ширина выше огибающей или не число) и диагностические
    ``width_min_m`` / ``width_max_m``.
    """
    n = len(frames)
    application = 0
    needed = 0
    unobserved = 0
    path = 0.0
    for i, frame in enumerate(frames):
        if not is_application_frame(frame):
            continue
        application += 1
        if i == n - 1:
            continue
        needed += 1
        distance, observed = _step(frames, i + 1)
        if not observed:
            unobserved += 1
            continue
        path += distance
    widths = [f['width'] for f in frames if f.get('width') is not None]
    over = any(not math.isfinite(w) or w > FOOTPRINT_WIDTH_ENVELOPE_M
               for w in widths)
    positive = [w for w in widths if math.isfinite(w) and w > 0]
    return {
        'rule_version': RETAINED_FOOTPRINT_RULE_VERSION,
        'application_frames': application,
        'steps_needed': needed,
        'steps_unobserved': unobserved,
        'application_path_m': path,
        'conservative_footprint_m2': (path * FOOTPRINT_WIDTH_ENVELOPE_M
                                      if unobserved == 0 else None),
        'width_over_envelope': over,
        'width_min_m': min(positive) if positive else None,
        'width_max_m': max(positive) if positive else None,
    }


def negligible_footprint(conservative_footprint_m2, raw_m2):
    """Консервативный след не больше FOOTPRINT_TO_RAW_MAX от RAW.

    Граница включена: ровно 0,027 -- ещё «пренебрежимо». Сравнение идёт
    умножением, а не делением, чтобы граница не зависела от округления
    частного.
    """
    if conservative_footprint_m2 is None or raw_m2 is None or raw_m2 <= 0:
        return False
    return conservative_footprint_m2 <= FOOTPRINT_TO_RAW_MAX * raw_m2
