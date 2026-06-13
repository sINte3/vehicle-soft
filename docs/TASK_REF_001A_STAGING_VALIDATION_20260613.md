# TASK-REF-001A Staging Validation

Date: 2026-06-13

## Summary

TASK-REF-001A improves the equipment reference page `/ref/equipment`.

This is a safe reference UI improvement:

- no database schema changes
- no data migrations
- no changes to `equipment_id` relationships
- no automatic duplicate merge
- no changes to daily report, Wialon import, fuel, or spare-parts business logic

## Implemented

Added to `/ref/equipment`:

- search by equipment name, plate number, equipment type, organization name, organization short name
- status filter:
  - all
  - active
  - inactive
- equipment statistics cards:
  - total in accessible organizations
  - active / inactive
  - filtered result count
  - empty default unit count
- diagnostics block:
  - zero default price count
  - normalized duplicate plate groups
  - first examples of duplicate plate groups
- visual marker for inactive equipment
- linked-record count marker near delete/disable actions
- Excel export now respects new search and status filters

## Safety decision

Equipment `name` is not unique by design.

Examples:

- `МТЗ-80Х`
- `МТЗ-80.1`
- `New Holland 7060`
- `John Deere 9970`

These are model names repeated across different plate numbers and organizations. Therefore TASK-REF-001A does not merge records and does not deduplicate by name.

## Validation

Validated on staging:

- `py_compile app.py`: passed
- `APP_IMPORT_OK`
- `TEMPLATE_REF_EQUIPMENT_LOAD_OK`

Authenticated route checks:

- `/ref/equipment` -> 200
- `/ref/equipment?status=active` -> 200
- `/ref/equipment?status=inactive` -> 200
- `/ref/equipment?q=MTZ` -> 200
- `/ref/equipment?org_id=1&status=all&q=80` -> 200
- `/ref/equipment/export?status=active&q=MTZ` -> 200
- `/ref/organizations` -> 200
- `/entry` -> 200

Expected files changed:

- `app.py`
- `templates/ref_equipment.html`
- `docs/TASK_REF_001A_STAGING_VALIDATION_20260613.md`

## Production status

Not yet deployed to production.
