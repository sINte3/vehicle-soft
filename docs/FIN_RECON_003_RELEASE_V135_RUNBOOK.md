# Выпуск v1.35 на рабочий сервер — пошагово

Для владельца. Всё делается на сервере **SRV-YOQSH**. Каждый шаг — «скопировать,
вставить, нажать Enter, посмотреть последнюю строку». Трек —
`docs/tracks/drones.md`, задача FIN-RECON-003. Выпуск — **защита июльского
импорта дронов**: импортёр, отдельная выборочная загрузка по точному списку
и read-only экспорт отпечатков строк. Это код запросов
https://github.com/sINte3/vehicle-soft/pull/260,
https://github.com/sINte3/vehicle-soft/pull/261 и
https://github.com/sINte3/vehicle-soft/pull/269.

Исходная версия — `a73d756`, выпуск v1.34 от 04.11 (тег
`v1.34-production-2026-11-04`). Выпуск закреплён на **изолированном коммите
`90516935fbd45a977c8c855d24e9385e621a437a`**: v1.34 + ветка PR #261 +
согласованный патч. Всё, что влито в `main` после v1.34 (в том числе
экраны GPS и agro-work), на сервер не едет. **Без миграции**: база не
меняется. **Импорт июля этим выпуском не выполняется и не разрешается** —
только по подписанному акту, отдельным решением.

Шаги 2 и 3 и откат — блоки выпуска v1.34, прошедшего на этом сервере 04.11:
блоки взяты из `docs/GPS_RELEASE_V134_RUNBOOK.md`, меняются только версии,
имя теста и имена журналов. Новые — шаг 1 (репетиция вместо staging) и
шаг 4 (проверка после выпуска).

## Что произойдёт — коротко

1. **Шаг 1 — репетиция, ничего не меняет.** Staging занят пилотом Дронов
   (DRONE-CARD-COVERAGE-001, с 02.10), поэтому чек-лист staging выполняется
   здесь же, на сервере, в отдельной папке `C:\VehicleSoft_FR3_QA`: свежая
   копия кода ровно на коммите выпуска, тесты, read-only preflight на
   временной копии **сегодняшней** базы production. Программа работает,
   ничего не останавливается.
2. После `REHEARSAL PASSED` я снимаю три строки FIN-RECON-003 из гейта
   выпуска (отдельный запрос на GitHub, только документ). Пока они стоят,
   шаги 2 и 3 сами остановятся.
3. **Шаг 3** обновляет программу с `a73d756` до `90516935`. Меняются ровно три
   файла: `tools/import_drone_works.py`, `tools/drone_works_selective.py`,
   `tools/drone_works_fingerprint_export.py`. Экраны, расчёты, книги Excel
   и Telegram-боты не меняются.
4. Во время обновления программа и оба Telegram-бота **не работают 3–5
   минут**. Предупредите людей заранее.
5. Перед обновлением делается резервная копия базы и сразу проверяется.
6. **Шаг 4** — тот же read-only preflight, теперь по коду production.

Всего около 40 минут, простой программы — 3–5 минут.

**Когда:** день назовёте вы. **Не с 00:30 до 05:00** — ночью на сервере
работают свои задачи. Шаг 1 можно выполнить в любой день заранее.

**Обновление кода в `C:\transport-report`** — не командой `git pull`, а
перемоткой (`git merge --ff-only`) ровно на коммит выпуска `90516935` и только
после резервной копии, как в выпусках v1.20–v1.34.

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

**Главное правило.** Каждый блок в конце печатает строку `RESULT: ...`:

- `RESULT: REHEARSAL PASSED`, `RESULT: CHECK PASSED`, `RESULT: RELEASE PASSED`,
  `RESULT: RELEASE CONFIRMED` — шаг прошёл;
- `... WITH A NOTE - ...` — шаг прошёл, но есть замечание: пришлите журнал;
- `RESULT: STOP - ...` — **дальше не идти** и ничего не исправлять самому.
  Пришлите файл журнала (путь — в строке `LOG FILE:`) и дождитесь ответа.

Журналы пишутся в папку `C:\VehicleSoft_Release`. Чтобы прислать файл,
откройте эту папку в Проводнике и перетащите файл в чат. В журналах нет
имён заказчиков, телефонов и сумм — только числа, хеши и имена проверок.

---

## Шаг 1. Репетиция на копии — ничего не меняет

Блок делает то, что чек-лист FIN-RECON-003 делал бы на staging, но не
трогает ни программу, ни базу:

1. проверяет, что на сервере стоит `a73d756` и файлы программы не правлены;
2. скачивает с GitHub свежую копию кода в новую папку
   `C:\VehicleSoft_FR3_QA\run_ДАТА_ВРЕМЯ\repo` и ставит её ровно на коммит
   выпуска `90516935`;
3. сверяет состав выпуска: код программы — ровно три файла `tools/`,
   миграций нет, ни один новый файл выпуска не лежит уже в папке программы;
4. гоняет девять наборов тестов на этой копии. Тесты пишут только в свои
   временные папки; если на рабочем столе есть папка
   `FIN_RECON_003_JULY_BOOKS` с пятью июльскими книгами, включаются и тесты
   на них, а книги сверяются по SHA-256 до и после;
5. берёт из `main` read-only preflight (тот, что проверен на стенде, — блок
   сверяет его SHA-256) и запускает его на копии кода против **временной
   копии** базы production: копия снимается штатным online backup SQLite,
   все проверки идут по ней, после чего она удаляется;
6. запускает тот же preflight с несуществующей службой — он обязан **не**
   пройти (проверка «ложного PASS»).

Откройте PowerShell от имени администратора, вставьте блок и нажмите Enter.
Блок работает около 10 минут:

```powershell
& {
$ErrorActionPreference = 'Continue'
$ProgressPreference = 'SilentlyContinue'
$prod      = 'C:\transport-report'
$db        = 'C:\transport-report\instance\transport.db'
$py        = 'C:\Program Files\Python314\python.exe'
$baseline  = 'a73d756dcb67c06388e58bc7c4a0a1ac64e59e34'
$release   = '90516935fbd45a977c8c855d24e9385e621a437a'
$preflight = 'ops/fin_recon_003/FIN_RECON_003_JULY_PREFLIGHT_READONLY.ps1'
$preflightSha = '7d1823ef9edc97fa7b955a6cc1e5e6944d06328758cb3ab04c884ce08aa3e46d'
$runtime   = @('tools/drone_works_fingerprint_export.py', 'tools/drone_works_selective.py', 'tools/import_drone_works.py')
$suites    = @('tools.test_import_drone_works_fin_recon_h5', 'tools.test_import_drone_works', 'tools.test_import_drone_works_counterless', 'tools.test_drone_works_selective', 'tools.test_drone_works_fingerprint_export', 'tests.test_drone_works_upload_002', 'tests.test_drone_works_screens', 'tests.test_drone_payer_unidentified', 'tests.test_drone_works_migration')
$directory = @('customers', 'aliases_all', 'customers_904_1009', 'works_total', 'hash_customers_le_903', 'hash_customers_904_996', 'hash_customers_997_1009', 'hash_customers_gt_1009', 'hash_aliases_to_le_903', 'hash_aliases_to_904_996', 'hash_aliases_to_997_1009', 'hash_aliases_to_gt_1009')
$books     = Join-Path $env:USERPROFILE 'Desktop\FIN_RECON_003_JULY_BOOKS'
$qa        = 'C:\VehicleSoft_FR3_QA'
$work      = 'C:\VehicleSoft_Release'
$log       = 'C:\VehicleSoft_Release\release_v135_step1.log'
function Invoke-Preflight([string]$repo, [string]$service, [string]$outDir) {
  New-Item -ItemType Directory -Force -Path $outDir | Out-Null
  $text = @(& powershell.exe -NoProfile -ExecutionPolicy Bypass -File $ps1 -RepoDir $repo -DbPath $db -Python $py -OutputDir $outDir -RequiredServices $service -ExpectedHead $release 2>&1 | ForEach-Object { [string]$_ })
  $text | ForEach-Object { Write-Host "    $_" }
  return ,$text
}
function Get-Verdict([string[]]$text) {
  $results = @($text | Where-Object { $_ -like 'RESULT=*' })
  if ($results.Count -ne 1) { throw 'the preflight did not finish - its RESULT line is missing' }
  if (@($text | Where-Object { $_ -like 'IMPORT_AUTHORIZED=NO*' }).Count -ne 1) { throw 'the preflight did not print IMPORT_AUTHORIZED=NO' }
  if (@($text | Where-Object { $_ -eq "CODE_RELEASE_PIN=$release" }).Count -ne 1) { throw 'the preflight was not pinned to the release commit' }
  if (@($text | Where-Object { $_ -like 'CHECK code_head_is_pinned_release=PASS*' }).Count -ne 1) { throw 'the code checked is not the release commit (code_head_is_pinned_release)' }
  if (@($text | Where-Object { $_ -eq 'SERVICE_HEALTH=VERIFIED' }).Count -ne 1) { throw 'the service TransportReport is not running (SERVICE_HEALTH)' }
  $failed = @($text | ForEach-Object { if ($_ -match '^CHECK ([a-z0-9_]+)=FAIL') { $Matches[1] } })
  $blocking = @($failed | Where-Object { $directory -notcontains $_ })
  if ($results[0] -eq 'RESULT=PREFLIGHT_PASSED') { return '' }
  if (($results[0] -eq 'RESULT=PREFLIGHT_FAILED') -and ($failed.Count -gt 0) -and ($blocking.Count -eq 0)) { return "the customer directory changed since 05.11 ($($failed -join ', ')) - not a check of this release; the July import needs a fresh snapshot" }
  throw "the preflight says $($results[0]); failed checks: $($blocking -join ', ')"
}
New-Item -ItemType Directory -Force -Path $work | Out-Null
try { Start-Transcript -Path $log -Append | Out-Null } catch { Write-Host 'NOTE: the log file could not be started' }
$failure = $null
$note = ''
$saved = @($env:PYTHONUTF8, $env:PYTHONDONTWRITEBYTECODE, $env:FIN_RECON_003_JULY_BOOKS)
try {
  if ((hostname) -ne 'srv-yoqsh') { throw "this computer is $(hostname), not SRV-YOQSH" }
  foreach ($path in @($prod, $db, $py)) {
    if (-not (Test-Path -LiteralPath $path)) { throw "not found: $path" }
  }
  $head = [string](& git -C $prod rev-parse HEAD)
  if ($head -ne $baseline) { throw "the program here is at $head, this release was prepared for $baseline" }
  $modified = @(& git -C $prod status --porcelain --untracked-files=no)
  if ($modified.Count -gt 0) { throw "program files were edited on this server: $($modified -join '; ')" }
  $origin = [string](& git -C $prod config --get remote.origin.url)
  if (-not $origin) { throw "the GitHub address could not be read from $prod" }
  $run = Join-Path $qa ('run_' + (Get-Date -Format 'yyyyMMdd_HHmmss'))
  $clone = Join-Path $run 'repo'
  New-Item -ItemType Directory -Force -Path $run | Out-Null
  Write-Host "== A fresh copy of the code from GitHub: $clone"
  & git clone --quiet $origin $clone 2>&1 | Out-Null
  if ($LASTEXITCODE -ne 0) { throw "git clone failed (exit $LASTEXITCODE) - is there a connection to GitHub?" }
  & git -C $clone checkout --quiet --detach $release 2>&1 | Out-Null
  if ($LASTEXITCODE -ne 0) { throw "the release commit $release is not on GitHub" }
  if ([string](& git -C $clone rev-parse HEAD) -ne $release) { throw 'the copy is not at the release commit' }
  & git -C $clone merge-base --is-ancestor $release origin/main
  if ($LASTEXITCODE -ne 0) { throw 'the release commit is not in main' }
  & git -C $clone merge-base --is-ancestor $baseline $release
  if ($LASTEXITCODE -ne 0) { throw 'the release does not continue the program on this server' }
  $names = @(& git -C $clone diff --name-only $baseline $release)
  $migrations = @($names | Where-Object { $_ -like 'migrate_*' })
  if ($migrations.Count -gt 0) { throw "migrations in the release: $($migrations -join ', '); this release has none" }
  $code = @($names | Where-Object { ($_ -notlike 'docs/*') -and ($_ -notlike '.github/*') -and ($_ -notlike 'tools/test_*') } | Sort-Object)
  if (($code -join ',') -ne (($runtime | Sort-Object) -join ',')) { throw "the program code of the release is not the three checked files: $($code -join ', ')" }
  Write-Host "RELEASE CODE: $($code -join ', ')"
  $clash = @(& git -C $clone diff --name-only --diff-filter=A $baseline $release | Where-Object { Test-Path -LiteralPath (Join-Path $prod $_) })
  if ($clash.Count -gt 0) { throw "files of the release already lie on the server: $($clash -join ', ')" }
  $env:PYTHONUTF8 = '1'
  $env:PYTHONDONTWRITEBYTECODE = '1'
  $before = @()
  if (Test-Path -LiteralPath $books) {
    $env:FIN_RECON_003_JULY_BOOKS = $books
    $before = @(Get-ChildItem -LiteralPath $books -Filter *.xlsx | Sort-Object Name | Get-FileHash -Algorithm SHA256 | ForEach-Object { $_.Hash })
    Write-Host "REAL BOOKS: $($before.Count)"
  } else { Write-Host 'REAL BOOKS: SKIPPED - there is no folder Desktop\FIN_RECON_003_JULY_BOOKS' }
  Push-Location -LiteralPath $clone
  try {
    foreach ($suite in $suites) {
      $lines = @(& $py -m unittest $suite 2>&1 | ForEach-Object { [string]$_ })
      $exit = $LASTEXITCODE
      Write-Host "TESTS $suite : $(@($lines | Where-Object { $_ -match '^(Ran |OK|FAILED)' }) -join ' / ')"
      if ($exit -ne 0) {
        $lines | Where-Object { $_ -match '^(FAIL|ERROR): ' } | ForEach-Object { Write-Host "    $_" }
        throw "the tests failed: $suite"
      }
    }
  } finally { Pop-Location }
  if ($before.Count -gt 0) {
    $after = @(Get-ChildItem -LiteralPath $books -Filter *.xlsx | Sort-Object Name | Get-FileHash -Algorithm SHA256 | ForEach-Object { $_.Hash })
    if (($before -join ',') -ne ($after -join ',')) { throw 'a July book changed during the tests' }
    Write-Host 'REAL BOOKS UNCHANGED: YES'
  }
  $ps1 = Join-Path $run 'FIN_RECON_003_JULY_PREFLIGHT_READONLY.ps1'
  $body = @(& git -C $clone show "origin/main:$preflight")
  if ($LASTEXITCODE -ne 0) { throw "$preflight is not in main" }
  [IO.File]::WriteAllText($ps1, (($body -join "`r`n") + "`r`n"), [Text.Encoding]::ASCII)
  $sha = (Get-FileHash -LiteralPath $ps1 -Algorithm SHA256).Hash.ToLower()
  if ($sha -ne $preflightSha) { throw "the preflight in main is not the checked one (sha256 $sha)" }
  Write-Host "== Read-only preflight: the copy of the code against a temporary copy of the live database"
  $note = Get-Verdict (Invoke-Preflight $clone 'TransportReport' (Join-Path $run 'preflight'))
  Write-Host '== The same preflight with a service that does not exist must not pass'
  $fake = Invoke-Preflight $clone 'TransportReport_NOT_EXISTING' (Join-Path $run 'preflight_false_pass')
  $fakeResult = @($fake | Where-Object { $_ -like 'RESULT=*' })
  if (($fakeResult.Count -ne 1) -or ($fakeResult[0] -eq 'RESULT=PREFLIGHT_PASSED') -or (@($fake | Where-Object { $_ -like 'SERVICE_HEALTH=NOT_VERIFIED*' }).Count -ne 1)) { throw 'a missing service did not stop the preflight (false PASS)' }
  Write-Host "FALSE PASS CHECK: $($fakeResult[0]) with SERVICE_HEALTH=NOT_VERIFIED - as it should be"
} catch {
  $failure = $_.Exception.Message
} finally {
  $env:PYTHONUTF8 = $saved[0]
  $env:PYTHONDONTWRITEBYTECODE = $saved[1]
  $env:FIN_RECON_003_JULY_BOOKS = $saved[2]
}
Write-Host "PROGRAM VERSION NOW: $([string](& git -C $prod rev-parse --short HEAD))"
if ($failure) { Write-Host "RESULT: STOP - $failure" } elseif ($note) { Write-Host "RESULT: REHEARSAL PASSED WITH A NOTE - $note" } else { Write-Host 'RESULT: REHEARSAL PASSED' }
Write-Host "LOG FILE: $log"
try { Stop-Transcript | Out-Null } catch { }
}
```

**Что должно получиться:** последняя строка перед `LOG FILE` —
`RESULT: REHEARSAL PASSED`. Выше:

- `RELEASE CODE: tools/drone_works_fingerprint_export.py, tools/drone_works_selective.py, tools/import_drone_works.py`;
- девять строк `TESTS ... : Ran N tests ... / OK`;
- `REAL BOOKS: 5` и `REAL BOOKS UNCHANGED: YES` — или `REAL BOOKS: SKIPPED`,
  если папки с книгами на рабочем столе нет (это не ошибка);
- строки preflight с отступом, среди них `CHECK code_head_is_pinned_release=PASS`,
  `SERVICE_HEALTH=VERIFIED`, `TEMP_COPY_REMOVED=YES`, `RESULT=PREFLIGHT_PASSED`
  и `IMPORT_AUTHORIZED=NO` — preflight ничего не разрешает;
- `FALSE PASS CHECK: RESULT=... with SERVICE_HEALTH=NOT_VERIFIED - as it should be`;
- `PROGRAM VERSION NOW: a73d756` — программа не тронута.

**Если `RESULT: REHEARSAL PASSED WITH A NOTE - the customer directory changed
since 05.11 ...`:** проверки выпуска прошли, а справочник заказчиков или
число работ изменились с 05.11 — обычная работа в программе. На выпуск это
не влияет: выпуск не читает и не пишет справочник. Импорт июля после
выпуска потребует свежего снимка. Пришлите журнал.

**Если `RESULT: STOP - ...`:** дальше не идти. Программа и база не тронуты;
папка `C:\VehicleSoft_FR3_QA` остаётся — не удаляйте её. Пришлите
`C:\VehicleSoft_Release\release_v135_step1.log`.

**Прислать:** `C:\VehicleSoft_Release\release_v135_step1.log`. Дальше —
моё сообщение «v1.35 — можно»: я снимаю три строки FIN-RECON-003 из гейта
выпуска и только после этого вы переходите к шагу 2.

## Шаг 2. Проверка перед выпуском — ничего не меняет

Проверка, что сервер и новая версия ровно такие, как задумано: тот сервер;
та версия программы сейчас; коммит выпуска есть в `main` и в нём ровно
проверенный код; гейт выпуска пуст; миграций в выпуске нет; ни один новый
файл выпуска не лежит уже в папке программы; резервной копии хватит места.
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
$baseline  = 'a73d756dcb67c06388e58bc7c4a0a1ac64e59e34'
$reviewed  = '90516935fbd45a977c8c855d24e9385e621a437a'
$release   = '90516935fbd45a977c8c855d24e9385e621a437a'
$runbookTest = 'tests/test_fin_recon_003_release_v135.py'
$work      = 'C:\VehicleSoft_Release'
$log       = 'C:\VehicleSoft_Release\release_v135_step2.log'
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
  $migrations = @(& git diff --name-only $head $release | Where-Object { $_ -like 'migrate_*' })
  if ($migrations.Count -gt 0) { throw "migrations in the release: $($migrations -join ', '); this release has none" }
  $untracked = @(& git status --porcelain --untracked-files=all | Where-Object { $_ -match '^\?\? ' })
  $clash = @(& git diff --name-only --diff-filter=A $head $release | Where-Object { Test-Path -LiteralPath (Join-Path $prod $_) })
  if ($clash.Count -gt 0) { throw "files of the release already lie on the server: $($clash -join ', ')" }
  Write-Host "UNTRACKED FILES: $($untracked.Count), none of them is a file of the release"
  Write-Host "RELEASE: $head -> $release"
  $later = @(& git --no-pager log --format=%h "$release..$main")
  Write-Host "MAIN: $main - $($later.Count) later commit(s) of main are not taken"
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
`RESULT: CHECK PASSED - go on to step 3`. Выше:

- три строки `SERVICE ... : Running`;
- `UNTRACKED FILES: N, none of them is a file of the release` — число
  неотслеживаемых файлов на сервере сейчас (04.11 было 111);
- `RELEASE: a73d756... -> 90516935...`;
- `MAIN: ... - N later commit(s) of main are not taken` — коммиты `main`
  после выпуска, которые остаются в стороне: экраны GPS и agro-work, этот
  ранбук и всё, что влито позже;
- одна строка влитого изменения: `Merge pull request #260 from
  claude/fin-recon-003-parser-repair` — исправление импортёра. Ветка PR #261
  и патч едут коммитами под коммитом выпуска и слиянием `b4cd7d2`, которое
  в списке не печатается: это не запрос GitHub.

Под `MIGRATIONS BEFORE THE RELEASE:` будет `DRIFT DETECTED` и две миграции
Дронов в `file-but-not-registered` — это известное состояние сервера с
выпуска v1.20, а не ошибка; шаг 3 сверяет, что список до и после выпуска
один и тот же.

**Если `RESULT: STOP - the release gate is closed ...`:** три строки
FIN-RECON-003 ещё стоят в гейте — шаг 1 не прислан или гейт ещё не снят.
Ничего не менялось; напишите мне.

**Если другой `RESULT: STOP - ...`:** дальше не идти. Программа не тронута и
работает как раньше. Пришлите `C:\VehicleSoft_Release\release_v135_step2.log`.

**Прислать:** `C:\VehicleSoft_Release\release_v135_step2.log`. Если в нём
`CHECK PASSED`, мой ответ ждать не нужно — можно сразу к шагу 3.

## Шаг 3. Выпуск — программа не работает 3–5 минут

Что делает блок, по порядку:

1. ещё раз проверяет всё, что проверял шаг 2 (ничего не меняя);
2. останавливает программу и обоих ботов;
3. проверяет, что базу никто не держит открытой;
4. делает резервную копию базы в `D:\transport-report-backups\production\daily\`
   и проверяет её: копия целая и размером ровно как база;
5. забирает с GitHub ровно коммит выпуска `90516935`;
6. проверяет, что миграции в базе остались как были — в этом выпуске их нет;
7. запускает программу и обоих ботов;
8. проверяет, что открывается страница входа и что раздел «Заявки и GPS»
   (`/agro-work/`) на месте — та же проверка, что во всех выпусках с v1.22.

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
$baseline  = 'a73d756dcb67c06388e58bc7c4a0a1ac64e59e34'
$reviewed  = '90516935fbd45a977c8c855d24e9385e621a437a'
$release   = '90516935fbd45a977c8c855d24e9385e621a437a'
$runbookTest = 'tests/test_fin_recon_003_release_v135.py'
$work      = 'C:\VehicleSoft_Release'
$log       = 'C:\VehicleSoft_Release\release_v135_step3.log'
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
  $migrations = @(& git diff --name-only $head $release | Where-Object { $_ -like 'migrate_*' })
  if ($migrations.Count -gt 0) { throw "migrations in the release: $($migrations -join ', '); this release has none" }
  $clash = @(& git diff --name-only --diff-filter=A $head $release | Where-Object { Test-Path -LiteralPath (Join-Path $prod $_) })
  if ($clash.Count -gt 0) { throw "files of the release already lie on the server: $($clash -join ', ')" }
  Write-Host "RELEASE: $head -> $release"
  $later = @(& git --no-pager log --format=%h "$release..$main")
  Write-Host "MAIN: $main - $($later.Count) later commit(s) of main are not taken"
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

- `RELEASE: a73d756... -> 90516935...` и `MAIN: ... not taken`;
- `SERVICES: Stopped`;
- `CLEAN -- no other process has the database open` или
  `STALE: no process holds the database` — оба ответа годятся: второй — известная
  особенность этой базы, а не ошибка (`docs/RELEASE_GATE.md`, «Приборы с
  известным шумом»);
- `BACKUP: D:\transport-report-backups\production\daily\transport_....db (...
  bytes, integrity ok, same size as the database)`;
- `UPDATED: a73d756... -> 90516935...`;
- `MIGRATIONS REGISTERED: 65 -> 65, unchanged`;
- `SERVICES: Running`;
- `SMOKE /login: 200 ...` и `SMOKE /agro-work/: asks to log in ...`;
- `NEW TRACEBACKS IN logs\error.log: 0`.

Строк от инструментов много — смотреть нужно только на `RESULT`.

**Если `RESULT: STOP - ...`:** дальше не идти. Программа в любом случае
снова запущена — блок запускает её сам. Строка `PROGRAM VERSION NOW:`
говорит, какая версия работает: `a73d756` — прежняя, ничего не поменялось;
`9051693` — новая. Пришлите `C:\VehicleSoft_Release\release_v135_step3.log`.
Если программа в браузере не открывается совсем, — раздел «Откат» в конце.

**Если `RESULT: RELEASE PASSED WITH A WARNING`:** выпуск прошёл, но в журнале
ошибок программы появились новые записи. Пришлите журнал шага 3 и дождитесь
ответа до шага 4.

**Прислать:** `C:\VehicleSoft_Release\release_v135_step3.log`. Если в нём
`RELEASE PASSED`, мой ответ ждать не нужно — можно сразу к шагу 4.

## Шаг 4. Проверка после выпуска — ничего не меняет

Тот же read-only preflight, что в шаге 1, но теперь по коду самой программы
в `C:\transport-report`: на сервере стоит ровно `90516935`, файлы импорта —
согласованные, защита работает на копии сегодняшней базы, служба
`TransportReport` запущена. Блок печатает и время запуска службы — оно
позже обновления шага 3.

```powershell
& {
$ErrorActionPreference = 'Continue'
$ProgressPreference = 'SilentlyContinue'
$prod      = 'C:\transport-report'
$db        = 'C:\transport-report\instance\transport.db'
$py        = 'C:\Program Files\Python314\python.exe'
$release   = '90516935fbd45a977c8c855d24e9385e621a437a'
$preflight = 'ops/fin_recon_003/FIN_RECON_003_JULY_PREFLIGHT_READONLY.ps1'
$preflightSha = '7d1823ef9edc97fa7b955a6cc1e5e6944d06328758cb3ab04c884ce08aa3e46d'
$directory = @('customers', 'aliases_all', 'customers_904_1009', 'works_total', 'hash_customers_le_903', 'hash_customers_904_996', 'hash_customers_997_1009', 'hash_customers_gt_1009', 'hash_aliases_to_le_903', 'hash_aliases_to_904_996', 'hash_aliases_to_997_1009', 'hash_aliases_to_gt_1009')
$work      = 'C:\VehicleSoft_Release'
$log       = 'C:\VehicleSoft_Release\release_v135_step4.log'
function Invoke-Preflight([string]$repo, [string]$service, [string]$outDir) {
  New-Item -ItemType Directory -Force -Path $outDir | Out-Null
  $text = @(& powershell.exe -NoProfile -ExecutionPolicy Bypass -File $ps1 -RepoDir $repo -DbPath $db -Python $py -OutputDir $outDir -RequiredServices $service -ExpectedHead $release 2>&1 | ForEach-Object { [string]$_ })
  $text | ForEach-Object { Write-Host "    $_" }
  return ,$text
}
function Get-Verdict([string[]]$text) {
  $results = @($text | Where-Object { $_ -like 'RESULT=*' })
  if ($results.Count -ne 1) { throw 'the preflight did not finish - its RESULT line is missing' }
  if (@($text | Where-Object { $_ -like 'IMPORT_AUTHORIZED=NO*' }).Count -ne 1) { throw 'the preflight did not print IMPORT_AUTHORIZED=NO' }
  if (@($text | Where-Object { $_ -eq "CODE_RELEASE_PIN=$release" }).Count -ne 1) { throw 'the preflight was not pinned to the release commit' }
  if (@($text | Where-Object { $_ -like 'CHECK code_head_is_pinned_release=PASS*' }).Count -ne 1) { throw 'the code checked is not the release commit (code_head_is_pinned_release)' }
  if (@($text | Where-Object { $_ -eq 'SERVICE_HEALTH=VERIFIED' }).Count -ne 1) { throw 'the service TransportReport is not running (SERVICE_HEALTH)' }
  $failed = @($text | ForEach-Object { if ($_ -match '^CHECK ([a-z0-9_]+)=FAIL') { $Matches[1] } })
  $blocking = @($failed | Where-Object { $directory -notcontains $_ })
  if ($results[0] -eq 'RESULT=PREFLIGHT_PASSED') { return '' }
  if (($results[0] -eq 'RESULT=PREFLIGHT_FAILED') -and ($failed.Count -gt 0) -and ($blocking.Count -eq 0)) { return "the customer directory changed since 05.11 ($($failed -join ', ')) - not a check of this release; the July import needs a fresh snapshot" }
  throw "the preflight says $($results[0]); failed checks: $($blocking -join ', ')"
}
New-Item -ItemType Directory -Force -Path $work | Out-Null
try { Start-Transcript -Path $log -Append | Out-Null } catch { Write-Host 'NOTE: the log file could not be started' }
$failure = $null
$note = ''
try {
  if ((hostname) -ne 'srv-yoqsh') { throw "this computer is $(hostname), not SRV-YOQSH" }
  $head = [string](& git -C $prod rev-parse HEAD)
  if ($head -ne $release) { throw "the program here is at $head, not at the release $release - was step 3 done?" }
  $modified = @(& git -C $prod status --porcelain --untracked-files=no)
  if ($modified.Count -gt 0) { throw "program files were edited on this server: $($modified -join '; ')" }
  $run = Join-Path $work ('v135_step4_' + (Get-Date -Format 'yyyyMMdd_HHmmss'))
  New-Item -ItemType Directory -Force -Path $run | Out-Null
  $ps1 = Join-Path $run 'FIN_RECON_003_JULY_PREFLIGHT_READONLY.ps1'
  $body = @(& git -C $prod show "origin/main:$preflight")
  if ($LASTEXITCODE -ne 0) { throw "$preflight is not in main" }
  [IO.File]::WriteAllText($ps1, (($body -join "`r`n") + "`r`n"), [Text.Encoding]::ASCII)
  $sha = (Get-FileHash -LiteralPath $ps1 -Algorithm SHA256).Hash.ToLower()
  if ($sha -ne $preflightSha) { throw "the preflight in main is not the checked one (sha256 $sha)" }
  Write-Host '== Read-only preflight: the program itself against a temporary copy of the live database'
  $note = Get-Verdict (Invoke-Preflight $prod 'TransportReport' (Join-Path $run 'preflight'))
  $started = 'unknown'
  try { $started = (Get-Process -Id (Get-CimInstance Win32_Service -Filter "Name='TransportReport'").ProcessId -ErrorAction Stop).StartTime.ToString('yyyy-MM-dd HH:mm:ss') } catch { }
  Write-Host "SERVICE TransportReport STARTED AT: $started"
} catch {
  $failure = $_.Exception.Message
}
Write-Host "PROGRAM VERSION NOW: $([string](& git -C $prod rev-parse --short HEAD))"
if ($failure) { Write-Host "RESULT: STOP - $failure" } elseif ($note) { Write-Host "RESULT: RELEASE CONFIRMED WITH A NOTE - $note" } else { Write-Host 'RESULT: RELEASE CONFIRMED' }
Write-Host "LOG FILE: $log"
try { Stop-Transcript | Out-Null } catch { }
}
```

**Что должно получиться:** `RESULT: RELEASE CONFIRMED`. Выше — строки
preflight с `CHECK code_head_is_pinned_release=PASS`, `SERVICE_HEALTH=VERIFIED`,
`RESULT=PREFLIGHT_PASSED`, `IMPORT_AUTHORIZED=NO`; `SERVICE TransportReport
STARTED AT:` — время из окна шага 3 (`unknown`, если Windows не отдала время
запуска, — это не ошибка, строка только для сведения);
`PROGRAM VERSION NOW: 9051693`.

`RELEASE CONFIRMED WITH A NOTE` — то же, что в шаге 1: справочник изменился
с 05.11, на выпуск это не влияет.

**Прислать:** `C:\VehicleSoft_Release\release_v135_step4.log`. Метку
выпуска (тег) поставите вы — команду я пришлю после вашего ответа.

**После выпуска импорт июля по-прежнему запрещён.** Выпуск ставит только
защиту. Загрузка — отдельным решением, по подписанному акту, через
выборочную загрузку по точному списку.

## Если что-то пошло не так

| Что видно | Что это значит | Что делать |
|---|---|---|
| `RESULT: STOP - this window is not run as administrator` | окно PowerShell открыто без прав администратора | закрыть окно, открыть «от имени администратора», повторить шаг |
| `RESULT: STOP - the program here is at ...` | на сервере не `a73d756` (шаги 1–2) или не `90516935` (шаг 4) | прислать журнал; ничего не менялось |
| `RESULT: STOP - git clone failed ...` | шаг 1 не скачал код с GitHub | проверить интернет на сервере, повторить шаг 1; ничего не менялось |
| `RESULT: STOP - the program code of the release is not the three checked files ...` | в выпуске оказался другой код | прислать журнал; ничего не менялось |
| `RESULT: STOP - the tests failed: ...` | набор тестов не прошёл на сервере | прислать журнал; ничего не менялось |
| `RESULT: STOP - a July book changed during the tests` | книга на рабочем столе изменилась во время тестов | не открывать книги; прислать журнал |
| `RESULT: STOP - the preflight in main is not the checked one ...` | файл preflight в `main` не тот, что проверен | прислать журнал; ничего не менялось |
| `RESULT: STOP - the preflight says ...` | проверка выпуска не прошла; имена проверок — в строке | прислать журнал; ничего не менялось |
| `RESULT: STOP - the service TransportReport is not running (SERVICE_HEALTH)` | служба программы не запущена | прислать журнал |
| `RESULT: STOP - a missing service did not stop the preflight (false PASS)` | preflight прошёл бы и без службы — так быть не должно | прислать журнал; ничего не менялось |
| `RESULT: STOP - the release gate is closed` | в `docs/RELEASE_GATE.md` есть открытые пункты | шаг 1 не прислан или гейт ещё не снят — написать мне; ничего не менялось |
| `RESULT: STOP - the release commit is not in main` | в `main` с сервера не видно `90516935` — так не должно быть: он влит 06.11 | прислать журнал; ничего не менялось |
| `RESULT: STOP - the release commit does not contain the checked version` | постоянные блока не согласованы | прислать журнал; ничего не менялось |
| `RESULT: STOP - between the checked version and the release commit there are changes outside docs/` | в выпуске код, которого не было в проверке | прислать журнал; ничего не менялось |
| `RESULT: STOP - this version is already on the server` | шаг 3 уже выполнен раньше | идти к шагу 4 |
| `RESULT: STOP - migrations in the release` | в выпуск попала миграция | прислать журнал; ничего не менялось |
| `RESULT: STOP - files of the release already lie on the server` | в папке программы уже лежит файл с именем нового файла выпуска | не удалять и не переносить его самому; прислать журнал; ничего не менялось |
| `RESULT: STOP - the backup did not pass` | копия базы не записалась или не совпала | прислать журнал; программа снова запущена на прежней версии |
| `RESULT: STOP - git merge --ff-only failed` | Git не смог забрать новую версию — например, мешает файл с тем же именем | прислать журнал; программа снова запущена на прежней версии |
| `RESULT: STOP - after the update the migrations are ...` | после обновления реестр миграций выглядит иначе, чем до | прислать журнал; программа запущена на новой версии, база не менялась |
| `RESULT: STOP - /agro-work/ answered ... instead of asking to log in` | службы запущены, но раздел «Заявки и GPS» не открывается | прислать журнал; строка `PROGRAM VERSION NOW:` скажет, какая версия работает |

## Откат — только если после шага 3 программа не открывается

Выполнять **только** если шаг 3 закончился `RESULT: STOP`, программа в
браузере не открывается совсем, а ответа от меня нет. Блок возвращает
прежнюю версию программы (`a73d756`, выпуск v1.34). Базу он **не** трогает:
миграций в выпуске нет, а сам выпуск в базу не пишет. Резервная копия
шага 3 остаётся на диске D:.

```powershell
& {
$ErrorActionPreference = 'Continue'
$ProgressPreference = 'SilentlyContinue'
$prod      = 'C:\transport-report'
$services  = @('TransportReport', 'TransportBot', 'TransportBot003')
$site      = 'http://10.103.25.14:5050'
$baseline  = 'a73d756dcb67c06388e58bc7c4a0a1ac64e59e34'
$reviewed  = '90516935fbd45a977c8c855d24e9385e621a437a'
$runbookTest = 'tests/test_fin_recon_003_release_v135.py'
$work      = 'C:\VehicleSoft_Release'
$log       = 'C:\VehicleSoft_Release\release_v135_rollback.log'
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

**Что должно получиться:** `ROLLED BACK: ... -> a73d756...`,
`SERVICES: Running`, `SMOKE /login: 200`, `RESULT: ROLLBACK PASSED`.

**Почему `git reset --keep`, а не `--hard`.** `--keep` возвращает файлы
программы к прежней версии и **отказывается** работать, если в них есть
правки, сделанные руками; неотслеживаемые файлы — журналы, выгрузки, база —
он не трогает никогда. Запрет устава на `git reset --hard` защищает общую
историю `main`; здесь двигается только рабочая копия сервера, назад ровно на
тот коммит, с которого шаг 3 её сдвинул.

**После отката** напишите мне: решаем вместе, что дальше.

**Прислать:** `C:\VehicleSoft_Release\release_v135_rollback.log` и журнал
шага 3.
