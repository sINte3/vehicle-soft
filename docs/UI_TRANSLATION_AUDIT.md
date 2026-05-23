# UI_TRANSLATION_AUDIT.md

Task: TASK-UI-001A  
Audit date: 2026-05-23  
Analyst: Claude Code (claude-sonnet-4-6)  
Status: **TASK-UI-001A COMPLETED. TASK-UI-001B Phase 1 COMPLETED. TASK-UI-001C Phase 2 COMPLETED (2026-05-23), verified.**

---

## Summary

The application has a UZ/RU language switch implemented via `translations.py` and a
`t()` context function. The switch works for labels wrapped in `t()`. However, a
significant portion of visible UI text bypasses the translation system entirely.

**No mojibake / garbled encoding found.** All templates are valid UTF-8. The grep
for mojibake patterns matched `Рўйхат` (Uzbek word for "list") which is correct text.

**Four categories of gap identified:**

1. **Fuel module (templates/fuel/\*.html) is entirely in Russian.** None of the six
   fuel templates uses `t()`. All labels, table headers, buttons, and flash messages
   are hardcoded Russian strings. Users with UZ selected see Russian in the fuel module.

2. **fuel_routes.py flash messages are in Russian.** 12 flash messages are Russian
   regardless of the user's language setting.

3. **Scattered hardcoded strings across other templates.** Several templates have
   Uzbek-only labels that do not switch to Russian. Most are in spare parts templates,
   the deficiencies list, and the equipment edit form.

4. **Missing translation keys.** At least 8 keys used in templates are absent from
   `translations.py`, causing silent fallback to the raw Uzbek string in both languages.

---

## Files inspected

| File | t() used? | Notes |
|---|---|---|
| `translations.py` | — | ~200 key/value pairs, UZ+RU |
| `templates/base.html` | Yes (mostly) | "Админ ▾", dropdown link hardcoded |
| `templates/login.html` | No | Labels in Uzbek only; login page has no language switch |
| `templates/index.html` | Yes (mostly) | JS multiselect labels hardcoded |
| `templates/daily_entry.html` | Yes (mostly) | JS confirm() dialogs hardcoded; "сўм" suffix hardcoded |
| `templates/report.html` | Yes (mostly) | Uses `t('КАТЕГОРИЯ')` — key missing |
| `templates/deficiencies.html` | Partial | Card header, empty state hardcoded |
| `templates/wialon.html` | Partial | Period mode labels, notification text hardcoded |
| `templates/workload.html` | Partial | Two column headers, empty state partial |
| `templates/wialon_mapping_list.html` | Not read | Likely same as wialon.html |
| `templates/wialon_report.html` | Not read | Likely Uzbek |
| `templates/wialon_auto_match.html` | Not read | Likely Uzbek |
| `templates/spare_parts_list.html` | Partial | ~8 labels hardcoded |
| `templates/spare_part_detail.html` | Partial | ~10 labels hardcoded |
| `templates/spare_part_form.html` | Not fully read | Has `← Рўйхат` hardcoded |
| `templates/spare_parts_catalog.html` | Not fully read | Has `← Рўйхат` hardcoded |
| `templates/admin_users.html` | Partial | "Наблюдатель" (RU only), "Блокланган" (UZ only) |
| `templates/admin_permissions.html` | Yes | Well translated |
| `templates/profile.html` | Yes | Well translated |
| `templates/ref_equipment.html` | Partial | Inline edit form labels hardcoded |
| `templates/ref_organizations.html` | Not fully read | |
| `templates/ref_work_types.html` | Not fully read | |
| `templates/ref_customers.html` | Not fully read | |
| `templates/error.html` | No | Back link hardcoded |
| `templates/fuel/dashboard.html` | No | Entirely Russian |
| `templates/fuel/warehouses.html` | No | Entirely Russian |
| `templates/fuel/transactions.html` | No | Entirely Russian |
| `templates/fuel/receipts.html` | Not read | Expect entirely Russian |
| `templates/fuel/stations.html` | Not read | Expect entirely Russian |
| `templates/fuel/initial_balance.html` | Not read | Expect entirely Russian |
| `app.py` | Flash only | 28 flash messages in Uzbek, no t() |
| `fuel_routes.py` | Flash only | 12 flash messages in Russian, no t() |
| `spare_parts.py` | Flash only | 9 flash messages in Uzbek, no t() |
| `wialon_import.py` | Flash only | 10+ flash messages in Uzbek, no t() |

---

## User-facing mojibake findings

**None.** All templates are valid UTF-8. No encoding corruption observed.

The grep pattern `Рў` matched `Рўйхат` (Uzbek: "list") in three spare parts templates
— this is correct Uzbek text, not mojibake.

---

## Translation gaps

### GAP-1 (CRITICAL): Fuel module entirely in Russian

All six `templates/fuel/*.html` are hardcoded Russian. Users with UZ language selected
see Russian labels throughout the fuel module.

Affected templates:
- `fuel/dashboard.html` — page title, stat cards, table headers, action buttons, empty states
- `fuel/warehouses.html` — form labels, table headers, action buttons, confirms
- `fuel/transactions.html` — filter labels, table headers, stat cards, empty state
- `fuel/receipts.html`, `fuel/stations.html`, `fuel/initial_balance.html` — not read, expect same

Sample hardcoded Russian strings:
```
"АЗС — Остатки топлива"
"Посл. синхр.", "Синхронизаций нет"
"История выдач", "Приходы", "Склады", "Нач. остатки"
"Выдано сегодня", "Складов", "АЗС активных"
"Остатки по складам", "Нач. остаток не задан"
"Склады и АЗС", "Добавить склад", "Сохранить", "Сброс"
"Редактировать", "Удалить", "Активна", "Отключена"
"Выдачи топлива (из Топаз)", "Транзакции (макс. 500)"
"С", "По", "Склад", "Показать"
"Кол-во выдач", "Итого литров", "Сумма"
"Дата/время", "АЗС", "Топливо", "Литры", "Цена", "Карта"
"Выдач за выбранный период нет."
"Убедитесь, что агент Топаз настроен и запущен."
```

### GAP-2 (HIGH): fuel_routes.py flash messages in Russian

12 flash messages in `fuel_routes.py` are hardcoded Russian, do not use `t()`.

| Line | Flash message |
|---|---|
| 174 | `'Введите название склада'` |
| 186 | `'Склад сохранён'` |
| 197 | `'Склад удалён'` |
| 228 | `'Заполните все поля'` |
| 248 | `'Начальный остаток сохранён'` |
| 309 | `'Заполните обязательные поля'` |
| 330 | `'Приход сохранён'` |
| 342 | `'Приход удалён'` |
| 418 | `'Заполните все поля'` |
| 430 | `'АЗС с Topaz ID {topaz_id} уже существует'` |
| 436 | `'АЗС сохранена'` |
| 447 | `'АЗС удалена'` |

### GAP-3 (MEDIUM): Other templates with hardcoded UZ-only text

**base.html:**
- Line 209: `Админ ▾` — admin dropdown link, not using `t()`
- Line 186: `Импорт / Маппинг` — Wialon dropdown link, hardcoded
- JS (line 261): `'Танланмаган'`, `'Барчаси'`, `'та танланди'` — multiselect widget strings

**wialon.html:**
- Lines 55–68: Period mode labels `Кунлик`, `Жорий ҳафта`, `Жорий ой` — not using `t()`
- Line 31: `Авто-маппинг ({{ pending_count }} та)` — notification text
- Line 38–39: Warning banner about unmapped vehicles — hardcoded Uzbek
- Line 161: `Ҳали импорт амалга оширилмаган.` — empty state, hardcoded

**workload.html:**
- Line 125: `Норма`, `Факт` — column sub-headers, hardcoded
- Line 187: `Танланган давр учун Виалон мотосоатлари мавжуд эмас.` — hardcoded

**deficiencies.html:**
- Lines 88–91: Card header `Камчиликлар рўйхати (...)` — hardcoded (key exists in `t()` but not called here)
- Line 157: `Камчиликлар киритилмаган` — empty state, hardcoded, key missing

**spare_parts_list.html:**
- Line 12: `Каталог` — button, not using `t()` (key exists in translations)
- Line 40: `— Барчаси —` — filter dropdown, hardcoded
- Line 47: `дан` / Line 51: `гача` — date range labels around date inputs
- Lines 74–76: Table headers `Позициялар`, `Ҳолат`, `Яратилган` — hardcoded
- Line 97: `Кўриш` — action button, hardcoded (key exists in translations)

**spare_part_detail.html:**
- Line 6: `← Рўйхат` — back button, hardcoded
- Line 11: `Маълумот` — card header
- Line 31: `Яратди` — label
- Line 57: `Позициялар (...)` — card header
- Lines 62–63: `Номи`, `Арт. рақами` — table headers
- Line 95: `Кўриб чиқиш` — review section header
- Line 88: `📤 {{ t('Юборилган') }}` — misuse: uses status word as action button label

**spare_part_form.html / spare_parts_catalog.html:**
- `← Рўйхат` — back button, hardcoded

**admin_users.html:**
- Line 33: `Наблюдатель` — Russian viewer role label, hardcoded (inconsistent with UZ/RU convention)
- Line 106: `Блокланган` — inactive user badge, hardcoded Uzbek

**ref_equipment.html (inline edit row):**
- Lines 166–178: `Тури`, `Номи`, `Рақам`, `Ўлчов`, `Нарх` — inline edit labels, not using `t()`

### GAP-4 (LOW): All flash messages bypass translation system

Flash messages in `app.py`, `spare_parts.py`, `wialon_import.py` are in Uzbek only.
They appear in the same HTML flash div regardless of language switch.
Fixing this requires either passing translated strings at flash time or translating
flash messages in the template — both are non-trivial architectural changes.

Scope: 50+ flash messages across 4 files. Lowest priority because UZ is the primary
business language and flash messages are operational (save/delete confirmations).

---

## Missing translation keys

| Key | Where used | Missing from |
|---|---|---|
| `'КАТЕГОРИЯ'` | `report.html` line 36 | `translations.py` (only `'Категория'` exists) |
| `'Қидириш'` | `ref_equipment.html` line 59 | `translations.py` |
| `'Наблюдатель'` | `admin_users.html` line 33 | `translations.py` |
| `'Блокланган'` | `admin_users.html` line 106 | `translations.py` |
| `'Кунлик'` | `wialon.html` line 55 | `translations.py` |
| `'Жорий ҳафта'` | `wialon.html` line 58 | `translations.py` |
| `'Жорий ой'` | `wialon.html` line 61 | `translations.py` |
| `'Камчиликлар киритилмаган'` | `deficiencies.html` line 157 | `translations.py` |
| `'Норма'` | `workload.html` line 125 | `translations.py` |
| `'Факт'` | `workload.html` line 125 | `translations.py` |
| `'Позициялар'` | `spare_parts_list.html`, `spare_part_detail.html` | `translations.py` |
| `'Ҳолат'` | `spare_parts_list.html` line 75 | `translations.py` |
| `'Яратилган'` | `spare_parts_list.html` line 76 | `translations.py` |
| `'Яратди'` | `spare_part_detail.html` line 31 | `translations.py` |
| `'Маълумот'` | `spare_part_detail.html` line 11 | `translations.py` |
| `'← Рўйхат'` | spare parts templates (3 files) | `translations.py` |
| `'Арт. рақами'` | `spare_part_detail.html` line 62 | `translations.py` |
| `'Кўриб чиқиш'` | `spare_part_detail.html` line 95 | `translations.py` |
| `'Ҳали импорт амалга оширилмаган.'` | `wialon.html` line 161 | `translations.py` |

---

## Proposed safe fix list (TASK-UI-001B scope)

Ordered by impact and risk. All fixes are text/key changes only — no logic changes.

### Fix group A — translations.py: add missing keys (zero risk)

Add these UZ→RU pairs to `translations.py`:

| UZ key | RU value |
|---|---|
| `'КАТЕГОРИЯ'` | `'КАТЕГОРИЯ'` (same; keeps uppercase) |
| `'Қидириш'` | `'Поиск'` |
| `'Наблюдатель'` | `'Наблюдатель'` (keep RU, add UZ: `'Кузатувчи'`) |
| `'Блокланган'` | `'Заблокирован'` |
| `'Кунлик'` | `'Дневной'` |
| `'Жорий ҳафта'` | `'Текущая неделя'` |
| `'Жорий ой'` | `'Текущий месяц'` |
| `'Камчиликлар киритилмаган'` | `'Недостатков не добавлено'` |
| `'Норма'` | `'Норма'` |
| `'Факт'` | `'Факт'` |
| `'Позициялар'` | `'Позиции'` |
| `'Ҳолат'` | `'Статус'` |
| `'Яратилган'` | `'Создан'` |
| `'Яратди'` | `'Создал'` |
| `'Маълумот'` | `'Сведения'` |
| `'← Рўйхат'` | `'← Список'` |
| `'Арт. рақами'` | `'Арт. номер'` |
| `'Кўриб чиқиш'` | `'Рассмотрение'` |
| `'Ҳали импорт амалга оширилмаган.'` | `'Импорт ещё не выполнялся.'` |
| `'Адмін'` | `'Админ'` |
| `'Импорт / Маппинг'` | `'Импорт / Маппинг'` |
| `'Танланмаган'` | `'Не выбрано'` |
| `'та танланди'` | `'выбрано'` |

### Fix group B — templates: wrap plain Uzbek labels in t() (low risk)

After group A is done, wrap these in `t()`:

- `base.html`: `Админ ▾` → `{{ t('Адмін') }} ▾`
- `base.html`: `Импорт / Маппинг` → `{{ t('Импорт / Маппинг') }}`
- `base.html` JS: multiselect strings `'Танланмаган'`, `'Барчаси'`, `'та танланди'`
- `deficiencies.html`: card header `Камчиликлар рўйхати` → `{{ t('Камчиликлар рўйхати') }}`
- `deficiencies.html`: empty state → `{{ t('Камчиликлар киритилмаган') }}`
- `wialon.html`: period mode labels → `{{ t('Кунлик') }}`, etc.
- `wialon.html`: empty state → `{{ t('Ҳали импорт амалга оширилмаган.') }}`
- `workload.html`: `Норма`, `Факт` headers → `{{ t('Норма') }}`, `{{ t('Факт') }}`
- `spare_parts_list.html`: `Каталог` → `{{ t('Каталог') }}`
- `spare_parts_list.html`: `Кўриш` → `{{ t('Кўриш') }}`
- `spare_parts_list.html`: `— Барчаси —` → `{{ t('— Барчаси —') }}`
- `spare_parts_list.html`: table headers → wrap each in `t()`
- `spare_part_detail.html`: back button, card headers, labels → wrap in `t()`
- `spare_part_form.html` / `spare_parts_catalog.html`: `← Рўйхат` → `{{ t('← Рўйхат') }}`
- `admin_users.html`: `Наблюдатель` → `{{ t('Наблюдатель') }}`; `Блокланган` → `{{ t('Блокланган') }}`
- `ref_equipment.html`: inline edit labels → wrap in `t()`
- `report.html`: already uses `t('КАТЕГОРИЯ')` — just add the key in group A

### Fix group C — fuel module translation (high effort, high value)

Add UZ translations for all Russian strings in `templates/fuel/*.html`.
This requires adding ~30 new keys to `translations.py` and wrapping all fuel template
strings in `t()`.

Recommended approach: create a `FUEL_TRANS` section in `translations.py`.

Sample new keys needed (UZ/RU pairs):
```
'АЗС — Ёқилғи қолдиқлари'      ↔  'АЗС — Остатки топлива'
'Буgun берилди'                 ↔  'Выдано сегодня'
'Омборлар'                      ↔  'Склады'
'Фаол АЗС'                      ↔  'АЗС активных'
'Омбор бўйича қолдиқлар'        ↔  'Остатки по складам'
'Ёқилғи тури'                   ↔  'Вид топлива'
'Қолдиқ, л'                     ↔  'Остаток, л'
'Бошланғич қолдиқ белгиланмаган' ↔ 'Нач. остаток не задан'
...
```

### Fix group D — flash messages (low priority, complex)

Lowest risk approach: translate in `fuel_routes.py` only, by importing `g` and
checking `g.lang` at the point of flashing. This eliminates the Russian-only gap.

app.py, spare_parts.py, wialon_import.py flash messages are already Uzbek (the
primary business language) and are lower priority.

---

## Files recommended for next implementation phase (TASK-UI-001B)

Phase 1 (safe, contained):
1. `translations.py` — add all missing keys (Fix group A)
2. `deficiencies.html` — wrap 2 strings
3. `admin_users.html` — wrap 2 strings
4. `ref_equipment.html` — wrap 5 inline edit labels
5. `spare_parts_list.html` — wrap ~8 strings
6. `spare_part_detail.html` — wrap ~10 strings
7. `spare_part_form.html`, `spare_parts_catalog.html` — wrap back button
8. `wialon.html` — wrap period mode labels + empty state
9. `workload.html` — wrap 2 column headers + empty state text
10. `base.html` — wrap Admin link + Wialon dropdown link + JS strings
11. `report.html` — no code change needed (key added in step 1)

Phase 2 (larger scope):
12. `fuel/dashboard.html` — full translation pass
13. `fuel/warehouses.html` — full translation pass
14. `fuel/transactions.html` — full translation pass
15. `fuel/receipts.html`, `fuel/stations.html`, `fuel/initial_balance.html`
16. `fuel_routes.py` — replace Russian flash messages with UZ (or bilingual)

---

## Risks

1. **Typos in new translation keys.** If a key in a template does not exactly match
   its entry in `translations.py`, `t()` silently returns the raw key string. This is
   invisible at startup; manual testing is required.

2. **JS strings in base.html.** The multiselect widget (`initMultiselect`) uses
   `triggerLabel` strings passed from Jinja2. Changing these requires updating all
   call sites that pass labels (`'{{ t("Ташкилот") }}'`). Risk: if call sites are
   missed, some multiselects show untranslated labels.

3. **Fuel template scope.** Fuel module has ~30 Russian strings. Adding UZ translations
   requires business confirmation of correct Uzbek terms for fuel-specific vocabulary
   (омбор / склад, ёқилғи қолдиқлари, АЗС, etc.).

4. **Flash message translation architecture.** Translating flash messages properly
   requires access to the user's language at flash time. Simplest approach: pass
   translated strings directly (use `g.lang` in route functions). Risk: if `g.lang`
   is not set at every flash point, fallback needed.

5. **`spare_part_detail.html` action button label.** Line 88 uses `t('Юборилган')`
   (submitted/sent) as a submit action button. This is semantically wrong — the button
   should say "Юбориш" (submit/send), not the status word. Changing this is a safe
   UX fix but needs confirmation before editing.

---

## Manual test checklist (for TASK-UI-001B after implementation)

Phase 1 — VERIFIED 2026-05-23:
- [x] Login, switch to RU — verify all nav links show Russian
- [x] Login, switch to UZ — verify all nav links show Uzbek
- [x] Dashboard: date mode tabs translate correctly
- [x] Dashboard: multiselect dropdowns show correct language labels
- [x] Daily entry: category headers translate
- [x] Report page: КАТЕГОРИЯ multiselect shows correct label
- [x] Deficiencies: card header and empty state translate
- [x] Wialon import: period mode tabs translate
- [x] Wialon workload: Норма / Факт column headers translate
- [x] Spare parts list: table headers, buttons translate
- [x] Spare part detail: all labels translate
- [x] Admin users: "Наблюдатель" and "Блокланган" translate
- [x] Equipment reference: inline edit form labels translate

Phase 2 — TASK-UI-001C COMPLETED 2026-05-23, verified 2026-05-23:
- [x] Fuel dashboard: all labels show in user's language
- [x] Fuel warehouses, transactions, receipts, stations, initial balance — translate
- [x] Flash messages in fuel_routes.py — bilingual via fuel_t()
- [x] No visible `t(` or untranslated key strings appear on any page
- Known: JS confirm() dialogs with Jinja variables (wh.name, st.name) not translated.

---

## Commands run

```cmd
"C:\Program Files\Python314\python.exe" -m py_compile translations.py app.py fuel_routes.py spare_parts.py wialon_import.py workload_report.py
```

Result: ALL_PASS — no syntax errors in any of the six files.

No database queries run. No files modified except docs/. No service restarts.
