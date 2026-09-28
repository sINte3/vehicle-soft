# DJI-AREA-RETAINED-FOOTPRINT-001 — выпуск на production: переход и устойчивое состояние

Один законченный порядок выпуска правила «перенесённый скаляр с пренебрежимым
следом» на production. Исполняет владелец; агент на серверах не исполняет
ничего. Правило и его доказательства — `docs/DJI_AREA_FOOTPRINT_CALIBRATION_001.md`
§8, приёмка — `tools/dji_area_control_acceptance.py`, оракул перехода —
`docs/DJI_AREA_SEPTEMBER_2026_RETAINED_FOOTPRINT_ORACLE.json`.

**Где.** Production, `SRV-YOQSH`: каталог `C:\transport-report`, база
`instance\transport.db`, сайт `http://10.103.25.14:5050`. Службы
`TransportReport`, `TransportBot`, `TransportBot003` останавливаются вместе,
как в шаге 2 `docs/RELEASE_AND_BACKUP_PROCEDURE.md`: иначе ни копия базы не
согласована, ни «база не изменилась» не доказуемо. Окно обслуживания —
несколько минут на блок.

**Чего здесь нет и не будет.** Обращения к DJI, исторического массового
backfill, решений администратора, изменения RAW, заполнения
`billable_area_m2`, применения «всего, что нашёл оценщик». Применяются только
названные строки; всё, что сверх них, — остановка.

## Почему переход, а не прежняя приёмка

Правило меняет результат сентября НАМЕРЕННО. В периоде оракула 01–18.09.2026
на production шесть записей группы B: пять переходят из проверки в доказанный
ноль (701661028, 702797709, 703830892, 703847599, 714181794; RAW 3,4106 га),
714181711 остаётся на проверке — след у неё крошечный, но структурного
совпадения нет, и именно структурный вентиль её держит.

Прежний оракул `docs/DJI_AREA_SEPTEMBER_2026_ORACLE.json` — запись алгоритма
ДО правила (слепой holdout 233/233 и живой блок R площадки 20.09.2026, 4623
записи). Он не меняется ни на байт и остаётся доказательством того, что до
правила эти записи оставались на проверке. Приёмка нового кода идёт по
новому оракулу в две явные фазы:

- **PRE-APPLY** — база ещё со строками прежнего кода. Сухой прогон периода с
  `--rows`; отчёт строится из строк базы, в которых решение заменено решением
  прогона, тем же `dji_area.control_report`, и совпадает с оракулом.
  Переписать прогон хочет РОВНО `expected_rewrites`, каждую — этим правилом и
  из «применение при плоском счётчике»; у остальных строк отпечаток и решение
  те же; RAW не меняется; billable пуст; 714181711 остаётся на проверке;
  отрицательные контрольные A2 не становятся фантомами; мостиков в
  корректировках нет; разбиение сходится. Ничего не пишется.
- **POST-APPLY** — после применения: отчёт по строкам базы совпадает с тем же
  оракулом; второй сухой прогон — `unchanged` по всем 4887 строкам и ничего
  больше; применение записало ровно названные строки новыми; те же проверки
  состояния.

`would_write > 0` само по себе не принимается никогда: так же выглядела бы и
регрессия. Доказательство — совпадение множества и причины.

## Порядок

0. **Предпосылки.** PR смержен, CI зелёный. Владелец создал аннотированный
   тег `dji-area-retained-footprint-001-rc1` на проверенном коммите и
   отправил его в origin. Без тега каждый блок останавливается на проверке
   ревизии, до первого действия.
1. **Блок R1 — до деплоя, только чтение.** Код — из стороннего клона тега.
   Стражи ревизии, тега и отпечатка; остановка служб; копия базы; снимок RAW;
   оценщик правила; сухой прогон сентября с `--rows`; PRE-APPLY; хеш базы до
   и после совпал — ничего не записано. PASS снимает строку
   `DJI-AREA-RETAINED-FOOTPRINT-001` в `docs/RELEASE_GATE.md`: проверка до
   деплоя пройдена, остальное — post-deploy QA этого же ранбука.
2. **Деплой `main`** по `docs/RELEASE_AND_BACKUP_PROCEDURE.md` — обычный
   порядок, не этот ранбук. Миграций у правила нет.
3. **Блок R2 — явное применение.** Код — развёрнутый. Стражи: HEAD production
   содержит тег, отпечаток замороженных файлов тот же. Остановка служб; копия;
   PRE-APPLY ещё раз (база ушла вперёд после R1); применение РОВНО
   `expected_rewrites` оракула (`--flight-id`); второй сухой прогон; POST-APPLY
   с `--apply-summary`; сторож RAW против снимка R1 (RAW и billable); службы
   подняты; дымовые проверки; отчёт периода — книга POST-APPLY.
4. **Блок R3 — отдельное решение владельца.** Исторические кандидаты вне
   сентября (23 из 28). Страж: свежий оценщик называет ровно утверждённый
   список 28.09.2026, иначе остановка до нового ревью. Сухой прогон по
   названным строкам обязан хотеть переписать ровно их; применение; второй
   сухой прогон `unchanged`; оценщик после; сторож RAW. Массового прохода нет.

## Ожидаемые числа (оракул перехода)

| Показатель | Ожидание |
|---|---|
| `records` / `raw_missing_records` | 4887 / 0 |
| `raw_ha` | 4687.1675 |
| `excluded_ha` / `excluded_records` | 175.8333 / 241 |
| `after_ha` | 4511.3342 |
| `pending_ha` / `pending_records` | 0 / 0 |
| `review_ha` / `review_records` | 4.7646 / 7 |
| `structural_candidates` / `chains_shown` | 240 / 240 |
| `proven_structural` / `proven_by_control_only` | 235 / 1 |
| `review_application_with_flat_counter` | 1 (714181711) |
| `bridges_excluded` | 0 |
| PRE-APPLY `calc_writes` | `{"unchanged": 4882, "would_write": 5}` |
| POST-APPLY второй прогон | `{"unchanged": 4887}` |
| применение | `{"new": 5}` |

Коды возврата: приёмка — 0 PASS, 1 ошибка аргументов, 2 базы нет, 3 FAIL;
оценщик — 0, 3 база изменилась во время чтения, 4 нарушен контроль; сторож RAW
— 0, 3 RAW изменён либо billable не пуст.

### Блок R1 — SRV-YOQSH, до деплоя: PRE-APPLY, только чтение

```powershell
& {
$ErrorActionPreference = 'Continue'
$expectedHost = 'srv-yoqsh'
$prod     = 'C:\transport-report'
$db       = 'C:\transport-report\instance\transport.db'
$services = @('TransportReport', 'TransportBot', 'TransportBot003')
$base     = 'C:\VehicleSoft_Retained_Footprint_Release'
$src      = 'C:\VehicleSoft_Retained_Footprint_Release\src'
$out      = 'C:\VehicleSoft_Retained_Footprint_Release\r1'
$rawSnap  = 'C:\VehicleSoft_Retained_Footprint_Release\raw_before.json'
$backup   = 'C:\transport-report\backups\dji-area'
$py       = 'C:\Program Files\Python314\python.exe'
$ExpectedTag = 'dji-area-retained-footprint-001-rc1'
$ExpectedFingerprint = '8bc0ecdf65311479e9b932ba6a5a6800a510a6be38ede70f31d7b0784dc38122'
$oracle   = 'docs\DJI_AREA_SEPTEMBER_2026_RETAINED_FOOTPRINT_ORACLE.json'
$from     = '2026-09-01'
$to       = '2026-09-18'
if ((hostname) -ne $expectedHost) { throw "STEP FAILED: host is $(hostname), expected $expectedHost" }
if ($prod -ne 'C:\transport-report') { throw "STEP FAILED: refusing a root that is not the production checkout" }
if ($db -ne (Join-Path $prod 'instance\transport.db')) { throw "STEP FAILED: refusing a database outside the production checkout" }
if (-not (Test-Path -LiteralPath $db)) { throw "STEP FAILED: database not found: $db" }
if (-not (Test-Path -LiteralPath $py)) { throw "STEP FAILED: python not found: $py" }
$missing = @($services | Where-Object { -not (Get-Service -Name $_ -ErrorAction SilentlyContinue) })
if ($missing.Count -gt 0) { throw "STEP FAILED: service(s) not found: $($missing -join ', ')" }
$origin = (& git -C $prod config --get remote.origin.url)
if (-not $origin) { throw "STEP FAILED: cannot read origin url from $prod" }
New-Item -ItemType Directory -Force -Path $base | Out-Null
if (Test-Path -LiteralPath $src) { Remove-Item -LiteralPath $src -Recurse -Force }
if (Test-Path -LiteralPath $out) { Remove-Item -LiteralPath $out -Recurse -Force }
& git clone --quiet --no-checkout $origin $src
if ($LASTEXITCODE -ne 0) { throw "STEP FAILED: git clone exit $LASTEXITCODE" }
& git -C $src fetch --quiet origin "refs/tags/${ExpectedTag}:refs/tags/${ExpectedTag}"
if ($LASTEXITCODE -ne 0) { throw "STEP FAILED: tag $ExpectedTag is not on origin -- the owner creates and pushes it after review" }
$pinned = (& git -C $src rev-parse --verify --quiet "$ExpectedTag^{commit}")
if (-not $pinned) { throw "STEP FAILED: tag $ExpectedTag does not resolve to a commit" }
& git -C $src checkout --quiet --detach $pinned
if ($LASTEXITCODE -ne 0) { throw "STEP FAILED: git checkout $pinned exit $LASTEXITCODE" }
$headSha = (& git -C $src rev-parse HEAD)
if ($headSha -ne $pinned) { throw "STEP FAILED: HEAD is $headSha, the reviewed revision is $pinned" }
$dirty = @(& git -C $src status --porcelain)
if ($dirty.Count -gt 0) { throw "STEP FAILED: the clone has local modifications -- refusing to run an unreviewed working tree" }
& git -C $src --no-pager log --oneline -1
Write-Host "REVISION PIN: $headSha"
Set-Location $src
$fpFound = @(& $py tools\dji_area_holdout.py fingerprint | Select-String -Pattern '^\s*CODE FINGERPRINT\s*:\s*([0-9a-f]{64})\s*$' | ForEach-Object { $_.Matches[0].Groups[1].Value })
if ($fpFound.Count -ne 1) { throw "STEP FAILED: expected exactly one CODE FINGERPRINT line, got $($fpFound.Count)" }
$fp = $fpFound[0]
if ($fp -ne $ExpectedFingerprint) { throw "STEP FAILED: code fingerprint is $fp, the reviewed one is $ExpectedFingerprint" }
Write-Host "CODE FINGERPRINT: $fp"
& $py -m compileall -q .
if ($LASTEXITCODE -ne 0) { throw "STEP FAILED: compileall exit $LASTEXITCODE" }
& $py -m unittest tests.test_dji_area_retained_footprint_001
if ($LASTEXITCODE -ne 0) { throw "STEP FAILED: rule self-test exit $LASTEXITCODE" }
& $py tools\test_dji_area_control_acceptance.py
if ($LASTEXITCODE -ne 0) { throw "STEP FAILED: acceptance tool self-test exit $LASTEXITCODE" }
& $py tools\test_dji_area_footprint_calibration.py
if ($LASTEXITCODE -ne 0) { throw "STEP FAILED: evaluator self-test exit $LASTEXITCODE" }
New-Item -ItemType Directory -Force -Path $backup | Out-Null
New-Item -ItemType Directory -Force -Path $out | Out-Null
$stamp = Get-Date -Format 'yyyyMMdd_HHmmss'
$ev = -1
$acc = -1
try {
  foreach ($name in $services) { Stop-Service -Name $name }
  $deadline = (Get-Date).AddSeconds(90)
  while (@($services | Where-Object { (Get-Service -Name $_).Status -ne 'Stopped' }).Count -gt 0) {
    if ((Get-Date) -gt $deadline) { throw "STEP FAILED: the services did not reach Stopped within 90s" }
    Start-Sleep -Seconds 2
  }
  Write-Host 'SERVICES STOPPED'
  & $py tools\check_db_lock.py --db $db
  $lock = $LASTEXITCODE
  if ($lock -eq 2) { throw "STEP FAILED: another process holds the database (exit 2)" }
  Write-Host "DB_LOCK_EXIT: $lock  (0 clean, 3 stale WAL is expected on this project)"
  $dest = Join-Path $backup ("transport.db.pre_retained_footprint_r1_" + $stamp + ".bak")
  Copy-Item -LiteralPath $db -Destination $dest -Force
  foreach ($sfx in @('-wal','-shm')) { if (Test-Path -LiteralPath ($db + $sfx)) { Copy-Item -LiteralPath ($db + $sfx) -Destination ($dest + $sfx) -Force } }
  if (-not (Test-Path -LiteralPath $dest)) { throw "STEP FAILED: backup was not created" }
  Write-Host ("BACKUP: " + $dest + "  " + (Get-Item -LiteralPath $dest).Length + " bytes")
  $before = (Get-FileHash -LiteralPath $db -Algorithm SHA256).Hash
  Write-Host "DB SHA256 BEFORE: $before"
  if (-not (Test-Path -LiteralPath $rawSnap)) { & $py tools\dji_area_raw_guard.py --db $db --save $rawSnap }
  if (-not (Test-Path -LiteralPath $rawSnap)) { throw "STEP FAILED: the RAW snapshot was not written" }
  & $py tools\dji_area_footprint_calibration.py --db $db --out (Join-Path $out 'evaluation') --evaluate-rule
  $ev = $LASTEXITCODE
  Write-Host "EVALUATOR EXIT CODE: $ev  (0 PASS, 4 a control violated)"
  & $py tools\dji_area_recalc.py --db $db --from $from --to $to --dry-run --quiet --json (Join-Path $out 'pre.json') --rows (Join-Path $out 'pre_rows.json')
  if ($LASTEXITCODE -ne 0) { throw "STEP FAILED: recalc dry-run exit $LASTEXITCODE" }
  & $py tools\dji_area_control_acceptance.py --db $db --oracle $oracle --phase pre-apply --recalc-summary (Join-Path $out 'pre.json') --recalc-rows (Join-Path $out 'pre_rows.json') --out (Join-Path $out 'acceptance')
  $acc = $LASTEXITCODE
  Write-Host "PRE-APPLY ACCEPTANCE EXIT CODE: $acc  (0 PASS, 3 FAIL)"
  $after = (Get-FileHash -LiteralPath $db -Algorithm SHA256).Hash
  Write-Host "DB SHA256 AFTER : $after"
  if ($before -ne $after) { throw "STOP: the database changed during a read-only run" }
  Write-Host 'READ-ONLY CONFIRMED: database bytes identical -- NOTHING WAS WRITTEN'
  if ($ev -ne 0) { throw "STOP: the retained-footprint evaluator did not pass (exit $ev) -- send back $out" }
  if ($acc -ne 0) { throw "STOP: PRE-APPLY acceptance did not pass (exit $acc) -- send back $out" }
} finally {
  foreach ($name in $services) { Restart-Service -Name $name }
  $deadline2 = (Get-Date).AddSeconds(90)
  while (@($services | Where-Object { (Get-Service -Name $_).Status -ne 'Running' }).Count -gt 0) {
    if ((Get-Date) -gt $deadline2) { throw "STEP FAILED: the services did not reach Running within 90s -- START THEM BY HAND" }
    Start-Sleep -Seconds 2
  }
  Write-Host 'SERVICES RUNNING'
}
Compress-Archive -Path "$out\*" -DestinationPath (Join-Path $base 'r1.zip') -Force
Write-Host 'PRE-APPLY PASS'
Write-Host 'SEND BACK: C:\VehicleSoft_Retained_Footprint_Release\r1.zip and the console text above'
}
```

Смотреть: `EVALUATOR EXIT CODE: 0` (28 кандидатов, контрольные в норме —
`evaluation\retained_footprint_evaluation.json`), в выводе приёмки
`rewrites of the dry run 5` с теми же пятью номерами, `must stay REVIEW
714181711`, `VERDICT: PASS`, `NOTHING WAS WRITTEN`. Любой провал — ничего не
записано; прислать `r1.zip`.

### Блок R2 — SRV-YOQSH, после деплоя `main`: явное применение и POST-APPLY

Вставляется только после PASS блока R1 и деплоя `main` на production. Если
база с R1 ушла вперёд так, что переход перестал быть ровно названным, блок
остановится на PRE-APPLY до первой записи.

```powershell
& {
$ErrorActionPreference = 'Continue'
$expectedHost = 'srv-yoqsh'
$prod     = 'C:\transport-report'
$db       = 'C:\transport-report\instance\transport.db'
$services = @('TransportReport', 'TransportBot', 'TransportBot003')
$site     = 'http://10.103.25.14:5050'
$base     = 'C:\VehicleSoft_Retained_Footprint_Release'
$out      = 'C:\VehicleSoft_Retained_Footprint_Release\r2'
$r1Verdict = 'C:\VehicleSoft_Retained_Footprint_Release\r1\acceptance\area_control_acceptance.json'
$rawSnap  = 'C:\VehicleSoft_Retained_Footprint_Release\raw_before.json'
$backup   = 'C:\transport-report\backups\dji-area'
$py       = 'C:\Program Files\Python314\python.exe'
$ExpectedTag = 'dji-area-retained-footprint-001-rc1'
$ExpectedFingerprint = '8bc0ecdf65311479e9b932ba6a5a6800a510a6be38ede70f31d7b0784dc38122'
$oracle   = 'docs\DJI_AREA_SEPTEMBER_2026_RETAINED_FOOTPRINT_ORACLE.json'
$from     = '2026-09-01'
$to       = '2026-09-18'
if ((hostname) -ne $expectedHost) { throw "STEP FAILED: host is $(hostname), expected $expectedHost" }
if ($prod -ne 'C:\transport-report') { throw "STEP FAILED: refusing a root that is not the production checkout" }
if ($db -ne (Join-Path $prod 'instance\transport.db')) { throw "STEP FAILED: refusing a database outside the production checkout" }
if ($site -notmatch ':5050$') { throw "STEP FAILED: refusing a site that is not the production port 5050" }
if (-not (Test-Path -LiteralPath $db)) { throw "STEP FAILED: database not found: $db" }
if (-not (Test-Path -LiteralPath $py)) { throw "STEP FAILED: python not found: $py" }
$missing = @($services | Where-Object { -not (Get-Service -Name $_ -ErrorAction SilentlyContinue) })
if ($missing.Count -gt 0) { throw "STEP FAILED: service(s) not found: $($missing -join ', ')" }
if (-not (Test-Path -LiteralPath $rawSnap)) { throw "STEP FAILED: $rawSnap not found -- run block R1 first" }
if (-not (Test-Path -LiteralPath $r1Verdict)) { throw "STEP FAILED: $r1Verdict not found -- run block R1 first" }
$r1 = Get-Content -LiteralPath $r1Verdict -Raw | ConvertFrom-Json
if (($r1.verdict -ne 'PASS') -or ($r1.phase -ne 'pre-apply')) { throw "STEP FAILED: block R1 did not pass PRE-APPLY -- there is nothing to apply" }
& git -C $prod fetch --quiet origin "refs/tags/${ExpectedTag}:refs/tags/${ExpectedTag}"
if ($LASTEXITCODE -ne 0) { throw "STEP FAILED: git fetch of the tag exit $LASTEXITCODE" }
$pinned = (& git -C $prod rev-parse --verify --quiet "$ExpectedTag^{commit}")
if (-not $pinned) { throw "STEP FAILED: tag $ExpectedTag does not resolve to a commit" }
$headSha = (& git -C $prod rev-parse HEAD)
& git -C $prod merge-base --is-ancestor $pinned $headSha
if ($LASTEXITCODE -ne 0) { throw "STEP FAILED: production HEAD $headSha does not contain the reviewed revision $pinned -- deploy main first" }
$changed = @(& git -C $prod status --porcelain --untracked-files=no)
if ($changed.Count -gt 0) { throw "STEP FAILED: the production checkout has modified tracked files -- refusing to run over them" }
& git -C $prod --no-pager log --oneline -1
Set-Location $prod
$fpFound = @(& $py tools\dji_area_holdout.py fingerprint | Select-String -Pattern '^\s*CODE FINGERPRINT\s*:\s*([0-9a-f]{64})\s*$' | ForEach-Object { $_.Matches[0].Groups[1].Value })
if ($fpFound.Count -ne 1) { throw "STEP FAILED: expected exactly one CODE FINGERPRINT line, got $($fpFound.Count)" }
$fp = $fpFound[0]
if ($fp -ne $ExpectedFingerprint) { throw "STEP FAILED: code fingerprint is $fp, the reviewed one is $ExpectedFingerprint -- the model changed after the tag, a new review is needed" }
Write-Host "CODE FINGERPRINT: $fp"
$ids = @((Get-Content -LiteralPath $oracle -Raw | ConvertFrom-Json).transition.expected_rewrites)
if ($ids.Count -lt 1) { throw "STEP FAILED: the oracle names no expected rewrites" }
$idArgs = @()
foreach ($id in $ids) { $idArgs += '--flight-id'; $idArgs += [string]$id }
Write-Host ("EXPECTED REWRITES: " + ($ids -join ', '))
New-Item -ItemType Directory -Force -Path $backup | Out-Null
if (Test-Path -LiteralPath $out) { Remove-Item -LiteralPath $out -Recurse -Force }
New-Item -ItemType Directory -Force -Path $out | Out-Null
$stamp = Get-Date -Format 'yyyyMMdd_HHmmss'
$pre = -1
$post = -1
$raw = -1
try {
  foreach ($name in $services) { Stop-Service -Name $name }
  $deadline = (Get-Date).AddSeconds(90)
  while (@($services | Where-Object { (Get-Service -Name $_).Status -ne 'Stopped' }).Count -gt 0) {
    if ((Get-Date) -gt $deadline) { throw "STEP FAILED: the services did not reach Stopped within 90s" }
    Start-Sleep -Seconds 2
  }
  Write-Host 'SERVICES STOPPED'
  & $py tools\check_db_lock.py --db $db
  $lock = $LASTEXITCODE
  if ($lock -eq 2) { throw "STEP FAILED: another process holds the database (exit 2)" }
  Write-Host "DB_LOCK_EXIT: $lock  (0 clean, 3 stale WAL is expected on this project)"
  $dest = Join-Path $backup ("transport.db.pre_retained_footprint_r2_" + $stamp + ".bak")
  Copy-Item -LiteralPath $db -Destination $dest -Force
  foreach ($sfx in @('-wal','-shm')) { if (Test-Path -LiteralPath ($db + $sfx)) { Copy-Item -LiteralPath ($db + $sfx) -Destination ($dest + $sfx) -Force } }
  if (-not (Test-Path -LiteralPath $dest)) { throw "STEP FAILED: backup was not created" }
  Write-Host ("BACKUP: " + $dest + "  " + (Get-Item -LiteralPath $dest).Length + " bytes")
  & $py tools\dji_area_recalc.py --db $db --from $from --to $to --dry-run --quiet --json (Join-Path $out 'pre.json') --rows (Join-Path $out 'pre_rows.json')
  if ($LASTEXITCODE -ne 0) { throw "STEP FAILED: recalc dry-run exit $LASTEXITCODE" }
  & $py tools\dji_area_control_acceptance.py --db $db --oracle $oracle --phase pre-apply --recalc-summary (Join-Path $out 'pre.json') --recalc-rows (Join-Path $out 'pre_rows.json') --out (Join-Path $out 'pre_apply')
  $pre = $LASTEXITCODE
  Write-Host "PRE-APPLY ACCEPTANCE EXIT CODE: $pre  (0 PASS, 3 FAIL)"
  if ($pre -ne 0) { throw "STOP: PRE-APPLY did not pass right before the apply (exit $pre) -- NOTHING WAS WRITTEN; send back $out" }
  & $py tools\dji_area_recalc.py --db $db --from $from --to $to --apply --quiet --json (Join-Path $out 'apply.json') @idArgs
  if ($LASTEXITCODE -ne 0) { throw "STEP FAILED: apply exit $LASTEXITCODE -- the backup printed above holds the database before the apply" }
  & $py tools\dji_area_recalc.py --db $db --from $from --to $to --dry-run --quiet --json (Join-Path $out 'second.json')
  if ($LASTEXITCODE -ne 0) { throw "STEP FAILED: second recalc dry-run exit $LASTEXITCODE" }
  & $py tools\dji_area_control_acceptance.py --db $db --oracle $oracle --phase post-apply --recalc-summary (Join-Path $out 'second.json') --apply-summary (Join-Path $out 'apply.json') --out (Join-Path $out 'post_apply')
  $post = $LASTEXITCODE
  Write-Host "POST-APPLY ACCEPTANCE EXIT CODE: $post  (0 PASS, 3 FAIL)"
  & $py tools\dji_area_raw_guard.py --db $db --compare $rawSnap
  $raw = $LASTEXITCODE
  Write-Host "RAW GUARD EXIT CODE: $raw  (0 RAW and billable untouched, 3 violated)"
  if ($post -ne 0) { throw "STOP: POST-APPLY did not pass (exit $post) -- the backup printed above holds the database before the apply; send back $out" }
  if ($raw -ne 0) { throw "STOP: the RAW guard exit $raw -- send back $out" }
} finally {
  foreach ($name in $services) { Restart-Service -Name $name }
  $deadline2 = (Get-Date).AddSeconds(90)
  while (@($services | Where-Object { (Get-Service -Name $_).Status -ne 'Running' }).Count -gt 0) {
    if ((Get-Date) -gt $deadline2) { throw "STEP FAILED: the services did not reach Running within 90s -- START THEM BY HAND" }
    Start-Sleep -Seconds 2
  }
  Write-Host 'SERVICES RUNNING'
}
Start-Sleep -Seconds 8
$login = Invoke-WebRequest -Uri ($site + '/login') -UseBasicParsing -TimeoutSec 30
if ($login.StatusCode -ne 200) { throw "STEP FAILED: smoke /login returned $($login.StatusCode)" }
if ($login.Content -notmatch 'vs-login-form') { throw "STEP FAILED: smoke /login did not render the login form" }
Write-Host 'SMOKE LOGIN: 200'
$anon = -1
$anonBody = ''
try { $r2 = Invoke-WebRequest -Uri ($site + '/drones/area-control') -UseBasicParsing -TimeoutSec 30; $anon = [int]$r2.StatusCode; $anonBody = $r2.Content } catch { if ($_.Exception.Response) { $anon = [int]$_.Exception.Response.StatusCode } }
Write-Host "SMOKE SCREEN FOR AN ANONYMOUS VISITOR: $anon  (200 with the login form expected)"
if ($anon -ne 200) { throw "STEP FAILED: /drones/area-control answered $anon for an anonymous visitor" }
if ($anonBody -notmatch 'vs-login-form') { throw "STEP FAILED: an anonymous visitor was NOT sent to the login form" }
Get-ChildItem -LiteralPath (Join-Path $out 'post_apply') | Select-Object Name, Length | Format-Table -AutoSize
Compress-Archive -Path "$out\*" -DestinationPath (Join-Path $base 'r2.zip') -Force
Write-Host 'POST-APPLY PASS'
Write-Host 'SEND BACK: C:\VehicleSoft_Retained_Footprint_Release\r2.zip and the console text above'
}
```

После блока глазами: войти на production, открыть
`/drones/area-control?date_from=2026-09-01&date_to=2026-09-18` — пять записей
среди корректировок с причиной «Площадь перенесена из предыдущей записи…»,
714181711 на проверке; книга периода —
`r2\post_apply\area_control_report.xlsx`.

### Блок R3 — отдельное решение: исторические кандидаты вне сентября

Не часть выпуска: вставляется только по отдельному решению владельца и только
после PASS блока R2. Утверждённый список — 28 кандидатов оценки production
28.09.2026 (тот же список — `production_evaluation.final_candidates` оракула
перехода); пять сентябрьских уже применены блоком R2, остаются 23. Свежий
оценщик обязан назвать РОВНО этот список — иначе остановка до нового ревью:
новая запись, ставшая кандидатом, не применяется молча. Пересчитываются только
названные строки (`--flight-id`), периода целиком здесь нет.

```powershell
& {
$ErrorActionPreference = 'Continue'
$expectedHost = 'srv-yoqsh'
$prod     = 'C:\transport-report'
$db       = 'C:\transport-report\instance\transport.db'
$services = @('TransportReport', 'TransportBot', 'TransportBot003')
$base     = 'C:\VehicleSoft_Retained_Footprint_Release'
$out      = 'C:\VehicleSoft_Retained_Footprint_Release\r3'
$r2Verdict = 'C:\VehicleSoft_Retained_Footprint_Release\r2\post_apply\area_control_acceptance.json'
$rawSnap3 = 'C:\VehicleSoft_Retained_Footprint_Release\raw_before_r3.json'
$backup   = 'C:\transport-report\backups\dji-area'
$py       = 'C:\Program Files\Python314\python.exe'
$ExpectedTag = 'dji-area-retained-footprint-001-rc1'
$ExpectedFingerprint = '8bc0ecdf65311479e9b932ba6a5a6800a510a6be38ede70f31d7b0784dc38122'
$oracle   = 'docs\DJI_AREA_SEPTEMBER_2026_RETAINED_FOOTPRINT_ORACLE.json'
$approved = @(567468930, 579492311, 589911352, 593926297, 622804207, 628111487, 653169760, 655626159, 660151343, 674438091, 677116076, 679813767, 683607628, 690137315, 692568940, 693319955, 695314135, 695707045, 695759434, 695784399, 697634191, 698068932, 698354474, 701661028, 702797709, 703830892, 703847599, 714181794)
if ((hostname) -ne $expectedHost) { throw "STEP FAILED: host is $(hostname), expected $expectedHost" }
if ($prod -ne 'C:\transport-report') { throw "STEP FAILED: refusing a root that is not the production checkout" }
if ($db -ne (Join-Path $prod 'instance\transport.db')) { throw "STEP FAILED: refusing a database outside the production checkout" }
if (-not (Test-Path -LiteralPath $db)) { throw "STEP FAILED: database not found: $db" }
if (-not (Test-Path -LiteralPath $py)) { throw "STEP FAILED: python not found: $py" }
$missing = @($services | Where-Object { -not (Get-Service -Name $_ -ErrorAction SilentlyContinue) })
if ($missing.Count -gt 0) { throw "STEP FAILED: service(s) not found: $($missing -join ', ')" }
if (-not (Test-Path -LiteralPath $r2Verdict)) { throw "STEP FAILED: $r2Verdict not found -- run block R2 first" }
$r2v = Get-Content -LiteralPath $r2Verdict -Raw | ConvertFrom-Json
if (($r2v.verdict -ne 'PASS') -or ($r2v.phase -ne 'post-apply')) { throw "STEP FAILED: block R2 did not pass POST-APPLY" }
& git -C $prod fetch --quiet origin "refs/tags/${ExpectedTag}:refs/tags/${ExpectedTag}"
if ($LASTEXITCODE -ne 0) { throw "STEP FAILED: git fetch of the tag exit $LASTEXITCODE" }
$pinned = (& git -C $prod rev-parse --verify --quiet "$ExpectedTag^{commit}")
if (-not $pinned) { throw "STEP FAILED: tag $ExpectedTag does not resolve to a commit" }
$headSha = (& git -C $prod rev-parse HEAD)
& git -C $prod merge-base --is-ancestor $pinned $headSha
if ($LASTEXITCODE -ne 0) { throw "STEP FAILED: production HEAD $headSha does not contain the reviewed revision $pinned" }
$changed = @(& git -C $prod status --porcelain --untracked-files=no)
if ($changed.Count -gt 0) { throw "STEP FAILED: the production checkout has modified tracked files -- refusing to run over them" }
Set-Location $prod
$fpFound = @(& $py tools\dji_area_holdout.py fingerprint | Select-String -Pattern '^\s*CODE FINGERPRINT\s*:\s*([0-9a-f]{64})\s*$' | ForEach-Object { $_.Matches[0].Groups[1].Value })
if ($fpFound.Count -ne 1) { throw "STEP FAILED: expected exactly one CODE FINGERPRINT line, got $($fpFound.Count)" }
$fp = $fpFound[0]
if ($fp -ne $ExpectedFingerprint) { throw "STEP FAILED: code fingerprint is $fp, the reviewed one is $ExpectedFingerprint" }
$september = @((Get-Content -LiteralPath $oracle -Raw | ConvertFrom-Json).transition.expected_rewrites)
$todo = @($approved | Where-Object { $september -notcontains $_ })
if ($todo.Count -ne ($approved.Count - $september.Count)) { throw "STEP FAILED: the September rewrites are not all among the approved candidates" }
Write-Host ("HISTORICAL CANDIDATES TO APPLY: " + $todo.Count)
$idArgs = @()
foreach ($id in $todo) { $idArgs += '--flight-id'; $idArgs += [string]$id }
New-Item -ItemType Directory -Force -Path $backup | Out-Null
if (Test-Path -LiteralPath $out) { Remove-Item -LiteralPath $out -Recurse -Force }
New-Item -ItemType Directory -Force -Path $out | Out-Null
$stamp = Get-Date -Format 'yyyyMMdd_HHmmss'
$ev2 = -1
$raw = -1
try {
  foreach ($name in $services) { Stop-Service -Name $name }
  $deadline = (Get-Date).AddSeconds(90)
  while (@($services | Where-Object { (Get-Service -Name $_).Status -ne 'Stopped' }).Count -gt 0) {
    if ((Get-Date) -gt $deadline) { throw "STEP FAILED: the services did not reach Stopped within 90s" }
    Start-Sleep -Seconds 2
  }
  Write-Host 'SERVICES STOPPED'
  & $py tools\check_db_lock.py --db $db
  $lock = $LASTEXITCODE
  if ($lock -eq 2) { throw "STEP FAILED: another process holds the database (exit 2)" }
  $dest = Join-Path $backup ("transport.db.pre_retained_footprint_r3_" + $stamp + ".bak")
  Copy-Item -LiteralPath $db -Destination $dest -Force
  foreach ($sfx in @('-wal','-shm')) { if (Test-Path -LiteralPath ($db + $sfx)) { Copy-Item -LiteralPath ($db + $sfx) -Destination ($dest + $sfx) -Force } }
  if (-not (Test-Path -LiteralPath $dest)) { throw "STEP FAILED: backup was not created" }
  Write-Host ("BACKUP: " + $dest + "  " + (Get-Item -LiteralPath $dest).Length + " bytes")
  if (Test-Path -LiteralPath $rawSnap3) { Remove-Item -LiteralPath $rawSnap3 -Force }
  & $py tools\dji_area_raw_guard.py --db $db --save $rawSnap3
  if ($LASTEXITCODE -ne 0) { throw "STEP FAILED: RAW snapshot exit $LASTEXITCODE" }
  & $py tools\dji_area_footprint_calibration.py --db $db --out (Join-Path $out 'evaluation') --evaluate-rule
  if ($LASTEXITCODE -ne 0) { throw "STOP: the evaluator did not pass (exit $LASTEXITCODE) -- nothing applied" }
  $evaluation = Get-Content -LiteralPath (Join-Path $out 'evaluation\retained_footprint_evaluation.json') -Raw | ConvertFrom-Json
  $fresh = @($evaluation.final_candidates)
  $drift = @(Compare-Object -ReferenceObject @($approved | Sort-Object) -DifferenceObject @($fresh | Sort-Object))
  if ($drift.Count -gt 0) { throw "STOP: the fresh candidates differ from the approved list -- a new owner review is needed, nothing applied" }
  $days = @($evaluation.records | Where-Object { $todo -contains $_.flight_id } | ForEach-Object { [string]$_.report_start_date } | Sort-Object)
  if ($days.Count -ne $todo.Count) { throw "STEP FAILED: report days found for $($days.Count) of $($todo.Count) candidates" }
  $from3 = $days[0]
  $to3 = $days[$days.Count - 1]
  Write-Host "REPORT DAYS: $from3 .. $to3"
  & $py tools\dji_area_recalc.py --db $db --from $from3 --to $to3 --dry-run --quiet --json (Join-Path $out 'dry.json') @idArgs
  if ($LASTEXITCODE -ne 0) { throw "STEP FAILED: targeted dry-run exit $LASTEXITCODE" }
  $dry = Get-Content -LiteralPath (Join-Path $out 'dry.json') -Raw | ConvertFrom-Json
  if (($dry.flights_in_period -ne $todo.Count) -or ($dry.calc_writes.would_write -ne $todo.Count)) { throw "STOP: the dry run does not want to rewrite exactly the $($todo.Count) approved records -- nothing applied" }
  & $py tools\dji_area_recalc.py --db $db --from $from3 --to $to3 --apply --quiet --json (Join-Path $out 'apply.json') @idArgs
  if ($LASTEXITCODE -ne 0) { throw "STEP FAILED: targeted apply exit $LASTEXITCODE -- the backup printed above holds the database before it" }
  $applied = Get-Content -LiteralPath (Join-Path $out 'apply.json') -Raw | ConvertFrom-Json
  if ($applied.calc_writes.new -ne $todo.Count) { throw "STOP: the apply wrote $($applied.calc_writes.new) new rows, expected $($todo.Count)" }
  & $py tools\dji_area_recalc.py --db $db --from $from3 --to $to3 --dry-run --quiet --json (Join-Path $out 'second.json') @idArgs
  if ($LASTEXITCODE -ne 0) { throw "STEP FAILED: second targeted dry-run exit $LASTEXITCODE" }
  $second = Get-Content -LiteralPath (Join-Path $out 'second.json') -Raw | ConvertFrom-Json
  if ($second.calc_writes.unchanged -ne $todo.Count) { throw "STOP: the second dry run is not unchanged for the applied records" }
  & $py tools\dji_area_footprint_calibration.py --db $db --out (Join-Path $out 'evaluation_after') --evaluate-rule
  $ev2 = $LASTEXITCODE
  Write-Host "EVALUATOR AFTER EXIT CODE: $ev2  (0 PASS, 4 a control violated)"
  & $py tools\dji_area_raw_guard.py --db $db --compare $rawSnap3
  $raw = $LASTEXITCODE
  Write-Host "RAW GUARD EXIT CODE: $raw  (0 RAW and billable untouched, 3 violated)"
  if ($ev2 -ne 0) { throw "STOP: the evaluator after the apply did not pass (exit $ev2) -- send back $out" }
  if ($raw -ne 0) { throw "STOP: the RAW guard exit $raw -- send back $out" }
} finally {
  foreach ($name in $services) { Restart-Service -Name $name }
  $deadline2 = (Get-Date).AddSeconds(90)
  while (@($services | Where-Object { (Get-Service -Name $_).Status -ne 'Running' }).Count -gt 0) {
    if ((Get-Date) -gt $deadline2) { throw "STEP FAILED: the services did not reach Running within 90s -- START THEM BY HAND" }
    Start-Sleep -Seconds 2
  }
  Write-Host 'SERVICES RUNNING'
}
Compress-Archive -Path "$out\*" -DestinationPath (Join-Path $base 'r3.zip') -Force
Write-Host 'HISTORICAL APPLY PASS'
Write-Host 'SEND BACK: C:\VehicleSoft_Retained_Footprint_Release\r3.zip and the console text above'
}
```

## Если блок остановился

- До строки `SERVICES STOPPED` — ничего не тронуто; причина в строке
  `STEP FAILED`.
- R1 и остановка до применения в R2 и R3 — база не записывалась (R1 это
  доказывает хешем, R2 и R3 — тем, что до применения идут только сухой прогон
  и чтение). Прислать архив и текст консоли.
- Остановка ПОСЛЕ применения (R2: POST-APPLY или сторож RAW; R3: проверки
  после `apply`) — службы подняты в `finally`, строки расчёта append-only.
  Копия базы до применения — путь в строке `BACKUP:`; восстанавливать её
  только решением владельца при остановленных службах, вместе с `-wal` и
  `-shm`.

## Откат

**Код.** `git revert -m 1` мерж-коммита PR, не `reset`. Правило выборочное:
после отката пересчёт даёт затронутым строкам прежний отпечаток, и
`insert_calculation` возвращает прежнюю строку (`reactivated`), ничего не
удаляя. Прочие строки отката не замечают — их отпечаток правило не трогало.

**Данные.** Схема не менялась, миграций нет. Строки расчёта append-only:
строка правила закрывается `superseded_at`, прежняя возвращается. Полный
возврат базы — копия из строки `BACKUP:` соответствующего блока.
