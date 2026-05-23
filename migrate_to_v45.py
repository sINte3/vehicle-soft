# -*- coding: utf-8 -*-
"""
migrate_to_v45.py - Combined migration for v4.5
Safely applies ALL migrations from v4.2 + v4.5.
Safe to run multiple times (idempotent).

Run on server 10.103.25.200:
  "C:\\Program Files\\Python314\\python.exe" migrate_to_v45.py
"""
import sqlite3, os

DB = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                  'instance', 'transport.db')


def col_exists(cur, table, col):
    cur.execute("PRAGMA table_info({})".format(table))
    return any(r[1] == col for r in cur.fetchall())


def table_exists(cur, table):
    cur.execute("SELECT name FROM sqlite_master WHERE type='table' AND name=?", (table,))
    return cur.fetchone() is not None


def run():
    if not os.path.exists(DB):
        print('ERROR: DB not found at', DB)
        return

    con = sqlite3.connect(DB)
    cur = con.cursor()

    # ── v4.2: add unknown_vehicles_json to vialon_imports ─────────────────
    if table_exists(cur, 'vialon_imports'):
        if not col_exists(cur, 'vialon_imports', 'unknown_vehicles_json'):
            cur.execute("""
                ALTER TABLE vialon_imports
                ADD COLUMN unknown_vehicles_json TEXT DEFAULT '[]'
            """)
            print("OK: added unknown_vehicles_json to vialon_imports")
        else:
            print("OK: unknown_vehicles_json already exists, skip")
    else:
        print("WARN: vialon_imports table not found (run migrate_add_wialon.py first?)")

    # ── v4.5: fuel tables ─────────────────────────────────────────────────
    fuel_tables = [
        ("fuel_stations", """CREATE TABLE IF NOT EXISTS fuel_stations (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            pos_id INTEGER UNIQUE NOT NULL,
            pos_name VARCHAR(200) DEFAULT '',
            pos_code VARCHAR(50) DEFAULT '',
            is_active INTEGER DEFAULT 1,
            updated_at DATETIME DEFAULT CURRENT_TIMESTAMP
        )"""),
        ("fuel_tanks", """CREATE TABLE IF NOT EXISTS fuel_tanks (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            station_id INTEGER NOT NULL REFERENCES fuel_stations(id),
            tank_name VARCHAR(200) DEFAULT '',
            fuel_name VARCHAR(100) DEFAULT '',
            max_volume REAL DEFAULT 0,
            updated_at DATETIME DEFAULT CURRENT_TIMESTAMP
        )"""),
        ("fuel_snapshots", """CREATE TABLE IF NOT EXISTS fuel_snapshots (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            station_id INTEGER REFERENCES fuel_stations(id),
            snapshot_date DATETIME NOT NULL,
            tank_name VARCHAR(200) DEFAULT '',
            fuel_name VARCHAR(100) DEFAULT '',
            volume REAL DEFAULT 0,
            max_volume REAL DEFAULT 0,
            temperature REAL,
            density REAL,
            height REAL,
            UNIQUE(station_id, tank_name)
        )"""),
        ("fuel_transactions", """CREATE TABLE IF NOT EXISTS fuel_transactions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            station_id INTEGER REFERENCES fuel_stations(id),
            tx_date DATETIME NOT NULL,
            card_id VARCHAR(100) DEFAULT '',
            fuel_name VARCHAR(100) DEFAULT '',
            volume REAL DEFAULT 0,
            amount REAL DEFAULT 0,
            price REAL DEFAULT 0,
            azs_code VARCHAR(50) DEFAULT '',
            session_num INTEGER,
            created_at DATETIME DEFAULT CURRENT_TIMESTAMP
        )"""),
        ("fuel_sync_logs", """CREATE TABLE IF NOT EXISTS fuel_sync_logs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            synced_at DATETIME DEFAULT CURRENT_TIMESTAMP,
            stations_count INTEGER DEFAULT 0,
            snapshots_count INTEGER DEFAULT 0,
            tx_count INTEGER DEFAULT 0,
            status VARCHAR(20) DEFAULT 'ok',
            error_msg TEXT DEFAULT ''
        )"""),
    ]

    for tname, sql in fuel_tables:
        cur.execute(sql)
        print("OK: {}".format(tname))

    # Indexes
    indexes = [
        "CREATE INDEX IF NOT EXISTS ix_fuel_snap_date ON fuel_snapshots(snapshot_date)",
        "CREATE INDEX IF NOT EXISTS ix_fuel_tx_date   ON fuel_transactions(tx_date)",
        "CREATE INDEX IF NOT EXISTS ix_fuel_sync_date ON fuel_sync_logs(synced_at)",
    ]
    for idx in indexes:
        cur.execute(idx)

    con.commit()
    con.close()

    print()
    print("Migration v4.5 completed successfully.")
    print("Database:", DB)
    input("\nPress Enter to close...")


if __name__ == '__main__':
    run()
