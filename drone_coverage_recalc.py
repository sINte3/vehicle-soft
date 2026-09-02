# -*- coding: utf-8 -*-
"""drone_coverage_recalc.py -- пересчёт полезной площади дронов.

Мост между базой и чистым расчётом `drone_useful_area`. Здесь -- чтение
входа, группировка в работы, отбор контуров-кандидатов и запись результата;
геометрия и решение о статусе живут там и сюда не копируются.

БЕЗ FLASK И БЕЗ SQLALCHEMY. Только stdlib `sqlite3`.

[REASON]: этого требует устав. Любой скрипт с `from app import app` --
писатель, а не читатель: `create_app()` зовёт `db.create_all()` на импорте, и
инструмент пересчёта, запущенный посмотреть сухим прогоном, молча создал бы
недостающие таблицы на боевой базе. Тот же выбор сделан в миграциях и в
диагностике трека.

ПАМЯТЬ. Пересчёт идёт ПО ДНЯМ: в память попадают маршруты одного локального
дня, а не все за всё время. На пике сезона это 476 вылетов, то есть единицы
мегабайт точек; за год их 22 855, и одним запросом это было бы гигабайты.
Контуры-кандидаты отбираются рамкой в SQL, а не загрузкой справочника:
полигон весит около 7 КБ, а контуров 5 489.

ИДЕМПОТЕНТНОСТЬ. Строка результата опознаётся по (`unit_key`, `work_date`,
`group_index`), а решение «переписывать или нет» принимает
`inputs_fingerprint` -- отпечаток маршрутов, параметров, версии алгоритма и
выбранного контура. Повторный `--apply` на тех же входах не пишет ничего;
изменившийся маршрут, контур, параметр или версия дают новый расчёт.
"""

import json
import os
import sqlite3

from datetime import datetime, timedelta

import drone_useful_area as ua

# Локальная зона холдинга. Работа -- это локальные сутки, а не сутки UTC:
# вылет в 21:30 по Бухаре -- это уже следующие сутки UTC, и группировка по
# UTC разрезала бы вечернюю работу надвое.
LOCAL_UTC_OFFSET = timedelta(hours=5)

# Запас рамки при отборе контуров-кандидатов, в градусах (~165 м).
CONTOUR_BBOX_MARGIN_DEG = 0.0015

# Сколько контуров максимум уходит в геометрический разбор одной работы.
# Рамка отбирает КОРОТКИЙ СПИСОК; решает полигон (`choose_contour`).
CONTOUR_CANDIDATE_LIMIT = 8

# Источник контуров DJI в общем справочнике `field_contours`.
CONTOUR_SOURCE = 'dji'

STATUS_ORDER = (ua.READY_ESTIMATE, ua.PARTIAL_DATA, ua.DATA_UNAVAILABLE,
                ua.CONTOUR_AMBIGUOUS, ua.CONTOUR_NOT_MATCHED, ua.ROUTE_INVALID)


class RecalcError(Exception):
    """Пересчёт невозможен. Никогда не поднимается ради «странного» числа."""


def connect(db_path):
    """Открыть СУЩЕСТВУЮЩУЮ базу.

    [REASON]: `sqlite3.connect` создаёт пустой файл, когда его нет. Инструмент,
    молча заведший новую базу вместо отказа, отчитается о нуле работ и будет
    выглядеть успешным.
    """
    if not os.path.exists(db_path):
        raise RecalcError('database not found at %s - refusing to run' % db_path)
    con = sqlite3.connect(db_path)
    con.row_factory = sqlite3.Row
    return con


def _require_tables(con):
    names = {row[0] for row in con.execute(
        "SELECT name FROM sqlite_master WHERE type='table'")}
    missing = [name for name in ('drone_flights', 'drone_flight_routes',
                                 'drone_coverage_works', 'field_contours')
               if name not in names]
    if missing:
        raise RecalcError('the database is missing %s - run '
                          'migrate_drones_useful_area_001.py first'
                          % ', '.join(missing))


def local_day(started_at):
    """Локальный календарный день вылета по метке UTC."""
    return (started_at + LOCAL_UTC_OFFSET).date()


def _parse_dt(value):
    if isinstance(value, datetime):
        return value
    text = str(value)
    for shape in ('%Y-%m-%d %H:%M:%S.%f', '%Y-%m-%d %H:%M:%S',
                  '%Y-%m-%dT%H:%M:%S.%f', '%Y-%m-%dT%H:%M:%S',
                  '%Y-%m-%d %H:%M', '%Y-%m-%d'):
        try:
            return datetime.strptime(text, shape)
        except ValueError:
            continue
    raise RecalcError('unreadable timestamp %r' % text)


def unit_key_of(drone_unit_id, nickname_raw):
    """Ключ машины -- НЕ NULL никогда.

    [REASON]: у неопознанной машины `drone_unit_id` пуст, а SQLite считает
    NULL в UNIQUE различными: работа без машины заводила бы вторую строку
    каждым пересчётом, и сумма росла бы прогон за прогоном, а каждая строка
    выглядела бы правильной. Неопознанные при этом разделены по написанию
    ника, иначе две разные машины слились бы в одну работу.
    """
    if drone_unit_id is not None:
        return 'unit:%d' % int(drone_unit_id)
    text = (nickname_raw or '').strip()
    return 'nick:%s' % (text[:100] if text else 'UNKNOWN')


# ─── Чтение входа ────────────────────────────────────────────────────────────

def flights_of_day(con, day):
    """Вылеты одного ЛОКАЛЬНОГО дня вместе с их маршрутами, если те есть."""
    start_utc = datetime(day.year, day.month, day.day) - LOCAL_UTC_OFFSET
    end_utc = start_utc + timedelta(days=1)
    rows = con.execute(
        'SELECT f.id, f.dji_flight_id, f.drone_unit_id, f.nickname_raw, '
        '       f.started_at, f.area_ha, '
        '       r.points_json, r.spray_width_m, r.content_sha256, '
        '       r.mission_uuid '
        '  FROM drone_flights f '
        '  LEFT JOIN drone_flight_routes r ON r.drone_flight_id = f.id '
        ' WHERE f.started_at >= ? AND f.started_at < ? '
        ' ORDER BY f.started_at, f.id',
        (start_utc.strftime('%Y-%m-%d %H:%M:%S'),
         end_utc.strftime('%Y-%m-%d %H:%M:%S'))).fetchall()

    flights = []
    for row in rows:
        started = _parse_dt(row['started_at'])
        points = None
        if row['points_json']:
            try:
                points = json.loads(row['points_json'])
            except ValueError:
                # Нечитаемый JSON -- это НЕ «маршрута нет». Пустой список
                # доводит работу до ROUTE_INVALID, а не до тихого пропуска.
                points = []
        flights.append({
            'flight_row_id': row['id'],
            'flight_id': row['dji_flight_id'],
            'drone_unit_id': row['drone_unit_id'],
            'nickname_raw': row['nickname_raw'],
            'unit_key': unit_key_of(row['drone_unit_id'], row['nickname_raw']),
            'day': local_day(started).isoformat(),
            'start_ms': int(started.timestamp() * 1000),
            'area_ha': row['area_ha'],
            'points': points,
            'spray_width_m': row['spray_width_m'],
            'content_sha256': row['content_sha256'],
            'mission_uuid': row['mission_uuid'],
            'has_route': row['points_json'] is not None,
        })
    return flights


def days_with_flights(con, date_from, date_to):
    """Локальные дни периода, в которых вообще есть вылеты."""
    start_utc = (datetime(date_from.year, date_from.month, date_from.day)
                 - LOCAL_UTC_OFFSET)
    end_utc = (datetime(date_to.year, date_to.month, date_to.day)
               + timedelta(days=1) - LOCAL_UTC_OFFSET)
    rows = con.execute(
        'SELECT DISTINCT date(started_at, ?) AS d FROM drone_flights '
        ' WHERE started_at >= ? AND started_at < ? ORDER BY d',
        ('+5 hours', start_utc.strftime('%Y-%m-%d %H:%M:%S'),
         end_utc.strftime('%Y-%m-%d %H:%M:%S'))).fetchall()
    days = []
    for row in rows:
        if not row['d']:
            continue
        parsed = datetime.strptime(row['d'], '%Y-%m-%d').date()
        if date_from <= parsed <= date_to:
            days.append(parsed)
    return days


def contour_candidates(con, points, margin=CONTOUR_BBOX_MARGIN_DEG,
                       limit=CONTOUR_CANDIDATE_LIMIT):
    """Контуры, чья рамка накрывает маршруты работы.

    Рамка -- это ОТБОР, а не решение: какой контур настоящий, решает
    `choose_contour` по полигону. Прямоугольники соседних полей пересекаются
    сплошь и рядом, и «первый по рамке» -- запросто соседнее поле.
    """
    if not points:
        return []
    lats = [p[0] for p in points]
    lngs = [p[1] for p in points]
    rows = con.execute(
        'SELECT id, external_id, name, geometry_geojson '
        '  FROM field_contours '
        ' WHERE source = ? AND is_active = 1 '
        '   AND geometry_geojson IS NOT NULL '
        '   AND bbox_min_lat IS NOT NULL '
        '   AND bbox_min_lat <= ? AND bbox_max_lat >= ? '
        '   AND bbox_min_lng <= ? AND bbox_max_lng >= ? '
        ' ORDER BY id LIMIT ?',
        (CONTOUR_SOURCE, max(lats) + margin, min(lats) - margin,
         max(lngs) + margin, min(lngs) - margin, limit)).fetchall()

    candidates = []
    for row in rows:
        try:
            geojson = json.loads(row['geometry_geojson'])
        except ValueError:
            # Битая геометрия одного кандидата не мешает проверить остальных:
            # `choose_contour` записывает причину отказа и идёт дальше.
            continue
        candidates.append({'uuid': row['external_id'] or ('id:%d' % row['id']),
                           'contour_row_id': row['id'],
                           'name': row['name'], 'geojson': geojson})
    return candidates


# ─── Расчёт одного дня ───────────────────────────────────────────────────────

def compute_day(con, day, params=None):
    """[(идентичность, WorkCoverage, вспомогательное)] за один локальный день."""
    params = params or ua.PARAMS
    flights = flights_of_day(con, day)
    if not flights:
        return []

    # Группировка -- пространственная и только по вылетам, У КОТОРЫХ ЕСТЬ
    # маршрут: рамку вылета без геометрии построить не из чего.
    routed = [item for item in flights if item['points']]
    groups = ua.group_routes(routed)

    # Вылеты без маршрута всё равно принадлежат работе: они делают её вход
    # неполным, и это обязано быть видно. Приписываются к работе своей машины
    # и своего дня; если работ у машины в этот день несколько, попадают в
    # первую -- число вылетов без маршрута от этого не меняется.
    unrouted = {}
    for item in flights:
        if not item['points']:
            unrouted.setdefault(item['unit_key'], []).append(item)

    results = []
    seen_units = set()
    for unit_key, group_day, index, members in groups:
        candidates = contour_candidates(
            con, [point for member in members for point in member['points']])
        extra = []
        if index == 0 and unit_key not in seen_units:
            extra = unrouted.pop(unit_key, [])
            seen_units.add(unit_key)

        coverage = ua.compute_work(
            members, candidates, params=params,
            flights_total=len(members) + len(extra),
            mission_state=_mission_state(members))

        contour_row_id = None
        for candidate in candidates:
            if candidate['uuid'] == coverage.contour_uuid:
                contour_row_id = candidate['contour_row_id']
                break

        head = members[0]
        results.append({
            'unit_key': unit_key,
            'drone_unit_id': head['drone_unit_id'],
            'work_date': group_day,
            'group_index': index,
            'coverage': coverage,
            'contour_row_id': contour_row_id,
            'route_fingerprint': ua.route_fingerprint(
                [(m['flight_id'], m['content_sha256']) for m in members]),
            'inputs_fingerprint': ua.inputs_fingerprint(
                [(m['flight_id'], m['content_sha256']) for m in members],
                params=params, contour_key=coverage.contour_uuid),
            'dji_area_ha': _sum_area(members + extra),
        })

    # Машина, у которой в этот день НИ ОДНОГО маршрута нет вовсе: работа
    # существует, число назвать нельзя. Строка со статусом DATA_UNAVAILABLE --
    # это не шум, а единственное место, где видно, что маршруты не доехали.
    for unit_key, members in sorted(unrouted.items()):
        coverage = ua.compute_work([], [], params=params,
                                   flights_total=len(members))
        results.append({
            'unit_key': unit_key,
            'drone_unit_id': members[0]['drone_unit_id'],
            'work_date': members[0]['day'],
            'group_index': 0,
            'coverage': coverage,
            'contour_row_id': None,
            'route_fingerprint': ua.route_fingerprint([]),
            'inputs_fingerprint': ua.inputs_fingerprint([], params=params,
                                                        contour_key=None),
            'dji_area_ha': _sum_area(members),
        })
    return results


def _mission_state(members):
    """SHARED / MIXED / ABSENT -- описание, а не основание группировки."""
    values = {item.get('mission_uuid') for item in members
              if item.get('mission_uuid')}
    if not values:
        return 'ABSENT'
    if len(values) == 1 and all(item.get('mission_uuid') for item in members):
        return 'SHARED'
    return 'MIXED'


def _sum_area(members):
    values = [item['area_ha'] for item in members
              if isinstance(item['area_ha'], (int, float))
              and not isinstance(item['area_ha'], bool)]
    return round(sum(values), 4) if values else None


# ─── Запись ──────────────────────────────────────────────────────────────────

WRITE_COLUMNS = (
    'unit_key', 'drone_unit_id', 'work_date', 'group_index',
    'inputs_fingerprint', 'route_fingerprint', 'field_contour_id',
    'contour_status', 'estimated_useful_area_ha', 'partial_estimate_ha',
    'sum_independent_swaths_ha', 'swath_union_ha', 'clipped_all_ha',
    'contour_area_ha', 'uncertainty_percent', 'algorithm_version',
    'params_json', 'flights_total', 'routes_total', 'flights_without_route',
    'flights_without_width', 'flights_without_width_on_work', 'work_segments',
    'route_points', 'quality_status', 'quality_reason', 'dji_area_ha',
    'mission_state', 'computed_at')


def _row_values(item, now):
    coverage = item['coverage']
    return {
        'unit_key': item['unit_key'],
        'drone_unit_id': item['drone_unit_id'],
        'work_date': item['work_date'],
        'group_index': item['group_index'],
        'inputs_fingerprint': item['inputs_fingerprint'],
        'route_fingerprint': item['route_fingerprint'],
        'field_contour_id': item['contour_row_id'],
        'contour_status': coverage.contour_status,
        'estimated_useful_area_ha': coverage.estimated_useful_area_ha,
        'partial_estimate_ha': coverage.partial_estimate_ha,
        'sum_independent_swaths_ha': coverage.sum_independent_swaths_ha,
        'swath_union_ha': coverage.swath_union_ha,
        'clipped_all_ha': coverage.clipped_all_ha,
        'contour_area_ha': coverage.contour_area_ha,
        'uncertainty_percent': coverage.uncertainty_percent,
        'algorithm_version': coverage.algorithm_version,
        'params_json': json.dumps(coverage.params, sort_keys=True),
        'flights_total': coverage.flights_total,
        'routes_total': coverage.routes_total,
        'flights_without_route': coverage.flights_without_route,
        'flights_without_width': coverage.flights_without_width,
        'flights_without_width_on_work':
            coverage.flights_without_width_on_work,
        'work_segments': coverage.work_segments,
        'route_points': coverage.route_points,
        'quality_status': coverage.quality_status,
        'quality_reason': coverage.quality_reason,
        'dji_area_ha': item['dji_area_ha'],
        'mission_state': coverage.mission_state,
        'computed_at': now,
    }


def recalculate(db_path, date_from, date_to, apply=False, params=None,
                now=None):
    """Пересчёт периода. Возвращает сводку без координат и без настоящих ID.

    `apply=False` -- сухой прогон: ни одной записи, соединение закрывается
    откатом. Это единственный режим, безопасный на живой базе.
    """
    if date_from > date_to:
        raise RecalcError('--from %s is after --to %s' % (date_from, date_to))
    params = params or ua.PARAMS
    now = now or datetime.utcnow()
    stamp = now.strftime('%Y-%m-%d %H:%M:%S')

    con = connect(db_path)
    try:
        _require_tables(con)
        summary = {status: 0 for status in STATUS_ORDER}
        summary.update({'days': 0, 'works': 0, 'inserted': 0, 'updated': 0,
                        'unchanged': 0, 'flights': 0, 'routes': 0,
                        'ready_area_ha': 0.0,
                        'algorithm_version': ua.ALGORITHM_VERSION,
                        'params': ua.algorithm_params(params),
                        'applied': bool(apply)})

        if apply:
            con.execute('BEGIN')

        for day in days_with_flights(con, date_from, date_to):
            summary['days'] += 1
            for item in compute_day(con, day, params=params):
                coverage = item['coverage']
                summary['works'] += 1
                summary[coverage.quality_status] = (
                    summary.get(coverage.quality_status, 0) + 1)
                summary['flights'] += coverage.flights_total
                summary['routes'] += coverage.routes_total
                if coverage.is_summable:
                    summary['ready_area_ha'] += coverage.estimated_useful_area_ha

                existing = con.execute(
                    'SELECT id, inputs_fingerprint FROM drone_coverage_works '
                    ' WHERE unit_key = ? AND work_date = ? AND group_index = ?',
                    (item['unit_key'], item['work_date'],
                     item['group_index'])).fetchone()

                if existing is None:
                    summary['inserted'] += 1
                    if apply:
                        values = _row_values(item, stamp)
                        con.execute(
                            'INSERT INTO drone_coverage_works (%s) VALUES (%s)'
                            % (', '.join(WRITE_COLUMNS),
                               ', '.join(':%s' % name
                                         for name in WRITE_COLUMNS)), values)
                elif existing['inputs_fingerprint'] != item['inputs_fingerprint']:
                    # Вход изменился -- маршрут, контур, параметр или версия.
                    # Молча устаревшее число тут недопустимо.
                    summary['updated'] += 1
                    if apply:
                        values = _row_values(item, stamp)
                        values['id'] = existing['id']
                        con.execute(
                            'UPDATE drone_coverage_works SET %s WHERE id = :id'
                            % ', '.join('%s = :%s' % (name, name)
                                        for name in WRITE_COLUMNS), values)
                else:
                    # Тот же вход -- та же строка. Повторный --apply не пишет
                    # ничего и не двигает `computed_at`.
                    summary['unchanged'] += 1

        if apply:
            con.commit()
        else:
            con.rollback()
        summary['ready_area_ha'] = round(summary['ready_area_ha'], 4)
        return summary
    except Exception:
        con.rollback()
        raise
    finally:
        con.close()


def format_summary(summary):
    """Сводка для консоли. ASCII, без координат и без настоящих ID."""
    lines = [
        'mode                : %s' % ('APPLY' if summary['applied']
                                      else 'DRY RUN (nothing written)'),
        'algorithm           : %s' % summary['algorithm_version'],
        'local days examined : %d' % summary['days'],
        'works               : %d' % summary['works'],
        'flights / routes    : %d / %d' % (summary['flights'],
                                           summary['routes']),
        'rows inserted       : %d' % summary['inserted'],
        'rows updated        : %d' % summary['updated'],
        'rows unchanged      : %d' % summary['unchanged'],
        '',
        'READY               : %d' % summary[ua.READY_ESTIMATE],
        'PARTIAL             : %d' % summary[ua.PARTIAL_DATA],
        'UNAVAILABLE         : %d' % summary[ua.DATA_UNAVAILABLE],
        'AMBIGUOUS           : %d' % summary[ua.CONTOUR_AMBIGUOUS],
        'NOT_MATCHED         : %d' % summary[ua.CONTOUR_NOT_MATCHED],
        'ROUTE_INVALID       : %d' % summary[ua.ROUTE_INVALID],
        '',
        'useful area of READY works only: %.4f ha'
        % summary['ready_area_ha'],
    ]
    return '\n'.join(lines)


def parse_day(text):
    try:
        return datetime.strptime(str(text), '%Y-%m-%d').date()
    except ValueError:
        raise RecalcError('a date must look like YYYY-MM-DD, got %r' % text)


__all__ = ['RecalcError', 'recalculate', 'compute_day', 'format_summary',
           'parse_day', 'local_day', 'unit_key_of', 'connect']
