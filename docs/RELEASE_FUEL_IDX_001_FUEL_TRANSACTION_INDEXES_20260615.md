# RELEASE FUEL-IDX-001  Fuel transaction date indexes

Date: 2026-06-15

## Status

Completed on staging and production.

## Code commit

`62001d48886f8a1342cc83a2ab958dc3d8a53ef2`  `Add fuel transaction date indexes`

## Source changes

Changed files:

- `models.py`
- `migrate_fuel_idx_001.py`

## Purpose

FUEL-IDX-001 implements the first high-priority recommendation from the external Fable audit EXTAUDIT002.

The issue confirmed during read-only audit:

- active table `fuel_transactions2` contains about 392k rows;
- legacy table `fuel_transactions` contains 0 rows;
- legacy index `ix_fuel_tx_date` exists on the empty legacy table;
- active table `fuel_transactions2` previously had only the unique autoindex on `(station_id, topaz_txn_id)`;
- date-range queries on `fuel_transactions2.txn_datetime` performed full table scans.

## Added indexes

The following indexes were added to the active table `fuel_transactions2`:

- `ix_fuel_transactions2_txn_datetime`
- `ix_fuel_transactions2_station_datetime`

Model declaration was also updated in `FuelTransaction2.__table_args__`.

## Migration

Migration script:

- `migrate_fuel_idx_001.py`

Migration name:

- `FUEL_IDX_001_FUEL_TRANSACTIONS2_INDEXES`

Migration behavior:

- idempotent;
- uses `CREATE INDEX IF NOT EXISTS`;
- records a row in `schema_migrations`;
- does not change business data;
- ignores `schema_migrations` and `sqlite_sequence` when checking business table counts.

## Staging execution

FUEL-IDX-001A read-only staging audit confirmed:

- `fuel_transactions2`: 392015 rows
- `fuel_transactions`: 0 rows
- before index:
  - `range_count_txn_datetime` used `SCAN fuel_transactions2`
  - `date_function_count_non_sargable` used `SCAN fuel_transactions2`

FUEL-IDX-001B staging migration completed successfully.

Staging validation after migration confirmed:

- app import OK;
- indexes present;
- migration row present;
- `fuel_transactions2` count unchanged: 392015;
- business table counts unchanged;
- `range_count_txn_datetime` uses:
  - `SEARCH fuel_transactions2 USING COVERING INDEX ix_fuel_transactions2_txn_datetime`
- `station_and_range_count` uses:
  - `SEARCH fuel_transactions2 USING COVERING INDEX ix_fuel_transactions2_station_datetime`
- `date(txn_datetime)` expression still scans, as expected until future FUEL-IDX-002.

Staging services were stopped for SQLite index migration and started again.

Final staging services:

- `TransportReportStaging`: RUNNING
- `TransportBotStaging`: RUNNING
- `TransportBot003Staging`: RUNNING

## Production rollout

Production rollout completed successfully.

Production source pull scope was verified before pull:

- `models.py`
- `migrate_fuel_idx_001.py`

Production backup before migration:

- source backup created under `D:\transport-report-backups\production\source\FUEL_IDX_001C_*`
- DB backup created under `D:\transport-report-backups\production\daily\FUEL_IDX_001C_*`

Production services were stopped before SQLite index migration and started again after migration.

Production migration output confirmed:

- both indexes were absent before migration;
- both indexes were created;
- migration row inserted;
- business count errors: none;
- no business data rows changed.

Production validation confirmed:

- app import OK;
- URL rules count: 86;
- expected indexes present;
- migration row present;
- `fuel_transactions2`: 392350 rows at validation time;
- `schema_migrations`: 14 rows;
- `range_count_txn_datetime` uses:
  - `SEARCH fuel_transactions2 USING COVERING INDEX ix_fuel_transactions2_txn_datetime`
- `station_and_range_count` uses:
  - `SEARCH fuel_transactions2 USING COVERING INDEX ix_fuel_transactions2_station_datetime`
- unauthenticated smoke checks passed:
  - `/login`
  - `/fuel/`
  - `/fuel/report`
- errors count: 0.

Final production services:

- `TransportReport`: RUNNING
- `TransportBot`: RUNNING
- `TransportBot003`: RUNNING

## Result

FUEL-IDX-001 is complete.

The main EXTAUDIT002 performance finding for indexed date-range access to `fuel_transactions2` is closed.

Remaining related future task:

- FUEL-IDX-002: replace `func.date(txn_datetime) == ...` style filters with sargable date ranges.
