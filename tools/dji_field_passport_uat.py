# -*- coding: utf-8 -*-
"""tools/dji_field_passport_uat.py -- DRONE-FIELD-PASSPORT-001 live UAT probe.

ASCII ONLY. The owner extracts this file on the server with
`git show <branch>:<path> | Set-Content -Encoding ASCII`; any non-ASCII byte
would be mangled on the way, so every Cyrillic string below is a \\u escape.
A test holds this.

WHAT IT DOES (on the staging copy of the production database)

  data   READ-ONLY (sqlite `mode=ro`) on --db. Picks every UAT case from the
         real data by query -- nothing is guessed or invented -- and proves,
         per case, the invariants the screens rely on:
           * membership: the card's confirmed set equals an INDEPENDENT SQL
             query (current attribution, same field_land_uuid, TIER1/TIER2);
           * totals: RAW / excluded / accepted of the card equal a hand sum of
             the Accepted Area provider items over exactly that set, and RAW
             equals SUM(drone_flights.area_ha) of that set;
           * no double count: no flight is confirmed in two records, and a
             real shared-md5 pair A/B keeps flight X in A only;
           * latest OBSERVED revision (not MAX(id)), historical truth, TIER2
             whose boundary bytes arrived after resolution;
           * store-level timings (3 runs).
  pages  Renders the three screens through the Flask test client on a
         THROWAWAY COPY (--page-copy), RU and UZ, checks status, numbers,
         sections and leaks, and times each page (3 runs). Importing `app`
         is a writer (create_all, WAL pragma) and the probe switches the
         admin's language ON THE COPY, so the copy is refused inside any
         `transport-report*` folder and when it is the --db file itself.

Nothing here writes to --db. Nothing talks to DJI or to Telegram.

Exit codes: 0 every gate passed; 1 usage; 2 database missing; 3 evidence
tables missing; 4 at least one BLOCKER gate failed (see the GATE lines).

Run (PowerShell, staging checkout as the working directory):

  & "C:\\Program Files\\Python314\\python.exe" <probe.py> --db instance\\transport.db --out-dir <run folder>\\probe --page-copy <run folder>\\probe\\page_copy.db

Output: ASCII summary on the console and in <out-dir>\\uat_summary.txt;
everything in <out-dir>\\uat_report.json (ensure_ascii). Field names go to
the JSON only; the console carries ids, uuids and numbers.
"""

import argparse
import json
import os
import re
import secrets
import statistics
import sys
import time
from datetime import date, datetime, timedelta

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if os.getcwd() not in sys.path:
    sys.path.insert(0, os.getcwd())
if REPO_ROOT not in sys.path:
    sys.path.insert(0, REPO_ROOT)

from dji_area import FIELD_RESOLVER_VERSION  # noqa: E402
from dji_area import accepted as acc  # noqa: E402
from dji_area import control_store  # noqa: E402
from dji_area import field as fld  # noqa: E402
from dji_area import field_store as fs  # noqa: E402
from dji_area import field_view as fv  # noqa: E402
from dji_area import store as dji_store  # noqa: E402

PROBE_ID = 'DRONE_FIELD_PASSPORT_UAT_001'
EXIT_OK, EXIT_USAGE, EXIT_NO_DB, EXIT_NO_TABLES, EXIT_GATE = 0, 1, 2, 3, 4
LOCAL = timedelta(hours=5)
T1, T2 = fld.TIER1_EXACT, fld.TIER2_STRONG
T3, T4 = fld.TIER3_SUPPORTED, fld.TIER4_GEOMETRIC
TOL_M2 = 0.5            # half a square metre: float noise, not a hectare

# Screen strings (RU unless noted), as \u escapes -- see the module note.
S_CONFIRMED = ('\u041f\u043e\u0434\u0442\u0432\u0435\u0440\u0436\u0434\u0451'
               '\u043d\u043d\u044b\u0435 \u0440\u0430\u0431\u043e\u0442\u044b')
S_CONFIRMED_UZ = ('\u0422\u0430\u0441\u0434\u0438\u049b\u043b\u0430\u043d'
                  '\u0433\u0430\u043d \u0438\u0448\u043b\u0430\u0440')
S_SHARED = ('\u0412\u044b\u043b\u0435\u0442\u044b \u043d\u0430 \u0442\u0435'
            '\u0445 \u0436\u0435 \u0433\u0440\u0430\u043d\u0438\u0446\u0430'
            '\u0445, \u043f\u0440\u0438\u0432\u044f\u0437\u0430\u043d\u043d'
            '\u044b\u0435 \u043a \u0434\u0440\u0443\u0433\u0438\u043c \u0437'
            '\u0430\u043f\u0438\u0441\u044f\u043c DJI')
S_HEADER = ('\u0417\u0430\u043f\u0438\u0441\u044c \u043f\u043e\u043b\u044f '
            'DJI')
S_LIST = ('\u0417\u0430\u043f\u0438\u0441\u0438 \u043f\u043e\u043b\u0435'
          '\u0439 DJI')
S_FIELD = '\u041f\u043e\u043b\u0435'
S_TOTAL = '\u0412\u0441\u0435\u0433\u043e'
S_RAW_TILE = 'DJI RAW, \u0433\u0430'
S_ACC_TILE = '\u041f\u0440\u0438\u043d\u044f\u0442\u043e, \u0433\u0430'
S_EXC_TILE = ('\u0418\u0441\u043a\u043b\u044e\u0447\u0435\u043d\u043e, '
              '\u0433\u0430')
S_NOT_CALC = ('\u043d\u0435 \u0440\u0430\u0441\u0441\u0447\u0438\u0442\u0430'
              '\u043d\u043e')
S_NOT_SAVED = '\u043d\u0435 \u0441\u043e\u0445\u0440\u0430\u043d\u0435\u043d'
S_COUNTED_OWN = ('\u0432\u044b\u043b\u0435\u0442 \u0443\u0447\u0442\u0451'
                 '\u043d \u0442\u043e\u043b\u044c\u043a\u043e \u0432 \u0441'
                 '\u0432\u043e\u0435\u0439 \u0437\u0430\u043f\u0438\u0441'
                 '\u0438')

# Leak markers. Coordinates: the area's latitude/longitude with FIVE or more
# decimals -- screens print hectares with at most four, so a hit is a
# coordinate, not a number of hectares.
FORBIDDEN = ('points_json', 'body_blob', 'body_text', 'body_path',
             'request_context', 'raw_json', 'contentMd5', 'upperRight',
             'X-Amz-Signature', 'Signature=', 'signedUrl', 'access_token',
             'Authorization', ':\\\\', 'C:\\', 'D:\\')
COORD_RE = re.compile(r'(?<![\d.])(?:3[7-9]|4[0-2]|6[3-6])\.\d{5,}')

STAT_RE = re.compile(r'<div class="vs-stat-label">(.*?)</div>\s*'
                     r'<div class="vs-stat-value is-num">(.*?)</div>', re.S)
NUM_RE = re.compile(r'-?\d[\d\s\u00a0\u202f]*\.\d+')


# --- small helpers ---------------------------------------------------------

class UsageError(Exception):
    pass


def ascii_safe(value):
    return str(value).encode('ascii', 'replace').decode('ascii')


def utc_bounds(date_from, date_to):
    start = datetime(date_from.year, date_from.month, date_from.day) - LOCAL
    end = datetime(date_to.year, date_to.month, date_to.day) \
        + timedelta(days=1) - LOCAL
    return start, end


def sql_time(dt):
    return dt.strftime('%Y-%m-%d %H:%M:%S')


def ha(m2):
    return None if m2 is None else round(float(m2) / 10000.0, 4)


def timed(fn, runs):
    times, result = [], None
    for _ in range(runs):
        t0 = time.perf_counter()
        result = fn()
        times.append((time.perf_counter() - t0) * 1000.0)
    return result, {'runs': runs, 'min_ms': round(min(times), 1),
                    'median_ms': round(statistics.median(times), 1),
                    'max_ms': round(max(times), 1)}


def number(text):
    match = NUM_RE.search(re.sub(r'<[^>]+>', ' ', text or ''))
    if not match:
        return None
    return float(re.sub(r'[\s\u00a0\u202f]', '', match.group(0)))


def section(html, title):
    marker = '<span class="vs-card-title">%s</span>' % title
    if marker not in html:
        return None
    return html.split(marker, 1)[1].split('<span class="vs-card-title">',
                                          1)[0]


def passports(fragment):
    return {int(i) for i in re.findall(r'/drones/flights/(\d+)/passport',
                                       fragment or '')}


def row_of(html, header):
    match = re.search(r'<th>%s</th><td>(.*?)</td></tr>' % re.escape(header),
                      html or '', re.S)
    return match.group(1) if match else None


def leaks(html):
    found = [m for m in FORBIDDEN if m in html]
    found += ['COORD:' + c for c in COORD_RE.findall(html)[:3]]
    return found


class Report(object):
    """Gates, cases and numbers; ASCII lines for the console."""

    def __init__(self):
        self.gates = []
        self.cases = {}
        self.data = {}
        self.lines = []

    def gate(self, gid, ok, blocker, detail):
        status = 'N/A' if ok is None else ('PASS' if ok else 'FAIL')
        self.gates.append({'id': gid, 'status': status, 'blocker': blocker,
                           'detail': detail})
        self.say('GATE %-34s %-4s %s %s' % (gid, status,
                                             'BLOCKER' if blocker else 'info',
                                             ascii_safe(detail)))

    def case(self, cid, value, detail=''):
        self.cases[cid] = value
        self.say('CASE %-24s %s %s' % (cid, ascii_safe(
            value if value is not None else 'NOT FOUND ON THIS COPY'),
            ascii_safe(detail)))

    def say(self, line):
        self.lines.append(line)

    def failed_blockers(self):
        return [g for g in self.gates
                if g['blocker'] and g['status'] == 'FAIL']


# --- data: independent queries ---------------------------------------------

def current_sql(alias='a'):
    """A current attribution row: not superseded, resolver version in force."""
    return ('%s.superseded_at IS NULL AND %s.field_resolver_version = ?'
            % (alias, alias))


def integrity(con, version):
    one = lambda sql, params=(): con.execute(sql, params).fetchone()[0]  # noqa
    return {
        'flights': one('SELECT COUNT(*) FROM drone_flights'),
        'flights_with_two_current_rows': one(
            'SELECT COUNT(*) FROM (SELECT flight_id FROM '
            'dji_field_attributions a WHERE ' + current_sql() +
            ' GROUP BY flight_id HAVING COUNT(*) > 1)', (version,)),
        'flights_confirmed_in_two_records': one(
            'SELECT COUNT(*) FROM (SELECT flight_id FROM '
            'dji_field_attributions a WHERE ' + current_sql() +
            ' AND a.field_attribution_tier IN (?, ?) AND '
            'a.field_land_uuid IS NOT NULL GROUP BY flight_id '
            'HAVING COUNT(DISTINCT a.field_land_uuid) > 1)',
            (version, T1, T2)),
        'land_records': one('SELECT COUNT(DISTINCT land_uuid) '
                            'FROM dji_land_revisions'),
        'land_revisions': one('SELECT COUNT(*) FROM dji_land_revisions'),
        'records_where_latest_observed_is_not_max_id': one(
            'SELECT COUNT(*) FROM (SELECT r.land_uuid, MAX(r.id) AS mx, '
            '(SELECT r2.id FROM dji_land_revisions r2 WHERE r2.land_uuid = '
            'r.land_uuid ORDER BY r2.last_seen_snapshot_id DESC, r2.id DESC '
            'LIMIT 1) AS lt FROM dji_land_revisions r GROUP BY r.land_uuid) '
            'WHERE mx <> lt'),
    }


def independent_confirmed(con, land, version):
    """{flight_id: area_ha} -- written here, NOT through field_store.

    The flight's LATEST current row (by id) must name the record and carry
    TIER1/TIER2; the flight must be in the journal (RAW comes from there).
    """
    rows = con.execute(
        'SELECT a.flight_id, f.area_ha FROM dji_field_attributions a '
        'JOIN drone_flights f ON f.dji_flight_id = a.flight_id '
        'WHERE ' + current_sql() + ' AND a.field_land_uuid = ? AND '
        'a.field_attribution_tier IN (?, ?) AND a.id = (SELECT MAX(a2.id) '
        'FROM dji_field_attributions a2 WHERE a2.flight_id = a.flight_id '
        'AND ' + current_sql('a2') + ')',
        (version, land, T1, T2, version)).fetchall()
    return {int(r[0]): float(r[1] or 0.0) for r in rows}


def latest_observed(con, land):
    row = con.execute(
        'SELECT id, name, serial_number, geometry_md5, last_seen_snapshot_id '
        'FROM dji_land_revisions WHERE land_uuid = ? '
        'ORDER BY last_seen_snapshot_id DESC, id DESC LIMIT 1',
        (land,)).fetchone()
    return dict(row) if row else None


def max_id_revision(con, land):
    row = con.execute(
        'SELECT id, name, serial_number, geometry_md5 FROM dji_land_revisions '
        'WHERE id = (SELECT MAX(id) FROM dji_land_revisions WHERE land_uuid '
        '= ?)', (land,)).fetchone()
    return dict(row) if row else None


# --- data: the card exactly as the route computes it -----------------------

def card_path(con, land, utc_start=None, utc_end=None):
    found = fs.field_flights(con, land, utc_start, utc_end)
    confirmed = fs.confirmed_members(found['rows'], land)
    provisional = fs.provisional_members(found['rows'], land)
    raw = {int(r['flight_id']): float(r['area_ha'] or 0.0) * 10000.0
           for r in confirmed + provisional}
    items = control_store.accepted_for(con, raw) if raw else {}
    conf_ids = [int(r['flight_id']) for r in confirmed]
    totals = acc.summarize(items[f] for f in conf_ids)
    return {'confirmed': conf_ids,
            'provisional': [int(r['flight_id']) for r in provisional],
            'rows': {int(r['flight_id']): r for r in found['rows']},
            'items': items, 'totals': totals, 'orphans': found['orphans']}


def hand_sum(items, ids):
    out = {'raw_m2': 0.0, 'accepted_m2': 0.0, 'excluded_m2': 0.0,
           'calc_raw_m2': 0.0, 'not_calculated': 0, 'accepted_missing': 0}
    for fid in ids:
        item = items[fid]
        out['raw_m2'] += item['raw_m2'] or 0.0
        if not item['calculated']:
            out['not_calculated'] += 1
            continue
        if item['accepted_m2'] is None:
            out['accepted_missing'] += 1
            continue
        out['accepted_m2'] += item['accepted_m2']
        out['excluded_m2'] += item['excluded_m2'] or 0.0
        out['calc_raw_m2'] += item['calc_raw_m2'] or 0.0
    return out


def totals_view(t):
    return {'records': t['records'], 'raw_ha': ha(t['raw_m2']),
            'accepted_ha': ha(t['accepted_m2']),
            'excluded_ha': ha(t['excluded_m2']),
            'not_calculated_records': t['not_calculated_records'],
            'not_calculated_raw_ha': ha(t['not_calculated_raw_m2']),
            'open_records': t['open_records'],
            'raw_mismatch_records': t['raw_mismatch_records'],
            'coverage': t['coverage'], 'control_ready': t['control_ready']}


def reconcile(con, rep, label, land, version):
    """Membership + totals of one card against independent sums."""
    card = card_path(con, land)
    ind = independent_confirmed(con, land, version)
    t = card['totals']
    hand = hand_sum(card['items'], card['confirmed'])
    raw_sql = sum(ind.values()) * 10000.0
    tiers = {}
    if card['confirmed']:
        marks = ','.join('?' * len(card['confirmed']))
        tiers = dict(con.execute(
            'SELECT a.field_attribution_tier, COUNT(*) FROM '
            'dji_field_attributions a WHERE ' + current_sql() +
            ' AND a.flight_id IN (%s) GROUP BY 1' % marks,
            [version] + card['confirmed']).fetchall())
    same = set(card['confirmed']) == set(ind)
    rep.gate('%s.membership' % label, same, True,
             'card=%d independent=%d only_card=%s only_sql=%s' % (
                 len(card['confirmed']), len(ind),
                 sorted(set(card['confirmed']) - set(ind))[:5],
                 sorted(set(ind) - set(card['confirmed']))[:5]))
    rep.gate('%s.confirmed_tiers_only_T1_T2' % label,
             set(tiers) <= {T1, T2}, True, 'tiers=%s' % tiers)
    rep.gate('%s.provisional_not_in_total' % label,
             not (set(card['provisional']) & set(card['confirmed'])), True,
             'provisional=%d' % len(card['provisional']))
    rep.gate('%s.raw_equals_sql_sum' % label,
             abs(raw_sql - t['raw_m2']) <= TOL_M2, True,
             'raw_ha card=%s sql=%s' % (ha(t['raw_m2']), ha(raw_sql)))
    rep.gate('%s.totals_equal_hand_sum_of_provider' % label,
             abs(hand['accepted_m2'] - t['accepted_m2']) <= TOL_M2
             and abs(hand['excluded_m2'] - t['excluded_m2']) <= TOL_M2
             and abs(hand['raw_m2'] - t['raw_m2']) <= TOL_M2, True,
             'accepted_ha card=%s hand=%s excluded_ha card=%s hand=%s' % (
                 ha(t['accepted_m2']), ha(hand['accepted_m2']),
                 ha(t['excluded_m2']), ha(hand['excluded_m2'])))
    delta = hand['accepted_m2'] + hand['excluded_m2'] - hand['calc_raw_m2']
    rep.gate('%s.accepted_plus_excluded_is_calc_raw' % label,
             abs(delta) <= TOL_M2, False,
             'delta_ha=%s (provider identity, not this screen)' % ha(delta))
    view = totals_view(t)
    rep.data.setdefault('cards', {})[label] = {
        'land_uuid': land, 'totals': view,
        'confirmed': len(card['confirmed']),
        'provisional': len(card['provisional']), 'orphans': card['orphans'],
        'hand_sum_ha': {k: (ha(v) if k.endswith('m2') else v)
                        for k, v in hand.items()},
        'raw_sql_ha': ha(raw_sql)}
    rep.say('CARD %-6s land=%s confirmed=%d provisional=%d raw_ha=%s '
            'accepted_ha=%s excluded_ha=%s not_calc=%d open=%d mismatch=%d '
            'coverage=%s' % (label, land, len(card['confirmed']),
                             len(card['provisional']), view['raw_ha'],
                             view['accepted_ha'], view['excluded_ha'],
                             view['not_calculated_records'],
                             view['open_records'],
                             view['raw_mismatch_records'], view['coverage']))
    return card


# --- data: case selection ---------------------------------------------------

def pick_one(con, sql, params):
    row = con.execute(sql, params).fetchone()
    return dict(row) if row else None


def select_cases(con, rep, version, sept):
    s_start, s_end = (sql_time(x) for x in sept)
    cur = current_sql()
    cases = {}
    cases['exact_field'] = pick_one(
        con, 'SELECT a.field_land_uuid AS land, COUNT(*) AS n FROM '
        'dji_field_attributions a JOIN drone_flights f ON f.dji_flight_id = '
        'a.flight_id WHERE ' + cur + ' AND a.field_attribution_tier = ? AND '
        'a.field_land_uuid IS NOT NULL GROUP BY 1 HAVING COUNT(*) >= 2 '
        'ORDER BY n DESC, 1 LIMIT 1', (version, T1))
    cases['identified_field'] = pick_one(
        con, 'SELECT a.field_land_uuid AS land, COUNT(*) AS n FROM '
        'dji_field_attributions a JOIN drone_flights f ON f.dji_flight_id = '
        'a.flight_id WHERE ' + cur + ' AND a.field_attribution_tier = ? AND '
        'a.field_land_uuid IS NOT NULL GROUP BY 1 ORDER BY n DESC, 1 LIMIT 1',
        (version, T2))
    cases['provisional_flight'] = pick_one(
        con, 'SELECT a.flight_id AS flight, a.field_land_uuid AS land, '
        'a.field_attribution_tier AS tier FROM dji_field_attributions a JOIN '
        'drone_flights f ON f.dji_flight_id = a.flight_id WHERE ' + cur +
        ' AND a.field_attribution_tier IN (?, ?) AND a.field_land_uuid IS '
        'NOT NULL ORDER BY f.started_at DESC LIMIT 1', (version, T3, T4))
    cases['max_confirmed_field'] = pick_one(
        con, 'SELECT a.field_land_uuid AS land, COUNT(*) AS n FROM '
        'dji_field_attributions a JOIN drone_flights f ON f.dji_flight_id = '
        'a.flight_id WHERE ' + cur + ' AND a.field_attribution_tier IN (?, ?) '
        'AND a.field_land_uuid IS NOT NULL GROUP BY 1 ORDER BY n DESC, 1 '
        'LIMIT 1', (version, T1, T2))
    cases['awaiting_recalc_flight'] = pick_one(
        con, 'SELECT a.flight_id AS flight, a.field_land_uuid AS land, '
        'a.geometry_md5 AS md5 FROM dji_field_attributions a JOIN '
        'drone_flights f ON f.dji_flight_id = a.flight_id WHERE ' + cur +
        ' AND a.field_attribution_tier = ? AND '
        'COALESCE(a.historical_geometry_available, 0) = 0 AND EXISTS (SELECT '
        '1 FROM dji_land_geometries g WHERE g.content_md5 = a.geometry_md5 '
        'AND g.md5_verified = 1) ORDER BY f.started_at DESC LIMIT 1',
        (version, T2))
    cases['historical_bytes_flight'] = pick_one(
        con, 'SELECT a.flight_id AS flight, a.field_land_uuid AS land, '
        'a.geometry_md5 AS md5 FROM dji_field_attributions a JOIN '
        'drone_flights f ON f.dji_flight_id = a.flight_id WHERE ' + cur +
        ' AND a.field_attribution_tier = ? AND '
        'a.historical_geometry_available = 1 AND f.started_at >= ? AND '
        'f.started_at < ? ORDER BY f.started_at DESC LIMIT 1',
        (version, T1, s_start, s_end))
    cases['no_card_flight'] = pick_one(
        con, 'SELECT a.flight_id AS flight FROM dji_field_attributions a JOIN '
        'drone_flights f ON f.dji_flight_id = a.flight_id LEFT JOIN '
        'dji_flight_evidence e ON e.flight_id = a.flight_id WHERE ' + cur +
        " AND a.field_attribution_method IN ('AUTO_NO_KEY', 'MANUAL_NO_KEY') "
        'AND e.card_revision_id IS NULL AND f.started_at >= ? AND '
        'f.started_at < ? ORDER BY f.started_at DESC LIMIT 1',
        (version, s_start, s_end))
    cases['no_calc_flight'] = pick_one(
        con, 'SELECT f.dji_flight_id AS flight FROM drone_flights f WHERE NOT '
        'EXISTS (SELECT 1 FROM dji_area_calculations c WHERE c.flight_id = '
        'f.dji_flight_id AND c.superseded_at IS NULL AND '
        'c.area_algorithm_version = ?) AND f.started_at >= ? AND '
        'f.started_at < ? ORDER BY f.started_at DESC LIMIT 1',
        (dji_store.AREA_ALGORITHM_VERSION, s_start, s_end)) or pick_one(
        con, 'SELECT f.dji_flight_id AS flight FROM drone_flights f WHERE NOT '
        'EXISTS (SELECT 1 FROM dji_area_calculations c WHERE c.flight_id = '
        'f.dji_flight_id AND c.superseded_at IS NULL AND '
        'c.area_algorithm_version = ?) ORDER BY f.started_at DESC LIMIT 1',
        (dji_store.AREA_ALGORITHM_VERSION,))
    # Area statuses through the PROVIDER over September flights; a confirmed
    # flight is preferred so the same case is on a card and on a passport.
    sept_raw = {int(r[0]): float(r[1] or 0.0) * 10000.0 for r in con.execute(
        'SELECT dji_flight_id, area_ha FROM drone_flights WHERE started_at >= '
        '? AND started_at < ? ORDER BY started_at DESC', (s_start, s_end))}
    items = control_store.accepted_for(con, sept_raw) if sept_raw else {}
    attrs = fs.current_attributions(con, list(sept_raw))
    by_status = {}
    for fid in sept_raw:              # newest first
        item = items[fid]
        confirmed = fv.is_confirmed(attrs.get(fid))
        key = item['status']
        if key == acc.ST_ACCEPTED and (item['decision_applied']
                                       or not confirmed):
            continue
        best = by_status.get(key)
        if best is None or (confirmed and not best[1]):
            by_status[key] = (fid, confirmed)
    for name, status in (('normal_flight', acc.ST_ACCEPTED),
                         ('corrected_flight', acc.ST_CORRECTED),
                         ('review_flight', acc.ST_NEEDS_DECISION),
                         ('pending_flight', acc.ST_PENDING)):
        got = by_status.get(status)
        cases[name] = {'flight': got[0], 'confirmed': got[1]} if got else None
    # Shared md5: a boundary held by two records, flown by a flight confirmed
    # in one of them.
    cases['shared_md5'] = pick_one(
        con, 'SELECT a.flight_id AS flight, a.field_land_uuid AS land_a, '
        '(SELECT MIN(r.land_uuid) FROM dji_land_revisions r WHERE '
        'r.geometry_md5 = a.geometry_md5 AND r.land_uuid <> '
        'a.field_land_uuid) AS land_b, a.geometry_md5 AS md5 FROM '
        'dji_field_attributions a JOIN drone_flights f ON f.dji_flight_id = '
        'a.flight_id WHERE ' + cur + ' AND a.field_attribution_tier IN (?, ?) '
        'AND a.field_land_uuid IS NOT NULL AND a.geometry_md5 IN (SELECT '
        'geometry_md5 FROM dji_land_revisions WHERE geometry_md5 IS NOT NULL '
        'GROUP BY geometry_md5 HAVING COUNT(DISTINCT land_uuid) > 1) AND '
        'EXISTS (SELECT 1 FROM dji_land_revisions r WHERE r.geometry_md5 = '
        'a.geometry_md5 AND r.land_uuid <> a.field_land_uuid) '
        'ORDER BY f.started_at DESC LIMIT 1', (version, T1, T2))
    cases['reobserved_record'] = pick_one(
        con, 'SELECT land FROM (SELECT r.land_uuid AS land, MAX(r.id) AS mx, '
        '(SELECT r2.id FROM dji_land_revisions r2 WHERE r2.land_uuid = '
        'r.land_uuid ORDER BY r2.last_seen_snapshot_id DESC, r2.id DESC '
        'LIMIT 1) AS lt FROM dji_land_revisions r GROUP BY r.land_uuid) '
        'WHERE mx <> lt ORDER BY land LIMIT 1', ())
    cases['history_field'] = pick_one(
        con, 'SELECT a.field_land_uuid AS land, COUNT(DISTINCT '
        'a.geometry_md5) AS boundaries FROM dji_field_attributions a JOIN '
        'drone_flights f ON f.dji_flight_id = a.flight_id WHERE ' + cur +
        ' AND a.field_attribution_tier IN (?, ?) AND a.field_land_uuid IS '
        'NOT NULL AND a.geometry_md5 IS NOT NULL GROUP BY 1 HAVING '
        'COUNT(DISTINCT a.geometry_md5) > 1 ORDER BY boundaries DESC, 1 '
        'LIMIT 1', (version, T1, T2))
    cases['problem_field'] = pick_one(
        con, 'SELECT a.field_land_uuid AS land, COUNT(*) AS n FROM '
        'dji_field_attributions a JOIN drone_flights f ON f.dji_flight_id = '
        'a.flight_id WHERE ' + cur + ' AND a.field_attribution_tier IN (?, ?) '
        'AND a.field_land_uuid IS NOT NULL GROUP BY 1 ORDER BY n DESC, 1 '
        'LIMIT 1', (version, T3, T4))
    for name in sorted(cases):
        rep.case(name, None if cases[name] is None else
                 ' '.join('%s=%s' % (k, v) for k, v in
                          sorted(cases[name].items())))
    return cases


# --- data: proofs -----------------------------------------------------------

def prove_history(con, rep, version, land):
    rows = con.execute(
        'SELECT a.flight_id, a.geometry_md5, f.started_at FROM '
        'dji_field_attributions a JOIN drone_flights f ON f.dji_flight_id = '
        'a.flight_id WHERE ' + current_sql() + ' AND a.field_land_uuid = ? '
        'AND a.field_attribution_tier IN (?, ?) AND a.geometry_md5 IS NOT '
        'NULL ORDER BY f.started_at', (version, land, T1, T2)).fetchall()
    header = fs.land_header(con, land)
    old = rows[0]
    newer = [r for r in rows if r[1] != old[1]]
    new = newer[-1]
    attrs = fs.current_attributions(con, [old[0], new[0]])
    ok = (attrs[old[0]]['geometry_md5'] == old[1]
          and attrs[new[0]]['geometry_md5'] == new[1] and old[1] != new[1])
    rep.gate('history.each_flight_keeps_its_md5', ok, True,
             'old=%s md5=%s at=%s new=%s md5=%s at=%s latest_md5=%s' % (
                 old[0], old[1][:12], str(old[2])[:16], new[0], new[1][:12],
                 str(new[2])[:16], (header['geometry_md5'] or '')[:12]))
    return {'land': land, 'old_flight': int(old[0]), 'old_md5': old[1],
            'new_flight': int(new[0]), 'new_md5': new[1],
            'latest_md5': header['geometry_md5']}


def prove_late_bytes(con, rep, version, case):
    fid, land = int(case['flight']), case['land']
    attr = fs.current_attributions(con, [fid])[fid]
    geometry = fs.geometry_rows(con, [attr['geometry_md5']]).get(
        attr['geometry_md5'])
    view = fv.attribution_view(attr, None, 'ru', geometry=geometry)
    members = fs.confirmed_members(fs.field_flights(con, land)['rows'], land)
    in_card = fid in {int(r['flight_id']) for r in members}
    rep.gate('late_bytes.state_stays_identified',
             view['state'] == fv.STATE_IDENTIFIED, True,
             'flight=%d state=%s' % (fid, view['state']))
    rep.gate('late_bytes.still_confirmed_member', in_card, True,
             'flight=%d land=%s' % (fid, land))
    rep.gate('late_bytes.label_not_missing_and_asks_recalc',
             view['awaiting_recalculation']
             and S_NOT_SAVED not in view['state_label'], True,
             'awaiting=%s' % view['awaiting_recalculation'])


def prove_shared(con, rep, version, case):
    fid, land_a, land_b = int(case['flight']), case['land_a'], case['land_b']
    card_a, card_b = card_path(con, land_a), card_path(con, land_b)
    shared_b = fs.shared_geometry_flights(con, land_b, [case['md5']],
                                          limit=100000)
    in_shared_b = fid in {int(r['flight_id']) for r in shared_b['rows']}
    in_a, in_b = fid in card_a['confirmed'], fid in card_b['confirmed']
    rep.gate('double_count.x_in_A_not_in_B', in_a and not in_b, True,
             'flight=%d A=%s in_A=%s B=%s in_B=%s' % (fid, land_a, in_a,
                                                       land_b, in_b))
    rep.gate('double_count.x_is_diagnostic_at_B', in_shared_b, False,
             'shared_rows_at_B=%d' % shared_b['total'])
    union = set(card_a['confirmed']) | set(card_b['confirmed'])
    both = list(card_a['confirmed']) + list(card_b['confirmed'])
    items = dict(card_a['items'])
    items.update(card_b['items'])
    union_acc = hand_sum(items, union)['accepted_m2']
    sum_acc = card_a['totals']['accepted_m2'] + card_b['totals']['accepted_m2']
    rep.gate('double_count.accepted_of_x_counted_once',
             both.count(fid) == 1 and abs(union_acc - sum_acc) <= TOL_M2,
             True, 'occurrences=%d accepted_ha A+B=%s union=%s' % (
                 both.count(fid), ha(sum_acc), ha(union_acc)))
    return {'flight': fid, 'land_a': land_a, 'land_b': land_b,
            'md5': case['md5']}


def prove_latest_revision(con, rep, land):
    header = fs.land_header(con, land)
    want = latest_observed(con, land)
    by_max = max_id_revision(con, land)
    rep.gate('latest_revision.header_is_latest_observed',
             header['id'] == want['id'], True,
             'land=%s header_id=%s observed_id=%s max_id=%s' % (
                 land, header['id'], want['id'], by_max['id']))
    labels = fs.fields_of_flights(con, [land])[land]
    listing = [r for r in fs.land_list(con, land)['rows']
               if r['land_uuid'] == land]
    rep.gate('latest_revision.list_and_labels_use_it',
             labels['name'] == want['name'] and listing
             and listing[0]['name'] == want['name'], True,
             'names_differ_from_max_id=%s' % (want['name'] != by_max['name']))
    return {'land': land, 'observed_id': want['id'], 'max_id': by_max['id'],
            'observed_name': want['name'], 'max_id_name': by_max['name']}


def list_checks(con, rep, cases, sept):
    out = {}
    land = (cases.get('exact_field') or {}).get('land')
    if not land:
        return out
    header = fs.land_header(con, land)
    by_uuid = fs.land_list(con, land)
    rep.gate('list.search_exact_uuid', [r['land_uuid'] for r in
                                        by_uuid['rows']] == [land], True,
             'hits=%d' % by_uuid['total'])
    if header['serial_number']:
        hits = fs.land_list(con, header['serial_number'], page_size=200)
        rep.gate('list.search_serial', land in {r['land_uuid'] for r in
                                                hits['rows']}, True,
                 'hits=%d' % hits['total'])
    if header['name']:
        hits = fs.land_list(con, header['name'], page_size=200)
        rep.gate('list.search_name', land in {r['land_uuid'] for r in
                                              hits['rows']}, True,
                 'hits=%d' % hits['total'])
        flipped = header['name'].swapcase()
        if flipped != header['name']:
            other = fs.land_list(con, flipped, page_size=200)
            cyrillic = any('\u0400' <= ch <= '\u04ff' for ch in
                           header['name'])
            rep.gate('list.search_other_case_known_limitation',
                     None, False, 'other_case_hits=%d cyrillic_name=%s' % (
                         other['total'], cyrillic))
    full = fs.land_list(con, '', page=1)
    page2 = fs.land_list(con, '', page=2)
    rep.gate('list.pagination', full['pages'] < 2 or not (
        {r['land_uuid'] for r in full['rows']}
        & {r['land_uuid'] for r in page2['rows']}), True,
        'total=%d pages=%d page_size=%d' % (full['total'], full['pages'],
                                            full['page_size']))
    sept_list = fs.land_list(con, '', sept[0], sept[1], True, 1)
    row = [r for r in fs.land_list(con, land)['rows']][0]
    card = card_path(con, land)
    last = max((card['rows'][f]['started_at'] for f in card['confirmed']),
               default=None)
    rep.gate('list.counts_match_card', row['confirmed'] == len(
        card['confirmed']) and row['provisional'] == len(
        card['provisional']), True,
        'list confirmed=%s provisional=%s card %d/%d' % (
            row['confirmed'], row['provisional'], len(card['confirmed']),
            len(card['provisional'])))
    rep.gate('list.last_confirmed_flight', (row['last_confirmed_at'] or '')[
        :19] == (last or '')[:19], True,
        'list=%s card=%s' % (str(row['last_confirmed_at'])[:19],
                             str(last)[:19]))
    out.update({'total_records': full['total'], 'pages': full['pages'],
                'september_records_with_flights': sept_list['total']})
    rep.say('LIST total_records=%d pages=%d september_with_flights=%d' % (
        full['total'], full['pages'], sept_list['total']))
    return out


def timings(con, rep, cases, sept, runs):
    out = {}
    _r, out['list_all_time'] = timed(lambda: fs.land_list(con), runs)
    _r, out['list_september'] = timed(
        lambda: fs.land_list(con, '', sept[0], sept[1]), runs)
    _r, out['census_september'] = timed(
        lambda: fs.census(con, sept[0], sept[1]), runs)
    land = (cases.get('max_confirmed_field') or {}).get('land')
    if land:
        _r, out['card_max_confirmed_store'] = timed(
            lambda: card_path(con, land), runs)
    flight = (cases.get('normal_flight') or {}).get('flight')
    if flight:
        def passport():
            raw = {flight: 0.0}
            control_store.accepted_for(con, raw)
            fs.current_attributions(con, [flight])
            fs.evidence_for(con, [flight])
            fs.calculation_meta(con, flight)
            if control_store.tables_present(con):
                control_store.decision_chains(con, [flight])
        _r, out['passport_store'] = timed(passport, runs)
    for key, value in sorted(out.items()):
        rep.say('TIME %-26s min=%sms median=%sms max=%sms (store, %d runs)'
                % (key, value['min_ms'], value['median_ms'], value['max_ms'],
                   value['runs']))
    return out


def run_data(con, rep, sept, runs, version=None):
    version = version or FIELD_RESOLVER_VERSION
    integ = integrity(con, version)
    rep.data['integrity'] = integ
    rep.say('DATA ' + ' '.join('%s=%s' % kv for kv in sorted(integ.items())))
    rep.gate('integrity.no_flight_confirmed_in_two_records',
             integ['flights_confirmed_in_two_records'] == 0, True,
             'count=%d' % integ['flights_confirmed_in_two_records'])
    rep.gate('integrity.one_current_row_per_flight',
             integ['flights_with_two_current_rows'] == 0, False,
             'count=%d (list counts would differ from the card if > 0)'
             % integ['flights_with_two_current_rows'])
    cases = select_cases(con, rep, version, sept)
    rep.data['cases'] = cases
    seen = {}
    for label, key in (('A', 'exact_field'), ('B', 'identified_field'),
                       ('C', 'problem_field'), ('MAX', 'max_confirmed_field')):
        land = (cases.get(key) or {}).get('land')
        if not land:
            rep.gate('%s.card' % label, None, False, 'no %s on this copy' % key)
        elif land in seen:
            # The same record twice proves nothing new; it is named instead.
            rep.data.setdefault('cards', {})[label] = dict(
                rep.data['cards'][seen[land]], same_as=seen[land])
            rep.say('CARD %-6s same record as %s' % (label, seen[land]))
        else:
            reconcile(con, rep, label, land, version)
            seen[land] = label
    if cases.get('shared_md5'):
        rep.data['shared'] = prove_shared(con, rep, version,
                                          cases['shared_md5'])
    else:
        rep.gate('double_count.shared_pair', None, False,
                 'shared md5 with a confirmed flight: not on this copy')
    if cases.get('history_field'):
        rep.data['history'] = prove_history(con, rep, version,
                                            cases['history_field']['land'])
    else:
        rep.gate('history.each_flight_keeps_its_md5', None, False,
                 'not observable on this copy')
    if cases.get('awaiting_recalc_flight'):
        prove_late_bytes(con, rep, version, cases['awaiting_recalc_flight'])
    else:
        rep.gate('late_bytes', None, False, 'no TIER2 awaiting recalc')
    land = (cases.get('reobserved_record') or {}).get('land') or \
        (cases.get('exact_field') or {}).get('land')
    if land:
        rep.data['latest_revision'] = prove_latest_revision(con, rep, land)
    rep.data['list'] = list_checks(con, rep, cases, sept)
    rep.data['timings_store'] = timings(con, rep, cases, sept, runs)
    return cases


# --- pages: Flask test client on a throwaway copy ---------------------------

def refuse_page_copy(page_copy, db_path):
    norm = lambda p: os.path.normcase(os.path.abspath(p))  # noqa: E731
    if not os.path.isfile(page_copy):
        return 'page copy not found: %s' % page_copy
    if norm(page_copy) == norm(db_path):
        return 'page copy is the --db file itself'
    parts = [p.lower() for p in norm(page_copy).replace('\\', '/').split('/')]
    if any(part.startswith('transport-report') for part in parts):
        return ('page copy lies inside a transport-report folder - it must be '
                'a throwaway file, never a live database')
    return None


def import_app_on(page_copy):
    """`app` bound to the throwaway copy. Must run before anything imports app."""
    if 'app' in sys.modules:
        raise RuntimeError('app is already imported - refusing to rebind it')
    os.environ['FLASK_ENV'] = 'sqlite_prod'
    os.environ.setdefault('SECRET_KEY', secrets.token_hex(16))
    import config
    uri = 'sqlite:///' + os.path.abspath(page_copy).replace('\\', '/')
    config.SqliteProductionConfig.SQLALCHEMY_DATABASE_URI = uri
    config.DevelopmentConfig.SQLALCHEMY_DATABASE_URI = uri
    from app import app as flask_app
    if flask_app.config['SQLALCHEMY_DATABASE_URI'] != uri:
        raise RuntimeError('app is not bound to the page copy')
    return flask_app


class Pages(object):

    def __init__(self, flask_app, rep, runs):
        from models import db, User
        self.app, self.db, self.User = flask_app, db, User
        self.rep, self.runs = rep, runs
        self.client = None
        self.user_id = None
        self.pages = {}

    def login(self):
        with self.app.app_context():
            ids = [u.id for u in self.User.query.filter_by(
                role='admin').order_by(self.User.id).all()
                if u.is_active_user]
        for uid in ids:
            client = self.app.test_client()
            with client.session_transaction() as sess:
                sess['_user_id'] = str(uid)
                sess['_fresh'] = True
            if client.get('/drones/fields').status_code == 200:
                self.client, self.user_id = client, uid
                return True
        return False

    def language(self, lang):
        with self.app.app_context():
            user = self.db.session.get(self.User, self.user_id)
            user.language = lang
            self.db.session.commit()

    def get(self, key, url, lang):
        times, response = [], None
        for _ in range(self.runs):
            t0 = time.perf_counter()
            response = self.client.get(url)
            times.append((time.perf_counter() - t0) * 1000.0)
        html = response.get_data(as_text=True)
        found = leaks(html)
        lang_attr = re.search(r'<html[^>]*\blang="([a-z]+)"', html)
        entry = {'url': url, 'lang': lang, 'status': response.status_code,
                 'bytes': len(html), 'leaks': found,
                 'html_lang': lang_attr.group(1) if lang_attr else None,
                 'min_ms': round(min(times), 1),
                 'median_ms': round(statistics.median(times), 1),
                 'max_ms': round(max(times), 1)}
        self.pages['%s/%s' % (key, lang)] = entry
        self.rep.say('PAGE %-22s %s status=%d median=%sms max=%sms kb=%d '
                     'leaks=%d' % (key, lang, entry['status'],
                                   entry['median_ms'], entry['max_ms'],
                                   entry['bytes'] // 1024, len(found)))
        return html, entry


def tile_values(html):
    out = {}
    for label, value in STAT_RE.findall(html or ''):
        out[label.strip()] = number(value)
    return out


def all_confirmed_on_card(pages, land, html_first):
    """Passport ids of the confirmed table across every page of the card."""
    ids = set(passports(section(html_first, S_CONFIRMED)))
    pages_total = re.findall(r'/ (\d+)</span>', html_first)
    last = int(pages_total[-1]) if pages_total else 1
    for page in range(2, last + 1):
        resp = pages.client.get('/drones/fields/%s?date_from=&date_to=&page=%d'
                                % (land, page))
        ids |= passports(section(resp.get_data(as_text=True), S_CONFIRMED))
    return ids


def run_pages(pages, rep, cases, data, sept_args):
    cards = data.get('cards', {})
    flights = {name: (cases.get(name) or {}).get('flight') for name in (
        'normal_flight', 'corrected_flight', 'review_flight', 'pending_flight',
        'historical_bytes_flight', 'no_calc_flight', 'no_card_flight',
        'awaiting_recalc_flight', 'provisional_flight')}
    shared = data.get('shared') or {}
    history = data.get('history') or {}
    flights['shared_x'] = shared.get('flight')
    flights['history_old'] = history.get('old_flight')
    flights['history_new'] = history.get('new_flight')
    lands = {label: card['land_uuid'] for label, card in cards.items()
             if not card.get('same_as')}
    if shared:
        lands['SHARED_A'], lands['SHARED_B'] = shared['land_a'], shared['land_b']
    latest = data.get('latest_revision') or {}
    if latest:
        lands['LATEST'] = latest['land']
    exact = (cases.get('exact_field') or {}).get('land')
    lists = {'list_all': '/drones/fields?date_from=&date_to=',
             'list_sept': '/drones/fields?%s' % sept_args,
             'list_sept_flights': '/drones/fields?%s&flights=1' % sept_args,
             'list_page2': '/drones/fields?date_from=&date_to=&page=2'}
    if exact:
        lists['list_uuid'] = '/drones/fields?date_from=&date_to=&q=%s' % exact
    leaked, errors = [], []
    for lang in ('ru', 'uz'):
        pages.language(lang)
        rendered = {}
        targets = [(k, u) for k, u in sorted(lists.items())]
        targets += [('card_' + k, '/drones/fields/%s?date_from=&date_to=' % v)
                    for k, v in sorted(lands.items())]
        targets += [('pass_' + k, '/drones/flights/%d/passport' % int(v))
                    for k, v in sorted(flights.items()) if v]
        for key, url in targets:
            html, entry = pages.get(key, url, lang)
            rendered[key] = html
            if entry['status'] != 200:
                errors.append('%s/%s=%d' % (key, lang, entry['status']))
            if entry['leaks']:
                leaked.append('%s/%s:%s' % (key, lang, entry['leaks'][:2]))
            if entry['html_lang'] != lang:
                errors.append('%s/%s html_lang=%s' % (key, lang,
                                                      entry['html_lang']))
        if lang == 'ru':
            check_ru(pages, rep, rendered, cards, lands, flights, shared,
                     history, latest)
        else:
            rep.gate('uz.card_title_is_uzbek', bool(
                rendered.get('card_A')) and S_CONFIRMED_UZ in rendered.get(
                'card_A', '') and S_CONFIRMED not in rendered.get('card_A',
                                                                  ''),
                False, 'card A in Uzbek')
    rep.gate('pages.all_200_and_lang', not errors, True,
             'errors=%s' % errors[:6])
    rep.gate('pages.no_leaks', not leaked, True, 'leaks=%s' % leaked[:4])
    bogus = pages.client.get('/drones/fields/' + '0' * 36)
    anon = pages.app.test_client().get('/drones/fields')
    rep.gate('pages.unknown_uuid_404', bogus.status_code == 404, True,
             'status=%d' % bogus.status_code)
    rep.gate('pages.anonymous_redirected', anon.status_code in (302, 401),
             True, 'status=%d' % anon.status_code)


def check_ru(pages, rep, html, cards, lands, flights, shared, history,
             latest):
    card = cards.get('A')
    if card and html.get('card_A'):
        tiles = tile_values(html['card_A'])
        want = card['totals']
        ok = all(abs((tiles.get(label) or 0.0) - round(value or 0.0, 2))
                 < 0.006 for label, value in (
                     (S_RAW_TILE, want['raw_ha']),
                     (S_ACC_TILE, want['accepted_ha'] or 0.0),
                     (S_EXC_TILE, want['excluded_ha'] or 0.0)))
        rep.gate('page.card_A_tiles_equal_provider', ok, True,
                 'tiles=%s want raw=%s acc=%s exc=%s' % (
                     {k[:9]: v for k, v in tiles.items()}, want['raw_ha'],
                     want['accepted_ha'], want['excluded_ha']))
        total = re.search(S_TOTAL + r': ([\d\s\u00a0\u202f]+)</span>',
                          html['card_A'])
        shown = int(re.sub(r'\D', '', total.group(1))) if total else None
        rep.gate('page.card_A_confirmed_count', shown == card['confirmed'],
                 True, 'shown=%s want=%d' % (shown, card['confirmed']))
    if shared and html.get('card_SHARED_B'):
        x = shared['flight']
        in_conf = x in all_confirmed_on_card(pages, shared['land_b'],
                                             html['card_SHARED_B'])
        in_diag = x in passports(section(html['card_SHARED_B'], S_SHARED))
        rep.gate('page.shared_B_x_not_in_confirmed', not in_conf, True,
                 'flight=%d in_confirmed=%s in_diagnostic=%s' % (
                     x, in_conf, in_diag))
        field = section(html.get('pass_shared_x'), S_FIELD) or ''
        rep.gate('page.passport_x_counted_in_own_record_only',
                 S_COUNTED_OWN in field, False, 'flight=%d' % x)
    if latest and html.get('card_LATEST'):
        head = section(html['card_LATEST'], S_HEADER) or ''
        ok = (latest['observed_name'] or '') in head
        if latest['max_id_name'] and \
                latest['max_id_name'] != latest['observed_name']:
            ok = ok and latest['max_id_name'] not in head
        rep.gate('page.header_is_latest_observed', ok, True,
                 'observed_id=%s max_id=%s' % (latest['observed_id'],
                                               latest['max_id']))
    if flights.get('no_calc_flight') and html.get('pass_no_calc_flight'):
        accepted = row_of(html['pass_no_calc_flight'], S_ACC_TILE) or ''
        rep.gate('page.no_calc_accepted_is_empty', S_NOT_CALC in accepted
                 and number(accepted) is None, True,
                 'flight=%s' % flights['no_calc_flight'])
    if flights.get('awaiting_recalc_flight') and \
            html.get('pass_awaiting_recalc_flight'):
        field = section(html['pass_awaiting_recalc_flight'], S_FIELD) or ''
        rep.gate('page.late_bytes_no_missing_label',
                 fv.AWAITING_LABEL[0] in field and S_NOT_SAVED not in field,
                 True, 'flight=%s' % flights['awaiting_recalc_flight'])
    if history and html.get('pass_history_old'):
        old = html['pass_history_old']
        ok = history['old_md5'] in old
        if history['latest_md5'] and history['latest_md5'] != \
                history['old_md5']:
            ok = ok and history['latest_md5'] not in (section(old, S_FIELD)
                                                      or '')
        rep.gate('page.history_old_passport_keeps_old_md5', ok, True,
                 'flight=%d' % history['old_flight'])


# --- CLI -------------------------------------------------------------------

class _Parser(argparse.ArgumentParser):
    def error(self, message):
        raise UsageError(message)


def parse_args(argv):
    parser = _Parser(description=PROBE_ID)
    parser.add_argument('--db', required=True)
    parser.add_argument('--out-dir', required=True)
    parser.add_argument('--page-copy')
    parser.add_argument('--from', dest='date_from', default='2026-09-01')
    parser.add_argument('--to', dest='date_to', default='2026-09-30')
    parser.add_argument('--runs', type=int, default=3)
    args = parser.parse_args(argv)
    try:
        args.date_from = date.fromisoformat(args.date_from)
        args.date_to = date.fromisoformat(args.date_to)
    except ValueError as exc:
        raise UsageError(str(exc))
    if args.date_to < args.date_from:
        raise UsageError('--to is before --from')
    if args.runs < 1:
        raise UsageError('--runs must be >= 1')
    return args


def main(argv=None, out=None, flask_app=None):
    out = out or sys.stdout
    try:
        args = parse_args(sys.argv[1:] if argv is None else argv)
    except UsageError as exc:
        out.write('ERROR: %s\n' % ascii_safe(exc))
        return EXIT_USAGE
    if not os.path.isfile(args.db):
        out.write('ERROR: database not found: %s\n' % ascii_safe(args.db))
        return EXIT_NO_DB
    if args.page_copy and flask_app is None:
        reason = refuse_page_copy(args.page_copy, args.db)
        if reason:
            out.write('ERROR: %s\n' % ascii_safe(reason))
            return EXIT_USAGE
    con = dji_store.connect(args.db, read_only=True)
    rep = Report()
    try:
        missing = fs.missing_tables(con)
        if missing:
            out.write('ERROR: missing tables: %s\n' % ', '.join(missing))
            return EXIT_NO_TABLES
        sept = utc_bounds(args.date_from, args.date_to)
        rep.say('%s period(UTC+5)=%s..%s resolver=%s algorithm=%s' % (
            PROBE_ID, args.date_from, args.date_to, FIELD_RESOLVER_VERSION,
            dji_store.AREA_ALGORITHM_VERSION))
        cases = run_data(con, rep, sept, args.runs)
    finally:
        con.close()
    if args.page_copy:
        flask_app = flask_app or import_app_on(args.page_copy)
        pages = Pages(flask_app, rep, args.runs)
        if pages.login():
            run_pages(pages, rep, cases, rep.data, 'date_from=%s&date_to=%s'
                      % (args.date_from.isoformat(), args.date_to.isoformat()))
            rep.data['pages'] = pages.pages
        else:
            rep.gate('pages.login', False, True,
                     'no active admin could open /drones/fields on the copy')
    failed = rep.failed_blockers()
    rep.say('RESULT %s gates=%d failed_blockers=%d' % (
        'PASS' if not failed else 'FAIL', len(rep.gates), len(failed)))
    os.makedirs(args.out_dir, exist_ok=True)
    with open(os.path.join(args.out_dir, 'uat_report.json'), 'w',
              encoding='utf-8') as handle:
        json.dump({'probe': PROBE_ID, 'gates': rep.gates, 'cases': rep.cases,
                   'data': rep.data}, handle, ensure_ascii=True, indent=1,
                  sort_keys=True, default=str)
    text = '\n'.join(rep.lines) + '\n'
    with open(os.path.join(args.out_dir, 'uat_summary.txt'), 'w',
              encoding='ascii', errors='replace') as handle:
        handle.write(text)
    out.write(text)
    return EXIT_GATE if failed else EXIT_OK


if __name__ == '__main__':
    sys.exit(main())
