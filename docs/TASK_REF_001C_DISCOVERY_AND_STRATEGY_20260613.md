# TASK-REF-001C Discovery and Cleanup Strategy

Date: 2026-06-13

## Purpose

TASK-REF-001C is a read-only analysis stage.

The goal is to prepare a safe strategy for cleaning reference data without changing historical records automatically.

This stage does not apply migrations and does not modify production data.

## Production state during discovery

Git state before discovery:

- `HEAD = origin/main = ba318b1`
- `git status = clean`

Database counts:

- `work_types = 104`
- `customers = 9`
- `daily_records = 15946`

## Work types analysis

### Duplicate work type names

Detected duplicate name groups:

1. `Пахта ташиш`
   - `id=31`, unit=`Рейс`, price=`300600`, usage by text name=`296`
   - `id=35`, unit=`тн/км`, price=`3340`, usage by text name=`296`

2. `Тола ташиш`
   - `id=47`, unit=`рейс`, price=`6600000`, usage by text name=`47`
   - `id=48`, unit=`Соат`, price=`178660`, usage by text name=`47`

3. `Тупроқ юклаш`
   - `id=54`, unit=`Мотосоат`, price=`340000`, usage by text name=`40`
   - `id=88`, unit=`соат`, price=`250000`, usage by text name=`40`

Important technical note:

- `daily_records.work_type` stores text, not `work_type_id`.
- Therefore duplicate rows with the same name cannot be linked to historical records by ID.
- The reported usage count is duplicated for rows with the same name because the query matches by text name.
- It is unsafe to merge or delete these rows automatically.

### Work type values used in reports but missing from reference table

Detected 5 exact missing values:

1. `Сут ва тайёр маҳсулотлар`
   - usage: `9`
   - no normalized match

2. `ер атрофини чизел килиш`
   - usage: `5`
   - normalized match: `Ер атрофини чизел килиш`

3. `__custom__`
   - usage: `2`
   - no normalized match

4. `Культивация Нақд (ЁММсиз)`
   - usage: `1`
   - no normalized match

5. `чигит ташиш`
   - usage: `1`
   - normalized match: `Чигит ташиш`

### Work type quality issues

Empty default unit:

- `id=90`, `Шудгор (нақд ёқилғисиз)`, price=`350000`, usage=`59`

Zero default price:

- `id=92`, `Хар хил иш (рейс)`, unit=`Рейс`, usage=`5`
- `id=66`, `Шоли ташиш`, unit=`м-соат`, usage=`2`

## Customer analysis

### Current customer reference table

Only 9 customers exist in the `customers` table.

No duplicate customer names were detected inside the reference table.

### Customer values used in reports

Production `daily_records.customer` contains:

- distinct used values: `2028`
- missing from `customers` reference table: `2020`

This means `daily_records.customer` is not currently being used as a strict reference to the `customers` table.

It contains mixed free-text values, including:

- official customers
- internal organizations
- fields
- PTZ/factory destinations
- transport routes
- farmers
- departments
- free-form notes
- malformed imported strings

### Top missing customer values by usage

Examples:

- `Чорва`  `233`
- `Фукаро`  `135`
- `Гарден`  `117`
- `Заминлари`  `86`
- `Фуқаро`  `71`
- `Урта чул янги ерлари`  `65`
- `Мирзачул`  `40`
- `Когон ПТЗ`  `37`
- `Кластер`  `34`
- `Завод олди пр1`  `30`
- `Арнасой ПТМ`  `29`
- `Самарқанд`  `28`
- `Гиждувон ПТЗ`  `27`
- `Жондор ПТЗ-Когон ПТЗ`  `27`
- `Жондор-Когон ПТЗ`  `27`
- `Жондор ПТМ-Когон ПТЗ`  `26`

### Pattern groups

Detected customer pattern groups:

- contains `кластер`: `394` distinct values, total usage `825`
- contains `булинма/булим`: `38` distinct values, total usage `104`
- contains `птз`: `94` distinct values, total usage `378`
- contains `сервис/service`: `63` distinct values, total usage `132`
- starts with number: `614` distinct values, total usage `1033`
- has quote/tab artifacts: `2` distinct values
- looks like phone/malformed pasted row: `1` distinct value
- very long over 100 chars: `1` distinct value

### Similarity groups

Detected 219 possible normalization groups.

High-confidence examples:

- `Чорва` / `чорва`
- `Фукаро` / `фукаро` / `ФУКАРО`
- `Гарден` / `Гарден ери`
- `Заминлари` / `заминлари`
- `Когон ПТЗ` / `КОГОН ПТЗ`
- `Кластер` / `Кластер ери`
- `Жондор-Когон ПТЗ` / `Жондор - Когон ПТЗ`
- multiple `Ғиждувон ёғ мой` spelling variants
- multiple `Пешку ёғ мой` spelling variants
- multiple numbered `Кластер` variants

## Risk assessment

### Safe without migration

The following can be done safely through UI/reference editing:

- Fill empty `default_unit`.
- Fill zero `default_price`.
- Add missing work types as new reference rows if business confirms them.
- Add frequently used customers as reference rows if business confirms them.
- Improve diagnostics/export screens.

### Requires controlled migration

The following must not be done manually or automatically without a separate migration plan:

- Renaming historical `daily_records.work_type`.
- Renaming historical `daily_records.customer`.
- Merging duplicate work types.
- Normalizing customer strings.
- Mapping free-text customer values to official customer rows.
- Removing duplicate work type rows.

### Requires data model decision

The current model stores:

- `DailyRecord.work_type` as text
- `DailyRecord.customer` as text

This prevents reliable ID-based cleanup.

Possible future model improvements:

1. Keep historical text fields but add optional reference IDs:
   - `daily_records.work_type_id`
   - `daily_records.customer_id`

2. Add alias/mapping tables:
   - `work_type_aliases`
   - `customer_aliases`

3. Add a controlled normalization queue:
   - raw value
   - suggested reference
   - confidence
   - approved by admin
   - applied/not applied
   - migration batch id

## Recommended next steps

### TASK-REF-001D  export and manual cleanup assistant

Build an admin-only diagnostic/export page or CSV/XLSX exports for:

- missing work type values
- duplicate work type names
- empty/default price issues
- top missing customer values
- customer similarity groups
- malformed customer rows

No data modifications.

### TASK-REF-001E  safe work type fixes

After business approval:

- Fill `Шудгор (нақд ёқилғисиз)` default unit.
- Fill prices for:
  - `Хар хил иш (рейс)`
  - `Шоли ташиш`
- Decide whether to add reference rows for:
  - `Сут ва тайёр маҳсулотлар`
  - `Культивация Нақд (ЁММсиз)`
- Decide whether to normalize only exact-case variants:
  - `ер атрофини чизел килиш`  `Ер атрофини чизел килиш`
  - `чигит ташиш`  `Чигит ташиш`

### TASK-REF-001F  customer normalization design

Do not normalize customers immediately.

First define business categories:

- internal organization
- external customer
- field/contour
- route/direction
- PTZ/factory destination
- farmer
- free text note

Then build a mapping interface with admin approval.

## Decision

TASK-REF-001C discovery confirms that automatic cleanup is unsafe.

The correct approach is staged:

1. Document current data quality.
2. Add export/diagnostic tools.
3. Fix simple reference defaults manually.
4. Design controlled mapping before touching historical customer/work_type text.

## Status

TASK-REF-001C discovery is complete.

No data was modified.
