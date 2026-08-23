# -*- coding: utf-8 -*-
"""DRONES_WORKS_OCT2025_USMON_ROW_FIX_001: строка со сбитыми колонками книги.

Строка опознаётся НЕ по пустой ставке и не по пустой дате -- таких в книгах
десятки, -- а по отпечатку самого дефекта: в `date_raw` лежит ставка
«178571.42857142855». Это и проверяется отрицательным контролем: соседняя
строка того же листа, у которой просто нет даты, чинить себя не даёт.

Run:
  python -m unittest tests.test_drone_works_oct2025_usmon_row_fix_001 -v
"""
import os
import sqlite3
import subprocess
import sys
import tempfile
import unittest

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SCRIPT = os.path.join(REPO_ROOT,
                      'migrate_drones_works_oct2025_usmon_row_fix_001.py')
sys.path.insert(0, REPO_ROOT)

import migrate_drones_works_oct2025_usmon_row_fix_001 as migration  # noqa

SCHEMA = """
CREATE TABLE drone_works (id INTEGER PRIMARY KEY AUTOINCREMENT,
  period_month TEXT NOT NULL, work_date_from TEXT, work_date_to TEXT,
  date_raw TEXT, customer_raw TEXT NOT NULL, area_ha NUMERIC,
  price_per_ha NUMERIC, amount NUMERIC, source_file TEXT, source_sheet TEXT,
  source_row INTEGER);
"""


def build_db(path, area=7.00, date_raw=migration.BROKEN_DATE_RAW,
             price=None, date_from=None, twins=0):
    conn = sqlite3.connect(path)
    conn.executescript(SCHEMA)
    row_id = 0

    def add(cust, area_ha, price_per_ha, amount, raw, dfrom, sfile, sheet,
            srow, period='2025-10'):
        nonlocal row_id
        row_id += 1
        conn.execute('INSERT INTO drone_works (id, period_month, '
                     'work_date_from, work_date_to, date_raw, customer_raw, '
                     'area_ha, price_per_ha, amount, source_file, '
                     'source_sheet, source_row) '
                     'VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)',
                     (row_id, period, dfrom, dfrom, raw, cust, area_ha,
                      price_per_ha, amount, sfile, sheet, srow))

    add('Амиршох Бехруз иқболи фх', area, price, 1250000.0, date_raw,
        date_from, migration.SOURCE_FILE, migration.SOURCE_SHEET,
        migration.SOURCE_ROW)
    # [REASON]: ОТРИЦАТЕЛЬНЫЙ КОНТРОЛЬ. Соседняя строка того же листа: тоже
    # без ставки, тоже без даты -- но date_raw у неё пуст, потому что
    # диспетчер просто не заполнил клетку. Опознание «по пустоте» починило бы
    # и её, поставив чужую ставку и чужую дату.
    add('Бухоро Элита уругчилик баракаси МЧЖ', 6.0, None, None, None, None,
        migration.SOURCE_FILE, migration.SOURCE_SHEET, 5)
    add('Ахад Салом фх', 19.0, 200000.0, 3800000.0, '2025-10-10 00:00:00',
        '2025-10-10', migration.SOURCE_FILE, 'свод ичи (Шахзод)', 5)
    for k in range(twins):
        add('Двойник', area, price, 1250000.0, date_raw, date_from,
            migration.SOURCE_FILE, migration.SOURCE_SHEET,
            migration.SOURCE_ROW)
    conn.commit()
    conn.close()


def run(db, *args):
    return subprocess.run([sys.executable, SCRIPT, '--db', db] + list(args),
                          capture_output=True, text=True)


def snapshot(db):
    conn = sqlite3.connect(db)
    try:
        return conn.execute('SELECT id, area_ha, price_per_ha, amount, '
                            'work_date_from, work_date_to, date_raw '
                            'FROM drone_works ORDER BY id').fetchall()
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


class UsmonRowFixTest(unittest.TestCase):

    def setUp(self):
        self.dir = tempfile.mkdtemp(prefix='usmon_')
        self.db = os.path.join(self.dir, 'transport.db')
        build_db(self.db)

    def test_dry_run_writes_nothing(self):
        before = snapshot(self.db)
        result = run(self.db)
        self.assertEqual(0, result.returncode, result.stdout + result.stderr)
        self.assertIn('DRY RUN', result.stdout)
        self.assertEqual(before, snapshot(self.db))
        self.assertEqual([], registry(self.db))

    def test_apply_sets_the_price_and_the_date(self):
        result = run(self.db, '--apply')
        self.assertEqual(0, result.returncode, result.stdout + result.stderr)
        row = snapshot(self.db)[0]
        self.assertAlmostEqual(migration.NEW_PRICE, float(row[2]), 5)
        self.assertEqual(migration.NEW_DATE, row[4])
        self.assertEqual(migration.NEW_DATE, row[5])
        self.assertEqual(migration.NEW_DATE_RAW, row[6])
        self.assertIn(migration.MIGRATION_ID, registry(self.db))

    def test_the_identity_holds_after_the_fix(self):
        run(self.db, '--apply')
        _id, area, price, amount = snapshot(self.db)[0][:4]
        self.assertAlmostEqual(float(amount), float(area) * float(price), 2)

    # ГЛАВНОЕ: соседняя строка без даты не трогается.
    def test_a_merely_undated_neighbour_is_not_touched(self):
        before = {r[0]: r for r in snapshot(self.db)}
        run(self.db, '--apply')
        after = {r[0]: r for r in snapshot(self.db)}
        touched = [i for i in before if before[i] != after[i]]
        self.assertEqual([1], touched)
        self.assertIsNone(after[2][2])
        self.assertIsNone(after[2][4])

    # ОТРИЦАТЕЛЬНЫЙ КОНТРОЛЬ на отпечаток: date_raw пуст -- отказ.
    def test_a_row_without_the_defect_fingerprint_is_refused(self):
        """[REASON]: пустые ставка и дата бывают у десятков честных строк.

        Ставка, лежащая в графе даты, бывает только у этой. Опознание «по
        пустоте» починило бы любую незаполненную строку.
        """
        os.remove(self.db)
        build_db(self.db, date_raw=None)
        before = snapshot(self.db)
        result = run(self.db, '--apply')
        self.assertEqual(1, result.returncode, result.stdout)
        self.assertIn('date_raw is', result.stdout)
        self.assertIn('fingerprint', result.stdout)
        self.assertEqual(before, snapshot(self.db))
        self.assertEqual([], registry(self.db))

    # ОТРИЦАТЕЛЬНЫЙ КОНТРОЛЬ: строку уже починили -- отказ, а не перезапись.
    def test_an_already_fixed_row_is_refused(self):
        os.remove(self.db)
        build_db(self.db, price=200000.0, date_from='2025-10-11',
                 date_raw='2025-10-11 00:00:00')
        before = snapshot(self.db)
        result = run(self.db, '--apply')
        self.assertEqual(1, result.returncode, result.stdout)
        self.assertIn('already set', result.stdout)
        self.assertEqual(before, snapshot(self.db))

    # ОТРИЦАТЕЛЬНЫЙ КОНТРОЛЬ на ставку ОТДЕЛЬНО от даты.
    def test_a_row_priced_by_hand_is_refused_even_while_still_undated(self):
        """[REASON]: изолированный контроль на «ставка уже стоит».

        Прежняя проверка строила базу, где стояли И ставка, И даты, И
        починенный date_raw -- отказ приходил от любой из трёх сетей, и
        снятие проверки ставки прошло бы незамеченным. Здесь ставку
        проставили руками на экране работ, а дату так и не поставили:
        сработать может ТОЛЬКО она.
        """
        os.remove(self.db)
        build_db(self.db, price=200000.0)
        before = snapshot(self.db)
        result = run(self.db, '--apply')
        self.assertEqual(1, result.returncode, result.stdout)
        self.assertIn('price is already set', result.stdout)
        self.assertEqual(before, snapshot(self.db))
        self.assertEqual([], registry(self.db))

    def test_a_different_area_is_refused(self):
        os.remove(self.db)
        build_db(self.db, area=9.0)
        before = snapshot(self.db)
        result = run(self.db, '--apply')
        self.assertEqual(1, result.returncode, result.stdout)
        self.assertIn('expected 7.00 ha, found 9.00', result.stdout)
        self.assertEqual(before, snapshot(self.db))

    def test_two_rows_with_the_same_provenance_are_refused(self):
        os.remove(self.db)
        build_db(self.db, twins=1)
        before = snapshot(self.db)
        result = run(self.db, '--apply')
        self.assertEqual(1, result.returncode, result.stdout)
        self.assertIn('found 2', result.stdout)
        self.assertEqual(before, snapshot(self.db))

    def test_second_apply_is_a_no_op(self):
        run(self.db, '--apply')
        after_first = snapshot(self.db)
        result = run(self.db, '--apply')
        self.assertEqual(0, result.returncode, result.stdout)
        self.assertIn('already applied', result.stdout)
        self.assertEqual(after_first, snapshot(self.db))

    def test_missing_database_gives_code_two_and_creates_nothing(self):
        absent = os.path.join(self.dir, 'nope.db')
        result = run(absent)
        self.assertEqual(2, result.returncode, result.stdout)
        self.assertFalse(os.path.exists(absent))

    # Постусловие: дата ДРУГОГО месяца унесла бы работу из октября.
    def test_the_postcondition_sees_the_row_leaving_october(self):
        """[REASON]: ровно так пропали 118.40 га -- работа ушла в чужой месяц

        и из отчёта исчезла молча.
        """
        conn = sqlite3.connect(self.db)
        try:
            before = migration.month_totals(conn)
            conn.execute("UPDATE drone_works SET price_per_ha = ?, "
                         "work_date_from = '2025-11-11', "
                         "work_date_to = '2025-11-11', date_raw = ? "
                         'WHERE id = 1', (migration.NEW_PRICE,
                                          migration.NEW_DATE_RAW))
            conn.commit()
            problems = migration.check_postcondition(conn, 1, before)
        finally:
            conn.close()
        self.assertTrue(any('row count moved' in p for p in problems),
                        problems)

    # Постусловие ловит РАЗЪЕХАВШУЮСЯ сумму отдельным сообщением.
    def test_the_postcondition_sees_the_identity_break(self):
        """[REASON]: строка выходит из корзины «проверить нечем» и обязана

        выйти в «сошлось». Ставка без согласованной суммы создала бы
        расхождение там, где его не было. Сверяется ИМЕННО сообщение про
        тождество: сдвиг суммы месяца ловится другой сетью, и без этой
        проверки текст «amount ... != ... x ...» исчез бы, а тест бы прошёл.
        """
        conn = sqlite3.connect(self.db)
        try:
            before = migration.month_totals(conn)
        finally:
            conn.close()
        run(self.db, '--apply')
        conn = sqlite3.connect(self.db)
        try:
            conn.execute('UPDATE drone_works SET amount = 999.0 WHERE id = 1')
            conn.commit()
            problems = migration.check_postcondition(conn, 1, before)
        finally:
            conn.close()
        self.assertTrue(any('!=' in p and 'x' in p for p in problems),
                        problems)

    def test_the_postcondition_is_silent_on_a_correct_apply(self):
        """Отрицательный контроль: сеть, срабатывающая всегда, бесполезна."""
        conn = sqlite3.connect(self.db)
        try:
            before = migration.month_totals(conn)
        finally:
            conn.close()
        run(self.db, '--apply')
        conn = sqlite3.connect(self.db)
        try:
            problems = migration.check_postcondition(conn, 1, before)
        finally:
            conn.close()
        self.assertEqual([], problems)

    def test_printed_rollback_restores_the_row(self):
        before = snapshot(self.db)
        result = run(self.db, '--apply')
        self.assertNotEqual(before, snapshot(self.db))
        statements, collecting = [], False
        for line in result.stdout.splitlines():
            if line.startswith('ROLLBACK OF DATA'):
                collecting = True
                continue
            if collecting and line.strip():
                statements.append(line.strip())
        conn = sqlite3.connect(self.db)
        try:
            for statement in statements:
                conn.execute(statement)
            conn.commit()
        finally:
            conn.close()
        self.assertEqual(before, snapshot(self.db))
        self.assertEqual([], registry(self.db))

    def test_the_console_never_leaks_cyrillic(self):
        result = run(self.db, '--apply')
        result.stdout.encode('ascii')
        self.assertIn('2025-10-11', result.stdout)


if __name__ == '__main__':
    unittest.main()
