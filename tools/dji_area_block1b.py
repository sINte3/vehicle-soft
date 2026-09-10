# -*- coding: utf-8 -*-
"""tools/dji_area_block1b.py -- evidence bundle этапа 1B. ТОЛЬКО ЧТЕНИЕ.

Собирает за ОДИН прогон всё, что нужно для первого независимого числа
площади по названным бортам и суткам:

  1. вылеты борта за отчётный день с привязкой к КОНКРЕТНОМУ ``land_uuid``
     (не по имени поля: имена в каталоге DJI не уникальны, на одном участке
     их встречается до девяти);
  2. ``totalArea`` / ``workArea`` / ``totalObstacleArea`` этих контуров в му и
     в гектарах, плюс проверка соотношения ``total - obstacle = work``;
  3. forensic по записям с плоским счётчиком: длительность, подразумеваемая
     скорость, совпадение скаляра с соседней записью;
  4. ``V4_APPLICATION_COVERAGE_ESTIMATE`` по кадрам V4 -- уникальное
     покрытие, повторное покрытие, вынос за контур, холостой пролёт,
     неизвестное;
  5. S = сумма(ширина x расстояние) против U = уникальное объединение, с
     явной пометкой вылетов, на которых это сравнение вообще способно
     что-то различить (S/U заметно больше 1).

БАЗУ НЕ МЕНЯЕТ. Открывает её через ``file:...?mode=ro``, не импортирует
Flask и не вызывает ``create_app()``. Ни одной записи, ни одного ALTER.

ЧЕГО РЕЗУЛЬТАТ НЕ УТВЕРЖДАЕТ. Это НЕ площадь к счёту заказчику и НЕ
доказательство того, что препарат долетел до земли: ширина берётся
записанная бортом, а её соответствие реальной полосе осаждения проверяется
только полевой калибровкой (NY/T 3213-2023).

ЗАПУСК (PowerShell, по одной команде на строку -- в PowerShell нет &&)

  cd C:\\transport-report-staging
  & "C:\\Program Files\\Python314\\python.exe" tools\\dji_area_block1b.py --db "C:\\transport-report-staging\\instance\\transport.db" --date 2026-08-18 --hardware 1581F574B2387001009R --hardware 1581F574B235W00100Q5 --out "C:\\transport-report-staging\\_block1b_out"

КОДЫ ВОЗВРАТА
  0  bundle собран
  1  предусловие не выполнено (нет таблиц модели -- миграция не применена)
  2  база не найдена (файл НЕ создаётся)

ОТКАТ
  Кода: удалить этот файл, его никто не импортирует.
  Данных: отката не требуется -- записей не производится.
"""

import argparse
import csv
import json
import os
import sqlite3
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from dji_area import coverage as cov  # noqa: E402
from dji_area import store  # noqa: E402
from dji_area import v4 as v4mod  # noqa: E402

BUNDLE_ID = 'DJI_AREA_BLOCK1B_001'
MU_M2 = 2000.0 / 3.0

REQUIRED_TABLES = ('dji_area_calculations', 'dji_field_attributions',
                   'dji_v4_summaries', 'dji_land_revisions',
                   'dji_source_revisions')

# Паспортные пределы T40: рабочая скорость распыления до 7 м/с, максимальная
# скорость полёта 10 м/с. Порог физический, не подобранный.
SPRAY_SPEED_MPS = 7.0
MAX_FLIGHT_SPEED_MPS = 10.0


def log(msg):
    """Консоль -- только ASCII (правило проекта)."""
    sys.stdout.write(msg + '\n')
    sys.stdout.flush()


def connect_ro(db_path):
    if not os.path.isfile(db_path):
        log('ERROR: database not found: %s' % db_path)
        sys.exit(2)
    uri = 'file:%s?mode=ro' % db_path.replace('?', '%3f').replace('#', '%23')
    con = sqlite3.connect(uri, uri=True)
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


FLIGHTS_SQL = """
SELECT c.flight_id, c.hardware_id, c.start_at_utc, c.end_at_utc,
       c.report_start_date, c.raw_area_m2, c.corrected_recorded_area_m2,
       c.counter_observed_delta_m2, c.controller_delta_area_m2,
       c.area_status, c.area_confidence, c.aggregation_eligibility,
       c.anomaly_flags_json, c.application_activity,
       c.application_channel_quality, c.area_algorithm_version,
       f.field_attribution_tier, f.field_land_uuid, f.linked_land_uuid,
       f.geometry_holder_land_uuid, f.field_name_at_snapshot,
       e.v4_revision_id, e.list_spray_width, e.v4_identity_status,
       e.list_start_ts, e.list_end_ts,
       v.span_s, v.frame_count, v.application_frames,
       v.moving_application_distance_m, v.width_min, v.width_max
FROM dji_area_calculations AS c
LEFT JOIN dji_field_attributions AS f
       ON f.flight_id = c.flight_id AND f.superseded_at IS NULL
LEFT JOIN dji_flight_evidence AS e ON e.flight_id = c.flight_id
LEFT JOIN dji_v4_summaries AS v ON v.flight_id = c.flight_id
WHERE c.superseded_at IS NULL AND c.report_start_date = ?
ORDER BY c.hardware_id, c.start_at_utc
"""


def duration_s(rec):
    a, b = rec.get('list_start_ts'), rec.get('list_end_ts')
    if isinstance(a, (int, float)) and isinstance(b, (int, float)) and b > a:
        return float(b - a)
    return rec.get('span_s')


def implied_speed(rec):
    """Скорость, которая понадобилась бы, чтобы покрыть RAW за это время."""
    raw, width, dur = rec.get('raw_area_m2'), rec.get('width_max'), duration_s(rec)
    if not raw or not width or not dur or dur <= 0:
        return None
    return float(raw) / (float(width) * dur)


def load_v4_frames(con, root, revision_id):
    if not revision_id:
        return None, 'NO_V4_REVISION'
    row = con.execute('SELECT * FROM dji_source_revisions WHERE id=?',
                      (revision_id,)).fetchone()
    if row is None:
        return None, 'REVISION_ROW_MISSING'
    try:
        body = store.read_body(root, row)
    except Exception as exc:                     # noqa: BLE001 -- named below
        return None, 'BODY_UNREADABLE:%s' % type(exc).__name__
    try:
        return v4mod.decode_v4(body).frames, None
    except v4mod.V4DecodeError as exc:
        return None, 'BODY_UNDECODABLE:%s' % exc


def write_json(out_dir, name, payload):
    path = os.path.join(out_dir, name)
    with open(path, 'w', encoding='utf-8') as fh:
        json.dump(payload, fh, ensure_ascii=False, indent=2, sort_keys=True,
                  default=str)
    log('  wrote %s (%d bytes)' % (name, os.path.getsize(path)))


def write_csv(out_dir, name, records):
    path = os.path.join(out_dir, name)
    with open(path, 'w', encoding='utf-8', newline='') as fh:
        if records:
            w = csv.DictWriter(fh, fieldnames=list(records[0].keys()))
            w.writeheader()
            for rec in records:
                w.writerow(rec)
    log('  wrote %s (%d rows)' % (name, len(records)))


def geojson_of(result, name):
    """Полоса подтверждённого применения как GeoJSON -- для карты evidence."""
    plane = result.get('plane')
    feats = []
    for seg in result.get('work_segments') or ():
        a = plane.latlon(seg.ax, seg.ay)
        b = plane.latlon(seg.bx, seg.by)
        feats.append({'type': 'Feature',
                      'properties': {'kind': 'application',
                                     'width_m': seg.width_m,
                                     'flight_id': name},
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
    args = ap.parse_args()

    log(BUNDLE_ID)
    log('db  : %s' % args.db)
    log('date: %s' % args.date)
    log('hw  : %s' % ', '.join(args.hardware))

    con = connect_ro(args.db)
    require_tables(con)
    root = store.source_root(args.db)
    os.makedirs(args.out, exist_ok=True)

    wanted = set(args.hardware)
    flights = [r for r in rows(con, FLIGHTS_SQL, (args.date,))
               if r['hardware_id'] in wanted]
    log('flights: %d' % len(flights))
    for hw in sorted(wanted):
        sel = [r for r in flights if r['hardware_id'] == hw]
        total = sum(r['raw_area_m2'] or 0 for r in sel)
        log('  %s : n=%d RAW=%.0f m2 = %.4f ha'
            % (hw, len(sel), total, total / 10000.0))

    # ── 1. контуры по land_uuid ──────────────────────────────────────────
    uuids = sorted({u for r in flights
                    for u in (r['field_land_uuid'], r['linked_land_uuid'],
                              r['geometry_holder_land_uuid']) if u})
    lands = []
    if uuids:
        marks = ','.join('?' * len(uuids))
        lands = rows(con, 'SELECT land_uuid, name, serial_number, '
                          'total_area_raw, work_area_raw, obstacle_area_raw, '
                          'area_unit, geometry_md5, land_type, '
                          'updated_at_source, raw_sha256, last_seen_snapshot_id '
                          'FROM dji_land_revisions WHERE land_uuid IN (%s) '
                          'ORDER BY land_uuid, last_seen_snapshot_id' % marks,
                     uuids)
    for L in lands:
        for src, dst in (('total_area_raw', 'total_area_ha'),
                         ('work_area_raw', 'work_area_ha'),
                         ('obstacle_area_raw', 'obstacle_area_ha')):
            v = L.get(src)
            L[dst] = (float(v) * MU_M2 / 10000.0) if v is not None else None
        t, w, o = L['total_area_ha'], L['work_area_ha'], L['obstacle_area_ha']
        # Проверяет опубликованное DJI соотношение Task = Field - Obstacle - margin.
        L['total_minus_obstacle_ha'] = None if (t is None or o is None) else t - o
        L['work_minus_total_minus_obstacle_ha'] = (
            None if (w is None or L['total_minus_obstacle_ha'] is None)
            else w - L['total_minus_obstacle_ha'])
    log('land revisions: %d rows for %d uuid(s)' % (len(lands), len(uuids)))

    # ── 2. forensic плоских записей ──────────────────────────────────────
    by_hw = {}
    for r in flights:
        by_hw.setdefault(r['hardware_id'], []).append(r)
    forensic = []
    for hw, group in by_hw.items():
        for i, r in enumerate(group):
            speed = implied_speed(r)
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
                    'implied_speed_m_s': speed,
                    'over_spray_speed': (None if speed is None
                                         else speed > SPRAY_SPEED_MPS),
                    'over_max_flight_speed': (None if speed is None
                                              else speed > MAX_FLIGHT_SPEED_MPS),
                    'exact_raw_repeat_of': repeat_of,
                    'application_activity': r['application_activity'],
                    'moving_application_distance_m':
                        r['moving_application_distance_m'],
                })
    log('forensic candidates (flat / exact repeat / impossible speed): %d'
        % len(forensic))

    # ── 3. покрытие по V4 ────────────────────────────────────────────────
    coverage_rows, geo = [], {'type': 'FeatureCollection', 'features': []}
    for r in flights:
        frames, why = load_v4_frames(con, root, r['v4_revision_id'])
        base = {'flight_id': r['flight_id'], 'hardware_id': r['hardware_id'],
                'start_at_utc': r['start_at_utc'],
                'field_land_uuid': r['field_land_uuid'],
                'raw_area_m2': r['raw_area_m2'],
                'area_status': r['area_status']}
        if frames is None:
            base.update({'coverage_status': 'NO_V4', 'reason': why})
            coverage_rows.append(base)
            continue
        quality = r['application_channel_quality'] or 'UNKNOWN'
        try:
            res = cov.coverage_from_v4(frames, quality)
        except cov.CoverageUnavailable as exc:
            base.update({'coverage_status': 'UNAVAILABLE', 'reason': exc.reason})
            coverage_rows.append(base)
            continue
        base.update({'coverage_status': res['status'],
                     'reason': res.get('reason')})
        for key in ('unique_application_ha', 'swath_integral_ha',
                    'repeated_application_ha', 's_over_u', 'flown_swath_ha',
                    'cell_m'):
            base[key] = res.get(key)
        base['widths_used_m'] = ','.join(
            str(x) for x in (res.get('widths_used_m') or ()))
        base['segment_length_m'] = json.dumps(res.get('segment_length_m') or {},
                                              sort_keys=True)
        # Различающая подвыборка: обычные параллельные проходы бесполезны.
        base['discriminating'] = (res.get('s_over_u') is not None
                                  and res['s_over_u'] > 1.15)
        coverage_rows.append(base)
        geo['features'].extend(geojson_of(res, r['flight_id']))

    done = [r for r in coverage_rows if r.get('coverage_status') == 'ESTIMATE']
    disc = [r for r in coverage_rows if r.get('discriminating')]
    log('coverage computed: %d of %d ; discriminating (S/U > 1.15): %d'
        % (len(done), len(coverage_rows), len(disc)))
    if done:
        total_u = sum(r.get('unique_application_ha') or 0 for r in done)
        total_s = sum(r.get('swath_integral_ha') or 0 for r in done)
        log('  sum unique application: %.4f ha ; sum swath integral: %.4f ha'
            % (total_u, total_s))

    write_json(args.out, 'meta.json', {
        'bundle_id': BUNDLE_ID, 'report_date': args.date,
        'hardware': sorted(wanted), 'db_path': args.db,
        'source_root': root, 'sqlite_version': sqlite3.sqlite_version,
        'v4_parser_version': v4mod.V4_PARSER_VERSION,
        'metric_name': 'V4_APPLICATION_COVERAGE_ESTIMATE',
        'not_billable': True,
        'counts': {'flights': len(flights), 'lands': len(lands),
                   'forensic': len(forensic), 'coverage_ok': len(done),
                   'discriminating': len(disc)},
        'note': 'read-only bundle; no database writes were performed',
    })
    write_csv(args.out, 'flights.csv', flights)
    write_csv(args.out, 'land_revisions.csv', lands)
    write_csv(args.out, 'forensic_flat_and_repeats.csv', forensic)
    write_csv(args.out, 'coverage.csv', coverage_rows)
    write_json(args.out, 'coverage.json', coverage_rows)
    write_json(args.out, 'application_footprint.geojson', geo)

    con.close()
    log('OK: bundle complete -> %s' % args.out)
    return 0


if __name__ == '__main__':
    sys.exit(main())
