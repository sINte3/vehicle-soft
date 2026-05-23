# MIGRATIONS.md — Migration Discipline for Vehicle Soft

## Overview

This document describes how to apply database schema changes safely on the
Windows production server (`C:\transport-report\`).

The project uses SQLite.  SQLite has no built-in migration runner, so this
project uses a lightweight custom registry: a table called `schema_migrations`
that records which scripts have been applied.

The registry mechanism was introduced by TASK-OPS-001 (2026-05-22).

---

## Standard migration procedure (Windows CMD)

Follow this order exactly for every migration:

```cmd
cd C:\transport-report

:: 1. Stop the service to release SQLite write lock
.\nssm.exe stop TransportReport

:: 2. Back up the database
copy instance\transport.db instance\transport.db.backup_%DATE:~-4%%DATE:~3,2%%DATE:~0,2%

:: 3. Run ONE migration script at a time
"C:\Program Files\Python314\python.exe" migrate_XXX_description.py

:: 4. Check the output — confirm no ERROR lines
::    Check the registry:
"C:\Program Files\Python314\python.exe" -c "import sqlite3; c=sqlite3.connect('instance/transport.db'); [print(r) for r in c.execute('SELECT id, name, applied_at FROM schema_migrations ORDER BY id')]; c.close()"

:: 5. Restart the service
.\nssm.exe start TransportReport
```

If `nssm.exe` is not in the current directory, replace steps 1 and 5 with:

```cmd
net stop TransportReport
net start TransportReport
```

**Never run two migration scripts in the same step.**
**Never skip the backup step.**

---

## Writing a new migration script

1. Name the file `migrate_NNN_short_description.py` where `NNN` is the next
   number in sequence (zero-padded to three digits).

2. Import the helpers at the top:

```python
from migration_utils import (
    ensure_schema_migrations_table,
    is_migration_applied,
    record_migration,
    migration_checksum,   # optional — for recording file digest
)

THIS_MIGRATION = 'migrate_NNN_short_description'
DESCRIPTION    = 'One sentence describing what this migration does.'
```

3. Guard every migration with the idempotency check:

```python
def run():
    ensure_schema_migrations_table()

    if is_migration_applied(THIS_MIGRATION):
        print('Already applied. Nothing to do.')
        return

    # ... do the work here ...

    record_migration(THIS_MIGRATION, description=DESCRIPTION)
    print('Done.')

if __name__ == '__main__':
    run()
```

4. Make the body idempotent too (use `IF NOT EXISTS`, `INSERT OR IGNORE`, etc.).

5. Include rollback instructions as a comment at the top of the file.

6. Run syntax check before handing off:

```cmd
"C:\Program Files\Python314\python.exe" -m py_compile migrate_NNN_short_description.py
```

---

## migration_utils.py reference

| Function | Purpose |
|---|---|
| `ensure_schema_migrations_table()` | Creates `schema_migrations` if absent. Safe to call every time. |
| `is_migration_applied(name)` | Returns `True` if `name` is already recorded. |
| `record_migration(name, description, checksum)` | Inserts a row. `INSERT OR IGNORE` — safe if already present. |
| `migration_checksum(path)` | SHA-256 hex digest of a file. Use to detect post-apply modifications. |

---

## Historical migration inventory

The following scripts existed before the registry was introduced.
**None of them are recorded in `schema_migrations` yet.**
Backfilling them is TASK-OPS-002 and requires human verification before marking
each one as applied.

| Script | Likely purpose | Notes |
|---|---|---|
| `migrate.py` | Initial schema bootstrap | First-ever migration |
| `migrate_equipment.py` | Equipment table changes | |
| `migrate_worktypes.py` | Work types seed / changes | |
| `migrate_to_v3.py` | v3 schema upgrade | |
| `migrate_add_wialon.py` | Wialon tables | |
| `migrate_v42.py` | v4.2 schema changes | |
| `migrate_to_v45.py` | v4.5 upgrade | |
| `migrate_v46.py` | v4.6 upgrade | |
| `migrate_v47.py` | v4.7 upgrade | |
| `migrate_fuel_v2.py` | Fuel v2 tables + seed data | Has idempotency for warehouses/stations |
| `migrate_tasks_abc3.py` | Tasks A/B/C P3 features | |
| `migrate_categories_v9.py` | 9-category equipment expansion | |
| `migrate_equipment_excel.py` | Equipment Excel import | |
| `migrate_module_permissions.py` | Module permission grants | Last run; confirmed applied on production |
| `migrate_000_migration_registry.py` | Bootstrap registry table | TASK-OPS-001; first script to use the registry |

**Do not mark old migrations as applied automatically.**
Backfilling must be done deliberately by a human who confirms each script
was actually run on the current production database.

---

## Rollback

For any migration that used the registry helpers:

1. Stop the service.
2. Restore the backup taken before the migration:

```cmd
.\nssm.exe stop TransportReport
copy /Y instance\transport.db.backup_YYYYMMDD instance\transport.db
.\nssm.exe start TransportReport
```

For structural-only migrations (e.g., adding a column with `ALTER TABLE`),
a manual `ALTER TABLE ... DROP COLUMN` or table rebuild may also work, but
restoring the backup is always the safest option.

---

## Checklist before every migration

- [ ] Service stopped.
- [ ] Database backup taken and named clearly.
- [ ] Script syntax-checked (`py_compile`).
- [ ] Script reviewed: idempotent, no business table drops, rollback comment present.
- [ ] Run one script at a time.
- [ ] Check output for errors.
- [ ] Check `schema_migrations` table after running.
- [ ] Start service only after sanity checks pass.
