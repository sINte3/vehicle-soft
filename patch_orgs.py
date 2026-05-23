"""
Patch: fix org names for Chorva, Terminal, Lattiko, Azteks
then re-insert the 33 skipped equipment records.
Run from C:\\transport-report\\
"""
import sqlite3, os

DB_PATH = os.path.join('instance', 'transport.db')

# Old names in DB -> new canonical names
RENAMES = {
    'Бухоро Агрокластер Чорва':        'Чорва',
    'Пахтасаноаттранс':                 'Пахтасаноаттранс',
    'Бухоро Агрокластер ДЗЗ (Латтико)': 'Латтико',
    'Азтекс':                            'Азтекс',
    'Терминал':                          'Терминал',
}

conn = sqlite3.connect(DB_PATH)
cur = conn.cursor()

# Print current org names to see what's in DB
print('Current org names in DB:')
cur.execute('SELECT id, name FROM organizations ORDER BY sort_order')
orgs = cur.fetchall()
for oid, oname in orgs:
    print(f'  {oid}: {oname!r}')

print()
print('Renaming...')
for old, new in RENAMES.items():
    cur.execute('SELECT id FROM organizations WHERE name = ?', (old,))
    row = cur.fetchone()
    if row:
        if old != new:
            cur.execute('UPDATE organizations SET name = ? WHERE id = ?', (new, row[0]))
            print(f'  Renamed: {old!r} -> {new!r}')
        else:
            print(f'  Already correct: {old!r}')
    else:
        print(f'  NOT FOUND: {old!r}')

conn.commit()
conn.close()
print()
print('Done. Now run: python migrate_equipment_excel.py')
print('(It will skip already-processed records and only add the 33 skipped ones)')
