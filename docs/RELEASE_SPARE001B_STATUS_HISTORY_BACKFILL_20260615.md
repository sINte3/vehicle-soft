# RELEASE SPARE001B  Spare parts status history audit and backfill

Date: 2026-06-15

## Status

Completed on staging and production.

## Base commit before docs

`8611424c51f30960e2daad2d282553f53b3a4c27`  `Document SPARE001A spare parts UX rollout`

## Scope

SPARE001B was a workflow/data integrity audit and data backfill for the spare parts module.

No code files were changed.

No schema changes were made.

No migrations were added.

No services were restarted.

## Read-only audit findings

The spare parts module already had status history support in code:

- `_add_status_history(...)` exists in `spare_parts.py`
- `save_request()` writes history when a request is created directly as `submitted`
- `submit_request()` writes `draft -> submitted`
- `approve_request()` writes `submitted -> approved`
- `reject_request()` writes `submitted -> rejected`

However, existing historical rows had no status history records.

### Staging before backfill

- `spare_part_requests`: 8
- `spare_part_status_history`: 0
- Requests with submitted/final status and zero history: 8

Status distribution:

- `submitted`: 7
- `approved`: 1
- `rejected`: 0

BOT003 outbox existed and had 2 sent rows for request IDs 7 and 8.

### Production before backfill

- `spare_part_requests`: 3
- `spare_part_status_history`: 0
- Requests with submitted/final status and zero history: 3

Status distribution after analysis:

- request 1: `submitted`
- request 2: `approved`
- request 3: `submitted`

## Staging backfill

Backup created:

`D:\transport-report-backups\staging\daily\transport_spare001b_status_history_backfill_20260615_133549.db`

Inserted status history rows:

- request 1: `NULL -> submitted`
- request 2: `NULL -> submitted`
- request 2: `submitted -> approved`
- request 3: `NULL -> submitted`
- request 4: `NULL -> submitted`
- request 5: `NULL -> submitted`
- request 6: `NULL -> submitted`
- request 7: `NULL -> submitted`
- request 8: `NULL -> submitted`

Staging result:

- inserted rows: 9
- `spare_part_status_history`: 9
- `AFTER_GAP_COUNT`: 0
- app import passed
- route checks returned expected login redirects
- Git remained clean
- services remained RUNNING
- no service restart was performed

## Production backfill

Backup created:

`D:\transport-report-backups\production\daily\transport_spare001b_status_history_backfill_20260615_133738.db`

Inserted status history rows:

- request 1: `NULL -> submitted`
- request 2: `NULL -> submitted`
- request 2: `submitted -> approved`
- request 3: `NULL -> submitted`

Production result:

- inserted rows: 4
- `spare_part_status_history`: 4
- validation errors: 0
- app import passed
- route checks returned expected login redirects
- Git remained clean
- `TransportReport`, `TransportBot`, `TransportBot003` remained RUNNING
- no service restart was performed

## Backfill logic

The operation was idempotent:

- It checked existing history per request.
- It inserted `submitted` history only when missing.
- It inserted final `approved/rejected` history only when missing.
- It did not create duplicate rows.
- It did not touch request status, items, audit logs, BOT003 outbox, users or source code.

## Final result

SPARE001B is complete.

Historical spare part requests now have status history records in staging and production.

Future requests should continue using the existing code path in `spare_parts.py`, which already records status history for new transitions.
