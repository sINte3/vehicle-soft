# -*- coding: utf-8 -*-
"""dji_area/accounting.py -- учётный класс записи поверх решения резолвера.

Здесь НЕТ классификатора. Что запись из себя представляет, решают
``structural.py`` (замороженный экран) и ``resolver.py`` (таблица решений по
счётчику V4). Этот модуль только ЧИТАЕТ их решение -- ``area_status``,
``aggregation_eligibility``, признаки экрана -- и раскладывает запись в один из
четырёх учётных классов целевой модели DJI-AREA-SIMPLIFY-001:

* ``NORMAL``             -> RAW;
* ``PHANTOM_PROVEN``     -> проверенный прирост счётчика, включая ноль;
* ``PHANTOM_STRUCTURAL`` -> сильный кандидат без проверенного интервала;
* ``REVIEW``             -> человеку, без автоматической корректировки.

ДВА ЗАПРЕТА, ради которых этот слой существует отдельно:

1. **Никакого auto-zero.** Кандидат экрана без проверенного интервала счётчика
   остаётся со своим RAW и уходит в нерешённую экспозицию. Ноль приходит только
   из ``corrected_recorded_area_m2`` резолвера, то есть из проверенного окна.
2. **RAW неизменяем.** Ни одна функция здесь RAW не переписывает; итог периода --
   это несколько раздельных величин, а не одно «исправленное» число.

Неизвестный статус -- не NORMAL, а REVIEW: новый статус резолвера обязан быть
разложен здесь явно, а не провалиться молча в «всё хорошо».

Здесь нет ввода-вывода, Flask и базы.
"""

import json

from dji_area import aggregate as agg
from dji_area import resolver as rs

ACCOUNTING_CLASSES_VERSION = 'dji-area-accounting-classes-1'

NORMAL = 'NORMAL'
PHANTOM_PROVEN = 'PHANTOM_PROVEN'
PHANTOM_STRUCTURAL = 'PHANTOM_STRUCTURAL'
REVIEW = 'REVIEW'
ACCOUNTING_CLASSES = (NORMAL, PHANTOM_PROVEN, PHANTOM_STRUCTURAL, REVIEW)

# ─── Причины (почему запись попала в класс) ──────────────────────────────────

R_COUNTER_CORROBORATED = 'COUNTER_CORROBORATED'
R_COUNTER_CORROBORATED_SUBWINDOW = 'COUNTER_CORROBORATED_SUBWINDOW'
R_RAW_UNVERIFIED_NO_V4 = 'RAW_UNVERIFIED_NO_USABLE_V4'
R_ZERO_RECORDED = 'ZERO_RECORDED'
R_CANDIDATE_REFUTED = 'STRUCTURAL_CANDIDATE_REFUTED_BY_COUNTER'
R_RETAINED_VALIDATED = 'COUNTER_VALIDATED_RETAINED'
R_RETAINED_VALIDATED_NOT_STRUCTURAL = 'COUNTER_VALIDATED_NOT_STRUCTURAL'
R_STRUCTURAL_NO_INTERVAL = 'STRUCTURAL_MATCH_WITHOUT_VALIDATED_INTERVAL'
R_APPLICATION_WITH_FLAT_COUNTER = 'APPLICATION_WITH_FLAT_COUNTER'
R_OVERSTATEMENT_NOT_CERTIFIED = 'OVERSTATEMENT_NOT_CERTIFIED'
R_INTERVAL_OVERLAP = 'INTERVAL_OVERLAP'
R_COUNTER_RELATIONSHIP = 'COUNTER_RELATIONSHIP_OUTLIER'
R_COUNTER_NONMONOTONE = 'COUNTER_NONMONOTONE'
R_BASELINE_UNKNOWN = 'BASELINE_UNKNOWN'
R_UNKNOWN_SUSPECT = 'UNKNOWN_SUSPECT'
R_APPLICATION_WITHOUT_AREA = 'APPLICATION_WITHOUT_MEASURED_AREA'
R_UNMAPPED_STATUS = 'UNMAPPED_STATUS'

_OVERSTATED = (rs.COUNTER_FLAT_RAW_OVERSTATED,
               rs.PARTIAL_RECORDED_OVERSTATEMENT)
_CORROBORATED = {rs.RAW_CORROBORATED: R_COUNTER_CORROBORATED,
                 rs.RAW_CORROBORATED_QUALIFIED:
                     R_COUNTER_CORROBORATED_SUBWINDOW}
_REVIEW_BY_STATUS = {
    rs.COUNTER_RELATIONSHIP_OUTLIER: R_COUNTER_RELATIONSHIP,
    rs.COUNTER_NONMONOTONE_REVIEW: R_COUNTER_NONMONOTONE,
    rs.BASELINE_UNKNOWN: R_BASELINE_UNKNOWN,
    rs.UNKNOWN_SUSPECT: R_UNKNOWN_SUSPECT,
    rs.APPLICATION_WITHOUT_MEASURED_AREA: R_APPLICATION_WITHOUT_AREA,
}
_NORMAL_BY_STATUS = {
    rs.RAW_UNVERIFIED: R_RAW_UNVERIFIED_NO_V4,
    rs.COUNTER_ZERO: R_ZERO_RECORDED,
    rs.ZERO_RECORDED_UNVERIFIED: R_ZERO_RECORDED,
    rs.CHANNEL_MISSING: R_ZERO_RECORDED,
}
# [REASON]: кандидат, у которого V4 ЕСТЬ и противоречит обоим прочтениям
# (не ноль и не RAW), -- не «сильный кандидат без проверки», а находка для
# человека. В PHANTOM_STRUCTURAL он выглядел бы ожидающим подтверждения,
# которое уже получено и оказалось иным.
_CANDIDATE_STAYS_REVIEW = (rs.COUNTER_RELATIONSHIP_OUTLIER,
                           rs.COUNTER_NONMONOTONE_REVIEW)


def _num(value):
    if value is None or isinstance(value, bool):
        return None
    return float(value)


def _flags(row):
    flags = row.get('anomaly_flags')
    if flags is None:
        text = row.get('anomaly_flags_json')
        if text:
            try:
                flags = json.loads(text)
            except ValueError:
                flags = []
    return list(flags or [])


def is_structural_match(row):
    """Кандидат замороженного экрана с совпавшим скаляром (lifecycle suspect)."""
    return (row.get('structural_candidate') in (True, 1)
            and row.get('scalar_source_check') in (True, 1))


def classify(row):
    """Учётный класс одной записи-расчёта.

    ``row`` -- словарь с ключами строки ``dji_area_calculations`` либо строки
    ``pipeline._flight_line``. Возвращает словарь: ``accounting_class``,
    ``reason``, ``raw_area_m2``, ``exposure_m2`` (RAW либо 0.0 при NULL),
    ``accounted_area_m2`` (None для нерешённых классов),
    ``validated_delta_m2`` и ``confirmed_overstatement_m2`` (только PROVEN).
    """
    status = row.get('area_status')
    eligibility = row.get('aggregation_eligibility')
    raw = _num(row.get('raw_area_m2'))
    corrected = _num(row.get('corrected_recorded_area_m2'))
    delta = _num(row.get('controller_delta_area_m2'))
    match = is_structural_match(row)

    out = {
        'accounting_class': None,
        'reason': None,
        'raw_area_m2': raw,
        'exposure_m2': raw if raw is not None else 0.0,
        'accounted_area_m2': None,
        'validated_delta_m2': None,
        'confirmed_overstatement_m2': None,
        'structural_match': match,
    }

    def done(cls, reason, accounted=None):
        out['accounting_class'] = cls
        out['reason'] = reason
        out['accounted_area_m2'] = accounted
        return out

    if status == rs.OVERLAP_REVIEW or eligibility == rs.AGG_EXCLUDED_OVERLAP:
        return done(REVIEW, R_INTERVAL_OVERLAP)

    if status in _OVERSTATED:
        # [REASON]: сам статус ещё не доказательство. impl-3 оставляет запись с
        # плоским счётчиком И наблюдённым применением нерешённой: ноль описывает
        # счётчик DJI, а не землю. В PROVEN идёт только то, что резолвер
        # допустил в ПРОВЕРЕННЫЙ подытог.
        if eligibility == rs.AGG_CERTIFIED and corrected is not None \
                and raw is not None:
            out['validated_delta_m2'] = delta if delta is not None else corrected
            out['confirmed_overstatement_m2'] = raw - corrected
            return done(PHANTOM_PROVEN,
                        R_RETAINED_VALIDATED if match
                        else R_RETAINED_VALIDATED_NOT_STRUCTURAL, corrected)
        if R_APPLICATION_WITH_FLAT_COUNTER in _flags(row):
            return done(REVIEW, R_APPLICATION_WITH_FLAT_COUNTER)
        return done(REVIEW, R_OVERSTATEMENT_NOT_CERTIFIED)

    if status in _CORROBORATED:
        # Счётчик подтвердил RAW. Если экран при этом назвал запись кандидатом,
        # прав счётчик: это промах экрана, и он обязан быть виден.
        return done(NORMAL, R_CANDIDATE_REFUTED if match
                    else _CORROBORATED[status], raw)

    if match and status not in _CANDIDATE_STAYS_REVIEW:
        # Никакого auto-zero: RAW остаётся, запись ждёт проверенного интервала.
        return done(PHANTOM_STRUCTURAL, R_STRUCTURAL_NO_INTERVAL)

    if status in _REVIEW_BY_STATUS:
        return done(REVIEW, _REVIEW_BY_STATUS[status])

    if status in _NORMAL_BY_STATUS:
        return done(NORMAL, _NORMAL_BY_STATUS[status], raw)

    return done(REVIEW, R_UNMAPPED_STATUS)


# ─── Итог периода: раздельные величины ───────────────────────────────────────

def empty_totals():
    return {
        'records': 0,
        'raw_sum_m2': 0.0,
        'raw_missing_records': 0,
        'class_records': {cls: 0 for cls in ACCOUNTING_CLASSES},
        'class_raw_m2': {cls: 0.0 for cls in ACCOUNTING_CLASSES},
        'reason_records': {},
        'proven_validated_delta_m2': 0.0,
        'confirmed_overstatement_m2': 0.0,
        'normal_counter_validated_records': 0,
        'normal_counter_validated_raw_m2': 0.0,
        'normal_unverified_records': 0,
        'normal_unverified_raw_m2': 0.0,
        'evidence': agg.empty_bucket(),
    }


def add_row(totals, row, decision=None):
    """Добавить запись в итог. ``decision`` -- результат ``classify(row)``."""
    decision = decision or classify(row)
    cls = decision['accounting_class']
    totals['records'] += 1
    raw = decision['raw_area_m2']
    if raw is None:
        totals['raw_missing_records'] += 1
    else:
        totals['raw_sum_m2'] += raw
    totals['class_records'][cls] += 1
    totals['class_raw_m2'][cls] += decision['exposure_m2']
    reason = decision['reason']
    totals['reason_records'][reason] = totals['reason_records'].get(reason, 0) + 1
    if cls == PHANTOM_PROVEN:
        totals['proven_validated_delta_m2'] += decision['validated_delta_m2']
        totals['confirmed_overstatement_m2'] += \
            decision['confirmed_overstatement_m2']
    elif cls == NORMAL:
        if reason in (R_COUNTER_CORROBORATED, R_COUNTER_CORROBORATED_SUBWINDOW,
                      R_CANDIDATE_REFUTED):
            totals['normal_counter_validated_records'] += 1
            totals['normal_counter_validated_raw_m2'] += decision['exposure_m2']
        else:
            totals['normal_unverified_records'] += 1
            totals['normal_unverified_raw_m2'] += decision['exposure_m2']
    # Подытоги certified / provisional считает существующий агрегатор, чтобы
    # «проверено» здесь и в отчёте /drones/area-evidence было одним числом.
    agg.add_row(totals['evidence'], row)
    return totals


def finalize(totals):
    """Производные и инварианты. Возвращает тот же словарь."""
    agg.finalize(totals['evidence'])
    class_raw = sum(totals['class_raw_m2'].values())
    totals['partition_holds'] = (
        sum(totals['class_records'].values()) == totals['records']
        and abs(class_raw - totals['raw_sum_m2']) <= 1e-6 * max(
            1.0, totals['raw_sum_m2']))
    totals['unresolved_exposure_m2'] = (totals['class_raw_m2'][PHANTOM_STRUCTURAL]
                                        + totals['class_raw_m2'][REVIEW])
    # [REASON]: справочная величина, а НЕ итог. Нерешённые записи сидят внутри
    # по своему RAW, поэтому это верхняя граница «после доказанных исключений»,
    # и называть её исправленной площадью нельзя.
    totals['raw_minus_confirmed_overstatement_m2'] = (
        totals['raw_sum_m2'] - totals['confirmed_overstatement_m2'])
    totals['certified_sum_m2'] = totals['evidence']['certified_sum_m2']
    totals['certified_records'] = totals['evidence']['certified_records']
    totals['provisional_sum_m2'] = totals['evidence']['provisional_sum_m2']
    totals['provisional_records'] = totals['evidence']['provisional_records']
    return totals


def summarize(rows, key_fn=None):
    """{'total': итог, 'by_key': {ключ: итог}} по строкам расчётов."""
    total = empty_totals()
    by_key = {}
    for row in rows:
        decision = classify(row)
        add_row(total, row, decision)
        if key_fn is not None:
            key = key_fn(row)
            bucket = by_key.get(key)
            if bucket is None:
                bucket = by_key[key] = empty_totals()
            add_row(bucket, row, decision)
    finalize(total)
    for bucket in by_key.values():
        finalize(bucket)
    return {'total': total, 'by_key': by_key}
