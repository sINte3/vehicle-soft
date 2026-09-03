# -*- coding: utf-8 -*-
"""ops/pilot_useful_area_001/pilot_db_probe.py -- безопасный осмотр базы.

DRONE-USEFUL-AREA-PILOT-001. Один прибор на все вопросы, которые операторские
скрипты задают базе: цела ли она, встала ли миграция, не поменялась ли
`drone_flights.area_ha`, что лежит за целевой день и что дал пересчёт.

БАЗА ОТКРЫВАЕТСЯ ТОЛЬКО НА ЧТЕНИЕ (`pilot_common.connect_readonly`), а
использованный режим попадает в улику. Ни одного импорта приложения: устав
проекта прямо запрещает `from app import app` в диагностике -- `create_app()`
зовёт `db.create_all()` и превращает читателя в писателя.

Локальные сутки берутся у `drone_coverage_recalc.local_day`, а не считаются
здесь второй раз: правило «работа -- это локальные сутки UTC+5» уже
записано в одном месте, и второй экземпляр той же арифметики однажды с ним
разойдётся.

Запуск (площадка; служба останавливать не требуется -- прибор не пишет):

  cd C:\\transport-report-staging
  & "C:\\Program Files\\Python314\\python.exe" ops\\pilot_useful_area_001\\pilot_db_probe.py snapshot --db C:\\transport-report-staging\\instance\\transport.db --day 2026-06-05 --out C:\\pilot\\evidence\\staging_snapshot.json

Коды возврата:
  0 -- осмотр выполнен (сам по себе НЕ означает «всё хорошо»: вердикты лежат
       в JSON, и решение принимает вызывающий скрипт);
  1 -- ошибка командной строки или чтения;
  2 -- база не найдена (файл НЕ создаётся);
  3 -- обязательная проверка провалена (только у подкоманд, которые её умеют).

Вывод -- ASCII JSON в stdout и, по `--out`, в файл. Ни координат, ни точек
маршрута, ни `dji_flight_id`, ни uuid контуров, ни названий полей в нём нет.
"""

import argparse
import os
import sys

from datetime import datetime, timedelta

HERE = os.path.dirname(os.path.abspath(__file__))
REPO_ROOT = os.path.dirname(os.path.dirname(HERE))
for path in (HERE, REPO_ROOT):
    if path not in sys.path:
        sys.path.insert(0, path)

import pilot_common as common  # noqa: E402

# [REASON]: правило локальных суток -- одно на проект и живёт в пересчёте.
# Импорт stdlib-модуля без Flask, тот же, что использует CI.
import drone_coverage_recalc as recalc  # noqa: E402


def _table_exists(con, name):
    row = con.execute("SELECT name FROM sqlite_master "
                      " WHERE type='table' AND name=?", (name,)).fetchone()
    return row is not None


def _index_exists(con, name):
    row = con.execute("SELECT name FROM sqlite_master "
                      " WHERE type='index' AND name=?", (name,)).fetchone()
    return row is not None


def integrity(con):
    """PRAGMA integrity_check плюс foreign_key_check. Обе -- только чтение."""
    rows = con.execute('PRAGMA integrity_check').fetchall()
    verdict = [str(row[0]) for row in rows]
    fk = con.execute('PRAGMA foreign_key_check').fetchall()
    return {
        'integrity_check': verdict,
        'integrity_ok': verdict == ['ok'],
        'foreign_key_violations': len(fk),
        'page_size': con.execute('PRAGMA page_size').fetchone()[0],
        'journal_mode': con.execute('PRAGMA journal_mode').fetchone()[0],
    }


def schema_state(con):
    """Что из DRONES_USEFUL_AREA_001 стоит в базе. Ни строки данных наружу."""
    tables = {name: _table_exists(con, name)
              for name in common.EXPECTED_TABLES}
    indexes = {name: _index_exists(con, name)
               for name in common.EXPECTED_INDEXES}

    registry_rows = 0
    registered = False
    checksum_present = False
    if _table_exists(con, 'schema_migrations'):
        registry_rows = con.execute(
            'SELECT count(*) FROM schema_migrations').fetchone()[0]
        row = con.execute('SELECT name, checksum FROM schema_migrations '
                          ' WHERE name = ?',
                          (common.MIGRATION_ID,)).fetchone()
        registered = row is not None
        checksum_present = bool(row and row['checksum'])

    return {
        'tables': tables,
        'tables_all_present': all(tables.values()),
        'indexes': indexes,
        'indexes_all_present': all(indexes.values()),
        'indexes_expected': len(common.EXPECTED_INDEXES),
        'indexes_present': sum(1 for value in indexes.values() if value),
        'migration_registered': registered,
        'migration_checksum_present': checksum_present,
        'schema_migrations_rows': registry_rows,
        'migration_id': common.MIGRATION_ID,
    }


def _utc_window(day):
    """Окно UTC для локальных суток UTC+5 -- ровно то, что строит пересчёт.

    [REASON]: смещение берётся из `drone_coverage_recalc.LOCAL_UTC_OFFSET`, а
    не пишется здесь числом. Правило «работа -- это локальные сутки» одно на
    проект, и второй экземпляр этой арифметики однажды с ним разойдётся --
    молча, потому что оба ответа выглядят правдоподобно.
    """
    midnight = datetime.strptime(str(day), '%Y-%m-%d')
    start = midnight - recalc.LOCAL_UTC_OFFSET
    end = start + timedelta(days=1)
    return (start.strftime('%Y-%m-%d %H:%M:%S'),
            end.strftime('%Y-%m-%d %H:%M:%S'))


def routes_of_day(con, day):
    """Маршруты и вылеты целевого дня. Идентификаторы наружу не выходят.

    `routes_outside_target_day` -- принятые маршруты, чей вылет НЕ принадлежит
    целевым локальным суткам. Ноль здесь -- условие пересчёта: собрали не тот
    день, значит собрали не то, и считать по этому нечего.

    Геометрия НЕ читается: считаются строки, а не точки. Прибор, тянущий
    `points_json` ради счётчика, поднимает в память день на 476 вылетов --
    и держит координаты в процессе, которому они не нужны.
    """
    if not _table_exists(con, 'drone_flight_routes'):
        return {'table_present': False}

    start, end = _utc_window(day)
    window = (start, end)

    flights_total = con.execute(
        'SELECT count(*) FROM drone_flights').fetchone()[0]
    flights_of_day = con.execute(
        'SELECT count(*) FROM drone_flights '
        ' WHERE started_at >= ? AND started_at < ?', window).fetchone()[0]
    routes_total = con.execute(
        'SELECT count(*) FROM drone_flight_routes').fetchone()[0]
    routed_of_day = con.execute(
        'SELECT count(*) FROM drone_flight_routes r '
        '  JOIN drone_flights f ON f.id = r.drone_flight_id '
        ' WHERE f.started_at >= ? AND f.started_at < ?', window).fetchone()[0]
    off_day = routes_total - routed_of_day

    census = con.execute(
        'SELECT sum(r.point_count) AS pts, '
        '       sum(CASE WHEN r.spray_width_recorded THEN 1 ELSE 0 END) AS wid, '
        '       sum(r.ingest_count) AS ingests '
        '  FROM drone_flight_routes r '
        '  JOIN drone_flights f ON f.id = r.drone_flight_id '
        ' WHERE f.started_at >= ? AND f.started_at < ?', window).fetchone()

    return {
        'table_present': True,
        'flights_in_database': flights_total,
        'flights_of_target_day': flights_of_day,
        'routes_total': routes_total,
        'routes_of_target_day': routed_of_day,
        'routes_outside_target_day': off_day,
        'routes_outside_target_day_is_zero': off_day == 0,
        'route_points_total': census['pts'] or 0,
        'routes_with_recorded_width': census['wid'] or 0,
        'route_ingests_total': census['ingests'] or 0,
    }


def coverage_of_day(con, day):
    """Результат расчёта за день: счётчики, площади, статусы. Без ID и точек."""
    if not _table_exists(con, 'drone_coverage_works'):
        return {'table_present': False}

    by_status = {status: 0 for status in common.QUALITY_STATUSES}
    reasons = {}
    contour_status = {}
    works = 0
    ready_area = 0.0
    non_ready_area_rows = 0
    dji_area = 0.0
    dji_area_rows = 0
    flights = 0
    routes = 0
    flights_without_route = 0
    flights_without_width = 0
    flights_without_width_on_work = 0
    works_without_width = 0
    work_segments = 0
    route_points = 0
    zero_work_segment_works = 0
    mission_state = {}
    algorithm_versions = {}
    uncertainty_values = []

    for row in con.execute('SELECT * FROM drone_coverage_works '
                           ' WHERE work_date = ?', (day,)):
        works += 1
        status = row['quality_status']
        by_status[status] = by_status.get(status, 0) + 1
        reasons[row['quality_reason']] = reasons.get(row['quality_reason'], 0) + 1
        key = row['contour_status'] or 'NONE'
        contour_status[key] = contour_status.get(key, 0) + 1
        state = row['mission_state'] or 'NONE'
        mission_state[state] = mission_state.get(state, 0) + 1
        version = row['algorithm_version']
        algorithm_versions[version] = algorithm_versions.get(version, 0) + 1

        area = row['estimated_useful_area_ha']
        if status in common.SUMMABLE_STATUSES:
            if area is not None:
                ready_area += float(area)
        elif area is not None:
            # [REASON]: строка не-READY с НЕ-NULL площадью -- это уже дефект,
            # а не оттенок. Правило проекта: число появляется только при
            # READY_ESTIMATE, всё остальное NULL, а не ноль.
            non_ready_area_rows += 1

        if row['dji_area_ha'] is not None:
            dji_area += float(row['dji_area_ha'])
            dji_area_rows += 1

        flights += row['flights_total'] or 0
        routes += row['routes_total'] or 0
        flights_without_route += row['flights_without_route'] or 0
        flights_without_width += row['flights_without_width'] or 0
        flights_without_width_on_work += row['flights_without_width_on_work'] or 0
        if row['flights_without_width_on_work']:
            works_without_width += 1
        work_segments += row['work_segments'] or 0
        route_points += row['route_points'] or 0
        if not (row['work_segments'] or 0):
            zero_work_segment_works += 1
        if row['uncertainty_percent'] is not None:
            uncertainty_values.append(float(row['uncertainty_percent']))

    ready = by_status.get('READY_ESTIMATE', 0)
    unresolved_contour = (by_status.get('CONTOUR_AMBIGUOUS', 0)
                          + by_status.get('CONTOUR_NOT_MATCHED', 0))
    without_number = works - ready

    return {
        'table_present': True,
        'work_date': day,
        'works': works,
        'by_status': by_status,
        'by_quality_reason': reasons,
        'by_contour_status': contour_status,
        'by_mission_state': mission_state,
        'algorithm_versions': algorithm_versions,
        'works_ready': ready,
        'works_without_number': without_number,
        'works_without_number_share': (round(without_number * 1.0 / works, 4)
                                       if works else None),
        'works_with_unresolved_contour': unresolved_contour,
        'works_without_confirmed_width': works_without_width,
        'works_with_zero_work_segments': zero_work_segment_works,
        'non_ready_rows_carrying_a_number': non_ready_area_rows,
        'only_ready_carries_a_number': non_ready_area_rows == 0,
        'ready_useful_area_ha': round(ready_area, 4),
        'dji_area_ha': round(dji_area, 4),
        'dji_area_rows': dji_area_rows,
        'flights_total': flights,
        'routes_total': routes,
        'flights_without_route': flights_without_route,
        'flights_without_width': flights_without_width,
        'flights_without_width_on_work': flights_without_width_on_work,
        'work_segments': work_segments,
        'route_points': route_points,
        'uncertainty_percent_max': (round(max(uncertainty_values), 4)
                                    if uncertainty_values else None),
    }


def rows_outside_target_day(con, day):
    """Строки расчёта за пределами целевого дня. Пилот их создавать не должен."""
    if not _table_exists(con, 'drone_coverage_works'):
        return None
    return con.execute('SELECT count(*) FROM drone_coverage_works '
                       ' WHERE work_date <> ?', (day,)).fetchone()[0]


def snapshot(db_path, day):
    """Полный безопасный снимок базы. Его и сравнивают между шагами."""
    con, mode = common.connect_readonly(db_path)
    try:
        payload = {
            'database': {
                'path_basename': os.path.basename(db_path),
                'size_bytes': os.path.getsize(db_path),
                'open_mode': mode,
            },
            'integrity': integrity(con),
            'schema': schema_state(con),
            'area_ha': common.area_ha_fingerprint(con),
            'routes': routes_of_day(con, day),
            'coverage': coverage_of_day(con, day),
            # [REASON]: полный отпечаток ВСЕХ строк и ВСЕХ колонок. Число
            # строк не меняется, когда строку переписали, -- а «сухой прогон
            # ничего не записал» доказывалось именно числом строк.
            'coverage_fingerprint': common.coverage_fingerprint(con),
            'coverage_rows_outside_target_day': rows_outside_target_day(con, day),
        }
    finally:
        con.close()
    return payload


# ─── Командная строка ───────────────────────────────────────────────────────

SUBCOMMANDS = ('snapshot', 'integrity', 'schema', 'area-fingerprint',
               'coverage-fingerprint', 'routes', 'coverage')


def build_parser():
    parser = argparse.ArgumentParser(
        prog='pilot_db_probe.py',
        description='DRONE-USEFUL-AREA-PILOT-001: read-only inspection of a '
                    'Vehicle Soft SQLite database. Never writes, never '
                    'imports the application, never prints a coordinate, a '
                    'flight id or a contour uuid.')
    parser.add_argument('command', choices=SUBCOMMANDS,
                        help='what to inspect')
    parser.add_argument('--db', required=True, metavar='PATH',
                        help='database file to read')
    parser.add_argument('--day', default=common.TARGET_DAY,
                        metavar='YYYY-MM-DD',
                        help='local (UTC+5) day the pilot targets')
    parser.add_argument('--run-id', required=True, metavar='ID',
                        help='the one run identifier every step of this pilot '
                             'shares')
    parser.add_argument('--kit-sha', required=True, metavar='SHA',
                        help='the MEASURED revision of the kit checkout')
    parser.add_argument('--out', metavar='PATH',
                        help='also write the evidence JSON to this file')
    parser.add_argument('--require', action='append', default=[],
                        metavar='CHECK',
                        help='fail with exit 3 unless the named check holds. '
                             'May be given more than once. Checks: '
                             'integrity, schema, no-off-day-routes, '
                             'only-ready-summed, area-sha256=<hex>, '
                             'coverage-sha256=<hex>')
    return parser


def _lookup_check(payload, name):
    """Значение именованной проверки. None -- «такой проверки нет»."""
    if name == 'integrity':
        return payload.get('integrity', {}).get('integrity_ok')
    if name == 'schema':
        schema = payload.get('schema', {})
        return bool(schema.get('tables_all_present')
                    and schema.get('indexes_all_present')
                    and schema.get('migration_registered'))
    if name == 'no-off-day-routes':
        return payload.get('routes', {}).get('routes_outside_target_day_is_zero')
    if name == 'only-ready-summed':
        return payload.get('coverage', {}).get('only_ready_carries_a_number')
    return None


def evaluate_requirements(payload, requirements):
    """[(имя, ok)] по каждому требованию. Неизвестное требование -- отказ."""
    results = []
    for raw in requirements:
        name = raw.strip()
        if name.startswith('area-sha256='):
            expected = name.split('=', 1)[1].strip().lower()
            actual = payload.get('area_ha', {}).get('sha256')
            results.append((name, bool(actual) and actual == expected))
            continue
        if name.startswith('coverage-sha256='):
            expected = name.split('=', 1)[1].strip().lower()
            actual = payload.get('coverage_fingerprint', {}).get('sha256')
            results.append((name, bool(actual) and actual == expected))
            continue
        value = _lookup_check(payload, name)
        # [REASON]: опечатка в имени проверки обязана ОТКАЗАТЬ, а не молча
        # пройти. Скрипт, требующий несуществующего условия и получающий
        # ноль, проверяет ровно ничего -- и выглядит при этом строгим.
        results.append((name, False if value is None else bool(value)))
    return results


def main(argv=None):
    args = build_parser().parse_args(argv)

    if not os.path.exists(args.db):
        sys.stderr.write('ERROR: database not found at %s - refusing '
                         'to run.\n' % args.db)
        return common.EXIT_NO_DATABASE

    try:
        if args.command == 'snapshot':
            payload = snapshot(args.db, args.day)
        else:
            con, mode = common.connect_readonly(args.db)
            try:
                if args.command == 'integrity':
                    payload = {'open_mode': mode, 'integrity': integrity(con)}
                elif args.command == 'schema':
                    payload = {'open_mode': mode, 'schema': schema_state(con)}
                elif args.command == 'area-fingerprint':
                    payload = {'open_mode': mode,
                               'area_ha': common.area_ha_fingerprint(con)}
                elif args.command == 'coverage-fingerprint':
                    payload = {'open_mode': mode,
                               'coverage_fingerprint':
                                   common.coverage_fingerprint(con)}
                elif args.command == 'routes':
                    payload = {'open_mode': mode,
                               'routes': routes_of_day(con, args.day)}
                else:
                    payload = {'open_mode': mode,
                               'coverage': coverage_of_day(con, args.day)}
            finally:
                con.close()
    except common.ProbeError as exc:
        sys.stderr.write('ERROR: %s\n' % exc)
        return common.EXIT_ERROR
    except Exception as exc:  # noqa: BLE001 -- прибор не имеет права упасть молча
        sys.stderr.write('ERROR: %s: %s\n' % (type(exc).__name__, exc))
        return common.EXIT_ERROR

    checks = evaluate_requirements(payload, args.require)
    payload['requirements'] = [{'check': name, 'passed': ok}
                               for name, ok in checks]
    payload['requirements_all_passed'] = all(ok for _name, ok in checks)

    document = common.evidence_envelope('db-probe:%s' % args.command, payload,
                                        args.run_id, args.kit_sha)
    # [REASON]: улика сама называет, куда смотрел прибор. Скрипт, доказывающий
    # «продакшен не тронут», обязан опираться на запись прибора, а не на то,
    # что оператор передал правильный --db.
    document['database_is_production'] = common.path_equals(
        args.db, common.PRODUCTION_DB)
    document['database_within_production_root'] = common.touches_production(
        args.db)
    common.emit(document, args.out)

    if not payload['requirements_all_passed']:
        # [REASON]: отказы идут в stderr, а не в stdout. stdout этого прибора --
        # ОДИН документ JSON, и вызывающий скрипт его разбирает; строка
        # 'CHECK FAILED', подмешанная туда, ломала бы разбор ровно в тот
        # момент, когда что-то пошло не так, то есть когда разбор нужнее всего.
        for name, ok in checks:
            if not ok:
                sys.stderr.write('CHECK FAILED: %s\n' % name)
        return common.EXIT_CHECK_FAILED
    return common.EXIT_OK


if __name__ == '__main__':
    sys.exit(main())
