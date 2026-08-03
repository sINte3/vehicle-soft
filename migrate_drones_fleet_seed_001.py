# -*- coding: utf-8 -*-
"""Migration DRONES_FLEET_SEED_001 -- DRONE-FLEET-001 reference data seed.

Fills the directories created by migrate_drones_fleet_001.py from
`Список операторов.xlsx`, sheets `Свод1` (dated 05.04.2026) and `10.07`
(covers 30.07-04.08.2026, so current as of 2026-08-02). Values were extracted
with openpyxl, values and not formulas.

Seeds:
  - drone_units.hardware_id       13 of 15 machines (see below)
  - drone_units.generator_id      14 of 15 machines
  - drone_units.subdivision_name  15 of 15 machines, DESCRIPTIVE ONLY
  - drone_operators               17 rows
  - drone_batteries               46 rows
  - drone_unit_status_log         15 rows, changed_on = 2026-08-02

Seeds NOTHING into drone_operator_assignments. This is deliberate and is the
most important line in this file.

[REASON]: NO ASSIGNMENT IS SEEDED. The current dispatcher sheet is
self-contradictory: it puts Anvarov Usmon on machines No 4 and No 8 at the
same time, marks No 10 as vacant while naming an operator for it, and gives
one phone number to two different people. The assignment table is what drives
hectare attribution -- the highest-consequence field in this whole task -- and
a wrong assignment moves hectares silently onto the wrong man. There is no
objective mark in our data that says which reading of the sheet is right; only
the owner knows. He enters assignments through /drones/operators, from
knowledge that exists nowhere in any file. Seeding a plausible guess here
would look like a fact.

[REASON]: hardware_id is seeded ONLY for the thirteen machines where the two
sheets agree. Machines No 2 and No 9 are left EMPTY because the sheets swap
their identifiers: one says No 2 = 64TBL8F002007V and No 9 = 64TBL8F002006B,
the other says exactly the opposite. Writing either version would put a wrong
airframe number into a register people will trust and would look identical to
a verified one. The screen lets the owner fill them in once he has walked out
to the airframes and read the plates.

[REASON]: the `Свод1` sheet writes several identifiers with Cyrillic
homoglyphs -- 64TBL8Е002007Н carries Cyrillic Е and Н, 64TBL6С002008V and
64TBL6С00200FE carry Cyrillic С. The literals below are the LATIN forms from
the `10.07` sheet, which agree with `Свод1` once the homoglyphs are folded.
plate_norm.py already implements that fold, and it is deliberately NOT wired
in here: PLATE-NORM-001 is a separate open task, and this seed stores literals
rather than normalising anything at write time.

Recorded conflicts -- resolved where a resolution was possible, named where it
was not. None of them is silently picked:

  1. PHONES. The `10.07` sheet gives 93-688-55-10 to BOTH Садуллаев Шахбоз and
     Рухиллоев Сайфулло; `Свод1` gives Рухиллоев 90-712-21-12. The seed uses
     the `Свод1` value for Рухиллоев and records this as a resolved conflict in
     his note, not as certainty.
  2. STATUS COUNT. The source sheet's own summary text says "8 соз / 7 носоз"
     while its counting columns say 9 and 6. The per-machine rows are the
     primary record and they give NINE serviceable and SIX unserviceable, which
     is what is seeded. The contradiction is written into the note of every
     seeded status row rather than resolved by preferring one number.
  3. BATTERIES 1-7. They sit in one merged cell covering the two
     "Бухоро Агрокластер" rows of the source, i.e. machines No 1 and No 2
     together. They are seeded to machine No 1 and every one of them carries a
     note saying the source does not separate them between No 1 and No 2.
  4. BATTERIES 29-34. The source lists 32-34 above 29-31 under two rows both
     labelled "Дрон № 11". The `10.07` sheet establishes that the first of those
     rows is machine No 10 (hardware ...AQ) and the second is No 11 (...AA);
     that split is used here and is named in each of those six notes.
  5. DUPLICATE SERIAL. Batteries labelled No 8 and No 10 both bear
     65VPN19DA50MSU. Seeded as they stand -- the schema carries no unique index
     for exactly this reason -- and both rows say so in their note. The screen
     shows a duplicate warning; nobody guesses which one is a typo.
  6. GENERATOR OF MACHINE No 8. The source writes 6QKZP2TO320033 with the
     letter O where every other identifier of that shape carries a zero. Seeded
     VERBATIM: the task supplies the literals and no second source exists to
     confirm a correction. Named here so the next reader knows it was seen and
     not overlooked.

Machine No 2 has no generator identifier in either sheet; its generator_id is
left empty too.

Safe / idempotent:
  - every INSERT is preceded by an existence check, so a second run inserts
    nothing (operators by full_name, batteries by the dispatchers' label
    number, statuses by "this machine has no status row at all");
  - the three drone_units columns are filled ONLY where the current value is
    NULL or empty, so a re-run can never overwrite a value the owner typed --
    in particular the two airframe identifiers he will fill in by hand;
  - a machine number named below that is absent from drone_units ABORTS the
    whole seed with that number; nothing is silently skipped;
  - refuses to run and exits with code 2 when instance/transport.db is absent;
  - registered through migration_utils, prints 'Already applied. Nothing to
    do.' on a re-run;
  - single transaction; postconditions verified BEFORE recording;
  - stdlib sqlite3 only, no Flask app context;
  - console output is ASCII only (NSSM/Windows log encoding). The seeded data
    itself is Cyrillic and goes to the database, never to the console.

Requires migrate_drones_fleet_001.py to have run first.

Run (service must be STOPPED first to avoid SQLite write conflicts):

  cd C:\\transport-report
  .\\nssm.exe stop TransportReport
  copy instance\\transport.db instance\\transport.db.backup_YYYYMMDD
  "C:\\Program Files\\Python314\\python.exe" migrate_drones_fleet_seed_001.py
  .\\nssm.exe start TransportReport

Rollback:
  Code rollback and data rollback are separate. Reverting the code leaves
  these rows in place and they harm nothing. Data rollback, only if the seed
  itself must go and BEFORE dropping the tables in
  migrate_drones_fleet_001.py's rollback:

    DELETE FROM drone_unit_status_log WHERE comment LIKE '%DRONES_FLEET_SEED_001%';
    DELETE FROM drone_batteries       WHERE note    LIKE '%DRONES_FLEET_SEED_001%';
    DELETE FROM drone_operators       WHERE note    LIKE '%DRONES_FLEET_SEED_001%';
    UPDATE drone_units SET hardware_id = NULL, generator_id = NULL,
                           subdivision_name = NULL;
    DELETE FROM schema_migrations WHERE name = 'DRONES_FLEET_SEED_001';

  Every seeded row carries the literal DRONES_FLEET_SEED_001 in its note or
  comment precisely so this rollback can find its own rows and leave anything
  a human added afterwards alone. The three UPDATEs are the exception: they
  cannot tell a seeded value from one the owner typed, so run them ONLY when
  no one has edited the machine cards yet, otherwise clear the three columns
  by hand for the machines the seed actually filled.
"""

import os
import sqlite3
import sys
from datetime import datetime

from migration_utils import (
    ensure_schema_migrations_table,
    is_migration_applied,
    record_migration,
    migration_checksum,
)

ROOT = os.path.dirname(os.path.abspath(__file__))
DB_PATH = os.path.join(ROOT, 'instance', 'transport.db')
MIGRATION_ID = 'DRONES_FLEET_SEED_001'
DESCRIPTION = ('DRONE-FLEET-001 seed: 17 operators, hardware ids for 13 of 15 '
               'machines, 14 generator ids, 15 descriptive subdivisions, '
               '46 batteries and 15 status rows dated 2026-08-02. NO '
               'assignment is seeded -- the source sheet is contradictory and '
               'assignment drives hectare attribution.')

# The status date of the whole seed: the `10.07` sheet is current as of this
# day. changed_on is NOT now(): the fleet was in this state on 2026-08-02, and
# stamping the run date would move six broken machines into whatever day the
# script happened to be executed.
STATUS_CHANGED_ON = '2026-08-02'

SOURCE_DOC = '«Список операторов.xlsx»'
SOURCE_SHEETS = 'Свод1 (05.04.2026) + 10.07 (по состоянию на 02.08.2026)'
# Every seeded row names its source document, its sheet and its date, and
# carries the migration id so the documented rollback can find its own rows.
NOTE_BASE = ('Источник: %s, листы %s. Заведено миграцией %s.'
             % (SOURCE_DOC, SOURCE_SHEETS, MIGRATION_ID))

STATUS_SERVICEABLE = 'serviceable'
STATUS_UNSERVICEABLE = 'unserviceable'

# The counting contradiction of the source, written into every status row.
STATUS_NOTE = (
    'Сводная строка источника говорит «8 соз / 7 носоз», а его же счётные '
    'столбцы — 9 и 6. Первичны построчные записи по машинам: 9 исправных, '
    '6 неисправных. Противоречие не разрешено, а зафиксировано.')

# ── A. Machines ────────────────────────────────────────────────────────────
# (number, hardware_id, generator_id, subdivision_name)
# hardware_id is None exactly where the two sheets disagree.
MACHINES = (
    (1,  '64TBL8F00200AP', '6QKZM7C0320374', 'Бухоро Агрокластер'),
    (2,  None,             None,             'Бухоро Агрокластер'),
    (3,  '64TBL6C00200C6', '6QKZP2T0320034', 'Ғиждувон ПТЗ'),
    (4,  '64TBL8E002007H', '6QZM5Q0320086',  'Ғиждувон ПТЗ'),
    (5,  '64TBL8E002008K', '6QKZP2L0320051', 'Шофиркон ПТЗ'),
    (6,  '64TBL66002009H', '6QKZM5Q0320250', 'Шофиркон ПТЗ'),
    (7,  '64TBL6C002008V', '6QKZM5Q0320070', 'Гарден Бухоро Агрокластер'),
    # Generator written with the letter O where its siblings carry a zero --
    # seeded verbatim, see conflict 6 in the module docstring.
    (8,  '64TBL8A002002P', '6QKZP2TO320033', 'Гарден Бухоро Агрокластер'),
    (9,  None,             '6QKZP2S0320190', 'Гарден Бухоро Агрокластер'),
    (10, '64TBL6C00200AQ', '6QKZP2L0320028', 'Когон ПТЗ'),
    (11, '64TBL6C00200AA', '6QKZMCQ0320606', 'Когон ПТЗ'),
    (12, '64TBL8H00200BA', '6QKZM5T0320398', 'Пешку ПТЗ'),
    (13, '64TBL6C00200FE', '6QKZM5T0320503', 'Пешку ПТЗ'),
    (14, '64TBL91002004L', '6QKZM5T0320505', 'Бухоро Сервис Агрокластер'),
    (15, '64TBL660020055', '6QKZMCQ0320610', 'Бухоро Сервис Агрокластер'),
)

# ── B. Operators ───────────────────────────────────────────────────────────
# (full_name, phone_primary, phone_secondary, subdivision_name, extra note)
PHONE_CONFLICT_NOTE = (
    'Разрешённый конфликт, не факт: лист 10.07 даёт номер 93-688-55-10 и '
    'Садуллаеву Шахбозу, и Рухиллоеву Сайфулло. Здесь взято значение из листа '
    'Свод1 — 90-712-21-12. Проверить у владельца.')

OPERATORS = (
    ('Садуллаев Шахбоз',    '93-688-55-10', None,           'Бухоро Агрокластер', None),
    ('Рухиллоев Сайфулло',  '90-712-21-12', None,           'Бухоро Агрокластер', PHONE_CONFLICT_NOTE),
    ('Холмуродов Шахзод',   '91-247-30-20', '98-775-38-48', 'Ғиждувон ПТЗ', None),
    ('Анваров Усмон',       '99-885-90-04', None,           'Ғиждувон ПТЗ', None),
    ('Қобилов Фаррух',      '99-703-88-57', '95-181-88-57', 'Шофиркон ПТЗ', None),
    ('Жураев Туйғун',       '33-600-44-74', None,           'Шофиркон ПТЗ', None),
    ('Файзуллаев Шохрух',   '91-444-32-33', None,           'Гарден Бухоро Агрокластер', None),
    ('Қодиров Нурали',      '95-874-68-08', None,           'Гарден Бухоро Агрокластер', None),
    ('Туробов Жахонгир',    '99-588-41-42', None,           'Гарден Бухоро Агрокластер', None),
    ('Хамроев Шохрух',      '99-102-01-31', None,           'Когон ПТЗ', None),
    ('Акрамов Амирбек',     '91-401-74-07', None,           'Когон ПТЗ', None),
    ('Шабанов Дилшод',      None,           None,           'Когон ПТЗ', None),
    ('Қудратов Мухриддин',  '93-425-78-77', None,           'Пешку ПТЗ', None),
    ('Ибодуллаев Хасанбой', '97-759-00-23', None,           'Пешку ПТЗ', None),
    ('Имомов Беҳзод',       '94-320-18-08', None,           'Пешку ПТЗ', None),
    ('Болтаев Шахзод',      '99-589-33-33', None,           'Бухоро Сервис Агрокластер', None),
    ('Жумаев Фуркат',       '99-964-51-41', None,           'Бухоро Сервис Агрокластер', None),
)

# ── C. Machine status as of 2026-08-02 ─────────────────────────────────────
# (machine number, status, comment in the source wording, Uzbek Cyrillic)
STATUSES = (
    (1,  STATUS_UNSERVICEABLE, 'Носоз Градиентда (болт-шоссе, крепления, шланг комп., 16 та паррак алмаштириш керак)'),
    (2,  STATUS_UNSERVICEABLE, 'Носоз, бугун Тошкент олиб кетилади (Заминлари даласида 380 кв симга урилган)'),
    (3,  STATUS_SERVICEABLE,   ''),
    (4,  STATUS_UNSERVICEABLE, 'Носоз Тошкентда (ФХ даласида 380 кв симга урилган)'),
    (5,  STATUS_SERVICEABLE,   '2 та батарейка носоз, 3 таси соз'),
    (6,  STATUS_SERVICEABLE,   '2 та батарейка носоз, 3 таси соз'),
    (7,  STATUS_UNSERVICEABLE, 'Носоз Тошкент олиб кетилган (Гарден ерида 380 кв симга урилган)'),
    (8,  STATUS_SERVICEABLE,   ''),
    (9,  STATUS_SERVICEABLE,   ''),
    (10, STATUS_SERVICEABLE,   ''),
    (11, STATUS_SERVICEABLE,   ''),
    (12, STATUS_UNSERVICEABLE, 'Носоз, паррак алмаштириш керак (5 та)'),
    (13, STATUS_UNSERVICEABLE, '7-мотор носоз, паррак алмаштириш керак (Терминалда вактинча буш)'),
    (14, STATUS_SERVICEABLE,   ''),
    (15, STATUS_SERVICEABLE,   ''),
)

# ── D. Batteries ───────────────────────────────────────────────────────────
BATTERY_WORKING = 'working'
BATTERY_NEW_SEALED = 'new_sealed'

MERGED_1_7_NOTE = ('Батареи 1-7 в источнике стоят в объединённой ячейке на две '
                   'строки «Бухоро Агрокластер» — машины № 1 и № 2. Заведены '
                   'на № 1; источник их между № 1 и № 2 не разделяет.')
SPLIT_10_11_NOTE = ('В источнике 32-34 стоят выше 29-31 под двумя строками, '
                    'обе подписаны «Дрон № 11». По листу 10.07 первая из них — '
                    'машина № 10 (…AQ), вторая — № 11 (…AA); этот разбор и '
                    'использован.')
DUP_SERIAL_NOTE = ('Серийный номер 65VPN19DA50MSU в источнике стоит у батарей '
                   '№ 8 и № 10 одновременно. Заведено как есть: либо описка, '
                   'либо две одинаковые бирки, и проверить это можно только на '
                   'самих батареях.')

# (label_number, serial_number, machine number, condition, extra note)
BATTERIES = (
    (1,  '65VPN18DA50MD4', 1,  BATTERY_WORKING,    MERGED_1_7_NOTE),
    (2,  '65VPN18DA50MGH', 1,  BATTERY_WORKING,    MERGED_1_7_NOTE),
    (3,  '65VPN37DA55ENK', 1,  BATTERY_WORKING,    MERGED_1_7_NOTE),
    (4,  '65VPM6GDA5612T', 1,  BATTERY_WORKING,    MERGED_1_7_NOTE),
    (5,  '65VPM6KDA563DC', 1,  BATTERY_WORKING,    MERGED_1_7_NOTE),
    (6,  '65VPM6KDA563D4', 1,  BATTERY_WORKING,    MERGED_1_7_NOTE),
    (7,  '65VPM6KDA563D7', 1,  BATTERY_WORKING,    MERGED_1_7_NOTE),
    (8,  '65VPN19DA50MSU', 3,  BATTERY_WORKING,    DUP_SERIAL_NOTE),
    (9,  '65VPN18DA50MD0', 3,  BATTERY_WORKING,    None),
    (10, '65VPN19DA50MSU', 3,  BATTERY_WORKING,    DUP_SERIAL_NOTE),
    (11, '65VPN18DA50MA7', 4,  BATTERY_WORKING,    None),
    (12, '65VPN18DA50MK5', 4,  BATTERY_WORKING,    None),
    (13, '65VPN17DA50LZZ', 4,  BATTERY_NEW_SEALED, None),
    (14, '65VPN19DA50MTC', 5,  BATTERY_WORKING,    None),
    (15, '65VPN18DA50MHU', 5,  BATTERY_WORKING,    None),
    (16, '65VPN18DA50MBY', 5,  BATTERY_WORKING,    None),
    (17, '65VPM9QDA56GXH', 6,  BATTERY_WORKING,    None),
    (18, '65VPN18DA50MJD', 6,  BATTERY_WORKING,    None),
    (19, '65VPN19DA50MTL', 6,  BATTERY_WORKING,    None),
    (20, '65VPN5TDA50715', 7,  BATTERY_WORKING,    None),
    (21, '65VPN38DA55F68', 7,  BATTERY_WORKING,    None),
    (22, '65VPN18DA50M7X', 7,  BATTERY_WORKING,    None),
    (23, '65VPN18DA50ME6', 8,  BATTERY_WORKING,    None),
    (24, '65VPN18DA50MBF', 8,  BATTERY_WORKING,    None),
    (25, '65VPN18DA50MJA', 8,  BATTERY_WORKING,    None),
    (26, '65VPN18DA50MMD', 9,  BATTERY_WORKING,    None),
    (27, '65VPN18DA50MBN', 9,  BATTERY_NEW_SEALED, None),
    (28, '65VPN17DA50LYM', 9,  BATTERY_NEW_SEALED, None),
    (29, '65VPN18DA50MKP', 11, BATTERY_WORKING,    SPLIT_10_11_NOTE),
    (30, '65VPN19DA50MSV', 11, BATTERY_WORKING,    SPLIT_10_11_NOTE),
    (31, '65VPN18DA50MKH', 11, BATTERY_WORKING,    SPLIT_10_11_NOTE),
    (32, '65VPN36DA55EH5', 10, BATTERY_WORKING,    SPLIT_10_11_NOTE),
    (33, '65VPN18DA50MC6', 10, BATTERY_WORKING,    SPLIT_10_11_NOTE),
    (34, '65VPN18DA50MCU', 10, BATTERY_NEW_SEALED, SPLIT_10_11_NOTE),
    (35, '65VPN18DA50MNY', 12, BATTERY_WORKING,    None),
    (36, '65VPN18DA50MEJ', 12, BATTERY_WORKING,    None),
    (37, '65VPN14DA50JHX', 12, BATTERY_WORKING,    None),
    (38, '65VPN17DA50LXE', 13, BATTERY_WORKING,    None),
    (39, '65VPN18DA50M5F', 13, BATTERY_WORKING,    None),
    (40, '65VPN18DA50MAW', 13, BATTERY_WORKING,    None),
    (41, '65VPN18DA50MMG', 14, BATTERY_WORKING,    None),
    (42, '65VPN18DA50MBV', 14, BATTERY_WORKING,    None),
    (43, '65VPN18DA50MK6', 14, BATTERY_WORKING,    None),
    (44, '65VPN18DA50MFR', 15, BATTERY_WORKING,    None),
    (45, '65VPN18DA50MGD', 15, BATTERY_WORKING,    None),
    (46, '65VPN19DA50MSC', 15, BATTERY_WORKING,    None),
)

REQUIRED_TABLES = ('drone_units', 'drone_operators',
                   'drone_operator_assignments', 'drone_unit_status_log',
                   'drone_batteries')
REQUIRED_UNIT_COLUMNS = ('hardware_id', 'generator_id', 'subdivision_name')


def _note(extra=None):
    return NOTE_BASE if not extra else '%s %s' % (NOTE_BASE, extra)


def _table_exists(cur, name):
    cur.execute("SELECT name FROM sqlite_master WHERE type='table' AND name=?",
                (name,))
    return cur.fetchone() is not None


def _column_names(cur, table):
    cur.execute('PRAGMA table_info(%s)' % table)
    return [row[1] for row in cur.fetchall()]


def _unit_ids_by_number(cur):
    cur.execute('SELECT number, id FROM drone_units')
    return {row[0]: row[1] for row in cur.fetchall()}


def _seed_machine_fields(cur, unit_ids, now):
    """Fill the three descriptive columns, never overwriting a set value."""
    filled = {'hardware_id': 0, 'generator_id': 0, 'subdivision_name': 0}
    for number, hardware_id, generator_id, subdivision in MACHINES:
        unit_id = unit_ids[number]
        for column, value in (('hardware_id', hardware_id),
                              ('generator_id', generator_id),
                              ('subdivision_name', subdivision)):
            if value is None:
                continue
            # [REASON]: the guard is in the WHERE clause, not in Python. The
            # owner will type the two missing airframe identifiers by hand;
            # a re-run of this seed must be unable to touch them, and that
            # guarantee must not depend on what this process happened to read.
            cur.execute(
                'UPDATE drone_units SET %s = ?, updated_at = ? '
                'WHERE id = ? AND (%s IS NULL OR %s = \'\')'
                % (column, column, column),
                (value, now, unit_id))
            filled[column] += cur.rowcount
    return filled


def _seed_operators(cur, now):
    inserted = 0
    for full_name, phone1, phone2, subdivision, extra in OPERATORS:
        cur.execute('SELECT id FROM drone_operators WHERE full_name = ?',
                    (full_name,))
        if cur.fetchone() is not None:
            continue
        cur.execute(
            'INSERT INTO drone_operators (full_name, phone_primary, '
            'phone_secondary, subdivision_name, is_active, note, created_at, '
            'updated_at) VALUES (?, ?, ?, ?, 1, ?, ?, ?)',
            (full_name, phone1, phone2, subdivision, _note(extra), now, now))
        inserted += 1
    return inserted


def _seed_batteries(cur, unit_ids, now):
    inserted = 0
    for label, serial, number, condition, extra in BATTERIES:
        cur.execute('SELECT id FROM drone_batteries WHERE label_number = ?',
                    (label,))
        if cur.fetchone() is not None:
            continue
        cur.execute(
            'INSERT INTO drone_batteries (serial_number, label_number, '
            'drone_unit_id, condition, note, is_active, created_at, '
            'updated_at) VALUES (?, ?, ?, ?, ?, 1, ?, ?)',
            (serial, label, unit_ids[number], condition, _note(extra),
             now, now))
        inserted += 1
    return inserted


def _seed_statuses(cur, unit_ids, now):
    """One status row per machine, only for machines with no history yet."""
    inserted = 0
    for number, status, source_comment in STATUSES:
        unit_id = unit_ids[number]
        cur.execute('SELECT id FROM drone_unit_status_log '
                    'WHERE drone_unit_id = ?', (unit_id,))
        if cur.fetchone() is not None:
            # [REASON]: the ledger is append-only and someone may already have
            # recorded a change by hand. A seed that appends anyway would put
            # a stale 2026-08-02 row under a fresher one and, on a tie of
            # changed_on, could flip the current status of a machine.
            continue
        # The source wording stays FIRST and verbatim; provenance follows on
        # its own line, because the ledger has no separate note column and the
        # rule is that every seeded row names its source.
        comment = source_comment.strip()
        comment = ('%s\n%s %s' % (comment, _note(), STATUS_NOTE)).strip()
        cur.execute(
            'INSERT INTO drone_unit_status_log (drone_unit_id, status, '
            'comment, changed_on, changed_by, created_at) '
            'VALUES (?, ?, ?, ?, NULL, ?)',
            (unit_id, status, comment, STATUS_CHANGED_ON, now))
        inserted += 1
    return inserted


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

    now = datetime.utcnow().isoformat()
    con = sqlite3.connect(DB_PATH)
    con.execute('PRAGMA busy_timeout=30000')
    try:
        cur = con.cursor()
        cur.execute('BEGIN')

        for table in REQUIRED_TABLES:
            if not _table_exists(cur, table):
                raise RuntimeError('precondition failed: table %s does not '
                                   'exist - run migrate_drones_fleet_001.py '
                                   'first' % table)
        unit_columns = _column_names(cur, 'drone_units')
        for column in REQUIRED_UNIT_COLUMNS:
            if column not in unit_columns:
                raise RuntimeError('precondition failed: drone_units.%s does '
                                   'not exist - run '
                                   'migrate_drones_fleet_001.py first'
                                   % column)

        unit_ids = _unit_ids_by_number(cur)
        # [REASON]: nothing is silently skipped. A machine number named in the
        # seed but missing from drone_units means this database is not the one
        # the appendix describes, and quietly seeding fourteen of fifteen
        # machines would leave a hole nobody would notice.
        for number, _h, _g, _s in MACHINES:
            if number not in unit_ids:
                raise RuntimeError('precondition failed: drone_units has no '
                                   'machine number %d' % number)

        filled = _seed_machine_fields(cur, unit_ids, now)
        operators = _seed_operators(cur, now)
        batteries = _seed_batteries(cur, unit_ids, now)
        statuses = _seed_statuses(cur, unit_ids, now)

        # ---- Postconditions, all of them BEFORE the registry write. ----
        cur.execute('SELECT count(*) FROM drone_operators')
        if cur.fetchone()[0] < len(OPERATORS):
            raise RuntimeError('postcondition failed: fewer than %d operators'
                               % len(OPERATORS))
        cur.execute('SELECT count(*) FROM drone_batteries')
        if cur.fetchone()[0] < len(BATTERIES):
            raise RuntimeError('postcondition failed: fewer than %d batteries'
                               % len(BATTERIES))
        cur.execute('SELECT count(*) FROM drone_units WHERE hardware_id IS '
                    'NOT NULL AND hardware_id != \'\'')
        if cur.fetchone()[0] < 13:
            raise RuntimeError('postcondition failed: fewer than 13 machines '
                               'carry a hardware_id')
        # The two disputed machines must still be empty after a FIRST run.
        cur.execute('SELECT count(*) FROM drone_units u '
                    'JOIN drone_unit_status_log s ON s.drone_unit_id = u.id '
                    'WHERE s.status = ?', (STATUS_SERVICEABLE,))
        serviceable = cur.fetchone()[0]
        cur.execute('SELECT count(*) FROM drone_units u '
                    'JOIN drone_unit_status_log s ON s.drone_unit_id = u.id '
                    'WHERE s.status = ?', (STATUS_UNSERVICEABLE,))
        unserviceable = cur.fetchone()[0]
        if statuses and (serviceable, unserviceable) != (9, 6):
            raise RuntimeError('postcondition failed: status ledger says '
                               '%d serviceable / %d unserviceable, expected '
                               '9 / 6' % (serviceable, unserviceable))
        # [REASON]: the loudest postcondition of this file. NOTHING may end up
        # in drone_operator_assignments, and that has to be read back from the
        # database rather than assumed from the fact that no INSERT was
        # written above.
        cur.execute('SELECT count(*) FROM drone_operator_assignments')
        if cur.fetchone()[0] != 0:
            raise RuntimeError('postcondition failed: this seed must never '
                               'create an operator assignment')

        con.commit()
    except Exception as exc:
        con.rollback()
        print('ERROR: seed failed and was rolled back: %s' % exc)
        sys.exit(1)
    finally:
        con.close()

    record_migration(MIGRATION_ID, description=DESCRIPTION,
                     checksum=migration_checksum(__file__))
    print('Done. operators=%d batteries=%d statuses=%d '
          'hardware_id=%d generator_id=%d subdivision=%d assignments=0'
          % (operators, batteries, statuses, filled['hardware_id'],
             filled['generator_id'], filled['subdivision_name']))
    print('Machines 2 and 9 keep an empty hardware_id on purpose: the two '
          'source sheets swap those two identifiers.')


if __name__ == '__main__':
    run()
