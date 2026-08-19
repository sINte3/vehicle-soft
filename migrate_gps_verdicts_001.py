# -*- coding: utf-8 -*-
"""Migration GPS_VERDICTS_001 -- the review journal of plan-vs-fact verdicts.

Creates ONE table, gps_verdicts. Purely additive: no existing table, column or
row is created, modified or dropped.

WHAT IT IS FOR
Section 3.2 of docs/GPS_PLAN_FAKT_VISION_ROADMAP.md promises the only honest
"learning" this track has: every time a person looks at a verdict and says
"yes, that is right" or "no, it is not", the event is recorded with who, when
and why. Once a season those records -- not a model retrained on the fly --
are what the tolerances get re-tuned against, with a published before/after.

APPEND-ONLY. One row per review event, never updated, never deleted. The
current state of an order is its LATEST row. That is deliberate: a verdict
recomputed tomorrow must not rewrite what a person said today, exactly as an
operator's work/transit answer survives a recomputation (GPS-2).

[REASON]: the numbers are SNAPSHOTTED into this row -- base_quantity, fact_ha,
deviation, verdict and, when there is no verdict, its reason. They are not
looked up later from gps_daily_aggregates, because the day can be recomputed:
points arrive late, a defect gets fixed, the method version moves. A journal
that says "confirmed" while the number next to it has silently changed is
worse than no journal, and the re-tuning it exists for would be done against
numbers nobody ever saw.

[REASON]: the review CHANGES NO NUMBER anywhere. It records that a person
looked. Letting a review edit the fact would turn a measurement into an
opinion, and the whole point of this track is a figure that can be defended a
year later.

Safe / idempotent (same contract as migrate_drones_reattach_001.py):
  - refuses to run and exits with code 2 when instance/transport.db is
    absent (sqlite3.connect would otherwise CREATE an empty database);
  - CREATE TABLE IF NOT EXISTS, so a second run over an existing table is a
    no-op even if the registry row were lost;
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
  copy instance\\transport.db instance\\transport.db.backup_20260819
  "C:\\Program Files\\Python314\\python.exe" migrate_gps_verdicts_001.py
  .\\nssm.exe start TransportReport

Rollback:
  Code rollback and data rollback are separate, and in that order. Reverting
  the GPS-6 commits leaves gps_verdicts in place; that is harmless -- nothing
  reads it once the model is gone, and db.create_all() does not drop tables.

  BEFORE dropping it, save the journal: every row is hand-entered by a person
  and no recomputation can restore it.

    .mode csv
    .output gps_verdicts_backup.csv
    SELECT * FROM gps_verdicts;
    .output stdout
    DROP TABLE gps_verdicts;
    DELETE FROM schema_migrations WHERE name = 'GPS_VERDICTS_001';

  No rollback step edits or deletes any pre-existing row of any other table.
"""

import os
import sqlite3
import sys

from migration_utils import (
    ensure_schema_migrations_table,
    is_migration_applied,
    record_migration,
    migration_checksum,
)

ROOT = os.path.dirname(os.path.abspath(__file__))
DB_PATH = os.path.join(ROOT, 'instance', 'transport.db')
MIGRATION_ID = 'GPS_VERDICTS_001'
DESCRIPTION = ('GPS-6: create gps_verdicts, the append-only journal of '
               'plan-vs-fact verdict reviews (who looked, when, what they '
               'decided, why, and the numbers as they stood at that moment). '
               'Additive only, one new table, no pre-existing row modified.')

CREATE_GPS_VERDICTS = """
    CREATE TABLE IF NOT EXISTS gps_verdicts (
        id             INTEGER PRIMARY KEY AUTOINCREMENT,
        work_order_id  INTEGER NOT NULL REFERENCES work_orders (id),
        work_date      DATE    NOT NULL,
        wialon_id      INTEGER,
        unit           VARCHAR(30) NOT NULL,
        base_quantity  REAL,
        fact_ha        REAL,
        deviation      REAL,
        verdict        VARCHAR(20) NOT NULL,
        no_data_reason VARCHAR(40),
        decision       VARCHAR(20) NOT NULL,
        comment        TEXT NOT NULL DEFAULT '',
        reviewed_by    INTEGER REFERENCES users (id),
        reviewed_at    DATETIME NOT NULL
    )
"""

INDEXES = (
    ('ix_gps_verdicts_order',
     'CREATE INDEX IF NOT EXISTS ix_gps_verdicts_order '
     'ON gps_verdicts (work_order_id, reviewed_at)'),
    ('ix_gps_verdicts_date',
     'CREATE INDEX IF NOT EXISTS ix_gps_verdicts_date '
     'ON gps_verdicts (work_date)'),
)

EXPECTED_COLUMNS = ('id', 'work_order_id', 'work_date', 'wialon_id', 'unit',
                    'base_quantity', 'fact_ha', 'deviation', 'verdict',
                    'no_data_reason', 'decision', 'comment', 'reviewed_by',
                    'reviewed_at')


def _table_exists(cur, name):
    cur.execute("SELECT name FROM sqlite_master WHERE type='table' AND name=?",
                (name,))
    return cur.fetchone() is not None


def _column_names(cur, table):
    cur.execute('PRAGMA table_info(%s)' % table)
    return [row[1] for row in cur.fetchall()]


def _index_names(cur, table):
    cur.execute('PRAGMA index_list(%s)' % table)
    return {row[1] for row in cur.fetchall()}


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

        # [REASON]: both REFERENCES targets must exist before the table is
        # created, or the journal would point at nothing. Their absence means
        # this is the wrong database.
        for table in ('users', 'work_orders'):
            if not _table_exists(cur, table):
                raise RuntimeError(
                    'precondition failed: table %s does not exist' % table)

        cur.execute(CREATE_GPS_VERDICTS)
        for _, statement in INDEXES:
            cur.execute(statement)

        # Postconditions before recording anything.
        if not _table_exists(cur, 'gps_verdicts'):
            raise RuntimeError('postcondition failed: table gps_verdicts '
                               'missing')
        columns = _column_names(cur, 'gps_verdicts')
        for name in EXPECTED_COLUMNS:
            if name not in columns:
                raise RuntimeError('postcondition failed: column %s missing '
                                   'on gps_verdicts' % name)
        present = _index_names(cur, 'gps_verdicts')
        for name, _ in INDEXES:
            if name not in present:
                raise RuntimeError('postcondition failed: index %s missing '
                                   'on gps_verdicts' % name)
        # [REASON]: the journal is append-only, so it must NOT carry a
        # uniqueness rule over the order -- a second review of the same order
        # is the normal case and the whole point of keeping history.
        cur.execute('PRAGMA index_list(gps_verdicts)')
        for row in cur.fetchall():
            if row[2]:
                raise RuntimeError('postcondition failed: gps_verdicts must '
                                   'have no UNIQUE index, found %s' % row[1])

        con.commit()
    except Exception as exc:
        con.rollback()
        print('ERROR: migration failed and was rolled back: %s' % exc)
        sys.exit(1)
    finally:
        con.close()

    record_migration(MIGRATION_ID, description=DESCRIPTION,
                     checksum=migration_checksum(__file__))
    print('Done. Table gps_verdicts with %d columns and %d indexes is in '
          'place.' % (len(EXPECTED_COLUMNS), len(INDEXES)))


if __name__ == '__main__':
    run()
