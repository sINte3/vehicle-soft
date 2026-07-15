# RELEASE SPARE-PARTS-P0-RELEASE-BLOCKERS — 2026-07-15

Closes the four release blockers from the second Codex Sol re-audit of the
merged SPARE-PARTS-FULL remediation (staging `6e23d53`). One branch, four
ordered commits: RE-SP-002, RE-SP-011, RE-SP-009, RE-SP-001.

## Changed files

| Commit | Files |
|---|---|
| RE-SP-002 | `spare_parts.py` (sku_save race handler), `tests/harness.py`, `tests/test_resp002_sku_race.py`, `tests/__init__.py` |
| RE-SP-011 | `migrate_spare_parts_acts_permission.py`, `migrate_spare_parts_sku_uniqueness.py`, `tests/test_resp011_migration_hardening.py` |
| RE-SP-009 | `spare_parts.py` (`_active_units` fallback), `migrate_spare_parts_units.py` (docs only), `tests/test_resp009_units_fallback.py` |
| RE-SP-001 | `spare_parts_attachment_reconcile.py` (new), `spare_parts.py` + `templates/spare_part_detail.html` (missing-file status), `tests/test_resp001_attachment_reconcile.py`, this document |

No schema change. No new Python dependency. No production deployment in
this task.

## Verification performed (disposable environments only)

- `python -m py_compile` on every changed `.py` file: PASS.
- `import app` check: PASS.
- `python -m unittest discover -s tests` — 28 tests, all PASS, all against
  a throwaway SQLite database and temp directories (never
  `instance\transport.db`, never the real uploads folder).

## Staging smoke instructions (Windows CMD)

Read-only attachment reconciliation report — safe to run any time, does
not require stopping the service (output goes OUTSIDE the project folder):

```cmd
cd C:\transport-report
"C:\Program Files\Python314\python.exe" spare_parts_attachment_reconcile.py --db instance\transport.db --uploads instance\uploads\spare_parts --out C:\reconcile\attachment_report.json
```

Migration hardening self-check on a disposable DB (never the real one):

```cmd
cd C:\transport-report
"C:\Program Files\Python314\python.exe" -m unittest tests.test_resp011_migration_hardening -v
```

Manual checks after deploying this code to staging:

1. SKU form: try saving an exact duplicate SKU twice — friendly bilingual
   message both times, no error page.
2. Request detail page of a request whose attachment file is present —
   unchanged. (On staging, request #4 currently has a missing file: the
   page must show «Файл недоступен / Файл мавжуд эмас» instead of a broken
   image; the DB row stays.)
3. `/spare-parts/new` and `/spare-parts/catalog` still load normally.

## Units deploy/rollback safe order (RE-SP-009, authoritative)

- **Deploy:** run `migrate_spare_parts_units.py` **before** starting code
  that queries `SparePartUnit`.
- **Rollback:** start code that no longer queries `SparePartUnit`
  **before** `DROP TABLE spare_part_units`. The narrow "no such table"
  fallback in `_active_units()` is a transitional safety net only; steady
  state is table present, directory authoritative.

## Rollback of this release

Each commit is independently revertable (`git revert <sha>`); Part 4 is
additive (one new script, one display-only template/route change; the
`_quarantine` and `_reconcile` directories are created only by an explicit
`--apply` run). No migration to undo.

## Honest closure status

- **RE-SP-002, RE-SP-011, RE-SP-009:** code fixes delivered and tested.
- **RE-SP-001:** the *tooling and controlled process* are delivered
  (read-only report, missing-file status surfacing, manifest-driven
  non-destructive apply: restore-from-verified-backup / move-to-quarantine /
  retain-in-registry — **no delete path exists**). The current staging data
  condition (1 missing referenced file on issued request 4, 12 orphan
  files) is **NOT repaired by merging this PR**. It remains until the owner
  reviews a reconciliation report and approves a specific manifest;
  production has 0 missing / 0 orphans and needs nothing. After staging
  validation and the owner's manifest decision, a **targeted** Codex
  re-audit of these four areas only is the agreed next step.
