# -*- coding: utf-8 -*-
"""
migrate_v42.py

Changes vs v4.1:
  1. Adds column unknown_vehicles_json to vialon_imports

Safe to run on existing database.
Run:
  "C:\\Program Files\\Python314\\python.exe" migrate_v42.py
"""

import sqlite3
import os

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DB_PATH  = os.path.join(BASE_DIR, 'instance', 'transport.db')


def column_exists(cur, table, column):
    cur.execute("PRAGMA table_info({})".format(table))
    return any(row[1] == column for row in cur.fetchall())


def run():
    if not os.path.exists(DB_PATH):
        print('ERROR: database not found at', DB_PATH)
        return

    con = sqlite3.connect(DB_PATH)
    cur = con.cursor()

    # Add unknown_vehicles_json to vialon_imports
    if not column_exists(cur, 'vialon_imports', 'unknown_vehicles_json'):
        cur.execute("""
            ALTER TABLE vialon_imports
            ADD COLUMN unknown_vehicles_json TEXT DEFAULT '[]'
        """)
        print("OK: added unknown_vehicles_json to vialon_imports")
    else:
        print("OK: unknown_vehicles_json already exists, skipped")

    con.commit()
    con.close()
    print()
    print("Migration v4.2 completed.")
    print("Database:", DB_PATH)


if __name__ == '__main__':
    run()
