## 2026-06-09 — BOT002B completed (Telegram bot runner)

- BOT002B (Telegram bot runner for spare parts) deployed to production on 2026-06-09.
- Production server: `srv-yoqsh` / `10.103.25.14`.
- Production path: `C:\\transport-report`.
- Production service `TransportBot` created and running.
- Commit: `c576624` — "Add Telegram bot runner for spare parts".
- DB backup before deployment: `D:\\transport-report-backups\\production\\daily\\transport_20260609_143144.db`.
- No DB migration was required.
- All smoke tests passed.

### Smoke test results

- `git pull --ff-only`: commit `c576624` applied successfully.
- `py_compile`: ALL PASS (7 bot files).
- `APP IMPORT OK`.
- `BOT ROUTES OK` — 7 routes including `/api/bot/logout`.
- `TransportReport`: RUNNING.
- `TransportBot`: RUNNING.
- `/api/bot/health`: ok.
- `bot.log`: "Application started", no errors.
- `bot_error.log`: empty.
- `TOKEN_PATTERN_COUNT=0` — no token in logs.
- `bot_state.db`: created (12288 bytes).
- DB integrity: ok.
- `/admin/users`: working, Telegram column visible, code generation working.
- Telegram `/start`: working.
- Telegram `/link`: account linked as Administrator.
- Telegram `/status`: 5 real requests shown.
- Telegram `/pending`: admin access working.
- Telegram `/logout`: session revoked correctly.
- `ACTIVE_BOT_SESSIONS` after logout: 0.
- `TOTAL_BOT_SESSIONS`: 1.

## 2026-06-04 - REPORT001B completed

- Completed REPORT001B: Excel export improvements for main report and daily activity report.
- Production backup before deployment: D:\transport-report-backups\production\daily\transport_20260604_142115.db.
- Changed files: app.py, excel_export.py, excel_daily_activity.py.
- Fixed /report preview logic: working status is handled as working, not worked.
- Fixed top work types and working/downtime row counts on /report.
- Main Excel report now follows user interface language: RU exports Russian workbook, UZ exports Uzbek workbook.
- Daily activity Excel report now follows user interface language: RU exports Russian workbook, UZ exports Uzbek workbook.
- Russian Детально sheet headers were translated.
- Russian agricultural machinery categories in daily activity report were translated.
- Existing Excel sheet structure and order were preserved; no new sheets were added.
- Workbook readability/print layout was improved.
- No database migration required.
- Production smoke test passed.

## 2026-06-04 - REPORT001A completed on production

- Production URL: http://10.103.25.14:5050.
- Files changed: app.py, templates/report.html.
- Improved the main transport work report page.
- Added report summaries, organization summaries, work type summaries, and detail preview.
- Added client-side table search and improved period/organization/category filters.
- Preserved Excel export behaviour.
- Production backup completed before deployment: D:\transport-report-backups\production\daily\transport_20260604_131327.db.
- py_compile passed.
- App import check passed: APP IMPORT OK.
- `/report` test client returned STATUS=200.
- TransportReport service restarted and running.
- REPORT001A production smoke test passed.
- No database migration required.

## 2026-06-04 - UX001D completed on production

- Production URL: http://10.103.25.14:5050.
- Files changed: templates/spare_parts_list.html, templates/spare_part_form.html, templates/spare_part_detail.html, templates/spare_parts_catalog.html.
- Improved spare parts request list with status counters, search, and status filter.
- Improved spare parts request form with sticky action panel, item counter, empty-row cleanup, and client-side validation.
- Improved spare part request detail page with summary cards and clearer admin/operator actions.
- Improved spare parts catalog with search and client-side validation.
- Production backup completed before deployment: D:\transport-report-backups\production\daily\transport_20260604_124931.db
- py_compile passed.
- App import check passed: APP IMPORT OK.
- TransportReport service restarted and running.
- UX001D production smoke test passed.
- No database migration required.

## 2026-06-04 - UX001C completed on production

- Production URL: http://10.103.25.14:5050.
- Files changed: templates/fuel/dashboard.html, templates/fuel/transactions.html, templates/fuel/receipts.html, templates/fuel/initial_balance.html, templates/fuel/warehouses.html, templates/fuel/stations.html.
- Improved Fuel dashboard and operator screens.
- Removed price/sum visual logic from Fuel UI; DT-only and liters-focused workflow is now clearer.
- Added search/filter UX for receipts, initial balances, warehouses, and stations.
- Staging Topaz transaction date issue was diagnosed as stale staging DB; production had current transactions through 2026-06-04.
- Staging DB was refreshed from current production backup before production deployment continued.
- Production backup completed before deployment: D:\transport-report-backups\production\daily\transport_20260604_120114.db
- py_compile passed.
- App import check passed: APP IMPORT OK.
- TransportReport service restarted and running.
- UX001C production smoke test passed.
- No database migration required.

## 2026-06-04 - UX001B completed on production

- Production URL: http://10.103.25.14:5050.
- Files changed: wialon_import.py, translations.py, templates/wialon_mapping_list.html, templates/wialon_auto_match.html.
- Improved Wialon mapping list UX with counters, search, status filters, pending objects area, and clearer actions.
- Improved Wialon auto-match UX with toolbar, filters, expand/collapse controls, visible-row skip action, and duplicate-selection validation.
- Added RU/UZ translations for new Wialon mapping and auto-match UI elements.
- Production backup completed before deployment: D:\transport-report-backups\production\daily\transport_20260604_112928.db.
- py_compile passed.
- App import check passed: APP IMPORT OK.
- TransportReport service restarted and running.
- UX001B production smoke test passed.
- No database migration required.

## 2026-06-04 - UX001A completed on production

- Production URL: http://10.103.25.14:5050.
- Files changed: templates/daily_entry.html, translations.py.
- Improved daily entry operator form with clearer selected date/organization/equipment summary.
- Added daily entry toolbar with save, mark-all-idle, expand/collapse, search, and counters.
- Added client-side validation and invalid-field highlighting before submit.
- Added and corrected RU/UZ translations for new daily entry UX elements.
- Production backup completed before deployment: D:\transport-report-backups\production\daily\transport_20260604_111225.db
- py_compile passed.
- App import check passed: APP IMPORT OK.
- TransportReport service restarted and running.
- UX001A production smoke test passed.
- No database migration required.

## 2026-06-04 - DATA001-3 completed on production

- Production URL: http://10.103.25.14:5050.
- Files changed: app.py, fuel_routes.py, spare_parts.py, wialon_import.py, templates/base.html.
- Added multi-error validation message support.
- Improved validation feedback for daily entry, Fuel, spare parts, and Wialon workflows.
- Updated base template to render validation errors as readable lists.
- Production backup completed before deployment: D:\transport-report-backups\production\daily\transport_20260604_104248.db
- py_compile passed.
- App import check passed: APP IMPORT OK.
- TransportReport service restarted and running.
- DATA001-3 production smoke test passed.
- No database migration required.

## 2026-06-04 - DATA001-2 completed on production

- Production URL: http://10.103.25.14:5050.
- Files changed: app.py, wialon_import.py, templates/ref_organizations.html, templates/ref_equipment.html, templates/ref_work_types.html, templates/ref_customers.html, templates/wialon_mapping_list.html.
- Added reference validation for duplicates, required fields, normalized names, equipment plate numbers, and non-negative default prices.
- Added Wialon mapping validation for duplicate Wialon names, active equipment only, and one-to-one equipment mapping.
- Added Wialon auto-match bulk validation to avoid partial saves on invalid data.
- Production backup completed before deployment: D:\transport-report-backups\production\daily\transport_20260604_102724.db
- py_compile passed.
- App import check passed: APP IMPORT OK.
- TransportReport service restarted and running.
- DATA001-2 production smoke test passed.
- No database migration required.

## 2026-06-04 - DATA001-1 completed on production

- Production URL: http://10.103.25.14:5050.
- Files changed: app.py, fuel_routes.py, spare_parts.py, templates/fuel/initial_balance.html, templates/fuel/receipts.html.
- Added backend validation for daily entry, Fuel, spare parts, and key references.
- Fuel business rules updated: DT only, no price fields, negative initial balances allowed.
- Valid and invalid production scenarios were smoke-tested.
- Production backup completed before deployment: D:\transport-report-backups\production\daily\transport_20260604_100121.db
- py_compile passed.
- App import check passed: APP IMPORT OK.
- TransportReport service restarted and running.
- No database migration required.

## 2026-06-03 - TASK-SEC-003F completed on production

- Production URL: http://10.103.25.14:5050.
- Files changed: app.py, spare_parts.py, templates/spare_part_detail.html.
- Added transport module permission checks to core transport routes.
- Hardened spare parts organization access for non-admin users.
- Non-admin spare parts users are restricted to accessible organizations and equipment.
- Spare parts approve/reject actions are admin-only.
- Production backup completed before deployment: D:\transport-report-backups\production\daily\transport_20260603_120118.db, integrity_check ok.
- py_compile passed.
- App import check passed: APP IMPORT OK.
- TransportReport service restarted and running.
- SEC003F production smoke test passed.
- No database migration required.

## 2026-06-03 - TASK-SEC-003E completed on production

- Production URL: http://10.103.25.14:5050.
- Files changed: app.py, fuel_routes.py, templates/ref_organizations.html, templates/ref_equipment.html, templates/ref_work_types.html, templates/ref_customers.html, templates/fuel/warehouses.html, templates/fuel/stations.html.
- Added dangerous delete protection for organizations, equipment, work types, customers, fuel warehouses, and fuel stations.
- Linked active equipment and linked active fuel stations are deactivated instead of physically deleted.
- Added equipment_reactivated and fuel_station_reactivated actions.
- UI now shows Used/Deactivate/Enable states instead of misleading delete buttons for linked records.
- Added audit logging for blocked delete, deactivation, and reactivation actions.
- Production backup completed before deployment: D:\transport-report-backups\production\daily\transport_20260603_111926.db, integrity_check ok.
- py_compile passed.
- App import check passed: APP IMPORT OK.
- TransportReport service restarted and running.
- SEC003E production smoke test passed.
- No database migration required.

## 2026-06-03 - TASK-SEC-003D completed on production

- Production URL: http://10.103.25.14:5050.
- Added CSRF protection for browser POST forms.
- Added csrf_token() Jinja global and hidden csrf_token fields in templates.
- Topaz token-auth API endpoints remain excluded from CSRF: /fuel/api/fuel_sync and /api/fuel_sync.
- Production backup completed before deployment: D:\transport-report-backups\production\daily\transport_20260603_095235.db, integrity_check ok.
- py_compile passed.
- App import check passed: APP IMPORT OK.
- TransportReport service restarted and running.
- Topaz ping verified on production: /fuel/api/fuel_ping returned ok.
- Production CSRF smoke test passed: login/logout, daily report save, reference save, Wialon mapping save, Fuel warehouse save, spare parts request creation, and admin audit page.
- No database migration required.

## 2026-06-03 - TASK-SEC-003C-3 completed on production

- Production URL: http://10.103.25.14:5050.
- Files changed: spare_parts.py, templates/spare_part_form.html, templates/spare_part_detail.html, templates/spare_parts_list.html, templates/spare_parts_catalog.html.
- Added spare parts audit actions: spare_part_request_created, spare_part_item_created, spare_part_request_status_changed, spare_part_catalog_created, spare_part_catalog_updated.
- Improved spare parts equipment selector: model, plate number, and organization are shown.
- Improved Russian UI labels in spare parts pages.
- Production backup completed before deployment: D:\transport-report-backups\production\daily\transport_20260603_084842.db, integrity_check ok.
- py_compile passed.
- App import check passed: APP IMPORT OK.
- TransportReport service restarted and running.
- /admin/audit verified manually for spare parts actions.
- No database migration required.

## 2026-06-03 - TASK-SEC-003C-2 completed on production

- Production URL: http://10.103.25.14:5050.
- Files changed: fuel_routes.py, templates/fuel/warehouses.html.
- Added Fuel audit actions: fuel_warehouse_created, fuel_warehouse_updated, fuel_warehouse_deleted, fuel_station_created, fuel_station_updated, fuel_station_deleted, fuel_initial_balance_saved, fuel_receipt_created, fuel_receipt_updated, fuel_receipt_deleted, fuel_topaz_sync_completed, fuel_topaz_sync_failed.
- Improved warehouse edit UX: edit form opens inline inside the selected warehouse card instead of the top of the page.
- Production backup completed before deployment: D:\transport-report-backups\production\daily\transport_20260603_081631.db, integrity_check ok.
- py_compile passed.
- App import check passed: APP IMPORT OK.
- TransportReport service restarted and running.
- /admin/audit verified manually for Fuel actions.
- No database migration required.

## 2026-06-03 - TASK-SEC-003C-1 completed on production

- Production URL: http://10.103.25.14:5050.
- File changed: wialon_import.py.
- Added Wialon audit actions: wialon_import_uploaded, wialon_auto_match_saved, wialon_mapping_created, wialon_mapping_updated, wialon_mapping_deleted, wialon_engine_hours_exported, wialon_workload_exported.
- Production backup completed before final verification: D:\transport-report-backups\production\daily\transport_20260602_221500.db, integrity_check ok.
- py_compile passed.
- App import check passed: APP IMPORT OK.
- TransportReport service restarted and running.
- /admin/audit verified manually; wialon_mapping_updated appears after saving a Wialon mapping.
- module_required(wialon) decorators were checked and restored before commit; no Wialon module-permission regression remains in final diff.
- No database migration required.

# AGENT_STATE.md — Current Project State

## State date

2026-05-24 (TASK-DEPLOY-005F completed: production cutover from `10.103.25.200` to `srv-yoqsh` (`10.103.25.14`) recorded as COMPLETED; docs/ORG_WINDOWS_SERVER_CUTOVER_RUNBOOK.md updated with cutover completion record; docs/AGENT_STATE.md, docs/TASKS.md, docs/DEPLOYMENT_PLAN.md, docs/RELEASE_AND_BACKUP_PROCEDURE.md updated — no application code, no database, no service changes)

## Materials reviewed

- `transport-report.zip` from the current project.
- `instructions.md`.
- `claude-session-01.md` through `claude-session-04.md`.

## Current codebase status

### Static checks

The following files pass Python syntax compilation on the production server:

- `app.py`
- `models.py`
- `excel_export.py`
- `wialon_import.py`
- `workload_report.py`
- `fuel_routes.py`
- `spare_parts.py`
- `translations.py`
- `excel_daily_activity.py`
- `config.py`
- `run_server.py`
- `migrate_module_permissions.py` (executed successfully on production)

### Database snapshot from uploaded archive

Observed production SQLite counts (as of 2026-05-19):

- `organizations`: 17
- `equipment`: 336
- `daily_records`: 9021
- `engine_hours_records`: 9870
- `vialon_mappings`: 379
- `vialon_imports`: 169
- `fuel_warehouses`: 10
- `fuel_stations2`: 21
- `fuel_transactions2`: 391069
- `users`: 2
- `app_modules`: 5
- `user_module_permissions`: 5
- `spare_part_requests`: 1

### Recently completed

**TASK-DEPLOY-005F — Record organization-server production cutover completion (2026-05-24 — COMPLETED)**

- `docs/ORG_WINDOWS_SERVER_CUTOVER_RUNBOOK.md`: cutover completion record section added at the top
  (status COMPLETED, old production facts, new production facts, final backup/cold copy, DB counts,
  backup task, Topaz switch facts, anti split-brain instruction, rollback status).
  Section Q (cutover completion table) filled in with verified operator facts.
- `docs/AGENT_STATE.md`, `docs/TASKS.md`, `docs/DEPLOYMENT_PLAN.md`,
  `docs/RELEASE_AND_BACKUP_PROCEDURE.md` updated.
- No application code changed. No database changed. No service restarted. No migrations.
  No git pull. No git push.

**TASK-DEPLOY-004D — Fix backup_transport_db.bat wrapper (2026-05-23 — COMPLETED)**

- `backup_transport_db.bat` corrected: previous wrapper called `backup_transport_db.py` correctly
  but exited with bare `exit /b %ERRORLEVEL%` and printed no success or failure messages of its own.
- Replaced with explicit `if errorlevel 1` failure block: prints "Backup FAILED. See
  backup_transport_db.py output above." and exits with code 1.
- On success: prints "Backup completed successfully." and exits with code 0.
- Comment block updated: removed stale "Updated by TASK-DEPLOY-004B" reference; now reads
  "Daily backup wrapper for the production SQLite database. Uses backup_transport_db.py
  with sqlite3.Connection.backup()."
- No raw `copy /Y` logic anywhere in the file. No SOURCE/DEST_FILE variables. No PowerShell
  timestamp. `backup_transport_db.bat` now actually calls `backup_transport_db.py` AND
  surfaces its exit code with clear human-readable messages.
- `backup_transport_db.py` unchanged (py_compile PASS — see Test Results).
- No application code changed. No database changed. No service restarted. No migrations.

**TASK-DEPLOY-004C — Fix update.bat pre-update backup failure message (2026-05-23 — COMPLETED)**

- `update.bat` STEP 1 failure block corrected: error message now reads
  "Check disk space, permissions, and backup_transport_db.py output." (previously
  omitted the backup_transport_db.py output hint).
- No other changes to `update.bat`; all other 004B changes confirmed present:
  no raw `copy /Y`, no `BACKUP_FILE` variable, no PowerShell TIMESTAMP block;
  rollback echoes reference `%BACKUP_DIR%`; final message references `%BACKUP_DIR%`.
- `update.bat` confirmed to use SQLite online backup API via `backup_transport_db.py`
  for the pre-update backup step — no raw file copy logic remains anywhere in the file.
- `docs/AGENT_STATE.md` and `docs/TASKS.md` updated.
- No application code changed. No database changed. No service restarted. No migrations.
- py_compile on `backup_transport_db.py` PASS (see Test Results).

**TASK-FUEL-001 — Standardize Topaz API path (2026-05-22 — COMPLETED)**

- `fuel_routes.py`: sync logic moved into `_perform_fuel_sync()` helper.
  Canonical `@fuel_bp.route('/api/fuel_sync')` view now calls the helper.
- `app.py`: legacy route `POST /api/fuel_sync` registered at app level (no blueprint
  prefix). Logs a WARNING naming the deprecated path, then delegates to the same helper.
  Token validation unchanged. No business logic duplicated.
- `docs/DECISIONS.md`: ADR-011 added.
- `docs/TASKS.md`: TASK-FUEL-001 moved to completed.
- `py_compile` and import check pass.

**TASK-SEC-002 — Move secrets to environment/config (2026-05-22 — COMPLETED)**

- `SECRET_KEY` hardcoded fallback removed from `config.py` base `Config` class.
  `DevelopmentConfig` retains a clearly-named dev-only fallback.
  `SqliteProductionConfig` and `ProductionConfig` inherit no fallback (None if unset).
- `run_server.py` now exits immediately with a clear ASCII-only error if `SECRET_KEY`
  is not in the environment. NSSM will mark the service as failed with a readable log.
- `FUEL_API_TOKEN` added to `Config` as `os.environ.get('FUEL_API_TOKEN')`.
- `fuel_routes.py` removed the old hardcoded API_TOKEN; token is now read from
  `current_app.config['FUEL_API_TOKEN']`.
  If not configured, all sync requests receive 401 (safe deny-all default).
- `current_app` added to fuel_routes flask imports.
- `docs/DEPLOYMENT_SECURITY.md` created with exact Windows `setx` commands,
  verification steps, and rollback instructions.

**TASK-SEC-001 — Enforce module permissions (2026-05-22 — COMPLETED)**

- `User.has_module_access(module_code)` method added to `models.py`.
  Admin always returns True; non-admin returns False unless an explicit
  `has_access=True` record exists in `user_module_permissions`.
- `module_required(module_code)` decorator factory added to `models.py`.
  Uses lazy imports to avoid polluting models module scope with Flask objects.
- Wialon routes (11 routes): `@module_required('wialon')` added above existing
  `@editor_required` / `@admin_required` decorators in `wialon_import.py`.
- Fuel routes (13 UI routes): `@module_required('fuel')` added in `fuel_routes.py`.
  API endpoints (`/fuel/api/fuel_ping`, `/fuel/api/fuel_sync`) remain token-only.
- Spare parts routes (9 routes): `@login_required` replaced with
  `@module_required('spare_parts')` in `spare_parts.py`.
- Deficiency routes (3 routes): `@module_required('deficiencies')` added in `app.py`.
- Navigation visibility fixed in `templates/base.html`: Wialon link shown only if
  `current_user.has_module_access('wialon')`; Fuel/АЗС link shown only if
  `current_user.has_module_access('fuel')`; spare parts and deficiencies links
  controlled by their respective module access checks.
- Migration script `migrate_module_permissions.py` executed successfully.
- Import corrected in `app.py`: `generate_daily_activity` imported from
  `excel_daily_activity` (was missing, caused startup failure).
- Direct access to a disabled module URL now returns 403.
- Site confirmed starting and working after all changes.

**TASK-OPS-001 — Migration discipline (2026-05-22 — COMPLETED)**

- `migration_utils.py` created with helpers: `ensure_schema_migrations_table`,
  `is_migration_applied`, `record_migration`, `migration_checksum`.
  Uses stdlib sqlite3 only — no new dependencies.
- `migrate_000_migration_registry.py` created: idempotent bootstrap script that
  creates `schema_migrations` table and records itself as the first migration.
- `models.py`: `SchemaMigration` SQLAlchemy model added.
- `docs/MIGRATIONS.md` created: full procedure, script template, historical inventory.
- `docs/DECISIONS.md`: ADR-012 added.
- `docs/QA_CHECKLIST.md`: migration checklist section added.
- `docs/TASKS.md`: TASK-OPS-001 moved to completed; TASK-OPS-002 added to backlog.
- py_compile and import check pass.
- **`migrate_000_migration_registry.py` HAS been run on production.**
  Confirmed by database: schema_migrations has 1 row with
  applied_at=2026-05-22T16:48:29.137350. (Previous note was stale.)

### Previously resolved items

- `daily_entry.html` renders all 9 equipment categories.
- `wialon_import.py` registers routes via `register_wialon_routes(app, ...)`.
- Wialon duration parser supports Russian day words.
- Equipment migration to 9 categories applied in DB and `models.py`.

## Current open risks

1. Legacy `/api/fuel_sync` alias is temporary. Remove from `app.py` once all Topaz
   agent configs are confirmed updated to `/fuel/api/fuel_sync`.
2. After deploying TASK-SEC-002, existing NSSM deployment will refuse to start
   until the operator sets `SECRET_KEY` and `FUEL_API_TOKEN` via `setx`.
   See `docs/DEPLOYMENT_SECURITY.md` for exact steps.
3. (RESOLVED) `migrate_000_migration_registry.py` has been run. The
   `schema_migrations` table exists in production with 1 row.
4. (RESOLVED) `migrate_001_backfill_historical_registry.py` was run successfully on
   production 2026-05-23. schema_migrations now has 10 rows (8 CONFIRMED_APPLIED
   backfilled + 1 bootstrap + 1 self). 5 pending scripts still need operator
   confirmation (TASK-OPS-002C). migrate_v47.py marked OBSOLETE.
5. Old fuel v1 tables coexist with v2 (safe short-term).
6. `wialon_import.py` is large; split deferred until current work stabilizes.
7. No CSRF protection on POST forms.

## Current production state

| Item | Value |
|---|---|
| Production server | `srv-yoqsh` (`10.103.25.14`) |
| Production URL | `http://10.103.25.14:5050` |
| Production service | `TransportReport` — RUNNING |
| Old workstation | `10.103.25.200` — `TransportReport` STOPPED (rollback standby only) |
| Staging | `http://10.103.25.14:5051` — `TransportReportStaging` RUNNING |
| Production backup task | `TransportDBBackupProduction` — daily 02:00, SYSTEM |
| Production backup dest | `D:\transport-report-backups\production\daily\` |
| Staging backup task | `TransportDBBackupStaging` — daily 03:00, SYSTEM |
| Staging backup dest | `D:\transport-report-backups\staging\daily\` |
| Topaz agent | `C:\topaz_agent.py` (task: `TopazFuelAgent`) — points to `http://10.103.25.14:5050` |
| Topaz test | ping OK, auth OK, sync OK (no 401/500/traceback) |

## Current recommended next task

**TASK-DEPLOY-005G — Post-cutover monitoring and cleanup (planned)**

- Monitor production logs and backup files daily for 3–5 business days.
- Confirm `D:\transport-report-backups\production\daily\` has fresh files each morning.
- Keep old workstation `TransportReport` STOPPED as rollback standby.
- Remove or disable the old service on `10.103.25.200` only after owner approval (not before).
- Document exact Topaz agent location/task (`C:\topaz_agent.py`, task: `TopazFuelAgent`) in a
  dedicated ops note once confirmed stable.
- Optionally add a small "old server disabled" landing page if users accidentally try the old URL.

TASK-OPS-002C remains open — operator must answer 5 confirmation questions in
`docs/MIGRATION_BACKFILL_ANALYSIS.md` for the LIKELY_APPLIED migration scripts.

TASK-DEPLOY-006 remains planned (PostgreSQL migration research — not urgent).

**Recently completed**

**TASK-DEPLOY-005E — Record staging QA and prepare production cutover plan (2026-05-23 — COMPLETED)**

- `docs/ORG_WINDOWS_SERVER_STAGING_RUNBOOK.md` updated:
  - Staging QA PASSED recorded (operator confirmed admin/operator/Excel/Wialon/Fuel/log — all OK).
  - Backup history updated: manual `--source` test backup (`transport_20260523_225240_staging.db`,
    46,809,088 bytes, integrity ok) and Task Scheduler test run (`transport_20260523_225344_staging.db`,
    46,809,088 bytes, integrity ok) both recorded.
  - `TransportDBBackupStaging` task state: Ready, next run 24.05.2026 03:00:00.
  - Section 4 QA checklist: all items marked [x] with operator confirmation.
  - Section 5 operator next steps updated to reflect completion; directs operator to cutover runbook.
- `docs/ORG_WINDOWS_SERVER_CUTOVER_RUNBOOK.md` created (new file):
  - Sections A–R: purpose/scope, preconditions, recommended paths, pre-cutover checklist on old
    workstation (git status, final backup, service stop, cold copy), DB transfer to new server,
    environment variables (placeholder commands only — no real secrets), dependency install, DB copy,
    syntax/import checks, read-only DB count verification, production backup wrapper
    (`backup_production_db.bat`) + Task Scheduler task (`TransportDBBackupProduction`, 02:00 daily),
    NSSM `TransportReport` service install, Windows Firewall rule, full production QA checklist,
    Topaz switch procedure (only after QA passes), user communication, rollback plan (before and
    after Topaz switch), anti split-brain warning, cutover completion record, post-cutover tasks.
- `docs/AGENT_STATE.md` and `docs/TASKS.md` updated.
- No application code changed. No database changed. No service restarted. No migrations. No git push.

**TASK-DEPLOY-005D — Add --source support to backup tool for staging (2026-05-23 — COMPLETED)**

- `backup_transport_db.py`: `--source <path>` argument added to argparse.
  Default remains `C:\transport-report\instance\transport.db` (production unchanged).
  `source_path` is now taken from `args.source` instead of the module-level constant.
  Docstring updated with new usage example for staging.
  Existing `--dest-dir` and `--suffix` arguments unchanged.
- `backup_transport_db.bat`: unchanged — calls `backup_transport_db.py` with no
  explicit `--source`, so production default source path continues to apply.
- `docs/ORG_WINDOWS_SERVER_STAGING_RUNBOOK.md` created: records staging server facts
  (srv-yoqsh, 10.103.25.14:5051, TransportReportStaging running); DB counts verified
  (users=3, equipment=336, fuel_transactions2=391284, schema_migrations=10);
  manual backup history recorded (integrity_check=ok, 46,809,088 bytes, 2026-05-23);
  Section 1 gives proper backup command with --source/--dest-dir/--suffix; Section 2
  gives Task Scheduler setup for TransportDBBackupStaging (03:00 daily, SYSTEM);
  Section 4 QA checklist; Section 5 operator next steps; Section 6 production-vs-staging
  comparison table; Section 7 Topaz/Wialon staging policy.
- `docs/AGENT_STATE.md` and `docs/TASKS.md` updated.
- py_compile PASS. Functional test PASS (--source with production DB, integrity_check ok,
  dest 46,809,088 bytes).
- No application code changed. No database changed. No service restarted. No migrations.
- No git push.

**TASK-DEPLOY-005B — Fix VPS runbook order and stale deployment-plan backup wording (2026-05-23 — COMPLETED)**

- `docs/VPS_STAGING_RUNBOOK.md` reordered: primary path is now Git → Python → empty
  `C:\transport-report` git clone → copy `nssm.exe` into the cloned folder → setx env
  vars → firewall → production backup → transfer → create `instance\` → copy DB → verify
  DB → install dependencies → syntax/import checks → `install_service.bat` → service/QA.
  Earlier sections (3.3 NSSM, 6.3 instance dir) updated to say "after clone". Section 16
  numbered checklist rewritten to clone first; "Alternative if folder already exists" kept
  only as a troubleshooting note at the end of Section 16, not as the primary path.
- `docs/DEPLOYMENT_PLAN.md` Sections 7 and 8: stale raw `copy "...transport.db..." "D:\backups\..."`
  examples replaced with the verified procedure (`cd C:\transport-report && backup_transport_db.bat`)
  that calls `backup_transport_db.py` via the SQLite online backup API with
  `PRAGMA integrity_check`. Backup destination corrected to
  `C:\transport-report-backups\daily\` and verified Task Scheduler task `TransportDBBackup`
  (daily 02:00, SYSTEM) referenced explicitly. TASK-DEPLOY-004 scope rewritten to describe
  the completed implementation (`docs/RELEASE_AND_BACKUP_PROCEDURE.md`, `update.bat`,
  `backup_transport_db.py`, `backup_transport_db.bat`, Task Scheduler task verified by
  operator). Unsupported retention automation moved to future improvement, not completed.
- `docs/AGENT_STATE.md`: duplicate TASK-OPS-002C paragraph in "Current recommended next task"
  removed; one copy kept.
- `docs/TASKS.md`: TASK-DEPLOY-005B entry added (completed). TASK-DEPLOY-005 remains planned
  awaiting VPS; TASK-DEPLOY-006 remains planned; TASK-OPS-002C remains pending.
- No application code changed. No database changed. No service restarted. No migrations.
- No git commit. No git push.

**TASK-DEPLOY-005A — VPS staging deployment runbook (2026-05-23 — COMPLETED)**

- `docs/VPS_STAGING_RUNBOOK.md` created: 16-section runbook covering VPS prerequisites,
  software installation, GitHub clone with PAT authentication, environment variables setup
  (SECRET_KEY and FUEL_API_TOKEN via setx /M), production database transfer and integrity
  verification, Python environment and dependency setup, NSSM service installation via
  install_service.bat, Windows Firewall rules for staging (port 5050 restricted to office IP),
  Nginx reverse proxy skeleton, automated daily backup via Task Scheduler, QA smoke test
  checklist, Topaz/Wialon staging policy (no Topaz agent change until staging QA passes),
  cutover plan draft, rollback plan, open questions for operator, and exact operator command
  checklist with 26 numbered steps.
- `docs/AGENT_STATE.md`, `docs/TASKS.md`, `docs/DEPLOYMENT_PLAN.md` updated with task status.
- No application code changed. No database changed. No service restarted. No migrations.
- No git commit. No git push.

**TASK-DEPLOY-004E — Close release/backup procedure after successful operator test (2026-05-23 — COMPLETED)**

- `backup_transport_db.py` syntax check: `py_compile` PASS (no output).
- `backup_transport_db.bat` manual run: SUCCESS.
  - SQLite online backup API used.
  - Backup created: `C:\transport-report-backups\daily\transport_20260523_182423.db`
  - Source size: 46,800,896 bytes. Destination size: 46,800,896 bytes.
  - Integrity check: `ok`. Wrapper printed: `Backup completed successfully.`
- Directory verification: `transport_20260523_182423.db` confirmed present at 46,800,896 bytes.
- Windows Task Scheduler daily backup task created:
  - Command: `schtasks /create /tn "TransportDBBackup" /tr "C:\transport-report\backup_transport_db.bat" /sc daily /st 02:00 /ru SYSTEM /f`
  - Result: SUCCESS. Task name: `TransportDBBackup`. Next run: 24.05.2026 2:00:00. State: Ready.
- Scheduled task manual run: `schtasks /run /tn "TransportDBBackup"` — SUCCESS.
  - New backup: `C:\transport-report-backups\daily\transport_20260523_182603.db`, 46,800,896 bytes.
- Git commits `428104a` and `10652e2` pushed to `origin/main`. Working tree clean.
- Documentation only. No application code changed. No database changed. No service restarted. No migrations.

**TASK-DEPLOY-004B — Safe SQLite backup via online backup API (2026-05-23 — COMPLETED and verified by operator 2026-05-23)**

- `backup_transport_db.py` created (stdlib only, no Flask imports): uses `sqlite3.Connection.backup()`
  for a consistent online backup even when WAL mode is active and the service is running.
  Accepts `--dest-dir` and `--suffix` arguments. Prints source path, dest path, source size,
  dest size, integrity check result. Exits non-zero on any failure.
  Performs `PRAGMA integrity_check` on destination; requires result `ok`.
- `backup_transport_db.bat` updated: removed raw `copy /Y` of `transport.db`; now calls
  `backup_transport_db.py` and propagates its exit code.
- `update.bat` updated: removed raw `copy /Y` pre-update backup block; now calls
  `backup_transport_db.py --dest-dir ... --suffix before_update`. Rollback echo messages
  updated to reference the backup directory instead of a stale `%BACKUP_FILE%` variable.
- `docs/RELEASE_AND_BACKUP_PROCEDURE.md` updated: removed claim that WAL mode makes raw
  `.db` copy safe; documented SQLite online backup API; updated output example; fixed known
  risks table row.
- No application code changed. No database changed. No service restarted. No migrations.

**TASK-DEPLOY-004 — Release package and backup procedure (2026-05-23 — SUPERSEDED by 004B for backup logic)**

- `docs/RELEASE_AND_BACKUP_PROCEDURE.md` created: purpose, pre-update checklist, automated
  and manual update procedures, migration handling rule, rollback procedure, manual backup,
  Task Scheduler daily backup setup, backup verification, restore procedure, post-update QA
  checklist, known risks, and operator quick-reference commands.
- `update.bat` created (not executed): pre-update DB backup → service stop → git pull
  --ff-only → syntax check → import check → migration warning with pause → service start.
  Fails fast at any step with clear next-action message. Service stays stopped on failure.
- `backup_transport_db.bat` created (not executed): locale-independent timestamped copy of
  `instance/transport.db` to `C:\transport-report-backups\daily\`. ASCII-only output.
  Creates destination folder if missing. Exits non-zero on source missing or copy failure.
- Note: raw file copy replaced by TASK-DEPLOY-004B.
- No application code changed. No database changed. No service restarted.

**TASK-DEPLOY-002 — GitHub repository creation and first push (2026-05-23 — COMPLETED)**

- Private GitHub repository created: https://github.com/sINte3/vehicle-soft
- Repository visibility: Private.
- Local branch: `main`. Remote: `origin`.
- Initial source push to `origin/main` completed successfully.
- Tag created and pushed: `v1.0-production-2026-05-23`.
- Final `git status`: branch main up to date with origin/main, working tree clean.
- `.gitignore` updated with two additional exclusions before first commit:
  `/PROMPT.md` (root-level Claude prompt file) and `*.docx` (binary user guide excluded).
- `PROMPT.md` and `Rukovodstvo_polzovatelya.docx` were excluded from the first commit.
- Sensitive/runtime files confirmed excluded: `instance/`, `reports/`, `logs/`, `Archive/`,
  `nssm.exe`, `wialon_import_v3.py`, `PROMPT_*.md`, `old_transport.db`, `.env`.
- No application code changed. No database changed. No service restarted.

**TASK-DEPLOY-003C — .gitignore root-only pattern anchoring (2026-05-23 — COMPLETED)**

- `.gitignore`: six patterns anchored with leading `/`:
  `/wialon.html`, `/wialon_auto_match.html`, `/wialon_report_v2.html`,
  `/Agroklastr_Tehnika_Konsolidaciya.xlsx`, `/Агрокластер_Техника_Консолидация.xlsx`,
  `/wialon_import_v3.py`.
- `templates/wialon.html` and `templates/wialon_auto_match.html` are now correctly not excluded.
- Documentation wording updated in `docs/SECRET_SCAN_REPORT.md`, `docs/AGENT_STATE.md`,
  `docs/TASKS.md`: `fuel_routes.py` hardcoded token references use plain language;
  blocking finding section clarified that `<REDACTED_LEGACY_FUEL_API_TOKEN>` is a
  placeholder — the real token value was redacted and is not present in any committed file.
- No application code changed. No database changed. No service restarted.

**TASK-DEPLOY-003B — Secret scan artifacts redacted (2026-05-23 — COMPLETED)**

- Literal legacy API token value redacted from all commit-eligible documentation files:
  `.gitignore`, `docs/SECRET_SCAN_REPORT.md`, `docs/AGENT_STATE.md`, `docs/TASKS.md`,
  `AUDIT_REPORT.md`. Replaced with `<REDACTED_LEGACY_FUEL_API_TOKEN>` placeholder.
- `PROMPT_*.md` (root-level only, `/PROMPT_*.md`) added to `.gitignore` to exclude
  Claude/ChatGPT handoff prompt files. `docs/PROMPT_PROTOCOL.md` unaffected.
- `docs/SECRET_SCAN_REPORT.md` updated to reflect 003B redaction status.
- No application code changed. No database changed. No service restarted.

**TASK-DEPLOY-002A — .gitignore created (2026-05-23 — COMPLETED)**

- `.gitignore` created from baseline in `docs/DEPLOYMENT_PLAN.md` Section 3.
- Extra exclusions added after project inspection:
  `.claude/`, `migration_log_*.csv`, `fix_names_log_*.csv`, `patch2_log_*.csv`,
  `Агрокластер_Техника_Консолидация.xlsx`, `wialon_import_v3.py`,
  `wialon.html`, `wialon_auto_match.html`, `wialon_report_v2.html`.
- `wialon_import_v3.py` excluded because it contains a stale hardcoded API token
  (`<REDACTED_LEGACY_FUEL_API_TOKEN>`) from before TASK-SEC-002. Active module is `wialon_import.py`.
- No code changes. No database changes. No service restart.

**TASK-DEPLOY-003A — Secret scan completed (2026-05-23 — COMPLETED)**

- Full source scan across `*.py`, `*.bat`, `*.html`, `*.js`, `*.css`, `*.md`.
- One blocking finding: `wialon_import_v3.py:674` hardcoded `<REDACTED_LEGACY_FUEL_API_TOKEN>`.
  Resolved by excluding the file from version control in `.gitignore`.
- All other findings are expected/documented defaults (admin123 seed, PG_PASS changeme,
  dev-only SECRET_KEY fallback, private LAN IPs in comments).
- `config.py`, `fuel_routes.py`, `run_server.py` verified clean (TASK-SEC-002 confirmed).
- `docs/SECRET_SCAN_REPORT.md` created with full findings table and operator next steps.
- Final verdict: SAFE to create private GitHub repository and push.

**TASK-DEPLOY-001 — GitHub/hosting migration plan (2026-05-23 — COMPLETED, planning only)**

- `docs/DEPLOYMENT_PLAN.md` created: full deployment and GitHub migration plan.
- Current deployment model documented (Windows/NSSM/SQLite/Waitress).
- `.gitignore` contents proposed (Section 3 of plan).
- GitHub repository structure proposed (Section 4).
- Hosting options compared: mini-server+UPS, Windows VPS, Linux VPS, PaaS (Sections 5–6).
- Recommended path: Phase 1 git hygiene → Phase 2 Windows VPS → Phase 3 HTTPS → Phase 4 Linux+PostgreSQL.
- Database strategy: SQLite short-term with automated backups; PostgreSQL migration plan outlined.
- Security requirements: HTTPS, domain, firewall, VPN, SECRET_KEY/FUEL_API_TOKEN, admin password, backups, monitoring.
- Topaz/Wialon impact documented: Topaz agent URL update required on server move.
- Task breakdown TASK-DEPLOY-002 through TASK-DEPLOY-006 defined.
- `docs/AGENT_STATE.md` and `docs/TASKS.md` updated.
- Syntax check: `py_compile app.py config.py run_server.py fuel_routes.py` — PASS.
- No code changes. No database changes. No service restart. Audit and planning only.

**TASK-UI-001C — Fuel module translation Phase 2 (2026-05-23 — COMPLETED, review findings fixed)**

- `translations.py`: 75 new UZ/RU fuel-module key pairs added (confirmed vocabulary).
  5 additional keys added in review-findings fix: `Ёқилғи қолдиқлари`, 3 info-card
  sentences for initial_balance.html, and the stations.html empty-state sentence.
- `templates/fuel/dashboard.html`: all Russian labels wrapped in `t()`. Two literal
  АЗС table headers additionally wrapped in `{{ t('АЗС') }}` (review fix).
- `templates/fuel/warehouses.html`: all visible Russian strings wrapped in `t()`.
- `templates/fuel/transactions.html`: all visible Russian strings wrapped in `t()`.
  Loop var renamed `t` → `txn`.
- `templates/fuel/receipts.html`: all visible Russian strings wrapped in `t()`.
- `templates/fuel/stations.html`: all visible Russian strings wrapped in `t()`.
  Hardcoded Russian empty state sentence fixed in review (review fix).
- `templates/fuel/initial_balance.html`: all visible Russian strings wrapped in `t()`.
  3 hardcoded Russian info-card sentences fixed in review (review fix).
- `fuel_routes.py`: `g` added to Flask imports. `fuel_t(uz, ru)` helper function added.
  All 12 Russian flash messages replaced with `fuel_t(...)` bilingual calls.
- No database changes. No migration changes. No Topaz API logic changes.
  No endpoint URL changes. No business logic changes.
- py_compile: ALL PASS. App import: OK.
- TransportReport service restarted successfully. Site opened successfully after restart.
- All six fuel pages manually verified in UZ/RU: /fuel/, /fuel/warehouses,
  /fuel/transactions, /fuel/receipts, /fuel/stations, /fuel/initial-balance.
- Known limitation: JS `confirm()` dialogs with `{{ wh.name }}`/`{{ st.name }}` variables
  not fully translated (technical constraint — mixed Jinja/JS string escaping).
  The `confirm()` for receipts delete (no variable) uses `t('Ўчириш')`.

**TASK-UI-001B Phase 1 — Translation fixes (2026-05-23 — COMPLETED)**

- `translations.py`: 32 new UZ/RU key pairs added (all Phase 1 keys from audit plus
  adjacent catalog/form strings).
- `templates/base.html`: Admin dropdown label and Wialon "Импорт / Маппинг" wrapped
  in `t()`. JS multiselect strings (Танланмаган / Барчаси / та танланди) wrapped.
- `templates/deficiencies.html`: Card header and empty state wrapped in `t()`.
- `templates/admin_users.html`: "Наблюдатель" role option and "Блокланган" badge wrapped.
- `templates/ref_equipment.html`: Inline edit form labels (5 fields) wrapped in `t()`.
- `templates/spare_parts_list.html`: Catalog button, org filter, date range labels
  (дан/гача), table headers (Позициялар/Ҳолат/Яратилган), Кўриш button wrapped.
- `templates/spare_part_detail.html`: Back button, card headers, Яратди label,
  Позициялар header, Номи/Арт. рақами table headers, Кўриб чиқиш header wrapped.
- `templates/spare_part_form.html`: Back button, h1, card header, Позициялар header,
  Номи/Арт. рақами table headers, + Қўшиш button wrapped.
- `templates/spare_parts_catalog.html`: Back button, h1, card header, form labels
  (Номи/Арт. рақами/Категория), Бекор қилиш button, count header, table headers wrapped.
- `templates/wialon.html`: Period mode labels (Кунлик/Жорий ҳафта/Жорий ой/Ихтиёрий)
  and empty state wrapped in `t()`.
- `templates/workload.html`: Норма/Факт column headers and empty state paragraph wrapped.
- py_compile: ALL PASS (`translations.py`, `app.py`, `fuel_routes.py`, `spare_parts.py`,
  `wialon_import.py`, `workload_report.py`).
- App import check: OK.
- TransportReport service restarted successfully. Site opened successfully after restart.
- Fuel module translation and flash messages remain Phase 2 (pending business confirmation).

**TASK-UI-001A — Translation audit (2026-05-23 — COMPLETED)**

- All 34 templates inspected. translations.py reviewed. Flash messages in all 4 Python
  route modules scanned.
- No mojibake found.
- 4 gap categories identified: fuel module (entirely Russian), fuel flash messages
  (Russian), scattered hardcoded strings in 10+ other templates, 19 missing keys.
- `docs/UI_TRANSLATION_AUDIT.md` created with full findings, proposed fix list,
  risks, and manual test checklist.
- py_compile passed on all 6 listed files.
- TASK-UI-001B (implementation) marked pending.

**TASK-OPS-002B — Backfill script run on production (2026-05-23 — COMPLETED)**

- `migrate_001_backfill_historical_registry.py` run successfully on production.
- Run output: inserted=8, skipped=0. Self-recorded as migrate_001_backfill_historical_registry.
- `schema_migrations` verified with 10 rows:
  1. migrate_000_migration_registry
  2. migrate_to_v3
  3. migrate_add_wialon
  4. migrate_to_v45
  5. migrate_v46
  6. migrate_tasks_abc3
  7. migrate_fuel_v2
  8. migrate_equipment_excel
  9. migrate_module_permissions
  10. migrate_001_backfill_historical_registry
- TransportReport service started successfully after the migration.
- `migrate_v47.py`: OBSOLETE warning block added at the top. Logic unchanged.
- `docs/MIGRATION_BACKFILL_ANALYSIS.md`: status and outcome sections updated.
- `docs/AGENT_STATE.md` and `docs/TASKS.md`: updated.

**TASK-OPS-002A — Backfill migration registry analysis (2026-05-23 — COMPLETED)**

- `docs/MIGRATION_BACKFILL_ANALYSIS.md` created: full classification of 14 historical
  migration scripts with evidence table, recommended backfill list, risks, and human
  confirmation checklist.
- `docs/AGENT_STATE.md`: corrected stale note about migrate_000 production status.
- `docs/TASKS.md`: TASK-OPS-002 updated to reflect analysis phase completion.
- No database writes. No code changes. No service restarts. Analysis only.

## TASK-SEC-003A production completion record

- Status: COMPLETED on production.
- Production date: 2026-05-26.
- GitHub commit: f51aac2 Add personal users password workflow and audit log.
- Post-release documentation date: 2026-06-02.
- Post-release DB backup: D:\transport-report-backups\production\daily\transport_20260602_165046.db.
- File rollback backup: D:\transport-report-backups\production\sec003a_code_backups\sec003a_prod_file_backup_20260526_100813.
- Verified: temporary password, forced password change, admin audit log page, audit events user_created/login_success/password_changed/logout.
- Rule: old shared operator account must be blocked only after all named operators confirm access; do not delete it.

## TASK-SEC-003B Phase 1 production completion record

- Status: COMPLETED on production.
- Production date: 2026-06-02.
- GitHub commit: 4c48c97 Add business action audit logging.
- Scope: business action audit logging for daily records and reference directories.
- Verified: daily_records_saved, customer_created, customer_deleted.
- Audit log time display fixed to local Uzbekistan time UTC+5.
- Pre-release DB backup: D:\transport-report-backups\before_sec003b_phase1\transport_before_sec003b_phase1_20260602_212510.db.
- File rollback backup: D:\transport-report-backups\production\sec003b_phase1_code_backups\sec003b_phase1_prod_file_backup_20260602_212510.


## 2026-06-04 - QA001 + BACKUP001 completed

- Created docs/QA_CHECKLIST.md with mandatory staging/production release smoke checks.
- Created docs/BACKUP_RESTORE_TEST_20260604.md with real restore-test evidence.
- Created docs/RELEASE_QA_BACKUP_20260604.md.
- Restore test folder: C:\transport-report-restore-test.
- Restored backup: D:\transport-report-backups\production\daily\transport_20260604_104248.db.
- Restored database path: C:\transport-report-restore-test\instance\transport.db.
- Restored DB size: 51,245,056 bytes.
- SQLite integrity_check: ok.
- Restored DB table count: 32.
- Restore app import check passed: RESTORE APP IMPORT OK.
- Production was not modified during the restore test.
- No database migration required.

## REPORT001C completed

Date/time: 2026-06-04 14:49:09  
Production: http://10.103.25.14:5050  
Backup: D:\transport-report-backups\production\daily\transport_20260604_144053.db

Current state:
- REPORT001C Fuel report and analytics is deployed to production.
- Database migration was not required.
- Production smoke test passed.
- Repository is expected to contain only REPORT001C source/doc changes before commit.

Changed files:
- fuel_routes.py
- templates/fuel/dashboard.html
- templates/fuel/report.html
- docs/RELEASE_REPORT001C_FUEL_REPORT_20260604.md
- docs/TASKS.md
- docs/AGENT_STATE.md


## 2026-06-04 — REPORT001D completed

REPORT001D — Fuel anomalies and warnings завершён и установлен на production.

Production:
- URL: http://10.103.25.14:5050/fuel/report
- Backup before deployment: `D:\transport-report-backups\production\daily\transport_20260604_145915.db`
- DB migration: not required
- Production smoke test: passed

Files changed:
- `fuel_routes.py`
- `templates/fuel/report.html`
- `docs/RELEASE_REPORT001D_FUEL_ANOMALIES_20260604.md`
- `docs/TASKS.md`
- `docs/AGENT_STATE.md`

Next recommended stage:
- REPORT001E or ERP-DASH001: management dashboard combining transport work, fuel, Wialon and spare-parts indicators.

## State update — 2026-06-05 — REPORT001E-1 completed

Latest completed release: REPORT001E-1 — Fuel warning registry.

Production state:
- commit pending at documentation step;
- database migration completed;
- `fuel_warning_reviews` table exists;
- production smoke test passed;
- repository expected to be clean after commit and cleanup.

Key production backup:
- D:\transport-report-backups\production\daily\transport_20260605_115535.db

Next planned stage:
- DASH001 — management dashboard for the main page.

## State update — 2026-06-06 — DASH001 completed

Latest completed release: DASH001 — Management dashboard for main page.

Production state:
- main page includes management dashboard;
- database migration was not required;
- production smoke test passed;
- repository expected to be clean after commit and cleanup.

Key production backup:
- D:\transport-report-backups\production\daily\transport_20260606_093202.db

Recommended next stage:
- DASH002 — dashboard drill-down links, severity highlighting, and role-aware dashboard polish.

## State update - 2026-06-13 - EXTAUDIT001, QA003, OPS002C closed

Latest completed stage:

- EXTAUDIT001 critical remediation closed.
- QA003 post-FIX003A regression audit completed: PASS WITH NOTES.
- OPS002C closed with owner-confirmed safe decision.
- No additional historical data-only migrations were recorded.
- No database changes were made for OPS002C closure.

Key commits:

- `c76ae42` - Document EXTAUDIT001 closure.
- `99611b8` - Document QA003 post-FIX003A regression audit.
- `32c13b7` - Document OPS002C pending migration confirmation.
- `fe0b991` - Document OPS002C closure.

Current production state:

- production HEAD: `fe0b991`
- staging HEAD: `fe0b991`
- origin/main: `fe0b991`
- production git status: clean
- staging git status: clean
- production services running: `TransportReport`, `TransportBot`, `TransportBot003`
- BOT003 dry-run: error null

OPS002C final decision:

- `migrate.py`: NO / NOT SURE - not recorded.
- `migrate_equipment.py`: NO / NOT SURE - not recorded.
- `migrate_worktypes.py`: NO / NOT SURE - not recorded.
- `migrate_categories_v9.py`: NO / NOT SURE - not recorded.
- `migrate_v42.py`: SKIP.

Recommended next stage:

1. `DASH002` - dashboard drill-down links, severity highlighting, role-aware polish.
2. `TASK-REF-001` - equipment reference improvements.
3. `TASK-REPORT-001` - multi-select report filters.

## State update - 2026-06-13 - DASH002B completed

Latest completed product stage:

- DASH002B main dashboard drill-down links completed and deployed to production.
- Main dashboard route remains `/`.
- There is no separate `/dashboard` route.
- Production HEAD after documentation sync: `30aeecf`.

Implemented:

- Quick drill-down links in dashboard cards.
- Warning severity banner.
- Warning links placement corrected to the warning card.
- Role-aware module access preserved.
- Template-only change in `templates/index.html`.

Production validation:

- Source backup created:
  - `D:\transport-report-backups\production\source\index_before_dash002b_20260613_160341.html`
- DB backup created:
  - `D:\transport-report-backups\production\daily\transport_dash002b_before_20260613_160341.db`
- Backup integrity: ok.
- `py_compile`: passed.
- App import: ok.
- Authenticated `/`: 200.
- `/login`: 200.
- Anonymous `/`: 302 to login, expected.
- `TransportReport`: running.
- `TransportBot`: running.
- `TransportBot003`: running.
- BOT003 dry-run: error null.

Key commits:

- `6d3fd4c` - Improve main dashboard drill-down links.
- `d05b673` - Fix dashboard warning quick links placement.
- `30aeecf` - Document DASH002B production rollout.

Recommended next stage:

1. DASH002C - small dashboard cleanup/polish after production user feedback.
2. TASK-REF-001 - equipment/reference improvements.
3. TASK-REPORT-001 - multi-select report filters.

## State update - 2026-06-13 - DASH002C completed

Latest completed product stage:

- DASH002C dashboard legacy report separation polish completed and deployed to production.
- Main dashboard route remains `/`.
- There is still no separate `/dashboard` route.
- Production HEAD after rollout report sync: `2152d32`.

Implemented:

- Top page header changed to main panel wording.
- Legacy daily report/filter block separated visually from dashboard.
- Added legacy section title and description.
- Added quick actions for data entry and full report.
- Kept old daily report/filter functionality visible and unchanged.
- Template-only UI polish in `templates/index.html`.

Production validation:

- Source backup created:
  - `D:\transport-report-backups\production\source\index_before_dash002c_20260613_162522.html`
- DB backup created:
  - `D:\transport-report-backups\production\daily\transport_dash002c_before_20260613_162522.db`
- Backup integrity: ok.
- `py_compile`: passed.
- App import: ok.
- Authenticated `/`: 200.
- `/login`: 200.
- Anonymous `/`: 302 to login, expected.
- `TransportReport`: running.
- `TransportBot`: running.
- `TransportBot003`: running.
- BOT003 dry-run: error null.

Key commits:

- `db191cd` - Polish dashboard legacy report separation.
- `2152d32` - Document DASH002C production rollout.

Recommended next stage:

1. TASK-REF-001 - equipment/reference improvements.
2. TASK-REPORT-001 - multi-select report filters.
3. UX003 - continued interface cleanup based on operator feedback.

## State update - 2026-06-13 - TASK-REF-001A completed

Latest completed product stage:

- TASK-REF-001A equipment reference filters and diagnostics completed and deployed to production.
- Production HEAD after rollout report sync: `79655e2`.

Implemented:

- `/ref/equipment` now supports search by equipment name, plate, type, organization name, and organization short name.
- Added status filter: all / active / inactive.
- Added statistics cards for equipment reference quality overview.
- Added diagnostics for:
  - empty default unit
  - zero default price
  - duplicate normalized plate groups
- Added inactive equipment visual marker.
- Added linked-record count near delete/disable actions.
- Excel export respects search and status filters.

Safety boundaries:

- No database schema changes.
- No migration scripts.
- No automatic deduplication.
- No merge of existing equipment records.
- No changes to existing `equipment_id` links.
- No changes to daily report, Wialon, fuel, spare-parts, Telegram bot, or BOT003 business logic.

Production validation:

- Source backups created:
  - `D:\transport-report-backups\production\source\app_before_task_ref_001a_20260613_165401.py`
  - `D:\transport-report-backups\production\source\ref_equipment_before_task_ref_001a_20260613_165401.html`
- DB backup created:
  - `D:\transport-report-backups\production\daily\transport_task_ref_001a_before_20260613_165401.db`
- Backup integrity: ok.
- `py_compile`: passed.
- App import: ok.
- Template load: ok.
- Authenticated `/ref/equipment`: 200.
- Filtered `/ref/equipment` checks: 200.
- Export `/ref/equipment/export?status=active&q=MTZ`: 200.
- `/login`: 200.
- Anonymous protected routes redirect to login as expected.
- `TransportReport`: running.
- `TransportBot`: running.
- `TransportBot003`: running.
- BOT003 dry-run: error null.

Key commits:

- `a7865f1` - Improve equipment reference filters and diagnostics.
- `79655e2` - Document TASK-REF-001A production rollout.

Recommended next stage:

1. TASK-REF-001B - continue reference improvements for work types/customers/organizations.
2. TASK-REPORT-001 - multi-select report filters.
3. UX003 - continued interface cleanup based on operator feedback.

## 2026-06-13  Current state after TASK-REF-001B

Current Git state after production rollout:

- Staging/prod/origin main target commit: `be30d1d`
- Latest completed feature: `TASK-REF-001B`
- Production rollout completed and manually confirmed by browser screenshots.
- Production service state:
  - `TransportReport`: RUNNING
  - `TransportBot`: RUNNING
  - `TransportBot003`: RUNNING

TASK-REF-001B changed only reference-page UI/controller diagnostics:

- `/ref/organizations`
- `/ref/work_types`
- `/ref/customers`

Important safety notes:

- No DB schema changes.
- No migrations.
- No reference data cleanup yet.
- Duplicate work-type names are only diagnosed, not merged.
- Customer values used in daily reports but missing from `customers` are only diagnosed, not normalized.
- Existing historical daily report values remain untouched.

Next recommended direction:

- Continue with reference/data-quality improvements only after deciding manual vs controlled migration strategy for customers and work types.
- Avoid automatic customer normalization until business rules are defined.

## 2026-06-13  Current state after TASK-REF-001C discovery

TASK-REF-001C was completed as a read-only production data quality discovery.

Current decision:

- Do not normalize customers automatically.
- Do not merge duplicate work types automatically.
- Do not change historical `daily_records.work_type` or `daily_records.customer` without a controlled migration plan.
- Next recommended step is `TASK-REF-001D`: diagnostic/export tools for manual cleanup planning.

Important discovered numbers:

- `work_types = 104`
- `customers = 9`
- `daily_records = 15946`
- duplicate work type name groups: 3
- missing work type exact values: 5
- distinct customer values in reports: 2028
- customer values missing from reference table: 2020

Related doc:

- `docs/TASK_REF_001C_DISCOVERY_AND_STRATEGY_20260613.md`

## 2026-06-13  Current state after TASK-REF-001D

TASK-REF-001D was completed on production.

Current Git target:

- `34acb33 Add reference cleanup diagnostic exports`

Production service state:

- `TransportReport`: RUNNING
- `TransportBot`: RUNNING
- `TransportBot003`: RUNNING

Implemented read-only export routes:

- `/ref/work_types/export_diagnostics`
- `/ref/customers/export_diagnostics`

Important safety notes:

- No DB schema changes.
- No migrations.
- No data modifications.
- No automatic normalization.
- Export files are diagnostic/manual-cleanup planning tools only.

Next recommended step:

- `TASK-REF-001E`: safe work type reference fixes after business approval:
  - fill empty default unit for `Шудгор (нақд ёқилғисиз)`
  - fill zero prices for `Хар хил иш (рейс)` and `Шоли ташиш`
  - decide whether to add missing reference rows
  - do not alter historical `daily_records` yet

## 2026-06-13  Current state after UX002A

UX002A was completed on production.

Current Git target:

- `1d0488c Add shared UX design system baseline`

Production service state:

- `TransportReport`: RUNNING
- `TransportBot`: RUNNING
- `TransportBot003`: RUNNING

Implemented:

- shared UX design system baseline in `templates/base.html`
- common styling for page headers, cards, filters, buttons, forms, tables, badges, flash blocks, responsive layout, and print layout

Important safety notes:

- No DB schema changes.
- No migrations.
- No route changes.
- No business logic changes.
- No bot logic changes.

Next recommended step:

- `UX002B / REPORT002A`: improve full report UX:
  - clearer report header and filter block
  - better summary cards
  - cleaner table wrapping/density
  - export/action area
  - no business logic changes in first pass

## 2026-06-13  Current state after REPORT002A

REPORT002A was completed on production.

Current Git target:

- `e2282d7 Fix REPORT002A date dash consistency`

Production service state:

- `TransportReport`: RUNNING
- `TransportBot`: RUNNING
- `TransportBot003`: RUNNING

Implemented:

- `/report` UX refresh
- report page header
- visible active filter summary
- report filter pills
- export/filter card styling
- report form CSS hook
- report KPI grid hook
- report table styling hook

Important safety notes:

- No DB schema changes.
- No migrations.
- No route changes.
- No business logic changes.
- No Excel generation logic changes.
- No bot logic changes.

Next recommended step:

- `UX002C / ENTRY002A`: improve daily entry page UX:
  - clearer entry header and context
  - better field grouping
  - clearer save/copy action area
  - safer visual hints for empty/idle/working rows
  - template-first approach without changing save logic

## 2026-06-14  Current state after ENTRY002A

ENTRY002A was completed on production.

Current Git target before docs-only production sync:

- `253beac Fix ENTRY002A staging doc markers`

Production service state:

- `TransportReport`: RUNNING
- `TransportBot`: RUNNING
- `TransportBot003`: RUNNING

Implemented:

- `/entry` UX refresh
- entry page header
- date and context summary pills
- guidance panel
- filter card styling
- filter form CSS hook
- save form CSS hook
- organization/equipment card visual styling
- working vs idle visual grouping
- sticky bottom save area styling
- non-blocking visual hints for incomplete working rows

Important safety notes:

- No DB schema changes.
- No migrations.
- No route changes.
- No `save_entry` changes.
- No `copy_previous_day` changes.
- No Excel/report logic changes.
- No bot logic changes.

Next recommended step:

- `UX002D / SPARE002A`: improve spare parts request/list UX:
  - clearer spare request header and status context
  - better filter/action layout
  - clearer request cards/table density
  - template-first approach without changing spare request business logic

## 2026-06-14  Current state after SPARE002A

SPARE002A was completed on production.

Current Git target before docs-only production sync:

- `6d391ab Fix spare parts header actions`

Production service state:

- `TransportReport`: RUNNING
- `TransportBot`: RUNNING
- `TransportBot003`: RUNNING

Implemented:

- `/spare-parts/` UX refresh
- `/spare-parts/new` UX refresh
- spare parts list page header
- status/context summary pills
- guidance panel
- filter form layout
- table visual density
- new request page header
- new request form grouping
- sticky action row styling
- non-blocking visual hints for incomplete item rows
- corrected top action buttons into one horizontal header row

Important safety notes:

- No DB schema changes.
- No migrations.
- No route changes.
- No `spare_parts.py` changes.
- No `save_request` changes.
- No `submit_request` changes.
- No `approve_request` changes.
- No `reject_request` changes.
- No BOT003 outbox logic changes.
- No bot logic changes.

Next recommended step:

- `UX002E / FUEL002A`: improve fuel/receipts UX using the same template-first approach:
  - clearer fuel receipts header and status context
  - better filter/action layout
  - table density/readability
  - no route or business logic changes in the first patch

## 2026-06-14  Current state after FUELST001

FUELST001 was completed on production.

Current Git target before docs-only production sync:

- `4aee239 Fix FUELST001 staging doc markers`

Production service state:

- `TransportReport`: RUNNING
- `TransportBot`: RUNNING
- `TransportBot003`: RUNNING

Implemented:

- Fixed `/fuel/stations` 500 error.
- Added template fallback for missing `L_form`.
- Confirmed `/fuel/stations` opens on production.

Important safety notes:

- No DB schema changes.
- No migrations.
- No route changes.
- No `fuel_routes.py` changes.
- No `save_station` changes.
- No `delete_station` changes.
- No `enable_station` changes.
- No BOT003 outbox logic changes.
- No bot logic changes.

Next recommended step:

- Resume `FUEL002A`: improve `/fuel/receipts` UX using template-first approach:
  - clearer header and summary pills
  - better filter/action layout
  - better receipt table readability
  - no route or business logic changes in the first patch

## 2026-06-14  Current state after FUEL002A receipts

FUEL002A receipts was completed on production.

Current Git target before docs-only production sync:

- `ed8955d Improve fuel receipts UX`

Production service state:

- `TransportReport`: RUNNING
- `TransportBot`: RUNNING
- `TransportBot003`: RUNNING

Implemented:

- Improved `/fuel/receipts` UX.
- Added summary/context strip.
- Added guidance panel.
- Improved form, filter and table readability.
- Confirmed `/fuel/receipts` opens and visual layout is accepted on production.

Important safety notes:

- No DB schema changes.
- No migrations.
- No route changes.
- No `fuel_routes.py` changes.
- No `save_receipt` changes.
- No `delete_receipt` changes.
- No BOT003 outbox logic changes.
- No bot logic changes.

Next recommended step:

- Continue FUEL UX phase with one of:
  - `FUEL002B`: improve `/fuel/transactions`
  - `FUEL002C`: improve `/fuel/warehouses`
  - `FUEL002D`: improve `/fuel/report`
  - `FUEL002E`: improve `/fuel/warnings`

## 2026-06-14  Current state after FUEL002B transactions

FUEL002B transactions was completed on production.

Current Git target before docs-only production sync:

- `44a706f Apply actual fuel transactions template UX`

Important note:

- `135ff40` and `3956887` did not contain the actual template change.
- The actual validated template change is `44a706f`.

Production service state:

- `TransportReport`: RUNNING
- `TransportBot`: RUNNING
- `TransportBot003`: RUNNING

Implemented:

- Improved `/fuel/transactions` UX.
- Added summary/context strip.
- Added guidance panel.
- Improved filter and dense table readability.
- Improved transaction table and sync logs table wrappers.
- Confirmed `/fuel/transactions` opens and visual layout is accepted on production.

Important safety notes:

- No DB schema changes.
- No migrations.
- No route changes.
- No `fuel_routes.py` changes.
- No transaction query changes.
- No Topaz sync changes.
- No BOT003 outbox logic changes.
- No bot logic changes.

Next recommended step:

- Continue FUEL UX phase with one of:
  - `FUEL002C`: improve `/fuel/warehouses`
  - `FUEL002D`: improve `/fuel/report`
  - `FUEL002E`: improve `/fuel/warnings`


## FUEL002C_WAREHOUSES_AGENT_STATE

Current completed milestone:

- FUEL002C warehouses UX and localization hotfix are deployed to production.
- Latest production HEAD: `81a1782e37f8f0317b0989e92d837245c35a2f1f`
- `HEAD == origin/main`
- `git status` clean
- Production services running:
  - `TransportReport`
  - `TransportBot`
  - `TransportBot003`
- BOT003 dry-run passed.
- Next recommended UX sequence: continue fuel admin pages after warehouses, or move to next queued module after user confirmation.


## FUEL002D_REPORT_AGENT_STATE

Current completed milestone:

- FUEL002D report UX is deployed to production.
- Latest production HEAD before docs-only close: `47bb0f29beff020fffb0de42eaeb58c22cd53d8e`
- `HEAD == origin/main`
- `git status` clean
- Production services running:
  - `TransportReport`
  - `TransportBot`
  - `TransportBot003`
- BOT003 dry-run passed.
- Next recommended UX sequence: continue fuel admin/report pages after user confirmation.


## FUEL002E_STATIONS_AGENT_STATE

Current completed milestone:

- FUEL002E stations UX is deployed to production.
- Latest production HEAD before docs-only close: `adace00c9cbb8ace90b060b5d1ae759cf78fd70f`
- `HEAD == origin/main`
- `git status` clean
- Production services running:
  - `TransportReport`
  - `TransportBot`
  - `TransportBot003`
- BOT003 dry-run passed.
- Next recommended UX sequence: continue remaining fuel pages or move to the next confirmed module.


## FUEL002F_INITIAL_BALANCE_AGENT_STATE

Current completed milestone:

- FUEL002F initial balance UX is deployed to production.
- Latest production HEAD before docs-only close: `da4565d49be2702ecc5873daa04cf6b66e071e8e`
- `HEAD == origin/main`
- `git status` clean
- Production services running:
  - `TransportReport`
  - `TransportBot`
  - `TransportBot003`
- BOT003 dry-run passed.
- Next recommended UX sequence: continue `/fuel/warnings`, then `/fuel/` dashboard.


## FUEL002G_WARNINGS_AGENT_STATE

Current completed milestone:

- FUEL002G warnings UX is deployed to production.
- Latest production HEAD before docs-only close: `0eef3e7b7e1891437731166a94ce057d102985fa`
- `HEAD == origin/main`
- `git status` clean
- Production services running:
  - `TransportReport`
  - `TransportBot`
  - `TransportBot003`
- BOT003 dry-run passed.
- Next recommended UX sequence: continue `/fuel/` dashboard after warnings.


## FUEL002H_DASHBOARD_AGENT_STATE

Current completed milestone:

- FUEL002H dashboard UX is deployed to production.
- Latest production HEAD before docs-only close: `713ced32dcb0a82814628be6f3b5ed46e53700e8`
- `HEAD == origin/main`
- `git status` clean
- Production services running:
  - `TransportReport`
  - `TransportBot`
  - `TransportBot003`
- BOT003 dry-run passed.
- Fuel section UX cycle FUEL002A-H is completed.
- Next recommended sequence: final QA pass for fuel module, then decide next module outside fuel.


## FUEL002_FINAL_QA_AGENT_STATE

Current completed milestone:

- FUEL002 fuel module UX cycle A-H is fully completed.
- Final QA passed on staging and production.
- Latest production HEAD before final QA docs-only close: `17df9143a0ae80b9f657736285ca816a94ed097d`
- Staging and production were both clean and synced to `origin/main`.
- All fuel routes returned HTTP 200.
- All fuel UX markers were present.
- Production services running:
  - `TransportReport`
  - `TransportBot`
  - `TransportBot003`
- Staging services running:
  - `TransportReportStaging`
  - `TransportBotStaging`
  - `TransportBot003Staging`
- BOT003 dry-run passed.
- Recommended next step: start read-only discovery for the next module outside fuel, likely main dashboard or spare-parts depending on priority.

## 2026-06-15  DASH002 Main dashboard UX completed

Status: completed and deployed to production.

Code commit:

`f2d73a9976e43346e9164d22ca33def90ba9d277`  `Improve main dashboard UX`

Summary:

- Improved main dashboard `/` UX.
- Changed only `templates/index.html`.
- Added `DASH002_MAIN_DASHBOARD_UX` marker.
- Preserved existing `/` route, filters, old daily report section, table, and all existing links.
- No DB, migration, business logic, Topaz, BOT003, or bot service changes.
- Staging visual QA passed.
- Production rollout passed.
- Production visual QA confirmed by user.
- Production service `TransportReport` restarted successfully.
- Bot services were not restarted and remained RUNNING.

Production backups:

- Source: `D:\transport-report-backups\production\source\DASH002_MAIN_DASHBOARD_UX_20260615_125933`
- DB: `D:\transport-report-backups\production\daily\transport_dash002_main_dashboard_ux_20260615_125933.db`

Next recommended stage:

1. Continue dashboard polish only if user reports visual issues.
2. Otherwise proceed to read-only discovery for the next module: `spare-parts`.

## 2026-06-15  SPARE001A Spare parts templates UX completed

Status: completed and deployed to production.

Code commit:

`53cfb078ca78782e7d7a17ffdb80ae1c30bb9509`  `Improve spare parts templates UX`

Summary:

- Improved spare parts module template UX.
- Changed only 4 templates:
  - `templates/spare_parts_list.html`
  - `templates/spare_part_form.html`
  - `templates/spare_part_detail.html`
  - `templates/spare_parts_catalog.html`
- Added `SPARE001A_TEMPLATE_UX` marker and scoped wrapper `spare001a-scope`.
- No DB, migration, route logic, POST handler, status transition, audit, BOT003, Telegram bot or Topaz changes.
- Staging technical validation passed.
- Staging visual QA confirmed by user.
- Production rollout passed.
- Production visual QA confirmed by user.
- Production service `TransportReport` restarted successfully.
- Bot services were not restarted and remained RUNNING.

Production backups:

- Source: `D:\transport-report-backups\production\source\SPARE001A_UX_20260615_131401`
- DB: `D:\transport-report-backups\production\daily\transport_spare001a_ux_20260615_131401.db`

Next recommended stage:

1. Commit this docs-only update and pull it to production without service restart.
2. Continue with next safe UX/discovery task only after user confirmation.

## 2026-06-15  SPARE001B spare parts status history audit/backfill completed

Status: completed on staging and production.

Summary:

- Performed read-only workflow audit of spare parts module.
- Confirmed existing code already writes `SparePartStatusHistory` for new submitted/approved/rejected transitions.
- Found historical data gap: existing requests had zero status history rows.
- Performed staging DB backfill after backup.
- Performed production DB backfill after backup.
- No code files changed.
- No DB schema changed.
- No migration added.
- No service restart performed.

Staging result:

- Requests: 8
- Inserted history rows: 9
- Remaining history gaps: 0
- Backup: `D:\transport-report-backups\staging\daily\transport_spare001b_status_history_backfill_20260615_133549.db`

Production result:

- Requests: 3
- Inserted history rows: 4
- Validation errors: 0
- Backup: `D:\transport-report-backups\production\daily\transport_spare001b_status_history_backfill_20260615_133738.db`

Services:

- `TransportReportStaging`: RUNNING
- `TransportBotStaging`: RUNNING
- `TransportBot003Staging`: RUNNING
- `TransportReport`: RUNNING
- `TransportBot`: RUNNING
- `TransportBot003`: RUNNING

Next recommended stage:

- SPARE001C: controlled staging workflow test for create draft, submit, approve/reject, BOT003 outbox and status history for a new test request.

## 2026-06-15  SPARE001C controlled staging spare parts workflow test completed

Status: completed on staging.

Summary:

- Performed controlled staging-only spare parts workflow test.
- Created request 9: `draft -> submitted -> approved`.
- Created request 10: `submitted -> rejected`.
- Confirmed status history rows for all expected transitions.
- Confirmed audit logs for created requests/items/status changes.
- Confirmed BOT003 outbox events for submitted/approved/rejected.
- Confirmed `TransportBot003Staging` delivered all 4 events.
- No source code changed.
- Production was not touched.
- No service restart performed.

Staging test run:

- Run tag: `SPARE001C_TEST_20260615_134745`
- Backup: `D:\transport-report-backups\staging\daily\transport_spare001c_workflow_test_20260615_134745.db`
- Test requests: 9 and 10
- BOT003 sent rows: 4
- Pending rows: 0
- Failed rows: 0

Services:

- `TransportReportStaging`: RUNNING
- `TransportBotStaging`: RUNNING
- `TransportBot003Staging`: RUNNING

Next recommended stage:

- SPARE001D: read-only role/access audit for spare parts permissions and organization filtering.

## 2026-06-15  SPARE001D spare parts role/access audit and permission enablement completed

Status: completed on staging and production.

Summary:

- Performed read-only role/access audit on staging.
- Performed read-only role/access audit on production.
- Confirmed active operators had organizations assigned but `spare_parts_access=0`.
- Enabled `spare_parts` access for active operators on staging.
- Validated operator access on staging.
- Enabled `spare_parts` access for active operators on production.
- Validated operator access on production.
- Confirmed catalog remains admin-only.
- No source code changed.
- No schema changed.
- No migration added.
- No service restart performed.

Operators enabled:

- `muhiddin`
- `abdugani`
- `mirfayz`
- `sardor`

Staging backups:

- `D:\transport-report-backups\staging\daily\transport_spare001d3_enable_spare_parts_active_ops_20260615_141345.db`
- `D:\transport-report-backups\staging\daily\transport_spare001d3_enable_spare_parts_active_ops_20260615_141354.db`

Production backup:

- `D:\transport-report-backups\production\daily\transport_spare001d4_enable_spare_parts_active_ops_20260615_141801.db`

Next recommended stage:

- SPARE001E: controlled operator workflow test on staging using a real active operator account path, not admin, to confirm end-user experience.

## 2026-06-15  SPARE001E controlled operator workflow test on staging completed

Status: completed on staging.

Summary:

- Performed controlled spare parts workflow test as active operator `muhiddin`.
- Initial test script failed before creating a request due to nonexistent `can_access_module` method; no request was created in that attempt.
- Fixed test used `user_module_permissions` for permission check.
- Created request 11 as `muhiddin`: `draft -> submitted`.
- Created request 12 as `muhiddin` due to repeated fixed run: `draft -> submitted`.
- Confirmed operator cannot approve or reject requests: `403 Forbidden`.
- Confirmed operator cannot open catalog: `403 Forbidden`.
- Confirmed detail page renders for operator.
- Confirmed status history rows for request 11 and 12.
- Confirmed audit logs with username snapshot `muhiddin`.
- Confirmed BOT003 staging delivered both submitted notifications.
- No source code changed.
- Production was not touched.
- No service restart performed.

Backups:

- `D:\transport-report-backups\staging\daily\transport_spare001e_operator_workflow_test_20260615_142154.db`
- `D:\transport-report-backups\staging\daily\transport_spare001e_operator_workflow_test_fixed_20260615_142410.db`
- `D:\transport-report-backups\staging\daily\transport_spare001e_operator_workflow_test_fixed_20260615_142426.db`

Next recommended stage:

- SPARE001F: final spare parts module documentation/QA closure, or move to the next feature after user approval.

## 2026-06-15  SPARE001F final spare parts QA closure completed

Status: completed on staging and production.

Summary:

- Performed final read-only QA for spare parts module on staging and production.
- Confirmed staging and production are synchronized on commit `2740519babb05dd7d640307a0c87f1d2d6fa62b1`.
- Confirmed services are RUNNING in both environments.
- Confirmed active operators have `spare_parts_access=1`.
- Confirmed all active operators have 17 assigned organizations.
- Confirmed status history coverage has no gaps.
- Confirmed BOT003 outbox has no pending/failed rows.
- Confirmed admin routes work.
- Confirmed operator list/new/detail routes work.
- Confirmed operator catalog access returns `403 Forbidden`.
- Confirmed unauthenticated users redirect to login.
- No DB writes were performed.
- No POST requests were executed.
- No source code changed.
- No service restart performed.

Final result:

- `STAGING_SPARE001F_FINAL_QA_OK=YES`
- `PRODUCTION_SPARE001F_FINAL_QA_OK=YES`

Spare parts module QA cycle is closed for the current scope.

## 2026-06-15  REPORT002 general `/report` validation completed

Status: completed on staging and production.

Summary:

- Confirmed `/report` already contains `REPORT002A_MARKER`.
- Confirmed `/report` GET works for admin and operator.
- Confirmed filtered GET works for admin and operator.
- Confirmed CSRF token is present in report form.
- Confirmed Excel export works for admin main report.
- Confirmed Excel export works for admin daily activity report.
- Confirmed Excel export works for operator main report.
- Confirmed generated `.xlsx` files are valid.
- Confirmed temporary export files were created only in isolated temp folders and removed.
- Confirmed DB counts did not change.
- Confirmed unauthenticated `/report` redirects to login.
- Confirmed `/report/` returns 404 under current route configuration.
- Confirmed services remained RUNNING.
- No source code changed.
- No DB writes were performed.
- No service restart performed.

Final result:

- `REPORT002_3B_FUNCTIONAL_VALIDATION_STAGING_OK=YES`
- `REPORT002_4_FUNCTIONAL_VALIDATION_PRODUCTION_OK=YES`

General `/report` route is closed for the current Claude-audit scope.

## 2026-06-15  UI003 general UI/design unification completed

Status: completed on staging and production.

Summary:

- Completed whole-application UI/template inventory.
- Confirmed 39 templates total.
- Confirmed 38 user-facing templates.
- Confirmed 14 templates already had known refresh markers.
- Confirmed no old Bootstrap `panel panel` usage.
- Confirmed no old `btn-default` usage.
- Completed route render audit for 30 GET rules and 60 user/render combinations.
- Confirmed route render errors count: 0.
- Confirmed traceback/Internal Server Error signals: none.
- Completed targeted source audit of unmarked templates.
- Confirmed all reviewed templates were acceptable modern unmarked templates except `error.html`.
- Applied template-only patch to `templates/error.html`.
- Added marker `UI003A_ERROR_TEMPLATE`.
- Validated staging error template render for 403, 404 and 500.
- Validated production error template render for 403, 404 and 500.
- Confirmed 404 smoke check on staging and production.
- Confirmed DB counts did not change.
- Confirmed no POST requests were executed.
- Restarted staging and production web service only to load template change.
- Confirmed bot services remained RUNNING.

Final code commit:

`c0fa7628fee91dc1fecbb6f7af88653eef45525c`  `Improve error page UI`

UI003 is closed for the current Claude-audit scope.

## 2026-06-15  QA003 final whole-application QA completed

Status: completed on staging and production.

Summary:

- Completed final read-only whole-application QA on staging.
- Completed final read-only whole-application QA on production.
- Confirmed app import OK on both environments.
- Confirmed required DB tables present.
- Confirmed active admin and active operators exist.
- Confirmed 5 active application modules.
- Confirmed organizations and equipment tables populated.
- Confirmed UI markers present:
  - `DASH002`
  - `FUEL002`
  - `SPARE001A_TEMPLATE_UX`
  - `REPORT002`
  - `UI003A_ERROR_TEMPLATE`
- Confirmed unauthenticated smoke checks passed.
- Confirmed authenticated GET route render QA passed.
- Confirmed 30 GET rules audited and 60 render/access rows checked.
- Confirmed route render errors count: 0.
- Confirmed warnings count: 0.
- Confirmed errors count: 0.
- Confirmed DB counts unchanged.
- Confirmed no POST requests were executed.
- Confirmed no service restart was performed.
- Confirmed production services remained RUNNING:
  - `TransportReport`
  - `TransportBot`
  - `TransportBot003`

Final markers:

- `QA003_1_FINAL_READ_ONLY_QA_STAGING_OK=YES`
- `QA003_1_FINAL_WHOLE_APPLICATION_READ_ONLY_QA_STAGING=OK`
- `QA003_2_FINAL_READ_ONLY_QA_PRODUCTION_OK=YES`
- `QA003_2_FINAL_WHOLE_APPLICATION_READ_ONLY_QA_PRODUCTION=OK`

QA003 is closed for the current Claude-audit scope.

Current base commit:

`374e4e5a0b4e226784b866da7f0414276a2f60d9`  `Document UI003 general UI unification`

## 2026-06-15  DOC003 final project state documentation completed

Status: completed as docs-only closure.

Summary:

- Created final project state document.
- Consolidated completed stages:
  - FUEL002
  - DASH002
  - SPARE001A-F
  - REPORT002
  - UI003
  - QA003
  - DOC003
- Confirmed current production/staging base before DOC003:
  `5dbdeb6bca34aec5ac22ba4feebbdd45c7f926a0`  `Document QA003 final whole app QA`
- Confirmed current project closure sequence is complete.
- Confirmed remaining future items are non-blocking and must be handled as separately scoped future tasks.

DOC003 is the final documentation/state closure stage for the current Claude-audit sequence.

## 2026-06-15  FUEL-IDX-001 fuel transaction date indexes completed

Status: completed on staging and production.

Code commit:

`62001d48886f8a1342cc83a2ab958dc3d8a53ef2`  `Add fuel transaction date indexes`

Summary:

- Confirmed EXTAUDIT002 finding with read-only staging query plan audit.
- Added indexes to active `fuel_transactions2` table:
  - `ix_fuel_transactions2_txn_datetime`
  - `ix_fuel_transactions2_station_datetime`
- Added idempotent migration:
  - `migrate_fuel_idx_001.py`
- Updated model declaration:
  - `FuelTransaction2.__table_args__`
- Applied migration on staging DB.
- Applied migration on production DB.
- Confirmed business data unchanged.
- Confirmed query plans now use covering indexes for date-range and station+date-range queries.
- Confirmed app import OK.
- Confirmed production HTTP smoke for `/login`, `/fuel/`, `/fuel/report`.
- Confirmed production services RUNNING after rollout:
  - `TransportReport`
  - `TransportBot`
  - `TransportBot003`

Remaining related future task:

- FUEL-IDX-002: replace non-sargable `date(txn_datetime)` filters with explicit datetime ranges.

## 2026-06-15 - FUEL-IDX-002 sargable fuel transaction date filters completed

Status: completed on staging and production.

Code commit:

781a826eab6e7e662032b1da1d29a373912a24fd

Summary:

- Completed follow-up to FUEL-IDX-001.
- Replaced remaining non-sargable func.date(FuelTransaction2.txn_datetime) == date.today() filters in fuel_routes.py.
- Replaced them with explicit day range filters:
  - txn_datetime >= today_start
  - txn_datetime < next_day_start
- Confirmed old and new counts match on staging and production.
- Confirmed query plan uses ix_fuel_transactions2_txn_datetime.
- Confirmed no remaining func.date(...) calls in fuel_routes.py.
- Confirmed production smoke for:
  - /login
  - /fuel/
  - /fuel/stations
  - /fuel/transactions
  - /fuel/report
- Restarted only TransportReport on production.
- TransportBot and TransportBot003 were not restarted.
- No DB writes were performed.

## 2026-06-15 - CLEAN-TPL-001 orphaned legacy fuel template cleanup completed

Status: completed on staging and production.

Code commit:

3bb4385472c54fefb19656608df33e3398085bfd

Summary:

- Removed five confirmed orphaned root-level legacy fuel templates:
  - templates/fuel_balance.html
  - templates/fuel_dashboard.html
  - templates/fuel_history.html
  - templates/fuel_receipts.html
  - templates/fuel_sync_log.html
- Confirmed current Flask render_template references use active templates/fuel/*.html.
- Confirmed active fuel templates remain present.
- Confirmed authenticated render of key fuel pages on staging and production.
- Confirmed unauthenticated smoke on key fuel pages.
- No DB writes were performed.
- No POST requests were executed.
- No production service restart was performed.

## 2026-06-15 - SEC-HARD-001 basic security hardening completed

Status: completed on staging and production.

Code commit:

3b95db567dde716cd95a885d1b4e568af8153dcb

Summary:

- Added MAX_CONTENT_LENGTH default of 16 MiB in app.py.
- Added explicit 500 error handler in app.py.
- Replaced Topaz fuel sync direct token comparison with hmac.compare_digest in fuel_routes.py.
- Confirmed app import and URL rules count.
- Confirmed MAX_CONTENT_LENGTH runtime value is 16777216.
- Confirmed 500 handler is registered.
- Confirmed old payload.get('token') != api_token expression removed.
- Confirmed staging and production HTTP smoke.
- Restarted only TransportReport on production.
- TransportBot and TransportBot003 were not restarted.
- No DB writes were performed.
- No POST requests were executed.

## 2026-06-15 - API-FUEL-LEGACY-001 fuel sync legacy alias audit completed

Status: completed as docs-only decision closure.

Base commit:

2cb98a1cff176e459059946a7cf03b1b24102400

Summary:

- Audited legacy /api/fuel_sync alias.
- Confirmed canonical /fuel/api/fuel_sync endpoint exists.
- Confirmed legacy alias /api/fuel_sync exists.
- Confirmed both endpoints call the shared _perform_fuel_sync() logic.
- Confirmed CSRF exemption includes both fuel sync paths.
- Confirmed hmac.compare_digest is used in shared sync token check.
- Confirmed GET on both POST-only sync endpoints returns 405.
- Decision: keep /api/fuel_sync temporarily until Topaz agent configuration is confirmed updated.
- No source files were modified.
- No DB writes were performed.
- No POST requests were executed.
- No service restart was performed.

## 2026-06-15 - PERF-DASH-001 fuel dashboard/report query optimization completed

Status: completed and deployed to production.

Code commit:

45049de8fd279f5352d89090b61b4716698f27ef

Summary:

- Audited dashboard/report SQL query counts on staging.
- Confirmed high repeated SELECT counts on /, /fuel/, and /fuel/report.
- Optimized fuel_routes.py only.
- Reduced SELECT count:
  - / from 101 to 28;
  - /fuel/ from 84 to 11;
  - /fuel/report from 94 to 19.
- Production rollout completed with source-only pull.
- Only TransportReport was restarted.
- Telegram bot services were not restarted.
- Production smoke passed.
- Production services are running.

## 2026-06-15 - PERF-SPARE-001 spare parts index query optimization completed

Status: completed and deployed to production.

Code commit:

578980a4818c536d2ec77d22ef935c3005489e59

Summary:

- Audited /spare-parts/ SQL query count on staging.
- Confirmed repeated SELECT patterns:
  - spare_part_request_items;
  - equipment;
  - status counts;
  - users.
- Optimized spare_parts.py only.
- Reduced /spare-parts/ SELECT count from 29 to 4.
- Production rollout completed with source-only pull.
- Only TransportReport was restarted.
- Telegram bot services were not restarted.
- Production smoke passed.
- Production services are running.

## 2026-06-15 - AUDIT-GET-SIDE-EFFECT-001 Wialon GET export side effect fixed

Status: completed and deployed to production.

Code commit:

5c86893cf9175210822e502a1e85f259a51938e8

Summary:

- Audited GET routes for unintended database writes.
- Confirmed one side effect:
  - GET /wialon/report/export attempted INSERT INTO audit_logs.
- Diagnostic found source in wialon_import.py, function wialon_report_export.
- Removed _audit_wialon(...) and db.session.commit() from the GET export path.
- Preserved Excel export response.
- Staging validation confirmed DML count is now 0.
- Production rollout completed with source-only pull.
- Only TransportReport was restarted.
- Telegram bot services were not restarted.
- Production smoke passed.

## 2026-06-15 - AUDIT-GET-SIDE-EFFECT-002 Wialon workload GET export side effect fixed

Status: completed and deployed to production.

Code commit:

a1a2f1ac745ccc9aa7e91aa328377430064d7ac6

Summary:

- Expanded GET export/download DML audit.
- Confirmed one remaining Wialon export side effect:
  - GET /wialon/workload/export attempted INSERT INTO audit_logs.
- Source found in wialon_import.py, function wialon_workload_export.
- Removed _audit_wialon(...) and db.session.commit() from the GET workload export path.
- Preserved workload Excel export response.
- Confirmed /wialon/report/export fix from AUDIT-GET-SIDE-EFFECT-001 is still present.
- Staging validation confirmed DML count is now 0 for all sampled export/download GET routes.
- Production rollout completed with source-only pull.
- Only TransportReport was restarted.
- Telegram bot services were not restarted.
- Production smoke passed.

## 2026-06-16 - AUDIT-GET-SIDE-EFFECT-003 Logout GET side effect fixed

Status: completed and deployed to production.

Code commit:

8132dd9f866d6b28ef6466d58180ad9634e299b3

Summary:

- Ran broad read-only GET DML audit on staging.
- Confirmed one remaining GET side effect:
  - GET /logout attempted INSERT INTO audit_logs.
- Patched app.py only.
- Removed log_audit(...) and db.session.commit() from GET /logout.
- Preserved logout_user() and redirect behavior.
- Staging validation passed.
- Production rollout completed with source-only pull.
- Only TransportReport was restarted.
- Telegram bot services were not restarted.
- Extra corrected post-rollout revalidation passed on staging and production.
- Final checked routes had DML count 0:
  - /logout
  - /
  - /admin/audit
  - /fuel/
  - /fuel/report
  - /spare-parts/
  - /wialon/report/export
  - /wialon/workload/export

## 2026-06-16 - PERF-REF-001 Reference equipment linked counters optimized

Status: completed and deployed to production.

Code commit:

bdd9b6b6e4a96b1799643d9613875f8e43cf0a1d

Summary:

- Ran read-only SQL audit for reference pages.
- Confirmed `/ref/equipment` had a severe N+1 query pattern:
  - 1348 SELECT total.
  - 1344 repeated linked-record count queries.
- Source diagnostic confirmed the issue was in `app.py`, function `ref_equipment`.
- Replaced per-row `.count()` calls with four grouped bulk count maps.
- Changed only `app.py`.
- No DB schema changes.
- No migrations.
- No templates changed.
- Staging validation reduced `/ref/equipment` from 1348 SELECT to 8 SELECT.
- Production rollout completed with source-only pull.
- Only TransportReport was restarted.
- Telegram bot services were not restarted.
- Production validation confirmed `/ref/equipment` remains at 8 SELECT and DML count 0.

## 2026-06-16 - PERF-REF-002 Reference work types usage counters optimized

Status: completed and deployed to production.

Code commit:

7afcc32a068fd419bb5743c12f2decec2eded37c

Summary:

- Ran read-only source and SQL diagnostic for `/ref/work_types`.
- Confirmed `/ref/work_types` had 106 SELECT.
- Confirmed root cause:
  - 104 repeated `daily_records` count queries.
- Source diagnostic confirmed issue in `app.py`, function `ref_work_types`.
- Replaced per-row `.count()` calls with one grouped bulk usage count map.
- Reused the grouped map for missing-from-reference diagnostics.
- Changed only `app.py`.
- No DB schema changes.
- No migrations.
- No templates changed.
- Staging validation reduced `/ref/work_types` from 106 SELECT to 2 SELECT.
- Production rollout completed with source-only pull.
- Only TransportReport was restarted.
- Telegram bot services were not restarted.
- Production validation confirmed `/ref/work_types` remains at 2 SELECT and DML count 0.

## 2026-06-16 - PERF-REF-003 Reference customers usage counters optimized

Status: completed and deployed to production.

Code commit:

99cd898824ad75eca120795f3aa40325c50bc143

Summary:

- Ran read-only source and SQL diagnostic for `/ref/customers`.
- Confirmed `/ref/customers` had 11 SELECT.
- Confirmed root cause:
  - 9 repeated `daily_records` count queries.
- Source diagnostic confirmed issue in `app.py`, function `ref_customers`.
- Replaced per-customer `.count()` calls with one grouped bulk usage count map.
- Reused the grouped map for missing-from-reference diagnostics.
- Changed only `app.py`.
- No DB schema changes.
- No migrations.
- No templates changed.
- Staging validation reduced `/ref/customers` from 11 SELECT to 2 SELECT.
- Production rollout completed with source-only pull.
- Only TransportReport was restarted.
- Telegram bot services were not restarted.
- Production validation confirmed `/ref/customers` remains at 2 SELECT and DML count 0.

## 2026-06-16 - PERF-REF-004 Reference organizations linked counters optimized

Status: completed and deployed to production.

Code commit:

735fa96ee1373e22ece08c1fd4908e0b14b63306

Summary:

- Ran read-only source and SQL diagnostic for `/ref/organizations`.
- Confirmed `/ref/organizations` had 86 SELECT.
- Confirmed root cause:
  - 17 repeated `equipment` count queries.
  - 17 repeated `fuel_warehouses` count queries.
  - 17 repeated `spare_part_requests` count queries.
  - 17 repeated `deficiencies` count queries.
  - 17 repeated user relationship count queries.
- Source diagnostic confirmed issue in `app.py`, function `ref_organizations`.
- Replaced per-organization `.count()` calls with grouped bulk linked counter maps.
- Counted users through `user_organizations`, not through `User.organization_id`.
- Changed only `app.py`.
- No DB schema changes.
- No migrations.
- No templates changed.
- Staging validation reduced `/ref/organizations` from 86 SELECT to 6 SELECT.
- Production rollout completed with source-only pull.
- Only TransportReport was restarted.
- Telegram bot services were not restarted.
- Production validation confirmed `/ref/organizations` remains at 6 SELECT and DML count 0.

## 2026-06-16 - PERF-WIALON-MAP-001 Wialon mapping response size optimized

Status: completed and deployed to production.

Code commit:

86317434c0d39b89c7812225483e5ea8178358f2

Summary:

- Ran read-only source, SQL, and response diagnostic for `/wialon/mapping`.
- Confirmed `/wialon/mapping` response was about 19.9 MB.
- Confirmed root cause:
  - 128,692 repeated `<option>` elements.
  - full active equipment list rendered inside each mapping dropdown.
  - repeated organization lazy-load queries.
- Changed only:
  - `wialon_import.py`
  - `templates/wialon_mapping_list.html`
- Added eager loading for mapping equipment and organizations.
- Built one shared `equipment_options` list in the Flask view.
- Replaced repeated server-rendered option loops with one client-side shared options payload.
- Reduced `/wialon/mapping` response to about 0.95 MB.
- Reduced `/wialon/mapping` SQL from 20 SELECT to 3 SELECT.
- Reduced repeated SQL count to 0.
- No DB schema changes.
- No migrations.
- Staging and production validation passed.
- Production rollout completed with source-only pull.
- Only TransportReport was restarted.
- Telegram bot services were not restarted.

## 2026-06-16 - PERF-REF-BODY-001 Reference equipment response size optimized

Status: completed and deployed to production.

Code commit:

89e68c49a8620b4b33202af344fda99a614c2908

Summary:

- Ran read-only source, SQL, and response diagnostic for heavy reference pages.
- Confirmed `/ref/equipment` was the largest remaining reference page.
- Baseline `/ref/equipment`:
  - 2,496,903 UTF-8 bytes.
  - 8,783 `<option>` elements.
  - 676 `<select>` elements.
  - 675 forms.
  - 8 SELECT.
  - repeated SQL count 0.
- Changed only `templates/ref_equipment.html`.
- Replaced repeated edit-row organization/category option loops with shared client-side options.
- Added shared `REF_EQUIPMENT_ORG_OPTIONS` and `REF_EQUIPMENT_CATEGORY_OPTIONS`.
- Preserved delete/deactivate/enable forms.
- Preserved existing Flask route and SQL logic.
- No DB schema changes.
- No migrations.
- Reduced `/ref/equipment` response to about 1.5 MB.
- Reduced `<option>` count to 719.
- Production rollout completed with source-only pull.
- Only TransportReport was restarted.
- Telegram bot services were not restarted.

## 2026-06-16 - PERF-REF-BODY-002 Reference equipment inline edit rendering optimized

Status: completed and deployed to production.

Code commit:

f3be56072961f8f26ec9842a7d9b4d62ab04523c

Summary:

- Diagnosed `/ref/equipment` remaining body-size issue after PERF-REF-BODY-001.
- Confirmed SQL was already optimized:
  - 8 SELECT.
  - repeated SQL count 0.
- Confirmed remaining issue was repeated hidden inline edit rows:
  - 336 old inline edit rows.
  - 675 forms.
  - 2,762 inputs.
  - 676 selects.
  - response body about 1.50 MB.
- Changed only `templates/ref_equipment.html`.
- Replaced per-row hidden edit forms with one reusable shared edit row.
- Added row-level `data-*` attributes.
- Preserved existing `/ref/equipment/save` POST contract.
- Preserved delete/deactivate/enable forms.
- Preserved shared organization/category option payloads from PERF-REF-BODY-001.
- Reduced `/ref/equipment` response to about 0.68 MB.
- Reduced old inline edit rows to 0.
- Reduced selects to 6.
- Reduced inputs to 417.
- Reduced forms to 340.
- No DB schema changes.
- No migrations.
- Production rollout completed with source-only pull.
- Only TransportReport was restarted.
- Telegram bot services were not restarted.

## 2026-06-16 - PERF-WIALON-WORKLOAD-001 Wialon workload SQL optimized

Status: completed and deployed to production.

Code commit:

9c16198af3ccf75fdc1ec4cb0ee50cff19cd1b9d

Summary:

- Diagnosed Wialon routes after `/wialon/auto_match` audit.
- Confirmed `/wialon/auto_match` was not the problem:
  - 3 SELECT.
  - repeated SQL count 0.
  - response body about 31 KB.
- Found real SQL issue in workload routes:
  - `/wialon/workload`: 21 SELECT, repeated equipment query 17 times.
  - `/wialon/workload/export`: 20 SELECT, repeated equipment query 17 times.
- Changed `workload_report.py` and `wialon_import.py`.
- Added `preloaded_orgs` support to `get_workload_data`.
- Replaced per-organization equipment queries with one bulk equipment query.
- Reused preloaded organizations in `/wialon/workload`.
- Kept workload export compatible.
- No DB schema changes.
- No migrations.
- No template changes.
- Reduced workload SQL to 4 SELECT.
- Eliminated repeated equipment SQL.
- Production rollout completed with source-only pull.
- Only TransportReport was restarted.
- Telegram bot services were not restarted.

## 2026-06-16 - PERF-WIALON-MAPPING-BODY-002 Wialon mapping shared forms

Status: completed and deployed to production.

Code commit:

1aa471977684b1fac950fdf758db57544082bd83

Summary:

- Diagnosed `/wialon/mapping` response body after SQL was already optimized.
- Confirmed SQL was not the problem:
  - 3 SELECT.
  - repeated SQL count 0.
  - DML count 0.
- Found HTML body issue:
  - 947,349 bytes.
  - 763 forms.
  - 1,909 inputs.
  - 384 selects.
  - 379 repeated edit save forms.
- Changed only `templates/wialon_mapping_list.html`.
- Replaced repeated per-row edit/delete forms with shared reusable forms.
- Preserved pending forms and manual add form.
- Preserved existing backend POST endpoints.
- Removed heavy rendered `data-search`.
- Removed repeated rendered `data-delete-url`.
- Added shared delete URL template.
- Changed search to use row text cache.
- Reduced `/wialon/mapping` body to 633,834 bytes.
- Reduced forms to 7, inputs to 18, selects to 6.
- Production rollout completed with template-only pull.
- Only TransportReport was restarted.
- Telegram bot services were not restarted.

## 2026-06-16 - PERF-WORK-TYPES-BODY-001 Reference work types shared forms

Status: completed and deployed to production.

Code commit:

42452831d2643908a229ef1cb5514f8701dab469

Summary:

- Diagnosed `/ref/work_types` response body.
- Confirmed SQL was not the problem:
  - 2 SELECT.
  - repeated SQL count 0.
  - DML count 0.
- Found HTML body issue:
  - 266,902 bytes.
  - 111 forms.
  - 530 inputs.
  - 110 CSRF inputs.
  - repeated hidden edit rows.
  - repeated delete forms.
- Changed only `templates/ref_work_types.html`.
- Replaced repeated inline edit rows with one shared edit row.
- Replaced repeated delete forms with one shared delete form.
- Preserved filter form and add-new form.
- Preserved backend POST endpoints.
- Reduced `/ref/work_types` body to about 127 KB.
- Reduced forms to 5, inputs to 12, CSRF inputs to 4.
- Production rollout completed with template-only pull.
- Only TransportReport was restarted.
- Telegram bot services were not restarted.

## 2026-06-16 - PERF-FUEL-STATIONS-NPLUS1-001 Fuel stations transaction counts optimization

Status: completed and deployed to production.

Code commit:

a7f295a9ece74f1821a52b755ae5daa024ecfd65

Summary:

- Diagnosed fuel dashboard and fuel reference routes.
- Confirmed `/fuel/` itself did not have repeated SQL:
  - 11 SELECT.
  - repeated SQL count 0.
- Found stronger optimization candidate in `/fuel/stations`:
  - 44 SELECT.
  - repeated SQL count 2.
  - repeated transaction count queries 21 + 21.
- Changed only:
  - fuel_routes.py.
  - templates/fuel/stations.html.
- Replaced per-station transaction count queries with one grouped count query.
- Reused preloaded transaction counts in `station_delete_info`.
- Replaced template `st.transactions.count()` with preloaded count.
- Reduced `/fuel/stations` from 44 SELECT to 3 SELECT.
- Reduced repeated SQL from 2 to 0.
- Production rollout completed.
- Only TransportReport was restarted.
- Telegram bot services were not restarted.

<!-- fuel-batch-perf-001d -->

## 2026-06-16 - fuel-batch-perf-001d docs closure

current stable code commit:

- `c4fd7d16b981bc1406aa65a6a9d48d23027bb6c0`

completed task:

- fuel-batch-perf-001 is closed.
- `/fuel/warehouses` optimized from 73 select and repeated sql 6 to 6 select and repeated sql 0.
- `/fuel/initial-balance` optimized from 11 select and repeated sql 1 to 2 select and repeated sql 0.
- staging validation passed.
- production validation passed.
- production post-restart smoke passed.
- only `transportreport` was restarted for the code rollout.
- `transportbot` and `transportbot003` were not restarted.
- validation used get requests only.
- no post requests were used during validation.
- no db writes were made during get validation.

next candidates after closure:

1. `/fuel/transactions`
   - staging showed 3 select, repeated 0.
   - production showed 12 select with station lazy-load repeated 9.
   - likely data-dependent n+1 by station relationship.
2. `/fuel/report`
   - 19 select with repeated warehouse query count 2.
   - lower priority.
3. `/fuel/warnings`
   - 18 select, repeated 0.
   - evaluate query volume separately.
4. ui/ux redesign
   - planned after technical debt is reduced.
<!-- perf-fuel-transactions-nplus1-001d -->

## 2026-06-16  PERF-FUEL-TRANSACTIONS-NPLUS1-001 completed

Closed performance issue on `/fuel/transactions`.

Root cause:
- The route joined `FuelStation2`, but did not eager-load `FuelTransaction2.station`.
- The template accessed `txn.station.name` and `txn.station.warehouse_name`, causing data-dependent lazy-load SELECTs.

Code change:
- Added:
  - `joinedload(FuelTransaction2.station).joinedload(FuelStation2.warehouse)`
- File changed:
  - `fuel_routes.py`
- Commit:
  - `7f928c0 optimize fuel transactions station loading`

Measured result:
- Before: production had data-dependent N+1 on station lazy load.
- After staging: status 200, SQL total 6, repeated SQL kinds 0, station lazy repeated total 0, warehouse lazy repeated total 0, non-select statements 0.
- After production: status 200, SQL total 6, repeated SQL kinds 0, station lazy repeated total 0, warehouse lazy repeated total 0, non-select statements 0.

Rollout notes:
- Production backup created before pull.
- Production updated with fast-forward pull.
- Restarted only `transportreport`.
- `transportbot` and `transportbot003` were queried only and not restarted.
<!-- perf-fuel-report-warehouse-query-001d -->

## 2026-06-16  PERF-FUEL-REPORT-WAREHOUSE-QUERY-001 completed

Closed duplicate warehouse query on `/fuel/report`.

Root cause:
- `_collect_fuel_report_data()` already loaded the ordered warehouse list.
- `fuel_report()` then loaded the same ordered warehouse list again before rendering the template.

Code change:
- Added `warehouses` to the report collector result.
- In `fuel_report()`, created `data_for_template = dict(data)`.
- Reused `data_for_template.pop('warehouses', None)` for the template `warehouses` argument.
- Passed `**data_for_template` to avoid duplicate `warehouses` keyword in `render_template`.
- File changed:
  - `fuel_routes.py`
- Commit:
  - `6e6237b optimize fuel report warehouse loading`

Measured result:
- Before: status 200, SQL total 22, repeated SQL kinds 1, warehouse ordered queries 2.
- After staging: status 200, SQL total 21, repeated SQL kinds 0, warehouse ordered queries 1, non-select statements 0.
- After production: status 200, SQL total 21, repeated SQL kinds 0, warehouse ordered queries 1, non-select statements 0.

Rollout notes:
- First staging patch exposed a duplicate `warehouses` keyword error in `render_template`; fixed before commit.
- Production backup created before pull.
- Production updated with fast-forward pull.
- Restarted only `transportreport`.
- `transportbot` and `transportbot003` were queried only and not restarted.
<!-- perf-fuel-get-routes-sweep-001d -->

## 2026-06-16  PERF-FUEL-GET-ROUTES-SWEEP-001 completed

Completed a read-only N+1/performance sweep for all fuel GET routes.

Route inventory:
- Total fuel GET routes: 9
- Routes:
  - `/fuel/`
  - `/fuel/api/fuel_ping`
  - `/fuel/initial-balance`
  - `/fuel/receipts`
  - `/fuel/report`
  - `/fuel/stations`
  - `/fuel/transactions`
  - `/fuel/warehouses`
  - `/fuel/warnings`

Measured clean routes in this sweep:
- `/fuel/warnings`
  - staging: status 200, SQL total 21, repeated SQL kinds 0, lazy-load repeated totals 0, non-select statements 0
  - production: status 200, SQL total 21, repeated SQL kinds 0, lazy-load repeated totals 0, non-select statements 0
- `/fuel/`
  - staging: status 200, SQL total 14, repeated SQL kinds 0, lazy-load repeated totals 0, non-select statements 0
  - production: status 200, SQL total 14, repeated SQL kinds 0, lazy-load repeated totals 0, non-select statements 0
- `/fuel/receipts`
  - staging: status 200, SQL total 5, repeated SQL kinds 0, lazy-load repeated totals 0, non-select statements 0
  - production: status 200, SQL total 5, repeated SQL kinds 0, lazy-load repeated totals 0, non-select statements 0

Previously optimized and/or validated fuel routes:
- `/fuel/transactions`
- `/fuel/report`
- `/fuel/warehouses`
- `/fuel/initial-balance`
- `/fuel/stations`

Operational notes:
- Diagnostics were read-only.
- No POST was executed.
- No commit/pull/restart was performed during diagnostics.
- All relevant services remained RUNNING.
- No code changes required for this sweep.
<!-- perf-index-fuel-sync-dup-001d -->

## 2026-06-16  PERF-INDEX-FUEL-SYNC-DUP-001 deployed

Completed and deployed the main dashboard FuelSyncLog duplicate query optimization.

What changed:
- `fuel_routes.py`
  - `_collect_fuel_report_data()` now returns `latest_sync`.
- `app.py`
  - `_build_dashboard_context()` reuses `fuel_report['latest_sync']` instead of issuing a duplicate direct query.
  - Fallback direct query is kept for collector failure cases.

Code commit:
- `f00b386 optimize index fuel sync loading`

Validation:
- Staging:
  - `/` status 200
  - SQL total 30
  - repeated SQL kinds 0
  - `fuel_sync_logs2` query count 2
  - non-select statements 0
- Production:
  - `/` status 200
  - SQL total 31
  - repeated SQL kinds 0
  - `fuel_sync_logs2` query count 2
  - non-select statements 0

Deployment state:
- staging = `f00b386`
- production = `f00b386`
- origin/main = `f00b386`

Backup:
- `d:\transport-report-backups\production\source\index_fuel_sync_dup_001_git_archive_before_20260616_200045_98ca314.zip`

Services:
- Restarted:
  - `transportreportstaging`
  - `transportreport`
- Not restarted:
  - `transportbot`
  - `transportbot003`
  - `transportbotstaging`
  - `transportbot003staging`

Final result:
- Main route `/` no longer has repeated SQL caused by duplicate latest Topaz sync lookup.
<!-- perf-core-get-routes-sweep-001d -->

## 2026-06-16  PERF-CORE-GET-ROUTES-SWEEP-001 completed

Completed read-only SQL/N+1 diagnostics for core GET routes.

Routes checked:
- `/`
- `/entry`
- `/deficiencies`
- `/report`

Verification:
- `/entry`
  - staging: status 200, SQL total 5, repeated SQL kinds 0, non-select statements 0
  - production: status 200, SQL total 5, repeated SQL kinds 0, non-select statements 0
- `/deficiencies`
  - staging: status 200, SQL total 5, repeated SQL kinds 0, non-select statements 0
  - production: status 200, SQL total 5, repeated SQL kinds 0, non-select statements 0
- `/report`
  - staging: status 200, SQL total 5, repeated SQL kinds 0, non-select statements 0
  - production: status 200, SQL total 5, repeated SQL kinds 0, non-select statements 0
- `/`
  - before optimization: repeated SQL kinds 1, duplicate latest `FuelSyncLog2` query
  - after optimization:
    - staging: status 200, SQL total 30, repeated SQL kinds 0, `fuel_sync_logs2` query count 2, non-select statements 0
    - production: status 200, SQL total 31, repeated SQL kinds 0, `fuel_sync_logs2` query count 2, non-select statements 0

Related task:
- `PERF-INDEX-FUEL-SYNC-DUP-001`

Final result:
- Core transport GET routes are clean.
<!-- perf-wialon-get-routes-sweep-001d -->

## 2026-06-16  PERF-WIALON-GET-ROUTES-SWEEP-001 completed

Completed read-only SQL/N+1 diagnostics for remaining Wialon GET routes.

Routes checked:
- `/wialon`
- `/wialon/auto_match`
- `/wialon/report`

Staging:
- `/wialon`: status 200, SQL total 6, repeated SQL kinds 0, non-select statements 0
- `/wialon/auto_match`: status 200, SQL total 5, repeated SQL kinds 0, non-select statements 0
- `/wialon/report`: status 200, SQL total 5, repeated SQL kinds 0, non-select statements 0

Production:
- `/wialon`: status 200, SQL total 6, repeated SQL kinds 0, non-select statements 0
- `/wialon/auto_match`: status 200, SQL total 5, repeated SQL kinds 0, non-select statements 0
- `/wialon/report`: status 200, SQL total 5, repeated SQL kinds 0, non-select statements 0

Final result:
- Remaining Wialon GET routes are clean.
- No code changes were needed.
- No service restart was required.
<!-- perf-spare-parts-get-routes-sweep-001d -->

## 2026-06-16  PERF-SPARE-PARTS-GET-ROUTES-SWEEP-001 completed

Completed read-only SQL/N+1 diagnostics for remaining spare parts GET routes.

Routes checked:
- `/spare-parts/catalog`
- `/spare-parts/new`
- `/spare-parts/<id>`

Staging:
- `/spare-parts/catalog`: status 200, SQL total 4, repeated SQL kinds 0, non-select statements 0
- `/spare-parts/new`: status 200, SQL total 4, repeated SQL kinds 0, non-select statements 0
- `/spare-parts/12`: status 200, SQL total 7, repeated SQL kinds 0, non-select statements 0

Production:
- `/spare-parts/catalog`: status 200, SQL total 4, repeated SQL kinds 0, non-select statements 0
- `/spare-parts/new`: status 200, SQL total 4, repeated SQL kinds 0, non-select statements 0
- `/spare-parts/3`: status 200, SQL total 6, repeated SQL kinds 0, non-select statements 0

Final result:
- Remaining spare parts GET routes are clean.
- No code changes were needed.
- No service restart was required.
<!-- perf-admin-users-orgs-nplus1-001d -->

## 2026-06-16  PERF-ADMIN-USERS-ORGS-NPLUS1-001 deployed

Completed and deployed the `/admin/users` organizations N+1 optimization.

What changed:
- `app.py`
  - imported `selectinload`
  - changed `/admin/users` query to use `selectinload(User.organizations)`

Code commit:
- `2216514 optimize admin users organization loading`

Before:
- `/admin/users` had repeated organization lazy-load queries:
  - SQL total 12
  - repeated SQL kinds 1
  - organization repeated total 7

After:
- `/admin/users`:
  - status 200
  - SQL total 6
  - repeated SQL kinds 0
  - organization repeated total 0
  - `user_organizations` query count 1
  - non-select statements 0

Regression:
- `/admin/permissions`: clean
- `/admin/audit`: clean

Deployment state:
- staging = `2216514`
- production = `2216514`
- origin/main = `2216514`

Services:
- Restarted:
  - `transportreportstaging`
  - `transportreport`
- Not restarted:
  - `transportbot`
  - `transportbot003`
  - `transportbotstaging`
  - `transportbot003staging`

Final result:
- `/admin/users` no longer has organization N+1 queries.
<!-- final-global-get-routes-control-001d -->

## 2026-06-16  Final global GET route control completed

Completed final read-only control after the current performance/security cleanup wave.

Repository state:
- staging = `9f31685`
- production = `9f31685`
- origin/main = `9f31685`

Final verbose diagnostics:
- staging exit code: 0
- production exit code: 0
- staging FINAL BAD ROUTES COUNT: 0
- production FINAL BAD ROUTES COUNT: 0

Validation result:
- All checked GET routes returned status 200 under authenticated admin test client.
- All checked routes had `repeated sql kinds = 0`.
- All checked routes had `non select statements = 0`.
- Staging and production are synchronized on the same commit.

Operational state:
- `transportreport`: RUNNING
- `transportreportstaging`: RUNNING
- `transportbot`: RUNNING
- `transportbot003`: RUNNING
- `transportbotstaging`: RUNNING
- `transportbot003staging`: RUNNING

No changes were made during the final read-only control:
- no commit
- no pull
- no POST
- no service restart

Current wave status:
- Fuel GET routes sweep: closed.
- Core GET routes sweep: closed.
- Wialon GET routes sweep: closed.
- Spare parts GET routes sweep: closed.
- `/admin/users` organization N+1: fixed, deployed, documented.
- `/admin/permissions`: verified clean.
- `/admin/audit`: verified clean.
- Final global GET route control: passed.

## 2026-06-17 - CSRF-AUDIT-001 completed

Status: completed as read-only security audit.

Baseline:

- 255a904 document final global get route control

Summary:

- Completed CSRF coverage audit after final global GET route control.
- Confirmed global CSRF helper and before_request enforcement in app.py.
- Confirmed all 52 discovered browser POST forms include CSRF markers.
- Confirmed 46 Python POST routes were inventoried.
- Confirmed potential risk item count was 0.
- Confirmed CSRF-exempt API paths are limited to fuel sync endpoints and /api/bot/*.
- Confirmed fuel sync API uses FUEL_API_TOKEN with hmac.compare_digest.
- Confirmed bot API uses one-time link-code verification and Bearer token sessions.
- No application code changes were required.
- No database changes, migrations, production pull, or service restart were performed.

Current status:

- CSRF-AUDIT-001 is closed.
- Next recommended choices: API-FUEL-LEGACY-002, TASK-REPORT-001, TASK-DEPLOY-005G.

## API-FUEL-LEGACY-002 / API-FUEL-LEGACY-006B — Legacy fuel sync alias removal

Date: 2026-06-19

Staging-first implementation after endpoint verification.

Evidence:
- Real Topaz sync source IP: 10.103.40.140.
- Production sync rows are fresh and successful in uel_sync_logs2.
- No production warning found for Topaz agent used deprecated endpoint /api/fuel_sync.
- Staging probe confirmed the warning is written when the legacy endpoint is called.
- Therefore the real Topaz agent is considered migrated to /fuel/api/fuel_sync.

Staging change:
- removed temporary POST /api/fuel_sync alias from pp.py;
- kept canonical POST /fuel/api/fuel_sync;
- removed /api/fuel_sync from CSRF exemption list;
- kept FUEL_API_TOKEN protection unchanged.

Production remains unchanged until staging validation is complete.

## API-FUEL-LEGACY-002 / API-FUEL-LEGACY-009 — Final completion

Date: 2026-06-20

Final status: completed.

What was done:
- Removed temporary legacy `POST /api/fuel_sync` alias.
- Kept canonical `POST /fuel/api/fuel_sync`.
- Removed `/api/fuel_sync` from CSRF exemption list.
- Kept `FUEL_API_TOKEN` validation unchanged.
- Rolled out to staging first, then production.
- Verified that both staging and production are on commit `9dd034e`.

Validation:
- `GET /api/fuel_sync` returns 404.
- `GET /fuel/api/fuel_sync` returns 405.
- `POST /fuel/api/fuel_sync` with invalid token returns 401.
- `/api/bot/health` returns 200.
- Production Topaz sync after rollout exists:
  - sync id: 2525
  - synced_at: 2026-06-20 03:33:02.704120
  - agent_ip: 10.103.40.140
  - received: 4
  - new: 4
  - status: ok
  - error: empty

Conclusion:
- The real Topaz agent is working through the canonical `/fuel/api/fuel_sync` endpoint.
- The legacy `/api/fuel_sync` alias has been removed safely.
- API-FUEL-LEGACY-002 is closed.

---

## Runtime state update - OPS-PY-CRASH-001 closed

Date: 2026-06-20

Vehicle Soft services now run with Python user-site isolation enabled via python -s.

Current service command lines:

- Production Flask: python.exe -s run_server.py
- Production BOT002: python.exe -s bot.py
- Production BOT003 worker: python.exe -s bot003_outbox_worker.py --interval 30 --batch-size 20
- Staging Flask: python.exe -s run_server.py
- Staging BOT002: python.exe -s bot.py
- Staging BOT003 worker: python.exe -s bot003_outbox_worker.py --interval 30 --batch-size 20

Validation:

- OPS-PY-CRASH-013 PASS
- all six services RUNNING
- production/staging HTTP checks passed
- no post-10:09 Vehicle Soft crash/restart evidence
- no DB changes
- no code changes
- no token rotation

Config backups:

- Staging: D:\transport-report-backups\staging\service_config\ops_py_crash_009_nssm_before_20260620_100240.txt
- Production: D:\transport-report-backups\production\service_config\ops_py_crash_012_nssm_before_20260620_100709.txt

## 2026-06-22 — FUEL-REPORT-011 completed

Fuel balance report was completed and deployed to production.
Final application commit: c5f898b add manual fuel expenses to balance report

Completed:
- fuel balance report page
- Excel export
- dashboard link
- manual expense support
- May manual expense for Варахшо чул: 2614.00 l
- June 01-18 Topaz CSV backfill for 934451, 895101, 935491
- production QA for May 2026 and June 01-18, 2026

Validated production totals:
- May 2026: opening 67472.00, receipts 257233.00, expenses 206418.22, closing 118286.78
- June 01-18: opening 118286.78, receipts 107426.00, expenses 166236.06, closing 59476.72

Operational rule:
- Topaz + 1C are the source of truth for fuel expense reconciliation.
- Manual Excel values are not accepted if they conflict with Topaz/1C.

Release document: docs/RELEASE_FUEL_REPORT_011_BALANCE_REPORT_20260622.md
