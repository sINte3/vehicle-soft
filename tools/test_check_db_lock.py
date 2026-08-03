#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Tests for tools/check_db_lock.py -- TOOL-DBLOCK-WAL-001.

The pair of assertions "non-zero while another process holds the database /
zero when it is free" is the whole point. The defect being fixed is that the
previous version passed BOTH cases, so a test that only exercises the happy
path repeats the defect instead of catching it.

The holder is a SEPARATE PROCESS, not a second connection in the same
interpreter: the real scenario is a Windows service, and same-process
behaviour differs (SQLite shares the cache and the locks differently).

Run:
  python tools/test_check_db_lock.py
"""

import os
import shutil
import sqlite3
import subprocess
import sys
import tempfile
import textwrap
import time
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
TOOL = os.path.join(HERE, 'check_db_lock.py')

EXIT_CLEAN = 0
EXIT_ERROR = 1
EXIT_HELD = 2
EXIT_STALE = 3

# [REASON]: the holder must reach WAL mode and touch the database before it
# reports HOLDING, otherwise the test races the probe against a connection
# that has not created -shm yet and the "held" assertion goes flaky.
#
# [REASON]: it then blocks on stdin instead of sleeping, so the test can end
# it two different ways. Closing stdin makes it close the connection and exit
# normally -- SQLite removes -wal/-shm, which is what stopping a service via
# NSSM looks like. kill() skips that and leaves the sidecars behind. Signals
# cannot express the difference: on Windows terminate() IS TerminateProcess,
# so terminate() and kill() are the same call and neither closes the database.
HOLDER_SOURCE = textwrap.dedent('''
    import sqlite3, sys
    con = sqlite3.connect(sys.argv[1])
    con.execute('PRAGMA journal_mode=WAL')
    con.execute('SELECT count(*) FROM t').fetchone()
    print('HOLDING', flush=True)
    sys.stdin.readline()
    con.close()
''')


def run_tool(db_path):
    """Run the tool as a subprocess; return (returncode, stdout+stderr)."""
    proc = subprocess.run(
        [sys.executable, TOOL, '--db', db_path, '--busy-timeout-ms', '750'],
        stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
    return proc.returncode, proc.stdout


class CheckDbLockTests(unittest.TestCase):

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix='check_db_lock_test_')
        self.db = os.path.join(self.tmp, 'transport.db')
        self.holder_py = os.path.join(self.tmp, 'holder.py')
        with open(self.holder_py, 'w', encoding='ascii') as fh:
            fh.write(HOLDER_SOURCE)
        con = sqlite3.connect(self.db)
        con.execute('PRAGMA journal_mode=WAL')
        con.execute('CREATE TABLE t (a INTEGER)')
        con.execute('INSERT INTO t VALUES (1)')
        con.commit()
        con.close()
        self.holder = None

    def tearDown(self):
        self.stop_holder()
        shutil.rmtree(self.tmp, ignore_errors=True)

    def start_holder(self):
        self.holder = subprocess.Popen(
            [sys.executable, self.holder_py, self.db],
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, text=True)
        line = self.holder.stdout.readline().strip()
        self.assertEqual(line, 'HOLDING', 'holder process failed to start')
        return self.holder

    def _reap(self):
        if self.holder is not None:
            for pipe in (self.holder.stdin, self.holder.stdout):
                if pipe is not None and not pipe.closed:
                    pipe.close()
        self.holder = None

    def stop_holder_gracefully(self):
        """Close the connection the way a stopped service would."""
        self.holder.stdin.write('\n')
        self.holder.stdin.flush()
        self.holder.wait(timeout=15)
        self._reap()

    def stop_holder(self):
        if self.holder is not None and self.holder.poll() is None:
            self.holder.kill()
            self.holder.wait()
        self._reap()

    # -- the discriminating pair ------------------------------------------

    def test_fails_while_another_process_holds_the_database(self):
        self.start_holder()
        code, out = run_tool(self.db)
        self.assertEqual(code, EXIT_HELD,
                         'tool must exit HELD while a holder is alive:\n' + out)
        self.assertIn('HELD', out)
        self.assertIn('DO NOT MIGRATE', out)

    def test_passes_once_the_holder_is_gone(self):
        self.start_holder()
        # [REASON]: a graceful close is what makes SQLite remove -wal/-shm.
        # That is the "services were stopped properly" path and it must end
        # CLEAN, not STALE.
        self.stop_holder_gracefully()
        code, out = run_tool(self.db)
        self.assertEqual(code, EXIT_CLEAN,
                         'tool must exit CLEAN once the db is free:\n' + out)
        self.assertIn('Safe to run migrations', out)

    def test_held_and_free_verdicts_actually_differ(self):
        """Negative control: the two runs must not print the same thing.

        This is the assertion the previous tool would fail -- it printed the
        identical green verdict in both states.
        """
        self.start_holder()
        held_code, held_out = run_tool(self.db)
        self.stop_holder_gracefully()
        free_code, free_out = run_tool(self.db)
        self.assertEqual(held_code, EXIT_HELD, held_out)
        self.assertEqual(free_code, EXIT_CLEAN, free_out)
        self.assertNotEqual(held_out, free_out)

    # -- the stale state ---------------------------------------------------

    def test_stale_artefacts_are_reported_separately(self):
        """kill() leaves -wal/-shm behind with no live holder: outcome 3."""
        holder = self.start_holder()
        # [REASON]: kill() is TerminateProcess on Windows and SIGKILL on
        # POSIX -- no cleanup runs, so SQLite never removes the sidecars.
        # That reproduces an unclean shutdown deterministically.
        holder.kill()
        holder.wait()
        self._reap()
        time.sleep(0.5)
        if not os.path.exists(self.db + '-shm'):
            # [REASON]: skip rather than fail. Leaving -shm behind after a
            # hard kill is SQLite/OS behaviour, not something this tool
            # controls; on a platform or filesystem where it does not hold
            # there is no stale state to test and a red CI run would say
            # nothing true about the tool.
            self.skipTest('platform does not leave -shm after a hard kill; '
                          'the stale state cannot be produced here')
        code, out = run_tool(self.db)
        self.assertEqual(code, EXIT_STALE,
                         'leftover artefacts with no holder must be STALE:\n'
                         + out)
        self.assertIn('STALE', out)

    def test_stale_is_distinct_from_held_and_from_clean(self):
        self.assertNotEqual(EXIT_STALE, EXIT_HELD)
        self.assertNotEqual(EXIT_STALE, EXIT_CLEAN)
        self.assertNotEqual(EXIT_STALE, EXIT_ERROR)

    # -- preserved behaviour ----------------------------------------------

    def test_missing_database_fails_and_creates_no_file(self):
        missing = os.path.join(self.tmp, 'does_not_exist.db')
        code, out = run_tool(missing)
        self.assertEqual(code, EXIT_ERROR)
        self.assertIn('ERROR', out)
        self.assertFalse(os.path.exists(missing),
                         'the tool must never create the database')

    def test_missing_database_exit_code_differs_from_held(self):
        self.assertNotEqual(EXIT_ERROR, EXIT_HELD)

    def test_clean_run_leaves_no_artefacts_behind(self):
        """A clean run must not make the NEXT run report STALE."""
        code, out = run_tool(self.db)
        self.assertEqual(code, EXIT_CLEAN, out)
        self.assertFalse(os.path.exists(self.db + '-wal'), out)
        self.assertFalse(os.path.exists(self.db + '-shm'), out)
        code2, out2 = run_tool(self.db)
        self.assertEqual(code2, EXIT_CLEAN, out2)

    def test_output_is_ascii_only(self):
        """NSSM/Windows log encoding mangles anything else."""
        outputs = []
        holder = self.start_holder()
        outputs.append(run_tool(self.db)[1])          # held
        holder.kill()
        holder.wait()
        self._reap()
        time.sleep(0.5)
        outputs.append(run_tool(self.db)[1])          # stale
        outputs.append(run_tool(os.path.join(self.tmp, 'nope.db'))[1])  # error
        for text in outputs:
            text.encode('ascii')

    def test_tool_never_writes_to_a_clean_database(self):
        before = os.path.getsize(self.db)
        with open(self.db, 'rb') as fh:
            payload = fh.read()
        code, out = run_tool(self.db)
        self.assertEqual(code, EXIT_CLEAN, out)
        self.assertEqual(os.path.getsize(self.db), before)
        with open(self.db, 'rb') as fh:
            self.assertEqual(fh.read(), payload,
                             'the tool must not modify the database')


if __name__ == '__main__':
    unittest.main(verbosity=2)
