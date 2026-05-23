# RELEASE_AND_BACKUP_PROCEDURE.md — Vehicle Soft

Task ID: TASK-DEPLOY-004  
Created: 2026-05-23  
Applies to: Windows production server, `C:\transport-report\`

---

## Purpose

This document defines:

- How to update the production application from the GitHub repository.
- How to back up the production database manually and automatically.
- How to verify that backups are working.
- How to restore the database from a backup.
- How to roll back a failed update.

Follow this document any time you deploy a code change to the production server.

---

## Current repository and release tag

| Item | Value |
|---|---|
| GitHub repository | https://github.com/sINte3/vehicle-soft (private) |
| First production tag | `v1.0-production-2026-05-23` |
| Default branch | `main` |
| Production server path | `C:\transport-report\` |
| Application URL | `http://10.103.25.200:5050` |
| Database | `C:\transport-report\instance\transport.db` |
| Daily backup location | `C:\transport-report-backups\daily\` |
| Pre-update backup location | `C:\transport-report-backups\before_update\` |

---

## Pre-update checklist

Before running any update, complete this checklist:

- [ ] Read the release notes or commit log on GitHub to understand what changed.
- [ ] Identify whether the release includes a migration script (`migrate_NNN_*.py`).
      If yes, read `docs\MIGRATIONS.md` before continuing.
- [ ] Confirm `SECRET_KEY` and `FUEL_API_TOKEN` are set on this server.
      (See `docs\DEPLOYMENT_SECURITY.md`.)
- [ ] Confirm a recent daily backup exists in `C:\transport-report-backups\daily\`.
      The pre-update script also creates a backup, but a recent daily backup is a second safety net.
- [ ] Notify users that a brief downtime is coming (service will be stopped during update).
- [ ] Close any open Excel reports downloaded from the application.

---

## Production update procedure

### Option A — Automated: use `update.bat`

`update.bat` is located in `C:\transport-report\`. It runs all steps in sequence and stops at any failure.

Open CMD as Administrator and run:

```cmd
cd C:\transport-report
update.bat
```

The script:
1. Verifies the working directory.
2. Creates a pre-update backup using the SQLite online backup API (`backup_transport_db.py`) in `C:\transport-report-backups\before_update\`.
3. Stops the `TransportReport` service.
4. Runs `git status` and `git pull --ff-only origin main`.
5. Runs Python syntax check on all main modules.
6. Runs application import check (`from app import app`).
7. Prints the migration warning and pauses — operator must confirm no migration is needed (or run migrations first).
8. Starts the `TransportReport` service.
9. Prints the backup file path and application URL.

If any step fails, the script exits immediately with a clear error message. The service remains stopped. See **Rollback procedure** below.

---

### Option B — Manual: step by step

Use this when `update.bat` cannot be used or when more control is needed.

Open CMD as Administrator in `C:\transport-report\`:

```cmd
cd C:\transport-report
```

#### Step 1: Create a manual backup

```cmd
backup_transport_db.bat
```

Confirm that the output says `SUCCESS` and shows the backup file path.

#### Step 2: Stop the service

```cmd
.\nssm.exe stop TransportReport
```

If `nssm.exe` is not in the project folder:

```cmd
net stop TransportReport
```

#### Step 3: Pull the latest code

```cmd
git status
git pull --ff-only origin main
```

If `git pull` fails (e.g., diverged history or authentication error), do NOT continue.
Start the service manually and investigate the git issue:

```cmd
.\nssm.exe start TransportReport
```

#### Step 4: Run syntax check

```cmd
"C:\Program Files\Python314\python.exe" -m py_compile app.py models.py config.py run_server.py fuel_routes.py spare_parts.py wialon_import.py workload_report.py translations.py
```

No output means all files passed. If a file is listed with an error, do NOT start the service.

#### Step 5: Run import check

```cmd
"C:\Program Files\Python314\python.exe" -c "from app import app; print('APP IMPORT OK')"
```

Must print `APP IMPORT OK`. If it prints an error, do NOT start the service.

#### Step 6: Run migrations if needed

If the release includes a migration script, run it now — BEFORE starting the service.
Follow `docs\MIGRATIONS.md` exactly. Do not skip the backup step (already done in Step 1).

#### Step 7: Start the service

```cmd
.\nssm.exe start TransportReport
```

If `nssm.exe` is not in the project folder:

```cmd
net start TransportReport
```

#### Step 8: Smoke test

Open `http://10.103.25.200:5050` in a browser and run through the smoke test below.

---

## Migration handling rule

**Migrations are NEVER automatic.**

- If a release includes a migration script (`migrate_NNN_*.py`), it must be run manually.
- Always run migrations AFTER stopping the service and BEFORE starting it.
- Always back up the database BEFORE running a migration.
- Follow `docs\MIGRATIONS.md` for the full procedure.
- If you are unsure whether a migration is needed, read the release notes or ask before continuing.

`update.bat` enforces this: it prints a warning and pauses before starting the service,
giving the operator a window to run migrations if needed.

---

## Rollback procedure

Use this procedure to undo a failed or broken update.

### Step 1: Stop the service (if running)

```cmd
cd C:\transport-report
.\nssm.exe stop TransportReport
```

If `nssm.exe` is not available:

```cmd
net stop TransportReport
```

### Step 2: Restore the database backup

Replace `transport_YYYYMMDD_HHMMSS_before_update.db` with the actual backup filename.
Check `C:\transport-report-backups\before_update\` for the correct filename.

```cmd
copy /Y "C:\transport-report-backups\before_update\transport_YYYYMMDD_HHMMSS_before_update.db" "C:\transport-report\instance\transport.db"
```

Confirm the command prints `1 file(s) copied.`

### Step 3: Revert code to the previous version

Option A — Revert using git (fast):

```cmd
cd C:\transport-report
git log --oneline -5
```

Find the commit hash of the previous known-good version, then:

```cmd
git checkout <previous-commit-hash> -- .
```

Or to revert all files to the last tagged release:

```cmd
git checkout v1.0-production-2026-05-23 -- .
```

Option B — Restore individual files from the Archive folder or a ZIP backup:
Copy the specific files that were updated.

### Step 4: Syntax check the reverted code

```cmd
"C:\Program Files\Python314\python.exe" -m py_compile app.py models.py config.py run_server.py fuel_routes.py spare_parts.py wialon_import.py workload_report.py translations.py
```

### Step 5: Start the service

```cmd
.\nssm.exe start TransportReport
```

### Step 6: Verify

Open `http://10.103.25.200:5050` and confirm login works and the dashboard loads.
Check `logs\error.log` for startup errors.

---

## Manual backup procedure

Run `backup_transport_db.bat` at any time to create a timestamped backup:

```cmd
cd C:\transport-report
backup_transport_db.bat
```

`backup_transport_db.bat` delegates to `backup_transport_db.py`, which uses the
SQLite online backup API (`sqlite3.Connection.backup()`). This produces a consistent
snapshot of the database even when the service is running and WAL mode is active.
The `.db-wal` and `.db-shm` sidecar files are NOT manually copied — the backup API
merges any pending WAL pages into the destination automatically.

Output example:
```
============================================================
 Transport DB Daily Backup
============================================================

============================================================
 Transport DB Backup  (SQLite online backup API)
============================================================
 Source : C:\transport-report\instance\transport.db
 Dest   : C:\transport-report-backups\daily\transport_20260523_020001.db

 Source size :  5,242,880 bytes

Running SQLite online backup...
 Dest size   :  5,242,880 bytes

Running integrity check on destination database...
 Integrity check : ok

SUCCESS: Backup written to:
         C:\transport-report-backups\daily\transport_20260523_020001.db
```

The script:
- Does NOT stop the service. The SQLite online backup API handles live consistency.
- Does NOT delete old backups.
- Performs a `PRAGMA integrity_check` on the destination after backup.
- Exits with code 1 and prints a clear error if the source is missing, the backup
  fails, the destination is empty, or the integrity check does not return `ok`.

You can also call `backup_transport_db.py` directly with custom arguments:

```cmd
"C:\Program Files\Python314\python.exe" backup_transport_db.py --dest-dir C:\my-backups\dir --suffix label
```

---

## Automated daily backup via Windows Task Scheduler

### Command-line setup (run CMD as Administrator):

```cmd
schtasks /create /tn "TransportDBBackup" /tr "C:\transport-report\backup_transport_db.bat" /sc daily /st 02:00 /ru SYSTEM /f
```

Parameters explained:
- `/tn "TransportDBBackup"` — task name.
- `/tr "C:\transport-report\backup_transport_db.bat"` — what to run.
- `/sc daily /st 02:00` — run every day at 02:00 (2 AM).
- `/ru SYSTEM` — run as SYSTEM account (has full local access).
- `/f` — overwrite if the task already exists.

### GUI setup (alternative):

1. Press `Win + R`, type `taskschd.msc`, press Enter.
2. Click **Create Basic Task...** in the right panel.
3. Name: `TransportDBBackup`. Click Next.
4. Trigger: **Daily**. Click Next. Set Start time: `02:00:00`. Click Next.
5. Action: **Start a program**. Click Next.
6. Program/script: `C:\transport-report\backup_transport_db.bat`
7. Start in: `C:\transport-report`
8. Click Next → Finish.

### Verify the task was created:

```cmd
schtasks /query /tn "TransportDBBackup" /fo LIST
```

### Run the task immediately to test it:

```cmd
schtasks /run /tn "TransportDBBackup"
```

Then check that a new file appears in `C:\transport-report-backups\daily\`.

### Modify the schedule (example: change to 03:00):

```cmd
schtasks /change /tn "TransportDBBackup" /st 03:00
```

### Delete the task (if needed):

```cmd
schtasks /delete /tn "TransportDBBackup" /f
```

---

## How to verify backups

After the task runs, verify a backup file was created:

```cmd
dir C:\transport-report-backups\daily\
```

Look for a file named `transport_YYYYMMDD_HHMMSS.db` with today's date.

Check the file size is similar to the live database:

```cmd
dir C:\transport-report\instance\transport.db
dir C:\transport-report-backups\daily\
```

Both files should be approximately the same size. The backup uses the SQLite online
backup API, which copies all committed pages including any pending WAL data, so the
destination size reflects the full database state at the time of backup.

`backup_transport_db.py` exits with code 1 if the destination is 0 bytes or if
`PRAGMA integrity_check` does not return `ok`. A zero-byte file will never be left
silently — the script always reports failure and sets a non-zero exit code.

---

## How to restore `instance\transport.db` from backup

Use this procedure to replace the production database with a backup copy.
This is destructive — any data entered after the backup was created will be lost.

### Step 1: Stop the service

```cmd
cd C:\transport-report
.\nssm.exe stop TransportReport
```

### Step 2: Copy the backup over the production database

Replace the filename with the actual backup you want to restore:

```cmd
copy /Y "C:\transport-report-backups\daily\transport_20260523_020001.db" "C:\transport-report\instance\transport.db"
```

Confirm: `1 file(s) copied.`

### Step 3: Verify the restored database (optional but recommended)

```cmd
"C:\Program Files\Python314\python.exe" -c "import sqlite3; c=sqlite3.connect('instance/transport.db'); print('tables:', [r[0] for r in c.execute(\"SELECT name FROM sqlite_master WHERE type='table' ORDER BY name\")]); c.close()"
```

This prints the list of tables. If you see the expected tables (`users`, `equipment`, `daily_records`, etc.), the database is intact.

### Step 4: Start the service

```cmd
.\nssm.exe start TransportReport
```

### Step 5: Open the application and verify

Open `http://10.103.25.200:5050`. Log in, check that the dashboard shows data
from the period covered by the backup.

---

## Post-update QA checklist

After every update, run the following smoke tests:

- [ ] Open `/login` — login page loads.
- [ ] Log in as admin — dashboard loads.
- [ ] Open `/entry` — daily entry page loads with all 9 equipment categories.
- [ ] Open `/report` — report page loads; generate a one-day report.
- [ ] Open `/wialon` — Wialon import page loads.
- [ ] Open `/wialon/workload` — workload report loads.
- [ ] Open `/fuel/` — fuel dashboard loads.
- [ ] Open `/ref/equipment` — equipment reference loads.
- [ ] Open `/admin/users` as admin — user list loads.
- [ ] Switch language UZ ↔ RU — labels change on all pages.
- [ ] Check `logs\error.log` — no new Python exceptions since startup.

Full checklist: `docs\QA_CHECKLIST.md`.

---

## Known risks

| Risk | Severity | Mitigation |
|---|---|---|
| `git pull --ff-only` fails due to diverged history | Medium | `update.bat` stops and prints instructions. Investigate with `git status` and `git log`. |
| Migration script forgotten — service starts without migrating | High | `update.bat` migration warning with manual pause. Read release notes before updating. |
| Database backup fails silently | Medium | `backup_transport_db.bat` exits with code 1 on failure. Task Scheduler can send email alerts (configure in task properties). |
| Backup disk runs out of space | Medium | Keep only the last 30 daily backups. Review `C:\transport-report-backups\daily\` monthly. |
| Service refuses to start after update | Medium | Check `logs\error.log`. Most common cause: `SECRET_KEY` not set. See `docs\DEPLOYMENT_SECURITY.md`. |
| Live-copy consistency risk | Low | Backups use the SQLite online backup API (`backup_transport_db.py`). WAL and SHM sidecar files are not manually copied — the API merges pending WAL pages into the destination automatically, producing a consistent snapshot. Raw `copy` of `.db` while WAL has uncheckpointed data is NOT used. |
| Rollback loses data entered since last backup | Medium | Pre-update backup is taken immediately before every update. Communicate downtime to users before updating during business hours. |

---

## Operator quick-reference commands

### Update production from GitHub

```cmd
cd C:\transport-report
update.bat
```

### Create a manual backup

```cmd
cd C:\transport-report
backup_transport_db.bat
```

### Stop the service

```cmd
cd C:\transport-report
.\nssm.exe stop TransportReport
```

(fallback: `net stop TransportReport`)

### Start the service

```cmd
cd C:\transport-report
.\nssm.exe start TransportReport
```

(fallback: `net start TransportReport`)

### Check service status

```cmd
sc query TransportReport
```

### View error log

```cmd
type C:\transport-report\logs\error.log
```

### List daily backups

```cmd
dir C:\transport-report-backups\daily\
```

### Syntax check all main modules

```cmd
cd C:\transport-report
"C:\Program Files\Python314\python.exe" -m py_compile app.py models.py config.py run_server.py fuel_routes.py spare_parts.py wialon_import.py workload_report.py translations.py
```

### Check migration registry

```cmd
cd C:\transport-report
"C:\Program Files\Python314\python.exe" -c "import sqlite3; c=sqlite3.connect('instance/transport.db'); [print(r) for r in c.execute('SELECT id, name, applied_at FROM schema_migrations ORDER BY id')]; c.close()"
```

### Check environment variables are set

```cmd
reg query "HKEY_LOCAL_MACHINE\SYSTEM\CurrentControlSet\Control\Session Manager\Environment" /v SECRET_KEY
reg query "HKEY_LOCAL_MACHINE\SYSTEM\CurrentControlSet\Control\Session Manager\Environment" /v FUEL_API_TOKEN
```
