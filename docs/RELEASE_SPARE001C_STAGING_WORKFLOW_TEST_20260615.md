# RELEASE SPARE001C  Controlled staging spare parts workflow test

Date: 2026-06-15

## Status

Completed on staging.

## Base commit before docs

`7ba531bbcdaf013cdad647e4962e9c71f5795825`  `Document SPARE001B status history backfill`

## Scope

SPARE001C was a controlled staging-only workflow test of the spare parts module.

No source code was changed.

No production DB changes were made.

No production workflow test was executed.

No service restart was performed.

## Staging backup

Before the controlled workflow test, staging DB backup was created:

`D:\transport-report-backups\staging\daily\transport_spare001c_workflow_test_20260615_134745.db`

## Test run

Run tag:

`SPARE001C_TEST_20260615_134745`

Test user:

- `user_id`: 1
- `username`: `admin`
- `full_name`: `Администратор`
- `role`: `admin`

Test organization/equipment:

- organization: `Когон ПТЗ`
- equipment: `МТЗ-80Х`
- plate: `80 261 EA`

## Workflow A

Request:

- request ID: 9
- note: `SPARE001C_TEST_20260615_134745_A_DRAFT_SUBMIT_APPROVE`
- item: `SPARE001C_TEST_20260615_134745_A_ITEM`
- part number: `SPARE001C-A`
- quantity: 2
- unit: `dona`

Workflow:

- created as `draft`
- submitted: `draft -> submitted`
- approved: `submitted -> approved`

Final status:

- `approved`

Status history rows:

- request 9: `draft -> submitted`
- request 9: `submitted -> approved`

Audit log rows:

- request created
- item created
- request submitted
- request approved

BOT003 outbox events:

- `spare_request_submitted`
- `spare_request_approved`

Delivery result:

- both events were sent by `TransportBot003Staging`

## Workflow B

Request:

- request ID: 10
- note: `SPARE001C_TEST_20260615_134745_B_SUBMIT_REJECT`
- item: `SPARE001C_TEST_20260615_134745_B_ITEM`
- part number: `SPARE001C-B`
- quantity: 3
- unit: `dona`

Workflow:

- created directly as `submitted`
- rejected: `submitted -> rejected`

Final status:

- `rejected`

Status history rows:

- request 10: `NULL -> submitted`
- request 10: `submitted -> rejected`

Audit log rows:

- request created
- item created
- request rejected

BOT003 outbox events:

- `spare_request_submitted`
- `spare_request_rejected`

Delivery result:

- both events were sent by `TransportBot003Staging`

## Validation result

After workflow test:

- requests increased from 8 to 10
- items increased from 8 to 10
- status history increased from 9 to 13
- audit logs increased from 738 to 745
- BOT003 outbox increased from 2 to 6

Validation:

- expected 2 controlled test requests: OK
- final statuses `approved/rejected`: OK
- expected 4 status history rows: OK
- `draft -> submitted`: OK
- `submitted -> approved`: OK
- `NULL -> submitted`: OK
- `submitted -> rejected`: OK
- expected BOT003 outbox events: OK
- detail page render with SPARE001A marker: OK
- Git working tree remained clean
- staging services remained RUNNING
- no service restart was performed

## BOT003 delivery confirmation

Read-only delivery check confirmed:

- `OUTBOX_TOTAL_FOR_RUN`: 4
- `OUTBOX_SENT_FOR_RUN`: 4
- `OUTBOX_PENDING_FOR_RUN`: 0
- `OUTBOX_FAILED_FOR_RUN`: 0

Sent rows:

- outbox ID 5: `spare_request_submitted`, request 9, `sent`
- outbox ID 6: `spare_request_approved`, request 9, `sent`
- outbox ID 7: `spare_request_submitted`, request 10, `sent`
- outbox ID 8: `spare_request_rejected`, request 10, `sent`

## Final result

SPARE001C is complete.

The spare parts module workflow is confirmed on staging:

- creation
- submit
- approve
- reject
- status history
- audit logs
- BOT003 notification outbox
- BOT003 staging delivery

Production was not touched during SPARE001C.
