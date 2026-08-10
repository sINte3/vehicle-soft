# UI-P6-002 · Модуль `wialon` — приведение к контрактам

**Фаза:** P6, второй модуль из шести.
**Зависимости:** UI-P6-001 (`ref_*`/admin) влит.

## SCOPE

Привести пять экранов Виалона к контрактам DD-028…DD-033: таблица с явно
объявленным режимом, один компонент статуса, полоса показателей, иконочный
набор вместо эмодзи, ноль собственного CSS внутри шаблона.

Ни одно решение здесь не принимается заново.

## AFFECTED FILES

```
templates/wialon.html
templates/wialon_auto_match.html
templates/wialon_mapping.html
templates/wialon_mapping_list.html
templates/wialon_report.html
wialon_import.py                     ← маршрут отдаёт предел окна импорта
static/css/design-system.css         ← ОБЩИЙ ФАЙЛ, объявляется
tests/test_wialon_module.py          ← новый
tools/ux/check_wialon_interactions.mjs ← новый
docs/ux/41-task-specs/UI-P6-002-*.{md,json}
docs/ux/34-p6-modules.md, docs/tracks/ui.md
docs/ux/design-system-baseline.json, docs/ux/51-regression/regression.json
```

**Общий файл — что именно и зачем.** `static/css/design-system.css`:

- перенесены правила из инлайновых `<style>` трёх шаблонов, на токенах:
  `.vs-meter*` (полоса-доля, DD-033), `.vs-tone`, `.vs-table-wrap-warning/-danger`,
  `.vs-notice-row`, `.vs-chip-row`, `.vs-btn-warning`, `.vs-hint`,
  `.vs-inset-panel`, `.vs-mono`, `.vs-toolbar.is-sticky`, `.vs-ml-auto`,
  `.vs-mt-xxs`, `.automatch-row-hidden`, `.automatch-row-error`;
- добавлено `[hidden] { display: none !important; }` — см. ниже, это
  исправление дефекта, а не оформление.

## ALLOWED CHANGES

Разметка, классы, порядок блоков. Удаление `<style>`. Замена эмодзи на
`icon()`. Замена `style.display` на атрибут `hidden`. Один параметр в
`render_template` маршрута импорта.

## PROTECTED AREAS

- Смысл выгрузок Excel: `wialon_report_export`, `wialon_workload_export` —
  ссылки и параметры не трогаются.
- Разбор форм: имена полей, порядок значений, `csrf_token`.
- Запросы маршрутов: `LIMIT 30` у журнала импорта остаётся 30, `ORDER BY`
  не меняется.
- Схема БД, миграции: у трека одна, `DAILY_UNITS_001`.

## Режим каждой таблицы (DD-028)

| Экран | Строк на бою | Режим | Сортировка |
|---|---|---|---|
| `/wialon` — журнал импорта | 169, окно 30 | `is-stream` | серверная, охват подписан |
| `/wialon/mapping` — связки | **379** | `is-static` | клиентская, умолчание = порядок маршрута |
| `/wialon/mapping` — ожидают решения | единицы | `is-static` | не объявлена |
| `/wialon/report` — по организации | десятки | `is-static` | **не объявлена намеренно** |
| `/wialon/auto_match` — три секции | десятки | `is-static` | **запрещена намеренно** |
| `wialon_mapping.html` — разбор | десятки | `is-static` | **запрещена намеренно** |

**Почему на трёх экранах сортировки не будет никогда.** Маршрут разбирает эти
формы **позиционно**: `getlist('vialon_name')[i]` сопоставляется с
`getlist('eq_choice')[i]`. Порядок значений в форме — это порядок строк в
разметке. Клиентская сортировка переставила бы строки, а вместе с ними и пары
«объект → техника», и моточасы легли бы не той машине — молча, без единой
ошибки на экране. У отчёта причина другая и тоже конкретная: колонка «№»
нумерует строки в порядке маршрута, и сортировка оставила бы номера на местах.

## ACCEPTANCE CRITERIA

| # | Критерий | Чем проверен | Число |
|---|---|---|---|
| 1 | одноразовых классов таблиц — 0 | `check_design_system.py --detail` | 7 → **0** |
| 2 | inline-CSS модуля — 0 | там же | 40 → **0** |
| 3 | хардкод-цветов — 0 | там же | 9 → **0** |
| 4 | эмодзи — 0 | там же | 36 → **0** |
| 5 | режим объявлен у каждой таблицы | `test_every_table_declares_a_mode` | — |
| 6 | сортировка запрещена там, где форма позиционная | `test_positional_decision_forms_are_never_sortable` | 2 файла |
| 7 | скрытые поля внутри ячеек | `test_hidden_inputs_live_inside_cells` | — |
| 8 | поля периода действительно скрыты | `check_wialon_interactions.mjs` | 20 проверок |
| 9 | охват окна импорта из маршрута | `test_import_window_scope_comes_from_the_route` | 30, контроль 3 |
| 10 | латиницы в узбекских подписях нет | `test_no_latin_uzbek_left_in_the_mapping_screen` | 26 подписей |
| 11 | тесты зелёные | `unittest discover` | 532 → **547** |
| 12 | axe 0 serious/critical | `shoot_canonical.mjs`, десктоп и 390 px | 10 прогонов, 0/0/0/0 |
| 13 | страницы вне модуля не изменились | `shoot_regression.mjs` против `9bea874` | 264 строки контроля, **макс 0,11 %** |
| 14 | новых переполнений по ширине нет | там же | **0 новых, 3 снято** |

Единственное движение вне модуля — `/spare-parts/catalog` и
`/spare-parts/skus`: следствие правки `[hidden]`, названное заранее в описании
коммита. Обе страницы стали короче на ≈ 68 px — ровно одна строка поля,
которое код прятал, а каскад показывал.

## REQUIRED TESTS

```
python -m compileall -q .
python tools/check_templates.py && python tools/test_check_templates.py
python tools/check_design_system.py && python tools/test_check_design_system.py
python -m unittest discover -s tests

# поведение и каскад: требуют поднятого экземпляра, в CI не входят
PLAYWRIGHT_BROWSERS_PATH=/opt/pw-browsers \
  node tools/ux/check_wialon_interactions.mjs --base http://127.0.0.1:5099
```

## VISUAL CHECKPOINTS

`/wialon`, `/wialon/mapping`, `/wialon/report`, `/wialon/auto_match` ×
4 вьюпорта, плюс `/wialon/mapping` на узбекском.
Набор — `UI-P6-002-screens.json`, кадры — `docs/ux/53-p6-wialon/index.html`.

## ROLLBACK

`git revert` в обратном порядке. `git reset --hard` запрещён.

## GIT

Ветка `claude/vehicle-soft-ui-modernize-l1pcfy`, один коммит на модуль,
файлы в индекс поимённо, мерж PR — merge commit.
