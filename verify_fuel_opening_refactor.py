# -*- coding: utf-8 -*-
"""verify_fuel_opening_refactor.py — FUEL-BIG-001, acceptance gate for the
_fuel_opening_for extraction (and the hard gate of the whole increment).

For every warehouse visible to the fuel UI, over a fixed period, this script
computes ``opening``, ``receipts``, ``expenses`` and ``closing`` twice:

  1. independently, from raw SQL over a read-only sqlite3 connection,
     following the balance formula
         balance = initial + receipts - Topaz issues - manual expense
                   + external add-back + reattributed out - reattributed in
     with the opening rolled forward over [initial_date, start_date); and

  2. through _fuel_report_build_rows (the code path the balance report uses,
     which now calls _fuel_opening_for).

Zero differences beyond 0.00 (two decimal places) on every warehouse is the
pass condition; the script exits non-zero otherwise and prints the number of
warehouses compared.

[REASON]: Read-only by construction. Never `from app import app` here —
create_app() calls db.create_all() at import time, which would make this
script a writer. A bare Flask app is built with a mode=ro SQLite URI for the
ORM half; the raw-SQL half opens its own sqlite3 mode=ro connection. The
script refuses to run if instance/transport.db is absent — sqlite3.connect()
would otherwise create an empty database.

Console output is ASCII-only (NSSM/Windows log encoding); the detailed
per-warehouse report, including Cyrillic warehouse names, goes to the UTF-8
file verify_fuel_opening_refactor_report.txt.

Usage:
    python verify_fuel_opening_refactor.py [--start 2026-05-01] [--end 2026-07-24]
"""
import argparse
import os
import sqlite3
import sys
from datetime import date, datetime, timedelta

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DB_PATH = os.path.join(BASE_DIR, 'instance', 'transport.db')
REPORT_PATH = os.path.join(BASE_DIR, 'verify_fuel_opening_refactor_report.txt')
FUEL_TYPE = 'ДТ'
TOLERANCE = 0.005  # equality at two decimal places


def fail(msg, code=2):
    print(msg)
    sys.exit(code)


def d(iso):
    return datetime.strptime(iso, '%Y-%m-%d').date()


def day_floor(day):
    """SQLite text bound for 00:00 of a date (matches SQLAlchemy storage)."""
    return day.isoformat() + ' 00:00:00'


def open_ro(path):
    return sqlite3.connect('file:%s?mode=ro' % path.replace('\\', '/'), uri=True)


def visible_warehouse_ids(cur, target_topaz_ids):
    cur.execute(
        'SELECT DISTINCT warehouse_id FROM fuel_stations2 '
        'WHERE warehouse_id IS NOT NULL AND topaz_id IN (%s)'
        % ','.join('?' * len(target_topaz_ids)),
        list(target_topaz_ids))
    ids = {r[0] for r in cur.fetchall()}
    cur.execute('SELECT id FROM fuel_warehouses WHERE show_in_ui = 1')
    ids.update(r[0] for r in cur.fetchall())
    return sorted(ids)


def scalar(cur, sql, params):
    cur.execute(sql, params)
    row = cur.fetchone()
    return float(row[0] or 0)


def sum_receipts(cur, wh_id, date_from, date_to):
    return scalar(cur,
                  'SELECT COALESCE(SUM(quantity), 0) FROM fuel_receipts2 '
                  'WHERE warehouse_id = ? AND fuel_type = ? '
                  'AND receipt_date >= ? AND receipt_date <= ?',
                  (wh_id, FUEL_TYPE, date_from.isoformat(), date_to.isoformat()))


def sum_topaz(cur, wh_id, dt_from, dt_to_excl):
    return scalar(cur,
                  'SELECT COALESCE(SUM(t.quantity), 0) '
                  'FROM fuel_transactions2 t '
                  'JOIN fuel_stations2 s ON s.id = t.station_id '
                  'WHERE s.warehouse_id = ? AND t.fuel_type = ? '
                  'AND t.txn_datetime >= ? AND t.txn_datetime < ?',
                  (wh_id, FUEL_TYPE, day_floor(dt_from), day_floor(dt_to_excl)))


def sum_manual(cur, wh_id, date_from, date_to):
    return scalar(cur,
                  'SELECT COALESCE(SUM(quantity), 0) FROM fuel_manual_expenses '
                  'WHERE warehouse_id = ? AND fuel_type = ? '
                  'AND COALESCE(is_deleted, 0) = 0 '
                  'AND expense_date >= ? AND expense_date <= ?',
                  (wh_id, FUEL_TYPE, date_from.isoformat(), date_to.isoformat()))


def sum_external(cur, wh_id, external_cards, lower, upper_excl):
    """External-card Topaz issues, each card only from max(lower, card start)."""
    total = 0.0
    for card, start in external_cards.items():
        eff = lower if lower >= start else start
        if eff >= upper_excl:
            continue
        total += scalar(cur,
                        'SELECT COALESCE(SUM(t.quantity), 0) '
                        'FROM fuel_transactions2 t '
                        'JOIN fuel_stations2 s ON s.id = t.station_id '
                        'WHERE s.warehouse_id = ? AND t.fuel_type = ? '
                        'AND t.card_number = ? '
                        'AND t.txn_datetime >= ? AND t.txn_datetime < ?',
                        (wh_id, FUEL_TYPE, card, day_floor(eff), day_floor(upper_excl)))
    return total


def load_reattributions(cur, external_cards):
    """All active reserve marks with source/target and the external flag."""
    cur.execute(
        'SELECT s.warehouse_id, r.target_warehouse_id, t.txn_datetime, '
        '       t.quantity, t.card_number '
        'FROM fuel_transaction_reattributions r '
        'JOIN fuel_transactions2 t ON t.id = r.transaction_id '
        'JOIN fuel_stations2 s ON s.id = t.station_id '
        'WHERE COALESCE(r.is_deleted, 0) = 0 AND t.fuel_type = ?',
        (FUEL_TYPE,))
    rows = []
    for src, tgt, txn_dt, qty, card in cur.fetchall():
        dt = str(txn_dt)[:19]
        is_ext = False
        card_s = str(card or '').strip()
        if card_s in external_cards:
            if dt >= day_floor(external_cards[card_s]):
                is_ext = True
        if is_ext:
            # [REASON]: FUEL-RESERVE section 7 — an external-fuel transaction
            # never moves litres via a mark, exactly as the code drops it.
            continue
        rows.append((src, tgt, dt, float(qty or 0)))
    return rows


def reattr_sums(marks, wh_id, lower, upper_excl):
    lo, hi = day_floor(lower), day_floor(upper_excl)
    out = sum(q for s, t, dt, q in marks if s == wh_id and lo <= dt < hi)
    inn = sum(q for s, t, dt, q in marks if t == wh_id and lo <= dt < hi)
    return out, inn


def raw_figures(cur, wh_id, start, end, external_cards, marks):
    cur.execute(
        'SELECT quantity, balance_date FROM fuel_initial_balances '
        'WHERE warehouse_id = ? AND fuel_type = ? '
        'ORDER BY balance_date ASC, id ASC LIMIT 1', (wh_id, FUEL_TYPE))
    row = cur.fetchone()

    if row is None:
        opening = 0.0
    else:
        qty = float(row[0] or 0)
        ib_date = d(str(row[1])[:10])
        if ib_date > start:
            opening = 0.0
        elif ib_date == start:
            opening = qty
        else:
            r_out_b, r_in_b = reattr_sums(marks, wh_id, ib_date, start)
            opening = (qty
                       + sum_receipts(cur, wh_id, ib_date, start - timedelta(days=1))
                       - sum_topaz(cur, wh_id, ib_date, start)
                       - sum_manual(cur, wh_id, ib_date, start - timedelta(days=1))
                       + sum_external(cur, wh_id, external_cards, ib_date, start)
                       + r_out_b - r_in_b)

    receipts = sum_receipts(cur, wh_id, start, end)
    topaz_raw = sum_topaz(cur, wh_id, start, end + timedelta(days=1))
    external = sum_external(cur, wh_id, external_cards, start, end + timedelta(days=1))
    r_out, r_in = reattr_sums(marks, wh_id, start, end + timedelta(days=1))
    manual = sum_manual(cur, wh_id, start, end)
    expenses = (topaz_raw - external - r_out + r_in) + manual
    closing = opening + receipts - expenses
    return {
        'opening': round(opening, 2),
        'receipts': round(receipts, 2),
        'expenses': round(expenses, 2),
        'closing': round(closing, 2),
    }


def orm_rows(start, end):
    """_fuel_report_build_rows output over a read-only SQLAlchemy engine."""
    from flask import Flask
    from models import db
    import fuel_routes

    app = Flask(__name__)
    app.config['SQLALCHEMY_DATABASE_URI'] = (
        'sqlite:///file:%s?mode=ro&uri=true' % DB_PATH.replace('\\', '/'))
    app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False
    db.init_app(app)
    # No db.create_all() — this connection must stay a reader.
    with app.app_context(), app.test_request_context('/fuel/balance-report'):
        rows, _items, _totals = fuel_routes._fuel_report_build_rows(
            start_date=start, end_date=end, show_zero=True, fuel_type=FUEL_TYPE)
    return {r['warehouse_id']: r for r in rows}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--start', default='2026-05-01')
    parser.add_argument('--end', default='2026-07-24')
    args = parser.parse_args()
    start, end = d(args.start), d(args.end)

    if not os.path.exists(DB_PATH):
        fail('ERROR: database not found at %s - refusing to run.' % DB_PATH)

    import fuel_routes
    external_cards = dict(fuel_routes.EXTERNAL_FUEL_CARDS)
    target_topaz = sorted(fuel_routes.FUEL_TARGET_TOPAZ_IDS)

    con = open_ro(DB_PATH)
    try:
        cur = con.cursor()
        wh_ids = visible_warehouse_ids(cur, target_topaz)
        cur.execute('SELECT id, name FROM fuel_warehouses')
        names = dict(cur.fetchall())
        marks = load_reattributions(cur, external_cards)
        raw = {wh: raw_figures(cur, wh, start, end, external_cards, marks)
               for wh in wh_ids}
    finally:
        con.close()

    orm = orm_rows(start, end)

    diffs = []
    lines = ['verify_fuel_opening_refactor — period %s .. %s' % (start, end), '']
    if set(orm) != set(raw):
        diffs.append('warehouse sets differ: raw=%s orm=%s'
                     % (sorted(raw), sorted(orm)))
    for wh in wh_ids:
        r = raw.get(wh)
        o = orm.get(wh)
        line = 'wh %s (%s): ' % (wh, names.get(wh, '?'))
        if o is None:
            lines.append(line + 'MISSING in report output')
            continue
        parts = []
        for key in ('opening', 'receipts', 'expenses', 'closing'):
            delta = abs(r[key] - o[key])
            mark = 'OK' if delta <= TOLERANCE else 'DIFF'
            parts.append('%s raw=%.2f report=%.2f %s' % (key, r[key], o[key], mark))
            if delta > TOLERANCE:
                diffs.append('wh %s %s: raw=%.2f report=%.2f (delta=%.4f)'
                             % (wh, key, r[key], o[key], delta))
        lines.append(line + '; '.join(parts))

    lines.append('')
    lines.append('warehouses compared: %d' % len(wh_ids))
    lines.append('differences beyond 0.00: %d' % len(diffs))
    for item in diffs:
        lines.append('  ' + item)

    with open(REPORT_PATH, 'w', encoding='utf-8') as fh:
        fh.write('\n'.join(lines) + '\n')

    print('warehouses compared: %d' % len(wh_ids))
    print('differences beyond 0.00: %d' % len(diffs))
    print('report written to %s' % os.path.basename(REPORT_PATH))
    if diffs:
        print('RESULT: FAIL')
        sys.exit(1)
    print('RESULT: PASS')


if __name__ == '__main__':
    main()
