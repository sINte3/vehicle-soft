"""
Find equipment where name contains a plate number pattern,
and suggest/apply the split into name + plate fields.

Run modes:
  python fix_eq_names.py          - show report only (safe, no changes)
  python fix_eq_names.py --apply  - apply fixes to DB

Plate patterns detected:
  - Ends with '80 XXX YY'  e.g. 'MTZ 261 EA'  -> name='MTZ', plate='261 EA'
  - Contains '80 NNN XX'   e.g. 'Экскаватор 80 270 EA'
  - 2-3 digit number + 2 letters at end: '873 GA', '265 EA', '571 HA'
"""

import sqlite3, os, re, sys, csv
from datetime import datetime

DB_PATH = os.path.join('instance', 'transport.db')
APPLY   = '--apply' in sys.argv

# Regex: plate patterns at END of name string
# Pattern 1: optional '80 ' + 3 digits + space + 2 letters  (e.g. '261 EA', '80 270 EA')
# Pattern 2: 3 digits + space + 2 letters                   (e.g. '873 GA')
PLATE_RE = re.compile(
    r'\s+'
    r'((?:80\s+)?\d{2,4}\s+[A-Z\u0410-\u042f]{1,3}[A-Z\u0410-\u042f]?)'
    r'\s*$',
    re.IGNORECASE
)

# Also catch names that ARE just a plate (e.g. name='261 EA', plate='')
PURE_PLATE_RE = re.compile(
    r'^((?:80\s+)?\d{2,4}\s+[A-Z\u0410-\u042f]{2,3})$',
    re.IGNORECASE
)


def clean(s):
    return (s or '').strip()


conn = sqlite3.connect(DB_PATH)
cur  = conn.cursor()

cur.execute('''
    SELECT e.id, e.name, e.plate, o.name as org_name, e.category, e.eq_type
    FROM equipment e
    JOIN organizations o ON e.organization_id = o.id
    ORDER BY o.sort_order, e.name
''')
rows = cur.fetchall()

fixes    = []   # (id, old_name, old_plate, new_name, new_plate, reason)
no_fixes = []   # rows that look OK

for eq_id, name, plate, org, cat, eq_type in rows:
    name  = clean(name)
    plate = clean(plate)

    # Case 1: plate already filled AND name ends with same plate-like pattern -> name has extra
    m = PLATE_RE.search(name)
    if m:
        candidate_plate = m.group(1).strip()
        new_name = name[:m.start()].strip()

        if not new_name:
            # whole name was a plate
            fixes.append((eq_id, name, plate, name, plate, 'SKIP_EMPTY_NAME'))
            continue

        if plate and plate.replace(' ', '').upper() == candidate_plate.replace(' ', '').upper():
            # plate field already correct, just strip from name
            fixes.append((eq_id, name, plate, new_name, plate, 'STRIP_FROM_NAME'))
        elif not plate:
            # no plate field, move from name to plate
            fixes.append((eq_id, name, plate, new_name, candidate_plate, 'SPLIT_NAME_TO_PLATE'))
        else:
            # plate field has different value — flag for manual review
            fixes.append((eq_id, name, plate, new_name, plate, 'REVIEW_PLATE_MISMATCH'))
        continue

    # Case 2: name IS a plate number (pure plate, no real name)
    if PURE_PLATE_RE.match(name):
        fixes.append((eq_id, name, plate, name, plate, 'SKIP_NAME_IS_PLATE'))
        continue

    no_fixes.append((eq_id, name, plate, org))

# ── Report ────────────────────────────────────────────────────────────────────
print()
print('=' * 70)
print('EQUIPMENT NAME CLEANUP REPORT')
print('=' * 70)
print()

actionable = [f for f in fixes if f[5] in ('STRIP_FROM_NAME', 'SPLIT_NAME_TO_PLATE')]
review     = [f for f in fixes if f[5] == 'REVIEW_PLATE_MISMATCH']
skip       = [f for f in fixes if f[5].startswith('SKIP')]

print(f'Total equipment:    {len(rows)}')
print(f'Auto-fixable:       {len(actionable)}')
print(f'Needs review:       {len(review)}')
print(f'Skipped (OK/edge):  {len(skip)}')
print(f'Already clean:      {len(no_fixes)}')
print()

if actionable:
    print('-' * 70)
    print('AUTO-FIXABLE (name contains plate pattern):')
    print('-' * 70)
    print(f'  {"ID":>5}  {"OLD NAME":<35} {"OLD PLATE":<12} {"NEW NAME":<25} {"NEW PLATE"}')
    for eq_id, old_name, old_plate, new_name, new_plate, reason in actionable:
        print(f'  {eq_id:>5}  {old_name:<35} {old_plate or "---":<12} {new_name:<25} {new_plate}')
    print()

if review:
    print('-' * 70)
    print('NEEDS MANUAL REVIEW (name has plate but plate field differs):')
    print('-' * 70)
    for eq_id, old_name, old_plate, new_name, new_plate, reason in review:
        print(f'  ID={eq_id}  name={old_name!r}  plate={old_plate!r}  -> proposed name={new_name!r}')
    print()

# ── Apply ─────────────────────────────────────────────────────────────────────
if APPLY:
    if not actionable:
        print('Nothing to apply.')
    else:
        print(f'Applying {len(actionable)} fixes...')
        for eq_id, old_name, old_plate, new_name, new_plate, reason in actionable:
            cur.execute(
                'UPDATE equipment SET name = ?, plate = ? WHERE id = ?',
                (new_name, new_plate, eq_id)
            )
            print(f'  Fixed ID={eq_id}: {old_name!r} -> name={new_name!r} plate={new_plate!r}')
        conn.commit()
        print(f'Done. {len(actionable)} records updated.')

        # Save log
        log_file = f'fix_names_log_{datetime.now().strftime("%Y%m%d_%H%M%S")}.csv'
        with open(log_file, 'w', newline='', encoding='utf-8-sig') as f:
            w = csv.writer(f)
            w.writerow(['id', 'old_name', 'old_plate', 'new_name', 'new_plate', 'action'])
            for row in actionable:
                w.writerow(row)
        print(f'Log saved: {log_file}')
else:
    print('=' * 70)
    print('DRY RUN - no changes made.')
    print('To apply fixes run:  python fix_eq_names.py --apply')
    print('=' * 70)

conn.close()
