"""
Patch 2: add missing orgs (Chorva, Lattiko, Azteks, Terminal)
then re-process the 33 skipped equipment records from Excel.
Run from C:\\transport-report\\
"""
import sqlite3, os, csv
import pandas as pd
from datetime import datetime

DB_PATH = os.path.join('instance', 'transport.db')
EXCEL_FILE = 'Agroklas ter_Tekhnika_Konsolidatsiya.xlsx'

# Try both filename variants
for fname in ['Агрокластер_Техника_Консолидация.xlsx',
              'Agroklas ter_Tekhnika_Konsolidatsiya.xlsx']:
    if os.path.exists(fname):
        EXCEL_FILE = fname
        break

LOG_FILE = f'patch2_log_{datetime.now().strftime("%Y%m%d_%H%M%S")}.csv'

EXCEL_CAT_MAP = {
    '1. Юкори унумли техникалар': 'yukori',
    '2. Чопик тракторлар':        'mtz',
    '3. Катнов тракторлар':       'qatnov',
    '4. Мини тракторлар':         'mini',
    '5. Комбайнлар':              'combine',
    '6. Махсус техникалар':       'special',
    '7. Юк ташувчи техникалар':   'yuk_transport',
    '8. Мотоцикл':                'motorcycle',
    '9. Йуловчи ташиш техникаси': 'passenger',
}

# Also handle Cyrillic versions
EXCEL_CAT_MAP_CYR = {
    '1. \u042e\u049b\u043e\u0440\u0438 \u0443\u043d\u0443\u043c\u043b\u0438 \u0442\u0435\u0445\u043d\u0438\u043a\u0430\u043b\u0430\u0440': 'yukori',
    '2. \u0427\u043e\u043f\u0438\u049b \u0442\u0440\u0430\u043a\u0442\u043e\u0440\u043b\u0430\u0440': 'mtz',
    '3. \u049a\u0430\u0442\u043d\u043e\u0432 \u0442\u0440\u0430\u043a\u0442\u043e\u0440\u043b\u0430\u0440': 'qatnov',
    '4. \u041c\u0438\u043d\u0438 \u0442\u0440\u0430\u043a\u0442\u043e\u0440\u043b\u0430\u0440': 'mini',
    '5. \u041a\u043e\u043c\u0431\u0430\u0439\u043d\u043b\u0430\u0440': 'combine',
    '6. \u041c\u0430\u0445\u0441\u0443\u0441 \u0442\u0435\u0445\u043d\u0438\u043a\u0430\u043b\u0430\u0440': 'special',
    '7. \u042e\u043a \u0442\u0430\u0448\u0443\u0432\u0447\u0438 \u0442\u0435\u0445\u043d\u0438\u043a\u0430\u043b\u0430\u0440': 'yuk_transport',
    '8. \u041c\u043e\u0442\u043e\u0446\u0438\u043a\u043b': 'motorcycle',
    '9. \u0419\u045e\u043b\u043e\u0432\u0447\u0438 \u0442\u0430\u0448\u0438\u0448 \u0442\u0435\u0445\u043d\u0438\u043a\u0430\u0441\u0438': 'passenger',
}
EXCEL_CAT_MAP.update(EXCEL_CAT_MAP_CYR)

# Org mapping: Excel name -> canonical DB name
ORG_MAP = {
    'Чорва': 'Чорва',
    'Латтико': 'Латтико',
    'Азтекс': 'Азтекс',
    'Терминал': 'Терминал',
}


def normalize_plate(p):
    if not p or str(p).strip().lower() in ('', 'nan', 'без номера',
                                            'без номер', 'без номера'):
        return ''
    return str(p).strip().upper().replace(' ', '')


conn = sqlite3.connect(DB_PATH)
cur = conn.cursor()

# Step 1: Show current state of missing orgs
print('Step 1: Checking missing orgs...')
cur.execute('SELECT MAX(sort_order) FROM organizations')
max_sort = (cur.fetchone()[0] or 20)

missing_orgs = ['Чорва', 'Латтико', 'Азтекс', 'Терминал']
for org_name in missing_orgs:
    cur.execute('SELECT id, name FROM organizations WHERE name = ?', (org_name,))
    row = cur.fetchone()
    if row:
        print(f'  EXISTS: id={row[0]} name={row[1]!r}')
    else:
        max_sort += 1
        cur.execute(
            'INSERT INTO organizations (name, short_name, sort_order) VALUES (?, ?, ?)',
            (org_name, org_name, max_sort)
        )
        print(f'  ADDED: {org_name!r}')

conn.commit()

# Rebuild org map
cur.execute('SELECT id, name FROM organizations')
org_map = {row[1]: row[0] for row in cur.fetchall()}
print(f'\nOrg map now has {len(org_map)} entries')

# Step 2: Read Excel and process only the orgs that were skipped
print('\nStep 2: Processing skipped equipment...')

df = pd.read_excel(EXCEL_FILE, header=1)
df.columns = ['num', 'category', 'eq_type', 'org', 'name', 'plate']
df = df[pd.to_numeric(df['num'], errors='coerce').notna()].copy()
df['plate'] = df['plate'].astype(str).str.strip()

# Only process orgs that were skipped
target_orgs = set(missing_orgs)
df_skipped = df[df['org'].isin(target_orgs)].copy()
print(f'Found {len(df_skipped)} rows for skipped orgs')

log = []
added = updated = skipped = 0

for _, row in df_skipped.iterrows():
    excel_org = str(row['org']).strip()
    org_id = org_map.get(excel_org)
    if not org_id:
        log.append({'action': 'SKIP', 'name': str(row['name']),
                    'plate': str(row['plate']), 'org': excel_org,
                    'detail': f'Org still not found: {excel_org!r}',
                    'status': 'SKIP_NO_ORG'})
        skipped += 1
        continue

    cat_str = str(row['category']).strip()
    cat_code = EXCEL_CAT_MAP.get(cat_str, '')
    if not cat_code:
        log.append({'action': 'SKIP', 'name': str(row['name']),
                    'plate': str(row['plate']), 'org': excel_org,
                    'detail': f'Unknown category: {cat_str!r}',
                    'status': 'SKIP_NO_CAT'})
        skipped += 1
        continue

    eq_type = str(row['eq_type']).strip()
    eq_name = str(row['name']).strip()
    excel_plate = normalize_plate(row['plate'])
    plate_raw = str(row['plate']).strip()

    # Find existing by plate or name
    found_id = None
    if excel_plate:
        cur.execute(
            "SELECT id FROM equipment WHERE REPLACE(UPPER(plate),' ','')=? AND organization_id=?",
            (excel_plate, org_id))
        row2 = cur.fetchone()
        if row2:
            found_id = row2[0]

    if not found_id:
        cur.execute(
            'SELECT id FROM equipment WHERE TRIM(name)=? AND organization_id=?',
            (eq_name, org_id))
        row2 = cur.fetchone()
        if row2:
            found_id = row2[0]

    if found_id:
        cur.execute(
            'UPDATE equipment SET category=?, eq_type=?, organization_id=? WHERE id=?',
            (cat_code, eq_type, org_id, found_id))
        log.append({'action': 'UPDATED', 'id': found_id, 'name': eq_name,
                    'plate': plate_raw, 'org': excel_org,
                    'category': cat_code, 'status': 'OK'})
        updated += 1
    else:
        cur.execute(
            'INSERT INTO equipment (name,plate,category,eq_type,organization_id,'
            'default_price,default_unit,is_active) VALUES (?,?,?,?,?,0,"",1)',
            (eq_name, plate_raw, cat_code, eq_type, org_id))
        new_id = cur.lastrowid
        log.append({'action': 'ADDED', 'id': new_id, 'name': eq_name,
                    'plate': plate_raw, 'org': excel_org,
                    'category': cat_code, 'status': 'OK'})
        added += 1

conn.commit()
conn.close()

# Write log
with open(LOG_FILE, 'w', newline='', encoding='utf-8-sig') as f:
    fields = ['action', 'id', 'name', 'plate', 'org', 'category', 'detail', 'status']
    w = csv.DictWriter(f, fieldnames=fields, extrasaction='ignore')
    w.writeheader()
    w.writerows(log)

print(f'\nDone! Updated={updated}, Added={added}, Skipped={skipped}')
print(f'Log: {LOG_FILE}')
