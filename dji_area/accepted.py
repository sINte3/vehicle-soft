# -*- coding: utf-8 -*-
"""dji_area/accepted.py -- принятая площадь вылета: один источник для всех отчётов.

DJI-AREA-ACCEPTED-PROPAGATION-001. Семантика «DJI RAW − доказанно исключённое
± решение администратора = принятая площадь» до этого инкремента жила только
внутри экрана «Контроль площади DJI», и связка, которая её даёт, была написана
ДВАЖДЫ: в `control_report.record_view` (экран, книга) и в
`control_store._auto_figures` (запись решения). Здесь она названа один раз:

    accounting.classify(расчёт)                 -> автоматический класс и его числа
    decisions.effective(класс, ..., решение, stale) -> эффективное состояние

и обе прежние точки, и рабочие отчёты модуля (вылеты, сводка, календарь,
ведомости против вылетов, расход раствора) берут числа отсюда. Ни класса, ни
решения этот модуль не выдумывает и не меняет: правило retained-следа, порог,
резолвер, модель решений и устаревание решения -- всё прежнее, из
`accounting.py`, `resolver.py` и `decisions.py`.

ТРИ ВЕЛИЧИНЫ ВЫЛЕТА, КОТОРЫЕ НЕЛЬЗЯ СМЕШИВАТЬ

    raw_m2       -- DJI RAW вылета так, как его хранят и показывают отчёты
                    модуля (`drone_flights.area_ha` × 10 000). Не меняется;
    accepted_m2  -- принятая площадь, ТОЛЬКО если у вылета есть текущий
                    расчёт Area Control; иначе None;
    excluded_m2  -- доказанно исключённое (автоматом или решением); None без
                    расчёта.

[REASON]: вылет без расчёта -- НЕ «принято = RAW». Запись, которую Area
Control не рассчитывал (всё, что раньше 01.03.2026, и свежие вылеты до
очередного цикла), не проверялась, и показать её принятой значило бы
выдать непроверенное за проверенное. Поэтому у неё отдельный статус
`NOT_CALCULATED`, принятая площадь пуста, а итоги считают такие записи
отдельно и говорят, сколько их и сколько RAW за ними стоит.

[REASON]: это НЕ `billable_area_m2` и не коммерческая площадь, а
техническая/операционная площадь контроля (решение владельца 29.09.2026).
Слова «счёт» и «оплата» здесь не звучат.

Нет ввода-вывода, Flask и базы. Пакетное чтение расчётов и решений --
`dji_area.control_store.accepted_for()`.
"""

from dji_area import accounting as acc
from dji_area import decisions as dec

ACCEPTED_VERSION = 'dji-area-accepted-1'
M2_PER_HA = 10000.0

# [REASON]: RAW вылета приходит из `drone_flights.area_ha` (га, float), RAW
# расчёта -- из ревизии списка (м²). Обратное умножение на 10 000 даёт
# погрешность порядка 1e-9 м²; сотая доля квадратного метра -- заведомо выше
# шума арифметики и заведомо ниже любой настоящей разницы записи DJI.
RAW_TOLERANCE_M2 = 0.01

# ─── Короткий статус для рабочих отчётов ─────────────────────────────────────
# Восемь эффективных состояний `decisions.STATES` -- для экрана контроля, где
# решают. Рабочему отчёту нужен один из пяти ответов; разложение выводится из
# групп `decisions`, а не перечисляется заново, чтобы новое состояние не
# провалилось молча в «принято».

ST_ACCEPTED = 'ACCEPTED'
ST_CORRECTED = 'CORRECTED'
ST_NEEDS_DECISION = 'NEEDS_DECISION'
ST_PENDING = 'PENDING'
ST_NOT_CALCULATED = 'NOT_CALCULATED'
SHORT_STATUSES = (ST_ACCEPTED, ST_CORRECTED, ST_NEEDS_DECISION, ST_PENDING,
                  ST_NOT_CALCULATED)
OPEN_STATUSES = (ST_NEEDS_DECISION, ST_PENDING)

# (ru, uz). Узбекский -- кириллицей.
STATUS_LABELS = {
    ST_ACCEPTED: ('принято', 'қабул қилинган'),
    ST_CORRECTED: ('скорректировано', 'тузатилган'),
    ST_NEEDS_DECISION: ('требует решения', 'қарор талаб қилинади'),
    ST_PENDING: ('ожидает доказательства', 'далил кутилмоқда'),
    ST_NOT_CALCULATED: ('не рассчитано', 'ҳисобланмаган'),
}
# Та же семантика цвета, что у состояний на экране контроля: корректировка --
# порядок (info), ожидание -- предупреждение, спор -- внимание. «Не
# рассчитано» -- пунктирная рамка без заливки: это отсутствие проверки, а не
# её результат.
STATUS_BADGES = {
    ST_ACCEPTED: '',
    ST_CORRECTED: 'vs-badge-info',
    ST_NEEDS_DECISION: 'vs-badge-danger',
    ST_PENDING: 'vs-badge-warning',
    ST_NOT_CALCULATED: 'is-outline',
}

# Полнота группы записей (итог дня, месяца, машины, периода).
COVERAGE_EMPTY = 'EMPTY'            # записей нет
COVERAGE_FULL = 'FULL'              # расчёт есть у каждой записи
COVERAGE_PARTIAL = 'PARTIAL'        # у части записей расчёта нет
COVERAGE_NONE = 'NOT_CALCULATED'    # расчёта нет ни у одной
COVERAGE_LABELS = {
    COVERAGE_EMPTY: ('нет вылетов', 'парвоз йўқ'),
    COVERAGE_FULL: ('рассчитано полностью', 'тўлиқ ҳисобланган'),
    COVERAGE_PARTIAL: ('рассчитано частично', 'қисман ҳисобланган'),
    COVERAGE_NONE: ('не рассчитано', 'ҳисобланмаган'),
}


# Колонки расчёта, которые читают `accounting.classify` и
# `decisions.decision_is_stale`. Пакетный читатель берёт ровно их, а не
# `SELECT *`: за месяц это тысячи строк, за всё время -- десятки тысяч.
# [REASON]: список держит тест, записывающий, какие ключи строки реально
# читает классификация на всей матрице статусов: новый ключ в
# `accounting.classify` без правки этого кортежа уронит его, а не превратится
# молча в None.
CALC_COLUMNS = (
    'id', 'flight_id', 'report_start_date', 'area_algorithm_version',
    'calculation_input_hash', 'raw_area_m2', 'corrected_recorded_area_m2',
    'controller_delta_area_m2', 'area_status', 'aggregation_eligibility',
    'anomaly_flags_json', 'structural_candidate', 'scalar_source_check',
)


def pick(pair, lang):
    return pair[0] if lang == 'ru' else pair[1]


def ha(value_m2):
    return None if value_m2 is None else float(value_m2) / M2_PER_HA


def short_status(state):
    """Один из пяти статусов по эффективному состоянию `decisions`.

    None -- расчёта нет. Неизвестное состояние -- «требует решения», а не
    «принято»: новый вариант обязан быть разложен здесь явно.
    """
    if state is None:
        return ST_NOT_CALCULATED
    if state in dec.CORRECTION_STATES:
        return ST_CORRECTED
    if state in dec.PENDING_STATES:
        return ST_PENDING
    if state in dec.HUMAN_STATES:
        return ST_NEEDS_DECISION
    if state in dec.STATES:
        return ST_ACCEPTED
    return ST_NEEDS_DECISION


# ─── Одна запись ─────────────────────────────────────────────────────────────

def auto_figures(calc):
    """(classify(calc), авто-принято м², авто-исключено м²).

    Автоматический слой: PHANTOM_PROVEN принимает проверенный прирост счётчика
    (0 или малый) и исключает остальное; все прочие классы -- RAW и 0.
    [REASON]: кандидат без проверенного интервала и спорная запись
    автоматически НЕ вычитаются (никакого auto-zero) -- это правило
    `accounting`, здесь оно только читается.
    """
    auto = acc.classify(calc)
    raw = auto['raw_area_m2']
    if auto['accounting_class'] == acc.PHANTOM_PROVEN:
        return (auto, auto['accounted_area_m2'],
                auto['confirmed_overstatement_m2'])
    return auto, raw, (0.0 if raw is not None else None)


def evaluate(calc, decision=None):
    """Эффективный результат записи, у которой ЕСТЬ текущий расчёт.

    ``calc`` -- строка `dji_area_calculations` (словарь); ``decision`` --
    действующее решение администратора (словарь) либо None. Устаревание
    решения -- ровно `decisions.decision_is_stale`, применение --
    `decisions.effective`: `ACCEPT_AUTO_RESULT`, принятое против прежнего
    расчёта, теряет силу, три других решения действуют с пометкой.
    """
    auto, auto_accepted, auto_excluded = auto_figures(calc)
    cls = auto['accounting_class']
    raw = auto['raw_area_m2']
    stale = dec.decision_is_stale(decision, calc)
    state, accepted, excluded, applied = dec.effective(
        cls, raw, auto_accepted, auto_excluded, decision, stale=stale)
    return {
        'calculated': True,
        'calc_raw_m2': raw,
        'accepted_m2': accepted,
        'excluded_m2': excluded,
        'state': state,
        'status': short_status(state),
        'is_open': state in dec.OPEN_STATES,
        'auto': auto,
        'auto_class': cls,
        'auto_reason': auto['reason'],
        'auto_accepted_m2': auto_accepted,
        'auto_excluded_m2': auto_excluded,
        'decision_id': decision.get('id') if decision else None,
        'decision_type': decision.get('decision_type') if decision else None,
        'decision_applied': applied,
        'decision_stale': bool(decision is not None and stale),
        'calculation_id': calc.get('id'),
    }


def not_calculated(raw_m2):
    """Вылет, для которого у Area Control нет текущего расчёта."""
    return {
        'calculated': False,
        'raw_m2': raw_m2,
        'calc_raw_m2': None,
        'raw_mismatch': False,
        'accepted_m2': None,
        'excluded_m2': None,
        'state': None,
        'status': ST_NOT_CALCULATED,
        'is_open': False,
        'auto': None,
        'auto_class': None,
        'auto_reason': None,
        'auto_accepted_m2': None,
        'auto_excluded_m2': None,
        'decision_id': None,
        'decision_type': None,
        'decision_applied': False,
        'decision_stale': False,
        'calculation_id': None,
    }


def for_flight(raw_m2, calc=None, decision=None):
    """RAW вылета рядом с его принятой площадью.

    ``raw_m2`` -- RAW модуля (`drone_flights.area_ha` × 10 000); ``calc`` --
    текущий расчёт либо None. Без расчёта -- `NOT_CALCULATED`, принятая пуста.

    [REASON]: RAW отчёта и RAW расчёта -- два прочтения одной записи DJI
    (строка вылета при приёме и ревизия списка при расчёте). Принятая площадь
    берётся из расчёта -- та же, что на экране контроля, -- а расхождение двух
    RAW не прячется в «исключено»: оно помечается `raw_mismatch`, и итоги
    называют число таких записей.
    """
    raw_m2 = None if raw_m2 is None else float(raw_m2)
    if calc is None:
        return not_calculated(raw_m2)
    out = evaluate(calc, decision)
    out['raw_m2'] = raw_m2
    calc_raw = out['calc_raw_m2']
    out['raw_mismatch'] = bool(
        calc_raw is not None and raw_m2 is not None
        and abs(calc_raw - raw_m2) > RAW_TOLERANCE_M2)
    return out


def label(item, lang):
    return pick(STATUS_LABELS[item['status']], lang)


def badge(item):
    return STATUS_BADGES[item['status']]


# ─── Итог группы записей ─────────────────────────────────────────────────────

def empty_totals():
    return {
        'records': 0,
        'raw_m2': 0.0,
        'calculated_records': 0,
        'calculated_raw_m2': 0.0,
        'accepted_m2': 0.0,
        'excluded_m2': 0.0,
        'accepted_missing_records': 0,
        'not_calculated_records': 0,
        'not_calculated_raw_m2': 0.0,
        'open_records': 0,
        'open_raw_m2': 0.0,
        'pending_records': 0,
        'needs_decision_records': 0,
        'corrected_records': 0,
        'decided_records': 0,
        'raw_mismatch_records': 0,
        'status_records': {status: 0 for status in SHORT_STATUSES},
    }


def add(totals, item):
    """Добавить вылет (результат `for_flight`) в итог. Возвращает итог.

    [REASON]: NULL принятой площади НЕ складывается как ноль. Вылет без
    расчёта уходит в `not_calculated_*`, принятая считается только по
    рассчитанным, и итог сам говорит, полон ли он (`coverage`).
    """
    totals['records'] += 1
    raw = item.get('raw_m2') or 0.0
    totals['raw_m2'] += raw
    totals['status_records'][item['status']] += 1
    if not item['calculated']:
        totals['not_calculated_records'] += 1
        totals['not_calculated_raw_m2'] += raw
        return totals
    totals['calculated_records'] += 1
    totals['calculated_raw_m2'] += raw
    if item['accepted_m2'] is None:
        totals['accepted_missing_records'] += 1
    else:
        totals['accepted_m2'] += item['accepted_m2']
    totals['excluded_m2'] += item['excluded_m2'] or 0.0
    if item['is_open']:
        totals['open_records'] += 1
        totals['open_raw_m2'] += item['calc_raw_m2'] or 0.0
    if item['status'] == ST_PENDING:
        totals['pending_records'] += 1
    elif item['status'] == ST_NEEDS_DECISION:
        totals['needs_decision_records'] += 1
    elif item['status'] == ST_CORRECTED:
        totals['corrected_records'] += 1
    if item['decision_applied']:
        totals['decided_records'] += 1
    if item.get('raw_mismatch'):
        totals['raw_mismatch_records'] += 1
    return totals


def finalize(totals):
    """Полнота и производные. Возвращает тот же словарь.

    ``accepted_full_m2`` -- принятая площадь группы, только когда расчёт есть
    у КАЖДОЙ записи (или записей нет вовсе); иначе None. Частичная сумма
    остаётся в ``accepted_m2`` и показывается только с подписью полноты.
    """
    if not totals['records']:
        coverage = COVERAGE_EMPTY
    elif not totals['not_calculated_records']:
        coverage = COVERAGE_FULL
    elif totals['calculated_records']:
        coverage = COVERAGE_PARTIAL
    else:
        coverage = COVERAGE_NONE
    totals['coverage'] = coverage
    totals['complete'] = coverage in (COVERAGE_FULL, COVERAGE_EMPTY) \
        and not totals['accepted_missing_records']
    totals['accepted_full_m2'] = totals['accepted_m2'] \
        if totals['complete'] else None
    totals['resolved'] = totals['complete'] and not totals['open_records']
    return totals


def summarize(items):
    """Итог по итерируемому результатов `for_flight`."""
    totals = empty_totals()
    for item in items:
        add(totals, item)
    return finalize(totals)


def group(items_with_keys):
    """{ключ: итог} по парам (ключ, результат `for_flight`)."""
    out = {}
    for key, item in items_with_keys:
        bucket = out.get(key)
        if bucket is None:
            bucket = out[key] = empty_totals()
        add(bucket, item)
    for bucket in out.values():
        finalize(bucket)
    return out
