# REPORT001E-1 — Fuel warning registry

Date: 2026-06-05  
Status: COMPLETED  
Production: http://10.103.25.14:5050  
Database migration: REQUIRED and completed  
Production backup before deployment: `D:\transport-report-backups\production\daily\transport_20260605_115535.db`  
Production smoke test: passed  
Commit message: `Add Fuel warning registry`

## Summary

REPORT001E-1 adds a managed registry for Fuel report warnings. The previous REPORT001D release calculated and displayed warnings in `/fuel/report`; this release makes those warnings actionable and persistent.

## Changed files

- `models.py`
- `fuel_routes.py`
- `templates/fuel/dashboard.html`
- `templates/fuel/report.html`
- `templates/fuel/warnings.html`

## Database changes

New table:

- `fuel_warning_reviews`

Purpose:

- store stable warning keys;
- keep review status;
- keep responsible comment;
- keep first/last seen timestamps;
- keep reviewer and resolution timestamps.

Production validation:

- `PRAGMA integrity_check`: ok
- `fuel_warning_reviews` table exists
- application import passed after migration

## Functional scope

- Added `/fuel/warnings` page.
- Added warning status management:
  - new;
  - in progress;
  - checked;
  - rejected.
- Added responsible comment storage.
- Added warning filters:
  - period;
  - status;
  - severity;
  - warning type;
  - search.
- Added link from Fuel dashboard to warning registry.
- Added link from `/fuel/report` to warning registry.
- Added warning review status display inside `/fuel/report`.
- Added audit logging for warning review actions.

## Audit actions

Expected audit actions:

- `fuel_warning_review_created`
- `fuel_warning_review_updated`

## Verification

Production checks completed:

- production DB backup completed successfully;
- backup integrity check passed;
- code compiled successfully;
- `APP IMPORT OK`;
- migration script completed successfully;
- `fuel_warning_reviews` table verified;
- `/fuel/warnings` returned HTTP 200;
- `/fuel/report` returned HTTP 200;
- Windows service `TransportReport` restarted and is running;
- browser smoke test passed.

## Notes

This release changes the database schema. Rollback requires restoring the pre-release backup or leaving the new table unused while rolling back the code files.
