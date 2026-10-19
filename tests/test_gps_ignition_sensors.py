# -*- coding: utf-8 -*-
"""GPS-23: датчики зажигания объектов Wialon -- обзор, только чтение.

Что проверяется и почему:
  1. Датчик зажигания узнаётся по типу, а не по имени: датчик с именем
     «Зажигание» и другим типом -- не зажигание; параметр из выражения
     («io_239*1») ищется среди параметров последнего сообщения.
  2. Источник моточасов -- по битам `cfl` (0x10, 0x20, 0x40, 0x200);
     `cneh` -- как прислан, без пересчёта.
  3. Машина, категория и исключение -- из базы только чтением; база не
     меняется ни на байт; отсутствующая база не создаётся (код 2).
  4. Ответ без `sens` и `cfl` у всех объектов -- не «датчиков нет», а код 4
     и CSV не пишется (отрицательный контроль: тот же ответ с флагами
     проходит). Запрос объектов -- с флагами датчиков и счётчиков.
  5. Сервисы только читающие; консоль -- ASCII без имён объектов и машин.
  6. Вход отклонён -- код 3; --out на месте базы -- код 2.

Запуск: python -m unittest tests.test_gps_ignition_sensors -v
"""

import csv
import hashlib
import io
import json
import os
import sqlite3
import sys
import tempfile
import time
import unittest
import urllib.parse
from contextlib import redirect_stderr, redirect_stdout
from unittest import mock

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if REPO_ROOT not in sys.path:
    sys.path.insert(0, REPO_ROOT)

import tools.gps_ignition_sensors as gi                           # noqa: E402
from tests.test_gps_link_mappings import (FakeResponse, FakeServer,  # noqa: E402
                                          Installed)

NOW = int(time.time())
READ_ONLY_SERVICES = {'token/login', 'core/search_items', 'core/logout'}
NAMES = ['МТЗ-80.1 80 001 EA', 'Камаз 80 777 KA', 'Т-28 без датчика',
         'МТЗ-80.1', 'Камаз']

# 1001 -- трактор с датчиком зажигания, параметр в последнем сообщении;
# 1002 -- грузовик (исключён категорией) с датчиком, параметра в сообщении нет;
# 1003 -- объект без машины: датчик назван «Зажигание», но тип другой.
UNITS = [
    {'id': 1001, 'nm': NAMES[0],
     'sens': {'1': {'n': 'Зажигание', 't': 'engine operation', 'p': 'io_239*1'},
              '2': {'n': 'Топливо', 't': 'fuel level', 'p': 'can_fuel'}},
     'cfl': 0x10 | 0x200, 'cneh': 1234.5,
     'lmsg': {'t': NOW - 600, 'p': {'io_239': 1, 'can_fuel': 40}}},
    {'id': 1002, 'nm': NAMES[1],
     'sens': {'1': {'n': 'Ignition', 't': 'engine operation', 'p': 'in1'}},
     'cfl': 0x01, 'cneh': 0,
     'lmsg': {'t': NOW - 10 * 86400, 'p': {'pwr_ext': 27.1}}},
    {'id': 1003, 'nm': NAMES[2],
     'sens': {'1': {'n': 'Зажигание', 't': 'digital', 'p': 'in2'}},
     'cfl': 0x20},
]


class SensorServer(FakeServer):
    """Части ответа -- только по запрошенным битам, как у сервера."""

    def __init__(self, units=None, login_error=None, search_error=None):
        super().__init__([], login_error=login_error)
        self.units_full = UNITS if units is None else units
        self.search_error = search_error
        self.flags_asked = []

    def urlopen(self, request, timeout=None):
        query = urllib.parse.parse_qs(request.data.decode('utf-8'))
        svc = query['svc'][0]
        if svc == 'core/search_items':
            self.calls.append(svc)
            if self.search_error is not None:
                return FakeResponse({'error': self.search_error})
            flags = int(json.loads(query['params'][0])['flags'])
            self.flags_asked.append(flags)
            items = []
            for unit in self.units_full:
                item = {'id': unit['id']}
                if flags & 0x1:
                    item['nm'] = unit['nm']
                if flags & 0x1000 and 'sens' in unit:
                    item['sens'] = unit['sens']
                if flags & 0x2000:
                    for key in ('cfl', 'cneh'):
                        if key in unit:
                            item[key] = unit[key]
                if flags & 0x400 and 'lmsg' in unit:
                    item['lmsg'] = unit['lmsg']
                items.append(item)
            return FakeResponse({'items': items})
        return super().urlopen(request, timeout)


def make_db(path):
    con = sqlite3.connect(path)
    con.executescript(
        'CREATE TABLE equipment (id INTEGER PRIMARY KEY, name TEXT, plate TEXT, '
        'category TEXT, organization_id INTEGER);'
        'CREATE TABLE vialon_mappings (id INTEGER PRIMARY KEY, vialon_name TEXT, '
        'wialon_id INTEGER, equipment_id INTEGER, skip BOOLEAN);'
        "INSERT INTO equipment VALUES (1, 'МТЗ-80.1', '80 001 EA', 'mtz', 1);"
        "INSERT INTO equipment VALUES (2, 'Камаз', '80 777 KA', 'yuk_transport', 1);"
        "INSERT INTO vialon_mappings VALUES (1, 'u1', 1001, 1, 0);"
        "INSERT INTO vialon_mappings VALUES (2, 'u2', 1002, 2, 0);")
    con.commit()
    con.close()


def digest(path):
    with open(path, 'rb') as handle:
        return hashlib.sha256(handle.read()).hexdigest()


class Base(unittest.TestCase):
    def setUp(self):
        self.folder = tempfile.mkdtemp()
        self.db = os.path.join(self.folder, 'transport.db')
        make_db(self.db)
        self.out = os.path.join(self.folder, 'ignition.csv')
        patcher = mock.patch.object(gi.config, 'read_token', return_value='T')
        patcher.start()
        self.addCleanup(patcher.stop)

    def run_tool(self, server, *extra):
        out, err = io.StringIO(), io.StringIO()
        with Installed(server), redirect_stdout(out), redirect_stderr(err):
            code = gi.main(['--db', self.db, '--out', self.out, '--pause', '0']
                           + list(extra))
        return code, out.getvalue(), err.getvalue()

    def rows(self):
        with open(self.out, encoding='utf-8-sig', newline='') as handle:
            return {int(r['wialon_id']): r
                    for r in csv.DictReader(handle, delimiter=';')}


class Survey(Base):
    def test_ignition_is_recognised_by_type_not_by_name(self):
        code, out, _ = self.run_tool(SensorServer())
        self.assertEqual(code, gi.EXIT_OK, out)
        rows = self.rows()
        self.assertEqual(rows[1001]['datchik_zazhiganiya'], 'da')
        self.assertEqual(rows[1002]['datchik_zazhiganiya'], 'da')
        self.assertEqual(rows[1003]['datchik_zazhiganiya'], 'net')
        self.assertEqual(rows[1001]['parametry_zazhiganiya'], 'io_239*1')
        self.assertIn('fuel level: can_fuel', rows[1001]['vse_datchiki'])
        self.assertIn('with an ignition sensor    : 2', out)

    def test_the_parameter_is_looked_up_in_the_last_message(self):
        _code, out, _ = self.run_tool(SensorServer())
        rows = self.rows()
        self.assertEqual(rows[1001]['param_v_poslednem'], 'da')
        self.assertEqual(rows[1001]['znachenie_v_poslednem'], 'io_239=1')
        self.assertEqual(rows[1002]['param_v_poslednem'], 'net')
        self.assertEqual(rows[1003]['param_v_poslednem'], '')
        self.assertIn('ignition parameter in the last message: 1, not in it: 1',
                      out)
        self.assertIn('last message older than 7 days: 1, never reported: 1',
                      out)

    def test_engine_hours_source_from_cfl_bits_and_raw_counter(self):
        _code, out, _ = self.run_tool(SensorServer())
        rows = self.rows()
        self.assertEqual(rows[1001]['motochasy_istochnik'], 'datchik_zazhiganiya')
        self.assertEqual(rows[1001]['motochasy_avto'], 'da')
        self.assertEqual(rows[1001]['cneh_kak_prislano'], '1234.5')
        self.assertEqual(rows[1002]['motochasy_istochnik'], 'net')
        self.assertEqual(rows[1002]['motochasy_avto'], 'net')
        self.assertEqual(rows[1003]['motochasy_istochnik'], 'absolyutnyy_datchik')
        self.assertIn('automatic engine hours from new messages: 1', out)

    def test_machine_category_and_exclusion_from_the_database(self):
        _code, out, _ = self.run_tool(SensorServer())
        rows = self.rows()
        self.assertEqual(rows[1001]['mashina'], 'МТЗ-80.1')
        self.assertEqual(rows[1001]['kategoriya'], 'mtz')
        self.assertEqual(rows[1001]['isklyuchen'], '')
        self.assertEqual(rows[1002]['isklyuchen'], 'ne_polevaya')
        self.assertEqual(rows[1003]['mashina'], '')
        self.assertIn('counted by plan-fact       : 2, of them with ignition: 1',
                      out)
        self.assertRegex(out, r'mtz\s+:\s+1 /\s+1')

    def test_the_request_asks_for_sensors_counters_and_last_message(self):
        server = SensorServer()
        self.run_tool(server)
        self.assertEqual(server.flags_asked, [0x3401])
        self.assertTrue(set(server.calls) <= READ_ONLY_SERVICES, server.calls)

    def test_the_database_is_not_touched(self):
        before = digest(self.db)
        self.run_tool(SensorServer())
        self.assertEqual(digest(self.db), before)

    def test_the_console_is_ascii_and_carries_no_names(self):
        code, out, err = self.run_tool(SensorServer())
        self.assertEqual(code, gi.EXIT_OK)
        self.assertTrue((out + err).isascii(), out)
        for name in NAMES + ['Зажигание', 'Топливо']:
            self.assertNotIn(name, out + err)

    def test_sensor_types_are_listed_so_a_wrong_type_string_shows(self):
        _code, out, _ = self.run_tool(SensorServer())
        for kind in ('engine operation', 'fuel level', 'digital'):
            self.assertIn(kind, out)


class Refusals(Base):
    def test_no_sensors_and_no_counters_is_a_refusal_not_an_empty_survey(self):
        bare = [{'id': u['id'], 'nm': u['nm']} for u in UNITS]
        code, out, _ = self.run_tool(SensorServer(units=bare))
        self.assertEqual(code, gi.EXIT_NO_SENSOR_DATA, out)
        self.assertIn('Keys in the answer: id:3, nm:3', out)
        self.assertFalse(os.path.exists(self.out))
        # отрицательный контроль: те же объекты с датчиками -- проходят
        code, _out, _ = self.run_tool(SensorServer())
        self.assertEqual(code, gi.EXIT_OK)

    def test_a_rejected_login_is_exit_3(self):
        code, _out, err = self.run_tool(SensorServer(login_error=4))
        self.assertEqual(code, gi.EXIT_WIALON)
        self.assertIn('ERROR', err)
        self.assertFalse(os.path.exists(self.out))

    def test_a_failed_search_is_exit_3(self):
        code, out, _ = self.run_tool(SensorServer(search_error=7))
        self.assertEqual(code, gi.EXIT_WIALON)
        self.assertIn('FAILED: core/search_items', out)

    def test_an_empty_fleet_is_exit_3(self):
        code, out, _ = self.run_tool(SensorServer(units=[]))
        self.assertEqual(code, gi.EXIT_WIALON)
        self.assertIn('no objects in the answer', out)

    def test_a_missing_database_is_refused_and_not_created(self):
        missing = os.path.join(self.folder, 'nope.db')
        out, err = io.StringIO(), io.StringIO()
        with redirect_stdout(out), redirect_stderr(err):
            code = gi.main(['--db', missing, '--out', self.out])
        self.assertEqual(code, gi.EXIT_BAD_INPUT)
        self.assertFalse(os.path.exists(missing))

    def test_out_on_the_database_is_refused(self):
        before = digest(self.db)
        out, err = io.StringIO(), io.StringIO()
        with redirect_stdout(out), redirect_stderr(err):
            code = gi.main(['--db', self.db, '--out', self.db])
        self.assertEqual(code, gi.EXIT_BAD_INPUT)
        self.assertEqual(digest(self.db), before)

    def test_a_database_without_mapping_tables_is_refused(self):
        other = os.path.join(self.folder, 'other.db')
        sqlite3.connect(other).close()
        out, err = io.StringIO(), io.StringIO()
        with redirect_stdout(out), redirect_stderr(err):
            code = gi.main(['--db', other, '--out', self.out])
        self.assertEqual(code, gi.EXIT_BAD_INPUT)
        self.assertIn('vialon_mappings', err.getvalue())


if __name__ == '__main__':
    unittest.main()
