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
            cancelled=None, gone_at=None, volume=None, unit='HECTARE'):
        self.number += 1
        app_id = 'app-%03d' % self.number
        self.con.execute(
            "INSERT INTO agro_work_applications (id, application_number, "
            "transport_id, work_type_id, unit, volume, status, created_at, "
            "updated_at, created_day, first_seen_run_id, last_seen_run_id, "
            "history_updated_at, initial_status, completed_day, cancelled_at, "
            "gone_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'u', ?, 1, 1, ?, ?, ?, ?, ?)",
            (app_id, 'N-%03d' % self.number, transport, work_type, unit,
             volume, status, '2026-09-%02dT08:00:00+05:00' % created,
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


class LateBackdated(unittest.TestCase):
    """B4, пункт 4: у суток «без заявки» названа поздняя заявка задним числом."""

    def setUp(self):
        self.fx = Fixture()

    def tearDown(self):
        self.fx.close()

    def day13(self, **kwargs):
        # Машина 12 (T2) работала 13-го: объект 1002, участок 3,5 га.
        return {(r['equipment_id'], r['day']): r
                for r in self.fx.run(**kwargs).reverse_rows()}[(12, D(13))]

    def backdated(self, created, **kwargs):
        return self.fx.app(transport='T2', created=created, completed=created,
                           initial='COMPLETED', **kwargs)

    def test_a_late_backdated_application_is_named_and_the_day_stays_uncovered(self):
        self.backdated(18)
        row = self.day13()
        self.assertEqual(row['coverage'], rc.C_UNCOVERED)
        self.assertEqual(row['late_app'].number, 'N-001')
        self.assertEqual(row['late_days'], 5)

    def test_within_n_it_covers_and_is_not_called_late(self):
        self.backdated(15)                          # окно 13..15 при N = 2
        row = self.day13()
        self.assertEqual(row['coverage'], rc.C_COVERED)
        self.assertIsNone(row['late_app'])

    def test_the_nearest_one_is_named(self):
        self.backdated(19)
        self.backdated(17)
        row = self.day13()
        self.assertEqual((row['late_app'].number, row['late_days']), ('N-002', 4))

    def test_only_backdated_ones_are_late(self):
        # Обычная заявка, созданная после работы, покрывает с дня создания
        # (правило 4) -- её опоздание не выдумывается.
        self.fx.app(transport='T2', created=16, completed=17)
        row = self.day13()
        self.assertEqual(row['coverage'], rc.C_UNCOVERED)
        self.assertIsNone(row['late_app'])

    def test_a_deleted_or_an_earlier_backdated_one_is_not_named(self):
        self.backdated(18, gone_at='2026-09-20 03:00:00')
        self.backdated(12)                          # окно 10..12, раньше суток
        row = self.day13()
        self.assertEqual(row['coverage'], rc.C_UNCOVERED)
        self.assertIsNone(row['late_app'])

    def test_with_n_unset_the_day_stays_without_verdict(self):
        self.backdated(18)
        row = self.day13(lookback=None)
        self.assertEqual((row['coverage'], row['reason'], row['late_app']),
                         (rc.C_NONE, rc.R_MAYBE_BACKDATED, None))

    def test_what_a_wider_n_would_cover_is_named_late_within_it(self):
        # Предпросмотр N = 6 от 30.09 переводил в покрытые ровно такие сутки:
        # при N = 2 они «без заявки», и у каждой названа заявка, опоздавшая
        # на 3..6 суток. Сутки, до которых N = 6 не дотягивается, -- тоже
        # названы, но опоздание больше шести.
        self.backdated(18)                          # 13-е: 5 суток
        narrow, wide = self.day13(), self.day13(lookback=6)
        self.assertEqual(wide['coverage'], rc.C_COVERED)
        self.assertEqual(narrow['coverage'], rc.C_UNCOVERED)
        self.assertTrue(2 < narrow['late_days'] <= 6)
        fx = Fixture()
        try:
            fx.app(transport='T2', created=21, completed=21, initial='COMPLETED')
            rows = {(r['equipment_id'], r['day']): r
                    for r in fx.run(lookback=6).reverse_rows()}
            self.assertEqual(rows[(12, D(13))]['coverage'], rc.C_UNCOVERED)
            self.assertEqual(rows[(12, D(13))]['late_days'], 8)
        finally:
            fx.close()

    def test_the_summary_and_the_machine_days_carry_it(self):
        self.backdated(18)
        ctx = self.fx.run()
        _, total = ctx.summary()
        # «Без заявки» -- двое суток (ещё машина 11, 10-го), поздняя заявка
        # -- у одних.
        self.assertEqual(total['day_' + rc.C_UNCOVERED], 2)
        self.assertEqual(total[rc.DAY_LATE], 1)
        day = {d['day']: d for d in ctx.machine_days(12)}[D(13)]
        self.assertEqual(day['coverage'][0], rc.C_UNCOVERED)
        self.assertEqual((day['late'][0].number, day['late'][1]), ('N-001', 5))


class WhatCameAfter(unittest.TestCase):
    """B5, решение владельца 07.10: «не заведена совсем» отдельно от
    «заведена обычным порядком уже после работы»."""

    def setUp(self):
        self.fx = Fixture()

    def tearDown(self):
        self.fx.close()

    def rows(self, **kwargs):
        return {(r['equipment_id'], r['day']): r
                for r in self.fx.run(**kwargs).reverse_rows()}

    def day13(self, **kwargs):
        # Машина 12 (T2) работала 13-го: объект 1002, участок 3,5 га.
        return self.rows(**kwargs)[(12, D(13))]

    def after(self, row):
        return (row['coverage'], row['after'],
                row['later_app'].number if row['later_app'] else None,
                row['later_days'])

    def test_without_any_later_application_it_was_not_entered_at_all(self):
        self.assertEqual(self.after(self.day13()),
                         (rc.C_UNCOVERED, rc.A_NOT_ENTERED, None, None))

    def test_a_later_ordinary_application_is_named_and_the_day_stays_uncovered(self):
        # Окно обычной заявки -- с дня создания (правило 4): 16..17 сутки
        # 13-го не покрывает, вердикт тот же.
        self.fx.app(transport='T2', created=16, completed=17)
        row = self.day13()
        self.assertEqual(self.after(row),
                         (rc.C_UNCOVERED, rc.A_ORDINARY, 'N-001', 3))
        self.assertIsNone(row['late_app'])
        self.assertFalse(row['later_app'].is_open)

    def test_an_open_one_is_ordinary_and_says_it_is_open(self):
        self.fx.app(transport='T2', status='IN_PROGRESS', created=15,
                    completed=None)
        row = self.day13()
        self.assertEqual(self.after(row),
                         (rc.C_UNCOVERED, rc.A_ORDINARY, 'N-001', 2))
        self.assertTrue(row['later_app'].is_open)

    def test_the_nearest_one_is_named(self):
        self.fx.app(transport='T2', created=19, completed=19)
        self.fx.app(transport='T2', status='PENDING', created=16, completed=None)
        self.fx.app(transport='T2', created=17, completed=18)
        self.assertEqual(self.after(self.day13()),
                         (rc.C_UNCOVERED, rc.A_ORDINARY, 'N-002', 3))

    def test_cancelled_and_deleted_ones_are_not_an_entry(self):
        # Ответы 4 и 11: отменённая и удалённая работу не покрывают -- и
        # заведённой работой не считаются.
        self.fx.app(transport='T2', status='CANCELLED', created=15,
                    completed=None, cancelled='2026-09-16T08:00:00+05:00')
        self.fx.app(transport='T2', created=16, completed=17,
                    gone_at='2026-09-20 03:00:00')
        self.assertEqual(self.after(self.day13()),
                         (rc.C_UNCOVERED, rc.A_NOT_ENTERED, None, None))

    def test_an_application_of_another_machine_does_not_count(self):
        self.fx.app(transport='T1', created=16, completed=17)
        self.assertEqual(self.day13()['after'], rc.A_NOT_ENTERED)

    def test_an_earlier_application_is_not_a_later_one(self):
        self.fx.app(transport='T2', created=10, completed=12)    # окно 10..12
        self.assertEqual(self.after(self.day13()),
                         (rc.C_UNCOVERED, rc.A_NOT_ENTERED, None, None))

    def test_one_entered_on_the_day_covers_it_and_nothing_is_said_after(self):
        self.fx.app(transport='T2', created=13, completed=14)
        row = self.day13()
        self.assertEqual((row['coverage'], row['after'], row['later_app']),
                         (rc.C_COVERED, None, None))

    def test_a_late_backdated_one_comes_first(self):
        # B4 называет её; обычная, заведённая раньше неё, уже не нужна.
        self.fx.app(transport='T2', created=15, completed=16)
        self.fx.app(transport='T2', created=18, completed=18, initial='COMPLETED')
        row = self.day13()
        self.assertEqual(self.after(row),
                         (rc.C_UNCOVERED, rc.A_BACKDATED, None, None))
        self.assertEqual((row['late_app'].number, row['late_days']), ('N-002', 5))

    def test_unknown_entry_order_is_named_apart(self):
        # Без истории статусов задним ли числом заведена заявка, неизвестно;
        # так же -- незнакомый статус. Это не «обычная позже» и не «не
        # заведена совсем».
        self.fx.app(transport='T2', created=18, completed=18, history=False)
        self.assertEqual(self.after(self.day13()),
                         (rc.C_UNCOVERED, rc.A_UNCLEAR, 'N-001', 5))
        fx = Fixture()
        try:
            fx.app(transport='T2', status='ARCHIVED', created=17, completed=None)
            row = {(r['equipment_id'], r['day']): r
                   for r in fx.run().reverse_rows()}[(12, D(13))]
            self.assertEqual((row['after'], row['later_app'].number,
                              row['later_days']), (rc.A_UNCLEAR, 'N-001', 4))
        finally:
            fx.close()

    def test_a_known_ordinary_one_wins_over_a_nearer_unknown_one(self):
        self.fx.app(transport='T2', created=16, completed=16, history=False)
        self.fx.app(transport='T2', created=19, completed=19)
        self.assertEqual(self.after(self.day13()),
                         (rc.C_UNCOVERED, rc.A_ORDINARY, 'N-002', 6))

    def test_covered_and_suspended_days_say_nothing_after(self):
        self.fx.app(transport='T1', created=10, completed=10)  # 11-й, 10-е
        self.fx.app(transport='T2', status='IN_PROGRESS', created=12,
                    completed=None)                             # 12-я, 13-е
        rows = self.rows()
        self.assertEqual((rows[(11, D(10))]['coverage'], rows[(11, D(10))]['after']),
                         (rc.C_COVERED, None))
        self.assertEqual((rows[(12, D(13))]['coverage'], rows[(12, D(13))]['after']),
                         (rc.C_NONE, None))

    def test_the_four_parts_add_up_to_the_uncovered_days(self):
        con = self.fx.con
        for day in (16, 18):
            dbh.add_day(con, 1002, '2026-09-%02d' % day, sites=[(2.0, None)])
        dbh.add_day(con, 1001, '2026-09-20', sites=[(1.5, None)])
        con.commit()
        self.fx.app(transport='T1', created=15, completed=15,
                    initial='COMPLETED')      # 11-я, 10-е: задним числом, 5
        self.fx.app(transport='T1', created=25, completed=25,
                    history=False)            # 11-я, 20-е: порядок неизвестен
        self.fx.app(transport='T2', created=17, completed=17)
        # 12-я: 13-е и 16-е -- обычная от 17-го (4 и 1 сутки), 18-е -- после
        # него заявок нет.
        ctx = self.fx.run()
        rows = {(r['equipment_id'], r['day']): r for r in ctx.reverse_rows()}
        self.assertEqual(
            {key: (row['after'], row['late_days'] or row['later_days'])
             for key, row in rows.items() if row['coverage'] == rc.C_UNCOVERED},
            {(11, D(10)): (rc.A_BACKDATED, 5), (11, D(20)): (rc.A_UNCLEAR, 5),
             (12, D(13)): (rc.A_ORDINARY, 4), (12, D(16)): (rc.A_ORDINARY, 1),
             (12, D(18)): (rc.A_NOT_ENTERED, None)})
        groups, total = ctx.summary()
        self.assertEqual(
            [total[rc.DAY_AFTER + kind] for kind in rc.AFTER_KINDS], [1, 2, 1, 1])
        for counter in list(groups.values()) + [total]:
            self.assertEqual(counter['day_' + rc.C_UNCOVERED],
                             sum(counter[rc.DAY_AFTER + kind]
                                 for kind in rc.AFTER_KINDS))
            self.assertEqual(counter[rc.DAY_LATE],
                             counter[rc.DAY_AFTER + rc.A_BACKDATED])
        days = {d['day']: d for d in ctx.machine_days(12)}
        self.assertEqual((days[D(13)]['after'][0], days[D(13)]['after'][1].number,
                          days[D(13)]['after'][2]), (rc.A_ORDINARY, 'N-003', 4))
        self.assertEqual(days[D(18)]['after'], (rc.A_NOT_ENTERED, None, None))
        self.assertIsNone(days[D(10)]['after'])         # у 12-й 10-го не работа


class OpenApplications(unittest.TestCase):
    """B4, пункт 5: открытые заявки от самой старой, суток с ввода."""

    def setUp(self):
        self.fx = Fixture()

    def tearDown(self):
        self.fx.close()

    def test_oldest_first_with_days_since_entry(self):
        self.fx.app(transport='T1', status='PENDING', created=10, completed=None)
        self.fx.app(transport='T2', status='IN_PROGRESS', created=3, completed=None)
        self.fx.app(transport='T1', created=5, completed=6)            # закрыта
        self.fx.app(transport='T2', status='IN_PROGRESS', created=4,
                    completed=None, gone_at='2026-09-20 03:00:00')     # удалена
        self.fx.app(transport='T1', status='PENDING', created=25,
                    completed=None)                                     # после периода
        rows = self.fx.run(date_from=10, date_to=20).open_applications()
        self.assertEqual([(r['app'].number, r['days_open']) for r in rows],
                         [('N-002', 25), ('N-001', 18)])

    def test_the_organisation_boundary_holds(self):
        self.fx.app(transport='T3', status='PENDING', created=10, completed=None)
        self.fx.app(transport='T1', status='PENDING', created=10, completed=None)
        self.fx.app(transport='T8', status='PENDING', created=10, completed=None)
        numbers = lambda **kw: sorted(r['app'].number for r in                  # noqa: E731
                                      self.fx.run(**kw).open_applications())
        self.assertEqual(numbers(), ['N-001', 'N-002', 'N-003'])
        self.assertEqual(numbers(org_ids={2}), ['N-001'])
        # Машина не сопоставлена -- только тому, кому видны все организации.
        self.assertEqual(numbers(org_ids={1, 2}), ['N-001', 'N-002'])


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



class ParseVolume(unittest.TestCase):
    def test_the_api_string_and_what_is_not_a_volume(self):
        self.assertEqual(rc.parse_volume('12.50'), 12.5)
        self.assertEqual(rc.parse_volume('7'), 7.0)
        self.assertEqual(rc.parse_volume('3,5'), 3.5)
        self.assertEqual(rc.parse_volume(4.0), 4.0)
        for value in (None, '', '  ', '0', '0.00', '-1', 'abc', 'nan', 'inf',
                      True):
            with self.subTest(value=value):
                self.assertIsNone(rc.parse_volume(value))


class VolumeAgainstGps(unittest.TestCase):
    """U2: объём заявки против гектаров GPS -- решения сессии по поручению
    владельца 08.10 (раздел 9.2 трека agro-work).

    Машина 12 (T2, объект 1002): 10-12 сутки без участков, 13-го -- работа
    3,5 + 0,5 = 4,0 га. Машина 11 (T1, 1001): 10-го -- 2,0 га, 14-го точек
    нет, 15-е не посчитано.
    """

    def setUp(self):
        self.fx = Fixture()

    def tearDown(self):
        self.fx.close()

    def row(self, app_id, **kwargs):
        return self.fx.forward(app_id, **kwargs)

    def volume(self, row):
        return (row['volume_verdict'], row['volume_reason'],
                row['volume_deviation'])

    def test_the_tolerance_of_orders_decides_both_ways(self):
        # Объём заявки против 4,0 га по GPS. Допуск -- В-2: 10 % или 0,3 га
        # (что больше), 20 % или 0,5 га.
        cases = (('4.00', rc.VOL_OK, 0.0), ('4.30', rc.VOL_OK, -0.3),
                 ('4.50', rc.VOL_WARN, -0.5), ('5.00', rc.VOL_WARN, -1.0),
                 ('5.10', rc.VOL_FAIL, -1.1), ('3.70', rc.VOL_OK, 0.3),
                 ('3.50', rc.VOL_WARN, 0.5), ('3.00', rc.VOL_FAIL, 1.0))
        for volume, verdict, deviation in cases:
            with self.subTest(volume=volume):
                fx = Fixture()
                self.addCleanup(fx.close)
                app_id = fx.app(transport='T2', created=12, completed=13,
                                volume=volume)
                row = fx.forward(app_id)
                self.assertEqual(row['verdict'], rc.V_WORK)
                self.assertEqual(row['gps_ha'], 4.0)
                self.assertEqual(self.volume(row), (verdict, None, deviation))
                self.assertEqual(row['volume'], float(volume))
                self.assertAlmostEqual(row['volume_share'],
                                       deviation / float(volume))

    def test_only_work_was_done_is_compared(self):
        no_work = self.fx.app(transport='T2', created=10, completed=11,
                              volume='5.00')
        opened = self.fx.app(transport='T1', status='IN_PROGRESS', created=16,
                             completed=None, volume='5.00')
        for app_id, verdict in ((no_work, rc.V_NO_WORK), (opened, rc.V_NONE)):
            row = self.row(app_id)
            with self.subTest(verdict=verdict):
                self.assertEqual(row['verdict'], verdict)
                self.assertEqual(self.volume(row), (None, None, None))
                self.assertIsNone(rc.volume_key(row))
                self.assertEqual(row['volume'], 5.0)

    def test_a_volume_not_in_hectares_or_absent_is_not_compared(self):
        hours = self.fx.app(transport='T2', created=12, completed=13,
                            volume='8.00', unit='HOUR')
        self.assertEqual(self.volume(self.row(hours)),
                         (None, rc.VR_NOT_HECTARE, None))
        for volume in (None, '', '0.00', 'abc'):
            with self.subTest(volume=volume):
                fx = Fixture()
                self.addCleanup(fx.close)
                app_id = fx.app(transport='T2', created=12, completed=13,
                                volume=volume)
                row = fx.forward(app_id)
                self.assertEqual(row['verdict'], rc.V_WORK)
                self.assertEqual(self.volume(row), (None, rc.VR_NO_VOLUME, None))
                self.assertEqual(rc.volume_key(row), rc.VOL_NONE)

    def test_an_unknown_day_in_the_window_leaves_the_hectares_incomplete(self):
        # 10..14 у машины 11: работа 10-го, 14-го точек нет.
        app_id = self.fx.app(transport='T1', created=10, completed=14,
                             volume='2.00')
        row = self.row(app_id)
        self.assertEqual((row['verdict'], row['unknown_days']), (rc.V_WORK, 1))
        self.assertEqual(self.volume(row), (None, rc.VR_DAYS_UNKNOWN, None))
        # Отрицательный контроль -- в своей базе, чтобы первая заявка не
        # пересеклась с ним: окно 10..13 без неизвестных суток сверяется.
        fx = Fixture()
        self.addCleanup(fx.close)
        known = fx.app(transport='T1', created=10, completed=13, volume='2.00')
        self.assertEqual(self.volume(fx.forward(known)), (rc.VOL_OK, None, 0.0))

    def test_an_open_application_that_could_cover_the_window_blocks_it(self):
        mine = self.fx.app(transport='T2', created=12, completed=13,
                           volume='4.00')
        other = self.fx.app(transport='T2', status='IN_PROGRESS', created=11,
                            completed=None, volume='9.00')
        row = self.row(mine)
        self.assertEqual(self.volume(row), (None, rc.VR_OPEN, None))
        self.assertEqual([a.id for a in row['volume_neighbours']], [other])
        self.assertIsNone(row['volume_group'])

    def test_an_open_application_entered_after_the_window_does_not(self):
        mine = self.fx.app(transport='T2', created=12, completed=13,
                           volume='4.00')
        self.fx.app(transport='T2', status='PENDING', created=14,
                    completed=None)
        row = self.row(mine)
        self.assertEqual(self.volume(row), (rc.VOL_OK, None, 0.0))
        self.assertEqual(row['volume_neighbours'], [])

    def test_an_application_with_an_unknown_window_blocks_it(self):
        mine = self.fx.app(transport='T2', created=12, completed=13,
                           volume='4.00')
        # Без истории: могла покрыть сутки не раньше 15 - N = 13-го.
        other = self.fx.app(transport='T2', created=15, completed=None,
                            history=False)
        row = self.row(mine)
        self.assertEqual(self.volume(row), (None, rc.VR_WINDOW_UNKNOWN, None))
        self.assertEqual([a.id for a in row['volume_neighbours']], [other])
        # Отрицательный контроль: созданная 16-го (16 - 2 = 14) окно 12..13
        # не задевает.
        fx = Fixture()
        self.addCleanup(fx.close)
        mine = fx.app(transport='T2', created=12, completed=13, volume='4.00')
        fx.app(transport='T2', created=16, completed=None, history=False)
        self.assertEqual(self.volume(fx.forward(mine)), (rc.VOL_OK, None, 0.0))

    def test_cancelled_and_deleted_applications_do_not_block(self):
        mine = self.fx.app(transport='T2', created=12, completed=13,
                           volume='4.00')
        self.fx.app(transport='T2', status='CANCELLED', created=12,
                    completed=None, cancelled='2026-09-14T08:00:00+05:00')
        self.fx.app(transport='T2', created=12, completed=13, volume='4.00',
                    gone_at='2026-09-20 03:00:00')
        self.assertEqual(self.volume(self.row(mine)), (rc.VOL_OK, None, 0.0))

    def test_another_machine_does_not_block(self):
        mine = self.fx.app(transport='T2', created=12, completed=13,
                           volume='4.00')
        self.fx.app(transport='T1', created=12, completed=13, volume='4.00')
        self.fx.app(transport='T1', status='IN_PROGRESS', created=10,
                    completed=None)
        self.assertEqual(self.volume(self.row(mine)), (rc.VOL_OK, None, 0.0))

    def test_a_second_agro_work_vehicle_of_the_same_machine_blocks_it(self):
        self.fx.con.execute("INSERT INTO agro_work_transports (id, plate_number, "
                            "plate_norm, equipment_id, match_status, "
                            "first_seen_at, last_seen_at) VALUES ('T9', 'P-T9', "
                            "'PT9', 12, 'manual', 't', 't')")
        self.fx.con.commit()
        mine = self.fx.app(transport='T2', created=12, completed=13,
                           volume='4.00')
        other = self.fx.app(transport='T9', created=13, completed=13,
                            volume='1.00')
        row = self.row(mine)
        self.assertEqual(self.volume(row), (None, rc.VR_OVERLAP, None))
        self.assertEqual([a.id for a in row['volume_neighbours']], [other])

    def test_overlapping_closed_windows_show_their_sum_without_a_colour(self):
        first = self.fx.app(transport='T2', created=12, completed=13,
                            volume='3.00')
        second = self.fx.app(transport='T2', created=13, completed=13,
                             volume='1.50')
        for mine, other in ((first, second), (second, first)):
            row = self.row(mine)
            with self.subTest(app=mine):
                self.assertEqual(self.volume(row), (None, rc.VR_OVERLAP, None))
                self.assertEqual([a.id for a in row['volume_neighbours']], [other])
                group = row['volume_group']
                self.assertEqual([a.id for a in group['apps']], [first, second])
                self.assertEqual((group['first'], group['last']), (D(12), D(13)))
                self.assertEqual((group['volume'], group['gps_ha']), (4.5, 4.0))

    def test_the_chain_reaches_past_the_direct_neighbour(self):
        # 10-11, 11-12, 12-13: первая и третья не пересекаются, но цепочка
        # одна; общее окно 10..13, по GPS 4,0 га (13-го).
        first = self.fx.app(transport='T2', created=10, completed=11,
                            volume='1.00')
        self.fx.app(transport='T2', created=11, completed=12, volume='1.00')
        third = self.fx.app(transport='T2', created=12, completed=13,
                            volume='2.00')
        row = self.row(third)
        self.assertEqual(self.volume(row), (None, rc.VR_OVERLAP, None))
        self.assertEqual(len(row['volume_neighbours']), 1)
        group = row['volume_group']
        self.assertEqual(group['apps'][0].id, first)
        self.assertEqual((group['first'], group['last'], group['volume'],
                          group['gps_ha']), (D(10), D(13), 4.0, 4.0))

    def test_no_sum_when_the_chain_is_not_comparable(self):
        cases = {
            'a member in hours': dict(unit='HOUR', volume='1.00'),
            'a member without volume': dict(volume=None),
            'a member of another method': dict(work_type=W_TIME, volume='1.00'),
        }
        for name, change in cases.items():
            with self.subTest(case=name):
                fx = Fixture()
                self.addCleanup(fx.close)
                mine = fx.app(transport='T2', created=12, completed=13,
                              volume='3.00')
                fx.app(transport='T2', created=13, completed=13, **change)
                row = fx.forward(mine)
                self.assertEqual(self.volume(row), (None, rc.VR_OVERLAP, None))
                self.assertIsNone(row['volume_group'])

    def test_no_sum_when_the_common_window_has_an_unknown_day(self):
        # Машина 11: своё окно 10..11 известно (работа 10-го), соседнее
        # 11..14 задевает 14-е, где точек нет.
        mine = self.fx.app(transport='T1', created=10, completed=11,
                           volume='2.00')
        self.fx.app(transport='T1', created=11, completed=14, volume='1.00')
        row = self.row(mine)
        self.assertEqual(self.volume(row), (None, rc.VR_OVERLAP, None))
        self.assertIsNone(row['volume_group'])

    def test_no_sum_when_an_open_application_could_cover_the_common_window(self):
        # Машина 11: своё окно 10..10 (работа 2,0 га) открытая, созданная
        # 12-го, не задевает; цепочка через 10..12 доходит до 12-го.
        mine = self.fx.app(transport='T1', created=10, completed=10,
                           volume='2.00')
        self.fx.app(transport='T1', created=10, completed=12, volume='1.00')
        self.fx.app(transport='T1', status='IN_PROGRESS', created=12,
                    completed=None)
        row = self.row(mine)
        self.assertEqual(row['verdict'], rc.V_WORK)
        self.assertEqual(self.volume(row), (None, rc.VR_OVERLAP, None))
        self.assertIsNone(row['volume_group'])
        # Отрицательный контроль: без открытой числа цепочки есть.
        fx = Fixture()
        self.addCleanup(fx.close)
        mine = fx.app(transport='T1', created=10, completed=10, volume='2.00')
        fx.app(transport='T1', created=10, completed=12, volume='1.00')
        group = fx.forward(mine)['volume_group']
        self.assertEqual((group['first'], group['last'], group['volume'],
                          group['gps_ha']), (D(10), D(12), 3.0, 2.0))

    def test_the_worst_neighbour_is_named(self):
        mine = self.fx.app(transport='T2', created=12, completed=13,
                           volume='4.00')
        self.fx.app(transport='T2', created=13, completed=13, volume='1.00')
        self.fx.app(transport='T2', created=15, completed=None, history=False)
        self.assertEqual(self.volume(self.row(mine)),
                         (None, rc.VR_WINDOW_UNKNOWN, None))
        self.fx.app(transport='T2', status='IN_PROGRESS', created=12,
                    completed=None)
        row = self.row(mine)
        self.assertEqual(self.volume(row), (None, rc.VR_OPEN, None))
        self.assertEqual(len(row['volume_neighbours']), 3)

    def test_the_verdicts_and_coverage_stay_as_they_were(self):
        mine = self.fx.app(transport='T2', created=12, completed=13,
                           volume='9.00')
        self.fx.app(transport='T2', created=13, completed=13, volume='1.00')
        ctx = self.fx.run()
        rows = {r['app'].id: r for r in ctx.forward_rows()}
        self.assertEqual(rows[mine]['verdict'], rc.V_WORK)
        self.assertEqual(rows[mine]['gps_ha'], 4.0)
        day = {(r['equipment_id'], r['day']): r for r in ctx.reverse_rows()}
        self.assertEqual(day[(12, D(13))]['coverage'], rc.C_COVERED)

    def test_the_summary_adds_up_to_work_was_done(self):
        self.fx.app(transport='T2', created=12, completed=13, volume='4.00')
        self.fx.app(transport='T2', created=13, completed=13, volume='1.00',
                    unit='HOUR')
        self.fx.app(transport='T1', created=10, completed=14, volume='2.00')
        self.fx.app(transport='T1', created=10, completed=10, volume='5.00')
        self.fx.app(transport='T2', created=10, completed=11, volume='5.00')
        groups, total = self.fx.run().summary()
        for counter in list(groups.values()) + [total]:
            self.assertEqual(counter['app_' + rc.V_WORK],
                             sum(counter[rc.VOL + key]
                                 for key in rc.VOLUME_VERDICTS))
            self.assertEqual(counter[rc.VOL + rc.VOL_NONE],
                             sum(counter[rc.VOL_REASON + reason]
                                 for reason in rc.VOLUME_REASONS))
        # T2: 12..13 пересекается с 13..13, у той объём в часах -- она «не в
        # гектарах»; T1: у 10..14 неизвестный день 14-го, 10..10 с ней
        # пересекается; 10..11 у T2 -- работы нет и в счёт объёма не идёт.
        self.assertEqual(total['app_' + rc.V_WORK], 4)
        self.assertEqual(total[rc.VOL + rc.VOL_NONE], 4)
        self.assertEqual(total[rc.VOL_REASON + rc.VR_OVERLAP], 2)
        self.assertEqual(total[rc.VOL_REASON + rc.VR_NOT_HECTARE], 1)
        self.assertEqual(total[rc.VOL_REASON + rc.VR_DAYS_UNKNOWN], 1)

    def test_the_colours_reach_the_summary(self):
        self.fx.app(transport='T2', created=12, completed=13, volume='5.10')
        self.fx.app(transport='T1', created=10, completed=10, volume='2.00')
        groups, total = self.fx.run().summary()
        self.assertEqual((total[rc.VOL + rc.VOL_OK], total[rc.VOL + rc.VOL_WARN],
                          total[rc.VOL + rc.VOL_FAIL],
                          total[rc.VOL + rc.VOL_NONE]), (1, 0, 1, 0))
        self.assertEqual(groups[(1, 'mtz')][rc.VOL + rc.VOL_FAIL], 1)


class MayCoverIsTheCoverageRule(unittest.TestCase):
    """«Могла покрыть» объёма и покрытие суток -- один критерий.

    Для каждых суток первая непустая группа по may_cover (окно, открытая,
    неизвестное окно, задним числом) совпадает с тем, что вернул coverage, --
    при утверждённом N и при неутверждённом.
    """

    def test_day_by_day_against_coverage(self):
        for lookback in (2, None):
            fx = Fixture()
            self.addCleanup(fx.close)
            fx.app(transport='T2', created=12, completed=13)
            fx.app(transport='T2', created=15, completed=16)
            fx.app(transport='T2', status='IN_PROGRESS', created=18,
                   completed=None)
            fx.app(transport='T2', created=21, completed=None, history=False)
            fx.app(transport='T2', created=24, completed=24,
                   initial='COMPLETED')
            fx.app(transport='T2', status='CANCELLED', created=9,
                   completed=None, cancelled='2026-09-26T08:00:00+05:00')
            fx.app(transport='T2', created=9, completed=27,
                   gone_at='2026-09-27 03:00:00')
            ctx = fx.run(date_from=8, date_to=27, lookback=lookback)
            live = ctx.live_apps_of(12)
            self.assertEqual(len(live), 5)
            for number in range(8, 28):
                day = D(number)
                with self.subTest(lookback=lookback, day=day):
                    groups = [
                        (rc.C_COVERED, None,
                         [a for a in live if a.window and ctx.may_cover(a, day)]),
                        (rc.C_NONE, rc.R_OPEN_COVERS,
                         [a for a in live if a.is_open and ctx.may_cover(a, day)]),
                        (rc.C_NONE, rc.R_WINDOW_UNKNOWN,
                         [a for a in live if a.window is None
                          and a.window_reason in rc.UNKNOWN_WINDOW_REASONS
                          and ctx.may_cover(a, day)]),
                        (rc.C_NONE, rc.R_MAYBE_BACKDATED,
                         [a for a in live if a.window_reason == rc.R_BACKDATED
                          and ctx.may_cover(a, day)])]
                    expected = next(((cov, reason, [a.id for a in apps])
                                     for cov, reason, apps in groups if apps),
                                    (rc.C_UNCOVERED, None, []))
                    coverage, reason, apps = ctx.coverage(12, day)
                    self.assertEqual((coverage, reason, [a.id for a in apps]),
                                     expected)
                    # Ни одна живая заявка не «могла покрыть» сутки, которые
                    # покрытие назвало ничьими.
                    if coverage == rc.C_UNCOVERED:
                        self.assertFalse([a for a in live
                                          if ctx.may_cover(a, day)])


if __name__ == '__main__':
    unittest.main()
