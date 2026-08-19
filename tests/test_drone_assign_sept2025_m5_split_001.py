# -*- coding: utf-8 -*-
"""DRONES_ASSIGN_SEPT2025_M5_SPLIT_001: раздел машины 5 на синтетике.

Четыре пути устава (CLAUDE.md, «Миграции») и четыре отрицательных контроля.
Проверка, дающая одинаковый результат на верной и на испорченной базе,
проверкой не является, поэтому каждое условие проверяется дважды -- когда оно
выполнено и когда нарушено.

  1. Чистая база: сухой прогон НИЧЕГО не пишет, боевой -- пишет и регистрирует.
  2. Повтор: «already applied», база не трогается второй раз.
  3. Базы нет: код 2, файл НЕ создан. sqlite3.connect() создал бы пустую базу,
     и без этой проверки миграция «успешно» отработала бы в пустоте.
  4. Предусловие нарушено (назначения правили после FIX_001): код 1, полный
     откат, в реестре пусто.
  5. Постусловие нарушено: код 1, откат.
  5а. ГЛАВНЫЙ отрицательный контроль этой миграции: база, в которой вылеты
     машины 5 за 05-06.09 лежат в других днях, то есть мир БЕЗ раздела.
     Постусловие обязано её отвергнуть -- иначе оно не отличает мир с
     разделом от мира без него и проверкой не является.
  5б. Вылет под двумя операторами не отдаётся молча одному из них.
  5в. Строка Холмуродова на машине 5 вне сентября предусловием не видна --
     её ловит отдельная гвардия перед вставкой, иначе 05-06.09 оказались бы
     покрыты дважды.
  6. Печатаемый откат ВОЗВРАЩАЕТ базу в исходное состояние -- проверяется
     исполнением напечатанного SQL, а не чтением его глазами.

Run:
  python -m unittest tests.test_drone_assign_sept2025_m5_split_001 -v
"""
import os
import sqlite3
import subprocess
import sys
import tempfile
import unittest

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SCRIPT = os.path.join(REPO_ROOT,
                      'migrate_drones_assign_sept2025_m5_split_001.py')
sys.path.insert(0, REPO_ROOT)

import migrate_drones_assign_sept2025_m5_split_001 as migration  # noqa: E402

# (машина, день, гектары). Подобраны так, что ПОСЛЕ раздела каждый оператор
# получает ровно то число, которое стоит в EXPECTED_HA, сумма месяца равна
# 5572.18, а без оператора остаются 12.41 га машин 2 и 9. До раздела те же
# вылеты дают Холмуродову 795.06 и Кобилову 311.26 -- и постусловие падает.
FLIGHTS = (
    (1, '2025-09-15', 1009.73),
    (3, '2025-09-15', 795.06),
    (5, '2025-09-05', 20.58),     # <- эти два дня и есть предмет миграции
    (5, '2025-09-15', 290.68),
    (4, '2025-09-20', 217.50),
    (6, '2025-09-20', 130.49),
    (15, '2025-09-07', 63.45),
    (15, '2025-09-20', 388.40),
    (7, '2025-09-20', 143.97),
    (8, '2025-09-15', 890.22),
    (10, '2025-09-20', 299.44),
    (11, '2025-09-08', 116.11),
    (11, '2025-09-20', 418.44),
    (12, '2025-09-10', 134.50),
    (13, '2025-09-20', 273.20),
    (12, '2025-09-20', 199.19),
    (14, '2025-09-20', 168.80),
    (2, '2025-09-15', 1.75),
    (9, '2025-09-15', 10.66),
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


class M5SplitTest(unittest.TestCase):

    def setUp(self):
        self.dir = tempfile.mkdtemp(prefix='m5split_')
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
        before = assignments(self.db)
        result = run(self.db, '--apply')
        self.assertEqual(0, result.returncode, result.stdout + result.stderr)
        self.assertIn('APPLIED', result.stdout)

        rows = assignments(self.db)
        by_unit = {}
        for number, name, date_from, date_to in rows:
            by_unit.setdefault(number, []).append((name, date_from, date_to))
        self.assertEqual(
            [('Холмуродов Шахзод', '2025-09-05', '2025-09-06'),
             ('Кобилов Фаррух', '2025-09-07', '2025-09-29')],
            by_unit[5])
        # Ровно одна новая строка и ровно одна сдвинутая -- больше ничего.
        self.assertEqual(len(before) + 1, len(rows))
        self.assertEqual([r for r in before if r[0] != 5],
                         [r for r in rows if r[0] != 5])
        self.assertIn(migration.MIGRATION_ID, registry(self.db))

    # 1. Основание раздела попадает в базу, а не остаётся в описании коммита.
    def test_the_new_row_carries_the_evidence_in_its_note(self):
        build_db(self.db)
        run(self.db, '--apply')
        conn = sqlite3.connect(self.db)
        try:
            note = conn.execute(
                'SELECT note FROM drone_operator_assignments '
                'WHERE date_from = ? AND date_to = ?',
                (migration.ADD_FROM, migration.ADD_TO)).fetchone()[0]
        finally:
            conn.close()
        self.assertEqual(migration.ADD_NOTE, note)
        self.assertIn('карта DJI', note)

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
        broken[3] = (5, 'Кобилов Фаррух', '2025-09-04', '2025-09-29')
        build_db(self.db, baseline=tuple(broken))
        before = assignments(self.db)
        result = run(self.db, '--apply')
        self.assertEqual(1, result.returncode, result.stdout)
        self.assertIn('PRECONDITION FAILED', result.stdout)
        self.assertEqual(before, assignments(self.db))
        self.assertEqual([], registry(self.db))

    # 4. И наоборот: миграция, уже применённая один раз, второй раз не пройдёт
    #    предусловие, даже если стереть запись реестра. Раздел не удваивается.
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

    # 5. Постусловие нарушено: код 1, откат.
    def test_hectares_that_do_not_match_the_reconciliation_roll_back(self):
        bad = list(FLIGHTS)
        bad[1] = (3, '2025-09-15', 895.06)     # +100 га Холмуродову
        build_db(self.db, flights=tuple(bad))
        before = assignments(self.db)
        result = run(self.db, '--apply')
        self.assertEqual(1, result.returncode, result.stdout)
        self.assertIn('POSTCONDITION FAILED', result.stdout)
        self.assertEqual(before, assignments(self.db))
        self.assertEqual([], registry(self.db))

    # 5а. ГЛАВНЫЙ отрицательный контроль: мир БЕЗ раздела отвергается.
    def test_a_world_without_the_split_is_refused(self):
        """Постусловие обязано отличать мир с разделом от мира без него.

        Здесь те же 20.58 га машины 5 перенесены с 05.09 на 15.09 -- то есть
        в день, который и до, и после миграции принадлежит Кобилову. Сумма
        месяца, число вылетов и все прочие операторы не меняются: отличается
        РОВНО то, ради чего миграция написана. Если бы постусловие этого не
        видело, оно прошло бы и на неверных данных.
        """
        without = [f for f in FLIGHTS if f != (5, '2025-09-05', 20.58)]
        without.append((5, '2025-09-16', 20.58))
        build_db(self.db, flights=tuple(without))
        result = run(self.db, '--apply')
        self.assertEqual(1, result.returncode, result.stdout)
        self.assertIn('POSTCONDITION FAILED', result.stdout)
        self.assertIn('795.06', result.stdout)      # столько дал бы Холмуродов
        self.assertIn('311.26', result.stdout)      # столько -- Кобилов
        self.assertEqual([], registry(self.db))

    # 5б. Вылет под двумя операторами не отдаётся молча одному из них.
    def test_a_flight_covered_by_two_operators_is_not_given_to_either(self):
        """Сеть безопасности должна РАБОТАТЬ, а не просто быть написанной.

        Эта миграция сажает на одну машину второго оператора, и ошибка в один
        день на границе дала бы перекрытие. Проверяется прямо: два назначения
        на машину 5 в одни дни -- и гектары обязаны уйти в корзину «спорное».
        """
        build_db(self.db)
        conn = sqlite3.connect(self.db)
        try:
            conn.execute(
                'INSERT INTO drone_operator_assignments '
                '(operator_id, drone_unit_id, date_from, date_to) '
                'VALUES ((SELECT id FROM drone_operators '
                "         WHERE full_name = 'Холмуродов Шахзод'), "
                "        5, '2025-09-05', '2025-09-29')")
            conn.commit()
            by_operator, unassigned, ambiguous, total = \
                migration.hectares_by_operator(conn)
            problems = migration.check_postcondition(conn)
        finally:
            conn.close()
        # Оба вылета машины 5 -- 20.58 и 290.68 -- стали спорными.
        self.assertAlmostEqual(311.26, ambiguous, places=2)
        self.assertAlmostEqual(
            0.0, by_operator.get(migration.normalize('Кобилов Фаррух'), 0.0),
            places=2)
        self.assertAlmostEqual(12.41, unassigned, places=2)
        self.assertAlmostEqual(5572.17, total, places=2)
        self.assertTrue(any('two operators at once' in line
                            for line in problems), problems)

    # 5в. Строка Холмуродова на машине 5 ВНЕ сентября предусловием не видна.
    def test_an_existing_row_outside_september_is_still_refused(self):
        """Предусловие смотрит только на сентябрь -- гвардия смотрит шире.

        Августовская строка Холмуродова на машине 5 пройдёт предусловие, но
        после вставки на этой машине оказались бы две его строки. Вставка
        обязана не состояться, а база -- остаться нетронутой.
        """
        build_db(self.db, extra=((5, 'Холмуродов Шахзод',
                                  '2025-08-01', '2025-08-31'),))
        before = assignments(self.db)
        result = run(self.db, '--apply')
        self.assertNotEqual(0, result.returncode, result.stdout)
        self.assertIn('already has', result.stdout + result.stderr)
        self.assertEqual(before, assignments(self.db))
        self.assertEqual([], registry(self.db))

    # 6. Напечатанный откат действительно возвращает базу в исходное.
    def test_printed_rollback_restores_the_baseline(self):
        build_db(self.db)
        before = assignments(self.db)
        result = run(self.db, '--apply')
        self.assertEqual(0, result.returncode, result.stdout)
        self.assertNotEqual(before, assignments(self.db))
        self.assertEqual([migration.MIGRATION_ID], registry(self.db))

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

    # 6. И после отката миграция проходит снова -- откат полный, а не почти.
    def test_the_migration_runs_again_after_its_own_rollback(self):
        build_db(self.db)
        first = run(self.db, '--apply')
        statements = []
        collecting = False
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
        self.assertIn('APPLIED', second.stdout)
        self.assertEqual(after_apply, assignments(self.db))


if __name__ == '__main__':
    unittest.main()
