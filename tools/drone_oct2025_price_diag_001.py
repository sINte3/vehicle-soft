#!/usr/bin/env python3
"""Диагностика: почему internal_price_001 отказал на трёх строках.

Только чтение. Использует ТЕ ЖЕ find_row/september_rates, что и сама
миграция -- значит видит ровно то же, что видит она, без риска чуть-чуть
другой логики. Печатает заказчика и все ставки сентября по каждой строке в
UTF-8 файл (консоль -- только ASCII, кириллица иначе превращается в
"?????").

Run:
  & "C:\\Program Files\\Python314\\python.exe" tools\\drone_oct2025_price_diag_001.py --db instance\\transport.db --out instance\\price_diag_20260825.txt
"""
import argparse
import os
import sqlite3
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import migrate_drones_works_oct2025_internal_price_001 as m


def _ascii(text):
    return str(text).encode('ascii', 'replace').decode('ascii')


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--db', default=m.DB_PATH)
    parser.add_argument('--out', default=None)
    args = parser.parse_args()

    conn = sqlite3.connect(args.db)
    lines = []
    for source_file, sheet, source_row, area, how in m.ROWS:
        found = m.find_row(conn, source_file, sheet, source_row)
        lines.append('%s | row %d' % (sheet, source_row))
        if len(found) != 1:
            lines.append('  find_row: expected 1, found %d' % len(found))
            continue
        work_id, got_area, price, amount, customer_raw, customer_id = found[0]
        lines.append('  work_id=%s area=%.2f price=%r amount=%r' %
                     (work_id, float(got_area or 0), price, amount))
        lines.append('  customer_raw=%r customer_id=%r' %
                     (customer_raw, customer_id))
        if how == 'september':
            rates = m.september_rates(conn, customer_id, customer_raw)
            if not rates:
                lines.append('  September: NOTHING for this customer_id/'
                             'customer_raw (exact match only, no fuzzy)')
            for rate, cnt, ha in rates:
                lines.append('  September rate %.2f -- %d row(s), %.2f ha'
                             % (rate, cnt, ha))
        lines.append('')
    conn.close()

    text = '\n'.join(lines)
    if args.out:
        with open(args.out, 'w', encoding='utf-8') as f:
            f.write(text)
        print('Written: %s' % _ascii(args.out))
    else:
        print(_ascii(text))


if __name__ == '__main__':
    main()
