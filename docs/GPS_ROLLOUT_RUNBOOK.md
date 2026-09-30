# Выкат трека GPS план-факт на production

Порядок для владельца. Всё выполняется на боевом сервере `C:\transport-report`
в PowerShell, **по одной команде на строку** — `&&` в PowerShell нет.

Собран 2026-08-19, после инкрементов GPS-1…GPS-8. Ни один шаг отсюда сессия
Claude выполнить не может: облачный контейнер не сидит в сети кластера, и
ходить туда не должен.

**Плейсхолдеров в командах нет.** Всё вставляется как есть; единственное, что
меняется по ходу, — дата в имени резервной копии и дата суток в расчёте.

---

## Откуда берётся код (правило с 28.09.2026)

`C:\transport-report` — каталог **боевого** сервера. Код туда приезжает
**только релизом** (порядок релиза и гейт — `docs/RELEASE_GATE.md`). `git pull`
в этом каталоге шаги трека не содержат и содержать не должны: 27–28.09 такой
`pull` сдвинул рабочую копию прода с записанного базиса `54a875d` на `436e890`
и подтянул PR #133, чья миграция на production не применена. Служба при этом
продолжала работать на старом коде, и любой её перезапуск загрузил бы код без
миграции.

Ночные задачи (раздел 7) работают на коде релиза из `C:\transport-report` —
как и раньше.

Инструменты трека с **новым** кодом — проверка до релиза, разовые прогоны —
запускаются из отдельного клона `C:\gps-tools` с ключами `--db` и `--dir` на
боевые файлы или на их копию. Все инструменты трека эти ключи принимают.

Завести клон (один раз; адрес берётся у рабочей копии прода, поэтому и учётные
данные те же):

```
if (-not (Test-Path C:\gps-tools)) { git clone (git -C C:\transport-report remote get-url origin) C:\gps-tools }
```

Обновить его до `main`:

```
git -C C:\gps-tools fetch origin
```

```
git -C C:\gps-tools checkout --detach origin/main
```

Проверить, на чём стоит клон:

```
git -C C:\gps-tools --no-pager log --oneline -1
```

Токен Wialon в клон не копируется: инструменты ищут `wialon_token.txt` в
текущем каталоге, поэтому те, кому нужен Wialon, запускаются из
`C:\transport-report` полным путём к скрипту в `C:\gps-tools`, например
`& "C:\Program Files\Python314\python.exe" C:\gps-tools\tools\gps_units_inventory.py --db ...`.

---

## 0. Что должно быть верно до начала

| Условие | Как проверить |
|---|---|
| Доступ к Wialon с адреса кластера | `Test-NetConnection web.gpstrack.uz -Port 443` → `TcpTestSucceeded : True` |
| Токен Wialon лежит на месте | файл `C:\transport-report\wialon_token.txt`, первая строка — токен |
| Код обновлён **релизом** | `git --no-pager log --oneline -1` в `C:\transport-report` показывает коммит релиза, записанный в `docs/RELEASE_GATE.md`; `git pull` здесь не делается (раздел выше) |
| Окружение с геостеком есть | `& C:\gps_venv\Scripts\python.exe -c "import shapely, scipy, pyproj; print('ok')"` |

Если окружения нет — две команды:

```
& "C:\Program Files\Python314\python.exe" -m venv C:\gps_venv
```

```
& C:\gps_venv\Scripts\python.exe -m pip install -r C:\transport-report\gps\requirements.txt
```

Коллектору геостек **не нужен**: он на голом stdlib и работает обычным
питоном сервера. Геостек нужен только суточному расчёту.

---

## 1. Три миграции

Служба останавливается один раз, миграции идут **по одной**, как требует
`docs/MIGRATIONS.md`.

```
cd C:\transport-report
```

```
.\nssm.exe stop TransportReport
```

```
copy instance\transport.db instance\transport.db.backup_20260819
```

```
& "C:\Program Files\Python314\python.exe" migrate_gps_daily_001.py
```

Ожидается: `Done. gps_daily_aggregates (14 columns) and gps_work_polygons (14 columns) with 3 indexes are in place.`

```
& "C:\Program Files\Python314\python.exe" migrate_gps_verdicts_001.py
```

Ожидается: `Done. Table gps_verdicts with 14 columns and 2 indexes is in place.`

```
& "C:\Program Files\Python314\python.exe" migrate_gps_sync_log_001.py
```

Ожидается: `Done. Table gps_sync_log with 21 columns and 1 index is in place.`

```
.\nssm.exe start TransportReport
```

Проверка реестра — три новых строки:

```
& "C:\Program Files\Python314\python.exe" -c "import sqlite3; c=sqlite3.connect('instance/transport.db'); [print(r) for r in c.execute(\"SELECT name, applied_at FROM schema_migrations WHERE name LIKE 'GPS%' ORDER BY id\")]; c.close()"
```

Любая из миграций, запущенная второй раз, скажет `Already applied. Nothing to do.`
и ничего не сделает. Это нормально и безопасно.

---

## 2. Первый прогон коллектора

Читает Wialon, кладёт точки в помесячные файлы рядом с базой. Службу
останавливать не надо: в `transport.db` коллектор пишет одну строку журнала и
только в самом конце.

```
cd C:\transport-report
```

```
& "C:\Program Files\Python314\python.exe" -m gps_collector.main *> gps_collect.log
```

**Перенаправление в файл обязательно.** Консоль Windows в режиме выделения —
включается случайным щелчком в окне — останавливает процесс на первом же
выводе. Это уже стоило треку трёх суток ожидания прогона, который все считали
медленным.

Ожидаемое время: около 20 минут на ~298 живых объектов (2 запроса на объект,
пауза 2 секунды). Первый прогон берёт сутки назад по каждому объекту.

Смотреть результат:

```
Get-Content gps_collect.log -Tail 20
```

В последних строках должно быть `points written: N of M seen` и
`requests: … | logins: 1 | abandoned: 0`. **`logins` больше единицы** означает,
что сессия рвалась; **`abandoned` больше нуля** — что какие-то запросы
пришлось бросить по сроку.

Строка `intervals truncated by the message limit: N` с N больше нуля значит,
что у N объектов за интервал набралось больше 50 000 сообщений — так бывает
только после долгого простоя (07.09 после 18 дней — у 104 объектов). Коллектор
взял первые 50 000 и поставил отметку на последней точке; хвост приедет
следующим прогоном. **Повторять прогон, пока N не станет нулём.** Суточный
расчёт за такие сутки сам ответит `sbor_nepolnyy` вместо площади и досчитает
их по `--catch-up`, когда точки доедут (раздел 4).

### Если нужен более глубокий первый заход

По умолчанию первый прогон берёт **одни** сутки назад. Чтобы взять неделю:

```
& "C:\Program Files\Python314\python.exe" -m gps_collector.main --backfill-days 7 *> gps_collect.log
```

Это умножает время прогона на объём, а не на число дней: запрос по объекту
один, интервал шире. Дальше коллектор идёт от watermark и глубина не важна.

---

## 3. Проверить приём глазами

Открыть в браузере **GPS план-факт → Приём данных** (`/gps/sync`).

| Что смотреть | Что должно быть |
|---|---|
| Состояние | «Полный». «Частичный» — часть объектов не прочитана, они приедут следующей ночью; «Прерван» — разбираться |
| Счётчики | **никакой красной плашки «Счётчики не сходятся»** |
| Увидено / Записано / Дубли / Без координат | сумма трёх последних равна первому |
| Запросов | около двух на спрошенный объект |
| «усечено: N» мелким под «Запросов» | не должно быть. Если есть — повторить прогон: хвост истории у этих объектов ещё не собран |

Красная плашка «Счётчики не сходятся» — единственное, что нельзя оставить без
разбора: если счётчики врут, всё, что по ним решат, будет решено по вранью.

---

## 4. Суточный расчёт

Считает участки работы по точкам и кладёт агрегаты и полигоны в `transport.db`.
Нужен геостек, поэтому запускается из `C:\gps_venv`.

Без аргументов считает вчерашние сутки по местному времени. За конкретные —
ключом `--date`:

```
cd C:\transport-report
```

```
& C:\gps_venv\Scripts\python.exe -m gps.daily
```

```
& C:\gps_venv\Scripts\python.exe -m gps.daily --date 2026-08-18
```

В выводе — по строке на объект: сколько участков и сколько гектаров, либо
причина, по которой площадь не публикуется (`redkaya_zapis`, `net_dvizheniya`,
`net_tochek`, `sbor_nepolnyy`, `spetstekhnika`). Внизу — `published / not computed / sites`.

`spetstekhnika` — машина в категории «Спецтехника» (решение владельца
28.09.2026): след трека посчитан, гектары по ней не считаются никогда. Если
машина на самом деле полевая — поменять категорию в карточке техники; ночной
`--catch-up` пересчитает последние 30 суток сам (строка
`category rule -- N day(s) now track only ..., M day(s) back to hectares`).

`sbor_nepolnyy` — коллектор не дошёл до конца этих суток (его отметка по
объекту стоит раньше полуночи: усечённый ответ после простоя). Площадь по
неполным точкам не публикуется, а участки и ответы операторов, если сутки уже
считались раньше, **не трогаются**. Когда коллектор догнал хвост, такие сутки
досчитываются одной командой — она находит их сама и ничего не спрашивает:

```
& C:\gps_venv\Scripts\python.exe -m gps.daily --catch-up
```

Та же команда добирает сутки, у которых есть точки, но нет строки вовсе
(объект, чей хвост приехал позже), за последние 30 дней. Ночная задача
(раздел 7) делает это сама после расчёта вчерашних суток.

Проверить глазами: **GPS план-факт → Факт по технике** (`/gps/fact`) — выбрать
те же сутки и машину. Должны быть показатели трека, участки и картинка
полигонов.

---

## 5. Зеркало контуров

Один раз, а дальше по мере надобности: справочник меняется медленно.

**Сначала вхолостую** — ничего не пишет, только показывает:

```
& "C:\Program Files\Python314\python.exe" tools\gps_mirror_contours.py --dry-run
```

Смотреть на строки `zones listed in resource 285`, `to add` и
`names repeated in Wialon`. Если числа выглядят разумно (зон около 17–18 тысяч):

```
& "C:\Program Files\Python314\python.exe" tools\gps_mirror_contours.py
```

Скрипт **ничего не удаляет и не гасит**: контур, пропавший из Wialon, остаётся
как был — на него могут ссылаться выставленные счета. Он только называет число
таких. Дубли имён складываются в `gps_contour_duplicates.csv`; это не ошибка
импорта, справочник содержит их по конструкции.

После зеркалирования у участков на экране «Факт по технике» появятся имена
полей: расчёт кладёт `contour_id` с самого начала, ему не хватало только
наполненного справочника.

---

## 6. Связка техники с объектами Wialon

Один раз после первого прогона коллектора, затем — когда в парке появились
новые трекеры.

**Без этого шага сверка пуста.** `/gps/orders` ищет объект машины через
`wialon_id` в сопоставлении Wialon, а эту колонку до сих пор никто не
заполнял: ручной импорт моточасов её не трогает, поля на экране нет. Пока
она пуста, каждый наряд показывает «техника не сопоставлена с объектом
Wialon», а «Факт по технике» пишет числа вместо имён машин.

**Сначала вхолостую** — ничего не пишет, только план:

```
& "C:\Program Files\Python314\python.exe" tools\gps_link_mappings.py
```

Скрипт берёт у Wialon список объектов (один запрос, сообщений не грузит) и
ставит `wialon_id` тем строкам сопоставления, чьё имя совпадает с именем ровно
одного объекта. Смотреть на `to link` и на строки, требующие решения. План
целиком — в `gps_link_plan.csv` (Excel открывает как есть); первыми в нём идут
строки, где нужен человек:

| Статус | Что это значит | Что делать |
|---|---|---|
| `neodnoznachno` | одно имя на нескольких объектах Wialon | по времени последнего сообщения выбрать живой: `--set ID_СТРОКИ=ID_ОБЪЕКТА` |
| `ne_naideno` | объекта с таким именем в Wialon нет | в колонке `candidates` — объекты, в имени которых есть номер машины; выбрать через `--set` или оставить |
| `stolknovenie_imen` | две наши строки с одним именем с точностью до пробелов и регистра | оставить одну, вторую пометить «не наша» на экране сопоставления |
| `rashozhdenie` | `wialon_id` уже стоит, а имя указывает на другой объект | скрипт не трогает; если стоящий id неверен — `--unset`, затем `--set` |
| `bez_stroki` | объект есть в Wialon, строки сопоставления нет | новый трекер или чужая техника: завести строку через импорт или не трогать |

Если план устраивает:

```
& "C:\Program Files\Python314\python.exe" tools\gps_link_mappings.py --apply
```

Пишутся только строки со статусом `svyazat`, одной транзакцией. Стоящий
`wialon_id` скрипт **никогда не перезаписывает**, строки «не наша техника» не
трогает, `equipment_id` не ставит: какой машине принадлежит объект, по-прежнему
решает человек на экране сопоставления.

Решения по строкам из CSV — тем же скриптом, с теми же замками (объект обязан
быть в парке, стоящий id не перезаписывается, противоречие точному имени —
отказ). Номера строк — колонка `mapping_id`, номера объектов —
`wialon_id_match`:

```
& "C:\Program Files\Python314\python.exe" tools\gps_link_mappings.py --set 77=12345 --apply
```

```
& "C:\Program Files\Python314\python.exe" tools\gps_link_mappings.py --unset 77 --apply
```

Колонка `equipment_objects` больше единицы — у машины несколько объектов
(история замены трекеров). Это не ошибка: сверка покажет по ней «несколько
объектов» и считать не станет; мёртвый трекер стоит пометить «не наша» на
экране сопоставления.

Повторный запуск безопасен: связанные строки он показывает как `uzhe`,
переименованные в Wialon объекты — как `pereimenovan`, пропавшие — как
`obekt_ischez`, и ничего из этого не меняет.

---

## 7. Расписание

Коллектор ночью, расчёт следом. Обе задачи — обычный Task Scheduler, как
суточная резервная копия базы.

Сначала два `.bat`-файла рядом с проектом.

`C:\transport-report\gps_collect.bat`:

```
@echo off
cd /d C:\transport-report
"C:\Program Files\Python314\python.exe" -m gps_collector.main >> gps_collect.log 2>&1
```

`C:\transport-report\gps_daily.bat`:

```
@echo off
cd /d C:\transport-report
C:\gps_venv\Scripts\python.exe -m gps.daily >> gps_daily.log 2>&1
C:\gps_venv\Scripts\python.exe -m gps.daily --catch-up >> gps_daily.log 2>&1
```

Вторая строка досчитывает сутки, которые прошлой ночью ждали коллектора
(`sbor_nepolnyy`, раздел 4). В обычную ночь она печатает одну строку
`nothing is waiting` и ничего не делает.

Без `--date` расчёт берёт **вчерашние сутки по местному времени** — ровно то,
что нужно ночной задаче. Считать «вчера» в командном файле Windows пришлось бы
через `for /f` вокруг PowerShell со вложенными кавычками, а это конструкция,
где одна кавычка съедает половину команды и задача молча не запускается; узнают
об этом через неделю по пустым суткам.

Затем две задачи (CMD от администратора):

```cmd
schtasks /create /tn "GpsCollect" /tr "C:\transport-report\gps_collect.bat" /sc daily /st 01:00 /ru SYSTEM /f
```

```cmd
schtasks /create /tn "GpsDaily" /tr "C:\transport-report\gps_daily.bat" /sc daily /st 02:30 /ru SYSTEM /f
```

Полтора часа между ними — с запасом: прогон занимает около двадцати минут.
Расчёт идёт **после** сбора, иначе он посчитает вчерашние сутки по неполным
точкам.

Проверить, что задачи заведены и когда пойдут:

```
schtasks /query /tn "GpsCollect" /fo LIST
```

```
schtasks /query /tn "GpsDaily" /fo LIST
```

Наутро после первой ночи — снова `/gps/sync`: там должна быть строка прогона.

---

## 8. Ретенция точек

**Расписания у ретенции нет и быть не должно.** Устав запрещает удалять
продовые данные автоматически, поэтому скрипт по умолчанию ничего не удаляет,
а `--apply` человек пишет руками.

Раз в квартал:

```
& "C:\Program Files\Python314\python.exe" tools\gps_retention.py
```

Он покажет, какие помесячные файлы старше 90 дней и сколько места освободится.
Если список устраивает:

```
& "C:\Program Files\Python314\python.exe" tools\gps_retention.py --apply
```

Текущий месяц не удаляется никогда. Watermark лежит отдельным файлом и
удаление месяца переживает — иначе следующий прогон пошёл бы за всей историей
по каждому объекту.

---

## 9. Что делать, если пошло не так

| Симптом | Что это значит | Что делать |
|---|---|---|
| `ERROR: ne udalos voyti` при запуске коллектора | не пускает Wialon | `Test-NetConnection web.gpstrack.uz -Port 443`; если TCP есть, а вход нет — токен |
| В журнале приёма «Прерван» | прогон умер на середине | точки, успевшие лечь, зафиксированы; watermark не потерян, следующий прогон доберёт. Причина — в колонке рядом |
| «Частичный» несколько ночей подряд по одному объекту | объект не читается | посмотреть его в Wialon: скорее всего трекер |
| Красная плашка «Счётчики не сходятся» | счётчики врут | не принимать решений по этим числам, разобраться |
| `redkaya_zapis` у машины каждый день | трекер пишет реже раза в 30 с | это ограничение метода, а не дефект: редкая запись всегда занижает площадь. Машину — механику на настройку трекера |
| «Расчёта нет» на экране сверки | за эти сутки суточный расчёт не проходил | запустить `gps.daily` за нужную дату |
| `sbor_nepolnyy` у машины, на приёме «усечено: N» | коллектор не дошёл до конца суток: усечённый ответ после простоя | повторить прогон коллектора до «усечено: 0», затем `gps.daily --catch-up`; ночная задача делает это сама |
| «Техника не сопоставлена с объектом Wialon» по каждому наряду | `wialon_id` в сопоставлении пуст — шаг 6 не выполнялся | `tools\gps_link_mappings.py`, затем `--apply` (раздел 6) |
| `database is locked` в расчёте | служба пишет в базу одновременно | повторить; соединения ждут 30 секунд, обычно этого хватает |

---

## 10. Откат

Порядок обратный, и **код откатывается раньше данных**.

1. Откатить код: `git revert` мерж-коммитов PR #89, #91, #93, #95, #119, #120
   в обратном порядке. `git reset --hard` уставом запрещён: ветка `main` общая для
   четырёх треков.
2. Снять задачи расписания:

```cmd
schtasks /delete /tn "GpsCollect" /f
```

```cmd
schtasks /delete /tn "GpsDaily" /f
```

3. Таблицы **можно оставить**: после отката кода их никто не читает, а
   `db.create_all()` таблиц не удаляет. Если всё же удалять — сначала сохранить
   то, что руками не восстановить: `gps_work_polygons.operator_label` (ответы
   операторов «работа/проезд») и весь `gps_verdicts` (журнал разбора). Команды
   выгрузки — в докстрингах самих миграций.
4. Помесячные файлы точек удалять не обязательно: после отката кода
   приложение их не читает вовсе (с A2 экран «Факт по технике» читает из них
   трек одной машины за одни сутки, только `mode=ro`). Место —
   `tools\gps_retention.py`.
5. Связки `wialon_id` снимаются тем же скриптом по списку из
   `gps_link_plan.csv`: `tools\gps_link_mappings.py --unset ID --apply`.
   Строки сопоставления при этом не удаляются, ручной импорт моточасов их
   не замечает.

---

## 11. Карта на экране «Факт по технике» (A2)

С A2 экран показывает сутки машины на карте: трек, найденные участки (номер
на карте тот же, что в таблице; цвет — ответ оператора) и контур поля из
справочника, если участок в него попал. Трек читается из помесячного файла
точек только чтением (`mode=ro`), в `transport.db` точки не попадают.

Библиотека — Leaflet 1.9.4 в репозитории (`static/vendor/leaflet/`, суммы и
лицензия — `VENDOR.md` там же), без CDN. Компонент общий для программы:
`static/js/vs-map.js`, `static/css/vs-map.css`, `vs_map.py`.

**Подложка грузится в браузере оператора из интернета.** Доступ нужен с
рабочих компьютеров. 28.09.2026 с компьютера оператора `10.103.53.128`
`tile.openstreetmap.org` и `ibasemaps-api.arcgis.com` отвечали
(`TcpTestSucceeded : True`). Без доступа карта всё равно встанет — трек,
участки и контуры от плиток не зависят, — но на сером фоне.

### Три подложки и почему их три

| Подложка | Что это | Свежесть | Чёткость | Условие |
|---|---|---|---|---|
| Спутник (чёткий, Esri) | World Imagery | раз в месяцы–год: Esri обновляет у Maxar раз в год | до 30–60 см | ключ Esri, бесплатно 2 000 000 плиток в месяц |
| Свежий снимок (Sentinel-2, 10 м) | Copernicus Data Space Ecosystem | снимок раз в 2–5 суток; под картой — дата | 10 м на пиксель | идентификатор конфигурации, бесплатная квота — ниже |
| Карта | OpenStreetMap | — | — | ничего не нужно |

Свежих и одновременно чётких снимков бесплатно не бывает ни у кого: чёткие
подложки обновляются раз в месяцы–годы, свежесть в днях даёт только
Sentinel-2. Поэтому обе на одной карте. По умолчанию открывается чёткая;
свежая — в переключателе слоёв, а её дата подписана под картой всегда.
Окно даты свежего снимка — месяц до суток работы и две недели после, не
дальше сегодняшнего: для вчерашней работы это самый свежий снимок, для
старой — снимок её результата.

Проверено 28.09.2026, что нельзя: плитки Google через Leaflet — вне условий
Google; Esri без ключа — вне условий Esri; сервис EOX Sentinel-2 — только
некоммерческий.

**Квота Copernicus.** Бесплатно — 10 000 единиц обработки в месяц, остаток
сгорает 1-го числа. Лимит запросов в месяц у бесплатного тарифа тоже есть;
его число из среды сессии не проверено (сайт документации Copernicus
закрыт), и панель Usage его не показывает (30.09.2026) — там видно только
фактическое использование за 31 день. Квота одна на учётную запись: всё,
что ходит под ней (и не только наша карта), тратит её вместе. Плитка 256×256 истинных
цветов — около 0,25 единицы и один запрос. Свежий слой просит плитки не
мельче 14-го уровня (7 м на пиксель на широте Бухары — мельче самого
снимка) и ближе растягивает их сам: на стенде поле на уровне 17 — 2 плитки
Sentinel-2 против 21 плитки Esri, приближение новых плиток не просит. Дата
снимка — один запрос WFS на каждое открытие экрана с картой. Квота
кончилась — свежий слой и его дата не грузятся до 1-го числа; чёткий Esri,
карта, трек и участки работают.

### Подключить (владелец)

Регистрации — в браузере; сайты Esri и Copernicus из среды сессии Claude
недоступны, поэтому названия пунктов меню могут отличаться от написанного.

1. **Esri.** Бесплатная учётная запись ArcGIS Location Platform
   (`https://location.arcgis.com`). В ней — API-ключ с правом на базовые карты
   (Basemaps); в ограничениях ключа по адресу (Referrers) — две строки:
   `http://10.103.25.14:5050` (боевой) и `http://10.103.25.14:5051`
   (площадка). Ключ показывается один раз — сразу к шагу 3.
2. **Copernicus.** Бесплатная учётная запись Copernicus Data Space
   Ecosystem (`https://dataspace.copernicus.eu`). В панели Sentinel Hub
   (`https://shapps.dataspace.copernicus.eu/dashboard`) — «Configuration
   Utility», новая конфигурация по шаблону Sentinel-2 L2A, где есть слой
   истинных цветов. Скопировать идентификатор конфигурации (ID). Если слой
   истинных цветов называется не `TRUE_COLOR` (например, `1_TRUE_COLOR`) —
   записать его идентификатор второй строкой файла в шаге 4.
3. Ключ Esri — в файл (Блокнот создаст его; вставить ключ первой строкой,
   сохранить, закрыть; ключ не пересылать и в отчёты не вставлять):

```
notepad C:\transport-report\instance\esri_api_key.txt
```

4. Идентификатор Copernicus — в файл, так же:

```
notepad C:\transport-report\instance\copernicus_instance_id.txt
```

Служба читает оба файла при каждом открытии экрана — перезапуск не нужен.
Действуют файлы с релиза, в котором есть A2; до него программа их не
читает. Выключить подложку — удалить её файл.

**Площадка.** Перед проверкой экрана на площадке — те же два файла в её
каталог (сейчас площадка за Дронами: делать, когда на неё выйдет GPS):

```
Copy-Item C:\transport-report\instance\esri_api_key.txt C:\transport-report-staging\instance\esri_api_key.txt
```

```
Copy-Item C:\transport-report\instance\copernicus_instance_id.txt C:\transport-report-staging\instance\copernicus_instance_id.txt
```

### Проверить ключи до релиза (сервер, PowerShell)

Все команды — в одном окне PowerShell, по порядку. Ключи читаются из файлов
так же, как их читает программа: первая непустая строка, у Copernicus вторая
строка — имя слоя, если есть. Вставлять ничего не нужно, на экран ключи не
выводятся. Сначала — доступ с сервера:

```
Test-NetConnection ibasemaps-api.arcgis.com -Port 443
```

```
Test-NetConnection sh.dataspace.copernicus.eu -Port 443
```

Ожидается `TcpTestSucceeded : True` у обоих. Если `False` — проверки ниже
не пройдут с сервера, и это не значит, что ключи плохие: операторам нужен
доступ со своих компьютеров, и его там проверить так же.

Каталог для результатов:

```
New-Item -ItemType Directory -Force C:\gps-tools\check
```

Windows PowerShell 5.1 не всегда включает TLS 1.2, без которого оба сервиса
отвечают ошибкой «Could not create SSL/TLS secure channel». Эта команда
включает его только в этом окне, на систему не влияет:

```
[Net.ServicePointManager]::SecurityProtocol = [Net.ServicePointManager]::SecurityProtocol -bor [Net.SecurityProtocolType]::Tls12
```

**Esri — одна плитка над полем 3208 (Бухара).** Заголовок `Referer` — тот
же, что шлёт браузер оператора с боевого адреса: так проверяется и
ограничение ключа по адресу из шага 1.

```
$esri = @(Get-Content C:\transport-report\instance\esri_api_key.txt | ForEach-Object { $_.Trim() } | Where-Object { $_ })[0]
```

```
Invoke-WebRequest ("https://ibasemaps-api.arcgis.com/arcgis/rest/services/World_Imagery/MapServer/tile/16/24810/44519?token=" + $esri) -Headers @{ Referer = 'http://10.103.25.14:5050/' } -OutFile C:\gps-tools\check\esri_tile.jpg -UseBasicParsing
```

```
(Get-Item C:\gps-tools\check\esri_tile.jpg).Length
```

Ожидается число больше 5 000 (байт) и никакой красной ошибки. Красная
ошибка — ключ не принят: не тот ключ, нет права Basemaps или адрес в
ограничении ключа записан не так (шаг 1). Число меньше 1 000 — в файле
текст ответа, а не снимок: `Get-Content C:\gps-tools\check\esri_tile.jpg`
покажет его, ключа в нём нет.

**Copernicus — идентификатор и слой из файла:**

```
$copernicus = @(Get-Content C:\transport-report\instance\copernicus_instance_id.txt | ForEach-Object { $_.Trim() } | Where-Object { $_ })
```

```
$layer = if ($copernicus.Count -gt 1) { $copernicus[1] } else { 'TRUE_COLOR' }
```

**Copernicus — какие снимки есть над полем за сентябрь** (это тот же
запрос, которым карта узнаёт дату снимка):

```
$answer = (Invoke-WebRequest ("https://sh.dataspace.copernicus.eu/ogc/wfs/" + $copernicus[0] + "?SERVICE=WFS&REQUEST=GetFeature&VERSION=2.0.0&TYPENAMES=DSS2&OUTPUTFORMAT=application/json&SRSNAME=EPSG:3857&BBOX=7184828,4865014,7186828,4867014&TIME=2026-09-01/2026-09-28&MAXCC=30&MAXFEATURES=100") -UseBasicParsing).Content
```

```
$answer | ConvertFrom-Json | Select-Object -ExpandProperty features | ForEach-Object { $_.properties.date } | Sort-Object -Unique
```

Ожидается несколько дат сентября 2026 вида `2026-09-24`, по одной в строке
(снимок раз в 2–5 суток, без облаков). Пусто или красная ошибка — показать
начало ответа (идентификатора в нём нет):

```
$answer.Substring(0, [Math]::Min(600, $answer.Length))
```

**Copernicus — отдаст ли WFS дату браузеру** (CORS). Дату снимка
спрашивает не сервер, а браузер оператора со страницы боевого адреса, и
браузер отдаст ответ странице, только если в нём есть заголовок
`Access-Control-Allow-Origin`. Тот же запрос с заголовком `Origin`, как у
браузера:

```
$wfs = Invoke-WebRequest ("https://sh.dataspace.copernicus.eu/ogc/wfs/" + $copernicus[0] + "?SERVICE=WFS&REQUEST=GetFeature&VERSION=2.0.0&TYPENAMES=DSS2&OUTPUTFORMAT=application/json&SRSNAME=EPSG:3857&BBOX=7184828,4865014,7186828,4867014&TIME=2026-09-01/2026-09-28&MAXCC=30&MAXFEATURES=100") -Headers @{ Origin = 'http://10.103.25.14:5050' } -UseBasicParsing
```

```
$wfs.Headers.GetEnumerator() | Where-Object { $_.Key -like 'Access-Control-*' } | Format-List Key, Value
```

Ожидается строка `Key : Access-Control-Allow-Origin` и значение `*` или
`http://10.103.25.14:5050`. Пусто — браузер дату не получит: слой всё равно
работает (окно дат), а под картой будет написано, что дата не определилась.

**Copernicus — сам снимок поля** (одна картинка 512×512 — около одной
единицы обработки из 10 000):

```
Invoke-WebRequest ("https://sh.dataspace.copernicus.eu/ogc/wms/" + $copernicus[0] + "?SERVICE=WMS&REQUEST=GetMap&VERSION=1.3.0&LAYERS=" + $layer + "&CRS=EPSG:3857&BBOX=7184828,4865014,7186828,4867014&WIDTH=512&HEIGHT=512&FORMAT=image/jpeg&TIME=2026-09-01/2026-09-28&MAXCC=30&PRIORITY=mostRecent&SHOWLOGO=false") -OutFile C:\gps-tools\check\sentinel_field.jpg -UseBasicParsing
```

```
(Get-Item C:\gps-tools\check\sentinel_field.jpg).Length
```

Ожидается число больше 10 000 (байт); в файле — снимок местности 2×2 км
вокруг поля 3208 в естественных цветах (`Start-Process
C:\gps-tools\check\sentinel_field.jpg` откроет его). Красная ошибка —
чаще всего слой называется не так (шаг 2): его имя из «Configuration
Utility» — второй строкой файла, и повторить с команды `$copernicus = ...`.
Оба снимка (`esri_tile.jpg`, `sentinel_field.jpg`) ключей не содержат, их
можно пересылать.

## 12. Площадка: экран «Факт по технике» до мержа (A1+A2)

Решение владельца 30.09 (путь 2): экран проверяется на площадке **до**
мержа, чтобы строка GPS в `docs/RELEASE_GATE.md` не закрывала прод-деплой
остальным трекам. Площадка — `C:\transport-report-staging`, служба
`TransportReportStaging`, порт 5051.

**Ревизия кода:** `4a5d16ac2b9ba0c29a1582b0e1b7c78cfd2d3aed` (ветка
`claude/gps-plan-fakt-vehicle-9nt03a`, `main` на 30.09 влит). Следом в
ветке идут только документы и тесты ранбука — кода они не меняют.

**Данные.** Экран имеет смысл только на настоящих данных, поэтому база
площадки на время проверки заменяется согласованной копией боевой (снимок
SQLite online backup, боевые файлы только читаются), а рядом кладутся копии
помесячных файлов точек за окно догона — 30 суток до вчера. Собственная база
площадки и её файлы точек сохраняются и возвращаются блоком «Вернуть
площадку». Миграция, которой нет на проде, одна — `migrate_agro_work_001.py`
(agro-work, #149, в `main`); у GPS миграций нет.

**Почему блок требует остановленных ботов площадки.** Уведомления в Telegram
отправляют отдельные службы ботов (очередь `bot003_notification_outbox`), не
сайт. Запущенный бот площадки на копии боевой базы разослал бы настоящим
людям то, что лежит в боевой очереди. Поэтому блок останавливается, если
любая другая служба `*Staging*` работает, а после проверки база площадки
возвращается.

### Перед выкладкой: остановить ботов площадки

На площадке работают две службы ботов — `TransportBot003Staging` и
`TransportBotStaging` (30.09.2026: обе запущены, запуск «Automatic»). Блок
выкладки с работающими ботами не идёт. Их нужно остановить и перевести на
ручной запуск, чтобы перезагрузка сервера не подняла их на копии боевой базы,
пока идёт проверка:

```
Set-Service -Name TransportBot003Staging -StartupType Manual
```

```
Set-Service -Name TransportBotStaging -StartupType Manual
```

```
Stop-Service -Name TransportBot003Staging -Force
```

```
Stop-Service -Name TransportBotStaging -Force
```

```
Get-Service -Name TransportBot003Staging, TransportBotStaging | Format-Table Name, Status, StartType -AutoSize
```

Ожидается: у обеих служб `Stopped` и `Manual`.

### Выложить (владелец, SRV-YOQSH, PowerShell от администратора)

Скопировать целиком и вставить. Блок останавливается на первой неудаче;
`STEP=PASS` последней строкой — только когда прошли все шаги. Займёт около
15 минут: копии баз и догон A1 (около 8 минут) — самое долгое.

```powershell
& {
  $ErrorActionPreference = 'Stop'
  $expectedHost = 'srv-yoqsh'
  $root         = 'C:\transport-report-staging'
  $prodInstance = 'C:\transport-report\instance'
  $service      = 'TransportReportStaging'
  $python       = 'C:\Program Files\Python314\python.exe'
  $geoPython    = 'C:\gps_venv\Scripts\python.exe'
  $branch       = 'claude/gps-plan-fakt-vehicle-9nt03a'
  $sha          = '4a5d16ac2b9ba0c29a1582b0e1b7c78cfd2d3aed'
  $runRoot      = 'D:\transport-report-backups\staging\gps_a1a2'
  $backupDir    = Join-Path $runRoot (Get-Date -Format 'yyyyMMdd_HHmmss')

  $open = @(Get-ChildItem $runRoot -Directory -ErrorAction SilentlyContinue | Where-Object { -not (Test-Path (Join-Path $_.FullName 'returned.txt')) })
  if ($open.Count -gt 0) { throw "STEP FAILED: run $($open[0].Name) was not returned -- run the block 'Vernut ploshchadku' first" }

  if ((hostname) -ne $expectedHost) { throw "STEP FAILED: host is $(hostname), expected $expectedHost" }
  if ($root -notlike '*transport-report-staging*') { throw "STEP FAILED: refusing a root that is not the staging checkout" }
  if (-not (Test-Path "$root\instance\transport.db")) { throw "STEP FAILED: staging database not found under $root" }
  if (-not (Test-Path "$prodInstance\transport.db")) { throw "STEP FAILED: production database not found under $prodInstance" }
  if (-not (Test-Path $geoPython)) { throw "STEP FAILED: geo python not found at $geoPython" }
  $svc = Get-Service -Name $service
  if ($svc.Name -eq 'TransportReport') { throw "STEP FAILED: that is the production service" }
  $others = @(Get-Service | Where-Object { $_.Name -like '*Staging*' -and $_.Name -ne $service })
  foreach ($o in $others) { Write-Output ("OTHER_STAGING_SERVICE=" + $o.Name + " " + $o.Status + " " + $o.StartType) }
  $running = @($others | Where-Object { $_.Status -ne 'Stopped' })
  if ($running.Count -gt 0) { throw "STEP FAILED: stop these staging services first, they would read the production copy: $(($running | ForEach-Object { $_.Name }) -join ', ')" }
  Write-Output ("SERVICE_BEFORE=" + $svc.Status)

  Set-Location $root
  $before = (git rev-parse HEAD)
  Write-Output ("BEFORE_HEAD=" + $before)
  $changed = @(git status --porcelain --untracked-files=no)
  if ($changed.Count -gt 0) { throw "STEP FAILED: $($changed.Count) tracked file(s) changed in the staging checkout -- send the output of git status" }
  git fetch origin
  if ($LASTEXITCODE -ne 0) { throw "STEP FAILED: git fetch" }
  git merge-base --is-ancestor $before origin/main
  if ($LASTEXITCODE -ne 0) { throw "STEP FAILED: staging runs $before, which is not in main (on: $((git branch -r --contains $before) -join ', ')) -- someone may still use staging; send this line" }
  git cat-file -e "$sha^{commit}"
  if ($LASTEXITCODE -ne 0) { throw "STEP FAILED: commit $sha not found after fetch" }
  git merge-base --is-ancestor $sha "origin/$branch"
  if ($LASTEXITCODE -ne 0) { throw "STEP FAILED: $sha is not on origin/$branch" }

  $yesterday = (Get-Date).Date.AddDays(-1)
  $months = @($yesterday.AddDays(-29).ToString('yyyyMM'), $yesterday.ToString('yyyyMM')) | Select-Object -Unique
  $points = @($months | ForEach-Object { "$prodInstance\gps_points_$_.db" } | Where-Object { Test-Path $_ })
  if ($points.Count -eq 0) { throw "STEP FAILED: no point files for $($months -join ', ') in $prodInstance" }
  Write-Output ("POINT_FILES=" + (($points | ForEach-Object { Split-Path $_ -Leaf }) -join ', '))
  $prodBytes = (Get-Item "$prodInstance\transport.db").Length
  $stagingBytes = (Get-Item "$root\instance\transport.db").Length
  $pointBytes = ($points | ForEach-Object { (Get-Item $_).Length } | Measure-Object -Sum).Sum
  $freeC = (Get-PSDrive -Name C).Free
  $freeD = (Get-PSDrive -Name D).Free
  Write-Output ("SIZES_MB prod=" + [math]::Round($prodBytes / 1MB) + " staging=" + [math]::Round($stagingBytes / 1MB) + " points=" + [math]::Round($pointBytes / 1MB) + " freeC=" + [math]::Round($freeC / 1MB) + " freeD=" + [math]::Round($freeD / 1MB))
  if ($freeD -lt 1.2 * ($prodBytes + $stagingBytes + 2 * $pointBytes)) { throw "STEP FAILED: not enough free space on D:" }
  if ($freeC -lt 1.2 * ($prodBytes + $pointBytes)) { throw "STEP FAILED: not enough free space on C:" }

  New-Item -ItemType Directory -Force -Path $backupDir | Out-Null
  Set-Content -Path "$backupDir\before_head.txt" -Value $before -Encoding ASCII
  $copies = @(
    @{ Name = 'staging_before'; Source = "$root\instance\transport.db" },
    @{ Name = 'prod_copy'; Source = "$prodInstance\transport.db" }
  ) + @($points | ForEach-Object { @{ Name = ((Split-Path $_ -Leaf) -replace '\.db$', ''); Source = $_ } })
  foreach ($c in $copies) {
    $dir = Join-Path $backupDir $c.Name
    New-Item -ItemType Directory -Force -Path $dir | Out-Null
    $out = & $python backup_transport_db.py --source $c.Source --dest-dir $dir --suffix $c.Name | Out-String
    if (($LASTEXITCODE -ne 0) -or ($out -notmatch 'Integrity check : ok')) { throw "STEP FAILED: copy of $($c.Source)" }
    $file = Get-ChildItem $dir -Filter '*.db' | Select-Object -First 1
    Write-Output ("COPY " + $c.Name + " = " + $file.FullName + " MB=" + [math]::Round($file.Length / 1MB) + " integrity=ok")
  }

  git checkout --detach $sha
  if ($LASTEXITCODE -ne 0) { throw "STEP FAILED: git checkout" }
  Write-Output ("AFTER_HEAD=" + (git rev-parse HEAD))
  & $python -m compileall -q .
  if ($LASTEXITCODE -ne 0) { throw "STEP FAILED: compileall" }
  & $python -m unittest tests.test_gps_fact_map tests.test_gps_fact_screen tests.test_gps_exclusion tests.test_gps_track_only_days
  if ($LASTEXITCODE -ne 0) { throw "STEP FAILED: GPS tests" }

  Stop-Service -Name $service -Force
  (Get-Service -Name $service).WaitForStatus('Stopped', (New-TimeSpan -Seconds 90))
  Write-Output ("SERVICE_STOPPED=" + (Get-Service -Name $service).Status)
  & $python tools\check_db_lock.py --db "$root\instance\transport.db"
  $lock = $LASTEXITCODE
  if ($lock -eq 2) { throw "STEP FAILED: another process holds the staging database (exit 2)" }
  Write-Output ("DB_LOCK_EXIT=$lock (0 clean, 3 stale WAL -- both fine: the file is replaced)")

  Set-Content -Path "$backupDir\swapped.txt" -Value 'staging database and point files replaced' -Encoding ASCII
  $keep = Join-Path $backupDir 'staging_points_before'
  New-Item -ItemType Directory -Force -Path $keep | Out-Null
  foreach ($c in $copies) {
    if ($c.Name -eq 'staging_before') { continue }
    $target = if ($c.Name -eq 'prod_copy') { "$root\instance\transport.db" } else { "$root\instance\$($c.Name).db" }
    if (($c.Name -ne 'prod_copy') -and (Test-Path $target)) { Move-Item $target $keep -Force }
    foreach ($side in @("$target-wal", "$target-shm")) { if (Test-Path $side) { Remove-Item $side -Force } }
    $file = Get-ChildItem (Join-Path $backupDir $c.Name) -Filter '*.db' | Select-Object -First 1
    Copy-Item $file.FullName $target -Force
    Write-Output ("PLACED " + $target)
  }
  foreach ($k in @('esri_api_key.txt', 'copernicus_instance_id.txt')) {
    if (Test-Path "$prodInstance\$k") { Copy-Item "$prodInstance\$k" "$root\instance\$k" -Force; Write-Output "KEY_FILE_COPIED=$k" }
    else { Write-Output "KEY_FILE_MISSING=$k" }
  }

  & $python migrate_agro_work_001.py
  if ($LASTEXITCODE -ne 0) { throw "STEP FAILED: migrate_agro_work_001" }
  & $python migrate_agro_work_001.py
  if ($LASTEXITCODE -ne 0) { throw "STEP FAILED: migrate_agro_work_001 second run (must print 'Already applied. Nothing to do.')" }
  $ErrorActionPreference = 'Continue'
  & $python tools\check_migration_drift.py --db "$root\instance\transport.db" > "$backupDir\drift.log" 2>&1
  $driftExit = $LASTEXITCODE
  $ErrorActionPreference = 'Stop'
  Write-Output ("DRIFT_EXIT=$driftExit (not judged here; the report is drift.log in the run folder)")

  $ErrorActionPreference = 'Continue'
  & $geoPython -m gps.daily --catch-up --db "$root\instance\transport.db" --dir "$root\instance" > "$backupDir\catchup_out.log" 2> "$backupDir\catchup_err.log"
  $catchExit = $LASTEXITCODE
  $ErrorActionPreference = 'Stop'
  Get-Content "$backupDir\catchup_out.log" | Select-String -Pattern 'catch-up|category rule|NE POSCHITANO' | ForEach-Object { Write-Output ("CATCHUP: " + $_.Line) }
  if ($catchExit -ne 0) { throw "STEP FAILED: catch-up exit $catchExit -- send $backupDir\catchup_out.log and catchup_err.log" }

  Start-Service -Name $service
  (Get-Service -Name $service).WaitForStatus('Running', (New-TimeSpan -Seconds 90))
  Start-Sleep -Seconds 8
  $login = Invoke-WebRequest -Uri 'http://10.103.25.14:5051/login' -UseBasicParsing -TimeoutSec 30
  if ($login.StatusCode -ne 200) { throw "STEP FAILED: smoke /login returned $($login.StatusCode)" }
  if ($login.Content -notmatch 'vs-login-form') { throw "STEP FAILED: smoke /login did not render the login form" }
  Write-Output "SMOKE_LOGIN=200"
  Write-Output ("FINAL_HEAD=" + (git rev-parse HEAD))
  Write-Output ("SERVICE_FINAL=" + (Get-Service -Name $service).Status)
  Write-Output "STEP=PASS"
}
```

**Прислать:** весь вывод блока. Ключевые строки: `BEFORE_HEAD`, `POINT_FILES`,
`SIZES_MB`, четыре-пять строк `COPY ... integrity=ok`, `AFTER_HEAD`,
`DB_LOCK_EXIT`, `DRIFT_EXIT`, строки `CATCHUP:` (среди них `category rule --
N day(s) now track only`), `SMOKE_LOGIN=200`, `STEP=PASS`.

**Если блок остановился.** До строки `SERVICE_STOPPED` база и файлы площадки
не менялись (после `AFTER_HEAD` переключена только ревизия кода); после неё
служба остаётся остановленной намеренно. В обоих случаях — прислать вывод и
вернуть площадку блоком «Вернуть площадку»; выкладку можно повторить только
после него.

### Проверить экран (владелец, браузер)

1. `http://10.103.25.14:5051` — войти своей обычной (боевой) учётной
   записью: база площадки — копия боевой.
2. «Факт по технике» за **вчерашний** день, в списке — полевой трактор с
   гектарами. Ожидается: карта с треком, номерами участков (те же, что в
   таблице) и контуром поля; справа вверху переключатель слоёв — «Спутник
   (чёткий, Esri)» (открыт), «Свежий снимок (Sentinel-2, 10 м)», «Карта»;
   под картой строка «Свежий снимок Sentinel-2 — от ДД.ММ.ГГГГ…». Включить
   свежий снимок — появляется 10-метровый снимок той же местности.
3. В списке — спецтехника, например Isuzu 260 JAA. Ожидается: «Спецтехника
   — гектары не считаются», пробег и время в движении есть, участков нет,
   трек на карте есть.
4. В выпадающем списке машины названы вместе с госномером.

**Прислать:** по каждому пункту — да или нет; строку под картой из пункта 2
целиком; два снимка экрана (пункты 2 и 3).

### Вернуть площадку (после проверки или после остановки блока)

Возвращает то, что заменил незакрытый прогон в
`D:\transport-report-backups\staging\gps_a1a2`: базу площадки и её файлы
точек (если прогон дошёл до замены — метка `swapped.txt`) и ревизию кода.
Прогон помечается `returned.txt`; пока метки нет, блок выкладки второй раз
не запустится — иначе его «база до выкладки» была бы уже копией боевой.
Файлы ключей подложек остаются.

```powershell
& {
  $ErrorActionPreference = 'Stop'
  $root    = 'C:\transport-report-staging'
  $service = 'TransportReportStaging'
  $runRoot = 'D:\transport-report-backups\staging\gps_a1a2'
  $open = @(Get-ChildItem $runRoot -Directory -ErrorAction SilentlyContinue | Where-Object { -not (Test-Path (Join-Path $_.FullName 'returned.txt')) } | Sort-Object Name)
  if ($open.Count -eq 0) { throw "STEP FAILED: no run to return under $runRoot" }
  if ($open.Count -gt 1) { throw "STEP FAILED: $($open.Count) runs are open ($(($open | ForEach-Object { $_.Name }) -join ', ')) -- send this line" }
  $run = $open[0]
  Write-Output ("RUN=" + $run.Name)
  if ((Get-Service -Name $service).Status -ne 'Stopped') {
    Stop-Service -Name $service -Force
    (Get-Service -Name $service).WaitForStatus('Stopped', (New-TimeSpan -Seconds 90))
  }
  Set-Location $root
  if (Test-Path (Join-Path $run.FullName 'swapped.txt')) {
    $backup = Get-ChildItem (Join-Path $run.FullName 'staging_before') -Filter '*.db' | Select-Object -First 1
    if (-not $backup) { throw "STEP FAILED: the run replaced the database but has no staging backup -- send this line" }
    foreach ($side in @("$root\instance\transport.db-wal", "$root\instance\transport.db-shm")) { if (Test-Path $side) { Remove-Item $side -Force } }
    Copy-Item $backup.FullName "$root\instance\transport.db" -Force
    Write-Output ("DATABASE_RETURNED=" + $backup.Name)
    foreach ($d in @(Get-ChildItem $run.FullName -Directory | Where-Object { $_.Name -like 'gps_points_*' })) {
      $target = "$root\instance\$($d.Name).db"
      foreach ($f in @($target, "$target-wal", "$target-shm")) { if (Test-Path $f) { Remove-Item $f -Force } }
      Write-Output ("REMOVED " + $target)
    }
    $kept = Join-Path $run.FullName 'staging_points_before'
    if (Test-Path $kept) { Get-ChildItem $kept -Filter '*.db' | ForEach-Object { Move-Item $_.FullName "$root\instance" -Force; Write-Output ("RESTORED " + $_.Name) } }
  } else {
    Write-Output "DATABASE_UNTOUCHED (the run stopped before the swap)"
  }
  $headFile = Join-Path $run.FullName 'before_head.txt'
  if (Test-Path $headFile) {
    $before = (Get-Content $headFile -TotalCount 1).Trim()
    git checkout --detach $before
    if ($LASTEXITCODE -ne 0) { throw "STEP FAILED: git checkout $before" }
  }
  Start-Service -Name $service
  (Get-Service -Name $service).WaitForStatus('Running', (New-TimeSpan -Seconds 90))
  Set-Content -Path (Join-Path $run.FullName 'returned.txt') -Value ((Get-Date -Format s) + ' ' + (git rev-parse HEAD)) -Encoding ASCII
  Write-Output ("RESTORED_HEAD=" + (git rev-parse HEAD))
  Write-Output "STEP=PASS"
}
```

**Прислать:** вывод блока; ключевые строки `RUN=`, `RESTORED_HEAD` (равна
`BEFORE_HEAD` выкладки), `STEP=PASS`. Копии в прогоне на `D:` остаются —
удалить их можно вручную, когда проверка закрыта.

### После возврата: вернуть ботов площадки

Только после `STEP=PASS` блока «Вернуть площадку» — база площадки снова
своя:

```
Set-Service -Name TransportBot003Staging -StartupType Automatic
```

```
Set-Service -Name TransportBotStaging -StartupType Automatic
```

```
Start-Service -Name TransportBot003Staging
```

```
Start-Service -Name TransportBotStaging
```

```
Get-Service -Name TransportBot003Staging, TransportBotStaging | Format-Table Name, Status, StartType -AutoSize
```

Ожидается: у обеих служб `Running` и `Automatic`.
