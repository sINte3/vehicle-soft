# -*- coding: utf-8 -*-
"""Migration DRONES_HARDWARE_ID_FIX_001 -- serials of machines 2 and 9 swapped.

Changes TWO cells: drone_units.hardware_id of machine number 2 and of machine
number 9. No table, column or index is created or dropped; no flight row is
touched; not one hectare moves.

WHAT IS WRONG. The park cards carry the airframes the other way round:

    machine  hardware_id in our DB   the airframe that really is this machine
    No 2     64TBL8F002006B         64TBL8F002007V   (DJI device "2 Klaster")
    No 9     64TBL8F002007V         64TBL8F002006B   (DJI device "9 Garden")

Three independent sources agree against our database, checked 2026-08-13:
  1. DJI Device Management: "2 Klaster" = 64TBL8F002007V, activated
     2024/09/12; "9 Garden" = 64TBL8F002006B, activated 2025/09/04.
  2. The djiag.com flight export: every row named "9 Garden" carries
     Body Code 64TBL8F002006B, and the single "2 Klaster" row carries
     64TBL8F002007V.
  3. The owner's own reference sheet "Spisok operatorov.xlsx", sheet
     "Svod1": Dron No 2 Id 64TBL8F002007V, Dron No 9 Id 64TBL8F002006B.
Only the sheet "10.07" of that same file agrees with our database, and it is
the sheet the cards were most likely typed from.

WHY IT MATTERS NOW AND NOT BEFORE. Today hardware_id is descriptive: flights
are attributed by the DJI nickname, so the hectares of No 2 and No 9 are
correct despite the wrong serials. The moment attribution moves to the
airframe -- which is the fix for the September/October/March mis-attribution
-- hardware_id becomes a KEY. Running that with these two cells swapped would
put every "9 Garden" flight on machine No 2 and every "2 Klaster" flight on
machine No 9: confidently, silently and wrongly. Hence this migration is a
PRECONDITION of the body-code work, not a cosmetic cleanup.

The generator ids are NOT touched. Generator 6QKZP2S0320190 belongs with
machine No 9 in "Svod1" as well, and machine No 2 has no generator in either
source: only the airframe column is wrong.

Safe / idempotent (same contract as migrate_drones_reattach_001.py):
  - refuses to run and exits with code 2 when instance/transport.db is
    absent (sqlite3.connect would otherwise CREATE an empty database);
  - PRECONDITION: machines 2 and 9 exist and hold exactly the swapped pair.
    Cells already correct -> nothing is written to drone_units, the registry
    row is still recorded and the script says so. Any third state (an
    unexpected serial, a missing machine) -> exit code 1, full rollback,
    registry untouched: a hand-edited value must be looked at by a human,
    never overwritten by a script;
  - registered through migration_utils, skips itself on a re-run and prints
    'Already applied. Nothing to do.';
  - single transaction; postconditions verified BEFORE recording;
  - no Flask app context, stdlib sqlite3 only (never `from app import app`:
    create_app() calls db.create_all() at import time and turns a reader
    into a writer);
  - console output is ASCII only (NSSM/Windows log encoding).

Run (service must be STOPPED first to avoid SQLite write conflicts):

  cd C:\\transport-report
  .\\nssm.exe stop TransportReport
  copy instance\\transport.db instance\\transport.db.backup_20260813
  "C:\\Program Files\\Python314\\python.exe" migrate_drones_hardware_id_fix_001.py
  .\\nssm.exe start TransportReport

Rollback:
  Code rollback and data rollback are separate, and in that order. Reverting
  the commit leaves the corrected serials in place -- which is harmless while
  hardware_id is still descriptive, and correct in any case. To put the two
  cells back exactly as they were (only ever needed if the owner shows the
  DJI console itself is wrong):

    UPDATE drone_units SET hardware_id = '64TBL8F002006B' WHERE number = 2;
    UPDATE drone_units SET hardware_id = '64TBL8F002007V' WHERE number = 9;
    DELETE FROM schema_migrations WHERE name = 'DRONES_HARDWARE_ID_FIX_001';
"""

import os
import sqlite3
import sys

from migration_utils import (ensure_schema_migrations_table,
                             is_migration_applied, migration_checksum,
                             record_migration)

MIGRATION_ID = 'DRONES_HARDWARE_ID_FIX_001'

DESCRIPTION = ('DRONE-BODYCODE-001: hardware_id of machines 2 and 9 were '
               'swapped; set 2=64TBL8F002007V, 9=64TBL8F002006B per DJI '
               'Device Management, the djiag.com export and sheet Svod1. '
               'No flight row touched.')

DB_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                       'instance', 'transport.db')

# Machine number -> (serial that is wrong today, serial it must become).
# Spelled out per machine instead of "swap whatever is there": a swap would
# happily exchange two values that were never the ones this migration is
# about, and would then report success.
FIX = {
    2: ('64TBL8F002006B', '64TBL8F002007V'),
    9: ('64TBL8F002007V', '64TBL8F002006B'),
}


def _table_exists(cur, name):
    cur.execute("SELECT name FROM sqlite_master WHERE type='table' AND name=?",
                (name,))
    return cur.fetchone() is not None


def _column_names(cur, table):
    cur.execute('PRAGMA table_info(%s)' % table)
    return [row[1] for row in cur.fetchall()]


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

    already_correct = False
    con = sqlite3.connect(DB_PATH)
    try:
        cur = con.cursor()
        cur.execute('BEGIN')

        if not _table_exists(cur, 'drone_units'):
            raise RuntimeError('precondition failed: table drone_units does '
                               'not exist -- run '
                               'migrate_drones_foundation_001.py first')
        if 'hardware_id' not in _column_names(cur, 'drone_units'):
            raise RuntimeError('precondition failed: drone_units has no '
                               'hardware_id column -- run the DRONE-FLEET-001 '
                               'migration first')

        current = {}
        for number in sorted(FIX):
            found = cur.execute(
                'SELECT id, hardware_id FROM drone_units WHERE number = ?',
                (number,)).fetchall()
            if len(found) != 1:
                raise RuntimeError(
                    'precondition failed: expected exactly one drone_units '
                    'row with number=%d, found %d' % (number, len(found)))
            current[number] = (found[0][1] or '').strip()

        if all(current[n] == FIX[n][1] for n in FIX):
            # [REASON]: cells that are already right are not an error and must
            # not be rewritten -- there is simply nothing to change. The
            # registry row is still written, so the next run answers
            # 'Already applied' instead of re-reading the park every time.
            already_correct = True
        else:
            mismatched = [n for n in sorted(FIX) if current[n] != FIX[n][0]]
            if mismatched:
                for number in sorted(FIX):
                    print('  unit %d: hardware_id = %r, expected %r before '
                          'the fix' % (number, current[number], FIX[number][0]))
                raise RuntimeError(
                    'precondition failed: unexpected serial(s) on machine(s) '
                    '%s -- refusing to overwrite a hand-edited value'
                    % ', '.join(str(n) for n in mismatched))

            for number in sorted(FIX):
                cur.execute(
                    'UPDATE drone_units SET hardware_id = ? WHERE number = ?',
                    (FIX[number][1], number))

        # Postconditions BEFORE recording: both cells hold the target value,
        # and each serial sits on exactly one machine -- the check that would
        # catch an update that moved one cell and not the other.
        for number in sorted(FIX):
            got = cur.execute(
                'SELECT hardware_id FROM drone_units WHERE number = ?',
                (number,)).fetchone()[0]
            if (got or '').strip() != FIX[number][1]:
                raise RuntimeError(
                    'postcondition failed for machine %d: hardware_id is %r, '
                    'expected %r' % (number, got, FIX[number][1]))
        for number in sorted(FIX):
            holders = cur.execute(
                'SELECT COUNT(*) FROM drone_units '
                'WHERE UPPER(TRIM(COALESCE(hardware_id, ""))) = ?',
                (FIX[number][1].upper(),)).fetchone()[0]
            if holders != 1:
                raise RuntimeError(
                    'postcondition failed: serial %s is on %d machines, '
                    'expected exactly 1' % (FIX[number][1], holders))

        con.commit()
    except Exception as exc:
        con.rollback()
        print('ERROR: migration failed and was rolled back: %s' % exc)
        sys.exit(1)
    finally:
        con.close()

    record_migration(MIGRATION_ID, description=DESCRIPTION,
                     checksum=migration_checksum(__file__))
    if already_correct:
        print('Already correct. Nothing to change; migration recorded.')
    else:
        print('Done. hardware_id fixed on 2 machines.')
        print('  unit 2: %s -> %s' % (FIX[2][0], FIX[2][1]))
        print('  unit 9: %s -> %s' % (FIX[9][0], FIX[9][1]))
    print('Flights were NOT touched: attribution still runs on the nickname, '
          'no hectares moved.')


if __name__ == '__main__':
    run()
