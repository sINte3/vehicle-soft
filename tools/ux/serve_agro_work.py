# -*- coding: utf-8 -*-
"""Одноразовый стенд «Сверки» (agro-work) для tools/ux/check_agro_work_screen.mjs.

Тот же временный стенд, что serve_ephemeral.py (временная база в каталоге
temp, генеральный засев), плюс то, чего генеральный засев дать не может:
таблицы agro_work_* (их создаёт только миграция AGRO_WORK_001, ORM-моделей у
них нет намеренно) и небольшой набор сверки -- тот же, что у
tests/test_agro_work_screen.py:

* две организации, три машины, сопоставление с объектами Wialon;
* заявки сентября 2026: «работа была» (наличные), «работы не было»
  (перечисление, открыта -- в процессе), машина agro-work без нашей техники,
  заявка второй организации (наличные), заявка, удалённая в agro-work;
* сутки GPS: работа 10.09, 13.09 (без заявки) и 15.09, сутки без работы;
* подписанные сессии ux_admin (RU) и ux_admin_uz (UZ) -- файлы storageState
  для Playwright. Пароль не вводится и не печатается.

routes.json репозитория НЕ переписывается. Всё живёт до выхода процесса.

Запуск (из корня репозитория; каталог --state-dir -- вне репозитория):

    python tools/ux/serve_agro_work.py --port 5099 --state-dir <каталог>

ДАННЫЕ СИНТЕТИЧЕСКИЕ.
"""

import argparse
import json
import os
import sqlite3
import sys
from datetime import date, datetime

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

import serve_ephemeral as se  # noqa: E402  (config -> временная база ДО app)

from sqlalchemy import text  # noqa: E402

import migrate_agro_work_001 as agro_mig  # noqa: E402
from models import (Equipment, GpsDailyAggregate, GpsWorkPolygon,  # noqa: E402
                    Organization, User, VialonMapping)

app, db = se.app, se.db
app.config['TEMPLATES_AUTO_RELOAD'] = True
app.jinja_env.auto_reload = True

PERIOD = '/agro-work/?from=2026-09-10&to=2026-09-20'


def install_agro_tables(path):
    con = sqlite3.connect(path)
    try:
        for _, ddl in agro_mig.TABLES:
            con.execute(ddl)
        for _, ddl, _ in agro_mig.INDEXES:
            con.execute(ddl)
        for _, ddl in agro_mig.TRIGGERS:
            con.execute(ddl)
        con.commit()
    finally:
        con.close()


def seed_orm():
    """Организации, машины, сопоставление и сутки GPS. {ключ: id машины}."""
    for table in ('gps_work_polygons', 'gps_daily_aggregates',
                  'vialon_mappings'):
        db.session.execute(text('DELETE FROM %s' % table))
    first = Organization(name='Бухоро агрокластер (стенд)', sort_order=97)
    second = Organization(name='Жиззах филиали (стенд)', sort_order=98)
    db.session.add_all([first, second])
    db.session.flush()
    machines = {
        'mtz1': Equipment(name='МТЗ-80.1', plate='80 001 EA', category='mtz',
                          organization_id=first.id),
        'mtz2': Equipment(name='МТЗ-82', plate='80 002 EA', category='mtz',
                          organization_id=first.id),
        'case': Equipment(name='Case Puma', plate='25 003 GA',
                          category='yukori', organization_id=second.id)}
    db.session.add_all(machines.values())
    db.session.flush()
    for key, unit in (('mtz1', 1001), ('mtz2', 1002), ('case', 1003)):
        db.session.add(VialonMapping(vialon_name='u%d' % unit, wialon_id=unit,
                                     equipment_id=machines[key].id, skip=False))
    computed = datetime(2026, 9, 21, 3, 0, 0)
    for unit, day, area in ((1001, 10, 2.5), (1002, 13, 3.0), (1003, 15, 7.0)):
        db.session.add(GpsDailyAggregate(
            work_date=date(2026, 9, day), wialon_id=unit, points_total=100,
            points_work=50, track_km=1.0, motion_gaps=0, lost_seconds=0.0,
            gps_jumps=0, method_version='v', computed_at=computed))
        db.session.add(GpsWorkPolygon(
            work_date=date(2026, 9, day), wialon_id=unit, site_number=1,
            area_ha=area, minutes=60.0, polygon_geojson='{}'))
    for day in (11, 12, 13):
        db.session.add(GpsDailyAggregate(
            work_date=date(2026, 9, day), wialon_id=1001, points_total=100,
            points_work=50, track_km=1.0, motion_gaps=0, lost_seconds=0.0,
            gps_jumps=0, method_version='v', computed_at=computed))
    return {key: item.id for key, item in machines.items()}


def seed_agro(path, ids):
    con = sqlite3.connect(path)
    try:
        con.execute("INSERT INTO agro_work_import_runs (id, started_at, "
                    "finished_at, status, tool_version, rows_seen, rows_new, "
                    "history_pending) VALUES (1, '2026-09-21 03:00:00', "
                    "'2026-09-21 03:05:00', 'ok', 'agro-work-import-1', 5, 5, 0)")
        for transport_id, plate, eq_id, status in (
                ('T1', 'P1', ids['mtz1'], 'auto'), ('T2', 'P2', ids['mtz2'], 'auto'),
                ('T3', 'P3', ids['case'], 'auto'), ('T8', 'ALFAKLAS12', None, 'none')):
            con.execute("INSERT INTO agro_work_transports (id, plate_number, "
                        "plate_norm, equipment_id, match_status, first_seen_at, "
                        "last_seen_at, company_name) VALUES (?, ?, ?, ?, ?, 't', "
                        "'t', 'Бухоро агрокластер')",
                        (transport_id, plate, plate, eq_id, status))
        con.execute("INSERT INTO agro_work_work_types (id, name, unit, method, "
                    "first_seen_at, last_seen_at) VALUES ('W1', 'Култивация', "
                    "'HECTARE', 'ga', 't', 't')")
        for number, transport, created, completed, status, pay in (
                (1, 'T1', 10, 11, 'COMPLETED', 'CASH'),
                (2, 'T1', 12, None, 'IN_PROGRESS', 'TRANSFER'),
                (3, 'T8', 10, 11, 'COMPLETED', None),
                (4, 'T3', 15, 15, 'COMPLETED', 'CASH'),
                (5, 'T1', 16, 16, 'COMPLETED', None)):
            con.execute(
                "INSERT INTO agro_work_applications (id, application_number, "
                "transport_id, work_type_id, work_type_name, unit, status, "
                "created_at, updated_at, created_day, first_seen_run_id, "
                "last_seen_run_id, history_updated_at, initial_status, "
                "completed_day, plate_number, payment_type, volume) VALUES "
                "(?, ?, ?, 'W1', 'Култивация', 'HECTARE', ?, ?, 'u', ?, 1, 1, "
                "'u', 'IN_PROGRESS', ?, ?, ?, '2.5')",
                ('A%d' % number, 'APP-UX-%03d' % number, transport, status,
                 '2026-09-%02dT08:00:00+05:00' % created, '2026-09-%02d' % created,
                 '2026-09-%02d' % completed if completed else None,
                 'P' + transport, pay))
        con.execute("UPDATE agro_work_applications SET gone_at = "
                    "'2026-09-21 03:00:00' WHERE id = 'A5'")
        con.commit()
    finally:
        con.close()


def write_state(path, value):
    with open(path, 'w', encoding='utf-8') as handle:
        json.dump({'cookies': [{
            'name': app.config.get('SESSION_COOKIE_NAME', 'session'),
            'value': value, 'domain': '127.0.0.1', 'path': '/',
            'expires': -1, 'httpOnly': True, 'secure': False,
            'sameSite': 'Lax'}], 'origins': []}, handle)


def main():
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument('--port', type=int, default=5099)
    parser.add_argument('--state-dir', required=True,
                        help='where ux_admin.json / ux_admin_uz.json go')
    args = parser.parse_args()
    se.seed_ux_fixtures.guard_disposable(se.DB_PATH)
    os.makedirs(args.state_dir, exist_ok=True)

    with app.app_context():
        db.create_all()
        se.create_non_model_tables()
    se.seed_ux_fixtures.seed(app, db, rows_per_table=14, verbose=False)
    install_agro_tables(se.DB_PATH)

    with app.app_context():
        ids = seed_orm()
        db.session.commit()
        with app.test_request_context():
            serializer = app.session_interface.get_signing_serializer(app)
            for name in ('ux_admin', 'ux_admin_uz'):
                user = User.query.filter_by(username=name).one()
                write_state(os.path.join(args.state_dir, name + '.json'),
                            serializer.dumps({'_user_id': str(user.id),
                                              '_fresh': True,
                                              '_csrf_token': 'ux-local'}))
    seed_agro(se.DB_PATH, ids)

    print('database : %s' % se.DB_PATH)
    print('sessions : ux_admin.json, ux_admin_uz.json in the state dir')
    print('serving  : http://127.0.0.1:%d%s' % (args.port, PERIOD))
    sys.stdout.flush()
    app.run(host='127.0.0.1', port=args.port, debug=False,
            use_reloader=False)
    return 0


if __name__ == '__main__':
    sys.exit(main())
