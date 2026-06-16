<!-- fuel-batch-perf-001d -->

## 2026-06-16 - fuel-batch-perf-001d docs closure

current stable code commit:

- `c4fd7d16b981bc1406aa65a6a9d48d23027bb6c0`

completed task:

- fuel-batch-perf-001 is closed.
- `/fuel/warehouses` optimized from 73 select and repeated sql 6 to 6 select and repeated sql 0.
- `/fuel/initial-balance` optimized from 11 select and repeated sql 1 to 2 select and repeated sql 0.
- staging validation passed.
- production validation passed.
- production post-restart smoke passed.
- only `transportreport` was restarted for the code rollout.
- `transportbot` and `transportbot003` were not restarted.
- validation used get requests only.
- no post requests were used during validation.
- no db writes were made during get validation.

next candidates after closure:

1. `/fuel/transactions`
   - staging showed 3 select, repeated 0.
   - production showed 12 select with station lazy-load repeated 9.
   - likely data-dependent n+1 by station relationship.
2. `/fuel/report`
   - 19 select with repeated warehouse query count 2.
   - lower priority.
3. `/fuel/warnings`
   - 18 select, repeated 0.
   - evaluate query volume separately.
4. ui/ux redesign
   - planned after technical debt is reduced.
