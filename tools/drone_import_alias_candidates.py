#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Decision table of the customer cards a book import WOULD create. Read-only.

FIN-RECON-002. Before books are applied, the owner decides for every farm
spelling the import does not know yet: attach it to an existing card (an
alias) or let a new card be created. This tool builds that table from the
importer's OWN parse of the books against the database it is pointed at --
not from a register or a report, whose spellings differ (a line break inside
a cell is «\\n» in the importer and « / » in the register; joining the two
lost 2 jobs / 7.60 ha in the first version of this table, review 23.10.2026).

It decides nothing. Similarity to existing cards and the holding-entity
markers are hints for the owner; no alias is created, no card is merged.

INVARIANT (checked, exit 1 when broken): every parsed row is in exactly one
group -- linked to an existing card, to be created, or with no customer at
all -- and the three groups add up to the run in rows, hectares and money;
every card the importer would create appears with at least one row.
--expect-rows / --expect-area / --expect-amount pin the run to known totals.

  python tools/drone_import_alias_candidates.py --dir <books> --manifest <file> \\
      --db <copy of transport.db> --out <new directory> \\
      [--expect-rows 132 --expect-area 1732.68 --expect-amount 277613871.12]

The database is opened read-only (file: URI, mode=ro). Writes only into --out,
which must not exist. Exit: 0 table written; 1 an invariant failed; 2 input
missing; 3 --out exists.
"""
import argparse
import csv
import difflib
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)

import import_drone_works as idw  # noqa: E402

TOL_HA, TOL_SUM = 0.005, 0.01
# [REASON]: words of the holding's OWN subdivisions as the dispatchers write
# them (including the typo «Агроклатер» seen in the June 2026 Gijduvon book).
# A hint that the farm may be one of ours -- internal tariff, internal card --
# never a decision.
HOLDING_MARKERS = ('агрокластер', 'агроклатер', 'гарден', 'сервис', 'птз', 'заминлари',
                   'худуд', 'тизим корхонаси')
SIMILAR = 0.80


def group_key(row):
    """The key the importer itself uses for a new card. One place, so a test
    can prove what happens when two parts of the tool disagree on it."""
    return idw.customer_key(row['customer_raw'])


def build(books, manifest_path, db):
    manifest, problems = idw.read_manifest(manifest_path)
    files = sorted(f for f in os.listdir(books) if f.lower().endswith('.xlsx') and not f.startswith('~$'))
    con = idw._connect(db, read_only=True)
    try:
        directory = idw.Directory(con)
        result = idw.collect(books, manifest, files, directory, None)
        idw.resolve(result, directory)
    finally:
        con.close()
    return result, directory, problems


def partition(result):
    linked, new, empty = {}, {}, []
    for row in result.rows:
        if not row['customer_raw']:
            empty.append(row)
        elif row['drone_customer_id'] is not None:
            linked.setdefault(row['drone_customer_id'], []).append(row)
        else:
            new.setdefault(group_key(row), []).append(row)
    return linked, new, empty


def totals(rows):
    return (len(rows), round(sum(r['area_ha'] or 0 for r in rows), 2),
            round(sum(r['amount'] or 0 for r in rows), 2))


def check_invariants(result, linked, new, empty, expect):
    problems = []
    run = totals(result.rows)
    parts = [totals(rs) for rs in linked.values()] + [totals(rs) for rs in new.values()] + [totals(empty)]
    summed = (sum(p[0] for p in parts), round(sum(p[1] for p in parts), 2), round(sum(p[2] for p in parts), 2))
    if summed[0] != run[0] or abs(summed[1] - run[1]) > TOL_HA or abs(summed[2] - run[2]) > TOL_SUM:
        problems.append('groups add up to %s, the run is %s' % (summed, run))
    created = {key for key, _name in result.created_customers}
    if created != set(new):
        problems.append('cards the importer would create (%d) differ from the table (%d)'
                        % (len(created), len(new)))
    zero = [k for k, rs in new.items() if not rs]
    if zero:
        problems.append('%d new card(s) with no rows' % len(zero))
    for label, want, got, tol in (('rows', expect[0], run[0], 0), ('area', expect[1], run[1], TOL_HA),
                                  ('amount', expect[2], run[2], TOL_SUM)):
        if want is not None and abs(got - want) > tol:
            problems.append('run %s %s, expected %s' % (label, got, want))
    return run, problems


def nearest(name, directory, n=3):
    key = idw.customer_key(name)
    scored = sorted(((difflib.SequenceMatcher(None, key, idw.customer_key(cname)).ratio(), cid, cname)
                     for cid, cname in directory.customer_names.items()), reverse=True)
    return scored[:n]


def write_tables(out, result, directory, linked, new, empty, run, problems):
    os.makedirs(out)
    # A key the importer does not know is exactly what the invariant reports;
    # the table is still written, under the spelling of its first row.
    names = {key: dict(result.created_customers).get(key, rows[0]['customer_raw']) for key, rows in new.items()}
    with open(os.path.join(out, 'NEW_CUSTOMER_DECISIONS.csv'), 'w', encoding='utf-8-sig', newline='') as fh:
        w = csv.writer(fh, delimiter=';')
        w.writerow(['new_card_name_as_imported', 'line_break_in_name', 'rows', 'area_ha', 'amount', 'tariffs',
                    'payment_types', 'source_sheets', 'holding_markers', 'hint', 'nearest_1', 'nearest_2',
                    'nearest_3', 'OWNER_DECISION (alias to card id / NEW)'])
        ordered = sorted(new.items(), key=lambda kv: (-totals(kv[1])[1], names[kv[0]]))
        for key, rows in ordered:
            name = names[key]
            near = nearest(name, directory)
            folded = idw.uz_fold(name)
            marks = [m for m in HOLDING_MARKERS if idw.uz_fold(m) in folded]
            hint = ('HOLDING_ENTITY?' if marks else
                    'SIMILAR_EXISTING?' if near and near[0][0] >= SIMILAR else 'NEW?')
            n, ha, amount = totals(rows)
            w.writerow([name.replace('\n', ' ↵ '), 'YES' if '\n' in name else '', n, ha, amount,
                        ' '.join(sorted({'%g' % r['price_per_ha'] for r in rows if r['price_per_ha'] is not None})),
                        ' '.join(sorted({r['payment_type'] for r in rows})),
                        ' | '.join(sorted({'%s / %s' % (r['source_file'], r['source_sheet']) for r in rows})),
                        ' '.join(marks), hint] +
                       ['%s #%s (%.2f)' % (cname.replace('\n', ' ↵ '), cid, ratio) for ratio, cid, cname in near] +
                       [''] * (3 - len(near)) + [''])
    with open(os.path.join(out, 'MATCHED_EXISTING_CARDS.csv'), 'w', encoding='utf-8-sig', newline='') as fh:
        w = csv.writer(fh, delimiter=';')
        w.writerow(['card_id', 'card_name', 'rows', 'area_ha', 'amount', 'spellings_in_books'])
        for cid, rows in sorted(linked.items(), key=lambda kv: -totals(kv[1])[1]):
            n, ha, amount = totals(rows)
            w.writerow([cid, directory.customer_names.get(cid, '?').replace('\n', ' ↵ '), n, ha, amount,
                        ' | '.join(sorted({r['customer_raw'].replace('\n', ' ↵ ') for r in rows}))])
    lines = ['run: %d rows / %.2f ha / %.2f' % run,
             'linked to existing cards: %d rows / %.2f ha / %.2f (%d cards)' % (totals(sum(linked.values(), [])) + (len(linked),)),
             'cards to create: %d rows / %.2f ha / %.2f (%d cards)' % (totals(sum(new.values(), [])) + (len(new),)),
             'no customer in the book: %d rows / %.2f ha / %.2f' % totals(empty),
             'INVARIANTS: ' + ('OK' if not problems else 'FAILED')] + ['  ' + p for p in problems]
    with open(os.path.join(out, 'SUMMARY.txt'), 'w', encoding='utf-8') as fh:
        fh.write('\n'.join(lines) + '\n')
    return lines


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split('\n')[0])
    ap.add_argument('--dir', required=True)
    ap.add_argument('--manifest', required=True)
    ap.add_argument('--db', required=True)
    ap.add_argument('--out', required=True)
    ap.add_argument('--expect-rows', type=int, default=None)
    ap.add_argument('--expect-area', type=float, default=None)
    ap.add_argument('--expect-amount', type=float, default=None)
    a = ap.parse_args(argv)
    for path in (a.dir, a.manifest, a.db):
        if not os.path.exists(path):
            print('ERROR: not found: %s' % idw._ascii(path))
            return 2
    if os.path.exists(a.out):
        print('ERROR: --out already exists; refusing')
        return 3
    result, directory, _problems = build(a.dir, a.manifest, a.db)
    linked, new, empty = partition(result)
    run, problems = check_invariants(result, linked, new, empty, (a.expect_rows, a.expect_area, a.expect_amount))
    for line in write_tables(a.out, result, directory, linked, new, empty, run, problems):
        print(idw._ascii(line))
    return 1 if problems else 0


if __name__ == '__main__':
    sys.exit(main())
