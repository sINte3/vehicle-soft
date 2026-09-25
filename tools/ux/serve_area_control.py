# -*- coding: utf-8 -*-
"""Одноразовый стенд экрана «Контроль площади DJI» для tools/ux/check_area_control.mjs.

Тот же временный стенд, что serve_ephemeral.py (временная база в каталоге
temp, генеральный засев), плюс то, чего генеральный засев дать не может:

* расчёты площади текущей версии алгоритма -- 4 дрона x 5 дней, в окне по
  умолчанию (последние 30 дней): обычные вылеты, цепочки A -> B -> C,
  доказанная корректировка, частичная, ожидающая V4, требующая решения;
  есть дни без открытых записей и дрон без них вовсе -- их обязан скрыть
  режим «Развернуть проблемные»;
* одно решение администратора с длинной причиной -- настоящим писателем
  control_store, затем триггеры миграции (иначе экран честно предупреждает
  администратора, что их нет);
* подписанные сессии ux_admin (RU) и ux_admin_uz (UZ) -- файлы storageState
  для Playwright. Пароль не вводится и не печатается.

routes.json репозитория НЕ переписывается (serve_ephemeral.main не
вызывается). Всё живёт до выхода процесса.

Запуск (из корня репозитория; каталог --state-dir -- вне репозитория):

    python tools/ux/serve_area_control.py --port 5099 --state-dir <каталог>

ВСЕ ДАННЫЕ СИНТЕТИЧЕСКИЕ.
"""

import argparse
import json
import os
import sys
from datetime import datetime, timedelta

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

import serve_ephemeral as se  # noqa: E402  (config -> временная база ДО app)

from sqlalchemy import text  # noqa: E402

import dji_area  # noqa: E402
import migrate_drone_area_control_v2_001 as mig  # noqa: E402
from dji_area import control_store  # noqa: E402
from dji_area import resolver as R  # noqa: E402
from dji_area import store as dji_store  # noqa: E402
from models import (DjiAreaCalculation, DroneUnit, User,  # noqa: E402
                    UserModulePermission)

app, db = se.app, se.db
# [REASON]: стенд проверяет правку шаблона. Без перезагрузки Jinja отдавал
# бы шаблон, прочитанный при первом запросе, и проверка шла бы по старому
# коду при новом CSS -- тихо и правдоподобно.
app.config['TEMPLATES_AUTO_RELOAD'] = True
app.jinja_env.auto_reload = True

LONG_COMMENT = ('Проверено визуально в DJI: плоский счётчик при наблюдённом '
                'распылении, площадь повторяет предыдущую Auto-работу этого '
                'борта; повторная проверка по маршруту и по V4 подтвердила '
                'полный фантом. ') * 2


def seed_area(today):
    """Расчёты площади. Возвращает id записей «требует решения»."""
    units = DroneUnit.query.order_by(DroneUnit.number).limit(4).all()
    for unit in units:
        unit.hardware_id = 'SYNTHETIC-HW-%d-NOT-REAL' % unit.number
    counter = [910000]

    def add(status, eligibility, raw, corrected, unit, day, minute,
            **extra):
        counter[0] += 1
        start = datetime(day.year, day.month, day.day, 3, 0) \
            + timedelta(minutes=minute)
        calc = DjiAreaCalculation(
            flight_id=counter[0], provider_account_id='SYNTHETIC',
            hardware_id=unit.hardware_id, hardware_id_source='SYNTHETIC',
            area_algorithm_version=dji_area.AREA_ALGORITHM_VERSION,
            calculation_input_hash='SYN-%d' % counter[0],
            calculated_at=datetime.utcnow(),
            start_at_utc=start, end_at_utc=start + timedelta(minutes=20),
            report_timezone=dji_area.REPORT_TIMEZONE,
            report_start_date=day, raw_area_m2=raw, raw_area_source='CARD',
            corrected_recorded_area_m2=corrected,
            controller_delta_area_m2=extra.get('delta'),
            area_status=status, aggregation_eligibility=eligibility,
            anomaly_flags_json=json.dumps(extra.get('flags', [])))
        if extra.get('base'):
            calc.structural_candidate = True
            calc.scalar_source_check = True
            calc.candidate_base_flight_id = extra['base']
            calc.bridge_flight_ids_json = json.dumps(extra['bridges'])
            calc.structural_rule_version = dji_area.STRUCTURAL_RULE_VERSION
            calc.v4_revision_id = None if extra.get('no_v4') else 1
        db.session.add(calc)
        return counter[0]

    review = []
    for index, unit in enumerate(units):
        for back in range(5):
            day = today - timedelta(days=back)
            minute = 0
            for _ in range(4):
                add(R.RAW_CORROBORATED, R.AGG_CERTIFIED, 30000.0, 30000.0,
                    unit, day, minute)
                minute += 7
            a = add(R.RAW_CORROBORATED, R.AGG_CERTIFIED, 90000.0, 90000.0,
                    unit, day, minute)
            b = add(R.RAW_CORROBORATED, R.AGG_CERTIFIED, 6000.0, 6000.0,
                    unit, day, minute + 5)
            chain = {'base': a, 'bridges': [b]}
            add(R.COUNTER_FLAT_RAW_OVERSTATED, R.AGG_CERTIFIED, 90000.0, 0.0,
                unit, day, minute + 10, **chain)
            add(R.PARTIAL_RECORDED_OVERSTATEMENT, R.AGG_CERTIFIED, 80000.0,
                5000.0, unit, day, minute + 15, **chain)
            # Четвёртый дрон -- без открытых записей: в режиме «проблемные»
            # он обязан скрыться целиком.
            if index < 3 and back % 2 == 0:
                add(R.RAW_UNVERIFIED, R.AGG_PROVISIONAL, 70000.0, 70000.0,
                    unit, day, minute + 20, no_v4=True, **chain)
            if index % 2 == 0 and back < 3:
                review.append(add(
                    R.COUNTER_FLAT_RAW_OVERSTATED, R.AGG_UNRESOLVED, 60000.0,
                    0.0, unit, day, minute + 25, delta=0.0,
                    flags=['APPLICATION_WITH_FLAT_COUNTER'], **chain))
    return review


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

    with app.app_context():
        # Генеральный засев кладёт в эти таблицы случайные строки: на
        # одноразовой базе они заменяются осмысленными.
        for table in ('drone_area_cycle_runs', 'drone_area_decisions',
                      'dji_area_calculations'):
            db.session.execute(text('DELETE FROM %s' % table))
        today = (datetime.utcnow() + timedelta(hours=5)).date()
        review = seed_area(today)
        db.session.commit()
        admin = User.query.filter_by(username='ux_admin').one()
        con = dji_store.connect(se.DB_PATH)
        try:
            control_store.record_decision(
                con, review[0], 'CONFIRM_FULL_PHANTOM', LONG_COMMENT, True,
                False, None, admin.id, 'ux_admin')
            for _name, ddl in mig.TRIGGERS:
                con.execute(ddl)
        finally:
            con.close()
        viewer = User.query.filter_by(username='ux_viewer').one()
        db.session.add(UserModulePermission(user_id=viewer.id,
                                            module_code='drones',
                                            has_access=True))
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
    print('sessions : ux_admin.json, ux_admin_uz.json in the state dir')
    print('serving  : http://127.0.0.1:%d/drones/area-control' % args.port)
    sys.stdout.flush()
    app.run(host='127.0.0.1', port=args.port, debug=False,
            use_reloader=False)
    return 0


if __name__ == '__main__':
    sys.exit(main())
