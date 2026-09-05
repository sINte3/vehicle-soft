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

# Состояние маршрута ОДНОГО вылета. Три значения, а не два: «не привозили»
# и «привезли негодное» -- разные факты и разные действия.
ROUTE_PRESENT = 'PRESENT'
ROUTE_ABSENT = 'ABSENT'
ROUTE_INVALID = 'INVALID'


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
        '       f.started_at, f.area_ha, f.work_seconds, '
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
        # Три состояния маршрута, а не два.
        #
        # [REASON]: «маршрута нет» и «маршрут есть, но негодный» -- разные
        # факты, и второй маскировался под первый. Нечитаемый JSON давал
        # пустой список, пустой список отправлял вылет в корзину «без
        # маршрута», и битая геометрия молча превращалась в DATA_UNAVAILABLE
        # вместо ROUTE_INVALID. Разница существенная: в первом случае сборщик
        # ещё не привозил маршрут, во втором он привёз то, что не читается, и
        # это повод смотреть на приём, а не ждать следующего сбора.
        points = None
        state = ROUTE_ABSENT
        if row['points_json'] is not None:
            state = ROUTE_INVALID
            try:
                parsed = json.loads(row['points_json'])
            except ValueError:
                parsed = None
            if isinstance(parsed, list) and len(parsed) >= 2:
                points = parsed
                state = ROUTE_PRESENT
        flights.append({
            'route_state': state,
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
            # Только для наблюдаемой скорости сухого прогона; в отпечаток и
            # в строку не входит (`WorkCoverage.NOT_STORED`).
            'work_seconds': row['work_seconds'],
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
    # годный маршрут: рамку вылета без геометрии построить не из чего.
    routed = [item for item in flights
              if item['route_state'] == ROUTE_PRESENT]
    absent = {}
    invalid = {}
    for item in flights:
        if item['route_state'] == ROUTE_ABSENT:
            absent.setdefault(item['unit_key'], []).append(item)
        elif item['route_state'] == ROUTE_INVALID:
            invalid.setdefault(item['unit_key'], []).append(item)

    groups = ua.group_routes(routed)

    # Сколько РАБОТ вышло у каждой машины за этот день. От этого зависит,
    # можно ли отнести вылет без маршрута к одной определённой работе.
    works_per_unit = {}
    for unit_key, _day, _index, _members in groups:
        works_per_unit[unit_key] = works_per_unit.get(unit_key, 0) + 1

    results = []
    used_indexes = {}
    for unit_key, group_day, index, members in groups:
        candidates = contour_candidates(
            con, [point for member in members for point in member['points']])

        used_indexes[(unit_key, group_day)] = max(
            used_indexes.get((unit_key, group_day), -1), index)

        # Вылеты без маршрута приписываются работе ТОЛЬКО когда работа у
        # машины в этот день одна: тогда другой кандидатуры нет и приписка
        # ничего не выдумывает.
        #
        # [REASON]: при двух и более работах приписка к первой была
        # произвольной. Она оставляла ОСТАЛЬНЫЕ работы в READY_ESTIMATE и
        # объявляла их полными -- при том, что недостающий вылет мог
        # принадлежать любой из них. Теперь ни одна не считается полной, а
        # сам вылет считается ровно один раз, своей отдельной строкой: класть
        # его в `flights_total` каждой работы значило бы удвоить и счётчик
        # вылетов, и площадь DJI.
        single_work = (works_per_unit.get(unit_key, 0) == 1
                       and not invalid.get(unit_key))
        extra = []
        indeterminate = []
        unassigned = 0
        if single_work:
            extra = absent.pop(unit_key, [])
        else:
            indeterminate = (list(absent.get(unit_key) or ())
                             + list(invalid.get(unit_key) or ()))
            unassigned = len(indeterminate)

        coverage = ua.compute_work(
            members, candidates, params=params,
            flights_total=len(members) + len(extra),
            unassigned_flights=unassigned,
            mission_state=_mission_state(members))

        contour_row_id = None
        contour_geometry = None
        for candidate in candidates:
            if candidate['uuid'] == coverage.contour_uuid:
                contour_row_id = candidate['contour_row_id']
                contour_geometry = candidate['geojson']
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
            # [REASON]: в отпечаток входят не только маршруты этой работы, но
            # и вылеты, делающие её полноту неопределённой -- приписанные к
            # ней (`extra`) и, при нескольких работах машины, все безмаршрутные
            # и негодные вылеты дня (`indeterminate`). Именно их появление
            # переводит работу из READY_ESTIMATE в PARTIAL_DATA, ничего не
            # меняя в самих маршрутах: отпечаток по одним маршрутам такой
            # переход не замечал, и в базе оставался READY_ESTIMATE.
            'inputs_fingerprint': ua.inputs_fingerprint(
                _flight_inputs(members + extra + indeterminate),
                params=params, contour_key=coverage.contour_uuid,
                contour_geometry=contour_geometry,
                contour_candidates=_candidate_inputs(candidates)),
            'dji_area_ha': _sum_area(members + extra),
        })

    def _next_index(unit_key, day):
        key = (unit_key, day)
        used_indexes[key] = used_indexes.get(key, -1) + 1
        return used_indexes[key]

    # Вылеты, чей сохранённый маршрут негоден. Своя строка со своим статусом:
    # ROUTE_INVALID, а не DATA_UNAVAILABLE -- маршрут привезли, он не читается.
    for unit_key, members in sorted(invalid.items()):
        day_iso = members[0]['day']
        coverage = ua.compute_work(
            [{'points': [], 'spray_width_m': None} for _ in members],
            [], params=params, flights_total=len(members))
        results.append({
            'unit_key': unit_key,
            'drone_unit_id': members[0]['drone_unit_id'],
            'work_date': day_iso,
            'group_index': _next_index(unit_key, day_iso),
            'coverage': coverage,
            'contour_row_id': None,
            'route_fingerprint': ua.route_fingerprint(
                [(m['flight_id'], m['content_sha256']) for m in members]),
            'inputs_fingerprint': ua.inputs_fingerprint(
                _flight_inputs(members), params=params, contour_key=None,
                contour_geometry=None, contour_candidates=[]),
            'dji_area_ha': _sum_area(members),
        })

    # Машина, у которой вылеты остались без маршрута вовсе. Строка со статусом
    # DATA_UNAVAILABLE -- это не шум, а единственное место, где видно, что
    # маршруты не доехали. При нескольких работах машины сюда же попадают
    # вылеты, которые не удалось отнести ни к одной из них.
    for unit_key, members in sorted(absent.items()):
        day_iso = members[0]['day']
        coverage = ua.compute_work([], [], params=params,
                                   flights_total=len(members))
        results.append({
            'unit_key': unit_key,
            'drone_unit_id': members[0]['drone_unit_id'],
            'work_date': day_iso,
            'group_index': _next_index(unit_key, day_iso),
            'coverage': coverage,
            'contour_row_id': None,
            'route_fingerprint': ua.route_fingerprint([]),
            'inputs_fingerprint': ua.inputs_fingerprint(
                _flight_inputs(members), params=params, contour_key=None,
                contour_geometry=None, contour_candidates=[]),
            'dji_area_ha': _sum_area(members),
        })
    return results


def _flight_inputs(members):
    """Описания вылетов для отпечатка. Значения наружу не выходят."""
    return [ua.flight_input(item['flight_id'], item['route_state'],
                            content_sha256=item['content_sha256'],
                            area_ha=item['area_ha'],
                            mission_uuid=item['mission_uuid'])
            for item in members]


def _candidate_inputs(candidates):
    """[(uuid, geojson)] всего короткого списка -- не только победителя."""
    return [(candidate['uuid'], candidate['geojson'])
            for candidate in (candidates or ())]


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


# Колонки, которые сравниваются при решении «строка не изменилась».
#
# [REASON]: `computed_at` намеренно ИСКЛЮЧЁН. Он меняется каждым прогоном по
# построению, и сравнение с ним объявляло бы изменившейся любую строку --
# `unchanged` перестал бы что-либо значить.
COMPARED_COLUMNS = tuple(name for name in WRITE_COLUMNS
                         if name != 'computed_at')


def _same_stored_values(existing, values):
    """True, когда сохранённая строка совпадает с только что посчитанной.

    Вторая сеть под отпечатком, а не замена ему. Отпечаток обязан меняться
    при изменении значимого входа -- это его работа, и она проверяется
    отдельно. Но отпечаток строится по входам, а сравнение смотрит на выход:
    если в отпечаток однажды забудут внести новый вход, эта проверка поймает
    расхождение на первом же прогоне, а не тогда, когда кто-нибудь заметит
    неверное число на странице.

    [REASON]: числа сравниваются как float с допуском. SQLite возвращает
    REAL, и `2.0 != 2` в Python-сравнении кортежей дало бы вечное `updated`
    на строках, где ничего не менялось.
    """
    for name in COMPARED_COLUMNS:
        stored = existing[name]
        fresh = values[name]
        if stored is None or fresh is None:
            if stored is not fresh:
                return False
            continue
        if isinstance(fresh, float) or isinstance(stored, float):
            try:
                if abs(float(stored) - float(fresh)) > 1e-9:
                    return False
                continue
            except (TypeError, ValueError):
                return False
        if stored != fresh:
            return False
    return True


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
                now=None, collect_works=False):
    """Пересчёт периода. Возвращает сводку без координат и без настоящих ID.

    `apply=False` -- сухой прогон: ни одной записи, соединение закрывается
    откатом. Это единственный режим, безопасный на живой базе.

    `params` -- параметры версии (`ua.params_for_version`); версия в сводке и
    в каждой строке выводится ИЗ НИХ. `collect_works=True` кладёт в сводку
    `works_detail` -- разложение каждой работы (полосы, объединение, обрезка,
    контур, погрешность, статус, длины отрезков по причинам, наблюдаемая
    скорость) без координат и без идентификаторов вылетов.
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
                        'unchanged': 0, 'deleted': 0, 'flights': 0,
                        'routes': 0, 'ready_area_ha': 0.0,
                        'algorithm_version': ua.version_of_params(params),
                        'params': ua.algorithm_params(params),
                        'applied': bool(apply)})
        if collect_works:
            summary['works_detail'] = []

        if apply:
            con.execute('BEGIN')

        # Полный набор работ, который даёт ЭТОТ пересчёт за период. По нему
        # ниже вычищаются строки, которых новый набор больше не содержит.
        produced = set()

        for day in days_with_flights(con, date_from, date_to):
            summary['days'] += 1
            for item in compute_day(con, day, params=params):
                coverage = item['coverage']
                summary['works'] += 1
                if collect_works:
                    summary['works_detail'].append(work_detail(item))
                summary[coverage.quality_status] = (
                    summary.get(coverage.quality_status, 0) + 1)
                summary['flights'] += coverage.flights_total
                summary['routes'] += coverage.routes_total
                if coverage.is_summable:
                    summary['ready_area_ha'] += coverage.estimated_useful_area_ha

                produced.add((item['unit_key'], item['work_date'],
                              item['group_index']))

                existing = con.execute(
                    'SELECT * FROM drone_coverage_works '
                    ' WHERE unit_key = ? AND work_date = ? AND group_index = ?',
                    (item['unit_key'], item['work_date'],
                     item['group_index'])).fetchone()
                values = _row_values(item, stamp)

                if existing is None:
                    summary['inserted'] += 1
                    if apply:
                        con.execute(
                            'INSERT INTO drone_coverage_works (%s) VALUES (%s)'
                            % (', '.join(WRITE_COLUMNS),
                               ', '.join(':%s' % name
                                         for name in WRITE_COLUMNS)), values)
                elif (existing['inputs_fingerprint']
                      != item['inputs_fingerprint']
                      or not _same_stored_values(existing, values)):
                    # Вход изменился -- вылет, маршрут, контур, параметр или
                    # версия, -- ЛИБО сохранённая строка разошлась со свежим
                    # расчётом. Молча устаревшая строка тут недопустима.
                    summary['updated'] += 1
                    if apply:
                        values['id'] = existing['id']
                        con.execute(
                            'UPDATE drone_coverage_works SET %s WHERE id = :id'
                            % ', '.join('%s = :%s' % (name, name)
                                        for name in WRITE_COLUMNS), values)
                else:
                    # Тот же вход И та же строка. Повторный --apply не пишет
                    # ничего и не двигает `computed_at`.
                    summary['unchanged'] += 1

        # ── Снятие устаревших строк ─────────────────────────────────────
        #
        # [REASON]: пересчёт -- это СНИМОК периода, а не только вставка и
        # обновление. Если вчера у машины было две работы, а после исправления
        # маршрутов они слились в одну, вторая строка никуда не девалась и
        # продолжала попадать в сумму на /drones/coverage -- итог завышался, и
        # ни одна строка при этом не выглядела неправильной. То же самое с
        # днём, у которого вылеты удалили или перенесли: день выпадает из
        # `days_with_flights`, его работы не пересчитываются, а прежние
        # результаты остаются.
        #
        # Поэтому подметается ВЕСЬ период, а не только дни, в которых сегодня
        # нашлись вылеты. Границы -- ровно запрошенные: строка за пределами
        # периода этим прогоном не проверялась, и удалять её было бы удалением
        # данных, о которых прогон ничего не знает.
        stale = [row['id'] for row in con.execute(
            'SELECT id, unit_key, work_date, group_index '
            '  FROM drone_coverage_works '
            ' WHERE work_date >= ? AND work_date <= ?',
            (date_from.isoformat(), date_to.isoformat())).fetchall()
            if (row['unit_key'], row['work_date'], row['group_index'])
            not in produced]
        summary['deleted'] = len(stale)
        if apply and stale:
            for start in range(0, len(stale), 500):
                chunk = stale[start:start + 500]
                con.execute(
                    'DELETE FROM drone_coverage_works WHERE id IN (%s)'
                    % ','.join('?' * len(chunk)), chunk)

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


# Колонки разложения одной работы: ровно те, что хранит строка, плюс
# наблюдаемые величины сухого прогона. Ни координат, ни идентификаторов
# вылетов: `unit_key` -- тот же ключ машины, что стоит в строке и на странице.
WORK_DETAIL_FIELDS = (
    'work_date', 'unit_key', 'group_index', 'quality_status',
    'quality_reason', 'contour_status', 'estimated_useful_area_ha',
    'partial_estimate_ha', 'sum_independent_swaths_ha', 'swath_union_ha',
    'clipped_all_ha', 'contour_area_ha', 'uncertainty_percent',
    'dji_area_ha', 'flights_total', 'routes_total', 'work_segments',
    'route_points', 'algorithm_version')


def work_detail(item):
    """Разложение одной работы для сухого прогона. Ничего приватного."""
    coverage = item['coverage']
    detail = {}
    for name in WORK_DETAIL_FIELDS:
        if name in ('work_date', 'unit_key', 'group_index', 'dji_area_ha'):
            detail[name] = item[name]
        else:
            detail[name] = getattr(coverage, name)
    detail['diagnostics'] = dict(coverage.diagnostics or {})
    return detail


def format_works(summary):
    """Разложение по работам для консоли. ASCII; печатается по запросу."""
    details = summary.get('works_detail')
    if details is None:
        return ''
    lines = ['', 'per-work decomposition (%d works, %s):'
             % (len(details), summary['algorithm_version'])]
    for detail in sorted(details, key=lambda d: (d['work_date'],
                                                 d['unit_key'],
                                                 d['group_index'])):
        diag = detail.get('diagnostics') or {}
        lengths = diag.get('length_by_reason_m') or {}
        lines.append('  %s %s #%d  %s (%s)  contour=%s'
                     % (detail['work_date'], detail['unit_key'],
                        detail['group_index'], detail['quality_status'],
                        detail['quality_reason'], detail['contour_status']))
        lines.append('    useful=%s partial=%s independent=%s union=%s '
                     'clipped_all=%s contour_ha=%s uncertainty=%s%% dji=%s'
                     % tuple(_num(detail[name]) for name in (
                         'estimated_useful_area_ha', 'partial_estimate_ha',
                         'sum_independent_swaths_ha', 'swath_union_ha',
                         'clipped_all_ha', 'contour_area_ha',
                         'uncertainty_percent', 'dji_area_ha')))
        lines.append('    flights=%d routes=%d work_segments=%d points=%d '
                     'route_length_m=%s'
                     % (detail['flights_total'], detail['routes_total'],
                        detail['work_segments'], detail['route_points'],
                        _num(diag.get('route_length_m'))))
        if lengths:
            lines.append('    length_by_reason_m: '
                         + ' '.join('%s=%s' % (reason, _num(lengths[reason]))
                                    for reason in sorted(lengths)))
        lines.append('    implied speed (observable, not a rule): '
                     'flights_with_duration=%s min=%s max=%s m/s'
                     % (diag.get('flights_with_duration', 0),
                        _num(diag.get('implied_speed_min_mps')),
                        _num(diag.get('implied_speed_max_mps'))))
    return '\n'.join(lines)


def _num(value):
    if value is None:
        return 'NULL'
    if isinstance(value, float):
        return '%.4f' % value
    return str(value)


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
        'rows deleted (stale): %d' % summary['deleted'],
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
           'format_works', 'work_detail', 'parse_day', 'local_day',
           'unit_key_of', 'connect']
