# -*- coding: utf-8 -*-
"""DRONE-APPLY-SQL-001: выполнить то, что владелец раскомментировал.

Ни sqlite3.exe, ни DB Browser на сервере нет, а вставить кириллический SQL в
PowerShell нельзя: консоль калечит имена листов в «?????», запрос не находит
ни строки и рапортует «удалено 0» -- выглядит благополучно, а не сделано
ничего. Именно это и случилось 2026-08-20: владелец остановил службу,
скопировал базу, запустил службу -- и удаление не выполнилось, потому что
выполнять было нечем.

Скрипт НИЧЕГО НЕ ВЫБИРАЕТ: он выполняет ровно те строки, которые человек
раскомментировал, и только по --apply. Проверяется и то, что он делает
обещанное, и -- главное -- чего он делать НЕ должен: белый список формы
запроса, отказ без копии базы, отказ при любом непонятном запросе.

Run:
  python -m unittest tests.test_drone_apply_sql_file_001 -v
"""
import os
import sqlite3
import sys
import tempfile
import unittest

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if REPO_ROOT not in sys.path:
    sys.path.insert(0, REPO_ROOT)
sys.path.insert(0, os.path.join(REPO_ROOT, 'tools'))

import drone_apply_sql_file as tool  # noqa: E402

SCHEMA = """
CREATE TABLE drone_works (id INTEGER PRIMARY KEY AUTOINCREMENT,
  period_month TEXT NOT NULL, work_date_from TEXT, customer_raw TEXT,
  area_ha NUMERIC, amount NUMERIC, source_file TEXT, source_sheet TEXT,
  source_row INTEGER);
"""

GOOD = 'Сервис Дрон Маълумот (2).xlsx'
STALE = 'Сервис Дрон Маълумот.xlsx'
SHEET = 'свод ичи (Мухриддин)'


def where(source_file, sheet=SHEET, month='2025-09'):
    return ("WHERE source_file = '%s' AND source_sheet = '%s' AND "
            "COALESCE(strftime('%%Y-%%m', work_date_from), period_month) "
            "= '%s'" % (source_file, sheet, month))


def build_db(path):
    conn = sqlite3.connect(path)
    conn.executescript(SCHEMA)
    row_id = 0
    for source_file, count, hectares in ((GOOD, 11, 205.00), (STALE, 6, 93.60)):
        per = hectares / count
        for number in range(count):
            row_id += 1
            conn.execute(
                'INSERT INTO drone_works (id, period_month, work_date_from, '
                'customer_raw, area_ha, amount, source_file, source_sheet, '
                "source_row) VALUES (?, '2025-09', '2025-09-10', ?, ?, ?, "
                '?, ?, ?)',
                (row_id, 'ФХ %d' % row_id, per, per * 200000, source_file,
                 SHEET, number + 4))
    conn.commit()
    conn.close()


def write_sql(path, uncommented=(STALE,), extra=''):
    lines = ['-- Сформировано tools/drone_import_duplicates.py', '']
    for source_file in (GOOD, STALE):
        lines.append('-- ВАРИАНТ: убрать %s' % source_file)
        lines.append('SELECT id, customer_raw FROM drone_works %s;'
                     % where(source_file))
        prefix = '' if source_file in uncommented else '-- '
        lines.append('%sDELETE FROM drone_works %s;'
                     % (prefix, where(source_file)))
        lines.append('')
    if extra:
        lines.append(extra)
    with open(path, 'w', encoding='utf-8') as handle:
        handle.write('\n'.join(lines))


class ApplySqlFileTest(unittest.TestCase):

    def setUp(self):
        self.dir = tempfile.mkdtemp(prefix='applysql_')
        self.db = os.path.join(self.dir, 'transport.db')
        self.sql = os.path.join(self.dir, 'drone_import_duplicates.sql')
        build_db(self.db)
        write_sql(self.sql)

    def backup(self):
        with open(self.db, 'rb') as src, \
                open(self.db + '.backup_20260820', 'wb') as dst:
            dst.write(src.read())

    def run_main(self, *extra):
        import contextlib
        import io
        buffer = io.StringIO()
        argv = sys.argv
        sys.argv = ['drone_apply_sql_file.py', '--db', self.db,
                    '--file', self.sql] + list(extra)
        try:
            with contextlib.redirect_stdout(buffer):
                code = tool.main()
        finally:
            sys.argv = argv
        return code, buffer.getvalue()

    def rows(self):
        conn = sqlite3.connect(self.db)
        try:
            return conn.execute('SELECT COUNT(*), ROUND(SUM(area_ha), 2) '
                                'FROM drone_works').fetchone()
        finally:
            conn.close()

    # Разбор: закомментированное не исполняется.
    def test_only_the_uncommented_delete_is_taken(self):
        with open(self.sql, encoding='utf-8') as handle:
            deletes, selects, refused = tool.classify(
                tool.statements(handle.read()))
        self.assertEqual([], refused)
        self.assertEqual(2, len(selects))
        self.assertEqual(1, len(deletes))
        self.assertIn(STALE, deletes[0])
        self.assertNotIn(GOOD, deletes[0])

    def test_a_commented_delete_glued_to_the_previous_line_is_not_run(self):
        """[REASON]: комментарий режется ПОСТРОЧНО, до склейки.

        Если срезать комментарии после склейки, «-- DELETE ...» приклеится к
        предыдущему запросу и уедет на исполнение вместе с ним.
        """
        with open(self.sql, encoding='utf-8') as handle:
            items = tool.statements(handle.read())
        for item in items:
            self.assertNotIn('--', item)

    # Сухой прогон: ничего не пишет, но показывает сколько уйдёт.
    def test_dry_run_writes_nothing_and_shows_the_count(self):
        before = self.rows()
        code, printed = self.run_main()
        self.assertEqual(0, code, printed)
        self.assertIn('would remove 6 row(s)', printed)
        self.assertIn('DRY RUN', printed)
        self.assertEqual(before, self.rows())
        printed.encode('ascii')

    # Боевой прогон убирает ровно то, что показал сухой.
    def test_apply_removes_exactly_the_uncommented_rows(self):
        self.backup()
        code, printed = self.run_main('--apply')
        self.assertEqual(0, code, printed)
        self.assertIn('APPLIED. 6 row(s) removed.', printed)
        count, hectares = self.rows()
        self.assertEqual(11, count)
        self.assertAlmostEqual(205.00, hectares, 2)
        conn = sqlite3.connect(self.db)
        try:
            left = {r[0] for r in conn.execute(
                'SELECT DISTINCT source_file FROM drone_works')}
        finally:
            conn.close()
        self.assertEqual({GOOD}, left)

    # Без копии базы -- отказ, даже если очень просят.
    def test_apply_without_a_backup_is_refused(self):
        """[REASON]: журнала загрузок с откатом нет, копия -- единственный

        путь назад. Отказ здесь дороже удобства.
        """
        before = self.rows()
        code, printed = self.run_main('--apply')
        self.assertEqual(1, code)
        self.assertIn('no backup found', printed)
        self.assertEqual(before, self.rows())

    # Белый список: любой другой запрос -- отказ, и НИЧЕГО не выполняется.
    def test_a_foreign_statement_refuses_everything(self):
        self.backup()
        write_sql(self.sql, extra='DELETE FROM drone_flights;')
        before = self.rows()
        code, printed = self.run_main('--apply')
        self.assertEqual(1, code)
        self.assertIn('REFUSING TO RUN', printed)
        self.assertEqual(before, self.rows())

    def test_a_delete_without_the_month_clause_is_refused(self):
        self.backup()
        write_sql(self.sql, uncommented=())
        with open(self.sql, 'a', encoding='utf-8') as handle:
            handle.write("\nDELETE FROM drone_works WHERE source_file = "
                         "'%s';\n" % STALE)
        before = self.rows()
        code, printed = self.run_main('--apply')
        self.assertEqual(1, code)
        self.assertIn('REFUSING TO RUN', printed)
        self.assertEqual(before, self.rows())

    def test_an_update_is_refused(self):
        self.backup()
        write_sql(self.sql, uncommented=(),
                  extra="UPDATE drone_works SET area_ha = 0;")
        before = self.rows()
        code, printed = self.run_main('--apply')
        self.assertEqual(1, code)
        self.assertIn('REFUSING TO RUN', printed)
        self.assertEqual(before, self.rows())

    # Ничего не раскомментировано -- скрипт это говорит, а не молчит.
    def test_nothing_uncommented_is_said_plainly(self):
        write_sql(self.sql, uncommented=())
        code, printed = self.run_main('--apply')
        self.assertEqual(0, code)
        self.assertIn('Nothing is uncommented', printed)
        self.assertEqual((17, 298.60), self.rows())

    # Отрицательный контроль на белый список: правильный запрос проходит.
    def test_the_allowed_shape_really_matches(self):
        self.assertTrue(tool.ALLOWED.match(
            'DELETE FROM drone_works ' + where(STALE)))
        self.assertFalse(tool.ALLOWED.match('DELETE FROM drone_flights'))
        self.assertFalse(tool.ALLOWED.match(
            'DELETE FROM drone_works WHERE 1=1'))

    def test_a_mismatch_between_plan_and_deed_rolls_everything_back(self):
        """Сеть на случай, если база изменилась между сухим и боевым.

        [REASON]: между прогонами кто-то может дописать или удалить строки
        через экран «Работы». Тогда боевой запрос заденет не то число строк,
        что показал сухой, и владелец увидит «удалено 6» там, где ушло 8.
        Проверяется подменой счётчика, потому что настоящую гонку в тесте не
        воспроизвести.
        """
        self.backup()
        before = self.rows()
        original = tool.count_targets
        tool.count_targets = lambda conn, sql: original(conn, sql) + 2
        try:
            code, printed = self.run_main('--apply')
        finally:
            tool.count_targets = original
        self.assertEqual(1, code)
        self.assertIn('ROLLED BACK', printed)
        self.assertEqual(before, self.rows())

    def test_missing_database_or_file_gives_code_two(self):
        argv = sys.argv
        sys.argv = ['drone_apply_sql_file.py', '--db',
                    os.path.join(self.dir, 'nope.db'), '--file', self.sql]
        try:
            self.assertEqual(2, tool.main())
        finally:
            sys.argv = argv
        sys.argv = ['drone_apply_sql_file.py', '--db', self.db, '--file',
                    os.path.join(self.dir, 'nope.sql')]
        try:
            self.assertEqual(2, tool.main())
        finally:
            sys.argv = argv

    def test_the_console_is_ascii(self):
        self.backup()
        _code, printed = self.run_main('--apply')
        printed.encode('ascii')
        self.assertNotIn('Мухриддин', printed)


if __name__ == '__main__':
    unittest.main()
