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
    6  a window captured zero flights (usually the wrong region)
    7  the session is in the wrong region
"""

import argparse
import sys

from drone_collector import config as config_module
from drone_collector.config import ConfigError, load_config
from drone_collector.logging_setup import format_run_summary, setup_logging
from drone_collector.sender import IngestRejected, send, write_dry_run
from drone_collector.session import SessionMissing, require_session
from drone_collector.window import (compute_window, format_date, parse_date,
                                    split_by_calendar_year)

EXIT_OK = 0
EXIT_CONFIG = 1
EXIT_SESSION = 2
EXIT_PERIOD = 3
EXIT_PAGINATION = 4
EXIT_INGEST = 5
EXIT_EMPTY = 6
EXIT_REGION = 7

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
        ('windows', None), ('windows_completed', None),
        ('region', None), ('page_size', None),
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

    # [REASON]: the picker RESETS a range that crosses a calendar-year
    # boundary, so a period that spans one is collected a year at a time. Each
    # sub-window is a full cycle of its own: set the period, verify it, walk
    # it, send it.
    windows = split_by_calendar_year(date_from, date_to)
    state['windows'] = len(windows)
    if len(windows) > 1:
        log.info('The period crosses a calendar-year boundary, which the '
                 'picker resets; splitting into %d windows: %s', len(windows),
                 ', '.join('%s..%s' % (format_date(a), format_date(b))
                           for a, b in windows))

    try:
        from drone_collector.browser import (
            BrowserError,
            FlightCollector,
            PeriodVerificationFailed,
            RegionMismatch,
            SessionExpired,
        )
    except ImportError as exc:
        log.error('Playwright is not available in this environment (%s). '
                  'Install the collector dependencies: pip install -r '
                  'drone_collector/requirements.txt && python -m playwright '
                  'install chromium', exc)
        return EXIT_CONFIG

    errors = (BrowserError, PeriodVerificationFailed, RegionMismatch,
              SessionExpired, SessionMissing)
    completed = []
    code = EXIT_OK

    try:
        with FlightCollector(cfg, log) as collector:
            collector.open_records()
            state['region'] = collector.check_region(cfg.expected_region)

            for index, (window_from, window_to) in enumerate(windows, start=1):
                log.info('Window %d/%d: %s .. %s', index, len(windows),
                         format_date(window_from), format_date(window_to))
                result = collector.collect_window(window_from, window_to)
                code = _account_for(result, args, kind, cfg, log, state)
                if code != EXIT_OK:
                    break
                completed.append((window_from, window_to))
    except ImportError as exc:
        # playwright is imported lazily, inside start().
        log.error('Playwright is not available in this environment (%s). '
                  'Install the collector dependencies: pip install -r '
                  'drone_collector/requirements.txt && python -m playwright '
                  'install chromium', exc)
        return EXIT_CONFIG
    except errors as exc:
        code = _exit_code_for(exc, log)
    except IngestRejected as exc:
        log.error('Ingest rejected the batch: %s', exc)
        code = EXIT_INGEST

    state['windows_completed'] = len(completed)
    if len(windows) > 1:
        # [REASON]: a failure in one sub-window must not discard the ones
        # already sent. Those flights are in the database; the operator needs
        # to know exactly which periods still need collecting, and re-running
        # a completed one is harmless because the ingest deduplicates.
        log.info('Windows completed and sent: %s',
                 ', '.join('%s..%s' % (format_date(a), format_date(b))
                           for a, b in completed) or 'none')
        if code != EXIT_OK:
            remaining = [w for w in windows if w not in completed]
            log.error('Windows NOT collected: %s',
                      ', '.join('%s..%s' % (format_date(a), format_date(b))
                                for a, b in remaining))

    return code


def _exit_code_for(exc, log):
    """Map a browser-side failure onto its documented exit code."""
    from drone_collector.browser import (PeriodVerificationFailed,
                                         RegionMismatch, SessionExpired)

    if isinstance(exc, RegionMismatch):
        log.error('%s', exc)
        return EXIT_REGION
    if isinstance(exc, (SessionMissing, SessionExpired)):
        log.error('%s', exc)
        return EXIT_SESSION
    if isinstance(exc, PeriodVerificationFailed):
        log.error('Period verification failed: %s', exc)
        log.error('Nothing was sent -- collecting the wrong period silently is '
                  'worse than failing.')
        return EXIT_PERIOD
    log.error('Browser run failed: %s', exc)
    return EXIT_PAGINATION


def _account_for(result, args, kind, cfg, log, state):
    """Record one window's result and send it. Returns an exit code."""
    _accumulate(state, result)
    if result.rejected:
        log.warning('Rejected responses by reason: %s', result.rejected)

    # Guard A: an empty window is an error, not a success.
    #
    # [REASON]: a session in the wrong region returns zero rows with no error
    # at all -- the run looks successful and collects nothing. This guard
    # needs no selector and is the one that actually protects the data.
    if not result.flights:
        if cfg.allow_empty_window:
            log.warning('EMPTY WINDOW %s .. %s: zero flights captured, and '
                        'DJI_ALLOW_EMPTY_WINDOW is set, so the run continues. '
                        'If this window was not expected to be empty, the '
                        'session is probably in the wrong region.',
                        format_date(result.date_from),
                        format_date(result.date_to))
            return EXIT_OK
        log.error('EMPTY WINDOW %s .. %s: zero flights captured. Nothing is '
                  'sent. The usual cause is a session switched to another '
                  'region, which returns an empty list with no error. Set '
                  'DJI_ALLOW_EMPTY_WINDOW=true if this period really is '
                  'empty.', format_date(result.date_from),
                  format_date(result.date_to))
        return EXIT_EMPTY

    if args.dry_run:
        target = write_dry_run(result.flights, kind, result.date_from,
                               result.date_to, cfg.out_dir)
        log.info('Dry run: %d flight(s) written to %s', len(result.flights),
                 target)
        print('%d flight(s) written to %s' % (len(result.flights), target))
    else:
        sent = send(result.flights, kind, result.date_from, result.date_to,
                    cfg, logger=log)
        for key in ('batches', 'seen', 'new', 'duplicates', 'unresolved',
                    'errors'):
            state[key] = (state.get(key) or 0) + getattr(sent, key)

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


def _accumulate(state, result):
    """Sum one window's collection counters into the run summary."""
    state['pages'] = (state.get('pages') or 0) + result.pages_captured
    state['pages_expected'] = ((state.get('pages_expected') or 0)
                               + (result.total_pages or 0))
    state['flights_captured'] = ((state.get('flights_captured') or 0)
                                 + result.flights_captured)
    state['flights_deduped'] = ((state.get('flights_deduped') or 0)
                                + len(result.flights))
    state['self_duplicates'] = ((state.get('self_duplicates') or 0)
                                + result.self_duplicates)
    state['rejected_responses'] = ((state.get('rejected_responses') or 0)
                                   + sum(result.rejected.values()))
    state['page_size'] = result.page_size


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
