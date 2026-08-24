# -*- coding: utf-8 -*-
"""DRONES_WORKS_OCT2025_OWNER_ROWS_001: три строки по решениям владельца.

Все три -- решения владельца, а не выводы из данных, поэтому проверяется и
то, что след источника помечен `[OWNER]`: месяц спустя разница между «вывели»
и «сказали» видна только по пометке.

Три вещи, ради которых этот файл написан:

1. **Строка ПЕРЕЕЗЖАЕТ, а не копируется.** Завести Холмуродову отдельную
   строку, оставив ту у Рухиллоева, значило бы посчитать одну работу дважды --
   и итог месяца это бы не заметил, он вырос бы «законно».
2. **Ставка справочной строки НЕ вписана числом**, а взята с существующей
   строки того же заказчика. Отрицательный контроль -- вторая база, где у
   строки-источника другая ставка: новая строка обязана получить именно её.
3. **Ставка наличной строки СВЕРЕНА**, а не принята: если наличные строки
   Хамроева стоят по другой цене, миграция отказывается.

Run:
  python -m unittest tests.test_drone_works_oct2025_owner_rows_001 -v
"""
import os
import sqlite3
import subprocess
import sys
import tempfile
import unittest

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SCRIPT = os.path.join(REPO_ROOT,
                      'migrate_drones_works_oct2025_owner_rows_001.py')
sys.path.insert(0, REPO_ROOT)

import migrate_drones_works_oct2025_owner_rows_001 as migration  # noqa

SCHEMA = """
CREATE TABLE drone_operators (id INTEGER PRIMARY KEY, full_name TEXT);
CREATE TABLE drone_works (id INTEGER PRIMARY KEY AUTOINCREMENT,
  period_month TEXT NOT NULL, work_date_from TEXT, work_date_to TEXT,
  date_raw TEXT, drone_operator_id INTEGER, operator_raw TEXT,
  customer_raw TEXT NOT NULL, area_ha NUMERIC, price_per_ha NUMERIC,
  amount NUMERIC, received_amount NUMERIC, received_kind TEXT,
  payment_type TEXT NOT NULL, subdivision_name TEXT, source_file TEXT,
  source_sheet TEXT, source_row INTEGER, note TEXT, created_at TEXT);
"""

OPERATORS = ('Рухиллоев Сайфулло', 'Холмуродов Шахзод', 'Хамроев Шохрух')
INTERNAL_RATE = 75040.0


def build_db(path, rate=INTERNAL_RATE, move_owner='Рухиллоев Сайфулло',
             hamroev_cash=(200000.0,), applied=migration.REQUIRES,
             move_amount=migration.MOVE_AMOUNT, marked=False):
    conn = sqlite3.connect(path)
    conn.executescript(SCHEMA)
    ids = {}
    for idx, name in enumerate(OPERATORS, 1):
        conn.execute('INSERT INTO drone_operators (id, full_name) '
                     'VALUES (?, ?)', (idx, name))
        ids[name] = idx
    row_id = 0

    def add(op, cust, area, price, amount, payment, sfile, sheet, srow,
            date='2025-10-10', sub=None, note=None):
        nonlocal row_id
        row_id += 1
        conn.execute(
            'INSERT INTO drone_works (id, period_month, work_date_from, '
            'work_date_to, drone_operator_id, customer_raw, area_ha, '
            'price_per_ha, amount, payment_type, subdivision_name, '
            'source_file, source_sheet, source_row, note) '
            'VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)',
            (row_id, '2025-10', date, date, ids.get(op), cust, area, price,
             amount, payment, sub, sfile, sheet, srow, note))

    # 1. Строка, которая должна переехать.
    add(move_owner, 'Пешку Сервис ери', migration.MOVE_AREA, 75040.0,
        move_amount, 'transfer', 'Дрон_маълумот_Достон_АКА.xlsx',
        migration.MOVE_SHEET, 42, date='2025-10-14',
        note=(migration.MARK + ': уже помечена') if marked else None)
    # Наличные строки Рухиллоева -- 178.90 га, их трогать нельзя.
    add(move_owner, 'Ахрор Али Асрор фх', 178.90, 200000.0, 35780000.0,
        'cash', 'Дрон_маълумот_Достон_АКА.xlsx', migration.MOVE_SHEET, 19)
    # 2. Строка-источник ставки -- ей цену проставила INTERNAL_PRICE.
    add('Холмуродов Шахзод', 'Бухоро Агрокластер Заминлари МЧЖ',
        migration.RATE_SOURCE_AREA, rate,
        None if rate is None else migration.RATE_SOURCE_AREA * rate,
        'transfer', migration.RATE_SOURCE_FILE, migration.RATE_SOURCE_SHEET,
        migration.RATE_SOURCE_ROW, sub='Ғиждувон ПТЗ')
    add('Холмуродов Шахзод', 'Ахад Салом фх', 19.00, 200000.0, 3800000.0,
        'cash', migration.RATE_SOURCE_FILE, migration.RATE_SOURCE_SHEET, 5,
        sub='Ғиждувон ПТЗ')
    # 3. Наличные строки Хамроева -- по ним сверяется ставка новой строки.
    per = 172.60 / max(len(hamroev_cash), 1)
    for price in hamroev_cash:
        add('Хамроев Шохрух', 'Когон фх', per, price, per * price, 'cash',
            'Когон ПТЗ Дрон маълумот.xlsx', 'свод ичи (Шохрух)', 5,
            sub='Когон ПТЗ')
    if applied:
        conn.execute('CREATE TABLE IF NOT EXISTS schema_migrations '
                     '(id INTEGER PRIMARY KEY AUTOINCREMENT, name TEXT, '
                     'description TEXT, checksum TEXT, applied_at TEXT)')
        for name in applied:
            conn.execute('INSERT INTO schema_migrations (name, description, '
                         "checksum, applied_at) VALUES (?, '', '', '')",
                         (name,))
    conn.commit()
    conn.close()


def run(db, *args):
    return subprocess.run([sys.executable, SCRIPT, '--db', db] + list(args),
                          capture_output=True, text=True)


def snapshot(db):
    conn = sqlite3.connect(db)
    try:
        return conn.execute('SELECT id, drone_operator_id, customer_raw, '
                            'area_ha, price_per_ha, amount, received_amount, '
                            'payment_type, note FROM drone_works ORDER BY id'
                            ).fetchall()
    finally:
        conn.close()


def hectares(db, name):
    conn = sqlite3.connect(db)
    try:
        return migration.operator_hectares(conn, name)
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


class OwnerRowsTest(unittest.TestCase):

    def setUp(self):
        self.dir = tempfile.mkdtemp(prefix='ownrows_')
        self.db = os.path.join(self.dir, 'transport.db')
        build_db(self.db)

    def test_dry_run_writes_nothing(self):
        before = snapshot(self.db)
        result = run(self.db)
        self.assertEqual(0, result.returncode, result.stdout + result.stderr)
        self.assertIn('DRY RUN', result.stdout)
        self.assertEqual(before, snapshot(self.db))
        self.assertNotIn(migration.MIGRATION_ID, registry(self.db))

    def test_apply_gives_the_promised_totals(self):
        result = run(self.db, '--apply')
        self.assertEqual(0, result.returncode, result.stdout + result.stderr)
        self.assertAlmostEqual(396.70, hectares(self.db, 'Холмуродов Шахзод'),
                               2)
        self.assertAlmostEqual(178.90, hectares(self.db,
                                                'Рухиллоев Сайфулло'), 2)
        self.assertAlmostEqual(196.60, hectares(self.db, 'Хамроев Шохрух'), 2)
        self.assertIn(migration.MIGRATION_ID, registry(self.db))

    # 1. ГЛАВНОЕ: строка ПЕРЕЕХАЛА, а не скопировалась.
    def test_the_row_moves_and_is_not_duplicated(self):
        """[REASON]: копия вместо переезда посчитала бы одну работу дважды,

        и итог месяца этого бы не заметил -- он вырос бы «законно» на 68 га.
        """
        before = snapshot(self.db)
        run(self.db, '--apply')
        after = snapshot(self.db)
        self.assertEqual(len(before) + 2, len(after))
        moved = [r for r in after if abs(float(r[3]) - 68.0) < 0.005]
        self.assertEqual(1, len(moved), 'строка на 68 га должна быть ОДНА')
        conn = sqlite3.connect(self.db)
        try:
            kh = migration.operator_id(conn, 'Холмуродов Шахзод')
        finally:
            conn.close()
        self.assertEqual(kh, moved[0][1])
        # Деньги едут вместе со строкой и не меняются.
        self.assertAlmostEqual(migration.MOVE_AMOUNT, float(moved[0][5]), 2)
        # customer_raw -- улика происхождения, её не переписывают.
        self.assertEqual('Пешку Сервис ери', moved[0][2])

    # 2. ГЛАВНОЕ: ставка справки ВЗЯТА, а не вписана.
    def test_the_transfer_rate_is_copied_from_the_source_row(self):
        """Отрицательный контроль -- вторая база с другой ставкой источника."""
        run(self.db, '--apply')
        new = [r for r in snapshot(self.db)
               if abs(float(r[3]) - 13.70) < 0.005]
        self.assertEqual(1, len(new))
        self.assertAlmostEqual(INTERNAL_RATE, float(new[0][4]), 2)
        self.assertAlmostEqual(13.70 * INTERNAL_RATE, float(new[0][5]), 2)

        other = os.path.join(self.dir, 'other.db')
        build_db(other, rate=85633.0)
        run(other, '--apply')
        new2 = [r for r in snapshot(other) if abs(float(r[3]) - 13.70) < 0.005]
        self.assertAlmostEqual(85633.0, float(new2[0][4]), 2)
        self.assertAlmostEqual(13.70 * 85633.0, float(new2[0][5]), 2)

    # 3. ГЛАВНОЕ: наличная ставка СВЕРЕНА, а не принята на веру.
    def test_a_different_cash_rate_is_refused(self):
        """[REASON]: 200 000 стоит в решении владельца, но проверяется по

        его же книгам. Если наличные строки Хамроева квотируют другое,
        подставлять 200 000 значило бы сочинить цену.
        """
        os.remove(self.db)
        build_db(self.db, hamroev_cash=(200000.0, 178571.43))
        before = snapshot(self.db)
        result = run(self.db, '--apply')
        self.assertEqual(1, result.returncode, result.stdout)
        self.assertIn('Hamroev cash rows quote', result.stdout)
        self.assertEqual(before, snapshot(self.db))
        self.assertNotIn(migration.MIGRATION_ID, registry(self.db))

    # 4. Наличная строка выходит «не собранной», а не расхождением.
    def test_the_cash_row_is_recorded_as_not_collected(self):
        run(self.db, '--apply')
        new = [r for r in snapshot(self.db)
               if abs(float(r[3]) - 24.00) < 0.005]
        self.assertEqual(1, len(new))
        self.assertAlmostEqual(0.0, float(new[0][6]), 6)
        self.assertEqual('cash', new[0][7])
        self.assertAlmostEqual(4800000.0, float(new[0][5]), 2)
        self.assertEqual('Радиан Жума', new[0][2])

    # 5. След источника -- [OWNER], а не [TELEMETRY].
    def test_every_touched_row_is_marked_as_the_owners_decision(self):
        run(self.db, '--apply')
        marked = [r for r in snapshot(self.db)
                  if r[8] and migration.MIGRATION_ID in r[8]]
        self.assertEqual(3, len(marked))
        for row in marked:
            self.assertIn('[OWNER]', row[8])
            self.assertNotIn('[TELEMETRY]', row[8])
            self.assertIn('2026-08-24', row[8])

    # 6. ПОРЯДОК назван словами.
    def test_it_refuses_until_the_required_migrations_are_applied(self):
        os.remove(self.db)
        build_db(self.db, applied=('DRONES_WORKS_OCT2025_OPERATOR_LINK_001',))
        before = snapshot(self.db)
        result = run(self.db, '--apply')
        self.assertEqual(1, result.returncode, result.stdout)
        self.assertIn('must be applied FIRST', result.stdout)
        self.assertIn('DRONES_WORKS_OCT2025_INTERNAL_PRICE_001',
                      result.stdout)
        self.assertEqual(before, snapshot(self.db))

    # 6. Источник ставки без цены -- отказ с указанием, что запустить.
    def test_a_rate_source_without_a_price_is_refused(self):
        os.remove(self.db)
        build_db(self.db, rate=None)
        result = run(self.db, '--apply')
        self.assertEqual(1, result.returncode, result.stdout)
        self.assertIn('has NO price yet', result.stdout)
        self.assertIn('INTERNAL_PRICE', result.stdout)

    # 7. Строка уже переехала -- отказ, а не второй переезд.
    def test_an_already_moved_row_is_refused(self):
        os.remove(self.db)
        build_db(self.db, move_owner='Холмуродов Шахзод')
        before = snapshot(self.db)
        result = run(self.db, '--apply')
        self.assertEqual(1, result.returncode, result.stdout)
        self.assertIn('does not belong to', result.stdout)
        self.assertEqual(before, snapshot(self.db))

    def test_a_wrong_amount_on_the_moved_row_is_refused(self):
        os.remove(self.db)
        build_db(self.db, move_amount=1.0)
        result = run(self.db, '--apply')
        self.assertEqual(1, result.returncode, result.stdout)
        self.assertIn('carries 1.00, expected', result.stdout)

    def test_rows_already_marked_are_refused(self):
        os.remove(self.db)
        build_db(self.db, marked=True)
        result = run(self.db, '--apply')
        self.assertEqual(1, result.returncode, result.stdout)
        self.assertIn('already carry this migration mark', result.stdout)

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

    # 8. Постусловие: тождество на новых строках.
    def test_the_postcondition_sees_a_broken_identity(self):
        conn = sqlite3.connect(self.db)
        try:
            before = migration.month_totals(conn)
        finally:
            conn.close()
        run(self.db, '--apply')
        conn = sqlite3.connect(self.db)
        try:
            conn.execute('UPDATE drone_works SET amount = 1.0 '
                         'WHERE ABS(area_ha - 13.70) < 0.005')
            conn.commit()
            problems = migration.check_postcondition(conn, before)
        finally:
            conn.close()
        self.assertTrue(any('amount = ha x rate' in p for p in problems),
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
            problems = migration.check_postcondition(conn, before)
        finally:
            conn.close()
        self.assertEqual([], problems)

    # 9. Напечатанный откат возвращает базу ЦЕЛИКОМ, включая заметку.
    def test_printed_rollback_restores_everything(self):
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
        self.assertNotIn(migration.MIGRATION_ID, registry(self.db))

    def test_the_console_never_leaks_cyrillic(self):
        result = run(self.db, '--apply')
        result.stdout.encode('ascii')
        self.assertIn('Kholmurodov', result.stdout)


if __name__ == '__main__':
    unittest.main()
