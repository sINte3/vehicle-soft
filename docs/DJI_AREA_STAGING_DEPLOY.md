# DJI-AREA-EVIDENCE-001 — развёртывание на площадке

Один блок для владельца. Выполняется **на SRV-YOQSH**, в PowerShell, от
учётной записи, под которой работает служба площадки. Блок останавливается
на первой же неудаче и печатает `STEP=PASS` последней строкой только когда
прошли все шаги.

**Площадка, не production.** В блоке нет ни одной команды к
`C:\transport-report` и к службе `TransportReport`. Он проверяет имя хоста,
путь и имя службы прежде, чем что-либо сделать.

Ревизия реализации: `2982396568ddc250f8aa9a03ffd20babb64fbc3b` (ветка `claude/dji-area-evidence-20260908`, PR не смерженный на момент
написания). Это ВЕСЬ код инкремента: следом в ветке идут только два
документа — этот блок и строка гейта релиза, кода они не меняют.
Миграция: `DJI_AREA_EVIDENCE_001` — восемь новых таблиц,
аддитивная; ни одна существующая таблица, колонка или строка не меняется, и
`drone_flights.area_ha` не читается и не пишется.

## Что этот деплой даёт и чего не даёт

Даёт: схему учёта площади по модели доказательств, два машинных приёмника
(`/drones/api/source_sync`, `/drones/api/land_snapshot_sync`), отчёт
`/drones/area-evidence` в **теневом режиме** и инструменты пересчёта.

Не даёт: ни одной строки данных. Таблицы после миграции пусты. Чтобы отчёт
показал числа, нужен сбор источников (`--sources`) и пересчёт
(`tools/dji_area_recalc.py`) — это отдельный шаг после того, как деплой
подтвердится. Ни одна цифра площадки не становится счётом: `billable` NULL.

## Блок

Скопировать целиком и вставить в PowerShell на SRV-YOQSH.

```powershell
& {
  $ErrorActionPreference = 'Stop'
  $expectedHost = 'srv-yoqsh'
  $root         = 'C:\transport-report-staging'
  $service      = 'TransportReportStaging'
  $python       = 'C:\Program Files\Python314\python.exe'
  $sha          = '2982396568ddc250f8aa9a03ffd20babb64fbc3b'
  $runId        = Get-Date -Format 'yyyyMMdd_HHmmss'
  $backupDir    = "D:\transport-report-backups\staging\dji_area_evidence_001\$runId"

  if ((hostname) -ne $expectedHost) { throw "STEP FAILED: host is $(hostname), expected $expectedHost" }
  if (-not (Test-Path "$root\instance\transport.db")) { throw "STEP FAILED: staging database not found under $root" }
  if ($root -notlike '*transport-report-staging*') { throw "STEP FAILED: refusing a root that is not the staging checkout" }
  $svc = Get-Service -Name $service
  if ($svc.Name -eq 'TransportReport') { throw "STEP FAILED: that is the production service" }
  Set-Location $root
  Write-Output ("BEFORE_HEAD=" + (git rev-parse HEAD))
  Write-Output ("BEFORE_DIRTY_LINES=" + ((git status --porcelain | Measure-Object -Line).Lines))
  Write-Output ("SERVICE_BEFORE=" + $svc.Status)

  New-Item -ItemType Directory -Force -Path $backupDir | Out-Null
  & $python backup_transport_db.py --source "$root\instance\transport.db" --dest-dir $backupDir --suffix dji_area_pre
  if ($LASTEXITCODE -ne 0) { throw "STEP FAILED: backup" }
  $backup = Get-ChildItem $backupDir -Filter '*.db' | Sort-Object LastWriteTime | Select-Object -Last 1
  if (-not $backup) { throw "STEP FAILED: backup file was not created" }
  $src = (Get-Item "$root\instance\transport.db").Length
  Write-Output ("BACKUP=" + $backup.FullName + " BYTES=" + $backup.Length + " SOURCE_BYTES=" + $src)
  $integrity = & $python -c "import sqlite3,sys;print(sqlite3.connect(sys.argv[1]).execute('PRAGMA integrity_check').fetchone()[0])" $backup.FullName
  if ($integrity -ne 'ok') { throw "STEP FAILED: backup integrity_check said $integrity" }
  Write-Output "BACKUP_INTEGRITY=ok"

  git fetch origin claude/dji-area-evidence-20260908
  if ($LASTEXITCODE -ne 0) { throw "STEP FAILED: git fetch" }
  git cat-file -e "$sha^{commit}"
  if ($LASTEXITCODE -ne 0) { throw "STEP FAILED: commit $sha is not in the repository after fetch" }
  git merge-base --is-ancestor HEAD $sha
  if ($LASTEXITCODE -ne 0) { throw "STEP FAILED: the current staging HEAD is not an ancestor of $sha -- stop and report the two hashes" }
  git checkout --detach $sha
  if ($LASTEXITCODE -ne 0) { throw "STEP FAILED: git checkout" }
  Write-Output ("AFTER_HEAD=" + (git rev-parse HEAD))

  & $python -m compileall -q .
  if ($LASTEXITCODE -ne 0) { throw "STEP FAILED: compileall" }
  & $python -m unittest tests.test_dji_area_core tests.test_dji_area_migration_001
  if ($LASTEXITCODE -ne 0) { throw "STEP FAILED: acceptance and migration tests" }

  Stop-Service -Name $service -Force
  (Get-Service -Name $service).WaitForStatus('Stopped', (New-TimeSpan -Seconds 90))
  Write-Output ("SERVICE_STOPPED=" + (Get-Service -Name $service).Status)

  & $python tools\check_db_lock.py
  $lock = $LASTEXITCODE
  if ($lock -eq 2) { throw "STEP FAILED: another process holds the database (exit 2)" }
  Write-Output ("DB_LOCK_EXIT=$lock (0 clean, 3 stale WAL is expected on this project)")

  & $python migrate_dji_area_evidence_001.py
  if ($LASTEXITCODE -ne 0) { throw "STEP FAILED: migration" }
  & $python migrate_dji_area_evidence_001.py
  if ($LASTEXITCODE -ne 0) { throw "STEP FAILED: migration re-run (must print 'Already applied. Nothing to do.')" }
  & $python tools\check_migration_drift.py --db instance\transport.db
  Write-Output ("DRIFT_EXIT=$LASTEXITCODE  # file-but-not-registered: 1 (DRONES_WORKS_OCT2025_REDATE_001) is the known permanent value")

  Start-Service -Name $service
  (Get-Service -Name $service).WaitForStatus('Running', (New-TimeSpan -Seconds 90))
  Start-Sleep -Seconds 8
  $login = Invoke-WebRequest -Uri 'http://10.103.25.14:5051/login' -UseBasicParsing -TimeoutSec 30
  if ($login.StatusCode -ne 200) { throw "STEP FAILED: smoke /login returned $($login.StatusCode)" }
  if ($login.Content -notmatch 'vs-login-form') { throw "STEP FAILED: smoke /login did not render the login form" }
  Write-Output "SMOKE_LOGIN=200"
  Write-Output ("FINAL_HEAD=" + (git rev-parse HEAD))
  Write-Output ("SERVICE_FINAL=" + (Get-Service -Name $service).Status)
  Write-Output "STEP=PASS"
}
```

## Что прислать в ответ

Весь вывод блока целиком. Ключевые строки: `BEFORE_HEAD`, `AFTER_HEAD`,
`BACKUP` с двумя размерами, `BACKUP_INTEGRITY=ok`, `DB_LOCK_EXIT`,
`DRIFT_EXIT`, `SMOKE_LOGIN=200`, `STEP=PASS`.

## Если блок остановился

Он останавливается ДО изменения службы на всём, что проверяется заранее
(хост, путь, служба, бэкап, ревизия). Если остановка случилась после
`SERVICE_STOPPED`, служба остаётся остановленной намеренно: запускать её
поверх незавершённой миграции нельзя. Порядок отката:

```powershell
& {
  $ErrorActionPreference = 'Stop'
  Set-Location 'C:\transport-report-staging'
  Copy-Item 'D:\transport-report-backups\staging\dji_area_evidence_001\<каталог прогона>\<файл>.db' 'C:\transport-report-staging\instance\transport.db' -Force
  git checkout --detach (git rev-parse 'HEAD@{1}')
  Start-Service -Name 'TransportReportStaging'
}
```

Два пути в первой команде подставляются из строки `BACKUP=` вывода: это
единственное место документа, где значение берётся из уже полученного
ответа, а не задано заранее.

## После деплоя

1. Открыть `http://10.103.25.14:5051/drones/area-evidence` — таблица пуста,
   и это правильно: данных ещё нет.
2. Сбор источников и пересчёт — отдельный шаг, командами из
   `docs/DJI_AREA_IMPLEMENTATION.md` §4–5.
3. Ручная сверка августа — по пакету
   `DJI_AREA_AUGUST_LIVE_VALIDATION_PACK.xlsx`.
4. **Production не деплоится** до решения владельца после сверки и
   квалификации по `TRUE_HOLDOUT_PROTOCOL_LOCKED.md`.
