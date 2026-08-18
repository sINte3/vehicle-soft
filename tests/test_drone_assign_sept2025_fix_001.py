# -*- coding: utf-8 -*-
"""DRONES_ASSIGN_SEPT2025_FIX_001: правка сентябрьских назначений на синтетике.

Четыре пути устава (CLAUDE.md, «Миграции») и два отрицательных контроля.
Проверка, дающая одинаковый результат на верной и на испорченной базе,
проверкой не является, поэтому здесь каждое условие проверяется дважды --
когда оно выполнено и когда нарушено.

  1. Чистая база: сухой прогон НИЧЕГО не пишет, боевой -- пишет и регистрирует.
  2. Повтор: «already applied», база не трогается второй раз.
  3. Базы нет: код 2, файл НЕ создан. sqlite3.connect() создал бы пустую базу,
     и без этой проверки миграция «успешно» отработала бы в пустоте.
  4. Предусловие нарушено (кто-то правил назначения после 2026-08-14): код 1,
     полный откат, в реестре пусто.
  5. Постусловие нарушено (гектары не сходятся со сверкой): код 1, откат.
     Это и есть отрицательный контроль на само постусловие: если бы оно
     считалось неверно, миграция прошла бы на испорченных данных.
  6. Печатаемый откат ВОЗВРАЩАЕТ базу в исходное состояние -- проверяется
     исполнением напечатанного SQL, а не чтением его глазами.

Run:
  python -m unittest tests.test_drone_assign_sept2025_fix_001 -v
"""
import os
import sqlite3
import subprocess
import sys
import tempfile
import unittest

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SCRIPT = os.path.join(REPO_ROOT, 'migrate_drones_assign_sept2025_fix_001.py')
sys.path.insert(0, REPO_ROOT)

import migrate_drones_assign_sept2025_fix_001 as migration  # noqa: E402

# (машина, день, гектары) -- подобраны так, что ПОСЛЕ миграции каждый оператор
# получает ровно то число, которое стоит в EXPECTED_HA, а сумма месяца равна
# 5572.18. До миграции те же вылеты распределяются иначе.
FLIGHTS = (
    (1, '2025-09-15', 1009.73),
    (3, '2025-09-15', 795.06),
    (4, '2025-09-20', 217.50),
    (5, '2025-09-15', 311.27),
    (6, '2025-09-20', 130.49),
    (7, '2025-09-20', 143.97),
    (8, '2025-09-15', 890.22),
    (9, '2025-09-15', 10.66),
    (10, '2025-09-15', 299.44),
    (11, '2025-09-08', 116.11),
    (11, '2025-09-20', 418.44),
    (12, '2025-09-10', 134.50),
    (12, '2025-09-20', 199.19),
    (13, '2025-09-20', 273.20),
    (14, '2025-09-20', 168.80),
    (15, '2025-09-07', 63.45),
    (15, '2025-09-20', 388.40),
    (2, '2025-09-15', 1.75),
)


def build_db(path, baseline=migration.BASELINE, flights=FLIGHTS):
    conn = sqlite3.connect(path)
    conn.executescript(
        'CREATE TABLE drone_units (id INTEGER PRIMARY KEY, number INTEGER);'
        'CREATE TABLE drone_operators (id INTEGER PRIMARY KEY, '
        '  full_name TEXT);'
        'CREATE TABLE drone_operator_assignments (id INTEGER PRIMARY KEY, '
        '  operator_id INTEGER, drone_unit_id INTEGER, date_from TEXT, '
        '  date_to TEXT, note TEXT, created_at TEXT, created_by INTEGER);'
        'CREATE TABLE drone_flights (id INTEGER PRIMARY KEY, '
        '  dji_flight_id INTEGER, drone_unit_id INTEGER, started_at TEXT, '
        '  area_ha REAL);')
    for number in range(1, 16):
        conn.execute('INSERT INTO drone_units (id, number) VALUES (?, ?)',
                     (number, number))
    names = []
    for _number, name, _f, _t in baseline:
        if name not in names:
            names.append(name)
    for idx, name in enumerate(names, 1):
        conn.execute('INSERT INTO drone_operators (id, full_name) '
                     'VALUES (?, ?)', (idx, name))
    for row_id, (number, name, date_from, date_to) in enumerate(baseline,
                                                                start=3):
        conn.execute(
            'INSERT INTO drone_operator_assignments '
            '(id, operator_id, drone_unit_id, date_from, date_to, note) '
            'VALUES (?, ?, ?, ?, ?, ?)',
            (row_id, names.index(name) + 1, number, date_from, date_to,
             '[TELEMETRY] baseline'))
    for idx, (unit, day, area) in enumerate(flights, 1):
        conn.execute(
            'INSERT INTO drone_flights '
            '(id, dji_flight_id, drone_unit_id, started_at, area_ha) '
            'VALUES (?, ?, ?, ?, ?)',
            (idx, 100000 + idx, unit, day + ' 06:00:00', area))
    conn.commit()
    conn.close()


def run(db, *extra):
    return subprocess.run([sys.executable, SCRIPT, '--db', db] + list(extra),
                          capture_output=True, text=True)


def assignments(db):
    conn = sqlite3.connect(db)
    try:
        return conn.execute(
            'SELECT u.number, o.full_name, a.date_from, a.date_to '
            'FROM drone_operator_assignments a '
            'JOIN drone_units u ON u.id = a.drone_unit_id '
            'JOIN drone_operators o ON o.id = a.operator_id '
            'ORDER BY u.number, a.date_from').fetchall()
    finally:
        conn.close()


def registry(db):
    conn = sqlite3.connect(db)
    try:
        names = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' "
            "AND name='schema_migrations'").fetchone()
        if not names:
            return []
        return [row[0] for row in conn.execute(
            'SELECT name FROM schema_migrations')]
    finally:
        conn.close()


class AssignFixTest(unittest.TestCase):

    def setUp(self):
        self.dir = tempfile.mkdtemp(prefix='assignfix_')
        self.db = os.path.join(self.dir, 'transport.db')

    # 1. Чистая база: сухой прогон ничего не пишет.
    def test_dry_run_writes_nothing(self):
        build_db(self.db)
        before = assignments(self.db)
        result = run(self.db)
        self.assertEqual(0, result.returncode, result.stdout + result.stderr)
        self.assertIn('DRY RUN', result.stdout)
        self.assertEqual(before, assignments(self.db))
        self.assertEqual([], registry(self.db))

    # 1. Чистая база: боевой прогон пишет ровно то, что обещал.
    def test_apply_makes_exactly_the_promised_changes(self):
        build_db(self.db)
        result = run(self.db, '--apply')
        self.assertEqual(0, result.returncode, result.stdout + result.stderr)
        self.assertIn('APPLIED', result.stdout)
        rows = assignments(self.db)
        by_unit = {}
        for number, name, date_from, date_to in rows:
            by_unit.setdefault(number, []).append((name, date_from, date_to))
        self.assertEqual(
            [('Хамроев Шохрух', '2025-09-06', '2025-09-10'),
             ('Ибодуллаев Хасанбой', '2025-09-11', '2025-09-30')],
            by_unit[11])
        self.assertEqual(
            [('Имомов Бехзод', '2025-09-06', '2025-09-16'),
             ('Кудратов Мухриддин', '2025-09-17', '2025-09-30')],
            by_unit[12])
        self.assertEqual(
            [('Жураев Туйгун', '2025-09-06', '2025-09-07'),
             ('Жумаев Фуркат', '2025-09-08', '2025-09-30')],
            by_unit[15])
        self.assertNotIn(9, by_unit)          # машина 9 осталась без оператора
        self.assertIn(migration.MIGRATION_ID, registry(self.db))

    # 2. Повтор: «already applied», второй раз ничего не меняется.
    def test_second_apply_is_a_no_op(self):
        build_db(self.db)
        run(self.db, '--apply')
        after_first = assignments(self.db)
        result = run(self.db, '--apply')
        self.assertEqual(0, result.returncode, result.stdout)
        self.assertIn('already applied', result.stdout)
        self.assertEqual(after_first, assignments(self.db))
        self.assertEqual(1, len(registry(self.db)))

    # 3. Базы нет: код 2, файл НЕ создан.
    def test_missing_database_gives_code_two_and_creates_nothing(self):
        absent = os.path.join(self.dir, 'nope.db')
        result = run(absent)
        self.assertEqual(2, result.returncode, result.stdout)
        self.assertFalse(os.path.exists(absent))

    # 4. Предусловие нарушено: код 1, полный откат, реестр пуст.
    def test_edited_baseline_is_refused(self):
        broken = list(migration.BASELINE)
        broken[9] = (11, 'Ибодуллаев Хасанбой', '2025-09-04', '2025-09-30')
        build_db(self.db, baseline=tuple(broken))
        before = assignments(self.db)
        result = run(self.db, '--apply')
        self.assertEqual(1, result.returncode, result.stdout)
        self.assertIn('PRECONDITION FAILED', result.stdout)
        self.assertEqual(before, assignments(self.db))
        self.assertEqual([], registry(self.db))

    # 5. Постусловие нарушено: код 1, откат. Отрицательный контроль на само
    #    постусловие -- без него миграция прошла бы на неверных данных.
    def test_hectares_that_do_not_match_the_reconciliation_roll_back(self):
        bad = list(FLIGHTS)
        bad[9] = (11, '2025-09-08', 216.11)   # +100 га не туда
        build_db(self.db, flights=tuple(bad))
        before = assignments(self.db)
        result = run(self.db, '--apply')
        self.assertEqual(1, result.returncode, result.stdout)
        self.assertIn('POSTCONDITION FAILED', result.stdout)
        self.assertIn('Хамроев'.encode('ascii', 'replace').decode('ascii'),
                      result.stdout)
        self.assertEqual(before, assignments(self.db))
        self.assertEqual([], registry(self.db))

    # 5а. Вылет под двумя операторами не отдаётся молча одному из них.
    def test_a_flight_covered_by_two_operators_is_not_given_to_either(self):
        """Модель прямо запрещает молчаливый выбор из нескольких кандидатов.

        Границы раздела в этой миграции непересекающиеся по построению, и
        предусловие не даёт им разъехаться. Но сеть безопасности должна
        РАБОТАТЬ, а не просто быть написанной, поэтому она проверяется прямо:
        два назначения на одну машину в один день -- и гектары дня обязаны
        уйти в корзину 'спорное', а не одному из двоих.
        """
        build_db(self.db)
        conn = sqlite3.connect(self.db)
        try:
            # Хамроев на машине 11 в те же дни, что и Хасанбой.
            conn.execute(
                'INSERT INTO drone_operator_assignments '
                '(operator_id, drone_unit_id, date_from, date_to) '
                'VALUES ((SELECT id FROM drone_operators '
                "         WHERE full_name = 'Хамроев Шохрух'), "
                "        11, '2025-09-05', '2025-09-30')")
            conn.commit()
            by_operator, unassigned, ambiguous, total = \
                migration.hectares_by_operator(conn)
        finally:
            conn.close()
        # Оба вылета машины 11 -- 116.11 и 418.44 -- стали спорными.
        self.assertAlmostEqual(534.55, ambiguous, places=2)
        self.assertAlmostEqual(
            0.0, by_operator.get(migration.normalize('Ибодуллаев Хасанбой'),
                                 0.0), places=2)
        self.assertAlmostEqual(5572.18, total, places=2)
        # И постусловие на такой базе не проходит.
        conn = sqlite3.connect(self.db)
        try:
            problems = migration.check_postcondition(conn)
        finally:
            conn.close()
        self.assertTrue(any('two operators at once' in line
                            for line in problems), problems)

    # 6. Напечатанный откат действительно возвращает базу в исходное.
    def test_printed_rollback_restores_the_baseline(self):
        build_db(self.db)
        before = assignments(self.db)
        result = run(self.db, '--apply')
        self.assertEqual(0, result.returncode, result.stdout)
        self.assertNotEqual(before, assignments(self.db))

        statements = []
        collecting = False
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
        self.assertEqual(before, assignments(self.db))
        self.assertEqual([], registry(self.db))


if __name__ == '__main__':
    unittest.main()
