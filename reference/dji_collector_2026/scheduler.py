"""
Long-running scheduler process. Runs under NSSM as a Windows service.

Schedule (Asia/Tashkent, UTC+5):
  - Every 2 hours, on the hour, 24/7  -> incremental sync (last 7 days)
  - Daily at 03:00                    -> full re-sync (last 30 days)
  - Daily at 03:30                    -> SQLite online backup of data/app.db
  - On startup                        -> one immediate incremental sync
                                          so you can verify the service
                                          works without waiting 2 hours.

Stop with Ctrl+C (during dev) or `nssm stop DjiAgrasCollector` (in prod).
"""
from __future__ import annotations

import logging
import signal
import sys
import time

from apscheduler.schedulers.blocking import BlockingScheduler
from apscheduler.triggers.cron import CronTrigger

from collector.incremental import run_incremental
from collector.logging_setup import setup_logging
from collector.backup import run_backup

log = logging.getLogger(__name__)

TZ = "Asia/Tashkent"


def job_incremental():
    log.info("=== job: incremental (7d) ===")
    try:
        run_incremental(days=7)
    except Exception:
        log.exception("incremental job crashed")


def job_full_30d():
    log.info("=== job: full re-sync (30d) ===")
    try:
        run_incremental(days=30)
    except Exception:
        log.exception("full 30d job crashed")


def job_backup_db():
    log.info("=== job: db backup ===")
    try:
        run_backup()
    except Exception:
        log.exception("backup job crashed")


def main():
    setup_logging("scheduler")
    log.info("Scheduler starting up")

    sched = BlockingScheduler(timezone=TZ)

    # Every 2 hours on the hour, 24/7
    sched.add_job(
        job_incremental,
        CronTrigger(hour="*/2", minute=0, timezone=TZ),
        id="incremental_2h",
        max_instances=1,
        coalesce=True,
        misfire_grace_time=600,
    )

    # Nightly full re-sync at 03:00
    sched.add_job(
        job_full_30d,
        CronTrigger(hour=3, minute=0, timezone=TZ),
        id="full_30d_nightly",
        max_instances=1,
        coalesce=True,
        misfire_grace_time=3600,
    )

    # Nightly SQLite backup at 03:30 (after the full re-sync has settled)
    sched.add_job(
        job_backup_db,
        CronTrigger(hour=3, minute=30, timezone=TZ),
        id="backup_db_nightly",
        max_instances=1,
        coalesce=True,
        misfire_grace_time=3600,
    )

    # Run one immediate sync on startup so the operator can verify it works
    sched.add_job(
        job_incremental,
        "date",
        id="startup_sync",
    )

    # Graceful shutdown on Ctrl+C / SIGTERM (NSSM sends CTRL_BREAK)
    def _graceful_shutdown(signum, frame):
        log.info("Signal %s received, shutting down scheduler", signum)
        sched.shutdown(wait=False)
        sys.exit(0)

    signal.signal(signal.SIGINT, _graceful_shutdown)
    signal.signal(signal.SIGTERM, _graceful_shutdown)

    log.info("Scheduler ready. Jobs registered:")
    for j in sched.get_jobs():
        log.info("  %s -> %s", j.id, j.trigger)

    try:
        sched.start()
    except (KeyboardInterrupt, SystemExit):
        log.info("Scheduler stopped")


if __name__ == "__main__":
    main()
