# MIGRATION_BACKFILL_ANALYSIS.md

Task: TASK-OPS-002A  
Analysis date: 2026-05-23  
Analyst: Claude Code (claude-sonnet-4-6)  
Status: **TASK-OPS-002A complete. TASK-OPS-002B complete — backfill script run successfully on production 2026-05-23. schema_migrations verified with 10 rows. Awaiting operator confirmation of 5 pending scripts (TASK-OPS-002C).**

---

## Summary

15 migration scripts exist in `C:\transport-report\`.  
Only `migrate_000_migration_registry` is currently recorded in `schema_migrations`.  
This document provides evidence-based classifications for all 14 remaining scripts.

Key findings:

- **8 scripts** are CONFIRMED_APPLIED based on schema evidence.
- **3 scripts** are LIKELY_APPLIED (data-only migrations; old_transport.db exists).
- **1 script** (migrate_v42.py) is LIKELY_APPLIED but superseded by migrate_to_v45.py.
- **1 script** (migrate_v47.py) is NOT_APPLIED — schema in DB contradicts what this script would have created.
- **1 script** (migrate_000_migration_registry.py) is already recorded in the registry.

**AGENT_STATE.md correction:** The file previously stated
"migrate_000_migration_registry.py has NOT been run on production yet."
The database confirms it WAS run on 2026-05-22T16:48:29. AGENT_STATE.md has been updated.

---

## Current schema_migrations rows

After TASK-OPS-002B run on 2026-05-23, production has 10 rows:

```
id=1   name=migrate_000_migration_registry          applied_at=2026-05-22T16:48:29
id=2   name=migrate_to_v3                           (backfilled 2026-05-23)
id=3   name=migrate_add_wialon                      (backfilled 2026-05-23)
id=4   name=migrate_to_v45                          (backfilled 2026-05-23)
id=5   name=migrate_v46                             (backfilled 2026-05-23)
id=6   name=migrate_tasks_abc3                      (backfilled 2026-05-23)
id=7   name=migrate_fuel_v2                         (backfilled 2026-05-23)
id=8   name=migrate_equipment_excel                 (backfilled 2026-05-23)
id=9   name=migrate_module_permissions              (backfilled 2026-05-23)
id=10  name=migrate_001_backfill_historical_registry (self-recorded 2026-05-23)
```

Run output: inserted=8, skipped=0. Service restarted cleanly.

---

## Database context at time of analysis

Production database: `instance/transport.db`

Table counts:

| Table | Count |
|---|---|
| organizations | 17 |
| equipment | 336 |
| daily_records | 10 281 |
| work_types | 94 |
| deficiencies | 29 |
| engine_hours_records | 9 870 |
| vialon_mappings | 379 |
| vialon_imports | 169 |
| fuel_warehouses | 10 |
| fuel_stations2 | 21 |
| fuel_transactions2 | 391 225 |
| fuel_stations (v1) | 11 |
| fuel_transactions (v1) | 0 |
| fuel_snapshots (v1) | 8 |
| fuel_balances (v1) | 4 |
| fuel_receipts (v1) | 0 |
| app_modules | 5 |
| user_module_permissions | 5 |
| spare_parts | 0 |
| spare_part_requests | 1 |
| spare_part_request_items | 1 |
| users | 2 |
| schema_migrations | 1 |

Old database: `old_transport.db` — **exists** on production server.

| Old table | Count |
|---|---|
| equipment | 244 |
| daily_records | 302 |
| work_types | 34 |
| organizations | 10 |

External files:

- `Агрокластер_Техника_Консолидация.xlsx` — **present** in project directory.
- `pandas` — **available** (version 3.0.2).

---

## Historical migration inventory and evidence

### migrate.py

**Purpose:** One-time data migration — copies `daily_records` from `old_transport.db`
to `instance/transport.db`.  
No schema changes.

**Expected effects:**
- daily_records rows imported (up to 302, depending on equipment matches).

**Evidence:**
- old_transport.db exists with 302 daily_records starting 2026-03-31.
- New DB has 10 281 daily_records also starting 2026-03-31 (same start date).
- A data bootstrap run is consistent with the production timeline.
- Cannot distinguish imported rows from manually entered rows.

**Classification:** LIKELY_APPLIED  
**Confidence:** Low (data only; no schema signature).  
**Backfill:** Requires human confirmation.

---

### migrate_equipment.py

**Purpose:** One-time data migration — copies `equipment` rows from `old_transport.db`
to `instance/transport.db`.  
No schema changes.

**Expected effects:**
- Up to 244 equipment rows imported.

**Evidence:**
- old_transport.db exists with 244 equipment rows.
- New DB has 336 equipment; 244 base import followed by migrate_equipment_excel.py
  expansion to 336 is a plausible sequence.
- Cannot independently confirm; migrate_equipment_excel.py may have produced the
  same final state even if this script was skipped.

**Classification:** LIKELY_APPLIED  
**Confidence:** Low (data only; no schema signature).  
**Backfill:** Requires human confirmation.

---

### migrate_worktypes.py

**Purpose:** One-time data migration — copies `work_types` rows from `old_transport.db`
to `instance/transport.db`.  
No schema changes.

**Expected effects:**
- Up to 34 work_type rows imported.

**Evidence:**
- old_transport.db exists with 34 work_types.
- New DB has 94 work_types (34 imported + 60 added manually via admin UI).
- Consistent with this script having been run during initial setup.

**Classification:** LIKELY_APPLIED  
**Confidence:** Low (data only; no schema signature).  
**Backfill:** Requires human confirmation.

---

### migrate_to_v3.py

**Purpose:** Schema migration — creates `deficiencies` table and its index;
reclassifies certain equipment from category `yukori` to `yuk_transport`.

**Expected effects:**
- `deficiencies` table with columns: id, work_date, sort_order, text,
  organization_id, created_by, created_at.
- Index: `ix_deficiencies_date` on `deficiencies(work_date)`.
- Equipment rows reclassified to `yuk_transport` based on eq_type/name keywords.

**Evidence:**
- `deficiencies` table EXISTS with 29 rows; schema matches exactly.
- `ix_deficiencies_date` index EXISTS (confirmed in sqlite_master).
- `yuk_transport` category has 31 equipment records.
- All columns present: work_date, sort_order, text, organization_id, created_by, created_at.

**Classification:** CONFIRMED_APPLIED  
**Confidence:** High.  
**Backfill:** Safe to backfill.

---

### migrate_add_wialon.py

**Purpose:** Schema migration — creates `vialon_mappings`, `vialon_imports`,
`engine_hours_records` tables and their indexes.

**Expected effects:**
- `vialon_mappings` table (UNIQUE on vialon_name).
- `vialon_imports` table.
- `engine_hours_records` table (UNIQUE on work_date, equipment_id).
- Index: `ix_engine_hours_date`.

**Evidence:**
- `vialon_mappings` EXISTS with 379 rows; UNIQUE autoindex present.
- `vialon_imports` EXISTS with 169 rows.
- `engine_hours_records` EXISTS with 9 870 rows.
- `ix_engine_hours_date` index EXISTS.
- `sqlite_autoindex_engine_hours_records_1` (UNIQUE constraint) EXISTS.

**Classification:** CONFIRMED_APPLIED  
**Confidence:** High.  
**Backfill:** Safe to backfill.

---

### migrate_v42.py

**Purpose:** Schema migration — adds column `unknown_vehicles_json` to `vialon_imports`.

**Expected effects:**
- `vialon_imports.unknown_vehicles_json TEXT DEFAULT '[]'` column added.

**Evidence:**
- Column EXISTS in `vialon_imports`: confirmed by PRAGMA table_info.
- `migrate_to_v45.py` also adds this same column (it is a "combined" migration
  that includes all v4.2 changes). Both scripts are idempotent for this column.
- Cannot determine which script added the column first.

**Classification:** LIKELY_APPLIED  
**Confidence:** Medium.  
**Backfill:** Requires human decision — see risks section below.

**Note:** `migrate_to_v45.py` supersedes this script. If only one is to be backfilled,
prefer `migrate_to_v45.py`. Recording both is acceptable but creates registry noise.

---

### migrate_to_v45.py

**Purpose:** Combined schema migration — v4.2 (`unknown_vehicles_json`) plus v4.5
(fuel v1 tables: fuel_stations, fuel_tanks, fuel_snapshots, fuel_transactions, fuel_sync_logs).

**Expected effects:**
- Column `unknown_vehicles_json` on `vialon_imports`.
- `fuel_stations` table (UNIQUE on pos_id).
- `fuel_tanks` table.
- `fuel_snapshots` table (UNIQUE on station_id, tank_name).
- `fuel_transactions` table.
- `fuel_sync_logs` table.
- Indexes: `ix_fuel_snap_date`, `ix_fuel_tx_date`, `ix_fuel_sync_date`.

**Evidence:**
- `unknown_vehicles_json` column EXISTS.
- `fuel_stations` EXISTS with 11 rows; `sqlite_autoindex_fuel_stations_1` (UNIQUE) present.
- `fuel_tanks` EXISTS.
- `fuel_snapshots` EXISTS with 8 rows; `sqlite_autoindex_fuel_snapshots_1` present.
- `fuel_transactions` EXISTS (0 rows).
- `fuel_sync_logs` EXISTS.
- `ix_fuel_snap_date`, `ix_fuel_tx_date`, `ix_fuel_sync_date` indexes all present.

**Classification:** CONFIRMED_APPLIED  
**Confidence:** High.  
**Backfill:** Safe to backfill.

---

### migrate_v46.py

**Purpose:** Schema migration — adds `fuel_balances` and `fuel_receipts` tables
(fuel v1 — linked to `fuel_stations`, not `fuel_warehouses`).

**Expected effects:**
- `fuel_balances` table (UNIQUE on station_id, balance_date, fuel_name).
- `fuel_receipts` table.
- Indexes: `ix_fuel_bal_date`, `ix_fuel_rec_date`.

**Evidence:**
- `fuel_balances` EXISTS with 4 rows. Schema: (id, station_id, balance_date, fuel_name,
  volume, note, created_by, created_at) — matches this script exactly. station_id
  confirmed to reference `fuel_stations` IDs (values 2, 3, 7 observed).
- `fuel_receipts` EXISTS (0 rows). Schema matches.
- `ix_fuel_bal_date` and `ix_fuel_rec_date` indexes EXIST.
- `sqlite_autoindex_fuel_balances_1` (UNIQUE constraint) PRESENT.

**Classification:** CONFIRMED_APPLIED  
**Confidence:** High.  
**Backfill:** Safe to backfill.

---

### migrate_v47.py

**Purpose:** Schema migration — creates `app_modules` (with icon, sort_order columns),
`user_module_permissions` (with can_view, can_edit columns), adds `users.language`,
and seeds 6 modules (entry, wialon, fuel, reports, deficiency, refs).

**Expected effects IF applied:**
- `app_modules` columns: id, code, name_uz, name_ru, **icon**, **sort_order**.
- `user_module_permissions` columns: id, user_id, module_code, **can_view**, **can_edit**.
- 6 module rows seeded: entry, wialon, fuel, reports, deficiency, refs.

**Actual database state:**
- `app_modules` columns: id, code, name_uz, name_ru, **is_active** — DIFFERENT from
  what this script creates (no icon, no sort_order column).
- `user_module_permissions` columns: id, user_id, module_code, **has_access** — DIFFERENT
  from what this script creates (no can_view, no can_edit).
- `app_modules` rows use codes: transport, wialon, fuel, deficiencies, spare_parts —
  DIFFERENT from what this script would seed (entry, wialon, fuel, reports, deficiency, refs).

**Classification:** NOT_APPLIED  
**Confidence:** Very high. Schema mismatch on two tables is conclusive.  
**Backfill:** DO NOT backfill. Recording this script as applied would be factually false.

**Action required:** This script is obsolete. It was superseded by `migrate_tasks_abc3.py`
which uses a different (and currently active) schema. Consider adding a comment to
`migrate_v47.py` marking it as obsolete so no one runs it in future.

---

### migrate_fuel_v2.py

**Purpose:** Schema + data migration — creates fuel v2 tables via `db.create_all()`,
then seeds 10 warehouses and 21 stations from hardcoded SEED_DATA.

**Expected effects:**
- `fuel_warehouses` table.
- `fuel_stations2` table.
- `fuel_initial_balances` table.
- `fuel_receipts2` table.
- `fuel_transactions2` table.
- `fuel_sync_logs2` table.
- 10 warehouses seeded (Заминлари, Чорва, Мирзачул ПТЗ, Когон ПТЗ, Гиждувон,
  Гарден, Пешку ПТЗ, Пешку Сервис, Шофиркон ПТЗ, Пахтасаноаттранс).
- 21 stations seeded (total from SEED_DATA: 1+1+3+3+5+1+2+1+2+2 = 21).

**Evidence:**
- All 6 fuel v2 tables EXIST.
- `fuel_warehouses` count: **10** — matches SEED_DATA exactly (10 warehouses).
- `fuel_stations2` count: **21** — matches SEED_DATA exactly (21 stations total).
- `fuel_transactions2` count: 391 225 — active production data.
- The exact 10/21 match with SEED_DATA is strong evidence.

**Classification:** CONFIRMED_APPLIED  
**Confidence:** High.  
**Backfill:** Safe to backfill.

**Note:** This script uses `db.create_all()` inside a Flask app context, which creates
ALL models defined at the time of execution, not only fuel v2 models. Some other tables
visible in the DB may have been created by this call in addition to the fuel v2 tables.

---

### migrate_tasks_abc3.py

**Purpose:** Schema migration — adds `users.language` column, creates `app_modules`,
`user_module_permissions`, `spare_parts`, `spare_part_requests`, `spare_part_request_items`.

**Expected effects:**
- `users.language VARCHAR(5) DEFAULT 'uz'` column.
- `app_modules` with columns: id, code, name_uz, name_ru, is_active.
- `user_module_permissions` with columns: id, user_id, module_code, has_access.
- `spare_parts` table.
- `spare_part_requests` table.
- `spare_part_request_items` table.

**Evidence:**
- `users.language` column EXISTS (confirmed by PRAGMA table_info).
- `app_modules` schema EXACTLY matches: id, code, name_uz, name_ru, is_active.
- `user_module_permissions` schema EXACTLY matches: id, user_id, module_code, has_access.
- `spare_parts` EXISTS (0 rows).
- `spare_part_requests` EXISTS (1 row).
- `spare_part_request_items` EXISTS (1 row).

**Classification:** CONFIRMED_APPLIED  
**Confidence:** Very high. Schema on two tables matches this script and contradicts migrate_v47.py.  
**Backfill:** Safe to backfill.

---

### migrate_categories_v9.py

**Purpose:** Data migration — reclassifies equipment rows in category `yukori` to
`combine` or `special` based on eq_type keyword matching.  
No schema changes.

**Expected effects:**
- Equipment rows with combine keywords → category `combine`.
- Equipment rows with special keywords → category `special`.

**Evidence:**
- `combine` category: 34 records PRESENT.
- `special` category: 27 records PRESENT.
- `yukori` category: 45 records remain (not all yukori equipment is reclassified).
- The fact that both combine and special categories are populated is consistent with
  this script having run, but migrate_equipment_excel.py also sets category codes
  from Excel data, making independent confirmation impossible.
- MIGRATIONS.md states this script should run "BEFORE migrate_equipment_excel.py".

**Classification:** LIKELY_APPLIED  
**Confidence:** Medium (no schema signature; effects overlap with migrate_equipment_excel.py).  
**Backfill:** Requires human confirmation.

---

### migrate_equipment_excel.py

**Purpose:** Data + reference migration — reads equipment from Excel, renames 6 old
organizations to canonical names, adds 4 new organizations, updates/adds equipment
with canonical 9-category codes.

**Expected effects:**
- Organization renames: 6 old names → canonical names.
- 4 new organizations added: Агрокластер, Глобал мегатекс, Голд гранит, Уругчилик.
- Equipment updated (category, eq_type, org_id) or added from Excel.
- All 9 category codes populated.

**Evidence:**
- 17 organizations PRESENT. 4 new ones (Агрокластер, Глобал мегатекс, Голд гранит,
  Уругчилик) present at IDs 12–15, consistent with being added after the original 9.
- Canonical organization names confirmed (Гиждувон ПТЗ, Мирзачул, Пешку Сервис, etc.).
- All 9 categories populated: yukori(45), mtz(116), qatnov(74), mini(4), combine(34),
  special(27), yuk_transport(31), motorcycle(4), passenger(1) = 336 total.
- 336 equipment count matches PROJECT_BRIEF.md ("336 equipment records").
- Excel file `Агрокластер_Техника_Консолидация.xlsx` EXISTS in project directory.
- pandas 3.0.2 is available.

**Classification:** CONFIRMED_APPLIED  
**Confidence:** High.  
**Backfill:** Safe to backfill.

---

### migrate_module_permissions.py

**Purpose:** Data migration — ensures all non-admin users have an explicit
`has_access=True` record for every active module. Uses INSERT OR IGNORE.

**Expected effects:**
- user_module_permissions rows inserted where none existed.

**Evidence:**
- Explicitly documented as "executed successfully on production" in:
  - `docs/AGENT_STATE.md` (TASK-SEC-001 section).
  - `docs/DECISIONS.md` ADR-008.
- `user_module_permissions` has 5 rows (1 non-admin user × 5 active modules).
- Some rows have `has_access=0` (wialon, fuel, spare_parts for user 2), indicating
  those records pre-existed with has_access=0 before this script ran (INSERT OR IGNORE
  would skip them), or were modified afterward via admin UI.

**Classification:** CONFIRMED_APPLIED  
**Confidence:** Very high (explicit documentation in two project files).  
**Backfill:** Safe to backfill.

---

## Evidence table

| Script | Classification | Key Evidence |
|---|---|---|
| migrate.py | LIKELY_APPLIED | old_transport.db has 302 records; new DB records start same date (2026-03-31) |
| migrate_equipment.py | LIKELY_APPLIED | old_transport.db has 244 equipment; 336 in new DB is consistent with import + Excel expansion |
| migrate_worktypes.py | LIKELY_APPLIED | old_transport.db has 34 work_types; new DB has 94 (34 imported + 60 manual) |
| migrate_to_v3.py | CONFIRMED_APPLIED | deficiencies table + ix_deficiencies_date index both present |
| migrate_add_wialon.py | CONFIRMED_APPLIED | vialon_mappings (379), vialon_imports (169), engine_hours_records (9870), ix_engine_hours_date index all present |
| migrate_v42.py | LIKELY_APPLIED | unknown_vehicles_json column present; cannot distinguish from migrate_to_v45.py |
| migrate_to_v45.py | CONFIRMED_APPLIED | All fuel v1 tables + ix_fuel_snap_date, ix_fuel_tx_date, ix_fuel_sync_date indexes present |
| migrate_v46.py | CONFIRMED_APPLIED | fuel_balances (4 rows), fuel_receipts (0 rows); schema and indexes match exactly |
| migrate_v47.py | NOT_APPLIED | app_modules has is_active column, not icon/sort_order; user_module_permissions has has_access, not can_view/can_edit; module codes differ |
| migrate_fuel_v2.py | CONFIRMED_APPLIED | fuel_warehouses=10, fuel_stations2=21 match SEED_DATA exactly |
| migrate_tasks_abc3.py | CONFIRMED_APPLIED | app_modules and user_module_permissions schemas match this script exactly; all spare parts tables present |
| migrate_categories_v9.py | LIKELY_APPLIED | combine=34 and special=27 present; overlaps with migrate_equipment_excel.py effects |
| migrate_equipment_excel.py | CONFIRMED_APPLIED | 336 equipment, 17 orgs, 4 new orgs at IDs 12–15, all 9 categories populated; Excel file present |
| migrate_module_permissions.py | CONFIRMED_APPLIED | Documented in AGENT_STATE.md and DECISIONS.md ADR-008; 5 permission rows exist |

---

## Recommended backfill list (safe to backfill without further confirmation)

Backfill these 8 scripts in the order listed. All are CONFIRMED_APPLIED based on
schema evidence or explicit documentation.

| Order | Script | Reason safe |
|---|---|---|
| 1 | migrate_to_v3 | deficiencies table + index confirmed |
| 2 | migrate_add_wialon | all 3 wialon tables + indexes confirmed |
| 3 | migrate_to_v45 | all fuel v1 tables + indexes confirmed |
| 4 | migrate_v46 | fuel_balances, fuel_receipts + indexes confirmed |
| 5 | migrate_tasks_abc3 | exact schema match on 2 tables; spare parts tables confirmed |
| 6 | migrate_fuel_v2 | warehouse count (10) and station count (21) match SEED_DATA exactly |
| 7 | migrate_equipment_excel | 336 equipment, 4 new orgs, 9 categories, Excel file present |
| 8 | migrate_module_permissions | explicitly documented in AGENT_STATE.md + DECISIONS.md |

---

## Scripts requiring human confirmation before backfilling

| Script | Classification | Why confirmation needed |
|---|---|---|
| migrate.py | LIKELY_APPLIED | Data only; cannot distinguish from manual entry; operator must confirm they ran it during setup |
| migrate_equipment.py | LIKELY_APPLIED | Data only; effects overlap with migrate_equipment_excel.py; operator must confirm |
| migrate_worktypes.py | LIKELY_APPLIED | Data only; work_types count consistent but not conclusive; operator must confirm |
| migrate_v42.py | LIKELY_APPLIED | Superseded by migrate_to_v45.py; operator should decide whether to record both or skip migrate_v42.py |
| migrate_categories_v9.py | LIKELY_APPLIED | Data only; effects indistinguishable from migrate_equipment_excel.py; operator must confirm |

---

## Scripts that must NOT be backfilled

| Script | Classification | Reason |
|---|---|---|
| migrate_v47.py | NOT_APPLIED | DB schema contradicts what this script creates; recording it as applied would be false |
| migrate_000_migration_registry.py | Already registered | Row present in schema_migrations; nothing to backfill |

---

## Risks

1. **migrate_v47.py is dangerous if run in future.**  
   It is still present in the project directory. If someone runs it:
   - `CREATE TABLE IF NOT EXISTS` will skip the existing app_modules and
     user_module_permissions tables (schema mismatch is silent — existing tables survive).
   - The seed INSERT OR IGNORE statements will add 6 wrong module codes
     (entry, reports, deficiency, refs) that the application does not recognize.
   - This could cause subtle permission UI bugs.  
   **Recommendation:** Add a comment to `migrate_v47.py` marking it as
   OBSOLETE/SUPERSEDED BY migrate_tasks_abc3.py.

2. **migrate_v42.py overlaps with migrate_to_v45.py.**  
   Both scripts add the same column. If both are backfilled, the registry will have
   two entries for the same schema change. This is not harmful but creates noise.
   If only one is backfilled, prefer `migrate_to_v45.py` (the combined migration).

3. **Data migration scripts (migrate.py, migrate_equipment.py, migrate_worktypes.py)
   may give a false sense of coverage.**  
   These scripts perform data imports, not schema changes. Backfilling them in the
   registry signals "this was applied" but does not protect against running them again
   (they do have idempotency via duplicate checks, so running again is safe but wasteful).

4. **migrate_fuel_v2.py uses db.create_all().**  
   This creates all SQLAlchemy-defined tables at the time it ran. The set of models
   may have been different from the current models.py. Some tables now in the DB may
   have been created by this call as a side effect (e.g., user_organizations if it
   was already defined in models.py at that time).

5. **AGENT_STATE.md was stale.**  
   It stated `migrate_000_migration_registry.py` had not been run on production, but
   the database confirms it was run on 2026-05-22T16:48:29. AGENT_STATE.md has been
   corrected in this session.

6. (RESOLVED) **Schema_migrations now has 10 rows** after TASK-OPS-002B ran successfully
   on 2026-05-23. The 8 CONFIRMED_APPLIED scripts are now registered. Future migration
   scripts can safely use `is_migration_applied()` for those 8 dependencies.
   The 5 LIKELY_APPLIED scripts remain unregistered until TASK-OPS-002C confirmation.

---

## TASK-OPS-002B outcome (2026-05-23 — COMPLETED)

`migrate_001_backfill_historical_registry.py` was run successfully on production.

**Actual run results:**

- inserted=8, skipped=0
- Self-recorded as migrate_001_backfill_historical_registry
- `schema_migrations` verified with 10 rows (see section above)
- TransportReport service started successfully after the migration

`migrate_v47.py` has an OBSOLETE warning block at the top. Its logic is unchanged;
the warning prevents accidental execution.

## TASK-OPS-002C — Pending operator confirmation

The 5 scripts below are excluded from the current backfill. The operator must
confirm each one before TASK-OPS-002C creates a follow-up backfill entry.

**The operator must confirm:**

- [ ] Yes, migrate.py was run during initial setup (imported daily_records from old_transport.db).
- [ ] Yes, migrate_equipment.py was run during initial setup (imported equipment from old_transport.db).
- [ ] Yes, migrate_worktypes.py was run during initial setup (imported work_types from old_transport.db).
- [ ] Yes, migrate_categories_v9.py was run before migrate_equipment_excel.py.
- [ ] Regarding migrate_v42.py: record it, skip it (prefer migrate_to_v45.py already recorded), or record both?
- [ ] Acknowledged: migrate_v47.py is NOT applied and must NOT be backfilled.
