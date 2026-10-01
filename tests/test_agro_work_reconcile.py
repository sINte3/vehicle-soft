# -*- coding: utf-8 -*-
"""agro-work B3: сверка в обе стороны -- окна, сутки GPS, вердикты, N.

Каждое правило ответа владельца на вопрос 4 (28.09) -- отдельным случаем:

  * обычная заявка: окно с дня создания по день «Выполнено» включительно;
  * заведённая сразу «Выполнено»: при неутверждённом N -- отдельная строка
    без вердикта; при N -- день ввода и N дней до него;
  * отменённая работу не покрывает; открытая -- без вердикта до закрытия.

И то, что отличает «работы не было» от «не знаем»: «точек нет» -- молчащий
трекер, а не стоящая машина, и нарушение по нему не выносится.

Запуск: python -m unittest tests.test_agro_work_reconcile -v
"""

import os
import re
import sqlite3
import sys
import unittest
from datetime import date

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if REPO_ROOT not in sys.path:
    sys.path.insert(0, REPO_ROOT)

from agro_work import reconcile as rc                          # noqa: E402
from tests import agro_work_db as dbh                          # noqa: E402

TODAY = date(2026, 9, 28)
D = lambda day: date(2026, 9, day)                             # noqa: E731

W_GA, W_TIME, W_TRIPS, W_NONE, W_UNMARKED = 'w-ga', 'w-time', 'w-trips', 'w-none', 'w-unm'
METHOD_OF = {W_GA: 'ga', W_TIME: 'vremya', W_TRIPS: 'reysy',
             W_NONE: 'ne_sveryaetsya', W_UNMARKED: None}


class Fixture:
    """Синтетическая база: техника, связи Wialon, машины agro-work, GPS."""

    def __init__(self):
        self.path = dbh.make_db()
        self.con = sqlite3.connect(self.path)
        self.number = 0
        con = self.con
        dbh.add_org(con, 1, 'Buxoro')
        dbh.add_org(con, 2, 'Jizzax')
        for eq_id, org, category in ((11, 1, 'mtz'), (12, 1, 'mtz'),
                                     (13, 2, 'special'), (14, 1, 'yuk_transport'),
                                     (15, 1, 'mtz'), (16, 1, 'mtz'),
                                     (17, 2, 'mtz')):
            dbh.add_equipment(con, eq_id, '80 0%d EA' % eq_id, category=category,
                              org_id=org)
        for mapping_id, (unit, eq_id) in enumerate(
                ((1001, 11), (1002, 12), (1003, 13), (1004, 14), (1005, 15),
                 (1006, 15), (1007, 17)), start=1):
            dbh.add_mapping(con, mapping_id, unit, eq_id)
        for transport, eq_id, status in (('T1', 11, 'auto'), ('T2', 12, 'auto'),
                                         ('T3', 13, 'auto'), ('T4', 14, 'auto'),
                                         ('T5', 15, 'auto'), ('T6', 16, 'auto'),
                                         ('T8', None, 'none')):
            con.execute("INSERT INTO agro_work_transports (id, plate_number, "
                        "plate_norm, equipment_id, match_status, first_seen_at, "
                        "last_seen_at) VALUES (?, ?, ?, ?, ?, 't', 't')",
                        (transport, 'P-' + transport, 'P' + transport, eq_id,
                         status))
        for work_type, method in METHOD_OF.items():
            con.execute("INSERT INTO agro_work_work_types (id, name, unit, "
                        "method, first_seen_at, last_seen_at) VALUES (?, ?, ?, "
                        "?, 't', 't')",
                        (work_type, 'name ' + work_type,
                         'HECTARE' if work_type == W_GA else 'HOUR', method))
        con.execute("INSERT INTO agro_work_import_runs (id, started_at, status, "
                    "tool_version) VALUES (1, 't', 'ok', 'v')")
        # Сутки GPS объекта 1001 (машина 11): по одному состоянию на сутки.
        dbh.add_day(con, 1001, '2026-09-10', sites=[(2.0, None)])
        dbh.add_day(con, 1001, '2026-09-11')                        # нет участков
        dbh.add_day(con, 1001, '2026-09-12', reason='net_dvizheniya')
        dbh.add_day(con, 1001, '2026-09-13', sites=[(1.1, 'проезд')])
        dbh.add_day(con, 1001, '2026-09-14', reason='net_tochek')
        # 09-15 у 1001 не посчитаны вовсе.
        for day in (10, 11, 12):
            dbh.add_day(con, 1002, '2026-09-%02d' % day)
        dbh.add_day(con, 1002, '2026-09-13', sites=[(3.5, None), (0.5, 'работа')])
        dbh.add_day(con, 1004, '2026-09-13', sites=[(40.0, None)])  # грузовой
        dbh.add_day(con, 1005, '2026-09-16', sites=[(1.0, None)])
        dbh.add_day(con, 1007, '2026-09-20', sites=[(4.0, None)])
        dbh.add_day(con, 9999, '2026-09-18', sites=[(2.2, None)])   # без машины
        con.commit()

    def app(self, transport='T1', status='COMPLETED', created=10, completed=11,
            initial='IN_PROGRESS', history=True, work_type=W_GA,
            cancelled=None, gone_at=None):
        self.number += 1
        app_id = 'app-%03d' % self.number
        self.con.execute(
            "INSERT INTO agro_work_applications (id, application_number, "
            "transport_id, work_type_id, unit, status, created_at, updated_at, "
            "created_day, first_seen_run_id, last_seen_run_id, "
            "history_updated_at, initial_status, completed_day, cancelled_at, "
            "gone_at) "
            "VALUES (?, ?, ?, ?, 'HECTARE', ?, ?, 'u', ?, 1, 1, ?, ?, ?, ?, ?)",
            (app_id, 'N-%03d' % self.number, transport, work_type, status,
             '2026-09-%02dT08:00:00+05:00' % created,
             '2026-09-%02d' % created, 'u' if history else None,
             initial if history else None,
             ('2026-09-%02d' % completed) if (history and completed) else None,
             cancelled, gone_at))
        self.con.commit()
        return app_id

    def run(self, date_from=10, date_to=20, **kwargs):
        con = sqlite3.connect('file:%s?mode=ro' % self.path.replace('\\', '/'),
                              uri=True)
        try:
            return rc.Reconciliation(con, D(date_from), D(date_to),
                                     today=TODAY, **kwargs)
        finally:
            con.close()

    def forward(self, app_id, **kwargs):
        ctx = self.run(**kwargs)
        rows = [r for r in ctx.forward_rows() if r['app'].id == app_id]
        self.assert_one = len(rows)
        return rows[0]

    def close(self):
        self.con.close()


class DayState(unittest.TestCase):
    def test_the_three_states_and_what_counts_as_work(self):
        self.assertEqual(rc.day_state(None, []), (rc.UNKNOWN, 0.0))
        self.assertEqual(rc.day_state({'reason': 'net_tochek'}, []),
                         (rc.UNKNOWN, 0.0))
        self.assertEqual(rc.day_state({'reason': 'sbor_nepolnyy'}, []),
                         (rc.UNKNOWN, 0.0))
        self.assertEqual(rc.day_state({'reason': 'redkaya_zapis'}, []),
                         (rc.UNKNOWN, 0.0))
        # Причина, которой трек GPS ещё не заводил, -- незнание, а не простой.
        self.assertEqual(rc.day_state({'reason': 'novaya_prichina'}, []),
                         (rc.UNKNOWN, 0.0))

    def test_special_equipment_is_unknown_even_with_old_hectares(self):
        # Трек GPS (PR #153, A1) с 30.09 публикует по спецтехнике только
        # след: причина REASON_TRACK_ONLY из gps/exclusion.py. Участки,
        # насчитанные до правила, сутки работой не делают.
        with open(os.path.join(REPO_ROOT, 'gps', 'exclusion.py'),
                  encoding='utf-8') as fh:
            reason = re.search(r'^REASON_TRACK_ONLY = "([a-z_]+)"$', fh.read(),
                               re.M).group(1)
        self.assertEqual(reason, 'spetstekhnika')
        self.assertEqual(rc.day_state({'reason': reason},
                                      [{'area_ha': 5.0, 'operator_label': None}]),
                         (rc.UNKNOWN, 0.0))
        self.assertEqual(rc.day_state({'reason': 'net_dvizheniya'}, []),
                         (rc.IDLE, 0.0))
        self.assertEqual(rc.day_state({'reason': None}, []), (rc.IDLE, 0.0))
        self.assertEqual(rc.day_state({'reason': None},
                                      [{'area_ha': 1.0, 'operator_label': 'проезд'}]),
                         (rc.IDLE, 0.0))
        # Участок без ответа -- работа: он уже прошёл порог 0,3 га.
        self.assertEqual(rc.day_state({'reason': None},
                                      [{'area_ha': 1.25, 'operator_label': None},
                                       {'area_ha': 0.5, 'operator_label': 'работа'},
                                       {'area_ha': 9.0, 'operator_label': 'проезд'}]),
                         (rc.WORK, 1.75))


class Forward(unittest.TestCase):
    def setUp(self):
        self.fx = Fixture()

    def tearDown(self):
        self.fx.close()

    def test_work_inside_the_window_confirms_the_application(self):
        app = self.fx.app(created=10, completed=11)
        row = self.fx.forward(app)
        self.assertEqual(row['verdict'], rc.V_WORK)
        self.assertEqual(row['work_days'], [D(10)])
        self.assertEqual(row['gps_ha'], 2.0)

    def test_all_days_known_and_none_worked_is_the_violation(self):
        # 11: участков нет; 12: движения нет; 13: единственный участок -- проезд.
        app = self.fx.app(created=11, completed=13)
        row = self.fx.forward(app)
        self.assertEqual(row['verdict'], rc.V_NO_WORK)
        self.assertEqual(row['unknown_days'], 0)

    def test_a_silent_tracker_is_not_a_standing_machine(self):
        # 13: участок -- проезд (работы нет); 14: точек нет. Единственные
        # неизвестные сутки окна -- «точек нет», и только они отличают
        # «не знаем» от «работы не было».
        app = self.fx.app(created=13, completed=14)
        row = self.fx.forward(app)
        self.assertEqual(row['verdict'], rc.V_NONE)
        self.assertEqual(row['reason'], rc.R_NO_GPS)
        self.assertEqual(row['unknown_days'], 1)

    def test_a_day_nobody_computed_is_not_a_day_without_work(self):
        # 15-е у 1001 не посчитано вовсе.
        app = self.fx.app(created=15, completed=15)
        row = self.fx.forward(app)
        self.assertEqual((row['verdict'], row['reason']), (rc.V_NONE, rc.R_NO_GPS))

    def test_the_window_ends_on_the_closing_day_inclusive(self):
        app = self.fx.app(created=11, completed=11)
        self.assertEqual(self.fx.forward(app)['verdict'], rc.V_NO_WORK)
        app = self.fx.app(created=9, completed=10)
        self.assertEqual(self.fx.forward(app)['verdict'], rc.V_WORK)

    def test_n_is_the_one_the_owner_approved(self):
        # Растяжка: N = 2 утвердил владелец 29.09 по замеру на копии боевой
        # базы (вопрос 4) и подтвердил 30.09: ввод задним числом через 3-6
        # суток -- нарушение, его сверка и должна показывать. Другое число --
        # только новым замером и его словом.
        self.assertEqual(rc.BACKDATED_LOOKBACK_DAYS, 2)
        app = self.fx.app(created=12, completed=12, initial='COMPLETED')
        self.assertEqual(self.fx.forward(app)['window'], (D(10), D(12)))

    def test_backdated_waits_for_n_and_then_looks_back_n_days(self):
        app = self.fx.app(created=12, completed=12, initial='COMPLETED')
        row = self.fx.forward(app, lookback=None)
        self.assertEqual((row['verdict'], row['reason']),
                         (rc.V_NONE, rc.R_BACKDATED))
        row = self.fx.forward(app, lookback=2)
        self.assertEqual(row['window'], (D(10), D(12)))
        self.assertEqual(row['verdict'], rc.V_WORK)
        row = self.fx.forward(app, lookback=1)
        self.assertEqual(row['verdict'], rc.V_NO_WORK)

    def test_open_cancelled_and_historyless_have_no_verdict(self):
        cases = [
            (self.fx.app(status='IN_PROGRESS', completed=None), rc.R_OPEN),
            (self.fx.app(status='PENDING', completed=None), rc.R_OPEN),
            (self.fx.app(status='CANCELLED', completed=None,
                         cancelled='2026-09-10T12:00:00+05:00'), rc.R_CANCELLED),
            (self.fx.app(history=False), rc.R_NO_HISTORY),
            (self.fx.app(completed=None), rc.R_NO_CLOSE_DATE),
        ]
        for app_id, reason in cases:
            row = self.fx.forward(app_id)
            self.assertEqual((row['verdict'], row['reason']), (rc.V_NONE, reason),
                             app_id)

    def test_deleted_in_agro_work_has_no_verdict_and_keeps_its_period(self):
        # Ответ владельца 11: удалённая заявка -- как отменённая, но в сверке
        # остаётся с пометкой. Без удаления у неё была бы «работа была».
        app = self.fx.app(created=10, completed=11,
                          gone_at='2026-09-21 03:00:00')
        row = self.fx.forward(app)
        self.assertEqual((row['verdict'], row['reason'], row['window']),
                         (rc.V_NONE, rc.R_GONE, None))
        ids = {r['app'].id for r in self.fx.run(date_from=10, date_to=11)
               .forward_rows()}
        self.assertIn(app, ids)
        ids = {r['app'].id for r in self.fx.run(date_from=12, date_to=20)
               .forward_rows()}
        self.assertNotIn(app, ids)

    def test_a_deleted_open_application_is_listed_until_its_deletion(self):
        app = self.fx.app(status='IN_PROGRESS', created=10, completed=None,
                          gone_at='2026-09-14 20:00:00')       # 15.09 по UTC+5
        in_range = {r['app'].id for r in self.fx.run(date_from=15, date_to=15)
                    .forward_rows()}
        after = {r['app'].id for r in self.fx.run(date_from=16, date_to=20)
                 .forward_rows()}
        self.assertIn(app, in_range)
        self.assertNotIn(app, after)

    def test_only_the_hectare_method_is_judged(self):
        for work_type, reason in ((W_TIME, rc.R_METHOD_TIME),
                                  (W_TRIPS, rc.R_METHOD_TRIPS),
                                  (W_NONE, rc.R_METHOD_NONE),
                                  (W_UNMARKED, rc.R_METHOD_UNMARKED)):
            row = self.fx.forward(self.fx.app(work_type=work_type))
            self.assertEqual((row['verdict'], row['reason']), (rc.V_NONE, reason))

    def test_machine_reasons_are_named_not_guessed(self):
        for transport, reason in (('T8', rc.R_NOT_MATCHED),
                                  ('T6', rc.R_NO_WIALON),
                                  ('T5', rc.R_MANY_OBJECTS),
                                  ('T4', rc.R_EXCLUDED)):
            row = self.fx.forward(self.fx.app(transport=transport))
            self.assertEqual((row['verdict'], row['reason']), (rc.V_NONE, reason),
                             transport)

    def test_a_window_reaching_past_the_period_is_judged_whole(self):
        # Период кончается 10-го, окно 10..13, работа только 13-го у 1002.
        app = self.fx.app(transport='T2', created=10, completed=13)
        row = self.fx.forward(app, date_from=1, date_to=10)
        self.assertEqual(row['verdict'], rc.V_WORK)
        self.assertEqual(row['work_days'], [D(13)])

    def test_the_period_lists_by_window_open_ones_until_closed(self):
        early = self.fx.app(created=1, completed=2)
        opened = self.fx.app(status='IN_PROGRESS', created=1, completed=None)
        ids = {r['app'].id for r in self.fx.run(date_from=10, date_to=20)
               .forward_rows()}
        self.assertNotIn(early, ids)
        self.assertIn(opened, ids)


class Reverse(unittest.TestCase):
    def setUp(self):
        self.fx = Fixture()

    def tearDown(self):
        self.fx.close()

    def rows(self, **kwargs):
        return {(r['equipment_id'], r['day']): r
                for r in self.fx.run(**kwargs).reverse_rows()}

    def test_covered_uncovered_and_not_in_agro_work(self):
        self.fx.app(created=10, completed=11)
        rows = self.rows()
        self.assertEqual(rows[(11, D(10))]['coverage'], rc.C_COVERED)
        self.assertEqual(rows[(12, D(13))]['coverage'], rc.C_UNCOVERED)
        self.assertEqual(rows[(12, D(13))]['gps_ha'], 4.0)
        self.assertEqual((rows[(17, D(20))]['coverage'], rows[(17, D(20))]['reason']),
                         (rc.C_NONE, rc.R_NOT_IN_AGRO))

    def test_a_cancelled_application_covers_nothing(self):
        self.fx.app(transport='T2', status='CANCELLED', created=12, completed=None,
                    cancelled='2026-09-14T09:00:00+05:00')
        self.assertEqual(self.rows()[(12, D(13))]['coverage'], rc.C_UNCOVERED)

    def test_a_deleted_application_covers_nothing(self):
        # Без удаления заявка 12..13 покрыла бы работу 13-го.
        self.fx.app(transport='T2', created=12, completed=13,
                    gone_at='2026-09-20 03:00:00')
        row = self.rows()[(12, D(13))]
        self.assertEqual((row['coverage'], row['reason']),
                         (rc.C_UNCOVERED, None))

    def test_a_deleted_open_application_does_not_suspend_the_verdict(self):
        self.fx.app(transport='T2', status='IN_PROGRESS', created=12,
                    completed=None, gone_at='2026-09-20 03:00:00')
        self.assertEqual(self.rows()[(12, D(13))]['coverage'], rc.C_UNCOVERED)

    def test_an_open_application_suspends_the_verdict(self):
        self.fx.app(transport='T2', status='IN_PROGRESS', created=12, completed=None)
        row = self.rows()[(12, D(13))]
        self.assertEqual((row['coverage'], row['reason']),
                         (rc.C_NONE, rc.R_OPEN_COVERS))
        # Открытая заявка, созданная ПОСЛЕ суток работы, их не покрывает.
        fx2 = Fixture()
        try:
            fx2.app(transport='T2', status='IN_PROGRESS', created=14, completed=None)
            row = {(r['equipment_id'], r['day']): r
                   for r in fx2.run().reverse_rows()}[(12, D(13))]
            self.assertEqual(row['coverage'], rc.C_UNCOVERED)
        finally:
            fx2.close()

    def test_a_later_backdated_application_may_cover_until_n_is_set(self):
        self.fx.app(transport='T2', created=15, completed=15, initial='COMPLETED')
        row = self.rows(lookback=None)[(12, D(13))]
        self.assertEqual((row['coverage'], row['reason']),
                         (rc.C_NONE, rc.R_MAYBE_BACKDATED))
        self.assertEqual(self.rows(lookback=1)[(12, D(13))]['coverage'],
                         rc.C_UNCOVERED)
        self.assertEqual(self.rows(lookback=2)[(12, D(13))]['coverage'],
                         rc.C_COVERED)

    def test_an_earlier_backdated_application_cannot_cover(self):
        self.fx.app(transport='T2', created=12, completed=12, initial='COMPLETED')
        self.assertEqual(self.rows()[(12, D(13))]['coverage'], rc.C_UNCOVERED)

    def test_an_application_without_history_suspends_while_it_could_cover(self):
        self.fx.app(transport='T2', created=20, history=False)
        row = self.rows(lookback=None)[(12, D(13))]
        self.assertEqual((row['coverage'], row['reason']),
                         (rc.C_NONE, rc.R_WINDOW_UNKNOWN))
        # При N = 3 заявка от 20-го не дотягивается до 13-го ни одним окном.
        self.assertEqual(self.rows(lookback=3)[(12, D(13))]['coverage'],
                         rc.C_UNCOVERED)

    def test_several_objects_and_excluded_machines(self):
        rows = self.rows()
        self.assertEqual(rows[(15, D(16))]['reason'], rc.R_MANY_OBJECTS)
        self.assertIsNone(rows[(15, D(16))]['gps_ha'])
        self.assertNotIn((14, D(13)), rows)          # грузовой не считается

    def test_work_of_objects_without_a_machine_is_counted_apart(self):
        self.assertEqual(self.fx.run().orphan_work_days(), 1)


class ScopeAndSummary(unittest.TestCase):
    def setUp(self):
        self.fx = Fixture()
        self.covered = self.fx.app(created=10, completed=11)
        self.violation = self.fx.app(created=11, completed=13)
        self.unmatched = self.fx.app(transport='T8')
        self.other_org = self.fx.app(transport='T3')

    def tearDown(self):
        self.fx.close()

    def test_an_organisation_sees_its_machines_and_no_unmatched_rows(self):
        ctx = self.fx.run(org_ids=[2])
        self.assertEqual({r['app'].id for r in ctx.forward_rows()},
                         {self.other_org})
        self.assertEqual({r['equipment_id'] for r in ctx.reverse_rows()}, {17})
        everything = self.fx.run()
        self.assertIn(self.unmatched, {r['app'].id for r in everything.forward_rows()})

    def test_the_summary_adds_up_by_group_and_in_total(self):
        ctx = self.fx.run()
        groups, total = ctx.summary()
        mtz = groups[(1, 'mtz')]
        self.assertEqual(mtz['app_rabota_est'], 1)
        self.assertEqual(mtz['app_rabota_net'], 1)
        self.assertEqual(mtz['day_pokryta'], 1)
        self.assertEqual(mtz['day_bez_zayavki'], 1)
        self.assertEqual(groups[(None, None)]['app_reason_' + rc.R_NOT_MATCHED], 1)
        for counter in list(groups.values()) + [total]:
            self.assertEqual(counter['applications'],
                             counter['app_rabota_est'] + counter['app_rabota_net']
                             + counter['app_bez_verdikta'])
            self.assertEqual(counter['work_days'],
                             counter['day_pokryta'] + counter['day_bez_zayavki']
                             + counter['day_bez_verdikta'])
        self.assertEqual(total['applications'], 4)


class Lags(unittest.TestCase):
    def test_lag_from_the_last_work_day_to_the_closing_mark(self):
        fx = Fixture()
        try:
            fx.app(created=10, completed=11)             # работа 10 -> лаг 1
            fx.app(created=10, completed=10)             # лаг 0
            fx.app(created=10, completed=12)             # 11, 12 известны -> лаг 2
            fx.app(created=10, completed=15)             # 14, 15 неизвестны -> вне
            fx.app(created=12, completed=12, initial='COMPLETED')  # задним числом
            fx.app(transport='T2', created=12, completed=13)       # 1002 -> лаг 0
            fx.app(transport='T2', created=10, completed=12)       # работы нет
            con = sqlite3.connect(fx.path)
            try:
                samples, excluded = rc.measure_lags(con, today=TODAY)
            finally:
                con.close()
        finally:
            fx.close()
        self.assertEqual(samples['all']['n'], 4)
        self.assertEqual(samples['all']['histogram'], {0: 2, 1: 1, 2: 1})
        self.assertEqual(samples['all']['max'], 2)
        self.assertEqual(samples['all']['covered_by_n'][0], 0.5)
        self.assertEqual(samples['all']['covered_by_n'][1], 0.75)
        self.assertEqual(samples['all']['covered_by_n'][2], 1.0)
        self.assertEqual(excluded['neizvestnye_sutki_posle_raboty'], 1)
        self.assertEqual(excluded['net_raboty_v_okne'], 1)
        self.assertEqual(samples['method_ga']['n'], 4)

    def test_percentile_is_the_nearest_rank(self):
        self.assertIsNone(rc.percentile([], 0.5))
        self.assertEqual(rc.percentile([0, 0, 1, 2], 0.5), 0)
        self.assertEqual(rc.percentile([0, 0, 1, 2], 0.9), 2)
        self.assertEqual(rc.percentile([5], 0.95), 5)
        self.assertEqual(rc.percentile(list(range(1, 11)), 0.9), 9)


if __name__ == '__main__':
    unittest.main()
