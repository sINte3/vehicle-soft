# -*- coding: utf-8 -*-
"""DRONE-SEPT2025-RECON-001: разбор «книга против телеметрии» на синтетике.

К каждой проверке -- отрицательный контроль, потому что проверка, дающая
одинаковый результат при верной и неверной карте, проверкой не является.
Ровно так и был найден дефект в самом инструменте: первая редакция считала
итогом месяца ВСЕ строки книги, поэтому отмена переноса справки, датированной
чужим месяцем, не меняла итог -- и проверка не различала два случая.

  1. Дата работы, а не книга, решает месяц. Строка, датированная другим
     месяцем, из итога месяца ИСКЛЮЧАЕТСЯ и называется отдельно.
     Отрицательный контроль: та же строка, объявленная в redated_rows,
     возвращается в месяц и меняет итог оператора.
  2. «Без даты» и «датирована другим месяцем» -- разные корзины. Первая
     входит в итог месяца, вторая нет; смешение даёт правдоподобный,
     но неверный итог.
  3. Диапазоны, перечисления и текстовая дата разбираются: «06-07.09.2025»
     -> два дня, «22,25.09.2025» -> два дня, «19-30.10.2025» -> двенадцать,
     «2025-09-26» -> один. Последнее -- дефект, найденный этим тестом:
     дату, набранную текстом, первая редакция отправляла в «без даты».
     Отрицательный контроль: «зимой» и «06-07» без месяца остаются без даты,
     а не получают выдуманное число.
  4. Оператор берётся из ИМЕНИ ЛИСТА, а не из колонки оператора: колонка в
     книгах врёт (сентябрь-2025, лист Жураева, 132.70 га подписаны чужим
     именем).
  5. Имя листа приводится к справочному картой алиасов; имя, которого в карте
     нет, -- отказ кодом 1 с названием и гектарами, а НЕ молчаливый ноль.
  6. Карта назначений обязана покрыть каждый вылет: непокрытый -> код 3.
  7. Гектары оператора считаются по окну машины, а не по машине целиком:
     одна машина, разделённая датой между двумя людьми, даёт им разные суммы.
  8. Каталога книг или выгрузки нет -> код 2, отчёт НЕ создан.

Run:
  python -m unittest tests.test_drone_month_books_vs_flights_001 -v
"""
import datetime
import json
import os
import subprocess
import sys
import tempfile
import unittest

import openpyxl

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TOOL = os.path.join(REPO_ROOT, 'tools', 'drone_month_books_vs_flights.py')
sys.path.insert(0, os.path.join(REPO_ROOT, 'tools'))

CASH_HEADER = ['№', 'ФХ номи', 'Майдон (га)', 'Хизмат кўрсатиш суммаси',
               'Жами сумма', 'Бошка харажатлар', 'Кирим қилинган',
               'Кирим қилинган сана', 'Дрон бошқарувчи оператор', '*Изоҳ']
TRANSFER_HEADER = ['№', 'ФХ номи', 'Мадон (га)', 'Хизмат кўрсатиш санаси',
                   'Хизмат кўрсатиш суммаси', 'Жами сумма']


def write_book(path, sheets):
    """sheets: {лист: {'cash': [(фх, га, дата, оператор_в_колонке)],
                       'transfer': [(фх, га, дата)]}}"""
    book = openpyxl.Workbook()
    book.remove(book.active)
    for title, tables in sheets.items():
        sheet = book.create_sheet(title[:31])
        row = 1
        sheet.cell(row=row, column=1, value='Маълумот')
        row += 1
        if tables.get('cash'):
            sheet.cell(row=row, column=1, value='Накд')
            row += 1
            for col, name in enumerate(CASH_HEADER, 1):
                sheet.cell(row=row, column=col, value=name)
            row += 1
            for idx, (farm, area, date, who) in enumerate(tables['cash'], 1):
                sheet.cell(row=row, column=1, value=idx)
                sheet.cell(row=row, column=2, value=farm)
                sheet.cell(row=row, column=3, value=area)
                sheet.cell(row=row, column=8, value=date)
                sheet.cell(row=row, column=9, value=who)
                row += 1
            sheet.cell(row=row, column=2, value='Жами сумма:')
            row += 2
        if tables.get('transfer'):
            sheet.cell(row=row, column=1, value='Справка')
            row += 1
            for col, name in enumerate(TRANSFER_HEADER, 1):
                sheet.cell(row=row, column=col, value=name)
            row += 1
            for idx, (farm, area, date) in enumerate(tables['transfer'], 1):
                sheet.cell(row=row, column=1, value=idx)
                sheet.cell(row=row, column=2, value=farm)
                sheet.cell(row=row, column=3, value=area)
                sheet.cell(row=row, column=4, value=date)
                row += 1
            sheet.cell(row=row, column=1, value='Жами:')
    book.save(path)


def write_flights(path, rows):
    """rows: [(дата-время, машина, ник, адрес, га)]"""
    book = openpyxl.Workbook()
    sheet = book.active
    sheet.title = 'Вылеты'
    for col, name in enumerate(['Дата и время (UTC+5)', 'Машина (№)',
                                'Ник (DJI)', 'Область', 'Адрес', 'Гектары'], 1):
        sheet.cell(row=1, column=col, value=name)
    for idx, (when, machine, nick, addr, area) in enumerate(rows, 2):
        sheet.cell(row=idx, column=1, value=when)
        sheet.cell(row=idx, column=2, value=machine)
        sheet.cell(row=idx, column=3, value=nick)
        sheet.cell(row=idx, column=4, value='Бухарская область')
        sheet.cell(row=idx, column=5, value=addr)
        sheet.cell(row=idx, column=6, value=area)
    book.save(path)


class MonthReconTest(unittest.TestCase):

    def setUp(self):
        self.dir = tempfile.mkdtemp(prefix='recon_')
        self.books = os.path.join(self.dir, 'books')
        os.makedirs(self.books)
        write_book(os.path.join(self.books, 'Когон ПТЗ Дрон маълумот.xlsx'), {
            'свод ичи Ибодуллаев Хасан': {
                # Колонка оператора врёт: подписана чужим именем.
                'cash': [('Ферма А', 40.0,
                          datetime.datetime(2025, 9, 20), 'Қодиров Нурали'),
                         ('Ферма Б', 20.0, '06-07.09.2025', 'Қодиров Нурали'),
                         ('Ферма В', 10.0, '22,25.09.2025', None),
                         ('Ферма Г', 4.0, '2025-09-26', None)],
                'transfer': [('Справка октября', 100.0, '19-30.10.2025'),
                             ('Справка без даты', 7.0, None),
                             ('Справка зимой', 3.0, 'зимой')],
            },
        })
        self.flights = os.path.join(self.dir, 'flights.xlsx')
        write_flights(self.flights, [
            ('2025-09-06 10:00:00', 11, 'Kogon№8', 'Kogon District', 9.0),
            ('2025-09-07 10:00:00', 11, 'Kogon№8', 'Kogon District', 9.0),
            ('2025-09-20 10:00:00', 11, 'Kogon№8', 'Kogon District', 40.0),
            ('2025-09-22 10:00:00', 11, 'Kogon№8', 'Kogon District', 5.0),
            ('2025-09-25 10:00:00', 11, 'Kogon№8', 'Kogon District', 5.0),
            ('2025-09-26 10:00:00', 11, 'Kogon№8', 'Kogon District', 6.0),
            # Август -- вне месяца, в отчёт попасть не должен.
            ('2025-08-31 10:00:00', 11, 'Kogon№8', 'Kogon District', 500.0),
        ])
        self.out = os.path.join(self.dir, 'out.xlsx')

    def spec(self, **over):
        base = {
            'month': '2025-09',
            'operator_aliases': {'ибодуллаев хасан': 'Ибодуллаев Хасанбой'},
            'assignments': [{'machine': 11, 'from': '2025-09-01',
                             'to': '2025-09-30',
                             'operator': 'Ибодуллаев Хасанбой', 'why': 'тест'}],
        }
        base.update(over)
        path = os.path.join(self.dir, 'spec_%d.json' % len(over))
        with open(path, 'w', encoding='utf-8') as handle:
            json.dump(base, handle, ensure_ascii=False)
        return path

    def run_tool(self, spec_path, out=None, extra=()):
        args = [sys.executable, TOOL, '--books-dir', self.books,
                '--flights', self.flights, '--month', '2025-09',
                '--out', out or self.out]
        if spec_path:
            args += ['--assignments', spec_path]
        args += list(extra)
        return subprocess.run(args, capture_output=True, text=True)

    def operators(self, path):
        book = openpyxl.load_workbook(path, data_only=False)
        sheet = book['По операторам']
        out = {}
        for row in sheet.iter_rows(min_row=2, values_only=True):
            if not row[0] or row[0] == 'ИТОГО':
                continue
            out[row[0]] = {'book': row[2], 'undated': row[3], 'tele': row[4]}
        book.close()
        return out

    # 1 + 2. Месяц решает дата работы; «без даты» и «чужой месяц» -- разное.
    def test_other_month_row_is_excluded_from_the_month(self):
        result = self.run_tool(self.spec())
        self.assertEqual(0, result.returncode, result.stdout + result.stderr)
        row = self.operators(self.out)['Ибодуллаев Хасанбой']
        # Наличные с датой 40 + 20 + 10 + 4 = 74, плюс 7 без даты и 3
        # нечитаемых = 84. Справка октября (100) в месяц не входит.
        self.assertAlmostEqual(84.0, row['book'], places=2)
        self.assertAlmostEqual(10.0, row['undated'], places=2)
        self.assertAlmostEqual(74.0, row['tele'], places=2)

    # 1, отрицательный контроль: объявленный перенос меняет итог.
    def test_redated_row_returns_into_the_month(self):
        spec = self.spec(redated_rows=[{
            'operator': 'Ибодуллаев Хасанбой', 'farm': 'Справка октября',
            'ha': 100.0, 'days': ['2025-09-28', '2025-09-29'],
        }])
        result = self.run_tool(spec)
        self.assertEqual(0, result.returncode, result.stdout + result.stderr)
        row = self.operators(self.out)['Ибодуллаев Хасанбой']
        self.assertAlmostEqual(184.0, row['book'], places=2)

    # 3. Диапазоны и перечисления; отрицательный контроль -- мусор без даты.
    def test_date_ranges_and_lists_are_parsed_garbage_is_not(self):
        import drone_month_books_vs_flights as tool
        self.assertEqual(['2025-09-06', '2025-09-07'],
                         tool.parse_date_cell('06-07.09.2025', 2025, 9)[1])
        self.assertEqual(['2025-09-22', '2025-09-25'],
                         tool.parse_date_cell('22,25.09.2025', 2025, 9)[1])
        self.assertEqual(12, len(tool.parse_date_cell('19-30.10.2025',
                                                      2025, 9)[1]))
        self.assertEqual(['2025-09-26'],
                         tool.parse_date_cell('2025-09-26', 2025, 9)[1])
        self.assertEqual(['2025-09-26'],
                         tool.parse_date_cell('2025-09-26 07:15:00',
                                              2025, 9)[1])
        for garbage in ('зимой', '', None, '35.09.2025', '30-19.09.2025'):
            self.assertEqual([], tool.parse_date_cell(garbage, 2025, 9)[1],
                             'из %r нельзя выводить дату' % (garbage,))

    # 4. Оператор -- из имени листа, не из колонки.
    def test_operator_comes_from_the_sheet_name_not_the_column(self):
        rows = self.operators(self.out) if os.path.exists(self.out) else None
        if rows is None:
            self.run_tool(self.spec())
            rows = self.operators(self.out)
        self.assertIn('Ибодуллаев Хасанбой', rows)
        self.assertNotIn('Қодиров Нурали', rows)

    # 5. Непокрытое картой имя -- отказ с числом, не молчаливый ноль.
    def test_unknown_operator_name_fails_loudly(self):
        result = self.run_tool(self.spec(operator_aliases={}))
        self.assertEqual(1, result.returncode, result.stdout)
        self.assertIn('absent from the assignment map', result.stdout)
        self.assertIn('84.00', result.stdout)
        self.assertFalse(os.path.exists(self.out))

    # 6. Непокрытый вылет -- код 3.
    def test_uncovered_flight_fails_with_code_three(self):
        spec = self.spec(assignments=[{'machine': 11, 'from': '2025-09-01',
                                       'to': '2025-09-19',
                                       'operator': 'Ибодуллаев Хасанбой',
                                       'why': 'окно короче месяца'}])
        result = self.run_tool(spec)
        self.assertEqual(3, result.returncode, result.stdout)
        self.assertIn('not covered', result.stdout)

    # 7. Одна машина, разделённая датой, даёт двум людям разные суммы.
    def test_one_machine_split_by_date_splits_hectares(self):
        spec = self.spec(
            operator_aliases={'ибодуллаев хасан': 'Ибодуллаев Хасанбой'},
            assignments=[
                {'machine': 11, 'from': '2025-09-01', 'to': '2025-09-10',
                 'operator': 'Хамроев Шохрух', 'why': 'первые дни'},
                {'machine': 11, 'from': '2025-09-11', 'to': '2025-09-30',
                 'operator': 'Ибодуллаев Хасанбой', 'why': 'остаток'},
            ])
        result = self.run_tool(spec)
        self.assertEqual(0, result.returncode, result.stdout + result.stderr)
        rows = self.operators(self.out)
        self.assertAlmostEqual(18.0, rows['Хамроев Шохрух']['tele'], places=2)
        self.assertAlmostEqual(56.0, rows['Ибодуллаев Хасанбой']['tele'],
                               places=2)

    # 8. Нет входных файлов -> код 2, отчёт не создан.
    def test_missing_inputs_give_code_two_and_no_report(self):
        args = [sys.executable, TOOL, '--books-dir',
                os.path.join(self.dir, 'nope'), '--flights', self.flights,
                '--month', '2025-09', '--out', self.out]
        result = subprocess.run(args, capture_output=True, text=True)
        self.assertEqual(2, result.returncode, result.stdout)
        self.assertFalse(os.path.exists(self.out))

    # Самоконтроль --expect-books-ha различает верное и неверное число.
    def test_expect_books_ha_discriminates(self):
        ok = self.run_tool(self.spec(), extra=['--expect-books-ha', '184.0'])
        self.assertEqual(0, ok.returncode, ok.stdout + ok.stderr)
        bad = self.run_tool(self.spec(), extra=['--expect-books-ha', '999.0'])
        self.assertEqual(1, bad.returncode, bad.stdout)
        self.assertIn('expected', bad.stdout)


if __name__ == '__main__':
    unittest.main()
