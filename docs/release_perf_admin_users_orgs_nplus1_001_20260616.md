# Release: PERF-ADMIN-USERS-ORGS-NPLUS1-001  Admin Users Organizations N+1 Optimization

Date: 2026-06-16  
Type: performance optimization  
Risk: low  
Scope: `/admin/users`

## Summary

Optimized `/admin/users` by removing repeated lazy-load queries for user organizations.

## Problem

The route loaded users with:

`User.query.order_by(User.username).all()`

The template then accessed organizations for each user. That triggered repeated lazy-load queries through `user_organizations`.

## Before

Staging and production diagnostics showed:

- status: 200
- SQL total: 12
- repeated SQL kinds: 1
- organization repeated total: 7
- non-select statements: 0

## Fix

Updated `/admin/users` query to eager-load organizations:

`selectinload(User.organizations)`

Also added:

`from sqlalchemy.orm import selectinload`

## Files Changed

- `app.py`

## Commit

- `2216514 optimize admin users organization loading`

## After

Staging after patch:

- status: 200
- SQL total: 6
- repeated SQL kinds: 0
- organization repeated total: 0
- `user_organizations` query count: 1
- non-select statements: 0

Production reconciliation diagnostics passed:

- repeated SQL kinds: 0
- non-select statements: 0

## Regression Checks

The following admin routes remained clean:

- `/admin/permissions`
- `/admin/audit`

## Deployment

- Staging deployed and restarted:
  - `transportreportstaging`
- Production deployed and restarted:
  - `transportreport`

Bot services were not restarted.

## Final Status

DONE  deployed to staging and production.
