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

from drone_collector.config import (MAX_BATCH_SIZE,
                                    MAX_LAND_SNAPSHOT_BATCH_SIZE,
                                    MAX_ROUTE_BATCH_SIZE,
                                    MAX_SOURCE_BATCH_SIZE)
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


# ─── DJI-AREA-EVIDENCE-001: immutable source bodies ──────────────────────────
#
# Contract, read out of drones.py:
#
#     POST {VEHICLE_SOFT_BASE_URL}/drones/api/source_sync
#     {"token": "...", "sources": [ ...source items, at most 50... ]}
#
# Four counters come back, and every seen source lands in exactly one:
#
#     seen = new + duplicates + errors          (+ refreshed, informational)
#
# `duplicates` are identical bytes already stored; a differing body of the
# same flight/type is a NEW revision, so a repeat send never overwrites.


class SourceSendResult(object):
    """The counters summed over every batch of one source run."""

    __slots__ = ('batches', 'seen', 'new', 'duplicates', 'errors',
                 'refreshed', 'status')

    def __init__(self):
        self.batches = 0
        self.seen = 0
        self.new = 0
        self.duplicates = 0
        self.errors = 0
        self.refreshed = 0
        self.status = None

    def add(self, body):
        self.batches += 1
        self.seen += _int(body.get('seen'))
        self.new += _int(body.get('new'))
        self.duplicates += _int(body.get('duplicates'))
        self.errors += _int(body.get('errors'))
        self.refreshed += _int(body.get('refreshed'))
        status = body.get('status')
        # The first non-ok status wins: one refused batch is a refused run.
        if self.status in (None, 'ok'):
            self.status = status
        return self

    @property
    def counters_agree(self):
        """seen == new + duplicates + errors.

        [REASON]: checked on OUR side too, not only asserted in the endpoint's
        docstring. A server that starts double-counting a bucket would
        otherwise be discovered by someone adding numbers off a screen.
        """
        return self.seen == self.new + self.duplicates + self.errors

    def as_dict(self):
        return {'batches': self.batches, 'seen': self.seen, 'new': self.new,
                'duplicates': self.duplicates, 'errors': self.errors,
                'refreshed': self.refreshed, 'status': self.status}

    def __repr__(self):
        return 'SourceSendResult(%s)' % self.as_dict()


def build_source_payload(token, sources):
    """The request body. Never log the result -- it carries the token."""
    return {'token': token, 'sources': sources}


def chunk_sources(sources, batch_size=None):
    """Split into batches, none of which exceeds the endpoint's cap of 50."""
    size = min(int(batch_size or MAX_SOURCE_BATCH_SIZE), MAX_SOURCE_BATCH_SIZE)
    if size < 1:
        size = 1
    return [sources[i:i + size] for i in range(0, len(sources), size)]


def send_sources(sources, cfg, logger=None, post_fn=None, sleep_fn=None):
    """Chunk and POST the source items. Returns a SourceSendResult.

    The items go through verbatim: `body_b64`, `sha256` and `size_bytes` were
    computed at capture time from the very bytes DJI served, and the endpoint
    re-checks the hash -- nothing here may touch them.
    """
    out = logger or log
    post = post_fn or _requests_post
    sleep = sleep_fn or time.sleep

    result = SourceSendResult()
    if not sources:
        out.info('Nothing to send: 0 sources')
        return result

    batches = chunk_sources(sources, getattr(cfg, 'source_batch_size', None))
    out.info('Sending %d source(s) in %d batch(es) of at most %d to %s',
             len(sources), len(batches), MAX_SOURCE_BATCH_SIZE,
             cfg.source_sync_url)

    for index, batch in enumerate(batches, start=1):
        payload = build_source_payload(cfg.api_token, batch)
        body = _post_with_retries(post, sleep, cfg.source_sync_url, payload,
                                  index, len(batches), out)
        result.add(body)
        out.info('Batch %d/%d answered: status=%s seen=%s new=%s '
                 'duplicates=%s errors=%s refreshed=%s', index, len(batches),
                 body.get('status'), body.get('seen'), body.get('new'),
                 body.get('duplicates'), body.get('errors'),
                 body.get('refreshed'))

    out.info('Source totals: batches=%d seen=%d new=%d duplicates=%d '
             'errors=%d refreshed=%d', result.batches, result.seen,
             result.new, result.duplicates, result.errors, result.refreshed)
    if not result.counters_agree:
        out.error('The endpoint returned counters that do not add up: '
                  'seen=%d but new+duplicates+errors=%d', result.seen,
                  result.new + result.duplicates + result.errors)
    return result


# ─── DJI-AREA-EVIDENCE-001: catalog snapshot ─────────────────────────────────
#
# Contract, read out of drones.py:
#
#     POST {VEHICLE_SOFT_BASE_URL}/drones/api/land_snapshot_sync
#     {"token": "...", "snapshot": {...}, "lands": [...], "geometries": [...]}
#
# One snapshot spans several requests sharing `snapshot.capture_run_id`; the
# request carrying `final: true` closes it. Counters:
#
#     lands_seen = lands_new + lands_seen_before + errors
#     geometries_seen = geometries_new + geometries_unchanged + geometries_errors


class LandSnapshotSendResult(object):
    """The counters summed over every chunk of one snapshot."""

    __slots__ = ('batches', 'lands_seen', 'lands_new', 'lands_seen_before',
                 'errors', 'geometries_seen', 'geometries_new',
                 'geometries_unchanged', 'geometries_errors',
                 'geometries_referenced_but_absent', 'status', 'snapshot_id')

    # [REASON]: `geometries_referenced_but_absent` стоит здесь не для
    # симметрии. Инкрементальный снимок нарочно не шлёт уже известные
    # полигоны, поэтому «пропущено» и «потеряно» отличает только этот
    # счётчик -- а приёмник считал его и отдавал в JSON, пока сборщик
    # молча выбрасывал. Тревога, которую никто не читает, тревогой не
    # является.
    COUNTER_KEYS = ('lands_seen', 'lands_new', 'lands_seen_before', 'errors',
                    'geometries_seen', 'geometries_new',
                    'geometries_unchanged', 'geometries_errors',
                    'geometries_referenced_but_absent')

    def __init__(self):
        self.batches = 0
        for key in self.COUNTER_KEYS:
            setattr(self, key, 0)
        self.status = None
        self.snapshot_id = None

    def add(self, body):
        self.batches += 1
        for key in self.COUNTER_KEYS:
            setattr(self, key, getattr(self, key) + _int(body.get(key)))
        if self.status in (None, 'ok'):
            self.status = body.get('status')
        if body.get('snapshot_id') is not None:
            self.snapshot_id = body.get('snapshot_id')
        return self

    @property
    def counters_agree(self):
        return (self.lands_seen == (self.lands_new + self.lands_seen_before
                                    + self.errors)
                and self.geometries_seen == (self.geometries_new
                                             + self.geometries_unchanged
                                             + self.geometries_errors))

    def as_dict(self):
        out = {'batches': self.batches, 'status': self.status,
               'snapshot_id': self.snapshot_id}
        for key in self.COUNTER_KEYS:
            out[key] = getattr(self, key)
        return out

    def __repr__(self):
        return 'LandSnapshotSendResult(%s)' % self.as_dict()


def build_land_snapshot_payload(token, snapshot, lands, geometries):
    """The request body. Never log the result -- it carries the token."""
    return {'token': token, 'snapshot': snapshot, 'lands': lands,
            'geometries': geometries}


def send_land_snapshot_chunk(chunk, cfg, logger=None, post_fn=None,
                             sleep_fn=None, index=1, total=1):
    """POST ONE chunk of a snapshot. Returns a LandSnapshotSendResult.

    A chunk is `{"snapshot": {...}, "lands": [...], "geometries": [...]}` --
    the envelope body the collector queued. Chunks are not merged here: the
    receiver reads `final` per request, so the order and the boundaries the
    collector chose must reach it exactly.
    """
    out = logger or log
    post = post_fn or _requests_post
    sleep = sleep_fn or time.sleep

    lands = chunk.get('lands') or []
    if len(lands) > MAX_LAND_SNAPSHOT_BATCH_SIZE:
        raise IngestRejected('snapshot chunk %d/%d holds %d lands, the cap is '
                             '%d -- not sent' % (index, total, len(lands),
                                                 MAX_LAND_SNAPSHOT_BATCH_SIZE))
    payload = build_land_snapshot_payload(cfg.api_token, chunk.get('snapshot'),
                                          lands, chunk.get('geometries') or [])
    body = _post_with_retries(post, sleep, cfg.land_snapshot_sync_url, payload,
                              index, total, out)
    result = LandSnapshotSendResult().add(body)
    out.info('Snapshot chunk %d/%d answered: status=%s snapshot_id=%s '
             'lands_seen=%s lands_new=%s lands_seen_before=%s errors=%s '
             'geometries_seen=%s geometries_new=%s geometries_unchanged=%s '
             'geometries_errors=%s referenced_but_absent=%s',
             index, total, body.get('status'),
             body.get('snapshot_id'), body.get('lands_seen'),
             body.get('lands_new'), body.get('lands_seen_before'),
             body.get('errors'), body.get('geometries_seen'),
             body.get('geometries_new'), body.get('geometries_unchanged'),
             body.get('geometries_errors'),
             body.get('geometries_referenced_but_absent'))
    return result


# ─── DRONE-AREA-CAPTURE-001: какие полигоны у приёмника уже есть ─────────────
#
#     POST {VEHICLE_SOFT_BASE_URL}/drones/api/land_geometry_manifest
#     {"token": "...", "content_md5": ["ab12...", ...]}
#     -> {"asked": 6171, "known": ["ab12...", ...], "known_count": 6158}
#
# Только чтение. Ответ ограничен тем, о чём спросили, поэтому запрос не
# выкачивает чужое хранилище целиком и растёт вместе с каталогом, а не с
# базой.


def build_geometry_manifest_payload(token, md5s):
    return {'token': token, 'content_md5': list(md5s)}


def known_geometry_md5(md5s, cfg, logger=None, post_fn=None, sleep_fn=None,
                       batch_size=None):
    """frozenset уже сохранённых contentMd5, либо None -- «выяснить не вышло».

    [REASON]: None и пустое множество -- РАЗНЫЕ ответы, и путать их нельзя.
    Пустое множество означает «приёмник не хранит ни одного из этих
    полигонов» и ведёт к честной полной загрузке. None означает «мы не
    знаем», и вызывающий обязан тоже скачать всё -- но сказать об этом в
    лог. Вернуть пустое множество при недоступном манифесте значило бы
    выдать незнание за знание.

    Отказ манифеста НЕ роняет прогон: снимок без инкрементальности медленный,
    снимок без полигонов -- испорченный.
    """
    from drone_collector.sources import MANIFEST_BATCH_SIZE

    out = logger or log
    url = cfg.land_geometry_manifest_url
    if not url:
        out.warning('No base URL is configured, so the geometry manifest was '
                    'not asked for; every polygon will be downloaded.')
        return None
    wanted = sorted({str(m).lower() for m in md5s if m})
    if not wanted:
        return frozenset()
    post = post_fn or _requests_post
    sleep = sleep_fn or time.sleep
    size = int(batch_size or MANIFEST_BATCH_SIZE)
    batches = [wanted[i:i + size] for i in range(0, len(wanted), size)]
    known = set()
    for index, batch in enumerate(batches, start=1):
        payload = build_geometry_manifest_payload(cfg.api_token, batch)
        try:
            body = _post_with_retries(post, sleep, url, payload, index,
                                      len(batches), out)
        except (TransportError, IngestRejected) as exc:
            out.warning('The geometry manifest is unavailable (%s). Falling '
                        'back to downloading every polygon -- slow, but the '
                        'snapshot stays complete.', exc)
            return None
        answer = body.get('known') if isinstance(body, dict) else None
        if not isinstance(answer, list):
            out.warning('The geometry manifest answered without a "known" '
                        'list. Falling back to downloading every polygon.')
            return None
        known.update(str(item).lower() for item in answer
                     if isinstance(item, str))
    out.info('Geometry manifest: asked about %d polygon(s), the receiver '
             'already stores %d.', len(wanted), len(known))
    return frozenset(known)
