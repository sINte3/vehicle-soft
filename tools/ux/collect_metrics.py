#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""tools/ux/collect_metrics.py -- READ-ONLY sbor metrik s bazy dlya UX-audita.

Chto delaet: otkryvaet SQLite v rezhime "tolko chtenie" i sobiraet TRI gruppy
chisel, nuzhnyh treku UI. Nikakih zapisey, nikakih izmeneniy, nikakih
personalnyh dannyh na vyhode.

  1. CHASTOTA RABOTY -- iz tablicy audit_logs: skolko deystviy po modulyam,
     marshrutam, rolyam i tipam deystviy, s razbivkoy po mesyacam. Otvechaet
     na vopros "kakie ekrany realno v ezhednevnoy rabote", kotoryy iz koda
     ne vyvoditsya.
     VAZHNO: audit_logs pishetsya na biznes-deystviyah (log_audit), a ne na
     kazhdom prosmotre stranicy. Eto chastota DEYSTVIY, a ne prosmotrov, i
     v otchete ona tak i nazvana. Nizhnyaya ocenka aktivnosti, ne verhnyaya.

  2. OBEM -- chislo strok v kazhdoy tablice. Otvechaet na vopros "skolko
     strok realno v tablice na ekrane".

  3. DLINY STROK -- po tekstovym kolonkam: min / sredneye / p90 / p99 / max
     chisla SIMVOLOV. Otvechaet na vopros "kakoy shiriny dolzhna byt kolonka,
     chtoby kirillica i uzbekskiye podpisi ne perenosilis".

CHEGO NA VYHODE NET, po postroeniyu:
  - ni odnogo znacheniya iz tekstovyh poley (tolko ih dlina);
  - kolonki s priznakami personalnyh dannyh propuskayutsya celikom
    (see PII_COLUMN_MARKERS): imena, loginy, heshi, tokeny, telefony,
    pochta, IP, user-agent, telegram-id;
  - slovar znacheniy sobiraetsya TOLKO dlya sluzhebnyh kolonok iz belogo
    spiska (status / type / role / unit i t.p.) i tolko kogda razlichnyh
    znacheniy ne bolshe 25. Eto nuzhno, chtoby narisovat badge dlya kazhdogo
    statusa, i ne soderzhit personalnyh dannyh.

BEZOPASNOST:
  - baza otkryvaetsya cherez URI file:...?mode=ro -- zapis fizicheski
    nevozmozhna, dazhe oshibochnaya;
  - stdlib sqlite3, BEZ Flask. Import app vyzval by create_all() na importe
    i sdelal by diagnostiku pisatelem (pravilo proekta);
  - nichego ne udalyaetsya i ne sozdaetsya, krome fayla otcheta;
  - vyvod v konsol tolko ASCII (NSSM/Windows cp1251), otchet -- UTF-8 JSON.

KAK ZAPUSKAT (PowerShell, po odnoy komande na stroku).

  Predpochtitelno -- na svezhey KOPII bazy, sdelannoy shtatnym bekapom:

    cd C:\\transport-report
    .\\backup_production_db.bat
    & "C:\\Program Files\\Python314\\python.exe" tools\\ux\\collect_metrics.py --db instance\\backups\\transport.db --out ux_metrics.json

  Dopustimo -- pryamo po zhivoy baze, tolko na chtenie:

    & "C:\\Program Files\\Python314\\python.exe" tools\\ux\\collect_metrics.py --db instance\\transport.db --out ux_metrics.json

Kody vyhoda: 0 -- otchet zapisan; 1 -- baza est, no chitat nechego;
2 -- fayl bazy ne nayden ili ne otkryvaetsya.
"""

import argparse
import json
import os
import sqlite3
import sys

# Kolonki, kotorye ne trogaem voobshche: dazhe dlina ih znacheniy ne nuzhna
# treku UI nastolko, chtoby riskovat.
PII_COLUMN_MARKERS = (
    'password', 'hash', 'token', 'secret', 'salt',
    'username', 'full_name', 'first_name', 'last_name', 'fio',
    'phone', 'email', 'mail', 'passport', 'inn',
    'ip_address', 'user_agent', 'telegram', 'tg_',
    'before_json', 'after_json', 'changes_json',
)

# Sluzhebnye kolonki, dlya kotoryh slovar znacheniy POLEZEN i bezopasen:
# iz nego stroitsya nabor statusnyh badge.
ENUM_COLUMN_NAMES = (
    'status', 'state', 'type', 'kind', 'category', 'role', 'role_snapshot',
    'unit', 'unit_code', 'currency', 'lang', 'language', 'method', 'action',
    'module', 'direction', 'source', 'reason', 'fuel_type', 'movement_type',
)
ENUM_MAX_DISTINCT = 25

# Tekstovye kolonki dlinnee etogo pochti navernyaka ne kolonka tablicy, a
# telo zametki -- statistika dliny po nim UI ne pomogaet.
TEXT_STAT_MAX_AVG = 400

# Zashchita ot dolgogo prohoda po ogromnoy tablice.
BIG_TABLE_ROWS = 400000


def _ascii(value):
    """Vernut stroku, bezopasnuyu dlya cp1251-konsoli Windows."""
    return str(value).encode('ascii', 'backslashreplace').decode('ascii')


def open_readonly(path):
    """Otkryt bazu strogo na chtenie. Lyubaya popytka zapisi -- oshibka SQLite."""
    if not os.path.isfile(path):
        print('ERROR: database file not found: ' + _ascii(path))
        sys.exit(2)
    uri = 'file:' + path.replace('?', '%3f').replace('#', '%23') + '?mode=ro'
    try:
        conn = sqlite3.connect(uri, uri=True, timeout=5)
    except sqlite3.Error as exc:
        # [REASON]: baza v rezhime WAL, otkrytaya tolko na chtenie, trebuet
        # chitaemyh -wal/-shm ryadom. Esli sluzhba derzhit ih inache, chestnee
        # skazat "voz'mite kopiyu", chem molcha vernut nepolnye chisla.
        print('ERROR: cannot open database read-only: ' + _ascii(exc))
        print('HINT: run backup_production_db.bat first and point --db at the copy.')
        sys.exit(2)
    conn.row_factory = sqlite3.Row
    return conn


def list_tables(conn):
    rows = conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' "
        "AND name NOT LIKE 'sqlite_%' ORDER BY name"
    ).fetchall()
    return [r['name'] for r in rows]


def table_columns(conn, table):
    return [(r['name'], (r['type'] or '').upper())
            for r in conn.execute('PRAGMA table_info("%s")' % table).fetchall()]


def is_pii(column):
    low = column.lower()
    return any(marker in low for marker in PII_COLUMN_MARKERS)


def scalar(conn, sql, params=()):
    try:
        row = conn.execute(sql, params).fetchone()
    except sqlite3.Error:
        return None
    return row[0] if row else None


def collect_volumes(conn, tables):
    """Chislo strok v kazhdoy tablice."""
    out = {}
    for table in tables:
        count = scalar(conn, 'SELECT COUNT(*) FROM "%s"' % table)
        if count is not None:
            out[table] = count
    return out


def collect_text_lengths(conn, tables, volumes):
    """Statistika DLINY tekstovyh kolonok. Znacheniya ne chitayutsya."""
    out = {}
    for table in tables:
        rows = volumes.get(table, 0)
        if not rows:
            continue
        if rows > BIG_TABLE_ROWS:
            out.setdefault(table, {})['_note'] = 'skipped: table too large'
            continue
        for column, ctype in table_columns(conn, table):
            if is_pii(column):
                continue
            if not ('CHAR' in ctype or 'TEXT' in ctype or 'CLOB' in ctype or ctype == ''):
                continue
            base = ('SELECT COUNT(*), MIN(LENGTH("%s")), MAX(LENGTH("%s")), '
                    'AVG(LENGTH("%s")) FROM "%s" WHERE "%s" IS NOT NULL '
                    "AND \"%s\" <> ''" % (column, column, column, table, column, column))
            try:
                filled, lo, hi, avg = conn.execute(base).fetchone()
            except sqlite3.Error:
                continue
            if not filled:
                continue
            if avg and avg > TEXT_STAT_MAX_AVG:
                continue
            stat = {
                'filled': filled,
                'min': lo,
                'max': hi,
                'avg': round(avg, 1) if avg is not None else None,
            }
            for label, pct in (('p90', 90), ('p95', 95), ('p99', 99)):
                offset = max(0, int(filled * pct / 100) - 1)
                stat[label] = scalar(
                    conn,
                    'SELECT LENGTH("%s") FROM "%s" WHERE "%s" IS NOT NULL '
                    "AND \"%s\" <> '' ORDER BY LENGTH(\"%s\") LIMIT 1 OFFSET ?"
                    % (column, table, column, column, column),
                    (offset,))
            out.setdefault(table, {})[column] = stat
    return out


def collect_enums(conn, tables):
    """Slovar znacheniy sluzhebnyh kolonok -- iz nego stroyatsya badge."""
    out = {}
    for table in tables:
        for column, _ctype in table_columns(conn, table):
            if is_pii(column) or column.lower() not in ENUM_COLUMN_NAMES:
                continue
            distinct = scalar(conn, 'SELECT COUNT(DISTINCT "%s") FROM "%s"' % (column, table))
            if not distinct or distinct > ENUM_MAX_DISTINCT:
                continue
            try:
                rows = conn.execute(
                    'SELECT "%s" AS v, COUNT(*) AS n FROM "%s" '
                    'WHERE "%s" IS NOT NULL GROUP BY 1 ORDER BY n DESC'
                    % (column, table, column)).fetchall()
            except sqlite3.Error:
                continue
            out.setdefault(table, {})[column] = {
                str(r['v']): r['n'] for r in rows}
    return out


def collect_activity(conn, tables):
    """Chastota DEYSTVIY iz audit_logs: modul, marshrut, rol, mesyac."""
    if 'audit_logs' not in tables:
        return {'_note': 'audit_logs table absent in this database'}

    present = {c for c, _t in table_columns(conn, 'audit_logs')}
    result = {'_note': 'business actions written by log_audit(), NOT page views'}

    total = scalar(conn, 'SELECT COUNT(*) FROM audit_logs')
    result['total_rows'] = total
    if not total:
        return result

    if 'created_at' in present:
        result['period'] = {
            'first': scalar(conn, 'SELECT MIN(created_at) FROM audit_logs'),
            'last': scalar(conn, 'SELECT MAX(created_at) FROM audit_logs'),
        }
        try:
            result['by_month'] = {
                str(r['m']): r['n'] for r in conn.execute(
                    'SELECT substr(created_at,1,7) AS m, COUNT(*) AS n '
                    'FROM audit_logs GROUP BY 1 ORDER BY 1').fetchall()}
        except sqlite3.Error:
            pass

    def group(column, limit=None):
        if column not in present:
            return None
        sql = ('SELECT "%s" AS v, COUNT(*) AS n FROM audit_logs '
               'WHERE "%s" IS NOT NULL GROUP BY 1 ORDER BY n DESC' % (column, column))
        if limit:
            sql += ' LIMIT %d' % limit
        try:
            return {str(r['v']): r['n'] for r in conn.execute(sql).fetchall()}
        except sqlite3.Error:
            return None

    for key, column, limit in (
            ('by_module', 'module', None),
            ('by_route', 'route', 120),
            ('by_role', 'role_snapshot', None),
            ('by_action', 'action', 60),
            ('by_method', 'method', None),
            ('by_entity_type', 'entity_type', 60)):
        value = group(column, limit)
        if value is not None:
            result[key] = value

    # Skolko razlichnyh lyudey rabotalo -- chislom, bez imen.
    if 'user_id' in present:
        result['distinct_users'] = scalar(
            conn, 'SELECT COUNT(DISTINCT user_id) FROM audit_logs '
                  'WHERE user_id IS NOT NULL')

    # Aktivnost po rolyam v razreze mesyacev -- kto realno rabotaet kazhdyy den.
    if 'created_at' in present and 'role_snapshot' in present:
        try:
            result['role_by_month'] = [
                {'month': str(r['m']), 'role': str(r['role']), 'n': r['n']}
                for r in conn.execute(
                    'SELECT substr(created_at,1,7) AS m, role_snapshot AS role, '
                    'COUNT(*) AS n FROM audit_logs GROUP BY 1,2 ORDER BY 1,3 DESC'
                ).fetchall()]
        except sqlite3.Error:
            pass

    return result


def main():
    parser = argparse.ArgumentParser(
        description='READ-ONLY UX metrics collector (no data values in output).')
    parser.add_argument('--db', required=True, help='path to the SQLite file')
    parser.add_argument('--out', default='ux_metrics.json', help='report file')
    args = parser.parse_args()

    conn = open_readonly(os.path.abspath(args.db))
    try:
        tables = list_tables(conn)
        if not tables:
            print('ERROR: no tables in database')
            return 1

        print('tables: %d' % len(tables))
        volumes = collect_volumes(conn, tables)
        print('volumes collected')
        activity = collect_activity(conn, tables)
        print('activity collected')
        enums = collect_enums(conn, tables)
        print('enums collected')
        lengths = collect_text_lengths(conn, tables, volumes)
        print('text lengths collected')

        report = {
            'schema_version': 1,
            'source_basename': os.path.basename(args.db),
            'tables': len(tables),
            'volumes': volumes,
            'activity': activity,
            'enums': enums,
            'text_lengths': lengths,
            'excluded_columns_rule': list(PII_COLUMN_MARKERS),
        }
    finally:
        conn.close()

    with open(args.out, 'w', encoding='utf-8') as handle:
        json.dump(report, handle, ensure_ascii=False, indent=1, sort_keys=True)

    size_kb = os.path.getsize(args.out) / 1024.0
    print('written: %s (%.1f KB)' % (_ascii(args.out), size_kb))
    print('no field values are included, only counts and string lengths')
    return 0


if __name__ == '__main__':
    sys.exit(main())
