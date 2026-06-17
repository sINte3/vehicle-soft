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

## 2026-06-15  QA003 final whole-application QA

Completed:

- [x] Run final read-only whole-application QA on staging.
- [x] Run Python compile check on staging core files.
- [x] Confirm staging app import.
- [x] Confirm staging required DB tables.
- [x] Confirm staging active admin/operator/module invariants.
- [x] Confirm staging unauthenticated route smoke checks.
- [x] Confirm staging authenticated GET route render QA.
- [x] Confirm staging business route endpoint expectations.
- [x] Confirm staging DB counts unchanged.
- [x] Run final read-only whole-application QA on production.
- [x] Confirm production app import.
- [x] Confirm production required DB tables.
- [x] Confirm production active admin/operator/module invariants.
- [x] Confirm production unauthenticated route smoke checks.
- [x] Confirm production authenticated GET route render QA.
- [x] Confirm production business route endpoint expectations.
- [x] Confirm production DB counts unchanged.
- [x] Confirm no POST requests were executed.
- [x] Confirm no service restart was performed.
- [x] Confirm production services RUNNING.
- [x] Close QA003 for current Claude-audit scope.

Pending:

- [ ] Commit this docs-only update.
- [ ] Pull docs-only update to production without service restart.
- [ ] Start DOC003 final overall documentation/state closure.

## 2026-06-15  DOC003 final project state documentation

Completed:

- [x] Create final project state document.
- [x] Summarize completed closure sequence.
- [x] Record current synchronized production/staging base.
- [x] Record current module and access model.
- [x] Record current QA003-confirmed production state.
- [x] Separate non-blocking future items from current closure blockers.
- [x] Close DOC003.

Current closure sequence status:

- [x] REPORT002 completed.
- [x] UI003 completed.
- [x] QA003 completed.
- [x] DOC003 completed.

Future work should start as a new scoped task.

## 2026-06-15  FUEL-IDX-001 fuel transaction date indexes

Completed:

- [x] Run read-only staging audit for `fuel_transactions2` indexes and query plans.
- [x] Confirm active table `fuel_transactions2` was missing date indexes.
- [x] Confirm date-range query used full scan before index.
- [x] Add `ix_fuel_transactions2_txn_datetime`.
- [x] Add `ix_fuel_transactions2_station_datetime`.
- [x] Add idempotent migration `migrate_fuel_idx_001.py`.
- [x] Update `FuelTransaction2.__table_args__` in `models.py`.
- [x] Back up staging source and DB.
- [x] Stop staging services for SQLite index migration.
- [x] Apply migration on staging DB.
- [x] Restart staging services.
- [x] Validate staging indexes and query plans.
- [x] Commit source changes.
- [x] Push source changes.
- [x] Verify production pull scope.
- [x] Back up production source and DB.
- [x] Stop production services for SQLite index migration.
- [x] Pull source changes to production.
- [x] Compile production changed files.
- [x] Apply migration on production DB.
- [x] Restart production services.
- [x] Validate production indexes and query plans.
- [x] Confirm production services RUNNING.
- [x] Close FUEL-IDX-001.

Next recommended task:

- [ ] FUEL-IDX-002: replace non-sargable `date(txn_datetime)` filters with explicit datetime ranges.

## 2026-06-15 - FUEL-IDX-002 sargable fuel transaction date filters

Completed:

- [x] Run read-only staging audit for non-sargable func.date(...) filters.
- [x] Confirm affected source lines in fuel_routes.py.
- [x] Confirm old and new count comparison matches.
- [x] Confirm explicit day range uses ix_fuel_transactions2_txn_datetime.
- [x] Replace func.date(FuelTransaction2.txn_datetime) == date.today() filters.
- [x] Validate source scan has no remaining func.date(...) calls in fuel_routes.py.
- [x] Validate staging app import.
- [x] Validate staging authenticated render of key fuel pages.
- [x] Restart TransportReportStaging.
- [x] Validate post-restart staging smoke.
- [x] Commit source change.
- [x] Push source change.
- [x] Verify production pull scope.
- [x] Back up production source file.
- [x] Pull source change to production.
- [x] Compile production changed file.
- [x] Restart production web service only.
- [x] Validate production source scan, query plan and HTTP smoke.
- [x] Confirm production services RUNNING.
- [x] Close FUEL-IDX-002.

Next recommended task candidates:

- [ ] SEC-HARD-001: MAX_CONTENT_LENGTH, constant-time fuel sync token compare, 500 handler.
- [ ] CLEAN-TPL-001: remove orphaned legacy fuel templates.
- [ ] PERF-DASH-001: joinedload improvements for transport dashboard/report.

## 2026-06-15 - CLEAN-TPL-001 orphaned legacy fuel template cleanup

Completed:

- [x] Run read-only staging audit for legacy root-level fuel templates.
- [x] Confirm active templates/fuel/*.html files exist.
- [x] Confirm current render_template references use active fuel templates.
- [x] Back up legacy templates on staging.
- [x] Delete only confirmed orphaned legacy templates on staging.
- [x] Validate deleted files are gone.
- [x] Validate active fuel templates remain.
- [x] Validate no Python render_template references to deleted files.
- [x] Validate staging authenticated render of key fuel pages.
- [x] Validate staging unauthenticated smoke.
- [x] Commit source deletions.
- [x] Push source deletions.
- [x] Verify production pull scope.
- [x] Back up production legacy templates.
- [x] Pull source deletions to production.
- [x] Validate production file state.
- [x] Validate production authenticated render of key fuel pages.
- [x] Validate production unauthenticated smoke.
- [x] Confirm production services RUNNING.
- [x] Close CLEAN-TPL-001.

Next recommended task candidates:

- [ ] SEC-HARD-001: MAX_CONTENT_LENGTH, constant-time fuel sync token compare, 500 handler.
- [ ] PERF-DASH-001: joinedload improvements for transport dashboard/report.
- [ ] API-FUEL-LEGACY-001: review legacy /api/fuel_sync alias.

## 2026-06-15 - SEC-HARD-001 basic security hardening

Completed:

- [x] Run read-only staging audit for basic security hardening.
- [x] Confirm MAX_CONTENT_LENGTH was missing.
- [x] Confirm explicit 500 error handler was missing.
- [x] Confirm fuel sync token comparison used direct string comparison.
- [x] Back up staging source files.
- [x] Add MAX_CONTENT_LENGTH default of 16 MiB.
- [x] Add explicit 500 error handler.
- [x] Replace Topaz fuel sync token comparison with hmac.compare_digest.
- [x] Validate staging source scan.
- [x] Validate staging app import and runtime config.
- [x] Validate staging HTTP smoke.
- [x] Restart TransportReportStaging.
- [x] Validate staging post-restart smoke.
- [x] Commit source change.
- [x] Push source change.
- [x] Verify production pull scope.
- [x] Back up production source files.
- [x] Pull source patch to production.
- [x] Compile production changed files.
- [x] Restart production web service only.
- [x] Validate production source scan, runtime config and HTTP smoke.
- [x] Confirm production services RUNNING.
- [x] Close SEC-HARD-001.

Next recommended task candidates:

- [ ] PERF-DASH-001: joinedload improvements for transport dashboard/report.
- [ ] API-FUEL-LEGACY-001: review legacy /api/fuel_sync alias.
- [ ] CSRF-AUDIT-001: deeper CSRF coverage audit for remaining POST routes.

## 2026-06-15 - API-FUEL-LEGACY-001 fuel sync legacy alias audit

Completed:

- [x] Run read-only staging audit for /api/fuel_sync legacy alias.
- [x] Confirm canonical /fuel/api/fuel_sync route exists.
- [x] Confirm legacy /api/fuel_sync route exists.
- [x] Confirm both endpoints call _perform_fuel_sync().
- [x] Confirm CSRF exemption includes both sync paths.
- [x] Confirm hmac.compare_digest is used in shared fuel sync token check.
- [x] Confirm GET on both POST-only sync endpoints returns 405.
- [x] Confirm no source files were modified.
- [x] Confirm no DB writes were performed.
- [x] Confirm no POST requests were executed.
- [x] Decide not to remove legacy alias until Topaz config is confirmed.
- [x] Close API-FUEL-LEGACY-001 as docs-only decision.

Next recommended task candidates:

- [ ] API-FUEL-LEGACY-002: confirm Topaz agent endpoint config and plan safe alias removal.
- [ ] PERF-DASH-001: joinedload improvements for transport dashboard/report.
- [ ] CSRF-AUDIT-001: deeper CSRF coverage audit for remaining POST routes.

## 2026-06-15 - PERF-DASH-001 fuel dashboard/report query optimization

Completed:

- [x] Run read-only staging SQL audit for dashboard/report routes.
- [x] Identify high SELECT routes:
  - /
  - /fuel/
  - /fuel/report
- [x] Identify fuel_routes.py as primary patch candidate.
- [x] Implement bulk fuel dashboard/report helpers.
- [x] Optimize _collect_fuel_report_data warehouse loop.
- [x] Validate staging source.
- [x] Restart staging TransportReportStaging only.
- [x] Validate staging post-restart smoke.
- [x] Commit source patch.
- [x] Push to GitHub.
- [x] Verify production pull scope is source-only.
- [x] Create production source backup.
- [x] Pull to production.
- [x] Compile and validate production source.
- [x] Restart production TransportReport only.
- [x] Confirm Telegram bot services were not restarted.
- [x] Validate production post-restart smoke.
- [x] Close PERF-DASH-001 as completed.

Future candidates:

- [ ] PERF-SPARE-001: optimize spare parts index repeated SELECTs.
- [ ] AUDIT-GET-SIDE-EFFECT-001: review GET export routes that write audit logs.
- [ ] API-FUEL-LEGACY-002: confirm Topaz endpoint config and plan safe legacy alias removal.
- [ ] CSRF-AUDIT-001: deeper CSRF coverage audit for remaining POST routes.

## 2026-06-15 - PERF-SPARE-001 spare parts index query optimization

Completed:

- [x] Run read-only staging SQL audit for /spare-parts/.
- [x] Confirm repeated SELECT patterns.
- [x] Identify spare_parts.py as patch target.
- [x] Add eager loading for spare parts index relationships.
- [x] Replace per-status count loop with grouped aggregate query.
- [x] Validate staging source.
- [x] Restart staging TransportReportStaging only.
- [x] Validate staging post-restart smoke.
- [x] Commit source patch.
- [x] Push to GitHub.
- [x] Verify production pull scope is source-only.
- [x] Create production source backup.
- [x] Pull to production.
- [x] Compile and validate production source.
- [x] Restart production TransportReport only.
- [x] Confirm Telegram bot services were not restarted.
- [x] Validate production post-restart smoke.
- [x] Close PERF-SPARE-001 as completed.

Future candidates:

- [ ] AUDIT-GET-SIDE-EFFECT-001: review GET export routes that write audit logs.
- [ ] API-FUEL-LEGACY-002: confirm Topaz endpoint config and plan safe legacy alias removal.
- [ ] CSRF-AUDIT-001: deeper CSRF coverage audit for remaining POST routes.

## 2026-06-15 - AUDIT-GET-SIDE-EFFECT-001 Wialon GET export side effect

Completed:

- [x] Run read-only staging audit for GET routes with DML blocker.
- [x] Confirm /wialon/report/export attempted INSERT INTO audit_logs during GET.
- [x] Confirm other sampled GET routes had no DML.
- [x] Run diagnostic source dump for wialon_report_export.
- [x] Identify _audit_wialon(...) and db.session.commit() inside GET export route.
- [x] Patch wialon_import.py only.
- [x] Validate /wialon/report/export returns Excel response without DML.
- [x] Restart staging TransportReportStaging only.
- [x] Validate staging post-restart smoke.
- [x] Commit source patch.
- [x] Push to GitHub.
- [x] Verify production pull scope is source-only.
- [x] Create production source backup.
- [x] Pull to production.
- [x] Compile and validate production source.
- [x] Restart production TransportReport only.
- [x] Confirm Telegram bot services were not restarted.
- [x] Validate production post-restart smoke.
- [x] Close AUDIT-GET-SIDE-EFFECT-001 as completed.

Future candidates:

- [ ] AUDIT-GET-SIDE-EFFECT-002: expand DML-blocked audit to all GET-only export/download routes.
- [ ] CSRF-AUDIT-001: deeper CSRF coverage audit for remaining POST routes.
- [ ] API-FUEL-LEGACY-002: confirm Topaz endpoint config and plan safe legacy alias removal.

## 2026-06-15 - AUDIT-GET-SIDE-EFFECT-002 Wialon workload GET export side effect

Completed:

- [x] Run expanded read-only staging audit for GET export/download routes with DML blocker.
- [x] Confirm /wialon/workload/export attempted INSERT INTO audit_logs during GET.
- [x] Confirm sampled export/download GET routes had no DML after fixes:
  - /ref/equipment/export
  - /ref/work_types/export_diagnostics
  - /ref/customers/export_diagnostics
  - /wialon/report/export
  - /fuel/report?export=1
  - /report?export=1
- [x] Identify _audit_wialon(...) and db.session.commit() inside wialon_workload_export.
- [x] Patch wialon_import.py only.
- [x] Validate /wialon/workload/export returns Excel response without DML.
- [x] Restart staging TransportReportStaging only.
- [x] Validate staging post-restart smoke.
- [x] Commit source patch.
- [x] Push to GitHub.
- [x] Verify production pull scope is source-only.
- [x] Create production source backup.
- [x] Pull to production.
- [x] Compile and validate production source.
- [x] Restart production TransportReport only.
- [x] Confirm Telegram bot services were not restarted.
- [x] Validate production post-restart smoke.
- [x] Close AUDIT-GET-SIDE-EFFECT-002 as completed.

Future candidates:

- [ ] AUDIT-GET-SIDE-EFFECT-003: broader read-only DML audit for non-export GET routes.
- [ ] CSRF-AUDIT-001: deeper CSRF coverage audit for remaining POST routes.
- [ ] API-FUEL-LEGACY-002: confirm Topaz endpoint config and plan safe legacy alias removal.

## 2026-06-16 - AUDIT-GET-SIDE-EFFECT-003 Logout GET side effect

Completed:

- [x] Run broad read-only staging audit for GET routes without URL parameters.
- [x] Confirm /logout attempted INSERT INTO audit_logs during GET.
- [x] Confirm other audited GET routes had no DML.
- [x] Patch app.py only.
- [x] Remove logout audit write from GET /logout.
- [x] Remove db.session.commit() from GET /logout.
- [x] Preserve logout_user() and redirect behavior.
- [x] Validate /logout returns redirect without DML.
- [x] Restart staging TransportReportStaging only.
- [x] Validate staging post-restart smoke.
- [x] Commit source patch.
- [x] Push to GitHub.
- [x] Verify production pull scope is source-only.
- [x] Create production source backup.
- [x] Pull to production.
- [x] Compile and validate production source.
- [x] Restart production TransportReport only.
- [x] Confirm Telegram bot services were not restarted.
- [x] Validate production post-restart smoke.
- [x] Run corrected post-rollout DML revalidation on staging and production.
- [x] Close AUDIT-GET-SIDE-EFFECT-003 as completed.

Future candidates:

- [ ] LOGOUT-POST-001: convert logout from GET to POST with CSRF-safe UI form.
- [ ] PERF-REF-001: optimize /ref/equipment query count and response size.
- [ ] PERF-WIALON-MAP-001: reduce /wialon/mapping response size and rendering cost.
- [ ] CSRF-AUDIT-001: deeper CSRF coverage audit for remaining POST routes.

## 2026-06-16 - PERF-REF-001 Reference equipment linked counters

Completed:

- [x] Run read-only SQL audit for reference pages.
- [x] Confirm `/ref/equipment` had 1348 SELECT.
- [x] Confirm root cause: 4 per-equipment linked count queries.
- [x] Run source diagnostic for `ref_equipment`.
- [x] Confirm template did not contain `.count()` calls.
- [x] Patch `app.py` only.
- [x] Replace per-equipment `.count()` calls with grouped bulk count maps.
- [x] Preserve delete/deactivate logic.
- [x] Validate `/ref/equipment` reduced to 8 SELECT on staging.
- [x] Confirm repeated SQL count is 0 on `/ref/equipment`.
- [x] Restart staging TransportReportStaging only.
- [x] Validate staging post-restart smoke.
- [x] Commit source patch.
- [x] Push to GitHub.
- [x] Verify production pull scope is source-only.
- [x] Create production source backup.
- [x] Pull to production.
- [x] Compile and validate production source.
- [x] Restart production TransportReport only.
- [x] Confirm Telegram bot services were not restarted.
- [x] Validate production post-restart smoke.
- [x] Close PERF-REF-001 as completed.

Future candidates:

- [ ] PERF-REF-002: optimize `/ref/work_types` repeated daily_records counters.
- [ ] PERF-REF-003: optimize `/ref/organizations` per-organization counters.
- [ ] PERF-WIALON-MAP-001: reduce `/wialon/mapping` response size and rendering cost.

## 2026-06-16 - PERF-REF-002 Reference work types usage counters

Completed:

- [x] Run read-only source and SQL diagnostic for `/ref/work_types`.
- [x] Confirm `/ref/work_types` had 106 SELECT.
- [x] Confirm root cause: 104 repeated `daily_records` count queries.
- [x] Confirm source location: `app.py`, function `ref_work_types`.
- [x] Patch `app.py` only.
- [x] Replace per-work-type `.count()` calls with grouped bulk usage count map.
- [x] Reuse grouped map for missing-from-reference diagnostics.
- [x] Preserve all/used/unused filtering behavior.
- [x] Preserve template rendering.
- [x] Validate `/ref/work_types` reduced to 2 SELECT on staging.
- [x] Confirm repeated SQL count is 0 on `/ref/work_types`.
- [x] Restart staging TransportReportStaging only.
- [x] Validate staging post-restart smoke.
- [x] Commit source patch.
- [x] Push to GitHub.
- [x] Verify production pull scope is source-only.
- [x] Create production source backup.
- [x] Pull to production.
- [x] Compile and validate production source.
- [x] Restart production TransportReport only.
- [x] Confirm Telegram bot services were not restarted.
- [x] Validate production post-restart smoke.
- [x] Close PERF-REF-002 as completed.

Future candidates:

- [ ] PERF-REF-003: optimize `/ref/customers` repeated daily_records counters.
- [ ] PERF-REF-004: optimize `/ref/organizations` per-organization counters.
- [ ] PERF-WIALON-MAP-001: reduce `/wialon/mapping` response size and rendering cost.

## 2026-06-16 - PERF-REF-003 Reference customers usage counters

Completed:

- [x] Run read-only source and SQL diagnostic for `/ref/customers`.
- [x] Confirm `/ref/customers` had 11 SELECT.
- [x] Confirm root cause: 9 repeated `daily_records` count queries.
- [x] Confirm source location: `app.py`, function `ref_customers`.
- [x] Patch `app.py` only.
- [x] Replace per-customer `.count()` calls with grouped bulk usage count map.
- [x] Reuse grouped map for missing-from-reference diagnostics.
- [x] Preserve all/internal/external and all/used/unused filtering behavior.
- [x] Preserve template rendering.
- [x] Validate `/ref/customers` reduced to 2 SELECT on staging.
- [x] Confirm repeated SQL count is 0 on `/ref/customers`.
- [x] Restart staging TransportReportStaging only.
- [x] Validate staging post-restart smoke.
- [x] Commit source patch.
- [x] Push to GitHub.
- [x] Verify production pull scope is source-only.
- [x] Create production source backup.
- [x] Pull to production.
- [x] Compile and validate production source.
- [x] Restart production TransportReport only.
- [x] Confirm Telegram bot services were not restarted.
- [x] Validate production post-restart smoke.
- [x] Close PERF-REF-003 as completed.

Future candidates:

- [ ] PERF-REF-004: optimize `/ref/organizations` per-organization counters.
- [ ] PERF-WIALON-MAP-001: reduce `/wialon/mapping` response size and rendering cost.

## 2026-06-16 - PERF-REF-004 Reference organizations linked counters

Completed:

- [x] Run read-only source and SQL diagnostic for `/ref/organizations`.
- [x] Confirm `/ref/organizations` had 86 SELECT.
- [x] Confirm root cause: repeated per-organization linked count queries.
- [x] Confirm source location: `app.py`, function `ref_organizations`.
- [x] Patch `app.py` only.
- [x] Replace Equipment per-organization `.count()` calls with grouped bulk count map.
- [x] Replace FuelWarehouse per-organization `.count()` calls with grouped bulk count map.
- [x] Replace SparePartRequest per-organization `.count()` calls with grouped bulk count map.
- [x] Replace Deficiency per-organization `.count()` calls with grouped bulk count map.
- [x] Replace user relationship count with grouped `user_organizations` count map.
- [x] Preserve delete-protection logic.
- [x] Preserve statistics logic.
- [x] Preserve template rendering.
- [x] Validate `/ref/organizations` reduced to 6 SELECT on staging.
- [x] Confirm repeated SQL count is 0 on `/ref/organizations`.
- [x] Restart staging TransportReportStaging only.
- [x] Validate staging post-restart smoke.
- [x] Commit source patch.
- [x] Push to GitHub.
- [x] Verify production pull scope is source-only.
- [x] Create production source backup.
- [x] Pull to production.
- [x] Compile and validate production source.
- [x] Restart production TransportReport only.
- [x] Confirm Telegram bot services were not restarted.
- [x] Validate production post-restart smoke.
- [x] Close PERF-REF-004 as completed.

Future candidates:

- [ ] PERF-WIALON-MAP-001: reduce `/wialon/mapping` response size and rendering cost.
- [ ] PERF-REF-BODY-001: reduce heavy reference page response size where useful.

## 2026-06-16 - PERF-WIALON-MAP-001 Wialon mapping response size

Completed:

- [x] Run read-only source, SQL, and response diagnostic for `/wialon/mapping`.
- [x] Confirm response size was about 19.9 MB.
- [x] Confirm `<option>` count was 128,692.
- [x] Confirm SQL count was 20 SELECT.
- [x] Confirm repeated organization lazy-load query pattern.
- [x] Confirm source route: `wialon_import.py`, function `wialon_mapping_list`.
- [x] Confirm template: `templates/wialon_mapping_list.html`.
- [x] Patch `wialon_import.py`.
- [x] Patch `templates/wialon_mapping_list.html`.
- [x] Add eager loading for mapping equipment organization.
- [x] Add eager loading for active equipment organization.
- [x] Build shared `equipment_options` list.
- [x] Replace repeated server-rendered equipment option loops.
- [x] Populate dropdowns client-side from shared JSON payload.
- [x] Preserve save/edit/delete/skip behavior.
- [x] Validate `/wialon/mapping` response reduced to about 0.95 MB on staging.
- [x] Validate `/wialon/mapping` SQL reduced to 3 SELECT on staging.
- [x] Confirm repeated SQL count is 0.
- [x] Restart staging TransportReportStaging only.
- [x] Validate staging post-restart smoke.
- [x] Commit source patch.
- [x] Push to GitHub.
- [x] Verify production pull scope is source-only.
- [x] Create production source backup.
- [x] Pull to production.
- [x] Compile and validate production source.
- [x] Restart production TransportReport only.
- [x] Confirm Telegram bot services were not restarted.
- [x] Validate production post-restart smoke.
- [x] Close PERF-WIALON-MAP-001 as completed.

Future candidates:

- [ ] PERF-REF-BODY-001: reduce heavy reference page response size where useful.
- [ ] PERF-WIALON-AUTOMATCH-001: audit `/wialon/auto_match` response size and SQL behavior if needed.

## 2026-06-16 - PERF-REF-BODY-001 Reference equipment response size

Completed:

- [x] Run read-only source, SQL, and response diagnostic for heavy reference pages.
- [x] Confirm `/ref/equipment` is the largest remaining reference page.
- [x] Confirm `/ref/equipment` body size was about 2.5 MB.
- [x] Confirm `/ref/equipment` option count was 8,783.
- [x] Confirm SQL was already optimized at 8 SELECT.
- [x] Confirm repeated SQL count was 0.
- [x] Confirm source route: `app.py`, function `ref_equipment`.
- [x] Confirm template: `templates/ref_equipment.html`.
- [x] Patch `templates/ref_equipment.html` only.
- [x] Replace repeated edit-row organization options with shared client-side options.
- [x] Replace repeated edit-row category options with shared client-side options.
- [x] Preserve filter controls.
- [x] Preserve add form.
- [x] Preserve delete/deactivate/enable forms.
- [x] Validate `/ref/equipment` response reduced to about 1.5 MB on staging.
- [x] Validate `/ref/equipment` option count reduced to 719.
- [x] Confirm `/ref/equipment` SQL remains 8 SELECT.
- [x] Confirm repeated SQL count remains 0.
- [x] Restart staging TransportReportStaging only.
- [x] Validate staging post-restart smoke.
- [x] Commit source patch.
- [x] Push to GitHub.
- [x] Verify production pull scope is source-only.
- [x] Create production source backup.
- [x] Pull to production.
- [x] Compile and validate production source.
- [x] Restart production TransportReport only.
- [x] Confirm Telegram bot services were not restarted.
- [x] Validate production post-restart smoke.
- [x] Close PERF-REF-BODY-001 as completed.

Future candidates:

- [ ] PERF-REF-BODY-002: reduce remaining `/ref/equipment` forms/inputs by converting inline edit rows to one reusable edit modal or lazy edit row.
- [ ] PERF-WIALON-AUTOMATCH-001: audit `/wialon/auto_match` response size and SQL behavior if needed.

## 2026-06-16 - PERF-REF-BODY-002 Reference equipment inline edit rendering

Completed:

- [x] Run read-only inline edit diagnostic for `/ref/equipment`.
- [x] Confirm `/ref/equipment` still had about 1.50 MB response after PERF-REF-BODY-001.
- [x] Confirm SQL was already optimized at 8 SELECT.
- [x] Confirm repeated SQL count was 0.
- [x] Confirm 336 hidden inline edit rows.
- [x] Confirm 675 forms.
- [x] Confirm 2,762 inputs.
- [x] Confirm 676 selects.
- [x] Confirm `save_equipment` accepts the same form fields with optional `id`.
- [x] Patch `templates/ref_equipment.html` only.
- [x] Add row-level `data-*` attributes.
- [x] Replace per-row hidden edit forms with one reusable shared edit row.
- [x] Preserve `/ref/equipment/save` POST contract.
- [x] Preserve delete/deactivate/enable forms.
- [x] Preserve shared organization/category options from PERF-REF-BODY-001.
- [x] Validate `/ref/equipment` response reduced to about 0.68 MB on staging.
- [x] Validate old inline edit rows reduced to 0.
- [x] Validate shared edit row count is 1.
- [x] Validate SQL remains 8 SELECT.
- [x] Validate repeated SQL count remains 0.
- [x] Restart staging TransportReportStaging only.
- [x] Validate staging post-restart smoke.
- [x] Commit source patch.
- [x] Push to GitHub.
- [x] Verify production pull scope is source-only.
- [x] Create production source backup.
- [x] Pull to production.
- [x] Compile and validate production source.
- [x] Restart production TransportReport only.
- [x] Confirm Telegram bot services were not restarted.
- [x] Validate production post-restart smoke.
- [x] Close PERF-REF-BODY-002 as completed.

Future candidates:

- [ ] PERF-WIALON-AUTOMATCH-001: audit `/wialon/auto_match` response size and SQL behavior if needed.
- [ ] PERF-WORK-TYPES-BODY-001: optional optimization of `/ref/work_types` inline edit rows if worth the risk.

## 2026-06-16 - PERF-WIALON-WORKLOAD-001 Wialon workload bulk equipment loading

Completed:

- [x] Run read-only Wialon route/source/response diagnostic.
- [x] Confirm `/wialon/auto_match` is not a performance problem.
- [x] Identify real SQL issue in `/wialon/workload`.
- [x] Identify same SQL issue in `/wialon/workload/export`.
- [x] Confirm repeated equipment SQL query count was 17.
- [x] Confirm `/wialon/workload` baseline SQL count was 21 SELECT.
- [x] Confirm `/wialon/workload/export` baseline SQL count was 20 SELECT.
- [x] Patch `workload_report.py`.
- [x] Patch `wialon_import.py`.
- [x] Add `preloaded_orgs` argument to `get_workload_data`.
- [x] Reuse preloaded organizations from `/wialon/workload`.
- [x] Replace per-organization equipment loading with one bulk equipment query.
- [x] Preserve mapped-equipment filtering.
- [x] Preserve export compatibility.
- [x] Validate `/wialon/workload` reduced to 4 SELECT on staging.
- [x] Validate `/wialon/workload/export` reduced to 4 SELECT on staging.
- [x] Validate repeated equipment SQL eliminated on staging.
- [x] Restart staging TransportReportStaging only.
- [x] Validate staging post-restart smoke.
- [x] Commit source patch.
- [x] Push to GitHub.
- [x] Verify production pull scope is source-only.
- [x] Create production source backup.
- [x] Pull to production.
- [x] Compile and validate production source.
- [x] Restart production TransportReport only.
- [x] Confirm Telegram bot services were not restarted.
- [x] Validate production post-restart smoke.
- [x] Close PERF-WIALON-WORKLOAD-001 as completed.

Future candidates:

- [ ] PERF-WIALON-MAPPING-BODY-002: optional reduction of remaining `/wialon/mapping` forms/inputs if worth the UX risk.
- [ ] PERF-WORK-TYPES-BODY-001: optional optimization of `/ref/work_types` inline edit rows if worth the risk.

## 2026-06-16 - PERF-WIALON-MAPPING-BODY-002 Wialon mapping shared forms

Completed:

- [x] Run read-only feasibility diagnostic for `/wialon/mapping`.
- [x] Confirm SQL already optimized at 3 SELECT.
- [x] Confirm repeated SQL count 0.
- [x] Identify HTML body issue caused by repeated per-row forms.
- [x] Confirm baseline body size 947,349 bytes.
- [x] Confirm baseline forms 763.
- [x] Confirm baseline inputs 1,909.
- [x] Confirm baseline selects 384.
- [x] Confirm baseline edit save forms 379.
- [x] Patch `templates/wialon_mapping_list.html`.
- [x] Replace repeated edit forms with shared edit form.
- [x] Replace repeated delete forms with shared delete form.
- [x] Preserve pending forms.
- [x] Preserve manual add form.
- [x] Preserve existing POST contracts.
- [x] Remove heavy rendered `data-search`.
- [x] Remove repeated rendered `data-delete-url`.
- [x] Add shared delete URL template.
- [x] Validate body size reduced to 633,834 bytes on staging.
- [x] Validate forms reduced to 7 on staging.
- [x] Validate inputs reduced to 18 on staging.
- [x] Validate selects reduced to 6 on staging.
- [x] Validate old edit forms removed on staging.
- [x] Restart staging TransportReportStaging only.
- [x] Validate staging post-restart smoke.
- [x] Commit source patch.
- [x] Push to GitHub.
- [x] Verify production pull scope is template-only.
- [x] Create production source backup.
- [x] Pull to production.
- [x] Compile and validate production source.
- [x] Restart production TransportReport only.
- [x] Confirm Telegram bot services were not restarted.
- [x] Validate production post-restart smoke.
- [x] Close PERF-WIALON-MAPPING-BODY-002 as completed.

Future candidates:

- [ ] PERF-WIALON-MAPPING-PAGINATION-001: optional pagination/lazy loading for `/wialon/mapping` if the table keeps growing.
- [ ] PERF-WORK-TYPES-BODY-001: optional optimization of `/ref/work_types` inline edit rows if worth the risk.

## 2026-06-16 - PERF-WORK-TYPES-BODY-001 Reference work types shared forms

Completed:

- [x] Run read-only feasibility diagnostic for `/ref/work_types`.
- [x] Confirm SQL already optimized at 2 SELECT.
- [x] Confirm repeated SQL count 0.
- [x] Identify HTML body issue caused by repeated inline edit/delete forms.
- [x] Confirm baseline body size 266,902 bytes.
- [x] Confirm baseline forms 111.
- [x] Confirm baseline inputs 530.
- [x] Confirm baseline CSRF inputs 110.
- [x] Patch `templates/ref_work_types.html`.
- [x] Replace repeated inline edit rows with shared edit row.
- [x] Replace repeated delete forms with shared delete form.
- [x] Preserve filter form.
- [x] Preserve add-new-work-type form.
- [x] Preserve existing POST contracts.
- [x] Validate body size reduced to about 127 KB on staging.
- [x] Validate forms reduced to 5 on staging.
- [x] Validate inputs reduced to 12 on staging.
- [x] Validate CSRF inputs reduced to 4 on staging.
- [x] Validate old inline edit rows removed on staging.
- [x] Restart staging TransportReportStaging only.
- [x] Validate staging post-restart smoke.
- [x] Commit source patch.
- [x] Push to GitHub.
- [x] Verify production pull scope is template-only.
- [x] Create production source backup.
- [x] Pull to production.
- [x] Compile and validate production source.
- [x] Restart production TransportReport only.
- [x] Confirm Telegram bot services were not restarted.
- [x] Validate production post-restart smoke.
- [x] Close PERF-WORK-TYPES-BODY-001 as completed.

Future candidates:

- [ ] PERF-REF-EQUIPMENT-BODY-003: optional further reduction of `/ref/equipment` remaining forms/actions if worth the UX risk.
- [ ] PERF-FUEL-DASH-REPEAT-001: optional investigation of remaining repeated query on `/fuel/`.

## 2026-06-16 - PERF-FUEL-STATIONS-NPLUS1-001 Fuel stations transaction counts optimization

Completed:

- [x] Run read-only fuel SQL diagnostic.
- [x] Confirm `/fuel/` repeated SQL count is 0.
- [x] Identify `/fuel/stations` as stronger N+1 candidate.
- [x] Confirm baseline `/fuel/stations` SQL count 44.
- [x] Confirm baseline repeated SQL count 2.
- [x] Confirm repeated transaction count queries 21 + 21.
- [x] Patch `fuel_routes.py`.
- [x] Add bulk transaction count query grouped by station ID.
- [x] Reuse preloaded counts inside `station_delete_info`.
- [x] Patch `templates/fuel/stations.html`.
- [x] Replace `st.transactions.count()` with preloaded transaction count.
- [x] Validate `/fuel/stations` reduced to 3 SELECT on staging.
- [x] Validate repeated SQL reduced to 0 on staging.
- [x] Validate DML count 0 on staging.
- [x] Validate no traceback on staging.
- [x] Restart staging TransportReportStaging only.
- [x] Validate staging post-restart smoke.
- [x] Commit source patch.
- [x] Push to GitHub.
- [x] Verify production pull scope is source-only.
- [x] Create production source backup.
- [x] Pull to production.
- [x] Compile and validate production source.
- [x] Restart production TransportReport only.
- [x] Confirm Telegram bot services were not restarted.
- [x] Validate production post-restart smoke.
- [x] Close PERF-FUEL-STATIONS-NPLUS1-001 as completed.

Future candidates:

- [ ] PERF-FUEL-WAREHOUSES-NPLUS1-001: optimize `/fuel/warehouses` repeated counts and lazy-loads.
- [ ] PERF-FUEL-TRANSACTIONS-NPLUS1-001: optimize `/fuel/transactions` station lazy-loads if repeat persists.
- [ ] PERF-FUEL-REPORT-REPEAT-001: review duplicate warehouse query in `/fuel/report`.

<!-- fuel-batch-perf-001d -->

### fuel-batch-perf-001 - fuel warehouse and initial balance performance

priority: p1
status: **completed 2026-06-16**

summary:

- optimized `/fuel/warehouses`.
- optimized `/fuel/initial-balance`.
- code commit: `c4fd7d16b981bc1406aa65a6a9d48d23027bb6c0`.
- `/fuel/warehouses`: 73 select -> 6 select.
- `/fuel/warehouses`: repeated sql 6 -> 0.
- `/fuel/initial-balance`: 11 select -> 2 select.
- `/fuel/initial-balance`: repeated sql 1 -> 0.
- staging validation passed.
- production validation passed.
- production post-restart smoke passed.
- only `transportreport` was restarted during code rollout.
- telegram bot services were not restarted.
- no db writes during get validation.
- no post during validation.
- production backup before rollout: `d:\transport-report-backups\production\source\fuel_batch_perf_001c_639172312812084107`.

notes:

- commit message in code commit contains a copy typo: `ptimize fuel warehouse loading`.
- the commit is valid and must not be amended.
- this entry is docs-only closure for fuel-batch-perf-001d.
<!-- perf-fuel-transactions-nplus1-001d -->

## PERF-FUEL-TRANSACTIONS-NPLUS1-001  `/fuel/transactions` station lazy-load optimization

Status: DONE  deployed to staging and production on 2026-06-16.

Scope:
- Optimized `/fuel/transactions`.
- Changed only `fuel_routes.py`.
- Added eager loading for `FuelTransaction2.station` and related `FuelStation2.warehouse`.
- Removed template-triggered lazy loading caused by `txn.station.name` and `txn.station.warehouse_name`.

Validation:
- Staging authenticated GET `/fuel/transactions`: status 200, SQL total 6, repeated SQL kinds 0, station lazy repeated total 0, warehouse lazy repeated total 0, non-select statements 0.
- Production authenticated GET `/fuel/transactions`: status 200, SQL total 6, repeated SQL kinds 0, station lazy repeated total 0, warehouse lazy repeated total 0, non-select statements 0.
- Flask smoke after restart: `/`, `/fuel/transactions`, `/fuel/stations`, `/fuel/warehouses` return expected 302 to login when unauthenticated.
- HTTP smoke: `/` and `/fuel/transactions` return expected 302 when unauthenticated.

Deployment:
- Commit: `7f928c0 optimize fuel transactions station loading`.
- Production backup: `d:\transport-report-backups\production\source\fuel_transactions_nplus1_001_before_20260616_190905_a6bd954.zip`.
- Restarted only `transportreport` on production.
- Telegram bot services were not restarted.
<!-- perf-fuel-report-warehouse-query-001d -->

## PERF-FUEL-REPORT-WAREHOUSE-QUERY-001  `/fuel/report` duplicate warehouse query cleanup

Status: DONE  deployed to staging and production on 2026-06-16.

Scope:
- Optimized `/fuel/report`.
- Changed only `fuel_routes.py`.
- Reused warehouses already loaded by `_collect_fuel_report_data()`.
- Removed duplicate `FuelWarehouse.query.order_by(FuelWarehouse.name).all()` in the report route.

Validation:
- Before: `/fuel/report` had SQL total 22, repeated SQL kinds 1, duplicate ordered warehouse query count 2.
- After staging: status 200, SQL total 21, repeated SQL kinds 0, warehouse ordered queries 1, non-select statements 0.
- After production: status 200, SQL total 21, repeated SQL kinds 0, warehouse ordered queries 1, non-select statements 0.
- Flask smoke after restart: `/`, `/fuel/report`, `/fuel/transactions`, `/fuel/receipts` return expected 302 to login when unauthenticated.
- HTTP smoke: `/` and `/fuel/report` return expected 302 when unauthenticated.

Deployment:
- Commit: `6e6237b optimize fuel report warehouse loading`.
- Production backup: `d:\transport-report-backups\production\source\fuel_report_warehouse_query_001_before_20260616_192639_d7961d8.zip`.
- Restarted only `transportreport` on production.
- Telegram bot services were not restarted.
<!-- perf-fuel-get-routes-sweep-001d -->

## PERF-FUEL-GET-ROUTES-SWEEP-001  fuel GET routes N+1 sweep

Status: DONE  no code changes required for the remaining fuel GET routes.

Scope:
- Inventoried all fuel GET routes.
- Verified the remaining read-only fuel pages after prior optimizations.
- No POST routes were executed.
- No service restart was required.

Fuel GET routes found:
- `/fuel/`
- `/fuel/api/fuel_ping`
- `/fuel/initial-balance`
- `/fuel/receipts`
- `/fuel/report`
- `/fuel/stations`
- `/fuel/transactions`
- `/fuel/warehouses`
- `/fuel/warnings`

Verification:
- `/fuel/warnings`: status 200, SQL total 21, repeated SQL kinds 0, lazy-load repeated totals 0, non-select statements 0.
- `/fuel/`: status 200, SQL total 14, repeated SQL kinds 0, lazy-load repeated totals 0, non-select statements 0.
- `/fuel/receipts`: status 200, SQL total 5, repeated SQL kinds 0, lazy-load repeated totals 0, non-select statements 0.
- `/fuel/api/fuel_ping`: technical ping route, no template and no N+1 risk.
- `/fuel/report`, `/fuel/transactions`, `/fuel/warehouses`, `/fuel/initial-balance`, `/fuel/stations` were already validated in prior performance tasks.

Conclusion:
- No remaining fuel GET N+1 issue found.
- Fuel GET route performance sweep is closed.
<!-- perf-index-fuel-sync-dup-001d -->

## PERF-INDEX-FUEL-SYNC-DUP-001  main dashboard FuelSyncLog duplicate query

Status: DONE  deployed to staging and production.

Scope:
- Main route: `/`
- Files changed:
  - `app.py`
  - `fuel_routes.py`

Problem:
- The main dashboard `/` called `_collect_fuel_report_data(d_from, d_to)`.
- `_collect_fuel_report_data()` already loaded latest Topaz sync data.
- `app.py` then queried `FuelSyncLog2.query.order_by(...).first()` again.
- Result: duplicated `fuel_sync_logs2` latest-sync query on `/`.

Fix:
- `_collect_fuel_report_data()` now exposes already-loaded `latest_sync` in its returned data.
- `_build_dashboard_context()` now reuses `fuel_report['latest_sync']`.
- Fallback direct query remains only if the fuel report collector fails.

Verification:
- Staging before fix: `/` had repeated SQL kind 1 and 3 `fuel_sync_logs2` queries.
- Staging after fix: `/` status 200, SQL total 30, repeated SQL kinds 0, `fuel_sync_logs2` query count 2, non-select statements 0.
- Production after deployment: `/` status 200, SQL total 31, repeated SQL kinds 0, `fuel_sync_logs2` query count 2, non-select statements 0.

Deployment:
- Code commit: `f00b386 optimize index fuel sync loading`
- Staging: `f00b386`
- Production: `f00b386`
- Production backup:
  - `d:\transport-report-backups\production\source\index_fuel_sync_dup_001_git_archive_before_20260616_200045_98ca314.zip`

Operational notes:
- `transportreportstaging` restarted after staging validation.
- `transportreport` restarted after production deployment.
- Bot services were not restarted.
<!-- perf-core-get-routes-sweep-001d -->

## PERF-CORE-GET-ROUTES-SWEEP-001  core GET routes N+1/performance sweep

Status: DONE  core transport pages verified.

Scope:
- `/`
- `/entry`
- `/deficiencies`
- `/report`

Results:
- `/entry`: clean, SQL total 5, repeated SQL kinds 0, non-select statements 0.
- `/deficiencies`: clean, SQL total 5, repeated SQL kinds 0, non-select statements 0.
- `/report`: clean, SQL total 5, repeated SQL kinds 0, non-select statements 0.
- `/`: initially had 1 repeated SQL kind caused by duplicate latest Topaz sync lookup. Fixed under `PERF-INDEX-FUEL-SYNC-DUP-001`.

Final `/` verification after fix:
- Staging: status 200, SQL total 30, repeated SQL kinds 0, `fuel_sync_logs2` query count 2, non-select statements 0.
- Production: status 200, SQL total 31, repeated SQL kinds 0, `fuel_sync_logs2` query count 2, non-select statements 0.

Related code commit:
- `f00b386 optimize index fuel sync loading`

Conclusion:
- Core GET routes sweep is closed.
- No remaining N+1 issue found in core transport pages.
<!-- perf-wialon-get-routes-sweep-001d -->

## PERF-WIALON-GET-ROUTES-SWEEP-001  remaining Wialon GET routes performance sweep

Status: DONE  Wialon remaining GET pages verified.

Scope:
- `/wialon`
- `/wialon/auto_match`
- `/wialon/report`

Results:
- `/wialon`: clean, repeated SQL kinds 0, non-select statements 0.
- `/wialon/auto_match`: clean, repeated SQL kinds 0, non-select statements 0.
- `/wialon/report`: clean, repeated SQL kinds 0, non-select statements 0.

Staging verification:
- `/wialon`: status 200, SQL total 6, repeated SQL kinds 0, non-select statements 0.
- `/wialon/auto_match`: status 200, SQL total 5, repeated SQL kinds 0, non-select statements 0.
- `/wialon/report`: status 200, SQL total 5, repeated SQL kinds 0, non-select statements 0.

Production verification:
- `/wialon`: status 200, SQL total 6, repeated SQL kinds 0, non-select statements 0.
- `/wialon/auto_match`: status 200, SQL total 5, repeated SQL kinds 0, non-select statements 0.
- `/wialon/report`: status 200, SQL total 5, repeated SQL kinds 0, non-select statements 0.

Conclusion:
- No code changes required.
- No N+1 issue found in the remaining Wialon GET pages.
<!-- perf-spare-parts-get-routes-sweep-001d -->

## PERF-SPARE-PARTS-GET-ROUTES-SWEEP-001  remaining spare parts GET routes performance sweep

Status: DONE  remaining spare parts GET pages verified.

Scope:
- `/spare-parts/catalog`
- `/spare-parts/new`
- `/spare-parts/<id>`

Results:
- `/spare-parts/catalog`: clean, repeated SQL kinds 0, non-select statements 0.
- `/spare-parts/new`: clean, repeated SQL kinds 0, non-select statements 0.
- `/spare-parts/<id>`: clean on sampled latest request records, repeated SQL kinds 0, non-select statements 0.

Staging verification:
- `/spare-parts/catalog`: status 200, SQL total 4, repeated SQL kinds 0, non-select statements 0.
- `/spare-parts/new`: status 200, SQL total 4, repeated SQL kinds 0, non-select statements 0.
- `/spare-parts/12`: status 200, SQL total 7, repeated SQL kinds 0, non-select statements 0.

Production verification:
- `/spare-parts/catalog`: status 200, SQL total 4, repeated SQL kinds 0, non-select statements 0.
- `/spare-parts/new`: status 200, SQL total 4, repeated SQL kinds 0, non-select statements 0.
- `/spare-parts/3`: status 200, SQL total 6, repeated SQL kinds 0, non-select statements 0.

Conclusion:
- No code changes required.
- No N+1 issue found in remaining spare parts GET pages.
<!-- perf-admin-users-orgs-nplus1-001d -->

## PERF-ADMIN-USERS-ORGS-NPLUS1-001  `/admin/users` organizations N+1 optimization

Status: DONE  deployed to staging and production.

Scope:
- `/admin/users`
- File changed:
  - `app.py`

Problem:
- `/admin/users` loaded users with `User.query.order_by(User.username).all()`.
- The template accessed each user's organizations.
- This caused repeated lazy-load queries through `user_organizations`.

Before optimization:
- Staging `/admin/users`: SQL total 12, repeated SQL kinds 1, organization repeated total 7.
- Production `/admin/users`: SQL total 12, repeated SQL kinds 1, organization repeated total 7.

Fix:
- Added `selectinload(User.organizations)` to the `/admin/users` query.
- Added import:
  - `from sqlalchemy.orm import selectinload`

After optimization:
- Staging `/admin/users`: status 200, SQL total 6, repeated SQL kinds 0, organization repeated total 0, `user_organizations` query count 1, non-select statements 0.
- Production `/admin/users`: diagnostics passed with repeated SQL kinds 0 and non-select statements 0.
- `/admin/permissions`: regression check passed, repeated SQL kinds 0.
- `/admin/audit`: regression check passed, repeated SQL kinds 0.

Deployment:
- Code commit: `2216514 optimize admin users organization loading`
- Staging: `2216514`
- Production: `2216514`

Operational notes:
- `transportreportstaging` restarted after validation.
- `transportreport` restarted after production deployment.
- Bot services were not restarted.
<!-- final-global-get-routes-control-001d -->

## FINAL-GLOBAL-GET-ROUTES-CONTROL-001  final GET route and service control

Status: DONE  staging and production passed final read-only control.

Scope:
- Final global GET route verification after performance/security cleanup.
- Staging and production comparison.
- Service state verification.

Repository state:
- Staging: `9f31685`
- Production: `9f31685`
- Origin/main: `9f31685`

Routes checked:
- Core:
  - `/`
  - `/entry`
  - `/deficiencies`
  - `/report`
- Fuel:
  - `/fuel/`
  - `/fuel/api/fuel_ping`
  - `/fuel/initial-balance`
  - `/fuel/receipts`
  - `/fuel/report`
  - `/fuel/stations`
  - `/fuel/transactions`
  - `/fuel/warehouses`
  - `/fuel/warnings`
- Spare parts:
  - `/spare-parts/`
  - `/spare-parts/catalog`
  - `/spare-parts/new`
  - latest request detail route
- Wialon:
  - `/wialon`
  - `/wialon/auto_match`
  - `/wialon/report`
  - `/wialon/workload`
  - `/wialon/mapping`
- Admin:
  - `/admin/users`
  - `/admin/permissions`
  - `/admin/audit`
- References:
  - `/ref/equipment`
  - `/ref/work_types`
  - `/ref/customers`
  - `/ref/organizations`

Final result:
- Staging final diagnostics exit code: 0
- Production final diagnostics exit code: 0
- Staging bad route count: 0
- Production bad route count: 0
- All checked authenticated GET routes returned status 200.
- All checked routes had repeated SQL kinds 0.
- All checked routes had non-select statements 0.
- Services were checked and remained RUNNING.
- No commit, pull, POST, or service restart was performed during final read-only diagnostics.

Services verified RUNNING:
- `transportreport`
- `transportreportstaging`
- `transportbot`
- `transportbot003`
- `transportbotstaging`
- `transportbot003staging`

<!-- csrf-audit-001d -->
## 2026-06-17 - CSRF-AUDIT-001 CSRF coverage audit

Status: completed as read-only security audit.

Completed:

- [x] Confirmed baseline 255a904.
- [x] Confirmed working tree was clean before audit.
- [x] Confirmed global CSRF token helper exists.
- [x] Confirmed global before_request CSRF enforcement exists.
- [x] Confirmed CSRF comparison uses hmac.compare_digest.
- [x] Inventoried Python POST routes.
- [x] Found 46 POST routes.
- [x] Inventoried HTML POST forms.
- [x] Found 52 POST forms.
- [x] Confirmed 52 of 52 browser POST forms contain CSRF marker.
- [x] Confirmed 0 browser POST forms without CSRF marker.
- [x] Reviewed CSRF-exempt paths.
- [x] Confirmed fuel sync API endpoints are protected by FUEL_API_TOKEN.
- [x] Confirmed bot API endpoints use Bearer token or one-time link-code logic.
- [x] Confirmed potential risk item count was 0.
- [x] Confirmed no code changes were required.
- [x] Confirmed no DB changes, migrations, production pull, or service restart were performed.

Result:

- CSRF-AUDIT-001 PASS.
- Code changes required: no.
- Production changes required: no.
- Service restart required: no.

Future rule:

- Any future browser POST form must include csrf_token.
- Any future /api/bot/* POST route must include explicit API authentication or one-time-code validation because /api/bot/* is CSRF-exempt by design.
