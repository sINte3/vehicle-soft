# -*- coding: utf-8 -*-
"""drone_collector/main.py -- the CLI entry point.

    python -m drone_collector.main --save-session
    python -m drone_collector.main --dry-run
    python -m drone_collector.main --from 2026-07-01 --to 2026-07-31
    python -m drone_collector.main                      (rolling window)

Without --from/--to the collector uses the rolling window from DJI_WINDOW_DAYS
and sends it as kind=incremental; with them it sends kind=backfill, unless
--kind says otherwise.

Exit codes (see drone_collector/README.md):
    0  success
    1  configuration error
    2  session missing or expired
    3  period verification failed
    4  the page walk did not complete
    5  the ingest endpoint rejected a batch
"""

import argparse
import sys

from drone_collector import config as config_module
from drone_collector.config import ConfigError, load_config
from drone_collector.logging_setup import format_run_summary, setup_logging
from drone_collector.sender import IngestRejected, send, write_dry_run
from drone_collector.session import SessionMissing, require_session
from drone_collector.window import compute_window, format_date, parse_date

EXIT_OK = 0
EXIT_CONFIG = 1
EXIT_SESSION = 2
EXIT_PERIOD = 3
EXIT_PAGINATION = 4
EXIT_INGEST = 5

KIND_INCREMENTAL = 'incremental'
KIND_BACKFILL = 'backfill'
KINDS = ('backfill', 'incremental', 'replay')


class UsageError(Exception):
    """A bad command line. Reported as a configuration error (exit 1)."""


class _Parser(argparse.ArgumentParser):
    """[REASON]: argparse exits with status 2 on a usage error, which is this
    program's "session missing" code. A wrong flag must not be reported as an
    expired DJI session, so usage errors are raised and mapped to exit 1."""

    def error(self, message):
        raise UsageError(message)


def build_parser():
    parser = _Parser(
        prog='python -m drone_collector.main',
        description='Collect DJI SmartFarm flights and send them to Vehicle '
                    'Soft.')
    parser.add_argument('--save-session', action='store_true',
                        help='open a browser, wait for a manual sign-in and '
                             'save the session; collects nothing')
    parser.add_argument('--dry-run', action='store_true',
                        help='write the flights to out/ instead of sending '
                             'them')
    parser.add_argument('--from', dest='date_from', metavar='YYYY-MM-DD',
                        help='first day of the period (inclusive)')
    parser.add_argument('--to', dest='date_to', metavar='YYYY-MM-DD',
                        help='last day of the period (inclusive)')
    parser.add_argument('--kind', choices=KINDS,
                        help='sync kind recorded by the ingest endpoint; '
                             'defaults to incremental for the rolling window '
                             'and to backfill when --from/--to are given')
    return parser


def resolve_period(args, cfg):
    """(date_from, date_to, kind) from the command line or the rolling window."""
    if bool(args.date_from) != bool(args.date_to):
        raise UsageError('--from and --to must be given together')

    if args.date_from:
        try:
            date_from = parse_date(args.date_from)
            date_to = parse_date(args.date_to)
        except ValueError as exc:
            raise UsageError(str(exc))
        if date_from > date_to:
            raise UsageError('--from %s is after --to %s'
                             % (args.date_from, args.date_to))
        return date_from, date_to, args.kind or KIND_BACKFILL

    date_from, date_to = compute_window(cfg.window_days, cfg.tz_offset_hours)
    return date_from, date_to, args.kind or KIND_INCREMENTAL


def main(argv=None):
    # Logging is configured before anything else so that a configuration
    # error lands in the file log too, not only on the console of whoever
    # happened to run it by hand.
    log = setup_logging(config_module.PACKAGE_ROOT / 'logs')

    summary = [
        ('kind', None), ('dry_run', False),
        ('period_from', None), ('period_to', None),
        ('pages', None), ('pages_expected', None),
        ('flights_captured', None), ('flights_deduped', None),
        ('self_duplicates', None), ('rejected_responses', None),
        ('batches', None), ('seen', None), ('new', None),
        ('duplicates', None), ('unresolved', None), ('errors', None),
        ('exit', None),
    ]
    state = dict(summary)

    code = EXIT_CONFIG
    summarize = True
    try:
        code = _run(argv, log, state)
    except SystemExit as exc:
        # argparse exits this way for --help. Nothing ran, so there is nothing
        # to summarise.
        summarize = False
        code = exc.code if isinstance(exc.code, int) else EXIT_OK
    except Exception:
        # [REASON]: the previous collector failed roughly one run in twenty,
        # almost always on a timeout, and the failures were only diagnosable
        # because something wrote them down. An unexpected exception is logged
        # with its traceback and reported as exit 1 rather than escaping and
        # leaving the scheduler with a bare non-zero status.
        log.exception('Unexpected failure')
        code = EXIT_CONFIG
    finally:
        if summarize:
            state['exit'] = code
            log.info(format_run_summary([(key, state.get(key))
                                         for key, _ in summary]))
    return code


def _run(argv, log, state):
    try:
        args = build_parser().parse_args(argv)
    except UsageError as exc:
        log.error('Usage error: %s', exc)
        return EXIT_CONFIG

    # --save-session and --dry-run never send anything, so they do not need
    # VEHICLE_SOFT_BASE_URL or DRONE_API_TOKEN. Every path that does send
    # requires both, and fails before the first request when one is missing.
    sends = not (args.save_session or args.dry_run)
    try:
        cfg = load_config(require_ingest=sends)
    except ConfigError as exc:
        log.error('Configuration error: %s', exc)
        return EXIT_CONFIG

    state['dry_run'] = bool(args.dry_run)
    log.info('Configuration: %s', cfg.describe())

    if args.save_session:
        return _save_session(cfg, log)

    try:
        date_from, date_to, kind = resolve_period(args, cfg)
    except UsageError as exc:
        log.error('Usage error: %s', exc)
        return EXIT_CONFIG
    state['kind'] = kind
    state['period_from'] = format_date(date_from)
    state['period_to'] = format_date(date_to)
    log.info('Period %s .. %s, kind=%s', format_date(date_from),
             format_date(date_to), kind)

    # Checked before the browser is launched: a missing session is the most
    # common reason for a failed run and it costs nothing to say so at once.
    try:
        require_session(cfg.storage_state)
    except SessionMissing as exc:
        log.error('%s', exc)
        return EXIT_SESSION

    try:
        from drone_collector.browser import (
            BrowserError,
            FlightCollector,
            PeriodVerificationFailed,
            SessionExpired,
        )
    except ImportError as exc:
        log.error('Playwright is not available in this environment (%s). '
                  'Install the collector dependencies: pip install -r '
                  'drone_collector/requirements.txt && python -m playwright '
                  'install chromium', exc)
        return EXIT_CONFIG

    try:
        with FlightCollector(cfg, log) as collector:
            result = collector.collect(date_from, date_to)
    except ImportError as exc:
        # playwright is imported lazily, inside start().
        log.error('Playwright is not available in this environment (%s). '
                  'Install the collector dependencies: pip install -r '
                  'drone_collector/requirements.txt && python -m playwright '
                  'install chromium', exc)
        return EXIT_CONFIG
    except SessionMissing as exc:
        log.error('%s', exc)
        return EXIT_SESSION
    except SessionExpired as exc:
        log.error('%s', exc)
        return EXIT_SESSION
    except PeriodVerificationFailed as exc:
        log.error('Period verification failed: %s', exc)
        log.error('Nothing was sent -- collecting the wrong period silently is '
                  'worse than failing.')
        return EXIT_PERIOD
    except BrowserError as exc:
        log.error('Browser run failed: %s', exc)
        return EXIT_PAGINATION

    state['pages'] = result.pages_captured
    state['pages_expected'] = result.total_pages
    state['flights_captured'] = result.flights_captured
    state['flights_deduped'] = len(result.flights)
    state['self_duplicates'] = result.self_duplicates
    state['rejected_responses'] = sum(result.rejected.values()) if result.rejected else 0
    if result.rejected:
        log.warning('Rejected responses by reason: %s', result.rejected)

    if args.dry_run:
        target = write_dry_run(result.flights, kind, date_from, date_to,
                               cfg.out_dir)
        log.info('Dry run: %d flight(s) written to %s', len(result.flights),
                 target)
        print('%d flight(s) written to %s' % (len(result.flights), target))
        return EXIT_OK if result.complete else EXIT_PAGINATION

    try:
        sent = send(result.flights, kind, date_from, date_to, cfg, logger=log)
    except IngestRejected as exc:
        log.error('Ingest rejected the batch: %s', exc)
        return EXIT_INGEST

    state['batches'] = sent.batches
    state['seen'] = sent.seen
    state['new'] = sent.new
    state['duplicates'] = sent.duplicates
    state['unresolved'] = sent.unresolved
    state['errors'] = sent.errors

    if not result.complete:
        # [REASON]: the flights that WERE captured are real and have already
        # been sent -- the ingest deduplicates by DJI flight id, so re-running
        # the same period is the normal repair and costs nothing. The non-zero
        # exit is what tells the scheduler and the operator that the walk was
        # short; reporting success here would hide a half-collected period.
        log.error('Page walk incomplete: %d of %s pages. The captured flights '
                  'were sent; re-run the same period to finish it.',
                  result.pages_captured, result.total_pages)
        return EXIT_PAGINATION

    return EXIT_OK


def _save_session(cfg, log):
    try:
        from drone_collector.session import save_session_interactive
        save_session_interactive(cfg)
    except ImportError as exc:
        log.error('Playwright is not available in this environment (%s). '
                  'Install the collector dependencies: pip install -r '
                  'drone_collector/requirements.txt && python -m playwright '
                  'install chromium', exc)
        return EXIT_CONFIG
    except SessionMissing as exc:
        log.error('%s', exc)
        return EXIT_SESSION
    except Exception as exc:
        log.error('Could not save the session: %s', exc)
        return EXIT_SESSION
    return EXIT_OK


if __name__ == '__main__':
    sys.exit(main())
