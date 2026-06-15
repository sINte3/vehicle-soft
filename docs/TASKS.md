# TASKS.md — Vehicle Soft Backlog

## Priority legend

- P0: production stability/security.
- P1: required for current business workflow.
- P2: important improvement.
- P3: future ERP expansion.

## In progress / next

(none — see backlog for upcoming work)

## Recently completed / appears completed

### REPORT001B - Excel export improvements

Priority: P1
Status: **completed 2026-06-04**

Changes made:

- Fixed working-row calculation in the main report preview.
- Fixed top work types and totals on /report.
- Improved existing Excel exports without changing sheet structure.
- Main report export is now language-aware: RU interface -> Russian Excel, UZ interface -> Uzbek Excel.
- Daily activity export is now language-aware: RU interface -> Russian Excel, UZ interface -> Uzbek Excel.
- Translated Russian headers in Детально sheets.
- Translated agricultural machinery categories in Russian daily activity export.
- Improved workbook readability and print layout while preserving existing sheet order and report structure.
- No database migration required.


### REPORT001A - Main report UX improvements

Priority: P1
Status: **completed 2026-06-04**

Changes made:

- Improved `/report` page with analysis-oriented layout.
- Added summary metrics: total amount, total quantity, rows, equipment count, working rows, idle/no-work rows.
- Added payment-type summary.
- Added organization summary.
- Added top work types summary.
- Added detail preview with client-side search.
- Improved period, organization, and category filters.
- Preserved Excel export behaviour.
- Verified `/report` test client returned STATUS=200.
- Verified production smoke test.
- No database migration required.
- Production backup completed before deployment: D:\transport-report-backups\production\daily\transport_20260604_131327.db


### UX001D - Spare parts module UX

Priority: P1
Status: **completed 2026-06-04**

Changes made:

- Improved spare parts request list UX with counters, search, and status filter.
- Improved spare part request form UX with sticky actions, item counter, empty-row cleanup, and client-side validation.
- Improved request detail page with summary cards and clearer submit/approve/reject actions.
- Improved spare parts catalog UX with search and required-name validation.
- Verified production smoke test for list, form, detail, catalog, Russian UI, and Uzbek UI.
- No database migration required.
- Production backup completed before deployment: D:\transport-report-backups\production\daily\transport_20260604_124931.db


### UX001C - Fuel module UX

Priority: P1
Status: **completed 2026-06-04**

Changes made:

- Improved Fuel dashboard, Topaz transactions, receipts, initial balances, warehouses, and stations screens.
- Removed price/sum visual logic from Fuel operator UI.
- Added clearer DT-only and liters-focused operator guidance.
- Added search/filter UX for receipts, initial balances, warehouses, and stations.
- Added clearer station active/disabled and warehouse used/delete states.
- Verified staging fuel data freshness issue was caused by stale staging DB; staging DB was refreshed from production backup.
- Verified production smoke test for dashboard, transactions, receipts, initial balances, warehouses, stations, and RU/UZ UI.
- No database migration required.
- Production backup completed before deployment: D:\transport-report-backups\production\daily\transport_20260604_120114.db


### UX001B - Wialon mapping UX

Priority: P1
Status: **completed 2026-06-04**

Changes made:

- Improved Wialon mapping list with counters, search, and status filters.
- Added clearer mapping statuses: linked, not in system, pending decision.
- Added separate pending Wialon objects area.
- Improved manual mapping actions and not-in-system workflow.
- Improved Wialon auto-match toolbar with search, filter, expand/collapse, and visible-row skip action.
- Added client-side duplicate equipment selection validation for auto-match bulk save.
- Added RU/UZ translations for new Wialon mapping and auto-match UI elements.
- Verified production smoke test for mapping list and auto-match workflows.
- No database migration required.
- Production backup completed before deployment: D:\transport-report-backups\production\daily\transport_20260604_112928.db


### UX001A - Daily entry UX improvements

Priority: P1
Status: **completed 2026-06-04**

Changes made:

- Improved daily entry form header and operator workflow.
- Added toolbar actions: save, mark all idle, expand all, collapse all, search, and counters.
- Improved equipment cards with model, plate number, type, unit, and per-equipment total.
- Added client-side validation and invalid-field highlighting before submit.
- Added RU/UZ translations for new daily entry UI elements.
- Fixed Uzbek text appearing in Russian interface for new UX elements.
- Verified production smoke test for valid save, search/filter, expand/collapse, and invalid input blocking.
- No database migration required.
- Production backup completed before deployment: D:\transport-report-backups\production\daily\transport_20260604_111225.db


### QA001 + BACKUP001 - QA checklist and backup restore test

Priority: P0
Status: **completed 2026-06-04**

Changes made:

- Created formal release QA checklist: docs/QA_CHECKLIST.md.
- Executed isolated production backup restore test in C:\transport-report-restore-test.
- Restored backup: D:\transport-report-backups\production\daily\transport_20260604_104248.db.
- Verified restored SQLite database integrity_check: ok.
- Verified restored database table count: 32.
- Verified application import on restored code/database set: RESTORE APP IMPORT OK.
- Documented restore test: docs/BACKUP_RESTORE_TEST_20260604.md.
- No application code changes.
- No database migration required.


### DATA001-3 - Validation UX improvements

Priority: P1
Status: **completed 2026-06-04**

Changes made:

- Added multi-error flash message rendering.
- Improved daily entry validation messages with equipment context.
- Improved Fuel validation messages for initial balance and receipt forms.
- Improved spare parts validation messages with row-level item details.
- Improved Wialon mapping and auto-match validation messages.
- Updated base template to display validation errors as readable lists.
- Verified production smoke test for valid and invalid scenarios.
- No database migration required.
- Production backup completed before deployment: D:\transport-report-backups\production\daily\transport_20260604_104248.db


### DATA001-2 - References and Wialon validation

Priority: P0
Status: **completed 2026-06-04**

Changes made:

- Added duplicate and required-field validation for organizations, equipment, work types, and customers.
- Added equipment plate normalization and duplicate protection.
- Added Wialon mapping validation: non-empty Wialon name, active equipment only, no duplicate links.
- Added Wialon auto-match validation to prevent duplicate Wialon names and duplicate equipment selections.
- Updated reference and Wialon forms with required/min constraints where applicable.
- Verified production smoke test for valid and invalid reference/Wialon scenarios.
- No database migration required.
- Production backup completed before deployment: D:\transport-report-backups\production\daily\transport_20260604_102724.db


### DATA001-1 - Input validation phase 1

Priority: P0
Status: **completed 2026-06-04**

Changes made:

- Added backend validation for daily entry save.
- Added backend validation for Fuel initial balances and receipts.
- Updated Fuel business rules: fuel type fixed as DT, prices removed, negative initial balances allowed.
- Added backend validation for spare parts requests and items.
- Added basic backend validation for organizations, equipment, work types, and customers.
- Verified production smoke test for valid and invalid scenarios.
- No database migration required.
- Production backup completed before deployment: D:\transport-report-backups\production\daily\transport_20260604_100121.db


### TASK-SEC-003F - Roles and access control hardening

Priority: P0
Status: **completed 2026-06-03**

Changes made:

- Added transport module permission checks to core transport routes.
- Protected daily entry, report, and transport reference routes with module_required(transport).
- Hardened spare parts organization access for non-admin users.
- Restricted spare parts list, creation, detail view, submit, approve/reject access by organization and role.
- Made spare parts approve/reject actions admin-only.
- Updated spare parts detail UI so approve/reject controls are shown only to admins.
- Verified admin access on production.
- Verified operator module restrictions on production.
- Verified zero-module test user receives expected 403 responses.
- No database migration required.
- Production backup completed before deployment: D:\transport-report-backups\production\daily\transport_20260603_120118.db, integrity_check ok.


### TASK-SEC-003E - Dangerous delete protection

Priority: P0
Status: **completed 2026-06-03**

Changes made:

- Blocked deletion of organizations with linked records.
- Protected equipment deletion: linked active equipment is deactivated instead of physically deleted.
- Added equipment reactivation from the equipment reference UI.
- Blocked deletion of used work types and customers.
- Blocked deletion of fuel warehouses with linked stations, receipts, or initial balances.
- Protected fuel station deletion: stations with Topaz transactions are deactivated instead of physically deleted.
- Added fuel station reactivation from the fuel station UI.
- Added audit logging for blocked delete/deactivation/reactivation actions.
- Updated UI so linked records show Used/Deactivate/Enable instead of misleading delete buttons.
- No database migration required.
- Production backup completed before deployment: D:\transport-report-backups\production\daily\transport_20260603_111926.db, integrity_check ok.


### TASK-SEC-003D - CSRF protection

Priority: P0
Status: **completed 2026-06-03**

Changes made:

- Added server-side CSRF token generation.
- Added server-side CSRF validation for browser POST forms.
- Added csrf_token hidden fields to browser forms.
- Excluded Topaz token-auth API endpoints from CSRF: /fuel/api/fuel_sync and /api/fuel_sync.
- Verified Topaz ping on production after deployment.
- Verified production smoke test: login/logout, daily report, references, Wialon mapping, Fuel warehouse, spare parts request, and admin audit.
- No database migration required.
- Production backup completed before deployment: D:\transport-report-backups\production\daily\transport_20260603_095235.db, integrity_check ok.


### TASK-SEC-003C-3 - Spare parts audit log

Priority: P0
Status: **completed 2026-06-03**

Changes made:

- Added audit logging for spare parts request creation.
- Added audit logging for spare parts request item creation.
- Added audit logging for spare parts status changes.
- Added audit logging for spare parts catalog create/update.
- Improved spare parts equipment selector: model, plate number, and organization are shown.
- Improved Russian UI labels in spare parts pages.
- Verified /admin/audit on production for spare parts actions.
- No database migration required.
- Production backup completed before deployment: D:\transport-report-backups\production\daily\transport_20260603_084842.db, integrity_check ok.


### TASK-SEC-003C-2 - Fuel audit log

Priority: P0
Status: **completed 2026-06-03**

Changes made:

- Added audit logging for Fuel warehouses create/update/delete.
- Added audit logging for Fuel stations create/update/delete.
- Added audit logging for Fuel initial balance save.
- Added audit logging for Fuel receipts create/update/delete.
- Added audit logging for Topaz sync completed/failed events.
- Improved warehouse edit UX: edit form opens inline inside the selected warehouse card.
- Verified /admin/audit on production for Fuel actions.
- No database migration required.
- Production backup completed before deployment: D:\transport-report-backups\production\daily\transport_20260603_081631.db, integrity_check ok.


### TASK-SEC-003C-1 - Wialon audit log

Priority: P0
Status: **completed 2026-06-03**

Changes made:

- Added audit logging for Wialon import upload.
- Added audit logging for Wialon auto-match bulk save.
- Added audit logging for Wialon mapping create/update/delete.
- Added audit logging for Wialon engine-hours export.
- Added audit logging for Wialon workload export.
- Verified /admin/audit on production: wialon_mapping_updated appears after saving a mapping.
- No database migration required.
- Production backup completed before final verification: D:\\transport-report-backups\\production\\daily\\transport_20260602_221500.db, integrity_check ok.


### TASK-FUEL-001 — Standardize Topaz API path

Priority: P1  
Status: **completed 2026-05-22**

Changes made:

- `fuel_routes.py`: sync body extracted into `_perform_fuel_sync()` helper function.
  Canonical `api_fuel_sync()` route now delegates to this helper (no logic duplication).
- `app.py`: legacy route `POST /api/fuel_sync` added at app level (no blueprint prefix).
  It logs a `WARNING` naming the deprecated path and recommends switching to
  `/fuel/api/fuel_sync`, then delegates to `_perform_fuel_sync()`.
  Token validation and sync logic are identical to the canonical path.
- `docs/DECISIONS.md`: ADR-011 added.
- `docs/AGENT_STATE.md`: updated.

Acceptance criteria:

- `POST /fuel/api/fuel_sync` continues to work (canonical, preferred).
- `POST /api/fuel_sync` works with same token and same logic (legacy alias).
- Neither endpoint bypasses `FUEL_API_TOKEN` validation.
- Legacy use produces a `WARNING` log entry. Token value and body are not logged.
- `py_compile` passes on `app.py`, `fuel_routes.py`, `run_server.py`.
- `from app import app` import check passes.

Operator action:

- Update Topaz agent configuration to POST to `/fuel/api/fuel_sync`.
  The `/api/fuel_sync` alias can be removed from `app.py` once all agent
  configs are confirmed updated.

### TASK-SEC-002 — Move secrets to environment/config

Priority: P0  
Status: **completed 2026-05-22**

Changes made:

- `config.py`: `Config.SECRET_KEY` no longer has a fallback. `DevelopmentConfig`
  overrides with a clearly-named dev-only fallback. `Config.FUEL_API_TOKEN` added
  reading from `FUEL_API_TOKEN` environment variable.
- `run_server.py`: early exit with clear ASCII error if `SECRET_KEY` is not set.
- `fuel_routes.py` removed the old hardcoded API_TOKEN; token is now read from
  `current_app.config['FUEL_API_TOKEN']`. Deny-all if not configured.
- `docs/DEPLOYMENT_SECURITY.md` created with `setx` commands, verification steps,
  NSSM restart instructions, and rollback guide.

Acceptance verified:

- `py_compile` passes on `config.py`, `fuel_routes.py`, `run_server.py`.
- `from app import app` import check passes.
- `/fuel/api/fuel_sync` still token-protected, still excluded from session module guard.
- Dev mode unaffected (DevelopmentConfig fallback active when FLASK_ENV=dev).

**Operator action required before deploying:**
Set `SECRET_KEY` and `FUEL_API_TOKEN` via `setx` before restarting the service.
See `docs/DEPLOYMENT_SECURITY.md`.

### TASK-SEC-001 — Enforce module permissions

Priority: P0  
Status: **completed 2026-05-22**

Changes made:

- `User.has_module_access(module_code)` added to `models.py`.
- `module_required(module_code)` decorator factory added to `models.py`.
- Decorators applied: 11 wialon routes, 13 fuel UI routes, 9 spare parts routes, 3 deficiency routes.
- Navigation visibility controlled in `templates/base.html` per module access.
- `migrate_module_permissions.py` executed successfully on production.
- Import of `generate_daily_activity` corrected in `app.py`.

Acceptance verified:

- Direct access to a disabled module URL returns 403.
- Admin can access all modules.
- Existing UI permission page still works.
- `/fuel/api/fuel_sync` remains token-only.
- Site starts and works correctly.

### TASK-CAT-001 — Expand equipment categories to 9

Priority: P1  
Status: completed in current archive.

Evidence:

- `models.py` defines 9 categories.
- DB contains 336 equipment records grouped across 9 categories.
- `daily_entry.html` now renders all 9 category sections.

### TASK-WIALON-001 — Fix Wialon duration parser

Priority: P1  
Status: completed in current archive.

Scope:

- Support `N день/дня/дней HH:MM:SS`.

### TASK-WORKLOAD-001 — Add workload report

Priority: P1  
Status: completed in current archive.

Routes:

- `/wialon/workload`
- `/wialon/workload/export`

## Paused

### TASK-SPARE-001 — Full spare parts approval workflow

Priority: P2  
Status: paused by user.

Planned parts:

- Applicant-only role.
- Notifications.
- Expanded statuses.
- Installation tracking.
- Anomaly detection.

Do not continue without user re-approval.

### TASK-FIN-001 — Cash and receivables

Priority: P3  
Status: blocked.

Blocker:

- Needs formal accounting rules from finance/accounting responsible person.

## Backlog

### TASK-UI-001 — Finish remaining UZ/RU translation gaps

Priority: P1  
Status: **COMPLETED 2026-05-23** — all phases (001A audit, 001B Phase 1, 001C Phase 2) done and verified.

**TASK-UI-001A — Audit (completed 2026-05-23)**

- All 34 templates inspected. No mojibake found.
- 4 gap categories: fuel module entirely Russian, fuel flash messages Russian,
  scattered hardcoded labels in 10+ templates, 19 missing translation keys.
- Full findings in `docs/UI_TRANSLATION_AUDIT.md`.

**TASK-UI-001B — Implementation**

Phase 1 (2026-05-23 — COMPLETED):
- `translations.py`: 32 new UZ/RU pairs added.
- 10 templates updated: `base.html`, `deficiencies.html`, `admin_users.html`,
  `ref_equipment.html`, `spare_parts_list.html`, `spare_part_detail.html`,
  `spare_part_form.html`, `spare_parts_catalog.html`, `wialon.html`, `workload.html`.
- py_compile ALL PASS (`translations.py`, `app.py`, `fuel_routes.py`, `spare_parts.py`,
  `wialon_import.py`, `workload_report.py`). App import OK.
- TransportReport service restarted successfully. Site opened successfully after restart.
- No database changes. No logic changes.

Phase 2 (pending, requires business confirmation of UZ fuel vocabulary):
- Translate all 6 `templates/fuel/*.html` templates (entirely Russian).
- Replace 12 Russian flash messages in `fuel_routes.py` with UZ equivalents.
- See `docs/UI_TRANSLATION_AUDIT.md` GAP-1 and GAP-2 for full Russian string inventory.

**TASK-UI-001C — Phase 2: Fuel module translation (2026-05-23 — COMPLETED, verified 2026-05-23)**

Changes made:
- `translations.py`: 75 new fuel-module UZ/RU key pairs added.
- All 6 `templates/fuel/*.html` templates: Russian labels wrapped in `t()`.
- `fuel_routes.py`: `fuel_t()` helper added; all 12 Russian flash messages bilingual.
- No DB changes. No API changes. No logic changes.
- py_compile ALL PASS. App import OK.

Review findings fixed (2026-05-23):
- `translations.py`: 5 missing keys added (`Ёқилғи қолдиқлари`, 3 info-card sentences, stations empty state).
- `templates/fuel/initial_balance.html`: 3 hardcoded Russian info-card sentences wrapped in `t()`.
- `templates/fuel/stations.html`: hardcoded Russian empty state wrapped in `t()`.
- `templates/fuel/dashboard.html`: 2 literal АЗС table headers wrapped in `{{ t('АЗС') }}`.
- py_compile ALL PASS. App import OK.

Verification checklist (verified 2026-05-23):
- [x] Login, switch UZ/RU — open /fuel/ — stat cards and table headers change language
- [x] /fuel/warehouses — form labels and table change language
- [x] /fuel/transactions — filter, table headers, empty state change language
- [x] /fuel/receipts — form labels, table headers change language
- [x] /fuel/stations — form and table change language
- [x] /fuel/initial-balance — form and table change language
- [x] Flash messages after save/delete — show correct language (fuel_t() bilingual)
- [x] No raw t( or untranslated key strings visible
- [x] Topaz ping /fuel/api/fuel_ping — still returns JSON ok
- [x] Topaz sync /fuel/api/fuel_sync — token auth unchanged

Verification checklist Phase 1 (manual — VERIFIED):
- [x] Login, switch to RU → nav labels change
- [x] Deficiencies page: "Список недостатков" / "Недостатков не добавлено"
- [x] Admin users: "Наблюдатель" / "Заблокирован"
- [x] Equipment ref: inline edit labels translate
- [x] Spare parts list: table headers translate
- [x] Wialon import: period mode tabs translate
- [x] Workload: Норма/Факт headers translate
- [x] Multiselect widget: "Не выбрано" / "выбрано" in dropdown label

### TASK-REPORT-001 — Multi-select report filters

Priority: P2  
Status: planned.

Scope:

- Multi-select organizations.
- Multi-select categories.
- Apply to Excel and/or web view after business confirmation.

### TASK-REF-001 — Equipment reference improvements

Priority: P2  
Status: planned.

Scope:

- Category/type filters.
- Numbering.
- Total count.
- Excel export.

### TASK-OPS-001 — Migration discipline

Priority: P0  
Status: **completed 2026-05-22**

Changes made:

- `migration_utils.py` created: helpers `ensure_schema_migrations_table`,
  `is_migration_applied`, `record_migration`, `migration_checksum`.
  No external dependencies (stdlib only).
- `migrate_000_migration_registry.py` created: idempotent bootstrap script
  that creates `schema_migrations` and records itself.
- `models.py`: `SchemaMigration` SQLAlchemy model added so `db.create_all()`
  creates the table on fresh installs.
- `docs/MIGRATIONS.md` created: Windows migration procedure, script template,
  historical inventory, checklist.
- `docs/DECISIONS.md`: ADR-012 added.
- `docs/QA_CHECKLIST.md`: migration section added.

Acceptance criteria:

- `py_compile` passes on `models.py`, `migration_utils.py`,
  `migrate_000_migration_registry.py`, `app.py`.
- `from app import app` import check passes.
- `migrate_000_migration_registry.py` NOT yet run on production (TASK-OPS-002 handles backfill).

Operator action:

- Before deploying, stop the service, back up `transport.db`, then run:
    `"C:\Program Files\Python314\python.exe" migrate_000_migration_registry.py`
  See `docs/MIGRATIONS.md` for full procedure.

### TASK-OPS-002 — Backfill migration registry for historical migrations

Priority: P1  
Status: **completed 2026-06-13 - OPS002C closed with owner-confirmed safe decision; no additional historical data-only migrations recorded.**

**TASK-OPS-002A — Analysis (completed 2026-05-23)**

- All 14 historical migration scripts inspected.
- Database inspected with read-only queries only.
- `docs/MIGRATION_BACKFILL_ANALYSIS.md` created with evidence table and classifications.

Results:

- 8 scripts CONFIRMED_APPLIED — safe to backfill.
- 5 scripts require operator confirmation (migrate.py, migrate_equipment.py,
  migrate_worktypes.py, migrate_v42.py, migrate_categories_v9.py).
- 1 script NOT_APPLIED (migrate_v47.py) — must NOT be backfilled.
- schema_migrations already has 1 row (migrate_000_migration_registry confirmed applied).

**TASK-OPS-002B — Backfill script run on production (2026-05-23 — COMPLETED)**

Changes made:

- `migrate_001_backfill_historical_registry.py` run successfully on production.
- `migrate_v47.py`: OBSOLETE warning block added at the top (logic unchanged).

Acceptance verified:

- Run output: inserted=8, skipped=0. Self-recorded as migrate_001_backfill_historical_registry.
- `schema_migrations` verified with 10 rows (1 bootstrap + 8 backfill + 1 self).
- No business-table data changed.
- TransportReport service started successfully after the migration.

**TASK-OPS-002C — Pending scripts (awaiting operator confirmation)**

Scope:

- Operator must answer the 5 confirmation questions in
  `docs/MIGRATION_BACKFILL_ANALYSIS.md`.
- After confirmation, create a second backfill script or extend the registry
  manually for the confirmed scripts from the pending list.
- Do not mark any pending script as applied without operator confirmation.

Pending scripts requiring confirmation:
- migrate.py (LIKELY_APPLIED — data migration from old_transport.db)
- migrate_equipment.py (LIKELY_APPLIED — data migration from old_transport.db)
- migrate_worktypes.py (LIKELY_APPLIED — data migration from old_transport.db)
- migrate_v42.py (LIKELY_APPLIED — superseded by migrate_to_v45.py; operator decides)
- migrate_categories_v9.py (LIKELY_APPLIED — data-only; overlaps with migrate_equipment_excel.py)

### TASK-DEPLOY-001 — GitHub/private repository and hosting migration plan

Priority: P2  
Status: **completed 2026-05-23** (planning and audit only — no code/database changes)

Changes made:

- `docs/DEPLOYMENT_PLAN.md` created with full analysis:
  - Current deployment model documented.
  - What must NOT be committed to GitHub (instance/, reports/, logs/, Archive/, nssm.exe, .env).
  - Proposed `.gitignore` contents.
  - Proposed GitHub repository structure and branching convention.
  - Hosting options compared: dedicated mini-server+UPS, Windows VPS, Linux VPS, PaaS.
  - Recommended phased path: Phase 1 git hygiene → Phase 2 Windows VPS → Phase 3 HTTPS → Phase 4 Linux+PostgreSQL.
  - Database path: SQLite short-term with strict backups; PostgreSQL migration plan.
  - Internet access/security requirements: HTTPS, domain, firewall, VPN, secrets, admin password.
  - Topaz/Wialon impact: agent URL update required on server move.
  - Task breakdown: TASK-DEPLOY-002 through TASK-DEPLOY-006.
  - Security risk register.
- Syntax check: `py_compile app.py config.py run_server.py fuel_routes.py` — PASS.

### TASK-DEPLOY-002 — GitHub repository hygiene

Priority: P1  
Status: **completed 2026-05-23**

**TASK-DEPLOY-002A — .gitignore created (2026-05-23 — COMPLETED)**

Changes made:

- `.gitignore` created from baseline in `docs/DEPLOYMENT_PLAN.md` Section 3.
- Extra exclusions added after project inspection: `.claude/`, CSV migration log patterns,
  Cyrillic Excel reference file, `wialon_import_v3.py` (hardcoded stale token),
  orphaned root-level HTML files.
- No Python changes. No database changes. No service restart.

**TASK-DEPLOY-002B — GitHub repository creation and first push (2026-05-23 — COMPLETED)**

Changes made:

- Private GitHub repository created: https://github.com/sINte3/vehicle-soft
- Repository visibility: Private. Local branch: `main`. Remote: `origin`.
- `.gitignore` updated with two additional exclusions before first commit:
  `/PROMPT.md` (root-level prompt file) and `*.docx` (binary user guide excluded).
- `git init`, `git add .`, initial commit, remote added, pushed to `origin/main`.
- Tag `v1.0-production-2026-05-23` created and pushed.
- Final `git status`: branch main up to date with origin/main, working tree clean.
- `PROMPT.md` and `Rukovodstvo_polzovatelya.docx` confirmed excluded from first commit.
- Sensitive/runtime files confirmed excluded: `instance/`, `reports/`, `logs/`, `Archive/`,
  `nssm.exe`, `wialon_import_v3.py`, `PROMPT_*.md`, `old_transport.db`, `.env`.
- No application code changed. No database changed. No service restarted.

### TASK-DEPLOY-003 — .gitignore and secret scan

Priority: P0 (must run before first `git push`)  
Status: **TASK-DEPLOY-003A, 003B, and 003C completed 2026-05-23. No blocking findings remain.**

**TASK-DEPLOY-003C — .gitignore root-only pattern anchoring (2026-05-23 — COMPLETED)**

Changes made:

- `.gitignore`: six filename patterns anchored with leading `/` to restrict matching to
  the project root only: `/wialon.html`, `/wialon_auto_match.html`, `/wialon_report_v2.html`,
  `/Agroklastr_Tehnika_Konsolidaciya.xlsx`, `/Агрокластер_Техника_Консолидация.xlsx`,
  `/wialon_import_v3.py`.
- `templates/wialon.html` and `templates/wialon_auto_match.html` correctly remain committable.
- Documentation wording updated: `fuel_routes.py` hardcoded token references use plain language;
  blocking finding clarified that `<REDACTED_LEGACY_FUEL_API_TOKEN>` is a placeholder only.
- No application code changed. No database changed. No service restarted.

**TASK-DEPLOY-003B — Secret scan artifact redaction (2026-05-23 — COMPLETED)**

Changes made:

- Literal legacy API token value redacted from all commit-eligible files:
  `.gitignore`, `docs/SECRET_SCAN_REPORT.md`, `docs/AGENT_STATE.md`, `docs/TASKS.md`,
  `AUDIT_REPORT.md`. Replaced with `<REDACTED_LEGACY_FUEL_API_TOKEN>` placeholder.
- `/PROMPT_*.md` pattern added to `.gitignore` to exclude root-level Claude/ChatGPT
  handoff prompt files. `docs/PROMPT_PROTOCOL.md` is unaffected (pattern anchored to root).
- `docs/SECRET_SCAN_REPORT.md` updated with TASK-DEPLOY-003B section.
- No application code changed. No database changed. No service restarted.

**TASK-DEPLOY-003A — Secret scan (2026-05-23 — COMPLETED)**

Changes made:

- Full source scan across `*.py`, `*.bat`, `*.html`, `*.js`, `*.css`.
- One blocking finding found and resolved: `wialon_import_v3.py:674` hardcoded
  `<REDACTED_LEGACY_FUEL_API_TOKEN>` token — excluded from repo via `.gitignore`.
- All other findings are expected: `admin123` seed default, `PG_PASS changeme`
  not active, `SECRET_KEY` dev fallback clearly named, private LAN IPs in comments.
- `config.py`, `fuel_routes.py`, `run_server.py` confirmed clean (TASK-SEC-002).
- `docs/SECRET_SCAN_REPORT.md` created.
- Final verdict: SAFE to push to private GitHub repository.

Acceptance criteria met:

- Zero secrets in files that will be committed.
- `wialon_import_v3.py` excluded from repo.
- `.gitignore` verified against all known sensitive paths.
- Deployment docs updated with post-install admin password change requirement.

### TASK-DEPLOY-004 — Release package and backup procedure

Priority: P1  
Status: **completed and verified 2026-05-23** (004 → 004B → 004C → 004D → 004E all done)

Files created (not yet executed):

- `docs/RELEASE_AND_BACKUP_PROCEDURE.md` — full procedure document (purpose, pre-update
  checklist, update procedure, migration rule, rollback, manual backup, Task Scheduler
  setup, backup verification, restore procedure, QA checklist, risks, operator commands).
- `update.bat` — production update helper: pre-update backup (via backup_transport_db.py),
  service stop, git pull --ff-only, syntax check, import check, migration warning with
  pause, service start. Exits immediately on any failure with clear next-steps message.
- `backup_transport_db.bat` — daily backup script: calls backup_transport_db.py (SQLite
  online backup API). ASCII-only output. Exits non-zero on any failure.

### TASK-DEPLOY-004D — Fix backup_transport_db.bat wrapper

Priority: P1  
Status: **completed 2026-05-23**

Problem fixed: TASK-DEPLOY-004B replaced the raw `copy /Y` logic in `backup_transport_db.bat`
with a call to `backup_transport_db.py`, but the wrapper exited with bare `exit /b %ERRORLEVEL%`
and printed no success or failure messages of its own.

Changes made:

- `backup_transport_db.bat` fully replaced with the correct wrapper:
  - No raw `copy /Y`. No SOURCE/DEST_FILE variables. No PowerShell timestamp block.
  - Calls `"C:\Program Files\Python314\python.exe" "%~dp0backup_transport_db.py"`.
  - On failure (`errorlevel 1`): prints "Backup FAILED. See backup_transport_db.py output above."
    and exits with code 1.
  - On success: prints "Backup completed successfully." and exits with code 0.
  - Comment block updated: removed stale "Updated by TASK-DEPLOY-004B" reference.
- `backup_transport_db.py` unchanged. py_compile PASS.
- `docs/AGENT_STATE.md` and `docs/TASKS.md` updated.
- No application code changed. No database changed. No service restarted. No migrations.

### TASK-DEPLOY-004C — Fix update.bat pre-update backup failure message

Priority: P1  
Status: **completed 2026-05-23**

Problem fixed: TASK-DEPLOY-004B left the STEP 1 failure block in `update.bat` saying only
"Check disk space and permissions." Missing: "and backup_transport_db.py output."

Changes made:

- `update.bat` STEP 1 failure block: error message corrected to read
  "Check disk space, permissions, and backup_transport_db.py output."
- All other 004B changes confirmed present: no raw `copy /Y`, no `BACKUP_FILE` variable,
  no PowerShell TIMESTAMP block; rollback echoes reference `%BACKUP_DIR%`;
  final success message references `%BACKUP_DIR%`.
- `docs/AGENT_STATE.md` and `docs/TASKS.md` updated.
- py_compile on `backup_transport_db.py` PASS.
- No application code changed. No database changed. No service restarted. No migrations.

### TASK-DEPLOY-004E — Close release/backup procedure after successful operator test

Priority: P1  
Status: **completed 2026-05-23**

Facts recorded:

- `py_compile backup_transport_db.py` — PASS (no output).
- `backup_transport_db.bat` manual run — SUCCESS.
  Backup: `C:\transport-report-backups\daily\transport_20260523_182423.db`
  Source size: 46,800,896 bytes. Destination size: 46,800,896 bytes.
  Integrity check: `ok`. Wrapper: `Backup completed successfully.`
- Directory verification: file confirmed present at 46,800,896 bytes.
- Task Scheduler task `TransportDBBackup` created: daily 02:00, SYSTEM, `/f`. Result: SUCCESS.
  Next run: 24.05.2026 2:00:00. State: Ready.
- Scheduled task manual run `schtasks /run /tn "TransportDBBackup"` — SUCCESS.
  New backup: `C:\transport-report-backups\daily\transport_20260523_182603.db`, 46,800,896 bytes.
- Commits `428104a` and `10652e2` pushed to `origin/main`. Working tree clean.
- Documentation only. No code, no database, no migrations, no service restart.

### TASK-DEPLOY-004B — Safe SQLite backup via online backup API

Priority: P1  
Status: **completed and verified 2026-05-23**

Problem fixed: TASK-DEPLOY-004 used raw `copy /Y transport.db` while the service was
running. In WAL mode with uncheckpointed pages, a raw copy of `.db` produces an
inconsistent backup. The `.db-wal` and `.db-shm` files were not copied.

Changes made:

- `backup_transport_db.py` created: stdlib only, no Flask imports. Uses
  `sqlite3.Connection.backup()` for a consistent online backup. Accepts `--dest-dir`
  and `--suffix` CLI arguments. Prints source/dest path and sizes. Performs
  `PRAGMA integrity_check` on the destination (requires result `ok`). Exits non-zero
  on any failure (source missing, backup error, dest missing, dest 0 bytes, bad integrity).
- `backup_transport_db.bat` updated: removed raw `copy /Y`; now calls
  `backup_transport_db.py` and propagates exit code.
- `update.bat` updated: STEP 1 now calls `backup_transport_db.py --dest-dir
  C:\transport-report-backups\before_update --suffix before_update`. Rollback echo
  messages updated to reference the backup directory rather than a `%BACKUP_FILE%` var.
- `docs/RELEASE_AND_BACKUP_PROCEDURE.md` updated: removed incorrect WAL safety claim;
  documented SQLite online backup API; updated output example and known risks table.
- No application code changed. No database changed. No service restarted. No migrations.

Operator verification completed 2026-05-23 (TASK-DEPLOY-004E):

- Manual test: PASS — backup created, integrity check ok, wrapper printed success.
- Task Scheduler task `TransportDBBackup` created and tested successfully.
- GitHub up to date. Working tree clean.

### TASK-DEPLOY-005 — Organization server production cutover

Priority: P2  
Depends on: TASK-DEPLOY-002, TASK-DEPLOY-003, TASK-DEPLOY-004  
Status: **COMPLETED 2026-05-24 — organization Windows Server production cutover completed. TASK-DEPLOY-005A, 005B, 005D, 005E, 005F all done.**

Acceptance evidence:

- Old `TransportReport` on workstation (`10.103.25.200`) STOPPED.
- New `TransportReport` on `srv-yoqsh` (`10.103.25.14`) RUNNING at `http://10.103.25.14:5050`.
- Production QA passed: admin, operator, Excel, Wialon, Fuel/АЗС all OK.
- Production backup task `TransportDBBackupProduction` (daily 02:00) created and tested.
- Topaz agent ping/auth/sync OK — no 401/500/traceback reported.
- DB counts verified: users=2, equipment=336, fuel_transactions2=391,284, schema_migrations=10.
- No errors in `service.log` or `error.log` after startup.

**TASK-DEPLOY-005F — Record organization-server production cutover completion (2026-05-24 — COMPLETED)**

Changes made:

- `docs/ORG_WINDOWS_SERVER_CUTOVER_RUNBOOK.md`: "CUTOVER COMPLETION RECORD — 2026-05-24" section
  added at the top with full cutover facts (old/new server state, backup/cold copy facts, DB counts,
  backup task, Topaz switch, anti split-brain instruction, rollback status). Section Q table
  filled in with all verified values.
- `docs/AGENT_STATE.md`: state date updated; current production state table added; recommended
  next tasks updated to TASK-DEPLOY-005G; TASK-DEPLOY-005F added to recently completed list.
- `docs/TASKS.md`: TASK-DEPLOY-005 overall status marked COMPLETED; TASK-DEPLOY-005F entry added;
  TASK-DEPLOY-005G added as planned.
- `docs/DEPLOYMENT_PLAN.md`: TASK-DEPLOY-005 status updated to COMPLETED; current production URL
  and endpoints updated.
- `docs/RELEASE_AND_BACKUP_PROCEDURE.md`: note added for production backup on new server.
- No application code changed. No database changed. No service restarted. No migrations.
  No git pull. No git push.

**TASK-DEPLOY-005E — Record staging QA and prepare production cutover plan (2026-05-23 — COMPLETED)**

Changes made:

- `docs/ORG_WINDOWS_SERVER_STAGING_RUNBOOK.md`: staging QA marked PASSED; backup history updated
  with `--source` manual test file (`transport_20260523_225240_staging.db`) and Task Scheduler
  test run file (`transport_20260523_225344_staging.db`), both 46,809,088 bytes, integrity ok.
  Section 4 QA checklist: all items [x]. Section 5 operator next steps updated to point to cutover runbook.
- `docs/ORG_WINDOWS_SERVER_CUTOVER_RUNBOOK.md` created: full production cutover runbook covering
  preconditions, recommended production paths on new server, pre-cutover checklist on workstation
  (git status, final backup, service stop, cold copy), DB transfer, environment variables (placeholder
  commands only), dependency install, DB copy, syntax/import checks, DB count verification, production
  backup wrapper + Task Scheduler, NSSM service install, Windows Firewall, production QA checklist,
  Topaz switch procedure, user communication, rollback plan (before and after Topaz switch), anti
  split-brain warning, cutover completion record, and post-cutover tasks.
- `docs/AGENT_STATE.md` and `docs/TASKS.md` updated.
- Documentation only. No application code changed. No database changed. No service restarted. No migrations.

**TASK-DEPLOY-005A — VPS staging runbook (2026-05-23 — COMPLETED)**

Changes made:

- `docs/VPS_STAGING_RUNBOOK.md` created: 16-section operator runbook with exact commands.
- Documentation only. No code, database, or service changes.

Runbook covers: VPS prerequisites, Git + Python 3.14 + NSSM install, GitHub private repo clone
with PAT, SECRET_KEY + FUEL_API_TOKEN via setx /M, DB transfer from production with integrity
check, install_service.bat usage, Windows Firewall rules, Nginx reverse proxy skeleton, daily
backup Task Scheduler setup, QA smoke test checklist, Topaz staging policy, cutover plan,
rollback plan, 15 open questions for operator, and 26-step exact operator command checklist.

**TASK-DEPLOY-005D — Add --source support to backup tool for staging (2026-05-23 — COMPLETED)**

Changes made:

- `backup_transport_db.py`: `--source <path>` argument added. Default source remains
  `C:\transport-report\instance\transport.db`. `source_path = args.source` replaces
  the module-level constant. Docstring updated with staging usage example.
- `backup_transport_db.bat`: unchanged — production default continues to apply.
- `docs/ORG_WINDOWS_SERVER_STAGING_RUNBOOK.md` created: staging server facts,
  DB counts, manual backup history, proper backup command, Task Scheduler setup
  (TransportDBBackupStaging, 03:00 daily, SYSTEM), QA checklist, operator next steps,
  production-vs-staging comparison, Topaz/Wialon staging policy.
- `docs/AGENT_STATE.md` and `docs/TASKS.md` updated.
- py_compile PASS. Functional test PASS (--source, integrity_check ok, 46,809,088 bytes).
- No application code changed. No database changed. No service restarted. No migrations.

Staging backup command (for operator to run on srv-yoqsh after pulling updated script):

```cmd
cd C:\transport-report-staging
"C:\Program Files\Python314\python.exe" backup_transport_db.py ^
  --source C:\transport-report-staging\instance\transport.db ^
  --dest-dir D:\transport-report-backups\staging\daily ^
  --suffix staging
```

Task Scheduler setup (run CMD as Administrator on srv-yoqsh):

```cmd
schtasks /create /tn "TransportDBBackupStaging" ^
  /tr "\"C:\Program Files\Python314\python.exe\" C:\transport-report-staging\backup_transport_db.py --source C:\transport-report-staging\instance\transport.db --dest-dir D:\transport-report-backups\staging\daily --suffix staging" ^
  /sc daily /st 03:00 /ru SYSTEM /f
```

**TASK-DEPLOY-005B — Fix VPS runbook order and stale deployment-plan backup wording (2026-05-23 — COMPLETED)**

Problem fixed: review of TASK-DEPLOY-005A surfaced three documentation issues:

1. `docs/VPS_STAGING_RUNBOOK.md` told the operator to copy `nssm.exe` into
   `C:\transport-report\` and create `C:\transport-report\instance\` before `git clone`,
   which makes the later `git clone ... C:\transport-report` fail because the target is
   non-empty. The workaround was buried in a note instead of the primary path being clean.
2. `docs/DEPLOYMENT_PLAN.md` Sections 7 and 8 still showed raw
   `copy "...transport.db..." "D:\backups\..."` examples and a "keep last 7 days; delete
   older files" retention claim. These were obsolete after TASK-DEPLOY-004B/004E replaced
   the raw copy with `backup_transport_db.py` (SQLite online backup API). The TASK-DEPLOY-004
   scope in the same file still described the planned-but-not-built design instead of the
   completed implementation.
3. `docs/AGENT_STATE.md` had a duplicated TASK-OPS-002C paragraph in "Current recommended
   next task".

Changes made:

- `docs/VPS_STAGING_RUNBOOK.md`:
  - Section 3.3 (NSSM) rewritten: do not pre-create `C:\transport-report\`; copy
    `nssm.exe` into the folder only after `git clone` (or place at `C:\nssm\nssm.exe`).
  - Section 6.3 (instance dir) clarified: runs after `git clone`, inside the cloned folder.
  - Section 8.1 prerequisites listed in deployment order.
  - Section 16 numbered checklist rewritten so the primary path is rent VPS → RDP → Git →
    Python 3.14 → `git clone` into empty `C:\transport-report` → drop `nssm.exe` into it →
    setx env vars → firewall → backup production → transfer → create `instance\` → copy DB
    → verify DB → install dependencies → syntax/import checks → `install_service.bat` →
    verify service → QA → backups.
  - "Alternative if `C:\transport-report` already exists" kept as a troubleshooting note
    at the end of Section 16 (rename folder and re-clone, or `git init`+`fetch`+`checkout`
    if the operator understands the implications). Not the primary path.
- `docs/DEPLOYMENT_PLAN.md`:
  - Section 7: raw `copy` example replaced with verified
    `cd C:\transport-report && backup_transport_db.bat`. Method documented as SQLite
    online backup API with `PRAGMA integrity_check`. Task `TransportDBBackup` (daily 02:00,
    SYSTEM, target `C:\transport-report-backups\daily\`) referenced. Automated retention
    moved from "required discipline" to "not currently automated — future improvement".
  - Section 8 "Backups": same raw-copy example replaced with `backup_transport_db.bat`
    description and verified Task Scheduler setup. Wrong `D:\backups\` / `C:\backups\`
    paths corrected to `C:\transport-report-backups\daily\`.
  - TASK-DEPLOY-004 scope rewritten to describe the completed implementation
    (`docs/RELEASE_AND_BACKUP_PROCEDURE.md`, `update.bat`, `backup_transport_db.py`,
    `backup_transport_db.bat`, Task Scheduler task `TransportDBBackup` verified by operator).
    Acceptance criteria marked met. Unsupported retention and offsite sync listed under
    "Not implemented (deferred — future improvement)".
- `docs/AGENT_STATE.md`: duplicate TASK-OPS-002C paragraph removed; one copy kept.
- `docs/TASKS.md`: this TASK-DEPLOY-005B entry added.
- Documentation only. No application code changed. No database changed. No service
  restarted. No migrations. No git commit. No git push.

Allowed safe read-only checks run during fix: file reads only.

### TASK-DEPLOY-005G — Post-cutover monitoring and cleanup

Priority: P2  
Depends on: TASK-DEPLOY-005F  
Status: **planned**

Scope:

- Monitor `D:\transport-report-backups\production\daily\` daily for 3–5 business days;
  confirm a new backup file appears each morning.
- Monitor `C:\transport-report\logs\service.log` and `error.log` for unexpected errors.
- Keep old workstation `TransportReport` service STOPPED as rollback standby.
- Remove or disable the old service on `10.103.25.200` only after explicit owner approval.
- Document Topaz agent exact location and task name in a dedicated ops note
  (`C:\topaz_agent.py`, task: `TopazFuelAgent`) once confirmed stable over multiple days.
- Optionally add a small "old server disabled" landing note if users accidentally try
  the old URL `http://10.103.25.200:5050`.

Acceptance criteria:

- 5 consecutive days of successful production backups confirmed.
- No recurring errors in logs.
- Owner has been notified and has confirmed the old workstation can remain stopped.

### TASK-DEPLOY-006 — PostgreSQL migration research

Priority: P3  
Status: planned. Not urgent — SQLite is stable at current scale.

Scope:

- Audit `models.py` for SQLite-specific constructs.
- Write and test SQLite → PostgreSQL bulk migration script.
- Verify `fuel_transactions2` (~391 K rows) migrates correctly.
- Document cutover procedure.

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

## REPORT001C — Fuel report and analytics

Status: COMPLETED  
Date: 2026-06-04  
Commit: pending at documentation generation time

Completed:
- Added Fuel report screen.
- Added period, warehouse, and station filters.
- Added fuel summary cards.
- Added warehouse and station breakdowns.
- Added recent transactions and synchronization history.
- Added Excel export.
- Added dashboard navigation link.
- Verified staging and production smoke tests.


## REPORT001D — Fuel anomalies and warnings — COMPLETED 2026-06-04

Статус: completed / production released.

Результат:
- добавлен блок проблем и предупреждений в `/fuel/report`;
- добавлены проверки отрицательных расчётных остатков, складов без начального остатка, АЗС без склада, отключённых АЗС с выдачами, неизвестных Topaz ID, давности синхронизации, крупных выдач и некорректных транзакций;
- добавлен лист предупреждений в Excel Fuel report;
- production smoke test passed;
- миграция БД не требовалась.

## REPORT001E-1 — Fuel warning registry — COMPLETED 2026-06-05

Status: completed in production.

Result:
- added managed Fuel warning registry;
- added `fuel_warning_reviews` table;
- added `/fuel/warnings` page;
- added warning status/comment workflow;
- added warning filters and search;
- added audit events for warning review actions;
- integrated warning status into `/fuel/report`.

Production backup:
- D:\transport-report-backups\production\daily\transport_20260605_115535.db

Release note:
- docs/RELEASE_REPORT001E_WARNING_REGISTRY_20260605.md



## BOT002B — Telegram bot runner for spare parts — COMPLETED 2026-06-09

Status: completed in production.

Result:
- created and deployed Telegram bot runner for spare parts requests;
- added bot_state.db for session persistence;
- added Telegram bot routes (/api/bot/health, /api/bot/logout);
- added Telegram commands: /start, /link, /status, /pending, /logout;
- added Telegram account linking workflow;
- all smoke tests passed (7 bot files, app import, bot routes, DB integrity, all Telegram commands).

Production backup:
- D:\transport-report-backups\production\daily\transport_20260609_143144.db

Production server:
- srv-yoqsh (10.103.25.14)
- TransportBot service created and running
- No DB migration required

## DASH001 — Management dashboard for main page — COMPLETED 2026-06-06

Status: completed in production.

Result:
- added management dashboard to the main page;
- added transport work KPIs;
- added Fuel and Topaz KPIs;
- added Fuel warning KPIs;
- added spare part request KPIs;
- added Wialon mapping KPIs;
- added system status and recent audit block;
- added quick links to operational sections;
- preserved existing daily work report and filters.

Production backup:
- D:\transport-report-backups\production\daily\transport_20260606_093202.db

Release note:
- docs/RELEASE_DASH001_MAIN_DASHBOARD_20260606.md

## 2026-06-13 - EXTAUDIT001 / QA003 / OPS002C closure

Status: completed.

Completed and documented:

- EXTAUDIT001 closure report: `docs/EXTAUDIT001_CLOSURE_REPORT_20260613.md`.
- QA003 post-FIX003A regression audit: `docs/QA003_POST_FIX003A_REGRESSION_20260613.md`.
- OPS002C pending migration confirmation: `docs/OPS002C_PENDING_MIGRATIONS_CONFIRMATION_20260613.md`.
- OPS002C closure report: `docs/OPS002C_CLOSURE_REPORT_20260613.md`.

Final OPS002C owner decision:

- No additional historical data-only migrations were recorded.
- `migrate.py`, `migrate_equipment.py`, `migrate_worktypes.py`, and `migrate_categories_v9.py` remain unrecorded due to no reliable proof and missing `old_transport.db`.
- `migrate_v42.py` was skipped because its key effect overlaps with already-recorded `migrate_to_v45`.
- No database changes were made during OPS002C closure.

Current confirmed state:

- staging HEAD after OPS002C closure: `fe0b991`
- production HEAD after OPS002C closure: `fe0b991`
- origin/main after OPS002C closure: `fe0b991`
- production services: `TransportReport`, `TransportBot`, `TransportBot003` running
- BOT003 dry-run: error null

## 2026-06-13 - DASH002B main dashboard drill-down links

Status: completed and deployed to production.

Completed:

- Main dashboard `/` improved with quick drill-down links.
- Transport card: link to main report.
- Fuel card: links to fuel report, warnings, transactions.
- Warnings card: severity banner plus links to registry, new warnings, critical warnings.
- Spare parts card: links to all requests, submitted requests, new request.
- Wialon card: links to mapping, auto-mapping, report.
- Role-aware access behavior preserved through existing module access checks.
- No database schema changes.
- No data migrations.

Validation:

- Staging authenticated `/` render: 200.
- Production authenticated `/` render: 200.
- `py_compile`: passed.
- Production `/login`: 200.
- Production `/`: 302 for unauthenticated users, expected redirect to login.
- `TransportReport`: running.
- `TransportBot`: running.
- `TransportBot003`: running.
- BOT003 dry-run: error null.

Commits:

- `6d3fd4c` - Improve main dashboard drill-down links.
- `d05b673` - Fix dashboard warning quick links placement.
- `30aeecf` - Document DASH002B production rollout.

Reports:

- `docs/DASH002B_STAGING_VALIDATION_20260613.md`
- `docs/DASH002B_PRODUCTION_ROLLOUT_20260613.md`

## 2026-06-13 - DASH002C dashboard legacy report separation polish

Status: completed and deployed to production.

Completed:

- Top page header changed from daily report wording to main panel wording.
- Main dashboard remains at `/`.
- Legacy daily report/filter block remains visible.
- Added visual separator before the legacy daily report section.
- Added section title: daily report and data entry.
- Added quick actions:
  - data entry
  - full report
- Existing dashboard cards and quick links preserved.
- No database schema changes.
- No data migrations.
- No route changes.
- No business logic changes.

Validation:

- Staging authenticated `/` render: 200.
- Production authenticated `/` render: 200.
- `py_compile`: passed.
- Production `/login`: 200.
- Production `/`: 302 for unauthenticated users, expected redirect to login.
- `TransportReport`: running.
- `TransportBot`: running.
- `TransportBot003`: running.
- BOT003 dry-run: error null.

Commits:

- `db191cd` - Polish dashboard legacy report separation.
- `2152d32` - Document DASH002C production rollout.

Reports:

- `docs/DASH002C_STAGING_VALIDATION_20260613.md`
- `docs/DASH002C_PRODUCTION_ROLLOUT_20260613.md`

## 2026-06-13 - TASK-REF-001A equipment reference filters and diagnostics

Status: completed and deployed to production.

Completed:

- Improved `/ref/equipment`.
- Added equipment search by:
  - equipment name
  - plate number
  - equipment type
  - organization name
  - organization short name
- Added status filter:
  - all
  - active
  - inactive
- Added equipment statistics cards:
  - total equipment in accessible organizations
  - active / inactive equipment
  - filtered result count
  - empty default unit count
- Added diagnostics block:
  - zero default price count
  - normalized duplicate plate groups
  - first duplicate plate examples
- Added inactive-equipment visual marker.
- Added linked-record count marker near delete/disable actions.
- Excel export now respects search and status filters.

Safety scope:

- No database schema changes.
- No data migrations.
- No automatic equipment merge.
- No automatic duplicate cleanup.
- No changes to `equipment_id` relationships.
- No changes to daily report, Wialon import, fuel, or spare-parts business logic.

Validation:

- Staging route checks passed.
- Production route checks passed.
- `py_compile`: passed.
- Production backup integrity: ok.
- `TransportReport`: running.
- `TransportBot`: running.
- `TransportBot003`: running.
- BOT003 dry-run: error null.

Commits:

- `a7865f1` - Improve equipment reference filters and diagnostics.
- `79655e2` - Document TASK-REF-001A production rollout.

Reports:

- `docs/TASK_REF_001A_STAGING_VALIDATION_20260613.md`
- `docs/TASK_REF_001A_PRODUCTION_ROLLOUT_20260613.md`

## 2026-06-13  TASK-REF-001B production complete

Status: COMPLETE.

Production commit:

- `be30d1d Improve reference pages filters and diagnostics`

Completed:

- Improved `/ref/organizations` with search, statistics, short-name field, and linked-record visibility.
- Improved `/ref/work_types` with search, usage filter, statistics, diagnostics, and usage counts.
- Improved `/ref/customers` with search, type filter, usage filter, statistics, diagnostics, and usage counts.
- Preserved existing delete blocking and edit behavior.
- No schema changes.
- No data migrations.
- No automatic cleanup or normalization.
- Production validation passed.
- Manual browser validation confirmed by screenshots.

Production services after rollout:

- `TransportReport`: RUNNING
- `TransportBot`: RUNNING
- `TransportBot003`: RUNNING

Related docs:

- `docs/TASK_REF_001B_STAGING_VALIDATION_20260613.md`
- `docs/TASK_REF_001B_PRODUCTION_ROLLOUT_20260613.md`

## 2026-06-13  TASK-REF-001C discovery complete

Status: DISCOVERY COMPLETE.

Scope:

- Read-only production data audit.
- No data changes.
- No migrations.
- No service restart.

Main findings:

- `work_types = 104`
- `customers = 9`
- `daily_records = 15946`
- duplicate work type name groups: 3
- work type values used in reports but missing from reference table: 5
- customer values used in reports but missing from reference table: 2020
- customer field is currently mixed free text, not a strict reference.

Decision:

- No automatic cleanup.
- No automatic customer normalization.
- Prepare export/diagnostic tools first.
- Only simple defaults may be fixed after business approval.

Related doc:

- `docs/TASK_REF_001C_DISCOVERY_AND_STRATEGY_20260613.md`

## 2026-06-13  TASK-REF-001D production complete

Status: COMPLETE.

Production commit:

- `34acb33 Add reference cleanup diagnostic exports`

Completed:

- Added `/ref/work_types/export_diagnostics`.
- Added `/ref/customers/export_diagnostics`.
- Added `Excel диагностика` button to `/ref/work_types`.
- Added `Excel диагностика` button to `/ref/customers`.
- Work type export includes Summary, Reference, Duplicate names, Missing from reference, Quality issues.
- Customer export includes Summary, Reference, Missing from reference, Similarity groups, Pattern groups.
- Production validation passed.
- Manual production browser validation confirmed.

Safety:

- No schema changes.
- No migrations.
- No data modifications.
- Read-only diagnostic exports only.

Related docs:

- `docs/TASK_REF_001D_STAGING_VALIDATION_20260613.md`
- `docs/TASK_REF_001D_PRODUCTION_ROLLOUT_20260613.md`

## 2026-06-13  UX002A production complete

Status: COMPLETE.

Production commit:

- `1d0488c Add shared UX design system baseline`

Completed:

- Added shared UX design system baseline to `templates/base.html`.
- Added common visual rules for page headers, cards, filters, buttons, forms, tables, badges, flash blocks, responsive layout, and print layout.
- Production validation passed.
- Manual production browser validation confirmed.

Safety:

- No schema changes.
- No migrations.
- No route changes.
- No business logic changes.
- No bot logic changes.

Related docs:

- `docs/UX002A_STAGING_VALIDATION_20260613.md`
- `docs/UX002A_PRODUCTION_ROLLOUT_20260613.md`

## 2026-06-13  REPORT002A production complete

Status: COMPLETE.

Production commits:

- `afd583e Improve transport report UX`
- `e2282d7 Fix REPORT002A date dash consistency`

Completed:

- Improved `/report` page header.
- Added visible active filter summary.
- Improved report filter pills.
- Improved export/filter card styling.
- Added report form, KPI grid, and table CSS hooks.
- Production validation passed.
- Manual production browser validation confirmed.

Safety:

- No schema changes.
- No migrations.
- No route changes.
- No business logic changes.
- No Excel generation logic changes.
- No bot logic changes.

Related docs:

- `docs/REPORT002A_STAGING_VALIDATION_20260613.md`
- `docs/REPORT002A_PRODUCTION_ROLLOUT_20260613.md`

## 2026-06-14  ENTRY002A production complete

Status: COMPLETE.

Production commits:

- `7cc64f4 Improve daily entry UX`
- `253beac Fix ENTRY002A staging doc markers`

Completed:

- Improved `/entry` page header.
- Added date and context summary pills.
- Added short guidance panel.
- Improved filter card styling.
- Added filter form and save form CSS hooks.
- Improved organization/equipment card visual styling.
- Added working vs idle visual grouping.
- Added sticky bottom save area styling.
- Added non-blocking visual hints for incomplete working rows.
- Production validation passed.
- Manual production browser validation confirmed.

Safety:

- No schema changes.
- No migrations.
- No route changes.
- No save_entry changes.
- No copy_previous_day changes.
- No Excel/report logic changes.
- No bot logic changes.

Related docs:

- `docs/ENTRY002A_STAGING_VALIDATION_20260614.md`
- `docs/ENTRY002A_PRODUCTION_ROLLOUT_20260614.md`

## 2026-06-14  SPARE002A production complete

Status: COMPLETE.

Production commits:

- `7e8ac60 Improve spare parts UX`
- `b76cede Fix SPARE002A staging doc markers`
- `6d391ab Fix spare parts header actions`

Completed:

- Improved `/spare-parts/` page header.
- Added status/context summary pills.
- Added guidance panel.
- Improved list filter form layout.
- Improved list table visual density.
- Improved `/spare-parts/new` page header.
- Added new request context summary pills.
- Added new request guidance panel.
- Improved new request form grouping.
- Improved new request table styling.
- Added sticky action row styling.
- Added non-blocking visual hints for incomplete item rows.
- Corrected top action buttons into one horizontal header row.
- Production validation passed.
- Manual production browser validation confirmed.

Safety:

- No schema changes.
- No migrations.
- No route changes.
- No spare_parts.py changes.
- No save_request changes.
- No submit_request changes.
- No approve_request changes.
- No reject_request changes.
- No BOT003 outbox logic changes.
- No bot logic changes.

Related docs:

- `docs/SPARE002A_STAGING_VALIDATION_20260614.md`
- `docs/SPARE002A_PRODUCTION_ROLLOUT_20260614.md`

## 2026-06-14  FUELST001 production complete

Status: COMPLETE.

Production commits:

- `9ad7267 Fix fuel stations page render`
- `4aee239 Fix FUELST001 staging doc markers`

Completed:

- Fixed `/fuel/stations` 500 error.
- Added safe template fallback for missing `L_form`.
- Production validation passed.
- Manual production browser validation confirmed.

Safety:

- No schema changes.
- No migrations.
- No route changes.
- No fuel_routes.py changes.
- No save_station changes.
- No delete_station changes.
- No enable_station changes.
- No BOT003 outbox logic changes.
- No bot logic changes.

Related docs:

- `docs/FUELST001_STAGING_VALIDATION_20260614.md`
- `docs/FUELST001_PRODUCTION_ROLLOUT_20260614.md`

## 2026-06-14  FUEL002A receipts production complete

Status: COMPLETE.

Production commit:

- `ed8955d Improve fuel receipts UX`

Completed:

- Improved `/fuel/receipts` UX.
- Added page header, subtitle, summary pills and guidance panel.
- Improved receipt form grouping.
- Improved filter form grouping.
- Improved table readability and horizontal wrapper.
- Added visual-only required-field hints.
- Production validation passed.
- Manual production browser validation confirmed.

Safety:

- No schema changes.
- No migrations.
- No route changes.
- No fuel_routes.py changes.
- No save_receipt changes.
- No delete_receipt changes.
- No station logic changes.
- No BOT003 outbox logic changes.
- No bot logic changes.

Related docs:

- `docs/FUEL002A_RECEIPTS_STAGING_VALIDATION_20260614.md`
- `docs/FUEL002A_RECEIPTS_PRODUCTION_ROLLOUT_20260614.md`

## 2026-06-14  FUEL002B transactions production complete

Status: COMPLETE.

Production commit:

- `44a706f Apply actual fuel transactions template UX`

Important note:

- `135ff40` and `3956887` were incomplete documentation/correction commits.
- The real template change is in `44a706f`.

Completed:

- Improved `/fuel/transactions` UX.
- Added page header, subtitle, summary pills and guidance panel.
- Improved date/warehouse filter grouping.
- Improved transactions table wrapper and readability.
- Improved sync logs table wrapper and readability.
- Production validation passed.
- Manual production browser validation confirmed.

Safety:

- No schema changes.
- No migrations.
- No route changes.
- No fuel_routes.py changes.
- No transaction query changes.
- No Topaz sync changes.
- No receipt logic changes.
- No station logic changes.
- No BOT003 outbox logic changes.
- No bot logic changes.

Related docs:

- `docs/FUEL002B_TRANSACTIONS_STAGING_VALIDATION_20260614.md`
- `docs/FUEL002B_TRANSACTIONS_PRODUCTION_ROLLOUT_20260614.md`


## FUEL002C_WAREHOUSES_PRODUCTION_DONE

Status: completed.

Completed on production:

- FUEL002C warehouses UX
- FUEL002C warehouses localization hotfix
- Production commit: `81a1782`
- Production URL: `/fuel/warehouses`
- Final verification: passed


## FUEL002D_REPORT_PRODUCTION_DONE

Status: completed.

Completed on production:

- FUEL002D report UX
- Production commit: `47bb0f2`
- Production URL: `/fuel/report`
- Final verification: passed


## FUEL002E_STATIONS_PRODUCTION_DONE

Status: completed.

Completed on production:

- FUEL002E stations UX
- Production commit: `adace00`
- Production URL: `/fuel/stations`
- Final verification: passed


## FUEL002F_INITIAL_BALANCE_PRODUCTION_DONE

Status: completed.

Completed on production:

- FUEL002F initial balance UX
- Production commit: `da4565d`
- Production URL: `/fuel/initial-balance`
- Final verification: passed


## FUEL002G_WARNINGS_PRODUCTION_DONE

Status: completed.

Completed on production:

- FUEL002G warnings UX
- Production commit: `0eef3e7`
- Production URL: `/fuel/warnings`
- Final verification: passed


## FUEL002H_DASHBOARD_PRODUCTION_DONE

Status: completed.

Completed on production:

- FUEL002H dashboard UX
- Production commit: `713ced3`
- Production URL: `/fuel/`
- Final verification: passed


## FUEL002_FINAL_QA_DONE

Status: completed.

Completed:

- Full fuel module UX cycle FUEL002A-H
- Final staging QA
- Final production QA
- Production HEAD: `17df914`
- Final QA result: passed

## 2026-06-15  DASH002 Main dashboard UX

Completed:

- [x] Read-only discovery for `/`.
- [x] Confirmed route: `app.py` / `index()`.
- [x] Confirmed template: `templates/index.html`.
- [x] Confirmed safe patch scope: template only.
- [x] Applied staging UX patch.
- [x] Validated py_compile, app import, template load, direct render and route behavior.
- [x] Restarted `TransportReportStaging`.
- [x] User visually checked staging.
- [x] Committed and pushed code commit `f2d73a9976e43346e9164d22ca33def90ba9d277`.
- [x] Backed up production source and DB.
- [x] Pulled code to production.
- [x] Validated production before restart.
- [x] Restarted only `TransportReport`.
- [x] Confirmed `TransportBot` and `TransportBot003` remained RUNNING.
- [x] User visually checked production.

Pending:

- [ ] Commit this docs-only update.
- [ ] Pull docs-only update to production without service restart.
- [ ] Start next module read-only discovery: `spare-parts`.

## 2026-06-15  SPARE001A Spare parts templates UX

Completed:

- [x] Read-only discovery for spare parts module.
- [x] Confirmed routes in `spare_parts.py`.
- [x] Confirmed DB tables and models.
- [x] Confirmed safe patch scope: 4 templates only.
- [x] Created staging backup for 4 templates.
- [x] Applied staging UX patch.
- [x] Cleaned trailing whitespace.
- [x] Validated `git diff --check`.
- [x] Validated `py_compile`.
- [x] Validated app import and template load.
- [x] Validated direct render for list, new, catalog and detail pages.
- [x] Restarted `TransportReportStaging`.
- [x] User visually checked staging.
- [x] Committed and pushed code commit `53cfb078ca78782e7d7a17ffdb80ae1c30bb9509`.
- [x] Backed up production source and DB.
- [x] Pulled code to production.
- [x] Validated production before restart.
- [x] Restarted only `TransportReport`.
- [x] Confirmed `TransportBot` and `TransportBot003` remained RUNNING.
- [x] User visually checked production.

Pending:

- [ ] Commit this docs-only update.
- [ ] Pull docs-only update to production without service restart.

## 2026-06-15  SPARE001B spare parts status history audit/backfill

Completed:

- [x] Read-only audit of `spare_parts.py` workflow.
- [x] Confirmed existing `_add_status_history(...)` helper.
- [x] Confirmed existing status history writes in submit/approve/reject paths.
- [x] Confirmed staging gap: 8 historical requests with zero history.
- [x] Confirmed production gap: 3 historical requests with zero history.
- [x] Backed up staging DB.
- [x] Backfilled staging status history.
- [x] Validated staging history coverage.
- [x] Backed up production DB.
- [x] Backfilled production status history.
- [x] Validated production history coverage.
- [x] Confirmed no code changes.
- [x] Confirmed no service restart.

Pending:

- [ ] Commit this docs-only update.
- [ ] Pull docs-only update to production without service restart.
- [ ] Continue with SPARE001C controlled workflow test on staging.

## 2026-06-15  SPARE001C controlled staging spare parts workflow test

Completed:

- [x] Backed up staging DB before controlled workflow test.
- [x] Created controlled test request 9.
- [x] Tested `draft -> submitted -> approved`.
- [x] Created controlled test request 10.
- [x] Tested `submitted -> rejected`.
- [x] Verified final statuses.
- [x] Verified 4 status history rows.
- [x] Verified audit logs.
- [x] Verified BOT003 outbox events.
- [x] Verified BOT003 staging delivery: 4 sent, 0 pending, 0 failed.
- [x] Confirmed Git remained clean.
- [x] Confirmed no production touch.
- [x] Confirmed no service restart.

Pending:

- [ ] Commit this docs-only update.
- [ ] Pull docs-only update to production without service restart.
- [ ] Continue with SPARE001D role/access read-only audit.

## 2026-06-15  SPARE001D spare parts role/access audit and permission enablement

Completed:

- [x] Read-only staging role/access audit.
- [x] Read-only production role/access audit.
- [x] Confirmed active operators had `spare_parts_access=0`.
- [x] Confirmed admin access was valid.
- [x] Confirmed unauthenticated users redirect to login.
- [x] Backed up staging DB.
- [x] Enabled `spare_parts` access for active operators on staging.
- [x] Validated staging operator access to list/new/details.
- [x] Validated staging catalog remains admin-only.
- [x] Backed up production DB.
- [x] Enabled `spare_parts` access for active operators on production.
- [x] Validated production operator access to list/new/details.
- [x] Validated production catalog remains admin-only.
- [x] Confirmed no source code changes.
- [x] Confirmed no service restart.

Pending:

- [ ] Commit this docs-only update.
- [ ] Pull docs-only update to production without service restart.
- [ ] Continue with SPARE001E controlled operator workflow test on staging.

## 2026-06-15  SPARE001F final spare parts QA closure

Completed:

- [x] Final read-only staging QA.
- [x] Final read-only production QA.
- [x] Confirmed Git sync on staging and production.
- [x] Confirmed services RUNNING on staging and production.
- [x] Confirmed active operator permissions.
- [x] Confirmed status history coverage.
- [x] Confirmed BOT003 outbox status.
- [x] Confirmed admin route access.
- [x] Confirmed operator route access.
- [x] Confirmed operator catalog remains forbidden.
- [x] Confirmed unauthenticated redirects.
- [x] Confirmed no DB writes.
- [x] Confirmed no POST requests.
- [x] Confirmed no service restart.
- [x] Closed spare parts module QA cycle for current scope.

Pending:

- [ ] Commit this docs-only update.
- [ ] Pull docs-only update to production without service restart.
- [ ] Decide next development module/stage.

## 2026-06-15  REPORT002 general `/report` validation

Completed:

- [x] Read-only staging audit of `/report`.
- [x] Source/template audit of `app.py` and `templates/report.html`.
- [x] Confirmed `REPORT002A_MARKER` is present.
- [x] Confirmed admin/operator GET access.
- [x] Confirmed filtered GET.
- [x] Confirmed CSRF token.
- [x] Confirmed Excel main export on staging.
- [x] Confirmed Excel daily activity export on staging.
- [x] Confirmed operator Excel main export on staging.
- [x] Confirmed production GET access.
- [x] Confirmed production Excel main export.
- [x] Confirmed production Excel daily activity export.
- [x] Confirmed generated `.xlsx` files are valid.
- [x] Confirmed DB counts did not change.
- [x] Confirmed no source changes were needed.
- [x] Confirmed no service restart.
- [x] Closed `/report` for current Claude-audit scope.

Pending:

- [ ] Commit this docs-only update.
- [ ] Pull docs-only update to production without service restart.
- [ ] Start UI003 general UI/design unification audit.

