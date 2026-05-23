"""
Equipment migration from Excel to DB.
Reads: Agroklastr_Texnika_Konsolidatsiya.xlsx
Updates: instance/transport.db
Creates: migration_log.csv with results
"""
import sqlite3, os, csv
from datetime import datetime

# --- CONFIGURATION ---

EXCEL_FILE = 'Агрокластер_Техника_Консолидация.xlsx'
DB_PATH = os.path.join('instance', 'transport.db')
LOG_FILE = f'migration_log_{datetime.now().strftime("%Y%m%d_%H%M%S")}.csv'

# Organization name mapping: Excel name -> canonical name for DB
ORG_NAME_MAP = {
    'Когон ПТЗ':      'Когон ПТЗ',
    'Гиждувон ПТЗ':   'Гиждувон ПТЗ',
    'Шофиркон ПТЗ':   'Шофиркон ПТЗ',
    'Пешку ПТЗ':      'Пешку ПТЗ',
    'Мирзачул':       'Мирзачул',
    'Пешку Сервис':   'Пешку Сервис',
    'Бухоро Гарден':  'Бухоро Гарден',
    'Заминлари':      'Заминлари',
    'Пахтасаноаттранс': 'Пахтасаноаттранс',
    'Чорва':          'Чорва',
    'Латтико':        'Латтико',
    'Азтекс':        'Азтекс',
    'Терминал':       'Терминал',
    'Агрокластер':    'Агрокластер',
    'Глобал мегатекс': 'Глобал мегатекс',
    'Голд гранит':    'Голд гранит',
    'Уругчилик':      'Уругчилик',
}

# Old DB org names -> new canonical names (for updating existing orgs)
OLD_TO_NEW_ORG = {
    'Ғиждувон ПТЗ':                    'Гиждувон ПТЗ',
    'Мирзачўл ПТЗ':                    'Мирзачул',
    'Бухоро Сервис Агрокластер':       'Пешку Сервис',
    'Гарден Бухоро Агрокластер':       'Бухоро Гарден',
    'Бухоро Агрокластер Заминлари':    'Заминлари',
    'Бухоро Агрокластер Чорва':        'Чорва',
    'Бухоро Агрокластер ДЗЗ (Латтико)': 'Латтико',
}

# Category mapping: Excel category string -> DB category code
EXCEL_CAT_MAP = {
    '1. Юқори унумли техникалар': 'yukori',
    '2. Чопиқ тракторлар':        'mtz',
    '3. Қатнов тракторлар':       'qatnov',
    '4. Мини тракторлар':         'mini',
    '5. Комбайнлар':              'combine',
    '6. Махсус техникалар':       'special',
    '7. Юк ташувчи техникалар':   'yuk_transport',
    '8. Мотоцикл':                'motorcycle',
    '9. Йўловчи ташиш техникаси': 'passenger',
}


def normalize_plate(p):
    """Normalize plate for comparison: uppercase, strip spaces."""
    if not p or str(p).strip() in ('', 'nan', 'bez nomera', 'без номера', 'без номер'):
        return ''
    return str(p).strip().upper().replace(' ', '')


def run_migration():
    import pandas as pd

    # Read Excel
    df = pd.read_excel(EXCEL_FILE, header=1)
    df.columns = ['num', 'category', 'eq_type', 'org', 'name', 'plate']
    df = df[pd.to_numeric(df['num'], errors='coerce').notna()].copy()
    df['plate'] = df['plate'].astype(str).str.strip()
    df['category_code'] = df['category'].map(EXCEL_CAT_MAP)

    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()

    log = []  # list of dicts for CSV log

    # STEP 1: Rename existing organizations
    print('Step 1: Renaming organizations...')
    for old_name, new_name in OLD_TO_NEW_ORG.items():
        cur.execute('SELECT id FROM organizations WHERE name = ?', (old_name,))
        row = cur.fetchone()
        if row:
            cur.execute('UPDATE organizations SET name = ? WHERE id = ?', (new_name, row[0]))
            print(f'  Renamed: {repr(old_name)} -> {repr(new_name)}')
            log.append({'action': 'ORG_RENAMED', 'detail': f'{old_name} -> {new_name}', 'status': 'OK'})

    # STEP 2: Add new organizations (those not yet in DB)
    print('Step 2: Adding new organizations...')
    new_orgs = ['Агрокластер', 'Глобал мегатекс', 'Голд гранит', 'Уругчилик']
    cur.execute('SELECT MAX(sort_order) FROM organizations')
    max_sort = (cur.fetchone()[0] or 0) + 1
    for org_name in new_orgs:
        cur.execute('SELECT id FROM organizations WHERE name = ?', (org_name,))
        if not cur.fetchone():
            cur.execute('INSERT INTO organizations (name, short_name, sort_order) VALUES (?, ?, ?)',
                       (org_name, org_name, max_sort))
            max_sort += 1
            print(f'  Added org: {repr(org_name)}')
            log.append({'action': 'ORG_ADDED', 'detail': org_name, 'status': 'OK'})

    conn.commit()

    # Build org_name -> org_id mapping (after renames and additions)
    cur.execute('SELECT id, name FROM organizations')
    org_map = {row[1]: row[0] for row in cur.fetchall()}

    # STEP 3: Process equipment from Excel
    print('Step 3: Processing equipment...')

    updated = added = skipped = 0

    for _, row in df.iterrows():
        excel_org = str(row['org']).strip()
        canonical_org = ORG_NAME_MAP.get(excel_org, excel_org)
        org_id = org_map.get(canonical_org)

        if not org_id:
            log.append({'action': 'SKIP', 'detail': f'Org not found: {repr(excel_org)}',
                       'name': row['name'], 'plate': row['plate'], 'status': 'SKIP_NO_ORG'})
            skipped += 1
            continue

        excel_plate = normalize_plate(row['plate'])
        cat_code = str(row['category_code']).strip() if row['category_code'] else ''
        eq_type  = str(row['eq_type']).strip()
        eq_name  = str(row['name']).strip()

        if not cat_code or cat_code == 'nan':
            log.append({'action': 'SKIP', 'detail': f'Unknown category: {repr(str(row["category"]))}',
                       'name': eq_name, 'plate': row['plate'], 'status': 'SKIP_NO_CAT'})
            skipped += 1
            continue

        # Try to find existing equipment
        # Primary: match by normalized plate (if plate is not empty) within same org
        found_id = None
        if excel_plate:
            cur.execute('''SELECT id, name, category, eq_type, organization_id
                          FROM equipment
                          WHERE REPLACE(UPPER(plate), ' ', '') = ?
                          AND organization_id = ?''', (excel_plate, org_id))
            eq_row = cur.fetchone()
            if eq_row:
                found_id = eq_row[0]
            else:
                # Try across all orgs (plate is unique)
                cur.execute('''SELECT id, name, category, eq_type, organization_id
                              FROM equipment
                              WHERE REPLACE(UPPER(plate), ' ', '') = ?''', (excel_plate,))
                eq_row = cur.fetchone()
                if eq_row:
                    found_id = eq_row[0]

        # Secondary: match by name within org (if no plate match)
        if not found_id:
            cur.execute('''SELECT id FROM equipment
                          WHERE TRIM(name) = ? AND organization_id = ?''', (eq_name, org_id))
            eq_row = cur.fetchone()
            if eq_row:
                found_id = eq_row[0]

        if found_id:
            # Update existing: category, eq_type, org (in case org changed due to rename)
            cur.execute('''UPDATE equipment
                          SET category = ?, eq_type = ?, organization_id = ?
                          WHERE id = ?''', (cat_code, eq_type, org_id, found_id))
            log.append({'action': 'UPDATED', 'id': found_id, 'name': eq_name,
                       'plate': row['plate'], 'org': canonical_org,
                       'category': cat_code, 'eq_type': eq_type, 'status': 'OK'})
            updated += 1
        else:
            # Insert new equipment
            cur.execute('''INSERT INTO equipment
                          (name, plate, category, eq_type, organization_id,
                           default_price, default_unit, is_active)
                          VALUES (?, ?, ?, ?, ?, 0, '', 1)''',
                       (eq_name, str(row['plate']).strip(), cat_code, eq_type, org_id))
            new_id = cur.lastrowid
            log.append({'action': 'ADDED', 'id': new_id, 'name': eq_name,
                       'plate': row['plate'], 'org': canonical_org,
                       'category': cat_code, 'eq_type': eq_type, 'status': 'OK'})
            added += 1

    conn.commit()
    conn.close()

    # Write log CSV
    with open(LOG_FILE, 'w', newline='', encoding='utf-8-sig') as f:
        fields = ['action', 'id', 'name', 'plate', 'org', 'category', 'eq_type', 'detail', 'status']
        w = csv.DictWriter(f, fieldnames=fields, extrasaction='ignore')
        w.writeheader()
        w.writerows(log)

    print(f'\nDone! Updated={updated}, Added={added}, Skipped={skipped}')
    print(f'Log saved to: {LOG_FILE}')


if __name__ == '__main__':
    run_migration()
