# Выпуск v1.29 на рабочий сервер — пошагово

Для владельца. Всё делается на сервере **SRV-YOQSH**. Каждый шаг — «скопировать,
вставить, нажать Enter, посмотреть последнюю строку». Треки —
`docs/tracks/gps-plan-fakt.md` и `docs/tracks/agro-work.md`, их ведёт одна
сессия. Выпуск — **подготовка метода «время»**: сборщик сохраняет зажигание
и счётчик моточасов, таблица часов считается каждую ночь, но **вердикты
«время» в «Сверке» не включаются** — проверка правил по отчёту Wialon 20.10
не прошла (шаг 1). Вторая часть — **одна «Сверка»** в меню: вкладки, общие
фильтры и выгрузка в Excel. Разбор — трек GPS, §0.6, и трек agro-work,
§9.6–9.8.

Порядок и блоки — те же, что у выпуска v1.28 (19.10), прошедшего на этом
сервере. Блок шага 3 — блок выпуска v1.20 (30.09), который на этом сервере
прошёл **с миграцией**: меняются только версии, имя миграции и журналы.
**Одна миграция** — GPS_ENGINE_001: новая пустая таблица часов. Существующие
таблицы и строки не меняются.

## Что произойдёт — коротко

1. Программа на сервере обновится с версии `784d18b` (выпуск v1.28) до
   новой. В ней всё, что влито с 19.10:
   - **Сборщик точек** (ночная задача в 01:00) сохраняет у каждой точки
     значение датчика зажигания и каждую ночь записывает показание счётчика
     моточасов Wialon — для проверки нашего счёта против счёта Wialon. В
     Wialon ничего не пишется. Файлы точек получают один новый столбец,
     файл при этом не переписывается. Если Wialon отклоняет вход из-за
     чужих неудачных входов с нашего адреса, сборщик пробует до шести раз
     с паузой 65 секунд, а не два (GPS-31): 20.10 ночной сбор пропал, когда
     отказали обе попытки.
   - **Таблица часов** `gps_daily_engine` (миграция GPS_ENGINE_001, шаг 3):
     по каждой машине и суткам — часы с включённым зажиганием, «неизвестно»
     (дыры в данных) и пробег. Считает её `gps.engine_hours`: в день выпуска
     — за период с 01.08.2026 (шаг 4), дальше каждую ночь третьей строкой
     ночного расчёта.
   - **Сверка «время» в этом выпуске не включается.** Проверка правил часов
     и пробега по отчёту Wialon «Моточасы» за сентябрь (GPS-28, 20.10) не
     прошла: у семи машин датчик зажигания по нашему счёту «включён»
     круглые сутки, когда Wialon часов не видит, а пробег отчёта оказался
     пробегом внутри моточасов. Заявки «время» остаются, как до выпуска, без
     вердикта с причиной «Метод «время»: трек GPS время работы пока не
     публикует»; в сентябре таких 853. Таблица часов считается, но в
     «Сверке» не показывается — её включит новая проверка, отдельным
     выпуском.
   - **Одна «Сверка».** В меню вместо прежних пунктов — один, «Сверка»: внутри
     вкладки «Свод», «Заявка → работа», «Работа → заявка»; фильтры
     организации, вида техники, статуса и типа оплаты; кнопка Excel выгружает
     то, что выбрано. Вердикты от этого не меняются.
   - **Ночной расчёт площади:** сутки, ждавшие сборщика, больше не
     пересчитываются, если их точек на диске уже нет (GPS-30): участки и
     ответы операторов на них сохраняются. Гектары не меняются.
   - Ручные инструменты GPS, только чтение: обзор датчиков зажигания
     (GPS-23; теперь умеет выгрузить настройки датчиков целиком), проверка
     часов против счётчика Wialon (GPS-25), зонд и проверка по отчёту
     Wialon «Моточасы» (GPS-27, GPS-28).
2. Миграция одна — GPS_ENGINE_001, новая пустая таблица `gps_daily_engine`.
   Шаг 3 применяет её при остановленной программе, после резервной копии, и
   запускает второй раз: второй запуск обязан ответить «уже сделано».
3. Во время обновления программа и оба Telegram-бота **не работают 3–5
   минут**. Предупредите людей заранее.
4. Резервная копия базы — в шаге 3, перед обновлением; блок её проверяет.
5. После обновления — шаг 4, программа работает: третья строка ночного
   расчёта, дозаполнение зажигания у точек с 01.08.2026 (несколько часов,
   из Wialon только чтение), расчёт часов за тот же период и проверка
   «Сверки» глазами. Утром следующего дня — строки в журналах сбора и
   расчёта (шаг 5).

Шаги 1–3 — около получаса, простой программы — 3–5 минут. Шаг 4 — несколько
часов, почти всё это время окно просто работает само.

**Когда:** день назовёте вы — по вашему правилу выпуски идут раз в неделю
пачкой. Шаги 1–4 — **в один день, с утра**: дозаполнение в шаге 4 идёт
несколько часов. **Не с 00:30 до 05:00** — ночью на сервере работают свои
задачи: сбор GPS, резервная копия, суточный расчёт и ночная загрузка
agro-work (в 04:00).

**Обновление кода в `C:\transport-report`** — не командой `git pull`, а
перемоткой (`git merge --ff-only`) ровно на проверенную версию и только после
резервной копии, как в выпусках v1.20–v1.28.

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

В шаге 4 блоки маленькие; у каждого написано, какие строки должны
получиться. Строка не та — дальше не идти, прислать журнал.

Журналы пишутся в папку `C:\VehicleSoft_Release`. Чтобы прислать файл,
откройте эту папку в Проводнике и перетащите файл в чат.

---

## Шаг 1. Дождаться моего сообщения «v1.29 — можно» — ничего не делать

Код выпуска и этот ранбук уже в `main` — вливать на GitHub ничего не нужно.
Перед выпуском я проверяю одно и пишу вам одно сообщение: **после
проверенной версии в `main` не влито кода.** Если влит — я пересоберу
ранбук и пришлю новый.

Проверка GPS-28 выпуск больше не держит. 20.10 она не прошла — последние
строки её журнала `VERDICT HOURS: NOT MET` и `VERDICT MILEAGE: NOT MET`, —
поэтому вердикты «время» в этом выпуске выключены: выпуск их не публикует.

День выпуска выбираете вы.

**Важно: до конца шага 4 не вливайте PR с кодом.** Выпуск проверен ровно
для кода, который сейчас в `main`. Документы вливать можно. Если в `main`
появится другой код, шаги 2 и 3 остановятся сами и ничего не тронут —
тогда напишите мне.

**Прислать:** ничего — после моего сообщения можно к шагу 2.

## Шаг 2. Проверка перед выпуском — ничего не меняет

Проверка, что сервер и новая версия ровно такие, как задумано: тот сервер;
та версия программы сейчас; код новой версии — ровно проверенный (после него
в `main` менялись только документы); гейт выпуска пуст; миграция в выпуске
ровно одна — `migrate_gps_engine_001.py`; резервной копии хватит места.
Программа продолжает работать, ничего не останавливается и не меняется.

Откройте PowerShell от имени администратора, вставьте блок и нажмите Enter:

```powershell
& {
$ErrorActionPreference = 'Continue'
$prod      = 'C:\transport-report'
$db        = 'C:\transport-report\instance\transport.db'
$py        = 'C:\Program Files\Python314\python.exe'
$backupBat = 'C:\transport-report\backup_production_db.bat'
$services  = @('TransportReport', 'TransportBot', 'TransportBot003')
$baseline  = '784d18b722dbcc761081e67dcc03ca824355244f'
$reviewed  = '276a16c5164b21fe3c57ee1d1fc1a32d66956375'
$migration = 'migrate_gps_engine_001.py'
$work      = 'C:\VehicleSoft_Release'
$log       = 'C:\VehicleSoft_Release\release_v129_step2.log'
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
`SERVICE ... : Running`, строка `RELEASE: 784d18b... -> ...` и список
влитых изменений: `#204`–`#218` — код и документы треков GPS и agro-work
с 19.10 (в том числе записи выпуска v1.28, прежние редакции этого ранбука
и исход проверки GPS-28) и `#219` — этот ранбук. Если в
списке есть ещё PR — значит, они только с документами: чужой код после
проверенной версии блок не пропускает.

Под `MIGRATIONS BEFORE THE RELEASE:` будет `DRIFT DETECTED` и две миграции
Дронов в `file-but-not-registered` — это известное состояние сервера с
выпуска v1.20, а не ошибка; шаг 3 сверяет, что после выпуска к ним ничего не
прибавилось.

**Если `RESULT: STOP - ...`:** дальше не идти. Программа не тронута и
работает как раньше. Пришлите `C:\VehicleSoft_Release\release_v129_step2.log`.

**Прислать:** `C:\VehicleSoft_Release\release_v129_step2.log`. Если в нём
`CHECK PASSED`, мой ответ ждать не нужно — можно сразу к шагу 3.

## Шаг 3. Выпуск — программа не работает 3–5 минут

Что делает блок, по порядку:

1. ещё раз проверяет всё, что проверял шаг 2 (ничего не меняя);
2. останавливает программу и обоих ботов;
3. проверяет, что базу никто не держит открытой;
4. делает резервную копию базы в `D:\transport-report-backups\production\daily\`
   и проверяет её: копия целая и размером ровно как база;
5. забирает новую версию программы с GitHub;
6. добавляет в базу одну новую пустую таблицу — таблицу часов
   `gps_daily_engine`; это «миграция». Запускает её дважды: второй раз она
   обязана ответить «уже сделано». Существующие данные не трогаются;
7. запускает программу и обоих ботов;
8. проверяет, что открывается страница входа и что раздел сверки
   (`/agro-work/`) на месте — та же проверка, что во всех выпусках с
   v1.22. Новое — в шагах 4 и 5.

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
$baseline  = '784d18b722dbcc761081e67dcc03ca824355244f'
$reviewed  = '276a16c5164b21fe3c57ee1d1fc1a32d66956375'
$migration = 'migrate_gps_engine_001.py'
$pendingId = 'GPS_ENGINE_001 (migrate_gps_engine_001.py)'
$doneLine  = 'Done. Table gps_daily_engine with 11 columns and 1 index is in place.'
$againLine = 'Already applied. Nothing to do.'
$work      = 'C:\VehicleSoft_Release'
$log       = 'C:\VehicleSoft_Release\release_v129_step3.log'
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

  Write-Host '== 6. Migration GPS_ENGINE_001 - one new empty table'
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

- `RELEASE: 784d18b... -> ...`;
- `SERVICES: Stopped`;
- `CLEAN -- no other process has the database open` или
  `STALE: no process holds the database` — оба ответа годятся: второй — известная
  особенность этой базы, а не ошибка (`docs/RELEASE_GATE.md`, «Приборы с
  известным шумом»);
- `BACKUP: D:\transport-report-backups\production\daily\transport_....db (...
  bytes, integrity ok, same size as the database)`;
- `UPDATED: 784d18b... -> ...`;
- строка миграции
  `Done. Table gps_daily_engine with 11 columns and 1 index is in place.`,
  затем `Already applied. Nothing to do.`;
- `MIGRATIONS REGISTERED: 60 -> 61`;
- `SERVICES: Running`;
- `SMOKE /login: 200 ...` и `SMOKE /agro-work/: asks to log in ...`;
- `NEW TRACEBACKS IN logs\error.log: 0`.

Строк от инструментов много — смотреть нужно только на `RESULT`.

**Если `RESULT: STOP - ...`:** дальше не идти. Программа в любом случае
снова запущена — блок запускает её сам. Строка `PROGRAM VERSION NOW:`
говорит, какая версия работает: `784d18b` — прежняя, ничего не поменялось;
другое значение — работает новая версия. Пришлите
`C:\VehicleSoft_Release\release_v129_step3.log`. Если программа в браузере
не открывается совсем, — раздел «Откат» в конце.

**Если `RESULT: RELEASE PASSED WITH A WARNING`:** выпуск прошёл, но в журнале
ошибок программы появились новые записи. Пришлите журнал шага 3 и дождитесь
ответа до шага 4.

**Прислать:** `C:\VehicleSoft_Release\release_v129_step3.log`. Если в нём
`RELEASE PASSED`, мой ответ ждать не нужно — можно сразу к шагу 4.

## Шаг 4. Метод «время» на рабочей базе — несколько часов, программа работает

Три действия и проверка глазами. Всё — из `C:\transport-report`: после шага 3
там код выпуска. Программа работает, люди могут пользоваться ею как обычно.

**4.1. Третья строка ночного расчёта.** Ночная задача в 02:30 запускает
`C:\transport-report\gps_daily.bat`: в нём две строки расчёта. Блок
добавляет третью — расчёт часов за последние 30 суток. Прежний файл он
сохраняет копией, а если файл не такой, каким его оставил ранбук
развёртывания GPS (§7), — ничего не меняет.

```powershell
cd C:\transport-report
& {
$bat  = 'C:\transport-report\gps_daily.bat'
$copy = 'C:\VehicleSoft_Release\gps_daily_before_v129.bat'
$want = @('@echo off', 'cd /d C:\transport-report', 'C:\gps_venv\Scripts\python.exe -m gps.daily >> gps_daily.log 2>&1', 'C:\gps_venv\Scripts\python.exe -m gps.daily --catch-up >> gps_daily.log 2>&1')
$line = 'C:\gps_venv\Scripts\python.exe -m gps.engine_hours >> gps_daily.log 2>&1'
$now  = @(Get-Content -LiteralPath $bat | ForEach-Object { $_.Trim() } | Where-Object { $_ })
if (($now -join '|') -eq (($want + $line) -join '|')) {
  'BAT: the third line is already in place - nothing changed'
} elseif (($now -join '|') -ne ($want -join '|')) {
  'BAT: STOP - gps_daily.bat is not as expected - nothing changed'
} else {
  Copy-Item -LiteralPath $bat -Destination $copy -Force
  Set-Content -LiteralPath $bat -Value ($want + $line) -Encoding ASCII
  "BAT: the third line is added, the old file is saved as $copy"
}
'--- gps_daily.bat now:'
Get-Content -LiteralPath $bat
} *> C:\VehicleSoft_Release\release_v129_step4_bat.log
Get-Content C:\VehicleSoft_Release\release_v129_step4_bat.log
```

Ожидаемо: `BAT: the third line is added, the old file is saved as
C:\VehicleSoft_Release\gps_daily_before_v129.bat`, затем содержимое файла —
пять строк, последняя:
`C:\gps_venv\Scripts\python.exe -m gps.engine_hours >> gps_daily.log 2>&1`.
Если блок запускается второй раз — `BAT: the third line is already in place
- nothing changed`, это тоже годится. Если `BAT: STOP` — к 4.2 не идти,
прислать `release_v129_step4_bat.log`: в нём видно, какой файл на сервере.

**4.2. Дозаполнение зажигания — несколько часов, из Wialon только чтение.**
Точки, собранные до выпуска, лежат без значения зажигания: прежний сборщик
его не сохранял. Инструмент заново читает из Wialon сообщения с 01.08.2026
(Wialon хранит три месяца) и вписывает зажигание в точки, которые уже
лежат. Новых точек он не добавляет, отметки сборщика не двигает, координаты
не трогает, в Wialon и в базу программы ничего не пишет. Повторный запуск
спрашивает только то, что ещё не заполнено.

```powershell
cd C:\transport-report
& "C:\Program Files\Python314\python.exe" -m gps_collector.refill --from 2026-08-01 --to (Get-Date -Format 'yyyy-MM-dd') *>> C:\VehicleSoft_Release\release_v129_step4_refill.log
"exit $LASTEXITCODE"
Get-Content C:\VehicleSoft_Release\release_v129_step4_refill.log -Tail 14
```

Идёт несколько часов: несколько сотен объектов, у каждого — сообщения
почти за три месяца. Ход виден в журнале (открыть Блокнотом): строка на
объект вида `[номер/всего] время ...`. Окно не закрывать, компьютер не
выключать. Ожидаемо: `exit 0`, а в конце журнала:

- `objects: ... | without an ignition sensor: ... | nothing to fill: ... | asked: ... | failed: 0`;
- `points without ignition before: ... | messages read: ..., with an ignition value: ... | points filled: ... | pieces truncated by the limit: ...`;
- под `after:` — по строке на месяц вида `2026-09: points ..., with ignition ...`:
  число «with ignition» больше, чем у того же месяца под `before:`;
- последняя: `REFILL ONLY: no point inserted, no watermark moved, nothing written to Wialon`.

Другие коды: `exit 1` — часть объектов не прочиталась (связь, Wialon просил
подождать): запустите тот же блок ещё раз, он спросит только недостающее;
`exit 3` — Wialon не принял вход: прислать журнал. **Если к 00:30 блок ещё
идёт**, нажмите Ctrl+C и запустите его снова на следующий день: сделанное не
теряется. Ночному сбору в 01:00 дозаполнение не вредит, но оба замедлят друг
друга.

**4.3. Расчёт часов за период — 10–30 минут.** Только после того, как 4.2
закончился `exit 0` (если и повтор дал `exit 1` — после моего ответа).
Пишет в таблицу часов, другие таблицы не трогает. В «Сверке» эти часы в
этом выпуске не показываются.

```powershell
cd C:\transport-report
& C:\gps_venv\Scripts\python.exe -m gps.engine_hours --from 2026-08-01 *> C:\VehicleSoft_Release\release_v129_step4_hours.log
"exit $LASTEXITCODE"
Get-Content C:\VehicleSoft_Release\release_v129_step4_hours.log -Tail 3
```

Ожидаемо: `exit 0`; первая строка журнала —
`engine hours 2026-08-01 -> ... (... days), rule ignition-R300-P150-2026-10-19`
(по вчерашние сутки), последняя —
`object-days with points: ... | recomputed: ... | unchanged: ... | recomputed with ignition data: ... | failed: 0`.
Между ними — по строке на сутки вида `2026-09-15: objects ..., recomputed ...`.

Другие коды: `exit 5` — часть объектов-суток не посчиталась, они
перечислены строками `FAILED`: прислать журнал, остальное записано; `exit 2`
— `ERROR: no table gps_daily_engine -- migration GPS_ENGINE_001 is not
applied`: таблицы нет, значит шаг 3 не дошёл до миграции — прислать журналы
шагов 3 и 4.

**4.4. Глазами — «Сверка» за сентябрь.** Откройте
`http://10.103.25.14:5050/agro-work/?from=2026-09-01&to=2026-09-30`:

- в меню один пункт «Сверка», внутри — вкладки «Свод», «Заявка → работа»,
  «Работа → заявка»; фильтры организации, вида техники, статуса и типа
  оплаты;
- у заявок «время» — по-прежнему причина «Метод «время»: трек GPS время
  работы пока не публикует» и никаких часов по GPS: в этом выпуске
  вердикты «время» выключены.

Затем нажмите кнопку **Excel** на «Своде» — скачается книга за сентябрь.

**Прислать:** `C:\VehicleSoft_Release\release_v129_step4_bat.log`,
`C:\VehicleSoft_Release\release_v129_step4_refill.log`,
`C:\VehicleSoft_Release\release_v129_step4_hours.log`, строки `exit` из 4.2 и
4.3 и скачанную книгу Excel. Цифры в книге я сверю сам. Метку выпуска
(тег) поставите вы — команду я пришлю после вашего ответа.

## Шаг 5. Утро следующего дня — первая ночь с новым сборщиком, 5 минут

Ночью сборщик в 01:00 впервые сохранит зажигание у новых точек и запишет
счётчики моточасов, а расчёт в 02:30 — посчитает часы третьей строкой.

Журналы читаются байтами, а не `Select-String`: начало `gps_collect.log`
записано в другой кодировке, и `Select-String` с `Get-Content` читают его
неверно. 20.10 они показали иероглифы и не нашли строк, которые в журнале
были.

```powershell
& { $t = [Text.Encoding]::Default.GetString([IO.File]::ReadAllBytes('C:\transport-report\gps_collect.log')) -replace "`0", ''; $t -split "`r?`n" | Where-Object { $_ -match '^login OK|^ignition:|ERROR' } | Select-Object -Last 2 }
& { $t = [Text.Encoding]::Default.GetString([IO.File]::ReadAllBytes('C:\transport-report\gps_daily.log')) -replace "`0", ''; $t -split "`r?`n" | Where-Object { $_ -match '^engine hours|^object-days with points' } | Select-Object -Last 2 }
```

Ожидаемо четыре строки:

- `login OK | tochki: C:\transport-report\instance`;
- `ignition: ... of ... points with a position carry it | asked objects without an ignition sensor: ... | filled into points stored before: ... | engine-hour counters noted: ...`
  — первое число — большая часть второго, счётчиков — несколько сотен;
- `engine hours ... -> ... (30 days), rule ignition-R300-P150-2026-10-19`;
- `object-days with points: ... | failed: 0`.

Если строки не те — последние 60 строк обоих журналов:

```powershell
& { $t = [Text.Encoding]::Default.GetString([IO.File]::ReadAllBytes('C:\transport-report\gps_collect.log')) -replace "`0", ''; $t -split "`r?`n" | Select-Object -Last 60 }
& { $t = [Text.Encoding]::Default.GetString([IO.File]::ReadAllBytes('C:\transport-report\gps_daily.log')) -replace "`0", ''; $t -split "`r?`n" | Select-Object -Last 60 }
```

**Прислать:** четыре строки; если они не те — и вывод второго блока.

## Если что-то пошло не так

| Что видно | Что это значит | Что делать |
|---|---|---|
| `RESULT: STOP - this window is not run as administrator` | окно PowerShell открыто без прав администратора | закрыть окно, открыть «от имени администратора», повторить шаг |
| `RESULT: STOP - the release gate is closed` | в `docs/RELEASE_GATE.md` есть открытые пункты | ждать, пока их трек снимет строку, затем повторить шаг 2; ничего не менялось |
| `RESULT: STOP - the checked version is not in main yet` | в `main` нет проверенной версии — так не должно быть: ранбук влит вместе с ней | прислать журнал; ничего не менялось |
| `RESULT: STOP - after the checked version main got changes outside docs/` | после проверки в `main` влит чужой код | прислать журнал; ничего не менялось, выпуск я пересоберу |
| `RESULT: STOP - this version is already on the server` | шаг 3 уже выполнен раньше | идти к шагу 4 |
| `RESULT: STOP - the program here is at ...` | на сервере не та версия, для которой готовился выпуск | прислать журнал; ничего не менялось |
| `RESULT: STOP - migrations in the release: ...; expected only migrate_gps_engine_001.py` | в выпуск попала другая миграция | прислать журнал; ничего не менялось |
| `RESULT: STOP - the backup did not pass` | копия базы не записалась или не совпала | прислать журнал; программа снова запущена на прежней версии |
| `RESULT: STOP - git merge --ff-only failed` | Git не смог забрать новую версию — например, мешает файл с тем же именем | прислать журнал; программа снова запущена на прежней версии |
| `RESULT: STOP - after the update the pending migrations are ...` | после обновления список ждущих миграций не тот, что ожидался | прислать журнал; программа запущена на новой версии, база не менялась |
| `RESULT: STOP - the migration did not finish` | миграция откатилась целиком | прислать журнал; программа работает на новой версии, таблицы часов нет, остальное работает |
| `RESULT: STOP - the second run of the migration did not answer that it is already applied` | миграция не записалась в реестр | прислать журнал; программа работает на новой версии |
| `RESULT: STOP - after the migration the pending migrations are ...` или `registered migrations went from ...` | реестр миграций после миграции не такой, как ожидался | прислать журнал; программа работает на новой версии |
| `RESULT: STOP - /agro-work/ answered ... instead of asking to log in` | службы запущены, но раздел сверки не открывается | прислать журнал; строка `PROGRAM VERSION NOW:` скажет, какая версия работает |
| в 4.1 `BAT: STOP - gps_daily.bat is not as expected` | на сервере файл ночного расчёта другой, чем записан в ранбуке развёртывания | ничего не менялось; прислать `release_v129_step4_bat.log`, к 4.2 не идти |
| в 4.2 `exit 1` | часть объектов не прочиталась | запустить блок 4.2 ещё раз — он спросит только недостающее |
| в 4.2 `exit 3` или `ERROR: ne udalos voyti` | Wialon не принял вход | прислать журнал; ничего не записано |
| в 4.3 `exit 2`, `ERROR: no table gps_daily_engine` | миграция не применена | прислать журналы шагов 3 и 4 |
| в 4.3 `exit 5` | часть объектов-суток не посчиталась (строки `FAILED`) | прислать журнал; остальное записано |
| в 4.4 у заявок «время» — вердикт или часы по GPS | на сервере не та версия: в этом выпуске вердикты «время» выключены | прислать снимок экрана и журнал шага 3 |
| в шаге 5 последняя строка сборщика `ERROR: ne udalos voyti` | Wialon не принял вход ни с одной из шести попыток: с нашего адреса кто-то входит с неверными данными | прислать строку и последние 60 строк (второй блок шага 5); ничего не запускать |
| в шаге 5 нет строки `ignition:` или `engine-hour counters noted: 0` | ночной сбор не прошёл или прошёл прежней версией | прислать последние 60 строк `C:\transport-report\gps_collect.log` (второй блок шага 5) |
| в шаге 5 нет строки `engine hours` | третья строка не добавлена или расчёт не прошёл | прислать `release_v129_step4_bat.log` и последние 60 строк `C:\transport-report\gps_daily.log` (второй блок шага 5) |

## Откат — только если после шага 3 программа не открывается

Выполнять **только** если шаг 3 закончился `RESULT: STOP`, программа в
браузере не открывается совсем, а ответа от меня нет. Блок возвращает
прежнюю версию программы (`784d18b`, выпуск v1.28). Базу он **не** трогает:
таблица часов, если миграция успела пройти, остаётся — прежняя версия её не
читает, и это безвредно. Столбец зажигания в файлах точек тоже остаётся:
прежний сборщик пишет точки по именам столбцов и его не замечает.
Резервные копии остаются на диске D:.

```powershell
& {
$ErrorActionPreference = 'Continue'
$ProgressPreference = 'SilentlyContinue'
$prod      = 'C:\transport-report'
$services  = @('TransportReport', 'TransportBot', 'TransportBot003')
$site      = 'http://10.103.25.14:5050'
$baseline  = '784d18b722dbcc761081e67dcc03ca824355244f'
$reviewed  = '276a16c5164b21fe3c57ee1d1fc1a32d66956375'
$work      = 'C:\VehicleSoft_Release'
$log       = 'C:\VehicleSoft_Release\release_v129_rollback.log'
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

**Что должно получиться:** `ROLLED BACK: ... -> 784d18b...`,
`SERVICES: Running`, `SMOKE /login: 200`, `RESULT: ROLLBACK PASSED`.

**Почему `git reset --keep`, а не `--hard`.** `--keep` возвращает файлы
программы к прежней версии и **отказывается** работать, если в них есть
правки, сделанные руками; неотслеживаемые файлы — журналы, выгрузки, база —
он не трогает никогда. Запрет устава на `git reset --hard` защищает общую
историю `main`; здесь двигается только рабочая копия сервера, назад ровно на
тот коммит, с которого шаг 3 её сдвинул.

**Если шаг 4.1 уже был сделан**, верните прежний файл ночного расчёта —
прежняя версия программы третьей строки не знает:

```powershell
Copy-Item -LiteralPath C:\VehicleSoft_Release\gps_daily_before_v129.bat -Destination C:\transport-report\gps_daily.bat -Force
```

**После отката** напишите мне: решаем вместе, что дальше.

**Прислать:** `C:\VehicleSoft_Release\release_v129_rollback.log` и журнал
шага 3.
