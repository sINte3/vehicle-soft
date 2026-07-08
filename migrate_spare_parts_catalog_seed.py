# -*- coding: utf-8 -*-
"""Migration SPARE_PARTS_CATALOG_SEED_V1 -- Spare parts starter catalog seed.

Seeds (from seed_data/spare_parts_catalog_v1.csv, 238 rows / 13 categories):
  - spare_part_categories: one row per unique
    (category_name_ru, category_name_uz, category_kind) in CSV order,
    inserted only if no category with that exact name_ru exists
  - spare_parts: one row per CSV line, inserted only if no part with that
    exact name AND category_id exists; status='active' (curated catalog,
    not pending_review), unit='dona' (model default -- the seed data does
    not specify units of measure)
  - one new app_modules permission row (INSERT OR IGNORE):
      spare_parts_reports

Safe / idempotent:
  - Every category/part insert is preceded by an existence check
    (INSERT OR IGNORE semantics; the tables have no UNIQUE constraint on
    these columns, so the check is explicit).
  - INSERT OR IGNORE for the app_modules seed row (code column is UNIQUE).
  - Registers itself in schema_migrations and skips on re-run.
  - Does not touch or delete any existing business data.
  - Any CSV row that cannot be parsed (wrong column count, empty field,
    unknown category_kind) ABORTS the whole migration with the row number
    -- nothing is ever silently skipped; the transaction rolls back.

Requires migrate_spare_parts_stage1.py to have run first (it creates
spare_part_categories and the spare_parts columns this seed fills).

[REASON]: This project's migration registry (migration_utils.py /
migrate_000_migration_registry.py) uses `id INTEGER PRIMARY KEY AUTOINCREMENT`
and registers by the `name` column with `applied_at` NOT NULL. This script
follows the proven name-based pattern used by migrate_spare_parts_stage1.py.

Run (service must be STOPPED first to avoid SQLite write conflicts):

  cd C:\\transport-report-staging
  .\\nssm.exe stop TransportReportStaging
  "C:\\Program Files\\Python314\\python.exe" migrate_spare_parts_catalog_seed.py
  .\\nssm.exe start TransportReportStaging

Rollback:
  This migration is purely additive (reference rows only; no schema change,
  no user is granted the new permission automatically).
  DELETE FROM spare_parts WHERE created_by IS NULL AND status = 'active'
    AND category_id IN (SELECT id FROM spare_part_categories
                        WHERE created_by IS NULL);
  DELETE FROM spare_part_categories WHERE created_by IS NULL;
  DELETE FROM app_modules WHERE code = 'spare_parts_reports';
  DELETE FROM schema_migrations WHERE name = 'SPARE_PARTS_CATALOG_SEED_V1';
  (Only safe BEFORE operators start linking request items to the seeded
   parts. After that, the safest rollback is the pre-migration DB backup.)
"""

import csv
import os
import sqlite3
import sys
from datetime import datetime

ROOT = os.path.dirname(os.path.abspath(__file__))
DB_PATH = os.path.join(ROOT, 'instance', 'transport.db')
# [REASON]: Path is resolved relative to this script's own directory so the
# migration works no matter which folder the operator runs it from.
CSV_PATH = os.path.join(ROOT, 'seed_data', 'spare_parts_catalog_v1.csv')
MIGRATION_ID = 'SPARE_PARTS_CATALOG_SEED_V1'

CSV_COLUMNS = ['category_name_ru', 'category_name_uz', 'category_kind',
               'part_name_ru', 'part_name_uz']
VALID_KINDS = ('unit', 'consumable')

# [REASON]: New permission primitive surfaces automatically in the existing
# /admin/permissions UI (it iterates active app_modules generically). Nobody
# is granted access here -- deny-by-default; only is_admin passes until an
# admin explicitly grants the module. Same pattern as the three
# SPARE_PARTS_STAGE1 permission rows.
NEW_MODULES = [
    ('spare_parts_reports',
     'Эҳтиёт қисмлар: ҳисоботлар',
     'Запчасти: отчёты'),
]

# [REASON]: Matches migration_utils._CREATE_TABLE_SQL exactly so a fresh install
# (where migrate_000 has not run yet) still gets a compatible registry table.
ENSURE_REGISTRY = """
    CREATE TABLE IF NOT EXISTS schema_migrations (
        id          INTEGER PRIMARY KEY AUTOINCREMENT,
        name        TEXT UNIQUE NOT NULL,
        applied_at  DATETIME NOT NULL,
        checksum    TEXT,
        description TEXT
    )
"""


def _migration_applied(cur):
    cur.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name='schema_migrations'"
    )
    if cur.fetchone() is None:
        return False
    cur.execute("PRAGMA table_info(schema_migrations)")
    cols = {r[1] for r in cur.fetchall()}
    if 'name' not in cols:
        return False
    cur.execute("SELECT 1 FROM schema_migrations WHERE name = ?", (MIGRATION_ID,))
    return cur.fetchone() is not None


def _record_migration(cur):
    # [REASON]: applied_at is NOT NULL in this project's registry, so it must be
    # supplied explicitly. INSERT OR IGNORE keeps the call idempotent.
    cur.execute("PRAGMA table_info(schema_migrations)")
    cols = {r[1] for r in cur.fetchall()}
    if 'applied_at' in cols:
        cur.execute(
            "INSERT OR IGNORE INTO schema_migrations (name, applied_at, description) "
            "VALUES (?, ?, ?)",
            (MIGRATION_ID, datetime.utcnow().isoformat(timespec='seconds'),
             'Spare parts starter catalog seed: 13 categories, 238 parts '
             'from seed_data/spare_parts_catalog_v1.csv, 1 permission '
             'module row (spare_parts_reports).'),
        )
    else:
        cur.execute(
            "INSERT OR IGNORE INTO schema_migrations (name) VALUES (?)",
            (MIGRATION_ID,),
        )


def _require_stage1_schema(cur):
    """Abort with a clear operator message if Stage 1 has not run yet."""
    cur.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name='spare_part_categories'"
    )
    if cur.fetchone() is None:
        print('ERROR: table spare_part_categories not found.', file=sys.stderr)
        print('Run migrate_spare_parts_stage1.py first, then re-run this '
              'script.', file=sys.stderr)
        sys.exit(1)
    cur.execute("PRAGMA table_info(spare_parts)")
    cols = {r[1] for r in cur.fetchall()}
    for needed in ('category_id', 'status', 'is_active'):
        if needed not in cols:
            print('ERROR: column spare_parts.{} not found.'.format(needed),
                  file=sys.stderr)
            print('Run migrate_spare_parts_stage1.py first, then re-run this '
                  'script.', file=sys.stderr)
            sys.exit(1)


def _read_csv_rows():
    """Read and strictly validate the seed CSV.

    Returns a list of (category_name_ru, category_name_uz, category_kind,
    part_name_ru, part_name_uz) tuples in file order.

    [REASON]: A malformed row raises ValueError with its 1-based line number
    and the whole migration rolls back -- the seed file is curated and
    version-controlled, so silently skipping a row would hide data loss.
    """
    if not os.path.exists(CSV_PATH):
        print('ERROR: Seed CSV not found at ' + CSV_PATH, file=sys.stderr)
        sys.exit(1)
    rows = []
    # utf-8-sig tolerates a BOM if the file is ever re-saved by Excel.
    with open(CSV_PATH, encoding='utf-8-sig', newline='') as f:
        reader = csv.reader(f)
        header = next(reader, None)
        if header != CSV_COLUMNS:
            raise ValueError('CSV header mismatch: expected {}, got {}'.format(
                CSV_COLUMNS, header))
        for line_no, row in enumerate(reader, start=2):
            if not row:
                continue  # a completely blank trailing line is not data
            if len(row) != 5:
                raise ValueError('CSV line {}: expected 5 columns, got {}'.format(
                    line_no, len(row)))
            cleaned = [c.strip() for c in row]
            if not all(cleaned):
                raise ValueError('CSV line {}: empty field'.format(line_no))
            if cleaned[2] not in VALID_KINDS:
                raise ValueError("CSV line {}: category_kind must be one of {}, "
                                 "got '{}'".format(line_no, VALID_KINDS, cleaned[2]))
            rows.append(tuple(cleaned))
    return rows


def _seed_categories(cur, rows):
    """Insert missing categories; return {category_name_ru: category_id}.

    sort_order is assigned sequentially (1, 2, 3, ...) in the order
    categories first appear in the CSV.
    """
    ordered = []
    seen = set()
    for cat_ru, cat_uz, kind, _part_ru, _part_uz in rows:
        if cat_ru not in seen:
            seen.add(cat_ru)
            ordered.append((cat_ru, cat_uz, kind))

    cat_ids = {}
    inserted = skipped = 0
    for sort_order, (cat_ru, cat_uz, kind) in enumerate(ordered, start=1):
        # [REASON]: One-time seed keyed on exact case-sensitive name_ru match
        # (per task spec) -- no fuzzy dedup; spare_part_categories has no
        # UNIQUE constraint, so the existence check is explicit.
        cur.execute("SELECT id FROM spare_part_categories WHERE name_ru = ?",
                    (cat_ru,))
        found = cur.fetchone()
        if found:
            cat_ids[cat_ru] = found[0]
            skipped += 1
            print("  category '{}' already present, skipped".format(cat_ru))
            continue
        # created_by stays NULL: system-seeded, not attributed to a user.
        cur.execute(
            "INSERT INTO spare_part_categories "
            "(name_ru, name_uz, kind, is_active, sort_order, created_at) "
            "VALUES (?, ?, ?, 1, ?, ?)",
            (cat_ru, cat_uz, kind, sort_order,
             datetime.utcnow().isoformat(sep=' ', timespec='seconds')),
        )
        cat_ids[cat_ru] = cur.lastrowid
        inserted += 1
        print("  category '{}' inserted (kind={}, sort_order={})".format(
            cat_ru, kind, sort_order))
    print("Categories: {} inserted, {} already present.".format(inserted, skipped))
    return cat_ids


def _seed_parts(cur, rows, cat_ids):
    inserted = skipped = 0
    for cat_ru, _cat_uz, _kind, part_ru, _part_uz in rows:
        category_id = cat_ids[cat_ru]
        # [REASON]: SparePart has a single `name` column; the Russian name is
        # used as the canonical catalog name (RU is the primary technical
        # nomenclature; CSV keeps part_name_uz for a future bilingual column).
        # Dedup key per task spec: exact name AND category_id.
        cur.execute(
            "SELECT 1 FROM spare_parts WHERE name = ? AND category_id = ?",
            (part_ru, category_id),
        )
        if cur.fetchone():
            skipped += 1
            continue
        # [REASON]: status='active' -- curated, reviewed starter catalog, not
        # a pending_review candidate. unit='dona' and part_number=''/category=''
        # are set explicitly because the model defaults are Python-side only
        # (the table DDL has no DEFAULT clause; a raw INSERT would leave NULL,
        # unlike every ORM-created row). created_by stays NULL (system seed).
        cur.execute(
            "INSERT INTO spare_parts "
            "(name, part_number, unit, category, category_id, status, "
            " is_active, created_at) "
            "VALUES (?, '', 'dona', '', ?, 'active', 1, ?)",
            (part_ru, category_id,
             datetime.utcnow().isoformat(sep=' ', timespec='seconds')),
        )
        inserted += 1
    print("Parts: {} inserted, {} already present.".format(inserted, skipped))


def _seed_modules(cur):
    cur.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name='app_modules'"
    )
    if cur.fetchone() is None:
        # Fresh install path: app.py create_app() seeds app_modules on first
        # start via db.create_all(); nothing to do here.
        print("  app_modules table not found, module seed skipped "
              "(fresh install seeds via app start)")
        return
    for code, name_uz, name_ru in NEW_MODULES:
        cur.execute(
            "INSERT OR IGNORE INTO app_modules (code, name_uz, name_ru, is_active) "
            "VALUES (?, ?, ?, 1)",
            (code, name_uz, name_ru),
        )
        print("  module {} ensured".format(code))


def run():
    if not os.path.exists(DB_PATH):
        print('ERROR: Database not found at ' + DB_PATH, file=sys.stderr)
        print('Run this migration from the project folder with '
              'instance\\transport.db present.', file=sys.stderr)
        sys.exit(1)

    con = sqlite3.connect(DB_PATH)
    con.execute("PRAGMA busy_timeout=30000")
    try:
        cur = con.cursor()
        cur.execute(ENSURE_REGISTRY)

        if _migration_applied(cur):
            print("Migration {} already applied. Skipping.".format(MIGRATION_ID))
            return

        _require_stage1_schema(cur)

        print("Reading seed CSV...")
        rows = _read_csv_rows()
        print("  {} data rows read.".format(len(rows)))

        print("Seeding categories...")
        cat_ids = _seed_categories(cur, rows)

        print("Seeding parts...")
        _seed_parts(cur, rows, cat_ids)

        print("Seeding permission module rows...")
        _seed_modules(cur)

        _record_migration(cur)
        con.commit()
        print("Migration {} applied successfully.".format(MIGRATION_ID))

    except BaseException as exc:
        con.rollback()
        print("Migration {} FAILED: {}".format(MIGRATION_ID, exc), file=sys.stderr)
        sys.exit(1)
    finally:
        con.close()


if __name__ == '__main__':
    run()
