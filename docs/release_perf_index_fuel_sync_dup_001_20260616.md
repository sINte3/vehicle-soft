# Release: PERF-INDEX-FUEL-SYNC-DUP-001  Main Dashboard FuelSyncLog Duplicate Query Fix

Date: 2026-06-16  
Type: performance optimization  
Risk: low  
Scope: main dashboard `/`

## Summary

Optimized the main dashboard `/` by removing a duplicate latest Topaz sync query.

## Problem

The main dashboard builds a Fuel summary by calling `_collect_fuel_report_data(d_from, d_to)`.

That collector already queried `FuelSyncLog2` to determine latest synchronization and warning status.

After that, `app.py` executed another identical latest-sync query:

- `FuelSyncLog2.query.order_by(FuelSyncLog2.synced_at.desc()).first()`

This produced a repeated `fuel_sync_logs2` SQL query on `/`.

## Fix

Changed the data flow:

- `fuel_routes.py`
  - `_collect_fuel_report_data()` now returns `latest_sync`.
- `app.py`
  - `_build_dashboard_context()` reuses `fuel_report['latest_sync']`.
  - Direct fallback query is kept only if `_collect_fuel_report_data()` fails.

## Files Changed

- `app.py`
- `fuel_routes.py`

## Commit

- `f00b386 optimize index fuel sync loading`

## Verification

### Staging

Before fix:

- `/` repeated SQL kinds: 1
- `fuel_sync_logs2` query count: 3

After fix:

- status: 200
- SQL total: 30
- repeated SQL kinds: 0
- `fuel_sync_logs2` query count: 2
- non-select statements: 0

### Production

After deployment:

- status: 200
- SQL total: 31
- repeated SQL kinds: 0
- `fuel_sync_logs2` query count: 2
- non-select statements: 0

## Production Backup

Created tracked-source backup before production pull:

`d:\transport-report-backups\production\source\index_fuel_sync_dup_001_git_archive_before_20260616_200045_98ca314.zip`

## Deployment

- Staging deployed and restarted:
  - `transportreportstaging`
- Production deployed and restarted:
  - `transportreport`

Bot services were not restarted.

## Final Status

DONE  deployed to staging and production.
