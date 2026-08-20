# -*- coding: utf-8 -*-
"""DRONES_WORKS_SEPT2025_OPERATOR_LINK_001: привязка книг к операторам.

Миграция привязывает 103 строки на 1 130.60 га к их операторам по
листу-источнику и возвращает 13 строк (132.70 га) от Кодирова Жураеву.

Главный риск здесь -- ЛИСТ-ТЁЗКА: «свод ичи (Шохрух)» есть и в книге Гардена
(Файзуллаев Шохрух), и в книге Когона (Хамроев Шохрух). Правка по имени листа
без условия «оператора нет» перебросила бы строки Хамроева Файзуллаеву, и в
диффе это выглядело бы совершенно нормально.

Четыре пути устава и шесть отрицательных контролей. Проверка, дающая
одинаковый результат на верной и на испорченной базе, проверкой не является.

Run:
  python -m unittest tests.test_drone_works_sept2025_operator_link_001 -v
"""
import os
import sqlite3
import subprocess
import sys
import tempfile
import unittest

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SCRIPT = os.path.join(REPO_ROOT,
                      'migrate_drones_works_sept2025_operator_link_001.py')
sys.path.insert(0, REPO_ROOT)

import migrate_drones_works_sept2025_operator_link_001 as migration  # noqa

SCHEMA = """
CREATE TABLE drone_operators (id INTEGER PRIMARY KEY, full_name TEXT);
CREATE TABLE drone_works (id INTEGER PRIMARY KEY AUTOINCREMENT,
  period_month TEXT NOT NULL, work_date_from TEXT, work_date_to TEXT,
  drone_operator_id INTEGER, customer_raw TEXT NOT NULL, area_ha NUMERIC,
  amount NUMERIC, source_sheet TEXT, source_row INTEGER);
"""

OPERATORS = ('Ибодуллаев Хасанбой', 'Холмуродов Шахзод', 'Файзуллаев Шоди',
             'Файзуллаев Шохрух', 'Анваров Усмон', 'Қодиров Нурали',
             'Жураев Туйғун', 'Хамроев Шохрух')


def build_db(path, extra_rows=(), skip=()):
    conn = sqlite3.connect(path)
    conn.executescript(SCHEMA)
    for idx, name in enumerate(OPERATORS, 1):
        conn.execute('INSERT INTO drone_operators (id, full_name) '
                     'VALUES (?, ?)', (idx, name))
    ids = {name: idx for idx, name in enumerate(OPERATORS, 1)}
    row_id = 0

    def add(sheet, operator, count, hectares, month='2025-09'):
        nonlocal row_id
        per = hectares / count
        for number in range(count):
            row_id += 1
            conn.execute(
                'INSERT INTO drone_works (id, period_month, work_date_from, '
                'work_date_to, drone_operator_id, customer_raw, area_ha, '
                'amount, source_sheet, source_row) '
                'VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)',
                (row_id, month, month + '-10', month + '-10',
                 ids.get(operator), 'ФХ %d' % row_id, per, per * 200000,
                 sheet, number + 4))

    # Пять групп без оператора -- ровно то, что снято с production.
    for sheet, name, count, hectares in migration.LINKS:
        if sheet in skip:
            continue
        add(sheet, None, count, hectares)
    # [REASON]: В ТЕХ ЖЕ ЛИСТАХ у операторов уже есть привязанные строки --
    # справочные. На production это 587.50 га Холмуродова в «свод ичи
    # (Шахзод)» и 90.60 га Анварова в «свод ичи (Усмон) ». Без них откат
    # «верни всех этого оператора в этом листе в NULL» выглядит правильным,
    # а на живой базе обнулил бы и их.
    add('свод ичи (Шахзод)', 'Холмуродов Шахзод', 4, 587.50)
    add('свод ичи (Усмон) ', 'Анваров Усмон', 7, 90.60)
    # 13 строк Жураева, записанные на Кодирова, и одна его собственная.
    add(migration.REASSIGN_SHEET, 'Қодиров Нурали', migration.REASSIGN_ROWS,
        migration.REASSIGN_HA)
    add(migration.REASSIGN_SHEET, 'Жураев Туйғун', 1, 67.0)
    # [REASON]: ЛИСТ-ТЁЗКА. В книге Когона тот же «свод ичи (Шохрух)», но это
    # Хамроев, и его строки уже привязаны. Правка по имени листа без условия
    # «оператора нет» уведёт их Файзуллаеву.
    add('свод ичи (Шохрух)', 'Хамроев Шохрух', 36, 412.60)
    # Соседи, которых трогать нельзя вовсе.
    add('свод ичи (Нурали)', 'Қодиров Нурали', 55, 560.20)
    add('свод ичи (Хасан)', None, 5, 40.0, month='2025-10')
    for sheet, operator, count, hectares in extra_rows:
        add(sheet, operator, count, hectares)
    conn.commit()
    conn.close()


def run(db, *extra):
    return subprocess.run([sys.executable, SCRIPT, '--db', db] + list(extra),
                          capture_output=True, text=True)


def by_operator(db, month='2025-09'):
    conn = sqlite3.connect(db)
    try:
        return dict(conn.execute(
            "SELECT COALESCE(o.full_name, ''), ROUND(SUM(w.area_ha), 2) "
            'FROM drone_works w '
            'LEFT JOIN drone_operators o ON o.id = w.drone_operator_id '
            "WHERE COALESCE(strftime('%Y-%m', w.work_date_from), "
            '      w.period_month) = ? GROUP BY 1', (month,)).fetchall())
    finally:
        conn.close()


def snapshot(db):
    conn = sqlite3.connect(db)
    try:
        return conn.execute('SELECT id, drone_operator_id, area_ha, amount, '
                            'source_sheet FROM drone_works ORDER BY id'
                            ).fetchall()
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


class OperatorLinkTest(unittest.TestCase):

    def setUp(self):
        self.dir = tempfile.mkdtemp(prefix='oplink_')
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

    # 1. Боевой прогон даёт ровно обещанные числа.
    def test_apply_gives_the_promised_totals(self):
        result = run(self.db, '--apply')
        self.assertEqual(0, result.returncode, result.stdout + result.stderr)
        self.assertIn('APPLIED', result.stdout)
        totals = by_operator(self.db)
        self.assertAlmostEqual(427.20, totals['Ибодуллаев Хасанбой'], 2)
        self.assertAlmostEqual(879.50, totals['Холмуродов Шахзод'], 2)
        self.assertAlmostEqual(174.50, totals['Файзуллаев Шоди'], 2)
        self.assertAlmostEqual(154.70 + 412.60,
                               totals['Файзуллаев Шохрух']
                               + totals['Хамроев Шохрух'], 2)
        self.assertAlmostEqual(172.80, totals['Анваров Усмон'], 2)
        self.assertAlmostEqual(199.70, totals['Жураев Туйғун'], 2)
        self.assertAlmostEqual(560.20, totals['Қодиров Нурали'], 2)
        self.assertNotIn('', totals)          # ни одной строки без оператора
        self.assertIn(migration.MIGRATION_ID, registry(self.db))

    # 1. ЛИСТ-ТЁЗКА: строки Хамроева остаются его.
    def test_the_namesake_sheet_of_another_book_is_not_touched(self):
        """[REASON]: «свод ичи (Шохрух)» есть и у Гардена, и у Когона.

        Без условия «оператора нет» 36 строк Хамроева уехали бы Файзуллаеву,
        и в диффе это выглядело бы совершенно нормально.
        """
        before = {row[0]: row[1] for row in snapshot(self.db)}
        run(self.db, '--apply')
        after = {row[0]: row[1] for row in snapshot(self.db)}
        conn = sqlite3.connect(self.db)
        try:
            hamroev = conn.execute(
                'SELECT id FROM drone_works WHERE drone_operator_id = '
                "(SELECT id FROM drone_operators WHERE full_name = "
                "'Хамроев Шохрух')").fetchall()
        finally:
            conn.close()
        self.assertEqual(36, len(hamroev))
        for (work_id,) in hamroev:
            self.assertEqual(before[work_id], after[work_id])
        totals = by_operator(self.db)
        self.assertAlmostEqual(412.60, totals['Хамроев Шохрух'], 2)
        self.assertAlmostEqual(154.70, totals['Файзуллаев Шохрух'], 2)

    # 1. Другой месяц не трогается.
    def test_another_month_is_left_alone(self):
        run(self.db, '--apply')
        october = by_operator(self.db, '2025-10')
        self.assertAlmostEqual(40.0, october[''], 2)

    # 1. Деньги остаются на своих строках.
    def test_money_does_not_move(self):
        before = {row[0]: (row[2], row[3]) for row in snapshot(self.db)}
        run(self.db, '--apply')
        after = {row[0]: (row[2], row[3]) for row in snapshot(self.db)}
        self.assertEqual(before, after)

    # 2. Повтор ничего не делает.
    def test_second_apply_is_a_no_op(self):
        run(self.db, '--apply')
        after_first = snapshot(self.db)
        result = run(self.db, '--apply')
        self.assertEqual(0, result.returncode, result.stdout)
        self.assertIn('already applied', result.stdout)
        self.assertEqual(after_first, snapshot(self.db))

    # 3. Базы нет: код 2, файл НЕ создан.
    def test_missing_database_gives_code_two_and_creates_nothing(self):
        absent = os.path.join(self.dir, 'nope.db')
        result = run(absent)
        self.assertEqual(2, result.returncode, result.stdout)
        self.assertFalse(os.path.exists(absent))

    # 4. Предусловие: лишняя строка без оператора -- отказ.
    def test_one_extra_orphan_row_is_refused(self):
        os.remove(self.db)
        build_db(self.db, extra_rows=(('свод ичи (Хасан)', None, 1, 5.0),))
        before = snapshot(self.db)
        result = run(self.db, '--apply')
        self.assertEqual(1, result.returncode, result.stdout)
        self.assertIn('PRECONDITION FAILED', result.stdout)
        self.assertEqual(before, snapshot(self.db))
        self.assertEqual([], registry(self.db))

    # 4. Предусловие: пропавшая группа -- отказ.
    def test_a_missing_group_is_refused(self):
        os.remove(self.db)
        build_db(self.db, skip=('свод ичи (Шоди)',))
        before = snapshot(self.db)
        result = run(self.db, '--apply')
        self.assertEqual(1, result.returncode, result.stdout)
        self.assertIn('PRECONDITION FAILED', result.stdout)
        # Отказ обязан назвать и общее число, и конкретную группу.
        self.assertIn('rows with no operator: expected 103', result.stdout)
        self.assertIn('expected 17 / 174.50 ha, found 0 / 0.00',
                      result.stdout)
        self.assertEqual(before, snapshot(self.db))

    # 4. Применённое состояние второй раз предусловие не проходит.
    def test_applied_state_no_longer_matches_the_precondition(self):
        run(self.db, '--apply')
        conn = sqlite3.connect(self.db)
        conn.execute('DELETE FROM schema_migrations')
        conn.commit()
        conn.close()
        before = snapshot(self.db)
        result = run(self.db, '--apply')
        self.assertEqual(1, result.returncode, result.stdout)
        self.assertIn('PRECONDITION FAILED', result.stdout)
        self.assertEqual(before, snapshot(self.db))

    # 5. Предусловие ловит и площадь, а не только число строк.
    def test_the_same_row_count_with_wrong_hectares_is_refused(self):
        """[REASON]: сверка только по числу строк прошла бы на любых данных."""
        conn = sqlite3.connect(self.db)
        conn.execute("UPDATE drone_works SET area_ha = area_ha + 1 "
                     "WHERE source_sheet = 'свод ичи (Шоди)' "
                     'AND drone_operator_id IS NULL AND id = '
                     "(SELECT MIN(id) FROM drone_works WHERE source_sheet = "
                     "'свод ичи (Шоди)')")
        conn.commit()
        conn.close()
        before = snapshot(self.db)
        result = run(self.db, '--apply')
        self.assertEqual(1, result.returncode, result.stdout)
        self.assertIn('PRECONDITION FAILED', result.stdout)
        self.assertEqual(before, snapshot(self.db))

    # 5. Перенос Жураеву проверяется отдельно.
    def test_the_reassignment_needs_its_own_rows(self):
        conn = sqlite3.connect(self.db)
        conn.execute("UPDATE drone_works SET drone_operator_id = "
                     "(SELECT id FROM drone_operators WHERE full_name = "
                     "'Жураев Туйғун') WHERE source_sheet = ? AND "
                     'drone_operator_id = (SELECT id FROM drone_operators '
                     "WHERE full_name = 'Қодиров Нурали') LIMIT 1",
                     (migration.REASSIGN_SHEET,))
        conn.commit()
        conn.close()
        before = snapshot(self.db)
        result = run(self.db, '--apply')
        self.assertEqual(1, result.returncode, result.stdout)
        self.assertIn('PRECONDITION FAILED', result.stdout)
        self.assertEqual(before, snapshot(self.db))

    # 5. Постусловие -- вторая сеть. Проверяется ПРЯМО, на испорченной базе.
    def test_the_postcondition_sees_a_row_left_without_an_operator(self):
        """[REASON]: сеть должна РАБОТАТЬ, а не просто быть написанной."""
        run(self.db, '--apply')
        conn = sqlite3.connect(self.db)
        try:
            conn.execute('UPDATE drone_works SET drone_operator_id = NULL '
                         "WHERE source_sheet = 'свод ичи (Шоди)' "
                         'AND id = (SELECT MIN(id) FROM drone_works '
                         "WHERE source_sheet = 'свод ичи (Шоди)')")
            conn.commit()
            before = {migration.normalize(n): 0.0 for n in
                      [x[1] for x in migration.LINKS]
                      + [migration.REASSIGN_TO, migration.REASSIGN_FROM]}
            problems = migration.check_postcondition(
                conn, before, migration.month_totals(conn))
        finally:
            conn.close()
        self.assertTrue(any('still have no operator' in line
                            for line in problems), problems)

    def test_the_postcondition_sees_money_moving(self):
        run(self.db, '--apply')
        conn = sqlite3.connect(self.db)
        try:
            before_month = migration.month_totals(conn)
            before = {migration.normalize(n): migration.operator_hectares(
                conn, n) for n in [x[1] for x in migration.LINKS]
                + [migration.REASSIGN_TO, migration.REASSIGN_FROM]}
            conn.execute('UPDATE drone_works SET amount = amount + 1000 '
                         'WHERE id = (SELECT MIN(id) FROM drone_works)')
            conn.commit()
            problems = migration.check_postcondition(conn, before,
                                                     before_month)
        finally:
            conn.close()
        self.assertTrue(any('month amount moved' in line
                            for line in problems), problems)

    def test_the_postcondition_sees_hectares_moving(self):
        conn = sqlite3.connect(self.db)
        try:
            before_month = migration.month_totals(conn)
            before = {migration.normalize(n): migration.operator_hectares(
                conn, n) for n in [x[1] for x in migration.LINKS]
                + [migration.REASSIGN_TO, migration.REASSIGN_FROM]}
        finally:
            conn.close()
        run(self.db, '--apply')
        conn = sqlite3.connect(self.db)
        try:
            conn.execute("INSERT INTO drone_works (period_month, "
                         'work_date_from, work_date_to, drone_operator_id, '
                         'customer_raw, area_ha, amount, source_sheet, '
                         "source_row) VALUES ('2025-09', '2025-09-10', "
                         "'2025-09-10', 1, 'Лишняя фх', 9.0, 0, 'x', 1)")
            conn.commit()
            problems = migration.check_postcondition(conn, before,
                                                     before_month)
        finally:
            conn.close()
        self.assertTrue(any('month hectares moved' in line
                            for line in problems), problems)

    def test_the_postcondition_sees_a_wrong_operator_delta(self):
        """Дельта каждого оператора сверяется отдельно, а не только итог."""
        conn = sqlite3.connect(self.db)
        try:
            before_month = migration.month_totals(conn)
            before = {migration.normalize(n): migration.operator_hectares(
                conn, n) for n in [x[1] for x in migration.LINKS]
                + [migration.REASSIGN_TO, migration.REASSIGN_FROM]}
        finally:
            conn.close()
        run(self.db, '--apply')
        conn = sqlite3.connect(self.db)
        try:
            # Строка Шоди уходит Анварову: итог месяца тот же, дельты -- нет.
            conn.execute("UPDATE drone_works SET drone_operator_id = "
                         "(SELECT id FROM drone_operators WHERE full_name = "
                         "'Анваров Усмон') WHERE source_sheet = "
                         "'свод ичи (Шоди)' AND id = (SELECT MIN(id) FROM "
                         "drone_works WHERE source_sheet = 'свод ичи (Шоди)')")
            conn.commit()
            problems = migration.check_postcondition(conn, before,
                                                     before_month)
        finally:
            conn.close()
        self.assertTrue(any('Fayzullaev' in line or 'Anvarov' in line
                            or '?' in line for line in problems), problems)
        self.assertEqual(2, len(problems), problems)

    def test_the_postcondition_is_silent_on_a_correct_apply(self):
        """Отрицательный контроль: сеть, срабатывающая всегда, бесполезна."""
        conn = sqlite3.connect(self.db)
        try:
            before_month = migration.month_totals(conn)
            before = {migration.normalize(n): migration.operator_hectares(
                conn, n) for n in [x[1] for x in migration.LINKS]
                + [migration.REASSIGN_TO, migration.REASSIGN_FROM]}
        finally:
            conn.close()
        run(self.db, '--apply')
        conn = sqlite3.connect(self.db)
        try:
            problems = migration.check_postcondition(conn, before,
                                                     before_month)
        finally:
            conn.close()
        self.assertEqual([], problems)

    # 6. Напечатанный откат возвращает базу.
    def test_printed_rollback_restores_the_books(self):
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
        self.assertTrue(statements, 'откат не напечатан')

        conn = sqlite3.connect(self.db)
        try:
            for statement in statements:
                conn.execute(statement)
            conn.commit()
        finally:
            conn.close()
        self.assertEqual(before, snapshot(self.db))
        self.assertEqual([], registry(self.db))

    # 6. И после отката миграция проходит снова.
    def test_the_migration_runs_again_after_its_own_rollback(self):
        first = run(self.db, '--apply')
        statements, collecting = [], False
        for line in first.stdout.splitlines():
            if line.startswith('ROLLBACK OF DATA'):
                collecting = True
                continue
            if collecting and line.strip():
                statements.append(line.strip())
        after_apply = snapshot(self.db)
        conn = sqlite3.connect(self.db)
        for statement in statements:
            conn.execute(statement)
        conn.commit()
        conn.close()
        second = run(self.db, '--apply')
        self.assertEqual(0, second.returncode, second.stdout + second.stderr)
        self.assertEqual(after_apply, snapshot(self.db))


if __name__ == '__main__':
    unittest.main()
