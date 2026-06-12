# BOT003 Production Rollout

**Date:** 2026-06-10
**Server:** srv-yoqsh

---

## 1. Rollout Status

BOT003 production rollout completed successfully on srv-yoqsh.

---

## 2. Production Code

| Item   | Value                                      |
|--------|--------------------------------------------|
| Path   | C:\transport-report                        |
| HEAD   | 7ceb523 Document BOT003 staging validation |
| Baseline | 67d91d8 Document BOT002B production deployment |

---

## 3. Backups Created Before Rollout

| Backup | Path                                                              |
|--------|-------------------------------------------------------------------|
| DB backup | D:\transport-report-backups\production\daily\transport_20260610_140803.db |
| DB backup before migration | D:\transport-report-backups\production\daily\transport_20260610_141323.db |
| Source snapshot | D:\transport-report-backups\production\source\transport_prod_source_before_bot003_20260610_140803.zip |

---

## 4. Production Migration

- Migration script: `migrate_bot003_outbox_v1.py` completed successfully.
- Table `bot003_notification_outbox` exists with 16 columns.
- Required indexes exist.
- `schema_migrations` table contains entry `migrate_bot003_outbox_v1`.

---

## 5. Production Services

| Service          | Status    |
|------------------|-----------|
| TransportReport  | RUNNING   |
| TransportBot     | RUNNING   |
| TransportBot003  | RUNNING   |

---

## 6. TransportBot003 NSSM Configuration

| Parameter      | Value                                                              |
|----------------|--------------------------------------------------------------------|
| Application    | C:\Program Files\Python314\python.exe                              |
| AppDirectory   | C:\transport-report                                                |
| AppParameters  | bot003_outbox_worker.py --interval 30 --batch-size 20              |
| stdout         | C:\transport-report\logs\bot003_worker_stdout.log                  |
| stderr         | C:\transport-report\logs\bot003_worker_stderr.log                  |

---

## 7. Production Telegram Bot

| Field          | Value                  |
|----------------|------------------------|
| BOT_USERNAME   | BuxAgroTransportBot    |
| BOT_FIRST_NAME | Buxoro Agro Transport  |

*(Token is not documented or exposed in this file.)*

---

## 8. Production Smoke Test

- Admin Telegram account linked successfully.
- Request #3 created as BOT003 production smoke test.
- Outbox row:
  - event_type: `spare_request_submitted`
  - request_id: `3`
  - target_user_id: `1`
  - status: `sent`
  - attempts: `0`
  - last_error: `None`
- TransportBot003 log confirmed:
  - `Processing 1 pending notifications`
  - `Sent notification 1 to telegram_id`
  - `Outbox processing complete - sent=1, failed=0, skipped=0`
- Telegram message was received from BuxAgroTransportBot.

---

## 9. Final State

| Metric                           | Value |
|----------------------------------|-------|
| bot003_notification_outbox Total | 1     |
| Status sent                      | 1     |
| Spare part requests total        | 3     |
| Active linked Telegram users     | 1     |
| git status                       | clean |

---

## 10. Rollback Note

- Stop TransportBot003 to disable BOT003 notifications.
- BOT002B TransportBot remains independent.
- The `bot003_notification_outbox` table is safe to leave in the database.
- Use DB/source backups listed above only if a full rollback is required.

---

*Document generated for BOT003 production rollout.*
