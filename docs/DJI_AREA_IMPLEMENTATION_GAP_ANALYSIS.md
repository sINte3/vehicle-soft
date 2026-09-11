# DJI-AREA-EVIDENCE-001 — gap analysis (до реализации)

Модель: `dji-area-evidence-2026-09-08-final-1`. Нормативный пакет:
`C:\VehicleSoft_Astra_Blind\OUTPUT\FINAL` (11 файлов, SHA256 сверены с
`LOCKED_SHA256_FINAL.json`, копия манифеста — `docs/dji_area_evidence/`).
Аудит репозитория выполнен 2026-09-08 на `origin/main` `9b53d21`
(8 карт: приёмники, сборщик, форензика V4, форензика полей, модели/фабрика,
шаблоны, документация трека, тесты/миграции).

Таблица составлена ДО кода и в коде не «подгонялась»: колонка «Действие»
описывает, что было сделано в этом PR, колонка «Риск» — что осталось.

| Компонент | Текущее поведение (main 9b53d21) | Требование FINAL spec | Разрыв | Действие | Миграция | Риск |
|---|---|---|---|---|---|---|
| Идентичность вылета | `drone_flights.dji_flight_id` (BIGINT UNIQUE), ник → машина, `serial_number` — id вылета | `(provider_account_id, flight_id)`, hardware отдельно, ник не ключ | Нет `provider_account_id`, нет `hardware_id` на вылете | `dji_flight_evidence.flight_id` + `provider_account_id`; `hardware_id` из карточки, иначе из маршрута с проверенной идентичностью, иначе из паспорта машины по нику с флагом `unit_nickname` | новая таблица | ник→машина дрейфовал (окт-2025, март-2026): для истории без карточек борт помечен как выведенный |
| Источники | list-запись целиком в `drone_flights.raw_json`; маршрут — нормализованные точки без raw body и без hash тела; карточек и V4 нет вовсе | Неизменяемые bytes/ссылка, SHA256, размер, время захвата, контекст запроса, версия парсера, идентификатор попытки; новая карточка = новая ревизия | Нет хранилища ревизий; `drone_flight_routes` перезаписывается на месте | `dji_source_revisions` (inline ≤64 КБ, иначе content-addressed gzip в `instance/dji_sources/`), UNIQUE(source_type, scope_key, sha256), `ingest_count`; `POST /drones/api/source_sync` | новая таблица + каталог файлов | рост диска ≈ 70 КБ/V4 (gzip), 8 196 записей августа → 123 МБ источников |
| List vs card | только list (int м²) | Хранить раздельно, не смешивать | Карточка не собиралась | `list_raw_area_m2` (int) и `card_raw_area_m2` (float) в `dji_flight_evidence`; `raw_area_source` в расчёте | — | — |
| Маршрут | `drone_collector.route_decode` читает embedded id (поле 2), но приёмник не сверяет его с запрошенным; `hardware_id` из маршрута отбрасывается | embedded id ≠ ожидаемый → карантин; filename не proof | Нет карантина | `evidence.parse_route_body` → `ROUTE_IDENTITY_ERROR`; маршрут в карантине не участвует в площади/применении/геометрии; `route_points_with_field3` — только структурная диагностика | — | 430 августовских route.bin 29–31.08 — чужие вылеты; ловятся |
| V4 | нигде в репозитории (только форензика) | immutable protobuf, wire presence ≠ default, окно, baseline | Полностью отсутствует | `dji_area/v4.py` (декодер с присутствием), `dji_v4_summaries` (кэш на ревизию) | новая таблица | V4 не несёт flight id: идентичность = путь захвата + окно времени; хранится как `v4_identity_status` |
| Baseline guard | — | ведущий пропуск ≠ измеренный 0; `685264927` → BASELINE_UNKNOWN | — | `counter_observed_delta` vs `counter_zero_default_delta`; полная D только при обоих концах на границе | — | реплей: 1 запись BASELINE_UNKNOWN (ровно 685264927) |
| Резолвер площади | `useful-area-v2` — геометрия маршрута × ширина внутри контура, статусы READY/PARTIAL/… | 14 статусов, методы, уверенность, no auto-zero, partial retained, overlap | Другая семантика (диагностика покрытия) | `dji_area/resolver.py`; `useful-area-v2` остаётся диагностикой, помечена на странице | — | — |
| Структурный экран | форензика: `md_build_flights` (mode4/null width/равенство площади с предыдущим) | frozen rule: same hardware, gap 0–1 с, ≥1 bridge, nearest prior mode4 width-present, area-blind | В продукте нет | `dji_area/structural.py`; кандидат — диагностика, не обнуление | — | реплей: 428 кандидатов, 428 совпадений скаляра; 414 flat + 13 partial подтверждены V4, остальные без V4 → UNKNOWN_SUSPECT / OVERLAP |
| Применение | — | activity / channel quality / evidence kind, 3 Gijduvon UNRELIABLE | — | `assess_application`, `KNOWN_UNRELIABLE_APPLICATION_HARDWARE`, capability по (борт, месяц) | — | список плохих бортов — явная конфигурация, ревизия `app-channel-hw-month-1` |
| Поле | `field_contours` — mutable upsert, bbox/центр, без hash/снимков/версий | immutable snapshots, land revisions, geometry bytes с MD5+SHA256, TIER1–5 | Нет истории каталога и байтов | `dji_land_snapshots`, `dji_land_revisions` (UNIQUE uuid+raw_sha256), `dji_land_geometries` (BLOB, md5_verified), `dji_area/field.py`, `dji_field_attributions`; `POST /drones/api/land_snapshot_sync`; `field_contours` не трогается | 3 новых таблицы | TIER4 опционален (`--with-geometric`), только среди сохранённых полигонов |
| Агрегация | `/drones/coverage` суммирует READY_ESTIMATE | RAW, certified, provisional, unresolved exposure, статусы, tiers, unassigned, app-without-area, unreliable; полный итог не подменять | — | `dji_area/aggregate.py`, страница `/drones/area-evidence` + xlsx | — | — |
| Cross-midnight | фильтры по `started_at + 5h` | день начала UTC+5, один раз; хронология не рвётся | совпадает | `report_start_date` в расчёте; загрузка периода с граничными днями | — | — |
| Версионирование расчёта | `drone_coverage_works` update-in-place по fingerprint | append-only, `calculation_input_hash`, replay детерминирован | Другая дисциплина | `dji_area_calculations` append-only, `superseded_at`, `insert_calculation` | новая таблица | — |
| Бэкфилл | `tools/recalculate_drone_useful_area.py` (dry-run/apply) | dry-run со сводкой, apply идемпотентный, из сохранённых источников, без CSV-классификаций | — | `tools/dji_area_recalc.py`, `tools/dji_area_import_sources.py` (evidence import, помечен) | — | — |
| Сборщик | list walk + `--route-ui-collect` + `--lands`; page.route не используется | карточка/маршрут/V4/airlines/каталог с провенансом | Нет захвата карточек и V4 | `drone_collector --sources`, `--land-snapshot` (page.route + route.fetch для V4 — запрос остаётся запросом SPA) | — | живьём не выполнялось в этой сессии |
| Billing / customer | `drone_works` — отдельная бухгалтерия книг | NULL до утверждённой политики | — | колонки NULL, не заполняются | — | — |
| Unique coverage | `estimated_useful_area_ha` (v2) на `/drones/coverage` | не выдавать как authoritative | подпись | одна фраза-пометка на странице coverage; данные не тронуты | — | — |
| Права | `module_required('drones')` | — | — | тот же код модуля (прецедент: gps → wialon) | — | — |
| CSRF | точечные пути в `app.py` | — | два новых пути | `app.py` (общий файл, объявлено в PR) | — | — |

## Что НЕ делается в этом PR (и почему)

- `field_contours` не получает hash/снимки: общая с GPS таблица; история живёт
  в `dji_land_*`, mutable справочник остаётся для прежних экранов.
- Continuity baseline (`VERIFIED_CONTINUITY`) не реализован: доказательства
  непрерывности сессии в источниках нет; ведущий пропуск всегда
  `UNVERIFIED_OMISSION`.
- Семантика поля №3 точки маршрута не именуется (UNKNOWN_SEMANTICS
  сохраняется); хранится только число точек с ним.
- Никакой автоматический customer/billing.
