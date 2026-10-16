# -*- coding: utf-8 -*-
"""fin_recon/schema.py -- DDL таблиц FIN_RECON_STORAGE_001. Один источник.

Читают: миграция `migrate_fin_recon_storage_001.py` (создаёт), тесты
(сверяют колонки), инструмент восстановления (через миграцию). Модели ORM у
этих таблиц нет и не будет: `db.create_all()` создавал бы их пустыми на
базе, где миграция не применена, -- тот дрейф, от которого предостерегает
устав.

Неизменяемость держит база, а не обещание импортёра:

* UPDATE и DELETE запрещены триггерами на каждой таблице;
* после печати (`fin_recon_snapshot_seal`) в снимок нельзя дописать ни одной
  строки -- триггер вставки дочерних таблиц проверяет печать;
* снимок нельзя вставить с заполненными `owner_approved_at` или
  `financially_closed_at` -- утверждение не входит в этот макроэтап;
* версия N > 1 вставляется только со ссылкой на версию N-1 того же месяца и
  с её хешем содержимого; версия 1 -- без ссылки.

[REASON]: деньги и гектары -- TEXT с десятичной записью, а не REAL. REAL
округлил бы 1 234 567,89 до ближайшего двоичного числа, и сумма строк
перестала бы совпадать с итогом. Пустое значение -- NULL, и CHECK требует,
чтобы NULL стоял ровно тогда, когда доказательный тип UNKNOWN: ноль на месте
неизвестного не помещается в таблицу физически.
"""

from fin_recon import (ANOMALY_STATUSES, BLOCKS, DECISION_KINDS,
                       EVIDENCE_KINDS, PAYOUT_EVIDENCE, SALARY_STATUSES,
                       SEMANTIC_KINDS, SOURCE_ROLES, STATUSES, UNITS,
                       VS_VERDICTS)


def _in(values):
    return '(' + ', '.join("'%s'" % v for v in values) + ')'


def _unknown_is_null(value_col, evidence_col):
    return ("CHECK ((%s = 'UNKNOWN' AND %s IS NULL) OR "
            "(%s <> 'UNKNOWN' AND %s IS NOT NULL))"
            % (evidence_col, value_col, evidence_col, value_col))


_PAYOUT_PAIRS = ' OR '.join("(kind = '%s' AND evidence = '%s')" % pair
                            for pair in sorted(PAYOUT_EVIDENCE.items()))
_ANOMALY_PAIRS = ' OR '.join("(status = '%s' AND status_code = '%s')" % pair
                             for pair in sorted(ANOMALY_STATUSES.items()))

SNAPSHOT_TABLE = 'fin_recon_period_snapshot'
SEAL_TABLE = 'fin_recon_snapshot_seal'

TABLES = (
    (SNAPSHOT_TABLE, """
    CREATE TABLE IF NOT EXISTS fin_recon_period_snapshot (
        id                        INTEGER PRIMARY KEY AUTOINCREMENT,
        period                    VARCHAR(7) NOT NULL,
        version                   INTEGER NOT NULL CHECK (version >= 1),
        supersedes_id             INTEGER REFERENCES fin_recon_period_snapshot (id),
        supersedes_content_sha256 VARCHAR(64),
        revision_reason           TEXT,
        status                    VARCHAR(40) NOT NULL CHECK (status IN %(statuses)s),
        owner_approved_at         DATETIME,
        financially_closed_at     DATETIME,
        title                     TEXT NOT NULL,
        currency                  VARCHAR(3) NOT NULL,
        compiled_at               VARCHAR(40) NOT NULL,
        producer                  TEXT NOT NULL,
        normalization_version     TEXT NOT NULL,
        schema_version            VARCHAR(40) NOT NULL,
        header_json               TEXT NOT NULL,
        vs_json                   TEXT,
        content_sha256            VARCHAR(64) NOT NULL UNIQUE,
        snapshot_file_sha256      VARCHAR(64) NOT NULL,
        manifest_sha256           VARCHAR(64) NOT NULL,
        package_id                VARCHAR(80) NOT NULL,
        snapshot_json             TEXT NOT NULL,
        imported_at               VARCHAR(30) NOT NULL,
        imported_by               VARCHAR(200) NOT NULL,
        importer_version          VARCHAR(40) NOT NULL,
        CONSTRAINT uq_fin_recon_snapshot_period_version UNIQUE (period, version),
        CHECK (length(content_sha256) = 64),
        CHECK (period GLOB '[0-9][0-9][0-9][0-9]-[01][0-9]')
    )""" % {'statuses': _in(STATUSES)}),
    ('fin_recon_source', """
    CREATE TABLE IF NOT EXISTS fin_recon_source (
        id            INTEGER PRIMARY KEY AUTOINCREMENT,
        snapshot_id   INTEGER NOT NULL REFERENCES fin_recon_period_snapshot (id),
        position      INTEGER NOT NULL,
        source_key    VARCHAR(20) NOT NULL,
        role          VARCHAR(40) NOT NULL CHECK (role IN %(roles)s),
        name          TEXT NOT NULL,
        sha256        VARCHAR(64) CHECK (sha256 IS NULL OR length(sha256) = 64),
        bytes         INTEGER CHECK (bytes IS NULL OR bytes >= 0),
        container_key VARCHAR(20),
        member        TEXT,
        in_package    INTEGER NOT NULL CHECK (in_package IN (0, 1)),
        note          TEXT,
        CONSTRAINT uq_fin_recon_source_key UNIQUE (snapshot_id, source_key),
        CHECK (sha256 IS NOT NULL OR (role = 'EXTERNAL' AND in_package = 0))
    )""" % {'roles': _in(SOURCE_ROLES)}),
    ('fin_recon_metric', """
    CREATE TABLE IF NOT EXISTS fin_recon_metric (
        id            INTEGER PRIMARY KEY AUTOINCREMENT,
        snapshot_id   INTEGER NOT NULL REFERENCES fin_recon_period_snapshot (id),
        position      INTEGER NOT NULL,
        metric_key    VARCHAR(40) NOT NULL,
        group_key     VARCHAR(20) NOT NULL,
        label         TEXT NOT NULL,
        semantic      VARCHAR(30) NOT NULL CHECK (semantic IN %(semantic)s),
        unit          VARCHAR(10) NOT NULL CHECK (unit IN %(units)s),
        value         VARCHAR(40),
        evidence      VARCHAR(30) NOT NULL CHECK (evidence IN %(evidence)s),
        check_rule    VARCHAR(40),
        identity_json TEXT,
        note          TEXT,
        CONSTRAINT uq_fin_recon_metric_key UNIQUE (snapshot_id, metric_key),
        %(unknown)s
    )""" % {'semantic': _in(SEMANTIC_KINDS), 'units': _in(UNITS),
            'evidence': _in(EVIDENCE_KINDS),
            'unknown': _unknown_is_null('value', 'evidence')}),
    ('fin_recon_operator_row', """
    CREATE TABLE IF NOT EXISTS fin_recon_operator_row (
        id                   INTEGER PRIMARY KEY AUTOINCREMENT,
        snapshot_id          INTEGER NOT NULL REFERENCES fin_recon_period_snapshot (id),
        position             INTEGER NOT NULL,
        operator_key         VARCHAR(20) NOT NULL,
        display_name         TEXT NOT NULL,
        organization         TEXT,
        area_total_ha        VARCHAR(30) NOT NULL,
        area_breakdown_json  TEXT NOT NULL,
        accrued_amount       VARCHAR(40),
        accrued_evidence     VARCHAR(30) NOT NULL CHECK (accrued_evidence IN %(evidence)s),
        accrued_source_value TEXT,
        accrued_ref_json     TEXT,
        recognized_total     VARCHAR(40) NOT NULL,
        difference_amount    VARCHAR(40),
        basis                TEXT NOT NULL,
        limitations          TEXT,
        CONSTRAINT uq_fin_recon_operator_key UNIQUE (snapshot_id, operator_key),
        %(unknown)s,
        CHECK ((accrued_amount IS NULL AND difference_amount IS NULL) OR
               (accrued_amount IS NOT NULL AND difference_amount IS NOT NULL))
    )""" % {'evidence': _in(EVIDENCE_KINDS),
            'unknown': _unknown_is_null('accrued_amount', 'accrued_evidence')}),
    ('fin_recon_payout', """
    CREATE TABLE IF NOT EXISTS fin_recon_payout (
        id           INTEGER PRIMARY KEY AUTOINCREMENT,
        snapshot_id  INTEGER NOT NULL REFERENCES fin_recon_period_snapshot (id),
        position     INTEGER NOT NULL,
        payout_key   VARCHAR(20) NOT NULL,
        operator_key VARCHAR(20) NOT NULL,
        kind         VARCHAR(30) NOT NULL,
        amount       VARCHAR(40) NOT NULL,
        evidence     VARCHAR(30) NOT NULL,
        record_date  VARCHAR(10),
        text         TEXT,
        ref_json     TEXT,
        decision_key VARCHAR(20),
        note         TEXT,
        CONSTRAINT uq_fin_recon_payout_key UNIQUE (snapshot_id, payout_key),
        CHECK (%(pairs)s)
    )""" % {'pairs': _PAYOUT_PAIRS}),
    ('fin_recon_work_row', """
    CREATE TABLE IF NOT EXISTS fin_recon_work_row (
        id               INTEGER PRIMARY KEY AUTOINCREMENT,
        snapshot_id      INTEGER NOT NULL REFERENCES fin_recon_period_snapshot (id),
        position         INTEGER NOT NULL,
        work_key         VARCHAR(20) NOT NULL,
        operator_key     VARCHAR(20) NOT NULL,
        source_key       VARCHAR(20) NOT NULL,
        sheet            TEXT NOT NULL,
        row_number       INTEGER NOT NULL CHECK (row_number >= 1),
        part             TEXT,
        refs_json        TEXT NOT NULL,
        farm             TEXT,
        block            VARCHAR(10) NOT NULL CHECK (block IN %(blocks)s),
        block_label      TEXT NOT NULL,
        area_ha          VARCHAR(30) NOT NULL,
        area_evidence    VARCHAR(30) NOT NULL CHECK (area_evidence IN ('BOOK_NUMBER', 'BOOK_FORMULA')),
        area_source_value TEXT,
        date_text        TEXT,
        work_dates_json  TEXT NOT NULL,
        work_date_from   VARCHAR(10),
        work_date_to     VARCHAR(10),
        work_month       VARCHAR(7),
        ledger_month     VARCHAR(7) NOT NULL,
        salary_status    VARCHAR(30) NOT NULL CHECK (salary_status IN %(salary)s),
        vs_verdict       VARCHAR(20) CHECK (vs_verdict IS NULL OR vs_verdict IN %(vs)s),
        vs_verdict_label TEXT,
        vs_db_rows       INTEGER CHECK (vs_db_rows IS NULL OR vs_db_rows >= 0),
        vs_db_area_ha    VARCHAR(30),
        vs_note          TEXT,
        anomalies_json   TEXT NOT NULL,
        CONSTRAINT uq_fin_recon_work_key UNIQUE (snapshot_id, work_key)
    )""" % {'blocks': _in(BLOCKS), 'salary': _in(SALARY_STATUSES),
            'vs': _in(VS_VERDICTS)}),
    ('fin_recon_anomaly', """
    CREATE TABLE IF NOT EXISTS fin_recon_anomaly (
        id             INTEGER PRIMARY KEY AUTOINCREMENT,
        snapshot_id    INTEGER NOT NULL REFERENCES fin_recon_period_snapshot (id),
        position       INTEGER NOT NULL,
        anomaly_key    VARCHAR(20) NOT NULL,
        priority       VARCHAR(2) NOT NULL CHECK (priority IN ('P0', 'P1', 'P2')),
        status         TEXT NOT NULL,
        status_code    VARCHAR(20) NOT NULL,
        group_label    TEXT,
        finding        TEXT NOT NULL,
        rule           TEXT NOT NULL,
        operators_json TEXT NOT NULL,
        source_address TEXT,
        CONSTRAINT uq_fin_recon_anomaly_key UNIQUE (snapshot_id, anomaly_key),
        CHECK (%(pairs)s)
    )""" % {'pairs': _ANOMALY_PAIRS}),
    ('fin_recon_owner_decision', """
    CREATE TABLE IF NOT EXISTS fin_recon_owner_decision (
        id             INTEGER PRIMARY KEY AUTOINCREMENT,
        snapshot_id    INTEGER NOT NULL REFERENCES fin_recon_period_snapshot (id),
        position       INTEGER NOT NULL,
        decision_key   VARCHAR(20) NOT NULL,
        kind           VARCHAR(20) NOT NULL CHECK (kind IN %(kinds)s),
        topic          TEXT NOT NULL,
        rule           TEXT NOT NULL,
        confirmation   TEXT,
        decided_on     VARCHAR(10),
        detail         TEXT,
        source_key     VARCHAR(20),
        anomalies_json TEXT NOT NULL,
        values_json    TEXT NOT NULL,
        CONSTRAINT uq_fin_recon_decision_key UNIQUE (snapshot_id, decision_key)
    )""" % {'kinds': _in(DECISION_KINDS)}),
    ('fin_recon_cross_period_link', """
    CREATE TABLE IF NOT EXISTS fin_recon_cross_period_link (
        id                 INTEGER PRIMARY KEY AUTOINCREMENT,
        snapshot_id        INTEGER NOT NULL REFERENCES fin_recon_period_snapshot (id),
        position           INTEGER NOT NULL,
        link_key           VARCHAR(20) NOT NULL,
        work_key           VARCHAR(20) NOT NULL,
        work_month         VARCHAR(7) NOT NULL,
        ledger_month       VARCHAR(7) NOT NULL,
        accrual_month      VARCHAR(7),
        area_ha            VARCHAR(30) NOT NULL,
        salary_amount      VARCHAR(40),
        salary_evidence    VARCHAR(30) NOT NULL CHECK (salary_evidence IN %(evidence)s),
        payout_record_date VARCHAR(10),
        anomalies_json     TEXT NOT NULL,
        policy             TEXT NOT NULL,
        note               TEXT,
        CONSTRAINT uq_fin_recon_link_key UNIQUE (snapshot_id, link_key),
        CHECK (work_month <> ledger_month),
        %(unknown)s
    )""" % {'evidence': _in(EVIDENCE_KINDS),
            'unknown': _unknown_is_null('salary_amount', 'salary_evidence')}),
    ('fin_recon_dji_snapshot', """
    CREATE TABLE IF NOT EXISTS fin_recon_dji_snapshot (
        id                       INTEGER PRIMARY KEY AUTOINCREMENT,
        snapshot_id              INTEGER NOT NULL UNIQUE REFERENCES fin_recon_period_snapshot (id),
        period_from              VARCHAR(10) NOT NULL,
        period_to                VARCHAR(10) NOT NULL,
        captured_at              VARCHAR(40) NOT NULL,
        source_key               VARCHAR(20) NOT NULL,
        flights                  INTEGER NOT NULL CHECK (flights >= 0),
        raw_ha                   VARCHAR(30) NOT NULL,
        accepted_ha              VARCHAR(30) NOT NULL,
        excluded_ha              VARCHAR(30) NOT NULL,
        excluded_auto_ha         VARCHAR(30) NOT NULL,
        excluded_admin_ha        VARCHAR(30) NOT NULL,
        open_review              INTEGER NOT NULL CHECK (open_review >= 0),
        pending_v4               INTEGER NOT NULL CHECK (pending_v4 >= 0),
        unattributed_ha          VARCHAR(30),
        unattributed_flights     INTEGER,
        single_candidate_ha      VARCHAR(30),
        single_candidate_flights INTEGER,
        multi_candidate_ha       VARCHAR(30),
        multi_candidate_flights  INTEGER,
        attribution_source_key   VARCHAR(20),
        algorithm_versions_json  TEXT NOT NULL,
        decisions_json           TEXT NOT NULL,
        semantics_note           TEXT NOT NULL,
        note                     TEXT
    )"""),
    (SEAL_TABLE, """
    CREATE TABLE IF NOT EXISTS fin_recon_snapshot_seal (
        snapshot_id     INTEGER PRIMARY KEY REFERENCES fin_recon_period_snapshot (id),
        sealed_at       VARCHAR(30) NOT NULL,
        content_sha256  VARCHAR(64) NOT NULL,
        row_counts_json TEXT NOT NULL
    )"""),
)

TABLE_NAMES = tuple(name for name, _ddl in TABLES)
CHILD_TABLES = tuple(name for name in TABLE_NAMES
                     if name not in (SNAPSHOT_TABLE, SEAL_TABLE))

INDEXES = (
    ('ux_fin_recon_work_source',
     'CREATE UNIQUE INDEX IF NOT EXISTS ux_fin_recon_work_source ON '
     "fin_recon_work_row (snapshot_id, source_key, sheet, row_number, "
     "COALESCE(part, ''))"),
    ('ix_fin_recon_work_month',
     'CREATE INDEX IF NOT EXISTS ix_fin_recon_work_month ON '
     'fin_recon_work_row (work_month)'),
    ('ix_fin_recon_link_month',
     'CREATE INDEX IF NOT EXISTS ix_fin_recon_link_month ON '
     'fin_recon_cross_period_link (work_month)'),
    ('ix_fin_recon_snapshot_period',
     'CREATE INDEX IF NOT EXISTS ix_fin_recon_snapshot_period ON '
     'fin_recon_period_snapshot (period, version)'),
)


def _immutable(table):
    return (
        ('trg_%s_no_update' % table,
         'CREATE TRIGGER IF NOT EXISTS trg_%s_no_update BEFORE UPDATE ON %s '
         "BEGIN SELECT RAISE(ABORT, '%s is immutable (FIN_RECON_STORAGE_001)'); "
         'END' % (table, table, table)),
        ('trg_%s_no_delete' % table,
         'CREATE TRIGGER IF NOT EXISTS trg_%s_no_delete BEFORE DELETE ON %s '
         "BEGIN SELECT RAISE(ABORT, '%s is immutable (FIN_RECON_STORAGE_001)'); "
         'END' % (table, table, table)),
    )


def _sealed_insert(table):
    return (
        'trg_%s_sealed' % table,
        'CREATE TRIGGER IF NOT EXISTS trg_%s_sealed BEFORE INSERT ON %s '
        'WHEN EXISTS (SELECT 1 FROM fin_recon_snapshot_seal s '
        'WHERE s.snapshot_id = NEW.snapshot_id) '
        "BEGIN SELECT RAISE(ABORT, 'snapshot is sealed: rows cannot be added "
        "(FIN_RECON_STORAGE_001)'); END" % (table, table))


TRIGGERS = tuple(
    trigger for table in TABLE_NAMES for trigger in _immutable(table)
) + tuple(_sealed_insert(table) for table in CHILD_TABLES) + (
    # [REASON]: утверждение и финансовое закрытие -- отдельное решение
    # владельца, которого нет. Будущий механизм утверждения -- отдельная
    # append-only запись со ссылкой на снимок, а не правка этих полей.
    ('trg_fin_recon_snapshot_no_approval',
     'CREATE TRIGGER IF NOT EXISTS trg_fin_recon_snapshot_no_approval '
     'BEFORE INSERT ON fin_recon_period_snapshot '
     'WHEN NEW.owner_approved_at IS NOT NULL '
     'OR NEW.financially_closed_at IS NOT NULL '
     "BEGIN SELECT RAISE(ABORT, 'approval and financial close are not part "
     "of FIN_RECON_STORAGE_001'); END"),
    # [REASON]: новая редакция не переписывает старую, а ссылается на неё --
    # на ту самую версию N-1 этого месяца и на её хеш. Иначе v2 могла бы
    # «заменить» чужой месяц или v1 с другим содержимым.
    ('trg_fin_recon_snapshot_version_chain',
     'CREATE TRIGGER IF NOT EXISTS trg_fin_recon_snapshot_version_chain '
     'BEFORE INSERT ON fin_recon_period_snapshot '
     'WHEN (NEW.version = 1 AND (NEW.supersedes_id IS NOT NULL '
     'OR NEW.supersedes_content_sha256 IS NOT NULL)) '
     'OR (NEW.version > 1 AND NOT EXISTS (SELECT 1 FROM '
     'fin_recon_period_snapshot p WHERE p.id = NEW.supersedes_id '
     'AND p.period = NEW.period AND p.version = NEW.version - 1 '
     'AND p.content_sha256 = NEW.supersedes_content_sha256)) '
     "BEGIN SELECT RAISE(ABORT, 'version chain broken: version N>1 must "
     "supersede version N-1 of the same period by its content hash'); END"),
    # [REASON]: печать ставится на снимок, у которого ещё нет печати, и с его
    # же хешем -- одна печать на снимок, ничья чужая.
    ('trg_fin_recon_seal_matches',
     'CREATE TRIGGER IF NOT EXISTS trg_fin_recon_seal_matches '
     'BEFORE INSERT ON fin_recon_snapshot_seal '
     'WHEN NOT EXISTS (SELECT 1 FROM fin_recon_period_snapshot p '
     'WHERE p.id = NEW.snapshot_id AND p.content_sha256 = NEW.content_sha256) '
     "BEGIN SELECT RAISE(ABORT, 'seal does not match its snapshot'); END"),
)

TRIGGER_NAMES = tuple(name for name, _ddl in TRIGGERS)
INDEX_NAMES = tuple(name for name, _ddl in INDEXES)


def expected_columns():
    """{table: [column, ...]} из DDL выше -- один источник правды."""
    out = {}
    for name, ddl in TABLES:
        cols = []
        for line in ddl.splitlines():
            stripped = line.strip()
            if not stripped or stripped.startswith(
                    ('CREATE', ')', 'CONSTRAINT', 'UNIQUE', 'CHECK', '(',
                     'OR ', '%')):
                continue
            first = stripped.split()[0]
            if first.isidentifier():
                cols.append(first)
        out[name] = cols
    return out


def missing_tables(con):
    """Имена таблиц FIN_RECON_STORAGE_001, которых в базе нет."""
    present = {row[0] for row in con.execute(
        "SELECT name FROM sqlite_master WHERE type = 'table'")}
    return [name for name in TABLE_NAMES if name not in present]


def present_objects(con):
    """Таблицы, индексы и триггеры fin_recon, которые уже есть в базе."""
    rows = con.execute(
        "SELECT type, name FROM sqlite_master WHERE name LIKE 'fin_recon_%' "
        "OR name LIKE 'trg_fin_recon_%' OR name LIKE 'ix_fin_recon_%' "
        "OR name LIKE 'ux_fin_recon_%'").fetchall()
    return sorted((row[0], row[1]) for row in rows)
