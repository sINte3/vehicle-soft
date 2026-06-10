"""
bot003_notifications.py - BOT003 Best-Effort Notification Enqueue Module

Isolated notification outbox enqueue for spare part request events.
This module is designed to NEVER poison the main SQLAlchemy session.

Key design rules:
1. All public functions are best-effort and never raise.
2. All errors are caught and logged as warnings.
3. Uses an independent sqlite3 connection (never the caller's db.session).
4. If bot003_notification_outbox table does not exist, logs a warning and returns.
5. Never calls db.session.rollback() on the main application session.
"""

import json
import logging
import sqlite3
import os
from datetime import datetime

logger = logging.getLogger("bot003_notifications")


def _get_db_path(app=None):
    """Resolve the SQLite database path from Flask app config or default path.

    Uses a separate sqlite3 connection - never the Flask-SQLAlchemy session.
    """
    if app:
        try:
            uri = app.config.get("SQLALCHEMY_DATABASE_URI", "")
            if uri and uri.startswith("sqlite:///"):
                # sqlite:///C:/path/to/instance/transport.db
                path = uri[len("sqlite:///"):]
                if path:
                    return path
        except Exception:
            pass
    # Fallback: resolve relative to this file's location
    base_dir = os.path.dirname(os.path.abspath(__file__))
    return os.path.join(base_dir, "instance", "transport.db")


def _outbox_table_exists(cursor):
    """Check if bot003_notification_outbox table exists."""
    cursor.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name=?",
        ("bot003_notification_outbox",)
    )
    return cursor.fetchone() is not None


def _make_dedupe_key(event_type, request_id, target_user_id=None, target_telegram_id=None):
    """Create a unique dedupe key for an event to prevent duplicate inserts.

    Format: bot003_{event_type}_{request_id}_{target}
    The target suffix ensures each recipient gets a unique notification row,
    even when the same event should notify multiple admins.

    Uses target_user_id when available, falling back to target_telegram_id,
    falling back to '0' (should not happen for valid notifications).
    """
    target = target_user_id or target_telegram_id or 0
    return "bot003_{}_{}_{}".format(event_type, request_id, target)


def _get_admin_telegram_ids(cursor):
    """Query admin users who have telegram_id set.

    Returns a list of dicts with user_id and telegram_id.
    """
    cursor.execute(
        "SELECT id, telegram_id FROM users WHERE telegram_id IS NOT NULL AND is_active_user = 1 AND role = 'admin'"
    )
    rows = cursor.fetchall()
    return [{"user_id": row[0], "telegram_id": str(row[1])} for row in rows]


def _get_user_telegram_id(cursor, user_id):
    """Get telegram_id for a specific user, or None."""
    cursor.execute(
        "SELECT telegram_id FROM users WHERE id = ? AND is_active_user = 1",
        (user_id,)
    )
    row = cursor.fetchone()
    if row and row[0]:
        return {"user_id": user_id, "telegram_id": str(row[0])}
    return None


def _build_notification_payload(event_type, request_id, extra=None):
    """Build a JSON payload for the notification outbox.

    Args:
        event_type: The type of event (e.g., 'spare_request_submitted').
        request_id: The spare part request ID.
        extra: Optional dict with additional fields (request_number, organization_name, etc.).

    Returns:
        A JSON string payload.
    """
    payload = {
        "event_type": event_type,
        "request_id": request_id,
    }
    if extra:
        # Only include known safe fields - avoid leaking sensitive data
        allowed_keys = [
            "request_number", "organization_name", "equipment_name",
            "created_by_name", "status", "created_at", "updated_at",
        ]
        for key in allowed_keys:
            if key in extra and extra[key] is not None:
                payload[key] = extra[key]
    return json.dumps(payload, ensure_ascii=False, default=str)


def _build_payload_for_request(cursor, event_type, request_id):
    """Build a notification payload enriched with available request data.

    Queries the main database to gather request details.
    This is a read-only query that cannot poison any write session.
    """
    payload = {
        "event_type": event_type,
        "request_id": request_id,
    }
    try:
        # Look up request details
        cursor.execute("""
            SELECT spr.status, spr.created_at, spr.request_date,
                   u.username, u.full_name,
                   org.name as org_name,
                   eq.name as eq_name
            FROM spare_part_requests spr
            LEFT JOIN users u ON u.id = spr.created_by
            LEFT JOIN organizations org ON org.id = spr.organization_id
            LEFT JOIN equipment eq ON eq.id = spr.equipment_id
            WHERE spr.id = ?
        """, (request_id,))
        row = cursor.fetchone()
        if row:
            if row[0]:
                payload["status"] = row[0]
            if row[1]:
                payload["created_at"] = row[1]
            if row[4]:
                payload["created_by_name"] = row[4]
            elif row[3]:
                payload["created_by_name"] = row[3]
            if row[5]:
                payload["organization_name"] = row[5]
            if row[6]:
                payload["equipment_name"] = row[6]
    except Exception:
        # Best-effort for payload enrichment - use basic payload if query fails
        pass

    return json.dumps(payload, ensure_ascii=False, default=str)


def _enqueue_best_effort(event_type, request_id, target_user_id, target_telegram_id,
                          payload_json, app=None):
    """Core enqueue function - inserts a row into bot003_notification_outbox.

    This function:
    - Opens its own sqlite3 connection
    - Never raises (catches all exceptions)
    - Uses dedupe_key for idempotent inserts

    Args:
        event_type: Notification event type string.
        request_id: Spare part request ID.
        target_user_id: User ID to notify (int or None).
        target_telegram_id: Telegram chat/user ID to send to (str or None).
        payload_json: JSON string payload.
        app: Optional Flask app (used to resolve DB path).

    Returns:
        True if enqueued successfully, False otherwise.
    """
    db_path = _get_db_path(app)
    now = datetime.utcnow().isoformat()
    dedupe_key = _make_dedupe_key(event_type, request_id, target_user_id, target_telegram_id)

    try:
        conn = sqlite3.connect(db_path)
        cursor = conn.cursor()

        # Check if table exists - if not, this is a non-fatal warning
        if not _outbox_table_exists(cursor):
            logger.warning(
                "bot003_notification_outbox table does not exist. "
                "Notification not enqueued for event_type=%s request_id=%s. "
                "Run migrate_bot003_outbox_v1.py to create the table.",
                event_type, request_id
            )
            conn.close()
            return False

        # Check for duplicate dedupe_key - ignore safely
        cursor.execute(
            "SELECT id FROM bot003_notification_outbox WHERE dedupe_key = ?",
            (dedupe_key,)
        )
        if cursor.fetchone():
            logger.info(
                "Duplicate dedupe_key for event_type=%s request_id=%s target=%s - skipping.",
                event_type, request_id, target_user_id or target_telegram_id
            )
            conn.close()
            return True

        # Insert the notification row
        cursor.execute("""
            INSERT INTO bot003_notification_outbox
                (event_type, request_id, target_user_id, target_telegram_id,
                 payload_json, dedupe_key, status, attempts, max_attempts,
                 available_at, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, 'pending', 0, 5, ?, ?, ?)
        """, (
            event_type,
            request_id,
            target_user_id,
            target_telegram_id,
            payload_json,
            dedupe_key,
            now,   # available_at = now, send immediately
            now,   # created_at
            now,   # updated_at
        ))

        conn.commit()
        conn.close()
        logger.info(
            "Notification enqueued: event_type=%s request_id=%s target_user_id=%s",
            event_type, request_id, target_user_id
        )
        return True

    except Exception:
        logger.exception(
            "Failed to enqueue notification for event_type=%s request_id=%s: ",
            event_type, request_id
        )
        try:
            conn.close()
        except Exception:
            pass
        return False


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def is_bot003_outbox_ready(app=None) -> bool:
    """Check whether the bot003_notification_outbox table exists.

    This is a safe check that does not modify any data.
    Returns True if the table exists, False otherwise (or on error).
    """
    try:
        db_path = _get_db_path(app)
        if not os.path.exists(db_path):
            return False
        conn = sqlite3.connect(db_path)
        cursor = conn.cursor()
        exists = _outbox_table_exists(cursor)
        conn.close()
        return bool(exists)
    except Exception:
        logger.warning("Failed to check bot003_outbox readiness", exc_info=True)
        return False


def enqueue_spare_request_submitted_best_effort(request_id: int, app=None) -> None:
    """Enqueue a notification for a newly submitted spare part request.

    Notifies all admin users who have linked their Telegram account.

    This function:
    - Runs entirely in its own sqlite3 connection.
    - Never raises - all errors are caught and logged.
    - Does not touch the main Flask-SQLAlchemy session.

    Args:
        request_id: The ID of the spare part request that was submitted.
        app: Optional Flask application instance (used for DB path resolution).
    """
    if not request_id:
        logger.warning("enqueue_spare_request_submitted_best_effort called with null request_id")
        return

    db_path = _get_db_path(app)

    try:
        conn = sqlite3.connect(db_path)
        cursor = conn.cursor()

        # Check table exists
        if not _outbox_table_exists(cursor):
            logger.warning(
                "bot003_notification_outbox missing - cannot enqueue "
                "spare_request_submitted for request_id=%s", request_id
            )
            conn.close()
            return

        # Find admin users with telegram_id
        admin_recipients = _get_admin_telegram_ids(cursor)
        if not admin_recipients:
            logger.info(
                "No admin users with telegram_id found - "
                "skipping notification for spare_request_submitted request_id=%s",
                request_id
            )
            conn.close()
            return

        # Build enriched payload
        payload_json = _build_payload_for_request(
            cursor, "spare_request_submitted", request_id
        )
        conn.close()

        # Enqueue one notification per admin
        for recipient in admin_recipients:
            _enqueue_best_effort(
                event_type="spare_request_submitted",
                request_id=request_id,
                target_user_id=recipient["user_id"],
                target_telegram_id=recipient["telegram_id"],
                payload_json=payload_json,
                app=app,
            )

    except Exception:
        logger.exception(
            "Failed to enqueue spare_request_submitted for request_id=%s: ",
            request_id
        )


def enqueue_spare_request_status_best_effort(request_id: int, event_type: str,
                                              app=None) -> None:
    """Enqueue a notification for a spare part request status change.

    Notifies the request creator if they have linked their Telegram account.

    This function:
    - Runs entirely in its own sqlite3 connection.
    - Never raises - all errors are caught and logged.
    - Does not touch the main Flask-SQLAlchemy session.

    Args:
        request_id: The ID of the spare part request whose status changed.
        event_type: The event type string (e.g., 'spare_request_approved').
        app: Optional Flask application instance (used for DB path resolution).
    """
    if not request_id or not event_type:
        logger.warning("enqueue_spare_request_status_best_effort called with invalid args")
        return

    valid_event_types = {
        "spare_request_approved",
        "spare_request_rejected",
        "spare_request_revision_requested",
    }
    if event_type not in valid_event_types:
        logger.warning(
            "Unknown event_type=%s for request_id=%s - skipping.",
            event_type, request_id
        )
        return

    db_path = _get_db_path(app)

    try:
        conn = sqlite3.connect(db_path)
        cursor = conn.cursor()

        # Check table exists
        if not _outbox_table_exists(cursor):
            logger.warning(
                "bot003_notification_outbox missing - cannot enqueue "
                "%s for request_id=%s", event_type, request_id
            )
            conn.close()
            return

        # Find the request creator
        cursor.execute(
            "SELECT created_by FROM spare_part_requests WHERE id = ?",
            (request_id,)
        )
        row = cursor.fetchone()
        if not row or not row[0]:
            logger.info(
                "No creator found for request_id=%s - "
                "skipping %s notification", request_id, event_type
            )
            conn.close()
            return

        creator_id = row[0]

        # Check if creator has telegram_id
        recipient = _get_user_telegram_id(cursor, creator_id)
        if not recipient:
            logger.info(
                "Creator (user_id=%s) has no telegram_id - "
                "skipping %s notification for request_id=%s",
                creator_id, event_type, request_id
            )
            conn.close()
            return

        # Build enriched payload
        payload_json = _build_payload_for_request(cursor, event_type, request_id)
        conn.close()

        # Enqueue notification
        _enqueue_best_effort(
            event_type=event_type,
            request_id=request_id,
            target_user_id=recipient["user_id"],
            target_telegram_id=recipient["telegram_id"],
            payload_json=payload_json,
            app=app,
        )

    except Exception:
        logger.exception(
            "Failed to enqueue %s for request_id=%s: ",
            event_type, request_id
        )
