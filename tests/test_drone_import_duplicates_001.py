# -*- coding: utf-8 -*-
"""DRONE-IMPORT-DUP-001: поиск книг, загруженных дважды.

Одна и та же книга, загруженная вторым файлом с суффиксом « (2)», удваивает
гектары и деньги: уникальность строки -- тройка (файл, лист, номер строки), и
второе имя файла делает её другой. В сентябре 2025 это дало +118.40 га.

Проверяется и то, что отчёт находит двойную загрузку, и то, что он МОЛЧИТ,
когда её нет: проверка, срабатывающая всегда, проверкой не является.
Отдельно -- что он ничего не пишет и не может писать.

Run:
  python -m unittest tests.test_drone_import_duplicates_001 -v
"""
import ast
import os
import sqlite3
import sys
import tempfile
import unittest

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if REPO_ROOT not in sys.path:
    sys.path.insert(0, REPO_ROOT)
sys.path.insert(0, os.path.join(REPO_ROOT, 'tools'))

import drone_import_duplicates as tool  # noqa: E402

SCHEMA = """
CREATE TABLE drone_works (id INTEGER PRIMARY KEY AUTOINCREMENT,
  period_month TEXT NOT NULL, work_date_from TEXT, customer_raw TEXT,
  area_ha NUMERIC, amount NUMERIC, source_file TEXT, source_sheet TEXT,
  source_row INTEGER);
"""

# Настоящая картина сентября: книга Сервиса загружена дважды.
GOOD = 'Сервис Дрон Маълумот (2).xlsx'
STALE = 'Сервис Дрон Маълумот.xlsx'
ROWS = (
    (GOOD, 'свод ичи (Мухриддин)', 11, 205.00, '2025-09'),
    (STALE, 'свод ичи (Мухриддин)', 6, 93.60, '2025-09'),
    (GOOD, 'свод ичи (Фурқат)', 37, 398.90, '2025-09'),
    (STALE, 'свод ичи (Фурқат)', 9, 24.80, '2025-09'),
    (GOOD, 'свод ичи (Беҳзод)', 38, 406.63, '2025-09'),
    ('Гарден Агрокластер.xlsx', 'свод ичи (Нурали)', 57, 884.50, '2025-09'),
)


def build_db(path, rows=ROWS, manual=0):
    conn = sqlite3.connect(path)
    conn.executescript(SCHEMA)
    row_id = 0
    for source_file, sheet, count, hectares, month in rows:
        per = hectares / count
        for number in range(count):
            row_id += 1
            conn.execute(
                'INSERT INTO drone_works (id, period_month, work_date_from, '
                'customer_raw, area_ha, amount, source_file, source_sheet, '
                'source_row) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)',
                (row_id, month, month + '-10', 'ФХ %d' % row_id, per,
                 per * 200000, source_file, sheet, number + 4))
    # [REASON]: строка, набранная руками, не имеет ни файла, ни листа.
    # Она законно «ниоткуда»; если такие складывать в одну группу, отчёт
    # объявит двойной загрузкой всю ручную правку месяца.
    for number in range(manual):
        row_id += 1
        conn.execute(
            'INSERT INTO drone_works (id, period_month, work_date_from, '
            'customer_raw, area_ha, amount, source_file, source_sheet, '
            "source_row) VALUES (?, '2025-09', '2025-09-10', ?, 5.0, "
            '1000000, NULL, NULL, NULL)', (row_id, 'Ручная %d' % number))
    # [REASON]: и ещё одна ловушка -- файл, у которого имя листа пустое.
    # Вместе с ручными строками он даёт группу «месяц + пустой лист» из ДВУХ
    # источников, и отчёт без гвардии объявил бы это двойной загрузкой.
    for number in range(manual and 3):
        row_id += 1
        conn.execute(
            'INSERT INTO drone_works (id, period_month, work_date_from, '
            'customer_raw, area_ha, amount, source_file, source_sheet, '
            "source_row) VALUES (?, '2025-09', '2025-09-10', ?, 4.0, "
            "800000, 'Без листа.xlsx', '', ?)",
            (row_id, 'Безлистовая %d' % number, number + 1))
    conn.commit()
    conn.close()


def scan(db, month=None):
    conn = tool.connect_ro(db)
    try:
        rows = tool.load(conn, month)
    finally:
        conn.close()
    return rows, tool.find_duplicates(rows)


class ImportDuplicatesTest(unittest.TestCase):

    def setUp(self):
        self.dir = tempfile.mkdtemp(prefix='impdup_')
        self.db = os.path.join(self.dir, 'transport.db')
        build_db(self.db)

    def test_the_double_upload_is_found(self):
        _rows, duplicates = scan(self.db)
        self.assertEqual({('2025-09', 'свод ичи (Мухриддин)'),
                          ('2025-09', 'свод ичи (Фурқат)')}, set(duplicates))

    def test_the_surplus_is_the_smaller_upload(self):
        """Полной считается загрузка с наибольшим числом строк."""
        _rows, duplicates = scan(self.db)
        items = tool.surplus(duplicates)
        self.assertEqual(2, len(items))
        self.assertEqual({STALE}, {i[2] for i in items})
        self.assertEqual(15, sum(i[3] for i in items))
        self.assertAlmostEqual(118.40, sum(i[4] for i in items), 2)

    def test_a_clean_month_is_silent(self):
        """Отрицательный контроль: отчёт, кричащий всегда, бесполезен."""
        os.remove(self.db)
        build_db(self.db, rows=tuple(r for r in ROWS if r[0] != STALE))
        _rows, duplicates = scan(self.db)
        self.assertEqual({}, duplicates)
        self.assertEqual([], tool.surplus(duplicates))

    def test_hand_typed_rows_are_not_a_double_upload(self):
        """[REASON]: у ручной строки нет ни файла, ни листа -- их много, и

        сложенные в одну группу они выглядели бы как загрузка из пустого
        файла. Тогда отчёт кричал бы на каждом месяце, где кто-то правил
        руками, и на него перестали бы смотреть.
        """
        os.remove(self.db)
        build_db(self.db, rows=tuple(r for r in ROWS if r[0] != STALE),
                 manual=5)
        _rows, duplicates = scan(self.db)
        self.assertEqual({}, duplicates)

    def test_the_same_sheet_in_another_month_is_not_a_duplicate(self):
        """Один лист в разных месяцах -- это разные книги, а не дубль."""
        os.remove(self.db)
        build_db(self.db, rows=(
            (GOOD, 'свод ичи (Мухриддин)', 11, 205.00, '2025-09'),
            ('Сервис Октябрь.xlsx', 'свод ичи (Мухриддин)', 9, 180.0,
             '2025-10')))
        _rows, duplicates = scan(self.db)
        self.assertEqual({}, duplicates)

    def test_month_filter_narrows_the_scan(self):
        os.remove(self.db)
        build_db(self.db, rows=ROWS + (
            ('Сервис Октябрь.xlsx', 'свод ичи (Мухриддин)', 9, 180.0,
             '2025-10'),
            ('Сервис Октябрь (2).xlsx', 'свод ичи (Мухриддин)', 4, 40.0,
             '2025-10')))
        _rows, all_months = scan(self.db)
        self.assertEqual(3, len(all_months))
        _rows, october = scan(self.db, '2025-10')
        self.assertEqual({('2025-10', 'свод ичи (Мухриддин)')}, set(october))

    def test_the_printed_sql_targets_only_the_surplus(self):
        """SQL обязан бить точно: файл, лист И месяц."""
        _rows, duplicates = scan(self.db)
        pairs = tool.sql_lines(tool.surplus(duplicates))
        self.assertEqual(2, len(pairs))
        for select, delete in pairs:
            self.assertTrue(select.startswith('SELECT'))
            self.assertTrue(delete.startswith('DELETE'))
            self.assertIn(STALE, delete)
            self.assertNotIn(GOOD, delete)
            self.assertIn("period_month) = '2025-09'", delete)
            self.assertIn('source_sheet', delete)

    def test_the_printed_sql_actually_removes_exactly_the_surplus(self):
        """Печатаемый SQL проверяется ИСПОЛНЕНИЕМ, а не чтением глазами."""
        _rows, duplicates = scan(self.db)
        pairs = tool.sql_lines(tool.surplus(duplicates))
        conn = sqlite3.connect(self.db)
        try:
            before = conn.execute('SELECT COUNT(*), ROUND(SUM(area_ha), 2) '
                                  'FROM drone_works').fetchone()
            for _select, delete in pairs:
                conn.execute(delete)
            conn.commit()
            after = conn.execute('SELECT COUNT(*), ROUND(SUM(area_ha), 2) '
                                 'FROM drone_works').fetchone()
            left = {r[0] for r in conn.execute(
                'SELECT DISTINCT source_file FROM drone_works')}
        finally:
            conn.close()
        self.assertEqual(before[0] - 15, after[0])
        self.assertAlmostEqual(before[1] - 118.40, after[1], 2)
        self.assertNotIn(STALE, left)
        self.assertIn(GOOD, left)

    def test_the_database_is_opened_read_only(self):
        conn = tool.connect_ro(self.db)
        try:
            with self.assertRaises(sqlite3.OperationalError):
                conn.execute('DELETE FROM drone_works')
        finally:
            conn.close()

    def test_the_tool_itself_never_writes(self):
        """[REASON]: read-only соединение защищает от ошибки, а не от замысла.

        Печатаемый DELETE -- это ТЕКСТ для владельца, он собирается в
        sql_lines и никогда не исполняется. Проверяется, что ни один
        литерал вне sql_lines не содержит пишущего слова.
        """
        path = os.path.join(REPO_ROOT, 'tools', 'drone_import_duplicates.py')
        with open(path, encoding='utf-8') as handle:
            tree = ast.parse(handle.read())
        allowed = {'sql_lines'}
        banned = ('insert into', 'update ', 'delete from', 'drop ', 'alter ')
        for node in ast.walk(tree):
            if not isinstance(node, ast.FunctionDef) or node.name in allowed:
                continue
            for child in ast.walk(node):
                if isinstance(child, ast.Constant) and \
                        isinstance(child.value, str):
                    lowered = child.value.lower()
                    for word in banned:
                        self.assertNotIn(
                            word, lowered,
                            'пишущее слово %r в %s: %r'
                            % (word, node.name, child.value[:60]))

    def test_exit_code_is_one_when_a_duplicate_is_found(self):
        import contextlib
        import io as _io
        buffer = _io.StringIO()
        argv = sys.argv
        sys.argv = ['drone_import_duplicates.py', '--db', self.db]
        try:
            with contextlib.redirect_stdout(buffer):
                code = tool.main()
        finally:
            sys.argv = argv
        printed = buffer.getvalue()
        printed.encode('ascii')          # консоль -- только ASCII
        self.assertEqual(1, code)
        self.assertIn('NOTHING WAS DELETED', printed)
        self.assertIn('Surplus hectares    : 118.40', printed)

    def test_the_sql_goes_to_a_utf8_file_not_the_console(self):
        """[REASON]: консоль Windows калечит кириллицу в «?????».

        Скопированный оттуда SQL не нашёл бы НИ ОДНОЙ строки и выглядел бы
        при этом безобидно -- «удалено 0 строк». Поэтому имена листов
        обязаны дойти до владельца файлом, а не через консоль.
        """
        import contextlib
        import io as _io
        buffer = _io.StringIO()
        argv = sys.argv
        sys.argv = ['drone_import_duplicates.py', '--db', self.db]
        try:
            with contextlib.redirect_stdout(buffer):
                tool.main()
        finally:
            sys.argv = argv
        printed = buffer.getvalue()
        printed.encode('ascii')
        self.assertNotIn('Мухриддин', printed)
        self.assertNotIn('DELETE FROM', printed)
        sql_path = os.path.join(self.dir, 'drone_import_duplicates.sql')
        self.assertTrue(os.path.isfile(sql_path))
        with open(sql_path, encoding='utf-8') as handle:
            sql = handle.read()
        self.assertIn('свод ичи (Мухриддин)', sql)
        self.assertIn(STALE, sql)
        self.assertNotIn(GOOD, sql)
        self.assertEqual(2, sql.count('SELECT id,'))
        self.assertEqual(2, sql.count('DELETE FROM'))

    def test_exit_code_is_zero_on_a_clean_base(self):
        os.remove(self.db)
        build_db(self.db, rows=tuple(r for r in ROWS if r[0] != STALE))
        import contextlib
        import io as _io
        buffer = _io.StringIO()
        argv = sys.argv
        sys.argv = ['drone_import_duplicates.py', '--db', self.db]
        try:
            with contextlib.redirect_stdout(buffer):
                code = tool.main()
        finally:
            sys.argv = argv
        self.assertEqual(0, code)
        self.assertIn('No book looks imported twice', buffer.getvalue())

    def test_missing_database_gives_code_two(self):
        argv = sys.argv
        sys.argv = ['drone_import_duplicates.py', '--db',
                    os.path.join(self.dir, 'nope.db')]
        try:
            self.assertEqual(2, tool.main())
        finally:
            sys.argv = argv


if __name__ == '__main__':
    unittest.main()
