# -*- coding: utf-8 -*-
"""Migration DRONE_AREA_CONTROL_V2_001 -- admin decisions and the DJI cycle ledger.

DRONE-AREA-CONTROL-V2-MEGA. Purely additive: two NEW tables, their indexes
and two guard triggers. No existing table, column or row is created,
modified or dropped. drone_flights.area_ha and every dji_* table
(including dji_area_calculations) are not read, not written and not
referenced by any statement here.

Creates:
  - drone_area_decisions   -- append-only history of administrator
                              decisions on a DJI area record (flight C):
                              decision type, the identity of the automatic
                              calculation it was taken against (calculation
                              id + algorithm version + input hash), snapshots
                              of the auto class and areas, the effective
                              result at decision time, comment, who, when,
                              and the chain link to the decision it replaces
                              (chain_seq 1, 2, 3 ... per flight;
                              supersedes_decision_id);
  - drone_area_cycle_runs  -- ledger of the DJI area cycle runs
                              (FLIGHTS -> MANIFEST -> SOURCES -> RECALC):
                              scheduled and manual ("refresh DJI data now"),
                              state QUEUED/RUNNING/SUCCESS/SUCCESS_WITH_
                              WARNINGS/FAILED/..., who requested it, the
                              window, the step, the exit code and a short
                              ASCII result. `active_slot` carries a partial
                              UNIQUE index: at most one manual run can be
                              QUEUED or RUNNING at a time.
  - two triggers that make drone_area_decisions append-only at the storage
    level: UPDATE and DELETE are refused with RAISE(ABORT).

[REASON]: a decision is its own table, not a column of
dji_area_calculations. The automatic calculation must stay exactly what the
resolver wrote (a recalculation appends a new row per new input), and an
UPDATE of a decision would erase who decided differently and why. A
correction or a revocation is a NEW row that replaces the previous one;
(flight_id, chain_seq) is UNIQUE, so two concurrent edits of one flight can
never both become "current".

[REASON]: the triggers are defence in depth, not the only guard -- the single
writer (dji_area/control_store.py) never issues UPDATE or DELETE on the
table. db.create_all() on a fresh install creates the table WITHOUT the
triggers (SQLAlchemy does not know them); running this migration there adds
them, because every statement is IF NOT EXISTS.

Safe / idempotent (same contract as migrate_dji_area_evidence_001.py):
  - refuses to run and exits with code 2 when instance/transport.db is
    absent (sqlite3.connect would otherwise CREATE an empty database);
  - CREATE TABLE / INDEX / TRIGGER IF NOT EXISTS;
  - registered through migration_utils, skips itself on a re-run and prints
    'Already applied. Nothing to do.';
  - ONE transaction: tables, indexes, triggers, postconditions and the
    schema_migrations row land on a single commit; postconditions are
    verified BEFORE the registry row is written;
  - precondition: users and dji_area_calculations must exist (the account
    table the decision author points to, and the DJI area model this layer
    sits on); otherwise exit code 1 with full rollback;
  - no Flask app context, stdlib sqlite3 only;
  - console output is ASCII only (NSSM/Windows log encoding).

Run (service must be STOPPED first to avoid SQLite write conflicts):

  cd C:\\transport-report
  .\\nssm.exe stop TransportReport
  copy instance\\transport.db instance\\transport.db.backup_before_area_v2
  & "C:\\Program Files\\Python314\\python.exe" migrate_drone_area_control_v2_001.py
  .\\nssm.exe start TransportReport

Rollback:
  Code rollback and data rollback are separate, and in that order.

  1. Code: revert the DRONE-AREA-CONTROL-V2-MEGA merge with `git revert -m 1`.
     The two tables stay in place and are harmless: nothing reads them once
     the code is gone, and db.create_all() does not drop tables. The report
     returns to the automatic result only -- decisions stop applying, they
     are NOT lost.
  2. Data (only after 1, only if the owner wants the history gone -- the
     decisions are audit records, and the project does not delete audit
     records by default):

       DROP TRIGGER trg_drone_area_decisions_no_update;
       DROP TRIGGER trg_drone_area_decisions_no_delete;
       DROP TABLE drone_area_decisions;
       DROP TABLE drone_area_cycle_runs;
       DELETE FROM schema_migrations WHERE name = 'DRONE_AREA_CONTROL_V2_001';

  No rollback step edits or deletes any pre-existing row of any other table.
"""

import os
import sqlite3
import sys

from datetime import datetime

from migration_utils import (
    ensure_schema_migrations_table,
    is_migration_applied,
    migration_checksum,
)

ROOT = os.path.dirname(os.path.abspath(__file__))
DB_PATH = os.path.join(ROOT, 'instance', 'transport.db')
MIGRATION_ID = 'DRONE_AREA_CONTROL_V2_001'
DESCRIPTION = ('DRONE-AREA-CONTROL-V2-MEGA: create drone_area_decisions '
               '(append-only admin decisions over the automatic DJI area '
               'calculation) and drone_area_cycle_runs (ledger of scheduled '
               'and manual DJI area cycles). Additive only; drone_flights and '
               'dji_* tables are neither read nor written.')

# [REASON]: column types are spelled exactly as SQLAlchemy renders the models
# (INTEGER, BIGINT, VARCHAR(n), TEXT, FLOAT, BOOLEAN, DATETIME, DATE) so that
# a migrated database and a fresh db.create_all() database agree column for
# column; tests/test_drone_area_control_v2_store.py
# (test_migration_ddl_matches_the_orm_models) compares the two.
TABLES = (
    ('drone_area_decisions', """
CREATE TABLE IF NOT EXISTS drone_area_decisions (
    id INTEGER NOT NULL,
    flight_id BIGINT NOT NULL,
    chain_seq INTEGER NOT NULL,
    supersedes_decision_id INTEGER,
    decision_type VARCHAR(40) NOT NULL,
    calculation_id INTEGER,
    area_algorithm_version VARCHAR(80),
    calculation_input_hash VARCHAR(64),
    auto_class VARCHAR(30),
    auto_reason VARCHAR(60),
    raw_area_m2 FLOAT,
    auto_accepted_m2 FLOAT,
    auto_excluded_m2 FLOAT,
    effective_accepted_m2 FLOAT,
    effective_excluded_m2 FLOAT,
    is_override BOOLEAN NOT NULL,
    comment TEXT NOT NULL,
    performed_by_user_id INTEGER,
    performed_by_name VARCHAR(150),
    performed_at DATETIME NOT NULL,
    decisions_version VARCHAR(40) NOT NULL,
    PRIMARY KEY (id),
    CONSTRAINT uq_drone_area_decisions_chain UNIQUE (flight_id, chain_seq),
    FOREIGN KEY(supersedes_decision_id) REFERENCES drone_area_decisions (id),
    FOREIGN KEY(performed_by_user_id) REFERENCES users (id)
)"""),
    ('drone_area_cycle_runs', """
CREATE TABLE IF NOT EXISTS drone_area_cycle_runs (
    id INTEGER NOT NULL,
    trigger_kind VARCHAR(20) NOT NULL,
    status VARCHAR(30) NOT NULL,
    active_slot INTEGER,
    requested_by_user_id INTEGER,
    requested_by_name VARCHAR(150),
    requested_at DATETIME NOT NULL,
    started_at DATETIME,
    finished_at DATETIME,
    heartbeat_at DATETIME,
    current_step VARCHAR(20),
    window_from DATE,
    window_to DATE,
    pid INTEGER,
    host VARCHAR(100),
    exit_code INTEGER,
    failed_step VARCHAR(20),
    message TEXT,
    result_json TEXT,
    PRIMARY KEY (id),
    FOREIGN KEY(requested_by_user_id) REFERENCES users (id)
)"""),
)

INDEXES = (
    ('ix_drone_area_decisions_flight',
     'CREATE INDEX IF NOT EXISTS ix_drone_area_decisions_flight '
     'ON drone_area_decisions (flight_id)'),
    ('ix_drone_area_cycle_runs_requested',
     'CREATE INDEX IF NOT EXISTS ix_drone_area_cycle_runs_requested '
     'ON drone_area_cycle_runs (requested_at)'),
    # [REASON]: the double-click guard lives in the database, not only in the
    # route. Two POSTs that pass the "is something running?" check at the
    # same moment both try to INSERT a row with active_slot=1; the second one
    # hits this index and is answered "already running" instead of starting a
    # second DJI collection.
    ('ux_drone_area_cycle_runs_active',
     'CREATE UNIQUE INDEX IF NOT EXISTS ux_drone_area_cycle_runs_active '
     'ON drone_area_cycle_runs (active_slot) WHERE active_slot IS NOT NULL'),
)

TRIGGERS = (
    ('trg_drone_area_decisions_no_update',
     'CREATE TRIGGER IF NOT EXISTS trg_drone_area_decisions_no_update '
     'BEFORE UPDATE ON drone_area_decisions BEGIN '
     "SELECT RAISE(ABORT, 'drone_area_decisions is append-only'); END"),
    ('trg_drone_area_decisions_no_delete',
     'CREATE TRIGGER IF NOT EXISTS trg_drone_area_decisions_no_delete '
     'BEFORE DELETE ON drone_area_decisions BEGIN '
     "SELECT RAISE(ABORT, 'drone_area_decisions is append-only'); END"),
)

TABLE_NAMES = tuple(name for name, _ddl in TABLES)

# Tables the migration REFERENCES or relies on; absent -> wrong database.
PRECONDITION_TABLES = ('users', 'dji_area_calculations')


def _exists(cur, kind, name):
    cur.execute('SELECT name FROM sqlite_master WHERE type=? AND name=?',
                (kind, name))
    return cur.fetchone() is not None


def _column_names(cur, table):
    cur.execute('PRAGMA table_info(%s)' % table)
    return [row[1] for row in cur.fetchall()]


def expected_columns():
    """{table: [column, ...]} parsed from the DDL above -- one source of truth."""
    out = {}
    for name, ddl in TABLES:
        cols = []
        for line in ddl.splitlines():
            stripped = line.strip()
            if not stripped or stripped.startswith(
                    ('CREATE', ')', 'CONSTRAINT', 'UNIQUE', 'PRIMARY',
                     'FOREIGN')):
                continue
            cols.append(stripped.split()[0])
        out[name] = cols
    return out


def _record_in_transaction(cur, name, description, checksum):
    """Registry row on the SAME transaction as the tables (see
    migrate_drones_useful_area_001.py for why record_migration() is not used)."""
    cur.execute(
        'INSERT INTO schema_migrations (name, applied_at, checksum, '
        'description) VALUES (?, ?, ?, ?)',
        (name, datetime.utcnow().isoformat(), checksum, description))


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

        for table in PRECONDITION_TABLES:
            if not _exists(cur, 'table', table):
                raise RuntimeError(
                    'precondition failed: table %s does not exist' % table)

        for _name, ddl in TABLES:
            cur.execute(ddl)
        for _name, statement in INDEXES:
            cur.execute(statement)
        for _name, statement in TRIGGERS:
            cur.execute(statement)

        # Postconditions before recording anything.
        expected = expected_columns()
        for table in TABLE_NAMES:
            if not _exists(cur, 'table', table):
                raise RuntimeError('postcondition failed: table %s missing'
                                   % table)
            columns = _column_names(cur, table)
            for name in expected[table]:
                if name not in columns:
                    raise RuntimeError('postcondition failed: column %s '
                                       'missing on %s' % (name, table))
        for name, _statement in INDEXES:
            if not _exists(cur, 'index', name):
                raise RuntimeError('postcondition failed: index %s missing'
                                   % name)
        for name, _statement in TRIGGERS:
            if not _exists(cur, 'trigger', name):
                raise RuntimeError('postcondition failed: trigger %s missing'
                                   % name)

        _record_in_transaction(cur, MIGRATION_ID, DESCRIPTION,
                               migration_checksum(__file__))
        con.commit()
    except Exception as exc:
        con.rollback()
        print('ERROR: migration failed and was rolled back: %s' % exc)
        sys.exit(1)
    finally:
        con.close()

    total_columns = sum(len(cols) for cols in expected_columns().values())
    print('Done. %d tables (%d columns), %d indexes and %d triggers are in '
          'place.' % (len(TABLE_NAMES), total_columns, len(INDEXES),
                      len(TRIGGERS)))


if __name__ == '__main__':
    run()
