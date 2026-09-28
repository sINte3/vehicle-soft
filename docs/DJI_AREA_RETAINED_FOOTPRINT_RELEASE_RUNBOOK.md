# DJI-AREA-RETAINED-FOOTPRINT-001 — выпуск на production: переход и устойчивое состояние

Один законченный порядок выпуска правила «перенесённый скаляр с пренебрежимым
следом» на production. Исполняет владелец; агент на серверах не исполняет
ничего. Правило и его доказательства — `docs/DJI_AREA_FOOTPRINT_CALIBRATION_001.md`
§8, приёмка — `tools/dji_area_control_acceptance.py`, оракул перехода —
`docs/DJI_AREA_SEPTEMBER_2026_RETAINED_FOOTPRINT_ORACLE.json`, инструмент
выпуска — `tools/dji_area_retained_release_closeout.py`
(DJI-AREA-RETAINED-RELEASE-CLOSEOUT-002).

**Где.** Production, `SRV-YOQSH`: каталог `C:\transport-report`, база
`instance\transport.db`, сайт `http://10.103.25.14:5050`. Службы
`TransportReport`, `TransportBot`, `TransportBot003` останавливаются вместе,
как в шаге 2 `docs/RELEASE_AND_BACKUP_PROCEDURE.md`: иначе ни копия базы не
согласована, ни «база не изменилась» не доказуемо. Окно обслуживания —
несколько минут на блок.

**Чего здесь нет и не будет.** Обращения к DJI, исторического массового
backfill, решений администратора, изменения RAW, заполнения
`billable_area_m2`, применения «всего, что нашёл оценщик», `git pull`,
`git reset`, `git clean`, удаления неотслеживаемых файлов production.
Применяются только названные строки; всё, что сверх них, — остановка.

## Состояние на 28.09.2026

По инвентаризации владельца (из среды разработки production недостижим —
здесь это записано, а не проверено):

- PR #140 смержен (`00f2fdc`); тег `dji-area-retained-footprint-001-rc1` →
  `1e9ca17`, отпечаток замороженного кода `8bc0ecdf…`. PR #141–#144 после него
  — только документы.
- Production: `C:\transport-report`, ветка `main`, HEAD `436e890`, дерево
  отслеживаемых файлов чистое, неотслеживаемых — 107 (не трогать). Миграция
  `DRONE_AREA_CONTROL_V2_001` уже применена: строка в реестре, таблицы
  `drone_area_cycle_runs` и `drone_area_decisions`, оба триггера append-only;
  `PRAGMA integrity_check = ok`. Выпуск её не применяет и не повторяет —
  инструмент только печатает её наличие.
- Первый R1 (клон тега rc1): самотесты 46 / 56 / 12 PASS; оценщик PASS
  (B 44, кандидатов 28, из них в сентябре 5; RAW изменён 0; billable 0).
  Сухой прогон сентября: 4887 вылетов, `unchanged` 4525, `would_write` 362 —
  оракул ждёт 5. PRE-APPLY: **FAIL**. Ничего не применено, код не
  развёрнут; службы перезапущены, `/login` 200, `/drones/area-control`
  анонимно — форма входа. Production работает на `436e890`.
- Ручная попытка выровнять отпечатки упала на кавычках PowerShell
  (`SyntaxError: '(' was never closed`) на первом же шаге, до остановки
  служб, до записи и до git. Встроенного Python в блоках больше нет: вся
  логика — в инструменте, блоки только проверяют ревизию, останавливают и
  поднимают службы и зовут его.

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

## Почему 357 строк нормализуются, а не принимаются

Разбор 362 строк первого R1 (владелец, 28.09.2026): 5 — ожидаемый переход,
357 — дрейф ОТПЕЧАТКА при том же решении. Ни у одной из 357 не изменились
RAW, принятое, статусы, учётный класс, допуск к итогу, применение, канал,
окно счётчика, решение по аномалии.

- **356** — у записи появилась более новая неизменяемая ревизия списка DJI
  (страница живого захвата вместо записи форензик-импорта), принятая ПОСЛЕ
  расчёта; RAW старого расчёта и новой ревизии совпал у 356 из 356.
- **1** — 710001927: свои ревизии (список, карточка), машина и хронология
  (`unit:8`) не менялись; её цепочка — база 710001923, мостик 710001925. У
  базы сменилась выбранная ревизия (31341 → 82969, другой SHA), у мостика —
  нет. Отпечаток цели включает SHA соседей по цепочке, поэтому сдвинулся за
  базой.

Отпечаток (`calculation_input_hash`) включает SHA источников — своих и
соседей. Новая неизменяемая ревизия того же вылета меняет отпечаток, даже
когда она несёт те же значения. Сухой прогон сравнивает только отпечаток,
поэтому такая строка — `would_write`, и PRE-APPLY с ней обязан упасть, иначе
он не отличил бы дрейф от регрессии.

**Нормализация — не новое правило.** В коде нет списка «357» и нет ни одного
номера вылета: инструмент решает по данным, для каждой строки отдельно, и
переписывает её штатным `pipeline.recalculate` с `--apply` по названным
`flight_id` — ровно так, как её переписал бы любой пересчёт этого периода
нынешним кодом. Результат строки при этом не меняется; меняется только то,
на какие неизменяемые источники она ссылается. Строка проходит, только если
доказано всё сразу:

1. текущая строка расчёта есть, сухой прогон хочет другой отпечаток;
2. строка, которую запишет пересчёт, совпадает с сохранённой во ВСЕХ колонках
   результата — это все колонки `dji_area_calculations`, кроме отпечатка,
   ссылок на ревизии и сводку V4 и служебных дат: RAW до бита, принятое,
   статусы, метод, уверенность, флаги аномалий, применение и канал, окно,
   экран и цепочка (база, мостики), пересечение, допуск к итогу, billable
   пуст;
3. правило на строке не срабатывает (его строки — переход, а не
   нормализация);
4. **причина доказана контрфактом**: тот же конвейер с указателями ревизий,
   откатанными к моменту сохранённого расчёта (ревизии, принятые к этому
   моменту; для вылетов того же прогона — ровно то, что записала их строка),
   воспроизводит сохранённый отпечаток бит в бит. Значит, кроме того, какие
   ревизии текущие, не изменилось ничего: ни код, ни конфигурация, ни машина,
   ни состав соседей. Причина называется поимённо: своя ревизия, ревизия
   соседа или обе;
5. действующее решение администратора не меняет статус «расчёт изменился» —
   решение привязано к отпечатку строки, и нормализация сделала бы живое
   решение устаревшим, а «принять автоматический результат» перестало бы
   действовать. Такая строка останавливает выпуск: как поступить с решением —
   выбор владельца, а не инструмента;
6. привязка к полю, которую перепишет то же применение, не сдвигается.

Любая строка, не прошедшая ворота, — отказ с названной причиной
(`SEMANTIC_CHANGE`, `RAW_CHANGED`, `BILLABLE_NOT_NULL`,
`UNEXPECTED_RULE_FIRING`, `UNEXPLAINED_HASH_DRIFT`,
`ADMIN_DECISION_WOULD_CHANGE`, `FIELD_ATTRIBUTION_WOULD_CHANGE`, …), и
тогда не нормализуется НИЧЕГО. 358-я строка, появившаяся после 28.09,
проходит те же ворота — не «принимается», а доказывается или
останавливает.

После записи инструмент читает базу, а не план: у каждой строки отпечаток
тот, что доказан, результат равен замещённой строке, прежняя строка закрыта
`superseded_at`; строки вне плана не тронуты; RAW и billable прежние; история
решений та же; повторная классификация — дрейфа нет, переход тот же и тем же
отпечатком; и код, который СЕЙЧАС стоит на production (`436e890`, его
собственный `tools\dji_area_recalc.py`), видит нормализованные строки
`unchanged`. Откат выпуска после нормализации поэтому ничего не оставляет в
дрейфе.

## Порядок

0. **Предпосылки.** PR DJI-AREA-RETAINED-RELEASE-CLOSEOUT-002 смержен, CI
   зелёный. Владелец создал аннотированный тег
   `dji-area-retained-release-closeout-002` на мерж-коммите этого PR и
   отправил его в origin. Тег `dji-area-retained-footprint-001-rc1` не
   трогается: он по-прежнему пин проверенной МОДЕЛИ, а новый тег — пин
   ревизии ВЫПУСКА, которая его содержит (блоки это проверяют). Замороженные
   файлы в этом PR не менялись — отпечаток тот же. Без тега каждый блок
   останавливается на проверке ревизии, до первого действия.
1. **Блок R1 — до деплоя.** Код — сторонний клон тега выпуска. Стражи
   ревизии, обоих тегов и отпечатка; самотесты; остановка служб;
   `r1` инструмента: копия базы (`backup_transport_db.py`, integrity_check,
   сверка с живой базой), снимок RAW (если его ещё нет), нормализация
   доказанных строк, повтор на коде production, оценщик правила, сухой
   прогон сентября с `--rows`, PRE-APPLY, сторож RAW; службы подняты; дымовые
   проверки. PASS снимает строку `DJI-AREA-RETAINED-FOOTPRINT-001` в
   `docs/RELEASE_GATE.md` (коммитом в `main`, с числами из
   `closeout_verdict.json`).
2. **Блок R2 — деплой и применение, после снятия строки гейта.** Код
   production перематывается `git merge --ff-only` ровно на коммит тега
   выпуска (не `git pull`, не на «что сейчас в main»): прежний HEAD обязан
   быть его предком, отслеживаемое дерево — чистым, в дельте — ни одной
   миграции, и ни один неотслеживаемый файл после перемотки не пропал
   (множество до и после сверяется при остановленных службах). Затем
   `r2` инструмента кодом production: вердикт R1 (PASS той же модели и того
   же оракула), копия, нормализация дрейфа, появившегося после R1 (обычно
   ноль), PRE-APPLY ещё раз, применение РОВНО `expected_rewrites` оракула,
   повтор по ним (`unchanged`), второй прогон периода, POST-APPLY с
   `--apply-summary`, сторож RAW против снимка R1. Службы подняты; дымовые
   проверки.
3. **После R2.** Глазами: войти на production, открыть
   `/drones/area-control?date_from=2026-09-01&date_to=2026-09-18` — пять
   записей среди корректировок с причиной «Площадь перенесена из предыдущей
   записи…», 714181711 на проверке; книга периода —
   `closeout_r2\post_apply\area_control_report.xlsx`. Релиз записывается в
   `docs/DEPLOYED.md` той сессией, которой поручен релиз.
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
| R1: классификация до записи | `EXPECTED_TRANSITION` 5, `REFUSED` 0; `HASH_ONLY` — сколько покажут данные (по разбору 28.09 — 357) |
| R1: повтор кода production по нормализованным | `unchanged` = число нормализованных |
| PRE-APPLY `calc_writes` | `{"unchanged": 4882, "would_write": 5}` |
| R2: нормализация дрейфа после R1 | обычно 0 |
| применение | `{"new": 5}` |
| повтор по применённым | `{"unchanged": 5}` |
| POST-APPLY второй прогон | `{"unchanged": 4887}` |

Коды возврата: инструмент закрытия — 0 PASS, 1 ошибка аргументов, 2 базы нет,
3 STOP; приёмка — 0 PASS, 1 ошибка аргументов, 2 базы нет, 3 FAIL; оценщик
— 0, 3 база изменилась во время чтения, 4 нарушен контроль; сторож RAW — 0,
3 RAW изменён либо billable не пуст.

Всё, что инструмент решил, лежит в каталоге прогона: `closeout_verdict.json`
(вердикт, копия базы, шаги с кодами возврата, отпечаток модели и оракула),
`normalize\classification_before.csv` (каждая строка не `UNCHANGED`: класс,
причина, прежний / новый / контрфактический отпечаток, какие ревизии
сменились), `normalize\classification_after.json`, `normalize\baseline_engine\`,
`evaluation\`, `pre.json`, `pre_rows.json`, `acceptance\` (R1) либо
`pre_apply\`, `apply.json`, `targeted.json`, `second.json`, `post_apply\` (R2).

### Блок R1 — SRV-YOQSH, до деплоя: нормализация и PRE-APPLY

```powershell
& {
$ErrorActionPreference = 'Continue'
$expectedHost = 'srv-yoqsh'
$prod     = 'C:\transport-report'
$db       = 'C:\transport-report\instance\transport.db'
$services = @('TransportReport', 'TransportBot', 'TransportBot003')
$site     = 'http://10.103.25.14:5050'
$base     = 'C:\VehicleSoft_Retained_Footprint_Release'
$src      = 'C:\VehicleSoft_Retained_Footprint_Release\src'
$out      = 'C:\VehicleSoft_Retained_Footprint_Release\closeout_r1'
$rawSnap  = 'C:\VehicleSoft_Retained_Footprint_Release\raw_before.json'
$backup   = 'C:\transport-report\backups\dji-area'
$py       = 'C:\Program Files\Python314\python.exe'
$ExpectedTag = 'dji-area-retained-footprint-001-rc1'
$ReleaseTag  = 'dji-area-retained-release-closeout-002'
$ExpectedFingerprint = '8bc0ecdf65311479e9b932ba6a5a6800a510a6be38ede70f31d7b0784dc38122'
$oracle   = 'docs\DJI_AREA_SEPTEMBER_2026_RETAINED_FOOTPRINT_ORACLE.json'
if ((hostname) -ne $expectedHost) { throw "STEP FAILED: host is $(hostname), expected $expectedHost" }
if ($prod -ne 'C:\transport-report') { throw "STEP FAILED: refusing a root that is not the production checkout" }
if ($db -ne (Join-Path $prod 'instance\transport.db')) { throw "STEP FAILED: refusing a database outside the production checkout" }
if ($site -notmatch ':5050$') { throw "STEP FAILED: refusing a site that is not the production port 5050" }
if (-not (Test-Path -LiteralPath $db)) { throw "STEP FAILED: database not found: $db" }
if (-not (Test-Path -LiteralPath $py)) { throw "STEP FAILED: python not found: $py" }
$missing = @($services | Where-Object { -not (Get-Service -Name $_ -ErrorAction SilentlyContinue) })
if ($missing.Count -gt 0) { throw "STEP FAILED: service(s) not found: $($missing -join ', ')" }
$origin = (& git -C $prod config --get remote.origin.url)
if (-not $origin) { throw "STEP FAILED: cannot read origin url from $prod" }
New-Item -ItemType Directory -Force -Path $base | Out-Null
$stamp = Get-Date -Format 'yyyyMMdd_HHmmss'
if (Test-Path -LiteralPath $out) { Move-Item -LiteralPath $out -Destination ($out + '_before_' + $stamp) }
if (Test-Path -LiteralPath $src) { Remove-Item -LiteralPath $src -Recurse -Force }
& git clone --quiet --no-checkout $origin $src
if ($LASTEXITCODE -ne 0) { throw "STEP FAILED: git clone exit $LASTEXITCODE" }
& git -C $src fetch --quiet origin "refs/tags/${ExpectedTag}:refs/tags/${ExpectedTag}"
if ($LASTEXITCODE -ne 0) { throw "STEP FAILED: tag $ExpectedTag is not on origin" }
& git -C $src fetch --quiet origin "refs/tags/${ReleaseTag}:refs/tags/${ReleaseTag}"
if ($LASTEXITCODE -ne 0) { throw "STEP FAILED: tag $ReleaseTag is not on origin -- the owner creates and pushes it after the review" }
$pinned = (& git -C $src rev-parse --verify --quiet "$ExpectedTag^{commit}")
if (-not $pinned) { throw "STEP FAILED: tag $ExpectedTag does not resolve to a commit" }
$release = (& git -C $src rev-parse --verify --quiet "$ReleaseTag^{commit}")
if (-not $release) { throw "STEP FAILED: tag $ReleaseTag does not resolve to a commit" }
& git -C $src merge-base --is-ancestor $pinned $release
if ($LASTEXITCODE -ne 0) { throw "STEP FAILED: the release $release does not contain the reviewed model $pinned" }
& git -C $src checkout --quiet --detach $release
if ($LASTEXITCODE -ne 0) { throw "STEP FAILED: git checkout $release exit $LASTEXITCODE" }
$headSha = (& git -C $src rev-parse HEAD)
if ($headSha -ne $release) { throw "STEP FAILED: HEAD is $headSha, the release is $release" }
$dirty = @(& git -C $src status --porcelain)
if ($dirty.Count -gt 0) { throw "STEP FAILED: the clone has local modifications -- refusing to run an unreviewed working tree" }
& git -C $src --no-pager log --oneline -1
Write-Host "RELEASE PIN: $headSha (model $pinned)"
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
& $py tools\test_dji_area_retained_release_closeout.py
if ($LASTEXITCODE -ne 0) { throw "STEP FAILED: closeout tool self-test exit $LASTEXITCODE" }
New-Item -ItemType Directory -Force -Path $backup | Out-Null
$r1 = -1
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
  & $py tools\dji_area_retained_release_closeout.py r1 --db $db --oracle $oracle --out $out --backup-dir $backup --raw-snapshot $rawSnap --baseline-root $prod
  $r1 = $LASTEXITCODE
  Write-Host "CLOSEOUT R1 EXIT CODE: $r1  (0 PASS, 3 STOP)"
  if ($r1 -ne 0) { throw "STOP: closeout R1 did not pass (exit $r1) -- send back $out and the console text" }
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
try { $page = Invoke-WebRequest -Uri ($site + '/drones/area-control') -UseBasicParsing -TimeoutSec 30; $anon = [int]$page.StatusCode; $anonBody = $page.Content } catch { if ($_.Exception.Response) { $anon = [int]$_.Exception.Response.StatusCode } }
Write-Host "SMOKE SCREEN FOR AN ANONYMOUS VISITOR: $anon  (200 with the login form expected)"
if ($anon -ne 200) { throw "STEP FAILED: /drones/area-control answered $anon for an anonymous visitor" }
if ($anonBody -notmatch 'vs-login-form') { throw "STEP FAILED: an anonymous visitor was NOT sent to the login form" }
Compress-Archive -Path "$out\*" -DestinationPath (Join-Path $base ('closeout_r1_' + $stamp + '.zip')) -Force
Write-Host 'PRE-APPLY PASS'
Write-Host "SEND BACK: $base\closeout_r1_$stamp.zip and the console text above"
}
```

Смотреть: в классификации `EXPECTED TRANSITION : 5` с теми же пятью номерами,
`REFUSED : 0`, `HASH_ONLY normalization` с причинами (`OWN_SOURCE_REVISION`,
`NEIGHBOUR_SOURCE_REVISION`); `apply calc_writes` — только `new`
(`reactivated`) на число нормализуемых; `CLASSIFICATION AFTER` — `HASH_ONLY 0`,
`after normalization the dry run wants: unchanged 4882, would_write 5`;
`baseline engine calc_writes {"unchanged": N}`; `EVALUATOR`-шаг с кодом 0;
в выводе приёмки `rewrites of the dry run 5` с теми же пятью номерами,
`must stay REVIEW 714181711`, `VERDICT: PASS`; `CLOSEOUT R1 VERDICT: PASS`.
Любой отказ классификации — `NOTHING WAS WRITTEN`; прислать архив и текст.

### Блок R2 — SRV-YOQSH, после PASS R1 и снятия строки гейта: деплой, применение, POST-APPLY

Вставляется только после PASS блока R1 и снятия строки гейта. Деплоится
ровно коммит тега выпуска — тот, что R1 проверил в клоне. Если база с R1
ушла вперёд так, что переход перестал быть ровно названным, блок остановится
на PRE-APPLY до первой записи перехода.

```powershell
& {
$ErrorActionPreference = 'Continue'
$expectedHost = 'srv-yoqsh'
$prod     = 'C:\transport-report'
$db       = 'C:\transport-report\instance\transport.db'
$services = @('TransportReport', 'TransportBot', 'TransportBot003')
$site     = 'http://10.103.25.14:5050'
$base     = 'C:\VehicleSoft_Retained_Footprint_Release'
$src      = 'C:\VehicleSoft_Retained_Footprint_Release\src'
$out      = 'C:\VehicleSoft_Retained_Footprint_Release\closeout_r2'
$r1Verdict = 'C:\VehicleSoft_Retained_Footprint_Release\closeout_r1\closeout_verdict.json'
$rawSnap  = 'C:\VehicleSoft_Retained_Footprint_Release\raw_before.json'
$backup   = 'C:\transport-report\backups\dji-area'
$py       = 'C:\Program Files\Python314\python.exe'
$ExpectedTag = 'dji-area-retained-footprint-001-rc1'
$ReleaseTag  = 'dji-area-retained-release-closeout-002'
$ExpectedFingerprint = '8bc0ecdf65311479e9b932ba6a5a6800a510a6be38ede70f31d7b0784dc38122'
$oracle   = 'docs\DJI_AREA_SEPTEMBER_2026_RETAINED_FOOTPRINT_ORACLE.json'
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
if (($r1.verdict -ne 'PASS') -or ($r1.phase -ne 'r1')) { throw "STEP FAILED: block R1 did not pass -- there is nothing to deploy" }
if ($r1.code_fingerprint -ne $ExpectedFingerprint) { throw "STEP FAILED: block R1 ran the model $($r1.code_fingerprint), the reviewed one is $ExpectedFingerprint" }
$origin = (& git -C $prod config --get remote.origin.url)
if (-not $origin) { throw "STEP FAILED: cannot read origin url from $prod" }
New-Item -ItemType Directory -Force -Path $base | Out-Null
$stamp = Get-Date -Format 'yyyyMMdd_HHmmss'
if (Test-Path -LiteralPath $out) { Move-Item -LiteralPath $out -Destination ($out + '_before_' + $stamp) }
if (Test-Path -LiteralPath $src) { Remove-Item -LiteralPath $src -Recurse -Force }
& git clone --quiet --no-checkout $origin $src
if ($LASTEXITCODE -ne 0) { throw "STEP FAILED: git clone exit $LASTEXITCODE" }
& git -C $src fetch --quiet origin "refs/tags/${ExpectedTag}:refs/tags/${ExpectedTag}"
if ($LASTEXITCODE -ne 0) { throw "STEP FAILED: tag $ExpectedTag is not on origin" }
& git -C $src fetch --quiet origin "refs/tags/${ReleaseTag}:refs/tags/${ReleaseTag}"
if ($LASTEXITCODE -ne 0) { throw "STEP FAILED: tag $ReleaseTag is not on origin" }
$pinned = (& git -C $src rev-parse --verify --quiet "$ExpectedTag^{commit}")
if (-not $pinned) { throw "STEP FAILED: tag $ExpectedTag does not resolve to a commit" }
$release = (& git -C $src rev-parse --verify --quiet "$ReleaseTag^{commit}")
if (-not $release) { throw "STEP FAILED: tag $ReleaseTag does not resolve to a commit" }
& git -C $src merge-base --is-ancestor $pinned $release
if ($LASTEXITCODE -ne 0) { throw "STEP FAILED: the release $release does not contain the reviewed model $pinned" }
& git -C $src checkout --quiet --detach $release
if ($LASTEXITCODE -ne 0) { throw "STEP FAILED: git checkout $release exit $LASTEXITCODE" }
$headSha = (& git -C $src rev-parse HEAD)
if ($headSha -ne $release) { throw "STEP FAILED: HEAD is $headSha, the release is $release" }
$dirty = @(& git -C $src status --porcelain)
if ($dirty.Count -gt 0) { throw "STEP FAILED: the clone has local modifications -- refusing to deploy an unreviewed tree" }
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
& $py tools\test_dji_area_retained_release_closeout.py
if ($LASTEXITCODE -ne 0) { throw "STEP FAILED: closeout tool self-test exit $LASTEXITCODE" }
& git -C $prod fetch --quiet origin "refs/tags/${ReleaseTag}:refs/tags/${ReleaseTag}"
if ($LASTEXITCODE -ne 0) { throw "STEP FAILED: git fetch of the tag into production exit $LASTEXITCODE" }
$prodRelease = (& git -C $prod rev-parse --verify --quiet "$ReleaseTag^{commit}")
if ($prodRelease -ne $release) { throw "STEP FAILED: production resolves $ReleaseTag to $prodRelease, the clone to $release" }
$headBefore = (& git -C $prod rev-parse HEAD)
& git -C $prod merge-base --is-ancestor $headBefore $release
if ($LASTEXITCODE -ne 0) { throw "STEP FAILED: production HEAD $headBefore is not an ancestor of $release -- a fast-forward is impossible" }
$changed = @(& git -C $prod status --porcelain --untracked-files=no)
if ($changed.Count -gt 0) { throw "STEP FAILED: the production checkout has modified tracked files -- refusing to deploy over them" }
$migrations = @(& git -C $prod diff --name-only $headBefore $release | Where-Object { $_ -match '^migrate_' })
if ($migrations.Count -gt 0) { throw "STEP FAILED: the delta carries migration(s) $($migrations -join ', ') -- this block deploys code only" }
Write-Host "DEPLOY: $headBefore -> $release"
& git -C $prod --no-pager log --oneline "$headBefore..$release"
New-Item -ItemType Directory -Force -Path $backup | Out-Null
$r2 = -1
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
  $untrackedBefore = @(& git -C $prod status --porcelain --untracked-files=all | Where-Object { $_ -match '^\?\? ' })
  & git -C $prod merge --ff-only $release
  if ($LASTEXITCODE -ne 0) { throw "STEP FAILED: git merge --ff-only exit $LASTEXITCODE -- the production code did not move" }
  $headAfter = (& git -C $prod rev-parse HEAD)
  if ($headAfter -ne $release) { throw "STEP FAILED: production HEAD is $headAfter after the fast-forward, expected $release" }
  $untrackedAfter = @(& git -C $prod status --porcelain --untracked-files=all | Where-Object { $_ -match '^\?\? ' })
  $lost = @($untrackedBefore | Where-Object { $untrackedAfter -notcontains $_ })
  if ($lost.Count -gt 0) { throw "STEP FAILED: $($lost.Count) untracked file(s) disappeared: $($lost -join ', ')" }
  Write-Host "DEPLOYED: $headAfter  (untracked files before $($untrackedBefore.Count), after $($untrackedAfter.Count), none lost)"
  Set-Location $prod
  $fpProd = @(& $py tools\dji_area_holdout.py fingerprint | Select-String -Pattern '^\s*CODE FINGERPRINT\s*:\s*([0-9a-f]{64})\s*$' | ForEach-Object { $_.Matches[0].Groups[1].Value })
  if (($fpProd.Count -ne 1) -or ($fpProd[0] -ne $ExpectedFingerprint)) { throw "STEP FAILED: the deployed tree does not carry the reviewed model -- send back the console text" }
  & $py tools\dji_area_retained_release_closeout.py r2 --db $db --oracle $oracle --out $out --backup-dir $backup --raw-snapshot $rawSnap --r1-verdict $r1Verdict
  $r2 = $LASTEXITCODE
  Write-Host "CLOSEOUT R2 EXIT CODE: $r2  (0 PASS, 3 STOP)"
  if ($r2 -ne 0) { throw "STOP: closeout R2 did not pass (exit $r2) -- send back $out and the console text" }
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
try { $page = Invoke-WebRequest -Uri ($site + '/drones/area-control') -UseBasicParsing -TimeoutSec 30; $anon = [int]$page.StatusCode; $anonBody = $page.Content } catch { if ($_.Exception.Response) { $anon = [int]$_.Exception.Response.StatusCode } }
Write-Host "SMOKE SCREEN FOR AN ANONYMOUS VISITOR: $anon  (200 with the login form expected)"
if ($anon -ne 200) { throw "STEP FAILED: /drones/area-control answered $anon for an anonymous visitor" }
if ($anonBody -notmatch 'vs-login-form') { throw "STEP FAILED: an anonymous visitor was NOT sent to the login form" }
Get-ChildItem -LiteralPath (Join-Path $out 'post_apply') | Select-Object Name, Length | Format-Table -AutoSize
Compress-Archive -Path "$out\*" -DestinationPath (Join-Path $base ('closeout_r2_' + $stamp + '.zip')) -Force
Write-Host 'POST-APPLY PASS'
Write-Host "SEND BACK: $base\closeout_r2_$stamp.zip and the console text above"
}
```

Смотреть: строку `DEPLOY:` — слева `436e890…`, справа коммит тега выпуска —
и список коммитов дельты; `DEPLOYED` с «none lost» по неотслеживаемым
файлам; в выводе `r2` — `apply calc_writes {"new": 5}`, повтор по
применённым `{"unchanged": 5}`, второй прогон `{"unchanged": 4887}`,
`VERDICT: PASS` приёмки POST-APPLY, `RAW UNTOUCHED`, `CLOSEOUT R2 VERDICT:
PASS`; дымовые проверки; `POST-APPLY PASS`.

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
$r2Verdict = 'C:\VehicleSoft_Retained_Footprint_Release\closeout_r2\post_apply\area_control_acceptance.json'
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
- R1: отказ классификации (`NOTHING WAS WRITTEN`) — база не записывалась;
  прислать каталог `closeout_r1` и текст консоли. Остановка ПОСЛЕ
  нормализации (оценщик, PRE-APPLY, сторож RAW) — записаны только строки
  нормализации: доказанно те же по результату, прежние закрыты
  `superseded_at`, код production видит их `unchanged`; делать с ними ничего
  не нужно. Копия базы до записи — `backup.path` в `closeout_verdict.json`.
- R2: остановка до `DEPLOYED` — код production не двигался. После
  `DEPLOYED` и до применения перехода — код новый, записей перехода нет;
  повторный блок R2 после разбора безопасен (fast-forward на тот же коммит —
  пустой, классификация и PRE-APPLY заново). Остановка ПОСЛЕ применения
  (повтор, второй прогон, POST-APPLY, сторож RAW) — службы подняты в
  `finally`, строки расчёта append-only; копия базы до записи —
  `backup.path` в `closeout_r2\closeout_verdict.json`; восстанавливать её
  только решением владельца при остановленных службах.
- R3 — как прежде: до применения только сухой прогон и чтение; после — копия
  из строки `BACKUP:`.
- Оценщик в R1 с `UNEXPECTED_REWRITE` и номером записи группы B вне
  сентября — у этой записи тоже сдвинулся отпечаток (оценщик 28.09 таких не
  видел). Нормализует инструмент только период оракула; такую запись не
  обходить, а прислать вывод — это разбор, а не повод ослабить проверку.
- Копии базы остаются в `C:\transport-report\backups\dji-area\retained_closeout_*`
  (по каталогу на прогон, путь — `backup.path` вердикта); удалять их —
  решение владельца.

## Откат

**Код.** `git revert -m 1` мерж-коммита PR правила и затем fast-forward
production на коммит отката — вперёд, не `reset`. Правило выборочное: после
отката пересчёт даёт затронутым строкам прежний отпечаток, и
`insert_calculation` возвращает прежнюю строку (`reactivated`), ничего не
удаляя. Прочие строки отката не замечают — их отпечаток правило не трогало.
Инструмент закрытия продукт не импортирует: его откат — удалить файл.

**Данные.** Схема не менялась, миграций нет. Строки расчёта append-only:
строка правила закрывается `superseded_at`, прежняя возвращается. Строки
нормализации откатывать не нужно: результат у них прежний, а код и до, и
после выпуска видит их `unchanged`. Полный возврат базы — копия из
`backup.path` вердикта соответствующего блока (R1 — до нормализации, R2 — до
деплоя и перехода).
