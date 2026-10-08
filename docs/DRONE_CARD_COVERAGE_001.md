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
* **Сбор — на SRV-YOQSH из чекаута пилота** (решение владельца по выводу
  W0, 08.10.2026; прежний план — рабочая машина, как блок W квалификации
  21.09.2026). Код — `C:\VehicleSoft_CardPilot\src` на пине, python — venv
  сборщика production. Сессия DJI production только читается
  (`--save-session` не передаётся). Очередь и журнал пилота — свои. Адрес
  площадки `:5051` и остальные настройки получает только процесс сборщика.
  Код, очередь, расписание и окружение production не меняются. Блок сбора
  отказывается от `:5050` и от любого приёмника, кроме `:5051`.
* **Пересчёт** — только `--db` базы площадки, при остановленной службе
  площадки.
* **На всё время пилота на площадке выключено всё, что пишет в её базу
  помимо пилота** (шаг B1):
  * боты площадки остановлены и переведены в `Disabled` — копия production
    несёт очередь уведомлений, бот разослал бы их живым людям;
  * два независимых барьера против цикла обновления площади на площадке:
    задача планировщика `DjiAreaRefreshStaging` (по расписанию и по
    кнопке; D1, 03.10.2026: пишет в базу площадки через
    `tools\dji_area_daily.py --run-queued`) отключена, и кнопка «Обновить
    данные DJI» площадки выключена — `DJI_REFRESH_LAUNCHER` убрана из
    окружения службы. В копии production — пользователи production; одно
    нажатие запустило бы сбор и пересчёт вне манифеста и лишние посещения
    DJI. Строка и состояние задачи сохранены, блок R их возвращает;
  * задачи планировщика: B1 печатает все включённые (кроме
    `\Microsoft\`) — имя, состояние, вид, исполняемый файл, рабочую папку,
    без аргументов. Отказ — если включённая задача относится к площадке
    (имя со `Staging`, путь площадки, `:5051`) и не является известной
    резервной копией площадки, или упоминает `VehicleSoft_` / `Holdout`.
    Строки вида `other` разбираются по выводу до канарейки;
  * другие службы площадки — остановлены и не запускаются сами (`Manual` /
    `Disabled`), иначе отказ.
* **Общая сессия DJI и общий замок сборщика.** По D1 задача площадки
  запускает сборщик из venv production и с общими
  `C:\transport-report\drone_collector\data\storage_state.json` и
  `collector.lock`. Сейчас это не меняется. Отключённая задача на время
  пилота не берёт ни сессию, ни замок. W1 берёт тот же замок с ожиданием 0:
  занят — сборщик выходит с кодом 24, блок останавливается. До запуска W1
  требует, чтобы задачи сбора production не шли, до их следующего запуска
  было не меньше 130 минут, а подсказки владельца замка не было. Сбор
  пилота обрывается на 100-й минуте. Расписание сборщика production ни B1,
  ни W1 не трогают.
* **Чего B1 не закрывает, и чем это ловится.** Приёмники `/drones/api/*`
  площадки принимают данные от любого владельца токена площадки. На время
  пилота на площадку шлёт только сбор W1/W2. W1 перед сбором проверяет,
  что нет включённых задач, которые собирают или пишут в площадку, и что
  все новые ревизии площадки — его прогона и только канареечных вылетов.
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
  останавливается. Сам сборщик эти признаки не распознаёт, поэтому W1
  читает его журнал во время сбора. При признаке, пяти вылетах подряд без
  карточки или превышении времени W1 обрывает свой прогон. Отказы на
  карточку пин пишет только в счётчик `sources_rejected`; по нему W1
  останавливается до пересчёта. Номера взамен неудачных не подставляются.
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
владельца, продолжать ли до 500. Блок W1 печатает предложение
`DECISION=…` по правилу из §14 (W1); решает владелец.

Канарейка выполнена 08.10.2026 (§8); владелец решил GO_TO_500. Остальные
450 собирает W2 (§14) по отдельному списку: замороженный `pilot_ids.txt`
минус `canary_ids.txt`. Второй раз канарейка не посещается.

## 8. Результаты пилота

**W1, канарейка 50 — факт** (SRV-YOQSH, 08.10.2026, `STEP=PASS`, прогон
`20261008_151912`, run id `sources:ids-file:20261008T101922Z`):

| | |
|---|---|
| запрошено / посещено / карточек | 50 / 50 / 50 |
| ошибок | 0 |
| новых ревизий источников | 200 |
| время сбора | 430 с (8,6 с на вылет) |
| EXACT / IDENTIFIED | 14 / 3 |
| подтверждено | 17 из 50 = 34,0 % (95 % Уилсон: 22,4–47,8 %) |
| NO_KEY / NOT_IN_CATALOG | 6 / 27 |

Сентябрь после S1: 7 240 вылетов. Подтверждено 279 (EXACT 129,
IDENTIFIED 150), NO_CARD 6 017, NO_KEY 439, NOT_IN_CATALOG 497.

34 % — доля на 50 вылетах канарейки, и интервал у неё широкий. На когорту
NO_CARD она не переносится и проекцией не объявляется. Оценка на всю
когорту будет после W2+S2 на 500.

**W2+S2, остальные 450** — подготовлен (§14), ждёт запуска владельцем.

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

Время на посещение до пилота не измерено, канарейка его меряет. Замер
W1: 430 с на 50 вылетов, 8,6 с на вылет, все V4 пришли. Опора
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

S2 печатает строки `DIAG` — только счётчики, номера вылетов и ИМЕНА полей
карточки; ключи, тела и координаты не печатаются:

* NOT_IN_CATALOG:
  * формат ключа — составной `uuid__md5` или только md5;
  * сколько разных контуров и сколько из них стоят на нескольких вылетах;
  * сколько вылетов периода стоят на этих контурах;
  * когда снят каталог полей (первый и последний снимок);
* NO_KEY:
  * поле `geometry_md5` в карточке отсутствует, пусто или есть, но не
    прочитано (последнее — ошибка разбора);
  * `manual_mode` и `mode_name` рядом с теми же полями подтверждённых
    вылетов;
  * имена других полей карточки, значение которых похоже на ключ.

Это воспроизводимые примеры и гипотезы для следующего анализа, а не
разработка каталога.

## 13. Рекомендация

После пилота.

## 14. Блоки владельца

Порядок. Каждый блок — один, вывод присылается целиком, следующий выдаётся
после разбора:

| Шаг | Где | Что | Пишет |
|---|---|---|---|
| B0 | SRV-YOQSH | онлайн-копия production, перепись сентября и августа, замороженная выборка, отпечатки | только папка пилота на D: и C:\VehicleSoft_CardPilot |
| B1 | SRV-YOQSH | после мержа PR занятия площадки: задача `DjiAreaRefreshStaging` сверяется с D1 и отключается первым изменением, всё для точного возврата и две онлайн-копии базы площадки, боты площадки — стоп и `Disabled`, кнопка DJI площадки выключена, закреплённая ревизия, база площадки = копия B0 (сверка sha256 и отпечатком до и после пуска), пуск только службы | площадка |
| D1 | SRV-YOQSH | только чтение: задача планировщика `DjiAreaRefreshStaging`, на которой встал первый B1 — действие, триггеры, учётная запись, прогоны, что она пишет в базу площадки, связь с кнопкой | только журнал блока в C:\VehicleSoft_CardPilot |
| W0 | рабочая машина (выполнен на SRV-YOQSH, 08.10.2026) | только чтение: клоны сборщика, python, сессия DJI, замок, очереди, задачи и процессы, окружение, связь с площадкой; общие ли сессия и замок с production-сборщиком SRV-YOQSH и когда production собирает (с сервера — только метаданные) | только журнал блока в C:\VehicleSoft_CardPilot |
| W1 + S1 | SRV-YOQSH, чекаут пилота (выполнен 08.10.2026: `STEP=PASS`, `GO_TO_500`) | один блок. Канарейка 50: `--sources --ids-file canary_ids.txt --send-sources` только на `:5051`, сессия и замок production (только чтение и общий замок), своя очередь, надзор за журналом, ворота сборщика. Только если ворота прошли — S1: служба площадки стоп → `dji_area_recalc.py --apply --flight-id` (50) → `measure --stage canary` → перепись → пуск. Итог — `DECISION=GO_TO_500 / STOP / SIMPLIFY` | площадка, DJI — 50 посещений, C:\VehicleSoft_CardPilot\w1 |
| W2 + S2 | SRV-YOQSH, чекаут пилота | файл `ops/drone_card_coverage_001/W2_S2_remaining450_block.ps1`, по решению владельца GO_TO_500 (08.10.2026). Сбор 450 = `pilot_ids.txt` минус `canary_ids.txt`, так же, как W1. Только после ворот: пересчёт ровно 450, `measure --stage pilot` на всех 500 с журналами W1 и W2, перепись, диагностика NO_KEY и NOT_IN_CATALOG | площадка, DJI — 450 посещений, C:\VehicleSoft_CardPilot\w2 |
| R | SRV-YOQSH | копия базы пилота сохраняется; база, HEAD, окружение, службы площадки и задача `DjiAreaRefreshStaging` — как до B1 (задача не запускается); production-сверка | площадка |

Список вылетов W1 берёт из папки B0 на SRV-YOQSH и сверяет его sha256 с
`plan.json` и с замороженным значением в блоке. W2 строит свой список из
тех же двух файлов B0 после такой же сверки.

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
     `AppEnvironment` или в значении `Environment` службы — отказ;
   * задачу `DjiAreaRefreshStaging` — ровно та, что показал D1: одна задача
     с этим именем, папка `\`, `Enabled=True`, не `Running`, нет процессов
     цикла площадки, отпечатки действия, триггеров и выгруженного XML
     равны выводу D1, внешняя обёртка
     `C:\ProgramData\VehicleSoft\DjiAreaRefreshStaging.ps1` есть и её
     sha256 равен выводу D1 (XML задачи не меняется, когда меняется
     обёртка, поэтому её sha256 проверяется отдельно). Любое расхождение —
     отказ до изменений. В общей проверке задач она видна как
     `KNOWN_AND_DISABLED_FOR_PILOT`, а не как разрешённая; любая другая
     задача, пишущая в площадку, по-прежнему — отказ.
2. **Пишет всё для точного возврата** в
   `D:\transport-report-backups\staging\card_pilot\staging_<время>`:
   HEAD и ветку, состояние и тип запуска трёх служб, размер базы,
   production HEAD и службы, состояние задачи (`task_before.txt`: папка,
   `Enabled`, `State`, три отпечатка, путь и sha256 обёртки).
3. **Задача отключается — это первое изменение.** Задача читается заново;
   если она уже `Running` или идёт процесс цикла — отказ без изменений:
   ни задача, ни процесс не останавливаются принудительно. Затем пишется
   отметка `task_isolation.txt` («R вернёт задачу»), выполняется
   `Disable-ScheduledTask` только для `\DjiAreaRefreshStaging`, повторное
   чтение доказывает `Enabled=False` / `Disabled` и что процессов цикла
   нет; затем — `task_disabled.txt`. Только после этого — первая
   онлайн-копия базы площадки (`staging_before`, с `integrity_check`,
   sha256 в `staging_backup.txt`).
4. **Боты** — `Disabled` и стоп, затем стоп службы площадки и проверка,
   что базу никто не держит. `DJI_REFRESH_LAUNCHER` убирается из
   окружения службы: в `refresh_launcher.txt` — эта строка и её номер, в
   `env_extra_before.txt` — число строк и sha256 всего списка (значения
   других переменных не печатаются и не сохраняются). Вторая онлайн-копия
   (`staging_final`) — это то, что R вернёт: после остановки служб в базу
   больше никто не пишет.
5. **Ревизия пилота** `39eab50`, `compileall`, тесты инструмента и ядра
   паспорта.
6. **База площадки = копия B0**: проверка блокировки, файл копируется,
   sha256 равен копии (`placed.txt`), отпечаток (`fingerprint`) равен
   `plan\fingerprint_before.json` байт в байт, реестр — 60 миграций.
   Миграции не запускаются.
7. **Пуск только службы площадки**: `/login` — 200, `/drones/fields` без
   входа ведёт на форму входа. Отпечаток после пуска снова равен B0,
   реестр — 60. Боты — `Stopped` и `Disabled`. Оба барьера на месте:
   задача `Disabled` и `DJI_REFRESH_LAUNCHER` нет в окружении службы.
8. **Production после**: HEAD и три службы `Running` — как до. Задачи
   production не трогаются.

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
  $isoTask      = 'DjiAreaRefreshStaging'
  $isoPath      = '\'
  $isoEnabled   = 'True'
  $isoActionFp  = 'ba8fe81d76697c38e7aa3e8f42069839365687652b00d45996d688b137845082'
  $isoTriggerFp = '36a9e7f1c95b82ffb99743e0c5c4ce95d83c9a430aac59f84ef3cbfab6145068'
  $isoXmlSha    = 'ac522141953b6346ff76d2b0cad86f00ae10a52988984c2c4c5d8d91fc4f5117'
  $isoWrapper   = 'C:\ProgramData\VehicleSoft\DjiAreaRefreshStaging.ps1'
  $isoWrapperSha = '8ca2aeddfe664ce471bcdc991ea973ded9da2a2039e9de2ce8185b8c88b163c1'
  $planDir      = Join-Path $baseline 'plan'
  $stamp        = Get-Date -Format 'yyyyMMdd_HHmmss'
  $runDir       = Join-Path $runRoot ('staging_' + $stamp)
  $siteParams   = $svcKey + '\' + $service + '\Parameters'
  $prodWant     = 'TransportBot=Running TransportBot003=Running TransportReport=Running'
  function Get-ProdServices { (@($prodNames | ForEach-Object { $s = Get-Service -Name $_ -ErrorAction SilentlyContinue; if ($s) { $_ + '=' + $s.Status } else { $_ + '=missing' } }) -join ' ') }
  function Get-ListHash([string[]]$list) { $h = [System.Security.Cryptography.SHA256]::Create(); -join ($h.ComputeHash([System.Text.Encoding]::UTF8.GetBytes(($list -join "`n"))) | ForEach-Object { $_.ToString('x2') }) }
  function Get-TextHash([string]$text) { $h = [System.Security.Cryptography.SHA256]::Create(); -join ($h.ComputeHash([System.Text.Encoding]::UTF8.GetBytes($text)) | ForEach-Object { $_.ToString('x2') }) }
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
  function Get-IsoState([string]$name, [string]$wrapper) {
    $all = @(Get-ScheduledTask -TaskName $name -ErrorAction SilentlyContinue)
    if ($all.Count -ne 1) { throw "STEP FAILED: $($all.Count) scheduled tasks are named $name, expected exactly one -- send this output" }
    $t = $all[0]
    $actionLines = @(@($t.Actions) | ForEach-Object { [string]$_.Execute + '|' + [string]$_.Arguments + '|' + [string]$_.WorkingDirectory })
    $triggerLines = @(@($t.Triggers) | ForEach-Object { Get-CimLine $_ })
    $wrapperSha = 'missing'
    if (Test-Path -LiteralPath $wrapper) { $wrapperSha = (Get-FileHash -LiteralPath $wrapper -Algorithm SHA256).Hash.ToLower() }
    [pscustomobject]@{ Path = [string]$t.TaskPath; State = [string]$t.State; Enabled = [string]$t.Settings.Enabled; ActionFp = (Get-TextHash ($actionLines -join "`n")); TriggerFp = (Get-TextHash ($triggerLines -join "`n")); XmlSha = (Get-TextHash ([string](Export-ScheduledTask -TaskName $t.TaskName -TaskPath $t.TaskPath))); WrapperSha = $wrapperSha }
  }
  function Get-IsoProcesses([string]$name) { @(Get-CimInstance -ClassName Win32_Process | Where-Object { $c = [string]$_.CommandLine; ($c -match [regex]::Escape($name)) -or (($c -match 'dji_area_daily|dji_area_recalc|drone_collector') -and ($c -match 'transport-report-staging')) }) }
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

    $iso = Get-IsoState $isoTask $isoWrapper
    Write-Output ("ISOLATE_TASK " + $isoTask + " path=" + $iso.Path + " state=" + $iso.State + " enabled=" + $iso.Enabled + " wrapper=" + $iso.WrapperSha)
    if ($iso.Path -ne $isoPath) { throw "STEP FAILED: $isoTask is in folder $($iso.Path), D1 saw $isoPath -- send this output" }
    if ($iso.Enabled -ne $isoEnabled) { throw "STEP FAILED: $isoTask Enabled=$($iso.Enabled), D1 saw $isoEnabled -- send this output" }
    if ($iso.State -eq 'Running') { throw "STEP FAILED: $isoTask is running now; nothing was changed -- run B1 again after it finishes" }
    foreach ($c in @(@('action', $iso.ActionFp, $isoActionFp), @('trigger', $iso.TriggerFp, $isoTriggerFp), @('task XML', $iso.XmlSha, $isoXmlSha), @('wrapper', $iso.WrapperSha, $isoWrapperSha))) {
      if ($c[1] -ne $c[2]) { throw "STEP FAILED: the $($c[0]) fingerprint of $isoTask is $($c[1]), D1 saw $($c[2]) -- send this output" }
    }
    $isoProcs = @(Get-IsoProcesses $isoTask)
    if ($isoProcs.Count -gt 0) { throw "STEP FAILED: $($isoProcs.Count) process(es) of the staging refresh cycle are running; nothing was changed -- run B1 again after they finish" }
    Write-Output 'ISOLATE_TASK_VERIFIED=action, triggers, task XML and wrapper equal D1; not running'

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
      if (($t.TaskName -eq $isoTask) -and ([string]$t.TaskPath -eq $isoPath)) { $kind = 'KNOWN_AND_DISABLED_FOR_PILOT' }
      $ok = (@('production', 'other', 'KNOWN_AND_DISABLED_FOR_PILOT') -contains $kind) -or (($knownTasks -contains $t.TaskName) -and ($text -match 'backup_transport_db\.py|backup_staging_db\.bat'))
      Write-Output ("TASK " + $t.TaskName + " " + $t.State + " " + $kind + " exe=" + $exe + " wd=" + ($wds -join ' ; ') + $(if ($ok) { '' } else { ' REFUSED' }))
      if (-not $ok) { $refused += $t.TaskName }
    }
    if ($refused.Count -gt 0) { throw "STEP FAILED: enabled scheduled task(s) that may write to staging: $($refused -join ', ') -- send this output" }
    Write-Output ("TASKS_CHECKED=" + $seen + " enabled (" + ($knownTasks -join ', ') + " only backs up the staging database; " + $isoTask + " is disabled for the pilot before any other change; production tasks write production only; 'other' lines are reviewed before the canary)")

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

    Write-Output '== 2. Everything needed for the exact restore (nothing is changed yet)'
    New-Item -ItemType Directory -Force -Path $runDir | Out-Null
    Set-Content -LiteralPath (Join-Path $runDir 'before_head.txt') -Value $before -Encoding ASCII
    Set-Content -LiteralPath (Join-Path $runDir 'before_ref.txt') -Value $beforeRef -Encoding ASCII
    Set-Content -LiteralPath (Join-Path $runDir 'services_before.txt') -Value $states -Encoding ASCII
    Set-Content -LiteralPath (Join-Path $runDir 'staging_db_before.txt') -Value ($db + '|' + $stagingBytes) -Encoding ASCII
    Set-Content -LiteralPath (Join-Path $runDir 'prod_head.txt') -Value $prodHead -Encoding ASCII
    Set-Content -LiteralPath (Join-Path $runDir 'prod_services.txt') -Value $prodServices -Encoding ASCII
    Set-Content -LiteralPath (Join-Path $runDir 'baseline.txt') -Value @($baseline, $snapshot, $snapSha.ToLower()) -Encoding ASCII
    Set-Content -LiteralPath (Join-Path $runDir 'task_before.txt') -Value @(('name=' + $isoTask), ('path=' + $iso.Path), ('enabled=' + $iso.Enabled), ('state=' + $iso.State), ('action=' + $iso.ActionFp), ('trigger=' + $iso.TriggerFp), ('xml=' + $iso.XmlSha), ('wrapper=' + $isoWrapper), ('wrapper_sha=' + $iso.WrapperSha)) -Encoding ASCII

    Write-Output '== 3. The staging refresh task disabled for the pilot (the first change)'
    $again = Get-IsoState $isoTask $isoWrapper
    if (($again.Path -ne $isoPath) -or ($again.Enabled -ne $isoEnabled) -or ($again.ActionFp -ne $isoActionFp) -or ($again.TriggerFp -ne $isoTriggerFp) -or ($again.XmlSha -ne $isoXmlSha) -or ($again.WrapperSha -ne $isoWrapperSha)) { throw "STEP FAILED: $isoTask changed after the checks; nothing was changed -- send this output" }
    if (($again.State -eq 'Running') -or (@(Get-IsoProcesses $isoTask).Count -gt 0)) { throw "STEP FAILED: $isoTask started running; nothing was changed -- run B1 again after it finishes" }
    Set-Content -LiteralPath (Join-Path $runDir 'task_isolation.txt') -Value ("$isoTask is disabled for the pilot; block R gives it back enabled=" + $again.Enabled + " from task_before.txt and never starts it") -Encoding ASCII
    $touched = $true
    if ($again.Enabled -eq 'True') { Disable-ScheduledTask -TaskName $isoTask -TaskPath $isoPath | Out-Null }
    $off = Get-IsoState $isoTask $isoWrapper
    if (($off.Enabled -ne 'False') -or ($off.State -ne 'Disabled')) { throw "STEP FAILED: $isoTask is Enabled=$($off.Enabled) State=$($off.State) after it was disabled" }
    if (@(Get-IsoProcesses $isoTask).Count -gt 0) { throw "STEP FAILED: a run of $isoTask is in progress -- send this output" }
    Set-Content -LiteralPath (Join-Path $runDir 'task_disabled.txt') -Value ("$isoTask Enabled=False State=Disabled") -Encoding ASCII
    Write-Output ("ISOLATE_TASK_NOW=Disabled (was enabled=" + $again.Enabled + "; block R gives that back, nothing here starts it)")
    Save-Backup 'staging_before'

    Write-Output '== 4. Staging bots disabled and stopped, then the staging site stopped'
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

    Write-Output '== 5. The pilot revision of the code'
    git checkout --quiet --detach $pin
    if ($LASTEXITCODE -ne 0) { throw 'STEP FAILED: git checkout of the pilot revision' }
    $head = [string](git rev-parse HEAD)
    if ($head -ne $pin) { throw "STEP FAILED: after checkout HEAD is $head, expected $pin" }
    Write-Output ("AFTER_HEAD=" + $head)
    & $python -m compileall -q .
    if ($LASTEXITCODE -ne 0) { throw 'STEP FAILED: compileall' }
    & $python -m unittest tests.test_dji_card_coverage_pilot tests.test_drone_field_passport_core
    if ($LASTEXITCODE -ne 0) { throw 'STEP FAILED: pilot tool and field passport tests' }

    Write-Output '== 6. The staging database becomes the exact B0 snapshot'
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

    Write-Output '== 7. Starting the staging site only'
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

    $fin = Get-IsoState $isoTask $isoWrapper
    $launcherLeft = @(@((Get-ItemProperty -LiteralPath $siteParams).AppEnvironmentExtra) -match '^\s*DJI_REFRESH_LAUNCHER=').Count
    if (($fin.Enabled -ne 'False') -or ($launcherLeft -ne 0)) { throw "STEP FAILED: the barriers are not both in place: $isoTask Enabled=$($fin.Enabled), DJI_REFRESH_LAUNCHER lines=$launcherLeft" }
    Write-Output ("BARRIERS=" + $isoTask + " Disabled, DJI_REFRESH_LAUNCHER absent from the staging site")

    Write-Output '== 8. Production after -- must be exactly as before'
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
* `SERVICE_BEFORE …` — три строки;
* `ISOLATE_TASK DjiAreaRefreshStaging path=\ state=Ready enabled=True wrapper=8ca2aedd…`,
  `ISOLATE_TASK_VERIFIED=…`;
* строки `TASK …` (ни одной с `REFUSED`), среди них
  `TASK DjiAreaRefreshStaging Ready KNOWN_AND_DISABLED_FOR_PILOT`, и
  `TASKS_CHECKED=…`; `SITE_ENV_NAMES=…` (только имена);
  `DJI_REFRESH_LAUNCHER_BEFORE=schtasks (staging site; switched off for the pilot)`;
* `ISOLATE_TASK_NOW=Disabled (was enabled=True; …)`;
* `STAGING_BACKUP staging_before … integrity=ok`;
* `BOT_NOW … Stopped Disabled` — две строки, `SERVICE_STOPPED=Stopped`,
  `DB_LOCK_AFTER_STOP=0` или `3`; если кнопка была включена —
  `DJI_REFRESH_LAUNCHER_NOW=absent`;
* `STAGING_BACKUP staging_final … integrity=ok`;
* `AFTER_HEAD=39eab50…`, тесты `OK`;
* `PLACED=… (equals the B0 snapshot)`, `FINGERPRINT_PLACED=equals …`,
  `REGISTERED=60`;
* `SMOKE_LOGIN=200`, `FIELDS_ANONYMOUS=…`, `FINGERPRINT_STARTED=equals …`,
  `REGISTERED_AFTER_START=60`, `BOT_FINAL … Stopped Disabled` — две строки,
  `BARRIERS=DjiAreaRefreshStaging Disabled, DJI_REFRESH_LAUNCHER absent from the staging site`;
* `PROD_HEAD_AFTER=8df5683…`, `PROD_SERVICES_AFTER=` — три `Running`;
* `FINAL_HEAD=39eab50…`, `SERVICE_FINAL=Running`, `RUN=…`.

Прислать весь вывод.

**Живой вывод 03.10.2026 — PASS** (проверен независимо). Прогон
`D:\transport-report-backups\staging\card_pilot\staging_20261003_160830`.

* Production: `8df5683`, три службы `Running`.
* Площадка: `39eab50`, `TransportReportStaging` — `Running`, оба бота —
  `Stopped Disabled`.
* Барьеры: `DjiAreaRefreshStaging` — `Disabled`, `DJI_REFRESH_LAUNCHER` в
  окружении площадки нет.
* `staging_before` и `staging_final` — по 413 241 344 байт, sha256
  `cbedf7b3…0751`, `integrity=ok`.
* База пилота — ровно копия B0 (sha256 `13d10bd5…0018`). Отпечаток после
  размещения и после пуска площадки равен B0; миграций 60 до и после пуска.

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

**Живой вывод 03.10.2026 — PASS, ничего не изменено.** Одна задача, папка
`\`, `Ready`, `Enabled=True`, учётная запись `S4U`/`Highest`,
`MultipleInstances=IgnoreNew`, `StartWhenAvailable=True`; действие —
`powershell.exe -File "C:\ProgramData\VehicleSoft\DjiAreaRefreshStaging.ps1"`.
Обёртка пишет в базу площадки через `tools\dji_area_daily.py --run-queued
--stop-above 50`, приём — только на `127.0.0.1:5051`, сборщик — venv,
сессия и замок production. `STAGING_WRITER=yes`, процессов нет; в журнале
циклов площадки 3 ручных и 1 плановый прогон. Кнопка площадки запускает
именно её (`DJI_REFRESH_LAUNCHER=schtasks`, `BUTTON_STARTS_THIS_TASK=yes`).
Отпечатки действия, триггеров, XML и sha256 обёртки зашиты в B1.

### W0 — рабочая машина: разведка перед канарейкой (только чтение)

**Зачем.** Сбор W1 пойдёт с рабочей машины, а там своя история: клоны
квалификации и holdout разных ревизий, своя сессия DJI, свои задачи. Пути
на ней не угадываются — W0 их находит. Ещё W0 отвечает на вопрос из §5:
общие ли у неё сессия и замок с production-сборщиком SRV-YOQSH
(`C:\transport-report\drone_collector\data\storage_state.json` и
`collector.lock`) и когда production собирает сам.

**Где.** На той машине, с которой пойдёт сбор W1. Окно PowerShell того
пользователя Windows, под которым работает сборщик; лучше «от имени
администратора» — тогда видны командные строки процессов других
пользователей.

**Что W0 не делает.** Не запускает ни сборщик, ни python, ни браузер. К DJI
не обращается. Ни одного POST. Не трогает замки, очереди, сессии, задачи,
службы и реестр. Из git — только чтение: `rev-parse`, `symbolic-ref`,
`tag --points-at`, `status` без замка индекса, `remote get-url`,
`cat-file -e`, `diff --quiet`; ничего не скачивает. Пишет только свой
журнал в `C:\VehicleSoft_CardPilot`.

**С SRV-YOQSH — только метаданные**, из фонового задания с потолком 90 с:

* размер и время изменения `storage_state.json` и `collector.lock`;
* подсказка владельца замка (`collector.lock.owner`: назначение, начало,
  машина);
* задачи планировщика, связанные со сборщиком: имя, состояние, расписание,
  следующий и последний запуск, исполняемый файл без аргументов.

Содержимое сессии не читается ни на сервере, ни на рабочей машине: только
размер, время и sha256. Если сервер отсюда не читается (нет прав, закрыт
брандмауэр), это печатается и остановкой не считается.

**Что печатает** (по пунктам задания):

1. Машина: имя, Windows, PowerShell, пользователь и права, диски, время и
   пояс.
2. Окружение: имена `DJI_*`, `DRONE_*`, `VEHICLE_SOFT_*`, `PLAYWRIGHT_*` и
   прокси — на уровне машины, пользователя и этой консоли. Значения —
   только у путей, переключателей и адреса приёмника, и то без
   логина/пароля в адресе. Токены — только «задан».
3. Поиск по локальным дискам на глубину 5:
   * клоны (`drone_collector\main.py`);
   * venv (`pyvenv.cfg`);
   * файлы `storage_state.json`;
   * папки браузеров Playwright.

   Пропускаются системные папки, `AppData`, `node_modules`, `.git` и
   ссылки-перенаправления. Потолок поиска — 180 с.
4. Что может запустить сборщик: задачи планировщика (кроме
   `\Microsoft\`), службы, процессы, автозапуск.
   * У связанных задач: действие (секреты в аргументах скрыты), триггеры,
     последний и следующий запуск.
   * У обёрток (`.ps1`/`.bat`/`.cmd`): sha256, что упоминают, каким
     переменным присваивают значения (только имена), какой python.
   * Остальные задачи — одной строкой имён.

   Клон из рабочей папки задачи добавляется, даже если поиск до него не
   дошёл.
5. Каждый клон:
   * git: HEAD, ветка, теги, изменённые отслеживаемые файлы, `origin` без
     учётных данных; совпадает ли код сборщика с пином пилота `39eab50`;
   * что поддерживает: `--sources`, `--ids-file`, `--send-sources`, замок,
     `DRONE_OUTBOX_DIR`;
   * имена в `.env`; действующие адрес приёмника, токен (только «задан»),
     сессия, замок, очередь, `DJI_HEADLESS` — и откуда каждое значение
     (окружение пользователя или машины перекрывает `.env`);
   * сессия — размер, время, sha256;
   * замок и подсказка владельца;
   * очередь — `pending`/`sent`/`corrupt` по видам и `.tmp`;
   * журналы.
6. Python: каждый venv — версия, `home`, Playwright, python-dotenv,
   requests. Python, который называют задачи и процессы. Папки браузеров.
7. Общее с production:
   * каждый `storage_state.json`: `SAME_FILE`, `LIKELY_COPY` (тот же
     размер и время), `NOT_THE_SAME` или «сравнить отсюда нельзя»; одинаковые
     байты между файлами этой машины;
   * замки: на рабочей машине замок не останавливает сбор на SRV-YOQSH, и
     наоборот;
   * держит ли production свой замок сейчас;
   * задачи сбора production с расписанием.
8. Сеть: GET `/login` на 5051 (ожидается 200 с формой входа) и на 5050
   (только связность).
9. Для W1:
   * клоны-кандидаты;
   * предлагаемая папка пилота `C:\VehicleSoft_CardPilot\w1`: существует
     ли, пересекается ли с клонами, очередями и сессиями;
   * существующие очереди и замки.

   Ничего не создаётся.

**Итог.** Строки `ATTENTION` — то, что план W1 обязан учесть:

* включённая задача сбора на этой машине;
* сборщик, идущий сейчас;
* занятый замок;
* переменная окружения, перекрывающая `.env`;
* клон, который шлёт на production;
* площадка не отвечает;
* поиск не уложился во время.

`ATTENTION` — не остановка. Последняя строка — одна из двух:

* `STEP=PASS (read only: nothing operational was changed)` — разведка
  полная;
* `STEP=STOP - …` — не найден ни один клон сборщика или раздел не
  прочитался (`SECTION_FAILED`). Всё остальное напечатано и в этом случае.

```powershell
& {
  $ErrorActionPreference = 'Stop'
  $ProgressPreference = 'SilentlyContinue'
  $server       = 'srv-yoqsh'
  $serverData   = 'C:\transport-report\drone_collector\data'
  $stagingLogin = 'http://10.103.25.14:5051/login'
  $prodLogin    = 'http://10.103.25.14:5050/login'
  $pin          = '39eab503069b7bb01a8342542edcbebbfc2210c2'
  $work         = 'C:\VehicleSoft_CardPilot'
  $pilotDir     = 'C:\VehicleSoft_CardPilot\w1'
  $maxDepth     = 5
  $budgetSec    = 180
  $remoteWait   = 90
  $stamp        = Get-Date -Format 'yyyyMMdd_HHmmss'
  $showValue    = @('VEHICLE_SOFT_BASE_URL', 'DJI_STORAGE_STATE', 'DJI_COLLECTOR_LOCK_PATH', 'DJI_COLLECTOR_LOCK_WAIT_S', 'DJI_HEADLESS', 'DRONE_OUTBOX_DIR', 'DJI_SOURCE_PAUSE_MS', 'DJI_SOURCE_WAIT_MS', 'DJI_SOURCE_BATCH_SIZE', 'DJI_EXPECTED_REGION', 'DJI_TZ_OFFSET_HOURS', 'PLAYWRIGHT_BROWSERS_PATH')
  $skipDirs     = @('Windows', 'Program Files', 'Program Files (x86)', '$Recycle.Bin', 'System Volume Information', 'Recovery', 'Config.Msi', 'PerfLogs', 'AppData', 'node_modules', '.git', '__pycache__', 'site-packages', 'ms-playwright', 'playwright-browsers', 'Package Cache', 'WinSxS')
  $collectorRx  = 'drone_collector|dji_area_daily|dji_area_backfill|dji_area_recalc|--save-session'
  $relevantRx   = 'drone_collector|dji_area|djiag|storage_state|collector\.lock|VehicleSoft|vehicle-soft|transport-report|Transport(Report|Bot)|Holdout|playwright|:505[01]\b|10\.103\.25\.14|srv-yoqsh'
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
  function Show-Url([string]$u) {
    $x = $null
    if ([uri]::TryCreate($u.Trim(), [UriKind]::Absolute, [ref]$x) -and $x.Host) { return ($x.Scheme + '://' + $x.Host + ':' + $x.Port + $x.AbsolutePath) }
    '[not a URL; value not shown]'
  }
  function Show-Value([string]$name, $value) {
    if ($null -eq $value) { return 'absent' }
    if ([string]$value -eq '') { return 'empty' }
    if ($showValue -notcontains $name) { return 'set (value not shown)' }
    if ($name -like '*_URL') { return (Show-Url ([string]$value)) }
    Hide-Secret ([string]$value)
  }
  function Read-DotEnv([string]$path) {
    $h = @{}
    foreach ($line in [System.IO.File]::ReadAllLines($path)) {
      $m = [regex]::Match($line, '^\s*(?:export\s+)?([A-Za-z_][A-Za-z0-9_]*)\s*=\s*(.*?)\s*$')
      if (-not $m.Success) { continue }
      $v = $m.Groups[2].Value
      if (($v -match '^"(.*)"$') -or ($v -match "^'(.*)'$")) { $v = $Matches[1] } else { $v = $v -replace '\s+#.*$', '' }
      $h[$m.Groups[1].Value] = $v
    }
    $h
  }
  function Get-Effective([string]$name, $dot) {
    if ($userEnv.ContainsKey($name)) { return [pscustomobject]@{ Value = $userEnv[$name]; Source = 'user environment' } }
    if ($machineEnv.ContainsKey($name)) { return [pscustomobject]@{ Value = $machineEnv[$name]; Source = 'machine environment' } }
    if ($dot.ContainsKey($name)) { return [pscustomobject]@{ Value = $dot[$name]; Source = '.env' } }
    [pscustomobject]@{ Value = $null; Source = 'default' }
  }
  function Get-Under([string]$base, [string]$value, [string[]]$default) {
    if ($value) { if ([System.IO.Path]::IsPathRooted($value)) { return $value } else { return [System.IO.Path]::Combine($base, $value) } }
    [System.IO.Path]::Combine([string[]](@($base) + $default))
  }
  function Get-FileFacts([string]$p) {
    $f = [pscustomobject]@{ Path = $p; Exists = $false; Bytes = 0; Changed = ''; ChangedUtc = ''; Sha = '' }
    if (-not (Test-Path -LiteralPath $p -PathType Leaf)) { return $f }
    $f.Exists = $true
    try {
      $it = Get-Item -LiteralPath $p -Force
      $f.Bytes = $it.Length
      $f.Changed = $it.LastWriteTime.ToString('yyyy-MM-dd HH:mm:ss')
      $f.ChangedUtc = $it.LastWriteTimeUtc.ToString('yyyy-MM-dd HH:mm:ss')
      $f.Sha = (Get-FileHash -LiteralPath $p -Algorithm SHA256).Hash.ToLower()
    } catch { $f.Sha = 'not readable now' }
    $f
  }
  function Read-Text([string]$p) { try { [System.IO.File]::ReadAllText($p) } catch { '' } }
  function Get-DirLine([string]$dir) {
    if (-not (Test-Path -LiteralPath $dir -PathType Container)) { return 'absent' }
    $files = @(Get-ChildItem -LiteralPath $dir -File -Recurse -Force -ErrorAction SilentlyContinue)
    if ($files.Count -eq 0) { return 'files=0' }
    $new = $files | Sort-Object LastWriteTime -Descending | Select-Object -First 1
    'files=' + $files.Count + ' newest=' + $new.Name + ' ' + $new.LastWriteTime.ToString('yyyy-MM-dd HH:mm')
  }
  function Get-OutboxLine([string]$dir) {
    if (-not (Test-Path -LiteralPath $dir -PathType Container)) { return 'absent' }
    try { [void][System.IO.Directory]::GetDirectories($dir) } catch { return 'not readable' }
    $parts = @()
    foreach ($sub in @('pending', 'sent', 'corrupt')) {
      $p = Join-Path $dir $sub
      if (-not (Test-Path -LiteralPath $p -PathType Container)) { $parts += ($sub + '=none'); continue }
      $names = @([System.IO.Directory]::GetFiles($p, '*.json') | ForEach-Object { [System.IO.Path]::GetFileName($_) })
      $kinds = @($names | ForEach-Object { if ($_ -match '^(source|route|field_geometry|land_snapshot)_') { $Matches[1] } else { 'other' } } | Group-Object | Sort-Object Name | ForEach-Object { $_.Name + '=' + $_.Count })
      $parts += ($sub + '=' + $names.Count + $(if ($kinds.Count) { ' (' + ($kinds -join ' ') + ')' } else { '' }))
    }
    ($parts -join ' ') + ' tmp=' + @([System.IO.Directory]::GetFiles($dir, '*.tmp', [System.IO.SearchOption]::AllDirectories)).Count
  }
  function Get-LockLine([string]$lock) {
    $l = $lock
    $f = Get-FileFacts $lock
    if ($f.Exists) { $l += ' file=present changed=' + $f.Changed } else { $l += ' file=absent' }
    if (Test-Path -LiteralPath ($lock + '.owner') -PathType Leaf) {
      try {
        $o = Get-Content -LiteralPath ($lock + '.owner') -Raw | ConvertFrom-Json
        $alive = [bool](Get-Process -Id ([int]$o.pid) -ErrorAction SilentlyContinue)
        $l += ' owner_hint=present pid=' + $o.pid + ' host=' + $o.host + ' purpose=' + $o.purpose + ' since_utc=' + $o.since_utc + ' pid_running_here=' + $(if ($alive) { 'yes' } else { 'no' })
      } catch { $l += ' owner_hint=present, not readable' }
    } else { $l += ' owner_hint=none (by the collector rule nobody holds it)' }
    $l
  }
  function Get-PyVersion([string]$exe) {
    if (-not (Test-Path -LiteralPath $exe -PathType Leaf)) { return 'missing' }
    try {
      $cfg = Join-Path (Split-Path -Parent (Split-Path -Parent $exe)) 'pyvenv.cfg'
      if (Test-Path -LiteralPath $cfg) { $m = @(Select-String -LiteralPath $cfg -Pattern '^\s*version(_info)?\s*=\s*(\S+)'); if ($m.Count) { return ('venv ' + $m[0].Matches[0].Groups[2].Value) } }
      $v = (Get-Item -LiteralPath $exe).VersionInfo.ProductVersion
      if ($v) { $v } else { 'unknown' }
    } catch { 'unknown' }
  }
  function Get-PythonRefs([string]$text) { @([regex]::Matches($text, '(?i)[A-Za-z]:\\[^"''<>|\r\n]*?\\pythonw?\.exe') | ForEach-Object { $_.Value } | Select-Object -Unique) }
  function Invoke-Git([string]$dir, [string[]]$gitArgs) {
    $ErrorActionPreference = 'Continue'
    $out = @(& git -c 'core.fsmonitor=false' -c 'safe.directory=*' -C $dir @gitArgs 2>&1 | ForEach-Object { [string]$_ })
    [pscustomobject]@{ Code = $LASTEXITCODE; Lines = $out }
  }
  $log = Join-Path $work ('card_pilot_w0_' + $stamp + '.log')
  try { New-Item -ItemType Directory -Force -Path $work | Out-Null; Start-Transcript -Path $log -Append | Out-Null } catch { Write-Output ('NOTE: the log file could not be started: ' + $_.Exception.Message) }
  $attention = New-Object System.Collections.ArrayList
  $failed = New-Object System.Collections.ArrayList
  $userEnv = @{}
  $machineEnv = @{}
  $checkouts = @()
  $venvs = @()
  $sessionFiles = @()
  $browserDirs = @()
  $pyRefs = @()
  $remote = $null
  $hostName = [string](hostname)
  $onServer = ($hostName -eq $server)

  Write-Output '== 1. This machine (W0 is read only: nothing operational is changed)'
  try {
    Write-Output ("HOST=" + $hostName)
    Write-Output ("HOST_ROLE=" + $(if ($onServer) { 'SRV-YOQSH itself, the production server -- not a separate workstation' } else { 'workstation, not SRV-YOQSH' }))
    $os = Get-CimInstance -ClassName Win32_OperatingSystem
    Write-Output ("WINDOWS=" + $os.Caption + " version=" + $os.Version + " build=" + $os.BuildNumber + " " + $os.OSArchitecture)
    $elevated = 'unknown'
    try { $elevated = [string]([Security.Principal.WindowsPrincipal][Security.Principal.WindowsIdentity]::GetCurrent()).IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator) } catch { }
    Write-Output ("POWERSHELL=" + $PSVersionTable.PSVersion + " USER=" + [Environment]::UserDomainName + '\' + [Environment]::UserName + " ELEVATED=" + $elevated)
    Write-Output ("TIME_LOCAL=" + (Get-Date).ToString('yyyy-MM-dd HH:mm:ss') + " UTC=" + (Get-Date).ToUniversalTime().ToString('yyyy-MM-dd HH:mm:ss') + " ZONE=" + [TimeZoneInfo]::Local.Id)
    $drives = @(Get-PSDrive -PSProvider FileSystem | Where-Object { (-not $_.DisplayRoot) -and ($null -ne $_.Free) })
    foreach ($d in $drives) { Write-Output ("DRIVE " + $d.Root + " free_gb=" + [math]::Round($d.Free / 1GB, 1) + " used_gb=" + [math]::Round($d.Used / 1GB, 1)) }
    $git = Get-Command git -ErrorAction SilentlyContinue
    Write-Output ("GIT=" + $(if ($git) { $git.Source } else { 'not installed' }))
  } catch { [void]$failed.Add('1 machine: ' + $_.Exception.Message + ' [block line ' + $_.InvocationInfo.ScriptLineNumber + ']'); Write-Output ('SECTION_FAILED ' + $failed[$failed.Count - 1]) }
  if (-not $onServer) {
    # Metadata only, from SRV-YOQSH: size and time of the production session and lock files, the owner hint of the lock,
    # and the task list. Started now, read in section 7; nothing on the server is opened for writing or run.
    try {
      $remote = Start-Job -ArgumentList $server, $serverData, $collectorRx -ScriptBlock {
        param($srv, $dataDir, $rx)
        $out = @()
        $share = '\\' + $srv + '\' + ($dataDir -replace '^([A-Za-z]):', '$1$')
        try {
          if (Test-Path -LiteralPath $share) {
            foreach ($f in @('storage_state.json', 'collector.lock', 'collector.lock.owner')) {
              $p = Join-Path $share $f
              if (Test-Path -LiteralPath $p) { $it = Get-Item -LiteralPath $p -Force; $out += ('FILE|' + $f + '|' + $it.Length + '|' + $it.LastWriteTimeUtc.ToString('yyyy-MM-dd HH:mm:ss')) } else { $out += ('FILE|' + $f + '|absent') }
            }
            $hint = Join-Path $share 'collector.lock.owner'
            if (Test-Path -LiteralPath $hint) { $o = Get-Content -LiteralPath $hint -Raw | ConvertFrom-Json; $out += ('OWNER|purpose=' + $o.purpose + ' since_utc=' + $o.since_utc + ' host=' + $o.host) }
          } else { $out += ('SHARE|' + $share) }
        } catch { $out += ('SHARE|' + $share + ' (' + $_.Exception.Message + ')') }
        try {
          $sched = New-Object -ComObject Schedule.Service
          $sched.Connect($srv)
          $folders = New-Object System.Collections.Queue
          $folders.Enqueue($sched.GetFolder('\'))
          $states = @('unknown', 'disabled', 'queued', 'ready', 'running')
          while ($folders.Count -gt 0) {
            $fo = $folders.Dequeue()
            if ($fo.Path -like '\Microsoft*') { continue }
            foreach ($sub in @($fo.GetFolders(0))) { $folders.Enqueue($sub) }
            foreach ($t in @($fo.GetTasks(1))) {
              $acts = @($t.Definition.Actions | ForEach-Object { [string]$_.Path + ' ' + [string]$_.Arguments + ' ' + [string]$_.WorkingDirectory })
              if (([string]$t.Name + ' ' + ($acts -join ' ')) -notmatch ($rx + '|DroneCollector|DroneArea|DjiArea')) { continue }
              $trs = @($t.Definition.Triggers | ForEach-Object { 'type' + $_.Type + ' start=' + $_.StartBoundary + ' enabled=' + $_.Enabled + $(if ($_.Repetition.Interval) { ' every=' + $_.Repetition.Interval } else { '' }) })
              $exe = @($t.Definition.Actions | ForEach-Object { [string]$_.Path }) -join ' ; '
              $out += ('TASK|' + $t.Path + ' state=' + $states[[int]$t.State] + ' enabled=' + $t.Enabled + ' next=' + $t.NextRunTime + ' last=' + $t.LastRunTime + ' result=' + $t.LastTaskResult + ' triggers=' + ($trs -join ' ; ') + ' exe=' + $exe)
            }
          }
          $out += 'SCHED|ok'
        } catch { $out += ('SCHED|' + $_.Exception.Message) }
        $out
      }
    } catch { Write-Output ("REMOTE_READ=not started: " + $_.Exception.Message) }
  }

  Write-Output '== 2. Environment: names; values only for paths, switches and the receiver address'
  try {
    foreach ($pair in @(@('machine', 'HKLM:\SYSTEM\CurrentControlSet\Control\Session Manager\Environment'), @('user', 'HKCU:\Environment'))) {
      try {
        $p = Get-ItemProperty -LiteralPath $pair[1] -ErrorAction Stop
        foreach ($q in $p.PSObject.Properties) { if ($q.Name -notmatch '^PS(Path|ParentPath|ChildName|Drive|Provider)$') { if ($pair[0] -eq 'machine') { $machineEnv[$q.Name] = [string]$q.Value } else { $userEnv[$q.Name] = [string]$q.Value } } }
      } catch { Write-Output ("ENV_SCOPE " + $pair[0] + " not readable: " + $_.Exception.Message) }
    }
    $console = @{}
    foreach ($e in @(Get-ChildItem Env:)) { $console[$e.Name] = [string]$e.Value }
    $names = @(@($machineEnv.Keys) + @($userEnv.Keys) + @($console.Keys) | Where-Object { ($_ -match '^(DJI_|DRONE_|VEHICLE_SOFT_|PLAYWRIGHT_)') -or ($_ -match '^(HTTPS?_PROXY|NO_PROXY|ALL_PROXY)$') } | ForEach-Object { $_.ToUpper() } | Sort-Object -Unique)
    foreach ($n in $names) {
      $m = if ($machineEnv.ContainsKey($n)) { $machineEnv[$n] } else { $null }
      $u = if ($userEnv.ContainsKey($n)) { $userEnv[$n] } else { $null }
      $c = if ($console.ContainsKey($n)) { $console[$n] } else { $null }
      Write-Output ("ENV " + $n + " machine=" + (Show-Value $n $m) + " user=" + (Show-Value $n $u) + " this_console=" + (Show-Value $n $c))
      if (($n -match '^(VEHICLE_SOFT_BASE_URL|DRONE_API_TOKEN|DJI_STORAGE_STATE|DJI_COLLECTOR_LOCK_PATH|DRONE_OUTBOX_DIR)$') -and (($null -ne $m) -or ($null -ne $u))) { [void]$attention.Add($n + ' is set in the ' + $(if ($null -ne $u) { 'user' } else { 'machine' }) + ' environment: it overrides the .env of every checkout on this machine (config.py: the process environment wins)') }
      elseif (($n -match '^(VEHICLE_SOFT_BASE_URL|DRONE_API_TOKEN)$') -and ($null -ne $c)) { [void]$attention.Add($n + ' is set in this console only (inherited): a collector started from this console would use it instead of .env') }
    }
    if ($names.Count -eq 0) { Write-Output 'ENV=no DJI_, DRONE_, VEHICLE_SOFT_, PLAYWRIGHT_ or proxy variables in the machine, user or console environment' }
  } catch { [void]$failed.Add('2 environment: ' + $_.Exception.Message + ' [block line ' + $_.InvocationInfo.ScriptLineNumber + ']'); Write-Output ('SECTION_FAILED ' + $failed[$failed.Count - 1]) }

  Write-Output ("== 3. Collector checkouts: local drives scanned to depth " + $maxDepth + " (folder names only)")
  try {
    $queue = New-Object System.Collections.Queue
    foreach ($d in $drives) { $queue.Enqueue(@([string]$d.Root, 0)) }
    $seen = 0
    $denied = 0
    $cut = $false
    $clock = [System.Diagnostics.Stopwatch]::StartNew()
    while ($queue.Count -gt 0) {
      if ($clock.Elapsed.TotalSeconds -gt $budgetSec) { $cut = $true; break }
      $item = $queue.Dequeue()
      $dir = [string]$item[0]
      $depth = [int]$item[1]
      $seen++
      if ([System.IO.File]::Exists([System.IO.Path]::Combine($dir, 'drone_collector', 'main.py'))) { $checkouts += $dir }
      if ([System.IO.File]::Exists([System.IO.Path]::Combine($dir, 'pyvenv.cfg'))) { $venvs += $dir; continue }
      if ([System.IO.File]::Exists([System.IO.Path]::Combine($dir, 'storage_state.json'))) { $sessionFiles += [System.IO.Path]::Combine($dir, 'storage_state.json') }
      if ($depth -ge $maxDepth) { continue }
      try { $subs = [System.IO.Directory]::GetDirectories($dir) } catch { $denied++; continue }
      foreach ($s in $subs) {
        $name = [System.IO.Path]::GetFileName($s)
        if ($skipDirs -contains $name) { if ($name -match 'playwright') { $browserDirs += $s }; continue }
        try { if (([System.IO.File]::GetAttributes($s) -band [System.IO.FileAttributes]::ReparsePoint) -ne 0) { continue } } catch { continue }
        $queue.Enqueue(@($s, ($depth + 1)))
      }
    }
    Write-Output ("SCAN roots=" + (@($drives | ForEach-Object { $_.Root }) -join ',') + " folders=" + $seen + " not_readable=" + $denied + " seconds=" + [math]::Round($clock.Elapsed.TotalSeconds) + " complete=" + $(if ($cut) { 'no (time limit ' + $budgetSec + ' s)' } else { 'yes' }))
    if ($cut) { [void]$attention.Add('the folder scan stopped at its time limit: a checkout deeper on the disk may be missing from this list') }
    Write-Output ("FOUND checkouts=" + $checkouts.Count + " venvs=" + $venvs.Count + " storage_state.json=" + $sessionFiles.Count + " playwright_browser_folders=" + $browserDirs.Count)
  } catch { [void]$failed.Add('3 scan: ' + $_.Exception.Message + ' [block line ' + $_.InvocationInfo.ScriptLineNumber + ']'); Write-Output ('SECTION_FAILED ' + $failed[$failed.Count - 1]) }

  Write-Output '== 4. What may start a collector here: scheduled tasks, services, processes, logon entries'
  try {
    $other = @()
    foreach ($t in @(Get-ScheduledTask | Where-Object { [string]$_.TaskPath -notlike '\Microsoft\*' })) {
      $acts = @($t.Actions)
      $wds = @($acts | ForEach-Object { [string]$_.WorkingDirectory } | Where-Object { $_ })
      $text = [string]$t.TaskName + ' ' + (@($acts | ForEach-Object { [string]$_.Execute + ' ' + [string]$_.Arguments + ' ' + [string]$_.WorkingDirectory }) -join ' ')
      $scripts = @(Get-ScriptPaths $text $wds | Where-Object { Test-Path -LiteralPath $_ -PathType Leaf })
      $all = $text + "`n" + (@($scripts | Where-Object { $_ -notmatch '\.py$' } | ForEach-Object { Read-Text $_ }) -join "`n")
      $hit = $all -match $relevantRx
      foreach ($c in $checkouts) { if ($all.ToLower().Contains($c.ToLower())) { $hit = $true } }
      if (-not $hit) { $other += ([string]$t.TaskPath + [string]$t.TaskName); continue }
      $collects = $all -match $collectorRx
      $ports = @([regex]::Matches($all, ':(505[01])\b') | ForEach-Object { $_.Groups[1].Value } | Select-Object -Unique)
      Write-Output ("TASK " + $t.TaskPath + $t.TaskName + " state=" + $t.State + " enabled=" + $t.Settings.Enabled + " user=" + $t.Principal.UserId + " logon=" + $t.Principal.LogonType + " runlevel=" + $t.Principal.RunLevel + " runs_collector=" + $(if ($collects) { 'yes' } else { 'no' }) + " ports=" + $(if ($ports.Count) { $ports -join ',' } else { 'none' }))
      $i = 0
      foreach ($a in $acts) { $i++; Write-Output ("  ACTION " + $i + " exe=" + (Hide-Secret ([string]$a.Execute)) + " args=" + (Hide-Secret ([string]$a.Arguments)) + " wd=" + [string]$a.WorkingDirectory) }
      $i = 0
      foreach ($tr in @($t.Triggers)) { $i++; Write-Output ("  TRIGGER " + $i + " " + (Get-CimLine $tr)) }
      if ($i -eq 0) { Write-Output '  TRIGGERS=none (runs only when started by hand or by another program)' }
      $next = ''
      try { $info = Get-ScheduledTaskInfo -TaskName $t.TaskName -TaskPath $t.TaskPath; $next = [string]$info.NextRunTime; Write-Output ("  RUNS last=" + $info.LastRunTime + " result=" + $info.LastTaskResult + " next=" + $info.NextRunTime) } catch { Write-Output ("  RUNS not readable: " + $_.Exception.Message) }
      foreach ($s in $scripts) {
        $f = Get-FileFacts $s
        $st = if ($s -match '\.py$') { '' } else { Read-Text $s }
        $refs = @(@('drone_collector', '--sources', '--ids-file', '--send-sources', '--save-session', 'dji_area_daily', '--run-queued', 'dji_area_recalc', 'storage_state', 'collector.lock', 'DJI_STORAGE_STATE', 'DJI_COLLECTOR_LOCK_PATH', 'DRONE_OUTBOX_DIR', 'PLAYWRIGHT_BROWSERS_PATH') | Where-Object { $st.Contains($_) })
        $setsEnv = @(@([regex]::Matches($st, '(?i)\$env:([A-Za-z_][A-Za-z0-9_]*)\s*=') | ForEach-Object { $_.Groups[1].Value }) + @([regex]::Matches($st, '(?im)^\s*set\s+"?([A-Za-z_][A-Za-z0-9_]*)=') | ForEach-Object { $_.Groups[1].Value }) | Select-Object -Unique)
        Write-Output ("  SCRIPT " + $s + " bytes=" + $f.Bytes + " sha256=" + $f.Sha + " mentions=" + ($refs -join ',') + " sets_env=" + ($setsEnv -join ',') + " python=" + ((Get-PythonRefs $st) -join ','))
      }
      foreach ($py in @(Get-PythonRefs $all)) { $pyRefs += ('task ' + $t.TaskName + '|' + $py) }
      foreach ($w in $wds) {
        $d = $w
        for ($k = 0; ($k -lt 4) -and $d; $k++) {
          if ([System.IO.File]::Exists([System.IO.Path]::Combine($d, 'drone_collector', 'main.py'))) { if (@($checkouts | Where-Object { $_.TrimEnd('\') -eq $d.TrimEnd('\') }).Count -eq 0) { $checkouts += $d; Write-Output ("  CHECKOUT_FROM_TASK " + $d) }; break }
          $d = Split-Path -Parent $d
        }
      }
      if ($collects -and ([string]$t.Settings.Enabled -eq 'True')) { [void]$attention.Add('enabled scheduled task ' + $t.TaskName + ' runs the collector or the daily cycle here (next run ' + $next + '): a pilot run must not overlap it') }
      if ($ports -contains '5050') { [void]$attention.Add('scheduled task ' + $t.TaskName + ' names port 5050 (production)') }
    }
    Write-Output ("OTHER_TASKS=" + $other.Count + $(if ($other.Count) { ': ' + ($other -join ', ') } else { '' }))
    $svcs = @(Get-CimInstance -ClassName Win32_Service | Where-Object { ([string]$_.Name + ' ' + [string]$_.PathName) -match $relevantRx })
    foreach ($s in $svcs) { Write-Output ("SERVICE " + $s.Name + " state=" + $s.State + " start=" + $s.StartMode + " account=" + $s.StartName + " path=" + (Hide-Secret ([string]$s.PathName))) }
    if ($svcs.Count -eq 0) { Write-Output 'SERVICES=none related to the collector or Vehicle Soft' }
    $procs = @(Get-CimInstance -ClassName Win32_Process)
    $running = @($procs | Where-Object { [string]$_.CommandLine -match $collectorRx })
    foreach ($p in $running) {
      $cmd = Hide-Secret ([string]$p.CommandLine)
      if ($cmd.Length -gt 300) { $cmd = $cmd.Substring(0, 300) + ' ...' }
      Write-Output ("PROCESS pid=" + $p.ProcessId + " name=" + $p.Name + " started=" + $p.CreationDate + " cmd=" + $cmd)
      foreach ($py in @(Get-PythonRefs ([string]$p.CommandLine + ' ' + [string]$p.ExecutablePath))) { $pyRefs += ('process ' + $p.ProcessId + '|' + $py) }
    }
    $browsersNow = @($procs | Where-Object { [string]$_.CommandLine -match 'ms-playwright|playwright-browsers|--remote-debugging-pipe' }).Count
    Write-Output ("COLLECTOR_PROCESSES_NOW=" + $running.Count + " PLAYWRIGHT_BROWSER_PROCESSES_NOW=" + $browsersNow)
    if ($running.Count -gt 0) { [void]$attention.Add($running.Count.ToString() + ' collector or cycle process(es) are running on this machine now') }
    foreach ($k in @('HKCU:\Software\Microsoft\Windows\CurrentVersion\Run', 'HKLM:\Software\Microsoft\Windows\CurrentVersion\Run')) {
      try { $p = Get-ItemProperty -LiteralPath $k -ErrorAction Stop; foreach ($q in $p.PSObject.Properties) { if (($q.Name -notmatch '^PS(Path|ParentPath|ChildName|Drive|Provider)$') -and ([string]$q.Value -match $relevantRx)) { Write-Output ("LOGON_ENTRY " + $k + " " + $q.Name + "=" + (Hide-Secret ([string]$q.Value))) } } } catch { }
    }
    foreach ($f in @([Environment]::GetFolderPath('Startup'), [Environment]::GetFolderPath('CommonStartup'))) {
      if ($f -and (Test-Path -LiteralPath $f)) { Write-Output ("STARTUP_FOLDER " + $f + " files=" + (@(Get-ChildItem -LiteralPath $f -File -Force | ForEach-Object { $_.Name }) -join ', ')) }
    }
  } catch { [void]$failed.Add('4 tasks and processes: ' + $_.Exception.Message + ' [block line ' + $_.InvocationInfo.ScriptLineNumber + ']'); Write-Output ('SECTION_FAILED ' + $failed[$failed.Count - 1]) }

  Write-Output '== 5. Each checkout: git, what it supports, settings, session, lock, queue, logs'
  $cands = @()
  $n = 0
  foreach ($c in $checkouts) {
    $n++
    try {
      $dc = Join-Path $c 'drone_collector'
      Write-Output ("CHECKOUT " + $n + " " + $c + $(if (Test-Path -LiteralPath (Join-Path $c 'app.py')) { ' (whole Vehicle Soft repository)' } else { ' (collector only)' }))
      $head = 'none'
      $code = 'not a git checkout'
      if ((Test-Path -LiteralPath (Join-Path $c '.git')) -and (-not $git)) { Write-Output '  GIT=git is not installed on this machine: the checkout is not read' }
      elseif (Test-Path -LiteralPath (Join-Path $c '.git')) {
        $g = Invoke-Git $c @('rev-parse', 'HEAD')
        if ($g.Code -ne 0) { Write-Output ("  GIT=error " + ($g.Lines -join ' ')) } else {
          $head = $g.Lines[0]
          $br = Invoke-Git $c @('symbolic-ref', '--short', '-q', 'HEAD')
          $tags = Invoke-Git $c @('tag', '--points-at', 'HEAD')
          $st = Invoke-Git $c @('--no-optional-locks', 'status', '--porcelain', '--untracked-files=no')
          $org = Invoke-Git $c @('remote', 'get-url', 'origin')
          if ((Invoke-Git $c @('cat-file', '-e', ($pin + '^{commit}'))).Code -eq 0) {
            $df = Invoke-Git $c @('diff', '--quiet', '--no-ext-diff', $pin, 'HEAD', '--', 'drone_collector')
            $code = if ($df.Code -eq 0) { 'same as the pilot pin' } elseif ($df.Code -eq 1) { 'differs from the pilot pin' } else { 'not compared' }
          } else { $code = 'pilot pin not in this clone (nothing was fetched)' }
          Write-Output ("  GIT HEAD=" + $head + " branch=" + $(if (($br.Code -eq 0) -and $br.Lines.Count) { $br.Lines[0] } else { 'detached' }) + " tags=" + $(if (($tags.Code -eq 0) -and $tags.Lines.Count) { $tags.Lines -join ',' } else { 'none' }) + " tracked_changes=" + $(if ($st.Code -eq 0) { $st.Lines.Count } else { 'not readable' }) + " origin=" + $(if (($org.Code -eq 0) -and $org.Lines.Count) { $org.Lines[0] -replace '://[^/@\s]+@', '://[hidden]@' } else { 'none' }))
          Write-Output ("  COLLECTOR_CODE=" + $code)
          if (($st.Code -eq 0) -and $st.Lines.Count) { Write-Output ("  TRACKED_CHANGES " + (@($st.Lines | Select-Object -First 5) -join ' ; ')) }
        }
      } else { Write-Output ("  GIT=not a git checkout; main.py sha256=" + (Get-FileFacts (Join-Path $dc 'main.py')).Sha) }
      $mainText = Read-Text (Join-Path $dc 'main.py')
      $cfgText = Read-Text (Join-Path $dc 'config.py')
      $sup = [ordered]@{ sources = $mainText.Contains("'--sources'"); ids_file = $mainText.Contains("'--ids-file'"); send_sources = $mainText.Contains("'--send-sources'"); lock = $mainText.Contains('DJI_COLLECTOR_LOCK_PATH'); outbox_setting = $cfgText.Contains('DRONE_OUTBOX_DIR') }
      Write-Output ("  SUPPORTS " + (@($sup.Keys | ForEach-Object { $_ + '=' + $(if ($sup[$_]) { 'yes' } else { 'no' }) }) -join ' ') + " logs=" + $(if ($cfgText.Contains("PACKAGE_ROOT / 'logs'")) { 'fixed to this checkout' } else { 'unknown' }))
      if (-not $sup['lock']) { Write-Output '  LOCK_NOTE=this code predates the collector lock: its runs take no lock at all' }
      $dot = @{}
      $envFile = Join-Path $dc '.env'
      if (Test-Path -LiteralPath $envFile -PathType Leaf) { $dot = Read-DotEnv $envFile; Write-Output ("  DOTENV " + $envFile + " names=" + (@($dot.Keys | Sort-Object) -join ',')) } else { Write-Output ("  DOTENV " + $envFile + " absent") }
      $eff = @{}
      foreach ($name in @('VEHICLE_SOFT_BASE_URL', 'DRONE_API_TOKEN', 'DJI_STORAGE_STATE', 'DJI_COLLECTOR_LOCK_PATH', 'DRONE_OUTBOX_DIR', 'DJI_HEADLESS')) {
        $eff[$name] = Get-Effective $name $dot
        Write-Output ("  EFFECTIVE " + $name + "=" + (Show-Value $name $eff[$name].Value) + " (from " + $eff[$name].Source + ")")
      }
      $url = [string]$eff['VEHICLE_SOFT_BASE_URL'].Value
      $receiver = if (-not $url) { 'none' } elseif ($url -match ':5051(/|$)') { 'staging :5051' } elseif ($url -match ':5050(/|$)') { 'PRODUCTION :5050' } else { 'other ' + (Show-Url $url) }
      Write-Output ("  RECEIVER=" + $receiver)
      if ($receiver -like 'PRODUCTION*') { [void]$attention.Add('checkout ' + $c + ' sends to production :5050') }
      $sessionPath = Get-Under $dc ([string]$eff['DJI_STORAGE_STATE'].Value) @('data', 'storage_state.json')
      $sf = Get-FileFacts $sessionPath
      Write-Output ("  SESSION " + $sessionPath + $(if ($sf.Exists) { " bytes=" + $sf.Bytes + " changed=" + $sf.Changed + " sha256=" + $sf.Sha } else { ' absent' }))
      if ($sf.Exists -and (@($sessionFiles | Where-Object { $_ -eq $sessionPath }).Count -eq 0)) { $sessionFiles += $sessionPath }
      $lockPath = Get-Under $dc ([string]$eff['DJI_COLLECTOR_LOCK_PATH'].Value) @('data', 'collector.lock')
      $lockLine = Get-LockLine $lockPath
      Write-Output ("  LOCK " + $lockLine)
      if ($lockLine -match 'owner_hint=present') { [void]$attention.Add('the collector lock of checkout ' + $c + ' has an owner hint: a run may hold it now') }
      $outbox = Get-Under $dc ([string]$eff['DRONE_OUTBOX_DIR'].Value) @('data', 'outbox')
      Write-Output ("  OUTBOX " + $outbox + " " + (Get-OutboxLine $outbox))
      foreach ($sub in @('logs', 'out')) { Write-Output ("  " + $sub.ToUpper() + " " + (Join-Path $dc $sub) + " " + (Get-DirLine (Join-Path $dc $sub))) }
      $data = Join-Path $dc 'data'
      if (Test-Path -LiteralPath $data -PathType Container) { Write-Output ("  DATA " + $data + " entries=" + (@(Get-ChildItem -LiteralPath $data -Force | ForEach-Object { $_.Name } | Sort-Object) -join ',')) } else { Write-Output ("  DATA " + $data + " absent") }
      $cands += [pscustomobject]@{ N = $n; Path = $c; Head = $head; Code = $code; Sup = $sup; Receiver = $receiver; Session = $sessionPath; SessionOk = ($sf.Exists -and ($sf.Bytes -gt 0)); Lock = $lockPath; Outbox = $outbox }
    } catch { [void]$failed.Add('5 checkout ' + $c + ': ' + $_.Exception.Message + ' [block line ' + $_.InvocationInfo.ScriptLineNumber + ']'); Write-Output ('SECTION_FAILED ' + $failed[$failed.Count - 1]) }
  }

  Write-Output '== 6. Python and browsers the collector can use'
  try {
    foreach ($v in $venvs) {
      $cfg = @{}
      foreach ($line in [System.IO.File]::ReadAllLines((Join-Path $v 'pyvenv.cfg'))) { $m = [regex]::Match($line, '^\s*([A-Za-z_-]+)\s*=\s*(.*?)\s*$'); if ($m.Success) { $cfg[$m.Groups[1].Value] = $m.Groups[2].Value } }
      $site = [System.IO.Path]::Combine($v, 'Lib', 'site-packages')
      $pk = @()
      foreach ($pkg in @('playwright', 'python_dotenv', 'requests')) {
        $dist = @(if (Test-Path -LiteralPath $site) { Get-ChildItem -LiteralPath $site -Directory -Filter ($pkg + '-*.dist-info') -ErrorAction SilentlyContinue })
        $pk += ($pkg + '=' + $(if ($dist.Count) { $dist[0].Name.Substring($pkg.Length + 1) -replace '\.dist-info$', '' } else { 'absent' }))
      }
      $exe = [System.IO.Path]::Combine($v, 'Scripts', 'python.exe')
      Write-Output ("VENV " + $v + " version=" + $(if ($cfg['version']) { $cfg['version'] } else { $cfg['version_info'] }) + " home=" + $cfg['home'] + " python=" + $exe + " python_present=" + (Test-Path -LiteralPath $exe -PathType Leaf) + " " + ($pk -join ' '))
    }
    if ($venvs.Count -eq 0) { Write-Output 'VENVS=none found' }
    foreach ($r in @($pyRefs | Select-Object -Unique)) { $src, $exe = $r -split '\|', 2; Write-Output ("COLLECTOR_PYTHON " + $src + " exe=" + $exe + " version=" + (Get-PyVersion $exe)) }
    if ($pyRefs.Count -eq 0) { Write-Output 'COLLECTOR_PYTHON=not named by any task or running process; the venvs above are the candidates' }
    foreach ($cmd in @(Get-Command -Name 'python.exe', 'py.exe' -All -ErrorAction SilentlyContinue)) { Write-Output ("PYTHON_ON_PATH " + $cmd.Source + " version=" + (Get-PyVersion $cmd.Source)) }
    $bdirs = @($browserDirs)
    if ($env:LOCALAPPDATA) { $bdirs += (Join-Path $env:LOCALAPPDATA 'ms-playwright') }
    foreach ($scope in @($machineEnv, $userEnv)) { if ($scope.ContainsKey('PLAYWRIGHT_BROWSERS_PATH')) { $bdirs += $scope['PLAYWRIGHT_BROWSERS_PATH'] } }
    foreach ($b in @($bdirs | Where-Object { $_ } | Select-Object -Unique)) {
      if (Test-Path -LiteralPath $b -PathType Container) { Write-Output ("BROWSERS " + $b + " " + (@(Get-ChildItem -LiteralPath $b -Directory | ForEach-Object { $_.Name } | Sort-Object) -join ',')) }
    }
  } catch { [void]$failed.Add('6 python: ' + $_.Exception.Message + ' [block line ' + $_.InvocationInfo.ScriptLineNumber + ']'); Write-Output ('SECTION_FAILED ' + $failed[$failed.Count - 1]) }

  Write-Output '== 7. Shared with the production collector on SRV-YOQSH? Session, lock, collection windows'
  try {
    $prodFiles = @{}
    $prodOwner = ''
    $remoteNote = ''
    $prodTasks = @()
    if ($onServer) { $remoteNote = 'this is SRV-YOQSH: the production files and tasks are the local ones above' }
    elseif ($remote) {
      if (-not (Wait-Job -Job $remote -Timeout $remoteWait)) { $remoteNote = 'SRV-YOQSH did not answer within ' + $remoteWait + ' s'; Stop-Job -Job $remote }
      foreach ($line in @(Receive-Job -Job $remote -ErrorAction SilentlyContinue)) {
        $kind, $rest = ([string]$line) -split '\|', 2
        if ($kind -eq 'FILE') { $f = $rest -split '\|'; $prodFiles[$f[0]] = $f }
        elseif ($kind -eq 'OWNER') { $prodOwner = $rest }
        elseif ($kind -eq 'TASK') { $prodTasks += $rest }
        elseif ($kind -eq 'SHARE') { $remoteNote = 'the production data folder is not readable from here: ' + $rest }
        elseif (($kind -eq 'SCHED') -and ($rest -ne 'ok')) { Write-Output ("PROD_SCHEDULE=not readable from this machine: " + $rest) }
        elseif ($kind -eq 'SCHED') { Write-Output ("PROD_SCHEDULE=read from " + $server + ": " + $prodTasks.Count + " collector-related task(s)") }
      }
      Remove-Job -Job $remote -Force
    } else { $remoteNote = 'the read of SRV-YOQSH was not started' }
    foreach ($k in @('storage_state.json', 'collector.lock', 'collector.lock.owner')) {
      if ($prodFiles.ContainsKey($k)) { Write-Output ("PROD_FILE " + $k + " " + $(if ($prodFiles[$k][1] -eq 'absent') { 'absent' } else { 'bytes=' + $prodFiles[$k][1] + ' changed_utc=' + $prodFiles[$k][2] })) }
    }
    if ($remoteNote) { Write-Output ("PROD_FILES=" + $remoteNote) }
    if ($prodFiles.ContainsKey('collector.lock.owner')) {
      if ($prodFiles['collector.lock.owner'][1] -ne 'absent') { Write-Output ("PROD_LOCK=held now (owner hint " + $prodOwner + ")"); [void]$attention.Add('the production collector lock on SRV-YOQSH has an owner hint: production is collecting now') } else { Write-Output 'PROD_LOCK=no owner hint: the production collector is not running now' }
    }
    $prodSession = [System.IO.Path]::Combine($serverData, 'storage_state.json')
    foreach ($s in @($sessionFiles | Select-Object -Unique)) {
      $f = Get-FileFacts $s
      $users = @($cands | Where-Object { $_.Session -eq $s } | ForEach-Object { $_.N })
      $twins = @($sessionFiles | Where-Object { ($_ -ne $s) -and ((Get-FileFacts $_).Sha -eq $f.Sha) })
      $verdict = if ($onServer -and ($s -eq $prodSession)) { 'SAME_FILE: this is the production collector session' }
        elseif ($s -match '^\\\\(srv-yoqsh|10\.103\.25\.14)\\') { 'SAME_FILE: the production session over the network' }
        elseif ($prodFiles.ContainsKey('storage_state.json') -and ($prodFiles['storage_state.json'][1] -ne 'absent')) { if (([string]$f.Bytes -eq $prodFiles['storage_state.json'][1]) -and ($f.ChangedUtc -eq $prodFiles['storage_state.json'][2])) { 'LIKELY_COPY of the production session: same size and change time (contents were not compared)' } else { 'NOT_THE_SAME: size or change time differ from the production session -- an own session or an older copy' } }
        else { 'OWN_FILE_ON_THIS_MACHINE: the production session could not be compared from here' }
      Write-Output ("SESSION_FILE " + $s + " bytes=" + $f.Bytes + " changed=" + $f.Changed + " sha256=" + $f.Sha + " used_by_checkout=" + $(if ($users.Count) { $users -join ',' } else { 'none' }) + " same_bytes_as=" + $(if ($twins.Count) { $twins -join ',' } else { 'none' }))
      Write-Output ("  SESSION_VS_PRODUCTION=" + $verdict)
    }
    if ($sessionFiles.Count -eq 0) { Write-Output 'SESSION_FILES=none on this machine' }
    $prodLock = [System.IO.Path]::Combine($serverData, 'collector.lock')
    foreach ($c in $cands) {
      $same = $onServer -and ($c.Lock -eq $prodLock)
      Write-Output ("LOCK_VS_PRODUCTION checkout=" + $c.N + " " + $c.Lock + " -> " + $(if ($same) { 'SAME_LOCK as the production collector' } elseif ($c.Lock -match '^\\\\') { 'a network path: check by hand' } else { 'a lock on ' + $hostName + ' only: it does not stop a run on SRV-YOQSH, and the production lock does not stop runs here' }))
    }
    foreach ($t in $prodTasks) { Write-Output ("PROD_TASK " + $t) }
    if ((-not $onServer) -and ($prodTasks.Count -eq 0)) { Write-Output 'PROD_SCHEDULE_DOCUMENTED=DroneCollectorDaily daily about 06:00 (docs/DJI_DAILY_EVIDENCE_RUN.md; not read live)' }
  } catch { [void]$failed.Add('7 production: ' + $_.Exception.Message + ' [block line ' + $_.InvocationInfo.ScriptLineNumber + ']'); Write-Output ('SECTION_FAILED ' + $failed[$failed.Count - 1]) }

  Write-Output '== 8. Network: GET of the login pages only'
  foreach ($u in @(@('STAGING_5051_LOGIN', $stagingLogin), @('PRODUCTION_5050_LOGIN', $prodLogin))) {
    try {
      $r = Invoke-WebRequest -Uri $u[1] -Method Get -UseBasicParsing -TimeoutSec 20 -MaximumRedirection 0
      $form = [string]$r.Content -match 'vs-login-form'
      Write-Output ($u[0] + "=" + [int]$r.StatusCode + $(if ($form) { ' login form' } else { ' no login form' }) + " (GET only, connectivity)")
      if (($u[0] -like 'STAGING*') -and (([int]$r.StatusCode -ne 200) -or (-not $form))) { [void]$attention.Add('the staging login page did not answer 200 with the login form') }
    } catch {
      Write-Output ($u[0] + "=ERROR " + $_.Exception.Message)
      if ($u[0] -like 'STAGING*') { [void]$attention.Add('the staging site does not answer from this machine: W1 could not send there') }
    }
  }

  Write-Output '== 9. For W1: candidates and a separate pilot queue, log and lock (nothing is run or created)'
  try {
    foreach ($c in $cands) { Write-Output ("CANDIDATE " + $c.N + " " + $c.Path + " head=" + $c.Head + " code=" + $c.Code + " sources=" + $c.Sup['sources'] + " ids_file=" + $c.Sup['ids_file'] + " send_sources=" + $c.Sup['send_sources'] + " lock=" + $c.Sup['lock'] + " outbox_setting=" + $c.Sup['outbox_setting'] + " receiver=" + $c.Receiver + " session_present=" + $c.SessionOk) }
    Write-Output ("PILOT_FOLDER_PROPOSED=" + $pilotDir + " exists=" + (Test-Path -LiteralPath $pilotDir))
    $inside = @(@($cands | ForEach-Object { $_.Path; $_.Outbox; (Split-Path -Parent $_.Session) }) + @($sessionFiles) | Where-Object { $_ -and ((($_ + '\').StartsWith($pilotDir + '\', [StringComparison]::OrdinalIgnoreCase)) -or (($pilotDir + '\').StartsWith(($_.TrimEnd('\') + '\'), [StringComparison]::OrdinalIgnoreCase))) } | Select-Object -Unique)
    Write-Output ("PILOT_FOLDER_OVERLAP=" + $(if ($inside.Count) { $inside -join ', ' } else { 'none: no checkout, queue or session is inside it, and it is inside none of them' }))
    $outboxes = @($cands | ForEach-Object { $_.Outbox } | Select-Object -Unique)
    Write-Output ("EXISTING_QUEUES=" + $outboxes.Count + $(if ($outboxes.Count) { ': ' + ($outboxes -join ', ') } else { '' }))
    $locks = @($cands | ForEach-Object { $_.Lock } | Select-Object -Unique)
    Write-Output ("EXISTING_LOCKS=" + $locks.Count + $(if ($locks.Count) { ': ' + ($locks -join ', ') } else { '' }))
  } catch { [void]$failed.Add('9 candidates: ' + $_.Exception.Message + ' [block line ' + $_.InvocationInfo.ScriptLineNumber + ']'); Write-Output ('SECTION_FAILED ' + $failed[$failed.Count - 1]) }

  Write-Output '== 10. Summary'
  if ($checkouts.Count -eq 0) { [void]$failed.Add('no drone_collector checkout was found on this machine' + $(if ($cut) { ' (the scan stopped at its time limit)' } else { '' })) }
  foreach ($a in $attention) { Write-Output ("ATTENTION " + $a) }
  Write-Output ("W0_ATTENTION=" + $attention.Count)
  Write-Output ("LOG FILE: " + $log)
  if ($failed.Count -gt 0) {
    Write-Output ("STEP=STOP - " + ($failed -join ' | ') + " (read only: nothing operational was changed)")
  } else {
    Write-Output 'STEP=PASS (read only: nothing operational was changed)'
  }
  try { Stop-Transcript | Out-Null } catch { }
}
```

Прислать весь вывод. После него — разбор и план W1; сбора до этого нет.

### W1 — SRV-YOQSH: канарейка 50 и S1 одним блоком

**Выполнен на SRV-YOQSH 08.10.2026: `STEP=PASS`, `DECISION=GO_TO_500`.**
Прогон `C:\VehicleSoft_CardPilot\w1\20261008_151912`, журнал блока
`C:\VehicleSoft_CardPilot\card_pilot_w1_20261008_151912.log`, run id
`sources:ids-file:20261008T101922Z`. Цифры — в §8.

Владелец выполнил не блок коммита `3cfc451`, а свою исправленную копию
`C:\VehicleSoft_CardPilot\W1_S1_canary50_staging_token.ps1` (sha256
`05dceafa078b29e988687021ff953949c2cd15a022f693f58545b6fc7a596f5c`).
Исправлений два:

1. HEAD production на 08.10.2026 — `3434996`, а не `8df5683`. Между ними
   24 коммита, и ни один не меняет `drone_collector`, `dji_area` и
   `drones.py`.
2. Токен. Машинный `DRONE_API_TOKEN` принадлежит production, и площадка
   ответила на него 401. Площадка приняла токен из окружения своей службы
   `TransportReportStaging`.

Блок ниже содержит оба исправления как проверки, и тесты их покрывают
(`W1InPowerShell`, `Text.test_w1_*`). Но выполнялся не он, а файл
владельца, исправленный вручную; CI этот файл не проходил. Повторно W1 не
запускается: блок откажет, увидев прогон в `w1`.

**Решение владельца по выводу W0 (08.10.2026).** W0 выполнен на SRV-YOQSH:
сборщик production работает здесь, отдельной рабочей машины нет. Поэтому
W1 идёт на SRV-YOQSH, но не из production-чекаута:

* код — отдельный чекаут пилота `C:\VehicleSoft_CardPilot\src` на пине
  `39eab50`;
* python — venv сборщика production
  (`C:\transport-report\drone_collector\.venv\Scripts\python.exe`);
* сессия DJI — файл production, только на чтение;
* замок — общий с production;
* очередь и журнал — свои.

Это заменяет «рабочую машину» в §5 и в строках W1/W2 таблицы выше.
Канарейка и проверка площадки (S1) идут одним блоком. После сбора без
отдельного шага, но только если ворота сборщика прошли; любые ворота
закрываются в сторону остановки.

**Что блок проверяет до DJI** (ничего не меняя и ничего не посылая):

* `canary_ids.txt` B0 — ровно 50 разных номеров, sha256 `5913a88d…`, равен
  `plan.json`; манифест — замороженный;
* чекаут пилота — на пине, без правок, знает `--sources`, `--ids-file`,
  `--send-sources`, замок и `DRONE_OUTBOX_DIR`, своего `.env` нет;
* python venv production в окружении запуска импортирует `drone_collector`
  из `C:\VehicleSoft_CardPilot\src` (`PACKAGE_ROOT` и файлы модулей) и видит
  Playwright;
* приёмник — `http://10.103.25.14:5051`; порт 5050 отвергается явно;
* `DRONE_API_TOKEN` берётся из окружения службы площадки
  (`AppEnvironmentExtra` в параметрах `TransportReportStaging`). Строка
  должна быть ровно одна. Площадка должна его принять: POST
  `/drones/api/land_geometry_manifest` с пустым списком. Этот вызов
  только читает и ничего не пишет; значение токена не печатается;
* production: HEAD `3434996`, три службы `Running`. Код DJI production
  (`drone_collector`, `dji_area`, `drones.py`) совпадает с `8df5683`: нет
  ни коммита, ни правки в рабочей копии. Другой HEAD — остановка до DJI:
  нужна новая проверка совместимости;
* `DroneCollectorDaily`, `DroneAreaDaily`, `DjiAreaRefresh` не идут, до
  следующего запуска по расписанию не меньше 130 минут;
* процессов сборщика или цикла нет;
* подсказки владельца замка production нет, или процесс из неё мёртв;
* площадка как после B1:
  * HEAD — пин, без правок;
  * `TransportReportStaging` — `Running`, оба бота — `Stopped Disabled`;
  * `DjiAreaRefreshStaging` — `Disabled`, `DJI_REFRESH_LAUNCHER` нет;
  * `/login` — 200;
  * открытый прогон B1 с `swapped.txt`;
  * отпечаток базы равен `fingerprint_before.json` B0;
  * миграций 60;
  * каталог полей (`dji_land_*`) равен копии B0;
* включённых задач, которые пишут в площадку или собирают, нет; известная
  резервная копия площадки не в счёт.

Непосредственно перед запуском production и окно проверяются ещё раз.

**Сбор.** Только штатная команда:

```
python -m drone_collector.main --sources --ids-file <копия canary_ids.txt> --send-sources
```

Запуск — из `C:\VehicleSoft_CardPilot\src`, дочерним процессом. Окружение
пилота получает только он; консоль, пользователь и машина не меняются:

| переменная | значение |
|---|---|
| `VEHICLE_SOFT_BASE_URL` | `http://10.103.25.14:5051` |
| `DJI_STORAGE_STATE` | файл сессии production |
| `DJI_COLLECTOR_LOCK_PATH` | замок production |
| `DJI_COLLECTOR_LOCK_WAIT_S` | `0` |
| `DRONE_OUTBOX_DIR` | `C:\VehicleSoft_CardPilot\w1\<время>\outbox` |
| `DJI_HEADLESS` | `true` |
| `DRONE_API_TOKEN` | из окружения службы площадки (значение не печатается) |

Из окружения убраны `PYTHONPATH`, `PYTHONHOME`, `PYTHONSAFEPATH` и
`PYTHONSTARTUP`. `--save-session` не передаётся.

Почему сессия production не под угрозой. Код пина записывает
`storage_state.json` в одном месте: `save_state_atomically`, достижимая
только из `--save-session`. В `--sources` файл только читается:
`require_session` и `new_context(storage_state=…)`. Блок сверяет sha256 и
время файла до и после.

Замок с ожиданием 0. Если сбор production держит замок, сборщик сразу
выходит с кодом 24, и блок останавливается: не ждёт и никого не убивает.

**Надзор во время сбора.** Сам сборщик 403, 429 и капчу не распознаёт: под
защитой DJI он продолжил бы обход по 70 с на вылет. Более того, отказ на
запрос карточки, дескриптора или V4 (не-2xx или код ошибки в теле) код пина
не пишет в журнал строкой. Он только прибавляет счётчик `sources_rejected`
в итоговой строке. Поэтому блок читает журнал сборщика построчно и
останавливает свой прогон, если:

* строка конфигурации сборщика показывает не приёмник `:5051`, не свою
  очередь, не сессию production или не `headless`;
* в журнале признак отказа. Пин пишет такие строки при истёкшей сессии, при
  прямом запросе дескриптора (`answered HTTP 403/429`) и при неработающем
  браузере (три страницы подряд не открылись). Капча и «не робот» — на
  случай, если такая строка появится. Число байт вида `(429 bytes)` —
  не ответ DJI и маскируется;
* второй вылет, у которого маршрут, дескриптор или V4 пришли, а карточка
  нет. Это единственный след отказа на запрос карточки во время обхода;
* три страницы записи подряд не открылись;
* пять вылетов подряд пришли без карточки (по любой причине). Пять, а не
  три: до 10 из 50 без карточки — ещё результат (правило решения), а вылеты
  идут по порядку номеров, соседние похожи. При 20 % случайных пропусков
  ложная остановка случится в 1,2 % прогонов (при трёх подряд — в 27 %);
* прогон длится больше 100 минут.

Остановка — `taskkill /T` только дерева этого процесса. Замок отпускает ОС;
подсказка владельца, если останется, мертва и безвредна.

Окно PowerShell не закрывать до строки `STEP=`: надзор живёт в нём. Блок
печатает `COLLECTOR_PID=…` и команду, которой остановить сбор, если окно
всё-таки закрыли.

**Повторной канарейки нет.** Сборщик шлёт данные только после всего
обхода, и у каждого запуска W1 своя очередь. Поэтому прогон, остановленный
DJI, оставляет площадку как была, и повторная вставка блока снова посетила
бы все 50. Блок отказывается, если в `C:\VehicleSoft_CardPilot\w1` есть
прогон, который запускал сборщик. Исключение — выход 24: замок был занят,
ничего не собрано. Новый сбор — только по новому решению владельца.

**Ворота сборщика** — без них пересчёта нет:

* код выхода 0 или 18 (часть вылетов неполная — допустимо); 24 — замок
  взял сбор production, ничего не собрано, блок можно вставить снова после
  него;
* `RUN SUMMARY`: запрошено и посещено 50, площадка приняла всё, ошибок
  приёма 0;
* `sources_rejected` не больше `sources_v4_failed`. Отказанная загрузка V4
  оставляет свой статус V4_FAILED и измеряется. Любой другой отказ —
  карточка, дескриптор, маршрут — признак защиты, пересчёта нет. В живых
  прогонах площадки 20 и 24.09.2026 было `sources_rejected=0`;
* `collector-stats` по журналу этого прогона: код 0, посещено 50. Число
  байт в строках замаскировано так же, как при надзоре;
* сессия production не изменилась;
* прогон записан в журнал чекаута пилота и не записан в журнал production;
* база production (`mode=ro`, только счётчики): ни одной ревизии с
  `capture_run_id` этого прогона, ни одной ревизии или улики канареечных
  вылетов с момента запуска;
* площадка: все новые ревизии — этого прогона и только канареечных
  вылетов, по одной на тип, их число равно `sources_new`; улики других
  вылетов с момента запуска не менялись.

**S1** — только при пройденных воротах:

1. Служба площадки — стоп, `check_db_lock`, каталог полей ещё раз равен B0.
2. `dji_area_recalc.py --dry-run`, затем `--apply`: `--from 2026-09-01
   --to 2026-09-30` и ровно 50 `--flight-id`. Требуется: в периоде 50
   вылетов, строк расчёта и привязки по 50.
3. `fingerprint` после.
4. `measure --stage canary --before <отпечаток до канарейки>` — все ворота
   неизменности: RAW, решения, прежние ревизии, миграции, вылеты вне
   канарейки.
5. Перепись сентября.
6. Пуск службы, миграций 60, площадка и production — как до блока.

Пересчёт дописывает кэш расшифрованных V4 (`dji_v4_summaries`) и
соседним загруженным вылетам. Это не улики и не привязки; число таких
строк печатается.

**Правило решения.** Владелец его не задавал; это моё предложение, по
выводу владелец решает сам:

* `DECISION=STOP` — любые ворота не прошли, или карточек меньше 40 из 50:
  исторические карточки этим путём надёжно не получить;
* `DECISION=SIMPLIFY` — всё прошло, но верхняя граница 95 % интервала доли
  подтверждённых среди получивших карточку ниже 20 %. 20 % — нижний край
  гипотезы §3. Дособор карточек покрытие почти не сдвинет; следующий шаг —
  данные полей (§12);
* `DECISION=GO_TO_500` — иначе. Это лишь значит, что владелец может
  разрешить W2; блок не запускает ничего сам, `pilot_ids.txt` не трогает.

**Что пишет.**

* `C:\VehicleSoft_CardPilot\w1\<время>\` — копия номеров, своя очередь,
  журнал сборщика, сводка `collector-stats`, отпечатки, `recalc_*.json`,
  `measure\`, `census_after.json`, вспомогательный `w1_check.py`
  (только чтение);
* журнал блока в `C:\VehicleSoft_CardPilot`;
* база площадки — через штатный приём и пересчёт. Её вернёт R вместе со
  всей площадкой;
* в папке данных production — только `collector.lock` и
  `collector.lock.owner` по протоколу общего замка, как у любого сбора.

Production в остальном только читается.

**Вывод.** Последние строки — `DECISION=…`, `DECISION_REASON=…`, `RUN=…`,
`STEP=PASS` или `STEP=STOP - …`. Выше:

* до DJI — `CANARY_COUNT=50`, `CANARY_SHA256=…`, `COLLECTOR_CODE`,
  `PYTHON`, `IMPORTS`, `RECEIVER`, `SESSION`, `LOCK`, `PILOT_OUTBOX`,
  `PILOT_LOG`, `PROD_TASK_*` со временем следующих запусков;
* сбор — журнал построчно (`  | …`), `COLLECTOR_EXIT`, `RUN_SUMMARY`,
  `COLLECTOR_GATE=PASS`;
* S1 — `RECALC_DRY-RUN`, `RECALC_APPLY`, строки `measure` и `GATE …`,
  `CENSUS`;
* итог — `ATTEMPTED`, `VISITED`, `CARDS_CAPTURED`, `FETCH_SUCCESS`,
  `WALL_SECONDS`, `MEDIAN_SECONDS_PER_FLIGHT`, `OUTCOME EXACT/IDENTIFIED/
  CONFIRMED/NO_KEY/NOT_IN_CATALOG/NO_CARD/OTHER_UNRESOLVED/NO_CALC`,
  `CASES`, `CONFIRMED_RATE` с интервалом Уилсона, `PROJECTION (not a
  fact)`, `BY_UNIT`, `BY_WEEK`.

`STEP=PASS` значит: все ворота пройдены. Решение при этом может быть любым
из трёх.

```powershell
& {
  $ErrorActionPreference = 'Stop'
  $ProgressPreference = 'SilentlyContinue'
  $expectedHost = 'srv-yoqsh'
  $src          = 'C:\VehicleSoft_CardPilot\src'
  $cpy          = 'C:\transport-report\drone_collector\.venv\Scripts\python.exe'
  $python       = 'C:\Program Files\Python314\python.exe'
  $root         = 'C:\transport-report-staging'
  $db           = 'C:\transport-report-staging\instance\transport.db'
  $service      = 'TransportReportStaging'
  $bots         = @('TransportBotStaging', 'TransportBot003Staging')
  $site         = 'http://10.103.25.14:5051'
  $prodRoot     = 'C:\transport-report'
  $prodDb       = 'C:\transport-report\instance\transport.db'
  $prodExpected = '3434996a434652b0b590be2cfe08c4dc54cf1fab'
  $prodBase     = '8df568394a840054ef6f842c6a8b272ca4c31aa8'
  $prodDji      = @('drone_collector', 'dji_area', 'drones.py')
  $prodNames    = @('TransportBot', 'TransportBot003', 'TransportReport')
  $prodTasks    = @('DroneCollectorDaily', 'DroneAreaDaily', 'DjiAreaRefresh')
  $session      = 'C:\transport-report\drone_collector\data\storage_state.json'
  $lock         = 'C:\transport-report\drone_collector\data\collector.lock'
  $prodLog      = 'C:\transport-report\drone_collector\logs\collector.log'
  $pin          = '39eab503069b7bb01a8342542edcbebbfc2210c2'
  $runRoot      = 'D:\transport-report-backups\staging\card_pilot'
  $baseline     = 'D:\transport-report-backups\staging\card_pilot\baseline_20261003_072956'
  $snapshot     = 'D:\transport-report-backups\staging\card_pilot\baseline_20261003_072956\snapshot\transport_20261003_073000_card_pilot_baseline.db'
  $canarySha    = '5913a88d1bfcecdfe0586fd0a81007ef7cc771d777a2754ebd8b5c1ec2e641da'
  $isoTask      = 'DjiAreaRefreshStaging'
  $work         = 'C:\VehicleSoft_CardPilot'
  $w1Root       = 'C:\VehicleSoft_CardPilot\w1'
  $svcKey       = 'HKLM:\SYSTEM\CurrentControlSet\Services'
  $maxCollectMin = 100
  $minGapMin    = 130
  $minFetched   = 40
  $maxNoCardRun = 5
  $maxCardRefused = 2
  $simplifyBelow = 0.20
  $stamp        = Get-Date -Format 'yyyyMMdd_HHmmss'
  $planDir      = Join-Path $baseline 'plan'
  $w1           = Join-Path $w1Root $stamp
  $outbox       = Join-Path $w1 'outbox'
  $idsCopy      = Join-Path $w1 'canary_ids.txt'
  $collectLog   = Join-Path $w1 'collector_stdout.log'
  $siteParams   = $svcKey + '\' + $service + '\Parameters'
  $prodWant     = 'TransportBot=Running TransportBot003=Running TransportReport=Running'
  $collectorRx  = 'drone_collector|dji_area_daily|dji_area_recalc|dji_area_backfill'
  $stopMarkers  = @(@('SESSION', '(?i)no longer signed in|SessionExpired|session (is )?(missing|expired)'), @('HTTP_429', '(?i)\b429\b|too many requests|rate.?limit'), @('HTTP_403', '(?i)\bHTTP 403\b|\b403 Forbidden\b|forbidden'), @('CAPTCHA', '(?i)captcha|verify you are human|challenge'), @('BROWSER_DEAD', '(?i)browser is not usable'))
  $helperText = @'
import hashlib, os, sqlite3, sys
# W1 read-only checks (DRONE-CARD-COVERAGE-001). Opens the database mode=ro, prints KEY=VALUE.
#   snapshot DB                                    counters and the field catalog digest
#   canary DB IDS SRC_AFTER V4_AFTER SINCE RUNID   where the rows written after those ids / since SINCE belong
def con_ro(path):
    if not os.path.isfile(path):
        raise SystemExit('NOT FOUND: ' + path)
    uri = 'file:%s?mode=ro' % os.path.abspath(path).replace('\\', '/').replace('?', '%3f').replace('#', '%23')
    return sqlite3.connect(uri, uri=True, timeout=30)
def one(con, sql, args=()):
    return con.execute(sql, args).fetchone()[0]
def snapshot(con):
    out = {'SOURCE_MAX_ID': one(con, 'SELECT COALESCE(MAX(id), 0) FROM dji_source_revisions'),
           'SOURCE_ROWS': one(con, 'SELECT COUNT(*) FROM dji_source_revisions'),
           'EVIDENCE_ROWS': one(con, 'SELECT COUNT(*) FROM dji_flight_evidence'),
           'V4SUM_ROWS': one(con, 'SELECT COUNT(*) FROM dji_v4_summaries'),
           'V4SUM_MAX_ID': one(con, 'SELECT COALESCE(MAX(id), 0) FROM dji_v4_summaries')}
    h = hashlib.sha256()
    for table, cols in (('dji_land_snapshots', 'id, captured_at_utc, received_count'),
                        ('dji_land_revisions', 'id, land_uuid, geometry_md5'),
                        ('dji_land_geometries', 'id, content_md5, sha256, md5_verified')):
        for row in con.execute('SELECT %s FROM %s ORDER BY id' % (cols, table)):
            h.update((table + '|' + '|'.join(repr(v) for v in row) + '\n').encode('utf-8'))
    out['CATALOG_SHA256'] = h.hexdigest()
    return out
def canary(con, ids, after, v4_after, since, run_id):
    marks = ','.join('?' * len(ids))
    new = 'FROM dji_source_revisions WHERE id > ?'
    return {
        'V4SUM_NEW': one(con, 'SELECT COUNT(*) FROM dji_v4_summaries WHERE id > ?', (v4_after,)),
        'V4SUM_NEW_OUTSIDE_CANARY': one(con, 'SELECT COUNT(*) FROM dji_v4_summaries WHERE id > ? AND flight_id NOT IN (%s)' % marks, [v4_after] + ids),
        'NEW_REVISIONS': one(con, 'SELECT COUNT(*) ' + new, (after,)),
        'NEW_FLIGHTS': one(con, 'SELECT COUNT(DISTINCT flight_id) ' + new, (after,)),
        'NEW_OUTSIDE_CANARY': one(con, 'SELECT COUNT(*) %s AND (flight_id IS NULL OR flight_id NOT IN (%s))' % (new, marks), [after] + ids),
        'NEW_OTHER_RUN': one(con, 'SELECT COUNT(*) %s AND (capture_run_id IS NULL OR capture_run_id <> ?)' % new, (after, run_id)),
        'NEW_REPEATED_TYPE': one(con, 'SELECT COUNT(*) FROM (SELECT 1 %s GROUP BY flight_id, source_type HAVING COUNT(*) > 1)' % new, (after,)),
        'RUN_ID_ROWS': one(con, 'SELECT COUNT(*) FROM dji_source_revisions WHERE capture_run_id = ?', (run_id,)),
        'CANARY_SOURCES_SINCE': one(con, 'SELECT COUNT(*) FROM dji_source_revisions WHERE flight_id IN (%s) AND (received_at >= ? OR last_seen_at >= ?)' % marks, ids + [since, since]),
        'CANARY_EVIDENCE_SINCE': one(con, 'SELECT COUNT(*) FROM dji_flight_evidence WHERE flight_id IN (%s) AND updated_at >= ?' % marks, ids + [since]),
        'EVIDENCE_OUTSIDE_SINCE': one(con, 'SELECT COUNT(*) FROM dji_flight_evidence WHERE flight_id NOT IN (%s) AND updated_at >= ?' % marks, ids + [since]),
    }
def main(argv):
    con = con_ro(argv[1])
    try:
        if argv[0] == 'snapshot':
            res = snapshot(con)
        elif argv[0] == 'canary':
            with open(argv[2], encoding='utf-8-sig') as fh:
                ids = [int(l.split('#')[0]) for l in fh if l.split('#')[0].strip()]
            res = canary(con, ids, int(argv[3]), int(argv[4]), argv[5], argv[6])
        else:
            raise SystemExit('unknown mode')
    finally:
        con.close()
    for k in sorted(res):
        print('%s=%s' % (k, res[k]))
    return 0
if __name__ == '__main__':
    sys.exit(main(sys.argv[1:]))
'@
  function Get-ProdServices { (@($prodNames | ForEach-Object { $s = Get-Service -Name $_ -ErrorAction SilentlyContinue; if ($s) { $_ + '=' + $s.Status } else { $_ + '=missing' } }) -join ' ') }
  function Get-Sha([string]$p) { (Get-FileHash -LiteralPath $p -Algorithm SHA256).Hash.ToLower() }
  function Test-SameFile([string]$a, [string]$b) { (Get-Sha $a) -eq (Get-Sha $b) }
  function Get-FileState([string]$p) { $i = Get-Item -LiteralPath $p -Force; (Get-Sha $p) + ' bytes=' + $i.Length + ' changed_utc=' + $i.LastWriteTimeUtc.ToString('yyyy-MM-dd HH:mm:ss') }
  function Read-Ids([string]$p) { @(Get-Content -LiteralPath $p | ForEach-Object { ($_ -split '#')[0].Trim() } | Where-Object { $_ } | ForEach-Object { [int64]$_ }) }
  function Pct($x) { if ($null -eq $x) { return '-' } ([double]$x * 100).ToString('0.0', [System.Globalization.CultureInfo]::InvariantCulture) + '%' }
  function Read-Pairs([string[]]$lines) { $h = @{}; foreach ($l in $lines) { if ($l -match '^([A-Z0-9_]+)=(.*)$') { $h[$Matches[1]] = $Matches[2] } }; $h }
  function Invoke-Helper([string[]]$helperArgs) {
    $o = @(& $python -I (Join-Path $w1 'w1_check.py') @helperArgs)
    if ($LASTEXITCODE -ne 0) { throw "STEP FAILED: the read-only check $($helperArgs[0]) on $($helperArgs[1]) exit $LASTEXITCODE -- $($o -join ' ')" }
    Read-Pairs $o
  }
  function Start-Child([string]$exe, [string]$arguments, [string]$cwd) {
    $psi = New-Object System.Diagnostics.ProcessStartInfo
    $psi.FileName = $exe
    $psi.Arguments = $arguments
    $psi.WorkingDirectory = $cwd
    $psi.UseShellExecute = $false
    $psi.CreateNoWindow = $true
    $psi.RedirectStandardOutput = $true
    $psi.RedirectStandardError = $true
    $psi.StandardOutputEncoding = New-Object System.Text.UTF8Encoding($false)
    $psi.StandardErrorEncoding = New-Object System.Text.UTF8Encoding($false)
    # The child alone gets the pilot environment; this console and the machine keep theirs.
    foreach ($k in $childDrop) { if ($psi.EnvironmentVariables.ContainsKey($k)) { $psi.EnvironmentVariables.Remove($k) } }
    foreach ($k in $childEnv.Keys) { $psi.EnvironmentVariables[$k] = [string]$childEnv[$k] }
    [System.Diagnostics.Process]::Start($psi)
  }
  function Stop-Child($p) {
    if ($p.HasExited) { return }
    # Only this process and its children (browser, driver); no other process is touched.
    try { & taskkill.exe /PID $p.Id /T /F 2>&1 | Out-Null } catch { }
    if (-not $p.HasExited) { try { $p.Kill($true) } catch { try { $p.Kill() } catch { } } }
    [void]$p.WaitForExit(30000)
  }
  function Get-LockOwner {
    $h = $lock + '.owner'
    if (-not (Test-Path -LiteralPath $h)) { return 'none' }
    try { $o = Get-Content -LiteralPath $h -Raw | ConvertFrom-Json } catch { return 'unreadable' }
    if (Get-Process -Id ([int]$o.pid) -ErrorAction SilentlyContinue) { return ('held by running pid ' + $o.pid + ' purpose ' + $o.purpose) }
    'stale (pid ' + $o.pid + ' is not running; the lock itself is released by the OS)'
  }
  function Test-Production([string]$label) {
    $h = [string](git -C $prodRoot rev-parse HEAD)
    $s = Get-ProdServices
    Write-Output ("PROD_" + $label + " HEAD=" + $h + " " + $s)
    if (($h -ne $prodExpected) -or ($s -ne $prodWant)) { throw "STEP FAILED: production is not as expected ($label) -- send this output" }
    # [REASON]: the production collector shares the session and the lock with the pilot; its DJI
    # code must be the one the pilot was checked against (8df5683 = the pin for these paths).
    & git -C $prodRoot diff --quiet $prodBase HEAD -- @prodDji
    $committed = $LASTEXITCODE
    $edited = @(git -C $prodRoot --no-optional-locks status --porcelain --untracked-files=no -- @prodDji)
    if (($committed -ne 0) -or ($edited.Count -ne 0)) { throw "STEP FAILED: the production DJI code (drone_collector, dji_area, drones.py) differs from $prodBase (diff exit $committed, $($edited.Count) local change(s)) -- a new compatibility check is needed" }
    Write-Output ("PROD_DJI_CODE_" + $label + "=unchanged since " + $prodBase)
  }
  function Test-Collision([string]$label) {
    foreach ($n in $prodTasks) {
      $t = @(Get-ScheduledTask -TaskName $n -ErrorAction SilentlyContinue)
      if ($t.Count -ne 1) { throw "STEP FAILED: $($t.Count) scheduled tasks named $n, expected one -- send this output" }
      $i = Get-ScheduledTaskInfo -TaskName $t[0].TaskName -TaskPath $t[0].TaskPath
      $next = $null
      if ($i.NextRunTime) { $next = [datetime]$i.NextRunTime; if ($next.Year -lt 2001) { $next = $null } }
      $gap = if ($next) { [math]::Floor(($next - (Get-Date)).TotalMinutes) } else { $null }
      Write-Output ("PROD_TASK_" + $label + " " + $n + " state=" + $t[0].State + " next=" + $(if ($next) { $next.ToString('yyyy-MM-dd HH:mm') + ' (in ' + $gap + ' min)' } else { 'none' }))
      if ([string]$t[0].State -eq 'Running') { throw "STEP FAILED: production task $n is running now -- run this block again after it finishes" }
      if (($null -ne $gap) -and ($gap -lt $minGapMin)) { throw "STEP FAILED: production task $n starts in $gap min; the canary needs a window of $minGapMin min -- run this block after that run" }
    }
    $procs = @(Get-CimInstance -ClassName Win32_Process | Where-Object { [string]$_.CommandLine -match $collectorRx })
    Write-Output ("COLLECTOR_PROCESSES_" + $label + "=" + $procs.Count)
    if ($procs.Count -gt 0) { throw "STEP FAILED: $($procs.Count) collector or cycle process(es) are running (pid $(($procs | ForEach-Object { $_.ProcessId }) -join ',')) -- run this block again after they finish" }
    $owner = Get-LockOwner
    Write-Output ("PROD_LOCK_OWNER_" + $label + "=" + $owner)
    if (($owner -ne 'none') -and ($owner -notlike 'stale*')) { throw "STEP FAILED: the production collector lock is $owner -- run this block again after it finishes" }
  }
  function Test-Staging([string]$label) {
    $h = [string](git -C $root rev-parse HEAD)
    $dirty = @(git -C $root --no-optional-locks status --porcelain --untracked-files=no)
    $svc = Get-Service -Name $service
    Write-Output ("STAGING_" + $label + " HEAD=" + $h + " tracked_changes=" + $dirty.Count + " " + $service + "=" + $svc.Status)
    if (($h -ne $pin) -or ($dirty.Count -ne 0)) { throw "STEP FAILED: staging is not on the clean pilot revision ($label) -- send this output" }
    if ([string]$svc.Status -ne 'Running') { throw "STEP FAILED: $service is $($svc.Status) ($label)" }
    foreach ($name in $bots) {
      $b = Get-Service -Name $name
      Write-Output ("STAGING_BOT_" + $label + " " + $name + " " + $b.Status + " " + $b.StartType)
      if (([string]$b.Status -ne 'Stopped') -or ([string]$b.StartType -ne 'Disabled')) { throw "STEP FAILED: $name is $($b.Status) $($b.StartType), B1 left it Stopped Disabled ($label)" }
    }
    $t = @(Get-ScheduledTask -TaskName $isoTask -ErrorAction SilentlyContinue)
    if (($t.Count -ne 1) -or ([string]$t[0].Settings.Enabled -ne 'False') -or ([string]$t[0].State -ne 'Disabled')) { throw "STEP FAILED: $isoTask is not Disabled as B1 left it ($label) -- send this output" }
    $extra = @((Get-ItemProperty -LiteralPath $siteParams).AppEnvironmentExtra)
    if (@($extra -match '^\s*DJI_REFRESH_LAUNCHER=').Count -ne 0) { throw "STEP FAILED: DJI_REFRESH_LAUNCHER is back in the staging site environment ($label)" }
    Write-Output ("STAGING_BARRIERS_" + $label + "=" + $isoTask + " Disabled, DJI_REFRESH_LAUNCHER absent")
    $login = Invoke-WebRequest -Uri ($site + '/login') -UseBasicParsing -TimeoutSec 30
    if (([int]$login.StatusCode -ne 200) -or ([string]$login.Content -notmatch 'vs-login-form')) { throw "STEP FAILED: the staging login page did not answer 200 with the form ($label)" }
    Write-Output ("STAGING_LOGIN_" + $label + "=200")
  }
  function Get-Registered([string]$out) {
    $ErrorActionPreference = 'Continue'
    & $python tools\check_migration_drift.py --db $db > $out 2>&1
    @(Select-String -LiteralPath $out -Pattern '^registered migrations: (\d+);' | ForEach-Object { $_.Matches[0].Groups[1].Value })
  }
  New-Item -ItemType Directory -Force -Path $work | Out-Null
  $log = Join-Path $work ('card_pilot_w1_' + $stamp + '.log')
  try { Start-Transcript -Path $log -Append | Out-Null } catch { Write-Output 'NOTE: the log file could not be started' }
  $failure = $null
  $collected = $false
  $stoppedSite = $false
  $measure = $null
  $stats = $null
  $summary = @{}
  try {
    Write-Output '== 1. Checks before DJI -- nothing is changed and nothing is sent'
    if ((hostname) -ne $expectedHost) { throw "STEP FAILED: host is $(hostname), expected $expectedHost" }
    foreach ($p in @($src, $cpy, $python, $db, $snapshot, (Join-Path $planDir 'canary_ids.txt'), (Join-Path $planDir 'plan.json'), (Join-Path $planDir 'fingerprint_before.json'), $session)) {
      if (-not (Test-Path -LiteralPath $p)) { throw "STEP FAILED: not found: $p" }
    }
    $plan = Get-Content -LiteralPath (Join-Path $planDir 'plan.json') -Raw | ConvertFrom-Json
    $canaryFile = Join-Path $planDir 'canary_ids.txt'
    $ids = Read-Ids $canaryFile
    $idsSha = Get-Sha $canaryFile
    if (($idsSha -ne $canarySha) -or ($plan.sample.canary_ids_sha256 -ne $canarySha)) { throw "STEP FAILED: canary_ids.txt has sha256 $idsSha, frozen $canarySha (plan.json $($plan.sample.canary_ids_sha256))" }
    if (($ids.Count -ne 50) -or (@($ids | Select-Object -Unique).Count -ne 50)) { throw "STEP FAILED: canary_ids.txt holds $($ids.Count) ids, the frozen canary is 50 unique ids" }
    if ((Get-Sha (Join-Path $planDir 'pilot_manifest.csv')) -ne $plan.sample.manifest_sha256) { throw 'STEP FAILED: pilot_manifest.csv is not the frozen one' }
    Write-Output ("CANARY_COUNT=" + $ids.Count)
    Write-Output ("CANARY_SHA256=" + $idsSha)
    # [REASON]: the collector sends only after its whole walk and every W1 run has its own
    # queue, so a run stopped by DJI leaves staging as it was. Pasting the block again would
    # visit DJI for all 50 again; only a run that found the lock busy (exit 24) collected nothing.
    $earlier = @(Get-ChildItem -LiteralPath $w1Root -Directory -ErrorAction SilentlyContinue | Where-Object { Test-Path -LiteralPath (Join-Path $_.FullName 'collector_stdout.log') } | Where-Object { $e = Join-Path $_.FullName 'collector_exit.txt'; -not ((Test-Path -LiteralPath $e) -and ([string](Get-Content -LiteralPath $e -Raw)).Trim() -eq '24') })
    if ($earlier.Count -gt 0) { throw "STEP FAILED: an earlier W1 run already started the collector ($($earlier[-1].FullName)); the canary is collected once -- send that run's output, a new collection needs the owner's decision" }
    Write-Output 'EARLIER_W1_COLLECTION=none'

    $srcHead = [string](git -C $src rev-parse HEAD)
    $srcDirty = @(git -C $src --no-optional-locks status --porcelain --untracked-files=no)
    if (($srcHead -ne $pin) -or ($srcDirty.Count -ne 0)) { throw "STEP FAILED: the pilot checkout $src is at $srcHead with $($srcDirty.Count) tracked change(s); expected the clean pin $pin" }
    $pkg = [System.IO.Path]::Combine($src, 'drone_collector')
    $mainText = [System.IO.File]::ReadAllText([System.IO.Path]::Combine($pkg, 'main.py'))
    foreach ($w in @("'--sources'", "'--ids-file'", "'--send-sources'", 'DJI_COLLECTOR_LOCK_PATH', 'DJI_COLLECTOR_LOCK_WAIT_S')) { if (-not $mainText.Contains($w)) { throw "STEP FAILED: the pilot collector does not know $w" } }
    if (-not ([System.IO.File]::ReadAllText([System.IO.Path]::Combine($pkg, 'config.py'))).Contains('DRONE_OUTBOX_DIR')) { throw 'STEP FAILED: the pilot collector does not know DRONE_OUTBOX_DIR' }
    if (Test-Path -LiteralPath ([System.IO.Path]::Combine($pkg, '.env'))) { throw 'STEP FAILED: the pilot checkout has a drone_collector\.env; the child environment must be the only source of settings' }
    Write-Output ("COLLECTOR_CODE=" + $src + " HEAD=" + $srcHead + " clean")

    if ($site -match ':5050') { throw "STEP FAILED: the receiver $site is the production port 5050 -- refused" }
    if ($site -notmatch ':5051$') { throw "STEP FAILED: the receiver $site is not the staging port 5051" }
    # [REASON]: staging accepts only its own token (W1, 08.10.2026: the machine token got 401).
    # It is read from the staging service environment, used once in the child, never shown.
    $tokenLines = @(@((Get-ItemProperty -LiteralPath $siteParams).AppEnvironmentExtra) | Where-Object { [string]$_ -match '^\s*DRONE_API_TOKEN=' })
    if ($tokenLines.Count -ne 1) { throw "STEP FAILED: the staging service environment holds $($tokenLines.Count) DRONE_API_TOKEN entries, expected exactly one" }
    $token = ([string]$tokenLines[0] -replace '^\s*DRONE_API_TOKEN=', '').Trim()
    $tokenLines = $null
    if (-not $token) { throw 'STEP FAILED: the DRONE_API_TOKEN of the staging service is empty' }
    $childEnv = [ordered]@{ VEHICLE_SOFT_BASE_URL = $site; DJI_STORAGE_STATE = $session; DJI_COLLECTOR_LOCK_PATH = $lock; DJI_COLLECTOR_LOCK_WAIT_S = '0'; DRONE_OUTBOX_DIR = $outbox; DJI_HEADLESS = 'true'; DRONE_API_TOKEN = $token; PYTHONIOENCODING = 'utf-8' }
    $childDrop = @('PYTHONPATH', 'PYTHONHOME', 'PYTHONSAFEPATH', 'PYTHONSTARTUP')
    $inherited = @(Get-ChildItem Env: | Where-Object { ($_.Name -match '^(DJI_|DRONE_|VEHICLE_SOFT_|PLAYWRIGHT_|HTTPS?_PROXY$|NO_PROXY$)') -and (-not $childEnv.Contains($_.Name)) } | ForEach-Object { $_.Name })
    Write-Output ("CHILD_ENV_SET=" + (@($childEnv.Keys) -join ',') + " (DRONE_API_TOKEN of the staging service, value not shown)")
    Write-Output ("CHILD_ENV_DROPPED=" + ($childDrop -join ',') + " CHILD_ENV_INHERITED=" + $(if ($inherited.Count) { $inherited -join ',' } else { 'none' }) + " (names only)")

    # [REASON]: python itself decides whether its import folders are the pilot folder
    # (samefile): 8.3 short names and letter case make a text comparison unreliable.
    $p = Start-Child $cpy ("-B -c `"import os, sys, importlib.util as u, drone_collector.config as c, drone_collector.main as m, drone_collector.sources as s; d = [str(c.PACKAGE_ROOT), os.path.dirname(os.path.abspath(m.__file__)), os.path.dirname(os.path.abspath(s.__file__))]; print(d[0]); print(os.path.abspath(m.__file__)); print(u.find_spec('playwright').origin); print(all(os.path.samefile(x, sys.argv[1]) for x in d))`" `"" + $pkg + "`"") $src
    $probeErr = $p.StandardError.ReadToEndAsync()
    $probe = @($p.StandardOutput.ReadToEnd() -split "`r?`n" | Where-Object { $_ })
    $p.WaitForExit()
    if (($p.ExitCode -ne 0) -or ($probe.Count -ne 4)) { throw "STEP FAILED: the collector python could not import the pilot collector and Playwright (exit $($p.ExitCode)) -- $($probeErr.Result)" }
    if ($probe[3] -ne 'True') { throw "STEP FAILED: the collector python imports drone_collector from $($probe[0]), not from $pkg" }
    Write-Output ("PYTHON=" + $cpy)
    Write-Output ("IMPORTS drone_collector=" + $probe[0] + " main=" + $probe[1] + " playwright=" + $probe[2])
    $browsers = @(@($env:PLAYWRIGHT_BROWSERS_PATH, $(if ($env:LOCALAPPDATA) { Join-Path $env:LOCALAPPDATA 'ms-playwright' })) | Where-Object { $_ -and (Test-Path -LiteralPath $_) } | ForEach-Object { Get-ChildItem -LiteralPath $_ -Directory -Filter 'chromium*' })
    Write-Output ("BROWSER=" + $(if ($browsers.Count) { $browsers[0].FullName } else { 'not found for this Windows user; if it is missing the collector stops at launch, before any DJI page' }))
    $sessionBefore = Get-FileState $session
    Write-Output ("SESSION=" + $session + " sha256=" + $sessionBefore + " (read only; --save-session is never passed)")
    Write-Output ("LOCK=" + $lock + " (shared with the production collector; wait 0 s)")
    Write-Output ("RECEIVER=" + $site + "/drones/api/source_sync")
    Write-Output ("PILOT_OUTBOX=" + $outbox)
    Write-Output ("PILOT_LOG=" + $collectLog)

    Test-Production 'BEFORE'
    Test-Collision 'BEFORE'
    Test-Staging 'BEFORE'
    $open = @(Get-ChildItem -LiteralPath $runRoot -Directory -Filter 'staging_*' | Where-Object { -not (Test-Path -LiteralPath (Join-Path $_.FullName 'returned.txt')) })
    if (($open.Count -ne 1) -or (-not (Test-Path -LiteralPath (Join-Path $open[0].FullName 'swapped.txt')))) { throw "STEP FAILED: expected exactly one open B1 run with swapped.txt under $runRoot, found $($open.Count) -- send this output" }
    Write-Output ("B1_RUN=" + $open[0].FullName + " (open; block R returns staging after the pilot)")
    $body = @{ token = $token; content_md5 = @() } | ConvertTo-Json -Compress
    try { $pre = Invoke-WebRequest -Uri ($site + '/drones/api/land_geometry_manifest') -Method Post -Body $body -ContentType 'application/json' -UseBasicParsing -TimeoutSec 30 -MaximumRedirection 0 } catch { throw "STEP FAILED: staging refused the DRONE_API_TOKEN of its own service on a read-only call ($($_.Exception.Message)) -- nothing was collected" }
    if (([int]$pre.StatusCode -ne 200) -or ([string]$pre.Content -notmatch '"asked"\s*:\s*0')) { throw "STEP FAILED: staging answered the read-only token check with $($pre.StatusCode)" }
    Write-Output 'TOKEN_CHECK=staging accepts the DRONE_API_TOKEN of its own service (read-only land_geometry_manifest, nothing written)'

    New-Item -ItemType Directory -Force -Path $w1 | Out-Null
    if (Test-Path -LiteralPath $outbox) { throw "STEP FAILED: the pilot outbox $outbox already exists" }
    Set-Content -LiteralPath (Join-Path $w1 'w1_check.py') -Value $helperText -Encoding ASCII
    Copy-Item -LiteralPath $canaryFile -Destination $idsCopy
    if ((Get-Sha $idsCopy) -ne $canarySha) { throw 'STEP FAILED: the copy of canary_ids.txt differs from the frozen file' }
    Set-Location -LiteralPath $root
    $fpPre = Join-Path $w1 'fingerprint_pre.json'
    & $python tools\dji_card_coverage_pilot.py fingerprint --db $db --out $fpPre | Out-Null
    if ($LASTEXITCODE -ne 0) { throw "STEP FAILED: fingerprint of the staging database (exit $LASTEXITCODE)" }
    if (-not (Test-SameFile $fpPre (Join-Path $planDir 'fingerprint_before.json'))) { throw 'STEP FAILED: the staging database no longer equals the B0 fingerprint -- send this output' }
    Write-Output 'FINGERPRINT_PRE=equals B0 fingerprint_before.json'
    $reg = @(Get-Registered (Join-Path $w1 'drift_before.log'))
    if (($reg.Count -ne 1) -or ($reg[0] -ne '60')) { throw "STEP FAILED: the staging database reports $($reg -join ',') registered migrations, expected 60" }
    Write-Output 'REGISTERED=60'
    $stBefore = Invoke-Helper @('snapshot', $db)
    $b0 = Invoke-Helper @('snapshot', $snapshot)
    if ($stBefore['CATALOG_SHA256'] -ne $b0['CATALOG_SHA256']) { throw 'STEP FAILED: the staging field catalog differs from the B0 copy' }
    Write-Output ("STAGING_COUNTERS sources_max_id=" + $stBefore['SOURCE_MAX_ID'] + " v4_summaries=" + $stBefore['V4SUM_ROWS'] + " catalog=equals B0")
    $probeProd = Invoke-Helper @('canary', $prodDb, $idsCopy, '0', '0', (Get-Date).ToUniversalTime().ToString('yyyy-MM-dd HH:mm:ss'), ('w1-probe-' + $stamp))
    if (($probeProd['RUN_ID_ROWS'] -ne '0') -or ($probeProd['CANARY_EVIDENCE_SINCE'] -ne '0')) { throw 'STEP FAILED: the read-only production check did not answer as expected' }
    Write-Output 'PROD_DB_READ=ok (mode=ro, counters only)'
    $sched = @(Get-ScheduledTask | Where-Object { ([string]$_.TaskPath -notlike '\Microsoft\*') -and ([string]$_.State -ne 'Disabled') -and ((@($_.Actions | ForEach-Object { [string]$_.Execute + ' ' + [string]$_.Arguments + ' ' + [string]$_.WorkingDirectory }) -join ' ') -match ('transport-report-staging|:5051|VehicleSoft_|' + $collectorRx)) -and ($prodTasks -notcontains $_.TaskName) -and ($_.TaskName -ne 'TransportDBBackupStaging') })
    if ($sched.Count -gt 0) { throw "STEP FAILED: enabled scheduled task(s) that may collect or write staging: $(($sched | ForEach-Object { $_.TaskName }) -join ', ')" }

    Write-Output '== 2. Live canary: 50 frozen flights, staging receiver only, shared production lock'
    Test-Production 'LAUNCH'
    Test-Collision 'LAUNCH'
    $t0 = (Get-Date).ToUniversalTime().AddSeconds(-5).ToString('yyyy-MM-dd HH:mm:ss')
    Write-Output ("NOW=" + (Get-Date).ToString('yyyy-MM-dd HH:mm:ss') + " UTC=" + $t0)
    $collected = $true
    $clock = [System.Diagnostics.Stopwatch]::StartNew()
    $proc = Start-Child $cpy ('-m drone_collector.main --sources --ids-file "' + $idsCopy + '" --send-sources') $src
    $errTask = $proc.StandardError.ReadToEndAsync()
    Write-Output ("COLLECTOR_PID=" + $proc.Id + " -- keep this window open until STEP= is printed; if it was closed: taskkill /PID " + $proc.Id + " /T /F")
    $writer = New-Object System.IO.StreamWriter($collectLog, $false, (New-Object System.Text.UTF8Encoding($false)))
    $stopWhy = $null
    $configSeen = $false
    $noCard = 0
    $pageErr = 0
    $cardRefused = 0
    $lines = New-Object System.Collections.ArrayList
    try {
      $next = $proc.StandardOutput.ReadLineAsync()
      while ($true) {
        if (-not $next.Wait(5000)) {
          if ($clock.Elapsed.TotalMinutes -gt $maxCollectMin) { $stopWhy = "the run passed the $maxCollectMin min limit"; break }
          continue
        }
        $line = $next.Result
        if ($null -eq $line) { break }
        [void]$lines.Add($line)
        $writer.WriteLine($line)
        $writer.Flush()
        if ($line -notmatch ': Flight \d+: captured ') { Write-Output ('  | ' + $line) }
        if ($line -match 'Configuration: (\{.*\})\s*$') {
          $configSeen = $true
          $cfg = $Matches[1] -replace '\\\\', '\'
          foreach ($want in @(("'source_sync_url': '" + $site + "/drones/api/source_sync'"), ("'outbox_dir': '" + $outbox + "'"), ("'storage_state': '" + $session + "'"), "'headless': True", "'api_token': 'set'")) {
            if (-not $cfg.Contains($want)) { $stopWhy = 'the collector configuration does not show ' + $want }
          }
        }
        if (($line -match ': Flight \d+: ') -and (-not $configSeen)) { $stopWhy = 'a flight was visited before the configuration line was seen' }
        # [REASON]: a byte count such as "(429 bytes)" is not a DJI answer; "HTTP 429" still is.
        $scan = $line -replace '\(\d+ bytes\)', '(N bytes)'
        foreach ($m in $stopMarkers) { if ($scan -match $m[1]) { $stopWhy = 'stop marker ' + $m[0] + ' in the collector log' } }
        if ($line -match ': Flight \d+: (V4|NO_V4_URL|NO_V4|V4_FAILED) \((.*)\)\s*$') {
          $parts = @($Matches[2] -split ',\s*')
          $pageErr = 0
          if ($parts -contains 'card') { $noCard = 0 } else {
            $noCard++
            # [REASON]: the collector logs no line for a refused card request. A flight whose
            # route, descriptor or V4 came but whose card did not is the only sign of it.
            if (@($parts | Where-Object { @('route', 'airlines', 'v4') -contains $_ }).Count -gt 0) { $cardRefused++ }
          }
        } elseif ($line -match ': Flight \d+: the record page did not open') { $noCard++; $pageErr++ }
        if ($pageErr -ge 3) { $stopWhy = 'three record pages in a row did not open' }
        if ($cardRefused -ge $maxCardRefused) { $stopWhy = "$maxCardRefused flights came without a card while their other parts came (refused card requests)" }
        # [REASON]: five, not three: up to 10 of 50 missing cards is still a result (minFetched),
        # and the ids are visited in order, so neighbours are alike.
        if ($noCard -ge $maxNoCardRun) { $stopWhy = "$maxNoCardRun flights in a row came without a card" }
        if ($stopWhy) { break }
        $next = $proc.StandardOutput.ReadLineAsync()
      }
    } finally {
      if ($stopWhy) { Stop-Child $proc }
      if (-not $proc.WaitForExit(120000)) { Stop-Child $proc; if (-not $stopWhy) { $stopWhy = 'the collector did not end after its output closed' } }
      $writer.Close()
      $errText = if ($errTask.Wait(30000)) { $errTask.Result } else { 'stderr still open after 30 s: a process of the collector tree is alive' }
      Set-Content -LiteralPath (Join-Path $w1 'collector_stderr.log') -Value $errText -Encoding UTF8
    }
    $clock.Stop()
    $code = $proc.ExitCode
    Set-Content -LiteralPath (Join-Path $w1 'collector_exit.txt') -Value ([string]$code) -Encoding ASCII
    $wall = [math]::Round($clock.Elapsed.TotalSeconds)
    Write-Output ("COLLECTOR_EXIT=" + $code + " WALL_SECONDS=" + $wall + " CARD_REFUSED_FLIGHTS=" + $cardRefused + $(if ($stopWhy) { " STOPPED_BY_THIS_BLOCK=" + $stopWhy } else { '' }))

    Write-Output '== 3. Collector gate'
    $sessionAfter = Get-FileState $session
    Write-Output ("SESSION_AFTER=" + $(if ($sessionAfter -eq $sessionBefore) { 'unchanged' } else { 'CHANGED ' + $sessionAfter }))
    $ownerAfter = Get-LockOwner
    Write-Output ("PROD_LOCK_OWNER_AFTER=" + $ownerAfter)
    $sumLine = @($lines | Where-Object { $_ -match 'RUN SUMMARY ' } | Select-Object -Last 1)
    if ($sumLine.Count -eq 1) { foreach ($m in [regex]::Matches(($sumLine[0] -replace '^.*RUN SUMMARY ', ''), '(\w+)=("[^"]*"|\S+)')) { $summary[$m.Groups[1].Value] = $m.Groups[2].Value.Trim('"') } }
    $runId = [string]$summary['snapshot_run_id']
    Write-Output ("RUN_SUMMARY run_id=" + $runId + " requested=" + $summary['sources_requested'] + " visited=" + $summary['sources_visited'] + " card=" + $summary['sources_card'] + " rejected=" + $summary['sources_rejected'] + " v4_failed=" + $summary['sources_v4_failed'] + " page_errors=" + $summary['sources_page_errors'] + " descriptor_refused=" + $summary['sources_descriptor_refused'] + " sent=" + $summary['sources_envelopes_sent'] + " accepted=" + $summary['sources_batch_accepted'] + " new=" + $summary['sources_new'] + " ingest_errors=" + $summary['sources_ingest_errors'])
    if ($runId) {
      $ourLog = [System.IO.Path]::Combine($pkg, 'logs', 'collector.log')
      $inPilot = (Test-Path -LiteralPath $ourLog) -and [bool](Select-String -LiteralPath $ourLog -SimpleMatch -Pattern $runId -Quiet)
      $inProd = (Test-Path -LiteralPath $prodLog) -and [bool](Select-String -LiteralPath $prodLog -SimpleMatch -Pattern $runId -Quiet)
      Write-Output ("RUN_LOGGED_IN pilot_checkout=" + $inPilot + " production_checkout=" + $inProd)
    }
    $prodAfter = Invoke-Helper @('canary', $prodDb, $idsCopy, '0', '0', $t0, $(if ($runId) { $runId } else { 'w1-no-run-id' }))
    Write-Output ("PROD_DB_AFTER run_rows=" + $prodAfter['RUN_ID_ROWS'] + " canary_sources_since=" + $prodAfter['CANARY_SOURCES_SINCE'] + " canary_evidence_since=" + $prodAfter['CANARY_EVIDENCE_SINCE'])
    if ($sessionAfter -ne $sessionBefore) { throw 'STEP FAILED: the production DJI session file changed during the run -- send this output' }
    if (($prodAfter['RUN_ID_ROWS'] -ne '0') -or ($prodAfter['CANARY_SOURCES_SINCE'] -ne '0') -or ($prodAfter['CANARY_EVIDENCE_SINCE'] -ne '0')) { throw 'STEP FAILED: the production database received canary evidence -- send this output' }
    if ($stopWhy) { throw "STEP FAILED: the canary was stopped: $stopWhy -- nothing is recalculated; evidence kept in $w1" }
    if ($code -eq 24) { throw "STEP FAILED: the production collector took the shared lock first (exit 24); nothing was collected -- run this block again after it finishes" }
    if (@(0, 18) -notcontains $code) { throw "STEP FAILED: the collector ended with exit $code (2 session, 19 not accepted, 24 lock busy, 1 error) -- nothing is recalculated; evidence kept in $w1" }
    if (($ownerAfter -ne 'none') -and ($ownerAfter -notlike 'stale*')) { throw "STEP FAILED: the production lock is $ownerAfter after the run" }
    if (-not $runId) { throw 'STEP FAILED: the collector printed no RUN SUMMARY with a run id' }
    if (-not $inPilot -or $inProd) { throw "STEP FAILED: the run was not logged by the pilot checkout only (pilot=$inPilot production=$inProd)" }
    if (($summary['sources_requested'] -ne '50') -or ($summary['sources_visited'] -ne '50')) { throw "STEP FAILED: the collector requested $($summary['sources_requested']) and visited $($summary['sources_visited']) of 50" }
    # [REASON]: card, airlines and route refusals (403, 429, an error code in the body) reach the
    # log only as this sum. A refused V4 download leaves its own V4_FAILED status and is measured.
    if ([int]$summary['sources_rejected'] -gt [int]$summary['sources_v4_failed']) { throw "STEP FAILED: DJI refused $([int]$summary['sources_rejected'] - [int]$summary['sources_v4_failed']) request(s) that were not V4 downloads (sources_rejected=$($summary['sources_rejected']), sources_v4_failed=$($summary['sources_v4_failed'])) -- nothing is recalculated; evidence kept in $w1" }
    if (($summary['sources_batch_accepted'] -ne 'true') -or ($summary['sources_ingest_errors'] -ne '0')) { throw "STEP FAILED: staging did not accept every source (accepted=$($summary['sources_batch_accepted']) errors=$($summary['sources_ingest_errors']))" }
    $statsLog = Join-Path $w1 'collector_for_stats.log'
    # [REASON]: "Flight N: captured card (429 bytes)" carries no DJI answer, but the HTTP_429
    # marker of collector-stats matches its byte count; byte counts are masked as in the live watch.
    [System.IO.File]::WriteAllLines($statsLog, [string[]]@($lines | ForEach-Object { $_ -replace '\(\d+ bytes\)', '(N bytes)' }), (New-Object System.Text.UTF8Encoding($false)))
    $statsJson = Join-Path $w1 'collector_stats.json'
    $statsOut = @(& $python tools\dji_card_coverage_pilot.py collector-stats --log $statsLog --ids $idsCopy --out $statsJson)
    $statsCode = $LASTEXITCODE
    $statsOut | ForEach-Object { Write-Output ('  ' + $_) }
    if ($statsCode -ne 0) { throw "STEP FAILED: collector-stats exit $statsCode (6 = DJI stop markers) -- nothing is recalculated; evidence kept in $w1" }
    $stats = Get-Content -LiteralPath $statsJson -Raw | ConvertFrom-Json
    if (([int]$stats.visited -ne 50) -or (@($stats.not_visited).Count -ne 0)) { throw "STEP FAILED: collector-stats saw $($stats.visited) of 50 flights visited" }
    $stNew = Invoke-Helper @('canary', $db, $idsCopy, $stBefore['SOURCE_MAX_ID'], $stBefore['V4SUM_MAX_ID'], $t0, $runId)
    Write-Output ("STAGING_NEW_EVIDENCE revisions=" + $stNew['NEW_REVISIONS'] + " flights=" + $stNew['NEW_FLIGHTS'] + " outside_canary=" + $stNew['NEW_OUTSIDE_CANARY'] + " other_run=" + $stNew['NEW_OTHER_RUN'] + " repeated_type=" + $stNew['NEW_REPEATED_TYPE'] + " evidence_outside_since=" + $stNew['EVIDENCE_OUTSIDE_SINCE'])
    if (($stNew['NEW_OUTSIDE_CANARY'] -ne '0') -or ($stNew['NEW_OTHER_RUN'] -ne '0') -or ($stNew['NEW_REPEATED_TYPE'] -ne '0') -or ($stNew['EVIDENCE_OUTSIDE_SINCE'] -ne '0') -or ([int]$stNew['NEW_FLIGHTS'] -gt 50)) { throw 'STEP FAILED: staging holds new evidence that is not this run of the 50 canary flights -- nothing is recalculated' }
    if ($stNew['NEW_REVISIONS'] -ne $summary['sources_new']) { throw "STEP FAILED: staging holds $($stNew['NEW_REVISIONS']) new revisions, the collector reported $($summary['sources_new'])" }
    Write-Output 'COLLECTOR_GATE=PASS'

    Write-Output '== 4. Staging S1: site stopped, recalc of exactly these 50 flights, measure, census'
    Stop-Service -Name $service -Force
    $stoppedSite = $true
    (Get-Service -Name $service).WaitForStatus('Stopped', (New-TimeSpan -Seconds 90))
    & $python tools\check_db_lock.py --db $db | Out-Null
    if (@(0, 3) -notcontains $LASTEXITCODE) { throw "STEP FAILED: check_db_lock exit $LASTEXITCODE -- another process holds the staging database" }
    if ((Invoke-Helper @('snapshot', $db))['CATALOG_SHA256'] -ne $b0['CATALOG_SHA256']) { throw 'STEP FAILED: the staging field catalog changed during the run' }
    $flightArgs = @($ids | ForEach-Object { '--flight-id'; [string]$_ })
    foreach ($mode in @('--dry-run', '--apply')) {
      $name = $mode.TrimStart('-')
      $out = @(& $python tools\dji_area_recalc.py --db $db --from 2026-09-01 --to 2026-09-30 $mode --quiet --json (Join-Path $w1 ('recalc_' + $name + '.json')) @flightArgs)
      $rc = $LASTEXITCODE
      Set-Content -LiteralPath (Join-Path $w1 ('recalc_' + $name + '.txt')) -Value $out -Encoding UTF8
      if ($rc -ne 0) { throw "STEP FAILED: recalc $mode exit $rc -- $($out -join ' ')" }
      $r = Get-Content -LiteralPath (Join-Path $w1 ('recalc_' + $name + '.json')) -Raw | ConvertFrom-Json
      $calc = 0; foreach ($q in $r.calc_writes.PSObject.Properties) { $calc += [int]$q.Value }
      $field = 0; foreach ($q in $r.field_writes.PSObject.Properties) { $field += [int]$q.Value }
      Write-Output ("RECALC_" + $name.ToUpper() + " flights_in_period=" + $r.flights_in_period + " calc_writes=" + $calc + " field_writes=" + $field + " tiers=" + (@($r.tier_counts.PSObject.Properties | ForEach-Object { $_.Name + '=' + $_.Value }) -join ','))
      if ([int]$r.flights_in_period -ne 50) { throw "STEP FAILED: recalc $mode took $($r.flights_in_period) flights, expected exactly the 50 canary flights" }
      if (($mode -eq '--apply') -and (($calc -ne 50) -or ($field -ne 50))) { throw "STEP FAILED: recalc wrote $calc calculation and $field attribution rows, expected 50 and 50" }
    }
    $stAfter = Invoke-Helper @('canary', $db, $idsCopy, $stBefore['SOURCE_MAX_ID'], $stBefore['V4SUM_MAX_ID'], $t0, $runId)
    Write-Output ("V4_SUMMARY_CACHE new=" + $stAfter['V4SUM_NEW'] + " outside_canary=" + $stAfter['V4SUM_NEW_OUTSIDE_CANARY'] + " (decoded-V4 cache recalc keeps for loaded neighbours; not evidence, not attribution)")
    if ($stAfter['NEW_REVISIONS'] -ne $stNew['NEW_REVISIONS']) { throw 'STEP FAILED: source revisions changed during the recalc' }
    $fpPost = Join-Path $w1 'fingerprint_post.json'
    & $python tools\dji_card_coverage_pilot.py fingerprint --db $db --out $fpPost | Out-Null
    if ($LASTEXITCODE -ne 0) { throw "STEP FAILED: fingerprint after the canary (exit $LASTEXITCODE)" }
    $mOut = @(& $python tools\dji_card_coverage_pilot.py measure --db $db --plan-dir $planDir --stage canary --before $fpPre --collector-stats $statsJson --out-dir (Join-Path $w1 'measure'))
    $mCode = $LASTEXITCODE
    $mOut | ForEach-Object { Write-Output ('  ' + $_) }
    if ($mCode -ne 0) { throw "STEP FAILED: measure exit $mCode (5 = an immutability gate failed) -- send this output" }
    $measure = Get-Content -LiteralPath ([System.IO.Path]::Combine($w1, 'measure', 'measure_canary.json')) -Raw | ConvertFrom-Json
    $cOut = @(& $python tools\dji_field_census.py --db $db --from 2026-09-01 --to 2026-09-30 --json (Join-Path $w1 'census_after.json'))
    if ($LASTEXITCODE -ne 0) { throw "STEP FAILED: census after the canary exit $LASTEXITCODE" }
    $seen = @{}
    foreach ($l in @($cOut | Where-Object { $_ -match 'flights total|CONFIRMED|EXACT|IDENTIFIED|NO_CARD|NO_KEY|NOT_IN_CATALOG|KEY_AFTER' })) { $x = $l.Trim(); if (-not $seen.ContainsKey($x)) { $seen[$x] = 1; Write-Output ('  CENSUS ' + $x) } }
    Start-Service -Name $service
    (Get-Service -Name $service).WaitForStatus('Running', (New-TimeSpan -Seconds 90))
    $stoppedSite = $false
    Start-Sleep -Seconds 8
    $reg = @(Get-Registered (Join-Path $w1 'drift_after.log'))
    if (($reg.Count -ne 1) -or ($reg[0] -ne '60')) { throw "STEP FAILED: after the canary the staging database reports $($reg -join ',') registered migrations" }
    Test-Staging 'AFTER'
    Test-Production 'AFTER'
    if ((Get-FileState $session) -ne $sessionBefore) { throw 'STEP FAILED: the production DJI session file changed' }
  } catch {
    $failure = $_.Exception.Message + ' [block line ' + $_.InvocationInfo.ScriptLineNumber + ']'
  }
  if ($stoppedSite) {
    try { Start-Service -Name $service; (Get-Service -Name $service).WaitForStatus('Running', (New-TimeSpan -Seconds 90)); Write-Output ("STAGING_SITE_RESTARTED=" + (Get-Service -Name $service).Status) } catch { Write-Output ("STAGING_SITE_RESTART_FAILED=" + $_.Exception.Message) }
  }

  Write-Output '== 5. Canary result'
  if ($stats) {
    $median = if ($null -ne $stats.seconds_per_visit_median) { [math]::Round([double]$stats.seconds_per_visit_median, 1) } else { '-' }
    Write-Output ("ATTEMPTED=50 VISITED=" + $stats.visited + " CARDS_CAPTURED=" + $stats.card_captured + " FAILED=" + (50 - [int]$stats.card_captured) + " NOT_VISITED=" + @($stats.not_visited).Count + " FETCH_SUCCESS=" + (Pct ([int]$stats.card_captured / 50.0)) + " WALL_SECONDS=" + $wall + " MEDIAN_SECONDS_PER_FLIGHT=" + $median)
    Write-Output ("STATUSES=" + ($stats.statuses | ConvertTo-Json -Compress) + " STOP_MARKERS=" + ($stats.stop_markers | ConvertTo-Json -Compress))
  }
  $decision = 'STOP'
  $why = $failure
  if ((-not $failure) -and $measure) {
    $rows = @(Import-Csv -LiteralPath ([System.IO.Path]::Combine($w1, 'measure', 'results_canary.csv')))
    $count = @{}
    foreach ($r in $rows) { $count[$r.after] = 1 + [int]$count[$r.after] }
    $exact = [int]$count['EXACT']
    $ident = [int]$count['IDENTIFIED']
    $other = @($rows | Where-Object { @('EXACT', 'IDENTIFIED', 'NO_KEY', 'NOT_IN_CATALOG', 'NO_CARD') -notcontains $_.after }).Count
    Write-Output ("OUTCOME EXACT=" + $exact + " IDENTIFIED=" + $ident + " CONFIRMED=" + ($exact + $ident) + " NO_KEY=" + [int]$count['NO_KEY'] + " NOT_IN_CATALOG=" + [int]$count['NOT_IN_CATALOG'] + " NO_CARD=" + [int]$count['NO_CARD'] + " OTHER_UNRESOLVED=" + $other + " NO_CALC=" + @($rows | Where-Object { $_.has_calc_after -ne 'True' }).Count)
    foreach ($g in @($rows | Group-Object after | Sort-Object Name)) { Write-Output ("CASES " + $g.Name + ": " + (@($g.Group | Select-Object -First 5 | ForEach-Object { $_.flight_id }) -join ', ')) }
    $fetched = [int]$measure.fetched
    $cf = $measure.conversion_among_fetched
    $lo = $cf.wilson95[0]
    $hi = $cf.wilson95[1]
    Write-Output ("CONFIRMED_RATE among_fetched=" + (Pct $cf.rate) + " wilson95=" + (Pct $lo) + ".." + (Pct $hi) + " among_50=" + (Pct (($exact + $ident) / 50.0)) + " fetched=" + $fetched)
    $ps = $measure.post_stratified_among_fetched
    if ($ps) { Write-Output ("PROJECTION (not a fact; n=50) post-stratified " + (Pct $ps.estimate) + " (" + (Pct $ps.low) + ".." + (Pct $ps.high) + ")") }
    foreach ($k in @('at_manifest_rate', 'at_manifest_wilson_low', 'at_manifest_wilson_high')) { $v = $measure.projection_on_no_card_cohort.$k; if ($v) { Write-Output ("PROJECTION (not a fact) " + $k + ": +" + $v.additional_confirmed + " confirmed of " + $measure.projection_on_no_card_cohort.no_card_flights + " NO_CARD -> " + $v.coverage_pct + "% of September") } }
    foreach ($part in @('by_unit', 'by_week')) { foreach ($q in $measure.$part.PSObject.Properties) { Write-Output (($part.ToUpper()) + " " + $q.Name + " attempted=" + $q.Value.attempted + " fetched=" + $q.Value.fetched + " confirmed=" + $q.Value.confirmed + " no_key=" + $q.Value.no_key + " not_in_catalog=" + $q.Value.not_in_catalog) } }
    if ($fetched -lt $minFetched) { $decision = 'STOP'; $why = "cards came for $fetched of 50 flights, fewer than $minFetched, so historical cards are not reliably obtainable this way" }
    elseif (($null -ne $hi) -and ([double]$hi -lt $simplifyBelow)) { $decision = 'SIMPLIFY'; $why = 'even the upper 95% bound of the confirmed rate among fetched cards is below 20% (the low end of the hypothesis in section 3): more cards will not move coverage much; the next step is the field data gap (section 12)' }
    else { $decision = 'GO_TO_500'; $why = 'collection was safe and complete, every gate passed, and the confirmed rate is not ruled below 20%; the owner may authorize W2 (the other 450 flights) -- nothing starts by itself' }
  }
  Write-Output ("DECISION=" + $decision)
  Write-Output ("DECISION_REASON=" + $why)
  if ($collected) { Write-Output ("RUN=" + $w1) }
  Write-Output ("LOG FILE: " + $log)
  if ($failure) { Write-Output ("STEP=STOP - " + $failure) } else { Write-Output 'STEP=PASS' }
  try { Stop-Transcript | Out-Null } catch { }
}
```

Прислать весь вывод. Остальные 450 вылетов блок не запускает: W2 — только по решению владельца.

### W2+S2 — SRV-YOQSH: остальные 450 и итог пилота на 500

**Решение владельца 08.10.2026: GO_TO_500.** W2+S2 — не текст для
вставки, а файл репозитория
`ops/drone_card_coverage_001/W2_S2_remaining450_block.ps1`. В нём больше
900 строк, поэтому вставка из сообщения исключена: владелец получает сам
файл и сверяет sha256. Запуск на SRV-YOQSH, после того как файл положен в
`C:\VehicleSoft_CardPilot`:

```powershell
Get-FileHash -Algorithm SHA256 C:\VehicleSoft_CardPilot\W2_S2_remaining450_block.ps1
powershell.exe -NoProfile -ExecutionPolicy Bypass -File C:\VehicleSoft_CardPilot\W2_S2_remaining450_block.ps1
```

Хеш сверяется со значением из описания PR и сообщения о передаче. Окно не
закрывать до строки `STEP=`.

**Что собирается.** 450 — это замороженный `pilot_ids.txt` минус
`canary_ids.txt`, в порядке манифеста. Блок сверяет:

* sha256 `pilot_manifest.csv`, `pilot_ids.txt` и `canary_ids.txt` равны
  замороженным в нём и записанным в `plan.json`; в `plan.json` 500 и 50;
* `pilot_ids.txt` — 500 разных номеров в порядке манифеста;
  `canary_ids.txt` — первые 50 из них, флаги `canary` манифеста — те же;
* остаток — 450 номеров, с канарейкой не пересекается, вместе с ней даёт
  ровно 500.

Остаток пишется в папку прогона как `remaining_450_ids.txt`: две строки
`#`, затем номера. Блок печатает его sha256 и сверяет файл с ним. Номера
не заменяются, выборка заново не строится, сверх 500 ничего не берётся.

**До DJI** — ничего не меняя. Всё, что проверял W1, проверяется теми же
функциями, байт в байт (тест `test_w2_shares_the_checked_functions_of_w1`):

* чекаут пилота на пине, сборщик импортируется из него;
* приёмник `:5051`, порт 5050 отвергается;
* токен — из окружения службы площадки, ровно одна строка, проверен
  вызовом только на чтение;
* production: HEAD `3c5c8c5` — выпуск v1.23 от 08.10.2026
  (`docs/DEPLOYED.md`), уже после W1. Код DJI не изменился с `8df5683`,
  три службы работают. Другой HEAD — остановка до DJI;
* задачи production не идут, до ближайшей не меньше 130 минут;
  процессов сборщика нет, замок свободен;
* площадка такая, какой её оставил B1, прогон B1 открыт;
* миграций 60, каталог полей равен B0, включённых задач, пишущих в
  площадку, нет.

Плюс улики W1:

* журнал W1 кончается строками `STEP=PASS` и `DECISION=GO_TO_500`;
* `RUN SUMMARY` W1:
  * run id `sources:ids-file:20261008T101922Z`;
  * запрошено, посещено и карточек — по 50;
  * новых источников 200, ошибок приёма 0, всё принято;
  * код выхода 0 или 18;
* W1 начинал с отпечатка B0. В его `measure_canary.json`: карточек 50,
  подтверждено 17 (EXACT 14, IDENTIFIED 3), NO_KEY 6, NOT_IN_CATALOG 27;
* на площадке 200 ревизий прогона W1, все по вылетам канарейки. У всех 50
  карточка есть, и она из прогона W1;
* отпечаток площадки сейчас побайтно равен `fingerprint_post.json` W1,
  то есть после W1 на площадке ничего не менялось. Он сохраняется как
  `fingerprint_pre.json` W2;
* `measure --stage canary --before <отпечаток B0>` сейчас: все ворота
  проходят, цифры те же — 50/17/14/3/6/27.

Любое расхождение — остановка до DJI. Ничего не восстанавливается и не
исправляется.

**Сбор.** Тот же, что в W1, по `remaining_450_ids.txt`, в своей папке
`C:\VehicleSoft_CardPilot\w2\<время>` со своей очередью. Перед запуском
блок печатает:

* `W1_ALREADY_DONE=50 W2_REMAINING=450 TOTAL_MANIFEST=500`;
* sha256 обоих списков;
* пути сборщика, сессии, замка и очереди, приёмник площадки;
* время запуска и ближайшие запуски production.

Надзор тот же, что в W1:

* маркеры 403, 429, капчи, сессии и браузера;
* две отказанные карточки;
* три страницы подряд не открылись;
* пять вылетов подряд без карточки;
* 100 минут.

Отличия от W1:

* посещение любого вылета канарейки останавливает сбор;
* маркер сессии ловит и «expired during the run»;
* предел времени проверяется на каждой строке журнала, а не только в
  паузе от 5 с. Медленный DJI, который отвечает каждые несколько секунд,
  иначе прошёл бы мимо предела в окно production;
* строка `RUN SUMMARY` маркерами не проверяется. Счётчик
  `sources_v4=429` — не ответ DJI; `collector-stats` эту строку тоже
  пропускает. На 50 вылетах W1 счётчики до 429 не доходили, на 450 —
  доходят;
* сборщик останавливается при любом выходе из надзора: по правилу, по
  Ctrl+C, по ошибке. Если он кончил сам — нет. Строки, напечатанные им
  до остановки, дописываются в журнал прогона.

По W1 на вылет уходит 8,6 с, на 450 — около 65 минут. Предел — 100 минут,
окно до production — не меньше 130.

**Ворота сборщика** — те же, что в W1, на 450 вылетов, и ещё два условия:
в журнале нет ни одного вылета канарейки, `sources_skipped_known` равен 0.
Всё, с чем ворота сравнивают, записано до них в `collector_done.json`:

* код выхода, причина остановки, время;
* время запуска и конца сбора (UTC, конец с запасом 60 с). Записи
  production по 450 вылетам считаются только в этом окне: ночной сбор
  production после W2 законно пишет строки сентябрьских вылетов, и
  повторная проверка ворот не должна на нём вставать;
* sha256 сессии до и сразу после сбора;
* `max(id)` источников и кэша V4 до сбора;
* sha256 списка.

Ворота читают журнал сборщика с диска. Ещё одно их условие: не осталось
процессов, запущенных сборщиком (браузерный драйвер держит сессию
production). Пройдя ворота, блок пишет `fingerprint_after_collection.json`,
а последним — `collector_gate.json`.

**S2** — только после ворот:

1. Пишется `s2_started.txt`, служба площадки останавливается. Затем
   `check_db_lock`; каталог полей по-прежнему равен B0. Пуск службы при
   отказе — в `finally`: Ctrl+C посреди S2 площадку остановленной не
   оставляет.
2. `dji_area_recalc.py --dry-run`, затем `--apply`, с `--from 2026-09-01
   --to 2026-09-30` и ровно 450 `--flight-id`. Требуется: в периоде 450
   вылетов, и строк расчёта и привязки учтено по 450 (`new + unchanged +
   reactivated`).
   Расчёты и привязки 50 вылетов W1 не трогаются: S1 их уже пересчитал, и
   повторный пересчёт изменил бы результат W1. У вылета W1 входы могут
   устареть из-за данных W2 (соседи в цепочке, канал, оборудование), и
   dry-run по нему покажет `would_write`. S2 такие вылеты сознательно не
   пересчитывает.
3. `fingerprint_after_recalc.json` сравнивается с двумя отпечатками.
   С отпечатком после сбора: RAW, решения, миграции и источники те же,
   изменились только вылеты из 450 и ни одного из 50. С отпечатком после
   W1: то же, кроме источников.
4. `collector-stats` по журналам W1 и W2 вместе, с `--ids pilot_ids.txt`:
   посещено 500. Затем `measure --stage pilot --before <отпечаток B0>
   --collector-stats <W1+W2>`: все ворота проверяются против B0, а
   `attempted` равен 500, а не 450. Второй `measure --stage pilot
   --before <отпечаток после W1>` проверяет, что ревизии W1 побайтно на
   месте. Это нужно отдельно: ворота `measure` не видят изменения 50
   вылетов W1 внутри стадии `pilot`.
5. Перепись сентября и диагностика (§12). Пуск службы, миграций 60,
   площадка и production — как до блока, сессия не менялась. Последним
   пишется `s2_done.txt`.

**Повторная вставка.** Что делать, блок решает по файлам в папках `w2`:

| в папке прогона | состояние | при повторной вставке |
|---|---|---|
| `s2_done.txt` | `COMPLETE` | отказ: W2+S2 уже выполнен |
| `collector_gate.json` без `s2_done.txt` | `S2_PENDING` | только S2 этого прогона, без DJI и без токена. Условие: после ворот на площадке изменились только 450 (их пересчёт), а RAW, решения, миграции и источники те же. Если прерванный S2 этого прогона оставил службу площадки остановленной (есть `s2_started.txt`), она сначала запускается. Пересчёт повторяется: он идемпотентен, строки приходят как `unchanged` |
| `collector_done.json`: сборщик кончил сам (код 0 или 18), блок его не останавливал, ворот нет | `GATE_PENDING` | ворота проверяются заново по файлам прогона, без DJI; затем S2. Так кончается и закрытое во время ворот окно, и ворота, вставшие на условии, которое потом прошло (например, замок взял production) |
| `collector_done.json`: код 24, или сборщик кончил до первого вылета и в очереди пусто | `NOT_STARTED` | DJI не посещался — новый прогон |
| журнала сборщика нет | `NOT_STARTED` | то же |
| PowerShell, записанный в `supervisor.txt` (номер и время старта), ещё жив — на любом шаге до `s2_done.txt`: сбор, ворота, S2 | `RUNNING` | ничего не делается, служба площадки не трогается: прогон идёт в другом окне, ждать его строки `STEP=`. Окно, закончившее работу, свой `supervisor.txt` удаляет |
| иначе: блок остановил сбор, окно закрыто посреди обхода, иной код выхода | `COLLECTION_STOPPED` | отказ без сбора. Печатается, сколько посещено и сколько вылетов полные; полные продолжение пропустит, остальные посетит заново (без карточки, с несостоявшимся V4 или неоткрывшейся страницей тоже). Ещё печатаются код выхода, run id и сколько файлов в `pending` и `sent` очереди. Если сборщик этого прогона ещё работает (окно закрыли), печатается `COLLECTOR_STILL_RUNNING pid=…` с командой `taskkill`. Продолжение — отдельный шаг по решению владельца: та же папка и очередь |

Последняя строка состояния — `W2_STATE=` и одно из этих шести значений.
После начала обхода повторного сбора блок сам не запускает никогда.

**Что меняется на площадке.**

* В базе площадки:
  * источники 450 вылетов — через штатный приём;
  * их улики, расчёты и привязки;
  * кэш расшифрованных V4 (`dji_v4_summaries`), в том числе соседей. Это
    не улики и не привязки; новых строк печатается столько, сколько
    добавлено.
* Служба площадки останавливается на время S2 и снова запускается.
* Боты, задача `DjiAreaRefreshStaging` и окружение площадки не
  трогаются.

Production только читается. Исключение — `collector.lock` и
`collector.lock.owner`: их пишет протокол общего замка, как при любом
сборе. R этот блок не запускает.

**Что остаётся.**

* Журнал блока: `C:\VehicleSoft_CardPilot\card_pilot_w2_<время>.log`.
* Папка прогона `C:\VehicleSoft_CardPilot\w2\<время>`:
  * списки: `remaining_450_ids.txt`, копии `pilot_ids.txt` и
    `canary_ids.txt`;
  * состояние: `supervisor.txt`, `collector_done.json`, `s2_started.txt`;
  * сбор: `outbox\`, `collector_stdout.log`, `collector_stderr.log`,
    `collector_exit.txt`, `collector_for_stats*.log`,
    `collector_stats_w2.json`, `collector_stats_pilot.json`;
  * отпечатки: `fingerprint_pre.json`,
    `fingerprint_after_collection.json`, `fingerprint_after_recalc.json`;
  * пересчёт: `recalc_dry-run.*`, `recalc_apply.*`;
  * измерения: `w1_recheck\`, `measure\` (`measure_pilot.json`,
    `results_pilot.csv`), `measure_since_w1\`, `census_after.json`,
    `diagnostics.txt`;
  * прочее: `drift_*.log`, `w2_check.py` (только чтение);
  * отметки `collector_gate.json` и `s2_done.txt`.

**Итог** — раздел 5 вывода:

* `COLLECTION` — посещено из 500, карточек, без карточки, ошибки страниц,
  время W2, среднее и медиана секунд на вылет;
* `STATUSES`, `STOP_MARKERS`;
* `SOURCES_SAVED` — W1 200 + W2;
* `OUTCOME` — EXACT, IDENTIFIED, CONFIRMED, NO_KEY, NOT_IN_CATALOG,
  NO_CARD, OTHER_UNRESOLVED, NO_CALC;
* `CASES`;
* `CONFIRMED_RATE` — на 500 и среди получивших карточку, с 95 %
  интервалом Уилсона;
* `COMPARE` — W1 17/50, W2 x/450 и все вместе;
* `PROJECTION (not a fact)` — постстратифицированная оценка на когорту
  NO_CARD и проекция на сентябрь;
* `BY_UNIT`, `BY_WEEK`, строки `DIAG`;
* `PILOT_STATUS=COMPLETE`, `W2_STATE`, `STEP`.

Решения дальше блок не предлагает. Следующие шаги — отчёт и R, отдельно.

**Проверки** — в `tests/test_dji_card_coverage_blocks.py`:

* `W2Text` — текст файла;
* `W2InPowerShell` — на подставном SRV-YOQSH, после настоящего прогона
  блока W1 в нём же. Замороженных вылетов в стенде 60 = 50 + 10, поэтому
  там `W2_REMAINING=10`.

В CI они идут и под Windows PowerShell 5.1.

### R — SRV-YOQSH: возврат площадки

Выдаётся только после разбора: по окончании пилота или если B1 встал с
`STAGING_CHANGED=yes`. Пока сборщик пилота может слать данные на
площадку, R не запускается. Очереди пилота (`C:\VehicleSoft_CardPilot\w1\…\outbox`,
`C:\VehicleSoft_CardPilot\w2\…\outbox`) после R не досылаются: площадка уже не та.

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
* Задача `DjiAreaRefreshStaging` — после базы, кода, окружения и служб, до
  `returned.txt`, по `task_before.txt`: сначала папка, отпечатки действия и
  триггеров и sha256 обёртки должны совпасть с записанными (иначе отказ,
  задача остаётся как есть — отключённой); затем `Enable-ScheduledTask`,
  если до B1 она была включена (была отключена — остаётся отключённой);
  затем `Enabled` и sha256 выгруженного XML — как до B1 (XML не совпал —
  задача отключается обратно, отказ). Вручную задача не запускается
  никогда. Повтор R сходится: включённая задача второй раз не включается.
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
  function Get-TextHash([string]$text) { $h = [System.Security.Cryptography.SHA256]::Create(); -join ($h.ComputeHash([System.Text.Encoding]::UTF8.GetBytes($text)) | ForEach-Object { $_.ToString('x2') }) }
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
  function Get-IsoState([string]$name, [string]$wrapper) {
    $all = @(Get-ScheduledTask -TaskName $name -ErrorAction SilentlyContinue)
    if ($all.Count -ne 1) { throw "STEP FAILED: $($all.Count) scheduled tasks are named $name, expected exactly one -- send this output" }
    $t = $all[0]
    $actionLines = @(@($t.Actions) | ForEach-Object { [string]$_.Execute + '|' + [string]$_.Arguments + '|' + [string]$_.WorkingDirectory })
    $triggerLines = @(@($t.Triggers) | ForEach-Object { Get-CimLine $_ })
    $wrapperSha = 'missing'
    if (Test-Path -LiteralPath $wrapper) { $wrapperSha = (Get-FileHash -LiteralPath $wrapper -Algorithm SHA256).Hash.ToLower() }
    [pscustomobject]@{ Path = [string]$t.TaskPath; State = [string]$t.State; Enabled = [string]$t.Settings.Enabled; ActionFp = (Get-TextHash ($actionLines -join "`n")); TriggerFp = (Get-TextHash ($triggerLines -join "`n")); XmlSha = (Get-TextHash ([string](Export-ScheduledTask -TaskName $t.TaskName -TaskPath $t.TaskPath))); WrapperSha = $wrapperSha }
  }
  function Get-IsoProcesses([string]$name) { @(Get-CimInstance -ClassName Win32_Process | Where-Object { $c = [string]$_.CommandLine; ($c -match [regex]::Escape($name)) -or (($c -match 'dji_area_daily|dji_area_recalc|drone_collector') -and ($c -match 'transport-report-staging')) }) }
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
    $isoSaved = $null
    $taskFile = Join-Path $run 'task_before.txt'
    if (Test-Path -LiteralPath $taskFile) {
      $isoSaved = @{}
      foreach ($line in @(Get-Content -LiteralPath $taskFile | Where-Object { $_ -match '^[a-z_]+=' })) { $kv = $line.Split('=', 2); $isoSaved[$kv[0]] = $kv[1] }
      foreach ($k in @('name', 'path', 'enabled', 'action', 'trigger', 'xml', 'wrapper', 'wrapper_sha')) { if (-not $isoSaved.ContainsKey($k)) { throw "STEP FAILED: task_before.txt has no $k -- send this output" } }
      if (@('True', 'False') -notcontains $isoSaved['enabled']) { throw "STEP FAILED: task_before.txt says enabled=$($isoSaved['enabled'])" }
      $pre = Get-IsoState $isoSaved['name'] $isoSaved['wrapper']
      Write-Output ("TASK_BEFORE_B1 " + $isoSaved['name'] + " path=" + $isoSaved['path'] + " enabled=" + $isoSaved['enabled'] + "; now enabled=" + $pre.Enabled + " state=" + $pre.State)
    } else {
      Write-Output 'TASK_BEFORE_B1=none recorded (B1 stopped before it saved the task state)'
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

    Write-Output '== 7. The staging refresh task as it was before B1 (it is not started)'
    if ($isoSaved) {
      $cur = Get-IsoState $isoSaved['name'] $isoSaved['wrapper']
      if ($cur.Path -ne $isoSaved['path']) { throw "STEP FAILED: $($isoSaved['name']) is in folder $($cur.Path), before B1 it was $($isoSaved['path'])" }
      foreach ($c in @(@('action fingerprint', $cur.ActionFp, $isoSaved['action']), @('trigger fingerprint', $cur.TriggerFp, $isoSaved['trigger']), @('wrapper sha256', $cur.WrapperSha, $isoSaved['wrapper_sha']))) {
        if ($c[1] -ne $c[2]) { throw "STEP FAILED: $($isoSaved['name']) $($c[0]) is $($c[1]), before B1 it was $($c[2]); the task is left as it is (enabled=$($cur.Enabled)) -- send this output" }
      }
      if (($isoSaved['enabled'] -eq 'True') -and ($cur.Enabled -ne 'True')) { Enable-ScheduledTask -TaskName $isoSaved['name'] -TaskPath $isoSaved['path'] | Out-Null }
      if (($isoSaved['enabled'] -eq 'False') -and ($cur.Enabled -ne 'False')) { Disable-ScheduledTask -TaskName $isoSaved['name'] -TaskPath $isoSaved['path'] | Out-Null }
      $cur = Get-IsoState $isoSaved['name'] $isoSaved['wrapper']
      if ($cur.Enabled -ne $isoSaved['enabled']) { throw "STEP FAILED: $($isoSaved['name']) Enabled is $($cur.Enabled), before B1 it was $($isoSaved['enabled'])" }
      if ($cur.XmlSha -ne $isoSaved['xml']) {
        if ($cur.Enabled -eq 'True') { Disable-ScheduledTask -TaskName $isoSaved['name'] -TaskPath $isoSaved['path'] | Out-Null }
        throw "STEP FAILED: $($isoSaved['name']) task XML sha256 is $($cur.XmlSha), before B1 it was $($isoSaved['xml']); the task is left disabled -- send this output"
      }
      Write-Output ("TASK_RESTORED " + $isoSaved['name'] + " enabled=" + $cur.Enabled + " state=" + $cur.State + " (action, triggers, task XML and wrapper equal the state before B1; not started)")
    } else {
      Write-Output 'TASK_RESTORED=not needed'
    }

    Write-Output '== 8. Production -- this block does not touch it'
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
`not touched by B1`), `TASK_RESTORED DjiAreaRefreshStaging enabled=True …`, три строки
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
