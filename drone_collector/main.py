# -*- coding: utf-8 -*-
"""drone_collector/main.py -- the CLI entry point.

    python -m drone_collector.main --save-session
    python -m drone_collector.main --dry-run
    python -m drone_collector.main --from 2026-07-01 --to 2026-07-31
    python -m drone_collector.main                      (rolling window)
    python -m drone_collector.main --lands              (field directory)
    python -m drone_collector.main --lands --dry-run
    python -m drone_collector.main --lands --with-geometry   (all polygons)
    python -m drone_collector.main --lands --with-geometry --geometry-id UUID
    python -m drone_collector.main --routes --from 2026-06-01 --to 2026-06-30
    python -m drone_collector.main --routes --ids-file ids.txt --dry-run
    python -m drone_collector.main --area-48h           (DJI-AREA-48H study)

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
   11  --geometry-id named a contour the directory does not hold
   12  route collection is disabled: the native fetch transport was
       disproved on the live cabinet and no valid UI transport exists yet
   13  --route-ui-probe saw route traffic, but none of it was a confirmed
       route POST (wrong host, method, status, payload or id sets)
   14  --area-48h could not write a shareable report because it would have
       carried something private; nothing was written

    8 and 9 are deliberately skipped: they belong to the other entry point of
    this package, `python -m drone_collector.devices`.

--routes and --lands --with-geometry belong to DRONE-COVERAGE-001 stage B.
They collect into the on-disk outbox (drone_collector/outbox.py) and send
NOTHING to Vehicle Soft: the receiving endpoints are stage C. Neither run
touches the flight-collection counters, and neither writes a drone_sync_logs
row -- by construction, because neither calls the sender at all.

That last sentence was false for `--lands --with-geometry` until this fix: the
run counted as a sending one, demanded the ingest token, and posted the whole
directory to /drones/api/land_sync. Two things now hold it true and are held by
tests: needs_no_ingest() names the geometry run, and _run_lands() branches on
--with-geometry BEFORE the send. Plain `--lands` still sends the snapshot --
that is what it is for, and nothing about it changed.
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
# [REASON]: its own code, not EXIT_EMPTY. "The directory holds no contour at
# all" and "the directory does not hold the ONE you named" call for different
# answers from whoever reads the scheduler log, and a shared code makes them
# the same answer.
EXIT_GEOMETRY_NOT_FOUND = 11
# [REASON]: "the cabinet refused this batch" (10) and "we have no way to ask
# at all" (12) are different facts and call for different next steps. The
# first is worth re-running with a fresh session; the second is not worth
# re-running until a transport exists.
EXIT_ROUTE_TRANSPORT_DISABLED = 12
# [REASON]: "we saw route traffic but none of it was a confirmed route POST"
# is a finding, not a success and not an absence. It needs its own code so the
# operator can tell it from exit 6, which means nothing was observed at all.
EXIT_ROUTE_PROBE_UNCONFIRMED = 13

KIND_INCREMENTAL = 'incremental'
KIND_BACKFILL = 'backfill'
# DJI-AREA-48H: безопасный отчёт не написан, потому что он унёс бы приватное.
#
# [REASON]: отдельный код, а не общий отказ. Утечка -- единственный исход, при
# котором прогон обязан НИЧЕГО не записать, и владелец должен отличать её от
# «кабинет не ответил» с одного взгляда на код возврата.
EXIT_AREA_LEAK = 14

KINDS = ('backfill', 'incremental', 'replay')

MODE_FLIGHTS = 'flights'
MODE_LANDS = 'lands'
MODE_ROUTES = 'routes'
MODE_ROUTE_PROBE = 'route-ui-probe'
MODE_AREA_48H = 'area-48h'

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

ROUTE_PROBE_SUMMARY_KEYS = (
    'mode', 'region', 'probe_route_responses', 'probe_observations',
    'probe_confirmed', 'probe_skipped_over_cap', 'probe_errors',
    'probe_request_failures', 'probe_pending_requests',
    'probe_only_all_ids', 'probe_operator_answered', 'probe_drained',
    'probe_report', 'exit',
)

# [REASON]: у режима свой набор ключей, а не общий с вылетами. Без него
# `--area-48h` печатал бы сводку сбора вылетов, где ВСЕ значения `-`, и строка
# успешного исследования выглядела бы точно как строка прогона, не собравшего
# ничего. Признак, одинаковый в двух разных случаях, признаком не является.
AREA_SUMMARY_KEYS = (
    'mode', 'region', 'probe_route_responses', 'probe_observations',
    'probe_confirmed', 'probe_errors', 'probe_operator_answered',
    'probe_drained', 'area_live_confirmed', 'area_flights_captured',
    'area_flights_wrong_day', 'area_directory_pages',
    'area_directory_contours', 'area_contours_downloaded', 'area_works',
    'area_status', 'exit',
)

LAND_SUMMARY_KEYS = (
    'mode', 'dry_run', 'pages', 'total_count', 'lands_captured',
    'lands_deduped', 'self_duplicates', 'rejected_responses', 'complete',
    'batches', 'seen', 'new', 'updated', 'unchanged', 'errors',
    'geometry_selected', 'geometry_seen', 'geometry_downloaded',
    'geometry_queued', 'geometry_unchanged', 'geometry_failed',
    'geometry_not_found', 'geometry_bytes', 'exit',
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
                        help='with --lands: also download the contour '
                             'polygons into the on-disk outbox. The signed '
                             'link is used in memory and stored nowhere. '
                             'Reaches NO Vehicle Soft endpoint at all -- not '
                             'even the directory snapshot, which is what plain '
                             '--lands is for. Without --geometry-id it '
                             'downloads EVERY contour of the directory.')
    parser.add_argument('--routes', action='store_true',
                        help='collect the geometric route of each flight into '
                             'the on-disk outbox. Needs either --from/--to '
                             '(the flights of that period name the ids) or '
                             '--ids-file. Sends nothing to Vehicle Soft.')
    parser.add_argument('--ids-file', dest='ids_file', metavar='PATH',
                        help='with --routes: read the flight ids to request '
                             'from this file, one per line, # comments '
                             'allowed. Skips the flight-list walk entirely.')
    parser.add_argument('--route-ui-probe', dest='route_ui_probe',
                        action='store_true',
                        help='open a browser, ask the operator to drive Task '
                             'History into the map view by hand, and WATCH the '
                             'request the cabinet makes for itself. The probe '
                             'opens the cabinet but does not initiate the '
                             'route POST. Queues nothing, sends nothing to '
                             'Vehicle Soft. Records shapes and lengths, never '
                             'a header value.')
    parser.add_argument('--area-48h', dest='area_48h', action='store_true',
                        help='DJI-AREA-48H: watch the cabinet make its own '
                             'route request, keep the decoded routes '
                             'PRIVATELY on disk, match the field contours the '
                             'routes actually fall into, and write one '
                             'shareable report of areas and reasons. Creates '
                             'no table, applies no migration, sends nothing '
                             'to Vehicle Soft and changes no money.')
    parser.add_argument('--area-48h-no-contours', dest='area_48h_no_contours',
                        action='store_true',
                        help='with --area-48h: skip the directory walk and '
                             'measure without any field polygon. The useful '
                             'area then cannot be clipped and the report says '
                             'so.')
    parser.add_argument('--geometry-id', dest='geometry_ids', metavar='UUID',
                        action='append',
                        help='with --lands --with-geometry: download the '
                             'polygon of THIS contour only, matched against '
                             'the directory node uuid exactly. May be given '
                             'more than once. Without it every contour of the '
                             'directory is downloaded, which is the run a '
                             'first pilot must not make.')
    return parser


def needs_no_ingest(args):
    """True for the runs that reach no Vehicle Soft endpoint at all.

    [REASON]: `--lands --with-geometry` belongs here, and its absence was a
    live defect. The old expression asked only about --save-session, --dry-run
    and --routes, so a real geometry run counted as a SENDING run: it demanded
    VEHICLE_SOFT_BASE_URL and DRONE_API_TOKEN, and then `_run_lands` posted the
    whole directory to /drones/api/land_sync -- writing `field_contours` and a
    `drone_sync_logs` row on a production system that stage B is not allowed to
    touch. The rule now lives in one named place instead of inline in `_run()`,
    so a test can hold it.
    """
    return bool(args.save_session or args.dry_run or args.routes
                or args.route_ui_probe or args.area_48h
                or (args.lands and args.with_geometry))


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
    if args.route_ui_probe and (args.lands or args.routes or args.date_from
                                or args.date_to or args.kind
                                or args.ids_file):
        raise UsageError('--route-ui-probe watches one request made by hand; '
                         'it takes no period, no ids file and no other walk')
    if args.area_48h and (args.lands or args.routes or args.route_ui_probe
                          or args.date_from or args.date_to or args.kind
                          or args.ids_file or args.with_geometry):
        raise UsageError('--area-48h is a study of one day driven by hand; it '
                         'takes no period, no ids file and no other walk')
    if args.area_48h_no_contours and not args.area_48h:
        raise UsageError('--area-48h-no-contours only makes sense together '
                         'with --area-48h')
    if args.geometry_ids and not (args.lands and args.with_geometry):
        raise UsageError('--geometry-id names which polygon to download and '
                         'only makes sense together with --lands '
                         '--with-geometry')
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
                    MODE_ROUTES: ROUTE_SUMMARY_KEYS,
                    MODE_AREA_48H: AREA_SUMMARY_KEYS,
                    MODE_ROUTE_PROBE: ROUTE_PROBE_SUMMARY_KEYS}.get(
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

    # --save-session, --dry-run, --routes and --lands --with-geometry never
    # send anything, so they do not need VEHICLE_SOFT_BASE_URL or
    # DRONE_API_TOKEN. Every path that does send requires both, and fails
    # before the first request when one is missing. The rule itself lives in
    # needs_no_ingest(), where a test can hold it.
    sends = not needs_no_ingest(args)
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

    if args.area_48h:
        return _run_area_48h(args, cfg, log, state)

    if args.route_ui_probe:
        return _run_route_ui_probe(args, cfg, log, state)

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

    geometry_exit = EXIT_OK
    try:
        with LandCollector(cfg, log) as collector:
            result = collector.collect()
            # [REASON]: the polygons are downloaded INSIDE the `with`, while
            # the browser context is still alive. The signed links live only
            # in the directory response that is already in memory, and they
            # expire six hours after DJI issued them -- there is no later
            # moment at which this could be done from saved data.
            if args.with_geometry and result.lands:
                geometry_exit = _run_geometry(collector, result, args, cfg,
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

    if args.with_geometry:
        # [REASON]: a geometry run reaches NO Vehicle Soft endpoint -- not the
        # snapshot, not anything else -- and this branch is what makes that
        # true rather than merely documented. Until this fix a real
        # `--lands --with-geometry` fell through to send_lands() below and
        # posted the whole directory to /drones/api/land_sync, writing
        # `field_contours` and a `drone_sync_logs` row. It also skips
        # write_lands_dry_run(): that file dumps the directory nodes verbatim,
        # and the nodes carry `geometry.storage.signedURL` -- writing it here
        # would put the very links this module keeps out of files onto disk.
        # Sending the snapshot is what plain `--lands` is for, and it still
        # does it.
        log.info('--with-geometry is a collect-only run: the polygons go to '
                 'the outbox, the directory snapshot is NOT sent to Vehicle '
                 'Soft and no dry-run dump of it is written. Run plain '
                 '--lands for the snapshot.')
    elif args.dry_run:
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
    if geometry_exit != EXIT_OK:
        return geometry_exit
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

    # [REASON]: the run stops HERE, before the browser, the session and the
    # flight-list walk. The transport it would use was disproved on the live
    # cabinet on 2026-08-27: the page's own requests brought 168 flights while
    # every one of our 19 route batches came back "invalid request time".
    # Walking the flight list first and failing afterwards would cost the
    # operator four pages of pagination to learn what is already known.
    from drone_collector.routes import NATIVE_FETCH_DISABLED_REASON
    log.error('%s', NATIVE_FETCH_DISABLED_REASON)
    log.error('Route collection is BLOCKED pending a valid UI transport. '
              'Nothing was collected, nothing was queued, nothing was sent.')
    return EXIT_ROUTE_TRANSPORT_DISABLED


def _run_route_ui_probe(args, cfg, log, state):
    """--route-ui-probe: посмотреть на ШТАТНЫЙ запрос кабинета.

    [REASON]: собственный `fetch` опровергнут живым прогоном, а подпись
    воспроизводить запрещено. Остаётся единственный честный путь -- дать
    кабинету спросить самому и посмотреть на форму его вопроса.

    Доказанная гарантия: **probe открывает кабинет, но POST к эндпоинту
    маршрутов не инициирует**. Сказать «не делает своего запроса к DJI» было
    бы неправдой -- открытие кабинета это навигация. В очередь ничего не
    кладёт и в Vehicle Soft не ходит.
    """
    state['mode'] = MODE_ROUTE_PROBE

    try:
        require_session(cfg.storage_state)
    except SessionMissing as exc:
        log.error('%s', exc)
        return EXIT_SESSION

    try:
        from drone_collector.route_ui_probe import (MAX_OBSERVATIONS,
                                                    PROMPT_LINES, RouteUiProbe,
                                                    monotonic_ms,
                                                    probe_exit_code,
                                                    ProbeTimingError,
                                                    pump_until,
                                                    start_operator_prompt,
                                                    validate_probe_timings,
                                                    write_report)
    except ImportError as exc:  # pragma: no cover -- our own module
        log.error('The route probe could not be imported (%s)', exc)
        return EXIT_CONFIG

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

    # [REASON]: ДО браузера. Прогон, который всё равно не смог бы дождаться
    # ответа, не должен открывать кабинет и просить человека о работе. В
    # сообщении только имена настроек и числа.
    try:
        validate_probe_timings(poll_ms=cfg.route_probe_poll_ms,
                               wait_ms=cfg.route_probe_wait_ms,
                               drain_ms=cfg.route_probe_drain_ms,
                               quiet_ms=cfg.route_probe_quiet_ms)
    except ProbeTimingError as exc:
        log.error('The probe timings are contradictory: %s', exc)
        return EXIT_CONFIG

    probe = RouteUiProbe(logger=log, expected_origin=cfg.route_api_origin)
    errors = (BrowserError, PeriodVerificationFailed, RegionMismatch,
              SessionExpired, SessionMissing)
    try:
        with FlightCollector(cfg, log) as collector:
            page = collector.page
            # [REASON]: подписка ставится ДО того, как человек что-либо
            # сделает. Запрос, случившийся до подписки, увидеть уже нельзя, а
            # второго живого прогона может и не быть.
            # [REASON]: слушается ВЕСЬ жизненный цикл запроса, а не только
            # `response`. Playwright объявляет `response`, когда получены
            # статус и заголовки; тело догружается позже и объявляется
            # `requestfinished`. Без двух последних подписок запрос, ушедший
            # до Enter и не успевший получить ответ, был бы для drain
            # невидим, а оборвавшийся -- незамеченным вовсе.
            page.on('request', probe.note_request)
            page.on('response', probe.note_response)
            page.on('requestfinished', probe.note_request_finished)
            page.on('requestfailed', probe.note_request_failed)

            collector.open_records()
            state['region'] = collector.check_region(cfg.expected_region)

            for line in PROMPT_LINES:
                print(line)

            # [REASON]: КОРНЕВОЕ ИСПРАВЛЕНИЕ. Раньше здесь стоял голый
            # `input()`, и он держал ПОТОК Playwright: пока человек смотрел на
            # карту, ни один обработчик события не выполнялся. Они пошли в
            # работу только после Enter -- то есть уже на выходе из
            # `with FlightCollector`, когда target закрывался, -- и все пять
            # `response.body()` живого прогона 2026-08-27 получили
            # `TargetClosedError`. Теперь человека спрашивает отдельный
            # демонический поток, а поток Playwright крутит короткий цикл и
            # отдаёт управление библиотеке на каждом обороте.
            prompt = start_operator_prompt(
                'Press Enter once the routes are drawn on the map: ')
            pump = page.wait_for_timeout
            waited = pump_until(
                pump, prompt.done.is_set, monotonic_ms,
                cfg.route_probe_wait_ms, cfg.route_probe_poll_ms)
            # [REASON]: ТРИ состояния, а не два. Отказ ввода -- не ответ
            # оператора: прежняя редакция ставила событие в `finally` и
            # объявляла человека ответившим, когда тот ничего не нажимал.
            operator_answered = bool(waited and prompt.answered)
            if waited and prompt.failed:
                log.error('The operator prompt could not be read (%s); the '
                          'run is not confirmed. Nothing of the exception '
                          'text is printed.', prompt.error_type)
            elif not waited:
                log.error('The operator did not answer within %d ms; the '
                          'probe stops waiting and drains what it already '
                          'saw.', cfg.route_probe_wait_ms)

            # Сигнал получен -- новых действий оператора больше не ждём, но
            # уже начатым запросам даём ограниченное время дойти до тела, и
            # только потом отпускаем браузер.
            #
            # [REASON]: `begin_drain` и `min_pumps=1` -- вместе. Без первого
            # «тишина» наступала мгновенно, если ответов ещё никто не видел;
            # без второго нулевой `quiet_ms` закрывал бы браузер, не прокачав
            # события ни разу. Событие, поставленное в очередь одновременно с
            # Enter, при этом терялось.
            probe.begin_drain(monotonic_ms())
            drain_completed = pump_until(
                pump,
                lambda: probe.is_quiet(monotonic_ms(),
                                       cfg.route_probe_quiet_ms),
                monotonic_ms, cfg.route_probe_drain_ms,
                cfg.route_probe_poll_ms, min_pumps=1)
            if not drain_completed:
                log.error('Route traffic had not settled %d ms after the '
                          'operator signalled (%d request(s) still pending); '
                          'the browser is closed now and the run is NOT '
                          'confirmed.', cfg.route_probe_drain_ms,
                          probe.pending_route_requests)
            print('Drained pending route responses: %s'
                  % ('yes' if drain_completed else 'TIMED OUT'))
    except errors as exc:
        return _exit_code_for(exc, log)

    confirmed = probe.confirmed_observations
    state['probe_route_responses'] = probe.route_responses
    state['probe_observations'] = len(probe.observations)
    state['probe_confirmed'] = len(confirmed)
    state['probe_skipped_over_cap'] = probe.skipped_over_cap
    # [REASON]: в сводке прогона, а не только в отчёте. Ответ, который
    # слушатель не смог прочитать, меняет код выхода, и строка сводки обязана
    # показывать, почему прогон не зелёный.
    state['probe_errors'] = probe.observation_errors
    state['probe_request_failures'] = probe.route_requests_failed
    state['probe_pending_requests'] = probe.pending_route_requests
    state['probe_only_all_ids'] = probe.saw_only_all_ids
    state['probe_operator_answered'] = operator_answered
    state['probe_drained'] = drain_completed

    try:
        target = write_report(probe, cfg.out_dir,
                              operator_answered=operator_answered,
                              drain_completed=drain_completed)
    except ValueError as exc:
        log.error('%s', exc)
        return EXIT_PAGINATION
    state['probe_report'] = str(target)

    print('The report carries shapes and lengths only -- no header value, no '
          'cookie, no signature, no request id and no response body.')

    # [REASON]: the decision is one pure function, so it can be read and
    # tested on its own. Exit 0 needs all three at once: something was seen,
    # EVERY observation is confirmed, and none was dropped by the cap. A mixed
    # result used to exit 0 on the strength of one confirmed POST beside an
    # unconfirmed answer -- exactly the class of false success this whole
    # review is about. A dropped observation is not "confirmed" either: about
    # it nothing at all is known. A response the listener could not read at
    # all is not "nothing observed" either: something arrived and we failed to
    # look at it, and calling that a clean run would be a lie.
    code = probe_exit_code(observations=len(probe.observations),
                           confirmed=len(confirmed),
                           skipped_over_cap=probe.skipped_over_cap,
                           observation_errors=probe.observation_errors,
                           drain_timed_out=not drain_completed,
                           operator_answered=operator_answered)

    if code == EXIT_EMPTY:
        log.error('No route request was observed. The cabinet may not have '
                  'been driven into the map view, or the day chosen has no '
                  'flights. Nothing was collected and nothing was sent.')
        print('No route request was observed; see %s' % target)
        return code

    if code != EXIT_OK:
        reasons = sorted({reason for item in probe.observations
                          for reason in item.not_confirmed_because})
        if probe.skipped_over_cap:
            reasons.append('%d observation(s) were dropped by the cap of %d'
                           % (probe.skipped_over_cap, MAX_OBSERVATIONS))
        if probe.observation_errors:
            reasons.append('%d response(s) could not be read by the listener'
                           % probe.observation_errors)
        if not drain_completed:
            reasons.append('route responses were still arriving when the '
                           'drain window of %d ms ran out'
                           % cfg.route_probe_drain_ms)
        if not operator_answered:
            reasons.append('the operator never confirmed the map view')
        if probe.route_requests_failed:
            reasons.append('%d route request(s) failed before their body '
                           'arrived' % probe.route_requests_failed)
        if probe.pending_route_requests:
            reasons.append('%d route request(s) were still unfinished when '
                           'the browser closed' % probe.pending_route_requests)
        log.error('%d route response(s) observed, %d confirmed, %d dropped by '
                  'the cap, %d unreadable -- NOT a confirmed run: %s',
                  len(probe.observations), len(confirmed),
                  probe.skipped_over_cap, probe.observation_errors,
                  '; '.join(reasons) or 'no reason recorded')
        print('%d route response(s) observed, %d confirmed. Report: %s'
              % (len(probe.observations), len(confirmed), target))
        return code

    log.info('Observed %d route response(s), %d distinct, all %d confirmed; '
             'report: %s', probe.route_responses, len(probe.observations),
             len(confirmed), target)
    print('%d route response(s) observed, %d distinct, all %d CONFIRMED. '
          'Report: %s' % (probe.route_responses, len(probe.observations),
                          len(confirmed), target))
    return code


def _run_area_48h(args, cfg, log, state):
    """--area-48h: DJI-AREA-48H, один живой прогон и один разбор.

    Три шага, и порядок между ними важен:

    1. наблюдение за ШТАТНЫМ запросом маршрутов -- ровно тот же жизненный цикл,
       что у `--route-ui-probe`, потому что он уже оплачен двумя живыми
       прогонами и повторять его второй реализацией нельзя;
    2. приватный снимок на диск СРАЗУ, до всего остального. Живой прогон
       делается один раз, и разбор, упавший на контурах, не имеет права унести
       с собой маршруты, ради которых владелец сидел у браузера;
    3. контуры и разбор.

    Прогон не создаёт таблиц, не применяет миграций, не открывает эндпоинтов,
    не обращается к Vehicle Soft и не меняет ни одного начисления.
    """
    state['mode'] = MODE_AREA_48H

    try:
        require_session(cfg.storage_state)
    except SessionMissing as exc:
        log.error('%s', exc)
        return EXIT_SESSION

    try:
        from drone_collector.area_study import (AreaCapture, PROMPT_LINES,
                                                STUDY_DAY, ShareableLeak,
                                                archive_existing,
                                                live_run_verdict, run_study,
                                                split_by_day, study_exit_code,
                                                write_capture, write_reports)
        from drone_collector.route_ui_probe import (monotonic_ms,
                                                    ProbeTimingError,
                                                    pump_until,
                                                    start_operator_prompt,
                                                    validate_probe_timings)
    except ImportError as exc:  # pragma: no cover -- our own modules
        log.error('The area study could not be imported (%s)', exc)
        return EXIT_CONFIG

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

    try:
        validate_probe_timings(poll_ms=cfg.route_probe_poll_ms,
                               wait_ms=cfg.route_probe_wait_ms,
                               drain_ms=cfg.route_probe_drain_ms,
                               quiet_ms=cfg.route_probe_quiet_ms)
    except ProbeTimingError as exc:
        log.error('The probe timings are contradictory: %s', exc)
        return EXIT_CONFIG

    capture_probe = AreaCapture(logger=log,
                                expected_origin=cfg.route_api_origin)
    errors = (BrowserError, PeriodVerificationFailed, RegionMismatch,
              SessionExpired, SessionMissing)
    operator_answered = drain_completed = False
    try:
        with FlightCollector(cfg, log) as collector:
            page = collector.page
            page.on('request', capture_probe.note_request)
            page.on('response', capture_probe.note_response)
            page.on('requestfinished', capture_probe.note_request_finished)
            page.on('requestfailed', capture_probe.note_request_failed)

            collector.open_records()
            state['region'] = collector.check_region(cfg.expected_region)

            for line in PROMPT_LINES:
                print(line)

            prompt = start_operator_prompt(
                'Press Enter once the routes are drawn on the map: ')
            pump = page.wait_for_timeout
            waited = pump_until(pump, prompt.done.is_set, monotonic_ms,
                                cfg.route_probe_wait_ms,
                                cfg.route_probe_poll_ms)
            operator_answered = bool(waited and prompt.answered)
            if waited and prompt.failed:
                log.error('The operator prompt could not be read (%s); the '
                          'run is not confirmed.', prompt.error_type)
            elif not waited:
                log.error('The operator did not answer within %d ms; the run '
                          'drains what it already saw.',
                          cfg.route_probe_wait_ms)

            capture_probe.begin_drain(monotonic_ms())
            drain_completed = pump_until(
                pump,
                lambda: capture_probe.is_quiet(monotonic_ms(),
                                               cfg.route_probe_quiet_ms),
                monotonic_ms, cfg.route_probe_drain_ms,
                cfg.route_probe_poll_ms, min_pumps=1)
            if not drain_completed:
                log.error('Route traffic had not settled %d ms after the '
                          'operator signalled (%d request(s) still pending).',
                          cfg.route_probe_drain_ms,
                          capture_probe.pending_route_requests)
            print('Drained pending route responses: %s'
                  % ('yes' if drain_completed else 'TIMED OUT'))
    except errors as exc:
        return _exit_code_for(exc, log)

    captured = capture_probe.captured_flights()
    flights, rejected_wrong_day = split_by_day(captured, STUDY_DAY)
    observations = capture_probe.observations
    id_sets_matched = bool(observations) and all(
        (item.comparison or {}).get('requested_and_returned_match') is True
        for item in observations)
    verdict = live_run_verdict(
        operator_answered=operator_answered,
        drain_completed=drain_completed,
        observations=len(observations),
        confirmed=len(capture_probe.confirmed_observations),
        skipped_over_cap=capture_probe.skipped_over_cap,
        observation_errors=capture_probe.observation_errors,
        capture_errors=capture_probe.capture_errors,
        pending_route_requests=capture_probe.pending_route_requests,
        route_requests_failed=capture_probe.route_requests_failed,
        id_sets_matched=id_sets_matched,
        flights_of_study_day=len(flights),
        flights_rejected_wrong_day=rejected_wrong_day,
        study_day=STUDY_DAY)

    state['probe_route_responses'] = capture_probe.route_responses
    state['probe_observations'] = len(observations)
    state['probe_confirmed'] = len(capture_probe.confirmed_observations)
    state['probe_errors'] = capture_probe.observation_errors
    state['probe_operator_answered'] = operator_answered
    state['probe_drained'] = drain_completed
    state['area_flights_captured'] = len(flights)
    state['area_flights_wrong_day'] = rejected_wrong_day
    state['area_live_confirmed'] = verdict['confirmed']

    code = study_exit_code(bool(captured), verdict)
    if code == EXIT_EMPTY:
        log.error('No route was captured. The cabinet may not have been '
                  'driven into the map view of %s, or every body refused to '
                  'decode. Nothing was collected and nothing was sent.',
                  STUDY_DAY)
        print('No route was captured; nothing was written.')
        return code

    capture = {
        'study_day_requested': STUDY_DAY,
        'day': STUDY_DAY,
        'decoder_version': _decoder_version(),
        'flights': flights,
        'flights_rejected_wrong_day': rejected_wrong_day,
        'live_run': verdict,
        'contours': [],
    }
    # [REASON]: снимок на диск ДО контуров. Ошибка на справочнике не имеет
    # права стоить владельцу второго похода в кабинет.
    target = write_capture(cfg.out_dir, capture)
    log.info('Private capture written: %s (%d flight(s) of %s, %d of another '
             'day left out)', target, len(flights), STUDY_DAY,
             rejected_wrong_day)
    print('Private capture (never share): %s' % target)

    if code != EXIT_OK:
        # [REASON]: безопасный отчёт НЕ пишется. Неподтверждённый прогон,
        # оформленный отчётом, читается как результат -- а он им не является.
        # Приватный снимок при этом сохранён: если владелец решит, что данных
        # достаточно, их пересчитает `--replay`, и оговорка о неподтверждённом
        # прогоне поедет в отчёт вместе с числами. Второй поход в кабинет не
        # нужен.
        for reason in verdict['reasons']:
            log.error('The live run is NOT confirmed: %s', reason)
        print('LIVE RUN NOT CONFIRMED: %s' % '; '.join(verdict['reasons']))
        print('No shareable report was written. The private capture above is '
              'intact; recompute from it with: python tools/dji_area_48h.py '
              '--replay %s' % target)
        return code

    # [REASON]: список заводится ЗДЕСЬ, до первого `append` и до `run_study`.
    # Его отсутствие обошлось владельцу целым живым прогоном: 168 маршрутов и
    # 116 контуров были собраны успешно, а отчёт упал на `NameError`, потому
    # что оговорки некуда было складывать. Приватный снимок к тому моменту уже
    # лежал на диске -- ровно затем он и пишется до контуров, -- поэтому
    # второй поход в кабинет не понадобился.
    notes = []

    if args.area_48h_no_contours:
        notes.append('the directory walk was skipped by --area-48h-no-'
                     'contours, so no area is clipped to a field polygon')
    else:
        try:
            _attach_contours(capture, cfg, log, state)
        except Exception as exc:
            # Тип исключения, не текст: сообщение приходит из чужой библиотеки.
            notes.append('the field contours could not be collected (%s); the '
                         'areas are reported unclipped'
                         % type(exc).__name__)
            log.warning('The contour phase failed (%s); the study continues '
                        'without polygons.', type(exc).__name__)
        write_capture(cfg.out_dir, capture)

    private, shareable = run_study(capture, notes=notes)
    # [REASON]: прошлый отчёт отодвигается, а не перезаписывается, и делается
    # это ПЕРЕД самой записью, а не в начале прогона. Отчёт прошлого раза --
    # свидетельство: сравнить «было / стало» больше будет нечем. Но и остаться
    # с одними `.bak` после упавшего прогона владелец не должен.
    for moved in archive_existing(cfg.out_dir):
        log.info('Previous shareable report archived: %s', moved)
    try:
        written = write_reports(cfg.out_dir, capture, private, shareable)
    except ShareableLeak as exc:
        log.error('The shareable report would have carried something private '
                  '(%s); NOTHING was written. The private capture stays on '
                  'disk, so nobody has to go back to the cabinet: fix the '
                  'cause and recompute with tools/dji_area_48h.py --replay '
                  '%s', exc, target)
        print('LEAK: the shareable report was not written. Recompute from the '
              'private capture with: python tools/dji_area_48h.py --replay %s'
              % target)
        return EXIT_AREA_LEAK

    state['area_status'] = shareable['final_status']
    state['area_works'] = shareable['works_total']
    log.info('DJI-AREA-48H: %d flight(s), %d work(s), status %s',
             shareable['flights_total'], shareable['works_total'],
             shareable['final_status'])
    print('Flights: %d   Works: %d   Status: %s'
          % (shareable['flights_total'], shareable['works_total'],
             shareable['final_status']))
    print('Shareable JSON: %s' % written['json'])
    print('Shareable MD:   %s' % written['md'])
    print('Send the OWNER only the two shareable files above. The private '
          'directory never leaves this machine.')
    return EXIT_OK


def _decoder_version():
    try:
        from drone_collector.route_decode import DECODER_VERSION
        return DECODER_VERSION
    except ImportError:  # pragma: no cover -- our own module
        return None


def _attach_contours(capture, cfg, log, state):
    """Дописать в снимок ТОЛЬКО те контуры, в которые попали маршруты.

    Справочник обходится списком (это метаданные, которые кабинет отдаёт сам),
    а полигон качается лишь у кандидатов -- у тех, чья рамка накрывает точки
    маршрута. Пять с половиной тысяч подписанных ссылок не берутся: отбор
    делает `candidate_contours` по СЫРЫМ узлам, до построения `ContourSource`,
    поэтому ссылка невыбранного контура не оказывается даже в памяти.
    """
    import hashlib
    import json as _json

    from drone_collector.area_study import candidate_contours
    from drone_collector.geometry import (ContextGeometryDownloader,
                                          GeometryError, contour_from_node,
                                          scrub)
    from drone_collector.lands import LandCollector

    wanted = {}
    with LandCollector(cfg, log) as collector:
        result = collector.collect()
        state['area_directory_pages'] = result.pages_captured
        state['area_directory_contours'] = len(result.lands)
        log.info('Directory walk for the study: %d contour(s), complete=%s',
                 len(result.lands), result.complete)
        by_uuid = {node.get('uuid'): node for node in result.lands
                   if isinstance(node, dict)}
        # [REASON]: качаются ВСЕ кандидаты короткого списка, а не первый.
        # Рамки соседних полей пересекаются постоянно, и «первый по доле точек
        # в рамке» -- это запросто соседнее или объемлющее поле. Какой полигон
        # настоящий, решает `choose_contour` уже по геометрии; чтобы ему было
        # из чего выбирать, полигоны должны быть на руках. Список ограничен
        # восемью на вылет, весь справочник по-прежнему не качается.
        for flight in capture['flights']:
            chosen = candidate_contours(result.lands, flight.get('points')
                                        or [])
            flight['contour_candidates'] = [uuid for uuid, _share in chosen]
            flight['contour_bbox_shares'] = chosen
            for uuid, _share in chosen:
                wanted.setdefault(uuid, by_uuid.get(uuid))

        download = ContextGeometryDownloader(collector.context, log)
        for uuid in sorted(key for key in wanted if key):
            node = wanted[uuid]
            source = contour_from_node(node) if node else None
            if source is None or not source.has_link or not source.content_md5:
                log.warning('Contour %s carries no geometry in the directory',
                            uuid)
                continue
            link = source.take_link()
            try:
                blob = download(link)
            except GeometryError as exc:
                log.warning('Contour %s did not download: %s', uuid,
                            scrub(exc))
                continue
            finally:
                link = None
            if hashlib.md5(blob).hexdigest().lower() != \
                    str(source.content_md5).lower():
                # [REASON]: расхождение md5 -- это чужой или обрезанный объект,
                # а полигон чужого поля даст уверенную неверную площадь.
                log.warning('Contour %s: DJI named a different content md5; '
                            'it is not used.', uuid)
                continue
            try:
                document = _json.loads(blob.decode('utf-8'))
            except (UnicodeDecodeError, ValueError) as exc:
                log.warning('Contour %s did not parse (%s)', uuid,
                            type(exc).__name__)
                continue
            capture['contours'].append({
                'uuid': uuid,
                'field_serial': source.field_serial,
                'name': source.name,
                'total_area_mu': source.total_area_mu,
                'geojson': document,
            })
    state['area_contours_downloaded'] = len(capture['contours'])
    log.info('Contours attached to the study: %d', len(capture['contours']))


def _run_routes_engine(args, cfg, log, state):
    """Прежний прогон сбора. Не вызывается, пока транспорт не доказан.

    [REASON]: не удалён вместе с транспортом. `RouteRun` исправен -- это
    показывают 95 тестов, -- и недостаёт ему ровно одного: способа задать
    вопрос кабинету так, как его задаёт сам кабинет. Выбросить работающий
    разбор вместе с опровергнутым транспортом значило бы писать его заново,
    когда способ найдётся.
    """
    try:
        require_session(cfg.storage_state)
    except SessionMissing as exc:
        log.error('%s', exc)
        return EXIT_SESSION

    try:
        from drone_collector.routes import (RouteRun, RouteRequestRefused,
                                            RouteRunError,
                                            disabled_route_transport,
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
                disabled_route_transport(),
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
        if run is not None and run.last_result is not None:
            # The counters of the aborted run are the answer to "how many ids
            # were never asked for", and they must not die with the exception.
            _account_for_routes(run.last_result, outbox, state)
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
    """--lands --with-geometry: download the full polygons.

    Returns an exit code: EXIT_OK on a clean pass, EXIT_GEOMETRY_NOT_FOUND
    when --geometry-id named a contour the directory does not hold, and
    EXIT_PAGINATION when at least one selected contour failed.
    """
    from drone_collector.geometry import (ContextGeometryDownloader,
                                          GeometryRun, STATUS_OK,
                                          STATUS_UNCHANGED)
    from drone_collector.geometry import write_dry_run as write_geometry_dry_run

    outbox = _open_outbox(cfg, log)
    run = GeometryRun(outbox, ContextGeometryDownloader(collector.context, log),
                      logger=log, pause_s=cfg.geometry_pause_ms / 1000.0,
                      dry_run=bool(args.dry_run))
    geometry = run.collect(result.lands, only_uuids=args.geometry_ids)

    failed = sum(count for status, count in geometry.by_status.items()
                 if status not in (STATUS_OK, STATUS_UNCHANGED))
    state['geometry_seen'] = geometry.seen
    state['geometry_downloaded'] = geometry.downloaded
    state['geometry_queued'] = geometry.queued
    state['geometry_unchanged'] = geometry.by_status.get(STATUS_UNCHANGED, 0)
    state['geometry_failed'] = failed
    state['geometry_bytes'] = geometry.bytes
    state['geometry_selected'] = (len(geometry.requested_uuids)
                                  if geometry.requested_uuids is not None
                                  else None)
    state['geometry_not_found'] = len(geometry.missing_uuids)

    if args.dry_run:
        target = write_geometry_dry_run(geometry, run.prepared_bodies,
                                        cfg.out_dir)
        log.info('Dry run: nothing was queued; %d contour(s) written to %s',
                 len(run.prepared_bodies), target)

    if geometry.missing_uuids:
        # [REASON]: a named contour that is not in the directory is an input
        # error with its own outcome, not a quiet zero. The uuids are printed
        # because a uuid is an identifier, not a credential -- unlike the
        # signed link of the same node, which is never printed anywhere.
        log.error('--geometry-id named %d contour(s) the directory does not '
                  'hold: %s', len(geometry.missing_uuids),
                  ', '.join(geometry.missing_uuids))
        if not result.complete:
            log.error('The directory walk was INCOMPLETE, so a named contour '
                      'may simply be on a page that was never fetched. Re-run '
                      'and let the walk finish before treating this as proof '
                      'that the contour is gone.')
        return EXIT_GEOMETRY_NOT_FOUND

    if failed:
        # [REASON]: a contour whose polygon did not arrive is not a silent
        # gap. It is named by status, and the run says so on exit -- a
        # half-collected set of polygons that reported success is exactly how
        # a missing field becomes invisible afterwards.
        log.error('%d contour(s) did not yield a usable polygon: %s',
                  failed, {status: count
                           for status, count in geometry.by_status.items()
                           if status not in (STATUS_OK, STATUS_UNCHANGED)})
        log.error('Re-run the same command: what already arrived is in the '
                  'queue and is not fetched again.')
        return EXIT_PAGINATION
    return EXIT_OK


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
