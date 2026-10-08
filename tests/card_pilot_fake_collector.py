# -*- coding: utf-8 -*-
"""Stand-in for `python -m drone_collector.main --sources --ids-file F --send-sources`.

DRONE-CARD-COVERAGE-001, tests of the W1+S1 owner block
(tests/test_dji_card_coverage_blocks.py). The test commits this file as
`drone_collector/main.py` of a throw-away pilot checkout; config.py and
runlock.py there stay the real ones. It never opens a browser and never
contacts anything.

What it does as the real collector does, because the block depends on it:

* logs to STDOUT in the real format ('%Y-%m-%d %H:%M:%S INFO collector: ...',
  drone_collector/logging_setup.py) and appends the same lines to
  PACKAGE_ROOT/logs/collector.log;
* prints the 'Configuration: {...}' line (Python dict repr, as
  CollectorConfig.describe() is logged) before taking the lock;
* takes the collector lock at DJI_COLLECTOR_LOCK_PATH with
  DJI_COLLECTOR_LOCK_WAIT_S through the real `runlock`, exit 24 when busy;
* opens the session file read only;
* visits only the ids of --ids-file, ascending, one status line per flight;
* queues each card during the walk and "sends" the queue after it, as the
  real drain does; "received" means what /drones/api/source_sync leaves on
  staging (a dji_source_revisions row with this run's capture_run_id and the
  card pointer in dji_flight_evidence); the real receiver is covered by
  tests/test_dji_card_coverage_pilot_e2e.py;
* ends with a 'RUN SUMMARY ...' line carrying snapshot_run_id.

The scenario (JSON at CARD_PILOT_FAKE_COLLECTOR) decides the status and the
card key per flight, extra log lines after a given flight (stop markers), a
hang after them (so the block has to stop the run), the exit code, writes it
must not do (a foreign revision, the session file, the production database
or log).
What it
saw -- argv, cwd, the environment names the block must set, whether the
token was present -- goes to the scenario's `record` file; no token value.
"""

import json
import os
import sqlite3
import sys
import time

from datetime import datetime, timezone

from drone_collector import config as collector_config
from drone_collector import runlock

PROVIDER = 'SYNTHETIC-ACCOUNT-NOT-REAL'
NAMES = ('VEHICLE_SOFT_BASE_URL', 'DJI_STORAGE_STATE', 'DJI_COLLECTOR_LOCK_PATH',
         'DJI_COLLECTOR_LOCK_WAIT_S', 'DRONE_OUTBOX_DIR', 'DJI_HEADLESS',
         'PYTHONPATH', 'PYTHONHOME', 'PYTHONSAFEPATH', 'PYTHONIOENCODING')
FLAGS = ('--sources', '--ids-file', '--send-sources')
LOG = []


def log(text, level='INFO'):
    line = '%s %s collector: %s' % (datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
                                     level, text)
    sys.stdout.write(line + '\n')
    sys.stdout.flush()
    LOG.append(line)


def flush_file_log(also=None):
    folder = os.path.join(str(collector_config.PACKAGE_ROOT), 'logs')
    os.makedirs(folder, exist_ok=True)
    for path in [os.path.join(folder, 'collector.log')] + ([also] if also else []):
        with open(path, 'a', encoding='utf-8') as fh:
            fh.write('\n'.join(LOG) + '\n')


def now_utc():
    return datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%S')


def receive(con, fid, n, run_id, key=None):
    sha = ('%064x' % (0xC0FFEE00 + fid * 7 + n))[-64:]
    stamp = now_utc()
    cur = con.execute(
        'INSERT INTO dji_source_revisions (provider_account_id, flight_id, '
        'scope_key, source_type, sha256, size_bytes, captured_at_utc, '
        'parser_version, request_context_json, capture_run_id, '
        'is_evidence_import, storage_kind, body_text, body_path, received_at, '
        "last_seen_at, ingest_count) VALUES (?,?,?,'CARD',?,100,?,'FAKE-COLLECTOR',"
        "'{}',?,0,'INLINE','{}',NULL,?,?,1)",
        (PROVIDER, fid, str(fid), sha, stamp, run_id, stamp, stamp))
    rev = cur.lastrowid
    key = key or '%032x' % (0xABC000 + n)
    if con.execute('SELECT 1 FROM dji_flight_evidence WHERE flight_id = ?',
                   (fid,)).fetchone():
        con.execute('UPDATE dji_flight_evidence SET card_revision_id = ?, '
                    'card_geometry_md5 = ?, updated_at = ? WHERE flight_id = ?',
                    (rev, key, stamp, fid))
    else:
        con.execute('INSERT INTO dji_flight_evidence (flight_id, '
                    'provider_account_id, card_revision_id, card_geometry_md5, '
                    'updated_at) VALUES (?,?,?,?,?)', (fid, PROVIDER, rev, key, stamp))


def main(argv):
    with open(os.environ['CARD_PILOT_FAKE_COLLECTOR'], encoding='utf-8') as fh:
        sc = json.load(fh)
    record = {'argv': argv, 'cwd': os.getcwd(), 'finished': False,
              'package_root': str(collector_config.PACKAGE_ROOT),
              'env': {k: os.environ.get(k) for k in NAMES},
              'token_present': bool(os.environ.get('DRONE_API_TOKEN')),
              'token_matches': os.environ.get('DRONE_API_TOKEN') == sc.get('token')}

    def save_record():
        with open(sc['record'], 'w', encoding='utf-8') as fh:
            json.dump(record, fh)
    save_record()
    with open(sc['record'] + '.runs', 'a', encoding='utf-8') as fh:
        fh.write(json.dumps(argv) + '\n')
    if [a for a in argv if a.startswith('-')] != list(FLAGS):
        log('usage: expected exactly %s' % ' '.join(FLAGS), 'ERROR')
        flush_file_log()
        return 2
    base = (os.environ.get('VEHICLE_SOFT_BASE_URL') or '').rstrip('/')
    described = {'records_url': 'https://www.djiag.com/records/list',
                 'storage_state': os.environ.get('DJI_STORAGE_STATE'),
                 'headless': (os.environ.get('DJI_HEADLESS') or '').lower() == 'true',
                 'api_token': 'set' if os.environ.get('DRONE_API_TOKEN') else 'missing',
                 'outbox_dir': sc.get('report_outbox', os.environ.get('DRONE_OUTBOX_DIR')),
                 'source_sync_url': base + '/drones/api/source_sync'}
    log('Configuration: %s' % described)
    if not base or not os.environ.get('DRONE_API_TOKEN'):
        log('Configuration error: VEHICLE_SOFT_BASE_URL or DRONE_API_TOKEN is not set', 'ERROR')
        flush_file_log()
        return 1
    lock = runlock.RunLock(os.environ['DJI_COLLECTOR_LOCK_PATH'], purpose='sources')
    if not lock.acquire(wait_s=float(os.environ.get('DJI_COLLECTOR_LOCK_WAIT_S') or 1800)):
        log('Another collector run holds %s (owner unknown). Waited 0 s; nothing was '
            'collected and nothing was sent. Exit 24.' % lock.path, 'ERROR')
        flush_file_log()
        return 24
    run_id = 'sources:ids-file:' + datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')
    code = int(sc.get('exit', 0))
    visited = cards = page_errors = new = 0
    try:
        with open(os.environ['DJI_STORAGE_STATE'], 'rb') as fh:
            fh.read()
        if sc.get('touch_session'):
            with open(os.environ['DJI_STORAGE_STATE'], 'ab') as fh:
                fh.write(b' ')
        with open(argv[argv.index('--ids-file') + 1], encoding='utf-8-sig') as fh:
            ids = sorted({int(l.split('#')[0]) for l in fh if l.split('#')[0].strip()})
        outbox = os.environ['DRONE_OUTBOX_DIR']
        for name in ('pending', 'sent'):
            os.makedirs(os.path.join(outbox, name), exist_ok=True)
        queued = []
        page_errors_in_row = 0
        for n, fid in enumerate(ids):
            status = sc.get('status', {}).get(str(fid), 'card')
            if status == 'stop_here':
                break
            visited += 1
            if status == 'page_error':
                page_errors += 1
                page_errors_in_row += 1
                log('Flight %d: the record page did not open (TimeoutError)' % fid, 'ERROR')
                if page_errors_in_row == 3:
                    # The pinned collector stops itself here (sources.py, browser_looks_dead).
                    log('Three record pages in a row did not open; the browser is not usable. '
                        'Stopping after %d of %d flight(s).' % (n + 1, len(ids)), 'ERROR')
                    break
            elif status == 'nothing':
                # The page opened and nothing came (sources.py: 'nothing captured').
                page_errors_in_row = 0
                log('Flight %d: NO_V4 (nothing captured)' % fid)
            else:
                # 'card', or 'no_v4': route and descriptor came, the card did not
                # (what a refused card request leaves).
                page_errors_in_row = 0
                items = 'airlines, card, route, v4' if status == 'card' else 'airlines, route'
                log('Flight %d: captured route (%d bytes)' % (fid, 429))
                log('Flight %d: V4 (%s)' % (fid, items) if status == 'card'
                    else 'Flight %d: NO_V4 (%s)' % (fid, items))
                if status == 'card':
                    cards += 1
                    queued.append((fid, n))
                    with open(os.path.join(outbox, 'pending', 'source_%d_card.json' % fid), 'w') as fh:
                        fh.write('{}')
            for line in sc.get('log_after', {}).get(str(n + 1), []):
                log(line, 'WARNING')
            if str(n + 1) == str(sc.get('hang_after')):
                time.sleep(float(sc.get('hang_s', 120)))
        # As the real collector: the queue is sent after the whole walk, so a
        # run stopped during it has sent nothing.
        con = sqlite3.connect(sc['staging_db'])
        for fid, n in queued:
            receive(con, fid, n, run_id, sc.get('card_keys', {}).get(str(fid)))
            new += 1
            name = 'source_%d_card.json' % fid
            os.replace(os.path.join(outbox, 'pending', name), os.path.join(outbox, 'sent', name))
        if sc.get('raw_change'):
            con.execute('UPDATE drone_flights SET area_ha = area_ha + 1 '
                        'WHERE dji_flight_id = ?', (ids[0],))
        for fid in sc.get('foreign_revisions', []):
            receive(con, int(fid), 999, run_id)
            new += 1
        con.commit()
        con.close()
        if sc.get('production_db'):
            prod = sqlite3.connect(sc['production_db'])
            receive(prod, ids[0], 0, run_id)
            prod.commit()
            prod.close()
    finally:
        lock.release()
    if not sc.get('no_summary'):
        log('RUN SUMMARY mode=sources dry_run=false snapshot_run_id=%s period_from=- '
            'sources_requested=%d sources_visited=%d sources_card=%d '
            'sources_v4_failed=%d sources_page_errors=%d sources_rejected=%d '
            'sources_descriptor_refused=0 '
            'sources_envelopes_sent=%d sources_batch_accepted=%s sources_new=%d '
            'sources_ingest_errors=%d exit=%d'
            % (run_id, len(ids), sc.get('report_visited', visited), cards,
               int(sc.get('v4_failed', 0)), page_errors, int(sc.get('rejected', 0)),
               cards, sc.get('accepted', 'true'), sc.get('report_new', new),
               int(sc.get('ingest_errors', 0)), code))
    flush_file_log(sc.get('also_log'))
    record['finished'] = True
    save_record()
    return code


if __name__ == '__main__':
    sys.exit(main(sys.argv[1:]))
