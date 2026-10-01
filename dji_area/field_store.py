# -*- coding: utf-8 -*-
"""dji_area/field_store.py -- чтение полей DJI и привязок вылетов. ТОЛЬКО чтение.

DRONE-FIELD-PASSPORT-001. Ни одной новой таблицы: всё, что нужно паспорту
поля и паспорту вылета, уже лежит в слое доказательств DJI
(миграция `DJI_AREA_EVIDENCE_001`):

* `dji_land_revisions` / `dji_land_snapshots` / `dji_land_geometries` --
  каталог полей DJI с историей: неизменяемые ревизии метаданных, моменты
  наблюдения и байты границ, адресованные по contentMd5;
* `dji_field_attributions` -- append-only привязка вылета к полю, которую
  пишет суточный пересчёт рядом с расчётом площади;
* `dji_flight_evidence`, `dji_source_revisions` -- указатели и неизменяемые
  ревизии источников.

Писатель всех этих таблиц -- `dji_area/store.py` (заморожен); здесь только
SELECT. Соединение -- как у `control_store`: sqlite3 с `row_factory =
sqlite3.Row`, у экранов -- `mode=ro`.

ПРАВИЛО ЧЛЕНСТВА (§4 задания). Вылет входит в подтверждённый итог записи
поля DJI тогда и только тогда, когда его ТЕКУЩАЯ строка привязки
(`superseded_at IS NULL`, действующая версия резолвера) несёт
`field_land_uuid` этой записи и подтверждённое состояние
(`field_view.CONFIRMED_STATES`).

[REASON]: `geometry_md5` вторым ключом членства НЕ служит. Одинаковые байты
границы бывают у нескольких записей каталога DJI (пересинхронизация
delete+create, правка без смены границы); если бы вылет попадал в каждую
запись с той же границей, его принятая площадь складывалась бы столько раз,
сколько у границы держателей. md5 здесь -- только версия границы,
происхождение и диагностика «та же граница у N записей».

Чтение пакетное: куски по 400 идентификаторов, число запросов не зависит от
числа вылетов. Тела источников и байты границ не читаются никогда --
паспорт показывает их хэши и время, а не содержимое.
"""

import sqlite3

from dji_area import AREA_ALGORITHM_VERSION, FIELD_RESOLVER_VERSION
from dji_area import field_view as fv

CHUNK = 400

REQUIRED_TABLES = (
    'drone_flights', 'dji_field_attributions', 'dji_flight_evidence',
    'dji_land_revisions', 'dji_land_geometries', 'dji_land_snapshots',
    'dji_area_calculations',
)

# Колонки строки привязки, которые читают экраны и перепись. Ни
# `geometry_key_raw` (сырой ключ карточки), ни родословная другие вылеты
# сюда не попадают: паспорт называет версию границы её md5.
ATTR_COLUMNS = (
    'id', 'flight_id', 'field_resolver_version', 'field_input_hash',
    'calculated_at', 'geometry_key_format', 'geometry_md5',
    'linked_land_uuid', 'geometry_holder_land_uuid',
    'historical_geometry_available', 'historical_geometry_sha256',
    'field_attribution_tier', 'field_attribution_method', 'field_confidence',
    'field_land_uuid', 'field_name_at_snapshot', 'field_serial_number',
    'land_snapshot_id', 'land_revision_id', 'holder_count', 'warnings_json',
    'tier4_inside_share',
)

# Указатели доказательств вылета: id ревизий и признаки, без тел.
EVIDENCE_COLUMNS = (
    'flight_id', 'list_revision_id', 'card_revision_id', 'route_revision_id',
    'v4_revision_id', 'airlines_revision_id', 'card_geometry_md5',
    'route_identity_status', 'v4_identity_status', 'v4_absent_reason',
    'hardware_id_source',
)

# Ревизия источника для паспорта: тип, хэш, время, разборщик. НЕ читаются
# `body_text`, `body_path` и `request_context_json`.
SOURCE_COLUMNS = (
    'id', 'source_type', 'sha256', 'size_bytes', 'captured_at_utc',
    'parser_version', 'schema_version', 'api_status', 'is_evidence_import',
)

CALC_META_COLUMNS = (
    'id', 'area_algorithm_version', 'calculation_input_hash',
    'calculated_at', 'list_revision_id', 'card_revision_id',
    'route_revision_id', 'v4_revision_id', 'v4_summary_id',
    'raw_area_source',
)


# ─── Служебное ───────────────────────────────────────────────────────────────

def _chunks(ids, size=CHUNK):
    ids = sorted({int(i) for i in ids})
    for start in range(0, len(ids), size):
        yield ids[start:start + size]


def _dict(row):
    return None if row is None else dict(row)


def missing_tables(con):
    names = {r[0] for r in con.execute(
        "SELECT name FROM sqlite_master WHERE type='table'")}
    return [name for name in REQUIRED_TABLES if name not in names]


def tables_present(con):
    return not missing_tables(con)


def _sql_time(dt):
    """19-символьная строка для сравнения с TEXT-колонкой времени.

    [REASON]: `drone_flights.started_at` пишет SQLAlchemy строкой из 26
    символов (с микросекундами), а stdlib-писатели -- из 19. Нижняя граница
    `>= 'YYYY-MM-DD HH:MM:SS'` включает обе записи той же секунды, верхняя
    `< ...` -- исключает: полуоткрытый период модуля держится для обоих
    форматов.
    """
    return None if dt is None else dt.strftime('%Y-%m-%d %H:%M:%S')


def _period(column, utc_start, utc_end_excl):
    sql, params = '', []
    if utc_start is not None:
        sql += ' AND %s >= ?' % column
        params.append(_sql_time(utc_start))
    if utc_end_excl is not None:
        sql += ' AND %s < ?' % column
        params.append(_sql_time(utc_end_excl))
    return sql, params


def _like(term):
    escaped = (term.replace('\\', '\\\\').replace('%', '\\%')
               .replace('_', '\\_'))
    return '%' + escaped + '%'


def _json_ready(con):
    """Есть ли в SQLite функции JSON (нужны для адреса из ревизии)."""
    try:
        con.execute("SELECT json_valid('{}')").fetchone()
        return True
    except sqlite3.Error:
        return False


def _address_sql(con, alias='r'):
    # [REASON]: адрес лежит только внутри `raw_json` ревизии (отдельной
    # колонки нет, выдумывать её нельзя). Берётся одно поле `address` --
    # тот же текст, что DJI показывает в кабинете; ни рамка, ни центр, ни
    # остальной узел наружу не выходят. Неразборчивый JSON даёт NULL, а не
    # падение запроса.
    if not _json_ready(con):
        return 'NULL'
    return ("CASE WHEN json_valid(%s.raw_json) THEN "
            "json_extract(%s.raw_json, '$.address') END" % (alias, alias))


# ─── Привязки и доказательства вылетов ───────────────────────────────────────

def current_attributions(con, flight_ids, version=None):
    """{flight_id DJI: текущая строка привязки} пакетно, по 400.

    Текущая -- `superseded_at IS NULL` под действующей версией резолвера;
    при двух текущих строках одного вылета берётся последняя по id (тот же
    выбор, что у расчёта площади).
    """
    version = version or FIELD_RESOLVER_VERSION
    out = {}
    cols = ', '.join(ATTR_COLUMNS)
    for chunk in _chunks(flight_ids):
        rows = con.execute(
            'SELECT %s FROM dji_field_attributions WHERE '
            'field_resolver_version=? AND superseded_at IS NULL AND '
            'flight_id IN (%s) ORDER BY id' % (cols, ','.join('?' * len(chunk))),
            [version] + chunk).fetchall()
        for row in rows:
            out[int(row['flight_id'])] = dict(row)
    return out


def evidence_for(con, flight_ids):
    """{flight_id DJI: указатели доказательств} пакетно, без тел."""
    out = {}
    cols = ', '.join(EVIDENCE_COLUMNS)
    for chunk in _chunks(flight_ids):
        rows = con.execute(
            'SELECT %s FROM dji_flight_evidence WHERE flight_id IN (%s)'
            % (cols, ','.join('?' * len(chunk))), chunk).fetchall()
        for row in rows:
            out[int(row['flight_id'])] = dict(row)
    return out


def source_revisions(con, revision_ids):
    """{id: ревизия источника без тела} для паспорта вылета."""
    ids = [i for i in revision_ids if i]
    out = {}
    cols = ', '.join(SOURCE_COLUMNS)
    for chunk in _chunks(ids):
        for row in con.execute(
                'SELECT %s FROM dji_source_revisions WHERE id IN (%s)'
                % (cols, ','.join('?' * len(chunk))), chunk).fetchall():
            out[int(row['id'])] = dict(row)
    return out


def calculation_meta(con, flight_id, version=None):
    """Метаданные текущего расчёта площади вылета (без самих величин)."""
    version = version or AREA_ALGORITHM_VERSION
    return _dict(con.execute(
        'SELECT %s FROM dji_area_calculations WHERE flight_id=? AND '
        'area_algorithm_version=? AND superseded_at IS NULL '
        'ORDER BY id DESC LIMIT 1' % ', '.join(CALC_META_COLUMNS),
        (int(flight_id), version)).fetchone())


def flight_row(con, flight_id):
    """Вылет из журнала `drone_flights` по id DJI с номером машины; или None."""
    return _dict(con.execute(
        'SELECT f.id, f.dji_flight_id, f.started_at, f.finished_at, '
        'f.area_ha, f.nickname_raw, f.drone_unit_id, f.region, '
        'u.number AS unit_number FROM drone_flights f '
        'LEFT JOIN drone_units u ON u.id = f.drone_unit_id '
        'WHERE f.dji_flight_id = ?', (int(flight_id),)).fetchone())


# ─── Каталог полей ───────────────────────────────────────────────────────────

def geometry_rows(con, md5s):
    """{content_md5: строка `dji_land_geometries` БЕЗ тела} для md5 списка."""
    md5s = sorted({m for m in md5s if m})
    out = {}
    for start in range(0, len(md5s), CHUNK):
        chunk = md5s[start:start + CHUNK]
        for row in con.execute(
                'SELECT content_md5, sha256, size_bytes, md5_verified, '
                'parse_status, ring_count, first_seen_at '
                'FROM dji_land_geometries WHERE content_md5 IN (%s)'
                % ','.join('?' * len(chunk)), chunk).fetchall():
            out[row['content_md5']] = dict(row)
    return out


def snapshots(con, snapshot_ids):
    ids = [i for i in snapshot_ids if i]
    out = {}
    for chunk in _chunks(ids):
        for row in con.execute(
                'SELECT id, captured_at_utc, complete, is_evidence_import '
                'FROM dji_land_snapshots WHERE id IN (%s)'
                % ','.join('?' * len(chunk)), chunk).fetchall():
            out[int(row['id'])] = dict(row)
    return out


def land_header(con, land_uuid):
    """Последняя ревизия записи поля DJI и её сводка; None -- записи нет."""
    agg = con.execute(
        'SELECT MAX(id) AS last_id, COUNT(*) AS revisions, '
        'COUNT(DISTINCT geometry_md5) AS boundaries, '
        'MIN(first_seen_snapshot_id) AS first_snapshot_id, '
        'MAX(last_seen_snapshot_id) AS last_snapshot_id '
        'FROM dji_land_revisions WHERE land_uuid = ?',
        (land_uuid,)).fetchone()
    if agg is None or agg['last_id'] is None:
        return None
    row = dict(con.execute(
        'SELECT r.id, r.land_uuid, r.external_id, r.serial_number, r.name, '
        'r.total_area_raw, r.work_area_raw, r.area_unit, r.geometry_md5, '
        'r.land_type, r.created_at_source, r.updated_at_source, '
        '%s AS address FROM dji_land_revisions r WHERE r.id = ?'
        % _address_sql(con), (agg['last_id'],)).fetchone())
    row.update({'revisions': agg['revisions'],
                'boundaries': agg['boundaries'],
                'first_snapshot_id': agg['first_snapshot_id'],
                'last_snapshot_id': agg['last_snapshot_id']})
    seen = snapshots(con, [agg['first_snapshot_id'], agg['last_snapshot_id']])
    row['first_seen_at'] = (seen.get(agg['first_snapshot_id']) or {}).get(
        'captured_at_utc')
    row['last_seen_at'] = (seen.get(agg['last_snapshot_id']) or {}).get(
        'captured_at_utc')
    external = con.execute(
        'SELECT DISTINCT external_id FROM dji_land_revisions WHERE '
        'land_uuid = ? AND external_id IS NOT NULL ORDER BY external_id',
        (land_uuid,)).fetchall()
    row['external_ids'] = [r[0] for r in external]
    return row


def land_revisions(con, land_uuid):
    """Все ревизии записи поля по порядку наблюдения, с моментами снимков."""
    return [dict(r) for r in con.execute(
        'SELECT r.id, r.geometry_md5, r.name, r.serial_number, '
        'r.first_seen_snapshot_id, r.last_seen_snapshot_id, r.seen_count, '
        's1.captured_at_utc AS first_seen_at, '
        's2.captured_at_utc AS last_seen_at '
        'FROM dji_land_revisions r '
        'LEFT JOIN dji_land_snapshots s1 ON s1.id = r.first_seen_snapshot_id '
        'LEFT JOIN dji_land_snapshots s2 ON s2.id = r.last_seen_snapshot_id '
        'WHERE r.land_uuid = ? ORDER BY r.first_seen_snapshot_id, r.id',
        (land_uuid,)).fetchall()]


def other_holders(con, md5s, land_uuid):
    """{md5: [другие записи DJI с той же границей]} -- только диагностика."""
    md5s = sorted({m for m in md5s if m})
    out = {m: [] for m in md5s}
    for start in range(0, len(md5s), CHUNK):
        chunk = md5s[start:start + CHUNK]
        for row in con.execute(
                'SELECT geometry_md5, land_uuid FROM dji_land_revisions '
                'WHERE geometry_md5 IN (%s) AND land_uuid <> ? '
                'GROUP BY geometry_md5, land_uuid ORDER BY land_uuid'
                % ','.join('?' * len(chunk)), chunk + [land_uuid]).fetchall():
            out[row['geometry_md5']].append(row['land_uuid'])
    return out


def land_list(con, query='', utc_start=None, utc_end_excl=None,
              only_with_flights=False, page=1, page_size=50, version=None):
    """Страница списка записей полей DJI. Геометрии и площадей вылетов нет.

    Последняя ревизия каждой записи, число ревизий и версий границы, число
    вылетов периода с подтверждённой и с предположительной привязкой и
    последний подтверждённый вылет. Принятая площадь здесь не считается:
    это список, а не отчёт по 6 000 полей.
    """
    version = version or FIELD_RESOLVER_VERSION
    period_sql, period_params = _period('f.started_at', utc_start,
                                        utc_end_excl)
    confirmed = list(fv.CONFIRMED_TIERS)
    provisional = list(fv.PROVISIONAL_TIERS)
    att = (
        'SELECT a.field_land_uuid AS land_uuid, '
        'SUM(CASE WHEN a.field_attribution_tier IN (?,?) THEN 1 ELSE 0 END) '
        'AS confirmed, '
        'SUM(CASE WHEN a.field_attribution_tier IN (?,?) THEN 1 ELSE 0 END) '
        'AS provisional, '
        'MAX(CASE WHEN a.field_attribution_tier IN (?,?) THEN f.started_at '
        'END) AS last_confirmed_at '
        'FROM dji_field_attributions a '
        'JOIN drone_flights f ON f.dji_flight_id = a.flight_id '
        'WHERE a.superseded_at IS NULL AND a.field_resolver_version = ? '
        'AND a.field_land_uuid IS NOT NULL' + period_sql +
        ' GROUP BY a.field_land_uuid')
    att_params = confirmed + provisional + confirmed + [version] + \
        period_params
    cur = ('SELECT land_uuid, MAX(id) AS last_id, COUNT(*) AS revisions, '
           'COUNT(DISTINCT geometry_md5) AS boundaries '
           'FROM dji_land_revisions GROUP BY land_uuid')
    address = _address_sql(con)
    where, where_params = [], []
    term = (query or '').strip()
    if term:
        pattern = _like(term)
        fields = ['r.name', 'r.serial_number', 'r.land_uuid', 'r.external_id']
        if address != 'NULL':
            fields.append(address)
        where.append('(' + ' OR '.join(
            "%s LIKE ? ESCAPE '\\'" % f for f in fields) + ')')
        where_params.extend([pattern] * len(fields))
    if only_with_flights:
        where.append('COALESCE(att.confirmed, 0) + COALESCE(att.provisional, '
                     '0) > 0')
    where_sql = (' WHERE ' + ' AND '.join(where)) if where else ''
    base = ('FROM (%s) cur JOIN dji_land_revisions r ON r.id = cur.last_id '
            'LEFT JOIN (%s) att ON att.land_uuid = r.land_uuid' % (cur, att))
    total = con.execute('SELECT COUNT(*) ' + base + where_sql,
                        att_params + where_params).fetchone()[0]
    pages = max(1, (total + page_size - 1) // page_size)
    page = min(max(1, int(page or 1)), pages)
    rows = con.execute(
        'SELECT r.land_uuid, r.name, r.serial_number, r.external_id, '
        'r.work_area_raw, r.total_area_raw, r.area_unit, '
        '%s AS address, cur.revisions, cur.boundaries, '
        'COALESCE(att.confirmed, 0) AS confirmed, '
        'COALESCE(att.provisional, 0) AS provisional, '
        'att.last_confirmed_at ' % address + base + where_sql +
        ' ORDER BY COALESCE(att.confirmed, 0) DESC, '
        'COALESCE(r.name, r.serial_number, r.land_uuid), r.land_uuid '
        'LIMIT ? OFFSET ?',
        att_params + where_params + [page_size, (page - 1) * page_size]
    ).fetchall()
    return {'rows': [dict(r) for r in rows], 'total': total, 'page': page,
            'pages': pages, 'page_size': page_size}


# ─── Вылеты записи поля ──────────────────────────────────────────────────────

def field_flights(con, land_uuid, utc_start=None, utc_end_excl=None,
                  version=None):
    """Вылеты, ТЕКУЩАЯ привязка которых несёт ``field_land_uuid`` записи.

    Возвращает {'rows': [...], 'orphans': n}: строки -- привязка вылета и
    его поля журнала (`drone_flights`); ``orphans`` -- привязки, вылета
    которых в журнале нет (их RAW взять неоткуда, в итог они не идут и
    называются числом). Разделение на подтверждённые и предположительные --
    `confirmed_members` / `provisional_members`.
    """
    version = version or FIELD_RESOLVER_VERSION
    period_sql, period_params = _period('f.started_at', utc_start,
                                        utc_end_excl)
    cols = ', '.join('a.%s' % c for c in ATTR_COLUMNS)
    rows = con.execute(
        'SELECT %s, f.id AS journal_id, f.dji_flight_id, f.started_at, '
        'f.area_ha, f.nickname_raw, u.number AS unit_number '
        'FROM dji_field_attributions a '
        'LEFT JOIN drone_flights f ON f.dji_flight_id = a.flight_id '
        'LEFT JOIN drone_units u ON u.id = f.drone_unit_id '
        'WHERE a.field_land_uuid = ? AND a.superseded_at IS NULL '
        'AND a.field_resolver_version = ?%s '
        'ORDER BY f.started_at DESC, a.flight_id DESC, a.id'
        % (cols, period_sql), [land_uuid, version] + period_params).fetchall()
    by_flight = {}
    order = []
    orphans = 0
    for row in rows:
        item = dict(row)
        fid = int(item['flight_id'])
        if item['journal_id'] is None:
            orphans += 1
            continue
        if fid not in by_flight:
            order.append(fid)
        # При двух текущих строках одного вылета -- последняя по id.
        if fid not in by_flight or item['id'] > by_flight[fid]['id']:
            by_flight[fid] = item
    return {'rows': [by_flight[fid] for fid in order], 'orphans': orphans}


def confirmed_members(rows, land_uuid):
    """Подтверждённые вылеты записи: ``field_land_uuid`` И состояние.

    [REASON]: проверка двойная намеренно. Выборка `field_flights` уже стоит
    на `field_land_uuid`, но этот фильтр -- последнее слово перед итогом:
    гектары поля складываются только из строк, где совпадает запись И
    состояние подтверждено (EXACT / IDENTIFIED). Строку, попавшую сюда по
    md5 или с предположительным уровнем, он не пропустит.
    """
    return [r for r in rows if r.get('field_land_uuid') == land_uuid
            and fv.classify(r)[0] in fv.CONFIRMED_STATES]


def provisional_members(rows, land_uuid):
    return [r for r in rows if r.get('field_land_uuid') == land_uuid
            and fv.classify(r)[0] in fv.PROVISIONAL_STATES]


def shared_geometry_flights(con, land_uuid, md5s, utc_start=None,
                            utc_end_excl=None, version=None, limit=100):
    """Вылеты на ТЕХ ЖЕ границах, учтённые НЕ в этой записи. Диагностика.

    ({'rows': [...], 'total': n}). Гектаров записи они не добавляют --
    см. правило членства в шапке модуля.
    """
    version = version or FIELD_RESOLVER_VERSION
    md5s = sorted({m for m in md5s if m})
    if not md5s:
        return {'rows': [], 'total': 0}
    period_sql, period_params = _period('f.started_at', utc_start,
                                        utc_end_excl)
    marks = ','.join('?' * len(md5s))
    base = ('FROM dji_field_attributions a '
            'LEFT JOIN drone_flights f ON f.dji_flight_id = a.flight_id '
            'WHERE a.geometry_md5 IN (%s) AND a.superseded_at IS NULL '
            'AND a.field_resolver_version = ? '
            'AND (a.field_land_uuid IS NULL OR a.field_land_uuid <> ?)%s'
            % (marks, period_sql))
    params = md5s + [version, land_uuid] + period_params
    total = con.execute('SELECT COUNT(*) ' + base, params).fetchone()[0]
    cols = ', '.join('a.%s' % c for c in ATTR_COLUMNS)
    rows = con.execute(
        'SELECT %s, f.started_at ' % cols + base +
        ' ORDER BY f.started_at DESC, a.flight_id DESC LIMIT ?',
        params + [limit]).fetchall()
    return {'rows': [dict(r) for r in rows], 'total': total}


# ─── Перепись покрытия ───────────────────────────────────────────────────────

def flight_census(con, utc_start, utc_end_excl, version=None,
                  calc_version=None):
    """(итог, {месяц UTC+5: корзина}) привязок вылетов периода.

    Классификация -- та же `field_view.classify`, что у экранов: сюда
    приходят сгруппированные виды строк (уровень, метод, есть ли карточка и
    ключ в ней, есть ли байты границы), а не копия правил. Один запрос с
    GROUP BY: строк ответа -- десятки, а не по вылету.
    """
    version = version or FIELD_RESOLVER_VERSION
    calc_version = calc_version or AREA_ALGORITHM_VERSION
    period_sql, period_params = _period('f.started_at', utc_start,
                                        utc_end_excl)
    rows = con.execute(
        "SELECT strftime('%Y-%m', f.started_at, '+5 hours') AS month, "
        'a.field_attribution_tier AS tier, '
        'a.field_attribution_method AS method, '
        'CASE WHEN a.id IS NULL THEN 0 ELSE 1 END AS has_attr, '
        "CASE WHEN a.warnings_json LIKE '%LINEAGE_CONFLICT%' THEN 1 ELSE 0 "
        'END AS conflict, '
        'CASE WHEN a.historical_geometry_available = 1 THEN 1 ELSE 0 END '
        'AS bytes, '
        'CASE WHEN a.geometry_md5 IS NOT NULL THEN 1 ELSE 0 END AS has_md5, '
        'CASE WHEN e.card_revision_id IS NULL THEN 0 ELSE 1 END AS has_card, '
        "CASE WHEN COALESCE(e.card_geometry_md5, '') = '' THEN 0 ELSE 1 END "
        'AS card_key, '
        'CASE WHEN EXISTS (SELECT 1 FROM dji_area_calculations c WHERE '
        'c.flight_id = f.dji_flight_id AND c.superseded_at IS NULL AND '
        'c.area_algorithm_version = ?) THEN 1 ELSE 0 END AS has_calc, '
        'COUNT(*) AS n '
        'FROM drone_flights f '
        'LEFT JOIN dji_field_attributions a ON a.flight_id = f.dji_flight_id '
        'AND a.superseded_at IS NULL AND a.field_resolver_version = ? '
        'LEFT JOIN dji_flight_evidence e ON e.flight_id = f.dji_flight_id '
        'WHERE 1 = 1' + period_sql +
        ' GROUP BY 1, 2, 3, 4, 5, 6, 7, 8, 9, 10',
        [calc_version, version] + period_params).fetchall()
    total = fv.empty_census()
    months = {}
    for r in rows:
        attr = None
        if r['has_attr']:
            attr = {'field_attribution_tier': r['tier'],
                    'field_attribution_method': r['method'],
                    'warnings_json': ('["LINEAGE_CONFLICT"]'
                                      if r['conflict'] else None),
                    'historical_geometry_available': r['bytes'],
                    'geometry_md5': 'present' if r['has_md5'] else None}
        evidence = {'card_revision_id': 1 if r['has_card'] else None,
                    'card_geometry_md5': 'present' if r['card_key'] else None}
        for bucket in (total, months.setdefault(r['month'],
                                                fv.empty_census())):
            fv.census_add(bucket, attr, evidence, bool(r['has_calc']),
                          count=r['n'])
    return total, months


def catalog_census(con):
    """Состояние каталога полей DJI: записи, ревизии, байты, общие границы."""
    catalog = {}
    one = con.execute(
        'SELECT COUNT(DISTINCT land_uuid), COUNT(*), '
        'COUNT(DISTINCT geometry_md5) FROM dji_land_revisions').fetchone()
    catalog['land_records'] = one[0]
    catalog['land_revisions'] = one[1]
    catalog['boundary_md5s'] = one[2]
    geo = con.execute(
        'SELECT COUNT(*), COALESCE(SUM(md5_verified), 0) '
        'FROM dji_land_geometries').fetchone()
    catalog['geometry_bodies'] = geo[0]
    catalog['geometry_bodies_verified'] = geo[1]
    catalog['md5_without_body'] = con.execute(
        'SELECT COUNT(DISTINCT r.geometry_md5) FROM dji_land_revisions r '
        'LEFT JOIN dji_land_geometries g ON g.content_md5 = r.geometry_md5 '
        'WHERE r.geometry_md5 IS NOT NULL AND g.id IS NULL').fetchone()[0]
    shared = con.execute(
        'SELECT COUNT(*), COALESCE(SUM(n), 0) FROM ('
        'SELECT geometry_md5, COUNT(DISTINCT land_uuid) AS n '
        'FROM dji_land_revisions WHERE geometry_md5 IS NOT NULL '
        'GROUP BY geometry_md5 HAVING COUNT(DISTINCT land_uuid) > 1)'
    ).fetchone()
    catalog['shared_md5'] = shared[0]
    catalog['shared_md5_records'] = shared[1]
    return catalog


def snapshot_census(con, now_utc=None, days=30):
    """Свежесть снимков каталога за последние ``days`` суток.

    [REASON]: байты старой границы сохраняются, только пока снимок каталога
    застал её в DJI. Если снимки идут не каждый день, правка поля в кабинете
    уносит прежнюю границу навсегда, и вылеты по ней останутся
    «граница не сохранена». Поэтому перепись называет, сколько суток окна
    снимок был, а не только дату последнего.
    """
    from datetime import datetime, timedelta

    now_utc = now_utc or datetime.utcnow()
    since = now_utc - timedelta(days=days)
    snap = con.execute(
        'SELECT COUNT(*) AS n, '
        'COALESCE(SUM(CASE WHEN complete = 1 THEN 1 ELSE 0 END), 0) '
        'AS complete, '
        "COUNT(DISTINCT substr(datetime(captured_at_utc, '+5 hours'), 1, 10))"
        ' AS days, MAX(captured_at_utc) AS last_at '
        'FROM dji_land_snapshots WHERE is_evidence_import = 0 '
        'AND captured_at_utc >= ?', (_sql_time(since),)).fetchone()
    last_any = con.execute(
        'SELECT MAX(captured_at_utc) FROM dji_land_snapshots '
        'WHERE is_evidence_import = 0').fetchone()[0]
    last_complete = con.execute(
        'SELECT MAX(captured_at_utc) FROM dji_land_snapshots '
        'WHERE is_evidence_import = 0 AND complete = 1').fetchone()[0]
    return {
        'window_days': days,
        'since_utc': _sql_time(since),
        'count': snap['n'], 'complete': snap['complete'],
        'days_with_snapshot': snap['days'],
        'last_in_window_utc': snap['last_at'],
        'last_utc': last_any, 'last_complete_utc': last_complete,
    }


def census(con, utc_start, utc_end_excl, now_utc=None, version=None,
           calc_version=None, snapshot_days=30):
    """Перепись: вылеты периода, каталог и свежесть снимков. Только чтение."""
    version = version or FIELD_RESOLVER_VERSION
    calc_version = calc_version or AREA_ALGORITHM_VERSION
    total, months = flight_census(con, utc_start, utc_end_excl, version,
                                  calc_version)
    return {'total': total, 'months': months,
            'catalog': catalog_census(con),
            'snapshots': snapshot_census(con, now_utc, snapshot_days),
            'versions': {'field_resolver': version,
                         'area_algorithm': calc_version}}


def fields_of_flights(con, land_uuids):
    """{land_uuid: последняя ревизия (имя, серийный)} -- подписи ссылок."""
    uuids = sorted({u for u in land_uuids if u})
    out = {}
    for start in range(0, len(uuids), CHUNK):
        chunk = uuids[start:start + CHUNK]
        for row in con.execute(
                'SELECT r.land_uuid, r.name, r.serial_number FROM '
                'dji_land_revisions r JOIN (SELECT land_uuid, MAX(id) AS '
                'last_id FROM dji_land_revisions WHERE land_uuid IN (%s) '
                'GROUP BY land_uuid) cur ON cur.last_id = r.id'
                % ','.join('?' * len(chunk)), chunk).fetchall():
            out[row['land_uuid']] = dict(row)
    return out

