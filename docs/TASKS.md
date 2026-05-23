# TASKS.md — Vehicle Soft Backlog

## Priority legend

- P0: production stability/security.
- P1: required for current business workflow.
- P2: important improvement.
- P3: future ERP expansion.

## In progress / next

(none — see backlog for upcoming work)

## Recently completed / appears completed

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
Status: **TASK-OPS-002A and TASK-OPS-002B COMPLETED 2026-05-23. TASK-OPS-002C pending operator confirmation.**

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

### TASK-DEPLOY-005 — Staging VPS deployment

Priority: P2  
Depends on: TASK-DEPLOY-002, TASK-DEPLOY-003, TASK-DEPLOY-004  
Status: **TASK-DEPLOY-005A and TASK-DEPLOY-005B completed 2026-05-23. Full deployment: planned — awaiting VPS.**

**TASK-DEPLOY-005A — VPS staging runbook (2026-05-23 — COMPLETED)**

Changes made:

- `docs/VPS_STAGING_RUNBOOK.md` created: 16-section operator runbook with exact commands.
- Documentation only. No code, database, or service changes.

Runbook covers: VPS prerequisites, Git + Python 3.14 + NSSM install, GitHub private repo clone
with PAT, SECRET_KEY + FUEL_API_TOKEN via setx /M, DB transfer from production with integrity
check, install_service.bat usage, Windows Firewall rules, Nginx reverse proxy skeleton, daily
backup Task Scheduler setup, QA smoke test checklist, Topaz staging policy, cutover plan,
rollback plan, 15 open questions for operator, and 26-step exact operator command checklist.

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

**Remaining TASK-DEPLOY-005 scope (not started — operator must rent VPS first):**

- Rent Windows Server 2022 VPS.
- Follow `docs/VPS_STAGING_RUNBOOK.md` step by step.
- Configure firewall, optional Nginx reverse proxy, HTTPS (Phase 3).
- Update Topaz agent to new server URL (after staging QA passes).
- Full QA checklist pass on VPS.
- Set up automated backups and UptimeRobot monitoring.

### TASK-DEPLOY-006 — PostgreSQL migration research

Priority: P3  
Status: planned. Not urgent — SQLite is stable at current scale.

Scope:

- Audit `models.py` for SQLite-specific constructs.
- Write and test SQLite → PostgreSQL bulk migration script.
- Verify `fuel_transactions2` (~391 K rows) migrates correctly.
- Document cutover procedure.
