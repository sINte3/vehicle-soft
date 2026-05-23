"""
Migrate existing v2 database to v3 (in-place, NO data loss).

What this script does:
1. Adds the new 'deficiencies' table for storing daily deficiency notes
2. Reclassifies existing trucks/dump trucks/tankers from category 'yukori'
   to the new 'yuk_transport' category

Run AFTER replacing app.py / models.py / excel_export.py with v3 versions:
    python migrate_to_v3.py
"""

import sqlite3
import os

DB_PATH = os.path.join(os.path.dirname(__file__), 'instance', 'transport.db')

# Equipment types/names that should become Yuk transport
YUK_TYPES = {'Самосвал', 'Грузовой', 'Бензовоз', 'Спецтехника'}
YUK_NAME_KEYWORDS = ['HOWO', 'MAN', 'Камаз', 'Kamaz', 'KAMAZ',
                     'Isuzu', 'ISUZU', 'Бензовоз', 'HYUNDAI 80']


def is_yuk_transport(name, eq_type):
    """Check if equipment should be classified as yuk_transport."""
    if eq_type and eq_type.strip() in YUK_TYPES:
        return True
    if name:
        for kw in YUK_NAME_KEYWORDS:
            if kw in name:
                return True
    return False


def migrate():
    if not os.path.exists(DB_PATH):
        print(f'ERROR: Database not found at {DB_PATH}')
        print('Run init_data.py first to create a fresh database.')
        return

    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()

    # ─── Step 1: Create deficiencies table ──────────────────────────
    print('Step 1: Creating deficiencies table...')
    cur.execute("""
        CREATE TABLE IF NOT EXISTS deficiencies (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            work_date DATE NOT NULL,
            sort_order INTEGER DEFAULT 0,
            text TEXT NOT NULL,
            organization_id INTEGER,
            created_by INTEGER,
            created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (organization_id) REFERENCES organizations(id),
            FOREIGN KEY (created_by) REFERENCES users(id)
        )
    """)
    cur.execute("""
        CREATE INDEX IF NOT EXISTS ix_deficiencies_date
        ON deficiencies(work_date)
    """)
    print('  OK: deficiencies table ready')

    # ─── Step 2: Reclassify equipment ────────────────────────────────
    print()
    print('Step 2: Reclassifying trucks as Yuk transport...')

    cur.execute('SELECT id, name, plate, category, eq_type FROM equipment')
    rows = cur.fetchall()

    reclassified = 0
    skipped = 0
    examples = []
    for row in rows:
        eid, name, plate, category, eq_type = row
        if category == 'yuk_transport':
            skipped += 1
            continue
        if is_yuk_transport(name, eq_type):
            cur.execute('UPDATE equipment SET category = ? WHERE id = ?',
                        ('yuk_transport', eid))
            reclassified += 1
            if len(examples) < 10:
                examples.append(f'{name} {plate or ""}'.strip())

    conn.commit()
    print(f'  Reclassified: {reclassified} equipment')
    if examples:
        print('  Examples:')
        for ex in examples:
            print(f'    - {ex}')

    # ─── Step 3: Stats ───────────────────────────────────────────────
    print()
    print('Step 3: Final statistics:')
    cur.execute("SELECT category, COUNT(*) FROM equipment GROUP BY category ORDER BY category")
    for cat, cnt in cur.fetchall():
        names = {'mtz': 'MTZ Tractors', 'yukori': 'High-perf & special',
                 'yuk_transport': 'Yuk transport (trucks)'}
        print(f'  {names.get(cat, cat):25s}: {cnt}')

    conn.close()
    print()
    print('OK: Migration completed successfully!')


if __name__ == '__main__':
    migrate()
