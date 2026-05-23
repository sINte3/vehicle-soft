"""Migrate equipment from old v1 database to new v2 database."""
import sqlite3

OLD_DB = r'C:\transport-report\old_transport.db'
NEW_DB = r'C:\transport-report\instance\transport.db'

old = sqlite3.connect(OLD_DB)
new = sqlite3.connect(NEW_DB)
old.row_factory = sqlite3.Row
new.row_factory = sqlite3.Row

# Map old org names to new org IDs
new_orgs = {}
for row in new.execute('SELECT id, name FROM organizations'):
    new_orgs[row['name']] = row['id']

# Map old org IDs to org names
old_org_names = {}
for row in old.execute('SELECT id, name FROM organizations'):
    old_org_names[row['id']] = row['name']

# Get existing equipment in new DB (to avoid duplicates)
existing = set()
for row in new.execute('SELECT name, plate, organization_id FROM equipment'):
    existing.add((row['name'], row['plate'] or '', row['organization_id']))

# Read all equipment from old DB
old_equipment = old.execute('''
    SELECT e.*, o.name as org_name
    FROM equipment e
    JOIN organizations o ON e.organization_id = o.id
    ORDER BY o.sort_order, e.category, e.name
''').fetchall()

print('Old DB: {} equipment found'.format(len(old_equipment)))

imported = 0
skipped = 0
org_not_found = set()

for eq in old_equipment:
    org_name = eq['org_name']
    new_org_id = new_orgs.get(org_name)

    if not new_org_id:
        org_not_found.add(org_name)
        skipped += 1
        continue

    # Check if already exists
    if (eq['name'], eq['plate'] or '', new_org_id) in existing:
        skipped += 1
        continue

    new.execute('''
        INSERT INTO equipment (name, plate, category, eq_type, organization_id,
                               default_price, default_unit, is_active)
        VALUES (?,?,?,?,?,?,?,?)
    ''', (
        eq['name'], eq['plate'] or '', eq['category'], eq['eq_type'] or '',
        new_org_id, eq['default_price'] or 0, eq['default_unit'] or '', 1
    ))
    imported += 1
    print('  + {} {} ({})'.format(eq['name'], eq['plate'] or '', org_name))

new.commit()
old.close()
new.close()

print('')
print('Imported: {}'.format(imported))
print('Skipped (already exist): {}'.format(skipped))
if org_not_found:
    print('Organizations not found in new DB:')
    for name in org_not_found:
        print('  - ' + name)
print('Done!')