# -*- coding: utf-8 -*-
"""GPS-5: сверка наряда с фактом по треку.

Проверяется то, что ошибётся молча и попадёт в счёт:

1. **Допуски В-2 в обе стороны от границы.** Проценты без абсолютной добавки
   залили бы очередь ложными «жёлтыми» на малых полях: ручной ввод округляется
   сеткой 0,5 га, и на поле 2 га одно округление даёт ±12%.

2. **Что входит в факт.** Участок, названный человеком проездом, вычитается.
   Участок без ответа входит — он прошёл подтверждённый порог 0,3 га. А
   предрегистрированное правило «≥ 1,3 га ИЛИ ≥ 25 мин» не участвует в
   арифметике ВООБЩЕ: оно ещё не принято, и считать по нему деньги значило бы
   ввести его в обход собственного протокола приёмки.

3. **Причина вместо числа.** «Сверить нельзя» и «расхождение ноль» — разные
   вещи. Каждая причина названа отдельно, и ни одна не притворяется нулём.

4. **Неоднозначная связь не угадывается.** Одна машина существует тремя
   объектами Wialon (замена трекеров). Сложить их — посчитать работу трижды,
   выбрать один — угадать. Сумма в счёте не угадывается.

Запуск:
  python -m unittest tests.test_gps_order_reconcile -v
"""
import unittest

from datetime import date, datetime

from tests.harness import app, db, reset_db, create_admin, create_org, login
from models import (Equipment, GpsDailyAggregate, GpsWorkPolygon, User,
                    VialonMapping, WorkOrder, WO_STATUS_DONE,
                    WO_STATUS_CANCELLED)

import gps_routes

DAY = date(2026, 7, 27)
SQUARE = '{"type": "Polygon", "coordinates": [[[64.55, 39.99], [64.56, 39.99],' \
         ' [64.56, 40.0], [64.55, 40.0], [64.55, 39.99]]]}'


class Site(object):
    """Ровно то, что читает fact_from_sites: площадь и ответ человека."""

    def __init__(self, area_ha, operator_label=None):
        self.area_ha = area_ha
        self.operator_label = operator_label


class Tolerances(unittest.TestCase):
    """Пункт 1. Границы взяты литералами, а не выражениями через константы:
    ожидание, записанное через саму константу, уезжает вместе с мутацией."""

    def verdict(self, base, fact):
        return gps_routes.verdict_for_ga(base, fact)[0]

    def test_ten_percent_is_still_green_and_a_hair_more_is_not(self):
        self.assertEqual(self.verdict(10.0, 9.0), 'ok')      # −10,0%
        self.assertEqual(self.verdict(10.0, 8.9), 'warn')    # −11,0%
        self.assertEqual(self.verdict(10.0, 8.0), 'warn')    # −20,0%
        self.assertEqual(self.verdict(10.0, 7.9), 'fail')    # −21,0%

    def test_the_absolute_allowance_saves_small_fields(self):
        # Поле 2 га, округление ручного ввода до 0,5 га: расхождение 0,3 га это
        # 15% и без абсолютной добавки было бы «жёлтым» на ровном месте.
        self.assertEqual(self.verdict(2.0, 1.7), 'ok')
        self.assertEqual(self.verdict(2.0, 1.6), 'warn')     # 0,4 га
        self.assertEqual(self.verdict(2.0, 1.5), 'warn')     # 0,5 га
        self.assertEqual(self.verdict(2.0, 1.4), 'fail')     # 0,6 га

    def test_the_allowance_works_in_the_plus_too(self):
        # Перевыполнение проверяется так же: приписка выглядит как плюс.
        self.assertEqual(self.verdict(10.0, 11.0), 'ok')
        self.assertEqual(self.verdict(10.0, 12.1), 'fail')

    def test_deviation_and_share_are_reported_signed(self):
        verdict, deviation, share = gps_routes.verdict_for_ga(10.0, 7.0)
        self.assertEqual(verdict, 'fail')
        self.assertAlmostEqual(deviation, -3.0)
        self.assertAlmostEqual(share, -0.3)


class WhatCountsAsFact(unittest.TestCase):
    """Пункт 2."""

    def test_a_site_called_a_transit_by_a_person_is_subtracted(self):
        fact, unanswered, unanswered_ha = gps_routes.fact_from_sites(
            [Site(5.0, 'работа'), Site(2.0, 'проезд')])
        self.assertEqual(fact, 5.0)
        self.assertEqual((unanswered, unanswered_ha), (0, 0.0))

    def test_a_site_without_an_answer_counts_but_is_named(self):
        fact, unanswered, unanswered_ha = gps_routes.fact_from_sites(
            [Site(5.0, 'работа'), Site(2.0)])
        self.assertEqual(fact, 7.0)
        self.assertEqual((unanswered, unanswered_ha), (1, 2.0))

    def test_the_pre_registered_rule_never_touches_the_arithmetic(self):
        # Участок 0,4 га и без ответа: правило «≥ 1,3 га ИЛИ ≥ 25 мин» назвало
        # бы его проездом. В факт он всё равно входит — правило не принято.
        fact, unanswered, _ = gps_routes.fact_from_sites([Site(0.4)])
        self.assertEqual(fact, 0.4)
        self.assertEqual(unanswered, 1)

    def test_a_day_of_nothing_but_transits_is_a_zero_fact_not_a_missing_one(self):
        fact, _, _ = gps_routes.fact_from_sites(
            [Site(2.0, 'проезд'), Site(1.0, 'проезд')])
        self.assertEqual(fact, 0.0)


class Reconcile(unittest.TestCase):
    def setUp(self):
        reset_db()
        self.admin_id = create_admin()
        self.org_id = create_org()
        with app.app_context():
            User.query.get(self.admin_id).language = 'ru'
            equipment = Equipment(name='МТЗ 261 EA', category='tractor',
                                  organization_id=self.org_id)
            db.session.add(equipment)
            db.session.flush()
            self.equipment_id = equipment.id
            db.session.commit()

    def _order(self, unit='ga', planned=8.0, actual=None, day=DAY,
               number='WO-1', equipment_id=None):
        with app.app_context():
            order = WorkOrder(
                number=number, organization_id=self.org_id,
                equipment_id=equipment_id or self.equipment_id,
                created_by=self.admin_id, status=WO_STATUS_DONE,
                planned_date=day, actual_date=day, unit=unit,
                planned_quantity=planned, actual_quantity=actual,
                work_type_text='культивация')
            db.session.add(order)
            db.session.commit()
            return order.id

    def _map(self, wialon_id=3464, name='МТЗ 261 EA', equipment_id=None):
        with app.app_context():
            db.session.add(VialonMapping(
                vialon_name=name, wialon_id=wialon_id,
                equipment_id=equipment_id or self.equipment_id, skip=False))
            db.session.commit()

    def _day(self, wialon_id=3464, reason=None, sites=()):
        with app.app_context():
            db.session.add(GpsDailyAggregate(
                work_date=DAY, wialon_id=wialon_id, points_total=1598,
                points_work=990, track_km=32.1, interval_median_s=30.0,
                sats_median=14.0, motion_gaps=0, lost_seconds=0.0, gps_jumps=0,
                reason=reason, method_version='adaptive-alpha-2026-08-12',
                computed_at=datetime(2026, 7, 28, 3, 0, 0)))
            for number, (area, label) in enumerate(sites, 1):
                db.session.add(GpsWorkPolygon(
                    work_date=DAY, wialon_id=wialon_id, site_number=number,
                    area_ha=area, minutes=120.0, polygon_geojson=SQUARE,
                    suggested_label='работа', operator_label=label))
            db.session.commit()

    def _rows(self):
        client = app.test_client()
        login(client, self.admin_id)
        resp = client.get('/gps/orders?from=2026-07-01&to=2026-07-31')
        self.assertEqual(resp.status_code, 200)
        return resp.get_data(as_text=True)

    def test_a_matching_order_comes_out_green(self):
        self._order(actual=8.5)
        self._map()
        self._day(sites=[(8.772, None)])
        html = self._rows()
        self.assertIn('vs-badge-success', html)
        self.assertIn('8.772', html)
        self.assertIn('без ответа: 1', html)

    def test_an_order_far_from_the_track_comes_out_red(self):
        self._order(actual=20.0)
        self._map()
        self._day(sites=[(8.772, None)])
        self.assertIn('vs-badge-danger', self._rows())

    def test_the_closed_quantity_wins_over_the_planned_one(self):
        # План 20 га, закрыто 8,5. Сверять надо с тем, за что выставят счёт.
        self._order(planned=20.0, actual=8.5)
        self._map()
        self._day(sites=[(8.772, None)])
        self.assertIn('vs-badge-success', self._rows())

    def test_a_unit_gps_cannot_measure_says_so(self):
        self._order(unit='soat', actual=8.0)
        self._map()
        self._day(sites=[(8.772, None)])
        html = self._rows()
        self.assertIn('Сверить нельзя', html)
        self.assertIn('Единица измерения не меряется по GPS', html)

    def test_a_machine_without_a_wialon_object_says_so(self):
        self._order(actual=8.0)
        self._day(sites=[(8.772, None)])
        self.assertIn('не сопоставлена с объектом Wialon', self._rows())

    def test_a_machine_with_several_wialon_objects_is_not_guessed(self):
        # Пункт 4: «New Holland 80 605 EA» существует тремя объектами.
        self._order(actual=8.0)
        self._map(wialon_id=10077, name='NH 80 605 EA (1)')
        self._map(wialon_id=10011, name='NH 80 605 EA (2)')
        self._day(wialon_id=10077, sites=[(8.772, None)])
        html = self._rows()
        self.assertIn('несколькими объектами Wialon', html)
        self.assertNotIn('8.772', html)

    def test_a_day_never_computed_is_not_a_zero(self):
        self._order(actual=8.0)
        self._map()
        html = self._rows()
        self.assertIn('За эти сутки расчёта нет', html)
        # [REASON]: проверяется значок вердикта, а не подпись «Вне допуска» --
        # она стоит счётчиком наверху всегда, даже когда счётчик равен нулю,
        # и поиск по подписи проходил бы при любом коде.
        self.assertNotIn('vs-badge-danger', html)

    def test_a_day_whose_area_was_refused_says_which_refusal(self):
        self._order(actual=8.0)
        self._map()
        self._day(reason='redkaya_zapis')
        html = self._rows()
        self.assertIn('Площадь за эти сутки не публиковалась', html)
        self.assertIn('Редкая запись', html)

    def test_an_order_without_a_quantity_has_nothing_to_compare(self):
        self._order(planned=None, actual=None)
        self._map()
        self._day(sites=[(8.772, None)])
        self.assertIn('нет объёма', self._rows())

    def test_a_cancelled_order_is_not_in_the_list(self):
        order_id = self._order(actual=8.0, number='WO-CANCELLED')
        with app.app_context():
            WorkOrder.query.get(order_id).status = WO_STATUS_CANCELLED
            db.session.commit()
        self.assertNotIn('WO-CANCELLED', self._rows())

    def test_the_filter_hides_what_is_inside_the_tolerance(self):
        self._order(actual=8.5, number='WO-GREEN')
        self._map()
        self._day(sites=[(8.772, None)])
        client = app.test_client()
        login(client, self.admin_id)
        html = client.get('/gps/orders?from=2026-07-01&to=2026-07-31&problems=1'
                          ).get_data(as_text=True)
        self.assertNotIn('WO-GREEN', html)
        # но счётчик наверху всё равно её считает: скрыт показ, не факт
        self.assertIn('Нарядов в окне', html)

    def test_an_operator_does_not_see_another_organizations_orders(self):
        # [REASON]: сверка не имеет права показать наряд организации, которую
        # человеку видеть не положено -- тот же охват, что и везде.
        from models import ROLE_OPERATOR, Organization, UserModulePermission
        self._order(actual=8.0, number='WO-OTHER-ORG')
        self._map()
        self._day(sites=[(8.772, None)])
        with app.app_context():
            other = Organization(name='Чужая организация')
            db.session.add(other)
            db.session.flush()
            operator = User(username='gps_reader', role=ROLE_OPERATOR,
                            language='ru')
            operator.set_password('x')
            operator.organizations.append(other)
            db.session.add(operator)
            db.session.flush()
            db.session.add(UserModulePermission(
                user_id=operator.id, module_code='wialon', has_access=True))
            db.session.commit()
            operator_id = operator.id
        client = app.test_client()
        login(client, operator_id)
        html = client.get('/gps/orders?from=2026-07-01&to=2026-07-31'
                          ).get_data(as_text=True)
        self.assertNotIn('WO-OTHER-ORG', html)


if __name__ == '__main__':
    unittest.main()
