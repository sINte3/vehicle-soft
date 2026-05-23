# AUDIT_REPORT.md — Introductory Audit

## Scope

Reviewed:

- Current `transport-report.zip` source tree and SQLite database.
- Project instructions.
- Four Claude session exports.

Not performed:

- Full browser end-to-end test, because runtime dependencies were not installed in the audit container.
- Destructive database tests.
- Live Topaz/Wialon connectivity tests.

## Status of materials

Available:

- Project archive.
- Current instructions.
- `claude-session-01.md`.
- `claude-session-02.md`.
- `claude-session-03.md`.
- `claude-session-04.md`.

Clarification:

- Original prompt mentioned five Claude sessions, but user clarified that there are four.

## Code audit: top findings

### [КРИТИЧНО] Module permissions are stored but not enforced centrally

Files:

- `app.py`: admin permission UI at lines 695–724.
- `models.py`: `AppModule`, `UserModulePermission` at lines 485–500.
- Module routes still use `editor_required`, `admin_required`, `login_required` or custom fuel admin check.

Impact:

- Admin can configure module permissions, but users may still access module URLs directly if their role allows it.

Fix:

- Add `module_required(module_code)` and apply to Wialon, Fuel, Deficiencies and Spare Parts.

### [КРИТИЧНО] Hardcoded secrets/tokens

Files:

- `config.py:11`: default `SECRET_KEY` fallback.
- `fuel_routes.py:26`: `API_TOKEN = '<REDACTED_LEGACY_FUEL_API_TOKEN>'`.

Impact:

- Predictable Flask session key if environment variable is not set.
- Fuel sync token visible in source and project archive.

Fix:

- Move secrets to environment variables and document Windows setup.

### [ВАЖНО] Fuel sync API route ambiguity

Files:

- `fuel_routes.py:24`: blueprint prefix `/fuel`.
- `fuel_routes.py:450`: route `/api/fuel_sync`.

Actual URL:

- `/fuel/api/fuel_sync`

Risk:

- Historical instructions and agent comments mention `/api/fuel_sync`; this can silently break Topaz agent sync.

Fix:

- Standardize URL or add backward-compatible alias.

### [ВАЖНО] No visible CSRF protection for POST forms

Files:

- Many POST routes in `app.py`, `wialon_import.py`, `fuel_routes.py`, `spare_parts.py`.

Impact:

- If the app becomes reachable beyond trusted LAN, CSRF risk becomes material.

Fix:

- Add CSRF before public internet exposure; evaluate Windows/simple deployment impact.

### [ВАЖНО] Runtime `db.create_all()` and many manual migrations

Files:

- `app.py:1009–1020` uses `db.create_all()` and seeds modules.
- Multiple migration scripts exist in root.

Impact:

- Schema state can drift.
- Double-running scripts may be unsafe unless each script is idempotent.

Fix:

- Add migration registry or adopt Alembic/Flask-Migrate after stabilization.

### [ВАЖНО] Fuel v1/v2 tables coexist

Tables observed:

- v1: `fuel_stations`, `fuel_snapshots`, `fuel_transactions`, `fuel_sync_logs`, `fuel_balances`, `fuel_receipts`.
- v2: `fuel_warehouses`, `fuel_stations2`, `fuel_initial_balances`, `fuel_receipts2`, `fuel_transactions2`, `fuel_sync_logs2`.

Impact:

- Developers may query or display wrong table set.
- Backup/archive size grows.

Fix:

- Document v1 as legacy, keep read-only, plan cleanup only after verified migration.

### [УЛУЧШЕНИЕ] Large mixed-responsibility modules

Files:

- `wialon_import.py` handles parsing, DB writes, routes and Excel export.
- `app.py` still contains many unrelated concerns.

Impact:

- Higher risk of regression when adding Wialon or report features.

Fix:

- Gradually split into `services/`, `routes/`, `reports/` after urgent tasks.

## Positive findings

- Current Python files compile syntactically.
- Last visible category display bug appears fixed in current `daily_entry.html`.
- Wialon `wialon_bp` crash source no longer exists in current source.
- Equipment categories and DB counts match the 9-category migration goal.
- `workload_report.py` is separated from Wialon import logic, which is a good pattern to continue.

## Recommended next development action

Do not begin finance or spare parts expansion yet.

First implement:

1. `TASK-SEC-001`: enforce module permissions.
2. `TASK-SEC-002`: move secrets/tokens to environment variables.
3. `TASK-FUEL-001`: standardize fuel sync endpoint.

These are safer foundational tasks before expanding ERP functionality.
