"""
Add 'amount_other' column to daily_records table.
Run: python add_boshqa_column.py
"""
import sqlite3
import os

DB_PATH = os.path.join(os.path.dirname(__file__), 'instance', 'transport.db')

if not os.path.exists(DB_PATH):
    print(f'ERROR: Database not found at {DB_PATH}')
    exit(1)

conn = sqlite3.connect(DB_PATH)
cur = conn.cursor()

# Check if column already exists
cur.execute("PRAGMA table_info(daily_records)")
columns = [row[1] for row in cur.fetchall()]

if 'amount_other' in columns:
    print('Column amount_other already exists. Nothing to do.')
else:
    cur.execute('ALTER TABLE daily_records ADD COLUMN amount_other FLOAT DEFAULT 0')
    conn.commit()
    print('OK: Column amount_other added to daily_records')

conn.close()
print('Done!')
