#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""tools/state_snapshot.py -- one pasteable state block per worktree.

Replaces the three rounds of question-and-answer at the start of every
working session ("which commit is production on?", "which migrations are
recorded?", "are the services up?") with a single command whose output can
be pasted into the chat as-is.

Prints, in order:
  - current HEAD short hash and the tags pointing at HEAD;
  - commits present on origin/main but not on HEAD
    (git log --oneline HEAD..origin/main). NOTE: this script does NOT run
    git fetch -- origin/main is whatever the last fetch left behind;
    fetching is the operator's decision and is said so in the output;
  - schema_migrations row count and the last five names
    (database at <worktree>/instance/transport.db, opened read-only);
  - on Windows only (os.name == 'nt'): the state of the six services
    TransportReport, TransportReportStaging, TransportBot,
    TransportBotStaging, TransportBot003, TransportBot003Staging
    via sc query.

Every step is independent: a failure (missing database, missing service,
git error) is printed and the snapshot continues -- a broken step must not
hide the remaining state.

Safety: raw sqlite3 with a mode=ro URI (no Flask import -- importing app
runs db.create_all() and writes). git output is decoded explicitly as
UTF-8, never relying on the console code page, and re-encoded to ASCII
with backslash escapes so cp866/cp1251 consoles cannot mangle it.

Usage:
  python tools/state_snapshot.py --worktree C:\\transport-report --label production
  python tools/state_snapshot.py --worktree C:\\transport-report-staging --label staging
"""

import argparse
import os
import sqlite3
import subprocess
import sys

SERVICES = (
    'TransportReport',
    'TransportReportStaging',
    'TransportBot',
    'TransportBotStaging',
    'TransportBot003',
    'TransportBot003Staging',
)


def _ascii(text):
    return str(text).encode('ascii', 'backslashreplace').decode('ascii')


def _emit(text=''):
    print(_ascii(text))


def _run(cmd, cwd=None):
    """Run a command, decode stdout+stderr explicitly as UTF-8."""
    proc = subprocess.run(cmd, cwd=cwd, stdout=subprocess.PIPE,
                          stderr=subprocess.STDOUT)
    return proc.returncode, proc.stdout.decode('utf-8', 'replace').strip()


def section_git(worktree):
    try:
        code, head = _run(['git', 'rev-parse', '--short', 'HEAD'], worktree)
        _emit('HEAD          : %s' % (head if code == 0 else 'FAILED: ' + head))
    except Exception as exc:
        _emit('HEAD          : FAILED: %s' % exc)

    try:
        code, tags = _run(['git', 'tag', '--points-at', 'HEAD'], worktree)
        if code != 0:
            _emit('tags at HEAD  : FAILED: %s' % tags)
        else:
            _emit('tags at HEAD  : %s' % (', '.join(tags.splitlines()) or '(none)'))
    except Exception as exc:
        _emit('tags at HEAD  : FAILED: %s' % exc)

    _emit('behind origin/main (as of the LAST fetch -- this script does NOT '
          'run git fetch; fetch first if freshness matters):')
    try:
        code, log = _run(['git', 'log', '--oneline', 'HEAD..origin/main'],
                         worktree)
        if code != 0:
            _emit('  FAILED: %s' % log)
        elif not log:
            _emit('  (none -- HEAD is up to date with the last-fetched origin/main)')
        else:
            for line in log.splitlines():
                _emit('  ' + line)
    except Exception as exc:
        _emit('  FAILED: %s' % exc)


def section_migrations(worktree):
    db_path = os.path.join(worktree, 'instance', 'transport.db')
    _emit('database      : %s' % db_path)
    if not os.path.exists(db_path):
        _emit('  FAILED: database file not found (step skipped)')
        return
    try:
        # [REASON]: mode=ro -- a snapshot must never create or write the db.
        uri = 'file:%s?mode=ro' % db_path.replace('\\', '/')
        con = sqlite3.connect(uri, uri=True)
        try:
            cur = con.cursor()
            cur.execute("SELECT name FROM sqlite_master WHERE type='table' "
                        "AND name='schema_migrations'")
            if cur.fetchone() is None:
                _emit('  FAILED: schema_migrations table does not exist')
                return
            cur.execute('SELECT COUNT(*) FROM schema_migrations')
            _emit('schema_migrations rows: %d' % cur.fetchone()[0])
            cur.execute('SELECT name FROM schema_migrations '
                        'ORDER BY id DESC LIMIT 5')
            _emit('last five (newest first):')
            for row in cur.fetchall():
                _emit('  - %s' % row[0])
        finally:
            con.close()
    except sqlite3.Error as exc:
        _emit('  FAILED: %s' % exc)


def section_services():
    if os.name != 'nt':
        _emit('services      : skipped (not Windows, os.name=%r)' % os.name)
        return
    _emit('services (sc query):')
    for name in SERVICES:
        try:
            code, out = _run(['sc', 'query', name])
            state = '(no STATE line in sc output)'
            for line in out.splitlines():
                if 'STATE' in line:
                    state = line.strip()
                    break
            if code != 0:
                # sc prints e.g. 1060 for a service that does not exist --
                # report and continue, a missing service must not abort.
                state = 'FAILED (sc exit %d): %s' % (
                    code, out.splitlines()[-1] if out else 'no output')
            _emit('  %-26s %s' % (name, state))
        except Exception as exc:
            _emit('  %-26s FAILED: %s' % (name, exc))


def main(argv=None):
    parser = argparse.ArgumentParser(
        description='Print a pasteable state snapshot of one worktree.')
    parser.add_argument('--worktree', required=True,
                        help='path to the git worktree (e.g. C:\\transport-report)')
    parser.add_argument('--label', required=True,
                        help='free-text label for the header (e.g. production)')
    args = parser.parse_args(argv)

    if not os.path.isdir(args.worktree):
        _emit('ERROR: worktree directory not found: %s' % args.worktree)
        return 2

    _emit('=== state snapshot [%s] ===' % args.label)
    _emit('worktree      : %s' % args.worktree)
    section_git(args.worktree)
    section_migrations(args.worktree)
    section_services()
    _emit('=== end of snapshot ===')
    return 0


if __name__ == '__main__':
    sys.exit(main())
