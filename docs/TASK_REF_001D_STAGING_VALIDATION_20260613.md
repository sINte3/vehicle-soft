# TASK-REF-001D Staging Validation

Date: 2026-06-13

## Summary

TASK-REF-001D adds read-only XLSX diagnostic exports for reference cleanup planning.

## Scope

Implemented on staging.

No production rollout yet.

No database schema changes.

No data migrations.

No data modification.

## Added routes

- `/ref/work_types/export_diagnostics`
- `/ref/customers/export_diagnostics`

## Work types diagnostic export

Workbook sheets:

- `Summary`
- `Reference`
- `Duplicate names`
- `Missing from reference`
- `Quality issues`

Purpose:

- export all work types
- show usage by text from `daily_records.work_type`
- show duplicate names
- show missing work type values
- show empty default unit and zero default price

## Customers diagnostic export

Workbook sheets:

- `Summary`
- `Reference`
- `Missing from reference`
- `Similarity groups`
- `Pattern groups`

Purpose:

- export current customer reference table
- export all customer values from `daily_records.customer` missing from reference
- group similar customer strings
- flag common patterns: cluster, PTZ, service, department, numeric prefixes, very long or malformed rows

## UI

Added `Excel диагностика` buttons to:

- `/ref/work_types`
- `/ref/customers`

## Validation

Passed:

- `py_compile app.py models.py config.py run_server.py`
- `APP_IMPORT_OK`
- `/ref/work_types` returns `200`
- `/ref/customers` returns `200`
- `/ref/work_types/export_diagnostics` returns XLSX `200`
- `/ref/customers/export_diagnostics` returns XLSX `200`
- exported workbooks contain expected sheets
- existing pages still return `200`:
  - `/ref/equipment`
  - `/ref/organizations`
  - `/entry`
  - `/report`
  - `/`

## Safety note

The export routes are read-only. They do not modify reference tables or historical daily records.

## Status

TASK-REF-001D staging validation passed.
