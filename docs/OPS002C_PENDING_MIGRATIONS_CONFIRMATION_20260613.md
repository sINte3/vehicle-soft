# TASK-OPS-002C Pending Migration Confirmation

Date: 2026-06-13

## Summary

TASK-OPS-002A and TASK-OPS-002B are already completed.

Current registry status:

- migrate_000_migration_registry recorded.
- migrate_001_backfill_historical_registry recorded.
- 8 historical schema migrations were safely backfilled.
- 5 historical scripts remain pending because they are data-only or overlap with later scripts.

This document prepares the final owner confirmation step before any additional registry backfill.

## Current production status

Production HEAD after EXTAUDIT closure:

- c76ae42 Document EXTAUDIT001 closure

Expected current schema_migrations state:

- migrate_000_migration_registry
- migrate_to_v3
- migrate_add_wialon
- migrate_to_v45
- migrate_v46
- migrate_tasks_abc3
- migrate_fuel_v2
- migrate_equipment_excel
- migrate_module_permissions
- migrate_001_backfill_historical_registry

## Pending scripts

The following scripts are not yet marked as applied:

1. migrate.py
2. migrate_equipment.py
3. migrate_worktypes.py
4. migrate_v42.py
5. migrate_categories_v9.py

## Evidence from previous analysis

### migrate.py

Purpose:

- Data migration from old_transport.db to current daily_records.

Evidence:

- old_transport.db existed.
- old DB had daily_records.
- production daily_records start date was consistent with imported history.

Reason not auto-backfilled:

- Data-only migration.
- Cannot distinguish script-created rows from manually entered rows.

Required owner confirmation:

- Was migrate.py run during initial setup?

### migrate_equipment.py

Purpose:

- Data migration from old_transport.db to current equipment.

Evidence:

- old_transport.db existed.
- old DB had equipment.
- current equipment count was consistent with old import plus later Excel expansion.

Reason not auto-backfilled:

- Data-only migration.
- Effects overlap with migrate_equipment_excel.py and manual edits.

Required owner confirmation:

- Was migrate_equipment.py run during initial setup?

### migrate_worktypes.py

Purpose:

- Data migration from old_transport.db to current work_types.

Evidence:

- old_transport.db existed.
- old DB had work_types.
- current work_types count was consistent with old import plus later manual additions.

Reason not auto-backfilled:

- Data-only migration.
- Cannot distinguish script-created rows from manually entered rows.

Required owner confirmation:

- Was migrate_worktypes.py run during initial setup?

### migrate_v42.py

Purpose:

- Adds vialon_imports.unknown_vehicles_json.

Evidence:

- Column exists.
- migrate_to_v45 also includes the same effect.
- migrate_to_v45 is already safely recorded.

Reason not auto-backfilled:

- migrate_v42.py is superseded/overlapped by migrate_to_v45.py.
- Recording both may be acceptable but adds registry noise.

Required owner decision:

- Skip migrate_v42.py because migrate_to_v45 is already recorded?
- Or record migrate_v42.py as historical applied?

Recommended decision:

- Skip migrate_v42.py unless there is clear evidence it was run separately before migrate_to_v45.py.

### migrate_categories_v9.py

Purpose:

- Data-only equipment category/category expansion work.

Evidence:

- Current DB has expected category distribution.
- Effects overlap with migrate_equipment_excel.py and later reference updates.

Reason not auto-backfilled:

- Data-only migration.
- Cannot distinguish from Excel import or manual category corrections.

Required owner confirmation:

- Was migrate_categories_v9.py run before migrate_equipment_excel.py?

## Confirmation checklist for owner

Please answer these five items:

1. migrate.py:
   - YES, it was run
   - NO / NOT SURE, do not record it

2. migrate_equipment.py:
   - YES, it was run
   - NO / NOT SURE, do not record it

3. migrate_worktypes.py:
   - YES, it was run
   - NO / NOT SURE, do not record it

4. migrate_categories_v9.py:
   - YES, it was run before migrate_equipment_excel.py
   - NO / NOT SURE, do not record it

5. migrate_v42.py:
   - SKIP, because migrate_to_v45 is already recorded
   - RECORD, because it was definitely run separately before migrate_to_v45.py

## Recommended safe answer if memory is uncertain

If there is no clear memory or evidence, choose:

1. migrate.py: NO / NOT SURE
2. migrate_equipment.py: NO / NOT SURE
3. migrate_worktypes.py: NO / NOT SURE
4. migrate_categories_v9.py: NO / NOT SURE
5. migrate_v42.py: SKIP

Reason:

- It is safer to leave uncertain historical data-only scripts unrecorded than to create false registry history.
- Current application is already stable.
- All critical schema migrations are already recorded.

## Next step

After owner confirmation:

- If all answers are NO / NOT SURE and migrate_v42.py = SKIP:
  - TASK-OPS-002C can be closed as "reviewed, no additional backfill".
- If any answer is YES:
  - Create a small idempotent migration registry script to record only confirmed scripts.
  - Run on staging first.
  - Back up production DB.
  - Run on production.
  - Document result.
