# -*- coding: utf-8 -*-
"""dji_area/store.py -- хранение доказательств DJI на stdlib sqlite3.

ЕДИНСТВЕННЫЙ писатель таблиц `dji_*`: его вызывают и приёмник Flask
(на своём sqlite3-соединении к той же базе, как `works_import_apply`
вызывает `import_drone_works.apply_rows`), и инструменты импорта/пересчёта.
ORM-модели в `models.py` служат чтению страниц и `db.create_all()` на
свежей установке; второго писателя, способного разойтись с первым, нет.

Тела источников: маленькие (list-запись, карточка, airlines, маршрут) --
inline в `body_text`; большие (V4, 100–700 КБ) -- в content-addressed
файловом хранилище `instance/dji_sources/<sha[:2]>/<sha>.gz` рядом с базой,
чтобы резервная копия каталога `instance` уносила и их. SHA256 всегда
считается по ИСХОДНЫМ байтам, а не по сжатым.

Ни один метод не переписывает принятую ревизию и не удаляет строку.
"""

import gzip
import json
import os
import sqlite3
import tempfile
import zlib
from datetime import datetime

from dji_area import (AREA_ALGORITHM_VERSION, FIELD_RESOLVER_VERSION,
                      V4_PARSER_VERSION)
from dji_area import evidence as ev
from dji_area.hashing import canonical_json

SOURCE_DIR_NAME = 'dji_sources'
# Тела до этого размера лежат inline (TEXT). Маршруты и карточки помещаются;
# V4 всегда уходит в файл.
INLINE_MAX_BYTES = 64 * 1024
ENCODING_RAW = 'raw'
ENCODING_GZIP = 'gzip'
STORAGE_INLINE = 'inline'
STORAGE_FILE = 'file'

SCOPE_CATALOG = 'catalog'


class StoreError(RuntimeError):
    pass


def utcnow():
    return datetime.utcnow().replace(microsecond=0)


def iso(dt):
    return dt.isoformat(sep=' ') if dt is not None else None


# ─── Соединение ──────────────────────────────────────────────────────────────

def connect(db_path, timeout_s=30):
    """Открыть СУЩЕСТВУЮЩУЮ базу. Отсутствие -- отказ, файл не создаётся."""
    if not os.path.exists(db_path):
        raise StoreError('database not found at %s - refusing to run' % db_path)
    # [REASON]: isolation_level=None отключает неявные BEGIN модуля sqlite3;
    # транзакциями управляют явные BEGIN/COMMIT писателей. Иначе первый
    # INSERT открывал бы транзакцию молча, а явный BEGIN после него падал бы
    # с «cannot start a transaction within a transaction».
    con = sqlite3.connect(db_path, timeout=timeout_s, isolation_level=None)
    con.row_factory = sqlite3.Row
    con.execute('PRAGMA busy_timeout=%d' % int(timeout_s * 1000))
    return con


def begin_immediate(con):
    """Открыть транзакцию писателя. Только ``BEGIN IMMEDIATE``.

    [REASON]: у всех писателей `dji_*` первый оператор -- SELECT (поиск уже
    принятой ревизии, чтение сводки V4). При отложенном ``BEGIN`` снимок
    чтения берётся на этом SELECT, и если другое соединение (та же
    transport.db обслуживает всё приложение) успеет закоммитить раньше первой
    записи, SQLite отвечает SQLITE_BUSY_SNAPSHOT и НЕ вызывает обработчик
    занятости: ``PRAGMA busy_timeout`` в такой ситуации не действует, и
    тридцатисекундное ожидание превращается в мгновенный отказ. Немедленная
    транзакция берёт блокировку писателя сразу, и busy_timeout снова работает.
    """
    con.execute('BEGIN IMMEDIATE')


def require_tables(con):
    names = {r[0] for r in con.execute(
        "SELECT name FROM sqlite_master WHERE type='table'")}
    for table in ('dji_source_revisions', 'dji_flight_evidence',
                  'dji_v4_summaries', 'dji_area_calculations',
                  'dji_field_attributions', 'dji_land_snapshots',
                  'dji_land_revisions', 'dji_land_geometries'):
        if table not in names:
            raise StoreError('table %s is missing - run '
                             'migrate_dji_area_evidence_001.py first' % table)


def source_root(db_path):
    return os.path.join(os.path.dirname(os.path.abspath(db_path)),
                        SOURCE_DIR_NAME)


# ─── Файловое хранилище ─────────────────────────────────────────────────────

def body_relpath(sha256, encoding):
    suffix = '.gz' if encoding == ENCODING_GZIP else '.bin'
    return '%s/%s%s' % (sha256[:2], sha256, suffix)


def write_body_file(root, sha256, body, encoding=ENCODING_GZIP):
    """Атомарно положить тело в хранилище. Существующий файл не трогается."""
    rel = body_relpath(sha256, encoding)
    target = os.path.join(root, rel.replace('/', os.sep))
    if os.path.exists(target):
        return rel
    os.makedirs(os.path.dirname(target), exist_ok=True)
    payload = gzip.compress(bytes(body), mtime=0) \
        if encoding == ENCODING_GZIP else bytes(body)
    fd, tmp = tempfile.mkstemp(prefix='.tmp-', dir=os.path.dirname(target))
    try:
        with os.fdopen(fd, 'wb') as fh:
            fh.write(payload)
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(tmp, target)
    finally:
        if os.path.exists(tmp):
            os.unlink(tmp)
    return rel


def read_body(root, row):
    """Байты ревизии: inline или из файла. Проверяет SHA256 при чтении."""
    if row['storage_kind'] == STORAGE_INLINE:
        data = (row['body_text'] or '').encode('utf-8')
    else:
        path = os.path.join(root, (row['body_path'] or '').replace('/', os.sep))
        # [REASON]: файловое хранилище живёт РЯДОМ с базой и восстанавливается
        # отдельно: backup_transport_db.py копирует только transport.db. База,
        # поднятая из бэкапа, законно встречает отсутствующее или битое тело.
        # Каждый вызывающий защищён от StoreError и только от неё; голый
        # OSError уходил в общий except приёмника и откатывал весь пакет из
        # полусотни исправных источников. Читатель не чинит файл -- он обязан
        # лишь сказать «тела нет» на языке модуля.
        try:
            with open(path, 'rb') as fh:
                data = fh.read()
            if row['body_encoding'] == ENCODING_GZIP:
                data = gzip.decompress(data)
        except (OSError, EOFError, zlib.error) as exc:
            raise StoreError('stored body of revision %s is unreadable (%s)'
                             % (row['id'], type(exc).__name__))
    if ev.sha256_bytes(data) != row['sha256']:
        raise StoreError('stored body of revision %s does not match its '
                         'sha256' % row['id'])
    return data


# ─── Ревизии источников ─────────────────────────────────────────────────────

def _scope_key(flight_id):
    return str(int(flight_id)) if flight_id is not None else SCOPE_CATALOG


def upsert_source_revision(con, root, source_type, body, flight_id=None,
                           provider=ev.PROVIDER_ACCOUNT_DEFAULT,
                           captured_at_utc=None, request_context=None,
                           capture_run_id=None, parser_version=None,
                           schema_version=None, api_status=None,
                           is_evidence_import=False, now=None,
                           inline_max_bytes=INLINE_MAX_BYTES):
    """Сохранить тело как ревизию. Те же байты -> та же строка (ingest_count+1).

    Возвращает (revision_id, created). Тело с маркером секрета отклоняется
    StoreError -- подписанные URL в базу не попадают.
    """
    if source_type not in ev.SOURCE_TYPES:
        raise StoreError('unknown source_type %r' % (source_type,))
    body = bytes(body)
    if ev.contains_secret_marker(body):
        raise StoreError('body of %s carries a secret marker; refused'
                         % source_type)
    # [REASON]: request_context -- единственное поле, которое ПО НАЗНАЧЕНИЮ
    # хранит URL, и именно оно оставалось без проверки, пока тело проверялось.
    # Сборщик срезает query до отправки, но гарантия «подписанный URL не
    # попадает в базу» принадлежит приёмнику, а не отправителю: иначе клиент
    # другой версии или ручной POST кладёт живой ключ в таблицу и в бэкап.
    # Значение маркера не печатается -- только имя поля.
    if request_context is not None and ev.contains_secret_marker(
            canonical_json(request_context)):
        raise StoreError('request_context of %s carries a secret marker; '
                         'refused' % source_type)
    sha = ev.sha256_bytes(body)
    scope = _scope_key(flight_id)
    now = now or utcnow()
    existing = con.execute(
        'SELECT id, ingest_count FROM dji_source_revisions WHERE '
        'source_type=? AND scope_key=? AND sha256=?',
        (source_type, scope, sha)).fetchone()
    if existing is not None:
        con.execute('UPDATE dji_source_revisions SET ingest_count=?, '
                    'last_seen_at=? WHERE id=?',
                    ((existing['ingest_count'] or 1) + 1, iso(now),
                     existing['id']))
        return existing['id'], False

    if len(body) <= inline_max_bytes and _is_text(body):
        storage_kind, body_text, body_path, encoding = (
            STORAGE_INLINE, body.decode('utf-8'), None, ENCODING_RAW)
    else:
        rel = write_body_file(root, sha, body, ENCODING_GZIP)
        storage_kind, body_text, body_path, encoding = (
            STORAGE_FILE, None, rel, ENCODING_GZIP)

    cur = con.execute(
        'INSERT INTO dji_source_revisions (provider_account_id, flight_id, '
        'scope_key, source_type, sha256, size_bytes, captured_at_utc, '
        'parser_version, schema_version, api_status, request_context_json, '
        'capture_run_id, is_evidence_import, storage_kind, body_text, '
        'body_path, body_encoding, received_at, last_seen_at, ingest_count) '
        'VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,1)',
        (provider, int(flight_id) if flight_id is not None else None, scope,
         source_type, sha, len(body), iso(captured_at_utc or now),
         parser_version, schema_version, api_status,
         canonical_json(request_context) if request_context else None,
         capture_run_id, 1 if is_evidence_import else 0, storage_kind,
         body_text, body_path, encoding, iso(now), iso(now)))
    return cur.lastrowid, True


def _is_text(body):
    try:
        body.decode('utf-8')
    except UnicodeDecodeError:
        return False
    return b'\x00' not in body


def latest_revisions(con, flight_id):
    """{source_type: row} -- последняя ревизия каждого типа для вылета."""
    rows = con.execute(
        'SELECT * FROM dji_source_revisions WHERE flight_id=? '
        'ORDER BY captured_at_utc, id', (int(flight_id),)).fetchall()
    out = {}
    for row in rows:
        out[row['source_type']] = row
    return out


def revision_by_id(con, revision_id):
    return con.execute('SELECT * FROM dji_source_revisions WHERE id=?',
                       (revision_id,)).fetchone()


# ─── Указатели доказательств вылета ─────────────────────────────────────────

EVIDENCE_COLUMNS = (
    'flight_id', 'provider_account_id', 'drone_flight_id', 'hardware_id',
    'hardware_id_source', 'list_revision_id', 'card_revision_id',
    'route_revision_id', 'v4_revision_id', 'airlines_revision_id',
    'list_raw_area_m2', 'card_raw_area_m2', 'route_area_m2', 'list_start_ts',
    'list_end_ts', 'list_mode_name', 'list_manual_mode', 'list_spray_width',
    'list_nickname', 'card_mode_name', 'card_manual_mode', 'card_spray_width',
    'card_geometry_md5', 'card_start_ts', 'card_end_ts', 'card_app_version',
    'card_drone_type', 'card_create_date', 'route_embedded_flight_id',
    'route_identity_status', 'route_hardware_id', 'route_spray_width',
    'route_point_count', 'route_points_with_field3', 'route_mode_name',
    'route_start_ms', 'route_end_ms', 'route_session_no',
    'v4_identity_status', 'v4_absent_reason', 'updated_at',
)


def refresh_flight_evidence(con, root, flight_id,
                            provider=ev.PROVIDER_ACCOUNT_DEFAULT, now=None):
    """Пересобрать строку `dji_flight_evidence` из последних ревизий.

    Разбор карточки/маршрута/list идёт через `dji_area.evidence`; список
    берётся из ревизии 'list', а при её отсутствии -- из
    `drone_flights.raw_json` (то, что приёмник вылетов уже хранит).
    """
    now = now or utcnow()
    flight_id = int(flight_id)
    revs = latest_revisions(con, flight_id)
    row = {name: None for name in EVIDENCE_COLUMNS}
    row['flight_id'] = flight_id
    row['provider_account_id'] = provider
    row['updated_at'] = iso(now)

    flight = con.execute(
        'SELECT id, raw_json FROM drone_flights WHERE dji_flight_id=?',
        (flight_id,)).fetchone()
    if flight is not None:
        row['drone_flight_id'] = flight['id']

    list_rec = None
    if 'list' in revs:
        row['list_revision_id'] = revs['list']['id']
        try:
            # [REASON]: тело ревизии бывает двух форм -- одна запись от
            # форензик-импорта и целая страница ответа DJI от живого
            # захвата (A13). Выбор записи по id живёт в evidence.py и
            # отказывает, если записи этого вылета в теле нет.
            list_rec = ev.select_list_record(
                json.loads(read_body(root, revs['list']).decode('utf-8')),
                flight_id)
        except (ValueError, StoreError):
            list_rec = None
    elif flight is not None and flight['raw_json']:
        # [REASON]: ИЗМЕНЯЕМЫЙ запасной путь. Колонку можно переписать позже,
        # поэтому поля отсюда доказательством не считаются: pipeline.py
        # помечает такую запись `LIST_FROM_MUTABLE_RAW_JSON`. Путь оставлен
        # для исторических вылетов, у которых живого списка никогда не
        # захватывали -- выдумывать им неизменяемый источник нельзя.
        try:
            list_rec = ev.parse_list_record(json.loads(flight['raw_json']))
        except ValueError:
            list_rec = None
    if list_rec:
        row['list_raw_area_m2'] = list_rec['raw_area_m2']
        row['list_start_ts'] = list_rec['start_ts']
        row['list_end_ts'] = list_rec['end_ts']
        row['list_mode_name'] = list_rec['mode_name']
        row['list_manual_mode'] = _bool(list_rec['manual_mode'])
        row['list_spray_width'] = list_rec['spray_width']
        row['list_nickname'] = list_rec['nickname']

    card = None
    if 'card' in revs:
        row['card_revision_id'] = revs['card']['id']
        try:
            card = ev.parse_card_body(read_body(root, revs['card']))
        except (ValueError, StoreError):
            card = None
    if card:
        row['card_raw_area_m2'] = card['raw_area_m2']
        row['card_mode_name'] = card['mode_name']
        row['card_manual_mode'] = _bool(card['manual_mode'])
        row['card_spray_width'] = card['spray_width']
        row['card_geometry_md5'] = card['geometry_md5']
        row['card_start_ts'] = card['start_ts']
        row['card_end_ts'] = card['end_ts']
        row['card_app_version'] = card['app_version']
        row['card_drone_type'] = card['drone_type']
        row['card_create_date'] = card['create_date']

    route = None
    if 'route' in revs:
        row['route_revision_id'] = revs['route']['id']
        try:
            route = ev.parse_route_body(read_body(root, revs['route']),
                                        flight_id)
        except StoreError:
            route = None
    if route:
        row['route_identity_status'] = route['identity_status']
        row['route_embedded_flight_id'] = route['embedded_flight_id']
        # [REASON]: скаляры маршрута записываются и при ROUTE_IDENTITY_ERROR --
        # намеренно. Спецификация §4 запрещает ИСПОЛЬЗОВАТЬ такой маршрут, а не
        # наблюдать его, и каждое запрещённое применение закрыто отдельно:
        # площадь берётся только из list/card, `hardware_for` пропускает лишь
        # ROUTE_OK, TIER4 и разбор точек требуют route_identity_ok. Строка
        # расчёта несёт рядом флаг ROUTE_IDENTITY_ERROR, по которому пакет
        # ручной сверки и отбирает класс ROUTE_QUARANTINED. Стереть скаляры
        # значило бы потерять то самое доказательство подмены, которым найден
        # дефект прежнего захвата: чужую площадь видно только рядом со своей.
        row['route_hardware_id'] = route['hardware_id']
        row['route_area_m2'] = route['area_m2']
        row['route_spray_width'] = route['spray_width']
        row['route_point_count'] = route['point_count']
        row['route_points_with_field3'] = route['points_with_field3']
        row['route_mode_name'] = route['mode_name']
        row['route_start_ms'] = route['start_ms']
        row['route_end_ms'] = route['end_ms']
        row['route_session_no'] = route['session_no']

    if 'airlines' in revs:
        row['airlines_revision_id'] = revs['airlines']['id']
        if 'v4' not in revs:
            try:
                text = read_body(root, revs['airlines']).decode('utf-8')
                compact = text.replace(' ', '')
                # [REASON]: ключ здесь -- `file_v4_url_path`, а НЕ
                # `file_v4_url`. Сырое тело airlines не хранится никогда: оба
                # производителя -- живой `drone_collector.sources`
                # (`airlines_document`) и офлайновый импорт
                # (`tools/dji_area_import_sources.airlines_paths`) -- кладут
                # производный документ, где ссылка урезана до host+path и
                # ключ получил суффикс `_path`. Проверка на `"file_v4_url"`
                # не совпадала ни с одним телом, какое когда-либо писалось,
                # и `NO_V4_URL_AT_SOURCE` не выставлялся никогда: «у DJI нет
                # V4» молча становилось `NOT_CAPTURED`, то есть «не
                # захватили». Старое написание оставлено рядом: если тело
                # такой формы где-то лежит, смысл у него тот же.
                for key in ('"file_v4_url_path"', '"file_v4_url"'):
                    if (key + ':null' in compact
                            or key + ':""' in compact):
                        row['v4_absent_reason'] = 'NO_V4_URL_AT_SOURCE'
                        break
            except (StoreError, UnicodeDecodeError):
                pass

    if 'v4' in revs:
        row['v4_revision_id'] = revs['v4']['id']
        context = None
        if revs['v4']['request_context_json']:
            try:
                context = json.loads(revs['v4']['request_context_json'])
            except ValueError:
                context = None
        status, _ok = ev.v4_identity(context, flight_id)
        row['v4_identity_status'] = status
    else:
        row['v4_identity_status'] = ev.V4_ABSENT
        if row['v4_absent_reason'] is None:
            row['v4_absent_reason'] = 'NOT_CAPTURED'

    hardware, source = ev.hardware_for(card, route)
    row['hardware_id'] = hardware
    row['hardware_id_source'] = source

    existing = con.execute(
        'SELECT id FROM dji_flight_evidence WHERE flight_id=?',
        (flight_id,)).fetchone()
    if existing is None:
        cols = ', '.join(EVIDENCE_COLUMNS)
        marks = ', '.join('?' for _ in EVIDENCE_COLUMNS)
        con.execute('INSERT INTO dji_flight_evidence (%s) VALUES (%s)'
                    % (cols, marks), [row[c] for c in EVIDENCE_COLUMNS])
    else:
        assignments = ', '.join('%s=?' % c for c in EVIDENCE_COLUMNS
                                if c != 'flight_id')
        con.execute('UPDATE dji_flight_evidence SET %s WHERE flight_id=?'
                    % assignments,
                    [row[c] for c in EVIDENCE_COLUMNS if c != 'flight_id']
                    + [flight_id])
    return row


def _bool(value):
    if value is None:
        return None
    return 1 if value else 0


def flight_evidence(con, flight_id):
    return con.execute('SELECT * FROM dji_flight_evidence WHERE flight_id=?',
                       (int(flight_id),)).fetchone()


# ─── Сводки V4 ───────────────────────────────────────────────────────────────

V4_SUMMARY_COLUMNS = (
    'frame_count', 'top_count', 'usage_type', 't_first_ms', 't_last_ms',
    'span_s', 'start_offset_s', 'end_offset_s', 'dt_min_s', 'dt_max_s',
    'dt_median_s', 'nonpositive_dt_count', 'dt_over_limit_count',
    'counter_present_frames', 'counter_first_encoded_native',
    'counter_first_encoded_bits', 'counter_last_encoded_native',
    'counter_last_encoded_bits', 'counter_first_encoded_at_ms',
    'counter_last_encoded_at_ms', 'counter_leading_omitted_frames',
    'counter_trailing_omitted_frames', 'counter_interior_omitted_frames',
    'counter_negative_steps', 'counter_positive_steps',
    'counter_max_jump_native', 'counter_max_drop_native',
    'counter_observed_delta_native', 'counter_observed_delta_m2',
    'counter_zero_default_delta_native', 'counter_zero_default_delta_m2',
    'counter_quantization_max_error', 'application_flag_frames',
    'flow_positive_frames', 'application_frames', 'quantity_first',
    'quantity_last', 'quantity_delta', 'moving_application_frames',
    'moving_application_distance_m', 'gps_jump_over_100m',
    'frames_with_position', 'width_positive_frames', 'width_min',
    'width_max',
)


def upsert_v4_summary(con, flight_id, revision_id, summary, window_quality,
                      baseline_status, window_reasons, now=None):
    """Одна сводка на ревизию V4; повтор с той же версией парсера -> unchanged."""
    now = now or utcnow()
    existing = con.execute(
        'SELECT id, parser_version FROM dji_v4_summaries WHERE '
        'source_revision_id=?', (revision_id,)).fetchone()
    values = {c: summary.get(c) for c in V4_SUMMARY_COLUMNS}
    values['flight_id'] = int(flight_id)
    values['source_revision_id'] = revision_id
    values['parser_version'] = summary.get('parser_version') or V4_PARSER_VERSION
    values['computed_at'] = iso(now)
    values['window_quality'] = window_quality
    values['baseline_status'] = baseline_status
    values['window_reasons_json'] = json.dumps(list(window_reasons or []))
    values['summary_json'] = canonical_json(summary)
    cols = list(values.keys())
    if existing is None:
        con.execute('INSERT INTO dji_v4_summaries (%s) VALUES (%s)'
                    % (', '.join(cols), ', '.join('?' for _ in cols)),
                    [values[c] for c in cols])
        return 'new', con.execute('SELECT last_insert_rowid()').fetchone()[0]
    if existing['parser_version'] == values['parser_version']:
        return 'unchanged', existing['id']
    assignments = ', '.join('%s=?' % c for c in cols
                            if c != 'source_revision_id')
    con.execute('UPDATE dji_v4_summaries SET %s WHERE source_revision_id=?'
                % assignments,
                [values[c] for c in cols if c != 'source_revision_id']
                + [revision_id])
    return 'updated', existing['id']


def v4_summary_for_revision(con, revision_id):
    return con.execute('SELECT * FROM dji_v4_summaries WHERE '
                       'source_revision_id=?', (revision_id,)).fetchone()


# ─── Расчёты площади (append-only) ───────────────────────────────────────────

CALC_COLUMNS = (
    'flight_id', 'provider_account_id', 'drone_flight_id', 'hardware_id',
    'hardware_id_source', 'area_algorithm_version', 'calculation_input_hash',
    'calculated_at', 'superseded_at', 'supersede_reason', 'start_at_utc',
    'end_at_utc', 'report_timezone', 'report_start_date', 'raw_area_m2',
    'raw_area_source', 'card_area_m2', 'route_area_m2',
    'counter_observed_delta_m2', 'counter_zero_default_delta_m2',
    'controller_delta_area_m2', 'counter_baseline_status',
    'counter_window_quality', 'window_reasons_json',
    'corrected_recorded_area_m2', 'area_status', 'evidence_status',
    'area_method', 'area_confidence', 'anomaly_flags_json',
    'application_activity', 'application_channel_quality',
    'application_evidence_kind', 'application_without_area',
    'structural_candidate', 'structural_rule_version',
    'candidate_base_flight_id', 'bridge_flight_ids_json',
    'boundary_gaps_json', 'scalar_source_check', 'overlap_group_id',
    'aggregation_eligibility', 'v4_summary_id', 'list_revision_id',
    'card_revision_id', 'route_revision_id', 'v4_revision_id',
    'unique_coverage_estimate_m2', 'coverage_domain_id',
    'coverage_method_version', 'customer_id', 'customer_mapping_id',
    'customer_mapping_version', 'billable_area_m2', 'billing_policy_version',
    'billing_approval_id',
)


def insert_calculation(con, row, now=None):
    """Append-only: тот же (flight, version, hash) -> unchanged; иначе новая
    строка, прежняя текущая закрывается `superseded_at`."""
    now = now or utcnow()
    values = {c: row.get(c) for c in CALC_COLUMNS}
    values['calculated_at'] = iso(now)
    values['superseded_at'] = None
    values['area_algorithm_version'] = (values['area_algorithm_version']
                                        or AREA_ALGORITHM_VERSION)
    for key in ('application_without_area', 'structural_candidate',
                'scalar_source_check'):
        values[key] = _bool(values[key])
    existing = con.execute(
        'SELECT id, superseded_at FROM dji_area_calculations WHERE flight_id=? '
        'AND area_algorithm_version=? AND calculation_input_hash=?',
        (values['flight_id'], values['area_algorithm_version'],
         values['calculation_input_hash'])).fetchone()
    if existing is not None:
        if existing['superseded_at'] is not None:
            # Тот же вход снова стал актуальным: вернуть в текущие.
            con.execute('UPDATE dji_area_calculations SET superseded_at=NULL, '
                        'supersede_reason=NULL WHERE id=?', (existing['id'],))
            con.execute('UPDATE dji_area_calculations SET superseded_at=?, '
                        'supersede_reason=? WHERE flight_id=? AND '
                        'area_algorithm_version=? AND superseded_at IS NULL '
                        'AND id<>?',
                        (iso(now), 'INPUT_CHANGED', values['flight_id'],
                         values['area_algorithm_version'], existing['id']))
            return 'reactivated', existing['id']
        return 'unchanged', existing['id']
    con.execute('UPDATE dji_area_calculations SET superseded_at=?, '
                'supersede_reason=? WHERE flight_id=? AND '
                'area_algorithm_version=? AND superseded_at IS NULL',
                (iso(now), 'INPUT_CHANGED', values['flight_id'],
                 values['area_algorithm_version']))
    cols = list(CALC_COLUMNS)
    con.execute('INSERT INTO dji_area_calculations (%s) VALUES (%s)'
                % (', '.join(cols), ', '.join('?' for _ in cols)),
                [values[c] for c in cols])
    return 'new', con.execute('SELECT last_insert_rowid()').fetchone()[0]


def current_calculation(con, flight_id, version=None):
    version = version or AREA_ALGORITHM_VERSION
    return con.execute(
        'SELECT * FROM dji_area_calculations WHERE flight_id=? AND '
        'area_algorithm_version=? AND superseded_at IS NULL '
        'ORDER BY id DESC LIMIT 1', (int(flight_id), version)).fetchone()


# ─── Привязка к полю (append-only) ───────────────────────────────────────────

FIELD_COLUMNS = (
    'flight_id', 'field_resolver_version', 'field_input_hash',
    'calculated_at', 'superseded_at', 'geometry_key_raw',
    'geometry_key_format', 'geometry_md5', 'linked_land_uuid',
    'geometry_holder_land_uuid', 'geometry_object_id',
    'historical_geometry_available', 'historical_geometry_sha256',
    'field_attribution_tier', 'field_attribution_method', 'field_confidence',
    'field_land_uuid', 'field_name_at_snapshot', 'field_serial_number',
    'land_snapshot_id', 'land_revision_id', 'field_lineage_evidence_json',
    'holder_count', 'candidate_count', 'warnings_json', 'tier4_inside_share',
    'tier4_heuristic_version',
)


def insert_field_attribution(con, row, now=None):
    now = now or utcnow()
    values = {c: row.get(c) for c in FIELD_COLUMNS}
    values['calculated_at'] = iso(now)
    values['superseded_at'] = None
    values['field_resolver_version'] = (values['field_resolver_version']
                                        or FIELD_RESOLVER_VERSION)
    values['historical_geometry_available'] = (
        1 if values['historical_geometry_available'] else 0)
    existing = con.execute(
        'SELECT id, superseded_at FROM dji_field_attributions WHERE '
        'flight_id=? AND field_resolver_version=? AND field_input_hash=?',
        (values['flight_id'], values['field_resolver_version'],
         values['field_input_hash'])).fetchone()
    if existing is not None:
        if existing['superseded_at'] is not None:
            con.execute('UPDATE dji_field_attributions SET superseded_at=NULL '
                        'WHERE id=?', (existing['id'],))
            con.execute('UPDATE dji_field_attributions SET superseded_at=? '
                        'WHERE flight_id=? AND field_resolver_version=? AND '
                        'superseded_at IS NULL AND id<>?',
                        (iso(now), values['flight_id'],
                         values['field_resolver_version'], existing['id']))
            return 'reactivated', existing['id']
        return 'unchanged', existing['id']
    con.execute('UPDATE dji_field_attributions SET superseded_at=? WHERE '
                'flight_id=? AND field_resolver_version=? AND '
                'superseded_at IS NULL',
                (iso(now), values['flight_id'],
                 values['field_resolver_version']))
    cols = list(FIELD_COLUMNS)
    con.execute('INSERT INTO dji_field_attributions (%s) VALUES (%s)'
                % (', '.join(cols), ', '.join('?' for _ in cols)),
                [values[c] for c in cols])
    return 'new', con.execute('SELECT last_insert_rowid()').fetchone()[0]


# ─── Каталог полей: снимки, ревизии, геометрия ───────────────────────────────

def create_land_snapshot(con, captured_at_utc, capture_run_id=None,
                         scope=None, expected_count=None,
                         is_evidence_import=False, now=None):
    now = now or utcnow()
    con.execute(
        'INSERT INTO dji_land_snapshots (captured_at_utc, capture_run_id, '
        'scope_json, expected_count, received_count, complete, '
        'manifest_sha256, is_evidence_import, created_at) '
        'VALUES (?,?,?,?,0,NULL,NULL,?,?)',
        (iso(captured_at_utc or now), capture_run_id,
         canonical_json(scope) if scope else None, expected_count,
         1 if is_evidence_import else 0, iso(now)))
    return con.execute('SELECT last_insert_rowid()').fetchone()[0]


def finalize_land_snapshot(con, snapshot_id, received_count, complete,
                           manifest_sha256=None):
    con.execute('UPDATE dji_land_snapshots SET received_count=?, complete=?, '
                'manifest_sha256=? WHERE id=?',
                (received_count, None if complete is None else (1 if complete else 0),
                 manifest_sha256, snapshot_id))


def upsert_land_revision(con, snapshot_id, parsed):
    """(land_uuid, raw_sha256) уникальны; повтор двигает last_seen/seen_count."""
    existing = con.execute(
        'SELECT id, seen_count FROM dji_land_revisions WHERE land_uuid=? AND '
        'raw_sha256=?', (parsed['land_uuid'], parsed['raw_sha256'])).fetchone()
    if existing is not None:
        con.execute('UPDATE dji_land_revisions SET last_seen_snapshot_id=?, '
                    'seen_count=? WHERE id=?',
                    (snapshot_id, (existing['seen_count'] or 1) + 1,
                     existing['id']))
        return 'seen', existing['id']
    con.execute(
        'INSERT INTO dji_land_revisions (land_uuid, external_id, '
        'serial_number, name, total_area_raw, work_area_raw, '
        'obstacle_area_raw, area_unit, geometry_md5, geometry_storage_uuid, '
        'land_type, created_at_source, updated_at_source, center_lat, '
        'center_lng, raw_json, raw_sha256, first_seen_snapshot_id, '
        'last_seen_snapshot_id, seen_count) '
        'VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,1)',
        (parsed['land_uuid'], parsed['external_id'], parsed['serial_number'],
         parsed['name'], parsed['total_area_raw'], parsed['work_area_raw'],
         parsed['obstacle_area_raw'], parsed['area_unit'],
         parsed['geometry_md5'], parsed['geometry_storage_uuid'],
         parsed['land_type'], iso(parsed['created_at_source']),
         iso(parsed['updated_at_source']), parsed['center_lat'],
         parsed['center_lng'], parsed['raw_json'], parsed['raw_sha256'],
         snapshot_id, snapshot_id))
    return 'new', con.execute('SELECT last_insert_rowid()').fetchone()[0]


def upsert_land_geometry(con, body, expected_md5=None, source_revision_id=None,
                         now=None):
    """Байты геометрии content-addressed по contentMd5; проверка md5(bytes)."""
    now = now or utcnow()
    body = bytes(body)
    md5 = ev.geometry_md5_of(body)
    verified = expected_md5 is None or expected_md5.lower() == md5
    if expected_md5 is not None and not verified:
        raise StoreError('geometry md5 mismatch: expected %s, bytes give %s'
                         % (expected_md5, md5))
    existing = con.execute('SELECT id, md5_verified FROM dji_land_geometries '
                           'WHERE content_md5=?', (md5,)).fetchone()
    parse_status, rings = 'UNPARSED', None
    try:
        from dji_area.field import rings_from_geojson
        rings = rings_from_geojson(json.loads(body.decode('utf-8')))
        parse_status = 'OK' if rings else 'NO_PLANT_RING'
    except (ValueError, UnicodeDecodeError):
        parse_status = 'INVALID_JSON'
    if existing is not None:
        if expected_md5 is not None and not existing['md5_verified']:
            con.execute('UPDATE dji_land_geometries SET md5_verified=1 '
                        'WHERE id=?', (existing['id'],))
        return 'unchanged', existing['id']
    con.execute(
        'INSERT INTO dji_land_geometries (content_md5, sha256, size_bytes, '
        'md5_verified, body_blob, parse_status, ring_count, '
        'source_revision_id, first_seen_at) VALUES (?,?,?,?,?,?,?,?,?)',
        (md5, ev.sha256_bytes(body), len(body),
         1 if expected_md5 is not None else 0, sqlite3.Binary(body),
         parse_status, len(rings) if rings else None, source_revision_id,
         iso(now)))
    return 'new', con.execute('SELECT last_insert_rowid()').fetchone()[0]


def geometry_bodies_missing(con):
    """Сколько ревизий каталога ссылаются на md5, тела которого НЕТ.

    [REASON]: инкрементальный снимок нарочно не присылает уже известные
    полигоны, и «тело пропущено, оно уже в хранилище» снаружи выглядит ровно
    как «тело потеряно». Разница видна только здесь: если ссылка есть, а
    тела нет, резолвер поля молча съедет с TIER1_EXACT на TIER2_STRONG,
    сохранив HIGH-уверенность, и никто этого не заметит. Число возвращается
    приёмником в ответе, чтобы расхождение было громким в тот же день.
    """
    row = con.execute(
        'SELECT COUNT(DISTINCT r.geometry_md5) FROM dji_land_revisions AS r '
        'LEFT JOIN dji_land_geometries AS g ON g.content_md5 = r.geometry_md5 '
        'WHERE r.geometry_md5 IS NOT NULL AND g.id IS NULL').fetchone()
    return int(row[0] or 0)


def verify_geometry_holders(con):
    """Пометить md5_verified у геометрий, чей md5 совпал с contentMd5 хотя бы
    одной ревизии каталога. Возвращает число обновлённых строк."""
    cur = con.execute(
        'UPDATE dji_land_geometries SET md5_verified=1 WHERE md5_verified=0 '
        'AND content_md5 IN (SELECT DISTINCT geometry_md5 FROM '
        'dji_land_revisions WHERE geometry_md5 IS NOT NULL)')
    return cur.rowcount


# ─── Каталог для резолвера поля ──────────────────────────────────────────────

class SqliteCatalog(object):
    """FieldCatalog поверх sqlite3. Кэширует холдеры и lineage на период."""

    def __init__(self, con):
        self.con = con
        self._holders = None
        self._flight_keys = None

    def _load_holders(self):
        if self._holders is None:
            self._holders = {}
            for row in self.con.execute(
                    'SELECT geometry_md5, land_uuid FROM dji_land_revisions '
                    'WHERE geometry_md5 IS NOT NULL'):
                self._holders.setdefault(row['geometry_md5'], set()).add(
                    row['land_uuid'])
        return self._holders

    def geometry_by_md5(self, md5):
        row = self.con.execute(
            'SELECT id, sha256, md5_verified FROM dji_land_geometries WHERE '
            'content_md5=?', (md5,)).fetchone()
        if row is None:
            return None
        holders = sorted(self._load_holders().get(md5, ()))
        return {'geometry_object_id': row['id'], 'sha256': row['sha256'],
                'holder_land_uuids': holders,
                'verified': bool(row['md5_verified'])}

    def land_revisions(self, uuid):
        rows = self.con.execute(
            'SELECT id, land_uuid, name, serial_number, geometry_md5, '
            'last_seen_snapshot_id FROM dji_land_revisions WHERE land_uuid=? '
            'ORDER BY last_seen_snapshot_id, id', (uuid,)).fetchall()
        return [{'land_revision_id': r['id'], 'land_uuid': r['land_uuid'],
                 'name': r['name'], 'serial_number': r['serial_number'],
                 'geometry_md5': r['geometry_md5'],
                 'snapshot_id': r['last_seen_snapshot_id']} for r in rows]

    def _load_flight_keys(self):
        if self._flight_keys is None:
            self._flight_keys = {}
            for row in self.con.execute(
                    'SELECT flight_id, card_geometry_md5 FROM '
                    'dji_flight_evidence WHERE card_geometry_md5 IS NOT NULL '
                    "AND card_geometry_md5 <> ''"):
                self._flight_keys[row['flight_id']] = row['card_geometry_md5']
        return self._flight_keys

    def holders_for_md5(self, md5):
        return sorted(self._load_holders().get(md5, ()))

    def lineage_uuids_for_md5(self, md5, exclude_flight_id=None):
        from dji_area.field import KEY_COMPOSITE, parse_geometry_key
        out = {}
        for fid, key in self._load_flight_keys().items():
            if fid == exclude_flight_id:
                continue
            fmt, uuid, key_md5 = parse_geometry_key(key)
            if fmt == KEY_COMPOSITE and key_md5 == md5:
                out.setdefault(uuid, []).append(fid)
        return out

    def lineage_holders_for_uuid(self, uuid, exclude_flight_id=None):
        from dji_area.field import KEY_COMPOSITE, parse_geometry_key
        out = {}
        holders = self._load_holders()
        verified = {r['content_md5'] for r in self.con.execute(
            'SELECT content_md5 FROM dji_land_geometries WHERE md5_verified=1')}
        for fid, key in self._load_flight_keys().items():
            if fid == exclude_flight_id:
                continue
            fmt, key_uuid, key_md5 = parse_geometry_key(key)
            if fmt != KEY_COMPOSITE or key_uuid != uuid:
                continue
            if key_md5 not in verified:
                continue
            for holder in holders.get(key_md5, ()):
                out.setdefault(holder, []).append(fid)
        return out

    def catalog_state_sha(self):
        row = self.con.execute(
            'SELECT COUNT(*), MAX(id) FROM dji_land_revisions').fetchone()
        # [REASON]: счётчика строк мало. `md5_verified` переключается ПО МЕСТУ,
        # не добавляя ни ревизии, ни геометрии, а резолвер именно по нему
        # отличает TIER1_EXACT (байты сверены) от TIER2_STRONG. Пока состояние
        # каталога считалось только COUNT/MAX, проверка байтов не меняла
        # отпечаток: пересчёт печатал TIER1, а в таблице навсегда оставался
        # TIER2 с historical_geometry_available=0.
        geo = self.con.execute(
            'SELECT COUNT(*), MAX(id), SUM(COALESCE(md5_verified, 0)) '
            'FROM dji_land_geometries').fetchone()
        return canonical_json({'revisions': list(row), 'geometries': list(geo)})

    def current_polygons(self):
        """Полигоны для TIER4: геометрии с разобранными кольцами и центром."""
        from dji_area.field import rings_from_geojson
        out = []
        # [REASON]: одна карточка поля даёт СТОЛЬКО строк ревизий, сколько раз
        # у неё правили метаданные (§5 требует хранить правку имени как новую
        # ревизию). Без выбора последней ревизии на пару (land_uuid, md5)
        # соединение размножало один и тот же полигон, а §5.5 объявляет
        # кандидата неоднозначным, когда РАЗНЫЕ текущие полигоны накрывают
        # маршрут, -- и поле с двумя ревизиями молча становилось TIER5.
        rows = self.con.execute(
            'SELECT g.id, g.content_md5, g.body_blob, r.land_uuid, r.name, '
            'r.center_lat, r.center_lng, r.last_seen_snapshot_id, r.id AS rid '
            'FROM dji_land_geometries g JOIN dji_land_revisions r '
            'ON r.geometry_md5 = g.content_md5 WHERE g.md5_verified=1 '
            'AND r.id = (SELECT MAX(r2.id) FROM dji_land_revisions r2 '
            '            WHERE r2.land_uuid = r.land_uuid '
            '              AND r2.geometry_md5 = r.geometry_md5)').fetchall()
        for r in rows:
            try:
                rings = rings_from_geojson(json.loads(bytes(r['body_blob'])
                                                      .decode('utf-8')))
            except (ValueError, UnicodeDecodeError):
                continue
            if not rings:
                continue
            center = None
            if r['center_lat'] is not None and r['center_lng'] is not None:
                center = (r['center_lat'], r['center_lng'])
            out.append({'land_uuid': r['land_uuid'], 'name': r['name'],
                        'rings': rings, 'center': center,
                        'snapshot_id': r['last_seen_snapshot_id'],
                        'land_revision_id': r['rid']})
        return out
