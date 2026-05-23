"""Migrate daily records from old v1 database to new v2 database."""
import sqlite3
import os

OLD_DB = r'C:\transport-report\old_transport.db'
NEW_DB = r'C:\transport-report\instance\transport.db'

if not os.path.exists(OLD_DB):
    print('ERROR: Old database not found at', OLD_DB)
    print('Copy your old transport.db to C:\\transport-report\\old_transport.db')
    exit(1)

old = sqlite3.connect(OLD_DB)
new = sqlite3.connect(NEW_DB)

old.row_factory = sqlite3.Row
new.row_factory = sqlite3.Row

# Build equipment name->id mapping for new DB
new_eq = {}
for row in new.execute('SELECT id, name, plate FROM equipment'):
    key = (row['name'], row['plate'])
    new_eq[key] = row['id']
    # Also map by name only (fallback)
    new_eq[row['name']] = row['id']

# Read old daily records
old_records = old.execute('''
    SELECT dr.*, e.name as eq_name, e.plate as eq_plate
    FROM daily_records dr
    JOIN equipment e ON dr.equipment_id = e.id
''').fetchall()

print('Old database: {} records found'.format(len(old_records)))

imported = 0
skipped = 0
not_found = set()

for r in old_records:
    # Find matching equipment in new DB
    new_id = new_eq.get((r['eq_name'], r['eq_plate'])) or new_eq.get(r['eq_name'])

    if not new_id:
        not_found.add(r['eq_name'])
        skipped += 1
        continue

    # Check if record already exists
    existing = new.execute(
        'SELECT id FROM daily_records WHERE work_date=? AND equipment_id=? AND line_index=?',
        (r['work_date'], new_id, r['line_index'] or 0)
    ).fetchone()

    if existing:
        skipped += 1
        continue

    new.execute('''
        INSERT INTO daily_records
        (work_date, equipment_id, line_index, status, work_type, customer,
         unit, quantity, price, amount_cash, amount_transfer, amount_internal,
         payment_type, idle_reason, note, created_by)
        VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
    ''', (
        r['work_date'], new_id, r['line_index'] or 0,
        r['status'], r['work_type'], r['customer'],
        r['unit'], r['quantity'], r['price'],
        r['amount_cash'], r['amount_transfer'], r['amount_internal'],
        r['payment_type'], r['idle_reason'], r['note'], 1
    ))
    imported += 1

new.commit()
old.close()
new.close()

print('Imported: {} records'.format(imported))
print('Skipped (already exist): {}'.format(skipped))
if not_found:
    print('Equipment not found in new DB:')
    for name in not_found:
        print('  -', name)
print('Done!')