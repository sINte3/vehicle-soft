# -*- coding: utf-8 -*-
"""tools/dji_area_48h.py -- пересчёт разбора DJI-AREA-48H без браузера.

    python tools/dji_area_48h.py --replay drone_collector/out/area_48h/private/capture.json
    python tools/dji_area_48h.py --replay <capture.json> --cell 0.25
    python tools/dji_area_48h.py --verify drone_collector/out/area_48h/DJI_AREA_48H_SHAREABLE.json --against <capture.json>
    python tools/dji_area_48h.py --preflight

ЗАЧЕМ ОТДЕЛЬНАЯ ЗАПУСКАЛКА

Живой прогон делается ОДИН раз: он стоит владельцу похода в кабинет DJI и
ручной работы в интерфейсе. Всё, что можно пересчитать после него, должно
пересчитываться без него -- иначе каждая правка порога превращалась бы в
просьбу «зайдите в браузер ещё раз».

`--replay` берёт приватный снимок, уже лежащий на диске, и заново считает обе
части отчёта. Сеть не трогается, браузер не открывается, кабинет DJI не
опрашивается. `--verify` отвечает на единственный вопрос перед отправкой
отчёта: не течёт ли он.

ЧЕГО ЭТОТ ИНСТРУМЕНТ НЕ ДЕЛАЕТ

Не обращается к базе Vehicle Soft, не импортирует `app`, не создаёт таблиц,
не применяет миграций, не меняет начислений и подтверждённых гектаров. Пишет
ровно в два безопасных файла и в приватный каталог рядом с ними.

ВЫВОД В КОНСОЛЬ ТОЛЬКО ASCII: скрипт запускается из PowerShell, и кириллица в
консоли Windows зависит от кодовой страницы. Содержательный текст уходит в
файлы отчёта, которые пишутся в UTF-8.
"""

import argparse
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from drone_collector.area_study import (  # noqa: E402
    DEFAULT_PARAMS, SHAREABLE_JSON, SHAREABLE_MD, ShareableLeak, StudyParams,
    archive_existing, assert_shareable, private_strings, read_capture,
    run_study, study_dir, write_reports)

EXIT_OK = 0
EXIT_USAGE = 2
EXIT_LEAK = 3
EXIT_PREFLIGHT = 5
EXIT_EMPTY = 6

# Переменные, при которых прогон мог бы что-нибудь отправить в Vehicle Soft.
#
# [REASON]: `--area-48h` их не требует и без них работает, но «не требует» --
# это про конфигурацию, а не про гарантию. Preflight отвечает на другой
# вопрос: не осталось ли в ОКРУЖЕНИИ того, чем отправка вообще возможна.
# Проверяемая пустота лучше обещания.
INGEST_VARIABLES = ('VEHICLE_SOFT_BASE_URL', 'DRONE_API_TOKEN')


def build_parser():
    parser = argparse.ArgumentParser(
        description='Recompute the DJI-AREA-48H study from a private capture.')
    parser.add_argument('--replay', metavar='CAPTURE',
                        help='private capture.json written by the live run')
    parser.add_argument('--verify', metavar='SHAREABLE',
                        help='check a shareable report for private content')
    parser.add_argument('--against', metavar='CAPTURE',
                        help='the private capture --verify checks against')
    parser.add_argument('--out', metavar='DIR',
                        help='where the reports go; defaults to the '
                             'collector out/ directory')
    parser.add_argument('--cell', type=float, default=DEFAULT_PARAMS.cell_m,
                        metavar='METRES',
                        help='raster cell in metres (default %.2f)'
                             % DEFAULT_PARAMS.cell_m)
    parser.add_argument('--min-pass', type=float,
                        default=DEFAULT_PARAMS.min_pass_m, metavar='METRES',
                        help='shortest run counted as a work pass (default '
                             '%.1f)' % DEFAULT_PARAMS.min_pass_m)
    parser.add_argument('--gap', type=float, default=DEFAULT_PARAMS.gap_m,
                        metavar='METRES',
                        help='a step longer than this is a recording gap '
                             '(default %.1f)' % DEFAULT_PARAMS.gap_m)
    parser.add_argument('--turn', type=float, default=DEFAULT_PARAMS.turn_deg,
                        metavar='DEGREES',
                        help='bearing change that ends a run (default %.1f)'
                             % DEFAULT_PARAMS.turn_deg)
    parser.add_argument('--preflight', action='store_true',
                        help='check the saved DJI session BY CONTENT, refuse '
                             'if a .env file exists, and refuse if the ingest '
                             'variables are still set in this process')
    parser.add_argument('--keep-old', action='store_true',
                        help='do not archive an existing shareable report')
    return parser


def default_out():
    from drone_collector.config import PACKAGE_ROOT
    return PACKAGE_ROOT / 'out'


def _preflight():
    """Всё, что должно быть верно ДО живого прогона. Только ASCII наружу."""
    from pathlib import Path

    from drone_collector.config import PACKAGE_ROOT, load_config
    from drone_collector.session import inspect_session

    problems = []

    env_file = Path(PACKAGE_ROOT).parent / '.env'
    collector_env = Path(PACKAGE_ROOT) / '.env'
    for candidate in (env_file, collector_env):
        if candidate.exists():
            # [REASON]: отказ, а не предупреждение. `.env` на пилотной машине
            # почти наверняка несёт адрес и токен Vehicle Soft, и прогон,
            # который «всё равно ничего не отправляет», доказывает это только
            # своим кодом. Отсутствие файла доказывает это независимо.
            problems.append('a .env file exists at %s; the study run must not '
                            'be able to reach Vehicle Soft at all' % candidate)

    for name in INGEST_VARIABLES:
        if os.environ.get(name):
            problems.append('%s is still set in this process; remove it '
                            'before the run' % name)

    try:
        cfg = load_config(require_ingest=False)
    except Exception as exc:
        problems.append('the configuration did not load (%s)'
                        % type(exc).__name__)
        cfg = None

    if cfg is not None:
        state = inspect_session(cfg.storage_state)
        print('Session file: %s' % cfg.storage_state)
        print('Session content: %s' % state.describe())
        if not state.usable:
            problems.append('the saved DJI session is not usable: %s'
                            % state.reason)

    if problems:
        print('PREFLIGHT FAILED:')
        for problem in problems:
            print('  - %s' % problem)
        return EXIT_PREFLIGHT
    print('PREFLIGHT OK: session usable, no .env, no ingest variable set.')
    return EXIT_OK


def _verify(args):
    with open(args.verify, encoding='utf-8') as handle:
        document = json.load(handle)
    forbidden = ()
    if args.against:
        forbidden = private_strings(read_capture(args.against))
    else:
        print('WARNING: --against was not given, so only the shape checks '
              'run; the content check needs the private capture.')
    try:
        assert_shareable(document, forbidden)
    except ShareableLeak as exc:
        print('LEAK: %s' % exc)
        return EXIT_LEAK
    print('CLEAN: %s carries no private value (%d forbidden string(s) '
          'checked)' % (args.verify, len(forbidden)))
    return EXIT_OK


def _replay(args):
    capture = read_capture(args.replay)
    flights = capture.get('flights') or []
    if not flights:
        print('The capture carries no flight; nothing to compute.')
        return EXIT_EMPTY
    params = StudyParams(cell_m=args.cell, min_pass_m=args.min_pass,
                         gap_m=args.gap, turn_deg=args.turn)
    out_dir = args.out or default_out()
    notes = ['recomputed offline from the private capture; no live request '
             'was made']
    if not args.keep_old:
        for moved in archive_existing(out_dir):
            print('Archived: %s' % moved)
    private, shareable = run_study(capture, params, notes=notes)
    try:
        written = write_reports(out_dir, capture, private, shareable)
    except ShareableLeak as exc:
        # [REASON]: отказ ДО записи. Утёкший отчёт, уже лежащий на диске,
        # владелец может отправить прежде, чем увидит сообщение об ошибке.
        print('LEAK: %s -- nothing was written.' % exc)
        return EXIT_LEAK
    print('Flights: %d   Works: %d   Status: %s'
          % (shareable['flights_total'], shareable['works_total'],
             shareable['final_status']))
    print('Private (never share): %s' % written['private'])
    print('Shareable JSON: %s' % written['json'])
    print('Shareable MD:   %s' % written['md'])
    return EXIT_OK


def main(argv=None):
    args = build_parser().parse_args(argv)
    chosen = sum(1 for flag in (args.replay, args.verify, args.preflight)
                 if flag)
    if chosen != 1:
        print('Give exactly one of --replay, --verify or --preflight.')
        return EXIT_USAGE
    if args.preflight:
        return _preflight()
    if args.verify:
        return _verify(args)
    return _replay(args)


if __name__ == '__main__':
    sys.exit(main())
