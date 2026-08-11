# -*- coding: utf-8 -*-
"""DRONE-MARCH-AMOUNT-FIX-001: три клетки мартовской книги Когона.

Владелец, дословно: «Мартовские 12 000 000 в колонке затрат - ошибка
диспетчера, надо в „Жами сумма"». Проверяются оба полюса каждой гарантии:
сухой прогон не пишет; apply исправляет ровно три клетки; значение, не
совпавшее с ожиданием, называется и НЕ затирается; второй apply меняет ноль
строк; чужие строки книги не тронуты.

Run:
  python -m unittest tests.test_drone_march_amount_fix_001 -v
"""
import os
import sqlite3
import sys
import tempfile
import unittest

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, REPO_ROOT)

import backfill_drone_march_amount_fix as fix  # noqa: E402


def build_db(path, row8_amount=None, row8_price=85633.0,
             row6_other=800000.0):
    con = sqlite3.connect(path)
    con.execute("""CREATE TABLE drone_works (
        id INTEGER PRIMARY KEY, source_file TEXT, source_sheet TEXT,
        source_row INTEGER, area_ha REAL, price_per_ha REAL, amount REAL,
        other_costs REAL, received_amount REAL, payment_type TEXT,
        customer_raw TEXT)""")
    rows = [
        (fix.BOOK, fix.SHEET, 6, 15.7, 85633.0, None, row6_other, None,
         'transfer', 'Когон ППР'),
        (fix.BOOK, fix.SHEET, 7, 6.0, 85633.0, None, None, None,
         'transfer', 'Резиденция Бухоро ш.'),
        (fix.BOOK, fix.SHEET, 8, 60.0, row8_price, row8_amount, None, None,
         'transfer', 'Зарафшон Нурафшон фх'),
        # Сосед из другой книги — трогать нельзя.
        ('Другая книга.xlsx', 'свод ичи', 8, 10.0, 85633.0, None, 500000.0,
         None, 'transfer', 'Чужой фх'),
    ]
    con.executemany(
        'INSERT INTO drone_works (source_file, source_sheet, source_row, '
        'area_ha, price_per_ha, amount, other_costs, received_amount, '
        'payment_type, customer_raw) VALUES (?,?,?,?,?,?,?,?,?,?)', rows)
    con.commit()
    con.close()


class MarchFixTests(unittest.TestCase):

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix='march_fix_')
        self.db = os.path.join(self.tmp, 'transport.db')
        self.report = os.path.join(self.tmp, 'report.txt')

    def run_fix(self, *extra):
        code = fix.main(['--db', self.db, '--report', self.report]
                        + list(extra))
        with open(self.report, encoding='utf-8') as fh:
            return code, fh.read()

    def rows(self):
        con = sqlite3.connect(self.db)
        try:
            return {(r[0], r[1]): (r[2], r[3], r[4]) for r in con.execute(
                'SELECT source_file, source_row, amount, price_per_ha, '
                'other_costs FROM drone_works')}
        finally:
            con.close()

    def test_dry_run_writes_nothing_and_plans_three(self):
        build_db(self.db)
        before = self.rows()
        code, report = self.run_fix()
        self.assertEqual(code, 0)
        self.assertEqual(self.rows(), before)
        self.assertIn('Было бы исправлений: 3', report)

    def test_apply_fixes_exactly_the_three_cells(self):
        build_db(self.db)
        code, report = self.run_fix('--apply')
        self.assertEqual(code, 0)
        self.assertIn('ЗАПИСАНО: 3 исправлений', report)
        after = self.rows()
        self.assertEqual(after[(fix.BOOK, 8)], (12000000.0, 200000.0, None))
        self.assertEqual(after[(fix.BOOK, 6)], (None, 85633.0, None))
        self.assertEqual(after[(fix.BOOK, 7)], (None, 85633.0, None),
                         'строка 7 не в списке — трогать нельзя')
        self.assertEqual(after[('Другая книга.xlsx', 8)],
                         (None, 85633.0, 500000.0),
                         'чужая книга с тем же номером строки цела')

    def test_a_value_that_moved_is_refused_not_overwritten(self):
        """Отрицательный полюс: в базе уже 999 — правка отказывается."""
        build_db(self.db, row8_price=999.0)
        code, report = self.run_fix('--apply')
        self.assertEqual(code, 0)
        self.assertIn('ОТКАЗ', report)
        self.assertIn('ожидалось 85633.0', report)
        after = self.rows()
        self.assertEqual(after[(fix.BOOK, 8)][1], 999.0)
        # Остальные два исправления той же партии применяются.
        self.assertEqual(after[(fix.BOOK, 8)][0], 12000000.0)
        self.assertEqual(after[(fix.BOOK, 6)][2], None)

    def test_second_apply_changes_zero_rows(self):
        build_db(self.db)
        self.run_fix('--apply')
        after_first = self.rows()
        _code, report = self.run_fix('--apply')
        self.assertEqual(self.rows(), after_first)
        self.assertIn('0 rows to change.', report)
        self.assertIn('ОТКАЗ', report)

    def test_the_report_carries_rollback_and_the_book_edit(self):
        build_db(self.db)
        _code, report = self.run_fix('--apply')
        self.assertIn('UPDATE drone_works SET amount = NULL WHERE id =',
                      report)
        self.assertIn('UPDATE drone_works SET price_per_ha = 85633.0',
                      report)
        self.assertIn('UPDATE drone_works SET other_costs = 800000.0',
                      report)
        self.assertIn('перенести 12 000 000 из «Бошка харажатлар» (G8)',
                      report)

    def test_missing_database_exits_with_code_two(self):
        code = fix.main(['--db', os.path.join(self.tmp, 'absent.db'),
                         '--report', self.report])
        self.assertEqual(code, 2)


if __name__ == '__main__':
    unittest.main()
