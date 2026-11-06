"""FIN-RECON-003, 03.11.2026: the July books read a second, independent way,
and every registry reconciled to it by key «файл!лист!строка».

Read-only. Standard library only: zipfile + xml.etree. It deliberately does
NOT import tools/import_drone_works.py, openpyxl or anything from the
repository, so a shared bug cannot make both sides agree.

Its own rule of a job row, written from the book layout and not from the
importer:
  - the sheet title starts with «свод ичи»;
  - a header row (a cell with «Майдон») has been seen; it fixes the farm,
    area, price, amount and date columns;
  - the row is not in an «Иш хаки» block (a cell starting «Жами иш хаки»
    opens one; a section title «Справка …» / «Нақд …» closes it);
  - no cell from column A to the area column starts with «Жами» / «ИТОГО»;
  - the area cell is a number, 0 < area <= 1000;
  - there is a farm name or a number in column A.
The «№» counter is never required. The payment is the section title above.

Usage (Python 3, -I recommended):
  python -I -X utf8 reconcile_july_328.py BOOKS_DIR IMPORTER_ROWS_CSV \
      REGISTRY_328_CSV MISSING_19_CSV OUT_DIR [PREVIOUS_REGISTRY_CSV]

Writes OUT_DIR/JULY_328_ROWS.csv (one line per key found anywhere),
OUT_DIR/JULY_EXCEPTIONS.csv (every disagreement) and prints an ASCII summary.
Exit code 0 when all registries agree on the key set and on area; 1 otherwise.
"""
import csv
import datetime
import os
import re
import sys
import zipfile
import xml.etree.ElementTree as ET

NS = {'m': 'http://schemas.openxmlformats.org/spreadsheetml/2006/main'}
REL_ID = ('{http://schemas.openxmlformats.org/officeDocument/2006/'
          'relationships}id')
PKG_REL = '{http://schemas.openxmlformats.org/package/2006/relationships}'


def _fold(text):
    return re.sub(r'\s+', ' ', str(text or '')).strip().casefold()


def _col_index(ref):
    letters = re.match(r'[A-Z]+', ref).group(0)
    index = 0
    for ch in letters:
        index = index * 26 + (ord(ch) - 64)
    return index - 1


def _shared_strings(zf):
    if 'xl/sharedStrings.xml' not in zf.namelist():
        return []
    root = ET.fromstring(zf.read('xl/sharedStrings.xml'))
    out = []
    for si in root.findall('m:si', NS):
        out.append(''.join(t.text or '' for t in si.iter(
            '{%s}t' % NS['m'])))
    return out


def _sheets(zf):
    wb = ET.fromstring(zf.read('xl/workbook.xml'))
    rels = ET.fromstring(zf.read('xl/_rels/workbook.xml.rels'))
    target = {r.get('Id'): r.get('Target')
              for r in rels.iter(PKG_REL + 'Relationship')}
    for sheet in wb.find('m:sheets', NS).findall('m:sheet', NS):
        path = target[sheet.get(REL_ID)]
        path = path.lstrip('/')
        if not path.startswith('xl/'):
            path = 'xl/' + path
        yield sheet.get('name'), path


def _rows(zf, path, strings):
    root = ET.fromstring(zf.read(path))
    for row in root.find('m:sheetData', NS).findall('m:row', NS):
        cells = {}
        for c in row.findall('m:c', NS):
            kind = c.get('t')
            v = c.find('m:v', NS)
            if kind == 's':
                value = strings[int(v.text)] if v is not None else None
            elif kind == 'inlineStr':
                value = ''.join(t.text or '' for t in c.iter(
                    '{%s}t' % NS['m']))
            elif kind in ('str', 'e'):
                value = v.text if v is not None else None
            elif kind == 'b':
                value = (v.text == '1') if v is not None else None
            else:
                value = float(v.text) if v is not None and v.text else None
            if value is not None and value != '':
                cells[_col_index(c.get('r'))] = value
        yield int(row.get('r')), cells


def _date(value):
    if isinstance(value, float) and 30000 < value < 60000:
        return (datetime.date(1899, 12, 30)
                + datetime.timedelta(days=int(value))).isoformat()
    m = re.match(r'^\s*(\d{1,2})\.(\d{1,2})\.(\d{4})\s*$', str(value or ''))
    if m:
        return datetime.date(int(m.group(3)), int(m.group(2)),
                             int(m.group(1))).isoformat()
    return ''


def read_books(books_dir):
    """{key: dict} for every job row under the rule in the docstring."""
    jobs = {}
    for name in sorted(os.listdir(books_dir)):
        if not name.lower().endswith('.xlsx'):
            continue
        with zipfile.ZipFile(os.path.join(books_dir, name)) as zf:
            strings = _shared_strings(zf)
            for title, path in _sheets(zf):
                if not _fold(title).startswith('свод ичи'):
                    continue
                cols = None
                section = ''
                wage = False
                for r, cells in _rows(zf, path, strings):
                    texts = {i: _fold(v) for i, v in cells.items()
                             if isinstance(v, str)}
                    first = texts.get(0, '')
                    if first.startswith('справка') or first.startswith('нақд') \
                            or first.startswith('накд'):
                        section = re.sub(r'\s+', ' ', cells[0]).strip()
                        wage = False
                        continue
                    if any(t.startswith('жами иш хаки') for t in texts.values()):
                        wage = True
                        continue
                    if any('майдон' in t for t in texts.values()):
                        cols = {}
                        for i, t in texts.items():
                            if 'майдон' in t:
                                cols['area'] = i
                            elif 'фх' in t:
                                cols['farm'] = i
                            elif 'хизмат' in t:
                                cols['price'] = i
                            elif 'жами сумма' in t:
                                cols['amount'] = i
                            elif 'сана' in t:
                                cols['date'] = i
                        continue
                    if cols is None or wage or 'area' not in cols:
                        continue
                    if any(texts.get(i, '').startswith(('жами', 'итого'))
                           for i in range(0, cols['area'] + 1)):
                        continue
                    area = cells.get(cols['area'])
                    if not isinstance(area, float) or not 0 < area <= 1000:
                        continue
                    farm = cells.get(cols.get('farm'))
                    # Ends only: «Гоу  бобо» keeps its double space, as typed.
                    farm = farm.strip() if isinstance(farm, str) else ''
                    counter = cells.get(0)
                    if not farm and not isinstance(counter, float):
                        continue
                    key = '%s!%s!%d' % (name, title, r)
                    jobs[key] = {
                        'section': section,
                        'payment': ('transfer' if _fold(section)
                                    .startswith('справка') else 'cash'),
                        'farm': farm, 'area': area,
                        'price': cells.get(cols.get('price')),
                        'amount': cells.get(cols.get('amount')),
                        'date': _date(cells.get(cols.get('date'))),
                        'counter': '' if counter is None else counter,
                    }
    return jobs


def _f(text):
    try:
        return float(text)
    except (TypeError, ValueError):
        return None


def _close(a, b, tol):
    return a is not None and b is not None and abs(a - b) <= tol


def main(argv):
    if len(argv) not in (6, 7):
        print(__doc__)
        return 2
    books, imp_csv, reg_csv, miss_csv, out_dir = argv[1:6]
    prev_csv = argv[6] if len(argv) == 7 else None
    xml = read_books(books)

    def load(path):
        with open(path, encoding='utf-8-sig', newline='') as fh:
            return list(csv.DictReader(fh))

    imp = {r['source_key']: r for r in load(imp_csv)
           if r['status'] == 'ACCEPTED'}
    imp_other = {r['source_key']: r for r in load(imp_csv)
                 if r['status'] != 'ACCEPTED'}
    reg = {r['source_key']: r for r in load(reg_csv)}
    miss = {'%s!%s!%s' % (r['source_file'], r['source_sheet'],
                          r['source_row']): r for r in load(miss_csv)}
    prev = None
    if prev_csv:
        prev = {'%s!%s!%s' % (r['file'], r['sheet'], r['row']): r
                for r in load(prev_csv)}

    keys = set(xml) | set(imp) | set(reg) | set(miss)
    if prev is not None:
        keys |= set(prev)

    def sort_key(k):
        parts = k.split('!')
        return (parts[0], parts[1], int(parts[2]))

    os.makedirs(out_dir, exist_ok=True)
    rows_out = []
    exceptions = []
    for key in sorted(keys, key=sort_key):
        x, i, g = xml.get(key), imp.get(key), reg.get(key)
        problems = []
        for label, present in (('XML', x), ('IMPORTER', i), ('REGISTRY', g)):
            if present is None:
                problems.append('MISSING_IN_%s' % label)
        if prev is not None and key not in prev:
            problems.append('MISSING_IN_PREVIOUS_REGISTRY')
        if x and i:
            if not _close(x['area'], _f(i['area_ha']), 1e-9):
                problems.append('AREA_XML_VS_IMPORTER')
            if x['payment'] != i['payment_type']:
                problems.append('PAYMENT_XML_VS_IMPORTER')
            if x['date'] != (i['work_date_from'] or ''):
                problems.append('DATE_XML_VS_IMPORTER')
            if x['farm'] != i['customer_raw']:
                problems.append('FARM_XML_VS_IMPORTER')
            if not _close(x['amount'], _f(i['amount']), 0.005) and not (
                    x['amount'] is None and not i['amount']):
                problems.append('AMOUNT_XML_VS_IMPORTER')
        if g and i:
            if not _close(_f(g['area_ha']), _f(i['area_ha']), 0.005):
                problems.append('AREA_REGISTRY_VS_IMPORTER')
            pay = {'Нақд': 'cash', 'Справка': 'transfer'}.get(
                g['payment_block'], g['payment_block'])
            if pay != i['payment_type']:
                problems.append('PAYMENT_REGISTRY_VS_IMPORTER')
            if (g['date'] or '') != (i['work_date_from'] or ''):
                problems.append('DATE_REGISTRY_VS_IMPORTER')
            if g['customer_raw'].strip() != i['customer_raw']:
                problems.append('FARM_REGISTRY_VS_IMPORTER')
            if not _close(_f(g['calculated_charge_uzs']), _f(i['amount']),
                          0.005):
                problems.append('CHARGE_REGISTRY_VS_BOOK_AMOUNT')
        if key in miss:
            if not i or i['counter_missing'] != '1':
                problems.append('NAMED_19_NOT_ACCEPTED_WITHOUT_NUMBER')
        elif i and i['counter_missing'] == '1':
            problems.append('ACCEPTED_WITHOUT_NUMBER_NOT_IN_NAMED_19')
        src = i or {}
        f, s, r = key.split('!')
        rows_out.append({
            'source_key': key, 'source_file': f, 'source_sheet': s,
            'source_row': r,
            'importer_status': (src.get('status') or
                                imp_other.get(key, {}).get('status')
                                or 'ABSENT'),
            'payment_type': src.get('payment_type', ''),
            'book_section': (x or {}).get('section', ''),
            'work_date': src.get('work_date_from', ''),
            'date_kind': src.get('date_kind', ''),
            'operator_raw': src.get('operator_raw', ''),
            'operator_registry': (g or {}).get('operator', ''),
            'customer_raw': src.get('customer_raw', ''),
            'customer_class': src.get('customer_class', ''),
            'area_ha': src.get('area_ha', ''),
            'price_per_ha': src.get('price_per_ha', ''),
            'amount_book_cell': src.get('amount', ''),
            'charge_registry': (g or {}).get('calculated_charge_uzs', ''),
            'other_costs': src.get('other_costs', ''),
            'received_amount': src.get('received_amount', ''),
            'received_kind': src.get('received_kind', ''),
            'row_counter': src.get('row_counter', ''),
            'counter_missing': src.get('counter_missing', ''),
            'in_xml': 'Y' if x else 'N',
            'in_importer': 'Y' if i else 'N',
            'in_registry_328': 'Y' if g else 'N',
            'in_named_19': 'Y' if key in miss else 'N',
            'in_previous_registry': ('' if prev is None
                                     else 'Y' if key in prev else 'N'),
            'error_status': ';'.join(problems) or 'OK',
        })
        for p in problems:
            exceptions.append({'source_key': key, 'exception': p})

    with open(os.path.join(out_dir, 'JULY_328_ROWS.csv'), 'w',
              encoding='utf-8-sig', newline='') as fh:
        w = csv.DictWriter(fh, fieldnames=list(rows_out[0]),
                           lineterminator='\n')
        w.writeheader()
        w.writerows(rows_out)
    with open(os.path.join(out_dir, 'JULY_EXCEPTIONS.csv'), 'w',
              encoding='utf-8-sig', newline='') as fh:
        w = csv.DictWriter(fh, fieldnames=['source_key', 'exception'],
                           lineterminator='\n')
        w.writeheader()
        w.writerows(exceptions)

    def tot(d, field):
        return sum(v[field] for v in d.values())

    print('keys, union of all registries : %d' % len(keys))
    print('xml reader rows               : %d  %.2f ha  book amount %.2f'
          % (len(xml), tot(xml, 'area'),
             sum(v['amount'] or 0 for v in xml.values())))
    for p in ('cash', 'transfer'):
        sel = [v for v in xml.values() if v['payment'] == p]
        print('  xml %-8s : %d rows %.2f ha'
              % (p, len(sel), sum(v['area'] for v in sel)))
    print('importer accepted             : %d  %.2f ha  amount %.2f'
          % (len(imp), sum(_f(v['area_ha']) for v in imp.values()),
             sum(_f(v['amount']) or 0 for v in imp.values())))
    print('registry 328                  : %d  %.2f ha  charge %.2f'
          % (len(reg), sum(_f(v['area_ha']) for v in reg.values()),
             sum(_f(v['calculated_charge_uzs']) or 0 for v in reg.values())))
    print('named 19                      : %d  %.2f ha'
          % (len(miss), sum(_f(v['area_ha']) for v in miss.values())))
    if prev is not None:
        print('previous registry             : %d  %.2f ha'
              % (len(prev), sum(_f(v['area_ha']) for v in prev.values())))
    counts = {}
    for e in exceptions:
        counts[e['exception']] = counts.get(e['exception'], 0) + 1
    print('exceptions                    : %d' % len(exceptions))
    for name in sorted(counts):
        print('  %-40s %d' % (name, counts[name]))
    hard = [n for n in counts if n.startswith(('MISSING_', 'AREA_', 'NAMED_',
                                                 'ACCEPTED_WITHOUT'))]
    return 1 if hard else 0


if __name__ == '__main__':
    sys.exit(main(sys.argv))
