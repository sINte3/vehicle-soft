"""
bot003_outbox_worker.py — BOT003 Outbox Worker for Telegram Notifications

Reads pending rows from bot003_notification_outbox and sends them via the
existing Telegram bot infrastructure. This module is designed to:
- Never crash the entire bot runner on a single bad notification.
- Be safe if the table does not exist.
- Be safe if the Telegram bot token is not configured.
- Keep BOT002B stable (does not modify bot.py or existing commands).

Usage (standalone):
    "C:\\Program Files\\Python314\\python.exe" bot003_outbox_worker.py

Usage (called from bot.py polling loop, future integration):
    from bot003_outbox_worker import process_outbox
    process_outbox(bot_app, app)
"""

import json
import logging
import os
import sqlite3
import sys
from datetime import datetime, timedelta

logger = logging.getLogger("bot003_outbox_worker")

# Module-level flag: set by the caller if the BotNotificationQueue table exists
_BOT003_OUTBOX_AVAILABLE = False


def _get_db_path():
    """Resolve SQLite database path relative to this script."""
    base_dir = os.path.dirname(os.path.abspath(__file__))
    return os.path.join(base_dir, "instance", "transport.db")


def _outbox_table_exists(cursor):
    """Check if bot003_notification_outbox table exists."""
    cursor.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name=?",
        ("bot003_notification_outbox",)
    )
    return cursor.fetchone() is not None


def _read_pending_rows(cursor, batch_size=20):
    """Read pending notification rows that are available to send.

    Uses row-level locking via locked_at to prevent duplicate processing
    in a concurrent worker scenario.

    Args:
        cursor: sqlite3 cursor.
        batch_size: Maximum number of rows to fetch.

    Returns:
        List of dicts with notification data.
    """
    now = datetime.utcnow().isoformat()

    # Lock rows atomically
    cursor.execute("""
        UPDATE bot003_notification_outbox
        SET locked_at = ?
        WHERE id IN (
            SELECT id FROM bot003_notification_outbox
            WHERE status = 'pending'
              AND attempts < max_attempts
              AND available_at <= ?
              AND (locked_at IS NULL OR locked_at < ?)
            ORDER BY created_at ASC
            LIMIT ?
        )
    """, (now, now, now, batch_size))

    # Fetch locked rows
    cursor.execute("""
        SELECT id, event_type, request_id, target_user_id, target_telegram_id,
               payload_json, attempts, max_attempts
        FROM bot003_notification_outbox
        WHERE locked_at = ?
        ORDER BY id ASC
    """, (now,))

    rows = cursor.fetchall()
    return [
        {
            "id": row[0],
            "event_type": row[1],
            "request_id": row[2],
            "target_user_id": row[3],
            "target_telegram_id": row[4],
            "payload_json": row[5],
            "attempts": row[6],
            "max_attempts": row[7],
        }
        for row in rows
    ]


def _mark_sent(cursor, notification_id):
    """Mark a notification as successfully sent."""
    now = datetime.utcnow().isoformat()
    cursor.execute("""
        UPDATE bot003_notification_outbox
        SET status = 'sent',
            sent_at = ?,
            locked_at = NULL,
            updated_at = ?
        WHERE id = ?
    """, (now, now, notification_id))


def _mark_failed(cursor, notification_id, error_message, increment_attempts=True,
                  permanent=False):
    """Mark a notification as failed.

    Args:
        cursor: sqlite3 cursor.
        notification_id: Row ID to update.
        error_message: Description of the failure.
        increment_attempts: Whether to increment the attempt counter.
        permanent: If True, mark as 'failed' (exhausted retries). Otherwise
                   mark as 'pending' again with a retry delay.
    """
    now = datetime.utcnow().isoformat()
    if permanent:
        cursor.execute("""
            UPDATE bot003_notification_outbox
            SET status = 'failed',
                last_error = ?,
                attempts = attempts + ?,
                locked_at = NULL,
                updated_at = ?
            WHERE id = ?
        """, (error_message, 1 if increment_attempts else 0, now, notification_id))
    else:
        # Requeue with exponential backoff: 30s, 2min, 5min, 15min, 30min
        retry_delays = [30, 120, 300, 900, 1800]
        cursor.execute(
            "SELECT attempts, max_attempts FROM bot003_notification_outbox WHERE id = ?",
            (notification_id,)
        )
        row = cursor.fetchone()
        current_attempts = (row[0] if row else 0) + (1 if increment_attempts else 0)
        max_attempts = row[1] if row else 5

        if current_attempts >= max_attempts:
            # Exhausted retries — mark as failed permanently
            _mark_failed(cursor, notification_id, error_message,
                         increment_attempts=False, permanent=True)
            return

        delay_idx = min(current_attempts - 1, len(retry_delays) - 1)
        delay_seconds = retry_delays[delay_idx]
        next_available = (datetime.utcnow() + timedelta(seconds=delay_seconds)).isoformat()

        cursor.execute("""
            UPDATE bot003_notification_outbox
            SET status = 'pending',
                attempts = ?,
                last_error = ?,
                available_at = ?,
                locked_at = NULL,
                updated_at = ?
            WHERE id = ?
        """, (current_attempts, error_message, next_available, now, notification_id))


def _send_telegram_message(telegram_id, text, bot_token):
    """Send a Telegram message using the Bot API directly.

    This avoids importing python-telegram-bot to prevent dependency issues.
    Uses the raw HTTP API.

    Args:
        telegram_id: Telegram chat/user ID (string).
        text: Message text to send.
        bot_token: Telegram Bot API token.

    Returns:
        True if sent successfully, False otherwise.
    """
    # [REASON]: Use urllib instead of requests to avoid external dependency.
    # The production bot already uses python-telegram-bot, but this worker
    # should be self-contained and not require that library.
    import urllib.request
    import urllib.error

    if not telegram_id or not bot_token:
        logger.warning("Cannot send: missing telegram_id or bot_token")
        return False

    url = "https://api.telegram.org/bot{}/sendMessage".format(bot_token)
    data = json.dumps({
        "chat_id": telegram_id,
        "text": text,
        "parse_mode": "HTML",
    }).encode("utf-8")

    req = urllib.request.Request(
        url,
        data=data,
        headers={"Content-Type": "application/json"},
        method="POST",
    )

    try:
        response = urllib.request.urlopen(req, timeout=15)
        result = json.loads(response.read().decode("utf-8"))
        if result.get("ok"):
            return True
        else:
            logger.warning("Telegram API returned error: %s", result.get("description"))
            return False
    except urllib.error.HTTPError as e:
        logger.warning("Telegram API HTTP error: %s %s", e.code, e.reason)
        return False
    except urllib.error.URLError as e:
        logger.warning("Telegram API connection error: %s", e.reason)
        return False
    except Exception:
        logger.exception("Unexpected error sending Telegram message:")
        return False


def _build_notification_text(payload):
    """Build a human-readable Telegram message from a notification payload.

    Args:
        payload: Dict parsed from payload_json.

    Returns:
        A formatted string for the Telegram message.
    """
    event_type = payload.get("event_type", "unknown")
    request_id = payload.get("request_id")
    request_number = payload.get("request_number", "")
    organization_name = payload.get("organization_name", "")
    equipment_name = payload.get("equipment_name", "")
    created_by_name = payload.get("created_by_name", "")
    status = payload.get("status", "")

    # Header based on event type
    if event_type == "spare_request_submitted":
        header = "\U0001F4E8 Yangi ehtiyot qism so'rovi"
    elif event_type == "spare_request_approved":
        header = "✅ So'rov tasdiqlandi"
    elif event_type == "spare_request_rejected":
        header = "❌ So'rov rad etildi"
    elif event_type == "spare_request_revision_requested":
        header = "\U0001F504 So'rov qayta ko'rishga yuborildi"
    else:
        header = "\U0001F514 Ehtiyot qism so'rovi yangilanishi"

    lines = [header]
    lines.append("")

    if request_id:
        lines.append("\U0001F4CB ID: #{}".format(request_id))
    if request_number:
        lines.append("\U0001F4CB Raqam: {}".format(request_number))
    if organization_name:
        lines.append("\U0001F3ED Tashkilot: {}".format(organization_name))
    if equipment_name:
        lines.append("\U0001F698 Texnika: {}".format(equipment_name))
    if created_by_name:
        lines.append("\U0001F464 Yaratuvchi: {}".format(created_by_name))
    if status:
        lines.append("\U0001F4C4 Holat: {}".format(status))

    lines.append("")
    lines.append("\U0001F517 <a href='http://10.103.25.14:5051/spare-parts/{}'>So'rovni ochish</a>".format(
        request_id or ""
    ))

    return "\n".join(lines)


def process_outbox(bot_token=None, app=None, batch_size=20):
    """Process pending notifications from the outbox table.

    This is the main entry point for the worker. It:
    1. Opens an isolated sqlite3 connection.
    2. Reads pending rows from bot003_notification_outbox.
    3. Sends each notification via Telegram Bot API.
    4. Updates row status to sent/failed.

    Args:
        bot_token: Telegram Bot API token. If None, reads from TG_BOT_TOKEN env var.
        app: Optional Flask app (unused, kept for API compatibility).
        batch_size: Maximum notifications to process per call.

    Returns:
        Dict with counts: {'processed': int, 'sent': int, 'failed': int,
                           'skipped': int, 'error': str or None}
    """
    result = {"processed": 0, "sent": 0, "failed": 0, "skipped": 0, "error": None}

    if not bot_token:
        bot_token = os.environ.get("TG_BOT_TOKEN", "")
    if not bot_token:
        msg = "TG_BOT_TOKEN not configured"
        logger.warning(msg)
        result["error"] = msg
        return result

    db_path = _get_db_path()
    if not os.path.exists(db_path):
        msg = "Database not found at {}".format(db_path)
        logger.warning(msg)
        result["error"] = msg
        return result

    conn = None
    try:
        conn = sqlite3.connect(db_path)
        cursor = conn.cursor()

        # Check if outbox table exists
        if not _outbox_table_exists(cursor):
            msg = "bot003_notification_outbox table does not exist"
            logger.info("BOT003: %s — skipping outbox processing", msg)
            result["error"] = msg
            conn.close()
            return result

        # Read pending rows
        rows = _read_pending_rows(cursor, batch_size)
        if not rows:
            logger.info("BOT003: No pending notifications to process")
            conn.commit()
            conn.close()
            return result

        result["processed"] = len(rows)
        logger.info("BOT003: Processing %d pending notifications", len(rows))

        for row in rows:
            notification_id = row["id"]
            telegram_id = row["target_telegram_id"]
            payload_json = row["payload_json"]

            if not telegram_id:
                logger.info(
                    "BOT003: Skipping notification %d — no target_telegram_id",
                    notification_id
                )
                _mark_failed(cursor, notification_id, "No target_telegram_id",
                             permanent=True)
                result["skipped"] += 1
                conn.commit()
                continue

            # Parse payload
            try:
                payload = json.loads(payload_json)
            except (json.JSONDecodeError, TypeError):
                logger.warning(
                    "BOT003: Invalid payload_json for notification %d",
                    notification_id
                )
                _mark_failed(cursor, notification_id, "Invalid payload_json",
                             permanent=True)
                result["failed"] += 1
                conn.commit()
                continue

            # Build message text
            text = _build_notification_text(payload)

            # Send via Telegram
            sent = _send_telegram_message(telegram_id, text, bot_token)

            if sent:
                _mark_sent(cursor, notification_id)
                result["sent"] += 1
                logger.info(
                    "BOT003: Sent notification %d to telegram_id=%s",
                    notification_id, telegram_id
                )
            else:
                _mark_failed(cursor, notification_id,
                             "Telegram API send failed")
                result["failed"] += 1
                logger.warning(
                    "BOT003: Failed to send notification %d to telegram_id=%s",
                    notification_id, telegram_id
                )

            conn.commit()

        conn.close()
        logger.info(
            "BOT003: Outbox processing complete — sent=%d, failed=%d, skipped=%d",
            result["sent"], result["failed"], result["skipped"]
        )

    except Exception:
        logger.exception("BOT003: Outbox processing error: ")
        result["error"] = "Unexpected error in outbox processing"
        if conn:
            try:
                conn.close()
            except Exception:
                pass

    return result


def main_loop(bot_token=None, interval_seconds=30, max_iterations=None):
    """Run the outbox worker in a continuous loop.

    Args:
        bot_token: Telegram Bot API token.
        interval_seconds: Seconds to sleep between iterations.
        max_iterations: Maximum number of iterations (None = run forever).
    """
    import time

    if not bot_token:
        bot_token = os.environ.get("TG_BOT_TOKEN", "")
    if not bot_token:
        print("ERROR: TG_BOT_TOKEN not set. Set the environment variable or pass bot_token.")
        sys.exit(1)

    print("BOT003 Outbox Worker started.")
    print("Polling interval: {}s".format(interval_seconds))
    if max_iterations:
        print("Max iterations: {}".format(max_iterations))
    print("Press Ctrl+C to stop.")
    print()

    iteration = 0
    try:
        while max_iterations is None or iteration < max_iterations:
            iteration += 1
            timestamp = datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S")
            print("[{}] Iteration {} — processing outbox...".format(timestamp, iteration))

            result = process_outbox(bot_token=bot_token)

            print(
                "  Processed: {}, Sent: {}, Failed: {}, Skipped: {}".format(
                    result["processed"], result["sent"],
                    result["failed"], result["skipped"]
                )
            )
            if result["error"]:
                print("  Note: {}".format(result["error"]))

            if max_iterations is not None and iteration >= max_iterations:
                break

            time.sleep(interval_seconds)

    except KeyboardInterrupt:
        print("\nBOT003 Outbox Worker stopped by user.")
    except Exception:
        logger.exception("BOT003: Fatal error in worker main loop: ")
        print("\nFATAL ERROR: See logs for details.")


if __name__ == "__main__":
    # Configure logging for standalone use
    log_dir = os.environ.get("BOT_LOG_DIR", "logs")
    os.makedirs(log_dir, exist_ok=True)

    log_file = os.path.join(log_dir, "bot003_outbox_worker.log")
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
        handlers=[
            logging.FileHandler(log_file, encoding="utf-8"),
            logging.StreamHandler(sys.stdout),
        ],
    )

    interval = int(os.environ.get("BOT003_POLL_INTERVAL", "30"))
    main_loop(interval_seconds=interval)
