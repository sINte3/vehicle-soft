# -*- coding: utf-8 -*-
"""drone_collector -- standalone DJI SmartFarm flight collector (DRONE-003).

This package is NOT part of the Flask application. It never imports `app`,
`models` or anything else that would trigger `db.create_all()`; it talks to
Vehicle Soft only over HTTP, through the token-authenticated ingest endpoint
POST /drones/api/flight_sync.

It is expected to run in its own virtual environment (see
drone_collector/requirements.txt), on its own schedule, under a real user
account -- Chromium is installed into a user profile and a service running as
LocalSystem cannot see it. Deployment is an owner-run step and is deliberately
not wired into the application's service configuration.
"""
