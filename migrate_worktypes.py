"""Migrate work types from old v1 database to new v2 database."""
import sqlite3

OLD_DB = r'C:\transport-report\old_transport.db'
NEW_DB = r'C:\transport-report\instance\transport.db'

old = sqlite3.connect(OLD_DB)
new = sqlite3.connect(NEW_DB)
old.row_factory = sqlite3.Row
new.row_factory = sqlite3.Row

# Get existing work types in new DB
existing = set()
for row in new.execute('SELECT name FROM work_types'):
    existing.add(row['name'])

# Read all from old DB
old_wt = old.execute('SELECT * FROM work_types ORDER BY name').fetchall()

print('Old DB: {} work types found'.format(len(old_wt)))

imported = 0
skipped = 0

for wt in old_wt:
    if wt['name'] in existing:
        skipped += 1
        continue

    new.execute('''
        INSERT INTO work_types (name, default_unit, default_price)
        VALUES (?,?,?)
    ''', (wt['name'], wt['default_unit'] or '', wt['default_price'] or 0))
    imported += 1
    print('  + {}  ({}  {} sum)'.format(wt['name'], wt['default_unit'] or '', wt['default_price'] or 0))

new.commit()
old.close()
new.close()

print('')
print('Imported: {}'.format(imported))
print('Skipped (already exist): {}'.format(skipped))
print('Done!')