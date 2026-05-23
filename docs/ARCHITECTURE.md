# ARCHITECTURE.md — Vehicle Soft Architecture

## Runtime architecture

```text
Browser
  ↓ HTTP
Flask app (`app.py`)
  ├─ core routes: auth, dashboard, daily entry, reports, references, admin
  ├─ Wialon routes registered by `register_wialon_routes(...)`
  ├─ Fuel blueprint: `fuel_routes.py`, prefix `/fuel`
  └─ Spare parts blueprint: `spare_parts.py`, prefix `/spare-parts`
  ↓ SQLAlchemy
SQLite `instance/transport.db`
  ↓ openpyxl
Excel reports in `reports/`
```

## Main files

### `app.py`

Responsibilities:

- Flask app factory.
- Login/logout/profile/language switch.
- Role decorators: `admin_required`, `editor_required`.
- Dashboard and date range parsing.
- Daily entry form and save logic.
- Deficiencies.
- Report generation.
- Admin users and module permission UI.
- Reference directories.
- Blueprint/route registration.

Current concern: module permissions exist in data/UI but are not a central route guard.

### `models.py`

Core models:

- `User`
- `Organization`
- `Equipment`
- `WorkType`
- `Customer`
- `DailyRecord`
- `Deficiency`

Wialon models:

- `VialonMapping`
- `VialonImport`
- `EngineHoursRecord`

Fuel v1 legacy models:

- `FuelStation`
- `FuelTank`
- `FuelSnapshot`
- `FuelTransaction`
- `FuelSyncLog`

Fuel v2 models:

- `FuelWarehouse`
- `FuelStation2`
- `FuelInitialBalance`
- `FuelReceipt2`
- `FuelTransaction2`
- `FuelSyncLog2`

Module permission models:

- `AppModule`
- `UserModulePermission`

Spare parts models:

- `SparePart`
- `SparePartRequest`
- `SparePartRequestItem`

## Equipment categories

`models.py` defines 9 categories and groups them for reports:

```text
tractors  = mtz + qatnov + mini
yukori    = yukori + combine + special
transport = yuk_transport + motorcycle + passenger
```

`templates/daily_entry.html` displays all 9 categories.

## Wialon data flow

```text
Wialon ZIP/CSV
  ↓ upload `/wialon/upload`
_extract_moto_csv()
  ↓
parse_moto_csv()
  ↓
apply_mappings()
  ↓
save_engine_hours()
  ↓
engine_hours_records
  ↓
workload_report.py + /wialon/workload
```

Important parsing rule:

- Wialon duration may be `HH:MM:SS` or `N день/дня/дней HH:MM:SS`.

## Fuel data flow

```text
Topaz agent
  ↓ JSON POST with token
/fuel/api/fuel_sync
  ↓
FuelStation2.topaz_id lookup
  ↓
FuelTransaction2 insert/dedup
  ↓
warehouse balance = initial + receipts − transactions
```

Important ambiguity:

- Earlier project instructions mention `/api/fuel_sync`, but the current blueprint prefix makes the actual route `/fuel/api/fuel_sync`.

## Excel reports

### Main report

- File: `excel_export.py`.
- Output: `Hisobot_DD_MM_YYYY.xlsx` or date range variant.
- Dynamic categories.
- Includes deficiencies.

### Daily activity report

- File: `excel_daily_activity.py`.
- Output: `Kunlik_ish_DD_MM_YYYY.xlsx`.

### Wialon workload report

- File: `workload_report.py`.
- Route: `/wialon/workload`.
- Export: `/wialon/workload/export`.
- Norm: calendar days × 8 moto-hours.

## Authentication and authorization

Current roles:

- `admin`
- `operator`
- `viewer`

Core checks:

- `admin_required`: admin only.
- `editor_required`: admin/operator.
- Organization access through `current_user.can_access_org(org_id)`.

Module permissions exist but are not currently enforced centrally.

## Deployment model

Typical update procedure:

```cmd
cd C:\transport-report
.\nssm.exe stop TransportReport
copy /Y <changed files> <target files>
"C:\Program Files\Python314\python.exe" <migration_script.py>
.\nssm.exe start TransportReport
```

For SQLite-writing migrations, service must be stopped first.
