# RELEASE_DATA001_2_REFS_WIALON_VALIDATION_20260604 - References and Wialon validation

Task: DATA001-2
Status: COMPLETED on production
Production URL: http://10.103.25.14:5050
Server: srv-yoqsh
Service: TransportReport
Database migration: not required

## Scope

Added validation for reference directories and Wialon mapping workflows.

## Reference validation

- Organization names are normalized and required.
- Duplicate organization names are rejected.
- Equipment organization, name, and plate number are required.
- Equipment plate number is normalized and converted to uppercase.
- Duplicate equipment plate numbers are rejected.
- Equipment category is validated.
- Equipment default price cannot be negative.
- Work type names are normalized and required.
- Duplicate work type names are rejected.
- Work type default price cannot be negative.
- Customer names are normalized and required.
- Duplicate customer names are rejected.
- Customer type is normalized to external/internal.

## Wialon mapping validation

- Wialon object names are normalized.
- Empty or too-short Wialon names are rejected.
- Mapping cannot be saved without equipment unless the object is explicitly marked as not in the system.
- Inactive equipment cannot be selected for Wialon mapping.
- One equipment item cannot be linked to multiple Wialon objects.
- Duplicate Wialon object names are blocked.
- Wialon mapping selectors show only active equipment.

## Wialon auto-match validation

- Empty rows are ignored.
- Duplicate Wialon names inside the submitted form are blocked.
- The same equipment cannot be selected for multiple Wialon objects.
- Inactive equipment cannot be selected.
- Bulk save does not partially save data when validation errors are present.

## Files changed

- app.py
- wialon_import.py
- templates/ref_organizations.html
- templates/ref_equipment.html
- templates/ref_work_types.html
- templates/ref_customers.html
- templates/wialon_mapping_list.html

## Production verification

- py_compile passed for core modules.
- Application import check passed: APP IMPORT OK.
- TransportReport service running after restart.
- Production database backup completed before deployment.
- Backup file: D:\transport-report-backups\production\daily\transport_20260604_102724.db
- DATA001-2 production smoke test passed.
- Valid reference saves verified.
- Duplicate reference values rejected.
- Equipment without plate rejected.
- Wialon mapping validation verified.
- Wialon auto-match validation verified.

## Notes

No database schema changes were made. No migration was required. Old production server 10.103.25.200 remains rollback-standby only and must not be started unless rollback is explicitly authorized.
