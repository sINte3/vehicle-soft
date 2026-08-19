# -*- coding: utf-8 -*-
"""DRONE-BOOKS-DRIFT-001: инструмент видит дрейф месяца и ничего не чинит.

К каждой проверке -- отрицательный контроль, потому что диагностика,
показывающая одно и то же на здоровой и на больной базе, диагностикой не
является:

  1. Книга, подписанная чужим месяцем, находится целиком. Отрицательный
     контроль: на базе, где месяц книги совпадает с датами работ, отчёт
     говорит «Расхождений нет».
  2. Работа на краю месяца (30.04 в майской книге) тоже попадает в список --
     инструмент не решает за человека, что мелко, а что крупно, но она стоит
     НИЖЕ крупной: сортировка по гектарам и есть весь его вердикт.
  3. Строки без читаемой даты считаются отдельно: они не попадают ни в один
     месяц по дате и потому в первый список попасть не могут.
  4. Итог месяца печатается дважды -- по книге и по датам -- и разница
     сходится с найденным дрейфом.
  5. База открывается только на чтение: попытка записи в неё изнутри
     инструмента невозможна, а сам файл после прогона не меняется.
  6. Базы нет -> код 2, файл НЕ создан.

Run:
  python -m unittest tests.test_drone_books_month_drift_001 -v
"""
import ast
import hashlib
import os
import sqlite3
import subprocess
import sys
import tempfile
import unittest

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TOOL = os.path.join(REPO_ROOT, 'tools', 'drone_books_month_drift.py')

# (файл, месяц книги, дата работы, гектары)
ROWS = (
    # Когонская книга: подписана октябрём, работы сентябрьские -- 839.80 га.
    ('Когон ПТЗ Дрон маълумот.xlsx', '2025-10', '2025-09-11', 400.00),
    ('Когон ПТЗ Дрон маълумот.xlsx', '2025-10', '2025-09-18', 439.80),
    # Ғиждувонская: справка на 511.50 га с чужим месяцем.
    ('Ғиждувон ПТЗ Дрон маълумот.xlsx', '2025-09', '2025-10-19', 511.50),
    # Край месяца: 30.04 в майской книге -- законно, но видно.
    ('Сервис Дрон маълумот МАЙ.xlsx', '2026-05', '2026-04-30', 12.30),
    # Здоровые строки: месяц книги и месяц работы совпадают.
    ('Когон ПТЗ Дрон маълумот.xlsx', '2025-09', '2025-09-12', 172.60),
    ('Сервис Дрон маълумот МАЙ.xlsx', '2026-05', '2026-05-04', 300.00),
)
UNDATED = (('Сервис Дрон Маълумот.xlsx', '2025-09', 298.60),)


def build_db(path, rows=ROWS, undated=UNDATED):
    conn = sqlite3.connect(path)
    conn.execute('CREATE TABLE drone_works (id INTEGER PRIMARY KEY, '
                 'period_month TEXT NOT NULL, work_date_from TEXT, '
                 'area_ha REAL, source_file TEXT)')
    for src, period, day, ha in rows:
        conn.execute('INSERT INTO drone_works (period_month, work_date_from, '
                     'area_ha, source_file) VALUES (?, ?, ?, ?)',
                     (period, day, ha, src))
    for src, period, ha in undated:
        conn.execute('INSERT INTO drone_works (period_month, work_date_from, '
                     'area_ha, source_file) VALUES (?, NULL, ?, ?)',
                     (period, ha, src))
    conn.commit()
    conn.close()


def run(db, report):
    return subprocess.run([sys.executable, TOOL, '--db', db,
                           '--report', report],
                          capture_output=True, text=True)


class ToolCase(unittest.TestCase):

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.db = os.path.join(self.tmp.name, 'transport.db')
        self.report = os.path.join(self.tmp.name, 'drift.txt')

    def tearDown(self):
        self.tmp.cleanup()

    def read(self):
        with open(self.report, encoding='utf-8') as fh:
            return fh.read()


class FindsTheDrift(ToolCase):
    """1 и 2. Чужой месяц найден; край месяца показан, но ниже крупного."""

    def test_the_misfiled_book_is_found_whole(self):
        build_db(self.db)
        result = run(self.db, self.report)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        report = self.read()
        # Обе когонские строки собраны в одну запись: 400.00 + 439.80.
        self.assertIn('839.80', report)
        self.assertIn('книга подписана 2025-10, работы датированы 2025-09',
                      report)
        self.assertIn('511.50', report)
        # Здоровые строки в список не попали.
        self.assertNotIn('172.60 га', report)

    def test_healthy_database_says_so(self):
        """Отрицательный контроль: без дрейфа отчёт это и говорит."""
        healthy = tuple(r for r in ROWS if r[1] == r[2][:7])
        build_db(self.db, rows=healthy, undated=())
        run(self.db, self.report)
        report = self.read()
        self.assertIn('Расхождений нет', report)
        self.assertIn('Таких нет', report)

    def test_the_big_one_is_listed_above_the_edge_of_month(self):
        build_db(self.db)
        run(self.db, self.report)
        report = self.read()
        self.assertLess(report.index('839.80'), report.index('511.50'))
        self.assertLess(report.index('511.50'), report.index('12.30'))


class CountsHonestly(ToolCase):
    """3 и 4. Бездатные отдельно; итоги месяца сходятся с дрейфом."""

    def test_undated_rows_are_counted_separately(self):
        build_db(self.db)
        report = self.read() if os.path.exists(self.report) else None
        run(self.db, self.report)
        report = self.read()
        head, tail = report.split('2. РАБОТЫ БЕЗ ЧИТАЕМОЙ ДАТЫ', 1)
        self.assertNotIn('298.60', head, 'бездатная строка попала в дрейф')
        self.assertIn('298.60', tail)

    def test_month_totals_differ_by_exactly_the_drift(self):
        build_db(self.db)
        run(self.db, self.report)
        report = self.read()
        block = report.split('3. ИТОГ МЕСЯЦА', 1)[1]
        # Сентябрь по книге: 511.50 + 172.60 + 298.60 = 982.70;
        # по датам работ: 400.00 + 439.80 + 172.60 = 1012.40.
        self.assertIn('2025-09', block)
        line = [ln for ln in block.splitlines() if '2025-09' in ln][0]
        numbers = [float(x) for x in line.split()[1:]]
        self.assertAlmostEqual(numbers[0], 982.70, places=2)
        self.assertAlmostEqual(numbers[1], 1012.40, places=2)
        self.assertAlmostEqual(numbers[2], 29.70, places=2)


class TouchesNothing(ToolCase):
    """5 и 6. Только чтение; отсутствующая база не создаётся."""

    def test_the_database_file_is_not_modified(self):
        build_db(self.db)
        with open(self.db, 'rb') as fh:
            before = hashlib.sha256(fh.read()).hexdigest()
        run(self.db, self.report)
        with open(self.db, 'rb') as fh:
            after = hashlib.sha256(fh.read()).hexdigest()
        self.assertEqual(before, after, 'инструмент изменил базу')

    def test_the_source_has_no_write_statement(self):
        """Отрицательный контроль к предыдущему: чтения хеша мало.

        Совпадение хеша доказывает лишь то, что на ЭТИХ данных ничего не
        записалось. Запрет должен быть в самом файле.
        """
        with open(TOOL, encoding='utf-8') as fh:
            source = fh.read()
        tree = ast.parse(source)
        # Докстринги -- не SQL. Смотрим строковые литералы, из которых
        # запросы и собираются, а рассказ о том, что записи нет, пропускаем.
        docstrings = {ast.get_docstring(node, clean=False)
                      for node in ast.walk(tree)
                      if isinstance(node, (ast.Module, ast.FunctionDef,
                                           ast.AsyncFunctionDef, ast.ClassDef))}
        literals = [n.value.upper() for n in ast.walk(tree)
                    if isinstance(n, ast.Constant)
                    and isinstance(n.value, str)
                    and n.value not in docstrings]
        for word in ('INSERT', 'UPDATE', 'DELETE', 'CREATE', 'DROP',
                     'REPLACE INTO'):
            for text in literals:
                self.assertNotIn(word, text,
                                 'в запросе инструмента есть запись: %s' % word)
        # Отрицательный контроль к самой проверке: она способна что-то найти.
        self.assertTrue(any('SELECT' in text for text in literals),
                        'проверка не видит даже SELECT - она ничего не ловит')
        self.assertIn('mode=ro', source)

    def test_missing_database_exits_two_and_creates_nothing(self):
        missing = os.path.join(self.tmp.name, 'no', 'transport.db')
        result = subprocess.run([sys.executable, TOOL, '--db', missing],
                                capture_output=True, text=True)
        self.assertEqual(result.returncode, 2)
        self.assertFalse(os.path.exists(missing))


if __name__ == '__main__':
    unittest.main()
