# -*- coding: utf-8 -*-
"""
migrate_v46.py — Adds fuel balance and receipt tables.
Run: "C:\\Program Files\\Python314\\python.exe" migrate_v46.py
"""
import sqlite3, os

DB = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                  'instance', 'transport.db')

SQL = [
    # Opening balances per AZS per date (manual entry)
    """CREATE TABLE IF NOT EXISTS fuel_balances (
        id          INTEGER PRIMARY KEY AUTOINCREMENT,
        station_id  INTEGER NOT NULL REFERENCES fuel_stations(id),
        balance_date DATE NOT NULL,
        fuel_name   VARCHAR(100) DEFAULT '',
        volume      REAL DEFAULT 0,
        note        TEXT DEFAULT '',
        created_by  INTEGER REFERENCES users(id),
        created_at  DATETIME DEFAULT CURRENT_TIMESTAMP,
        UNIQUE(station_id, balance_date, fuel_name)
    )""",
    "CREATE INDEX IF NOT EXISTS ix_fuel_bal_date ON fuel_balances(balance_date)",
    # Fuel receipts (manual entry)
    """CREATE TABLE IF NOT EXISTS fuel_receipts (
        id          INTEGER PRIMARY KEY AUTOINCREMENT,
        station_id  INTEGER NOT NULL REFERENCES fuel_stations(id),
        receipt_date DATE NOT NULL,
        fuel_name   VARCHAR(100) DEFAULT '',
        volume      REAL DEFAULT 0,
        supplier    VARCHAR(200) DEFAULT '',
        note        TEXT DEFAULT '',
        created_by  INTEGER REFERENCES users(id),
        created_at  DATETIME DEFAULT CURRENT_TIMESTAMP
    )""",
    "CREATE INDEX IF NOT EXISTS ix_fuel_rec_date ON fuel_receipts(receipt_date)",
]

def run():
    if not os.path.exists(DB):
        print('ERROR: DB not found:', DB); return
    con = sqlite3.connect(DB)
    cur = con.cursor()
    for sql in SQL:
        cur.execute(sql)
        name = sql.split()[2] if sql.startswith('CREATE TABLE') else sql.split()[4]
        print('OK:', name)
    con.commit(); con.close()
    print('\nMigration v4.6 done.')
    input("Press Enter...")

if __name__ == '__main__':
    run()
