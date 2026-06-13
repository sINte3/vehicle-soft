# EXTAUDIT001 External Audit Report

**Project:** Vehicle Soft / Transport Report — Bukhara Agrocluster
**Audited artifact:** source-only ZIP at HEAD `eaaaf32` ("Document FUELR001A fuel receipts verification")
**Audit date:** 2026-06-12
**Auditor role:** external senior software auditor / product architect / UI-UX reviewer / Flask-Jinja-SQLite consultant
**Method:** static source inspection only. No code was modified. No runtime testing was performed. Findings marked **[source-inferred]** were established by reading code; findings marked **[runtime-verify]** require confirmation on the live server before acting.

---

## 1. Executive Summary

This is a healthy, unusually well-disciplined internal production system for its size and team. The codebase shows clear evidence of a deliberate hardening campaign (SEC003A–F, DATA001, TASK-OPS-001) that actually landed in code, not just in documentation: CSRF protection is enforced globally, module permissions are now applied as route decorators across all modules, audit logging with before/after snapshots covers business actions, dangerous deletes are blocked or converted to deactivation, secrets have been moved out of source, and a migration registry with a written discipline exists. The Telegram stack (BOT001/002/003) is genuinely well architected for a three-service Windows deployment: hashed link codes and tokens, constant-time comparisons, an isolated post-commit outbox with deduplication and exponential backoff, and a worker that cannot crash the bot or the web app.

The previous internal audit's critical findings (`AUDIT_REPORT.md`) have all been remediated in the current HEAD. That is a strong signal about the project's process maturity.

Against that backdrop, the highest-value problems found by this audit are small, concrete, and fixable in hours rather than weeks:

1. **The BOT003 notification deep link is hardcoded to the staging server** (`http://10.103.25.14:5051/...` in `bot003_outbox_worker.py:293`). Production users who tap "So'rovni ochish" are sent to staging. **[source-inferred; runtime-verify by opening the link in a real production Telegram message]**
2. **`app.py` contains double-encoded Cyrillic (mojibake) in 19 lines**, including two that are user-visible: the Flask-Login redirect message (`app.py:65`) and — worse — the idle-reason text that "Copy previous day" **writes into the production database** (`app.py:964`), plus the fresh-install seed names for `app_modules` (`app.py:1992–1997`).
3. **Three OS processes write to one SQLite file with no WAL or busy-timeout configuration**, which is a latent "database is locked" risk as notification volume grows.
4. Several **dormant or half-wired features** (`SparePartStatusHistory` never populated, `tg_notifications` preference never consulted, `spare_request_revision_requested` event with no producer, legacy `bot_notification_queue` table) create confusion for the next developer and for the bot's own API consumers.

Overall opinion: **keep the architecture exactly as it is** (Flask + Jinja + SQLite + NSSM + the three-service Telegram layout) and spend the next cycle on a short hotfix phase (encoding + notification URL + escaping), a small operational-resilience phase (WAL, error logging, upload limits), and then proceed to BOT004 (approve/reject from Telegram), which is the single highest-business-value feature the current foundation is clearly ready for.

---

## 2. What Looks Good

**Security work that actually shipped.**
- `module_required(module_code)` is defined centrally in `models.py` and applied consistently across `app.py`, `fuel_routes.py`, `spare_parts.py`, and the Wialon routes. The earlier audit's top critical finding is fixed. **[source-inferred]**
- Global CSRF guard as a `before_request` hook with `hmac.compare_digest`, with a tight, documented exemption list (Topaz agent endpoints and `/api/bot/*` Bearer-token endpoints only) (`app.py:100–124`).
- Account lockout after 5 failed logins for 15 minutes, forced temporary-password change flow with an endpoint allow-list, password-vs-username check, and "new password must differ" checks (`app.py:313–418`, `sec003a_ext.py`).
- Soft-delete pattern everywhere it matters: users are blocked instead of deleted; equipment/station deletes are **blocked and converted to deactivation when linked records exist**, with the linkage counts captured in the audit record (`app.py:1727–1769` and the fuel equivalents). This is genuinely good ERP hygiene.
- Audit logging (`sec003a_ext.log_audit`) records actor snapshot, route, IP, user agent, before/after JSON, and masks password/token/secret keys recursively (`_safe_json`). Parameterized SQL is used for all audit reads and writes, including the filtered admin audit view (`app.py:1409–1451`).
- No `|safe` filter appears anywhere in templates; Jinja autoescaping is therefore intact across the whole UI. **[source-inferred]**
- Secrets: `SECRET_KEY` has **no fallback** in the production config classes, and `run_server.py` fails fast with clear NSSM-readable instructions if it is missing. `FUEL_API_TOKEN` missing → deny-all. `.env.example`, `.gitignore`, and `docs/SECRET_SCAN_REPORT.md` show a real secret-hygiene process, including the redaction follow-up tasks.

**Telegram architecture.**
- One-time 6-digit link codes are stored only as SHA-256 hashes, verified with `hmac.compare_digest`, expire in 10 minutes, and are cleared on use. API tokens are 32-byte `secrets.token_urlsafe` values, stored hashed, with expiry, revocation, `last_used_at`, and an inactive-user guard (BOT001B) that prevents both token issuance and `telegram_id` writes (`bot_api.py`, `bot_security.py`).
- The BOT003 outbox is the correct pattern for this environment: enqueue happens **after** `db.session.commit()` on the business action, through an **independent sqlite3 connection** that can never poison the Flask-SQLAlchemy session, is best-effort and never raises, deduplicates per recipient via `dedupe_key`, and the worker retries with exponential backoff (30 s → 30 min) up to `max_attempts` before marking permanent failure. The worker is a separate NSSM service and uses raw `urllib` so it has zero dependency coupling to `python-telegram-bot`.
- Log hardening in `bot.py` suppresses `httpx`/`httpcore` logs that could leak the bot token.

**Operational discipline.**
- A real migration registry (`schema_migrations`, `migration_utils.py`, `migrate_000_…`) with a written procedure (`docs/MIGRATIONS.md`): stop service → backup → one script at a time → verify registry → start. Scripts follow an idempotency template.
- Backups use the **SQLite online backup API** (`backup_transport_db.py`), not raw file copy — the script even documents why raw copy is unsafe under WAL. `update.bat` enforces working directory, pre-update backup, `git pull --ff-only`, `py_compile`, an import check, and an explicit manual gate before starting the service when a migration might be pending.
- The dashboard surfaces **backup freshness** (latest `.db` age with ok/warning at 36 h) directly to admins (`app.py:445–470`) — a small feature with outsized operational value.
- Release/QA documentation per task (SEC003*, UX001*, REPORT001*, BOT00*) with rollback notes; QA002 post-BOT003 regression audit recorded service states, git alignment, and smoke results.

**Data handling quality.**
- Daily-entry save validates equipment-org ownership, status enum, payment enum, positive quantity / non-negative price, collects **all** errors and shows them in one pass, and audits before/after row snapshots (`app.py:791–947`).
- Wialon import handles ZIP-or-CSV, three encodings (`utf-8-sig`/`utf-8`/`cp1251`), header-by-substring column detection, top-level row filtering, and the Russian day-format durations documented in `ARCHITECTURE.md`.
- Topaz sync deduplicates on `(station_id, topaz_txn_id)` with a DB-level unique constraint, logs every sync with counts, and has a legacy-path alias with a deprecation warning rather than a silent break (`app.py:1953–1963`).
- Excel reports are isolated in dedicated modules (`excel_export.py`, `excel_daily_activity.py`, `workload_report.py`) with the management-approved Times New Roman styling preserved as constants.

**Testing.**
- Nine bot-related test files exist, split sensibly between pure unit tests (formatters, state, config import) and live API contract tests against staging. That is more automated testing than most internal tools of this size ever get.

---

## 3. Main Risks

Ranked by (impact on users/data) × (likelihood), with effort to fix noted.

| # | Risk | Severity | Evidence | Effort |
|---|------|----------|----------|--------|
| R1 | **Production Telegram notifications link to the staging server.** The deep link is hardcoded as `http://10.103.25.14:5051/spare-parts/{id}` in `bot003_outbox_worker.py:293`. Production users may open staging and act on staging data believing it is production. | High (user confusion, possible wrong-environment edits) | source line 293; **[runtime-verify]** by checking the link in the production smoke-test message from 2026-06-10 | Low |
| R2 | **Mojibake written into production data.** "Copy previous day" inserts `idle_reason='Р'Р°Т›С‚РёРЅС‡Р° Р±СћС€'` (double-encoded "Вақтинча бўш") into `daily_records` (`app.py:964`). Every use corrupts new rows; the garbage then appears in the UI and in Excel reports. Also user-visible: `login_message` (`app.py:65`); fresh-install `app_modules` seed names (`app.py:1992–1997`). 19 affected lines total in `app.py` (rest are comments). | High (data quality, management-facing reports) | python scan in this audit; existing rows need a one-time data fix **[runtime-verify count via SQL]** | Low |
| R3 | **Single SQLite file, three writer processes, no WAL / busy_timeout anywhere.** Web app (SQLAlchemy), enqueue module, and outbox worker all write `instance/transport.db`; none sets `PRAGMA journal_mode=WAL` or a busy timeout beyond sqlite3's 5 s default. Under unlucky timing this yields "database is locked" errors, most likely surfacing as silently failed notification enqueues (they are best-effort) or 500s on web saves. | Medium-High (intermittent, hard to diagnose) | grep across repo: no `journal_mode`/`busy_timeout` settings; `backup_transport_db.py` comments mention WAL awareness but app never enables it | Low-Medium |
| R4 | **No 500 handler and broad silent exception swallowing.** `app.py` defines 400/403/404 handlers only; the dashboard context builder wraps each block in `except Exception: pass` (`app.py:510–592`), so a broken fuel report or audit query degrades silently with no log line. There is no central application logging configuration at all in the web app. | Medium (failures invisible to the operator who is not a programmer) | source | Low |
| R5 | **Notification text is sent with `parse_mode=HTML` without escaping payload fields.** Organization/equipment/user names containing `<`, `>` or `&` will make the Telegram API reject the message → notification retries then permanently fails (`bot003_outbox_worker.py:246–296`). | Medium (silent notification loss for specific records) | source | Low |
| R6 | **Dormant features that lie to their consumers.** `SparePartStatusHistory` is never written (no producer anywhere), yet the bot API returns it as `history` — always empty. `tg_notifications` and `tg_quiet_hours` exist in the schema and API responses but are **not consulted** by the BOT003 enqueue path (`_get_admin_telegram_ids` filters only role/active/telegram_id). `spare_request_revision_requested` is formatted by the worker but no route ever produces it. Legacy `bot_notification_queue` table coexists with `bot003_notification_outbox`. | Medium (developer confusion, broken user expectation of "notifications off") | source greps in this audit | Medium |
| R7 | **Fresh-install path is broken in two ways**: (a) `db.create_all()` does not create `audit_logs` (no ORM model — table comes only from `migrate_sec003a_real.py`), and `log_audit` silently no-ops without it, so a rebuilt staging DB runs with **zero audit logging**; (b) the `app_modules` seed inserts mojibake names (R2). | Medium (staging fidelity, audit gaps) | `models.py` has no AuditLog model; `sec003a_ext.py:125` early-return | Low-Medium |
| R8 | **Link-code brute-force surface.** `/api/bot/link/verify` is unauthenticated and unrated; a 6-digit code is 10⁶ combinations in a 10-minute window. Mitigated today by LAN-only exposure and the small set of users with active codes, but it is the weakest auth link if the app ever faces a wider network (Cloudflare Tunnel plan). | Low-Medium today; High if exposed | `bot_api.py:143–228` | Low |
| R9 | **Session policy contradiction.** `PERMANENT_SESSION_LIFETIME = 86400` (24 h) but `login_user(user, remember=True)` issues a remember-cookie with Flask-Login's default ~365-day duration (`app.py:334`), so the effective login lifetime is a year, not a day. | Low-Medium (policy clarity) | source | Low |
| R10 | **Hardcoded environment facts in code**: backup directory `D:\transport-report-backups\production\daily` (`app.py:446`) — staging shows production's backup status or "unknown"; UTC+5 display shift hardcoded (`app.py:1442`); bot API default base URL `127.0.0.1:5051` (staging port) in `bot_config.py`. None is wrong on the production box today, but each is a trap during the planned Hetzner/Cloudflare move. | Medium (future migrations) | source | Low-Medium |
| R11 | **No upload size limit** (`MAX_CONTENT_LENGTH` unset) and Wialon upload reads the whole file into memory; a mistaken multi-hundred-MB upload can exhaust memory on the old server. | Low-Medium | `wialon_import.py:246–260` | Trivial |
| R12 | **Outbox row "lock" does not actually lock.** `locked_at = now` is immediately re-grabbable by another worker one tick later (`locked_at < now2` is true). Safe only because exactly one TransportBot003 instance runs. Document this constraint or add a lock-timeout window before ever running two workers. | Low (deployment-constraint dependent) | `bot003_outbox_worker.py:84–105` | Low |
| R13 | **Docs drift.** `docs/ARCHITECTURE.md` still states "module permissions… are not enforced centrally" — false at current HEAD — and `docs/UI_TRANSLATION_AUDIT.md` claims the fuel module is Russian-only, while fuel templates now carry inline UZ/RU pairs. A future contributor (or AI session) acting on these docs will mis-plan work. | Medium (process risk for an AI-assisted workflow) | doc vs. code comparison | Low |
| R14 | Minor latent bugs: Wialon column-index truthiness (`idx_move and idx_move < len(row)` skips a column that lands at index 0; same for `idx_idle`, `wialon_import.py:~332`); Topaz token compared with `!=` instead of `compare_digest` (`fuel_routes.py:1642`); unbounded `.all()` on spare-request list (fine at current volume, will not scale forever). | Low | source | Trivial each |

UX, operational, and product-level risks are detailed in their own sections below.

---

## 4. Architecture Audit

**Overall shape.** App-factory Flask application (`create_app()` in `app.py`, ~2,000 lines) with three blueprints — `fuel_bp` (`/fuel`), `spare_parts_bp` (`/spare-parts`), `bot_api_bp` (`/api/bot`) — plus Wialon routes registered functionally via `register_wialon_routes(app, editor_required, admin_required)`. This hybrid is fine; the function-registration style for Wialon exists to share the role decorators and is documented. The runtime is Waitress (4 threads) under NSSM as `TransportReport`, with two sibling services (`TransportBot` long-polling runner, `TransportBot003` outbox worker). The separation of web / bot / worker into three OS processes is the single best architectural decision in the project: each can fail, restart, and be rolled back independently. **[source-inferred; service layout per docs/BOT003_PRODUCTION_ROLLOUT and QA002]**

**Module boundaries.**
- Good: fuel, spare parts, and bot API are self-contained modules with their own validation, audit helpers, and templates subfolder (fuel).
- Mixed: `wialon_import.py` (1,187 lines) combines CSV/ZIP parsing, auto-matching, DB writes, six routes, and an inline Excel export. It works, but it is the file where a future regression is most likely. The previous audit said the same; the safe path remains "extract parsing into a `wialon_parser.py` later, no behavior change."
- `app.py` itself remains a monolith of auth + dashboard + entry + report + references + admin. At ~2,000 lines it is at the upper bound of comfortable; nothing requires splitting it now, but new feature areas should land as blueprints, not as more `app.py` sections.

**Configuration.** `config.py` is clean: three profiles (`dev` / `sqlite_prod` / `prod`-PostgreSQL), env-driven, no production fallbacks for secrets. The prepared PostgreSQL class still defaults `PG_PASS='changeme'` — harmless until used, but worth removing the default before any PG work. `run_server.py` correctly defaults `FLASK_ENV=sqlite_prod` and fails fast on a missing `SECRET_KEY` with operator-grade instructions. ASCII-only discipline in `run_server.py` and `.bat` files is respected (the cp1251/NSSM constraint).

**Static assets.** There is no `static/` directory: all CSS (~160 lines of a coherent design-token system) lives inline in `base.html`, and shared JS (the multiselect widget) is inlined too. Consequences: every page ships the full CSS, browser caching of styles is impossible, and the IBM Plex Sans font is loaded from Google Fonts — on a LAN without internet egress for client PCs this silently falls back to system fonts. None of this is broken, but extracting `static/app.css` (and self-hosting the font, or accepting the fallback explicitly) is a cheap, zero-risk, DB-free improvement.

**Internationalization.** Two parallel mechanisms coexist: (a) `translations.py` (~860 keys) with the `t()` context function, used by core templates; (b) inline `{% set L_x = 'ru' if lang == 'ru' else 'uz' %}` ternaries, used by fuel, spare parts, and the new dashboard. Both work; the duplication is the cost. Route-level flash messages use a third micro-pattern (`ui_t`, `fuel_t`, `_spare_t`, `_wialon_t` — four nearly identical local helpers). A later UX phase should pick one mechanism and converge; until then, document the rule "new templates use the inline-set pattern" (or the opposite) so the split stops growing.

**Encoding hygiene.** All templates and every Python module except `app.py` are clean UTF-8. `app.py` carries 19 mojibake lines from a historical encoding accident: 16 are section-divider comments (cosmetic), 3 are functional (Section 3, R2). The fix is mechanical (re-type the strings), but because `app.py` is the production core, it should ship as its own tiny, diffable commit with `py_compile` + import check + smoke, per the existing release procedure.

**Startup behavior.** `db.create_all()` runs on every start plus a first-run `app_modules` seed. With the migration registry now in place, `create_all` is redundant-but-harmless for existing DBs (it only creates missing tables) — except that it creates a **false sense** of fresh-install completeness: `audit_logs` and `bot003_notification_outbox` are migration-only tables, so a from-scratch environment is silently missing audit logging until the SEC003A and BOT003 migrations are run. Either add lightweight ORM models for those tables (so `create_all` covers them) or document loudly in `MIGRATIONS.md` that a fresh install must run the migration set in a stated order.

**Dependency management.** `requirements.txt` pins six packages for the web app; `requirements_bot002.txt` pins `python-telegram-bot` for the bot service. That split matches the process split — good. `pandas` is imported only by two historical one-off scripts (`migrate_equipment_excel.py`, `patch2_skipped.py`) and correctly absent from requirements; once those scripts are archived (below), the question disappears entirely.

**Root-level clutter.** `patch_orgs.py`, `patch2_skipped.py`, `fix_eq_names.py`, `add_boshqa_column.py`, and the original `migrate.py` (hardcoded `C:\transport-report\old_transport.db`) are one-time historical operations sitting next to live modules. They compile, they can be accidentally run, and they confuse the migration inventory. Move them to an `archive/` (or `tools/history/`) folder — a pure `git mv`, no code change.

---

## 5. Database and Migration Audit

**Schema.** 30+ ORM models in one `models.py`, with sensible indexes where queries need them (`daily_records(work_date, equipment_id, line_index)`, fuel tx date/azs, outbox status indexes via migration) and real unique constraints guarding business invariants: `engine_hours(work_date, equipment_id)`, `fuel_transactions2(station_id, topaz_txn_id)`, `fuel_initial_balances(warehouse_id, fuel_type)`, `vialon_mappings.vialon_name`, `users.telegram_id`, `bot_api_sessions.token_hash`. The delete-then-reinsert pattern for daily entry is wrapped per-equipment and audited; acceptable at this scale.

**Known dualities (legacy debt, correctly quarantined so far):**
- Fuel v1 models (`FuelStation/Tank/Snapshot/Transaction/SyncLog`) coexist with v2 (`*2` tables). v1 still has root-level templates (`fuel_dashboard.html`, `fuel_balance.html`, `fuel_receipts.html`, `fuel_history.html`, `fuel_sync_log.html`). Whether any v1 route is still reachable should be confirmed **[runtime-verify / grep of registered routes on server]** — from source, the fuel blueprint serves only v2 pages, so the v1 templates appear orphaned. Action: mark v1 read-only-legacy in `ARCHITECTURE.md`, plan removal only after a verified period of zero use.
- `bot_notification_queue` (BOT001, ORM) vs `bot003_notification_outbox` (BOT003, raw SQL) — the former is dead. Document it as superseded; drop only in a far-future cleanup migration.

**Migration system.** The registry (`schema_migrations` + `migration_utils.py`) and `docs/MIGRATIONS.md` are exactly right for SQLite-on-Windows: stop service → backup → one idempotent script → verify registry → start. Nineteen `migrate_*.py` scripts exist; only the registry-era ones follow the `NNN_` naming. The backfill of historical scripts (TASK-OPS-002 via `migrate_001_backfill_historical_registry.py`) is designed but, per `MIGRATIONS.md`, **not yet executed** — meaning the registry currently understates what production has actually applied. That is acceptable as long as humans remember it; finishing the deliberate backfill removes the ambiguity. **[runtime-verify: SELECT from schema_migrations on production]**
A fresh-rebuild order is not written anywhere. Recommend adding a short "fresh install: run these N scripts in this order" list to `MIGRATIONS.md` — it doubles as disaster-recovery documentation.

**Integrity enforcement gap.** SQLAlchemy on SQLite does **not** enable `PRAGMA foreign_keys=ON` by default, and no engine-connect listener sets it; ORM-level `cascade=` rules protect ORM deletes only, while the raw-sqlite3 writers (notifications, worker) and any manual SQL bypass FK checking entirely. Today the SEC003E delete-blocking largely compensates. When convenient, add the standard `@event.listens_for(Engine, "connect")` pragma listener — one function, no schema change — and consider `journal_mode=WAL` + `busy_timeout` in the same listener plus in the two raw connections (addresses R3).

**Backup/rollback readiness.** Strong: online-backup tool with `--source/--dest-dir/--suffix`, pre-update backups in `update.bat`, per-release backup paths recorded in rollout docs, dashboard freshness indicator, and a documented restore procedure. One inconsistency: `backup_transport_db.py` defaults to `C:\transport-report-backups\daily` while the dashboard watches `D:\transport-report-backups\production\daily` — presumably the scheduled task passes `--dest-dir` **[runtime-verify scheduled task arguments]**; align the script default with reality to remove the trap.

**Where future migrations are risky.**
1. Any `users` table change — it is touched by ORM, by `sec003a_ext` raw SQL (column-introspecting, so resilient), and by BOT001 fields; coordinate all three views.
2. Renaming/retiring fuel v1 tables — verify zero readers first.
3. The eventual PostgreSQL move — `parse_datetime_safe` and the audit log's ISO-string timestamps assume SQLite's loose typing; date columns stored as TEXT comparisons (`created_at >= 'YYYY-MM-DDT00:00:00'`) will need review. Keep this on the radar; do not start it as a side effect of anything else.

---

## 6. Security and Permissions Audit

**Authentication.** Username/password via Flask-Login with hashed passwords (Werkzeug), per-account lockout (5 fails / 15 min), forced temporary-password change on admin-set passwords, last-login and last-IP tracking, and full auth-event audit (success / failure / locked / logout / password change). Password policy is minimal (≥8 chars, must not contain username) — adequate for a 10-user LAN tool; raise only if exposure widens.

**Authorization.** Three-layer model, consistently applied:
1. Role decorators (`admin_required`, `editor_required`) — viewer is read-only by `can_edit` checks inside spare-parts/fuel edit routes as well, not just at the nav level.
2. **Module permissions** via `module_required('transport'|'fuel'|'wialon'|'deficiencies'|'spare_parts')` on every relevant route, deny-by-default for non-admins, admin-managed grid UI, all changes audited. This audit verified the decorator on all `app.py` routes, all fuel routes, all spare-parts routes, and the Wialon registrations — the SEC003F claim holds in code.
3. **Organization scoping**: `can_access_org`/`get_org_ids` filters every list/report/save across transport, spare parts, and the bot API; cross-org form tampering is rejected in `save_entry` both at the `org_id` level and per-equipment (`eq.organization_id != org_id` check).

**CSRF.** Custom session-token implementation with constant-time comparison and header fallback for AJAX; exemptions are exactly the two token-authenticated machine APIs. Tokens are static per session (standard for this pattern). 400 handler gives a human message.

**Dangerous actions (SEC003E).** Users: block-not-delete with self-block prevention. Equipment & fuel stations: enable/disable lifecycle; hard delete blocked when linked records exist, with the block itself audited including linkage counts. Work types / customers: admin-only deletes. This is the right shape for an ERP.

**Telegram security.** Covered in Section 7; the auth chain (admin-issued hashed one-time code → hashed bearer token, 30-day expiry, revocation, inactive-guard) is sound. Residual items: no rate limit on `/api/bot/link/verify` (R8); the bot's local `bot_state.db` stores the **raw** API token (necessary for operation — protect the file with NTFS ACLs and note it in deployment docs); `/api/bot/health` and `/fuel/api/fuel_ping` are unauthenticated but disclose nothing sensitive (module/version string, server time).

**Secrets handling.** No live secret found in the ZIP. `.env.example` documents required variables; `.gitignore` excludes `.env`, DBs, logs, and the historical token-bearing `wialon_import_v3.py`; `SECRET_SCAN_REPORT.md` shows the redaction was followed through into docs. The one pending real-world item from project history — rotating the staging bot token that leaked into earlier session artifacts — is outside this ZIP but should be confirmed done **[runtime-verify in BotFather]**.

**Web-platform hardening not yet present (acceptable today, listed for completeness):** no security headers (X-Frame-Options/X-Content-Type-Options/CSP), `SESSION_COOKIE_SECURE` unset (meaningless over plain HTTP LAN), `remember=True` vs 24-h session contradiction (R9), no `MAX_CONTENT_LENGTH` (R11), Topaz token compared with `!=` (R14). Every one of these becomes mandatory pre-work for the Cloudflare-Tunnel exposure plan; none blocks LAN operation now.

**Access separation admin/operator.** Verified across modules: admin-only = user management, permissions grid, audit view, org/warehouse/station management, work-type & customer deletes, spare-request approve/reject; operator = data entry and own-draft submission; viewer = read-only within granted modules and orgs. The model is coherent and matches the documented intent.

---

## 7. Telegram Architecture Audit

**BOT001 (foundation).** Schema (telegram fields on `users`, `bot_api_sessions`, `spare_part_status_history`, `bot_notification_queue`) + read-only API + admin link-code issuance. Design choices that aged well: never accepting login/password over Telegram; hash-only storage; one-time code flashed to the admin and never logged; org-scoped reads; serializer that structurally cannot leak `password_hash` or code hashes.

**BOT002/BOT002B (runner).** `bot.py` long-polling with seven commands, local `bot_state.db` session cache, isolated HTTP client with timeout handling, formatter module with its own unit tests, log hardening, Python-3.14 event-loop workaround documented inline. The runner talks to the Flask API over HTTP rather than importing the app — correct: it keeps the bot restartable without touching the web service and makes the staging/production split a pure env-var matter (`BOT_API_BASE_URL`). The shipped default of `127.0.0.1:5051` is staging-flavored; production must override it **[runtime-verify NSSM AppEnvironmentExtra for TransportBot]**.

**BOT003 (outbox + worker).** The strongest subsystem in the project. Producer (`bot003_notifications.py`): post-commit, best-effort, independent connection, dedupe-keyed, admin-fanout for submissions and creator-targeting for status changes. Consumer (`bot003_outbox_worker.py`): batch read with soft row-claiming, per-row commit, exponential backoff, permanent-failure state with `last_error`, `--once/--dry-run/--interval` modes, and a separate `diagnose_bot003_outbox.py` for operators. Validation was done staging-first with documented production rollout and smoke test.

**Defects within BOT003 (all small):**
1. Hardcoded staging deep link (R1) — should come from a single `APP_PUBLIC_BASE_URL` env var with a safe default, read by the worker.
2. No HTML escaping of payload fields under `parse_mode=HTML` (R5) — escape `&<>` when formatting.
3. `tg_notifications` / `tg_quiet_hours` ignored (R6) — either honor the flag in `_get_admin_telegram_ids` and the creator path, or remove the fields from the API response so the bot does not imply a working preference.
4. `spare_request_revision_requested` has formatter support but no producer — either add the "return for revision" transition (it is a genuinely useful workflow step) or delete the dead branch.
5. Row-claiming is single-worker-safe only (R12) — add a comment + a lock-timeout window (e.g., re-claimable only if `locked_at < now − 5 min`) before anyone ever scales workers.
6. `SparePartStatusHistory` unpopulated while the bot API serves it (R6) — the web transitions already compute before/after; writing one history row there is a five-line, no-migration change that makes `/status` and request detail genuinely useful.

**Future bot roadmap (architecture-compatible, in order of leverage):**
- **BOT004 — act from Telegram**: add authenticated **write** endpoints (`POST /api/bot/requests/<id>/approve|reject`) reusing the existing web-route logic and audit, then inline buttons in the runner with idempotency on the request status (the state machine already rejects non-`submitted` transitions, which is the natural idempotency guard). This is the feature operators will feel most.
- **BOT005 — operational alerts**: reuse the outbox for non-spare-parts events: Topaz sync stale > N hours, backup older than 36 h (the dashboard already computes both), fuel danger-warnings digest.
- **BOT006 — daily digest**: morning summary per admin (yesterday's entry coverage %, idle counts, open requests) — read-only, zero schema change.
- Later: Mini App for the spare-parts visual form (spec already drafted per project history) — keep it strictly on top of the same Bearer-token API.

---

## 8. UI/UX Audit

**Design system.** `base.html` defines a compact, coherent token set (CSS variables for surfaces, borders, accent green, semantic danger/warn/info, payment-type colors) with consistent card, table, badge, button, flash, and form patterns, plus a reusable multiselect widget. The visual language — calm light theme, IBM Plex Sans, dense enterprise tables, colored status badges — is already the "realistic internal enterprise dashboard" the redesign brief asks for. The right strategy is refinement, not replacement.

**What works well for the operator audience:**
- Sticky top nav with role badge and module-gated items — users only see what they may open; active-state highlighting per endpoint.
- Daily entry: per-equipment cards with working/idle left-border color coding, multi-line work rows, computed amount display, toggle groups; validation errors returned as a single titled list (UX001A).
- Dashboard (DASH001): per-module KPI cards with access gating, danger/warn coloring driven by fuel warnings, last-sync age, recent audit feed, and backup freshness — a real management view, not decoration.
- Reports page: on-screen preview (totals, per-org and per-work-type summaries, 300-row capped table) before committing to an Excel download; bilingual file names.
- Flash messages support multi-line structured errors with a title + bullet list.

**Gaps and friction points (none requires a DB change):**
1. **Translation-mechanism split** (Section 4): some labels are hardcoded in one language — e.g., nav items "Журнал действий" (`base.html:217`, RU-only) and "Юклама ҳисобот" (`base.html:192`, UZ-only). A user in the other language sees foreign labels in the main menu.
2. **Mojibake exposure**: the Flask-Login "please log in" flash and any `idle_reason` rows already written by copy-previous-day render as garbage in tables and Excel (R2).
3. **Mobile**: a single 768 px breakpoint collapses the entry grid and nav, but wide tables (report preview, Wialon mapping list, fuel transactions) rely on browser-default overflow without a scroll affordance; on phones this reads as "cut off". Cheap fix: wrap wide tables in an `overflow-x:auto` container and add a subtle scroll hint.
4. **No favicon / app identity**; browser tabs show the default icon — trivial polish with disproportionate perceived-quality effect.
5. **External font dependency** (Google Fonts) — on machines without internet the UI silently falls back; self-host the two weights actually used, or accept and document the fallback.
6. **Empty states** are plain text where they exist; lists like spare parts with zero rows could guide the next action ("Янги сўров яратиш →"). Low priority, high perceived quality.
7. **Delete confirmations**: present on some forms via `onsubmit confirm` patterns but not uniformly **[runtime-verify per page]**; standardize a single confirm helper for all destructive POSTs.
8. **No pagination** on spare-parts list and Wialon mapping list (`.all()`); fine at today's volumes, will degrade gracefully but slowly — add simple limit/offset paging when row counts approach thousands.
9. **Inline CSS/JS** per Section 4 — extract to `static/` for caching and diff-friendly UI work.

**RU/UZ handling verdict:** functionally bilingual nearly everywhere (core via `t()`, fuel/spare/dashboard via inline pairs), with a short residual list of one-language strings. The 2026-05-23 translation audit document is now outdated and should be refreshed rather than trusted.

---

## 9. Module-by-Module Review

**Dashboard (`/`, `index.html`, `_build_dashboard_context`).** Aggregates transport KPIs from the filtered record set, fuel totals/warnings via `_collect_fuel_report_data`, Wialon mapping coverage, spare-request counts (org-scoped for non-admins), last 8 audit rows, and backup freshness. Module cards hide when access is absent. Weaknesses: every sub-block swallows exceptions silently (R4), so a broken fuel calculation shows zeros instead of an error; the backup path is environment-hardcoded (R10). Verdict: strong feature, needs observability.

**Reports (`/report`, `excel_export.py`, `excel_daily_activity.py`).** Preview-then-download flow; org/category filters enforced against the user's org set on both GET and POST; language-aware exports; the 8-sheet management workbook preserved per ADR-003. `REPORTS_DIR` accumulates generated files indefinitely **[runtime-verify disk usage]** — add a cleanup (delete > 30 days) or generate to a temp file and stream. The legacy single-org `org_id` query parameter is still honored for backward compatibility — good touch.

**Fuel (`/fuel/*`, `fuel_routes.py`).** Clear v2 domain (warehouse = org → stations with `topaz_id` → receipts in / Topaz transactions out → balance = initial + receipts − issued), warnings engine with severity, codes, and a persistent review workflow (`FuelWarningReview` with new/in-progress/resolved and audited updates — REPORT001E). Sync endpoint: token-gated, dedup-constrained, fully logged, with a deprecated alias kept deliberately. Admin-only management of warehouses/stations with enable/disable lifecycle; operator-level receipts gated by `can_edit`. Issues: token comparison style (R14); the module is RU-led with UZ inline pairs — consistent within itself; v1 leftovers (Section 5).

**Wialon (`/wialon/*`, `wialon_import.py`, `workload_report.py`).** Upload (ZIP/CSV, multi-encoding) → mapping (manual + auto-match with normalized-name heuristics and a review screen) → `engine_hours_records` upsert keyed by date+equipment → workload report with 8 h/day norm and Excel export. Import logs retain unknown-vehicle lists for follow-up, and the index page computes a pending-mapping count. Day-range imports divide period totals across days per documented rule. Issues: the index-0 truthiness bug (R14, latent), no file-size cap (R11), module mixes too many concerns (Section 4).

**Spare parts (`/spare-parts/*`, `spare_parts.py`).** Draft → submitted → approved/rejected state machine with creator-only submission, admin-only review, review comments, full audit, org scoping on every path, and post-commit BOT003 notifications. Catalog with search powering both web and bot. Gaps: status history table never written (R6); no "return for revision" transition though the notification layer anticipates it; items are name-snapshotted on the request (good — catalog edits don't rewrite history).

**Admin/security pages.** Users (create/edit with temp-password enforcement, org assignment, block), per-user Telegram link-code button with expiry messaging, module-permissions grid, and the audit-log browser with date/user/action/module filters, parameterized SQL, and a 300-row cap with UTC+5 display conversion (hardcoded — R10). The permissions grid posts the full matrix each save — simple and auditable; fine at this user count.

**Telegram-related web surface.** No standalone Telegram pages exist (by design); the admin touchpoint is the link-code action on the users page, and notification behavior is invisible in the web UI. A future small win: a read-only "Notifications" admin page over `bot003_notification_outbox` (status counts, recent failures with `last_error`) so the non-programmer operator can see delivery health without SQL — `diagnose_bot003_outbox.py` already proves the queries.

---

## 10. Recommended Redesign Direction

The current UI is already 80% of the right answer for an internal enterprise tool. The direction should be **consolidation, not reinvention** — explicitly not a marketing-style restyle, no frontend framework, no route changes, and no DB involvement.

1. **Tokenize and extract.** Move the existing CSS variables + components from `base.html` into `static/app.css` (one file, cache-busted by a `?v=` query). Self-host IBM Plex Sans (two weights) or formally adopt the system-font fallback. Add a favicon and a consistent page `<title>` pattern. Zero behavioral risk.
2. **One i18n mechanism.** Decide between `translations.py`/`t()` and inline pairs; migrate the minority side template-by-template (each template is an independent, reviewable, DB-free patch). Fix the handful of one-language nav labels first — they are the most visible.
3. **Table standards.** A single `.table-wrap{overflow-x:auto}` convention for wide tables; standardized status-badge classes shared by transport/fuel/spare-parts instead of three local variants; right-aligned numeric columns everywhere (mostly already true).
4. **Form & destructive-action standards.** One shared confirm pattern for all delete/disable POSTs; one shared validation-error flash format (already exists — adopt it in the remaining modules' routes).
5. **Empty states & micro-copy.** Short bilingual guidance lines with a primary action where lists can be empty (spare parts, Wialon unknowns, fuel receipts).
6. **Dashboard as the home for health.** Keep extending the existing card pattern (it earned trust): add a "Notifications" health tile (outbox failed/pending counts) and a "Topaz sync" age tile — both read-only.

Phasing: each numbered item is an independent `UX00x` patch — visual-only, staging-first, no migrations, trivially rollback-able by file revert — exactly matching the project's stated change-management preferences.

---

## 11. Recommended New Features

**Quick wins (hours each; no schema change unless noted):**
1. Configurable notification base URL (`APP_PUBLIC_BASE_URL` env) replacing the hardcoded staging link — fixes R1.
2. Mojibake remediation: 3 functional strings + 16 comments in `app.py`, plus a one-time SQL fix for affected `idle_reason` rows (count first). Fixes R2.
3. HTML-escape payload fields in the worker's message builder (R5).
4. Honor `tg_notifications=0` in both enqueue paths (R6, user-trust fix).
5. Write `SparePartStatusHistory` on every status transition (R6) — instantly improves bot `/status` and request detail.
6. SQLite resilience: engine-connect listener setting `foreign_keys=ON`, `journal_mode=WAL`, `busy_timeout`; same pragmas in the two raw sqlite3 connections (R3).
7. `MAX_CONTENT_LENGTH` (e.g., 32 MB) + friendly 413 handler (R11).
8. 500 error handler + basic rotating-file logging for the web app; replace dashboard `except: pass` with logged warnings (R4).
9. Archive the five historical one-off scripts; refresh `ARCHITECTURE.md` and `UI_TRANSLATION_AUDIT.md` to current truth (R13).
10. Trivial code fixes: `is None` checks for Wialon column indexes; `hmac.compare_digest` for the Topaz token; drop `remember=True` or set an explicit `REMEMBER_COOKIE_DURATION` (R9, R14).

**Medium improvements (days; staged):**
11. **BOT004 — approve/reject from Telegram**: two authenticated write endpoints + inline buttons + status-machine idempotency + audit attribution to the acting user. Highest operator-visible value on the list.
12. "Return for revision" status (`returned`) in the spare-parts workflow, wiring the already-anticipated `spare_request_revision_requested` notification.
13. Operational alerts through the existing outbox: Topaz sync stale, backup stale, daily fuel danger-warning digest (BOT005).
14. Admin "Notifications health" page over the outbox (read-only).
15. Reports housekeeping: scheduled cleanup of `reports/` and `before_update` backups older than N days.
16. Rate-limit `/api/bot/link/verify` (per-IP/per-telegram_id counter with cooldown) — prerequisite for any wider network exposure (R8).
17. Audit-log CSV export for the admin (filters already exist).
18. Pagination on spare-parts and Wialon mapping lists.

**Larger future features (weeks; separate planning):**
19. Branch-submission workflow (ROADMAP Stage 2): branch operator enters → submits day for org → head office approves/returns; reuses the spare-parts state-machine pattern and the outbox for notifications. Needs a `daily_submissions` table — its own design doc first.
20. Telegram Mini App for the spare-parts visual form, strictly atop the Bearer API.
21. PostgreSQL migration (config prepared) — only as its own project with a data-migration rehearsal on staging, after the Hetzner/Cloudflare decision; SQLite is not currently the bottleneck.
22. Wialon API pull (replace manual ZIP upload) — keep the existing parser as the fallback path.
23. Equipment passport page (per-unit history: work, engine hours, fuel, spare parts) — pure read aggregation over existing tables; strong management appeal.

---

## 12. Proposed Roadmap

Each phase is small, staging-first, source-controlled, independently rollback-able, and never mixes UI redesign, DB changes, and business logic in one patch. Validation commands follow the existing release procedure (`py_compile` → import check → test-client checks → staging smoke → production rollout with backup).

**Phase FIX001 — Production-correctness hotfixes (1 short release)**
- FIX001A: `APP_PUBLIC_BASE_URL` env var for notification links; HTML-escape payload fields. Files: `bot003_outbox_worker.py` only. Validation: staging notification message link + a name containing `<` test. Rollback: file revert, restart TransportBot003.
- FIX001B: mojibake remediation in `app.py` (3 functional strings, 16 comments) + one-time data-fix script for corrupted `idle_reason` rows (report-only mode first, registry-recorded). Rollback: file revert + pre-fix DB backup.

**Phase OPS001 — Resilience & observability (1 release)**
- OPS001A: SQLite pragmas (FK ON, WAL, busy_timeout) via engine listener + in both raw connections. Note for ops: confirm the backup tool behavior under WAL (it already uses the online API — designed for this).
- OPS001B: 500 handler, rotating-file logging, dashboard exception logging, `MAX_CONTENT_LENGTH` + 413 page.
- OPS001C: archive historical scripts; complete the deliberate TASK-OPS-002 registry backfill; add the fresh-install migration order to `MIGRATIONS.md`; refresh `ARCHITECTURE.md` / `UI_TRANSLATION_AUDIT.md`.

**Phase BOT004 — Act from Telegram (2 releases)**
- BOT004A: write endpoints `POST /api/bot/requests/<id>/approve|reject` (admin-token only, state-machine guarded, audited with acting user, status-history row written). Contract tests added beside the existing BOT002 test suite.
- BOT004B: inline approve/reject buttons in the runner with callback handling, double-tap idempotency, and outbox notifications to the creator. Staging validation doc per BOT003 template, then production rollout.
- BOT004C (optional, same foundation): "return for revision" transition end-to-end.

**Phase UX001E–H — UI consolidation (visual-only, no DB)**
- UX001E: extract `static/app.css`, self-host font, favicon.
- UX001F: i18n unification pass 1 (nav + base + remaining one-language labels).
- UX001G: table wrap/scroll standard, unified status badges, shared confirm helper.
- UX001H: empty states + notifications/Topaz health tiles on the dashboard.

**Phase BOT005 — Operational alerts (1 release)**
- Stale-sync, stale-backup, danger-warning digest events through the existing outbox; admin "Notifications health" page.

**Phase QA003 — Regression checkpoint**
- Post-BOT004/UX regression audit per QA002 template: services, git alignment, smoke of all modules in both languages, outbox health, audit-log spot checks.

**Phase SEC004 — Pre-exposure hardening (only when the Cloudflare/Hetzner move is scheduled)**
- Rate-limit link/verify and login, security headers, cookie flags, HTTPS assumptions, remember-cookie policy, token-rotation runbook. Explicitly out of scope until exposure is decided.

**Deferred (own projects):** branch-submission workflow; PostgreSQL migration; Wialon API pull; Mini App.

---

## 13. Top 20 Action Items

Ranked by Impact (user/data value) vs Risk-of-change vs Effort. I = impact, R = change risk, E = effort (L/M/H).

| # | Action | I | R | E | Phase |
|---|--------|---|---|---|-------|
| 1 | Notification link → `APP_PUBLIC_BASE_URL` env (kill hardcoded :5051) | H | L | L | FIX001A |
| 2 | Fix mojibake `idle_reason` write in copy-previous-day + login message + module seed | H | L | L | FIX001B |
| 3 | One-time data fix for corrupted `idle_reason` rows (count → backup → update) | H | L | L | FIX001B |
| 4 | HTML-escape notification payload fields | M | L | L | FIX001A |
| 5 | SQLite WAL + busy_timeout + FK pragma in all three writers | H | M | L | OPS001A |
| 6 | 500 handler + file logging + log dashboard exceptions | M | L | L | OPS001B |
| 7 | `MAX_CONTENT_LENGTH` + 413 page | M | L | L | OPS001B |
| 8 | Write `SparePartStatusHistory` on every transition | M | L | L | BOT004A |
| 9 | Honor `tg_notifications` in enqueue paths | M | L | L | FIX001A/BOT004 |
| 10 | BOT004 write API (approve/reject) with audit + tests | H | M | M | BOT004A |
| 11 | Inline approve/reject buttons in bot with idempotency | H | M | M | BOT004B |
| 12 | Archive 5 historical root scripts; refresh stale docs | M | L | L | OPS001C |
| 13 | Complete registry backfill + fresh-install order in MIGRATIONS.md | M | L | L | OPS001C |
| 14 | Extract `static/app.css`, self-host font, favicon | M | L | L | UX001E |
| 15 | Fix one-language nav labels; start i18n unification | M | L | L | UX001F |
| 16 | Table overflow standard + unified badges + confirm helper | M | L | M | UX001G |
| 17 | Rate-limit `/api/bot/link/verify` | M | L | L | SEC004 (early ok) |
| 18 | `compare_digest` for Topaz token; Wialon `is None` index fix; remember-cookie policy | L | L | L | OPS001B |
| 19 | Reports/ and before_update cleanup job | L | L | L | OPS001C |
| 20 | Admin notifications-health page + dashboard tile | M | L | M | BOT005/UX001H |

---

## 14. What You Would NOT Change

- **Flask + Jinja + SQLite + Waitress + NSSM on Windows** — correct for the team, the user count, and the operator's skill profile (ADR-001/002 stand).
- **The three-service split** (web / bot runner / outbox worker) and the outbox pattern itself — this is the architecture larger systems converge to; do not merge the worker into the bot or the web app.
- **Current routes, URL structure, and the permission model** (roles + module grants + org scoping) — stable, audited, and understood by users; BOT004 should reuse, not reshape, it.
- **The migration registry discipline and the stop→backup→one-script→verify→start procedure** — keep it manual and explicit; do not introduce Alembic auto-migrations on this SQLite production.
- **Excel report formats and styling** (ADR-003) — management-approved; any change remains a business decision, not a refactor side effect.
- **ASCII-only `.bat`/`run_server.py` policy and LocalSystem service identity** — hard-won Windows constraints; preserve verbatim.
- **The security layers added by SEC003A–F** — including block-instead-of-delete and audit before/after snapshots; build on them.
- **bot_api's read-model and token scheme** — extend with write endpoints; do not replace with webhook/Mini-App auth schemes prematurely.
- **Staging-first release culture and per-task release docs** — the audit's confidence in this codebase rests largely on this practice.

---

## 15. Questions For The Project Owner

Only items that change roadmap ordering or implementation choices:

1. **Production notification link:** open the Telegram message from the 2026-06-10 production smoke test — does its link point to `:5051`? (Determines whether FIX001A is a hotfix-now or next-release item.)
2. **Who may approve spare-part requests?** Today: admins only. For BOT004, should designated non-admin reviewers exist (per-org reviewer role), or is admin-only correct for the foreseeable future?
3. **Is "return for revision" wanted** as a real status in the spare-parts workflow, or should the dormant event type be removed?
4. **`tg_notifications` semantics:** should an admin with notifications off stop receiving new-request fanout, or is fanout mandatory for admins regardless of preference?
5. **Are any fuel v1 pages still used by anyone** (old bookmarks)? If provably not, v1 templates can be removed in a cleanup release and the tables documented as frozen.
6. **Backup destinations:** confirm the scheduled task's actual `--dest-dir` (C: vs D:) so the script default and the dashboard indicator can be aligned with one declared truth.
7. **Server internet egress for browsers:** can client PCs reach Google Fonts? (Decides self-hosting vs accepted fallback in UX001E.)
8. **Audit-log and Topaz-transaction retention:** is indefinite growth acceptable for now, or should OPS phases include a retention policy (e.g., archive audit rows > 12 months)?
9. **Timing of the Cloudflare-Tunnel exposure:** if it is within ~2 months, SEC004 should be scheduled immediately after BOT004; if later, it can stay deferred.
10. **Was the leaked staging bot token rotated in BotFather?** (Outside this ZIP; closing the loop on project history.)

---

## 16. Suggested Next Prompt For Implementation

Do not implement yet. The next implementation prompt should be **FIX001 — Production Correctness Hotfix**, scoped exactly to:

1. `bot003_outbox_worker.py`: read `APP_PUBLIC_BASE_URL` from the environment (default to the current production web URL), build the deep link from it, and HTML-escape all payload-derived fields in `_build_notification_text`. No other behavior changes.
2. `app.py`: replace the three functional mojibake strings (login message at line 65, copy-previous-day `idle_reason` at line 964, `app_modules` seed names at lines 1992–1997) with correct Cyrillic; optionally clean the 16 mojibake comment lines in the same pass.
3. A registry-recorded, idempotent one-time data-fix script with a mandatory `--report` (count-only) mode and an `--apply` mode that updates existing corrupted `idle_reason` values to the correct text, preceded by the standard backup.

The prompt must require: full files (not fragments); `py_compile` and app-import checks; staging deployment with a scripted validation (trigger one staging notification containing a `<` character in an org name; verify link host/port and message delivery; run the data-fix in report mode against a staging copy); explicit production rollout steps with backup paths; and rollback notes (file revert + restart TransportBot003 / TransportReport; DB restore only if `--apply` was run). After FIX001 is verified in production, the following prompt should be OPS001A (SQLite pragmas), then BOT004A planning.

---

*End of EXTAUDIT001 report. All findings above derive from static inspection of the `eaaaf32` source ZIP; items tagged [runtime-verify] must be confirmed on the live environment before being treated as production facts.*
