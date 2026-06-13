# EXTAUDIT001 Closure Report

Date: 2026-06-13

## Summary

EXTAUDIT001 external audit has been reviewed, triaged, fixed, validated, and documented.

Critical and near-critical findings were closed through:

- FIX001
- FIX002
- FIX003A
- QA003

Result:

EXTAUDIT001 critical remediation is complete.

No rollback required.

## Audit Import

External audit report was imported into the repository:

- b14d5cc Document EXTAUDIT001 external audit report

## Closed Findings

### 1. BOT003 production URL hardcoded to staging

Status: CLOSED

Problem:

- BOT003 generated links with staging port 5051.
- Production notifications needed production URL/port 5050.

Fix:

- Added APP_PUBLIC_BASE_URL support.
- Production URL now resolves from environment/config.
- BOT003 deep links no longer hardcode staging URL.

Commits:

- 573e576 Fix BOT003 public URL and mojibake strings
- 2c1051b Document FIX001 production rollout

Validation:

- Production rollout completed.
- Services stayed running.
- BOT003 dry-run passed.

### 2. Mojibake strings in application code/data

Status: CLOSED

Problem:

- Several corrupted Cyrillic/Uzbek strings existed in app.py and production data.

Fix:

- Fixed code strings.
- Added one-time production data fix for corrupted daily_records idle_reason.

Commits:

- 573e576 Fix BOT003 public URL and mojibake strings
- 2c1051b Document FIX001 production rollout

Validation:

- Encoding checks passed.
- Production data fix applied.
- App import and route checks passed.

### 3. SQLite multi-process hardening

Status: CLOSED

Problem:

- Web app, bot, and BOT003 worker access the same SQLite database.
- WAL and busy_timeout hardening were needed.

Fix:

- Added sqlite_runtime.py.
- Enabled SQLite WAL at app startup.
- Added SQLAlchemy connection listener.
- Hardened raw sqlite3 runtime connections used by BOT003.
- Configured busy timeout and related PRAGMAs for runtime paths.

Commits:

- 0f7789c Enable SQLite WAL and busy timeout hardening
- 97c0748 Document FIX002 production rollout

Validation:

- Production DB integrity OK.
- journal_mode = wal.
- Runtime checks passed.
- Services remained running.
- BOT003 dry-run passed.

Note:

Raw ad-hoc sqlite3 checks may still show default per-connection busy_timeout/synchronous. This is expected. Runtime configured connections are hardened.

### 4. spare_part_status_history dormant feature

Status: CLOSED FOR NEW EVENTS

Problem:

- SparePartStatusHistory model and table existed.
- bot_api.py already serialized history.
- Web workflow did not write history rows.

Fix:

- spare_parts.py now writes status history before commit for:
  - new request created directly as submitted
  - draft -> submitted
  - submitted -> approved
  - submitted -> rejected

Commit:

- 9a5603e Honor Telegram notification preference and write spare history

Validation:

- Helper insert/rollback test passed.
- No schema changes.
- No historical backfill.
- Production rollout completed.

Note:

Existing historical requests were not backfilled by design. New status transitions will be recorded going forward.

### 5. users.tg_notifications ignored by BOT003

Status: CLOSED

Problem:

- users.tg_notifications existed but was not enforced by BOT003 recipient selection.

Fix:

- _get_admin_telegram_ids now requires tg_notifications = 1.
- _get_user_telegram_id now requires tg_notifications = 1.

Commit:

- 9a5603e Honor Telegram notification preference and write spare history

Validation:

- Transactional rollback test confirmed:
  - when tg_notifications = 0, user is excluded
  - when tg_notifications = 1, user is included
- BOT003 dry-run passed.
- Production rollout completed.

## Post-Fix Regression Audit

QA003 was completed after FIX001, FIX002, and FIX003A.

Commit:

- 99611b8 Document QA003 post-FIX003A regression audit

Result:

PASS WITH NOTES

Confirmed:

- Production git clean.
- Staging git clean.
- Production services running.
- Staging services running.
- py_compile passed.
- App import passed.
- Route smoke tests passed.
- DB integrity OK.
- BOT003 dry-run error null.
- /fuel/receipts authenticated render returns 200 on production and staging.

## Historical Log Note

Production logs/error.log contains historical /fuel/receipts errors:

- TypeError: Object of type Undefined is not JSON serializable
- variable: L_add

Focused current validation confirmed:

- production /fuel/receipts authenticated render -> 200
- staging /fuel/receipts authenticated render -> 200

Therefore this is not an active regression.

## Deferred / Non-Critical Items

The following items are intentionally deferred and are not treated as active critical defects:

### 1. legacy bot_notification_queue

Status: DEFERRED

Reason:

- Table is legacy/dormant.
- Current BOT003 uses bot003_notification_outbox.
- Removing legacy table requires separate cleanup plan and migration risk review.

Decision:

- Do not drop now.
- Keep as-is until a future cleanup release.

### 2. spare_request_revision_requested / return-for-revision workflow

Status: FUTURE FEATURE

Reason:

- BOT003 has a dormant event name, but business workflow is not implemented.
- Adding return-for-revision requires UI, permissions, status model, audit behavior, and notification text.

Decision:

- Do not implement inside audit remediation.
- Treat as separate future feature.

### 3. foreign_keys = 0 on raw SQLite checks

Status: DEFERRED / EXPECTED CURRENT STATE

Reason:

- Existing schema and legacy data may not be ready for enforcing foreign_keys = 1 globally.
- Enabling this globally is higher-risk and should be handled as a separate migration project.

Decision:

- Do not change during audit closure.
- Keep as future DB-hardening item.

### 4. Historical status backfill

Status: DEFERRED

Reason:

- Backfilling old request history would require reconstructing events from available audit/data.
- Risk of inaccurate historical rows.

Decision:

- Do not backfill.
- Record only new transitions going forward.

## Current Repository State

Expected final state after this report:

- staging HEAD: this closure report commit
- production HEAD: will be synced doc-only after commit
- origin/main: this closure report commit

## Result

EXTAUDIT001 remediation is closed.

Critical audit findings were fixed and validated.

Remaining items are documented as future work, not blockers.
