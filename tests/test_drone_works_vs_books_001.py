# -*- coding: utf-8 -*-
"""DRONE-BOOKS-VERIFY-001: сверка drone_works с книгами ловит расхождения.

Проверка, которая всегда говорит «сошлось», проверкой не является, поэтому
здесь два контроля на одной фикстуре: на нетронутой базе сверка обязана
дать НОЛЬ расхождений, а после шести подсадок — ровно эти шесть, каждую в
своей графе. Подсадки выбраны разными по природе: изменённое число,
появившееся значение там, где в книге пусто, стёртое значение, работа без
строки в книге и строка книги без работы.

Run:
  python -m unittest tests.test_drone_works_vs_books_001 -v
"""
import os
import sqlite3
import sys
import tempfile
import unittest

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(REPO_ROOT, 'tools'))
import verify_drone_works_against_books as verify  # noqa: E402

BOOK = 'Тест Дрон маълумот.xlsx'
SHEET = 'свод ичи (Тестчи)'

# Шапка ровно теми фразами, что ищет импортёр: сверка обязана разбирать
# книгу его правилами, иначе она проверяла бы собственный парсер.
HEADER = ['№', 'ФХ номи ', 'Майдон (га)', 'Хизмат кўрсатиш суммаси',
          'Жами сумма\n(обьём)', 'Бошка харажатлар ', 'Кирим қилинган',
          'Дрон \nишлаган сана', '*Изоҳ']

# (№, заказчик, га, цена, сумма, харажат, получено)
DATA = [
    (1, 'Биринчи фх', 10.0, 200000, 2000000, 300000, 1700000),
    (2, 'Иккинчи фх', 5.5, 200000, 1100000, None, 1100000),
    (3, 'Учинчи фх', 20.0, 150000, 3000000, 500000, 2500000),
    (4, 'Туртинчи фх', 7.25, 200000, 1450000, None, None),
]


def write_book(directory):
    from openpyxl import Workbook
    wb = Workbook()
    ws = wb.active
    ws.title = SHEET
    ws.append(['"Тест ПТЗ" АЖга қарашли Дронларни'])
    ws.append([])
    ws.append(['Нақд (Март ойи)'])
    ws.append(HEADER)
    for row in DATA:
        num, customer, area, price, amount, other, received = row
        ws.append([num, customer, area, price, amount, other, received,
                   '2026-03-15', None])
    path = os.path.join(directory, BOOK)
    wb.save(path)
    return path


def write_manifest(directory):
    path = os.path.join(directory, 'manifest.txt')
    with open(path, 'w', encoding='utf-8') as handle:
        handle.write('# тестовый манифест\n%s 2026-03\n' % BOOK)
    return path


def build_db(path, rows):
    con = sqlite3.connect(path)
    con.execute("""CREATE TABLE drone_works (
        id INTEGER PRIMARY KEY, source_file TEXT, source_sheet TEXT,
        source_row INTEGER, area_ha REAL, price_per_ha REAL, amount REAL,
        received_amount REAL, other_costs REAL, customer_raw TEXT)""")
    con.executemany(
        'INSERT INTO drone_works (source_file, source_sheet, source_row, '
        'area_ha, price_per_ha, amount, received_amount, other_costs, '
        'customer_raw) VALUES (?,?,?,?,?,?,?,?,?)', rows)
    con.commit()
    con.close()


class VerifyAgainstBooksTests(unittest.TestCase):

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix='books_verify_')
        write_book(self.tmp)
        self.manifest = write_manifest(self.tmp)
        self.db = os.path.join(self.tmp, 'transport.db')
        # Книга разбирается тем же парсером — фикстура базы строится из его
        # результата, а не из констант: иначе тест сверял бы мои ожидания.
        self.book_rows, problems = verify.parse_books(self.tmp,
                                                      {BOOK: '2026-03'})
        self.assertEqual(problems, [], 'книга должна разбираться без проблем')
        self.assertEqual(len(self.book_rows), len(DATA))

    def _db_rows(self):
        out = []
        for (f, s, r), row in sorted(self.book_rows.items()):
            out.append((f, s, r, row.get('area_ha'), row.get('price_per_ha'),
                        row.get('amount'), row.get('received_amount'),
                        row.get('other_costs'), row.get('customer_raw')))
        return out

    def _run(self):
        report = os.path.join(self.tmp, 'report.txt')
        argv = sys.argv
        sys.argv = ['verify', '--db', self.db, '--dir', self.tmp,
                    '--manifest', self.manifest, '--report', report]
        try:
            self.assertEqual(verify.main(), 0)
        finally:
            sys.argv = argv
        with open(report, encoding='utf-8') as handle:
            return handle.read()

    def test_untouched_database_reports_no_discrepancy(self):
        """Положительный полюс: сходится — значит сходится."""
        build_db(self.db, self._db_rows())
        report = self._run()
        self.assertIn('СОВПАЛИ ПОЛНОСТЬЮ: %d работ' % len(DATA), report)
        self.assertIn('РАСХОДЯТСЯ       : 0 работ', report)
        self.assertIn('ТОЛЬКО В БАЗЕ    : 0', report)
        self.assertIn('ТОЛЬКО В КНИГАХ  : 0', report)

    def test_every_kind_of_discrepancy_is_found_and_named(self):
        """Отрицательный полюс: шесть подсадок разной природы."""
        build_db(self.db, self._db_rows())
        con = sqlite3.connect(self.db)
        con.execute('UPDATE drone_works SET amount = amount + 5000000 '
                    'WHERE source_row = 5')
        con.execute('UPDATE drone_works SET area_ha = area_ha + 3.5 '
                    'WHERE source_row = 6')
        con.execute('UPDATE drone_works SET other_costs = 777000 '
                    'WHERE source_row = 6')
        con.execute('UPDATE drone_works SET received_amount = NULL '
                    'WHERE source_row = 7')
        con.execute(
            'INSERT INTO drone_works (source_file, source_sheet, source_row, '
            'area_ha, price_per_ha, amount, received_amount, other_costs, '
            'customer_raw) VALUES (?,?,?,?,?,?,?,?,?)',
            (BOOK, SHEET, 999, 10.0, 200000, 2000000, 0, None, 'Призрак фх'))
        con.execute('DELETE FROM drone_works WHERE source_row = 8')
        con.commit()
        con.close()

        report = self._run()
        self.assertIn('РАСХОДЯТСЯ       : 3 работ', report)
        self.assertIn('ТОЛЬКО В БАЗЕ    : 1', report)
        self.assertIn('ТОЛЬКО В КНИГАХ  : 1', report)
        self.assertIn('+5000000.00', report)
        self.assertIn('+3.50', report)
        self.assertIn('+777000.00', report)
        self.assertIn('Призрак фх', report)
        for title in ('РАСХОЖДЕНИЯ ПО «ПЛОЩАДЬ»', 'РАСХОЖДЕНИЯ ПО «СУММА»',
                      'РАСХОЖДЕНИЯ ПО «ПОЛУЧЕНО»',
                      'РАСХОЖДЕНИЯ ПО «ЗАТРАТЫ»'):
            self.assertIn(title, report, title)

    def test_float_dust_is_not_a_discrepancy(self):
        """8.7 в книге и 8.699999999999999 в базе — одно число, не находка."""
        rows = self._db_rows()
        rows = [tuple(v + 1e-9 if i == 3 and isinstance(v, float) else v
                      for i, v in enumerate(r)) for r in rows]
        build_db(self.db, rows)
        report = self._run()
        self.assertIn('РАСХОДЯТСЯ       : 0 работ', report)

    def test_the_script_never_writes(self):
        """Режим mode=ro: запись через то же соединение обязана падать."""
        build_db(self.db, self._db_rows())
        con = verify.open_readonly(self.db)
        try:
            with self.assertRaises(sqlite3.OperationalError):
                con.execute('UPDATE drone_works SET amount = 1')
        finally:
            con.close()

    def test_missing_database_exits_with_code_two(self):
        with self.assertRaises(SystemExit) as caught:
            verify.open_readonly(os.path.join(self.tmp, 'absent.db'))
        self.assertEqual(caught.exception.code, 2)


if __name__ == '__main__':
    unittest.main()
