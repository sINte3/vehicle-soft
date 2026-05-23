# ORG_WINDOWS_SERVER_STAGING_RUNBOOK.md — Vehicle Soft

Task ID: TASK-DEPLOY-005D  
Created: 2026-05-23  
Applies to: Organization Windows Server staging (`srv-yoqsh`, IP `10.103.25.14`)

---

## Staging server facts

| Item | Value |
|---|---|
| Hostname | `srv-yoqsh` |
| IP | `10.103.25.14` |
| Staging URL | `http://10.103.25.14:5051` |
| Service name | `TransportReportStaging` |
| Project path | `C:\transport-report-staging\` |
| Staging DB | `C:\transport-report-staging\instance\transport.db` |
| Staging backup dir | `D:\transport-report-backups\staging\daily\` |

---

## Staging server status (2026-05-23)

- `http://10.103.25.14:5051` opens login/main UI — CONFIRMED.
- Service `TransportReportStaging` is RUNNING.
- Staging DB counts verified (read-only query, 2026-05-23):

| Table | Count |
|---|---|
| users | 3 |
| equipment | 336 |
| fuel_transactions2 | 391,284 |
| schema_migrations | 10 |

- Manual staging backup integrity check: `ok` (46,809,088 bytes). See Section 3.

---

## Section 1 — Staging backup procedure

### 1.1 Manual on-demand staging backup

Use `backup_transport_db.py` with `--source` pointing to the staging DB:

```cmd
cd C:\transport-report-staging
"C:\Program Files\Python314\python.exe" backup_transport_db.py ^
  --source C:\transport-report-staging\instance\transport.db ^
  --dest-dir D:\transport-report-backups\staging\daily ^
  --suffix staging
```

The script uses the SQLite online backup API. It does NOT stop the service.
It performs a `PRAGMA integrity_check` on the destination and exits non-zero on failure.

Expected output:

```
============================================================
 Transport DB Backup  (SQLite online backup API)
============================================================
 Source : C:\transport-report-staging\instance\transport.db
 Dest   : D:\transport-report-backups\staging\daily\transport_YYYYMMDD_HHMMSS_staging.db

 Source size : 46,809,088 bytes

Running SQLite online backup...
 Dest size   : 46,809,088 bytes

Running integrity check on destination database...
 Integrity check : ok

SUCCESS: Backup written to:
         D:\transport-report-backups\staging\daily\transport_YYYYMMDD_HHMMSS_staging.db
```

### 1.2 Manual verification of backup integrity (2026-05-23)

A manual backup of the staging DB was already performed on 2026-05-23 using a one-off
Python command (before `--source` support was added to the script). Result:

- Source: `C:\transport-report-staging\instance\transport.db`
- Destination: `D:\transport-report-backups\staging\daily\transport_staging_20260523_223133.db`
- Integrity check: `ok`
- File size: 46,809,088 bytes

The `--source` option added in TASK-DEPLOY-005D enables the same operation via
`backup_transport_db.py` without any one-off Python commands.

---

## Section 2 — Automated staging backup via Task Scheduler

Set up a daily automated backup for the staging DB (run CMD as Administrator on
`srv-yoqsh`):

```cmd
schtasks /create /tn "TransportDBBackupStaging" ^
  /tr "\"C:\Program Files\Python314\python.exe\" C:\transport-report-staging\backup_transport_db.py --source C:\transport-report-staging\instance\transport.db --dest-dir D:\transport-report-backups\staging\daily --suffix staging" ^
  /sc daily /st 03:00 /ru SYSTEM /f
```

Parameters:
- `/tn "TransportDBBackupStaging"` — task name (distinct from production `TransportDBBackup`).
- `/tr "..."` — calls `backup_transport_db.py` with staging source and dest.
- `/sc daily /st 03:00` — 03:00 daily (one hour after production backup at 02:00).
- `/ru SYSTEM` — run as SYSTEM account.
- `/f` — overwrite if the task already exists.

### Verify the task:

```cmd
schtasks /query /tn "TransportDBBackupStaging" /fo LIST
```

### Run it immediately to test:

```cmd
schtasks /run /tn "TransportDBBackupStaging"
```

Then confirm a new file appears in `D:\transport-report-backups\staging\daily\`.

---

## Section 3 — Staging backup history

| Date | Type | File | Size | Integrity |
|---|---|---|---|---|
| 2026-05-23 | Manual (one-off Python command) | `transport_staging_20260523_223133.db` | 46,809,088 bytes | ok |

After the `TransportDBBackupStaging` Task Scheduler task is created, future backups
will use `backup_transport_db.py --source ... --suffix staging` (see Section 2).

---

## Section 4 — Staging QA checklist

After confirming the staging service is running, verify these manually:

- [ ] `http://10.103.25.14:5051` — login page loads.
- [ ] Log in as admin — dashboard loads.
- [ ] `/entry` — daily entry page loads with all 9 equipment categories.
- [ ] `/report` — report page loads; generate a one-day report.
- [ ] `/wialon` — Wialon import page loads.
- [ ] `/fuel/` — fuel dashboard loads.
- [ ] `/ref/equipment` — equipment reference loads.
- [ ] Switch language UZ/RU — labels change.
- [ ] Module permissions: non-admin user cannot access a disabled module.
- [ ] `D:\transport-report-backups\staging\daily\` — backup file exists after task run.

---

## Section 5 — Operator next steps

1. Run Section 1.1 manual backup command at least once to confirm `--source` support works
   on `srv-yoqsh` with the updated `backup_transport_db.py`.
2. Create the `TransportDBBackupStaging` Task Scheduler task (Section 2).
3. Complete the QA checklist (Section 4) — test all UI modules on staging.
4. After staging QA passes, proceed to TASK-DEPLOY-006 planning
   (PostgreSQL migration research) or cutover planning.

---

## Section 6 — Production vs staging comparison

| Item | Production | Staging |
|---|---|---|
| Server | `10.103.25.200` | `10.103.25.14` (srv-yoqsh) |
| Port | `5050` | `5051` |
| Service | `TransportReport` | `TransportReportStaging` |
| Project path | `C:\transport-report\` | `C:\transport-report-staging\` |
| DB path | `C:\transport-report\instance\transport.db` | `C:\transport-report-staging\instance\transport.db` |
| Backup dir | `C:\transport-report-backups\daily\` | `D:\transport-report-backups\staging\daily\` |
| Backup task | `TransportDBBackup` (02:00) | `TransportDBBackupStaging` (03:00) |
| Backup suffix | (none) | `staging` |

---

## Section 7 — Topaz/Wialon staging policy

Do NOT change Topaz agent configuration to point to staging.
Topaz continues to sync to production only.
Staging Wialon and Topaz features can be tested manually using imported data only.
Update Topaz agent only after full cutover to a new production server is confirmed.
