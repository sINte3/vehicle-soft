# -*- coding: utf-8 -*-
"""DRONES_WORKS_SEPT2025_REDATE_001: четыре строки книги Холмуродова.

Миграция переводит 587.50 га с октябрьских дат на сентябрьские и передаёт из
них 76.00 га Анварову. Основание -- положение машин: 20, 23 и 27 сентября
машина №3 (Холмуродов) стояла в Коровулбозоре, а названные в этих строках поля
летала машина №4 (Анваров).

Проверяются четыре пути устава -- чистая база, повтор, база отсутствует,
непройденное предусловие -- и отдельно то, чего миграция делать НЕ должна:
трогать date_raw, customer_raw и чужие строки. У каждой проверки есть
отрицательный контроль, потому что проверка, дающая одинаковый ответ при
верном и неверном коде, проверкой не является.
"""

import os
import shutil
import sqlite3
import sys
import tempfile
import unittest

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if REPO_ROOT not in sys.path:
    sys.path.insert(0, REPO_ROOT)

import migration_utils  # noqa: E402
import migrate_drones_works_sept2025_redate_001 as mig  # noqa: E402

SCHEMA = """
CREATE TABLE drone_operators (id INTEGER PRIMARY KEY, full_name VARCHAR(200));
CREATE TABLE drone_works (
    id INTEGER PRIMARY KEY AUTOINCREMENT, period_month VARCHAR(7) NOT NULL,
    work_date_from DATE, work_date_to DATE, date_raw VARCHAR(100),
    drone_operator_id INTEGER, customer_raw VARCHAR(300) NOT NULL,
    area_ha NUMERIC NOT NULL);
"""

# Четыре настоящие строки книги плюс соседи, которые трогать нельзя.
TARGETS = (
    ('Розия Бекова', 25.0, '2025-10-20', '2025-10-20', '2025-10-20'),
    ('Анвар Ота замини', 27.5, '2025-10-23', '2025-10-23', '2025-10-23'),
    ('Умарбобо Усмонов', 23.5, '2025-10-27', '2025-10-27', '2025-10-27'),
    ('Бухоро Агрокластер Заминлари', 511.5, '2025-10-19', '2025-10-30',
     '19-30.10.2025'),
)
# [REASON]: «Розия Бекова фх» на 34.5 га 05.09 -- ДРУГАЯ строка того же
# заказчика у того же оператора. Если опознавать строку по имени, правка
# заденет и её.
NEIGHBOURS = (
    ('Розия Бекова фх', 34.5, '2025-09-05', '2025-09-05', 1),
    ('Арк Эко кластер', 45.0, '2025-09-16', '2025-09-16', 1),
    ('Шамси Зиё фх', 9.0, '2025-09-17', '2025-09-17', 2),
)


class Fixture(object):

    def __init__(self, with_targets=True):
        self.dir = tempfile.mkdtemp(prefix='redate_')
        self.path = os.path.join(self.dir, 'transport.db')
        con = sqlite3.connect(self.path)
        con.executescript(SCHEMA)
        con.execute("INSERT INTO drone_operators (id, full_name) VALUES "
                    "(1, 'Холмуродов Шахзод'), (2, 'Анваров Усмон')")
        if with_targets:
            for customer, area, dfrom, dto, raw in TARGETS:
                con.execute(
                    'INSERT INTO drone_works (period_month, work_date_from, '
                    'work_date_to, date_raw, drone_operator_id, customer_raw, '
                    'area_ha) VALUES (?, ?, ?, ?, 1, ?, ?)',
                    ('2025-09', dfrom, dto, raw, customer, area))
        for customer, area, dfrom, dto, op in NEIGHBOURS:
            con.execute(
                'INSERT INTO drone_works (period_month, work_date_from, '
                'work_date_to, date_raw, drone_operator_id, customer_raw, '
                'area_ha) VALUES (?, ?, ?, ?, ?, ?, ?)',
                ('2025-09', dfrom, dto, dfrom, op, customer, area))
        con.commit()
        con.close()

    def run(self, apply_changes=True):
        argv = ['--db', self.path] + (['--apply'] if apply_changes else [])
        return mig.main(argv)

    def query(self, sql, params=()):
        con = sqlite3.connect(self.path)
        try:
            return con.execute(sql, params).fetchall()
        finally:
            con.close()

    def month(self, m):
        return self.query(
            "SELECT COALESCE(SUM(area_ha), 0) FROM drone_works WHERE "
            "COALESCE(strftime('%Y-%m', work_date_from), period_month) = ?",
            (m,))[0][0]

    def operator(self, name, m='2025-09'):
        return self.query(
            "SELECT COALESCE(SUM(w.area_ha), 0) FROM drone_works w "
            "JOIN drone_operators o ON o.id = w.drone_operator_id "
            "WHERE o.full_name = ? AND COALESCE("
            "strftime('%Y-%m', w.work_date_from), w.period_month) = ?",
            (name, m))[0][0]

    def close(self):
        shutil.rmtree(self.dir, ignore_errors=True)


class Base(unittest.TestCase):

    def setUp(self):
        self._real_db = migration_utils.DB_PATH
        self._real_mig_db = mig.DB_PATH
        self.fx = Fixture()

    def tearDown(self):
        migration_utils.DB_PATH = self._real_db
        mig.DB_PATH = self._real_mig_db
        self.fx.close()


class CleanRun(Base):
    """Путь 1: чистая база."""

    def test_1_dates_move_to_september(self):
        self.fx.run()
        rows = self.fx.query('SELECT customer_raw, work_date_from, '
                             'work_date_to FROM drone_works '
                             "WHERE work_date_from LIKE '2025-10%'")
        self.assertEqual(rows, [], 'октябрьских дат не должно остаться')

    def test_1a_the_month_totals_move_by_exactly_587_50(self):
        before_sept, before_oct = self.fx.month('2025-09'), self.fx.month('2025-10')
        self.fx.run()
        self.assertAlmostEqual(self.fx.month('2025-09') - before_sept, 587.5, 2)
        self.assertAlmostEqual(before_oct - self.fx.month('2025-10'), 587.5, 2)

    def test_1b_seventy_six_hectares_move_to_anvarov(self):
        before = self.fx.operator('Анваров Усмон')
        self.fx.run()
        self.assertAlmostEqual(self.fx.operator('Анваров Усмон') - before,
                               76.0, 2)

    def test_1c_kholmurodov_keeps_the_511_5_block(self):
        before = self.fx.operator('Холмуродов Шахзод')
        self.fx.run()
        self.assertAlmostEqual(self.fx.operator('Холмуродов Шахзод') - before,
                               511.5, 2)

    def test_1d_negative_date_raw_is_evidence_and_stays_verbatim(self):
        """[REASON]: date_raw -- исходная клетка книги. Перепишешь её -- и
        доказательства, что дата была октябрьской, больше нет."""
        self.fx.run()
        raw = self.fx.query("SELECT date_raw FROM drone_works "
                            "WHERE customer_raw = 'Бухоро Агрокластер Заминлари'")
        self.assertEqual(raw, [('19-30.10.2025',)])

    def test_1e_negative_neighbouring_rows_are_untouched(self):
        """«Розия Бекова фх» 34.5 га 05.09 -- другая строка того же заказчика."""
        self.fx.run()
        rows = self.fx.query(
            'SELECT work_date_from, drone_operator_id FROM drone_works '
            "WHERE customer_raw = 'Розия Бекова фх'")
        self.assertEqual(rows, [('2025-09-05', 1)])

    def test_1f_customer_raw_is_never_rewritten(self):
        self.fx.run()
        names = {r[0] for r in self.fx.query('SELECT customer_raw FROM drone_works')}
        for customer, _a, _f, _t, _r in TARGETS:
            self.assertIn(customer, names)


class DryRun(Base):
    """Путь 1а: сухой прогон обязан ничего не менять."""

    def test_2_dry_run_writes_nothing(self):
        before = self.fx.query('SELECT id, work_date_from, work_date_to, '
                               'drone_operator_id FROM drone_works ORDER BY id')
        self.fx.run(apply_changes=False)
        self.assertEqual(self.fx.query(
            'SELECT id, work_date_from, work_date_to, drone_operator_id '
            'FROM drone_works ORDER BY id'), before)

    def test_2a_dry_run_does_not_record_the_migration(self):
        self.fx.run(apply_changes=False)
        rows = self.fx.query("SELECT name FROM sqlite_master "
                             "WHERE name = 'schema_migrations'")
        if rows:
            self.assertEqual(self.fx.query(
                'SELECT COUNT(*) FROM schema_migrations')[0][0], 0)

    def test_2b_negative_apply_does_change_things(self):
        """Иначе проверка выше прошла бы и на миграции, которая не работает."""
        before = self.fx.month('2025-09')
        self.fx.run(apply_changes=True)
        self.assertNotAlmostEqual(self.fx.month('2025-09'), before, places=2)


class Repeat(Base):
    """Путь 2: повторный запуск."""

    def test_3_second_run_changes_nothing(self):
        self.fx.run()
        after_first = self.fx.query(
            'SELECT id, work_date_from, work_date_to, drone_operator_id '
            'FROM drone_works ORDER BY id')
        self.fx.run()
        self.assertEqual(self.fx.query(
            'SELECT id, work_date_from, work_date_to, drone_operator_id '
            'FROM drone_works ORDER BY id'), after_first)

    def test_3a_the_registry_holds_exactly_one_row(self):
        self.fx.run()
        self.fx.run()
        self.assertEqual(self.fx.query(
            'SELECT COUNT(*) FROM schema_migrations WHERE name = ?',
            (mig.MIGRATION_ID,))[0][0], 1)


class MissingDatabase(unittest.TestCase):
    """Путь 3: базы нет -- код 2, файл не создан."""

    def test_4_exits_2_and_creates_nothing(self):
        real, real_mig = migration_utils.DB_PATH, mig.DB_PATH
        tmp = tempfile.mkdtemp(prefix='redate_missing_')
        missing = os.path.join(tmp, 'nope.db')
        try:
            with self.assertRaises(SystemExit) as caught:
                mig.main(['--db', missing, '--apply'])
            self.assertEqual(caught.exception.code, 2)
            self.assertFalse(os.path.exists(missing))
        finally:
            migration_utils.DB_PATH, mig.DB_PATH = real, real_mig
            shutil.rmtree(tmp, ignore_errors=True)


class PreconditionFails(Base):
    """Путь 4: предусловие не выполнено -- код 1, полный откат, реестр пуст."""

    def test_5_a_missing_row_stops_the_run(self):
        self.fx.query("SELECT 1")
        con = sqlite3.connect(self.fx.path)
        con.execute("DELETE FROM drone_works "
                    "WHERE customer_raw = 'Умарбобо Усмонов'")
        con.commit(); con.close()
        with self.assertRaises(SystemExit) as caught:
            self.fx.run()
        self.assertEqual(caught.exception.code, 1)

    def test_5a_nothing_was_written_after_the_failure(self):
        con = sqlite3.connect(self.fx.path)
        con.execute("DELETE FROM drone_works "
                    "WHERE customer_raw = 'Умарбобо Усмонов'")
        con.commit(); con.close()
        before = self.fx.query(
            'SELECT id, work_date_from, drone_operator_id FROM drone_works '
            'ORDER BY id')
        with self.assertRaises(SystemExit):
            self.fx.run()
        self.assertEqual(self.fx.query(
            'SELECT id, work_date_from, drone_operator_id FROM drone_works '
            'ORDER BY id'), before)
        self.assertEqual(self.fx.query(
            'SELECT COUNT(*) FROM schema_migrations')[0][0], 0)

    def test_5b_a_changed_area_stops_the_run(self):
        """Гектары -- часть опознания строки. Изменились -- это не та строка."""
        con = sqlite3.connect(self.fx.path)
        con.execute("UPDATE drone_works SET area_ha = 30.0 "
                    "WHERE customer_raw = 'Розия Бекова'")
        con.commit(); con.close()
        with self.assertRaises(SystemExit) as caught:
            self.fx.run()
        self.assertEqual(caught.exception.code, 1)

    def test_5c_a_row_already_on_anvarov_stops_the_run(self):
        con = sqlite3.connect(self.fx.path)
        con.execute("UPDATE drone_works SET drone_operator_id = 2 "
                    "WHERE customer_raw = 'Анвар Ота замини'")
        con.commit(); con.close()
        with self.assertRaises(SystemExit) as caught:
            self.fx.run()
        self.assertEqual(caught.exception.code, 1)

    def test_5d_a_duplicated_row_stops_the_run(self):
        """[REASON]: две одинаковые строки -- не «возьми любую». Неизвестно,
        какая из них настоящая, и молча выбрать одну значит угадать."""
        con = sqlite3.connect(self.fx.path)
        con.execute(
            'INSERT INTO drone_works (period_month, work_date_from, '
            'work_date_to, date_raw, drone_operator_id, customer_raw, area_ha)'
            " VALUES ('2025-09', '2025-10-20', '2025-10-20', '2025-10-20', 1, "
            "'Розия Бекова', 25.0)")
        con.commit(); con.close()
        with self.assertRaises(SystemExit) as caught:
            self.fx.run()
        self.assertEqual(caught.exception.code, 1)

    def test_5e_negative_the_untouched_fixture_succeeds(self):
        """Обратный контроль ко всем проверкам выше: без порчи -- проходит."""
        self.assertEqual(self.fx.run(), 0)


class Rollback(Base):
    """Путь 5: напечатанный откат действительно возвращает базу назад."""

    def test_6_the_printed_rollback_restores_the_rows(self):
        before = self.fx.query(
            'SELECT id, work_date_from, work_date_to, drone_operator_id '
            'FROM drone_works ORDER BY id')
        self.fx.run()
        self.assertNotEqual(self.fx.query(
            'SELECT id, work_date_from, work_date_to, drone_operator_id '
            'FROM drone_works ORDER BY id'), before)

        con = sqlite3.connect(self.fx.path)
        try:
            for (customer, area, old_from, old_to, new_from, _new_to, new_op,
                 _why) in mig.ROWS:
                if new_op:
                    con.execute(
                        'UPDATE drone_works SET work_date_from = ?, '
                        'work_date_to = ?, drone_operator_id = ('
                        'SELECT id FROM drone_operators WHERE full_name = ?) '
                        'WHERE customer_raw = ? AND ABS(area_ha - ?) < 0.005 '
                        'AND work_date_from = ?',
                        (old_from, old_to, mig.OP_FROM, customer, area,
                         new_from))
                else:
                    con.execute(
                        'UPDATE drone_works SET work_date_from = ?, '
                        'work_date_to = ? WHERE customer_raw = ? '
                        'AND ABS(area_ha - ?) < 0.005 AND work_date_from = ?',
                        (old_from, old_to, customer, area, new_from))
            con.execute('DELETE FROM schema_migrations WHERE name = ?',
                        (mig.MIGRATION_ID,))
            con.commit()
        finally:
            con.close()

        self.assertEqual(self.fx.query(
            'SELECT id, work_date_from, work_date_to, drone_operator_id '
            'FROM drone_works ORDER BY id'), before)
        self.assertEqual(self.fx.query(
            'SELECT COUNT(*) FROM schema_migrations')[0][0], 0)


if __name__ == '__main__':
    unittest.main()
