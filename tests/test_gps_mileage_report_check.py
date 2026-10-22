# -*- coding: utf-8 -*-
"""GPS-32 -- правило P у грузовиков против отчёта Wialon «Сводка», только чтение.

Подставной Wialon: грузовики, трактор, «не наша», сообщения по суткам и
отчёт «Сводка» по суткам UTC. Держится предрегистрация (трек GPS, §0.6,
«Дополнение к предрегистрации», 22.10.2026):
  * выборка -- только грузовые без пометки «не наша»;
  * период -- август и 01--21.10, сентябрь не спрашивается;
  * сутки в счёте -- от двух точек, нет строки отчёта -- W = 0;
  * эталон -- «Пробег по всем сообщениям», «в поездках» -- справочно;
  * B1 и B3 с их границами, выборки 200 и 50, KM1 только справочно;
  * вердикт последней строкой;
  * шаблон в запросе по образцу шаблона 23, только читающие сервисы,
    освобождение результата; пробный отчёт без пробега -- код 3 до прогона;
  * объект пишется в CSV целиком: отказ во втором периоде не оставляет
    половины, а --resume доделывает.

Run:
  python -m unittest tests.test_gps_mileage_report_check
"""

import csv
import io
import json
import math
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

import tools.gps_mileage_report_check as check                     # noqa: E402
from gps_collector.tests.support import FakeResponse, FakeServer   # noqa: E402
from gps_collector.tests.support import Installed                  # noqa: E402
from gps_collector.wialon import Client                            # noqa: E402

QUIET = lambda *args, **kwargs: None                               # noqa: E731
UTC = timezone.utc
READ_ONLY = {'token/login', 'core/search_items', 'messages/load_interval',
             'messages/unload', 'report/exec_report', 'report/get_result_rows',
             'report/cleanup_result', 'core/logout'}
LON, LAT = 64.4, 39.8


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
        "INSERT INTO equipment VALUES (3, 'Чужой', '3', 'yuk_transport', 1);"
        "INSERT INTO equipment VALUES (5, 'Исузу', '5', 'yuk_transport', 1);"
        "INSERT INTO vialon_mappings VALUES (1, 'u1', 101, 1, 0);"
        "INSERT INTO vialon_mappings VALUES (2, 'u2', 102, 2, 0);"
        "INSERT INTO vialon_mappings VALUES (3, 'u3', 103, 3, 1);"
        "INSERT INTO vialon_mappings VALUES (5, 'u5', 105, 5, NULL);")
    con.commit()
    con.close()


# Таблица «Сводка» шаблона 23 ресурса 285, как её отдал сервер зонду GPS-27
# (19.10), без масок датчиков: столбцы, параметры, порядок фильтров, флаги.
SERVER_TEMPLATE_23 = {
    'n': 'unit_generic',
    'c': '["mileage","fuel_level_begin","filled","thefted","fuel_level_end",'
         '"fuel_consumption_abs","eh","avg_speed"]',
    'cp': '[{},{},{},{},{},{},{},{}]', 's': '',
    'filter_order': ['base_eh_sensor', 'sensor_name'],
    'p': '{"grouping":"{\\"type\\":\\"day\\"}","custom_interval":{"type":0}}',
    'f': 4369}
NOTHING = {'reportResult': {'msgsRendered': 0, 'stats': [], 'tables': [],
                            'attachments': []}}


def columns_of(template):
    """Столбцы таблицы так, как их разбирает сервер: только JSON-список."""
    try:
        value = json.loads(template['tbl'][0]['c'])
    except ValueError:
        return []
    return value if isinstance(value, list) else []


def day_messages(day, km, speed=30.0, count=60):
    """Сообщения одних суток UTC: count точек раз в минуту с 06:00, путь km
    на север; при km = 0 -- стоящая машина со скоростью 0."""
    start = check.day_start(day) + 6 * 3600
    step = math.degrees(km / (count - 1) / 6371.0) if km else 0.0
    return [{'t': start + 60 * i,
             'pos': {'x': LON, 'y': LAT + step * i,
                     's': speed if km else 0.0, 'c': 0, 'sc': 14}}
            for i in range(count)]


class SummaryServer(FakeServer):
    """Wialon с отчётом «Сводка»: report {unit: {UTC day: (all, trips)}},
    tracks {unit: {UTC day: km}} -- сообщения.

    Как сервер 20.10: столбцы шаблона он берёт только JSON-списком, иначе --
    отчёт без единой таблицы. Объект из `empty` -- без таблицы при принятом
    шаблоне; из `refuse` -- отказ кодом; `refuse_after` -- отказ на втором и
    следующих отчётах объекта.
    """

    def __init__(self, report, tracks, refuse=(), empty=(), refuse_after=(),
                 **kwargs):
        super().__init__(**kwargs)
        self.report = dict(report)
        self.report.setdefault(check.PROBE_UNIT,
                               {'2026-10-15': ('230.50 km', '201.10 km')})
        self.tracks = tracks
        self.refuse = set(refuse)
        self.empty = set(empty)
        self.refuse_after = set(refuse_after)
        self.execs = []
        self.rows = []

    def messages(self, unit_id, t_from, t_to):
        out = []
        for day, km in sorted(self.tracks.get(unit_id, {}).items()):
            out.extend(m for m in day_messages(day, km) if t_from <= m['t'] < t_to)
        return out

    def urlopen(self, request, timeout=None):
        query = urllib.parse.parse_qs(request.data.decode('utf-8'))
        svc = query['svc'][0]
        params = json.loads(query['params'][0])
        if svc == 'report/exec_report':
            self.calls.append((svc, params))
            self.execs.append(params)
            unit = params['reportObjectId']
            earlier = sum(1 for e in self.execs[:-1]
                          if e['reportObjectId'] == unit)
            if unit in self.refuse or (unit in self.refuse_after and earlier):
                return FakeResponse({'error': 4})
            if not columns_of(params['reportTemplate']) or unit in self.empty:
                self.rows = []
                return FakeResponse(NOTHING)
            lo, hi = params['interval']['from'], params['interval']['to']
            days = {day: cells for day, cells in self.report.get(unit, {}).items()
                    if lo <= check.day_start(day) + 3600 <= hi}
            self.rows = [{'n': i, 't1': check.day_start(day) + 3600,
                          't2': check.day_start(day) + 7200,
                          'c': [str(i + 1), day, km_all, km_trips]}
                         for i, (day, (km_all, km_trips)) in
                         enumerate(sorted(days.items()))]
            return FakeResponse({'reportResult': {'tables': [
                {'name': 'unit_generic', 'rows': len(self.rows)}]}})
        if svc == 'report/get_result_rows':
            self.calls.append((svc, params))
            return FakeResponse(self.rows)
        if svc == 'report/cleanup_result':
            self.calls.append((svc, params))
            return FakeResponse({})
        return FakeServer.urlopen(self, request, timeout)


# 102 и 105 -- грузовики; 101 -- трактор, 103 -- «не наша», 104 -- без машины.
TRACKS = {
    102: {'2026-08-05': 12.0, '2026-08-06': 0.0, '2026-09-10': 40.0,
          '2026-10-02': 25.0},
    105: {'2026-10-21': 8.0},
    101: {'2026-08-05': 5.0},
    103: {'2026-08-05': 5.0},
    104: {'2026-08-05': 5.0},
}
REPORT = {
    102: {'2026-08-05': ('12.40 km', '11.90 km'),
          '2026-08-06': ('0.10 km', '0 km'),
          '2026-09-10': ('41.00 km', '40.00 km')},
    105: {'2026-10-21': ('8.30 km', '8.00 km')},
}


def fleet_server(report=None, tracks=None, **kwargs):
    fleet = [{'id': unit, 'last_t': utc('2026-10-21 12:00'), 'cfl': 784}
             for unit in (101, 102, 103, 104, 105)]
    fleet[4]['cfl'] = 785
    return SummaryServer(REPORT if report is None else report,
                         TRACKS if tracks is None else tracks, fleet=fleet,
                         **kwargs)


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
            code = check.main(['--db', self.db, '--out', self.out] + list(extra),
                              client_factory=lambda pause: Client(pause=0.0,
                                                                  log=QUIET))
        return code, out.getvalue(), err.getvalue()

    def rows(self):
        with open(self.out, encoding='utf-8-sig', newline='') as handle:
            return {(r['wialon_id'], r['den_utc']): r
                    for r in csv.DictReader(handle, delimiter=';')}


class EndToEnd(Base):
    def test_the_trucks_the_periods_and_both_sides(self):
        code, out, err = self.run_check(fleet_server())
        self.assertEqual(code, check.EXIT_OK, err)
        rows = self.rows()
        # только грузовики без «не наша»; сентябрь вне периода
        self.assertEqual(sorted(rows), [('102', '2026-08-05'), ('102', '2026-08-06'),
                                        ('102', '2026-10-02'), ('105', '2026-10-21')])
        day = rows[('102', '2026-08-05')]
        self.assertAlmostEqual(float(day['nashe_km']), 12.0, delta=0.01)
        self.assertEqual((day['wialon_km'], day['wialon_km_v_poezdkah']),
                         ('12.4', '11.9'))
        self.assertEqual(day['tochek'], '60')
        # стоящая машина: пар со скоростью 0 у обеих точек правило P не считает
        self.assertEqual(rows[('102', '2026-08-06')]['nashe_km'], '0.0')
        # нет строки отчёта -- W = 0
        self.assertEqual(rows[('102', '2026-10-02')]['wialon_km'], '0.0')
        self.assertTrue(out.isascii())
        self.assertTrue(out.strip().splitlines()[-1].startswith(
            'VERDICT TRUCK MILEAGE: '))
        self.assertIn('trucks 2 | truck-days 4', out)
        self.assertIn('B1  W >= 3 km, ours >= 3 km: 2 of 2 = 100.0 %', out)
        self.assertIn('cfl of the trucks (how Wialon counts): 784 x1, 785 x1', out)

    def test_only_reading_services_and_the_template_in_the_request(self):
        server = fleet_server()
        code, out, _ = self.run_check(server)
        self.assertEqual(code, check.EXIT_OK)
        self.assertLessEqual({svc for svc, _ in server.calls}, READ_ONLY)
        # пробные сутки, затем по два периода на грузовик
        self.assertEqual([e['reportObjectId'] for e in server.execs],
                         [923, 102, 102, 105, 105])
        self.assertEqual(server.execs[0]['interval'],
                         {'from': utc('2026-10-14 19:00'),
                          'to': utc('2026-10-15 19:00') - 1, 'flags': 0})
        self.assertIn('template check on object 923, 2026-10-15: mileage in all '
                      'messages 230.50 km, in trips 201.10 km', out)
        self.assertEqual([(e['interval']['from'], e['interval']['to'])
                          for e in server.execs[1:3]],
                         [(utc('2026-08-01 00:00'), utc('2026-09-01 00:00') - 1),
                          (utc('2026-10-01 00:00'), utc('2026-10-22 00:00') - 1)])
        loads = {(t_from, t_to) for unit, t_from, t_to in server.loads}
        self.assertTrue(all(t_from >= utc('2026-10-01 00:00')
                            or t_to < utc('2026-09-01 00:00')
                            for t_from, t_to in loads), loads)
        params = server.execs[1]
        self.assertEqual((params['reportTemplateId'], params['reportResourceId']),
                         (0, 285))
        table = params['reportTemplate']['tbl'][0]
        self.assertEqual(table['n'], 'unit_generic')
        self.assertEqual(json.loads(table['c']), ['mileage_all', 'mileage'])
        self.assertEqual(len(json.loads(table['cl'])), 2)
        self.assertEqual(json.loads(table['cp']), [{}, {}])
        # тот же вид, что у шаблона 23 сервера, кроме столбцов
        for key in ('p', 'f', 'filter_order', 's'):
            self.assertEqual(table[key], SERVER_TEMPLATE_23[key], key)
        self.assertEqual(sum(1 for svc, _ in server.calls
                             if svc == 'report/cleanup_result'), len(server.execs))

    def test_a_comma_separated_column_list_is_not_accepted(self):
        """Отрицательный контроль: столбцы через запятую сервер не берёт."""
        old = json.loads(json.dumps(check.REPORT_TEMPLATE))
        old['tbl'][0]['c'] = 'mileage_all,mileage'
        self.assertEqual(columns_of(old), [])
        with mock.patch.object(check, 'REPORT_TEMPLATE', old):
            code, out, _ = self.run_check(fleet_server())
        self.assertEqual(code, check.EXIT_WIALON)
        self.assertIn('the template in the request was not accepted', out)

    def test_a_template_check_without_mileage_is_code_3(self):
        for server in (fleet_server(empty={923}), fleet_server(refuse={923}),
                       fleet_server(report={923: {'2026-10-15': ('0 km', '0 km')}})):
            with self.subTest(server=server):
                code, out, _ = self.run_check(server)
                self.assertEqual(code, check.EXIT_WIALON)
                self.assertIn('FAILED: the summary report gave no mileage on '
                              'the check object 923', out)
                self.assertIn('nothing was checked', out)
                self.assertFalse(os.path.exists(self.out))
                self.assertEqual([e['reportObjectId'] for e in server.execs],
                                 [923])
                self.assertNotIn('messages/load_interval',
                                 {svc for svc, _ in server.calls})
                self.assertNotIn('VERDICT', out)

    def test_a_truck_without_a_table_is_w_zero(self):
        code, out, _ = self.run_check(fleet_server(empty={105}))
        self.assertEqual(code, check.EXIT_OK)
        self.assertIn('trucks whose report had no table in a period, W = 0 on '
                      'its days: 1', out)
        self.assertEqual(self.rows()[('105', '2026-10-21')]['wialon_km'], '0.0')

    def test_a_refusal_in_the_second_period_leaves_no_half_object(self):
        code, out, _ = self.run_check(fleet_server(refuse_after={102}))
        self.assertEqual(code, check.EXIT_PARTIAL)
        self.assertIn('objects not read (run again with --resume): 102', out)
        self.assertEqual({unit for unit, _day in self.rows()}, {'105'})
        server = fleet_server()
        code, out, _ = self.run_check(server, '--resume')
        self.assertEqual(code, check.EXIT_OK)
        self.assertEqual([e['reportObjectId'] for e in server.execs],
                         [923, 102, 102])
        self.assertIn('already in the csv 1', out)
        self.assertEqual(len(self.rows()), 4)
        self.assertIn('trucks 2 | truck-days 4', out)

    def test_bad_input_is_code_2(self):
        code, _, err = self.run_check(fleet_server(), '--resume')
        self.assertEqual(code, check.EXIT_BAD_INPUT)
        self.assertIn('--resume', err)
        os.remove(self.db)
        code, _, err = self.run_check(fleet_server())
        self.assertEqual(code, check.EXIT_BAD_INPUT)
        self.assertFalse(os.path.exists(self.db))

    def test_a_day_with_a_single_point_is_not_a_day_of_the_check(self):
        server = fleet_server()
        real = server.messages

        def sparse(unit_id, t_from, t_to):
            found = real(unit_id, t_from, t_to)
            # у 105 за 21.10 остаётся одна точка
            return found[:1] if unit_id == 105 else found

        server.messages = sparse
        code, _, err = self.run_check(server)
        self.assertEqual(code, check.EXIT_OK, err)
        self.assertNotIn(('105', '2026-10-21'), self.rows())


class Preregistered(unittest.TestCase):
    def test_the_numbers_and_the_periods(self):
        self.assertEqual(check.PERIODS, (('2026-08-01', '2026-08-31'),
                                         ('2026-10-01', '2026-10-21')))
        self.assertEqual((check.WORK_KM, check.IDLE_KM), (3.0, 0.5))
        self.assertEqual((check.SHARE_1, check.SHARE_3, check.MIN_1, check.MIN_3),
                         (0.90, 0.95, 200, 50))
        self.assertEqual(check.REPORT_COLUMNS[0], 'mileage_all')

    def test_the_periods_skip_september(self):
        days = [day for first, last in check.PERIODS
                for day in check.period_days(first, last)]
        self.assertEqual(len(days), 31 + 21)
        self.assertFalse([day for day in days if day.startswith('2026-09')])


class Imports(unittest.TestCase):
    def test_system_python_is_enough(self):
        # Владелец запускает инструмент системным Python из C:\gps-tools: без
        # Flask, без models и без геометрии.
        import subprocess
        code = ('import sys, tools.gps_mileage_report_check; print(sorted(m for '
                'm in ("flask", "models", "numpy", "shapely") if m in sys.modules))')
        out = subprocess.run([sys.executable, '-c', code], cwd=REPO_ROOT,
                             capture_output=True, text=True, check=True).stdout
        self.assertEqual(out.strip(), '[]')


def row(km, wkm):
    return {'wialon_id': '1', 'nashe_km': str(km), 'wialon_km': str(wkm)}


class Judge(unittest.TestCase):
    def test_met_and_the_three_kilometre_boundary(self):
        work = [row(3.0, 3.0)] * 200               # 3 км -- уже «работа»
        idle = [row(2.999, 0.499)] * 50            # меньше 3 км -- не «работа»
        result = check.judge(work + idle)
        self.assertEqual(result['verdict'], 'MET')
        self.assertEqual((result['b1'], result['b3']), ((200, 200), (50, 50)))

    def test_b1_needs_ninety_percent(self):
        idle = [row(0.0, 0.0)] * 50
        ok = [row(10.0, 10.0)] * 180 + [row(2.9, 10.0)] * 20
        self.assertEqual(check.judge(ok + idle)['verdict'], 'MET')
        bad = [row(10.0, 10.0)] * 179 + [row(2.9, 10.0)] * 21
        self.assertEqual(check.judge(bad + idle)['verdict'], 'NOT MET')
        # W ровно 3 км -- сутки B1; 2,999 -- нет
        self.assertEqual(check.judge([row(0.0, 3.0)])['b1'], (0, 1))
        self.assertEqual(check.judge([row(0.0, 2.999)])['b1'], (0, 0))

    def test_b3_needs_ninety_five_percent_of_idle_days(self):
        work = [row(10.0, 10.0)] * 200
        ok = [row(0.0, 0.1)] * 95 + [row(3.0, 0.1)] * 5
        self.assertEqual(check.judge(work + ok)['verdict'], 'MET')
        bad = [row(0.0, 0.1)] * 94 + [row(3.0, 0.1)] * 6
        self.assertEqual(check.judge(work + bad)['verdict'], 'NOT MET')
        # W ровно 0,5 км -- уже не простой
        self.assertEqual(check.judge([row(5.0, 0.5)])['b3'], (0, 0))

    def test_small_samples_and_a_failed_criterion(self):
        self.assertEqual(check.judge([row(10.0, 10.0)] * 199
                                     + [row(0.0, 0.0)] * 50)['verdict'],
                         'NOT EVALUATED')
        self.assertEqual(check.judge([row(10.0, 10.0)] * 200
                                     + [row(0.0, 0.0)] * 49)['verdict'],
                         'NOT EVALUATED')
        # провал важнее малой выборки
        self.assertEqual(check.judge([row(0.0, 10.0)] * 200)['verdict'],
                         'NOT MET')

    def test_km1_is_reference_only(self):
        # Все сутки -- «работа» с обеих сторон, но наш пробег вдвое меньше:
        # KM1 провален, вердикт -- по порогу.
        rows = [row(5.0, 10.0)] * 200 + [row(0.0, 0.0)] * 50
        result = check.judge(rows)
        self.assertEqual(result['km1'], (0, 200))
        self.assertEqual(result['verdict'], 'MET')
        # пол 1 км: W = 5 км, расхождение ровно 1 км -- в допуске
        self.assertEqual(check.judge([row(4.0, 5.0)])['km1'], (1, 1))


if __name__ == '__main__':
    unittest.main()
