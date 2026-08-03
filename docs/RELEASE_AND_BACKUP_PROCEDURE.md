# RELEASE_AND_BACKUP_PROCEDURE.md — Vehicle Soft

Task ID: TASK-DEPLOY-004  
Created: 2026-05-23  
Updated: 2026-08-02 (DOC-RELEASE-PROC-001, DEPLOY-DRIFT-001)  
Applies to: Windows production server, `C:\transport-report\`

---

> ## ⛔ НЕ ЗАПУСКАТЬ `update.bat` ДЛЯ РЕЛИЗА С МИГРАЦИЕЙ
>
> `update.bat` остаётся в репозитории и работает, но он останавливает **одну**
> службу из трёх и стартует её сразу после паузы с вопросом о миграции —
> то есть поднимает новый код на непромигрированной базе, если оператор
> ответит не задумываясь. Ровно так сломался релиз v1.10: служба поднялась на
> новом коде раньше своей миграции и отвечала `no such column` почти на каждом
> экране.
>
> Для любого релиза используйте **[Процедуру обновления](#процедура-обновления-фактическая)**
> ниже. Скрипт не изменён и не удалён: это отдельное решение.

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
| Production server | `srv-yoqsh` (10.103.25.14) |
| Production server path | `C:\transport-report\` |
| Application URL | `http://10.103.25.14:5050` |
| Database | `C:\transport-report\instance\transport.db` |
| Daily backup location | `D:\transport-report-backups\production\daily\` |
| Daily backup wrapper | `C:\transport-report\backup_production_db.bat` |
| Scheduled task | `TransportDBBackupProduction`, daily 02:00, SYSTEM |
| Pre-update backup location | `C:\transport-report-backups\before_update\` |

---

## Pre-update checklist

Before running any update, complete this checklist:

- [ ] Read the release notes or commit log on GitHub to understand what changed.
- [ ] Identify whether the release includes a migration script (`migrate_NNN_*.py`).
      If yes, read `docs\MIGRATIONS.md` before continuing.
- [ ] Confirm `SECRET_KEY` and `FUEL_API_TOKEN` are set on this server.
      (See `docs\DEPLOYMENT_SECURITY.md`.)
- [ ] Confirm a recent daily backup exists in `D:\transport-report-backups\production\daily\`.
      The pre-update script also creates a backup, but a recent daily backup is a second safety net.
- [ ] Notify users that a brief downtime is coming — **all three** services are
      stopped during the update, not just `TransportReport`.
- [ ] Close any open Excel reports downloaded from the application.
- [ ] Записать числа для дымовой проверки **до** начала работ: количество
      единиц техники, контрагентов, и ключевые числа затронутого трека.
      После релиза сверять не с чем, если они не записаны заранее.
- [ ] Записать ожидаемый переход `file-but-not-registered` для шага 6:
      `0 -> N -> 0`, где N — число миграций в релизе.

---

## Процедура обновления (фактическая)

Так выпущены v1.9, v1.10 и v1.11. Порядок шагов — часть процедуры, а не
оформление: он выведен из того, чем именно сломался v1.10.

PowerShell, по одной команде на строку — в PowerShell нет `&&`.

### Шаг 1 — Предполётная проверка, службы ещё работают

Только чтение, ничего не меняет. Выполняется до остановки служб, чтобы
обнаруженная проблема не стоила простоя.

```
cd C:\transport-report
git fetch origin
git status --short
git rev-parse --short HEAD
git --no-pager log --oneline HEAD..origin/main
```

- Локальных коммитов впереди `origin/main` быть не должно. Если они есть —
  остановиться и разобраться, откуда они взялись.
- Прочитать **весь** вывод `log --oneline HEAD..origin/main` и назвать каждый
  трек, который в нём присутствует. `main` общая для четырёх треков; в релиз
  едет всё, что смержено, а не только то, чего вы ждёте.

### Шаг 2 — Остановить все три службы

`AppDirectory` = `C:\transport-report` у трёх служб, и все три пишут в одну базу.
Остановка одной оставляет двух живых писателей.

```
Stop-Service TransportReport
Stop-Service TransportBot
Stop-Service TransportBot003
Get-Service TransportReport, TransportBot, TransportBot003 | Format-Table Name, Status
```

Все три обязаны показать `Stopped`. Не «команда прошла без ошибки» — именно
`Stopped` в выводе.

### Шаг 3 — Резервная копия на остановленной базе

```
.\backup_production_db.bat
```

Проверить в выводе три вещи: `SUCCESS`, размер приёмника **равен** размеру
источника, `Integrity check : ok`. Размер сверять числом, а не на глаз.

### Шаг 4 — Проверка блокировки базы

```
& "C:\Program Files\Python314\python.exe" tools\check_db_lock.py
```

Код возврата 0 (`CLEAN`) — можно продолжать. Код 2 (`HELD`) — база у кого-то
открыта, вернуться к шагу 2. Код 3 (`STALE`) — остались `-wal`/`-shm` без
живого держателя, это след неаккуратной остановки; разобраться до миграции.

### Шаг 5 — Забрать код

```
git merge --ff-only origin/main
git rev-parse --short HEAD
```

`--ff-only` намеренно: если слияние не перемотка, история разошлась и это
разбирают до релиза, а не во время него.

### Шаг 6 — Дрейф миграций, замер «после pull» (DEPLOY-DRIFT-001)

```
& "C:\Program Files\Python314\python.exe" tools\check_migration_drift.py --db instance\transport.db
```

Замеряется **трижды за релиз**: до pull, после pull, после миграций. Значимая
величина одна — `file-but-not-registered`. Ожидаемый переход записывается
**заранее**, до первого замера: `0 -> N -> 0`, где N — число миграций в релизе.

Секции `resolved-by-backfill`, `registered-but-no-file` и `unclassifiable` —
исторический хвост (`TOOL-DRIFT-LEGACY-001`); их числа от релиза к релизу не
меняются и сами по себе ничего не значат. Меняются — повод разобраться.

### Шаг 7 — Миграции, каждая по два раза

Каждый скрипт миграции запускается дважды подряд. Второй запуск обязан
сказать «уже применено» и ничего не сделать — это и есть проверка
идемпотентности, а не формальность.

```
& "C:\Program Files\Python314\python.exe" migrate_<имя>.py
& "C:\Program Files\Python314\python.exe" migrate_<имя>.py
```

Затем повторить шаг 6 — третий замер дрейфа. `file-but-not-registered` обязан
вернуться к 0.

### Шаг 8 — Поднять службы

```
Restart-Service TransportReport
Restart-Service TransportBot
Restart-Service TransportBot003
Get-Service TransportReport, TransportBot, TransportBot003 | Format-Table Name, Status
```

`Restart-Service`, а не `Start-Service`: последний на уже работающей службе
молча ничего не делает. Все три обязаны показать `Running`.

### Шаг 9 — Дымовая проверка против чисел, снятых ДО релиза

Числа для сверки снимаются **до** начала работ — иначе сверять не с чем и
проверка вырождается в «страница открылась».

- `/login`, вход, дашборд;
- `/ref/equipment` — количество единиц техники совпадает с записанным до релиза;
- `/report` — отчёт за один день строится;
- `/fuel/` — топливный дашборд и отчёт по остаткам открываются;
- запчасти — рабочий стол открывается;
- экраны затронутого релизом трека — по описанию PR;
- переключение UZ ↔ RU;
- `logs\error.log` — новых исключений с момента старта нет.

Полный список: `docs\QA_CHECKLIST.md`.

### Шаг 10 — Тег, только после дымовой проверки

```
git tag -a v<версия>-production-<ГГГГ-ММ-ДД> -m "Production release v<версия>"
git push origin v<версия>-production-<ГГГГ-ММ-ДД>
```

Тег ставится **после** шага 9, никогда до. Тег до проверки означает метку
«проверено» на непроверенном коде.

---

## Migration handling rule

**Migrations are NEVER automatic.**

- If a release includes a migration script (`migrate_NNN_*.py`), it must be run manually.
- Always run migrations AFTER stopping the services and BEFORE starting them.
- Always back up the database BEFORE running a migration.
- Always run each migration twice; the second run proves idempotency.
- Follow `docs\MIGRATIONS.md` for the full procedure.
- If you are unsure whether a migration is needed, read the release notes or ask before continuing.

Порядок из шагов 2–8 выше — единственное, что это правило обеспечивает.
`update.bat` его не обеспечивает: он останавливает одну службу из трёх и
стартует её сразу после паузы с вопросом о миграции. См. баннер в начале
документа.

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

Open `http://10.103.25.14:5050` and confirm login works and the dashboard loads.
Check `logs\error.log` for startup errors.

---

## Manual backup procedure

Run `backup_production_db.bat` at any time to create a timestamped backup in the production backup folder:

```cmd
cd C:\transport-report
backup_production_db.bat
```

`backup_production_db.bat` delegates to `backup_transport_db.py`, which uses the
SQLite online backup API (`sqlite3.Connection.backup()`). This produces a consistent
snapshot of the database even when the service is running and WAL mode is active.
The `.db-wal` and `.db-shm` sidecar files are NOT manually copied — the backup API
merges any pending WAL pages into the destination automatically.

Output example (from operator test run 2026-05-23):
```
============================================================
 Transport DB Daily Backup
============================================================

============================================================
 Transport DB Backup  (SQLite online backup API)
============================================================
 Source : C:\transport-report\instance\transport.db
 Dest   : D:\transport-report-backups\production\daily\transport_20260523_182423.db

 Source size : 46,800,896 bytes

Running SQLite online backup...
 Dest size   : 46,800,896 bytes

Running integrity check on destination database...
 Integrity check : ok

SUCCESS: Backup written to:
         D:\transport-report-backups\production\daily\transport_20260523_182423.db
Backup completed successfully.
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

The production daily backup task `TransportDBBackupProduction` is already active on `srv-yoqsh`.
It runs `backup_production_db.bat` daily at 02:00 as SYSTEM and writes to
`D:\transport-report-backups\production\daily\`.

Use the commands below to inspect, test, or recreate the task if ever needed.

### Verify the task exists and is active:

```cmd
schtasks /query /tn "TransportDBBackupProduction" /fo LIST
```

### Run the task immediately to test it:

```cmd
schtasks /run /tn "TransportDBBackupProduction"
```

Then check that a new file appears in `D:\transport-report-backups\production\daily\`.

### Recreate the task (if deleted or on a fresh server):

```cmd
schtasks /create /tn "TransportDBBackupProduction" /tr "C:\transport-report\backup_production_db.bat" /sc daily /st 02:00 /ru SYSTEM /f
```

Parameters:
- `/tn "TransportDBBackupProduction"` — task name.
- `/tr "C:\transport-report\backup_production_db.bat"` — wrapper that writes to `D:\transport-report-backups\production\daily\`.
- `/sc daily /st 02:00` — every day at 02:00 (2 AM).
- `/ru SYSTEM` — runs as SYSTEM account (full local access, including D: drive).
- `/f` — overwrite if the task already exists.

### Modify the schedule (example: change to 03:00):

```cmd
schtasks /change /tn "TransportDBBackupProduction" /st 03:00
```

### Delete the task (if needed):

```cmd
schtasks /delete /tn "TransportDBBackupProduction" /f
```

---

## Cutover history — production backup setup (TASK-DEPLOY-005F, 2026-05-24)

When production was cut over to `srv-yoqsh` (`10.103.25.14`) on 2026-05-24, the production backup
wrapper and scheduled task were created on the new server:

| Item | Value |
|---|---|
| Backup wrapper | `C:\transport-report\backup_production_db.bat` |
| Scheduled task | `TransportDBBackupProduction` |
| Schedule | Daily 02:00, run as SYSTEM |
| Backup destination | `D:\transport-report-backups\production\daily\` |
| First test backup | `transport_20260523_235432.db`, 46,809,088 bytes, integrity ok |
| Task state | Ready / next run 24.05.2026 02:00 |

The wrapper calls `backup_transport_db.py` with `--source C:\transport-report\instance\transport.db`
and `--dest-dir D:\transport-report-backups\production\daily`. The same SQLite online backup API
and integrity check as the workstation backup apply.

The old workstation (`10.103.25.200`) backup task (`TransportDBBackup`, target
`C:\transport-report-backups\daily\`) remains on the workstation in standby. It is inactive
because the service is stopped and will not run unless a rollback to the old workstation occurs.

---

## Operator verification record (TASK-DEPLOY-004E, 2026-05-23) — old workstation

The full backup procedure was completed and verified on the old workstation (`10.103.25.200`)
on 2026-05-23. Production has since moved to `srv-yoqsh` (`10.103.25.14`); this record is
retained for history only.

| Step | Command | Result |
|---|---|---|
| Syntax check | `py_compile backup_transport_db.py` | PASS — no output |
| Manual backup | `backup_transport_db.bat` | SUCCESS — integrity check `ok`, wrapper: `Backup completed successfully.` |
| Backup file | `transport_20260523_182423.db` in `C:\transport-report-backups\daily\` | 46,800,896 bytes |
| Create task | `schtasks /create /tn "TransportDBBackup" ... /sc daily /st 02:00 /ru SYSTEM /f` | SUCCESS — next run 24.05.2026 2:00:00, state Ready |
| Test task run | `schtasks /run /tn "TransportDBBackup"` | SUCCESS — new backup `transport_20260523_182603.db`, 46,800,896 bytes |
| Git status | commits `428104a` and `10652e2` on `origin/main` | Working tree clean |

The `TransportDBBackup` scheduled task was active on the old workstation at the time of this
record. The current production task is `TransportDBBackupProduction` on `srv-yoqsh`.

---

## How to verify backups

After the task runs, verify a backup file was created:

```cmd
dir D:\transport-report-backups\production\daily\
```

Look for a file named `transport_YYYYMMDD_HHMMSS.db` with today's date.

Check the file size is similar to the live database:

```cmd
dir C:\transport-report\instance\transport.db
dir D:\transport-report-backups\production\daily\
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
copy /Y "D:\transport-report-backups\production\daily\transport_YYYYMMDD_HHMMSS.db" "C:\transport-report\instance\transport.db"
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

Open `http://10.103.25.14:5050`. Log in, check that the dashboard shows data
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
| `git merge --ff-only` fails due to diverged history | Medium | Шаг 5 останавливает релиз. Разбирать через `git status` и `git log`, до старта служб. |
| Migration script forgotten — service starts without migrating | High | Сломало v1.10. Закрыто порядком шагов 2–8 и тройным замером дрейфа (шаг 6, `DEPLOY-DRIFT-001`), а не паузой с вопросом к оператору. |
| Одна служба остановлена вместо трёх | High | Шаг 2 останавливает все три и требует `Stopped` в выводе `Get-Service`. Шаг 4 (`check_db_lock.py`, код 2) ловит оставшегося держателя базы. |
| Database backup fails silently | Medium | `backup_production_db.bat` exits with code 1 on failure. Task Scheduler can send email alerts (configure in task properties). |
| Backup disk runs out of space | Medium | Keep only the last 30 daily backups. Review `D:\transport-report-backups\production\daily\` monthly. |
| Service refuses to start after update | Medium | Check `logs\error.log`. Most common cause: `SECRET_KEY` not set. See `docs\DEPLOYMENT_SECURITY.md`. |
| Live-copy consistency risk | Low | Backups use the SQLite online backup API (`backup_transport_db.py`). WAL and SHM sidecar files are not manually copied — the API merges pending WAL pages into the destination automatically, producing a consistent snapshot. Raw `copy` of `.db` while WAL has uncheckpointed data is NOT used. |
| Rollback loses data entered since last backup | Medium | Pre-update backup is taken immediately before every update. Communicate downtime to users before updating during business hours. |

---

## Operator quick-reference commands

### Update production from GitHub

Полная процедура — [«Процедура обновления (фактическая)»](#процедура-обновления-фактическая).
Одной командой релиз не выпускается: `update.bat` для релиза с миграцией
использовать нельзя (см. баннер в начале документа).

### Check the database lock before migrating

```
cd C:\transport-report
& "C:\Program Files\Python314\python.exe" tools\check_db_lock.py
```

### Check migration drift

```
cd C:\transport-report
& "C:\Program Files\Python314\python.exe" tools\check_migration_drift.py --db instance\transport.db
```

### Create a manual backup

```cmd
cd C:\transport-report
backup_production_db.bat
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
dir D:\transport-report-backups\production\daily\
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
