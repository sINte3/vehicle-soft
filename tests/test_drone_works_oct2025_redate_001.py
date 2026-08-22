# -*- coding: utf-8 -*-
"""DRONES_WORKS_OCT2025_REDATE_001: 118.40 га возвращаются в октябрь.

Пятнадцать наличных строк ОКТЯБРЬСКОЙ книги Сервиса (9 Жумаева на 24.80 га,
6 Кудратова на 93.60) числятся за сентябрём: даты у них нет, а period_month
партии загрузки был сентябрьский.

Проверяется ОБА состояния базы, потому что миграция обязана различать их сама:
  A -- строки на месте: перенос, ровно 15 строк, только period_month;
  B -- строки удалены (это и произошло на production 2026-08-20): отказ
       кодом 1 с порядком возврата через переимпорт, а НЕ тихий успех.

Отдельно -- что датированные строки ТОГО ЖЕ файла и ТОГО ЖЕ листа не тронуты:
у них period_month тоже сентябрьский, и без условия «даты нет» они попали бы
под ту же правку.

Run:
  python -m unittest tests.test_drone_works_oct2025_redate_001 -v
"""
import os
import sqlite3
import subprocess
import sys
import tempfile
import unittest

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SCRIPT = os.path.join(REPO_ROOT, 'migrate_drones_works_oct2025_redate_001.py')
sys.path.insert(0, REPO_ROOT)

import migrate_drones_works_oct2025_redate_001 as migration  # noqa

SCHEMA = """
CREATE TABLE drone_works (id INTEGER PRIMARY KEY AUTOINCREMENT,
  period_month TEXT NOT NULL, work_date_from TEXT, work_date_to TEXT,
  drone_operator_id INTEGER, customer_raw TEXT NOT NULL, area_ha NUMERIC,
  amount NUMERIC, source_file TEXT, source_sheet TEXT, source_row INTEGER);
"""

SERVIS = migration.SOURCE_FILE
# Настоящие площади наличных блоков октябрьской книги.
FURQAT = (1.9, 2.5, 3.6, 1.3, 3.0, 4.3, 1.5, 2.0, 4.7)
MUHRIDDIN = (22.2, 12.9, 8.0, 27.9, 8.6, 14.0)


def build_db(path, drop_undated=False, extra=(), keep=None):
    """keep -- сколько недатированных строк каждого листа оставить."""
    conn = sqlite3.connect(path)
    conn.executescript(SCHEMA)
    row_id = 0

    def add(source_file, sheet, source_row, area, date, period):
        nonlocal row_id
        row_id += 1
        conn.execute(
            'INSERT INTO drone_works (id, period_month, work_date_from, '
            'work_date_to, drone_operator_id, customer_raw, area_ha, amount, '
            'source_file, source_sheet, source_row) '
            'VALUES (?, ?, ?, ?, 1, ?, ?, ?, ?, ?, ?)',
            (row_id, period, date, date, 'ФХ %d' % row_id, area,
             area * 200000, source_file, sheet, source_row))

    if not drop_undated:
        for index, area in enumerate(FURQAT):
            if keep is not None and index >= keep:
                break
            add(SERVIS, 'свод ичи (Фурқат)', index + 4, area, None, '2025-09')
        for index, area in enumerate(MUHRIDDIN):
            if keep is not None and index >= keep:
                break
            add(SERVIS, 'свод ичи (Мухриддин)', index + 4, area, None,
                '2025-09')
    # [REASON]: ДАТИРОВАННЫЕ строки ТОГО ЖЕ файла и ТЕХ ЖЕ листов. period_month
    # у них тоже '2025-09' -- их спасает только условие «даты нет». В октябре
    # они уже лежат по своим датам, и переносить их не надо.
    add(SERVIS, 'свод ичи (Фурқат)', 17, 3.1, '2025-10-08', '2025-09')
    add(SERVIS, 'свод ичи (Фурқат)', 18, 19.2, '2025-10-10', '2025-09')
    add(SERVIS, 'свод ичи (Мухриддин)', 14, 3.4, '2025-10-10', '2025-09')
    # Двадцать датированных строк Имомова из той же книги -- уже в октябре.
    for number in range(20):
        add(SERVIS, 'свод ичи (Беҳзод)', number + 4, 191.70 / 20,
            '2025-10-1%d' % (number % 5), '2025-09')
    # Настоящая сентябрьская книга Сервиса -- ДРУГОЙ ФАЙЛ, тот же лист.
    for number in range(11):
        add('Сервис Дрон Маълумот (2).xlsx', 'свод ичи (Мухриддин)',
            number + 4, 205.00 / 11, '2025-09-1%d' % (number % 10), '2025-09')
    # [REASON]: и у НЕЁ есть строки без даты -- справки с перечислением дней,
    # которых читатель дат не разбирает (DRONE-BOOKS-DRIFT-001: 677.60 га
    # сентября лежат без даты). Лист тот же, месяц тот же, даты нет -- три
    # условия из четырёх совпадают, и отличает эти строки ТОЛЬКО имя файла.
    # Без них проверка на имя файла была бы одинаково зелёной с ним и без
    # него, а на живой базе перенос утащил бы в октябрь чужой сентябрь.
    for number in range(2):
        add('Сервис Дрон Маълумот (2).xlsx', 'свод ичи (Мухриддин)',
            number + 20, 15.00, None, '2025-09')
    for source_file, sheet, source_row, area, date, period in extra:
        add(source_file, sheet, source_row, area, date, period)
    conn.commit()
    conn.close()


def run(db, *args):
    return subprocess.run([sys.executable, SCRIPT, '--db', db] + list(args),
                          capture_output=True, text=True)


def snapshot(db):
    conn = sqlite3.connect(db)
    try:
        return conn.execute('SELECT id, period_month, work_date_from, '
                            'area_ha, amount, source_file, source_sheet '
                            'FROM drone_works ORDER BY id').fetchall()
    finally:
        conn.close()


def month(db, name):
    conn = sqlite3.connect(db)
    try:
        return conn.execute(
            'SELECT COUNT(*), ROUND(COALESCE(SUM(area_ha), 0), 2) '
            "FROM drone_works WHERE COALESCE(strftime('%Y-%m', "
            'work_date_from), period_month) = ?', (name,)).fetchone()
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


class RedateTest(unittest.TestCase):

    def setUp(self):
        self.dir = tempfile.mkdtemp(prefix='octredate_')
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

    # 1. Состояние A: перенос даёт ровно обещанные числа.
    def test_apply_moves_exactly_the_fifteen_rows(self):
        september_before = month(self.db, '2025-09')
        october_before = month(self.db, '2025-10')
        result = run(self.db, '--apply')
        self.assertEqual(0, result.returncode, result.stdout + result.stderr)
        self.assertIn('APPLIED', result.stdout)
        september = month(self.db, '2025-09')
        october = month(self.db, '2025-10')
        self.assertEqual(september_before[0] - 15, september[0])
        self.assertAlmostEqual(september_before[1] - 118.40, september[1], 2)
        self.assertEqual(october_before[0] + 15, october[0])
        self.assertAlmostEqual(october_before[1] + 118.40, october[1], 2)
        self.assertIn(migration.MIGRATION_ID, registry(self.db))

    # 1. ГЛАВНОЕ: датированные строки того же файла и листа не тронуты.
    def test_dated_rows_of_the_same_sheet_are_not_touched(self):
        """[REASON]: у них period_month тоже '2025-09'.

        Условие на файл и лист их НЕ отсекает, условие на месяц НЕ отсекает
        -- отсекает только «даты нет». Без него правка переписала бы 23
        лишние строки, месяц бы у них не изменился (COALESCE берёт дату), и
        расхождение никак бы себя не проявило, кроме как в откате.
        """
        before = {row[0]: row[1] for row in snapshot(self.db)}
        run(self.db, '--apply')
        after = {row[0]: row[1] for row in snapshot(self.db)}
        moved = [work_id for work_id in before
                 if before[work_id] != after[work_id]]
        self.assertEqual(15, len(moved))
        conn = sqlite3.connect(self.db)
        try:
            dated = [r[0] for r in conn.execute(
                'SELECT id FROM drone_works WHERE work_date_from IS NOT NULL')]
        finally:
            conn.close()
        self.assertEqual(34, len(dated))          # 3 справки + 20 + 11
        for work_id in dated:
            self.assertEqual(before[work_id], after[work_id])

    # 1. Настоящая сентябрьская книга Сервиса не тронута -- включая её
    #    НЕДАТИРОВАННЫЕ строки того же листа, которые отличает только файл.
    def test_the_real_september_book_is_left_alone(self):
        """[REASON]: изолированный контроль на имя файла.

        Две строки книги «(2)» лежат в том же листе, в том же месяце и тоже
        без даты. Ни лист, ни месяц, ни отсутствие даты их не отсекают --
        отсекает только источник. Убери его из условия, и 30.00 га чужого
        сентября уедут в октябрь вместе с нашими пятнадцатью.
        """
        before = {row[0]: row[1] for row in snapshot(self.db)}
        run(self.db, '--apply')
        conn = sqlite3.connect(self.db)
        try:
            rows = conn.execute(
                'SELECT COUNT(*), ROUND(SUM(area_ha), 2) FROM drone_works '
                "WHERE source_file = 'Сервис Дрон Маълумот (2).xlsx' "
                "AND period_month = '2025-09'").fetchone()
            undated = [r[0] for r in conn.execute(
                'SELECT id FROM drone_works WHERE source_file = '
                "'Сервис Дрон Маълумот (2).xlsx' AND work_date_from IS NULL")]
            after = {r[0]: r[1] for r in conn.execute(
                'SELECT id, period_month FROM drone_works')}
        finally:
            conn.close()
        self.assertEqual(13, rows[0])
        self.assertAlmostEqual(235.00, rows[1], 2)
        self.assertEqual(2, len(undated))
        for work_id in undated:
            self.assertEqual('2025-09', after[work_id])
            self.assertEqual(before[work_id], after[work_id])

    # 1. Деньги и площади остаются на своих строках.
    def test_only_the_month_changes(self):
        before = {row[0]: (row[2], row[3], row[4]) for row in snapshot(self.db)}
        run(self.db, '--apply')
        after = {row[0]: (row[2], row[3], row[4]) for row in snapshot(self.db)}
        self.assertEqual(before, after)

    # 2. Состояние B: строк нет -- отказ с порядком возврата, а не успех.
    def test_state_b_refuses_and_prints_the_reimport_order(self):
        """[REASON]: это и есть настоящее состояние production после

        2026-08-20. Тихий «нечего делать, код 0» оставил бы октябрь на
        1255.20 га и выглядел бы как успех.
        """
        os.remove(self.db)
        build_db(self.db, drop_undated=True)
        before = snapshot(self.db)
        result = run(self.db, '--apply')
        self.assertEqual(1, result.returncode, result.stdout)
        self.assertIn('PRECONDITION FAILED', result.stdout)
        self.assertIn('NOTHING TO RE-DATE', result.stdout)
        self.assertIn('15 new rows and 11 already present', result.stdout)
        self.assertIn('period 2025-10', result.stdout)
        self.assertEqual(before, snapshot(self.db))
        self.assertEqual([], registry(self.db))

    # 2. Отказ состояния B не притворяется «книги переимпортировали».
    def test_state_b_does_not_print_the_generic_refusal(self):
        """Отрицательный контроль к предыдущей: сообщение обязано РАЗЛИЧАТЬ

        «строк нет вовсе» и «строк не то число». Одно лечится переимпортом,
        другое -- разбором, и общий совет тут не помогает.
        """
        os.remove(self.db)
        build_db(self.db, drop_undated=True)
        absent = run(self.db, '--apply').stdout
        os.remove(self.db)
        build_db(self.db, keep=2)
        partial = run(self.db, '--apply').stdout
        self.assertIn('NOTHING TO RE-DATE', absent)
        self.assertNotIn('refusing to guess', absent)
        self.assertNotIn('NOTHING TO RE-DATE', partial)
        self.assertIn('refusing to guess', partial)
        self.assertIn('expected 9 / 24.80 ha, found 2 /', partial)

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

    # 5. Предусловие: лишняя недатированная строка -- отказ.
    def test_one_extra_undated_row_is_refused(self):
        os.remove(self.db)
        build_db(self.db, extra=((SERVIS, 'свод ичи (Фурқат)', 99, 5.0, None,
                                  '2025-09'),))
        before = snapshot(self.db)
        result = run(self.db, '--apply')
        self.assertEqual(1, result.returncode, result.stdout)
        self.assertIn('PRECONDITION FAILED', result.stdout)
        self.assertIn('expected 9 / 24.80 ha, found 10 / 29.80', result.stdout)
        self.assertEqual(before, snapshot(self.db))

    # 5. Предусловие ловит площадь ВНУТРИ группы, не только число строк.
    def test_hectares_moved_between_the_two_groups_are_refused(self):
        """[REASON]: строк по-прежнему 15, всего по-прежнему 118.40 -- общий

        итог сходится и молчит. Отличить такую базу может только сверка
        площади внутри каждой группы.
        """
        conn = sqlite3.connect(self.db)
        for sheet, delta in (('свод ичи (Фурқат)', 1.0),
                             ('свод ичи (Мухриддин)', -1.0)):
            conn.execute('UPDATE drone_works SET area_ha = area_ha + ? '
                         'WHERE id = (SELECT MIN(id) FROM drone_works '
                         'WHERE source_sheet = ? AND work_date_from IS NULL)',
                         (delta, sheet))
        conn.commit()
        conn.close()
        before = snapshot(self.db)
        result = run(self.db, '--apply')
        self.assertEqual(1, result.returncode, result.stdout)
        self.assertIn('expected 9 / 24.80 ha, found 9 / 25.80', result.stdout)
        self.assertNotIn('together: expected', result.stdout)
        self.assertEqual(before, snapshot(self.db))

    # 5. Постусловие -- вторая сеть, проверяется на испорченной базе.
    def test_the_postcondition_sees_work_appearing_out_of_nowhere(self):
        conn = sqlite3.connect(self.db)
        try:
            before_sep = migration.month_totals(conn, '2025-09')
            before_oct = migration.month_totals(conn, '2025-10')
            before_grand = migration.grand_totals(conn)
        finally:
            conn.close()
        run(self.db, '--apply')
        conn = sqlite3.connect(self.db)
        try:
            conn.execute("INSERT INTO drone_works (period_month, "
                         'work_date_from, work_date_to, drone_operator_id, '
                         'customer_raw, area_ha, amount, source_file, '
                         'source_sheet, source_row) '
                         "VALUES ('2025-10', NULL, NULL, 1, 'Лишняя фх', "
                         "7.0, 0, 'x.xlsx', 'x', 1)")
            conn.commit()
            problems = migration.check_postcondition(conn, before_sep,
                                                     before_oct, before_grand)
        finally:
            conn.close()
        self.assertTrue(any('total rows changed' in line
                            for line in problems), problems)

    def test_the_postcondition_sees_money_changing(self):
        conn = sqlite3.connect(self.db)
        try:
            before_sep = migration.month_totals(conn, '2025-09')
            before_oct = migration.month_totals(conn, '2025-10')
            before_grand = migration.grand_totals(conn)
        finally:
            conn.close()
        run(self.db, '--apply')
        conn = sqlite3.connect(self.db)
        try:
            conn.execute('UPDATE drone_works SET amount = amount + 1000 '
                         'WHERE id = (SELECT MIN(id) FROM drone_works)')
            conn.commit()
            problems = migration.check_postcondition(conn, before_sep,
                                                     before_oct, before_grand)
        finally:
            conn.close()
        self.assertTrue(any('total amount changed' in line
                            for line in problems), problems)

    def test_the_postcondition_is_silent_on_a_correct_apply(self):
        """Отрицательный контроль: сеть, срабатывающая всегда, бесполезна."""
        conn = sqlite3.connect(self.db)
        try:
            before_sep = migration.month_totals(conn, '2025-09')
            before_oct = migration.month_totals(conn, '2025-10')
            before_grand = migration.grand_totals(conn)
        finally:
            conn.close()
        run(self.db, '--apply')
        conn = sqlite3.connect(self.db)
        try:
            problems = migration.check_postcondition(conn, before_sep,
                                                     before_oct, before_grand)
        finally:
            conn.close()
        self.assertEqual([], problems)

    # 6. Напечатанный откат возвращает базу.
    def test_printed_rollback_restores_the_months(self):
        before = snapshot(self.db)
        result = run(self.db, '--apply')
        self.assertEqual(0, result.returncode, result.stdout)
        self.assertNotEqual(before, snapshot(self.db))
        statements, collecting = [], False
        for line in result.stdout.splitlines():
            if line.startswith('ROLLBACK OF DATA'):
                collecting = True
                continue
            if collecting and line.strip():
                statements.append(line.strip())
        self.assertTrue(statements)
        conn = sqlite3.connect(self.db)
        try:
            for statement in statements:
                conn.execute(statement)
            conn.commit()
        finally:
            conn.close()
        self.assertEqual(before, snapshot(self.db))
        self.assertEqual([], registry(self.db))

    # 7. Консоль -- только ASCII.
    def test_the_console_never_leaks_cyrillic(self):
        result = run(self.db, '--apply')
        result.stdout.encode('ascii')
        self.assertIn('1373.60', result.stdout)


if __name__ == '__main__':
    unittest.main()
