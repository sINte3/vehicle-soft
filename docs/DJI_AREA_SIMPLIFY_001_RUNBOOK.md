# DJI-AREA-SIMPLIFY-001 — слепой holdout сентября: блоки для владельца

Протокол и критерии зафиксированы заранее в
`docs/DJI_AREA_SIMPLIFY_001_HOLDOUT_PREREG.md`. Здесь — только исполнение.

**Production не участвует ни в одном шаге.** База и служба production не
называются ни в одном блоке; сборщик production (`DroneCollectorDaily`) и его
сессия не используются. Данные идут только на площадку (порт 5051), и блок
отказывается работать, если приёмник в `.env` — не она.

## Сентябрь уже пройден — что осталось

Слепой holdout 01.09–18.09.2026 **выполнен 19.09.2026**: 233 кандидата из 233
попаданий, опровержений нет, нижняя граница 98,72 %, контрольные ворота PASS,
общий вердикт PASS. Переигрывать его нельзя и не нужно.

Тот же прогон вскрыл два дефекта, которые к приёмке кандидатов отношения не
имеют, но делают сентябрьский ИТОГ неполным (`docs/DJI_AREA_SIMPLIFY_001_SEPTEMBER_FINDINGS.md`):

1. **идентичность машины** — исправлено в коде (impl-4);
2. **скаляры списка на площадке не разобраны** — тела целы, их надо перечитать.

Поэтому сейчас нужен **блок R**, и только он. Новых обращений к DJI не
требуется: всё, что нужно, уже лежит в неизменяемом хранилище площадки.

Блоки 0, W и S остаются инструкцией для СЛЕДУЮЩЕГО периода. Повторно
использовать сентябрьский `plan.json` нельзя: код изменился, и `report`
откажется (код 4) — так и задумано.

## Блоки

| # | Где | Что делает | Пишет |
|---|---|---|---|
| R | SRV-YOQSH, площадка | перечитать сохранённые тела в улики и пересчитать | в базу площадки, после резервной копии |
| 0 | рабочая машина | одноразовое сохранение сессии DJI из hotfix PR #127 | только файл состояния на диск |
| W | рабочая машина, где живёт сборщик | список периода → план (SHA-256) → адресный сбор V4 по плану | только в площадку |
| S | SRV-YOQSH, площадка | пересчёт периода и отчёт holdout | только в базу площадки, после резервной копии |

Блок 0 выполняется один раз и повторяется, только если сессия истекла. Между
W и S — один ручной шаг: скопировать `plan.json` с рабочей машины на сервер.

## Пин проверенной ревизии

Блоки W и S обязаны доказать, что работают на ревизии, которую смотрел ревьюер, —
**до** первого обращения к кабинету DJI и до первого обращения к базе площадки.
Имени ветки для этого недостаточно: ветка подвижна.

Точный SHA коммита внутрь самого этого коммита положить невозможно: вписывание
SHA в файл меняет содержимое коммита и, значит, его SHA. Поэтому пин двойной, и
ни одна его половина не ссылается сама на себя.

| Пин | Что доказывает | Почему не самореференция |
|---|---|---|
| Аннотированный тег `dji-area-productionization-001-rc1` | `git rev-parse HEAD` совпадает с коммитом, на который указывает тег: это ровно та ревизия целиком | тег создаётся **после** коммита и живёт отдельной ссылкой; в блоке записано только его ИМЯ |
| Отпечаток кода `$ExpectedFingerprint` | содержимое девяти файлов, которые считают вердикт, не разошлось с проверенным | отпечаток берётся по `FROZEN_FILES`, а этот файл в них не входит |

Тег отвечает на вопрос «та ли ревизия», отпечаток — на вопрос «не правили ли
рабочую копию после клонирования». Блок дополнительно требует чистый
`git status`.

Тройка «ветка, тег, отпечаток» переезжает вместе с кодом и всегда называет одну
ревизию. 21.09.2026 (DJI-AREA-PRODUCTIONIZATION-001) она переведена с
`claude/dji-area-simplify-001` / `dji-area-simplify-001-reviewed-5` /
`316dfd53…` на нынешние значения: в замороженном `dji_area/pipeline.py`
исправлена зависимость идентичности группы от окна пересчёта, и отпечаток
обязан был измениться. Сентябрьские результаты от этого не изменились (полный
пересчёт 01.09–18.09 на копии площадки — `unchanged=4623`); блок R и holdout
сентября исполнены на `reviewed-4` / `reviewed-5` и остаются историей этих тегов.

Отпечаток печатает сам инструмент:

```powershell
& 'C:\Program Files\Python314\python.exe' tools\dji_area_holdout.py fingerprint
```

Самотест ранбука сверяет число, записанное в блоках, с настоящим отпечатком
кода. Если кто-то правит замороженный файл и забывает про ранбук, проверка
падает в CI, а не на сервере.

## Блок R — площадка: вернуть скаляры списка и пересчитать

Приёмник площадки развёрнут на ревизии от 08.09.2026, а она разбирает ревизию
списка как ОДНУ запись. Живой захват кладёт целую страницу ответа DJI, поэтому
разбор падал на каждом вылете: строка улик получала `list_revision_id`, а
скаляры оставались пустыми. Проверено на 300 из 300 настоящих тел.

Блок ничего не собирает: он перечитывает уже сохранённые байты нынешним кодом.
Обращений к кабинету DJI в нём нет вовсе.

```powershell
& {
$ErrorActionPreference = 'Continue'
$staging = 'C:\transport-report-staging'
$db      = 'C:\transport-report-staging\instance\transport.db'
$service = 'TransportReportStaging'
$src     = 'C:\VehicleSoft_Holdout_Staging\src_r'
$out     = 'C:\VehicleSoft_Holdout_Staging\reparse'
$backup  = 'C:\transport-report-staging\backups\dji-area'
$py      = 'C:\Program Files\Python314\python.exe'
$branch  = 'claude/dji-area-productionization-001'
$ExpectedTag = 'dji-area-productionization-001-rc1'
$ExpectedFingerprint = '7c01a13148410b494d725984b2725618f3ef096535e52dfdd75305eb3d10edb6'
$from    = '2026-09-01'
$to      = '2026-09-18'
if ($staging -notlike '*transport-report-staging*') { throw "STEP FAILED: refusing a root that is not the staging checkout" }
if ($db -notlike '*transport-report-staging*') { throw "STEP FAILED: refusing a database outside the staging checkout" }
if ($service -ne 'TransportReportStaging') { throw "STEP FAILED: refusing a service that is not the staging service" }
if (-not (Test-Path -LiteralPath $db)) { throw "STEP FAILED: database not found: $db" }
if (-not (Test-Path -LiteralPath $py)) { throw "STEP FAILED: python not found: $py" }
$origin = (& git -C $staging config --get remote.origin.url)
if (-not $origin) { throw "STEP FAILED: cannot read origin url from $staging" }
if (Test-Path -LiteralPath $src) { Remove-Item -LiteralPath $src -Recurse -Force }
if (Test-Path -LiteralPath $out) { Remove-Item -LiteralPath $out -Recurse -Force }
& git clone --branch $branch --quiet $origin $src
if ($LASTEXITCODE -ne 0) { throw "STEP FAILED: git clone exit $LASTEXITCODE" }
& git -C $src --no-pager log --oneline -1
$pinned = (& git -C $src rev-parse --verify --quiet "$ExpectedTag^{commit}")
if (-not $pinned) { throw "STEP FAILED: tag $ExpectedTag is not in $src -- fetch it: git -C $src fetch --tags" }
$headSha = (& git -C $src rev-parse HEAD)
if ($headSha -ne $pinned) { throw "STEP FAILED: HEAD is $headSha, the reviewed revision is $pinned" }
$dirty = @(& git -C $src status --porcelain)
if ($dirty.Count -gt 0) { throw "STEP FAILED: the clone has local modifications -- refusing to run an unreviewed working tree" }
Write-Host "REVISION PIN: $headSha"
Set-Location $src
$fpFound = @(& $py tools\dji_area_holdout.py fingerprint | Select-String -Pattern '^\s*CODE FINGERPRINT\s*:\s*([0-9a-f]{64})\s*$' | ForEach-Object { $_.Matches[0].Groups[1].Value })
if ($fpFound.Count -ne 1) { throw "STEP FAILED: expected exactly one CODE FINGERPRINT line, got $($fpFound.Count)" }
$fp = $fpFound[0]
if ($fp -ne $ExpectedFingerprint) { throw "STEP FAILED: code fingerprint is $fp, the reviewed one is $ExpectedFingerprint" }
Write-Host "CODE FINGERPRINT: $fp"
& $py tools\test_dji_area_reparse_evidence.py
if ($LASTEXITCODE -ne 0) { throw "STEP FAILED: reparse self-test exit $LASTEXITCODE" }
& $py -m unittest tests.test_dji_area_identity_001
if ($LASTEXITCODE -ne 0) { throw "STEP FAILED: identity self-test exit $LASTEXITCODE" }
New-Item -ItemType Directory -Force -Path $backup | Out-Null
New-Item -ItemType Directory -Force -Path $out | Out-Null
$stamp = Get-Date -Format 'yyyyMMdd_HHmmss'
try {
  Stop-Service -Name $service
  $deadline = (Get-Date).AddSeconds(90)
  while ((Get-Service -Name $service).Status -ne 'Stopped') {
    if ((Get-Date) -gt $deadline) { throw "STEP FAILED: service did not reach Stopped within 90s" }
    Start-Sleep -Seconds 2
  }
  Write-Host ("SERVICE STOPPED: " + (Get-Service -Name $service).Status)
  $dest = Join-Path $backup ("transport.db.pre_reparse_" + $stamp + ".bak")
  Copy-Item -LiteralPath $db -Destination $dest -Force
  foreach ($sfx in @('-wal','-shm')) { if (Test-Path -LiteralPath ($db + $sfx)) { Copy-Item -LiteralPath ($db + $sfx) -Destination ($dest + $sfx) -Force } }
  if (-not (Test-Path -LiteralPath $dest)) { throw "STEP FAILED: backup was not created" }
  Write-Host ("BACKUP: " + $dest + "  " + (Get-Item -LiteralPath $dest).Length + " bytes")
  & $py tools\dji_area_reparse_evidence.py --db $db --from $from --to $to --dry-run
  if ($LASTEXITCODE -ne 0) { throw "STEP FAILED: reparse dry-run exit $LASTEXITCODE" }
  & $py tools\dji_area_reparse_evidence.py --db $db --from $from --to $to --apply
  if ($LASTEXITCODE -ne 0) { throw "STEP FAILED: reparse apply exit $LASTEXITCODE" }
  & $py tools\dji_area_reparse_evidence.py --db $db --from $from --to $to --apply
  if ($LASTEXITCODE -ne 0) { throw "STEP FAILED: reparse second apply exit $LASTEXITCODE" }
  & $py tools\dji_area_recalc.py --db $db --from $from --to $to --apply --json (Join-Path $out 'recalc1.json')
  if ($LASTEXITCODE -ne 0) { throw "STEP FAILED: recalc apply exit $LASTEXITCODE" }
  & $py tools\dji_area_recalc.py --db $db --from $from --to $to --apply --json (Join-Path $out 'recalc2.json')
  if ($LASTEXITCODE -ne 0) { throw "STEP FAILED: recalc second apply exit $LASTEXITCODE" }
  & $py tools\dji_area_idempotence_gate.py --summary (Join-Path $out 'recalc2.json')
  if ($LASTEXITCODE -ne 0) { throw "STEP FAILED: idempotence gate refused the second apply, exit $LASTEXITCODE" }
} finally {
  Restart-Service -Name $service
  $deadline2 = (Get-Date).AddSeconds(90)
  while ((Get-Service -Name $service).Status -ne 'Running') {
    if ((Get-Date) -gt $deadline2) { throw "STEP FAILED: service did not reach Running within 90s -- START IT BY HAND" }
    Start-Sleep -Seconds 2
  }
  Write-Host ("SERVICE RUNNING: " + (Get-Service -Name $service).Status)
}
Get-ChildItem -LiteralPath $out | Select-Object Name, Length | Format-Table -AutoSize
Compress-Archive -Path "$out\*" -DestinationPath 'C:\VehicleSoft_Holdout_Staging\reparse.zip' -Force
Write-Host 'SEND BACK: C:\VehicleSoft_Holdout_Staging\reparse.zip and the console text above'
}
```

### Контрольные числа блока R

**Восстановлено будет 4623, а не 3980.** Числа 3980 и 4623 отвечают на разные
вопросы, и их легко перепутать:

- **3980** -- это `raw_missing_records`: записи, у которых площадь не известна
  ВООБЩЕ;
- **4623** -- это записи периода, у которых отсутствуют **скаляры списка**. Их
  все: разбор страницы падал одинаково на каждом вылете;
- **643** -- разница. У этих записей скаляры списка тоже отсутствовали, но
  площадь пришла запасным путём `card_raw_area_m2`, поэтому в
  `raw_missing_records` они не попали.

Пересборка чинит именно скаляры списка, поэтому её итог -- 4623.

Два инварианта, которые обязаны выполниться ТОЧНО, какими бы ни были
абсолютные числа на площадке:

| Инвариант | Где смотреть |
|---|---|
| `list scalars recovered` == `list scalars missing before` | первый `--apply` |
| `list scalars lost` == 0 во всех трёх прогонах | иначе код возврата 3 и блок останавливается сам |
| второй `--apply`: `rows changed : 0` И `rows rewritten physically : 0` | идемпотентность |
| `calc writes` второго пересчёта -- только `unchanged` | ворота идемпотентности |

**Второй `--apply` обязан не менять базу ФИЗИЧЕСКИ.** Живой прогон 20.09.2026
показал, что одного `rows changed : 0` мало: строка улик переписывалась целиком
вместе с новым `updated_at`, и SHA файла базы менялся при нулевой содержательной
работе (`UPDATED_AT_DIFFERENCES=4623`, `NON_TIMESTAMP_MISMATCHES=0`). Теперь
каждая строка пересобирается внутри точки сохранения и при совпадении всех
содержательных полей откатывается, а прогон, не переписавший ни одной строки,
не коммитится вовсе -- иначе пустой `COMMIT` поднял бы счётчик изменений в
заголовке файла. Проверяемо прямо на площадке:

| Что проверить после второго `--apply` | Ожидание |
|---|---|
| `rows rewritten physically` | `0` |
| SHA-256 файла базы до и после | совпадают |
| `updated_at` в `dji_flight_evidence` | не изменился ни у одной строки |

**Если `list scalars lost` не ноль, восстанавливать из копии не нужно.** Запись
атомарна: весь прогон идёт одной транзакцией, решение записывать принимается
ПОСЛЕ подсчёта потерь, и при любой потере выполняется `ROLLBACK`. В выводе будет
строка `ROLLED BACK`, база останется побайтово прежней, а код возврата 3 не даст
стартовать пересчёту. Резервная копия выше остаётся страховкой от того, чего эта
гарантия не покрывает, -- от сбоя питания и от ошибки оператора.

Ожидаемые значения (реплика площадки, собранная из тех же конвертов):

| Показатель | До | После |
|---|---|---|
| `flights with stored sources` | -- | 4623 |
| `list scalars missing before` (первый прогон) | -- | 4623 |
| `list scalars recovered` (первый `--apply`) | -- | 4623 |
| `list scalars missing before` (второй прогон) | -- | 0 |
| `rows rewritten physically` (первый `--apply`) | -- | 4623 |
| `rows changed` (второй `--apply`) | -- | 0 |
| `rows rewritten physically` (второй `--apply`) | -- | 0 |
| `raw missing` в сводке пересчёта | 3980 | 0 |
| `raw sum m2` | 5 210 833 (521,08 га) | 44 132 825 (4413,28 га) |
| `structural cand.` | 0 | 233 |
| `calc writes` | `new` | `unchanged` |

**Оговорка о точности.** Реплика собрана по дампу списка, снятому более ранним
проходом: в ней 4838 вылетов против 4861 у площадки. Поэтому абсолютные числа
площадки могут отличаться на пару десятков записей и в четвёртом знаке
гектаров. Инварианты выше от этого не зависят и обязаны выполниться точно;
если `recovered` окажется заметно меньше, чем `missing before`, прогон
неуспешен независимо от того, что напечатано в остальных строках.

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
   **площадки** в `VEHICLE_SOFT_BASE_URL` (порт 5051), токеном площадки в
   `DRONE_API_TOKEN` и строкой

   ```
   DJI_STORAGE_STATE=C:\VehicleSoft_Holdout\session\storage_state.json
   ```

   Блок проверяет только порт приёмника; значение токена он не печатает.
3. Сохраните сессию **из hotfix PR #127**, а не из этой ветки (см. ниже).

### Почему сессия сохраняется не отсюда

`drone_collector/session.py` в ветке holdout — заведомо старый: в нём тот самый
дефект `--save-session`, который чинит PR #127. Запускать его значило бы начинать
с известно сломанного пути.

Поэтому сессия создаётся отдельной временной копией на точном коммите PR #127
`b1c57ab3b99e22e4ecf4a68de4d1057ec7c3d8db` и передаётся holdout-сборщику файлом
через `DJI_STORAGE_STATE`. **PR #127 в эту ветку не мержится и не
cherry-pick-ается**: код holdout остаётся тем, что смотрел ревьюер, а из hotfix
берётся только результат его работы — `storage_state.json`.

Блок ниже одноразовый: он ставит временную копию, открывает браузер, ждёт вашего
входа руками и сохраняет состояние в общий каталог. Он ничего не собирает, никуда
не отправляет и к площадке не обращается.

```powershell
& {
$ErrorActionPreference = 'Continue'
$review  = 'C:\VehicleSoft_DJI_Review_20260918'
$tmp     = 'C:\VehicleSoft_Holdout\session_src'
$state   = 'C:\VehicleSoft_Holdout\session\storage_state.json'
$sha     = 'b1c57ab3b99e22e4ecf4a68de4d1057ec7c3d8db'
$py      = 'C:\Program Files\Python314\python.exe'
if (-not (Test-Path -LiteralPath $py)) { throw "STEP FAILED: python not found: $py" }
if (-not (Test-Path -LiteralPath $review)) { throw "STEP FAILED: review clone not found: $review" }
if (Test-Path -LiteralPath $tmp) { Remove-Item -LiteralPath $tmp -Recurse -Force }
& git clone --quiet $review $tmp
if ($LASTEXITCODE -ne 0) { throw "STEP FAILED: git clone exit $LASTEXITCODE" }
& git -C $tmp fetch --quiet origin $sha
if ($LASTEXITCODE -ne 0) { throw "STEP FAILED: the PR #127 commit is not reachable from this clone; fetch it first" }
& git -C $tmp checkout --quiet --detach $sha
if ($LASTEXITCODE -ne 0) { throw "STEP FAILED: git checkout $sha exit $LASTEXITCODE" }
$at = (& git -C $tmp rev-parse HEAD)
if ($at -ne $sha) { throw "STEP FAILED: the temporary copy is at $at, expected $sha" }
Write-Host "SESSION SOURCE: PR127 $at"
New-Item -ItemType Directory -Force -Path (Split-Path -Parent $state) | Out-Null
$env:DJI_STORAGE_STATE = $state
Set-Location $tmp
& $py -m drone_collector.main --save-session
if ($LASTEXITCODE -ne 0) { throw "STEP FAILED: --save-session exit $LASTEXITCODE" }
if (-not (Test-Path -LiteralPath $state)) { throw "STEP FAILED: $state was not written" }
Write-Host ("SESSION SAVED: " + $state + "  " + (Get-Item -LiteralPath $state).Length + " bytes")
Write-Host 'Now set DJI_STORAGE_STATE to that path in the holdout .env and run block W'
}
```

Временную копию `C:\VehicleSoft_Holdout\session_src` после этого можно удалить:
нужен только файл состояния.

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
$branch  = 'claude/dji-area-productionization-001'
$ExpectedTag = 'dji-area-productionization-001-rc1'
$ExpectedFingerprint = '7c01a13148410b494d725984b2725618f3ef096535e52dfdd75305eb3d10edb6'
if (-not (Test-Path -LiteralPath $py)) { throw "STEP FAILED: python not found: $py" }
if (-not (Test-Path -LiteralPath $review)) { throw "STEP FAILED: review clone not found: $review" }
if (-not (Test-Path -LiteralPath $work)) { New-Item -ItemType Directory -Force -Path $work | Out-Null }
if (-not (Test-Path -LiteralPath $src)) { & git clone --branch $branch --quiet $review $src }
if (-not (Test-Path -LiteralPath $src)) { throw "STEP FAILED: git clone did not create $src" }
& git -C $src --no-pager log --oneline -1
$head = (& git -C $src rev-parse --abbrev-ref HEAD)
if ($head -ne $branch) { throw "STEP FAILED: $src is on $head, expected $branch" }
$pinned = (& git -C $src rev-parse --verify --quiet "$ExpectedTag^{commit}")
if (-not $pinned) { throw "STEP FAILED: tag $ExpectedTag is not in $src -- fetch it: git -C $src fetch --tags" }
$headSha = (& git -C $src rev-parse HEAD)
if ($headSha -ne $pinned) { throw "STEP FAILED: HEAD is $headSha, the reviewed revision is $pinned" }
$dirty = @(& git -C $src status --porcelain)
if ($dirty.Count -gt 0) { throw "STEP FAILED: the clone has local modifications -- refusing to run an unreviewed working tree" }
Write-Host "REVISION PIN: $headSha"
if (-not (Test-Path -LiteralPath $envFile)) { throw "STEP FAILED: $envFile not found -- see the preparation section of the runbook; nothing was collected" }
$urlLines = @(Select-String -LiteralPath $envFile -Pattern '^\s*VEHICLE_SOFT_BASE_URL\s*=')
if ($urlLines.Count -eq 0) { throw "STEP FAILED: VEHICLE_SOFT_BASE_URL is not set in $envFile" }
$notStaging = @($urlLines | Where-Object { $_.Line -notmatch ':5051' })
if ($notStaging.Count -gt 0) { throw "STEP FAILED: a receiver line in .env is not the staging port 5051 -- refusing to send anywhere else" }
if ($env:VEHICLE_SOFT_BASE_URL -and ($env:VEHICLE_SOFT_BASE_URL -notmatch ':5051')) { throw "STEP FAILED: the process environment overrides the receiver with a non-staging address" }
Write-Host 'RECEIVER CHECK: staging port 5051'
Set-Location $src
$fpFound = @(& $py tools\dji_area_holdout.py fingerprint | Select-String -Pattern '^\s*CODE FINGERPRINT\s*:\s*([0-9a-f]{64})\s*$' | ForEach-Object { $_.Matches[0].Groups[1].Value })
if ($fpFound.Count -ne 1) { throw "STEP FAILED: expected exactly one CODE FINGERPRINT line, got $($fpFound.Count)" }
$fp = $fpFound[0]
if ($fp -ne $ExpectedFingerprint) { throw "STEP FAILED: code fingerprint is $fp, the reviewed one is $ExpectedFingerprint" }
Write-Host "CODE FINGERPRINT: $fp"
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
$branch  = 'claude/dji-area-productionization-001'
$ExpectedTag = 'dji-area-productionization-001-rc1'
$ExpectedFingerprint = '7c01a13148410b494d725984b2725618f3ef096535e52dfdd75305eb3d10edb6'
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
& git clone --branch $branch --quiet $origin $src
if ($LASTEXITCODE -ne 0) { throw "STEP FAILED: git clone exit $LASTEXITCODE" }
& git -C $src --no-pager log --oneline -1
$pinned = (& git -C $src rev-parse --verify --quiet "$ExpectedTag^{commit}")
if (-not $pinned) { throw "STEP FAILED: tag $ExpectedTag is not in $src -- fetch it: git -C $src fetch --tags" }
$headSha = (& git -C $src rev-parse HEAD)
if ($headSha -ne $pinned) { throw "STEP FAILED: HEAD is $headSha, the reviewed revision is $pinned" }
$dirty = @(& git -C $src status --porcelain)
if ($dirty.Count -gt 0) { throw "STEP FAILED: the clone has local modifications -- refusing to run an unreviewed working tree" }
Write-Host "REVISION PIN: $headSha"
Set-Location $src
$fpFound = @(& $py tools\dji_area_holdout.py fingerprint | Select-String -Pattern '^\s*CODE FINGERPRINT\s*:\s*([0-9a-f]{64})\s*$' | ForEach-Object { $_.Matches[0].Groups[1].Value })
if ($fpFound.Count -ne 1) { throw "STEP FAILED: expected exactly one CODE FINGERPRINT line, got $($fpFound.Count)" }
$fp = $fpFound[0]
if ($fp -ne $ExpectedFingerprint) { throw "STEP FAILED: code fingerprint is $fp, the reviewed one is $ExpectedFingerprint" }
Write-Host "CODE FINGERPRINT: $fp"
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
текст обеих консолей. В выводе должны быть: `REVISION PIN` и `CODE FINGERPRINT`
(одинаковые в обоих блоках), `RECEIVER CHECK`, `PLAN SHA256` (одно и то же
значение в обоих блоках), путь резервной копии, слово `unchanged` во второй
сводке пересчёта, обе строки `DB SHA256` и `HOLDOUT EXIT CODE`.

`HOLDOUT EXIT CODE` читается так: 0 — `PASS`; 3 — `FAIL` (либо приёмка
кандидатов, либо контрольные ворота: причина названа строкой выше); 5 —
`INCONCLUSIVE`; 4 — план или замороженный код изменены после фиксации.

## Если блок остановился

- `refusing ...` — сработала защита от production. Ничего не выполнялось.
- `.env not found` / `receiver ... is not the staging port` — сбор не начинался.
- `self-test exit ...` — окружение не проходит самотест; базы не трогались.
- `list walk (dry-run) exit 2` — нет сессии DJI; см. подготовку.
- `plan exit 1` с текстом `no complete report day` — в списке нет ни одного
  полного дня сентября; прислать вывод.
- `tag ... is not in` — клон без тегов. `git -C <клон> fetch --tags` и повторить.
- `HEAD is ..., the reviewed revision is ...` — ветка ушла вперёд после ревью.
  Остановиться и сообщить оба SHA: запускать непроверенную ревизию нельзя.
- `code fingerprint is ...` — рабочая копия правлена после клонирования.
- `holdout report exit 4` — план или замороженный код (включая сам инструмент)
  изменены после фиксации. Это находка, а не сбой: прислать вывод как есть.
- `idempotence gate refused ...` — второй пересчёт написал новые строки; прислать
  `apply1.json`, `apply2.json` и вывод ворот.
- `service did not reach Running` — **поднять службу площадки вручную** и сообщить.

## Откат

Блок W пишет на площадку только через её приёмники: вылеты (дубликаты
пропускаются) и неизменяемые ревизии источников. Блок S добавляет append-only
строки расчётов. Вернуть базу площадки к состоянию до блока S: остановить службу,
скопировать `transport.db.pre_holdout_<метка>.bak` поверх `instance\transport.db`
(вместе с `-wal` и `-shm`, если они были), запустить службу.
