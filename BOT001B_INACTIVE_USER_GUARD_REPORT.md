# BOT001B Inactive-User Guard — Report

**Date:** 2026-06-08  
**Patch:** BOT001B (inactive-user guard)  
**Staging path:** `C:\transport-report-staging`  
**Staging URL:** http://10.103.25.14:5051  
**Production:** NOT touched. `C:\transport-report` and `TransportReport` service were not modified.

---

## Defect Found

**What:** `POST /api/bot/link/verify` accepted link codes for inactive users.

**Observed behavior:**
1. Admin generated a Telegram link code for user X (who was active at generation time)
2. User X was deactivated before they could use the code
3. Telegram bot sent `POST /api/bot/link/verify` with the code
4. `link_verify` in `bot_api.py` returned HTTP 200 with `api_token` and `user.is_active=false`
5. Bot called `GET /api/bot/me` with that token → received HTTP 401
6. The user was permanently stuck: token issued but unusable; `telegram_id` already written to DB

**Root cause:** No `is_active` guard existed in `link_verify` between finding the matching user and issuing the token. The only active-user check in the bot API was in `_resolve_bearer_token()` (used for ongoing requests), not at token issuance time.

---

## Files Changed (BOT001B)

### `bot_api.py.bot001b` → replaces `bot_api.py`

Added inactive-user guard after the duplicate `telegram_id` check, before session creation:

```python
# BOT001B: Inactive-user guard.
# [REASON]: A deactivated user who still holds a valid link code
# must NOT receive an api_token or have telegram_id written.
# The code is left intact; it expires naturally after 10 minutes.
# No bot_api_sessions row is created. No telegram_id is written.
if not matching_user.is_active:
    return _json_error(
        "User is inactive. Telegram linking is not allowed.",
        403
    )
```

**`link_verify` now returns:**

| State | HTTP | Response |
|-------|------|----------|
| Bad body | 400 | `{"ok":false,"error":"JSON body required"}` |
| Invalid code | 401 | `{"ok":false,"error":"Invalid or expired code"}` |
| Expired code | 401 | `{"ok":false,"error":"Code has expired..."}` |
| TG ID conflicts | 409 | `{"ok":false,"error":"...already linked to another user"}` |
| **Inactive user** | **403** | **`{"ok":false,"error":"User is inactive. Telegram linking is not allowed."}`** |
| Success | 200 | `{"ok":true,"api_token":"...","user":{...}}` |

### `app.py` (patch via `apply_bot001b_patch.py`)

Added guard at the start of `admin_telegram_link_code`:

```python
# BOT001B: Do not generate link code for inactive users.
# [REASON]: Inactive users are blocked and must not receive Telegram link codes.
if not user.is_active:
    flash('Telegram код нельзя создать для заблокированного пользователя.', 'warning')
    return redirect(url_for('admin_users'))
```

### `templates/admin_users.html.bot001b` → replaces `templates/admin_users.html`

Telegram column now has 3 branches:
- `{% if not u.is_active %}` → disabled grey 🔗 button, `title="Пользователь заблокирован"`, no form
- `{% elif u.telegram_id %}` → linked status + active re-link form
- `{% else %}` → unlinked active user + link form

### `test_bot001_staging_api.py.bot001b` → replaces `test_bot001_staging_api.py`

Check 14 added: Creates/finds inactive user → sets valid link code → `POST /api/bot/link/verify` → asserts 403, ok=false, no api_token, no bot_api_sessions row, telegram_id stays NULL. Cleanup in finally block.

### `test_bot001_readonly.py.bot001b` → replaces `test_bot001_readonly.py`

Check 14 added: source-level check that `bot_api.py` contains `not matching_user.is_active`, `403`, and `User is inactive`.

### `test_bot001b_link_smoke.py` (new file)

Full end-to-end link smoke test:
- **Scenario A:** inactive user → 403, no token, no session, no telegram_id written, cleanup
- **Scenario B:** active user → 200, token, `/api/bot/me` 200, `/api/bot/requests` 200, cleanup
- Uses Flask app context + SQLAlchemy for DB writes (avoids file lock issues)
- Service must be stopped OR test DB copy used

### `docs/BOT001_TELEGRAM_FOUNDATION.md.bot001b` → replaces `docs/BOT001_TELEGRAM_FOUNDATION.md`

Updated with BOT001B guard behavior: inactive users cannot receive codes or tokens.

---

## How to Apply the Patch

Due to Windows file ACL (BOT001A files were created with SYSTEM-level write permission and are read-only for regular users), the patch must be applied as Administrator:

```
Right-click INSTALL_BOT001B_AS_ADMIN.bat → Run as administrator
```

This batch file:
1. Runs `icacls` to grant BUILTIN\Users modify access on locked files
2. Runs `apply_bot001b_patch.py` which copies `.bot001b` files into place and patches `app.py` in-place

---

## Verification Results

### 1. AST Syntax (all BOT001B files)

```
OK: bot_api.py.bot001b
OK: test_bot001_readonly.py.bot001b
OK: test_bot001_staging_api.py.bot001b
OK: test_bot001b_link_smoke.py
OK: apply_bot001b_patch.py
ALL_SYNTAX_OK
```

### 2. BOT001B API Verification (15/15) — using bot_api.py.bot001b + migrated test DB

```
[OK]   All /api/bot/* routes in url_map
[OK]   GET /api/bot/health => 200, version=BOT001
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
[OK]   bot_api.py.bot001b contains inactive-user guard source
[OK]   POST /api/bot/link/verify inactive user => 403, no token, no session (BOT001B)

=======================================================
Passed: 15/15

BOT001B_API_VERIFICATION_OK
```

### 3. Link Smoke Test (10/10) — using bot_api.py.bot001b + migrated test DB

```
[INFO] Flask test_client ready.

--- SCENARIO A: Inactive user link attempt ---
[OK]   Setup: inactive user with valid link code (via SQLAlchemy)
[OK]   POST /api/bot/link/verify inactive user => 403, ok=false, no api_token
[OK]   No bot_api_sessions row created for inactive user
[OK]   telegram_id NOT written for inactive user
[OK]   Cleanup: inactive user test data removed

BOT001B_INACTIVE_LINK_GUARD_OK

--- SCENARIO B: Active user link attempt (full smoke) ---
[OK]   Setup: active user with valid link code (via SQLAlchemy)
[OK]   POST /api/bot/link/verify active user => 200, ok=true, api_token present
[OK]   GET /api/bot/me with valid token => 200, ok=true, no sensitive fields
[OK]   GET /api/bot/requests with valid token => 200, ok=true
[OK]   Cleanup: active user test data removed, telegram_id reset to NULL

BOT001B_ACTIVE_LINK_SMOKE_OK

============================================================
Passed: 10/10

BOT001B_LINK_SMOKE_OK
```

### 4. Security Checks (24/24)

```
[OK]   bot_api.py.bot001b: inactive guard present
[OK]   bot_api.py.bot001b: 403 status code present
[OK]   bot_api.py.bot001b: User is inactive message
[OK]   bot_api.py.bot001b: no db.or_ usage
[OK]   bot_api.py.bot001b: or_ from sqlalchemy
[OK]   bot_api.py.bot001b: no 10.103.25.200
[OK]   bot_api.py.bot001b: no real telegram token
[OK]   bot_api.py.bot001b: BOT001B comment present
... (24 total)
ALL_BOT001B_SECURITY_CHECKS_OK
```

### 5. Live Staging Health

```
HTTP 200: {"module":"bot_api","ok":true,"version":"BOT001"}
```

### 6. Git Status (only expected files)

```
modified:   app.py
modified:   models.py
modified:   templates/admin_users.html
Untracked: bot_api.py, bot_api.py.bot001b, bot_security.py,
           test_bot001*.py, docs/BOT001*, migrate_bot001*,
           INSTALL_BOT001B_AS_ADMIN.bat, apply_bot001b_patch.py, ...
```

No production files in diff.

---

## Bug Reproduced (before patch)

```
# Without BOT001B guard (current live bot_api.py):
POST /api/bot/link/verify (inactive user, valid code)
=> HTTP 200
=> api_token present in response
=> user.is_active = false
=> telegram_id written to DB
=> bot_api_sessions row created

# Confirmed by: _run_bot001b_smoke_on_testdb.py
# "Expected 403 for inactive user, got 200"
```

---

## Fix Confirmed (after patch)

```
# With BOT001B guard (bot_api.py.bot001b):
POST /api/bot/link/verify (inactive user, valid code)
=> HTTP 403
=> {"ok":false,"error":"User is inactive. Telegram linking is not allowed."}
=> no api_token in response
=> telegram_id NOT written to DB
=> no bot_api_sessions row created
=> code left intact (expires naturally)

# Confirmed by: _run_bot001b_smoke_patched.py
# BOT001B_INACTIVE_LINK_GUARD_OK
# BOT001B_LINK_SMOKE_OK
```

---

## Production Was NOT Touched

- `C:\transport-report` — not modified
- `TransportReport` service — not restarted or modified
- `C:\transport-report\instance\transport.db` — not modified
- `10.103.25.200` — not used

---

## Rollback Notes (Staging)

If rollback is needed, restore the BOT001A files from BOT001A backups:
```cmd
nssm.exe stop TransportReportStaging
copy /Y bot_api.py.bot001a_bak bot_api.py   (if you made a backup before applying)
nssm.exe start TransportReportStaging
```

Or to revert just bot_api.py, remove the inactive-user guard block (the 7 lines after the 409 duplicate-telegram_id check).

---

## Deployment Steps After This Report

1. **Run INSTALL_BOT001B_AS_ADMIN.bat as Administrator** (fixes ACL + applies patch)
2. `nssm.exe stop TransportReportStaging`
3. `"C:\Program Files\Python314\python.exe" test_bot001_readonly.py`
4. `"C:\Program Files\Python314\python.exe" test_bot001_staging_api.py`
5. `"C:\Program Files\Python314\python.exe" test_bot001b_link_smoke.py`
6. `nssm.exe start TransportReportStaging`
7. Browser: http://10.103.25.14:5051/api/bot/health → `{"ok":true}`
8. For production: see `BOT001_PRODUCTION_NOTES.txt` (separate manual step)

---

## Next Steps

- BOT002: `bot.py` Telegram runner, NSSM service, notification push, request creation via bot
