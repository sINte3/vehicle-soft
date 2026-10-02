# DRONE-FIELD-PASSPORT-001 — поля DJI и паспорт работы

Цепочка, которую теперь видно в модуле Дронов целиком:

```
поле DJI → привязанные вылеты → DJI RAW → исключено Area Control → принято
         → почему → доказательства → историческая граница → решение админа
```

Три экрана только для чтения поверх уже существующего слоя доказательств DJI
и инструмент переписи. **Ни одной миграции, ни одной новой таблицы, ни одной
новой колонки.** Принятая площадь — только `dji_area/accepted.py` через
`control_store.accepted_for`, та же, что в рабочих отчётах (PR #152). Карты в
этом инкременте нет.

## 1. Цель

Владелец хочет открыть поле и увидеть, какие работы на нём подтверждены,
сколько гектаров принято и почему; открыть вылет и увидеть, к какому полю он
привязан, на какой версии границы, с какими доказательствами и решениями.
До этого инкремента всё это лежало в базе (`dji_field_attributions`,
`dji_land_*`, `dji_area_calculations`, `drone_area_decisions`), но экрана,
который связывает поле с принятой площадью, не было.

## 2. Предметная семантика

* **Запись поля DJI = `land_uuid`.** Это запись каталога DJI SmartFarm, а не
  «поле на земле». Экран называет её «Поле DJI», но всегда показывает UUID,
  серийный номер, внешние id и версии границы.
* **UUID не сливаются автоматически.** Одна и та же земля может быть в DJI
  несколькими записями (пересинхронизация delete+create, копия поля). Это
  видно как диагностика «та же граница у других записей DJI», но итоги
  записей не объединяются.
* **Текущая ревизия записи — последняя НАБЛЮДАВШАЯСЯ, а не с наибольшим
  id.** `store.upsert_land_revision` на повторе уже известного содержимого
  новой строки не создаёт, а двигает `last_seen_snapshot_id` у старой: после
  A → B → A текущая — A с меньшим id. Единственное место выбора —
  `field_store._latest_order` (`last_seen_snapshot_id DESC, id DESC`, тот же
  порядок, что у резолвера `field._latest_revision`); им пользуются шапка
  карточки, список и подписи ссылок (`land_header`, `land_list`,
  `fields_of_flights`). REVIEW-FIX-1.
* **Площадь каталога DJI — справочно.** Она в mu (15 mu = 1 га), её показывает
  кабинет DJI; к принятой площади работ она отношения не имеет и в итоги не
  входит.
* **Принятая площадь — техническая площадь контроля, а не площадь к
  оплате.** Решение владельца 30.09.2026 остаётся в силе.

## 3. Правило членства

Вылет входит в **подтверждённый итог** записи поля DJI тогда и только тогда,
когда одновременно:

1. его **текущая** строка привязки (`dji_field_attributions`,
   `superseded_at IS NULL`, действующая версия резолвера
   `dji-field-tiers-2026-09-08-impl-1`);
2. несёт `field_land_uuid` = запрашиваемая запись;
3. и подтверждённое состояние: `EXACT` или `IDENTIFIED`.

Проверка двойная: выборка `field_store.field_flights` стоит на
`field_land_uuid = ?` (индекс `ix_dji_field_attributions_land`), а
`field_store.confirmed_members` ещё раз сверяет запись и состояние прямо
перед итогом.

**`geometry_md5` ключом членства не является.** Он служит только версией
границы, происхождением, родословной, навигацией и диагностикой «та же
граница у N записей DJI».

**Текущесть — по `superseded_at`, а не по порядку id.** Пересчёт с прежним
входом реактивирует старую строку с её id, поэтому текущая строка бывает
старше замещённой; тест `F_A_REACTIVATED` держит это.

## 4. Состояния привязки

Переводит записанный результат резолвера в слова — `dji_area/field_view.py`
(чистый модуль, без ввода-вывода). Резолвер и его строки не меняются.

| Состояние | Откуда | В подтверждённом итоге | Что видит человек |
|---|---|---|---|
| `EXACT` | `TIER1_EXACT` | да | «Подтверждено, граница сохранена»: байты границы сохранены и сверены по md5 |
| `IDENTIFIED` | `TIER2_STRONG` | да | «Подтверждено; историческая граница не сохранена». Сегодняшняя граница за историческую не выдаётся. Если сверенные байты той же границы пришли ПОСЛЕ расчёта привязки — «Подтверждено; граница получена после расчёта привязки», нужен пересчёт (раздел 6) |
| `PROBABLE` | `TIER3_SUPPORTED` | **нет** | «Предположительно»: опора на другие вылеты. Отдельная таблица на карточке поля |
| `CANDIDATE` | `TIER4_GEOMETRIC` | **нет** | «Геометрический кандидат»: только диагностика. В штатном цикле TIER4 не пишется (только `--with-geometric`) |
| `AMBIGUOUS` | TIER5 с методом `*_LINEAGE_CONFLICT` или предупреждением `LINEAGE_CONFLICT` | **нет** | «Неоднозначно», нужна проверка человеком |
| `UNRESOLVED` | TIER5 и всё незнакомое | нет | «Поле не определено» + причина (ниже) |
| `NOT_RESOLVED` | текущей строки привязки нет | нет | «Привязка не рассчитана» (вылет вне периода расчётов) |

Причины `UNRESOLVED`:

| Причина | Как определяется | Лечится |
|---|---|---|
| `NO_CARD` | метод `AUTO_NO_KEY`/`MANUAL_NO_KEY` и `dji_flight_evidence.card_revision_id IS NULL` (или строки доказательств нет) | сбором карточки |
| `NO_KEY` | тот же метод, карточка собрана, `card_geometry_md5` пуст | ничем: DJI ключа не дал (для `MANUAL_NO_KEY` — «ручной режим») |
| `KEY_AFTER_RESOLUTION` | тот же метод, но в карточке ключ ЕСТЬ — пришёл после расчёта привязки | пересчётом |
| `NOT_IN_CATALOG` | метод `*_NOT_IN_CATALOG` | снимком каталога |
| `UNPARSED` | метод `UNPARSED_KEY` | разбором формата ключа |
| `OTHER` | всё прочее, включая незнакомый уровень | — |

**Незнакомый уровень не повышается до подтверждения**, а становится
`UNRESOLVED / OTHER`. «Нет расчёта площади» — свойство площади, а не поля:
его даёт `dji_area.accepted` («не рассчитано»), в состояниях привязки его нет.

## 5. Без двойного счёта

* Итог записи поля строится только из её подтверждённых членов (раздел 3).
  Вылет с одной текущей строкой привязки несёт один `field_land_uuid` —
  значит, в подтверждённый итог входит не больше одной записи.
* Вылеты на ТЕХ ЖЕ границах (тот же md5), чья текущая привязка несёт
  `field_land_uuid` ДРУГОЙ записи, на карточке — отдельной таблицей «Вылеты
  на тех же границах, привязанные к другим записям DJI», **без гектаров**,
  со своим состоянием (EXACT, IDENTIFIED, PROBABLE, CANDIDATE) и ссылкой на
  запись. Вылет без `field_land_uuid` (поле не определено, противоречие
  родства) в этот список не попадает: совпадение md5 не делает его «учтённым
  в другой записи» — он не учтён нигде (REVIEW-FIX-1). Паспорт такого вылета
  говорит «Эту границу держат записи DJI … в подтверждённые гектары ни одной
  из этих записей вылет не входит».
* Итог карточки — `accepted.summarize` по тем же результатам
  `accepted_for`, что у рабочих отчётов. Своей арифметики площади в экранах
  нет.

## 6. Историческая граница

Граница вылета — `geometry_md5` его строки привязки, то есть версия, которую
назвала **карточка этого вылета**, а не последняя ревизия записи. Вылет
августа на V1 показывает V1, даже когда последняя ревизия записи — V2
(проверено тестом `test_the_august_passport_keeps_the_august_boundary` с
отрицательным контролем на сентябрьском вылете).

Если байтов той версии нет (`IDENTIFIED`), паспорт пишет «историческая
граница не сохранена» и сегодняшнюю границу вместо неё не подставляет.

**Байты, пришедшие после расчёта привязки (REVIEW-FIX-1).** Состояние —
вывод последнего расчёта резолвера; наличие байтов — то, что лежит в
`dji_land_geometries` сейчас. Это разные вещи, и экран их не смешивает:

| Резолвер записал | Сейчас в каталоге | Состояние | Подпись |
|---|---|---|---|
| TIER2, байтов нет | байтов нет | IDENTIFIED | «Подтверждено; историческая граница не сохранена» |
| TIER2, байтов нет | байты, `md5_verified=1` | IDENTIFIED (не повышается) | «Подтверждено; граница получена после расчёта привязки» + «нужен пересчёт привязки» |
| TIER2, байтов нет | байты, `md5_verified=0` | IDENTIFIED | «Подтверждено; граница получена, ещё не сверена» |

Состояние до EXACT здесь не поднимается — это дело пересчёта (отпечаток
каталога учитывает `md5_verified`, суточный цикл пересчитает). Членство и
итог поля не меняются. Предупреждение резолвера «байты исторической границы
не сохранены» при пришедших байтах заменяется на «получены после расчёта
привязки». Перепись считает такие вылеты отдельно (раздел 10).

## 7. Экраны

Все — `GET`, `@module_required('drones')`, та же модель прав, что у модуля.

| URL | Что |
|---|---|
| `GET /drones/fields` | Список записей полей DJI. Поиск по названию, серийному, UUID, внешнему id и адресу (`json_extract(raw_json,'$.address')`, только это поле). Период (по умолчанию — всё время), флажок «только с вылетами». Страница по 50 на сервере. Без геометрии и без принятой площади: это список, а не отчёт по 6 000 полей. Строка: название, серийный, площадь DJI (справочно), ревизий / версий границы, подтверждённых и предположительных вылетов периода, последний подтверждённый вылет. Над таблицей — покрытие периода: сколько вылетов, у скольких поле подтверждено, сколько «карточка не собрана» / «ключа нет» |
| `GET /drones/fields/<land_uuid>` | Карточка записи: шапка (название, серийный, UUID, внешние id, площадь каталога, первый/последний снимок), версии границы (md5, период наблюдения, байты сохранены ли, число подтверждённых вылетов, другие записи с той же границей — без их гектаров), итог по подтверждённым (плитки `_accepted.html`), подтверждённые работы (дата, машина, id DJI, RAW, исключено, принято, статус, привязка, паспорт) постранично, отдельно предположительные/кандидаты с их RAW, отдельно вылеты на тех же границах, привязанные к другим записям |
| `GET /drones/flights/<dji_flight_id>/passport` | Паспорт вылета: А. Площадь (RAW, исключено, принято, статус, годится ли как база контроля; без расчёта — «не рассчитано»), Б. Почему (класс, причина, доказательство, цепочка A→B→C — из `control_report.record_view`, без нового вердикта), В. Поле (состояние, причина, запись, название на момент привязки, md5, сохранена ли граница, другие держатели, метод и уверенность, предупреждения, снимок), Г. Решения администратора (действующее, история, кто, когда, комментарий, автомат на момент решения, устаревание), Д. Происхождение (тип, короткий SHA-256, время, разборщик ревизий источников; версия и входы расчёта; версия и входы привязки, снимок и ревизия каталога). Открывается и без расчёта, и без привязки; нужен только вылет в `drone_flights` |

Навигация: плитка «Поля DJI и паспорт работ» на `/drones/reports`; дата
вылета в «Вылетах дронов» ведёт в паспорт; кнопка «Паспорт вылета» на
странице решения администратора. Общее меню модулей
(`templates/_module_nav.html`) не тронуто, поэтому на страницах полей пункт
«Отчёты» в меню модуля не подсвечен. Страница решения администратора не
заменена: паспорт объясняет, решение записывается там.

## 8. Безопасность

Не показывается и не читается: тела источников (`body_text`), пути хранения
(`body_path`), контекст запросов (`request_context_json`), байты границ
(`body_blob`), `raw_json` ревизий каталога (кроме одного поля `address` для
поиска), `points_json`, координаты маршрута, кольца и рамки, подписанные
ссылки DJI, токены. Колонки читаются явными списками (`ATTR_COLUMNS`,
`EVIDENCE_COLUMNS`, `SOURCE_COLUMNS`, `CALC_META_COLUMNS` в
`dji_area/field_store.py`), не `SELECT *`.

Тест `Privacy` проходит все страницы сценария на двух языках и ищет в HTML
приманки посева (подписанная ссылка, токен, путь хранения, тело границы,
`points_json`, координаты узла DJI) и любую «координату района» регуляркой;
рядом отрицательный контроль — та же регулярка находит координату в
`raw_json` посева. Внешний текст (название, серийный) экранируется: тест с
`<script>` в названии поля.

## 9. Производительность

* Чтение пакетное, куски по 400 id: число запросов не зависит от числа
  вылетов. Тест `QueryCount`: карточка с 1 и со 150 вылетами — одинаковое
  число запросов к sqlite3 и к SQLAlchemy; отрицательный контроль — чтение по
  вылету счётчик видит.
* Новых индексов нет. Планы запросов (`EXPLAIN QUERY PLAN` на схеме из
  миграций): вылеты записи — `ix_dji_field_attributions_land`; та же граница —
  `ix_dji_field_attributions_md5`; текущая привязка вылета —
  `ix_dji_field_attributions_flight_current`; ревизии — `ix_dji_land_revisions_uuid`;
  перепись периода — `ix_drone_flights_started_at` (есть в модели), затем
  `flight_current` и уникальный индекс доказательств.
* Синтетическая база размера production (6 000 записей полей, 12 000
  ревизий, 40 000 вылетов с привязками, 30 000 расчётов), без кэша:
  список за всё время — 368 мс, за месяц — 59 мс, поиск — 270 мс; перепись за
  всё время — 146 мс, за месяц — 15 мс; карточка самой большой записи
  (2 003 вылета) — 36 мс. После REVIEW-FIX-1 (текущая ревизия — коррелированный
  подзапрос по `ix_dji_land_revisions_uuid`, сортировка 1–3 строк на запись;
  лучший из трёх): список 233 / 51 мс, поиск 257 мс, перепись 128 / 20 мс,
  шапка записи 0,1 мс, подписи 400 записей 3,4 мс. На production не
  измерялось.
* Принятая площадь для 6 000 полей не считается нигде: список её не
  показывает, карточка считает только свои вылеты.

## 10. Перепись: `tools/dji_field_census.py`

stdlib, `mode=ro`, явные `--db`, `--from`, `--to` (местные даты UTC+5
включительно). Необязательно: `--json PATH` (машиночитаемый вывод),
`--snapshot-days N` (окно свежести снимков, по умолчанию 30), `--now
YYYY-MM-DDTHH:MM` (для воспроизводимости). К DJI не обращается, ничего не
пишет, кроме JSON. Консоль — только ASCII.

Коды возврата: `0` — выполнено; `1` — ошибка аргументов; `2` — базы нет
(файл не создаётся); `3` — нет таблиц слоя доказательств.

Что выводит — по периоду и по каждому месяцу UTC+5:

* вылетов всего; с расчётом площади; с текущей привязкой;
* `CONFIRMED` = `EXACT` + `IDENTIFIED`; `PROBABLE`; `CANDIDATE`; `AMBIGUOUS`;
  `UNRESOLVED` с разбивкой `NO_CARD` / `NO_KEY` / `KEY_AFTER_RESOLUTION` /
  `NOT_IN_CATALOG` / `UNPARSED` / `OTHER`; `NOT_RESOLVED`;
* с байтами исторической границы на момент расчёта привязки; с md5 границы
  без байтов на момент расчёта; со сверенными байтами границы СЕЙЧАС
  (`boundary bytes available NOW`); из них IDENTIFIED, чьи байты пришли после
  расчёта и ждут пересчёта (`IDENTIFIED awaiting recalc`);

по каталогу: записей DJI, ревизий, различных md5, тел границ сохранено и
сверено, md5 без тела, md5 у нескольких записей (и сколько записей
затронуто); по снимкам каталога за окно: сколько, полных, в скольких
местных сутках окна был снимок, последний и последний полный.

Классификация — та же `field_view.classify`, что у экранов. Сумма состояний
равна числу вылетов (держится тестом).

Синтетический прогон сценария тестов (не production):

```
== period ==   (2026-09-01 .. 2026-09-30)
  flights total                          18
    with area calculation                 7   38.9%
    with current field attribution       17   94.4%
  CONFIRMED (EXACT + IDENTIFIED)          6   33.3%
  PROBABLE / CANDIDATE / AMBIGUOUS        1 / 1 / 1
  UNRESOLVED                              8   (NO_CARD 3, NO_KEY 2,
                                              KEY_AFTER_RESOLUTION 1,
                                              NOT_IN_CATALOG 1, UNPARSED 1)
  NOT_RESOLVED                            1
  boundary bytes available NOW            7
    IDENTIFIED awaiting recalc            0
== DJI field catalog ==
  land records 4, revisions 6, md5 shared by >1 record 1 (records 2)
```

Живых чисел здесь нет: перепись на копии production — пункт UAT.

## 11. Решения владельца

1. Будущая карта (историческая граница и маршрут) — только внутри
   авторизованного модуля Дронов, на общем `vs-map`: без публичных адресов,
   без подписанных ссылок DJI, без сырых байтов, без автоматического экспорта
   KML/GeoJSON.
2. В этом PR карты нет.
3. Полный суточный сбор карточек вылетов ещё НЕ одобрен. Перепись нужна,
   чтобы это решение принималось по числам.
4. UUID записей DJI автоматически не сливаются.
5. Ручной привязки вылета к полю пока нет.
6. Принятая площадь (Accepted Area) остаётся единственным операционным
   источником площади.

## 12. Сознательно не сделано

Карта; рисование и правка границ; экспорт KML/GeoJSON; карта 6 000 полей;
объединение UUID; ручная привязка; полный сбор карточек; уникальное
покрытие, путь×ширина, площадь полигона как площадь работ; биллинг;
автоматическое закрытие REVIEW; новые пороги; новая площадь поля; правки
резолвера, расчёта, сборщика, планировщика, `models.py`, `app.py`, общего
меню, `vs-map`, Leaflet, GPS.

## 13. Риски и открытые вопросы

* **Покрытие карточками.** Поле определяется доказательно только по ключу из
  карточки вылета. Где карточки не собраны, вылеты будут `NO_CARD`. Сколько
  их на production — первая цифра переписи. Даже при полном сборе часть
  вылетов останется без поля (`NO_KEY`, ручной режим): реплика августа
  показала порядок до половины вылетов в TIER5.
* **Свежесть снимков каталога на production не проверена.** Байты старой
  границы сохраняются, только пока снимок застал её в DJI. Перепись
  показывает число суток со снимком за 30 дней.
* **TIER2 → TIER1 не поднимается сам.** Если байты границы пришли после
  расчёта привязки, состояние обновит только пересчёт (это поведение
  резолвера, оно не менялось). До пересчёта экран пишет «граница получена
  после расчёта привязки», перепись — `IDENTIFIED awaiting recalc`.
* **Тот же класс ошибки в замороженном коде — не тронут.**
  `SqliteCatalog.current_polygons` (`dji_area/store.py`) берёт ревизию пары
  (land_uuid, md5) через MAX(id). Граница при этом та же (один md5), но имя
  и центр могут оказаться не последними после A → B → A. Используется только
  необязательным TIER4 (в штатном цикле выключен, `--with-geometric`);
  `store.py` заморожен, вне этого PR не исправлялось.
* **Паспорт требует строку в `drone_flights`.** Вылет, известный только слою
  доказательств, даёт 404 — на карточке поля такие привязки названы числом
  («Привязок к этой записи без вылета в журнале вылетов: N»).
* **Поиск `LIKE` не различает регистр только для латиницы.** Кириллица в
  названии ищется с учётом регистра (ограничение SQLite без ICU). Известное
  ограничение для UAT; ICU, новые зависимости и индекс не вводятся.
* **Поиск — по текущей ревизии записи.** Прежнее название записи (до
  переименования в DJI) поиском не находится; его видно в «Версиях границы»
  карточки.
* **Пункт «Отчёты» в меню модуля на страницах полей не подсвечен** — общее
  меню не трогалось намеренно.
* **Зонд UAT меряет время на сервере, без сети.** Время страниц у зонда —
  рендер тестовым клиентом на копии; сеть и браузер в него не входят. Время
  «как у человека» — пункт 8 просмотра в браузере (§14.5).

Открытых вопросов о бизнес-правилах у инкремента нет: состояния и членство —
из задания владельца, площадь — из провайдера.

## 14. UAT на площадке до мержа (владелец, SRV-YOQSH)

Живой UAT идёт **до мержа** PR #160, на точном коде
`e7e97f1f3193eb7e5081478d2024b19267378d37` и свежей копии production
(SQLite online backup). Production только читается, миграций нет. Порядок
блоков, их проверки и возврат площадки повторяют выкладку GPS 30.09.2026
(`docs/GPS_ROLLOUT_RUNBOOK.md` §12).

Номера полей и вылетов для проверки выдумывать не нужно. Их выбирает
запросами по живой копии зонд `tools/dji_field_passport_uat.py`. Он идёт в
ветке после `e7e97f1`, кода приложения не меняет; блок выкладки это
проверяет.

**Порядок:**

0. Смержить PR #161: строка `docs/STAGING.md` «занято: Дроны».
1. Остановить ботов площадки (14.1).
2. Выкладка и копия production (14.2).
3. Перепись (14.3).
4. Зонд (14.4).
5. Просмотр в браузере (14.5).
6. «Вернуть площадку» (14.6).
7. Вернуть ботов (14.7).
8. Освободить строку `docs/STAGING.md`. Это отдельный docs-only PR, его готовит
   сессия после отчёта.

Все блоки — PowerShell от администратора. Каждый останавливается на первой
неудаче. `STEP=PASS` последней строкой выводится только тогда, когда прошли
все шаги. Прислать надо весь вывод каждого блока.

### 14.1 Остановить ботов площадки

Сначала записать, как они стоят сейчас: в 14.7 их нужно вернуть ровно так.

```
Get-Service -Name TransportBot003Staging, TransportBotStaging | Format-Table Name, Status, StartType -AutoSize
```

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

Ожидается: у обеих служб `Stopped` и `Manual`. Причина та же, что у GPS:
запущенный бот площадки на копии боевой базы разослал бы настоящим людям то,
что лежит в боевой очереди уведомлений.

### 14.2 Выложить (около 10 минут: три копии баз и тесты)

Что делает блок:

* **До изменения площадки:**
  * записывает её состояние;
  * снимает факты production только чтением: HEAD, службы, размер базы;
  * делает две online-копии — базы площадки и production — с
    `integrity_check`;
  * кладёт рядом зонд и третью, выбрасываемую копию для страниц.
* **Потом:**
  * переключает код на `e7e97f1`;
  * прогоняет тесты на копии;
  * подменяет базу площадки копией production;
  * запускает только сайт.
* **В конце:** сверяет, что production не изменился.

```powershell
& {
  $ErrorActionPreference = 'Stop'
  $expectedHost = 'srv-yoqsh'
  $root         = 'C:\transport-report-staging'
  $prodRoot     = 'C:\transport-report'
  $service      = 'TransportReportStaging'
  $python       = 'C:\Program Files\Python314\python.exe'
  $branch       = 'claude/practical-davinci-chb4r7'
  $sha          = 'e7e97f1f3193eb7e5081478d2024b19267378d37'
  $runRoot      = 'D:\transport-report-backups\staging\field_passport_uat'
  $backupDir    = Join-Path $runRoot (Get-Date -Format 'yyyyMMdd_HHmmss')

  $open = @(Get-ChildItem $runRoot -Directory -ErrorAction SilentlyContinue | Where-Object { -not (Test-Path (Join-Path $_.FullName 'returned.txt')) })
  if ($open.Count -gt 0) { throw "STEP FAILED: run $($open[0].Name) was not returned -- run the block 'Vernut ploshchadku' first" }
  if ((hostname) -ne $expectedHost) { throw "STEP FAILED: host is $(hostname), expected $expectedHost" }
  if ($root -notlike '*transport-report-staging*') { throw "STEP FAILED: refusing a root that is not the staging checkout" }
  if (-not (Test-Path "$root\instance\transport.db")) { throw "STEP FAILED: staging database not found under $root" }
  if (-not (Test-Path "$prodRoot\instance\transport.db")) { throw "STEP FAILED: production database not found under $prodRoot" }
  $svc = Get-Service -Name $service
  if ($svc.Name -eq 'TransportReport') { throw "STEP FAILED: that is the production service" }
  $others = @(Get-Service | Where-Object { $_.Name -like '*Staging*' -and $_.Name -ne $service })
  foreach ($o in $others) { Write-Output ("OTHER_STAGING_SERVICE=" + $o.Name + " " + $o.Status + " " + $o.StartType) }
  $running = @($others | Where-Object { $_.Status -ne 'Stopped' })
  if ($running.Count -gt 0) { throw "STEP FAILED: stop these staging services first, they would read the production copy: $(($running | ForEach-Object { $_.Name }) -join ', ')" }
  Write-Output ("SERVICE_BEFORE=" + $svc.Status)
  try { $pre = Invoke-WebRequest -Uri 'http://10.103.25.14:5051/login' -UseBasicParsing -TimeoutSec 30; Write-Output ("STAGING_LOGIN_BEFORE=" + $pre.StatusCode) } catch { Write-Output ("STAGING_LOGIN_BEFORE=ERROR " + $_.Exception.Message) }

  $prodHead = (git -C $prodRoot rev-parse HEAD)
  $prodServices = (@(Get-Service | Where-Object { $_.Name -like 'Transport*' -and $_.Name -notlike '*Staging*' } | Sort-Object Name | ForEach-Object { $_.Name + '=' + $_.Status }) -join ' ')
  Write-Output ("PROD_HEAD=" + $prodHead)
  Write-Output ("PROD_SERVICES=" + $prodServices)
  Write-Output ("PROD_DB_BYTES=" + (Get-Item "$prodRoot\instance\transport.db").Length)

  Set-Location $root
  $before = (git rev-parse HEAD)
  Write-Output ("BEFORE_HEAD=" + $before)
  Write-Output ("STAGING_DB=" + "$root\instance\transport.db" + " BYTES=" + (Get-Item "$root\instance\transport.db").Length)
  $changed = @(git status --porcelain --untracked-files=no)
  if ($changed.Count -gt 0) { throw "STEP FAILED: $($changed.Count) tracked file(s) changed in the staging checkout -- send the output of git status" }
  git fetch origin
  if ($LASTEXITCODE -ne 0) { throw "STEP FAILED: git fetch" }
  git merge-base --is-ancestor $before origin/main
  if ($LASTEXITCODE -ne 0) { throw "STEP FAILED: staging runs $before, which is not in main (on: $((git branch -r --contains $before) -join ', ')) -- someone may still use staging; send this line" }
  $row = (git show origin/main:docs/STAGING.md) -join ' '
  if ($row -notmatch 'DRONE-FIELD-PASSPORT-001') { throw "STEP FAILED: docs/STAGING.md on main does not show this UAT -- merge PR #161 first" }
  Write-Output "STAGING_ROW=occupied by DRONE-FIELD-PASSPORT-001 on origin/main"
  git cat-file -e "$sha^{commit}"
  if ($LASTEXITCODE -ne 0) { throw "STEP FAILED: commit $sha not found after fetch" }
  git merge-base --is-ancestor $sha "origin/$branch"
  if ($LASTEXITCODE -ne 0) { throw "STEP FAILED: $sha is not on origin/$branch" }
  $later = @(git diff --name-only $sha "origin/$branch")
  $appFiles = @($later | Where-Object { $_ -notmatch '^(docs/|tests/|tools/dji_field_passport_uat\.py$)' })
  if ($appFiles.Count -gt 0) { throw "STEP FAILED: after $sha the branch changes application files: $($appFiles -join ', ')" }
  Write-Output ("KIT_FILES_AFTER_SHA=" + ($later -join ', '))

  $prodBytes = (Get-Item "$prodRoot\instance\transport.db").Length
  $stagingBytes = (Get-Item "$root\instance\transport.db").Length
  $freeC = (Get-PSDrive -Name C).Free
  $freeD = (Get-PSDrive -Name D).Free
  Write-Output ("SIZES_MB prod=" + [math]::Round($prodBytes / 1MB) + " staging=" + [math]::Round($stagingBytes / 1MB) + " freeC=" + [math]::Round($freeC / 1MB) + " freeD=" + [math]::Round($freeD / 1MB))
  if ($freeD -lt 1.2 * ($stagingBytes + 2 * $prodBytes)) { throw "STEP FAILED: not enough free space on D:" }
  if ($freeC -lt 1.2 * $prodBytes) { throw "STEP FAILED: not enough free space on C:" }

  New-Item -ItemType Directory -Force -Path $backupDir | Out-Null
  Set-Content -Path "$backupDir\before_head.txt" -Value $before -Encoding ASCII
  Set-Content -Path "$backupDir\prod_head.txt" -Value $prodHead -Encoding ASCII
  Set-Content -Path "$backupDir\prod_services.txt" -Value $prodServices -Encoding ASCII
  foreach ($c in @(@{ Name = 'staging_before'; Source = "$root\instance\transport.db" }, @{ Name = 'prod_copy'; Source = "$prodRoot\instance\transport.db" })) {
    $dir = Join-Path $backupDir $c.Name
    New-Item -ItemType Directory -Force -Path $dir | Out-Null
    $out = & $python backup_transport_db.py --source $c.Source --dest-dir $dir --suffix $c.Name | Out-String
    if (($LASTEXITCODE -ne 0) -or ($out -notmatch 'Integrity check : ok')) { throw "STEP FAILED: copy of $($c.Source)" }
    $file = Get-ChildItem $dir -Filter '*.db' | Select-Object -First 1
    Write-Output ("COPY " + $c.Name + " = " + $file.FullName + " BYTES=" + $file.Length + " integrity=ok")
  }

  $probeDir = Join-Path $backupDir 'probe'
  New-Item -ItemType Directory -Force -Path $probeDir | Out-Null
  $probe = Join-Path $probeDir 'dji_field_passport_uat.py'
  git show "origin/${branch}:tools/dji_field_passport_uat.py" | Set-Content -Path $probe -Encoding ASCII
  & $python -m py_compile $probe
  if ($LASTEXITCODE -ne 0) { throw "STEP FAILED: the probe did not compile after extraction" }
  $prodCopy = Get-ChildItem (Join-Path $backupDir 'prod_copy') -Filter '*.db' | Select-Object -First 1
  Copy-Item $prodCopy.FullName (Join-Path $probeDir 'page_copy.db')
  Write-Output ("PROBE=" + $probe)

  git checkout --detach $sha
  if ($LASTEXITCODE -ne 0) { throw "STEP FAILED: git checkout" }
  Write-Output ("AFTER_HEAD=" + (git rev-parse HEAD))
  & $python -m compileall -q .
  if ($LASTEXITCODE -ne 0) { throw "STEP FAILED: compileall" }
  & $python -m unittest tests.test_drone_field_passport_core tests.test_drone_field_passport_001 tests.test_dji_area_accepted_core
  if ($LASTEXITCODE -ne 0) { throw "STEP FAILED: field passport and accepted area tests" }

  Stop-Service -Name $service -Force
  (Get-Service -Name $service).WaitForStatus('Stopped', (New-TimeSpan -Seconds 90))
  Write-Output ("SERVICE_STOPPED=" + (Get-Service -Name $service).Status)
  & $python tools\check_db_lock.py --db "$root\instance\transport.db"
  $lock = $LASTEXITCODE
  if ($lock -eq 2) { throw "STEP FAILED: another process holds the staging database (exit 2)" }
  Write-Output ("DB_LOCK_EXIT=$lock (0 clean, 3 stale WAL -- both fine: the file is replaced)")
  Set-Content -Path "$backupDir\swapped.txt" -Value 'staging database replaced by the production copy' -Encoding ASCII
  foreach ($side in @("$root\instance\transport.db-wal", "$root\instance\transport.db-shm")) { if (Test-Path $side) { Remove-Item $side -Force } }
  Copy-Item $prodCopy.FullName "$root\instance\transport.db" -Force
  Write-Output ("PLACED " + "$root\instance\transport.db")
  Write-Output "MIGRATIONS=none (PR #160 has no migration)"
  $ErrorActionPreference = 'Continue'
  & $python tools\check_migration_drift.py --db "$root\instance\transport.db" > "$backupDir\drift.log" 2>&1
  $driftExit = $LASTEXITCODE
  $ErrorActionPreference = 'Stop'
  Write-Output ("DRIFT_EXIT=$driftExit (report only, drift.log in the run folder)")

  Start-Service -Name $service
  (Get-Service -Name $service).WaitForStatus('Running', (New-TimeSpan -Seconds 90))
  Start-Sleep -Seconds 8
  $login = Invoke-WebRequest -Uri 'http://10.103.25.14:5051/login' -UseBasicParsing -TimeoutSec 30
  if ($login.StatusCode -ne 200) { throw "STEP FAILED: smoke /login returned $($login.StatusCode)" }
  if ($login.Content -notmatch 'vs-login-form') { throw "STEP FAILED: smoke /login did not render the login form" }
  Write-Output "SMOKE_LOGIN=200"
  $fields = Invoke-WebRequest -Uri 'http://10.103.25.14:5051/drones/fields' -UseBasicParsing -TimeoutSec 30
  if ($fields.Content -notmatch 'vs-login-form') { throw "STEP FAILED: /drones/fields without a session did not lead to the login form" }
  Write-Output "FIELDS_ANONYMOUS=login form (route exists, sign-in required)"

  $prodHeadAfter = (git -C $prodRoot rev-parse HEAD)
  $prodServicesAfter = (@(Get-Service | Where-Object { $_.Name -like 'Transport*' -and $_.Name -notlike '*Staging*' } | Sort-Object Name | ForEach-Object { $_.Name + '=' + $_.Status }) -join ' ')
  Write-Output ("PROD_HEAD_AFTER=" + $prodHeadAfter)
  Write-Output ("PROD_SERVICES_AFTER=" + $prodServicesAfter)
  if (($prodHeadAfter -ne $prodHead) -or ($prodServicesAfter -ne $prodServices)) { throw "STEP FAILED: production head or services changed during the block -- send this output" }
  Write-Output ("FINAL_HEAD=" + (git rev-parse HEAD))
  Write-Output ("SERVICE_FINAL=" + (Get-Service -Name $service).Status)
  Write-Output ("RUN=" + $backupDir)
  Write-Output "STEP=PASS"
}
```

Ключевые строки ответа:

* до изменений: `STAGING_LOGIN_BEFORE`, `BEFORE_HEAD`, `STAGING_DB`,
  `PROD_HEAD`, `PROD_SERVICES`, `PROD_DB_BYTES`;
* проверки: `STAGING_ROW`, `KIT_FILES_AFTER_SHA`, две строки
  `COPY … integrity=ok`, `AFTER_HEAD`, `DB_LOCK_EXIT`, `DRIFT_EXIT`;
* итог: `SMOKE_LOGIN=200`, `FIELDS_ANONYMOUS`, `PROD_HEAD_AFTER`,
  `PROD_SERVICES_AFTER`, `RUN`, `STEP=PASS`.

`integrity_check` production в отчёте — по его online-копии: это
согласованный снимок той же базы. По живой базе production блок проверку не
гоняет.

**Если блок остановился.** До строки `SERVICE_STOPPED` база площадки не
менялась (после `AFTER_HEAD` переключена только ревизия кода). После неё
служба остаётся остановленной намеренно. В обоих случаях прислать вывод и
выполнить 14.6.

### 14.3 Перепись: сентябрь, август, последние 30 полных суток

Только чтение (`mode=ro`) базы площадки, то есть копии production. Вывод и
JSON ложатся в папку прогона, `census\`.

```powershell
& {
  $ErrorActionPreference = 'Stop'
  $root    = 'C:\transport-report-staging'
  $python  = 'C:\Program Files\Python314\python.exe'
  $sha     = 'e7e97f1f3193eb7e5081478d2024b19267378d37'
  $runRoot = 'D:\transport-report-backups\staging\field_passport_uat'
  $open = @(Get-ChildItem $runRoot -Directory | Where-Object { -not (Test-Path (Join-Path $_.FullName 'returned.txt')) } | Sort-Object Name)
  if ($open.Count -ne 1) { throw "STEP FAILED: expected exactly one open run under $runRoot, found $($open.Count)" }
  if (-not (Test-Path (Join-Path $open[0].FullName 'swapped.txt'))) { throw "STEP FAILED: the open run has not placed the production copy -- run 14.2 first" }
  Set-Location $root
  if ((git rev-parse HEAD) -ne $sha) { throw "STEP FAILED: staging is not on $sha" }
  $out = Join-Path $open[0].FullName 'census'
  New-Item -ItemType Directory -Force -Path $out | Out-Null
  $to30 = (Get-Date).Date.AddDays(-1)
  $from30 = $to30.AddDays(-29)
  $periods = @(
    @{ Name = '2026_09'; From = '2026-09-01'; To = '2026-09-30' },
    @{ Name = '2026_08'; From = '2026-08-01'; To = '2026-08-31' },
    @{ Name = 'last30'; From = $from30.ToString('yyyy-MM-dd'); To = $to30.ToString('yyyy-MM-dd') }
  )
  foreach ($p in $periods) {
    $json = Join-Path $out ('census_' + $p.Name + '.json')
    $started = Get-Date
    $text = & $python tools\dji_field_census.py --db "$root\instance\transport.db" --from $p.From --to $p.To --json $json | Out-String
    if ($LASTEXITCODE -ne 0) { throw "STEP FAILED: census $($p.Name) exit $LASTEXITCODE" }
    $seconds = [math]::Round(((Get-Date) - $started).TotalSeconds, 1)
    Set-Content -Path (Join-Path $out ('census_' + $p.Name + '.txt')) -Value $text -Encoding ASCII
    Write-Output $text
    Write-Output ("CENSUS_SECONDS " + $p.Name + "=" + $seconds)
  }
  Write-Output ("CENSUS_DIR=" + $out)
  Write-Output "STEP=PASS"
}
```

### 14.4 Зонд: случаи, сверка итогов, двойной счёт, история, страницы, время

Часть данных зонд выполняет только чтением базы площадки. Страницы он рисует
тестовым клиентом на `page_copy.db`, выбрасываемой копии в папке прогона.
Импорт приложения пишет в эту копию (WAL, язык админа), поэтому в папке
`transport-report*` зонд работать отказывается.

Код выхода зонда: `0` — все ворота прошли; `4` — упали ворота-блокеры
(строки `GATE … FAIL BLOCKER`). Оба исхода — ответ: блок падает только на
неожиданном коде.

```powershell
& {
  $ErrorActionPreference = 'Stop'
  $root    = 'C:\transport-report-staging'
  $python  = 'C:\Program Files\Python314\python.exe'
  $sha     = 'e7e97f1f3193eb7e5081478d2024b19267378d37'
  $runRoot = 'D:\transport-report-backups\staging\field_passport_uat'
  $open = @(Get-ChildItem $runRoot -Directory | Where-Object { -not (Test-Path (Join-Path $_.FullName 'returned.txt')) } | Sort-Object Name)
  if ($open.Count -ne 1) { throw "STEP FAILED: expected exactly one open run under $runRoot, found $($open.Count)" }
  if (-not (Test-Path (Join-Path $open[0].FullName 'swapped.txt'))) { throw "STEP FAILED: the open run has not placed the production copy -- run 14.2 first" }
  Set-Location $root
  if ((git rev-parse HEAD) -ne $sha) { throw "STEP FAILED: staging is not on $sha" }
  $probeDir = Join-Path $open[0].FullName 'probe'
  $probe = Join-Path $probeDir 'dji_field_passport_uat.py'
  $pageCopy = Join-Path $probeDir 'page_copy.db'
  if (-not (Test-Path $probe)) { throw "STEP FAILED: probe not found at $probe" }
  $ErrorActionPreference = 'Continue'
  & $python $probe --db "$root\instance\transport.db" --out-dir $probeDir --page-copy $pageCopy 2> (Join-Path $probeDir 'probe_stderr.log')
  $code = $LASTEXITCODE
  $ErrorActionPreference = 'Stop'
  Write-Output ("PROBE_EXIT=$code (0 all gates passed, 4 a blocker gate failed)")
  Write-Output ("PROBE_DIR=" + $probeDir)
  if (($code -ne 0) -and ($code -ne 4)) { throw "STEP FAILED: probe exit $code -- send $probeDir\probe_stderr.log" }
  Write-Output "STEP=PASS"
}
```

Прислать весь вывод (строки `DATA`, `CASE`, `GATE`, `CARD`, `LIST`,
`TIME`, `PAGE`, `RESULT`). Файл `probe\uat_report.json` — по возможности
тоже: в нём названия полей и числа по каждой странице.

### 14.5 Просмотр в браузере (владелец)

`http://10.103.25.14:5051` — войти своей обычной боевой учётной записью: база
площадки — копия боевой. Номера взять из строк `CASE` зонда.

1. «Дроны» → «Отчёты» → «Поля DJI и паспорт работ». Список открылся. Период
   01.09.2026–30.09.2026, флажок «только с вылетами».
2. Поиск по UUID из `CASE exact_field` → одна строка. Поиск по части
   названия этого поля → поле в списке. Затем то же название буквами другого
   регистра: если поле не нашлось — это известное ограничение (кириллица
   ищется с учётом регистра), записать и не чинить.
3. Карточка этого поля: плитки «DJI RAW / Принято / Исключено» равны строке
   `CARD A` зонда (до двух знаков), «Всего: N» = `confirmed` из `CARD A`.
4. Паспорта вылетов из `CASE normal_flight`, `corrected_flight`,
   `review_flight`, `no_calc_flight` (или `no_card_flight`),
   `historical_bytes_flight` — адрес `/drones/flights/<номер>/passport`.
   * Статус площади соответствует названию случая.
   * У `no_calc_flight` в «Принято» — «не рассчитано», без числа.
   * Разделы «Почему» и «Решения администратора» совпадают со страницей
     решения («Площадь и решение»).
5. Если есть `CASE awaiting_recalc_flight`: в паспорте «Подтверждено; граница
   получена после расчёта привязки» и «нужен пересчёт привязки». Слов
   «граница не сохранена» нет.
6. Если есть `CASE shared_md5`:
   * в карточке `land_b` вылет `flight` стоит только в таблице «Вылеты на тех
     же границах, привязанные к другим записям DJI»;
   * в «Подтверждённых работах» записи `land_b` этого вылета нет;
   * в карточке `land_a` он есть.
7. Профиль → язык «Ўзбекча» → те же список, карточка и паспорт: всё на
   кириллице, страницы открываются. Вернуть язык, как было.
8. Время: список за всё время, сентябрь, поиск по UUID, поиск по названию —
   «быстро / секунда / дольше двух секунд».

Прислать: по каждому пункту «да / нет / нет случая», время (пункт 8) и
снимки экрана карточки (пункт 3) и паспорта `review_flight`.

### 14.6 Вернуть площадку (после проверки или после остановки 14.2)

Блок возвращает то, что заменил незакрытый прогон: базу площадки (если
прогон дошёл до замены — метка `swapped.txt`) и ревизию кода. Потом
проверяет `/login`, целостность возвращённой базы и то, что production не
изменился. Прогон помечается `returned.txt`; пока метки нет, 14.2 второй раз
не запустится.

```powershell
& {
  $ErrorActionPreference = 'Stop'
  $root     = 'C:\transport-report-staging'
  $prodRoot = 'C:\transport-report'
  $service  = 'TransportReportStaging'
  $python   = 'C:\Program Files\Python314\python.exe'
  $runRoot  = 'D:\transport-report-backups\staging\field_passport_uat'
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
    Write-Output ("DATABASE_RETURNED=" + $backup.Name + " BYTES=" + (Get-Item "$root\instance\transport.db").Length)
    $integrity = & $python -c "import sqlite3,sys;print(sqlite3.connect('file:'+sys.argv[1]+'?mode=ro',uri=True).execute('PRAGMA integrity_check').fetchone()[0])" "$root\instance\transport.db"
    if ($integrity -ne 'ok') { throw "STEP FAILED: returned database integrity_check said $integrity" }
    Write-Output "RETURNED_DB_INTEGRITY=ok"
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
  Start-Sleep -Seconds 8
  $login = Invoke-WebRequest -Uri 'http://10.103.25.14:5051/login' -UseBasicParsing -TimeoutSec 30
  if ($login.StatusCode -ne 200) { throw "STEP FAILED: /login returned $($login.StatusCode)" }
  Write-Output "STAGING_LOGIN_AFTER=200"
  $prodHeadFile = Join-Path $run.FullName 'prod_head.txt'
  if (Test-Path $prodHeadFile) {
    $prodBefore = (Get-Content $prodHeadFile -TotalCount 1).Trim()
    $prodNow = (git -C $prodRoot rev-parse HEAD)
    Write-Output ("PROD_HEAD_BEFORE=" + $prodBefore + " PROD_HEAD_NOW=" + $prodNow)
    if ($prodNow -ne $prodBefore) { throw "STEP FAILED: production HEAD differs from the one recorded before the UAT -- send this line" }
  }
  Write-Output ("PROD_SERVICES_NOW=" + ((@(Get-Service | Where-Object { $_.Name -like 'Transport*' -and $_.Name -notlike '*Staging*' } | Sort-Object Name | ForEach-Object { $_.Name + '=' + $_.Status })) -join ' '))
  Set-Content -Path (Join-Path $run.FullName 'returned.txt') -Value ((Get-Date -Format s) + ' ' + (git rev-parse HEAD)) -Encoding ASCII
  Write-Output ("RESTORED_HEAD=" + (git rev-parse HEAD))
  Write-Output "STEP=PASS"
}
```

Прислать вывод. Ключевые строки:

* `RUN`, `DATABASE_RETURNED`, `RETURNED_DB_INTEGRITY=ok`;
* `RESTORED_HEAD` — должна совпасть с `BEFORE_HEAD` из 14.2;
* `STAGING_LOGIN_AFTER=200`, `PROD_HEAD_BEFORE`/`PROD_HEAD_NOW`;
* `STEP=PASS`.

Копии на `D:` остаются; удалить их можно вручную, когда проверка закрыта.

### 14.7 Вернуть ботов площадки

Только после `STEP=PASS` блока 14.6: база площадки снова своя. Вернуть так,
как было записано в 14.1. Если там стояли `Running` и `Automatic`:

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

### 14.8 Освободить площадку

После 14.6 и 14.7 строка `docs/STAGING.md` возвращается в «нет». Это
отдельный docs-only PR, его готовит сессия по присланным выводам.

### 14.9 Ворота UAT

**Блокеры** (любой → FAIL):

* неверное членство (`*.membership`, `*.confirmed_tiers_only_T1_T2`);
* двойной счёт (`integrity.no_flight_confirmed_in_two_records`,
  `double_count.*`, `page.shared_B_x_not_in_confirmed`);
* неверная принятая (`*.totals_equal_hand_sum_of_provider`,
  `*.raw_equals_sql_sum`, `page.card_A_tiles_equal_provider`);
* неверная текущая ревизия (`latest_revision.*`,
  `page.header_is_latest_observed`);
* переписанная история (`history.*`, `page.history_old_passport_*`);
* «не рассчитано» показано как RAW (`page.no_calc_accepted_is_empty`);
* утечка (`pages.no_leaks`);
* права (`pages.anonymous_redirected`, `FIELDS_ANONYMOUS`);
* страница 500 (`pages.all_200_and_lang`);
* неприемлемое время.

**Не блокеры:**

* поиск по кириллице с учётом регистра;
* MAX(id) в замороженном `current_polygons` (необязательный TIER4, в
  штатном цикле выключен);
* отсутствие редкого живого случая (строка `CASE … NOT FOUND`), когда его
  инвариант держит синтетический отрицательный тест.

## 15. Откат

Кода: `git revert` мерж-коммита (порядок — только `git revert`, не `reset`).
Данных: нечего — инкремент ничего не пишет и схему не меняет.

## 16. Проверки

* `tests/test_dji_field_passport_uat.py` — 14 проверок живого UAT-зонда
  `tools/dji_field_passport_uat.py` и блоков §14:
  * файл зонда — чистый ASCII, строки экранов в нём равны строкам шаблонов;
  * на синтетике сценария ядра все ворота проходят и все классы случаев
    находятся запросом, база не пишется;
  * отрицательные контроли — подтекающее членство, MAX(id) вместо последней
    наблюдавшейся ревизии и утечка на странице роняют прогон (код 4);
  * отказы: нет базы — код 2, файл не создан; нет таблиц — 3; копия для
    страниц в папке `transport-report*` или сама база — 1;
  * страницы RU и UZ: 200, без утечек, плитки = провайдер;
  * блоки §14: production никогда не цель записи, миграций нет, службы —
    только площадочные, код площадки ровно `e7e97f1`, ASCII без
    плейсхолдеров. Четыре мутации документа — запись в production,
    миграция, рестарт боевой службы, другой SHA — роняют тесты.

  Все 15 блоков §14 разобраны парсером PowerShell 7.4: ошибок 0. Windows
  PowerShell 5.1 в контейнере сессии нет; блоки используют только
  конструкции, которые уже шли на сервере в блоках GPS §12.

* `tests/test_drone_field_passport_core.py` (stdlib, в CI) — 39 проверок
  (29 + 10 REVIEW-FIX-1):
  состояния и причины, NO_CARD ≠ NO_KEY, членство, общая граница без двойного
  счёта, итог = провайдер, подмена вылета ломает итог, «не рассчитано» ≠ RAW,
  REVIEW от провайдера, история V1/V2, пакетность, только чтение, список,
  перепись и инструмент (коды 0/1/2/3, ASCII, без записи). Отрицательные
  контроли: классификатор без карточки, членство по md5, предположительные в
  гектарах, последняя граница вместо исторической, чтение по вылету.
  REVIEW-FIX-1: A → B → A через замороженные `parse_land_node` +
  `upsert_land_revision` (шапка, список, подписи, версии; контроль — на этих
  данных MAX(id) выбирает B); TIER2 с байтами, пришедшими позже (сверенными и
  нет), без пересчёта — состояние и членство прежние, «не сохранена» не
  пишется, перепись считает отдельно, контроль — прежняя подпись проверку не
  прошла бы; вылет без записи на общей границе не попадает в список «другой
  записи» ни у A, ни у B.
* `tests/test_drone_field_passport_001.py` (Flask) — 31 проверка (25 + 6
  REVIEW-FIX-1: список, шапка карточки и подпись в паспорте — ревизия A;
  паспорт и карточка при байтах, пришедших позже; паспорт вылета без записи
  на общей границе): итог
  карточки = `accepted.summarize` (с подменой), предположительные отдельно,
  общая граница, паспорт без расчёта и без привязки, REVIEW, NO_CARD/NO_KEY,
  история, происхождение без тел, приватность на всех страницах RU/UZ,
  экранирование, пагинация, язык, права (200/403/редирект), 404, только GET,
  число запросов.
* UI на одноразовом стенде с тем же синтетическим посевом (Playwright +
  axe-core, логика `tools/ux/check_overflow.mjs` и `check_a11y_all.mjs`;
  штатные скрипты идут по `docs/ux/11-baseline/routes.json`, где карточки и
  паспорта нет — у их аргументов нет подстановки id): 8 страниц × 4 ширины
  (1440, 1024, 600, 390) × 2 языка = 64 загрузки — горизонтального
  переполнения 0; axe serious+critical на 1440 и 390 — 0. После REVIEW-FIX-1
  (плюс карточка записи A → B → A и паспорт вылета без записи, на посеве с
  байтами, пришедшими позже): 10 страниц × 4 × 2 = 80 загрузок — переполнения
  0, axe 0.
* Мутационная проверка (вручную, вне набора): уровень кандидата в
  подтверждённых, предположительные в итоге, NO_CARD слит с NO_KEY,
  замещённые строки в привязке (два места), членство по md5, итог карточки
  с предположительными, паспорт на последней границе, RAW вместо «не
  рассчитано», путь хранения в паспорте — каждая роняет тесты.
  REVIEW-FIX-1, каждая роняет тесты: текущая ревизия по MAX(id) — общая и
  по отдельности в `land_header`, `land_list`, `fields_of_flights`; прежняя
  подпись при пришедших байтах; прежний текст предупреждения; перепись без
  признака «байты сейчас»; TIER2 повышен до EXACT по пришедшим байтам;
  маршрут паспорта и строки карточки не передают байты сейчас; список общей
  границы снова с вылетами без записи; безусловная фраза «учтён только в
  своей записи» в паспорте.
