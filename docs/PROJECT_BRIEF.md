# PROJECT_BRIEF.md — Vehicle Soft

## Purpose

Vehicle Soft automates daily transport and equipment reporting for Bukhoro Agroklaster. The original manual workflow was daily Excel copying, clearing, data entry and management reporting. The application now centralizes reference data, daily work records, Wialon moto-hours imports, fuel station monitoring and Excel report generation.

## Primary users

- Head office operator: maintains reference data, enters and checks daily work, exports reports.
- Branch/operator users: may eventually enter data for assigned organizations and submit for approval.
- Viewer users: read-only reporting access.
- Admin: user management, organization access, module permissions and system configuration.

## Technical environment

- Backend: Python 3.14, Flask, Flask-SQLAlchemy, Flask-Login.
- Database: SQLite in production on Windows Server.
- Frontend: Jinja2 templates, vanilla JavaScript, CSS.
- Reports: Excel generation with `openpyxl`.
- Runtime: Waitress/NSSM Windows service.
- Server path: `C:\transport-report\`.

## Business scope implemented

### Transport daily reporting

- Organizations, equipment, work types, customers.
- Daily record entry by date and organization.
- Multiple work lines per equipment per day.
- Payment types: cash, transfer, internal, other.
- Idle status and deficiency tracking.
- Main dashboard with date range filters.
- Excel report generation.

### Equipment structure

The current production database has 336 equipment records across 9 categories:

1. `yukori` — Юқори унумли техникалар.
2. `mtz` — Чопиқ тракторлар.
3. `qatnov` — Қатнов тракторлар.
4. `mini` — Мини тракторлар.
5. `combine` — Комбайнлар.
6. `special` — Махсус техникалар.
7. `yuk_transport` — Юк ташувчи техникалар.
8. `motorcycle` — Мотоцикл.
9. `passenger` — Йўловчи ташиш техникаси.

The main Excel report keeps business grouping but uses the expanded categories internally.

### Wialon

- Wialon ZIP/CSV import.
- Mapping between Wialon vehicle names and application equipment.
- Moto-hours records stored in `engine_hours_records`.
- Workload report `/wialon/workload` and Excel export.
- Parser supports Wialon duration values with Russian day words such as `1 день 17:36:07`.

### Fuel / Topaz

- Fuel warehouses correspond to organizations.
- Fuel stations map Topaz IDs to warehouses.
- Fuel transactions are stored in `fuel_transactions2`.
- Fuel balance = initial balance + receipts − Topaz transactions.
- Historical fuel v1 tables still exist and should not be removed without a controlled migration.

### Spare parts

- Early request module exists: requests, request items, catalog, submit, approve and reject flows.
- Full approval workflow, applicant-only role, notifications, installation tracking and anomaly detection are not complete.

## Business goals

### MVP

- Reliable daily reporting.
- Accurate Excel output.
- Wialon import and workload reports.
- Reference data aligned with inventory.
- Admin/user access controls stable enough for production.

### Basic ERP

- Branch submission workflow.
- Full spare parts request and approval lifecycle.
- Fuel accounting dashboard and reconciliation.
- Better role/module isolation.
- Operational audit logs.

### Full ERP

- Finance/cash/receivables after formal accounting rules.
- Telegram notifications and quick actions.
- PostgreSQL migration.
- Linux/Nginx/HTTPS public deployment if required.
- Strong audit trail, backups and monitoring.

## Key constraints

- User is not a programmer; deployment instructions must be copy-paste ready.
- Windows Server/NSSM compatibility is mandatory.
- Existing data must be preserved.
- SQLite locking must be respected during migrations.
- Approved Excel report layouts must not be casually changed.
