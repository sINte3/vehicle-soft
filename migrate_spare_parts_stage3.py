# -*- coding: utf-8 -*-
"""Migration SPARE_PARTS_STAGE3 -- Spare parts Stage 3 schema + eq_type backfill.

Creates (additive only -- no existing column is dropped, renamed or retyped):

  Part 1 -- equipment-model reference:
    - equipment_models (canonical model reference; name UNIQUE NOT NULL,
      migrated_from_eq_type audit column)
    - additive nullable column equipment.model_id -> equipment_models.id
    - one-to-one backfill: one equipment_models row per DISTINCT non-empty
      Equipment.eq_type value, then every matching Equipment.model_id is
      pointed at it. NO near-match dedup (that is a manual human decision made
      later through the merge screen). Empty/NULL eq_type -> model_id NULL.

  Part 2 -- compatibility matrix:
    - spare_part_compatibility (part <-> equipment model,
      UNIQUE(spare_part_id, equipment_model_id))

  Part 4 -- maintenance norms:
    - spare_part_maintenance_norms (part [+ optional model] -> interval_hours)

  Supporting indexes for all three tables.

Permissions:
  No new app_modules row. The Stage-3 screens (equipment-model management,
  compatibility editor, maintenance norms) all reuse the existing
  'spare_parts_catalog_manage' permission seeded in Stage 1.

Safe / idempotent:
  - CREATE TABLE IF NOT EXISTS / CREATE INDEX IF NOT EXISTS.
  - ALTER TABLE ... ADD COLUMN only when the column is absent.
  - INSERT OR IGNORE for equipment_models (name is UNIQUE), so re-running
    never duplicates a model.
  - Equipment.model_id is only set where it is currently NULL, so re-running
    never clobbers a value set by hand through the UI.
  - Registers itself in schema_migrations and skips wholesale on re-run.
  - Does not touch or delete any existing business data, and does NOT modify
    eq_type -- every existing eq_type reader keeps working unchanged.

[REASON]: SPARE-STAGE3 — follows the exact name-based schema_migrations pattern
proven by migrate_spare_parts_stage2.py (id INTEGER PRIMARY KEY AUTOINCREMENT,
applied_at NOT NULL). See that file for the rationale.

Run (service must be STOPPED first to avoid SQLite write conflicts):

  cd C:\\transport-report-staging
  .\\nssm.exe stop TransportReportStaging
  "C:\\Program Files\\Python314\\python.exe" migrate_spare_parts_stage3.py
  .\\nssm.exe start TransportReportStaging

Rollback:
  This migration is purely additive (three new tables, one nullable column,
  indexes). A manual rollback would need to undo, in this order:
  DROP INDEX IF EXISTS idx_spare_part_maintenance_norms_part;
  DROP INDEX IF EXISTS idx_spare_part_compatibility_model;
  DROP INDEX IF EXISTS idx_spare_part_compatibility_part;
  DROP TABLE IF EXISTS spare_part_maintenance_norms;
  DROP TABLE IF EXISTS spare_part_compatibility;
  DROP TABLE IF EXISTS equipment_models;
  DELETE FROM schema_migrations WHERE name = 'SPARE_PARTS_STAGE3';
  (Dropping the added equipment.model_id column requires a SQLite table
   rebuild. As with prior spare-parts migrations, the real safety net is the
   pre-migration database backup. Note: eq_type is untouched, so simply not
   using the new code is a complete functional rollback.)
"""

import os
import sqlite3
import sys
from datetime import datetime

ROOT = os.path.dirname(os.path.abspath(__file__))
DB_PATH = os.path.join(ROOT, 'instance', 'transport.db')
MIGRATION_ID = 'SPARE_PARTS_STAGE3'

CREATE_EQUIPMENT_MODELS = """
    CREATE TABLE IF NOT EXISTS equipment_models (
        id                    INTEGER PRIMARY KEY,
        name                  VARCHAR(150) NOT NULL UNIQUE,
        name_uz               VARCHAR(150),
        manufacturer          VARCHAR(100),
        is_active             BOOLEAN DEFAULT 1,
        migrated_from_eq_type VARCHAR(150),
        created_at            DATETIME DEFAULT CURRENT_TIMESTAMP
    )
"""

CREATE_COMPATIBILITY = """
    CREATE TABLE IF NOT EXISTS spare_part_compatibility (
        id                 INTEGER PRIMARY KEY,
        spare_part_id      INTEGER NOT NULL REFERENCES spare_parts(id),
        equipment_model_id INTEGER NOT NULL REFERENCES equipment_models(id),
        created_by         INTEGER REFERENCES users(id),
        created_at         DATETIME DEFAULT CURRENT_TIMESTAMP,
        CONSTRAINT uq_spare_part_compatibility_part_model
            UNIQUE (spare_part_id, equipment_model_id)
    )
"""

CREATE_MAINTENANCE_NORMS = """
    CREATE TABLE IF NOT EXISTS spare_part_maintenance_norms (
        id                 INTEGER PRIMARY KEY,
        spare_part_id      INTEGER NOT NULL REFERENCES spare_parts(id),
        equipment_model_id INTEGER REFERENCES equipment_models(id),
        interval_hours     FLOAT NOT NULL,
        is_active          BOOLEAN DEFAULT 1,
        created_by         INTEGER REFERENCES users(id),
        created_at         DATETIME DEFAULT CURRENT_TIMESTAMP
    )
"""

# ALTER TABLE ... ADD COLUMN statements, applied only when the column is absent.
EQUIPMENT_COLUMNS = [
    ("model_id", "ALTER TABLE equipment ADD COLUMN model_id INTEGER"
                 " REFERENCES equipment_models(id)"),
]

INDEXES = [
    "CREATE INDEX IF NOT EXISTS idx_spare_part_compatibility_part"
    " ON spare_part_compatibility(spare_part_id)",
    "CREATE INDEX IF NOT EXISTS idx_spare_part_compatibility_model"
    " ON spare_part_compatibility(equipment_model_id)",
    "CREATE INDEX IF NOT EXISTS idx_spare_part_maintenance_norms_part"
    " ON spare_part_maintenance_norms(spare_part_id)",
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


def _add_missing_columns(cur, table, columns):
    existing = [r[1] for r in cur.execute(
        "PRAGMA table_info({})".format(table)).fetchall()]
    for col_name, ddl in columns:
        if col_name not in existing:
            cur.execute(ddl)
            print("  added column {}.{}".format(table, col_name))
        else:
            print("  column {}.{} already present, skipped".format(table, col_name))


def _backfill_models(cur):
    """One-to-one eq_type -> equipment_models seed + model_id mapping.

    [REASON]: SPARE-STAGE3 — lossless, one-to-one mapping only. Distinct raw
    eq_type strings become distinct models even if they differ only in case or
    punctuation ('New Holland 7060' vs 'NEW HOLLAND-7060'); deduplication is a
    manual human decision made later via the merge screen, never here.
    """
    # DISTINCT non-empty eq_type values, exact text preserved (no normalisation).
    rows = cur.execute(
        "SELECT DISTINCT eq_type FROM equipment "
        "WHERE eq_type IS NOT NULL AND TRIM(eq_type) <> ''"
    ).fetchall()
    eq_types = [r[0] for r in rows]
    created = 0
    for value in eq_types:
        # INSERT OR IGNORE: name is UNIQUE, so a model created earlier (by a
        # prior run or by hand) is left untouched and never duplicated.
        cur.execute(
            "INSERT OR IGNORE INTO equipment_models "
            "(name, migrated_from_eq_type, is_active, created_at) "
            "VALUES (?, ?, 1, ?)",
            (value, value, datetime.utcnow().isoformat(timespec='seconds')),
        )
        created += cur.rowcount
    print("  {} distinct eq_type value(s); {} new equipment_models row(s)".format(
        len(eq_types), created))

    # Map name -> id, then point every still-unmapped Equipment at its model.
    name_to_id = {r[0]: r[1] for r in cur.execute(
        "SELECT name, id FROM equipment_models").fetchall()}
    mapped = 0
    for value in eq_types:
        model_id = name_to_id.get(value)
        if model_id is None:
            continue
        # [REASON]: only fill model_id where it is currently NULL — never
        # clobber a value already set (idempotency + respects manual UI edits).
        cur.execute(
            "UPDATE equipment SET model_id = ? WHERE eq_type = ? AND model_id IS NULL",
            (model_id, value),
        )
        mapped += cur.rowcount
    print("  {} equipment row(s) linked to a model".format(mapped))


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
             'SPARE-STAGE3: equipment_models table + equipment.model_id column '
             'with one-to-one eq_type backfill; spare_part_compatibility matrix; '
             'spare_part_maintenance_norms; indexes.'),
        )
    else:
        cur.execute(
            "INSERT OR IGNORE INTO schema_migrations (name) VALUES (?)",
            (MIGRATION_ID,),
        )


def apply(cur):
    """Run all Stage-3 DDL + backfill on an open cursor. Does not commit."""
    print("Creating tables...")
    cur.execute(CREATE_EQUIPMENT_MODELS)
    cur.execute(CREATE_COMPATIBILITY)
    cur.execute(CREATE_MAINTENANCE_NORMS)

    print("Adding columns to equipment...")
    _add_missing_columns(cur, 'equipment', EQUIPMENT_COLUMNS)

    print("Backfilling equipment models from eq_type...")
    _backfill_models(cur)

    print("Creating indexes...")
    for sql in INDEXES:
        cur.execute(sql)


def run(db_path=None):
    """CLI/entry point. db_path defaults to instance/transport.db (production).

    [REASON]: SPARE-STAGE3 — db_path is parameterised only so the migration can
    be exercised against a temporary SQLite file in an automated test without
    touching the real database; production always uses the default.
    """
    path = db_path or DB_PATH
    if not os.path.exists(path):
        print('ERROR: Database not found at ' + path, file=sys.stderr)
        print('Run this migration from the project folder with '
              'instance\\transport.db present.', file=sys.stderr)
        sys.exit(1)

    con = sqlite3.connect(path)
    con.execute("PRAGMA busy_timeout=30000")
    try:
        cur = con.cursor()
        cur.execute(ENSURE_REGISTRY)

        if _migration_applied(cur):
            print("Migration {} already applied. Skipping.".format(MIGRATION_ID))
            return

        apply(cur)

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
