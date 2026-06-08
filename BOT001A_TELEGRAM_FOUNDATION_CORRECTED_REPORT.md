# BOT001A Telegram Foundation — Corrected Patch Report

**Date:** 2026-06-06  
**Patch:** BOT001A (corrects BOT001)  
**Staging path:** `C:\transport-report-staging`  
**Staging URL:** http://10.103.25.14:5051  
**Production:** NOT touched. `C:\transport-report` and `TransportReport` service were not modified.

---

## What Was Wrong in BOT001

| # | Critical Issue | Impact |
|---|---------------|--------|
| 1 | `bot_api_bp` was imported in `app.py` but `app.register_blueprint(bot_api_bp)` was never called | `/api/bot/*` routes did NOT appear in `url_map`; entire bot API was unreachable |
| 2 | Migration added `telegram_id INTEGER UNIQUE` — `ALTER TABLE ADD COLUMN` in SQLite does NOT support `UNIQUE` constraints | Migration would fail with `OperationalError` on first run |
| 3 | `idx_users_telegram_id` was a plain index, not a UNIQUE partial index | Duplicate `telegram_id` values across users would not be prevented |
| 4 | Staging install guide placed APP IMPORT CHECK before migration | `db.create_all()` could create tables without proper indexes |
| 5 | Production notes used `copy instance\transport.db ...` as the main backup of a live production DB | SQLite WAL may not be flushed; backup could be corrupt |
| 6 | Tests used `warn()` and masked failures; would pass with service running | No actual verification of correct behavior after migration |
| 7 | `bot_api.py` used `db.or_()` — `db.or_` does not exist in this project's SQLAlchemy setup | Catalog search would raise `AttributeError` at runtime |
| 8 | Duplicate `verify_secret` in `bot_api.py` import block | Code smell; masked the real import set |

---

## What Was Fixed in BOT001A

### Fix 1 — `app.register_blueprint(bot_api_bp)` added

**File:** [app.py](file:///C:/transport-report-staging/app.py)

Added after `app.register_blueprint(spare_parts_bp)`, before error handlers:

```python
# ─── BOT001: Telegram Bot API ─────────────────────────────────────────────
# [REASON]: bot_api_bp provides /api/bot/* endpoints. Registered after spare_parts_bp.
# CSRF is already exempted for /api/bot/* in is_csrf_exempt() above.
app.register_blueprint(bot_api_bp)
```

**Confirmed:** `/api/bot/health` appears in `app.url_map` (see test output below).

---

### Fix 2 — Migration: `telegram_id` added as plain `INTEGER`

**File:** [migrate_bot001_telegram_foundation.py](file:///C:/transport-report-staging/migrate_bot001_telegram_foundation.py)

Before (wrong):
```python
("telegram_id", "INTEGER UNIQUE"),
```

After (correct):
```python
("telegram_id", "INTEGER"),   # plain; uniqueness via partial index below
```

SQLite `ALTER TABLE ADD COLUMN` does not support `UNIQUE`. The column is added as plain `INTEGER`.

---

### Fix 3 — `idx_users_telegram_id` is now a UNIQUE partial index

**File:** [migrate_bot001_telegram_foundation.py](file:///C:/transport-report-staging/migrate_bot001_telegram_foundation.py)

```sql
CREATE UNIQUE INDEX IF NOT EXISTS idx_users_telegram_id
ON users(telegram_id) WHERE telegram_id IS NOT NULL
```

This enforces that no two users can share the same `telegram_id`, while allowing many users to have `telegram_id = NULL` (not yet linked). Each index is checked for existence before creation.

---

### Fix 4 — Staging install order corrected

**File:** [BOT001_STAGING_INSTALL.txt](file:///C:/transport-report-staging/BOT001_STAGING_INSTALL.txt)

Correct order:
1. `cd /d C:\transport-report-staging`
2. `nssm.exe stop TransportReportStaging`
3. Backup staging DB (after service stopped — safe SQLite state)
4. Backup files that will change
5. AST syntax checks on all .py files
6. Run `migrate_bot001_telegram_foundation.py`
7. Direct `PRAGMA integrity_check`
8. APP IMPORT CHECK + route verification (after migration)
9. `test_bot001_readonly.py`
10. `test_bot001_staging_api.py`
11. `nssm.exe start TransportReportStaging`
12. Browser smoke test

---

### Fix 5 — Production backup uses `backup_production_db.bat`

**File:** [BOT001_PRODUCTION_NOTES.txt](file:///C:/transport-report-staging/BOT001_PRODUCTION_NOTES.txt)

Production backup now requires running `backup_production_db.bat`, which uses Python's `sqlite3.Connection.backup()` API for a safe, consistent snapshot. The notes also include a template for `backup_production_db.bat` if it is missing.

---

### Fix 6 — Tests are now strict (no warnings mode)

**Files:** [test_bot001_readonly.py](file:///C:/transport-report-staging/test_bot001_readonly.py), [test_bot001_staging_api.py](file:///C:/transport-report-staging/test_bot001_staging_api.py)

- All checks use `[OK]` / `[FAIL]` — no `[WARN]` mode
- Tests require service stopped + migration applied before running
- `test_bot001_readonly.py`: 13 checks including DB table/index presence, all Telegram fields on `User`, blueprint registration source check
- `test_bot001_staging_api.py`: 13 API checks including all 5 routes, sensitive field absence, bad Bearer token handling

---

### Fix 7 — `bot_api.py`: `or_` imported from `sqlalchemy`

**File:** [bot_api.py](file:///C:/transport-report-staging/bot_api.py)

Before:
```python
from sqlalchemy import text
# ...
db.or_(SparePart.name.ilike(pattern), SparePart.part_number.ilike(pattern))
```

After:
```python
from sqlalchemy import or_
# ...
or_(SparePart.name.ilike(pattern), SparePart.part_number.ilike(pattern))
```

---

### Fix 8 — Duplicate `verify_secret` import removed

**File:** [bot_api.py](file:///C:/transport-report-staging/bot_api.py)

```python
from bot_security import (
    utcnow, verify_secret, hash_secret, hash_api_token,
    make_api_token, parse_datetime_safe,
)
```

---

## Files Changed

### New Files (BOT001A)

| File | Purpose |
|------|---------|
| [bot_security.py](file:///C:/transport-report-staging/bot_security.py) | Cryptographic helpers (stdlib only) |
| [bot_api.py](file:///C:/transport-report-staging/bot_api.py) | Flask Blueprint `/api/bot/*` — 7 endpoints |
| [migrate_bot001_telegram_foundation.py](file:///C:/transport-report-staging/migrate_bot001_telegram_foundation.py) | Idempotent SQLite migration (corrected) |
| [test_bot001_readonly.py](file:///C:/transport-report-staging/test_bot001_readonly.py) | Strict read-only tests (13 checks) |
| [test_bot001_staging_api.py](file:///C:/transport-report-staging/test_bot001_staging_api.py) | Strict API tests (13 checks) |
| [BOT001_STAGING_INSTALL.txt](file:///C:/transport-report-staging/BOT001_STAGING_INSTALL.txt) | Corrected installation guide |
| [BOT001_PRODUCTION_NOTES.txt](file:///C:/transport-report-staging/BOT001_PRODUCTION_NOTES.txt) | Production notes (read-only reference) |
| [docs/BOT001_TELEGRAM_FOUNDATION.md](file:///C:/transport-report-staging/docs/BOT001_TELEGRAM_FOUNDATION.md) | Updated documentation |

### Modified Files

| File | Change |
|------|--------|
| [app.py](file:///C:/transport-report-staging/app.py) | Added `app.register_blueprint(bot_api_bp)` + import fixes |
| [models.py](file:///C:/transport-report-staging/models.py) | 6 Telegram fields on `User`; 3 new model classes |
| [templates/admin_users.html](file:///C:/transport-report-staging/templates/admin_users.html) | TELEGRAM column + 🔗 link code button |

### ZIP Archive

[BOT001A_TELEGRAM_FOUNDATION_CORRECTED_20260606.zip](file:///C:/transport-report-staging/BOT001A_TELEGRAM_FOUNDATION_CORRECTED_20260606.zip)

---

## Verification Checks Run and Results

### 1. AST Syntax Check

```
OK: bot_security.py
OK: bot_api.py
OK: migrate_bot001_telegram_foundation.py
OK: models.py
OK: app.py
OK: test_bot001_readonly.py
OK: test_bot001_staging_api.py
ALL_SYNTAX_OK
```

### 2. Migration on Test DB Copy (SQLite backup API copy of staging DB)

```
[1/8] PRAGMA integrity_check (before)...
      integrity_check: OK
[2/8] Adding Telegram columns to 'users' table...
      Added users.telegram_id  (INTEGER)
      Added users.tg_notifications  (INTEGER NOT NULL DEFAULT 1)
      Added users.tg_quiet_hours  (VARCHAR(20))
      Added users.tg_link_code_hash  (VARCHAR(128))
      Added users.tg_link_code_expires_at  (DATETIME)
      Added users.tg_link_code_created_at  (DATETIME)
[3/8] Creating 'spare_part_status_history' table...
      Created spare_part_status_history.
[4/8] Creating 'bot_api_sessions' table...
      Created bot_api_sessions.
[5/8] Creating 'bot_notification_queue' table...
      Created bot_notification_queue.
[6/8] Creating indexes...
      Created UNIQUE partial index idx_users_telegram_id.
      Created index idx_bot_api_sessions_token_hash.
      Created index idx_bot_api_sessions_user_id.
      Created index idx_bot_notification_queue_status.
      Created index idx_bot_notification_queue_telegram_id.
      Created index idx_spare_part_status_history_request_id.
[7/8] Recording migration in schema_migrations...
      Recorded: migrate_bot001_telegram_foundation
[8/8] PRAGMA integrity_check (after)...
      integrity_check: OK
BOT001A migration complete.
Migration returned: True
```

### 3. Idempotency Check (migration run again on same DB)

```
[SKIP] Migration 'migrate_bot001_telegram_foundation' already recorded in schema_migrations.
       Re-checking all indexes are present anyway (idempotent)...
[2/8] ... Column users.telegram_id already exists. Skipping. (x6)
[3/8] ... Table spare_part_status_history already exists. Skipping.
[4/8] ... Table bot_api_sessions already exists. Skipping.
[5/8] ... Table bot_notification_queue already exists. Skipping.
[6/8] ... Index idx_users_telegram_id already exists. Skipping. (x6)
[8/8] integrity_check: OK
BOT001A migration complete.
Idempotency check returned: True
```

### 4. App Import + Route Verification

```
APP_IMPORT_OK (db.create_all skipped for route check)
Bot routes: ['/api/bot/catalog', '/api/bot/equipment', '/api/bot/health',
             '/api/bot/link/verify', '/api/bot/me', '/api/bot/requests',
             '/api/bot/requests/<int:rid>']
  FOUND: /api/bot/health
  FOUND: /api/bot/me
  FOUND: /api/bot/requests
  FOUND: /api/bot/equipment
  FOUND: /api/bot/catalog
  FOUND: /api/bot/link/verify
ALL_BOT_ROUTES_OK
```

> **Confirmation:** `app.register_blueprint(bot_api_bp)` is present and working. All required routes appear in `url_map`.

### 5. API Tests (13/13) — against migrated test DB copy

```
[OK]   All /api/bot/* routes in url_map
[OK]   GET /api/bot/health => 200, ok=true, version=BOT001
[OK]   GET /api/bot/me (no token) => 401
[OK]   GET /api/bot/requests (no token) => 401
[OK]   GET /api/bot/equipment (no token) => 401
[OK]   GET /api/bot/catalog (no token) => 401
[OK]   GET /api/bot/requests/99999 (no token) => 401
[OK]   POST /api/bot/link/verify (bad body) => 400
[OK]   POST /api/bot/link/verify (wrong code) => 401
[OK]   GET /api/bot/health (bad Bearer) => 200 (public)
[OK]   GET /api/bot/me (bad Bearer) => 401
[OK]   /api/bot/health has no sensitive fields
[OK]   POST /api/bot/link/verify (bad tg_id type) => 400

=======================================================
Passed: 13/13

BOT001_STAGING_API_TEST_OK
```

### 6. Security and Reference Checks (18/18)

```
[OK]   No real Telegram secrets in config.py
[OK]   No 10.103.25.200 in: bot_api.py
[OK]   No 10.103.25.200 in: bot_security.py
[OK]   No 10.103.25.200 in: migrate_bot001_telegram_foundation.py
[OK]   No 10.103.25.200 in: test_bot001_readonly.py
[OK]   No 10.103.25.200 in: test_bot001_staging_api.py
[OK]   No 10.103.25.200 in: BOT001_STAGING_INSTALL.txt
[OK]   No 10.103.25.200 in: BOT001_PRODUCTION_NOTES.txt
[OK]   No 10.103.25.200 in: docs\BOT001_TELEGRAM_FOUNDATION.md
[OK]   bot_api_bp imported in app.py
[OK]   app.register_blueprint(bot_api_bp) found in app.py
[OK]   Migration: telegram_id is plain INTEGER (no UNIQUE constraint on column)
[OK]   Migration: UNIQUE partial index for telegram_id present
[OK]   bot_api.py: or_ imported from sqlalchemy
[OK]   bot_api.py: no db.or_ usage
[OK]   bot_api.py: verify_secret imported exactly once
[OK]   No production path in: bot_api.py
[OK]   No production path in: bot_security.py

ALL_SECURITY_CHECKS_OK
```

### 7. py_compile Note

`py_compile -m` shows `PermissionError: [WinError 5]` when trying to write `.pyc` files to `__pycache__\` while the staging service holds a write lock on the directory. This is a Windows file lock behavior — **not a syntax error**. All files validated via `ast.parse()` with zero errors.

---

## Production Was NOT Touched

- `C:\transport-report` — not modified
- `TransportReport` service — not restarted or modified
- `C:\transport-report\instance\transport.db` — not modified
- `10.103.25.200` — not used (old rollback standby, not production)

---

## Rollback Notes (Staging)

```cmd
nssm.exe stop TransportReportStaging
copy /Y instance\transport.db.backup_before_bot001a_YYYYMMDD instance\transport.db
copy /Y app.py.backup_before_bot001a                          app.py
copy /Y models.py.backup_before_bot001a                       models.py
copy /Y templates\admin_users.html.backup_before_bot001a      templates\admin_users.html
nssm.exe start TransportReportStaging
```

---

## Next Steps

1. Follow [BOT001_STAGING_INSTALL.txt](file:///C:/transport-report-staging/BOT001_STAGING_INSTALL.txt) step by step
2. Stop `TransportReportStaging`, apply migration, run all tests
3. Confirm `BOT001_READONLY_TEST_OK` and `BOT001_STAGING_API_TEST_OK` with service stopped
4. Browser smoke test: http://10.103.25.14:5051/api/bot/health
5. For production: follow [BOT001_PRODUCTION_NOTES.txt](file:///C:/transport-report-staging/BOT001_PRODUCTION_NOTES.txt) — separate manual step
6. Plan BOT002: full `bot.py` Telegram runner
