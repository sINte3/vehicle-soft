# RELEASE FUEL-REPORT-011: Fuel Balance Report, Manual Expenses, Topaz Backfill

Date: 2026-06-22
Project: Vehicle Soft / transport-report
Environment: production and staging
Final application commit: c5f898b add manual fuel expenses to balance report

## Summary

This release completed the fuel balance report workflow.

Added:
- fuel balance report page
- Excel export for the fuel balance report
- dashboard link to the report
- manual fuel expense support
- May 2026 manual expense correction for Варахшо чул
- June 2026 Topaz CSV backfill for missing imported transactions
- production QA for May 2026 and June 01-18, 2026

## Source of truth rule

Topaz PAK Автономный налив and 1C reports are the source of truth for fuel expense reconciliation.
If a manually prepared Excel file conflicts with Topaz/1C, the manual Excel values are not accepted.

## Included application commits

Production was updated from:
3d1f691 clarify fuel legacy warehouse dashboard mode

to:
c5f898b add manual fuel expenses to balance report

Included commits:
- bad13a8 add fuel balance period report
- d08c76a improve fuel balance report layout
- c5f898b add manual fuel expenses to balance report

## Database changes

New table: fuel_manual_expenses
Purpose: store auditable manual fuel expenses issued outside Topaz columns but included in warehouse balance calculations.

## Manual expense inserted

Warehouse: Варахшо чул
- 2026-05-07: 1579.00 l
- 2026-05-08: 1035.00 l
- Total: 2614.00 l

Reason: fuel was issued manually and was not issued through a Topaz column.

## Topaz CSV backfill

Backfilled period: 2026-06-01 through 2026-06-18

Backfilled Topaz IDs:
- 934451: Бензовоз Исузу / Исузу 259
- 895101: Вобкент ПТМ
- 935491: Мирзачул ПТЗ

Backfill result:

Бензовоз Исузу / 934451:
- before: 0.00 l
- created: 234 transactions / 16449.04 l
- after: 16449.04 l

Вобкент ПТМ / 895101:
- before: 34268.78 l
- created: 11 transactions / 2718.89 l
- after: 36987.67 l

Мирзачул ПТЗ / 935491:
- before: 24589.79 l
- created: 5 transactions / 2824.17 l
- after: 27413.96 l

Total backfilled fuel: 21992.10 l

## Validated production totals

May 2026:
- Opening balance: 67472.00 l
- Receipts: 257233.00 l
- Expenses: 206418.22 l
- Closing balance: 118286.78 l

June 01-18, 2026:
- Opening balance: 118286.78 l
- Receipts: 107426.00 l
- Expenses: 166236.06 l
- Closing balance: 59476.72 l

User visually confirmed production June expense: 166236.06 l

## Production QA

Passed:
- py_compile OK
- APP_IMPORT_OK
- /fuel/ 200
- /fuel/balance-report May 2026 200
- /fuel/balance-report/export May 2026 200
- /fuel/balance-report June 01-18 200
- /fuel/balance-report/export June 01-18 200

Services after deployment:
- TransportReport RUNNING
- TransportReportStaging RUNNING
- TransportBot RUNNING
- TransportBotStaging RUNNING
- TransportBot003 RUNNING
- TransportBot003Staging RUNNING

## Production backups and audit paths

Production DB backup:
D:\transport-report-backups\production\daily\fuel_report_011j_production_db_before_report_deploy_backfill_20260622_171746.db

Production audit:
D:\transport-report-backups\production\audits\fuel_report_011j_production_deploy_db_backfill_20260622_171746

Production console log:
D:\transport-report-backups\production\audits\fuel_report_011j_production_deploy_db_backfill_20260622_171746\fuel_report_011j_console_log.txt

Production source backup:
D:\transport-report-backups\production\audits\fuel_report_011j_production_deploy_db_backfill_20260622_171746\source_before_deploy

Production CSV archive:
D:\transport-report-backups\production\manual_imports\topaz_20260601_20260618

## Operational notes

1. Reconcile fuel expenses against Topaz and 1C.
2. Do not accept manual Excel values if they conflict with Topaz/1C.
3. Manual expenses must be entered explicitly and auditable.
4. If Topaz/1C show higher расход than Vehicle Soft, first check whether Topaz operations were imported into fuel_transactions2.
5. CSV backfill was used only to correct confirmed missing historical imports.
6. Future import reliability should be checked for changed Topaz IDs, especially old 812301 and new 934451.

## Status

Status: COMPLETED
Production: OK
Staging: OK
Final application commit: c5f898b
