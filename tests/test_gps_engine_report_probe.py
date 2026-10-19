# -*- coding: utf-8 -*-
"""GPS-27 -- зонд отчёта «Моточасы» Wialon: только чтение, всё в JSON.

Сервер подставной. Держится: только читающие сервисы; результат отчёта
освобождается после каждого выполнения, даже при ошибке; шаблон, переданный
в запросе, собран из колонок, которые назвал сам сервер; ответ без шаблонов
-- не «шаблонов нет», а прямое указание на возможно неверный флаг; консоль
ASCII; коды выхода.

Run:
  python -m unittest tests.test_gps_engine_report_probe
"""

import io
import json
import os
import sys
import tempfile
import unittest
import urllib.parse
from contextlib import redirect_stderr, redirect_stdout
from unittest import mock

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if REPO_ROOT not in sys.path:
    sys.path.insert(0, REPO_ROOT)

import tools.gps_engine_report_probe as probe                      # noqa: E402
from tests.test_gps_link_mappings import FakeResponse, Installed   # noqa: E402

TYPES = [{'n': 'unit_trips', 'l': 'Поездки', 'c': [{'n': 'time_begin'}]},
         {'n': 'unit_engine_hours', 'l': 'Моточасы',
          'c': [{'n': 'time_begin', 'l': 'Начало'},
                {'n': 'duration_ival', 'l': 'Моточасы'}]}]
ENGINE_TEMPLATE = {'id': 7, 'n': 'Моточасы по дням', 'ct': 'avl_unit',
                   'tbl': [{'n': 'unit_engine_hours', 'c': 'duration_ival'}]}
TRIPS_TEMPLATE = {'id': 3, 'n': 'Поездки', 'ct': 'avl_unit',
                  'tbl': [{'n': 'unit_trips'}]}


class ReportServer:
    def __init__(self, rep=True, exec_error=None, broken=None, templates=None):
        self.rep = rep
        self.exec_error = exec_error
        self.broken = broken
        self.templates = [TRIPS_TEMPLATE, ENGINE_TEMPLATE] if templates is None \
            else templates
        self.calls = []
        self.execs = []
        self.params = {}

    def urlopen(self, request, timeout=None):
        query = urllib.parse.parse_qs(request.data.decode('utf-8'))
        svc = query['svc'][0]
        params = json.loads(query['params'][0])
        self.calls.append(svc)
        self.params[svc] = params
        if svc == self.broken:
            raise OSError('connection reset')
        if svc == 'token/login':
            return FakeResponse({'eid': 'SESSION'})
        if svc == 'report/get_report_tables':
            return FakeResponse(TYPES)
        if svc == 'core/search_items':
            item = {'id': 285, 'nm': 'Бухоро агрокластер'}
            if self.rep:
                item['rep'] = {str(t['id']): {'id': t['id'], 'n': t['n'],
                                              'ct': t['ct']}
                               for t in self.templates}
            return FakeResponse({'items': [item, {'id': 170, 'nm': 'Дилер'}]})
        if svc == 'report/get_report_data':
            return FakeResponse([t for t in self.templates
                                 if t['id'] in params['col']])
        if svc == 'report/exec_report':
            self.execs.append(params)
            if self.exec_error is not None:
                return FakeResponse({'error': self.exec_error})
            return FakeResponse({'reportResult': {'tables': [{
                'name': 'unit_engine_hours', 'rows': 2,
                'header': ['Начало', 'Моточасы'], 'total': ['', '5:23:10']}]}})
        if svc == 'report/get_result_rows':
            return FakeResponse([{'c': ['08:00', '3:00:00']},
                                 {'c': ['14:00', '2:23:10']}])
        return FakeResponse({})


class Base(unittest.TestCase):
    def setUp(self):
        self.folder = tempfile.mkdtemp()
        self.out = os.path.join(self.folder, 'probe.json')
        patcher = mock.patch.object(probe.config, 'read_token', return_value='T')
        patcher.start()
        self.addCleanup(patcher.stop)

    def run_probe(self, server, *extra):
        out, err = io.StringIO(), io.StringIO()
        with Installed(server), redirect_stdout(out), redirect_stderr(err):
            code = probe.main(['--out', self.out, '--pause', '0'] + list(extra))
        return code, out.getvalue(), err.getvalue()

    def found(self):
        with open(self.out, encoding='utf-8') as handle:
            return json.load(handle)


class Probe(Base):
    def test_a_saved_engine_template_and_the_inline_one_are_both_run(self):
        server = ReportServer()
        code, out, err = self.run_probe(server)
        self.assertEqual(code, probe.EXIT_OK, err)
        self.assertEqual([e['reportTemplateId'] for e in server.execs], [7, 0])
        self.assertEqual(server.execs[0]['reportObjectId'], probe.DEFAULT_UNIT)
        self.assertEqual(server.execs[0]['interval']['to']
                         - server.execs[0]['interval']['from'], 86399)
        found = self.found()
        self.assertEqual(found['engine_templates'][0]['template']['id'], 7)
        self.assertEqual(found['runs'][0]['tables'][0]['total'], ['', '5:23:10'])
        self.assertEqual(len(found['runs'][0]['tables'][0]['rows']), 2)
        self.assertIn('templates with an engine-hours table: 1', out)
        self.assertIn('table unit_engine_hours, rows 2, total ["", "5:23:10"]',
                      out)
        self.assertTrue(out.isascii())

    def test_only_reading_services_are_used(self):
        server = ReportServer()
        self.run_probe(server)
        self.assertLessEqual(set(server.calls), set(probe.READ_ONLY_SERVICES))
        search = server.params['core/search_items']
        self.assertEqual(search['spec']['itemsType'], 'avl_resource')
        self.assertEqual(search['flags'], 0x2001)

    def test_every_result_is_freed_even_after_an_error(self):
        server = ReportServer()
        self.run_probe(server)
        self.assertEqual(server.calls.count('report/cleanup_result'), 2)
        failing = ReportServer(exec_error=4)
        code, out, _ = self.run_probe(failing)
        self.assertEqual(code, probe.EXIT_OK)
        self.assertEqual(failing.calls.count('report/cleanup_result'), 2)
        self.assertIn('run template 0 (inline): error 4', out)
        self.assertEqual(self.found()['runs'][1]['error'], 4)

    def test_the_inline_template_takes_the_columns_the_server_names(self):
        template = probe.inline_template(TYPES)
        table = template['tbl'][0]
        self.assertEqual(template['id'], 0)
        self.assertEqual(table['n'], 'unit_engine_hours')
        self.assertEqual(table['c'], 'time_begin,duration_ival')
        self.assertEqual(probe.inline_template([])['tbl'][0]['n'],
                         'unit_engine_hours')

    def test_no_rep_key_is_named_as_a_possibly_wrong_flag(self):
        server = ReportServer(rep=False)
        code, out, _ = self.run_probe(server)
        self.assertEqual(code, probe.EXIT_OK)
        self.assertIn('NO key rep in any resource - flag 0x2000 may be wrong',
                      out)
        self.assertEqual([e['reportTemplateId'] for e in server.execs], [0])
        self.assertEqual(server.execs[0]['reportResourceId'], 285)

    def test_without_an_engine_template_only_the_inline_one_runs(self):
        server = ReportServer(templates=[TRIPS_TEMPLATE])
        self.run_probe(server, '--unit', '1001', '--day', '2026-09-15')
        self.assertEqual([(e['reportTemplateId'], e['reportObjectId'])
                          for e in server.execs], [(0, 1001)])


class Refusals(Base):
    def test_bad_day_and_missing_folder_are_code_2(self):
        code, _, _ = self.run_probe(ReportServer(), '--day', '15.10.2026')
        self.assertEqual(code, probe.EXIT_BAD_INPUT)
        out, err = io.StringIO(), io.StringIO()
        with redirect_stdout(out), redirect_stderr(err):
            code = probe.main(['--out', os.path.join(self.folder, 'no', 'x.json')])
        self.assertEqual(code, probe.EXIT_BAD_INPUT)

    def test_a_refused_login_is_code_3(self):
        class Refused(ReportServer):
            def urlopen(self, request, timeout=None):
                if b'token%2Flogin' in request.data:
                    return FakeResponse({'error': 8})
                return super().urlopen(request, timeout)

        code, _, err = self.run_probe(Refused())
        self.assertEqual(code, probe.EXIT_WIALON)
        self.assertIn('error 8', err)

    def test_a_broken_connection_is_code_3_and_the_json_says_why(self):
        server = ReportServer(broken='report/get_report_data')
        with mock.patch.object(probe.config, 'RETRY_PAUSE_S', (0, 0, 0)):
            code, out, _ = self.run_probe(server)
        self.assertEqual(code, probe.EXIT_WIALON)
        self.assertIn('FAILED: OSError', out)
        self.assertIn('OSError', self.found()['failure'])
        self.assertIn('core/logout', server.calls)


if __name__ == '__main__':
    unittest.main()
