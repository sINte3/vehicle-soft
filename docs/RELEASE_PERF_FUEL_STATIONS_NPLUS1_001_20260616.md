# RELEASE PERF-FUEL-STATIONS-NPLUS1-001 - Fuel stations transaction counts optimization

Date: 2026-06-16

## Status

Completed and deployed to production.

## Code commit

a7f295a9ece74f1821a52b755ae5daa024ecfd65

## Purpose

PERF-FUEL-STATIONS-NPLUS1-001 removed the N+1 transaction count pattern from the fuel stations reference page.

Target route:

- GET /fuel/stations

## Background

PERF-FUEL-DASH-REPEAT-001A originally checked `/fuel/` for a repeated fuel transaction query.

Result:

- `/fuel/` had 11 SELECT.
- repeated SQL count was 0.
- no source patch was justified for `/fuel/`.

The same diagnostic showed a better optimization candidate:

- `/fuel/stations` had repeated per-station transaction count queries.

Baseline `/fuel/stations`:

- response UTF-8 bytes: 60,440
- SQL count: 44 SELECT
- SQL unique count: 4
- repeated SQL count: 2
- repeated transaction count queries: 21 + 21
- DML count: 0
- no traceback
- station rows: 21
- forms: 23
- inputs: 28
- CSRF inputs: 23

Root cause:

- the route calculated transaction count per station inside a Python loop;
- the template also rendered `st.transactions.count()`, causing repeated count queries.

## Implemented change

Changed files:

- fuel_routes.py
- templates/fuel/stations.html

Main route change:

- collected all station IDs once;
- loaded transaction counts with one grouped query:
  - `FuelTransaction2.station_id`
  - `func.count(FuelTransaction2.id)`
  - `WHERE station_id IN (...)`
  - `GROUP BY station_id`
- stored counts in `station_tx_counts`;
- reused the count inside `station_delete_info`.

Main template change:

- replaced `st.transactions.count()` with the preloaded count:
  - `(station_delete_info.get(st.id) or {}).get('transactions_count', 0)`

Markers added:

- `PERF-FUEL-STATIONS-NPLUS1-001B_MARKER: bulk transaction counts for fuel stations.`
- `PERF-FUEL-STATIONS-NPLUS1-001B_MARKER: use preloaded transaction counts.`

No DB schema changes were made.

No migrations were run.

No POST requests were executed during validation.

## Staging validation

Staging validation passed before commit.

Source validation:

- py_compile passed;
- git diff --check passed;
- app import OK;
- URL rules count: 86;
- source checks passed.

Staging `/fuel/stations` after patch:

- response UTF-8 bytes: 60,449
- SQL count: 3 SELECT
- SQL unique count: 3
- repeated SQL count: 0
- DML count: 0
- no traceback
- station rows: 21
- forms: 23
- inputs: 28
- CSRF inputs: 23
- old `st.transactions.count()` mentions: 0

Regression sample:

- /fuel/: 11 SELECT, repeated SQL 0
- /fuel/warehouses: 73 SELECT, repeated SQL 6
- /fuel/transactions: 3 SELECT, repeated SQL 0 on staging
- /fuel/report: 19 SELECT, repeated SQL 1
- /ref/work_types: 2 SELECT, repeated SQL 0
- /wialon/mapping: 3 SELECT, repeated SQL 0
- /spare-parts/: 4 SELECT, repeated SQL 0

Staging post-restart smoke passed.

## Production rollout

PERF-FUEL-STATIONS-NPLUS1-001C deployed the code commit to production.

Production rollout details:

- production source backup created before pull:
  - D:\transport-report-backups\production\source\PERF_FUEL_STATIONS_NPLUS1_001C_20260616_165855
- production pull scope verified:
  - fuel_routes.py
  - templates/fuel/stations.html
- production pull was fast-forward only;
- production py_compile passed;
- production source validation passed;
- only TransportReport was restarted;
- Telegram bot services were not restarted.

Production `/fuel/stations` after patch:

- response UTF-8 bytes: 60,449
- SQL count: 3 SELECT
- SQL unique count: 3
- repeated SQL count: 0
- DML count: 0
- no traceback
- station rows: 21
- forms: 23
- inputs: 28
- CSRF inputs: 23
- old `st.transactions.count()` mentions: 0

Production post-restart smoke passed.

## Production service state after rollout

- TransportReport: RUNNING
- TransportBot: RUNNING
- TransportBot003: RUNNING

## Result

Fuel stations page SQL was reduced without changing business behavior:

- `/fuel/stations`: 44 SELECT reduced to 3 SELECT;
- repeated SQL: 2 reduced to 0;
- transaction counts are still displayed;
- delete/deactivate protection still uses transaction count;
- route POST contracts unchanged;
- no DB/schema changes;
- no migrations;
- no Telegram bot restart.

## Remaining observations

Current heavier fuel areas after this optimization:

- `/fuel/warehouses`: 73 SELECT, repeated SQL 6.
- `/fuel/report`: 19 SELECT, repeated SQL 1.
- `/fuel/transactions`: production showed 11 SELECT, repeated SQL 1 due to repeated station lazy-loads in the current production data sample.
- `/fuel/`: 11 SELECT, repeated SQL 0.

Future candidates:

- PERF-FUEL-WAREHOUSES-NPLUS1-001: optimize `/fuel/warehouses` repeated station/receipt/initial-balance/transaction counts.
- PERF-FUEL-TRANSACTIONS-NPLUS1-001: optimize `/fuel/transactions` station lazy-loads if repeat persists in staging/production comparison.
- PERF-FUEL-REPORT-REPEAT-001: review duplicate warehouse query in `/fuel/report`.
