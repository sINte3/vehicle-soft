# -*- coding: utf-8 -*-
"""DRONE-OPERATOR-AUDIT-001: аудит раскладывает гектары и ничего не теряет.

Фикстура посчитана руками, и каждая проверка держит отрицательный контроль --
аудит, который на любой базе печатает «сходится», аудитом не является:

  апрель-2026:  машина 1 (оператор Алим, открытое назначение):
                  01.04 -- 10 га, 02.04 -- 20 га, 03.04 -- 30 га;
                  но 03.04 покрыт ЕЩЁ и точечным назначением Бека -> спорное;
                машина 2 (Бек, до 30.04): 04.04 -- 40 га;
                вылет без машины: 20.04 -- 5 га.
                ИТОГО телеметрии 105 = Алим 30 + Бек 40 + спорное 30 + 5.
  май-2026:     машина 1 (Алим): 10.05 -- 7 га;
                машина 2: 05.05 -- 15 га, назначение Бека кончилось 30.04
                -> без назначения. ИТОГО 22 = Алим 7 + без назначения 15.
  книги:        Алим: 01-02.04 25 га (лётные дни), 10.04 5 га (машина
                молчала -> класс В), без даты 7 га (месяц книги 2026-04);
                Хасан (в справочнике не найден): 04.04 40 га.

Run:
  python -m unittest tests.test_drone_operator_audit_001 -v
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
TOOL = os.path.join(REPO_ROOT, 'tools', 'drone_operator_audit.py')

ALIM = 'Оператор Алим'
BEK = 'Оператор Бек'


def build_db(path):
    conn = sqlite3.connect(path)
    conn.execute('CREATE TABLE drone_units (id INTEGER PRIMARY KEY, '
                 'number INTEGER UNIQUE)')
    conn.execute('INSERT INTO drone_units (id, number) VALUES (1, 1), (2, 2)')
    conn.execute('CREATE TABLE drone_operators (id INTEGER PRIMARY KEY, '
                 'full_name TEXT NOT NULL)')
    conn.execute('INSERT INTO drone_operators (id, full_name) '
                 'VALUES (1, ?), (2, ?)', (ALIM, BEK))
    conn.execute('CREATE TABLE drone_operator_assignments '
                 '(id INTEGER PRIMARY KEY, operator_id INTEGER NOT NULL, '
                 ' drone_unit_id INTEGER NOT NULL, date_from TEXT NOT NULL, '
                 ' date_to TEXT, note TEXT)')
    conn.executemany(
        'INSERT INTO drone_operator_assignments '
        '(operator_id, drone_unit_id, date_from, date_to, note) '
        'VALUES (?, ?, ?, ?, ?)',
        [(1, 1, '2026-04-01', None, '[SHEET] лист'),
         (2, 2, '2026-04-01', '2026-04-30', '[OWNER] владелец'),
         # Точечное второе назначение на машину 1 -- день 03.04 спорный.
         (2, 1, '2026-04-03', '2026-04-03', '[OWNER] спорный день')])
    conn.execute('CREATE TABLE drone_flights (id INTEGER PRIMARY KEY, '
                 'drone_unit_id INTEGER, started_at TEXT, area_ha REAL)')
    conn.executemany(
        'INSERT INTO drone_flights (drone_unit_id, started_at, area_ha) '
        'VALUES (?, ?, ?)',
        [(1, '2026-04-01 07:00:00', 10.0),
         (1, '2026-04-02 07:00:00', 20.0),
         (1, '2026-04-03 07:00:00', 30.0),
         (2, '2026-04-04 07:00:00', 40.0),
         (None, '2026-04-20 07:00:00', 5.0),
         (1, '2026-05-10 07:00:00', 7.0),
         (2, '2026-05-05 07:00:00', 15.0)])
    conn.execute('CREATE TABLE drone_works (id INTEGER PRIMARY KEY, '
                 'drone_operator_id INTEGER, operator_raw TEXT, '
                 'work_date_from TEXT, work_date_to TEXT, '
                 'period_month TEXT NOT NULL, area_ha REAL, '
                 'source_file TEXT, source_sheet TEXT, source_row INTEGER)')
    conn.executemany(
        'INSERT INTO drone_works (drone_operator_id, operator_raw, '
        ' work_date_from, work_date_to, period_month, area_ha, '
        ' source_file, source_sheet, source_row) '
        'VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)',
        [(1, 'Алим', '2026-04-01', '2026-04-02', '2026-04', 25.0,
          'Книга А.xlsx', 'свод', 4),
         # Машина Алима 10.04 молчала -> класс В.
         (1, 'Алим', '2026-04-10', None, '2026-04', 5.0,
          'Книга А.xlsx', 'свод', 9),
         # Без даты -> колонка «без даты», в дни не попадает.
         (1, 'Алим', None, None, '2026-04', 7.0,
          'Книга А.xlsx', 'свод', 12),
         # Оператор в справочнике не найден.
         (None, 'Хасан', '2026-04-04', None, '2026-04', 40.0,
          'Книга К.xlsx', 'свод', 3)])
    conn.commit()
    conn.close()


def run(db, out):
    return subprocess.run(
        [sys.executable, TOOL, '--db', db, '--month-from', '2026-04',
         '--month-to', '2026-05', '--out', out],
        capture_output=True, text=True)


def load_sheet(out, title):
    import openpyxl
    wb = openpyxl.load_workbook(out, data_only=True)
    try:
        return list(wb[title].iter_rows(min_row=2, values_only=True))
    finally:
        wb.close()


class AuditCase(unittest.TestCase):

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.db = os.path.join(self.tmp.name, 'transport.db')
        self.out = os.path.join(self.tmp.name, 'audit.xlsx')
        build_db(self.db)

    def tearDown(self):
        self.tmp.cleanup()


class TheMatrix(AuditCase):
    """Матрица и сводка: числа совпадают с посчитанными руками."""

    def test_summary_buckets_add_up_and_say_so(self):
        result = run(self.db, self.out)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        rows = {r[0]: r for r in load_sheet(self.out, 'Сводка')}
        # Апрель: 105 = операторы 70 + спорное 30 + без назначения 0 + 5.
        month = rows['2026-04']
        self.assertAlmostEqual(month[1], 105.0, places=2)
        self.assertAlmostEqual(month[2], 70.0, places=2)
        self.assertAlmostEqual(month[3], 30.0, places=2)
        self.assertAlmostEqual(month[4], 0.0, places=2)
        self.assertAlmostEqual(month[5], 5.0, places=2)
        self.assertEqual(month[8], 'сходится')
        # Май: 22 = Алим 7 + без назначения 15.
        month = rows['2026-05']
        self.assertAlmostEqual(month[1], 22.0, places=2)
        self.assertAlmostEqual(month[2], 7.0, places=2)
        self.assertAlmostEqual(month[4], 15.0, places=2)
        self.assertEqual(month[8], 'сходится')
        self.assertIn('[ok]', result.stdout)
        self.assertNotIn('MISMATCH', result.stdout)

    def test_operator_month_rows_hand_checked(self):
        run(self.db, self.out)
        rows = {(r[0], r[1]): r
                for r in load_sheet(self.out, 'Оператор x месяц')}
        alim = rows[(ALIM, '2026-04')]
        # Телеметрия Алима БЕЗ спорного дня: 10 + 20 = 30, не 60.
        self.assertAlmostEqual(alim[2], 30.0, places=2)
        self.assertAlmostEqual(alim[3], 30.0, places=2)   # 25 + 5 с датой
        self.assertAlmostEqual(alim[4], 7.0, places=2)    # без даты
        self.assertAlmostEqual(alim[5], 37.0, places=2)
        self.assertAlmostEqual(alim[6], 7.0, places=2)    # книга - телеметрия
        bek = rows[(BEK, '2026-04')]
        self.assertAlmostEqual(bek[2], 40.0, places=2)
        self.assertAlmostEqual(bek[5], 0.0, places=2)
        # Нераспознанный оператор виден строкой, а не проглочен.
        hasan = rows[('-- не распознан --', '2026-04')]
        self.assertAlmostEqual(hasan[5], 40.0, places=2)

    def test_the_disputed_day_is_named_not_split(self):
        run(self.db, self.out)
        rows = load_sheet(self.out, 'Спорное покрытие')
        self.assertEqual(len(rows), 1)
        day, number, names, _flights, ha = rows[0]
        self.assertEqual((day, number), ('2026-04-03', 1))
        self.assertIn(ALIM, names)
        self.assertIn(BEK, names)
        self.assertAlmostEqual(ha, 30.0, places=2)


class TheClasses(AuditCase):
    """Классы А и В: попадает виновное, не попадает невинное."""

    def test_class_a_lists_unbooked_flight_days_only(self):
        run(self.db, self.out)
        rows = load_sheet(self.out, 'Летал -- записи нет')
        days = {(r[2], r[1]) for r in rows}
        # Спорный день 03.04 не записан ни у одного -- у обоих класс А;
        # у Алима ещё 10.05 (май без книг вовсе).
        self.assertIn((ALIM, '2026-04-03'), days)
        self.assertIn((BEK, '2026-04-03'), days)
        self.assertIn((ALIM, '2026-05-10'), days)
        self.assertIn((BEK, '2026-04-04'), days)   # Бек не писал книг вовсе
        # Отрицательный контроль: записанные дни 01-02.04 в класс не попали.
        self.assertNotIn((ALIM, '2026-04-01'), days)
        self.assertNotIn((ALIM, '2026-04-02'), days)

    def test_class_c_lists_the_work_on_a_silent_day_only(self):
        run(self.db, self.out)
        rows = load_sheet(self.out, 'Запись -- полёта нет')
        self.assertEqual(len(rows), 1)
        op, month, src, _sheet, srow, date_from, _to, ha = rows[0]
        self.assertEqual(op, ALIM)
        self.assertEqual(date_from, '2026-04-10')
        self.assertEqual(srow, 9)
        self.assertAlmostEqual(ha, 5.0, places=2)
        # Отрицательный контроль встроен: работа 01-02.04 (лётные дни)
        # в списке отсутствует, иначе rows было бы больше одной.

    def test_unassigned_and_no_machine_are_separate_buckets(self):
        run(self.db, self.out)
        rows = load_sheet(self.out, 'Без назначения')
        as_map = {(r[0], r[1]): r[3] for r in rows}
        self.assertAlmostEqual(as_map[('2026-05', 2)], 15.0, places=2)
        self.assertAlmostEqual(as_map[('2026-04', 'без машины')], 5.0,
                               places=2)
        # Отрицательный контроль: покрытый апрель машины 2 в корзину не попал.
        self.assertNotIn(('2026-04', 2), as_map)

    def test_assignments_legend_carries_sources(self):
        run(self.db, self.out)
        rows = load_sheet(self.out, 'Назначения')
        sources = {r[5] for r in rows}
        self.assertEqual(sources, {'[SHEET]', '[OWNER]'})


class TouchesNothing(AuditCase):
    """Только чтение, доказано дважды; отсутствующая база не создаётся."""

    def test_the_database_file_is_not_modified(self):
        with open(self.db, 'rb') as fh:
            before = hashlib.sha256(fh.read()).hexdigest()
        run(self.db, self.out)
        with open(self.db, 'rb') as fh:
            after = hashlib.sha256(fh.read()).hexdigest()
        self.assertEqual(before, after, 'аудит изменил базу')

    def test_the_source_has_no_write_statement(self):
        """Хеш доказывает «на этих данных»; запрет должен жить в файле."""
        with open(TOOL, encoding='utf-8') as fh:
            source = fh.read()
        tree = ast.parse(source)
        docstrings = {ast.get_docstring(node, clean=False)
                      for node in ast.walk(tree)
                      if isinstance(node, (ast.Module, ast.FunctionDef,
                                           ast.AsyncFunctionDef,
                                           ast.ClassDef))}
        literals = [n.value.upper() for n in ast.walk(tree)
                    if isinstance(n, ast.Constant)
                    and isinstance(n.value, str)
                    and n.value not in docstrings]
        for word in ('INSERT', 'UPDATE ', 'DELETE', 'DROP ', 'REPLACE INTO',
                     'CREATE TABLE'):
            for text in literals:
                self.assertNotIn(word, text,
                                 'в запросе аудита есть запись: %s' % word)
        self.assertTrue(any('SELECT' in text for text in literals),
                        'проверка не видит даже SELECT -- она ничего не ловит')
        self.assertIn('mode=ro', source)

    def test_missing_database_exits_two_and_creates_nothing(self):
        missing = os.path.join(self.tmp.name, 'no', 'transport.db')
        result = subprocess.run([sys.executable, TOOL, '--db', missing],
                                capture_output=True, text=True)
        self.assertEqual(result.returncode, 2)
        self.assertFalse(os.path.exists(missing))


if __name__ == '__main__':
    unittest.main()
