# DECISIONS.md — Architectural Decisions

## ADR-001 — Use Flask + SQLite on Windows Server

Status: accepted.

Reason:

- Fast implementation for internal operational use.
- Low user count.
- No need for separate DB server at MVP stage.
- User can deploy with copy-paste Windows commands.

Consequences:

- SQLite write locking must be respected.
- Stop service before migrations.
- PostgreSQL migration may be needed for full ERP/public deployment.

## ADR-002 — Keep frontend simple

Status: accepted.

Decision:

- Jinja2 templates, vanilla JS, CSS.
- No React/Vue/Bootstrap dependency unless explicitly approved.

Reason:

- Easier deployment and debugging on Windows Server 2012 R2.
- Lower maintenance burden.

## ADR-003 — Excel output is business-critical

Status: accepted.

Decision:

- Preserve approved Excel formats and styling.
- Report changes require explicit business confirmation.

Reason:

- Reports are shown to management.
- Operators rely on familiar layout.

## ADR-004 — Expand equipment categories to 9

Status: implemented.

Decision:

- Use 9 inventory-aligned categories from consolidation Excel.
- Preserve existing daily records.
- Do not delete unmatched old data.

Consequence:

- Templates and reports must avoid hardcoded 3-category assumptions.

## ADR-005 — Keep main Excel report grouped for usability

Status: implemented.

Decision:

- Internal categories expanded to 9.
- Report grouping remains business-friendly:
  - tractors = `mtz`, `qatnov`, `mini`;
  - productive/special = `yukori`, `combine`, `special`;
  - transport/other = `yuk_transport`, `motorcycle`, `passenger`.

## ADR-006 — Wialon workload report is separate from main report

Status: implemented.

Decision:

- Do not merge workload into main daily revenue report.
- Provide separate web page and separate Excel export.

Reason:

- Workload metrics and revenue reports answer different questions.

## ADR-007 — Fuel warehouse equals organization

Status: implemented.

Decision:

- Fuel stock is tracked by organization/warehouse.
- Each station maps to a warehouse.
- Topaz transactions are deducted from the warehouse of the station.

Risk:

- API path and agent configuration must be standardized.

## ADR-008 — Module permission enforcement via `module_required` decorator

Status: completed 2026-05-22.

Decision:

- `User.has_module_access(module_code)` added to `models.py`. Admin always passes;
  non-admin requires an explicit `has_access=True` row in `user_module_permissions`.
- `module_required(module_code)` decorator factory defined in `models.py` using
  lazy Flask imports to avoid polluting model module scope.
- Applied to all wialon, fuel UI, spare parts, and deficiency routes.
- The Topaz agent API endpoint (`/fuel/api/fuel_sync`) is excluded from the module
  guard because it is a machine-to-machine endpoint protected by a token, not by
  a user session.
- Deny-by-default policy: if no permission record exists for a non-admin user,
  access is denied. The migration script grants all existing non-admin users
  access to all active modules to preserve current practical access.
- Navigation links in `templates/base.html` are also gated by module access:
  Wialon, Fuel/АЗС, spare parts, and deficiencies links are hidden for users
  without the corresponding permission (defense-in-depth alongside the 403 guard).
- `migrate_module_permissions.py` was executed successfully on production.
- Startup import error corrected: `generate_daily_activity` now imported from
  `excel_daily_activity` in `app.py`.

## ADR-010 — Secrets read from environment variables

Status: implemented 2026-05-22.

Decision:

- `SECRET_KEY` and `FUEL_API_TOKEN` are read exclusively from environment variables
  via `os.environ.get(...)`. No hardcoded production fallbacks.
- `run_server.py` fails fast with an ASCII-only error message if `SECRET_KEY` is
  absent, so NSSM logs show a clear cause instead of a silent Flask session failure.
- `DevelopmentConfig` retains a clearly-named dev-only fallback for `SECRET_KEY`
  so local development does not require environment setup.
- `FUEL_API_TOKEN` uses deny-all safe default: if the variable is not set, all
  `/fuel/api/fuel_sync` requests receive 401. The service still starts.
- No new Python dependencies introduced (standard `os.environ` only).

Deployment impact:

- **Operator must set `SECRET_KEY` and `FUEL_API_TOKEN` via `setx` before restarting
  the NSSM service.** See `docs/DEPLOYMENT_SECURITY.md` for exact commands.

Rollback:

- Restore previous `config.py`, `fuel_routes.py`, `run_server.py` from backup.

## ADR-011 — Topaz API path: canonical route + legacy alias

Status: implemented 2026-05-22.

Decision:

- Canonical Topaz sync endpoint: `POST /fuel/api/fuel_sync` (served by the `fuel`
  blueprint with `url_prefix='/fuel'`).
- Legacy compatibility alias: `POST /api/fuel_sync` (app-level route, no blueprint prefix).
  Added because older Topaz agent configurations may reference `/api/fuel_sync` from
  earlier project documentation.
- Both paths use the same `_perform_fuel_sync()` helper in `fuel_routes.py`.
  There is no logic duplication.
- Both paths apply the same `FUEL_API_TOKEN` validation (deny-all if token not set).
- The legacy alias logs a `WARNING` on every call to prompt operator to update the
  agent config. Token value and request body are never logged.
- The `/api/fuel_sync` alias is temporary and should be removed from `app.py` once
  all Topaz agent configurations are confirmed updated to `/fuel/api/fuel_sync`.

Risk:

- Leaving the alias in place indefinitely is not harmful (same security) but adds a
  dead route. Review during next maintenance window.

## ADR-012 — Lightweight migration registry via schema_migrations table

Status: implemented 2026-05-22 (TASK-OPS-001).

Decision:

- A `schema_migrations` table is added to the SQLite database.
  Columns: `id`, `name` (unique), `applied_at`, `checksum`, `description`.
- A companion SQLAlchemy model `SchemaMigration` is added to `models.py`
  so that `db.create_all()` creates the table on fresh installs.
- A helper module `migration_utils.py` provides:
  `ensure_schema_migrations_table()`, `is_migration_applied(name)`,
  `record_migration(name, ...)`, and `migration_checksum(path)`.
  No external dependencies — stdlib `sqlite3`, `hashlib`, `datetime` only.
- A bootstrap script `migrate_000_migration_registry.py` creates the table
  on existing production databases and records itself as the first migration.
  It is idempotent and safe to run multiple times.
- Historical migration scripts are inventoried in `docs/MIGRATIONS.md`
  but are NOT backfilled automatically; that work is TASK-OPS-002.

Reason:

- Previous migrations (14 scripts) could be run twice or out of order with no
  detection mechanism and no single registry of what had been applied.
- This mechanism is the minimal viable registry: no new dependencies, no
  framework, no external runner — operators deploy with copy-paste commands.

Consequences:

- New migration scripts must call `is_migration_applied()` at the top and
  `record_migration()` at the end (see `docs/MIGRATIONS.md`).
- Old scripts are not retroactively guarded; TASK-OPS-002 handles backfill.

## ADR-009 — Spare parts workflow postponed

Status: postponed by user.

Decision:

- Do not continue large spare parts workflow until re-approved.
- Existing early module can remain, but do not expand without confirmation.
