# -*- coding: utf-8 -*-
"""Migration DJI_AREA_EVIDENCE_001 -- DJI area evidence model storage.

Creates EIGHT tables and their indexes for the model
dji-area-evidence-2026-09-08-final-1. Purely additive: no existing table,
column or row is created, modified or dropped. drone_flights.area_ha -- the
DJI figure the invoices were written against -- is not read, not written and
not referenced by any statement here.

Creates:
  - dji_source_revisions   -- immutable DJI response bodies (or file refs)
                              with SHA256, size, capture time, request
                              context, capture run, parser version;
  - dji_flight_evidence    -- per-flight pointers to the current revisions,
                              hardware id with its source, route identity
                              status (quarantine), V4 identity status;
  - dji_v4_summaries       -- decoded V4 telemetry summary per revision:
                              channel presence, window gates, counter
                              endpoints (encoded vs zero-default), application
                              channels;
  - dji_area_calculations  -- append-only area resolver results: RAW,
                              controller delta, corrected recorded area,
                              status/method/confidence, anomaly flags,
                              structural screen, overlap, eligibility;
                              billing/customer columns NULL by contract;
  - dji_field_attributions -- append-only field tiers TIER1..TIER5 with
                              linked/holder land uuids and lineage evidence;
  - dji_land_snapshots     -- one row per catalog capture with completeness;
  - dji_land_revisions     -- immutable land metadata revisions;
  - dji_land_geometries    -- content-addressed geometry bytes (md5 + sha256).

[REASON]: eight tables, not one. Sources are RECEIVED DATA and exist whether
or not anything has been computed from them; a calculation is a CONCLUSION
under one named version of the rules and must be appendable without touching
a byte of the source; land snapshots are HISTORY that a mutable field
directory (field_contours) cannot represent.

Safe / idempotent (same contract as migrate_drones_useful_area_001.py):
  - refuses to run and exits with code 2 when instance/transport.db is
    absent (sqlite3.connect would otherwise CREATE an empty database);
  - CREATE TABLE / CREATE INDEX IF NOT EXISTS, so a second run over existing
    tables is a no-op even if the registry row were lost;
  - registered through migration_utils, skips itself on a re-run and prints
    'Already applied. Nothing to do.';
  - ONE transaction for everything: all tables, all indexes, the
    postconditions and the schema_migrations row land on a single commit;
    postconditions are verified BEFORE the registry row is written;
  - precondition: drone_flights must exist (the drones module is installed);
    its absence means the wrong database and exit code 1 with full rollback;
  - no Flask app context, stdlib sqlite3 only;
  - console output is ASCII only (NSSM/Windows log encoding).

Run (service must be STOPPED first to avoid SQLite write conflicts):

  cd C:\\transport-report-staging
  .\\nssm.exe stop TransportReportStaging
  copy instance\\transport.db instance\\transport.db.backup_YYYYMMDD
  & "C:\\Program Files\\Python314\\python.exe" migrate_dji_area_evidence_001.py
  .\\nssm.exe start TransportReportStaging

Rollback:
  Code rollback and data rollback are separate, and in that order. Reverting
  the DJI-AREA-EVIDENCE-001 commits leaves the eight tables in place; that is
  harmless -- nothing reads them once the models are gone, and
  db.create_all() does not drop tables. Dropping them is a manual step and is
  only correct AFTER the code revert:

    DROP TABLE dji_field_attributions;
    DROP TABLE dji_area_calculations;
    DROP TABLE dji_v4_summaries;
    DROP TABLE dji_flight_evidence;
    DROP TABLE dji_land_geometries;
    DROP TABLE dji_land_revisions;
    DROP TABLE dji_land_snapshots;
    DROP TABLE dji_source_revisions;
    DELETE FROM schema_migrations WHERE name = 'DJI_AREA_EVIDENCE_001';

  The content-addressed file store instance/dji_sources/ (source bodies
  larger than the inline threshold) is removed by hand after the DROPs.
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
MIGRATION_ID = 'DJI_AREA_EVIDENCE_001'
DESCRIPTION = ('DJI-AREA-EVIDENCE-001: create dji_source_revisions, '
               'dji_flight_evidence, dji_v4_summaries, dji_area_calculations, '
               'dji_field_attributions, dji_land_snapshots, '
               'dji_land_revisions, dji_land_geometries for the model '
               'dji-area-evidence-2026-09-08-final-1. Additive only; '
               'drone_flights.area_ha is neither read nor written.')

# [REASON]: column types are spelled exactly as SQLAlchemy renders the
# models (FLOAT, INTEGER, BIGINT, VARCHAR(n), TEXT, DATETIME, DATE, BOOLEAN,
# BLOB) so that a migrated database and a fresh db.create_all() database
# agree column for column; tests/test_dji_area_migration_001.py compares
# the two through PRAGMA table_info.

TABLES = (
    ('dji_source_revisions', """
    CREATE TABLE IF NOT EXISTS dji_source_revisions (
        id                   INTEGER PRIMARY KEY AUTOINCREMENT,
        provider_account_id  VARCHAR(80) NOT NULL,
        flight_id            BIGINT,
        scope_key            VARCHAR(40) NOT NULL,
        source_type          VARCHAR(30) NOT NULL,
        sha256               VARCHAR(64) NOT NULL,
        size_bytes           INTEGER NOT NULL,
        captured_at_utc      DATETIME NOT NULL,
        parser_version       VARCHAR(40),
        schema_version       VARCHAR(40),
        api_status           INTEGER,
        request_context_json TEXT,
        capture_run_id       VARCHAR(120),
        is_evidence_import   BOOLEAN NOT NULL DEFAULT 0,
        storage_kind         VARCHAR(20) NOT NULL,
        body_text            TEXT,
        body_path            VARCHAR(300),
        body_encoding        VARCHAR(20),
        received_at          DATETIME NOT NULL,
        last_seen_at         DATETIME,
        ingest_count         INTEGER NOT NULL DEFAULT 1,
        CONSTRAINT uq_dji_source_revisions_identity
            UNIQUE (source_type, scope_key, sha256)
    )"""),
    ('dji_flight_evidence', """
    CREATE TABLE IF NOT EXISTS dji_flight_evidence (
        id                        INTEGER PRIMARY KEY AUTOINCREMENT,
        flight_id                 BIGINT NOT NULL UNIQUE,
        provider_account_id       VARCHAR(80) NOT NULL,
        drone_flight_id           INTEGER REFERENCES drone_flights (id),
        hardware_id               VARCHAR(50),
        hardware_id_source        VARCHAR(20),
        list_revision_id          INTEGER,
        card_revision_id          INTEGER,
        route_revision_id         INTEGER,
        v4_revision_id            INTEGER,
        airlines_revision_id      INTEGER,
        list_raw_area_m2          FLOAT,
        card_raw_area_m2          FLOAT,
        route_area_m2             FLOAT,
        list_start_ts             INTEGER,
        list_end_ts               INTEGER,
        list_mode_name            INTEGER,
        list_manual_mode          BOOLEAN,
        list_spray_width          FLOAT,
        list_nickname             VARCHAR(100),
        card_mode_name            INTEGER,
        card_manual_mode          BOOLEAN,
        card_spray_width          FLOAT,
        card_geometry_md5         VARCHAR(80),
        card_start_ts             INTEGER,
        card_end_ts               INTEGER,
        card_app_version          VARCHAR(20),
        card_drone_type           VARCHAR(20),
        card_create_date          INTEGER,
        route_embedded_flight_id  BIGINT,
        route_identity_status     VARCHAR(30),
        route_hardware_id         VARCHAR(50),
        route_spray_width         FLOAT,
        route_point_count         INTEGER,
        route_points_with_field3  INTEGER,
        route_mode_name           INTEGER,
        route_start_ms            BIGINT,
        route_end_ms              BIGINT,
        route_session_no          VARCHAR(100),
        v4_identity_status        VARCHAR(30),
        v4_absent_reason          VARCHAR(40),
        updated_at                DATETIME NOT NULL
    )"""),
    ('dji_v4_summaries', """
    CREATE TABLE IF NOT EXISTS dji_v4_summaries (
        id                                INTEGER PRIMARY KEY AUTOINCREMENT,
        flight_id                         BIGINT NOT NULL,
        source_revision_id                INTEGER NOT NULL UNIQUE,
        parser_version                    VARCHAR(40) NOT NULL,
        computed_at                       DATETIME NOT NULL,
        frame_count                       INTEGER NOT NULL,
        top_count                         INTEGER,
        usage_type                        INTEGER,
        t_first_ms                        BIGINT,
        t_last_ms                         BIGINT,
        span_s                            FLOAT,
        start_offset_s                    FLOAT,
        end_offset_s                      FLOAT,
        dt_min_s                          FLOAT,
        dt_max_s                          FLOAT,
        dt_median_s                       FLOAT,
        nonpositive_dt_count              INTEGER,
        dt_over_limit_count               INTEGER,
        counter_present_frames            INTEGER NOT NULL DEFAULT 0,
        counter_first_encoded_native      FLOAT,
        counter_first_encoded_bits        VARCHAR(8),
        counter_last_encoded_native       FLOAT,
        counter_last_encoded_bits         VARCHAR(8),
        counter_first_encoded_at_ms       BIGINT,
        counter_last_encoded_at_ms        BIGINT,
        counter_leading_omitted_frames    INTEGER,
        counter_trailing_omitted_frames   INTEGER,
        counter_interior_omitted_frames   INTEGER,
        counter_negative_steps            INTEGER,
        counter_positive_steps            INTEGER,
        counter_max_jump_native           FLOAT,
        counter_max_drop_native           FLOAT,
        counter_observed_delta_native     FLOAT,
        counter_observed_delta_m2         FLOAT,
        counter_zero_default_delta_native FLOAT,
        counter_zero_default_delta_m2     FLOAT,
        counter_quantization_max_error    FLOAT,
        application_flag_frames           INTEGER,
        flow_positive_frames              INTEGER,
        application_frames                INTEGER,
        quantity_first                    FLOAT,
        quantity_last                     FLOAT,
        quantity_delta                    FLOAT,
        moving_application_frames         INTEGER,
        moving_application_distance_m     FLOAT,
        gps_jump_over_100m                INTEGER,
        frames_with_position              INTEGER,
        width_positive_frames             INTEGER,
        width_min                         FLOAT,
        width_max                         FLOAT,
        window_quality                    VARCHAR(30),
        baseline_status                   VARCHAR(30),
        window_reasons_json               TEXT,
        summary_json                      TEXT NOT NULL
    )"""),
    ('dji_area_calculations', """
    CREATE TABLE IF NOT EXISTS dji_area_calculations (
        id                            INTEGER PRIMARY KEY AUTOINCREMENT,
        flight_id                     BIGINT NOT NULL,
        provider_account_id           VARCHAR(80) NOT NULL,
        drone_flight_id               INTEGER,
        hardware_id                   VARCHAR(50),
        hardware_id_source            VARCHAR(20),
        area_algorithm_version        VARCHAR(80) NOT NULL,
        calculation_input_hash        VARCHAR(64) NOT NULL,
        calculated_at                 DATETIME NOT NULL,
        superseded_at                 DATETIME,
        supersede_reason              VARCHAR(80),
        start_at_utc                  DATETIME NOT NULL,
        end_at_utc                    DATETIME,
        report_timezone               VARCHAR(40) NOT NULL,
        report_start_date             DATE NOT NULL,
        raw_area_m2                   FLOAT,
        raw_area_source               VARCHAR(20),
        card_area_m2                  FLOAT,
        route_area_m2                 FLOAT,
        counter_observed_delta_m2     FLOAT,
        counter_zero_default_delta_m2 FLOAT,
        controller_delta_area_m2      FLOAT,
        counter_baseline_status       VARCHAR(30),
        counter_window_quality        VARCHAR(30),
        window_reasons_json           TEXT,
        corrected_recorded_area_m2    FLOAT,
        area_status                   VARCHAR(40) NOT NULL,
        evidence_status               VARCHAR(40),
        area_method                   VARCHAR(50),
        area_confidence               VARCHAR(10),
        anomaly_flags_json            TEXT,
        application_activity          VARCHAR(20),
        application_channel_quality   VARCHAR(20),
        application_evidence_kind     VARCHAR(20),
        application_without_area      BOOLEAN,
        structural_candidate          BOOLEAN,
        structural_rule_version       VARCHAR(60),
        candidate_base_flight_id      BIGINT,
        bridge_flight_ids_json        TEXT,
        boundary_gaps_json            TEXT,
        scalar_source_check           BOOLEAN,
        overlap_group_id              VARCHAR(60),
        aggregation_eligibility       VARCHAR(20) NOT NULL,
        v4_summary_id                 INTEGER,
        list_revision_id              INTEGER,
        card_revision_id              INTEGER,
        route_revision_id             INTEGER,
        v4_revision_id                INTEGER,
        unique_coverage_estimate_m2   FLOAT,
        coverage_domain_id            VARCHAR(60),
        coverage_method_version       VARCHAR(40),
        customer_id                   INTEGER,
        customer_mapping_id           INTEGER,
        customer_mapping_version      VARCHAR(40),
        billable_area_m2              FLOAT,
        billing_policy_version        VARCHAR(40),
        billing_approval_id           INTEGER,
        CONSTRAINT uq_dji_area_calculations_identity
            UNIQUE (flight_id, area_algorithm_version, calculation_input_hash)
    )"""),
    ('dji_field_attributions', """
    CREATE TABLE IF NOT EXISTS dji_field_attributions (
        id                            INTEGER PRIMARY KEY AUTOINCREMENT,
        flight_id                     BIGINT NOT NULL,
        field_resolver_version        VARCHAR(60) NOT NULL,
        field_input_hash              VARCHAR(64) NOT NULL,
        calculated_at                 DATETIME NOT NULL,
        superseded_at                 DATETIME,
        geometry_key_raw              VARCHAR(80),
        geometry_key_format           VARCHAR(20),
        geometry_md5                  VARCHAR(32),
        linked_land_uuid              VARCHAR(40),
        geometry_holder_land_uuid     VARCHAR(40),
        geometry_object_id            INTEGER,
        historical_geometry_available BOOLEAN NOT NULL DEFAULT 0,
        historical_geometry_sha256    VARCHAR(64),
        field_attribution_tier        VARCHAR(20) NOT NULL,
        field_attribution_method      VARCHAR(70),
        field_confidence              VARCHAR(10),
        field_land_uuid               VARCHAR(40),
        field_name_at_snapshot        VARCHAR(300),
        field_serial_number           VARCHAR(50),
        land_snapshot_id              INTEGER,
        land_revision_id              INTEGER,
        field_lineage_evidence_json   TEXT,
        holder_count                  INTEGER,
        candidate_count               INTEGER,
        warnings_json                 TEXT,
        tier4_inside_share            FLOAT,
        tier4_heuristic_version       VARCHAR(60),
        CONSTRAINT uq_dji_field_attributions_identity
            UNIQUE (flight_id, field_resolver_version, field_input_hash)
    )"""),
    ('dji_land_snapshots', """
    CREATE TABLE IF NOT EXISTS dji_land_snapshots (
        id                 INTEGER PRIMARY KEY AUTOINCREMENT,
        captured_at_utc    DATETIME NOT NULL,
        capture_run_id     VARCHAR(120),
        scope_json         TEXT,
        expected_count     INTEGER,
        received_count     INTEGER NOT NULL DEFAULT 0,
        complete           BOOLEAN,
        manifest_sha256    VARCHAR(64),
        is_evidence_import BOOLEAN NOT NULL DEFAULT 0,
        created_at         DATETIME NOT NULL
    )"""),
    ('dji_land_revisions', """
    CREATE TABLE IF NOT EXISTS dji_land_revisions (
        id                     INTEGER PRIMARY KEY AUTOINCREMENT,
        land_uuid              VARCHAR(40) NOT NULL,
        external_id            VARCHAR(120),
        serial_number          VARCHAR(50),
        name                   VARCHAR(300),
        total_area_raw         FLOAT,
        work_area_raw          FLOAT,
        obstacle_area_raw      FLOAT,
        area_unit              VARCHAR(10) NOT NULL DEFAULT 'mu',
        geometry_md5           VARCHAR(32),
        geometry_storage_uuid  VARCHAR(40),
        land_type              VARCHAR(40),
        created_at_source      DATETIME,
        updated_at_source      DATETIME,
        center_lat             FLOAT,
        center_lng             FLOAT,
        raw_json               TEXT NOT NULL,
        raw_sha256             VARCHAR(64) NOT NULL,
        first_seen_snapshot_id INTEGER NOT NULL,
        last_seen_snapshot_id  INTEGER NOT NULL,
        seen_count             INTEGER NOT NULL DEFAULT 1,
        CONSTRAINT uq_dji_land_revisions_identity
            UNIQUE (land_uuid, raw_sha256)
    )"""),
    ('dji_land_geometries', """
    CREATE TABLE IF NOT EXISTS dji_land_geometries (
        id                 INTEGER PRIMARY KEY AUTOINCREMENT,
        content_md5        VARCHAR(32) NOT NULL UNIQUE,
        sha256             VARCHAR(64) NOT NULL,
        size_bytes         INTEGER NOT NULL,
        md5_verified       BOOLEAN NOT NULL DEFAULT 0,
        body_blob          BLOB NOT NULL,
        parse_status       VARCHAR(20),
        ring_count         INTEGER,
        source_revision_id INTEGER,
        first_seen_at      DATETIME NOT NULL
    )"""),
)

INDEXES = (
    ('ix_dji_source_revisions_flight_type',
     'CREATE INDEX IF NOT EXISTS ix_dji_source_revisions_flight_type '
     'ON dji_source_revisions (flight_id, source_type)'),
    ('ix_dji_source_revisions_type_captured',
     'CREATE INDEX IF NOT EXISTS ix_dji_source_revisions_type_captured '
     'ON dji_source_revisions (source_type, captured_at_utc)'),
    ('ix_dji_source_revisions_run',
     'CREATE INDEX IF NOT EXISTS ix_dji_source_revisions_run '
     'ON dji_source_revisions (capture_run_id)'),
    ('ix_dji_flight_evidence_hardware_start',
     'CREATE INDEX IF NOT EXISTS ix_dji_flight_evidence_hardware_start '
     'ON dji_flight_evidence (hardware_id, card_start_ts)'),
    ('ix_dji_flight_evidence_drone_flight',
     'CREATE INDEX IF NOT EXISTS ix_dji_flight_evidence_drone_flight '
     'ON dji_flight_evidence (drone_flight_id)'),
    ('ix_dji_v4_summaries_flight',
     'CREATE INDEX IF NOT EXISTS ix_dji_v4_summaries_flight '
     'ON dji_v4_summaries (flight_id)'),
    ('ix_dji_area_calculations_report_date',
     'CREATE INDEX IF NOT EXISTS ix_dji_area_calculations_report_date '
     'ON dji_area_calculations (report_start_date)'),
    ('ix_dji_area_calculations_hw_date',
     'CREATE INDEX IF NOT EXISTS ix_dji_area_calculations_hw_date '
     'ON dji_area_calculations (hardware_id, report_start_date)'),
    ('ix_dji_area_calculations_status',
     'CREATE INDEX IF NOT EXISTS ix_dji_area_calculations_status '
     'ON dji_area_calculations (area_status)'),
    ('ix_dji_area_calculations_flight_current',
     'CREATE INDEX IF NOT EXISTS ix_dji_area_calculations_flight_current '
     'ON dji_area_calculations (flight_id, superseded_at)'),
    ('ix_dji_field_attributions_tier',
     'CREATE INDEX IF NOT EXISTS ix_dji_field_attributions_tier '
     'ON dji_field_attributions (field_attribution_tier)'),
    ('ix_dji_field_attributions_land',
     'CREATE INDEX IF NOT EXISTS ix_dji_field_attributions_land '
     'ON dji_field_attributions (field_land_uuid)'),
    ('ix_dji_field_attributions_md5',
     'CREATE INDEX IF NOT EXISTS ix_dji_field_attributions_md5 '
     'ON dji_field_attributions (geometry_md5)'),
    ('ix_dji_field_attributions_flight_current',
     'CREATE INDEX IF NOT EXISTS ix_dji_field_attributions_flight_current '
     'ON dji_field_attributions (flight_id, superseded_at)'),
    ('ix_dji_land_revisions_uuid',
     'CREATE INDEX IF NOT EXISTS ix_dji_land_revisions_uuid '
     'ON dji_land_revisions (land_uuid)'),
    ('ix_dji_land_revisions_md5',
     'CREATE INDEX IF NOT EXISTS ix_dji_land_revisions_md5 '
     'ON dji_land_revisions (geometry_md5)'),
    ('ix_dji_land_revisions_external',
     'CREATE INDEX IF NOT EXISTS ix_dji_land_revisions_external '
     'ON dji_land_revisions (external_id)'),
    ('ix_dji_land_geometries_sha256',
     'CREATE INDEX IF NOT EXISTS ix_dji_land_geometries_sha256 '
     'ON dji_land_geometries (sha256)'),
)

TABLE_NAMES = tuple(name for name, _ddl in TABLES)

# Tables the migration REFERENCES or relies on; absent -> wrong database.
PRECONDITION_TABLES = ('drone_flights',)


def _table_exists(cur, name):
    cur.execute("SELECT name FROM sqlite_master WHERE type='table' AND name=?",
                (name,))
    return cur.fetchone() is not None


def _index_exists(cur, name):
    cur.execute("SELECT name FROM sqlite_master WHERE type='index' AND name=?",
                (name,))
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
            if not stripped or stripped.startswith(('CREATE', ')', 'CONSTRAINT',
                                                    'UNIQUE')):
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
            if not _table_exists(cur, table):
                raise RuntimeError(
                    'precondition failed: table %s does not exist' % table)

        for _name, ddl in TABLES:
            cur.execute(ddl)
        for _name, statement in INDEXES:
            cur.execute(statement)

        # Postconditions before recording anything.
        expected = expected_columns()
        for table in TABLE_NAMES:
            if not _table_exists(cur, table):
                raise RuntimeError('postcondition failed: table %s missing'
                                   % table)
            columns = _column_names(cur, table)
            for name in expected[table]:
                if name not in columns:
                    raise RuntimeError('postcondition failed: column %s '
                                       'missing on %s' % (name, table))
        for name, _statement in INDEXES:
            if not _index_exists(cur, name):
                raise RuntimeError('postcondition failed: index %s missing'
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
    print('Done. %d tables (%d columns) and %d indexes are in place.'
          % (len(TABLE_NAMES), total_columns, len(INDEXES)))


if __name__ == '__main__':
    run()
