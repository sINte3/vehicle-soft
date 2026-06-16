# Release: PERF-FUEL-REPORT-WAREHOUSE-QUERY-001  Fuel Report Warehouse Query Cleanup

Date: 2026-06-16  
Commit: `6e6237b optimize fuel report warehouse loading`  
Type: performance optimization  
Risk: low  
Scope: `/fuel/report`

## Summary

Optimized `/fuel/report` by removing a duplicate ordered warehouse query.

## Root Cause

`_collect_fuel_report_data()` already loaded the ordered list of fuel warehouses. After that, `fuel_report()` loaded the same warehouse list again before rendering `templates/fuel/report.html`.

This was not a lazy-load N+1 issue. It was a constant duplicate query.

## Code Change

Changed `fuel_routes.py`:

- added `warehouses` to the data returned by `_collect_fuel_report_data()`;
- reused the already-loaded warehouse list in `fuel_report()`;
- used `data_for_template = dict(data)` and removed `warehouses` from that dict before passing `**data_for_template` into `render_template`.

Marker:

- `perf-fuel-report-warehouse-query-001_marker`

## Validation Results

Before:

- status: 200
- SQL total: 22
- repeated SQL kinds: 1
- warehouse ordered queries: 2

Staging after fix:

- status: 200
- SQL total: 21
- repeated SQL kinds: 0
- warehouse ordered queries: 1
- non-select statements: 0

Production after fix:

- status: 200
- SQL total: 21
- repeated SQL kinds: 0
- warehouse ordered queries: 1
- non-select statements: 0

Smoke checks:

- Flask test client unauthenticated routes returned expected 302 to login.
- HTTP checks for `/` and `/fuel/report` returned expected 302 unauthenticated.

## Deployment

Production backup:

`d:\transport-report-backups\production\source\fuel_report_warehouse_query_001_before_20260616_192639_d7961d8.zip`

Production service action:

- restarted only `transportreport`;
- did not restart `transportbot`;
- did not restart `transportbot003`.

## Rollback

Rollback source archive is available at the production backup path above.

If rollback is required:

1. restore source from backup;
2. restart only `transportreport`;
3. verify `/`, `/fuel/report`, `/fuel/transactions`, `/fuel/receipts`.

## Final Status

DONE.
