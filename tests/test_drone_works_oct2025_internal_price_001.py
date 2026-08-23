# -*- coding: utf-8 -*-
"""DRONES_WORKS_OCT2025_INTERNAL_PRICE_001: цена внутреннего контура октября.

Правило владельца называет ИСТОЧНИК («как в сентябре для земель Агрокластера»),
а не число, поэтому главная проверка тут не «поставилось ли 85 633», а
«взялось ли оно ИЗ СЕНТЯБРЯ и отказывается ли миграция, когда сентябрь молчит
или отвечает двумя разными числами». Тариф внутреннего контура непостоянен --
75 040, 76 458, 85 633, 86 000 в разных листах, -- и на 473.40 га разница
между крайними почти 60 млн сум.

Отдельно проверяется, что НОЛЬ у строки «Топшириқ» -- это записанный ноль, а
не пустота: пустая клетка значит «цена неизвестна», ноль значит «цены нет по
решению», и отчёт эти случаи различает.

Run:
  python -m unittest tests.test_drone_works_oct2025_internal_price_001 -v
"""
import os
import sqlite3
import subprocess
import sys
import tempfile
import unittest

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SCRIPT = os.path.join(REPO_ROOT,
                      'migrate_drones_works_oct2025_internal_price_001.py')
sys.path.insert(0, REPO_ROOT)

import migrate_drones_works_oct2025_internal_price_001 as migration  # noqa

SCHEMA = """
CREATE TABLE drone_works (id INTEGER PRIMARY KEY AUTOINCREMENT,
  period_month TEXT NOT NULL, work_date_from TEXT, drone_customer_id INTEGER,
  customer_raw TEXT NOT NULL, area_ha NUMERIC, price_per_ha NUMERIC,
  amount NUMERIC, source_file TEXT, source_sheet TEXT, source_row INTEGER);
"""

ZAMINLARI_MCHJ = 'Бухоро Агрокластер Заминлари МЧЖ'
ZAMINLARI = 'Бухоро Агрокластер Заминлари'
PAXTA = 'Сервис пахта даласи'
TOPSHIRIQ = 'Сервис ерлари (Топшириқ Ғаниев ташрифи учун)'
SEPT_RATE = 85633.0


def build_db(path, sept=((1, SEPT_RATE, 511.5),), october_extra=(),
             priced=()):
    """sept -- (customer_id, ставка, га) сентябрьских строк заказчика 1."""
    conn = sqlite3.connect(path)
    conn.executescript(SCHEMA)
    row_id = 0

    def add(period, date, cid, cust, area, price, amount, sfile, sheet, srow):
        nonlocal row_id
        row_id += 1
        conn.execute('INSERT INTO drone_works (id, period_month, '
                     'work_date_from, drone_customer_id, customer_raw, '
                     'area_ha, price_per_ha, amount, source_file, '
                     'source_sheet, source_row) '
                     'VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)',
                     (row_id, period, date, cid, cust, area, price, amount,
                      sfile, sheet, srow))

    # Сентябрь: те же заказчики со ставками -- источник правила.
    for cid, rate, area in sept:
        add('2025-09', '2025-09-20', cid, ZAMINLARI_MCHJ, area, rate,
            None if rate is None else area * rate,
            'Ғиждувон сентябрь.xlsx', 'свод ичи (Шахзод)', 4)
    add('2025-09', '2025-09-21', 2, ZAMINLARI, 100.0, SEPT_RATE,
        100.0 * SEPT_RATE, 'Шофиркон сентябрь.xlsx', 'свод ичи (Туйғун)', 5)
    add('2025-09', '2025-09-22', 3, PAXTA, 10.0, SEPT_RATE, 10.0 * SEPT_RATE,
        'Сервис сентябрь.xlsx', 'свод ичи (Мухриддин)', 6)
    # [REASON]: ЧУЖОЙ заказчик сентября с ДРУГОЙ ставкой. Если миграция
    # начнёт брать «любую сентябрьскую ставку внутренних земель», она возьмёт
    # 200 000 и никто этого не заметит: сумма всё равно сойдётся с гектарами.
    add('2025-09', '2025-09-23', 9, 'Чужое фх', 50.0, 200000.0, 10000000.0,
        'Чужая сентябрь.xlsx', 'свод ичи (Чужой)', 7)

    # Октябрь: четыре строки без цены.
    for cid, cust, area, sfile, sheet, srow in (
            (1, ZAMINLARI_MCHJ, 296.00, migration.GIJDUVON,
             'свод ичи (Шахзод)', 11),
            (2, ZAMINLARI, 174.00, migration.SHOFIRKON,
             'свод ичи (Туйғун)', 15),
            (3, PAXTA, 3.40, migration.SERVIS, 'свод ичи (Мухриддин)', 14),
            (4, TOPSHIRIQ, 3.10, migration.SERVIS, 'свод ичи (Фурқат)', 17)):
        price = dict(priced).get(srow)
        add('2025-10', None, cid, cust, area, price,
            None if price is None else area * price, sfile, sheet, srow)
    # Соседняя октябрьская строка с ценой -- её трогать нельзя.
    add('2025-10', '2025-10-06', 5, 'Камфорт Агро фх', 26.0, 200000.0,
        5200000.0, migration.SERVIS, 'свод ичи (Беҳзод)', 12)
    for args in october_extra:
        add(*args)
    conn.commit()
    conn.close()


def run(db, *args):
    return subprocess.run([sys.executable, SCRIPT, '--db', db] + list(args),
                          capture_output=True, text=True)


def snapshot(db):
    conn = sqlite3.connect(db)
    try:
        return conn.execute('SELECT id, area_ha, price_per_ha, amount '
                            'FROM drone_works ORDER BY id').fetchall()
    finally:
        conn.close()


def priced_rows(db):
    conn = sqlite3.connect(db)
    try:
        return {(r[0], r[1]): (r[2], r[3]) for r in conn.execute(
            'SELECT source_sheet, source_row, price_per_ha, amount '
            "FROM drone_works WHERE period_month = '2025-10'")}
    finally:
        conn.close()


def registry(db):
    conn = sqlite3.connect(db)
    try:
        if not conn.execute("SELECT name FROM sqlite_master WHERE type='table'"
                            " AND name='schema_migrations'").fetchone():
            return []
        return [r[0] for r in conn.execute('SELECT name FROM '
                                           'schema_migrations')]
    finally:
        conn.close()


class InternalPriceTest(unittest.TestCase):

    def setUp(self):
        self.dir = tempfile.mkdtemp(prefix='octprice_')
        self.db = os.path.join(self.dir, 'transport.db')
        build_db(self.db)

    # 1. Сухой прогон ничего не пишет.
    def test_dry_run_writes_nothing(self):
        before = snapshot(self.db)
        result = run(self.db)
        self.assertEqual(0, result.returncode, result.stdout + result.stderr)
        self.assertIn('DRY RUN', result.stdout)
        self.assertEqual(before, snapshot(self.db))
        self.assertEqual([], registry(self.db))

    # 1. ГЛАВНОЕ: ставка ВЗЯТА ИЗ СЕНТЯБРЯ, а не вписана в код.
    def test_the_rate_is_read_from_september_not_hardcoded(self):
        """[REASON]: правило владельца называет источник, а не число.

        Отрицательный контроль -- вторая база, где сентябрь даёт ДРУГУЮ
        ставку: миграция обязана поставить именно её. Проверка на одной
        ставке была бы одинаково зелёной и для кода, который просто вписал
        85 633 константой.
        """
        run(self.db, '--apply')
        got = priced_rows(self.db)
        self.assertAlmostEqual(SEPT_RATE, got[('свод ичи (Шахзод)', 11)][0], 2)
        self.assertAlmostEqual(296.0 * SEPT_RATE,
                               got[('свод ичи (Шахзод)', 11)][1], 2)

        other = os.path.join(self.dir, 'other.db')
        build_db(other, sept=((1, 76458.0, 511.5),))
        run(other, '--apply')
        got2 = priced_rows(other)
        self.assertAlmostEqual(76458.0, got2[('свод ичи (Шахзод)', 11)][0], 2)
        self.assertAlmostEqual(296.0 * 76458.0,
                               got2[('свод ичи (Шахзод)', 11)][1], 2)
        # Ставка чужого заказчика (200 000) не взята ни в одном случае.
        self.assertNotAlmostEqual(200000.0,
                                  got[('свод ичи (Шахзод)', 11)][0], 2)

    # 1. Ноль «Топшириқ» -- записанный ноль, а не пустота.
    def test_the_topshiriq_row_gets_a_written_zero(self):
        run(self.db, '--apply')
        rate, amount = priced_rows(self.db)[('свод ичи (Фурқат)', 17)]
        self.assertIsNotNone(rate)
        self.assertAlmostEqual(0.0, float(rate), 6)
        self.assertAlmostEqual(0.0, float(amount), 6)

    # 1. Тождество «сумма = га x ставка» выполняется у всех четырёх.
    def test_every_priced_row_satisfies_the_identity(self):
        run(self.db, '--apply')
        conn = sqlite3.connect(self.db)
        try:
            bad = conn.execute(
                'SELECT source_sheet, source_row, area_ha, price_per_ha, '
                "amount FROM drone_works WHERE period_month = '2025-10' "
                'AND price_per_ha IS NOT NULL '
                'AND ABS(amount - area_ha * price_per_ha) > 0.01').fetchall()
        finally:
            conn.close()
        self.assertEqual([], bad)

    # 1. Соседняя октябрьская строка с ценой не тронута.
    def test_a_neighbour_row_is_not_touched(self):
        before = {r[0]: r for r in snapshot(self.db)}
        run(self.db, '--apply')
        after = {r[0]: r for r in snapshot(self.db)}
        touched = [i for i in before if before[i] != after[i]]
        self.assertEqual(4, len(touched))
        neighbour = priced_rows(self.db)[('свод ичи (Беҳзод)', 12)]
        self.assertAlmostEqual(200000.0, float(neighbour[0]), 2)

    # 2. ОТКАЗ: сентябрь молчит.
    def test_september_without_a_rate_is_refused(self):
        os.remove(self.db)
        build_db(self.db, sept=())
        before = snapshot(self.db)
        result = run(self.db, '--apply')
        self.assertEqual(1, result.returncode, result.stdout)
        self.assertIn('PRECONDITION FAILED', result.stdout)
        self.assertIn('September gives 0 distinct rate(s)', result.stdout)
        self.assertIn('--rate', result.stdout)
        self.assertEqual(before, snapshot(self.db))
        self.assertEqual([], registry(self.db))

    # 2. ОТКАЗ: сентябрь отвечает ДВУМЯ разными ставками.
    def test_two_september_rates_are_refused(self):
        """[REASON]: тариф внутреннего контура непостоянен, и выбрать за

        владельца между 75 040 и 85 633 нельзя: на 473.40 га это почти
        60 млн сум разницы.
        """
        os.remove(self.db)
        build_db(self.db, sept=((1, 85633.0, 300.0), (1, 75040.0, 211.5)))
        before = snapshot(self.db)
        result = run(self.db, '--apply')
        self.assertEqual(1, result.returncode, result.stdout)
        self.assertIn('September gives 2 distinct rate(s)', result.stdout)
        self.assertIn('85633.00', result.stdout)
        self.assertIn('75040.00', result.stdout)
        self.assertEqual(before, snapshot(self.db))

    # 2. --rate спасает, но помечается как OWNER-SUPPLIED.
    def test_the_owner_rate_is_used_and_labelled(self):
        os.remove(self.db)
        build_db(self.db, sept=())
        result = run(self.db, '--apply', '--rate', '85633')
        self.assertEqual(0, result.returncode, result.stdout)
        self.assertIn('OWNER-SUPPLIED', result.stdout)
        got = priced_rows(self.db)
        self.assertAlmostEqual(85633.0, got[('свод ичи (Шахзод)', 11)][0], 2)
        # Ноль «Топшириқ» остаётся нулём, а не становится 85 633.
        self.assertAlmostEqual(0.0,
                               float(got[('свод ичи (Фурқат)', 17)][0]), 6)

    # 3. Повтор ничего не делает.
    def test_second_apply_is_a_no_op(self):
        run(self.db, '--apply')
        after_first = snapshot(self.db)
        result = run(self.db, '--apply')
        self.assertEqual(0, result.returncode, result.stdout)
        self.assertIn('already applied', result.stdout)
        self.assertEqual(after_first, snapshot(self.db))

    # 4. Базы нет: код 2, файл НЕ создан.
    def test_missing_database_gives_code_two_and_creates_nothing(self):
        absent = os.path.join(self.dir, 'nope.db')
        result = run(absent)
        self.assertEqual(2, result.returncode, result.stdout)
        self.assertFalse(os.path.exists(absent))

    # 5. Предусловие: у строки уже есть цена -- не перезаписывать.
    def test_an_already_priced_row_is_refused(self):
        os.remove(self.db)
        build_db(self.db, priced=((11, 100000.0),))
        before = snapshot(self.db)
        result = run(self.db, '--apply')
        self.assertEqual(1, result.returncode, result.stdout)
        self.assertIn('already has a price', result.stdout)
        self.assertEqual(before, snapshot(self.db))

    # 5. Предусловие: не та площадь -- отказ.
    def test_a_different_area_is_refused(self):
        conn = sqlite3.connect(self.db)
        conn.execute("UPDATE drone_works SET area_ha = 300.0 WHERE "
                     "source_row = 11 AND period_month = '2025-10'")
        conn.commit()
        conn.close()
        before = snapshot(self.db)
        result = run(self.db, '--apply')
        self.assertEqual(1, result.returncode, result.stdout)
        self.assertIn('expected 296.00 ha, found 300.00', result.stdout)
        self.assertEqual(before, snapshot(self.db))

    # 6. Постусловие ловит несогласованную сумму.
    def test_the_postcondition_sees_an_amount_that_does_not_match(self):
        conn = sqlite3.connect(self.db)
        try:
            before = migration.month_totals(conn)
            plan, problems = migration.resolve_rates(conn)
            self.assertEqual([], problems)
            for work_id, _s, _r, _a, rate, _w in plan:
                conn.execute('UPDATE drone_works SET price_per_ha = ?, '
                             'amount = 1.0 WHERE id = ?', (rate, work_id))
            conn.commit()
            found = migration.check_postcondition(conn, plan, before)
        finally:
            conn.close()
        self.assertTrue(any('amount' in p and '!=' in p for p in found), found)

    def test_the_postcondition_is_silent_on_a_correct_apply(self):
        """Отрицательный контроль: сеть, срабатывающая всегда, бесполезна."""
        conn = sqlite3.connect(self.db)
        try:
            before = migration.month_totals(conn)
            plan, _p = migration.resolve_rates(conn)
        finally:
            conn.close()
        run(self.db, '--apply')
        conn = sqlite3.connect(self.db)
        try:
            found = migration.check_postcondition(conn, plan, before)
        finally:
            conn.close()
        self.assertEqual([], found)

    # 6. Постусловие ловит появившуюся из ниоткуда строку.
    def test_the_postcondition_sees_a_row_appearing(self):
        conn = sqlite3.connect(self.db)
        try:
            before = migration.month_totals(conn)
            plan, _p = migration.resolve_rates(conn)
        finally:
            conn.close()
        run(self.db, '--apply')
        conn = sqlite3.connect(self.db)
        try:
            conn.execute("INSERT INTO drone_works (period_month, "
                         'customer_raw, area_ha, source_file, source_sheet, '
                         "source_row) VALUES ('2025-10', 'Лишняя', 5.0, "
                         "'x.xlsx', 'x', 1)")
            conn.commit()
            found = migration.check_postcondition(conn, plan, before)
        finally:
            conn.close()
        self.assertTrue(any('row count moved' in p for p in found), found)

    # 7. Напечатанный откат возвращает ПУСТОТУ, а не ноль.
    def test_printed_rollback_restores_the_empty_price(self):
        before = snapshot(self.db)
        result = run(self.db, '--apply')
        self.assertEqual(0, result.returncode, result.stdout)
        statements, collecting = [], False
        for line in result.stdout.splitlines():
            if line.startswith('ROLLBACK OF DATA'):
                collecting = True
                continue
            if collecting and line.strip():
                statements.append(line.strip())
        conn = sqlite3.connect(self.db)
        try:
            for statement in statements:
                conn.execute(statement)
            conn.commit()
        finally:
            conn.close()
        self.assertEqual(before, snapshot(self.db))
        self.assertEqual([], registry(self.db))
        for (_sheet, srow), (rate, _amount) in priced_rows(self.db).items():
            if srow in (11, 15, 14, 17):
                self.assertIsNone(rate)

    # 8. Консоль -- только ASCII.
    def test_the_console_never_leaks_cyrillic(self):
        result = run(self.db, '--apply')
        result.stdout.encode('ascii')
        self.assertIn('TOTAL', result.stdout)


if __name__ == '__main__':
    unittest.main()
