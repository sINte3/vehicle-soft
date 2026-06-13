"""
sqlite_runtime.py - SQLite runtime connection helpers for FIX002.

Provides safe reusable functions to configure SQLite connections with
runtime PRAGMAs for multi-process WAL access:

- busy_timeout=30000
- journal_mode=WAL
- synchronous=NORMAL
- wal_autocheckpoint=1000

Design rules:
- Never force PRAGMA foreign_keys=ON in FIX002.
- Safe to call multiple times.
- Log warnings rather than failing application startup on transient PRAGMA errors.
"""

import logging
import os
import sqlite3

logger = logging.getLogger("sqlite_runtime")


def configure_sqlite_connection(conn, busy_timeout_ms=30000):
    """Apply runtime PRAGMAs to an open sqlite3 connection."""
    try:
        conn.execute("PRAGMA busy_timeout=%d" % busy_timeout_ms)
    except Exception:
        logger.warning("Failed to set SQLite busy_timeout", exc_info=True)

    try:
        conn.execute("PRAGMA synchronous=NORMAL")
    except Exception:
        logger.warning("Failed to set SQLite synchronous=NORMAL", exc_info=True)

    try:
        conn.execute("PRAGMA wal_autocheckpoint=1000")
    except Exception:
        logger.warning("Failed to set SQLite wal_autocheckpoint", exc_info=True)

    return conn


def enable_sqlite_wal(db_path, busy_timeout_ms=30000):
    """Enable WAL mode for a SQLite database path.

    Returns True only when SQLite confirms journal_mode=wal.
    """
    if not os.path.exists(db_path):
        logger.warning("enable_sqlite_wal: database not found at %s", db_path)
        return False

    conn = None
    try:
        conn = sqlite3.connect(db_path, timeout=30)
        row = conn.execute("PRAGMA journal_mode=WAL").fetchone()
        configure_sqlite_connection(conn, busy_timeout_ms)
        mode = (row[0] if row else "").lower()
        return mode == "wal"
    except Exception:
        logger.exception("enable_sqlite_wal: failed for %s", db_path)
        return False
    finally:
        if conn is not None:
            conn.close()


def open_connection(db_path, busy_timeout_ms=30000):
    """Open a sqlite3 connection with FIX002 runtime PRAGMAs applied.

    This is used by runtime raw-sqlite modules such as BOT003 enqueue and worker.
    journal_mode=WAL is attempted first and treated as best-effort.
    """
    try:
        conn = sqlite3.connect(db_path, timeout=30)
    except Exception:
        logger.exception("open_connection: failed for %s", db_path)
        return None

    try:
        conn.execute("PRAGMA journal_mode=WAL")
    except Exception:
        logger.warning("open_connection: failed to ensure WAL for %s", db_path, exc_info=True)

    configure_sqlite_connection(conn, busy_timeout_ms)
    return conn
