# RELEASE_SEC003E_DANGEROUS_DELETES_20260603 - Dangerous delete protection

Task: TASK-SEC-003E
Status: COMPLETED on production
Production URL: http://10.103.25.14:5050
Server: srv-yoqsh
Service: TransportReport
Database migration: not required

## Scope

Protected dangerous delete actions and aligned UI with backend delete rules.

## Backend protection

- Blocked organization deletion when linked records exist.
- Protected equipment deletion when linked records exist; active linked equipment is deactivated instead of physically deleted.
- Blocked work type deletion when used in daily records.
- Blocked customer deletion when used in daily records.
- Blocked fuel warehouse deletion when linked stations, receipts, or initial balances exist.
- Protected fuel station deletion when Topaz transactions exist; active linked stations are deactivated instead of physically deleted.
- Added equipment reactivation.
- Added fuel station reactivation.

## Audit actions

- organization_delete_blocked
- equipment_delete_blocked
- equipment_delete_blocked_deactivated
- equipment_reactivated
- work_type_delete_blocked
- customer_delete_blocked
- fuel_warehouse_delete_blocked
- fuel_station_delete_blocked
- fuel_station_delete_blocked_deactivated
- fuel_station_reactivated

## UI changes

- Linked organizations show used/blocked state instead of misleading delete action.
- Linked equipment shows Deactivate; inactive equipment shows Enable.
- Used work types and customers show used state with usage count.
- Linked fuel warehouses show used state instead of delete action.
- Linked fuel stations show Deactivate; inactive stations show Enable.

## Files changed

- app.py
- fuel_routes.py
- templates/ref_organizations.html
- templates/ref_equipment.html
- templates/ref_work_types.html
- templates/ref_customers.html
- templates/fuel/warehouses.html
- templates/fuel/stations.html

## Production verification

- py_compile passed for core modules.
- Application import check passed: APP IMPORT OK.
- TransportReport service running after restart.
- Production database backup created successfully before deployment.
- Backup file: D:\transport-report-backups\production\daily\transport_20260603_111926.db
- Backup integrity_check: ok.
- SEC003E production smoke test passed.
- fuel_station_reactivated verified on production.

## Notes

No database schema changes were made. No migration was required. Old production server 10.103.25.200 remains rollback-standby only and must not be started unless rollback is explicitly authorized.
