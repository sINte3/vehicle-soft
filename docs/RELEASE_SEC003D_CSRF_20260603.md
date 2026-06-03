# RELEASE_SEC003D_CSRF_20260603 - CSRF protection

Task: TASK-SEC-003D
Status: COMPLETED on production
Production URL: http://10.103.25.14:5050
Server: srv-yoqsh
Service: TransportReport
Database migration: not required

## Scope

Added CSRF protection for browser POST forms.

## Changes

- Added server-side CSRF token generation.
- Added server-side CSRF validation for POST requests.
- Added csrf_token() Jinja global for templates.
- Added hidden csrf_token fields to browser forms.
- Kept token-auth Topaz API endpoints excluded from CSRF.

## CSRF exclusions

The following API endpoints remain excluded from CSRF and continue to use token authentication:

- /fuel/api/fuel_sync
- /api/fuel_sync

## Files changed

- app.py
- templates/*.html
- templates/fuel/*.html

## Production verification

- py_compile passed for core modules.
- Application import check passed: APP IMPORT OK.
- TransportReport service running after restart.
- Production database backup created successfully before deployment.
- Backup file: D:\transport-report-backups\production\daily\transport_20260603_095235.db
- Backup integrity_check: ok.
- Topaz ping verified on production: /fuel/api/fuel_ping returned ok.
- Production CSRF smoke test passed: login/logout, daily report save, reference save, Wialon mapping save, Fuel warehouse save, spare parts request creation, and admin audit page.

## Notes

No database schema changes were made. No migration was required. Old production server 10.103.25.200 remains rollback-standby only and must not be started unless rollback is explicitly authorized.
