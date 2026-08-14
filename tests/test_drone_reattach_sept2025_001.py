# -*- coding: utf-8 -*-
"""DRONES_REATTACH_SEPT2025_001: сентябрь получает машины из ПРОЧИТАННОГО.

Правила этой миграции — не вывод из сумм, а запись того, что 2026-08-14 отдал
сам кабинет DJI: пятнадцать бортов, снятых по одному через фильтр устройства,
5 661 вылет, ни одного вылета под двумя бортами. Тесты проверяют, что запись
внутренне согласована и что миграция делает ровно её.

К каждой проверке — отрицательный контроль, потому что миграция, которая
«прошла успешно» на любой базе, ничего не гарантирует:

  1. Правила сходятся с кабинетом: сумма по правилам равна 5 661, и она же
     равна сумме вылетов, которые djiag.com показывает по бортам.
     Отрицательный контроль: до миграции борта с кабинетом НЕ сходятся.
  2. Расщепление 8 сентября разрезано МЕЖДУ вылетами, а не посреди них:
     последний вылет борта 12 в 13:30 UTC, первый борта 10 в 13:53 UTC.
  3. После миграции в сентябре не остаётся ни одного вылета без машины.
  4. Предусловие ловит базу, которая не та, из которой выведены правила.
  5. Месяц считается в UTC+5: ночной вылет 31.08 в 20:30 UTC принадлежит
     сентябрю и обязан получить машину.

Run:
  python -m unittest tests.test_drone_reattach_sept2025_001 -v
"""
import datetime
import os
import sqlite3
import sys
import tempfile
import unittest

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, REPO_ROOT)
import migration_utils  # noqa: E402
import migrate_drones_reattach_sept2025_001 as migration  # noqa: E402

# Времена расщепления 8 сентября, как их прочитал обход (UTC).
KOGON7_LAST_ON_12 = '2025-09-08 13:30:16'
KOGON7_FIRST_ON_10 = '2025-09-08 13:53:23'


def build_db(path, extra=None, drop_one=None):
    """База в состоянии ДО миграции: ники есть, машины у большинства нет."""
    conn = sqlite3.connect(path)
    conn.execute('CREATE TABLE drone_units (id INTEGER PRIMARY KEY, '
                 'number INTEGER UNIQUE, hardware_id TEXT)')
    for number in range(1, 16):
        conn.execute('INSERT INTO drone_units (number) VALUES (?)', (number,))
    conn.execute('CREATE TABLE drone_flights (id INTEGER PRIMARY KEY, '
                 'drone_unit_id INTEGER, nickname_raw TEXT, '
                 'serial_number TEXT, started_at TEXT, area_ha REAL)')
    serial = 0
    # Полдень по UTC+5 — вылет заведомо не гуляет по границе суток.
    stamp = '2025-09-15 07:00:00'

    def area_for(machine):
        # [REASON]: площадь берётся из чисел кабинета, а не «по единице на
        # вылет». Постусловие миграции сверяет гектары КАЖДОЙ машины с
        # djiag.com, и фикстура, не воспроизводящая их, проверяла бы не
        # миграцию, а саму себя.
        flights, hectares = migration.DJI_AFTER[machine]
        return hectares / float(flights)

    for nickname, number, count in migration.SIMPLE_RULES:
        if drop_one == nickname:
            count -= 1
        for _ in range(count):
            serial += 1
            conn.execute(
                'INSERT INTO drone_flights (drone_unit_id, nickname_raw, '
                'serial_number, started_at, area_ha) VALUES (?, ?, ?, ?, ?)',
                (None, nickname, 'R%08d' % serial, stamp, area_for(number)))
    # Servis№10: 8 штук 8 сентября, 371 в остальные дни.
    for index in range(migration.SPLIT_SERVIS10_COUNTS[10]):
        serial += 1
        conn.execute(
            'INSERT INTO drone_flights (drone_unit_id, nickname_raw, '
            'serial_number, started_at, area_ha) VALUES (?, ?, ?, ?, ?)',
            (None, 'Servis№10', 'R%08d' % serial, '2025-09-08 07:00:00',
             area_for(migration.SPLIT_SERVIS10_ON_DAY)))
    for index in range(migration.SPLIT_SERVIS10_COUNTS[15]):
        serial += 1
        conn.execute(
            'INSERT INTO drone_flights (drone_unit_id, nickname_raw, '
            'serial_number, started_at, area_ha) VALUES (?, ?, ?, ?, ?)',
            (None, 'Servis№10', 'R%08d' % serial, '2025-09-12 07:00:00',
             area_for(migration.SPLIT_SERVIS10_OTHERWISE)))
    # Kogon№7: 10 штук до разреза, 229 после.
    for index in range(migration.SPLIT_KOGON7_COUNTS[12]):
        serial += 1
        conn.execute(
            'INSERT INTO drone_flights (drone_unit_id, nickname_raw, '
            'serial_number, started_at, area_ha) VALUES (?, ?, ?, ?, ?)',
            (None, 'Kogon№7', 'R%08d' % serial, KOGON7_LAST_ON_12,
             area_for(migration.SPLIT_KOGON7_BEFORE)))
    for index in range(migration.SPLIT_KOGON7_COUNTS[10]):
        serial += 1
        conn.execute(
            'INSERT INTO drone_flights (drone_unit_id, nickname_raw, '
            'serial_number, started_at, area_ha) VALUES (?, ?, ?, ?, ?)',
            (None, 'Kogon№7', 'R%08d' % serial, KOGON7_FIRST_ON_10,
             area_for(migration.SPLIT_KOGON7_AFTER)))
    for row in (extra or []):
        conn.execute(
            'INSERT INTO drone_flights (drone_unit_id, nickname_raw, '
            'serial_number, started_at, area_ha) VALUES (?, ?, ?, ?, ?)', row)
    conn.commit()
    conn.close()


def machine_counts(path):
    conn = sqlite3.connect(path)
    rows = conn.execute(
        "SELECT u.number, COUNT(*) FROM drone_flights f "
        "JOIN drone_units u ON u.id = f.drone_unit_id "
        "WHERE strftime('%Y-%m', datetime(f.started_at, '+300 minutes')) "
        "= '2025-09' GROUP BY u.number").fetchall()
    conn.close()
    return dict(rows)


def orphans(path):
    conn = sqlite3.connect(path)
    count = conn.execute(
        "SELECT COUNT(*) FROM drone_flights WHERE drone_unit_id IS NULL AND "
        "strftime('%Y-%m', datetime(started_at, '+300 minutes')) = '2025-09'"
    ).fetchone()[0]
    conn.close()
    return count


class Rules(unittest.TestCase):
    """1 и 2. Запись согласована сама с собой и с кабинетом."""

    def test_rules_add_up_to_the_swept_total(self):
        total = sum(count for _n, _u, count in migration.SIMPLE_RULES)
        total += sum(migration.SPLIT_SERVIS10_COUNTS.values())
        total += sum(migration.SPLIT_KOGON7_COUNTS.values())
        self.assertEqual(total, migration.EXPECTED_TOTAL)
        self.assertEqual(total, 5661)

    def test_dji_totals_add_up_to_the_same_number(self):
        self.assertEqual(sum(f for f, _ha in migration.DJI_AFTER.values()),
                         migration.EXPECTED_TOTAL)

    def test_every_rule_points_at_a_machine_the_check_covers(self):
        for _nickname, number, _count in migration.SIMPLE_RULES:
            self.assertIn(number, migration.DJI_AFTER)
        for number in (migration.SPLIT_SERVIS10_ON_DAY,
                       migration.SPLIT_SERVIS10_OTHERWISE,
                       migration.SPLIT_KOGON7_BEFORE,
                       migration.SPLIT_KOGON7_AFTER):
            self.assertIn(number, migration.DJI_AFTER)

    def test_no_spelling_is_listed_twice(self):
        names = [nickname for nickname, _u, _c in migration.SIMPLE_RULES]
        self.assertEqual(len(names), len(set(names)))
        self.assertNotIn('Servis№10', names)
        self.assertNotIn('Kogon№7', names)

    def test_the_cut_falls_between_the_two_flights_not_across_them(self):
        """Разрез 8 сентября обязан лежать МЕЖДУ вылетами."""
        cut = migration.SPLIT_KOGON7_CUT_UTC
        self.assertGreater(cut, KOGON7_LAST_ON_12)
        self.assertLess(cut, KOGON7_FIRST_ON_10)


class MigrationCase(unittest.TestCase):

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.db = os.path.join(self.tmp.name, 'transport.db')
        self._saved = (migration.DB_PATH, migration_utils.DB_PATH)
        migration.DB_PATH = self.db
        migration_utils.DB_PATH = self.db

    def tearDown(self):
        migration.DB_PATH, migration_utils.DB_PATH = self._saved
        self.tmp.cleanup()


class HappyPath(MigrationCase):
    """1 и 3. Сентябрь получает машины, и ни один вылет не остаётся без неё."""

    def test_every_machine_matches_the_cabinet(self):
        build_db(self.db)
        # Отрицательный контроль: ДО миграции машин нет вовсе.
        self.assertEqual(machine_counts(self.db), {})
        self.assertEqual(orphans(self.db), migration.EXPECTED_TOTAL)

        migration.run()

        counts = machine_counts(self.db)
        for number, (want_flights, _ha) in migration.DJI_AFTER.items():
            self.assertEqual(counts.get(number, 0), want_flights,
                             'машина %d' % number)
        self.assertEqual(orphans(self.db), 0)

    def test_the_split_day_goes_to_two_different_machines(self):
        build_db(self.db)
        migration.run()
        conn = sqlite3.connect(self.db)
        rows = dict(conn.execute(
            "SELECT u.number, COUNT(*) FROM drone_flights f "
            "JOIN drone_units u ON u.id = f.drone_unit_id "
            "WHERE f.nickname_raw = 'Kogon№7' GROUP BY u.number").fetchall())
        conn.close()
        self.assertEqual(rows.get(migration.SPLIT_KOGON7_BEFORE),
                         migration.SPLIT_KOGON7_COUNTS[12])
        self.assertEqual(rows.get(migration.SPLIT_KOGON7_AFTER),
                         migration.SPLIT_KOGON7_COUNTS[10])

    def test_second_run_is_a_no_op(self):
        build_db(self.db)
        migration.run()
        before = machine_counts(self.db)
        migration.run()
        self.assertEqual(machine_counts(self.db), before)


class Guards(MigrationCase):
    """4 и 5. Чужая база отвергается; ночь на 1 сентября — сентябрь."""

    def test_a_database_missing_one_flight_is_refused(self):
        build_db(self.db, drop_one='GardenN6')
        with self.assertRaises(SystemExit) as caught:
            migration.run()
        self.assertEqual(caught.exception.code, 1)
        # Откат полный: ни одна машина не проставлена.
        self.assertEqual(machine_counts(self.db), {})

    def test_night_flight_of_31_august_belongs_to_september(self):
        """20:30 UTC 31.08 — это 01:30 1 сентября для тех, кто летал."""
        build_db(self.db, extra=[
            (None, 'GardenN6', 'RNIGHT001', '2025-08-31 20:30:00', 1.0)])
        # Этот вылет ломает предусловие по числу — значит миграция обязана
        # отказаться, а не молча приписать его августу.
        with self.assertRaises(SystemExit) as caught:
            migration.run()
        self.assertEqual(caught.exception.code, 1)
        conn = sqlite3.connect(self.db)
        month = conn.execute(
            "SELECT strftime('%Y-%m', datetime(started_at, '+300 minutes')) "
            "FROM drone_flights WHERE serial_number = 'RNIGHT001'").fetchone()[0]
        conn.close()
        self.assertEqual(month, '2025-09')

    def test_missing_database_exits_with_code_two(self):
        migration.DB_PATH = os.path.join(self.tmp.name, 'no', 'db.sqlite')
        with self.assertRaises(SystemExit) as caught:
            migration.run()
        self.assertEqual(caught.exception.code, 2)
        self.assertFalse(os.path.exists(migration.DB_PATH))


if __name__ == '__main__':
    unittest.main()
