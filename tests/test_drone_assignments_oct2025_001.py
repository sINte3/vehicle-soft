# -*- coding: utf-8 -*-
"""DRONE-FLEET-ASSIGN-001, часть 2: октябрь-2025 .. август-2026.

К каждой проверке -- отрицательный контроль, потому что загрузчик, который
«отработал успешно» на любой базе, ничего не гарантирует:

  1. Таблица назначений согласована сама с собой: ни одна пара строк одной
     машины не пересекается, ни одна строка не лезет в загруженный сентябрь
     (раньше 2025-10-01), разбивка №2 стыкуется день в день.
  2. Сухой прогон не пишет НИЧЕГО -- даже с таблицей вылетов рядом.
     Отрицательный контроль: тот же вход с --apply пишет 24 строки.
  3. Уже загруженный сентябрь не задваивается: строки сентябрьского
     загрузчика лежат в фикстуре, как на проде, и ни одна не получает
     соседа с пересечением. Повторный --apply вставляет 0.
  4. Окна «сознательно без оператора» назначениями НЕ покрыты: день из окна
     №10 не покрыт никем, а соседний рабочий день №2 покрыт -- иначе проверка
     не отличала бы пустую базу от загруженной.
  5. Ненайденный оператор откладывает свою строку (код 3), остальные
     грузятся; отчёт печатает справочник.
  6. Отчёт называет цену непокрытых окон живыми числами из drone_flights.
  7. Базы нет -> код 2, файл НЕ создан.

Run:
  python -m unittest tests.test_drone_assignments_oct2025_001 -v
"""
import datetime
import os
import sqlite3
import subprocess
import sys
import tempfile
import unittest

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, REPO_ROOT)
import load_drone_assignments_oct2025_aug2026 as loader  # noqa: E402
import load_drone_assignments_sept2025 as sept  # noqa: E402

SCRIPT = os.path.join(REPO_ROOT, 'load_drone_assignments_oct2025_aug2026.py')

OPERATORS = (
    'Рухиллоев Сайфулло', 'Холмуродов Шахзод', 'Анваров Усмон',
    'Қобилов Фаррух', 'Жураев Туйғун', 'Файзуллаев Шохрух',
    'Қодиров Нурали', 'Хамроев Шохрух', 'Ибодуллаев Хасанбой',
    'Қудратов Мухриддин', 'Имомов Беҳзод', 'Жумаев Фуркат',
    'Садуллаев Шахбоз', 'Болтаев Шахзод', 'Шабанов Дилшод',
    'Акрамов Амирбек', 'Туробов Жахонгир', 'Файзуллаев Шоди',
)

# Сентябрь, как он лежит на проде после первого загрузчика: окна полётов,
# №14 -- до 31.10.2025 со слов владельца.
SEPT_ROWS = (
    (1, 'Рухиллоев Сайфулло', '2025-09-02', '2025-09-30'),
    (3, 'Холмуродов Шахзод', '2025-09-04', '2025-09-30'),
    (4, 'Анваров Усмон', '2025-09-17', '2025-09-29'),
    (5, 'Қобилов Фаррух', '2025-09-05', '2025-09-29'),
    (6, 'Жураев Туйғун', '2025-09-17', '2025-09-28'),
    (7, 'Файзуллаев Шохрух', '2025-09-17', '2025-09-30'),
    (8, 'Қодиров Нурали', '2025-09-04', '2025-09-30'),
    (9, 'Хамроев Шохрух', '2025-09-05', '2025-09-27'),
    (10, 'Хамроев Шохрух', '2025-09-07', '2025-09-22'),
    (11, 'Ибодуллаев Хасанбой', '2025-09-05', '2025-09-30'),
    (12, 'Қудратов Мухриддин', '2025-09-06', '2025-09-30'),
    (13, 'Имомов Беҳзод', '2025-09-17', '2025-09-30'),
    (14, 'Файзуллаев Шоди', '2025-09-17', '2025-10-31'),
    (15, 'Жумаев Фуркат', '2025-09-06', '2025-09-30'),
)

# Немного вылетов: одно окно из UNCOVERED (№10, июнь-июль 2026) и один
# рабочий день №2 -- чтобы отчёт мог назвать цену, а тест -- проверить её.
FLIGHTS = (
    (10, '2026-06-25 07:00:00', 5.00),
    (10, '2026-07-01 07:00:00', 3.50),
    (2, '2026-05-01 07:00:00', 7.77),
)


def build_db(path, drop_operator=None, with_september=True):
    conn = sqlite3.connect(path)
    conn.execute('CREATE TABLE drone_units (id INTEGER PRIMARY KEY, '
                 'number INTEGER UNIQUE, hardware_id TEXT)')
    for number in range(1, 16):
        conn.execute('INSERT INTO drone_units (number) VALUES (?)', (number,))
    conn.execute('CREATE TABLE drone_operators ('
                 ' id INTEGER PRIMARY KEY AUTOINCREMENT,'
                 ' full_name TEXT NOT NULL, phone_primary TEXT,'
                 ' phone_secondary TEXT, subdivision_name TEXT,'
                 ' is_active BOOLEAN DEFAULT 1, note TEXT,'
                 ' created_at DATETIME, updated_at DATETIME)')
    for name in OPERATORS:
        if name == drop_operator:
            continue
        conn.execute('INSERT INTO drone_operators (full_name) VALUES (?)',
                     (name,))
    conn.execute('CREATE TABLE drone_operator_assignments '
                 '(id INTEGER PRIMARY KEY, operator_id INTEGER NOT NULL, '
                 ' drone_unit_id INTEGER NOT NULL, date_from TEXT NOT NULL, '
                 ' date_to TEXT, note TEXT, created_at TEXT, '
                 ' created_by INTEGER)')
    if with_september:
        for number, operator, date_from, date_to in SEPT_ROWS:
            conn.execute(
                'INSERT INTO drone_operator_assignments (operator_id, '
                ' drone_unit_id, date_from, date_to, note) '
                'SELECT o.id, u.id, ?, ?, ? FROM drone_operators o, '
                ' drone_units u WHERE o.full_name = ? AND u.number = ?',
                (date_from, date_to, '[TELEMETRY] сентябрь', operator,
                 number))
    conn.execute('CREATE TABLE drone_flights (id INTEGER PRIMARY KEY, '
                 'drone_unit_id INTEGER, started_at TEXT, area_ha REAL)')
    for number, stamp, ha in FLIGHTS:
        conn.execute(
            'INSERT INTO drone_flights (drone_unit_id, started_at, area_ha) '
            'SELECT id, ?, ? FROM drone_units WHERE number = ?',
            (stamp, ha, number))
    conn.commit()
    conn.close()


def rows(path, note_prefix=None):
    conn = sqlite3.connect(path)
    out = conn.execute(
        'SELECT a.id, u.number, o.full_name, a.date_from, a.date_to, a.note '
        'FROM drone_operator_assignments a '
        'JOIN drone_units u ON u.id = a.drone_unit_id '
        'JOIN drone_operators o ON o.id = a.operator_id '
        'ORDER BY a.id').fetchall()
    conn.close()
    if note_prefix is not None:
        out = [r for r in out if (r[5] or '').startswith(note_prefix)]
    return out


def new_rows(path):
    """Строки, добавленные вторым загрузчиком (не сентябрьской фикстурой)."""
    return [r for r in rows(path)
            if not (r[5] or '').startswith('[TELEMETRY] сентябрь')]


def run(db, *extra):
    return subprocess.run(
        [sys.executable, SCRIPT, '--db', db] + list(extra),
        capture_output=True, text=True)


def covered_on(path, number, day):
    conn = sqlite3.connect(path)
    got = conn.execute(
        'SELECT COUNT(*) FROM drone_operator_assignments a '
        'JOIN drone_units u ON u.id = a.drone_unit_id '
        'WHERE u.number = ? AND a.date_from <= ? '
        "  AND COALESCE(a.date_to, '9999-12-31') >= ?",
        (number, day, day)).fetchone()[0]
    conn.close()
    return got


class Constants(unittest.TestCase):
    """1. Таблица согласована сама с собой -- ДО всякой базы."""

    def test_no_two_rows_of_one_machine_overlap(self):
        by_unit = {}
        for number, _op, date_from, date_to, _s, _w in loader.ASSIGNMENTS:
            by_unit.setdefault(number, []).append(
                (date_from, date_to or '9999-12-31'))
        for number, spans in by_unit.items():
            spans.sort()
            for (f1, t1), (f2, t2) in zip(spans, spans[1:]):
                self.assertLess(t1, f2,
                                'машина %d: %s..%s пересекается с %s..%s'
                                % (number, f1, t1, f2, t2))

    def test_nothing_starts_before_october(self):
        """Сентябрь загружен отдельно -- сюда ему хода нет."""
        for number, _op, date_from, _t, _s, _w in loader.ASSIGNMENTS:
            self.assertGreaterEqual(date_from, loader.FIRST_ALLOWED_DAY,
                                    'машина %d лезет в сентябрь' % number)

    def test_the_number2_split_meets_day_to_day(self):
        """Хамроев по 22.03, Сайфулло с 23.03 -- встык, без дыры и нахлёста."""
        n2 = sorted((r for r in loader.ASSIGNMENTS if r[0] == 2),
                    key=lambda r: r[2])
        self.assertEqual(len(n2), 2)
        khamroev, sayfullo = n2
        self.assertEqual(khamroev[3], '2026-03-22')
        self.assertEqual(sayfullo[2], '2026-03-23')
        gap = (datetime.date.fromisoformat(sayfullo[2])
               - datetime.date.fromisoformat(khamroev[3])).days
        self.assertEqual(gap, 1)
        # И уход Хамроева с №2 стыкуется с его приходом на №10.
        n10 = [r for r in loader.ASSIGNMENTS
               if r[0] == 10 and r[1] == 'Хамроев Шохрух']
        self.assertEqual(n10[0][2], '2026-03-23')

    def test_every_source_is_declared_and_every_row_explains_itself(self):
        for row in loader.ASSIGNMENTS:
            self.assertIn(row[4], (loader.SHEET, loader.TELEMETRY,
                                   loader.OWNER))
            self.assertGreater(len(row[5]), 20, 'машина %d' % row[0])

    def test_uncovered_windows_do_not_overlap_assignments(self):
        """Окно «без оператора» не должно лежать внутри назначения."""
        for number, date_from, date_to, _why in loader.UNCOVERED:
            for unit, _op, a, b, _s, _w in loader.ASSIGNMENTS:
                if unit != number:
                    continue
                b = b or '9999-12-31'
                self.assertTrue(date_to < a or date_from > b,
                                'окно %s..%s машины %d покрыто назначением '
                                '%s..%s' % (date_from, date_to, number, a, b))


class LoaderCase(unittest.TestCase):

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.db = os.path.join(self.tmp.name, 'transport.db')
        self.report = os.path.join(self.tmp.name, 'report.txt')

    def tearDown(self):
        self.tmp.cleanup()

    def read_report(self):
        with open(self.report, encoding='utf-8') as fh:
            return fh.read()


class DryRunAndApply(LoaderCase):
    """2 и 3. Сухой прогон молчит; запись кладёт 24 и не задваивает."""

    def test_dry_run_writes_nothing_but_apply_writes(self):
        build_db(self.db)
        before = rows(self.db)
        result = run(self.db, '--report', self.report)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual(rows(self.db), before, 'сухой прогон записал строки')

        result = run(self.db, '--report', self.report, '--apply')
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual(len(new_rows(self.db)), len(loader.ASSIGNMENTS))
        self.assertEqual(len(new_rows(self.db)), 24)

    def test_september_rows_survive_untouched(self):
        build_db(self.db)
        sept_before = rows(self.db, note_prefix='[TELEMETRY] сентябрь')
        self.assertEqual(len(sept_before), 14)
        run(self.db, '--report', self.report, '--apply')
        self.assertEqual(rows(self.db, note_prefix='[TELEMETRY] сентябрь'),
                         sept_before)

    def test_second_apply_inserts_nothing(self):
        build_db(self.db)
        run(self.db, '--report', self.report, '--apply')
        snapshot = rows(self.db)
        result = run(self.db, '--report', self.report, '--apply')
        self.assertEqual(result.returncode, 0)
        self.assertIn('Rows to insert: 0', result.stdout)
        self.assertEqual(rows(self.db), snapshot)

    def test_open_ended_rows_carry_null_not_a_fake_date(self):
        build_db(self.db)
        run(self.db, '--report', self.report, '--apply')
        open_ended = [r for r in new_rows(self.db) if r[4] is None]
        expected = sum(1 for r in loader.ASSIGNMENTS if r[3] is None)
        self.assertEqual(len(open_ended), expected)
        self.assertGreater(expected, 0)


class WhatStaysOpen(LoaderCase):
    """4 и 6. Решения владельца не перекрыты; цена окон названа."""

    def test_owner_undefined_windows_stay_uncovered(self):
        build_db(self.db)
        run(self.db, '--report', self.report, '--apply')
        # День из окна №10 20.06-06.07 не покрыт никем...
        self.assertEqual(covered_on(self.db, 10, '2026-06-25'), 0)
        # ...октябрь №15 тоже...
        self.assertEqual(covered_on(self.db, 15, '2025-10-20'), 0)
        # ...а рабочий день №2 покрыт -- отрицательный контроль: иначе
        # проверка не отличала бы пустую базу от загруженной.
        self.assertEqual(covered_on(self.db, 2, '2026-05-01'), 1)
        self.assertEqual(covered_on(self.db, 2, '2025-12-15'), 1)

    def test_the_report_prices_open_windows_from_live_flights(self):
        build_db(self.db)
        run(self.db, '--report', self.report)
        report = self.read_report()
        self.assertIn('СОЗНАТЕЛЬНО БЕЗ ОПЕРАТОРА', report)
        # №10: 5.00 + 3.50 из фикстуры вылетов.
        self.assertIn('8.50', report)
        # Отрицательный контроль: покрытый день №2 в непокрытые не попал.
        self.assertNotIn('7.77', report)


class Guards(LoaderCase):
    """5 и 7. Ненайденное имя откладывает строку; базы нет -- отказ."""

    def test_absent_operator_postpones_his_rows_and_loads_the_rest(self):
        build_db(self.db, drop_operator='Болтаев Шахзод')
        result = run(self.db, '--report', self.report, '--apply')
        self.assertEqual(result.returncode, 3, result.stdout + result.stderr)
        written = new_rows(self.db)
        self.assertEqual(len(written), 23)
        self.assertNotIn('Болтаев Шахзод', [r[2] for r in written])
        report = self.read_report()
        self.assertIn('ОПЕРАТОР НЕ НАЙДЕН', report)
        self.assertIn('СПРАВОЧНИК ОПЕРАТОРОВ ЦЕЛИКОМ', report)

    def test_after_the_person_is_added_the_second_run_completes(self):
        build_db(self.db, drop_operator='Болтаев Шахзод')
        run(self.db, '--report', self.report, '--apply')
        conn = sqlite3.connect(self.db)
        conn.execute('INSERT INTO drone_operators (full_name) VALUES (?)',
                     ('Болтаев Шахзод',))
        conn.commit()
        conn.close()
        result = run(self.db, '--report', self.report, '--apply')
        self.assertEqual(result.returncode, 0)
        self.assertEqual(len(new_rows(self.db)), 24)

    def test_a_hand_made_overlap_skips_that_row_only(self):
        build_db(self.db)
        conn = sqlite3.connect(self.db)
        conn.execute(
            'INSERT INTO drone_operator_assignments (operator_id, '
            ' drone_unit_id, date_from, date_to, note) '
            'SELECT o.id, u.id, ?, NULL, ? FROM drone_operators o, '
            ' drone_units u WHERE o.full_name = ? AND u.number = 3',
            ('2026-01-01', 'ручная строка', 'Холмуродов Шахзод'))
        conn.commit()
        conn.close()
        result = run(self.db, '--report', self.report, '--apply')
        self.assertEqual(result.returncode, 0)
        numbers = [r[1] for r in new_rows(self.db)
                   if not (r[5] or '').startswith('ручная')]
        self.assertNotIn(3, numbers, 'строка №3 легла поверх ручной')
        self.assertEqual(len([n for n in numbers]), 23)

    def test_missing_database_exits_two_and_creates_nothing(self):
        missing = os.path.join(self.tmp.name, 'no', 'transport.db')
        result = run(missing, '--apply')
        self.assertEqual(result.returncode, 2)
        self.assertFalse(os.path.exists(missing))


if __name__ == '__main__':
    unittest.main()
