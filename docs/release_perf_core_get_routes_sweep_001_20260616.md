# Release: PERF-CORE-GET-ROUTES-SWEEP-001  Core GET Routes Performance Sweep

Date: 2026-06-16  
Type: verification / performance closure  
Risk: none  
Scope: core transport pages

## Summary

Completed SQL/N+1 diagnostics for the core GET routes:

- `/`
- `/entry`
- `/deficiencies`
- `/report`

## Results

### `/entry`

Staging and production:

- status: 200
- SQL total: 5
- repeated SQL kinds: 0
- non-select statements: 0

### `/deficiencies`

Staging and production:

- status: 200
- SQL total: 5
- repeated SQL kinds: 0
- non-select statements: 0

### `/report`

Staging and production:

- status: 200
- SQL total: 5
- repeated SQL kinds: 0
- non-select statements: 0

### `/`

The route initially had one repeated SQL kind caused by duplicate latest Topaz sync lookup.

The issue was fixed in:

- `f00b386 optimize index fuel sync loading`

Final verification:

Staging:

- status: 200
- SQL total: 30
- repeated SQL kinds: 0
- `fuel_sync_logs2` query count: 2
- non-select statements: 0

Production:

- status: 200
- SQL total: 31
- repeated SQL kinds: 0
- `fuel_sync_logs2` query count: 2
- non-select statements: 0

## Conclusion

Core GET route sweep is complete.

No remaining N+1 issue was found in the core transport pages.
