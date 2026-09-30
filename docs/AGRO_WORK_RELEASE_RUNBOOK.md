# agro-work: выпуск на рабочий сервер — пошагово

Для владельца. Всё делается на сервере **SRV-YOQSH**. Программировать не
нужно: каждый шаг — «скопировать, вставить, нажать Enter, посмотреть
последнюю строку». Трек — `docs/tracks/agro-work.md`.

## Что произойдёт — коротко

1. Программа на сервере обновится до новой версии. В ней появится раздел
   «Сверка agro-work». Тем же выпуском приедут уже влитые работы соседних
   треков: GPS (PR #153, проверен на площадке) — карта со спутником на
   экране «Факт по технике» и спецтехника без гектаров; Дроны (PR #152) —
   принятая площадь DJI в отчётах. Всё остальное работает как раньше.
2. Во время обновления программа и оба Telegram-бота **не работают 5–10
   минут**. Предупредите людей заранее.
3. Перед обновлением делается резервная копия базы и сразу проверяется. Если
   что-то пойдёт не так, данные не пропадут.
4. После обновления — первая загрузка заявок из agro-work (около 40 минут,
   программа в это время работает как обычно), перенос ваших решений с
   проверочной копии и ночное расписание загрузки.
5. В agro-work по-прежнему **ничего не записывается**: программа только
   читает оттуда. Пароль от agro-work нигде не показывается.

Всего около полутора часов; вашего внимания — около получаса; простой
программы — 5–10 минут.

**Когда:** в спокойное время, когда программой почти не пользуются.
**Не с 00:30 до 05:00** — ночью на сервере работают свои задачи: сбор GPS,
резервная копия, суточный расчёт.

**Сейчас выпуск ждёт приёмки Дронов.** PR #152 Дронов (влит 30.09) положил
строку в `docs/RELEASE_GATE.md`: «UAT владельца на настоящих данных» по
`docs/DJI_AREA_ACCEPTED_PROPAGATION_001.md`, §5. Пока строка не снята,
прод-деплой закрыт для всего проекта — это правило проекта, и шаги 2 и 3 сами
его проверяют: ответят `RESULT: STOP - the release gate is closed` и ничего
не тронут. Порядок такой: шаг 1 можно сделать сразу; приёмку Дронов ведёт их
сессия; когда трек Дронов снимет строку, начинайте с шага 2.

**Почему в этом ранбуке есть обновление кода в `C:\transport-report`.**
28.09 было решено: обновление рабочей папки в шаги владельцу не ставить.
30.09 вы поручили выпуск этой сессии («Релиз делай если нужно»), поэтому шаг 3
обновляет код — но не командой `git pull`, а перемоткой
(`git merge --ff-only`) ровно на проверенную версию и только после резервной
копии. Порядок взят из `docs/RELEASE_AND_BACKUP_PROCEDURE.md`, шаги 1–9.

---

## Как выполнять команды — прочитайте один раз

**Открыть PowerShell от имени администратора.**

1. Нажмите «Пуск», наберите `PowerShell`.
2. На строке «Windows PowerShell» нажмите правую кнопку мыши →
   «Запуск от имени администратора» → «Да».
3. Проверьте: в заголовке окна есть слово «Администратор». Если его нет,
   закройте окно и повторите пункт 2.

**Вставить и выполнить.**

1. У каждого серого блока с командами на этой странице справа вверху есть
   значок «скопировать» (два квадратика). Нажмите его — скопируется весь блок.
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

Журналы больших блоков пишутся в папку `C:\VehicleSoft_AgroWork`. Чтобы
прислать файл, откройте эту папку в Проводнике и перетащите файл в чат.

---

## Шаг 0. Файлы на месте — выполнен 30.09

Три `True`: файл учётных данных, ваши ручные связки машин и ваша разметка
методов лежат там, где их ждут шаги 6 и 7.

## Шаг 1. Влить изменения на GitHub

Новая версия лежит на GitHub в запросе PR #154. «Влить» — сделать её
основной, чтобы сервер мог её забрать. Сам сервер на этом шаге не меняется.

**Важно: до конца шага 4 не вливайте другие PR с кодом.** Выпуск проверен
ровно для кода, который сейчас в `main` (agro-work, GPS #153, Дроны #152),
плюс этот PR. Документы вливать можно — например, снятие строки гейта. Если
в `main` появится другой код, шаги 2 и 3 остановятся сами и ничего не
тронут — тогда напишите мне.

1. Откройте https://github.com/sINte3/vehicle-soft/pull/154
2. Внизу — зелёная кнопка. Если на ней не написано «Merge pull request»,
   нажмите стрелку рядом с ней и выберите **«Create a merge commit»** (не
   «Squash» и не «Rebase»).
3. Нажмите «Merge pull request», затем «Confirm merge».
4. Должна появиться фиолетовая надпись «Merged».

**Прислать:** ничего, я увижу сам.

## Шаг 2. Проверка перед выпуском — ничего не меняет

Проверка, что сервер и новая версия ровно такие, как задумано: тот сервер;
та версия программы сейчас; код новой версии — ровно проверенный (после него
в `main` менялись только документы); гейт выпуска пуст; в новой версии ровно
одна миграция; резервной копии хватит места. Программа продолжает работать, ничего не
останавливается и не меняется.

**Сначала запишите одно число** — после выпуска с ним сравнивается:

1. Откройте программу в браузере: «Справочники» → «Техника».
2. Вверху — «Всего в доступных организациях». Запишите это число.

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
$files     = @('C:\VehicleSoft_Secrets\agro_work_credentials.txt', 'C:\VehicleSoft_AgroWork\agro_work_unmatched.csv', 'C:\VehicleSoft_AgroWork\agro_work_methods.xlsx')
$baseline  = 'eb7d0034333e996258232e6e254806c656a99b47'
$reviewed  = 'de81e43289abd3d600cc30ef80b596ec0bf26240'
$migration = 'migrate_agro_work_001.py'
$work      = 'C:\VehicleSoft_AgroWork'
$log       = 'C:\VehicleSoft_AgroWork\release_step2.log'
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
  foreach ($path in @($db, $py, $backupBat) + $files) {
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
  if ($LASTEXITCODE -ne 0) { throw 'the checked version is not in main yet - was PR #154 merged in step 1?' }
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
  if (($migrations.Count -ne 1) -or ($migrations[0] -ne $migration)) { throw "migrations in the release: $($migrations -join ', '); expected only $migration" }
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
`SERVICE ... : Running`, строка `RELEASE: eb7d0034... -> ...` и список
влитых изменений: `Merge pull request #146` … `#154`.

**Если `RESULT: STOP - ...`:** дальше не идти. Программа не тронута и
работает как раньше. Пришлите `C:\VehicleSoft_AgroWork\release_step2.log`.

**Прислать:** `C:\VehicleSoft_AgroWork\release_step2.log`. Если в нём
`CHECK PASSED`, мой ответ ждать не нужно — можно сразу к шагу 3.

## Шаг 3. Выпуск — программа не работает 5–10 минут

Что делает блок, по порядку:

1. ещё раз проверяет всё, что проверял шаг 2 (ничего не меняя);
2. останавливает программу и обоих ботов;
3. проверяет, что базу никто не держит открытой;
4. делает резервную копию базы в `D:\transport-report-backups\production\daily\`
   и проверяет её: копия целая и размером ровно как база;
5. забирает новую версию программы с GitHub;
6. добавляет в базу семь новых пустых таблиц для agro-work — это «миграция».
   Запускает её дважды: второй раз она обязана ответить «уже сделано».
   Существующие данные не трогаются;
7. запускает программу и обоих ботов;
8. проверяет, что открывается страница входа и что раздел «Сверка
   agro-work» на месте.

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
$baseline  = 'eb7d0034333e996258232e6e254806c656a99b47'
$reviewed  = 'de81e43289abd3d600cc30ef80b596ec0bf26240'
$migration = 'migrate_agro_work_001.py'
$pendingId = 'AGRO_WORK_001 (migrate_agro_work_001.py)'
$doneLine  = 'Done. 7 agro_work tables (100 columns), 7 indexes and 4 triggers are in place.'
$againLine = 'Already applied. Nothing to do.'
$work      = 'C:\VehicleSoft_AgroWork'
$log       = 'C:\VehicleSoft_AgroWork\release_step3.log'
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
  if ($LASTEXITCODE -ne 0) { throw 'the checked version is not in main yet - was PR #154 merged in step 1?' }
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
  if (($migrations.Count -ne 1) -or ($migrations[0] -ne $migration)) { throw "migrations in the release: $($migrations -join ', '); expected only $migration" }
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

  Write-Host '== 6. Migration AGRO_WORK_001 - seven new empty tables'
  if (-not (Test-Lock (Invoke-Tool $py @('tools\check_db_lock.py', '--db', $db)))) { throw 'the database is held by another program (check_db_lock)' }
  $drift1 = Get-Drift (Invoke-Tool $py @('tools\check_migration_drift.py', '--db', $db)).Lines
  $want1 = (@(@($drift0.Pending -split ' \| ' | Where-Object { $_ }) + $pendingId | Sort-Object) -join ' | ')
  if ($drift1.Pending -ne $want1) { throw "after the update the pending migrations are '$($drift1.Pending)', expected '$want1'" }
  $first = Invoke-Tool $py @($migration)
  if (($first.Code -ne 0) -or ($first.Lines -notcontains $doneLine)) { throw "the migration did not finish (exit $($first.Code))" }
  $second = Invoke-Tool $py @($migration)
  if (($second.Code -ne 0) -or ($second.Lines -notcontains $againLine)) { throw "the second run of the migration did not answer that it is already applied (exit $($second.Code))" }
  $drift2 = Get-Drift (Invoke-Tool $py @('tools\check_migration_drift.py', '--db', $db)).Lines
  if ($drift2.Pending -ne $drift0.Pending) { throw "after the migration the pending migrations are '$($drift2.Pending)', before the release they were '$($drift0.Pending)'" }
  if ($drift2.Registered -ne ($drift0.Registered + 1)) { throw "registered migrations went from $($drift0.Registered) to $($drift2.Registered), expected one more" }
  Write-Host "MIGRATIONS REGISTERED: $($drift0.Registered) -> $($drift2.Registered)"
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
  Write-Host "RESULT: RELEASE PASSED WITH A WARNING - $($warnings -join '; ') - send the log file before step 5"
} else {
  Write-Host 'RESULT: RELEASE PASSED'
}
Write-Host "LOG FILE: $log"
try { Stop-Transcript | Out-Null } catch { }
}
```

**Что должно получиться:** в конце `RESULT: RELEASE PASSED`. Выше, по
порядку:

- `RELEASE: eb7d0034... -> ...`;
- `SERVICES: Stopped`;
- `CLEAN -- no other process has the database open` или
  `STALE: no process holds the database` — оба ответа годятся: второй — известная
  особенность этой базы, а не ошибка (`docs/RELEASE_GATE.md`, «Приборы с
  известным шумом»);
- `BACKUP: D:\transport-report-backups\production\daily\transport_....db (...
  bytes, integrity ok, same size as the database)`;
- `UPDATED: eb7d0034... -> ...`;
- строка миграции
  `Done. 7 agro_work tables (100 columns), 7 indexes and 4 triggers are in place.`,
  затем `Already applied. Nothing to do.`;
- `MIGRATIONS REGISTERED: ... -> ...` — второе число на один больше первого;
- `SERVICES: Running`;
- `SMOKE /login: 200 ...` и `SMOKE /agro-work/: asks to log in ...`;
- `NEW TRACEBACKS IN logs\error.log: 0`.

Строк от инструментов много — смотреть нужно только на `RESULT`.

**Если `RESULT: STOP - ...`:** дальше не идти. Программа в любом случае
снова запущена — блок запускает её сам. Строка `PROGRAM VERSION NOW:`
говорит, какая версия работает: `eb7d003` — прежняя, ничего не поменялось;
другое значение — новая версия работает, раздел «Сверка agro-work» может
писать «ещё не установлен». Пришлите `C:\VehicleSoft_AgroWork\release_step3.log`.
Если программа в браузере не открывается совсем, — раздел «Откат» в конце.

**Если `RESULT: RELEASE PASSED WITH A WARNING`:** выпуск прошёл, но в журнале
ошибок программы появились новые записи. Пришлите журнал шага 3 и дождитесь
ответа до шага 5.

**Прислать:** `C:\VehicleSoft_AgroWork\release_step3.log`.

## Шаг 4. Проверка глазами — 3 минуты

1. Откройте программу в браузере и войдите.
2. «Справочники» → «Техника»: «Всего в доступных организациях» — то же
   число, что вы записали в шаге 2.
3. В меню слева появился пункт «Сверка agro-work». Откройте его: там написано
   «Импорт ещё не запускался.» Так и должно быть — заявки загрузятся в шаге 5.
4. «GPS план-факт» → «Факт по технике»: выберите любую машину и вчерашний
   день — открывается карта, по умолчанию спутниковая. Это изменение трека
   GPS (PR #153), приехавшее тем же выпуском.
5. «Дроны» → «Сводка» — открывается; рядом с площадью DJI видна принятая
   площадь. Это изменение трека Дронов (PR #152); его подробную приёмку вы
   уже прошли до выпуска.
6. Переключите язык на узбекский и обратно — страницы открываются.
7. Откройте «Отчёт» за вчерашний день — он строится.

**Прислать:** «шаг 4 — всё сходится» или что не так. Метку выпуска в
GitHub (тег) я поставлю сам после вашего ответа.

## Шаг 5. Первая загрузка заявок — около 40 минут, программа работает

Программа читает из agro-work все заявки и историю статусов первой тысячи
закрытых заявок — по одному запросу в 2 секунды, чтобы не нагружать их
сервер. Пользоваться программой в это время можно.

```powershell
cd C:\transport-report
```

```powershell
& "C:\Program Files\Python314\python.exe" tools\agro_work_import.py --credentials C:\VehicleSoft_Secrets\agro_work_credentials.txt --max-history 1000 *> agro_import_prod_1.log
```

Около 40 минут на экране ничего не происходит — это нормально: всё пишется
в файл. Когда снова появится `PS C:\transport-report>`, посмотрите конец
файла:

```powershell
Get-Content agro_import_prod_1.log -Tail 25
```

**Что должно получиться:**

- `agro-work import run 1: ok`;
- в строке `applications:` почти все заявки новые (около 5 000) и
  `rejected 0`;
- `history: fetched 1000 | failed 0 | still pending ...` — «ещё в очереди»
  около 3 000; они догрузятся ночами (шаги 8 и 10);
- строка `links:` — по номеру связались около 286 машин;
- в конце — `unmatched machines: ...`. Файл `agro_work_unmatched.csv` в
  `C:\transport-report` — новый и пустой по `equipment_id`: ваши связки
  возьмутся из проверочной папки в шаге 6.

**Прислать:** `C:\transport-report\agro_import_prod_1.log`.

## Шаг 6. Ваши связки машин — из файла, заполненного 29.09

Сначала план — он ничего не пишет:

```powershell
cd C:\transport-report
```

```powershell
& "C:\Program Files\Python314\python.exe" tools\agro_work_links.py --from-csv C:\VehicleSoft_AgroWork\agro_work_unmatched.csv
```

**Что должно получиться:** 46 строк вида
`link   ... -> equipment ... (название / госномер / хозяйство)` и в конце
`dry run: nothing was written`. Пробегите глазами названия — это те же 46
машин, что вы связали 29.09.

Если так — запись:

```powershell
& "C:\Program Files\Python314\python.exe" tools\agro_work_links.py --from-csv C:\VehicleSoft_AgroWork\agro_work_unmatched.csv --apply
```

**Что должно получиться:** `written: 46 link(s), 0 unlink(s)` и строка
`links now: manual 46 | ...`.

**Прислать:** вывод обеих команд (выделить текст в окне мышью, Enter —
скопировано; вставить в чат).

## Шаг 7. Методы сверки — из вашей книги 29.09

Сначала план — он ничего не пишет:

```powershell
cd C:\transport-report
```

```powershell
& "C:\Program Files\Python314\python.exe" tools\agro_work_methods.py --import C:\VehicleSoft_AgroWork\agro_work_methods.xlsx
```

**Что должно получиться:** строки `set ...` и итог
`set 81 | change 0 | clear 0 | same 20` (если видов работ по-прежнему 101),
затем `dry run: nothing was written`.

Если так — запись:

```powershell
& "C:\Program Files\Python314\python.exe" tools\agro_work_methods.py --import C:\VehicleSoft_AgroWork\agro_work_methods.xlsx --apply
```

**Что должно получиться:** тот же план и `written: 81`.

**Прислать:** вывод обеих команд.

## Шаг 8. Ночная загрузка по расписанию

Каждую ночь в 04:00 программа сама загружает новые и изменившиеся заявки.
04:00 — после ночных задач сервера: сбор GPS в 01:00, резервная копия в
02:00, суточный расчёт GPS в 02:30. Пока догружается история, ночной прогон
берёт до 1 000 историй и идёт около 40 минут (решение 29.09).

Сначала — файл, который запускает загрузку:

```powershell
cd C:\transport-report
```

```powershell
Set-Content -Path C:\transport-report\agro_work_import.bat -Encoding Ascii -Value '@echo off', 'cd /d C:\transport-report', '"C:\Program Files\Python314\python.exe" tools\agro_work_import.py --credentials C:\VehicleSoft_Secrets\agro_work_credentials.txt --max-history 1000 >> agro_work_import.log 2>&1'
```

Затем — задача в расписании Windows:

```powershell
schtasks /create /tn "AgroWorkImport" /tr "C:\transport-report\agro_work_import.bat" /sc daily /st 04:00 /ru SYSTEM /f
```

```powershell
schtasks /query /tn "AgroWorkImport" /fo LIST
```

**Что должно получиться:** после первой команды — сообщение об успешном
создании задачи; после второй — у задачи время следующего запуска в 04:00.
Задача работает от имени системы (SYSTEM): файл учётных данных ей доступен.

**Прислать:** вывод `schtasks /query`.

## Шаг 9. Сверка на рабочей базе

Отчёт сверки за сентябрь — как 29.09 на копии, только теперь на рабочей
базе. База только читается; книга `agro_work_reconcile_2026-09-30.xlsx`
появится в `C:\transport-report`.

```powershell
cd C:\transport-report
```

```powershell
& "C:\Program Files\Python314\python.exe" tools\agro_work_reconcile.py --from 2026-09-01 --to 2026-09-30 *> agro_reconcile_prod.log
```

```powershell
Get-Content agro_reconcile_prod.log
```

**Что должно получиться:** те же разделы, что в шаге 11 на копии, и числа
того же порядка, что в предпросмотре N = 2 от 30.09: заявка → работа —
около 99 выполнено, около 1 без работы, около 1 500 без вердикта; работа →
заявка — «без заявки» около 190. Числа не совпадут до единицы: на рабочей
базе пока 1 000 историй статусов против 1 300 на копии, и в сентябре
добавились последние три дня. После первой ночи с новым кодом GPS (PR #153)
сутки спецтехники становятся «без вердикта»: 9 машино-суток «работы без
заявки» сентября — бензовоз Isuzu `80 259 JAA` (8) и ZOOMLION `80 326 МВА`
(1) — уйдут из нарушений. Экран «Сверка agro-work» показывает то же самое.

**Прислать:** `C:\transport-report\agro_reconcile_prod.log` и книгу
`C:\transport-report\agro_work_reconcile_2026-09-30.xlsx`.

## Шаг 10. Через три-четыре ночи — обычный режим

Каждое утро первые дни — одна команда, она только читает журнал ночной
загрузки:

```powershell
cd C:\transport-report
```

```powershell
Select-String -Path C:\transport-report\agro_work_import.log -Pattern "still pending" | Select-Object -Last 1
```

Когда в строке будет `still pending 0`, ночную загрузку переводят на обычный
режим — не больше 300 историй за ночь, только новые и изменившиеся заявки
(решение 29.09):

```powershell
Set-Content -Path C:\transport-report\agro_work_import.bat -Encoding Ascii -Value '@echo off', 'cd /d C:\transport-report', '"C:\Program Files\Python314\python.exe" tools\agro_work_import.py --credentials C:\VehicleSoft_Secrets\agro_work_credentials.txt >> agro_work_import.log 2>&1'
```

Задачу пересоздавать не нужно: она запускает тот же файл.

**Прислать:** строку со `still pending 0`.

## Шаг 11 (по желанию). Папка проверки

После шагов 6 и 7 ваши решения живут в рабочей базе, с журналом изменений.
Папка `C:\VehicleSoft_AgroWork` с копией базы больше не нужна; файлы
`agro_work_unmatched.csv` и `agro_work_methods.xlsx` из неё можно сохранить
себе на память. Удаляется вручную, когда сочтёте нужным, — но **после** того,
как присланы журналы шагов 2 и 3: они лежат в ней же.
`C:\VehicleSoft_Secrets` не удалять — его читает ночная загрузка.

---

## Если что-то пошло не так

| Что видно | Что это значит | Что делать |
|---|---|---|
| `RESULT: STOP - this window is not run as administrator` | окно PowerShell открыто без прав администратора | закрыть окно, открыть «от имени администратора», повторить шаг |
| `RESULT: STOP - the release gate is closed` | в `docs/RELEASE_GATE.md` есть открытые пункты — сейчас строка Дронов (PR #152) | ждать, пока их трек снимет строку, затем повторить шаг 2; ничего не менялось |
| `RESULT: STOP - the checked version is not in main yet` | PR #154 ещё не влит | шаг 1; ничего не менялось |
| `RESULT: STOP - after the checked version main got changes outside docs/` | после проверки в `main` влит чужой код | прислать журнал; ничего не менялось, выпуск я пересоберу |
| `RESULT: STOP - this version is already on the server` | шаг 3 уже выполнен раньше | идти к шагу 4 |
| `RESULT: STOP - the program here is at ...` | на сервере не та версия, для которой готовился выпуск | прислать журнал; ничего не менялось |
| `RESULT: STOP - the backup did not pass` | копия базы не записалась или не совпала | прислать журнал; программа снова запущена на прежней версии |
| `RESULT: STOP - git merge --ff-only failed` | Git не смог забрать новую версию — например, мешает файл с тем же именем | прислать журнал; программа снова запущена на прежней версии |
| `RESULT: STOP - the migration did not finish` | миграция откатилась целиком | прислать журнал; программа работает на новой версии, «Сверка agro-work» пишет «ещё не установлен» — остальное работает |
| `tables missing: ... run migrate_agro_work_001.py first` в шаге 5 | миграция не применена | ничего не записано и в agro-work не спрошено; прислать журнал шага 3 |
| `the database is read-only for this window` | окно открыто без прав администратора | открыть PowerShell от имени администратора, `cd C:\transport-report`, повторить |
| `import run N is still running` | идёт другая загрузка, например ночная | дождаться; если окно давно закрыто — прислать строку |
| `the run stopped: ... -> 503` или `-> 429` | сервер agro-work занят | всё прочитанное сохранено; повторить шаг через час |
| `equipment N already belongs to agro-work machine ...` в шаге 6 | номер нашей машины занят другой машиной agro-work | прислать строку; ничего не записано |
| в `schtasks /query` нет задачи или `Last Result` не 0 | задача не создана или упала | прислать вывод `schtasks /query /tn "AgroWorkImport" /fo LIST /v` и хвост `agro_work_import.log` |

## Откат — только если после шага 3 программа не открывается

Выполнять **только** если шаг 3 закончился `RESULT: STOP`, программа в
браузере не открывается совсем, а ответа от меня нет. Блок возвращает
прежнюю версию программы (`eb7d003`). Базу он **не** трогает: семь новых
таблиц остаются пустыми, прежняя версия их просто не видит — данные не
теряются. Резервная копия шага 3 остаётся на диске D:.

```powershell
& {
$ErrorActionPreference = 'Continue'
$ProgressPreference = 'SilentlyContinue'
$prod      = 'C:\transport-report'
$services  = @('TransportReport', 'TransportBot', 'TransportBot003')
$site      = 'http://10.103.25.14:5050'
$baseline  = 'eb7d0034333e996258232e6e254806c656a99b47'
$reviewed  = 'de81e43289abd3d600cc30ef80b596ec0bf26240'
$work      = 'C:\VehicleSoft_AgroWork'
$log       = 'C:\VehicleSoft_AgroWork\release_rollback.log'
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

**Что должно получиться:** `ROLLED BACK: ... -> eb7d0034...`,
`SERVICES: Running`, `SMOKE /login: 200`, `RESULT: ROLLBACK PASSED`.

**Почему `git reset --keep`, а не `--hard`.** `--keep` возвращает файлы
программы к прежней версии и **отказывается** работать, если в них есть
правки, сделанные руками; неотслеживаемые файлы — журналы, выгрузки, база —
он не трогает никогда. Запрет устава на `git reset --hard` защищает общую
историю `main`; здесь двигается только рабочая копия сервера, назад ровно на
тот коммит, с которого шаг 3 её сдвинул.

После отката повторный выпуск — только по новой версии этого ранбука: в нём
будет учтено, что миграция уже применена.

**Прислать:** `C:\VehicleSoft_AgroWork\release_rollback.log` и журнал шага 3.

Данные трека — семь таблиц agro-work — удаляются только вручную и только
после отката кода, по докстрингу `migrate_agro_work_001.py`. Ночная загрузка
отключается командой `schtasks /delete /tn "AgroWorkImport" /f`, файл
`C:\transport-report\agro_work_import.bat` после этого удаляется вручную.
Автоматически ничего не удаляется.
