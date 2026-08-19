# -*- coding: utf-8 -*-
"""DRONES_WORKS_SEPT2025_REDATE_002: четыре строки книги Холмуродова, даты.

Миграция переводит 587.50 га с октябрьских дат на сентябрьские и НЕ меняет
оператора: гектары в книге писались на оператора, у Холмуродова 879.50 га
(решение владельца 2026-08-19, отменяющее вывод REDATE_001).

Она обязана привести базу к правильному виду из ЛЮБОГО из двух состояний,
потому что REDATE_001 могла быть уже применена:

  A -- REDATE_001 не применялась: даты октябрьские, оператор Холмуродов;
  B -- REDATE_001 применена: даты сентябрьские, три строки у Анварова.

Проверяются четыре пути устава -- чистая база, повтор, база отсутствует,
непройденное предусловие -- оба состояния, и отдельно то, чего миграция
делать НЕ должна: трогать date_raw, customer_raw и чужие строки. У каждой
проверки есть отрицательный контроль, потому что проверка, дающая одинаковый
ответ при верном и неверном коде, проверкой не является.
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
import migrate_drones_works_sept2025_redate_002 as mig  # noqa: E402

SCHEMA = """
CREATE TABLE drone_operators (id INTEGER PRIMARY KEY, full_name VARCHAR(200));
CREATE TABLE drone_works (
    id INTEGER PRIMARY KEY AUTOINCREMENT, period_month VARCHAR(7) NOT NULL,
    work_date_from DATE, work_date_to DATE, date_raw VARCHAR(100),
    drone_operator_id INTEGER, customer_raw VARCHAR(300) NOT NULL,
    area_ha NUMERIC NOT NULL);
"""

# Состояние A -- как строки лежат в живой базе сегодня.
TARGETS_A = (
    ('Розия Бекова', 25.0, '2025-10-20', '2025-10-20', '2025-10-20', 1),
    ('Анвар Ота замини', 27.5, '2025-10-23', '2025-10-23', '2025-10-23', 1),
    ('Умарбобо Усмонов', 23.5, '2025-10-27', '2025-10-27', '2025-10-27', 1),
    ('Бухоро Агрокластер Заминлари', 511.5, '2025-10-19', '2025-10-30',
     '19-30.10.2025', 1),
)
# Состояние B -- как их оставила бы REDATE_001: даты сентябрьские, три
# строки у Анварова (id 2), четвёртая осталась у Холмуродова (id 1).
TARGETS_B = (
    ('Розия Бекова', 25.0, '2025-09-20', '2025-09-20', '2025-10-20', 2),
    ('Анвар Ота замини', 27.5, '2025-09-23', '2025-09-23', '2025-10-23', 2),
    ('Умарбобо Усмонов', 23.5, '2025-09-27', '2025-09-27', '2025-10-27', 2),
    ('Бухоро Агрокластер Заминлари', 511.5, '2025-09-19', '2025-09-30',
     '19-30.10.2025', 1),
)
# [REASON]: «Розия Бекова фх» на 34.5 га 05.09 -- ДРУГАЯ строка того же
# заказчика у того же оператора. Если опознавать строку по одному имени,
# правка заденет и её.
NEIGHBOURS = (
    ('Розия Бекова фх', 34.5, '2025-09-05', '2025-09-05', 1),
    # [REASON]: ТОТ ЖЕ customer_raw, что и у целевой строки, но другая
    # площадь. Без неё «ищем по имени» и «ищем по имени и площади» дают
    # одинаковый результат, и проверка на площадь ничего не проверяет.
    ('Розия Бекова', 12.0, '2025-09-11', '2025-09-11', 1),
    ('Арк Эко кластер', 45.0, '2025-09-16', '2025-09-16', 1),
    ('Шамси Зиё фх', 9.0, '2025-09-17', '2025-09-17', 2),
)


class Fixture(object):

    def __init__(self, targets=TARGETS_A, with_targets=True):
        self.dir = tempfile.mkdtemp(prefix='redate002_')
        self.path = os.path.join(self.dir, 'transport.db')
        con = sqlite3.connect(self.path)
        con.executescript(SCHEMA)
        con.execute("INSERT INTO drone_operators (id, full_name) VALUES "
                    "(1, 'Холмуродов Шахзод'), (2, 'Анваров Усмон')")
        if with_targets:
            for customer, area, dfrom, dto, raw, op in targets:
                con.execute(
                    'INSERT INTO drone_works (period_month, work_date_from, '
                    'work_date_to, date_raw, drone_operator_id, '
                    'customer_raw, area_ha) VALUES (?, ?, ?, ?, ?, ?, ?)',
                    ('2025-09', dfrom, dto, raw, op, customer, area))
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

    def run_capture(self, apply_changes=True):
        """Возвращает (код выхода, весь вывод).

        [REASON]: и предусловие, и постусловие завершаются кодом 1. Проверка
        "код равен 1" одинаково пройдёт и когда предусловие сработало, и
        когда оно молча пропустило испорченную строку, а поймало её уже
        постусловие. Поэтому отказ обязан быть опознан по тексту.
        """
        import io as _io
        import contextlib
        buffer = _io.StringIO()
        code = None
        try:
            with contextlib.redirect_stdout(buffer):
                self.run(apply_changes)
        except SystemExit as exc:
            code = exc.code
        return code, buffer.getvalue()

    def query(self, sql, params=()):
        con = sqlite3.connect(self.path)
        try:
            return con.execute(sql, params).fetchall()
        finally:
            con.close()

    def rows(self):
        return self.query('SELECT id, customer_raw, area_ha, work_date_from, '
                          'work_date_to, date_raw, drone_operator_id '
                          'FROM drone_works ORDER BY id')

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

    def registry(self):
        names = self.query("SELECT name FROM sqlite_master WHERE type='table'"
                           " AND name='schema_migrations'")
        if not names:
            return []
        return [r[0] for r in self.query('SELECT name FROM schema_migrations')]

    def close(self):
        shutil.rmtree(self.dir, ignore_errors=True)


class Base(unittest.TestCase):

    targets = TARGETS_A

    def setUp(self):
        self._real_db = migration_utils.DB_PATH
        self._real_mig_db = mig.DB_PATH
        self.fx = Fixture(targets=self.targets)

    def tearDown(self):
        migration_utils.DB_PATH = self._real_db
        mig.DB_PATH = self._real_mig_db
        self.fx.close()


class StateATest(Base):
    """REDATE_001 не применялась: правятся только даты."""

    targets = TARGETS_A

    def test_dry_run_writes_nothing(self):
        before = self.fx.rows()
        self.assertEqual(0, self.fx.run(apply_changes=False))
        self.assertEqual(before, self.fx.rows())
        self.assertEqual([], self.fx.registry())

    def test_apply_moves_587_5_from_october_to_september(self):
        sept, october = self.fx.month('2025-09'), self.fx.month('2025-10')
        self.assertEqual(0, self.fx.run())
        self.assertAlmostEqual(sept + 587.5, self.fx.month('2025-09'), 2)
        self.assertAlmostEqual(october - 587.5, self.fx.month('2025-10'), 2)
        self.assertIn(mig.MIGRATION_ID, self.fx.registry())

    def test_the_operator_is_not_touched(self):
        """Главное отличие от REDATE_001: 76.00 га НЕ уходят Анварову."""
        anvarov = self.fx.operator('Анваров Усмон')
        kholm = self.fx.operator('Холмуродов Шахзод')
        self.assertEqual(0, self.fx.run())
        self.assertAlmostEqual(anvarov, self.fx.operator('Анваров Усмон'), 2)
        self.assertAlmostEqual(kholm + 587.5,
                               self.fx.operator('Холмуродов Шахзод'), 2)
        # Все четыре строки по-прежнему за Холмуродовым.
        for customer, area, _f, _t, _raw, _op in TARGETS_A:
            got = self.fx.query('SELECT drone_operator_id FROM drone_works '
                                'WHERE customer_raw = ? AND '
                                'ABS(area_ha - ?) < 0.005', (customer, area))
            self.assertEqual([(1,)], got, customer)

    def test_date_raw_and_customer_raw_survive(self):
        """Исходные клетки книги -- вещественное доказательство разбора."""
        before = {r[1]: (r[2], r[5]) for r in self.fx.rows()}
        self.assertEqual(0, self.fx.run())
        after = {r[1]: (r[2], r[5]) for r in self.fx.rows()}
        self.assertEqual(before, after)

    def test_neighbours_are_untouched(self):
        """«Розия Бекова фх» 34.5 га 05.09 -- другая строка того же имени."""
        # [REASON]: отбор по ИМЕНИ И ПЛОЩАДИ, а не по одному имени -- среди
        # соседей есть «Розия Бекова» 12.0 га, тёзка целевой строки.
        targets = {(t[0], t[1]) for t in TARGETS_A}
        before = [r for r in self.fx.rows()
                  if (r[1], float(r[2])) not in targets]
        self.assertEqual(0, self.fx.run())
        after = [r for r in self.fx.rows()
                 if (r[1], float(r[2])) not in targets]
        self.assertEqual(before, after)
        self.assertEqual(4, len(after))

    def test_second_run_is_a_no_op(self):
        self.assertEqual(0, self.fx.run())
        snapshot = self.fx.rows()
        self.assertEqual(0, self.fx.run())
        self.assertEqual(snapshot, self.fx.rows())
        self.assertEqual(1, len(self.fx.registry()))


class StateBTest(Base):
    """REDATE_001 применена: возвращается оператор, месяцы не меняются."""

    targets = TARGETS_B

    def test_apply_returns_the_rows_to_kholmurodov(self):
        kholm = self.fx.operator('Холмуродов Шахзод')
        anvarov = self.fx.operator('Анваров Усмон')
        sept, october = self.fx.month('2025-09'), self.fx.month('2025-10')
        self.assertEqual(0, self.fx.run())
        self.assertAlmostEqual(kholm + 76.0,
                               self.fx.operator('Холмуродов Шахзод'), 2)
        self.assertAlmostEqual(anvarov - 76.0,
                               self.fx.operator('Анваров Усмон'), 2)
        # Итоги месяцев не двигаются -- даты уже сентябрьские.
        self.assertAlmostEqual(sept, self.fx.month('2025-09'), 2)
        self.assertAlmostEqual(october, self.fx.month('2025-10'), 2)

    def test_dates_stay_september(self):
        self.assertEqual(0, self.fx.run())
        for customer, area, dfrom, dto, _raw, _op in TARGETS_B:
            got = self.fx.query('SELECT work_date_from, work_date_to FROM '
                                'drone_works WHERE customer_raw = ? AND '
                                'ABS(area_ha - ?) < 0.005', (customer, area))
            self.assertEqual([(dfrom, dto)], got, customer)

    def test_dry_run_writes_nothing(self):
        before = self.fx.rows()
        self.assertEqual(0, self.fx.run(apply_changes=False))
        self.assertEqual(before, self.fx.rows())

    def test_a_wrong_postcondition_rolls_state_b_back_too(self):
        """Отрицательный контроль на постусловие ИМЕННО состояния B.

        [REASON]: у состояний A и B разные наборы постусловий. Контроль на
        одном из них ничего не говорит про другой: сеть, натянутая только
        над путём A, оставляет путь B без охраны.
        """
        before = self.fx.rows()
        original = mig.RESTORED_HA
        mig.RESTORED_HA = 10.0
        try:
            code, out = self.fx.run_capture()
        finally:
            mig.RESTORED_HA = original
        self.assertEqual(1, code, out)
        self.assertIn('postcondition failed', out)
        self.assertEqual(before, self.fx.rows())
        self.assertEqual([], self.fx.registry())


class RefusalTest(Base):
    """Пути отказа. Каждый -- с отрицательным контролем на самой проверке."""

    def test_missing_database_gives_code_two_and_creates_nothing(self):
        absent = os.path.join(self.fx.dir, 'nope.db')
        with self.assertRaises(SystemExit) as caught:
            mig.main(['--db', absent, '--apply'])
        self.assertEqual(2, caught.exception.code)
        self.assertFalse(os.path.exists(absent))

    def test_a_hand_edited_row_is_refused(self):
        """Третье состояние -- значит базу правили руками. Не трогаем."""
        con = sqlite3.connect(self.fx.path)
        con.execute("UPDATE drone_works SET work_date_from = '2025-11-20', "
                    "work_date_to = '2025-11-20' WHERE customer_raw = "
                    "'Розия Бекова' AND ABS(area_ha - 25.0) < 0.005")
        con.commit()
        con.close()
        before = self.fx.rows()
        code, out = self.fx.run_capture()
        self.assertEqual(1, code, out)
        self.assertIn('neither known state', out)
        self.assertEqual(before, self.fx.rows())
        self.assertEqual([], self.fx.registry())

    def test_mixed_states_are_refused(self):
        """Часть строк по REDATE_001, часть нет -- значит кто-то вмешался."""
        con = sqlite3.connect(self.fx.path)
        con.execute("UPDATE drone_works SET work_date_from = '2025-09-23', "
                    "work_date_to = '2025-09-23', drone_operator_id = 2 "
                    "WHERE customer_raw = 'Анвар Ота замини'")
        con.commit()
        con.close()
        before = self.fx.rows()
        code, out = self.fx.run_capture()
        self.assertEqual(1, code, out)
        self.assertIn('MIXED states', out)
        self.assertEqual(before, self.fx.rows())

    def test_a_missing_row_is_refused(self):
        con = sqlite3.connect(self.fx.path)
        con.execute("DELETE FROM drone_works WHERE customer_raw = "
                    "'Умарбобо Усмонов'")
        con.commit()
        con.close()
        before = self.fx.rows()
        code, out = self.fx.run_capture()
        self.assertEqual(1, code, out)
        self.assertIn('expected exactly 1 row', out)
        self.assertEqual(before, self.fx.rows())

    def test_a_duplicated_row_is_refused(self):
        """Две строки с тем же заказчиком и площадью -- какую править?"""
        con = sqlite3.connect(self.fx.path)
        con.execute('INSERT INTO drone_works (period_month, work_date_from, '
                    'work_date_to, date_raw, drone_operator_id, '
                    'customer_raw, area_ha) VALUES '
                    "('2025-09', '2025-10-23', '2025-10-23', '2025-10-23', "
                    "1, 'Анвар Ота замини', 27.5)")
        con.commit()
        con.close()
        before = self.fx.rows()
        code, out = self.fx.run_capture()
        self.assertEqual(1, code, out)
        self.assertIn('expected exactly 1 row', out)
        self.assertEqual(before, self.fx.rows())

    def test_the_namesake_row_with_another_area_is_not_touched(self):
        """«Розия Бекова» 12.0 га -- тот же заказчик, другая работа.

        [REASON]: правка по одному имени задела бы её. Проверяется и то, что
        целевая строка всё-таки поправлена: иначе тест прошёл бы и на
        миграции, которая не делает вообще ничего.
        """
        self.assertEqual(0, self.fx.run())
        namesake = self.fx.query(
            'SELECT work_date_from, work_date_to, drone_operator_id '
            'FROM drone_works WHERE customer_raw = ? '
            'AND ABS(area_ha - 12.0) < 0.005', ('Розия Бекова',))
        self.assertEqual([('2025-09-11', '2025-09-11', 1)], namesake)
        target = self.fx.query(
            'SELECT work_date_from FROM drone_works WHERE customer_raw = ? '
            'AND ABS(area_ha - 25.0) < 0.005', ('Розия Бекова',))
        self.assertEqual([('2025-09-20',)], target)

    def test_a_wrong_postcondition_would_roll_the_whole_thing_back(self):
        """Отрицательный контроль на само постусловие.

        [REASON]: если бы постусловие не считало дельты, миграция прошла бы
        на данных, где переехало не 587.50 га. Здесь ожидаемая дельта
        подменяется -- и миграция обязана откатиться целиком.
        """
        before = self.fx.rows()
        original = mig.REDATED_HA
        mig.REDATED_HA = 500.0
        try:
            code, out = self.fx.run_capture()
        finally:
            mig.REDATED_HA = original
        self.assertEqual(1, code, out)
        self.assertIn('postcondition failed', out)
        self.assertEqual(before, self.fx.rows())
        self.assertEqual([], self.fx.registry())


class RollbackTest(Base):
    """Напечатанный откат обязан ВОЗВРАЩАТЬ базу, а не выглядеть правильно."""

    def _printed_rollback(self):
        import io
        import contextlib
        buffer = io.StringIO()
        with contextlib.redirect_stdout(buffer):
            self.fx.run()
        statements, collecting = [], False
        for line in buffer.getvalue().splitlines():
            if line.startswith('ROLLBACK OF DATA'):
                collecting = True
                continue
            if collecting and line.strip():
                statements.append(line.strip())
        return statements

    def test_printed_rollback_restores_the_book(self):
        before = self.fx.rows()
        statements = self._printed_rollback()
        self.assertTrue(statements, 'откат не напечатан')
        self.assertNotEqual(before, self.fx.rows())
        con = sqlite3.connect(self.fx.path)
        try:
            for statement in statements:
                con.execute(statement)
            con.commit()
        finally:
            con.close()
        self.assertEqual(before, self.fx.rows())
        self.assertEqual([], self.fx.registry())

    def test_the_migration_runs_again_after_its_own_rollback(self):
        statements = self._printed_rollback()
        after_apply = self.fx.rows()
        con = sqlite3.connect(self.fx.path)
        try:
            for statement in statements:
                con.execute(statement)
            con.commit()
        finally:
            con.close()
        self.assertEqual(0, self.fx.run())
        self.assertEqual(after_apply, self.fx.rows())


if __name__ == '__main__':
    unittest.main()
