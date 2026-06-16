<!-- fuel-batch-perf-001d -->

### fuel-batch-perf-001 - fuel warehouse and initial balance performance

priority: p1
status: **completed 2026-06-16**

summary:

- optimized `/fuel/warehouses`.
- optimized `/fuel/initial-balance`.
- code commit: `c4fd7d16b981bc1406aa65a6a9d48d23027bb6c0`.
- `/fuel/warehouses`: 73 select -> 6 select.
- `/fuel/warehouses`: repeated sql 6 -> 0.
- `/fuel/initial-balance`: 11 select -> 2 select.
- `/fuel/initial-balance`: repeated sql 1 -> 0.
- staging validation passed.
- production validation passed.
- production post-restart smoke passed.
- only `transportreport` was restarted during code rollout.
- telegram bot services were not restarted.
- no db writes during get validation.
- no post during validation.
- production backup before rollout: `d:\transport-report-backups\production\source\fuel_batch_perf_001c_639172312812084107`.

notes:

- commit message in code commit contains a copy typo: `ptimize fuel warehouse loading`.
- the commit is valid and must not be amended.
- this entry is docs-only closure for fuel-batch-perf-001d.
