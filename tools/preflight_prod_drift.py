# -*- coding: utf-8 -*-
"""preflight_prod_drift.py -- schema drift check for the PR #8..#15 deploy.

Production runs the code of PR #6 and PR #7 while three migrations that
shipped with PR #6 are NOT in schema_migrations:

    SPARE_PARTS_UNITS
    SPARE_PARTS_ACTS_PERMISSION
    SPARE_PARTS_SKU_UNIQUENESS

app.py calls db.create_all() on every start, which silently creates a
MISSING TABLE (empty) but never adds a missing column, index or seed row.
This script establishes what is actually in the production database, and
pre-computes what each pending migration will do when it runs.

READ ONLY. Only SELECT and PRAGMA statements. Safe with the service up.

Console output is pure ASCII. The full report, including Cyrillic values,
goes to a UTF-8 file.

Run:
  cd C:\\transport-report
  & "C:\\Program Files\\Python314\\python.exe" preflight_prod_drift.py

Result file: preflight_prod_drift.txt (UTF-8)
"""

import io
import os
import re
import sqlite3
import sys
from datetime import datetime

# [REASON]: adopted into tools/ from the untracked production copy
# (DEPLOY-DRIFT-001). ROOT must stay the repository root -- one level above
# tools/ -- so instance/transport.db and the report files resolve exactly
# as they did when this script lived at C:\transport-report directly.
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DB_PATH = os.path.join(ROOT, 'instance', 'transport.db')
OUT_PATH = os.path.join(ROOT, 'preflight_prod_drift.txt')

# Copied verbatim from migrate_spare_parts_units.py so the forecast below
# matches what the migration will actually do.
BASELINE_UNITS = [
    ('dona',     'sht',    'dona',   10),
    ('litr',     'l',      'litr',   20),
    ('kg',       'kg',     'kg',     30),
    ('komplekt', 'kompl',  'toplam', 40),
    ('metr',     'm',      'metr',   50),
]
BASELINE_CODES = {u[0] for u in BASELINE_UNITS}

ALIASES = {
    'dona': 'dona', 'шт': 'dona', 'шт.': 'dona', 'штук': 'dona',
    'штука': 'dona', 'дона': 'dona', 'pcs': 'dona', 'ед': 'dona',
    'ед.': 'dona',
    'litr': 'litr', 'л': 'litr', 'литр': 'litr', 'литров': 'litr',
    'l': 'litr', 'лит': 'litr',
    'kg': 'kg', 'кг': 'kg', 'кг.': 'kg', 'килограмм': 'kg',
    'komplekt': 'komplekt', 'компл': 'komplekt', 'компл.': 'komplekt',
    'комплект': 'komplekt', 'тўплам': 'komplekt', 'туплам': 'komplekt',
    'set': 'komplekt',
    'metr': 'metr', 'м': 'metr', 'м.': 'metr', 'метр': 'metr', 'm': 'metr',
}

LINES = []


def emit(text=''):
    LINES.append(text)


def table_exists(cur, name):
    cur.execute("SELECT name FROM sqlite_master WHERE type='table' AND name=?",
                (name,))
    return cur.fetchone() is not None


def columns(cur, table):
    cur.execute("PRAGMA table_info({0})".format(table))
    return [r[1] for r in cur.fetchall()]


def index_map(cur, table):
    """name -> unique flag (1/0) for every index on table."""
    cur.execute("PRAGMA index_list({0})".format(table))
    return {r[1]: r[2] for r in cur.fetchall()}


def scalar(cur, sql, params=()):
    cur.execute(sql, params)
    row = cur.fetchone()
    return row[0] if row else None


def slug(value, taken):
    """Same code derivation as migrate_spare_parts_units._slug()."""
    base = re.sub(r'[^a-z0-9]+', '_', value.strip().lower()).strip('_')
    if not base:
        base = 'legacy'
    base = base[:24]
    code = base
    n = 2
    while code in taken:
        code = '{0}_{1}'.format(base, n)
        n += 1
    return code


# ------------------------------------------------------------- 1. UNITS

def section_units(cur):
    emit('=' * 70)
    emit('1. SPARE_PARTS_UNITS (SP-F-024) -- pending migration')
    emit('=' * 70)

    exists = table_exists(cur, 'spare_part_units')
    emit('table spare_part_units      : {0}'.format(
        'EXISTS' if exists else 'ABSENT'))
    if exists:
        idx = index_map(cur, 'spare_part_units')
        emit('uq_spare_part_units_code    : {0}'.format(
            'present, unique' if idx.get('uq_spare_part_units_code') == 1
            else 'MISSING'))
        rows = scalar(cur, 'SELECT COUNT(*) FROM spare_part_units')
        emit('rows                        : {0}'.format(rows))
        if rows:
            cur.execute('SELECT code, name_ru, name_uz, is_active'
                        ' FROM spare_part_units ORDER BY id')
            for code, ru, uz, act in cur.fetchall():
                emit('    {0:<14} ru={1:<12} uz={2:<12} active={3}'.format(
                    code, ru, uz, act))
        else:
            emit('    -> EMPTY. Created by db.create_all(), never seeded.')
            emit('    -> The units directory has been silently degraded to')
            emit('       free text since PR #6 went live on production.')
    else:
        emit('    -> Absent. The migration will create and seed it.')
    emit('')

    emit('legacy unit values in real data (what the migration will scan):')
    values = {}
    for table in ('spare_part_request_items', 'spare_parts'):
        if not table_exists(cur, table):
            continue
        if 'unit' not in columns(cur, table):
            emit('  {0}: no unit column'.format(table))
            continue
        cur.execute("SELECT unit, COUNT(*) FROM {0} WHERE unit IS NOT NULL"
                    " AND trim(unit) != '' GROUP BY unit".format(table))
        for value, cnt in cur.fetchall():
            v = (value or '').strip()
            if v:
                values[v] = values.get(v, 0) + cnt
    if not values:
        emit('  (none)')
    for value in sorted(values):
        emit('  {0!r:<20} rows: {1}'.format(value, values[value]))
    emit('')

    emit('forecast -- rows the migration will AUTO-REGISTER:')
    taken = set(BASELINE_CODES)
    if exists:
        cur.execute('SELECT code FROM spare_part_units')
        taken |= {r[0] for r in cur.fetchall()}
    auto = 0
    for value in sorted(values):
        norm = value.lower()
        if norm in taken or norm in ALIASES:
            continue
        code = slug(norm, taken)
        taken.add(code)
        auto += 1
        emit('  {0!r} -> code {1!r}'.format(value, code))
    if auto == 0:
        emit('  (none -- every legacy value maps onto a baseline code)')
    emit('total auto-registered rows forecast: {0}'.format(auto))
    emit('')


# ----------------------------------------------- 2. ACTS PERMISSION

def section_acts_permission(cur):
    emit('=' * 70)
    emit('2. SPARE_PARTS_ACTS_PERMISSION (SP-F-002 + SP-F-019) -- pending')
    emit('=' * 70)

    if not table_exists(cur, 'app_modules'):
        emit('!! app_modules missing -- migration would FAIL loudly')
        emit('')
        return

    has_row = scalar(cur, "SELECT COUNT(*) FROM app_modules"
                          " WHERE code = 'spare_parts_acts'")
    emit("app_modules row 'spare_parts_acts' : {0}".format(
        'present' if has_row else 'MISSING'))
    if not has_row:
        emit('    -> Non-admin users currently cannot open write-off acts.')
    emit('')

    if table_exists(cur, 'user_module_permissions'):
        holders = scalar(cur,
                         "SELECT COUNT(DISTINCT user_id)"
                         " FROM user_module_permissions"
                         " WHERE module_code IN ('spare_parts_issue',"
                         " 'spare_parts_approve') AND has_access = 1")
        emit('users holding issue/approve       : {0}'.format(holders))
        pending = scalar(cur, """
            SELECT COUNT(DISTINCT p.user_id) FROM user_module_permissions p
            WHERE p.module_code IN ('spare_parts_issue', 'spare_parts_approve')
              AND p.has_access = 1
              AND NOT EXISTS (
                SELECT 1 FROM user_module_permissions a
                WHERE a.user_id = p.user_id
                  AND a.module_code = 'spare_parts_acts' AND a.has_access = 1)
        """)
        emit('forecast -- auto-grant rows to add: {0}'.format(pending))
        cur.execute("SELECT DISTINCT user_id FROM user_module_permissions"
                    " WHERE module_code IN ('spare_parts_issue',"
                    " 'spare_parts_approve') AND has_access = 1"
                    " ORDER BY user_id")
        ids = [str(r[0]) for r in cur.fetchall()]
        emit('affected user ids                 : {0}'.format(
            ', '.join(ids) if ids else 'none'))
    emit('')

    if table_exists(cur, 'spare_part_write_off_acts'):
        total = scalar(cur, 'SELECT COUNT(*) FROM spare_part_write_off_acts')
        emit('write-off acts on production      : {0}'.format(total))
        idx = index_map(cur, 'spare_part_write_off_acts')
        flag = idx.get('idx_spare_part_write_off_acts_request_id')
        emit('idx ..._request_id                : {0}'.format(
            'MISSING' if flag is None
            else ('present, UNIQUE' if flag == 1 else 'present, NOT unique')))
        cur.execute('SELECT request_id, COUNT(*) c'
                    ' FROM spare_part_write_off_acts'
                    ' GROUP BY request_id HAVING c > 1')
        dupes = cur.fetchall()
        if dupes:
            emit('!! BLOCKER -- requests with several acts (migration aborts):')
            for req_id, cnt in dupes:
                emit('     request #{0}: {1} acts'.format(req_id, cnt))
        else:
            emit('duplicate request_id groups       : 0  (index can be made'
                 ' UNIQUE)')
    emit('')


# ------------------------------------------------- 3. SKU UNIQUENESS

def section_sku_uniqueness(cur):
    emit('=' * 70)
    emit('3. SPARE_PARTS_SKU_UNIQUENESS (SP-F-008) -- pending migration')
    emit('=' * 70)

    if not table_exists(cur, 'spare_part_skus'):
        emit('!! spare_part_skus missing -- migration would FAIL loudly')
        emit('')
        return

    total = scalar(cur, 'SELECT COUNT(*) FROM spare_part_skus')
    active = scalar(cur, 'SELECT COUNT(*) FROM spare_part_skus'
                         ' WHERE is_active = 1')
    emit('SKU rows total / active           : {0} / {1}'.format(total, active))

    idx = index_map(cur, 'spare_part_skus')
    flag = idx.get('uq_spare_part_skus_normalized')
    emit('uq_spare_part_skus_normalized     : {0}'.format(
        'MISSING' if flag is None
        else ('present, UNIQUE' if flag == 1 else 'present, NOT unique')))

    cur.execute("""
        SELECT spare_part_id, lower(trim(brand)), lower(trim(article_number)),
               lower(trim(supplier)), COUNT(*) AS c
        FROM spare_part_skus
        WHERE is_active = 1
        GROUP BY 1, 2, 3, 4
        HAVING c > 1
    """)
    dupes = cur.fetchall()
    if dupes:
        emit('!! BLOCKER -- normalized duplicate SKU groups (migration aborts):')
        for part_id, brand, article, supplier, cnt in dupes:
            emit('     part #{0} [{1}|{2}|{3}] x{4}'.format(
                part_id, brand, article, supplier, cnt))
    else:
        emit('normalized duplicate groups       : 0  (index can be created)')
    emit('')


# --------------------------------------------------------- 4. NAME_UZ

def section_name_uz(cur):
    emit('=' * 70)
    emit('4. SPARE_PARTS_NAME_UZ (PR #9) -- ships WITH this release')
    emit('=' * 70)
    if not table_exists(cur, 'spare_parts'):
        emit('!! spare_parts missing -- migration would FAIL loudly')
        emit('')
        return
    cols = columns(cur, 'spare_parts')
    emit('spare_parts.name_uz column        : {0}'.format(
        'PRESENT' if 'name_uz' in cols else 'absent (expected before deploy)'))
    emit('spare_parts rows                  : {0}'.format(
        scalar(cur, 'SELECT COUNT(*) FROM spare_parts')))
    emit('')
    emit('NOTE: db.create_all() never adds a column to an existing table.')
    emit('If the new code starts before this migration runs, every query')
    emit('selecting name_uz raises "no such column". This migration is')
    emit('therefore ORDER-CRITICAL: it must run BEFORE the service starts.')
    emit('')


# ---------------------------------------------------- 5. REGISTRY RECAP

def section_registry(cur):
    emit('=' * 70)
    emit('0. MIGRATION REGISTRY -- what is pending')
    emit('=' * 70)
    if not table_exists(cur, 'schema_migrations'):
        emit('!! schema_migrations missing')
        emit('')
        return
    cur.execute('SELECT name FROM schema_migrations')
    recorded = {r[0] for r in cur.fetchall()}
    pending = [
        ('SPARE_PARTS_UNITS',            'PR #6  -- DRIFT: code already live'),
        ('SPARE_PARTS_ACTS_PERMISSION',  'PR #6  -- DRIFT: code already live'),
        ('SPARE_PARTS_SKU_UNIQUENESS',   'PR #6  -- DRIFT: code already live'),
        ('SPARE_PARTS_NAME_UZ',          'PR #9  -- ships with this release'),
        ('SPARE_PARTS_RESERVATIONS',     'PR #13 -- ships with this release'),
        ('SPARE_PARTS_MIN_LEVELS',       'PR #14 -- ships with this release'),
    ]
    for name, comment in pending:
        emit('  {0:<30} {1:<8} {2}'.format(
            name, 'APPLIED' if name in recorded else 'pending', comment))
    emit('')


def main():
    if not os.path.exists(DB_PATH):
        print('ERROR: database not found at ' + DB_PATH, file=sys.stderr)
        sys.exit(1)

    con = sqlite3.connect(DB_PATH)
    con.execute('PRAGMA busy_timeout=30000')
    try:
        cur = con.cursor()
        emit('SCHEMA DRIFT CHECK -- PRODUCTION, DEPLOY OF PR #8..#15')
        emit('db      : {0}'.format(DB_PATH))
        emit('snapshot: {0}'.format(datetime.now().isoformat(timespec='seconds')))
        emit('')
        section_registry(cur)
        section_units(cur)
        section_acts_permission(cur)
        section_sku_uniqueness(cur)
        section_name_uz(cur)
        emit('END OF DRIFT CHECK')
    finally:
        con.rollback()
        con.close()

    with io.open(OUT_PATH, 'w', encoding='utf-8', newline='\r\n') as fh:
        fh.write(u'\n'.join(LINES) + u'\n')

    print('OK: drift report written to ' + OUT_PATH)
    print('Lines: {0}'.format(len(LINES)))
    print('Open it in the IDE (UTF-8) and send the content.')


if __name__ == '__main__':
    main()
