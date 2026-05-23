# -*- coding: utf-8 -*-
"""
migrate_add_wialon.py

Adds three new tables for Wialon integration:
  - vialon_mappings
  - vialon_imports
  - engine_hours_records

Safe to run on existing database — does NOT drop any existing data.
Run once on the server:
  C:\Program Files\Python314\python.exe migrate_add_wialon.py
"""

import sqlite3
import os

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DB_PATH  = os.path.join(BASE_DIR, 'instance', 'transport.db')


def run():
    if not os.path.exists(DB_PATH):
        print('ERROR: database not found at', DB_PATH)
        return

    con = sqlite3.connect(DB_PATH)
    cur = con.cursor()

    # ── 1. vialon_mappings ─────────────────────────────────────────────
    cur.execute("""
        CREATE TABLE IF NOT EXISTS vialon_mappings (
            id           INTEGER PRIMARY KEY AUTOINCREMENT,
            vialon_name  VARCHAR(300) NOT NULL UNIQUE,
            equipment_id INTEGER REFERENCES equipment(id),
            skip         INTEGER NOT NULL DEFAULT 0,
            created_by   INTEGER REFERENCES users(id),
            created_at   DATETIME DEFAULT CURRENT_TIMESTAMP,
            updated_at   DATETIME DEFAULT CURRENT_TIMESTAMP
        )
    """)
    print('OK: vialon_mappings')

    # ── 2. vialon_imports ──────────────────────────────────────────────
    cur.execute("""
        CREATE TABLE IF NOT EXISTS vialon_imports (
            id               INTEGER PRIMARY KEY AUTOINCREMENT,
            import_date      DATE NOT NULL,
            filename         VARCHAR(300) DEFAULT '',
            vehicles_in_file INTEGER DEFAULT 0,
            vehicles_matched INTEGER DEFAULT 0,
            vehicles_saved   INTEGER DEFAULT 0,
            vehicles_skipped INTEGER DEFAULT 0,
            vehicles_unknown INTEGER DEFAULT 0,
            created_by       INTEGER REFERENCES users(id),
            created_at       DATETIME DEFAULT CURRENT_TIMESTAMP
        )
    """)
    print('OK: vialon_imports')

    # ── 3. engine_hours_records ────────────────────────────────────────
    cur.execute("""
        CREATE TABLE IF NOT EXISTS engine_hours_records (
            id           INTEGER PRIMARY KEY AUTOINCREMENT,
            work_date    DATE NOT NULL,
            equipment_id INTEGER NOT NULL REFERENCES equipment(id),
            import_id    INTEGER REFERENCES vialon_imports(id),
            engine_hours REAL DEFAULT 0,
            hours_moving REAL DEFAULT 0,
            hours_idle   REAL DEFAULT 0,
            vialon_name  VARCHAR(300) DEFAULT '',
            UNIQUE(work_date, equipment_id)
        )
    """)
    cur.execute("""
        CREATE INDEX IF NOT EXISTS ix_engine_hours_date
        ON engine_hours_records(work_date)
    """)
    print('OK: engine_hours_records')

    con.commit()
    con.close()
    print()
    print('Migration completed successfully.')
    print('Database:', DB_PATH)


if __name__ == '__main__':
    run()
