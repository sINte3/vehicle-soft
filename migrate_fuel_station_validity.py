# -*- coding: utf-8 -*-
"""Idempotent migration for FuelStation2 validity/replacement fields."""
import os
import sqlite3

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DB_PATH = os.path.join(BASE_DIR, 'instance', 'transport.db')

COLUMNS = {
    'valid_from': 'DATE',
    'valid_to': 'DATE',
    'replacement_of_id': 'INTEGER',
    'notes': "TEXT DEFAULT ''",
}

def main():
    if not os.path.exists(DB_PATH):
        raise SystemExit(f'DB not found: {DB_PATH}')

    conn = sqlite3.connect(DB_PATH)
    try:
        cols = {row[1] for row in conn.execute('PRAGMA table_info(fuel_stations2)').fetchall()}
        changed = []
        for name, ddl in COLUMNS.items():
            if name not in cols:
                conn.execute(f'ALTER TABLE fuel_stations2 ADD COLUMN {name} {ddl}')
                changed.append(name)
        conn.commit()

        if changed:
            print('ADDED_COLUMNS=' + ','.join(changed))
        else:
            print('NO_SCHEMA_CHANGE_NEEDED')

        final_cols = [row[1] for row in conn.execute('PRAGMA table_info(fuel_stations2)').fetchall()]
        print('fuel_stations2 columns:')
        print(', '.join(final_cols))
    finally:
        conn.close()

if __name__ == '__main__':
    main()
