# ROADMAP.md — Vehicle Soft ERP Roadmap

## Stage 1 — MVP Stabilization

Goal: make the current production app safer and more maintainable.

Features:

- Enforce module permissions in routes.
- Move secrets and API tokens out of source.
- Standardize Topaz API URL.
- Add migration registry.
- Finish translation gaps.
- Verify all 9 equipment categories in entry, reports and references.
- Improve backup/update instructions.

Complexity: 2–3 / 5.

Dependencies:

- Current production archive.
- Admin approval for token/env setup.

## Stage 2 — Basic ERP

Goal: move from reporting app to controlled operational workflow.

Features:

- Branch submission workflow:
  - branch operator enters data;
  - submits to head office;
  - head office approves/rejects/returns.
- Spare parts workflow:
  - applicant-only role;
  - request statuses;
  - review comments;
  - admin notifications;
  - installation history;
  - anomaly detection.
- Fuel dashboard stabilization:
  - Topaz agent monitoring;
  - failed sync alerts;
  - warehouse reconciliation.
- Better reference exports and filters.

Complexity: 3–4 / 5.

Dependencies:

- Stable module permission system.
- Defined spare part approvers.
- Telegram bot token/chat IDs if notifications are required.

## Stage 3 — Full ERP

Goal: turn Vehicle Soft into a broader operational/financial platform.

Features:

- Finance module:
  - cash transactions;
  - receivables;
  - partial payments;
  - internal transfers;
  - audit trail.
- Telegram bot:
  - approvals;
  - notifications;
  - summary reports.
- PostgreSQL migration.
- Linux/Nginx/HTTPS public deployment if needed.
- Central audit log.
- Scheduled backups and monitoring.

Complexity: 5 / 5.

Dependencies:

- Formal accounting rules from finance.
- Server strategy decision.
- Security hardening.
- Backup/restore procedure.
