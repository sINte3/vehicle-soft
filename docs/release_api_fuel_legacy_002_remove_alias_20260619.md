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

## API-FUEL-LEGACY-002 / API-FUEL-LEGACY-009 — Final completion

Date: 2026-06-20

Final status: completed.

What was done:
- Removed temporary legacy `POST /api/fuel_sync` alias.
- Kept canonical `POST /fuel/api/fuel_sync`.
- Removed `/api/fuel_sync` from CSRF exemption list.
- Kept `FUEL_API_TOKEN` validation unchanged.
- Rolled out to staging first, then production.
- Verified that both staging and production are on commit `9dd034e`.

Validation:
- `GET /api/fuel_sync` returns 404.
- `GET /fuel/api/fuel_sync` returns 405.
- `POST /fuel/api/fuel_sync` with invalid token returns 401.
- `/api/bot/health` returns 200.
- Production Topaz sync after rollout exists:
  - sync id: 2525
  - synced_at: 2026-06-20 03:33:02.704120
  - agent_ip: 10.103.40.140
  - received: 4
  - new: 4
  - status: ok
  - error: empty

Conclusion:
- The real Topaz agent is working through the canonical `/fuel/api/fuel_sync` endpoint.
- The legacy `/api/fuel_sync` alias has been removed safely.
- API-FUEL-LEGACY-002 is closed.
