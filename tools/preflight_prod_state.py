# -*- coding: utf-8 -*-
"""preflight_prod_state.py -- PROD DEPLOY 1..5 pre-flight snapshot (READ ONLY).

Purpose: capture the exact state of the production database BEFORE the
deploy of increments 1-5, so that (a) we know which migrations are already
recorded, (b) we know in advance how many reservation rows the
SPARE_PARTS_RESERVATIONS backfill will create on production, and (c) we
have a documented "no demo data on production" check.

This script NEVER writes to the database. It runs only SELECT and PRAGMA
statements. It is safe to run while the service is up.

Console output is pure ASCII (cp866/cp1251 safe). The full report,
including Cyrillic organization names, is written to a UTF-8 file.

Run:
  cd C:\\transport-report
  & "C:\\Program Files\\Python314\\python.exe" preflight_prod_state.py

Result file: preflight_prod_state.txt (UTF-8)
"""

import io
import os
import sqlite3
import sys
from datetime import datetime

# [REASON]: adopted into tools/ from the untracked production copy
# (DEPLOY-DRIFT-001). ROOT must stay the repository root -- one level above
# tools/ -- so instance/transport.db and the report files resolve exactly
# as they did when this script lived at C:\transport-report directly.
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DB_PATH = os.path.join(ROOT, 'instance', 'transport.db')
OUT_PATH = os.path.join(ROOT, 'preflight_prod_state.txt')

FLOAT_TOLERANCE = 0.001

LINES = []


def emit(text=''):
    """Write a line to the report file buffer (may contain Cyrillic)."""
    LINES.append(text)


def table_exists(cur, name):
    cur.execute("SELECT name FROM sqlite_master WHERE type='table' AND name=?",
                (name,))
    return cur.fetchone() is not None


def index_exists(cur, name):
    cur.execute("SELECT name FROM sqlite_master WHERE type='index' AND name=?",
                (name,))
    return cur.fetchone() is not None


def columns(cur, table):
    cur.execute("PRAGMA table_info({0})".format(table))
    return [r[1] for r in cur.fetchall()]


def scalar(cur, sql, params=()):
    cur.execute(sql, params)
    row = cur.fetchone()
    return row[0] if row else None


# ---------------------------------------------------------------- sections

def section_environment(cur):
    emit('=' * 70)
    emit('1. ENVIRONMENT')
    emit('=' * 70)
    emit('db path      : {0}'.format(DB_PATH))
    emit('db size      : {0:,} bytes'.format(os.path.getsize(DB_PATH)))
    emit('journal_mode : {0}'.format(scalar(cur, 'PRAGMA journal_mode')))
    emit('user_version : {0}'.format(scalar(cur, 'PRAGMA user_version')))
    emit('snapshot at  : {0}'.format(datetime.now().isoformat(timespec='seconds')))
    emit('')


def section_migrations(cur):
    emit('=' * 70)
    emit('2. schema_migrations REGISTRY')
    emit('=' * 70)
    if not table_exists(cur, 'schema_migrations'):
        emit('!! schema_migrations table DOES NOT EXIST on this database')
        emit('')
        return
    cur.execute('SELECT id, name, applied_at FROM schema_migrations ORDER BY id')
    rows = cur.fetchall()
    emit('recorded migrations: {0}'.format(len(rows)))
    for r in rows:
        emit('  {0:>4}  {1:<45} {2}'.format(r[0], r[1], r[2]))
    names = {r[1] for r in rows}
    emit('')
    for target in ('SPARE_PARTS_RESERVATIONS', 'SPARE_PARTS_MIN_LEVELS'):
        emit('  {0:<28} recorded: {1}'.format(
            target, 'YES' if target in names else 'no  <- expected before deploy'))
    emit('')


def section_schema_objects(cur):
    emit('=' * 70)
    emit('3. SCHEMA OBJECTS THIS RELEASE TOUCHES')
    emit('=' * 70)
    prereq = ('spare_part_requests', 'spare_part_request_items',
              'spare_part_warehouses', 'spare_part_skus',
              'spare_part_inventory')
    emit('prerequisite tables (must all exist):')
    for t in prereq:
        emit('  {0:<38} {1}'.format(t, 'OK' if table_exists(cur, t) else 'MISSING !!'))
    emit('')
    emit('tables created by this release (must NOT exist yet):')
    for t in ('spare_part_reservations', 'spare_part_min_levels'):
        exists = table_exists(cur, t)
        emit('  {0:<38} {1}'.format(t, 'ALREADY EXISTS !!' if exists else 'absent (expected)'))
        if exists:
            emit('     rows: {0}  <- deploy-before-migrate guard will FAIL if > 0'
                 .format(scalar(cur, 'SELECT COUNT(*) FROM {0}'.format(t))))
    emit('')
    emit('indexes created by this release (must NOT exist yet):')
    for i in ('idx_spare_part_reservations_wh_sku_status',
              'idx_spare_part_reservations_request_id',
              'uq_spare_part_reservations_active_item',
              'idx_spare_part_request_items_request_id',
              'uq_spare_part_min_levels_wh_sku'):
        emit('  {0:<44} {1}'.format(i, 'EXISTS' if index_exists(cur, i) else 'absent'))
    emit('')


def section_volumes(cur):
    emit('=' * 70)
    emit('4. SPARE PARTS VOLUMES ON PRODUCTION')
    emit('=' * 70)
    cur.execute('SELECT status, COUNT(*) FROM spare_part_requests'
                ' GROUP BY status ORDER BY status')
    emit('requests by status:')
    total = 0
    for status, cnt in cur.fetchall():
        total += cnt
        emit('  {0:<20} {1}'.format(status, cnt))
    emit('  {0:<20} {1}'.format('TOTAL', total))
    emit('')
    for t in ('spare_part_request_items', 'spare_part_warehouses',
              'spare_part_skus', 'spare_part_inventory',
              'spare_part_inventory_movements'):
        if table_exists(cur, t):
            emit('  {0:<38} {1}'.format(t, scalar(cur, 'SELECT COUNT(*) FROM ' + t)))
    emit('')
    emit('warehouses per organization:')
    cur.execute('SELECT o.id, o.name,'
                ' (SELECT COUNT(*) FROM spare_part_warehouses w'
                '  WHERE w.organization_id = o.id)'
                ' FROM organizations o ORDER BY o.id')
    for org_id, org_name, wh_count in cur.fetchall():
        emit('  org {0:<4} {1:<40} warehouses: {2}'.format(
            org_id, (org_name or '')[:40], wh_count))
    emit('')


def section_backfill_forecast(cur):
    """Dry simulation of migrate_spare_parts_reservations._backfill()."""
    emit('=' * 70)
    emit('5. RESERVATIONS BACKFILL FORECAST (simulation, nothing written)')
    emit('=' * 70)

    item_cols = columns(cur, 'spare_part_request_items')
    if 'sku_id' not in item_cols:
        emit('!! spare_part_request_items has no sku_id column on this database.')
        emit('   The backfill would insert 0 rows. Investigate before deploy.')
        emit('')
        return

    cur.execute('SELECT organization_id, id FROM spare_part_warehouses')
    wh_by_org = {r[0]: r[1] for r in cur.fetchall()}

    cur.execute('SELECT warehouse_id, sku_id, quantity FROM spare_part_inventory')
    remaining = {(r[0], r[1]): float(r[2] or 0) for r in cur.fetchall()}

    cur.execute("SELECT id, organization_id FROM spare_part_requests"
                " WHERE status = 'approved' ORDER BY request_date, id")
    approved = cur.fetchall()

    rows_forecast = 0
    requests_covered = 0
    items_short = 0
    skipped_no_warehouse = 0
    orgs_without_warehouse = {}

    for req_id, org_id in approved:
        warehouse_id = wh_by_org.get(org_id)
        if warehouse_id is None:
            skipped_no_warehouse += 1
            orgs_without_warehouse[org_id] = orgs_without_warehouse.get(org_id, 0) + 1
            continue
        cur.execute('SELECT id, sku_id, quantity FROM spare_part_request_items'
                    ' WHERE request_id = ? AND sku_id IS NOT NULL ORDER BY id',
                    (req_id,))
        items = cur.fetchall()
        if not items:
            continue
        requests_covered += 1
        for _item_id, sku_id, item_qty in items:
            requested = float(item_qty or 0)
            key = (warehouse_id, sku_id)
            available = remaining.get(key, 0.0)
            granted = round(max(0.0, min(requested, available)), 3)
            remaining[key] = available - granted
            if granted + FLOAT_TOLERANCE < requested:
                items_short += 1
            rows_forecast += 1

    emit('approved requests found            : {0}'.format(len(approved)))
    emit('  covered with reservation rows    : {0}'.format(requests_covered))
    emit('  skipped, organization has no wh  : {0}'.format(skipped_no_warehouse))
    emit('  approved but no SKU items        : {0}'.format(
        len(approved) - requests_covered - skipped_no_warehouse))
    emit('reservation rows the migration will insert: {0}'.format(rows_forecast))
    emit('items that will be short of stock  : {0}'.format(items_short))
    emit('')
    if orgs_without_warehouse:
        emit('organizations with approved requests but NO warehouse:')
        for org_id, cnt in sorted(orgs_without_warehouse.items()):
            name = scalar(cur, 'SELECT name FROM organizations WHERE id = ?', (org_id,))
            emit('  org {0:<4} {1:<40} approved requests: {2}'.format(
                org_id, (name or '?')[:40], cnt))
        emit('')


def section_demo_data(cur):
    emit('=' * 70)
    emit('6. DEMO / TEST DATA CHECK (expected: all zero except SMOKE-TEST)')
    emit('=' * 70)
    checks = [
        ('DEMO-2026H1 marker', '%DEMO-2026H1%'),
        ('REGR-TEST- prefix', '%REGR-TEST-%'),
        ('REGR2-TEST- prefix', '%REGR2-TEST-%'),
        ('SMOKE-TEST note (known, 3 drafts)', '%SMOKE-TEST%'),
    ]
    for label, pattern in checks:
        cnt = scalar(cur,
                     'SELECT COUNT(*) FROM spare_part_requests WHERE note LIKE ?',
                     (pattern,))
        emit('  {0:<38} {1}'.format(label, cnt))
    emit('')
    cur.execute("SELECT id, status, note FROM spare_part_requests"
                " WHERE note LIKE '%DEMO-2026H1%' OR note LIKE '%REGR-TEST-%'"
                "    OR note LIKE '%REGR2-TEST-%' OR note LIKE '%SMOKE-TEST%'"
                " ORDER BY id")
    hits = cur.fetchall()
    if hits:
        emit('matching requests:')
        for req_id, status, note in hits:
            emit('  #{0:<6} {1:<12} {2}'.format(req_id, status, (note or '')[:80]))
    else:
        emit('  no matching requests at all')
    emit('')


def main():
    if not os.path.exists(DB_PATH):
        print('ERROR: database not found at ' + DB_PATH, file=sys.stderr)
        sys.exit(1)

    con = sqlite3.connect(DB_PATH)
    con.execute('PRAGMA busy_timeout=30000')
    try:
        cur = con.cursor()
        emit('PRE-FLIGHT SNAPSHOT -- PRODUCTION DEPLOY OF INCREMENTS 1-5')
        emit('')
        section_environment(cur)
        section_migrations(cur)
        section_schema_objects(cur)
        section_volumes(cur)
        section_backfill_forecast(cur)
        section_demo_data(cur)
        emit('END OF SNAPSHOT')
    finally:
        con.rollback()   # nothing to commit; explicit no-write guarantee
        con.close()

    with io.open(OUT_PATH, 'w', encoding='utf-8', newline='\r\n') as fh:
        fh.write(u'\n'.join(LINES) + u'\n')

    print('OK: snapshot written to ' + OUT_PATH)
    print('Lines: {0}'.format(len(LINES)))
    print('Open it in the IDE (UTF-8) and send the content.')


if __name__ == '__main__':
    main()
