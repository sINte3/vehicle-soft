# DRONE-CARD-COVERAGE-001 — пилот: сколько дают недостающие карточки вылетов DJI

Трек Дронов. Статус: **подготовка — аудит, инструмент, замороженная выборка;
живого сбора не было.** Сбор начинается только по отдельному сообщению
владельца после шага B0.

## 1. Проблема

Паспорт поля (`DRONE-FIELD-PASSPORT-001`, на production с выпуска v1.21)
признаёт работу поля подтверждённой только по текущей привязке вылета
EXACT (TIER1) или IDENTIFIED (TIER2). Ключ поля для привязки берётся из
карточки вылета DJI (`card_geometry_md5`). Перепись сентября 2026 на
копии production (UAT 02.10.2026):

| | вылетов | доля |
|---|---|---|
| всего | 7 240 | |
| подтверждено (EXACT 115 + IDENTIFIED 147) | 262 | 3,6 % |
| карточка не собрана (NO_CARD) | 6 067 | 83,8 % |
| ключа поля нет в карточке (NO_KEY) | 433 | |
| ключа нет в каталоге (NOT_IN_CATALOG) | 470 | |
| привязка не рассчитана | 8 | |

Август: 8 196 вылетов, подтверждено 2 110 (25,7 %), NO_CARD 43,8 %.

Главное узкое место сентября — несобранные карточки, а не каталог полей и
не паспорт. Но карточка не обязательно даёт подтверждение: в ней может не
быть ключа (NO_KEY), ключа может не быть в каталоге (NOT_IN_CATALOG).
Пилот измеряет это на ограниченной выборке, прежде чем решать про
исторический сбор или ежедневный сбор карточек.

## 2. Базовая линия

Числа UAT — ориентир. Базовая линия пилота — **новая перепись свежей
online-копии production** (шаг B0 ниже): сентябрь и август,
`tools/dji_field_census.py`, плюс построчная когорта NO_CARD сентября и
контрольная когорта (карточка уже есть). Копия B0 потом становится базой
площадки: состояния «до» манифеста и базы пилота совпадают байт в байт
(сверяется отпечатком).

## 3. Гипотеза и что уже видно

Среди сентябрьских вылетов, у которых карточка уже есть (1 173),
подтверждено 262 = 22,3 %; NO_KEY 433 (37 %); NOT_IN_CATALOG 470 (40 %).
В августе — 2 110 из 4 605 = 45,8 %.

**Это не оценка для NO_CARD.** Карточки сентября собирались не случайно:
суточный манифест берёт кандидатов экрана контроля площади плюс малую
контрольную выборку (6–42 вылета в сутки). Поэтому пилот берёт
стратифицированную выборку именно из NO_CARD. Контрольная когорта
показывается рядом, перевзвешенная на слои NO_CARD. Отбор по аномалии
перевзвешивание не убирает, и это остаётся оговоркой.

Рабочая гипотеза: конверсия NO_CARD → подтверждено лежит где-то между
20 и 45 %. Пилот на 500 вылетах сужает её до ±4–5 п. п.

## 4. Как сейчас собирается карточка (аудит репозитория)

**Новый сборщик не нужен.** Всё, что нужно для сбора по явному списку,
уже есть и работает на production:

| Что | Где |
|---|---|
| сбор | `python -m drone_collector.main --sources --ids-file FILE [--send-sources]` |
| что делает одно посещение | открывает `https://www.djiag.com/record/<id>`; страница кабинета сама запрашивает карточку (`GET …/api/web/v1/flight_records/<id>`), маршрут (`POST …/flight_datas/flight_records`), дескриптор (`GET …/api/web/v2/airlines/<id>`) и V4-файл по подписанной ссылке. Сборщик слушает и хранит байты, а не запрашивает сам. Исключение — один прямой запрос дескриптора, если страница его не попросила (`drone_collector/sources.py`) |
| по списку | `--ids-file`: номер вылета в строке, `#` — комментарий. Период и только отсутствующие — `--from/--to` (обход списка) или манифест |
| возобновление | вылет, чьи источники уже в очереди (`pending` или `sent`), повторно не посещается: повтор того же файла идёт только к недостающим |
| темп | пауза между вылетами `DJI_SOURCE_PAUSE_MS` = 1 500 мс, ожидание V4 до `DJI_SOURCE_WAIT_MS` = 70 с; три неоткрывшиеся страницы подряд — остановка («the browser is not usable») |
| сессия | сохранённая сессия DJI (`--save-session`); вылет на страницу входа — `SessionExpired`, код 2 |
| блокировка | `drone_collector/data/collector.lock`: два прогона на одной сессии не идут одновременно |
| очередь | `DRONE_OUTBOX_DIR`, атомарная и идемпотентная; отправка — отдельный флаг `--send-sources` |
| приём | `POST /drones/api/source_sync`: неизменяемая ревизия (повтор тех же байт — `duplicates`), затем `refresh_flight_evidence` для вылета — `card_revision_id`, `card_geometry_md5` |
| привязка | `tools/dji_area_recalc.py --apply --flight-id …` (пакет `dji_area.pipeline`): append-only строки `dji_area_calculations` и `dji_field_attributions`, прежняя текущая закрывается `superseded_at`. `drone_flights.area_ha` не читается и не пишется, `drone_area_decisions` не пишутся |
| перепись | `tools/dji_field_census.py`, `dji_area.field_view.classify` — те же функции, что у экранов |

Почему у 84 % сентября нет карточки: production собирает источники
**адресно** — суточный цикл `tools/dji_area_daily.py` берёт у
`/drones/api/area_capture_manifest` только кандидатов экрана и малую
контрольную выборку. Полный сбор по парку — около 270 посещений в сутки, и
он владельцем не одобрен.

Путь карточки целиком: DJI → тело карточки (`dji_source_revisions`,
`source_type='card'`) → указатели вылета (`dji_flight_evidence`) →
пересчёт привязки (`dji_field_attributions`) → перепись и паспорт. Пока
пересчёта нет, вылет с полученной карточкой виден как
`KEY_AFTER_RESOLUTION` или `NO_KEY`, а не как подтверждённый. Сквозной тест
это проверяет.

**Новый код — только инструмент пилота**
`tools/dji_card_coverage_pilot.py`. Он только читает: замороженная
выборка, отпечатки «до», разбор журнала сборщика, сверка «после». К DJI он
не обращается, сборщик и приёмник не трогает.

## 5. Границы безопасности

* **Production не меняется ни одной операцией.** Шаг B0 только читает
  production: онлайн-копия базы (`backup_transport_db.py`, SQLite backup
  API) и `git rev-parse`. Код пилота берётся в отдельную папку
  `C:\VehicleSoft_CardPilot\src`. HEAD и службы production сверяются до и
  после.
* **Сбор — с рабочей машины, как блок W квалификации 21.09.2026.** Сессия
  DJI и `.env` holdout-сборщика уже смотрят на площадку (порт 5051).
  Production-сборщик, его очередь, сессия и расписание не трогаются. Блок
  сбора отказывается, если адрес приёмника не `:5051` — в `.env` или в
  окружении процесса. Очередь пилота — своя папка.
* **Пересчёт** — только `--db` базы площадки, при остановленной службе
  площадки.
* **Инструмент пилота** отказывается открывать базу в папке
  `transport-report`. Отсутствующую базу он не создаёт, открывает только
  `mode=ro`.
* **Ворота неизменности** (`measure`) — код 5, если нарушено хоть одно:
  * RAW (`area_ha`) всех вылетов прежний;
  * `drone_area_decisions` прежние;
  * прежние ревизии источников байт в байт;
  * реестр миграций прежний;
  * у вылетов вне этапа не изменились ни привязка, ни расчёт, ни карточка.
* **Остановка, а не обход защиты.** Признаки 403/429, капча, истёкшая
  сессия или остановка браузера → `collector-stats` даёт код 6, пилот
  останавливается.
* **Миграций, таблиц, колонок нет.** Манифест и отчёты — файлы.

## 6. Замороженная выборка

* **Когорта** — все вылеты сентября (UTC+5) в состоянии UNRESOLVED/NO_CARD
  на копии B0.
* **Слои** — борт (`drone_units.number`; без борта — короткий хэш ника) ×
  ISO-неделя местной даты.
* **Размещение** — пропорционально размеру слоя, методом наибольшего
  остатка. Маленький слой не раздувается, слой не получает больше, чем в
  нём есть.
* **Внутри слоя** — наименьшие ключи `sha256("DRONE-CARD-COVERAGE-001|" +
  flight_id)`. Не `random`, не ручной выбор, не порядок строк.
* **Размер** — `min(500, когорта)`. Потолок 500 зашит в инструмент:
  `--cap` его уменьшает, но не превышает.
* **Порядок манифеста** — по доле ранга внутри слоя. Любой префикс сам
  распределён по слоям пропорционально; канарейка = первые 50.
* **Заморозка** — `pilot_manifest.csv` и его sha256 в `plan.json`.
  Повторный `plan` с тем же входом даёт те же байты; с другим входом —
  отказ. Неудобные вылеты не заменяются, отказ сбора остаётся в
  знаменателе.
* **Контроль** — вылеты сентября, у которых карточка уже есть, в тех же
  слоях (`control_with_card.csv`).

## 7. Канарейка

Первые 50 вылетов манифеста, тем же сборщиком и темпом, без ускорения.
После неё — сверка (`collector-stats` и `measure --stage canary`) и решение
владельца, продолжать ли до 500. Повтор файла 500 идёт тем же журналом
очереди и канарейку не посещает второй раз.

## 8. Результаты пилота

Ожидают сбора.

## 9. Перепись до и после

До — шаг B0. После — `measure` и перепись базы площадки после пересчёта.

## 10. Нагрузка на DJI — оценка до сбора

Одно посещение — это запросы, которые страница кабинета делает сама при
открытии записи вылета. Приблизительно 4 на вылет: карточка, маршрут,
дескриптор, V4 до 1 МБ. Изредка ещё один прямой запрос дескриптора и один
контрольный на прогон.

| | посещений | запросов к API DJI, порядок | время, оценка |
|---|---|---|---|
| канарейка | 50 | ~200 (не больше ~250) | 10–25 мин |
| пилот целиком | 500 | ~2 000 (не больше ~2 500) | 1,5–4 ч |
| для сравнения: суточный адресный сбор | 6–42 | 24–170 | — |
| для сравнения: весь парк за сутки | ~270 | ~1 100 | — |

Время на посещение до пилота не измерено, канарейка его меряет. Опора
оценки — пауза 1,5 с, загрузка страницы и ожидание V4. Потолок — 70 с на
вылет, если V4 не приходит: тогда 500 вылетов заняли бы до 10 ч, и это
признак остановиться.

## 11. Статистическая проекция — метод

* Конверсия в подтверждённые считается двумя способами: среди получивших
  карточку и среди всего манифеста (отказы сбора остаются в знаменателе).
  Оба — с 95 % интервалом Уилсона.
* Постстратифицированная оценка: веса — доли слоёв в когорте NO_CARD.
* Проекция на всю когорту: `доля × NO_CARD` по точке и обеим границам
  интервала, и итоговое покрытие сентября. Это проекция, не факт.
* Точность при 500 вылетах и доле около 30 % — около ±4 п. п.; на
  канарейке — около ±12 п. п.

## 12. NO_KEY и NOT_IN_CATALOG

Пилот решает только NO_CARD. Если после карточки большая часть уходит в
NO_KEY или NOT_IN_CATALOG, это отдельный следующий анализ (данные полей),
а не повод расширять сбор карточек.

## 13. Рекомендация

После пилота.

## 14. Блоки владельца

Порядок. Каждый блок — один, вывод присылается целиком, следующий выдаётся
после разбора:

| Шаг | Где | Что | Пишет |
|---|---|---|---|
| B0 | SRV-YOQSH | онлайн-копия production, перепись сентября и августа, замороженная выборка, отпечатки | только папка пилота на D: и C:\VehicleSoft_CardPilot |
| B1 | SRV-YOQSH | после мержа PR занятия площадки: копия и HEAD площадки, остановка службы и ботов площадки, деплой закреплённой ревизии, база площадки = копия B0 (сверка отпечатком), пуск службы | площадка |
| W1 | рабочая машина | канарейка 50: `--sources --ids-file canary_ids.txt --send-sources` только на `:5051`, своя очередь, разбор журнала | площадка, DJI — 50 посещений |
| S1 | SRV-YOQSH | служба площадки стоп → `dji_area_recalc.py --apply --flight-id` (50) → `measure --stage canary` → пуск | площадка |
| W2 | рабочая машина | только по решению владельца: тот же сбор по `pilot_ids.txt` (канарейка пропускается) | площадка, DJI — до 450 посещений |
| S2 | SRV-YOQSH | пересчёт 500, `measure --stage pilot`, случаи для проверки глазами | площадка |
| R | SRV-YOQSH | восстановление базы, HEAD и ботов площадки; production-сверка | площадка |

Список вылетов на рабочую машину передаётся через блок. B0 печатает
номера, блок W пишет файл и сверяет его sha256 с `plan.json`.

### B0 — SRV-YOQSH: базовая линия и замороженная выборка (только чтение production)

К DJI не обращается, площадку не трогает. Пишет только:

* `D:\transport-report-backups\staging\card_pilot\baseline_<время>` —
  онлайн-копия базы, перепись в JSON, папка `plan`;
* `C:\VehicleSoft_CardPilot` — клон закреплённой ревизии пилота и журнал
  блока.

```powershell
& {
$ErrorActionPreference = 'Continue'
$ProgressPreference = 'SilentlyContinue'
$prod      = 'C:\transport-report'
$db        = 'C:\transport-report\instance\transport.db'
$py        = 'C:\Program Files\Python314\python.exe'
$services  = @('TransportReport', 'TransportBot', 'TransportBot003')
$expected  = '8df568394a840054ef6f842c6a8b272ca4c31aa8'
$branch    = 'claude/practical-davinci-chb4r7'
$pin       = '39eab503069b7bb01a8342542edcbebbfc2210c2'
$pilotRoot = 'D:\transport-report-backups\staging\card_pilot'
$work      = 'C:\VehicleSoft_CardPilot'
$src       = 'C:\VehicleSoft_CardPilot\src'
function Invoke-Tool([string]$exe, [string[]]$arguments) {
  $lines = @(& $exe @arguments 2>&1 | ForEach-Object { "$_" })
  $code = $LASTEXITCODE
  foreach ($line in $lines) { Write-Host "    $line" }
  return [pscustomobject]@{ Code = $code; Lines = $lines }
}
function Get-States() {
  return (@($services | ForEach-Object { $s = Get-Service -Name $_ -ErrorAction SilentlyContinue; if ($s) { "$_=$($s.Status)" } else { "$_=not found" } }) -join ', ')
}
$stamp = (Get-Date).ToString('yyyyMMdd_HHmmss')
$run = Join-Path $pilotRoot ('baseline_' + $stamp)
$log = Join-Path $work ('card_pilot_b0_' + $stamp + '.log')
New-Item -ItemType Directory -Force -Path $work | Out-Null
try { Start-Transcript -Path $log -Append | Out-Null } catch { Write-Host 'NOTE: the log file could not be started' }
$failure = $null
try {
  Write-Host '== 1. Checks - nothing is changed'
  if ((hostname) -ne 'srv-yoqsh') { throw "this computer is $(hostname), not SRV-YOQSH" }
  foreach ($path in @($db, $py, (Join-Path $prod 'backup_transport_db.py'))) {
    if (-not (Test-Path -LiteralPath $path)) { throw "not found: $path" }
  }
  $size = (Get-Item -LiteralPath $db).Length
  $freeD = (Get-PSDrive -Name D -ErrorAction SilentlyContinue).Free
  $freeC = (Get-PSDrive -Name C -ErrorAction SilentlyContinue).Free
  Write-Host "PRODUCTION DB BYTES: $size"
  Write-Host "FREE D: $freeD"
  Write-Host "FREE C: $freeC"
  if ((-not $freeD) -or ($freeD -lt (2 * $size + 300MB))) { throw 'drive D: has not enough space for the baseline copy' }
  if ((-not $freeC) -or ($freeC -lt 1GB)) { throw 'drive C: has less than 1 GB free' }
  $headBefore = [string](& git -C $prod rev-parse HEAD)
  $statesBefore = Get-States
  Write-Host "PRODUCTION HEAD: $headBefore"
  Write-Host "PRODUCTION SERVICES: $statesBefore"
  if ($headBefore -ne $expected) { throw "production is at $headBefore, the pilot was prepared for $expected - send this output" }

  Write-Host '== 2. The reviewed pilot code in its own folder (production is only read)'
  $origin = [string](& git -C $prod remote get-url origin)
  if ((-not $origin) -or ($LASTEXITCODE -ne 0)) { throw 'the GitHub address of the production repository could not be read' }
  if (-not (Test-Path -LiteralPath $src)) {
    & git -c advice.detachedHead=false clone --quiet $prod $src
    if ($LASTEXITCODE -ne 0) { throw "git clone into $src failed (exit $LASTEXITCODE)" }
  }
  & git -C $src fetch --quiet $origin $branch
  if ($LASTEXITCODE -ne 0) { throw "git fetch of $branch failed (exit $LASTEXITCODE) - is there a connection to GitHub?" }
  $dirty = @(& git -C $src status --porcelain --untracked-files=no)
  if ($dirty.Count -gt 0) { throw "the pilot folder $src has edited files: $($dirty -join '; ')" }
  & git -C $src checkout --quiet --detach $pin
  if ($LASTEXITCODE -ne 0) { throw "the reviewed commit $pin is not available (exit $LASTEXITCODE)" }
  $srcHead = [string](& git -C $src rev-parse HEAD)
  if ($srcHead -ne $pin) { throw "the pilot folder is at $srcHead, expected $pin" }
  Write-Host "PILOT CODE: $srcHead"

  Write-Host '== 3. Online copy of the production database (SQLite backup API)'
  $snapDir = Join-Path $run 'snapshot'
  $bk = Invoke-Tool $py @((Join-Path $prod 'backup_transport_db.py'), '--source', $db, '--dest-dir', $snapDir, '--suffix', 'card_pilot_baseline')
  $snap = $null
  $integrity = $false
  for ($i = 0; $i -lt $bk.Lines.Count; $i++) {
    if ($bk.Lines[$i] -match 'Integrity check : ok') { $integrity = $true }
    if (($bk.Lines[$i] -match '^SUCCESS: Backup written to:') -and ($i + 1 -lt $bk.Lines.Count)) { $snap = $bk.Lines[$i + 1].Trim() }
  }
  if (($bk.Code -ne 0) -or (-not $integrity) -or (-not $snap) -or (-not (Test-Path -LiteralPath $snap))) { throw "the online copy did not pass (exit $($bk.Code))" }
  Write-Host "SNAPSHOT: $snap ($((Get-Item -LiteralPath $snap).Length) bytes, integrity ok)"

  Write-Host '== 4. Census of the copy - September and August (read only)'
  Set-Location -LiteralPath $src
  foreach ($month in @(@('2026-09-01', '2026-09-30', 'census_2026_09.json'), @('2026-08-01', '2026-08-31', 'census_2026_08.json'))) {
    $c = Invoke-Tool $py @('tools\dji_field_census.py', '--db', $snap, '--from', $month[0], '--to', $month[1], '--json', (Join-Path $run $month[2]))
    if ($c.Code -ne 0) { throw "the census $($month[0]) .. $($month[1]) failed (exit $($c.Code))" }
  }

  Write-Host '== 5. Frozen pilot sample and fingerprints (read only)'
  $planDir = Join-Path $run 'plan'
  $p = Invoke-Tool $py @('tools\dji_card_coverage_pilot.py', 'plan', '--db', $snap, '--out-dir', $planDir)
  if ($p.Code -ne 0) { throw "the pilot plan failed (exit $($p.Code))" }
  $f = Invoke-Tool $py @('tools\dji_card_coverage_pilot.py', 'fingerprint', '--db', $snap, '--out', (Join-Path $planDir 'fingerprint_before.json'))
  if ($f.Code -ne 0) { throw "the fingerprint failed (exit $($f.Code))" }
  foreach ($name in @('canary_ids.txt', 'pilot_ids.txt')) {
    $ids = @(Get-Content -LiteralPath (Join-Path $planDir $name) | Where-Object { $_ -notmatch '^\s*#' -and $_.Trim() })
    Write-Host "$($name.ToUpper()) ($($ids.Count)):"
    for ($k = 0; $k -lt $ids.Count; $k += 10) { Write-Host ('  ' + (($ids[$k..([math]::Min($k + 9, $ids.Count - 1))]) -join ' ')) }
  }

  Write-Host '== 6. Production after - must be exactly as before'
  $headAfter = [string](& git -C $prod rev-parse HEAD)
  $statesAfter = Get-States
  Write-Host "PRODUCTION HEAD AFTER: $headAfter"
  Write-Host "PRODUCTION SERVICES AFTER: $statesAfter"
  if ($headAfter -ne $headBefore) { throw 'the production HEAD changed during this block' }
  if ($statesAfter -ne $statesBefore) { throw 'the production services changed during this block' }
  Write-Host "PILOT FOLDER: $run"
} catch {
  $failure = $_.Exception.Message
}
if ($failure) { Write-Host "STEP=STOP - $failure" } else { Write-Host 'STEP=PASS' }
Write-Host "LOG FILE: $log"
try { Stop-Transcript | Out-Null } catch { }
}
```

**Ожидается** в конце `STEP=PASS`. Выше — по порядку:

* `PRODUCTION HEAD: 8df5683…` и три службы `Running`, до и после;
* `PILOT CODE: 39eab50…`;
* `SNAPSHOT: … integrity ok`;
* две переписи — сентябрь и август;
* строки плана: `COHORT NO_CARD`, `CONTROL WITH CARD`, `SAMPLE 500 of cap
  500, canary 50`, `MANIFEST SHA256`;
* строки отпечатков: RAW, решения, ревизии источников;
* номера канарейки и пилота.

Блок останавливается до копии базы, если:

* production не на `8df5683`;
* в папке пилота есть правки;
* закреплённой ревизии нет.

Прислать весь вывод.

## 15. Откат и восстановление

* Production не меняется — откатывать нечего.
* Площадка восстанавливается блоком R: база из копии B1, HEAD из записи
  B1, службы и боты — как были. Затем — docs-only PR освобождения
  `docs/STAGING.md`.
* Папки пилота (`D:\…\card_pilot`, `C:\VehicleSoft_CardPilot`) — только
  файлы пилота; удалять их или нет, решает владелец после отчёта.

## 16. Инженерный вердикт

После пилота.
