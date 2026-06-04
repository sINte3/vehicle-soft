# RELEASE_DATA001_1_VALIDATION_20260604 - Input validation phase 1

Task: DATA001-1
Status: COMPLETED on production
Production URL: http://10.103.25.14:5050
Server: srv-yoqsh
Service: TransportReport
Database migration: not required

## Scope

Added backend validation for critical operator input forms and aligned Fuel business rules with real operations.

## Transport daily entry validation

- Organization is required.
- Work date must be valid.
- Equipment must belong to the selected organization.
- Inactive equipment cannot be used for daily work entry.
- Working equipment must have at least one work row.
- Work row must have a work type.
- Work quantity must be greater than zero.
- Work price cannot be negative.
- Payment type is normalized to valid values.

## Fuel validation and business rules

- Fuel type is fixed as DT.
- Initial fuel balance allows negative quantities, because adjustment balances can be negative.
- Initial fuel balance date must be valid.
- Fuel receipt quantity must be greater than zero.
- Fuel receipt date must be valid.
- Fuel receipt warehouse must exist.
- Fuel receipt price fields were removed from UI and backend logic; internal stations are not commercial fuel sellers.
- Topaz fuel transactions are stored as DT.
- Fuel station name, warehouse, and Topaz ID are validated.
- Fuel station Topaz ID must be positive and unique during create/update.

## Spare parts validation

- Spare part request organization is required.
- Request date must be valid.
- Request must contain at least one item.
- Spare part item name is required.
- Spare part item quantity must be greater than zero.
- Inactive equipment cannot be selected.
- Equipment must belong to the selected organization.
- Invalid request actions are rejected.

## Reference validation

- Organization name is required.
- Equipment organization and name are required.
- Equipment default price cannot be negative.
- Work type name is required.
- Work type default price cannot be negative.
- Customer name is required.
- Customer type is normalized to external/internal.

## Files changed

- app.py
- fuel_routes.py
- spare_parts.py
- templates/fuel/initial_balance.html
- templates/fuel/receipts.html

## Production verification

- py_compile passed for core modules.
- Application import check passed: APP IMPORT OK.
- TransportReport service running after restart.
- Production database backup completed before deployment.
- Backup file: D:\transport-report-backups\production\daily\transport_20260604_100121.db
- DATA001-1 production smoke test passed.
- Valid daily report save verified.
- Valid Fuel initial balance save verified, including negative quantity.
- Valid Fuel receipt save verified without price field.
- Valid spare parts request save verified.
- Invalid daily quantity rejected.
- Invalid Fuel receipt quantity rejected.
- Spare parts request without items rejected.
- Spare part item with zero or negative quantity rejected.

## Notes

No database schema changes were made. No migration was required. Old production server 10.103.25.200 remains rollback-standby only and must not be started unless rollback is explicitly authorized.
