# -*- coding: utf-8 -*-
"""Ранбук GPS, раздел 12: выкладка ветки на площадку и возврат площадки.

Блоки вставляются владельцем в консоль SRV-YOQSH как есть, и ошибку в них
ловит сервер, а не CI. Здесь держится то, что проверка текста поймать может:

  * блоков два: «Выложить» и «Вернуть площадку»; оба -- `& { ... }` со
    `$ErrorActionPreference = 'Stop'` и `STEP=PASS` последней строкой;
  * боевое только читается: служба -- `TransportReportStaging`, служба
    `TransportReport` не останавливается и не запускается, пути боевого
    экземпляра не стоят ни в одной пишущей команде;
  * метки прогона (`swapped.txt`, `returned.txt`, `before_head.txt`, папки
    копий) названы в обоих блоках одинаково -- переименование в одном
    блоке молча сломало бы возврат;
  * [REASON]: Windows PowerShell 5.1 при `$ErrorActionPreference = 'Stop'`
    превращает первую строку stderr перенаправленной программы в
    остановку блока. Каждое `2>` стоит между `'Continue'` и `'Stop'`;
  * каждый упомянутый файл проекта существует, ревизия кода записана
    полностью и совпадает с названной в тексте раздела;
  * плейсхолдеров и `&&` нет, текст блоков -- только ASCII (консоль).

Поведение блоков проверено прогоном в PowerShell 7 на симуляции сервера
(см. трек GPS, 30.09); этот тест держит текст, чтобы правка не сломала его.
"""
import os
import re
import sys
import unittest

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if REPO_ROOT not in sys.path:
    sys.path.insert(0, REPO_ROOT)

RUNBOOK = os.path.join(REPO_ROOT, 'docs', 'GPS_ROLLOUT_RUNBOOK.md')


def section():
    with open(RUNBOOK, encoding='utf-8') as handle:
        text = handle.read()
    start = text.index('## 12. ')
    following = text.find('\n## ', start + 1)
    return text[start:] if following < 0 else text[start:following]


def blocks():
    return re.findall(r'```powershell\n(.*?)```', section(), re.S)


class Shape(unittest.TestCase):

    def test_two_blocks_each_stopping_on_the_first_failure(self):
        found = blocks()
        self.assertEqual(len(found), 2)
        for block in found:
            with self.subTest(block=block[:40]):
                lines = [line for line in block.splitlines() if line.strip()]
                self.assertEqual(lines[0], '& {')
                self.assertEqual(lines[1].strip(), "$ErrorActionPreference = 'Stop'")
                self.assertEqual(lines[-2].strip(), 'Write-Output "STEP=PASS"')
                self.assertEqual(lines[-1], '}')

    def test_no_placeholders_and_ascii_only(self):
        for block in blocks():
            with self.subTest(block=block[:40]):
                self.assertNotIn('&&', block)
                self.assertIsNone(re.search(r'<[^>\n]*>', block))
                self.assertTrue(block.isascii())

    def test_the_pinned_revision_is_complete_and_named_in_the_text(self):
        deploy = blocks()[0]
        sha = re.search(r"\$sha\s*=\s*'([0-9a-f]+)'", deploy).group(1)
        self.assertEqual(len(sha), 40)
        self.assertIn('**Ревизия кода:** `%s`' % sha, section())


class ProductionIsOnlyRead(unittest.TestCase):

    WRITES = re.compile(r'\b(Remove-Item|Move-Item|Set-Content|Out-File|'
                        r'Add-Content|New-Item|Clear-Content)\b')

    def test_the_staging_service_and_root_are_guarded(self):
        deploy = blocks()[0]
        self.assertIn("$service      = 'TransportReportStaging'", deploy)
        self.assertIn("$root         = 'C:\\transport-report-staging'", deploy)
        self.assertIn("if ((hostname) -ne $expectedHost)", deploy)
        self.assertIn("-notlike '*transport-report-staging*'", deploy)
        self.assertIn("if ($svc.Name -eq 'TransportReport')", deploy)

    def test_services_are_started_and_stopped_only_through_the_variable(self):
        for block in blocks():
            for line in block.splitlines():
                for verb in ('Stop-Service', 'Start-Service'):
                    if verb in line:
                        with self.subTest(line=line.strip()):
                            self.assertRegex(line, verb + r' -Name \$service\b')

    def test_production_paths_never_appear_in_a_writing_command(self):
        for block in blocks():
            for line in block.splitlines():
                production = ('$prodInstance' in line
                              or re.search(r'C:\\transport-report\\', line))
                if not production or '$prodInstance =' in line.replace(' ', ''):
                    continue
                with self.subTest(line=line.strip()):
                    self.assertIsNone(self.WRITES.search(line))
                    self.assertNotRegex(line, r'>\s*"?\$prodInstance')
                    copy = re.search(r'Copy-Item\s+(\S+)\s+(\S+)', line)
                    if copy:
                        self.assertNotIn('$prodInstance', copy.group(2))

    def test_the_production_database_is_read_through_the_backup_tool(self):
        deploy = blocks()[0]
        self.assertIn("Source = \"$prodInstance\\transport.db\"", deploy)
        self.assertIn('backup_transport_db.py --source $c.Source', deploy)


class TheTwoBlocksAgree(unittest.TestCase):

    MARKERS = ('swapped.txt', 'returned.txt', 'before_head.txt',
               'staging_before', 'staging_points_before', 'gps_points_')

    def test_run_markers_are_named_alike_in_both_blocks(self):
        deploy, back = blocks()
        for marker in self.MARKERS:
            with self.subTest(marker=marker):
                self.assertIn(marker, deploy)
                self.assertIn(marker, back)
        run_root = re.compile(r"\$runRoot\s*=\s*'([^']+)'")
        self.assertEqual(run_root.search(deploy).group(1),
                         run_root.search(back).group(1))

    def test_deploy_refuses_while_a_run_is_not_returned(self):
        deploy = blocks()[0]
        self.assertIn("'returned.txt'", deploy)
        self.assertIn('was not returned', deploy)


class WindowsPowerShell51(unittest.TestCase):

    def test_every_stderr_redirect_sits_between_continue_and_stop(self):
        for block in blocks():
            lines = block.splitlines()
            for number, line in enumerate(lines):
                if re.search(r'\s2>', line):
                    with self.subTest(line=line.strip()):
                        before = [l.strip() for l in lines[max(0, number - 3):number]]
                        after = [l.strip() for l in lines[number + 1:number + 4]]
                        self.assertIn("$ErrorActionPreference = 'Continue'", before)
                        self.assertIn("$ErrorActionPreference = 'Stop'", after)


class NamedFilesExist(unittest.TestCase):

    def test_scripts_and_test_modules_named_in_the_blocks_exist(self):
        deploy = blocks()[0]
        for script in ('backup_transport_db.py', 'tools\\check_db_lock.py',
                       'tools\\check_migration_drift.py',
                       'migrate_agro_work_001.py'):
            with self.subTest(script=script):
                self.assertIn(script, deploy)
                self.assertTrue(os.path.isfile(
                    os.path.join(REPO_ROOT, *script.split('\\'))))
        self.assertIn('-m gps.daily --catch-up', deploy)
        self.assertTrue(os.path.isfile(os.path.join(REPO_ROOT, 'gps', 'daily.py')))
        modules = re.search(r'-m unittest ([^\n]+)', deploy).group(1).split()
        self.assertGreaterEqual(len(modules), 3)
        for module in modules:
            with self.subTest(module=module):
                self.assertTrue(os.path.isfile(os.path.join(
                    REPO_ROOT, *module.split('.')) + '.py'))


if __name__ == '__main__':
    unittest.main()
