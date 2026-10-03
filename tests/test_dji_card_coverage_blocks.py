# -*- coding: utf-8 -*-
"""Блоки владельца B0, B1 и R из docs/DRONE_CARD_COVERAGE_001.md.

[REASON]: блоки вставляются в консоль Windows PowerShell 5.1 ВЕРБАТИМ.
Свойства ниже ломаются молча -- ни глаз при чтении диффа, ни py_compile их не
ловят:
  * блок целиком в `& { ... }`, внутри только ASCII, нет `&&` и
    плейсхолдеров, python с пробелом в пути зовётся через `&`;
  * B1 и R говорят об одной и той же площадке (папка, база, службы, адрес,
    папка прогонов), пин и production-коммит B1 равны пину и коммиту B0;
  * production в B1 и R только читается: службы production не
    останавливаются, не запускаются и не перенастраиваются, git production
    вызывается только для `rev-parse`;
  * B1 не запускает ни миграций, ни пересчёта, ни сбора, ботов площадки
    переводит в `Disabled`, а `returned.txt` пишет только тогда, когда
    площадку не трогал;
  * R возвращает базу только при `swapped.txt` и только из строки
    `staging_final`, сохраняет базу пилота до перезаписи и пишет
    `returned.txt` последним.
Поведение блоков проверено на стенде (pwsh 7 и подмены служб, реестра,
планировщика и сети, настоящие git, python и SQLite) -- описание и результаты
в разделе 14 документа. Здесь -- только неизменные свойства текста.
Stdlib. Запуск:  python -m unittest tests.test_dji_card_coverage_blocks
"""
import os
import re
import unittest

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DOC = os.path.join(REPO_ROOT, 'docs', 'DRONE_CARD_COVERAGE_001.md')
PROD_SERVICES = ('TransportReport', 'TransportBot', 'TransportBot003')


def blocks():
    text = open(DOC, encoding='utf-8').read()
    out = {}
    for name in ('B0', 'B1', 'R'):
        m = re.search(r'^### %s .*?\n```powershell\n(.*?)\n```' % name, text,
                      re.S | re.M)
        assert m, name
        out[name] = m.group(1)
    return out


def const(block, name):
    m = re.search(r"^\s*\$%s\s*=\s*'([^']*)'\s*$" % name, block, re.M)
    return m.group(1) if m else None


class Text(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        cls.b = blocks()

    def test_paste_safety(self):
        for name, block in self.b.items():
            with self.subTest(name):
                block.encode('ascii')
                self.assertTrue(block.startswith('& {'))
                self.assertTrue(block.rstrip().endswith('}'))
                self.assertNotIn('&&', block)
                self.assertIsNone(re.search(r'<[A-Za-z_ ]+>', block), 'placeholder')
                self.assertIn('STEP=PASS', block)
                self.assertIn('STEP=STOP', block)
                # python lives under 'Program Files': a call without '&' is a
                # string expression, not a command, and runs nothing.
                self.assertIsNone(re.search(r'(?m)(^|[;{]\s*)\$(python|py)\s+[^=\s]', block))
                self.assertRegex(block, r'& \$(python|exe) ')

    def test_same_staging_and_pins(self):
        b0, b1, r = self.b['B0'], self.b['B1'], self.b['R']
        for name in ('root', 'prodRoot', 'python', 'service', 'runRoot', 'db', 'site', 'work', 'expectedHost'):
            with self.subTest(name):
                self.assertIsNotNone(const(b1, name))
                self.assertEqual(const(b1, name), const(r, name))
        self.assertEqual(const(b1, 'pin'), const(b0, 'pin'))
        self.assertEqual(const(b1, 'branch'), const(b0, 'branch'))
        self.assertEqual(const(b1, 'prodExpected'), const(b0, 'expected'))
        self.assertEqual(const(b1, 'runRoot'), const(b0, 'pilotRoot'))
        self.assertTrue(const(b1, 'snapshot').startswith(const(b1, 'baseline') + '\\snapshot\\'))
        self.assertTrue(const(b1, 'baseline').startswith(const(b1, 'runRoot') + '\\baseline_'))
        self.assertEqual(const(b1, 'root'), 'C:\\transport-report-staging')
        self.assertEqual(const(b1, 'db'), const(b1, 'root') + '\\instance\\transport.db')
        self.assertTrue(const(b1, 'site').endswith(':5051'))
        for name in ('B1', 'R'):
            self.assertIn("$bots         = @('TransportBotStaging', 'TransportBot003Staging')", self.b[name])

    def test_production_is_only_read(self):
        for name in ('B1', 'R'):
            block = self.b[name]
            with self.subTest(name):
                for verb in ('Set-Service', 'Stop-Service', 'Start-Service', 'Restart-Service'):
                    for m in re.finditer(r'%s -Name (\S+)' % verb, block):
                        self.assertIn(m.group(1), ('$name', '$service'), m.group(0))
                for svc in PROD_SERVICES:
                    for m in re.finditer(r"'%s'" % svc, block):
                        line = block[block.rfind('\n', 0, m.start()) + 1:block.find('\n', m.start())]
                        self.assertIn('$prodNames', line)
                for m in re.finditer(r'git -C \$prodRoot (\S+)', block):
                    self.assertEqual(m.group(1), 'rev-parse')
                self.assertNotIn("'C:\\transport-report\\instance", block)

    def test_b1_does_only_what_was_asked(self):
        b1 = self.b['B1']
        for word in ('migrate_', 'dji_area_recalc', 'drone_collector.main', 'dji_area_daily',
                     'measure', '--apply'):
            self.assertNotIn(word, b1)
        self.assertIn('Set-Service -Name $name -StartupType Disabled', b1)
        self.assertEqual(b1.count('Start-Service'), 1)
        self.assertIn('Start-Service -Name $service', b1)
        # returned.txt only on the path where staging was not touched
        write = "Set-Content -LiteralPath (Join-Path $runDir 'returned.txt')"
        self.assertEqual(b1.count(write), 1)
        i = b1.index(write)
        self.assertIn('} else {', b1[b1.rindex('if ($touched)', 0, i):i])
        # every marker of a change is written before the change it marks
        self.assertLess(b1.index("'bots_disabled.txt'"), b1.index('Set-Service -Name $name'))
        self.assertLess(b1.index("'refresh_launcher.txt'"), b1.index('Set-ItemProperty'))
        self.assertLess(b1.index("'swapped.txt'"), b1.index('Copy-Item -LiteralPath $snapshot'))
        self.assertLess(b1.index("Save-Backup 'staging_before'"), b1.index('$touched = $true'))
        self.assertLess(b1.index('$touched = $true'), b1.index('Set-Service -Name $name'))
        self.assertLess(b1.index("Save-Backup 'staging_final'"), b1.index('git checkout --quiet --detach $pin'))

    def test_restore_order(self):
        r = self.b['R']
        self.assertIn("Where-Object { $_ -like 'staging_final|*' }", r)
        self.assertIn("Test-Path -LiteralPath (Join-Path $run 'swapped.txt')", r)
        self.assertLess(r.index('--suffix pilot_final'), r.index('Copy-Item -LiteralPath $from.Path'))
        self.assertLess(r.index("Copy-Item -LiteralPath $f -Destination $rawDir"),
                        r.index('Copy-Item -LiteralPath $from.Path'))
        write = "Set-Content -LiteralPath (Join-Path $run 'returned.txt')"
        self.assertEqual(r.count(write), 1)
        self.assertLess(r.index('Copy-Item -LiteralPath $from.Path'), r.index(write))
        self.assertLess(r.index('Set-Service -Name $name -StartupType $want.StartType'), r.index(write))
        self.assertLess(r.index('"PROD_HEAD_AFTER="'), r.index(write))
        for word in ('$pin', 'migrate_', 'dji_area_recalc', 'drone_collector.main'):
            self.assertNotIn(word, r)


if __name__ == '__main__':
    unittest.main()
