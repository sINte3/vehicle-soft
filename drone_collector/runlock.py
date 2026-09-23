# -*- coding: utf-8 -*-
"""drone_collector/runlock.py -- one DJI activity at a time, across processes.

DRONE-AREA-CONTROL-V2-MEGA, block D. Stdlib only: the collector runs in its
own venv and must not pull application dependencies, and the application
imports this module under its own interpreter (the same rule by which
`dji_area` imports `drone_collector.route_decode`).

What it guards:

* the COLLECTOR lock (`drone_collector/data/collector.lock`) -- held by every
  `drone_collector.main` run that opens the DJI cabinet or the file outbox.
  Two browsers on one saved DJI session, or two drains racing on `mark_sent`
  and `sweep_stale_temp()`, are exactly what must not happen;
* the CYCLE lock (`<instance>/dji_area_cycle.lock`, next to the database) --
  held by `tools/dji_area_daily.py` for the whole FLIGHTS -> MANIFEST ->
  SOURCES -> RECALC cycle, whether it was started by the scheduler, by the
  "refresh DJI data now" button or by the backfill tool. Two cycles would
  share one work directory and delete each other's manifest files.

[REASON]: an OS lock on an open handle, not a PID file or O_EXCL. The
operating system releases the lock when the process dies -- killed by the
service manager, by a reboot, by an unhandled exception -- so a crash can
never leave a stale "someone is running" marker that blocks every later run.
`msvcrt.locking` on Windows, `fcntl.flock` elsewhere.

The owner file next to the lock (`<lock>.owner`, JSON) is only a hint for
people: pid, host, purpose and start time. Whether the lock is held is
decided by trying the lock itself, never by reading that file.
"""

import io
import json
import os
import socket
import time
from datetime import datetime

try:  # pragma: no cover - platform branch
    import msvcrt
except ImportError:  # pragma: no cover - platform branch
    msvcrt = None
try:  # pragma: no cover - platform branch
    import fcntl
except ImportError:  # pragma: no cover - platform branch
    fcntl = None

COLLECTOR_LOCK_NAME = 'collector.lock'
CYCLE_LOCK_NAME = 'dji_area_cycle.lock'


class LockBusy(RuntimeError):
    """The lock is held by another process and the wait ran out."""


def _try_lock(handle):
    """Take the lock without waiting. True on success."""
    handle.seek(0)
    try:
        if msvcrt is not None:
            msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
        else:
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        return True
    except OSError:
        return False


def _unlock(handle):
    handle.seek(0)
    try:
        if msvcrt is not None:
            msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
        else:
            fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
    except OSError:
        pass


class RunLock(object):
    """An exclusive, non-reentrant, cross-process lock on one file."""

    def __init__(self, path, purpose=''):
        self.path = os.path.abspath(path)
        self.purpose = purpose
        self._handle = None

    @property
    def held(self):
        return self._handle is not None

    def acquire(self, wait_s=0.0, poll_s=1.0, sleep_fn=time.sleep,
                clock=time.monotonic):
        """Take the lock, waiting up to ``wait_s`` seconds. True if taken."""
        if self._handle is not None:
            return True
        directory = os.path.dirname(self.path)
        if directory:
            os.makedirs(directory, exist_ok=True)
        # 'a+' creates the file without truncating a lock someone else holds.
        handle = io.open(self.path, 'a+b')
        deadline = clock() + max(0.0, float(wait_s or 0))
        while True:
            if _try_lock(handle):
                self._handle = handle
                self._write_owner()
                return True
            remaining = deadline - clock()
            if remaining <= 0:
                handle.close()
                return False
            sleep_fn(min(poll_s, remaining))

    def release(self):
        if self._handle is None:
            return
        # [REASON]: the owner hint goes first, while the lock is still held,
        # so a reader never sees "free" next to a fresh owner record.
        try:
            os.remove(self.path + '.owner')
        except OSError:
            pass
        _unlock(self._handle)
        self._handle.close()
        self._handle = None

    def _write_owner(self):
        info = {'pid': os.getpid(), 'host': socket.gethostname(),
                'purpose': self.purpose,
                'since_utc': datetime.utcnow().replace(
                    microsecond=0).isoformat()}
        tmp = self.path + '.owner.tmp'
        try:
            with io.open(tmp, 'w', encoding='utf-8') as fh:
                json.dump(info, fh)
            os.replace(tmp, self.path + '.owner')
        except OSError:
            pass

    def __enter__(self):
        if not self.acquire():
            raise LockBusy(self.path)
        return self

    def __exit__(self, *exc):
        self.release()
        return False


def is_held(path):
    """Is the lock held by SOMEONE right now? Never blocks.

    Taking and immediately releasing the lock is the only honest answer; a
    process that races us for the lock in that instant loses one attempt and
    retries (every acquirer here polls).
    """
    if not os.path.exists(path):
        return False
    probe = RunLock(path, purpose='probe')
    try:
        handle = io.open(probe.path, 'a+b')
    except OSError:
        return False
    try:
        if _try_lock(handle):
            _unlock(handle)
            return False
        return True
    finally:
        handle.close()


def owner(path):
    """The owner hint (dict) or None. Informational only."""
    try:
        with io.open(path + '.owner', encoding='utf-8') as fh:
            data = json.load(fh)
        return data if isinstance(data, dict) else None
    except (OSError, ValueError):
        return None


def cycle_lock_path(db_path=None, work_dir=None):
    """Where the cycle lock lives: next to the database, else in the work dir.

    [REASON]: next to the database, because the database is what the cycle
    ends in (RECALC) and what the web process can find without any extra
    configuration. The collector-only half of the two-host topology has no
    database, so it locks its work directory instead.
    """
    if db_path:
        return os.path.join(os.path.dirname(os.path.abspath(db_path)),
                            CYCLE_LOCK_NAME)
    return os.path.join(os.path.abspath(work_dir or '.'), CYCLE_LOCK_NAME)
