# -*- coding: utf-8 -*-
"""DRONE-IMPORT-DUP-001: поиск книг, загруженных дважды.

САМОЕ ВАЖНОЕ В ЭТОМ ФАЙЛЕ -- проверка на ЛИСТ-ТЁЗКУ. «свод ичи (Шохрух)»
есть и в книге Гардена (Файзуллаев Шохрух), и в книге Когона (Хамроев
Шохрух). Первая редакция отчёта объявила гарденскую книгу лишней и
предложила удалить 154.70 га живых работ; поймано боевым прогоном
2026-08-20, а не тестом. Теперь дубль от тёзки отличает ОПЕРАТОР.

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
CREATE TABLE drone_operators (id INTEGER PRIMARY KEY, full_name TEXT);
CREATE TABLE drone_works (id INTEGER PRIMARY KEY AUTOINCREMENT,
  period_month TEXT NOT NULL, work_date_from TEXT, customer_raw TEXT,
  area_ha NUMERIC, amount NUMERIC, drone_operator_id INTEGER,
  source_file TEXT, source_sheet TEXT, source_row INTEGER);
"""

OPERATORS = ('Қудратов Мухриддин', 'Жумаев Фуркат', 'Имомов Беҳзод',
             'Қодиров Нурали', 'Файзуллаев Шохрух', 'Хамроев Шохрух')

# Настоящая картина сентября: книга Сервиса загружена дважды.
GOOD = 'Сервис Дрон Маълумот (2).xlsx'
STALE = 'Сервис Дрон Маълумот.xlsx'
ROWS = (
    (GOOD, 'свод ичи (Мухриддин)', 11, 205.00, '2025-09', 'Қудратов Мухриддин'),
    (STALE, 'свод ичи (Мухриддин)', 6, 93.60, '2025-09', 'Қудратов Мухриддин'),
    (GOOD, 'свод ичи (Фурқат)', 37, 398.90, '2025-09', 'Жумаев Фуркат'),
    (STALE, 'свод ичи (Фурқат)', 9, 24.80, '2025-09', 'Жумаев Фуркат'),
    (GOOD, 'свод ичи (Беҳзод)', 38, 406.63, '2025-09', 'Имомов Беҳзод'),
    ('Гарден Агрокластер.xlsx', 'свод ичи (Нурали)', 57, 884.50, '2025-09',
     'Қодиров Нурали'),
    # [REASON]: НАСТОЯЩАЯ ЛОВУШКА, на которой первая редакция отчёта
    # предложила удалить 154.70 га живых работ. «свод ичи (Шохрух)» есть и в
    # книге Гардена, и в книге Когона -- это книги ДВУХ РАЗНЫХ ЛЮДЕЙ.
    ('Гарден_Агрокластер_Дрон_маълумот.xlsx', 'свод ичи (Шохрух)', 14, 154.70,
     '2025-09', 'Файзуллаев Шохрух'),
    ('Когон ПТЗ Дрон маълумот (2).xlsx', 'свод ичи (Шохрух)', 36, 412.60,
     '2025-09', 'Хамроев Шохрух'),
    # [REASON]: ВТОРАЯ ловушка, с боевого прогона 2026-08-20. Апрель 2026:
    # «свод ичи (Шахзод)» приходит из книги Ғиждувона (Холмуродов) и из книги
    # Сервиса, где оператор не привязан вовсе. Молчание -- не согласие:
    # признак «оператор тот же» тут не работает, и предлагать удаление
    # 492.15 га нельзя.
    ('Дрон Ғиждувон ПТЗ Апрель.xlsx', 'свод ичи (Шахзод)', 21, 361.30,
     '2026-04', 'Холмуродов Шахзод'),
    ('Сервис Дрон маълумот АПРЕЛЬ.xlsx', 'свод ичи (Шахзод)', 28, 492.15,
     '2026-04', None),
)


def build_db(path, rows=ROWS, manual=0):
    conn = sqlite3.connect(path)
    conn.executescript(SCHEMA)
    ids = {}
    for idx, name in enumerate(OPERATORS, 1):
        conn.execute('INSERT INTO drone_operators (id, full_name) '
                     'VALUES (?, ?)', (idx, name))
        ids[name] = idx
    row_id = 0
    for row in rows:
        source_file, sheet, count, hectares, month = row[:5]
        operator = row[5] if len(row) > 5 else None
        per = hectares / count
        for number in range(count):
            row_id += 1
            conn.execute(
                'INSERT INTO drone_works (id, period_month, work_date_from, '
                'customer_raw, area_ha, amount, drone_operator_id, '
                'source_file, source_sheet, source_row) '
                'VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)',
                (row_id, month, month + '-10', 'ФХ %d' % row_id, per,
                 per * 200000, ids.get(operator), source_file, sheet,
                 number + 4))
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

    # ГЛАВНОЕ: лист-тёзка -- НЕ дубль, и удалять по нему нечего.
    def test_a_namesake_sheet_is_not_a_duplicate(self):
        """Два разных человека под одним именем листа.

        [REASON]: без различения по оператору отчёт предложил бы удалить
        154.70 га книги Файзуллаева Шохруха как «лишнюю загрузку» книги
        Хамроева Шохруха. Это и случилось на production 2026-08-20.
        """
        _rows, duplicates = scan(self.db)
        key = ('2025-09', 'свод ичи (Шохрух)')
        self.assertIn(key, duplicates)
        self.assertEqual(tool.NAMESAKE, duplicates[key]['kind'])
        self.assertEqual(
            {'Файзуллаев Шохрух', 'Хамроев Шохрух'},
            {f[4] for f in duplicates[key]['files']})
        # Ни в кандидатах, ни в SQL его быть не должно.
        self.assertNotIn(key, {(i[0], i[1]) for i in
                               tool.candidates(duplicates)})
        self.assertEqual([key[0]], [t[0] for t in tool.namesakes(duplicates)])

    # ВТОРАЯ ловушка: сторона без оператора -- не согласие.
    def test_a_side_without_an_operator_is_undecidable(self):
        """[REASON]: молчание не есть согласие.

        Первая редакция считала «нет оператора» за «оператор тот же» и на
        апреле 2026 предложила удалить 492.15 га как дубль. Такие группы
        обязаны уходить в UNKNOWN, и SQL по ним не печатается.
        """
        _rows, duplicates = scan(self.db)
        key = ('2026-04', 'свод ичи (Шахзод)')
        self.assertEqual(tool.UNKNOWN, duplicates[key]['kind'])
        self.assertNotIn(key, {(i[0], i[1]) for i in
                               tool.candidates(duplicates)})
        self.assertEqual([key], [(m, sh) for m, sh, _f
                                 in tool.unknowns(duplicates)])

    def test_a_real_double_upload_is_a_candidate(self):
        _rows, duplicates = scan(self.db)
        for sheet in ('свод ичи (Мухриддин)', 'свод ичи (Фурқат)'):
            key = ('2025-09', sheet)
            self.assertEqual(tool.CANDIDATE, duplicates[key]['kind'], sheet)
            self.assertEqual(1, len({f[4] for f in duplicates[key]['files']}),
                             sheet)

    def test_the_undecidable_group_gets_no_sql_at_all(self):
        self._run_main()
        sql_path = os.path.join(self.dir, 'drone_import_duplicates.sql')
        with open(sql_path, encoding='utf-8') as handle:
            sql = handle.read()
        self.assertIn('НЕ ОПОЗНАНО', sql)
        self.assertIn('свод ичи (Шахзод)', sql)
        for line in sql.splitlines():
            if 'Шахзод' in line:
                self.assertTrue(line.strip().startswith('--'), line)
        self.assertNotIn('2026-04', ''.join(
            line for line in sql.splitlines()
            if line.startswith('SELECT') or line.startswith('-- DELETE')))

    def test_both_files_are_offered_never_one_verdict(self):
        """Отчёт НЕ решает, какая загрузка лишняя -- это говорит свод.

        [REASON]: первая редакция считала полной загрузку с наибольшим
        числом строк. На апреле 2026 это назвало бы лишним файл с 361.30 га
        в пользу файла с 492.15, а на мае -- файл с 548.10 га в пользу файла
        с 226.51. Число строк такого не решает.
        """
        _rows, duplicates = scan(self.db)
        items = tool.candidates(duplicates)
        self.assertEqual(4, len(items))          # два листа x два файла
        self.assertEqual({STALE, GOOD}, {i[2] for i in items})

    def test_a_clean_month_is_silent(self):
        """Отрицательный контроль: отчёт, кричащий всегда, бесполезен."""
        os.remove(self.db)
        build_db(self.db, rows=tuple(r for r in ROWS if r[0] != STALE
                                     and r[1] not in ('свод ичи (Шохрух)',
                                                      'свод ичи (Шахзод)')))
        _rows, duplicates = scan(self.db)
        self.assertEqual({}, duplicates)
        self.assertEqual([], tool.candidates(duplicates))

    def test_hand_typed_rows_are_not_a_double_upload(self):
        """[REASON]: у ручной строки нет ни файла, ни листа -- их много, и

        сложенные в одну группу они выглядели бы как загрузка из пустого
        файла. Тогда отчёт кричал бы на каждом месяце, где кто-то правил
        руками, и на него перестали бы смотреть.
        """
        os.remove(self.db)
        build_db(self.db, rows=tuple(r for r in ROWS if r[0] != STALE
                                     and r[1] not in ('свод ичи (Шохрух)',
                                                      'свод ичи (Шахзод)')),
                 manual=5)
        _rows, duplicates = scan(self.db)
        self.assertEqual({}, duplicates)

    def test_the_same_sheet_in_another_month_is_not_a_duplicate(self):
        os.remove(self.db)
        build_db(self.db, rows=(
            (GOOD, 'свод ичи (Мухриддин)', 11, 205.00, '2025-09',
             'Қудратов Мухриддин'),
            ('Сервис Октябрь.xlsx', 'свод ичи (Мухриддин)', 9, 180.0,
             '2025-10', 'Қудратов Мухриддин')))
        _rows, duplicates = scan(self.db)
        self.assertEqual({}, duplicates)

    def test_month_filter_narrows_the_scan(self):
        _rows, all_months = scan(self.db)
        self.assertEqual(4, len(all_months))
        _rows, september = scan(self.db, '2025-09')
        self.assertEqual(3, len(september))
        _rows, april = scan(self.db, '2026-04')
        self.assertEqual(1, len(april))

    def test_the_printed_sql_targets_one_file_one_sheet_one_month(self):
        _rows, duplicates = scan(self.db)
        pairs = tool.sql_lines(tool.candidates(duplicates))
        self.assertEqual(4, len(pairs))
        for select, delete in pairs:
            self.assertTrue(select.startswith('SELECT'))
            self.assertTrue(delete.startswith('DELETE'))
            self.assertIn("period_month) = '2025-09'", delete)
            self.assertIn('source_sheet', delete)
            self.assertIn('source_file', delete)
            self.assertNotIn('Шохрух', delete)

    def test_the_chosen_delete_removes_exactly_that_upload(self):
        """Печатаемый SQL проверяется ИСПОЛНЕНИЕМ, а не чтением глазами."""
        _rows, duplicates = scan(self.db)
        stale = [i for i in tool.candidates(duplicates) if i[2] == STALE]
        pairs = tool.sql_lines(stale)
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
        # Книга Файзуллаева Шохруха не тронута.
        self.assertIn('Гарден_Агрокластер_Дрон_маълумот.xlsx', left)

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
        sql_lines и никогда не исполняется.
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

    def _run_main(self):
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
        return code, buffer.getvalue()

    def test_exit_code_is_one_when_a_duplicate_is_found(self):
        code, printed = self._run_main()
        printed.encode('ascii')          # консоль -- только ASCII
        self.assertEqual(1, code)
        self.assertIn('NOTHING WAS DELETED', printed)
        self.assertIn('DOES NOT DECIDE', printed)
        self.assertIn('Namesake sheets     : 1', printed)
        self.assertIn('Undecidable         : 1', printed)

    def test_the_sql_file_marks_the_namesake_and_comments_every_delete(self):
        """Файл обязан и предупредить про тёзку, и не дать удалить сгоряча.

        [REASON]: DELETE выписывается ЗАКОММЕНТИРОВАННЫМ. Файл, который
        можно выполнить целиком одним махом, снёс бы обе стороны каждого
        кандидата -- то есть и верную загрузку тоже.
        """
        self._run_main()
        sql_path = os.path.join(self.dir, 'drone_import_duplicates.sql')
        with open(sql_path, encoding='utf-8') as handle:
            sql = handle.read()
        self.assertIn('ТЁЗКИ, НЕ ДУБЛЬ. Ничего не удалять.', sql)
        self.assertIn('свод ичи (Шохрух)', sql)
        # Ни одного DELETE по листу-тёзке.
        for line in sql.splitlines():
            if 'DELETE' in line:
                self.assertTrue(line.strip().startswith('--'), line)
                self.assertNotIn('Шохрух', line)
        self.assertEqual(4, sql.count('SELECT id,'))
        self.assertEqual(4, sql.count('-- DELETE FROM'))

    def test_the_console_never_leaks_cyrillic(self):
        _code, printed = self._run_main()
        printed.encode('ascii')
        self.assertNotIn('DELETE FROM', printed)

    def test_exit_code_is_zero_on_a_clean_base(self):
        os.remove(self.db)
        build_db(self.db, rows=tuple(r for r in ROWS if r[0] != STALE
                                     and r[1] != 'свод ичи (Шохрух)'))
        os.remove(self.db)
        build_db(self.db, rows=tuple(r for r in ROWS if r[0] != STALE
                                     and r[1] not in ('свод ичи (Шохрух)',
                                                      'свод ичи (Шахзод)')))
        code, printed = self._run_main()
        self.assertEqual(0, code)
        self.assertIn('No book looks imported twice', printed)

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
