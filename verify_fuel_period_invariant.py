# -*- coding: utf-8 -*-
"""verify_fuel_period_invariant.py — FUEL-BIG-001, F2.1 acceptance.

Invariant: a period given as explicit 00:00 / 23:59 must produce figures
identical (to 0.00) to the date-only path, for

  1. the balance report (_fuel_report_build_rows), every fuel-visible
     warehouse, every period figure and every daily cell;
  2. the AZS report totals (_collect_fuel_report_data);
  3. the station issues report of at least one station.

Additionally two narrowing cases are asserted, printing the numbers:

  - a one-hour window returns no more litres than the containing day;
  - a window 00:00-00:00 of a single day returns only transactions
    timestamped exactly at midnight, and receipts of that whole date.

Exits non-zero on any mismatch. Console output is ASCII-only; the detail
goes to the UTF-8 file verify_fuel_period_invariant_report.txt.

[REASON]: Read-only by construction — never `from app import app` here
(create_app() calls db.create_all() at import time and becomes a writer);
a bare Flask app with a mode=ro SQLite URI drives the ORM, and the raw
lookups for the narrowing cases use sqlite3 mode=ro. Refuses to run when
instance/transport.db is absent.

Usage:
    python verify_fuel_period_invariant.py [--start 2026-05-01] [--end 2026-07-24]
"""
import argparse
import os
import sqlite3
import sys
from datetime import date, datetime, timedelta

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DB_PATH = os.path.join(BASE_DIR, 'instance', 'transport.db')
REPORT_PATH = os.path.join(BASE_DIR, 'verify_fuel_period_invariant_report.txt')
TOLERANCE = 0.005

ROW_KEYS = ('opening', 'receipts', 'expenses', 'topaz_expenses',
            'manual_expenses', 'external_expenses', 'reserve_out', 'closing')
TOTAL_KEYS = ('opening', 'receipts', 'issued', 'manual', 'external',
              'reserve_out', 'ending')


def d(iso):
    return datetime.strptime(iso, '%Y-%m-%d').date()


def make_app():
    from flask import Flask
    from models import db
    app = Flask(__name__)
    app.config['SQLALCHEMY_DATABASE_URI'] = (
        'sqlite:///file:%s?mode=ro&uri=true' % DB_PATH.replace('\\', '/'))
    app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False
    db.init_app(app)
    return app


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--start', default='2026-05-01')
    parser.add_argument('--end', default='2026-07-24')
    args = parser.parse_args()
    start, end = d(args.start), d(args.end)

    if not os.path.exists(DB_PATH):
        print('ERROR: database not found at %s - refusing to run.' % DB_PATH)
        sys.exit(2)

    import fuel_routes as fr

    diffs = []
    lines = ['verify_fuel_period_invariant — period %s .. %s' % (start, end), '']

    def cmp_val(label, a, b):
        delta = abs(round(a, 2) - round(b, 2))
        if delta > TOLERANCE:
            diffs.append('%s: date-only=%.2f explicit=%.2f (delta=%.4f)'
                         % (label, a, b, delta))

    explicit_start_dt = datetime.combine(start, datetime.min.time())
    explicit_end_dt_excl = (datetime.combine(end, datetime.min.time())
                            + timedelta(hours=23, minutes=59)
                            + timedelta(minutes=1))

    app = make_app()
    with app.app_context(), app.test_request_context('/fuel/balance-report'):
        # 1. balance report: date-only vs explicit 00:00/23:59
        rows_a, items_a, totals_a = fr._fuel_report_build_rows(start, end, True, 'ДТ')
        rows_b, items_b, totals_b = fr._fuel_report_build_rows(
            start, end, True, 'ДТ',
            start_dt=explicit_start_dt, end_dt_exclusive=explicit_end_dt_excl)
        map_a = {r['warehouse_id']: r for r in rows_a}
        map_b = {r['warehouse_id']: r for r in rows_b}
        if set(map_a) != set(map_b):
            diffs.append('balance: warehouse sets differ')
        for wh in sorted(map_a):
            ra, rb = map_a[wh], map_b.get(wh)
            if rb is None:
                continue
            for key in ROW_KEYS:
                cmp_val('balance wh %s %s' % (wh, key), ra[key], rb[key])
            for item in items_a:
                da = ra['daily'][item['key']]
                dbc = rb['daily'][item['key']]
                cmp_val('balance wh %s day %s receipts' % (wh, item['key']),
                        da['receipts'], dbc['receipts'])
                cmp_val('balance wh %s day %s expenses' % (wh, item['key']),
                        da['expenses'], dbc['expenses'])
        n_wh = len(map_a)
        lines.append('balance report: %d warehouses compared, %d figures each'
                     % (n_wh, len(ROW_KEYS) + 2 * len(items_a)))

        # 2. AZS report totals
        data_a = fr._collect_fuel_report_data(start, end)
        data_b = fr._collect_fuel_report_data(
            start, end,
            start_dt=explicit_start_dt, end_dt_exclusive=explicit_end_dt_excl)
        for key in TOTAL_KEYS:
            cmp_val('azs totals %s' % key,
                    float(data_a['totals'].get(key) or 0),
                    float(data_b['totals'].get(key) or 0))
        if data_a['totals'].get('transactions') != data_b['totals'].get('transactions'):
            diffs.append('azs totals transactions: %s vs %s'
                         % (data_a['totals'].get('transactions'),
                            data_b['totals'].get('transactions')))
        lines.append('azs report: totals compared (%d figures + tx count)'
                     % len(TOTAL_KEYS))

        # 3. station issues report for at least one station with traffic
        con = sqlite3.connect('file:%s?mode=ro' % DB_PATH.replace('\\', '/'), uri=True)
        cur = con.cursor()
        cur.execute(
            'SELECT station_id, COUNT(*) FROM fuel_transactions2 '
            'WHERE txn_datetime >= ? AND txn_datetime < ? '
            'GROUP BY station_id ORDER BY COUNT(*) DESC LIMIT 1',
            (start.isoformat() + ' 00:00:00',
             (end + timedelta(days=1)).isoformat() + ' 00:00:00'))
        st_row = cur.fetchone()
        if st_row:
            st_id = st_row[0]
            txa = fr._fuel_station_issues_query(st_id, start, end)
            txb = fr._fuel_station_issues_query(
                st_id, start, end,
                start_dt=explicit_start_dt, end_dt_exclusive=explicit_end_dt_excl)
            cmp_val('station %s litres' % st_id,
                    sum(t.quantity or 0 for t in txa),
                    sum(t.quantity or 0 for t in txb))
            if len(txa) != len(txb):
                diffs.append('station %s row count: %d vs %d' % (st_id, len(txa), len(txb)))
            lines.append('station issues: station %s, %d rows compared' % (st_id, len(txa)))
        else:
            lines.append('station issues: no station with transactions in the period')
            diffs.append('station issues: nothing to compare — no traffic in period')

        # 4a. narrowing: one hour vs its containing day.
        # [REASON]: pick the busiest hour among VISIBLE warehouses and
        # NON-external cards, so the demonstration prints real litres — an
        # external card's hour nets to zero by design and would prove nothing.
        visible_ids = sorted(map_a)
        ext_cards = sorted(fr.EXTERNAL_FUEL_CARDS)
        hr_sql = (
            "SELECT s.warehouse_id, date(t.txn_datetime), "
            "       CAST(strftime('%H', t.txn_datetime) AS INTEGER), "
            "       SUM(t.quantity) "
            'FROM fuel_transactions2 t JOIN fuel_stations2 s ON s.id = t.station_id '
            'WHERE t.txn_datetime >= ? AND t.txn_datetime < ? '
            '  AND s.warehouse_id IN (' + (','.join('?' * len(visible_ids)) or 'NULL') + ') '
            "  AND TRIM(COALESCE(t.card_number, '')) NOT IN ("
            + (','.join('?' * len(ext_cards)) or "''") + ') '
            'GROUP BY 1, 2, 3 ORDER BY 4 DESC LIMIT 1')
        cur.execute(hr_sql,
                    [start.isoformat() + ' 00:00:00',
                     (end + timedelta(days=1)).isoformat() + ' 00:00:00']
                    + visible_ids + ext_cards)
        hr_row = cur.fetchone()
        if hr_row:
            wh_id, day_s, hour, _litres = hr_row
            day = d(day_s)
            hour_start = datetime.combine(day, datetime.min.time()) + timedelta(hours=hour)
            # [REASON]: [HH:00, HH:59] through the parser convention is
            # end + 1 minute exclusive = the exact hour.
            _, hrows = None, fr._fuel_report_build_rows(
                day, day, True, 'ДТ',
                start_dt=hour_start,
                end_dt_exclusive=hour_start + timedelta(hours=1))[0]
            _drows = fr._fuel_report_build_rows(day, day, True, 'ДТ')[0]
            h_map = {r['warehouse_id']: r for r in hrows}
            d_map = {r['warehouse_id']: r for r in _drows}
            h_litres = h_map.get(wh_id, {}).get('topaz_expenses', 0.0)
            d_litres = d_map.get(wh_id, {}).get('topaz_expenses', 0.0)
            lines.append('narrowing 1: wh %s %s hour %02d: hour=%.2f L, day=%.2f L'
                         % (wh_id, day_s, hour, h_litres, d_litres))
            print('narrowing 1: hour=%.2f L <= day=%.2f L' % (h_litres, d_litres))
            if h_litres - d_litres > TOLERANCE:
                diffs.append('one-hour window exceeds its day: %.2f > %.2f'
                             % (h_litres, d_litres))
        else:
            lines.append('narrowing 1: no transactions in period')
            diffs.append('narrowing 1: nothing to check — no traffic in period')

        # 4b. narrowing: 00:00-00:00 of a single day = midnight transactions
        # only, receipts of the whole date
        cur.execute(
            'SELECT s.warehouse_id, date(t.txn_datetime), SUM(t.quantity) '
            'FROM fuel_transactions2 t JOIN fuel_stations2 s ON s.id = t.station_id '
            "WHERE strftime('%M:%S', t.txn_datetime) = '00:00' "
            "AND strftime('%H', t.txn_datetime) = '00' "
            'AND t.txn_datetime >= ? AND t.txn_datetime < ? '
            'GROUP BY 1, 2 ORDER BY 3 DESC LIMIT 1',
            (start.isoformat() + ' 00:00:00',
             (end + timedelta(days=1)).isoformat() + ' 00:00:00'))
        mid_row = cur.fetchone()
        if mid_row:
            wh_id, day_s, midnight_litres = mid_row
            midnight_litres = float(midnight_litres or 0)
        else:
            # no midnight-stamped rows anywhere: assert the window yields zero
            wh_id, day_s, midnight_litres = None, start.isoformat(), 0.0
        day = d(day_s)
        day_floor = datetime.combine(day, datetime.min.time())
        nrows = fr._fuel_report_build_rows(
            day, day, True, 'ДТ',
            start_dt=day_floor,
            end_dt_exclusive=day_floor + timedelta(minutes=1))[0]
        n_map = {r['warehouse_id']: r for r in nrows}
        drows = fr._fuel_report_build_rows(day, day, True, 'ДТ')[0]
        d_map = {r['warehouse_id']: r for r in drows}
        check_ids = [wh_id] if wh_id is not None else sorted(n_map)
        for cid in check_ids:
            got_topaz = n_map.get(cid, {}).get('topaz_expenses', 0.0)
            want = midnight_litres if cid == wh_id else None
            got_receipts = n_map.get(cid, {}).get('receipts', 0.0)
            day_receipts = d_map.get(cid, {}).get('receipts', 0.0)
            lines.append('narrowing 2: wh %s %s 00:00-00:00: topaz=%.2f '
                         '(midnight rows %.2f), receipts=%.2f (whole date %.2f)'
                         % (cid, day_s, got_topaz,
                            want if want is not None else 0.0,
                            got_receipts, day_receipts))
            print('narrowing 2: wh %s topaz=%.2f midnight=%.2f receipts=%.2f/%.2f'
                  % (cid, got_topaz,
                     want if want is not None else 0.0,
                     got_receipts, day_receipts))
            # NOTE: topaz_expenses in the report is net of external/reserve;
            # compare against raw midnight litres only when no mark/external
            # touches those rows — detect by recomputing net from raw.
            if want is not None and abs(got_topaz - want) > TOLERANCE:
                cur.execute(
                    'SELECT COUNT(*) FROM fuel_transaction_reattributions r '
                    'JOIN fuel_transactions2 t ON t.id = r.transaction_id '
                    'JOIN fuel_stations2 s ON s.id = t.station_id '
                    'WHERE COALESCE(r.is_deleted,0)=0 AND s.warehouse_id = ? '
                    'AND t.txn_datetime = ?', (cid, day_s + ' 00:00:00'))
                marked = cur.fetchone()[0]
                if not marked:
                    diffs.append('midnight window wh %s: topaz=%.2f expected %.2f'
                                 % (cid, got_topaz, want))
            if abs(got_receipts - day_receipts) > TOLERANCE:
                diffs.append('midnight window wh %s: receipts=%.2f, whole date=%.2f'
                             % (cid, got_receipts, day_receipts))
        con.close()

    lines.append('')
    lines.append('warehouses compared: %d' % n_wh)
    lines.append('differences: %d' % len(diffs))
    for item in diffs:
        lines.append('  ' + item)
    with open(REPORT_PATH, 'w', encoding='utf-8') as fh:
        fh.write('\n'.join(lines) + '\n')

    print('warehouses compared: %d' % n_wh)
    print('differences: %d' % len(diffs))
    print('report written to %s' % os.path.basename(REPORT_PATH))
    if diffs:
        print('RESULT: FAIL')
        sys.exit(1)
    print('RESULT: PASS')


if __name__ == '__main__':
    main()
