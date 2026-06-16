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

## FUEL-IDX-002 sargable fuel transaction date filters - 2026-06-15

Result: PASSED.

Staging:

- Read-only audit found two old func.date(FuelTransaction2.txn_datetime) filters.
- Source patch changed only fuel_routes.py.
- TARGET_PRESENT=False.
- FUNC_DATE_CALL_LINES_AFTER_PATCH=[].
- Old/new count comparison matched:
  - 30 vs 30 on staging.
- New range query uses ix_fuel_transactions2_txn_datetime.
- Authenticated render passed:
  - /fuel/
  - /fuel/stations
  - /fuel/transactions
  - /fuel/report
- Unauthenticated smoke passed.
- TransportReportStaging restarted and running.

Production:

- Pull scope verified:
  - fuel_routes.py only.
- Source backup created.
- Production compile passed.
- Only TransportReport restarted.
- TransportBot and TransportBot003 were not restarted.
- Source scan confirmed no remaining old func.date(...) filters.
- Old/new count comparison matched:
  - 47 vs 47 on production.
- New range query uses ix_fuel_transactions2_txn_datetime.
- HTTP smoke passed:
  - /login
  - /fuel/
  - /fuel/stations
  - /fuel/transactions
  - /fuel/report
- No DB writes were performed.
- Final production services running.

Final code commit:

{code_commit}

## CLEAN-TPL-001 orphaned legacy fuel template cleanup - 2026-06-15

Result: PASSED.

Deleted templates:

- templates/fuel_balance.html
- templates/fuel_dashboard.html
- templates/fuel_history.html
- templates/fuel_receipts.html
- templates/fuel_sync_log.html

Staging:

- Read-only audit confirmed root-level legacy fuel templates were orphaned.
- Backup created before deletion.
- Deleted only the five confirmed legacy templates.
- Active templates/fuel/*.html files remained present.
- No Python render_template references to deleted files.
- App import OK.
- Authenticated render passed:
  - /fuel/
  - /fuel/warehouses
  - /fuel/initial-balance
  - /fuel/receipts
  - /fuel/transactions
  - /fuel/stations
  - /fuel/report
  - /fuel/warnings
- Unauthenticated smoke passed.
- No DB writes were performed.
- No POST requests were executed.
- No service restart was performed.

Production:

- Pull scope verified:
  - five template deletions only.
- Production source backup created.
- Deleted legacy templates confirmed absent.
- Active templates/fuel/*.html files confirmed present.
- App import OK.
- Authenticated render passed:
  - /fuel/
  - /fuel/warehouses
  - /fuel/initial-balance
  - /fuel/receipts
  - /fuel/transactions
  - /fuel/stations
  - /fuel/report
  - /fuel/warnings
- Unauthenticated smoke passed.
- No DB writes were performed.
- No POST requests were executed.
- No service restart was performed.
- Final production services running:
  - TransportReport
  - TransportBot
  - TransportBot003

Final code commit:

{code_commit}

## SEC-HARD-001 basic security hardening - 2026-06-15

Result: PASSED.

Changed files:

- app.py
- fuel_routes.py

Staging:

- Read-only audit found:
  - MAX_CONTENT_LENGTH missing.
  - explicit 500 handler missing.
  - fuel sync token check using direct comparison.
- Source backup created.
- py_compile passed.
- git diff --check passed.
- Source scan passed:
  - app_has_MAX_CONTENT_LENGTH=True
  - app_has_500_handler=True
  - fuel_has_hmac_import=True
  - fuel_has_compare_digest=True
  - fuel_old_token_compare_removed=True
- App import OK.
- URL rules count: 86.
- MAX_CONTENT_LENGTH runtime value: 16777216.
- 500 handler registered.
- HTTP smoke passed:
  - /login
  - /fuel/api/fuel_ping
  - /fuel/
  - /fuel/report
  - /fuel/transactions
  - /nonexistent-security-audit-url
- TransportReportStaging restarted and running.
- No DB writes were performed.
- No POST requests were executed.

Production:

- Pull scope verified:
  - app.py
  - fuel_routes.py
- Production source backup created.
- Production compile passed.
- Only TransportReport restarted.
- TransportBot and TransportBot003 were not restarted.
- Source scan passed:
  - app_has_MAX_CONTENT_LENGTH=True
  - app_has_500_handler=True
  - fuel_has_hmac_import=True
  - fuel_has_compare_digest=True
  - fuel_old_token_compare_removed=True
- App import OK.
- URL rules count: 86.
- MAX_CONTENT_LENGTH runtime value: 16777216.
- 500 handler registered.
- HTTP smoke passed:
  - /login
  - /fuel/api/fuel_ping
  - /fuel/
  - /fuel/report
  - /fuel/transactions
  - /nonexistent-security-audit-url
- No DB writes were performed.
- No POST requests were executed.
- Final production services running:
  - TransportReport
  - TransportBot
  - TransportBot003

Final code commit:

{code_commit}

## API-FUEL-LEGACY-001 fuel sync legacy alias audit - 2026-06-15

Result: PASSED.

Audit type:

- read-only;
- staging only;
- no source changes;
- no DB writes;
- no POST requests;
- no service restart.

Confirmed routes:

- /fuel/api/fuel_sync
  - endpoint: fuel.api_fuel_sync
  - method: POST
- /api/fuel_sync
  - endpoint: api_fuel_sync_legacy
  - method: POST

Validation:

- app import OK.
- URL rules count: 86.
- Canonical route present.
- Legacy alias present.
- Both endpoints call shared _perform_fuel_sync().
- CSRF exemption includes both:
  - /fuel/api/fuel_sync
  - /api/fuel_sync
- hmac.compare_digest present in shared token check.
- GET /fuel/api/fuel_sync returned 405.
- GET /api/fuel_sync returned 405.
- No tracebacks found.
- Final staging git status clean.
- Staging services running:
  - TransportReportStaging
  - TransportBotStaging
  - TransportBot003Staging

Decision:

- Keep /api/fuel_sync temporarily.
- Treat it as deprecated.
- Do not remove until Topaz agent configuration is confirmed to use /fuel/api/fuel_sync.

## PERF-DASH-001 fuel dashboard/report query optimization - 2026-06-15

Result: PASSED.

Scope:

- fuel_routes.py only.
- No DB schema changes.
- No migration.
- No POST requests during validation.
- No Telegram bot restart.

Baseline audit:

- /: 101 SELECT.
- /fuel/: 84 SELECT.
- /fuel/report: 94 SELECT.
- /spare-parts/: 29 SELECT, left for future task.
- /wialon/report/export writes audit_logs on GET, excluded from this task.

Staging patch validation:

- py_compile passed.
- git diff --check passed.
- app import OK.
- URL rules count: 86.
- Source checks passed:
  - PERF-DASH-001 V3B marker present.
  - _fuel_balance_map present.
  - _fuel_station_count_map present.
  - _fuel_today_expense_map present.
  - _fuel_latest_txn_map present.
  - bulk collect loop present.
  - joinedload present.
  - selectinload(FuelWarehouse.stations) absent.
- SQL count after patch:
  - /: 28 SELECT.
  - /fuel/: 11 SELECT.
  - /fuel/report: 19 SELECT.
- No DB writes.
- No POST requests.
- No tracebacks.
- Staging post-restart smoke OK.

Production validation:

- Production pull scope: fuel_routes.py only.
- Production source backup created.
- Production pull fast-forward only.
- Production py_compile passed.
- Production source validation passed.
- Only TransportReport restarted.
- TransportBot and TransportBot003 were not restarted.
- Production smoke OK.

Final production services:

- TransportReport: RUNNING.
- TransportBot: RUNNING.
- TransportBot003: RUNNING.

## PERF-SPARE-001 spare parts index query optimization - 2026-06-15

Result: PASSED.

Scope:

- spare_parts.py only.
- No DB schema changes.
- No migration.
- No POST requests during validation.
- No Telegram bot restart.

Baseline audit:

- /spare-parts/: 29 SELECT.
- Repeated spare_part_request_items SELECTs: 12.
- Repeated equipment SELECTs: 8.
- Repeated status count SELECTs: 4.
- Repeated users SELECTs: 2.

Staging patch validation:

- py_compile passed.
- git diff --check passed.
- app import OK.
- URL rules count: 86.
- Source checks passed:
  - PERF-SPARE-001B marker present.
  - joinedload present.
  - selectinload present.
  - grouped counts present.
  - old status count loop removed.
  - selectinload(SparePartRequest.items) present.
  - joinedload(SparePartRequest.equipment) present.
  - joinedload(SparePartRequest.organization) present.
  - joinedload(SparePartRequest.creator) present.
- SQL count after patch:
  - /spare-parts/: 4 SELECT.
- No DB writes.
- No POST requests.
- No tracebacks.
- Staging post-restart smoke OK.

Production validation:

- Production pull scope: spare_parts.py only.
- Production source backup created.
- Production pull fast-forward only.
- Production py_compile passed.
- Production source validation passed.
- Only TransportReport restarted.
- TransportBot and TransportBot003 were not restarted.
- Production smoke OK.

Final production services:

- TransportReport: RUNNING.
- TransportBot: RUNNING.
- TransportBot003: RUNNING.

## AUDIT-GET-SIDE-EFFECT-001 Wialon GET export side effect - 2026-06-15

Result: PASSED.

Scope:

- wialon_import.py only.
- No DB schema changes.
- No migration.
- No POST requests during validation.
- No Telegram bot restart.

Baseline audit:

- /wialon/report/export attempted INSERT INTO audit_logs during GET.
- DML was blocked by read-only audit hook.
- No actual DB writes were performed during audit.

Clean sampled GET routes:

- /wialon/report
- /wialon
- /report
- /fuel/report
- /spare-parts/

Diagnostic:

- source file: wialon_import.py
- function: wialon_report_export
- write source:
  - _audit_wialon(...)
  - db.session.commit()

Staging patch validation:

- py_compile passed.
- git diff --check passed.
- app import OK.
- URL rules count: 86.
- Source checks passed:
  - AUDIT-GET-SIDE-EFFECT-001B marker present.
  - _audit_wialon(...) removed from wialon_report_export.
  - db.session.commit() removed from wialon_report_export.
  - send_file response preserved.
- /wialon/report/export:
  - status OK.
  - response mimetype application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.
  - DML count: 0.
  - SELECT count: 2.
- No tracebacks.
- No POST requests.
- Staging post-restart smoke OK.

Production validation:

- Production pull scope: wialon_import.py only.
- Production source backup created.
- Production pull fast-forward only.
- Production py_compile passed.
- Production source validation passed.
- Only TransportReport restarted.
- TransportBot and TransportBot003 were not restarted.
- Production smoke OK.

Final production services:

- TransportReport: RUNNING.
- TransportBot: RUNNING.
- TransportBot003: RUNNING.

## AUDIT-GET-SIDE-EFFECT-002 Wialon workload GET export side effect - 2026-06-15

Result: PASSED.

Scope:

- wialon_import.py only.
- No DB schema changes.
- No migration.
- No POST requests during validation.
- No Telegram bot restart.

Baseline audit:

- /wialon/workload/export attempted INSERT INTO audit_logs during GET.
- DML was blocked by read-only audit hook.
- No actual DB writes were performed during audit.

Clean sampled export/download GET routes:

- /ref/equipment/export
- /ref/work_types/export_diagnostics
- /ref/customers/export_diagnostics
- /wialon/report/export
- /fuel/report?export=1
- /report?export=1

Source area:

- source file: wialon_import.py
- function: wialon_workload_export
- write source:
  - _audit_wialon(...)
  - db.session.commit()

Staging patch validation:

- py_compile passed.
- git diff --check passed.
- app import OK.
- URL rules count: 86.
- Source checks passed:
  - AUDIT-GET-SIDE-EFFECT-001B marker still present in wialon_report_export.
  - _audit_wialon(...) removed from wialon_report_export.
  - db.session.commit() removed from wialon_report_export.
  - AUDIT-GET-SIDE-EFFECT-002B marker present in wialon_workload_export.
  - _audit_wialon(...) removed from wialon_workload_export.
  - db.session.commit() removed from wialon_workload_export.
  - workload send_file response preserved.
- /wialon/workload/export:
  - status OK.
  - response mimetype application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.
  - DML count: 0.
  - SELECT count: 20.
- No tracebacks.
- No POST requests.
- Staging post-restart smoke OK.

Production validation:

- Production pull scope: wialon_import.py only.
- Production source backup created.
- Production pull fast-forward only.
- Production py_compile passed.
- Production source validation passed.
- Only TransportReport restarted.
- TransportBot and TransportBot003 were not restarted.
- Production smoke OK.

Final production services:

- TransportReport: RUNNING.
- TransportBot: RUNNING.
- TransportBot003: RUNNING.

## AUDIT-GET-SIDE-EFFECT-003 Logout GET side effect - 2026-06-16

Result: PASSED.

Scope:

- app.py only.
- No DB schema changes.
- No migration.
- No POST requests during validation.
- No Telegram bot restart.

Baseline audit:

- /logout attempted INSERT INTO audit_logs during GET.
- DML was blocked by read-only audit hook.
- No actual DB writes were performed during audit.

Implementation:

- Removed log_audit(...) from GET /logout.
- Removed db.session.commit() from GET /logout.
- Preserved logout_user().
- Preserved redirect behavior.

Staging patch validation:

- py_compile passed.
- git diff --check passed.
- app import OK.
- URL rules count: 86.
- Source checks passed:
  - AUDIT-GET-SIDE-EFFECT-003B marker present.
  - log_audit(...) removed from logout.
  - db.session.commit() removed from logout.
  - logout_user() preserved.
  - redirect preserved.
- /logout:
  - status OK.
  - redirect response preserved.
  - DML count: 0.
  - SELECT count: 0.
- No tracebacks.
- No POST requests.
- Staging post-restart smoke OK.

Production validation:

- Production pull scope: app.py only.
- Production source backup created.
- Production pull fast-forward only.
- Production py_compile passed.
- Production source validation passed.
- Only TransportReport restarted.
- TransportBot and TransportBot003 were not restarted.
- Production smoke OK.

Corrected post-rollout revalidation:

- The initial 003C staging DML validation had a typo in the validation helper, not in application code.
- Corrected revalidation was run on staging and production.
- Staging result: PASSED.
- Production result: PASSED.
- Revalidated routes:
  - /logout
  - /
  - /admin/audit
  - /fuel/
  - /fuel/report
  - /spare-parts/
  - /wialon/report/export
  - /wialon/workload/export
- All revalidated routes had DML count 0.
- No source files were modified during revalidation.
- No service restart was performed during revalidation.

Final production services:

- TransportReport: RUNNING.
- TransportBot: RUNNING.
- TransportBot003: RUNNING.

## PERF-REF-001 Reference equipment linked counters - 2026-06-16

Result: PASSED.

Scope:

- app.py only.
- No DB schema changes.
- No migration.
- No template changes.
- No POST requests during validation.
- No Telegram bot restart.

Baseline audit:

- /ref/equipment:
  - SELECT count: 1348.
  - response body: about 2.33 MB.
  - DML count: 0.
  - no traceback.
- Root cause:
  - 336 repeated daily_records count queries.
  - 336 repeated engine_hours_records count queries.
  - 336 repeated vialon_mappings count queries.
  - 336 repeated spare_part_requests count queries.

Source diagnostic:

- file: app.py.
- function: ref_equipment.
- template `templates/ref_equipment.html` did not contain `.count()` calls.
- N+1 counts were in the Flask view.

Implementation:

- Replaced four per-row `.count()` calls with grouped bulk count maps.
- Preserved equipment_delete_info structure.
- Preserved can_delete / can_deactivate / is_disabled logic.
- Preserved ref_equipment template rendering.

Staging patch validation:

- py_compile passed.
- git diff --check passed.
- app import OK.
- URL rules count: 86.
- Source checks passed:
  - PERF-REF-001C marker present.
  - bulk helper present.
  - per-row DailyRecord count removed.
  - per-row EngineHoursRecord count removed.
  - per-row VialonMapping count removed.
  - per-row SparePartRequest count removed.
  - render_template preserved.
- /ref/equipment:
  - SELECT count reduced to 8.
  - repeated SQL count: 0.
  - DML count: 0.
  - no traceback.
- Staging post-restart smoke OK.

Production validation:

- Production pull scope: app.py only.
- Production source backup created.
- Production pull fast-forward only.
- Production py_compile passed.
- Production source validation passed.
- /ref/equipment:
  - SELECT count: 8.
  - repeated SQL count: 0.
  - DML count: 0.
  - no traceback.
- Only TransportReport restarted.
- TransportBot and TransportBot003 were not restarted.
- Production smoke OK.

Final production services:

- TransportReport: RUNNING.
- TransportBot: RUNNING.
- TransportBot003: RUNNING.

## PERF-REF-002 Reference work types usage counters - 2026-06-16

Result: PASSED.

Scope:

- app.py only.
- No DB schema changes.
- No migration.
- No template changes.
- No POST requests during validation.
- No Telegram bot restart.

Baseline diagnostic:

- /ref/work_types:
  - SELECT count: 106.
  - repeated SQL count: 1 pattern.
  - repeated daily_records count queries: 104.
  - DML count: 0.
  - no traceback.

Source diagnostic:

- file: app.py.
- function: ref_work_types.
- problem was in Flask view, not template.

Implementation:

- Replaced per-work-type `.count()` calls with one grouped bulk count map.
- Reused grouped `daily_work_type_counts` map for `wt_used`.
- Removed separate distinct query for `DailyRecord.work_type`.
- Preserved existing statistics and filter logic.
- Preserved ref_work_types template rendering.

Staging patch validation:

- py_compile passed.
- git diff --check passed.
- app import OK.
- URL rules count: 86.
- Source checks passed:
  - PERF-REF-002B marker present.
  - bulk GROUP BY present.
  - daily_work_type_counts map present.
  - per-row DailyRecord count removed.
  - distinct DailyRecord.work_type query removed.
  - render_template preserved.
- /ref/work_types:
  - SELECT count reduced to 2.
  - repeated SQL count: 0.
  - DML count: 0.
  - no traceback.
- Staging post-restart smoke OK.

Production validation:

- Production pull scope: app.py only.
- Production source backup created.
- Production pull fast-forward only.
- Production py_compile passed.
- Production source validation passed.
- /ref/work_types:
  - SELECT count: 2.
  - repeated SQL count: 0.
  - DML count: 0.
  - no traceback.
- Only TransportReport restarted.
- TransportBot and TransportBot003 were not restarted.
- Production smoke OK.

Final production services:

- TransportReport: RUNNING.
- TransportBot: RUNNING.
- TransportBot003: RUNNING.

## PERF-REF-003 Reference customers usage counters - 2026-06-16

Result: PASSED.

Scope:

- app.py only.
- No DB schema changes.
- No migration.
- No template changes.
- No POST requests during validation.
- No Telegram bot restart.

Baseline diagnostic:

- /ref/customers:
  - SELECT count: 11.
  - repeated SQL count: 1 pattern.
  - repeated daily_records count queries: 9.
  - DML count: 0.
  - no traceback.

Source diagnostic:

- file: app.py.
- function: ref_customers.
- problem was in Flask view, not template.

Implementation:

- Replaced per-customer `.count()` calls with one grouped bulk count map.
- Reused grouped `daily_customer_counts` map for `cust_used`.
- Removed separate distinct query for `DailyRecord.customer`.
- Preserved existing statistics and filter logic.
- Preserved ref_customers template rendering.

Staging patch validation:

- py_compile passed.
- git diff --check passed.
- app import OK.
- URL rules count: 86.
- Source checks passed:
  - PERF-REF-003B marker present.
  - bulk GROUP BY present.
  - daily_customer_counts map present.
  - per-row DailyRecord count removed.
  - distinct DailyRecord.customer query removed.
  - render_template preserved.
- /ref/customers:
  - SELECT count reduced to 2.
  - repeated SQL count: 0.
  - DML count: 0.
  - no traceback.
- Staging post-restart smoke OK.

Production validation:

- Production pull scope: app.py only.
- Production source backup created.
- Production pull fast-forward only.
- Production py_compile passed.
- Production source validation passed.
- /ref/customers:
  - SELECT count: 2.
  - repeated SQL count: 0.
  - DML count: 0.
  - no traceback.
- Only TransportReport restarted.
- TransportBot and TransportBot003 were not restarted.
- Production smoke OK.

Final production services:

- TransportReport: RUNNING.
- TransportBot: RUNNING.
- TransportBot003: RUNNING.

## PERF-REF-004 Reference organizations linked counters - 2026-06-16

Result: PASSED.

Scope:

- app.py only.
- No DB schema changes.
- No migration.
- No template changes.
- No POST requests during validation.
- No Telegram bot restart.

Baseline diagnostic:

- /ref/organizations:
  - SELECT count: 86.
  - repeated SQL count: 5 patterns.
  - repeated linked count queries:
    - Equipment: 17.
    - FuelWarehouse: 17.
    - SparePartRequest: 17.
    - Deficiency: 17.
    - User relationship count: 17.
  - DML count: 0.
  - no traceback.

Source diagnostic:

- file: app.py.
- function: ref_organizations.
- problem was in Flask view, not template.

Implementation:

- Replaced per-organization linked `.count()` calls with grouped bulk count maps.
- Used helper `_org_count_map(model)` for models with `organization_id`.
- Counted users through `user_organizations`.
- Removed `org.users.count()` from the route.
- Preserved existing statistics and delete-protection logic.
- Preserved ref_organizations template rendering.

Staging patch validation:

- py_compile passed.
- git diff --check passed.
- app import OK.
- URL rules count: 86.
- Source checks passed:
  - PERF-REF-004B marker present.
  - org_ids list present.
  - `_org_count_map` helper present.
  - grouped count maps present.
  - user count through `user_organizations` present.
  - per-row linked counts removed.
  - render_template preserved.
- /ref/organizations:
  - SELECT count reduced to 6.
  - repeated SQL count: 0.
  - DML count: 0.
  - no traceback.
- Staging post-restart smoke OK.

Production validation:

- Production pull scope: app.py only.
- Production source backup created.
- Production pull fast-forward only.
- Production py_compile passed.
- Production source validation passed.
- /ref/organizations:
  - SELECT count: 6.
  - repeated SQL count: 0.
  - DML count: 0.
  - no traceback.
- Only TransportReport restarted.
- TransportBot and TransportBot003 were not restarted.
- Production smoke OK.

Final production services:

- TransportReport: RUNNING.
- TransportBot: RUNNING.
- TransportBot003: RUNNING.

## PERF-WIALON-MAP-001 Wialon mapping response size - 2026-06-16

Result: PASSED.

Scope:

- wialon_import.py.
- templates/wialon_mapping_list.html.
- No DB schema changes.
- No migration.
- No POST requests during validation.
- No Telegram bot restart.

Baseline diagnostic:

- /wialon/mapping:
  - response chars: 19,225,278.
  - response UTF-8 bytes: 19,898,023.
  - `<option>` count: 128,692.
  - `<select>` count: 384.
  - SQL count: 20 SELECT.
  - repeated SQL: 17 organization lazy-load queries.
  - DML count: 0.
  - no traceback.

Source diagnostic:

- route file: wialon_import.py.
- route function: wialon_mapping_list.
- template: templates/wialon_mapping_list.html.
- problem was repeated template rendering of full equipment option lists plus missing eager-load of equipment organization data.

Implementation:

- Added `joinedload(VialonMapping.equipment).joinedload(Equipment.organization)`.
- Added `joinedload(Equipment.organization)` for active equipment list.
- Built shared `equipment_options` payload in Flask.
- Passed `equipment_options` to the template.
- Removed repeated `{% for eq in all_equipment %}` option loops.
- Added shared client-side dropdown population.
- Preserved existing mapping save/edit/delete/skip workflow.

Staging validation:

- py_compile passed.
- git diff --check passed.
- app import OK.
- URL rules count: 86.
- Source checks passed:
  - route marker present.
  - template marker present.
  - eager loading present.
  - shared equipment options present.
  - repeated `all_equipment` option loops removed.
  - save/edit JS functions preserved.
- /wialon/mapping:
  - response chars: 898,253.
  - response UTF-8 bytes: 947,349.
  - `<option>` count: 387.
  - SQL count: 3 SELECT.
  - repeated SQL count: 0.
  - DML count: 0.
  - no traceback.
- Staging post-restart smoke OK.

Production validation:

- Production pull scope:
  - wialon_import.py.
  - templates/wialon_mapping_list.html.
- Production source backup created.
- Production pull fast-forward only.
- Production py_compile passed.
- Production source validation passed.
- /wialon/mapping:
  - response chars: 898,253.
  - response UTF-8 bytes: 947,349.
  - `<option>` count: 387.
  - SQL count: 3 SELECT.
  - repeated SQL count: 0.
  - DML count: 0.
  - no traceback.
- Only TransportReport restarted.
- TransportBot and TransportBot003 were not restarted.
- Production smoke OK.

Final production services:

- TransportReport: RUNNING.
- TransportBot: RUNNING.
- TransportBot003: RUNNING.

## PERF-REF-BODY-001 Reference equipment response size - 2026-06-16

Result: PASSED.

Scope:

- templates/ref_equipment.html.
- No DB schema changes.
- No migration.
- No POST requests during validation.
- No Telegram bot restart.

Baseline diagnostic:

- /ref/equipment:
  - response chars: 2,330,660.
  - response UTF-8 bytes: 2,496,903.
  - `<option>` count: 8,783.
  - `<select>` count: 676.
  - `<form>` count: 675.
  - `<input>` count: 2,762.
  - SQL count: 8 SELECT.
  - repeated SQL count: 0.
  - DML count: 0.
  - no traceback.

Source diagnostic:

- route file: app.py.
- route function: ref_equipment.
- template: templates/ref_equipment.html.
- problem was remaining response body size, not SQL.
- heaviest repeated HTML was edit-row organization/category option lists.

Implementation:

- Added marker:
  - PERF-REF-BODY-001B_MARKER.
- Added shared organization options payload:
  - REF_EQUIPMENT_ORG_OPTIONS.
- Added shared category options payload:
  - REF_EQUIPMENT_CATEGORY_OPTIONS.
- Added client-side edit-select population:
  - populateRefEquipmentEditSelects.
- Removed repeated edit-row organization option loop.
- Removed repeated edit-row category option loop.
- Preserved existing filter controls.
- Preserved add form.
- Preserved delete/deactivate/enable forms.
- Preserved route and SQL logic.

Staging validation:

- py_compile passed.
- git diff --check passed.
- app import OK.
- URL rules count: 86.
- Source checks passed:
  - marker present.
  - shared organization class present.
  - shared category class present.
  - shared option payloads present.
  - old edit loops removed.
  - delete/enable forms preserved.
- /ref/equipment:
  - response chars: 1,422,642.
  - response UTF-8 bytes: 1,498,837.
  - `<option>` count: 719.
  - SQL count: 8 SELECT.
  - repeated SQL count: 0.
  - DML count: 0.
  - no traceback.
- Staging post-restart smoke OK.

Production validation:

- Production pull scope:
  - templates/ref_equipment.html.
- Production source backup created:
  - D:\transport-report-backups\production\source\PERF_REF_BODY_001C_20260616_121122.
- Production pull fast-forward only.
- Production py_compile passed.
- Production source validation passed.
- /ref/equipment:
  - response chars: 1,422,776.
  - response UTF-8 bytes: 1,498,971.
  - `<option>` count: 719.
  - SQL count: 8 SELECT.
  - repeated SQL count: 0.
  - DML count: 0.
  - no traceback.
- Only TransportReport restarted.
- TransportBot and TransportBot003 were not restarted.
- Production smoke OK.

Final production services:

- TransportReport: RUNNING.
- TransportBot: RUNNING.
- TransportBot003: RUNNING.

## PERF-REF-BODY-002 Reference equipment inline edit rendering - 2026-06-16

Result: PASSED.

Scope:

- templates/ref_equipment.html.
- No DB schema changes.
- No migration.
- No POST requests during validation.
- No Telegram bot restart.

Baseline diagnostic:

- /ref/equipment:
  - response chars: 1,422,642.
  - response UTF-8 bytes: 1,498,837.
  - `<tr>` count: 673.
  - `<select>` count: 676.
  - `<option>` count: 719.
  - `<input>` count: 2,762.
  - `<form>` count: 675.
  - CSRF hidden inputs: 674.
  - hidden `id` inputs: 336.
  - old inline edit rows: 336.
  - SQL count: 8 SELECT.
  - repeated SQL count: 0.
  - DML count: 0.
  - no traceback.

Source diagnostic:

- route file: app.py.
- route function: ref_equipment.
- template: templates/ref_equipment.html.
- write endpoint: save_equipment.
- save_equipment accepts optional `id` and the same fields used by the edit form.
- problem was repeated hidden edit forms, not SQL.

Implementation:

- Added marker:
  - PERF-REF-BODY-002B_MARKER.
- Added row-level `data-*` attributes for equipment values.
- Removed old per-row hidden edit row:
  - `eq-edit-{{ eq.id }}`.
- Added one shared edit row:
  - `ref-equipment-edit-row`.
- Added reusable edit JavaScript:
  - `editEq(id)`.
  - `cancelEqEdit()`.
- Preserved existing save endpoint:
  - `/ref/equipment/save`.
- Preserved delete/deactivate/enable forms.
- Preserved shared option payloads from PERF-REF-BODY-001.

Staging validation:

- py_compile passed.
- git diff --check passed.
- app import OK.
- URL rules count: 86.
- Source checks passed:
  - marker present.
  - shared edit row present.
  - row data attributes present.
  - old inline edit row removed.
  - old edit DOM references removed.
  - shared organization/category options preserved.
  - delete/enable forms preserved.
- /ref/equipment:
  - response chars: 635,796.
  - response UTF-8 bytes: 681,467.
  - old inline edit rows: 0.
  - shared edit rows: 1.
  - data rows: 336.
  - `<select>` count: 6.
  - `<option>` count: 49.
  - `<input>` count: 417.
  - `<form>` count: 340.
  - SQL count: 8 SELECT.
  - repeated SQL count: 0.
  - DML count: 0.
  - no traceback.
- Staging post-restart smoke OK.

Production validation:

- Production pull scope:
  - templates/ref_equipment.html.
- Production source backup created:
  - D:\transport-report-backups\production\source\PERF_REF_BODY_002C_20260616_122247.
- Production pull fast-forward only.
- Production py_compile passed.
- Production source validation passed.
- /ref/equipment:
  - response chars: 635,930.
  - response UTF-8 bytes: 681,601.
  - old inline edit rows: 0.
  - shared edit rows: 1.
  - data rows: 336.
  - `<select>` count: 6.
  - `<option>` count: 49.
  - `<input>` count: 417.
  - `<form>` count: 340.
  - SQL count: 8 SELECT.
  - repeated SQL count: 0.
  - DML count: 0.
  - no traceback.
- Only TransportReport restarted.
- TransportBot and TransportBot003 were not restarted.
- Production smoke OK.

Final production services:

- TransportReport: RUNNING.
- TransportBot: RUNNING.
- TransportBot003: RUNNING.

## PERF-WIALON-WORKLOAD-001 Wialon workload bulk equipment loading - 2026-06-16

Result: PASSED.

Scope:

- workload_report.py.
- wialon_import.py.
- No DB schema changes.
- No migration.
- No template changes.
- No POST requests during validation.
- No Telegram bot restart.

Baseline diagnostic:

- `/wialon/auto_match`:
  - response UTF-8 bytes: 31,000.
  - SQL count: 3 SELECT.
  - repeated SQL count: 0.
  - DML count: 0.
  - no traceback.
- `/wialon/workload`:
  - response UTF-8 bytes: 230,785.
  - SQL count: 21 SELECT.
  - repeated SQL count: 2.
  - repeated equipment query count: 17.
  - DML count: 0.
  - no traceback.
- `/wialon/workload/export`:
  - direct passthrough XLSX response.
  - SQL count: 20 SELECT.
  - repeated SQL count: 1.
  - repeated equipment query count: 17.
  - DML count: 0.
  - no traceback.

Source diagnostic:

- `wialon_workload` route: wialon_import.py.
- `wialon_workload_export` route: wialon_import.py.
- workload builder: workload_report.py.
- Problem was repeated equipment loading inside `get_workload_data`.
- SQL issue was not in `/wialon/auto_match`.

Implementation:

- Added marker:
  - PERF-WIALON-WORKLOAD-001B_MARKER.
- Updated `get_workload_data` signature:
  - `get_workload_data(d_from, d_to, org_ids=None, preloaded_orgs=None)`.
- Added reuse of `preloaded_orgs`.
- Added one bulk equipment query:
  - `Equipment.organization_id.in_(org_ids_for_equipment)`.
- Added `equipment_by_org` grouping in memory.
- Removed per-organization:
  - `.filter_by(organization_id=org.id, is_active=True)`.
- Updated `/wialon/workload` call:
  - `get_workload_data(d_from, d_to, filter_org_ids, preloaded_orgs=user_orgs)`.
- Preserved workload export compatibility.

Staging validation:

- py_compile passed.
- git diff --check passed.
- app import OK.
- URL rules count: 86.
- Source checks passed.
- `/wialon/workload`:
  - response UTF-8 bytes: 230,785.
  - SQL count: 4 SELECT.
  - repeated SQL count: 0.
  - repeated equipment SQL: 0.
  - DML count: 0.
  - no traceback.
- `/wialon/workload/export`:
  - direct passthrough XLSX response.
  - SQL count: 4 SELECT.
  - repeated SQL count: 0.
  - repeated equipment SQL: 0.
  - DML count: 0.
  - no traceback.
- Regression routes passed:
  - /wialon/auto_match.
  - /wialon/mapping.
  - /wialon.
  - /wialon/report.
  - /ref/equipment.
- Staging post-restart smoke OK.

Production validation:

- Production pull scope:
  - workload_report.py.
  - wialon_import.py.
- Production source backup created:
  - D:\transport-report-backups\production\source\PERF_WIALON_WORKLOAD_001C_20260616_123713.
- Production pull fast-forward only.
- Production py_compile passed.
- Production source validation passed.
- `/wialon/workload`:
  - response UTF-8 bytes: 230,785.
  - SQL count: 4 SELECT.
  - repeated SQL count: 0.
  - repeated equipment SQL: 0.
  - DML count: 0.
  - no traceback.
- `/wialon/workload/export`:
  - direct passthrough XLSX response.
  - SQL count: 4 SELECT.
  - repeated SQL count: 0.
  - repeated equipment SQL: 0.
  - DML count: 0.
  - no traceback.
- Only TransportReport restarted.
- TransportBot and TransportBot003 were not restarted.
- Production smoke OK.

Final production services:

- TransportReport: RUNNING.
- TransportBot: RUNNING.
- TransportBot003: RUNNING.

## PERF-WIALON-MAPPING-BODY-002 Wialon mapping shared forms - 2026-06-16

Result: PASSED.

Scope:

- templates/wialon_mapping_list.html.
- Template-only change.
- No route code change.
- No DB schema changes.
- No migration.
- No POST requests during validation.
- No Telegram bot restart.

Baseline diagnostic:

- `/wialon/mapping`:
  - response UTF-8 bytes: 947,349.
  - SQL count: 3 SELECT.
  - repeated SQL count: 0.
  - DML count: 0.
  - no traceback.
  - mapping rows: 379.
  - pending rows: 3.
  - forms: 763.
  - inputs: 1,909.
  - selects: 384.
  - options: 387.
  - CSRF inputs: 763.
  - edit save forms: 379.
  - pending save forms: 3.
  - equipment selects: 384.

Implementation:

- Added marker:
  - PERF-WIALON-MAPPING-BODY-002B_MARKER.
- Added fix markers:
  - PERF-WIALON-MAPPING-BODY-002B_FIX_MARKER.
  - PERF-WIALON-MAPPING-BODY-002B_FIX2_MARKER.
- Replaced repeated row save forms with one shared edit form.
- Replaced repeated row delete forms with one shared delete form.
- Preserved pending save forms.
- Preserved manual add form.
- Preserved `wialon_mapping_save`.
- Preserved `wialon_mapping_delete`.
- Removed repeated rendered `data-search`.
- Removed repeated rendered `data-delete-url`.
- Added shared delete URL template.
- Search now uses row text cache.

Staging validation:

- py_compile passed.
- git diff --check passed.
- app import OK.
- URL rules count: 86.
- Source checks passed.
- `/wialon/mapping`:
  - response UTF-8 bytes: 633,834.
  - SQL count: 3 SELECT.
  - repeated SQL count: 0.
  - DML count: 0.
  - no traceback.
  - mapping rows: 379.
  - pending rows: 3.
  - forms: 7.
  - inputs: 18.
  - selects: 6.
  - options: 9.
  - CSRF inputs: 7.
  - old edit save forms: 0.
  - pending save forms: 3.
  - shared edit row: 1.
  - shared edit form: 1.
  - shared delete form: 1.
  - `data-search`: 0.
  - `data-delete-url`: 0.
  - visible edit buttons: 379.
  - visible skip buttons: 379.
  - visible delete buttons: 379.
- Regression routes passed:
  - /wialon/auto_match.
  - /wialon/workload.
  - /wialon.
  - /wialon/report.
  - /ref/equipment.
- Staging post-restart smoke OK.

Production validation:

- Production pull scope:
  - templates/wialon_mapping_list.html.
- Production source backup created:
  - D:\transport-report-backups\production\source\PERF_WIALON_MAPPING_BODY_002C_20260616_145746.
- Production pull fast-forward only.
- Production py_compile passed.
- Production source validation passed.
- `/wialon/mapping`:
  - response UTF-8 bytes: 633,834.
  - SQL count: 3 SELECT.
  - repeated SQL count: 0.
  - DML count: 0.
  - no traceback.
  - mapping rows: 379.
  - pending rows: 3.
  - forms: 7.
  - inputs: 18.
  - selects: 6.
  - options: 9.
  - CSRF inputs: 7.
  - old edit save forms: 0.
  - pending save forms: 3.
  - shared edit row: 1.
  - shared edit form: 1.
  - shared delete form: 1.
  - `data-search`: 0.
  - `data-delete-url`: 0.
  - visible edit buttons: 379.
  - visible skip buttons: 379.
  - visible delete buttons: 379.
- Only TransportReport restarted.
- TransportBot and TransportBot003 were not restarted.
- Production smoke OK.

Final production services:

- TransportReport: RUNNING.
- TransportBot: RUNNING.
- TransportBot003: RUNNING.

## PERF-WORK-TYPES-BODY-001 Reference work types shared forms - 2026-06-16

Result: PASSED.

Scope:

- templates/ref_work_types.html.
- Template-only change.
- No route code change.
- No DB schema changes.
- No migration.
- No POST requests during validation.
- No Telegram bot restart.

Baseline diagnostic:

- `/ref/work_types`:
  - response UTF-8 bytes: 266,902.
  - SQL count: 2 SELECT.
  - repeated SQL count: 0.
  - DML count: 0.
  - no traceback.
  - table rows: 209.
  - forms: 111.
  - inputs: 530.
  - selects: 1.
  - options: 3.
  - CSRF inputs: 110.
  - hidden id inputs: 104.
  - display:none count: 106.

Implementation:

- Added marker:
  - PERF-WORK-TYPES-BODY-001B_MARKER.
- Replaced repeated hidden inline edit rows with one shared edit row.
- Replaced repeated delete forms with one shared delete form.
- Preserved filter form.
- Preserved add-new form.
- Preserved `save_work_type`.
- Preserved `delete_work_type`.
- Added shared delete URL template.
- Added row metadata:
  - data-wt-id.
  - data-name.
  - data-default-unit.
  - data-default-price.

Staging validation:

- py_compile passed.
- git diff --check passed.
- app import OK.
- URL rules count: 86.
- Source checks passed.
- `/ref/work_types`:
  - response UTF-8 bytes: 127,204.
  - SQL count: 2 SELECT.
  - repeated SQL count: 0.
  - DML count: 0.
  - no traceback.
  - table rows: 107.
  - forms: 5.
  - inputs: 12.
  - selects: 1.
  - options: 3.
  - CSRF inputs: 4.
  - hidden id inputs: 1.
  - old inline edit rows: 0.
  - shared edit row: 1.
  - shared edit form: 1.
  - shared delete form: 1.
  - data-wt-id count: 105.
  - data-name count: 104.
- Regression routes passed:
  - /ref/equipment.
  - /wialon/mapping.
  - /wialon/workload.
  - /fuel/.
  - /spare-parts/.
- Staging post-restart smoke OK.

Production validation:

- Production pull scope:
  - templates/ref_work_types.html.
- Production source backup created:
  - D:\transport-report-backups\production\source\PERF_WORK_TYPES_BODY_001C_20260616_154849.
- Production pull fast-forward only.
- Production py_compile passed.
- Production source validation passed.
- `/ref/work_types`:
  - response UTF-8 bytes: 127,209.
  - SQL count: 2 SELECT.
  - repeated SQL count: 0.
  - DML count: 0.
  - no traceback.
  - table rows: 107.
  - forms: 5.
  - inputs: 12.
  - selects: 1.
  - options: 3.
  - CSRF inputs: 4.
  - hidden id inputs: 1.
  - old inline edit rows: 0.
  - shared edit row: 1.
  - shared edit form: 1.
  - shared delete form: 1.
  - data-wt-id count: 105.
  - data-name count: 104.
- Only TransportReport restarted.
- TransportBot and TransportBot003 were not restarted.
- Production smoke OK.

Final production services:

- TransportReport: RUNNING.
- TransportBot: RUNNING.
- TransportBot003: RUNNING.

## PERF-FUEL-STATIONS-NPLUS1-001 Fuel stations transaction counts optimization - 2026-06-16

Result: PASSED.

Scope:

- fuel_routes.py.
- templates/fuel/stations.html.
- Source-only change.
- No DB schema changes.
- No migration.
- No POST requests during validation.
- No Telegram bot restart.

Baseline diagnostic:

- `/fuel/`:
  - SQL count: 11 SELECT.
  - repeated SQL count: 0.
  - DML count: 0.
  - no traceback.
  - no patch justified for dashboard itself.
- `/fuel/stations`:
  - response UTF-8 bytes: 60,440.
  - SQL count: 44 SELECT.
  - SQL unique count: 4.
  - repeated SQL count: 2.
  - repeated transaction count queries: 21 + 21.
  - DML count: 0.
  - no traceback.
  - station rows: 21.
  - forms: 23.
  - inputs: 28.
  - CSRF inputs: 23.

Implementation:

- Added route marker:
  - PERF-FUEL-STATIONS-NPLUS1-001B_MARKER: bulk transaction counts for fuel stations.
- Added template marker:
  - PERF-FUEL-STATIONS-NPLUS1-001B_MARKER: use preloaded transaction counts.
- Added one grouped transaction count query:
  - FuelTransaction2.station_id.
  - func.count(FuelTransaction2.id).
  - station_id IN station_ids.
  - GROUP BY station_id.
- Reused counts through `station_delete_info`.
- Replaced `st.transactions.count()` in template with preloaded count.
- Preserved:
  - save_station.
  - delete_station.
  - enable_station.
  - station delete/deactivate protection.

Staging validation:

- py_compile passed.
- git diff --check passed.
- app import OK.
- URL rules count: 86.
- Source checks passed.
- `/fuel/stations`:
  - response UTF-8 bytes: 60,449.
  - SQL count: 3 SELECT.
  - SQL unique count: 3.
  - repeated SQL count: 0.
  - DML count: 0.
  - no traceback.
  - station rows: 21.
  - old `st.transactions.count()` mentions: 0.
- Regression routes checked:
  - /fuel/.
  - /fuel/warehouses.
  - /fuel/transactions.
  - /fuel/report.
  - /ref/work_types.
  - /wialon/mapping.
  - /spare-parts/.
- Staging post-restart smoke OK.

Production validation:

- Production pull scope:
  - fuel_routes.py.
  - templates/fuel/stations.html.
- Production source backup created:
  - D:\transport-report-backups\production\source\PERF_FUEL_STATIONS_NPLUS1_001C_20260616_165855.
- Production pull fast-forward only.
- Production py_compile passed.
- Production source validation passed.
- `/fuel/stations`:
  - response UTF-8 bytes: 60,449.
  - SQL count: 3 SELECT.
  - SQL unique count: 3.
  - repeated SQL count: 0.
  - DML count: 0.
  - no traceback.
  - station rows: 21.
  - old `st.transactions.count()` mentions: 0.
- Only TransportReport restarted.
- TransportBot and TransportBot003 were not restarted.
- Production smoke OK.

Final production services:

- TransportReport: RUNNING.
- TransportBot: RUNNING.
- TransportBot003: RUNNING.

