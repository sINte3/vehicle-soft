# -*- coding: utf-8 -*-
"""DRONES_REATTACH_BODYCODE_001: переносит ровно то, что доказано, и ничего сверх.

Проверяется четыре вещи, и к каждой — отрицательный контроль, потому что
миграция, которая «прошла успешно» на любой базе, ничего не гарантирует:

  1. На ожидаемой базе переезжают ровно 392 вылета, и после этого месячный
     итог каждого затронутого борта равен числу djiag.com. Отрицательный
     контроль: до миграции те же итоги НЕ равны — иначе проверка не отличала
     бы починенную базу от сломанной.
  2. Чужие месяцы не трогаются: апрель–июль сходятся и без миграции, значит
     ни один их вылет не должен сменить машину.
  3. Предусловие ловит базу, которую уже правили руками: один заранее
     переставленный вылет -> отказ, полный откат, реестр пуст.
  4. Месяц вылета считается в UTC+5: вылет 31.03 в 20:30 UTC принадлежит
     апрелю и НЕ должен переехать вместе с мартовскими.

Run:
  python -m unittest tests.test_drone_reattach_bodycode_001 -v
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
import migrate_drones_reattach_bodycode_001 as migration  # noqa: E402

# (месяц, написание, машина сейчас, вылетов, гектаров) — то, что должно
# лежать в базе ДО миграции. Числа взяты из выгрузки прода 2026-08-13.
BEFORE = [
    ('2025-10', '12 Peshku', 12, 185, 201.98),
    ('2025-10', '14 Servis', 14, 76, 87.96),
    ('2025-10', '13 Servis', 13, 67, 47.92),
    ('2025-10', '3 Gijduvon', 3, 327, 386.05),
    ('2026-03', '12 Peshku', 12, 37, 42.17),
    ('2026-03', '13 Servis', 13, 27, 23.49),
    ('2026-03', '13 Peshku', 13, 259, 255.26),
    ('2026-03', '15 Servis', 15, 131, 116.05),
    ('2026-03', '12 Servis', 12, 101, 99.64),
    # Чужой месяц: апрель сходится сам, миграция не должна его касаться.
    ('2026-04', '14 Servis', 14, 516, 479.50),
]


def build_db(path, extra=None):
    """Синтетическая база из двух таблиц, что читает миграция."""
    conn = sqlite3.connect(path)
    conn.execute('CREATE TABLE drone_units (id INTEGER PRIMARY KEY, '
                 'number INTEGER UNIQUE, hardware_id TEXT)')
    for number in range(1, 16):
        conn.execute('INSERT INTO drone_units (number) VALUES (?)', (number,))
    conn.execute('CREATE TABLE drone_flights (id INTEGER PRIMARY KEY, '
                 'drone_unit_id INTEGER, nickname_raw TEXT, '
                 'serial_number TEXT, started_at TEXT, area_ha REAL)')
    serial = 0
    for month, nickname, unit, flights, hectares in BEFORE:
        # Полдень по UTC+5 -- вылет заведомо не гуляет по границе суток.
        day = datetime.datetime.strptime(month + '-15', '%Y-%m-%d')
        stamp = (day + datetime.timedelta(hours=7)).strftime('%Y-%m-%d %H:%M:%S')
        for _ in range(flights):
            serial += 1
            conn.execute(
                'INSERT INTO drone_flights (drone_unit_id, nickname_raw, '
                'serial_number, started_at, area_ha) VALUES (?, ?, ?, ?, ?)',
                (unit, nickname, 'R%08d' % serial, stamp, hectares / flights))
    for row in (extra or []):
        conn.execute(
            'INSERT INTO drone_flights (drone_unit_id, nickname_raw, '
            'serial_number, started_at, area_ha) VALUES (?, ?, ?, ?, ?)', row)
    conn.commit()
    conn.close()


def month_totals(path, month, number):
    conn = sqlite3.connect(path)
    row = conn.execute(
        "SELECT COUNT(*), COALESCE(ROUND(SUM(area_ha), 4), 0) "
        "FROM drone_flights f JOIN drone_units u ON u.id = f.drone_unit_id "
        "WHERE u.number = ? AND strftime('%Y-%m', "
        "      datetime(f.started_at, '+300 minutes')) = ?",
        (number, month)).fetchone()
    conn.close()
    return row


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

    def applied(self):
        conn = sqlite3.connect(self.db)
        try:
            names = [r[0] for r in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table' "
                "AND name='schema_migrations'")]
            if not names:
                return []
            return [r[0] for r in conn.execute(
                'SELECT name FROM schema_migrations')]
        finally:
            conn.close()


class HappyPath(MigrationCase):
    """1 и 2. Переезжает ровно доказанное; чужие месяцы не трогаются."""

    def test_moves_and_postconditions(self):
        build_db(self.db)
        # Отрицательный контроль: ДО миграции борта не сходятся с DJI.
        self.assertEqual(month_totals(self.db, '2025-10', 13)[0], 67)
        self.assertNotEqual(month_totals(self.db, '2025-10', 13)[0], 185)

        migration.run()

        self.assertIn(migration.MIGRATION_ID, self.applied())
        for month in sorted(migration.DJI_AFTER):
            for number, (want_f, want_h) in migration.DJI_AFTER[month].items():
                got_f, got_h = month_totals(self.db, month, number)
                self.assertEqual(got_f, want_f,
                                 '%s машина %d' % (month, number))
                self.assertLessEqual(abs(got_h - want_h), migration.HA_TOL,
                                     '%s машина %d' % (month, number))

    def test_other_months_are_untouched(self):
        build_db(self.db)
        before = month_totals(self.db, '2026-04', 14)
        migration.run()
        self.assertEqual(month_totals(self.db, '2026-04', 14), before)
        self.assertEqual(before[0], 516)

    def test_second_run_changes_nothing(self):
        build_db(self.db)
        migration.run()
        snapshot = month_totals(self.db, '2025-10', 13)
        migration.run()
        self.assertEqual(month_totals(self.db, '2025-10', 13), snapshot)


class Guards(MigrationCase):
    """3 и 4. Отказ на правленой базе; ночной вылет не уезжает с мартом."""

    def test_hand_edited_database_is_refused_and_rolled_back(self):
        build_db(self.db)
        conn = sqlite3.connect(self.db)
        victim = conn.execute(
            "SELECT id FROM drone_flights WHERE nickname_raw = '12 Peshku' "
            "AND drone_unit_id = 12 LIMIT 1").fetchone()[0]
        conn.execute('UPDATE drone_flights SET drone_unit_id = 13 WHERE id = ?',
                     (victim,))
        conn.commit()
        conn.close()

        with self.assertRaises(SystemExit) as caught:
            migration.run()
        self.assertEqual(caught.exception.code, 1)
        self.assertNotIn(migration.MIGRATION_ID, self.applied())
        # Откат полный: на №13 осталась только подпорченная строка.
        conn = sqlite3.connect(self.db)
        left = conn.execute(
            "SELECT COUNT(*) FROM drone_flights WHERE nickname_raw = "
            "'12 Peshku' AND drone_unit_id = 13").fetchone()[0]
        conn.close()
        self.assertEqual(left, 1)

    def test_night_flight_of_31_march_belongs_to_april_and_stays(self):
        """20:30 UTC 31.03 -- это 01:30 1 апреля, значит не мартовский вылет."""
        build_db(self.db, extra=[
            (13, '13 Servis', 'RNIGHT001', '2026-03-31 20:30:00', 1.0)])
        migration.run()
        conn = sqlite3.connect(self.db)
        unit = conn.execute(
            "SELECT drone_unit_id FROM drone_flights "
            "WHERE serial_number = 'RNIGHT001'").fetchone()[0]
        conn.close()
        # Остался на 13: миграция трогает только месяц 2026-03 по UTC+5.
        self.assertEqual(unit, 13)

    def test_missing_database_exits_with_code_two(self):
        migration.DB_PATH = os.path.join(self.tmp.name, 'no', 'db.sqlite')
        with self.assertRaises(SystemExit) as caught:
            migration.run()
        self.assertEqual(caught.exception.code, 2)
        self.assertFalse(os.path.exists(migration.DB_PATH))


class Constants(unittest.TestCase):
    """Константы согласованы между собой и с арифметикой переносов."""

    def test_moved_rows_match_the_declared_total(self):
        self.assertEqual(sum(row[4] for row in migration.MOVES), 392)
        self.assertAlmostEqual(sum(row[5] for row in migration.MOVES),
                               403.52, places=2)

    def test_every_target_machine_appears_in_the_dji_check(self):
        for month, _nick, _old, new, _f, _h in migration.MOVES:
            self.assertIn(new, migration.DJI_AFTER[month],
                          'машина %d в %s не проверяется постусловием'
                          % (new, month))

    def test_october_targets_reproduce_the_moved_numbers(self):
        self.assertEqual(migration.DJI_AFTER['2025-10'][13], (185, 201.98))
        self.assertEqual(migration.DJI_AFTER['2025-10'][14], (0, 0.0))


if __name__ == '__main__':
    unittest.main()
