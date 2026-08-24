# -*- coding: utf-8 -*-
"""DRONES_ASSIGN_OCT2025_SERVIS15_001: машина №15 октября -- Жумаева Фурқата.

Синтетика повторяет ФОРМУ настоящих данных октября: у машины №15 вылеты с
08 по 30 октября и ни одного назначения; у соседних машин назначения есть;
часть вылетов лежит у бортов, которые остаются без оператора намеренно
(гарденские №7 и хвост №8) -- их миграция трогать не смеет.

Главная сеть здесь -- ПЕРЕКРЫТИЕ. Модель назначений намеренно разрешает двум
операторам стоять на одной машине, и вылет, накрытый двумя, уходит в корзину
«Несколько операторов»: гектары не исчезают из итога месяца, но пропадают у
ОБОИХ людей. Проверка итога месяца такую беду не видит вовсе, поэтому
«вылетов с двумя операторами» проверяется отдельно и с обеих сторон --
предусловием и постусловием.

Run:
  python -m unittest tests.test_drone_assign_oct2025_servis15_001 -v
"""
import datetime
import os
import sqlite3
import subprocess
import sys
import tempfile
import unittest

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SCRIPT = os.path.join(REPO_ROOT,
                      'migrate_drones_assign_oct2025_servis15_001.py')
sys.path.insert(0, REPO_ROOT)

import migrate_drones_assign_oct2025_servis15_001 as migration  # noqa

SCHEMA = """
CREATE TABLE drone_operators (id INTEGER PRIMARY KEY, full_name TEXT);
CREATE TABLE drone_units (id INTEGER PRIMARY KEY, number INTEGER);
CREATE TABLE drone_operator_assignments (id INTEGER PRIMARY KEY AUTOINCREMENT,
  operator_id INTEGER NOT NULL, drone_unit_id INTEGER NOT NULL,
  date_from DATE NOT NULL, date_to DATE, note TEXT, created_at TEXT);
CREATE TABLE drone_flights (id INTEGER PRIMARY KEY AUTOINCREMENT,
  drone_unit_id INTEGER, started_at TEXT NOT NULL, area_ha NUMERIC);
"""

OPERATORS = ('Жумаев Фуркат', 'Имомов Беҳзод', 'Қодиров Нурали',
             'Холмуродов Шахзод')
UNITS = (3, 7, 8, 10, 13, 15)

# (машина, день октября, вылетов, гектаров) -- форма настоящей выгрузки.
UNIT15_DAYS = ((8, 5, 3.85), (9, 9, 7.56), (10, 25, 21.95), (11, 3, 1.22),
               (12, 3, 1.60), (13, 5, 4.13), (14, 3, 0.09), (15, 2, 1.68),
               (21, 1, 0.34), (30, 11, 5.4991))   # итого 67 / 47.9174
GARDEN7_DAYS = ((8, 12, 7.6728), (9, 3, 1.98), (10, 4, 1.03), (11, 2, 0.82),
                (12, 3, 0.47))                    # итого 24 / 11.9728
GARDEN8_TAIL = ((14, 2, 0.80), (15, 3, 0.4672))   # итого 5 / 1.2672
UNIT10_DAYS = ((6, 1, 0.0),)                      # итого 1 / 0.00


def build_db(path, unit15=UNIT15_DAYS, extra_assignments=(), skip_unit15=False,
             skip_operator=False):
    conn = sqlite3.connect(path)
    conn.executescript(SCHEMA)
    ids = {}
    for idx, name in enumerate(OPERATORS, 1):
        if skip_operator and name == 'Жумаев Фуркат':
            continue
        conn.execute('INSERT INTO drone_operators (id, full_name) '
                     'VALUES (?, ?)', (idx, name))
        ids[name] = idx
    for idx, number in enumerate(UNITS, 1):
        if skip_unit15 and number == 15:
            continue
        conn.execute('INSERT INTO drone_units (id, number) VALUES (?, ?)',
                     (idx, number))
    unit_by_number = {n: i for i, n in enumerate(UNITS, 1)}

    def flights(number, days):
        for day, count, hectares in days:
            per = hectares / count
            for k in range(count):
                # [REASON]: 03:00 по Ташкенту -> 22:00 UTC ПРЕДЫДУЩЕГО дня.
                # Хранится UTC, а месяц и день считаются по UTC+5, ровно как
                # в приложении. Ставить полдень значило бы никогда не задеть
                # смещение и не проверить его.
                started = (datetime.datetime(2025, 10, day, 3, 0)
                           - datetime.timedelta(minutes=300)
                           + datetime.timedelta(minutes=k))
                conn.execute('INSERT INTO drone_flights (drone_unit_id, '
                             'started_at, area_ha) VALUES (?, ?, ?)',
                             (unit_by_number.get(number),
                              started.strftime('%Y-%m-%d %H:%M:%S'), per))

    flights(15, unit15)
    flights(7, GARDEN7_DAYS)
    flights(8, GARDEN8_TAIL)
    flights(10, UNIT10_DAYS)
    # [REASON]: фикстура несёт ВЕСЬ месяц, а не только сирот. Итог октября
    # на проде 1478.1176 га; привязанная часть -- 1416.9602. Без неё
    # постусловие «итог месяца не сдвинулся» проверялось бы на огрызке и
    # ничего бы не значило, а предусловие с настоящими числами не прошло бы
    # вовсе. Та же грабля, что и с выдуманной фикстурой отчёта о дублях.
    conn.execute('INSERT INTO drone_units (id, number) VALUES (98, 99)')
    unit_by_number[99] = 98
    flights(99, ((9, 10, 1416.9602),))
    conn.execute('INSERT INTO drone_operator_assignments (operator_id, '
                 'drone_unit_id, date_from, date_to, note) '
                 "VALUES (?, 98, '2025-10-01', '2025-10-31', 'сосед')",
                 (ids.get('Имомов Беҳзод', 2),))
    # [REASON]: у машины №15 есть ЗАКОННОЕ окно другого месяца. Октябрьского
    # перекрытия оно не даёт, но откат вида «удали назначения этой машины»
    # снёс бы и его -- поэтому откат идёт по id вставленной строки.
    conn.execute('INSERT INTO drone_operator_assignments (operator_id, '
                 'drone_unit_id, date_from, date_to, note) '
                 "VALUES (?, ?, '2025-09-01', '2025-09-30', 'сентябрь')",
                 (ids.get('Холмуродов Шахзод', 4), unit_by_number[15]))
    # Гарденская №8 привязана только по 13.10 -- хвост 14-15 остаётся ничей.
    conn.execute('INSERT INTO drone_operator_assignments (operator_id, '
                 'drone_unit_id, date_from, date_to, note) '
                 "VALUES (?, ?, '2025-10-08', '2025-10-13', 'гарден')",
                 (ids.get('Қодиров Нурали', 3), unit_by_number[8]))
    for op, number, dfrom, dto in extra_assignments:
        conn.execute('INSERT INTO drone_operator_assignments (operator_id, '
                     'drone_unit_id, date_from, date_to, note) '
                     "VALUES (?, ?, ?, ?, 'доп')",
                     (ids.get(op, 1), unit_by_number[number], dfrom, dto))
    conn.commit()
    conn.close()


def run(db, *args):
    return subprocess.run([sys.executable, SCRIPT, '--db', db] + list(args),
                          capture_output=True, text=True)


def snapshot(db):
    conn = sqlite3.connect(db)
    try:
        return conn.execute('SELECT id, operator_id, drone_unit_id, '
                            'date_from, date_to FROM '
                            'drone_operator_assignments ORDER BY id'
                            ).fetchall()
    finally:
        conn.close()


def attribution(db):
    conn = sqlite3.connect(db)
    try:
        return migration.attribution(conn)
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


class Servis15Test(unittest.TestCase):

    def setUp(self):
        self.dir = tempfile.mkdtemp(prefix='oct15_')
        self.db = os.path.join(self.dir, 'transport.db')
        build_db(self.db)

    # 0. Фикстура повторяет настоящие числа -- иначе проверять нечего.
    def test_the_fixture_reproduces_the_production_figures(self):
        (orphan_n, orphan_ha), many, _by_op, (total_n, total_ha) = \
            attribution(self.db)
        self.assertEqual(migration.EXPECTED_ORPHAN_FLIGHTS, orphan_n)
        self.assertAlmostEqual(migration.EXPECTED_ORPHAN_HA, orphan_ha, 2)
        self.assertEqual(0, many)
        self.assertEqual(97 + 10, total_n)
        self.assertAlmostEqual(migration.EXPECTED_MONTH_HA, total_ha, 2)

    # 1. Сухой прогон ничего не пишет.
    def test_dry_run_writes_nothing(self):
        before = snapshot(self.db)
        result = run(self.db)
        self.assertEqual(0, result.returncode, result.stdout + result.stderr)
        self.assertIn('DRY RUN', result.stdout)
        self.assertEqual(before, snapshot(self.db))
        self.assertEqual([], registry(self.db))

    # 1. Боевой прогон даёт ровно обещанные числа.
    def test_apply_binds_the_machine_and_the_hectares_land(self):
        result = run(self.db, '--apply')
        self.assertEqual(0, result.returncode, result.stdout + result.stderr)
        self.assertIn('APPLIED', result.stdout)
        (orphan_n, orphan_ha), many, by_op, (_n, total_ha) = \
            attribution(self.db)
        conn = sqlite3.connect(self.db)
        try:
            op_id = migration.operator_id(conn, migration.OPERATOR)
        finally:
            conn.close()
        self.assertAlmostEqual(migration.EXPECTED_UNIT_HA, by_op[op_id], 2)
        self.assertAlmostEqual(migration.EXPECTED_ORPHAN_HA_AFTER, orphan_ha,
                               2)
        self.assertEqual(97 - 67, orphan_n)
        self.assertEqual(0, many)
        self.assertIn(migration.MIGRATION_ID, registry(self.db))
        self.assertEqual(4, len(snapshot(self.db)))

    # 1. Гарденские борта и хвост №8 остаются без оператора НАМЕРЕННО.
    def test_the_garden_machines_are_left_unattributed(self):
        out = run(self.db, '--apply').stdout
        (orphan_n, orphan_ha), _many, _by_op, _t = attribution(self.db)
        self.assertEqual(24 + 5 + 1, orphan_n)
        self.assertAlmostEqual(11.9728 + 1.2672 + 0.0, orphan_ha, 2)
        # [REASON]: остаток обязан быть НАЗВАН, а не просто остаться. Молча
        # оставленные 13.24 га выглядят как «всё привязано», и следующая
        # сессия про них не узнает.
        self.assertIn('STILL WITHOUT AN OPERATOR', out)
        self.assertIn('machine No 7', out)
        self.assertIn('11.97 ha', out)

    # 2. ГЛАВНОЕ: перекрытие ловится ПРЕДУСЛОВИЕМ.
    def test_an_existing_overlapping_window_is_refused(self):
        """[REASON]: модель разрешает двум операторам стоять на одной машине.

        Вылет, накрытый двумя, уходит в «Несколько операторов»: из итога
        месяца он не пропадает, поэтому сверка итога МОЛЧИТ, а гектары
        исчезают у обоих людей. Такую базу трогать нельзя вовсе.
        """
        os.remove(self.db)
        build_db(self.db, extra_assignments=(
            ('Холмуродов Шахзод', 15, '2025-10-20', '2025-11-05'),))
        before = snapshot(self.db)
        result = run(self.db, '--apply')
        self.assertEqual(1, result.returncode, result.stdout)
        self.assertIn('PRECONDITION FAILED', result.stdout)
        self.assertIn('overlapping', result.stdout)
        self.assertEqual(before, snapshot(self.db))
        self.assertEqual([], registry(self.db))

    # 2. И ПОСТУСЛОВИЕМ -- отрицательный контроль на ту же сеть.
    def test_the_postcondition_sees_a_flight_with_two_operators(self):
        """Сеть должна РАБОТАТЬ, а не просто быть написанной."""
        run(self.db, '--apply')
        conn = sqlite3.connect(self.db)
        try:
            unit = migration.unit_id(conn, 15)
            conn.execute('INSERT INTO drone_operator_assignments '
                         '(operator_id, drone_unit_id, date_from, date_to, '
                         "note) VALUES (4, ?, '2025-10-01', '2025-10-31', 'x')",
                         (unit,))
            conn.commit()
            problems = migration.check_postcondition(conn)
        finally:
            conn.close()
        self.assertTrue(any('MORE THAN ONE' in p for p in problems), problems)

    def test_the_postcondition_is_silent_on_a_correct_apply(self):
        """Отрицательный контроль: сеть, срабатывающая всегда, бесполезна."""
        run(self.db, '--apply')
        conn = sqlite3.connect(self.db)
        try:
            problems = migration.check_postcondition(conn)
        finally:
            conn.close()
        self.assertEqual([], problems)

    # 2в. Постусловие ловит ОСТАТОК отдельно от всего остального.
    def test_the_postcondition_sees_the_leftover_change_alone(self):
        """[REASON]: изолированный контроль на проверку остатка.

        Гарденская №7 получает оператора со стороны: Жумаеву по-прежнему
        47.92, итог месяца тот же, «несколько операторов» ноль -- все прочие
        сети молчат. Сработать может ТОЛЬКО сверка оставшихся 13.24 га.
        """
        run(self.db, '--apply')
        conn = sqlite3.connect(self.db)
        try:
            unit7 = migration.unit_id(conn, 7)
            conn.execute('INSERT INTO drone_operator_assignments '
                         '(operator_id, drone_unit_id, date_from, date_to, '
                         "note) VALUES (3, ?, '2025-10-01', '2025-10-31', 'x')",
                         (unit7,))
            conn.commit()
            problems = migration.check_postcondition(conn)
        finally:
            conn.close()
        self.assertEqual(1, len(problems), problems)
        self.assertIn('without an operator', problems[0])

    # 3. Окно закрыто: ноябрь Жумаеву не достаётся.
    def test_the_window_is_closed_and_november_is_not_taken(self):
        """[REASON]: открытое окно молча забрало бы ноябрь и всё дальше."""
        run(self.db, '--apply')
        conn = sqlite3.connect(self.db)
        try:
            row = conn.execute(
                'SELECT date_to FROM drone_operator_assignments '
                'WHERE note LIKE ?', ('%SERVIS15%',)).fetchone()
            unit = migration.unit_id(conn, 15)
            conn.execute('INSERT INTO drone_flights (drone_unit_id, '
                         "started_at, area_ha) VALUES (?, '2025-11-05 03:00:00'"
                         ', 9.0)', (unit,))
            conn.commit()
            covered = conn.execute(
                'SELECT COUNT(*) FROM drone_flights f '
                'JOIN drone_operator_assignments a '
                '  ON a.drone_unit_id = f.drone_unit_id '
                " AND date(a.date_from) <= date(f.started_at, '+300 minutes')"
                '  AND (a.date_to IS NULL OR date(a.date_to) >= '
                "     date(f.started_at, '+300 minutes')) "
                "WHERE date(f.started_at, '+300 minutes') >= '2025-11-01'"
            ).fetchone()[0]
        finally:
            conn.close()
        self.assertEqual(migration.DATE_TO, row[0])
        self.assertEqual(0, covered)

    # 4. Повтор ничего не делает.
    def test_second_apply_is_a_no_op(self):
        run(self.db, '--apply')
        after_first = snapshot(self.db)
        result = run(self.db, '--apply')
        self.assertEqual(0, result.returncode, result.stdout)
        self.assertIn('already applied', result.stdout)
        self.assertEqual(after_first, snapshot(self.db))

    # 5. Базы нет: код 2, файл НЕ создан.
    def test_missing_database_gives_code_two_and_creates_nothing(self):
        absent = os.path.join(self.dir, 'nope.db')
        result = run(absent)
        self.assertEqual(2, result.returncode, result.stdout)
        self.assertFalse(os.path.exists(absent))

    # 6. Предусловие: другое число вылетов у №15 -- отказ.
    def test_a_different_flight_count_is_refused(self):
        os.remove(self.db)
        build_db(self.db, unit15=UNIT15_DAYS + ((16, 1, 3.0),))
        before = snapshot(self.db)
        result = run(self.db, '--apply')
        self.assertEqual(1, result.returncode, result.stdout)
        self.assertIn('PRECONDITION FAILED', result.stdout)
        self.assertIn('machine No 15 in October', result.stdout)
        self.assertEqual(before, snapshot(self.db))

    # 6. Предусловие ловит площадь при ТОМ ЖЕ числе вылетов.
    def test_the_same_flight_count_with_wrong_hectares_is_refused(self):
        """[REASON]: сверка только по числу вылетов прошла бы на любых данных."""
        conn = sqlite3.connect(self.db)
        conn.execute('UPDATE drone_flights SET area_ha = area_ha + 1.0 '
                     'WHERE id = (SELECT MIN(f.id) FROM drone_flights f '
                     'JOIN drone_units u ON u.id = f.drone_unit_id '
                     'WHERE u.number = 15)')
        conn.commit()
        conn.close()
        before = snapshot(self.db)
        result = run(self.db, '--apply')
        self.assertEqual(1, result.returncode, result.stdout)
        self.assertIn('machine No 15 in October', result.stdout)
        self.assertEqual(before, snapshot(self.db))

    # 6. Нет машины / нет оператора -- отказ, а не молчаливый пропуск.
    def test_a_missing_machine_is_refused(self):
        os.remove(self.db)
        build_db(self.db, skip_unit15=True)
        result = run(self.db, '--apply')
        self.assertEqual(1, result.returncode, result.stdout)
        self.assertIn('machine No 15 resolves to 0 rows', result.stdout)

    def test_a_missing_operator_is_refused(self):
        os.remove(self.db)
        build_db(self.db, skip_operator=True)
        result = run(self.db, '--apply')
        self.assertEqual(1, result.returncode, result.stdout)
        self.assertIn('resolves to 0 rows', result.stdout)

    # 7. Напечатанный откат возвращает базу.
    def test_printed_rollback_restores_the_assignments(self):
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
        conn = sqlite3.connect(self.db)
        try:
            for statement in statements:
                conn.execute(statement)
            conn.commit()
        finally:
            conn.close()
        self.assertEqual(before, snapshot(self.db))
        self.assertEqual([], registry(self.db))
        # [REASON]: сентябрьское окно той же машины обязано ПЕРЕЖИТЬ откат.
        # Откат вида «удали назначения машины №15» вернул бы ту же длину
        # списка на глаз, но унёс бы чужой месяц.
        self.assertTrue(any(row[4] == '2025-09-30' for row in
                            snapshot(self.db)), snapshot(self.db))

    # 8. Консоль -- только ASCII, и след источника стоит в note.
    def test_the_console_is_ascii_and_the_note_names_the_evidence(self):
        result = run(self.db, '--apply')
        result.stdout.encode('ascii')
        conn = sqlite3.connect(self.db)
        try:
            note = conn.execute(
                'SELECT note FROM drone_operator_assignments '
                'WHERE note LIKE ?', ('%SERVIS15%',)).fetchone()[0]
        finally:
            conn.close()
        self.assertTrue(note.startswith('[TELEMETRY]'))
        self.assertIn('Agro Savdo cho', note)


if __name__ == '__main__':
    unittest.main()
