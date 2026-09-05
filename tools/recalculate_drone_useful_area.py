# -*- coding: utf-8 -*-
"""tools/recalculate_drone_useful_area.py -- пересчёт полезной площади дронов.

Тонкая обёртка над `drone_coverage_recalc`: разбор командной строки, коды
возврата и печать сводки. Ни расчёта, ни SQL здесь нет.

Режим выбирается ЯВНО и ровно один. Ни `--dry-run`, ни `--apply` по умолчанию
не подразумевается: инструмент, который пишет в базу потому, что оператор
забыл флаг, -- это тот же класс дефекта, что скрытое допущение в формуле.

Запуск (служба должна быть ОСТАНОВЛЕНА перед --apply):

  cd C:\\transport-report
  & "C:\\Program Files\\Python314\\python.exe" tools\\recalculate_drone_useful_area.py --from 2026-06-05 --to 2026-06-05 --dry-run
  & "C:\\Program Files\\Python314\\python.exe" tools\\recalculate_drone_useful_area.py --from 2026-06-05 --to 2026-06-05 --apply

Коды возврата:
  0 -- пересчёт выполнен;
  1 -- ошибка командной строки или пересчёта;
  2 -- база не найдена (файл НЕ создаётся).

Вывод только ASCII: он читается в консоли PowerShell и в журнале NSSM, где
кодовая страница не наша гарантия. Ни координат, ни настоящих идентификаторов
вылетов в сводке нет и быть не может -- она состоит из счётчиков.
"""

import argparse
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

import drone_coverage_recalc as recalc  # noqa: E402
import drone_useful_area as ua  # noqa: E402

DEFAULT_DB = os.path.join(ROOT, 'instance', 'transport.db')

EXIT_OK = 0
EXIT_USAGE = 1
EXIT_NO_DATABASE = 2


def build_parser():
    parser = argparse.ArgumentParser(
        prog='recalculate_drone_useful_area.py',
        description='Recalculate the estimated useful area of drone works '
                    'over an explicit period. The production rule is %s; '
                    'an older version can be replayed with --dry-run only.'
                    % ua.ALGORITHM_VERSION)
    parser.add_argument('--from', dest='date_from', required=True,
                        metavar='YYYY-MM-DD',
                        help='first local (UTC+5) day of the period')
    parser.add_argument('--to', dest='date_to', required=True,
                        metavar='YYYY-MM-DD',
                        help='last local (UTC+5) day of the period')
    parser.add_argument('--dry-run', action='store_true',
                        help='compute and report, write nothing')
    parser.add_argument('--apply', action='store_true',
                        help='compute and write, in one transaction')
    parser.add_argument('--db', dest='db_path', default=DEFAULT_DB,
                        metavar='PATH',
                        help='database file (default: instance/transport.db)')
    parser.add_argument('--algorithm-version', dest='algorithm_version',
                        choices=ua.ALGORITHM_VERSIONS,
                        default=ua.ALGORITHM_VERSION, metavar='VERSION',
                        help='rules to compute by (default: %s). Any other '
                             'version is a read-only regression replay and '
                             'is accepted with --dry-run only'
                             % ua.ALGORITHM_VERSION)
    parser.add_argument('--show-works', action='store_true',
                        help='print the decomposition of every work: swaths, '
                             'union, clipping, contour, uncertainty, status, '
                             'segment lengths by reason, implied speed. No '
                             'coordinates, no flight identifiers')
    return parser


def check_usage(args):
    """Всё, в чём командная строка может быть неправа, в одном месте."""
    if args.dry_run and args.apply:
        raise recalc.RecalcError(
            '--dry-run and --apply are mutually exclusive: choose one')
    if not args.dry_run and not args.apply:
        raise recalc.RecalcError(
            'choose a mode explicitly: --dry-run writes nothing, --apply '
            'writes in one transaction')
    # [REASON]: старая версия правил -- регрессионный повтор, а не выбор.
    # Записать её значило бы положить в базу число, посчитанное по правилам,
    # которые действующими уже не являются, под честной старой меткой.
    if args.apply and args.algorithm_version != ua.ALGORITHM_VERSION:
        raise recalc.RecalcError(
            '--apply writes only the production rule %s; %s can be replayed '
            'with --dry-run only' % (ua.ALGORITHM_VERSION,
                                     args.algorithm_version))


def main(argv=None):
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        check_usage(args)
        date_from = recalc.parse_day(args.date_from)
        date_to = recalc.parse_day(args.date_to)
        if date_from > date_to:
            raise recalc.RecalcError('--from %s is after --to %s'
                                     % (args.date_from, args.date_to))
    except recalc.RecalcError as exc:
        print('ERROR: %s' % exc)
        return EXIT_USAGE

    if not os.path.exists(args.db_path):
        # [REASON]: sqlite3.connect would CREATE an empty file here, and the
        # run would then report zero works and look successful.
        print('ERROR: database not found at %s - refusing to run.'
              % args.db_path)
        return EXIT_NO_DATABASE

    try:
        summary = recalc.recalculate(
            args.db_path, date_from, date_to, apply=args.apply,
            params=ua.params_for_version(args.algorithm_version),
            collect_works=args.show_works)
    except recalc.RecalcError as exc:
        print('ERROR: %s' % exc)
        return EXIT_USAGE

    print('Period: %s .. %s (local UTC+5)' % (date_from, date_to))
    print(recalc.format_summary(summary))
    if args.show_works:
        print(recalc.format_works(summary))
    if not args.apply:
        print('')
        print('Nothing was written. Re-run with --apply to store the result.')
    return EXIT_OK


if __name__ == '__main__':
    sys.exit(main())
