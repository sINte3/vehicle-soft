# -*- coding: utf-8 -*-
"""DRONE-CASH-SPLIT-001: канал оплаты отделён, подотчёт оператора считается.

Две задачи, обе — решения владельца от 2026-08-11. Первая: остаток по
справке показывается отдельно, потому что книги диспетчеров поступлений по
перечислению не содержат вовсе, и весь счёт такой работы выглядел долгом.
Вторая: подотчёт оператора — свой экран, потому что это другая
задолженность: заказчик должен холдингу, оператор должен кассе, и харажат
уменьшает вторую, но не первую.

Фикстура построена так, чтобы наивная реализация провалилась: у неё есть
работа по справке без поступления, наличная работа, рассчитанная ровно на
харажат, строка «Оператор топшириши керак», которую нельзя считать сданной,
и работа без оператора, которую нельзя выбросить.

Run:
  python -m unittest tests.test_drone_cash_split_001 -v
"""
import unittest

from tests.harness import app, create_admin, create_org, login, reset_db
from models import (db, DroneOperator, DroneWork, User,
                    DRONE_RECEIVED_KIND_OPERATOR_DUE,
                    DRONE_RECEIVED_KIND_RECEIVED)

import drones

KOGON = 'Когон ПТЗ'


class Fixture(object):
    pass


def build_fixture():
    reset_db()
    f = Fixture()
    f.org = create_org('Бухоро Агрокластер')
    f.admin = create_admin('cash_admin')
    with app.app_context():
        db.session.get(User, f.admin).language = 'ru'
        ops = [DroneOperator(full_name='Оператор Первый',
                             subdivision_name=KOGON),
               DroneOperator(full_name='Оператор Второй',
                             subdivision_name=KOGON)]
        db.session.add_all(ops)
        db.session.commit()
        f.op1, f.op2 = ops[0].id, ops[1].id

        seq = [0]

        def work(payment, amount, received, other=None, operator=None,
                 kind=DRONE_RECEIVED_KIND_RECEIVED, area=10.0):
            seq[0] += 1
            db.session.add(DroneWork(
                period_month='2026-04', drone_operator_id=operator,
                customer_raw='ФХ %d' % seq[0], area_ha=area, amount=amount,
                received_amount=received, other_costs=other,
                received_kind=kind if received is not None else None,
                payment_type=payment, subdivision_name=KOGON,
                source_file='книга.xlsx', source_sheet='свод ичи',
                source_row=seq[0]))

        # Справка: поступление в книге не записано -- весь счёт выглядел долгом.
        work('transfer', 5000000, None, operator=f.op1)
        # Наличные, рассчитан полностью: получено = сумма - харажат.
        # «Долг» по старой формуле равен ровно харажату.
        work('cash', 2000000, 1700000, other=300000, operator=f.op1)
        # Наличные, оператор ещё не сдал и сам это написал.
        work('cash', 1000000, 400000, operator=f.op1,
             kind=DRONE_RECEIVED_KIND_OPERATOR_DUE)
        # Наличные второго оператора: сдал всё, затрат нет.
        work('cash', 800000, 800000, operator=f.op2)
        # Наличные без оператора: корзина, её нельзя выбрасывать.
        work('cash', 600000, 100000, other=50000, operator=None)
        # Внутренняя работа: в подотчёт не входит.
        work('internal', 900000, 0, operator=f.op1)
        db.session.commit()
    return f


def data_of(fn, lang='ru'):
    # Контекст запроса настоящий по двум причинам:
    # _drone_works_filters_from_args читает request.args, у которого есть
    # type=int (у обычного dict его нет), а _drone_t берёт язык из g.lang.
    # Без явного языка подписи вернулись бы узбекскими, и тест сравнивал бы
    # русские строки с узбекскими.
    from flask import g, request
    with app.app_context():
        with app.test_request_context('/drones/works/debts'):
            g.lang = lang
            filters = drones._drone_works_filters_from_args(request.args)
            conds = drones._drone_work_conditions(filters)
            return fn(conds)


class ChannelSplitTests(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        cls.f = build_fixture()
        cls.data = data_of(drones._drone_debt_channels)

    def by_payment(self):
        return {r['payment']: r for r in self.data['rows']}

    def test_the_transfer_channel_shows_its_whole_bill_as_outstanding(self):
        row = self.by_payment()['transfer']
        self.assertAlmostEqual(row['outstanding'], 5000000.0, places=2)
        self.assertAlmostEqual(row['received'], 0.0, places=2)

    def test_the_transfer_row_says_it_is_not_a_debt(self):
        """Число без этих слов и есть недоразумение, ради которого правка."""
        note = self.by_payment()['transfer']['note']
        self.assertTrue(note)
        self.assertIn('не долг', note)
        self.assertIn('реестр', note)

    def test_the_cash_row_points_at_the_operator_screen(self):
        note = self.by_payment()['cash']['note']
        self.assertIn('затраты операторов', note)

    def test_every_channel_present_carries_a_note(self):
        """Отрицательный полюс: подпись не только у справки."""
        for row in self.data['rows']:
            self.assertTrue(row['note'], row['payment'])

    def test_the_split_sums_to_the_same_total_as_the_debts_page(self):
        """Разложение, а не второй расчёт: итог обязан совпасть до копейки."""
        report = data_of(drones._drone_works_report_data)
        self.assertAlmostEqual(self.data['total']['outstanding'],
                               report['totals']['outstanding'], places=2)
        self.assertAlmostEqual(self.data['total']['amount'],
                               report['totals']['amount'], places=2)
        self.assertEqual(self.data['total']['jobs'], report['totals']['jobs'])

    def test_cash_comes_first_and_transfer_second(self):
        order = [r['payment'] for r in self.data['rows']]
        self.assertEqual(order[:2], ['cash', 'transfer'])

    def test_the_notes_are_bilingual_and_uzbek_is_cyrillic(self):
        """Отрицательный полюс: подпись не захардкожена по-русски."""
        uz = data_of(drones._drone_debt_channels, lang='uz')
        ru_note = self.by_payment()['transfer']['note']
        uz_note = {r['payment']: r for r in uz['rows']}['transfer']['note']
        self.assertNotEqual(ru_note, uz_note)
        self.assertTrue(uz_note)
        latin = [c for c in uz_note if 'a' <= c.lower() <= 'z']
        self.assertEqual(latin, [], 'узбекский только кириллицей: %r' % latin)


class OperatorCashTests(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        cls.f = build_fixture()
        cls.data = data_of(drones._drone_operator_cash_data)

    def by_operator(self):
        return {r['operator_id']: r for r in self.data['rows']}

    def test_only_cash_works_are_counted(self):
        """Отрицательный полюс: справка и внутренняя работа не попадают.

        Оператор Первый имеет справку на 5 000 000 и внутреннюю на 900 000;
        в подотчёт должны войти только его наличные 2 000 000 + 1 000 000.
        """
        row = self.by_operator()[self.f.op1]
        self.assertAlmostEqual(row['collected'], 3000000.0, places=2)
        self.assertEqual(row['jobs'], 2)

    def test_the_balance_is_collected_minus_costs_minus_handed(self):
        row = self.by_operator()[self.f.op1]
        self.assertAlmostEqual(row['costs'], 300000.0, places=2)
        self.assertAlmostEqual(row['handed'], 1700000.0, places=2)
        self.assertAlmostEqual(row['balance'], 1000000.0, places=2)

    def test_operator_due_is_not_counted_as_handed_in(self):
        """Заявление «я ещё должен» — это не приход.

        У Первого одна наличная работа на 1 000 000 несёт 400 000 с признаком
        «Оператор топшириши керак». Если бы их сложили со сданным, остаток
        уменьшился бы на 400 000 и долг оператора превратился бы в приход.
        """
        row = self.by_operator()[self.f.op1]
        self.assertAlmostEqual(row['declared'], 400000.0, places=2)
        self.assertNotAlmostEqual(row['handed'], 2100000.0, places=2)

    def test_a_settled_operator_has_a_zero_balance(self):
        row = self.by_operator()[self.f.op2]
        self.assertAlmostEqual(row['balance'], 0.0, places=2)

    def test_works_without_an_operator_get_their_own_row_and_sort_last(self):
        rows = self.data['rows']
        self.assertTrue(rows[-1]['is_unresolved'])
        self.assertAlmostEqual(rows[-1]['balance'], 450000.0, places=2)
        self.assertIn('не определён', rows[-1]['label'])

    def test_the_total_is_the_sum_of_the_rows(self):
        for key in ('collected', 'costs', 'handed', 'declared', 'balance'):
            self.assertAlmostEqual(
                self.data['total'][key],
                sum(r[key] for r in self.data['rows']), places=2, msg=key)


class ScreensRenderTests(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        cls.f = build_fixture()

    def client(self):
        client = app.test_client()
        login(client, self.f.admin)
        return client

    def test_the_operator_cash_screen_opens_with_its_columns(self):
        page = self.client().get('/drones/reports/operator-cash')
        self.assertEqual(page.status_code, 200)
        html = page.get_data(as_text=True)
        for probe in ('Подотчёт операторов', 'Собрано', 'Затраты', 'Сдано',
                      'Остаток', 'Заявлено',
                      'остаток = собрано − затраты − сдано'):
            self.assertIn(probe, html, probe)

    def test_the_debts_screen_carries_the_channel_table(self):
        page = self.client().get('/drones/works/debts')
        self.assertEqual(page.status_code, 200)
        html = page.get_data(as_text=True)
        self.assertIn('Остаток по каналам оплаты', html)
        self.assertIn('ждём банковский реестр', html)

    def test_the_hub_carries_the_new_tile(self):
        html = self.client().get('/drones/reports').get_data(as_text=True)
        self.assertIn('Подотчёт операторов', html)
        self.assertIn('/drones/reports/operator-cash', html)

    def test_the_filter_form_is_really_there(self):
        """Отрицательный полюс: форма набрана, а не подключена пустотой."""
        html = self.client().get(
            '/drones/reports/operator-cash').get_data(as_text=True)
        self.assertIn('name="period"', html)
        self.assertIn('name="operator_id"', html)
        self.assertIn('name="subdivision"', html)

    def test_the_period_filter_actually_filters(self):
        html = self.client().get(
            '/drones/reports/operator-cash?period=2020-01').get_data(
                as_text=True)
        self.assertIn('Наличных работ за выбранный период нет.', html)


if __name__ == '__main__':
    unittest.main()
