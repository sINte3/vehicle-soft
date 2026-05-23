# QA_CHECKLIST.md — Vehicle Soft Quality Checklist

## Before changing code

- Read project docs.
- Identify affected files.
- Identify whether DB schema or reference data changes.
- Decide whether service stop is required.
- Create backup instructions if production data is touched.

## Python syntax checks

Run:

```cmd
cd C:\transport-report
"C:\Program Files\Python314\python.exe" -m py_compile app.py models.py excel_export.py wialon_import.py workload_report.py fuel_routes.py spare_parts.py translations.py excel_daily_activity.py config.py run_server.py
```

For migration scripts, compile the exact script:

```cmd
"C:\Program Files\Python314\python.exe" -m py_compile migrate_name.py
```

## Database safety

Before schema/reference migration:

```cmd
cd C:\transport-report
.\nssm.exe stop TransportReport
copy instance\transport.db instance\transport.db.backup_before_update
```

After migration:

- Check migration output.
- Check row counts if relevant.
- Start service only after sanity checks.

## Smoke test after service start

Open:

- `/login`
- `/`
- `/entry`
- `/report`
- `/wialon`
- `/wialon/workload`
- `/fuel/`
- `/ref/equipment`
- `/admin/users` as admin

## Daily entry checklist

- Select date.
- Select organization.
- Confirm all 9 equipment categories display when that organization has equipment.
- Mark equipment as working.
- Add multiple work lines.
- Save.
- Reopen same date/org and confirm records persisted.
- Change date/org only after saving.

## Main Excel report checklist

- Generate one-day report.
- Generate date range report.
- Test organization filter.
- Test category filter if changed.
- Verify:
  - totals match web dashboard;
  - deficiencies sheet exists;
  - category sheets are correct;
  - file opens in Excel;
  - sheet names are under Excel limit.

## Wialon import checklist

- Import a ZIP with `Моточасы.csv`.
- Test duration values:
  - `17:01:00`;
  - `1 день 17:36:07`;
  - `2 дня 16:51:41`.
- Check unknown vehicles list.
- Check mapped vehicles saved to `engine_hours_records`.
- Check skipped vehicles are not saved.
- Check duplicate behavior for existing date/equipment.

## Workload report checklist

- Open `/wialon/workload`.
- Test day/week/month/range modes.
- Test organization filter.
- Export Excel.
- Verify:
  - norm = calendar days × 8;
  - fact = sum of engine hours;
  - zero-hour equipment is red;
  - organizations separated by empty row;
  - no unexpected subtotal rows if approved format says none.

## Fuel module checklist

- Open `/fuel/`.
- Check warehouse list.
- Check station mapping.
- Check initial balances.
- Check receipts.
- Check transactions.
- Test sync endpoint with known-safe sample data in a test copy first.
- Verify API URL used by Topaz agent.

## Authorization checklist

- Admin can access all modules.
- Operator can access only assigned organizations.
- Viewer cannot edit.
- User without module permission cannot open module route directly after TASK-SEC-001.

## Migration checklist (TASK-OPS-001+)

Before running any migration script:

- [ ] Service stopped (`.\nssm.exe stop TransportReport` or `net stop TransportReport`).
- [ ] Database backup taken and named with date (`transport.db.backup_YYYYMMDD`).
- [ ] Script syntax-checked: `py_compile migrate_NNN_name.py`.
- [ ] Script reviewed: idempotent body, no business table drops, rollback comment present.

Running the migration:

- [ ] Run one script at a time.
- [ ] Check output — no ERROR lines.
- [ ] Verify registry after running:
  ```cmd
  "C:\Program Files\Python314\python.exe" -c "import sqlite3; c=sqlite3.connect('instance/transport.db'); [print(r) for r in c.execute('SELECT id, name, applied_at FROM schema_migrations ORDER BY id')]; c.close()"
  ```
- [ ] Start service only after output looks correct.

## Release package checklist

Every release must include:

- Changed files.
- Migration scripts if needed.
- README/update instructions.
- Rollback instructions.
- Syntax check result.
- Manual test checklist.
