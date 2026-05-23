# SECRET_SCAN_REPORT.md — Pre-Push Secret and Hygiene Scan

Task IDs: TASK-DEPLOY-002A, TASK-DEPLOY-003A, TASK-DEPLOY-003B  
Completed: 2026-05-23  
Scanned by: Claude Code (automated read-only grep; no live secret values logged)

---

## TASK-DEPLOY-003C — .gitignore root-only pattern anchoring (2026-05-23)

TASK-DEPLOY-003B left several `.gitignore` patterns unanchored. Without a leading `/`,
Git applies them recursively and may exclude files with the same name inside subdirectories
such as `templates/`. TASK-DEPLOY-003C anchored all root-only patterns.

**What was done:**

- `.gitignore`: six filename patterns given a leading `/` to restrict matching to root only:
  `/wialon.html`, `/wialon_auto_match.html`, `/wialon_report_v2.html`,
  `/Agroklastr_Tehnika_Konsolidaciya.xlsx`, `/Агрокластер_Техника_Консолидация.xlsx`,
  `/wialon_import_v3.py`.
- `.gitignore` comment for `wialon_import_v3.py` updated: no longer mentions the
  redacted placeholder by name; refers to `docs/SECRET_SCAN_REPORT.md` instead.
- Documentation wording in `docs/AGENT_STATE.md`, `docs/TASKS.md`, and this file updated:
  references to `fuel_routes.py removed API_TOKEN = '<REDACTED_LEGACY_FUEL_API_TOKEN>'`
  replaced with plain language; blocking finding section clarified that the placeholder
  is not the actual token value.
- `templates/wialon.html` and `templates/wialon_auto_match.html` are now correctly NOT
  excluded — only root-level orphaned copies are excluded.
- No application code changed. No database changed. No service restarted.

**Status: SAFE to create a private GitHub repository and push.**

---

## TASK-DEPLOY-003B — Post-scan redaction (2026-05-23)

TASK-DEPLOY-003A correctly identified and excluded `wialon_import_v3.py` via `.gitignore`,
but wrote the literal token value into documentation comments. TASK-DEPLOY-003B redacted all
occurrences from commit-eligible files.

**What was done:**

- Literal legacy token value replaced with `<REDACTED_LEGACY_FUEL_API_TOKEN>` placeholder in all
  commit-eligible markdown and documentation files (`.gitignore`, this report, `docs/AGENT_STATE.md`,
  `docs/TASKS.md`, `AUDIT_REPORT.md`).
- `PROMPT_*.md` added to `.gitignore` (root-level only, via `/PROMPT_*.md` pattern) to exclude
  Claude/ChatGPT handoff prompt files from version control. `docs/PROMPT_PROTOCOL.md` is unaffected.
- No application code changed. No database changed. No service restarted.

**Final status: SAFE to create a private GitHub repository and push only after verifying `git status`.**

---

## Summary

A manual source-code scan was performed across all `.py`, `.bat`, `.html`, `.js`, `.css`,
and `.md` files in `C:\transport-report\` before the first GitHub push. One legacy file
contained a hardcoded API token from before TASK-SEC-002. That file (`wialon_import_v3.py`)
has been excluded from version control via `.gitignore`. The literal token value has been
redacted from all documentation (TASK-DEPLOY-003B). No other blocking secrets were found
in files that would be committed.

**Final verdict: SAFE to create private GitHub repository and push after applying `.gitignore`.**

---

## Files scanned

| File group | Count | Tool |
|---|---|---|
| `*.py` (project root) | ~40 files | Grep (case-insensitive, line-number mode) |
| `*.bat` | 2 files (`install_service.bat`, `start.bat`) | Grep + Read |
| `*.md` (docs and root) | ~25 files | Grep |
| `*.js` | static/ folder | Grep (no matches) |
| `*.css` | static/ folder | Grep (no matches) |
| `.env.example` | 1 file | Read |
| `config.py` | 1 file | Read |
| `run_server.py` | 1 file | Read |
| `fuel_routes.py` | 1 file | Read (first 80 lines + token logic) |
| `init_data.py` | 1 file | Read |
| `install_service.bat` | 1 file | Read |
| `start.bat` | 1 file | Read |
| `wialon_import.py` | header lines | Grep |
| `wialon_import_v3.py` | full token lines | Grep + Read |

Patterns searched: `SECRET_KEY`, `FUEL_API_TOKEN`, `API_TOKEN`, `password`, `passwd`,
`token`, `secret`, `changeme`, `admin123`, Topaz credentials, Wialon credentials,
connection strings, hardcoded IPs, database paths.

---

## Findings table

| # | File | Line | Pattern | Value (redacted) | Severity | Status |
|---|---|---|---|---|---|---|
| 1 | `wialon_import_v3.py` | 674 | hardcoded token | `<REDACTED_LEGACY_FUEL_API_TOKEN>` | **BLOCKING** | Resolved — file excluded by `.gitignore` |
| 2 | `config.py` | 46 | `PG_PASS` default | `'changeme'` | Low | Documented — not active config |
| 3 | `config.py` | 33 | `SECRET_KEY` dev fallback | `'dev-only-insecure-key-...'` | Low | Acceptable — clearly named dev-only |
| 4 | `init_data.py` | 28 | seed password | `'admin123'` | Low | Known default — must change post-install |
| 5 | `start.bat` | 28 | echo password | `admin123` | Low | Informational echo only — must change post-install |
| 6 | `install_service.bat` | 83 | echo password | `admin123` | Low | Informational echo only — must change post-install |
| 7 | `wialon_import.py` | 7 | LAN IP | `10.103.40.140` (docstring) | Info | Private LAN IP in comment — acceptable in private repo |
| 8 | `wialon_import_v3.py` | 7 | LAN IP | `10.103.40.140` (docstring) | Info | File excluded from repo by `.gitignore` |
| 9 | `migrate_to_v45.py` | 7 | LAN IP | `10.103.25.200` (comment) | Info | Private LAN IP in comment — acceptable in private repo |
| 10 | `migrate.py` | 5–6 | DB paths | `C:\transport-report\...` | Info | Historical one-time script — Windows path in comment, expected |
| 11 | `config.py` | 13 | `SECRET_KEY` | none — `os.environ.get(...)` | Safe | Environment-backed only (TASK-SEC-002) |
| 12 | `config.py` | 17 | `FUEL_API_TOKEN` | none — `os.environ.get(...)` | Safe | Environment-backed only (TASK-SEC-002) |
| 13 | `fuel_routes.py` | 476–477 | token validation | reads from `current_app.config` | Safe | No hardcoded token (TASK-SEC-002) |
| 14 | `run_server.py` | 19–22 | `SECRET_KEY` check | exits if missing | Safe | Fail-fast guard, no actual value |
| 15 | `.env.example` | 10, 14 | placeholder values | `change-me-to-...` | Safe | Template only, no real secrets |

---

## Safe and expected findings

### Finding 3 — `DevelopmentConfig.SECRET_KEY` dev fallback (config.py:33)

```python
SECRET_KEY = os.environ.get('SECRET_KEY', 'dev-only-insecure-key-do-not-use-in-production')
```

**Why acceptable**: The long name makes intent explicit. This value is only active when
`FLASK_ENV=dev`. Production uses `SqliteProductionConfig` which inherits the base
`Config.SECRET_KEY = os.environ.get('SECRET_KEY')` with no fallback. `run_server.py`
exits immediately if `SECRET_KEY` is missing.

### Finding 4, 5, 6 — `admin123` in seed script and `.bat` echo lines

`init_data.py` seeds the initial admin user with password `admin123` and prints it to
the console. `start.bat` and `install_service.bat` echo the same for operator guidance.

**Why acceptable**: This is a post-install default, not a production secret. The pattern
is identical to most open-source CMS and ERP installers. The `.bat` files contain only
an `echo` statement — not a live credential.

**Required operator action**: Change the admin password immediately after every fresh install.
See exact steps under "Operator next steps" below.

### Finding 2 — `ProductionConfig.PG_PASS = 'changeme'` (config.py:46)

```python
PG_PASS = os.environ.get('PG_PASS', 'changeme')
```

**Why not currently blocking**: The active production config is `SqliteProductionConfig`,
selected by `FLASK_ENV=sqlite_prod` in `run_server.py`. `ProductionConfig` (PostgreSQL)
is never instantiated on the current production server.

**Future risk**: When `FLASK_ENV=prod` is activated (Phase 4 PostgreSQL migration),
this fallback becomes active. The operator MUST remove it and set `PG_PASS` via `setx /M`
before enabling `prod` config. Documented in `docs/DEPLOYMENT_PLAN.md` Section 7.

### Findings 7, 9 — Private LAN IP addresses in comments

`wialon_import.py:7` — `10.103.40.140` — Topaz agent source IP, docstring only.
`migrate_to_v45.py:7` — `10.103.25.200` — Production server IP, comment only.

**Why acceptable**: Both are internal LAN addresses in non-executable comments. They
carry no authentication material. For a private GitHub repository this is acceptable.
If the repo is ever made public, these should be replaced with placeholder values.

---

## Blocking finding — RESOLVED by `.gitignore`

### Finding 1 — `wialon_import_v3.py:674` — Hardcoded API token

**Original code found**:
```python
if token != app.config.get('TOPAZ_AGENT_TOKEN', '<REDACTED_LEGACY_FUEL_API_TOKEN>'):
```

**Why blocking**: `wialon_import_v3.py` contained a real hardcoded API token from a prior
version of the application (before TASK-SEC-002). The real token value was redacted and
is not present in this report — `<REDACTED_LEGACY_FUEL_API_TOKEN>` is a placeholder only.
Even though the active module `wialon_import.py` no longer uses it (TASK-SEC-002 removed
the hardcoded token from `fuel_routes.py`), the old backup file `wialon_import_v3.py`
retains the original. If committed to GitHub, the actual token would be permanently
visible in git history.

**Resolution**: `wialon_import_v3.py` was added to `.gitignore` with an explanatory
comment. The file is NOT a part of the active application — the active module is
`wialon_import.py`. The old backup file will never be committed.

**Operator verification**: After `git init`, run `git status`. Confirm that
`wialon_import_v3.py` does NOT appear in the output.

---

## `.gitignore` verification

### Created: `C:\transport-report\.gitignore`

The file was created from the baseline in `docs/DEPLOYMENT_PLAN.md` Section 3 with
the following additions identified during project inspection:

| Addition | Reason |
|---|---|
| `.claude/` | Claude Code session and memory directory — not project source |
| `migration_log_*.csv` | Log output from `migrate_001_backfill_historical_registry.py` |
| `fix_names_log_*.csv` | Log output from `fix_eq_names.py` |
| `patch2_log_*.csv` | Log output from patch scripts |
| `/Agroklastr_Tehnika_Konsolidaciya.xlsx` | Binary reference import file in root — historical, not source (root-only) |
| `/Агрокластер_Техника_Консолидация.xlsx` | Same, Cyrillic form (root-only) |
| `/wialon_import_v3.py` | Old backup with hardcoded API token (BLOCKING finding resolved here; root-only) |
| `/wialon.html` | Orphaned root-level copy; active templates are in `templates/` (root-only) |
| `/wialon_auto_match.html` | Same (root-only) |
| `/wialon_report_v2.html` | Same (root-only) |

### Coverage verification

| Path | Covered? | Pattern |
|---|---|---|
| `instance/transport.db` | Yes | `instance/transport.db` |
| `instance/transport.db.backup` | Yes | `instance/transport.db.backup*` |
| `instance/transport.db.backup_before_ops001` | Yes | `instance/transport.db.backup*` |
| `instance/transport.db.backup_before_ops002b` | Yes | `instance/transport.db.backup*` |
| `instance/transport.db.backup_v2` | Yes | `instance/transport.db.backup*` |
| `instance/old_transport.db` | Yes | `instance/old_transport.db` |
| `old_transport.db` (root) | Yes | `old_transport.db` |
| `reports/*.xlsx` (63 files) | Yes | `reports/*.xlsx` |
| `logs/error.log`, `logs/service.log` | Yes | `logs/*.log` |
| `Archive/` (16 ZIPs) | Yes | `Archive/` |
| `.env` | Yes | `.env` |
| `__pycache__/` | Yes | `__pycache__/` |
| `nssm.exe` | Yes | `nssm.exe` |
| `.claude/` | Yes | `.claude/` |
| `migration_log_20260517_124445.csv` | Yes | `migration_log_*.csv` |
| `fix_names_log_20260518_095106.csv` | Yes | `fix_names_log_*.csv` |
| `patch2_log_20260517_130057.csv` | Yes | `patch2_log_*.csv` |
| `Агрокластер_Техника_Консолидация.xlsx` | Yes | `/Agroklastr_Tehnika_Konsolidaciya.xlsx` + `/Агрокластер_Техника_Консолидация.xlsx` |
| `wialon_import_v3.py` | Yes | `/wialon_import_v3.py` |
| `wialon.html` | Yes | `/wialon.html` |
| `wialon_auto_match.html` | Yes | `/wialon_auto_match.html` |
| `wialon_report_v2.html` | Yes | `/wialon_report_v2.html` |

### Files intentionally NOT excluded (safe to commit)

| File | Reason |
|---|---|
| `.env.example` | Template only — contains placeholder values, no real secrets (verified) |
| `config.py` | No production secrets (TASK-SEC-002 completed); dev fallback clearly named |
| `run_server.py` | No secrets; fail-fast guard only |
| `fuel_routes.py` | No hardcoded token (TASK-SEC-002 completed) |
| `init_data.py` | Default seed password `admin123` — known post-install default, not a secret |
| `install_service.bat` | Echo only; does not set `SECRET_KEY`; operator action post-install |
| `start.bat` | Echo only |
| `wialon_import.py` | Active module; LAN IP in comment only; no auth credentials |
| All `migrate_*.py` | Historical migration scripts; contain only DB paths, no credentials |
| All `templates/*.html` | Jinja2 templates; no credentials |
| `docs/` | All documentation files including this report |

---

## Final recommendation

**SAFE to create a private GitHub repository and push.**

Pre-conditions that are now met:
1. `.gitignore` is created and covers all database files, secrets, logs, and archives.
2. The one blocking finding (`wialon_import_v3.py` hardcoded token) is resolved by `.gitignore`.
3. No real secret values were found in any file that will be committed.
4. `config.py` reads `SECRET_KEY` and `FUEL_API_TOKEN` from environment only.
5. `fuel_routes.py` reads the API token from `current_app.config` per-request.
6. `.env.example` contains only placeholder values.

Remaining non-blocking items to track:
- `ProductionConfig.PG_PASS = 'changeme'` — must be replaced when `prod` config is activated (TASK-DEPLOY-006 / Phase 4).
- Admin password `admin123` — must be changed after every fresh install (documented).
- Private LAN IPs in comments — acceptable for a private repository; replace before making repo public.

---

## Operator next steps

### Step 1 — Verify `.gitignore` is working (before git init)

Run once to confirm the exclusion list is loaded correctly. After `git init`, run:

```cmd
cd C:\transport-report
git status
```

Confirm that these are NOT listed:
- `instance/` (entire directory)
- `reports/` (entire directory)
- `nssm.exe`
- `wialon_import_v3.py`
- `old_transport.db`
- `.env` (if it exists)

### Step 2 — Change the admin password immediately after each fresh install

After running `install_service.bat` or `init_data.py` on a fresh server:

1. Open the application in a browser.
2. Log in with `admin` / `admin123`.
3. Navigate to Admin → Change Password.
4. Set a new strong password (minimum 12 characters, mixed case + digits).
5. Log out and log in with the new password to confirm.

### Step 3 — Before enabling `prod` (PostgreSQL) config in the future

When switching `FLASK_ENV=prod`:

```cmd
setx PG_PASS "your-strong-postgres-password" /M
```

And update `config.py` line 46 to remove the `'changeme'` fallback:

```python
PG_PASS = os.environ.get('PG_PASS')  # no fallback — fail safe
```

This is a Phase 4 action (TASK-DEPLOY-006). Not required now.

### Step 4 — Proceed to TASK-DEPLOY-002 (GitHub repository creation)

1. Create a **private** GitHub repository named `transport-report` (or similar).
2. In `C:\transport-report\`, run:
   ```cmd
   git init
   git add .
   git status
   ```
   Verify that excluded files do not appear.
3. Continue with:
   ```cmd
   git commit -m "Initial commit: Vehicle Soft production v1.0"
   git remote add origin https://github.com/YOUR_ORG/transport-report.git
   git push -u origin main
   git tag v1.0-production-2026-05-23
   git push --tags
   ```
