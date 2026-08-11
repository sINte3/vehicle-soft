# -*- coding: utf-8 -*-
"""MEGA-3, экран «Контроль данных»: факты «ноль там, где ноль невозможен».

Фикстура строится так, чтобы наивная реализация провалилась: рядом с каждой
аномалией посажен здоровый двойник, и тест проверяет не только «аномалия
найдена», но и «здоровый сосед НЕ назван». Отрицательный контроль в конце
доказывает, что секция «гектары без химии» различает два случая, а не пуста
или полна всегда.

Run:
  python -m unittest tests.test_drone_health_001 -v
"""
import datetime
import unittest

from tests.harness import app, create_admin, create_org, login, reset_db
from models import (db, DroneCustomer, DroneFlight, DroneOperator,
                    DroneOperatorAssignment, DroneUnit, DroneWork, User)

import drones

KOGON = 'Когон ПТЗ'


class Fixture(object):
    pass


def build_fixture():
    reset_db()
    f = Fixture()
    f.org = create_org('Бухоро Агрокластер')
    f.admin = create_admin('health_admin')
    with app.app_context():
        user = db.session.get(User, f.admin)
        user.language = 'ru'
        units = [DroneUnit(number=i, organization_id=f.org,
                           subdivision_name=KOGON) for i in (1, 2, 3)]
        op = DroneOperator(full_name='Оператор Молчаливый',
                           subdivision_name=KOGON)
        db.session.add_all(units + [op])
        db.session.commit()
        f.unit_ok, f.unit_dry, f.unit_silent = [u.id for u in units]
        # Назначение на машину №3 с 2026-03-01 без даты конца: покрывает и
        # прошлые месяцы вселенной, и текущий (который показан быть не должен).
        db.session.add(DroneOperatorAssignment(
            operator_id=op.id, drone_unit_id=f.unit_silent,
            date_from=datetime.date(2026, 3, 1), date_to=None))

        seq = [0]

        def flight(when, area, unit, liters=None, kg=None):
            seq[0] += 1
            db.session.add(DroneFlight(
                dji_flight_id=920000 + seq[0], drone_unit_id=unit,
                nickname_raw='fixture', started_at=when, area_ha=area,
                spray_liters=liters, sow_kg=kg, raw_json='{}'))

        # 2026-03: здоровый месяц (литры записаны) — он же месяц вселенной.
        flight(datetime.datetime(2026, 3, 10, 3), 10.0, f.unit_ok, liters=5.0)
        # 2026-04: у №1 химия записана, у №2 — гектары без литров и кг.
        flight(datetime.datetime(2026, 4, 5, 3), 5.0, f.unit_ok, liters=2.0)
        flight(datetime.datetime(2026, 4, 6, 3), 20.0, f.unit_dry)
        # Вылет без машины.
        flight(datetime.datetime(2026, 4, 7, 3), 7.0, None)

        customers = [DroneCustomer(name='ФХ Без записей'),
                     DroneCustomer(name='ФХ Записанный ноль'),
                     DroneCustomer(name='ФХ Здоровый')]
        db.session.add_all(customers)
        db.session.commit()
        f.cust_null, f.cust_zero, f.cust_ok = [c.id for c in customers]

        def work(customer_id, area, amount, price, n):
            db.session.add(DroneWork(
                period_month='2026-04', drone_customer_id=customer_id,
                customer_raw='строка %d' % n, area_ha=area, amount=amount,
                price_per_ha=price, payment_type='cash',
                source_file='книга здоровья.xlsx', source_sheet='л',
                source_row=n))

        # «Без записей»: две работы, суммы NULL. Цена одной тоже NULL —
        # она же попадает в «работы без цены».
        work(f.cust_null, 12.0, None, None, 1)
        work(f.cust_null, 18.0, None, 200000, 2)
        # «Записанный ноль»: сумма есть и равна нулю.
        work(f.cust_zero, 5.0, 0.0, 200000, 3)
        # Здоровый: гектары и живая сумма.
        work(f.cust_ok, 9.0, 1800000.0, 200000, 4)
        # Ещё одна работа без цены, у здорового заказчика.
        work(f.cust_ok, 3.0, 600000.0, None, 5)
        db.session.commit()
    return f


class DroneHealthDataTests(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        cls.f = build_fixture()
        with app.app_context():
            with app.test_request_context('/drones/reports/health'):
                cls.data = drones._drone_health_data()

    def test_dry_month_names_the_dry_machine_and_not_the_healthy_one(self):
        rows = self.data['dry_months']
        self.assertEqual([(r['unit_id'], r['month']) for r in rows],
                         [(self.f.unit_dry, '2026-04')])
        self.assertAlmostEqual(rows[0]['area'], 20.0, places=6)

    def test_zero_money_distinguishes_null_from_recorded_zero(self):
        by_id = {r['customer_id']: r for r in self.data['zero_money']}
        self.assertEqual(set(by_id), {self.f.cust_null, self.f.cust_zero})
        self.assertEqual(by_id[self.f.cust_null]['amounts_recorded'], 0)
        self.assertEqual(by_id[self.f.cust_zero]['amounts_recorded'], 1)

    def test_healthy_customer_is_not_accused(self):
        self.assertNotIn(self.f.cust_ok,
                         [r['customer_id'] for r in self.data['zero_money']])

    def test_silent_assignment_lists_full_months_only(self):
        rows = self.data['silent_assignments']
        self.assertEqual([(r['unit_id'], r['month']) for r in rows],
                         [(self.f.unit_silent, '2026-03'),
                          (self.f.unit_silent, '2026-04')])
        self.assertNotIn(self.data['current_month'],
                         [r['month'] for r in rows])

    def test_flying_machines_are_not_silent(self):
        units = {r['unit_id'] for r in self.data['silent_assignments']}
        self.assertNotIn(self.f.unit_ok, units)
        self.assertNotIn(self.f.unit_dry, units)

    def test_unattached_flights_are_counted_by_month(self):
        self.assertEqual(self.data['unattached'],
                         [{'month': '2026-04', 'flights': 1, 'area': 7.0}])

    def test_no_price_counts_exactly_the_priceless_works(self):
        self.assertEqual(self.data['no_price']['works'], 2)
        self.assertAlmostEqual(self.data['no_price']['area'], 15.0, places=6)

    def test_counters_agree_with_their_sections(self):
        c = self.data['counters']
        self.assertEqual(c['dry_months'], len(self.data['dry_months']))
        self.assertEqual(c['zero_money'], len(self.data['zero_money']))
        self.assertEqual(c['silent_assignments'],
                         len(self.data['silent_assignments']))
        self.assertEqual(c['unattached_flights'], 1)
        self.assertEqual(c['no_price_works'], 2)


class DroneHealthScreenTests(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        cls.f = build_fixture()

    def test_screen_renders_with_every_section_title(self):
        client = app.test_client()
        login(client, self.f.admin)
        page = client.get('/drones/reports/health')
        self.assertEqual(page.status_code, 200)
        html = page.get_data(as_text=True)
        for probe in ('Контроль данных', 'ни литра, ни килограмма',
                      'гектары есть, сумм нет', 'машина месяц не летала',
                      'Вылеты без машины', 'Работы без цены'):
            self.assertIn(probe, html, probe)

    def test_hub_carries_the_tile(self):
        client = app.test_client()
        login(client, self.f.admin)
        page = client.get('/drones/reports')
        self.assertIn('Контроль данных', page.get_data(as_text=True))


class DryMonthNegativeControlTests(unittest.TestCase):
    """Секция обязана различать «литры не записаны» и «литры записаны»."""

    def test_recording_liters_empties_the_section(self):
        f = build_fixture()
        with app.app_context():
            with app.test_request_context('/drones/reports/health'):
                before = drones._drone_health_data()
            self.assertEqual(len(before['dry_months']), 1)
            row = (DroneFlight.query
                   .filter_by(drone_unit_id=f.unit_dry).one())
            row.spray_liters = 27.5
            db.session.commit()
            with app.test_request_context('/drones/reports/health'):
                after = drones._drone_health_data()
            self.assertEqual(after['dry_months'], [])


if __name__ == '__main__':
    unittest.main(verbosity=2)
