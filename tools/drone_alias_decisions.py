#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Apply the OWNER-APPROVED customer decision table before a book import.

FIN-RECON-002. The table is NEW_CUSTOMER_DECISIONS.csv written by
tools/drone_import_alias_candidates.py, with the owner's columns filled
(protocol: docs/FIN_RECON_002_ALIAS_APPROVAL_PROTOCOL.md):

  DECISION  TARGET_CARD_ID  NEW_CARD_TYPE  meaning
  ALIAS     id of a card    (empty)        the spelling becomes an alias of that card
  NEW       (empty)         external|internal  a new card, of that type, with the spelling as alias
  HOLD      (empty)         (empty)        not decided -- the import of these books stays blocked
  SET_TYPE  id of a card    external|internal  an EXISTING card changes type only; the row's
                                           new_card_name_as_imported repeats the card's name as a
                                           guard and normalized_key stays empty

[REASON]: the tool decides NOTHING. Similarity hints in the table are never
used: a spelling goes to a card only because the owner wrote that card's id
next to it. The importer itself creates every unknown spelling as an
'external' card (apply_rows); applying the table first leaves the import
nothing to create, so every card that exists afterwards was named by the
owner, with the type the owner chose.

The table is checked against the importer's OWN parse of the books against
the same database. After the change -- on an in-memory copy for a dry run,
inside the write transaction for --apply -- the importer resolves the books
again and must put every row exactly where the table says: ALIAS rows on the
target card, NEW rows on their new card, rows already matched where they
were, and nothing left to create except HOLD spellings. Work rows, the cards
that existed and their aliases must be byte-identical (SET_TYPE: only
customer_type of the named card moves).

--apply: one BEGIN IMMEDIATE transaction -- validation again under the lock,
cards, aliases, postconditions and the schema_migrations row
DRONE_ALIAS_DECISIONS_<first 16 hex of the table's SHA-256>, one COMMIT. The
receipt is two-phase (<receipt>.pending before COMMIT, <receipt> after); a
re-run with the same --receipt completes or sets aside an interrupted one by
the state of the database. Refused while any HOLD remains.

  python tools/drone_alias_decisions.py --decisions <table.csv> --dir <books> --manifest <file> \\
      --db <copy of transport.db> --out <new dir>                         (dry run, writes only --out)
  python tools/drone_alias_decisions.py --decisions <table.csv> --dir <books> --manifest <file> \\
      --db <db> --apply --receipt <path>

Exit: 0 DRY RUN OK / APPLIED / ALREADY APPLIED; 1 NOT APPLIED (table
invalid, HOLD on --apply, postcondition, error before COMMIT); 2 input
missing; 3 NEEDS_RECOVERY; 4 --out exists. Console ASCII only.
"""
import argparse
import csv
import datetime
import hashlib
import io
import json
import os
import sqlite3
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
for _p in (HERE, os.path.dirname(HERE)):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import drone_import_alias_candidates as cand  # noqa: E402
import import_drone_works as idw  # noqa: E402

DECISIONS = ('ALIAS', 'NEW', 'HOLD', 'SET_TYPE')
CARD_TYPES = ('external', 'internal')
REQUIRED_COLUMNS = ('normalized_key', 'new_card_name_as_imported') + cand.DECISION_COLUMNS
SNAPSHOT_TABLES = ('drone_customers', 'drone_customer_aliases', 'drone_works', 'drone_operators',
                   'drone_units', 'schema_migrations')


class TableError(Exception):
    pass


def ascii_(text):
    return str(text).encode('ascii', 'replace').decode('ascii')


def now_utc():
    return datetime.datetime.now(datetime.timezone.utc).replace(tzinfo=None).isoformat()


def ro_connect(db):
    path = os.path.abspath(db).replace('\\', '/').replace('?', '%3f').replace('#', '%23')
    return sqlite3.connect('file:%s?mode=ro' % path, uri=True)


# ─── The table ────────────────────────────────────────────────────────────────

def read_table(path):
    """[(line number, row dict)]; raises TableError on a structural defect."""
    with open(path, 'rb') as fh:
        raw = fh.read()
    rows = list(csv.DictReader(io.StringIO(raw.decode('utf-8-sig'), newline=''), delimiter=';'))
    missing = [c for c in REQUIRED_COLUMNS if rows and c not in rows[0]] if rows else list(REQUIRED_COLUMNS)
    if missing:
        raise TableError('E_COLUMNS: missing column(s) %s' % missing)
    return [(i + 2, {k: (v or '').strip() for k, v in r.items() if k is not None}) for i, r in enumerate(rows)], \
        hashlib.sha256(raw).hexdigest()


def migration_id(table_sha):
    return 'DRONE_ALIAS_DECISIONS_' + table_sha[:16].upper()


def card_rows(conn):
    return {r[0]: r for r in conn.execute('SELECT * FROM drone_customers ORDER BY id')}


def validate(table, conn, new_keys, names):
    """Problems as 'E_CODE: text', and the plan {'alias': {key: id}, 'new': {key: (name, type)},
    'hold': [key], 'set_type': {id: (type, name)}}. Reads only."""
    problems, plan = [], {'alias': {}, 'new': {}, 'hold': [], 'set_type': {}}
    cards = {r[0]: r for r in conn.execute('SELECT id, name, is_active, customer_type FROM drone_customers')}
    by_name = {}
    for cid, name, _act, _t in cards.values():
        by_name.setdefault(idw.customer_key(name), []).append(cid)
    aliases = {r[0]: r[1] for r in conn.execute('SELECT normalized_key, drone_customer_id FROM drone_customer_aliases')}
    seen = {}
    for line, r in table:
        d, key, target, ctype = r['DECISION'].upper(), r['normalized_key'], r['TARGET_CARD_ID'], r['NEW_CARD_TYPE']
        where = 'line %d' % line
        if d not in DECISIONS:
            problems.append('E_DECISION: %s: %r is not one of %s' % (where, r['DECISION'], '/'.join(DECISIONS)))
            continue
        if d == 'SET_TYPE':
            if key:
                problems.append('E_SET_TYPE_KEY: %s: SET_TYPE names a card, not a spelling; leave normalized_key empty'
                                % where)
                continue
        else:
            if key in seen:
                problems.append('E_DUP_KEY: %s: key already decided on line %d' % (where, seen[key]))
                continue
            seen[key] = line
            if key != idw.customer_key(key):
                problems.append('E_KEY_NOT_NORMAL: %s: key is not in the importer\'s normal form' % where)
                continue
            if key not in new_keys:
                problems.append('E_KEY_UNKNOWN: %s: the import would not create this spelling '
                                '(already an alias, or not in these books)' % where)
                continue
            if key in aliases:
                problems.append('E_ALIAS_TAKEN: %s: the spelling is already an alias of card %s' % (where, aliases[key]))
                continue
        tid = None
        if d in ('ALIAS', 'SET_TYPE'):
            try:
                tid = int(target)
            except ValueError:
                problems.append('E_TARGET_ID_FORMAT: %s: TARGET_CARD_ID %r is not a number' % (where, target))
                continue
            if tid not in cards:
                problems.append('E_TARGET_MISSING: %s: card %d does not exist' % (where, tid))
                continue
        elif target:
            problems.append('E_EXTRA_FIELDS: %s: %s takes no TARGET_CARD_ID' % (where, d))
            continue
        if d in ('NEW', 'SET_TYPE'):
            if ctype not in CARD_TYPES:
                problems.append('E_TYPE: %s: NEW_CARD_TYPE must be %s, got %r' % (where, ' or '.join(CARD_TYPES), ctype))
                continue
        elif ctype:
            problems.append('E_EXTRA_FIELDS: %s: %s takes no NEW_CARD_TYPE' % (where, d))
            continue
        if d == 'ALIAS':
            if cards[tid][2] == 0:
                problems.append('E_TARGET_INACTIVE: %s: card %d is switched off' % (where, tid))
                continue
            plan['alias'][key] = tid
        elif d == 'NEW':
            name = names[key]
            if idw.customer_key(name) in by_name:
                problems.append('E_NEW_NAME_TAKEN: %s: card(s) %s already carry this name -- decide ALIAS'
                                % (where, by_name[idw.customer_key(name)]))
                continue
            plan['new'][key] = (name, ctype)
        elif d == 'HOLD':
            plan['hold'].append(key)
        else:
            if idw.customer_key(cards[tid][1]) != idw.customer_key(r['new_card_name_as_imported'].replace(' ↵ ', '\n')):
                problems.append('E_SET_TYPE_GUARD: %s: card %d is not named as the table says (renamed since?)'
                                % (where, tid))
                continue
            if tid in plan['set_type']:
                problems.append('E_DUP_KEY: %s: card %d retyped twice' % (where, tid))
                continue
            plan['set_type'][tid] = (ctype, cards[tid][1])
    for key in sorted(set(new_keys) - set(seen)):
        problems.append('E_KEY_MISSING: the import would create %r and the table does not decide it'
                        % ascii_(names.get(key, key)))
    return problems, plan


# ─── The change ───────────────────────────────────────────────────────────────

def insert_columns(conn, table, wanted):
    have = {r[1] for r in conn.execute('PRAGMA table_info("%s")' % table)}
    return [c for c in wanted if c in have]


def apply_plan(conn, plan, tag):
    """Inside an open transaction. Returns the diff rows."""
    stamp = now_utc()
    diff = []
    ccols = insert_columns(conn, 'drone_customers', ('name', 'customer_type', 'is_active', 'note', 'created_at',
                                                     'updated_at'))
    acols = insert_columns(conn, 'drone_customer_aliases', ('raw_name', 'normalized_key', 'drone_customer_id',
                                                            'is_active', 'created_at'))
    values = {'is_active': 1, 'note': tag, 'created_at': stamp, 'updated_at': stamp}
    for key, (name, ctype) in sorted(plan['new'].items()):
        row = dict(values, name=name, customer_type=ctype)
        cur = conn.execute('INSERT INTO drone_customers (%s) VALUES (%s)' % (', '.join(ccols), ', '.join('?' * len(ccols))),
                           [row[c] for c in ccols])
        cid = cur.lastrowid
        diff.append(('CREATE_CARD', cid, name, '', '', ctype))
        _alias(conn, acols, name, key, cid, stamp)
        diff.append(('CREATE_ALIAS', cid, name, key, '', ''))
    for key, tid in sorted(plan['alias'].items()):
        _alias(conn, acols, plan['raw'][key], key, tid, stamp)
        diff.append(('CREATE_ALIAS', tid, plan['raw'][key], key, '', ''))
    for tid, (ctype, name) in sorted(plan['set_type'].items()):
        before = conn.execute('SELECT customer_type FROM drone_customers WHERE id = ?', (tid,)).fetchone()[0]
        conn.execute('UPDATE drone_customers SET customer_type = ? WHERE id = ?', (ctype, tid))
        diff.append(('SET_TYPE', tid, name, '', before or '', ctype))
    return diff


def _alias(conn, acols, raw, key, cid, stamp):
    row = {'raw_name': raw, 'normalized_key': key, 'drone_customer_id': cid, 'is_active': 1, 'created_at': stamp}
    conn.execute('INSERT INTO drone_customer_aliases (%s) VALUES (%s)' % (', '.join(acols), ', '.join('?' * len(acols))),
                 [row[c] for c in acols])


def fingerprint(conn, table, where='', params=(), skip=()):
    cols = [r[1] for r in conn.execute('PRAGMA table_info("%s")' % table) if r[1] not in skip]
    h = hashlib.sha256()
    for row in conn.execute('SELECT %s FROM "%s" %s ORDER BY 1' % (', '.join('"%s"' % c for c in cols), table, where),
                            params):
        h.update(repr(tuple(row)).encode('utf-8'))
    return h.hexdigest()


def snapshot(conn, plan):
    retyped = sorted(plan['set_type'])
    marks = ', '.join('?' * len(retyped))
    return {
        'works': fingerprint(conn, 'drone_works'),
        'cards_untouched': fingerprint(conn, 'drone_customers', 'WHERE id NOT IN (%s)' % marks if retyped else '',
                                       retyped),
        'cards_retyped': fingerprint(conn, 'drone_customers', 'WHERE id IN (%s)' % marks, retyped,
                                     skip=('customer_type',)) if retyped else '',
        'aliases': fingerprint(conn, 'drone_customer_aliases'),
        'max_card': conn.execute('SELECT COALESCE(MAX(id), 0) FROM drone_customers').fetchone()[0],
        'max_alias': conn.execute('SELECT COALESCE(MAX(id), 0) FROM drone_customer_aliases').fetchone()[0],
        'n_cards': conn.execute('SELECT COUNT(*) FROM drone_customers').fetchone()[0],
        'n_aliases': conn.execute('SELECT COUNT(*) FROM drone_customer_aliases').fetchone()[0],
    }


def postconditions(conn, plan, before, books, manifest, first):
    """The importer, re-run against conn, must resolve every row as decided."""
    problems = []
    retyped = sorted(plan['set_type'])
    marks = ', '.join('?' * len(retyped))
    if fingerprint(conn, 'drone_works') != before['works']:
        problems.append('drone_works changed')
    if fingerprint(conn, 'drone_customers', 'WHERE id <= ?%s' % (' AND id NOT IN (%s)' % marks if retyped else ''),
                   [before['max_card']] + retyped) != before['cards_untouched']:
        problems.append('a card that existed before changed')
    if retyped and fingerprint(conn, 'drone_customers', 'WHERE id IN (%s)' % marks, retyped,
                               skip=('customer_type',)) != before['cards_retyped']:
        problems.append('a retyped card changed beyond customer_type')
    for tid, (ctype, _n) in plan['set_type'].items():
        if conn.execute('SELECT customer_type FROM drone_customers WHERE id = ?', (tid,)).fetchone()[0] != ctype:
            problems.append('card %d did not get type %s' % (tid, ctype))
    if fingerprint(conn, 'drone_customer_aliases', 'WHERE id <= ?', (before['max_alias'],)) != before['aliases']:
        problems.append('an alias that existed before changed')
    n_cards = conn.execute('SELECT COUNT(*) FROM drone_customers').fetchone()[0]
    n_aliases = conn.execute('SELECT COUNT(*) FROM drone_customer_aliases').fetchone()[0]
    if n_cards - before['n_cards'] != len(plan['new']):
        problems.append('cards created %d, decided NEW %d' % (n_cards - before['n_cards'], len(plan['new'])))
    if n_aliases - before['n_aliases'] != len(plan['new']) + len(plan['alias']):
        problems.append('aliases created %d, decided %d' % (n_aliases - before['n_aliases'],
                                                           len(plan['new']) + len(plan['alias'])))
    directory = idw.Directory(conn)
    files = sorted(f for f in os.listdir(books) if f.lower().endswith('.xlsx') and not f.startswith('~$'))
    second = idw.collect(books, idw.read_manifest(manifest)[0], files, directory, None)
    idw.resolve(second, directory)
    if cand.totals(second.rows) != cand.totals(first.rows):
        problems.append('the importer now reads %s, before %s' % (cand.totals(second.rows), cand.totals(first.rows)))
    left = {k for k, _n in second.created_customers}
    if left != set(plan['hold']):
        problems.append('the import would still create %d card(s), HOLD is %d' % (len(left), len(plan['hold'])))
    new_ids = {key: directory.aliases.get(key) for key in plan['new']}
    was = {(r['source_file'], r['source_sheet'], r['source_row']): r['drone_customer_id'] for r in first.rows}
    for row in second.rows:
        key = cand.group_key(row)
        want = (plan['alias'].get(key) or new_ids.get(key) or
                (None if key in plan['hold'] or not row['customer_raw'] else
                 was[(row['source_file'], row['source_sheet'], row['source_row'])]))
        if row['drone_customer_id'] != want:
            problems.append('row %s / %s / %s resolves to %s, decided %s'
                            % (ascii_(row['source_file']), ascii_(row['source_sheet']), row['source_row'],
                               row['drone_customer_id'], want))
            break
    return problems


def memory_copy(db):
    src = ro_connect(db)
    mem = sqlite3.connect(':memory:')
    mem.isolation_level = None
    try:
        for table in SNAPSHOT_TABLES:
            row = src.execute("SELECT sql FROM sqlite_master WHERE type = 'table' AND name = ?", (table,)).fetchone()
            if row is None:
                continue
            mem.execute(row[0])
            cols = ['"%s"' % c[1] for c in src.execute('PRAGMA table_info("%s")' % table)]
            mem.executemany('INSERT INTO "%s" (%s) VALUES (%s)' % (table, ', '.join(cols), ', '.join('?' * len(cols))),
                            src.execute('SELECT %s FROM "%s"' % (', '.join(cols), table)))
    finally:
        src.close()
    return mem


# ─── State, receipt, registry (same contract as the link migration) ───────────

def registry_has(conn, mid):
    if conn.execute("SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'schema_migrations'").fetchone() is None:
        return False
    return conn.execute('SELECT 1 FROM schema_migrations WHERE name = ?', (mid,)).fetchone() is not None


def state_of(db, mid, receipt_rows):
    """APPLIED / NOT_APPLIED / NEEDS_RECOVERY from the database alone.

    receipt_rows: [(key, card id or None, card name)] of the aliases a run of
    this table creates -- read from the pending/final receipt when there is one."""
    conn = ro_connect(db)
    try:
        registered = registry_has(conn, mid)
        present = 0
        for key, cid, name in receipt_rows:
            row = conn.execute('SELECT a.drone_customer_id, c.name FROM drone_customer_aliases a '
                               'JOIN drone_customers c ON c.id = a.drone_customer_id WHERE a.normalized_key = ?',
                               (key,)).fetchone()
            if row is not None and (row[0] == cid if cid is not None else idw.customer_key(row[1]) ==
                                    idw.customer_key(name)):
                present += 1
    finally:
        conn.close()
    if registered and present == len(receipt_rows):
        return 'APPLIED'
    if not registered and present == 0:
        return 'NOT_APPLIED'
    return 'NEEDS_RECOVERY'


def record_in_transaction(conn, mid, description, checksum):
    """Registry row on the SAME transaction as the cards and aliases -- see
    migrate_drones_works_merged_customer_link_001.py and
    migrate_drones_useful_area_001.py for why it cannot be migration_utils."""
    conn.execute('INSERT INTO schema_migrations (name, applied_at, checksum, description) VALUES (?, ?, ?, ?)',
                 (mid, now_utc(), checksum, description))


def write_pending(path, receipt):
    with open(path + '.pending', 'x', encoding='utf-8') as fh:
        json.dump(receipt, fh, ensure_ascii=False, indent=1)
        fh.flush()
        os.fsync(fh.fileno())


def commit(conn):
    conn.execute('COMMIT')


def finalize_receipt(path, receipt):
    tmp = path + '.tmp'
    with open(tmp, 'w', encoding='utf-8') as fh:
        json.dump(receipt, fh, ensure_ascii=False, indent=1)
        fh.flush()
        os.fsync(fh.fileno())
    os.replace(tmp, path)
    write_diff_csv(path + '.diff.csv', receipt['diff'])
    if os.path.exists(path + '.pending'):
        os.remove(path + '.pending')


def archive_pending(path):
    target = '%s.aborted-%s' % (path, datetime.datetime.now(datetime.timezone.utc).strftime('%Y%m%dT%H%M%SZ'))
    os.replace(path + '.pending', target)
    return target


def write_diff_csv(path, diff):
    with open(path, 'w', encoding='utf-8-sig', newline='') as fh:
        w = csv.writer(fh, delimiter=';')
        w.writerow(['action', 'card_id', 'card_name', 'normalized_key', 'type_before', 'type_after'])
        for row in diff:
            w.writerow([row[0], row[1], str(row[2]).replace('\n', ' ↵ '), row[3], row[4], row[5]])


def result(status, code, lines=()):
    for line in lines:
        print(ascii_(line))
    print('RESULT: %s' % status)
    return code


# ─── Main ─────────────────────────────────────────────────────────────────────

def importer_view(args):
    first, _directory, _problems = cand.build(args.dir, args.manifest, args.db)
    _linked, new, _empty = cand.partition(first)
    names = dict(first.created_customers)
    return first, new, names


def plan_summary(plan, new):
    def tot(keys):
        rows = [r for k in keys for r in new.get(k, [])]
        return cand.totals(rows)
    return ['ALIAS    %3d spelling(s): %d rows / %.2f ha / %.2f' % ((len(plan['alias']),) + tot(plan['alias'])),
            'NEW      %3d card(s):     %d rows / %.2f ha / %.2f' % ((len(plan['new']),) + tot(plan['new'])),
            '  of them internal: %d' % sum(1 for _n, t in plan['new'].values() if t == 'internal'),
            'HOLD     %3d spelling(s): %d rows / %.2f ha / %.2f' % ((len(plan['hold']),) + tot(plan['hold'])),
            'SET_TYPE %3d existing card(s)' % len(plan['set_type'])]


def dry_run(args, table, table_sha):
    mid = migration_id(table_sha)
    conn = ro_connect(args.db)
    try:
        if registry_has(conn, mid):
            return result('ALREADY APPLIED', 0, ['%s: this table is already applied.' % mid])
    finally:
        conn.close()
    first, new, names = importer_view(args)
    mem = memory_copy(args.db)
    try:
        problems, plan = validate(table, mem, set(new), names)
        if problems:
            return result('NOT APPLIED', 1, ['TABLE REFUSED -- nothing changed:'] + ['  ' + p for p in problems])
        plan['raw'] = {k: names[k] for k in plan['alias']}
        before = snapshot(mem, plan)
        mem.execute('BEGIN')
        diff = apply_plan(mem, plan, 'alias decisions %s' % mid)
        post = postconditions(mem, plan, before, args.dir, args.manifest, first)
        mem.execute('ROLLBACK')
    finally:
        mem.close()
    if post:
        return result('NOT APPLIED', 1, ['POSTCONDITION FAILED on the in-memory copy:'] + ['  ' + p for p in post])
    os.makedirs(args.out)
    write_diff_csv(os.path.join(args.out, 'DECISIONS_DIFF.csv'), diff)
    lines = ['%s: DRY RUN on an in-memory copy, nothing written to the database.' % mid,
             'table SHA-256 %s' % table_sha] + plan_summary(plan, new)
    if plan['hold']:
        lines.append('IMPORT BLOCKED: %d spelling(s) on HOLD; --apply refuses until they are decided.'
                     % len(plan['hold']))
    with open(os.path.join(args.out, 'SUMMARY.txt'), 'w', encoding='utf-8') as fh:
        fh.write('\n'.join(lines + ['RESULT: DRY RUN OK']) + '\n')
    return result('DRY RUN OK', 0, lines)


def apply(args, table, table_sha):
    mid = migration_id(table_sha)
    if not args.receipt:
        return result('NOT APPLIED', 1, ['ERROR: --apply needs --receipt <path>.'])
    receipt_path = os.path.abspath(args.receipt)
    pending = receipt_path + '.pending'
    for path in (pending, receipt_path):
        if os.path.exists(path):
            try:
                with open(path, encoding='utf-8') as fh:
                    old = json.load(fh)
            except ValueError:
                return result('NEEDS_RECOVERY', 3, ['%s exists and is unreadable.' % os.path.basename(path)])
            if old.get('migration_id') != mid:
                return result('NOT APPLIED', 1, ['ERROR: %s belongs to another table.' % os.path.basename(path)])
            state = state_of(args.db, mid, [(k, c, n) for k, c, n in old['aliases']])
            if path == pending and state == 'APPLIED':
                old.update(status='APPLIED', recovered_at_utc=now_utc())
                finalize_receipt(receipt_path, old)
                return result('APPLIED', 0, ['%s: the previous run committed; receipt completed now.' % mid])
            if path == pending and state == 'NOT_APPLIED':
                kept = archive_pending(receipt_path)
                print('NOTE: the previous run stopped before COMMIT; its pending receipt is kept as %s.'
                      % ascii_(os.path.basename(kept)))
                continue
            if path == receipt_path and state == 'APPLIED':
                return result('ALREADY APPLIED', 0, ['%s: already applied.' % mid])
            return result('NEEDS_RECOVERY', 3, ['%s: the receipt and the database disagree; nothing changed.' % mid])
    conn = ro_connect(args.db)
    try:
        if registry_has(conn, mid):
            return result('ALREADY APPLIED', 0, ['%s: already applied.' % mid])
    finally:
        conn.close()
    first, new, names = importer_view(args)
    import migration_utils
    migration_utils.DB_PATH = os.path.abspath(args.db)
    migration_utils.ensure_schema_migrations_table()
    conn = sqlite3.connect(args.db)
    conn.isolation_level = None
    pending_written = False
    try:
        try:
            conn.execute('BEGIN IMMEDIATE')
            if registry_has(conn, mid):
                conn.execute('ROLLBACK')
                return result('ALREADY APPLIED', 0, ['%s: already applied.' % mid])
            problems, plan = validate(table, conn, set(new), names)
            if not problems and plan['hold']:
                problems = ['HOLD: %d spelling(s) are not decided; the import stays blocked.' % len(plan['hold'])]
            if problems:
                conn.execute('ROLLBACK')
                return result('NOT APPLIED', 1, ['TABLE REFUSED -- nothing changed:'] + ['  ' + p for p in problems])
            plan['raw'] = {k: names[k] for k in plan['alias']}
            before = snapshot(conn, plan)
            diff = apply_plan(conn, plan, 'alias decisions %s' % mid)
            post = postconditions(conn, plan, before, args.dir, args.manifest, first)
            if post:
                conn.execute('ROLLBACK')
                return result('NOT APPLIED', 1, ['POSTCONDITION FAILED -- rolled back:'] + ['  ' + p for p in post])
            with open(os.path.abspath(__file__), 'rb') as fh:
                checksum = hashlib.sha256(fh.read()).hexdigest()
            record_in_transaction(conn, mid, 'FIN-RECON-002 alias decisions, table %s: %d ALIAS, %d NEW, %d SET_TYPE'
                                  % (table_sha, len(plan['alias']), len(plan['new']), len(plan['set_type'])), checksum)
            aliases = [(k, t, plan['raw'][k]) for k, t in plan['alias'].items()] + \
                      [(k, None, n) for k, (n, _t) in plan['new'].items()]
            receipt = {'migration_id': mid, 'status': 'PENDING', 'table_sha256': table_sha,
                       'tool_sha256': checksum, 'db_basename': os.path.basename(args.db),
                       'started_at_utc': now_utc(), 'aliases': aliases, 'diff': diff,
                       'set_type': {str(k): v for k, v in plan['set_type'].items()},
                       'fingerprints_before': {k: v for k, v in before.items()}}
            write_pending(receipt_path, receipt)
            pending_written = True
        except Exception as exc:
            if conn.in_transaction:
                conn.execute('ROLLBACK')
            if pending_written:
                os.remove(pending)
            return result('NOT APPLIED', 1, ['ERROR before COMMIT, rolled back: %s: %s' % (type(exc).__name__, exc)])
        try:
            commit(conn)
        except Exception as exc:
            if conn.in_transaction:
                conn.execute('ROLLBACK')
            print(ascii_('WARNING: COMMIT raised %s: %s' % (type(exc).__name__, exc)))
            state = state_of(args.db, mid, aliases)
            if state == 'APPLIED':
                receipt.update(status='APPLIED', committed_at_utc=now_utc())
                finalize_receipt(receipt_path, receipt)
                return result('APPLIED', 0, ['%s: the database holds the change; receipt completed.' % mid])
            if state == 'NOT_APPLIED':
                archive_pending(receipt_path)
                return result('NOT APPLIED', 1, ['%s: COMMIT did not happen.' % mid])
            return result('NEEDS_RECOVERY', 3, ['%s: COMMIT failed and the database is half-way.' % mid])
    finally:
        conn.close()
    receipt.update(status='APPLIED', committed_at_utc=now_utc())
    finalize_receipt(receipt_path, receipt)
    return result('APPLIED', 0, ['%s: APPLIED.' % mid] + plan_summary(plan, new) +
                  ['receipt and control diff: %s, %s.diff.csv' % (os.path.basename(receipt_path),
                                                                  os.path.basename(receipt_path))])


def main(argv=None):
    ap = argparse.ArgumentParser(description='Apply the owner-approved customer decision table.')
    ap.add_argument('--decisions', required=True)
    ap.add_argument('--dir', required=True)
    ap.add_argument('--manifest', required=True)
    ap.add_argument('--db', required=True)
    ap.add_argument('--out', default=None, help='dry run: new directory for the control diff')
    ap.add_argument('--apply', action='store_true')
    ap.add_argument('--receipt', default=None)
    a = ap.parse_args(argv)
    for path in (a.decisions, a.dir, a.manifest, a.db):
        if not os.path.exists(path):
            print('ERROR: not found: %s' % ascii_(path))
            return 2
    try:
        table, table_sha = read_table(a.decisions)
    except TableError as exc:
        return result('NOT APPLIED', 1, ['TABLE REFUSED: %s' % exc])
    if a.apply:
        return apply(a, table, table_sha)
    if not a.out:
        print('ERROR: a dry run needs --out <new directory>')
        return 2
    if os.path.exists(a.out):
        print('ERROR: --out already exists; refusing')
        return 4
    return dry_run(a, table, table_sha)


if __name__ == '__main__':
    sys.exit(main())
