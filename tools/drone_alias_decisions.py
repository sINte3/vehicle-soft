#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Apply the OWNER-APPROVED customer decision table before a book import.

FIN-RECON-002. The table is NEW_CUSTOMER_DECISIONS.csv written by
tools/drone_import_alias_candidates.py -- one line per spelling of the books,
with what the import does with it today (import_status) -- and the owner's
columns filled (protocol: docs/FIN_RECON_002_ALIAS_APPROVAL_PROTOCOL.md).
EVERY spelling of the books needs a decision allowed for its status:

  import_status  DECISION      columns it needs                 what happens
  CREATE         ALIAS         TARGET_CARD_ID + TARGET_CARD_NAME  the spelling becomes an alias of that card
                 NEW           NEW_CARD_TYPE                     a new card named as the spelling
                 GROUP_NEW     NEW_CARD_NAME + NEW_CARD_TYPE      ONE new card per NEW_CARD_NAME; every
                                                                 spelling of the group becomes its alias
                 HOLD          --                                not decided: the import stays blocked
  MATCHED        KEEP          CURRENT_CARD_ID                   stays where its alias already leads
                 REPOINT       CURRENT_CARD_ID + EXPECTED_SOURCE_ROWS and either
                               TARGET_CARD_ID + TARGET_CARD_NAME or NEW_CARD_NAME of a GROUP_NEW
                                                                 the EXISTING alias moves to that card
  UNIDENTIFIED   UNIDENTIFIED  EXPECTED_SOURCE_ROWS (+ CURRENT_CARD_ID when an alias exists)
                                                                 a generic citizen: no card (importer rule)
  (key empty)    SET_TYPE      TARGET_CARD_ID + TARGET_CARD_NAME + NEW_CARD_TYPE
                                                                 an EXISTING card changes type only

Any other filled column is refused. TARGET_CARD_NAME repeats the card's name
from the directory as the owner saw it, and CURRENT_CARD_ID the card the
spelling resolves to today: a card renamed, switched off or resolved
elsewhere since the table was approved stops the tool -- IDs are never
guessed (owner's requirement of 27.10.2026). EXPECTED_SOURCE_ROWS is the JSON
list of [file, sheet, row] of the spelling, copied from the candidates
table; for the exceptions -- an existing match overridden (REPOINT) or a
generic citizen (UNIDENTIFIED) -- it is mandatory and must equal the parse
exactly.

--forbid-cards lists cards no spelling of these books may end on -- for
June 2026 the owner named the generic «Фукаро» cards and the territorial
«Ғиждувон ПТЗ» cards 684-692. ALIAS, REPOINT or KEEP onto one is refused.

[REASON]: the tool decides NOTHING. Similarity hints in the table are never
used: a spelling goes to a card only because the owner wrote that card's id
next to it. The importer itself creates every unknown spelling as an
'external' card (apply_rows); applying the table first leaves the import
nothing to create, so every card that exists afterwards was named by the
owner, with the type the owner chose.

The table is checked against the importer's OWN parse of the books against
the same database. After the change -- on an in-memory copy for a dry run,
inside the write transaction for --apply -- the importer resolves the books
again and must put every row exactly where the table says, and nothing may
be left to create except HOLD spellings. Work rows and the cards that existed
must be byte-identical (SET_TYPE: only customer_type of the named card
moves); aliases that existed too, except the drone_customer_id of a REPOINT.
A REPOINT moves the alias, not the works already stored under it -- those
keep their card (see tests/test_drone_alias_move_does_not_relink.py).

--apply: one BEGIN IMMEDIATE transaction -- validation again under the lock,
cards, aliases, postconditions and the schema_migrations row
DRONE_ALIAS_DECISIONS_<first 16 hex of the table's SHA-256>, one COMMIT. The
receipt is two-phase (<receipt>.pending before COMMIT, <receipt> after); a
re-run with the same --receipt completes or sets aside an interrupted one by
the state of the database. Refused while any HOLD remains.

  python tools/drone_alias_decisions.py --decisions <table.csv> --dir <books> --manifest <file> \\
      --db <copy of transport.db> --out <new dir> [--forbid-cards 589,684-692,803]   (dry run)
  python tools/drone_alias_decisions.py --decisions <table.csv> --dir <books> --manifest <file> \\
      --db <db> --apply --receipt <path> [--forbid-cards ...]

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

DECISIONS = ('ALIAS', 'NEW', 'GROUP_NEW', 'HOLD', 'KEEP', 'REPOINT', 'UNIDENTIFIED', 'SET_TYPE')
ALLOWED = {cand.STATUS_CREATE: ('ALIAS', 'NEW', 'GROUP_NEW', 'HOLD'),
           cand.STATUS_MATCHED: ('KEEP', 'REPOINT'),
           cand.STATUS_UNIDENTIFIED: ('UNIDENTIFIED',)}
GUARD_COLUMNS = ('TARGET_CARD_ID', 'TARGET_CARD_NAME', 'NEW_CARD_NAME', 'NEW_CARD_TYPE', 'CURRENT_CARD_ID',
                 'EXPECTED_SOURCE_ROWS')
# Columns each decision may fill; every other guard column must stay empty.
FILLABLE = {
    'ALIAS': ('TARGET_CARD_ID', 'TARGET_CARD_NAME', 'EXPECTED_SOURCE_ROWS'),
    'NEW': ('NEW_CARD_TYPE', 'EXPECTED_SOURCE_ROWS'),
    'GROUP_NEW': ('NEW_CARD_NAME', 'NEW_CARD_TYPE', 'EXPECTED_SOURCE_ROWS'),
    'HOLD': ('EXPECTED_SOURCE_ROWS',),
    'KEEP': ('CURRENT_CARD_ID', 'EXPECTED_SOURCE_ROWS'),
    'REPOINT': ('TARGET_CARD_ID', 'TARGET_CARD_NAME', 'NEW_CARD_NAME', 'CURRENT_CARD_ID', 'EXPECTED_SOURCE_ROWS'),
    'UNIDENTIFIED': ('CURRENT_CARD_ID', 'EXPECTED_SOURCE_ROWS'),
    'SET_TYPE': ('TARGET_CARD_ID', 'TARGET_CARD_NAME', 'NEW_CARD_TYPE'),
}
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


def name_key(text):
    """A card name as the table shows it («↵» for a line break) -> comparison key."""
    return idw.customer_key(str(text or '').replace('↵', ' '))


def parse_forbid(text):
    """'589,684-692' -> {589, 684, ..., 692}. Raises ValueError on garbage."""
    out = set()
    for part in (text or '').replace(' ', '').split(','):
        if not part:
            continue
        if '-' in part:
            lo, hi = (int(x) for x in part.split('-', 1))
            out.update(range(lo, hi + 1))
        else:
            out.add(int(part))
    return out


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


def _source_rows_equal(text, rows):
    try:
        given = json.loads(text)
        given = sorted([str(f), str(s), int(n)] for f, s, n in given)
    except (ValueError, TypeError):
        return None
    return given == json.loads(cand.source_rows(rows))


def validate(table, conn, keys, forbid=frozenset()):
    """Problems as 'E_CODE: text', and the plan. Reads only.

    keys: cand.spellings() of the importer's parse against this database.
    plan: {'alias': {key: id}, 'new': {key: (name, type)}, 'groups': {name key: {'name', 'type', 'keys'}},
           'repoint': {key: (from id, to id or None, group name key or None)}, 'keep': {key: id},
           'unidentified': [key], 'hold': [key], 'set_type': {id: (type, name)}, 'decision': {key: D}}
    """
    problems = []
    plan = {'alias': {}, 'new': {}, 'groups': {}, 'repoint': {}, 'keep': {}, 'unidentified': [], 'hold': [],
            'set_type': {}, 'decision': {}}
    cards = {r[0]: r for r in conn.execute('SELECT id, name, is_active, customer_type FROM drone_customers')}
    by_name = {}
    for cid, name, _act, _t in cards.values():
        by_name.setdefault(idw.customer_key(name), []).append(cid)
    aliases = {r[0]: r[1] for r in conn.execute('SELECT normalized_key, drone_customer_id FROM drone_customer_aliases')}
    seen, repoint_groups = {}, {}

    def card_ok(where, tid_text, name_text, role='target', check_forbid=True):
        try:
            tid = int(tid_text)
        except ValueError:
            problems.append('E_TARGET_ID_FORMAT: %s: %s card id %r is not a number' % (where, role, tid_text))
            return None
        if tid not in cards:
            problems.append('E_TARGET_MISSING: %s: card %d does not exist' % (where, tid))
            return None
        if name_key(name_text) != idw.customer_key(cards[tid][1]):
            problems.append('E_TARGET_NAME: %s: card %d is not named as the table says (renamed since, or '
                            'another id?)' % (where, tid))
            return None
        if cards[tid][2] is not None and int(cards[tid][2]) == 0:
            problems.append('E_TARGET_INACTIVE: %s: card %d is switched off' % (where, tid))
            return None
        if check_forbid and tid in forbid:
            problems.append('E_TARGET_FORBIDDEN: %s: card %d is on the --forbid-cards list' % (where, tid))
            return None
        return tid

    for line, r in table:
        d, key = r['DECISION'].upper(), r['normalized_key']
        where = 'line %d' % line
        if d not in DECISIONS:
            problems.append('E_DECISION: %s: %r is not one of %s' % (where, r['DECISION'], '/'.join(DECISIONS)))
            continue
        extra = [c for c in GUARD_COLUMNS if r.get(c) and c not in FILLABLE[d]]
        if extra:
            problems.append('E_EXTRA_FIELDS: %s: %s takes no %s' % (where, d, ', '.join(extra)))
            continue
        if d == 'SET_TYPE':
            if key:
                problems.append('E_SET_TYPE_KEY: %s: SET_TYPE names a card, not a spelling; leave normalized_key '
                                'empty' % where)
                continue
            if r['NEW_CARD_TYPE'] not in CARD_TYPES:
                problems.append('E_TYPE: %s: NEW_CARD_TYPE must be %s' % (where, ' or '.join(CARD_TYPES)))
                continue
            # A type is a label of the card, not a destination of rows: the
            # forbid list does not apply.
            tid = card_ok(where, r['TARGET_CARD_ID'], r['TARGET_CARD_NAME'], check_forbid=False)
            if tid is None:
                continue
            if tid in plan['set_type']:
                problems.append('E_DUP_KEY: %s: card %d retyped twice' % (where, tid))
                continue
            plan['set_type'][tid] = (r['NEW_CARD_TYPE'], cards[tid][1])
            continue
        if key in seen:
            problems.append('E_DUP_KEY: %s: key already decided on line %d' % (where, seen[key]))
            continue
        seen[key] = line
        if key != idw.customer_key(key):
            problems.append('E_KEY_NOT_NORMAL: %s: key is not in the importer\'s normal form' % where)
            continue
        if key not in keys:
            problems.append('E_KEY_UNKNOWN: %s: no row of these books carries this spelling' % where)
            continue
        entry = keys[key]
        status = entry['status']
        if status not in ALLOWED:
            problems.append('E_KEY_MIXED: %s: the rows of this spelling resolve two ways' % where)
            continue
        if d not in ALLOWED[status]:
            problems.append('E_DECISION_FOR_STATUS: %s: %s is not allowed for a %s spelling (allowed: %s)'
                            % (where, d, status, '/'.join(ALLOWED[status])))
            continue
        if r['EXPECTED_SOURCE_ROWS'] or d in ('REPOINT', 'UNIDENTIFIED'):
            same = _source_rows_equal(r['EXPECTED_SOURCE_ROWS'], entry['rows']) if r['EXPECTED_SOURCE_ROWS'] else None
            if same is None:
                problems.append('E_SOURCE_ROWS_FORMAT: %s: EXPECTED_SOURCE_ROWS must be the JSON list of '
                                '[file, sheet, row] of the spelling' % where)
                continue
            if not same:
                problems.append('E_SOURCE_ROWS_DIFFER: %s: the books now carry this spelling on other rows than '
                                'the table says' % where)
                continue
        plan['decision'][key] = d
        if d in ('KEEP', 'REPOINT') or (d == 'UNIDENTIFIED' and key in aliases):
            try:
                current = int(r['CURRENT_CARD_ID'])
            except ValueError:
                problems.append('E_CURRENT_CARD: %s: CURRENT_CARD_ID %r is required and must be a number'
                                % (where, r['CURRENT_CARD_ID']))
                continue
            now = entry['card'] if d != 'UNIDENTIFIED' else aliases[key]
            if current != now:
                problems.append('E_CURRENT_CARD: %s: the spelling resolves to card %s, the table says %d'
                                % (where, now, current))
                continue
        elif d == 'UNIDENTIFIED' and r['CURRENT_CARD_ID']:
            problems.append('E_CURRENT_CARD: %s: the spelling has no alias, CURRENT_CARD_ID must be empty' % where)
            continue
        if d == 'ALIAS':
            tid = card_ok(where, r['TARGET_CARD_ID'], r['TARGET_CARD_NAME'])
            if tid is None:
                continue
            if key in aliases:
                problems.append('E_ALIAS_TAKEN: %s: the spelling is already an alias of card %s' % (where, aliases[key]))
                continue
            plan['alias'][key] = tid
        elif d == 'NEW':
            if r['NEW_CARD_TYPE'] not in CARD_TYPES:
                problems.append('E_TYPE: %s: NEW_CARD_TYPE must be %s' % (where, ' or '.join(CARD_TYPES)))
                continue
            name = dict_name(entry)
            if idw.customer_key(name) in by_name:
                problems.append('E_NEW_NAME_TAKEN: %s: card(s) %s already carry this name -- decide ALIAS'
                                % (where, by_name[idw.customer_key(name)]))
                continue
            plan['new'][key] = (name, r['NEW_CARD_TYPE'])
        elif d == 'GROUP_NEW':
            if r['NEW_CARD_TYPE'] not in CARD_TYPES or not r['NEW_CARD_NAME']:
                problems.append('E_TYPE: %s: GROUP_NEW needs NEW_CARD_NAME and NEW_CARD_TYPE %s'
                                % (where, ' or '.join(CARD_TYPES)))
                continue
            gkey = name_key(r['NEW_CARD_NAME'])
            if gkey in by_name:
                problems.append('E_NEW_NAME_TAKEN: %s: card(s) %s already carry the name %r -- decide ALIAS to it '
                                'after proving it is the same organisation' % (where, by_name[gkey],
                                                                              ascii_(r['NEW_CARD_NAME'])))
                continue
            group = plan['groups'].setdefault(gkey, {'name': r['NEW_CARD_NAME'].replace(' ↵ ', '\n'),
                                                     'type': r['NEW_CARD_TYPE'], 'keys': []})
            if group['type'] != r['NEW_CARD_TYPE']:
                problems.append('E_GROUP_TYPE: %s: the group %r is %s on another line' % (where, ascii_(group['name']),
                                                                                         group['type']))
                continue
            group['keys'].append(key)
        elif d == 'HOLD':
            plan['hold'].append(key)
        elif d == 'KEEP':
            if entry['card'] in forbid:
                problems.append('E_TARGET_FORBIDDEN: %s: KEEP leaves %d row(s) on card %d of the --forbid-cards list'
                                % (where, len(entry['rows']), entry['card']))
                continue
            plan['keep'][key] = entry['card']
        elif d == 'REPOINT':
            if bool(r['TARGET_CARD_ID']) == bool(r['NEW_CARD_NAME']):
                problems.append('E_REPOINT_TARGET: %s: REPOINT needs either TARGET_CARD_ID + TARGET_CARD_NAME or the '
                                'NEW_CARD_NAME of a GROUP_NEW, exactly one' % where)
                continue
            if r['TARGET_CARD_ID']:
                tid = card_ok(where, r['TARGET_CARD_ID'], r['TARGET_CARD_NAME'])
                if tid is None:
                    continue
                if tid == entry['card']:
                    problems.append('E_REPOINT_TARGET: %s: the alias already leads to card %d -- decide KEEP'
                                    % (where, tid))
                    continue
                plan['repoint'][key] = (entry['card'], tid, None)
            else:
                plan['repoint'][key] = (entry['card'], None, name_key(r['NEW_CARD_NAME']))
                repoint_groups[key] = line
        elif d == 'UNIDENTIFIED':
            plan['unidentified'].append(key)
    for key, line in sorted(repoint_groups.items()):
        if plan['repoint'][key][2] not in plan['groups']:
            problems.append('E_REPOINT_GROUP_UNKNOWN: line %d: no GROUP_NEW line of this table creates that card'
                            % line)
    new_names = {idw.customer_key(n): k for k, (n, _t) in plan['new'].items()}
    for gkey, group in plan['groups'].items():
        if gkey in new_names:
            problems.append('E_NEW_NAME_TAKEN: the group %r has the same name as the NEW spelling %r'
                            % (ascii_(group['name']), ascii_(new_names[gkey])))
    for key in sorted(set(keys) - set(seen)):
        problems.append('E_KEY_MISSING: the books carry %r (%s, %d row(s)) and the table does not decide it'
                        % (ascii_(dict_name(keys[key])), keys[key]['status'], len(keys[key]['rows'])))
    return problems, plan


def dict_name(entry):
    """The spelling as written in its first row -- the name a NEW card gets."""
    return entry['rows'][0]['customer_raw']


# ─── The change ───────────────────────────────────────────────────────────────

def insert_columns(conn, table, wanted):
    have = {r[1] for r in conn.execute('PRAGMA table_info("%s")' % table)}
    return [c for c in wanted if c in have]


def apply_plan(conn, plan, tag):
    """Inside an open transaction. Returns (diff rows, {group name key: new card id})."""
    stamp = now_utc()
    diff = []
    ccols = insert_columns(conn, 'drone_customers', ('name', 'customer_type', 'is_active', 'note', 'created_at',
                                                     'updated_at'))
    acols = insert_columns(conn, 'drone_customer_aliases', ('raw_name', 'normalized_key', 'drone_customer_id',
                                                            'is_active', 'created_at'))
    values = {'is_active': 1, 'note': tag, 'created_at': stamp, 'updated_at': stamp}

    def card(name, ctype):
        row = dict(values, name=name, customer_type=ctype)
        cur = conn.execute('INSERT INTO drone_customers (%s) VALUES (%s)' % (', '.join(ccols), ', '.join('?' * len(ccols))),
                           [row[c] for c in ccols])
        diff.append(('CREATE_CARD', cur.lastrowid, name, '', '', '', ctype))
        return cur.lastrowid

    for key, (name, ctype) in sorted(plan['new'].items()):
        cid = card(name, ctype)
        _alias(conn, acols, name, key, cid, stamp)
        diff.append(('CREATE_ALIAS', cid, name, key, '', '', ''))
    group_ids = {}
    for gkey, group in sorted(plan['groups'].items()):
        gid = group_ids[gkey] = card(group['name'], group['type'])
        for key in sorted(group['keys']):
            _alias(conn, acols, plan['raw'][key], key, gid, stamp)
            diff.append(('CREATE_ALIAS', gid, plan['raw'][key], key, '', '', ''))
    for key, tid in sorted(plan['alias'].items()):
        _alias(conn, acols, plan['raw'][key], key, tid, stamp)
        diff.append(('CREATE_ALIAS', tid, plan['raw'][key], key, '', '', ''))
    for key, (was, tid, gkey) in sorted(plan['repoint'].items()):
        to = tid if tid is not None else group_ids[gkey]
        cur = conn.execute('UPDATE drone_customer_aliases SET drone_customer_id = ? WHERE normalized_key = ? '
                           'AND drone_customer_id = ?', (to, key, was))
        if cur.rowcount != 1:
            raise LookupError('alias %r: expected to repoint 1 row, repointed %d' % (ascii_(key), cur.rowcount))
        diff.append(('REPOINT_ALIAS', to, plan['raw'][key], key, was, '', ''))
    for tid, (ctype, name) in sorted(plan['set_type'].items()):
        before = conn.execute('SELECT customer_type FROM drone_customers WHERE id = ?', (tid,)).fetchone()[0]
        conn.execute('UPDATE drone_customers SET customer_type = ? WHERE id = ?', (ctype, tid))
        diff.append(('SET_TYPE', tid, name, '', '', before or '', ctype))
    return diff, group_ids


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


def _not_in(column, values):
    return ('%s NOT IN (%s)' % (column, ', '.join('?' * len(values))), list(values)) if values else ('1 = 1', [])


def snapshot(conn, plan):
    retyped = sorted(plan['set_type'])
    moved = sorted(plan['repoint'])
    card_where, card_params = _not_in('id', retyped)
    alias_where, alias_params = _not_in('normalized_key', moved)
    return {
        'works': fingerprint(conn, 'drone_works'),
        'cards_untouched': fingerprint(conn, 'drone_customers', 'WHERE ' + card_where, card_params),
        'cards_retyped': fingerprint(conn, 'drone_customers', 'WHERE id IN (%s)' % ', '.join('?' * len(retyped)),
                                     retyped, skip=('customer_type',)) if retyped else '',
        'aliases': fingerprint(conn, 'drone_customer_aliases', 'WHERE ' + alias_where, alias_params),
        'aliases_moved': fingerprint(conn, 'drone_customer_aliases',
                                     'WHERE normalized_key IN (%s)' % ', '.join('?' * len(moved)), moved,
                                     skip=('drone_customer_id',)) if moved else '',
        'max_card': conn.execute('SELECT COALESCE(MAX(id), 0) FROM drone_customers').fetchone()[0],
        'max_alias': conn.execute('SELECT COALESCE(MAX(id), 0) FROM drone_customer_aliases').fetchone()[0],
        'n_cards': conn.execute('SELECT COUNT(*) FROM drone_customers').fetchone()[0],
        'n_aliases': conn.execute('SELECT COUNT(*) FROM drone_customer_aliases').fetchone()[0],
    }


def expected_card(plan, group_ids, directory, key):
    """Where a row of this spelling must land after the change; 'UNIDENTIFIED' or None (HOLD)."""
    d = plan['decision'].get(key)
    if d == 'ALIAS':
        return plan['alias'][key]
    if d == 'NEW':
        return directory.aliases.get(key, 'missing')
    if d == 'GROUP_NEW':
        return next(group_ids[g] for g, group in plan['groups'].items() if key in group['keys'])
    if d == 'REPOINT':
        _was, tid, gkey = plan['repoint'][key]
        return tid if tid is not None else group_ids[gkey]
    if d == 'KEEP':
        return plan['keep'][key]
    if d == 'UNIDENTIFIED':
        return 'UNIDENTIFIED'
    return None


def postconditions(conn, plan, before, books, manifest, first, group_ids, forbid=frozenset()):
    """The importer, re-run against conn, must resolve every row as decided. Returns (problems, second run)."""
    problems = []
    retyped = sorted(plan['set_type'])
    moved = sorted(plan['repoint'])
    card_where, card_params = _not_in('id', retyped)
    alias_where, alias_params = _not_in('normalized_key', moved)
    if fingerprint(conn, 'drone_works') != before['works']:
        problems.append('drone_works changed')
    if fingerprint(conn, 'drone_customers', 'WHERE id <= ? AND ' + card_where,
                   [before['max_card']] + card_params) != before['cards_untouched']:
        problems.append('a card that existed before changed')
    if retyped and fingerprint(conn, 'drone_customers', 'WHERE id IN (%s)' % ', '.join('?' * len(retyped)), retyped,
                               skip=('customer_type',)) != before['cards_retyped']:
        problems.append('a retyped card changed beyond customer_type')
    for tid, (ctype, _n) in plan['set_type'].items():
        if conn.execute('SELECT customer_type FROM drone_customers WHERE id = ?', (tid,)).fetchone()[0] != ctype:
            problems.append('card %d did not get type %s' % (tid, ctype))
    if fingerprint(conn, 'drone_customer_aliases', 'WHERE id <= ? AND ' + alias_where,
                   [before['max_alias']] + alias_params) != before['aliases']:
        problems.append('an alias that existed before changed')
    if moved and fingerprint(conn, 'drone_customer_aliases',
                             'WHERE normalized_key IN (%s)' % ', '.join('?' * len(moved)), moved,
                             skip=('drone_customer_id',)) != before['aliases_moved']:
        problems.append('a repointed alias changed beyond drone_customer_id')
    n_cards = conn.execute('SELECT COUNT(*) FROM drone_customers').fetchone()[0]
    n_aliases = conn.execute('SELECT COUNT(*) FROM drone_customer_aliases').fetchone()[0]
    want_cards = len(plan['new']) + len(plan['groups'])
    want_aliases = len(plan['new']) + len(plan['alias']) + sum(len(g['keys']) for g in plan['groups'].values())
    if n_cards - before['n_cards'] != want_cards:
        problems.append('cards created %d, decided %d' % (n_cards - before['n_cards'], want_cards))
    if n_aliases - before['n_aliases'] != want_aliases:
        problems.append('aliases created %d, decided %d' % (n_aliases - before['n_aliases'], want_aliases))
    directory = idw.Directory(conn)
    files = sorted(f for f in os.listdir(books) if f.lower().endswith('.xlsx') and not f.startswith('~$'))
    second = idw.collect(books, idw.read_manifest(manifest)[0], files, directory, None)
    idw.resolve(second, directory)
    if cand.totals(second.rows) != cand.totals(first.rows):
        problems.append('the importer now reads %s, before %s' % (cand.totals(second.rows), cand.totals(first.rows)))
    left = {k for k, _n in second.created_customers}
    if left != set(plan['hold']):
        problems.append('the import would still create %d card(s), HOLD is %d' % (len(left), len(plan['hold'])))
    for row in second.rows:
        if not row['customer_raw']:
            want, got = None, row['drone_customer_id']
        else:
            want = expected_card(plan, group_ids, directory, cand.group_key(row))
            got = 'UNIDENTIFIED' if row.get('payer_unidentified') else row['drone_customer_id']
        if got != want:
            problems.append('row %s / %s / %s resolves to %s, decided %s'
                            % (ascii_(row['source_file']), ascii_(row['source_sheet']), row['source_row'], got, want))
            break
        if got in forbid:
            problems.append('row %s / %s / %s ends on forbidden card %s'
                            % (ascii_(row['source_file']), ascii_(row['source_sheet']), row['source_row'], got))
            break
    return problems, second


def routing(first_keys, second, plan, directory_names):
    """The matrix «spelling -> card before -> card after» with rows / ha / amount."""
    after = {}
    for row in second.rows:
        if row['customer_raw']:
            after.setdefault(cand.group_key(row), set()).add(
                'UNIDENTIFIED' if row.get('payer_unidentified') else
                ('HOLD' if row['drone_customer_id'] is None else row['drone_customer_id']))
    out = []
    for key, entry in sorted(first_keys.items(), key=lambda kv: (kv[1]['status'], -cand.totals(kv[1]['rows'])[1])):
        n, ha, amount = cand.totals(entry['rows'])
        before = {cand.STATUS_CREATE: 'NEW', cand.STATUS_UNIDENTIFIED: 'NONE'}.get(entry['status'], entry['card'])
        target = sorted(after.get(key, {'?'}), key=str)
        out.append((key, dict_name(entry), entry['status'], plan['decision'].get(key, ''), n, ha, amount, before,
                    ' '.join(str(t) for t in target),
                    ' '.join(str(directory_names.get(t, '')) for t in target if isinstance(t, int))))
    return out


def by_target(second):
    """{card id or HOLD/UNIDENTIFIED: (rows, ha, amount)} of the books after the change."""
    groups = {}
    for row in second.rows:
        if not row['customer_raw']:
            k = 'NO_CUSTOMER'
        elif row.get('payer_unidentified'):
            k = 'UNIDENTIFIED'
        else:
            k = row['drone_customer_id'] if row['drone_customer_id'] is not None else 'HOLD'
        groups.setdefault(k, []).append(row)
    return {k: cand.totals(rows) for k, rows in groups.items()}


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
    this table creates or repoints -- read from the pending/final receipt when
    there is one."""
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
        w.writerow(['action', 'card_id', 'card_name', 'normalized_key', 'card_before', 'type_before', 'type_after'])
        for row in diff:
            w.writerow([row[0], row[1], str(row[2]).replace('\n', ' ↵ '), row[3], row[4], row[5], row[6]])


def write_routing_csv(path, rows):
    with open(path, 'w', encoding='utf-8-sig', newline='') as fh:
        w = csv.writer(fh, delimiter=';')
        w.writerow(['normalized_key', 'spelling', 'import_status', 'decision', 'rows', 'area_ha', 'amount',
                    'card_before', 'card_after', 'card_after_name'])
        for r in rows:
            w.writerow([r[0], str(r[1]).replace('\n', ' ↵ ')] + list(r[2:9]) + [str(r[9]).replace('\n', ' ↵ ')])


def result(status, code, lines=()):
    for line in lines:
        print(ascii_(line))
    print('RESULT: %s' % status)
    return code


# ─── Main ─────────────────────────────────────────────────────────────────────

def importer_view(args):
    first, _directory, _problems = cand.build(args.dir, args.manifest, args.db)
    return first, cand.spellings(first)


def raw_names(plan, keys):
    keys_needing_raw = set(plan['alias']) | set(plan['repoint']) | {k for g in plan['groups'].values()
                                                                   for k in g['keys']}
    return {k: dict_name(keys[k]) for k in keys_needing_raw}


def plan_summary(plan, keys):
    def tot(ks):
        return cand.totals([r for k in ks for r in keys[k]['rows']])
    group_keys = [k for g in plan['groups'].values() for k in g['keys']]
    return ['ALIAS        %3d spelling(s): %d rows / %.2f ha / %.2f' % ((len(plan['alias']),) + tot(plan['alias'])),
            'NEW          %3d card(s):     %d rows / %.2f ha / %.2f' % ((len(plan['new']),) + tot(plan['new'])),
            '  of them internal: %d' % sum(1 for _n, t in plan['new'].values() if t == 'internal'),
            'GROUP_NEW    %3d card(s) for %d spelling(s): %d rows / %.2f ha / %.2f'
            % ((len(plan['groups']), len(group_keys)) + tot(group_keys)),
            'REPOINT      %3d alias(es):   %d rows / %.2f ha / %.2f' % ((len(plan['repoint']),) + tot(plan['repoint'])),
            'KEEP         %3d spelling(s): %d rows / %.2f ha / %.2f' % ((len(plan['keep']),) + tot(plan['keep'])),
            'UNIDENTIFIED %3d spelling(s): %d rows / %.2f ha / %.2f (no card)'
            % ((len(plan['unidentified']),) + tot(plan['unidentified'])),
            'HOLD         %3d spelling(s): %d rows / %.2f ha / %.2f' % ((len(plan['hold']),) + tot(plan['hold'])),
            'SET_TYPE     %3d existing card(s)' % len(plan['set_type'])]


def target_lines(targets, names):
    out = ['AFTER THE CHANGE, rows of these books by card:']
    for k in sorted(targets, key=str):
        out.append('  %-14s %4d rows / %10.2f ha / %16.2f  %s'
                   % ((str(k),) + targets[k] + (ascii_(str(names.get(k, '')).replace('\n', ' / ')),)))
    return out


def dry_run(args, table, table_sha, forbid):
    mid = migration_id(table_sha)
    conn = ro_connect(args.db)
    try:
        if registry_has(conn, mid):
            return result('ALREADY APPLIED', 0, ['%s: this table is already applied.' % mid])
    finally:
        conn.close()
    first, keys = importer_view(args)
    mem = memory_copy(args.db)
    try:
        problems, plan = validate(table, mem, keys, forbid)
        if problems:
            return result('NOT APPLIED', 1, ['TABLE REFUSED -- nothing changed:'] + ['  ' + p for p in problems])
        plan['raw'] = raw_names(plan, keys)
        before = snapshot(mem, plan)
        mem.execute('BEGIN')
        diff, group_ids = apply_plan(mem, plan, 'alias decisions %s' % mid)
        post, second = postconditions(mem, plan, before, args.dir, args.manifest, first, group_ids, forbid)
        names = {r[0]: r[1] for r in mem.execute('SELECT id, name FROM drone_customers')}
        mem.execute('ROLLBACK')
    finally:
        mem.close()
    if post:
        return result('NOT APPLIED', 1, ['POSTCONDITION FAILED on the in-memory copy:'] + ['  ' + p for p in post])
    os.makedirs(args.out)
    write_diff_csv(os.path.join(args.out, 'DECISIONS_DIFF.csv'), diff)
    write_routing_csv(os.path.join(args.out, 'KEY_ROUTING.csv'), routing(keys, second, plan, names))
    lines = ['%s: DRY RUN on an in-memory copy, nothing written to the database.' % mid,
             'table SHA-256 %s' % table_sha] + plan_summary(plan, keys) + \
        target_lines(by_target(second), names)
    if plan['hold']:
        lines.append('IMPORT BLOCKED: %d spelling(s) on HOLD; --apply refuses until they are decided.'
                     % len(plan['hold']))
    with open(os.path.join(args.out, 'SUMMARY.txt'), 'w', encoding='utf-8') as fh:
        fh.write('\n'.join(lines + ['RESULT: DRY RUN OK']) + '\n')
    return result('DRY RUN OK', 0, lines)


def apply(args, table, table_sha, forbid):
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
    first, keys = importer_view(args)
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
            problems, plan = validate(table, conn, keys, forbid)
            if not problems and plan['hold']:
                problems = ['HOLD: %d spelling(s) are not decided; the import stays blocked.' % len(plan['hold'])]
            if problems:
                conn.execute('ROLLBACK')
                return result('NOT APPLIED', 1, ['TABLE REFUSED -- nothing changed:'] + ['  ' + p for p in problems])
            plan['raw'] = raw_names(plan, keys)
            before = snapshot(conn, plan)
            diff, group_ids = apply_plan(conn, plan, 'alias decisions %s' % mid)
            post, second = postconditions(conn, plan, before, args.dir, args.manifest, first, group_ids, forbid)
            if post:
                conn.execute('ROLLBACK')
                return result('NOT APPLIED', 1, ['POSTCONDITION FAILED -- rolled back:'] + ['  ' + p for p in post])
            with open(os.path.abspath(__file__), 'rb') as fh:
                checksum = hashlib.sha256(fh.read()).hexdigest()
            record_in_transaction(conn, mid, 'FIN-RECON-002 alias decisions, table %s: %d ALIAS, %d NEW, %d GROUP_NEW, '
                                  '%d REPOINT, %d SET_TYPE' % (table_sha, len(plan['alias']), len(plan['new']),
                                                               len(plan['groups']), len(plan['repoint']),
                                                               len(plan['set_type'])), checksum)
            group_names = {gkey: g['name'] for gkey, g in plan['groups'].items()}
            aliases = ([(k, t, plan['raw'][k]) for k, t in plan['alias'].items()] +
                       [(k, None, n) for k, (n, _t) in plan['new'].items()] +
                       [(k, None, g['name']) for g in plan['groups'].values() for k in g['keys']] +
                       [(k, t, plan['raw'][k]) if t is not None else (k, None, group_names[gk])
                        for k, (_w, t, gk) in plan['repoint'].items()])
            receipt = {'migration_id': mid, 'status': 'PENDING', 'table_sha256': table_sha,
                       'tool_sha256': checksum, 'db_basename': os.path.basename(args.db),
                       'started_at_utc': now_utc(), 'aliases': aliases, 'diff': diff,
                       'set_type': {str(k): v for k, v in plan['set_type'].items()},
                       'repoint': {k: [w, t, group_names.get(gk)] for k, (w, t, gk) in plan['repoint'].items()},
                       'unidentified': sorted(plan['unidentified']), 'forbid_cards': sorted(forbid),
                       'targets_after': {str(k): v for k, v in by_target(second).items()},
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
    return result('APPLIED', 0, ['%s: APPLIED.' % mid] + plan_summary(plan, keys) +
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
    ap.add_argument('--forbid-cards', default='',
                    help='card ids no spelling may end on, e.g. 589,684-692,803 (owner decision)')
    a = ap.parse_args(argv)
    for path in (a.decisions, a.dir, a.manifest, a.db):
        if not os.path.exists(path):
            print('ERROR: not found: %s' % ascii_(path))
            return 2
    try:
        forbid = frozenset(parse_forbid(a.forbid_cards))
    except ValueError:
        return result('NOT APPLIED', 1, ['ERROR: --forbid-cards must be ids and ranges, e.g. 589,684-692'])
    try:
        table, table_sha = read_table(a.decisions)
    except TableError as exc:
        return result('NOT APPLIED', 1, ['TABLE REFUSED: %s' % exc])
    if a.apply:
        return apply(a, table, table_sha, forbid)
    if not a.out:
        print('ERROR: a dry run needs --out <new directory>')
        return 2
    if os.path.exists(a.out):
        print('ERROR: --out already exists; refusing')
        return 4
    return dry_run(a, table, table_sha, forbid)


if __name__ == '__main__':
    sys.exit(main())
