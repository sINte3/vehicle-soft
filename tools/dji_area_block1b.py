# -*- coding: utf-8 -*-
"""tools/dji_area_block1b.py -- evidence bundle этапа 1B. ТОЛЬКО ЧТЕНИЕ.

Собирает за ОДИН прогон всё, что нужно для первого независимого числа
площади по названным бортам и суткам, и делает bundle САМОДОСТАТОЧНЫМ:
неизменяемые тела V4, маршрутов и геометрии полей выгружаются рядом с
таблицами, с проверкой SHA/MD5 и манифестом. Расчёт после этого
воспроизводится где угодно, без доступа к серверу.

  1. вылеты борта за отчётный день с привязкой к КОНКРЕТНОМУ ``land_uuid``
     и к КОНКРЕТНОЙ ревизии каталога (``land_revision_id``), а не «все
     ревизии с таким uuid»: подпись контура, прочитанная сегодня, может
     относиться к другой версии полигона, чем работа 18 августа;
  2. ``totalArea`` / ``workArea`` / ``totalObstacleArea`` в му и гектарах,
     плюс ОСТАТОК ``total - obstacle - work``, который НЕ называется
     safety margin: чем он является, ещё предстоит доказать;
  3. forensic записей с плоским счётчиком и точных повторов скаляра:
     длительность, минимально необходимая средняя скорость, база повтора;
  4. ``V4_APPLICATION_COVERAGE_ESTIMATE`` -- уникальное покрытие, повторное
     покрытие, вынос за контур, холостой пролёт, неизвестное;
  5. S = сумма(ширина_i x расстояние_i) по кадрам против U = уникальное
     объединение, с пометкой вылетов, где это сравнение вообще способно
     что-то различить.

БАЗУ НЕ МЕНЯЕТ. Открывает через ``file:...?mode=ro``, читает всё в ОДНОЙ
транзакции (снимок не может разъехаться между выборками), не импортирует
Flask и не вызывает ``create_app()``.

ЧЕГО РЕЗУЛЬТАТ НЕ УТВЕРЖДАЕТ. Это НЕ площадь к счёту и НЕ доказательство
того, что препарат долетел до земли: ширина берётся записанная бортом, а её
соответствие реальной полосе осаждения проверяется только полевой
калибровкой (NY/T 3213-2023).

ЗАПУСК (PowerShell, по одной команде на строку -- в PowerShell нет &&)

  cd C:\\transport-report-staging
  & "C:\\Program Files\\Python314\\python.exe" tools\\dji_area_block1b.py --db "C:\\transport-report-staging\\instance\\transport.db" --date 2026-08-18 --hardware 1581F574B2387001009R --hardware 1581F574B235W00100Q5 --out "C:\\VehicleSoft_Block1B\\out"

КОДЫ ВОЗВРАТА
  0  bundle собран. Отдельные вылеты при этом могут иметь
     ``coverage_status = FAILED`` с текстом ошибки: сбой на ОДНОМ вылете не
     роняет прогон, но объявляется строкой WARNING в сводке
  1  предусловие не выполнено (нет таблиц модели, либо каталог вывода занят)
  2  база не найдена (файл НЕ создаётся)

ОТКАТ
  Кода: удалить этот файл, его никто не импортирует.
  Данных: отката не требуется -- записей в базу не производится.
"""

import argparse
import csv
import hashlib
import json
import os
import sqlite3
import sys
from datetime import datetime

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from dji_area import AREA_ALGORITHM_VERSION, FIELD_RESOLVER_VERSION  # noqa: E402
from dji_area import coverage as cov  # noqa: E402
from dji_area import store  # noqa: E402
from dji_area import v4 as v4mod  # noqa: E402

BUNDLE_ID = 'DJI_AREA_BLOCK1B_002'
MU_M2 = 2000.0 / 3.0

REQUIRED_TABLES = ('dji_area_calculations', 'dji_field_attributions',
                   'dji_v4_summaries', 'dji_land_revisions',
                   'dji_source_revisions', 'dji_land_geometries')

# Паспортные пределы T40: рабочая скорость распыления до 7 м/с, максимальная
# скорость полёта 10 м/с. Порог физический, не подобранный.
SPRAY_SPEED_MPS = 7.0
MAX_FLIGHT_SPEED_MPS = 10.0

# Вылет полезен для опыта S против U только если они заметно расходятся: на
# обычных параллельных проходах обе гипотезы дают одно число. Порог
# диагностический -- он ничего не исключает из выгрузки, только помечает.
DISCRIMINATING_S_OVER_U = 1.15


def log(msg):
    """Консоль -- только ASCII (правило проекта)."""
    sys.stdout.write(msg + '\n')
    sys.stdout.flush()


def _git_commit():
    """Коммит рабочей копии инструмента. Без него bundle не привязан к коду."""
    try:
        import subprocess
        out = subprocess.run(['git', '-C', ROOT, 'rev-parse', 'HEAD'],
                             capture_output=True, text=True, timeout=20)
        return out.stdout.strip() or None
    except Exception:                             # noqa: BLE001 -- best effort
        return None


def sha256_file(path):
    h = hashlib.sha256()
    with open(path, 'rb') as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b''):
            h.update(chunk)
    return h.hexdigest()


def connect_ro(db_path):
    if not os.path.isfile(db_path):
        log('ERROR: database not found: %s' % db_path)
        sys.exit(2)
    uri = 'file:%s?mode=ro' % db_path.replace('?', '%3f').replace('#', '%23')
    con = sqlite3.connect(uri, uri=True, isolation_level=None)
    con.row_factory = sqlite3.Row
    return con


def require_tables(con):
    have = {r[0] for r in con.execute(
        "SELECT name FROM sqlite_master WHERE type='table'")}
    missing = [t for t in REQUIRED_TABLES if t not in have]
    if missing:
        log('ERROR: required tables missing: %s' % ', '.join(missing))
        log('       DJI_AREA_EVIDENCE_001 migration is not applied here.')
        sys.exit(1)


def rows(con, sql, params=()):
    return [dict(r) for r in con.execute(sql, params).fetchall()]


# [REASON]: `superseded_at IS NULL` НЕ выделяет одну строку. store.insert_calculation
# закрывает прежнюю строку только В ПРЕДЕЛАХ СВОЕЙ версии алгоритма, поэтому после
# подъёма impl-2 -> impl-3 в базе одновременно живут ДВЕ текущие строки одного
# вылета, и выборка без фильтра версии вернула бы обе. Соседний потребитель
# (tools/dji_area_validation_pack.py:127) фильтрует; здесь этого не было.
# То же для атрибуции поля и её собственной версии резолвера.
#
# [REASON]: `dji_v4_summaries.flight_id` НЕ уникален -- уникален
# `source_revision_id`, а у вылета может быть несколько ревизий V4. Связь
# через flight_id либо размножила бы строки, либо подмешала сводку ЧУЖОЙ
# ревизии; именно на forensic плоских записей это исказило бы вывод сильнее
# всего. Единственная правильная связь -- та, которую расчёт сам и записал.
FLIGHTS_SQL = """
SELECT c.flight_id, c.hardware_id, c.start_at_utc, c.end_at_utc,
       c.report_start_date, c.raw_area_m2, c.corrected_recorded_area_m2,
       c.counter_observed_delta_m2, c.controller_delta_area_m2,
       c.area_status, c.area_confidence, c.aggregation_eligibility,
       c.anomaly_flags_json, c.application_activity,
       c.application_channel_quality, c.area_algorithm_version,
       c.v4_summary_id, c.calculation_input_hash,
       f.field_attribution_tier, f.field_attribution_method,
       f.field_land_uuid, f.linked_land_uuid, f.geometry_holder_land_uuid,
       f.geometry_md5 AS attribution_geometry_md5,
       f.land_snapshot_id, f.land_revision_id,
       f.historical_geometry_available, f.historical_geometry_sha256,
       f.field_name_at_snapshot, f.field_input_hash,
       e.v4_revision_id, e.route_revision_id, e.list_spray_width,
       e.v4_identity_status, e.route_identity_status,
       e.list_start_ts, e.list_end_ts,
       v.id AS v4_summary_row_id, v.source_revision_id AS v4_summary_source_id,
       v.span_s, v.frame_count, v.application_frames,
       v.moving_application_distance_m, v.width_min, v.width_max,
       v.parser_version AS v4_summary_parser_version
FROM dji_area_calculations AS c
LEFT JOIN dji_field_attributions AS f
       ON f.flight_id = c.flight_id AND f.superseded_at IS NULL
      AND f.field_resolver_version = ?
LEFT JOIN dji_flight_evidence AS e ON e.flight_id = c.flight_id
LEFT JOIN dji_v4_summaries AS v ON v.id = c.v4_summary_id
WHERE c.superseded_at IS NULL AND c.report_start_date = ?
  AND c.area_algorithm_version = ?
ORDER BY c.hardware_id, c.start_at_utc
"""


def duration_s(rec):
    a, b = rec.get('list_start_ts'), rec.get('list_end_ts')
    if isinstance(a, (int, float)) and isinstance(b, (int, float)) and b > a:
        return float(b - a)
    return rec.get('span_s')


def min_required_speed(rec):
    """Минимально необходимая средняя скорость, чтобы покрыть RAW.

    [REASON]: знаменатель -- ВЕСЬ интервал записи, включая взлёт, перелёт,
    развороты и посадку, когда распыления нет. Значит настоящая скорость на
    проходах была ВЫШЕ этой. Поэтому величина работает только в одну
    сторону: превышение физического предела фальсифицирует запись, а
    значение ниже предела не подтверждает ничего.
    """
    raw, width, dur = rec.get('raw_area_m2'), rec.get('width_max'), duration_s(rec)
    if not raw or not width or not dur or dur <= 0:
        return None
    return float(raw) / (float(width) * dur)


def read_revision_body(con, root, revision_id):
    if not revision_id:
        return None, None, 'NO_REVISION'
    row = con.execute('SELECT * FROM dji_source_revisions WHERE id=?',
                      (revision_id,)).fetchone()
    if row is None:
        return None, None, 'REVISION_ROW_MISSING'
    try:
        body = store.read_body(root, row)
    except Exception as exc:                      # noqa: BLE001 -- named below
        return None, dict(row), 'BODY_UNREADABLE:%s' % type(exc).__name__
    return body, dict(row), None


def write_json(out_dir, name, payload):
    path = os.path.join(out_dir, name)
    with open(path, 'w', encoding='utf-8') as fh:
        json.dump(payload, fh, ensure_ascii=False, indent=2, sort_keys=True,
                  default=str)
    log('  wrote %s (%d bytes)' % (name, os.path.getsize(path)))


def write_csv(out_dir, name, records):
    path = os.path.join(out_dir, name)
    keys = []
    for rec in records:
        for k in rec:
            if k not in keys:
                keys.append(k)
    with open(path, 'w', encoding='utf-8', newline='') as fh:
        if keys:
            w = csv.DictWriter(fh, fieldnames=keys, extrasaction='ignore')
            w.writeheader()
            for rec in records:
                w.writerow(rec)
    log('  wrote %s (%d rows)' % (name, len(records)))


def save_blob(blob_dir, kind, key, body, declared_sha=None, declared_md5=None):
    """Сохранить неизменяемое тело и вернуть запись манифеста с проверкой."""
    sha = hashlib.sha256(body).hexdigest()
    md5 = hashlib.md5(body).hexdigest()
    name = '%s-%s.bin' % (kind, sha[:16])
    with open(os.path.join(blob_dir, name), 'wb') as fh:
        fh.write(body)
    return {'kind': kind, 'key': key, 'file': 'blobs/' + name,
            'size_bytes': len(body), 'sha256': sha, 'md5': md5,
            'declared_sha256': declared_sha, 'declared_md5': declared_md5,
            'sha256_matches': (None if declared_sha is None
                               else declared_sha == sha),
            'md5_matches': (None if declared_md5 is None
                            else declared_md5 == md5)}


def geojson_features(result, flight_id):
    plane = result.get('plane')
    feats = []
    for seg in result.get('work_segments') or ():
        a = plane.latlon(seg.ax, seg.ay)
        b = plane.latlon(seg.bx, seg.by)
        feats.append({'type': 'Feature',
                      'properties': {'kind': 'application',
                                     'width_m': seg.width_m,
                                     'flight_id': flight_id},
                      'geometry': {'type': 'LineString',
                                   'coordinates': [[a[1], a[0]], [b[1], b[0]]]}})
    return feats


def main():
    ap = argparse.ArgumentParser(description='DJI-AREA block 1B read-only bundle')
    ap.add_argument('--db', required=True)
    ap.add_argument('--date', required=True, help='report_start_date, YYYY-MM-DD')
    ap.add_argument('--hardware', action='append', required=True,
                    help='flight controller SN; repeat for several machines')
    ap.add_argument('--out', required=True)
    ap.add_argument('--allow-existing-out', action='store_true',
                    help='reuse a non-empty output directory (off by default)')
    ap.add_argument('--algorithm-version', default=AREA_ALGORITHM_VERSION,
                    help='area_algorithm_version to read (default: current)')
    ap.add_argument('--field-resolver-version', default=FIELD_RESOLVER_VERSION,
                    help='field_resolver_version to read (default: current)')
    args = ap.parse_args()

    started = datetime.utcnow().replace(microsecond=0).isoformat() + 'Z'
    log(BUNDLE_ID)
    log('db  : %s' % args.db)
    log('date: %s' % args.date)
    log('hw  : %s' % ', '.join(args.hardware))

    # [REASON]: перезапись каталога стирает предыдущую выгрузку молча, и
    # смешанный каталог с файлами двух прогонов невозможно отличить от
    # целого. Bundle -- доказательство; он либо новый, либо не создаётся.
    if os.path.isdir(args.out) and os.listdir(args.out) \
            and not args.allow_existing_out:
        log('ERROR: output directory exists and is not empty: %s' % args.out)
        log('       choose a new directory, or pass --allow-existing-out.')
        sys.exit(1)

    con = connect_ro(args.db)
    require_tables(con)
    root = store.source_root(args.db)
    blob_dir = os.path.join(args.out, 'blobs')
    os.makedirs(blob_dir, exist_ok=True)

    # [REASON]: одна read-транзакция на весь сбор. Иначе между выборкой
    # вылетов и выборкой ревизий каталога живая база успевает измениться, и
    # bundle описывает два разных состояния как одно.
    con.execute('BEGIN')
    try:
        wanted = set(args.hardware)
        flights = [r for r in rows(con, FLIGHTS_SQL,
                                   (args.field_resolver_version, args.date,
                                    args.algorithm_version))
                   if r['hardware_id'] in wanted]
        log('flights: %d  (algorithm %s)' % (len(flights),
                                             args.algorithm_version))
        seen_ids = [r['flight_id'] for r in flights]
        if len(set(seen_ids)) != len(seen_ids):
            log('ERROR: duplicate flight rows returned -- the version filter '
                'did not isolate one calculation per flight.')
            sys.exit(1)
        if not flights:
            log('NOTE: no rows for this algorithm version. Either the day was '
                'never recalculated with it, or --algorithm-version is wrong.')
        for hw in sorted(wanted):
            sel = [r for r in flights if r['hardware_id'] == hw]
            # [REASON]: NULL здесь никогда не ноль (dji_area/__init__.py).
            # Сложив NULL как 0, дневной итог машины стал бы меньше правды и
            # прочитался бы как измеренный.
            known = [r['raw_area_m2'] for r in sel
                     if r['raw_area_m2'] is not None]
            total = sum(known)
            log('  %s : n=%d RAW=%.0f m2 = %.4f ha  (records without RAW: %d)'
                % (hw, len(sel), total, total / 10000.0, len(sel) - len(known)))

        # ── 1. ТОЧНЫЕ ревизии контуров, на которые ссылается атрибуция ────
        rev_ids = sorted({r['land_revision_id'] for r in flights
                          if r['land_revision_id']})
        lands = []
        if rev_ids:
            marks = ','.join('?' * len(rev_ids))
            lands = rows(con, 'SELECT * FROM dji_land_revisions WHERE id IN (%s)'
                              % marks, rev_ids)
        # Прочие ревизии тех же участков -- только как справка о вариантах,
        # ЯВНО отделённая от той, что была использована.
        uuids = sorted({u for r in flights
                        for u in (r['field_land_uuid'], r['linked_land_uuid'],
                                  r['geometry_holder_land_uuid']) if u})
        other = []
        if uuids:
            marks = ','.join('?' * len(uuids))
            other = rows(con, 'SELECT * FROM dji_land_revisions '
                              'WHERE land_uuid IN (%s) ORDER BY land_uuid, id'
                              % marks, uuids)
        used_ids = {L['id'] for L in lands}
        other = [L for L in other if L['id'] not in used_ids]

        for L in lands + other:
            for src, dst in (('total_area_raw', 'total_area_ha'),
                             ('work_area_raw', 'work_area_ha'),
                             ('obstacle_area_raw', 'obstacle_area_ha')):
                v = L.get(src)
                L[dst] = (float(v) * MU_M2 / 10000.0) if v is not None else None
            t, w, o = (L['total_area_ha'], L['work_area_ha'],
                       L['obstacle_area_ha'])
            L['total_minus_obstacle_ha'] = (
                None if (t is None or o is None) else t - o)
            # [REASON]: опубликованная формула -- Task = Field - Obstacle -
            # Safety Margin. Проверив только Field - Obstacle, мы бы назвали
            # проверенной формулу, у которой не проверено третье слагаемое.
            # Поэтому остаток выносится отдельно и НЕ называется safety
            # margin: чем он является, ещё предстоит доказать.
            L['unexplained_exclusion_ha'] = (
                None if (t is None or o is None or w is None) else t - o - w)
            L['revision_role'] = ('USED_BY_ATTRIBUTION' if L['id'] in used_ids
                                  else 'OTHER_REVISION_SAME_UUID')
            L.pop('raw_json', None)     # тело уезжает отдельным blob'ом
        log('land revisions: %d used by attribution, %d other for the same uuid(s)'
            % (len(lands), len(other)))

        # ── 2. историческая идентичность контура ─────────────────────────
        vintage = []
        for r in flights:
            vintage.append({
                'flight_id': r['flight_id'],
                'field_attribution_tier': r['field_attribution_tier'],
                'field_land_uuid': r['field_land_uuid'],
                'land_snapshot_id': r['land_snapshot_id'],
                'land_revision_id': r['land_revision_id'],
                'attribution_geometry_md5': r['attribution_geometry_md5'],
                'historical_geometry_available':
                    r['historical_geometry_available'],
                'historical_geometry_sha256': r['historical_geometry_sha256'],
                # Явный вывод, а не молчаливое использование сегодняшнего
                # полигона там, где исторический не доказан.
                'polygon_vintage': ('HISTORICAL_PROVEN'
                                    if r['historical_geometry_available']
                                    else 'UNKNOWN_CURRENT_SNAPSHOT_ONLY'),
            })
        unknown_vintage = sum(1 for v in vintage
                              if v['polygon_vintage'] != 'HISTORICAL_PROVEN')
        log('polygon vintage: %d of %d flights have NO proven historical geometry'
            % (unknown_vintage, len(vintage)))

        # ── 3. forensic ──────────────────────────────────────────────────
        by_hw = {}
        for r in flights:
            by_hw.setdefault(r['hardware_id'], []).append(r)
        forensic = []
        for hw, group in by_hw.items():
            for i, r in enumerate(group):
                speed = min_required_speed(r)
                repeat_of = None
                for j in range(max(0, i - 3), i):
                    if r['raw_area_m2'] and r['raw_area_m2'] == group[j]['raw_area_m2']:
                        repeat_of = group[j]['flight_id']
                        break
                if r['area_status'] == 'COUNTER_FLAT_RAW_OVERSTATED' \
                        or repeat_of is not None \
                        or (speed is not None and speed > SPRAY_SPEED_MPS):
                    forensic.append({
                        'flight_id': r['flight_id'], 'hardware_id': hw,
                        'start_at_utc': r['start_at_utc'],
                        'area_status': r['area_status'],
                        'raw_area_m2': r['raw_area_m2'],
                        'duration_s': duration_s(r),
                        'width_max': r['width_max'],
                        'min_required_speed_m_s': speed,
                        # Falsification работает ТОЛЬКО в эту сторону.
                        'falsified_by_physics': (
                            None if speed is None
                            else speed > MAX_FLIGHT_SPEED_MPS),
                        'above_rated_spray_speed': (
                            None if speed is None else speed > SPRAY_SPEED_MPS),
                        'exact_raw_repeat_of': repeat_of,
                        'application_activity': r['application_activity'],
                        'moving_application_distance_m':
                            r['moving_application_distance_m'],
                    })
        log('forensic candidates (flat / exact repeat / above rated speed): %d'
            % len(forensic))

        # ── 3a. исторические геометрии контуров, до расчёта покрытия ─────
        # [REASON]: контур нужен САМОМУ расчёту, а не только отчёту: без него
        # «внутри поля» и «снаружи поля» не существуют как величины. Поэтому
        # геометрия читается ДО покрытия и берётся по той ревизии, на которую
        # ссылается атрибуция ЭТОГО вылета, а не по последней для участка.
        geometry_by_revision = {}
        for L in lands:
            md5 = L.get('geometry_md5')
            if not md5:
                geometry_by_revision[L['id']] = (None, 'NO_GEOMETRY_MD5')
                continue
            row = con.execute('SELECT * FROM dji_land_geometries '
                              'WHERE content_md5=?', (md5,)).fetchone()
            if row is None:
                geometry_by_revision[L['id']] = (None, 'GEOMETRY_NOT_CAPTURED')
                continue
            try:
                doc = json.loads(bytes(row['body_blob']).decode('utf-8'))
            except (ValueError, UnicodeDecodeError) as exc:
                geometry_by_revision[L['id']] = (
                    None, 'GEOMETRY_UNPARSED:%s' % type(exc).__name__)
                continue
            geometry_by_revision[L['id']] = (doc, None)

        # ── 4. покрытие + выгрузка неизменяемых тел ──────────────────────
        manifest_blobs, coverage_rows = [], []
        geo = {'type': 'FeatureCollection', 'features': []}
        seen_blob = set()
        for r in flights:
            base = {'flight_id': r['flight_id'], 'hardware_id': r['hardware_id'],
                    'start_at_utc': r['start_at_utc'],
                    'field_land_uuid': r['field_land_uuid'],
                    'land_revision_id': r['land_revision_id'],
                    'raw_area_m2': r['raw_area_m2'],
                    'area_status': r['area_status'],
                    'v4_summary_id': r['v4_summary_id']}

            for kind, rev_id in (('route', r['route_revision_id']),
                                 ('v4', r['v4_revision_id'])):
                body, row, why = read_revision_body(con, root, rev_id)
                if body is None:
                    continue
                if rev_id not in seen_blob:
                    seen_blob.add(rev_id)
                    manifest_blobs.append(save_blob(
                        blob_dir, kind, 'revision:%d' % rev_id, body,
                        declared_sha=row.get('sha256')))

            body, row, why = read_revision_body(con, root, r['v4_revision_id'])
            if body is None:
                base.update({'coverage_status': 'NO_V4', 'reason': why})
                coverage_rows.append(base)
                continue
            try:
                frames = v4mod.decode_v4(body).frames
            except v4mod.V4DecodeError as exc:
                base.update({'coverage_status': 'NO_V4',
                             'reason': 'BODY_UNDECODABLE:%s' % exc})
                coverage_rows.append(base)
                continue

            quality = r['application_channel_quality'] or 'UNKNOWN'
            doc, geo_why = geometry_by_revision.get(
                r['land_revision_id'], (None, 'NO_ATTRIBUTED_REVISION'))
            # Историческая геометрия не доказана -> контур не подставляется
            # вовсе. Сентябрьская версия полигона не имеет права стать
            # геометрией августовской работы.
            if not r['historical_geometry_available']:
                doc, geo_why = None, 'HISTORICAL_GEOMETRY_NOT_PROVEN'
            base['contour_reason'] = geo_why
            try:
                res = cov.coverage_from_v4(frames, quality,
                                           land_geometry_document=doc)
            except cov.CoverageUnavailable as exc:
                base.update({'coverage_status': 'UNAVAILABLE',
                             'reason': exc.reason})
                coverage_rows.append(base)
                continue
            except Exception as exc:             # noqa: BLE001 -- см. REASON
                # [REASON]: bundle собирается один раз, руками владельца, на
                # машине, до которой сессии не дотянуться. Нештатная ошибка
                # на ОДНОМ вылете не имеет права стоить всей поездки на
                # сервер: вылет помечается FAILED вместе с типом и текстом
                # ошибки, остальные считаются. Молчать здесь нельзя -- в
                # сводке появляется WARNING, а строка видна в coverage.csv.
                base.update({'coverage_status': 'FAILED',
                             'reason': '%s: %s' % (type(exc).__name__, exc)})
                coverage_rows.append(base)
                continue
            base.update({'coverage_status': res['status'],
                         'reason': res.get('reason')})
            for key in ('unique_application_ha',
                        'unique_application_inside_field_ha',
                        'application_outside_field_ha',
                        'swath_integral_ha', 's_minus_u_ha',
                        'repeated_application_ha', 's_over_u',
                        'painted_swath_total_ha', 'flown_not_applied_ha',
                        'repeated_share_of_unique',
                        'contour_ha', 'contour_status',
                        'cell_m', 'coarsened'):
                base[key] = res.get(key)
            widths = res.get('widths_used_m') or []
            base['widths_used_m'] = ','.join(str(x) for x in widths)
            # S считается по кадрам как сумма(ширина_i x расстояние_i); при
            # единственной ширине он ещё и точен в узком смысле.
            base['s_single_width'] = (len(widths) == 1)
            base['segment_length_m'] = json.dumps(
                res.get('segment_length_m') or {}, sort_keys=True)
            # [REASON]: движок МЕРИТ свою дискретизационную ошибку вторым
            # прогоном на вдвое грубой сетке, и его же докстринг говорит, что
            # число без погрешности читается как точное. Экспортёр её ронял.
            base['uncertainty_percent'] = json.dumps(
                res.get('uncertainty_percent') or {}, sort_keys=True)
            base['thresholds'] = json.dumps(res.get('thresholds') or {},
                                            sort_keys=True)
            base['discriminating'] = (res.get('s_over_u') is not None
                                      and res['s_over_u']
                                      > DISCRIMINATING_S_OVER_U)
            coverage_rows.append(base)
            geo['features'].extend(geojson_features(res, r['flight_id']))

        # геометрия контуров -- тоже неизменяемым телом
        md5s = sorted({L.get('geometry_md5') for L in lands
                       if L.get('geometry_md5')})
        geom_meta = []
        for md5 in md5s:
            row = con.execute('SELECT * FROM dji_land_geometries '
                              'WHERE content_md5=?', (md5,)).fetchone()
            if row is None:
                geom_meta.append({'content_md5': md5, 'status': 'NOT_CAPTURED'})
                continue
            row = dict(row)
            body = bytes(row.pop('body_blob'))
            manifest_blobs.append(save_blob(
                blob_dir, 'land_geometry', 'md5:%s' % md5, body,
                declared_sha=row.get('sha256'), declared_md5=md5))
            row['status'] = 'CAPTURED'
            geom_meta.append(row)
        log('blobs exported: %d (route/v4/land geometry), land geometries: %d'
            % (len(manifest_blobs), len(geom_meta)))

        done = [r for r in coverage_rows if r.get('coverage_status') == 'ESTIMATE']
        disc = [r for r in coverage_rows if r.get('discriminating')]
        log('coverage computed: %d of %d ; discriminating (S/U > %.2f): %d'
            % (len(done), len(coverage_rows), DISCRIMINATING_S_OVER_U,
               len(disc)))
        failed = [r for r in coverage_rows
                  if r.get('coverage_status') == 'FAILED']
        if failed:
            log('WARNING: %d flight(s) failed coverage and are named in '
                'coverage.csv, the bundle is still complete' % len(failed))
        if done:
            log('  sum unique application: %.4f ha ; sum swath integral: %.4f ha'
                % (sum(r.get('unique_application_ha') or 0 for r in done),
                   sum(r.get('swath_integral_ha') or 0 for r in done)))
        bad = [b for b in manifest_blobs if b['sha256_matches'] is False
               or b['md5_matches'] is False]
        if bad:
            log('WARNING: %d blob(s) do not match their declared digest' % len(bad))
    finally:
        con.execute('ROLLBACK')

    write_json(args.out, 'manifest.json', {
        'bundle_id': BUNDLE_ID, 'started_utc': started,
        'finished_utc': datetime.utcnow().replace(microsecond=0).isoformat() + 'Z',
        'report_date': args.date, 'hardware': sorted(wanted),
        'db_path': args.db, 'source_root': root,
        'exporter_sha256': sha256_file(os.path.abspath(__file__)),
        'repo_commit': _git_commit(),
        'coverage_module_sha256': sha256_file(
            os.path.join(ROOT, 'dji_area', 'coverage.py')),
        'sqlite_version': sqlite3.sqlite_version,
        'python_version': sys.version.split()[0],
        'v4_parser_version': v4mod.V4_PARSER_VERSION,
        # [REASON]: порог, которого нет в отчёте, -- скрытое допущение. Через
        # месяц никто не вспомнит, что разрывом считался кадр через 3 с.
        'coverage_thresholds': {
            'max_frame_gap_s': cov.MAX_FRAME_GAP_S,
            'max_ground_speed_mps': cov.MAX_GROUND_SPEED_MPS,
            'discriminating_s_over_u': DISCRIMINATING_S_OVER_U,
            'spray_speed_mps': SPRAY_SPEED_MPS,
            'max_flight_speed_mps': MAX_FLIGHT_SPEED_MPS},
        'area_algorithm_version': AREA_ALGORITHM_VERSION,
        'field_resolver_version': FIELD_RESOLVER_VERSION,
        'metric_name': 'V4_APPLICATION_COVERAGE_ESTIMATE',
        'not_billable': True,
        'blobs': manifest_blobs,
        'source_revision_ids': sorted(seen_blob),
        'land_revision_ids_used': sorted(L['id'] for L in lands),
        'counts': {'flights': len(flights),
                   'land_revisions_used': len(lands),
                   'land_revisions_other': len(other),
                   'flights_without_proven_historical_geometry': unknown_vintage,
                   'forensic': len(forensic), 'coverage_ok': len(done),
                   'discriminating': len(disc), 'blobs': len(manifest_blobs)},
        'note': 'read-only bundle; one read transaction; no database writes',
    })
    write_csv(args.out, 'flights.csv', flights)
    write_csv(args.out, 'land_revisions.csv', lands + other)
    write_csv(args.out, 'polygon_vintage.csv', vintage)
    write_csv(args.out, 'forensic_flat_and_repeats.csv', forensic)
    write_csv(args.out, 'coverage.csv', coverage_rows)
    write_json(args.out, 'coverage.json', coverage_rows)
    write_json(args.out, 'land_geometries.json', geom_meta)
    write_json(args.out, 'application_footprint.geojson', geo)

    con.close()
    log('OK: bundle complete -> %s' % args.out)
    return 0


if __name__ == '__main__':
    sys.exit(main())
