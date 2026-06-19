# RELEASE API-FUEL-LEGACY-002 — Remove legacy fuel sync alias

Date: 2026-06-19
Scope: staging first

## Summary

Removed the temporary app-level legacy fuel sync alias:

- removed POST /api/fuel_sync
- kept canonical POST /fuel/api/fuel_sync
- kept token protection through FUEL_API_TOKEN
- removed /api/fuel_sync from CSRF exemption list

## Evidence before removal

- Real Topaz sync source IP is 10.103.40.140.
- Production uel_sync_logs2 has fresh successful rows on 2026-06-19.
- Production sync status is healthy: recent rows have status=ok.
- No production warning was found for Topaz agent used deprecated endpoint /api/fuel_sync.
- Staging probe confirmed the warning is actually logged when /api/fuel_sync is called.
- Therefore, absence of the warning in production is meaningful evidence that the real Topaz agent is not using the old endpoint.

## Expected behavior after change

- GET /api/fuel_sync returns 404.
- POST /api/fuel_sync is no longer a registered Flask route.
- GET /fuel/api/fuel_sync returns 405.
- POST /fuel/api/fuel_sync with an invalid token returns 401.
- Valid Topaz sync continues through POST /fuel/api/fuel_sync.

## Rollback

Restore pp.py from the source backup created before this change, or revert this commit, then restart the affected Flask service.

## Status

Staging implementation completed in API-FUEL-LEGACY-006B pending production rollout.
