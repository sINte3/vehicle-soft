# -*- coding: utf-8 -*-
"""DRONE-FLEET-ASSIGN-001: сентябрь получает операторов и НЕ получает догадок.

К каждой проверке -- отрицательный контроль, потому что загрузчик, который
«отработал успешно» на любой базе, ничего не гарантирует:

  1. Сухой прогон (режим по умолчанию) не пишет НИЧЕГО. Отрицательный
     контроль: тот же прогон с --apply пишет 14 строк.
  2. Повторный --apply вставляет 0: сентябрь уже покрыт.
  3. Уже лежащее пересекающееся назначение не задваивается.
  4. Имена сходятся через гомоглифы (Кобилов/Қобилов). Отрицательный
     контроль: точное сравнение строк этих двоих НЕ свело бы.
  5. Незнакомый оператор -> код 1, полный откат, в базе пусто.
  6. Базы нет -> код 2, файл НЕ создан.
  7. Машина 2 не получает никого -- это решение, а не забытая строка.
  8. У каждой строки в note стоит префикс источника, и он один из двух.
  9. Границы периода -- окно полётов, а не весь месяц.

Run:
  python -m unittest tests.test_drone_assignments_sept2025_001 -v
"""
import os
import sqlite3
import subprocess
import sys
import tempfile
import unittest

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, REPO_ROOT)
import load_drone_assignments_sept2025 as loader  # noqa: E402

SCRIPT = os.path.join(REPO_ROOT, 'load_drone_assignments_sept2025.py')

# Как имена записаны В СПРАВОЧНИКЕ: часть по-узбекски, часть по-русски --
# ровно та смесь, что лежит на проде.
STORED_NAMES = {
    'Рухиллоев Сайфулло': 'Рухиллоев Сайфулло',
    'Холмуродов Шахзод': 'Холмуродов Шахзод',
    'Анваров Усмон': 'Анваров Усмон',
    'Кобилов Фаррух': 'Қобилов Фаррух',
    'Жураев Туйгун': 'Жураев Туйғун',
    'Файзуллаев Шохрух': 'Файзуллаев Шохрух',
    'Кодиров Нурали': 'Қодиров Нурали',
    'Хамроев Шохрух': 'Хамроев Шохрух',
    'Ибодуллаев Хасанбой': 'Ибодуллаев Хасанбой',
    'Кудратов Мухриддин': 'Қудратов Мухриддин',
    'Имомов Бехзод': 'Имомов Беҳзод',
    'Файзиев Шоди': 'Файзиев Шоди',
    'Жумаев Фуркат': 'Жумаев Фуркат',
}


def build_db(path, drop_operator=None):
    conn = sqlite3.connect(path)
    conn.execute('CREATE TABLE drone_units (id INTEGER PRIMARY KEY, '
                 'number INTEGER UNIQUE)')
    for number in range(1, 16):
        conn.execute('INSERT INTO drone_units (number) VALUES (?)', (number,))
    conn.execute('CREATE TABLE drone_operators (id INTEGER PRIMARY KEY, '
                 'full_name TEXT NOT NULL)')
    for key, stored in STORED_NAMES.items():
        if key == drop_operator:
            continue
        conn.execute('INSERT INTO drone_operators (full_name) VALUES (?)',
                     (stored,))
    conn.execute('CREATE TABLE drone_operator_assignments '
                 '(id INTEGER PRIMARY KEY, operator_id INTEGER NOT NULL, '
                 ' drone_unit_id INTEGER NOT NULL, date_from TEXT NOT NULL, '
                 ' date_to TEXT, note TEXT, created_at TEXT, created_by INTEGER)')
    conn.commit()
    conn.close()


def rows(path):
    conn = sqlite3.connect(path)
    out = conn.execute(
        'SELECT a.id, u.number, o.full_name, a.date_from, a.date_to, a.note '
        'FROM drone_operator_assignments a '
        'JOIN drone_units u ON u.id = a.drone_unit_id '
        'JOIN drone_operators o ON o.id = a.operator_id '
        'ORDER BY u.number').fetchall()
    conn.close()
    return out


def run(db, *extra):
    return subprocess.run(
        [sys.executable, SCRIPT, '--db', db] + list(extra),
        capture_output=True, text=True)


class LoaderCase(unittest.TestCase):

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.db = os.path.join(self.tmp.name, 'transport.db')
        self.report = os.path.join(self.tmp.name, 'report.txt')

    def tearDown(self):
        self.tmp.cleanup()


class DryRunAndApply(LoaderCase):
    """1 и 2. Сухой прогон молчит, --apply пишет, повтор не задваивает."""

    def test_dry_run_writes_nothing_but_apply_writes(self):
        build_db(self.db)
        result = run(self.db, '--report', self.report)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual(rows(self.db), [], 'сухой прогон записал строки')

        # Отрицательный контроль: тот же вход с --apply пишет.
        result = run(self.db, '--report', self.report, '--apply')
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual(len(rows(self.db)), len(loader.ASSIGNMENTS))
        self.assertEqual(len(rows(self.db)), 14)

    def test_second_apply_inserts_nothing(self):
        build_db(self.db)
        run(self.db, '--report', self.report, '--apply')
        before = rows(self.db)
        result = run(self.db, '--report', self.report, '--apply')
        self.assertEqual(result.returncode, 0)
        self.assertIn('Rows to insert: 0', result.stdout)
        self.assertEqual(rows(self.db), before)

    def test_existing_assignment_is_not_duplicated(self):
        build_db(self.db)
        conn = sqlite3.connect(self.db)
        unit8 = conn.execute(
            'SELECT id FROM drone_units WHERE number = 8').fetchone()[0]
        op = conn.execute('SELECT id FROM drone_operators LIMIT 1').fetchone()[0]
        conn.execute('INSERT INTO drone_operator_assignments '
                     '(operator_id, drone_unit_id, date_from, date_to) '
                     'VALUES (?, ?, ?, ?)', (op, unit8, '2025-09-01', None))
        conn.commit()
        conn.close()

        run(self.db, '--report', self.report, '--apply')
        eights = [r for r in rows(self.db) if r[1] == 8]
        self.assertEqual(len(eights), 1, 'машина 8 получила второе назначение')


class WhatIsWritten(LoaderCase):
    """7, 8 и 9. Простой, метка источника и границы периода."""

    def setUp(self):
        super(WhatIsWritten, self).setUp()
        build_db(self.db)
        run(self.db, '--report', self.report, '--apply')
        self.written = rows(self.db)

    def test_idle_machine_gets_nobody(self):
        numbers = [r[1] for r in self.written]
        self.assertNotIn(2, numbers)
        for idle in loader.IDLE_UNITS:
            self.assertNotIn(idle, numbers)

    def test_every_row_carries_a_source_prefix(self):
        for _id, number, _name, _f, _t, note in self.written:
            self.assertTrue(
                note.startswith('[TELEMETRY] ') or note.startswith('[OWNER] '),
                'машина %d: note без метки источника: %r' % (number, note))

    def test_owner_rows_are_not_dressed_up_as_telemetry(self):
        """Отрицательный контроль: то, что назвал владелец, помечено OWNER."""
        by_number = {r[1]: r[5] for r in self.written}
        for number in (4, 6, 9, 10, 11):
            self.assertTrue(by_number[number].startswith('[OWNER] '),
                            'машина %d выдана за подтверждённую' % number)
        for number in (1, 8, 14):
            self.assertTrue(by_number[number].startswith('[TELEMETRY] '),
                            'машина %d потеряла подтверждение' % number)

    def test_period_is_the_flight_window_not_the_whole_month(self):
        by_number = {r[1]: (r[3], r[4]) for r in self.written}
        # Машина 10 летала 07..22 сентября -- ни днём раньше, ни позже.
        self.assertEqual(by_number[10], ('2025-09-07', '2025-09-22'))
        # Ни одна строка не начинается 01.09 и не кончается 30.09 «по месяцу»:
        # 30.09 встречается только там, где машина в этот день летала.
        self.assertNotIn('2025-09-01', [f for f, _t in by_number.values()])
        for number, (date_from, date_to) in by_number.items():
            self.assertLessEqual(date_from, date_to, 'машина %d' % number)
            self.assertTrue('2025-09-01' <= date_from <= '2025-09-30')
            self.assertTrue('2025-09-01' <= date_to <= '2025-09-30')


class Guards(LoaderCase):
    """4, 5 и 6. Гомоглифы, незнакомый оператор, отсутствующая база."""

    def test_uzbek_and_russian_spellings_are_the_same_person(self):
        # Отрицательный контроль: точное сравнение эти две строки НЕ свело бы.
        self.assertNotEqual('Кобилов Фаррух', 'Қобилов Фаррух')
        self.assertEqual(loader.normalize('Кобилов Фаррух'),
                         loader.normalize('Қобилов Фаррух'))
        self.assertEqual(loader.normalize('Имомов Бехзод'),
                         loader.normalize('Имомов Беҳзод'))
        # И не сводит РАЗНЫХ людей.
        self.assertNotEqual(loader.normalize('Хамроев Шохрух'),
                            loader.normalize('Файзуллаев Шохрух'))

        build_db(self.db)
        run(self.db, '--report', self.report, '--apply')
        names = {r[1]: r[2] for r in rows(self.db)}
        self.assertEqual(names[5], 'Қобилов Фаррух')
        self.assertEqual(names[13], 'Имомов Беҳзод')

    def test_unknown_operator_refuses_and_writes_nothing(self):
        build_db(self.db, drop_operator='Жумаев Фуркат')
        result = run(self.db, '--report', self.report, '--apply')
        self.assertEqual(result.returncode, 1)
        self.assertEqual(rows(self.db), [], 'откат неполный')

    def test_missing_database_exits_two_and_creates_nothing(self):
        missing = os.path.join(self.tmp.name, 'no', 'transport.db')
        result = run(missing, '--apply')
        self.assertEqual(result.returncode, 2)
        self.assertFalse(os.path.exists(missing))


class Constants(unittest.TestCase):
    """Таблица назначений согласована сама с собой."""

    def test_no_machine_is_listed_twice(self):
        numbers = [row[0] for row in loader.ASSIGNMENTS]
        self.assertEqual(len(numbers), len(set(numbers)))

    def test_idle_machine_is_absent_from_the_table(self):
        numbers = {row[0] for row in loader.ASSIGNMENTS}
        for idle in loader.IDLE_UNITS:
            self.assertNotIn(idle, numbers)

    def test_all_fifteen_machines_are_accounted_for(self):
        covered = {row[0] for row in loader.ASSIGNMENTS} | set(loader.IDLE_UNITS)
        self.assertEqual(covered, set(range(1, 16)))

    def test_every_source_is_one_of_the_two_declared(self):
        for row in loader.ASSIGNMENTS:
            self.assertIn(row[4], (loader.TELEMETRY, loader.OWNER))

    def test_every_row_explains_itself(self):
        for row in loader.ASSIGNMENTS:
            self.assertGreater(len(row[5]), 30,
                               'машина %d: обоснование слишком короткое' % row[0])


if __name__ == '__main__':
    unittest.main()
