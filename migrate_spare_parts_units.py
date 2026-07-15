# -*- coding: utf-8 -*-
"""Migration SPARE_PARTS_UNITS -- SP-F-024.

Owner decision (2026-07-14): units of measure become a STRICT managed
bilingual directory (not free text, not a soft alias approach).

Creates:
  - table spare_part_units (code / name_ru / name_uz / is_active /
    sort_order / created_at, UNIQUE index on code) -- the
    SparePartCategory-shaped reference table.
  - baseline seed rows (INSERT OR IGNORE by unique code):
      dona     / шт    / дона     (the historical default value 'dona' is
                                   itself the code, so existing stored rows
                                   already read as valid codes)
      litr     / л     / литр
      kg       / кг    / кг
      komplekt / компл / тўплам
      metr     / м     / метр
  - IN ADDITION, every distinct legacy unit value found in
    spare_part_request_items.unit and spare_parts.unit that is not
    recognized as one of the seeded codes/labels/aliases is auto-registered
    as its own active unit row (code auto-derived, display label = the
    original text, printed in the output). Nothing historical is dropped or
    renamed -- per the task spec, every legacy value must map to a seeded
    code so the strict pickers/validation never orphan real data.

Does NOT rewrite any existing spare_part_request_items.unit or
spare_parts.unit values: item units are historical snapshots by existing
convention. The directory governs what the pickers offer going forward.

Safe / idempotent:
  - CREATE TABLE IF NOT EXISTS / CREATE UNIQUE INDEX IF NOT EXISTS.
  - INSERT OR IGNORE for every seed row (code is UNIQUE).
  - Registers itself in schema_migrations and skips on re-run.

Run (service must be STOPPED first to avoid SQLite write conflicts):

  cd C:\\transport-report
  .\\nssm.exe stop TransportReport
  "C:\\Program Files\\Python314\\python.exe" migrate_spare_parts_units.py
  .\\nssm.exe start TransportReport

Deploy / rollback ORDER (RE-SP-009 — this order is mandatory):

  Deploy:   apply migrate_spare_parts_units.py FIRST, then deploy code
            that queries SparePartUnit.
  Rollback: deploy code that no longer queries SparePartUnit FIRST, then:
              DROP TABLE IF EXISTS spare_part_units;
              DELETE FROM schema_migrations WHERE name = 'SPARE_PARTS_UNITS';

  An EMPTY existing table yields the legacy free-text behavior by design.
  A MISSING table is different: _active_units() in spare_parts.py carries a
  narrow "no such table" safety net (added by RE-SP-009) that returns the
  same free-text fallback instead of a 500 — but that net exists ONLY for
  the transitional deploy/rollback window described above, not as a
  supported steady state. Intended steady state: table present, directory
  authoritative.
"""

import os
import re
import sqlite3
import sys
from datetime import datetime

ROOT = os.path.dirname(os.path.abspath(__file__))
DB_PATH = os.path.join(ROOT, 'instance', 'transport.db')
MIGRATION_ID = 'SPARE_PARTS_UNITS'

CREATE_TABLE = """
    CREATE TABLE IF NOT EXISTS spare_part_units (
        id         INTEGER PRIMARY KEY,
        code       VARCHAR(30) NOT NULL,
        name_ru    VARCHAR(100) NOT NULL,
        name_uz    VARCHAR(100) NOT NULL,
        is_active  BOOLEAN DEFAULT 1,
        sort_order INTEGER DEFAULT 0,
        created_at DATETIME DEFAULT CURRENT_TIMESTAMP
    )
"""

CREATE_INDEX = """
    CREATE UNIQUE INDEX IF NOT EXISTS uq_spare_part_units_code
      ON spare_part_units(code)
"""

# (code, name_ru, name_uz, sort_order)
# [REASON]: 'dona' is deliberately the code for pieces -- it is the literal
# default value the application has stored on items since Stage 1, so all
# existing rows already carry a valid code without any data rewrite.
BASELINE_UNITS = [
    ('dona',     'шт',    'дона',   10),
    ('litr',     'л',     'литр',   20),
    ('kg',       'кг',    'кг',     30),
    ('komplekt', 'компл', 'тўплам', 40),
    ('metr',     'м',     'метр',   50),
]

# Normalized legacy spellings that map onto a baseline code. Used ONLY to
# decide whether a legacy value needs its own auto-registered row -- stored
# data is never rewritten.
ALIASES = {
    'dona': 'dona', 'шт': 'dona', 'шт.': 'dona', 'штук': 'dona',
    'штука': 'dona', 'дона': 'dona', 'pcs': 'dona', 'ед': 'dona',
    'ед.': 'dona',
    'litr': 'litr', 'л': 'litr', 'литр': 'litr', 'литров': 'litr',
    'l': 'litr', 'лит': 'litr',
    'kg': 'kg', 'кг': 'kg', 'кг.': 'kg', 'килограмм': 'kg',
    'komplekt': 'komplekt', 'компл': 'komplekt', 'компл.': 'komplekt',
    'комплект': 'komplekt', 'тўплам': 'komplekt', 'туплам': 'komplekt',
    'set': 'komplekt',
    'metr': 'metr', 'м': 'metr', 'м.': 'metr', 'метр': 'metr', 'm': 'metr',
}

ENSURE_REGISTRY = """
    CREATE TABLE IF NOT EXISTS schema_migrations (
        id          INTEGER PRIMARY KEY AUTOINCREMENT,
        name        TEXT UNIQUE NOT NULL,
        applied_at  DATETIME NOT NULL,
        checksum    TEXT,
        description TEXT
    )
"""


def _slug(value, taken):
    """Derive a unique ASCII code from a legacy unit value."""
    base = re.sub(r'[^a-z0-9]+', '_', value.strip().lower()).strip('_')
    if not base:
        base = 'legacy'
    base = base[:24]
    code = base
    n = 2
    while code in taken:
        code = '{}_{}'.format(base, n)
        n += 1
    return code


def _distinct_legacy_units(cur):
    values = set()
    for table in ('spare_part_request_items', 'spare_parts'):
        cur.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name=?",
            (table,))
        if cur.fetchone() is None:
            continue
        cur.execute(
            "SELECT DISTINCT unit FROM {} WHERE unit IS NOT NULL "
            "AND trim(unit) != ''".format(table))
        values.update(r[0].strip() for r in cur.fetchall() if r[0] and r[0].strip())
    return sorted(values)


def _seed_units(cur):
    for code, name_ru, name_uz, sort_order in BASELINE_UNITS:
        cur.execute(
            "INSERT OR IGNORE INTO spare_part_units "
            "(code, name_ru, name_uz, is_active, sort_order) "
            "VALUES (?, ?, ?, 1, ?)",
            (code, name_ru, name_uz, sort_order))
        print("  unit {} ({} / {}) ensured".format(code, name_ru, name_uz))

    # Auto-register unrecognized legacy values so the strict pickers can
    # still offer everything real data already uses.
    cur.execute("SELECT code FROM spare_part_units")
    taken = {r[0] for r in cur.fetchall()}
    legacy = _distinct_legacy_units(cur)
    if legacy:
        print("  distinct legacy unit values found: {}".format(
            ', '.join(repr(v) for v in legacy)))
    extra_sort = 100
    for value in legacy:
        norm = value.lower()
        if norm in taken or norm in ALIASES:
            continue
        code = _slug(norm, taken)
        cur.execute(
            "INSERT OR IGNORE INTO spare_part_units "
            "(code, name_ru, name_uz, is_active, sort_order) "
            "VALUES (?, ?, ?, 1, ?)",
            (code, value, value, extra_sort))
        taken.add(code)
        extra_sort += 10
        print("  AUTO-REGISTERED legacy unit {!r} as code {!r} -- review its "
              "RU/UZ labels on the catalog screen later".format(value, code))


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
    cur.execute("PRAGMA table_info(schema_migrations)")
    cols = {r[1] for r in cur.fetchall()}
    if 'applied_at' in cols:
        cur.execute(
            "INSERT OR IGNORE INTO schema_migrations (name, applied_at, description) "
            "VALUES (?, ?, ?)",
            (MIGRATION_ID, datetime.utcnow().isoformat(timespec='seconds'),
             'SP-F-024: spare_part_units managed directory (table, unique '
             'code index, baseline seeds, auto-registered legacy values).'),
        )
    else:
        cur.execute(
            "INSERT OR IGNORE INTO schema_migrations (name) VALUES (?)",
            (MIGRATION_ID,),
        )


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

        print("Creating spare_part_units table...")
        cur.execute(CREATE_TABLE)
        cur.execute(CREATE_INDEX)

        print("Seeding units...")
        _seed_units(cur)

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
