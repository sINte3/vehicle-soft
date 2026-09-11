# -*- coding: utf-8 -*-
"""dji_area/aggregate.py -- агрегация расчётов с явным происхождением.

Итог периода -- это НЕ одно число. Для каждой группы (день, борт, поле)
отдаются раздельно: RAW DJI, проверенный подытог счётчика, provisional
оценка, число записей по статусам, RAW-экспозиция нерешённых записей,
счётчики применения-без-площади и ненадёжного канала, распределение по
tiers поля и корзина «поле не определено». Сумма с UNKNOWN полным итогом
не называется.

Вход -- строки расчётов (словари с ключами таблицы
``dji_area_calculations`` + tier из ``dji_field_attributions``). Здесь нет
ввода-вывода, Flask и базы.
"""

from dji_area.resolver import (AGG_CERTIFIED, AGG_EXCLUDED_OVERLAP,
                               AGG_PROVISIONAL, AGG_UNRESOLVED, AREA_STATUSES,
                               CH_UNRELIABLE)
from dji_area.field import TIER5_UNKNOWN, TIERS


def _num(value):
    if value is None or isinstance(value, bool):
        return None
    return float(value)


def empty_bucket():
    return {
        'records': 0,
        'raw_sum_m2': 0.0,
        'raw_known_records': 0,
        'raw_missing_records': 0,
        'certified_sum_m2': 0.0,
        'certified_records': 0,
        'controller_delta_sum_m2': 0.0,
        'controller_delta_records': 0,
        'provisional_sum_m2': 0.0,
        'provisional_records': 0,
        'unresolved_records': 0,
        'unresolved_raw_exposure_m2': 0.0,
        'overlap_records': 0,
        'overlap_raw_exposure_m2': 0.0,
        'application_without_area_records': 0,
        'unreliable_channel_records': 0,
        'status_counts': {status: 0 for status in AREA_STATUSES},
        'tier_counts': {tier: 0 for tier in TIERS},
        'tier_raw_m2': {tier: 0.0 for tier in TIERS},
        'unassigned_raw_m2': 0.0,
        'unassigned_records': 0,
    }


def add_row(bucket, row):
    """Добавить одну запись-расчёт в корзину. Детерминировано, без округления."""
    bucket['records'] += 1
    raw = _num(row.get('raw_area_m2'))
    if raw is None:
        bucket['raw_missing_records'] += 1
    else:
        bucket['raw_known_records'] += 1
        bucket['raw_sum_m2'] += raw

    status = row.get('area_status')
    if status in bucket['status_counts']:
        bucket['status_counts'][status] += 1
    else:
        bucket['status_counts'][status or 'NONE'] = (
            bucket['status_counts'].get(status or 'NONE', 0) + 1)

    eligibility = row.get('aggregation_eligibility')
    corrected = _num(row.get('corrected_recorded_area_m2'))
    delta = _num(row.get('controller_delta_area_m2'))
    if eligibility == AGG_CERTIFIED and corrected is not None:
        bucket['certified_sum_m2'] += corrected
        bucket['certified_records'] += 1
    elif eligibility == AGG_PROVISIONAL and corrected is not None:
        bucket['provisional_sum_m2'] += corrected
        bucket['provisional_records'] += 1
    elif eligibility == AGG_EXCLUDED_OVERLAP:
        bucket['overlap_records'] += 1
        if raw is not None:
            bucket['overlap_raw_exposure_m2'] += raw
    else:
        bucket['unresolved_records'] += 1
        if raw is not None:
            bucket['unresolved_raw_exposure_m2'] += raw
    if delta is not None and eligibility == AGG_CERTIFIED:
        bucket['controller_delta_sum_m2'] += delta
        bucket['controller_delta_records'] += 1

    if row.get('application_without_area') is True:
        bucket['application_without_area_records'] += 1
    if row.get('application_channel_quality') == CH_UNRELIABLE:
        bucket['unreliable_channel_records'] += 1

    tier = row.get('field_attribution_tier') or TIER5_UNKNOWN
    if tier not in bucket['tier_counts']:
        tier = TIER5_UNKNOWN
    bucket['tier_counts'][tier] += 1
    if raw is not None:
        bucket['tier_raw_m2'][tier] += raw
    if tier == TIER5_UNKNOWN:
        bucket['unassigned_records'] += 1
        if raw is not None:
            bucket['unassigned_raw_m2'] += raw
    return bucket


def finalize(bucket):
    """Инварианты и производные. Возвращает тот же словарь.

    Держит: records = certified + provisional + unresolved + overlap;
    сумма tier_counts = records; unassigned входит в tier_counts[TIER5].
    """
    total = (bucket['certified_records'] + bucket['provisional_records']
             + bucket['unresolved_records'] + bucket['overlap_records'])
    bucket['eligibility_partition_holds'] = (total == bucket['records'])
    bucket['tier_partition_holds'] = (
        sum(bucket['tier_counts'].values()) == bucket['records'])
    # Полный итог НЕ вычисляется: certified + provisional -- это
    # «известная часть», нерешённое остаётся экспозицией.
    bucket['known_subtotal_m2'] = (bucket['certified_sum_m2']
                                   + bucket['provisional_sum_m2'])
    bucket['is_complete'] = (bucket['unresolved_records'] == 0
                             and bucket['overlap_records'] == 0
                             and bucket['raw_missing_records'] == 0)
    return bucket


def aggregate(rows, key_fn):
    """{key: bucket} по ``key_fn(row)``; плюс ключ ``'__total__'``."""
    buckets = {}
    total = empty_bucket()
    for row in rows:
        key = key_fn(row)
        bucket = buckets.get(key)
        if bucket is None:
            bucket = buckets[key] = empty_bucket()
        add_row(bucket, row)
        add_row(total, row)
    for bucket in buckets.values():
        finalize(bucket)
    finalize(total)
    buckets['__total__'] = total
    return buckets
