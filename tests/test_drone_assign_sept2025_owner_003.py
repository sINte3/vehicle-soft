# -*- coding: utf-8 -*-
"""DRONES_ASSIGN_SEPT2025_OWNER_003: три решения владельца на синтетике.

Миграция режет строку Анварова на машине 4 на три куска, вставляет между
ними два дня Холмуродова и добавляет два одиночных дня на машине 9. Главный
риск здесь -- границы: зазор оставит день без оператора, нахлёст сделает
гектары спорными, и ни то ни другое не видно глазами в диффе.

Четыре пути устава (CLAUDE.md, «Миграции») и пять отрицательных контролей.
Проверка, дающая одинаковый результат на верной и на испорченной базе,
проверкой не является, поэтому каждое условие проверяется дважды.

  1. Чистая база: сухой прогон НИЧЕГО не пишет, боевой -- пишет и регистрирует.
  2. Повтор: «already applied», база не трогается второй раз.
  3. Базы нет: код 2, файл НЕ создан.
  4. Предусловие нарушено: код 1, полный откат, в реестре пусто.
  5. Постусловие нарушено: код 1, откат.
  5а. Мир БЕЗ решений владельца отвергается -- иначе постусловие не отличает
     сделанное от несделанного.
  5б. Зазор на границе (день без оператора) ловится.
  5в. Нахлёст на границе (день у двоих) ловится.
  5г. Применённое состояние второй раз не проходит предусловие.
  6. Печатаемый откат ВОЗВРАЩАЕТ базу -- проверяется исполнением, а не
     чтением глазами.

Run:
  python -m unittest tests.test_drone_assign_sept2025_owner_003 -v
"""
import os
import sqlite3
import subprocess
import sys
import tempfile
import unittest

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SCRIPT = os.path.join(REPO_ROOT,
                      'migrate_drones_assign_sept2025_owner_003.py')
sys.path.insert(0, REPO_ROOT)

import migrate_drones_assign_sept2025_owner_003 as migration  # noqa: E402

# (машина, день, гектары). Подобраны так, что ПОСЛЕ миграции каждый оператор
# получает ровно то, что стоит в EXPECTED_HA, сумма месяца равна 5572.18, а
# без оператора остаются 1.76 га машины 2. До миграции те же вылеты дают
# Холмуродову 815.64, Анварову 217.50, Кодирову 890.22, Кобилову 290.68 и
# 12.41 га без оператора.
FLIGHTS = (
    (1, '2025-09-15', 1009.73),
    (3, '2025-09-15', 795.06),
    (5, '2025-09-05', 20.58),
    (5, '2025-09-15', 290.68),
    # Машина 4 -- четыре куска, которые миграция и растаскивает.
    (4, '2025-09-18', 100.51),      # Анваров, до раздела
    (4, '2025-09-23', 26.03),       # -> Холмуродов
    (4, '2025-09-25', 42.52),       # Анваров, между днями Холмуродова
    (4, '2025-09-27', 22.44),       # -> Холмуродов
    (4, '2025-09-29', 25.99),       # Анваров, хвост
    (6, '2025-09-20', 130.49),
    (15, '2025-09-07', 63.45),
    (15, '2025-09-20', 388.40),
    (7, '2025-09-20', 143.97),
    (8, '2025-09-15', 890.22),
    # Машина 9 -- три дня, два из которых получают хозяина.
    (9, '2025-09-05', 6.02),        # -> Кодиров
    (9, '2025-09-26', 0.00),        # так и остаётся ничьим, но и гектаров нет
    (9, '2025-09-27', 4.65),        # -> Кобилов
    (10, '2025-09-20', 299.44),
    (11, '2025-09-08', 116.11),
    (11, '2025-09-20', 418.44),
    (12, '2025-09-10', 134.50),
    (13, '2025-09-20', 273.20),
    (12, '2025-09-20', 199.19),
    (14, '2025-09-20', 168.80),
    (2, '2025-09-27', 0.07),
    (2, '2025-09-29', 1.69),
)


def build_db(path, baseline=migration.BASELINE, flights=FLIGHTS, extra=()):
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
    for _number, name, _from, _to in migration.BASELINE:
        if name not in names:
            names.append(name)
    for idx, name in enumerate(names, 1):
        conn.execute('INSERT INTO drone_operators (id, full_name) '
                     'VALUES (?, ?)', (idx, name))
    for row_id, (number, name, date_from, date_to) in enumerate(
            tuple(baseline) + tuple(extra), start=3):
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
        if not conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table' "
                "AND name='schema_migrations'").fetchone():
            return []
        return [row[0] for row in conn.execute(
            'SELECT name FROM schema_migrations')]
    finally:
        conn.close()


class OwnerDecisionsTest(unittest.TestCase):

    def setUp(self):
        self.dir = tempfile.mkdtemp(prefix='owner003_')
        self.db = os.path.join(self.dir, 'transport.db')

    # 1. Сухой прогон ничего не пишет.
    def test_dry_run_writes_nothing(self):
        build_db(self.db)
        before = assignments(self.db)
        result = run(self.db)
        self.assertEqual(0, result.returncode, result.stdout + result.stderr)
        self.assertIn('DRY RUN', result.stdout)
        self.assertEqual(before, assignments(self.db))
        self.assertEqual([], registry(self.db))

    # 1. Боевой прогон делает ровно то, что обещал -- и ни строкой больше.
    def test_apply_makes_exactly_the_promised_changes(self):
        build_db(self.db)
        before = assignments(self.db)
        result = run(self.db, '--apply')
        self.assertEqual(0, result.returncode, result.stdout + result.stderr)
        self.assertIn('APPLIED', result.stdout)

        rows = assignments(self.db)
        by_unit = {}
        for number, name, date_from, date_to in rows:
            by_unit.setdefault(number, []).append((name, date_from, date_to))
        self.assertEqual(
            [('Анваров Усмон', '2025-09-17', '2025-09-22'),
             ('Холмуродов Шахзод', '2025-09-23', '2025-09-23'),
             ('Анваров Усмон', '2025-09-24', '2025-09-26'),
             ('Холмуродов Шахзод', '2025-09-27', '2025-09-27'),
             ('Анваров Усмон', '2025-09-28', '2025-09-29')],
            by_unit[4])
        self.assertEqual(
            [('Кодиров Нурали', '2025-09-05', '2025-09-05'),
             ('Кобилов Фаррух', '2025-09-27', '2025-09-27')],
            by_unit[9])
        # Шесть новых строк, одна укороченная -- и больше ничего.
        self.assertEqual(len(before) + 6, len(rows))
        self.assertEqual([r for r in before if r[0] not in (4, 9)],
                         [r for r in rows if r[0] not in (4, 9)])
        self.assertIn(migration.MIGRATION_ID, registry(self.db))

    # 1. Границы стыкуются день в день -- без зазора и без нахлёста.
    def test_the_four_boundaries_meet_exactly(self):
        """[REASON]: зазор и нахлёст дают РАЗНЫЕ ошибки и оба тихие.

        Зазор -- гектары уходят в «без оператора», нахлёст -- в «спорное».
        Постусловие ловит оба, но эта проверка называет причину прямо на
        границах, а не через итог.
        """
        build_db(self.db)
        run(self.db, '--apply')
        unit4 = [(f, t) for n, _o, f, t in assignments(self.db) if n == 4]
        for (_prev_from, prev_to), (next_from, _next_to) in zip(unit4,
                                                                unit4[1:]):
            import datetime
            gap = (datetime.date.fromisoformat(next_from)
                   - datetime.date.fromisoformat(prev_to)).days
            self.assertEqual(1, gap,
                             'между %s и %s разрыв %d дн.'
                             % (prev_to, next_from, gap))

    # 1. Основание каждого решения попадает в базу, а не остаётся в коммите.
    def test_every_new_row_carries_its_reason(self):
        build_db(self.db)
        run(self.db, '--apply')
        conn = sqlite3.connect(self.db)
        try:
            notes = dict(conn.execute(
                'SELECT a.date_from || "/" || u.number, a.note '
                'FROM drone_operator_assignments a '
                'JOIN drone_units u ON u.id = a.drone_unit_id '
                'WHERE a.note NOT LIKE "[TELEMETRY]%"').fetchall())
        finally:
            conn.close()
        self.assertEqual(6, len(notes))
        self.assertIn('Анвар Ота замини', notes['2025-09-23/4'])
        self.assertIn('Умарбобо Усмонов', notes['2025-09-27/4'])
        self.assertIn('Фатулло Обод', notes['2025-09-27/9'])
        self.assertIn('в плюсе', notes['2025-09-05/9'])

    # 2. Повтор -- ничего не делает.
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

    # 4. Предусловие нарушено: код 1, полный откат.
    def test_edited_baseline_is_refused(self):
        broken = list(migration.BASELINE)
        broken[2] = (4, 'Анваров Усмон', '2025-09-16', '2025-09-29')
        build_db(self.db, baseline=tuple(broken))
        before = assignments(self.db)
        result = run(self.db, '--apply')
        self.assertEqual(1, result.returncode, result.stdout)
        self.assertIn('PRECONDITION FAILED', result.stdout)
        self.assertEqual(before, assignments(self.db))
        self.assertEqual([], registry(self.db))

    # 5г. Применённое состояние второй раз предусловие не проходит.
    def test_applied_state_no_longer_matches_the_precondition(self):
        build_db(self.db)
        run(self.db, '--apply')
        conn = sqlite3.connect(self.db)
        try:
            conn.execute('DELETE FROM schema_migrations')
            conn.commit()
        finally:
            conn.close()
        before = assignments(self.db)
        result = run(self.db, '--apply')
        self.assertEqual(1, result.returncode, result.stdout)
        self.assertIn('PRECONDITION FAILED', result.stdout)
        self.assertEqual(before, assignments(self.db))

    # 5а. ГЛАВНЫЙ отрицательный контроль: мир БЕЗ решений владельца.
    def test_a_world_without_the_decisions_is_refused(self):
        """Постусловие обязано отличать сделанное от несделанного.

        Здесь гектары машин 4 и 9 переставлены по дням так, что решения
        владельца перестают что-либо менять: спорные дни пусты, а их гектары
        перенесены в дни, которые и так принадлежат прежним владельцам.
        Сумма месяца и число вылетов не меняются.
        """
        moved = []
        for unit, day, area in FLIGHTS:
            if (unit, day) in ((4, '2025-09-23'), (4, '2025-09-27')):
                moved.append((unit, '2025-09-18', area))
            elif (unit, day) == (9, '2025-09-05'):
                moved.append((8, '2025-09-15', area))
            elif (unit, day) == (9, '2025-09-27'):
                moved.append((5, '2025-09-15', area))
            else:
                moved.append((unit, day, area))
        build_db(self.db, flights=tuple(moved))
        result = run(self.db, '--apply')
        self.assertEqual(1, result.returncode, result.stdout)
        self.assertIn('POSTCONDITION FAILED', result.stdout)
        self.assertEqual([], registry(self.db))

    # 5б. Зазор на границе: день остаётся без оператора.
    def test_a_gap_at_a_boundary_is_caught(self):
        """Сеть безопасности должна РАБОТАТЬ, а не просто быть написанной.

        [REASON]: постусловие проверяется прямо, на базе с рукотворным
        зазором, а не подменой констант в файле миграции. Подмена файла
        оставляла бы миграцию испорченной, если тест упадёт посередине.
        """
        build_db(self.db)
        run(self.db, '--apply')
        conn = sqlite3.connect(self.db)
        try:
            # 18-22.09 остаются ничьими: строка Анварова обрывается 17.09.
            # [REASON]: зазор ставится на день, где ВЫЛЕТЫ ЕСТЬ (18.09,
            # 100.51 га). Зазор в пустом дне ничего не меняет в числах, и
            # проверка на нём прошла бы одинаково при верном и неверном коде.
            conn.execute("UPDATE drone_operator_assignments "
                         "SET date_to = '2025-09-17' WHERE drone_unit_id = 4 "
                         "AND date_from = '2025-09-17'")
            conn.commit()
            problems = migration.check_postcondition(conn)
            _by, unassigned, ambiguous, _total = \
                migration.hectares_by_operator(conn)
        finally:
            conn.close()
        self.assertAlmostEqual(0.0, ambiguous, places=2)
        self.assertAlmostEqual(migration.EXPECTED_UNASSIGNED_HA + 100.51,
                               unassigned, places=2)
        self.assertTrue(any('without an operator' in line
                            for line in problems), problems)

    # 5в. Нахлёст на границе: день у двоих сразу.
    def test_an_overlap_at_a_boundary_is_caught(self):
        build_db(self.db)
        run(self.db, '--apply')
        conn = sqlite3.connect(self.db)
        try:
            # 23.09 оказывается и у Анварова, и у Холмуродова.
            conn.execute("UPDATE drone_operator_assignments "
                         "SET date_to = '2025-09-23' WHERE drone_unit_id = 4 "
                         "AND date_from = '2025-09-17'")
            conn.commit()
            problems = migration.check_postcondition(conn)
            _by, _unassigned, ambiguous, _total = \
                migration.hectares_by_operator(conn)
        finally:
            conn.close()
        self.assertAlmostEqual(26.03, ambiguous, places=2)
        self.assertTrue(any('two operators at once' in line
                            for line in problems), problems)

    # 5в-контроль: на ВЕРНОЙ базе те же две проверки молчат.
    def test_the_boundary_checks_are_silent_when_the_split_is_right(self):
        """[REASON]: проверка, которая срабатывает всегда, ничего не значит."""
        build_db(self.db)
        run(self.db, '--apply')
        conn = sqlite3.connect(self.db)
        try:
            problems = migration.check_postcondition(conn)
            _by, unassigned, ambiguous, _total = \
                migration.hectares_by_operator(conn)
        finally:
            conn.close()
        self.assertEqual([], problems)
        self.assertAlmostEqual(0.0, ambiguous, places=2)
        self.assertAlmostEqual(migration.EXPECTED_UNASSIGNED_HA, unassigned,
                               places=2)

    # 6. Напечатанный откат действительно возвращает базу.
    def test_printed_rollback_restores_the_baseline(self):
        build_db(self.db)
        before = assignments(self.db)
        result = run(self.db, '--apply')
        self.assertEqual(0, result.returncode, result.stdout)
        self.assertNotEqual(before, assignments(self.db))

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
        self.assertEqual(before, assignments(self.db))
        self.assertEqual([], registry(self.db))

    # 6. И после отката миграция проходит снова -- откат полный.
    def test_the_migration_runs_again_after_its_own_rollback(self):
        build_db(self.db)
        first = run(self.db, '--apply')
        statements, collecting = [], False
        for line in first.stdout.splitlines():
            if line.startswith('ROLLBACK OF DATA'):
                collecting = True
                continue
            if collecting and line.strip():
                statements.append(line.strip())
        after_apply = assignments(self.db)
        conn = sqlite3.connect(self.db)
        try:
            for statement in statements:
                conn.execute(statement)
            conn.commit()
        finally:
            conn.close()
        second = run(self.db, '--apply')
        self.assertEqual(0, second.returncode, second.stdout + second.stderr)
        self.assertEqual(after_apply, assignments(self.db))


if __name__ == '__main__':
    unittest.main()
