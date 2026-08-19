# -*- coding: utf-8 -*-
"""Migration DRONES_REATTACH_BODYCODE_001 -- October 2025 and March 2026:
five nickname spellings sat on the wrong machine.

Moves flights between machines. No table, column or index is created or
dropped; no flight is added or deleted; not one hectare appears or vanishes.
Only drone_flights.drone_unit_id changes, on exactly 392 rows.

WHAT IS WRONG. Attribution runs on the DJI nickname, and drone_nicknames maps
one spelling to one machine forever. But DJI labels migrated between
airframes, so in some months a spelling pointed at the wrong machine:

    month     spelling      flights  hectares   was   must be
    2025-10   12 Peshku         185    201.98   12    13
    2025-10   14 Servis          76     87.96   14    12
    2025-10   13 Servis          67     47.92   13    15
    2026-03   12 Peshku          37     42.17   12    13
    2026-03   13 Servis          27     23.49   13    15

HOW THIS WAS DERIVED, AND WHY IT IS NOT A GUESS. djiag.com reports hectares
and flight counts PER AIRFRAME (the device is identified by its serial, which
renaming does not touch); the owner supplied those figures for every month
from September 2025 to August 2026. Our side was grouped per spelling from
drone_flights. An exhaustive search over every possible assignment of
spellings to airframes was then run for each month, accepting only
assignments where BOTH numbers match for EVERY airframe -- flight count
exactly, hectares within 0.05. For October 2025 and March 2026 exactly ONE
assignment exists, and it is the table above. The same search over April, May,
June and July 2026 also returns exactly one assignment: the one already in the
database, which is why those months are not touched.

September 2025 is deliberately NOT in this migration. There, no assignment of
whole spellings reproduces the DJI per-airframe totals, which means a spelling
was carried by two different airframes inside that month (the fleet was
renamed on 27-29 September). Resolving it needs per-airframe per-DAY figures
from DJI; guessing would be inventing a business rule, which the charter
forbids.

WHY A MIGRATION AND NOT THE EXISTING RE-ATTRIBUTION SCREEN. That path
(DroneReattachRun) only ever fills drone_unit_id where it is NULL, and its
ledger documents that invariant; the verification scripts rely on it. This
correction MOVES rows that already have a machine, so it belongs in a
versioned, registered, one-time script with its own rollback -- not in a
ledger whose meaning would have to be widened.

Safe / idempotent (same contract as migrate_drones_reattach_001.py):
  - refuses to run and exits with code 2 when instance/transport.db is
    absent (sqlite3.connect would otherwise CREATE an empty database);
  - PRECONDITION per row of the table above: the flights currently sitting on
    the OLD machine under that spelling in that month must number exactly as
    listed, with the listed hectares (tolerance 0.05). One row off -> exit 1,
    full rollback, registry untouched. A database that has already been
    hand-edited must be looked at by a human, not overwritten;
  - POSTCONDITION: after the move, every machine's month total equals the
    figure djiag.com reports for that airframe. Those figures are constants
    below, so the check is against DJI, not against our own arithmetic;
  - registered through migration_utils, skips itself on a re-run and prints
    'Already applied. Nothing to do.';
  - single transaction; postconditions verified BEFORE recording;
  - no Flask app context, stdlib sqlite3 only (never `from app import app`:
    create_app() calls db.create_all() at import time and turns a reader
    into a writer);
  - console output is ASCII only (NSSM/Windows log encoding).

[REASON]: the month of a flight is computed as UTC+5, the same way
_drone_flight_month_expr() does it in drones.py. THESE DRONES SPRAY AT NIGHT:
a flight stored at 2026-03-31 20:30 UTC happened at 01:30 on 1 April for the
people who flew it. Grouping by UTC would move such a flight into the wrong
month, and this migration would then update a row it must not touch.

Run (service must be STOPPED first to avoid SQLite write conflicts):

  cd C:\\transport-report
  .\\nssm.exe stop TransportReport
  copy instance\\transport.db instance\\transport.db.backup_20260813
  "C:\\Program Files\\Python314\\python.exe" migrate_drones_reattach_bodycode_001.py
  .\\nssm.exe start TransportReport

Rollback:
  Code rollback and data rollback are separate, and in that order. Reverting
  the commit leaves the corrected attribution in place, which is the correct
  state. To put the flights back exactly where they were, run these five
  statements -- they are the table above, reversed, and they select the same
  rows by spelling and month, not by id:

    UPDATE drone_flights SET drone_unit_id = (SELECT id FROM drone_units WHERE number = 12)
     WHERE nickname_raw = '12 Peshku'
       AND strftime('%Y-%m', datetime(started_at, '+300 minutes')) = '2025-10';
    UPDATE drone_flights SET drone_unit_id = (SELECT id FROM drone_units WHERE number = 14)
     WHERE nickname_raw = '14 Servis'
       AND strftime('%Y-%m', datetime(started_at, '+300 minutes')) = '2025-10';
    UPDATE drone_flights SET drone_unit_id = (SELECT id FROM drone_units WHERE number = 13)
     WHERE nickname_raw = '13 Servis'
       AND strftime('%Y-%m', datetime(started_at, '+300 minutes')) = '2025-10';
    UPDATE drone_flights SET drone_unit_id = (SELECT id FROM drone_units WHERE number = 12)
     WHERE nickname_raw = '12 Peshku'
       AND strftime('%Y-%m', datetime(started_at, '+300 minutes')) = '2026-03';
    UPDATE drone_flights SET drone_unit_id = (SELECT id FROM drone_units WHERE number = 13)
     WHERE nickname_raw = '13 Servis'
       AND strftime('%Y-%m', datetime(started_at, '+300 minutes')) = '2026-03';
    DELETE FROM schema_migrations WHERE name = 'DRONES_REATTACH_BODYCODE_001';
"""

import os
import sqlite3
import sys

from migration_utils import (ensure_schema_migrations_table,
                             is_migration_applied, migration_checksum,
                             record_migration)

MIGRATION_ID = 'DRONES_REATTACH_BODYCODE_001'

DESCRIPTION = ('DRONE-BODYCODE-001: five spellings moved to the airframe that '
               'actually flew them in 2025-10 and 2026-03 (392 flights, '
               '403.52 ha). Derived from the djiag.com per-airframe figures; '
               'the assignment is the only one that reproduces them.')

DB_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                       'instance', 'transport.db')

# Смещение показа: месяц вылета считается в UTC+5, как в drones.py.
DISPLAY_TZ_MINUTES = 300

# (месяц, написание ника, машина СЕЙЧАС, машина ДОЛЖНА, вылетов, гектаров)
MOVES = (
    ('2025-10', '12 Peshku', 12, 13, 185, 201.98),
    ('2025-10', '14 Servis', 14, 12, 76, 87.96),
    ('2025-10', '13 Servis', 13, 15, 67, 47.92),
    ('2026-03', '12 Peshku', 12, 13, 37, 42.17),
    ('2026-03', '13 Servis', 13, 15, 27, 23.49),
)

# Что djiag.com показывает по каждому борту за эти месяцы: (вылетов, гектаров).
# Постусловие сверяется С ЭТИМИ ЧИСЛАМИ, то есть с DJI, а не с собственной
# арифметикой миграции.
DJI_AFTER = {
    '2025-10': {12: (76, 87.96), 13: (185, 201.98), 14: (0, 0.0),
                15: (67, 47.92)},
    '2026-03': {12: (101, 99.64), 13: (296, 297.44), 15: (158, 139.54)},
}

HA_TOL = 0.05

MONTH_EXPR = ("strftime('%%Y-%%m', datetime(started_at, '+%d minutes'))"
              % DISPLAY_TZ_MINUTES)


def _table_exists(cur, name):
    cur.execute("SELECT name FROM sqlite_master WHERE type='table' AND name=?",
                (name,))
    return cur.fetchone() is not None


def _unit_id(cur, number):
    row = cur.execute('SELECT id FROM drone_units WHERE number = ?',
                      (number,)).fetchone()
    if row is None:
        raise RuntimeError('precondition failed: no drone_units row with '
                           'number=%d' % number)
    return row[0]


def _stats(cur, month, nickname, unit_id):
    """Сколько вылетов и гектаров у написания в месяце на данной машине."""
    sql = ('SELECT COUNT(*), COALESCE(ROUND(SUM(area_ha), 4), 0) '
           'FROM drone_flights '
           'WHERE nickname_raw = ? AND drone_unit_id IS ? AND %s = ?'
           % MONTH_EXPR)
    return cur.execute(sql, (nickname, unit_id, month)).fetchone()


def _unit_month_totals(cur, month, unit_id):
    sql = ('SELECT COUNT(*), COALESCE(ROUND(SUM(area_ha), 4), 0) '
           'FROM drone_flights WHERE drone_unit_id IS ? AND %s = ?'
           % MONTH_EXPR)
    return cur.execute(sql, (unit_id, month)).fetchone()


def run():
    if not os.path.exists(DB_PATH):
        # [REASON]: sqlite3.connect(path) would CREATE an empty database when
        # the file is missing -- never do that in a migration.
        print('ERROR: database not found at %s - refusing to run.' % DB_PATH)
        sys.exit(2)

    ensure_schema_migrations_table()
    if is_migration_applied(MIGRATION_ID):
        print('Already applied. Nothing to do.')
        return

    con = sqlite3.connect(DB_PATH)
    try:
        cur = con.cursor()
        cur.execute('BEGIN')

        for table in ('drone_units', 'drone_flights'):
            if not _table_exists(cur, table):
                raise RuntimeError('precondition failed: table %s does not '
                                   'exist' % table)

        numbers = set()
        for _m, _n, old, new, _f, _h in MOVES:
            numbers.add(old)
            numbers.add(new)
        for month in DJI_AFTER:
            numbers.update(DJI_AFTER[month])
        ids = dict((number, _unit_id(cur, number)) for number in sorted(numbers))

        # Precondition: every move finds exactly the rows it expects.
        for month, nickname, old, new, want_flights, want_ha in MOVES:
            got_flights, got_ha = _stats(cur, month, nickname, ids[old])
            if got_flights != want_flights or abs(got_ha - want_ha) > HA_TOL:
                raise RuntimeError(
                    'precondition failed for %s / %s on machine %d: found '
                    '%d flights / %.2f ha, expected %d / %.2f'
                    % (month, nickname, old, got_flights, got_ha,
                       want_flights, want_ha))

        moved_rows = 0
        for month, nickname, old, new, _f, _h in MOVES:
            sql = ('UPDATE drone_flights SET drone_unit_id = ? '
                   'WHERE nickname_raw = ? AND drone_unit_id IS ? AND %s = ?'
                   % MONTH_EXPR)
            cur.execute(sql, (ids[new], nickname, ids[old], month))
            moved_rows += cur.rowcount

        # Postcondition BEFORE recording: every machine touched here now shows
        # exactly what djiag.com shows for that airframe in that month.
        for month in sorted(DJI_AFTER):
            for number in sorted(DJI_AFTER[month]):
                want_flights, want_ha = DJI_AFTER[month][number]
                got_flights, got_ha = _unit_month_totals(cur, month,
                                                         ids[number])
                if got_flights != want_flights or abs(got_ha - want_ha) > HA_TOL:
                    raise RuntimeError(
                        'postcondition failed: machine %d in %s has %d '
                        'flights / %.2f ha, djiag.com reports %d / %.2f'
                        % (number, month, got_flights, got_ha, want_flights,
                           want_ha))

        expected_rows = sum(row[4] for row in MOVES)
        if moved_rows != expected_rows:
            raise RuntimeError('postcondition failed: %d rows updated, '
                               'expected %d' % (moved_rows, expected_rows))

        con.commit()
    except Exception as exc:
        con.rollback()
        print('ERROR: migration failed and was rolled back: %s' % exc)
        sys.exit(1)
    finally:
        con.close()

    record_migration(MIGRATION_ID, description=DESCRIPTION,
                     checksum=migration_checksum(__file__))
    print('Done. %d flights moved to the airframe that flew them.'
          % sum(row[4] for row in MOVES))
    for month, nickname, old, new, flights, hectares in MOVES:
        print('  %s %-10s %4d flights %8.2f ha : unit %d -> unit %d'
              % (month, nickname, flights, hectares, old, new))
    print('Every machine month total now equals the djiag.com figure for '
          'that airframe.')
    print('September 2025 is NOT fixed here: per-airframe DAILY figures are '
          'still missing.')


if __name__ == '__main__':
    run()
