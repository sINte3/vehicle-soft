# -*- coding: utf-8 -*-
"""drone_collector/main.py -- the CLI entry point.

    python -m drone_collector.main --save-session
    python -m drone_collector.main --dry-run
    python -m drone_collector.main --from 2026-07-01 --to 2026-07-31
    python -m drone_collector.main                      (rolling window)
    python -m drone_collector.main --lands              (field directory)
    python -m drone_collector.main --lands --dry-run
    python -m drone_collector.main --lands --with-geometry   (full polygons)
    python -m drone_collector.main --routes --from 2026-06-01 --to 2026-06-30
    python -m drone_collector.main --routes --ids-file ids.txt --dry-run

Without --from/--to the collector uses the rolling window from DJI_WINDOW_DAYS
and sends it as kind=incremental; with them it sends kind=backfill, unless
--kind says otherwise.

Exit codes (see drone_collector/README.md):
    0  success
    1  configuration error
    2  session missing or expired
    3  period verification failed
    4  the page walk did not complete (flights, or --lands)
    5  the ingest endpoint rejected a batch
    6  a window captured zero flights, or --lands captured zero
       contours (usually the wrong region)
    7  the session is in the wrong region
   10  the cabinet refused to serve the routes (--routes only)

    8 and 9 are deliberately skipped: they belong to the other entry point of
    this package, `python -m drone_collector.devices`.

--routes and --lands --with-geometry belong to DRONE-COVERAGE-001 stage B.
They collect into the on-disk outbox (drone_collector/outbox.py) and send
NOTHING to Vehicle Soft: the receiving endpoints are stage C. Neither run
touches the flight-collection counters, and neither writes a drone_sync_logs
row -- by construction, because neither calls the sender at all.
"""

import argparse
import sys

from drone_collector import config as config_module
from drone_collector.config import ConfigError, load_config
from drone_collector.logging_setup import format_run_summary, setup_logging
from drone_collector.sender import (IngestRejected, send, send_lands,
                                    write_dry_run, write_lands_dry_run)
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
# [REASON]: 10, а не 8. Коды 8 и 9 в этом пакете уже заняты -- их выдаёт
# `python -m drone_collector.devices` (нет устройств в списке / устройства не
# сходятся с окном). Одно число с двумя смыслами внутри одного пакета
# означает, что оператор, читающий код выхода из журнала планировщика,
# однажды прочтёт его неверно.
EXIT_ROUTE_REFUSED = 10

KIND_INCREMENTAL = 'incremental'
KIND_BACKFILL = 'backfill'
KINDS = ('backfill', 'incremental', 'replay')

MODE_FLIGHTS = 'flights'
MODE_LANDS = 'lands'
MODE_ROUTES = 'routes'

FLIGHT_SUMMARY_KEYS = (
    'mode', 'kind', 'dry_run', 'period_from', 'period_to',
    'windows', 'windows_completed', 'region', 'page_size',
    'pages', 'pages_expected', 'flights_captured', 'flights_deduped',
    'self_duplicates', 'rejected_responses',
    'batches', 'seen', 'new', 'duplicates', 'unresolved', 'errors', 'exit',
)

ROUTE_SUMMARY_KEYS = (
    'mode', 'dry_run', 'period_from', 'period_to', 'region',
    'flights_seen', 'ids_requested', 'batches', 'responses',
    'routes_new', 'routes_duplicates', 'routes_missing', 'routes_errors',
    'routes_unlinked', 'routes_without_width', 'route_points',
    'quarantined', 'outbox_pending', 'exit',
)

LAND_SUMMARY_KEYS = (
    'mode', 'dry_run', 'pages', 'total_count', 'lands_captured',
    'lands_deduped', 'self_duplicates', 'rejected_responses', 'complete',
    'batches', 'seen', 'new', 'updated', 'unchanged', 'errors',
    'geometry_seen', 'geometry_downloaded', 'geometry_queued',
    'geometry_unchanged', 'geometry_failed', 'geometry_bytes', 'exit',
)


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
    parser.add_argument('--lands', action='store_true',
                        help='collect the Field Management directory (the '
                             'contour names operators type on the controller) '
                             'instead of flights; takes no period')
    parser.add_argument('--with-geometry', dest='with_geometry',
                        action='store_true',
                        help='with --lands: also download the full contour '
                             'polygon of every field into the on-disk outbox. '
                             'The signed link is used in memory and stored '
                             'nowhere. Sends nothing to Vehicle Soft.')
    parser.add_argument('--routes', action='store_true',
                        help='collect the geometric route of each flight into '
                             'the on-disk outbox. Needs either --from/--to '
                             '(the flights of that period name the ids) or '
                             '--ids-file. Sends nothing to Vehicle Soft.')
    parser.add_argument('--ids-file', dest='ids_file', metavar='PATH',
                        help='with --routes: read the flight ids to request '
                             'from this file, one per line, # comments '
                             'allowed. Skips the flight-list walk entirely.')
    return parser


def check_usage(args):
    """Everything the command line can be wrong about, in one place.

    [REASON]: checked before the environment is read. A malformed command line
    reported as "VEHICLE_SOFT_BASE_URL is not set" sends the operator to the
    wrong file entirely. This was one guard for --lands; the stage B flags
    made it four, and four scattered `if`s in _run() would have been the next
    place a combination went unchecked.
    """
    if args.lands and args.routes:
        raise UsageError('--lands and --routes are two different walks; run '
                         'them one at a time')
    if args.lands and (args.date_from or args.date_to or args.kind):
        raise UsageError('--lands takes no period and no --kind. The Field '
                         'Management directory is a snapshot of the current '
                         'state; it has no date filter.')
    if args.with_geometry and not args.lands:
        raise UsageError('--with-geometry extends the directory walk and only '
                         'makes sense together with --lands')
    if args.ids_file and not args.routes:
        raise UsageError('--ids-file names the flights whose routes to '
                         'collect and only makes sense together with --routes')
    if args.routes and args.kind:
        raise UsageError('--routes sends nothing to Vehicle Soft, so --kind '
                         'has nothing to label')
    if args.routes and not args.ids_file and not args.date_from:
        raise UsageError('--routes needs to know WHICH flights: give '
                         '--from/--to, and the flights of that period name '
                         'the ids, or give --ids-file')


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

    state = {'mode': MODE_FLIGHTS, 'dry_run': False}

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
            # [REASON]: one line per run with always the same keys in the same
            # order -- but the two walks have almost no counters in common, and
            # printing twenty '-' for a lands run would make the flight keys
            # look like failures. Two templates, chosen by what actually ran.
            keys = {MODE_LANDS: LAND_SUMMARY_KEYS,
                    MODE_ROUTES: ROUTE_SUMMARY_KEYS}.get(
                        state.get('mode'), FLIGHT_SUMMARY_KEYS)
            log.info(format_run_summary([(key, state.get(key))
                                         for key in keys]))
    return code


def _run(argv, log, state):
    try:
        args = build_parser().parse_args(argv)
    except UsageError as exc:
        log.error('Usage error: %s', exc)
        return EXIT_CONFIG

    try:
        check_usage(args)
    except UsageError as exc:
        log.error('Usage error: %s', exc)
        return EXIT_CONFIG

    # --save-session, --dry-run and --routes never send anything, so they do
    # not need VEHICLE_SOFT_BASE_URL or DRONE_API_TOKEN. Every path that does
    # send requires both, and fails before the first request when one is
    # missing.
    sends = not (args.save_session or args.dry_run or args.routes)
    try:
        cfg = load_config(require_ingest=sends)
    except ConfigError as exc:
        log.error('Configuration error: %s', exc)
        return EXIT_CONFIG

    state['dry_run'] = bool(args.dry_run)
    log.info('Configuration: %s', cfg.describe())

    if args.save_session:
        return _save_session(cfg, log)

    if args.lands:
        return _run_lands(args, cfg, log, state)

    if args.routes:
        return _run_routes(args, cfg, log, state)

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


def incomplete_walk_message(collected, total_count, dry_run):
    """What a short directory walk ends on.

    [REASON]: the sentence about sending is CHOSEN by dry_run, not written
    once for both. The first version said "what was collected has been sent"
    unconditionally, and the very first real dry run on production printed it
    -- while a dry run sends nothing by definition. An operator message that
    states something untrue about what reached the database is worse than no
    message at all: the next decision gets made on it, and here that decision
    is whether the database now holds a partial directory.

    [REASON]: what WAS collected is real, and on a real run it has already
    been sent -- the ingest upserts by DJI uuid, so re-running is the normal
    repair and costs nothing but time. That is why the advice is "re-run",
    not "clean up first".
    """
    tail = ('Nothing was sent -- this is a dry run; re-run --lands --dry-run '
            'to finish it.' if dry_run else
            'What was collected has been sent; re-run --lands to finish it.')
    return ('Directory walk incomplete: %d contour(s) collected of %s '
            'reported by DJI. %s'
            % (collected,
               total_count if total_count is not None
               else 'an unknown number', tail))


def _run_lands(args, cfg, log, state):
    """Snapshot the Field Management directory. Returns an exit code."""
    state['mode'] = MODE_LANDS

    # Checked before the browser is launched, same as the flight walk.
    try:
        require_session(cfg.storage_state)
    except SessionMissing as exc:
        log.error('%s', exc)
        return EXIT_SESSION

    try:
        from drone_collector.lands import LandCollector, LandsError
    except ImportError as exc:
        log.error('Playwright is not available in this environment (%s). '
                  'Install the collector dependencies: pip install -r '
                  'drone_collector/requirements.txt && python -m playwright '
                  'install chromium', exc)
        return EXIT_CONFIG

    geometry_clean = True
    try:
        with LandCollector(cfg, log) as collector:
            result = collector.collect()
            # [REASON]: the polygons are downloaded INSIDE the `with`, while
            # the browser context is still alive. The signed links live only
            # in the directory response that is already in memory, and they
            # expire six hours after DJI issued them -- there is no later
            # moment at which this could be done from saved data.
            if args.with_geometry and result.lands:
                geometry_clean = _run_geometry(collector, result, args, cfg,
                                               log, state)
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
    except LandsError as exc:
        log.error('Directory walk failed: %s', exc)
        return EXIT_PAGINATION

    state['pages'] = result.pages_captured
    state['total_count'] = result.total_count
    state['lands_captured'] = result.nodes_captured
    state['lands_deduped'] = len(result.lands)
    state['self_duplicates'] = result.self_duplicates
    state['rejected_responses'] = sum(result.rejected.values())
    state['complete'] = result.complete
    if result.rejected:
        log.warning('Rejected responses by reason: %s', result.rejected)

    # [REASON]: the same guard the flight walk has, for the same reason -- a
    # session switched to another region returns an empty directory with no
    # error at all, and a snapshot of nothing would overwrite nothing and
    # report success.
    if not result.lands:
        log.error('EMPTY DIRECTORY: zero contours captured. Nothing is sent. '
                  'The usual cause is a session switched to another region, '
                  'which returns an empty list with no error.')
        return EXIT_EMPTY

    if args.dry_run:
        target = write_lands_dry_run(result.lands, cfg.out_dir,
                                     total_count=result.total_count)
        log.info('Dry run: %d contour(s) written to %s', len(result.lands),
                 target)
        print('%d contour(s) written to %s' % (len(result.lands), target))
    else:
        try:
            sent = send_lands(result.lands, cfg, logger=log)
        except IngestRejected as exc:
            log.error('Ingest rejected the batch: %s', exc)
            return EXIT_INGEST
        for key in ('batches', 'seen', 'new', 'updated', 'unchanged',
                    'errors'):
            state[key] = getattr(sent, key)

    if not result.complete:
        # [REASON]: the non-zero exit is what says the snapshot is partial;
        # reporting success would leave a half-collected directory looking
        # authoritative, and a contour missing from it is invisible
        # afterwards -- it simply never matches anything.
        log.error('%s', incomplete_walk_message(
            len(result.lands), result.total_count, args.dry_run))
        return EXIT_PAGINATION
    if not geometry_clean:
        log.error('The directory itself is complete, but at least one polygon '
                  'is missing. Re-run --lands --with-geometry: what already '
                  'arrived is in the queue and is not fetched again.')
        return EXIT_PAGINATION
    return EXIT_OK


def _open_outbox(cfg, log):
    """The stage B queue, ready to write. Sweeps leftovers of a torn write."""
    from drone_collector.outbox import Outbox

    outbox = Outbox(cfg.outbox_dir).prepare()
    swept = outbox.sweep_stale_temp()
    if swept:
        # [REASON]: a torn write leaves a .tmp that no reader ever picks up.
        # Sweeping is silent housekeeping, but the COUNT is logged: several of
        # them in a row means runs are being killed mid-write, and that is
        # worth knowing before the disk says so.
        log.warning('Swept %d unfinished queue file(s) left by an interrupted '
                    'run.', swept)
    log.info('Outbox: %s', outbox.counts())
    return outbox


def _flight_ids_of_period(collector, args, cfg, log, state):
    """Walk the flight list and return the ids of that period.

    [REASON]: the route endpoint takes flight ids, not a period -- confirmed
    on live traffic, V1_REQUEST_BODY_CONFIRMED. So a period-driven route run
    first asks the cabinet WHICH flights the period holds, using the walk that
    has been in production since 2026-08-08, and then asks for those routes.
    The walk is read-only here: nothing is sent, so no drone_sync_logs row is
    created and the flight counters do not move.
    """
    date_from, date_to, _kind = resolve_period(args, cfg)
    state['period_from'] = format_date(date_from)
    state['period_to'] = format_date(date_to)
    log.info('Route period %s .. %s', format_date(date_from),
             format_date(date_to))

    ids = []
    seen = 0
    for window_from, window_to in split_by_calendar_year(date_from, date_to):
        result = collector.collect_window(window_from, window_to)
        seen += len(result.flights)
        for flight in result.flights:
            value = flight.get('id')
            if value is not None:
                ids.append(value)
        if not result.complete:
            log.error('Page walk incomplete for %s .. %s; the ids of that '
                      'window are partial and so will the routes be.',
                      format_date(window_from), format_date(window_to))
    state['flights_seen'] = seen
    return ids


def _run_routes(args, cfg, log, state):
    """DRONE-COVERAGE-001 stage B: collect geometric routes. Returns an exit code."""
    state['mode'] = MODE_ROUTES

    try:
        require_session(cfg.storage_state)
    except SessionMissing as exc:
        log.error('%s', exc)
        return EXIT_SESSION

    try:
        from drone_collector.routes import (PageRouteTransport, RouteRun,
                                            RouteRequestRefused, RouteRunError,
                                            read_ids_file)
        from drone_collector.routes import write_dry_run as write_routes_dry_run
        from drone_collector.outbox import OutboxError
    except ImportError as exc:  # pragma: no cover -- import of our own module
        log.error('The route collector could not be imported (%s)', exc)
        return EXIT_CONFIG

    given_ids = None
    if args.ids_file:
        try:
            given_ids = read_ids_file(args.ids_file)
        except (OSError, RouteRunError) as exc:
            log.error('Could not read --ids-file: %s', exc)
            return EXIT_CONFIG
        if not given_ids:
            log.error('--ids-file %s names no flight at all; nothing to do.',
                      args.ids_file)
            return EXIT_EMPTY
        log.info('%d flight id(s) read from %s', len(given_ids), args.ids_file)

    outbox = _open_outbox(cfg, log)

    try:
        from drone_collector.browser import (BrowserError, FlightCollector,
                                             PeriodVerificationFailed,
                                             RegionMismatch, SessionExpired)
    except ImportError as exc:
        log.error('Playwright is not available in this environment (%s). '
                  'Install the collector dependencies: pip install -r '
                  'drone_collector/requirements.txt && python -m playwright '
                  'install chromium', exc)
        return EXIT_CONFIG

    errors = (BrowserError, PeriodVerificationFailed, RegionMismatch,
              SessionExpired, SessionMissing)
    result = None
    run = None
    try:
        with FlightCollector(cfg, log) as collector:
            collector.open_records()
            state['region'] = collector.check_region(cfg.expected_region)

            ids = (given_ids if given_ids is not None
                   else _flight_ids_of_period(collector, args, cfg, log, state))
            if not ids:
                log.error('EMPTY: no flight id to ask a route for. The usual '
                          'cause is a session switched to another region, '
                          'which returns an empty list with no error.')
                return EXIT_EMPTY

            run = RouteRun(
                outbox,
                PageRouteTransport(collector.page, cfg.route_api_origin, log),
                logger=log, batch_size=cfg.route_batch_size,
                batch_pause_s=cfg.route_pause_ms / 1000.0,
                quarantine_dir=cfg.outbox_dir / 'quarantine',
                dry_run=bool(args.dry_run))
            result = run.collect(ids)
    except ImportError as exc:
        log.error('Playwright is not available in this environment (%s).', exc)
        return EXIT_CONFIG
    except RouteRequestRefused as exc:
        # [REASON]: its own exit code. "The cabinet would not serve the routes"
        # and "the page walk was short" need different answers from whoever is
        # looking, and one shared non-zero code makes them the same answer.
        log.error('%s', exc)
        log.error('Nothing further was collected. Re-run after signing in '
                  'again; if it repeats with a fresh session, the request made '
                  'from the page is not being signed the way the cabinet '
                  'expects, and that is a finding to write down.')
        return EXIT_ROUTE_REFUSED
    except RouteRunError as exc:
        log.error('Route run failed: %s', exc)
        return EXIT_PAGINATION
    except errors as exc:
        return _exit_code_for(exc, log)

    _account_for_routes(result, outbox, state)

    if args.dry_run:
        try:
            target = write_routes_dry_run(result, run.prepared_bodies,
                                          cfg.out_dir)
        except OutboxError as exc:
            # [REASON]: the dry-run report is checked for secret markers before
            # it is written, exactly as the queue checks an envelope. A refusal
            # is a finding about the payload, not a crash, and it deserves the
            # same named exit as any other failed route run -- not the bare
            # traceback of the catch-all in main().
            log.error('The dry-run report was NOT written: %s', exc)
            return EXIT_PAGINATION
        log.info('Dry run: nothing was queued; %d route(s) written to %s',
                 len(run.prepared_bodies), target)
        print('%d route(s) written to %s (nothing was queued)'
              % (len(run.prepared_bodies), target))

    if state.get('flights_seen') is not None:
        # Acceptance criterion 1 of stage B, checked by the run itself rather
        # than by hand afterwards.
        collected = result.new + result.duplicates
        log.info('Period check: %s flight(s) in the window, %d route(s) '
                 'collected, %d without a route in DJI.',
                 state['flights_seen'], collected, result.missing)

    if result.errors:
        log.error('%d route(s) failed. The queue keeps what did succeed; '
                  're-running the same ids is safe and asks only for what is '
                  'still missing.', result.errors)
        return EXIT_PAGINATION
    return EXIT_OK


def _account_for_routes(result, outbox, state):
    """Copy the route counters into the run summary."""
    state['ids_requested'] = result.requested
    state['batches'] = result.batches
    state['responses'] = result.responses
    state['routes_new'] = result.new
    state['routes_duplicates'] = result.duplicates
    state['routes_missing'] = result.missing
    state['routes_errors'] = result.errors
    state['routes_unlinked'] = result.unlinked
    state['routes_without_width'] = result.without_width
    state['route_points'] = result.points
    state['quarantined'] = result.quarantined
    state['outbox_pending'] = outbox.counts()['pending']


def _run_geometry(collector, result, args, cfg, log, state):
    """--lands --with-geometry: download the full polygons. Returns True on
    a clean pass, False when at least one contour failed."""
    from drone_collector.geometry import (ContextGeometryDownloader,
                                          GeometryRun, STATUS_OK,
                                          STATUS_UNCHANGED)
    from drone_collector.geometry import write_dry_run as write_geometry_dry_run

    outbox = _open_outbox(cfg, log)
    run = GeometryRun(outbox, ContextGeometryDownloader(collector.context, log),
                      logger=log, pause_s=cfg.geometry_pause_ms / 1000.0,
                      dry_run=bool(args.dry_run))
    geometry = run.collect(result.lands)

    failed = sum(count for status, count in geometry.by_status.items()
                 if status not in (STATUS_OK, STATUS_UNCHANGED))
    state['geometry_seen'] = geometry.seen
    state['geometry_downloaded'] = geometry.downloaded
    state['geometry_queued'] = geometry.queued
    state['geometry_unchanged'] = geometry.by_status.get(STATUS_UNCHANGED, 0)
    state['geometry_failed'] = failed
    state['geometry_bytes'] = geometry.bytes

    if args.dry_run:
        target = write_geometry_dry_run(geometry, run.prepared_bodies,
                                        cfg.out_dir)
        log.info('Dry run: nothing was queued; %d contour(s) written to %s',
                 len(run.prepared_bodies), target)

    if failed:
        # [REASON]: a contour whose polygon did not arrive is not a silent
        # gap. It is named by status, and the run says so on exit -- a
        # half-collected set of polygons that reported success is exactly how
        # a missing field becomes invisible afterwards.
        log.error('%d contour(s) did not yield a usable polygon: %s',
                  failed, {status: count
                           for status, count in geometry.by_status.items()
                           if status not in (STATUS_OK, STATUS_UNCHANGED)})
    return not failed


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
