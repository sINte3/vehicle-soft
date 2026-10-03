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
* **На всё время пилота на площадке выключено всё, что пишет в её базу
  помимо пилота** (шаг B1):
  * боты площадки остановлены и переведены в `Disabled` — копия production
    несёт очередь уведомлений, бот разослал бы их живым людям;
  * кнопка «Обновить данные DJI» площадки выключена: `DJI_REFRESH_LAUNCHER`
    убрана из окружения службы. В копии production — пользователи
    production; одно нажатие запустило бы сбор и пересчёт вне манифеста и
    лишние посещения DJI. Строка сохранена, блок R её возвращает;
  * задачи планировщика: B1 печатает все включённые (кроме
    `\Microsoft\`) — имя, состояние, вид, исполняемый файл, рабочую папку,
    без аргументов. Отказ — если включённая задача относится к площадке
    (имя со `Staging`, путь площадки, `:5051`) и не является известной
    резервной копией площадки, или упоминает `VehicleSoft_` / `Holdout`.
    Строки вида `other` разбираются по выводу до канарейки;
  * другие службы площадки — остановлены и не запускаются сами (`Manual` /
    `Disabled`), иначе отказ.
* **Чего B1 не закрывает, и чем это ловится.** Приёмники `/drones/api/*`
  площадки принимают данные от любого владельца токена площадки. На время
  пилота на площадку шлёт только сбор W1/W2; разведка рабочей машины перед
  канарейкой проверяет, что там нет задач holdout-сборщика по расписанию.
  Каталог полей (`dji_land_*`) отпечаток пилота не покрывает — S1/S2
  сверяют его с копией B0 до пересчёта.
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
| B1 | SRV-YOQSH | после мержа PR занятия площадки: всё для точного возврата и две онлайн-копии базы площадки, боты площадки — стоп и `Disabled`, кнопка DJI площадки выключена, закреплённая ревизия, база площадки = копия B0 (сверка sha256 и отпечатком до и после пуска), пуск только службы | площадка |
| D1 | SRV-YOQSH | только чтение: задача планировщика `DjiAreaRefreshStaging`, на которой встал первый B1 — действие, триггеры, учётная запись, прогоны, что она пишет в базу площадки, связь с кнопкой | только журнал блока в C:\VehicleSoft_CardPilot |
| W1 | рабочая машина | канарейка 50: `--sources --ids-file canary_ids.txt --send-sources` только на `:5051`, своя очередь, разбор журнала | площадка, DJI — 50 посещений |
| S1 | SRV-YOQSH | служба площадки стоп → `dji_area_recalc.py --apply --flight-id` (50) → `measure --stage canary` → пуск | площадка |
| W2 | рабочая машина | только по решению владельца: тот же сбор по `pilot_ids.txt` (канарейка пропускается) | площадка, DJI — до 450 посещений |
| S2 | SRV-YOQSH | пересчёт 500, `measure --stage pilot`, случаи для проверки глазами | площадка |
| R | SRV-YOQSH | копия базы пилота сохраняется; база, HEAD, окружение и службы площадки — как до B1; production-сверка | площадка |

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

### B1 — SRV-YOQSH: площадка под пилот

Production только читается (HEAD и три службы — до и после). Площадку
меняет так:

1. **Ничего не меняя, проверяет:**
   * копию B0 — размер, пустой `-wal`, sha256 равен `plan.json`;
   * замороженные файлы — sha256 манифеста, канарейки и пилота против
     `plan.json`; манифест и канарейка — ещё и против значений B0;
   * production — HEAD `8df5683`, все три службы `Running`;
   * строку `docs/STAGING.md` на `origin/main` — ровно одна строка,
     «да», `DRONE-CARD-COVERAGE-001 / historical pilot:`;
   * закреплённую ревизию — есть на ветке, от `8df5683` отличается только
     документами, тестами, CI и инструментом пилота (8 файлов;
     пустое сравнение — тоже отказ);
   * службы площадки — `Running` или `Stopped` (иначе R не сможет вернуть
     их точно); другие службы площадки и задачи планировщика (§5);
   * окружение службы площадки читается (печатаются только имена
     переменных); `DJI_REFRESH_LAUNCHER` в окружении машины, в
     `AppEnvironment` или в значении `Environment` службы — отказ.
2. **Пишет всё для точного возврата** в
   `D:\transport-report-backups\staging\card_pilot\staging_<время>`:
   HEAD и ветку, состояние и тип запуска трёх служб, размер базы,
   production HEAD и службы. Затем — первая онлайн-копия базы площадки
   (`staging_before`, с `integrity_check`, sha256 в
   `staging_backup.txt`).
3. **Боты** — `Disabled` и стоп, затем стоп службы площадки и проверка,
   что базу никто не держит. `DJI_REFRESH_LAUNCHER` убирается из
   окружения службы: в `refresh_launcher.txt` — эта строка и её номер, в
   `env_extra_before.txt` — число строк и sha256 всего списка (значения
   других переменных не печатаются и не сохраняются). Вторая онлайн-копия
   (`staging_final`) — это то, что R вернёт: после остановки служб в базу
   больше никто не пишет.
4. **Ревизия пилота** `39eab50`, `compileall`, тесты инструмента и ядра
   паспорта.
5. **База площадки = копия B0**: проверка блокировки, файл копируется,
   sha256 равен копии (`placed.txt`), отпечаток (`fingerprint`) равен
   `plan\fingerprint_before.json` байт в байт, реестр — 60 миграций.
   Миграции не запускаются.
6. **Пуск только службы площадки**: `/login` — 200, `/drones/fields` без
   входа ведёт на форму входа. Отпечаток после пуска снова равен B0,
   реестр — 60. Боты — `Stopped` и `Disabled`.
7. **Production после**: HEAD и три службы `Running` — как до.

Если блок встал до первого изменения площадки, он сам закрывает свою папку
(`returned.txt`) и печатает `STAGING_CHANGED=no`; повторить можно сразу.
Если после — `STAGING_CHANGED=yes`, площадку возвращает блок R.

```powershell
& {
  $ErrorActionPreference = 'Stop'
  $ProgressPreference = 'SilentlyContinue'
  $expectedHost = 'srv-yoqsh'
  $root         = 'C:\transport-report-staging'
  $prodRoot     = 'C:\transport-report'
  $python       = 'C:\Program Files\Python314\python.exe'
  $service      = 'TransportReportStaging'
  $bots         = @('TransportBotStaging', 'TransportBot003Staging')
  $prodNames    = @('TransportBot', 'TransportBot003', 'TransportReport')
  $prodExpected = '8df568394a840054ef6f842c6a8b272ca4c31aa8'
  $branch       = 'claude/practical-davinci-chb4r7'
  $pin          = '39eab503069b7bb01a8342542edcbebbfc2210c2'
  $runRoot      = 'D:\transport-report-backups\staging\card_pilot'
  $baseline     = 'D:\transport-report-backups\staging\card_pilot\baseline_20261003_072956'
  $snapshot     = 'D:\transport-report-backups\staging\card_pilot\baseline_20261003_072956\snapshot\transport_20261003_073000_card_pilot_baseline.db'
  $snapBytes    = 503255040
  $manifestSha  = '1782d19899ed1e06345e859542d4705d6ac750d6ac57078577f5c03ad6dc66f8'
  $canarySha    = '5913a88d1bfcecdfe0586fd0a81007ef7cc771d777a2754ebd8b5c1ec2e641da'
  $site         = 'http://10.103.25.14:5051'
  $knownTasks   = @('TransportDBBackupStaging')
  $db           = 'C:\transport-report-staging\instance\transport.db'
  $work         = 'C:\VehicleSoft_CardPilot'
  $svcKey       = 'HKLM:\SYSTEM\CurrentControlSet\Services'
  $machineKey   = 'HKLM:\SYSTEM\CurrentControlSet\Control\Session Manager\Environment'
  $planDir      = Join-Path $baseline 'plan'
  $stamp        = Get-Date -Format 'yyyyMMdd_HHmmss'
  $runDir       = Join-Path $runRoot ('staging_' + $stamp)
  $siteParams   = $svcKey + '\' + $service + '\Parameters'
  $prodWant     = 'TransportBot=Running TransportBot003=Running TransportReport=Running'
  function Get-ProdServices { (@($prodNames | ForEach-Object { $s = Get-Service -Name $_ -ErrorAction SilentlyContinue; if ($s) { $_ + '=' + $s.Status } else { $_ + '=missing' } }) -join ' ') }
  function Get-ListHash([string[]]$list) { $h = [System.Security.Cryptography.SHA256]::Create(); -join ($h.ComputeHash([System.Text.Encoding]::UTF8.GetBytes(($list -join "`n"))) | ForEach-Object { $_.ToString('x2') }) }
  function Test-SameFile([string]$a, [string]$b) { (Get-FileHash -LiteralPath $a -Algorithm SHA256).Hash -eq (Get-FileHash -LiteralPath $b -Algorithm SHA256).Hash }
  function Save-Backup([string]$label) {
    $dir = Join-Path $runDir $label
    New-Item -ItemType Directory -Force -Path $dir | Out-Null
    $out = & $python (Join-Path $root 'backup_transport_db.py') --source $db --dest-dir $dir --suffix $label | Out-String
    if (($LASTEXITCODE -ne 0) -or ($out -notmatch 'Integrity check : ok')) { throw "STEP FAILED: online backup $label of the staging database -- $out" }
    $file = @(Get-ChildItem -LiteralPath $dir -Filter '*.db')
    if ($file.Count -ne 1) { throw "STEP FAILED: expected one backup file in $dir, found $($file.Count)" }
    $hash = (Get-FileHash -LiteralPath $file[0].FullName -Algorithm SHA256).Hash.ToLower()
    Add-Content -LiteralPath (Join-Path $runDir 'staging_backup.txt') -Value ($label + '|' + $file[0].FullName + '|' + $file[0].Length + '|' + $hash) -Encoding ASCII
    Write-Output ("STAGING_BACKUP " + $label + " " + $file[0].FullName + " BYTES=" + $file[0].Length + " SHA256=" + $hash + " integrity=ok")
  }
  New-Item -ItemType Directory -Force -Path $work | Out-Null
  $log = Join-Path $work ('card_pilot_b1_' + $stamp + '.log')
  try { Start-Transcript -Path $log -Append | Out-Null } catch { Write-Output 'NOTE: the log file could not be started' }
  $touched = $false
  $failure = $null
  try {
    Write-Output '== 1. Checks - nothing is changed yet'
    if ((hostname) -ne $expectedHost) { throw "STEP FAILED: host is $(hostname), expected $expectedHost" }
    foreach ($path in @($db, $python, $snapshot, (Join-Path $planDir 'plan.json'), (Join-Path $planDir 'pilot_manifest.csv'), (Join-Path $planDir 'canary_ids.txt'), (Join-Path $planDir 'pilot_ids.txt'), (Join-Path $planDir 'fingerprint_before.json'))) {
      if (-not (Test-Path -LiteralPath $path)) { throw "STEP FAILED: not found: $path" }
    }
    $open = @(Get-ChildItem -LiteralPath $runRoot -Directory -Filter 'staging_*' -ErrorAction SilentlyContinue | Where-Object { -not (Test-Path -LiteralPath (Join-Path $_.FullName 'returned.txt')) })
    if ($open.Count -gt 0) { throw "STEP FAILED: staging run $($open[0].Name) is still open (no returned.txt) -- send this output" }

    $plan = Get-Content -LiteralPath (Join-Path $planDir 'plan.json') -Raw | ConvertFrom-Json
    if ($plan.sample.manifest_sha256 -ne $manifestSha) { throw "STEP FAILED: plan.json names manifest $($plan.sample.manifest_sha256), B0 printed $manifestSha" }
    if ($plan.sample.canary_ids_sha256 -ne $canarySha) { throw "STEP FAILED: plan.json names canary ids $($plan.sample.canary_ids_sha256), B0 printed $canarySha" }
    foreach ($f in @(@('pilot_manifest.csv', $plan.sample.manifest_sha256), @('canary_ids.txt', $plan.sample.canary_ids_sha256), @('pilot_ids.txt', $plan.sample.pilot_ids_sha256))) {
      $h = (Get-FileHash -LiteralPath (Join-Path $planDir $f[0]) -Algorithm SHA256).Hash
      if ($h -ne $f[1]) { throw "STEP FAILED: $($f[0]) has sha256 $h, frozen $($f[1])" }
      Write-Output ("FROZEN " + $f[0] + " SHA256=" + $h.ToLower())
    }
    if (([int]$plan.sample.size -ne 500) -or ([int]$plan.sample.canary -ne 50)) { throw "STEP FAILED: plan.json says sample $($plan.sample.size), canary $($plan.sample.canary)" }
    $snapItem = Get-Item -LiteralPath $snapshot
    if ($snapItem.Length -ne $snapBytes) { throw "STEP FAILED: the B0 snapshot is $($snapItem.Length) bytes, B0 wrote $snapBytes" }
    $snapWal = $snapshot + '-wal'
    if ((Test-Path -LiteralPath $snapWal) -and ((Get-Item -LiteralPath $snapWal).Length -gt 0)) { throw 'STEP FAILED: the B0 snapshot has a non-empty -wal next to it -- send this output' }
    $snapSha = (Get-FileHash -LiteralPath $snapshot -Algorithm SHA256).Hash
    if ($snapSha -ne $plan.database.sha256) { throw "STEP FAILED: the B0 snapshot has sha256 $snapSha, the frozen plan was made on $($plan.database.sha256)" }
    Write-Output ("B0_SNAPSHOT=" + $snapshot + " BYTES=" + $snapItem.Length + " SHA256=" + $snapSha.ToLower() + " (equals plan.json)")

    $prodHead = [string](git -C $prodRoot rev-parse HEAD)
    $prodServices = Get-ProdServices
    Write-Output ("PROD_HEAD=" + $prodHead)
    Write-Output ("PROD_SERVICES=" + $prodServices)
    if ($prodHead -ne $prodExpected) { throw "STEP FAILED: production is at $prodHead, the pilot was prepared for $prodExpected" }
    if ($prodServices -ne $prodWant) { throw 'STEP FAILED: not all three production services are Running -- send this output' }

    Set-Location -LiteralPath $root
    $before = [string](git rev-parse HEAD)
    $beforeRef = ('' + (git symbolic-ref -q HEAD)).Trim()
    $stagingBytes = (Get-Item -LiteralPath $db).Length
    Write-Output ("BEFORE_HEAD=" + $before + " REF=" + $(if ($beforeRef) { $beforeRef } else { 'detached' }))
    Write-Output ("STAGING_DB=" + $db + " BYTES=" + $stagingBytes)
    $changed = @(git status --porcelain --untracked-files=no)
    if ($changed.Count -gt 0) { throw "STEP FAILED: $($changed.Count) tracked file(s) changed in the staging checkout -- send the output of git status" }
    git fetch --quiet origin
    if ($LASTEXITCODE -ne 0) { throw 'STEP FAILED: git fetch' }
    git merge-base --is-ancestor $before origin/main
    if ($LASTEXITCODE -ne 0) { throw "STEP FAILED: staging runs $before, which is not in main -- someone may still use staging; send this line" }
    $psi = New-Object System.Diagnostics.ProcessStartInfo
    $psi.FileName = 'git'
    $psi.Arguments = 'show origin/main:docs/STAGING.md'
    $psi.WorkingDirectory = $root
    $psi.UseShellExecute = $false
    $psi.RedirectStandardOutput = $true
    $psi.StandardOutputEncoding = [System.Text.Encoding]::UTF8
    $proc = [System.Diagnostics.Process]::Start($psi)
    $stagingText = $proc.StandardOutput.ReadToEnd()
    $proc.WaitForExit()
    if ($proc.ExitCode -ne 0) { throw 'STEP FAILED: git show origin/main:docs/STAGING.md' }
    $stagingDoc = @($stagingText -split "`r?`n")
    $dataRows = @($stagingDoc | Where-Object { $_ -match '^\|' } | Select-Object -Skip 2)
    $yes = [string][char]0x0434 + [string][char]0x0430
    if ($dataRows.Count -ne 1) { throw "STEP FAILED: docs/STAGING.md on main has $($dataRows.Count) rows in the table, the rule is exactly one" }
    if (($dataRows[0].Split('|')[1].Trim() -ne $yes) -or ($dataRows[0] -notmatch 'DRONE-CARD-COVERAGE-001 / historical pilot:')) { throw 'STEP FAILED: docs/STAGING.md on main does not show staging occupied by DRONE-CARD-COVERAGE-001' }
    Write-Output 'STAGING_ROW=occupied by DRONE-CARD-COVERAGE-001 on origin/main (one row)'
    foreach ($c in @($pin, $prodExpected)) {
      git cat-file -e "$c^{commit}"
      if ($LASTEXITCODE -ne 0) { throw "STEP FAILED: commit $c not found after fetch" }
    }
    git merge-base --is-ancestor $pin "origin/$branch"
    if ($LASTEXITCODE -ne 0) { throw "STEP FAILED: $pin is not on origin/$branch" }
    $delta = @(git diff --no-renames --name-only $prodExpected $pin)
    if ($LASTEXITCODE -ne 0) { throw 'STEP FAILED: git diff of production and the pilot revision' }
    if ($delta -notcontains 'tools/dji_card_coverage_pilot.py') { throw 'STEP FAILED: the comparison with production does not show the pilot tool -- send this output' }
    $appDelta = @($delta | Where-Object { $_ -notmatch '^(docs/|tests/|\.github/|tools/dji_card_coverage_pilot\.py$)' })
    if ($appDelta.Count -gt 0) { throw "STEP FAILED: the pilot revision differs from production in application files: $($appDelta -join ', ')" }
    Write-Output ("PIN_VS_PRODUCTION=" + $delta.Count + " file(s), application files 0")

    $svcNames = @($service) + $bots
    $states = @()
    foreach ($name in $svcNames) {
      $s = Get-Service -Name $name -ErrorAction SilentlyContinue
      if (-not $s) { throw "STEP FAILED: service not found: $name" }
      $reg = Get-ItemProperty -LiteralPath ($svcKey + '\' + $name) -ErrorAction SilentlyContinue
      $delayed = if ($reg -and ($null -ne $reg.DelayedAutostart)) { [string]$reg.DelayedAutostart } else { '' }
      $states += ($s.Name + '|' + $s.Status + '|' + $s.StartType + '|' + $delayed)
      Write-Output ("SERVICE_BEFORE " + $s.Name + " " + $s.Status + " " + $s.StartType + " delayed=" + $delayed)
      if (@('Running', 'Stopped') -notcontains [string]$s.Status) { throw "STEP FAILED: $name is $($s.Status); it must be Running or Stopped before the pilot -- send this output" }
      if (($name -eq $service) -and $reg -and (@($reg.Environment) -match '^\s*DJI_REFRESH_LAUNCHER=').Count -gt 0) { throw 'STEP FAILED: the service Environment value of the staging site sets DJI_REFRESH_LAUNCHER -- send this output' }
    }
    $others = @(Get-Service | Where-Object { $_.Name -like '*Staging*' -and ($svcNames -notcontains $_.Name) })
    foreach ($o in $others) { Write-Output ("OTHER_STAGING_SERVICE=" + $o.Name + " " + $o.Status + " " + $o.StartType) }
    $liveOthers = @($others | Where-Object { ([string]$_.Status -ne 'Stopped') -or (@('Manual', 'Disabled') -notcontains [string]$_.StartType) })
    if ($liveOthers.Count -gt 0) { throw "STEP FAILED: other staging services are running or start by themselves and could touch the pilot database: $(($liveOthers | ForEach-Object { $_.Name }) -join ', ')" }

    $refused = @()
    $seen = 0
    foreach ($t in @(Get-ScheduledTask | Where-Object { ([string]$_.TaskPath -notlike '\Microsoft\*') -and ([string]$_.State -ne 'Disabled') })) {
      $acts = @($t.Actions)
      $exe = (@($acts | ForEach-Object { [string]$_.Execute }) -join ' ; ')
      $wds = @($acts | ForEach-Object { [string]$_.WorkingDirectory } | Where-Object { $_ })
      $text = (@($acts | ForEach-Object { [string]$_.Execute + ' ' + [string]$_.Arguments + ' ' + [string]$_.WorkingDirectory }) -join ' ')
      $seen++
      $kind = 'other'
      if (($text -match 'C:\\transport-report\\') -and (@($wds | Where-Object { $_ -notmatch '^C:\\transport-report(\\|$)' }).Count -eq 0)) { $kind = 'production' }
      if ($text -match 'VehicleSoft_|Holdout') { $kind = 'pilot-or-holdout' }
      if (($t.TaskName -match 'Staging') -or ($text -match 'transport-report-staging|:5051')) { $kind = 'staging' }
      $ok = (@('production', 'other') -contains $kind) -or (($knownTasks -contains $t.TaskName) -and ($text -match 'backup_transport_db\.py|backup_staging_db\.bat'))
      Write-Output ("TASK " + $t.TaskName + " " + $t.State + " " + $kind + " exe=" + $exe + " wd=" + ($wds -join ' ; ') + $(if ($ok) { '' } else { ' REFUSED' }))
      if (-not $ok) { $refused += $t.TaskName }
    }
    if ($refused.Count -gt 0) { throw "STEP FAILED: enabled scheduled task(s) that may write to staging: $($refused -join ', ') -- send this output" }
    Write-Output ("TASKS_CHECKED=" + $seen + " enabled (" + ($knownTasks -join ', ') + " only backs up the staging database; production tasks write production only; 'other' lines are reviewed before the canary)")

    $params = Get-ItemProperty -LiteralPath $siteParams -ErrorAction SilentlyContinue
    $extra = @()
    if ($params -and $params.AppEnvironmentExtra) { $extra = @($params.AppEnvironmentExtra) }
    if ($extra.Count -eq 0) { throw 'STEP FAILED: the environment of the staging site (NSSM AppEnvironmentExtra) could not be read -- send this output' }
    Write-Output ("SITE_ENV_NAMES=" + (@($extra | ForEach-Object { $n = ($_ -split '=', 2)[0].Trim(); if ($n -match '^[A-Za-z_][A-Za-z0-9_]*$') { $n } else { '?' } }) -join ',') + " (names only)")
    if ($params -and (@($params.AppEnvironment) -match '^\s*DJI_REFRESH_LAUNCHER=').Count -gt 0) { throw 'STEP FAILED: AppEnvironment of the staging site sets DJI_REFRESH_LAUNCHER -- send this output' }
    $machine = Get-ItemProperty -LiteralPath $machineKey -ErrorAction SilentlyContinue
    if ($machine -and ([string]$machine.DJI_REFRESH_LAUNCHER).Trim()) { throw 'STEP FAILED: the machine environment sets DJI_REFRESH_LAUNCHER, which reaches production too -- send this output' }
    $launcher = @($extra | Where-Object { $_ -match '^\s*DJI_REFRESH_LAUNCHER=' })
    $modes = @($launcher | ForEach-Object { ($_ -replace '^\s*DJI_REFRESH_LAUNCHER=', '').Trim().ToLower() })
    $active = @($modes | Where-Object { @('schtasks', 'subprocess') -contains $_ })
    Write-Output ("DJI_REFRESH_LAUNCHER_BEFORE=" + $(if ($active.Count) { ($active -join ',') + ' (staging site; switched off for the pilot)' } elseif ($launcher.Count) { 'set, inactive value' } else { 'absent' }))

    $freeC = (Get-PSDrive -Name C).Free
    $freeD = (Get-PSDrive -Name D).Free
    Write-Output ("SIZES_MB staging=" + [math]::Round($stagingBytes / 1MB) + " snapshot=" + [math]::Round($snapBytes / 1MB) + " freeC=" + [math]::Round($freeC / 1MB) + " freeD=" + [math]::Round($freeD / 1MB))
    if ($freeD -lt (2.5 * $stagingBytes)) { throw 'STEP FAILED: not enough free space on D: for two staging backups' }
    if ($freeC -lt (1.5 * $snapBytes)) { throw 'STEP FAILED: not enough free space on C: for the pilot database' }
    try { $pre = Invoke-WebRequest -Uri ($site + '/login') -UseBasicParsing -TimeoutSec 30; Write-Output ("STAGING_LOGIN_BEFORE=" + $pre.StatusCode) } catch { Write-Output ("STAGING_LOGIN_BEFORE=ERROR " + $_.Exception.Message) }

    Write-Output '== 2. Everything needed for the exact restore, then an online backup'
    New-Item -ItemType Directory -Force -Path $runDir | Out-Null
    Set-Content -LiteralPath (Join-Path $runDir 'before_head.txt') -Value $before -Encoding ASCII
    Set-Content -LiteralPath (Join-Path $runDir 'before_ref.txt') -Value $beforeRef -Encoding ASCII
    Set-Content -LiteralPath (Join-Path $runDir 'services_before.txt') -Value $states -Encoding ASCII
    Set-Content -LiteralPath (Join-Path $runDir 'staging_db_before.txt') -Value ($db + '|' + $stagingBytes) -Encoding ASCII
    Set-Content -LiteralPath (Join-Path $runDir 'prod_head.txt') -Value $prodHead -Encoding ASCII
    Set-Content -LiteralPath (Join-Path $runDir 'prod_services.txt') -Value $prodServices -Encoding ASCII
    Set-Content -LiteralPath (Join-Path $runDir 'baseline.txt') -Value @($baseline, $snapshot, $snapSha.ToLower()) -Encoding ASCII
    Save-Backup 'staging_before'

    Write-Output '== 3. Staging bots disabled and stopped, then the staging site stopped'
    $touched = $true
    Set-Content -LiteralPath (Join-Path $runDir 'bots_disabled.txt') -Value 'staging bots are set to Disabled and stopped for the pilot; services_before.txt has their state before' -Encoding ASCII
    foreach ($name in $bots) {
      Set-Service -Name $name -StartupType Disabled
      if ([string](Get-Service -Name $name).Status -ne 'Stopped') { Stop-Service -Name $name -Force }
      (Get-Service -Name $name).WaitForStatus('Stopped', (New-TimeSpan -Seconds 90))
      $s = Get-Service -Name $name
      Write-Output ("BOT_NOW " + $s.Name + " " + $s.Status + " " + $s.StartType)
    }
    if ([string](Get-Service -Name $service).Status -ne 'Stopped') { Stop-Service -Name $service -Force }
    (Get-Service -Name $service).WaitForStatus('Stopped', (New-TimeSpan -Seconds 90))
    Write-Output ("SERVICE_STOPPED=" + (Get-Service -Name $service).Status)
    & $python tools\check_db_lock.py --db $db
    $lock = $LASTEXITCODE
    if (($lock -ne 0) -and ($lock -ne 3)) { throw "STEP FAILED: check_db_lock exit $lock -- another process holds the staging database (2) or it is missing" }
    Write-Output ("DB_LOCK_AFTER_STOP=$lock (0 clean, 3 stale WAL)")
    if ($active.Count -gt 0) {
      $saved = @(for ($i = 0; $i -lt $extra.Count; $i++) { if ($extra[$i] -match '^\s*DJI_REFRESH_LAUNCHER=') { [string]$i + '|' + $extra[$i] } })
      Set-Content -LiteralPath (Join-Path $runDir 'refresh_launcher.txt') -Value $saved -Encoding ASCII
      Set-Content -LiteralPath (Join-Path $runDir 'env_extra_before.txt') -Value @([string]$extra.Count, (Get-ListHash $extra)) -Encoding ASCII
      $kept = @($extra | Where-Object { $_ -notmatch '^\s*DJI_REFRESH_LAUNCHER=' })
      Set-ItemProperty -LiteralPath $siteParams -Name 'AppEnvironmentExtra' -Value ([string[]]$kept) -Type MultiString
      $now = @((Get-ItemProperty -LiteralPath $siteParams).AppEnvironmentExtra)
      if ((@($now -match '^\s*DJI_REFRESH_LAUNCHER=').Count -ne 0) -or ($now.Count -ne $kept.Count)) { throw 'STEP FAILED: DJI_REFRESH_LAUNCHER could not be switched off in the staging site environment' }
      Write-Output ("DJI_REFRESH_LAUNCHER_NOW=absent (" + $now.Count + " other entries kept; refresh_launcher.txt keeps the line for the restore)")
    }
    Save-Backup 'staging_final'

    Write-Output '== 4. The pilot revision of the code'
    git checkout --quiet --detach $pin
    if ($LASTEXITCODE -ne 0) { throw 'STEP FAILED: git checkout of the pilot revision' }
    $head = [string](git rev-parse HEAD)
    if ($head -ne $pin) { throw "STEP FAILED: after checkout HEAD is $head, expected $pin" }
    Write-Output ("AFTER_HEAD=" + $head)
    & $python -m compileall -q .
    if ($LASTEXITCODE -ne 0) { throw 'STEP FAILED: compileall' }
    & $python -m unittest tests.test_dji_card_coverage_pilot tests.test_drone_field_passport_core
    if ($LASTEXITCODE -ne 0) { throw 'STEP FAILED: pilot tool and field passport tests' }

    Write-Output '== 5. The staging database becomes the exact B0 snapshot'
    & $python tools\check_db_lock.py --db $db
    $lock = $LASTEXITCODE
    if (($lock -ne 0) -and ($lock -ne 3)) { throw "STEP FAILED: check_db_lock exit $lock -- another process holds the staging database (2) or it is missing" }
    Write-Output ("DB_LOCK_EXIT=$lock (0 clean, 3 stale WAL -- both fine: the file is replaced)")
    Set-Content -LiteralPath (Join-Path $runDir 'swapped.txt') -Value 'staging database replaced by the B0 snapshot' -Encoding ASCII
    foreach ($side in @("$db-wal", "$db-shm")) { if (Test-Path -LiteralPath $side) { Remove-Item -LiteralPath $side -Force } }
    Copy-Item -LiteralPath $snapshot -Destination $db -Force
    $placedSha = (Get-FileHash -LiteralPath $db -Algorithm SHA256).Hash
    if ($placedSha -ne $snapSha) { throw "STEP FAILED: the placed database has sha256 $placedSha, the snapshot $snapSha" }
    Set-Content -LiteralPath (Join-Path $runDir 'placed.txt') -Value $placedSha.ToLower() -Encoding ASCII
    Write-Output ("PLACED=" + $db + " SHA256=" + $placedSha.ToLower() + " (equals the B0 snapshot)")
    $fpPlaced = Join-Path $runDir 'fingerprint_placed.json'
    & $python tools\dji_card_coverage_pilot.py fingerprint --db $db --out $fpPlaced
    if ($LASTEXITCODE -ne 0) { throw "STEP FAILED: fingerprint of the placed database (exit $LASTEXITCODE)" }
    if (-not (Test-SameFile $fpPlaced (Join-Path $planDir 'fingerprint_before.json'))) { throw 'STEP FAILED: the fingerprint of the placed database differs from the B0 fingerprint_before.json -- send this output' }
    Write-Output 'FINGERPRINT_PLACED=equals B0 fingerprint_before.json'
    $ErrorActionPreference = 'Continue'
    & $python tools\check_migration_drift.py --db $db > (Join-Path $runDir 'drift.log') 2>&1
    $ErrorActionPreference = 'Stop'
    $registered = @(Select-String -LiteralPath (Join-Path $runDir 'drift.log') -Pattern '^registered migrations: (\d+);' | ForEach-Object { $_.Matches[0].Groups[1].Value })
    Write-Output ("REGISTERED=" + ($registered -join ',') + " (no migrations are run; drift.log in the run folder)")
    if (($registered.Count -ne 1) -or ($registered[0] -ne '60')) { throw 'STEP FAILED: the pilot database does not report 60 registered migrations' }

    Write-Output '== 6. Starting the staging site only'
    Start-Service -Name $service
    (Get-Service -Name $service).WaitForStatus('Running', (New-TimeSpan -Seconds 90))
    Start-Sleep -Seconds 8
    $login = Invoke-WebRequest -Uri ($site + '/login') -UseBasicParsing -TimeoutSec 30
    if ($login.StatusCode -ne 200) { throw "STEP FAILED: smoke /login returned $($login.StatusCode)" }
    if ($login.Content -notmatch 'vs-login-form') { throw 'STEP FAILED: smoke /login did not render the login form' }
    Write-Output 'SMOKE_LOGIN=200'
    $fields = Invoke-WebRequest -Uri ($site + '/drones/fields') -UseBasicParsing -TimeoutSec 30
    if ($fields.Content -notmatch 'vs-login-form') { throw 'STEP FAILED: /drones/fields without a session did not lead to the login form' }
    Write-Output 'FIELDS_ANONYMOUS=login form (route exists, sign-in required)'
    $fpStarted = Join-Path $runDir 'fingerprint_started.json'
    & $python tools\dji_card_coverage_pilot.py fingerprint --db $db --out $fpStarted | Out-Null
    if ($LASTEXITCODE -ne 0) { throw "STEP FAILED: fingerprint after the start (exit $LASTEXITCODE)" }
    if (-not (Test-SameFile $fpStarted (Join-Path $planDir 'fingerprint_before.json'))) { throw 'STEP FAILED: the database changed when the staging site started -- send this output' }
    Write-Output 'FINGERPRINT_STARTED=equals B0 fingerprint_before.json'
    $ErrorActionPreference = 'Continue'
    & $python tools\check_migration_drift.py --db $db > (Join-Path $runDir 'drift_after_start.log') 2>&1
    $ErrorActionPreference = 'Stop'
    $registered = @(Select-String -LiteralPath (Join-Path $runDir 'drift_after_start.log') -Pattern '^registered migrations: (\d+);' | ForEach-Object { $_.Matches[0].Groups[1].Value })
    Write-Output ("REGISTERED_AFTER_START=" + ($registered -join ','))
    if (($registered.Count -ne 1) -or ($registered[0] -ne '60')) { throw 'STEP FAILED: after the start the database does not report 60 registered migrations' }
    foreach ($name in $bots) {
      $s = Get-Service -Name $name
      if (([string]$s.Status -ne 'Stopped') -or ([string]$s.StartType -ne 'Disabled')) { throw "STEP FAILED: $name is $($s.Status) $($s.StartType) -- it must stay stopped and disabled during the pilot" }
      Write-Output ("BOT_FINAL " + $s.Name + " " + $s.Status + " " + $s.StartType)
    }

    Write-Output '== 7. Production after -- must be exactly as before'
    $prodHeadAfter = [string](git -C $prodRoot rev-parse HEAD)
    $prodServicesAfter = Get-ProdServices
    Write-Output ("PROD_HEAD_AFTER=" + $prodHeadAfter)
    Write-Output ("PROD_SERVICES_AFTER=" + $prodServicesAfter)
    if (($prodHeadAfter -ne $prodHead) -or ($prodServicesAfter -ne $prodWant)) { throw 'STEP FAILED: production head or services are not as before -- send this output' }
    Write-Output ("FINAL_HEAD=" + [string](git rev-parse HEAD))
    Write-Output ("SERVICE_FINAL=" + (Get-Service -Name $service).Status)
    Write-Output ("RUN=" + $runDir)
  } catch {
    $failure = $_.Exception.Message + ' [block line ' + $_.InvocationInfo.ScriptLineNumber + ']'
  }
  if ($failure) {
    Write-Output ("STEP=STOP - " + $failure)
    if ($touched) {
      Write-Output 'STAGING_CHANGED=yes -- send this whole output; staging is returned by block R, nothing else is to be done by hand'
    } else {
      if (Test-Path -LiteralPath $runDir) { Set-Content -LiteralPath (Join-Path $runDir 'returned.txt') -Value 'nothing was changed on staging; the block stopped before the first change' -Encoding ASCII }
      Write-Output 'STAGING_CHANGED=no -- staging was not touched; send this whole output'
    }
  } else {
    Write-Output 'STEP=PASS'
  }
  Write-Output ("LOG FILE: " + $log)
  try { Stop-Transcript | Out-Null } catch { }
}
```

**Ожидается** в конце `STEP=PASS`. Выше — по порядку:

* `FROZEN pilot_manifest.csv / canary_ids.txt / pilot_ids.txt SHA256=…` —
  первые два равны значениям B0;
* `B0_SNAPSHOT=… BYTES=503255040 SHA256=… (equals plan.json)`;
* `PROD_HEAD=8df5683…`,
  `PROD_SERVICES=TransportBot=Running TransportBot003=Running TransportReport=Running`;
* `BEFORE_HEAD=…`, `STAGING_DB=… BYTES=…`, `STAGING_ROW=occupied …`,
  `PIN_VS_PRODUCTION=8 file(s), application files 0`;
* `SERVICE_BEFORE …` — три строки; строки `TASK …` (ни одной с `REFUSED`)
  и `TASKS_CHECKED=…`; `SITE_ENV_NAMES=…` (только имена);
  `DJI_REFRESH_LAUNCHER_BEFORE=…`;
* `STAGING_BACKUP staging_before … integrity=ok`;
* `BOT_NOW … Stopped Disabled` — две строки, `SERVICE_STOPPED=Stopped`,
  `DB_LOCK_AFTER_STOP=0` или `3`; если кнопка была включена —
  `DJI_REFRESH_LAUNCHER_NOW=absent`;
* `STAGING_BACKUP staging_final … integrity=ok`;
* `AFTER_HEAD=39eab50…`, тесты `OK`;
* `PLACED=… (equals the B0 snapshot)`, `FINGERPRINT_PLACED=equals …`,
  `REGISTERED=60`;
* `SMOKE_LOGIN=200`, `FIELDS_ANONYMOUS=…`, `FINGERPRINT_STARTED=equals …`,
  `REGISTERED_AFTER_START=60`, `BOT_FINAL … Stopped Disabled` — две строки;
* `PROD_HEAD_AFTER=8df5683…`, `PROD_SERVICES_AFTER=` — три `Running`;
* `FINAL_HEAD=39eab50…`, `SERVICE_FINAL=Running`, `RUN=…`.

Прислать весь вывод.

### D1 — SRV-YOQSH: разведка задачи `DjiAreaRefreshStaging` (только чтение)

**Почему.** Первый живой B1 (03.10.2026) встал на проверке задач
планировщика до первого изменения (`STAGING_CHANGED=no`): включённая
задача `DjiAreaRefreshStaging` (`Ready`, вид `staging`, `powershell.exe`).
В список разрешённых она не добавляется: если она пишет в площадку, на время
пилота её нужно отключить, а R — вернуть как было. Сначала — что она делает.

D1 ничего не меняет: ни задачу, ни службы, ни базу, ни реестр, ни git.
Пишет только свой журнал в `C:\VehicleSoft_CardPilot`. Показывает:

* имя, папку, состояние, `Enabled`, учётную запись (`UserId`, `LogonType`,
  `RunLevel`), настройки;
* действия — `Execute`, `Arguments`, `WorkingDirectory`; триггеры;
  `LastRunTime`, `LastTaskResult`, `NextRunTime`;
* отпечатки действия, триггеров и выгруженного определения задачи — для
  проверки «определение не изменилось» в следующем B1;
* файлы, которые запускает действие (обёртки `.ps1`/`.bat`/`.cmd` — текстом,
  `.py` — размер и sha256), на два уровня вглубь;
* на что указывает: папка площадки, порт 5051, папка production, цикл
  площади, сборщик, пересчёт, holdout — и итог `STAGING_WRITER`;
* идёт ли она сейчас: процессы цикла и сборщика, файлы замков;
* журнал циклов площади в базе площадки (`mode=ro`): сколько прогонов по
  расписанию и вручную, последние 10;
* связана ли с ней кнопка «Обновить данные DJI» площадки
  (`DJI_REFRESH_LAUNCHER=schtasks` и `DJI_REFRESH_TASK_NAME`).

Секреты не печатаются: строка обёртки, где упомянуты token, secret,
password, cookie, bearer, authorization или api key, скрывается целиком; в
аргументах и командных строках скрываются значения `ключ=значение`,
`-Token значение` и длинные случайные строки. Из окружения службы —
только имена переменных и режим кнопки.

```powershell
& {
  $ErrorActionPreference = 'Stop'
  $ProgressPreference = 'SilentlyContinue'
  $expectedHost = 'srv-yoqsh'
  $taskName     = 'DjiAreaRefreshStaging'
  $root         = 'C:\transport-report-staging'
  $prodRoot     = 'C:\transport-report'
  $python       = 'C:\Program Files\Python314\python.exe'
  $service      = 'TransportReportStaging'
  $prodNames    = @('TransportBot', 'TransportBot003', 'TransportReport')
  $db           = 'C:\transport-report-staging\instance\transport.db'
  $work         = 'C:\VehicleSoft_CardPilot'
  $svcKey       = 'HKLM:\SYSTEM\CurrentControlSet\Services'
  $stamp        = Get-Date -Format 'yyyyMMdd_HHmmss'
  $siteParams   = $svcKey + '\' + $service + '\Parameters'
  function Get-ProdServices { (@($prodNames | ForEach-Object { $s = Get-Service -Name $_ -ErrorAction SilentlyContinue; if ($s) { $_ + '=' + $s.Status } else { $_ + '=missing' } }) -join ' ') }
  function Get-TextHash([string]$text) { $h = [System.Security.Cryptography.SHA256]::Create(); -join ($h.ComputeHash([System.Text.Encoding]::UTF8.GetBytes($text)) | ForEach-Object { $_.ToString('x2') }) }
  function Hide-Secret([string]$s) {
    if (-not $s) { return '' }
    $s = [regex]::Replace($s, '(?i)((token|secret|password|passwd|pwd|apikey|api_key|api-key|cookie|bearer|authorization)[A-Za-z0-9_]*\s*[=:]\s*)("[^"]*"|''[^'']*''|\S+)', '$1[hidden]')
    $s = [regex]::Replace($s, '(?i)(-(token|password|secret|apikey|key)\s+)("[^"]*"|''[^'']*''|\S+)', '$1[hidden]')
    [regex]::Replace($s, '[A-Za-z0-9+/_=-]{32,}', { param($m) $v = $m.Value; if ((($v -replace '[^0-9]', '').Length -ge 6) -or ($v -notmatch '[_/-]')) { '[hidden ' + $v.Length + ' chars]' } else { $v } })
  }
  function Get-CimLine($o) {
    $parts = @()
    foreach ($p in @($o.CimInstanceProperties | Sort-Object Name)) {
      $v = $p.Value
      if (($null -eq $v) -or ([string]$v -eq '')) { continue }
      if ($v.CimInstanceProperties) {
        foreach ($q in @($v.CimInstanceProperties | Sort-Object Name)) { if (($null -ne $q.Value) -and ([string]$q.Value -ne '')) { $parts += ($p.Name + '.' + $q.Name + '=' + (@($q.Value) -join ',')) } }
      } else {
        $parts += ($p.Name + '=' + (@($v) -join ','))
      }
    }
    [string]$o.CimClass.CimClassName + ' ' + ($parts -join ' ')
  }
  function Get-ScriptPaths([string]$text, [string[]]$baseDirs) {
    $found = @()
    foreach ($m in [regex]::Matches($text, '"([^"]+\.(ps1|bat|cmd|py))"|([^\s"'';&|<>]+\.(ps1|bat|cmd|py))\b')) {
      $p = if ($m.Groups[1].Success) { $m.Groups[1].Value } else { $m.Groups[3].Value }
      if (-not [System.IO.Path]::IsPathRooted($p)) {
        $dirs = @($baseDirs | Where-Object { $_ } | Select-Object -Unique)
        $hit = @($dirs | ForEach-Object { Join-Path $_ $p } | Where-Object { Test-Path -LiteralPath $_ } | Select-Object -First 1)
        $p = if ($hit.Count -gt 0) { $hit[0] } else { $p + ' (relative; looked in ' + ($dirs -join ', ') + ')' }
      }
      if ($found -notcontains $p) { $found += $p }
    }
    $found
  }
  New-Item -ItemType Directory -Force -Path $work | Out-Null
  $log = Join-Path $work ('card_pilot_d1_' + $stamp + '.log')
  try { Start-Transcript -Path $log -Append | Out-Null } catch { Write-Output 'NOTE: the log file could not be started' }
  $failure = $null
  try {
    Write-Output '== 1. The scheduled task (read only)'
    if ((hostname) -ne $expectedHost) { throw "STEP FAILED: host is $(hostname), expected $expectedHost" }
    $prodHead = [string](git -C $prodRoot rev-parse HEAD)
    $prodServices = Get-ProdServices
    Write-Output ("PROD_HEAD=" + $prodHead)
    Write-Output ("PROD_SERVICES=" + $prodServices)
    $tasks = @(Get-ScheduledTask -TaskName $taskName -ErrorAction SilentlyContinue)
    Write-Output ("TASKS_WITH_THIS_NAME=" + $tasks.Count)
    if ($tasks.Count -eq 0) { throw "STEP FAILED: no scheduled task named $taskName -- send this output" }
    $allText = ''
    $wds = @()
    foreach ($t in $tasks) {
      Write-Output ("TASK_NAME=" + $t.TaskName)
      Write-Output ("TASK_PATH=" + $t.TaskPath)
      Write-Output ("STATE=" + $t.State)
      Write-Output ("ENABLED=" + $t.Settings.Enabled)
      Write-Output ("AUTHOR=" + (Hide-Secret ([string]$t.Author)) + " REGISTERED=" + $t.Date)
      Write-Output ("DESCRIPTION=" + (Hide-Secret ([string]$t.Description)))
      Write-Output ("PRINCIPAL UserId=" + $t.Principal.UserId + " LogonType=" + $t.Principal.LogonType + " RunLevel=" + $t.Principal.RunLevel + " GroupId=" + $t.Principal.GroupId)
      Write-Output ("SETTINGS MultipleInstances=" + $t.Settings.MultipleInstances + " ExecutionTimeLimit=" + $t.Settings.ExecutionTimeLimit + " AllowDemandStart=" + $t.Settings.AllowDemandStart + " StartWhenAvailable=" + $t.Settings.StartWhenAvailable + " Hidden=" + $t.Settings.Hidden)
      $actionLines = @()
      $i = 0
      foreach ($a in @($t.Actions)) {
        $i++
        $actionLines += ([string]$a.Execute + '|' + [string]$a.Arguments + '|' + [string]$a.WorkingDirectory)
        Write-Output ("ACTION " + $i + " TYPE=" + $a.CimClass.CimClassName)
        Write-Output ("ACTION " + $i + " EXECUTE=" + (Hide-Secret ([string]$a.Execute)))
        Write-Output ("ACTION " + $i + " ARGUMENTS=" + (Hide-Secret ([string]$a.Arguments)))
        Write-Output ("ACTION " + $i + " WORKING_DIRECTORY=" + [string]$a.WorkingDirectory)
        $allText += ' ' + [string]$a.Execute + ' ' + [string]$a.Arguments + ' ' + [string]$a.WorkingDirectory
        if ($a.WorkingDirectory) { $wds += [string]$a.WorkingDirectory }
      }
      $triggerLines = @()
      $i = 0
      foreach ($tr in @($t.Triggers)) {
        $i++
        $line = Get-CimLine $tr
        $triggerLines += $line
        Write-Output ("TRIGGER " + $i + " " + $line)
      }
      if ($i -eq 0) { Write-Output 'TRIGGERS=none (runs only when started by hand or by the site button)' }
      $info = Get-ScheduledTaskInfo -TaskName $t.TaskName -TaskPath $t.TaskPath
      Write-Output ("LAST_RUN_TIME=" + $info.LastRunTime)
      Write-Output ("LAST_TASK_RESULT=" + $info.LastTaskResult + " (0x" + ('{0:X8}' -f [int64]$info.LastTaskResult) + ")")
      Write-Output ("NEXT_RUN_TIME=" + $info.NextRunTime)
      Write-Output ("MISSED_RUNS=" + $info.NumberOfMissedRuns)
      Write-Output ("ACTION_FINGERPRINT=" + (Get-TextHash ($actionLines -join "`n")))
      Write-Output ("TRIGGER_FINGERPRINT=" + (Get-TextHash ($triggerLines -join "`n")))
      $xml = [string](Export-ScheduledTask -TaskName $t.TaskName -TaskPath $t.TaskPath)
      Write-Output ("TASK_XML_SHA256=" + (Get-TextHash $xml) + " (the exported definition; its text is not printed)")
    }

    Write-Output '== 2. The files the action runs (lines that may hold a secret are hidden)'
    $queue = @(Get-ScriptPaths $allText (@($wds) + @($root)))
    $seen = @()
    for ($k = 0; $k -lt $queue.Count; $k++) {
      $p = $queue[$k]
      if ($seen -contains $p) { continue }
      $seen += $p
      if (-not (Test-Path -LiteralPath $p)) { Write-Output ("FILE " + $p + " NOT FOUND"); continue }
      $item = Get-Item -LiteralPath $p
      Write-Output ("FILE " + $item.FullName + " BYTES=" + $item.Length + " CHANGED=" + $item.LastWriteTime.ToString('yyyy-MM-dd HH:mm') + " SHA256=" + (Get-FileHash -LiteralPath $p -Algorithm SHA256).Hash.ToLower())
      if ($p -match '\.py$') { continue }
      $lines = @(Get-Content -LiteralPath $p -TotalCount 150)
      for ($n = 0; $n -lt $lines.Count; $n++) {
        $text = [string]$lines[$n]
        if ($text -match '(?i)(token|secret|password|passwd|cookie|bearer|authorization|api.?key)') { Write-Output ("  " + ($n + 1) + ": [line hidden: it names " + $Matches[1] + "]") } else { Write-Output ("  " + ($n + 1) + ": " + (Hide-Secret $text)) }
        $allText += ' ' + $text
      }
      if ($k -lt 8) { foreach ($q in @(Get-ScriptPaths (($lines | ForEach-Object { [string]$_ }) -join "`n") (@((Split-Path -Parent $item.FullName)) + @($wds) + @($root)))) { if ($queue -notcontains $q) { $queue += $q } } }
    }
    if ($queue.Count -eq 0) { Write-Output 'FILES=none named in the action' }

    Write-Output '== 3. What the task points at'
    $checks = [ordered]@{
      'STAGING_FOLDER'   = 'transport-report-staging'
      'PORT_5051'        = '5051'
      'PRODUCTION_FOLDER' = 'C:\\transport-report\\'
      'DAILY_CYCLE'      = 'dji_area_daily'
      'RUN_QUEUED'       = '--run-queued'
      'COLLECTOR'        = 'drone_collector'
      'RECALC'           = 'dji_area_recalc'
      'SOURCE_SYNC'      = 'source_sync|--sources'
      'HOLDOUT'          = 'VehicleSoft_|Holdout'
    }
    foreach ($key in $checks.Keys) { Write-Output ("POINTS_AT " + $key + "=" + $(if ($allText -match $checks[$key]) { 'yes' } else { 'no' })) }
    $writer = ($allText -match 'transport-report-staging|5051') -and ($allText -match 'dji_area_daily|drone_collector|dji_area_recalc|source_sync|--sources|--db')
    Write-Output ("STAGING_WRITER=" + $(if ($writer) { 'yes' } else { 'not shown by the action text -- see the lines above' }))

    Write-Output '== 4. Is it running now'
    $procs = @(Get-CimInstance -ClassName Win32_Process | Where-Object { ([string]$_.CommandLine -match 'dji_area_daily|drone_collector|dji_area_recalc|DjiAreaRefresh') -and ([string]$_.CommandLine -notmatch 'card_pilot_d1_') })
    foreach ($pr in $procs) { Write-Output ("PROCESS PID=" + $pr.ProcessId + " PARENT=" + $pr.ParentProcessId + " NAME=" + $pr.Name + " STARTED=" + $pr.CreationDate + " CMD=" + (Hide-Secret ([string]$pr.CommandLine))) }
    Write-Output ("PROCESSES=" + $procs.Count)
    foreach ($lk in @((Join-Path $root 'instance\dji_area_cycle.lock'), (Join-Path $root 'drone_collector\data\collector.lock'))) {
      if (Test-Path -LiteralPath $lk) { Write-Output ("LOCK_FILE " + $lk + " CHANGED=" + (Get-Item -LiteralPath $lk).LastWriteTime.ToString('yyyy-MM-dd HH:mm') + " (a file, not proof of a holder)") } else { Write-Output ("LOCK_FILE " + $lk + " absent") }
    }

    Write-Output '== 5. What it wrote to the staging database (read only)'
    $code = @'
import sqlite3, sys
path = sys.argv[1].replace('\\', '/')
con = sqlite3.connect('file:' + path + '?mode=ro', uri=True)
names = {r[0] for r in con.execute("SELECT name FROM sqlite_master WHERE type='table'")}
if 'drone_area_cycle_runs' not in names:
    print('CYCLE_RUNS=table absent')
else:
    for kind, n, first, last in con.execute("SELECT trigger_kind, COUNT(*), MIN(requested_at), MAX(requested_at) FROM drone_area_cycle_runs GROUP BY trigger_kind ORDER BY trigger_kind"):
        print('CYCLE_RUNS_BY_KIND %s count=%d first=%s last=%s' % (kind, n, first, last))
    for row in con.execute("SELECT id, trigger_kind, status, requested_at, started_at, finished_at, current_step FROM drone_area_cycle_runs ORDER BY id DESC LIMIT 10"):
        print('CYCLE_RUN ' + ' | '.join('' if v is None else str(v) for v in row))
con.close()
'@
    $code | & $python - $db
    if ($LASTEXITCODE -ne 0) { Write-Output ("CYCLE_RUNS=could not be read (exit " + $LASTEXITCODE + ")") }
    $logDir = Join-Path $root 'instance\dji_refresh_logs'
    if (Test-Path -LiteralPath $logDir) { foreach ($f in @(Get-ChildItem -LiteralPath $logDir -File | Sort-Object LastWriteTime -Descending | Select-Object -First 5)) { Write-Output ("REFRESH_LOG " + $f.Name + " CHANGED=" + $f.LastWriteTime.ToString('yyyy-MM-dd HH:mm') + " BYTES=" + $f.Length) } } else { Write-Output 'REFRESH_LOGS=absent' }

    Write-Output '== 6. The staging site button (names and modes only, no values)'
    $params = Get-ItemProperty -LiteralPath $siteParams -ErrorAction SilentlyContinue
    $extra = @()
    if ($params -and $params.AppEnvironmentExtra) { $extra = @($params.AppEnvironmentExtra) }
    Write-Output ("SITE_ENV_NAMES=" + (@($extra | ForEach-Object { $n = ($_ -split '=', 2)[0].Trim(); if ($n -match '^[A-Za-z_][A-Za-z0-9_]*$') { $n } else { '?' } }) -join ','))
    $launcher = @($extra | Where-Object { $_ -match '^\s*DJI_REFRESH_LAUNCHER=' } | ForEach-Object { ($_ -split '=', 2)[1].Trim().ToLower() })
    $taskSetting = @($extra | Where-Object { $_ -match '^\s*DJI_REFRESH_TASK_NAME=' } | ForEach-Object { ($_ -split '=', 2)[1].Trim() })
    Write-Output ("DJI_REFRESH_LAUNCHER=" + $(if ($launcher.Count -eq 0) { 'absent' } else { (@($launcher | ForEach-Object { if (@('schtasks', 'subprocess') -contains $_) { $_ } else { 'other value' } }) -join ',') }))
    Write-Output ("DJI_REFRESH_TASK_NAME=" + $(if ($taskSetting.Count -eq 0) { 'absent' } else { (@($taskSetting | ForEach-Object { if ($_ -match '^[A-Za-z0-9_.\- \\]{1,120}$') { $_ } else { 'other value' } }) -join ',') }))
    Write-Output ("BUTTON_STARTS_THIS_TASK=" + $(if (($launcher -contains 'schtasks') -and (@($taskSetting | Where-Object { ($_ -split '\\')[-1] -eq $taskName }).Count -gt 0)) { 'yes' } else { 'no' }))

    Write-Output '== 7. Production after -- nothing here changes it'
    $prodHeadAfter = [string](git -C $prodRoot rev-parse HEAD)
    $prodServicesAfter = Get-ProdServices
    Write-Output ("PROD_HEAD_AFTER=" + $prodHeadAfter)
    Write-Output ("PROD_SERVICES_AFTER=" + $prodServicesAfter)
    if (($prodHeadAfter -ne $prodHead) -or ($prodServicesAfter -ne $prodServices)) { throw 'STEP FAILED: production changed while this block ran -- send this output' }
  } catch {
    $failure = $_.Exception.Message + ' [block line ' + $_.InvocationInfo.ScriptLineNumber + ']'
  }
  if ($failure) { Write-Output ("STEP=STOP - " + $failure) } else { Write-Output 'STEP=PASS (read only: nothing was changed)' }
  Write-Output ("LOG FILE: " + $log)
  try { Stop-Transcript | Out-Null } catch { }
}
```

**Ожидается** в конце `STEP=PASS (read only: nothing was changed)`. Прислать
весь вывод. По нему решается, как B1 изолирует задачу на время пилота и как R
её возвращает.

### R — SRV-YOQSH: возврат площадки

Выдаётся только после разбора: по окончании пилота или если B1 встал с
`STAGING_CHANGED=yes`. Пока сборщик рабочей машины может слать данные на
площадку, R не запускается.

* Работает с единственной открытой папкой `staging_*` (без
  `returned.txt`); две открытые или ни одной — отказ без изменений.
* Ничего не меняя, сверяет: копия для возврата цела (размер и sha256 из
  `staging_backup.txt`), в рабочей копии площадки нет правок, коммит до
  B1 на месте.
* Останавливает службу и ботов площадки.
* Только если база была заменена (`swapped.txt`): сначала база пилота
  сохраняется в `pilot_final_<время>` — онлайн-копией с `integrity_check`,
  а если база повреждена, отсутствует или B1 встал посреди копирования
  (нет `placed.txt`) — сырыми файлами `transport.db`/`-wal`/`-shm`. Держит
  базу другой процесс — отказ. Затем файл копии `staging_final` (строка
  именно с этой меткой в `staging_backup.txt`) на место, sha256 равен
  записанному, отметка `db_restored.txt`.
* HEAD — как до B1 (ветка или отсоединённый HEAD — как было).
* `DJI_REFRESH_LAUNCHER` — обратно на её прежнее место, если B1 её
  убирал; sha256 всего списка сверяется с записанным. Если кто-то менял
  окружение после B1 — строка всё равно возвращается, а итог —
  `STEP=PASS_WITH_NOTES` с пояснением, значения не печатаются.
* Службы — тип запуска и состояние из `services_before.txt`; если служба
  площадки работала — `/login` 200.
* Production не трогается, HEAD и службы сверяются до и после.
* `returned.txt` пишется последним. Если R встал посередине, его можно
  запустить снова: восстановленная база второй раз не перезаписывается
  (`db_restored.txt`), остальные шаги повторяемы.

```powershell
& {
  $ErrorActionPreference = 'Stop'
  $ProgressPreference = 'SilentlyContinue'
  $expectedHost = 'srv-yoqsh'
  $root         = 'C:\transport-report-staging'
  $prodRoot     = 'C:\transport-report'
  $python       = 'C:\Program Files\Python314\python.exe'
  $service      = 'TransportReportStaging'
  $bots         = @('TransportBotStaging', 'TransportBot003Staging')
  $prodNames    = @('TransportBot', 'TransportBot003', 'TransportReport')
  $runRoot      = 'D:\transport-report-backups\staging\card_pilot'
  $db           = 'C:\transport-report-staging\instance\transport.db'
  $site         = 'http://10.103.25.14:5051'
  $work         = 'C:\VehicleSoft_CardPilot'
  $svcKey       = 'HKLM:\SYSTEM\CurrentControlSet\Services'
  $siteParams   = $svcKey + '\' + $service + '\Parameters'
  $stamp        = Get-Date -Format 'yyyyMMdd_HHmmss'
  function Get-ListHash([string[]]$list) { $h = [System.Security.Cryptography.SHA256]::Create(); -join ($h.ComputeHash([System.Text.Encoding]::UTF8.GetBytes(($list -join "`n"))) | ForEach-Object { $_.ToString('x2') }) }
  function Get-ProdServices { (@($prodNames | ForEach-Object { $s = Get-Service -Name $_ -ErrorAction SilentlyContinue; if ($s) { $_ + '=' + $s.Status } else { $_ + '=missing' } }) -join ' ') }
  New-Item -ItemType Directory -Force -Path $work | Out-Null
  $log = Join-Path $work ('card_pilot_r_' + $stamp + '.log')
  try { Start-Transcript -Path $log -Append | Out-Null } catch { Write-Output 'NOTE: the log file could not be started' }
  $touched = $false
  $failure = $null
  $notes = @()
  try {
    Write-Output '== 1. Checks - nothing is changed yet'
    if ((hostname) -ne $expectedHost) { throw "STEP FAILED: host is $(hostname), expected $expectedHost" }
    $open = @(Get-ChildItem -LiteralPath $runRoot -Directory -Filter 'staging_*' -ErrorAction SilentlyContinue | Where-Object { -not (Test-Path -LiteralPath (Join-Path $_.FullName 'returned.txt')) })
    if ($open.Count -ne 1) { throw "STEP FAILED: $($open.Count) open staging runs, expected exactly one -- send this output" }
    $run = $open[0].FullName
    Write-Output ("RUN=" + $run)
    $before = ('' + (Get-Content -LiteralPath (Join-Path $run 'before_head.txt') -TotalCount 1)).Trim()
    if ($before -notmatch '^[0-9a-f]{40}$') { throw "STEP FAILED: before_head.txt holds '$before', not a commit" }
    $beforeRef = ''
    if (Test-Path -LiteralPath (Join-Path $run 'before_ref.txt')) { $beforeRef = ('' + (Get-Content -LiteralPath (Join-Path $run 'before_ref.txt') -TotalCount 1)).Trim() }
    $states = @{}
    foreach ($line in @(Get-Content -LiteralPath (Join-Path $run 'services_before.txt') | Where-Object { $_.Trim() })) {
      $p = $line.Split('|')
      $states[$p[0]] = @{ Status = $p[1]; StartType = $p[2]; Delayed = $(if ($p.Count -gt 3) { $p[3] } else { '' }) }
    }
    foreach ($name in @($service) + $bots) {
      if (-not $states.ContainsKey($name)) { throw "STEP FAILED: services_before.txt has no line for $name" }
      if (-not (Get-Service -Name $name -ErrorAction SilentlyContinue)) { throw "STEP FAILED: service not found: $name" }
      Write-Output ("SERVICE_BEFORE_B1 " + $name + " " + $states[$name].Status + " " + $states[$name].StartType + " delayed=" + $states[$name].Delayed)
    }
    $swapped = Test-Path -LiteralPath (Join-Path $run 'swapped.txt')
    $from = $null
    if ($swapped) {
      $rec = @(Get-Content -LiteralPath (Join-Path $run 'staging_backup.txt') | Where-Object { $_ -like 'staging_final|*' })
      if ($rec.Count -ne 1) { throw "STEP FAILED: the database was replaced, but staging_backup.txt has $($rec.Count) staging_final lines -- send this output" }
      $p = $rec[0].Split('|')
      $from = [pscustomobject]@{ Label = $p[0]; Path = $p[1]; Bytes = [long]$p[2]; Sha = $p[3] }
      if (-not (Test-Path -LiteralPath $from.Path)) { throw "STEP FAILED: backup not found: $($from.Path)" }
      if ((Get-Item -LiteralPath $from.Path).Length -ne $from.Bytes) { throw "STEP FAILED: backup $($from.Path) changed size since B1" }
      if ((Get-FileHash -LiteralPath $from.Path -Algorithm SHA256).Hash -ne $from.Sha) { throw "STEP FAILED: backup $($from.Path) changed since B1 (sha256)" }
      Write-Output ("RESTORE_FROM " + $from.Label + " " + $from.Path + " BYTES=" + $from.Bytes + " SHA256=" + $from.Sha + " (unchanged since B1)")
      $dbBytes = 0
      if (Test-Path -LiteralPath $db) { $dbBytes = (Get-Item -LiteralPath $db).Length }
      $freeD = (Get-PSDrive -Name D).Free
      if ($freeD -lt (1.5 * $dbBytes + 100MB)) { throw 'STEP FAILED: not enough free space on D: to keep a copy of the pilot database' }
    } else {
      Write-Output 'RESTORE_FROM=none (the staging database was never replaced)'
    }
    $launcherFile = Join-Path $run 'refresh_launcher.txt'
    $launcher = @()
    if (Test-Path -LiteralPath $launcherFile) { $launcher = @(Get-Content -LiteralPath $launcherFile | Where-Object { $_ -match '^\d+\|\s*DJI_REFRESH_LAUNCHER=' }) }
    $prodHead = [string](git -C $prodRoot rev-parse HEAD)
    $prodServices = Get-ProdServices
    Write-Output ("PROD_HEAD=" + $prodHead)
    Write-Output ("PROD_SERVICES=" + $prodServices)
    Set-Location -LiteralPath $root
    $changed = @(git status --porcelain --untracked-files=no)
    if ($changed.Count -gt 0) { throw "STEP FAILED: $($changed.Count) tracked file(s) changed in the staging checkout -- send the output of git status" }
    git cat-file -e "$before^{commit}"
    if ($LASTEXITCODE -ne 0) { throw "STEP FAILED: commit $before not found in the staging checkout" }
    Write-Output ("STAGING_HEAD_NOW=" + [string](git rev-parse HEAD))

    Write-Output '== 2. Staging site and bots stopped'
    $touched = $true
    foreach ($name in $bots + @($service)) {
      if ([string](Get-Service -Name $name).Status -ne 'Stopped') { Stop-Service -Name $name -Force }
      (Get-Service -Name $name).WaitForStatus('Stopped', (New-TimeSpan -Seconds 90))
    }
    Write-Output 'STOPPED=site and both bots'

    Write-Output '== 3. The staging database as it was before B1'
    $restoredMark = Join-Path $run 'db_restored.txt'
    if ($swapped -and (Test-Path -LiteralPath $restoredMark)) {
      Write-Output 'DB_RESTORED=already (an earlier run of this block restored it; db_restored.txt)'
    } elseif ($swapped) {
      $keep = Join-Path $run ('pilot_final_' + $stamp)
      New-Item -ItemType Directory -Force -Path $keep | Out-Null
      $placed = Test-Path -LiteralPath (Join-Path $run 'placed.txt')
      $raw = $null
      if (-not (Test-Path -LiteralPath $db)) {
        Write-Output 'PILOT_DB=missing (nothing to keep)'
      } elseif (-not $placed) {
        $raw = 'B1 stopped while it was placing the copy'
      } else {
        & $python tools\check_db_lock.py --db $db
        $lock = $LASTEXITCODE
        if ($lock -eq 2) { throw 'STEP FAILED: check_db_lock exit 2 -- another process holds the staging database' }
        if (($lock -ne 0) -and ($lock -ne 3)) {
          $raw = "check_db_lock exit $lock"
        } else {
          $out = & $python (Join-Path $root 'backup_transport_db.py') --source $db --dest-dir $keep --suffix pilot_final | Out-String
          $kept = @(Get-ChildItem -LiteralPath $keep -Filter '*.db')
          if (($LASTEXITCODE -eq 0) -and ($out -match 'Integrity check : ok') -and ($kept.Count -eq 1)) {
            Write-Output ("PILOT_DB_KEPT=" + $kept[0].FullName + " BYTES=" + $kept[0].Length + " SHA256=" + (Get-FileHash -LiteralPath $kept[0].FullName -Algorithm SHA256).Hash.ToLower() + " integrity=ok")
          } else {
            $raw = 'the online copy did not pass integrity_check'
          }
        }
      }
      if ($raw) {
        $rawDir = Join-Path $keep 'raw'
        New-Item -ItemType Directory -Force -Path $rawDir | Out-Null
        foreach ($f in @($db, "$db-wal", "$db-shm")) { if (Test-Path -LiteralPath $f) { Copy-Item -LiteralPath $f -Destination $rawDir -Force } }
        Write-Output ("PILOT_DB_KEPT=raw files in " + $rawDir + " (" + $raw + ")")
      }
      foreach ($side in @("$db-wal", "$db-shm")) { if (Test-Path -LiteralPath $side) { Remove-Item -LiteralPath $side -Force } }
      Copy-Item -LiteralPath $from.Path -Destination $db -Force
      $sha = (Get-FileHash -LiteralPath $db -Algorithm SHA256).Hash
      if ($sha -ne $from.Sha) { throw "STEP FAILED: the restored database has sha256 $sha, the backup $($from.Sha)" }
      Set-Content -LiteralPath $restoredMark -Value $sha.ToLower() -Encoding ASCII
      Write-Output ("DB_RESTORED=" + $db + " SHA256=" + $sha.ToLower() + " (equals " + $from.Label + ", which passed integrity_check in B1)")
    } else {
      Write-Output 'DB_RESTORED=not needed'
    }

    Write-Output '== 4. The staging code as it was before B1'
    if ($beforeRef -match '^refs/heads/(.+)$') { git checkout --quiet $Matches[1] } else { git -c advice.detachedHead=false checkout --quiet --detach $before }
    if ($LASTEXITCODE -ne 0) { throw 'STEP FAILED: git checkout of the staging revision before B1' }
    $head = [string](git rev-parse HEAD)
    if ($head -ne $before) { throw "STEP FAILED: HEAD is $head, before B1 it was $before" }
    Write-Output ("HEAD_RESTORED=" + $head + " REF=" + $(if ($beforeRef) { $beforeRef } else { 'detached' }))

    Write-Output '== 5. The staging site environment as it was before B1'
    if ($launcher.Count -gt 0) {
      $params = Get-ItemProperty -LiteralPath $siteParams
      $extra = @()
      if ($params.AppEnvironmentExtra) { $extra = @($params.AppEnvironmentExtra) }
      if (@($extra -match '^\s*DJI_REFRESH_LAUNCHER=').Count -eq 0) {
        $list = New-Object System.Collections.ArrayList
        foreach ($e in $extra) { [void]$list.Add([string]$e) }
        foreach ($item in @($launcher | Sort-Object { [int]($_.Split('|', 2)[0]) })) {
          $at = [int]($item.Split('|', 2)[0])
          $line = $item.Split('|', 2)[1]
          if ($at -gt $list.Count) { $at = $list.Count }
          $list.Insert($at, $line)
        }
        Set-ItemProperty -LiteralPath $siteParams -Name 'AppEnvironmentExtra' -Value ([string[]]$list.ToArray()) -Type MultiString
      }
      $now = @((Get-ItemProperty -LiteralPath $siteParams).AppEnvironmentExtra)
      if (@($now -match '^\s*DJI_REFRESH_LAUNCHER=').Count -ne $launcher.Count) { throw 'STEP FAILED: DJI_REFRESH_LAUNCHER could not be put back into the staging site environment' }
      $want = @(Get-Content -LiteralPath (Join-Path $run 'env_extra_before.txt'))
      if (($want.Count -eq 2) -and ([string]$now.Count -eq $want[0]) -and ((Get-ListHash ([string[]]$now)) -eq $want[1])) {
        Write-Output 'DJI_REFRESH_LAUNCHER=restored (the whole environment list equals the one before B1)'
      } else {
        $notes += 'the staging site environment differs from the one before B1 in more than the launcher line (someone changed it after B1); the launcher line is back'
        Write-Output 'DJI_REFRESH_LAUNCHER=restored, ENV_EXTRA=DIFFERS from before B1 (values are not printed)'
      }
    } else {
      Write-Output 'DJI_REFRESH_LAUNCHER=not touched by B1'
    }

    Write-Output '== 6. Staging services as they were before B1'
    foreach ($name in @($service) + $bots) {
      $want = $states[$name]
      Set-Service -Name $name -StartupType $want.StartType
      if ($want.Delayed -ne '') {
        $reg = Get-ItemProperty -LiteralPath ($svcKey + '\' + $name) -ErrorAction SilentlyContinue
        if ((-not $reg) -or ([string]$reg.DelayedAutostart -ne $want.Delayed)) { Set-ItemProperty -LiteralPath ($svcKey + '\' + $name) -Name 'DelayedAutostart' -Value ([int]$want.Delayed) -Type DWord }
      }
      if ($want.Status -eq 'Running') {
        Start-Service -Name $name
        (Get-Service -Name $name).WaitForStatus('Running', (New-TimeSpan -Seconds 90))
        if ($name -eq $service) { Start-Sleep -Seconds 8 }
      }
      $s = Get-Service -Name $name
      if (([string]$s.Status -ne $want.Status) -or ([string]$s.StartType -ne $want.StartType)) { throw "STEP FAILED: $name is $($s.Status) $($s.StartType), before B1 it was $($want.Status) $($want.StartType)" }
      Write-Output ("SERVICE_NOW " + $name + " " + $s.Status + " " + $s.StartType)
    }
    if ([string](Get-Service -Name $service).Status -eq 'Running') {
      $login = Invoke-WebRequest -Uri ($site + '/login') -UseBasicParsing -TimeoutSec 30
      if (($login.StatusCode -ne 200) -or ($login.Content -notmatch 'vs-login-form')) { throw 'STEP FAILED: smoke /login after the restore' }
      Write-Output 'SMOKE_LOGIN=200'
    }

    Write-Output '== 7. Production -- this block does not touch it'
    $prodHeadAfter = [string](git -C $prodRoot rev-parse HEAD)
    $prodServicesAfter = Get-ProdServices
    Write-Output ("PROD_HEAD_AFTER=" + $prodHeadAfter)
    Write-Output ("PROD_SERVICES_AFTER=" + $prodServicesAfter)
    if (($prodHeadAfter -ne $prodHead) -or ($prodServicesAfter -ne $prodServices)) { throw 'STEP FAILED: production changed while this block ran -- send this output' }
    Set-Content -LiteralPath (Join-Path $run 'returned.txt') -Value ('staging returned to its state before B1 at ' + (Get-Date -Format 'yyyy-MM-dd HH:mm:ss')) -Encoding ASCII
    Write-Output ("RETURNED=" + $run)
  } catch {
    $failure = $_.Exception.Message + ' [block line ' + $_.InvocationInfo.ScriptLineNumber + ']'
  }
  if ($failure) {
    Write-Output ("STEP=STOP - " + $failure)
    if ($touched) { Write-Output 'STAGING_CHANGED=yes -- send this whole output; this block can be run again once the cause is fixed' } else { Write-Output 'STAGING_CHANGED=no -- send this whole output' }
  } elseif ($notes.Count -gt 0) {
    foreach ($n in $notes) { Write-Output ("NOTE: " + $n) }
    Write-Output 'STEP=PASS_WITH_NOTES -- send this whole output'
  } else {
    Write-Output 'STEP=PASS'
  }
  Write-Output ("LOG FILE: " + $log)
  try { Stop-Transcript | Out-Null } catch { }
}
```

**Ожидается** в конце `STEP=PASS`, выше — `RESTORE_FROM staging_final …
(unchanged since B1)` (или `none`, если база не заменялась),
`PILOT_DB_KEPT=…`, `DB_RESTORED=…`, `HEAD_RESTORED=…`,
`DJI_REFRESH_LAUNCHER=restored (the whole environment list equals …)` (или
`not touched by B1`), три строки
`SERVICE_NOW …` как до B1, `SMOKE_LOGIN=200`, `RETURNED=…`. После — PR
освобождения `docs/STAGING.md`.

## 15. Откат и восстановление

* Production не меняется — откатывать нечего.
* Площадка восстанавливается блоком R (§14): база — из последней копии B1
  (`staging_final`, снята после остановки служб), HEAD — из записи B1,
  `DJI_REFRESH_LAUNCHER` — из `refresh_launcher.txt`, службы и боты — как
  в `services_before.txt`. Перед заменой R сохраняет онлайн-копию базы
  пилота (`pilot_final`). Затем — docs-only PR освобождения
  `docs/STAGING.md`.
* Папки пилота (`D:\…\card_pilot`, `C:\VehicleSoft_CardPilot`) — только
  файлы пилота; удалять их или нет, решает владелец после отчёта.

## 16. Инженерный вердикт

После пилота.
