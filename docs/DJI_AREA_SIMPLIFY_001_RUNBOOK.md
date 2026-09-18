# DJI-AREA-SIMPLIFY-001 — слепой holdout сентября: два блока для владельца

Протокол и критерии зафиксированы заранее в
`docs/DJI_AREA_SIMPLIFY_001_HOLDOUT_PREREG.md`. Здесь — только исполнение.

**Production не участвует ни в одном шаге.** База и служба production не
называются ни в одном блоке; сборщик production (`DroneCollectorDaily`) и его
сессия не используются. Данные идут только на площадку (порт 5051), и блок
отказывается работать, если приёмник в `.env` — не она.

Блоков два, потому что работа идёт на двух машинах, и порядок жёсткий:

| # | Где | Что делает | Пишет |
|---|---|---|---|
| W | рабочая машина, где живёт сборщик | список сентября → план (SHA-256) → адресный сбор V4 по плану | только в площадку |
| S | SRV-YOQSH, площадка | пересчёт сентября и отчёт holdout | только в базу площадки, после резервной копии |

Между ними — один ручной шаг: скопировать `plan.json` с рабочей машины на
сервер (путь назван ниже).

## Что именно разрешает владелец

Один запрос, одно «да»:

> Разрешаю живой сбор из кабинета DJI сохранённой сессией: список вылетов с
> 31.08.2026 по сегодня и источники (card, route, airlines, V4) примерно по
> 650 записям из плана, с отправкой **только на площадку**; и запись пересчёта
> сентября в базу площадки после резервной копии.

Объём оценён по августу: около 235 кандидатов и около 420 контрольных записей на
17 дней. На 09.09.2026 такой же сбор шёл 11 секунд на запись (223 записи за
41 минуту), то есть блок W займёт около двух часов. Он возобновляем.

Весь парк не собирается: это около 15 % записей периода.

## Подготовка рабочей машины (один раз, руками)

Вход в кабинет DJI выполняется человеком, поэтому в блок он не входит.

1. Блок W сам клонирует ветку в `C:\VehicleSoft_Holdout\src`. Если каталога ещё
   нет, запустите блок один раз: он остановится на проверке `.env` и ничего не
   соберёт.
2. Положите `C:\VehicleSoft_Holdout\src\drone_collector\.env` с адресом
   **площадки** в `VEHICLE_SOFT_BASE_URL` (порт 5051) и токеном площадки в
   `DRONE_API_TOKEN`. Блок проверяет только порт и значение токена не печатает.
3. Сохраните сессию:

```powershell
Set-Location 'C:\VehicleSoft_Holdout\src'
& 'C:\Program Files\Python314\python.exe' -m drone_collector.main --save-session
```

Если `--save-session` ведёт себя так, как описано в PR #127, сессию можно
сохранить из ветки этого PR и указать путь к файлу в `DJI_STORAGE_STATE` того же
`.env`. Ветка holdout `drone_collector/session.py` не трогает.

## Блок W — рабочая машина

Вставлять целиком, ничего не редактируя. По одной команде на строку.

Блок обёрнут в `& { ... }` намеренно: вставленные в консоль строки PowerShell
исполняет по одной, и `throw` в одной из них следующие не останавливает. Без
обёртки отказ на проверке приёмника не помешал бы сборщику запуститься строкой
ниже. В обёртке блок — одно выражение, и `throw` прекращает его целиком.

```powershell
& {
$ErrorActionPreference = 'Continue'
$review  = 'C:\VehicleSoft_DJI_Review_20260918'
$work    = 'C:\VehicleSoft_Holdout'
$src     = 'C:\VehicleSoft_Holdout\src'
$envFile = 'C:\VehicleSoft_Holdout\src\drone_collector\.env'
$outDir  = 'C:\VehicleSoft_Holdout\src\drone_collector\out'
$listDb  = 'C:\VehicleSoft_Holdout\list\holdout_list.db'
$planDir = 'C:\VehicleSoft_Holdout\plan'
$plan    = 'C:\VehicleSoft_Holdout\plan\plan.json'
$ids     = 'C:\VehicleSoft_Holdout\plan\capture_ids.txt'
$py      = 'C:\Program Files\Python314\python.exe'
$branch  = 'claude/dji-area-simplify-001'
if (-not (Test-Path -LiteralPath $py)) { throw "STEP FAILED: python not found: $py" }
if (-not (Test-Path -LiteralPath $review)) { throw "STEP FAILED: review clone not found: $review" }
if (-not (Test-Path -LiteralPath $work)) { New-Item -ItemType Directory -Force -Path $work | Out-Null }
if (-not (Test-Path -LiteralPath $src)) { & git clone --branch $branch --quiet $review $src }
if (-not (Test-Path -LiteralPath $src)) { throw "STEP FAILED: git clone did not create $src" }
& git -C $src --no-pager log --oneline -1
$head = (& git -C $src rev-parse --abbrev-ref HEAD)
if ($head -ne $branch) { throw "STEP FAILED: $src is on $head, expected $branch" }
if (-not (Test-Path -LiteralPath $envFile)) { throw "STEP FAILED: $envFile not found -- see the preparation section of the runbook; nothing was collected" }
$urlLines = @(Select-String -LiteralPath $envFile -Pattern '^\s*VEHICLE_SOFT_BASE_URL\s*=')
if ($urlLines.Count -eq 0) { throw "STEP FAILED: VEHICLE_SOFT_BASE_URL is not set in $envFile" }
$notStaging = @($urlLines | Where-Object { $_.Line -notmatch ':5051' })
if ($notStaging.Count -gt 0) { throw "STEP FAILED: a receiver line in .env is not the staging port 5051 -- refusing to send anywhere else" }
if ($env:VEHICLE_SOFT_BASE_URL -and ($env:VEHICLE_SOFT_BASE_URL -notmatch ':5051')) { throw "STEP FAILED: the process environment overrides the receiver with a non-staging address" }
Write-Host 'RECEIVER CHECK: staging port 5051'
Set-Location $src
& $py tools\test_dji_area_holdout.py
if ($LASTEXITCODE -ne 0) { throw "STEP FAILED: holdout tool self-test exit $LASTEXITCODE" }
& $py -m unittest tests.test_dji_area_accounting
if ($LASTEXITCODE -ne 0) { throw "STEP FAILED: accounting classes self-test exit $LASTEXITCODE" }
if (-not (Test-Path -LiteralPath $plan)) {
  if (-not (Test-Path -LiteralPath $listDb)) {
    $walkTo = Get-Date
    if ($walkTo -gt [datetime]'2026-10-01') { $walkTo = [datetime]'2026-10-01' }
    $walkToText = $walkTo.ToString('yyyy-MM-dd')
    & $py -m drone_collector.main --from 2026-08-31 --to $walkToText --dry-run
    if ($LASTEXITCODE -ne 0) { throw "STEP FAILED: list walk (dry-run) exit $LASTEXITCODE" }
    $dump = Get-ChildItem -LiteralPath $outDir -Filter 'flights_2026-08-31_*.json' | Sort-Object LastWriteTime | Select-Object -Last 1
    if (-not $dump) { throw "STEP FAILED: the list dump was not written to $outDir" }
    & $py tools\dji_area_holdout.py list-db --list-json $dump.FullName --db $listDb
    if ($LASTEXITCODE -ne 0) { throw "STEP FAILED: list-db exit $LASTEXITCODE" }
  }
  & $py tools\dji_area_holdout.py plan --db $listDb --from 2026-09-01 --to auto --out $planDir
  if ($LASTEXITCODE -ne 0) { throw "STEP FAILED: plan exit $LASTEXITCODE" }
}
if (-not (Test-Path -LiteralPath $ids)) { throw "STEP FAILED: $ids not found -- the plan is incomplete" }
$locked = (Get-Content -LiteralPath $plan -Raw | ConvertFrom-Json)
Write-Host ("PLAN SHA256: " + $locked.locked_sha256)
Write-Host ("PLAN PERIOD: " + $locked.locked.period.from + " .. " + $locked.locked.period.to)
$sendTo = ([datetime]$locked.locked.period.to).AddDays(1).ToString('yyyy-MM-dd')
& $py -m drone_collector.main --from 2026-08-31 --to $sendTo --kind backfill
if ($LASTEXITCODE -ne 0) { throw "STEP FAILED: flight backfill to staging exit $LASTEXITCODE" }
& $py -m drone_collector.main --sources --ids-file $ids --send-sources
$rc = $LASTEXITCODE
Write-Host "SOURCES EXIT CODE: $rc"
if (($rc -ne 0) -and ($rc -ne 18)) { throw "STEP FAILED: source capture exit $rc" }
if ($rc -eq 18) { Write-Host 'INCOMPLETE: run this block again -- only the missing flights will be visited' }
Compress-Archive -Path "$planDir\*" -DestinationPath 'C:\VehicleSoft_Holdout\holdout_plan.zip' -Force
Write-Host 'SEND BACK: C:\VehicleSoft_Holdout\holdout_plan.zip and the console text above'
}
```

### Что блок W делает по шагам

1. Проверяет, что приёмник — площадка, **до** любого обращения к кабинету.
2. Прогоняет самотесты инструмента и учётных классов.
3. Если плана ещё нет: читает список вылетов в режиме `--dry-run` (ничего никуда
   не отправляется), строит одноразовую списочную базу, составляет план и печатает
   `PLAN SHA256`. Эта строка появляется в консоли **раньше**, чем запрошен первый
   V4, — она и есть доказательство, что предсказания зафиксированы до раскрытия.
4. Отправляет вылеты периода на площадку (`kind=backfill`, дубликаты приёмник
   пропускает): без них серверный пересчёт сентября не увидит.
5. Посещает записи из `capture_ids.txt` и отправляет их источники на площадку.

Повторный запуск безопасен: план не перезаписывается (инструмент отказывает),
уже собранные записи сборщик не посещает.

### Коды сборщика, которые здесь возможны

| Код | Что значит | Что делать |
|---|---|---|
| 2 | сессии нет или она истекла | сохранить сессию заново, запустить блок снова |
| 18 | часть записей не дала полного набора | запустить блок снова |
| 19 | площадка не приняла пакет | прислать вывод; проверить, что площадка запущена |

### На следующее утро

Убедиться, что ночной сбор production отработал как обычно (журнал
синхронизации дронов в приложении). Вторая сессия того же аккаунта DJI 09.09
проблем не создавала, но проверить — одна минута.

## Ручной шаг между блоками

Скопировать с рабочей машины `C:\VehicleSoft_Holdout\plan\plan.json` на сервер в
`C:\VehicleSoft_Holdout_Staging\plan.json`. Блок S без этого файла не начнётся и
ничего не изменит.

## Блок S — SRV-YOQSH, площадка

Делает запись в базу площадки: пересчёт сентября (append-only строки расчётов).
Перед записью — резервная копия вместе с `-wal` и `-shm`, путь печатается.
Служба площадки остановлена всю доказательную часть и поднимается в `finally`.

```powershell
& {
$ErrorActionPreference = 'Continue'
$staging = 'C:\transport-report-staging'
$db      = 'C:\transport-report-staging\instance\transport.db'
$service = 'TransportReportStaging'
$base    = 'C:\VehicleSoft_Holdout_Staging'
$src     = 'C:\VehicleSoft_Holdout_Staging\src'
$planIn  = 'C:\VehicleSoft_Holdout_Staging\plan.json'
$out     = 'C:\VehicleSoft_Holdout_Staging\report'
$recalc  = 'C:\VehicleSoft_Holdout_Staging\recalc'
$backup  = 'C:\transport-report-staging\backups\dji-area'
$py      = 'C:\Program Files\Python314\python.exe'
$branch  = 'claude/dji-area-simplify-001'
if ($staging -notlike '*transport-report-staging*') { throw "STEP FAILED: refusing a root that is not the staging checkout" }
if ($db -notlike '*transport-report-staging*') { throw "STEP FAILED: refusing a database outside the staging checkout" }
if ($service -ne 'TransportReportStaging') { throw "STEP FAILED: refusing a service that is not the staging service" }
if (-not (Test-Path -LiteralPath $db)) { throw "STEP FAILED: database not found: $db" }
if (-not (Test-Path -LiteralPath $py)) { throw "STEP FAILED: python not found: $py" }
if (-not (Test-Path -LiteralPath $planIn)) { throw "STEP FAILED: $planIn not found -- copy plan.json from the workstation first; nothing was changed" }
$origin = (& git -C $staging config --get remote.origin.url)
if (-not $origin) { throw "STEP FAILED: cannot read origin url from $staging" }
if (Test-Path -LiteralPath $src) { Remove-Item -LiteralPath $src -Recurse -Force }
if (Test-Path -LiteralPath $out) { Remove-Item -LiteralPath $out -Recurse -Force }
if (Test-Path -LiteralPath $recalc) { Remove-Item -LiteralPath $recalc -Recurse -Force }
& git clone --branch $branch --depth 1 --quiet $origin $src
if ($LASTEXITCODE -ne 0) { throw "STEP FAILED: git clone exit $LASTEXITCODE" }
& git -C $src --no-pager log --oneline -1
Set-Location $src
& $py tools\test_dji_area_holdout.py
if ($LASTEXITCODE -ne 0) { throw "STEP FAILED: holdout tool self-test exit $LASTEXITCODE" }
& $py tools\test_dji_area_idempotence_gate.py
if ($LASTEXITCODE -ne 0) { throw "STEP FAILED: idempotence gate self-test exit $LASTEXITCODE" }
$locked = (Get-Content -LiteralPath $planIn -Raw | ConvertFrom-Json)
$from = $locked.locked.period.from
$to = $locked.locked.period.to
if (-not $from) { throw "STEP FAILED: the plan carries no period start" }
if (-not $to) { throw "STEP FAILED: the plan carries no period end" }
Write-Host ("PLAN SHA256: " + $locked.locked_sha256)
Write-Host ("PLAN PERIOD: $from .. $to")
$stamp = Get-Date -Format 'yyyyMMdd_HHmmss'
New-Item -ItemType Directory -Force -Path $backup | Out-Null
New-Item -ItemType Directory -Force -Path $recalc | Out-Null
$rc = -1
try {
  Stop-Service -Name $service
  $deadline = (Get-Date).AddSeconds(90)
  while ((Get-Service -Name $service).Status -ne 'Stopped') {
    if ((Get-Date) -gt $deadline) { throw "STEP FAILED: service did not reach Stopped within 90s" }
    Start-Sleep -Seconds 2
  }
  Write-Host ("SERVICE STOPPED: " + (Get-Service -Name $service).Status)
  $dest = Join-Path $backup ("transport.db.pre_holdout_" + $stamp + ".bak")
  Copy-Item -LiteralPath $db -Destination $dest -Force
  foreach ($sfx in @('-wal','-shm')) { if (Test-Path -LiteralPath ($db + $sfx)) { Copy-Item -LiteralPath ($db + $sfx) -Destination ($dest + $sfx) -Force } }
  if (-not (Test-Path -LiteralPath $dest)) { throw "STEP FAILED: backup was not created" }
  Write-Host ("BACKUP: " + $dest + "  " + (Get-Item -LiteralPath $dest).Length + " bytes")
  & $py tools\dji_area_recalc.py --db $db --from $from --to $to --dry-run --json (Join-Path $recalc 'dryrun.json')
  if ($LASTEXITCODE -ne 0) { throw "STEP FAILED: recalc dry-run exit $LASTEXITCODE" }
  & $py tools\dji_area_recalc.py --db $db --from $from --to $to --apply --json (Join-Path $recalc 'apply1.json')
  if ($LASTEXITCODE -ne 0) { throw "STEP FAILED: recalc apply exit $LASTEXITCODE" }
  & $py tools\dji_area_recalc.py --db $db --from $from --to $to --apply --json (Join-Path $recalc 'apply2.json')
  if ($LASTEXITCODE -ne 0) { throw "STEP FAILED: recalc second apply exit $LASTEXITCODE" }
  & $py tools\dji_area_idempotence_gate.py --summary (Join-Path $recalc 'apply2.json')
  if ($LASTEXITCODE -ne 0) { throw "STEP FAILED: idempotence gate refused the second apply, exit $LASTEXITCODE" }
  $before = (Get-FileHash -LiteralPath $db -Algorithm SHA256).Hash
  Write-Host "DB SHA256 BEFORE: $before"
  & $py tools\dji_area_holdout.py report --db $db --plan $planIn --out $out
  $rc = $LASTEXITCODE
  Write-Host "HOLDOUT EXIT CODE: $rc  (0 PASS, 3 FAIL, 5 INCONCLUSIVE are all valid results)"
  $after = (Get-FileHash -LiteralPath $db -Algorithm SHA256).Hash
  Write-Host "DB SHA256 AFTER : $after"
  if ($before -ne $after) { throw "STOP: the database changed during a read-only run" }
  Write-Host "READ-ONLY CONFIRMED: database bytes identical"
} finally {
  Restart-Service -Name $service
  $deadline2 = (Get-Date).AddSeconds(90)
  while ((Get-Service -Name $service).Status -ne 'Running') {
    if ((Get-Date) -gt $deadline2) { throw "STEP FAILED: service did not reach Running within 90s -- START IT BY HAND" }
    Start-Sleep -Seconds 2
  }
  Write-Host ("SERVICE RUNNING: " + (Get-Service -Name $service).Status)
}
if (($rc -ne 0) -and ($rc -ne 3) -and ($rc -ne 5)) { throw "STEP FAILED: holdout report exit $rc -- no verdict was produced" }
Copy-Item -LiteralPath (Join-Path $recalc 'dryrun.json') -Destination $out -Force
Copy-Item -LiteralPath (Join-Path $recalc 'apply1.json') -Destination $out -Force
Copy-Item -LiteralPath (Join-Path $recalc 'apply2.json') -Destination $out -Force
Get-ChildItem -LiteralPath $out | Select-Object Name, Length | Format-Table -AutoSize
Compress-Archive -Path "$out\*" -DestinationPath 'C:\VehicleSoft_Holdout_Staging\holdout_report.zip' -Force
Write-Host 'SEND BACK: C:\VehicleSoft_Holdout_Staging\holdout_report.zip and the console text above'
}
```

Блоку S нужна ветка на GitHub: он клонирует её по адресу `origin` площадки, как
это делал блок этапа 1B.

## Что прислать обратно

`holdout_plan.zip` с рабочей машины, `holdout_report.zip` с сервера и полный
текст обеих консолей. В выводе должны быть: `RECEIVER CHECK`, `PLAN SHA256` (одно
и то же значение в обоих блоках), путь резервной копии, слово `unchanged` во
второй сводке пересчёта, обе строки `DB SHA256` и `HOLDOUT EXIT CODE`.

## Если блок остановился

- `refusing ...` — сработала защита от production. Ничего не выполнялось.
- `.env not found` / `receiver ... is not the staging port` — сбор не начинался.
- `self-test exit ...` — окружение не проходит самотест; базы не трогались.
- `list walk (dry-run) exit 2` — нет сессии DJI; см. подготовку.
- `plan exit 1` с текстом `no complete report day` — в списке нет ни одного
  полного дня сентября; прислать вывод.
- `holdout report exit 4` — план или замороженный код изменены после фиксации.
  Это находка, а не сбой: прислать вывод как есть.
- `idempotence gate refused ...` — второй пересчёт написал новые строки; прислать
  `apply1.json`, `apply2.json` и вывод ворот.
- `service did not reach Running` — **поднять службу площадки вручную** и сообщить.

## Откат

Блок W пишет на площадку только через её приёмники: вылеты (дубликаты
пропускаются) и неизменяемые ревизии источников. Блок S добавляет append-only
строки расчётов. Вернуть базу площадки к состоянию до блока S: остановить службу,
скопировать `transport.db.pre_holdout_<метка>.bak` поверх `instance\transport.db`
(вместе с `-wal` и `-shm`, если они были), запустить службу.
