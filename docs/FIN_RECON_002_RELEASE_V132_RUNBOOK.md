# Выпуск v1.32 — FIN-RECON-002, июнь 2026 — пошагово

Для владельца. Всё делается на сервере **SRV-YOQSH**, блоки — «скопировать,
вставить, Enter, посмотреть строку `RESULT`». Трек — `docs/tracks/drones.md`,
запрос https://github.com/sINte3/vehicle-soft/pull/232. Порядок, проверки и
вспомогательные функции блоков — те же, что у выпуска v1.31
(`docs/GPS_RELEASE_V131_RUNBOOK.md`), прошедшего на этом сервере 22.10.

## Что произойдёт — коротко

1. Программа обновится с `998bc20` (выпуск v1.31) ровно до `c4b0396` —
   merge-коммита этого запроса. Новое:
   - работы «Фукаро» и «Ромитан т. Фукаро» хранятся без карточки, с отметкой
     «Плательщик-гражданин не установлен»; долг по ним — по каждой работе,
     без взаимозачёта;
   - отчёты и журнал различают «не указан», «не определён» и «гражданин не
     установлен».

   Вместе с выпуском приедут ручные инструменты трека GPS, влитые в `main`
   после v1.31 (PR #229–#248):
   - разведка перевозок;
   - проверка рейсов 1С;
   - зонд 1С;
   - недельная выгрузка 1С и её приёмка (GPS-37).

   Программа и ночные задачи их не вызывают: `gps/hauling.py` импортируют
   только они. Задание Планировщика для выгрузки 1С этот выпуск не ставит —
   оно идёт отдельным PR трека GPS.

   **Выпуск закреплён на `c4b039630fd75264994267abe69f89d9eaba7632`.**
   Коммиты, влитые в `main` позже (PR #249 и дальше), в него не входят:
   шаги 2 и 3 проверяют, что `c4b0396` есть в `main` и что `998bc20` — его
   предок, репетируют и ставят ровно его.
2. **В базе** (в одном окне, программа остановлена), по порядку:
   1. колонка `drone_works.payer_unidentified` — **до** запуска нового кода;
   2. 31 июньская строка объединённых ячеек → №137 (21) и №138 (10);
      отдельно работа 997 с №136 на №137;
   3. таблица решений владельца:
      - алиасы, 44 новые карточки `external`;
      - одна новая «Ғиждувон ПТЗ» (`internal`);
      - №137 и №155 → `internal`;
   4. импорт двух июньских книг (Ғиждувон, Шофиркон): 132 строки /
      1 732,68 га / 277 613 871,12;
   5. проверка базы против копии, снятой в этом же окне.
3. Май и все закрытые периоды не меняются — это проверяется побайтно.
4. Программа и оба Telegram-бота **не работают около 10 минут**.
   Перед окном — резервная копия, сразу проверяется. Если окно
   остановится, когда база уже менялась, программа сама **не запускается**:
   путь назад — «Восстановление базы» из копии окна.

**Когда:** день назовёте вы. **Не с 00:30 до 05:00** — ночью на сервере свои
задачи. Шаг 2 (репетиция) — в тот же день, что шаг 3, после ночной копии.

## Как выполнять команды

PowerShell **от имени администратора** (в заголовке окна — «Администратор»).
Нажать значок «скопировать» у серого блока, правой кнопкой вставить в окно,
Enter; ждать строку `PS C:\...>`.

Блок заканчивается строкой `RESULT: ...`:

- `RESULT: CHECK PASSED` или `RESULT: RELEASE PASSED` — к следующему шагу;
- `RESULT: STOP - ...` — **дальше не идти**, ничего не исправлять самому;
  прислать журнал из строки `LOG FILE:` (папка `C:\VehicleSoft_Release`).

## Шаг 1. Разрешение, слияние, файлы — программа не трогается

1. Ваше письменное разрешение на выпуск. Без него шаги 2–4 не выполняются.
2. https://github.com/sINte3/vehicle-soft/pull/232 слит 29.10 кнопкой
   «Create a merge commit» — коммит
   `c4b039630fd75264994267abe69f89d9eaba7632`. Это и есть выпуск.
3. Положить на сервер, в `C:\VehicleSoft_Release\fin_recon_002\`, файлы из
   приватного пакета:
   - таблицу `FIN_RECON_002_RELEASE_DECISIONS_20261029.csv`;
   - в папку `books\` — ровно две книги: «Дрон Ғиждувон ПТЗ Июнь.xlsx» и
     «Шофиркон ПТЗ Дрон маълумот Июнь.xlsx».

   Блоки сверяют их по SHA-256:

   | Файл | SHA-256 |
   |---|---|
   | таблица выпуска | `754dab3571f4cae516933fd55cf3ee68a7f83b4a21162767b2833f67220a99d0` |
   | Ғиждувон, июнь | `3d9ad76cfdd901548225e41045c58973eb43937c0a910afd992854dab5afebb2` |
   | Шофиркон, июнь | `f4a6591453268d686722321bac1f84942aa7e8e3b0a976bad9a61d5db3a74215` |

   **Таблица выпуска** — это утверждённая вами таблица
   (`085df4bc9e5e681d0aa639c8624079ac375b13bbf17359095fc27feb69ae548f`)
   байт в байт плюс две строки `SET_TYPE`: №137 и №155 → `internal`.

Код, влитый в `main` после `c4b0396`, шагам 2–4 не мешает и на сервер не
попадает: блоки берут закреплённый коммит, а не вершину `main`.

## Шаг 2. Проверка и репетиция на копии — production не меняется

Блок сверяет сервер, версию, гейт, состав выпуска и файлы. Затем повторяет
выпуск целиком на копии свежей ночной резервной копии, кодом ровно этой
версии, во временной папке. Копия с данными удаляется сразу после репетиции.
Программа работает, ничего не останавливается. Займёт 5–15 минут.

```powershell
& {
$ErrorActionPreference = 'Continue'
$ProgressPreference = 'SilentlyContinue'
$prod      = 'C:\transport-report'
$db        = 'C:\transport-report\instance\transport.db'
$py        = 'C:\Program Files\Python314\python.exe'
$backupBat = 'C:\transport-report\backup_production_db.bat'
$services  = @('TransportReport', 'TransportBot', 'TransportBot003')
$baseline  = '998bc20bfdb81e6d90ab413b63ab59da665aac65'
$reviewed  = '518b6407821c65f3bb17d4f8889d7ec031cb5a4f'
$release   = 'c4b039630fd75264994267abe69f89d9eaba7632'
$runbookTest = 'tests/test_fin_recon_002_release_runbook.py'
$expectMig = 'migrate_drones_works_merged_customer_link_001.py migrate_drones_works_payer_unidentified_001.py'
$frdir     = 'C:\VehicleSoft_Release\fin_recon_002'
$table     = 'C:\VehicleSoft_Release\fin_recon_002\FIN_RECON_002_RELEASE_DECISIONS_20261029.csv'
$tableSha  = '754dab3571f4cae516933fd55cf3ee68a7f83b4a21162767b2833f67220a99d0'
$bookShas  = '3d9ad76cfdd901548225e41045c58973eb43937c0a910afd992854dab5afebb2 f4a6591453268d686722321bac1f84942aa7e8e3b0a976bad9a61d5db3a74215'
$daily     = 'D:\transport-report-backups\production\daily'
$repoUrl   = 'https://github.com/sINte3/vehicle-soft.git'
$forbid    = '589,684-692,803'
$work      = 'C:\VehicleSoft_Release'
$log       = 'C:\VehicleSoft_Release\release_v132_step2.log'
$admin     = ([Security.Principal.WindowsPrincipal][Security.Principal.WindowsIdentity]::GetCurrent()).IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)
function Get-OpenItems([string[]]$lines) {
  for ($i = 0; $i -lt $lines.Count; $i++) {
    if ($lines[$i].Trim() -eq '|---|---|---|---|---|') {
      $count = 0
      for ($j = $i + 1; ($j -lt $lines.Count) -and $lines[$j].StartsWith('|'); $j++) { $count++ }
      return $count
    }
  }
  return -1
}
function Invoke-Tool([string]$exe, [string[]]$arguments) {
  $lines = @(& $exe @arguments 2>&1 | ForEach-Object { "$_" })
  $code = $LASTEXITCODE
  foreach ($line in $lines) { Write-Host "    $line" }
  return [pscustomobject]@{ Code = $code; Lines = $lines }
}
New-Item -ItemType Directory -Force -Path $work | Out-Null
try { Start-Transcript -Path $log -Append | Out-Null } catch { Write-Host 'NOTE: the log file could not be started' }
$failure = $null
try {
  if (-not $admin) { throw 'this window is not run as administrator - close it and open PowerShell with Run as administrator' }
  if ((hostname) -ne 'srv-yoqsh') { throw "this computer is $(hostname), not SRV-YOQSH" }
  foreach ($path in @($db, $py, $backupBat, $table, $daily)) {
    if (-not (Test-Path -LiteralPath $path)) { throw "not found: $path" }
  }
  foreach ($name in $services) {
    $service = Get-Service -Name $name -ErrorAction SilentlyContinue
    if (-not $service) { throw "service not found: $name" }
    Write-Host "SERVICE $name : $($service.Status)"
  }
  $size = (Get-Item -LiteralPath $db).Length
  $freeC = (Get-PSDrive -Name C).Free
  $freeD = (Get-PSDrive -Name D -ErrorAction SilentlyContinue).Free
  if (-not $freeD) { throw 'drive D: not found - the backup is written there' }
  if ($freeD -lt (2 * $size)) { throw "drive D: has $([math]::Round($freeD / 1MB)) MB free, the backup needs about $([math]::Round($size / 1MB)) MB" }
  if ($freeC -lt (3 * $size)) { throw "drive C: has $([math]::Round($freeC / 1MB)) MB free, the rehearsal copy needs about $([math]::Round(3 * $size / 1MB)) MB" }
  $sha = (Get-FileHash -LiteralPath $table -Algorithm SHA256).Hash.ToLower()
  if ($sha -ne $tableSha) { throw "the decision table is not the release one (SHA $sha)" }
  $bookFiles = @(Get-ChildItem -LiteralPath "$frdir\books" -Filter '*.xlsx' -File -ErrorAction SilentlyContinue)
  $found = (@($bookFiles | ForEach-Object { (Get-FileHash -LiteralPath $_.FullName -Algorithm SHA256).Hash.ToLower() } | Sort-Object) -join ' ')
  if ($found -ne $bookShas) { throw "the books folder must hold exactly the two June books of the release (found $($bookFiles.Count) file(s))" }
  Write-Host 'FILES: the decision table and both June books are the release ones'
  Set-Location -LiteralPath $prod
  & git fetch --quiet origin
  if ($LASTEXITCODE -ne 0) { throw "git fetch failed (exit $LASTEXITCODE) - is there a connection to GitHub?" }
  $head = [string](& git rev-parse HEAD)
  $main = [string](& git rev-parse origin/main)
  & git merge-base --is-ancestor $release $main
  if ($LASTEXITCODE -ne 0) { throw "the release commit is not in main: $release, main $main" }
  & git merge-base --is-ancestor $reviewed $release
  if ($LASTEXITCODE -ne 0) { throw 'the release commit does not contain the checked version' }
  $changed = @(& git diff --name-only $reviewed $release | Where-Object { ($_ -notlike 'docs/*') -and ($_ -notlike '.github/*') -and ($_ -ne $runbookTest) })
  if ($changed.Count -gt 0) { throw "between the checked version and the release commit there are changes outside docs/: $($changed -join ', ')" }
  $open = Get-OpenItems @(& git show "${main}:docs/RELEASE_GATE.md")
  if ($open -lt 0) { throw 'docs/RELEASE_GATE.md could not be read' }
  if ($open -gt 0) { throw "the release gate is closed: $open open item(s) in docs/RELEASE_GATE.md - the deploy waits until they are removed" }
  & git merge-base --is-ancestor $reviewed $head
  if ($LASTEXITCODE -eq 0) { throw 'this version is already on the server - step 3 was done before; go on to step 4' }
  if ($head -ne $baseline) { throw "the program here is at $head, this release was prepared for $baseline" }
  $modified = @(& git status --porcelain --untracked-files=no)
  if ($modified.Count -gt 0) { throw "program files were edited on this server: $($modified -join '; ')" }
  & git merge-base --is-ancestor $head $release
  if ($LASTEXITCODE -ne 0) { throw 'the new version does not continue the current one' }
  $migrations = (@(& git diff --name-only $head $release | Where-Object { $_ -like 'migrate_*' } | Sort-Object) -join ' ')
  if ($migrations -ne $expectMig) { throw "migrations in the release: '$migrations', expected '$expectMig'" }
  $untracked = @(& git status --porcelain --untracked-files=all | Where-Object { $_ -match '^\?\? ' })
  $clash = @(& git diff --name-only --diff-filter=A $head $release | Where-Object { Test-Path -LiteralPath (Join-Path $prod $_) })
  if ($clash.Count -gt 0) { throw "files of the release already lie on the server: $($clash -join ', ')" }
  Write-Host "UNTRACKED FILES: $($untracked.Count), none of them is a file of the release"
  Write-Host "RELEASE: $head -> $release"
  $later = @(& git --no-pager log --format=%h "$release..$main")
  Write-Host "MAIN: $main - $($later.Count) later commit(s) of main are not taken"
  & git --no-pager log --merges --format=%s "$head..$release" | Where-Object { $_ -like 'Merge pull request*' } | ForEach-Object { Write-Host "  $_" }
  $latest = Get-ChildItem -LiteralPath $daily -Filter 'transport_*.db' -File | Sort-Object LastWriteTime -Descending | Select-Object -First 1
  if (-not $latest) { throw "no daily backup in $daily" }
  if ($latest.LastWriteTime -lt (Get-Date).AddHours(-36)) { throw "the newest daily backup $($latest.Name) is older than 36 hours" }
  Write-Host "REHEARSAL ON: $($latest.FullName) ($($latest.Length) bytes, $($latest.LastWriteTime))"
  $stamp = Get-Date -Format 'yyyyMMdd_HHmmss'
  $src = "$work\fr002_src_$stamp"
  $rwork = "$work\fr002_rehearsal_$stamp"
  & git clone --quiet $repoUrl $src
  if ($LASTEXITCODE -ne 0) { throw "git clone failed (exit $LASTEXITCODE)" }
  & git -C $src checkout --quiet --detach $release
  if (($LASTEXITCODE -ne 0) -or ([string](& git -C $src rev-parse HEAD) -ne $release)) { throw "the rehearsal copy of the code is not at $release" }
  $r = Invoke-Tool $py @("$src\tools\fin_recon_002_staging_check.py", '--backup', $latest.FullName, '--books', "$frdir\books", '--work', $rwork, '--decisions', $table, '--forbid-cards', $forbid)
  if (Test-Path -LiteralPath "$rwork\copy.db") { Remove-Item -LiteralPath "$rwork\copy.db" -Force }
  if (($r.Code -ne 0) -or (@($r.Lines -match '^RESULT: PASS \(0 failed\)').Count -ne 1)) { throw "the rehearsal did not pass (exit $($r.Code)) - report: $rwork\STAGING_CHECK_REPORT.txt" }
  Set-Content -LiteralPath "$work\fr002_step2_passed_$release.txt" -Value "$($latest.Name) $stamp" -Encoding ASCII
  Write-Host "REHEARSAL: PASS on $($latest.Name), report $rwork\STAGING_CHECK_REPORT.txt, the copy with data deleted"
  Write-Host 'MIGRATIONS BEFORE THE RELEASE:'
  & $py tools\check_migration_drift.py --db $db 2>&1 | ForEach-Object { Write-Host "  $_" }
} catch {
  $failure = $_.Exception.Message
}
if ($failure) { Write-Host "RESULT: STOP - $failure" } else { Write-Host 'RESULT: CHECK PASSED - go on to step 3' }
Write-Host "LOG FILE: $log"
try { Stop-Transcript | Out-Null } catch { }
}
```

**Что должно получиться:** `RESULT: CHECK PASSED - go on to step 3`. Выше:

- три строки `SERVICE ... : Running`;
- `FILES: ...`;
- `UNTRACKED FILES: 111, none of them is a file of the release` (число —
  сколько на сервере сейчас; 22.10 было 111);
- `RELEASE: 998bc20... -> c4b0396...` и список влитых PR;
- `MAIN: ... - N later commit(s) of main are not taken` — сколько коммитов
  `main` после выпуска остаются в стороне;
- `REHEARSAL: PASS ...`. Это 39 проверок репетиции: май побайтно, 132
  строки, маршруты 137 / 155 / ПТЗ / без карточки, №137 и №155
  `internal`, повторный импорт +0.

Под `MIGRATIONS BEFORE THE RELEASE:` — `DRIFT DETECTED`: известное состояние
сервера, шаг 3 сверяет его до и после.

**Прислать:** `C:\VehicleSoft_Release\release_v132_step2.log`. Если в нём
`CHECK PASSED`, моего ответа ждать не нужно.

## Шаг 3. Выпуск — программа не работает около 10 минут

Что делает блок, по порядку:

1. Ещё раз проверяет всё, что проверял шаг 2. Требует, чтобы шаг 2 прошёл
   именно для этой версии.
2. Останавливает программу и обоих ботов, проверяет, что база свободна.
3. Делает резервную копию в `D:\transport-report-backups\production\daily\` и
   проверяет её.
4. Перематывает код ровно до `c4b0396` (`git merge --ff-only`), более
   поздние коммиты `main` не забирает. Неотслеживаемые файлы сверяются
   списком до и после.
5. Колонка «Фукаро» — **до** запуска нового кода.
6. Привязки июня: `june`, затем `june-anchor`.
7. Таблица решений.
8. Импорт двух книг.
9. Реестр миграций: +4, список ожидающих — как был.
10. Проверка базы против копии пункта 3 (`tools\fin_recon_002_release_check.py`).
11. Запускает программу и ботов; страница входа и раздел Дронов открываются.

Если что-то не так, блок останавливается. Дальше всё зависит от того,
начала ли меняться база:

- **сбой в пунктах 1–4** (база ещё не менялась): блок возвращает код к
  `998bc20` и запускает программу обратно, на прежней версии;
- **сбой в пунктах 5–10** (база уже менялась), в том числе если не прошла
  итоговая проверка пункта 10 или окно прервано: блок **не запускает**
  программу. Он печатает `RESULT: STOP`, путь к резервной копии окна и к
  папке квитанций и оставляет файл-отметку
  `C:\VehicleSoft_Release\fr002_step3_db_stop.txt`. Копия, квитанции и
  журнал остаются на месте. Службы руками не запускать, «Откат» не
  выполнять: путь назад — раздел «Восстановление базы» ниже. Пока программа
  стояла, пользователи ничего не записали, поэтому копия окна возвращает
  базу ровно в состояние до выпуска.

Пока отметка лежит, шаг 3 повторно не запускается.

```powershell
& {
$ErrorActionPreference = 'Continue'
$ProgressPreference = 'SilentlyContinue'
$prod      = 'C:\transport-report'
$db        = 'C:\transport-report\instance\transport.db'
$py        = 'C:\Program Files\Python314\python.exe'
$backupBat = 'C:\transport-report\backup_production_db.bat'
$errLog    = 'C:\transport-report\logs\error.log'
$services  = @('TransportReport', 'TransportBot', 'TransportBot003')
$site      = 'http://10.103.25.14:5050'
$baseline  = '998bc20bfdb81e6d90ab413b63ab59da665aac65'
$reviewed  = '518b6407821c65f3bb17d4f8889d7ec031cb5a4f'
$release   = 'c4b039630fd75264994267abe69f89d9eaba7632'
$runbookTest = 'tests/test_fin_recon_002_release_runbook.py'
$expectMig = 'migrate_drones_works_merged_customer_link_001.py migrate_drones_works_payer_unidentified_001.py'
$frdir     = 'C:\VehicleSoft_Release\fin_recon_002'
$table     = 'C:\VehicleSoft_Release\fin_recon_002\FIN_RECON_002_RELEASE_DECISIONS_20261029.csv'
$tableSha  = '754dab3571f4cae516933fd55cf3ee68a7f83b4a21162767b2833f67220a99d0'
$bookShas  = '3d9ad76cfdd901548225e41045c58973eb43937c0a910afd992854dab5afebb2 f4a6591453268d686722321bac1f84942aa7e8e3b0a976bad9a61d5db3a74215'
$noFile    = 'DRONES_WORKS_JUN2026_MERGED_CUSTOMER_LINK_001 DRONES_WORKS_JUN2026_ZAMINLARI_ANCHOR_137_001 DRONE_ALIAS_DECISIONS_754DAB3571F4CAE5'
$batch     = 'FIN_RECON_002_JUNE_2026'
$forbid    = '589,684-692,803'
$work      = 'C:\VehicleSoft_Release'
$stopMark  = 'C:\VehicleSoft_Release\fr002_step3_db_stop.txt'
$log       = 'C:\VehicleSoft_Release\release_v132_step3.log'
$admin     = ([Security.Principal.WindowsPrincipal][Security.Principal.WindowsIdentity]::GetCurrent()).IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)
function Get-OpenItems([string[]]$lines) {
  for ($i = 0; $i -lt $lines.Count; $i++) {
    if ($lines[$i].Trim() -eq '|---|---|---|---|---|') {
      $count = 0
      for ($j = $i + 1; ($j -lt $lines.Count) -and $lines[$j].StartsWith('|'); $j++) { $count++ }
      return $count
    }
  }
  return -1
}
function Invoke-Tool([string]$exe, [string[]]$arguments) {
  $lines = @(& $exe @arguments 2>&1 | ForEach-Object { "$_" })
  $code = $LASTEXITCODE
  foreach ($line in $lines) { Write-Host "    $line" }
  return [pscustomobject]@{ Code = $code; Lines = $lines }
}
function Get-Drift([string[]]$lines) {
  $registered = -1
  $pending = @()
  $inside = $false
  foreach ($line in $lines) {
    if ($line -match '^registered migrations: (\d+);') { $registered = [int]$Matches[1] }
    if ($line -match '^file-but-not-registered: \d+$') { $inside = $true; continue }
    if ($inside -and ($line -match '^  - (.+)$')) { $pending += $Matches[1]; continue }
    $inside = $false
  }
  return [pscustomobject]@{ Registered = $registered; Pending = (@($pending | Sort-Object) -join ' | ') }
}
function Get-NoFile([string[]]$lines) {
  $names = @()
  $inside = $false
  foreach ($line in $lines) {
    if ($line -match '^registered-but-no-file: \d+$') { $inside = $true; continue }
    if ($inside -and ($line -match '^  - (.+)$')) { $names += $Matches[1]; continue }
    $inside = $false
  }
  return $names
}
function Get-Backup([string[]]$lines) {
  $source = $null
  $dest = $null
  $path = $null
  $integrity = $false
  for ($i = 0; $i -lt $lines.Count; $i++) {
    if ($lines[$i] -match 'Source size\s*:\s*([\d,]+) bytes') { $source = $Matches[1] }
    if ($lines[$i] -match 'Dest size\s*:\s*([\d,]+) bytes') { $dest = $Matches[1] }
    if ($lines[$i] -match 'Integrity check : ok') { $integrity = $true }
    if (($lines[$i] -match '^SUCCESS: Backup written to:') -and ($i + 1 -lt $lines.Count)) { $path = $lines[$i + 1].Trim() }
  }
  return [pscustomobject]@{ Ok = ([bool]$path -and $integrity -and [bool]$source -and ($source -eq $dest)); Size = $dest; Path = $path }
}
function Test-Lock($result) {
  if ($result.Code -eq 0) { return $true }
  return (($result.Code -eq 3) -and (@($result.Lines -match 'no process holds the database').Count -gt 0))
}
function Wait-Services([string]$want, [int]$seconds) {
  $deadline = (Get-Date).AddSeconds($seconds)
  while ($true) {
    $late = @($services | Where-Object { [string](Get-Service -Name $_).Status -ne $want })
    if ($late.Count -eq 0) { return $true }
    if ((Get-Date) -gt $deadline) { return $false }
    Start-Sleep -Seconds 2
  }
}
function Read-NewText([string]$path, [long]$from) {
  if (-not (Test-Path -LiteralPath $path)) { return '' }
  $stream = [System.IO.File]::Open($path, [System.IO.FileMode]::Open, [System.IO.FileAccess]::Read, [System.IO.FileShare]::ReadWrite)
  try {
    if ($stream.Length -lt $from) { $from = 0 }
    [void]$stream.Seek($from, [System.IO.SeekOrigin]::Begin)
    $reader = New-Object System.IO.StreamReader($stream)
    return $reader.ReadToEnd()
  } finally {
    $stream.Dispose()
  }
}
New-Item -ItemType Directory -Force -Path $work | Out-Null
try { Start-Transcript -Path $log -Append | Out-Null } catch { Write-Host 'NOTE: the log file could not be started' }
$stopped = $false
$updated = $false
$dbTouched = $false
$passed = $false
$noStart = $false
$failure = $null
$warnings = @()
$errFrom = 0
try {
  Write-Host '== 1. Checks - nothing is changed yet'
  if (-not $admin) { throw 'this window is not run as administrator - close it and open PowerShell with Run as administrator' }
  if ((hostname) -ne 'srv-yoqsh') { throw "this computer is $(hostname), not SRV-YOQSH" }
  if (Test-Path -LiteralPath $stopMark) { throw "a previous window stopped after the database changed ($stopMark) - run the restore block first" }
  foreach ($path in @($db, $py, $backupBat, $table)) {
    if (-not (Test-Path -LiteralPath $path)) { throw "not found: $path" }
  }
  foreach ($name in $services) {
    if (-not (Get-Service -Name $name -ErrorAction SilentlyContinue)) { throw "service not found: $name" }
  }
  $sha = (Get-FileHash -LiteralPath $table -Algorithm SHA256).Hash.ToLower()
  if ($sha -ne $tableSha) { throw "the decision table is not the release one (SHA $sha)" }
  $bookFiles = @(Get-ChildItem -LiteralPath "$frdir\books" -Filter '*.xlsx' -File -ErrorAction SilentlyContinue)
  $found = (@($bookFiles | ForEach-Object { (Get-FileHash -LiteralPath $_.FullName -Algorithm SHA256).Hash.ToLower() } | Sort-Object) -join ' ')
  if ($found -ne $bookShas) { throw "the books folder must hold exactly the two June books of the release (found $($bookFiles.Count) file(s))" }
  Set-Location -LiteralPath $prod
  & git fetch --quiet origin
  if ($LASTEXITCODE -ne 0) { throw "git fetch failed (exit $LASTEXITCODE) - is there a connection to GitHub?" }
  $head = [string](& git rev-parse HEAD)
  $main = [string](& git rev-parse origin/main)
  & git merge-base --is-ancestor $release $main
  if ($LASTEXITCODE -ne 0) { throw "the release commit is not in main: $release, main $main" }
  & git merge-base --is-ancestor $reviewed $release
  if ($LASTEXITCODE -ne 0) { throw 'the release commit does not contain the checked version' }
  $changed = @(& git diff --name-only $reviewed $release | Where-Object { ($_ -notlike 'docs/*') -and ($_ -notlike '.github/*') -and ($_ -ne $runbookTest) })
  if ($changed.Count -gt 0) { throw "between the checked version and the release commit there are changes outside docs/: $($changed -join ', ')" }
  $open = Get-OpenItems @(& git show "${main}:docs/RELEASE_GATE.md")
  if ($open -lt 0) { throw 'docs/RELEASE_GATE.md could not be read' }
  if ($open -gt 0) { throw "the release gate is closed: $open open item(s) in docs/RELEASE_GATE.md - the deploy waits until they are removed" }
  & git merge-base --is-ancestor $reviewed $head
  if ($LASTEXITCODE -eq 0) { throw 'this version is already on the server - step 3 was done before; go on to step 4' }
  if ($head -ne $baseline) { throw "the program here is at $head, this release was prepared for $baseline" }
  $modified = @(& git status --porcelain --untracked-files=no)
  if ($modified.Count -gt 0) { throw "program files were edited on this server: $($modified -join '; ')" }
  & git merge-base --is-ancestor $head $release
  if ($LASTEXITCODE -ne 0) { throw 'the new version does not continue the current one' }
  $migrations = (@(& git diff --name-only $head $release | Where-Object { $_ -like 'migrate_*' } | Sort-Object) -join ' ')
  if ($migrations -ne $expectMig) { throw "migrations in the release: '$migrations', expected '$expectMig'" }
  if (-not (Test-Path -LiteralPath "$work\fr002_step2_passed_$release.txt")) { throw 'step 2 has not passed for this version - run step 2 first' }
  $books = "$frdir\books"
  $receipts = "$frdir\receipts_$(Get-Date -Format 'yyyyMMdd_HHmmss')"
  New-Item -ItemType Directory -Force -Path $receipts | Out-Null
  Write-Host "RELEASE: $head -> $release"
  $later = @(& git --no-pager log --format=%h "$release..$main")
  Write-Host "MAIN: $main - $($later.Count) later commit(s) of main are not taken"
  $drift0Lines = (Invoke-Tool $py @('tools\check_migration_drift.py', '--db', $db)).Lines
  $drift0 = Get-Drift $drift0Lines
  $noFile0 = @(Get-NoFile $drift0Lines)
  if ($drift0.Registered -lt 0) { throw 'check_migration_drift did not report the registry' }
  if (Test-Path -LiteralPath $errLog) { $errFrom = (Get-Item -LiteralPath $errLog).Length }

  Write-Host '== 2. Stopping the program and both bots'
  $stopped = $true
  foreach ($name in $services) { Stop-Service -Name $name }
  if (-not (Wait-Services 'Stopped' 90)) { throw 'the services did not stop within 90 seconds' }
  Write-Host 'SERVICES: Stopped'
  if (-not (Test-Lock (Invoke-Tool $py @('tools\check_db_lock.py', '--db', $db)))) { throw 'the database is held by another program (check_db_lock)' }

  Write-Host '== 3. Backup of the database'
  $bk = Invoke-Tool $backupBat @()
  $backup = Get-Backup $bk.Lines
  if (($bk.Code -ne 0) -or (-not $backup.Ok)) { throw "the backup did not pass (exit $($bk.Code)) - the program stays on the old version" }
  Write-Host "BACKUP: $($backup.Path) ($($backup.Size) bytes, integrity ok, same size as the database)"

  Write-Host '== 4. Taking the new version'
  $untracked0 = @(& git status --porcelain --untracked-files=all | Where-Object { $_ -match '^\?\? ' })
  $merge = Invoke-Tool 'git' @('merge', '--ff-only', $release)
  if ($merge.Code -ne 0) { throw "git merge --ff-only failed (exit $($merge.Code)) - the program stays on the old version" }
  $updated = $true
  $now = [string](& git rev-parse HEAD)
  if ($now -ne $release) { throw "after the update the code is at $now, expected $release" }
  $untracked1 = @(& git status --porcelain --untracked-files=all | Where-Object { $_ -match '^\?\? ' })
  if (($untracked1 -join '|') -ne ($untracked0 -join '|')) { throw "the untracked files changed during the update: before $($untracked0.Count), after $($untracked1.Count)" }
  Write-Host "UPDATED: $head -> $now (untracked files $($untracked0.Count) before and after, the same list)"

  Write-Host '== 5. The payer_unidentified column - before the new code starts'
  $dbTouched = $true
  $r = Invoke-Tool $py @('migrate_drones_works_payer_unidentified_001.py', '--db', $db)
  if (($r.Code -ne 0) -or (@($r.Lines -match '^Done\. 1 column added to drone_works').Count -ne 1)) { throw "the payer_unidentified migration did not pass (exit $($r.Code))" }

  Write-Host '== 6. June links of the merged cells'
  foreach ($scope in @('june', 'june-anchor')) {
    $r = Invoke-Tool $py @('migrate_drones_works_merged_customer_link_001.py', '--db', $db, '--scope', $scope, '--apply', '--receipt', "$receipts\link_$scope.json")
    if (($r.Code -ne 0) -or (@($r.Lines -match '^RESULT: APPLIED').Count -ne 1)) { throw "the link migration --scope $scope did not pass (exit $($r.Code))" }
  }

  Write-Host '== 7. The decision table'
  $r = Invoke-Tool $py @('tools\drone_alias_decisions.py', '--decisions', $table, '--dir', $books, '--manifest', 'tools\drone_works_manifest.txt', '--db', $db, '--apply', '--receipt', "$receipts\decisions.json", '--forbid-cards', $forbid)
  if (($r.Code -ne 0) -or (@($r.Lines -match '^RESULT: APPLIED').Count -ne 1)) { throw "the decision table did not pass (exit $($r.Code))" }

  Write-Host '== 8. Import of the two June books'
  $r = Invoke-Tool $py @('tools\import_drone_works.py', '--dir', $books, '--manifest', 'tools\drone_works_manifest.txt', '--db', $db, '--apply', '--batch', $batch, '--report', "$receipts\import_report.txt")
  if (($r.Code -ne 0) -or (@($r.Lines -match '^WRITTEN: 0 customers created, 132 works inserted, 0 already present and skipped\.$').Count -ne 1)) { throw "the import did not pass (exit $($r.Code))" }

  Write-Host '== 9. Migration registry'
  $drift1Lines = (Invoke-Tool $py @('tools\check_migration_drift.py', '--db', $db)).Lines
  $drift1 = Get-Drift $drift1Lines
  $wantNoFile = (@($noFile0 + ($noFile -split ' ')) | Sort-Object) -join ' '
  $gotNoFile = (@(Get-NoFile $drift1Lines) | Sort-Object) -join ' '
  if (($drift1.Registered -ne ($drift0.Registered + 4)) -or ($drift1.Pending -ne $drift0.Pending) -or ($gotNoFile -ne $wantNoFile)) { throw "after the release the registry is $($drift1.Registered), pending '$($drift1.Pending)', without a file '$gotNoFile'; expected $($drift0.Registered + 4), '$($drift0.Pending)', '$wantNoFile'" }
  Write-Host "MIGRATIONS REGISTERED: $($drift0.Registered) -> $($drift1.Registered), pending unchanged"

  Write-Host '== 10. The database against the backup of this window'
  $r = Invoke-Tool $py @('tools\fin_recon_002_release_check.py', '--db', $db, '--baseline', $backup.Path)
  if (($r.Code -ne 0) -or (@($r.Lines -match '^RESULT: RELEASE CHECK PASSED').Count -ne 1)) { throw 'the release check did not pass - see the FAIL lines above' }
  $passed = $true
} catch {
  $failure = $_.Exception.Message
} finally {
  if ($dbTouched -and (-not $passed)) {
    $noStart = $true
    if (-not $failure) { $failure = 'the window was interrupted after the database started to change' }
    $mark = @("RELEASE: $release", "BACKUP: $($backup.Path)", "RECEIPTS: $receipts", "LOG: $log", "FAILURE: $failure", "TIME: $(Get-Date -Format 'yyyy-MM-dd HH:mm:ss')")
    try { [System.IO.File]::WriteAllLines($stopMark, [string[]]$mark) } catch { Write-Host "THE STOP FILE COULD NOT BE WRITTEN: $stopMark - keep this window open and send the log" }
    Write-Host '== The database was being changed when the window stopped - the services are NOT started'
    Write-Host "BACKUP OF THIS WINDOW: $($backup.Path)"
    Write-Host "RECEIPTS: $receipts"
    Write-Host "STOP FILE: $stopMark"
    Write-Host 'Do not start the services by hand and do not run the rollback block. Send the log file and the receipts folder; the way back is the restore block.'
  }
  if ($updated -and (-not $dbTouched)) {
    Write-Host '== The database was not changed - the code goes back before the start'
    & git reset --keep $baseline 2>&1 | ForEach-Object { Write-Host "    $_" }
    if (($LASTEXITCODE -ne 0) -or ([string](& git rev-parse HEAD) -ne $baseline)) {
      $noStart = $true
      Write-Host 'THE CODE COULD NOT BE RETURNED - the services are NOT started: send the log file'
    }
  }
  if ($stopped -and (-not $noStart)) {
    Write-Host '== 11. Starting the program and both bots'
    foreach ($name in $services) { Restart-Service -Name $name }
    if (Wait-Services 'Running' 90) {
      Write-Host 'SERVICES: Running'
    } else {
      Write-Host 'THE SERVICES ARE NOT RUNNING - start them by hand: Restart-Service TransportReport; Restart-Service TransportBot; Restart-Service TransportBot003'
      if (-not $failure) { $failure = 'the services did not start within 90 seconds' }
    }
  }
}
if ($stopped -and (-not $failure)) {
  Write-Host '== 12. Smoke check'
  $code = 0
  $body = ''
  $deadline = (Get-Date).AddSeconds(90)
  while ((Get-Date) -lt $deadline) {
    try { $page = Invoke-WebRequest -Uri ($site + '/login') -UseBasicParsing -TimeoutSec 15; $code = [int]$page.StatusCode; $body = [string]$page.Content } catch { $code = 0 }
    if ($code -eq 200) { break }
    Start-Sleep -Seconds 3
  }
  if (($code -ne 200) -or ($body -notmatch 'vs-login-form')) {
    $failure = "the login page did not open (HTTP $code)"
  } else {
    Write-Host 'SMOKE /login: 200, the login form is there'
    $drones = 0
    $dronesBody = ''
    try { $page = Invoke-WebRequest -Uri ($site + '/drones/works') -UseBasicParsing -TimeoutSec 30; $drones = [int]$page.StatusCode; $dronesBody = [string]$page.Content } catch { if ($_.Exception.Response) { $drones = [int]$_.Exception.Response.StatusCode } }
    if (($drones -ne 200) -or ($dronesBody -notmatch 'vs-login-form')) { $failure = "/drones/works answered $drones instead of asking to log in" } else { Write-Host 'SMOKE /drones/works: asks to log in - the section is there' }
  }
  $newText = Read-NewText $errLog $errFrom
  $tracebacks = ([regex]::Matches($newText, 'Traceback')).Count
  Write-Host "NEW TRACEBACKS IN logs\error.log: $tracebacks"
  if ($tracebacks -gt 0) {
    $warnings += 'new errors in logs\error.log'
    @($newText -split "`n" | Select-Object -Last 40) | ForEach-Object { Write-Host "    $_" }
  }
}
Write-Host "PROGRAM VERSION NOW: $([string](& git -C $prod rev-parse --short HEAD))"
if ($failure) {
  Write-Host "RESULT: STOP - $failure"
  if (-not $stopped) { Write-Host 'Nothing was changed: the program kept working on the old version.' }
} elseif ($warnings.Count -gt 0) {
  Write-Host "RESULT: RELEASE PASSED WITH A WARNING - $($warnings -join '; ') - send the log file before step 4"
} else {
  Write-Host 'RESULT: RELEASE PASSED'
}
Write-Host "LOG FILE: $log"
try { Stop-Transcript | Out-Null } catch { }
}
```

**Что должно получиться:** в конце `RESULT: RELEASE PASSED`. Выше, по порядку:

- `RELEASE: 998bc20... -> c4b0396...` и `MAIN: ... not taken`;
- `SERVICES: Stopped`;
- `CLEAN ...` или `STALE: no process holds the database` — оба ответа годятся;
- `BACKUP: D:\transport-report-backups\production\daily\transport_....db (...)`;
- `UPDATED: 998bc20... -> c4b0396... (untracked files 111 before and after, the same list)`;
- `Done. 1 column added to drone_works, no row backfilled.`;
- дважды `RESULT: APPLIED` (привязки);
- `RESULT: APPLIED` (таблица);
- `WRITTEN: 0 customers created, 132 works inserted, 0 already present and skipped.`;
- `MIGRATIONS REGISTERED: 61 -> 65, pending unchanged` (числа — как в
  реестре сервера; разница ровно 4);
- `RESULT: RELEASE CHECK PASSED (0 failed)`;
- `SERVICES: Running`;
- `SMOKE /login: 200 ...` и `SMOKE /drones/works: asks to log in ...`;
- `NEW TRACEBACKS IN logs\error.log: 0`.

**Прислать:** `C:\VehicleSoft_Release\release_v132_step3.log` и папку
`C:\VehicleSoft_Release\fin_recon_002\receipts_...` (квитанции и отчёт
импорта).

## Шаг 4. Проверка глазами — 10 минут, вы как администратор

Обновить страницы с Ctrl+F5.

1. **Дроны → Работы, отчёт за июнь 2026.** В разрезе по заказчикам:
   - есть строка «Плательщик-гражданин не установлен»: 12 работ, 61,80 га;
   - №137 «Бухоро Агрокластер Заминлари МЧЖ» — с работами июня.
2. **Журнал работ с фильтром «Плательщик-гражданин не установлен»** — 12
   строк, у каждой своя сумма и свой долг.
3. **Заказчики:**
   - №137 и №155 — тип «внутренний»;
   - есть одна карточка «Ғиждувон ПТЗ», тоже «внутренний».
4. **Подотчёт операторов, июнь** — открывается, колонки «Собрано / Затраты /
   Сдано / Остаток» на месте.
5. **Май** в тех же отчётах — те же числа, что до выпуска.

**Прислать:** «шаг 4 — всё сходится» или что не так, со снимком экрана.

## Если что-то пошло не так

| Что видно | Что это значит | Что делать |
|---|---|---|
| `RESULT: STOP - this window is not run as administrator` | окно без прав администратора | открыть «от имени администратора», повторить шаг |
| `RESULT: STOP - not found: ...` | нет базы, Python или файла выпуска | положить файлы шага 1; ничего не менялось |
| `RESULT: STOP - the decision table is not the release one` | в папке не та таблица | положить таблицу из приватного пакета; ничего не менялось |
| `RESULT: STOP - the books folder must hold exactly the two June books of the release` | в `books\` лишние файлы или не те книги | оставить ровно две книги из шага 1; ничего не менялось |
| `RESULT: STOP - the release gate is closed` | в `docs/RELEASE_GATE.md` открытые пункты | ждать снятия; ничего не менялось |
| `RESULT: STOP - the release commit is not in main` | в `main` с сервера не видно `c4b0396` | прислать журнал; ничего не менялось |
| `RESULT: STOP - the release commit does not contain the checked version` | постоянные блока не согласованы | прислать журнал; ничего не менялось |
| `RESULT: STOP - between the checked version and the release commit there are changes outside docs/` | в выпуске код, которого не было в проверке | прислать журнал; ничего не менялось |
| `RESULT: STOP - this version is already on the server` | шаг 3 уже выполнен | к шагу 4 |
| `RESULT: STOP - the program here is at ...` | на сервере не `998bc20` | прислать журнал; ничего не менялось |
| `RESULT: STOP - migrations in the release` | состав миграций не тот | прислать журнал; ничего не менялось |
| `RESULT: STOP - files of the release already lie on the server` | файл с именем из выпуска уже лежит на сервере | не удалять его; прислать журнал |
| `RESULT: STOP - the newest daily backup` | ночной копии нет или она старше 36 часов | дождаться ночной копии, повторить шаг 2 |
| `RESULT: STOP - the rehearsal did not pass` | репетиция на копии не сошлась | прислать журнал и `STAGING_CHECK_REPORT.txt`; production не менялся |
| `RESULT: STOP - step 2 has not passed for this version` | шаг 2 не пройден для этой версии | выполнить шаг 2 |
| `RESULT: STOP - the backup did not pass` | копия базы не записалась | прислать журнал; программа запущена на прежней версии |
| `RESULT: STOP - git merge --ff-only failed` | Git не забрал новую версию | прислать журнал; программа запущена на прежней версии |
| `RESULT: STOP - the untracked files changed during the update` | обновление задело неотслеживаемые файлы | ничего не удалять; прислать журнал |
| `RESULT: STOP - a previous window stopped after the database changed` | прошлое окно остановилось после изменения базы | сначала «Восстановление базы»; ничего не менялось |
| `RESULT: STOP - the payer_unidentified migration did not pass` | колонка не добавилась | **программа не запущена**; прислать журнал; «Восстановление базы» |
| `RESULT: STOP - the link migration` | привязки не записаны | **программа не запущена**; прислать журнал и квитанции; «Восстановление базы» |
| `RESULT: STOP - the decision table did not pass` | таблица не записана | **программа не запущена**; прислать журнал и квитанции; «Восстановление базы» |
| `RESULT: STOP - the import did not pass` | книги не загружены | **программа не запущена**; прислать журнал и отчёт импорта из `receipts_...`; «Восстановление базы» |
| `RESULT: STOP - after the release the registry is` | реестр не тот, что ожидался | **программа не запущена**; прислать журнал; «Восстановление базы» |
| `RESULT: STOP - the release check did not pass` | база изменилась не так, как решено | **программа не запущена**; прислать журнал; ничего не исправлять самому; «Восстановление базы» |
| `RESULT: STOP - the window was interrupted after the database started to change` | окно прервано (Ctrl+C) после начала записи | **программа не запущена**; прислать журнал; «Восстановление базы» |
| `RESULT: STOP - no window stopped after a database change` | «Восстановление» без отметки шага 3 | блок не нужен; ничего не менялось |
| `RESULT: STOP - the backup of the window is not found` | копии из отметки нет на диске | не запускать программу; прислать журнал; ничего не менялось |
| `RESULT: STOP - these services are not stopped` | кто-то запустил службы после остановки окна | прислать журнал; ничего не менялось |
| `RESULT: STOP - the restored database differs from the backup` | копия легла не байт в байт | **программа не запущена**; файлы окна лежат в `fr002_db_after_stop_...`; прислать журнал |
| `RESULT: STOP - the window stopped after a database change` | «Откат» после остановки окна с изменённой базой | выполнить «Восстановление базы», не «Откат»; ничего не менялось |
| `RESULT: STOP - /drones/works answered` | раздел Дронов не открывается | прислать журнал; если программа не открывается совсем — «Откат» |

## Критерии STOP — коротко

- **Остановиться**, если:
  - любой блок печатает `RESULT: STOP`;
  - в шаге 4 май не совпадает с тем, что было до выпуска;
  - строка «Плательщик-гражданин не установлен» не 12 работ;
  - №137 или №155 — не «внутренний»;
  - карточек «Ғиждувон ПТЗ» не ровно одна.
- **Если шаг 3 остановился после изменения базы** — программа не запущена,
  это намеренно. Службы руками не запускать. Путь назад — «Восстановление
  базы»: копия окна, сверенная по SHA-256, и код `998bc20`.
- **Откатывать сразу — только код** и только если шаг 3 прошёл, а программа
  не открывается совсем. Данные окна прошли проверку против копии и для
  прежнего кода безопасны:
  - лишняя колонка `payer_unidentified` прежним кодом не читается;
  - строки, карточки и алиасы — обычные записи.
- **Восстанавливать базу из копии шага 3 — только вместе с сессией.**
  Восстановление сотрёт всё, что пользователи записали после запуска.

## Восстановление базы — только если шаг 3 остановился после изменения базы

Когда: шаг 3 напечатал `The database was being changed when the window
stopped`, и лежит отметка `C:\VehicleSoft_Release\fr002_step3_db_stop.txt`.
Сначала прислать журнал и квитанции; блок выполнить после ответа.

Что делает блок:

1. Проверяет отметку, копию окна из неё и то, что службы всё ещё
   остановлены.
2. Убирает файлы базы окна (`transport.db`, `-wal`, `-shm`) в папку
   `C:\VehicleSoft_Release\fr002_db_after_stop_<время>` — ничего не
   удаляется.
3. Кладёт на место копию окна и сверяет её с копией по SHA-256 байт в байт.
4. Возвращает код к `998bc20`.
5. Переименовывает отметку и только после всего этого запускает программу.

Любая проверка не прошла — программа не запускается, а файлы окна остаются
в папке из пункта 2. Пользовательские записи не теряются: пока программа
стояла, их не было.

```powershell
& {
$ErrorActionPreference = 'Continue'
$ProgressPreference = 'SilentlyContinue'
$prod      = 'C:\transport-report'
$db        = 'C:\transport-report\instance\transport.db'
$py        = 'C:\Program Files\Python314\python.exe'
$services  = @('TransportReport', 'TransportBot', 'TransportBot003')
$site      = 'http://10.103.25.14:5050'
$baseline  = '998bc20bfdb81e6d90ab413b63ab59da665aac65'
$reviewed  = '518b6407821c65f3bb17d4f8889d7ec031cb5a4f'
$runbookTest = 'tests/test_fin_recon_002_release_runbook.py'
$work      = 'C:\VehicleSoft_Release'
$stopMark  = 'C:\VehicleSoft_Release\fr002_step3_db_stop.txt'
$log       = 'C:\VehicleSoft_Release\release_v132_restore.log'
$admin     = ([Security.Principal.WindowsPrincipal][Security.Principal.WindowsIdentity]::GetCurrent()).IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)
function Invoke-Tool([string]$exe, [string[]]$arguments) {
  $lines = @(& $exe @arguments 2>&1 | ForEach-Object { "$_" })
  $code = $LASTEXITCODE
  foreach ($line in $lines) { Write-Host "    $line" }
  return [pscustomobject]@{ Code = $code; Lines = $lines }
}
function Test-Lock($result) {
  if ($result.Code -eq 0) { return $true }
  return (($result.Code -eq 3) -and (@($result.Lines -match 'no process holds the database').Count -gt 0))
}
function Wait-Services([string]$want, [int]$seconds) {
  $deadline = (Get-Date).AddSeconds($seconds)
  while ($true) {
    $late = @($services | Where-Object { [string](Get-Service -Name $_).Status -ne $want })
    if ($late.Count -eq 0) { return $true }
    if ((Get-Date) -gt $deadline) { return $false }
    Start-Sleep -Seconds 2
  }
}
New-Item -ItemType Directory -Force -Path $work | Out-Null
try { Start-Transcript -Path $log -Append | Out-Null } catch { Write-Host 'NOTE: the log file could not be started' }
$moved = $false
$restored = $false
$keep = $null
$failure = $null
try {
  if (-not $admin) { throw 'this window is not run as administrator - close it and open PowerShell with Run as administrator' }
  if ((hostname) -ne 'srv-yoqsh') { throw "this computer is $(hostname), not SRV-YOQSH" }
  if (-not (Test-Path -LiteralPath $stopMark)) { throw 'no window stopped after a database change - this block is not needed' }
  $backupPath = ''
  foreach ($line in @(Get-Content -LiteralPath $stopMark)) { if ($line -match '^BACKUP: (.+)$') { $backupPath = $Matches[1].Trim() } }
  if ((-not $backupPath) -or (-not (Test-Path -LiteralPath $backupPath))) { throw "the backup of the window is not found: '$backupPath'" }
  $running = @($services | Where-Object { [string](Get-Service -Name $_).Status -ne 'Stopped' })
  if ($running.Count -gt 0) { throw "these services are not stopped: $($running -join ', ') - the restore works only while the program is stopped" }
  Set-Location -LiteralPath $prod
  $head = [string](& git rev-parse HEAD)
  if ($head -ne $baseline) {
    & git merge-base --is-ancestor $reviewed $head
    if ($LASTEXITCODE -ne 0) { throw 'the program is neither at the previous version nor at this release - restore only together with the session' }
    $changed = @(& git diff --name-only $reviewed $head | Where-Object { ($_ -notlike 'docs/*') -and ($_ -notlike '.github/*') -and ($_ -ne $runbookTest) })
    if ($changed.Count -gt 0) { throw "the program is past this release (code changed after it: $($changed -join ', ')) - restore only together with the session" }
    & git merge-base --is-ancestor $baseline $head
    if ($LASTEXITCODE -ne 0) { throw 'the previous version is not an ancestor of the current one' }
  }
  $modified = @(& git status --porcelain --untracked-files=no)
  if ($modified.Count -gt 0) { throw "program files were edited on this server: $($modified -join '; ')" }
  if (-not (Test-Lock (Invoke-Tool $py @('tools\check_db_lock.py', '--db', $db)))) { throw 'the database is held by another program (check_db_lock)' }
  $want = (Get-FileHash -LiteralPath $backupPath -Algorithm SHA256).Hash
  $keep = "$work\fr002_db_after_stop_$(Get-Date -Format 'yyyyMMdd_HHmmss')"
  New-Item -ItemType Directory -Force -Path $keep | Out-Null
  Write-Host '== Moving the database files of the stopped window aside - nothing is deleted'
  $moved = $true
  foreach ($suffix in @('', '-wal', '-shm', '-journal')) {
    if (Test-Path -LiteralPath "$db$suffix") { Move-Item -LiteralPath "$db$suffix" -Destination $keep -ErrorAction Stop }
  }
  Write-Host "KEPT: $keep"
  Copy-Item -LiteralPath $backupPath -Destination $db -ErrorAction Stop
  $got = (Get-FileHash -LiteralPath $db -Algorithm SHA256).Hash
  if ($got -ne $want) { throw 'the restored database differs from the backup' }
  foreach ($suffix in @('-wal', '-shm', '-journal')) {
    if (Test-Path -LiteralPath "$db$suffix") { throw "a $suffix file is beside the restored database" }
  }
  Write-Host "RESTORED: $backupPath -> $db (SHA-256 $want, the same)"
  if ($head -ne $baseline) {
    & git reset --keep $baseline 2>&1 | ForEach-Object { Write-Host "    $_" }
    if ($LASTEXITCODE -ne 0) { throw "git reset --keep failed (exit $LASTEXITCODE)" }
  }
  $now = [string](& git rev-parse HEAD)
  if ($now -ne $baseline) { throw "after the restore the code is at $now, expected $baseline" }
  Write-Host "CODE: $head -> $now"
  Rename-Item -LiteralPath $stopMark -NewName "fr002_step3_db_stop_restored_$(Get-Date -Format 'yyyyMMdd_HHmmss').txt" -ErrorAction Stop
  $restored = $true
} catch {
  $failure = $_.Exception.Message
} finally {
  if ($restored) {
    Write-Host '== Starting the program and both bots'
    foreach ($name in $services) { Restart-Service -Name $name }
    if (Wait-Services 'Running' 90) {
      Write-Host 'SERVICES: Running'
    } else {
      Write-Host 'THE SERVICES ARE NOT RUNNING - start them by hand: Restart-Service TransportReport; Restart-Service TransportBot; Restart-Service TransportBot003'
      if (-not $failure) { $failure = 'the services did not start within 90 seconds' }
    }
  } elseif ($moved) {
    Write-Host "THE SERVICES ARE NOT STARTED - the database files of the window are kept in $keep; send the log file"
  } else {
    Write-Host 'Nothing was changed: the services stay stopped.'
  }
}
if ($restored -and (-not $failure)) {
  $code = 0
  $deadline = (Get-Date).AddSeconds(90)
  while ((Get-Date) -lt $deadline) {
    try { $code = [int](Invoke-WebRequest -Uri ($site + '/login') -UseBasicParsing -TimeoutSec 15).StatusCode } catch { $code = 0 }
    if ($code -eq 200) { break }
    Start-Sleep -Seconds 3
  }
  if ($code -ne 200) { $failure = "the login page did not open (HTTP $code)" } else { Write-Host 'SMOKE /login: 200' }
}
Write-Host "PROGRAM VERSION NOW: $([string](& git -C $prod rev-parse --short HEAD))"
if ($failure) { Write-Host "RESULT: STOP - $failure" } else { Write-Host 'RESULT: RESTORE PASSED' }
Write-Host "LOG FILE: $log"
try { Stop-Transcript | Out-Null } catch { }
}
```

**Что должно получиться:** `KEPT: ...`, `RESTORED: ... (SHA-256 ..., the same)`,
`CODE: ... -> 998bc20...`, `SERVICES: Running`, `SMOKE /login: 200`,
`RESULT: RESTORE PASSED`. Программа снова на v1.31 с базой до выпуска.
**Прислать:** `C:\VehicleSoft_Release\release_v132_restore.log`. Повторный
выпуск — только после разбора причины.

## Откат — только если после шага 3 программа не открывается

Блок возвращает прежнюю версию программы (`998bc20`, выпуск v1.31). Базу он
**не трогает**: колонка, привязки, карточки и 132 работы остаются. Прежний
код показывает работы «Фукаро» как «Заказчик не определён». **До повторного
выпуска не добавляйте написание «Фукаро» ни к одной карточке.** Резервная
копия шага 3 остаётся на диске D:.

```powershell
& {
$ErrorActionPreference = 'Continue'
$ProgressPreference = 'SilentlyContinue'
$prod      = 'C:\transport-report'
$services  = @('TransportReport', 'TransportBot', 'TransportBot003')
$site      = 'http://10.103.25.14:5050'
$baseline  = '998bc20bfdb81e6d90ab413b63ab59da665aac65'
$reviewed  = '518b6407821c65f3bb17d4f8889d7ec031cb5a4f'
$runbookTest = 'tests/test_fin_recon_002_release_runbook.py'
$work      = 'C:\VehicleSoft_Release'
$stopMark  = 'C:\VehicleSoft_Release\fr002_step3_db_stop.txt'
$log       = 'C:\VehicleSoft_Release\release_v132_rollback.log'
$admin     = ([Security.Principal.WindowsPrincipal][Security.Principal.WindowsIdentity]::GetCurrent()).IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)
function Wait-Services([string]$want, [int]$seconds) {
  $deadline = (Get-Date).AddSeconds($seconds)
  while ($true) {
    $late = @($services | Where-Object { [string](Get-Service -Name $_).Status -ne $want })
    if ($late.Count -eq 0) { return $true }
    if ((Get-Date) -gt $deadline) { return $false }
    Start-Sleep -Seconds 2
  }
}
New-Item -ItemType Directory -Force -Path $work | Out-Null
try { Start-Transcript -Path $log -Append | Out-Null } catch { Write-Host 'NOTE: the log file could not be started' }
$stopped = $false
$failure = $null
try {
  if (-not $admin) { throw 'this window is not run as administrator - close it and open PowerShell with Run as administrator' }
  if ((hostname) -ne 'srv-yoqsh') { throw "this computer is $(hostname), not SRV-YOQSH" }
  if (Test-Path -LiteralPath $stopMark) { throw "the window stopped after a database change ($stopMark) - this block does not fit, run the restore block" }
  Set-Location -LiteralPath $prod
  $head = [string](& git rev-parse HEAD)
  if ($head -eq $baseline) { throw 'the program is already on the previous version - there is nothing to roll back' }
  & git merge-base --is-ancestor $reviewed $head
  if ($LASTEXITCODE -ne 0) { throw 'the program is not at this release - roll back only together with the session' }
  $changed = @(& git diff --name-only $reviewed $head | Where-Object { ($_ -notlike 'docs/*') -and ($_ -notlike '.github/*') -and ($_ -ne $runbookTest) })
  if ($changed.Count -gt 0) { throw "the program is past this release (code changed after it: $($changed -join ', ')) - roll back only together with the session" }
  & git merge-base --is-ancestor $baseline $head
  if ($LASTEXITCODE -ne 0) { throw 'the previous version is not an ancestor of the current one' }
  $modified = @(& git status --porcelain --untracked-files=no)
  if ($modified.Count -gt 0) { throw "program files were edited on this server: $($modified -join '; ')" }
  Write-Host '== Stopping the program and both bots'
  $stopped = $true
  foreach ($name in $services) { Stop-Service -Name $name }
  if (-not (Wait-Services 'Stopped' 90)) { throw 'the services did not stop within 90 seconds' }
  Write-Host '== Returning the previous version'
  & git reset --keep $baseline 2>&1 | ForEach-Object { Write-Host "    $_" }
  if ($LASTEXITCODE -ne 0) { throw "git reset --keep failed (exit $LASTEXITCODE)" }
  $now = [string](& git rev-parse HEAD)
  if ($now -ne $baseline) { throw "after the rollback the code is at $now, expected $baseline" }
  Write-Host "ROLLED BACK: $head -> $now"
} catch {
  $failure = $_.Exception.Message
} finally {
  if ($stopped) {
    foreach ($name in $services) { Restart-Service -Name $name }
    if (Wait-Services 'Running' 90) { Write-Host 'SERVICES: Running' } else {
      Write-Host 'THE SERVICES ARE NOT RUNNING - start them by hand: Restart-Service TransportReport; Restart-Service TransportBot; Restart-Service TransportBot003'
      if (-not $failure) { $failure = 'the services did not start within 90 seconds' }
    }
  }
}
if ($stopped -and (-not $failure)) {
  $code = 0
  $deadline = (Get-Date).AddSeconds(90)
  while ((Get-Date) -lt $deadline) {
    try { $code = [int](Invoke-WebRequest -Uri ($site + '/login') -UseBasicParsing -TimeoutSec 15).StatusCode } catch { $code = 0 }
    if ($code -eq 200) { break }
    Start-Sleep -Seconds 3
  }
  if ($code -ne 200) { $failure = "the login page did not open (HTTP $code)" } else { Write-Host 'SMOKE /login: 200' }
}
Write-Host "PROGRAM VERSION NOW: $([string](& git -C $prod rev-parse --short HEAD))"
if ($failure) { Write-Host "RESULT: STOP - $failure" } else { Write-Host 'RESULT: ROLLBACK PASSED' }
Write-Host "LOG FILE: $log"
try { Stop-Transcript | Out-Null } catch { }
}
```

**Что должно получиться:** `ROLLED BACK: ... -> 998bc20...`, `SERVICES: Running`,
`SMOKE /login: 200`, `RESULT: ROLLBACK PASSED`.

**Почему `git reset --keep`.** Он возвращает файлы программы и отказывается
работать, если в них есть ручные правки. Неотслеживаемые файлы — журналы,
выгрузки, базу — он не трогает никогда.

**Откат данных — только вместе с сессией.** Квитанции шага 3 несут обратный
SQL каждой привязки и список записей таблицы решений. Работы импорта
помечены партией `FIN_RECON_002_JUNE_2026`.

**Прислать:** `C:\VehicleSoft_Release\release_v132_rollback.log` и журнал шага 3.
