# RELEASE API-FUEL-LEGACY-001 - Fuel sync legacy alias audit

Date: 2026-06-15

## Status

Completed as docs-only decision closure.

No source changes were made.

## Base commit

2cb98a1cff176e459059946a7cf03b1b24102400

## Purpose

API-FUEL-LEGACY-001 reviewed the legacy Topaz fuel sync alias:

- /api/fuel_sync

and compared it with the canonical endpoint:

- /fuel/api/fuel_sync

The goal was to decide whether the old alias can be safely removed now or should remain temporarily.

## Read-only audit result

API-FUEL-LEGACY-001A confirmed:

- app import OK;
- URL rules count: 86;
- canonical endpoint exists:
  - /fuel/api/fuel_sync
  - endpoint: fuel.api_fuel_sync
  - method: POST
- legacy alias exists:
  - /api/fuel_sync
  - endpoint: api_fuel_sync_legacy
  - method: POST
- both endpoints call the same shared sync logic:
  - _perform_fuel_sync()
- CSRF exemption includes both endpoints:
  - /fuel/api/fuel_sync
  - /api/fuel_sync
- token comparison is already protected through hmac.compare_digest in the shared sync logic;
- safe GET smoke returned 405 for both POST-only sync endpoints;
- no tracebacks were found;
- no source files were modified;
- no DB writes were performed;
- no POST requests were executed;
- no service restart was performed;
- production was not touched.

## Findings

The legacy alias is still present.

This is not a production bug by itself because:

- it uses the same FUEL_API_TOKEN protected sync logic;
- it shares the same _perform_fuel_sync() implementation;
- it is covered by the same CSRF skip rule as the canonical sync endpoint;
- GET requests are rejected with 405.

However, it should be treated as deprecated because the canonical endpoint is:

- /fuel/api/fuel_sync

## Decision

Do not remove /api/fuel_sync yet.

Keep the legacy alias temporarily until the Topaz agent configuration is confirmed to use:

- /fuel/api/fuel_sync

After confirmation, create a separate removal task.

## Recommended future task

API-FUEL-LEGACY-002:

- confirm current Topaz agent endpoint configuration;
- if all agents use /fuel/api/fuel_sync, remove /api/fuel_sync alias;
- validate canonical sync only;
- update deployment notes and rollback instructions.

## Result

API-FUEL-LEGACY-001 is closed as a read-only audit and docs-only decision.

No code changes were required in this task.
