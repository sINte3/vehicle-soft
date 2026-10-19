# Выпуск v1.28 на рабочий сервер — пошагово

Для владельца. Всё делается на сервере **SRV-YOQSH**. Каждый шаг — «скопировать,
вставить, нажать Enter, посмотреть последнюю строку». Треки —
`docs/tracks/gps-plan-fakt.md` и `docs/tracks/agro-work.md`, их ведёт одна
сессия. Выпуск — **правило территорий**: с 19.10.2026 работа на территории
подразделения (двор завода, ПТМ, пункт приёма, АЗС) не считается гектарами
поля — ваше решение 15.10. Вторая часть — **поля прошлых участков** (GPS-22):
экран «Факт по технике» снова называет у участков до 15.10 то поле, в котором
они лежат. Разбор — трек GPS, §0.5.

Порядок и блоки — те же, что у выпусков v1.20 (01.10), v1.21 (02.10), v1.22,
v1.23, v1.24 (08.10), v1.25, v1.26 и v1.27 (09.10), прошедших на этом сервере:
блоки — из `docs/AGRO_WORK_RELEASE_V127_RUNBOOK.md`, меняются только версии и
имена журналов. **Без миграции**: схема базы не меняется. Данные меняет только
шаг 4 — один столбец у участков, после резервной копии.

## Что произойдёт — коротко

1. Программа на сервере обновится с версии `7235c66` (выпуск v1.27) до новой.
   В ней всё, что влито с 09.10:
   - **Правило территорий** (в коде — дата включения 19.10.2026). С суток 19.10.2026
     ночной расчёт режет участки по 85 зонам вашего списка от 15.10: кусок
     участка, лежащий на территории наполовину и больше, не гектары целиком;
     из остальных кусков территория вычитается, остатки меньше 0,3 га
     отпадают. Снятого участка на экране нет — ответить на него «работа»
     нельзя; оспорить снятие можно только списком зон. У суток, посчитанных
     с правилом, внизу экрана версия метода
     `overflow-cap-2026-10-07+territory-2026-10-15_v3`; у суток с причиной
     (нет движения, редкая запись, спецтехника) метки нет — так задумано.
     Первые сутки с правилом — 19.10.2026, расчёт считает их **в 02:30 следующего
     дня**. Прошлые сутки не пересчитываются. Гектары заметно упадут, сильнее
     всего у разъездных машин и МТЗ, — это ожидаемо. Оценка назад по окну
     замера 15.10 (не прогноз): всего −22,2 %, МТЗ −23,5 %, разъездные −66,0 %.
   - **Сверка agro-work.** Сутки, у которых правило сняло все участки,
     сверка видит как «работы нет»; гектарная заявка, вся работа по которой
     была на территории, получит «работы не было» — ваше решение 18.10
     («Оставить «работы не было»»). Где правило вычло часть участка, гектары
     GPS у заявки меньше, и светофор объёма может сдвинуться в любую сторону.
     Прошлые вердикты не меняются.
   - **Поля прошлых участков (GPS-22).** 15.10 запись зеркала контуров
     положила под прежние номера строк другие зоны Wialon, и у участков,
     посчитанных до 15.10, экран называет чужое поле. Шаг 4 выбирает поле
     заново — тем же способом, что ночной расчёт. Сухой прогон 16.10: из
     13 575 участков поле сменилось бы у 5 923. В день выпуска прогон делается
     заново, числа будут свои. Гектары и ответы операторов не меняются.
   - Остальное: открытая заявка agro-work в сверке красная; подсказка «скрыто
     фильтрами» в поиске машины на экране «Факт по технике»; правила края
     поля в движке **выключены** и ничего не меняют; ручные инструменты GPS;
     правка инструмента импорта ведомостей Дронов `tools/import_drone_works.py`
     (PR `#197`, Дроны, 15.10 — влит «squash», поэтому в списке шага 2 его не
     будет).
2. Миграций нет — схема базы `transport.db` не меняется. Данные меняет только
   шаг 4: GPS-22 пишет участкам номер строки поля (`contour_id`) — один
   столбец, одной транзакцией, после резервной копии. Площадь, многоугольник
   и ответы операторов не трогаются.
3. Во время обновления программа и оба Telegram-бота **не работают 3–5
   минут**. Предупредите людей заранее.
4. Резервная копия базы делается дважды: перед обновлением (шаг 3) и перед
   записью полей (шаг 4.5). Обе проверяются.
5. После обновления — проверка, что список территорий читается кодом выпуска
   (на копии базы), и GPS-22 (шаг 4, 20–40 минут, программа работает). Утром
   следующего дня — две строки в журнале ночного расчёта и версия метода на
   экране (шаг 5).

Шаги 1–3 — около получаса, простой программы — 3–5 минут. Шаг 4 — 20–40
минут, программа работает.

**Когда:** шаги 1–4 — **в один день, 19.10.2026, днём**, в спокойное время, когда
программой почти не пользуются. **Не с 00:30 до 05:00** — ночью на сервере
работают свои задачи: сбор GPS, резервная копия, суточный расчёт и ночная
загрузка agro-work (в 04:00). Дата включения правила записана в код — 19.10.2026.
Если выпуск сдвигается на другой день, **шаг 1 не делайте** — напишите мне: я
поставлю новую дату и пришлю новый запрос выпуска. Запрос выпуска, влитый без
выпуска в тот же день, — ловушка для любого следующего выпуска.

**Обновление кода в `C:\transport-report`** — не командой `git pull`, а
перемоткой (`git merge --ff-only`) ровно на проверенную версию и только после
резервной копии, как в выпусках v1.20–v1.27.

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

В шаге 4 блоки маленькие, по две-три команды; у каждого написано, какие
строки должны получиться. Строка не та — дальше не идти, прислать журнал.

Журналы пишутся в папку `C:\VehicleSoft_Release`. Чтобы прислать файл,
откройте эту папку в Проводнике и перетащите файл в чат.

---

## Шаг 1. Влить запрос выпуска на GitHub

Блоки этого ранбука проверены вместе с кодом выпуска и лежат в запросе PR
#203. «Влить» — сделать его основной версией, чтобы сервер мог её
забрать. Сам сервер на этом шаге не меняется.

**Важно: до конца шага 4 не вливайте другие PR с кодом.** Выпуск проверен
ровно для кода, который сейчас в `main`, плюс этот PR. Документы вливать
можно. Если в `main` появится другой код, шаги 2 и 3 остановятся сами и
ничего не тронут — тогда напишите мне.

**Важно: вливать — в день выпуска, 19.10.2026.** В запросе записана дата
включения правила. Если день другой — не вливайте, напишите мне.

1. Откройте https://github.com/sINte3/vehicle-soft/pull/203
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

Откройте PowerShell от имени администратора, вставьте блок и нажмите Enter:

```powershell
& {
$ErrorActionPreference = 'Continue'
$prod      = 'C:\transport-report'
$db        = 'C:\transport-report\instance\transport.db'
$py        = 'C:\Program Files\Python314\python.exe'
$backupBat = 'C:\transport-report\backup_production_db.bat'
$services  = @('TransportReport', 'TransportBot', 'TransportBot003')
$baseline  = '7235c6657bcb8d04d3cc76795b27cab30db0566f'
$reviewed  = 'fb3473d1a5718ab8b345aefa9f39526aab8c1677'
$work      = 'C:\VehicleSoft_Release'
$log       = 'C:\VehicleSoft_Release\release_v128_step2.log'
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
`SERVICE ... : Running`, строка `RELEASE: 7235c66... -> ...` и список
влитых изменений: `#181`–`#184`, `#186`–`#196`, `#198`–`#202` — код и
документы трека GPS с 09.10 (в том числе записи выпуска v1.27) и `#203` —
этот выпуск. PR `#197` (Дроны) в списке не будет: его влили «squash», без
строки «Merge pull request». Если в списке есть ещё PR — значит, они только с
документами: чужой код после проверенной версии блок не пропускает.

Под `MIGRATIONS BEFORE THE RELEASE:` будет `DRIFT DETECTED` и две миграции
Дронов в `file-but-not-registered` — это известное состояние сервера с
выпуска v1.20, а не ошибка; шаг 3 сверяет, что список до и после выпуска
один и тот же.

**Если `RESULT: STOP - ...`:** дальше не идти. Программа не тронута и
работает как раньше. Пришлите `C:\VehicleSoft_Release\release_v128_step2.log`.

**Прислать:** `C:\VehicleSoft_Release\release_v128_step2.log`. Если в нём
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
   (`/agro-work/`) на месте — та же проверка, что во всех выпусках с
   v1.22. Новую версию она от старой не отличает; новое — в шагах 4 и 5.

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
$baseline  = '7235c6657bcb8d04d3cc76795b27cab30db0566f'
$reviewed  = 'fb3473d1a5718ab8b345aefa9f39526aab8c1677'
$work      = 'C:\VehicleSoft_Release'
$log       = 'C:\VehicleSoft_Release\release_v128_step3.log'
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

- `RELEASE: 7235c66... -> ...`;
- `SERVICES: Stopped`;
- `CLEAN -- no other process has the database open` или
  `STALE: no process holds the database` — оба ответа годятся: второй — известная
  особенность этой базы, а не ошибка (`docs/RELEASE_GATE.md`, «Приборы с
  известным шумом»);
- `BACKUP: D:\transport-report-backups\production\daily\transport_....db (...
  bytes, integrity ok, same size as the database)`;
- `UPDATED: 7235c66... -> ...`;
- `MIGRATIONS REGISTERED: 60 -> 60, unchanged`;
- `SERVICES: Running`;
- `SMOKE /login: 200 ...` и `SMOKE /agro-work/: asks to log in ...`;
- `NEW TRACEBACKS IN logs\error.log: 0`.

Строк от инструментов много — смотреть нужно только на `RESULT`.

**Если `RESULT: STOP - ...`:** дальше не идти. Программа в любом случае
снова запущена — блок запускает её сам. Строка `PROGRAM VERSION NOW:`
говорит, какая версия работает: `7235c66` — прежняя, ничего не поменялось;
другое значение — новая. Пришлите `C:\VehicleSoft_Release\release_v128_step3.log`.
Если программа в браузере не открывается совсем, — раздел «Откат» в конце.

**Если `RESULT: RELEASE PASSED WITH A WARNING`:** выпуск прошёл, но в журнале
ошибок программы появились новые записи. Пришлите журнал шага 3 и дождитесь
ответа до шага 4.

**Прислать:** `C:\VehicleSoft_Release\release_v128_step3.log`. Если в нём
`RELEASE PASSED`, мой ответ ждать не нужно — можно сразу к шагу 4.

## Шаг 4. Территория и поля прошлых участков — 20–40 минут, программа работает

Две проверки и одна запись. Сначала — что список территорий читается кодом
выпуска и в правило вошли ровно 85 зон (на **копии** базы, боевая база не
трогается). Затем GPS-22: свежий сухой прогон, проверка плана, резервная
копия, запись, повторный сухой прогон. Всё — из `C:\transport-report`:
после шага 3 там код выпуска. Инструменты расчёта запускаются окружением
`C:\gps_venv\Scripts\python.exe` — в нём геометрия. Программа работает,
операторы могут отвечать на экране: запись меняет только номер поля у
участка, ответы не трогает. Зеркало контуров (`tools\gps_mirror_contours.py`)
во время выпуска **не запускать**.

**4.1. Копия базы для проверки — ничего не меняет в боевой базе.** Копия
кладётся в `C:\gps-tools\check\v128_check.db` (около 520 МБ, минуту-две);
пустая папка `v128_empty_points` нужна следующему блоку.

```powershell
cd C:\transport-report
New-Item -ItemType Directory -Force -Path C:\gps-tools\check\v128_empty_points | Out-Null
& "C:\Program Files\Python314\python.exe" tools\agro_work_copy_db.py --from C:\transport-report\instance\transport.db --to C:\gps-tools\check\v128_check.db --replace
```

Ожидаемо две строки: `copied ... bytes to C:\gps-tools\check\v128_check.db`
и `integrity of the copy: ok`. Если `ERROR:` — прислать вывод, к 4.2 не идти.

**4.2. Территория загружается кодом выпуска — пишет только в копию.** Расчёт
запускается за 19.10.2026 по одной машине (393 — МТЗ-80.1 80 239 NA; какая именно
машина, не важно) с пустой папкой точек: точек там нет, сутки он не считает,
но список зон читает и печатает, что из него вошло в правило. В копию он
запишет одну строку «нет точек» за 19.10.2026 — боевая база не трогается.

```powershell
cd C:\transport-report
& C:\gps_venv\Scripts\python.exe -m gps.daily --date 2026-10-19 --unit 393 --db C:\gps-tools\check\v128_check.db --dir C:\gps-tools\check\v128_empty_points *> C:\VehicleSoft_Release\release_v128_step4_territory.log
Get-Content C:\VehicleSoft_Release\release_v128_step4_territory.log
```

Ожидаемо, среди строк (в этом порядке):

- `territory: 2 zone(s) left out of the rule: 7603 (larger than 50 ha), 18227 (larger than 50 ha)`
  — две зоны списка больше 50 га, они вне правила по его условию;
- `2026-10-19: 1 object(s), ... contour(s) in the directory`;
- `territory rule territory-2026-10-15_v3 (from 2026-10-19): 85 zone(s)` — ровно 85;
- `393      net_tochek` — точек в пустой папке нет, так и задумано;
- последняя: `published: 0 | not computed: 1 | sites: 0`.

Если в строке `left out of the rule` есть другие зоны или зон не 85 — **к 4.3
не идти**, прислать журнал: у какой-то зоны списка в зеркале пропал или
изменился контур, разбираем до ночи. Если вместо этих строк `ERROR: territory
rule` — прислать журнал **сразу**: с таким списком ночной расчёт в 02:30
встанет для всех машин.

**4.3. GPS-22: свежий сухой прогон — ничего не записывает.** Идёт несколько
минут (в 16.10 — 13 575 участков).

```powershell
cd C:\transport-report
& C:\gps_venv\Scripts\python.exe tools\gps_reattach_contours.py --db C:\transport-report\instance\transport.db --out C:\gps-tools\check\reattach_v128_plan.csv *> C:\VehicleSoft_Release\release_v128_step4_plan.log
Get-Content C:\VehicleSoft_Release\release_v128_step4_plan.log
```

Ожидаемо: строка `sites scanned` с числом участков (больше 13 575: прибавились
сутки после 16.10), затем `same field`, `another field`, `gets a field`,
`loses its field`, `of the changes, sites with an operator answer`, разбивка
по месяцам, и в конце:

- `DRY RUN - nothing was written to the database (opened mode=ro)`;
- `changes that --apply would make: ...` — число, которое запишет 4.6 (16.10
  было 5 923; сейчас около того);
- `plan: C:\gps-tools\check\reattach_v128_plan.csv`.

**4.4. Проверка плана: сутки с 15.10 инструмент менять не должен.** Эти сутки
ночной расчёт считал уже после записи зеркала, поэтому GPS-22 обязан
согласиться с ним по каждому участку. Если нет — выбор поля у инструмента
другой, и записывать нельзя.

```powershell
cd C:\transport-report
& C:\gps_venv\Scripts\python.exe tools\gps_reattach_contours.py --db C:\transport-report\instance\transport.db --since 2026-10-15 --out C:\gps-tools\check\reattach_v128_since.csv *> C:\VehicleSoft_Release\release_v128_step4_since.log
Get-Content C:\VehicleSoft_Release\release_v128_step4_since.log
```

Ожидаемо: `changes that --apply would make: 0`. Если не 0 — **к 4.5 не идти**,
прислать `release_v128_step4_since.log` и
`C:\gps-tools\check\reattach_v128_since.csv`: одиночное расхождение возможно
при двух почти равных перекрытиях, но решаю я по CSV.

**4.5. Резервная копия — программа продолжает работать.**

```powershell
& C:\transport-report\backup_production_db.bat
```

Ожидаемо: `Integrity check : ok`, строка `SUCCESS: Backup written to:` с путём
под ней и последняя `Backup completed successfully.` Размер копии может
немного отличаться от базы — программа работает, это нормально. Если в конце
`Backup FAILED` — не идти к 4.6, пришлите вывод.

**4.6. Запись — пишет в базу.** Одна транзакция, минуты. Перед записью
инструмент сам сверяет, что справочник контуров тот же, что при плане, и
что каждый участок не пересчитан с тех пор; иначе откатывает всё.

```powershell
cd C:\transport-report
& C:\gps_venv\Scripts\python.exe tools\gps_reattach_contours.py --db C:\transport-report\instance\transport.db --out C:\gps-tools\check\reattach_v128_apply.csv --apply *> C:\VehicleSoft_Release\release_v128_step4_apply.log
"exit $LASTEXITCODE"
Get-Content C:\VehicleSoft_Release\release_v128_step4_apply.log
```

Ожидаемо: `exit 0`, а в журнале та же сводка, что в 4.3, и в конце:

- `written: ... site(s) got their field again, one transaction; skipped: 0`
  — число как `changes that --apply would make` в 4.3;
- `report : C:\gps-tools\check\reattach_v128_apply.csv`.

Другие коды `exit`: `1` — ничего не записано (справочник изменился за время
запуска, постусловие не сошлось или сбой — в журнале `FAILED: ...`); `2` —
неверный вход, ничего не записано (например, CSV открыт в Excel); `3` —
записано, но часть участков пропущена: их успели пересчитать; `4` — база
занята, ничего не записано, повторить через минуту; `5` — записано, но CSV
отчёта не записался. При любом коде, кроме 0, — прислать журнал; при `1`,
`2` и `4` блок 4.6 можно повторить после ответа.

**4.7. Повторный сухой прогон — всё записано.**

```powershell
cd C:\transport-report
& C:\gps_venv\Scripts\python.exe tools\gps_reattach_contours.py --db C:\transport-report\instance\transport.db --out C:\gps-tools\check\reattach_v128_after.csv *> C:\VehicleSoft_Release\release_v128_step4_after.log
Get-Content C:\VehicleSoft_Release\release_v128_step4_after.log -Tail 4
```

Ожидаемо: `changes that --apply would make: 0`. Если не 0 — прислать журнал.

**4.8. Глазами.** Откройте
`http://10.103.25.14:5050/gps/fact?date=2026-10-14`, выберите любую машину
с участками: у каждого участка названо поле, в котором он лежит, и контур
этого поля на карте накрывает участок (до записи у многих участков значилось
чужое поле). Гектары те же, что были.

**Прислать:** `C:\VehicleSoft_Release\release_v128_step4_territory.log`,
`C:\VehicleSoft_Release\release_v128_step4_plan.log`,
`C:\VehicleSoft_Release\release_v128_step4_since.log`,
`C:\VehicleSoft_Release\release_v128_step4_apply.log`,
`C:\VehicleSoft_Release\release_v128_step4_after.log`, строку `exit` из 4.6
и «шаг 4 — всё сходится» или что не так. Метку выпуска (тег) поставите вы —
команду я пришлю после вашего ответа.

## Шаг 5. Утро следующего дня — первые сутки с правилом, 5 минут

Сутки 19.10.2026 ночной расчёт считает в 02:30 следующего дня. Утром — две строки
в журнале расчёта и версия метода на экране.

```powershell
Select-String -Path C:\transport-report\gps_daily.log -Pattern 'territory rule', 'left out of the rule' | Select-Object -Last 4
```

Ожидаемо, среди найденных (могут повториться: второй раз — от прохода
`--catch-up` той же ночи):

- `territory: 2 zone(s) left out of the rule: 7603 (larger than 50 ha), 18227 (larger than 50 ha)`;
- `territory rule territory-2026-10-15_v3 (from 2026-10-19): 85 zone(s)`.

Затем откройте `http://10.103.25.14:5050/gps/fact?date=2026-10-19`, выберите машину
с участками: внизу «Версия метода:
overflow-cap-2026-10-07+territory-2026-10-15_v3». У суток с причиной (нет
движения, редкая запись, спецтехника) метки нет — так задумано. Гектары за
эти сутки меньше, чем были бы без правила, сильнее всего у разъездных машин
и МТЗ, — это ожидаемо.

**Прислать:** найденные строки и снимок экрана одной машины за 19.10.2026 с
версией метода.

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
| в 4.1 `ERROR: target is inside a transport-report folder` | путь копии указан внутри папки программы — инструмент отказывает, так и должно быть | прислать вывод; ничего не менялось |
| в 4.1 нет строки `integrity of the copy: ok` (другой `ERROR:` или трассировка) | копия не записалась: источник не найден или мало места на диске C: | прислать вывод; ничего не менялось; к 4.2 не идти |
| в 4.2 в `left out of the rule` другие зоны, или зон не 85 | у зоны списка в зеркале пропал или изменился контур | к 4.3 не идти; прислать `release_v128_step4_territory.log` — разбираем до ночи |
| в 4.2 `ERROR: territory rule` | список зон не читается кодом выпуска — ночной расчёт встанет для всех машин | прислать журнал сразу; до 02:30 решаем: исправление или откат |
| в 4.4 `changes that --apply would make:` не 0 | GPS-22 выбрал поле не так, как ночной расчёт, хотя бы у одного участка после 15.10 | к 4.5 не идти; прислать журнал и `reattach_v128_since.csv` |
| в 4.6 `exit 1`, в журнале `FAILED: field_contours changed since the plan was made` | за время запуска кто-то записал зеркало контуров | ничего не записано; прислать журнал; после ответа повторить 4.6 |
| в 4.6 `exit 1`, в журнале другая строка `FAILED:` | постусловие не сошлось или сбой внутри записи | ничего не записано; прислать журнал |
| в 4.6 `exit 2` | неверный вход — например, CSV открыт в Excel | ничего не записано; закрыть Excel, повторить 4.6 |
| в 4.6 `exit 3` | записано, часть участков пропущена: их пересчитали после плана | норма; прислать журнал, идти к 4.7 |
| в 4.6 `exit 4` | база занята дольше 30 секунд | ничего не записано; повторить 4.6 через минуту |
| в 4.6 `exit 5` | записано, но CSV отчёта не записался | строки записаны; прислать журнал, идти к 4.7 |
| в 4.7 `changes that --apply would make:` не 0 | после записи остались участки с другим полем | прислать журнал и `reattach_v128_after.csv` |
| в 4.8 у участка по-прежнему чужое поле | браузер показал страницу из своей памяти | обновить страницу с Ctrl+F5; если не помогло — прислать снимок |
| в шаге 5 строк `territory` нет | ночной расчёт не прошёл или прошёл на прежней версии | прислать последние 60 строк `C:\transport-report\gps_daily.log` |
| в шаге 5 на экране версия без `+territory-2026-10-15_v3` у суток без причины | сутки посчитаны без правила | прислать снимок и строки журнала |

## Откат — только если после шага 3 программа не открывается

Выполнять **только** если шаг 3 закончился `RESULT: STOP`, программа в
браузере не открывается совсем, а ответа от меня нет. Блок возвращает
прежнюю версию программы (`7235c66`, выпуск v1.27). Базу он **не** трогает:
миграций в выпуске нет. Если шаг 4 уже записал поля участков, они остаются —
прежняя версия показывает их так же, и это верные поля. Резервные копии
остаются на диске D:.

```powershell
& {
$ErrorActionPreference = 'Continue'
$ProgressPreference = 'SilentlyContinue'
$prod      = 'C:\transport-report'
$services  = @('TransportReport', 'TransportBot', 'TransportBot003')
$site      = 'http://10.103.25.14:5050'
$baseline  = '7235c6657bcb8d04d3cc76795b27cab30db0566f'
$reviewed  = 'fb3473d1a5718ab8b345aefa9f39526aab8c1677'
$work      = 'C:\VehicleSoft_Release'
$log       = 'C:\VehicleSoft_Release\release_v128_rollback.log'
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

**Что должно получиться:** `ROLLED BACK: ... -> 7235c66...`,
`SERVICES: Running`, `SMOKE /login: 200`, `RESULT: ROLLBACK PASSED`.

**Почему `git reset --keep`, а не `--hard`.** `--keep` возвращает файлы
программы к прежней версии и **отказывается** работать, если в них есть
правки, сделанные руками; неотслеживаемые файлы — журналы, выгрузки, база —
он не трогает никогда. Запрет устава на `git reset --hard` защищает общую
историю `main`; здесь двигается только рабочая копия сервера, назад ровно на
тот коммит, с которого шаг 3 её сдвинул.

**После отката** напишите мне: если откат случился после ночи, у суток с
19.10.2026 в базе стоит версия с правилом, а прежняя версия правила не знает —
решаем вместе, пересчитывать ли их. Запрос выпуска в `main` при откате я
отменяю сам (`git revert`), чтобы дата включения не осталась там без
выпуска.

**Прислать:** `C:\VehicleSoft_Release\release_v128_rollback.log` и журнал
шага 3.
