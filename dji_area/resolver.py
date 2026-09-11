# -*- coding: utf-8 -*-
"""dji_area/resolver.py -- резолвер технической площади записи DJI.

Реализует нормативную таблицу решений спецификации
``VEHICLE_SOFT_DJI_AREA_PRODUCTION_SPEC.md`` §4 (модель
``dji-area-evidence-2026-09-08-final-1``). Вход -- доказательства ОДНОЙ
записи (RAW, сводка V4 с гейтами окна, маршрут, структурный экран, пересечение
интервалов, качество канала применения); выход -- статус, метод, уверенность,
техническая оценка и флаги аномалий.

ЧЕТЫРЕ ЗАПРЕТА, которые держит этот модуль (и тесты):

1. **Никакого auto-zero.** Кандидат структурного экрана без проверенного
   интервала счётчика не получает ноль. Ноль появляется ТОЛЬКО как
   ``controller_delta_area_m2 == 0`` при полном проверенном окне.
2. **Малый положительный прирост сохраняется**, даже в один квант
   (≈6.667 м²): ``PARTIAL_RECORDED_OVERSTATEMENT`` с D, не 0.
3. **Ведущий пропуск канала -- не измеренный ноль.** Без baseline полная
   разность интервала ``NULL``; ``counter_zero_default_delta_m2`` -- только
   диагностика. Случай 685264927 обязан дать ``BASELINE_UNKNOWN``.
4. **Отсутствие evidence -- не доказательство бездействия.** V4 нет -->
   RAW остаётся RAW (provisional) либо UNKNOWN; NULL никогда не 0.

Здесь нет ввода-вывода, Flask и базы.
"""

from dji_area import (AREA_ALGORITHM_VERSION, CHANNEL_CAPABILITY_REVISION,
                      STRUCTURAL_RULE_VERSION)
from dji_area.v4 import (
    BASELINE_UNKNOWN as _BASELINE_UNKNOWN_VALUE,
    COUNTER_QUANTUM_M2,
    WINDOW_BASELINE_UNKNOWN,
    WINDOW_CHANNEL_MISSING,
    WINDOW_GOOD,
    WINDOW_IDENTITY_ERROR,
    WINDOW_INCOMPLETE,
    WINDOW_NONMONOTONE,
    counter_endpoints_at_boundary,
    evaluate_window,
    is_exactly_flat,
)

# ─── Статусы площади ─────────────────────────────────────────────────────────

RAW_CORROBORATED = 'RAW_CORROBORATED'
RAW_CORROBORATED_QUALIFIED = 'RAW_CORROBORATED_QUALIFIED'
COUNTER_FLAT_RAW_OVERSTATED = 'COUNTER_FLAT_RAW_OVERSTATED'
PARTIAL_RECORDED_OVERSTATEMENT = 'PARTIAL_RECORDED_OVERSTATEMENT'
RAW_UNVERIFIED = 'RAW_UNVERIFIED'
UNKNOWN_SUSPECT = 'UNKNOWN_SUSPECT'
APPLICATION_WITHOUT_MEASURED_AREA = 'APPLICATION_WITHOUT_MEASURED_AREA'
COUNTER_ZERO = 'COUNTER_ZERO'
ZERO_RECORDED_UNVERIFIED = 'ZERO_RECORDED_UNVERIFIED'
CHANNEL_MISSING = 'CHANNEL_MISSING'
BASELINE_UNKNOWN = 'BASELINE_UNKNOWN'
COUNTER_RELATIONSHIP_OUTLIER = 'COUNTER_RELATIONSHIP_OUTLIER'
COUNTER_NONMONOTONE_REVIEW = 'COUNTER_NONMONOTONE_REVIEW'
OVERLAP_REVIEW = 'OVERLAP_REVIEW'

AREA_STATUSES = (
    RAW_CORROBORATED, RAW_CORROBORATED_QUALIFIED,
    COUNTER_FLAT_RAW_OVERSTATED, PARTIAL_RECORDED_OVERSTATEMENT,
    RAW_UNVERIFIED, UNKNOWN_SUSPECT, APPLICATION_WITHOUT_MEASURED_AREA,
    COUNTER_ZERO, ZERO_RECORDED_UNVERIFIED, CHANNEL_MISSING, BASELINE_UNKNOWN,
    COUNTER_RELATIONSHIP_OUTLIER, COUNTER_NONMONOTONE_REVIEW, OVERLAP_REVIEW,
)

# Статусы, чья оценка входит в ПРОВЕРЕННЫЙ (certified) подытог. Всё
# остальное -- provisional либо unresolved, и сумма с ними полным итогом не
# называется.
CERTIFIED_STATUSES = (RAW_CORROBORATED, COUNTER_FLAT_RAW_OVERSTATED,
                      PARTIAL_RECORDED_OVERSTATEMENT, COUNTER_ZERO)
PROVISIONAL_STATUSES = (RAW_CORROBORATED_QUALIFIED, RAW_UNVERIFIED)

# ─── Методы ──────────────────────────────────────────────────────────────────

M_RAW_WITH_VALIDATED_COUNTER = 'RAW_WITH_VALIDATED_COUNTER'
M_RAW_WITH_SUBWINDOW_CORROBORATION = 'RAW_WITH_SUBWINDOW_CORROBORATION'
M_VALIDATED_COUNTER_DELTA = 'VALIDATED_COUNTER_DELTA'
M_RAW_FALLBACK = 'RAW_FALLBACK'
M_NO_SAFE_CORRECTION = 'NO_SAFE_CORRECTION'
M_ACTIVITY_FLAG_ONLY = 'ACTIVITY_FLAG_ONLY'
M_RAW_ONLY = 'RAW_ONLY'
M_NO_COUNTER_MEASUREMENT = 'NO_COUNTER_MEASUREMENT'
M_NO_SAFE_INTERVAL_DELTA = 'NO_SAFE_INTERVAL_DELTA'
M_REVIEW_REQUIRED = 'REVIEW_REQUIRED'
M_UNRESOLVED_INTERVAL_OWNERSHIP = 'UNRESOLVED_INTERVAL_OWNERSHIP'

# ─── Уверенность (качественная, НЕ вероятность) ──────────────────────────────

C_HIGH = 'HIGH'
C_MEDIUM = 'MEDIUM'
C_LOW = 'LOW'
C_UNKNOWN = 'UNKNOWN'

# ─── Применение ──────────────────────────────────────────────────────────────

ACT_PRESENT = 'PRESENT'
ACT_NOT_OBSERVED = 'NOT_OBSERVED'
ACT_UNKNOWN = 'UNKNOWN'

CH_INFORMATIVE = 'INFORMATIVE'
CH_UNRELIABLE = 'UNRELIABLE'
CH_UNKNOWN = 'UNKNOWN'

EK_FLAG_OR_FLOW = 'FLAG_OR_FLOW'
EK_QUANTITY_ONLY = 'QUANTITY_ONLY'
EK_MULTIPLE = 'MULTIPLE'
EK_NONE = 'NONE'
EK_UNRELIABLE = 'UNRELIABLE'

# [REASON]: борт `3 Gijduvon` (1581F5742255T0C1L061) не пишет flow/flags даже
# на длинных нормальных вылетах (B2: четыре нормальных targets без флагов и
# flat 688669423). Отсутствие флагов на нём -- не «нет», а «канал
# неинформативен». Список -- явная версионируемая конфигурация, а не догадка
# по никнейму; расширяется только с evidence по борту и периоду.
KNOWN_UNRELIABLE_APPLICATION_HARDWARE = frozenset({'1581F5742255T0C1L061'})

# ─── Право на агрегирование ──────────────────────────────────────────────────

AGG_CERTIFIED = 'CERTIFIED'
AGG_PROVISIONAL = 'PROVISIONAL'
AGG_UNRESOLVED = 'UNRESOLVED'
AGG_EXCLUDED_OVERLAP = 'EXCLUDED_OVERLAP'

# ─── Диагностический допуск согласования RAW и D ─────────────────────────────

AGREEMENT_ABS_M2 = 20.0
AGREEMENT_REL = 0.01
# Порог «сильного несоответствия» (D < 0.1·A при A > 100) -- класс, а не
# разрешение обнулять.
PARTIAL_MAX_SHARE = 0.10
PARTIAL_MIN_RAW_M2 = 100.0


def raw_and_counter_agree(delta_m2, raw_m2):
    """abs(D - A) <= max(20 м², 1 % A). Диагностический допуск, не CI."""
    if delta_m2 is None or raw_m2 is None:
        return False
    return abs(delta_m2 - raw_m2) <= max(AGREEMENT_ABS_M2,
                                         AGREEMENT_REL * raw_m2)


# ─── Применение ──────────────────────────────────────────────────────────────

def channel_quality_for(hardware_id, informative_evidence):
    """Качество канала применения борта в периоде.

    ``informative_evidence`` -- True, если у этого борта в том же периоде есть
    хотя бы одна запись с положительными flag/flow кадрами (evidence
    способности канала по периоду, ревизия CHANNEL_CAPABILITY_REVISION).
    Известный плохой борт -- UNRELIABLE всегда.
    """
    if hardware_id in KNOWN_UNRELIABLE_APPLICATION_HARDWARE:
        return CH_UNRELIABLE
    if informative_evidence:
        return CH_INFORMATIVE
    return CH_UNKNOWN


def assess_application(summary, channel_quality):
    """(activity, evidence_kind, flags) по каналам 7.1/7.2/7.10.

    Только quantity (7.10) без flags/flow -- не доказательство распыления:
    возможен отложенный апдейт. Активность остаётся UNKNOWN, сохраняется
    отдельный флаг.
    """
    flags = []
    if summary is None:
        return ACT_UNKNOWN, EK_NONE, flags
    flag_or_flow = (summary.get('application_frames') or 0) > 0
    quantity_delta = summary.get('quantity_delta')
    quantity_up = quantity_delta is not None and quantity_delta > 0
    if flag_or_flow and quantity_up:
        return ACT_PRESENT, EK_MULTIPLE, flags
    if flag_or_flow:
        return ACT_PRESENT, EK_FLAG_OR_FLOW, flags
    if quantity_up:
        flags.append('QUANTITY_ONLY_INCREASE')
        return ACT_UNKNOWN, EK_QUANTITY_ONLY, flags
    if channel_quality == CH_INFORMATIVE:
        return ACT_NOT_OBSERVED, EK_NONE, flags
    flags.append('APPLICATION_CHANNEL_' + channel_quality)
    return ACT_UNKNOWN, EK_UNRELIABLE, flags


# ─── Результат ───────────────────────────────────────────────────────────────

class AreaDecision(object):
    """Решение резолвера по ОДНОЙ записи. Ровно то, что ложится в строку."""

    __slots__ = (
        'area_status', 'evidence_status', 'area_method', 'area_confidence',
        'raw_area_m2', 'raw_area_source',
        'corrected_recorded_area_m2', 'controller_delta_area_m2',
        'counter_observed_delta_m2', 'counter_zero_default_delta_m2',
        'counter_baseline_status', 'counter_window_quality',
        'window_reasons', 'anomaly_flags',
        'application_activity', 'application_channel_quality',
        'application_evidence_kind', 'application_without_area',
        'structural_candidate', 'structural_rule_version',
        'candidate_base_flight_id', 'bridge_flight_ids', 'boundary_gaps_s',
        'scalar_source_check',
        'overlap_group_id', 'aggregation_eligibility',
        'area_algorithm_version', 'channel_capability_revision',
    )

    def __init__(self, **kwargs):
        for name in self.__slots__:
            setattr(self, name, kwargs.get(name))
        if self.anomaly_flags is None:
            self.anomaly_flags = []
        if self.window_reasons is None:
            self.window_reasons = []

    def as_dict(self):
        return {name: getattr(self, name) for name in self.__slots__}


def _flag(flags, name):
    if name not in flags:
        flags.append(name)


def resolve_area(evidence):
    """Таблица решений. ``evidence`` -- словарь, см. ``EVIDENCE_KEYS``.

    Обязательные ключи: ``raw_area_m2`` (float или None), ``raw_area_source``.
    Необязательные: ``v4`` ({'summary': dict, 'identity_ok': bool}),
    ``route`` ({'identity_status': str}), ``structural`` (результат
    ``structural.screen``), ``overlap`` ({'group_id', 'conflict'}),
    ``channel_quality`` (INFORMATIVE/UNRELIABLE/UNKNOWN), ``hardware_id``.
    """
    raw = evidence.get('raw_area_m2')
    raw_source = evidence.get('raw_area_source')
    v4 = evidence.get('v4')
    route = evidence.get('route') or {}
    structural = evidence.get('structural') or {}
    overlap = evidence.get('overlap') or {}
    channel_quality = evidence.get('channel_quality') or CH_UNKNOWN

    flags = []
    summary = v4.get('summary') if v4 else None

    if raw is None:
        _flag(flags, 'RAW_MISSING')
    raw_zero = raw is not None and raw == 0
    raw_positive = raw is not None and raw > 0

    # ── Внешние признаки ─────────────────────────────────────────────────
    if route.get('identity_status') == 'ROUTE_IDENTITY_ERROR':
        _flag(flags, 'ROUTE_IDENTITY_ERROR')
    structural_candidate = bool(structural.get('candidate'))
    scalar_check = structural.get('scalar_source_check')
    if structural_candidate:
        _flag(flags, 'STRUCTURAL_RETAINED_CANDIDATE')
        if scalar_check:
            _flag(flags, 'RETAINED_SCALAR_SOURCE_MATCH')
    overlap_conflict = bool(overlap.get('conflict'))
    if overlap_conflict:
        _flag(flags, 'OVERLAP_INTERVAL')
    # [REASON]: «подозрительность» без V4 определяется только evidence
    # lifecycle: структурный кандидат с совпавшим скаляром либо конфликт
    # интервалов. Null width, равная площадь или короткая длительность сами
    # по себе подозрением не являются (спецификация §4).
    lifecycle_suspect = (structural_candidate and bool(scalar_check)) \
        or overlap_conflict

    # ── Окно и baseline ──────────────────────────────────────────────────
    window_quality = None
    baseline_status = None
    window_reasons = []
    if summary is not None:
        window_quality, baseline_status, window_reasons = evaluate_window(
            summary, identity_ok=v4.get('identity_ok', True))
    else:
        _flag(flags, 'V4_MISSING')

    # [REASON]: файл V4, снятый по ЧУЖОМУ пути, -- телеметрия другого вылета.
    # Спецификация §2 п. 2 («Конфликт identity запрещает delta в resolver»)
    # закрывает controller_delta_area_m2, и гейт окна это делает. Но оценка
    # применения считалась до ветвления, и при RAW=0 запись получала
    # APPLICATION_WITHOUT_MEASURED_AREA -- утверждение «работа была» на
    # основании файла, который сам резолвер только что признал чужим. Без
    # этого файла та же запись читается как ZERO_RECORDED_UNVERIFIED. Активность
    # обязана стать UNKNOWN, а не PRESENT.
    #
    # Диагностические counter_observed / counter_zero_default при этом
    # СОХРАНЯЮТСЯ: они принадлежат связанной ревизии, едут вместе с
    # v4_summary_id, окном IDENTITY_ERROR и флагом, и запрет спецификации на
    # них не распространяется. Стирать их значило бы потерять единственный
    # след того, что именно было захвачено.
    identity_conflict = summary is not None and not v4.get('identity_ok', True)
    if identity_conflict:
        _flag(flags, 'V4_IDENTITY_APPLICATION_EVIDENCE_EXCLUDED')

    observed_m2 = summary.get('counter_observed_delta_m2') if summary else None
    zero_default_m2 = (summary.get('counter_zero_default_delta_m2')
                       if summary else None)

    activity, evidence_kind, app_flags = assess_application(
        None if identity_conflict else summary, channel_quality)
    for name in app_flags:
        _flag(flags, name)

    controller_delta = None
    corrected = None
    status = None
    method = None
    confidence = None

    def fallback(bad_v4=False):
        """RAW без пригодного V4: provisional либо UNKNOWN.

        ``bad_v4`` -- V4 у записи ЕСТЬ, но он негоден (неполное окно,
        отсутствующий канал площади, чужая идентичность).

        [REASON]: строка «Bad/incomplete V4» решающей таблицы §4 фиксирует
        уверенность LOW, тогда как строка «RAW positive + V4 missing» даёт
        MEDIUM/LOW. Обе ветви приходили сюда и получали MEDIUM: запись с
        заведомо сломанным V4 подавалась ровно так же уверенно, как запись,
        у которой V4 просто нет. Число не меняется, меняется его цена.
        """
        if raw_positive or raw is None:
            if lifecycle_suspect:
                return UNKNOWN_SUSPECT, None, M_NO_SAFE_CORRECTION, C_LOW
            if raw is None:
                return UNKNOWN_SUSPECT, None, M_NO_SAFE_CORRECTION, C_LOW
            return (RAW_UNVERIFIED, raw, M_RAW_FALLBACK,
                    C_LOW if bad_v4 else C_MEDIUM)
        # RAW == 0
        if activity == ACT_PRESENT:
            return (APPLICATION_WITHOUT_MEASURED_AREA, None,
                    M_ACTIVITY_FLAG_ONLY, C_MEDIUM)
        return ZERO_RECORDED_UNVERIFIED, None, M_RAW_ONLY, C_LOW

    if summary is None:
        status, corrected, method, confidence = fallback()

    elif window_quality == WINDOW_IDENTITY_ERROR:
        _flag(flags, 'V4_IDENTITY_ERROR')
        if raw_positive or raw is None:
            status, corrected, method, confidence = (
                UNKNOWN_SUSPECT, None, M_NO_SAFE_CORRECTION, C_LOW)
        else:
            status, corrected, method, confidence = fallback(bad_v4=True)

    elif window_quality == WINDOW_CHANNEL_MISSING:
        _flag(flags, 'AREA_CHANNEL_ABSENT')
        if raw_zero:
            if activity == ACT_PRESENT:
                status, corrected, method, confidence = (
                    APPLICATION_WITHOUT_MEASURED_AREA, None,
                    M_ACTIVITY_FLAG_ONLY, C_MEDIUM)
            else:
                status, corrected, method, confidence = (
                    CHANNEL_MISSING, None, M_NO_COUNTER_MEASUREMENT,
                    C_UNKNOWN)
        else:
            status, corrected, method, confidence = fallback(bad_v4=True)

    elif window_quality == WINDOW_INCOMPLETE:
        _flag(flags, 'V4_WINDOW_INCOMPLETE')
        status, corrected, method, confidence = fallback(bad_v4=True)

    elif window_quality == WINDOW_NONMONOTONE:
        _flag(flags, 'COUNTER_NONMONOTONE')
        status, corrected, method, confidence = (
            COUNTER_NONMONOTONE_REVIEW, None, M_REVIEW_REQUIRED, C_LOW)

    else:  # WINDOW_GOOD
        start_ok, end_ok = counter_endpoints_at_boundary(summary)
        flat = is_exactly_flat(summary)
        if start_ok and end_ok:
            controller_delta = observed_m2
            if raw is None:
                status, corrected, method, confidence = (
                    UNKNOWN_SUSPECT, None, M_NO_SAFE_CORRECTION, C_LOW)
            elif raw_positive:
                if flat:
                    status, corrected, method, confidence = (
                        COUNTER_FLAT_RAW_OVERSTATED, 0.0,
                        M_VALIDATED_COUNTER_DELTA, C_HIGH)
                    if activity == ACT_PRESENT:
                        _flag(flags, 'APPLICATION_WITH_FLAT_COUNTER')
                elif raw_and_counter_agree(controller_delta, raw):
                    status, corrected, method, confidence = (
                        RAW_CORROBORATED, raw, M_RAW_WITH_VALIDATED_COUNTER,
                        C_HIGH)
                elif (controller_delta > 0 and raw > PARTIAL_MIN_RAW_M2
                      and controller_delta < PARTIAL_MAX_SHARE * raw):
                    status, corrected, method, confidence = (
                        PARTIAL_RECORDED_OVERSTATEMENT, controller_delta,
                        M_VALIDATED_COUNTER_DELTA, C_HIGH)
                    _flag(flags, 'SMALL_POSITIVE_INCREMENT_RETAINED')
                else:
                    status, corrected, method, confidence = (
                        COUNTER_RELATIONSHIP_OUTLIER, None,
                        M_REVIEW_REQUIRED, C_LOW)
                    _flag(flags, 'RAW_COUNTER_MAGNITUDE_MISMATCH')
            else:  # raw == 0
                if flat:
                    status, corrected, method, confidence = (
                        COUNTER_ZERO, 0.0, M_VALIDATED_COUNTER_DELTA, C_HIGH)
                else:
                    status, corrected, method, confidence = (
                        COUNTER_RELATIONSHIP_OUTLIER, None,
                        M_REVIEW_REQUIRED, C_LOW)
                    _flag(flags, 'RAW_ZERO_COUNTER_POSITIVE')
        else:
            # Полный интервал не измерен: только encoded subwindow.
            if not start_ok:
                _flag(flags, 'COUNTER_LEADING_OMISSION')
            if not end_ok:
                _flag(flags, 'COUNTER_TRAILING_OMISSION')
            if raw_positive and raw_and_counter_agree(observed_m2, raw):
                status, corrected, method, confidence = (
                    RAW_CORROBORATED_QUALIFIED, raw,
                    M_RAW_WITH_SUBWINDOW_CORROBORATION, C_MEDIUM)
            elif raw_zero:
                # Flat subwindow при RAW=0 не доказывает измеренный ноль
                # всего интервала; activity -- отдельно.
                if activity == ACT_PRESENT:
                    status, corrected, method, confidence = (
                        APPLICATION_WITHOUT_MEASURED_AREA, None,
                        M_ACTIVITY_FLAG_ONLY, C_MEDIUM)
                elif observed_m2 is not None and observed_m2 > 0:
                    status, corrected, method, confidence = (
                        COUNTER_RELATIONSHIP_OUTLIER, None,
                        M_REVIEW_REQUIRED, C_LOW)
                    _flag(flags, 'RAW_ZERO_COUNTER_POSITIVE')
                else:
                    status, corrected, method, confidence = (
                        ZERO_RECORDED_UNVERIFIED, None, M_RAW_ONLY, C_LOW)
                    _flag(flags, 'COUNTER_SUBWINDOW_FLAT')
            else:
                subwindow_flat = (observed_m2 is None
                                  or observed_m2 <= COUNTER_QUANTUM_M2)
                zero_default_agrees = raw_and_counter_agree(zero_default_m2,
                                                            raw)
                if subwindow_flat or zero_default_agrees:
                    # [REASON]: 685264927 -- первые кадры без счётчика, затем
                    # константа; zero-default даёт 9073 м² (== RAW), encoded
                    # subwindow 0. Первый закодированный скачок не доказывает
                    # новую работу, а совпадение zero-default с RAW не
                    # доказывает baseline. Полная разность NULL.
                    status, corrected, method, confidence = (
                        BASELINE_UNKNOWN, None, M_NO_SAFE_INTERVAL_DELTA,
                        C_UNKNOWN)
                    window_quality = WINDOW_BASELINE_UNKNOWN
                    _flag(flags, 'ZERO_DEFAULT_DELTA_IS_DIAGNOSTIC_ONLY')
                else:
                    # [REASON]: 692752823 -- encoded subwindow 2800 м² при
                    # RAW 2626 и zero-default 2813: ни одно соглашение не
                    # сходится, окно уже несёт больше работы, чем записано.
                    # Это residual величины, а не вопрос baseline: review, не
                    # выбор удобного числа.
                    status, corrected, method, confidence = (
                        COUNTER_RELATIONSHIP_OUTLIER, None,
                        M_REVIEW_REQUIRED, C_LOW)
                    _flag(flags, 'RAW_COUNTER_MAGNITUDE_MISMATCH')

    # ── Применение без измеренной площади ────────────────────────────────
    if activity == ACT_PRESENT:
        application_without_area = bool(
            raw_zero or (controller_delta is not None
                         and controller_delta == 0))
        if (evidence_kind == EK_QUANTITY_ONLY):
            application_without_area = None
    elif activity == ACT_NOT_OBSERVED:
        application_without_area = False
    else:
        application_without_area = None
    if (evidence_kind == EK_QUANTITY_ONLY
            and (raw_zero or (controller_delta is not None
                              and controller_delta == 0))):
        _flag(flags, 'QUANTITY_INCREASE_WITHOUT_AREA')

    # ── Пересечение интервалов: право на агрегирование ───────────────────
    evidence_status = status
    if overlap_conflict:
        status = OVERLAP_REVIEW
        corrected = None
        method = M_UNRESOLVED_INTERVAL_OWNERSHIP
        confidence = C_LOW
        eligibility = AGG_EXCLUDED_OVERLAP
    elif 'APPLICATION_WITH_FLAT_COUNTER' in flags:
        # [REASON]: жёсткий ноль здесь -- корректное утверждение о СЧЁТЧИКЕ
        # DJI, но не о земле. Счётчик не вырос, а применение наблюдалось: либо
        # площадь перенесена из прошлой записи, либо это повторный проход по
        # уже учтённой поверхности. Отличить их нечем, пока не посчитан
        # независимый footprint, поэтому запись не имеет права попасть в
        # ПРОВЕРЕННЫЙ подытог наравне с доказанным нулём: она уходит в
        # «недостаточно данных» вместе со своей RAW-экспозицией.
        eligibility = AGG_UNRESOLVED
    elif status in CERTIFIED_STATUSES:
        eligibility = AGG_CERTIFIED
    elif corrected is not None:
        eligibility = AGG_PROVISIONAL
    else:
        eligibility = AGG_UNRESOLVED

    return AreaDecision(
        area_status=status,
        evidence_status=evidence_status,
        area_method=method,
        area_confidence=confidence,
        raw_area_m2=raw,
        raw_area_source=raw_source,
        corrected_recorded_area_m2=corrected,
        controller_delta_area_m2=controller_delta,
        counter_observed_delta_m2=observed_m2,
        counter_zero_default_delta_m2=zero_default_m2,
        counter_baseline_status=baseline_status,
        counter_window_quality=window_quality,
        window_reasons=list(window_reasons),
        anomaly_flags=flags,
        application_activity=activity,
        application_channel_quality=channel_quality,
        application_evidence_kind=evidence_kind,
        application_without_area=application_without_area,
        # [REASON]: NULL значит «экран не выполнялся или неприменим»; False --
        # «выполнялся, кандидата нет». Слить их в False значило бы выдать
        # непроверенную запись за проверенную.
        structural_candidate=(None if not structural
                              or not structural.get('applicable', True)
                              else structural_candidate),
        structural_rule_version=STRUCTURAL_RULE_VERSION,
        candidate_base_flight_id=structural.get('base_flight_id'),
        bridge_flight_ids=list(structural.get('bridge_flight_ids') or []),
        boundary_gaps_s=list(structural.get('boundary_gaps_s') or []),
        scalar_source_check=scalar_check,
        overlap_group_id=overlap.get('group_id'),
        aggregation_eligibility=eligibility,
        area_algorithm_version=AREA_ALGORITHM_VERSION,
        channel_capability_revision=CHANNEL_CAPABILITY_REVISION,
    )


# Экспорт для тестов и документации.
_UNUSED = (_BASELINE_UNKNOWN_VALUE, WINDOW_GOOD)
