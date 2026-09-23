# Контроль площади DJI — ранбук оператора и владельца

DJI-AREA-PRODUCTIONIZATION-001. Штатная функция Vehicle Soft поверх доказанного
механизма C-фантомов: DJI показал → программа доказанно исключила → осталось →
что ещё не решено → почему исключена каждая запись → ссылка на вылет в DJI.

**Production этим ранбуком не затрагивается.** Ни один блок ниже не называет ни
каталог, ни службу, ни порт production. Задача планировщика `DroneCollectorDaily`
не меняется. Таблица «Открытые пункты» в `docs/RELEASE_GATE.md` не пуста, поэтому
деплой на production закрыт независимо от результата этой ветки.

## Что неизменно

- `drone_flights.area_ha` — RAW DJI. Слой площади его не читает и не пишет.
  Исправленная площадь живёт отдельной величиной в `dji_area_calculations`.
- `billable_area_m2` остаётся NULL. Экран — контроль, а не счёт.
- Правило кандидата заморожено: `structural-retained-screen-frozen-1`.
  Алгоритм — `impl-4`.
- Автоматически исправляется только цель цепочки **C** и только когда счётчик V4
  это подтвердил. Мостик **B** — настоящее ручное внесение
  (DJI-AREA-BRIDGE-B-VALIDATION-001) и не исключается никогда. Кандидат без V4 и
  запись «на проверке» остаются по RAW.

## Экран и книга

`/drones/area-control` — прямой пункт меню «Дроны» → «Контроль площади DJI»
(с DRONE-AREA-CONTROL-V2-MEGA; плитка в «Отчётах» тоже осталась), книга —
кнопкой «Excel» на экране, файл `drone_area_control_` + период + `.xlsx` (при
нестандартном времени к периоду добавляется `_ЧЧММ-ЧЧММ`).
Технический экран
`/drones/area-evidence` остался прежним: он по своему контракту не показывает ни
одного производного итога, поэтому продуктовый экран — отдельный.

| Число | Что значит |
|---|---|
| DJI RAW, га | площадь, как её сообщил DJI; программой не меняется |
| Подтверждённо исключено, га | завышение, которое подтвердил счётчик V4: RAW минус проверенный прирост. В скобках — число записей |
| Площадь после подтверждённых корректировок, га | RAW минус только доказанное. Всё нерешённое сидит внутри по RAW |
| Ожидает доказательства / V4, га | сильные кандидаты, по которым V4 ещё не получен (либо DJI его не хранит). Учтены по RAW |
| Требует проверки, га | доказательство есть, но безопасного автоматического решения нет. Учтены по RAW |

Арифметика одна: **RAW − исключено = площадь после корректировок**; сверху
экрана она написана формулой «площадь по данным DJI − доказанное завышение =
площадь, принятая программой». Числа — ЭФФЕКТИВНЫЕ: действующее решение
администратора (раздел «Решение администратора по спорной записи») стоит выше
автомата; «принято автоматически» показано рядом отдельной колонкой. Дерево
Дрон → День → Вылет складывается в итог на каждом уровне; обычные вылеты дня
свёрнуты в строку «остальные» с их суммой. Строка статуса говорит прямо, все
ли записи периода разрешены и входит ли спорная площадь в принятое.

Период — «Дата/время с» и «Дата/время по» (UTC+5, минуты, по умолчанию 00:00 и
23:59; «по 23:59» включает всю последнюю минуту).

Дерево показывает цепочку `A → B → C`: все три — ссылки на записи DJI, у
каждой местное время. `A` — базовая
Auto-работа, `C` — запись, повторившая её площадь, `B` — промежуточный ручной
участок с пометкой «не корректируется». Частичный повтор не обнуляется:
принимается подтверждённый V4 прирост.

Книга: листы «Сводка», «По_дронам», «Корректировки», «Требует_проверки» (как
были, первыми), затем «По_дням», «Реестр» (все записи с корректировкой,
ожиданием или решением: A/B/C с временем и ссылками, автоматический вердикт,
решение, кто и когда) и «История_решений». Период, время и дрон в книге — те
же, что на экране, итоги — те же эффективные; вкладка экрана («Все»,
«Подтверждённые», …) данных не фильтрует, поэтому книга несёт полный реестр
отобранного периода и пишет в «Сводке», какая вкладка была открыта.
Выгрузка вылетов `drones_flights_*.xlsx` и техническая книга не изменились.

## Ежедневный цикл

Один вход: `tools\dji_area_daily.py`. Четыре шага, порядок задан зависимостями.

| Шаг | Что делает | DJI |
|---|---|---|
| FLIGHTS | обход вылетов окна ± сутки; вместе с вылетами уходит их списочное доказательство | да, только список |
| MANIFEST | `POST /drones/api/area_capture_manifest`: кому нужен адресный V4 | нет |
| SOURCES | `--sources --ids-file`: источники ТОЛЬКО названных вылетов | да, адресно |
| RECALC | пересчёт того же окна поверх сохранённых улик | нет |

Манифест называет (а) **всех** кандидатов замороженного экрана без V4 и (б)
малую детерминированную контрольную выборку: до 3 записей в день по признакам
риска и до 6 случайных, по одной на борт по кругу. Кандидаты имеют приоритет над
лимитом: лимит срезает только контрольные записи, а если кандидатов больше
лимита, ответ помечается `over_cap` и цикл останавливается. Запись, по которой
DJI не хранит V4, видна отдельным состоянием `NO_V4_AT_SOURCE` и повторно не
запрашивается. Выборка детерминирована по дню: повторный вызов не тянет новую
порцию того же дня.

Объём на сентябрьских данных (01–18.09.2026, 4623 вылета):

| Окно | Записей в манифесте |
|---|---|
| один непосещённый день | 6 … 42, медиана 19 |
| три непосещённых дня подряд | 37 … 106, медиана 61 |
| сбор по всему парку за день | около 270 |

В установившемся режиме в трёхдневном окне непосещён только свежий день,
поэтому суточный объём — первая строка. `--stop-above 50` — порог именно
суточного объёма.

Окно по умолчанию — три отчётных дня UTC+5: цепочка может пересечь полночь, V4
приходит с задержкой, и завтрашний прогон обязан лечить вчерашнюю запись.
Результат пересчёта от окна не зависит: свидетельство канала и идентичность
группы берутся по полным календарным месяцам.

Повторный запуск безопасен: уже захваченный вылет манифест не называет,
`--sources` уже поставленное в очередь не открывает, пересчёт на том же входе
отвечает `unchanged`.

### Коды возврата цикла

| Код | Что значит | Что делать |
|---|---|---|
| 0 | цикл выполнен: итог `SUCCESS`, либо `SUCCESS_WITH_WARNINGS` — без V4 остался только контрольный вылет, ни одна корректировка не заблокирована | ничего; предупреждение `CONTROL_EVIDENCE_MISSING` видно в журнале прогонов и в `last_cycle.json` |
| 1 | ошибка аргументов | исправить команду |
| 2 | база не найдена | проверить путь; файл не создаётся |
| 3 | шаг упал; после FLIGHTS и MANIFEST цикл остановлен, после SOURCES пересчёт по уже сохранённым доказательствам выполнен | строка `step ... exit N` называет шаг; код 2 шага FLIGHTS — сессия DJI истекла; код 24 шага — идёт другой сбор, сборщик занят |
| 4 | манифест больше `--stop-above`; **к DJI за источниками не обращались** | см. «Манифест слишком велик» |
| 5 | кандидат остался без доказательства V4 после SOURCES (или повторный манифест VERIFY не ответил); пересчёт выполнен | запустить ещё раз: доберёт только недостающее; вылеты названы в `last_cycle.json`, ключ `evidence_misses.candidates` |
| 6 | пересчёт что-то записал при `--expect-unchanged` | прислать `area_recalc.json`: вход изменился между прогонами |
| 7 | другой цикл площади держит блокировку цикла; ни один шаг не запускался | подождать и повторить; строка `BUSY:` называет владельца блокировки |

Код 18 сборщика («собрано не всё») сам по себе больше не код 5: цикл
спрашивает манифест ещё раз (шаг VERIFY) и различает потерю кандидата — провал —
и потерю контрольного вылета — предупреждение. Контрольный вылет, срезанный
лимитом манифеста ещё до сбора, потерей не считается.

### Две топологии

На production сборщик и база стоят на одном сервере — цикл идёт одной командой.
На площадке сборщик живёт на рабочей машине (там сохранена сессия DJI), а база —
на SRV-YOQSH. Поэтому рабочая машина выполняет шаги 1–3 (`--skip-recalc`), а
сервер — шаг 4 (`--recalc-only`). Окно в обеих половинах считает один и тот же
код.

## Как понять, что сбор сломан

| Признак | Где видно | Что это значит |
|---|---|---|
| «Ожидает доказательства / V4» растёт день ото дня | экран, фильтр «Ожидает V4» | шаг SOURCES не доезжает: смотреть код возврата цикла |
| в реестре «Ожидает V4» есть записи старше трёх дней | экран | окно их уже не лечит; повторить день адресно (ниже) |
| цикл вернул 3 на шаге FLIGHTS с кодом 2 | консоль / журнал задачи | сессия DJI истекла: сохранить заново (блок 0 в `docs/DJI_AREA_SIMPLIFY_001_RUNBOOK.md`) |
| цикл возвращает 5 несколько дней подряд | консоль / журнал прогонов | КАНДИДАТ стабильно не даёт V4: вылеты — в `last_cycle.json`, `evidence_misses.candidates`; прислать `collector.log` |
| панель «Обновить данные DJI»: «Успешно, с предупреждениями» | экран | без V4 остался только контрольный вылет; ничего не делать |
| панель: «Прервано» или «Запуск не состоялся» | экран | см. «Обновить данные DJI из интерфейса» |
| цикл вернул 7 | консоль | идёт другой цикл площади (кнопка, расписание или backfill); повторить позже |
| цикл вернул 4 | консоль | см. «Манифест слишком велик» |
| за вчера на экране 0 записей, а дроны летали | экран | не сработал шаг FLIGHTS; журнал синхронизации дронов покажет то же |
| число «DJI не хранит V4» в манифесте резко выросло | `area_manifest.json`, `counts.candidates_no_v4_at_source` | у DJI изменилась выдача V4; автоматически ничего не обнуляется, записи остаются по RAW |

### Манифест слишком велик

Код 4 — это остановка ДО адресного сбора источников, а не сбой. Обход списка
вылетов (шаг FLIGHTS) к этому моменту уже выполнен: без него манифесту не о чем
судить, и это тот же обход, что и в обычном ночном сборе. Причина кода 4 почти
всегда одна:
цикл несколько дней не запускался, и в окне оказалось больше одного
непосещённого дня (три таких дня законно дают 37–106 записей). Лечится прогоном
по одному дню, от старого к новому, — каждый укладывается в порог. Если в порог
не укладывается **один** день, это неожиданность: прислать `area_manifest.json`
и не поднимать `--stop-above`, пока причина не названа.

## Как повторить последние три дня

Цикл идемпотентен, поэтому повтор — это просто ещё один запуск. На хосте, где
сборщик и база стоят вместе:

```powershell
& {
$ErrorActionPreference = 'Continue'
$py = 'C:\Program Files\Python314\python.exe'
if (-not (Test-Path -LiteralPath $py)) { throw "STEP FAILED: python not found: $py" }
if (-not (Test-Path -LiteralPath 'tools\dji_area_daily.py')) { throw "STEP FAILED: run this from the repository root of the host that holds the database" }
if (-not (Test-Path -LiteralPath 'instance\transport.db')) { throw "STEP FAILED: instance\transport.db not found here" }
& $py tools\dji_area_daily.py --db instance\transport.db --days 3
Write-Host "DAILY CYCLE EXIT CODE: $LASTEXITCODE"
}
```

Один конкретный день — `--from 2026-09-17 --to 2026-09-17` вместо `--days 3`
(дата в примере настоящая, из принятого сентябрьского периода). Только пересчёт,
без единого обращения к DJI и к сборщику, — `--recalc-only`.

## Обновить данные DJI из интерфейса

DRONE-AREA-CONTROL-V2-MEGA. Кнопка «Обновить данные DJI» стоит на экранах
«Контроль площади DJI», «Вылеты» и «Источники». Видна пользователю с правом
правки; нажатие — POST с CSRF и подтверждением.

**Что происходит.** Запрос ставит ручной прогон ежедневного цикла в очередь
(`drone_area_cycle_runs`, «Ожидает запуска») и будит исполнителя; страница
возвращается сразу. Исполнитель — тот же `tools\dji_area_daily.py` с флагом
`--run-queued`: берёт прогон из очереди и выполняет обычный цикл FLIGHTS →
MANIFEST → SOURCES → (VERIFY) → RECALC по тому же окну, что и по расписанию
(три отчётных дня). Контуры полей не скачиваются. Пока прогон идёт, панель
раз в 15 секунд спрашивает состояние и перезагружает страницу по окончании.

**Как будится исполнитель** — настройка окружения СЛУЖБЫ приложения:

| Переменная | Значение | Где |
|---|---|---|
| `DJI_REFRESH_LAUNCHER` | `schtasks` — запустить задачу планировщика; `subprocess` — отсоединённый процесс; пусто — кнопка отключена с объяснением | production: `schtasks` |
| `DJI_REFRESH_TASK_NAME` | имя задачи планировщика (буквы, цифры, `_ . -` и пробел) | только для `schtasks` |
| `DJI_REFRESH_PYTHON` | интерпретатор исполнителя для `subprocess` (по умолчанию — интерпретатор службы) | только для `subprocess` |
| `DJI_COLLECTOR_PYTHON` | интерпретатор venv сборщика, передаётся как `--collector-python` | только для `subprocess` |

[REASON]: на production — задача планировщика, а не дочерний процесс службы.
Служба работает как LocalSystem, а Chromium сборщика и сохранённая сессия DJI
лежат в профиле пользователя, под которым идёт ночной сбор вылетов; ребёнок
службы не нашёл бы ни того, ни другого. Кнопка выполняет только
`schtasks /Run /TN <имя из настройки>` — ни одного аргумента из запроса.

Задача планировщика для кнопки (создаёт владелец, ОТДЕЛЬНОЙ задачей — не
строкой внутри ночного сбора):

```text
Имя:         DjiAreaRefresh (то же значение -- в DJI_REFRESH_TASK_NAME)
Пользователь: тот же, под которым идёт ночной сбор вылетов;
             «Выполнять вне зависимости от регистрации пользователя»
Триггер:     нет (запускается кнопкой)
Действие:    <корень>\drone_collector\.venv\Scripts\python.exe
             tools\dji_area_daily.py --db instance\transport.db --run-queued
Рабочая папка: <корень> (корень репозитория production)
Параметры:   «Если задача уже выполняется -- не запускать новый экземпляр»
```

Весь цикл при этом идёт интерпретатором venv сборщика: пересчёт
(`tools\dji_area_recalc.py`) и журнал используют только стандартную
библиотеку.

**Состояния панели.**

| Состояние | Что значит | Что делать |
|---|---|---|
| Не запускалось | журнал пуст | — |
| Ожидает запуска | в очереди; «ждёт окончания другого сбора», если цикл держит блокировку | ждать |
| Выполняется | идёт шаг, названный словами | ждать |
| Успешно | цикл выполнен | — |
| Успешно, с предупреждениями | без V4 остались только контрольные вылеты | ничего; следующий прогон доберёт |
| Ошибка | шаг, код и сообщение (очищено от секретов) | по коду цикла, раздел «Коды возврата цикла» |
| Не запущено: шёл другой сбор | исполнитель не дождался блокировки цикла | нажать позже |
| Прервано | процесс умер, не закрыв запись (перезапуск службы, сбой) | нажать ещё раз; повтор безопасен |
| Запуск не состоялся | задача не поднялась, процесс не стартовал или очередь никто не взял 15 минут | проверить задачу планировщика и переменные службы |

Вывод исполнителя в режиме `subprocess` — `instance\dji_refresh_logs\run_<N>.log`;
в режиме `schtasks` — журнал задачи планировщика и `drone_collector\logs\collector.log`.

**Один сбор за раз.** Три замка: частичный UNIQUE-индекс журнала (второе
нажатие прикрепляется к идущему прогону), блокировка цикла
`instance\dji_area_cycle.lock` (её берут ручной прогон, прогон по расписанию и
backfill) и блокировка сборщика `drone_collector\data\collector.lock` (её берёт
каждый запуск сборщика, включая ночной; ждёт до `DJI_COLLECTOR_LOCK_WAIT_S`,
по умолчанию 1800 с, затем код 24). Блокировки — ОС-уровня: процесс, убитый
службой или перезагрузкой, отпускает их сам. Файлы `*.lock` и `*.lock.owner`
удалять руками не нужно и не следует: владелец замка определяется попыткой
взять замок, а не наличием файла.

## Как проверить одну корректировку

1. Открыть `/drones/area-control`, вкладка «Подтверждённые корректировки»,
   раскрыть дрон и день.
2. В строке — цепочка `A → B → C` с временем каждого звена. Открыть ссылки `A` и
   `C` в DJI: тот же участок, у `C` площадь повторяет `A`, а нового
   обработанного следа нет.
3. Колонки «DJI RAW, га», «Принято, га», «Исключено, га»: исключено = RAW −
   принято. При частичном повторе «Принято» больше нуля — это подтверждённый V4
   прирост.
4. Колонка «Доказательство» обязана говорить «V4 получен, счётчик проверен».
   Иного основания для автоматической корректировки нет.
5. Технические подробности той же записи (счётчик, окно, флаги) — на
   `/drones/area-evidence`, поиск по дате и дрону.

Запись без цепочки с объяснением «Завышение подтверждено V4 контрольной
проверкой» — это находка контрольной выборки: правило её не назвало, а счётчик
показал завышение. Так выглядит известный пропуск правила; в сентябре он один.

## Решение администратора по спорной записи

Автомат ничего не вычитает без доказательства, поэтому спорные записи
(«Требует решения», «Ожидает доказательства») остаются по RAW. Решает
администратор — отдельным append-only слоем `drone_area_decisions`;
автоматический расчёт при этом не меняется ни в одной колонке.

1. В дереве у записи ссылка «Решить» (видна только администратору) — карточка
   записи `/drones/area-control/flight/<id>`: автомат, итог, цепочка с
   временем, история решений.
2. Выбрать решение, написать причину (обязательно), отметить подтверждение:

| Решение | Принято | Исключено | После решения |
|---|---|---|---|
| Подтвердить полный фантом | 0 | весь RAW | подтверждённая корректировка |
| Принять автоматический результат | как у автомата | как у автомата | разрешена |
| Оставить площадь DJI RAW | RAW | 0 | разрешена |
| Нужны дополнительные доказательства | RAW | 0 | остаётся открытой |
| Отменить решение | — | — | снова автомат; история сохраняется |

3. Итоги экрана, дерево и книга пересчитываются сразу; в истории — кто, когда,
   что и почему. Исправление и отмена — новая строка истории, прежняя не
   стирается.

Переопределить ДОКАЗАННУЮ корректировку (счётчик V4) можно только из свёрнутой
секции карточки «Переопределить доказанный автоматический результат» — с
дополнительной отметкой. Решение «принять автоматический результат» перестаёт
действовать, если пересчёт дал новый результат; остальные решения действуют и
получают пометку «расчёт изменился после решения — проверьте».

## Как отключить цикл

Эта ветка **не ставит ни одного расписания**: цикл запускается только командой
человека или кнопкой «Обновить данные DJI», поэтому «отключить» сегодня значит
«не запускать». Кнопку отключает пустая `DJI_REFRESH_LAUNCHER` в окружении
службы (кнопка тогда честно говорит «не настроено») либо
`Disable-ScheduledTask` на задаче кнопки. Экран при этом
продолжает работать по уже сохранённым расчётам; новые кандидаты просто
остаются в «Ожидает V4» и учитываются по RAW.

Когда расписание появится (отдельное решение владельца после QA площадки), оно
обязано быть ОТДЕЛЬНОЙ задачей планировщика, а не строкой внутри
`DroneCollectorDaily`, — чтобы отключаться одной командой
`Disable-ScheduledTask`, не задевая ночной сбор вылетов.

## Откат

**Код.** DJI-AREA-PRODUCTIONIZATION-001 новых таблиц и колонок не вводил.
DRONE-AREA-CONTROL-V2-MEGA вводит две новые таблицы миграцией
`migrate_drone_area_control_v2_001.py` (`drone_area_decisions`,
`drone_area_cycle_runs`); откат кода их трогать не требует — после
`git revert` они просто не читаются, решения перестают действовать, но не
теряются. Откат площадки — вернуть прежнюю ревизию блоком ниже; откат
`main` — `git revert` мерж-коммита PR (не `reset`).

```powershell
& {
$ErrorActionPreference = 'Continue'
$expectedHost = 'srv-yoqsh'
$staging = 'C:\transport-report-staging'
$service = 'TransportReportStaging'
$memo    = 'C:\VehicleSoft_Area_Staging\before_head.txt'
if ((hostname) -ne $expectedHost) { throw "STEP FAILED: host is $(hostname), expected $expectedHost" }
if ($staging -notlike '*transport-report-staging*') { throw "STEP FAILED: refusing a root that is not the staging checkout" }
if ($service -ne 'TransportReportStaging') { throw "STEP FAILED: refusing a service that is not the staging service" }
if (-not (Test-Path -LiteralPath $memo)) { throw "STEP FAILED: $memo not found -- block A never reached the checkout, there is nothing to roll back" }
$back = (Get-Content -LiteralPath $memo -Raw).Trim()
if ($back -notmatch '^[0-9a-f]{40}$') { throw "STEP FAILED: $memo does not hold a commit hash" }
try {
  Stop-Service -Name $service
  $deadline = (Get-Date).AddSeconds(90)
  while ((Get-Service -Name $service).Status -ne 'Stopped') {
    if ((Get-Date) -gt $deadline) { throw "STEP FAILED: service did not reach Stopped within 90s" }
    Start-Sleep -Seconds 2
  }
  & git -C $staging checkout --quiet --detach $back
  if ($LASTEXITCODE -ne 0) { throw "STEP FAILED: git checkout $back exit $LASTEXITCODE" }
  Write-Host ("ROLLED BACK TO: " + (& git -C $staging rev-parse HEAD))
} finally {
  Restart-Service -Name $service
  $deadline2 = (Get-Date).AddSeconds(90)
  while ((Get-Service -Name $service).Status -ne 'Running') {
    if ((Get-Date) -gt $deadline2) { throw "STEP FAILED: service did not reach Running within 90s -- START IT BY HAND" }
    Start-Sleep -Seconds 2
  }
  Write-Host ("SERVICE RUNNING: " + (Get-Service -Name $service).Status)
}
}
```

**Данные.** Блок A базу не меняет вовсе. Блоки W и S только добавляют: вылеты,
неизменяемые источники, append-only строки расчёта. Прежний код эти строки
терпит — таблицы существуют с 08.09.2026, — поэтому откат кода отката данных не
требует. Если вернуть нужно именно данные, источник — копия, путь к которой блок
S печатает строкой `BACKUP:` (вместе с `-wal` и `-shm`); копируется при
остановленной службе. Готового блока для этого нет намеренно: в нём был бы
путь-заполнитель. Таблицы V2 удаляются только после отката кода и только
решением владельца (решения — аудит): порядок — в докстринге миграции.

## Квалификация площадки: пройдена 21.09.2026

Блоки **A → W → S исполнены владельцем**, все три PASS. Ревизия
`ae42e23d40e38089329f90debd99cc6add98cc19`, тег
`dji-area-productionization-001-rc1`, отпечаток кода `7c01a131…`. Production не
затрагивался; служба `TransportReportStaging` поднята.

**Блок A — PASS.** Приёмка сентября 01–18.09.2026 на БАЗЕ ПЛОЩАДКИ совпала с
оракулом до последней цифры: 4623 записи, `raw_missing` 0, RAW 4413,2825 га,
исключено 167,8088 га (230 записей), после корректировок 4245,4737 га, ждёт V4
0, на проверке 10,3631 га (13), кандидатов 233, цепочек 233, доказано правилом
229, только контрольной выборкой 1, `APPLICATION_WITH_FLAT_COUNTER` на проверке
4, мостиков среди корректировок 0, сухой пересчёт `unchanged=4623`, SHA базы не
изменился. Резервная копия —
`backups\dji-area\transport.db.pre_area_control_20260921_145226.bak`. Экран
осмотрен владельцем глазами: пять чисел и таблица по дронам на месте, цепочка
A → B → C раскрывается, у мостика стоит «не корректируется», REVIEW остаётся
по RAW.

**Блок W — PASS.** Живой день — 20.09.2026. Обход списка: 620 вылетов за
19–21.09, из них 557 новых для площадки, 63 дубликата, 0 нераспознанных, 0
ошибок. Манифест за 20.09: **22 идентификатора = 15 кандидатов + 7
контрольных**, `NO_V4_AT_SOURCE` 0, `over_cap` false — внутри предсказанного
суточного диапазона 6–42 и ниже порога 50. Посещены все 22, и каждый отдал
полный набор: карточка, маршрут, airlines, **V4**. Счётчики сборщика:
`sources_requested=22 sources_visited=22 sources_full=22 sources_v4=22
sources_no_v4=0 sources_v4_failed=0 sources_page_errors=0 sources_rejected=0`.
Отправлено 88 конвертов источников (22 × 4), принято 88 новых, 0 ошибок приёма,
код возврата 0. Сбора по всему парку не было.

**Блок S — PASS.** Окно 19–21.09.2026. Первый пересчёт: 620 вылетов, RAW
5 976 919 м² (597,6919 га), кандидатов экрана 24, V4 в наличии 22, `CERTIFIED`
15, `COUNTER_FLAT_RAW_OVERSTATED` 15, записано `calc new=620` и
`field new=620`. Второй пересчёт: `calc_writes unchanged=620`,
`field_writes unchanged=620`, ворота идемпотентности PASS. Сторож RAW: снимок
15 664 вылета, RAW сохранён у 15 664, изменён у 0, новых вылетов 557
(разрешено), `billable` не NULL 0 — **RAW UNTOUCHED**. Повторная приёмка
сентября после живого смоука — PASS ровно с теми же числами оракула: живой цикл
не сдвинул уже принятый период. `STEP=PASS`.

Числа блока S читаются так: адресный сбор шёл за ОДИН день (20.09), поэтому V4
получили 15 кандидатов этого дня — и все 15 подтвердились как завышение. Ещё 9
кандидатов окна приходятся на 19 и 21 сентября, V4 по ним не запрашивался, и они
остались по RAW в графе «Ожидает доказательства / V4». Завтрашний прогон цикла
берёт их сам: окно скользит.

### Находка квалификации: унаследованный `DRONE_API_TOKEN`

Единственная остановка за всю квалификацию, и она не в алгоритме и не в
продукте. В консоли рабочей машины остался `DRONE_API_TOKEN` от прежней работы.
Приоритет окружения процесса над `.env` — сознательное решение
`drone_collector/config.py`, поэтому сборщик взял устаревшее значение и получил
401. Владелец убрал переменную из процесса руками, после чего сборщик прочитал
верный токен из скопированного `.env`, и блок прошёл целиком.

Приоритет не меняется. Исправлен только блок W: он сам сохраняет унаследованное
значение, убирает переменную, проверяет, что она убрана, и возвращает её в
`finally`. Ни одно значение не печатается. Свойство держится тестом
`tools/test_dji_area_production_runbook.py` в CI. Квалификация 21.09.2026 шла на
тексте блока ДО этой правки — ручной обход владельца ей эквивалентен.

## Площадка: три блока

Порядок: **A** (сервер) → **W** (рабочая машина) → **S** (сервер). Каждый блок
вставляется целиком, ничего не редактируя, и обёрнут в `& { ... }`: вставленные
в консоль строки PowerShell исполняет по одной, и `throw` без обёртки следующие
строки не останавливает.

Все три блока доказывают ревизию до первого действия: аннотированный тег
`dji-area-productionization-001-rc1` плюс отпечаток кода — та же пара, что в
`docs/DJI_AREA_SIMPLIFY_001_RUNBOOK.md`.

Пин называет ревизию, которая прошла квалификацию. Закрывающие коммиты — раздел
выше, правка блока W и тест к ней — легли ПОСЛЕ этого тега, поэтому перед любым
следующим прогоном блоков пин обновляется на тег той ревизии, которую и будут
запускать. Иначе откажет собственный гейт блока, и это правильно: он для того и
стоит.

Чем блоки проверены. `tools/test_dji_area_production_runbook.py` держит их
свойства в CI (порядок шагов, гейты, обращение с унаследованным токеном,
отсутствие production и заполнителей, равенство пина настоящему отпечатку,
равенство чисел приёмки оракулу). Все блоки разобраны настоящим парсером
PowerShell 5.1 и 7 без ошибок. Главное же — **все три блока исполнены живьём
21.09.2026 и дали PASS**; результаты в разделе выше.

| # | Где | Что делает | Пишет | DJI |
|---|---|---|---|---|
| A | SRV-YOQSH | приёмка сентября на данных площадки (только чтение), затем деплой ревизии | код площадки; база — нет | нет |
| W | рабочая машина | вылеты → манифест → адресные источники за один вчерашний день | только в площадку | да, адресно |
| S | SRV-YOQSH | пересчёт окна дважды, ворота идемпотентности, сторож RAW, повторная приёмка сентября | append-only строки расчёта, после копии | нет |

### Что именно разрешает владелец

> Разрешаю развернуть `dji-area-productionization-001-rc1` на площадке и один
> адресный сбор из кабинета DJI сохранённой сессией: список вылетов за три дня и
> источники по манифесту за один вчерашний день (ожидается 6–42 записи, порог
> остановки 50), с отправкой **только на площадку**; и запись пересчёта окна в
> базу площадки после резервной копии.

### Блок A — SRV-YOQSH: приёмка сентября и деплой

Сначала всё доказывается в стороннем клоне, и только потом трогается площадка.
Приёмка идёт ДО переключения ревизии: если сентябрь на данных площадки не
сходится с оракулом, ничего не разворачивается.

Среди самотестов — Flask-набор экрана, книги и endpoint
(`tests.test_dji_area_control_web_001`) и набор технической страницы, которую
ветка обязана была не задеть (`tests.test_dji_area_report_001`). CI их не
исполняет по контракту workflow (в нём нет зависимостей приложения), поэтому
сервер — единственное место, где они идут на тех версиях Flask и Jinja, под
которыми площадка работает. Оба набора строят приложение на временной базе во
временном каталоге (`tests/harness.py`) и `instance\transport.db` не касаются;
идут они в стороннем клоне и до остановки службы.

```powershell
& {
$ErrorActionPreference = 'Continue'
$expectedHost = 'srv-yoqsh'
$staging = 'C:\transport-report-staging'
$db      = 'C:\transport-report-staging\instance\transport.db'
$service = 'TransportReportStaging'
$site    = 'http://10.103.25.14:5051'
$base    = 'C:\VehicleSoft_Area_Staging'
$src     = 'C:\VehicleSoft_Area_Staging\src'
$out     = 'C:\VehicleSoft_Area_Staging\acceptance'
$memo    = 'C:\VehicleSoft_Area_Staging\before_head.txt'
$rawSnap = 'C:\VehicleSoft_Area_Staging\raw_before.json'
$backup  = 'C:\transport-report-staging\backups\dji-area'
$py      = 'C:\Program Files\Python314\python.exe'
$branch  = 'claude/dji-area-productionization-001'
$ExpectedTag = 'dji-area-productionization-001-rc1'
$ExpectedFingerprint = '7c01a13148410b494d725984b2725618f3ef096535e52dfdd75305eb3d10edb6'
$from    = '2026-09-01'
$to      = '2026-09-18'
if ((hostname) -ne $expectedHost) { throw "STEP FAILED: host is $(hostname), expected $expectedHost" }
if ($staging -notlike '*transport-report-staging*') { throw "STEP FAILED: refusing a root that is not the staging checkout" }
if ($db -notlike '*transport-report-staging*') { throw "STEP FAILED: refusing a database outside the staging checkout" }
if ($service -ne 'TransportReportStaging') { throw "STEP FAILED: refusing a service that is not the staging service" }
if ($site -notmatch ':5051$') { throw "STEP FAILED: refusing a site that is not the staging port 5051" }
if (-not (Test-Path -LiteralPath $db)) { throw "STEP FAILED: database not found: $db" }
if (-not (Test-Path -LiteralPath $py)) { throw "STEP FAILED: python not found: $py" }
$origin = (& git -C $staging config --get remote.origin.url)
if (-not $origin) { throw "STEP FAILED: cannot read origin url from $staging" }
New-Item -ItemType Directory -Force -Path $base | Out-Null
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
& $py -m compileall -q .
if ($LASTEXITCODE -ne 0) { throw "STEP FAILED: compileall exit $LASTEXITCODE" }
& $py -m unittest tests.test_dji_area_capture_manifest_001 tests.test_dji_area_control_report_001 tests.test_dji_area_identity_001
if ($LASTEXITCODE -ne 0) { throw "STEP FAILED: product self-tests exit $LASTEXITCODE" }
& $py tools\test_dji_area_control_acceptance.py
if ($LASTEXITCODE -ne 0) { throw "STEP FAILED: acceptance tool self-test exit $LASTEXITCODE" }
& $py tools\test_dji_area_raw_guard.py
if ($LASTEXITCODE -ne 0) { throw "STEP FAILED: RAW guard self-test exit $LASTEXITCODE" }
& $py tools\test_dji_area_daily.py
if ($LASTEXITCODE -ne 0) { throw "STEP FAILED: daily cycle self-test exit $LASTEXITCODE" }
& $py -m unittest tests.test_dji_area_control_web_001 tests.test_dji_area_report_001
if ($LASTEXITCODE -ne 0) { throw "STEP FAILED: screen, workbook and endpoint self-tests (Flask) exit $LASTEXITCODE" }
$beforeHead = (& git -C $staging rev-parse HEAD)
if ($beforeHead -notmatch '^[0-9a-f]{40}$') { throw "STEP FAILED: cannot read the staging HEAD" }
Write-Host "BEFORE_HEAD: $beforeHead"
$changed = @(& git -C $staging status --porcelain --untracked-files=no)
if ($changed.Count -gt 0) { $changed | ForEach-Object { Write-Host "  MODIFIED: $_" } }
if ($changed.Count -gt 0) { throw "STEP FAILED: the staging checkout has modified tracked files -- refusing to switch revisions over them" }
& git -C $staging fetch --quiet origin $branch
if ($LASTEXITCODE -ne 0) { throw "STEP FAILED: git fetch of the branch exit $LASTEXITCODE" }
& git -C $staging fetch --quiet origin "refs/tags/${ExpectedTag}:refs/tags/${ExpectedTag}"
if ($LASTEXITCODE -ne 0) { throw "STEP FAILED: git fetch of the tag exit $LASTEXITCODE" }
$sha = (& git -C $staging rev-parse --verify --quiet "$ExpectedTag^{commit}")
if ($sha -ne $pinned) { throw "STEP FAILED: the tag resolves to $sha in the staging checkout and to $pinned in the clone" }
& git -C $staging merge-base --is-ancestor $beforeHead $sha
if ($LASTEXITCODE -ne 0) { throw "STEP FAILED: the staging HEAD $beforeHead is not an ancestor of $sha -- stop and report both hashes" }
New-Item -ItemType Directory -Force -Path $backup | Out-Null
New-Item -ItemType Directory -Force -Path $out | Out-Null
$stamp = Get-Date -Format 'yyyyMMdd_HHmmss'
$acc = -1
try {
  Stop-Service -Name $service
  $deadline = (Get-Date).AddSeconds(90)
  while ((Get-Service -Name $service).Status -ne 'Stopped') {
    if ((Get-Date) -gt $deadline) { throw "STEP FAILED: service did not reach Stopped within 90s" }
    Start-Sleep -Seconds 2
  }
  Write-Host ("SERVICE STOPPED: " + (Get-Service -Name $service).Status)
  & $py tools\check_db_lock.py --db $db
  $lock = $LASTEXITCODE
  if ($lock -eq 2) { throw "STEP FAILED: another process holds the database (exit 2)" }
  Write-Host "DB_LOCK_EXIT: $lock  (0 clean, 3 stale WAL is expected on this project)"
  $dest = Join-Path $backup ("transport.db.pre_area_control_" + $stamp + ".bak")
  Copy-Item -LiteralPath $db -Destination $dest -Force
  foreach ($sfx in @('-wal','-shm')) { if (Test-Path -LiteralPath ($db + $sfx)) { Copy-Item -LiteralPath ($db + $sfx) -Destination ($dest + $sfx) -Force } }
  if (-not (Test-Path -LiteralPath $dest)) { throw "STEP FAILED: backup was not created" }
  Write-Host ("BACKUP: " + $dest + "  " + (Get-Item -LiteralPath $dest).Length + " bytes")
  $before = (Get-FileHash -LiteralPath $db -Algorithm SHA256).Hash
  Write-Host "DB SHA256 BEFORE: $before"
  if (-not (Test-Path -LiteralPath $rawSnap)) { & $py tools\dji_area_raw_guard.py --db $db --save $rawSnap }
  if (-not (Test-Path -LiteralPath $rawSnap)) { throw "STEP FAILED: the RAW snapshot was not written" }
  & $py tools\dji_area_recalc.py --db $db --from $from --to $to --dry-run --quiet --json (Join-Path $out 'recalc_dryrun.json')
  if ($LASTEXITCODE -ne 0) { throw "STEP FAILED: recalc dry-run exit $LASTEXITCODE" }
  & $py tools\dji_area_control_acceptance.py --db $db --oracle docs\DJI_AREA_SEPTEMBER_2026_ORACLE.json --recalc-summary (Join-Path $out 'recalc_dryrun.json') --out $out
  $acc = $LASTEXITCODE
  Write-Host "ACCEPTANCE EXIT CODE: $acc  (0 PASS, 3 FAIL)"
  $after = (Get-FileHash -LiteralPath $db -Algorithm SHA256).Hash
  Write-Host "DB SHA256 AFTER : $after"
  if ($before -ne $after) { throw "STOP: the database changed during a read-only run" }
  Write-Host "READ-ONLY CONFIRMED: database bytes identical"
  if ($acc -ne 0) { throw "STOP: the September acceptance did not pass on the staging data (exit $acc) -- NOTHING WAS DEPLOYED; send back $out" }
  if ($beforeHead -ne $sha) { Set-Content -LiteralPath $memo -Value $beforeHead -Encoding ASCII }
  if ($beforeHead -eq $sha) { Write-Host 'ALREADY AT THE REVIEWED REVISION: the rollback memo is left as it was' }
  & git -C $staging checkout --quiet --detach $sha
  if ($LASTEXITCODE -ne 0) { throw "STEP FAILED: git checkout $sha exit $LASTEXITCODE -- the staging checkout stays on $beforeHead" }
  $afterHead = (& git -C $staging rev-parse HEAD)
  if ($afterHead -ne $sha) { throw "STEP FAILED: the staging checkout is at $afterHead, expected $sha" }
  Write-Host "AFTER_HEAD: $afterHead"
} finally {
  Restart-Service -Name $service
  $deadline2 = (Get-Date).AddSeconds(90)
  while ((Get-Service -Name $service).Status -ne 'Running') {
    if ((Get-Date) -gt $deadline2) { throw "STEP FAILED: service did not reach Running within 90s -- START IT BY HAND" }
    Start-Sleep -Seconds 2
  }
  Write-Host ("SERVICE RUNNING: " + (Get-Service -Name $service).Status)
}
Start-Sleep -Seconds 8
$login = Invoke-WebRequest -Uri ($site + '/login') -UseBasicParsing -TimeoutSec 30
if ($login.StatusCode -ne 200) { throw "STEP FAILED: smoke /login returned $($login.StatusCode)" }
if ($login.Content -notmatch 'vs-login-form') { throw "STEP FAILED: smoke /login did not render the login form" }
Write-Host 'SMOKE LOGIN: 200'
$noToken = -1
try { $r1 = Invoke-WebRequest -Uri ($site + '/drones/api/area_capture_manifest') -Method Post -Body '{}' -ContentType 'application/json' -UseBasicParsing -TimeoutSec 30; $noToken = [int]$r1.StatusCode } catch { if ($_.Exception.Response) { $noToken = [int]$_.Exception.Response.StatusCode } }
Write-Host "SMOKE MANIFEST WITHOUT TOKEN: $noToken  (401 expected; 404 old code, 400 no CSRF exemption, 200 endpoint is open)"
if ($noToken -ne 401) { throw "STEP FAILED: the manifest endpoint answered $noToken without a token, expected 401" }
$anon = -1
$anonBody = ''
try { $r2 = Invoke-WebRequest -Uri ($site + '/drones/area-control') -UseBasicParsing -TimeoutSec 30; $anon = [int]$r2.StatusCode; $anonBody = $r2.Content } catch { if ($_.Exception.Response) { $anon = [int]$_.Exception.Response.StatusCode } }
Write-Host "SMOKE SCREEN FOR AN ANONYMOUS VISITOR: $anon  (200 with the login form expected; 404 old code)"
if ($anon -ne 200) { throw "STEP FAILED: /drones/area-control answered $anon for an anonymous visitor" }
if ($anonBody -notmatch 'vs-login-form') { throw "STEP FAILED: an anonymous visitor was NOT sent to the login form" }
Get-ChildItem -LiteralPath $out | Select-Object Name, Length | Format-Table -AutoSize
Compress-Archive -Path "$out\*" -DestinationPath 'C:\VehicleSoft_Area_Staging\area_acceptance.zip' -Force
Write-Host 'STEP=PASS'
Write-Host 'SEND BACK: C:\VehicleSoft_Area_Staging\area_acceptance.zip and the console text above'
}
```

Ожидаемые числа приёмки (они же — в `docs/DJI_AREA_SEPTEMBER_2026_ORACLE.json`):

| Показатель | Ожидание |
|---|---|
| `records` / `raw_missing_records` | 4623 / 0 |
| `raw_ha` | 4413.2825 |
| `excluded_ha` / `excluded_records` | 167.8088 / 230 |
| `after_ha` | 4245.4737 |
| `pending_ha` / `pending_records` | 0 / 0 |
| `review_ha` / `review_records` | 10.3631 / 13 |
| `structural_candidates` / `chains_shown` | 233 / 233 |
| `proven_structural` / `proven_by_control_only` | 229 / 1 |
| `review_application_with_flat_counter` | 4 (остаются на проверке, не обнуляются) |
| `bridges_excluded` | 0 |
| `recalc dry-run calc_writes` | `{"unchanged": 4623}` |

Последняя строка — доказательство, что правка замороженного `pipeline.py`
сентябрь не изменила: строки расчёта на площадке записаны ревизией
`reviewed-4`, а нынешний код на тех же уликах не хочет переписать ни одной.

После блока A глазами: войти на `http://10.103.25.14:5051`, открыть
`/drones/area-control?date_from=2026-09-01&date_to=2026-09-18`, сверить пять
чисел с таблицей выше (на экране — два знака: 4413,28 / 167,81 / 4245,47 / 0,00
/ 10,36), скачать книгу и открыть лист «По_дронам».

### Блок W — рабочая машина: один вчерашний день

Сборщик работает из отдельного клона проверенной ревизии; `.env` и сессия DJI
берутся у holdout-сборщика, потому что они уже смотрят на площадку. Значение
токена блок не печатает и не читает — файл копируется целиком.

**Унаследованный токен процесса.** `drone_collector/config.py` намеренно отдаёт
приоритет окружению процесса над `.env`: так задача планировщика, которая
экспортирует токен, побеждает устаревший файл. Из-за этого же консоль, в которой
`DRONE_API_TOKEN` остался от прежней работы, отправляет сборщика со старым
токеном, и манифест отвечает 401 при верном `.env`. Это ровно то, на чём
остановилась квалификация 21.09.2026. Приоритет не меняется — он правильный.
Блок сам сохраняет унаследованное значение, убирает переменную на время своей
работы, проверяет, что она действительно убрана, и возвращает её в `finally`;
ни одно значение не печатается.

До первого обращения к кабинету блок доказывает: ревизию, что приёмник —
площадка, и что площадка отвечает на манифест (иначе обход вылетов потратил бы
обращение к DJI впустую). Порог стоит дважды: на манифесте ДО обхода — тогда
кабинет не тронут вовсе — и на манифесте после обхода, перед источниками.

Окно — **один вчерашний день**, а не три: на площадке все свежие дни ещё не
посещены, и трёхдневное окно законно дало бы 37–106 записей. Один день — это
настоящий суточный объём (6–42), и порог 50 для него осмыслен.

```powershell
& {
$ErrorActionPreference = 'Continue'
$review   = 'C:\VehicleSoft_DJI_Review_20260918'
$work     = 'C:\VehicleSoft_AreaDaily'
$src      = 'C:\VehicleSoft_AreaDaily\src'
$send     = 'C:\VehicleSoft_AreaDaily\send'
$envFrom  = 'C:\VehicleSoft_Holdout\src\drone_collector\.env'
$envFile  = 'C:\VehicleSoft_AreaDaily\src\drone_collector\.env'
$state    = 'C:\VehicleSoft_Holdout\session\storage_state.json'
$browsers = 'C:\VehicleSoft_Holdout\playwright-browsers'
$cpy      = 'C:\VehicleSoft_Holdout\session_venv\Scripts\python.exe'
$py       = 'C:\Program Files\Python314\python.exe'
$daily    = 'C:\VehicleSoft_AreaDaily\src\drone_collector\data\area_daily'
$branch   = 'claude/dji-area-productionization-001'
$ExpectedTag = 'dji-area-productionization-001-rc1'
$ExpectedFingerprint = '7c01a13148410b494d725984b2725618f3ef096535e52dfdd75305eb3d10edb6'
$StopAbove = 50
if (-not (Test-Path -LiteralPath $py)) { throw "STEP FAILED: python not found: $py" }
if (-not (Test-Path -LiteralPath $cpy)) { throw "STEP FAILED: collector python not found: $cpy" }
if (-not (Test-Path -LiteralPath $review)) { throw "STEP FAILED: review clone not found: $review" }
if (-not (Test-Path -LiteralPath $browsers)) { throw "STEP FAILED: Playwright browsers not found: $browsers" }
if (-not (Test-Path -LiteralPath $state)) { throw "STEP FAILED: the DJI session file is missing: $state -- save it with block 0 of docs\DJI_AREA_SIMPLIFY_001_RUNBOOK.md" }
if (-not (Test-Path -LiteralPath $envFrom)) { throw "STEP FAILED: $envFrom not found -- nothing was collected" }
New-Item -ItemType Directory -Force -Path $work | Out-Null
if (-not (Test-Path -LiteralPath $src)) { & git clone --branch $branch --quiet $review $src }
if (-not (Test-Path -LiteralPath $src)) { throw "STEP FAILED: git clone did not create $src" }
& git -C $src --no-pager log --oneline -1
$pinned = (& git -C $src rev-parse --verify --quiet "$ExpectedTag^{commit}")
if (-not $pinned) { throw "STEP FAILED: tag $ExpectedTag is not in $src -- fetch it: git -C $src fetch --tags" }
$headSha = (& git -C $src rev-parse HEAD)
if ($headSha -ne $pinned) { throw "STEP FAILED: HEAD is $headSha, the reviewed revision is $pinned" }
$dirty = @(& git -C $src status --porcelain)
if ($dirty.Count -gt 0) { throw "STEP FAILED: the clone has local modifications -- refusing to run an unreviewed working tree" }
Write-Host "REVISION PIN: $headSha"
if (-not (Test-Path -LiteralPath $envFile)) { Copy-Item -LiteralPath $envFrom -Destination $envFile }
if (-not (Test-Path -LiteralPath $envFile)) { throw "STEP FAILED: $envFile was not created" }
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
& $py -m unittest drone_collector.tests.test_area_manifest
if ($LASTEXITCODE -ne 0) { throw "STEP FAILED: manifest client self-test exit $LASTEXITCODE" }
& $py tools\test_dji_area_daily.py
if ($LASTEXITCODE -ne 0) { throw "STEP FAILED: daily cycle self-test exit $LASTEXITCODE" }
# The process may carry an inherited DRONE_API_TOKEN from earlier work,
# and config.py lets the process environment win over .env BY DESIGN (see
# the docstring of load_dotenv_file). A stale inherited token then beats
# the copied staging .env and the manifest answers 401. Remove it for this
# block only and put it back in finally. No token value is read or printed.
$hadToken = Test-Path env:DRONE_API_TOKEN
if ($hadToken) { $savedToken = $env:DRONE_API_TOKEN }
if ($hadToken) { Remove-Item env:DRONE_API_TOKEN }
if ($hadToken -and (Test-Path env:DRONE_API_TOKEN)) { throw "STEP FAILED: the inherited DRONE_API_TOKEN could not be removed from this process" }
if ($hadToken) { Write-Host 'PROCESS TOKEN: an inherited one was found, removed for this block and restored at the end' }
if (-not $hadToken) { Write-Host 'PROCESS TOKEN: none inherited; the copied staging .env is authoritative' }
try {
  $env:PLAYWRIGHT_BROWSERS_PATH = $browsers
  $day = (Get-Date).ToUniversalTime().AddHours(5).AddDays(-1).ToString('yyyy-MM-dd')
  Write-Host "SMOKE DAY (UTC+5, yesterday): $day"
  if (Test-Path -LiteralPath $send) { Remove-Item -LiteralPath $send -Recurse -Force }
  New-Item -ItemType Directory -Force -Path $send | Out-Null
  & $cpy -m drone_collector.area_manifest --out (Join-Path $send 'preflight_ids.txt') --summary (Join-Path $send 'preflight_manifest.json') --from $day --to $day --stop-above $StopAbove
  $pre = $LASTEXITCODE
  Write-Host "PREFLIGHT MANIFEST EXIT CODE: $pre  (0 ok, 22 larger than $StopAbove, 23 unavailable)"
  if ($pre -eq 22) { throw "STOP: the manifest is larger than $StopAbove before the walk -- DJI was NOT contacted; send back $send" }
  if ($pre -ne 0) { throw "STOP: staging does not serve the manifest (exit $pre) -- run block A first; DJI was NOT contacted" }
  & $cpy tools\dji_area_daily.py --skip-recalc --from $day --to $day --stop-above $StopAbove
  $rc = $LASTEXITCODE
  Write-Host "DAILY CYCLE EXIT CODE: $rc  (0 ok, 5 sources incomplete, 4 manifest too large)"
  foreach ($name in @('area_manifest.json','area_ids.txt')) { if (Test-Path -LiteralPath (Join-Path $daily $name)) { Copy-Item -LiteralPath (Join-Path $daily $name) -Destination $send -Force } }
  Compress-Archive -Path "$send\*" -DestinationPath 'C:\VehicleSoft_AreaDaily\area_smoke_w.zip' -Force
  if ($rc -eq 4) { throw "STOP: the manifest is larger than $StopAbove -- DJI was NOT contacted for sources; send back C:\VehicleSoft_AreaDaily\area_smoke_w.zip" }
  if (($rc -ne 0) -and ($rc -ne 5)) { throw "STEP FAILED: daily cycle exit $rc" }
  if ($rc -eq 5) { Write-Host 'INCOMPLETE: run this block again -- only the missing flights will be visited' }
  Write-Host 'SEND BACK: C:\VehicleSoft_AreaDaily\area_smoke_w.zip and the console text above'
} finally {
  if ($hadToken) { $env:DRONE_API_TOKEN = $savedToken }
  $savedToken = $null
  Write-Host "PROCESS TOKEN RESTORED: $hadToken"
}
}
```

В выводе цикла обязана быть строка вида
`manifest  ids=19 candidates=13 controls=6 no_v4_at_source=0` — это и есть
размер адресного сбора. Сбора по всему парку в блоке нет: шагу SOURCES период
не передаётся вовсе, только файл идентификаторов манифеста.

Коды сборщика внутри шага: 2 — сессии нет или она истекла (сохранить заново
блоком 0 holdout-ранбука и повторить); 6 — в окне обхода ни одного вылета
(сборщик считает пустое окно ошибкой: так выглядит сессия в чужом регионе); 18 —
часть записей не дала полного набора (повторить блок); 19 — площадка не приняла
пакет (прислать вывод).

На следующее утро — убедиться, что ночной сбор production отработал как обычно
(журнал синхронизации дронов). Вторая сессия того же аккаунта DJI 09.09.2026
проблем не создавала, но проверить — одна минута.

### Блок S — SRV-YOQSH: пересчёт окна и доказательства

Окно `--days 3` заведомо накрывает вчерашний день блока W, даже если между
блоками прошла полночь. Более широкое окно безвредно: пересчёт идемпотентен и
от окна не зависит.

```powershell
& {
$ErrorActionPreference = 'Continue'
$expectedHost = 'srv-yoqsh'
$staging = 'C:\transport-report-staging'
$db      = 'C:\transport-report-staging\instance\transport.db'
$service = 'TransportReportStaging'
$out     = 'C:\VehicleSoft_Area_Staging\smoke'
$rawSnap = 'C:\VehicleSoft_Area_Staging\raw_before.json'
$backup  = 'C:\transport-report-staging\backups\dji-area'
$py      = 'C:\Program Files\Python314\python.exe'
$ExpectedTag = 'dji-area-productionization-001-rc1'
$ExpectedFingerprint = '7c01a13148410b494d725984b2725618f3ef096535e52dfdd75305eb3d10edb6'
if ((hostname) -ne $expectedHost) { throw "STEP FAILED: host is $(hostname), expected $expectedHost" }
if ($staging -notlike '*transport-report-staging*') { throw "STEP FAILED: refusing a root that is not the staging checkout" }
if ($db -notlike '*transport-report-staging*') { throw "STEP FAILED: refusing a database outside the staging checkout" }
if ($service -ne 'TransportReportStaging') { throw "STEP FAILED: refusing a service that is not the staging service" }
if (-not (Test-Path -LiteralPath $db)) { throw "STEP FAILED: database not found: $db" }
if (-not (Test-Path -LiteralPath $py)) { throw "STEP FAILED: python not found: $py" }
if (-not (Test-Path -LiteralPath $rawSnap)) { throw "STEP FAILED: $rawSnap not found -- block A writes it; nothing was changed" }
$pinned = (& git -C $staging rev-parse --verify --quiet "$ExpectedTag^{commit}")
if (-not $pinned) { throw "STEP FAILED: tag $ExpectedTag is not in the staging checkout -- run block A first" }
$headSha = (& git -C $staging rev-parse HEAD)
if ($headSha -ne $pinned) { throw "STEP FAILED: the staging checkout is at $headSha, the reviewed revision is $pinned -- run block A first" }
$changed = @(& git -C $staging status --porcelain --untracked-files=no)
if ($changed.Count -gt 0) { throw "STEP FAILED: the staging checkout has modified tracked files" }
Write-Host "REVISION PIN: $headSha"
Set-Location $staging
$fpFound = @(& $py tools\dji_area_holdout.py fingerprint | Select-String -Pattern '^\s*CODE FINGERPRINT\s*:\s*([0-9a-f]{64})\s*$' | ForEach-Object { $_.Matches[0].Groups[1].Value })
if ($fpFound.Count -ne 1) { throw "STEP FAILED: expected exactly one CODE FINGERPRINT line, got $($fpFound.Count)" }
$fp = $fpFound[0]
if ($fp -ne $ExpectedFingerprint) { throw "STEP FAILED: code fingerprint is $fp, the reviewed one is $ExpectedFingerprint" }
Write-Host "CODE FINGERPRINT: $fp"
& $py tools\test_dji_area_idempotence_gate.py
if ($LASTEXITCODE -ne 0) { throw "STEP FAILED: idempotence gate self-test exit $LASTEXITCODE" }
if (Test-Path -LiteralPath $out) { Remove-Item -LiteralPath $out -Recurse -Force }
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
  $dest = Join-Path $backup ("transport.db.pre_area_smoke_" + $stamp + ".bak")
  Copy-Item -LiteralPath $db -Destination $dest -Force
  foreach ($sfx in @('-wal','-shm')) { if (Test-Path -LiteralPath ($db + $sfx)) { Copy-Item -LiteralPath ($db + $sfx) -Destination ($dest + $sfx) -Force } }
  if (-not (Test-Path -LiteralPath $dest)) { throw "STEP FAILED: backup was not created" }
  Write-Host ("BACKUP: " + $dest + "  " + (Get-Item -LiteralPath $dest).Length + " bytes")
  & $py tools\dji_area_daily.py --db $db --recalc-only --days 3 --work-dir (Join-Path $out 'run1')
  if ($LASTEXITCODE -ne 0) { throw "STEP FAILED: recalculation exit $LASTEXITCODE" }
  & $py tools\dji_area_daily.py --db $db --recalc-only --days 3 --expect-unchanged --work-dir (Join-Path $out 'run2')
  $second = $LASTEXITCODE
  if ($second -eq 6) { throw "STOP: the second recalculation WROTE rows -- the cycle is not idempotent on the staging data; send back $out" }
  if ($second -ne 0) { throw "STEP FAILED: second recalculation exit $second" }
  & $py tools\dji_area_idempotence_gate.py --summary (Join-Path $out 'run2\area_recalc.json')
  if ($LASTEXITCODE -ne 0) { throw "STEP FAILED: idempotence gate refused the second recalculation, exit $LASTEXITCODE" }
  & $py tools\dji_area_raw_guard.py --db $db --compare $rawSnap
  $raw = $LASTEXITCODE
  Write-Host "RAW GUARD EXIT CODE: $raw  (0 untouched, 3 RAW WAS TOUCHED)"
  if ($raw -ne 0) { throw "STOP: the RAW guard did not confirm that drone_flights.area_ha is untouched (exit $raw)" }
  & $py tools\dji_area_control_acceptance.py --db $db --oracle docs\DJI_AREA_SEPTEMBER_2026_ORACLE.json --out (Join-Path $out 'september_after_smoke')
  $acc = $LASTEXITCODE
  Write-Host "SEPTEMBER AFTER THE SMOKE, ACCEPTANCE EXIT CODE: $acc  (0 PASS, 3 FAIL)"
  if ($acc -ne 0) { throw "STOP: the daily cycle disturbed the accepted September figures (exit $acc); send back $out" }
} finally {
  Restart-Service -Name $service
  $deadline2 = (Get-Date).AddSeconds(90)
  while ((Get-Service -Name $service).Status -ne 'Running') {
    if ((Get-Date) -gt $deadline2) { throw "STEP FAILED: service did not reach Running within 90s -- START IT BY HAND" }
    Start-Sleep -Seconds 2
  }
  Write-Host ("SERVICE RUNNING: " + (Get-Service -Name $service).Status)
}
Compress-Archive -Path "$out\*" -DestinationPath 'C:\VehicleSoft_Area_Staging\area_smoke_s.zip' -Force
Write-Host 'STEP=PASS'
Write-Host 'SEND BACK: C:\VehicleSoft_Area_Staging\area_smoke_s.zip and the console text above'
}
```

После блока S глазами: на `/drones/area-control` за вчерашний день видны
кандидаты блока W. Те, по которым V4 приехал, стали «Подтверждённая
корректировка» либо «Принято по DJI»; те, по которым нет, — «Ожидает V4» с
объяснением и остаются по RAW.

### Что прислать обратно

`area_acceptance.zip` и `area_smoke_s.zip` с сервера, `area_smoke_w.zip` с
рабочей машины и полный текст трёх консолей. В выводе должны быть: `REVISION
PIN` и `CODE FINGERPRINT` (одинаковые во всех трёх блоках), `BEFORE_HEAD` и
`AFTER_HEAD`, путь `BACKUP`, обе строки `DB SHA256` и `READ-ONLY CONFIRMED`,
`ACCEPTANCE EXIT CODE: 0`, три строки `SMOKE ...`, `RECEIVER CHECK`, `SMOKE DAY`,
`PREFLIGHT MANIFEST EXIT CODE`, строка `manifest  ids=...`, `DAILY CYCLE EXIT
CODE`, слово `unchanged` во второй сводке пересчёта, `RAW GUARD EXIT CODE: 0`,
`SEPTEMBER AFTER THE SMOKE, ACCEPTANCE EXIT CODE: 0` и `STEP=PASS` в блоках A
и S.

### Если блок остановился

- `refusing ...`, `host is ...` — сработала защита от чужого хоста или от
  production. Ничего не выполнялось.
- Остановка блока A до строки `SERVICE STOPPED` — площадка не тронута вовсе.
- `NOTHING WAS DEPLOYED` — приёмка на данных площадки не сошлась с оракулом.
  Служба поднята на прежней ревизии. Прислать `acceptance`: в
  `area_control_acceptance.json` поимённо названо каждое расхождение.
- `the database changed during a read-only run` — кто-то писал в базу при
  остановленной службе. Это находка сама по себе; ревизия не переключалась.
- Блок W: `DJI was NOT contacted` — остановка до кабинета. Иначе — смотреть код
  шага в выводе цикла.
- Блок S остановился после `BACKUP:` — строки расчёта append-only, прежние не
  переписаны; служба поднята в `finally`.

## Production: что НЕ сделано и что понадобится

Ничего из этого ранбука на production не исполняется. Чтобы функция заработала
там, нужны три отдельных решения владельца, и ни одно из них этой веткой не
принимается:

1. **Гейт.** `docs/RELEASE_GATE.md` должен опустеть — это условие всего проекта.
2. **Линия `main`.** Production стоит на `b1c57ab` (head hotfix PR #127), а не на
   `main`; возврат на линию `main` — отдельная операция.
3. **Миграция V2.** `migrate_drone_area_control_v2_001.py` при остановленной
   службе и снятой копии; без неё экран работает, но решения и журнал
   обновлений недоступны (экран говорит это словами).
4. **Кнопка.** Задача планировщика для «Обновить данные DJI» и переменные
   службы — раздел «Обновить данные DJI из интерфейса».
5. **Расписание.** Отдельная задача планировщика с одной командой
   `tools\dji_area_daily.py --db instance\transport.db` после ночного обхода
   вылетов. Она заменяет предложенный в `docs/DJI_DAILY_EVIDENCE_RUN.md` сбор
   источников по всему парку (около 270 посещений в сутки) адресным (6–42).
   Каталог полей (`--land-snapshot`) цикл не трогает — он остаётся как есть.

## Исторический backfill: инструмент готов, не исполнялся

Прошлые периоды проходятся **тем же** суточным циклом, окно за окном:
`tools\dji_area_backfill.py`. Второго алгоритма нет — инструмент зовёт
`tools\dji_area_daily.py` (FLIGHTS → MANIFEST → SOURCES → VERIFY → RECALC) на
каждое окно под той же блокировкой цикла. `UPDATE drone_flights SET area_ha` не
выполняется никогда: исправленная площадь живёт рядом с RAW, а не вместо него;
`billable_area_m2` по-прежнему NULL. Этой задачей backfill **не запускался** —
запуск и разбор результата решает владелец.

Правила:

1. Сначала площадка, потом production. Окно — от одного до семи дней, окно
   никогда не пересекает границу месяца; идут от старого к новому.
2. После КАЖДОГО окна чекпойнт пишется атомарно (временный файл и замена). Окно
   «готово» (`DONE`) повторно не трогается, пока не сменилась версия
   алгоритма площади (единственная «необходимость» переделать готовое) или не
   дан `--force`. Упавшее окно (`FAILED`) повторяется при следующем запуске;
   после `--max-attempts` попыток (по умолчанию 3) — `GAVE_UP` и пропуск до
   `--retry-gave-up`.
3. Остановить можно в любой момент (Ctrl+C, `--max-windows N`); следующий запуск
   той же командой продолжит с первого незавершённого окна. Чекпойнт с другим
   диапазоном или другой длиной окна не смешивается — инструмент отказывает.
4. У DJI телеметрия V4 хранится не вечно. Для старых периодов значительная
   часть кандидатов будет «DJI не хранит V4» — это честное «недостаточно
   доказательств»: запись остаётся по RAW в «ожидает доказательства», а не
   обнуляется. Такие записи попадают в список для ручного разбора.
5. Кандидат без V4 делает окно `FAILED` (код цикла 5); промах только
   контрольной выборки — `DONE` с предупреждением.

План без запуска (ничего не пишет и к DJI не обращается):

```text
& "C:\Program Files\Python314\python.exe" tools\dji_area_backfill.py --plan --from 2026-03-01 --to 2026-09-22
```

Запуск на хосте, где стоят и сборщик, и база (интерпретатор venv сборщика,
окно — один день, не больше 30 окон за раз):

```text
drone_collector\.venv\Scripts\python.exe tools\dji_area_backfill.py --db instance\transport.db --from 2026-03-01 --to 2026-09-22 --window-days 1 --max-windows 30
```

Артефакты — рядом с чекпойнтом (`drone_collector\data\area_backfill\`):

| Файл | Что в нём |
|---|---|
| `checkpoint.json` | каждое окно: статус, попытки, последний код, итог цикла, версия алгоритма, манифест, промахи доказательств, нерешённые записи |
| `backfill_summary.json` | по месяцам: окон всего / готово / упало / сдалось / впереди; записей ждёт доказательства / требует решения / «DJI не хранит V4» |
| `backfill_unresolved.csv` | месяц, окно, flight_id, класс, причина — список для ручной проверки |
| `work\` | рабочие файлы цикла последнего окна (`area_manifest.json`, `last_cycle.json`, …) |

Коды возврата backfill:

| Код | Что значит |
|---|---|
| `0` | все окна диапазона готовы |
| `1` | ошибка аргументов либо чекпойнт от другого диапазона или длины окна |
| `2` | база не найдена; ничего не создано |
| `3` | диапазон пройден, но есть окна `FAILED` или `GAVE_UP` — смотреть `backfill_unresolved.csv` и чекпойнт |
| `7` | остановлен: блокировку цикла держит другой сбор; запустить позже той же командой |
| `8` | остановлен по `--max-windows`, окна ещё остались; запустить ту же команду снова |

После прохода месяца — пересчёт месяца должен ответить `unchanged` на повторе
(`tools\dji_area_recalc.py --dry-run --from ... --to ...`) и сторож RAW
(`tools\dji_area_raw_guard.py`) — «ни одна RAW не изменилась». Исторические
корректировки попадают в расчёты с операторами только отдельным решением
владельца.
