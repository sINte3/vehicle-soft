# UI-P6-001 · Модуль `ref_*` / `admin` — приведение к контрактам

**Фаза:** P6, первый модуль из шести.
**Зависимости:** P5 закрыта (`33-p5-foundation.md`), подпись владельца под P4
(`32-canonical-screens.md` §7).

## SCOPE

Привести семь экранов справочников и администрирования к контрактам,
принятым в P3 и реализованным на эталонных экранах в P4: таблица с явно
объявленным режимом (DD-028…DD-031), один компонент статуса (DD-032), полоса
показателей (ADOPT-1), иконочный набор вместо эмодзи, ноль собственного CSS
внутри шаблона.

**Это не «улучшить дизайн модуля».** Ни одно решение здесь не принимается
заново: каждое уже принято в `31-design-system-v2.md` и показано на эталоне.
Задача — перенести, а не выдумать.

Модуль выбран первым, потому что он мелкий: 7 шаблонов против 21 у АЗС. Это
обкатка порядка работ, а не самая нужная миграция.

## AFFECTED FILES

```
templates/ref_customers.html
templates/ref_equipment.html
templates/ref_organizations.html
templates/ref_work_types.html
templates/admin_users.html
templates/admin_permissions.html
templates/audit_logs.html
static/css/design-system.css      ← ОБЩИЙ ФАЙЛ, объявляется
app.py                            ← ОБЩИЙ ФАЙЛ, объявляется
tests/test_ref_admin_module.py    ← новый
docs/ux/41-task-specs/UI-P6-001-ref-admin.md
docs/ux/design-system-baseline.json
docs/tracks/ui.md
```

**Общие файлы — что именно и зачем.**

- `static/css/design-system.css`: удаляются мёртвые классы `vs-badge-cat-*` и
  `vs-badge-role-*` (DD-032 прямо называет их лишними; в разметке не
  используется ни один). Добавляется `.vs-table td.vs-cell-form` — ячейка,
  внутри которой лежит форма редактирования строки.
- `app.py`: маршрут `admin_audit_logs` отдаёт в шаблон предел выборки
  (`LIMIT 300` уже был в коде и нигде не был подписан). Контракт `is-stream`
  требует явной подписи охвата; без передачи числа шаблон дублировал бы
  константу и молча разошёлся бы с маршрутом.

## ALLOWED CHANGES

Разметка, классы, порядок блоков внутри перечисленных шаблонов. Удаление
`<style>` из шаблона. Замена эмодзи на `icon()`. Добавление `data-sort`,
`data-sort-value`, `data-sort-default`. Один параметр в `render_template`
маршрута аудита.

## PROTECTED AREAS

- Смысл и состав выгрузок Excel: `export_equipment`,
  `export_customers_diagnostics`, `export_work_types_diagnostics` — кнопки
  переезжают, ссылки и параметры не трогаются.
- Бизнес-логика: правила «можно ли удалить / отключить / включить»
  (`del_info.can_delete`, `can_deactivate`, `is_disabled`) переносятся
  дословно, включая ветвление.
- Схема БД, миграции: у трека одна миграция, `DAILY_UNITS_001`, и второй быть
  не должно.
- Запросы маршрутов: ни один фильтр, ни один `ORDER BY`, ни один `LIMIT` не
  меняется. У аудита число 300 остаётся 300.
- Поведение форм: `name`, `action`, `csrf_token`, `onsubmit`-подтверждения —
  как есть.

## Режим каждой таблицы (DD-028) — объявляется числом, а не на глаз

Объёмы взяты из `docs/ux/10-metrics/ux_metrics.json`, снятых с боевой базы.

| Экран | Строк на бою | Режим | Основание |
|---|---|---|---|
| `/ref/customers` | 9 | `is-static` | всё помещается, сортировка на клиенте |
| `/ref/organizations` | 22 | `is-static` | то же |
| `/admin/users` | 7 | `is-static` | то же |
| `/admin/permissions` | 7 × 13 модулей | `is-static` | матрица прав, сортировка по логину |
| `/ref/work_types` | 104 | `is-static` | ниже порога ~200 |
| `/ref/equipment` | **336** | `is-static` | **выше порога, см. ниже** |
| `/admin/audit` | 3436, окно 300 | `is-stream` | сервер режет `LIMIT 300` |

**`/ref/equipment` — 336 строк при пороге ~200, и это записывается, а не
заминается.** Режим `is-static` объявлен потому, что таблица именно такова
сегодня: маршрут отдаёт `q.all()` без предела, страница рисует всё, разбиения
нет. Перевод в `is-paged` — это серверное разбиение и серверная сортировка, то
есть смена того, как оператор работает со списком (сейчас он ищет по всей
странице браузером). Такое решение принимает владелец, а не миграция вёрстки.
Сам порог ~200 в DD-028 помечен как **непроверенное предположение**, которое
измеряется на staging в P7. Пункт открыт и назван в `docs/tracks/ui.md`.

## ACCEPTANCE CRITERIA

Каждый — с указанием, чем проверен.

1. **Одноразовых классов таблиц в модуле — 0.** Проверка:
   `python tools/check_design_system.py --detail`, раздел `table-class`, ни
   одной строки с `ref_`, `admin_`, `audit_logs`. Было 7 (все — таблица
   вовсе без класса).
2. **Инлайнового CSS в модуле — 0 строк.** Проверка: тот же вывод, раздел
   `inline-style`. Было 46 строк в четырёх шаблонах.
3. **Хардкод-цветов в модуле — 0.** Было 12 (по 6 в `admin_users` и
   `admin_permissions` — роль красилась инлайновым `background:#e74c3c`).
4. **Эмодзи в модуле — 0.** Было 14. Проверка: раздел `glyph`.
5. **Режим объявлен у каждой таблицы**, и он совпадает с таблицей выше.
   Проверка: тест `test_every_table_declares_a_mode`.
6. **Сортировка объявлена явно** там, где режим `is-static`: у таблицы есть
   `data-sortable` и `data-sort-default`. Проверка: тест
   `test_static_tables_declare_default_sort`.
7. **Охват окна подписан** на `/admin/audit`: в подвале таблицы стоит число
   строк и предел, и предел приходит из маршрута, а не написан в шаблоне.
   Проверка: тест `test_audit_window_scope_comes_from_the_route`
   (отрицательный контроль: подмена предела в маршруте меняет строку в HTML).
8. **Ни одна ссылка на выгрузку Excel не изменилась.** Проверка: тест
   `test_export_links_survived_the_migration` сверяет `href` целиком.
9. **Все тесты зелёные**, ни один не удалён и не ослаблен:
   `python -m unittest discover -s tests`.
10. **axe: 0 serious/critical** по семи маршрутам, на десктопе **и на 390 px**.
11. **Дифф «до/после» на 4 вьюпортах** снят и просмотрен; страницы вне модуля
    не изменились.

## REQUIRED TESTS

```
python -m compileall -q .
python tools/check_templates.py
python tools/test_check_templates.py
python tools/check_design_system.py
python tools/test_check_design_system.py
python -m unittest discover -s tests

# povedenie: trebuet podnyatogo ekzemplyara, v CI ne vhodit
PLAYWRIGHT_BROWSERS_PATH=/opt/pw-browsers \
  node tools/ux/check_ref_admin_interactions.mjs --base http://127.0.0.1:5099
```

**Почему поведение проверяется отдельным скриптом.** В тестах проекта нет
движка JS: они рендерят HTML и сравнивают строки. Миграция перевела общие
строки редактирования с `style="display:none"` на атрибут `hidden`, а показ и
скрытие — с `el.style.display` на `el.hidden`. Шаблон рендерится одинаково
верно в обоих случаях, и строковый тест молча прошёл бы на сломанном коде.

## VISUAL CHECKPOINTS

Семь маршрутов × 4 вьюпорта (1440 / 1024 / 600 / 390):

```
/ref/customers  /ref/organizations  /ref/work_types  /ref/equipment
/admin/users    /admin/permissions  /admin/audit
```

Плюс контроль: остальные 65 немигрированных страниц не должны измениться.

## BEFORE / AFTER

Инструмент — `tools/ux/shoot_regression.mjs` (он же считает переполнение по
ширине). База сравнения — коммит **до** этого PR, а не начало трека.

Порог: страницы **вне** модуля — ≤ 1 % площади, как в P5. Страницы модуля
меняются намеренно, их дифф читается глазами, а не порогом.

## РЕЗУЛЬТАТ — чем закрыт каждый критерий

| # | Критерий | Чем проверен | Число |
|---|---|---|---|
| 1 | одноразовых классов таблиц — 0 | `check_design_system.py --detail` | 7 → **0** |
| 2 | inline-CSS модуля — 0 | там же | 46 → **0** |
| 3 | хардкод-цветов — 0 | там же | 12 → **0** |
| 4 | эмодзи — 0 | там же | 14 → **0** |
| 5 | режим объявлен у каждой таблицы | `test_every_table_declares_a_mode` | 7 таблиц |
| 6 | сортировка объявлена явно | `test_client_default_sort_never_overrides_the_server_order` | 4 с умолчанием, 2 намеренно без |
| 7 | охват окна аудита подписан из маршрута | `test_audit_window_scope_comes_from_the_route` | предел 300, отрицательный контроль 7 |
| 8 | ссылки выгрузок целы | `test_export_links_survived_the_migration` | 3 ссылки |
| 9 | тесты зелёные | `python -m unittest discover -s tests` | 518 → **532**, OK |
| 10 | axe 0 serious/critical, десктоп и 390 px | `shoot_canonical.mjs`, 16 прогонов | 0/0/0/0 |
| 11 | дифф «до/после» снят | `52-p6-ref-admin/index.html`, 60 кадров | 0 сбоев |

Отрицательный контроль прогнан по пяти проверкам: код ломался намеренно, и
падала ровно ожидаемая (таблица в `34-p6-modules.md` §1.8).

Проверка доступности получила отрицательный контроль от самой работы: с
`max-height` на списке диагностики axe даёт 4 serious, без него — 0, на тех же
страницах и данных.

## ROLLBACK

`git revert` коммитов в обратном порядке. `git reset --hard` запрещён:
ветка общая, между хешем и HEAD лежат чужие коммиты.

## GIT

Ветка `claude/vehicle-soft-ui-modernize-l1pcfy`. Коммит один на модуль,
файлы в индекс — поимённо. Мерж PR — merge commit, не squash.
