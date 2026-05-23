"""
Reclassify existing yukori equipment to combine or special based on eq_type.
Run BEFORE migrate_equipment_excel.py
"""
import sqlite3, os

DB_PATH = os.path.join('instance', 'transport.db')

# Rules: if eq_type contains any of these strings -> reclassify
COMBINE_KEYWORDS = ['Ростсельмаш', 'Комбайн', 'JOHN DEERE', 'комбайн', 'AXIAL', 'CASE CE', 'JAGUAR', 'TC5']
SPECIAL_KEYWORDS = ['Погрузчик', 'Экскаватор', 'экскаватор', 'HYUNDAI', 'Бульдозер', 'Автокран', 'Бензовоз', 'Водовоз']

conn = sqlite3.connect(DB_PATH)
cur = conn.cursor()

cur.execute("SELECT id, name, eq_type, category FROM equipment WHERE category = 'yukori'")
rows = cur.fetchall()

reclassified = 0
for eq_id, name, eq_type, cat in rows:
    new_cat = None
    if eq_type:
        for kw in COMBINE_KEYWORDS:
            if kw.lower() in eq_type.lower():
                new_cat = 'combine'
                break
        if not new_cat:
            for kw in SPECIAL_KEYWORDS:
                if kw.lower() in eq_type.lower():
                    new_cat = 'special'
                    break
    if new_cat:
        cur.execute('UPDATE equipment SET category = ? WHERE id = ?', (new_cat, eq_id))
        print(f'  {cat} -> {new_cat}: {repr(name)} ({repr(eq_type)})')
        reclassified += 1

conn.commit()
conn.close()
print(f'Reclassified {reclassified} equipment records.')
