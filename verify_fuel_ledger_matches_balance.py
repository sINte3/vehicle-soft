# -*- coding: utf-8 -*-
"""verify_fuel_ledger_matches_balance.py — FUEL-BIG-001, F4.1 acceptance.

For EVERY fuel-visible warehouse over the given period, the ledger
(_fuel_ledger) must equal the balance report (_fuel_report_build_rows)
to the cent:

  - ledger opening            == balance-report opening
  - ledger receipts total     == receipts
  - ledger outgoing total     == expenses
  - ledger closing            == closing
  - external table total      == external_expenses
  - reattributions out        == reserve_out

All comparisons at two decimal places; all differences printed; exit
non-zero on any. Also prints the number of warehouses compared and the
number of ledger rows built per warehouse — that second number tells the
owner whether pagination is needed.

[REASON]: Read-only by construction — never `from app import app`
(create_app() runs db.create_all() at import time and becomes a writer);
a bare Flask app with a mode=ro SQLite URI drives both code paths. Refuses
to run when instance/transport.db is absent. ASCII console; UTF-8 detail
file verify_fuel_ledger_matches_balance_report.txt.

Usage:
    python verify_fuel_ledger_matches_balance.py [--start 2026-05-01] [--end 2026-07-24]
"""
import argparse
import os
import sys
from datetime import datetime, timedelta

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DB_PATH = os.path.join(BASE_DIR, 'instance', 'transport.db')
REPORT_PATH = os.path.join(BASE_DIR, 'verify_fuel_ledger_matches_balance_report.txt')
TOLERANCE = 0.005
FUEL_TYPE = 'ДТ'


def d(iso):
    return datetime.strptime(iso, '%Y-%m-%d').date()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--start', default='2026-05-01')
    parser.add_argument('--end', default='2026-07-24')
    args = parser.parse_args()
    start, end = d(args.start), d(args.end)

    if not os.path.exists(DB_PATH):
        print('ERROR: database not found at %s - refusing to run.' % DB_PATH)
        sys.exit(2)

    from flask import Flask
    from models import db
    from sqlalchemy import func
    import fuel_routes as fr
    from models import (FuelTransactionReattribution, FuelTransaction2,
                        FuelStation2)

    app = Flask(__name__)
    app.config['SQLALCHEMY_DATABASE_URI'] = (
        'sqlite:///file:%s?mode=ro&uri=true' % DB_PATH.replace('\\', '/'))
    app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False
    db.init_app(app)

    start_dt = datetime.combine(start, datetime.min.time())
    end_dt_exclusive = datetime.combine(end + timedelta(days=1), datetime.min.time())

    diffs = []
    lines = ['verify_fuel_ledger_matches_balance — period %s .. %s' % (start, end), '']

    with app.app_context(), app.test_request_context('/fuel/ledger'):
        rows, _items, _totals = fr._fuel_report_build_rows(
            start, end, show_zero=True, fuel_type=FUEL_TYPE)
        report = {r['warehouse_id']: r for r in rows}

        def reattr_out_for(wh_id):
            """Litres of active marks whose SOURCE is this warehouse, in the
            window, external excluded — computed directly, not via the maps."""
            q = (db.session.query(FuelTransaction2.quantity,
                                  FuelTransaction2.card_number,
                                  FuelTransaction2.txn_datetime)
                 .join(FuelTransactionReattribution,
                       FuelTransactionReattribution.transaction_id == FuelTransaction2.id)
                 .join(FuelStation2, FuelStation2.id == FuelTransaction2.station_id)
                 .filter(func.coalesce(FuelTransactionReattribution.is_deleted, 0) == 0,
                         FuelStation2.warehouse_id == wh_id,
                         FuelTransaction2.fuel_type == FUEL_TYPE,
                         FuelTransaction2.txn_datetime >= start_dt,
                         FuelTransaction2.txn_datetime < end_dt_exclusive))
            total = 0.0
            for qty, card, txn_dt in q.all():
                if fr._is_external_fuel_txn(card, txn_dt):
                    continue
                total += float(qty or 0)
            return total

        for wh_id in sorted(report):
            rep = report[wh_id]
            led = fr._fuel_ledger(wh_id, FUEL_TYPE, start_dt, end_dt_exclusive,
                                  start, end)
            main = led['main']
            checks = [
                ('opening', main['opening'], rep['opening']),
                ('receipts', main['total_in'], rep['receipts']),
                ('outgoing', main['total_out'], rep['expenses']),
                ('closing', main['closing'], rep['closing']),
                ('external', led['external']['total'], rep['external_expenses']),
                ('reserve_out', reattr_out_for(wh_id), rep['reserve_out']),
            ]
            n_rows = (len(main['rows'])
                      + (len(led['reserve']['rows']) if led['reserve'] else 0)
                      + len(led['external']['rows']))
            parts = []
            for key, lv, rv in checks:
                delta = abs(round(lv, 2) - round(rv, 2))
                mark = 'OK' if delta <= TOLERANCE else 'DIFF'
                parts.append('%s ledger=%.2f report=%.2f %s' % (key, lv, rv, mark))
                if delta > TOLERANCE:
                    diffs.append('wh %s %s: ledger=%.2f report=%.2f (delta=%.4f)'
                                 % (wh_id, key, lv, rv, delta))
            lines.append('wh %s (%s): rows=%d; %s'
                         % (wh_id, rep['warehouse'], n_rows, '; '.join(parts)))
            print('wh %s: rows=%d %s' % (wh_id, n_rows,
                                         'OK' if all('DIFF' not in p for p in parts)
                                         else 'DIFF'))

    lines.append('')
    lines.append('warehouses compared: %d' % len(report))
    lines.append('differences: %d' % len(diffs))
    for item in diffs:
        lines.append('  ' + item)
    with open(REPORT_PATH, 'w', encoding='utf-8') as fh:
        fh.write('\n'.join(lines) + '\n')

    print('warehouses compared: %d' % len(report))
    print('differences: %d' % len(diffs))
    print('report written to %s' % os.path.basename(REPORT_PATH))
    if diffs:
        print('RESULT: FAIL')
        sys.exit(1)
    print('RESULT: PASS')


if __name__ == '__main__':
    main()
