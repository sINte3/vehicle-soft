# -*- coding: utf-8 -*-
"""ops/pilot_useful_area_001/pilot_report.py -- безопасный итог пилота.

DRONE-USEFUL-AREA-PILOT-001. Собирает улики шести шагов в ОДИН JSON и ОДИН
Markdown, выносит машинный вердикт GO / ADJUST / REJECT со стабильными кодами
причин и сам проверяет получившийся отчёт на приватные значения.

ЧТО ЗДЕСЬ ГЛАВНОЕ. Отчёт строится по БЕЛОМУ СПИСКУ полей: ключ, которого нет
в `ALLOWED_FIELDS`, в отчёт не попадает и делает сборку отказом. Чёрный список
(«вырезать координаты») ловит то, о чём подумали; белый список ловит и то, о
чём не подумали, -- а улики приезжают с трёх машин и однажды привезут поле,
которого сегодня не существует.

ЧЕГО ЗДЕСЬ НЕТ И НЕ БУДЕТ: координат, точек маршрута, `dji_flight_id`, uuid
контуров, названий полей заказчика, cookies, токенов, подписей, `request_id`
и содержимого приватного capture.

КАЖДЫЙ КОНВЕРТ УЛИКИ ПРОВЕРЯЕТСЯ. Улики приезжают с трёх машин и пишутся
пятью скриптами. Конверт сверяется по всем полям сразу: `kit`, версия,
`evidence_kind`, `run_id`, `target_day`, ИЗМЕРЕННЫЙ `kit_sha`, `product_sha` и
метка времени, а метки ещё и по порядку между собой. Расхождение любого поля
-- REJECT: улики разных запусков, сложенные вместе, дают отчёт, который
выглядит безупречно и описывает прогон, которого не было.

ВЕРДИКТ `GO` НЕ ВЫДАЁТСЯ БЕЗ РЕШЕНИЯ ВЛАДЕЛЬЦА. Порог доли работ без числа и
допустимое отклонение от площади DJI -- бизнес-правила, а выдумывать их
устав запрещает. Пока владелец не назвал оба, верхний возможный вердикт --
`TECHNICAL_GO`: тракт исправен, разрешения на production он не даёт и
сопровождается кодом `BUSINESS_DECISION_REQUIRED`.

ТРИ ПОКАЗАТЕЛЯ ЗАДАНИЯ НЕ ВЫЧИСЛЯЮТСЯ, И ЭТО НАЗВАНО ПРЯМО. Схема
`DRONES_USEFUL_AREA_001` хранит `work_segments` (рабочие отрезки) на работу,
но НЕ хранит ни числа отрезков холостого перелёта, ни признака «вылет целиком
холостой», ни `mission_state` на вылет. Посчитать их можно только новой
колонкой или вторым разбором геометрии -- то есть новой функцией продукта,
которая этим макроэтапом запрещена. Поля остаются `null` со стабильным кодом
`NOT_RECORDED_BY_SCHEMA`, а рядом стоят ближайшие ЧЕСТНЫЕ величины на уровне
работы. Выдуманного числа здесь нет.

Запуск:

  & "C:\\Program Files\\Python314\\python.exe" ops\\pilot_useful_area_001\\pilot_report.py --preflight ... --deploy ... --collect ... --recalc ... --staging-snapshot ... --out-json ... --out-md ...

Коды возврата: 0 -- отчёт собран; 1 -- ошибка ввода; 3 -- отчёт не прошёл
проверку на приватные данные (файлы всё равно записаны -- их надо увидеть,
чтобы починить, -- но вердикт принудительно REJECT).
"""

import argparse
import json
import os
import re
import sys

from datetime import datetime

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)

import pilot_common as common  # noqa: E402

NOT_RECORDED = 'NOT_RECORDED_BY_SCHEMA'

# ─── Белый список полей отчёта ──────────────────────────────────────────────
#
# Плоские имена: отчёт намеренно плоский, чтобы этот список читался целиком
# одним взглядом и чтобы проверка не зависела от глубины вложенности.
ALLOWED_FIELDS = (
    'kit', 'kit_version', 'generated_utc', 'target_day',
    'run_id', 'kit_sha', 'product_sha',
    'envelope_problems', 'evidence_order_problems',
    'deploy_phase', 'backup_verified', 'service_started',
    'smoke_test_ok', 'smoke_test_status', 'smoke_test_path',
    'owner_share_threshold', 'owner_dji_delta_percent',
    'production_rollout_authorised', 'business_decision_required',
    'coverage_fingerprint_sha256', 'dry_run_wrote_nothing',
    'probe_request_failures', 'probe_pending_requests',
    'staging_url', 'evidence_present', 'evidence_missing',
    'flights_received', 'routes_received', 'works_formed',
    'ready_estimate', 'partial_data', 'data_unavailable',
    'contour_ambiguous', 'contour_not_matched', 'route_invalid',
    'dji_area_ha', 'ready_useful_area_ha', 'delta_ha', 'delta_percent',
    'fully_idle_flights_excluded', 'fully_idle_flights_excluded_note',
    'works_with_zero_work_segments',
    'mixed_flights', 'mixed_flights_note', 'works_with_mixed_mission_state',
    'work_segments', 'idle_segments', 'idle_segments_note',
    'works_without_confirmed_width', 'works_with_unresolved_contour',
    'works_without_number', 'works_without_number_share',
    'recalculation_seconds',
    'verdict', 'verdict_reasons', 'verdict_notes', 'conditions',
    'privacy_scan_passed', 'privacy_violations',
    'area_ha_fingerprints', 'area_ha_unchanged',
    'integrity_ok', 'migration_on_copy_ok', 'migration_on_staging_ok',
    'staging_backup_recorded', 'staging_sha_before', 'staging_sha_after',
    'recalc_runs', 'collect_counters',
)

# Ключи, которым РАЗРЕШЕНО нести длинную шестнадцатеричную строку.
HEX_ALLOWED_KEYS = ('kit_sha', 'product_sha', 'staging_sha_before',
                    'staging_sha_after', 'area_ha_fingerprints', 'sha256',
                    'backup_sha256', 'coverage_fingerprint_sha256')

UUID_RE = re.compile(r'\b[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-'
                     r'[0-9a-fA-F]{4}-[0-9a-fA-F]{12}\b')
# [REASON]: координата в этом проекте -- десятичные градусы с семью знаками
# (`routes.COORDINATE_DECIMALS`). Пять знаков и больше -- уже не площадь и не
# доля: площади округляются до четырёх, доли -- до четырёх.
COORDINATE_RE = re.compile(r'-?\b\d{1,3}\.\d{5,}\b')
LONG_HEX_RE = re.compile(r'\b[0-9a-f]{40,}\b', re.IGNORECASE)
SECRET_WORD_RE = re.compile(
    r'(?i)\b(cookie|cookies|authorization|bearer|request[_-]?id|'
    r'x-auth|signature|storage_state|set-cookie)\b')


class ReportError(Exception):
    """Отчёт собрать нельзя."""


# ─── Чтение улик ────────────────────────────────────────────────────────────

def load_evidence(path):
    try:
        document = common.read_evidence(path)
    except common.ProbeError as exc:
        raise ReportError(str(exc))
    if not isinstance(document, dict):
        raise ReportError('%s does not hold a JSON object' % path)
    return document


def payload_of(document):
    """Полезная нагрузка улики. Отсутствующая улика -- пустой словарь.

    [REASON]: отсутствующая улика обязана давать REJECT, а не падение.
    Отчёт собирают в конце длинного дня, и трассировка стека вместо вердикта
    -- это ещё один час на то, чтобы понять, что просто не донесли файл.
    """
    if not isinstance(document, dict):
        return {}
    payload = document.get('payload')
    return payload if isinstance(payload, dict) else {}


def _number(value, default=None):
    if isinstance(value, bool) or value is None:
        return default
    if isinstance(value, (int, float)):
        return value
    return default


def _flag(value):
    """Строгое приведение к bool: отсутствующее -- НЕ истина."""
    return value is True


def counters_of(collect):
    return (payload_of(collect).get('counters') or {})


# ─── Условия вердикта ───────────────────────────────────────────────────────
#
# Каждое условие -- (код, значение, что оно означает). Код стабилен: он
# уезжает в отчёт и по нему разговаривают. Формулировку менять можно, код --
# нет.

def build_conditions(preflight, deploy, collect, recalc_runs, staging):
    """Обязательные условия. Любое нарушенное делает вердикт REJECT."""
    pre = payload_of(preflight)
    dep = payload_of(deploy)
    col = payload_of(collect)
    snap = payload_of(staging)

    coverage = snap.get('coverage', {}) or {}
    routes = snap.get('routes', {}) or {}
    counters = col.get('counters', {}) or {}

    dry = _run_by_label(recalc_runs, 'dry-run')
    apply1 = _run_by_label(recalc_runs, 'apply-1')
    apply2 = _run_by_label(recalc_runs, 'apply-2')

    fingerprints = collect_fingerprints(preflight, deploy, staging)
    area_unchanged = len(set(fingerprints.values())) <= 1 and bool(fingerprints)

    works = _number(coverage.get('works'), 0)

    conditions = [
        ('DEPLOY_MANIFEST_IS_DONE', dep.get('phase') == 'done',
         'the deploy manifest reached phase "done"; any other phase means the '
         'deploy stopped somewhere and the pilot is not on a deployed staging'),
        ('BACKUP_CREATED_AND_VERIFIED',
         bool(dep.get('backup_path')) and bool(dep.get('backup_sha256'))
         and _flag(dep.get('backup_verified')),
         'a staging backup was written, hashed and its integrity checked '
         'BEFORE anything changed'),
        ('STAGING_SERVICE_STARTED', _flag(dep.get('service_started')),
         'the staging service came back up after the migration'),
        ('SMOKE_TEST_OK', _flag(dep.get('smoke_test_ok')),
         'the staging smoke test hit %s and got exactly one of %s, following '
         'redirects only inside the staging authority'
         % (common.SMOKE_PATH,
            ', '.join(str(code) for code in common.SMOKE_ALLOWED_STATUS))),
        ('DRY_RUN_WROTE_NOTHING', _flag(_dry_run_wrote_nothing(recalc_runs)),
         'the coverage table was byte-identical before and after the dry run, '
         'compared over every row and every column'),
        ('MIGRATION_ON_COPY_OK', _flag(pre.get('migration_on_copy_ok')),
         'the migration applied cleanly to an isolated copy of production, '
         'twice, with both tables, five indexes and a registry row'),
        ('MIGRATION_ON_STAGING_OK', _flag(dep.get('migration_on_staging_ok')),
         'the migration applied on staging and the registry row is there'),
        ('INTEGRITY_OK',
         _flag((snap.get('integrity') or {}).get('integrity_ok')),
         'PRAGMA integrity_check on the staging database returned ok'),
        ('AREA_HA_UNCHANGED', area_unchanged,
         'every recorded drone_flights.area_ha fingerprint is the same value'),
        ('LIVE_ROUTE_DECODE_OK',
         _number(counters.get('collect_decode_failures'), 1) == 0
         and _number(counters.get('collect_routes_captured'), 0) > 0
         and _number(counters.get('collect_bodies_captured'), 0) > 0,
         'route-decode-2 parsed every captured body and captured at least one'),
        ('OPERATOR_ANSWERED', _flag(counters.get('probe_operator_answered')),
         'the operator confirmed the map view'),
        ('DRAIN_COMPLETED', _flag(counters.get('probe_drained')),
         'route traffic had settled before the browser closed'),
        ('NO_OBSERVATION_ERRORS',
         _number(counters.get('probe_errors'), 1) == 0
         and _number(counters.get('collect_capture_errors'), 1) == 0,
         'no observation error and no capture error was recorded'),
        ('NO_UNFINISHED_ROUTE_REQUESTS',
         _flag(col.get('no_unfinished_route_requests')),
         'no route request was left pending and none failed'),
        ('NO_RESPONSE_OVER_CAP',
         _number(counters.get('probe_skipped_over_cap'), 1) == 0,
         'no observation was dropped by the size cap'),
        ('ALL_OBSERVATIONS_CONFIRMED',
         _number(counters.get('probe_observations'), 0) > 0
         and (_number(counters.get('probe_confirmed'), -1)
              == _number(counters.get('probe_observations'), 0)),
         'every observed route response was confirmed'),
        ('ID_SETS_MATCHED',
         _number(counters.get('exit'), 1) == 0
         and _flag(counters.get('collect_live_confirmed')),
         'the requested and returned id sets matched (the collector exits 16 '
         'when they do not, so exit 0 with a confirmed verdict is the proof)'),
        ('BATCH_FULLY_ACCEPTED',
         _flag(counters.get('collect_batch_accepted'))
         and _number(counters.get('collect_errors'), 1) == 0
         and _number(counters.get('collect_unlinked'), 1) == 0
         and _flag(col.get('ingest_counters_balance')),
         'staging accepted the whole batch: no error, no unlinked route, and '
         'seen = new + updated + unchanged'),
        ('QUEUE_CLOSED',
         _number(counters.get('collect_left_pending'), 1) == 0
         and _number(counters.get('collect_envelopes_sent'), 0) > 0,
         'every sent envelope left the queue and none stayed pending'),
        ('PERIOD_IS_THE_TARGET_DAY',
         all(_flag(run.get('period_is_the_target_day'))
             for run in (dry, apply1, apply2) if run)
         and _flag(routes.get('routes_outside_target_day_is_zero'))
         and _number(snap.get('coverage_rows_outside_target_day'), 1) == 0,
         'every recalculation ran on the target day only, no accepted route '
         'belongs to another day and no coverage row was written outside it'),
        ('DRY_RUN_AND_APPLY_AGREE',
         bool(apply1) and _flag(apply1.get('outputs_agree')),
         'the dry run and the apply produced the same counters and the same '
         'READY total'),
        ('SECOND_APPLY_IDEMPOTENT', _second_apply_is_idempotent(apply2, works),
         'the repeated apply inserted 0, updated 0, deleted 0 and counted '
         'every row as unchanged'),
        ('NO_ROUTE_INVALID',
         _number((coverage.get('by_status') or {}).get('ROUTE_INVALID'), 1) == 0,
         'not one stored route failed to read back as geometry'),
        ('ONLY_READY_IN_TOTAL',
         _flag(coverage.get('only_ready_carries_a_number'))
         and _totals_agree(coverage, apply1),
         'only READY_ESTIMATE works carry a number, and the stored total '
         'equals the total the recalculation reported'),
        ('WORKS_WERE_PRODUCED', works > 0,
         'the day produced at least one work to judge'),
    ]
    return [{'code': code, 'passed': bool(value), 'means': means}
            for code, value, means in conditions]


def _dry_run_wrote_nothing(runs):
    """Улика сухого прогона несёт полный отпечаток до и после.

    [REASON]: раньше это условие держалось числом строк. Число строк не
    меняется, когда строку переписали, -- а переписанная строка и есть запись.
    """
    dry = _run_by_label(runs, 'dry-run')
    if not dry:
        return False
    return _flag(dry.get('wrote_nothing'))


def _run_by_label(runs, label):
    for run in runs:
        if payload_of(run).get('label') == label:
            return payload_of(run)
    return None


def _second_apply_is_idempotent(apply2, works):
    if not apply2:
        return False
    summary = apply2.get('summary', {}) or {}
    return (_number(summary.get('inserted'), 1) == 0
            and _number(summary.get('updated'), 1) == 0
            and _number(summary.get('deleted'), 1) == 0
            and _number(summary.get('unchanged'), -1) == works
            and _flag(apply2.get('outputs_agree')))


def _totals_agree(coverage, apply1):
    """Сумма в базе и сумма, названная пересчётом, -- одно число."""
    if not apply1:
        return False
    stored = _number(coverage.get('ready_useful_area_ha'))
    reported = _number((apply1.get('summary') or {}).get('ready_area_ha'))
    if stored is None or reported is None:
        return False
    return abs(float(stored) - float(reported)) <= 1e-4


def collect_fingerprints(preflight, deploy, staging):
    """Все записанные отпечатки area_ha под понятными именами."""
    found = {}
    pre = payload_of(preflight)
    dep = payload_of(deploy)
    snap = payload_of(staging)
    for name, value in (
            ('production_copy_before_migration',
             (pre.get('area_ha_before') or {}).get('sha256')),
            ('production_copy_after_migration',
             (pre.get('area_ha_after') or {}).get('sha256')),
            ('staging_before_migration',
             (dep.get('area_ha_before') or {}).get('sha256')),
            ('staging_after_migration',
             (dep.get('area_ha_after') or {}).get('sha256')),
            ('staging_after_recalculation',
             (snap.get('area_ha') or {}).get('sha256'))):
        if value:
            found[name] = value
    return found


# ─── Вердикт ────────────────────────────────────────────────────────────────

def decide(conditions, coverage, owner_threshold, dji_delta_percent,
           dji_delta_limit, privacy_ok, envelope_problems=()):
    """GO / TECHNICAL_GO / ADJUST / REJECT и стабильные коды причин.

    Порядок жёсткий: сначала конверты улик, потом обязательные условия, и
    только потом -- бизнес-пороги.

    [REASON]: `GO` -- это разрешение катить дальше, и выдать его на пороге,
    который выбрала сессия, значит выдать решение владельца за своё. Устав
    запрещает выдумывать бизнес-правила прямо. Поэтому, пока владелец не
    назвал ОБА правила -- долю работ без числа и допустимое отклонение от
    площади DJI, -- верхний возможный вердикт `TECHNICAL_GO`: тракт исправен,
    решение не принято, production не разрешён.
    """
    failed = [item['code'] for item in conditions if not item['passed']]
    if envelope_problems:
        failed = ['EVIDENCE_DOES_NOT_BELONG_TO_ONE_RUN'] + failed
    if not privacy_ok:
        failed = failed + ['REPORT_CONTAINS_PRIVATE_VALUES']
    if failed:
        return 'REJECT', failed

    share = _number(coverage.get('works_without_number_share'))
    missing_rules = []
    if owner_threshold is None:
        missing_rules.append('OWNER_SHARE_THRESHOLD_NOT_SET')
    if dji_delta_limit is None:
        missing_rules.append('OWNER_DJI_DELTA_RULE_NOT_SET')

    breaches = []
    if (owner_threshold is not None and share is not None
            and share > owner_threshold):
        breaches.append('WORKS_WITHOUT_NUMBER_SHARE_ABOVE_OWNER_THRESHOLD')
    if (dji_delta_limit is not None and dji_delta_percent is not None
            and abs(dji_delta_percent) > dji_delta_limit):
        breaches.append('DJI_DELTA_ABOVE_OWNER_LIMIT')
    if breaches:
        return 'ADJUST', breaches

    if missing_rules:
        return 'TECHNICAL_GO', (['ALL_MANDATORY_CONDITIONS_HELD',
                                 'BUSINESS_DECISION_REQUIRED',
                                 'PRODUCTION_ROLLOUT_NOT_AUTHORISED']
                                + missing_rules)
    return 'GO', ['ALL_MANDATORY_CONDITIONS_HELD',
                  'OWNER_RULES_SATISFIED']


# ─── Проверка отчёта на приватные значения ──────────────────────────────────

def scan_for_private_values(report, markdown):
    """Нарушения приватности в СОБРАННОМ отчёте. Пустой список -- чисто.

    Три сети, и они ловят разное:
      1. белый список полей -- всё, чего в отчёте быть не должно, включая то,
         о чём здесь не подумали;
      2. вид значения -- uuid, координата, длинная шестнадцатеричная строка;
      3. слова секретов в тексте Markdown.
    """
    violations = []

    for key in sorted(report):
        if key not in ALLOWED_FIELDS:
            violations.append({'code': 'UNDECLARED_FIELD', 'where': key})

    for key, value in sorted(report.items()):
        for text in _strings_of(value):
            if UUID_RE.search(text):
                violations.append({'code': 'UUID_IN_REPORT', 'where': key})
            if COORDINATE_RE.search(text):
                violations.append({'code': 'COORDINATE_LIKE_VALUE',
                                   'where': key})
            if (LONG_HEX_RE.search(text)
                    and not _hex_is_allowed(key, report)):
                violations.append({'code': 'UNEXPECTED_LONG_HEX',
                                   'where': key})
            if SECRET_WORD_RE.search(text):
                violations.append({'code': 'SECRET_WORD_IN_REPORT',
                                   'where': key})
        for number in _numbers_of(value):
            if _looks_like_a_coordinate(number):
                violations.append({'code': 'COORDINATE_LIKE_NUMBER',
                                   'where': key})

    text = str(markdown or '')
    for pattern, code in ((UUID_RE, 'UUID_IN_MARKDOWN'),
                          (COORDINATE_RE, 'COORDINATE_LIKE_IN_MARKDOWN'),
                          (SECRET_WORD_RE, 'SECRET_WORD_IN_MARKDOWN')):
        if pattern.search(text):
            violations.append({'code': code, 'where': 'markdown'})

    seen = []
    for item in violations:
        if item not in seen:
            seen.append(item)
    return seen


def _hex_is_allowed(key, report):
    if key in HEX_ALLOWED_KEYS:
        return True
    if key == 'area_ha_fingerprints':
        return True
    return False


def _looks_like_a_coordinate(number):
    """Число похоже на десятичный градус? Площади округлены до 4 знаков."""
    if isinstance(number, bool) or not isinstance(number, float):
        return False
    text = repr(float(number))
    if '.' not in text or 'e' in text or 'E' in text:
        return False
    return len(text.split('.', 1)[1]) >= 5


def _strings_of(value):
    if isinstance(value, str):
        yield value
    elif isinstance(value, dict):
        for key, item in value.items():
            yield str(key)
            for found in _strings_of(item):
                yield found
    elif isinstance(value, (list, tuple)):
        for item in value:
            for found in _strings_of(item):
                yield found


def _numbers_of(value):
    if isinstance(value, bool):
        return
    if isinstance(value, (int, float)):
        yield value
    elif isinstance(value, dict):
        for item in value.values():
            for found in _numbers_of(item):
                yield found
    elif isinstance(value, (list, tuple)):
        for item in value:
            for found in _numbers_of(item):
                yield found


# ─── Конверты: все улики одного запуска, или ни одной ───────────────────────

EXPECTED_KINDS = (
    ('preflight', 'preflight'),
    ('deploy', 'deploy'),
    ('collect', 'collect:summary'),
    ('recalc:dry-run', 'recalc:dry-run'),
    ('recalc:apply-1', 'recalc:apply-1'),
    ('recalc:apply-2', 'recalc:apply-2'),
    ('staging_snapshot', 'db-probe:snapshot'),
)


def validate_evidence_set(documents, run_id, kit_sha):
    """Каждый конверт -- свой, и все они про один запуск.

    Возвращает список кодов. Пустой -- набор согласован.

    [REASON]: без этой проверки отчёт складывал улики, ни разу не спросив, от
    одного ли они прогона. Два прогона одного дня -- и предполёт первого лёг
    бы рядом с пересчётом второго; каждая строка отчёта была бы верной, а
    отчёт в целом -- про прогон, которого не было.
    """
    problems = []
    for name, kind in EXPECTED_KINDS:
        document = documents.get(name)
        if document is None:
            problems.append('EVIDENCE_ABSENT:%s' % name)
            continue
        for issue in common.validate_envelope(document, kind, run_id=run_id,
                                              kit_sha=kit_sha):
            problems.append('%s:%s' % (issue, name))

    ordered = [(name, documents.get(name)) for name, _kind in EXPECTED_KINDS
               if documents.get(name) is not None]
    problems.extend(common.check_time_order(ordered))
    return problems


# ─── Сборка отчёта ──────────────────────────────────────────────────────────

def build_report(preflight, deploy, collect, recalc_runs, staging,
                 recalculation_seconds, owner_threshold, dji_delta_limit,
                 missing_evidence, run_id=None, kit_sha=None):
    """Плоский отчёт: только объявленные поля, только счётчики и площади."""
    snap = payload_of(staging)
    coverage = snap.get('coverage', {}) or {}
    routes = snap.get('routes', {}) or {}
    by_status = coverage.get('by_status', {}) or {}
    col = payload_of(collect)
    dep = payload_of(deploy)
    pre = payload_of(preflight)

    dji = _number(coverage.get('dji_area_ha'), 0.0)
    ready_area = _number(coverage.get('ready_useful_area_ha'), 0.0)
    delta = round(float(ready_area) - float(dji), 4)
    delta_percent = (round(delta * 100.0 / float(dji), 2)
                     if dji else None)

    fingerprints = collect_fingerprints(preflight, deploy, staging)
    conditions = build_conditions(preflight, deploy, collect, recalc_runs,
                                  staging)

    documents = {'preflight': preflight, 'deploy': deploy, 'collect': collect,
                 'staging_snapshot': staging}
    for label in ('dry-run', 'apply-1', 'apply-2'):
        for run in recalc_runs:
            if payload_of(run).get('label') == label:
                documents['recalc:%s' % label] = run
    measured_run_id = run_id or (deploy or {}).get('run_id')
    measured_kit_sha = kit_sha or (deploy or {}).get('kit_sha')
    envelope_problems = validate_evidence_set(documents, measured_run_id,
                                              measured_kit_sha)

    dry = _run_by_label(recalc_runs, 'dry-run')
    report = {
        'kit': common.KIT_ID,
        'kit_version': common.KIT_VERSION,
        'generated_utc': datetime.utcnow().strftime('%Y-%m-%dT%H:%M:%SZ'),
        # ИЗМЕРЕННЫЕ ревизии, а не зашитые константы. Отчёт, печатающий
        # константу, доказывает лишь то, что константа записана в файле.
        'run_id': measured_run_id,
        'kit_sha': measured_kit_sha,
        'product_sha': (deploy or {}).get('product_sha'),
        'envelope_problems': envelope_problems,
        'evidence_order_problems': [item for item in envelope_problems
                                    if item.startswith('OUT_OF_ORDER')],
        'deploy_phase': dep.get('phase'),
        'backup_verified': _flag(dep.get('backup_verified')),
        'service_started': _flag(dep.get('service_started')),
        'smoke_test_ok': _flag(dep.get('smoke_test_ok')),
        'smoke_test_status': _number(dep.get('smoke_test_status')),
        'smoke_test_path': dep.get('smoke_test_path'),
        'dry_run_wrote_nothing': _flag((dry or {}).get('wrote_nothing')),
        'coverage_fingerprint_sha256':
            (snap.get('coverage_fingerprint') or {}).get('sha256'),
        'probe_request_failures':
            _number(counters_of(collect).get('probe_request_failures')),
        'probe_pending_requests':
            _number(counters_of(collect).get('probe_pending_requests')),
        'target_day': common.TARGET_DAY,
        'staging_url': common.STAGING_URL,

        'evidence_present': sorted(name for name, value in (
            ('preflight', preflight), ('deploy', deploy),
            ('collect', collect), ('staging_snapshot', staging))
            if value),
        'evidence_missing': sorted(missing_evidence),

        'flights_received': _number(routes.get('flights_of_target_day')),
        'routes_received': _number(routes.get('routes_of_target_day')),
        'works_formed': _number(coverage.get('works')),

        'ready_estimate': _number(by_status.get('READY_ESTIMATE')),
        'partial_data': _number(by_status.get('PARTIAL_DATA')),
        'data_unavailable': _number(by_status.get('DATA_UNAVAILABLE')),
        'contour_ambiguous': _number(by_status.get('CONTOUR_AMBIGUOUS')),
        'contour_not_matched': _number(by_status.get('CONTOUR_NOT_MATCHED')),
        'route_invalid': _number(by_status.get('ROUTE_INVALID')),

        'dji_area_ha': dji,
        'ready_useful_area_ha': ready_area,
        'delta_ha': delta,
        'delta_percent': delta_percent,

        # [REASON]: три поля задания схема НЕ хранит. Ближайшая честная
        # величина стоит рядом под своим именем; выдуманного числа нет.
        'fully_idle_flights_excluded': None,
        'fully_idle_flights_excluded_note': NOT_RECORDED,
        'works_with_zero_work_segments':
            _number(coverage.get('works_with_zero_work_segments')),
        'mixed_flights': None,
        'mixed_flights_note': NOT_RECORDED,
        'works_with_mixed_mission_state':
            _number((coverage.get('by_mission_state') or {}).get('MIXED'), 0),
        'idle_segments': None,
        'idle_segments_note': NOT_RECORDED,

        'work_segments': _number(coverage.get('work_segments')),
        'works_without_confirmed_width':
            _number(coverage.get('works_without_confirmed_width')),
        'works_with_unresolved_contour':
            _number(coverage.get('works_with_unresolved_contour')),
        'works_without_number': _number(coverage.get('works_without_number')),
        'works_without_number_share':
            _number(coverage.get('works_without_number_share')),

        'recalculation_seconds': _number(recalculation_seconds),

        'owner_share_threshold': owner_threshold,
        'owner_dji_delta_percent': dji_delta_limit,

        'area_ha_fingerprints': fingerprints,
        'area_ha_unchanged': len(set(fingerprints.values())) <= 1
                             and bool(fingerprints),
        'integrity_ok': _flag((snap.get('integrity') or {}).get('integrity_ok')),
        'migration_on_copy_ok': _flag(pre.get('migration_on_copy_ok')),
        'migration_on_staging_ok': _flag(dep.get('migration_on_staging_ok')),
        'staging_backup_recorded': bool(dep.get('backup_path')),
        'staging_sha_before': dep.get('sha_before'),
        'staging_sha_after': dep.get('sha_after'),

        'recalc_runs': [{'label': payload_of(run).get('label'),
                         'summary': payload_of(run).get('summary', {}),
                         'outputs_agree': payload_of(run).get('outputs_agree'),
                         'period_is_the_target_day':
                             payload_of(run).get('period_is_the_target_day')}
                        for run in recalc_runs],
        'collect_counters': col.get('counters', {}),
        'conditions': conditions,
    }

    # Порядок важен и он такой:
    #   1. проверить СОБРАННЫЕ поля -- вердикта в них ещё нет;
    #   2. вынести вердикт, зная результат проверки;
    #   3. нарисовать Markdown уже с вердиктом;
    #   4. проверить ещё раз, теперь и текст. Приватное значение, приехавшее
    #      вместе с вердиктом, обязано поймать этот второй проход.
    violations = scan_for_private_values(report, '')
    report['privacy_scan_passed'] = not violations
    report['privacy_violations'] = violations
    report['verdict_notes'] = _notes(report, dji_delta_limit)

    verdict, reasons = decide(conditions, coverage, owner_threshold,
                              delta_percent, dji_delta_limit, not violations,
                              envelope_problems)
    report['verdict'] = verdict
    report['verdict_reasons'] = reasons
    report['production_rollout_authorised'] = (verdict == 'GO')
    report['business_decision_required'] = (verdict != 'GO')

    markdown = render_markdown(report)
    violations = scan_for_private_values(report, markdown)
    report['privacy_scan_passed'] = not violations
    report['privacy_violations'] = violations
    if violations and report['verdict'] != 'REJECT':
        report['verdict'] = 'REJECT'
        report['verdict_reasons'] = ['REPORT_CONTAINS_PRIVATE_VALUES']
        report['production_rollout_authorised'] = False
        report['business_decision_required'] = True
    markdown = render_markdown(report)
    return report, markdown


def _notes(report, dji_delta_limit):
    """Информационные коды. На вердикт не влияют НИКОГДА."""
    notes = ['FULLY_IDLE_FLIGHTS_NOT_RECORDED',
             'IDLE_SEGMENTS_NOT_RECORDED',
             'MIXED_FLIGHTS_PER_FLIGHT_NOT_RECORDED']
    if dji_delta_limit is None:
        notes.append('DJI_DELTA_NOT_AUTOJUDGED')
    if report.get('owner_share_threshold') is None:
        notes.append('OWNER_SHARE_THRESHOLD_NOT_SET')
    if report.get('evidence_missing'):
        notes.append('EVIDENCE_INCOMPLETE')
    return notes


# ─── Markdown ───────────────────────────────────────────────────────────────

def _cell(value):
    if value is None:
        return 'не записано'
    if isinstance(value, bool):
        return 'да' if value else 'НЕТ'
    return str(value)


def render_markdown(report):
    """Тот же отчёт для человека. Ни одного значения сверх JSON."""
    lines = [
        '# DRONE-USEFUL-AREA-PILOT-001 — итог контролируемого пилота',
        '',
        '| Поле | Значение |',
        '|---|---|',
        '| Идентификатор запуска | `%s` |' % _cell(report['run_id']),
        '| Ревизия комплекта (измерена) | `%s` |' % _cell(report['kit_sha']),
        '| Ревизия продукта | `%s` |' % _cell(report['product_sha']),
        '| Целевой день | %s |' % report['target_day'],
        '| Площадка | %s |' % report['staging_url'],
        '| Отчёт собран (UTC) | %s |' % report['generated_utc'],
        '',
        '## Что приехало и что посчитано',
        '',
        '| Показатель | Значение |',
        '|---|---|',
        '| Вылетов за день | %s |' % _cell(report['flights_received']),
        '| Маршрутов принято | %s |' % _cell(report['routes_received']),
        '| Работ сформировано | %s |' % _cell(report['works_formed']),
        '| READY_ESTIMATE | %s |' % _cell(report['ready_estimate']),
        '| PARTIAL_DATA | %s |' % _cell(report['partial_data']),
        '| DATA_UNAVAILABLE | %s |' % _cell(report['data_unavailable']),
        '| CONTOUR_AMBIGUOUS | %s |' % _cell(report['contour_ambiguous']),
        '| CONTOUR_NOT_MATCHED | %s |' % _cell(report['contour_not_matched']),
        '| ROUTE_INVALID | %s |' % _cell(report['route_invalid']),
        '',
        '## Площади',
        '',
        '| Показатель | Значение |',
        '|---|---|',
        '| Площадь DJI, га | %s |' % _cell(report['dji_area_ha']),
        '| Полезная площадь, только READY_ESTIMATE, га | %s |'
        % _cell(report['ready_useful_area_ha']),
        '| Разница, га | %s |' % _cell(report['delta_ha']),
        '| Разница, %% | %s |' % _cell(report['delta_percent']),
        '',
        '## Разбор работ',
        '',
        '| Показатель | Значение |',
        '|---|---|',
        '| Рабочих отрезков | %s |' % _cell(report['work_segments']),
        '| Отрезков холостого перелёта | %s (%s) |'
        % (_cell(report['idle_segments']), report['idle_segments_note']),
        '| Полностью холостых вылетов исключено | %s (%s) |'
        % (_cell(report['fully_idle_flights_excluded']),
           report['fully_idle_flights_excluded_note']),
        '| Работ без единого рабочего отрезка | %s |'
        % _cell(report['works_with_zero_work_segments']),
        '| Смешанных вылетов | %s (%s) |'
        % (_cell(report['mixed_flights']), report['mixed_flights_note']),
        '| Работ со смешанным mission_state | %s |'
        % _cell(report['works_with_mixed_mission_state']),
        '| Работ без подтверждённой ширины | %s |'
        % _cell(report['works_without_confirmed_width']),
        '| Работ с неоднозначным или ненайденным контуром | %s |'
        % _cell(report['works_with_unresolved_contour']),
        '| Работ без числа | %s (доля %s) |'
        % (_cell(report['works_without_number']),
           _cell(report['works_without_number_share'])),
        '| Длительность пересчёта, с | %s |'
        % _cell(report['recalculation_seconds']),
        '',
        '## Целостность и неизменность площади DJI',
        '',
        '| Проверка | Значение |',
        '|---|---|',
        '| PRAGMA integrity_check | %s |' % _cell(report['integrity_ok']),
        '| Фаза манифеста деплоя | %s |' % _cell(report['deploy_phase']),
        '| Резервная копия проверена | %s |' % _cell(report['backup_verified']),
        '| Служба площадки поднялась | %s |' % _cell(report['service_started']),
        '| Smoke-тест %s | %s (статус %s) |'
        % (_cell(report['smoke_test_path']), _cell(report['smoke_test_ok']),
           _cell(report['smoke_test_status'])),
        '| Сухой прогон ничего не записал | %s |'
        % _cell(report['dry_run_wrote_nothing']),
        '| Оборванных запросов маршрута | %s |'
        % _cell(report['probe_request_failures']),
        '| Незавершённых запросов маршрута | %s |'
        % _cell(report['probe_pending_requests']),
        '| Миграция на изолированной копии | %s |'
        % _cell(report['migration_on_copy_ok']),
        '| Миграция на площадке | %s |'
        % _cell(report['migration_on_staging_ok']),
        '| drone_flights.area_ha не менялась | %s |'
        % _cell(report['area_ha_unchanged']),
        '| Резервная копия площадки записана | %s |'
        % _cell(report['staging_backup_recorded']),
        '',
        '## Обязательные условия',
        '',
        '| Код | Пройдено |',
        '|---|---|',
    ]
    for item in report['conditions']:
        lines.append('| `%s` | %s |' % (item['code'],
                                        _cell(item['passed'])))
    lines += [
        '',
        '## Вердикт',
        '',
        '**%s**' % report['verdict'],
        '',
        'Причины (стабильные коды):',
        '',
    ]
    for code in report['verdict_reasons']:
        lines.append('- `%s`' % code)
    lines += ['', 'Примечания (на вердикт не влияют):', '']
    for code in report['verdict_notes']:
        lines.append('- `%s`' % code)
    lines += [
        '',
        '| Правило владельца | Значение |',
        '|---|---|',
        '| Порог доли работ без числа | %s |'
        % _cell(report['owner_share_threshold']),
        '| Допустимое отклонение от площади DJI, %% | %s |'
        % _cell(report['owner_dji_delta_percent']),
        '| Production rollout разрешён этим отчётом | %s |'
        % _cell(report['production_rollout_authorised']),
        '',
        ('**Оба правила -- бизнес-решения владельца, и комплект их не '
         'выдумывает.** Пока хотя бы одно не названо, верхний возможный '
         'вердикт -- `TECHNICAL_GO`: тракт исправен, решение не принято, '
         'production этим отчётом НЕ разрешён.'),
        '',
        '## Согласованность улик',
        '',
        '| Проверка | Значение |',
        '|---|---|',
        '| Все улики одного запуска | %s |'
        % _cell(not report['envelope_problems']),
        '| Нарушений конвертов | %d |' % len(report['envelope_problems']),
        '',
        '## Проверка отчёта на приватные значения',
        '',
        '| Проверка | Значение |',
        '|---|---|',
        '| Пройдена | %s |' % _cell(report['privacy_scan_passed']),
        '| Нарушений | %d |' % len(report['privacy_violations']),
        '',
        # [REASON]: эта строка НЕ перечисляет запретные слова по именам.
        # Первая редакция перечисляла -- и собственная проверка отчёта нашла
        # их в его же пояснении, объявив чистый отчёт грязным. Прибор был
        # прав: он ищет слово, а не намерение. Значит слов тут быть не должно.
        'Отчёт строится по белому списку полей: ни одно значение сверх '
        'объявленных счётчиков, площадей, статусов и отпечатков в него не '
        'попадает, а попытка добавить туда что-то ещё делает сборку отказом.',
        '',
    ]
    return '\n'.join(lines)


# ─── Командная строка ───────────────────────────────────────────────────────

def build_parser():
    parser = argparse.ArgumentParser(
        prog='pilot_report.py',
        description='DRONE-USEFUL-AREA-PILOT-001: one safe JSON and one safe '
                    'Markdown report, with a machine verdict.')
    parser.add_argument('--preflight', metavar='PATH')
    parser.add_argument('--deploy', metavar='PATH')
    parser.add_argument('--collect', metavar='PATH')
    parser.add_argument('--staging-snapshot', dest='staging', metavar='PATH')
    parser.add_argument('--recalc', action='append', default=[],
                        metavar='PATH',
                        help='recalculation evidence; give it three times: '
                             'dry-run, apply-1, apply-2')
    parser.add_argument('--recalc-seconds', type=float, default=None,
                        metavar='N',
                        help='how long the applying recalculation took')
    parser.add_argument('--run-id', required=True, metavar='ID',
                        help='the one run identifier every evidence file must '
                             'carry')
    parser.add_argument('--kit-sha', required=True, metavar='SHA',
                        help='the MEASURED revision of the kit checkout')
    # [REASON]: НЕТ значения по умолчанию, и это главное в этих двух флагах.
    # Порог, выбранный сессией и подставленный молча, превращается в решение
    # владельца, которого он не принимал. Без них вердикт останавливается на
    # TECHNICAL_GO и говорит, чего не хватает.
    parser.add_argument('--owner-share-threshold', type=float, default=None,
                        metavar='SHARE',
                        help='the share of works without a number the OWNER '
                             'accepts. No default: unset means the business '
                             'decision has not been made')
    parser.add_argument('--owner-dji-delta-percent', type=float, default=None,
                        metavar='PERCENT',
                        help='the deviation from the DJI area the OWNER '
                             'accepts. No default, for the same reason')
    parser.add_argument('--out-json', required=True, metavar='PATH')
    parser.add_argument('--out-md', required=True, metavar='PATH')
    return parser


def main(argv=None):
    args = build_parser().parse_args(argv)

    missing = []
    documents = {}
    for name, path in (('preflight', args.preflight), ('deploy', args.deploy),
                       ('collect', args.collect),
                       ('staging_snapshot', args.staging)):
        if not path:
            missing.append(name)
            documents[name] = None
            continue
        try:
            documents[name] = load_evidence(path)
        except ReportError as exc:
            sys.stderr.write('ERROR: %s\n' % exc)
            return common.EXIT_ERROR

    runs = []
    for path in args.recalc:
        try:
            runs.append(load_evidence(path))
        except ReportError as exc:
            sys.stderr.write('ERROR: %s\n' % exc)
            return common.EXIT_ERROR
    for label in ('dry-run', 'apply-1', 'apply-2'):
        if not _run_by_label(runs, label):
            missing.append('recalc:%s' % label)

    report, markdown = build_report(
        documents['preflight'], documents['deploy'], documents['collect'],
        runs, documents['staging_snapshot'], args.recalc_seconds,
        args.owner_share_threshold, args.owner_dji_delta_percent, missing,
        run_id=args.run_id, kit_sha=args.kit_sha)

    common.write_evidence(args.out_json, report)
    directory = os.path.dirname(os.path.abspath(args.out_md))
    if directory and not os.path.isdir(directory):
        os.makedirs(directory)
    with open(args.out_md, 'w', encoding='utf-8', newline='\n') as handle:
        handle.write(markdown)

    print('VERDICT=%s' % report['verdict'])
    for code in report['verdict_reasons']:
        print('REASON=%s' % code)
    print('PRODUCTION_ROLLOUT_AUTHORISED=%s'
          % ('yes' if report['production_rollout_authorised'] else 'no'))
    for problem in report['envelope_problems']:
        print('ENVELOPE=%s' % problem)
    print('PRIVACY_SCAN=%s' % ('PASS' if report['privacy_scan_passed']
                               else 'FAIL'))
    print('JSON=%s' % os.path.abspath(args.out_json))
    print('MARKDOWN=%s' % os.path.abspath(args.out_md))

    if not report['privacy_scan_passed']:
        for item in report['privacy_violations']:
            sys.stderr.write('PRIVACY VIOLATION: %s at %s\n'
                             % (item['code'], item['where']))
        return common.EXIT_CHECK_FAILED
    return common.EXIT_OK


if __name__ == '__main__':
    sys.exit(main())
