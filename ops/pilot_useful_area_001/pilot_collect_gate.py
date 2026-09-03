# -*- coding: utf-8 -*-
"""ops/pilot_useful_area_001/pilot_collect_gate.py -- ворота перед пересчётом.

DRONE-USEFUL-AREA-PILOT-001. Единственный вопрос: можно ли вообще считать по
тому, что привёз живой сбор.

Почему это отдельные ворота, а не строчка в скрипте пересчёта. Пересчёт по
неполному захвату даёт ЧИСЛО. Число ложится в `drone_coverage_works`, попадает
на `/drones/coverage` и с этого момента ничем не отличается от числа, которому
можно верить: строка выглядит одинаково в обоих случаях. Отказ до первого
вызова пересчёта дешевле любого разбора потом.

Проверяется ВСЁ, что делает захват полным, и отдельно -- что улика вообще про
этот запуск:

  * код возврата сборщика 0;
  * `collect_live_confirmed` истинно;
  * оператор ответил, drain завершился;
  * ноль ошибок наблюдения, захвата и декодирования;
  * ноль ответов, выпавших по лимиту размера;
  * ноль ОБОРВАННЫХ и ноль НЕЗАВЕРШЁННЫХ маршрутных запросов -- своими
    числами из сводки, а не выводом из равенства observations и confirmed:
    запрос, умерший до тела, в observations не попадает вовсе;
  * множества запрошенных и возвращённых ID совпали;
  * пакет принят площадкой ЦЕЛИКОМ, ноль errors, ноль unlinked, ноль
    оставшихся в очереди конвертов, счётчики приёма сходятся;
  * день, `run_id`, `kit_sha` и `product_sha` -- те же, что у запуска.

Запуск:

  python pilot_collect_gate.py --collect <collect.json> --deploy <deploy.json> --run-id <id> --kit-sha <sha> --day 2026-06-05 --out <file>

Коды возврата: 0 -- считать можно; 1 -- ошибка ввода; 3 -- считать нельзя.
"""

import argparse
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)

import pilot_common as common  # noqa: E402

# Каждое условие -- (код, как его прочитать). Коды стабильны: по ним
# разговаривают, и менять их нельзя, даже если формулировка изменится.
REQUIRED_ZERO = (
    ('probe_errors', 'OBSERVATION_ERRORS'),
    ('probe_skipped_over_cap', 'RESPONSE_DROPPED_BY_THE_SIZE_CAP'),
    ('probe_request_failures', 'ROUTE_REQUESTS_FAILED'),
    ('probe_pending_requests', 'ROUTE_REQUESTS_STILL_PENDING'),
    ('collect_capture_errors', 'CAPTURE_ERRORS'),
    ('collect_decode_failures', 'DECODE_FAILURES'),
    ('collect_errors', 'INGEST_REPORTED_ERRORS'),
    ('collect_unlinked', 'INGEST_REPORTED_UNLINKED_ROUTES'),
    ('collect_left_pending', 'ENVELOPES_LEFT_PENDING'),
)

REQUIRED_TRUE = (
    ('collect_live_confirmed', 'COLLECTION_NOT_CONFIRMED'),
    ('probe_operator_answered', 'OPERATOR_DID_NOT_ANSWER'),
    ('probe_drained', 'DRAIN_DID_NOT_COMPLETE'),
    ('collect_batch_accepted', 'BATCH_NOT_FULLY_ACCEPTED'),
    ('collect_send_enabled', 'ROUTES_WERE_NOT_SENT'),
)

REQUIRED_POSITIVE = (
    ('probe_observations', 'NOTHING_OBSERVED'),
    ('collect_bodies_captured', 'NO_BODY_CAPTURED'),
    ('collect_routes_captured', 'NO_ROUTE_CAPTURED'),
    ('collect_envelopes_sent', 'NO_ENVELOPE_WAS_SENT'),
)


def _number(counters, key, default=None):
    value = counters.get(key)
    if isinstance(value, bool) or not isinstance(value, int):
        return default
    return value


def evaluate(collect, deploy, run_id, kit_sha, day):
    """Список причин отказа. Пустой -- считать можно."""
    reasons = []

    # 1. Улика вообще про этот запуск?
    for issue in common.validate_envelope(collect, 'collect:summary',
                                          run_id=run_id, kit_sha=kit_sha,
                                          target_day=day):
        reasons.append('COLLECT_ENVELOPE_%s' % issue)
    if deploy is not None:
        for issue in common.validate_envelope(deploy, 'deploy', run_id=run_id,
                                              kit_sha=kit_sha, target_day=day):
            reasons.append('DEPLOY_ENVELOPE_%s' % issue)
        # [REASON]: сбор не мог состояться раньше деплоя -- маршруты принимает
        # площадка, которую деплой поднимает. Обратный порядок меток означает,
        # что улики не про один прогон, как бы они ни выглядели по отдельности.
        for issue in common.check_time_order([('deploy', deploy),
                                              ('collect', collect)]):
            reasons.append('ORDER_%s' % issue)

    payload = collect.get('payload') if isinstance(collect, dict) else None
    if not isinstance(payload, dict):
        reasons.append('COLLECT_EVIDENCE_HAS_NO_PAYLOAD')
        return reasons
    counters = payload.get('counters')
    if not isinstance(counters, dict):
        reasons.append('COLLECT_EVIDENCE_HAS_NO_COUNTERS')
        return reasons

    # 2. Код возврата сборщика.
    if _number(counters, 'exit', 1) != 0:
        reasons.append('COLLECTOR_EXIT_NOT_ZERO')

    # 3. Всё, что обязано быть нулём, истиной и положительным.
    for key, code in REQUIRED_ZERO:
        if _number(counters, key, 1) != 0:
            reasons.append(code)
    for key, code in REQUIRED_TRUE:
        if counters.get(key) is not True:
            reasons.append(code)
    for key, code in REQUIRED_POSITIVE:
        if _number(counters, key, 0) <= 0:
            reasons.append(code)
    if counters.get('dry_run') is not False:
        reasons.append('RUN_WAS_A_DRY_RUN')

    # 4. Каждое наблюдение подтверждено.
    observations = _number(counters, 'probe_observations', 0)
    confirmed = _number(counters, 'probe_confirmed', -1)
    if observations > 0 and confirmed != observations:
        reasons.append('NOT_EVERY_OBSERVATION_CONFIRMED')

    # 5. Множества ID совпали. Сборщик выходит с кодом 16, когда они
    #    разошлись, поэтому ноль вместе с подтверждённым вердиктом -- это и
    #    есть доказательство совпадения (drone_collector/main.py,
    #    collect_exit_code).
    if not (_number(counters, 'exit', 1) == 0
            and counters.get('collect_live_confirmed') is True):
        reasons.append('ID_SETS_NOT_PROVEN_TO_MATCH')

    # 6. Счётчики приёма сходятся: seen = new + updated + unchanged + errors
    #    + unlinked (drones.py, api_route_sync).
    seen = _number(counters, 'collect_seen')
    parts = [_number(counters, name) for name in
             ('collect_new', 'collect_updated', 'collect_unchanged',
              'collect_errors', 'collect_unlinked')]
    if seen is None or any(part is None for part in parts):
        reasons.append('INGEST_COUNTERS_INCOMPLETE')
    elif seen != sum(parts):
        reasons.append('INGEST_COUNTERS_DO_NOT_BALANCE')

    # 7. Собственный вердикт сборщика, если он его записал.
    if payload.get('passed') is not True:
        reasons.append('COLLECT_EVIDENCE_SAYS_NOT_PASSED')

    ordered = []
    for reason in reasons:
        if reason not in ordered:
            ordered.append(reason)
    return ordered


def build_parser():
    parser = argparse.ArgumentParser(
        prog='pilot_collect_gate.py',
        description='DRONE-USEFUL-AREA-PILOT-001: refuse to recalculate over '
                    'a capture that was not complete, confirmed and fully '
                    'accepted.')
    parser.add_argument('--collect', required=True, metavar='PATH')
    parser.add_argument('--deploy', metavar='PATH')
    parser.add_argument('--run-id', required=True, metavar='ID')
    parser.add_argument('--kit-sha', required=True, metavar='SHA')
    parser.add_argument('--day', default=common.TARGET_DAY,
                        metavar='YYYY-MM-DD')
    parser.add_argument('--out', metavar='PATH')
    return parser


def main(argv=None):
    args = build_parser().parse_args(argv)
    try:
        collect = common.read_evidence(args.collect)
        deploy = (common.read_evidence(args.deploy) if args.deploy
                  and os.path.exists(args.deploy) else None)
    except common.ProbeError as exc:
        sys.stderr.write('ERROR: %s\n' % exc)
        return common.EXIT_ERROR

    reasons = evaluate(collect, deploy, args.run_id, args.kit_sha, args.day)
    payload = {
        'passed': not reasons,
        'reasons': reasons,
        'collect_evidence': os.path.abspath(args.collect),
        'checked_day': args.day,
    }
    common.emit(common.evidence_envelope('collect:gate', payload, args.run_id,
                                         args.kit_sha), args.out)
    if reasons:
        for reason in reasons:
            sys.stderr.write('COLLECT GATE: %s\n' % reason)
        return common.EXIT_CHECK_FAILED
    return common.EXIT_OK


if __name__ == '__main__':
    sys.exit(main())
