# TASK-REF-001B Staging Validation

Date: 2026-06-13

## Summary

TASK-REF-001B improves reference pages for:

- organizations
- work types
- customers

## Scope

Implemented on staging only.

No production rollout yet.

## Implemented

### Organizations

- Added search by organization name and short name.
- Added statistics cards.
- Added visible short name field in create/edit forms.
- Added linked-record count display.
- Preserved delete blocking for organizations with related records.

### Work types

- Added search by name/default unit.
- Added usage filter: all / used / unused.
- Added statistics cards.
- Added diagnostics for:
  - empty default unit
  - zero default price
  - duplicate names
  - values used in daily reports but missing from reference table
- Added usage count column.
- Preserved edit and delete-block behavior.

### Customers

- Added search by customer name.
- Added customer type filter: all / internal / external.
- Added usage filter: all / used / unused.
- Added statistics cards.
- Added diagnostics for customer values used in daily reports but missing from reference table.
- Added usage count column.
- Preserved edit and delete-block behavior.

## Safety scope

No database schema changes.

No data migrations.

No automatic duplicate cleanup.

No automatic customer normalization.

No changes to daily report, Wialon, fuel, spare-parts, Telegram bot, or BOT003 business logic.

## Validation

Passed:

- `py_compile app.py models.py config.py run_server.py`
- `APP_IMPORT_OK`
- template load checks:
  - `ref_organizations.html`
  - `ref_work_types.html`
  - `ref_customers.html`
  - `ref_equipment.html`
- authenticated route checks:
  - `/ref/organizations`
  - `/ref/organizations?q=ПСТ`
  - `/ref/work_types`
  - `/ref/work_types?q=Пахта`
  - `/ref/work_types?usage=used`
  - `/ref/work_types?usage=unused`
  - `/ref/customers`
  - `/ref/customers?q=Кластер`
  - `/ref/customers?customer_type=internal`
  - `/ref/customers?customer_type=external`
  - `/ref/customers?usage=used`
  - `/ref/customers?usage=unused`
  - `/ref/equipment`
  - `/entry`
  - `/report`
  - `/`
- changed-template encoding check passed.

## Next step

Manual browser validation on staging:

- `http://10.103.25.14:5051/ref/organizations`
- `http://10.103.25.14:5051/ref/work_types`
- `http://10.103.25.14:5051/ref/customers`
