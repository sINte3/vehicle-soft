# -*- coding: utf-8 -*-
"""drone_collector/runlock.py -- one DJI activity at a time, across processes.

DRONE-AREA-CONTROL-V2-MEGA, block D. What is held here:

* a held lock refuses a second holder -- in this process through another
  handle, and in another process;
* the lock dies with its process: a killed holder leaves no stale "someone is
  running" behind (the reason it is an OS lock and not a PID file);
* `is_held` answers without taking the lock away from anybody and without
  creating the file it was asked about;
* the owner hint names pid and purpose while the lock is held and disappears
  with the lock -- it is a hint, never the decision;
* the wait is bounded and polls, and the cycle lock sits next to the database.

Stdlib only: the collector runs in its own venv.

Run:  python -m unittest drone_collector.tests.test_runlock
"""

import os
import subprocess
import sys
import tempfile
import unittest

from drone_collector import runlock

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(
    os.path.abspath(__file__))))

CHILD = r'''
import sys, time
from drone_collector import runlock
lock = runlock.RunLock(sys.argv[1], purpose='child-holder')
if not lock.acquire():
    print('BUSY', flush=True)
    sys.exit(3)
print('HELD', flush=True)
time.sleep(120)
'''


class Base(unittest.TestCase):

    def setUp(self):
        self._dir = tempfile.TemporaryDirectory(prefix='runlock_',
                                                ignore_cleanup_errors=True)
        self.addCleanup(self._dir.cleanup)
        self.path = os.path.join(self._dir.name, 'sub', 'x.lock')

    def lock(self, purpose='test'):
        lock = runlock.RunLock(self.path, purpose=purpose)
        self.addCleanup(lock.release)
        return lock


class InOneProcess(Base):

    def test_a_held_lock_refuses_a_second_holder_until_released(self):
        first, second = self.lock('first'), self.lock('second')
        self.assertTrue(first.acquire())
        self.assertTrue(first.held)
        self.assertFalse(second.acquire())
        self.assertFalse(second.held)
        first.release()
        self.assertTrue(second.acquire())

    def test_acquire_is_idempotent_for_the_holder(self):
        lock = self.lock()
        self.assertTrue(lock.acquire())
        self.assertTrue(lock.acquire())
        lock.release()
        lock.release()
        self.assertFalse(runlock.is_held(self.path))

    def test_is_held_follows_the_lock_and_takes_nothing_away(self):
        lock = self.lock()
        self.assertFalse(runlock.is_held(self.path))
        self.assertTrue(lock.acquire())
        self.assertTrue(runlock.is_held(self.path))
        self.assertTrue(runlock.is_held(self.path))
        # The probe did not steal it: a third party is still refused.
        self.assertFalse(self.lock('third').acquire())
        lock.release()
        self.assertFalse(runlock.is_held(self.path))

    def test_is_held_does_not_create_the_file_it_is_asked_about(self):
        self.assertFalse(runlock.is_held(self.path))
        self.assertFalse(os.path.exists(self.path))
        self.assertFalse(os.path.exists(os.path.dirname(self.path)))

    def test_the_owner_hint_lives_exactly_as_long_as_the_lock(self):
        lock = self.lock('dji-area-cycle')
        self.assertIsNone(runlock.owner(self.path))
        self.assertTrue(lock.acquire())
        hint = runlock.owner(self.path)
        self.assertEqual(hint['purpose'], 'dji-area-cycle')
        self.assertEqual(hint['pid'], os.getpid())
        self.assertIn('since_utc', hint)
        lock.release()
        self.assertIsNone(runlock.owner(self.path))

    def test_the_hint_is_not_the_decision(self):
        # A stale owner file from a dead run must not make the lock "held".
        os.makedirs(os.path.dirname(self.path))
        with open(self.path + '.owner', 'w') as fh:
            fh.write('{"pid": 1, "purpose": "stale"}')
        self.assertFalse(runlock.is_held(self.path))
        self.assertTrue(self.lock().acquire())

    def test_the_context_manager_raises_busy_instead_of_running(self):
        self.assertTrue(self.lock('holder').acquire())
        with self.assertRaises(runlock.LockBusy):
            with runlock.RunLock(self.path):
                self.fail('the body ran without the lock')

    def test_the_context_manager_releases_on_the_way_out(self):
        with runlock.RunLock(self.path) as lock:
            self.assertTrue(lock.held)
            self.assertTrue(runlock.is_held(self.path))
        self.assertFalse(runlock.is_held(self.path))


class TheWait(Base):

    def test_the_wait_polls_and_gives_up_at_the_deadline(self):
        self.assertTrue(self.lock('holder').acquire())
        now = [100.0]
        sleeps = []

        def sleep(seconds):
            sleeps.append(seconds)
            now[0] += seconds

        waiter = self.lock('waiter')
        self.assertFalse(waiter.acquire(wait_s=10, poll_s=4, sleep_fn=sleep,
                                        clock=lambda: now[0]))
        # Never sleeps past the deadline: 4 + 4 + 2.
        self.assertEqual(sleeps, [4, 4, 2])
        self.assertFalse(waiter.held)

    def test_a_zero_wait_never_sleeps(self):
        self.assertTrue(self.lock('holder').acquire())
        sleeps = []
        self.assertFalse(self.lock('waiter').acquire(wait_s=0,
                                                     sleep_fn=sleeps.append))
        self.assertEqual(sleeps, [])

    def test_a_lock_freed_during_the_wait_is_taken(self):
        holder = self.lock('holder')
        self.assertTrue(holder.acquire())

        def sleep(_seconds):
            holder.release()

        waiter = self.lock('waiter')
        self.assertTrue(waiter.acquire(wait_s=30, poll_s=1, sleep_fn=sleep))
        self.assertEqual(runlock.owner(self.path)['purpose'], 'waiter')


class AcrossProcesses(Base):
    """The property the module exists for: the OS owns the lock."""

    def start_child(self):
        env = dict(os.environ)
        env['PYTHONPATH'] = REPO_ROOT + os.pathsep + env.get('PYTHONPATH', '')
        child = subprocess.Popen([sys.executable, '-c', CHILD, self.path],
                                 cwd=REPO_ROOT, env=env,
                                 stdout=subprocess.PIPE,
                                 stderr=subprocess.PIPE,
                                 universal_newlines=True)
        self.addCleanup(self._reap, child)
        line = child.stdout.readline().strip()
        self.assertEqual(line, 'HELD', child.stderr.read()
                         if child.poll() is not None else line)
        return child

    @staticmethod
    def _reap(child):
        if child.poll() is None:
            child.kill()
        child.wait(timeout=30)
        for stream in (child.stdout, child.stderr):
            if stream:
                stream.close()

    def test_another_process_holding_it_refuses_this_one(self):
        self.start_child()
        self.assertTrue(runlock.is_held(self.path))
        self.assertFalse(self.lock().acquire())
        self.assertEqual(runlock.owner(self.path)['purpose'], 'child-holder')

    def test_a_killed_holder_leaves_no_stale_lock(self):
        child = self.start_child()
        self.assertFalse(self.lock('before').acquire())
        child.kill()
        child.wait(timeout=30)
        # [REASON]: Windows frees the locks of a dead process "as resources
        # allow", not synchronously with the kill -- hence a short wait, not
        # an immediate attempt. A PID file would stay "held" forever here.
        after = self.lock('after')
        self.assertTrue(after.acquire(wait_s=15, poll_s=0.05))
        self.assertEqual(runlock.owner(self.path)['purpose'], 'after')


class Paths(unittest.TestCase):

    def test_the_cycle_lock_sits_next_to_the_database(self):
        db = os.path.join(tempfile.gettempdir(), 'inst', 'transport.db')
        self.assertEqual(runlock.cycle_lock_path(db, '/elsewhere'),
                         os.path.join(os.path.dirname(os.path.abspath(db)),
                                      runlock.CYCLE_LOCK_NAME))

    def test_without_a_database_it_sits_in_the_work_directory(self):
        work = os.path.join(tempfile.gettempdir(), 'area_daily')
        self.assertEqual(runlock.cycle_lock_path(None, work),
                         os.path.join(os.path.abspath(work),
                                      runlock.CYCLE_LOCK_NAME))

    def test_the_two_lock_names(self):
        self.assertEqual(runlock.COLLECTOR_LOCK_NAME, 'collector.lock')
        self.assertEqual(runlock.CYCLE_LOCK_NAME, 'dji_area_cycle.lock')


if __name__ == '__main__':
    unittest.main()
