#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""tools/drone_replay_appdb.py -- replay the old collector's raw DJI
payloads into the /drones/api/flight_sync endpoint (DRONE-002).

Reads the `raw_json` column of the `flights` table in the old collector
database (app.db) and POSTs the payload objects to the endpoint in chunks
with kind='replay'. The endpoint deduplicates on the payload id, so
re-running the replay after a failure is safe and is the normal recovery
path.

Token: read from the DRONE_API_TOKEN environment variable of the calling
shell. --token exists only as an explicit override and puts the secret
into the shell history -- prefer the environment variable. When neither is
present the script exits with a clear message; there is no default and no
empty-token fallback. The token is never printed, not even truncated.

Safety:
  - the source database is opened read-only AND immutable:
    file:<path>?mode=ro&immutable=1. The source is a static copy;
    mode=ro alone would still create -shm and -wal files beside it.
    immutable=1 is correct ONLY because the copy is static -- never open a
    live database this way;
  - stdlib sqlite3 and urllib only; no Flask import (importing app runs
    db.create_all() and turns a replay tool into a writer);
  - console output is ASCII only (NSSM/Windows log encoding).

--inject-unknown appends one synthetic flight with nickname '99 Nowhere'
and dji id 99999999999 (real DJI ids in the sample are 9 digits, so it
cannot collide). It is the positive control for the unresolved path: the
response must report unresolved=1 and the row must be stored with
drone_unit_id NULL. On a repeat run it counts as a duplicate.

Usage:
  set DRONE_API_TOKEN=...   (cmd)   /   export DRONE_API_TOKEN=... (sh)
  python tools/drone_replay_appdb.py --db D:\\old\\app.db ^
      --url http://10.103.25.200:5050/drones/api/flight_sync
"""

import argparse
import json
import os
import sqlite3
import sys
import urllib.error
import urllib.request

DEFAULT_CHUNK = 500

# [REASON]: the synthetic id must be impossible in real data -- observed DJI
# flight ids are 9-digit numbers, this one has 11 digits.
INJECT_ID = 99999999999
INJECT_NICKNAME = '99 Nowhere'


def _ascii(text):
    return str(text).encode('ascii', 'backslashreplace').decode('ascii')


def _open_readonly(path):
    """Open the static source copy read-only and immutable.

    [REASON]: mode=ro alone still lets SQLite create -shm/-wal companion
    files next to the source in WAL mode; immutable=1 prevents that and is
    correct here ONLY because the source is a static copy, not a live db.
    """
    if not os.path.exists(path):
        raise RuntimeError('source database not found at %s' % path)
    uri = 'file:%s?mode=ro&immutable=1' % (
        path.replace('\\', '/').replace('?', '%3f'))
    return sqlite3.connect(uri, uri=True)


def _iter_source_payloads(con, limit):
    """Yield parsed raw_json objects from the old collector's flights table."""
    sql = 'SELECT id, raw_json FROM flights ORDER BY id'
    if limit is not None:
        sql += ' LIMIT %d' % limit
    for row_id, raw in con.execute(sql):
        try:
            yield json.loads(raw)
        except (TypeError, ValueError) as exc:
            raise RuntimeError('flights.id=%s carries unparseable raw_json: %s'
                               % (row_id, exc))


def _synthetic_unknown_flight():
    """Positive control for the unresolved path: storable, unknown nickname."""
    return {
        'id': INJECT_ID,
        'nickname': INJECT_NICKNAME,
        'serial_number': 'SYNTHETIC-UNKNOWN',
        'flyer_name': 'SYNTHETIC',
        'team_name': 'SYNTHETIC',
        'start_timestamp': 1747699200,
        'end_timestamp': 1747699260,
        'work_time_seconds': 60,
        'new_work_area': 0,
        'spray_usage': 0,
        'sow_usage': 0,
        'usage_type': 0,
        'mode_name': 0,
        'manual_mode': False,
        'location': None,
        'lat': None,
        'lng': None,
    }


def _post_chunk(url, token, flights, timeout):
    body = json.dumps({'token': token, 'kind': 'replay',
                       'flights': flights}).encode('utf-8')
    req = urllib.request.Request(
        url, data=body, method='POST',
        headers={'Content-Type': 'application/json'})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return resp.status, resp.read().decode('utf-8', 'replace')
    except urllib.error.HTTPError as exc:
        return exc.code, exc.read().decode('utf-8', 'replace')


def main(argv=None):
    parser = argparse.ArgumentParser(
        description='Replay raw DJI payloads from the old collector app.db '
                    'into /drones/api/flight_sync.',
        epilog='The endpoint deduplicates on the DJI flight id, so '
               're-running after a failure is safe.')
    parser.add_argument('--db', required=True,
                        help='path to the old collector app.db (static copy)')
    parser.add_argument('--url', required=True,
                        help='full endpoint URL, e.g. '
                             'http://10.103.25.200:5050/drones/api/flight_sync')
    parser.add_argument('--chunk', type=int, default=DEFAULT_CHUNK,
                        help='flights per request (default %d, endpoint cap '
                             'is 1000)' % DEFAULT_CHUNK)
    parser.add_argument('--limit', type=int, default=None,
                        help='replay only the first N source rows (optional)')
    parser.add_argument('--inject-unknown', action='store_true',
                        help='append one synthetic flight with an unknown '
                             "nickname ('99 Nowhere') as the positive "
                             'control for the unresolved path')
    parser.add_argument('--token', default=None,
                        help='explicit token override. WARNING: puts the '
                             'secret into the shell history -- prefer the '
                             'DRONE_API_TOKEN environment variable')
    parser.add_argument('--timeout', type=int, default=120,
                        help='HTTP timeout per chunk, seconds (default 120)')
    args = parser.parse_args(argv)

    token = args.token if args.token is not None else os.environ.get(
        'DRONE_API_TOKEN')
    if not token:
        print('ERROR: no token. Set the DRONE_API_TOKEN environment variable '
              'in the calling shell (or pass --token explicitly). There is '
              'no default.')
        return 2
    if args.chunk < 1 or args.chunk > 1000:
        print('ERROR: --chunk must be between 1 and 1000 (endpoint cap).')
        return 2

    try:
        con = _open_readonly(args.db)
    except (RuntimeError, sqlite3.Error) as exc:
        print('ERROR: %s' % _ascii(exc))
        return 2

    totals = {'seen': 0, 'new': 0, 'duplicates': 0, 'unresolved': 0,
              'errors': 0}
    chunk_no = 0
    buffer = []

    def flush():
        nonlocal chunk_no
        if not buffer:
            return
        chunk_no += 1
        status, text = _post_chunk(args.url, token, buffer, args.timeout)
        if status != 200:
            raise RuntimeError('chunk %d: HTTP %d: %s'
                               % (chunk_no, status, text[:300]))
        reply = json.loads(text)
        for key in totals:
            totals[key] += int(reply.get(key, 0))
        print('chunk %3d: sent %4d  -> log_id=%s new=%s duplicates=%s '
              'unresolved=%s errors=%s'
              % (chunk_no, len(buffer), reply.get('log_id'),
                 reply.get('new'), reply.get('duplicates'),
                 reply.get('unresolved'), reply.get('errors')))
        del buffer[:]

    try:
        for payload in _iter_source_payloads(con, args.limit):
            buffer.append(payload)
            if len(buffer) >= args.chunk:
                flush()
        if args.inject_unknown:
            buffer.append(_synthetic_unknown_flight())
        flush()
    except (RuntimeError, ValueError, OSError) as exc:
        print('ERROR: %s' % _ascii(exc))
        print('Chunks already accepted stay accepted; re-running the replay '
              'is safe (the endpoint deduplicates).')
        return 1
    finally:
        con.close()

    print('')
    print('replay finished: %d chunk(s)' % chunk_no)
    print('  seen       : %d' % totals['seen'])
    print('  new        : %d' % totals['new'])
    print('  duplicates : %d' % totals['duplicates'])
    print('  unresolved : %d' % totals['unresolved'])
    print('  errors     : %d' % totals['errors'])
    return 0


if __name__ == '__main__':
    sys.exit(main())
