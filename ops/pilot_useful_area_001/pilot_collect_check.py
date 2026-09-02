# -*- coding: utf-8 -*-
"""ops/pilot_useful_area_001/pilot_collect_check.py -- сторона BAK-TEX11.

DRONE-USEFUL-AREA-PILOT-001. Две подкоманды, и обе ничего не открывают:

  preflight -- ДО браузера: куда настроен сборщик, есть ли структурно годная
               сессия, не противоречивы ли тайминги наблюдателя;
  summary   -- ПОСЛЕ прогона: строка `RUN SUMMARY` из вывода сборщика,
               разобранная в улику по БЕЛОМУ СПИСКУ ключей.

НИ ОДНО ЗНАЧЕНИЕ СЕКРЕТА ОТСЮДА НЕ ВЫХОДИТ. Токен показывается как
`set`/`missing` (это `CollectorConfig.describe()`, единственный
санкционированный способ), сессия -- счётчиками (`inspect_session`), и ни
cookie, ни подпись, ни `request_id`, ни идентификатор вылета в улику не
попадают: белый список ключей не содержит для них места.

Адрес приёмника проверяется по ФАКТИЧЕСКОЙ конфигурации, а не по переменной
окружения: `drone_collector/config.py` читает ещё и `drone_collector/.env`,
и проверка одного лишь `$env:VEHICLE_SOFT_BASE_URL` объявила бы безопасным
прогон, который на самом деле настроен на боевой адрес.

Запуск (venv сборщика, репозиторий пилота):

  & "C:\\VehicleSoft_DJI_StageB_Pilot\\drone_collector\\.venv\\Scripts\\python.exe" ops\\pilot_useful_area_001\\pilot_collect_check.py preflight --out C:\\pilot\\evidence\\collect_preflight.json

Коды возврата: 0 -- проверка пройдена; 1 -- ошибка; 3 -- проверка провалена.
"""

import argparse
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
REPO_ROOT = os.path.dirname(os.path.dirname(HERE))
for path in (HERE, REPO_ROOT):
    if path not in sys.path:
        sys.path.insert(0, path)

import pilot_common as common  # noqa: E402

# Ключи строки RUN SUMMARY режима сбора. Белый список: ключ, которого здесь
# нет, в улику не попадает. Список повторяет COLLECT_SUMMARY_KEYS сборщика
# намеренно -- прибор проверки не спрашивает у проверяемого, что тому можно
# показывать.
COLLECT_KEYS = (
    'mode', 'dry_run', 'region', 'probe_route_responses',
    'probe_observations', 'probe_confirmed', 'probe_errors',
    'probe_skipped_over_cap', 'probe_operator_answered', 'probe_drained',
    'collect_live_confirmed', 'collect_bodies_captured',
    'collect_decode_failures', 'collect_capture_errors',
    'collect_routes_captured', 'collect_routes_queued',
    'collect_routes_duplicate', 'collect_send_enabled',
    'collect_envelopes_sent', 'collect_batch_accepted', 'collect_left_pending',
    'collect_seen', 'collect_new', 'collect_updated', 'collect_unchanged',
    'collect_errors', 'collect_unlinked', 'exit',
)

# [REASON]: `region` -- единственное текстовое поле сводки, и оно приезжает
# от кабинета. Названием поля заказчика оно не бывает, но проверка стоит
# дёшево, а «текст из внешнего источника попал в отчёт без разбора» -- ровно
# тот способ, которым приватное значение однажды и уедет.
#
# Имя региона НАЧИНАЕТСЯ С БУКВЫ. Первая редакция допускала цифры и точку с
# первого символа -- и десятичный градус с семью знаками проходил её
# насквозь: проверка, написанная против координаты, пропускала ровно
# координату. Поймано отрицательным контролем в
# tools/test_pilot_useful_area_001.py.
REGION_RE = re.compile(r'^[A-Za-z][A-Za-z0-9 _.\-]{0,39}$')

SUMMARY_MARK = 'RUN SUMMARY '
PAIR_RE = re.compile(r'([a-z_]+)=("[^"]*"|\S+)')


def parse_run_summary(text):
    """Последняя строка RUN SUMMARY, разобранная по белому списку.

    Берётся ПОСЛЕДНЯЯ: в одном журнале могут лежать прогоны подряд, и
    отчитываться надо о том, который только что закончился.
    """
    line = None
    for candidate in str(text).splitlines():
        if SUMMARY_MARK in candidate:
            line = candidate[candidate.index(SUMMARY_MARK)
                             + len(SUMMARY_MARK):]
    if line is None:
        raise common.ProbeError('no "RUN SUMMARY" line in the captured output '
                                '- the collector did not finish a run')

    raw = {}
    for key, value in PAIR_RE.findall(line):
        raw[key] = value.strip('"')

    counters = {}
    unknown = []
    for key, value in sorted(raw.items()):
        if key not in COLLECT_KEYS:
            unknown.append(key)
            continue
        counters[key] = _coerce(value)

    missing = [key for key in COLLECT_KEYS if key not in counters]
    region = counters.get('region')
    if isinstance(region, str) and not REGION_RE.match(region):
        counters['region'] = 'REDACTED_UNEXPECTED_SHAPE'
    return counters, unknown, missing


def _coerce(value):
    if value == '-':
        return None
    if value == 'true':
        return True
    if value == 'false':
        return False
    try:
        return int(value)
    except ValueError:
        return value


def collect_verdict(counters):
    """PASS только при ПОЛНОМ захвате и ПОЛНОМ принятии площадкой.

    Список причин, а не одно «нет»: оператору чинить разное.
    """
    reasons = []

    def number(key, default=None):
        value = counters.get(key)
        if isinstance(value, bool) or not isinstance(value, int):
            return default
        return value

    if number('exit', 1) != 0:
        reasons.append('COLLECTOR_EXIT_NOT_ZERO')
    if counters.get('collect_live_confirmed') is not True:
        reasons.append('COLLECTION_NOT_CONFIRMED')
    if counters.get('probe_operator_answered') is not True:
        reasons.append('OPERATOR_DID_NOT_ANSWER')
    if counters.get('probe_drained') is not True:
        reasons.append('DRAIN_DID_NOT_COMPLETE')
    if number('probe_observations', 0) <= 0:
        reasons.append('NOTHING_OBSERVED')
    elif number('probe_confirmed', -1) != number('probe_observations', 0):
        reasons.append('NOT_EVERY_OBSERVATION_CONFIRMED')
    if number('probe_skipped_over_cap', 1) != 0:
        reasons.append('RESPONSE_DROPPED_BY_THE_SIZE_CAP')
    if number('probe_errors', 1) != 0:
        reasons.append('OBSERVATION_ERRORS')
    if number('collect_capture_errors', 1) != 0:
        reasons.append('CAPTURE_ERRORS')
    if number('collect_decode_failures', 1) != 0:
        reasons.append('DECODE_FAILURES')
    if number('collect_routes_captured', 0) <= 0:
        reasons.append('NO_ROUTE_CAPTURED')
    if counters.get('collect_send_enabled') is not True:
        reasons.append('ROUTES_WERE_NOT_SENT')
    if counters.get('collect_batch_accepted') is not True:
        reasons.append('BATCH_NOT_FULLY_ACCEPTED')
    if number('collect_left_pending', 1) != 0:
        reasons.append('ENVELOPES_LEFT_PENDING')
    if number('collect_envelopes_sent', 0) <= 0:
        reasons.append('NO_ENVELOPE_WAS_SENT')
    if number('collect_errors', 1) != 0:
        reasons.append('INGEST_REPORTED_ERRORS')
    if number('collect_unlinked', 1) != 0:
        reasons.append('INGEST_REPORTED_UNLINKED_ROUTES')
    if counters.get('dry_run') is not False:
        reasons.append('RUN_WAS_A_DRY_RUN')

    seen = number('collect_seen')
    parts = [number('collect_new'), number('collect_updated'),
             number('collect_unchanged')]
    balanced = (seen is not None and all(part is not None for part in parts)
                and seen == sum(parts) + (number('collect_errors', 0) or 0)
                + (number('collect_unlinked', 0) or 0))
    if not balanced:
        reasons.append('INGEST_COUNTERS_DO_NOT_BALANCE')

    return {
        'passed': not reasons,
        'reasons': reasons,
        'ingest_counters_balance': balanced,
        'no_unfinished_route_requests':
            number('probe_observations', 0) > 0
            and number('probe_confirmed', -1) == number('probe_observations', 0)
            and number('probe_skipped_over_cap', 1) == 0,
    }


# ─── preflight ──────────────────────────────────────────────────────────────

def run_preflight(expect_url):
    """Куда настроен сборщик, годна ли сессия, не противоречивы ли тайминги."""
    from drone_collector import config as collector_config
    from drone_collector.session import inspect_session
    from drone_collector.route_ui_probe import (ProbeTimingError,
                                                validate_probe_timings)

    cfg = collector_config.load_config(require_ingest=True)
    described = cfg.describe()

    session = inspect_session(cfg.storage_state)

    timings_ok = True
    timings_reason = ''
    try:
        validate_probe_timings(poll_ms=cfg.route_probe_poll_ms,
                               wait_ms=cfg.route_probe_wait_ms,
                               drain_ms=cfg.route_probe_drain_ms,
                               quiet_ms=cfg.route_probe_quiet_ms)
    except ProbeTimingError as exc:
        timings_ok = False
        timings_reason = str(exc)

    target_is_staging = common.url_is_staging(cfg.base_url)
    target_is_production = common.url_is_production(cfg.base_url)

    payload = {
        # Адрес -- не секрет и обязан быть виден: без него нельзя доказать,
        # что отправка шла на площадку.
        'base_url': cfg.base_url,
        'route_sync_url': cfg.route_sync_url,
        'expected_url': expect_url,
        'target_is_staging': target_is_staging,
        'target_is_production': target_is_production,
        'target_matches_expected': common.url_is_staging(expect_url)
                                   and target_is_staging,
        # Токен -- ТОЛЬКО set/missing. Значение не покидает процесс.
        'api_token': described['api_token'],
        'session': {
            'usable': bool(session.usable),
            'reason': session.reason,
            'bytes': session.bytes,
            'cookies': session.cookies,
            'origins': session.origins,
            'local_storage_items': session.local_storage_items,
        },
        'probe_timings_valid': timings_ok,
        'probe_timings_reason': timings_reason,
        'route_probe_poll_ms': cfg.route_probe_poll_ms,
        'route_probe_wait_ms': cfg.route_probe_wait_ms,
        'route_probe_drain_ms': cfg.route_probe_drain_ms,
        'route_probe_quiet_ms': cfg.route_probe_quiet_ms,
        'headless': cfg.headless,
    }

    reasons = []
    if not target_is_staging:
        reasons.append('TARGET_IS_NOT_STAGING')
    if target_is_production:
        reasons.append('TARGET_IS_PRODUCTION')
    if described['api_token'] != 'set':
        reasons.append('DRONE_API_TOKEN_IS_NOT_SET')
    if not session.usable:
        reasons.append('SESSION_IS_NOT_USABLE')
    if not timings_ok:
        reasons.append('PROBE_TIMINGS_ARE_CONTRADICTORY')
    if cfg.headless:
        # [REASON]: режим сбора требует человека у браузера -- он ведёт
        # кабинет до карты руками. Безголовый прогон дождётся оператора,
        # которого нет, и закончится неподтверждённым сбором.
        reasons.append('HEADLESS_IS_ON_BUT_THE_RUN_NEEDS_AN_OPERATOR')

    payload['passed'] = not reasons
    payload['reasons'] = reasons
    return payload


# ─── Командная строка ───────────────────────────────────────────────────────

def build_parser():
    parser = argparse.ArgumentParser(
        prog='pilot_collect_check.py',
        description='DRONE-USEFUL-AREA-PILOT-001: collector-side checks that '
                    'open no browser and print no secret value.')
    parser.add_argument('command', choices=('preflight', 'summary'))
    parser.add_argument('--input', metavar='PATH',
                        help='with summary: the captured collector output')
    parser.add_argument('--expect-url', default=common.STAGING_URL,
                        metavar='URL',
                        help='the only ingest base URL this pilot allows')
    parser.add_argument('--out', metavar='PATH')
    return parser


def main(argv=None):
    args = build_parser().parse_args(argv)

    if args.command == 'preflight':
        if common.url_is_production(args.expect_url):
            sys.stderr.write('ERROR: --expect-url points at production. This '
                             'kit never sends to production.\n')
            return common.EXIT_ERROR
        try:
            payload = run_preflight(args.expect_url)
        except Exception as exc:  # noqa: BLE001
            sys.stderr.write('ERROR: %s: %s\n' % (type(exc).__name__, exc))
            return common.EXIT_ERROR
        kind = 'collect:preflight'
    else:
        if not args.input:
            sys.stderr.write('ERROR: summary needs --input\n')
            return common.EXIT_ERROR
        if not os.path.exists(args.input):
            sys.stderr.write('ERROR: captured output not found at %s\n'
                             % args.input)
            return common.EXIT_ERROR
        with open(args.input, encoding='utf-8', errors='replace') as handle:
            text = handle.read()
        try:
            counters, unknown, missing = parse_run_summary(text)
        except common.ProbeError as exc:
            sys.stderr.write('ERROR: %s\n' % exc)
            return common.EXIT_ERROR
        verdict = collect_verdict(counters)
        payload = {
            'counters': counters,
            'keys_not_in_the_allowlist': unknown,
            'keys_missing_from_the_summary': missing,
            'passed': verdict['passed'],
            'reasons': verdict['reasons'],
            'ingest_counters_balance': verdict['ingest_counters_balance'],
            'no_unfinished_route_requests':
                verdict['no_unfinished_route_requests'],
        }
        kind = 'collect:summary'

    common.emit(common.evidence_envelope(kind, payload), args.out)
    if not payload.get('passed'):
        for reason in payload.get('reasons', ()):
            sys.stderr.write('CHECK FAILED: %s\n' % reason)
        return common.EXIT_CHECK_FAILED
    return common.EXIT_OK


if __name__ == '__main__':
    sys.exit(main())
