# -*- coding: utf-8 -*-
"""DRONE-FIELD-PASSPORT-001: живой UAT-зонд `tools/dji_field_passport_uat.py`.

Зонд идёт на площадку и проверяет копию production, поэтому здесь
держится то, что должно быть верным ДО поездки:

  * файл -- чистый ASCII (на сервер он попадает через `git show |
    Set-Content -Encoding ASCII`, не-ASCII байт там исказится);
  * на синтетике сценария ядра все ворота проходят, а все классы случаев
    находятся запросом, без подстановки номеров;
  * ворота различают: подтекающее членство, MAX(id) вместо последней
    наблюдавшейся ревизии и утечка на страницу роняют прогон (код 4);
  * база не пишется: часть данных -- `mode=ro`; копия для страниц не может
    лежать в папке `transport-report*` и не может быть самой базой;
  * страницы на копии: RU и UZ, 200, без утечек, плитки = провайдер.

ВСЕ ДАННЫЕ СИНТЕТИЧЕСКИЕ (сценарий `tests/test_drone_field_passport_core`).
"""

import io
import json
import os
import shutil
import sqlite3
import tempfile
import unittest

from tests.harness import app, TEST_DB_PATH

import tests.test_drone_field_passport_core as core
from tests.test_drone_field_passport_001 import Seeded

from dji_area import field_store as fs

import importlib.util

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PROBE_PATH = os.path.join(REPO_ROOT, 'tools', 'dji_field_passport_uat.py')


def load_probe():
    spec = importlib.util.spec_from_file_location('dji_field_passport_uat',
                                                  PROBE_PATH)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


probe = load_probe()


def gates_of(out_dir):
    with open(os.path.join(out_dir, 'uat_report.json'),
              encoding='utf-8') as handle:
        report = json.load(handle)
    return {g['id']: g for g in report['gates']}, report


class Source(unittest.TestCase):

    def test_the_probe_is_pure_ascii(self):
        with open(PROBE_PATH, 'rb') as handle:
            data = handle.read()
        self.assertEqual([b for b in data if b > 127], [])

    def test_screen_strings_are_the_real_ones(self):
        """Escapes in the probe equal the strings the templates print."""
        from dji_area import field_view as fv
        for name, text in (('S_CONFIRMED', 'Подтверждённые работы'),
                           ('S_SHARED', 'Вылеты на тех же границах, '
                                        'привязанные к другим записям DJI'),
                           ('S_HEADER', 'Запись поля DJI'),
                           ('S_NOT_CALC', 'не рассчитано'),
                           ('S_COUNTED_OWN',
                            'вылет учтён только в своей записи')):
            self.assertEqual(getattr(probe, name), text, name)
        self.assertIn(probe.S_NOT_SAVED, fv.STATE_LABELS[
            fv.STATE_IDENTIFIED][0])


class DataPart(unittest.TestCase):
    """Часть данных на синтетической базе, только чтение."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix='field_passport_uat_')
        self.db = os.path.join(self.tmp, 'copy.db')
        con = core.make_schema(self.db)
        core.seed_scenario(con)
        core.seed_revisited_land(con)
        con.close()
        core.add_geometry_later(self.db, core.MD5_NO_BYTES, verified=1)
        self.out = os.path.join(self.tmp, 'out')

    def tearDown(self):
        shutil.rmtree(self.tmp, True)

    def run_probe(self, *extra):
        buf = io.StringIO()
        code = probe.main(['--db', self.db, '--out-dir', self.out] +
                          list(extra), out=buf)
        return code, buf.getvalue()

    def test_every_gate_passes_and_every_case_is_found(self):
        before = os.path.getmtime(self.db), os.path.getsize(self.db)
        code, text = self.run_probe()
        self.assertEqual(code, probe.EXIT_OK, text)
        text.encode('ascii')
        gates, report = gates_of(self.out)
        self.assertFalse([g for g in gates.values() if g['status'] == 'FAIL'])
        cases = report['cases']
        self.assertTrue(cases['exact_field'].startswith('land=' + core.LAND_A))
        self.assertIn('flight=%d' % core.F_A_IDENT,
                      cases['awaiting_recalc_flight'])
        self.assertIn('land_a=' + core.LAND_A, cases['shared_md5'])
        self.assertIn('land_b=' + core.LAND_B, cases['shared_md5'])
        self.assertEqual(cases['reobserved_record'], 'land=' + core.LAND_R)
        self.assertEqual(gates['late_bytes.state_stays_identified']['status'],
                         'PASS')
        self.assertEqual(
            gates['latest_revision.header_is_latest_observed']['status'],
            'PASS')
        self.assertEqual(
            gates['double_count.accepted_of_x_counted_once']['status'], 'PASS')
        # Только чтение: файл базы не тронут.
        self.assertEqual((os.path.getmtime(self.db),
                          os.path.getsize(self.db)), before)

    def test_negative_control_leaky_membership_fails_the_run(self):
        original = fs.confirmed_members
        fs.confirmed_members = lambda rows, land: [
            r for r in rows if r.get('field_land_uuid') == land]
        try:
            code, text = self.run_probe()
        finally:
            fs.confirmed_members = original
        self.assertEqual(code, probe.EXIT_GATE, text)
        gates, _ = gates_of(self.out)
        self.assertEqual(gates['A.membership']['status'], 'FAIL')

    def test_negative_control_max_id_revision_fails_the_run(self):
        original = fs._latest_order
        fs._latest_order = lambda alias: '%s.id DESC' % alias
        try:
            code, text = self.run_probe()
        finally:
            fs._latest_order = original
        self.assertEqual(code, probe.EXIT_GATE, text)
        gates, _ = gates_of(self.out)
        self.assertEqual(
            gates['latest_revision.header_is_latest_observed']['status'],
            'FAIL')

    def test_the_documented_page_copy_layout_is_accepted(self):
        """Регрессия UAT 02.10.2026: копия в раскладке 14.2 принимается."""
        safe = os.path.join(self.tmp, 'VehicleSoft_FieldPassport_UAT',
                            '20261002_111704')
        os.makedirs(safe)
        page_copy = os.path.join(safe, 'page_copy.db')
        shutil.copy(self.db, page_copy)
        self.assertIsNone(probe.refuse_page_copy(page_copy, self.db))

    def test_refusals(self):
        missing = os.path.join(self.tmp, 'absent.db')
        code = probe.main(['--db', missing, '--out-dir', self.out],
                             out=io.StringIO())
        self.assertEqual(code, probe.EXIT_NO_DB)
        self.assertFalse(os.path.exists(missing))
        empty = os.path.join(self.tmp, 'empty.db')
        sqlite3.connect(empty).close()
        code = probe.main(['--db', empty, '--out-dir', self.out],
                             out=io.StringIO())
        self.assertEqual(code, probe.EXIT_NO_TABLES)
        code = probe.main(['--db', self.db], out=io.StringIO())
        self.assertEqual(code, probe.EXIT_USAGE)
        # Копия для страниц: не в папке transport-report* (площадка,
        # production, их резервные копии на D:) и не сама база.
        live = os.path.join(self.tmp, 'transport-report-staging', 'instance')
        os.makedirs(live)
        live_db = os.path.join(live, 'transport.db')
        shutil.copy(self.db, live_db)
        backups = os.path.join(self.tmp, 'transport-report-backups', 'staging',
                               'field_passport_uat', '20261002_111704',
                               'probe')
        os.makedirs(backups)
        backup_copy = os.path.join(backups, 'page_copy.db')
        shutil.copy(self.db, backup_copy)
        for page_copy in (live_db, backup_copy, self.db):
            self.assertIsNotNone(probe.refuse_page_copy(page_copy, self.db),
                                 page_copy)
            code = probe.main(['--db', self.db, '--out-dir', self.out,
                               '--page-copy', page_copy], out=io.StringIO())
            self.assertEqual(code, probe.EXIT_USAGE, page_copy)


class RunbookBlocks(unittest.TestCase):
    """§14 документа: блоки, которые владелец вставит на SRV-YOQSH.

    Проверяется то, что ломается молча и стоит дорого: production никогда
    не цель записи, миграций нет, службы трогаются только площадочные, код
    площадки -- ровно e7e97f1, блоки -- ASCII без плейсхолдеров.
    """

    UAT_SHA = 'e7e97f1f3193eb7e5081478d2024b19267378d37'

    @classmethod
    def setUpClass(cls):
        import re
        path = os.path.join(REPO_ROOT, 'docs', 'DRONE_FIELD_PASSPORT_001.md')
        with open(path, encoding='utf-8') as handle:
            text = handle.read()
        section = text[text.index('## 14. UAT'):text.index('## 15. ')]
        cls.blocks = re.findall(r'```(?:powershell)?\n(.*?)```', section,
                                re.S)
        cls.big = [b for b in cls.blocks if b.startswith('& {')]

    def test_there_are_five_main_blocks(self):
        # 14.2, 14.3, 14.4, 14.6 and the page-copy cleanup after 14.6.
        self.assertEqual(len(self.big), 5)

    def test_the_documented_page_copy_is_accepted_by_the_probe(self):
        """UAT 02.10.2026: §14.2 put the copy under D:\\transport-report-
        backups and the probe refused it (exit 1). The rule is right; the
        runbook was wrong. Every page-copy path the runbook builds must pass
        the probe's own rule, and the old path must still be refused."""
        import re
        roots = set()
        for block in self.big:
            roots.update(re.findall(r"'(C:\\VehicleSoft_FieldPassport_UAT)'",
                                    block))
            for line in block.splitlines():
                if 'page_copy.db' in line:
                    self.assertTrue('$pageDir' in line or
                                    'VehicleSoft_FieldPassport_UAT' in line,
                                    line)
                    self.assertNotIn('$probeDir', line)
                    self.assertNotIn('$backupDir', line)
        self.assertEqual(roots, {'C:\\VehicleSoft_FieldPassport_UAT'})
        documented = 'C:\\VehicleSoft_FieldPassport_UAT\\20261002_111704' \
            '\\page_copy.db'
        self.assertFalse(probe.inside_transport_report(documented))
        old = ('D:\\transport-report-backups\\staging\\field_passport_uat'
               '\\20261002_111704\\probe\\page_copy.db')
        self.assertTrue(probe.inside_transport_report(old))
        for live in ('C:\\transport-report\\instance\\transport.db',
                     'C:\\transport-report-staging\\instance\\transport.db',
                     'C:/Transport-Report-Staging/instance/x.db'):
            self.assertTrue(probe.inside_transport_report(live), live)

    def test_blocks_are_ascii_without_placeholders(self):
        import re
        for block in self.blocks:
            self.assertEqual([c for c in block if ord(c) > 127], [], block)
            self.assertNotIn('&&', block)
            self.assertIsNone(re.search(r'<[a-z][^>|]*>', block), block)

    def test_production_is_never_written(self):
        for block in self.big:
            for line in block.splitlines():
                if '$prodRoot' not in line and 'C:\\transport-report\\' not in line:
                    continue
                for verb in ('Copy-Item', 'Remove-Item', 'Move-Item',
                             'Set-Content', 'Stop-Service', 'Start-Service',
                             'Restart-Service', 'checkout', 'migrate_'):
                    self.assertNotIn(verb, line, line)

    def test_only_staging_services_are_stopped_or_started(self):
        import re
        for block in self.blocks:
            for name in re.findall(
                    r'(?:Stop|Start|Restart)-Service -Name (\S+)', block):
                self.assertIn(name, ('$service', 'TransportBot003Staging',
                                     'TransportBotStaging'), block)
            for value in re.findall(r"\$service\s*=\s*'([^']+)'", block):
                self.assertEqual(value, 'TransportReportStaging')

    def test_no_migration_runs(self):
        for block in self.blocks:
            self.assertNotIn('migrate_', block)

    def test_staging_code_is_exactly_the_reviewed_commit(self):
        import re
        shas = set()
        for block in self.big:
            shas.update(re.findall(r"\$sha\s*=\s*'([0-9a-f]+)'", block))
        self.assertEqual(shas, {self.UAT_SHA})
        deploy = self.big[0]
        self.assertIn('git checkout --detach $sha', deploy)
        self.assertIn("'DRONE-FIELD-PASSPORT-001'", deploy)
        # После e7e97f1 в ветке разрешены только документы, тесты и зонд.
        self.assertIn('tools/dji_field_passport_uat', deploy)


class PagePart(Seeded):
    """Страницы через тестовый клиент: RU, UZ, утечки, плитки, история."""

    def setUp(self):
        super(PagePart, self).setUp()
        con = sqlite3.connect(TEST_DB_PATH)
        try:
            core.seed_revisited_land(con)
        finally:
            con.close()
        core.add_geometry_later(TEST_DB_PATH, core.MD5_NO_BYTES, verified=1)
        self.out = tempfile.mkdtemp(prefix='field_passport_uat_pages_')

    def tearDown(self):
        shutil.rmtree(self.out, True)

    def run_probe(self):
        buf = io.StringIO()
        code = probe.main(['--db', TEST_DB_PATH, '--out-dir', self.out,
                           '--page-copy', TEST_DB_PATH, '--runs', '1'],
                          out=buf, flask_app=app)
        return code, buf.getvalue()

    def test_pages_pass_in_both_languages(self):
        code, text = self.run_probe()
        self.assertEqual(code, probe.EXIT_OK, text)
        gates, report = gates_of(self.out)
        for gid in ('pages.all_200_and_lang', 'pages.no_leaks',
                    'page.card_A_tiles_equal_provider',
                    'page.card_A_confirmed_count',
                    'page.shared_B_x_not_in_confirmed',
                    'page.header_is_latest_observed',
                    'page.no_calc_accepted_is_empty',
                    'page.late_bytes_no_missing_label',
                    'page.history_old_passport_keeps_old_md5',
                    'pages.unknown_uuid_404', 'pages.anonymous_redirected',
                    'uz.card_title_is_uzbek'):
            self.assertEqual(gates[gid]['status'], 'PASS', (gid, text))
        langs = {key.split('/')[1] for key in report['data']['pages']}
        self.assertEqual(langs, {'ru', 'uz'})

    def test_negative_control_a_leak_on_a_page_fails_the_run(self):
        original = probe.FORBIDDEN
        # Строка, которая есть на каждой странице модуля, выдаётся за
        # утечку: детектор обязан её увидеть и уронить прогон.
        probe.FORBIDDEN = original + ('vs-card-title',)
        try:
            code, text = self.run_probe()
        finally:
            probe.FORBIDDEN = original
        self.assertEqual(code, probe.EXIT_GATE, text)
        gates, _ = gates_of(self.out)
        self.assertEqual(gates['pages.no_leaks']['status'], 'FAIL')


if __name__ == '__main__':
    unittest.main()
