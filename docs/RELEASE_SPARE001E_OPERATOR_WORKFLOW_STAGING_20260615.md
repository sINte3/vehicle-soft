# RELEASE SPARE001E  Controlled operator workflow test on staging

Date: 2026-06-15

## Status

Completed on staging.

## Base commit before docs

`35281968a2535d12090e1dec7e9f63436be16e14`  `Document SPARE001D spare parts access permissions`

## Scope

SPARE001E was a controlled staging-only workflow test using an active operator account, not admin.

No source code was changed.

Production DB was not touched.

No production workflow test was executed.

No service restart was performed.

## Initial failed test attempt

The first SPARE001E test attempt failed before creating a request because the test script called a nonexistent model method:

`operator.can_access_module("spare_parts")`

Actual application access control uses `@module_required('spare_parts')` and `user_module_permissions`, not a `User.can_access_module(...)` method.

Result of failed attempt:

- no test request was created
- no POST reached `/spare-parts/save`
- no source code changed
- production was not touched
- services remained RUNNING

Backup from failed attempt:

`D:\transport-report-backups\staging\daily\transport_spare001e_operator_workflow_test_20260615_142154.db`

## Fixed operator workflow test

The corrected test checked module permission through `user_module_permissions` and then exercised the real spare parts endpoints as an active operator.

Test operator:

- user ID: 4
- username: `muhiddin`
- full name: `Шукуров Мухиддин`
- role: `operator`
- `spare_parts_access`: 1
- `can_edit`: True
- organizations assigned: 17

Test organization/equipment:

- organization: `Когон ПТЗ`
- equipment: `МТЗ-80Х`
- plate: `80 261 EA`

## First fixed run

Backup:

`D:\transport-report-backups\staging\daily\transport_spare001e_operator_workflow_test_fixed_20260615_142410.db`

Created request:

- request ID: 11
- note: `SPARE001E_TEST_FIXED_20260615_142410_OPERATOR_DRAFT_SUBMIT`
- created by: `muhiddin`
- final status: `submitted`

Workflow:

- created as `draft`
- submitted by operator: `draft -> submitted`

Validation:

- created_by = operator user ID 4
- operator detail page rendered successfully
- operator approve attempt returned `403 Forbidden`
- operator reject attempt returned `403 Forbidden`
- operator catalog access returned `403 Forbidden`
- status history row created
- audit logs created
- BOT003 outbox row created and sent

BOT003:

- outbox ID: 9
- event: `spare_request_submitted`
- request ID: 11
- status: `sent`
- sent_at: `2026-06-15T09:24:17.710992+00:00`

## Second fixed run

The fixed command was accidentally run a second time. It was valid and created a second controlled staging request.

Backup:

`D:\transport-report-backups\staging\daily\transport_spare001e_operator_workflow_test_fixed_20260615_142426.db`

Created request:

- request ID: 12
- note: `SPARE001E_TEST_FIXED_20260615_142426_OPERATOR_DRAFT_SUBMIT`
- created by: `muhiddin`
- final status: `submitted`

Workflow:

- created as `draft`
- submitted by operator: `draft -> submitted`

Validation:

- created_by = operator user ID 4
- operator detail page rendered successfully
- operator approve attempt returned `403 Forbidden`
- operator reject attempt returned `403 Forbidden`
- operator catalog access returned `403 Forbidden`
- status history row created
- audit logs created
- BOT003 outbox row created

BOT003:

- outbox ID: 10
- event: `spare_request_submitted`
- request ID: 12
- status: `sent`
- sent_at: `2026-06-15T09:24:48.065072+00:00`

## Final read-only BOT003 delivery confirmation

Read-only delivery check confirmed:

- request 11: `spare_request_submitted` = `sent`
- request 12: `spare_request_submitted` = `sent`
- errors count: 0

No DB writes were performed during final delivery check.

No service restart was performed.

## Final result

SPARE001E is complete.

Confirmed on staging:

- active operator can create a draft spare part request
- active operator can submit own request
- created_by is correctly set to the operator
- status history records `draft -> submitted`
- audit logs use operator username snapshot
- BOT003 sends submitted notification to admin Telegram target
- operator cannot approve requests
- operator cannot reject requests
- operator cannot open catalog
- production was not touched
