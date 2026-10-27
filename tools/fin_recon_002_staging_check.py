#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""FIN-RECON-002 rehearsal on a FULL copy of a production backup. Never touches the source.

Runs, on a private copy and in the order of the planned write window, every
step of the June fix and reports whether each matched what was measured
offline on 22.10.2026 and what the owner decided on 27.10.2026:

  1. copy of the backup through the SQLite backup API (source opened
     read-only and immutable; its SHA-256 is compared before and after);
  2. schema of the drone tables; the canonical cards the owner named
     (137 «Бухоро Агрокластер Заминлари МЧЖ», 155 «Гарден Бухоро Агрокластер
     МЧЖ») exist, carry those names and are switched on;
  2b. migrate_drones_works_payer_unidentified_001.py on the copy: the column
     appears, no row is flagged, a second run says Already applied;
  3. May: 190 / 3 499.45 by book month, 187 / 3 462.01 by calendar; every May
     row is kept to compare byte for byte at the end;
  4. link migrations (merged-cell customers): the 31 empty links (21 to 137,
     10 to 138) and, separately, the stored June work 997 from 136 to 137.
     Each dry run leaves the copy byte-identical; each --apply changes
     drone_customer_id of exactly its rows; a second run says ALREADY APPLIED;
  5. the customer cut of June (the CASE of _drone_works_report_data(): card /
     «Плательщик-гражданин не установлен» / «Заказчик не указан» / «Заказчик
     не определён»), before and after both migrations;
  6. the decision table on the FULL customer directory, invariant pinned to
     132 / 1 732.68 / 277 613 871.12;
  6b. with --decisions: the owner's executable table, dry run (copy
     byte-identical) with --forbid-cards. While the table holds HOLD the
     --apply must be REFUSED and change nothing -- the rehearsal then stops
     before the import and ends HOLD_BLOCKED, never PASS. Without HOLD:
     --apply on the copy, and the import must create no card of its own;
  7. importer dry run of the two June books: 132 rows, nothing rejected,
     every operator resolved, no unknown header, nothing already present;
  8. importer apply on the same copy: every older row byte-identical, +132,
     May unchanged, the rows of the books land on 137 / 155 / the one new
     «Ғиждувон ПТЗ» card / no card for the generic citizens exactly as the
     owner decided, none on the forbidden cards; a second apply adds 0.

Writes only into --work (must not exist). The copy holds production data: keep
it on the server and delete it after reading the report. Console output is
ASCII; the full report is UTF-8 in --work/STAGING_CHECK_REPORT.txt.

  python tools/fin_recon_002_staging_check.py --backup <daily backup .db> --books <dir with the 2 June books> \\
      --work <new dir> [--decisions <executable table.csv> --forbid-cards 589,684-692,803]

Exit: 0 every check PASS; 1 at least one FAIL; 2 input missing or wrong
book SHA; 3 --work exists; 4 every check PASS but the decision table still
holds HOLD, so the import was not rehearsed (HOLD_BLOCKED).
"""
import argparse
import hashlib
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
# The owner's canonical cards of 27.10.2026, checked by name, never by guess.
CANONICAL = {137: 'Бухоро Агрокластер Заминлари МЧЖ', 155: 'Гарден Бухоро Агрокластер МЧЖ'}
PTZ_CARD_NAME = 'Ғиждувон ПТЗ'
# June cut, both link migrations together: 21 empty links + the stored 997 to 137.
LINK_DELTA = {'unstated': (-31, -900.30, -77095080.10), '137': (22, 782.60, 67016064.93),
              '138': (10, 144.70, 12391095.10), '136': (-1, -27.00, -2312079.93)}
LINK_ROWS = {'june': 31, 'june-anchor': 1}
# Rows of the two books after the decisions and the import (owner, 27.10.2026).
EXPECT_ROUTES = {'137': (5, 378.80, 32437780.40), '155': (3, 53.24, 4559100.92),
                 'PTZ': (16, 170.60, 14608989.80), 'UNIDENTIFIED': (12, 61.80, 12360000.00)}
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


def norm(name):
    return ' '.join(str(name or '').split()).casefold()


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


def may_rows(con):
    return con.execute("SELECT * FROM drone_works WHERE period_month = '2026-05' OR %s = '2026-05' ORDER BY id"
                       % CAL).fetchall()


def month_totals(con, expr, month):
    row = con.execute('SELECT COUNT(*), ROUND(COALESCE(SUM(area_ha), 0), 2) FROM drone_works WHERE %s = ?'
                      % expr, (month,)).fetchone()
    return (row[0], row[1])


def customer_cut(con, month):
    sql = ("SELECT CASE WHEN drone_customer_id IS NOT NULL THEN printf('%%d', drone_customer_id) "
           "WHEN payer_unidentified IS 1 THEN 'unidentified' "
           "WHEN TRIM(COALESCE(customer_raw, '')) = '' THEN 'unstated' ELSE 'unresolved' END, "
           "COUNT(*), ROUND(SUM(area_ha), 2), ROUND(SUM(COALESCE(amount, 0)), 2), "
           "ROUND(SUM(COALESCE(received_amount, 0)), 2), "
           "ROUND(SUM(CASE WHEN COALESCE(amount, 0) - COALESCE(received_amount, 0) > 0.005 "
           "THEN COALESCE(amount, 0) - COALESCE(received_amount, 0) ELSE 0 END), 2) "
           "FROM drone_works WHERE %s = ? GROUP BY 1" % CAL)
    return {r[0]: r[1:] for r in con.execute(sql, (month,))}


def routes(con, first_new_id, ptz_id):
    """{'137' / 'PTZ' / 'UNIDENTIFIED' / card id: (rows, ha, amount)} of the rows imported now."""
    out = {}
    for cid, flag, n, ha, amount in con.execute(
            'SELECT drone_customer_id, payer_unidentified IS 1, COUNT(*), ROUND(SUM(area_ha), 2), '
            'ROUND(SUM(COALESCE(amount, 0)), 2) FROM drone_works WHERE id >= ? GROUP BY 1, 2', (first_new_id,)):
        key = 'UNIDENTIFIED' if flag else ('PTZ' if ptz_id is not None and cid == ptz_id else str(cid))
        out[key] = (n, ha, amount)
    return out


def parse_forbid(text):
    out = set()
    for part in (text or '').replace(' ', '').split(','):
        if part:
            lo, _, hi = part.partition('-')
            out.update(range(int(lo), int(hi or lo) + 1))
    return out


def main(argv=None):
    ap = argparse.ArgumentParser(description='FIN-RECON-002 rehearsal on a full backup copy.')
    ap.add_argument('--backup', required=True)
    ap.add_argument('--books', required=True)
    ap.add_argument('--work', required=True)
    ap.add_argument('--decisions', default=None,
                    help="the owner's executable decision table (tools/drone_alias_decisions.py); "
                         'applied on the copy before the import')
    ap.add_argument('--forbid-cards', default='',
                    help='passed to tools/drone_alias_decisions.py, e.g. 589,684-692,803')
    a = ap.parse_args(argv)
    # [REASON]: every step runs as a subprocess from the repository root;
    # a relative path would point somewhere else there.
    a.backup, a.books, a.work = (os.path.abspath(p) for p in (a.backup, a.books, a.work))
    if a.decisions:
        a.decisions = os.path.abspath(a.decisions)
    if not os.path.isfile(a.backup) or not os.path.isdir(a.books):
        print('ERROR: --backup or --books not found')
        return 2
    try:
        forbid = parse_forbid(a.forbid_cards)
    except ValueError:
        print('ERROR: --forbid-cards must be ids and ranges')
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
    hold_blocked = False

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

    # 2. schema and the owner's canonical cards
    need = {'drone_works': ('id', 'period_month', 'work_date_from', 'drone_customer_id', 'customer_raw', 'area_ha',
                            'amount', 'other_costs', 'received_amount', 'received_kind', 'payment_type',
                            'source_file', 'source_sheet', 'source_row', 'import_batch'),
            'drone_customers': ('id', 'name', 'customer_type', 'is_active'),
            'drone_customer_aliases': ('normalized_key',),
            'drone_operators': ('id', 'full_name'), 'schema_migrations': ('name', 'applied_at', 'checksum')}
    missing = ['%s.%s' % (t, c) for t, cols in need.items()
               for c in cols if c not in {r[1] for r in con.execute('PRAGMA table_info("%s")' % t)}]
    rep.check(not missing, 'schema: drone tables and schema_migrations carry every column used %s' % (missing or ''))
    for cid, want in sorted(CANONICAL.items()):
        card = con.execute('SELECT name, is_active, customer_type FROM drone_customers WHERE id = ?', (cid,)).fetchone()
        # is_active NULL counts as on, as everywhere in the drone directory.
        rep.check(card is not None and norm(card[0]) == norm(want) and (card[1] is None or int(card[1]) != 0),
                  'canonical card %d exists, is named %r and is switched on (found %s)'
                  % (cid, want, None if card is None else (card[0], card[1], card[2])))
    ptz_existing = con.execute('SELECT id, name FROM drone_customers').fetchall()
    ptz_existing = [cid for cid, name in ptz_existing if norm(name) == norm(PTZ_CARD_NAME)]
    rep.info('cards already named exactly %r: %s (GROUP_NEW refuses the name if any exists)'
             % (PTZ_CARD_NAME, ptz_existing or 'none'))
    con.close()

    # 2b. the «Фукаро» column
    pmig = os.path.join(REPO, 'migrate_drones_works_payer_unidentified_001.py')
    rc, out = run([pmig, '--db', copy])
    rep.info(out)
    con = sqlite3.connect(copy)
    has_col = 'payer_unidentified' in {r[1] for r in con.execute('PRAGMA table_info(drone_works)')}
    flagged = con.execute('SELECT COUNT(*) FROM drone_works WHERE payer_unidentified IS NOT NULL').fetchone()[0] \
        if has_col else None
    con.close()
    rep.check(rc == 0 and has_col and flagged == 0, 'payer_unidentified migration: column added, 0 rows flagged')
    rc, out = run([pmig, '--db', copy])
    rep.check(rc == 0 and 'Already applied' in out, 'payer_unidentified migration second run: Already applied')

    # 3. May
    con = sqlite3.connect(copy)
    rep.check(month_totals(con, 'period_month', '2026-05') == MAY['period']
              and month_totals(con, CAL, '2026-05') == MAY['calendar'],
              'May before: period %s, calendar %s' % (month_totals(con, 'period_month', '2026-05'),
                                                     month_totals(con, CAL, '2026-05')))
    may_before = may_rows(con)
    june_before = month_totals(con, 'period_month', '2026-06')
    rep.info('June by book month before: %s' % (june_before,))
    before = {t: table_rows(con, t) for t in TABLES}
    cut_before = customer_cut(con, '2026-06')
    con.close()

    # 4. link migrations: the 31 empty links, then the stored 997, each with its own receipt
    mig = os.path.join(REPO, 'migrate_drones_works_merged_customer_link_001.py')
    for scope in ('june', 'june-anchor'):
        sha_before = sha_file(copy)
        rc, out = run([mig, '--db', copy, '--scope', scope])
        rep.info(out)
        rep.check(rc == 0 and 'RESULT: DRY RUN OK' in out and sha_file(copy) == sha_before,
                  'migration %s dry run: DRY RUN OK and the copy is byte-identical' % scope)
        receipt = os.path.join(a.work, 'link_receipt_%s.json' % scope)
        con = sqlite3.connect(copy)
        step_before = table_rows(con, 'drone_works')
        con.close()
        rc, out = run([mig, '--db', copy, '--scope', scope, '--apply', '--receipt', receipt])
        rep.info(out)
        rep.check(rc == 0 and 'RESULT: APPLIED' in out and os.path.isfile(receipt),
                  'migration %s --apply: APPLIED, receipt written' % scope)
        rc, out = run([mig, '--db', copy, '--scope', scope, '--apply', '--receipt', receipt])
        rep.check(rc == 0 and 'RESULT: ALREADY APPLIED' in out, 'migration %s second run: ALREADY APPLIED' % scope)
        con = sqlite3.connect(copy)
        step_after = table_rows(con, 'drone_works')
        cols = [r[1] for r in con.execute('PRAGMA table_info(drone_works)')]
        con.close()
        link_at = cols.index('drone_customer_id')
        changed = [i for i in step_after if step_after[i] != step_before.get(i)]
        only_link = all(step_after[i][:link_at] + step_after[i][link_at + 1:] ==
                        step_before[i][:link_at] + step_before[i][link_at + 1:] for i in changed)
        rep.check(len(changed) == LINK_ROWS[scope] and only_link and len(step_after) == len(step_before),
                  'migration %s changed exactly %d row(s), only drone_customer_id (changed %d)'
                  % (scope, LINK_ROWS[scope], len(changed)))
    con = sqlite3.connect(copy)
    after = {t: table_rows(con, t) for t in TABLES}
    rep.check(all(after[t] == before[t] for t in TABLES if t != 'drone_works'),
              'customers, aliases and operators byte-identical after both migrations')
    rep.check(may_rows(con) == may_before, 'May byte-identical after both June migrations (%d rows)' % len(may_before))

    # 5. customer cut delta
    cut_after = customer_cut(con, '2026-06')
    names = dict(con.execute('SELECT id, name FROM drone_customers'))
    rep.info('')
    rep.info('JUNE CUSTOMER CUT (calendar), migrations only: group | rows | ha | amount | received | outstanding')
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
    rep.check(ok_delta, 'customer cut delta: unstated -31 / -900.30 / -77095080.10; card 137 +22 / 782.60 '
                        '(21 links + work 997); card 138 +10 / 144.70; card 136 -1 / -27.00; no other group moves')
    con.close()

    # 6. decision table on the full directory
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
              'decision table on the FULL directory: invariants hold (see alias_decisions/)')

    # 6b. the owner's executable decision table, when one is given
    if a.decisions:
        tool = os.path.join(REPO, 'tools', 'drone_alias_decisions.py')
        common = ['--decisions', a.decisions, '--dir', books_dir, '--manifest', manifest, '--db', copy,
                  '--forbid-cards', a.forbid_cards]
        sha_before = sha_file(copy)
        rc, out = run([tool] + common + ['--out', os.path.join(a.work, 'decisions_dry_run')])
        rep.info(out)
        hold_blocked = 'IMPORT BLOCKED' in out
        rep.check(rc == 0 and 'RESULT: DRY RUN OK' in out and sha_file(copy) == sha_before,
                  'decision table dry run: OK, the copy byte-identical%s'
                  % (' -- HOLD present, import blocked' if hold_blocked else ', no HOLD'))
        rc, out = run([tool] + common + ['--apply', '--receipt', os.path.join(a.work, 'decisions_receipt.json')])
        rep.info(out)
        if hold_blocked:
            rep.check(rc == 1 and 'HOLD:' in out and 'RESULT: NOT APPLIED' in out and sha_file(copy) == sha_before,
                      'decision table --apply with HOLD: REFUSED, the copy byte-identical')
        else:
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
    rep.check(num('payer a citizen, unidentified') == EXPECT_ROUTES['UNIDENTIFIED'][0],
              'importer dry run: %s rows of generic citizens, no card (owner: 12)' % num('payer a citizen, unidentified'))
    rep.info('cards the import would create on the full directory: %s' % num('customers to be created'))
    if a.decisions and not hold_blocked:
        rep.check(num('customers to be created') == 0,
                  'after the decision table the import creates no card of its own (got %s)'
                  % num('customers to be created'))

    if hold_blocked:
        rep.info('')
        rep.info('IMPORT NOT REHEARSED: the decision table still holds HOLD spellings. The rehearsal of the '
                 'import (step 8) needs the whole table approved. Nothing below this line ran.')
        return finish(rep, a.work, hold_blocked)

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
    rep.check(may_rows(con) == may_before and month_totals(con, 'period_month', '2026-05') == MAY['period']
              and month_totals(con, CAL, '2026-05') == MAY['calendar'], 'May byte-identical after the import')
    rep.check(month_totals(con, 'period_month', '2026-06') == (june_before[0] + 132, round(june_before[1] + 1732.68, 2)),
              'June by book month after the import: %s (offline: %s on 22.10)'
              % (month_totals(con, 'period_month', '2026-06'), JUNE_PERIOD_AFTER_IMPORT))
    ptz = [cid for cid, name in con.execute('SELECT id, name FROM drone_customers')
           if norm(name) == norm(PTZ_CARD_NAME)]
    got = routes(con, max_id + 1, ptz[0] if len(ptz) == 1 else None)
    rep.info('rows of the two books by card after the import: %s' % sorted(got.items(), key=lambda kv: str(kv[0])))
    if a.decisions:
        rep.check(len(ptz) == 1, 'exactly one card named %r after the decisions (found %s)' % (PTZ_CARD_NAME, ptz))
        for key, want in sorted(EXPECT_ROUTES.items()):
            rep.check(got.get(key) == want, 'books -> %s: %s, owner decided %s' % (key, got.get(key), want))
        rep.check(not any(k.isdigit() and int(k) in forbid for k in got),
                  'no row of the books on a forbidden card %s' % sorted(forbid))
    con.close()
    rc, out = run([imp, '--dir', books_dir, '--manifest', manifest, '--db', copy, '--apply',
                   '--batch', 'FIN_RECON_002_STAGING_REHEARSAL_REPEAT', '--report', os.path.join(a.work, 'import_repeat_report.txt')])
    rep.check(rc == 0 and 'WRITTEN: 0 customers created, 0 works inserted, 132 already present' in out,
              'second import apply: 0 inserted, 132 already present')
    return finish(rep, a.work, hold_blocked)


def finish(rep, work, hold_blocked):
    status = 'FAIL' if rep.failed else ('HOLD_BLOCKED' if hold_blocked else 'PASS')
    rep.info('')
    rep.info('RESULT: %s (%d failed)' % (status, rep.failed))
    with open(os.path.join(work, 'STAGING_CHECK_REPORT.txt'), 'w', encoding='utf-8') as fh:
        fh.write('\n'.join(rep.lines) + '\n')
    print('RESULT: %s (%d failed). Report: STAGING_CHECK_REPORT.txt. Delete copy.db after reading.'
          % (status, rep.failed))
    return 1 if rep.failed else (4 if hold_blocked else 0)


if __name__ == '__main__':
    sys.exit(main())
