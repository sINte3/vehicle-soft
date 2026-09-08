# -*- coding: utf-8 -*-
"""tools/dji_area_recalc.py -- пересчёт учёта площади DJI по модели доказательств.

Тонкая обёртка над `dji_area.pipeline`: разбор командной строки, коды
возврата и печать сводки. Ни расчёта, ни SQL здесь нет.

Режим выбирается ЯВНО и ровно один: `--dry-run` считает всё и ничего не
пишет; `--apply` пишет append-only строки `dji_area_calculations` и
`dji_field_attributions` (прежняя текущая строка закрывается
`superseded_at`, не переписывается) и кэширует сводки V4. Повторный
`--apply` того же входа даёт `unchanged` по всем строкам.

Запуск (для --apply служба площадки остановлена либо база -- копия):

  cd C:\\transport-report-staging
  & "C:\\Program Files\\Python314\\python.exe" tools\\dji_area_recalc.py --from 2026-08-01 --to 2026-08-31 --dry-run
  & "C:\\Program Files\\Python314\\python.exe" tools\\dji_area_recalc.py --from 2026-08-01 --to 2026-08-31 --apply

  --flight-id 673501214 --flight-id 685264927   -- только названные записи
  --with-geometric                              -- пробовать TIER4 (маршрут в полигоне)
  --json C:\\path\\summary.json                  -- сводка в файл (UTF-8)
  --rows C:\\path\\rows.json                     -- построчный результат (диагностика)

Коды возврата: 0 выполнено; 1 ошибка командной строки/пересчёта; 2 база не
найдена (файл НЕ создаётся). Вывод в консоль только ASCII.
"""

import argparse
import json
import os
import sys
from datetime import date

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from dji_area import AREA_ALGORITHM_VERSION, FIELD_RESOLVER_VERSION  # noqa: E402
from dji_area import pipeline  # noqa: E402
from dji_area import store  # noqa: E402

DEFAULT_DB = os.path.join(ROOT, 'instance', 'transport.db')

EXIT_OK = 0
EXIT_USAGE = 1
EXIT_NO_DATABASE = 2


def build_parser():
    parser = argparse.ArgumentParser(
        prog='dji_area_recalc.py',
        description='Recalculate the DJI area evidence model (%s, field %s) '
                    'over an explicit period of report days (UTC+5).'
                    % (AREA_ALGORITHM_VERSION, FIELD_RESOLVER_VERSION))
    parser.add_argument('--from', dest='date_from', required=True,
                        metavar='YYYY-MM-DD')
    parser.add_argument('--to', dest='date_to', required=True,
                        metavar='YYYY-MM-DD')
    parser.add_argument('--dry-run', action='store_true',
                        help='compute and report, write nothing')
    parser.add_argument('--apply', action='store_true',
                        help='write append-only calculation rows')
    parser.add_argument('--db', dest='db_path', default=DEFAULT_DB,
                        metavar='PATH')
    parser.add_argument('--flight-id', dest='flight_ids', action='append',
                        type=int, metavar='ID')
    parser.add_argument('--with-geometric', action='store_true',
                        help='try TIER4 route-in-current-polygon candidates')
    parser.add_argument('--batch-size', type=int, default=500)
    parser.add_argument('--json', dest='json_path', metavar='PATH',
                        help='write the summary as JSON (UTF-8)')
    parser.add_argument('--rows', dest='rows_path', metavar='PATH',
                        help='write per-flight rows as JSON (UTF-8)')
    parser.add_argument('--quiet', action='store_true')
    return parser


def parse_day(text):
    return date.fromisoformat(text)


def check_usage(args):
    if args.dry_run and args.apply:
        raise ValueError('--dry-run and --apply are mutually exclusive')
    if not args.dry_run and not args.apply:
        raise ValueError('choose a mode explicitly: --dry-run writes nothing, '
                         '--apply writes calculation rows')
    if args.batch_size <= 0:
        raise ValueError('--batch-size must be positive')


def format_summary(summary, apply):
    lines = []
    lines.append('DJI AREA RECALC %s' % ('APPLY' if apply else 'DRY-RUN'))
    lines.append('  algorithm         : %s' % AREA_ALGORITHM_VERSION)
    lines.append('  field resolver    : %s' % FIELD_RESOLVER_VERSION)
    lines.append('  flights in period : %d (loaded with boundary days: %d)'
                 % (summary['flights_in_period'], summary['flights_loaded']))
    lines.append('  raw sum m2        : %.4f (missing raw: %d)'
                 % (summary['raw_sum_m2'], summary['raw_missing_records']))
    lines.append('  certified sum m2  : %.4f over %d records'
                 % (summary['certified_sum_m2'], summary['certified_records']))
    lines.append('  provisional sum m2: %.4f over %d records'
                 % (summary['provisional_sum_m2'],
                    summary['provisional_records']))
    lines.append('  controller D sum  : %.4f m2 (certified only)'
                 % summary['controller_delta_sum_m2'])
    lines.append('  unresolved        : %d records, raw exposure %.4f m2'
                 % (summary['unresolved_records'],
                    summary['unresolved_raw_exposure_m2']))
    lines.append('  overlap records   : %d' % summary['overlap_records'])
    lines.append('  app without area  : %d'
                 % summary['application_without_area_records'])
    lines.append('  unreliable channel: %d'
                 % summary['unreliable_channel_records'])
    lines.append('  structural cand.  : %d (scalar match %d)'
                 % (summary['structural_candidates'],
                    summary['structural_scalar_matches']))
    lines.append('  v4 decoded        : %d' % summary['v4_summaries_decoded'])
    for title, key in (('status', 'status_counts'),
                       ('v4 availability', 'v4_availability'),
                       ('baseline', 'baseline_counts'),
                       ('window', 'window_quality_counts'),
                       ('field tier', 'tier_counts'),
                       ('eligibility', 'eligibility_counts'),
                       ('hardware source', 'hardware_source_counts'),
                       ('route identity', 'route_identity_counts'),
                       ('application', 'application_activity_counts'),
                       ('calc writes', 'calc_writes'),
                       ('field writes', 'field_writes')):
        items = summary.get(key) or {}
        lines.append('  %-18s: %s' % (title, ', '.join(
            '%s=%s' % (k, v) for k, v in sorted(items.items()))))
    return '\n'.join(lines)


def main(argv=None):
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        check_usage(args)
        date_from = parse_day(args.date_from)
        date_to = parse_day(args.date_to)
    except ValueError as exc:
        print('ERROR: %s' % exc)
        return EXIT_USAGE
    if date_from > date_to:
        print('ERROR: --from is after --to')
        return EXIT_USAGE
    if not os.path.exists(args.db_path):
        # [REASON]: sqlite3.connect would create an empty file and report zero.
        print('ERROR: database not found at %s - refusing to run.'
              % args.db_path)
        return EXIT_NO_DATABASE

    def progress(text):
        if not args.quiet:
            print('  ... %s' % text)

    try:
        summary = pipeline.recalculate(
            args.db_path, date_from, date_to, apply=args.apply,
            flight_ids=args.flight_ids, with_geometric=args.with_geometric,
            batch_size=args.batch_size, collect_rows=bool(args.rows_path),
            progress=progress)
    except (store.StoreError, pipeline.PipelineError) as exc:
        print('ERROR: %s' % exc)
        return EXIT_USAGE
    rows = summary.pop('flights', [])
    print(format_summary(summary, args.apply))
    if args.json_path:
        with open(args.json_path, 'w', encoding='utf-8') as fh:
            json.dump(summary, fh, ensure_ascii=False, indent=1, default=str)
        print('  summary json      : %s' % args.json_path)
    if args.rows_path:
        with open(args.rows_path, 'w', encoding='utf-8') as fh:
            json.dump(rows, fh, ensure_ascii=False, indent=1, default=str)
        print('  rows json         : %s' % args.rows_path)
    if not args.apply:
        print('Nothing was written. Re-run with --apply to store the result.')
    return EXIT_OK


if __name__ == '__main__':
    sys.exit(main())
