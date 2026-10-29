#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""FIN-RECON-002, release v1.32: the live database against the backup of the same window. Reads only.

Step 3 of docs/FIN_RECON_002_RELEASE_V132_RUNBOOK.md runs it after the
import and BEFORE the services start again, so nothing but the release has
written between the backup and the check. It answers one question: did the
window change exactly what the owner decided, and nothing else?

  1. May (book month and calendar) byte-identical with the backup and still
     190 / 3 499.45 and 187 / 3 462.01;
  2. every work of the backup is still there; the ones that changed are
     exactly the 32 rows of both link scopes (21 + 1 to 137, 10 to 138), and
     only in drone_customer_id; no work of the backup carries payer_unidentified;
  3. the import batch: 132 / 1 732.68 / 277 613 871.12, the rows of the books
     on 137 / 155 / the one «Ғиждувон ПТЗ» card / no card for the generic
     citizens as decided, none on a forbidden card; no other new work;
  4. June by book month = backup + 132 rows / 1 732.68 ha;
  5. cards: every card of the backup unchanged but customer_type of 137 and
     155, now internal; 45 new cards (44 external, one internal «Ғиждувон
     ПТЗ»); aliases of the backup unchanged but the one REPOINT, now on it;
  6. registry: the payer column, both link scopes, the release decision table.

Expectations are not retyped: link rows come from the link migration's
SCOPES, totals and routes from tools/fin_recon_002_staging_check.py, the
registry name of the table from tools/drone_alias_decisions.py.

  python tools/fin_recon_002_release_check.py --db instance\\transport.db --baseline <backup .db>

Exit 0 RELEASE CHECK PASSED; 1 FAILED; 2 a database is missing. Console ASCII.
"""
import argparse
import os
import sqlite3
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
for _p in (HERE, os.path.dirname(HERE)):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import drone_alias_decisions as decisions  # noqa: E402
import fin_recon_002_staging_check as staging  # noqa: E402
import migrate_drones_works_merged_customer_link_001 as link  # noqa: E402
import migrate_drones_works_payer_unidentified_001 as payer  # noqa: E402

BATCH = 'FIN_RECON_002_JUNE_2026'
# The executable table of the release: the owner-approved table
# (085df4bc...548f) byte for byte + SET_TYPE 137 and 155 -> internal.
RELEASE_TABLE_SHA = '754dab3571f4cae516933fd55cf3ee68a7f83b4a21162767b2833f67220a99d0'
FORBID = '589,684-692,803'
NEW_CARDS = {'external': 44, 'internal': 1}
NEW_ALIASES = 63            # 44 NEW + 14 ALIAS + 5 spellings of the PTZ group
REPOINTED_ALIASES = 1       # the territory spelling of №690 -> the PTZ card


def expected_links():
    """{work id: card after} of both link scopes."""
    out = {}
    for scope in ('june', 'june-anchor'):
        for g in link.SCOPES[scope]['groups']:
            for target in g['targets']:
                out[target[0]] = g['card']
    return out


def expected_registry():
    return {payer.MIGRATION_ID, link.SCOPES['june']['migration_id'],
            link.SCOPES['june-anchor']['migration_id'], decisions.migration_id(RELEASE_TABLE_SHA)}


def ro(path, immutable=False):
    return sqlite3.connect(staging.uri(path, immutable), uri=True)


def columns(con, table):
    return [r[1] for r in con.execute('PRAGMA table_info("%s")' % table)]


def rows_on(con, table, cols):
    return {r[0]: r for r in con.execute('SELECT %s FROM "%s" ORDER BY 1'
                                         % (', '.join('"%s"' % c for c in cols), table))}


def check(live, base, rep, batch=BATCH):
    """Every check of the docstring; results go to rep (a staging.Report)."""
    # [REASON]: every query below reads the new column, and the routes need
    # it; without it the release did not happen and the rest would only crash.
    if not rep.check('payer_unidentified' in columns(live, 'drone_works'),
                     'the live database has drone_works.payer_unidentified (payer migration ran)'):
        return rep
    # [REASON]: the backup predates the payer_unidentified column; rows are
    # compared on the backup's columns, the new column is checked on its own.
    wcols = columns(base, 'drone_works')
    may = ('SELECT %s FROM drone_works WHERE period_month = \'2026-05\' OR %s = \'2026-05\' ORDER BY id'
           % (', '.join('"%s"' % c for c in wcols), staging.CAL))
    may_base = base.execute(may).fetchall()
    # 1. May
    rep.check(live.execute(may).fetchall() == may_base,
              'May: every row byte-identical with the backup (%d rows)' % len(may_base))
    rep.check(staging.month_totals(live, 'period_month', '2026-05') == staging.MAY['period']
              and staging.month_totals(live, staging.CAL, '2026-05') == staging.MAY['calendar'],
              'May: period %s, calendar %s (owner: %s, %s)'
              % (staging.month_totals(live, 'period_month', '2026-05'),
                 staging.month_totals(live, staging.CAL, '2026-05'), staging.MAY['period'], staging.MAY['calendar']))
    # 2. works of the backup
    before, after = rows_on(base, 'drone_works', wcols), rows_on(live, 'drone_works', wcols)
    at = wcols.index('drone_customer_id')
    missing = sorted(set(before) - set(after))
    changed = {i: after[i][at] for i in before if i in after and after[i] != before[i]}
    only_link = all(after[i][:at] + after[i][at + 1:] == before[i][:at] + before[i][at + 1:] for i in changed)
    rep.check(not missing and changed == expected_links() and only_link,
              'works of the backup: none missing, exactly the %d link rows changed, only drone_customer_id '
              '(missing %d, changed %d)' % (len(expected_links()), len(missing), len(changed)))
    flagged_old = live.execute('SELECT COUNT(*) FROM drone_works WHERE id <= ? AND payer_unidentified IS NOT NULL',
                               (max(before) if before else 0,)).fetchone()[0]
    rep.check(flagged_old == 0, 'no work of the backup carries payer_unidentified (found %d)' % flagged_old)
    # 3. the import batch
    top = max(before) if before else 0
    new = live.execute('SELECT COUNT(*), ROUND(SUM(area_ha), 2), ROUND(SUM(COALESCE(amount, 0)), 2), '
                       'SUM(import_batch IS NOT ?) FROM drone_works WHERE id > ?', (batch, top)).fetchone()
    rep.check(tuple(new[:3]) == staging.EXPECT_IMPORT and new[3] == 0,
              'new works: %s, all of batch %s (owner: %s)' % (tuple(new[:3]), batch, staging.EXPECT_IMPORT))
    ptz = [cid for cid, name in live.execute('SELECT id, name FROM drone_customers')
           if staging.norm(name) == staging.norm(staging.PTZ_CARD_NAME)]
    got = staging.routes(live, top + 1, ptz[0] if len(ptz) == 1 else None)
    for key, want in sorted(staging.EXPECT_ROUTES.items()):
        rep.check(got.get(key) == want, 'books -> %s: %s, owner decided %s' % (key, got.get(key), want))
    forbid = staging.parse_forbid(FORBID)
    rep.check(not any(k.isdigit() and int(k) in forbid for k in got),
              'no row of the books on a forbidden card %s' % FORBID)
    # 4. June
    june = staging.month_totals(base, 'period_month', '2026-06')
    want = (june[0] + staging.EXPECT_IMPORT[0], round(june[1] + staging.EXPECT_IMPORT[1], 2))
    rep.check(staging.month_totals(live, 'period_month', '2026-06') == want,
              'June by book month: %s, backup + import = %s'
              % (staging.month_totals(live, 'period_month', '2026-06'), want))
    # 5. cards and aliases
    ccols = columns(base, 'drone_customers')
    cb, ca = rows_on(base, 'drone_customers', ccols), rows_on(live, 'drone_customers', ccols)
    t = ccols.index('customer_type')
    retyped = {i for i in cb if i in ca and ca[i] != cb[i]}
    same_but_type = all(ca[i][:t] + ca[i][t + 1:] == cb[i][:t] + cb[i][t + 1:] for i in retyped)
    canonical = {i: ca.get(i, (None,) * len(ccols))[t] for i in sorted(staging.CANONICAL)}
    rep.check(set(cb) <= set(ca) and retyped <= set(staging.CANONICAL) and same_but_type
              and all(v == staging.CANONICAL_TYPE for v in canonical.values()),
              'cards of the backup unchanged but the type of %s, now %s (found %s)'
              % (sorted(staging.CANONICAL), staging.CANONICAL_TYPE, canonical))
    created = {}
    for i in set(ca) - set(cb):
        created[ca[i][t]] = created.get(ca[i][t], 0) + 1
    ptz_type = ca[ptz[0]][t] if len(ptz) == 1 else None
    rep.check(created == NEW_CARDS and len(ptz) == 1 and ptz[0] not in cb and ptz_type == staging.CANONICAL_TYPE,
              'new cards %s (decided %s), one %s card, internal (found %s, %s)'
              % (created, NEW_CARDS, staging.PTZ_CARD_NAME.encode('ascii', 'replace').decode(), ptz, ptz_type))
    acols = columns(base, 'drone_customer_aliases')
    ab, aa = rows_on(base, 'drone_customer_aliases', acols), rows_on(live, 'drone_customer_aliases', acols)
    k = acols.index('drone_customer_id')
    moved = {i: aa[i][k] for i in ab if i in aa and aa[i] != ab[i]}
    only_card = all(aa[i][:k] + aa[i][k + 1:] == ab[i][:k] + ab[i][k + 1:] for i in moved)
    rep.check(set(ab) <= set(aa) and len(set(aa) - set(ab)) == NEW_ALIASES and len(moved) == REPOINTED_ALIASES
              and only_card and set(moved.values()) == set(ptz[:1]),
              'aliases: %d new (decided %d), %d of the backup moved, only to the %s card'
              % (len(set(aa) - set(ab)), NEW_ALIASES, len(moved), 'PTZ'))
    # 6. registry
    names = {r[0] for r in live.execute('SELECT name FROM schema_migrations')}
    old = {r[0] for r in base.execute('SELECT name FROM schema_migrations')}
    rep.check(names - old == expected_registry() and old <= names,
              'registry: +%d %s (decided +%d)' % (len(names - old), sorted(names - old), len(expected_registry())))
    return rep


def main(argv=None):
    ap = argparse.ArgumentParser(description='FIN-RECON-002 release v1.32 check. Reads only.')
    ap.add_argument('--db', required=True, help='the live database after the window')
    ap.add_argument('--baseline', required=True, help='the backup taken in the same window, before the release')
    ap.add_argument('--batch', default=BATCH)
    a = ap.parse_args(argv)
    for path in (a.db, a.baseline):
        if not os.path.isfile(path):
            print('ERROR: database not found: %s' % path.encode('ascii', 'replace').decode('ascii'))
            return 2
    rep = staging.Report()
    live, base = ro(a.db), ro(a.baseline, immutable=True)
    try:
        check(live, base, rep, a.batch)
    except sqlite3.Error as exc:
        # A database that does not have the shape the release leaves is a
        # FAIL with its reason, never a traceback the runbook cannot read.
        rep.check(False, 'the database could not be read as the release leaves it: %s' % exc)
    finally:
        live.close()
        base.close()
    print('RESULT: RELEASE CHECK %s (%d failed)' % ('FAILED' if rep.failed else 'PASSED', rep.failed))
    return 1 if rep.failed else 0


if __name__ == '__main__':
    sys.exit(main())
