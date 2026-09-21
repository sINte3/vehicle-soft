# -*- coding: utf-8 -*-
"""Самопроверка tools/dji_area_raw_guard.py.

Что здесь держится:

* изменённая площадь известного вылета -- нарушение, даже на один бит;
* пропавший вылет -- нарушение;
* НОВЫЙ вылет нарушением не является: цикл законно добавляет вылеты, и сторож,
  который падал бы на них, пришлось бы отключить в первый же день;
* ненулевая `billable_area_m2` -- нарушение;
* база без таблицы расчётов -- не нарушение (слой площади ещё не развёрнут);
* сторож сам ничего не пишет; отсутствующая база -- код 2 без создания файла;
* пустой и чужой снимок не принимаются за доказательство;
* коды возврата -- числа, которые читает блок PowerShell.

Stdlib. Запуск:  python tools\\test_dji_area_raw_guard.py
"""

import hashlib
import io
import json
import os
import shutil
import sqlite3
import sys
import tempfile
import unittest
from contextlib import redirect_stdout

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if REPO_ROOT not in sys.path:
    sys.path.insert(0, REPO_ROOT)

from tools import dji_area_raw_guard as tool  # noqa: E402


class Base(unittest.TestCase):

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix='raw_guard_')
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        self.db = os.path.join(self.tmp, 'transport.db')
        self.snapshot = os.path.join(self.tmp, 'snap', 'raw_before.json')
        con = sqlite3.connect(self.db)
        con.execute('CREATE TABLE drone_flights (id INTEGER PRIMARY KEY, '
                    'dji_flight_id BIGINT, area_ha FLOAT)')
        con.execute('CREATE TABLE dji_area_calculations (id INTEGER PRIMARY '
                    'KEY, flight_id BIGINT, billable_area_m2 FLOAT)')
        con.executemany(
            'INSERT INTO drone_flights (dji_flight_id, area_ha) VALUES (?, ?)',
            [(701, 1.25), (702, 0.0), (703, None), (704, 3.3333333333333335)])
        con.execute('INSERT INTO dji_area_calculations (flight_id, '
                    'billable_area_m2) VALUES (701, NULL)')
        con.commit()
        con.close()

    def sql(self, statement, *params):
        con = sqlite3.connect(self.db)
        con.execute(statement, params)
        con.commit()
        con.close()

    def run_tool(self, *argv):
        out = io.StringIO()
        with redirect_stdout(out):
            code = tool.main(['--db', self.db] + list(argv))
        return code, out.getvalue()

    def save(self):
        code, text = self.run_tool('--save', self.snapshot)
        self.assertEqual(code, 0, text)

    def digest(self):
        with open(self.db, 'rb') as fh:
            return hashlib.sha256(fh.read()).hexdigest()


class Untouched(Base):

    def test_the_same_database_passes(self):
        self.save()
        code, text = self.run_tool('--compare', self.snapshot)
        self.assertEqual(code, 0, text)
        self.assertIn('RAW kept           : 4', text)
        self.assertIn('VERDICT: RAW UNTOUCHED', text)

    def test_new_flights_are_counted_and_allowed(self):
        self.save()
        self.sql('INSERT INTO drone_flights (dji_flight_id, area_ha) '
                 'VALUES (705, 9.0)')
        code, text = self.run_tool('--compare', self.snapshot)
        self.assertEqual(code, 0, text)
        self.assertIn('new flights        : 1', text)

    def test_new_calculation_rows_are_not_a_violation(self):
        self.save()
        self.sql('INSERT INTO dji_area_calculations (flight_id, '
                 'billable_area_m2) VALUES (702, NULL)')
        self.assertEqual(self.run_tool('--compare', self.snapshot)[0], 0)

    def test_a_database_without_the_area_layer_is_still_guarded(self):
        self.sql('DROP TABLE dji_area_calculations')
        self.save()
        self.assertEqual(self.run_tool('--compare', self.snapshot)[0], 0)


class Touched(Base):

    def test_a_rewritten_area_is_caught_and_named(self):
        self.save()
        self.sql('UPDATE drone_flights SET area_ha = 0 WHERE dji_flight_id = ?',
                 701)
        code, text = self.run_tool('--compare', self.snapshot)
        self.assertEqual(code, 3)
        self.assertIn('RAW CHANGED        : 1', text)
        self.assertIn('changed 701: 1.25 -> 0.0', text)
        self.assertIn('VERDICT: RAW WAS TOUCHED', text)

    def test_the_last_bit_counts(self):
        self.save()
        self.sql('UPDATE drone_flights SET area_ha = ? WHERE dji_flight_id = ?',
                 3.333333333333333, 704)
        self.assertEqual(self.run_tool('--compare', self.snapshot)[0], 3)

    def test_null_turned_into_zero_is_a_change(self):
        # «Площадь неизвестна» и «площадь ноль» -- разные утверждения.
        self.save()
        self.sql('UPDATE drone_flights SET area_ha = 0 WHERE dji_flight_id = ?',
                 703)
        self.assertEqual(self.run_tool('--compare', self.snapshot)[0], 3)

    def test_a_deleted_flight_is_caught(self):
        self.save()
        self.sql('DELETE FROM drone_flights WHERE dji_flight_id = ?', 702)
        code, text = self.run_tool('--compare', self.snapshot)
        self.assertEqual(code, 3)
        self.assertIn('missing 702', text)

    def test_a_billable_area_is_caught(self):
        self.save()
        self.sql('UPDATE dji_area_calculations SET billable_area_m2 = 12500 '
                 'WHERE flight_id = ?', 701)
        code, text = self.run_tool('--compare', self.snapshot)
        self.assertEqual(code, 3)
        self.assertIn('billable not NULL  : 1', text)


class TheGuardItself(Base):

    def test_it_writes_nothing_to_the_database(self):
        before = self.digest()
        self.save()
        self.run_tool('--compare', self.snapshot)
        self.assertEqual(self.digest(), before)

    def test_its_connection_cannot_write_at_all(self):
        # Сторож только читает, поэтому совпавший хеш не доказал бы режима
        # открытия: проверяется сам отказ SQLite.
        con = tool.connect_read_only(self.db)
        self.addCleanup(con.close)
        with self.assertRaises(sqlite3.OperationalError):
            con.execute('UPDATE drone_flights SET area_ha = 0')

    def test_a_missing_database_is_code_2_and_no_file_appears(self):
        ghost = os.path.join(self.tmp, 'nowhere', 'absent.db')
        out = io.StringIO()
        with redirect_stdout(out):
            code = tool.main(['--db', ghost, '--save', self.snapshot])
        self.assertEqual(code, 2)
        self.assertFalse(os.path.exists(ghost))
        self.assertFalse(os.path.exists(self.snapshot))

    def test_an_empty_database_cannot_be_snapshotted(self):
        self.sql('DELETE FROM drone_flights')
        code, _text = self.run_tool('--save', self.snapshot)
        self.assertEqual(code, 1)
        self.assertFalse(os.path.exists(self.snapshot))

    def test_a_foreign_or_broken_snapshot_is_not_evidence(self):
        os.makedirs(os.path.dirname(self.snapshot))
        for body in ('{not json', json.dumps({'area_ha': {}}),
                     json.dumps({'guard': 'SOMETHING_ELSE', 'area_ha': {}}),
                     json.dumps({'guard': tool.GUARD_ID, 'area_ha': []})):
            with io.open(self.snapshot, 'w', encoding='utf-8') as fh:
                fh.write(body)
            self.assertEqual(self.run_tool('--compare', self.snapshot)[0], 1,
                             body)

    def test_exit_codes_are_the_literal_numbers_the_runbook_reads(self):
        self.assertEqual((tool.EXIT_OK, tool.EXIT_USAGE, tool.EXIT_NO_DATABASE,
                          tool.EXIT_VIOLATED), (0, 1, 2, 3))

    def test_console_output_is_ascii_only(self):
        self.save()
        self.sql('UPDATE drone_flights SET area_ha = 7 WHERE dji_flight_id = ?',
                 701)
        _code, text = self.run_tool('--compare', self.snapshot)
        self.assertTrue(text)
        self.assertTrue(all(ord(ch) < 128 for ch in text), text)


if __name__ == '__main__':
    unittest.main()
