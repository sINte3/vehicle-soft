# -*- coding: utf-8 -*-
"""DRONE-SUMMARY-LITERS-UNKNOWN-001: «литров не записано» больше не ноль.

`coalesce(sum(spray_liters), 0.0)` схлопывал два РАЗНЫХ факта в одно число:

  * ни у одного вылета литры не записаны -- «данных нет»;
  * литры записаны и равны нулю -- «дрон летал сухим».

Сводка печатала для обоих `0`, и «Л/га» вместе с ней. На выгрузке машины №8
за июнь-2026 это дало «Литров раствора: 0» и «Л/га: 0» при 588 вылетах,
которые DJI пометил как опрыскивание, -- и по этому нулю нельзя было понять,
сломан ли расходомер или работа шла без раствора. Отчёт `/drones/reports/spray`
такие случаи различал давно (счётчик `no_liters`); сводка -- нет.

Тот же класс дефекта, что «признак успеха, определённый пробой, неразличающей
два поля», и прямое продолжение DRONE-ZERO-VS-UNKNOWN-001.

К каждой проверке -- ОТРИЦАТЕЛЬНЫЙ КОНТРОЛЬ с настоящим нулём: он обязан
остаться нулём, иначе «правка» просто прячет колонку.

Run:
  python -m unittest tests.test_drone_summary_liters_unknown_001 -v
"""
import datetime
import unittest

from tests.harness import app, reset_db
from models import db, DroneFlight, DroneUnit, Organization

import drones


def _unit(number):
    org = Organization.query.first()
    if org is None:
        org = Organization(name='Тест')
        db.session.add(org)
        db.session.flush()
    unit = DroneUnit(number=number, organization_id=org.id, is_active=True)
    db.session.add(unit)
    db.session.flush()
    return unit


def _flight(unit_id, day, area, liters, dji_id):
    return DroneFlight(
        dji_flight_id=dji_id,
        drone_unit_id=unit_id,
        nickname_raw='fixture',
        # 03:00 по Ташкенту -- 22:00 UTC предыдущего дня; месяц считается по
        # местной дате, и полдень никогда не задел бы смещение.
        started_at=datetime.datetime(2026, 6, day, 3, 0)
                   - datetime.timedelta(minutes=300),
        work_seconds=600,
        area_ha=area,
        spray_liters=liters,
        sow_kg=None,
        usage_type=0,
        raw_json='{}',
    )


class SummaryLitersUnknownTest(unittest.TestCase):

    def setUp(self):
        self.ctx = app.app_context()
        self.ctx.push()
        reset_db()

    def tearDown(self):
        db.session.remove()
        self.ctx.pop()

    def _summary(self, rows):
        unit = _unit(8)
        for idx, (area, liters) in enumerate(rows):
            db.session.add(_flight(unit.id, 5 + idx, area, liters,
                                   700000 + idx))
        db.session.commit()
        conds = drones._drone_flight_conditions({
            'date_from': datetime.date(2026, 6, 1),
            'date_to': datetime.date(2026, 6, 30),
            'unit_id': None, 'region': ''})
        return drones._drone_summary_data(conds)

    # ГЛАВНОЕ: ни у одного вылета литров нет -- это ПРОЧЕРК, не ноль.
    def test_a_month_with_no_litres_recorded_reads_as_unknown(self):
        data = self._summary([(10.0, None), (20.0, None), (5.0, None)])
        self.assertIsNone(data['totals']['spray_liters'])
        self.assertEqual(0, data['totals']['spray_rows'])
        machine = data['by_machine']['rows'][0]
        self.assertIsNone(machine['spray_liters'])
        self.assertIsNone(machine['liters_per_ha'])
        self.assertIsNone(data['by_machine']['total']['spray_liters'])
        self.assertIsNone(data['by_machine']['total']['liters_per_ha'])
        self.assertIsNone(data['by_usage']['rows'][0]['spray_liters'])
        # Гектары при этом на месте: неизвестны литры, а не работа.
        self.assertAlmostEqual(35.0, data['totals']['area_ha'], 2)

    # ОТРИЦАТЕЛЬНЫЙ КОНТРОЛЬ: настоящий ноль обязан остаться нулём.
    def test_a_recorded_zero_stays_a_zero(self):
        """[REASON]: без этой проверки «правка» могла бы просто гасить

        колонку всегда, и отличить исправленный код от спрятанного было бы
        нечем.
        """
        data = self._summary([(10.0, 0.0), (20.0, 0.0)])
        self.assertIsNotNone(data['totals']['spray_liters'])
        self.assertAlmostEqual(0.0, data['totals']['spray_liters'], 6)
        self.assertEqual(2, data['totals']['spray_rows'])
        machine = data['by_machine']['rows'][0]
        self.assertAlmostEqual(0.0, machine['spray_liters'], 6)
        self.assertIsNotNone(machine['liters_per_ha'])
        self.assertAlmostEqual(0.0, machine['liters_per_ha'], 6)

    # ОТРИЦАТЕЛЬНЫЙ КОНТРОЛЬ: обычный месяц считается как считался.
    def test_ordinary_litres_are_unchanged(self):
        data = self._summary([(10.0, 275.0), (20.0, 550.0)])
        self.assertAlmostEqual(825.0, data['totals']['spray_liters'], 2)
        machine = data['by_machine']['rows'][0]
        self.assertAlmostEqual(27.5, machine['liters_per_ha'], 4)

    # ЧАСТИЧНЫЙ случай: часть вылетов с литрами -- это ИЗВЕСТНО, но неполно.
    def test_a_partially_recorded_month_is_not_unknown(self):
        """[REASON]: «известно про часть» -- не «неизвестно про всё».

        Прочерк здесь спрятал бы настоящие литры; число здесь честнее, а на
        неполноту указывает отдельный отчёт по расходу.
        """
        data = self._summary([(10.0, 275.0), (20.0, None)])
        self.assertAlmostEqual(275.0, data['totals']['spray_liters'], 2)
        self.assertEqual(1, data['totals']['spray_rows'])
        machine = data['by_machine']['rows'][0]
        self.assertIsNotNone(machine['liters_per_ha'])

    # Экран открывается и печатает прочерк, а не падает на None.
    def test_the_summary_page_renders_the_dash(self):
        self._summary([(10.0, None)])
        from tests.harness import create_admin, login
        admin_id = create_admin()
        client = app.test_client()
        login(client, admin_id)
        page = client.get('/drones/summary'
                          '?date_from=2026-06-01&date_to=2026-06-30')
        self.assertEqual(200, page.status_code)
        body = page.data.decode('utf-8')
        # Язык по умолчанию узбекский; подпись плитки -- «Литр эритма».
        self.assertIn('Литр эритма', body)
        # [REASON]: до правки шаблон падал на None (TypeError в '%.2f'), и
        # проверка «страница открывается» -- та самая, что это ловит.
        tile = body.split('Литр эритма', 1)[1][:300]
        self.assertIn('—', tile)
        self.assertNotIn('0.00', tile.split('vs-stat-value', 1)[1][:80])


if __name__ == '__main__':
    unittest.main()
