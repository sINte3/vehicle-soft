# -*- coding: utf-8 -*-
"""Migration AGRO_WORK_001 -- the tables of the agro-work import (track
agro-work, increment B1) and of the work-type dictionary (B2).

Creates SEVEN tables. Purely additive: no existing table, column or row is
created, modified or dropped.

Creates:
  - agro_work_import_runs     -- one row per importer run: what it saw, what
                                 it wrote, how many requests it cost. The
                                 identity rows_seen = rows_new + rows_updated
                                 + rows_unchanged + rows_rejected +
                                 rows_duplicate holds for every finished run.
  - agro_work_work_types      -- the 67 work types of agro-work with the unit
                                 their API gives, and the reconciliation
                                 method the OWNER assigns (B2). `method` is
                                 NULL until he does; the importer never
                                 touches it.
  - agro_work_transports      -- the machines of the agro-work registry,
                                 without driver name and phone, and the
                                 resolved link to our equipment row.
  - agro_work_transport_links -- the owner's hand-made links (answer 7 of
                                 28.09): survive every re-import, are never
                                 deleted -- unlinking stamps unlinked_at.
  - agro_work_applications    -- the applications, keyed by agro-work's own
                                 id, without any personal field, plus the
                                 dates derived from the status history.
  - agro_work_status_events   -- the status history of an application, as
                                 their API gives it: action, statuses, time,
                                 and the NAMES of the changed fields only.
  - agro_work_changes         -- the journal: every field the importer saw
                                 change, every link and every method set by
                                 hand. Append-only, enforced by two triggers.

[REASON]: personal data is kept out BY CONSTRUCTION, not by a filter. The
owner ruled on 28.09 (question 6) that names and phones of farmers, drivers
and operators are not stored: farm_info.owner_name, farm_info.owner_phone,
transport_info.driver_name, created_by_name, updated_by_name. None of these
tables has a column that could hold them. The farm's NAME is an open question
(8) and has no column either; the farm is kept by its agro-work id only.
agro_work_status_events.changed_fields keeps field names, never values: a
changed farmer would otherwise carry both names into our database.

[REASON]: agro_work_changes and agro_work_status_events are append-only, and
it is the database that says so, not a promise of the importer. A journal
that a later fix can quietly rewrite is not a journal; the status history is
a mirror of a history that itself only grows. Nothing in the track ever needs
UPDATE or DELETE on them -- a re-fetched history is merged with INSERT OR
IGNORE on its natural key.

[REASON]: the owner's link is a separate table and is never overwritten by
the importer. The importer recomputes the AUTOMATIC link (exact plate) on
every run; the owner's decision is an input to that computation, not one of
its outputs, and a re-import must not be able to erase it (answer 7).

[REASON]: `method` is constrained to the four values of the track plan
(гектары / время / рейсы / не сверяется, as ASCII slugs). The owner marks the
method, the code never guesses it -- and a typo in a hand-edited spreadsheet
must fail at the write, not turn into a fifth method that nothing reads.

Safe / idempotent (same contract as migrate_drones_reattach_001.py):
  - refuses to run and exits with code 2 when instance/transport.db is
    absent (sqlite3.connect would otherwise CREATE an empty database);
  - CREATE TABLE / INDEX / TRIGGER IF NOT EXISTS, so a second run over
    existing tables is a no-op even if the registry row were lost;
  - registered through migration_utils, skips itself on a re-run and prints
    'Already applied. Nothing to do.';
  - single transaction; postconditions verified BEFORE recording;
  - no Flask app context, stdlib sqlite3 only (never `from app import app`:
    create_app() calls db.create_all() at import time and turns a reader
    into a writer);
  - console output is ASCII only (NSSM/Windows log encoding).

Run on production -- by the release, never before it (service must be STOPPED
first to avoid SQLite write conflicts):

  cd C:\\transport-report
  .\\nssm.exe stop TransportReport
  copy instance\\transport.db instance\\transport.db.backup_YYYYMMDD
  "C:\\Program Files\\Python314\\python.exe" migrate_agro_work_001.py
  .\\nssm.exe start TransportReport

Before the release the owner runs it ONLY inside the separate folder of
docs/AGRO_WORK_B1_RUNBOOK.md, where instance\\transport.db is a copy: the
path below is taken from the location of this file, so a copy of the script
can only ever touch the database next to it.

Rollback:
  Code rollback and data rollback are separate, and in that order. Reverting
  the agro-work commits leaves the seven tables in place; that is harmless --
  nothing reads them once the code is gone, and db.create_all() does not drop
  tables (no ORM model maps them). Dropping them is a manual step and is only
  correct AFTER the code revert.

  BEFORE dropping, save the owner's decisions: the hand-made links and the
  methods are entered by a person and no re-import can restore them.

    .mode csv
    .output agro_work_links_backup.csv
    SELECT agro_transport_id, equipment_id, plate_at_link, linked_at,
           unlinked_at, note FROM agro_work_transport_links;
    .output agro_work_methods_backup.csv
    SELECT id, name, unit, method, method_set_at, method_source
      FROM agro_work_work_types WHERE method IS NOT NULL;
    .output stdout
    DROP TABLE agro_work_changes;
    DROP TABLE agro_work_status_events;
    DROP TABLE agro_work_applications;
    DROP TABLE agro_work_transport_links;
    DROP TABLE agro_work_transports;
    DROP TABLE agro_work_work_types;
    DROP TABLE agro_work_import_runs;
    DELETE FROM schema_migrations WHERE name = 'AGRO_WORK_001';

  The triggers go with their tables. No rollback step edits or deletes any
  pre-existing row of any other table.
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
MIGRATION_ID = 'AGRO_WORK_001'
DESCRIPTION = ('agro-work B1/B2: create agro_work_import_runs, '
               'agro_work_work_types, agro_work_transports, '
               'agro_work_transport_links, agro_work_applications, '
               'agro_work_status_events and agro_work_changes (append-only '
               'journal). No personal field of agro-work has a column. '
               'Additive only, seven new tables, no pre-existing row '
               'modified.')

# Methods of the track plan, B2: гектары, время, рейсы, не сверяется.
METHODS = ('ga', 'vremya', 'reysy', 'ne_sveryaetsya')
MATCH_STATUSES = ('manual', 'auto', 'ambiguous', 'none')

CREATE_IMPORT_RUNS = """
    CREATE TABLE IF NOT EXISTS agro_work_import_runs (
        id              INTEGER PRIMARY KEY AUTOINCREMENT,
        started_at      DATETIME    NOT NULL,
        finished_at     DATETIME,
        status          VARCHAR(20) NOT NULL,
        tool_version    VARCHAR(40) NOT NULL,
        api_count       INTEGER,
        pages           INTEGER NOT NULL DEFAULT 0,
        rows_seen       INTEGER NOT NULL DEFAULT 0,
        rows_new        INTEGER NOT NULL DEFAULT 0,
        rows_updated    INTEGER NOT NULL DEFAULT 0,
        rows_unchanged  INTEGER NOT NULL DEFAULT 0,
        rows_rejected   INTEGER NOT NULL DEFAULT 0,
        rows_duplicate  INTEGER NOT NULL DEFAULT 0,
        rows_gone       INTEGER NOT NULL DEFAULT 0,
        history_fetched INTEGER NOT NULL DEFAULT 0,
        history_failed  INTEGER NOT NULL DEFAULT 0,
        history_pending INTEGER,
        requests        INTEGER NOT NULL DEFAULT 0,
        logins          INTEGER NOT NULL DEFAULT 0,
        refreshes       INTEGER NOT NULL DEFAULT 0,
        message         TEXT,
        detail_json     TEXT
    )
"""

CREATE_WORK_TYPES = """
    CREATE TABLE IF NOT EXISTS agro_work_work_types (
        id             VARCHAR(36)  PRIMARY KEY,
        name           VARCHAR(300) NOT NULL,
        unit           VARCHAR(20),
        unit_display   VARCHAR(60),
        method         VARCHAR(20),
        method_set_at  DATETIME,
        method_source  VARCHAR(200),
        first_seen_at  DATETIME NOT NULL,
        last_seen_at   DATETIME NOT NULL,
        CONSTRAINT ck_agro_work_work_types_method CHECK (
            method IS NULL OR method IN ('ga', 'vremya', 'reysy',
                                         'ne_sveryaetsya'))
    )
"""

CREATE_TRANSPORTS = """
    CREATE TABLE IF NOT EXISTS agro_work_transports (
        id             VARCHAR(36)  PRIMARY KEY,
        plate_number   VARCHAR(40)  NOT NULL,
        plate_norm     VARCHAR(40)  NOT NULL,
        brand_name     VARCHAR(120),
        model          VARCHAR(120),
        category_name  VARCHAR(120),
        company_id     VARCHAR(36),
        company_name   VARCHAR(300),
        in_registry    INTEGER NOT NULL DEFAULT 0,
        equipment_id   INTEGER REFERENCES equipment (id),
        match_status   VARCHAR(20) NOT NULL DEFAULT 'none',
        first_seen_at  DATETIME NOT NULL,
        last_seen_at   DATETIME NOT NULL,
        CONSTRAINT ck_agro_work_transports_match CHECK (
            match_status IN ('manual', 'auto', 'ambiguous', 'none'))
    )
"""

CREATE_TRANSPORT_LINKS = """
    CREATE TABLE IF NOT EXISTS agro_work_transport_links (
        id                 INTEGER PRIMARY KEY AUTOINCREMENT,
        agro_transport_id  VARCHAR(36) NOT NULL
                           REFERENCES agro_work_transports (id),
        equipment_id       INTEGER NOT NULL REFERENCES equipment (id),
        plate_at_link      VARCHAR(40) NOT NULL,
        linked_at          DATETIME NOT NULL,
        unlinked_at        DATETIME,
        note               VARCHAR(200)
    )
"""

CREATE_APPLICATIONS = """
    CREATE TABLE IF NOT EXISTS agro_work_applications (
        id                  VARCHAR(36) PRIMARY KEY,
        application_number  VARCHAR(40) NOT NULL,
        company_id          VARCHAR(36),
        company_key         VARCHAR(10),
        company_name        VARCHAR(300),
        transport_id        VARCHAR(36),
        plate_number        VARCHAR(40),
        farm_id             VARCHAR(36),
        work_type_id        VARCHAR(36),
        work_type_name      VARCHAR(300),
        unit                VARCHAR(20),
        volume              VARCHAR(40),
        unit_price          VARCHAR(40),
        total_amount        VARCHAR(40),
        payment_type        VARCHAR(20),
        with_fuel           INTEGER,
        status              VARCHAR(20) NOT NULL,
        start_time          VARCHAR(40),
        end_time            VARCHAR(40),
        is_active           INTEGER,
        created_at          VARCHAR(40) NOT NULL,
        updated_at          VARCHAR(40) NOT NULL,
        created_day         DATE NOT NULL,
        first_seen_run_id   INTEGER NOT NULL
                            REFERENCES agro_work_import_runs (id),
        last_seen_run_id    INTEGER NOT NULL
                            REFERENCES agro_work_import_runs (id),
        gone_at             DATETIME,
        history_updated_at  VARCHAR(40),
        history_fetched_at  DATETIME,
        initial_status      VARCHAR(20),
        completed_at        VARCHAR(40),
        completed_day       DATE,
        cancelled_at        VARCHAR(40)
    )
"""

CREATE_STATUS_EVENTS = """
    CREATE TABLE IF NOT EXISTS agro_work_status_events (
        id              INTEGER PRIMARY KEY AUTOINCREMENT,
        application_id  VARCHAR(36) NOT NULL
                        REFERENCES agro_work_applications (id),
        action          VARCHAR(30) NOT NULL DEFAULT '',
        old_status      VARCHAR(20) NOT NULL DEFAULT '',
        new_status      VARCHAR(20) NOT NULL DEFAULT '',
        changed_at      VARCHAR(40) NOT NULL,
        changed_fields  TEXT,
        fetched_at      DATETIME NOT NULL,
        CONSTRAINT uq_agro_work_status_events_event UNIQUE (
            application_id, changed_at, action, old_status, new_status)
    )
"""

CREATE_CHANGES = """
    CREATE TABLE IF NOT EXISTS agro_work_changes (
        id          INTEGER PRIMARY KEY AUTOINCREMENT,
        run_id      INTEGER REFERENCES agro_work_import_runs (id),
        source      VARCHAR(20) NOT NULL,
        entity      VARCHAR(20) NOT NULL,
        entity_id   VARCHAR(36) NOT NULL,
        field       VARCHAR(40) NOT NULL,
        old_value   TEXT,
        new_value   TEXT,
        changed_at  DATETIME NOT NULL
    )
"""

TABLES = (
    ('agro_work_import_runs', CREATE_IMPORT_RUNS),
    ('agro_work_work_types', CREATE_WORK_TYPES),
    ('agro_work_transports', CREATE_TRANSPORTS),
    ('agro_work_transport_links', CREATE_TRANSPORT_LINKS),
    ('agro_work_applications', CREATE_APPLICATIONS),
    ('agro_work_status_events', CREATE_STATUS_EVENTS),
    ('agro_work_changes', CREATE_CHANGES),
)

INDEXES = (
    ('ix_agro_work_transports_equipment',
     'CREATE INDEX IF NOT EXISTS ix_agro_work_transports_equipment '
     'ON agro_work_transports (equipment_id)',
     'agro_work_transports'),
    ('ix_agro_work_transports_plate',
     'CREATE INDEX IF NOT EXISTS ix_agro_work_transports_plate '
     'ON agro_work_transports (plate_norm)',
     'agro_work_transports'),
    # [REASON]: at most ONE live link per agro-work machine. A partial unique
    # index, because an unlinked row stays in the table for the history --
    # only the rows with unlinked_at IS NULL are the owner's current word.
    ('uq_agro_work_transport_links_active',
     'CREATE UNIQUE INDEX IF NOT EXISTS uq_agro_work_transport_links_active '
     'ON agro_work_transport_links (agro_transport_id) '
     'WHERE unlinked_at IS NULL',
     'agro_work_transport_links'),
    # [REASON]: the reconciliation asks "which applications of this machine
    # touch this day" for every machine-day of a period; a scan of the whole
    # season per question would make the screen as slow as the season is long.
    ('ix_agro_work_applications_transport_day',
     'CREATE INDEX IF NOT EXISTS ix_agro_work_applications_transport_day '
     'ON agro_work_applications (transport_id, created_day)',
     'agro_work_applications'),
    ('ix_agro_work_applications_number',
     'CREATE INDEX IF NOT EXISTS ix_agro_work_applications_number '
     'ON agro_work_applications (application_number)',
     'agro_work_applications'),
    ('ix_agro_work_status_events_application',
     'CREATE INDEX IF NOT EXISTS ix_agro_work_status_events_application '
     'ON agro_work_status_events (application_id)',
     'agro_work_status_events'),
    ('ix_agro_work_changes_entity',
     'CREATE INDEX IF NOT EXISTS ix_agro_work_changes_entity '
     'ON agro_work_changes (entity, entity_id)',
     'agro_work_changes'),
)

TRIGGERS = (
    ('trg_agro_work_changes_no_update',
     'CREATE TRIGGER IF NOT EXISTS trg_agro_work_changes_no_update '
     'BEFORE UPDATE ON agro_work_changes BEGIN '
     "SELECT RAISE(ABORT, 'agro_work_changes is append-only'); END"),
    ('trg_agro_work_changes_no_delete',
     'CREATE TRIGGER IF NOT EXISTS trg_agro_work_changes_no_delete '
     'BEFORE DELETE ON agro_work_changes BEGIN '
     "SELECT RAISE(ABORT, 'agro_work_changes is append-only'); END"),
    ('trg_agro_work_status_events_no_update',
     'CREATE TRIGGER IF NOT EXISTS trg_agro_work_status_events_no_update '
     'BEFORE UPDATE ON agro_work_status_events BEGIN '
     "SELECT RAISE(ABORT, 'agro_work_status_events is append-only'); END"),
    ('trg_agro_work_status_events_no_delete',
     'CREATE TRIGGER IF NOT EXISTS trg_agro_work_status_events_no_delete '
     'BEFORE DELETE ON agro_work_status_events BEGIN '
     "SELECT RAISE(ABORT, 'agro_work_status_events is append-only'); END"),
)

EXPECTED_COLUMNS = {
    'agro_work_import_runs': (
        'id', 'started_at', 'finished_at', 'status', 'tool_version',
        'api_count', 'pages', 'rows_seen', 'rows_new', 'rows_updated',
        'rows_unchanged', 'rows_rejected', 'rows_duplicate', 'rows_gone',
        'history_fetched', 'history_failed', 'history_pending', 'requests',
        'logins', 'refreshes', 'message', 'detail_json'),
    'agro_work_work_types': (
        'id', 'name', 'unit', 'unit_display', 'method', 'method_set_at',
        'method_source', 'first_seen_at', 'last_seen_at'),
    'agro_work_transports': (
        'id', 'plate_number', 'plate_norm', 'brand_name', 'model',
        'category_name', 'company_id', 'company_name', 'in_registry',
        'equipment_id', 'match_status', 'first_seen_at', 'last_seen_at'),
    'agro_work_transport_links': (
        'id', 'agro_transport_id', 'equipment_id', 'plate_at_link',
        'linked_at', 'unlinked_at', 'note'),
    'agro_work_applications': (
        'id', 'application_number', 'company_id', 'company_key',
        'company_name', 'transport_id', 'plate_number', 'farm_id',
        'work_type_id', 'work_type_name', 'unit', 'volume', 'unit_price',
        'total_amount', 'payment_type', 'with_fuel', 'status', 'start_time',
        'end_time', 'is_active', 'created_at', 'updated_at', 'created_day',
        'first_seen_run_id', 'last_seen_run_id', 'gone_at',
        'history_updated_at', 'history_fetched_at', 'initial_status',
        'completed_at', 'completed_day', 'cancelled_at'),
    'agro_work_status_events': (
        'id', 'application_id', 'action', 'old_status', 'new_status',
        'changed_at', 'changed_fields', 'fetched_at'),
    'agro_work_changes': (
        'id', 'run_id', 'source', 'entity', 'entity_id', 'field',
        'old_value', 'new_value', 'changed_at'),
}

# [REASON]: listed here so the postcondition can prove the rule, not just
# claim it: no table of this migration may carry a column with one of these
# names. The first five are the owner's own list (question 6, 28.09).
# driver_phone is the same kind of field in the transport registry. farm_name
# waits for question 8. comment is the operators' free text: the
# reconciliation does not need it, and a name or a phone typed into it would
# arrive here unasked.
FORBIDDEN_COLUMNS = ('owner_name', 'owner_phone', 'driver_name',
                     'created_by_name', 'updated_by_name',
                     'driver_phone', 'farm_name', 'comment')


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


def _trigger_names(cur):
    cur.execute("SELECT name FROM sqlite_master WHERE type='trigger'")
    return {row[0] for row in cur.fetchall()}


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

        # [REASON]: equipment is the one pre-existing table these tables point
        # at. Without it this is not the application's database, and the links
        # the owner makes would point at nothing.
        if not _table_exists(cur, 'equipment'):
            raise RuntimeError('precondition failed: table equipment does '
                               'not exist')

        for _, ddl in TABLES:
            cur.execute(ddl)
        for _, ddl, _ in INDEXES:
            cur.execute(ddl)
        for _, ddl in TRIGGERS:
            cur.execute(ddl)

        # Postconditions before recording anything.
        for table, expected in EXPECTED_COLUMNS.items():
            if not _table_exists(cur, table):
                raise RuntimeError('postcondition failed: table %s missing'
                                   % table)
            columns = _column_names(cur, table)
            for name in expected:
                if name not in columns:
                    raise RuntimeError('postcondition failed: column %s '
                                       'missing on %s' % (name, table))
            for name in columns:
                if name in FORBIDDEN_COLUMNS:
                    raise RuntimeError('postcondition failed: personal column '
                                       '%s on %s' % (name, table))
        for name, _, table in INDEXES:
            if name not in _index_names(cur, table):
                raise RuntimeError('postcondition failed: index %s missing on '
                                   '%s' % (name, table))
        triggers = _trigger_names(cur)
        for name, _ in TRIGGERS:
            if name not in triggers:
                raise RuntimeError('postcondition failed: trigger %s missing'
                                   % name)

        con.commit()
    except Exception as exc:
        con.rollback()
        print('ERROR: migration failed and was rolled back: %s' % exc)
        sys.exit(1)
    finally:
        con.close()

    record_migration(MIGRATION_ID, description=DESCRIPTION,
                     checksum=migration_checksum(__file__))
    print('Done. %d agro_work tables (%d columns), %d indexes and %d triggers '
          'are in place.' % (len(EXPECTED_COLUMNS),
                             sum(len(c) for c in EXPECTED_COLUMNS.values()),
                             len(INDEXES), len(TRIGGERS)))


if __name__ == '__main__':
    run()
