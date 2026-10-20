# -*- coding: utf-8 -*-
"""GPS-28 -- правила R и P против отчёта Wialon «Моточасы», только чтение.

Подставной Wialon: объекты с датчиками, сообщения, отчёт по суткам UTC.
Держится предрегистрация (трек GPS, §0.6, «Дополнение», 19.10.2026):
выборка (считаемые с датчиком зажигания и грузовые), сутки UTC, сутки в
счёте -- от двух точек, нет строки отчёта -- W = 0, K1/K3 для часов и
KM1/KM3 для пробега с их границами, два вердикта последними строками;
шаблон в запросе, только читающие сервисы, освобождение результата,
продолжение прерванного прогона.

Run:
  python -m unittest tests.test_gps_engine_report_check
"""

import csv
import io
import json
import os
import sqlite3
import sys
import tempfile
import unittest
import urllib.parse
from contextlib import redirect_stderr, redirect_stdout
from datetime import datetime, timezone
from unittest import mock

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if REPO_ROOT not in sys.path:
    sys.path.insert(0, REPO_ROOT)

import tools.gps_engine_report_check as check                      # noqa: E402
from gps_collector.tests.support import FakeResponse, FakeServer   # noqa: E402
from gps_collector.tests.support import Installed                  # noqa: E402
from gps_collector.wialon import Client                            # noqa: E402

QUIET = lambda *args, **kwargs: None                               # noqa: E731
UTC = timezone.utc
SENS = {'1': {'n': 'Зажигание', 't': 'engine operation', 'p': 'io_239'}}
READ_ONLY = {'token/login', 'core/search_items', 'messages/load_interval',
             'messages/unload', 'report/exec_report', 'report/get_result_rows',
             'report/cleanup_result', 'core/logout'}


def utc(text):
    return int(datetime.strptime(text, '%Y-%m-%d %H:%M').replace(
        tzinfo=UTC).timestamp())


def make_db(path):
    con = sqlite3.connect(path)
    con.executescript(
        'CREATE TABLE equipment (id INTEGER PRIMARY KEY, name TEXT, plate TEXT, '
        'category TEXT, organization_id INTEGER);'
        'CREATE TABLE vialon_mappings (id INTEGER PRIMARY KEY, vialon_name TEXT, '
        'wialon_id INTEGER, equipment_id INTEGER, skip BOOLEAN);'
        "INSERT INTO equipment VALUES (1, 'МТЗ', '1', 'mtz', 1);"
        "INSERT INTO equipment VALUES (2, 'Камаз', '2', 'yuk_transport', 1);"
        "INSERT INTO equipment VALUES (3, 'Чужой', '3', 'mtz', 1);"
        "INSERT INTO vialon_mappings VALUES (1, 'u1', 101, 1, 0);"
        "INSERT INTO vialon_mappings VALUES (2, 'u2', 102, 2, 0);"
        "INSERT INTO vialon_mappings VALUES (3, 'u3', 103, 3, 1);")
    con.commit()
    con.close()


# Шаблон 3 ресурса 285, как его отдал сервер зонду GPS-27 (19.10): столбцы,
# их параметры и группировка -- JSON в строке.
SERVER_TEMPLATE_3 = {
    'n': 'unit_engine_hours',
    'c': '["duration","absolute_eh_begin","absolute_eh_end","duration_move",'
         '"duration_stay"]',
    'cp': '[{},{},{},{},{}]', 's': '',
    'p': '{"grouping":"{\\"type\\":\\"day\\"}"}', 'f': 4369}
NOTHING = {'reportResult': {'msgsRendered': 0, 'stats': [], 'tables': [],
                            'attachments': []}}


def columns_of(template):
    """Столбцы таблицы так, как их разбирает сервер: только JSON-список."""
    try:
        value = json.loads(template['tbl'][0]['c'])
    except ValueError:
        return []
    return value if isinstance(value, list) else []


class ReportServer(FakeServer):
    """Wialon with an engine-hours report: {unit: {UTC day: cells}}.

    Как сервер 20.10: столбцы шаблона он берёт только JSON-списком, иначе --
    отчёт без единой таблицы (NOTHING). Объект из `empty` -- без таблицы
    при принятом шаблоне.
    """

    def __init__(self, report, refuse=(), empty=(), **kwargs):
        super().__init__(**kwargs)
        self.report = dict(report)
        self.report.setdefault(check.PROBE_UNIT,
                               {'2026-10-15': ('4:55:44', '104.05 km')})
        self.refuse = set(refuse)
        self.empty = set(empty)
        self.execs = []
        self.rows = []

    def urlopen(self, request, timeout=None):
        query = urllib.parse.parse_qs(request.data.decode('utf-8'))
        svc = query['svc'][0]
        params = json.loads(query['params'][0])
        if svc == 'report/exec_report':
            self.calls.append((svc, params))
            self.execs.append(params)
            unit = params['reportObjectId']
            if unit in self.refuse:
                return FakeResponse({'error': 4})
            if not columns_of(params['reportTemplate']) or unit in self.empty:
                self.rows = []
                return FakeResponse(NOTHING)
            days = self.report.get(unit, {})
            self.rows = [{'n': i, 't1': check.day_start(day) + 3600,
                          't2': check.day_start(day) + 7200,
                          'c': [str(i + 1), day, duration, km]}
                         for i, (day, (duration, km)) in
                         enumerate(sorted(days.items()))]
            return FakeResponse({'reportResult': {'tables': [
                {'name': 'unit_engine_hours', 'rows': len(self.rows)}]}})
        if svc == 'report/get_result_rows':
            self.calls.append((svc, params))
            return FakeResponse(self.rows)
        if svc == 'report/cleanup_result':
            self.calls.append((svc, params))
            return FakeResponse({})
        return FakeServer.urlopen(self, request, timeout)


def ignition_on(index, stamp):
    return {'p': {'io_239': 1}}


def fleet_server(refuse=(), report=None, empty=()):
    # 101 -- трактор, считается, датчик есть; 102 -- грузовой, считается
    # только пробег; 103 -- «не наш», вне выборки; 104 -- без машины и без
    # датчика зажигания, вне выборки.
    fleet = [{'id': 101, 'last_t': utc('2026-09-03 00:00'), 'sens': SENS},
             {'id': 102, 'last_t': utc('2026-09-03 00:00'), 'sens': SENS},
             {'id': 103, 'last_t': utc('2026-09-03 00:00'), 'sens': SENS},
             {'id': 104, 'last_t': utc('2026-09-03 00:00')}]
    if report is None:
        report = {101: {'2026-09-01': ('23:56:00', '0.62 km')},
                  102: {'2026-09-02': ('1:00:00', '12.5 km')}}
    return ReportServer(report, refuse=refuse, empty=empty, fleet=fleet,
                        step_s=240,
                        extras={101: ignition_on, 102: ignition_on,
                                103: ignition_on, 104: ignition_on})


class Base(unittest.TestCase):
    def setUp(self):
        self.folder = tempfile.mkdtemp()
        self.db = os.path.join(self.folder, 'transport.db')
        make_db(self.db)
        self.out = os.path.join(self.folder, 'check.csv')
        patcher = mock.patch.object(check.config, 'read_token',
                                    return_value='T')
        patcher.start()
        self.addCleanup(patcher.stop)

    def run_check(self, server, *extra):
        out, err = io.StringIO(), io.StringIO()
        with Installed(server), redirect_stdout(out), redirect_stderr(err):
            code = check.main(['--db', self.db, '--out', self.out,
                               '--from', '2026-09-01', '--to', '2026-09-02']
                              + list(extra),
                              client_factory=lambda pause: Client(pause=0.0,
                                                                  log=QUIET))
        return code, out.getvalue(), err.getvalue()

    def rows(self):
        with open(self.out, encoding='utf-8-sig', newline='') as handle:
            return list(csv.DictReader(handle, delimiter=';'))


class EndToEnd(Base):
    def test_the_sample_the_days_and_both_sides(self):
        server = fleet_server()
        code, out, err = self.run_check(server)
        self.assertEqual(code, check.EXIT_OK, err)
        rows = self.rows()
        self.assertEqual(sorted({r['wialon_id'] for r in rows}), ['101', '102'])
        first = [r for r in rows if r['wialon_id'] == '101'
                 and r['den_utc'] == '2026-09-01'][0]
        # Шаг 240 с, зажигание всё время включено: пары внутри суток UTC.
        # Подставной сервер шлёт первое сообщение через шаг после начала
        # интервала (00:04), последнее в сутках -- 23:56.
        self.assertEqual(int(first['nashe_vkl_s']), 86400 - 2 * 240)
        self.assertEqual(int(first['wialon_motochasy_s']), 23 * 3600 + 56 * 60)
        self.assertEqual(float(first['wialon_km']), 0.62)
        self.assertEqual(first['chasy_v_proverke'], 'da')
        truck = [r for r in rows if r['wialon_id'] == '102'
                 and r['den_utc'] == '2026-09-01'][0]
        self.assertEqual((truck['chasy_v_proverke'], truck['probeg_v_proverke']),
                         ('net', 'da'))
        # Нет строки отчёта -- W = 0.
        self.assertEqual(truck['wialon_motochasy_s'], '0')
        self.assertTrue(out.isascii())
        tail = out.strip().splitlines()[-2:]
        self.assertTrue(tail[0].startswith('VERDICT HOURS: '))
        self.assertTrue(tail[1].startswith('VERDICT MILEAGE: '))
        self.assertIn('NOT EVALUATED', tail[0])

    def test_only_reading_services_and_the_template_in_the_request(self):
        server = fleet_server()
        code, out, _ = self.run_check(server)
        self.assertEqual(code, check.EXIT_OK)
        self.assertLessEqual({svc for svc, _ in server.calls}, READ_ONLY)
        # Первый отчёт -- пробные сутки, дальше -- объекты выборки.
        self.assertEqual([e['reportObjectId'] for e in server.execs],
                         [923, 101, 102])
        self.assertEqual(server.execs[0]['interval'],
                         {'from': utc('2026-10-14 19:00'),
                          'to': utc('2026-10-15 19:00') - 1, 'flags': 0})
        self.assertIn('template check on object 923, 2026-10-15: engine hours '
                      '4:55:44, mileage 104.05 km', out)
        exec_params = server.execs[1]
        self.assertEqual(exec_params['reportTemplateId'], 0)
        self.assertEqual(exec_params['reportResourceId'], 285)
        table = exec_params['reportTemplate']['tbl'][0]
        self.assertEqual(table['n'], 'unit_engine_hours')
        self.assertEqual(json.loads(table['c']), ['duration', 'mileage'])
        self.assertEqual(len(json.loads(table['cl'])), 2)
        self.assertEqual(json.loads(table['cp']), [{}, {}])
        self.assertEqual(json.loads(json.loads(table['p'])['grouping']),
                         {'type': 'day'})
        # Тот же вид, что у шаблона 3 сервера, кроме столбцов: строки JSON
        # без пробелов, буква в букву.
        self.assertEqual(table['p'], SERVER_TEMPLATE_3['p'])
        self.assertEqual(table['f'], SERVER_TEMPLATE_3['f'])
        self.assertEqual(type(json.loads(table['cp'])),
                         type(json.loads(SERVER_TEMPLATE_3['cp'])))
        self.assertIn('duration', json.loads(SERVER_TEMPLATE_3['c']))
        self.assertEqual(exec_params['interval']['from'], utc('2026-09-01 00:00'))
        self.assertEqual(exec_params['interval']['to'],
                         utc('2026-09-02 00:00') + 86399)
        self.assertEqual(sum(1 for svc, _ in server.calls
                             if svc == 'report/cleanup_result'), len(server.execs))

    def test_a_comma_separated_column_list_is_the_failure_of_20_10(self):
        """Отрицательный контроль: шаблон 20.10 подставной сервер не берёт."""
        old = json.loads(json.dumps(check.REPORT_TEMPLATE))
        old['tbl'][0]['c'] = 'duration,mileage'
        self.assertEqual(columns_of(old), [])
        self.assertEqual(columns_of(check.REPORT_TEMPLATE), ['duration', 'mileage'])
        with mock.patch.object(check, 'REPORT_TEMPLATE', old):
            code, out, _ = self.run_check(fleet_server())
        self.assertEqual(code, check.EXIT_WIALON)
        self.assertIn('the template in the request was not accepted', out)

    def test_a_template_check_without_engine_hours_is_code_3(self):
        # Нет таблицы, отказ кодом, таблица без часов -- шаблону не верим,
        # и долгий прогон не начинается: CSV не создан, сообщений не спрошено.
        for server in (fleet_server(empty={923}), fleet_server(refuse={923}),
                       fleet_server(report={923: {'2026-10-15': ('0:00:00',
                                                                 '0 km')}})):
            with self.subTest(server=server):
                code, out, _ = self.run_check(server)
                self.assertEqual(code, check.EXIT_WIALON)
                self.assertIn('FAILED: the engine-hours report gave no table '
                              'with engine hours on the check object 923', out)
                self.assertIn('nothing was checked', out)
                self.assertFalse(os.path.exists(self.out))
                self.assertEqual([e['reportObjectId'] for e in server.execs],
                                 [923])
                self.assertNotIn('messages/load_interval',
                                 {svc for svc, _ in server.calls})
                self.assertNotIn('VERDICT', out)

    def test_an_object_without_a_table_is_w_zero(self):
        # Шаблон проверен -- значит, отчёт без таблицы -- объект без моточасов
        # (по предрегистрации нет строки -- W = 0), а не отказ.
        code, out, _ = self.run_check(fleet_server(empty={101}))
        self.assertEqual(code, check.EXIT_OK)
        self.assertIn('objects whose report had no table, W = 0 on their days: 1',
                      out)
        first = [r for r in self.rows() if r['wialon_id'] == '101']
        self.assertTrue(first)
        self.assertEqual({r['wialon_motochasy_s'] for r in first}, {'0'})
        self.assertEqual({r['wialon_km'] for r in first}, {'0.0'})

    def test_a_refused_report_is_code_1_whatever_the_object(self):
        code, out, _ = self.run_check(fleet_server(refuse={101}))
        self.assertEqual(code, check.EXIT_PARTIAL)
        self.assertIn('objects not read (run again with --resume): 101', out)

    def test_a_later_refusal_is_code_1_and_resume_finishes_the_job(self):
        code, out, _ = self.run_check(fleet_server(refuse={102}))
        self.assertEqual(code, check.EXIT_PARTIAL)
        self.assertIn('objects not read (run again with --resume): 102', out)
        self.assertEqual({r['wialon_id'] for r in self.rows()}, {'101'})
        server = fleet_server()
        code, out, _ = self.run_check(server, '--resume')
        self.assertEqual(code, check.EXIT_OK)
        self.assertEqual([e['reportObjectId'] for e in server.execs], [923, 102])
        self.assertEqual(sorted({r['wialon_id'] for r in self.rows()}),
                         ['101', '102'])
        self.assertIn('already in the csv 1', out)

    def test_bad_input_is_code_2(self):
        for extra in (('--from', '01.09.2026'),
                      ('--from', '2026-09-05', '--to', '2026-09-01')):
            code, _, _ = self.run_check(fleet_server(), *extra)
            self.assertEqual(code, check.EXIT_BAD_INPUT, extra)
        os.remove(self.db)
        code, _, err = self.run_check(fleet_server())
        self.assertEqual(code, check.EXIT_BAD_INPUT)
        self.assertFalse(os.path.exists(self.db))

    def test_resume_without_a_csv_is_code_2(self):
        code, _, err = self.run_check(fleet_server(), '--resume')
        self.assertEqual(code, check.EXIT_BAD_INPUT)
        self.assertIn('--resume', err)


class OnePointDays(Base):
    def test_a_day_with_a_single_point_is_not_a_day_of_the_check(self):
        server = fleet_server()
        real = server.messages

        def sparse(unit_id, t_from, t_to):
            found = real(unit_id, t_from, t_to)
            if unit_id != 101:
                return found
            # у 101 за 02.09 остаётся одна точка
            second = check.day_start('2026-09-02')
            day_two = [m for m in found if m['t'] >= second]
            return [m for m in found if m['t'] < second] + day_two[:1]

        server.messages = sparse
        code, _, err = self.run_check(server)
        self.assertEqual(code, check.EXIT_OK, err)
        days = {r['den_utc'] for r in self.rows() if r['wialon_id'] == '101'}
        self.assertEqual(days, {'2026-09-01'})


class Parsing(unittest.TestCase):
    def test_durations(self):
        self.assertEqual(check.parse_duration('4:55:44'), 17744)
        self.assertEqual(check.parse_duration('27:01:02'), 97262)
        self.assertEqual(check.parse_duration('1 days 02:00:00'), 93600)
        self.assertEqual(check.parse_duration({'t': '0:00:02', 'v': 2}), 2)
        self.assertIsNone(check.parse_duration('-----'))

    def test_kilometres(self):
        self.assertEqual(check.parse_km('104.05 km'), 104.05)
        self.assertEqual(check.parse_km('1,5 km'), 1.5)
        self.assertEqual(check.parse_km('0 km'), 0.0)
        self.assertIsNone(check.parse_km(''))

    def test_days_are_utc_days(self):
        # 03:20 UTC 15.10 -- это 08:20 по Ташкенту, сутки те же; 22:00 UTC
        # 15.10 -- уже 16.10 по Ташкенту, а сутки отчёта -- 15.10.
        self.assertEqual(check.day_of(utc('2026-10-15 22:00')), '2026-10-15')


def row(on=None, w=0, km=0.0, wkm=0.0, hours=True):
    return {'chasy_v_proverke': 'da' if hours else 'net',
            'probeg_v_proverke': 'da',
            'nashe_vkl_s': '' if on is None else str(on),
            'wialon_motochasy_s': str(w), 'nashe_km': str(km),
            'wialon_km': str(wkm)}


class Judge(unittest.TestCase):
    def test_hours_met_and_its_boundaries(self):
        good = [row(on=36000, w=36000)] * 180 + [row(on=36000 + 5400, w=36000)] * 20
        idle = [row(on=0, w=0)] * 50
        self.assertEqual(check.judge(good + idle)['hours'], 'MET')
        # 21 из 200 вне допуска -- 89,5 %
        bad = ([row(on=36000, w=36000)] * 179
               + [row(on=36000 + 5401, w=36000)] * 21)
        self.assertEqual(check.judge(bad + idle)['hours'], 'NOT MET')
        # пол 1800 с: W = 1 ч, расхождение ровно полчаса -- в допуске
        floor = [row(on=1800, w=3600)] * 200
        self.assertEqual(check.judge(floor + idle)['k1'], (200, 200))

    def test_the_fifteen_percent_band_decides_the_verdict(self):
        # 30 из 200 суток ровно на 15 %: в допуске -- MET; при 10 % было бы 85 %.
        rows = ([row(on=36000, w=36000)] * 170
                + [row(on=36000 + 5400, w=36000)] * 30 + [row(on=0, w=0)] * 50)
        self.assertEqual(check.judge(rows)['hours'], 'MET')

    def test_a_failed_criterion_beats_a_small_sample(self):
        bad = [row(on=0, w=36000)] * 200
        self.assertEqual(check.judge(bad + [row(on=0, w=0)] * 10)['hours'],
                         'NOT MET')

    def test_hours_false_work_on_idle_days(self):
        good = [row(on=36000, w=36000)] * 200
        # 95 % от 100 суток простоя -- не больше пяти ложных «работ».
        idle = [row(on=0, w=0)] * 95 + [row(on=1800, w=30)] * 5
        self.assertEqual(check.judge(good + idle)['hours'], 'MET')
        idle = [row(on=0, w=0)] * 94 + [row(on=1800, w=30)] * 6
        self.assertEqual(check.judge(good + idle)['hours'], 'NOT MET')
        idle = [row(on=1799, w=30)] * 100
        self.assertEqual(check.judge(good + idle)['k3'], (100, 100))

    def test_mileage_met_and_its_floor(self):
        # 30 суток с расхождением ровно 1 км при W = 5 км: пол 1 км их
        # держит в допуске (15 % от 5 км -- только 0,75 км).
        good = [row(km=10.0, wkm=10.0)] * 170 + [row(km=4.0, wkm=5.0)] * 30
        idle = [row(km=0.0, wkm=0.1)] * 50
        result = check.judge(good + idle)
        self.assertEqual(result['mileage'], 'MET')
        self.assertEqual(result['km1'], (200, 200))
        miss = [row(km=10.0, wkm=10.0)] * 179 + [row(km=3.0, wkm=5.0)] * 21
        self.assertEqual(check.judge(miss + idle)['mileage'], 'NOT MET')
        false_work = [row(km=0.0, wkm=0.1)] * 47 + [row(km=3.0, wkm=0.2)] * 3
        self.assertEqual(check.judge(good + false_work)['mileage'], 'NOT MET')

    def test_small_samples_are_not_evaluated_and_trucks_do_not_count_hours(self):
        self.assertEqual(check.judge([row(on=36000, w=36000)] * 10)['hours'],
                         'NOT EVALUATED')
        rows = [row(on=None, w=36000, hours=False)] * 300
        self.assertEqual(check.judge(rows)['k1'], (0, 0))


if __name__ == '__main__':
    unittest.main()
