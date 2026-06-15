# QA_CHECKLIST - Vehicle Soft release verification

Purpose: mandatory smoke-test checklist for every staging and production release.

## 1. Pre-release code checks

Run before every service restart:

- cd /d C:\transport-report
- git status --short
- git log --oneline -5
- py_compile for app.py, models.py, sec003a_ext.py, migrate_sec003a_real.py, wialon_import.py, workload_report.py, fuel_routes.py, spare_parts.py, translations.py, config.py, run_server.py
- APP IMPORT OK check
- TransportReport service status check

Expected result:

- git status is clean before starting a new release, except temporary source zip/txt files.
- py_compile completes without output or errors.
- APP IMPORT OK is printed.
- TransportReport service is RUNNING before and after release.

## 2. Backup before production deployment

Run before every production deployment that changes code or templates:

- cd /d C:\transport-report
- backup_production_db.bat

Expected result:

- SUCCESS is printed.
- integrity_check: ok.
- Backup file is written to D:\transport-report-backups\production\daily.

## 3. Login/Auth smoke test

Verify:

- Login works for admin.
- Logout works.
- Forced temporary password workflow opens when required.
- User profile/language change works.
- Invalid credentials do not log in.

## 4. Module permissions smoke test

Verify:

- Admin opens all modules.
- Operator with transport=1 can open transport pages.
- Operator with fuel=0 receives 403 on /fuel.
- Operator with wialon=0 receives 403 on /wialon.
- Operator with spare_parts=0 receives 403 on /spare-parts.
- Zero-module test user receives expected 403 responses.

## 5. Transport module smoke test

Verify:

- / opens.
- /entry opens.
- Daily entry with valid data saves.
- Daily entry rejects quantity <= 0.
- Daily entry rejects working equipment without work rows.
- /report opens.
- Excel/report export works if changed by release.

## 6. Reference directories smoke test

Verify:

- Organization save works with valid data.
- Duplicate organization name is rejected.
- Equipment save works with valid organization/name/plate.
- Equipment without plate is rejected.
- Duplicate equipment plate is rejected.
- Work type save works.
- Duplicate work type is rejected.
- Customer save works.
- Duplicate customer is rejected.
- Dangerous delete protections work: used records show Used/Deactivate/Enable states.

## 7. Wialon smoke test

Verify:

- /wialon opens for authorized user.
- Wialon mapping list opens.
- Valid mapping saves.
- Mapping without equipment and without Not in system is rejected.
- One equipment cannot be linked to multiple Wialon objects.
- Inactive equipment is not selectable.
- Auto-match bulk save rejects duplicate Wialon names and duplicate equipment selections.

## 8. Fuel smoke test

Verify:

- /fuel opens for authorized user.
- Warehouses page opens.
- Warehouse edit/save works.
- Initial balance saves with positive value.
- Initial balance allows negative adjustment value.
- Receipts save with quantity > 0.
- Receipts reject quantity <= 0.
- Receipt form has no price field.
- Fuel type is fixed as DT.
- Fuel station edit/save works.
- Disabled fuel station can be reactivated.
- /fuel/api/fuel_ping returns ok.

## 9. Spare parts smoke test

Verify:

- /spare-parts opens for authorized user.
- Valid request with at least one item saves.
- Request without items is rejected.
- Item with empty name is rejected.
- Item with quantity <= 0 is rejected.
- Non-admin cannot approve/reject.
- Admin can approve/reject submitted request.
- User cannot open request from inaccessible organization.

## 10. Deficiencies smoke test

Verify:

- /deficiencies opens for authorized user.
- Valid deficiency saves.
- User cannot create/edit/delete deficiency for inaccessible organization.

## 11. Audit log smoke test

Verify:

- /admin/audit opens for admin.
- Recent tested actions appear in audit log where audit is expected.
- Blocked dangerous delete actions appear in audit log.

## 12. Validation UX smoke test

Verify:

- Multiple validation errors are displayed as a readable list.
- Russian UI shows Russian validation messages.
- Uzbek UI shows Uzbek validation messages where implemented.
- Error messages explain what operator must fix.

## 13. Post-release Git checks

After commit:

- git status --short
- git push origin main

Expected result:

- git status is clean after commit.
- push to origin/main succeeds.

## 14. Rollback rule

Old production server 10.103.25.200 remains rollback-standby only and must not be started unless rollback is explicitly authorized.

## QA003 final whole-application QA  2026-06-15

Result: PASSED.

Staging:

- `QA003_1_FINAL_READ_ONLY_QA_STAGING_OK=YES`
- `QA003_1_FINAL_WHOLE_APPLICATION_READ_ONLY_QA_STAGING=OK`
- warnings: 0
- errors: 0
- DB count errors: 0
- route render errors: 0

Production:

- `QA003_2_FINAL_READ_ONLY_QA_PRODUCTION_OK=YES`
- `QA003_2_FINAL_WHOLE_APPLICATION_READ_ONLY_QA_PRODUCTION=OK`
- warnings: 0
- errors: 0
- DB count errors: 0
- route render errors: 0

Confirmed:

- no DB writes
- no POST requests
- no service restart
- production services running

## DOC003 final documentation closure  2026-06-15

Result: PASSED.

Confirmed:

- DOC003 is docs-only.
- No source code changes.
- No DB changes.
- No POST requests.
- No service restart required.
- Current closure sequence fully documented.

## FUEL-IDX-001 fuel transaction date indexes  2026-06-15

Result: PASSED.

Staging:

- Read-only audit confirmed missing date indexes on active `fuel_transactions2`.
- Before index: date-range query used `SCAN fuel_transactions2`.
- Migration applied successfully.
- Indexes created:
  - `ix_fuel_transactions2_txn_datetime`
  - `ix_fuel_transactions2_station_datetime`
- After index:
  - date-range query uses covering index.
  - station+date-range query uses covering index.
- Business data unchanged.
- Staging services running.

Production:

- Source pull scope verified.
- Source backup created.
- DB backup created.
- Services stopped before SQLite index migration.
- Migration applied successfully.
- Business data unchanged.
- App import OK.
- HTTP smoke passed:
  - `/login`
  - `/fuel/`
  - `/fuel/report`
- Production services running.

Final code commit:

`62001d48886f8a1342cc83a2ab958dc3d8a53ef2`

