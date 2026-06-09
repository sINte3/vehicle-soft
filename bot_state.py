"""
bot_state.py -- BOT002 Local SQLite Session State

Manages the local bot state database (instance\\bot_state.db).
Stores Telegram user <-> API token mapping.

Security rules:
- Never print or log the api_token.
- Use only stdlib sqlite3.
- All operations are idempotent.
- DB file is NOT the same as the Flask app DB.
"""

import sqlite3
import os
from datetime import datetime, timezone


# ---------------------------------------------------------------------------
# DB schema
# ---------------------------------------------------------------------------

_CREATE_TABLE_SQL = """
CREATE TABLE IF NOT EXISTS bot_sessions (
    telegram_id INTEGER PRIMARY KEY,
    api_token   TEXT    NOT NULL,
    user_id     INTEGER,
    username    TEXT    NOT NULL DEFAULT '',
    full_name   TEXT    NOT NULL DEFAULT '',
    role        TEXT    NOT NULL DEFAULT '',
    created_at  TEXT    NOT NULL,
    updated_at  TEXT    NOT NULL
);
"""

_CREATE_INDEX_SQL = """
CREATE INDEX IF NOT EXISTS idx_bot_sessions_user_id ON bot_sessions(user_id);
"""


# ---------------------------------------------------------------------------
# Public functions
# ---------------------------------------------------------------------------

def init_state_db(path: str) -> None:
    """Create the local bot state DB and tables if they do not exist.

    Safe to call multiple times (idempotent).

    Args:
        path: File path for the SQLite database (e.g. instance\\bot_state.db).
    """
    # Ensure parent directory exists
    parent = os.path.dirname(os.path.abspath(path))
    if parent and not os.path.exists(parent):
        os.makedirs(parent, exist_ok=True)

    con = sqlite3.connect(path)
    try:
        con.execute(_CREATE_TABLE_SQL)
        con.execute(_CREATE_INDEX_SQL)
        con.commit()
    finally:
        con.close()


def save_session(path: str, telegram_id: int, api_token: str, user_profile: dict) -> None:
    """Insert or replace a bot session for a Telegram user.

    Args:
        path:         Path to state DB.
        telegram_id:  Telegram user ID.
        api_token:    Raw API token (stored in DB, never logged).
        user_profile: Dict with user info (from /api/bot/me or /api/bot/link/verify).
                      Expected keys: id, username, full_name, role.
    """
    if not isinstance(telegram_id, int) or telegram_id <= 0:
        raise ValueError("telegram_id must be a positive integer")
    if not api_token or not isinstance(api_token, str):
        raise ValueError("api_token must be a non-empty string")

    now_str = datetime.now(timezone.utc).isoformat()
    user_id = user_profile.get("id")
    username = str(user_profile.get("username") or "")
    full_name = str(user_profile.get("full_name") or "")
    role = str(user_profile.get("role") or "")

    con = sqlite3.connect(path)
    try:
        con.execute("""
            INSERT INTO bot_sessions
                (telegram_id, api_token, user_id, username, full_name, role, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(telegram_id) DO UPDATE SET
                api_token  = excluded.api_token,
                user_id    = excluded.user_id,
                username   = excluded.username,
                full_name  = excluded.full_name,
                role       = excluded.role,
                updated_at = excluded.updated_at
        """, (telegram_id, api_token, user_id, username, full_name, role, now_str, now_str))
        con.commit()
    finally:
        con.close()


def get_session(path: str, telegram_id: int) -> dict | None:
    """Return session info for a Telegram user, or None if not found.

    Args:
        path:        Path to state DB.
        telegram_id: Telegram user ID.

    Returns:
        Dict with keys: telegram_id, api_token, user_id, username,
        full_name, role, created_at, updated_at.
        Returns None if no session exists.

    Security: api_token is included in the returned dict but must NOT be logged.
    """
    con = sqlite3.connect(path)
    con.row_factory = sqlite3.Row
    try:
        cur = con.execute(
            "SELECT * FROM bot_sessions WHERE telegram_id = ?",
            (telegram_id,)
        )
        row = cur.fetchone()
        if row is None:
            return None
        return dict(row)
    finally:
        con.close()


def delete_session(path: str, telegram_id: int) -> bool:
    """Delete the session for a Telegram user.

    Args:
        path:        Path to state DB.
        telegram_id: Telegram user ID.

    Returns:
        True if a session was deleted, False if no session existed.
    """
    con = sqlite3.connect(path)
    try:
        cur = con.execute(
            "DELETE FROM bot_sessions WHERE telegram_id = ?",
            (telegram_id,)
        )
        con.commit()
        return cur.rowcount > 0
    finally:
        con.close()


def list_sessions(path: str) -> list:
    """Return a list of all sessions (for admin/debugging use only).

    Each item is a dict with all session fields.
    IMPORTANT: This includes api_token -- handle with care. Never log it.

    Args:
        path: Path to state DB.

    Returns:
        List of dicts (may be empty if no sessions exist).
    """
    if not os.path.exists(path):
        return []
    con = sqlite3.connect(path)
    con.row_factory = sqlite3.Row
    try:
        cur = con.execute(
            "SELECT * FROM bot_sessions ORDER BY updated_at DESC"
        )
        rows = cur.fetchall()
        return [dict(row) for row in rows]
    finally:
        con.close()
