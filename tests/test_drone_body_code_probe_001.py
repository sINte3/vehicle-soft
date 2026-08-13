# -*- coding: utf-8 -*-
"""DRONE-BODYCODE-001: разведка привязки по серийнику планера.

Проверяется четыре вещи, и к каждой — отрицательный контроль, потому что
проверка, дающая одинаковый результат при верном и неверном парке, проверкой
не является:

  1. Единицы. Колонка `Sprayed area` выгрузки — в МУ, а не в гектарах:
     1 га = 15 му. Отрицательный контроль: то же число без деления
     отличается от базы в пятнадцать раз, и тест это видит.
  2. Раздел A отличает поле-борт от поля-вылета. Кандидатом становится ключ,
     чьи значения повторяются (борт), а не ключ, уникальный на строку
     (идентификатор вылета). Отрицательный контроль: payload без поля борта
     кандидатов не даёт.
  3. Раздел C переносит ровно то, что должно переехать. Ключевой случай —
     перепутанные `hardware_id` у №2 и №9: при верном парке зимняя выгрузка
     даёт НОЛЬ переносов, при перепутанном — ровно вылеты этих двух бортов.
     Это и есть отрицательный контроль всей цепочки: если бы инструмент
     возвращал одно и то же в обоих состояниях, он ничего не проверял бы.
  4. Инструмент НЕ ПИШЕТ: файл базы побайтно тот же после прогона.

Run:
  python -m unittest tests.test_drone_body_code_probe_001 -v
"""
import hashlib
import json
import os
import sqlite3
import sys
import tempfile
import unittest

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(REPO_ROOT, 'tools'))
import drone_body_code_probe as probe  # noqa: E402

# Парк как он есть в DJI Device Management (проверено 2026-08-13).
CORRECT_PARK = {
    2: '64TBL8F002007V',   # DJI "2 Klaster"
    3: '64TBL6C00200C6',   # DJI "3 Gijduvon"
    4: '64TBL8E002007H',   # DJI "4 Гиждувон"
    9: '64TBL8F002006B',   # DJI "9 Garden"
}
# Парк как он был заведён у нас: серийники №2 и №9 стоят наоборот.
SWAPPED_PARK = dict(CORRECT_PARK)
SWAPPED_PARK[2] = '64TBL8F002006B'
SWAPPED_PARK[9] = '64TBL8F002007V'

# Кусок зимней выгрузки: (serial вылета, body планера, имя борта, время, му).
# Числа — из реальной выгрузки владельца за 01.12.2025–28.02.2026.
EXPORT_ROWS = [
    ('R1790694323', '64TBL8F002006B', '9 Garden',   '2026-02-25 13:58:08', 0.02),
    ('R4579688914', '64TBL8F002006B', '9 Garden',   '2026-02-25 13:57:36', 0.00),
    ('R2717592043', '64TBL8F002006B', '9 Garden',   '2026-02-25 11:42:38', 0.26),
    ('R8811110001', '64TBL8F002007V', '2 Klaster',  '2026-02-21 17:55:55', 0.83),
    ('R8811110002', '64TBL6C00200C6', '3 Gijduvon', '2026-02-20 10:44:45', 10.35),
    ('R8811110003', '64TBL8E002007H', '4 Gijduvon', '2026-02-20 10:45:06', 7.80),
]
# Имя борта -> номер машины. Так вылет привязан СЕГОДНЯ: по строке ника.
NICK_TO_NUMBER = {'9 Garden': 9, '2 Klaster': 2,
                  '3 Gijduvon': 3, '4 Gijduvon': 4}


def build_db(path, park, payload_has_body=False):
    """Синтетическая база из двух таблиц, которые читает инструмент."""
    conn = sqlite3.connect(path)
    conn.execute('CREATE TABLE drone_units (id INTEGER PRIMARY KEY, '
                 'number INTEGER UNIQUE, hardware_id TEXT)')
    for number in sorted(park):
        conn.execute('INSERT INTO drone_units (number, hardware_id) '
                     'VALUES (?, ?)', (number, park[number]))
    conn.execute('CREATE TABLE drone_flights (id INTEGER PRIMARY KEY, '
                 'drone_unit_id INTEGER, nickname_raw TEXT, '
                 'serial_number TEXT, started_at TEXT, area_ha REAL, '
                 'raw_json TEXT)')
    unit_id = {}
    for uid, number in conn.execute('SELECT id, number FROM drone_units'):
        unit_id[number] = uid
    for serial, body, aircraft, started, area_mu in EXPORT_ROWS:
        payload = {'id': 1, 'nickname': aircraft, 'serial_number': serial,
                   'new_work_area': area_mu / probe.MU_PER_HECTARE * 10000.0}
        if payload_has_body:
            payload['body_code'] = body
        conn.execute(
            'INSERT INTO drone_flights (drone_unit_id, nickname_raw, '
            'serial_number, started_at, area_ha, raw_json) '
            'VALUES (?, ?, ?, ?, ?, ?)',
            (unit_id[NICK_TO_NUMBER[aircraft]], aircraft, serial, started,
             area_mu / probe.MU_PER_HECTARE,
             json.dumps(payload, ensure_ascii=False)))
    conn.commit()
    conn.close()


def export_rows():
    """Строки в том виде, в каком их отдаёт read_export()."""
    return [{'serial': serial, 'body': body, 'aircraft': aircraft,
             'flight_time': started,
             'area_ha': area_mu / probe.MU_PER_HECTARE}
            for serial, body, aircraft, started, area_mu in EXPORT_ROWS]


class Units(unittest.TestCase):
    """1. Единицы выгрузки — му, а не гектары."""

    def test_divisor_is_fifteen(self):
        self.assertEqual(probe.MU_PER_HECTARE, 15.0)

    def test_export_hectares_match_the_database(self):
        """Сумма выгрузки после деления равна сумме гектаров в базе."""
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, 'db.sqlite')
            build_db(path, CORRECT_PARK)
            conn = sqlite3.connect(path)
            db_ha = conn.execute(
                'SELECT ROUND(SUM(area_ha), 4) FROM drone_flights').fetchone()[0]
            conn.close()
        export_ha = sum(r['area_ha'] for r in export_rows())
        self.assertAlmostEqual(export_ha, db_ha, places=4)

    def test_without_the_divisor_the_check_would_be_fifteen_times_off(self):
        """Отрицательный контроль: без деления сверка расходится в 15 раз."""
        raw_mu = sum(row[4] for row in EXPORT_ROWS)
        export_ha = sum(r['area_ha'] for r in export_rows())
        self.assertAlmostEqual(raw_mu / export_ha, 15.0, places=6)
        self.assertNotAlmostEqual(raw_mu, export_ha, places=2)


class RawJsonProbe(unittest.TestCase):
    """2. Раздел A отличает поле борта от поля вылета."""

    def test_body_field_is_found_when_present(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, 'db.sqlite')
            build_db(path, CORRECT_PARK, payload_has_body=True)
            conn = probe.open_readonly(path)
            _keys, candidates, parsed = probe.probe_raw_json(conn)
            conn.close()
        self.assertEqual(parsed, len(EXPORT_ROWS))
        found = {key for key, _uniq, _sample in candidates}
        self.assertIn('body_code', found)
        # Борт повторяется: различных значений меньше, чем вылетов.
        uniq = dict((key, u) for key, u, _s in candidates)['body_code']
        self.assertLess(uniq, len(EXPORT_ROWS))

    def test_no_candidate_when_payload_has_no_body_field(self):
        """Отрицательный контроль: без поля борта кандидатов нет."""
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, 'db.sqlite')
            build_db(path, CORRECT_PARK, payload_has_body=False)
            conn = probe.open_readonly(path)
            _keys, candidates, _parsed = probe.probe_raw_json(conn)
            conn.close()
        found = {key for key, _uniq, _sample in candidates}
        self.assertNotIn('body_code', found)
        # serial_number кандидатом быть не должен: он уникален на вылет.
        self.assertNotIn('serial_number', found)

    def test_serial_number_is_per_flight(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, 'db.sqlite')
            build_db(path, CORRECT_PARK)
            conn = probe.open_readonly(path)
            total, filled, distinct = probe.probe_serial_shape(conn)
            conn.close()
        self.assertEqual(total, len(EXPORT_ROWS))
        self.assertEqual(filled, distinct)


class JoinAgainstPark(unittest.TestCase):
    """3. Раздел C: переносы считаются, и парк решает, сколько их."""

    def _join(self, park):
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, 'db.sqlite')
            build_db(path, park)
            conn = probe.open_readonly(path)
            units = probe.load_units(conn)
            stats = probe.join_export(conn, export_rows(), units)
            conn.close()
        return stats

    def test_correct_park_moves_nothing(self):
        """Парк верен — привязка по нику и по борту совпадают, переносов 0."""
        stats = self._join(CORRECT_PARK)
        self.assertEqual(stats['matched_rows'], len(EXPORT_ROWS))
        self.assertEqual(stats['missing_rows'], 0)
        self.assertEqual(stats['moves'], [])
        self.assertEqual(stats['same'], len(EXPORT_ROWS))

    def test_swapped_park_moves_exactly_those_two_machines(self):
        """Отрицательный контроль: перепутанный парк даёт ровно 2 и 9."""
        stats = self._join(SWAPPED_PARK)
        moved = [(m['from_number'], m['to_number']) for m in stats['moves']]
        self.assertEqual(len(moved), 4)          # три «9 Garden» + один «2 Klaster»
        self.assertEqual(set(moved), {(9, 2), (2, 9)})
        self.assertEqual(sum(1 for m in moved if m == (9, 2)), 3)
        self.assertEqual(sum(1 for m in moved if m == (2, 9)), 1)
        # Машины 3 и 4 не задеты: их серийники в обоих парках одни и те же.
        self.assertNotIn(3, [m['from_number'] for m in stats['moves']])
        self.assertNotIn(4, [m['from_number'] for m in stats['moves']])

    def test_unknown_body_code_is_reported_not_guessed(self):
        """Body Code, которого нет в парке, не угадывается, а называется."""
        rows = export_rows()
        rows.append({'serial': 'R9999999999', 'body': '64TBLNOSUCH01',
                     'aircraft': '77 Unknown',
                     'flight_time': '2026-02-01 10:00:00', 'area_ha': 1.0})
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, 'db.sqlite')
            build_db(path, CORRECT_PARK)
            conn = probe.open_readonly(path)
            units = probe.load_units(conn)
            stats = probe.join_export(conn, rows, units)
            conn.close()
        # Вылета с таким serial в базе нет — строка попадает в «не нашлось»,
        # а не приписывается ближайшей машине.
        self.assertEqual(stats['missing_rows'], 1)
        self.assertEqual(stats['moves'], [])

    def test_month_grouping(self):
        stats = self._join(SWAPPED_PARK)
        self.assertEqual({m['month'] for m in stats['moves']}, {'2026-02'})


class WritesNothing(unittest.TestCase):
    """4. Инструмент — читатель."""

    def test_database_file_is_byte_identical_after_the_run(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, 'db.sqlite')
            build_db(path, SWAPPED_PARK)
            with open(path, 'rb') as handle:
                before = hashlib.sha256(handle.read()).hexdigest()
            conn = probe.open_readonly(path)
            units = probe.load_units(conn)
            probe.probe_raw_json(conn)
            probe.probe_serial_shape(conn)
            probe.join_export(conn, export_rows(), units)
            conn.close()
            with open(path, 'rb') as handle:
                after = hashlib.sha256(handle.read()).hexdigest()
        self.assertEqual(before, after)

    def test_write_through_the_same_connection_fails(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, 'db.sqlite')
            build_db(path, CORRECT_PARK)
            conn = probe.open_readonly(path)
            with self.assertRaises(sqlite3.OperationalError):
                conn.execute('UPDATE drone_units SET hardware_id = "x"')
            conn.close()

    def test_missing_database_exits_with_code_two(self):
        with self.assertRaises(SystemExit) as caught:
            probe.open_readonly(os.path.join('no', 'such', 'db.sqlite'))
        self.assertEqual(caught.exception.code, 2)


if __name__ == '__main__':
    unittest.main()
