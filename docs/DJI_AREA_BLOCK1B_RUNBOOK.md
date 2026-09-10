# DRONE-AREA этап 1B — один готовый блок для владельца

Делает два дела за один прогон:

1. **пересчёт площадки на текущую версию алгоритма** (`impl-3`) за 18.08.2026
   — это ЗАПИСЬ в базу площадки, разрешённая владельцем 10.09.2026;
2. **сбор evidence bundle** по дронам №5 и №6 — только чтение.

**Production не участвует.** Блок отказывается работать, если путь не
похож на площадку, и ни разу не называет ни путь, ни службу прода.

**Перед записью делается резервная копия базы** вместе с `-wal` и `-shm`, и
её путь печатается. Служба площадки останавливается на время записи и
перезапускается в `finally` — то есть даже если шаг упадёт, служба будет
поднята.

**Идемпотентность доказывается на месте:** пересчёт применяется дважды,
второй прогон обязан отчитаться `unchanged`. Если он отчитается иначе —
это находка, и её надо прислать.

**Чтение доказывается хешем:** вокруг шага сбора bundle печатается sha256
базы до и после; если они разошлись, блок останавливается сам.

## Что нужно прислать обратно

`C:\VehicleSoft_Block1B\block1b_out3.zip` и **полный текст вывода консоли**.
В выводе должны быть: путь резервной копии, обе сводки пересчёта, слово
`unchanged` во второй, обе строки sha256 и код возврата bundle.

## Блок

По одной команде на строку. В PowerShell нет `&&` — вставлять целиком,
ничего не редактируя.

```powershell
$ErrorActionPreference = 'Continue'
$staging = 'C:\transport-report-staging'
$db      = 'C:\transport-report-staging\instance\transport.db'
$service = 'TransportReportStaging'
$work    = 'C:\VehicleSoft_Block1B'
$out     = 'C:\VehicleSoft_Block1B\out3'
$recalc  = 'C:\VehicleSoft_Block1B\recalc'
$backup  = 'C:\transport-report-staging\backups\dji-area'
$py      = 'C:\Program Files\Python314\python.exe'
$branch  = 'claude/dji-agras-area-review-7sw9c1'
if ($staging -notlike '*transport-report-staging*') { throw "STEP FAILED: refusing a root that is not the staging checkout" }
if ($db -notlike '*transport-report-staging*') { throw "STEP FAILED: refusing a database outside the staging checkout" }
if ($service -ne 'TransportReportStaging') { throw "STEP FAILED: refusing a service that is not the staging service" }
if (-not (Test-Path -LiteralPath $db)) { throw "STEP FAILED: database not found: $db" }
if (-not (Test-Path -LiteralPath $py)) { throw "STEP FAILED: python not found: $py" }
$origin = (& git -C $staging config --get remote.origin.url)
if (-not $origin) { throw "STEP FAILED: cannot read origin url from $staging" }
if (Test-Path -LiteralPath $work) { Remove-Item -LiteralPath $work -Recurse -Force }
& git clone --branch $branch --depth 1 --quiet $origin $work
if ($LASTEXITCODE -ne 0) { throw "STEP FAILED: git clone exit $LASTEXITCODE" }
& git -C $work --no-pager log --oneline -1
Set-Location $work
& $py tools\test_dji_area_block1b.py
if ($LASTEXITCODE -ne 0) { throw "STEP FAILED: tool self-test exit $LASTEXITCODE" }
$stamp = Get-Date -Format 'yyyyMMdd_HHmmss'
New-Item -ItemType Directory -Force -Path $backup | Out-Null
New-Item -ItemType Directory -Force -Path $recalc | Out-Null
try {
  Stop-Service -Name $service
  Start-Sleep -Seconds 3
  $dest = Join-Path $backup ("transport.db.pre_impl3_" + $stamp + ".bak")
  Copy-Item -LiteralPath $db -Destination $dest -Force
  foreach ($sfx in @('-wal','-shm')) { if (Test-Path -LiteralPath ($db + $sfx)) { Copy-Item -LiteralPath ($db + $sfx) -Destination ($dest + $sfx) -Force } }
  if (-not (Test-Path -LiteralPath $dest)) { throw "STEP FAILED: backup was not created" }
  Write-Host ("BACKUP: " + $dest + "  " + (Get-Item -LiteralPath $dest).Length + " bytes")
  & $py tools\dji_area_recalc.py --db $db --from 2026-08-18 --to 2026-08-18 --dry-run --json (Join-Path $recalc 'dryrun.json')
  if ($LASTEXITCODE -ne 0) { throw "STEP FAILED: recalc dry-run exit $LASTEXITCODE" }
  & $py tools\dji_area_recalc.py --db $db --from 2026-08-18 --to 2026-08-18 --apply --json (Join-Path $recalc 'apply1.json')
  if ($LASTEXITCODE -ne 0) { throw "STEP FAILED: recalc apply exit $LASTEXITCODE" }
  Write-Host 'SECOND APPLY MUST REPORT unchanged:'
  & $py tools\dji_area_recalc.py --db $db --from 2026-08-18 --to 2026-08-18 --apply --json (Join-Path $recalc 'apply2.json')
  if ($LASTEXITCODE -ne 0) { throw "STEP FAILED: recalc second apply exit $LASTEXITCODE" }
} finally {
  Restart-Service -Name $service
  Start-Sleep -Seconds 5
  Get-Service -Name $service | Select-Object Name, Status | Format-Table -AutoSize
}
$before = (Get-FileHash -LiteralPath $db -Algorithm SHA256).Hash
Write-Host "DB SHA256 BEFORE: $before"
& $py tools\dji_area_block1b.py --db $db --date 2026-08-18 --hardware 1581F574B2387001009R --hardware 1581F574B235W00100Q5 --out $out
$rc = $LASTEXITCODE
Write-Host "BUNDLE EXIT CODE: $rc"
$after = (Get-FileHash -LiteralPath $db -Algorithm SHA256).Hash
Write-Host "DB SHA256 AFTER : $after"
if ($before -ne $after) { throw "STOP: the database changed during a read-only run" }
Write-Host "READ-ONLY CONFIRMED: database bytes identical"
Copy-Item -LiteralPath (Join-Path $recalc 'dryrun.json') -Destination $out -Force
Copy-Item -LiteralPath (Join-Path $recalc 'apply1.json') -Destination $out -Force
Copy-Item -LiteralPath (Join-Path $recalc 'apply2.json') -Destination $out -Force
Get-ChildItem -LiteralPath $out | Select-Object Name, Length | Format-Table -AutoSize
Compress-Archive -Path "$out\*" -DestinationPath 'C:\VehicleSoft_Block1B\block1b_out3.zip' -Force
Write-Host 'SEND BACK: C:\VehicleSoft_Block1B\block1b_out3.zip and the console text above'
```

## Что делать, если блок остановился

- `refusing a root/database/service ...` — сработала защита от прода.
  Ничего не выполнялось.
- `database not found` / `python not found` — прислать фактические пути.
- `git clone exit ...` — сервер не получил ветку. Прислать текст ошибки.
- `tool self-test exit ...` — окружение не проходит самотест инструмента;
  **база не трогалась**, резервная копия не делалась.
- `backup was not created` — запись не начиналась.
- `recalc ... exit ...` — пересчёт упал. База осталась с резервной копией
  рядом; служба перезапущена блоком. Прислать вывод.
- `exit 1` у bundle — на базе нет таблиц модели, либо каталог вывода занят.
- `exit 2` у bundle — база не найдена.
- `STOP: the database changed` — остановиться и сообщить.

## Откат

Служба останавливается и перезапускается блоком; отдельного отката не
требуется. Если понадобится вернуть базу до пересчёта: остановить службу,
скопировать `transport.db.pre_impl3_<метка>.bak` обратно поверх
`instance\transport.db` (вместе с `-wal`/`-shm`, если они были), запустить
службу. Пересчёт добавляет строки append-only и закрывает прежние
`superseded_at`, поэтому потери данных он не создаёт.

## Что даёт bundle

`V4_APPLICATION_COVERAGE_ESTIMATE` — оценку уникальной поверхности под
подтверждённым применением. Раздельно: уникальное покрытие, его доля внутри
исторического контура и вынос за него, повторное покрытие, холостой пролёт,
разложение длин по причинам, S против U, и неизменяемые тела
V4/маршрутов/геометрии с проверкой SHA/MD5.

Разбиение «внутри/снаружи» появляется ТОЛЬКО там, где историческая геометрия
контура доказана; иначе выводится причина, а не число.

Числом площади к счёту это **не является**. Соответствие записанной ширины
реальной полосе осаждения проверяется только полевой калибровкой (этап 2,
методика NY/T 3213—2023).
