# CLAUDE.md — Vehicle Soft Project Rules

## Project identity

Vehicle Soft is a Flask-based web application for Bukhoro Agroklaster transport, equipment, Wialon, fuel station and reporting workflows.

The application is used in production on a Windows server. Changes must be conservative, traceable and easy for a non-programmer operator to deploy.

## Current production context

- Project path on server: `C:\transport-report\`
- Application URL: `http://10.103.25.200:5050`
- Python: `C:\Program Files\Python314\python.exe`
- Service: NSSM service `TransportReport`
- DB: SQLite file `instance\transport.db`
- Frontend: Jinja2 templates, vanilla JavaScript and CSS only
- Excel generation: `openpyxl`
- Server runtime: Waitress via NSSM

## Required reading before every task

Read these files before editing code:

1. `docs/PROJECT_BRIEF.md`
2. `docs/AGENT_STATE.md`
3. `docs/ARCHITECTURE.md`
4. `docs/DECISIONS.md`
5. `docs/TASKS.md`
6. `docs/QA_CHECKLIST.md`
7. `docs/PROMPT_PROTOCOL.md`

For code tasks, inspect the actual target files before making any change.

## Non-negotiable constraints

- Never change database schema without a migration script and rollback note.
- Never delete production data automatically.
- Never drop or recreate `transport.db` in production.
- Never use Cyrillic text in `.bat` files.
- Never use Cyrillic text in `run_server.py` output because NSSM/Windows log encoding can break.
- Never add new Python dependencies without explaining why and updating `requirements.txt`.
- Never change core Excel layout without preserving approved business meaning.
- Do not rewrite the app from scratch unless explicitly approved.
- Do not use external frontend frameworks.
- Do not assume business rules for accounting, approval workflows or fuel accounting.

## Required comments for code changes

When adding or changing non-obvious logic, add a nearby comment:

```python
# [REASON]: Explain why this logic exists and which project constraint/business rule it protects.
```

Do not add noisy comments to every trivial line. Use this comment for business rules, migrations, compatibility fixes, Windows-specific fixes, Wialon/Topaz parsing, report formulas and security checks.

## Current module map

- `app.py` — app factory, auth, dashboard, daily entry, reports, references, admin users, module permission UI.
- `models.py` — SQLAlchemy models, roles, 9 equipment categories, Wialon, fuel, module permissions and spare parts models.
- `excel_export.py` — main Excel report generator with dynamic categories.
- `excel_daily_activity.py` — daily activity report.
- `wialon_import.py` — Wialon import, mapping, moto-hours reports and workload routes.
- `workload_report.py` — workload report data and Excel generator.
- `fuel_routes.py` — fuel station / warehouse / Topaz sync module.
- `spare_parts.py` — early spare parts request module.
- `translations.py` — UZ/RU translation dictionary.

## Release rules

Every deliverable must include:

1. Changed files only, unless a full archive is explicitly requested.
2. Migration script if database schema or reference data changes.
3. Step-by-step Windows CMD instructions.
4. Syntax check results.
5. Manual test checklist.
6. Rollback instructions.

## Service commands

Use these commands in instructions:

```cmd
cd C:\transport-report
.\nssm.exe stop TransportReport
.\nnssm.exe start TransportReport
```

If `nssm.exe` is unavailable in the current directory, use:

```cmd
net stop TransportReport
net start TransportReport
```

## Current recommended next work

Do not start a new large feature before addressing these audit items:

1. Enforce module permissions in route guards, not only admin UI.
2. Move secrets/tokens to environment variables.
3. Clarify and standardize Topaz sync API URL.
4. Split `wialon_import.py` into smaller units after current urgent work stabilizes.
5. Formalize migration/versioning process.
