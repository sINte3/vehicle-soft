# RELEASE SPARE001D  Spare parts role/access audit and permission enablement

Date: 2026-06-15

## Status

Completed on staging and production.

## Base commit before docs

`4ebc43479af9819aae0a2ef3e71562770cd54240`  `Document SPARE001C staging workflow test`

## Scope

SPARE001D covered read-only access audit and DB permission enablement for the spare parts module.

No source code was changed.

No schema changes were made.

No migrations were added.

No service restart was performed.

## Audit finding

The spare parts module was technically working, but active operators had no module-level access.

Affected active operators:

- `muhiddin`  Шукуров Мухиддин
- `abdugani`  Акрамов Абдугани
- `mirfayz`  Джураев Мирфайз
- `sardor`  Ёдгоров Сардор

Before the fix:

- `is_active_user=1`
- `role=operator`
- organization assignments existed
- `user_module_permissions.spare_parts=0`
- `/spare-parts/*` returned `403 Forbidden`

Admin access was already valid.

## Staging audit

Staging read-only audit confirmed:

- admin could access spare parts list, creation page, catalog and details
- active operators had assigned organizations but no `spare_parts` access
- active operators received `403 Forbidden` on:
  - `/spare-parts/`
  - `/spare-parts/new`
  - `/spare-parts/catalog`
  - `/spare-parts/<id>`
- unauthenticated users were redirected to login
- no DB writes were performed during audit
- no POST requests were executed
- no service restart was performed

## Production audit

Production read-only audit confirmed the same condition:

- active operators had `spare_parts_access=0`
- active operators had 17 assigned organizations
- active operators received `403 Forbidden` on spare parts routes
- admin access was valid
- no DB writes were performed during audit
- no POST requests were executed
- no service restart was performed

## Staging permission update

Backup created before staging DB update:

`D:\transport-report-backups\staging\daily\transport_spare001d3_enable_spare_parts_active_ops_20260615_141345.db`

The staging permission update enabled `spare_parts` access for active operators:

- user ID 4: `muhiddin`
- user ID 5: `abdugani`
- user ID 6: `mirfayz`
- user ID 7: `sardor`

A repeated idempotent staging run was also performed and created an additional backup:

`D:\transport-report-backups\staging\daily\transport_spare001d3_enable_spare_parts_active_ops_20260615_141354.db`

Final staging result:

- all active operators have `spare_parts_access=1`
- `/spare-parts/` opens
- `/spare-parts/new` opens
- `/spare-parts/<id>` opens for assigned organizations
- `/spare-parts/catalog` remains `403 Forbidden` for operators
- validation errors: 0
- services remained RUNNING
- no service restart was performed

## Production permission update

Backup created before production DB update:

`D:\transport-report-backups\production\daily\transport_spare001d4_enable_spare_parts_active_ops_20260615_141801.db`

The production permission update enabled `spare_parts` access for active operators:

- user ID 4: `muhiddin`
- user ID 5: `abdugani`
- user ID 6: `mirfayz`
- user ID 7: `sardor`

Final production result:

- all active operators have `spare_parts_access=1`
- `/spare-parts/` opens
- `/spare-parts/new` opens
- `/spare-parts/<id>` opens for assigned organizations
- `/spare-parts/catalog` remains `403 Forbidden` for operators
- validation errors: 0
- `TransportReport`, `TransportBot`, `TransportBot003` remained RUNNING
- no service restart was performed

## Intended access model after SPARE001D

Admin:

- can open spare parts list
- can create requests
- can open request details
- can approve/reject requests
- can open catalog
- can manage catalog

Operator:

- can open spare parts list
- can create requests
- can open request details for assigned organizations
- cannot open catalog
- cannot manage catalog
- cannot approve/reject requests unless future requirements explicitly change this

Unauthenticated user:

- redirected to login

## Final result

SPARE001D is complete.

The spare parts module is now available to active operators on staging and production while catalog management remains restricted to admin.
