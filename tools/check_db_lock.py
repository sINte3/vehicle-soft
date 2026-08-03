#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""check_db_lock.py -- prove that no other process has the database open.

Run this with all services stopped, immediately before applying migrations.

TOOL-DBLOCK-WAL-001
  The previous version took a `BEGIN EXCLUSIVE` transaction and printed
  "safe to run migrations" when it succeeded. In WAL mode that proves
  nothing: `BEGIN EXCLUSIVE` only needs the WAL write lock, and idle
  connections hold no write lock, so it succeeds while a live application
  has the database open. On the v1.11 release the runbook ran this check
  twice as a negative control -- once with three services up, once with
  them down -- and it printed the identical green verdict both times.

  Measured on Windows / Python 3.14 / stdlib sqlite3, one temporary WAL
  database, one holder process holding an idle connection:

      state   -wal/-shm   BEGIN EXCLUSIVE   locking_mode=EXCLUSIVE
      free    absent      OK                OK
      held    present     OK   <-- useless  SQLITE_BUSY
      stale   present     OK                OK

  So neither signal alone is enough and `BEGIN EXCLUSIVE` is worthless
  here. This tool uses both of the signals that do discriminate:

    - presence of `-wal` / `-shm`, sampled from the filesystem BEFORE any
      connection is opened, separates "clean" from "artefacts present";
    - `PRAGMA locking_mode = EXCLUSIVE` followed by a transaction forces
      the exclusive *file* lock and raises SQLITE_BUSY while any other
      connection holds the database -- that separates "held" from "stale".

Three outcomes, not two:

  0  CLEAN  -- no other holder and no leftover WAL artefacts.
  2  HELD   -- another process has the database open. Stop the services.
  3  STALE  -- artefacts present but no live holder: what an unclean
               shutdown leaves behind. Neither safe to proceed nor a
               running service, and the operator must be told which of
               the two he is looking at.
  1  ERROR  -- the database file does not exist (kept distinct from HELD).

Run:
  cd C:\\transport-report
  & "C:\\Program Files\\Python314\\python.exe" tools\\check_db_lock.py

Exit code 0 = safe to migrate. Anything else = do not migrate.
"""

import argparse
import os
import sqlite3
import sys

# [REASON]: adopted into tools/ from the untracked production copy
# (DEPLOY-DRIFT-001). ROOT must stay the repository root -- one level above
# tools/ -- so instance/transport.db and the report files resolve exactly
# as they did when this script lived at C:\transport-report directly.
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DEFAULT_DB = os.path.join(ROOT, 'instance', 'transport.db')

EXIT_CLEAN = 0
EXIT_ERROR = 1
EXIT_HELD = 2
EXIT_STALE = 3


def artefact_state(db_path):
    """Return [(name, present, size)] for the -wal and -shm sidecar files.

    Sampled from the filesystem only. Must be called BEFORE any connection
    is opened.
    """
    # [REASON]: opening a read/write connection to a database with a leftover
    # WAL checkpoints it and deletes the -wal file on close. Sampling after
    # the probe would therefore report the state this tool itself produced,
    # not the state the operator needs to see.
    rows = []
    for suffix in ('-wal', '-shm'):
        path = db_path + suffix
        present = os.path.exists(path)
        rows.append((os.path.basename(path), present,
                     os.path.getsize(path) if present else 0))
    return rows


def probe_live_holder(db_path, busy_timeout_ms):
    """Return (held, detail). held=True when another process has the db open.

    Never creates the database: the caller checks existence first.
    """
    # [REASON]: a read-only connection (file:...?mode=ro) cannot be used to
    # probe a WAL database -- it cannot create the -shm index it needs and
    # fails with "disk I/O error" identically whether the database is free,
    # held or stale. A check that returns the same result in every state is
    # not a check, so the probe has to be a read/write connection.
    con = sqlite3.connect(db_path, timeout=max(busy_timeout_ms, 1) / 1000.0)
    try:
        con.execute('PRAGMA busy_timeout=%d' % busy_timeout_ms)
        # [REASON]: locking_mode=EXCLUSIVE alone changes nothing until the
        # next transaction -- SQLite defers taking the exclusive file lock.
        # The BEGIN plus a read is what forces it; a read keeps the probe
        # free of any write of our own.
        con.execute('PRAGMA locking_mode=EXCLUSIVE')
        con.execute('BEGIN')
        con.execute('SELECT count(*) FROM sqlite_master').fetchone()
        con.rollback()
        return False, ''
    except sqlite3.OperationalError as exc:
        return True, str(exc)
    finally:
        con.close()


def main(argv=None):
    parser = argparse.ArgumentParser(
        description='Prove that no other process has the database open.')
    parser.add_argument('--db', default=DEFAULT_DB,
                        help='path to the SQLite database '
                             '(default: instance/transport.db)')
    parser.add_argument('--busy-timeout-ms', type=int, default=5000,
                        help='how long to wait for the exclusive lock '
                             '(default: 5000)')
    args = parser.parse_args(argv)

    db_path = args.db

    # [REASON]: sqlite3.connect() CREATES a missing file. Exiting before any
    # connection is opened is what keeps a typo in --db from leaving an empty
    # database behind that the next run would then call clean.
    if not os.path.exists(db_path):
        print('ERROR: database not found at ' + db_path, file=sys.stderr)
        return EXIT_ERROR

    artefacts = artefact_state(db_path)
    for name, present, size in artefacts:
        if present:
            print('  {0:<20} present, {1:,} bytes'.format(name, size))
        else:
            print('  {0:<20} absent'.format(name))

    any_artefact = any(present for _, present, _ in artefacts)
    held, detail = probe_live_holder(db_path, args.busy_timeout_ms)
    print('')

    if held:
        print('HELD: {0}'.format(detail), file=sys.stderr)
        print('Another process has the database open. DO NOT MIGRATE.',
              file=sys.stderr)
        print('Stop the services first: TransportReport, TransportBot, '
              'TransportBot003.', file=sys.stderr)
        return EXIT_HELD

    if any_artefact:
        print('STALE: no process holds the database, but -wal/-shm are '
              'present.', file=sys.stderr)
        print('That is what an unclean shutdown leaves behind, not a running '
              'service.', file=sys.stderr)
        print('Confirm the services really are stopped, then have someone '
              'who knows', file=sys.stderr)
        print('the state of the last shutdown clear it. DO NOT MIGRATE yet.',
              file=sys.stderr)
        # [REASON]: this probe opened a read/write connection, which
        # checkpoints and removes a leftover -wal. Say so, or the operator
        # reruns the tool, sees -wal gone and reads the change as evidence
        # that the problem fixed itself.
        print('NOTE: this check just checkpointed the leftover -wal. A rerun '
              'will show', file=sys.stderr)
        print('      -wal absent; that is this tool, not a change of state.',
              file=sys.stderr)
        return EXIT_STALE

    print('CLEAN -- no other process has the database open, no leftover WAL.')
    print('Safe to run migrations.')
    return EXIT_CLEAN


if __name__ == '__main__':
    sys.exit(main())
