# -*- coding: utf-8 -*-
"""Одноразовый стенд экрана «Факт по технике» для tools/ux/check_gps_map.mjs.

Тот же временный стенд, что serve_ephemeral.py (временная база в каталоге
temp, генеральный засев), плюс то, чего генеральный засев дать не может:

* сутки 27.07.2026 объекта 3464 -- НАСТОЯЩИЙ трек из
  gps/tests/fixtures/track_3464_20260727.csv в помесячном файле точек
  временного каталога, контур 3208 из gps/tests/fixtures/zone_3208.json в
  справочнике и участок на его земле (8,772 га);
* те же сутки погрузчика 9001 -- спецтехника (`spetstekhnika`), трек без
  участков;
* выбор машины (замечания владельца 09.10.2026, проверка
  check_gps_fact_picker.mjs): вторая организация «Когон ПТЗ» с New Holland,
  чей госномер записан РУССКИМИ С и А («80 156 СА»), с участком, на который
  уже ответили «проезд»; объект с одной строкой сопоставления без машины
  («Камаз 80 777 KA»); объект без строки вовсе, чьё имя знает только файл
  коллектора («Т-28 80 990 HA»); и вторые сутки 26.07 трактора 3464 -- для
  смены суток с той же машиной;
* подписанные сессии ux_admin (RU) и ux_admin_uz (UZ) -- файлы storageState
  для Playwright. Пароль не вводится и не печатается.

Без ключа `--imagery` подложка -- OSM, как у владельца до того, как он
положит ключи. С `--imagery` стенд кладёт НЕНАСТОЯЩИЕ ключ Esri и
идентификатор Copernicus: проверка карты подменяет ответы этих сервисов в
браузере и в сеть с ними не ходит. Плитки из интернета в песочнице могут не
грузиться -- карта при этом обязана встать: векторные слои от плиток не
зависят.

routes.json репозитория НЕ переписывается. Всё живёт до выхода процесса.

Запуск (из корня репозитория; каталог --state-dir -- вне репозитория):

    python tools/ux/serve_gps_fact.py --port 5099 --state-dir <каталог>

ДАННЫЕ ТРЕКА НАСТОЯЩИЕ (фикстура движка), ОСТАЛЬНЫЕ -- СИНТЕТИЧЕСКИЕ.
"""

import argparse
import csv
import json
import os
import sys
from datetime import date, datetime, timedelta, timezone

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

import serve_ephemeral as se  # noqa: E402  (config -> временная база ДО app)

from sqlalchemy import text  # noqa: E402

from gps_collector import storage  # noqa: E402
from models import (Equipment, FieldContour, GpsDailyAggregate,  # noqa: E402
                    GpsWorkPolygon, Organization, User, VialonMapping)

app, db = se.app, se.db
app.config['TEMPLATES_AUTO_RELOAD'] = True
app.jinja_env.auto_reload = True
POINTS_DIR = os.path.join(se._TMP, 'gps_points')
app.config['GPS_POINTS_DIR'] = POINTS_DIR
app.config['MAP_ESRI_KEY_FILE'] = os.path.join(se._TMP, 'esri_api_key.txt')
app.config['MAP_COPERNICUS_INSTANCE_FILE'] = os.path.join(
    se._TMP, 'copernicus_instance_id.txt')

# Ненастоящие ключ и идентификатор для --imagery: браузерная проверка
# подменяет ответы Esri и Copernicus сама и в сеть с ними не ходит.
FAKE_ESRI_KEY = 'AAPK-ux-stand-not-a-real-key'
FAKE_INSTANCE = '00000000-0000-4000-8000-00000000ux01'

FIXTURES = os.path.join(se.REPO_ROOT, 'gps', 'tests', 'fixtures')
TZ = timezone(timedelta(hours=5))
DAY = date(2026, 7, 27)
DAY_BEFORE = date(2026, 7, 26)
TRACTOR, LOADER = 3464, 9001
HOLLAND, KAMAZ, T28 = 102, 103, 7002


def fixture_track():
    rows = []
    with open(os.path.join(FIXTURES, 'track_3464_20260727.csv'),
              encoding='utf-8-sig') as handle:
        for row in csv.DictReader(handle, delimiter=';'):
            stamp = datetime.strptime(row['date'] + ' ' + row['time'],
                                      '%Y-%m-%d %H:%M:%S').replace(tzinfo=TZ)
            rows.append((TRACTOR, int(stamp.timestamp()), float(row['lon']),
                         float(row['lat']), float(row['speed']), 90,
                         int(row['sats'] or -1)))
    return rows


def zone_polygon():
    with open(os.path.join(FIXTURES, 'zone_3208.json'), encoding='utf-8') as handle:
        zone = json.load(handle)
    ring = [[point['x'], point['y']] for point in zone['points']]
    if ring[0] != ring[-1]:
        ring.append(ring[0])
    return json.dumps({'type': 'Polygon', 'coordinates': [ring]})


def loader_track():
    """Погрузчик на площадке: петли по 40 м, 3 часа, запись раз в 30 с."""
    start = int(datetime(2026, 7, 27, 8, 0, tzinfo=TZ).timestamp())
    rows = []
    for i in range(360):
        phase = (i % 20) / 20.0
        rows.append((LOADER, start + i * 30, 64.5700 + 0.0004 * phase,
                     39.9800 + 0.0002 * ((i // 20) % 2), 6.0, 90, 12))
    return rows


def aggregate(unit, reason, points_total, km, day=DAY):
    return GpsDailyAggregate(
        work_date=day, wialon_id=unit, points_total=points_total,
        points_work=points_total // 2, track_km=km, interval_median_s=30.0,
        sats_median=14.0, motion_gaps=0, lost_seconds=0.0, gps_jumps=0,
        reason=reason, method_version='adaptive-alpha-2026-08-12',
        computed_at=datetime(2026, 7, 28, 3, 0, 0))


def seed_gps():
    for table in ('gps_work_polygons', 'gps_daily_aggregates', 'gps_verdicts',
                  'vialon_mappings'):
        db.session.execute(text('DELETE FROM %s' % table))
    db.session.execute(text("DELETE FROM field_contours WHERE source = 'wialon'"))
    org = Organization.query.first()
    tractor = Equipment(name='МТЗ-80.1', plate='80 261 EA', category='mtz',
                        organization_id=org.id)
    loader = Equipment(name='Погрузчик Amkodor', plate='80 373 HA',
                       category='special', organization_id=org.id)
    db.session.add_all([tractor, loader])
    db.session.flush()
    db.session.add_all([
        VialonMapping(vialon_name='МТЗ 261 EA', wialon_id=TRACTOR,
                      equipment_id=tractor.id, skip=False),
        VialonMapping(vialon_name='Погрузчик 373 HA', wialon_id=LOADER,
                      equipment_id=loader.id, skip=False)])
    contour = FieldContour(source='wialon', external_id='3208',
                           name='1508 Нурхон Бобохон',
                           geometry_geojson=zone_polygon())
    db.session.add(contour)
    db.session.flush()
    db.session.add(aggregate(TRACTOR, None, 1598, 32.1))
    db.session.add(GpsWorkPolygon(
        work_date=DAY, wialon_id=TRACTOR, site_number=1, area_ha=8.772,
        minutes=317.4, polygon_geojson=zone_polygon(), contour_id=contour.id,
        alpha_used_m=10.0, pass_spacing_m=5.45, suggested_label='работа'))
    db.session.add(aggregate(LOADER, 'spetstekhnika', 360, 14.4))
    storage.write_points(POINTS_DIR, fixture_track() + loader_track())

    # ── выбор машины: вторая организация, объекты без машины ──
    kogon = Organization(name='Когон ПТЗ МЧЖ', short_name='Когон ПТЗ',
                         sort_order=99)
    db.session.add(kogon)
    db.session.flush()
    holland = Equipment(name='New Holland 7060', plate='80 156 СА',
                        category='yukori', organization_id=kogon.id)
    db.session.add(holland)
    db.session.flush()
    db.session.add_all([
        VialonMapping(vialon_name='NH 156', wialon_id=HOLLAND,
                      equipment_id=holland.id, skip=False),
        VialonMapping(vialon_name='Камаз 80 777 KA', wialon_id=KAMAZ,
                      skip=False)])
    db.session.add(aggregate(HOLLAND, None, 900, 21.0))
    db.session.add(GpsWorkPolygon(
        work_date=DAY, wialon_id=HOLLAND, site_number=1, area_ha=3.1,
        minutes=64.0, polygon_geojson=json.dumps({'type': 'Polygon', 'coordinates': [[
            [64.58, 39.97], [64.59, 39.97], [64.59, 39.975], [64.58, 39.975],
            [64.58, 39.97]]]}),
        alpha_used_m=10.0, pass_spacing_m=5.0, suggested_label='работа',
        operator_label='проезд'))
    db.session.add(aggregate(KAMAZ, None, 300, 80.0))
    db.session.add(aggregate(T28, None, 200, 12.0))
    db.session.add(aggregate(TRACTOR, None, 800, 15.0, day=DAY_BEFORE))
    storage.write_unit_names(POINTS_DIR, [{'id': T28, 'name': 'Т-28 80 990 HA'}])


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
    parser.add_argument('--imagery', action='store_true',
                        help='write FAKE Esri key and Copernicus instance id')
    args = parser.parse_args()
    se.seed_ux_fixtures.guard_disposable(se.DB_PATH)
    os.makedirs(args.state_dir, exist_ok=True)
    os.makedirs(POINTS_DIR, exist_ok=True)
    if args.imagery:
        with open(app.config['MAP_ESRI_KEY_FILE'], 'w', encoding='utf-8') as handle:
            handle.write(FAKE_ESRI_KEY + '\n')
        with open(app.config['MAP_COPERNICUS_INSTANCE_FILE'], 'w',
                  encoding='utf-8') as handle:
            handle.write(FAKE_INSTANCE + '\n')

    with app.app_context():
        db.create_all()
        se.create_non_model_tables()
    se.seed_ux_fixtures.seed(app, db, rows_per_table=14, verbose=False)

    with app.app_context():
        seed_gps()
        db.session.commit()
        with app.test_request_context():
            serializer = app.session_interface.get_signing_serializer(app)
            for name in ('ux_admin', 'ux_admin_uz'):
                user = User.query.filter_by(username=name).one()
                write_state(os.path.join(args.state_dir, name + '.json'),
                            serializer.dumps({'_user_id': str(user.id),
                                              '_fresh': True,
                                              '_csrf_token': 'ux-local'}))

    print('database : %s' % se.DB_PATH)
    print('points   : %s' % POINTS_DIR)
    print('sessions : ux_admin.json, ux_admin_uz.json in the state dir')
    print('serving  : http://127.0.0.1:%d/gps/fact?date=2026-07-27' % args.port)
    sys.stdout.flush()
    app.run(host='127.0.0.1', port=args.port, debug=False,
            use_reloader=False)
    return 0


if __name__ == '__main__':
    sys.exit(main())
