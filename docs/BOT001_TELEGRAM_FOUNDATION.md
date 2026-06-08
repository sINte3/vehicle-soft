# BOT001A Telegram Foundation (corrected patch) — Updated for BOT001B

**Project:** Vehicle Soft / Transport Report — Bukhoro Agrocluster  
**Patch:** BOT001A + BOT001B (inactive-user guard)  
**Date:** 2026-06-08  
**Staging:** http://10.103.25.14:5051  
**Production:** http://10.103.25.14:5050 — separate manual deployment step required

---

## Corrections in BOT001A vs BOT001

| # | Issue | Fix |
|---|-------|-----|
| 1 | `bot_api_bp` was imported in `app.py` but never registered | Added `app.register_blueprint(bot_api_bp)` inside `create_app()` |
| 2 | Migration added `telegram_id INTEGER UNIQUE` — unsupported by SQLite `ALTER TABLE ADD COLUMN` | Changed to `INTEGER`; uniqueness enforced by separate UNIQUE partial index |
| 3 | `idx_users_telegram_id` was a plain index | Changed to `CREATE UNIQUE INDEX ... WHERE telegram_id IS NOT NULL` |
| 4 | Staging install guide placed APP IMPORT CHECK before migration | Corrected order: stop → backup → migrate → import check → tests → start |
| 5 | Production notes used `copy` of live DB as backup | Now uses `backup_production_db.bat` (safe SQLite backup API) |
| 6 | Tests used `warn()` to mask failures when service is running | Tests now strictly PASS/FAIL; require service stopped + migration done |
| 7 | `bot_api.py` used `db.or_()` | Changed to `or_` imported from `sqlalchemy` |
| 8 | Duplicate `verify_secret` in `bot_api.py` imports | Deduped; clean single import block |

---

## BOT001B — Inactive-User Guard

**Defect found in BOT001A smoke test:**  
`POST /api/bot/link/verify` accepted a link code belonging to an inactive user, returned `api_token` with `user.is_active=false`, then `GET /api/bot/me` rejected the same token with 401 because `_resolve_bearer_token()` blocks inactive users.

**Root cause:** No `is_active` check was performed before issuing the token in `link_verify`.

### What Changed in BOT001B

| File | Change |
|------|--------|
| `bot_api.py` | Added inactive-user guard in `link_verify` after duplicate `telegram_id` check, before session creation |
| `app.py` | Added inactive-user guard in `admin_telegram_link_code` route: refuses to generate code for inactive users |
| `templates/admin_users.html` | Inactive users: show disabled grey 🔗 button with tooltip; active users: normal link form |
| `test_bot001_staging_api.py` | Check 14: inactive user link attempt → 403, no token, no session row, no telegram_id written |
| `test_bot001_readonly.py` | Check 14: source-level check that `bot_api.py` contains inactive-user guard |
| `test_bot001b_link_smoke.py` | Full smoke test: Scenario A (inactive → 403) and Scenario B (active → 200, /me, /requests) with cleanup |

### Inactive-User Guard Behavior

**`POST /api/bot/link/verify` (BOT001B guard):**

- Code is found and not expired ✓
- Code belongs to an **inactive** user → HTTP 403:
  ```json
  {"ok": false, "error": "User is inactive. Telegram linking is not allowed."}
  ```
- No `api_token` is returned.
- No `bot_api_sessions` row is created.
- `users.telegram_id` is NOT written.
- The link code is left intact (not cleared); it expires naturally after 10 minutes.

**`POST /admin/users/<uid>/telegram-link-code` (BOT001B admin guard):**

- If `user.is_active` is False → flash warning message, redirect, no code generated:
  > "Telegram код нельзя создать для заблокированного пользователя."
- `tg_link_code_hash` is NOT written.
- Audit log entry is NOT created.

**Admin UI (BOT001B):**

- Inactive users: disabled grey 🔗 button with tooltip "Пользователь заблокирован"
- Active users without `telegram_id`: active link form (same as before)
- Active users with `telegram_id`: linked status display + active re-link form (same as before)

---

## What Was Added

### New Python Files

| File | Purpose |
|------|---------|
| `bot_security.py` | Cryptographic helpers: `generate_link_code`, `hash_secret`, `verify_secret`, `make_api_token`, `hash_api_token`, `parse_datetime_safe`, `utcnow`. Stdlib only (no external deps). |
| `bot_api.py` | Flask Blueprint `/api/bot/*` — 7 endpoints (read-only + link verification + inactive guard) |
| `migrate_bot001_telegram_foundation.py` | Idempotent SQLite migration |
| `test_bot001_readonly.py` | Strict read-only verification (14 checks including inactive guard source check) |
| `test_bot001_staging_api.py` | Strict API tests via Flask test_client (14 checks including inactive user 403 test) |
| `test_bot001b_link_smoke.py` | Full link smoke test (Scenario A: inactive 403; Scenario B: active 200 + cleanup) |
| `BOT001_STAGING_INSTALL.txt` | Step-by-step CMD installation guide (corrected order) |
| `BOT001_PRODUCTION_NOTES.txt` | Production deployment checklist (read-only reference) |
| `docs/BOT001_TELEGRAM_FOUNDATION.md` | This document |

### Modified Files

| File | Change |
|------|--------|
| `models.py` | Added 6 Telegram columns to `User`; added 3 new model classes |
| `app.py` | Import + register `bot_api_bp`; CSRF exempt `/api/bot/*`; admin link code route with inactive guard |
| `templates/admin_users.html` | TELEGRAM column: inactive users show disabled button |

---

## Database Changes

### `users` table — new columns

> **Important:** `telegram_id` is added as a plain `INTEGER` column (no `UNIQUE` constraint on the column itself). Uniqueness across non-NULL values is enforced by a separate UNIQUE partial index. This is the correct pattern for SQLite `ALTER TABLE ADD COLUMN`.

| Column | Type | Notes |
|--------|------|-------|
| `telegram_id` | `INTEGER NULL` | Added as plain INTEGER; uniqueness via partial index |
| `tg_notifications` | `INTEGER NOT NULL DEFAULT 1` | 1 = on, 0 = off |
| `tg_quiet_hours` | `VARCHAR(20) NULL` | e.g. `"22:00-07:00"` |
| `tg_link_code_hash` | `VARCHAR(128) NULL` | SHA-256 of one-time code (never stored plain) |
| `tg_link_code_expires_at` | `DATETIME NULL` | Expiry (10 min from admin generation) |
| `tg_link_code_created_at` | `DATETIME NULL` | Generation timestamp |

### New Tables

- `spare_part_status_history` (7 columns)
- `bot_api_sessions` (8 columns)
- `bot_notification_queue` (11 columns)

### Indexes Created

| Index | Type | Target |
|-------|------|--------|
| `idx_users_telegram_id` | **UNIQUE PARTIAL** | `users(telegram_id) WHERE telegram_id IS NOT NULL` |
| `idx_bot_api_sessions_token_hash` | Regular | `bot_api_sessions(token_hash)` |
| `idx_bot_api_sessions_user_id` | Regular | `bot_api_sessions(user_id)` |
| `idx_bot_notification_queue_status` | Regular | `bot_notification_queue(status)` |
| `idx_bot_notification_queue_telegram_id` | Regular | `bot_notification_queue(telegram_id)` |
| `idx_spare_part_status_history_request_id` | Regular | `spare_part_status_history(request_id)` |

---

## API Endpoints (BOT001A + BOT001B)

### Public (no auth required)

| Method | Path | Description |
|--------|------|-------------|
| GET | `/api/bot/health` | Health check, returns `{"ok":true,"module":"bot_api","version":"BOT001"}` |

### Auth: Bearer token (obtained via link/verify)

| Method | Path | Description |
|--------|------|-------------|
| POST | `/api/bot/link/verify` | Verify one-time code, receive API token. **403** if user is inactive. |
| GET | `/api/bot/me` | Current user profile |
| GET | `/api/bot/requests` | Spare part requests (paginated) |
| GET | `/api/bot/requests/<id>` | Request detail + items + history |
| GET | `/api/bot/equipment` | Active equipment list |
| GET | `/api/bot/catalog` | Spare parts catalog search |

---

## Security Design

### One-Time Telegram Link Code Flow

1. Admin opens `/admin/users`, clicks 🔗 for a user → calls `POST /admin/users/<uid>/telegram-link-code`
2. **BOT001B:** If user is inactive → flash warning, no code generated, redirect
3. Server generates 6-digit code via `secrets.randbelow()` (cryptographically random)
4. Only the **SHA-256 hash** of the code is stored in `users.tg_link_code_hash`; expiry set to 10 min
5. The **plain code** is shown to admin **once** in a flash message — never logged
6. Admin tells the user their code via a separate channel (verbally or secure message)
7. Telegram bot sends `POST /api/bot/link/verify` with `{"telegram_id": ..., "code": "..."}`
8. Server iterates users with active codes, verifies with `hmac.compare_digest` (constant-time)
9. **BOT001B:** If matching user is inactive → HTTP 403, no token, no writes
10. On success: writes `telegram_id`, clears code fields, creates `bot_api_sessions` record
11. Bot receives raw `api_token` **once** — stores it locally; server stores only its SHA-256 hash

### What Is Explicitly Not Used

- **Login via Telegram username+password** — not implemented and not planned. Explicitly prohibited.
- **Credentials in config.py** — all secrets come from environment variables only
- **Raw tokens in DB** — only SHA-256 hashes stored in `bot_api_sessions.token_hash`
- **Plain link codes in DB** — only SHA-256 hashes stored in `users.tg_link_code_hash`
- **Tokens for inactive users** — BOT001B: inactive users receive 403, no token is ever issued

### Expired and Revoked Sessions

- `_resolve_bearer_token()` in `bot_api.py` checks `revoked_at IS NOT NULL` and `expires_at` before accepting a session
- `last_used_at` is updated **only** after the token passes all validity checks

---

## Production Deployment

**BOT001A+BOT001B is NOT automatically deployed to production.**

Production deployment requires a separate manual checklist (see `BOT001_PRODUCTION_NOTES.txt`).

Key rules:
- Use `backup_production_db.bat` for production DB backup (not a raw file copy of a live DB)
- Stop the production service **before** the backup
- Run the migration **after** the backup, while the service is still stopped
- Run the APP IMPORT CHECK **after** the migration (not before)
- Do NOT touch `C:\transport-report` or restart `TransportReport` service during staging work

---

## What Is NOT in BOT001A+BOT001B (future patches)

| Feature | Patch |
|---------|-------|
| bot.py — Telegram bot runner | BOT002 |
| NSSM service for bot.py | BOT002 |
| Creating spare part requests via bot | BOT002 |
| Status transitions via bot | BOT003 |
| Push notifications | BOT002 |
| Cloudflare Tunnel | Deployment phase |
| Daily digest / anomaly summary | BOT004 |
| Telegram Mini App (WebApp) | Not in current scope |
