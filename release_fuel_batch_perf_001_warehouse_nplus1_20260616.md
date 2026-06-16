# fuel-batch-perf-001 - warehouse nplus1 optimization

date: 2026-06-16
status: completed
scope: docs-only closure for already deployed code
production: http://10.103.25.14:5050

## summary

fuel-batch-perf-001 optimized fuel warehouse and initial balance pages by replacing per-row lazy relationship and count access with bulk preload and grouped count maps.

## code commit

`c4fd7d16b981bc1406aa65a6a9d48d23027bb6c0`

commit message note:

- actual commit message: `ptimize fuel warehouse loading`
- this is a copy typo only.
- the commit is valid.
- do not amend or rewrite history.

## changed code files in code commit

- `fuel_routes.py`
- `templates/fuel/warehouses.html`

## performance results

### `/fuel/warehouses`

before:

- 73 select
- repeated sql: 6

after:

- 6 select
- repeated sql: 0

### `/fuel/initial-balance`

before:

- 11 select
- repeated sql: 1

after:

- 2 select
- repeated sql: 0

## validation

- staging validation passed.
- production validation passed.
- production post-restart smoke passed.
- no db writes during get validation.
- no post during validation.

## rollout notes

- production was updated by fast-forward pull.
- only `transportreport` was restarted.
- `transportbot` was not restarted.
- `transportbot003` was not restarted.
- staging and production were both verified on code commit `c4fd7d16b981bc1406aa65a6a9d48d23027bb6c0`.

## production backup

`d:\transport-report-backups\production\source\fuel_batch_perf_001c_639172312812084107`

## next candidates

1. `/fuel/transactions`
   - production validation showed 12 select and station lazy-load repeated 9.
   - likely data-dependent n+1 on station relationship.
2. `/fuel/report`
   - 19 select and repeated warehouse query count 2.
3. `/fuel/warnings`
   - 18 select, repeated 0.
4. ui/ux redesign after performance and security technical debt.
