# DJI-AREA-EVIDENCE-001 — учёт площади DJI по модели доказательств

Модель: **`dji-area-evidence-2026-09-08-final-1`**. Решение исследования:
`GO_WITH_GUARDS`. Нормативные документы — вне репозитория
(`C:\VehicleSoft_Astra_Blind\OUTPUT\FINAL`), их SHA256-манифест и таблица
прослеживаемости — `docs/dji_area_evidence/`. Реализация: версия резолвера
`dji-area-evidence-2026-09-08-final-1-impl-2` (см. §12), поле
`dji-field-tiers-2026-09-08-impl-1`, парсер V4 `v4-parse-1`, структурный экран
`structural-retained-screen-frozen-1`, ревизия способности канала
`app-channel-hw-month-1`.

**Режим на площадке — теневой.** `drone_flights.area_ha` (площадь DJI, по
которой выставлялись счета) не читается и не пишется ни одной строкой этого
инкремента. Новые числа живут рядом, версионированы, append-only и не
являются ни уникальным физическим покрытием, ни площадью поля, ни счётом.
**Production не трогать до решения владельца** после ручной сверки августа и
квалификации площадки по `TRUE_HOLDOUT_PROTOCOL_LOCKED.md`.

## 1. Что хранится

| Таблица | Смысл | Ключ |
|---|---|---|
| `dji_source_revisions` | неизменяемое тело ответа DJI (list-запись, карточка, маршрут, V4, airlines, страница каталога, геометрия): SHA256, размер, время захвата, контекст запроса (путь без подписи), идентификатор попытки, версия парсера; тело inline (≤ 64 КБ) или content-addressed gzip в `instance/dji_sources/<sha[:2]>/<sha>.gz` | `(source_type, scope_key, sha256)`; повтор тех же байтов двигает `ingest_count` |
| `dji_flight_evidence` | текущие указатели записи вылета на ревизии; борт (`hardware_id` + источник: card / route / unit_nickname); идентичность маршрута (`ROUTE_IDENTITY_ERROR` = карантин) и V4 (`URL_PATH_MATCH` / `DIRECTORY_ONLY` / `MISMATCH` / `ABSENT`) | `flight_id` |
| `dji_v4_summaries` | сводка ревизии V4: присутствие каналов, окно, концы счётчика (закодированные и zero-default), применение | `source_revision_id` |
| `dji_area_calculations` | результат резолвера: RAW, controller delta, corrected, статус/метод/уверенность, флаги, применение, структурный экран, пересечение, право на агрегирование; `billable_area_m2`/`customer_id` всегда NULL | `(flight_id, area_algorithm_version, calculation_input_hash)`; `superseded_at` |
| `dji_field_attributions` | tier поля, linked/holder uuid, имя на снимке, lineage | `(flight_id, field_resolver_version, field_input_hash)`; `superseded_at` |
| `dji_land_snapshots` / `dji_land_revisions` / `dji_land_geometries` | неизменяемая история каталога полей: снимок (полнота, ожидалось/получено), metadata-ревизия `(land_uuid, raw_sha256)`, байты геометрии по `contentMd5` с проверкой md5(bytes) и SHA256 | см. таблицы |

Миграция `DJI_AREA_EVIDENCE_001` (`migrate_dji_area_evidence_001.py`): только
CREATE TABLE/INDEX IF NOT EXISTS, одна транзакция, предусловие
`drone_flights`, реестр в той же транзакции. Четыре пути и сверка DDL с ORM —
`tests/test_dji_area_migration_001.py` (18 тестов).

## 2. Семантика резолвера площади (`dji_area/resolver.py`)

Вход: RAW (list int м²; card float отдельно), сводка V4 с гейтами окна,
идентичность маршрута, структурный экран, пересечение интервалов, качество
канала применения. Замороженные гейты окна: ≥ 2 кадров, положительный span,
смещения концов ≤ 1.01 с относительно записи, все dt > 0 и ≤ 1 с; поле 9
отсутствует во всех кадрах → `CHANNEL_MISSING`; убывание счётчика →
`NONMONOTONE`; чужая идентичность → `IDENTITY_ERROR`. Ничего не подрезается и
не экстраполируется.

Baseline: полная разность интервала `controller_delta_area_m2` — только когда
счётчик закодирован и в первом, и в последнем кадре. При ведущем пропуске
хранятся `counter_observed_delta_m2` (закодированное подокно) и
`counter_zero_default_delta_m2` (диагностика с подстановкой нуля, никогда не
production-источник).

| Условие | Статус | corrected | Метод |
|---|---|---|---|
| RAW>0, полное окно, `abs(D−A) ≤ max(20 м², 1 % A)` | `RAW_CORROBORATED` | A | RAW_WITH_VALIDATED_COUNTER |
| RAW>0, ведущий пропуск, подокно согласуется с A | `RAW_CORROBORATED_QUALIFIED` | A (provisional), D=NULL | RAW_WITH_SUBWINDOW_CORROBORATION |
| RAW>0, полное окно, концы равны по битам | `COUNTER_FLAT_RAW_OVERSTATED` | 0 (технический интервал; не «ничего не делал») | VALIDATED_COUNTER_DELTA |
| RAW>0, полное окно, 0 < D < 0.1·A (A>100) | `PARTIAL_RECORDED_OVERSTATEMENT` | D (в т.ч. один квант 6.667 м²) | VALIDATED_COUNTER_DELTA |
| RAW>0, V4 нет, обычная запись | `RAW_UNVERIFIED` | A (provisional) | RAW_FALLBACK |
| RAW>0, V4 нет/негодно, кандидат экрана со совпавшим скаляром или пересечение | `UNKNOWN_SUSPECT` | NULL | NO_SAFE_CORRECTION |
| RAW=0, канал площади отсутствует, применение есть | `APPLICATION_WITHOUT_MEASURED_AREA` | NULL, флаг true | ACTIVITY_FLAG_ONLY |
| RAW=0, полное окно, плоский счётчик | `COUNTER_ZERO` | 0 | VALIDATED_COUNTER_DELTA |
| RAW=0, канала нет, применения нет | `CHANNEL_MISSING` | NULL | NO_COUNTER_MEASUREMENT |
| RAW=0, V4 нет | `ZERO_RECORDED_UNVERIFIED` | NULL | RAW_ONLY |
| ведущий пропуск, подокно плоское либо zero-default ≈ A (685264927) | `BASELINE_UNKNOWN` | NULL | NO_SAFE_INTERVAL_DELTA |
| D и A расходятся, не flat/tiny (692752823) | `COUNTER_RELATIONSHIP_OUTLIER` | NULL | REVIEW_REQUIRED |
| счётчик убывал | `COUNTER_NONMONOTONE_REVIEW` | NULL | REVIEW_REQUIRED |
| пересечение интервалов того же борта | `OVERLAP_REVIEW` (evidence_status сохранён) | NULL | UNRESOLVED_INTERVAL_OWNERSHIP |

Применение: `application_activity` PRESENT / NOT_OBSERVED / UNKNOWN;
`application_evidence_kind` FLAG_OR_FLOW / QUANTITY_ONLY / MULTIPLE / NONE /
UNRELIABLE; борт `1581F5742255T0C1L061` (3 Gijduvon) — UNRELIABLE всегда;
у остальных канал INFORMATIVE только при flag/flow evidence того же борта в
том же месяце. Quantity-only — не доказательство распыления
(`QUANTITY_INCREASE_WITHOUT_AREA`).

Право на агрегирование: CERTIFIED (`RAW_CORROBORATED`, `COUNTER_FLAT_RAW_OVERSTATED`,
`PARTIAL_RECORDED_OVERSTATEMENT`, `COUNTER_ZERO`), PROVISIONAL
(`RAW_CORROBORATED_QUALIFIED`, `RAW_UNVERIFIED`), UNRESOLVED (всё с NULL),
EXCLUDED_OVERLAP. Агрегат (`dji_area/aggregate.py`) отдаёт RAW, certified,
provisional, unresolved exposure, пересечения, application-without-area,
unreliable, tiers, unassigned; «полного итога» не существует — только
«известная часть» плюс экспозиция.

Структурный экран (`dji_area/structural.py`, заморожен): тот же борт,
хронология по `start_ts` с граничными днями, target mode 4 без ширины,
обратный проход по зазорам 0–1 с, ≥ 1 bridge, ближайшая mode-4 запись с
шириной = база; равенство скаляра проверяется ПОСЛЕ выбора базы. Кандидат —
диагностика; ноль ставит только проверенный интервал счётчика.

Хеш входа (`dji_area/hashing.py`): SHA источников, соседи по экрану и
пересечению, channel evidence, конфигурация порогов и версии. Тот же вход →
`unchanged`; иной → новая строка, прежняя закрывается `superseded_at`.

## 3. Поле (`dji_area/field.py`)

Ключ `geometry_md5` карточки: plain MD5 или `UUID__MD5`. TIER1_EXACT — байты
геометрии с этим md5 сохранены и проверены; linked uuid и holder uuid
раздельно. TIER2_STRONG — uuid в каталоге (или md5 совпал с contentMd5
записи каталога), байты нужной версии не сохранены. TIER3_SUPPORTED —
lineage через другие точно разрешённые записи, конфликт → TIER5.
TIER4_GEOMETRIC — только `--with-geometric`: ≥ 80 % точек маршрута с
проверенной идентичностью внутри ОДНОГО сохранённого полигона.
TIER5_UNKNOWN — остальное. Tier не меняет площадь; TIER5 виден в корзине
«поле не определено». Имя поля — текст как есть; число в имени площадью не
является.

## 4. Сборщик и приём

- `POST /drones/api/source_sync` — ревизии источников (карточка, маршрут,
  V4, airlines, list); токен в теле; `seen = new + duplicates + errors`;
  тело с маркером подписанного URL или несовпавшим SHA отклоняется; после
  пакета пересобирается `dji_flight_evidence`.
- `POST /drones/api/land_snapshot_sync` — неизменяемый снимок каталога
  (ревизии + байты геометрии с проверкой md5); `field_contours` не трогается.
- Сборщик: режимы `--sources`, `--send-sources`, `--land-snapshot`
  (`drone_collector/README.md`, раздел «Sources»). V4 читается через
  `page.route` + `route.fetch()` — запрос остаётся запросом SPA, ничего не
  подменяется; из события `response` тела терялись (~25 % в форензике).
- `tools/dji_area_import_sources.py` — импорт сохранённого форензик-архива
  (помечен `is_evidence_import`), только для площадки/QA.

## 5. Пересчёт и бэкфилл

```
& "C:\Program Files\Python314\python.exe" tools\dji_area_recalc.py --from 2026-08-01 --to 2026-08-31 --dry-run
& "C:\Program Files\Python314\python.exe" tools\dji_area_recalc.py --from 2026-08-01 --to 2026-08-31 --apply
```

Сухой прогон печатает сводку по статусам, доступности V4, baseline, tiers,
нерешённым, пересечениям, применению без площади — и ничего не пишет.
`--apply` идемпотентен (второй прогон → `unchanged`), пишет партиями по 500,
повторный запуск после обрыва безопасен. `--flight-id` ограничивает набор;
`--rows` выгружает построчную диагностику.

Производительность (реплей августа, 8 196 записей, 1 410 V4, локальная
машина): первый `--apply` 2 мин 28 с, повторный 23 с; рост базы +40 МБ на
28 366 ревизий/8 196 расчётов/6 057 ревизий каталога/798 геометрий; файловое
хранилище 123 МБ (в основном V4 gzip ≈ 80 КБ на файл). Страницы читают
только сохранённые расчёты; V4 на запрос не декодируется.

## 6. Отчёт

`/drones/area-evidence` (плитка «Площадь DJI: техническая оценка» в хабе
отчётов) и `/drones/area-evidence.xlsx`: DJI RAW, проверено, предварительно,
недостаточно данных (с RAW-экспозицией), повторные/перенесённые, площадь не
измерена при активности, поле не определено; таблица по дням и машинам и
построчный разбор. UNKNOWN показывается словами, не `0.00`. Страница
`/drones/coverage` (`useful-area-v2`) осталась как геометрическая
диагностика покрытия и помечена ссылкой на новый отчёт.

## 7. Реплей августа (доказательство воспроизводимости)

`tools/dji_area_recalc.py --dry-run` на реплей-базе (8 516 записей через
настоящий `flight_sync`, источники импортированы из архива):

| Показатель | Реализация | Эталон FINAL | Разница |
|---|---|---|---|
| RAW list, 8 196 записей | 87 350 343 м² | 87 350 343 м² | 0 |
| exact-flat retained | 414 / 3 271 923 м² | 414 / 3 271 923 м² | 0 |
| tiny-positive retained | 13 / 101 495 м² RAW, D = 193.336169 м² | 13 / 101 495 / 193.336169 | 0 |
| baseline-ambiguous 685264927 | `BASELINE_UNKNOWN`, D NULL | BASELINE_UNKNOWN | — |
| missing/bad V4 среди 433 | 3 `UNKNOWN_SUSPECT`, 1 `OVERLAP_REVIEW`, 1 `RAW_UNVERIFIED` | 5 UNKNOWN | по составу |
| консервативный сценарий | 83 977 118.336169 м² (вычислен) | 83 977 118.336169 м² | 0.000000 |
| RAW=0 с применением | 7 `APPLICATION_WITHOUT_MEASURED_AREA` + 677314979 `COUNTER_ZERO` (флаг true) | 8 (7 без канала, 1 со счётчиком) | 0 |
| 13 кейсов матрицы | 13/13 ожидаемых статусов | — | — |

Ни один порог не подбирался; totals не hardcode. Различие с историческим
покрытием tiers: TIER1 2 900 против 2 966 в исследовании — 66 записей, чьи
байты геометрии не были скачаны (walk остановлен на 798/823), у нас
TIER2_STRONG по совпадению md5 с каталогом; TIER4 не считался (опция).

## 8. Диагностика

- Запись: `SELECT * FROM dji_area_calculations WHERE flight_id=? AND superseded_at IS NULL` — статус, метод, флаги, хеш входа; `window_reasons_json` объясняет INCOMPLETE.
- Источники: `SELECT source_type, sha256, captured_at_utc, storage_kind, body_path FROM dji_source_revisions WHERE flight_id=?`; тело — `dji_area.store.read_body` (проверяет SHA при чтении).
- Сводка V4: `dji_v4_summaries.summary_json`.
- Построчная выгрузка: `tools/dji_area_recalc.py --dry-run --flight-id N --rows out.json`.
- Пакет ручной сверки: `tools/dji_area_validation_pack.py`.

## 9. Откат

Код — `git revert` коммитов инкремента в обратном порядке. Данные — восемь
таблиц и каталог `instance/dji_sources/` можно оставить (ничто их не
читает после отката) либо удалить по порядку из докстринга миграции; строка
`DJI_AREA_EVIDENCE_001` в `schema_migrations` удаляется последней. Ни один
шаг отката не касается `drone_flights`, `field_contours`, `drone_coverage_works`.

## 10. Квалификация площадки и запрет production

Гейт (по `TRUE_HOLDOUT_PROTOCOL_LOCKED.md`): все acceptance-тесты зелёные;
ни одного NULL→0 в БД/API/Excel/UI; чужой маршрут не участвует; интервалы не
считаются дважды; RAW сходится с DJI в пределах объяснённой точности;
replay источников воспроизводим; сентябрьский unseen holdout (30–40 записей,
≥ 3 борта, > 1 день) оценён по протоколу; ручная сверка августа владельцем
по `DJI_AREA_AUGUST_LIVE_VALIDATION_PACK.xlsx` выполнена. До прохождения
гейта коррекция — shadow/provisional, RAW доступен, billing NULL.
**Production не деплоится и не мигрируется этим инкрементом.**

## 12. Разбор состязательного ревью

Реализацию читали три независимых ревьюера, не знавших выводов друг друга;
каждую находку затем пытались опровергнуть отдельные проверяющие с
воспроизведением на коде и на реплей-базе. Из 12 заявленных находок
подтверждены 5; остальные семь либо описывали уже закрытый случай, либо
опирались на использование, которого в коде нет. Исправлены только
подтверждённые. Резолвер после правок несёт версию
`dji-area-evidence-2026-09-08-final-1-impl-2`.

| Что было | Почему это дефект | Что стало |
|---|---|---|
| Файл V4 с ЧУЖОЙ идентичностью всё равно давал оценку применения: при RAW=0 запись получала `APPLICATION_WITHOUT_MEASURED_AREA` | утверждение «работа была» по телеметрии другого вылета; без этого файла та же запись читается как `ZERO_RECORDED_UNVERIFIED` | применение считается только по своему файлу; активность `UNKNOWN`, флаг `V4_IDENTITY_APPLICATION_EVIDENCE_EXCLUDED`; свидетельство канала борта тоже не берётся из чужого файла |
| Негодный V4 (неполное окно, отсутствующий канал площади) давал уверенность `MEDIUM` | строка «Bad/incomplete V4» решающей таблицы §4 фиксирует `LOW`, а `MEDIUM/LOW` относится к строке «V4 просто нет»; сломанный источник подавался так же уверенно, как отсутствующий | `LOW`; число не меняется, меняется его цена |
| Нечитаемое тело V4 давало ту же строку, что «V4 нет», и ТОТ ЖЕ отпечаток входа | восстановив файловое хранилище, оператор получал `unchanged` и навсегда оставался с деградированной строкой | причина (`V4_BODY_UNREADABLE` / `V4_BODY_UNDECODABLE`) входит в отпечаток и в флаги: возврат файла переписывает строку |
| Свидетельство канала собиралось по загруженным датам | месячный и дневной пересчёты давали разный `application_activity` одной записи и вытесняли строки друг друга бесконечно | свидетельство считается по ПОЛНЫМ календарным месяцам: результат зависит от данных, а не от аргументов командной строки |
| `current_polygons()` выдавал по строке на КАЖДУЮ ревизию карточки поля | правка имени поля рождает вторую ревизию с той же геометрией, и TIER4 объявлял единственное поле неоднозначным | берётся последняя ревизия пары (поле, геометрия); §5.5 говорит о разных полигонах, а не о разных метаданных |

Попутно, в тех же файлах и без изменения решений: чтение тела источника
переводит `OSError`/битый gzip в `StoreError` (иначе потерянный файл ронял
весь пакет приёма); `request_context` проверяется на маркер секрета так же,
как тело; писатели открывают `BEGIN IMMEDIATE` (при отложенном `BEGIN`
SQLite отвечает `SQLITE_BUSY_SNAPSHOT` в обход `busy_timeout`);
`catalog_state_sha()` учитывает `md5_verified` (иначе сверка байтов не
доходила до сохранённой привязки); разбор GeoJSON переживает документ, где
`properties`, `geometry` или полигон -- не тот тип.

**Что НЕ исправлено намеренно.** Скаляры маршрута с
`ROUTE_IDENTITY_ERROR` по-прежнему записываются в `dji_flight_evidence` и
`route_area_m2` строки расчёта. Проверка показала, что все 423 такие строки
несут рядом флаг `ROUTE_IDENTITY_ERROR`, что каждое запрещённое §4
ПРИМЕНЕНИЕ закрыто отдельно (площадь только из list/card, борт только при
`ROUTE_OK`, TIER4 и разбор точек требуют `route_identity_ok`) и что ни один
потребитель этой колонки не читает. Спецификация запрещает использовать
такой маршрут, а не наблюдать его; именно рядом лежащая чужая площадь и
позволила найти дефект прежнего захвата.

## 11. Известные ограничения

- V4 не несёт flight id: идентичность — путь захвата + окно времени.
- `VERIFIED_CONTINUITY` baseline не реализован (нет доказательства).
- Поле №3 точки маршрута остаётся UNKNOWN_SEMANTICS.
- Без карточки борт выводится из паспорта машины по нику (`unit_nickname`) —
  для истории до сбора карточек.
- Скаляры маршрута с `ROUTE_IDENTITY_ERROR` сохраняются как
  наблюдение (см. §12): потребитель обязан читать
  `route_identity_status` рядом.
- Хранение всех V4 стоит ≈ 0.6 ГБ/мес (gzip): политика полного/выборочного
  сбора — операционное решение владельца.
