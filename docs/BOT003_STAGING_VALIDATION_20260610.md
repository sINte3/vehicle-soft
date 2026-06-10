# BOT003  -  Staging Validation 2026-06-10

## 1. Purpose

BOT003 adds Telegram push notifications for spare part request lifecycle events
(creation, submission, approval, rejection) using an isolated outbox pattern.

Key design rule: **notification failures must never break spare part request
creation or status changes.** The spare parts business transaction commits first,
and notification enqueue runs as a best-effort operation afterwards.

## 2. Safety Architecture

```
spare_parts.py (web request)
    |
    |-- db.session.commit()          <-- business transaction commits FIRST
    |-- [best-effort] enqueue_*_best_effort()
    |       |
    |       |-- Opens independent sqlite3 connection
    |       |-- INSERT into bot003_notification_outbox
    |       |-- Catches and logs all exceptions
    |       |-- NEVER raises, NEVER poisons the Flask session

bot003_outbox_worker.py (polling worker, separate process)
    |
    |-- SELECT pending rows from bot003_notification_outbox
    |-- Send via Telegram Bot API HTTPS call
    |-- UPDATE status = sent / failed
    |-- Retry with exponential backoff on failure
```

- Enqueue runs after successful `db.session.commit()` in spare_parts.py.
- Enqueue uses an independent `sqlite3` connection (never the caller's Flask session).
- All enqueue errors are caught, logged as warnings, and suppressed.
- The worker processes `bot003_notification_outbox` in a separate NSSM service.
- Existing BOT002B polling bot (`bot.py`) is unchanged  -  no interference.

## 3. Commits Included

| Commit | Description |
|--------|-------------|
| `6297a75` | Add safe BOT003 notification outbox foundation |
| `b562cfd` | Harden BOT003 outbox worker CLI |
| `a3a89e5` | Fix BOT003 worker dry-run loop mode |

## 4. Staging Migration

- Migration script `migrate_bot003_outbox_v1.py` executed on staging.
- Table `bot003_notification_outbox` created with 16 columns, 3 indexes.
- `schema_migrations` registry contains row `migrate_bot003_outbox_v1`.
- Migration is idempotent  -  safe to re-run.

### Table schema

| Column | Type | Constraints |
|--------|------|-------------|
| id | INTEGER | PRIMARY KEY AUTOINCREMENT |
| event_type | TEXT | NOT NULL |
| request_id | INTEGER | NOT NULL |
| target_user_id | INTEGER | NULL |
| target_telegram_id | TEXT | NULL |
| payload_json | TEXT | NOT NULL |
| dedupe_key | TEXT | NOT NULL UNIQUE |
| status | TEXT | NOT NULL DEFAULT 'pending' |
| attempts | INTEGER | NOT NULL DEFAULT 0 |
| max_attempts | INTEGER | NOT NULL DEFAULT 5 |
| available_at | TEXT | NOT NULL |
| locked_at | TEXT | NULL |
| sent_at | TEXT | NULL |
| last_error | TEXT | NULL |
| created_at | TEXT | NOT NULL |
| updated_at | TEXT | NOT NULL |

### Indexes

- `idx_bot003_outbox_status_available`  -  status, available_at
- `idx_bot003_outbox_request_id`  -  request_id
- `idx_bot003_outbox_target_user_id`  -  target_user_id

## 5. Staging Tests Passed

| # | Test | Result |
|---|------|--------|
| 1 | Spare part request creation before outbox migration | PASS  -  no 500, table-missing warning logged gracefully |
| 2 | Request #6 enqueue with dummy Telegram ID | PASS  -  pending row created, cleaned up after test |
| 3 | Dry-run worker on pending row | PASS  -  row read without modification, no send attempted |
| 4 | Admin Telegram account linking | PASS  -  `/link <code>` flow completed successfully |
| 5 | Request #7 real send (manual) | PASS  -  `processed=1, sent=1, failed=0` |
| 6 | Request #8 service auto-send | PASS  -  `status='sent', attempts=0, last_error=None` |
| 7 | Request #8 Telegram message received | PASS  -  message arrived at linked admin chat |
| 8 | `bot003_outbox_worker.py --once --dry-run` | PASS  -  empty outbox: `error=null, dry_run=true` |
| 9 | `bot003_outbox_worker.py --dry-run --max-iterations 1 --interval 1` | PASS  -  loop completes without token |
| 10 | `process_outbox()` backward compatibility | PASS  -  accepts `bot_token=` and `batch_size=` |

## 6. Staging Services

All three staging services are RUNNING:

| Service | Status | Purpose |
|---------|--------|---------|
| `TransportReportStaging` | RUNNING | Flask web app (main application) |
| `TransportBotStaging` | RUNNING | BOT002B Telegram polling bot (`bot.py`) |
| `TransportBot003Staging` | RUNNING | BOT003 outbox worker (`bot003_outbox_worker.py`) |

### TransportBot003Staging configuration

| Setting | Value |
|---------|-------|
| Application | `C:\Program Files\Python314\python.exe` |
| AppDirectory | `C:\transport-report-staging` |
| AppParameters | `bot003_outbox_worker.py --interval 30 --batch-size 20` |
| stdout log | `C:\transport-report-staging\logs\bot003_worker_stdout.log` |
| stderr log | `C:\transport-report-staging\logs\bot003_worker_stderr.log` |

## 7. Important Security Note

**The Telegram bot token was exposed in console output during manual diagnostics
on staging. It must be rotated via BotFather in Telegram before production rollout.**

Rules inherited from BOT002B:
- Never print `TG_BOT_TOKEN` in logs, documentation, console output, or chat.
- Never store the token in source control or documentation files.
- The token is read from the `TG_BOT_TOKEN` environment variable on the server.

A new token must be generated via BotFather in Telegram, then updated in the
NSSM service environment **without printing the token value**. The BOT002B and
BOT003 NSSM services use `AppEnvironmentExtra` to pass the token to the
application. After updating the token, restart both services and re-link all
staging Telegram sessions before proceeding to production deployment.

## 8. Production Rollout  -  Not Done Yet

BOT003 is validated on staging but **not deployed to production**.
Production services are not modified.

### Production services expected after rollout

| Service | Purpose |
|---------|---------|
| `TransportReport` | Flask web app (main application) |
| `TransportBot` | BOT002B Telegram polling bot (`bot.py`) |
| `TransportBot003` | BOT003 outbox worker (`bot003_outbox_worker.py`) |

### Required production rollout steps

1. Pre-deployment backup:
   ```
   cd C:\transport-report
   backup_transport_db.bat
   ```

2. Pull code on production server:
   ```
   cd C:\transport-report
   git pull --ff-only
   ```

3. Verify syntax and imports:
   ```
   "C:\Program Files\Python314\python.exe" -m py_compile bot003_outbox_worker.py bot003_notifications.py
   "C:\Program Files\Python314\python.exe" -c "from app import app; print('OK')"
   ```

4. Stop services, run migration:
   ```
   net stop TransportBot
   net stop TransportReport
   "C:\Program Files\Python314\python.exe" migrate_bot003_outbox_v1.py
   net start TransportReport
   net start TransportBot
   ```

5. Create `TransportBot003` NSSM service (same pattern as TransportBotStaging).

6. Rotate token via BotFather in Telegram. Do not print or store the new value.

7. Update `TG_BOT_TOKEN` in NSSM service environment using `AppEnvironmentExtra`
   for each affected service (`TransportBot`, `TransportBot003`). Do not use `setx`
   - NSSM services do not re-read `setx` environment variables after service start.
   Update `AppEnvironmentExtra` using a local administrator procedure that does not echo the token to console, logs, screenshots, chat, or documentation. After updating the token, restart `TransportBot` and `TransportBot003` and verify both services can read the token.

8. Full smoke test on production:
   - Spare part request creation (no 500)
   - Enqueue produces pending row in outbox
   - Outbox worker sends notification
   - Admin receives Telegram message
   - Dry-run mode reports correctly

### Rollback plan

- Stop `TransportBot003` service.
- Revert code via `git checkout <previous-commit>`.
- Restart `TransportBot` only (BOT002B commands unaffected).
- The `bot003_notification_outbox` table is safe to leave in the database
  (no existing code depends on it).

## 9. Files

| File | Purpose |
|------|---------|
| `bot003_outbox_worker.py` | Outbox worker: polls pending rows, sends via Telegram, retries on failure |
| `bot003_notifications.py` | Best-effort enqueue module for spare parts lifecycle events |
| `migrate_bot003_outbox_v1.py` | Idempotent migration creating `bot003_notification_outbox` table |
| `diagnose_bot003_outbox.py` | Read-only diagnostic tool for outbox table state |
| `docs/BOT003_STAGING_VALIDATION_20260610.md` | This document |
| `docs/BOT003_RECOVERY_AND_SAFE_ARCHITECTURE_20260609.md` | Architecture decisions and failure analysis |
