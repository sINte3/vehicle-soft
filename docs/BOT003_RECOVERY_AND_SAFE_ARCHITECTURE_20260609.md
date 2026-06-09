# BOT003 — Recovery and Safe Architecture

**Date:** 2026-06-09
**Author:** Claude Code (staging-only implementation)
**Status:** Architecture document — not yet applied to production

---

## 1. What failed in the rejected BOT003 attempt

The previous BOT003 attempt (archived at `C:\temp\BOT003_CLAUDE_FAILED_20260609_210208.zip`) attempted to add Telegram push notifications for spare part request status changes. It failed with a **500 Internal Server Error** when submitting spare part requests.

### Root Cause

The existing `bot_notification_queue` table had `NOT NULL` constraints on `payload_json` and `attempts` columns from the earlier BOT001/BOT002 schema. The failed BOT003 code inserted notification rows **without `payload_json`**, causing:

1. `sqlite3.IntegrityError` — `NOT NULL constraint failed: bot_notification_queue.payload_json`
2. `sqlalchemy.exc.PendingRollbackError` — the main session entered a poisoned state
3. The spare part request transaction (which triggered the notification) was rolled back together with the failed notification insert

### Architectural Failure

The core mistake was **sharing the same SQLAlchemy session** between the spare parts business transaction and the notification enqueue. When the notification insert failed, it made the entire session unusable, and the business operation could not be committed.

### Files Modified in the Failed Attempt

The rollback was clean because:
- The migration script `migrate_bot003_notification_queue.py` was never executed in staging
- No table `bot003_notification_queue` schema changes persisted
- The rollback restored the working state

---

## 2. Why notification logic must be isolated from the spare parts business transaction

### Principle: Notifications are best-effort, not business-critical

A spare part request creation, approval, or rejection must succeed **even if the Telegram bot is down**, the database is slow, or the notification payload is malformed.

### The Shared Session Problem

```
[Web Request]
  |-- Start SQLAlchemy transaction (db.session)
  |-- INSERT into spare_part_requests       <- business data
  |-- INSERT into bot_notification_queue    <- notification data (FAILS!)
  |-- db.session.commit()                   <- never reached, session is poisoned
  |-- 500 Internal Server Error             <- user sees this
```

### The Isolated Outbox Solution

```
[Web Request]
  |-- Start SQLAlchemy transaction (db.session)
  |-- INSERT into spare_part_requests       <- business data
  |-- db.session.commit()                   <- commits first, always
  |-- [Best-effort] After commit:
  |     |-- Open separate sqlite3 connection
  |     |-- INSERT into bot003_notification_outbox
  |     |-- Close connection
  |     |-- If any error: log warning, return
  |-- 200 Success                           <- user sees this
```

### Rule

> **Telegram notification failure must NEVER break creation, saving, submitting, approving, rejecting, or returning a spare part request.**

---

## 3. New safe architecture

### Components

| Component | File | Role |
|-----------|------|------|
| Core spare parts flow | `spare_parts.py` | Business logic — commits first |
| Best-effort enqueue | `bot003_notifications.py` | Post-commit notification outbox insert |
| Notification outbox table | `bot003_notification_outbox` (new) | Durable queue for Telegram messages |
| Outbox worker | `bot003_outbox_worker.py` | Reads pending rows, sends via Telegram bot |
| Existing Telegram bot | `bot.py` (unchanged) | Telegram event listener — preserves BOT002B |
| Migration script | `migrate_bot003_outbox_v1.py` | Creates the outbox table — not yet executed |

### Data Flow

```
spare_parts.py (web request)
    |
    |-- Business transaction
    |   |-- spare_part_requests INSERT/UPDATE
    |   |-- spare_part_request_items INSERT
    |   |-- spare_part_request_history INSERT
    |   |-- db.session.commit()  <- ALWAYS FIRST
    |
    |-- Post-commit (best-effort)
        |-- bot003_notifications.enqueue_*_best_effort()
            |-- Isolated sqlite3 connection
                |-- bot003_notification_outbox INSERT
                    |-- status = 'pending'
                    |-- available_at = NOW
                    |-- dedupe_key = event_type + request_id + hash

bot003_outbox_worker.py (polling loop)
    |-- SELECT from bot003_notification_outbox WHERE status='pending' AND available_at <= NOW
    |-- Send via Telegram bot API
    |-- UPDATE status = 'sent' | 'failed'
    |-- Increment attempts, set next available_at for retries
```

---

## 4. Explicit rule: main spare part transaction commits first

```python
# CORRECT — commit first, notify after
db.session.add(history_entry)
db.session.commit()  # Main transaction commits FIRST
# Best-effort notification (isolated, never poisons main session)
enqueue_spare_request_submitted_best_effort(request.id)
```

```python
# WRONG — never do this
db.session.add(history_entry)
enqueue_spare_request_submitted_best_effort(request.id)  # BEFORE commit
db.session.commit()
```

---

## 5. Explicit rule: notification enqueue uses isolated session or separate connection

The enqueue function **must not** use the caller's `db.session`. It opens its own `sqlite3` connection or an isolated SQLAlchemy session resolved from the Flask app config.

```python
def enqueue_spare_request_submitted_best_effort(request_id: int) -> None:
    try:
        conn = sqlite3.connect(get_db_path())
        cursor = conn.cursor()
        cursor.execute("INSERT INTO bot003_notification_outbox (...) VALUES (...)")
        conn.commit()
        conn.close()
    except Exception:
        logger.warning("Notification enqueue failed (non-fatal)", exc_info=True)
```

---

## 6. Explicit rule: all notification errors are caught and logged

Every public function in `bot003_notifications.py`:

- Catches `Exception` (broad catch — includes `sqlite3.Error`, `ValueError`, `TypeError`, etc.)
- Logs via `logger.warning(...)` or `logger.exception(...)`
- Never raises
- Returns `None`

---

## 7. Explicit rule: do not reuse the old bot_notification_queue table

The old `bot_notification_queue` table:
- Already has rows from BOT001/BOT002
- Has `NOT NULL` constraints that were the root cause of the failure
- Is used by the existing BOT002B worker

Create a **new table** `bot003_notification_outbox` with a clean schema designed for the outbox pattern.

---

## 8. Migration review requirement

The migration script `migrate_bot003_outbox_v1.py`:
- Creates `bot003_notification_outbox`
- Is idempotent (checks if table exists)
- Has been reviewed for correctness
- **Has NOT been executed** — will remain unexecuted at the end of this work session
- Execution requires explicit approval after review

---

## 9. Staging-only validation checklist

- [ ] Spare part request form loads without error (table missing scenario)
- [ ] Spare part request submission succeeds (no 500) with table missing
- [ ] Approval/rejection/revision succeed without notification table
- [ ] `bot003_notifications.py` logs "outbox table missing" warning on enqueue calls
- [ ] `bot003_outbox_worker.py` imports without error
- [ ] `migrate_bot003_outbox_v1.py` is syntactically valid and idempotent
- [ ] `bot.py` (BOT002B) unchanged — existing commands still work
- [ ] All Python files compile (`py_compile` passes)
- [ ] Flask app import succeeds
- [ ] All core routes return non-500 responses
- [ ] `diagnose_bot003_outbox.py` reads schema without modifying data
