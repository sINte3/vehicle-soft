# Выпуск v1.26 на рабочий сервер — пошагово

Для владельца. Всё делается на сервере **SRV-YOQSH**. Каждый шаг — «скопировать,
вставить, нажать Enter, посмотреть последнюю строку». Треки —
`docs/tracks/agro-work.md` и `docs/tracks/gps-plan-fakt.md`, их ведёт одна
сессия. Выпуск исправляет ошибку выпуска v1.25: в таблице «Свода» сверки
agro-work столбец «Машин» по строкам недосчитывает машины, у которых в
периоде есть заявка, но нет суток работы по GPS. 09.10 на рабочей базе —
152 по строкам при «Итого» 246. «Итого», девять чисел и светофор объёма
ошибка не задевает. Разбор — трек agro-work, раздел 9.2.

Порядок и блоки — те же, что у выпусков v1.20 (01.10), v1.21 (02.10), v1.22,
v1.23, v1.24 (08.10) и v1.25 (09.10), прошедших на этом сервере: блоки — из
`docs/AGRO_WORK_RELEASE_V125_RUNBOOK.md`, меняются только версии и имена
журналов. **Без миграции**: в этом выпуске база не меняется.

## Что произойдёт — коротко

1. Программа на сервере обновится с версии `fc7896c` (выпуск v1.25) до новой.
   В работе программы меняется одно: столбец «Машин» в таблице «Свода»
   сверки agro-work — на экране и на листе «Свод» книги отчёта — снова
   считает все машины строки, в том числе те, у которых в периоде есть
   только заявка. «Итого», карточки, остальные столбцы и вывод отчёта в
   консоль — те же. Остальное в выпуске — тесты и документы.
2. Миграций нет — база не меняется.
3. Во время обновления программа и оба Telegram-бота **не работают 3–5
   минут**. Предупредите людей заранее.
4. Перед обновлением делается резервная копия базы и сразу проверяется.
5. После обновления — проверка глазами: девять чисел «Итого» на «Своде» должны
   остаться теми же, что были до выпуска, а у трёх строк, где «Машин» сейчас
   0, — появиться машины.

Всего около получаса, простой программы — 3–5 минут.

**Когда:** в спокойное время, когда программой почти не пользуются. **Не с
00:30 до 05:00** — ночью на сервере работают свои задачи: сбор GPS, резервная
копия, суточный расчёт и ночная загрузка agro-work (в 04:00).

**Обновление кода в `C:\transport-report`** — не командой `git pull`, а
перемоткой (`git merge --ff-only`) ровно на проверенную версию и только после
резервной копии, как в выпусках v1.20–v1.25.

---

## Как выполнять команды — прочитайте один раз

**Открыть PowerShell от имени администратора.**

1. Нажмите «Пуск», наберите `PowerShell`.
2. На строке «Windows PowerShell» нажмите правую кнопку мыши →
   «Запуск от имени администратора» → «Да».
3. Проверьте: в заголовке окна есть слово «Администратор». Если его нет,
   закройте окно и повторите пункт 2.

**Вставить и выполнить.**

1. У каждого серого блока с командами справа вверху есть значок
   «скопировать» (два квадратика). Нажмите его — скопируется весь блок.
2. Щёлкните **правой** кнопкой мыши внутри окна PowerShell — текст вставится.
3. Нажмите Enter. Если строка начинается с `>>`, нажмите Enter ещё раз.
4. Дождитесь, пока внизу снова появится строка вида `PS C:\...>`. Пока её
   нет, команда работает: окно не закрывать.

**Главное правило.** Большие блоки (шаги 2 и 3) в конце печатают строку
`RESULT: ...`:

- `RESULT: CHECK PASSED` или `RESULT: RELEASE PASSED` — можно к следующему
  шагу;
- `RESULT: STOP - ...` — **дальше не идти** и ничего не исправлять самому.
  Пришлите файл журнала (путь — в строке `LOG FILE:`) и дождитесь ответа.

Журналы больших блоков пишутся в папку `C:\VehicleSoft_Release`. Чтобы
прислать файл, откройте эту папку в Проводнике и перетащите файл в чат.

---

## Шаг 1. Влить запрос выпуска на GitHub

Блоки этого ранбука проверены вместе с кодом выпуска и лежат в запросе PR
#178. «Влить» — сделать его основной версией, чтобы сервер мог её
забрать. Сам сервер на этом шаге не меняется.

**Важно: до конца шага 4 не вливайте другие PR с кодом.** Выпуск проверен
ровно для кода, который сейчас в `main`, плюс этот PR. Документы вливать
можно. Если в `main` появится другой код, шаги 2 и 3 остановятся сами и
ничего не тронут — тогда напишите мне.

1. Откройте https://github.com/sINte3/vehicle-soft/pull/178
2. Внизу — зелёная кнопка. Если на ней не написано «Merge pull request»,
   нажмите стрелку рядом с ней и выберите **«Create a merge commit»** (не
   «Squash» и не «Rebase»).
3. Нажмите «Merge pull request», затем «Confirm merge».
4. Должна появиться фиолетовая надпись «Merged».

**Прислать:** ничего, я увижу сам.

## Шаг 2. Проверка перед выпуском — ничего не меняет

Проверка, что сервер и новая версия ровно такие, как задумано: тот сервер;
та версия программы сейчас; код новой версии — ровно проверенный (после него
в `main` менялись только документы); гейт выпуска пуст; миграций в выпуске
нет; резервной копии хватит места. Программа продолжает работать, ничего не
останавливается и не меняется.

**Сначала запишите числа** — после выпуска с ними сравнивается:

1. Откройте в браузере
   `http://10.103.25.14:5050/agro-work/?from=2026-09-01&to=2026-09-30`.
2. Вверху — карточка «Работа была: объём против GPS». Запишите её четыре
   строки: «Вне допуска», «На грани», «Сверить нельзя», «В допуске». 09.10
   они были 6 · 1 · 101 · 1.
3. Прокрутите страницу вниз до таблицы и найдите её последнюю строку «Итого».
   В ней девять чисел по порядку: машин, заявок, работа была, работы нет, без
   вердикта (заявки), суток с работой по GPS, покрыто, без заявки, без
   вердикта (сутки). Запишите все девять. 09.10, после выпуска v1.25, они
   были 246 · 2226 · 109 · 1 · 2116 · 1735 · 683 · 439 · 613; сегодня могут
   слегка отличаться — после выпуска должны остаться те, что вы запишете
   сейчас.

**Затем** откройте PowerShell от имени администратора, вставьте блок и
нажмите Enter:

```powershell
& {
$ErrorActionPreference = 'Continue'
$prod      = 'C:\transport-report'
$db        = 'C:\transport-report\instance\transport.db'
$py        = 'C:\Program Files\Python314\python.exe'
$backupBat = 'C:\transport-report\backup_production_db.bat'
$services  = @('TransportReport', 'TransportBot', 'TransportBot003')
$baseline  = 'fc7896c6c3a3e0d4c930ddfac5b7c17e99fe35bd'
$reviewed  = '9e04bcd58513f9f11f6cc059a0ac73a16ac7a58f'
$work      = 'C:\VehicleSoft_Release'
$log       = 'C:\VehicleSoft_Release\release_v126_step2.log'
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
New-Item -ItemType Directory -Force -Path $work | Out-Null
try { Start-Transcript -Path $log -Append | Out-Null } catch { Write-Host 'NOTE: the log file could not be started' }
$failure = $null
try {
  if (-not $admin) { throw 'this window is not run as administrator - close it and open PowerShell with Run as administrator' }
  if ((hostname) -ne 'srv-yoqsh') { throw "this computer is $(hostname), not SRV-YOQSH" }
  foreach ($path in @($db, $py, $backupBat)) {
    if (-not (Test-Path -LiteralPath $path)) { throw "not found: $path" }
  }
  foreach ($name in $services) {
    $service = Get-Service -Name $name -ErrorAction SilentlyContinue
    if (-not $service) { throw "service not found: $name" }
    Write-Host "SERVICE $name : $($service.Status)"
  }
  $free = (Get-PSDrive -Name D -ErrorAction SilentlyContinue).Free
  $size = (Get-Item -LiteralPath $db).Length
  if (-not $free) { throw 'drive D: not found - the backup is written there' }
  if ($free -lt (2 * $size)) { throw "drive D: has $([math]::Round($free / 1MB)) MB free, the backup needs about $([math]::Round($size / 1MB)) MB" }
  Set-Location -LiteralPath $prod
  & git fetch --quiet origin
  if ($LASTEXITCODE -ne 0) { throw "git fetch failed (exit $LASTEXITCODE) - is there a connection to GitHub?" }
  $head = [string](& git rev-parse HEAD)
  $release = [string](& git rev-parse origin/main)
  & git merge-base --is-ancestor $reviewed $release
  if ($LASTEXITCODE -ne 0) { throw 'the checked version is not in main yet - was the release PR merged in step 1?' }
  $changed = @(& git diff --name-only $reviewed $release | Where-Object { $_ -notlike 'docs/*' })
  if ($changed.Count -gt 0) { throw "after the checked version main got changes outside docs/: $($changed -join ', ')" }
  $open = Get-OpenItems @(& git show "${release}:docs/RELEASE_GATE.md")
  if ($open -lt 0) { throw 'docs/RELEASE_GATE.md could not be read' }
  if ($open -gt 0) { throw "the release gate is closed: $open open item(s) in docs/RELEASE_GATE.md - the deploy waits until they are removed" }
  & git merge-base --is-ancestor $reviewed $head
  if ($LASTEXITCODE -eq 0) { throw 'this version is already on the server - step 3 was done before; go on to step 4' }
  if ($head -ne $baseline) { throw "the program here is at $head, this release was prepared for $baseline" }
  $modified = @(& git status --porcelain --untracked-files=no)
  if ($modified.Count -gt 0) { throw "program files were edited on this server: $($modified -join '; ')" }
  & git merge-base --is-ancestor $head $release
  if ($LASTEXITCODE -ne 0) { throw 'the new version does not continue the current one' }
  $migrations = @(& git diff --name-only $head $release | Where-Object { $_ -like 'migrate_*' })
  if ($migrations.Count -gt 0) { throw "migrations in the release: $($migrations -join ', '); this release has none" }
  Write-Host "RELEASE: $head -> $release"
  & git --no-pager log --merges --format=%s "$head..$release" | Where-Object { $_ -like 'Merge pull request*' } | ForEach-Object { Write-Host "  $_" }
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

**Что должно получиться:** последняя строка перед `LOG FILE` —
`RESULT: CHECK PASSED - go on to step 3`. Выше — три строки
`SERVICE ... : Running`, строка `RELEASE: fc7896c... -> ...` и список
влитых изменений: `#178` — этот выпуск, в нём же записи выпуска v1.25.
Если в списке есть ещё PR — значит, они только с документами: чужой код
после проверенной версии блок не пропускает.

**Если `RESULT: STOP - ...`:** дальше не идти. Программа не тронута и
работает как раньше. Пришлите `C:\VehicleSoft_Release\release_v126_step2.log`.

**Прислать:** `C:\VehicleSoft_Release\release_v126_step2.log`. Если в нём
`CHECK PASSED`, мой ответ ждать не нужно — можно сразу к шагу 3.

## Шаг 3. Выпуск — программа не работает 3–5 минут

Что делает блок, по порядку:

1. ещё раз проверяет всё, что проверял шаг 2 (ничего не меняя);
2. останавливает программу и обоих ботов;
3. проверяет, что базу никто не держит открытой;
4. делает резервную копию базы в `D:\transport-report-backups\production\daily\`
   и проверяет её: копия целая и размером ровно как база;
5. забирает новую версию программы с GitHub;
6. проверяет, что миграции в базе остались как были — в этом выпуске их нет;
7. запускает программу и обоих ботов;
8. проверяет, что открывается страница входа и что раздел сверки
   (`/agro-work/`) на месте. Этой проверкой нельзя отличить новую версию от
   старой — адрес у обеих один; новое увидите глазами в шаге 4.

Если на любом пункте что-то не так, блок останавливается и **всё равно
запускает программу обратно** — даже когда остановка случилась посередине.

Вставьте блок в то же окно (или в новое окно от имени администратора) и
нажмите Enter:

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
$baseline  = 'fc7896c6c3a3e0d4c930ddfac5b7c17e99fe35bd'
$reviewed  = '9e04bcd58513f9f11f6cc059a0ac73a16ac7a58f'
$work      = 'C:\VehicleSoft_Release'
$log       = 'C:\VehicleSoft_Release\release_v126_step3.log'
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
$failure = $null
$warnings = @()
$errFrom = 0
try {
  Write-Host '== 1. Checks - nothing is changed yet'
  if (-not $admin) { throw 'this window is not run as administrator - close it and open PowerShell with Run as administrator' }
  if ((hostname) -ne 'srv-yoqsh') { throw "this computer is $(hostname), not SRV-YOQSH" }
  foreach ($path in @($db, $py, $backupBat)) {
    if (-not (Test-Path -LiteralPath $path)) { throw "not found: $path" }
  }
  foreach ($name in $services) {
    if (-not (Get-Service -Name $name -ErrorAction SilentlyContinue)) { throw "service not found: $name" }
  }
  Set-Location -LiteralPath $prod
  & git fetch --quiet origin
  if ($LASTEXITCODE -ne 0) { throw "git fetch failed (exit $LASTEXITCODE) - is there a connection to GitHub?" }
  $head = [string](& git rev-parse HEAD)
  $release = [string](& git rev-parse origin/main)
  & git merge-base --is-ancestor $reviewed $release
  if ($LASTEXITCODE -ne 0) { throw 'the checked version is not in main yet - was the release PR merged in step 1?' }
  $changed = @(& git diff --name-only $reviewed $release | Where-Object { $_ -notlike 'docs/*' })
  if ($changed.Count -gt 0) { throw "after the checked version main got changes outside docs/: $($changed -join ', ')" }
  $open = Get-OpenItems @(& git show "${release}:docs/RELEASE_GATE.md")
  if ($open -lt 0) { throw 'docs/RELEASE_GATE.md could not be read' }
  if ($open -gt 0) { throw "the release gate is closed: $open open item(s) in docs/RELEASE_GATE.md - the deploy waits until they are removed" }
  & git merge-base --is-ancestor $reviewed $head
  if ($LASTEXITCODE -eq 0) { throw 'this version is already on the server - step 3 was done before; go on to step 4' }
  if ($head -ne $baseline) { throw "the program here is at $head, this release was prepared for $baseline" }
  $modified = @(& git status --porcelain --untracked-files=no)
  if ($modified.Count -gt 0) { throw "program files were edited on this server: $($modified -join '; ')" }
  & git merge-base --is-ancestor $head $release
  if ($LASTEXITCODE -ne 0) { throw 'the new version does not continue the current one' }
  $migrations = @(& git diff --name-only $head $release | Where-Object { $_ -like 'migrate_*' })
  if ($migrations.Count -gt 0) { throw "migrations in the release: $($migrations -join ', '); this release has none" }
  Write-Host "RELEASE: $head -> $release"
  $drift0 = Get-Drift (Invoke-Tool $py @('tools\check_migration_drift.py', '--db', $db)).Lines
  if ($drift0.Registered -lt 0) { throw 'check_migration_drift did not report the registry' }
  if (Test-Path -LiteralPath $errLog) { $errFrom = (Get-Item -LiteralPath $errLog).Length }

  Write-Host '== 2. Stopping the program and both bots'
  $stopped = $true
  foreach ($name in $services) { Stop-Service -Name $name }
  if (-not (Wait-Services 'Stopped' 90)) { throw 'the services did not stop within 90 seconds' }
  Write-Host 'SERVICES: Stopped'

  Write-Host '== 3. Is the database free'
  if (-not (Test-Lock (Invoke-Tool $py @('tools\check_db_lock.py', '--db', $db)))) { throw 'the database is held by another program (check_db_lock)' }

  Write-Host '== 4. Backup of the database'
  $bk = Invoke-Tool $backupBat @()
  $backup = Get-Backup $bk.Lines
  if (($bk.Code -ne 0) -or (-not $backup.Ok)) { throw "the backup did not pass (exit $($bk.Code)) - the program stays on the old version" }
  Write-Host "BACKUP: $($backup.Path) ($($backup.Size) bytes, integrity ok, same size as the database)"

  Write-Host '== 5. Taking the new version'
  $untracked0 = @(& git status --porcelain --untracked-files=all | Where-Object { $_ -match '^\?\? ' })
  $merge = Invoke-Tool 'git' @('merge', '--ff-only', $release)
  if ($merge.Code -ne 0) { throw "git merge --ff-only failed (exit $($merge.Code)) - the program stays on the old version" }
  $now = [string](& git rev-parse HEAD)
  if ($now -ne $release) { throw "after the update the code is at $now, expected $release" }
  $untracked1 = @(& git status --porcelain --untracked-files=all | Where-Object { $_ -match '^\?\? ' })
  Write-Host "UPDATED: $head -> $now (untracked files before $($untracked0.Count), after $($untracked1.Count))"

  Write-Host '== 6. Migrations - this release has none'
  $drift1 = Get-Drift (Invoke-Tool $py @('tools\check_migration_drift.py', '--db', $db)).Lines
  if (($drift1.Registered -ne $drift0.Registered) -or ($drift1.Pending -ne $drift0.Pending)) { throw "after the update the migrations are $($drift1.Registered) registered, pending '$($drift1.Pending)'; before the release they were $($drift0.Registered), pending '$($drift0.Pending)'" }
  Write-Host "MIGRATIONS REGISTERED: $($drift0.Registered) -> $($drift1.Registered), unchanged"
} catch {
  $failure = $_.Exception.Message
} finally {
  if ($stopped) {
    Write-Host '== 7. Starting the program and both bots'
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
  Write-Host '== 8. Smoke check'
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
    $agro = 0
    $agroBody = ''
    try { $page = Invoke-WebRequest -Uri ($site + '/agro-work/') -UseBasicParsing -TimeoutSec 30; $agro = [int]$page.StatusCode; $agroBody = [string]$page.Content } catch { if ($_.Exception.Response) { $agro = [int]$_.Exception.Response.StatusCode } }
    if (($agro -ne 200) -or ($agroBody -notmatch 'vs-login-form')) { $failure = "/agro-work/ answered $agro instead of asking to log in" } else { Write-Host 'SMOKE /agro-work/: asks to log in - the section is there' }
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

**Что должно получиться:** в конце `RESULT: RELEASE PASSED`. Выше, по
порядку:

- `RELEASE: fc7896c... -> ...`;
- `SERVICES: Stopped`;
- `CLEAN -- no other process has the database open` или
  `STALE: no process holds the database` — оба ответа годятся: второй — известная
  особенность этой базы, а не ошибка (`docs/RELEASE_GATE.md`, «Приборы с
  известным шумом»);
- `BACKUP: D:\transport-report-backups\production\daily\transport_....db (...
  bytes, integrity ok, same size as the database)`;
- `UPDATED: fc7896c... -> ...`;
- `MIGRATIONS REGISTERED: 60 -> 60, unchanged`;
- `SERVICES: Running`;
- `SMOKE /login: 200 ...` и `SMOKE /agro-work/: asks to log in ...`;
- `NEW TRACEBACKS IN logs\error.log: 0`.

Строк от инструментов много — смотреть нужно только на `RESULT`.

**Если `RESULT: STOP - ...`:** дальше не идти. Программа в любом случае
снова запущена — блок запускает её сам. Строка `PROGRAM VERSION NOW:`
говорит, какая версия работает: `fc7896c` — прежняя, ничего не поменялось;
другое значение — новая. Пришлите `C:\VehicleSoft_Release\release_v126_step3.log`.
Если программа в браузере не открывается совсем, — раздел «Откат» в конце.

**Если `RESULT: RELEASE PASSED WITH A WARNING`:** выпуск прошёл, но в журнале
ошибок программы появились новые записи. Пришлите журнал шага 3 и дождитесь
ответа до шага 4.

**Прислать:** `C:\VehicleSoft_Release\release_v126_step3.log`.

## Шаг 4. Проверка глазами — 10 минут

**4.1. Девять чисел.** Откройте ту же страницу, что в шаге 2, и обновите её
(F5): `http://10.103.25.14:5050/agro-work/?from=2026-09-01&to=2026-09-30`. В
последней строке «Итого» — те же девять чисел, что вы записали. Выпуск
вердикты не меняет, поэтому совпасть должно до единицы. Если хоть одно число
другое — остановитесь, не откатывайте сами, пришлите снимок экрана и оба
набора чисел.

**4.2. «Машин» по строкам.** В той же таблице первый столбец с числами —
«Машин». Найдите три строки: «Пахтасаноаттранс · 7. Юк ташувчи
техникалар», «Мирзачул · 5. Комбайнлар» и «Чорва · 3. Қатнов тракторлар».
В версии v1.25 у всех трёх 0. Теперь — не 0. 08.10, в версии v1.24, где
этой ошибки ещё не было, было 17, 9 и 5; если девять чисел 4.1 те же, что
09.10, будут те же 17, 9 и 5. Что сумма «Машин» по всем строкам равна
«Итого», я проверю по книге отчёта (4.4) — складывать глазами не нужно.

**4.3. Карточка объёма.** Вверху «Свода» карточка «Работа была: объём
против GPS» — те же четыре строки, что вы записали в шаге 2: «Вне допуска»,
«На грани», «Сверить нельзя», «В допуске». Выпуск их не меняет.

**4.4. Отчёт на рабочем сервере** — только чтение; результат кладётся в папку
журналов, а не в папку программы:

```powershell
cd C:\transport-report
& "C:\Program Files\Python314\python.exe" tools\agro_work_reconcile.py --from 2026-09-01 --to 2026-09-30 --out C:\VehicleSoft_Release\agro_work_reconcile_v126.xlsx *> C:\VehicleSoft_Release\agro_reconcile_v126.log
Get-Content C:\VehicleSoft_Release\agro_reconcile_v126.log
```

Ожидаемо: строки `applications: ...` и `machine-days with GPS work: ...` —
те же числа, что в «Итого» (4.1), кроме числа машин; строка `work confirmed,
volume vs GPS: ...` — те же четыре числа, что в карточке (4.3). Числа машин
консоль не печатает: их я проверю по книге, лист «Свод» — сумма «Машин» по
строкам должна равняться «Итого».

**Прислать:** вывод 4.4, файл `C:\VehicleSoft_Release\agro_work_reconcile_v126.xlsx`
и «шаг 4 — всё сходится» или что не так. Метку выпуска (тег) поставите вы —
команду я пришлю после вашего ответа.

## Если что-то пошло не так

| Что видно | Что это значит | Что делать |
|---|---|---|
| `RESULT: STOP - this window is not run as administrator` | окно PowerShell открыто без прав администратора | закрыть окно, открыть «от имени администратора», повторить шаг |
| `RESULT: STOP - the release gate is closed` | в `docs/RELEASE_GATE.md` есть открытые пункты | ждать, пока их трек снимет строку, затем повторить шаг 2; ничего не менялось |
| `RESULT: STOP - the checked version is not in main yet` | PR выпуска ещё не влит | шаг 1; ничего не менялось |
| `RESULT: STOP - after the checked version main got changes outside docs/` | после проверки в `main` влит чужой код | прислать журнал; ничего не менялось, выпуск я пересоберу |
| `RESULT: STOP - this version is already on the server` | шаг 3 уже выполнен раньше | идти к шагу 4 |
| `RESULT: STOP - the program here is at ...` | на сервере не та версия, для которой готовился выпуск | прислать журнал; ничего не менялось |
| `RESULT: STOP - migrations in the release` | в выпуск попала миграция | прислать журнал; ничего не менялось |
| `RESULT: STOP - the backup did not pass` | копия базы не записалась или не совпала | прислать журнал; программа снова запущена на прежней версии |
| `RESULT: STOP - git merge --ff-only failed` | Git не смог забрать новую версию — например, мешает файл с тем же именем | прислать журнал; программа снова запущена на прежней версии |
| `RESULT: STOP - after the update the migrations are ...` | после обновления реестр миграций выглядит иначе, чем до | прислать журнал; программа запущена на новой версии, база не менялась |
| `RESULT: STOP - /agro-work/ answered ... instead of asking to log in` | службы запущены, но раздел сверки не открывается | прислать журнал; строка `PROGRAM VERSION NOW:` скажет, какая версия работает |
| в 4.1 хоть одно из девяти чисел другое | вердикты после выпуска иные, чем до | не откатывать самому; прислать снимок экрана и оба набора чисел |
| в 4.2 у трёх строк «Машин» по-прежнему 0 | браузер показал страницу из своей памяти или работает прежняя версия | обновить страницу с Ctrl+F5; если 0 остался — прислать снимок и журнал шага 3 |
| в 4.3 четыре строки карточки другие, чем в шаге 2 | светофор объёма после выпуска иной, чем до | не откатывать самому; прислать снимок и оба набора чисел |

## Откат — только если после шага 3 программа не открывается

Выполнять **только** если шаг 3 закончился `RESULT: STOP`, программа в
браузере не открывается совсем, а ответа от меня нет. Блок возвращает
прежнюю версию программы (`fc7896c`, выпуск v1.25): в ней «Машин» по строкам
«Свода» снова недосчитан. Базу он **не** трогает: миграций в выпуске нет,
сверка только читает. Резервная копия шага 3 остаётся на диске D:.

```powershell
& {
$ErrorActionPreference = 'Continue'
$ProgressPreference = 'SilentlyContinue'
$prod      = 'C:\transport-report'
$services  = @('TransportReport', 'TransportBot', 'TransportBot003')
$site      = 'http://10.103.25.14:5050'
$baseline  = 'fc7896c6c3a3e0d4c930ddfac5b7c17e99fe35bd'
$reviewed  = '9e04bcd58513f9f11f6cc059a0ac73a16ac7a58f'
$work      = 'C:\VehicleSoft_Release'
$log       = 'C:\VehicleSoft_Release\release_v126_rollback.log'
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
  Set-Location -LiteralPath $prod
  $head = [string](& git rev-parse HEAD)
  if ($head -eq $baseline) { throw 'the program is already on the previous version - there is nothing to roll back' }
  & git merge-base --is-ancestor $reviewed $head
  if ($LASTEXITCODE -ne 0) { throw 'the program is not at this release - roll back only together with the session' }
  $changed = @(& git diff --name-only $reviewed $head | Where-Object { $_ -notlike 'docs/*' })
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

**Что должно получиться:** `ROLLED BACK: ... -> fc7896c...`,
`SERVICES: Running`, `SMOKE /login: 200`, `RESULT: ROLLBACK PASSED`.

**Почему `git reset --keep`, а не `--hard`.** `--keep` возвращает файлы
программы к прежней версии и **отказывается** работать, если в них есть
правки, сделанные руками; неотслеживаемые файлы — журналы, выгрузки, база —
он не трогает никогда. Запрет устава на `git reset --hard` защищает общую
историю `main`; здесь двигается только рабочая копия сервера, назад ровно на
тот коммит, с которого шаг 3 её сдвинул.

**Прислать:** `C:\VehicleSoft_Release\release_v126_rollback.log` и журнал
шага 3.
