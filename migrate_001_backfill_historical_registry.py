# -*- coding: utf-8 -*-
"""
migrate_001_backfill_historical_registry.py -- TASK-OPS-002B

Backfills schema_migrations registry rows for 8 confirmed-applied historical
migration scripts that predate the registry introduced by TASK-OPS-001.

PURPOSE
  This script ONLY inserts registry metadata rows.
  It does NOT re-apply any migration logic.
  All 8 listed migrations have already been applied to the production database;
  their effects (tables, columns, indexes, seed data) are confirmed present by
  the evidence analysis in docs/MIGRATION_BACKFILL_ANALYSIS.md (TASK-OPS-002A).

CONFIRMED-APPLIED migrations recorded by this script:
  1. migrate_to_v3
  2. migrate_add_wialon
  3. migrate_to_v45
  4. migrate_v46
  5. migrate_tasks_abc3
  6. migrate_fuel_v2
  7. migrate_equipment_excel
  8. migrate_module_permissions

EXPLICITLY EXCLUDED from this script (do not add without operator confirmation):
  - migrate.py             -- LIKELY_APPLIED (data only); operator confirmation pending.
  - migrate_equipment.py   -- LIKELY_APPLIED (data only); operator confirmation pending.
  - migrate_worktypes.py   -- LIKELY_APPLIED (data only); operator confirmation pending.
  - migrate_v42.py         -- LIKELY_APPLIED but superseded by migrate_to_v45; operator decides.
  - migrate_categories_v9.py -- LIKELY_APPLIED (data only); operator confirmation pending.
  - migrate_v47.py         -- NOT_APPLIED; must NEVER be backfilled.

SAFETY
  - Idempotent: uses record_migration() which issues INSERT OR IGNORE.
  - Does not touch any business table.
  - Does not re-run any migration logic or ALTER schema.
  - Will not create a new empty database (migration_utils guards this).
  - Safe to run multiple times; second run prints "Already applied" and exits.

RUN (service must be STOPPED first to avoid SQLite write conflicts):
  cd C:\\transport-report
  .\\nssm.exe stop TransportReport
  copy instance\\transport.db instance\\transport.db.backup_before_ops002b
  "C:\\Program Files\\Python314\\python.exe" migrate_001_backfill_historical_registry.py
  .\\nssm.exe start TransportReport

  If nssm.exe is not in the current directory:
    net stop TransportReport
    net start TransportReport

VERIFY after running:
  "C:\\Program Files\\Python314\\python.exe" -c "import sqlite3; c=sqlite3.connect('instance/transport.db'); [print(r) for r in c.execute('SELECT id, name, applied_at FROM schema_migrations ORDER BY id')]; c.close()"

ROLLBACK:
  To remove only the backfill rows (no business data is touched):
  .\\nssm.exe stop TransportReport
  "C:\\Program Files\\Python314\\python.exe" -c "
  import sqlite3
  names = [
      'migrate_to_v3', 'migrate_add_wialon', 'migrate_to_v45',
      'migrate_v46', 'migrate_tasks_abc3', 'migrate_fuel_v2',
      'migrate_equipment_excel', 'migrate_module_permissions',
      'migrate_001_backfill_historical_registry',
  ]
  c = sqlite3.connect('instance/transport.db')
  c.executemany('DELETE FROM schema_migrations WHERE name = ?', [(n,) for n in names])
  c.commit()
  c.close()
  print('Rollback done.')
  "
  .\\nssm.exe start TransportReport

  Safest rollback: restore the backup taken before running this script:
    .\\nssm.exe stop TransportReport
    copy /Y instance\\transport.db.backup_before_ops002b instance\\transport.db
    .\\nssm.exe start TransportReport
"""

import os

from migration_utils import (
    ensure_schema_migrations_table,
    is_migration_applied,
    record_migration,
    migration_checksum,
)

THIS_MIGRATION = 'migrate_001_backfill_historical_registry'
DESCRIPTION    = 'Backfill registry rows for 8 confirmed-applied historical migrations (TASK-OPS-002B).'

BASE_DIR = os.path.dirname(os.path.abspath(__file__))

# [REASON]: Order reflects logical application sequence (schema dependencies first,
# data/reference migrations last). All 8 are CONFIRMED_APPLIED per TASK-OPS-002A.
CONFIRMED_MIGRATIONS = [
    (
        'migrate_to_v3',
        'migrate_to_v3.py',
        'Created deficiencies table and ix_deficiencies_date index; reclassified '
        'yuk_transport equipment from yukori category. '
        'BACKFILL: confirmed by deficiencies table (29 rows) and ix_deficiencies_date '
        'index present in production DB (TASK-OPS-002A).',
    ),
    (
        'migrate_add_wialon',
        'migrate_add_wialon.py',
        'Created vialon_mappings, vialon_imports, engine_hours_records tables and '
        'ix_engine_hours_date index. '
        'BACKFILL: confirmed by 379/169/9870 rows and all indexes present in '
        'production DB (TASK-OPS-002A).',
    ),
    (
        'migrate_to_v45',
        'migrate_to_v45.py',
        'Combined v4.2+v4.5: added unknown_vehicles_json to vialon_imports; created '
        'fuel_stations, fuel_tanks, fuel_snapshots, fuel_transactions, fuel_sync_logs '
        'with ix_fuel_snap_date, ix_fuel_tx_date, ix_fuel_sync_date indexes. '
        'BACKFILL: confirmed by all fuel v1 tables and indexes present (TASK-OPS-002A).',
    ),
    (
        'migrate_v46',
        'migrate_v46.py',
        'Created fuel_balances and fuel_receipts tables (fuel v1, linked to '
        'fuel_stations) with ix_fuel_bal_date and ix_fuel_rec_date indexes. '
        'BACKFILL: confirmed by both tables and both indexes in production DB '
        '(TASK-OPS-002A).',
    ),
    (
        'migrate_tasks_abc3',
        'migrate_tasks_abc3.py',
        'Added users.language column; created app_modules, user_module_permissions, '
        'spare_parts, spare_part_requests, spare_part_request_items tables. '
        'BACKFILL: confirmed by exact schema match on app_modules (id, code, name_uz, '
        'name_ru, is_active) and user_module_permissions (id, user_id, module_code, '
        'has_access) in production DB (TASK-OPS-002A).',
    ),
    (
        'migrate_fuel_v2',
        'migrate_fuel_v2.py',
        'Created fuel v2 tables (fuel_warehouses, fuel_stations2, '
        'fuel_initial_balances, fuel_receipts2, fuel_transactions2, fuel_sync_logs2) '
        'and seeded 10 warehouses and 21 stations from SEED_DATA. '
        'BACKFILL: confirmed by exact count match (10 warehouses, 21 stations) '
        'with SEED_DATA in production DB (TASK-OPS-002A).',
    ),
    (
        'migrate_equipment_excel',
        'migrate_equipment_excel.py',
        'Renamed 6 organizations; added 4 new organizations; updated/added equipment '
        'from Excel consolidation file to reach 336 records across 9 categories. '
        'BACKFILL: confirmed by 336 equipment, 17 organizations, 4 new org IDs 12-15, '
        'and all 9 categories populated in production DB (TASK-OPS-002A).',
    ),
    (
        'migrate_module_permissions',
        'migrate_module_permissions.py',
        'Granted all active modules to all non-admin users via INSERT OR IGNORE. '
        'BACKFILL: confirmed by explicit documentation in AGENT_STATE.md (TASK-SEC-001 '
        'section) and DECISIONS.md ADR-008, plus user_module_permissions rows present '
        '(TASK-OPS-002A).',
    ),
]


def run():
    ensure_schema_migrations_table()

    # [REASON]: Guard the entire backfill as a single atomic unit so it is safe
    # to run multiple times without double-inserting any row.
    if is_migration_applied(THIS_MIGRATION):
        print('Backfill already applied (migrate_001_backfill_historical_registry).')
        print('Nothing to do.')
        return

    print('Starting historical registry backfill -- TASK-OPS-002B')
    print('Inserting registry metadata only. No migration logic is re-run.')
    print()

    inserted = 0
    skipped = 0

    for name, filename, description in CONFIRMED_MIGRATIONS:
        filepath = os.path.join(BASE_DIR, filename)
        if os.path.exists(filepath):
            checksum = migration_checksum(filepath)
        else:
            # [REASON]: File may have been removed after it was applied (e.g., cleanup).
            # Recording without a checksum is still useful for auditing purposes.
            checksum = None
            print('  WARNING  : {} not found on disk; recording without checksum.'.format(filename))

        newly_inserted = record_migration(name, description=description, checksum=checksum)

        if newly_inserted:
            print('  INSERTED : {}'.format(name))
            inserted += 1
        else:
            # record_migration uses INSERT OR IGNORE, so this branch triggers if
            # a row for `name` was already present before this script ran.
            print('  SKIPPED  : {} (already in registry)'.format(name))
            skipped += 1

    print()
    print('Backfill summary  : inserted={} skipped={}'.format(inserted, skipped))

    # Record this backfill script itself as the final step.
    my_checksum = migration_checksum(os.path.abspath(__file__))
    record_migration(THIS_MIGRATION, description=DESCRIPTION, checksum=my_checksum)
    print('Recorded self     : {}'.format(THIS_MIGRATION))
    print()
    print('Backfill complete.')
    print()
    print('Verify with:')
    print(r'  "C:\Program Files\Python314\python.exe" -c "import sqlite3; '
          r"c=sqlite3.connect('instance/transport.db'); "
          r"[print(r) for r in c.execute('SELECT id, name, applied_at FROM schema_migrations ORDER BY id')]; "
          r'c.close()"')
    print()
    print('Next step: TASK-OPS-002C -- operator confirms the 5 pending scripts.')
    print('  See docs/MIGRATION_BACKFILL_ANALYSIS.md for the confirmation checklist.')


if __name__ == '__main__':
    run()
