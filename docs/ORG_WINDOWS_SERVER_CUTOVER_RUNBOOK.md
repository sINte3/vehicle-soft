# ORG_WINDOWS_SERVER_CUTOVER_RUNBOOK.md — Vehicle Soft

Task ID: TASK-DEPLOY-005E  
Created: 2026-05-23  
Prerequisite: Staging QA at `http://10.103.25.14:5051` PASSED (see `docs/ORG_WINDOWS_SERVER_STAGING_RUNBOOK.md`)

---

## CUTOVER COMPLETION RECORD — 2026-05-24

**Status: COMPLETED**

### Old production (stopped)

| Item | Value |
|---|---|
| Server | `10.103.25.200` (old workstation) |
| URL | `http://10.103.25.200:5050` |
| `TransportReport` service state | **STOPPED** |
| Final online backup | `C:\transport-report-backups\daily\transport_20260523_233516.db` |
| Backup source size | 46,809,088 bytes |
| Backup dest size | 46,809,088 bytes |
| Backup integrity check | ok |
| Cold copy (post-stop snapshot) | `C:\transport-report\instance\transport.db.backup_final_before_server_cutover` |

### New production (active)

| Item | Value |
|---|---|
| Server | `srv-yoqsh` (`10.103.25.14`) |
| URL | `http://10.103.25.14:5050` |
| `TransportReport` service state | **RUNNING** |
| DB copied from | `C:\temp\transport_final_cutover.db` |
| DB file size | 46,809,088 bytes |
| DB `users` count | 2 |
| DB `equipment` count | 336 |
| DB `fuel_transactions2` count | 391,284 |
| DB `schema_migrations` count | 10 |
| Production backup wrapper | `C:\transport-report\backup_production_db.bat` |
| Backup task | `TransportDBBackupProduction` — daily 02:00, SYSTEM, state: Ready |
| Backup destination | `D:\transport-report-backups\production\daily\` |
| First backup on new server | `transport_20260523_235432.db`, 46,809,088 bytes, integrity ok |
| Firewall rule | `VehicleSoft-Production-5050-LAN` — TCP 5050, remoteip 10.103.0.0/16 |
| Production QA result | **PASS** — admin, operator, Excel, Wialon, Fuel/АЗС all OK; no log errors |

### Topaz switch (completed)

| Item | Value |
|---|---|
| Topaz agent file | `C:\topaz_agent.py` (task name: `TopazFuelAgent`) |
| `API_URL` | `http://10.103.25.14:5050/fuel/api/fuel_sync` |
| `API_PING` | `http://10.103.25.14:5050/fuel/api/fuel_ping` |
| Ping test | Status 200 — OK |
| Auth test | Status 200 |
| First sync | Firebird connected OK — no new transactions — done |
| 401 / 500 / Traceback | None reported |
| New server logs after switch | No visible errors |

### Anti split-brain instruction

**CRITICAL: Do NOT restart `TransportReport` on old workstation (`10.103.25.200`) unless a rollback is explicitly required and authorized.**

Starting the old service while users are on the new server will create split-brain data. Data entered on both systems simultaneously cannot be merged automatically — manual DB reconciliation by the project maintainer is required.

### Rollback status

- Old workstation (`10.103.25.200`) is rollback standby only — `TransportReport` service is STOPPED.
- If any manual entries or Topaz syncs were made on the new server after cutover, rollback requires an explicit DB reconciliation decision before starting the old service.
- Do not merge DBs automatically. Contact the project maintainer before any database changes.

---

## A. Purpose and scope

Move production from:

| Item | Old workstation |
|---|---|
| Server | `10.103.25.200` |
| URL | `http://10.103.25.200:5050` |
| Path | `C:\transport-report` |
| Service | `TransportReport` |
| DB | `C:\transport-report\instance\transport.db` |

To:

| Item | New organization server (`srv-yoqsh`) |
|---|---|
| Server | `10.103.25.14` |
| URL | `http://10.103.25.14:5050` |
| Path | `C:\transport-report` |
| Service | `TransportReport` |
| DB | `C:\transport-report\instance\transport.db` |

Rules:

- Old workstation stays stopped and available as rollback standby.
- No business data should be entered during the cutover window.
- Cutover must be done after working hours (maintenance window).
- Do not delete staging (`C:\transport-report-staging`) until production on the new server is verified.

---

## B. Preconditions

Verify ALL of the following before starting. Do not proceed if any item is not met.

- [ ] Staging at `http://10.103.25.14:5051` has passed full QA (all items in `docs/ORG_WINDOWS_SERVER_STAGING_RUNBOOK.md` Section 4).
- [ ] `github.com/sINte3/vehicle-soft` `origin/main` is up to date on the workstation:
      `git status` must show "nothing to commit, working tree clean, up to date with origin/main".
- [ ] Automated backup `TransportDBBackup` is active and a recent backup file exists in `C:\transport-report-backups\daily\`.
- [ ] Staging backup task `TransportDBBackupStaging` is active on `srv-yoqsh`.
- [ ] Operator has Administrator access on both machines.
- [ ] All users have been notified of the maintenance window and told NOT to log in.
- [ ] Cutover window is after working hours (e.g., after 19:00 local time).

---

## C. Recommended production paths on new server (`srv-yoqsh`)

| Item | Path |
|---|---|
| Production project path | `C:\transport-report` |
| Production DB | `C:\transport-report\instance\transport.db` |
| Service log | `C:\transport-report\logs\service.log` |
| Error log | `C:\transport-report\logs\error.log` |
| Backup folder | `D:\transport-report-backups\production\daily` |
| Service name | `TransportReport` |
| Production URL | `http://10.103.25.14:5050` |

The project path on the new server is the same as on the old workstation (`C:\transport-report`). This keeps NSSM configuration consistent and simplifies rollback.

---

## D. Pre-cutover checklist on old workstation (10.103.25.200)

Run all steps on the old production workstation before stopping the service.

### D.1 — Confirm git status

```cmd
cd C:\transport-report
git status
```

Expected: "nothing to commit, working tree clean"

If uncommitted changes exist: commit or stash before proceeding.

### D.2 — Run a final online backup

```cmd
cd C:\transport-report
backup_transport_db.bat
```

Wait for: `Backup completed successfully.`

Do not continue until this message appears.

### D.3 — Verify backup file

```cmd
dir C:\transport-report-backups\daily\ /O-D
```

Confirm the newest file was just created (today's date/time, ~46 MB).

### D.4 — Stop production service on workstation

```cmd
cd C:\transport-report
nssm.exe stop TransportReport
sc query TransportReport
```

Expected: `STATE: STOPPED`

The application is now unavailable at `http://10.103.25.200:5050`. The cutover window has begun.

### D.5 — Cold copy of DB after service stop (extra rollback snapshot)

The online backup from D.2 is already safe. This extra cold copy is an additional rollback snapshot taken after the service is stopped, when no WAL pages can be written.

```cmd
copy /Y C:\transport-report\instance\transport.db C:\transport-report\instance\transport.db.backup_final_before_server_cutover
```

This provides a second rollback option independent of the backup folder.

---

## E. Transfer final DB to new server (`srv-yoqsh`)

### E.1 — Copy final DB to new server

Transfer the newest backup file from the workstation to the new server.

- Source (workstation): newest file in `C:\transport-report-backups\daily\`
- Destination (server): `C:\temp\transport_final_cutover.db`

Use Windows file sharing, RDP clipboard, or any available transfer method (shared network drive, USB if on-premises).

On the new server, create the temp folder if needed:

```cmd
if not exist C:\temp mkdir C:\temp
```

### E.2 — Inspect `C:\transport-report` on new server

Before cloning, check whether `C:\transport-report` already exists:

```cmd
dir C:\
```

- If `C:\transport-report` does NOT exist: proceed to E.3 (clone from GitHub).
- If `C:\transport-report` already exists: STOP and inspect its contents.
  Do NOT blindly overwrite. Determine what it contains before deciding whether to rename, delete, or reuse.

### E.3 — Clone production project from GitHub

If `C:\transport-report` does not exist on the new server:

```cmd
git clone https://github.com/sINte3/vehicle-soft.git C:\transport-report
```

Confirm the clone completes without errors before continuing.

---

## F. Set production environment variables on new server

Open CMD as Administrator on `srv-yoqsh`.

```cmd
setx SECRET_KEY "REPLACE-WITH-PRODUCTION-SECRET-KEY" /M
setx FUEL_API_TOKEN "REPLACE-WITH-PRODUCTION-TOPAZ-TOKEN" /M
setx FLASK_ENV "sqlite_prod" /M
setx PORT "5050" /M
setx HOST "0.0.0.0" /M
```

**Warnings:**

- Replace `REPLACE-WITH-PRODUCTION-SECRET-KEY` and `REPLACE-WITH-PRODUCTION-TOPAZ-TOKEN` with real values.
- Do NOT paste real secret values into documentation or chat messages.
- `FUEL_API_TOKEN` must match the token configured in the Topaz agent. Topaz agent will be reconfigured in Section M.
- `SECRET_KEY` rotation logs out all active sessions — acceptable during a maintenance window.
- Open a new CMD window after running `setx` to pick up the new environment variables.

Verify in the new CMD window:

```cmd
echo %SECRET_KEY%
echo %FUEL_API_TOKEN%
echo %FLASK_ENV%
echo %PORT%
echo %HOST%
```

---

## G. Install dependencies and copy DB on new server

```cmd
cd C:\transport-report
"C:\Program Files\Python314\python.exe" -m pip install -r requirements.txt
```

Create `instance\` folder if missing:

```cmd
if not exist instance mkdir instance
```

Copy the final cutover DB:

```cmd
copy /Y C:\temp\transport_final_cutover.db C:\transport-report\instance\transport.db
```

Run syntax checks (no output = PASS):

```cmd
"C:\Program Files\Python314\python.exe" -m py_compile app.py models.py config.py run_server.py fuel_routes.py spare_parts.py wialon_import.py workload_report.py translations.py backup_transport_db.py
```

Run import check:

```cmd
"C:\Program Files\Python314\python.exe" -c "from app import app; print('APP IMPORT OK')"
```

Expected: `APP IMPORT OK`

---

## H. Verify database on new server

Run this read-only count query:

```cmd
"C:\Program Files\Python314\python.exe" -c "import sqlite3; c=sqlite3.connect('instance/transport.db'); print('users:', c.execute('select count(*) from users').fetchone()[0]); print('equipment:', c.execute('select count(*) from equipment').fetchone()[0]); print('fuel_transactions2:', c.execute('select count(*) from fuel_transactions2').fetchone()[0]); print('schema_migrations:', c.execute('select count(*) from schema_migrations').fetchone()[0]); c.close()"
```

Expected counts (staging baseline 2026-05-23 — actual cutover values will be higher for live tables):

| Table | Staging baseline | Minimum expected |
|---|---|---|
| users | 3 | ≥ 3 |
| equipment | 336 | 336 |
| fuel_transactions2 | 391,284 | ≥ 391,284 |
| schema_migrations | 10 | 10 |

If any count is 0 or obviously wrong: STOP. Do not install the service. Rollback to workstation (Section O).

---

## I. Create production backup wrapper on new server

Create the file `C:\transport-report\backup_production_db.bat`:

```bat
@echo off
cd /d C:\transport-report
"C:\Program Files\Python314\python.exe" backup_transport_db.py --source "C:\transport-report\instance\transport.db" --dest-dir "D:\transport-report-backups\production\daily"
if errorlevel 1 (
    echo Backup FAILED. See backup_transport_db.py output above.
    exit /b 1
)
echo Backup completed successfully.
exit /b 0
```

Create backup destination folder:

```cmd
if not exist D:\transport-report-backups\production\daily mkdir D:\transport-report-backups\production\daily
```

Test the backup wrapper immediately:

```cmd
cd C:\transport-report
backup_production_db.bat
dir D:\transport-report-backups\production\daily\ /O-D
```

Expected: `Backup completed successfully.` and a new ~46 MB file in the folder.

Create Task Scheduler task for automated daily production backup (run CMD as Administrator):

```cmd
schtasks /create /tn "TransportDBBackupProduction" /tr "C:\transport-report\backup_production_db.bat" /sc daily /st 02:00 /ru SYSTEM /f
schtasks /run /tn "TransportDBBackupProduction"
dir D:\transport-report-backups\production\daily\ /O-D
```

Confirm a second file appears after the task runs.

---

## J. Create production NSSM service on new server

Open CMD as Administrator on `srv-yoqsh`.

Create `logs\` folder and copy `nssm.exe` from staging:

```cmd
cd C:\transport-report
if not exist logs mkdir logs
copy C:\transport-report-staging\nssm.exe C:\transport-report\nssm.exe
```

Install the `TransportReport` service:

```cmd
nssm.exe install TransportReport "C:\Program Files\Python314\python.exe" "run_server.py"
nssm.exe set TransportReport AppDirectory "C:\transport-report"
nssm.exe set TransportReport DisplayName "Vehicle Soft Production"
nssm.exe set TransportReport Description "Vehicle Soft production on organization server"
nssm.exe set TransportReport Start SERVICE_AUTO_START
nssm.exe set TransportReport AppEnvironmentExtra "FLASK_ENV=sqlite_prod" "PORT=5050" "HOST=0.0.0.0"
nssm.exe set TransportReport AppStdout "C:\transport-report\logs\service.log"
nssm.exe set TransportReport AppStderr "C:\transport-report\logs\error.log"
nssm.exe set TransportReport AppRotateFiles 1
nssm.exe set TransportReport AppRotateBytes 1048576
nssm.exe start TransportReport
sc query TransportReport
```

Expected: `STATE: RUNNING`

Check logs immediately for errors:

```cmd
type C:\transport-report\logs\service.log
type C:\transport-report\logs\error.log
```

No Python traceback should appear in `error.log`. If a traceback appears, the service will stop — fix the issue before continuing.

---

## K. Windows Firewall on new server

Allow port 5050 from the internal network:

```cmd
netsh advfirewall firewall add rule name="VehicleSoft-Production-5050-LAN" dir=in action=allow protocol=TCP localport=5050 remoteip=10.103.0.0/16
```

Verify:

```cmd
netsh advfirewall firewall show rule name="VehicleSoft-Production-5050-LAN"
```

---

## L. Production verification

Open `http://10.103.25.14:5050` in a browser and run the full manual QA checklist:

- [ ] Login as admin — dashboard loads.
- [ ] Login as operator — dashboard loads.
- [ ] Dashboard shows correct equipment count.
- [ ] `/entry` — daily entry form loads with all 9 equipment categories.
- [ ] `/report` — report page loads; generate a one-day report; download Excel file.
- [ ] `/wialon` — Wialon import page loads.
- [ ] `/fuel/` — fuel dashboard loads with stations and balances.
- [ ] Module permissions — non-admin user cannot access a disabled module (403 returned).
- [ ] Language switch UZ/RU — labels change throughout the UI.
- [ ] Logs — `C:\transport-report\logs\service.log` and `error.log` show no tracebacks.
- [ ] Backup task — `D:\transport-report-backups\production\daily\` contains a recent backup file.

Do NOT proceed to Topaz switch (Section M) until all items above are checked.

---

## M. Topaz switch

Perform this step only after Section L verification passes.

**Current Topaz configuration (pointing to old workstation):**

| Item | URL |
|---|---|
| Sync endpoint | `http://10.103.25.200:5050/fuel/api/fuel_sync` |
| Ping endpoint | `http://10.103.25.200:5050/fuel/api/fuel_ping` |

**New Topaz configuration (pointing to new server):**

| Item | URL |
|---|---|
| Sync endpoint | `http://10.103.25.14:5050/fuel/api/fuel_sync` |
| Ping endpoint | `http://10.103.25.14:5050/fuel/api/fuel_ping` |

Steps:

1. Verify the ping endpoint on the new server responds:
   Open `http://10.103.25.14:5050/fuel/api/fuel_ping`
   Expected: JSON response with `status: ok`.

2. Update Topaz agent configuration:
   - Change sync URL to `http://10.103.25.14:5050/fuel/api/fuel_sync`.
   - If the `FUEL_API_TOKEN` was changed, update the token in the Topaz agent as well.
   - Restart the Topaz agent.

3. Monitor logs after first sync:

```cmd
type C:\transport-report\logs\service.log
```

Look for successful sync entries (HTTP 200). No 401 (token mismatch) or 500 (server error).

4. After first successful Topaz sync is confirmed in the logs, the cutover is complete.

---

## N. User communication

After Section L (production verification) passes:

- Notify all users of the new application address: `http://10.103.25.14:5050`
- Ask users to update their bookmarks.
- Warn users explicitly: do NOT use the old address `http://10.103.25.200:5050`.
  The old service is stopped. Entering data on a stopped or accidentally restarted old server
  would cause split-brain data loss (see Section P).

---

## O. Rollback plan

### If new server fails BEFORE Topaz switch (Section M not started)

The old workstation has the same data as of cutover backup (service is stopped, no new data entered since D.4).

On old workstation (10.103.25.200):

```cmd
cd C:\transport-report
nssm.exe start TransportReport
sc query TransportReport
```

Verify `http://10.103.25.200:5050` is accessible.
Notify users to continue using the old URL.
No DB restore is needed — workstation DB was not modified after the service was stopped.

### If new server fails AFTER Topaz switch (Section M completed)

Topaz may have sent new fuel sync data to the new server after the switch.

Steps:

1. Switch Topaz agent URL back to `http://10.103.25.200:5050/fuel/api/fuel_sync` and restart the Topaz agent.

2. Start the old workstation service:

```cmd
cd C:\transport-report
nssm.exe start TransportReport
sc query TransportReport
```

3. Assess data loss:
   - If Topaz synced new fuel data to the new server after cutover, that data is not on the workstation.
   - If manual entries were made on the new server but not on the workstation, those entries are lost on rollback.
   - Decide whether a DB copy from new server to workstation is practical, based on how much data was entered.

4. After the old workstation is serving users again, treat the rollback as complete and plan a second cutover attempt.

---

## P. Anti split-brain warning

**CRITICAL: Never allow users to enter data into both old and new production systems at the same time.**

Rules:

- Old production (`10.103.25.200:5050`) must be STOPPED before the final DB is copied to the new server (Step D.4).
- Users must not log into the old address after the cutover window starts.
- After successful cutover, old production service must remain STOPPED — not just idle, but stopped.
- If the old service is accidentally started after cutover, users might enter data into a stale copy of the DB. That data will be silently lost and will never appear on the new server.

If split-brain is suspected (both systems running simultaneously with users on both):

- Stop both services immediately.
- Determine which DB has the latest data by checking fuel transaction timestamps and manual entry dates.
- Do NOT merge the DBs automatically. Manual reconciliation is required.
- Contact the project maintainer before making any database changes.

---

## Q. Cutover completion record

Fill in after the cutover is complete:

| Item | Value |
|---|---|
| Date/time of cutover | 2026-05-23 (maintenance window); recorded 2026-05-24 |
| Operator who performed cutover | Умид Байбутаев |
| Final DB backup file used for transfer | `transport_20260523_233516.db` (46,809,088 bytes, integrity ok) |
| users count on new server | 2 |
| equipment count on new server | 336 |
| fuel_transactions2 count on new server | 391,284 |
| schema_migrations count on new server | 10 |
| `http://10.103.25.14:5050` QA result | **PASS** |
| Topaz first sync confirmed | **YES** |
| Topaz first sync date/time | 2026-05-23 (after Topaz agent pointed to new server) |
| Old workstation service state | **STOPPED** (rollback standby — do not restart without authorization) |
| Notes | FUEL_API_TOKEN transferred; backup task TransportDBBackupProduction active; no 401/500/errors reported |

---

## R. Post-cutover tasks

After the cutover is verified and Topaz sync is confirmed:

1. Ensure Topaz agent is using the canonical endpoint:
   `http://10.103.25.14:5050/fuel/api/fuel_sync`
   (Not `/api/fuel_sync` — that is the legacy alias. Remove the alias from `app.py` once confirmed.)

2. Update internal bookmarks and any documentation that references `10.103.25.200`.

3. Monitor `logs\service.log` and `logs\error.log` daily for the first week.

4. Keep old workstation as standby for at least 30 days after successful cutover, then decommission or repurpose.

5. Update `docs/DEPLOYMENT_PLAN.md` to reflect the new production URL.

6. After stabilization, proceed to `TASK-DEPLOY-006` (PostgreSQL migration research) if desired.
