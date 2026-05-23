# DEPLOYMENT_PLAN.md — GitHub Migration and Hosting Plan

Task ID: TASK-DEPLOY-001  
Completed: 2026-05-23 (planning and audit only — no code, database, or service changes)

---

## 1. Current Deployment Model

| Aspect | Detail |
|---|---|
| OS | Windows 10 / Windows Server (NSSM, Waitress, Python 3.14) |
| Python | `C:\Program Files\Python314\python.exe` |
| Flask entry point | `app.py` (factory), started via `run_server.py` |
| WSGI server | Waitress `3.0.0`, 4 threads, port 5050 |
| Service manager | NSSM service `TransportReport`, auto-start |
| Database | SQLite `instance\transport.db` (44.5 MB, ~391 K fuel transactions) |
| Backups present | `instance\transport.db.backup`, `transport.db.backup_v2`, `transport.db.backup_before_ops001`, `transport.db.backup_before_ops002b` |
| Reports folder | `reports\` — 63 generated Excel files (~70 MB), not version-controlled |
| Logs | `logs\error.log` (883 KB), `logs\service.log` (24 KB), rotated by NSSM |
| Static | `static\` — CSS, JS, images (committed) |
| Templates | `templates\` — Jinja2 HTML (committed) |
| Config | `config.py` — `SqliteProductionConfig` is the production default |
| Required env vars | `SECRET_KEY` (fail-fast without it), `FUEL_API_TOKEN` (required for Topaz sync) |
| Optional env vars | `FLASK_ENV` (default: `sqlite_prod`), `HOST`, `PORT` |
| Application URL | `http://10.103.25.200:5050` (LAN only) |
| Install scripts | `install_service.bat` (NSSM installer), `start.bat` (dev/test) |
| Secret template | `.env.example` (exists; contains only placeholders, no real secrets) |
| Python dependencies | 6 packages: flask, flask-sqlalchemy, flask-login, openpyxl, waitress, werkzeug |
| Archive folder | `Archive\` — 16 historical version ZIPs (manual release archives) |

### Current availability risk

The application runs on a single office workstation. Any power outage, Windows freeze,
or hardware failure makes the system unavailable to all users. This is the primary
driver for the deployment migration.

---

## 2. What Must NOT Be Committed to GitHub

### Absolute exclusions — secrets and operational data

| Path | Reason |
|---|---|
| `instance\transport.db` | Production database (44.5 MB, personal/operational data) |
| `instance\transport.db.backup*` | All database backups |
| `instance\old_transport.db` | Legacy database |
| `old_transport.db` | Root-level legacy DB copy |
| `.env` | Would contain real `SECRET_KEY` and `FUEL_API_TOKEN` |

### Generated output

| Path | Reason |
|---|---|
| `reports\*.xlsx` | Generated Excel reports — 63 files, runtime output not source code |
| `logs\*.log` | NSSM runtime logs — runtime output not source code |

### Historical archives

| Path | Reason |
|---|---|
| `Archive\*.zip` | 16 manual release ZIPs — replaced by git history after migration |

### Runtime artifacts

| Path | Reason |
|---|---|
| `__pycache__\` | Compiled Python bytecode |
| `*.pyc` | Python compiled files |

### Binaries

| Path | Reason |
|---|---|
| `nssm.exe` | Third-party binary (download from https://nssm.cc/download) |

### Already safe to commit

- `.env.example` — exists, contains only placeholders (confirmed no real secrets)
- All `*.py` files — secrets removed in TASK-SEC-002; no hardcoded production keys
- `install_service.bat`, `start.bat` — contain only default `admin123` echo (not a secret; must be changed post-install per deployment docs)

---

## 3. Proposed .gitignore

```gitignore
# Database — NEVER commit production data
instance/transport.db
instance/transport.db.backup*
instance/old_transport.db
old_transport.db

# Generated reports
reports/*.xlsx
reports/*.xls

# Logs
logs/*.log
logs/*.txt

# Historical release archives (replaced by git history)
Archive/

# Environment variables and secrets
.env

# Python runtime artifacts
__pycache__/
*.py[cod]
*.pyo
*.pyd

# Binary tools — download separately from https://nssm.cc/download
nssm.exe

# Windows desktop artifacts
Thumbs.db
Desktop.ini

# IDE project files
.vscode/
.idea/
*.swp
*.swo
```

---

## 4. GitHub Repository Structure

The repository should contain exactly the source code needed to deploy and run the application.
Items excluded by `.gitignore` are not listed.

```
transport-report/
├── .gitignore                           ← TO CREATE (TASK-DEPLOY-002)
├── .env.example                         ← already exists
├── README.md
├── UPGRADE_TO_V3.md
├── PROMPT_DEPLOY001_GITHUB_HOSTING_PLAN.md
│
├── app.py                               ← app factory, core routes
├── models.py                            ← SQLAlchemy models
├── config.py                            ← multi-environment config
├── run_server.py                        ← Waitress production entry point
├── translations.py                      ← UZ/RU translation dictionary
├── migration_utils.py                   ← migration registry helpers
├── requirements.txt
│
├── excel_export.py                      ← main Excel report
├── excel_daily_activity.py              ← daily activity report
├── wialon_import.py                     ← Wialon ZIP/CSV import
├── workload_report.py                   ← workload report
├── fuel_routes.py                       ← fuel/Topaz blueprint
├── spare_parts.py                       ← spare parts blueprint
│
├── init_data.py                         ← initial reference data seeder
├── add_boshqa_column.py                 ← utility migration helper
│
├── migrate_000_migration_registry.py    ← bootstrap (run on production)
├── migrate_001_backfill_historical_registry.py
├── migrate.py                           ← historical (LIKELY_APPLIED)
├── migrate_equipment.py                 ← historical (LIKELY_APPLIED)
├── migrate_worktypes.py                 ← historical (LIKELY_APPLIED)
├── migrate_to_v3.py                     ← historical (CONFIRMED_APPLIED)
├── migrate_add_wialon.py                ← historical (CONFIRMED_APPLIED)
├── migrate_v42.py                       ← historical (LIKELY_APPLIED)
├── migrate_to_v45.py                    ← historical (CONFIRMED_APPLIED)
├── migrate_v46.py                       ← historical (CONFIRMED_APPLIED)
├── migrate_v47.py                       ← OBSOLETE warning added
├── migrate_fuel_v2.py                   ← historical (CONFIRMED_APPLIED)
├── migrate_tasks_abc3.py                ← historical (CONFIRMED_APPLIED)
├── migrate_categories_v9.py             ← historical (LIKELY_APPLIED)
├── migrate_equipment_excel.py           ← historical (CONFIRMED_APPLIED)
├── migrate_module_permissions.py        ← historical (CONFIRMED_APPLIED)
│
├── install_service.bat                  ← Windows NSSM service installer
├── start.bat                            ← dev/test quick start
│
├── templates/
│   ├── base.html
│   ├── login.html
│   ├── error.html
│   ├── *.html                           ← all Jinja2 templates
│   └── fuel/
│       └── *.html                       ← fuel module templates
│
├── static/
│   └── ...                              ← CSS, JS, images
│
└── docs/
    ├── PROJECT_BRIEF.md
    ├── ARCHITECTURE.md
    ├── DECISIONS.md
    ├── AGENT_STATE.md
    ├── TASKS.md
    ├── PROMPT_PROTOCOL.md
    ├── DEPLOYMENT_SECURITY.md
    ├── DEPLOYMENT_PLAN.md               ← this file
    ├── MIGRATIONS.md
    ├── MIGRATION_BACKFILL_ANALYSIS.md
    ├── QA_CHECKLIST.md
    └── UI_TRANSLATION_AUDIT.md
```

### Branching convention (proposed)

| Branch | Purpose |
|---|---|
| `main` | Production-ready code only. Tagged for each release. |
| `feature/TASK-XXX-description` | Feature or bugfix branch. Merged to `main` after QA. |
| `hotfix/TASK-XXX-description` | Emergency fix. Fast-tracked to `main`. |

One operator/developer working alone can simplify to just `main` + short-lived feature
branches. No mandatory code review gate needed at current team size.

---

## 5. Hosting Options Comparison

### Option A — Dedicated office mini-server + UPS

**Description**: Replace the current workstation with a dedicated mini-PC (Intel NUC,
Beelink, or similar) connected to a UPS.

| Factor | Assessment |
|---|---|
| One-time cost | ~$150–300 mini-PC + ~$50–80 UPS |
| Monthly cost | $0 (office electricity) |
| Availability | Eliminates power-off risk; local LAN access still requires VPN for remote |
| Internet access | Requires static office IP or dynamic DNS + port forwarding through ISP router |
| Windows/NSSM | No changes — identical environment |
| SQLite | Fine |
| Topaz/Wialon | No agent URL changes |
| Migration effort | Minimal: copy files, set env vars, install NSSM service |
| HTTPS | Requires additional reverse proxy setup if exposed to internet |

**Best for**: Eliminating the power/crash risk without changing anything else.
**Does not solve**: remote internet access without additional VPN or port-forwarding work.

---

### Option B — Windows VPS (recommended for Phase 2)

**Description**: Rent a Windows Server VPS from a provider (Timeweb, Reg.ru, Hetzner,
Contabo). Deploy the application identically to the current setup.

| Factor | Assessment |
|---|---|
| Monthly cost | ~$15–30/month |
| Availability | 99.9% uptime SLA |
| Internet access | Public IP from day one |
| Windows/NSSM | Identical — no code changes |
| SQLite | Fine at current scale |
| HTTPS | Requires Nginx for Windows or IIS as reverse proxy + SSL certificate |
| Topaz agent | Must update config with new IP/hostname |
| Wialon | No changes (browser-based upload) |
| Migration effort | Low: RDP, copy files, set env vars, install NSSM service |
| Firewall | Configure Windows Firewall: block port 5050 from internet, allow only via Nginx |

**Best for**: Rapid migration to internet-accessible hosting with zero code changes.
**Recommended** for Phase 2 (see Section 6).

---

### Option C — Linux VPS (recommended for Phase 3)

**Description**: Rent a Linux VPS (Ubuntu 22.04 LTS) on Hetzner, DigitalOcean, or Linode.
Run the Flask app via systemd + Waitress or Gunicorn behind Nginx.

| Factor | Assessment |
|---|---|
| Monthly cost | ~$5–10/month (cheaper than Windows VPS) |
| Availability | 99.9% uptime SLA |
| Internet access | Public IP from day one |
| HTTPS | Trivial: Nginx + certbot + Let's Encrypt (free, auto-renewed) |
| SQLite | Fine at current scale; easier PostgreSQL upgrade later |
| Encoding risk | Must test UTF-8 handling of Cyrillic filenames in reports/ and templates/ |
| Windows-specific | `.bat` files → shell scripts; NSSM → systemd; check Python 3.14 availability |
| Topaz agent | Must update config with new IP/hostname |
| Migration effort | Moderate: rewrite service installer for systemd, test all features |
| Future PostgreSQL | Easiest migration path |

**Best for**: Long-term production with minimal cost and maximum Linux tooling.
**Recommended** for Phase 3 after Windows VPS validates the deployment.

---

### Option D — PaaS (Render / Railway)

**Description**: Deploy directly to a managed platform via `git push`.

| Factor | Assessment |
|---|---|
| Monthly cost | $7–15/month (production tier) |
| HTTPS | Automatic |
| SQLite | **Incompatible**: ephemeral filesystem; data lost on redeploy |
| Required first | SQLite → PostgreSQL migration must be complete |
| Wialon uploads | Needs persistent file storage (S3 or mounted volume) |
| Topaz agent | Stable domain available |
| Migration effort | High: PostgreSQL migration + storage for uploads |

**Best for**: Phase 4 after PostgreSQL migration is complete and validated.
**Not recommended** until the database is migrated.

---

## 6. Recommended Phased Path

### Phase 1 — Git hygiene + private GitHub repository (this week)

Actions:
1. Create `.gitignore` — TASK-DEPLOY-002.
2. Scan all files for committed secrets before first push — TASK-DEPLOY-003.
3. Create a **private** GitHub repository.
4. Initial commit: all source code excluding `.gitignore` targets.
5. Push to `main` branch.
6. Tag the initial commit: `v1.0-production-2026-05-23`.

Result: the codebase is version-controlled. Updates can be delivered as a `git pull`
instead of a manual file copy.

---

### Phase 2 — Stable server: Windows VPS (next 1–2 months)

Actions:
1. Rent a Windows Server VPS (Windows Server 2022 recommended).
2. Install Python 3.14, configure system env vars via `setx /M`.
3. Clone the private GitHub repository.
4. Run `install_service.bat` to install NSSM and the `TransportReport` service.
5. Copy the current `instance\transport.db` to the VPS as the initial database.
6. Set up automated daily database backup — TASK-DEPLOY-004.
7. Configure Windows Firewall (block port 5050 directly from internet).
8. Install Nginx for Windows as a reverse proxy; obtain a free SSL certificate.
9. Update Topaz agent configuration to point to new server hostname/IP.
10. Test all modules before directing users to the new URL.

Result: the application is accessible from any internet-connected device via HTTPS.
The current office workstation can remain as a local backup.

---

### Phase 3 — HTTPS hardening + domain (within 3 months of Phase 2)

Actions:
1. Register a domain name (e.g., `transport.agrocluster.uz` or similar).
2. Point DNS A record to VPS IP.
3. Replace IP-based access with domain-based access.
4. Renew/automate SSL certificate.
5. Restrict admin routes to VPN or office IP allowlist (optional but recommended).
6. Add UptimeRobot or similar free monitoring.

---

### Phase 4 — Linux VPS + PostgreSQL (Full ERP stage)

Actions:
1. Provision Ubuntu 22.04 LTS VPS.
2. Write and test SQLite → PostgreSQL data migration — TASK-DEPLOY-006.
3. Deploy application with systemd + Waitress behind Nginx.
4. Set up pg_dump automated backups.
5. Cut over DNS from Windows VPS to Linux VPS.
6. Decommission Windows VPS.

---

## 7. Database Path

### Short term — SQLite with backup discipline

Current state: 44.5 MB, ~391 K fuel transactions. SQLite is completely adequate for
this usage pattern (low concurrency, single writer, small team).

Required discipline before any server move:

1. **Automated daily backup**: Windows Task Scheduler job:
   ```cmd
   copy "C:\transport-report\instance\transport.db" ^
        "D:\backups\transport_%date:~-4%%date:~3,2%%date:~0,2%.db"
   ```
2. Rotate: keep at least 7 daily backups.
3. Offsite copy: sync backup folder to a network share, cloud drive, or VPS.
4. On any migration: stop service → backup → run script → verify → start service.
5. Continue using `schema_migrations` registry for all future schema changes.

### Long term — PostgreSQL migration plan

`config.py` already has `ProductionConfig` with a PostgreSQL URI. The plumbing exists.

Migration plan outline (TASK-DEPLOY-006):

1. Audit all SQLAlchemy models in `models.py` for SQLite-specific constructs:
   - `TEXT` storage of booleans.
   - `AUTOINCREMENT` vs PostgreSQL `SERIAL`/`IDENTITY`.
   - Date/time timezone handling.
2. Install PostgreSQL (local or managed service).
3. Switch `FLASK_ENV=prod` and set `PG_HOST`, `PG_PORT`, `PG_DB`, `PG_USER`, `PG_PASS`
   environment variables. **Remove the `changeme` default in `ProductionConfig`** —
   currently `PG_PASS` has a fallback default that would be a security risk if left
   unchanged.
4. Write a one-time bulk migration script using Python `sqlite3` → `psycopg2`.
5. Test on a copy of production data. Verify row counts match.
6. Cut over: stop service → run migration → switch env → restart service.

Not urgent: current SQLite is stable and sufficient.

---

## 8. Internet Access and Security Requirements

### HTTPS

Required before exposing the application to the public internet.

- Linux VPS: Nginx + certbot (Let's Encrypt) — free, auto-renewed.
- Windows VPS: Nginx for Windows + ZeroSSL free certificate; or IIS reverse proxy.
- Waitress does not handle TLS natively. A reverse proxy is always required.

Nginx config sketch (Linux):
```nginx
server {
    listen 443 ssl;
    server_name transport.yourdomain.uz;

    ssl_certificate     /etc/letsencrypt/live/transport.yourdomain.uz/fullchain.pem;
    ssl_certificate_key /etc/letsencrypt/live/transport.yourdomain.uz/privkey.pem;

    location / {
        proxy_pass http://127.0.0.1:5050;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
    }
}

server {
    listen 80;
    server_name transport.yourdomain.uz;
    return 301 https://$host$request_uri;
}
```

### Domain

Register a domain and point an A record to the VPS IP. A subdomain works fine (e.g.,
`transport.yourdomain.uz`). No multi-domain setup needed at current scope.

### Firewall rules

| Rule | Direction | Action |
|---|---|---|
| Port 443 (HTTPS) | Inbound | Allow from all |
| Port 80 (HTTP redirect) | Inbound | Allow from all |
| Port 5050 (Waitress) | Inbound | **Block** — internal only, accessed via Nginx |
| Topaz agent source IP | Inbound to `/fuel/api/fuel_sync` | Allow — configure in Nginx |
| RDP (Windows VPS) | Inbound | Restrict to office IP only |
| SSH (Linux VPS) | Inbound | Restrict to office IP or key-based only |

### VPN / IP allowlist

Recommended as defense-in-depth before HTTPS is configured, and for admin routes:

- **Tailscale** or **WireGuard** — zero-cost, easy to configure for small teams.
- Nginx IP allowlist on `/admin/` routes:
  ```nginx
  location /admin/ {
      allow 10.103.25.0/24;   # office network
      deny all;
      proxy_pass http://127.0.0.1:5050;
  }
  ```

### SECRET_KEY

- Currently read from environment only (TASK-SEC-002 completed).
- Rotate if it was ever committed to any repository or shown in logs.
- Minimum 32 random bytes: `python -c "import secrets; print(secrets.token_hex(32))"`.
- Rotating invalidates all active user sessions (users are logged out once).

### FUEL_API_TOKEN

- Currently read from environment only (TASK-SEC-002 completed).
- Must match the token in the Topaz agent configuration.
- Rotate after any suspected exposure; update both server env var and Topaz agent config.

### Admin password policy

- Default `admin123` password is visible in `install_service.bat` echo lines (informational,
  not a real secret). Must be changed immediately after any fresh installation.
- Current application has no password complexity enforcement — operator self-discipline required.
- Recommended future work: add minimum 8-character validation on user create/edit form.

### Backups

Automated daily backup using Windows Task Scheduler (create a scheduled task):

```cmd
copy "C:\transport-report\instance\transport.db" ^
     "C:\backups\transport_%date:~-4%%date:~3,2%%date:~0,2%.db"
```

Retention: keep 7 daily backups minimum. Offsite: sync to a network share or cloud drive.

### Monitoring

- **UptimeRobot** free tier: HTTP check every 5 minutes; email/Telegram alert on down.
- Log rotation: already configured in NSSM (`AppRotateFiles 1`, 1 MB rotate threshold).
- Check `logs\error.log` periodically for recurring Python exceptions.

---

## 9. Topaz / Wialon Impact

### Topaz agent — URL update (required on any server move)

| Item | Current | After server move |
|---|---|---|
| Canonical endpoint | `POST /fuel/api/fuel_sync` | Same path, new server hostname/IP |
| Legacy alias | `POST /api/fuel_sync` (temporary, logs WARNING) | Same path, new server hostname/IP |
| Token | `FUEL_API_TOKEN` env var | Must be set on new server before starting service |

**Operator action on server move**:
1. Set `FUEL_API_TOKEN` on new server via `setx FUEL_API_TOKEN "token" /M`.
2. Update Topaz agent config: point URL to `http://new-server-ip:5050/fuel/api/fuel_sync`
   (or `https://new-domain/fuel/api/fuel_sync` after HTTPS is set up).
3. Restart the Topaz agent.
4. Monitor `logs\service.log` to confirm sync calls are arriving.

### Legacy `/api/fuel_sync` alias removal

Remove from `app.py` after all of these are confirmed:
1. All Topaz agent configs updated to `/fuel/api/fuel_sync`.
2. Server logs show zero calls to legacy path for at least 7 days.
3. Operator confirms all Topaz agent installs have been updated.

### Token rotation procedure

1. Generate new token: `python -c "import secrets; print(secrets.token_hex(32))"`.
2. Set on server: `setx FUEL_API_TOKEN "new-token" /M` + restart service.
3. Update Topaz agent config with new token + restart agent.
4. Verify sync in service log.

### Wialon import

Wialon is a manual browser upload (ZIP file → `/wialon/upload`). No agent or API call.
No network configuration change needed on server move. Operators simply use the new URL.

---

## 10. Concrete Task Breakdown

### TASK-DEPLOY-002 — GitHub repository hygiene

Priority: P1  
Depends on: TASK-DEPLOY-001 (this document)

Scope:
- Create `.gitignore` with content from Section 3 of this document.
- Verify all root-level files: confirm no database files, no `.env`, no large binaries in the commit.
- Confirm `.env.example` contains only placeholders (already confirmed).
- Note in deployment docs that `admin123` default password must be changed post-install.
- Create private GitHub repository.
- Initial commit + push to `main` branch.
- Tag `v1.0-production-2026-05-23`.

Acceptance criteria:
- `git status` shows clean working tree after `.gitignore` is applied.
- `instance/` and `reports/` are excluded.
- `git push` completes without `transport.db` included.

---

### TASK-DEPLOY-003 — .gitignore and secret scan

Priority: P0  
Must run before the first `git push`.

Scope:
- Scan all `.py` files for hardcoded passwords, tokens, IP addresses using grep or
  `truffleHog` / `git-secrets`.
- Verify `config.py`: `Config.SECRET_KEY` has no production fallback (confirmed post-TASK-SEC-002).
- Verify `config.py`: `ProductionConfig.PG_PASS` default is `changeme` — document that
  this must be replaced before enabling the `prod` config; not a current risk since
  production uses `sqlite_prod`.
- Verify `fuel_routes.py`: no hardcoded `API_TOKEN` (confirmed post-TASK-SEC-002).
- Check `init_data.py`: default `admin123` user seed — safe for a seed script but must
  be changed post-install; document this explicitly.
- Check `wialon_import.py` for any hardcoded Wialon API credentials.
- Check `install_service.bat`: sets `FLASK_ENV` and `PORT` via AppEnvironmentExtra but
  does NOT set `SECRET_KEY` — confirm this is intentional (operator sets it separately
  via `setx /M`) and document clearly.

Acceptance criteria:
- Zero secrets found in any file that will be committed.
- `truffleHog` or equivalent scan returns no high-entropy string hits in source files.
- Deployment docs updated with post-install password change requirement.

---

### TASK-DEPLOY-004 — Release package and backup procedure

Priority: P1

Scope:
- Document the step-by-step procedure for deploying an update from GitHub to the
  production server (stop service → pull/copy → migrate if needed → start service).
- Create an `update.bat` helper script (Windows CMD):
  ```cmd
  @echo off
  cd C:\transport-report
  nssm stop TransportReport
  git pull origin main
  nssm start TransportReport
  ```
  Note: only safe if no migration is needed. When a migration is required, migration
  must run before `nssm start`.
- Automate daily `transport.db` backup via Windows Task Scheduler.
  Task: runs daily at 02:00, copies `instance\transport.db` to `D:\backups\`.
  Retention: keep last 7 days; delete older files.
- Document rollback procedure:
  1. Stop service.
  2. Restore backup: `copy /Y D:\backups\transport_YYYYMMDD.db instance\transport.db`.
  3. Revert code: `git checkout main` or restore from ZIP.
  4. Start service.

Acceptance criteria:
- Operator can follow the procedure without a programmer present.
- Backup task confirmed running and backup files appear in target directory.
- Rollback procedure tested on a non-production copy.

---

### TASK-DEPLOY-005 — Staging VPS deployment

Priority: P2  
Depends on: TASK-DEPLOY-002, TASK-DEPLOY-003, TASK-DEPLOY-004

Scope:
1. Rent a Windows Server 2022 VPS (recommended: Timeweb, Hetzner, Contabo).
2. Install Python 3.14 on VPS.
3. Clone private GitHub repository to `C:\transport-report\`.
4. Set `SECRET_KEY` and `FUEL_API_TOKEN` via `setx /M`.
5. Copy current production `instance\transport.db` to VPS (one-time initial migration).
6. Run `install_service.bat` to configure NSSM.
7. Configure Windows Firewall: allow 80/443 from all; block 5050 from internet.
8. Install Nginx for Windows as reverse proxy; configure HTTPS.
9. Update Topaz agent config with new server URL.
10. Test all modules end-to-end (use `docs\QA_CHECKLIST.md`).
11. Set up automated daily backup task.
12. Set up UptimeRobot monitoring.
13. Announce new URL to users; redirect old LAN URL if needed.

Acceptance criteria:
- Full `QA_CHECKLIST.md` smoke test passes on VPS.
- Topaz sync arriving on new server (verified in `logs\service.log`).
- HTTPS certificate valid; HTTP redirects to HTTPS.
- Automated daily backup confirmed running.

---

### TASK-DEPLOY-006 — PostgreSQL migration research

Priority: P3  
Blocker: not urgent; SQLite is stable at current scale.

Scope:
1. Audit `models.py` for SQLite-specific constructs:
   - Boolean columns stored as `TEXT` or `INTEGER`.
   - Datetime storage (naive vs aware).
   - `AUTOINCREMENT` mapping to PostgreSQL `SERIAL`/`IDENTITY`.
2. Review `ProductionConfig` in `config.py` for completeness.
3. Write a standalone migration script (`migrate_sqlite_to_postgres.py`):
   - Reads each table from SQLite using `sqlite3`.
   - Writes to PostgreSQL using `psycopg2`.
   - Reports row counts before/after.
4. Test on a copy of `transport.db` against a local PostgreSQL instance.
5. Verify:
   - `fuel_transactions2` (~391 K rows) migrates correctly and in acceptable time.
   - All SQLAlchemy relationships work under PostgreSQL.
6. Document the cutover procedure (stop service → migrate → switch `FLASK_ENV=prod` → restart).

Acceptance criteria:
- Row counts match between source SQLite and destination PostgreSQL.
- Application starts and all QA checklist items pass against PostgreSQL.
- Migration script is idempotent (safe to re-run without duplicating data).
- Document updated with exact psycopg2 dependency addition to `requirements.txt`.

---

## 11. Security Risk Register

| Risk | Severity | Status | Mitigation |
|---|---|---|---|
| Application on single workstation with no UPS | High | Open | Phase 2: Windows VPS |
| No HTTPS on LAN (HTTP only) | Medium | Open | Phase 2/3: Nginx + TLS |
| Admin password `admin123` not changed post-install | High | Open (procedure gap) | Add to deployment checklist in TASK-DEPLOY-003 |
| Legacy `/api/fuel_sync` alias left indefinitely | Low | Open | Remove after Topaz agent confirmed updated |
| `ProductionConfig.PG_PASS` has `changeme` default | Low | Open (not active config) | Replace when `prod` config is enabled |
| No CSRF protection on POST forms | Medium | Open | Future security task |
| No password complexity enforcement | Low | Open | Future improvement |
| Wialon import accepts arbitrary ZIP | Low | Open | File content validated in parser |
| No account lockout after failed logins | Low | Open | Future security task |
| SQLite has no encryption at rest | Low | Accepted | Low-sensitivity data; VPS disk encryption recommended |

---

## Summary

**Immediate action (Phase 1)**: Create `.gitignore`, scan for secrets, push to a private
GitHub repository. This is TASK-DEPLOY-002 + TASK-DEPLOY-003. No code changes, no service
changes, no database changes needed.

**Short-term action (Phase 2)**: Deploy to a Windows VPS. This solves the power/availability
problem and enables internet access. Requires Topaz agent URL update.

**Long-term (Phase 3–4)**: Linux VPS + HTTPS, then PostgreSQL. These are not urgent.
