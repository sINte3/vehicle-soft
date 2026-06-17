# CSRF-AUDIT-001 - CSRF coverage audit

Date: 2026-06-17
Type: security audit
Scope: read-only discovery and source review
Status: PASS - no code changes required

## Baseline

- Repository: C:\transport-report-staging
- Baseline commit: 255a904
- Branch: main
- Production was not touched.
- No files, database rows, git state, or services were changed during discovery.

## What was checked

- Global CSRF token generation.
- Global POST request CSRF enforcement.
- CSRF exempt path rules.
- Python POST routes in app.py, fuel_routes.py, spare_parts.py, wialon_import.py, bot_api.py.
- HTML templates under templates.
- Browser POST forms and hidden CSRF token presence.
- API POST routes intentionally exempt from browser-session CSRF.

## Findings

- app.py defines get_csrf_token().
- app.py registers csrf_token as a Jinja global.
- app.py defines is_csrf_exempt().
- app.py enforces CSRF for normal POST requests in before_request.
- Submitted CSRF token is accepted from form csrf_token, X-CSRF-Token, or X-CSRFToken.
- CSRF comparison uses hmac.compare_digest.
- POST forms found: 52.
- POST forms with CSRF marker: 52.
- POST forms without CSRF marker: 0.
- POST routes found: 46.
- Potential risk item count: 0.

## CSRF exempt paths

- /fuel/api/fuel_sync
- /api/fuel_sync
- /api/bot/*

## API protection review

- /fuel/api/fuel_sync and /api/fuel_sync use shared _perform_fuel_sync() logic.
- Fuel sync validates JSON payload, submitted token, configured FUEL_API_TOKEN, and hmac.compare_digest.
- /api/bot/link/verify uses one-time Telegram link code verification.
- Bearer-token protected bot endpoints use Authorization: Bearer token.
- Bot API tokens are stored as hashes in bot_api_sessions.
- Expired, revoked, missing, or inactive-user bot sessions are rejected.
- /api/bot/logout requires a valid Bearer token.

## Conclusion

CSRF-AUDIT-001 passed.

No code changes are required.

## Operational notes

- No database changes.
- No migrations.
- No production pull.
- No service restart.
- Bot services were not restarted.
- Web services were not restarted.

## Future rule

- Any future browser POST form must include a hidden csrf_token field.
- Any future /api/bot/* POST endpoint must include explicit API authentication or one-time-code validation because /api/bot/* is CSRF-exempt by design.
