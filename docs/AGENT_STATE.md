# AGENT_STATE.md — Current Project State

## State date

2026-05-23 (TASK-DEPLOY-003C completed; .gitignore root-only patterns anchored; documentation wording clarified)

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

## Current recommended next task

**TASK-DEPLOY-002 (remainder) — GitHub repository creation and first push**
`.gitignore` is created and all secret scan artifacts are redacted (003A + 003B done).
Remaining: `git init`, initial commit, create private GitHub repository, push to `main`,
tag `v1.0-production-2026-05-23`. See `docs/SECRET_SCAN_REPORT.md` Step 4 for exact commands.
Before pushing, verify with `git status` that `wialon_import_v3.py` and `PROMPT_*.md`
files do not appear in the staged file list.

TASK-OPS-002C remains open — operator must answer 5 confirmation questions in
`docs/MIGRATION_BACKFILL_ANALYSIS.md` for the LIKELY_APPLIED migration scripts.

**Recently completed**

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
