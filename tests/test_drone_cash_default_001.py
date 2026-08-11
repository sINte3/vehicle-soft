# -*- coding: utf-8 -*-
"""DRONE-CASH-DEFAULT-001: «нет пометки — значит наличка» и выверенное
«получено».

Решение владельца 2026-08-11, приведено дословно в докстринге бэкфилла:
строка книги без явной пометки «Справка»/перечисление считается наличкой.
Здесь проверяются обе половины: правило в импортёре и его применение к уже
лежащим строкам, плюс девятнадцать выверенных значений «получено».

Каждая проверка с отрицательным полюсом: сухой прогон не пишет, повторный
--apply меняет ноль строк, чужое значение НЕ затирается, а записанный ноль
не превращается в пустоту.

Run:
  python -m unittest tests.test_drone_cash_default_001 -v
"""
import os
import sqlite3
import sys
import tempfile
import unittest

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(REPO_ROOT, 'tools'))
sys.path.insert(0, REPO_ROOT)

import import_drone_works as importer      # noqa: E402
import backfill_drone_cash_and_received as backfill  # noqa: E402

BOOK = 'Тест книга.xlsx'
SHEET = 'свод ичи (Тестчи)'

CSV_TEXT = """# тестовые правки
source_file;source_sheet;source_row;expected_received;new_received;note
{book};{sheet};4;;7800000.0;
{book};{sheet};5;;0.0;ПУЛ ОЛИНМАГАН
{book};{sheet};6;1000.0;555000.0;ожидание не совпадёт
{book};{sheet};99;;123.0;строки нет в базе
""".format(book=BOOK, sheet=SHEET)


def build_db(path, rows):
    con = sqlite3.connect(path)
    con.execute("""CREATE TABLE drone_works (
        id INTEGER PRIMARY KEY, source_file TEXT, source_sheet TEXT,
        source_row INTEGER, area_ha REAL, amount REAL, received_amount REAL,
        other_costs REAL, payment_type TEXT NOT NULL, customer_raw TEXT)""")
    con.executemany(
        'INSERT INTO drone_works (source_file, source_sheet, source_row, '
        'area_ha, amount, received_amount, other_costs, payment_type, '
        'customer_raw) VALUES (?,?,?,?,?,?,?,?,?)', rows)
    con.commit()
    con.close()


def rows_fixture():
    #  (файл, лист, строка, га, сумма, получено, харажат, тип, заказчик)
    return [
        (BOOK, SHEET, 4, 10.0, 8500000, None, 700000, 'unknown', 'Биринчи фх'),
        (BOOK, SHEET, 5, 5.0, 4280000, None, None, 'unknown', 'Иккинчи фх'),
        # Ожидание правки — 1000.0, а в базе 2000.0: правка обязана
        # отказаться, а тип оплаты у этой же строки — смениться.
        (BOOK, SHEET, 6, 7.0, 1400000, 2000.0, None, 'unknown', 'Учинчи фх'),
        # Чужие типы оплаты не трогаются вовсе.
        (BOOK, SHEET, 7, 3.0, 600000, 600000, None, 'transfer', 'Туртинчи фх'),
        (BOOK, SHEET, 8, 2.0, 400000, 400000, None, 'internal', 'Бешинчи фх'),
        (BOOK, SHEET, 9, 1.0, 200000, 200000, None, 'cash', 'Олтинчи фх'),
    ]


class ImporterRuleTests(unittest.TestCase):
    """Первая половина решения: правило живёт в импортёре."""

    def test_a_row_with_no_block_is_cash(self):
        self.assertEqual(importer.payment_when_no_block(),
                         importer.PAYMENT_CASH)

    def test_cash_is_not_the_unknown_bucket_in_disguise(self):
        """Отрицательный полюс: 'unknown' остаётся существующим значением.

        Тип не удалён и не переименован — он просто перестал возникать при
        импорте. Строка, набранная руками, по-прежнему может быть без типа,
        и отчёты обязаны уметь её показать.
        """
        self.assertIn(importer.PAYMENT_UNKNOWN, importer.PAYMENT_TYPES)
        self.assertNotIn(importer.PAYMENT_UNKNOWN,
                         importer.PAYMENT_OVERRIDE_TYPES)


class BackfillTests(unittest.TestCase):
    """Вторая половина: применение к уже лежащим строкам."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix='cash_default_')
        self.db = os.path.join(self.tmp, 'transport.db')
        self.csv = os.path.join(self.tmp, 'corrections.csv')
        with open(self.csv, 'w', encoding='utf-8') as fh:
            fh.write(CSV_TEXT)
        build_db(self.db, rows_fixture())
        self.report = os.path.join(self.tmp, 'report.txt')

    def run_backfill(self, *extra):
        code = backfill.main(['--db', self.db, '--corrections', self.csv,
                              '--report', self.report] + list(extra))
        with open(self.report, encoding='utf-8') as fh:
            return code, fh.read()

    def works(self):
        con = sqlite3.connect(self.db)
        try:
            return {r[0]: (r[1], r[2]) for r in con.execute(
                'SELECT source_row, payment_type, received_amount '
                'FROM drone_works')}
        finally:
            con.close()

    def test_dry_run_writes_nothing(self):
        before = self.works()
        code, report = self.run_backfill()
        self.assertEqual(code, 0)
        self.assertEqual(self.works(), before)
        self.assertIn('СУХОЙ ПРОГОН', report)
        self.assertIn('тип оплаты 3 строк', report)

    def test_apply_turns_unknown_into_cash_and_leaves_others_alone(self):
        self.run_backfill('--apply')
        after = self.works()
        self.assertEqual(after[4][0], 'cash')
        self.assertEqual(after[5][0], 'cash')
        self.assertEqual(after[6][0], 'cash')
        self.assertEqual(after[7][0], 'transfer', 'перечисление не трогать')
        self.assertEqual(after[8][0], 'internal', 'внутреннее не трогать')
        self.assertEqual(after[9][0], 'cash')

    def test_apply_fills_the_verified_received_amounts(self):
        self.run_backfill('--apply')
        after = self.works()
        self.assertAlmostEqual(after[4][1], 7800000.0, places=2)

    def test_a_recorded_zero_is_written_as_zero_not_left_empty(self):
        """«Деньги не получены» и «не записано» — разные утверждения."""
        self.run_backfill('--apply')
        self.assertIsNotNone(self.works()[5][1])
        self.assertAlmostEqual(self.works()[5][1], 0.0, places=2)

    def test_a_value_that_does_not_match_the_expectation_is_refused(self):
        """Отрицательный полюс: чужую правку молча не затирают."""
        code, report = self.run_backfill('--apply')
        self.assertEqual(code, 0)
        self.assertAlmostEqual(self.works()[6][1], 2000.0, places=2)
        self.assertIn('ОТКАЗ', report)

    def test_a_correction_for_a_row_that_is_absent_is_named(self):
        _code, report = self.run_backfill('--apply')
        self.assertIn('НЕТ В БАЗЕ', report)

    def test_second_apply_changes_nothing(self):
        self.run_backfill('--apply')
        after_first = self.works()
        _code, report = self.run_backfill('--apply')
        self.assertEqual(self.works(), after_first)
        self.assertIn('0 rows to change.', report)

    def test_the_report_carries_both_rollbacks_and_the_books_list(self):
        _code, report = self.run_backfill('--apply')
        self.assertIn("SET payment_type = 'unknown' WHERE id IN", report)
        self.assertIn('SET received_amount = NULL WHERE id =', report)
        self.assertIn('ВПИСАТЬ В САМИ КНИГИ', report)
        self.assertIn('«Кирим қилинган» = 7800000.0', report)

    def test_missing_database_exits_with_code_two(self):
        code = backfill.main(['--db', os.path.join(self.tmp, 'absent.db'),
                              '--corrections', self.csv,
                              '--report', self.report])
        self.assertEqual(code, 2)

    def test_missing_corrections_file_exits_with_code_two(self):
        code = backfill.main(['--db', self.db, '--report', self.report,
                              '--corrections',
                              os.path.join(self.tmp, 'absent.csv')])
        self.assertEqual(code, 2)


class RealCorrectionsFileTests(unittest.TestCase):
    """Файл правок, который поедет на production, читается и осмыслен."""

    def test_the_shipped_csv_parses_and_holds_nineteen_rows(self):
        rows = backfill.read_corrections(backfill.DEFAULT_CSV)
        self.assertEqual(len(rows), 19)
        zeros = [r for r in rows if r['new'] == 0]
        filled = [r for r in rows if r['new'] not in (0, None)]
        self.assertEqual(len(zeros), 5)
        self.assertEqual(len(filled), 14)
        for r in zeros:
            self.assertTrue(r['note'], 'ноль обязан нести пояснение владельца')
        for r in rows:
            self.assertTrue(r['file'] and r['sheet'] and r['row'])


if __name__ == '__main__':
    unittest.main()
