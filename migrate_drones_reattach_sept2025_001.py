# -*- coding: utf-8 -*-
"""Migration DRONES_REATTACH_SEPT2025_001 -- September 2025 gets its machines.

Fills drone_unit_id on the 5 304 September flights that had none, and moves
the 128 that sat on the wrong machine. 5 661 flights in total, every one of
them attributed to the airframe that actually flew it.

WHERE THE ANSWER COMES FROM, AND WHY IT IS NOT A GUESS. September could not be
solved by matching monthly sums -- an exhaustive search proved no assignment of
whole spellings to airframes reproduces the DJI per-airframe totals, because
the fleet was renamed on 27-29 September and one spelling was carried by two
different airframes. So it was READ instead: on 2026-08-14 the owner ran
`python -m drone_collector.devices --from 2025-09-01 --to 2025-09-30`, which
sets the cabinet's own Device filter on each airframe in turn and walks its
pages. All fifteen devices came back complete, 5 661 flights, no flight under
two devices, and the count of every single device matched what djiag.com
reports for it. The rules below are that reading, grouped by spelling.

THE FLEET WAS RENAMED ON 8 SEPTEMBER, and the data shows it to the minute:

  * `Servis№10` flew on airframe 10 on 8 September and on airframe 15 from
    9 September onwards;
  * `Kogon№7` flew on airframe 12 until 18:30 on 8 September (UTC+5) and on
    airframe 10 from 18:53 the same evening. The cut is placed at 13:45 UTC
    (18:45 local), between the two, and it is the only rule in this file that
    needs a time of day rather than a date.

Everything else is one spelling, one airframe, for the whole month: 49 rules.

[REASON]: the month and the day are computed as UTC+5, exactly as
_drone_flight_month_expr() does it in drones.py. THESE DRONES SPRAY AT NIGHT --
a flight stored at 20:30 UTC belongs to the next day for the people who flew
it, and grouping by UTC would put the rename on the wrong side of midnight.

Safe / idempotent (same contract as migrate_drones_reattach_bodycode_001.py):
  - refuses to run and exits with code 2 when instance/transport.db is absent;
  - PRECONDITION per rule: the number of September flights carrying that
    spelling must be exactly the number the sweep read from DJI. One row off
    -> exit 1, full rollback, registry untouched;
  - POSTCONDITION: every machine's September flight count equals the figure
    djiag.com reports for that airframe EXACTLY, its hectares within 0.15, and
    NO September flight is left without a machine;
  - registered through migration_utils, skips itself on a re-run;
  - single transaction; postconditions verified BEFORE recording;
  - stdlib sqlite3 only, no Flask app context;
  - console output is ASCII only.

Run (service must be STOPPED first):

  cd C:\\transport-report
  .\\nssm.exe stop TransportReport
  copy instance\\transport.db instance\\transport.db.backup_sept
  "C:\\Program Files\\Python314\\python.exe" migrate_drones_reattach_sept2025_001.py
  .\\nssm.exe start TransportReport

Rollback:
  Reverting the commit leaves the corrected attribution in place, which is the
  correct state. To restore September exactly as it was -- 5 304 flights with
  no machine and 128 on the wrong one -- the spellings and the machines they
  used to point at are in the alias map (drone_nicknames), so the honest undo
  is to clear the month and re-run the old resolution:

    UPDATE drone_flights SET drone_unit_id = NULL
     WHERE strftime('%Y-%m', datetime(started_at, '+300 minutes')) = '2025-09';
    -- then re-apply drone_nicknames through the re-attribution screen
    DELETE FROM schema_migrations WHERE name = 'DRONES_REATTACH_SEPT2025_001';
"""

import os
import sqlite3
import sys

from migration_utils import (ensure_schema_migrations_table,
                             is_migration_applied, migration_checksum,
                             record_migration)

MIGRATION_ID = 'DRONES_REATTACH_SEPT2025_001'

DESCRIPTION = ('DRONE-BODYCODE-001: September 2025 attributed from the DJI '
               'per-device sweep of 2026-08-14 -- 5 304 flights gained a '
               'machine, 128 moved, all 5 661 verified against djiag.com.')

DB_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                       'instance', 'transport.db')

MONTH = '2025-09'
DISPLAY_TZ_MINUTES = 300
HA_TOL = 0.15

# [REASON]: проценты SQLite экранированы удвоением, а `%d` -- настоящая
# подстановка смещения. Сгенерированная версия этого файла ставила одинарный
# `%Y`, и модуль падал на импорте с ValueError ещё до первого запроса.
MONTH_EXPR = ("strftime('%%Y-%%m', datetime(started_at, '+%d minutes'))"
              % DISPLAY_TZ_MINUTES)
DAY_EXPR = ("strftime('%%Y-%%m-%%d', datetime(started_at, '+%d minutes'))"
            % DISPLAY_TZ_MINUTES)

# (написание ника, номер машины, сколько таких вылетов прочитано у DJI)
SIMPLE_RULES = (
    ('1 Klaster', 1, 36),
    ('11 Kogon', 11, 10),
    ('12 Peshku', 13, 69),
    ('13 Servis', 15, 18),
    ('14 Servis', 12, 31),
    ('15 Servis', 14, 10),
    ('2 Klaster', 2, 3),
    ('3 Gijduvon', 3, 49),
    ('4 Gijduvon', 4, 34),
    ('5 Shofirko', 5, 8),
    ('6 Shofirko', 6, 13),
    ('7 Garden', 7, 23),
    ('8 Garden', 8, 53),
    ('Agro 4', 14, 2),
    ('Agro 5', 14, 22),
    ('Agro1', 4, 33),
    ('Agro3', 6, 17),
    ('Agro4', 7, 24),
    ('Dron2', 13, 42),
    ('GardenN6', 8, 724),
    ('Garden№5', 9, 9),
    ('Gijduvon№3', 3, 605),
    ('Klaster№1', 1, 553),
    ('Klaster№2', 2, 2),
    ('Kogon№8', 11, 436),
    ('Mirzachol №2', 8, 7),
    ('Mirzachol №6', 12, 45),
    ('Mirzochol №3', 11, 15),
    ('Mirzochol №4', 15, 22),
    ('Mirzochol №5', 10, 35),
    ('N2Gijduvon', 4, 230),
    ('PeshBekzod', 13, 153),
    ('PeshkShodi', 14, 140),
    ('Peshku№4', 15, 12),
    ('Peshku№9', 12, 267),
    ('Q.Nurali', 8, 63),
    ('ShofirkoN2', 6, 130),
    ('Shofirko№4', 5, 340),
    ('VobkenShox', 7, 115),
    ('№001', 1, 87),
    ('№001 Sayfu', 1, 144),
    ('№3', 3, 4),
    ('№3 Kogon', 11, 66),
    ('№3 Shaxzod', 3, 108),
    ('№4', 5, 57),
    ('№4 Servis', 15, 52),
    ('№4 Shaxzod', 5, 27),
    ('№5', 9, 6),
    ('№6 Shoxruh', 8, 92),
)

# Два написания сменили борт внутри месяца -- 8 сентября, в день переименования
# парка. Границы прочитаны из данных, а не назначены.
SPLIT_SERVIS10_DAY = '2025-09-08'      # в этот день -- борт 10, дальше 15
SPLIT_SERVIS10_ON_DAY = 10
SPLIT_SERVIS10_OTHERWISE = 15
SPLIT_SERVIS10_COUNTS = {10: 8, 15: 371}

SPLIT_KOGON7_DAY = '2025-09-08'
SPLIT_KOGON7_CUT_UTC = '2025-09-08 13:45:00'   # 18:45 по UTC+5
SPLIT_KOGON7_BEFORE = 12                       # последний вылет в 18:30
SPLIT_KOGON7_AFTER = 10                        # следующий в 18:53
SPLIT_KOGON7_COUNTS = {10: 229, 12: 10}

# Что показывает djiag.com по каждому борту за сентябрь: (вылетов, гектаров).
# Постусловие сверяется С ЭТИМИ ЧИСЛАМИ, то есть с кабинетом, а не с
# собственной арифметикой миграции.
DJI_AFTER = {
     1: ( 820,  1009.75),
     2: (   5,     1.76),
     3: ( 766,   795.08),
     4: ( 297,   217.51),
     5: ( 432,   311.28),
     6: ( 160,   130.49),
     7: ( 162,   143.97),
     8: ( 939,   890.25),
     9: (  15,    10.66),
    10: ( 272,   299.45),
    11: ( 527,   534.57),
    12: ( 353,   333.70),
    13: ( 264,   273.21),
    14: ( 174,   168.81),
    15: ( 475,   451.86),
}

EXPECTED_TOTAL = 5661


def _table_exists(cur, name):
    cur.execute("SELECT name FROM sqlite_master WHERE type='table' AND name=?",
                (name,))
    return cur.fetchone() is not None


def _unit_ids(cur):
    ids = {}
    for number, uid in cur.execute('SELECT number, id FROM drone_units'):
        ids[number] = uid
    return ids


def _count(cur, nickname):
    return cur.execute(
        'SELECT COUNT(*) FROM drone_flights WHERE nickname_raw = ? AND %s = ?'
        % MONTH_EXPR, (nickname, MONTH)).fetchone()[0]


def run():
    if not os.path.exists(DB_PATH):
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
        ids = _unit_ids(cur)
        for number in sorted(DJI_AFTER):
            if number not in ids:
                raise RuntimeError('precondition failed: no drone_units row '
                                   'with number=%d' % number)

        total = cur.execute(
            'SELECT COUNT(*) FROM drone_flights WHERE %s = ?' % MONTH_EXPR,
            (MONTH,)).fetchone()[0]
        if total != EXPECTED_TOTAL:
            raise RuntimeError(
                'precondition failed: %s holds %d flight(s), the sweep read '
                '%d' % (MONTH, total, EXPECTED_TOTAL))

        # Precondition: every spelling is present in exactly the number the
        # sweep read. A spelling that has grown or shrunk means the database
        # is not the one these rules were derived from.
        for nickname, _number, expected in SIMPLE_RULES:
            got = _count(cur, nickname)
            if got != expected:
                raise RuntimeError(
                    'precondition failed: %r has %d September flight(s), the '
                    'sweep read %d' % (nickname, got, expected))
        for nickname, counts in (('Servis№10', SPLIT_SERVIS10_COUNTS),
                                 ('Kogon№7', SPLIT_KOGON7_COUNTS)):
            got = _count(cur, nickname)
            want = sum(counts.values())
            if got != want:
                raise RuntimeError(
                    'precondition failed: %r has %d September flight(s), the '
                    'sweep read %d' % (nickname, got, want))

        moved = 0
        for nickname, number, _expected in SIMPLE_RULES:
            cur.execute(
                'UPDATE drone_flights SET drone_unit_id = ? '
                'WHERE nickname_raw = ? AND %s = ?' % MONTH_EXPR,
                (ids[number], nickname, MONTH))
            moved += cur.rowcount

        cur.execute(
            'UPDATE drone_flights SET drone_unit_id = ? '
            'WHERE nickname_raw = ? AND %s = ? AND %s = ?'
            % (MONTH_EXPR, DAY_EXPR),
            (ids[SPLIT_SERVIS10_ON_DAY], 'Servis№10', MONTH,
             SPLIT_SERVIS10_DAY))
        moved += cur.rowcount
        cur.execute(
            'UPDATE drone_flights SET drone_unit_id = ? '
            'WHERE nickname_raw = ? AND %s = ? AND %s != ?'
            % (MONTH_EXPR, DAY_EXPR),
            (ids[SPLIT_SERVIS10_OTHERWISE], 'Servis№10', MONTH,
             SPLIT_SERVIS10_DAY))
        moved += cur.rowcount

        cur.execute(
            'UPDATE drone_flights SET drone_unit_id = ? '
            'WHERE nickname_raw = ? AND %s = ? AND started_at < ?'
            % MONTH_EXPR,
            (ids[SPLIT_KOGON7_BEFORE], 'Kogon№7', MONTH,
             SPLIT_KOGON7_CUT_UTC))
        moved += cur.rowcount
        cur.execute(
            'UPDATE drone_flights SET drone_unit_id = ? '
            'WHERE nickname_raw = ? AND %s = ? AND started_at >= ?'
            % MONTH_EXPR,
            (ids[SPLIT_KOGON7_AFTER], 'Kogon№7', MONTH, SPLIT_KOGON7_CUT_UTC))
        moved += cur.rowcount

        # Postconditions BEFORE recording.
        orphans = cur.execute(
            'SELECT COUNT(*) FROM drone_flights '
            'WHERE %s = ? AND drone_unit_id IS NULL' % MONTH_EXPR,
            (MONTH,)).fetchone()[0]
        if orphans:
            raise RuntimeError('postcondition failed: %d September flight(s) '
                               'still have no machine' % orphans)
        for number in sorted(DJI_AFTER):
            want_flights, want_ha = DJI_AFTER[number]
            got_flights, got_ha = cur.execute(
                'SELECT COUNT(*), COALESCE(ROUND(SUM(area_ha), 4), 0) '
                'FROM drone_flights WHERE drone_unit_id = ? AND %s = ?'
                % MONTH_EXPR, (ids[number], MONTH)).fetchone()
            if got_flights != want_flights:
                raise RuntimeError(
                    'postcondition failed: machine %d has %d September '
                    'flight(s), djiag.com reports %d'
                    % (number, got_flights, want_flights))
            if abs(got_ha - want_ha) > HA_TOL:
                raise RuntimeError(
                    'postcondition failed: machine %d has %.2f ha in '
                    'September, djiag.com reports %.2f'
                    % (number, got_ha, want_ha))

        con.commit()
    except Exception as exc:
        con.rollback()
        print('ERROR: migration failed and was rolled back: %s' % exc)
        sys.exit(1)
    finally:
        con.close()

    record_migration(MIGRATION_ID, description=DESCRIPTION,
                     checksum=migration_checksum(__file__))
    print('Done. September 2025 is attributed: %d flight(s) written.' % moved)
    print('Every machine now shows exactly what djiag.com shows for its '
          'airframe, and no September flight is left without a machine.')


if __name__ == '__main__':
    run()
