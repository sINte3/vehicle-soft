# RELEASE_UX001B_WIALON_MAPPING_UX_20260604 - Wialon mapping UX

Task: UX001B
Status: COMPLETED on production
Production URL: http://10.103.25.14:5050
Server: srv-yoqsh
Service: TransportReport
Database migration: not required

## Scope

Improved Wialon mapping and auto-match user experience without changing database schema or business rules.

## Changes

- Added Wialon mapping counters: total, linked, not in system, and pending decisions.
- Added search on the mapping list by Wialon object, equipment model, plate number, and organization.
- Added mapping status filters.
- Made mapping statuses clearer: linked, not in system, pending decision.
- Added a separate pending Wialon objects area.
- Improved manual mapping action layout.
- Improved auto-match toolbar with search, filter, expand/collapse controls, and visible-row skip action.
- Added client-side duplicate equipment selection validation before bulk save.
- Added validation for unresolved visible auto-match rows before bulk save.
- Updated auto-match behavior: validation errors return to auto-match, successful save opens mapping list.
- Added RU/UZ translations for new Wialon mapping and auto-match UI elements.

## Files changed

- wialon_import.py
- translations.py
- templates/wialon_mapping_list.html
- templates/wialon_auto_match.html

## Production verification

- py_compile passed for core modules.
- Application import check passed: APP IMPORT OK.
- TransportReport service running after restart.
- Production database backup completed before deployment.
- Backup file: D:\transport-report-backups\production\daily\transport_20260604_112928.db
- UX001B production smoke test passed.
- Wialon mapping list opened successfully.
- Mapping counters verified.
- Mapping search and status filters verified.
- Manual mapping actions verified.
- Auto-match search/filter/expand/collapse controls verified.
- Duplicate equipment selection validation verified.
- Visible rows mark-as-not-in-system action verified.
- Correct bulk save and redirect to mapping list verified.
- Russian and Uzbek UI checked in browser.

## Notes

No database schema changes were made. No migration was required. Old production server 10.103.25.200 remains rollback-standby only and must not be started unless rollback is explicitly authorized.
