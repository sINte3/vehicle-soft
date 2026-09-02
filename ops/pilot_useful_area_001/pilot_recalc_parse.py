# -*- coding: utf-8 -*-
"""ops/pilot_useful_area_001/pilot_recalc_parse.py -- сводка пересчёта в JSON.

DRONE-USEFUL-AREA-PILOT-001. `tools/recalculate_drone_useful_area.py` печатает
сводку в консоль; операторскому скрипту нужна та же сводка машиночитаемой,
чтобы сравнить сухой прогон с фактическим и второй `--apply` с первым.

Разбор СТРОГИЙ: не нашлось ожидаемого поля -- отказ с кодом 1. Разбор,
возвращающий нули на непонятом вводе, объявил бы идемпотентным прогон, у
которого просто не прочитали вывод, -- то есть дал бы одинаковый ответ при
верном и неверном коде.

Запуск:

  & "C:\\Program Files\\Python314\\python.exe" ops\\pilot_useful_area_001\\pilot_recalc_parse.py --input C:\\pilot\\evidence\\recalc_dry.txt --label dry-run --out C:\\pilot\\evidence\\recalc_dry.json

Коды возврата: 0 -- разобрано; 1 -- ввод не разобран.

Вывод -- ASCII JSON. В сводке пересчёта нет и не может быть координат или
идентификаторов: она состоит из счётчиков (`drone_coverage_recalc`).
"""

import argparse
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)

import pilot_common as common  # noqa: E402

# (ключ улики, метка в выводе инструмента, тип)
INTEGER_FIELDS = (
    ('days', 'local days examined'),
    ('works', 'works'),
    ('inserted', 'rows inserted'),
    ('updated', 'rows updated'),
    ('unchanged', 'rows unchanged'),
    ('deleted', 'rows deleted (stale)'),
    ('READY_ESTIMATE', 'READY'),
    ('PARTIAL_DATA', 'PARTIAL'),
    ('DATA_UNAVAILABLE', 'UNAVAILABLE'),
    ('CONTOUR_AMBIGUOUS', 'AMBIGUOUS'),
    ('CONTOUR_NOT_MATCHED', 'NOT_MATCHED'),
    ('ROUTE_INVALID', 'ROUTE_INVALID'),
)

FLIGHTS_ROUTES = re.compile(r'^flights / routes\s*:\s*(\d+)\s*/\s*(\d+)\s*$')
MODE = re.compile(r'^mode\s*:\s*(.+?)\s*$')
ALGORITHM = re.compile(r'^algorithm\s*:\s*(\S+)\s*$')
PERIOD = re.compile(r'^Period:\s*(\d{4}-\d{2}-\d{2})\s*\.\.\s*'
                    r'(\d{4}-\d{2}-\d{2})\s*\(local UTC\+5\)\s*$')
READY_AREA = re.compile(r'^useful area of READY works only:\s*'
                        r'([-+]?\d+(?:\.\d+)?)\s*ha\s*$')


class ParseError(Exception):
    """Вывод инструмента не разобран."""


def _integer_pattern(label):
    return re.compile(r'^%s\s*:\s*(-?\d+)\s*$' % re.escape(label))


def parse_summary(text):
    """Сводка пересчёта из вывода инструмента. Отказ, если поля не хватает."""
    lines = [line.rstrip('\r\n') for line in str(text).splitlines()]
    result = {}
    missing = []

    for key, label in INTEGER_FIELDS:
        pattern = _integer_pattern(label)
        for line in lines:
            found = pattern.match(line.strip())
            if found:
                result[key] = int(found.group(1))
                break
        else:
            missing.append(label)

    for name, pattern, keys in (
            ('mode', MODE, ('mode',)),
            ('algorithm', ALGORITHM, ('algorithm_version',)),
            ('flights / routes', FLIGHTS_ROUTES, ('flights', 'routes')),
            ('useful area of READY works only', READY_AREA,
             ('ready_area_ha',))):
        for line in lines:
            found = pattern.match(line.strip())
            if found:
                if name == 'flights / routes':
                    result['flights'] = int(found.group(1))
                    result['routes'] = int(found.group(2))
                elif name == 'useful area of READY works only':
                    result['ready_area_ha'] = float(found.group(1))
                else:
                    result[keys[0]] = found.group(1)
                break
        else:
            missing.append(name)

    for line in lines:
        found = PERIOD.match(line.strip())
        if found:
            result['period_from'] = found.group(1)
            result['period_to'] = found.group(2)
            break

    if missing:
        raise ParseError('the recalculation output is missing: %s'
                         % ', '.join(missing))

    mode = result.get('mode', '')
    result['applied'] = mode.strip().upper().startswith('APPLY')
    result['dry_run'] = mode.strip().upper().startswith('DRY RUN')
    if not (result['applied'] or result['dry_run']):
        raise ParseError('the recalculation mode was neither APPLY nor DRY '
                         'RUN: %r' % mode)

    counted = sum(result[key] for key, _label in INTEGER_FIELDS
                  if key in common.QUALITY_STATUSES)
    result['status_total'] = counted
    # [REASON]: работа обязана попасть ровно в одну корзину статуса. Если
    # сумма статусов разошлась с числом работ, сводка внутренне противоречива,
    # и сравнивать сухой прогон с фактическим по ней уже нельзя.
    result['status_total_matches_works'] = counted == result['works']
    result['nothing_written'] = (result['inserted'] == 0
                                 and result['updated'] == 0
                                 and result['deleted'] == 0)
    return result


COMPARED_KEYS = ('works', 'flights', 'routes', 'READY_ESTIMATE',
                 'PARTIAL_DATA', 'DATA_UNAVAILABLE', 'CONTOUR_AMBIGUOUS',
                 'CONTOUR_NOT_MATCHED', 'ROUTE_INVALID', 'ready_area_ha',
                 'algorithm_version')


def compare(first, second):
    """Что различается между двумя сводками. Пусто -- значит совпали.

    Сравниваются ВЫВОДЫ расчёта, а не строки записи: `inserted`/`updated`
    у сухого прогона и у первого `--apply` совпадают по построению, а вот у
    второго `--apply` обязаны стать нулями -- это отдельная проверка, не эта.
    """
    differences = {}
    for key in COMPARED_KEYS:
        left = first.get(key)
        right = second.get(key)
        if isinstance(left, float) or isinstance(right, float):
            same = (left is not None and right is not None
                    and abs(float(left) - float(right)) <= 1e-6)
        else:
            same = left == right
        if not same:
            differences[key] = {'first': left, 'second': right}
    return differences


def build_parser():
    parser = argparse.ArgumentParser(
        prog='pilot_recalc_parse.py',
        description='Turn the console summary of '
                    'tools/recalculate_drone_useful_area.py into evidence '
                    'JSON, strictly.')
    parser.add_argument('--input', required=True, metavar='PATH',
                        help='file holding the captured tool output')
    parser.add_argument('--label', required=True,
                        metavar='NAME',
                        help='which run this is: dry-run, apply-1, apply-2')
    parser.add_argument('--expect-day', default=common.TARGET_DAY,
                        metavar='YYYY-MM-DD',
                        help='the period both bounds must equal')
    parser.add_argument('--compare-with', metavar='PATH',
                        help='an earlier evidence JSON to compare against')
    parser.add_argument('--out', metavar='PATH',
                        help='write the evidence JSON here')
    return parser


def main(argv=None):
    import json
    args = build_parser().parse_args(argv)

    if not os.path.exists(args.input):
        sys.stderr.write('ERROR: captured output not found at %s\n'
                         % args.input)
        return common.EXIT_ERROR

    with open(args.input, encoding='utf-8', errors='replace') as handle:
        text = handle.read()

    try:
        summary = parse_summary(text)
    except ParseError as exc:
        sys.stderr.write('ERROR: %s\n' % exc)
        return common.EXIT_ERROR

    payload = {
        'label': args.label,
        'summary': summary,
        'period_is_the_target_day': (
            summary.get('period_from') == args.expect_day
            and summary.get('period_to') == args.expect_day),
    }

    if args.compare_with:
        if not os.path.exists(args.compare_with):
            sys.stderr.write('ERROR: comparison evidence not found at %s\n'
                             % args.compare_with)
            return common.EXIT_ERROR
        with open(args.compare_with, encoding='ascii') as handle:
            other = json.load(handle)
        other_summary = other.get('payload', {}).get('summary', {})
        differences = compare(other_summary, summary)
        payload['compared_with'] = other.get('payload', {}).get('label')
        payload['differences'] = differences
        payload['outputs_agree'] = not differences

    common.emit(common.evidence_envelope('recalc:%s' % args.label, payload),
                args.out)
    return common.EXIT_OK


if __name__ == '__main__':
    sys.exit(main())
