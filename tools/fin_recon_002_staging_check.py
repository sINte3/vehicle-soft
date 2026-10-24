#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""FIN-RECON-002 rehearsal on a FULL copy of a production backup. Never touches the source.

Runs, on a private copy and in the order of the planned write window, every
step of the June fix and reports whether each matched what was measured
offline on 22.10.2026:

  1. copy of the backup through the SQLite backup API (source opened
     read-only and immutable; its SHA-256 is compared before and after);
  2. schema of the drone tables;
  3. May: 190 / 3 499.45 by book month, 187 / 3 462.01 by calendar;
  4. link migration (merged-cell customers): dry run must leave the copy
     byte-identical; --apply must change drone_customer_id of exactly the 31
     rows and nothing else; a second run must say ALREADY APPLIED;
  5. the customer cut of June (same CASE as _drone_works_report_data():
     card / «Заказчик не указан» / «Заказчик не определён»), before and after,
     with outstanding = amount - received -- the delta the owner sees on the
     report and debt screens;
  6. the alias decision table on the FULL customer directory, with its
     invariant pinned to 132 / 1 732.68 / 277 613 871.12;
  6b. with --decisions: the owner's approved decision table, dry run (copy
     byte-identical) and --apply on the copy; the import must then create
     no card of its own;
  7. importer dry run of the two June books: 132 rows, nothing rejected,
     every operator resolved, no unknown header, nothing already present;
  8. importer apply on the same copy: every older row byte-identical, +132,
     May unchanged, June by book month 226 / 3 735.32; a second apply adds 0.

Writes only into --work (must not exist). The copy holds production data: keep
it on the server and delete it after reading the report. Console output is
ASCII; the full report is UTF-8 in --work/STAGING_CHECK_REPORT.txt.

  python tools/fin_recon_002_staging_check.py --backup <daily backup .db> --books <dir with the 2 June books> --work <new dir>

Exit: 0 every check PASS; 1 at least one FAIL; 2 input missing or wrong
book SHA; 3 --work exists.
"""
import argparse
import hashlib
import io
import os
import re
import sqlite3
import subprocess
import sys

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
BOOKS = {'Дрон Ғиждувон ПТЗ Июнь.xlsx': '3d9ad76cfdd901548225e41045c58973eb43937c0a910afd992854dab5afebb2',
         'Шофиркон ПТЗ Дрон маълумот Июнь.xlsx': 'f4a6591453268d686722321bac1f84942aa7e8e3b0a976bad9a61d5db3a74215'}
EXPECT_IMPORT = (132, 1732.68, 277613871.12)
# Both books' expense columns summed, Usmon's «Ёкилги ва бошка харажатлар» included.
EXPECT_OTHER_COSTS = 29025000.00
MAY = {'period': (190, 3499.45), 'calendar': (187, 3462.01)}
JUNE_PERIOD_AFTER_IMPORT = (226, 3735.32)
LINK_DELTA = {'unstated': (-31, -900.30, -77095080.10), '136': (21, 755.60, 64703985.00),
              '138': (10, 144.70, 12391095.10)}
CAL = "COALESCE(strftime('%Y-%m', work_date_from), period_month)"
TABLES = ('drone_works', 'drone_customers', 'drone_customer_aliases', 'drone_operators')


def sha_file(path):
    h = hashlib.sha256()
    with open(path, 'rb') as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b''):
            h.update(chunk)
    return h.hexdigest()


def uri(path, immutable=False):
    p = os.path.abspath(path).replace('\\', '/').replace('?', '%3f').replace('#', '%23')
    return 'file:%s?mode=ro%s' % (p, '&immutable=1' if immutable else '')


class Report(object):
    def __init__(self):
        self.lines, self.failed = [], 0

    def info(self, text=''):
        self.lines.append(text)

    def check(self, ok, text):
        self.failed += 0 if ok else 1
        self.lines.append(('PASS ' if ok else 'FAIL ') + text)
        print(('PASS ' if ok else 'FAIL ') + text.encode('ascii', 'replace').decode('ascii'))
        return ok


def run(args, cwd=REPO):
    r = subprocess.run([sys.executable] + args, cwd=cwd, capture_output=True)
    return r.returncode, r.stdout.decode('utf-8', 'replace') + r.stderr.decode('utf-8', 'replace')


def table_rows(con, table):
    return {row[0]: row for row in con.execute('SELECT * FROM "%s" ORDER BY 1' % table)}


def month_totals(con, expr, month):
    row = con.execute('SELECT COUNT(*), ROUND(COALESCE(SUM(area_ha), 0), 2) FROM drone_works WHERE %s = ?'
                      % expr, (month,)).fetchone()
    return (row[0], row[1])


def customer_cut(con, month):
    sql = ("SELECT CASE WHEN drone_customer_id IS NOT NULL THEN printf('%%d', drone_customer_id) "
           "WHEN TRIM(COALESCE(customer_raw, '')) = '' THEN 'unstated' ELSE 'unresolved' END, "
           "COUNT(*), ROUND(SUM(area_ha), 2), ROUND(SUM(COALESCE(amount, 0)), 2), "
           "ROUND(SUM(COALESCE(received_amount, 0)), 2), "
           "ROUND(SUM(CASE WHEN COALESCE(amount, 0) - COALESCE(received_amount, 0) > 0.005 "
           "THEN COALESCE(amount, 0) - COALESCE(received_amount, 0) ELSE 0 END), 2) "
           "FROM drone_works WHERE %s = ? GROUP BY 1" % CAL)
    return {r[0]: r[1:] for r in con.execute(sql, (month,))}


def main(argv=None):
    ap = argparse.ArgumentParser(description='FIN-RECON-002 rehearsal on a full backup copy.')
    ap.add_argument('--backup', required=True)
    ap.add_argument('--books', required=True)
    ap.add_argument('--work', required=True)
    ap.add_argument('--decisions', default=None,
                    help="the owner's approved decision table (tools/drone_alias_decisions.py); "
                         'applied on the copy before the import')
    a = ap.parse_args(argv)
    # [REASON]: every step runs as a subprocess from the repository root;
    # a relative path would point somewhere else there.
    a.backup, a.books, a.work = (os.path.abspath(p) for p in (a.backup, a.books, a.work))
    if a.decisions:
        a.decisions = os.path.abspath(a.decisions)
    if not os.path.isfile(a.backup) or not os.path.isdir(a.books):
        print('ERROR: --backup or --books not found')
        return 2
    for name, sha in BOOKS.items():
        p = os.path.join(a.books, name)
        if not os.path.isfile(p) or sha_file(p) != sha:
            print('ERROR: book missing or not the approved version: %s'
                  % name.encode('ascii', 'replace').decode('ascii'))
            return 2
    if os.path.exists(a.work):
        print('ERROR: --work already exists; refusing')
        return 3
    os.makedirs(a.work)
    rep = Report()
    copy = os.path.join(a.work, 'copy.db')

    # 1. copy
    src_sha = sha_file(a.backup)
    src = sqlite3.connect(uri(a.backup, immutable=True), uri=True)
    dst = sqlite3.connect(copy)
    src.backup(dst)
    dst.close()
    src.close()
    rep.check(sha_file(a.backup) == src_sha, 'source backup byte-identical after the copy (SHA %s...)' % src_sha[:16])
    con = sqlite3.connect(copy)
    rep.check(con.execute('PRAGMA integrity_check').fetchone()[0] == 'ok', 'copy: PRAGMA integrity_check = ok')

    # 2. schema
    need = {'drone_works': ('id', 'period_month', 'work_date_from', 'drone_customer_id', 'customer_raw', 'area_ha',
                            'amount', 'other_costs', 'received_amount', 'received_kind', 'payment_type',
                            'source_file', 'source_sheet', 'source_row', 'import_batch'),
            'drone_customers': ('id', 'name', 'customer_type'), 'drone_customer_aliases': ('normalized_key',),
            'drone_operators': ('id', 'full_name'), 'schema_migrations': ('name', 'applied_at', 'checksum')}
    missing = ['%s.%s' % (t, c) for t, cols in need.items()
               for c in cols if c not in {r[1] for r in con.execute('PRAGMA table_info("%s")' % t)}]
    rep.check(not missing, 'schema: drone tables and schema_migrations carry every column used %s' % (missing or ''))

    # 3. May
    rep.check(month_totals(con, 'period_month', '2026-05') == MAY['period']
              and month_totals(con, CAL, '2026-05') == MAY['calendar'],
              'May before: period %s, calendar %s' % (month_totals(con, 'period_month', '2026-05'),
                                                     month_totals(con, CAL, '2026-05')))
    june_before = month_totals(con, 'period_month', '2026-06')
    rep.info('June by book month before: %s' % (june_before,))
    before = {t: table_rows(con, t) for t in TABLES}
    cut_before = customer_cut(con, '2026-06')
    con.close()

    # 4. link migration
    mig = os.path.join(REPO, 'migrate_drones_works_merged_customer_link_001.py')
    sha_before = sha_file(copy)
    rc, out = run([mig, '--db', copy])
    rep.info(out)
    rep.check(rc == 0 and 'RESULT: DRY RUN OK' in out and sha_file(copy) == sha_before,
              'migration dry run: DRY RUN OK and the copy is byte-identical')
    receipt = os.path.join(a.work, 'link_receipt.json')
    rc, out = run([mig, '--db', copy, '--apply', '--receipt', receipt])
    rep.info(out)
    rep.check(rc == 0 and 'RESULT: APPLIED' in out and os.path.isfile(receipt), 'migration --apply: APPLIED, receipt written')
    rc, out = run([mig, '--db', copy, '--apply', '--receipt', receipt])
    rep.check(rc == 0 and 'RESULT: ALREADY APPLIED' in out, 'migration second run: ALREADY APPLIED')
    con = sqlite3.connect(copy)
    after = {t: table_rows(con, t) for t in TABLES}
    cols = [r[1] for r in con.execute('PRAGMA table_info(drone_works)')]
    link_at = cols.index('drone_customer_id')
    changed = [i for i in after['drone_works'] if after['drone_works'][i] != before['drone_works'].get(i)]
    only_link = all(after['drone_works'][i][:link_at] + after['drone_works'][i][link_at + 1:] ==
                    before['drone_works'][i][:link_at] + before['drone_works'][i][link_at + 1:] for i in changed)
    rep.check(len(changed) == 31 and only_link and len(after['drone_works']) == len(before['drone_works']),
              'migration changed exactly 31 rows, only drone_customer_id (changed %d)' % len(changed))
    rep.check(all(after[t] == before[t] for t in TABLES if t != 'drone_works'),
              'customers, aliases and operators byte-identical after the migration')
    rep.check(month_totals(con, 'period_month', '2026-05') == MAY['period'], 'May unchanged by the June migration')

    # 5. customer cut delta
    cut_after = customer_cut(con, '2026-06')
    names = dict(con.execute('SELECT id, name FROM drone_customers'))
    rep.info('')
    rep.info('JUNE CUSTOMER CUT (calendar), migration only: group | rows | ha | amount | received | outstanding')
    ok_delta = True
    for key in sorted(set(cut_before) | set(cut_after)):
        b = cut_before.get(key, (0, 0, 0, 0, 0))
        f = cut_after.get(key, (0, 0, 0, 0, 0))
        d = tuple(round(x - y, 2) for x, y in zip(f, b))
        label = names.get(int(key), '?') if key.isdigit() else key
        if any(d):
            rep.info('  %s %s | before %s | after %s | delta %s' % (key, label, b, f, d))
        want = LINK_DELTA.get(key)
        if want is not None and (d[0], d[1], d[2]) != want:
            ok_delta = False
        if want is None and any(d):
            ok_delta = False
    rep.check(ok_delta, 'customer cut delta: unstated -31 / -900.30 / -77095080.10 -> card 136 +21 / 755.60, '
                        'card 138 +10 / 144.70; no other group moves')
    rep.info('Outstanding moves with the rows: these are internal works with no received amount. Whether they '
             'belong on the debt screen is the owner\'s decision, not this migration\'s.')
    con.close()

    # 6. alias decision table on the full directory
    books_dir = os.path.join(a.work, 'books')
    os.makedirs(books_dir)
    for name in BOOKS:
        with open(os.path.join(a.books, name), 'rb') as s, open(os.path.join(books_dir, name), 'wb') as d:
            d.write(s.read())
    manifest = os.path.join(a.work, 'manifest.txt')
    with open(manifest, 'w', encoding='utf-8') as fh:
        fh.write(''.join('%s    2026-06\n' % n for n in BOOKS))
    rc, out = run([os.path.join(REPO, 'tools', 'drone_import_alias_candidates.py'), '--dir', books_dir,
                   '--manifest', manifest, '--db', copy, '--out', os.path.join(a.work, 'alias_decisions'),
                   '--expect-rows', '132', '--expect-area', '1732.68', '--expect-amount', '277613871.12'])
    rep.info(out)
    rep.check(rc == 0 and 'INVARIANTS: OK' in out,
              'alias decision table on the FULL directory: invariants hold (see alias_decisions/)')

    # 6b. the owner's approved decision table, when one is given
    if a.decisions:
        tool = os.path.join(REPO, 'tools', 'drone_alias_decisions.py')
        common = ['--decisions', a.decisions, '--dir', books_dir, '--manifest', manifest, '--db', copy]
        sha_before = sha_file(copy)
        rc, out = run([tool] + common + ['--out', os.path.join(a.work, 'decisions_dry_run')])
        rep.info(out)
        rep.check(rc == 0 and 'RESULT: DRY RUN OK' in out and 'IMPORT BLOCKED' not in out
                  and sha_file(copy) == sha_before,
                  'decision table dry run: OK, no HOLD, the copy byte-identical')
        rc, out = run([tool] + common + ['--apply', '--receipt', os.path.join(a.work, 'decisions_receipt.json')])
        rep.info(out)
        rep.check(rc == 0 and 'RESULT: APPLIED' in out, 'decision table --apply on the copy: APPLIED')

    # 7. importer dry run
    imp = os.path.join(REPO, 'tools', 'import_drone_works.py')
    rc, out = run([imp, '--dir', books_dir, '--manifest', manifest, '--db', copy,
                   '--report', os.path.join(a.work, 'import_dryrun_report.txt')])
    rep.info(out)

    def num(label):
        m = re.search(r'^%s\s*:\s*([\d.]+)' % re.escape(label), out, re.M)
        return float(m.group(1)) if m else None
    rep.check(rc == 0 and num('rows accepted') == 132 and num('hectares') == 1732.68
              and num('amount total') == 277613871.12, 'importer dry run: 132 / 1732.68 / 277613871.12')
    rep.check(num('rows rejected') == 0 and num('operator UNRESOLVED rows') == 0
              and num('rows already in the database') == 0 and num('unsafe filename aliases') == 0
              and 'headers matching no known phrase : 0' in out,
              'importer dry run: 0 rejected, 0 unresolved operators, 0 present, 0 unsafe names, 0 unknown headers')
    rep.info('cards the import would create on the full directory: %s (owner alias table decides)'
             % num('customers to be created'))
    if a.decisions:
        rep.check(num('customers to be created') == 0,
                  'after the decision table the import creates no card of its own (got %s)'
                  % num('customers to be created'))

    # 8. importer apply on the copy
    con = sqlite3.connect(copy)
    max_id = con.execute('SELECT MAX(id) FROM drone_works').fetchone()[0]
    old = table_rows(con, 'drone_works')
    con.close()
    rc, out = run([imp, '--dir', books_dir, '--manifest', manifest, '--db', copy, '--apply',
                   '--batch', 'FIN_RECON_002_STAGING_REHEARSAL', '--report', os.path.join(a.work, 'import_apply_report.txt')])
    rep.info(out)
    con = sqlite3.connect(copy)
    now = table_rows(con, 'drone_works')
    new = con.execute('SELECT COUNT(*), ROUND(SUM(area_ha), 2), ROUND(SUM(amount), 2) FROM drone_works WHERE id > ?',
                      (max_id,)).fetchone()
    rep.check(rc == 0 and tuple(new) == EXPECT_IMPORT and all(now[i] == old[i] for i in old),
              'import apply: +%s, every older row byte-identical' % (tuple(new),))
    costs = con.execute('SELECT ROUND(COALESCE(SUM(other_costs), 0), 2) FROM drone_works WHERE id > ?',
                        (max_id,)).fetchone()[0]
    rep.check(abs(costs - EXPECT_OTHER_COSTS) < 0.01,
              'import apply: other_costs of the 132 rows = %.2f (books: %.2f)' % (costs, EXPECT_OTHER_COSTS))
    rep.check(month_totals(con, 'period_month', '2026-05') == MAY['period']
              and month_totals(con, CAL, '2026-05') == MAY['calendar'], 'May unchanged after the import')
    rep.check(month_totals(con, 'period_month', '2026-06') == (june_before[0] + 132, round(june_before[1] + 1732.68, 2)),
              'June by book month after the import: %s (offline: %s on 22.10)'
              % (month_totals(con, 'period_month', '2026-06'), JUNE_PERIOD_AFTER_IMPORT))
    con.close()
    rc, out = run([imp, '--dir', books_dir, '--manifest', manifest, '--db', copy, '--apply',
                   '--batch', 'FIN_RECON_002_STAGING_REHEARSAL_REPEAT', '--report', os.path.join(a.work, 'import_repeat_report.txt')])
    rep.check(rc == 0 and 'WRITTEN: 0 customers created, 0 works inserted, 132 already present' in out,
              'second import apply: 0 inserted, 132 already present')

    rep.info('')
    rep.info('RESULT: %s (%d failed)' % ('PASS' if not rep.failed else 'FAIL', rep.failed))
    with open(os.path.join(a.work, 'STAGING_CHECK_REPORT.txt'), 'w', encoding='utf-8') as fh:
        fh.write('\n'.join(rep.lines) + '\n')
    print('RESULT: %s (%d failed). Report: STAGING_CHECK_REPORT.txt. Delete copy.db after reading.'
          % ('PASS' if not rep.failed else 'FAIL', rep.failed))
    return 0 if not rep.failed else 1


if __name__ == '__main__':
    sys.exit(main())
