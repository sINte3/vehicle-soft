# BOT002 Telegram Runner

**Module**: BOT002A — Telegram Bot Runner (Long Polling)
**Project**: Vehicle Soft / Transport Report — Bukhoro Agrocluster
**Date**: 2026-06-08
**API version**: BOT002A
**Previous**: BOT001A (foundation), BOT001B (inactive-user guard)

---

## What BOT002A Does

BOT002A delivers the first working Telegram bot runner for the Spare Parts module.

### Flask API (bot_api.py)

Adds the `POST /api/bot/logout` endpoint to the existing BOT001 API:

- Revokes the current `BotApiSession` (sets `revoked_at`)
- Clears `users.telegram_id` for that user
- Clears `tg_link_code_hash`, `tg_link_code_expires_at`, `tg_link_code_created_at`
- Returns `{"ok": true, "message": "Telegram account unlinked."}`
- Missing or invalid token returns HTTP 401
- No DB migration required (uses existing BOT001 fields)

### Bot Runner (bot.py)

A standalone Telegram bot using long polling (`python-telegram-bot==20.7`).  
No webhook. No HTTPS. No Mini App.

### Support Modules (stdlib only)

| File | Purpose |
|---|---|
| `bot_config.py` | Environment variables → `BotSettings` dataclass |
| `bot_state.py` | Local SQLite session storage (`instance/bot_state.db`) |
| `bot_http_client.py` | HTTP GET/POST via `urllib` (no external deps) |
| `bot_formatters.py` | Russian-language message formatters |

---

## What BOT002A Does NOT Do

- No request creation via Telegram
- No status change via Telegram
- No photo upload via Telegram
- No webhook
- No HTTPS
- No Telegram Mini App
- No production deployment (staging-verified package only)
- No DB migration

---

## Telegram Commands

| Command | Description |
|---|---|
| `/start` | Welcome; shows menu if linked, prompts to link if not |
| `/help` | List all available commands |
| `/menu` | Show main menu with inline keyboard |
| `/link <code>` | Link account using 6-digit one-time code from admin panel |
| `/status` | Show last 5 spare part requests |
| `/pending` | Show submitted requests (admin role only) |
| `/logout` | Revoke API session and unlink Telegram account |

---

## Linking Flow

1. Admin generates a 6-digit code in the web admin panel (User → Telegram Code)
2. User sends `/link 123456` to the bot
3. Bot calls `POST /api/bot/link/verify` with `{telegram_id, code}`
4. On success: `api_token` is returned, saved to local `bot_state.db`
5. User's `telegram_id` is written to `users` table
6. Code fields (`tg_link_code_hash`, etc.) are cleared immediately
7. Session is valid for 30 days

**Validation**:
- Code must be exactly 6 digits
- Code verified with constant-time HMAC comparison (no timing attacks)
- Inactive users receive HTTP 403 (no token issued)
- Duplicate `telegram_id` returns HTTP 409

---

## Logout / Unlink Behavior

### When the user sends `/logout`:

1. Bot calls `POST /api/bot/logout` with Bearer token
2. Server revokes the session (`bot_api_sessions.revoked_at` is set)
3. Server clears `users.telegram_id` for that user
4. Server clears any pending link code fields
5. Bot deletes local session from `bot_state.db`
6. Bot confirms logout to the user

**Robustness**: Local session is deleted even if the API call fails
(e.g. token already expired/revoked). The bot cannot get stuck in a
linked state locally.

**Missing token**: `POST /api/bot/logout` without Authorization header returns HTTP 401.
404 is never returned after BOT002A is deployed.

---

## Environment Variables

| Variable | Default | Required |
|---|---|---|
| `TG_BOT_TOKEN` | (none) | **Yes** — at bot runtime only |
| `BOT_API_BASE_URL` | `http://127.0.0.1:5051` | No |
| `BOT_STATE_DB` | `instance\bot_state.db` | No |
| `BOT_LOG_DIR` | `logs` | No |
| `BOT_REQUEST_TIMEOUT_SECONDS` | `10` | No |

**Never store `TG_BOT_TOKEN` in code, config files, or git.**  
Set it as a NSSM service environment variable only.

---

## Local bot_state.db Security Note

`bot_state.db` is a local SQLite file that stores:
- `telegram_id` → `api_token` mapping
- User profile cache (username, full_name, role)

**Security considerations**:
- The file contains raw `api_token` values
- Protect it with filesystem permissions (not world-readable)
- Default location: `instance\bot_state.db` (same dir as Flask app DB)
- The `instance\` directory should be restricted to the service account
- Tokens are never logged or printed by any bot module

If `bot_state.db` is compromised, call `POST /api/bot/logout` for all
affected sessions, or revoke sessions directly in the `bot_api_sessions` table.

---

## Why No HTTPS Is Required for Long Polling

`python-telegram-bot` uses long polling: the bot **reaches out** to Telegram's
servers and pulls updates. The bot does not expose any inbound port.

- Telegram's servers only communicate with bots that connect to them
- No external connections are made to the bot server
- The Flask API (`/api/bot/*`) is called over localhost only (`127.0.0.1`)
- No external HTTPS, Cloudflare Tunnel, or reverse proxy is needed

This is fundamentally different from webhooks (where Telegram calls your server).

---

## API Endpoint Summary (After BOT002A)

| Endpoint | Method | Auth | Description |
|---|---|---|---|
| `/api/bot/health` | GET | None | Health check |
| `/api/bot/link/verify` | POST | None | Verify one-time code, issue token |
| `/api/bot/me` | GET | Bearer | Current user profile |
| `/api/bot/requests` | GET | Bearer | List spare part requests |
| `/api/bot/requests/<id>` | GET | Bearer | Single request detail |
| `/api/bot/equipment` | GET | Bearer | Equipment list |
| `/api/bot/catalog` | GET | Bearer | Spare parts catalog |
| `/api/bot/logout` | POST | Bearer | **NEW in BOT002A** — revoke session |

---

## Future: BOT003 / BOT004

**BOT003** (planned):
- Submit new spare part requests via bot
- Photo attachment to requests
- Request status notifications push

**BOT004** (planned):
- Admin approve/reject requests via bot
- Bulk notification delivery
- Request statistics summary

---

## Notes on Previous BOT002 (Rejected)

The previous BOT002 package was rejected for the following reasons:

1. The ZIP referenced a file by name that was missing from the ZIP itself
2. A test accepted HTTP 404 for the logout endpoint as acceptable (not an error)
3. The report and test files contradicted each other

**BOT002A fixes all of these**:
- No missing-file references in BOT002A
- The updated bot_api content is in `bot_api.py` (present in the ZIP)
- `test_bot002_api_contract.py` requires exactly 401 for `/api/bot/logout`; 404 is FAILURE
- The report matches the actual files
