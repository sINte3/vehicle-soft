# -*- coding: utf-8 -*-
"""drone_collector/sender.py -- POST the collected flights to Vehicle Soft.

Contract, read out of drones.py:

    POST {VEHICLE_SOFT_BASE_URL}/drones/api/flight_sync
    {"token": "...", "kind": "incremental",
     "period_from": "2026-07-01", "period_to": "2026-07-31",
     "flights": [ ...raw flight objects, verbatim... ]}

  * the token travels in the BODY, not in a header -- the same convention as
    the Topaz fuel sync;
  * kind is one of backfill / incremental / replay;
  * at most 1000 flights per request, above which the endpoint answers 413;
  * the answer carries five counters: seen, new, duplicates, unresolved,
    errors. All five are logged. `unresolved` is a SUBSET of `new`, not a
    separate bucket -- adding them together double-counts flights.

Units are not converted here. new_work_area is m2, spray_usage is millilitres
and sow_usage is grams in the payload, and drones.py performs those
conversions itself. The flight objects go through verbatim; a second
conversion on this side would divide by 10 000 twice.
"""

import json
import logging
import time

from pathlib import Path

from drone_collector.config import MAX_BATCH_SIZE, MAX_ROUTE_BATCH_SIZE
from drone_collector.window import format_date

log = logging.getLogger(__name__)

KINDS = ('backfill', 'incremental', 'replay')

# Three attempts total: the first, then waits of 2 s and 4 s.
RETRY_ATTEMPTS = 3
RETRY_BACKOFF_SECONDS = (2, 4)

# [REASON]: 400, 401 and 413 are contract errors -- a malformed body, a wrong
# token, a batch above the cap. Retrying them changes nothing and buries the
# cause under three identical failures; the run fails immediately and says
# which one it was.
NON_RETRYABLE_STATUSES = (400, 401, 413)

# A 1000-flight batch is a single SQLite write transaction on the server.
REQUEST_TIMEOUT_S = 120


class TransportError(Exception):
    """Network-level failure (connection refused, timeout). Retryable."""


class IngestRejected(Exception):
    """The endpoint refused the batch. main() -> exit code 5."""


class SendResult(object):
    """The five counters summed over every batch of one run, plus the log ids."""

    __slots__ = ('batches', 'seen', 'new', 'duplicates', 'unresolved',
                 'errors', 'log_ids')

    def __init__(self):
        self.batches = 0
        self.seen = 0
        self.new = 0
        self.duplicates = 0
        self.unresolved = 0
        self.errors = 0
        self.log_ids = []

    def add(self, body):
        self.batches += 1
        self.seen += _int(body.get('seen'))
        self.new += _int(body.get('new'))
        self.duplicates += _int(body.get('duplicates'))
        self.unresolved += _int(body.get('unresolved'))
        self.errors += _int(body.get('errors'))
        log_id = body.get('log_id')
        if log_id is not None:
            self.log_ids.append(log_id)
        return self

    def as_dict(self):
        return {'batches': self.batches, 'seen': self.seen, 'new': self.new,
                'duplicates': self.duplicates, 'unresolved': self.unresolved,
                'errors': self.errors}

    def __repr__(self):
        return 'SendResult(%s)' % self.as_dict()


def _int(value):
    if value is None or isinstance(value, bool):
        return 0
    try:
        return int(value)
    except (TypeError, ValueError):
        return 0


def chunk(flights, batch_size):
    """Split into batches, none of which exceeds the endpoint's cap of 1000.

    batch_size is clamped here as well as in config.load_config: this function
    is called directly by the tests and must not be able to produce an
    oversized batch whatever it is handed.
    """
    size = min(int(batch_size), MAX_BATCH_SIZE)
    if size < 1:
        size = 1
    return [flights[i:i + size] for i in range(0, len(flights), size)]


def build_payload(token, kind, period_from, period_to, flights):
    """The request body. Never log the result -- it carries the token."""
    if kind not in KINDS:
        raise ValueError('kind must be one of %s, got %r'
                         % (', '.join(KINDS), kind))
    return {
        'token': token,
        'kind': kind,
        'period_from': format_date(period_from),
        'period_to': format_date(period_to),
        'flights': flights,
    }


def _requests_post(url, payload, timeout_s):
    """Default transport. Returns (status_code, parsed_body_or_None)."""
    # Lazy import: the tests and CI run in the application's Python, where the
    # collector's dependencies are not installed.
    import requests

    try:
        response = requests.post(url, json=payload, timeout=timeout_s)
    except requests.exceptions.RequestException as exc:
        raise TransportError(str(exc))
    try:
        body = response.json()
    except ValueError:
        body = None
    return response.status_code, body


def send(flights, kind, period_from, period_to, cfg, logger=None,
         post_fn=None, sleep_fn=None):
    """Chunk and POST. Returns a SendResult; raises IngestRejected on refusal.

    post_fn and sleep_fn are injectable so the retry policy can be tested
    without a network and without waiting.
    """
    out = logger or log
    post = post_fn or _requests_post
    sleep = sleep_fn or time.sleep

    result = SendResult()
    if not flights:
        # [REASON]: an empty batch would still create a drone_sync_logs row.
        # On a schedule that runs several times a day those rows accumulate
        # into pages of "seen 0" noise and make the real question -- did the
        # historical collection go through -- a manual summing exercise.
        out.info('Nothing to send: 0 flights for %s .. %s',
                 format_date(period_from), format_date(period_to))
        return result

    batches = chunk(flights, cfg.batch_size)
    out.info('Sending %d flight(s) in %d batch(es) of at most %d to %s',
             len(flights), len(batches), min(cfg.batch_size, MAX_BATCH_SIZE),
             cfg.flight_sync_url)

    for index, batch in enumerate(batches, start=1):
        payload = build_payload(cfg.api_token, kind, period_from, period_to,
                                batch)
        body = _post_with_retries(post, sleep, cfg.flight_sync_url, payload,
                                  index, len(batches), out)
        result.add(body)
        out.info('Batch %d/%d accepted: log_id=%s seen=%s new=%s duplicates=%s'
                 ' unresolved=%s errors=%s', index, len(batches),
                 body.get('log_id'), body.get('seen'), body.get('new'),
                 body.get('duplicates'), body.get('unresolved'),
                 body.get('errors'))

    out.info('Ingest totals: batches=%d seen=%d new=%d duplicates=%d '
             'unresolved=%d errors=%d (unresolved is a subset of new)',
             result.batches, result.seen, result.new, result.duplicates,
             result.unresolved, result.errors)
    return result


def _post_with_retries(post, sleep, url, payload, index, total, out):
    """One batch, up to RETRY_ATTEMPTS times. Returns the parsed body."""
    last_error = None
    for attempt in range(1, RETRY_ATTEMPTS + 1):
        try:
            # The payload is never logged: it carries the token.
            status, body = post(url, payload, REQUEST_TIMEOUT_S)
        except TransportError as exc:
            last_error = 'connection failed: %s' % exc
            out.warning('Batch %d/%d attempt %d/%d: %s', index, total, attempt,
                        RETRY_ATTEMPTS, last_error)
        else:
            if status in NON_RETRYABLE_STATUSES:
                raise IngestRejected(
                    'batch %d/%d refused with HTTP %d: %s -- not retried, this'
                    ' is a contract error' % (index, total, status,
                                              _error_text(body)))
            if 400 <= status < 500:
                raise IngestRejected(
                    'batch %d/%d refused with HTTP %d: %s' % (index, total,
                                                              status,
                                                              _error_text(body)))
            if status >= 500:
                last_error = 'HTTP %d: %s' % (status, _error_text(body))
                out.warning('Batch %d/%d attempt %d/%d: %s', index, total,
                            attempt, RETRY_ATTEMPTS, last_error)
            elif not isinstance(body, dict):
                last_error = 'HTTP %d with an unparsable body' % status
                out.warning('Batch %d/%d attempt %d/%d: %s', index, total,
                            attempt, RETRY_ATTEMPTS, last_error)
            else:
                return body

        if attempt < RETRY_ATTEMPTS:
            delay = RETRY_BACKOFF_SECONDS[min(attempt - 1,
                                              len(RETRY_BACKOFF_SECONDS) - 1)]
            out.info('Retrying batch %d/%d in %d s', index, total, delay)
            sleep(delay)

    raise IngestRejected('batch %d/%d failed after %d attempts (%s)'
                         % (index, total, RETRY_ATTEMPTS, last_error))


def _error_text(body):
    if isinstance(body, dict):
        return str(body.get('error') or body.get('message') or body)
    return 'no JSON body'


def dry_run_path(out_dir, period_from, period_to):
    return Path(out_dir) / ('flights_%s_%s.json' % (format_date(period_from),
                                                    format_date(period_to)))


def write_dry_run(flights, kind, period_from, period_to, out_dir):
    """Write what would have been sent, minus the token, and return the path.

    The file is the ingest body without the `token` key: it can be inspected,
    diffed and -- with a token added -- replayed by hand. The token is left
    out on purpose; a dry-run dump is the kind of file that ends up attached
    to a ticket.
    """
    target = dry_run_path(out_dir, period_from, period_to)
    target.parent.mkdir(parents=True, exist_ok=True)
    document = {
        'kind': kind,
        'period_from': format_date(period_from),
        'period_to': format_date(period_to),
        'count': len(flights),
        'flights': flights,
    }
    with target.open('w', encoding='utf-8') as handle:
        json.dump(document, handle, ensure_ascii=False, indent=2)
    return target


# ─── Field-contour snapshot (DRONE-LANDS-001) ────────────────────────────────
#
# Contract, read out of drones.py:
#
#     POST {VEHICLE_SOFT_BASE_URL}/drones/api/land_sync
#     {"token": "...", "lands": [ ...raw GraphQL `node` objects, verbatim... ]}
#
#   * the token travels in the BODY, like the flight sync;
#   * there is no `kind` and no period: a directory snapshot has neither;
#   * at most 1000 lands per request, above which the endpoint answers 413;
#   * the answer carries four counters: seen, new, updated, unchanged, plus
#     errors. They partition what was sent -- seen = new + updated +
#     unchanged + errors.
#
# Areas are NOT converted here. They arrive in MU and drones.py divides by 15;
# a second conversion on this side would divide twice.


class LandSendResult(object):
    """The counters summed over every batch of one snapshot."""

    __slots__ = ('batches', 'seen', 'new', 'updated', 'unchanged', 'errors')

    def __init__(self):
        self.batches = 0
        self.seen = 0
        self.new = 0
        self.updated = 0
        self.unchanged = 0
        self.errors = 0

    def add(self, body):
        self.batches += 1
        self.seen += _int(body.get('seen'))
        self.new += _int(body.get('new'))
        self.updated += _int(body.get('updated'))
        self.unchanged += _int(body.get('unchanged'))
        self.errors += _int(body.get('errors'))
        return self

    def as_dict(self):
        return {'batches': self.batches, 'seen': self.seen, 'new': self.new,
                'updated': self.updated, 'unchanged': self.unchanged,
                'errors': self.errors}

    def __repr__(self):
        return 'LandSendResult(%s)' % self.as_dict()


def build_land_payload(token, lands):
    """The request body. Never log the result -- it carries the token."""
    return {'token': token, 'lands': lands}


def send_lands(lands, cfg, logger=None, post_fn=None, sleep_fn=None):
    """Chunk and POST the directory. Returns a LandSendResult."""
    out = logger or log
    post = post_fn or _requests_post
    sleep = sleep_fn or time.sleep

    result = LandSendResult()
    if not lands:
        out.info('Nothing to send: 0 contours')
        return result

    batches = chunk(lands, cfg.batch_size)
    out.info('Sending %d contour(s) in %d batch(es) of at most %d to %s',
             len(lands), len(batches), min(cfg.batch_size, MAX_BATCH_SIZE),
             cfg.land_sync_url)

    for index, batch in enumerate(batches, start=1):
        payload = build_land_payload(cfg.api_token, batch)
        body = _post_with_retries(post, sleep, cfg.land_sync_url, payload,
                                  index, len(batches), out)
        result.add(body)
        out.info('Batch %d/%d accepted: seen=%s new=%s updated=%s '
                 'unchanged=%s errors=%s', index, len(batches),
                 body.get('seen'), body.get('new'), body.get('updated'),
                 body.get('unchanged'), body.get('errors'))

    out.info('Snapshot totals: batches=%d seen=%d new=%d updated=%d '
             'unchanged=%d errors=%d', result.batches, result.seen,
             result.new, result.updated, result.unchanged, result.errors)
    return result


# ─── DRONE-USEFUL-AREA-001: маршруты ────────────────────────────────────────
#
# Contract, read out of drones.py:
#
#     POST {VEHICLE_SOFT_BASE_URL}/drones/api/route_sync
#     {"token": "...", "routes": [ ...route_body objects... ]}
#
# Six counters come back, and every seen route lands in exactly one of them:
#
#     seen = new + updated + unchanged + errors + unlinked
#
# `unlinked` is NOT an error: it counts routes whose flight has not been
# synced yet. The flights must go first; the routes are then re-sent and land.


class RouteSendResult(object):
    """The six counters summed over every batch of one route run."""

    __slots__ = ('batches', 'seen', 'new', 'updated', 'unchanged', 'errors',
                 'unlinked')

    def __init__(self):
        self.batches = 0
        self.seen = 0
        self.new = 0
        self.updated = 0
        self.unchanged = 0
        self.errors = 0
        self.unlinked = 0

    def add(self, body):
        self.batches += 1
        self.seen += _int(body.get('seen'))
        self.new += _int(body.get('new'))
        self.updated += _int(body.get('updated'))
        self.unchanged += _int(body.get('unchanged'))
        self.errors += _int(body.get('errors'))
        self.unlinked += _int(body.get('unlinked'))
        return self

    @property
    def counters_agree(self):
        """seen == new + updated + unchanged + errors + unlinked.

        [REASON]: checked on OUR side too, not only asserted in the endpoint's
        docstring. A server that starts double-counting a bucket would
        otherwise be discovered by someone adding numbers off a screen.
        """
        return self.seen == (self.new + self.updated + self.unchanged
                             + self.errors + self.unlinked)

    def as_dict(self):
        return {'batches': self.batches, 'seen': self.seen, 'new': self.new,
                'updated': self.updated, 'unchanged': self.unchanged,
                'errors': self.errors, 'unlinked': self.unlinked}

    def __repr__(self):
        return 'RouteSendResult(%s)' % self.as_dict()


def build_route_payload(token, routes):
    """The request body. Never log the result -- it carries the token."""
    return {'token': token, 'routes': routes}


def chunk_routes(routes, batch_size=None):
    """Split into batches, none of which exceeds the endpoint's cap of 500."""
    size = min(int(batch_size or MAX_ROUTE_BATCH_SIZE), MAX_ROUTE_BATCH_SIZE)
    if size < 1:
        size = 1
    return [routes[i:i + size] for i in range(0, len(routes), size)]


def send_routes(routes, cfg, logger=None, post_fn=None, sleep_fn=None):
    """Chunk and POST the routes. Returns a RouteSendResult."""
    out = logger or log
    post = post_fn or _requests_post
    sleep = sleep_fn or time.sleep

    result = RouteSendResult()
    if not routes:
        out.info('Nothing to send: 0 routes')
        return result

    batches = chunk_routes(routes, getattr(cfg, 'route_batch_size', None))
    out.info('Sending %d route(s) in %d batch(es) of at most %d to %s',
             len(routes), len(batches), MAX_ROUTE_BATCH_SIZE,
             cfg.route_sync_url)

    for index, batch in enumerate(batches, start=1):
        payload = build_route_payload(cfg.api_token, batch)
        body = _post_with_retries(post, sleep, cfg.route_sync_url, payload,
                                  index, len(batches), out)
        result.add(body)
        out.info('Batch %d/%d accepted: seen=%s new=%s updated=%s '
                 'unchanged=%s errors=%s unlinked=%s', index, len(batches),
                 body.get('seen'), body.get('new'), body.get('updated'),
                 body.get('unchanged'), body.get('errors'),
                 body.get('unlinked'))

    out.info('Route totals: batches=%d seen=%d new=%d updated=%d unchanged=%d '
             'errors=%d unlinked=%d', result.batches, result.seen, result.new,
             result.updated, result.unchanged, result.errors, result.unlinked)
    if not result.counters_agree:
        out.error('The endpoint returned counters that do not add up: '
                  'seen=%d but new+updated+unchanged+errors+unlinked=%d',
                  result.seen, result.new + result.updated + result.unchanged
                  + result.errors + result.unlinked)
    return result


def route_dry_run_path(out_dir):
    return Path(out_dir) / 'routes_snapshot.json'


def write_routes_dry_run(routes, out_dir):
    """Write what would have been sent, minus the token, and return the path.

    [REASON]: the dry-run file carries route COORDINATES, so it lands in the
    run's own output directory beside the private capture -- never in the
    repository, never in a log. Same rule as the area study's private snapshot.
    """
    target = route_dry_run_path(out_dir)
    target.parent.mkdir(parents=True, exist_ok=True)
    document = {'count': len(routes), 'routes': routes}
    with target.open('w', encoding='utf-8') as handle:
        json.dump(document, handle, ensure_ascii=False, indent=2)
    return target

def land_dry_run_path(out_dir):
    return Path(out_dir) / 'lands_snapshot.json'


def write_lands_dry_run(lands, out_dir, total_count=None):
    """Write what would have been sent, minus the token, and return the path."""
    target = land_dry_run_path(out_dir)
    target.parent.mkdir(parents=True, exist_ok=True)
    document = {
        'count': len(lands),
        'total_count_reported_by_dji': total_count,
        'lands': lands,
    }
    with target.open('w', encoding='utf-8') as handle:
        json.dump(document, handle, ensure_ascii=False, indent=2)
    return target
