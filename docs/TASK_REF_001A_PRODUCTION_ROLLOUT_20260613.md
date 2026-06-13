# TASK-REF-001A Production Rollout

Date: 2026-06-13

## Summary

TASK-REF-001A was deployed to production successfully.

The release improves the equipment reference page `/ref/equipment`.

## Production commit

- `a7865f1` - Improve equipment reference filters and diagnostics

## Implemented

Added to `/ref/equipment`:

- search by equipment name, plate number, equipment type, organization name, organization short name
- status filter: all, active, inactive
- statistics cards:
  - total equipment in accessible organizations
  - active / inactive equipment
  - filtered result count
  - empty default unit count
- diagnostics block:
  - zero default price count
  - normalized duplicate plate groups
  - first duplicate plate examples
- visual marker for inactive equipment
- linked-record count marker near delete/disable actions
- Excel export now respects search and status filters

## Safety scope

No database schema changes.

No data migrations.

No equipment merge.

No automatic duplicate cleanup.

No changes to `equipment_id` relationships.

No changes to daily report, Wialon import, fuel, or spare-parts business logic.

## Production backups

Created before deployment:

- `D:\transport-report-backups\production\source\app_before_task_ref_001a_20260613_165401.py`
- `D:\transport-report-backups\production\source\ref_equipment_before_task_ref_001a_20260613_165401.html`
- `D:\transport-report-backups\production\daily\transport_task_ref_001a_before_20260613_165401.db`

Backup integrity:

- `PRAGMA integrity_check`: `ok`

## Production validation

Compilation:

- `py_compile app.py models.py config.py run_server.py bot003_outbox_worker.py`: passed

Application checks:

- `APP_IMPORT_OK`
- `TEMPLATE_REF_EQUIPMENT_LOAD_OK`
- `TASK_REF_001A_PRODUCTION_VALIDATION_OK`

Authenticated route checks:

- `/ref/equipment` -> 200
- `/ref/equipment?status=active` -> 200
- `/ref/equipment?status=inactive` -> 200
- `/ref/equipment?q=MTZ` -> 200
- `/ref/equipment?org_id=1&status=all&q=80` -> 200
- `/ref/equipment/export?status=active&q=MTZ` -> 200
- `/ref/organizations` -> 200
- `/entry` -> 200
- `/report` -> 200
- `/` -> 200

Anonymous route checks:

- `/` -> 302 to `/login?next=%2F`
- `/ref/equipment` -> 302 to `/login?next=%2Fref%2Fequipment`
- `/report` -> 302 to `/login?next=%2Freport`
- `/entry` -> 302 to `/login?next=%2Fentry`
- `/fuel/` -> 302 to `/login?next=%2Ffuel%2F`
- `/spare-parts/` -> 302 to `/login?next=%2Fspare-parts%2F`
- `/wialon` -> 302 to `/login?next=%2Fwialon`

HTTP checks after service restart:

- `/login`: 200
- `/`: 302 for unauthenticated user, expected login redirect

Services:

- `TransportReport`: RUNNING
- `TransportBot`: RUNNING
- `TransportBot003`: RUNNING

BOT003 dry-run:

- processed: 0
- sent: 0
- failed: 0
- skipped: 0
- error: null
- dry_run: true

## Final production state

- production HEAD: `a7865f1`
- origin/main: `a7865f1`
- working tree: clean

## Browser verification required

Open production:

- `http://10.103.25.14:5050/ref/equipment`

Check visually:

- statistics cards are visible
- status filter is visible
- search field is visible
- Excel button remains visible
- equipment table loads
- inactive equipment, if present in filter, is visually marked
- existing edit/delete/disable buttons remain available according to permissions
