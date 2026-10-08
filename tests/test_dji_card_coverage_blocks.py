# -*- coding: utf-8 -*-
"""Блоки владельца B0, B1, D1, R, W0 и W1 из docs/DRONE_CARD_COVERAGE_001.md и файл W2+S2.

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
BlocksInPowerShell исполняет B1 и R, как они напечатаны в документе, против
подставного сервера (tests/card_pilot_blocks_harness.ps1): подменены имя
машины, службы, планировщик, реестр, диски и сеть; настоящие -- git (история
этого репозитория: main c34ea9a, пин 39eab50, площадка 2013bed, production
8df5683), python, backup_transport_db.py, check_db_lock.py,
check_migration_drift.py, инструмент пилота и SQLite. Пути: PASS, возврат и
повтор; отказы до первого изменения; каждая остановка после него и R после
неё; повторяемость R; повреждённая и отсутствующая база.
W0InPowerShell исполняет W0 (разведка рабочей машины) против подставной
рабочей машины (tests/card_pilot_w0_harness.ps1): ни один файл не меняется,
ни один секрет, мимо которого W0 проходит (сессия, .env, обёртки, процессы,
окружение, автозапуск, origin), не попадает ни в вывод, ни в журнал.
W0OnWindows исполняет W0 без подмен на Windows-раннере.
W1InPowerShell исполняет W1+S1 (канарейка 50 и пересчёт на площадке) против
подставного SRV-YOQSH (tests/card_pilot_w1_harness.ps1). Настоящие: git,
python, SQLite, инструмент пилота, пересчёт, перепись, config.py и
runlock.py сборщика и сам дочерний процесс с его окружением; работу
сборщика делает tests/card_pilot_fake_collector.py. Пути: GO_TO_500,
SIMPLIFY, STOP по малой доле карточек; каждый отказ до DJI; каждая
остановка после сбора и до пересчёта (код выхода, признаки DJI, три вылета
без карточки, время, чужие записи, запись в production, сессия, журнал
production); отказ на шаге S1 с перезапуском площадки.
W2Text и W2InPowerShell -- W2+S2, файл
ops/drone_card_coverage_001/W2_S2_remaining450_block.ps1, на том же
подставном SRV-YOQSH после настоящего прогона W1 (в стенде 60 = 50 + 10).
Пути: PASS; каждый отказ до DJI, в том числе по уликам W1 и по площадке,
изменённой после W1; остановки во время и после сбора; повторная вставка
(остановленный сбор не повторяется, после ворот -- только S2 без DJI,
выполненный -- отказ); занятый замок; прерванный S2; пересчёт сверх 450
отвергается. Функции проверок W2 -- те же строки, что в W1.
Нужен PowerShell и полная история git: CARD_PILOT_POWERSHELL=powershell (на
сервере -- Windows PowerShell 5.1, в CI -- задача windows-powershell-51) или
pwsh. Без переменной класс пропускается.
Stdlib. Запуск:  python -m unittest tests.test_dji_card_coverage_blocks
"""
import hashlib
import json
import os
import re
import shutil
import sqlite3
import subprocess
import sys
import tempfile
import time
import unittest

from datetime import datetime, timedelta, timezone

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if REPO_ROOT not in sys.path:
    sys.path.insert(0, REPO_ROOT)
from tests.test_dji_card_coverage_pilot import build_db  # noqa: E402
from tests.test_drone_field_passport_core import Seed  # noqa: E402

DOC = os.path.join(REPO_ROOT, 'docs', 'DRONE_CARD_COVERAGE_001.md')
PROD_SERVICES = ('TransportReport', 'TransportBot', 'TransportBot003')
POWERSHELL = os.environ.get('CARD_PILOT_POWERSHELL')
HERE = os.path.dirname(os.path.abspath(__file__))
HARNESS = os.path.join(HERE, 'card_pilot_blocks_harness.ps1')
HOLDER = os.path.join(HERE, 'card_pilot_db_holder.py')
WRITER = os.path.join(HERE, 'card_pilot_db_writer.py')


def blocks():
    with open(DOC, encoding='utf-8') as fh:
        text = fh.read()
    out = {}
    for name in ('B0', 'B1', 'D1', 'R', 'W0', 'W1'):
        m = re.search(r'^### %s .*?\n```powershell\n(.*?)\n```' % name, text,
                      re.S | re.M)
        assert m, name
        out[name] = m.group(1)
    return out


def without_process_pattern(block):
    """The block minus Get-IsoProcesses, whose regex names the cycle tools it looks for."""
    return '\n'.join(l for l in block.splitlines() if 'function Get-IsoProcesses' not in l)


def function_text(block, name):
    m = re.search(r'^  function %s\b.*?(?=^  function |^  New-Item )' % re.escape(name), block, re.S | re.M)
    assert m, name
    return m.group(0)


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
                # "$name:" in a string is a scope or drive name: the block does not even parse.
                self.assertIsNone(re.search(r'\$(?!env:|global:|script:)\w+:(?!:)', block))
                self.assertIn('STEP=PASS', block)
                self.assertIn('STEP=STOP', block)
                # python lives under 'Program Files': a call without '&' is a
                # string expression, not a command, and runs nothing.
                self.assertIsNone(re.search(r'(?m)(^|[;{]\s*)\$(python|py)\s+[^=\s]', block))
                if name != 'W0':  # W0 runs no python at all (test_w0_is_read_only)
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
            self.assertNotIn(word, without_process_pattern(b1))
        # the staging refresh task: disabled once, by name and folder; never
        # enabled, started, stopped, changed or re-registered by B1
        self.assertEqual(b1.count('Disable-ScheduledTask'), 1)
        self.assertIn('Disable-ScheduledTask -TaskName $isoTask -TaskPath $isoPath', b1)
        for word in ('Enable-ScheduledTask', 'Start-ScheduledTask', 'Stop-ScheduledTask', 'Set-ScheduledTask',
                     'Register-ScheduledTask', 'Unregister-ScheduledTask', 'Stop-Process'):
            self.assertNotIn(word, b1)
        self.assertIsNone(re.search(r'schtasks(\.exe)?\s+/', b1))
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
        # the task is verified, its state saved and the intent marked before it
        # is disabled; that is the first change, before the backup and the bots
        self.assertLess(b1.index("throw \"STEP FAILED: the $($c[0]) fingerprint of $isoTask"), b1.index("'task_before.txt'"))
        self.assertLess(b1.index("'task_before.txt'"), b1.index("'task_isolation.txt'"))
        self.assertLess(b1.index("'task_isolation.txt'"), b1.index('$touched = $true'))
        self.assertLess(b1.index('$touched = $true'), b1.index('Disable-ScheduledTask'))
        self.assertLess(b1.index('Disable-ScheduledTask'), b1.index("'task_disabled.txt'"))
        self.assertLess(b1.index("'task_disabled.txt'"), b1.index("Save-Backup 'staging_before'"))
        self.assertLess(b1.index("Save-Backup 'staging_before'"), b1.index('Set-Service -Name $name'))
        self.assertLess(b1.index('$touched = $true'), b1.index('Set-Service -Name $name'))
        self.assertLess(b1.index("Save-Backup 'staging_final'"), b1.index('git checkout --quiet --detach $pin'))

    def test_d1_is_read_only(self):
        d1 = self.b['D1']
        for word in ('Set-Service', 'Stop-Service', 'Start-Service', 'Restart-Service',
                     'Disable-ScheduledTask', 'Enable-ScheduledTask', 'Register-ScheduledTask',
                     'Unregister-ScheduledTask', 'Start-ScheduledTask', 'Stop-ScheduledTask',
                     'Set-ScheduledTask', 'Set-ItemProperty', 'New-ItemProperty',
                     'Remove-Item', 'Copy-Item', 'Move-Item', 'Set-Content', 'Add-Content',
                     'Out-File', 'Stop-Process', 'git checkout', 'git fetch', 'git reset',
                     'INSERT', 'UPDATE', 'DELETE', 'CREATE', 'DROP', 'migrate_'):
            self.assertNotIn(word, d1, word)
        self.assertIsNone(re.search(r'schtasks(\.exe)?\s+/', d1))
        for m in re.finditer(r'git -C \$prodRoot (\S+)', d1):
            self.assertEqual(m.group(1), 'rev-parse')
        self.assertIn("'file:' + path + '?mode=ro', uri=True", d1)
        # the only write: its own log in the pilot work folder
        self.assertEqual(re.findall(r'New-Item [^|]*', d1), ['New-Item -ItemType Directory -Force -Path $work '])
        self.assertIn("Start-Transcript -Path $log", d1)
        self.assertEqual(const(d1, 'taskName'), 'DjiAreaRefreshStaging')
        for name in ('root', 'prodRoot', 'python', 'service', 'db', 'work', 'expectedHost'):
            self.assertEqual(const(d1, name), const(self.b['B1'], name), name)

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
            self.assertNotIn(word, without_process_pattern(r))
        # the task: given back after the database, code, environment and
        # services, before returned.txt; enabled or disabled as saved, never run
        for word in ('Start-ScheduledTask', 'Stop-ScheduledTask', 'Set-ScheduledTask',
                     'Register-ScheduledTask', 'Unregister-ScheduledTask'):
            self.assertNotIn(word, r)
        self.assertIsNone(re.search(r'schtasks(\.exe)?\s+/', r))
        self.assertEqual(r.count('Enable-ScheduledTask'), 1)
        self.assertIn("Enable-ScheduledTask -TaskName $isoSaved['name'] -TaskPath $isoSaved['path']", r)
        for marker in ("'== 3. The staging database", "'== 4. The staging code", "'== 5. The staging site environment",
                       "'== 6. Staging services"):
            self.assertLess(r.index(marker), r.index('Enable-ScheduledTask'))
        self.assertLess(r.index('Enable-ScheduledTask'), r.index("'== 8. Production"))
        # what does not depend on Enabled is proved before the task is enabled
        self.assertLess(r.index("@('wrapper sha256'"), r.index('Enable-ScheduledTask'))
        self.assertLess(r.index('Enable-ScheduledTask'), r.index('if ($cur.XmlSha -ne $isoSaved'))
        self.assertLess(r.index('if ($cur.XmlSha -ne $isoSaved'), r.index(write))

    def test_task_guard_is_the_live_d1_output(self):
        b1 = self.b['B1']
        for name, value in (('isoTask', 'DjiAreaRefreshStaging'), ('isoPath', '\\'), ('isoEnabled', 'True'),
                            ('isoActionFp', 'ba8fe81d76697c38e7aa3e8f42069839365687652b00d45996d688b137845082'),
                            ('isoTriggerFp', '36a9e7f1c95b82ffb99743e0c5c4ce95d83c9a430aac59f84ef3cbfab6145068'),
                            ('isoXmlSha', 'ac522141953b6346ff76d2b0cad86f00ae10a52988984c2c4c5d8d91fc4f5117'),
                            ('isoWrapper', 'C:\\ProgramData\\VehicleSoft\\DjiAreaRefreshStaging.ps1'),
                            ('isoWrapperSha', '8ca2aeddfe664ce471bcdc991ea973ded9da2a2039e9de2ce8185b8c88b163c1')):
            self.assertEqual(const(b1, name), value, name)
        self.assertEqual(const(self.b['D1'], 'taskName'), const(b1, 'isoTask'))

    def test_fingerprints_are_computed_by_the_same_text(self):
        # D1 printed the fingerprints that B1 compares; R re-checks them. One
        # function text for all three, or the comparison is not a comparison.
        for name in ('Get-TextHash', 'Get-CimLine'):
            text = function_text(self.b['D1'], name)
            for block in ('B1', 'R'):
                self.assertEqual(function_text(self.b[block], name), text, (name, block))
        for name in ('Get-IsoState', 'Get-IsoProcesses'):
            self.assertEqual(function_text(self.b['B1'], name), function_text(self.b['R'], name), name)
        d1 = self.b['D1']
        self.assertIn("$actionLines += ([string]$a.Execute + '|' + [string]$a.Arguments + '|' + [string]$a.WorkingDirectory)", d1)
        self.assertIn("(Get-TextHash ($actionLines -join \"`n\"))", d1)
        self.assertIn("(Get-TextHash ($triggerLines -join \"`n\"))", d1)
        self.assertIn("Get-TextHash ([string](Export-ScheduledTask -TaskName $t.TaskName -TaskPath $t.TaskPath))", self.b['B1'])

    def test_w0_is_read_only(self):
        w0 = self.b['W0']
        for word in ('Set-Service', 'Stop-Service', 'Start-Service', 'Restart-Service',
                     'Disable-ScheduledTask', 'Enable-ScheduledTask', 'Register-ScheduledTask',
                     'Unregister-ScheduledTask', 'Start-ScheduledTask', 'Stop-ScheduledTask',
                     'Set-ScheduledTask', 'Set-ItemProperty', 'New-ItemProperty', 'Remove-ItemProperty',
                     'Remove-Item', 'Copy-Item', 'Move-Item', 'Rename-Item', 'Set-Content', 'Add-Content',
                     'Out-File', 'Stop-Process', 'Start-Process', 'Invoke-RestMethod', 'Invoke-Expression',
                     'WriteAll', 'ExecQuery', 'https://', 'drone_collector.main', '.RegisterTaskDefinition',
                     '.DeleteTask', '.Run(', '.Stop('):
            self.assertFalse(word in w0, word)
        # The only programs it starts: git, for reading (the subcommands are
        # pinned below), and its own background job.
        self.assertEqual(re.findall(r'(?m)&\s+(?!\{)(\S+)', w0), ['git'])
        self.assertIn("& git -c 'core.fsmonitor=false' -c 'safe.directory=*' -C $dir @gitArgs 2>&1", w0)
        calls = re.findall(r"Invoke-Git \$c @\(([^)]*)\)", w0)
        allowed = {"'rev-parse', 'HEAD'", "'symbolic-ref', '--short', '-q', 'HEAD'", "'tag', '--points-at', 'HEAD'",
                   "'--no-optional-locks', 'status', '--porcelain', '--untracked-files=no'",
                   "'remote', 'get-url', 'origin'", "'cat-file', '-e', ($pin + '^{commit}'",
                   "'diff', '--quiet', '--no-ext-diff', $pin, 'HEAD', '--', 'drone_collector'"}
        self.assertEqual(set(calls), allowed)
        self.assertEqual(w0.count('Start-Job'), 1)
        # Writes: the folder of its own log and the transcript, nothing else.
        self.assertEqual(re.findall(r'New-Item [^|]*', w0), ['New-Item -ItemType Directory -Force -Path $work '])
        self.assertEqual(w0.count('Start-Transcript'), 1)
        # The web: GET of the two login pages, no redirects followed.
        self.assertEqual(w0.count('Invoke-WebRequest'), 1)
        self.assertIn('Invoke-WebRequest -Uri $u[1] -Method Get -UseBasicParsing -TimeoutSec 20 -MaximumRedirection 0', w0)
        self.assertEqual(const(w0, 'stagingLogin'), const(self.b['B1'], 'site') + '/login')
        self.assertEqual(const(w0, 'prodLogin'), 'http://10.103.25.14:5050/login')
        # The DJI session is never opened as text: size, time and sha256 only.
        for m in re.finditer(r'(Get-Content|ReadAllText|ReadAllLines|Read-Text|Select-String)[^\n]*', w0):
            self.assertNotRegex(m.group(0), r'(?i)session|storage_state', m.group(0))
        # Values are printed only for names that cannot hold a secret.
        shown = re.search(r"\$showValue\s*= @\(([^)]*)\)", w0).group(1)
        names = re.findall(r"'([A-Z_]+)'", shown)
        self.assertIn('VEHICLE_SOFT_BASE_URL', names)
        for name in names:
            self.assertNotRegex(name, r'TOKEN|SECRET|PASS|PWD|KEY|COOKIE|AUTH|CRED|PROXY')
        # Task Scheduler of SRV-YOQSH: connect and read, nothing else.
        job = w0[w0.index('Start-Job'):w0.index("Write-Output '== 2.")]
        self.assertEqual(sorted(set(re.findall(r'\$(?:sched|fo)\.(\w+)\(', job))),
                         ['Connect', 'GetFolder', 'GetFolders', 'GetTasks'])
        self.assertIn("'STEP=PASS (read only: nothing operational was changed)'", w0)
        self.assertIn('STEP=STOP - ', w0)

    def test_w0_names_what_the_server_blocks_name(self):
        w0, b1 = self.b['W0'], self.b['B1']
        self.assertEqual(const(w0, 'server'), const(b1, 'expectedHost'))
        self.assertEqual(const(w0, 'serverData'), const(b1, 'prodRoot') + '\\drone_collector\\data')
        self.assertEqual(const(w0, 'pin'), const(b1, 'pin'))
        self.assertEqual(const(w0, 'work'), const(b1, 'work'))
        self.assertTrue(const(w0, 'pilotDir').startswith(const(w0, 'work') + '\\'))


    def test_w1_names_the_frozen_canary_and_the_server(self):
        w1, b1, w0 = self.b['W1'], self.b['B1'], self.b['W0']
        for name in ('expectedHost', 'root', 'db', 'service', 'site', 'prodRoot', 'python', 'pin',
                     'runRoot', 'baseline', 'snapshot', 'canarySha', 'work'):
            with self.subTest(name):
                self.assertIsNotNone(const(w1, name))
                self.assertEqual(const(w1, name), const(b1, name))
        # The frozen canary of B0 (03.10.2026), the production commit and data folder W0 read.
        self.assertEqual(const(w1, 'canarySha'), '5913a88d1bfcecdfe0586fd0a81007ef7cc771d777a2754ebd8b5c1ec2e641da')
        # Production as W1 found it live (08.10.2026); its DJI code is that of the B0 commit.
        self.assertEqual(const(w1, 'prodExpected'), PROD_NOW)
        self.assertEqual(const(w1, 'prodBase'), PROD)
        self.assertIn("$prodDji      = @('drone_collector', 'dji_area', 'drones.py')", w1)
        self.assertEqual(const(w1, 'site'), 'http://10.103.25.14:5051')
        self.assertEqual(const(w1, 'session'), const(w0, 'serverData') + '\\storage_state.json')
        self.assertEqual(const(w1, 'lock'), const(w0, 'serverData') + '\\collector.lock')
        self.assertEqual(const(w1, 'prodLog'), const(w1, 'prodRoot') + '\\drone_collector\\logs\\collector.log')
        self.assertEqual(const(w1, 'prodDb'), const(w1, 'prodRoot') + '\\instance\\transport.db')
        self.assertEqual(const(w1, 'cpy'), const(w1, 'prodRoot') + '\\drone_collector\\.venv\\Scripts\\python.exe')
        self.assertEqual(const(w1, 'src'), const(w1, 'work') + '\\src')
        self.assertEqual(const(w1, 'w1Root'), const(w0, 'pilotDir'))
        self.assertIn("$prodTasks    = @('DroneCollectorDaily', 'DroneAreaDaily', 'DjiAreaRefresh')", w1)
        self.assertIn("$isoTask      = 'DjiAreaRefreshStaging'", w1)

    def test_w1_starts_one_collector_run_of_the_frozen_50(self):
        w1 = self.b['W1']
        # What it does, not what it says it does not do ('--save-session is never passed').
        code = '\n'.join(l for l in w1.splitlines() if not l.lstrip().startswith('Write-Output'))
        # One collection, the normal command, the copy of the frozen file; no session save, no W2.
        self.assertEqual(w1.count('drone_collector.main'), 2)  # the import probe and the run
        self.assertEqual(re.findall(r"Start-Child \$cpy \('-m [^)]*\)", w1),
                         ["Start-Child $cpy ('-m drone_collector.main --sources --ids-file \"' + $idsCopy + '\" --send-sources')"])
        self.assertEqual(w1.count('Start-Child $cpy'), 2)
        self.assertEqual(w1.count('[System.Diagnostics.Process]::Start('), 1)
        for word in ('--save-session', 'pilot_ids', 'Start-Process', 'Invoke-Expression', 'Remove-Item',
                     'Move-Item', 'SetEnvironmentVariable', 'drain', '--routes', '--lands', '--from-date',
                     '--days', 'Register-ScheduledTask', 'Set-ScheduledTask', 'Disable-ScheduledTask',
                     'Enable-ScheduledTask', 'Start-ScheduledTask', 'Stop-ScheduledTask', 'Set-Service',
                     'Restart-Service', 'Set-ItemProperty', 'New-ItemProperty', 'Remove-ItemProperty',
                     'Stop-Process', 'Wait-Process', 'Invoke-RestMethod', 'source_sync -Method'):
            self.assertNotIn(word, code, word)
        self.assertIsNone(re.search(r'\$env:\w+\s*=', w1))
        self.assertEqual(re.findall(r'(?:Stop|Start)-Service -Name (\$\w+)', w1), ['$service'] * 3)
        self.assertEqual(re.findall(r"Copy-Item [^\n]*", w1),
                         ['Copy-Item -LiteralPath $canaryFile -Destination $idsCopy'])
        # The 50 of the frozen file, the pilot checkout, the copy checked against the frozen hash.
        self.assertIn('$ids = Read-Ids $canaryFile', w1)
        self.assertIn('if ((Get-Sha $idsCopy) -ne $canarySha)', w1)
        self.assertIn("if (($ids.Count -ne 50) -or (@($ids | Select-Object -Unique).Count -ne 50))", w1)
        self.assertIn("$flightArgs = @($ids | ForEach-Object { '--flight-id'; [string]$_ })", w1)
        self.assertIn('tools\\dji_area_recalc.py --db $db --from 2026-09-01 --to 2026-09-30 $mode --quiet', w1)
        self.assertEqual(w1.count('dji_area_recalc.py'), 1)

    def test_w1_child_environment(self):
        w1 = self.b['W1']
        m = re.search(r'\$childEnv = \[ordered\]@\{ (.*?) \}\n', w1)
        pairs = dict(p.split(' = ', 1) for p in m.group(1).split('; '))
        self.assertEqual(pairs, {'VEHICLE_SOFT_BASE_URL': '$site', 'DJI_STORAGE_STATE': '$session',
                                 'DJI_COLLECTOR_LOCK_PATH': '$lock', 'DJI_COLLECTOR_LOCK_WAIT_S': "'0'",
                                 'DRONE_OUTBOX_DIR': '$outbox', 'DJI_HEADLESS': "'true'",
                                 'DRONE_API_TOKEN': '$token', 'PYTHONIOENCODING': "'utf-8'"})
        self.assertIn("$childDrop = @('PYTHONPATH', 'PYTHONHOME', 'PYTHONSAFEPATH', 'PYTHONSTARTUP')", w1)
        self.assertIn("$outbox       = Join-Path $w1 'outbox'", w1)
        self.assertIn("$w1           = Join-Path $w1Root $stamp", w1)
        self.assertIn("if (Test-Path -LiteralPath $outbox) { throw", w1)
        # The token: the staging service's own, exactly one entry, never printed, only its name.
        self.assertIn("$tokenLines = @(@((Get-ItemProperty -LiteralPath $siteParams).AppEnvironmentExtra) | Where-Object { [string]$_ -match '^\\s*DRONE_API_TOKEN=' })", w1)
        self.assertIn('if ($tokenLines.Count -ne 1) { throw', w1)
        self.assertNotIn('machineKey', w1)
        for line in w1.splitlines():
            if 'Write-Output' in line:
                self.assertNotRegex(line, r'\$token\b|\$childEnv\[|\$childEnv\.Values|\$body\b')
        # 5050 is refused before anything else is looked at.
        refuse = w1.index("if ($site -match ':5050')")
        self.assertLess(refuse, w1.index("if ($site -notmatch ':5051$')"))
        self.assertLess(refuse, w1.index('Start-Child $cpy'))

    def test_w1_reads_production_only(self):
        w1 = self.b['W1']
        helper = re.search(r"\$helperText = @'\n(.*?)\n'@", w1, re.S).group(1)
        self.assertIn("uri = 'file:%s?mode=ro'", helper)
        self.assertEqual(helper.count('sqlite3.connect('), 1)
        for word in ('INSERT', 'UPDATE', 'DELETE', 'DROP', 'ALTER', 'CREATE', 'commit', 'REPLACE', 'PRAGMA'):
            self.assertNotIn(word, helper, word)
        self.assertEqual(w1.count("Invoke-Helper @('canary', $prodDb"), 2)
        self.assertEqual(len(re.findall(r'\$prodDb\b', w1)), 3)  # the constant and the two read-only checks
        # git on production: read only.
        self.assertEqual(sorted(re.findall(r'git -C \$prodRoot ((?:--no-optional-locks )?\S+)', w1)),
                         ['--no-optional-locks status', 'diff', 'rev-parse'])
        self.assertIn('& git -C $prodRoot diff --quiet $prodBase HEAD -- @prodDji', w1)
        self.assertEqual(w1.count("& $python -I (Join-Path $w1 'w1_check.py')"), 1)
        # The production session: hashed and named, never opened as text.
        for m in re.finditer(r'[^\n]*\$session\b[^\n]*', w1):
            self.assertNotRegex(m.group(0), r'Get-Content|ReadAll|Select-String|Set-Content|Copy-Item')
        # The lock: its owner hint read, nothing else; the production log searched for the run id.
        for m in re.finditer(r'[^\n]*(\$lock\b|\$prodLog\b)[^\n]*', w1):
            self.assertNotRegex(m.group(0), r'Set-Content|Remove|Out-File|WriteAll|Copy-Item|New-Item')

    def test_w1_gates_before_dji_and_before_recalc(self):
        w1 = self.b['W1']
        launch = w1.index("$proc = Start-Child $cpy")
        for gate in ("Test-Production 'BEFORE'", "Test-Collision 'BEFORE'", "Test-Staging 'BEFORE'",
                     "Test-Production 'LAUNCH'", "Test-Collision 'LAUNCH'", 'FINGERPRINT_PRE=equals B0',
                     'REGISTERED=60', 'TOKEN_CHECK=', 'Write-Output ("CANARY_COUNT=" + $ids.Count)'):
            self.assertLess(w1.index(gate), launch, gate)
        # A run that started the collector is never followed by another (exit 24 collected nothing).
        self.assertLess(w1.index("Write-Output 'EARLIER_W1_COLLECTION=none'"), w1.index('Start-Child $cpy'))
        self.assertIn("Set-Content -LiteralPath (Join-Path $w1 'collector_exit.txt') -Value ([string]$code)", w1)
        stop = w1.index("Stop-Service -Name $service -Force")
        for gate in ("if ($stopWhy) { throw", "if (@(0, 18) -notcontains $code)", "if ($statsCode -ne 0)",
                     "COLLECTOR_GATE=PASS", "if ($sessionAfter -ne $sessionBefore)", "if (-not $inPilot -or $inProd)"):
            self.assertLess(w1.index(gate), stop, gate)
        # Lock and window: the owner hint is a stop, the lock itself is not waited for.
        self.assertIn("if (($owner -ne 'none') -and ($owner -notlike 'stale*')) { throw", w1)
        self.assertIn('if ([string]$t[0].State -eq \'Running\') { throw', w1)
        self.assertIn('if (($null -ne $gap) -and ($gap -lt $minGapMin)) { throw', w1)
        # The run is cut off before the next production window can open.
        gap = int(re.search(r'^  \$minGapMin\s*= (\d+)$', w1, re.M).group(1))
        limit = int(re.search(r'^  \$maxCollectMin = (\d+)$', w1, re.M).group(1))
        self.assertEqual((limit, gap), (100, 130))
        self.assertLess(limit, gap)
        # The decision is printed once, from three values, and starts nothing.
        self.assertEqual(w1.count('Write-Output ("DECISION=" + $decision)'), 1)
        self.assertEqual(sorted(set(re.findall(r"\$decision = '(\w+)'", w1))), ['GO_TO_500', 'SIMPLIFY', 'STOP'])
        after = w1[w1.index("Write-Output '== 5. Canary result'"):]
        self.assertNotIn('Start-Child', after)
        self.assertNotIn('& $python', after)


MAIN = 'c34ea9aade48768cd6c0daaad9458972e869d335'
BRANCH_HEAD = '8706dab'
PIN = '39eab503069b7bb01a8342542edcbebbfc2210c2'
PROD = '8df568394a840054ef6f842c6a8b272ca4c31aa8'
STAGING_HEAD = '2013bed88c19b6383097d0c0b9b65442c85c26ce'
LOGIN = '<form method="post" class="vs-login-form">'
BRANCH = 'claude/practical-davinci-chb4r7'


def sh(*args, cwd=None):
    return subprocess.run(list(args), cwd=cwd, check=True, capture_output=True,
                          text=True).stdout.strip()


def sha(path):
    h = hashlib.sha256()
    with open(path, 'rb') as fh:
        h.update(fh.read())
    return h.hexdigest()


class Bed(object):
    """One disposable server: origin, staging, production, B0 run, plan."""

    def __init__(self, root, staging_row='occupied'):
        self.root = root
        os.makedirs(root, exist_ok=True)
        self.origin = os.path.join(root, 'origin.git')
        sh('git', 'clone', '-q', '--bare', '--shared', REPO_ROOT, self.origin)
        main = MAIN
        if staging_row != 'occupied':
            work = os.path.join(root, 'w')
            sh('git', 'clone', '-q', '--shared', self.origin, work)
            sh('git', 'checkout', '-q', MAIN, cwd=work)
            path = os.path.join(work, 'docs', 'STAGING.md')
            text = open(path, encoding='utf-8').read()
            row = [l for l in text.splitlines() if l.startswith('| \u0434\u0430 |')][0]
            if staging_row == 'phrase_missing':
                text = text.replace('DRONE-CARD-COVERAGE-001 / historical pilot:',
                                    'something else:')
            elif staging_row == 'released':
                text = text.replace(row, row.replace('| \u0434\u0430 |', '| \u043d\u0435\u0442 |', 1))
            elif staging_row == 'two_rows':
                text = text.replace(row, row + '\n' + row.replace('DRONE-CARD', 'OTHER-TRACK'))
            open(path, 'w', encoding='utf-8').write(text)
            sh('git', '-c', 'user.name=t', '-c', 'user.email=t@t', 'commit',
               '-q', '-am', 'free', cwd=work)
            main = sh('git', 'rev-parse', 'HEAD', cwd=work)
            sh('git', 'push', '-q', self.origin, 'HEAD:refs/heads/tmp', cwd=work)
        sh('git', '--git-dir', self.origin, 'update-ref', 'refs/heads/main', main)
        sh('git', '--git-dir', self.origin, 'update-ref',
           'refs/heads/' + BRANCH, sh('git', 'rev-parse', BRANCH_HEAD, cwd=REPO_ROOT))
        self.staging = os.path.join(root, 'transport-report-staging')
        sh('git', 'clone', '-q', '--shared', self.origin, self.staging)
        sh('git', '-c', 'advice.detachedHead=false', 'checkout', '-q', STAGING_HEAD,
           cwd=self.staging)
        # The blocks call tools\\x.py from the staging folder. Where '\\' is
        # not a path separator (the Linux stand-in) that is a file name of its
        # own; on Windows the files come from the checkout itself.
        if os.sep != '\\':
            for rel in ('tools/check_db_lock.py', 'tools/check_migration_drift.py',
                        'tools/dji_card_coverage_pilot.py'):
                blob = subprocess.run(['git', 'show', PIN + ':' + rel], cwd=REPO_ROOT,
                                      check=True, capture_output=True).stdout
                with open(os.path.join(self.staging, rel.replace('/', '\\')), 'wb') as fh:
                    fh.write(blob)
        os.makedirs(os.path.join(self.staging, 'instance'), exist_ok=True)
        self.db = os.path.join(self.staging, 'instance', 'transport.db')
        build_db(self.db, no_card=20)
        self.prod = os.path.join(root, 'prod')
        sh('git', 'clone', '-q', '--shared', REPO_ROOT, self.prod)
        sh('git', '-c', 'advice.detachedHead=false', 'checkout', '-q', PROD, cwd=self.prod)
        self.run_root = os.path.join(root, 'card_pilot')
        self.baseline = os.path.join(self.run_root, 'baseline_20261003_072956')
        os.makedirs(os.path.join(self.baseline, 'snapshot'))
        self.snapshot = os.path.join(self.baseline, 'snapshot',
                                     'transport_20261003_073000_card_pilot_baseline.db')
        build_db(self.snapshot, no_card=60)
        con = sqlite3.connect(self.snapshot)
        con.executemany('INSERT INTO schema_migrations (name, applied_at) VALUES (?, ?)',
                        [('SYNTH_%02d' % i, '2026-09-01') for i in range(59)])
        con.commit()
        con.close()
        open(self.snapshot + '-wal', 'w').close()
        self.plan_dir = os.path.join(self.baseline, 'plan')
        tool = os.path.join(REPO_ROOT, 'tools', 'dji_card_coverage_pilot.py')
        sh(sys.executable, tool, 'plan', '--db', self.snapshot, '--out-dir', self.plan_dir)
        sh(sys.executable, tool, 'fingerprint', '--db', self.snapshot, '--out',
           os.path.join(self.plan_dir, 'fingerprint_before.json'))
        self.plan = json.load(open(os.path.join(self.plan_dir, 'plan.json')))
        self.work = os.path.join(root, 'work')
        os.makedirs(os.path.join(root, 'ProgramData'))
        self.wrapper = os.path.join(root, 'ProgramData', 'DjiAreaRefreshStaging.ps1')
        with open(self.wrapper, 'wb') as fh:
            fh.write(ISO_WRAPPER_TEXT.encode('ascii'))

    def restore_block(self, **override):
        text = blocks()['R']
        values = {'root': self.staging, 'prodRoot': self.prod, 'python': sys.executable,
                  'runRoot': self.run_root, 'db': self.db, 'site': 'http://staging.invalid',
                  'work': self.work}
        values.update(override)
        for name, value in values.items():
            pat = re.compile(r"^(  \$%s\s*= )'[^']*'$" % name, re.M)
            assert len(pat.findall(text)) == 1, name
            text = pat.sub(lambda m: m.group(1) + "'" + value + "'", text)
        return text

    def block(self, **override):
        text = blocks()['B1']
        values = {
            'root': self.staging, 'prodRoot': self.prod, 'python': sys.executable,
            'runRoot': self.run_root, 'baseline': self.baseline,
            'snapshot': self.snapshot, 'db': self.db,
            'manifestSha': self.plan['sample']['manifest_sha256'],
            'canarySha': self.plan['sample']['canary_ids_sha256'],
            'site': 'http://staging.invalid', 'work': self.work,
            'isoWrapper': self.wrapper, 'isoWrapperSha': sha(self.wrapper),
        }
        values.update(iso_fingerprints(iso_task()))
        values.update(override)
        for name, value in values.items():
            pat = re.compile(r"^(  \$%s\s*= )'[^']*'$" % name, re.M)
            assert len(pat.findall(text)) == 1, name
            text = pat.sub(lambda m: m.group(1) + "'" + value + "'", text)
        size = os.path.getsize(self.snapshot)
        text, n = re.subn(r'^(  \$snapBytes\s*= )\d+$', r'\g<1>%d' % size, text, flags=re.M)
        assert n == 1
        # The plan here is 60 flights; the frozen real plan is 500 / 50.
        text = text.replace("([int]$plan.sample.size -ne 500) -or ([int]$plan.sample.canary -ne 50)",
                            "([int]$plan.sample.size -ne %d) -or ([int]$plan.sample.canary -ne %d)"
                            % (self.plan['sample']['size'], self.plan['sample']['canary']))
        return text


SITE_KEY = 'HKLM:\\SYSTEM\\CurrentControlSet\\Services\\TransportReportStaging\\Parameters'
SITE_SVC_KEY = 'HKLM:\\SYSTEM\\CurrentControlSet\\Services\\TransportReportStaging'
MACHINE_KEY = 'HKLM:\\SYSTEM\\CurrentControlSet\\Control\\Session Manager\\Environment'
SECRET = 'SECRET_KEY=do-not-print-0f9e'
ENV_EXTRA = ['FLASK_ENV=sqlite_prod', 'PORT=5051', SECRET, 'DJI_REFRESH_LAUNCHER=subprocess',
             'DRONE_API_TOKEN=do-not-print-77aa']
BOTS = ('TransportBotStaging', 'TransportBot003Staging')
PROD_SERVICES = ('TransportReport', 'TransportBot', 'TransportBot003')
# The staging refresh task as D1 found it live (03.10.2026); stand-in values.
ISO = 'DjiAreaRefreshStaging'
ISO_ARGS = ('-NoProfile -NonInteractive -ExecutionPolicy Bypass -File '
            '"C:\\ProgramData\\VehicleSoft\\DjiAreaRefreshStaging.ps1"')
ISO_TRIGGERS = [{'Class': 'MSFT_TaskDailyTrigger',
                 # Not an ISO date: pwsh 7 would turn it into a DateTime when it reads the JSON.
                 'Props': {'DaysInterval': '1', 'Enabled': 'True', 'StartBoundary': 'daily-0300',
                           'Repetition': {'StopAtDurationEnd': 'False'}}}]
ISO_XML = '<Task version="1.4"><Principal>S4U Highest</Principal><Actions>powershell.exe</Actions>'
ISO_WRAPPER_TEXT = ('& C:\\transport-report\\drone_collector\\.venv\\Scripts\\python.exe '
                    'C:\\transport-report-staging\\tools\\dji_area_daily.py --run-queued\n')


def text_hash(text):
    return hashlib.sha256(text.encode('utf-8')).hexdigest()


def cim_line(trigger):
    """Python twin of Get-CimLine for the stand-in triggers."""
    parts = []
    props = trigger['Props']
    for name in sorted(props, key=str.lower):
        value = props[name]
        if isinstance(value, dict):
            parts.extend('%s.%s=%s' % (name, k, value[k]) for k in sorted(value, key=str.lower) if value[k] != '')
        elif value != '':
            parts.append('%s=%s' % (name, value))
    return trigger['Class'] + ' ' + ' '.join(parts)


def iso_task(**changes):
    task = {'TaskName': ISO, 'TaskPath': '\\', 'State': 'Ready', 'Enabled': True,
            'Execute': 'powershell.exe', 'Arguments': ISO_ARGS, 'WorkingDirectory': '',
            'Triggers': ISO_TRIGGERS, 'Xml': ISO_XML}
    task.update(changes)
    return task


def iso_fingerprints(task):
    return {'isoActionFp': text_hash('%s|%s|%s' % (task['Execute'], task['Arguments'], task['WorkingDirectory'])),
            'isoTriggerFp': text_hash('\n'.join(cim_line(t) for t in task['Triggers'])),
            'isoXmlSha': text_hash(task['Xml'] + '<Enabled>%s</Enabled>' % str(task['Enabled']).lower()),
            'isoEnabled': str(task['Enabled'])}


def tasks_plus(*extra):
    return scenario()['Tasks'] + list(extra)


def scenario(**changes):
    value = {
        'Host': 'SRV-YOQSH',
        'Services': {
            'TransportReport': {'Status': 'Running', 'StartType': 'Automatic'},
            'TransportBot': {'Status': 'Running', 'StartType': 'Automatic'},
            'TransportBot003': {'Status': 'Running', 'StartType': 'Automatic'},
            'TransportReportStaging': {'Status': 'Running', 'StartType': 'Automatic'},
            'TransportBotStaging': {'Status': 'Running', 'StartType': 'Automatic'},
            'TransportBot003Staging': {'Status': 'Running', 'StartType': 'Automatic'},
        },
        'StopFails': [],
        'Tasks': [
            iso_task(),
            {'TaskName': 'TransportDBBackupStaging', 'State': 'Ready',
             'Execute': 'C:\\Program Files\\Python314\\python.exe',
             'Arguments': 'C:\\transport-report-staging\\backup_transport_db.py --source C:\\transport-report-staging\\instance\\transport.db'},
            {'TaskName': 'DroneCollectorDaily', 'State': 'Ready',
             'Execute': 'C:\\transport-report\\drone_daily.bat', 'Arguments': ''},
            {'TaskName': 'OldHoldout', 'State': 'Disabled',
             'Execute': 'C:\\VehicleSoft_Holdout_Staging\\run.bat', 'Arguments': ''},
            {'TaskName': 'GoogleUpdate', 'State': 'Ready',
             'Execute': 'C:\\Program Files\\Google\\Update\\GoogleUpdate.exe', 'Arguments': '/c'},
            {'TaskName': 'Defrag', 'TaskPath': '\\Microsoft\\Windows\\Defrag\\', 'State': 'Ready',
             'Execute': 'C:\\transport-report\\x.exe', 'Arguments': ''},
        ],
        'Registry': {SITE_KEY: {'AppEnvironmentExtra': list(ENV_EXTRA)},
                     MACHINE_KEY: {'Path': 'C:\\Windows'}},
        'Processes': [],
        'Web': {'/login': {'Status': 200, 'Body': LOGIN},
                '/drones/fields': {'Status': 200, 'Body': LOGIN}},
    }
    value.update(changes)
    return value


@unittest.skipUnless(POWERSHELL, 'CARD_PILOT_POWERSHELL is not set')
class BlocksInPowerShell(unittest.TestCase):
    """B1 и R исполняются против подставного сервера (см. докстринг модуля)."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.tmp, True)

    def run_text(self, text, sc):
        bf = os.path.join(self.tmp, 'block.ps1')
        sf = os.path.join(self.tmp, 'scenario.json')
        cf = os.path.join(self.tmp, 'calls.txt')
        open(bf, 'w', encoding='utf-8').write(text)
        json.dump(sc, open(sf, 'w'))
        p = subprocess.run([POWERSHELL, '-NoProfile', '-NonInteractive', '-ExecutionPolicy',
                            'Bypass', '-File', HARNESS, '-BlockFile', bf,
                            '-ScenarioFile', sf, '-CallsFile', cf],
                           capture_output=True, text=True, timeout=900)
        out = p.stdout + p.stderr
        calls = [l for l in open(cf).read().splitlines() if l]
        services = json.load(open(cf + '.services.json'))
        registry = json.load(open(cf + '.registry.json'))
        tasks = json.load(open(cf + '.tasks.json'))
        self.last_tasks = tasks if isinstance(tasks, list) else [tasks]
        return out, calls, services, registry

    def run_block(self, bed, sc, **override):
        return self.run_text(bed.block(**override), sc)

    def run_restore(self, bed, sc, **override):
        return self.run_text(bed.restore_block(**override), sc)

    def carry(self, sc, services, registry):
        """The next block sees the services, registry and tasks the previous one left."""
        nxt = dict(sc)
        nxt['Services'] = services
        nxt['Registry'] = registry
        left = {(t['TaskName'], t['TaskPath']): t for t in self.last_tasks}
        tasks = []
        for t in sc['Tasks']:
            t = dict(t)
            seen = left.get((t['TaskName'], t.get('TaskPath', '\\')))
            if seen:
                t['Enabled'], t['State'] = seen['Enabled'], seen['State']
            t.pop('RunningFromRead', None)
            tasks.append(t)
        nxt['Tasks'] = tasks
        nxt.pop('HoldDb', None)
        return nxt

    def iso_now(self):
        hit = [t for t in self.last_tasks if t['TaskName'] == ISO]
        self.assertEqual(len(hit), 1)
        return hit[0]['Enabled'], hit[0]['State']

    def task_calls(self, calls):
        return [c for c in calls if 'ScheduledTask' in c]

    def staging_head(self, bed):
        return sh('git', 'rev-parse', 'HEAD', cwd=bed.staging)

    def runs(self, bed):
        if not os.path.isdir(bed.run_root):
            return []
        return sorted(os.path.join(bed.run_root, d) for d in os.listdir(bed.run_root)
                      if d.startswith('staging_'))

    def assertUntouched(self, bed, calls, db_sha_before, out):
        for c in calls:
            self.assertFalse(c.startswith(('Set-Service', 'Stop-Service', 'Start-Service',
                                           'Restart-Service', 'Set-ItemProperty')), c)
        self.assertEqual(self.task_calls(calls), [])
        self.assertEqual(self.staging_head(bed), STAGING_HEAD)
        self.assertEqual(sha(bed.db), db_sha_before)
        self.assertIn('STAGING_CHANGED=no', out)
        self.assertNotIn('STEP=PASS', out)
        for run in self.runs(bed):
            self.assertTrue(os.path.exists(os.path.join(run, 'returned.txt')), run)

    def assertNoSecrets(self, out, bed):
        self.assertNotIn('do-not-print', out)
        for name in os.listdir(bed.work):
            self.assertNotIn('do-not-print', open(os.path.join(bed.work, name), errors='replace').read())

    def assertPass(self, bed, out, calls, svc, reg, staging_db_sha):
        self.assertIn('STEP=PASS', out, out)
        self.assertNotIn('BLOCK THREW', out)
        self.assertNotIn('STEP=STOP', out)
        self.assertEqual(self.staging_head(bed), PIN)
        self.assertEqual(sha(bed.db), sha(bed.snapshot))
        for name in BOTS:
            self.assertEqual(svc[name], {'Status': 'Stopped', 'StartType': 'Disabled'})
        self.assertEqual(svc['TransportReportStaging'], {'Status': 'Running', 'StartType': 'Automatic'})
        for name in PROD_SERVICES:
            self.assertEqual(svc[name], {'Status': 'Running', 'StartType': 'Automatic'})
        self.assertEqual([c for c in calls if re.search(r' Transport(Report|Bot|Bot003)$', c)], [])
        order = [c for c in calls if not c.startswith('GET')]
        self.assertEqual(order, [
            'Disable-ScheduledTask \\' + ISO,
            'Set-Service TransportBotStaging Disabled', 'Stop-Service TransportBotStaging',
            'Set-Service TransportBot003Staging Disabled', 'Stop-Service TransportBot003Staging',
            'Stop-Service TransportReportStaging',
            'Set-ItemProperty %s AppEnvironmentExtra MultiString' % SITE_KEY,
            'Start-Service TransportReportStaging'])
        # The refresh task is disabled for the pilot; no other task is touched
        # (the call list above has exactly one task call).
        self.assertEqual(self.iso_now(), (False, 'Disabled'))
        # Every other entry of the site environment kept, in order; only the launcher is gone.
        self.assertEqual(reg[SITE_KEY]['AppEnvironmentExtra'],
                         [e for e in ENV_EXTRA if not e.startswith('DJI_REFRESH_LAUNCHER=')])
        runs = self.runs(bed)
        self.assertEqual(len(runs), 1)
        run = runs[0]
        self.assertFalse(os.path.exists(os.path.join(run, 'returned.txt')))
        self.assertEqual(open(os.path.join(run, 'before_head.txt')).read().strip(), STAGING_HEAD)
        self.assertEqual(open(os.path.join(run, 'before_ref.txt')).read().strip(), '')
        self.assertEqual(sorted(open(os.path.join(run, 'services_before.txt')).read().split()), sorted([
            'TransportReportStaging|Running|Automatic|', 'TransportBotStaging|Running|Automatic|',
            'TransportBot003Staging|Running|Automatic|']))
        self.assertEqual(open(os.path.join(run, 'refresh_launcher.txt')).read().split(),
                         ['3|DJI_REFRESH_LAUNCHER=subprocess'])
        env_before = open(os.path.join(run, 'env_extra_before.txt')).read().split()
        self.assertEqual(env_before[0], str(len(ENV_EXTRA)))
        self.assertEqual(env_before[1], hashlib.sha256('\n'.join(ENV_EXTRA).encode('utf-8')).hexdigest())
        self.assertTrue(os.path.exists(os.path.join(run, 'placed.txt')))
        task_before = dict(l.split('=', 1) for l in open(os.path.join(run, 'task_before.txt')).read().split())
        self.assertEqual(task_before, dict(
            name=ISO, path='\\', enabled='True', state='Ready', wrapper=bed.wrapper,
            wrapper_sha=sha(bed.wrapper), **{k: v for k, v in zip(
                ('action', 'trigger', 'xml'),
                [iso_fingerprints(iso_task())[x] for x in ('isoActionFp', 'isoTriggerFp', 'isoXmlSha')])}))
        for name in ('swapped.txt', 'bots_disabled.txt', 'fingerprint_placed.json', 'task_isolation.txt', 'task_disabled.txt',
                     'fingerprint_started.json', 'drift.log', 'drift_after_start.log'):
            self.assertTrue(os.path.exists(os.path.join(run, name)), name)
        rec = [l.split('|') for l in open(os.path.join(run, 'staging_backup.txt')).read().splitlines() if l]
        self.assertEqual([r[0] for r in rec], ['staging_before', 'staging_final'])
        for label, path, size, digest in rec:
            self.assertEqual(os.path.getsize(path), int(size))
            self.assertEqual(sha(path), digest)
            con = sqlite3.connect(path)
            self.assertEqual(con.execute('PRAGMA integrity_check').fetchone()[0], 'ok')
            con.close()
        # The final backup holds the database that was replaced (nothing wrote between).
        con = sqlite3.connect(rec[1][1])
        n_backup = con.execute('SELECT COUNT(*) FROM drone_flights').fetchone()[0]
        con.close()
        self.assertEqual(n_backup, staging_db_sha[1])
        for key in ('B0_SNAPSHOT=', 'PIN_VS_PRODUCTION=8 file(s)', 'DB_LOCK_AFTER_STOP=0', 'STAGING_BACKUP staging_before',
                    'STAGING_BACKUP staging_final', 'DB_LOCK_EXIT=0', 'PLACED=',
                    'FINGERPRINT_PLACED=equals', 'FINGERPRINT_STARTED=equals',
                    'REGISTERED=60', 'REGISTERED_AFTER_START=60', 'SMOKE_LOGIN=200',
                    'FIELDS_ANONYMOUS=', 'PROD_HEAD_AFTER=' + PROD,
                    'PROD_SERVICES_AFTER=TransportBot=Running TransportBot003=Running TransportReport=Running',
                    'STAGING_ROW=occupied', 'DJI_REFRESH_LAUNCHER_BEFORE=subprocess',
                    'DJI_REFRESH_LAUNCHER_NOW=absent', 'TASK TransportDBBackupStaging Ready staging',
                    'TASK DroneCollectorDaily Ready production', 'TASK GoogleUpdate Ready other',
                    'TASKS_CHECKED=4 enabled', 'TASK %s Ready KNOWN_AND_DISABLED_FOR_PILOT' % ISO,
                    'ISOLATE_TASK_VERIFIED=', 'ISOLATE_TASK_NOW=Disabled (was enabled=True',
                    'BARRIERS=%s Disabled, DJI_REFRESH_LAUNCHER absent' % ISO,
                    'BOT_FINAL TransportBotStaging Stopped Disabled',
                    'SITE_ENV_NAMES=FLASK_ENV,PORT,SECRET_KEY,DJI_REFRESH_LAUNCHER,DRONE_API_TOKEN (names only)'):
            self.assertIn(key, out)
        self.assertNotIn('OldHoldout', out)
        self.assertNotIn('Defrag', out)
        self.assertNotIn('REFUSED', out)
        self.assertNoSecrets(out, bed)
        return run

    def db_rows(self, path):
        con = sqlite3.connect(path)
        try:
            return con.execute('SELECT COUNT(*) FROM drone_flights').fetchone()[0]
        finally:
            con.close()

    def test_pass_then_restore_then_again(self):
        bed = Bed(os.path.join(self.tmp, 'bed'))
        staging_sha = sha(bed.db)
        staging_rows = self.db_rows(bed.db)
        # The site writes once more while it stops: only the second backup has it.
        sc = scenario(WriteDb=bed.db, Python=sys.executable,
                      WriterScript=WRITER)
        out, calls, svc, reg = self.run_block(bed, sc)
        run = self.assertPass(bed, out, calls, svc, reg, (staging_sha, staging_rows))
        rec = [l.split('|') for l in open(os.path.join(run, 'staging_backup.txt')).read().splitlines() if l]
        self.assertNotEqual(rec[0][3], rec[1][3])
        sc = scenario()
        # The pilot writes to the staging database meanwhile.
        con = sqlite3.connect(bed.db)
        con.execute('UPDATE drone_flights SET area_ha = area_ha + 1')
        con.commit()
        con.close()
        pilot_sha = sha(bed.db)
        out, calls, svc, reg = self.run_restore(bed, self.carry(sc, svc, reg))
        self.assertIn('STEP=PASS', out, out)
        self.assertEqual(self.staging_head(bed), STAGING_HEAD)
        self.assertEqual(self.db_rows(bed.db), staging_rows)
        final = [l.split('|') for l in open(os.path.join(run, 'staging_backup.txt')).read().splitlines() if l][-1]
        self.assertEqual(sha(bed.db), final[3])
        for name in ('TransportReportStaging',) + BOTS + PROD_SERVICES:
            self.assertEqual(svc[name], {'Status': 'Running', 'StartType': 'Automatic'}, name)
        self.assertEqual(reg[SITE_KEY]['AppEnvironmentExtra'], ENV_EXTRA)
        self.assertIn('the whole environment list equals the one before B1', out)
        folders = [d for d in os.listdir(run) if d.startswith('pilot_final_')]
        self.assertEqual(len(folders), 1)
        kept = [f for f in os.listdir(os.path.join(run, folders[0])) if f.endswith('.db')]
        self.assertEqual(len(kept), 1)
        con = sqlite3.connect(os.path.join(run, folders[0], kept[0]))
        self.assertEqual(con.execute('PRAGMA integrity_check').fetchone()[0], 'ok')
        con.close()
        self.assertNotEqual(pilot_sha, final[3])
        self.assertTrue(os.path.exists(os.path.join(run, 'returned.txt')))
        order = [c for c in calls if not c.startswith('GET')]
        self.assertEqual(order, [
            'Stop-Service TransportReportStaging',
            'Set-ItemProperty %s AppEnvironmentExtra MultiString' % SITE_KEY,
            'Set-Service TransportReportStaging Automatic', 'Start-Service TransportReportStaging',
            'Set-Service TransportBotStaging Automatic', 'Start-Service TransportBotStaging',
            'Set-Service TransportBot003Staging Automatic', 'Start-Service TransportBot003Staging',
            'Enable-ScheduledTask \\' + ISO])
        self.assertEqual(self.iso_now(), (True, 'Ready'))
        self.assertIn('TASK_RESTORED %s enabled=True' % ISO, out)
        self.assertIn('STEP=PASS\n', out)
        for key in ('RESTORE_FROM staging_final', 'PILOT_DB_KEPT=', 'DB_RESTORED=',
                    'HEAD_RESTORED=' + STAGING_HEAD, 'DJI_REFRESH_LAUNCHER=restored', 'RETURNED='):
            self.assertIn(key, out)
        self.assertNoSecrets(out, bed)
        # R again: nothing open, refuses without any change.
        out2, calls2, _, _ = self.run_restore(bed, self.carry(sc, svc, reg))
        self.assertIn('0 open staging runs', out2)
        self.assertIn('STAGING_CHANGED=no', out2)
        self.assertEqual(calls2, [])
        # B1 can run again after the return.
        out3, calls3, svc3, reg3 = self.run_block(bed, self.carry(sc, svc, reg))
        self.assertIn('STEP=PASS', out3, out3)
        self.assertEqual(len(self.runs(bed)), 2)

    def test_refusals_before_any_change(self):
        bed = Bed(os.path.join(self.tmp, 'bed'))
        before = sha(bed.db)
        prod_down = dict(scenario()['Services'], TransportBot003={'Status': 'Stopped', 'StartType': 'Automatic'})
        prod_missing = {k: v for k, v in scenario()['Services'].items() if k != 'TransportBot'}
        cases = [
            ('host is', dict(sc=scenario(Host='OTHER'))),
            ('plan.json names manifest', dict(override=dict(manifestSha='0' * 64))),
            ('plan.json names canary', dict(override=dict(canarySha='0' * 64))),
            ('may write to staging: StagingDroneDaily', dict(sc=scenario(Tasks=tasks_plus(
                {'TaskName': 'StagingDroneDaily', 'State': 'Ready',
                 'Execute': 'C:\\transport-report-staging\\x.bat', 'Arguments': ''})))),
            ('may write to staging: HoldoutCollector', dict(sc=scenario(Tasks=tasks_plus(
                {'TaskName': 'HoldoutCollector', 'State': 'Ready',
                 'Execute': 'C:\\VehicleSoft_Holdout_Staging\\venv\\python.exe',
                 'Arguments': '-m drone_collector.main --sources'})))),
            ('may write to staging: HoldoutSources', dict(sc=scenario(Tasks=tasks_plus(
                {'TaskName': 'HoldoutSources', 'State': 'Ready',
                 'Execute': 'C:\\transport-report\\drone_collector\\.venv\\Scripts\\python.exe',
                 'Arguments': '-m drone_collector.main --sources --send-sources',
                 'WorkingDirectory': 'C:\\VehicleSoft_Holdout_Staging'})))),
            ('may write to staging: TransportDBBackupStaging', dict(sc=scenario(Tasks=[iso_task(),
                {'TaskName': 'TransportDBBackupStaging', 'State': 'Ready',
                 'Execute': 'C:\\Users\\x\\evil.bat', 'Arguments': ''}]))),
            # The staging refresh task: anything but the exact D1 picture stops B1.
            ('is running now', dict(sc=scenario(Tasks=[iso_task(State='Running')]))),
            ('process(es) of the staging refresh cycle are running', dict(sc=scenario(Processes=[
                'python.exe C:\\transport-report-staging\\tools\\dji_area_daily.py --run-queued']))),
            ('the action fingerprint of ' + ISO, dict(sc=scenario(Tasks=[iso_task(Arguments=ISO_ARGS + ' -X')]))),
            ('the trigger fingerprint of ' + ISO, dict(sc=scenario(Tasks=[iso_task(Triggers=[dict(
                ISO_TRIGGERS[0], Props=dict(ISO_TRIGGERS[0]['Props'], StartBoundary='daily-0400'))])]))),
            ('the task XML fingerprint of ' + ISO, dict(sc=scenario(Tasks=[iso_task(Xml=ISO_XML + '<x/>')]))),
            ('the wrapper fingerprint of ' + ISO, dict(override=dict(isoWrapperSha='1' * 64))),
            ('the wrapper fingerprint of %s is missing' % ISO, dict(override=dict(isoWrapper='C:\\no\\such.ps1'))),
            ('2 scheduled tasks are named ' + ISO, dict(sc=scenario(Tasks=[iso_task(), iso_task(TaskPath='\\Other\\')]))),
            ('is in folder \\VehicleSoft\\', dict(sc=scenario(Tasks=[iso_task(TaskPath='\\VehicleSoft\\')]))),
            ('Enabled=False, D1 saw True', dict(sc=scenario(Tasks=[iso_task(Enabled=False, State='Disabled')]))),
            ('start by themselves', dict(sc=scenario(Services=dict(
                scenario()['Services'], TransportGpsStaging={'Status': 'Stopped', 'StartType': 'Automatic'})))),
            ('must be Running or Stopped', dict(sc=scenario(Services=dict(
                scenario()['Services'], TransportBotStaging={'Status': 'Paused', 'StartType': 'Automatic'})))),
            ('service Environment value of the staging site sets DJI_REFRESH_LAUNCHER', dict(sc=scenario(Registry={
                SITE_KEY: {'AppEnvironmentExtra': list(ENV_EXTRA)},
                SITE_SVC_KEY: {'Environment': ['DJI_REFRESH_LAUNCHER=subprocess']}}))),
            ('other staging services are running', dict(sc=scenario(Services=dict(
                scenario()['Services'], TransportGpsStaging={'Status': 'Running', 'StartType': 'Automatic'})))),
            ('service not found', dict(sc=scenario(Services={k: v for k, v in scenario()['Services'].items()
                                                              if k != 'TransportBot003Staging'}))),
            ('not all three production services are Running', dict(sc=scenario(Services=prod_down))),
            ('not all three production services are Running', dict(sc=scenario(Services=prod_missing))),
            ('machine environment sets DJI_REFRESH_LAUNCHER', dict(sc=scenario(Registry={
                SITE_KEY: {'AppEnvironmentExtra': list(ENV_EXTRA)},
                MACHINE_KEY: {'DJI_REFRESH_LAUNCHER': 'schtasks'}}))),
            ('AppEnvironment of the staging site sets DJI_REFRESH_LAUNCHER', dict(sc=scenario(Registry={
                SITE_KEY: {'AppEnvironmentExtra': ['PORT=5051'],
                           'AppEnvironment': ['PORT=5051', 'DJI_REFRESH_LAUNCHER=subprocess']}}))),
            ('(NSSM AppEnvironmentExtra) could not be read', dict(sc=scenario(Registry={
                MACHINE_KEY: {'Path': 'C:\\Windows'}}))),
        ]
        for needle, kw in cases:
            with self.subTest(needle):
                out, calls, svc, reg = self.run_block(bed, kw.get('sc', scenario()),
                                                      **kw.get('override', {}))
                self.assertIn('STEP=STOP - STEP FAILED', out, out)
                self.assertIn(needle, out)
                self.assertUntouched(bed, calls, before, out)
                self.assertEqual(self.runs(bed), [])
                self.assertNoSecrets(out, bed)

    def test_staging_row_must_be_one_occupied_row(self):
        for variant, needle in (('phrase_missing', 'does not show staging occupied'),
                                ('released', 'does not show staging occupied'),
                                ('two_rows', 'has 2 rows in the table')):
            with self.subTest(variant):
                bed = Bed(os.path.join(self.tmp, 'bed_' + variant), staging_row=variant)
                before = sha(bed.db)
                out, calls, _, _ = self.run_block(bed, scenario())
                self.assertIn(needle, out, out)
                self.assertUntouched(bed, calls, before, out)

    def test_refusals_on_files_and_git(self):
        bed = Bed(os.path.join(self.tmp, 'bed'))
        before = sha(bed.db)
        # 1. snapshot is not what the plan was made on
        # Binary reads and writes: text mode on Windows would turn the
        # restored files into CRLF and change their sha256.
        plan_path = os.path.join(bed.plan_dir, 'plan.json')
        original = open(plan_path, 'rb').read()
        doc = json.loads(original.decode('ascii'))
        doc['database']['sha256'] = 'f' * 64
        open(plan_path, 'wb').write(json.dumps(doc).encode('ascii'))
        out, calls, _, _ = self.run_block(bed, scenario())
        self.assertIn('the frozen plan was made on', out)
        self.assertUntouched(bed, calls, before, out)
        open(plan_path, 'wb').write(original)
        # 2. the frozen canary file was edited
        canary = os.path.join(bed.plan_dir, 'canary_ids.txt')
        saved = open(canary, 'rb').read()
        open(canary, 'ab').write(b'123\n')
        out, calls, _, _ = self.run_block(bed, scenario())
        self.assertIn('canary_ids.txt has sha256', out)
        self.assertUntouched(bed, calls, before, out)
        open(canary, 'wb').write(saved)
        # 3. an open staging run
        os.makedirs(os.path.join(bed.run_root, 'staging_20261003_000000'))
        out, calls, _, _ = self.run_block(bed, scenario())
        self.assertIn('is still open', out)
        self.assertEqual(calls, [])
        shutil.rmtree(os.path.join(bed.run_root, 'staging_20261003_000000'))
        # 4. edited tracked file in the staging checkout
        with open(os.path.join(bed.staging, 'app.py'), 'a') as fh:
            fh.write('# edit\n')
        out, calls, _, _ = self.run_block(bed, scenario())
        self.assertIn('tracked file(s) changed', out)
        self.assertEqual(calls, [])
        sh('git', 'checkout', '-q', '--', 'app.py', cwd=bed.staging)
        # 5. production not at the expected commit
        sh('git', '-c', 'advice.detachedHead=false', 'checkout', '-q', 'HEAD~1', cwd=bed.prod)
        out, calls, _, _ = self.run_block(bed, scenario())
        self.assertIn('production is at', out)
        self.assertUntouched(bed, calls, before, out)
        # 6. positive control of the pin comparison: an empty delta is refused
        sh('git', '-c', 'advice.detachedHead=false', 'checkout', '-q', PIN, cwd=bed.prod)
        out, calls, _, _ = self.run_block(bed, scenario(), prodExpected=PIN)
        self.assertIn('does not show the pilot tool', out)
        self.assertUntouched(bed, calls, before, out)
        # 7. a production commit the staging clone does not have
        out, calls, _, _ = self.run_block(bed, scenario(), prodExpected='1' * 40)
        self.assertIn('production is at', out)
        self.assertUntouched(bed, calls, before, out)

    def test_failure_before_first_change_closes_its_run(self):
        # The task starts running between the checks and the isolation: B1
        # stops before its first change, closes its own run, and can be run
        # again at once.
        bed = Bed(os.path.join(self.tmp, 'bed'))
        before = sha(bed.db)
        out, calls, _, _ = self.run_block(bed, scenario(Tasks=[iso_task(RunningFromRead=2)]))
        self.assertIn('STEP=STOP - STEP FAILED: %s started running; nothing was changed' % ISO, out, out)
        self.assertUntouched(bed, calls, before, out)
        self.assertEqual(len(self.runs(bed)), 1)
        self.assertTrue(os.path.exists(os.path.join(self.runs(bed)[0], 'task_before.txt')))
        out, calls, svc, reg = self.run_block(bed, scenario())
        self.assertIn('STEP=PASS', out, out)

    def test_backup_failure_after_isolation_is_returned_by_r(self):
        # The online backup comes after the task is disabled: its failure is
        # a stop after the first change, and R gives the task back.
        bed = Bed(os.path.join(self.tmp, 'bed'))
        good = open(bed.db, 'rb').read()
        open(bed.db, 'wb').write(b'not a database ' * 100)
        broken = sha(bed.db)
        out, calls, svc, reg = self.run_block(bed, scenario())
        self.assertIn('STEP=STOP - STEP FAILED: online backup staging_before', out, out)
        self.assertIn('STAGING_CHANGED=yes', out)
        self.assertEqual(self.task_calls(calls), ['Disable-ScheduledTask \\' + ISO])
        self.assertEqual(self.iso_now(), (False, 'Disabled'))
        out, calls, svc, reg = self.run_restore(bed, self.carry(scenario(), svc, reg))
        self.assertIn('STEP=PASS', out, out)
        self.assertIn('DB_RESTORED=not needed', out)
        self.assertEqual(sha(bed.db), broken)
        self.assertEqual(self.task_calls(calls), ['Enable-ScheduledTask \\' + ISO])
        self.assertEqual(self.iso_now(), (True, 'Ready'))
        open(bed.db, 'wb').write(good)
        out, calls, svc, reg = self.run_block(bed, self.carry(scenario(), svc, reg))
        self.assertIn('STEP=PASS', out, out)

    def test_initially_disabled_task_stays_disabled(self):
        bed = Bed(os.path.join(self.tmp, 'bed'))
        off = iso_task(Enabled=False, State='Disabled')
        sc = scenario(Tasks=[off] + scenario()['Tasks'][1:])
        out, calls, svc, reg = self.run_block(bed, sc, **iso_fingerprints(off))
        self.assertIn('STEP=PASS', out, out)
        self.assertIn('ISOLATE_TASK_NOW=Disabled (was enabled=False', out)
        self.assertEqual(self.task_calls(calls), [])
        self.assertEqual(self.iso_now(), (False, 'Disabled'))
        out, calls, svc, reg = self.run_restore(bed, self.carry(sc, svc, reg))
        self.assertIn('STEP=PASS', out, out)
        self.assertIn('TASK_RESTORED %s enabled=False' % ISO, out)
        self.assertEqual(self.task_calls(calls), [])
        self.assertEqual(self.iso_now(), (False, 'Disabled'))

    def test_lock_held_stops_before_the_swap_and_restore(self):
        bed = Bed(os.path.join(self.tmp, 'bed'))
        before = sha(bed.db)
        sc = scenario(HoldDb=bed.db, Python=sys.executable,
                      HolderScript=HOLDER)
        out, calls, svc, reg = self.run_block(bed, sc)
        self.assertIn('check_db_lock exit 2', out, out)
        self.assertIn('STAGING_CHANGED=yes', out)
        self.assertNotIn('STEP=PASS', out)
        self.assertEqual(sha(bed.db), before)
        run = self.runs(bed)[0]
        self.assertFalse(os.path.exists(os.path.join(run, 'swapped.txt')))
        self.assertFalse(os.path.exists(os.path.join(run, 'returned.txt')))
        self.assertEqual(svc['TransportReportStaging']['Status'], 'Stopped')
        # The lock is checked right after the stop, before the second backup
        # (which would wait on a held lock) and before the checkout.
        self.assertIn('DB_LOCK_AFTER_STOP', out.replace('check_db_lock exit 2', 'DB_LOCK_AFTER_STOP'))
        self.assertEqual([l.split('|')[0] for l in open(os.path.join(run, 'staging_backup.txt')).read().splitlines() if l],
                         ['staging_before'])
        self.assertEqual(self.staging_head(bed), STAGING_HEAD)
        out, calls, svc, reg = self.run_restore(bed, self.carry(scenario(), svc, reg))
        self.assertIn('STEP=PASS', out, out)
        self.assertIn('RESTORE_FROM=none', out)
        self.assertIn('DB_RESTORED=not needed', out)
        self.assertEqual(sha(bed.db), before)
        self.assertEqual(self.staging_head(bed), STAGING_HEAD)
        for name in ('TransportReportStaging',) + BOTS:
            self.assertEqual(svc[name], {'Status': 'Running', 'StartType': 'Automatic'}, name)
        # The lock stopped B1 before the environment edit: nothing to put back.
        self.assertIn('DJI_REFRESH_LAUNCHER=not touched by B1', out)
        self.assertEqual(self.iso_now(), (True, 'Ready'))
        self.assertEqual(reg[SITE_KEY]['AppEnvironmentExtra'], ENV_EXTRA)
        self.assertNotIn('Set-ItemProperty %s AppEnvironmentExtra MultiString' % SITE_KEY, calls)

    def test_fingerprint_baseline_mismatch_stops_after_swap_and_restores(self):
        # fingerprint_before.json that does not belong to the snapshot: caught
        # right after the swap, before the site starts.
        bed = Bed(os.path.join(self.tmp, 'bed'))
        before_rows = self.db_rows(bed.db)
        fp = os.path.join(bed.plan_dir, 'fingerprint_before.json')
        doc = json.load(open(fp))
        doc['raw_area_ha']['sha256'] = '0' * 64
        open(fp, 'w').write(json.dumps(doc, ensure_ascii=True, indent=1, sort_keys=True) + '\n')
        out, calls, svc, reg = self.run_block(bed, scenario())
        self.assertIn('differs from the B0 fingerprint_before.json', out, out)
        self.assertIn('STAGING_CHANGED=yes', out)
        self.assertNotIn('Start-Service TransportReportStaging', calls)
        run = self.runs(bed)[0]
        self.assertTrue(os.path.exists(os.path.join(run, 'swapped.txt')))
        out, calls, svc, reg = self.run_restore(bed, self.carry(scenario(), svc, reg))
        self.assertIn('STEP=PASS', out, out)
        self.assertEqual(self.db_rows(bed.db), before_rows)
        self.assertEqual(self.staging_head(bed), STAGING_HEAD)

    def test_smoke_failure_after_start_is_a_stop(self):
        bed = Bed(os.path.join(self.tmp, 'bed'))
        out, calls, svc, reg = self.run_block(bed, scenario(Web={
            '/login': {'Status': 500, 'Body': 'x'}, '/drones/fields': {'Status': 200, 'Body': LOGIN}}))
        self.assertIn('STEP=STOP', out)
        self.assertIn('STAGING_CHANGED=yes', out)
        self.assertNotIn('STEP=PASS', out)
        run = self.runs(bed)[0]
        self.assertTrue(os.path.exists(os.path.join(run, 'swapped.txt')))
        for name in BOTS:
            self.assertEqual(svc[name], {'Status': 'Stopped', 'StartType': 'Disabled'})

    def test_db_changed_by_the_start_is_a_stop(self):
        bed = Bed(os.path.join(self.tmp, 'bed'))
        out, calls, svc, reg = self.run_block(bed, scenario(
            WriteOnStart=bed.db, Python=sys.executable, WriterScript=WRITER))
        self.assertIn('the database changed when the staging site started', out, out)
        self.assertIn('STAGING_CHANGED=yes', out)
        self.assertNotIn('STEP=PASS', out)

    def b1_pass(self, bed, sc=None):
        sc = sc or scenario()
        out, calls, svc, reg = self.run_block(bed, sc)
        self.assertIn('STEP=PASS', out, out)
        run = self.runs(bed)[0]
        final = [l.split('|') for l in open(os.path.join(run, 'staging_backup.txt')).read().splitlines()
                 if l.startswith('staging_final|')][0]
        return run, final, self.carry(scenario(), svc, reg)

    def test_both_barriers_are_checked_after_the_start(self):
        # Something enables the task again while the site starts: B1 must not
        # report PASS with one barrier down.
        bed = Bed(os.path.join(self.tmp, 'bed'))
        out, calls, svc, reg = self.run_block(bed, scenario(EnableTaskOnSiteStart=ISO))
        self.assertIn('STEP=STOP - STEP FAILED: the barriers are not both in place: %s Enabled=True' % ISO, out, out)
        self.assertIn('STAGING_CHANGED=yes', out)

    def test_restore_refuses_a_changed_task(self):
        # The task or its wrapper changed during the pilot: R leaves disabled
        # what it cannot prove is the task it disabled, and keeps the run open.
        for what in ('xml', 'wrapper'):
            with self.subTest(what):
                bed = Bed(os.path.join(self.tmp, 'bed_' + what))
                run, final, nxt = self.b1_pass(bed)
                if what == 'xml':
                    nxt['Tasks'][0]['Xml'] = ISO_XML + '<Edited/>'
                    needle = 'task XML sha256 is'
                else:
                    with open(bed.wrapper, 'ab') as fh:
                        fh.write(b'# edited\n')
                    needle = 'wrapper sha256 is'
                out, calls, svc, reg = self.run_restore(bed, nxt)
                self.assertIn('STEP=STOP - STEP FAILED: %s %s' % (ISO, needle), out, out)
                self.assertIn('STAGING_CHANGED=yes', out)
                self.assertFalse(os.path.exists(os.path.join(run, 'returned.txt')))
                # left disabled: the wrapper is checked before enabling; the
                # task XML only can be, so it is disabled again at once
                self.assertEqual(self.iso_now(), (False, 'Disabled'))
                self.assertEqual(self.task_calls(calls), [] if what == 'wrapper' else
                                 ['Enable-ScheduledTask \\' + ISO, 'Disable-ScheduledTask \\' + ISO])

    def test_restore_converges_after_a_stop(self):
        bed = Bed(os.path.join(self.tmp, 'bed'))
        tasks = [
            {'TaskName': 'TransportDBBackupStaging', 'State': 'Ready',
             'Execute': 'C:\\transport-report-staging\\backup_staging_db.bat', 'Arguments': ''},
            {'TaskName': 'TopazFuelAgent', 'State': 'Ready',
             'Execute': 'C:\\Program Files\\Python314\\python.exe', 'Arguments': 'C:\\topaz_agent.py'},
        ]
        run, final, nxt = self.b1_pass(bed, scenario(Tasks=tasks_plus(*tasks)))
        bad = dict(nxt, Web={'/login': {'Status': 500, 'Body': 'x'},
                             '/drones/fields': {'Status': 200, 'Body': LOGIN}})
        out, calls, svc, reg = self.run_restore(bed, bad)
        self.assertIn('STEP=STOP', out, out)
        self.assertIn('STAGING_CHANGED=yes', out)
        self.assertTrue(os.path.exists(os.path.join(run, 'db_restored.txt')))
        self.assertFalse(os.path.exists(os.path.join(run, 'returned.txt')))
        self.assertEqual(self.iso_now(), (False, 'Disabled'))
        out, calls, svc, reg = self.run_restore(bed, self.carry(scenario(), svc, reg))
        self.assertIn('STEP=PASS', out, out)
        self.assertIn('DB_RESTORED=already', out)
        self.assertEqual(self.task_calls(calls), ['Enable-ScheduledTask \\' + ISO])
        self.assertEqual(self.iso_now(), (True, 'Ready'))
        # A third run after the return: nothing open, no change, the task as it is.
        out, calls, svc, reg = self.run_restore(bed, self.carry(scenario(), svc, reg))
        self.assertIn('0 open staging runs', out)
        self.assertEqual(calls, [])
        self.assertEqual(sha(bed.db), final[3])
        self.assertEqual(self.staging_head(bed), STAGING_HEAD)
        self.assertEqual(reg[SITE_KEY]['AppEnvironmentExtra'], ENV_EXTRA)
        self.assertEqual(len([d for d in os.listdir(run) if d.startswith('pilot_final_')]), 1)
        for name in ('TransportReportStaging',) + BOTS:
            self.assertEqual(svc[name], {'Status': 'Running', 'StartType': 'Automatic'}, name)

    def test_restore_with_a_damaged_or_missing_database(self):
        for variant in ('interrupted_copy', 'missing', 'garbage'):
            with self.subTest(variant):
                bed = Bed(os.path.join(self.tmp, 'bed_' + variant))
                run, final, nxt = self.b1_pass(bed)
                for side in (bed.db + '-wal', bed.db + '-shm'):
                    if os.path.exists(side):
                        os.remove(side)
                if variant == 'interrupted_copy':
                    os.remove(os.path.join(run, 'placed.txt'))
                    with open(bed.db, 'r+b') as fh:
                        fh.truncate(4096)
                elif variant == 'missing':
                    os.remove(bed.db)
                else:
                    open(bed.db, 'wb').write(b'not a database ' * 300)
                out, calls, svc, reg = self.run_restore(bed, nxt)
                self.assertIn('STEP=PASS', out, out)
                self.assertEqual(sha(bed.db), final[3])
                if variant == 'missing':
                    self.assertIn('PILOT_DB=missing', out)
                else:
                    self.assertIn('PILOT_DB_KEPT=raw files', out)
                    raw = [d for d in os.listdir(run) if d.startswith('pilot_final_')][0]
                    self.assertIn('transport.db', os.listdir(os.path.join(run, raw, 'raw')))
                if variant == 'interrupted_copy':
                    self.assertIn('B1 stopped while it was placing the copy', out)
                if variant == 'garbage':
                    self.assertIn('check_db_lock exit 1', out)

    def test_restore_source_and_environment_drift(self):
        bed = Bed(os.path.join(self.tmp, 'bed'))
        run, final, nxt = self.b1_pass(bed)
        # A later block appends a backup line: R still takes staging_final.
        before = [l for l in open(os.path.join(run, 'staging_backup.txt')).read().splitlines() if l][0].split('|')
        with open(os.path.join(run, 'staging_backup.txt'), 'a') as fh:
            fh.write('pre_recalc|%s|%s|%s\n' % (before[1], before[2], before[3]))
        # Someone edits the site environment after B1.
        nxt['Registry'][SITE_KEY]['AppEnvironmentExtra'] = (
            nxt['Registry'][SITE_KEY]['AppEnvironmentExtra'] + ['NEW_VAR=1'])
        out, calls, svc, reg = self.run_restore(bed, nxt)
        self.assertIn('RESTORE_FROM staging_final', out, out)
        self.assertEqual(sha(bed.db), final[3])
        self.assertIn('ENV_EXTRA=DIFFERS', out)
        self.assertIn('STEP=PASS_WITH_NOTES', out)
        self.assertNotIn('STEP=PASS\n', out)
        self.assertNotIn('do-not-print', out)
        self.assertEqual(reg[SITE_KEY]['AppEnvironmentExtra'][3], 'DJI_REFRESH_LAUNCHER=subprocess')
        self.assertTrue(os.path.exists(os.path.join(run, 'returned.txt')))

    def test_restore_refuses_a_changed_backup(self):
        bed = Bed(os.path.join(self.tmp, 'bed'))
        sc = scenario()
        out, calls, svc, reg = self.run_block(bed, sc)
        self.assertIn('STEP=PASS', out, out)
        run = self.runs(bed)[0]
        final = [l.split('|') for l in open(os.path.join(run, 'staging_backup.txt')).read().splitlines() if l][-1]
        with open(final[1], 'r+b') as fh:
            fh.seek(-1, 2)
            last = fh.read(1)
            fh.seek(-1, 2)
            fh.write(bytes([last[0] ^ 0xFF]))
        self.assertNotEqual(sha(final[1]), final[3])
        pilot_sha = sha(bed.db)
        out, calls, svc2, reg2 = self.run_restore(bed, self.carry(sc, svc, reg))
        self.assertIn('changed since B1 (sha256)', out, out)
        self.assertIn('STAGING_CHANGED=no', out)
        self.assertEqual(calls, [])
        self.assertEqual(sha(bed.db), pilot_sha)
        self.assertEqual(self.staging_head(bed), PIN)


W0_HARNESS = os.path.join(HERE, 'card_pilot_w0_harness.ps1')
# Every one of these sits somewhere on the stand-in workstation; none may be printed or logged.
W0_SECRETS = ('tok-SECRET-1', 'COOKIE-SECRET-2', 'WRAPPER-SECRET-3', 'PROC-SECRET-4', 'USER-SECRET-5',
              'CONSOLE-SECRET-6', 'RUN-SECRET-7', 'origin-pass-8', 'abcdefabcdefabcdef')
W0_NOHOST = 'card-pilot-w0.invalid'
STAGING_LOGIN_URL = 'http://10.103.25.14:5051/login'
PROD_LOGIN_URL = 'http://10.103.25.14:5050/login'
HOLDOUT_PY = 'C:\\VehicleSoft_Holdout\\session_venv\\Scripts\\python.exe'


def write(path, text, mode='w'):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, mode, encoding=None if 'b' in mode else 'utf-8') as fh:
        fh.write(text)


def read(path):
    with open(path, encoding='utf-8', errors='replace') as fh:
        return fh.read()


def read_bytes(path):
    with open(path, 'rb') as fh:
        return fh.read()


def sparse_clone(path, commit):
    """The repository at `commit`, only the root files and drone_collector/."""
    sh('git', 'clone', '-q', '--shared', '--no-checkout', REPO_ROOT, path)
    sh('git', 'sparse-checkout', 'set', 'drone_collector', cwd=path)
    sh('git', '-c', 'advice.detachedHead=false', 'checkout', '-q', '--detach', commit, cwd=path)


def tree_state(root, skip):
    """Every file and folder under root (but `skip`): size, mtime and sha256."""
    state = {}
    for base, dirs, files in os.walk(root):
        if os.path.abspath(base).startswith(os.path.abspath(skip)):
            continue
        state[os.path.relpath(base, root)] = 'dir'
        for name in files:
            p = os.path.join(base, name)
            if os.path.islink(p):
                continue
            st = os.stat(p)
            state[os.path.relpath(p, root)] = (st.st_size, st.st_mtime_ns, sha(p))
    return state


class Workstation(object):
    """A disposable workstation like the one of the 21.09.2026 qualification."""

    def __init__(self, root):
        self.root = root
        self.c = os.path.join(root, 'C')
        self.d = os.path.join(root, 'D')
        self.work = os.path.join(self.c, 'VehicleSoft_CardPilot')
        # Named up front: the scenario mentions them whether or not they are built.
        self.src = os.path.join(self.c, 'VehicleSoft_Holdout', 'src')
        self.deep = os.path.join(self.d, 'l1', 'l2', 'l3', 'l4', 'l5', 'repo')
        self.wrapper = os.path.join(self.c, 'ProgramData', 'VehicleSoft', 'area_daily.ps1')
        self.browsers = os.path.join(self.c, 'VehicleSoft_Holdout', 'playwright-browsers')
        os.makedirs(self.c)
        os.makedirs(self.d)

    def drives(self):
        return [{'Name': 'C', 'Root': self.c + os.sep, 'Free': 2e11, 'Used': 1e11},
                {'Name': 'D', 'Root': self.d + os.sep, 'Free': 5e11, 'Used': 2e11},
                {'Name': 'Z', 'Root': 'Z:\\', 'Free': 1e9, 'Used': 1e9, 'DisplayRoot': '\\\\fileserver\\share'}]

    def holdout(self):
        """Git checkout of the holdout collector, its own session, venv and browsers."""
        h = os.path.join(self.c, 'VehicleSoft_Holdout')
        dc = os.path.join(self.src, 'drone_collector')
        for name in ('main.py', 'config.py', '__init__.py'):
            write(os.path.join(dc, name), read(os.path.join(REPO_ROOT, 'drone_collector', name)))
        sh('git', 'init', '-q', '-b', 'main', cwd=self.src)
        sh('git', 'add', 'drone_collector', cwd=self.src)
        sh('git', '-c', 'user.name=t', '-c', 'user.email=t@t', 'commit', '-q', '-m', 'holdout', cwd=self.src)
        sh('git', 'remote', 'add', 'origin', 'https://owner:origin-pass-8@github.com/sinte3/vehicle-soft.git', cwd=self.src)
        self.src_head = sh('git', 'rev-parse', 'HEAD', cwd=self.src)
        self.session = os.path.join(h, 'session', 'storage_state.json')
        write(self.session, '{"cookies": [{"name": "sid", "value": "COOKIE-SECRET-2"}], "origins": []}')
        write(os.path.join(dc, '.env'),
              'VEHICLE_SOFT_BASE_URL=http://10.103.25.14:5051\n'
              'DRONE_API_TOKEN=tok-SECRET-1\n'
              'DJI_STORAGE_STATE=%s\n'
              'DJI_HEADLESS=true   # unattended\n'
              'export DJI_SOURCE_PAUSE_MS="1500"\n' % self.session)
        self.venv = os.path.join(h, 'session_venv')
        write(os.path.join(self.venv, 'pyvenv.cfg'), 'home = C:\\Program Files\\Python314\ninclude-system-site-packages = false\nversion = 3.14.0\n')
        for dist in ('playwright-1.61.0', 'python_dotenv-1.0.1', 'requests-2.32.3'):
            os.makedirs(os.path.join(self.venv, 'Lib', 'site-packages', dist + '.dist-info'))
        write(os.path.join(self.venv, 'Scripts', 'python.exe'), '')
        os.makedirs(os.path.join(self.browsers, 'chromium-1181'))
        data = os.path.join(dc, 'data')
        self.lock = os.path.join(data, 'collector.lock')
        write(self.lock, '')
        write(self.lock + '.owner', '{"pid": 999999, "host": "BAK-TEX11", "purpose": "sources", "since_utc": "2026-10-03T05:00:00"}')
        self.outbox = os.path.join(data, 'outbox')
        for rel in ('pending/source_1_a.json', 'pending/source_2_b.json', 'sent/source_0_c.json',
                    'sent/route_9_d.json', 'pending/x.json.tmp'):
            write(os.path.join(self.outbox, *rel.split('/')), '{}')
        write(os.path.join(dc, 'logs', 'collector.log'), 'INFO run\n')

    def area_daily(self):
        """An older collector copy, not a git checkout, whose .env names production."""
        self.area = os.path.join(self.c, 'VehicleSoft_AreaDaily', 'src')
        write(os.path.join(self.area, 'drone_collector', 'main.py'), "parser.add_argument('--routes')\n")
        write(os.path.join(self.area, 'drone_collector', '.env'),
              'VEHICLE_SOFT_BASE_URL=http://10.103.25.14:5050\nDRONE_API_TOKEN=tok-SECRET-1\n')

    def full_clone(self):
        self.full = os.path.join(self.d, 'work', 'vehicle-soft')
        sparse_clone(self.full, PIN)

    def decoys(self):
        """Where the scan must not look, and a checkout only a task can show."""
        write(os.path.join(self.c, 'Windows', 'vs', 'drone_collector', 'main.py'), '# skipped\n')
        write(os.path.join(self.deep, 'drone_collector', 'main.py'), "'--sources'\n")
        self.backup = os.path.join(self.d, 'backup', 'storage_state.json')
        os.makedirs(os.path.dirname(self.backup))
        shutil.copy2(self.session, self.backup)
        try:
            os.symlink(self.c, os.path.join(self.c, 'loop'), target_is_directory=True)
        except OSError:
            pass
        write(self.wrapper,
              "$env:DRONE_API_TOKEN = 'WRAPPER-SECRET-3'\n"
              "$env:DJI_STORAGE_STATE = 'C:\\VehicleSoft_Holdout\\session\\storage_state.json'\n"
              "& '%s' -m drone_collector.main --sources --ids-file ids.txt --send-sources\n"
              "# receiver http://10.103.25.14:5051\n" % HOLDOUT_PY)

    def scenario(self, **changes):
        daily = [{'Class': 'MSFT_TaskDailyTrigger', 'Props': {'DaysInterval': '1', 'Enabled': 'True', 'StartBoundary': 'daily-0700'}}]
        value = {
            'Host': 'BAK-TEX11',
            'Drives': self.drives(),
            'Tasks': [
                {'TaskName': 'HoldoutSources', 'TaskPath': '\\', 'State': 'Ready', 'Enabled': True, 'UserId': 'BAK-TEX11\\owner',
                 'Actions': [{'Execute': HOLDOUT_PY, 'WorkingDirectory': self.src,
                              'Arguments': '-m drone_collector.main --sources --from 2026-09-01 --send-sources --token abcdefabcdefabcdefabcdefabcdef123456'}],
                 'Triggers': daily, 'LastRunTime': 'last-0700', 'LastTaskResult': '0', 'NextRunTime': 'next-0700'},
                {'TaskName': 'AreaWrapper', 'TaskPath': '\\VehicleSoft\\', 'State': 'Disabled', 'Enabled': False, 'UserId': 'BAK-TEX11\\owner',
                 'Actions': [{'Execute': 'powershell.exe', 'Arguments': '-NoProfile -File "%s"' % self.wrapper, 'WorkingDirectory': ''}],
                 'Triggers': daily},
                {'TaskName': 'DeepRepoTask', 'TaskPath': '\\', 'State': 'Ready', 'Enabled': True, 'UserId': 'BAK-TEX11\\owner',
                 'Actions': [{'Execute': 'C:\\Python314\\python.exe', 'Arguments': '-m drone_collector.main --dry-run', 'WorkingDirectory': self.deep}],
                 'Triggers': [], 'NextRunTime': 'never'},
                {'TaskName': 'GoogleUpdateTaskMachineUA', 'TaskPath': '\\', 'State': 'Ready', 'Enabled': True, 'UserId': 'SYSTEM',
                 'Actions': [{'Execute': 'C:\\Program Files (x86)\\Google\\Update\\GoogleUpdate.exe', 'Arguments': '/ua', 'WorkingDirectory': ''}],
                 'Triggers': daily},
                {'TaskName': 'ScheduledDefrag', 'TaskPath': '\\Microsoft\\Windows\\Defrag\\', 'State': 'Ready', 'Enabled': True, 'UserId': 'SYSTEM',
                 'Actions': [{'Execute': 'drone_collector_defrag.exe', 'Arguments': '', 'WorkingDirectory': ''}], 'Triggers': daily},
            ],
            'Processes': [
                {'ProcessId': 4242, 'Name': 'python.exe', 'ExecutablePath': HOLDOUT_PY, 'CreationDate': 'started-0500',
                 'CommandLine': HOLDOUT_PY + ' -m drone_collector.main --sources --ids-file ids.txt DRONE_API_TOKEN=PROC-SECRET-4'},
                {'ProcessId': 4243, 'Name': 'chrome.exe', 'CommandLine': 'C:\\VehicleSoft_Holdout\\playwright-browsers\\chromium-1181\\chrome.exe --remote-debugging-pipe'},
                {'ProcessId': 4244, 'Name': 'chrome.exe', 'CommandLine': 'C:\\Users\\o\\AppData\\Local\\ms-playwright\\chromium-1181\\chrome.exe --type=renderer'},
                {'ProcessId': 4245, 'Name': 'explorer.exe', 'CommandLine': 'C:\\Windows\\explorer.exe'},
            ],
            'Services': [{'Name': 'Spooler', 'State': 'Running', 'StartMode': 'Auto', 'StartName': 'LocalSystem',
                          'PathName': 'C:\\Windows\\System32\\spoolsv.exe'}],
            'Registry': {
                MACHINE_KEY: {'Path': 'C:\\Windows', 'PLAYWRIGHT_BROWSERS_PATH': self.browsers},
                'HKCU:\\Environment': {'TEMP': 'C:\\Temp', 'DRONE_API_TOKEN': 'USER-SECRET-5'},
                'HKCU:\\Software\\Microsoft\\Windows\\CurrentVersion\\Run': {
                    'HoldoutTray': 'C:\\VehicleSoft_Holdout\\tray.exe --token RUN-SECRET-7',
                    'OneDrive': 'C:\\OneDrive\\OneDrive.exe /background'},
            },
            'Web': {STAGING_LOGIN_URL: {'Status': 200, 'Body': LOGIN}, PROD_LOGIN_URL: {'Status': 200, 'Body': LOGIN}},
        }
        value.update(changes)
        return value

    def block(self, **override):
        text = blocks()['W0']
        values = {'server': W0_NOHOST, 'work': self.work, 'pilotDir': os.path.join(self.work, 'w1')}
        values.update(override)
        for name, value in values.items():
            if isinstance(value, int):
                text, n = re.subn(r'^(  \$%s\s*= )\d+$' % name, r'\g<1>%d' % value, text, flags=re.M)
            else:
                text, n = re.subn(r"^(  \$%s\s*= )'[^']*'$" % name, lambda m: m.group(1) + "'" + value + "'", text, flags=re.M)
            assert n == 1, name
        return text


def line_with(out, prefix):
    hits = [l for l in out.splitlines() if l.startswith(prefix)]
    assert len(hits) == 1, (prefix, hits)
    return hits[0]


@unittest.skipUnless(POWERSHELL, 'CARD_PILOT_POWERSHELL is not set')
class W0InPowerShell(unittest.TestCase):
    """W0 as printed in the document, against a stand-in workstation.

    [REASON]: W0 is the first block that runs on the workstation, where no
    path is known in advance. What must hold whatever it finds: nothing on
    the machine changes, no secret it walks past reaches the console or the
    log, and the facts W1 is planned from (which checkout, session, lock,
    queue and receiver) are the ones on disk.
    """

    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.ws = Workstation(os.path.join(self.tmp, 'ws'))

    def run_w0(self, sc, **override):
        bf = os.path.join(self.tmp, 'w0.ps1')
        sf = os.path.join(self.tmp, 'scenario.json')
        cf = os.path.join(self.tmp, 'web.txt')
        write(bf, self.ws.block(**override))
        write(sf, json.dumps(sc))
        env = {k: v for k, v in os.environ.items()
               if not re.match(r'(?i)(DJI_|DRONE_|VEHICLE_SOFT_|PLAYWRIGHT_)', k)}
        env.update({'DRONE_API_TOKEN': 'CONSOLE-SECRET-6',
                    'VEHICLE_SOFT_BASE_URL': 'http://owner:CONSOLE-SECRET-6@10.103.25.14:5051/'})
        before = tree_state(self.ws.root, self.ws.work)
        p = subprocess.run([POWERSHELL, '-NoProfile', '-NonInteractive', '-ExecutionPolicy', 'Bypass',
                            '-File', W0_HARNESS, '-BlockFile', bf, '-ScenarioFile', sf, '-CallsFile', cf],
                           capture_output=True, text=True, timeout=900, env=env)
        out = p.stdout + p.stderr
        self.assertNotIn('BLOCK THREW', out, out)
        # Read only: every file and folder of the machine is as it was.
        self.assertEqual(tree_state(self.ws.root, self.ws.work), before)
        logs = os.listdir(self.ws.work) if os.path.isdir(self.ws.work) else []
        self.assertEqual(len(logs), 1, logs)
        self.assertRegex(logs[0], r'^card_pilot_w0_\d{8}_\d{6}\.log$')
        log = read(os.path.join(self.ws.work, logs[0]))
        for secret in W0_SECRETS:
            self.assertNotIn(secret, out)
            self.assertNotIn(secret, log)
        self.web = [l for l in read(cf).splitlines() if l]
        return out

    def test_workstation(self):
        ws = self.ws
        ws.holdout()
        ws.area_daily()
        ws.full_clone()
        ws.decoys()
        out = self.run_w0(ws.scenario())
        last = [l for l in out.splitlines() if l.strip()][-1]
        self.assertEqual(last, 'STEP=PASS (read only: nothing operational was changed)', out)
        self.assertEqual(self.web, ['Get ' + STAGING_LOGIN_URL, 'Get ' + PROD_LOGIN_URL])
        for key in ('HOST=BAK-TEX11', 'HOST_ROLE=workstation, not SRV-YOQSH',
                    'ENV DRONE_API_TOKEN machine=absent user=set (value not shown) this_console=set (value not shown)',
                    'ENV VEHICLE_SOFT_BASE_URL machine=absent user=absent this_console=http://10.103.25.14:5051/',
                    'ENV PLAYWRIGHT_BROWSERS_PATH machine=' + ws.browsers + ' user=absent this_console=absent',
                    # three checkouts on the disk; Windows\, the depth-6 one and the loop are not walked into
                    'FOUND checkouts=3 venvs=1 storage_state.json=2 playwright_browser_folders=1',
                    '  CHECKOUT_FROM_TASK ' + ws.deep,
                    'TASK \\HoldoutSources state=Ready enabled=True user=BAK-TEX11\\owner logon=Interactive runlevel=Limited runs_collector=yes ports=none',
                    '  TRIGGER 1 MSFT_TaskDailyTrigger DaysInterval=1 Enabled=True StartBoundary=daily-0700',
                    '  RUNS last=last-0700 result=0 next=next-0700',
                    'TASK \\VehicleSoft\\AreaWrapper state=Disabled enabled=False',
                    '  TRIGGERS=none (runs only when started by hand or by another program)',
                    'OTHER_TASKS=1: \\GoogleUpdateTaskMachineUA',
                    'SERVICES=none related to the collector or Vehicle Soft',
                    'COLLECTOR_PROCESSES_NOW=1 PLAYWRIGHT_BROWSER_PROCESSES_NOW=2',
                    'LOGON_ENTRY HKCU:\\Software\\Microsoft\\Windows\\CurrentVersion\\Run HoldoutTray=C:\\VehicleSoft_Holdout\\tray.exe --token [hidden]',
                    'COLLECTOR_PYTHON task HoldoutSources exe=' + HOLDOUT_PY + ' version=missing',
                    'BROWSERS ' + ws.browsers + ' chromium-1181',
                    'STAGING_5051_LOGIN=200 login form (GET only, connectivity)',
                    'PRODUCTION_5050_LOGIN=200 login form (GET only, connectivity)',
                    'PROD_FILES=the production data folder is not readable from here: \\\\' + W0_NOHOST + '\\C$\\transport-report\\drone_collector\\data',
                    'PROD_SCHEDULE_DOCUMENTED=DroneCollectorDaily daily about 06:00',
                    'PILOT_FOLDER_PROPOSED=' + os.path.join(ws.work, 'w1') + ' exists=False',
                    'PILOT_FOLDER_OVERLAP=none: ', 'EXISTING_QUEUES=4', 'W0_ATTENTION=7'):
            self.assertIn(key, out)
        self.assertIn('PROD_SCHEDULE=not readable from this machine: ', out)
        self.assertNotIn('Windows' + os.sep + 'vs', out)
        self.assertNotIn('ScheduledDefrag', out)
        self.assertNotIn('OneDrive', out)
        self.assertNotIn(os.path.join(ws.c, 'loop'), out)
        self.assertIn('  ACTION 1 exe=' + HOLDOUT_PY + ' args=-m drone_collector.main --sources --from 2026-09-01 --send-sources --token [hidden] wd=' + ws.src, out)
        self.assertRegex(out, r'PROCESS pid=4242 name=python\.exe started=started-0500 cmd=.*DRONE_API_TOKEN=\[hidden\]')
        self.assertIn('  SCRIPT %s bytes=%d sha256=%s mentions=drone_collector,--sources,--ids-file,--send-sources,storage_state,DJI_STORAGE_STATE '
                      'sets_env=DRONE_API_TOKEN,DJI_STORAGE_STATE python=%s' % (ws.wrapper, os.path.getsize(ws.wrapper), sha(ws.wrapper), HOLDOUT_PY), out)
        # the holdout checkout: git, support, settings and where each comes from, session, lock, queue, logs
        n = re.search(r'^CHECKOUT (\d+) %s \(collector only\)$' % re.escape(ws.src), out, re.M).group(1)
        block = out[out.index('CHECKOUT %s %s' % (n, ws.src)):]
        block = block[:block.index('\nCHECKOUT ', 1) if '\nCHECKOUT ' in block[1:] else block.index('== 6.')]
        for key in ('  GIT HEAD=%s branch=main tags=none tracked_changes=0 origin=https://[hidden]@github.com/sinte3/vehicle-soft.git' % ws.src_head,
                    '  COLLECTOR_CODE=pilot pin not in this clone (nothing was fetched)',
                    '  SUPPORTS sources=yes ids_file=yes send_sources=yes lock=yes outbox_setting=yes logs=fixed to this checkout',
                    '  DOTENV %s names=DJI_HEADLESS,DJI_SOURCE_PAUSE_MS,DJI_STORAGE_STATE,DRONE_API_TOKEN,VEHICLE_SOFT_BASE_URL' % os.path.join(ws.src, 'drone_collector', '.env'),
                    '  EFFECTIVE VEHICLE_SOFT_BASE_URL=http://10.103.25.14:5051/ (from .env)',
                    '  EFFECTIVE DRONE_API_TOKEN=set (value not shown) (from user environment)',
                    '  EFFECTIVE DJI_STORAGE_STATE=%s (from .env)' % ws.session,
                    '  EFFECTIVE DJI_COLLECTOR_LOCK_PATH=absent (from default)',
                    '  EFFECTIVE DJI_HEADLESS=true (from .env)',
                    '  RECEIVER=staging :5051',
                    '  SESSION %s bytes=%d' % (ws.session, os.path.getsize(ws.session)),
                    'sha256=' + sha(ws.session),
                    '  LOCK %s file=present' % ws.lock,
                    'owner_hint=present pid=999999 host=BAK-TEX11 purpose=sources',
                    'pid_running_here=no',
                    '  OUTBOX %s pending=2 (source=2) sent=2 (route=1 source=1) corrupt=none tmp=1' % ws.outbox,
                    'newest=collector.log'):
            self.assertIn(key, block)
        self.assertRegex(out, r'(?m)^CHECKOUT \d+ %s \(collector only\)\n  GIT=not a git checkout; main\.py sha256=[0-9a-f]{64}\n'
                              r'  SUPPORTS sources=no ids_file=no send_sources=no lock=no outbox_setting=no logs=unknown\n'
                              r'  LOCK_NOTE=this code predates the collector lock' % re.escape(ws.area))
        self.assertRegex(out, r'(?m)^CHECKOUT \d+ %s \(whole Vehicle Soft repository\)\n  GIT HEAD=%s branch=detached tags=none tracked_changes=0 origin=%s\n'
                              r'  COLLECTOR_CODE=same as the pilot pin$' % (re.escape(ws.full), PIN, re.escape(REPO_ROOT)))
        self.assertRegex(out, r'VENV %s version=3\.14\.0 home=C:\\Program Files\\Python314 python=\S+ python_present=True '
                              r'playwright=1\.61\.0 python_dotenv=1\.0\.1 requests=2\.32\.3' % re.escape(ws.venv))
        # the session: one file, a byte copy on D:, used by the holdout checkout; not comparable with production from here
        s = line_with(out, 'SESSION_FILE ' + ws.session + ' ')
        self.assertIn(' sha256=%s used_by_checkout=%s same_bytes_as=%s' % (sha(ws.session), n, ws.backup), s)
        self.assertIn('  SESSION_VS_PRODUCTION=OWN_FILE_ON_THIS_MACHINE: the production session could not be compared from here', out)
        self.assertIn('LOCK_VS_PRODUCTION checkout=%s %s -> a lock on BAK-TEX11 only' % (n, ws.lock), out)
        self.assertIn('CANDIDATE %s %s head=%s code=pilot pin not in this clone (nothing was fetched) sources=True ids_file=True '
                      'send_sources=True lock=True outbox_setting=True receiver=staging :5051 session_present=True' % (n, ws.src, ws.src_head), out)
        for note in ('ATTENTION DRONE_API_TOKEN is set in the user environment',
                     'ATTENTION VEHICLE_SOFT_BASE_URL is set in this console only',
                     'ATTENTION enabled scheduled task HoldoutSources runs the collector or the daily cycle here (next run next-0700)',
                     'ATTENTION enabled scheduled task DeepRepoTask runs the collector',
                     'ATTENTION 1 collector or cycle process(es) are running on this machine now',
                     'ATTENTION checkout %s sends to production :5050' % ws.area,
                     'ATTENTION the collector lock of checkout %s has an owner hint' % ws.src):
            self.assertIn(note, out)
        self.assertNotIn('ATTENTION enabled scheduled task AreaWrapper', out)

    def test_on_the_server_the_files_are_the_production_ones(self):
        ws = self.ws
        prod = os.path.join(ws.c, 'transport-report')
        sparse_clone(prod, PROD)
        data = os.path.join(prod, 'drone_collector', 'data')
        write(os.path.join(data, 'storage_state.json'), '{"cookies": [{"value": "COOKIE-SECRET-2"}]}')
        write(os.path.join(data, 'collector.lock'), '')
        bat = os.path.join(prod, 'drone_daily.bat')
        write(bat, 'cd /d C:\\transport-report\r\npython -m drone_collector.main\r\n')
        sc = ws.scenario(Host=W0_NOHOST, Drives=ws.drives()[:1], Processes=[], Registry={}, Tasks=[
            {'TaskName': 'DroneCollectorDaily', 'TaskPath': '\\', 'State': 'Ready', 'Enabled': True, 'UserId': 'SRV-YOQSH\\umid',
             'Actions': [{'Execute': bat, 'Arguments': '', 'WorkingDirectory': prod}],
             'Triggers': [{'Class': 'MSFT_TaskDailyTrigger', 'Props': {'DaysInterval': '1', 'StartBoundary': 'daily-0600'}}],
             'NextRunTime': 'next-0600'}])
        out = self.run_w0(sc, serverData=data)
        self.assertTrue(out.rstrip().endswith('STEP=PASS (read only: nothing operational was changed)'), out)
        for key in ('HOST_ROLE=SRV-YOQSH itself, the production server -- not a separate workstation',
                    'PROD_FILES=this is SRV-YOQSH: the production files and tasks are the local ones above',
                    '  SESSION_VS_PRODUCTION=SAME_FILE: this is the production collector session',
                    '-> SAME_LOCK as the production collector', '  COLLECTOR_CODE=same as the pilot pin',
                    'TASK \\DroneCollectorDaily state=Ready enabled=True', 'runs_collector=yes',
                    'ATTENTION enabled scheduled task DroneCollectorDaily runs the collector or the daily cycle here (next run next-0600)'):
            self.assertIn(key, out)
        for key in ('PROD_SCHEDULE', 'REMOTE_READ'):
            self.assertNotIn(key, out)

    def test_no_checkout_is_a_stop(self):
        out = self.run_w0(self.ws.scenario(Tasks=[], Processes=[]))
        self.assertIn('FOUND checkouts=0', out)
        self.assertTrue(out.rstrip().endswith('STEP=STOP - no drone_collector checkout was found on this machine '
                                              '(read only: nothing operational was changed)'), out)

    def test_scan_time_limit_is_said(self):
        self.ws.holdout()
        out = self.run_w0(self.ws.scenario(Tasks=[], Processes=[]), budgetSec=0)
        self.assertIn('complete=no (time limit 0 s)', out)
        self.assertIn('ATTENTION the folder scan stopped at its time limit', out)
        self.assertTrue(out.rstrip().endswith('STEP=STOP - no drone_collector checkout was found on this machine '
                                              '(the scan stopped at its time limit) (read only: nothing operational was changed)'), out)

    def test_a_section_that_cannot_be_read_is_a_stop_the_rest_still_printed(self):
        self.ws.holdout()
        out = self.run_w0(self.ws.scenario(TasksFail='Access is denied', Web={}))
        self.assertRegex(out, r'SECTION_FAILED 4 tasks and processes: Access is denied \[block line \d+\]')
        self.assertIn('CHECKOUT 1 ' + self.ws.src, out)
        self.assertIn('STAGING_5051_LOGIN=ERROR', out)
        self.assertIn('ATTENTION the staging site does not answer from this machine', out)
        self.assertRegex(out.rstrip().splitlines()[-1],
                         r'^STEP=STOP - 4 tasks and processes: Access is denied \[block line \d+\] \(read only: nothing operational was changed\)$')


@unittest.skipUnless(POWERSHELL and os.name == 'nt', 'needs Windows and CARD_PILOT_POWERSHELL')
class TaskDiscoveryOnWindows(unittest.TestCase):
    registration = '-Settings (New-ScheduledTaskSettingsSet -MultipleInstances IgnoreNew)'

    """D1 against a REAL scheduled task on the Windows runner.

    [REASON]: D1 reads Task Scheduler objects (actions, CIM triggers,
    principal, settings, Get-ScheduledTaskInfo, Export-ScheduledTask) that
    no stand-in reproduces faithfully. Here only the constants change: the
    task name (unique per run), the host, the folders, the python and the
    database. Everything else is the block as printed.
    """

    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.task = 'CardPilotD1Test%d' % os.getpid()
        self.root = os.path.join(self.tmp, 'transport-report-staging')
        os.makedirs(os.path.join(self.root, 'instance', 'dji_refresh_logs'))
        os.makedirs(os.path.join(self.root, 'ops'))
        os.makedirs(os.path.join(self.root, 'tools'))
        open(os.path.join(self.root, 'tools', 'dji_area_daily.py'), 'w').write('# stand-in\n')
        self.db = os.path.join(self.root, 'instance', 'transport.db')
        con = sqlite3.connect(self.db)
        con.execute('CREATE TABLE drone_area_cycle_runs (id INTEGER PRIMARY KEY, trigger_kind TEXT, '
                    'status TEXT, requested_by_name TEXT, requested_at TEXT, started_at TEXT, '
                    'finished_at TEXT, current_step TEXT)')
        con.executemany('INSERT INTO drone_area_cycle_runs (trigger_kind, status, requested_by_name, '
                        'requested_at, started_at, finished_at, current_step) VALUES (?,?,?,?,?,?,?)',
                        [('SCHEDULED', 'SUCCESS', 'Person Name', '2026-10-02 03:00:00',
                          '2026-10-02 03:00:01', '2026-10-02 03:20:00', 'RECALC')])
        con.commit()
        con.close()
        self.wrapper = os.path.join(self.root, 'ops', 'dji_area_daily_staging.ps1')
        open(self.wrapper, 'w').write(
            '$env:DRONE_API_TOKEN = "abcdefabcdefabcdefabcdefabcdefabcdef1234"\n'
            'Set-Location ' + self.root + '\n'
            '& python tools\\dji_area_daily.py --db instance\\transport.db\n')
        script = ('$a = New-ScheduledTaskAction -Execute powershell.exe -Argument \'-NoProfile -ExecutionPolicy Bypass -File "%s" -Token abcdefghabcdefghabcdefghabcdefgh99\' -WorkingDirectory \'%s\'\n'
                  '$t = New-ScheduledTaskTrigger -Daily -At 3am\n'
                  'Register-ScheduledTask -TaskName \'%s\' -Action $a -Trigger $t %s | Out-Null\n'
                  % (self.wrapper, self.root, self.task, self.registration))
        self.ps(script)
        self.addCleanup(self.ps, "Unregister-ScheduledTask -TaskName '%s' -Confirm:$false" % self.task)

    def ps(self, script):
        path = os.path.join(self.tmp, 'ps_%d.ps1' % len(os.listdir(self.tmp)))
        open(path, 'w').write(script)
        p = subprocess.run([POWERSHELL, '-NoProfile', '-NonInteractive', '-ExecutionPolicy', 'Bypass',
                            '-File', path], capture_output=True, text=True, timeout=300)
        self.assertEqual(p.returncode, 0, p.stdout + p.stderr)
        return p.stdout

    def run_d1(self, task=None):
        import socket
        block = blocks()['D1']
        values = {'taskName': task or self.task, 'expectedHost': socket.gethostname(),
                  'root': self.root, 'prodRoot': REPO_ROOT, 'python': sys.executable,
                  'db': self.db, 'work': os.path.join(self.tmp, 'work')}
        for name, value in values.items():
            pat = re.compile(r"^(  \$%s\s*= )'[^']*'$" % name, re.M)
            self.assertEqual(len(pat.findall(block)), 1, name)
            block = pat.sub(lambda m: m.group(1) + "'" + value + "'", block)
        return self.ps(block)

    def test_real_task(self):
        out = self.run_d1()
        self.assertIn('STEP=PASS (read only: nothing was changed)', out, out)
        for key in ('TASKS_WITH_THIS_NAME=1', 'TASK_NAME=' + self.task, 'ENABLED=True',
                    'ACTION 1 TYPE=MSFT_TaskExecAction', 'ACTION 1 EXECUTE=powershell.exe',
                    'TRIGGER 1 MSFT_TaskDailyTrigger', 'LAST_TASK_RESULT=', 'NEXT_RUN_TIME=',
                    'ACTION_FINGERPRINT=', 'TRIGGER_FINGERPRINT=', 'TASK_XML_SHA256=',
                    'POINTS_AT STAGING_FOLDER=yes', 'POINTS_AT DAILY_CYCLE=yes',
                    'STAGING_WRITER=yes', 'CYCLE_RUNS_BY_KIND SCHEDULED count=1',
                    '[line hidden: it names TOKEN]', '-Token [hidden]', 'BUTTON_STARTS_THIS_TASK=no'):
            self.assertIn(key, out)
        # Get-Item gives the long form of the temp folder (RUNNER~1 -> the
        # account name), so the file lines are matched by their tail.
        self.assertRegex(out, r'FILE \S*\\ops\\dji_area_daily_staging\.ps1 BYTES=')
        self.assertRegex(out, r'FILE \S*\\tools\\dji_area_daily\.py BYTES=')
        for secret in ('abcdefabcdefabcdef', 'abcdefghabcdefgh', 'Person Name'):
            self.assertNotIn(secret, out)
        # read only: the task is exactly as registered
        self.assertIn('Ready', self.ps("(Get-ScheduledTask -TaskName '%s').State" % self.task))
        # the same fingerprints on a second read
        again = self.run_d1()
        pick = lambda text, key: [l for l in text.splitlines() if l.startswith(key)]
        for key in ('ACTION_FINGERPRINT=', 'TRIGGER_FINGERPRINT=', 'TASK_XML_SHA256='):
            self.assertEqual(pick(out, key), pick(again, key), key)
        # a disabled task reads as disabled
        self.ps("Disable-ScheduledTask -TaskName '%s' | Out-Null" % self.task)
        off = self.run_d1()
        self.assertIn('ENABLED=False', off)
        self.assertIn('STATE=Disabled', off)

    def test_missing_task_is_a_stop(self):
        out = self.run_d1(task='CardPilotNoSuchTask%d' % os.getpid())
        self.assertIn('TASKS_WITH_THIS_NAME=0', out, out)
        self.assertIn('STEP=STOP - STEP FAILED: no scheduled task named', out)



class TaskIsolationOnWindows(TaskDiscoveryOnWindows):
    """B1's disable and R's enable on a REAL task registered like the live one.

    [REASON]: R refuses to close the run unless the action, trigger and task
    XML fingerprints after Enable-ScheduledTask equal the ones saved before
    Disable-ScheduledTask. Whether Task Scheduler gives back the same
    definition after that round trip is a property of Windows, not of the
    block, so it is proved here on the real service, with the live task's
    principal (S4U, Highest) and settings, by the functions B1 and R carry.
    The D1 tests run again on this task as well.
    """
    registration = ('-Principal (New-ScheduledTaskPrincipal -UserId ($env:USERDOMAIN + \'\\\' + $env:USERNAME) '
                    '-LogonType S4U -RunLevel Highest) '
                    '-Settings (New-ScheduledTaskSettingsSet -MultipleInstances IgnoreNew -StartWhenAvailable)')

    def test_disable_and_enable_round_trip(self):
        d1 = self.run_d1()
        self.assertIn('STEP=PASS', d1, d1)
        self.assertIn('LogonType=S4U RunLevel=Highest', d1)
        printed = dict(l.split('=', 1) for l in d1.splitlines()
                       if l.startswith(('ACTION_FINGERPRINT=', 'TRIGGER_FINGERPRINT=')))
        printed['TASK_XML_SHA256'] = [l for l in d1.splitlines() if l.startswith('TASK_XML_SHA256=')][0].split('=', 1)[1].split()[0]
        b1 = blocks()['B1']
        funcs = ''.join(function_text(b1, n) for n in ('Get-TextHash', 'Get-CimLine', 'Get-IsoState', 'Get-IsoProcesses'))
        script = (funcs +
                  "$n = '%s'\n$w = '%s'\n"
                  "$a = Get-IsoState $n $w\n"
                  "Disable-ScheduledTask -TaskName $n -TaskPath '\\' | Out-Null\n"
                  "$b = Get-IsoState $n $w\n"
                  "Enable-ScheduledTask -TaskName $n -TaskPath '\\' | Out-Null\n"
                  "$c = Get-IsoState $n $w\n"
                  "$p = @(Get-IsoProcesses $n).Count\n"
                  "ConvertTo-Json -InputObject @($a, $b, $c, $p) -Depth 3\n") % (self.task, self.wrapper)
        a, b, c, procs = json.loads(self.ps(script))
        # What D1 printed is what B1 compares.
        self.assertEqual(a['ActionFp'], printed['ACTION_FINGERPRINT'])
        self.assertEqual(a['TriggerFp'], printed['TRIGGER_FINGERPRINT'])
        self.assertEqual(a['XmlSha'], printed['TASK_XML_SHA256'])
        self.assertEqual(a['WrapperSha'], sha(self.wrapper))
        self.assertEqual((a['Path'], a['Enabled'], a['State']), ('\\', 'True', 'Ready'))
        # Disabled for the pilot: the definition is the same, the task cannot start.
        self.assertEqual((b['Enabled'], b['State']), ('False', 'Disabled'))
        self.assertEqual((b['ActionFp'], b['TriggerFp']), (a['ActionFp'], a['TriggerFp']))
        # Given back: exactly the definition that was saved, task XML included.
        self.assertEqual((c['Enabled'], c['State']), ('True', 'Ready'))
        for key in ('Path', 'ActionFp', 'TriggerFp', 'XmlSha', 'WrapperSha'):
            self.assertEqual(c[key], a[key], key)
        self.assertEqual(procs, 0)
        self.assertNotIn('Running', self.ps("(Get-ScheduledTask -TaskName '%s').State" % self.task))



@unittest.skipUnless(POWERSHELL and os.name == 'nt', 'needs Windows and CARD_PILOT_POWERSHELL')
class W0OnWindows(unittest.TestCase):
    """W0 with nothing replaced, on the Windows runner.

    [REASON]: no stand-in reproduces Task Scheduler objects, CIM, the
    registry, an admin share or the Task Scheduler COM service W0 reads on
    SRV-YOQSH. Here only constants change: SRV-YOQSH is this runner
    ('localhost'), its production data folder is a planted one, the scan
    depth is 2 so the runner's disks are walked quickly, and the login pages
    are a closed local port.
    """

    def setUp(self):
        self.task = 'CardPilotW0Daily%d' % os.getpid()
        self.top = 'C:\\CardPilotW0%d' % os.getpid()
        self.addCleanup(shutil.rmtree, self.top, True)
        self.tmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.src = os.path.join(self.top, 'src')
        dc = os.path.join(self.src, 'drone_collector')
        for name in ('main.py', 'config.py', '__init__.py'):
            write(os.path.join(dc, name), read(os.path.join(REPO_ROOT, 'drone_collector', name)))
        sh('git', 'init', '-q', '-b', 'main', cwd=self.src)
        sh('git', 'add', 'drone_collector', cwd=self.src)
        sh('git', '-c', 'user.name=t', '-c', 'user.email=t@t', 'commit', '-q', '-m', 'w0', cwd=self.src)
        self.session = os.path.join(self.top, 'session', 'storage_state.json')
        write(self.session, '{"cookies": [{"name": "sid", "value": "COOKIE-SECRET-2"}]}')
        write(os.path.join(dc, '.env'), 'VEHICLE_SOFT_BASE_URL=http://10.103.25.14:5051\nDRONE_API_TOKEN=tok-SECRET-1\n'
                                        'DJI_STORAGE_STATE=%s\n' % self.session)
        self.prod = os.path.join(self.top, 'prod', 'transport-report', 'drone_collector', 'data')
        os.makedirs(self.prod)
        shutil.copy2(self.session, os.path.join(self.prod, 'storage_state.json'))
        write(os.path.join(self.prod, 'collector.lock'), '')
        self.ps("$a = New-ScheduledTaskAction -Execute python.exe -Argument '-m drone_collector.main --sources --send-sources "
                "--token abcdefabcdefabcdefabcdefabcdef123456' -WorkingDirectory '%s'\n"
                "Register-ScheduledTask -TaskName '%s' -Action $a -Trigger (New-ScheduledTaskTrigger -Daily -At 6am) | Out-Null\n"
                % (self.src, self.task))
        self.addCleanup(self.ps, "Unregister-ScheduledTask -TaskName '%s' -Confirm:$false" % self.task)

    def ps(self, script):
        path = os.path.join(self.tmp, 'ps_%d.ps1' % len(os.listdir(self.tmp)))
        write(path, script)
        p = subprocess.run([POWERSHELL, '-NoProfile', '-NonInteractive', '-ExecutionPolicy', 'Bypass',
                            '-File', path], capture_output=True, text=True, timeout=600)
        self.assertEqual(p.returncode, 0, p.stdout + p.stderr)
        return p.stdout

    def test_real_machine(self):
        ws = Workstation(os.path.join(self.tmp, 'unused'))
        block = ws.block(server='localhost', serverData=self.prod, work=os.path.join(self.tmp, 'work'),
                         pilotDir=os.path.join(self.tmp, 'work', 'w1'), maxDepth=2, budgetSec=240,
                         stagingLogin='http://127.0.0.1:9/login', prodLogin='http://127.0.0.1:9/login')
        before = tree_state(self.top, os.path.join(self.tmp, 'none'))
        out = self.ps(block)
        self.assertEqual([l for l in out.splitlines() if l.strip()][-1],
                         'STEP=PASS (read only: nothing operational was changed)', out)
        for key in ('HOST_ROLE=workstation, not SRV-YOQSH', 'CHECKOUT 1 %s (collector only)' % self.src,
                    '  SUPPORTS sources=yes ids_file=yes send_sources=yes lock=yes outbox_setting=yes',
                    '  RECEIVER=staging :5051', 'TASK \\%s state=Ready enabled=True' % self.task,
                    '  TRIGGER 1 MSFT_TaskDailyTrigger', '--token [hidden]',
                    'PROD_SCHEDULE=read from localhost: ', 'PROD_TASK \\%s state=ready enabled=True' % self.task,
                    'STAGING_5051_LOGIN=ERROR', 'ATTENTION enabled scheduled task %s runs the collector' % self.task):
            self.assertIn(key, out)
        self.assertRegex(out, r'TASK \\%s state=Ready enabled=True [^\n]* runs_collector=yes' % self.task)
        if 'PROD_FILE storage_state.json bytes=' in out:
            # The admin share answered: the planted session is a byte copy with the same time.
            self.assertIn('  SESSION_VS_PRODUCTION=LIKELY_COPY of the production session', out)
            self.assertIn('PROD_LOCK=no owner hint: the production collector is not running now', out)
        else:
            self.assertIn('PROD_FILES=the production data folder is not readable from here', out)
        for secret in ('tok-SECRET-1', 'COOKIE-SECRET-2', 'abcdefabcdefabcdef'):
            self.assertNotIn(secret, out)
        self.assertEqual(tree_state(self.top, os.path.join(self.tmp, 'none')), before)
        self.assertIn('Ready', self.ps("(Get-ScheduledTask -TaskName '%s').State" % self.task))


W1_HARNESS = os.path.join(HERE, 'card_pilot_w1_harness.ps1')
FAKE_COLLECTOR = os.path.join(HERE, 'card_pilot_fake_collector.py')
W1_TOKEN = 'W1-TOKEN-SECRET-9c41'
MACHINE_TOKEN = 'MACHINE-TOKEN-SECRET-4b7e'
SITE_ENV = ['FLASK_ENV=sqlite_prod', 'PORT=5051', 'SECRET_KEY=do-not-print-0f9e', 'DRONE_API_TOKEN=' + W1_TOKEN]
PROD_NOW = '3434996a434652b0b590be2cfe08c4dc54cf1fab'
# Production after W1: v1.23 (docs/DEPLOYED.md, 08.10.2026); its DJI code is that of PROD.
PROD_V123 = '3c5c8c5688b6a1586ef868c0cb62d750e366f34b'
W1_SESSION_SECRET = 'W1-SESSION-COOKIE-SECRET-5d2e'
W1_SITE = 'http://10.103.25.14:5051'
# Present in the stand-in field catalog: a card with this key resolves EXACT.
CATALOG_KEY = 'c' * 32
W1_TOOLS = ('check_db_lock.py', 'check_migration_drift.py', 'dji_card_coverage_pilot.py',
            'dji_area_recalc.py', 'dji_field_census.py')
# Where '\\' is not a path separator, `tools\\x.py` run from the staging folder
# is a file of its own: it runs tools/x.py as itself.
TOOL_SHIM = ("import os, runpy, sys\n"
             "here = os.path.dirname(os.path.abspath(__file__))\n"
             "runpy.run_path(os.path.join(here, 'tools', %r), run_name='__main__')\n")


def pid_alive(pid):
    if os.name == 'nt':
        out = subprocess.run(['tasklist', '/FI', 'PID eq %d' % pid, '/NH'], capture_output=True, text=True).stdout
        return str(pid) in out.split()
    try:
        os.kill(pid, 0)
    except OSError:
        return False
    try:
        with open('/proc/%d/stat' % pid) as fh:
            return fh.read().split()[2] != 'Z'
    except OSError:
        return True


class W1Server(object):
    """SRV-YOQSH as W1 meets it: B1 done, the pilot checkout, production at rest.

    The pin is a stand-in: the real pin with drone_collector/main.py replaced
    by tests/card_pilot_fake_collector.py; config.py and runlock.py stay real.
    Staging and the pilot checkout are both on it, as on the server.
    """

    def __init__(self, root):
        os.makedirs(root)
        # [REASON]: the runner's temp folder may be an 8.3 short name; python
        # names the import folder by its long name, and so do the asserts here.
        root = self.root = os.path.realpath(root)
        self.staging = os.path.join(root, 'transport-report-staging')
        sh('git', 'clone', '-q', '--shared', REPO_ROOT, self.staging)
        sh('git', '-c', 'advice.detachedHead=false', 'checkout', '-q', PIN, cwd=self.staging)
        shutil.copyfile(FAKE_COLLECTOR, os.path.join(self.staging, 'drone_collector', 'main.py'))
        sh('git', '-c', 'user.name=t', '-c', 'user.email=t@t', 'commit', '-q', '-am',
           'stand-in collector', cwd=self.staging)
        self.pin = sh('git', 'rev-parse', 'HEAD', cwd=self.staging)
        sh('git', 'branch', '-q', 'stand-in-pin', cwd=self.staging)
        self.work = os.path.join(root, 'VehicleSoft_CardPilot')
        self.w1_root = os.path.join(self.work, 'w1')
        self.src = os.path.join(self.work, 'src')
        sh('git', 'clone', '-q', '--shared', '--branch', 'stand-in-pin', self.staging, self.src)
        sh('git', '-c', 'advice.detachedHead=false', 'checkout', '-q', '--detach', self.pin, cwd=self.src)
        # The collector venv has Playwright; here a package of that name will do.
        write(os.path.join(self.src, 'playwright', '__init__.py'), '# stand-in\n')
        if os.sep != '\\':
            for name in W1_TOOLS:
                write(os.path.join(self.staging, 'tools\\' + name), TOOL_SHIM % name)
        # B0: the frozen baseline, plan and fingerprint; the field catalog holds CATALOG_KEY.
        self.run_root = os.path.join(root, 'card_pilot')
        self.baseline = os.path.join(self.run_root, 'baseline_20261003_072956')
        self.plan_dir = os.path.join(self.baseline, 'plan')
        self.snapshot = os.path.join(self.baseline, 'snapshot',
                                     'transport_20261003_073000_card_pilot_baseline.db')
        os.makedirs(os.path.dirname(self.snapshot))
        self.build(self.snapshot)
        tool = os.path.join(self.staging, 'tools', 'dji_card_coverage_pilot.py')
        sh(sys.executable, tool, 'plan', '--db', self.snapshot, '--out-dir', self.plan_dir)
        sh(sys.executable, tool, 'fingerprint', '--db', self.snapshot, '--out',
           os.path.join(self.plan_dir, 'fingerprint_before.json'))
        self.plan = json.loads(read(os.path.join(self.plan_dir, 'plan.json')))
        self.canary_file = os.path.join(self.plan_dir, 'canary_ids.txt')
        self.ids = sorted(int(l.split('#')[0]) for l in read(self.canary_file).splitlines()
                          if l.split('#')[0].strip())
        assert len(self.ids) == 50
        # B1: staging is the B0 copy, its run is open.
        self.db = os.path.join(self.staging, 'instance', 'transport.db')
        os.makedirs(os.path.dirname(self.db))
        shutil.copyfile(self.snapshot, self.db)
        write(os.path.join(self.run_root, 'staging_20261002_101500', 'returned.txt'), 'returned\n')
        self.b1_run = os.path.join(self.run_root, 'staging_20261003_160830')
        write(os.path.join(self.b1_run, 'swapped.txt'), 'swapped\n')
        # Production: its checkout, database, DJI session, shared lock, log, outbox.
        self.prod = os.path.join(root, 'transport-report')
        sh('git', 'clone', '-q', '--shared', REPO_ROOT, self.prod)
        sh('git', '-c', 'advice.detachedHead=false', 'checkout', '-q', PROD_NOW, cwd=self.prod)
        self.prod_db = os.path.join(self.prod, 'instance', 'transport.db')
        os.makedirs(os.path.dirname(self.prod_db))
        self.build(self.prod_db)
        data = os.path.join(self.prod, 'drone_collector', 'data')
        self.session = os.path.join(data, 'storage_state.json')
        write(self.session, '{"cookies": [{"name": "sid", "value": "%s"}], "origins": []}' % W1_SESSION_SECRET)
        self.lock = os.path.join(data, 'collector.lock')
        write(self.lock, '')
        self.prod_outbox = os.path.join(data, 'outbox')
        write(os.path.join(self.prod_outbox, 'sent', 'source_1_card.json'), '{}')
        self.prod_log = os.path.join(self.prod, 'drone_collector', 'logs', 'collector.log')
        write(self.prod_log, '2026-10-08 06:00:01 INFO collector: RUN SUMMARY mode=daily exit=0\n')
        self.browsers = os.path.join(root, 'ms-playwright')
        os.makedirs(os.path.join(self.browsers, 'chromium-1181'))
        self.record = os.path.join(root, 'collector_record.json')
        self.fake = os.path.join(root, 'collector_scenario.json')

    @staticmethod
    def build(path):
        build_db(path, no_card=60)
        con = sqlite3.connect(path)
        con.executemany('INSERT INTO schema_migrations (name, applied_at) VALUES (?, ?)',
                        [('SYNTH_%02d' % i, '2026-09-01') for i in range(59)])
        s = Seed(con)
        snap = s.snapshot(datetime(2026, 8, 1, 5, 0))
        s.revision('cccccccc-0000-4000-8000-0000000000cc', CATALOG_KEY, 'SYNTHETIC FIELD', snap, snap)
        s.geometry(CATALOG_KEY)
        con.commit()
        con.close()

    def state(self):
        """What W1 must not change in production, and the pristine pieces of B0."""
        return {'prod_db': sha(self.prod_db), 'session': (sha(self.session), os.stat(self.session).st_mtime_ns),
                'prod_log': sha(self.prod_log), 'prod_outbox': tree_state(self.prod_outbox, self.root + '-none'),
                'plan': {f: sha(os.path.join(self.plan_dir, f)) for f in sorted(os.listdir(self.plan_dir))},
                'snapshot': sha(self.snapshot),
                'prod_head': sh('git', 'rev-parse', 'HEAD', cwd=self.prod)}

    def tasks(self, **changes):
        if os.name != 'nt':
            from zoneinfo import ZoneInfo
            now = datetime.now(ZoneInfo(W1_TZ)).replace(tzinfo=None)
        else:
            now = datetime.now()

        def at(hours):
            return (now + timedelta(hours=hours)).strftime('%Y-%m-%d %H:%M:%S')
        prod_py = 'C:\\transport-report\\drone_collector\\.venv\\Scripts\\python.exe'
        tasks = {
            'DroneCollectorDaily': {'State': 'Ready', 'NextRunTime': at(10), 'Execute': prod_py,
                                    'Arguments': '-m drone_collector.main', 'WorkingDirectory': 'C:\\transport-report'},
            'DroneAreaDaily': {'State': 'Ready', 'NextRunTime': at(11.5), 'Execute': 'powershell.exe',
                               'Arguments': '-File C:\\ProgramData\\VehicleSoft\\area_daily.ps1 dji_area_daily'},
            'DjiAreaRefresh': {'State': 'Ready', 'Execute': 'powershell.exe',
                               'Arguments': '-File C:\\ProgramData\\VehicleSoft\\dji_area_refresh.ps1'},
            ISO: {'State': 'Disabled', 'Execute': 'powershell.exe', 'Arguments': ISO_ARGS},
            'TransportDBBackupStaging': {'State': 'Ready', 'NextRunTime': at(3), 'Execute': 'python.exe',
                                         'Arguments': 'C:\\transport-report-staging\\tools\\backup_transport_db.py'},
            'GoogleUpdateTaskMachineUA': {'State': 'Ready', 'NextRunTime': at(1),
                                          'Execute': 'C:\\Program Files (x86)\\Google\\Update\\GoogleUpdate.exe'},
        }
        for name, change in changes.items():
            if change is None:
                tasks.pop(name)
            else:
                tasks.setdefault(name, {}).update(change)
        out = []
        for name, t in tasks.items():
            t = dict(t, TaskName=name)
            if isinstance(t.get('NextRunTime'), (int, float)):
                t['NextRunTime'] = at(t['NextRunTime'])
            out.append(t)
        out.append({'TaskName': 'ScheduledDefrag', 'TaskPath': '\\Microsoft\\Windows\\Defrag\\', 'State': 'Ready',
                    'Execute': 'drone_collector_defrag.exe'})
        return out

    def scenario(self, tasks=None, **changes):
        value = {
            'Host': 'srv-yoqsh',
            'Token': W1_TOKEN,
            'Services': {
                'TransportReport': {'Status': 'Running', 'StartType': 'Automatic'},
                'TransportBot': {'Status': 'Running', 'StartType': 'Automatic'},
                'TransportBot003': {'Status': 'Running', 'StartType': 'Automatic'},
                'TransportReportStaging': {'Status': 'Running', 'StartType': 'Automatic'},
                'TransportBotStaging': {'Status': 'Stopped', 'StartType': 'Disabled'},
                'TransportBot003Staging': {'Status': 'Stopped', 'StartType': 'Disabled'},
            },
            'Tasks': self.tasks(**(tasks or {})),
            'Processes': [{'ProcessId': 4245, 'Name': 'explorer.exe', 'CommandLine': 'C:\\Windows\\explorer.exe'}],
            'Registry': {
                # The machine token is production's: staging refuses it (W1, 08.10.2026).
                MACHINE_KEY: {'Path': 'C:\\Windows', 'DRONE_API_TOKEN': MACHINE_TOKEN},
                SITE_KEY: {'AppEnvironmentExtra': SITE_ENV},
            },
            'Web': {W1_SITE + '/login': {'Status': 200, 'Body': LOGIN}},
        }
        value.update(changes)
        return value

    def collector(self, **changes):
        value = {'record': self.record, 'staging_db': self.db, 'token': W1_TOKEN}
        value.update(changes)
        return value

    def block(self, **override):
        text = blocks()['W1']
        values = {'src': self.src, 'cpy': sys.executable, 'python': sys.executable, 'root': self.staging,
                  'db': self.db, 'prodRoot': self.prod, 'prodDb': self.prod_db, 'session': self.session,
                  'lock': self.lock, 'prodLog': self.prod_log, 'pin': self.pin, 'runRoot': self.run_root,
                  'baseline': self.baseline, 'snapshot': self.snapshot,
                  'canarySha': self.plan['sample']['canary_ids_sha256'],
                  'work': self.work, 'w1Root': self.w1_root}
        values.update(override)
        for name, value in values.items():
            if isinstance(value, int):
                text, n = re.subn(r'^(  \$%s\s*= )\d+$' % name, r'\g<1>%d' % value, text, flags=re.M)
            else:
                text, n = re.subn(r"^(  \$%s\s*= )'[^']*'$" % name, lambda m: m.group(1) + "'" + value + "'",
                                  text, flags=re.M)
            assert n == 1, name
        return text

    def runs(self):
        if not os.path.isdir(self.w1_root):
            return []
        return sorted(os.path.join(self.w1_root, d) for d in os.listdir(self.w1_root))

    def collector_runs(self):
        path = self.record + '.runs'
        return [json.loads(l) for l in read(path).splitlines()] if os.path.exists(path) else []

    def rows(self, table, outside=True):
        """Rows of `table` for flights outside (or inside) the canary."""
        con = sqlite3.connect(self.db)
        try:
            cols = [r[1] for r in con.execute('PRAGMA table_info(%s)' % table)]
            key = 'dji_flight_id' if 'dji_flight_id' in cols else 'flight_id'
            marks = ','.join('?' * len(self.ids))
            return sorted(con.execute('SELECT * FROM %s WHERE %s %s IN (%s)'
                                      % (table, key, 'NOT' if outside else '', marks), self.ids).fetchall(),
                          key=repr)
        finally:
            con.close()


def run_server_block(srv, stem, text, sc, collector):
    """Run one block against the stand-in SRV-YOQSH; (output, calls, final services)."""
    bf, sf, cf = stem + '.ps1', stem + '.scenario.json', stem + '.calls.txt'
    write(bf, text)
    write(sf, json.dumps(sc))
    write(srv.fake, json.dumps(collector))
    if os.path.exists(srv.record):
        os.remove(srv.record)
    env = {k: v for k, v in os.environ.items()
           if not re.match(r'(?i)(DJI_|DRONE_|VEHICLE_SOFT_|PLAYWRIGHT_|PYTHON|HTTPS?_PROXY$|NO_PROXY$)', k)}
    # The owner's console holds other values; only the collector gets the pilot ones.
    env.update({'CARD_PILOT_FAKE_COLLECTOR': srv.fake,
                'VEHICLE_SOFT_BASE_URL': 'http://10.103.25.14:5050',
                'DRONE_OUTBOX_DIR': srv.prod_outbox,
                'DJI_COLLECTOR_LOCK_WAIT_S': '1800',
                'DJI_STORAGE_STATE': os.path.join(os.path.dirname(stem), 'other_session.json'),
                'DJI_HEADLESS': 'false',
                'DRONE_API_TOKEN': 'CONSOLE-TOKEN-DECOY-3',
                # Left in the child, these two would import production's drone_collector.
                'PYTHONSAFEPATH': '1', 'PYTHONPATH': srv.prod,
                'PLAYWRIGHT_BROWSERS_PATH': srv.browsers})
    if os.name != 'nt':
        env['TZ'] = W1_TZ
    p = subprocess.run([POWERSHELL, '-NoProfile', '-NonInteractive', '-ExecutionPolicy', 'Bypass',
                        '-File', W1_HARNESS, '-BlockFile', bf, '-ScenarioFile', sf, '-CallsFile', cf],
                       capture_output=True, text=True, timeout=900, env=env)
    out = p.stdout + p.stderr
    return out, [l for l in read(cf).splitlines() if l], json.loads(read(cf + '.services.json'))


def check_server_calls(test, calls, out):
    """What a W1 or W2 block may do to the server: stop and start staging, read the rest."""
    test.assertEqual([c for c in calls if c.startswith('CONSOLE_ENV_CHANGED')], [], out)
    test.assertEqual([c for c in calls if 'ScheduledTask' in c or 'ItemProperty' in c
                      or c.startswith(('Set-Service', 'Restart-Service'))], [])
    test.assertEqual([c for c in calls if re.match(r'(Stop|Start)-Service ', c)
                      and c.split()[1] != 'TransportReportStaging'], [])
    test.assertEqual([c for c in calls if c.startswith('WEB ') and ':5050' in c], [])


def check_no_secret(test, out, work, runs):
    """No token, cookie or secret in the output, the block logs or any file of the runs."""
    texts = [out] + [read(os.path.join(work, f)) for f in os.listdir(work) if f.endswith('.log')]
    for run in runs:
        for base, _, files in os.walk(run):
            texts += [read(os.path.join(base, f)) for f in files]
    for secret in W1_SECRETS:
        for text in texts:
            test.assertNotIn(secret, text)


NEW_EVIDENCE = 'staging holds new evidence that is not this run of the 50 canary flights'
PROD_EVIDENCE = 'the production database received canary evidence'
# [REASON]: SRV-YOQSH runs at UTC+5 and the receiver stamps UTC; on a UTC
# runner a missing ToUniversalTime() would pass unseen. Where the stand-in
# can set it (pwsh on Linux honours TZ), the block runs at UTC+5.
W1_TZ = 'Asia/Tashkent'
# What the pinned collector writes when the page asked for no descriptor (sources.py).
DESCRIPTOR_LINE = 'Flight %d: the page asked for no descriptor; the direct request answered HTTP %d (%d bytes)'
W1_SECRETS = (W1_TOKEN, W1_SESSION_SECRET, 'do-not-print', 'CONSOLE-TOKEN-DECOY-3', MACHINE_TOKEN)
W1_TABLES = ('drone_flights', 'dji_field_attributions', 'dji_area_calculations', 'dji_flight_evidence',
             'dji_source_revisions')


@unittest.skipUnless(POWERSHELL, 'CARD_PILOT_POWERSHELL is not set')
class W1InPowerShell(unittest.TestCase):
    """W1+S1 as printed in the document, against a stand-in SRV-YOQSH.

    [REASON]: W1 is the first block that talks to DJI and writes to staging
    from a collector. What must hold: it collects the frozen 50 and nothing
    else, from the pilot checkout, into staging only, with the pilot
    settings given to the collector process alone; any gate that fails
    stops it before DJI or before the recalculation; the recalculation
    touches the 50; production and its session are only read; no second
    collector run starts (W2 is the owner's decision).
    Real here: git, python, SQLite, the pilot tool, the recalculation, the
    census, the collector's config.py and runlock.py, the child process and
    its environment. Stand-ins: the services, Task Scheduler, the registry,
    CIM, the web, and the collector's work (tests/card_pilot_fake_collector.py).
    """

    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.srv = W1Server(os.path.join(self.tmp, 'srv'))
        self.n = 0

    def run_w1(self, sc=None, collector=None, **override):
        srv = self.srv
        self.n += 1
        sc = sc or srv.scenario()
        out, self.calls, self.services = run_server_block(
            srv, os.path.join(self.tmp, 'w1_%d' % self.n), srv.block(**override), sc, collector or srv.collector())
        self.assertNotIn('BLOCK THREW', out, out)
        self.record = json.loads(read(srv.record)) if os.path.exists(srv.record) else None
        check_server_calls(self, self.calls, out)
        # Every service ends as it began (S1 stops and starts staging only).
        self.assertEqual(self.services, json.loads(json.dumps(sc['Services'])), out)
        log = line_with(out, 'LOG FILE: ')[len('LOG FILE: '):]
        self.assertEqual(os.path.dirname(log), srv.work)
        self.assertRegex(os.path.basename(log), r'^card_pilot_w1_\d{8}_\d{6}\.log$')
        self.assertIn('== 1. Checks before DJI', read(log))
        check_no_secret(self, out, srv.work, srv.runs())
        self.assertEqual(len([l for l in out.splitlines() if l.startswith('DECISION=')]), 1, out)
        return out

    def assertStopBeforeDji(self, out, message, before):
        self.assertIn('STEP=STOP - STEP FAILED: ' + message, out)
        self.assertIn('DECISION=STOP', out)
        self.assertNotIn('== 2.', out)
        self.assertEqual(self.srv.collector_runs(), [])
        self.assertEqual(sha(self.srv.db), sha(self.srv.snapshot))
        self.assertEqual(self.srv.state(), before)
        self.assertEqual([c for c in self.calls if c.startswith(('Stop-Service', 'Start-Service'))], [])

    def assertStopBeforeRecalc(self, out, message, before):
        self.assertIn('STEP=STOP - STEP FAILED: ' + message, out)
        self.assertIn('DECISION=STOP', out)
        self.assertNotIn('COLLECTOR_GATE=PASS', out)
        self.assertNotIn('RECALC_', out)
        self.assertEqual([c for c in self.calls if c.startswith(('Stop-Service', 'Start-Service'))], [])
        self.assertEqual(len(self.srv.collector_runs()), 1)
        self.assertEqual(self.srv.state(), before)
        for table in ('dji_field_attributions', 'dji_area_calculations'):
            self.assertEqual(self.srv.rows(table, outside=False), self.pristine[table], table)
        self.assertEqual(len(self.srv.runs()), 1)
        self.assertIn('RUN=' + self.srv.runs()[0], out)

    def pristine_rows(self):
        self.pristine = {t: self.srv.rows(t, outside=False) for t in W1_TABLES}
        self.outside = {t: self.srv.rows(t) for t in W1_TABLES}

    def lock_is_free(self):
        """A run this block stopped holds the shared lock no longer."""
        return subprocess.run(
            [sys.executable, '-c', 'import sys; sys.path.insert(0, sys.argv[1]); '
             'from drone_collector import runlock; l = runlock.RunLock(sys.argv[2]); '
             'sys.exit(0 if l.acquire(wait_s=10) else 1)', self.srv.src, self.srv.lock]).returncode == 0

    def reset(self):
        """Back to the server as it was before a collection (between subtests)."""
        srv = self.srv
        shutil.copyfile(srv.snapshot, srv.db)
        for p in (srv.db + '-wal', srv.db + '-shm', srv.lock + '.owner', srv.record + '.runs'):
            if os.path.exists(p):
                os.remove(p)
        shutil.rmtree(srv.w1_root, True)
        shutil.rmtree(os.path.join(srv.src, 'drone_collector', 'logs'), True)

    def test_pass_go(self):
        srv = self.srv
        self.pristine_rows()
        before = srv.state()
        keys = {str(fid): CATALOG_KEY for fid in srv.ids[:20]}
        # A body of 429 bytes is not HTTP 429 (the descriptor line and every "captured" line).
        out = self.run_w1(collector=srv.collector(card_keys=keys, log_after={'2': [DESCRIPTOR_LINE % (srv.ids[1], 200, 429)]}))
        self.assertEqual(out.splitlines()[-1], 'STEP=PASS', out)
        self.assertIn(DESCRIPTOR_LINE % (srv.ids[1], 200, 429), out)
        for line in ('CANARY_COUNT=50', 'CANARY_SHA256=' + srv.plan['sample']['canary_ids_sha256'],
                     'COLLECTOR_GATE=PASS', 'DECISION=GO_TO_500', 'REGISTERED=60', 'FINGERPRINT_PRE=equals B0',
                     'RECEIVER=' + W1_SITE + '/drones/api/source_sync', 'LOCK=' + srv.lock,
                     'PYTHON=' + sys.executable, 'SESSION_AFTER=unchanged', 'PROD_LOCK_OWNER_AFTER=none',
                     'RUN_LOGGED_IN pilot_checkout=True production_checkout=False',
                     'PROD_DB_AFTER run_rows=0 canary_sources_since=0 canary_evidence_since=0',
                     'STAGING_NEW_EVIDENCE revisions=50 flights=50 outside_canary=0 other_run=0 repeated_type=0',
                     'RECALC_DRY-RUN flights_in_period=50', 'RECALC_APPLY flights_in_period=50 calc_writes=50 field_writes=50',
                     'ATTEMPTED=50 VISITED=50 CARDS_CAPTURED=50 FAILED=0 NOT_VISITED=0 FETCH_SUCCESS=100.0%',
                     'OUTCOME EXACT=20 IDENTIFIED=0 CONFIRMED=20 NO_KEY=0 NOT_IN_CATALOG=30 NO_CARD=0',
                     'CONFIRMED_RATE among_fetched=40.0% wilson95=', 'PROJECTION (not a fact', 'BY_UNIT ', 'BY_WEEK ',
                     'CASES EXACT: ', 'STAGING_AFTER HEAD=' + srv.pin, 'PROD_AFTER HEAD=' + PROD_NOW, 'PROD_DJI_CODE_AFTER=unchanged since ' + PROD):
            self.assertIn(line, out)
        for name in ('PROD_TASK_BEFORE', 'PROD_TASK_LAUNCH'):
            for task in ('DroneCollectorDaily', 'DroneAreaDaily', 'DjiAreaRefresh'):
                self.assertRegex(out, r'%s %s state=Ready next=' % (name, task))
        self.assertNotIn('STOP_MARKERS={"', out.replace('STOP_MARKERS={}', ''))
        # Exactly one collector run: the frozen 50, the normal command, from the pilot checkout.
        run = srv.runs()
        self.assertEqual(len(run), 1)
        run = run[0]
        ids_copy = os.path.join(run, 'canary_ids.txt')
        self.assertEqual(srv.collector_runs(), [['--sources', '--ids-file', ids_copy, '--send-sources']])
        self.assertEqual(sha(ids_copy), sha(srv.canary_file))
        rec = self.record
        self.assertTrue(rec['finished'])
        self.assertTrue(os.path.samefile(rec['cwd'], srv.src))
        self.assertTrue(os.path.samefile(rec['package_root'], os.path.join(srv.src, 'drone_collector')))
        # The pilot settings reached the collector process, and only it.
        self.assertEqual(rec['env'], {
            'VEHICLE_SOFT_BASE_URL': W1_SITE, 'DJI_STORAGE_STATE': srv.session, 'DJI_COLLECTOR_LOCK_PATH': srv.lock,
            'DJI_COLLECTOR_LOCK_WAIT_S': '0', 'DRONE_OUTBOX_DIR': os.path.join(run, 'outbox'),
            'DJI_HEADLESS': 'true', 'PYTHONPATH': None, 'PYTHONHOME': None, 'PYTHONSAFEPATH': None,
            'PYTHONIOENCODING': 'utf-8'})
        self.assertTrue(rec['token_matches'])
        self.assertEqual(len(os.listdir(os.path.join(run, 'outbox', 'sent'))), 50)
        for name in ('collector_stdout.log', 'collector_stats.json', 'fingerprint_pre.json', 'fingerprint_post.json',
                     'recalc_apply.json', 'census_after.json', 'w1_check.py'):
            self.assertTrue(os.path.exists(os.path.join(run, name)), name)
        self.assertTrue(os.path.exists(os.path.join(run, 'measure', 'measure_canary.json')))
        # Production only read: database, session, log, outbox, checkout; lock free.
        self.assertEqual(srv.state(), before)
        self.assertFalse(os.path.exists(srv.lock + '.owner'))
        # Staging: the 50 recalculated, every other flight as it was.
        for table in W1_TABLES:
            self.assertEqual(srv.rows(table), self.outside[table], table)
        for table in ('dji_field_attributions', 'dji_area_calculations'):
            self.assertNotEqual(srv.rows(table, outside=False), self.pristine[table], table)
        self.assertEqual([c for c in self.calls if re.match(r'(Stop|Start)-Service', c)],
                         ['Stop-Service TransportReportStaging', 'Start-Service TransportReportStaging'])
        web = [c for c in self.calls if c.startswith('WEB ')]
        self.assertEqual(web, ['WEB Get %s/login' % W1_SITE, 'WEB Post %s/drones/api/land_geometry_manifest' % W1_SITE,
                               'WEB Get %s/login' % W1_SITE])
        self.assertIn('CHILD_ENV_INHERITED=PLAYWRIGHT_BROWSERS_PATH (names only)', out)
        self.assertIn('BROWSER=' + os.path.join(srv.browsers, 'chromium-1181'), out)

    def test_simplify_when_cards_do_not_confirm(self):
        # Two refused V4 downloads (V4_FAILED) are a result, not a refusal of the canary.
        out = self.run_w1(collector=self.srv.collector(rejected=2, v4_failed=2))
        self.assertEqual(out.splitlines()[-1], 'STEP=PASS', out)
        self.assertIn('OUTCOME EXACT=0 IDENTIFIED=0 CONFIRMED=0 NO_KEY=0 NOT_IN_CATALOG=50', out)
        self.assertIn('DECISION=SIMPLIFY', out)

    def test_low_fetch_is_a_stop_decision_after_passed_gates(self):
        srv = self.srv
        # Every other record page gives nothing: never five in a row, no refused card, 25 of 50 fetched.
        status = {str(fid): 'nothing' for fid in srv.ids[1::2]}
        out = self.run_w1(collector=srv.collector(status=status, exit=18))
        self.assertEqual(out.splitlines()[-1], 'STEP=PASS', out)
        self.assertIn('CARDS_CAPTURED=25 FAILED=25', out)
        self.assertIn('DECISION=STOP', out)
        self.assertIn('DECISION_REASON=cards came for 25 of 50 flights, fewer than 40, so ', out)

    def test_refusals_before_dji(self):
        srv = self.srv
        before = srv.state()
        # [REASON]: frozen files are saved and put back as bytes: a text-mode
        # write on Windows turns LF into CRLF and changes their sha256.
        plan_json = read_bytes(os.path.join(srv.plan_dir, 'plan.json'))
        canary_text = read_bytes(srv.canary_file)
        other_sha = '0' * 64
        cases = [
            ('host', dict(sc=srv.scenario(Host='bak-tex11')), 'host is bak-tex11'),
            ('canary sha', dict(canarySha=other_sha), 'canary_ids.txt has sha256 '),
            ('site 5050', dict(site='http://10.103.25.14:5050'), 'the receiver http://10.103.25.14:5050 is the production port 5050 -- refused'),
            ('site other', dict(site='http://10.103.25.14:8080'), 'the receiver http://10.103.25.14:8080 is not the staging port 5051'),
            ('pilot pin', dict(pin=PIN), 'the pilot checkout '),
            ('no staging token', dict(sc=srv.scenario(Registry={MACHINE_KEY: {'DRONE_API_TOKEN': W1_TOKEN},
                                                               SITE_KEY: {'AppEnvironmentExtra': ['PORT=5051']}})),
             'the staging service environment holds 0 DRONE_API_TOKEN entries, expected exactly one'),
            ('two staging tokens', dict(sc=srv.scenario(Registry={MACHINE_KEY: {'DRONE_API_TOKEN': MACHINE_TOKEN},
                                                                 SITE_KEY: {'AppEnvironmentExtra': SITE_ENV + ['DRONE_API_TOKEN=x']}})),
             'the staging service environment holds 2 DRONE_API_TOKEN entries, expected exactly one'),
            ('token refused', dict(sc=srv.scenario(Token='another')), 'staging refused the DRONE_API_TOKEN of its own service'),
            ('production head', dict(prodExpected=STAGING_HEAD), 'production is not as expected (BEFORE)'),
            ('production service', dict(sc=srv.scenario(Services=dict(srv.scenario()['Services'], TransportBot={'Status': 'Stopped', 'StartType': 'Automatic'}))),
             'production is not as expected (BEFORE)'),
            ('task running', dict(sc=srv.scenario(tasks={'DroneAreaDaily': {'State': 'Running'}})),
             'production task DroneAreaDaily is running now'),
            ('window', dict(sc=srv.scenario(tasks={'DroneCollectorDaily': {'NextRunTime': 1.5}})),
             'production task DroneCollectorDaily starts in '),
            ('task missing', dict(sc=srv.scenario(tasks={'DjiAreaRefresh': None})), '0 scheduled tasks named DjiAreaRefresh'),
            ('collector process', dict(sc=srv.scenario(Processes=[{'ProcessId': 5150, 'Name': 'python.exe',
                                                                  'CommandLine': 'python.exe tools\\dji_area_daily.py --apply'}])),
             '1 collector or cycle process(es) are running (pid 5150)'),
            ('staging bot running', dict(sc=srv.scenario(Services=dict(srv.scenario()['Services'], TransportBot003Staging={'Status': 'Running', 'StartType': 'Disabled'}))),
             'TransportBot003Staging is Running Disabled'),
            ('staging bot', dict(sc=srv.scenario(Services=dict(srv.scenario()['Services'], TransportBotStaging={'Status': 'Stopped', 'StartType': 'Manual'}))),
             'TransportBotStaging is Stopped Manual'),
            ('staging site', dict(sc=srv.scenario(Services=dict(srv.scenario()['Services'], TransportReportStaging={'Status': 'Stopped', 'StartType': 'Automatic'}))),
             'TransportReportStaging is Stopped (BEFORE)'),
            ('iso task', dict(sc=srv.scenario(tasks={ISO: {'State': 'Ready'}})), 'DjiAreaRefreshStaging is not Disabled'),
            ('launcher', dict(sc=srv.scenario(Registry={MACHINE_KEY: {'DRONE_API_TOKEN': MACHINE_TOKEN},
                                                       SITE_KEY: {'AppEnvironmentExtra': SITE_ENV + ['DJI_REFRESH_LAUNCHER=subprocess']}})),
             'DJI_REFRESH_LAUNCHER is back'),
            ('login', dict(sc=srv.scenario(Web={W1_SITE + '/login': {'Status': 500, 'Body': 'error'}})),
             'the staging login page did not answer 200'),
            ('other task', dict(sc=srv.scenario(tasks={'CardPilotHoldout': {'State': 'Ready', 'Execute': 'python.exe',
                                                                           'Arguments': '-m drone_collector.main --sources'}})),
             'enabled scheduled task(s) that may collect or write staging: CardPilotHoldout'),
        ]
        for name, kw, message in cases:
            with self.subTest(name):
                out = self.run_w1(**kw)
                self.assertStopBeforeDji(out, message, before)
        with self.subTest('lock held by a running collector'):
            write(srv.lock + '.owner', json.dumps({'pid': os.getpid(), 'host': 'srv-yoqsh', 'purpose': 'daily'}))
            out = self.run_w1()
            os.remove(srv.lock + '.owner')
            self.assertStopBeforeDji(out, 'the production collector lock is held by running pid %d purpose daily' % os.getpid(), before)
        with self.subTest('49 ids'):
            ids = [l for l in canary_text.splitlines() if l.split(b'#')[0].strip()]
            write(srv.canary_file, canary_text.replace(ids[-1] + b'\n', b''), 'wb')
            new = sha(srv.canary_file)
            write(os.path.join(srv.plan_dir, 'plan.json'),
                  plan_json.replace(srv.plan['sample']['canary_ids_sha256'].encode('ascii'), new.encode('ascii')), 'wb')
            out = self.run_w1(canarySha=new)
            write(srv.canary_file, canary_text, 'wb')
            write(os.path.join(srv.plan_dir, 'plan.json'), plan_json, 'wb')
            self.assertStopBeforeDji(out, 'canary_ids.txt holds 49 ids, the frozen canary is 50 unique ids', before)
        with self.subTest('canary file changed, plan.json not'):
            ids = [l for l in canary_text.splitlines() if l.split(b'#')[0].strip()]
            write(srv.canary_file, canary_text.replace(ids[-1] + b'\n', b'810004\n'), 'wb')
            out = self.run_w1()
            write(srv.canary_file, canary_text, 'wb')
            self.assertStopBeforeDji(out, 'canary_ids.txt has sha256 ', before)
        with self.subTest('staging off the pin'):
            sh('git', '-c', 'advice.detachedHead=false', 'checkout', '-q', '--detach', PIN, cwd=srv.staging)
            out = self.run_w1()
            sh('git', '-c', 'advice.detachedHead=false', 'checkout', '-q', '--detach', srv.pin, cwd=srv.staging)
            self.assertStopBeforeDji(out, 'staging is not on the clean pilot revision (BEFORE)', before)
            self.assertIn('STAGING_BEFORE HEAD=%s tracked_changes=0' % PIN, out)
        with self.subTest('staging edited'):
            path = os.path.join(srv.staging, 'dji_area', 'pipeline.py')
            text = read(path)
            write(path, text + '\n# local change\n')
            out = self.run_w1()
            sh('git', 'checkout', '-q', '--', 'dji_area/pipeline.py', cwd=srv.staging)
            self.assertStopBeforeDji(out, 'staging is not on the clean pilot revision (BEFORE)', before)
            self.assertIn('STAGING_BEFORE HEAD=%s tracked_changes=1' % srv.pin, out)
        with self.subTest('production DJI code edited'):
            path = os.path.join(srv.prod, 'drones.py')
            text = read_bytes(path)
            write(path, text + b'\n# local change\n', 'wb')
            out = self.run_w1()
            write(path, text, 'wb')
            self.assertStopBeforeDji(out, 'the production DJI code (drone_collector, dji_area, drones.py) differs from %s '
                                     '(diff exit 0, 1 local change(s))' % PROD, before)
        with self.subTest('production DJI code committed'):
            path = os.path.join(srv.prod, 'dji_area', 'pipeline.py')
            text = read_bytes(path)
            write(path, text + b'\n# new release\n', 'wb')
            sh('git', '-c', 'user.name=t', '-c', 'user.email=t@t', 'commit', '-q', '-am', 'release', cwd=srv.prod)
            head = sh('git', 'rev-parse', 'HEAD', cwd=srv.prod)
            out = self.run_w1(prodExpected=head)
            sh('git', '-c', 'advice.detachedHead=false', 'checkout', '-q', '--detach', PROD_NOW, cwd=srv.prod)
            self.assertStopBeforeDji(out, 'the production DJI code (drone_collector, dji_area, drones.py) differs from %s '
                                     '(diff exit 1, 0 local change(s))' % PROD, before)
        with self.subTest('pilot checkout changed'):
            path = os.path.join(srv.src, 'drone_collector', 'config.py')
            text = read(path)
            write(path, text + '\n# local change\n')
            out = self.run_w1()
            sh('git', 'checkout', '-q', '--', 'drone_collector/config.py', cwd=srv.src)
            self.assertStopBeforeDji(out, 'the pilot checkout %s is at %s with 1 tracked change(s)' % (srv.src, srv.pin), before)
        with self.subTest('pilot .env'):
            env_file = os.path.join(srv.src, 'drone_collector', '.env')
            write(env_file, 'VEHICLE_SOFT_BASE_URL=http://10.103.25.14:5050\n')
            out = self.run_w1()
            os.remove(env_file)
            self.assertStopBeforeDji(out, 'the pilot checkout has a drone_collector\\.env', before)
        with self.subTest('no Playwright'):
            shutil.move(os.path.join(srv.src, 'playwright'), os.path.join(self.tmp, 'playwright'))
            out = self.run_w1()
            shutil.move(os.path.join(self.tmp, 'playwright'), os.path.join(srv.src, 'playwright'))
            self.assertStopBeforeDji(out, 'the collector python could not import the pilot collector and Playwright', before)
        with self.subTest('B1 run returned'):
            write(os.path.join(srv.b1_run, 'returned.txt'), 'returned\n')
            out = self.run_w1()
            os.remove(os.path.join(srv.b1_run, 'returned.txt'))
            self.assertStopBeforeDji(out, 'expected exactly one open B1 run with swapped.txt', before)
        with self.subTest('staging fingerprint drift'):
            con = sqlite3.connect(srv.db)
            con.execute('UPDATE drone_flights SET area_ha = area_ha + 1 WHERE dji_flight_id = ?', (srv.ids[0],))
            con.commit()
            con.close()
            out = self.run_w1()
            self.assertIn('STEP=STOP - STEP FAILED: the staging database no longer equals the B0 fingerprint', out)
            self.assertEqual(srv.collector_runs(), [])
            self.assertNotIn('== 2.', out)
            self.reset()
        with self.subTest('staging catalog drift'):
            con = sqlite3.connect(srv.db)
            Seed(con).geometry('d' * 32)
            con.commit()
            con.close()
            out = self.run_w1()
            self.assertIn('STEP=STOP - STEP FAILED: the staging field catalog differs from the B0 copy', out)
            self.assertEqual(srv.collector_runs(), [])
            self.reset()

    def test_lock_busy_at_launch_is_exit_24_and_no_recalc(self):
        srv = self.srv
        self.pristine_rows()
        before = srv.state()
        holder = subprocess.Popen(
            [sys.executable, '-c', 'import os, sys, time; sys.path.insert(0, sys.argv[1]); '
             'from drone_collector import runlock; l = runlock.RunLock(sys.argv[2], purpose="daily"); '
             'assert l.acquire(wait_s=0); os.remove(sys.argv[2] + ".owner"); print("held", flush=True); time.sleep(600)',
             srv.src, srv.lock], stdout=subprocess.PIPE, text=True)
        self.addCleanup(holder.stdout.close)
        self.addCleanup(holder.wait)
        self.addCleanup(holder.kill)
        self.assertEqual(holder.stdout.readline().strip(), 'held')
        out = self.run_w1()
        self.assertStopBeforeRecalc(out, 'the production collector took the shared lock first (exit 24); '
                                    'nothing was collected -- run this block again after it finishes', before)
        self.assertIn('Another collector run holds', out)
        self.assertEqual(sha(srv.db), sha(srv.snapshot))
        # Nothing was collected: once production has finished, the block runs.
        holder.kill()
        holder.wait()
        out = self.run_w1()
        self.assertEqual(out.splitlines()[-1], 'STEP=PASS', out)
        self.assertEqual(len(srv.collector_runs()), 2)

    def test_a_stopped_canary_is_not_collected_again(self):
        """Stopped by DJI, the run sent nothing: staging equals B0, yet a second paste must not visit DJI."""
        srv = self.srv
        out = self.run_w1(collector=srv.collector(log_after={'3': [DESCRIPTOR_LINE % (srv.ids[2], 429, 135)]},
                                                  hang_after=3))
        self.assertIn('the canary was stopped: stop marker HTTP_429', out)
        self.assertEqual(sha(srv.db), sha(srv.snapshot))
        first = srv.runs()
        self.assertEqual(len(first), 1)
        out = self.run_w1()
        self.assertIn('STEP=STOP - STEP FAILED: an earlier W1 run already started the collector (%s); '
                      'the canary is collected once' % first[0], out)
        self.assertNotIn('== 2.', out)
        self.assertEqual(len(srv.collector_runs()), 1)

    def test_collector_failures_stop_before_recalc(self):
        srv = self.srv
        self.pristine_rows()
        before = srv.state()
        ids = srv.ids
        cases = [
            ('exit 2', srv.collector(exit=2), 'the collector ended with exit 2'),
            ('429 marker', srv.collector(log_after={'3': [DESCRIPTOR_LINE % (ids[2], 429, 135)]}, hang_after=3),
             'the canary was stopped: stop marker HTTP_429'),
            ('403 marker', srv.collector(log_after={'2': [DESCRIPTOR_LINE % (ids[1], 403, 0)]}, hang_after=2),
             'the canary was stopped: stop marker HTTP_403'),
            ('captcha', srv.collector(log_after={'2': ['Please verify you are human']}, hang_after=2),
             'the canary was stopped: stop marker CAPTCHA'),
            ('session', srv.collector(log_after={'1': ['the saved session at %s is no longer signed in (landed on '
                                                       'https://www.djiag.com/login) -- run `python -m drone_collector.main '
                                                       '--save-session` again' % srv.session]}, hang_after=1),
             'the canary was stopped: stop marker SESSION'),
            ('five without card', srv.collector(status={str(f): 'nothing' for f in ids[4:9]}, hang_after=9),
             'the canary was stopped: 5 flights in a row came without a card'),
            ('refused cards', srv.collector(status={str(ids[3]): 'no_v4', str(ids[20]): 'no_v4'}, hang_after=21),
             'the canary was stopped: 2 flights came without a card while their other parts came (refused card requests)'),
            ('page errors', srv.collector(status={str(f): 'page_error' for f in ids[:3]}),
             'the canary was stopped: three record pages in a row did not open'),
            ('refused requests', srv.collector(rejected=3, v4_failed=1, exit=18),
             'DJI refused 2 request(s) that were not V4 downloads (sources_rejected=3, sources_v4_failed=1)'),
            ('foreign outbox', srv.collector(report_outbox=srv.prod_outbox, hang_after=1),
             "the canary was stopped: the collector configuration does not show 'outbox_dir'"),
            ('not accepted', srv.collector(accepted='false', exit=19), 'the collector ended with exit 19'),
            ('accepted false exit 0', srv.collector(accepted='false'), 'staging did not accept every source'),
            ('no summary', srv.collector(no_summary=True), 'the collector printed no RUN SUMMARY'),
            ('ended early', srv.collector(status={str(ids[-1]): 'stop_here'}),
             'the collector requested 50 and visited 49 of 50'),
            ('stats disagree', srv.collector(status={str(ids[-1]): 'stop_here'}, report_visited=50),
             'collector-stats saw 49 of 50 flights visited'),
            ('new count', srv.collector(report_new=49), 'staging holds 50 new revisions, the collector reported 49'),
            ('foreign revision', srv.collector(foreign_revisions=[810004]), NEW_EVIDENCE),
            # Each part of the staging evidence gate alone (the other counters stay 0).
            ('other run', srv.collector(status={str(ids[0]): 'nothing'}, other_run_revisions=[ids[0]]), NEW_EVIDENCE,
             'flights=50 outside_canary=0 other_run=1 repeated_type=0 evidence_outside_since=0'),
            ('repeated type', srv.collector(repeated_revisions=[ids[0]]), NEW_EVIDENCE,
             'flights=50 outside_canary=0 other_run=0 repeated_type=1 evidence_outside_since=0'),
            ('outside revision', srv.collector(status={str(ids[0]): 'nothing'}, outside_revisions=[810004]), NEW_EVIDENCE,
             'flights=50 outside_canary=1 other_run=0 repeated_type=0 evidence_outside_since=0'),
            ('outside evidence', srv.collector(outside_evidence=[810001]), NEW_EVIDENCE,
             'flights=50 outside_canary=0 other_run=0 repeated_type=0 evidence_outside_since=1'),
            # Production: this run's id, or canary rows since the start without it.
            ('production write', srv.collector(production_db={'db': srv.prod_db, 'this_run': True}), PROD_EVIDENCE,
             'PROD_DB_AFTER run_rows=1 canary_sources_since=1 canary_evidence_since=1'),
            ('production other run', srv.collector(production_db={'db': srv.prod_db, 'other_run': True}), PROD_EVIDENCE,
             'PROD_DB_AFTER run_rows=0 canary_sources_since=1 canary_evidence_since=0'),
            ('production evidence', srv.collector(production_db={'db': srv.prod_db, 'evidence': True}), PROD_EVIDENCE,
             'PROD_DB_AFTER run_rows=0 canary_sources_since=0 canary_evidence_since=1'),
            ('session written', srv.collector(touch_session=True), 'the production DJI session file changed during the run'),
            ('logged in production', srv.collector(also_log=srv.prod_log),
             'the run was not logged by the pilot checkout only (pilot=True production=True)'),
        ]
        session = read_bytes(srv.session)
        prod_log = read_bytes(srv.prod_log)
        for case in cases:
            name, collector, message = case[:3]
            with self.subTest(name):
                out = self.run_w1(collector=collector)
                if len(case) > 3:
                    self.assertIn(case[3], out)
                expected = dict(before)
                if collector.get('production_db'):
                    expected['prod_db'] = sha(srv.prod_db)
                    self.assertNotEqual(expected['prod_db'], before['prod_db'])
                if name == 'session written':
                    expected['session'] = srv.state()['session']
                    self.assertNotEqual(expected['session'], before['session'])
                if name == 'logged in production':
                    expected['prod_log'] = sha(srv.prod_log)
                    self.assertNotEqual(expected['prod_log'], before['prod_log'])
                self.assertStopBeforeRecalc(out, message, expected)
                if 'stopped' in message:
                    self.assertIn('STOPPED_BY_THIS_BLOCK=', out)
                    self.assertTrue(self.lock_is_free())
                if collector.get('hang_after'):
                    # Stopped in the middle of the walk: killed, not finished.
                    self.assertFalse(self.record['finished'])
                if collector.get('production_db'):
                    os.remove(srv.prod_db)
                    srv.build(srv.prod_db)
                    before['prod_db'] = sha(srv.prod_db)
                if name == 'session written':
                    write(srv.session, session, 'wb')
                    os.utime(srv.session, ns=(before['session'][1], before['session'][1]))
                if name == 'logged in production':
                    write(srv.prod_log, prod_log, 'wb')
                self.reset()

    def test_a_stop_kills_the_collector_tree_and_nothing_else(self):
        srv = self.srv
        bystander = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(300)'])
        self.addCleanup(bystander.wait)
        self.addCleanup(bystander.kill)
        out = self.run_w1(collector=srv.collector(grandchild=True, hang_after=2,
                                                  log_after={'2': [DESCRIPTOR_LINE % (srv.ids[1], 429, 135)]}))
        self.assertIn('STEP=STOP - STEP FAILED: the canary was stopped: stop marker HTTP_429', out)
        child = self.record['grandchild']
        for _ in range(20):
            if not pid_alive(child):
                break
            time.sleep(0.5)
        self.assertFalse(pid_alive(child), 'a process of the collector tree survived the stop')
        self.assertIsNone(bystander.poll(), 'a process outside the collector tree was killed')
        self.assertTrue(self.lock_is_free())
        self.assertNotIn('stderr still open', read(os.path.join(srv.runs()[0], 'collector_stderr.log')))

    def test_time_limit_stops_the_run(self):
        srv = self.srv
        self.pristine_rows()
        before = srv.state()
        out = self.run_w1(collector=srv.collector(hang_after=1), maxCollectMin=0)
        self.assertStopBeforeRecalc(out, 'the canary was stopped: the run passed the 0 min limit', before)
        self.assertFalse(self.record['finished'])
        self.assertTrue(self.lock_is_free())

    def test_failure_in_s1_restarts_staging(self):
        srv = self.srv
        out = self.run_w1(collector=srv.collector(raw_change=True))
        self.assertIn('COLLECTOR_GATE=PASS', out)
        self.assertIn('RECALC_APPLY flights_in_period=50', out)
        self.assertIn('STEP=STOP - STEP FAILED: measure exit 5', out)
        self.assertIn('STAGING_SITE_RESTARTED=Running', out)
        self.assertIn('DECISION=STOP', out)
        self.assertEqual([c for c in self.calls if re.match(r'(Stop|Start)-Service', c)],
                         ['Stop-Service TransportReportStaging', 'Start-Service TransportReportStaging'])

    def mutant(self, old, new):
        """Run a variant of the block: shows what one of its lines is there for."""
        srv = self.srv
        text = srv.block()
        self.assertEqual(text.count(old), 1, old)
        original = srv.block
        srv.block = lambda **kw: text.replace(old, new)
        self.addCleanup(setattr, srv, 'block', original)

    def test_collector_stats_alone_stops_the_recalc(self):
        """With the live watch blind to DJI refusals, collector-stats still refuses."""
        srv = self.srv
        self.pristine_rows()
        before = srv.state()
        self.mutant(re.search(r"(?m)^  \$stopMarkers  = @\(.*\)$", srv.block()).group(0), '  $stopMarkers  = @()')
        out = self.run_w1(collector=srv.collector(log_after={'5': [DESCRIPTOR_LINE % (srv.ids[4], 429, 135)]}))
        self.assertIn('COLLECTOR VERDICT    STOP (HTTP_429)', out)
        self.assertStopBeforeRecalc(out, 'collector-stats exit 6 (6 = DJI stop markers) -- nothing is recalculated', before)

    def test_child_settings_are_what_proves_the_import(self):
        """Negative control: with PYTHONSAFEPATH left in the child the probe refuses."""
        srv = self.srv
        before = srv.state()
        self.mutant("$childDrop = @('PYTHONPATH', 'PYTHONHOME', 'PYTHONSAFEPATH', 'PYTHONSTARTUP')",
                    "$childDrop = @('PYTHONHOME', 'PYTHONSTARTUP')")
        # Production's checkout imports cleanly too: only the location tells them apart.
        write(os.path.join(srv.prod, 'playwright', '__init__.py'), '# stand-in\n')
        out = self.run_w1()
        self.assertIn('STEP=STOP - STEP FAILED: the collector python imports drone_collector from %s, not from %s'
                      % (os.path.join(srv.prod, 'drone_collector'), os.path.join(srv.src, 'drone_collector')), out)
        self.assertEqual(srv.collector_runs(), [])
        self.assertEqual(srv.state(), before)


W2_FILE = os.path.join(REPO_ROOT, 'ops', 'drone_card_coverage_001', 'W2_S2_remaining450_block.ps1')
W2_SHARED = ('Get-ProdServices', 'Get-Sha', 'Test-SameFile', 'Get-FileState', 'Read-Ids', 'Pct', 'Read-Pairs',
             'Start-Child', 'Stop-Child', 'Get-LockOwner', 'Test-Production', 'Test-Collision', 'Test-Staging',
             'Get-Registered')


def w2_text():
    with open(W2_FILE, encoding='ascii') as fh:
        return fh.read()


class W2Text(unittest.TestCase):
    """W2+S2 is a file the owner runs as it is (ops/drone_card_coverage_001)."""

    def setUp(self):
        self.w2 = w2_text()
        self.w1 = blocks()['W1']

    def test_w2_is_one_ascii_block(self):
        w2 = self.w2
        self.assertTrue(w2.startswith('& {\n'))
        self.assertTrue(w2.endswith('\n}\n'))
        self.assertNotIn('\t', w2)
        self.assertNotIn('\r', w2)
        self.assertNotIn('&&', w2)
        # "$name:" inside double quotes is a scope-qualified variable in PowerShell.
        self.assertIsNone(re.search(r'\$(?!env:|global:|script:)\w+:(?!:)', w2))
        self.assertIsNone(re.search(r'<[a-z_ ]+>|\bTODO\b|XXX', w2.replace('<this file>', '')))

    def test_w2_names_the_frozen_sample_w1_and_the_server(self):
        w2, w1 = self.w2, self.w1
        for name in ('expectedHost', 'src', 'cpy', 'python', 'root', 'db', 'service', 'site', 'prodRoot', 'prodDb',
                     'prodBase', 'session', 'lock', 'prodLog', 'pin', 'runRoot', 'baseline',
                     'snapshot', 'canarySha', 'isoTask', 'work', 'svcKey'):
            with self.subTest(name):
                self.assertIsNotNone(const(w2, name))
                self.assertEqual(const(w2, name), const(w1, name))
        for line in ("  $prodDji      = @('drone_collector', 'dji_area', 'drones.py')",
                     "  $prodTasks    = @('DroneCollectorDaily', 'DroneAreaDaily', 'DjiAreaRefresh')",
                     "  $bots         = @('TransportBotStaging', 'TransportBot003Staging')",
                     '  $maxCollectMin = 100', '  $minGapMin    = 130', '  $maxNoCardRun = 5', '  $maxCardRefused = 2',
                     '  $pilotCount   = 500', '  $canaryCount  = 50'):
            self.assertIn(line + '\n', w2)
        # B0 as frozen on 03.10.2026; W1 as the owner ran it on 08.10.2026.
        self.assertEqual(const(w2, 'manifestSha'), '1782d19899ed1e06345e859542d4705d6ac750d6ac57078577f5c03ad6dc66f8')
        self.assertEqual(const(w2, 'pilotSha'), '456b6486f41c8cc66bb8e745196346ea0817e5f6c159c990d8070dcc08e21e8e')
        self.assertEqual(const(w2, 'canarySha'), '5913a88d1bfcecdfe0586fd0a81007ef7cc771d777a2754ebd8b5c1ec2e641da')
        self.assertEqual(const(w2, 'w1Run'), const(w1, 'w1Root') + '\\20261008_151912')
        self.assertEqual(const(w2, 'w1Log'), const(w1, 'work') + '\\card_pilot_w1_20261008_151912.log')
        self.assertEqual(const(w2, 'w1RunId'), 'sources:ids-file:20261008T101922Z')
        for name, value in (('w1New', 200), ('w1Exact', 14), ('w1Identified', 3), ('w1NoKey', 6),
                            ('w1NotInCatalog', 27)):
            self.assertRegex(w2, r'(?m)^  \$%s\s*= %d$' % (name, value))
        self.assertEqual(const(w2, 'w2Root'), const(w1, 'work') + '\\w2')
        self.assertEqual(const(w2, 'prodExpected'), PROD_V123)
        self.assertEqual(const(w2, 'prodBase'), PROD)
        # The stop signs of W1, and the session expiring in the middle of a longer run.
        m1 = re.search(r"(?m)^  \$stopMarkers  = (.*)$", w1).group(1)
        m2 = re.search(r"(?m)^  \$stopMarkers  = (.*)$", w2).group(1)
        self.assertEqual(m2, m1.replace("session (is )?(missing|expired)'", "session (is )?(missing|expired)|expired during the run'"))

    def test_w2_production_dji_code_is_that_of_w1(self):
        """The DJI code of the production W2 expects is that of the commit W1 was checked against."""
        have = [subprocess.run(['git', '-C', REPO_ROOT, 'cat-file', '-e', c + '^{commit}'],
                               capture_output=True).returncode == 0 for c in (PROD, PROD_V123)]
        if not all(have):
            self.skipTest('needs the full git history (the windows-powershell-51 job has it)')
        self.assertEqual(subprocess.run(['git', '-C', REPO_ROOT, 'diff', '--quiet', PROD, PROD_V123, '--',
                                         'drone_collector', 'dji_area', 'drones.py']).returncode, 0)

    def test_w2_shares_the_checked_functions_of_w1(self):
        """The checks that ran live in W1 are the same lines here, not a re-typed copy."""
        for name in W2_SHARED:
            with self.subTest(name):
                one = function_text(self.w1, name).replace('the canary needs a window', 'the collection needs a window')
                self.assertEqual(function_text(self.w2, name), one)

    def test_w2_collects_the_450_once_and_recalculates_them_only(self):
        w2 = self.w2
        code = '\n'.join(l for l in w2.splitlines() if not l.lstrip().startswith(('Write-Output', '#')))
        self.assertEqual(re.findall(r"Start-Child \$cpy \('-m [^)]*\)", w2),
                         ["Start-Child $cpy ('-m drone_collector.main --sources --ids-file \"' + $idsFile + '\" --send-sources')"])
        self.assertEqual(w2.count('Start-Child $cpy'), 2)  # the import probe and the run
        self.assertEqual(w2.count('[System.Diagnostics.Process]::Start('), 1)
        self.assertIn("$idsFile = Join-Path $w2 'remaining_450_ids.txt'", w2)
        self.assertIn("$outbox = Join-Path $w2 'outbox'", w2)
        # 450 = the frozen pilot minus the W1 canary, in manifest order; the file is checked by hash.
        self.assertIn('$remaining = @($pilotIds | Where-Object { -not $canarySet.Contains($_) })', w2)
        self.assertIn('$remainingCount = $pilotCount - $canaryCount', w2)
        self.assertIn('if ((Get-Sha $idsFile) -ne $remainingSha)', w2)
        self.assertIn("$flightArgs = @($remaining | ForEach-Object { '--flight-id'; [string]$_ })", w2)
        self.assertEqual(w2.count('dji_area_recalc.py'), 1)
        self.assertIn('tools\\dji_area_recalc.py --db $db --from 2026-09-01 --to 2026-09-30 $mode2 --quiet', w2)
        for word in ('--save-session', 'Start-Process', 'Invoke-Expression', 'Remove-Item', 'Move-Item',
                     'SetEnvironmentVariable', 'drain', '--routes', '--lands', '--from-date', '--days',
                     'Register-ScheduledTask', 'Set-ScheduledTask', 'Disable-ScheduledTask', 'Enable-ScheduledTask',
                     'Start-ScheduledTask', 'Stop-ScheduledTask', 'Set-Service', 'Restart-Service', 'Set-ItemProperty',
                     'New-ItemProperty', 'Remove-ItemProperty', 'Stop-Process', 'Wait-Process', 'Invoke-RestMethod',
                     'source_sync -Method', 'git pull', 'migrate_', 'backup_transport_db',
                     'Copy-Item -LiteralPath $snapshot', '--with-geometric'):
            self.assertNotIn(word, code, word)
        # The tools it runs: the checks, the pilot tool, one recalculation, the census; no cycle, no backfill.
        self.assertEqual(sorted(set(re.findall(r'tools\\(\w+)\.py', w2))),
                         ['check_db_lock', 'check_migration_drift', 'dji_area_recalc', 'dji_card_coverage_pilot',
                          'dji_field_census'])
        self.assertIsNone(re.search(r'\$env:\w+\s*=', w2))
        # Staging only: start after an interrupted S2 of the same run, stop and start in S2, restart in finally.
        self.assertEqual(re.findall(r'(?:Stop|Start)-Service -Name (\$\w+)', w2), ['$service'] * 4)
        # B1's returned.txt is looked at, never written: returning staging is R's.
        self.assertEqual(re.findall(r'[^\n]*returned\.txt[^\n]*', w2),
                         ["    $open = @(Get-ChildItem -LiteralPath $runRoot -Directory -Filter 'staging_*' | Where-Object { -not (Test-Path -LiteralPath (Join-Path $_.FullName 'returned.txt')) })"])
        # The measure covers the 500 with both collection logs; attempted must be the 500.
        self.assertIn("collector-stats --log $w1ForStats --log (Join-Path $w2 'collector_for_stats.log') --ids $pilotCopy", w2)
        self.assertIn('measure --db $db --plan-dir $planDir --stage pilot --before $fpB0 --collector-stats $statsAllJson', w2)
        self.assertIn('if ([int]$measure.attempted -ne $pilotCount)', w2)
        # No decision to go further and no R: the pilot ends here.
        self.assertNotRegex(w2, r'Write-Output \(?["\']DECISION=')
        self.assertIn("PILOT_STATUS=COMPLETE (W2 and R are not started by this block; R is a separate step)", w2)

    def test_w2_child_environment_and_token(self):
        w2 = self.w2
        m = re.search(r'\$childEnv = \[ordered\]@\{ (.*?) \}\n', w2)
        pairs = dict(p.split(' = ', 1) for p in m.group(1).split('; '))
        self.assertEqual(pairs, {'VEHICLE_SOFT_BASE_URL': '$site', 'DJI_STORAGE_STATE': '$session',
                                 'DJI_COLLECTOR_LOCK_PATH': '$lock', 'DJI_COLLECTOR_LOCK_WAIT_S': "'0'",
                                 'DRONE_OUTBOX_DIR': '$outbox', 'DJI_HEADLESS': "'true'",
                                 'DRONE_API_TOKEN': '$token', 'PYTHONIOENCODING': "'utf-8'"})
        self.assertIn("$childDrop = @('PYTHONPATH', 'PYTHONHOME', 'PYTHONSAFEPATH', 'PYTHONSTARTUP')", w2)
        self.assertIn("$tokenLines = @(@((Get-ItemProperty -LiteralPath $siteParams).AppEnvironmentExtra) | Where-Object { [string]$_ -match '^\\s*DRONE_API_TOKEN=' })", w2)
        self.assertIn('if ($tokenLines.Count -ne 1) { throw', w2)
        self.assertEqual(len(re.findall(r'\$token\b', w2)), 4)  # read, emptiness check, the child, the read-only check
        for line in w2.splitlines():
            if 'Write-Output' in line:
                self.assertNotRegex(line, r'\$token\b|\$childEnv\[|\$childEnv\.Values|\$body\b')
        refuse = w2.index("if ($site -match ':5050')")
        self.assertLess(refuse, w2.index('Start-Child $cpy'))

    def test_w2_reads_production_only(self):
        w2 = self.w2
        helper = re.search(r"\$helperText = @'\n(.*?)\n'@", w2, re.S).group(1)
        self.assertIn("uri = 'file:%s?mode=ro'", helper)
        self.assertEqual(helper.count('sqlite3.connect('), 1)
        for word in ('INSERT', 'UPDATE', 'DELETE', 'DROP', 'ALTER', 'CREATE', 'commit', 'REPLACE', 'PRAGMA'):
            self.assertNotIn(word, helper, word)
        self.assertEqual(len(re.findall(r'\$prodDb\b', w2)), 3)  # the constant, the probe, the gate
        self.assertEqual(sorted(re.findall(r'git -C \$prodRoot ((?:--no-optional-locks )?\S+)', w2)),
                         ['--no-optional-locks status', 'diff', 'rev-parse'])
        for m in re.finditer(r'[^\n]*\$session\b[^\n]*', w2):
            self.assertNotRegex(m.group(0), r'Get-Content|ReadAll|Select-String|Set-Content|Copy-Item|Read-Utf8')
        for m in re.finditer(r'[^\n]*(\$lock\b|\$prodLog\b)[^\n]*', w2):
            self.assertNotRegex(m.group(0), r'Set-Content|Remove|Out-File|WriteAll|Copy-Item|New-Item')
        # The diagnostics print counts, flight ids and field NAMES; never a key, a body or a value.
        diag = helper[helper.index('def diag('):helper.index('def main(')]
        self.assertNotRegex(diag, r"lines\.append\([^\n]*(raw|data\[|body|geometry_key_raw)")

    def test_w2_order_of_gates_and_state(self):
        w2 = self.w2
        launch = w2.index('$proc = Start-Child $cpy')
        for gate in ("Test-Production 'BEFORE'", "Test-Collision 'BEFORE'", "Test-Staging 'BEFORE'",
                     "Test-Production 'LAUNCH'", "Test-Collision 'LAUNCH'", 'FINGERPRINT_PRE=equals W1 fingerprint_post.json',
                     'REGISTERED=60', 'TOKEN_CHECK=', 'W1_RECHECK_NOW', "STEP FAILED: the W1 log $w1Log",
                     'W1_ON_STAGING', '$done = @($runs', '$partial = @($runs', 'W2_REMAINING='):
            self.assertLess(w2.index(gate), launch, gate)
        stop = w2.index('Stop-Service -Name $service -Force')
        for gate in ('if ($stopWhy) { throw', 'if (@(0, 18) -notcontains $code)', 'if ($statsCode -ne 0)',
                     'if ($sessionAfter -ne [string]$done.session_before)', 'if (-not $inPilot -or $inProd)',
                     'if ($revisited.Count -ne 0)', "COLLECTOR_GATE=PASS"):
            self.assertLess(w2.index(gate), stop, gate)
        # What the gate needs is on disk before the gate; the collector is stopped unless it ended by itself.
        self.assertLess(w2.index("WriteAllText((Join-Path $w2 'collector_done.json')"), w2.index("Write-Output '== 3. Collector gate'"))
        self.assertIn('if (-not $ended) { Stop-Child $proc }', w2)
        self.assertLess(w2.index('if ($clock.Elapsed.TotalMinutes -gt $maxCollectMin)'), w2.index('if (-not $next.Wait(5000)) { continue }'))
        # The gate marker is written after every collector check, the S2 marker after every S2 check.
        marker = w2.index("WriteAllText((Join-Path $w2 'collector_gate.json')")
        self.assertLess(w2.index("Write-Fingerprint (Join-Path $w2 'fingerprint_after_collection.json')"), marker)
        for gate in ('if ($stNew[', 'if (([int]$stats.visited -ne $remainingCount)'):
            self.assertLess(w2.index(gate), marker, gate)
        done = w2.index("WriteAllText((Join-Path $w2 's2_done.txt')")
        for gate in ("Test-Staging 'AFTER'", "Test-Production 'AFTER'", 'if ($m2Code -ne 0)', 'if ($mCode -ne 0)'):
            self.assertLess(w2.index(gate), done, gate)
        self.assertLess(done, w2.index('  } catch {\n    $failure = '))
        self.assertEqual(sorted(set(re.findall(r"return '([A-Z0-9_]+)'", function_text(w2, 'Get-RunState'))) | {'COLLECTION_STOPPED'}),
                         ['COLLECTION_STOPPED', 'COMPLETE', 'GATE_PENDING', 'NOT_STARTED', 'RUNNING', 'S2_PENDING'])
        # The staging site is started again in finally, so that Ctrl+C during S2 does not leave it stopped.
        self.assertLess(w2.index('  } finally {\n    # [REASON]: in finally'), w2.index("Write-Output '== 5. Pilot result"))


W2_RESULT_TABLES = ('dji_field_attributions', 'dji_area_calculations', 'dji_flight_evidence', 'dji_source_revisions',
                    'drone_flights', 'drone_area_decisions')


@unittest.skipUnless(POWERSHELL, 'CARD_PILOT_POWERSHELL is not set')
class W2InPowerShell(unittest.TestCase):
    """W2+S2 as the file in ops/, against the stand-in SRV-YOQSH after a real W1.

    [REASON]: W2 continues a live pilot. It must collect exactly the frozen
    pilot minus the 50 W1 already visited, only after W1's evidence and the
    staging state W1 left are proven; never visit DJI twice for the same
    run; recalculate the 450 alone; measure all 500 with both collection
    logs; and leave production, the 50 W1 results and every flight outside
    the pilot as they were. setUpClass runs the real W1 block once (its
    collection by the stand-in collector); the bed has 60 frozen flights
    (50 + 10), so W2_REMAINING is 10 here and 450 on the server.
    """

    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.mkdtemp()
        srv = cls.srv = W1Server(os.path.join(cls.tmp, 'srv'))
        cls.w2_root = os.path.join(srv.work, 'w2')
        keys = {str(fid): CATALOG_KEY for fid in srv.ids[:20]}
        out, _, _ = run_server_block(srv, os.path.join(cls.tmp, 'w1'), srv.block(), srv.scenario(),
                                     srv.collector(card_keys=keys))
        assert out.splitlines()[-1] == 'STEP=PASS' and 'DECISION=GO_TO_500' in out, out
        cls.w1_run = srv.runs()[0]
        cls.w1_log = line_with(out, 'LOG FILE: ')[len('LOG FILE: '):]
        cls.w1_run_id = re.search(r'RUN_SUMMARY run_id=(\S+) ', out).group(1)
        # [REASON]: on the server W1 ran hours before W2; here seconds. The receiver's stamps are
        # moved back to that morning (not hashed by the fingerprint), or the "evidence of other
        # flights since the launch" gate would see W1's own.
        con = sqlite3.connect(srv.db)
        con.execute("UPDATE dji_flight_evidence SET updated_at = '2026-10-08 10:19:22' WHERE updated_at > '2026-10-08 10:19:22'")
        con.execute("UPDATE dji_source_revisions SET received_at = '2026-10-08 10:19:22', last_seen_at = '2026-10-08 10:19:22' "
                    "WHERE received_at > '2026-10-08 10:19:22'")
        con.commit()
        con.close()
        cls.post_w1 = read_bytes(srv.db)
        # Production was released again after W1 (v1.23), its DJI code unchanged.
        sh('git', '-c', 'advice.detachedHead=false', 'checkout', '-q', PROD_V123, cwd=srv.prod)
        cls.pilot_log = os.path.join(srv.src, 'drone_collector', 'logs', 'collector.log')
        cls.pilot_log_bytes = read_bytes(cls.pilot_log)
        cls.pilot = [int(l) for l in read(os.path.join(srv.plan_dir, 'pilot_ids.txt')).splitlines()
                     if l.strip() and not l.startswith('#')]
        cls.canary = [int(l) for l in read(srv.canary_file).splitlines() if l.strip() and not l.startswith('#')]
        cls.remaining = [i for i in cls.pilot if i not in set(cls.canary)]
        assert (len(cls.pilot), len(cls.canary), len(cls.remaining)) == (60, 50, 10), (len(cls.pilot), len(cls.remaining))

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.tmp, True)

    def setUp(self):
        srv = self.srv
        with open(srv.db, 'wb') as fh:
            fh.write(self.post_w1)
        for p in (srv.db + '-wal', srv.db + '-shm', srv.lock + '.owner', srv.record + '.runs', srv.record):
            if os.path.exists(p):
                os.remove(p)
        shutil.rmtree(self.w2_root, True)
        with open(self.pilot_log, 'wb') as fh:
            fh.write(self.pilot_log_bytes)
        for f in os.listdir(srv.work):
            if f.startswith('card_pilot_w2_'):
                os.remove(os.path.join(srv.work, f))
        self.n = 0
        self.before = srv.state()

    def text(self, **override):
        srv, plan = self.srv, self.srv.plan['sample']
        values = {'src': srv.src, 'cpy': sys.executable, 'python': sys.executable, 'root': srv.staging,
                  'db': srv.db, 'prodRoot': srv.prod, 'prodDb': srv.prod_db, 'session': srv.session,
                  'lock': srv.lock, 'prodLog': srv.prod_log, 'pin': srv.pin, 'runRoot': srv.run_root,
                  'baseline': srv.baseline, 'snapshot': srv.snapshot, 'manifestSha': plan['manifest_sha256'],
                  'pilotSha': plan['pilot_ids_sha256'], 'canarySha': plan['canary_ids_sha256'],
                  'pilotCount': 60, 'work': srv.work, 'w1Run': self.w1_run, 'w1Log': self.w1_log,
                  'w1RunId': self.w1_run_id, 'w1New': 50, 'w1Exact': 20, 'w1Identified': 0, 'w1NoKey': 0,
                  'w1NotInCatalog': 30, 'w2Root': self.w2_root}
        values.update(override)
        text = w2_text()
        for name, value in values.items():
            if isinstance(value, int):
                text, n = re.subn(r'^(  \$%s\s*= )\d+$' % name, r'\g<1>%d' % value, text, flags=re.M)
            else:
                text, n = re.subn(r"^(  \$%s\s*= )'[^']*'$" % name, lambda m: m.group(1) + "'" + value + "'",
                                  text, flags=re.M)
            assert n == 1, name
        return text

    def collector(self, **changes):
        # W2's cards: 4 on a catalogued contour, 3 without a contour key (2 with the field
        # empty, 1 without the field), 3 on an unknown contour.
        keys = {str(f): CATALOG_KEY for f in self.remaining[:4]}
        keys.update({str(f): '' for f in self.remaining[4:6]})
        keys[str(self.remaining[6])] = '-'
        return self.srv.collector(card_keys=keys, **changes)

    def run_w2(self, sc=None, collector=None, services_back=True, **override):
        srv = self.srv
        self.n += 1
        sc = sc or srv.scenario()
        out, self.calls, services = run_server_block(srv, os.path.join(self.tmp, 'w2_%s_%d' % (self._testMethodName, self.n)),
                                                     self.text(**override), sc, collector or self.collector())
        self.assertNotIn('BLOCK THREW', out, out)
        check_server_calls(self, self.calls, out)
        if services_back:
            self.assertEqual(services, json.loads(json.dumps(sc['Services'])), out)
        log = line_with(out, 'LOG FILE: ')[len('LOG FILE: '):]
        self.assertRegex(os.path.basename(log), r'^card_pilot_w2_\d{8}_\d{6}\.log$')
        check_no_secret(self, out, srv.work, self.runs())
        self.assertEqual([l for l in out.splitlines() if l.startswith('DECISION=')], [])
        return out

    def runs(self):
        if not os.path.isdir(self.w2_root):
            return []
        return sorted(os.path.join(self.w2_root, d) for d in os.listdir(self.w2_root))

    def table(self, name, ids=None, outside=False, db=None):
        con = sqlite3.connect(db or self.srv.db)
        try:
            cols = [r[1] for r in con.execute('PRAGMA table_info(%s)' % name)]
            key = 'dji_flight_id' if 'dji_flight_id' in cols else 'flight_id'
            if ids is None:
                rows = con.execute('SELECT * FROM %s' % name).fetchall()
            else:
                marks = ','.join('?' * len(ids))
                rows = con.execute('SELECT * FROM %s WHERE %s %s IN (%s)' % (name, key, 'NOT' if outside else '', marks),
                                   list(ids)).fetchall()
            return sorted(rows, key=repr)
        finally:
            con.close()

    def post_w1_table(self, name, ids=None, outside=False):
        path = os.path.join(self.tmp, 'post_w1.db')
        if not os.path.exists(path):
            with open(path, 'wb') as fh:
                fh.write(self.post_w1)
        return self.table(name, ids, outside, db=path)

    def assertStopBeforeDji(self, out, message):
        self.assertIn('STEP=STOP - STEP FAILED: ' + message, out)
        self.assertNotIn('== 2.', out)
        self.assertEqual(self.srv.collector_runs(), [])
        self.assertEqual(read_bytes(self.srv.db), self.post_w1)
        self.assertEqual(self.srv.state(), self.before)
        self.assertEqual([c for c in self.calls if c.startswith(('Stop-Service', 'Start-Service'))], [])
        self.assertNotIn('W2_STATE=COLLECTION_STOPPED', out)

    def assertStopBeforeRecalc(self, out, message, state='COLLECTION_STOPPED'):
        self.assertIn('STEP=STOP - STEP FAILED: ' + message, out)
        self.assertNotIn('COLLECTOR_GATE=PASS', out)
        self.assertNotIn('RECALC_', out)
        self.assertEqual([c for c in self.calls if c.startswith(('Stop-Service', 'Start-Service'))], [])
        self.assertEqual(len(self.srv.collector_runs()), 1)
        for name in ('dji_field_attributions', 'dji_area_calculations'):
            self.assertEqual(self.table(name), self.post_w1_table(name), name)
        # A run this block stopped is never collected again; a collector that ended by itself is
        # only checked again (its gate), never collected again either.
        self.assertIn('W2_STATE=' + state + ' -- ', out)
        if state == 'COLLECTION_STOPPED':
            self.assertIn('PARTIAL_W2_STATE visited=', out)
        else:
            self.assertNotIn('PARTIAL_W2_STATE', out)

    def test_the_interpreter_is_the_one_ci_asked_for(self):
        """In CI CARD_PILOT_POWERSHELL_MAJOR=5: these runs prove Windows PowerShell 5.1, not pwsh 7."""
        got = subprocess.run([POWERSHELL, '-NoProfile', '-NonInteractive', '-Command', '$PSVersionTable.PSVersion.Major'],
                             capture_output=True, text=True, timeout=120).stdout.strip()
        self.assertRegex(got, r'^\d+$')
        want = os.environ.get('CARD_PILOT_POWERSHELL_MAJOR')
        if want:
            self.assertEqual(got, want)

    def test_pass_collects_the_450_recalculates_them_and_measures_the_500(self):
        srv = self.srv
        out = self.run_w2()
        self.assertEqual(out.splitlines()[-1], 'STEP=PASS', out)
        run = self.runs()
        self.assertEqual(len(run), 1)
        run = run[0]
        ids_file = os.path.join(run, 'remaining_450_ids.txt')
        # 1. Exactly the frozen pilot minus the canary, in manifest order, and its hash printed.
        self.assertEqual([int(l) for l in read(ids_file).splitlines() if not l.startswith('#')], self.remaining)
        self.assertEqual(read(ids_file).count('\n'), 12)
        for line in ('W1_ALREADY_DONE=50 W2_REMAINING=10 TOTAL_MANIFEST=60',
                     'PILOT_IDS_SHA256=' + srv.plan['sample']['pilot_ids_sha256'],
                     'REMAINING_IDS_SHA256=' + sha(ids_file), 'MODE=fresh W2 run ' + run,
                     # 7. W1 as verified, and staging exactly as W1 left it.
                     'W1_RUN_SUMMARY exit=0 run_id=' + self.w1_run_id + ' requested=50 visited=50 card=50 new=50',
                     'W1_MEASURE fetched=50 confirmed=20 EXACT=20 IDENTIFIED=0 NO_KEY=0 NOT_IN_CATALOG=30',
                     'W1_ON_STAGING revisions=50 flights=50 outside_canary=0 canary_with_card=50 canary_card_from_w1=50',
                     'FINGERPRINT_PRE=equals W1 fingerprint_post.json',
                     'W1_RECHECK_NOW fetched=50 confirmed=20 EXACT=20 IDENTIFIED=0 NO_KEY=0 NOT_IN_CATALOG=30 gates_exit=0',
                     'TOKEN_CHECK=staging accepts the DRONE_API_TOKEN of its own service', 'REGISTERED=60',
                     'RECEIVER=' + W1_SITE + '/drones/api/source_sync', 'PILOT_OUTBOX=' + os.path.join(run, 'outbox'),
                     'COLLECTOR_GATE=PASS', 'CANARY_REVISITED=0', 'SESSION_AFTER=unchanged',
                     'RUN_LOGGED_IN pilot_checkout=True production_checkout=False',
                     'PROD_DB_AFTER run_rows=0 w2_sources_since=0 w2_evidence_since=0',
                     'STAGING_NEW_EVIDENCE revisions=10 flights=10 outside_w2=0 other_run=0 repeated_type=0 evidence_outside_since=0',
                     'RECALC_DRY-RUN flights_in_period=10', 'RECALC_APPLY flights_in_period=10 calc_writes=10',
                     'outside_w2=0 w1_canary=0 raw=1 decisions=1 migrations=1 sources=1',
                     # 14. All 60 (500 on the server) attempted, from both collection logs.
                     'COLLECTION visited=60 of 60 cards=60 without_card=0', 'SOURCES_SAVED W1=50 W2=10 total=60',
                     'OUTCOME EXACT=24 IDENTIFIED=0 CONFIRMED=24 NO_KEY=3 NOT_IN_CATALOG=33 NO_CARD=0 OTHER_UNRESOLVED=0 NO_CALC=0',
                     'COMPARE W1=20/50 40.0% W2=4/10 40.0% ALL=24/60 40.0%', 'CONFIRMED_RATE all_500=40.0% wilson95=',
                     'PROJECTION (not a fact', 'BY_UNIT ', 'BY_WEEK ', 'CASES NO_KEY: ',
                     'DIAG NO_KEY flights=3 key_field_absent=1 key_field_empty=2 key_in_card_but_not_read=0',
                     'DIAG NOT_IN_CATALOG flights=33 key_format=[PLAIN_MD5:33]', 'DIAG CATALOG snapshots=1 ',
                     'PILOT_STATUS=COMPLETE', 'W2_STATE=COMPLETE', 'STAGING_AFTER HEAD=' + srv.pin,
                     'PROD_AFTER HEAD=' + PROD_V123, 'PROD_DJI_CODE_AFTER=unchanged since ' + PROD):
            self.assertIn(line, out)
        self.assertRegex(out, r'CHANGED_SINCE_W1 flights=\d+ outside_w2=0 w1_canary=0 raw=1 decisions=1 migrations=1')
        for task in ('DroneCollectorDaily', 'DroneAreaDaily', 'DjiAreaRefresh'):
            self.assertRegex(out, r'PROD_TASK_LAUNCH %s state=Ready next=' % task)
        measure = json.loads(read(os.path.join(run, 'measure', 'measure_pilot.json')))
        self.assertEqual((measure['attempted'], measure['fetched'], measure['confirmed']), (60, 60, 24))
        self.assertEqual(json.loads(read(os.path.join(run, 'collector_stats_pilot.json')))['visited'], 60)
        # 2. One collector run: the remaining ids, never a canary flight; the staging token; own outbox.
        self.assertEqual(srv.collector_runs(), [['--sources', '--ids-file', ids_file, '--send-sources']])
        rec = json.loads(read(srv.record))
        self.assertTrue(rec['token_matches'])
        self.assertEqual(rec['env']['DRONE_OUTBOX_DIR'], os.path.join(run, 'outbox'))
        self.assertEqual(rec['env']['VEHICLE_SOFT_BASE_URL'], W1_SITE)
        self.assertEqual(len(os.listdir(os.path.join(run, 'outbox', 'sent'))), 10)
        stats = json.loads(read(os.path.join(run, 'collector_stats_w2.json')))
        self.assertEqual(sorted(int(k) for k in stats['per_flight']), sorted(self.remaining))
        # 13, 15. Staging: the 50 W1 results and every flight outside the pilot as W1 left them.
        for name in W2_RESULT_TABLES:
            self.assertEqual(self.table(name, self.canary), self.post_w1_table(name, self.canary), name)
            self.assertEqual(self.table(name, self.pilot, outside=True), self.post_w1_table(name, self.pilot, outside=True), name)
        self.assertNotEqual(self.table('dji_area_calculations', self.remaining),
                            self.post_w1_table('dji_area_calculations', self.remaining))
        # 16. Production only read; the lock free; the site stopped for S2 and started again.
        self.assertEqual(srv.state(), self.before)
        self.assertFalse(os.path.exists(srv.lock + '.owner'))
        self.assertEqual([c for c in self.calls if re.match(r'(Stop|Start)-Service', c)],
                         ['Stop-Service TransportReportStaging', 'Start-Service TransportReportStaging'])
        # 17. Nothing more: pasting again does nothing at all.
        out = self.run_w2()
        self.assertIn('STEP=STOP - STEP FAILED: W2+S2 was already completed in ' + run, out)
        self.assertIn('W2_STATE=COMPLETE', out)
        self.assertEqual(len(srv.collector_runs()), 1)
        self.assertEqual([c for c in self.calls if c.startswith(('Stop-Service', 'Start-Service'))], [])

    def test_refusals_before_dji(self):
        srv = self.srv
        plan = srv.plan['sample']
        other = '0' * 64
        cases = [
            ('host', dict(sc=srv.scenario(Host='bak-tex11')), 'host is bak-tex11'),
            ('pilot sha', dict(pilotSha=other), os.path.join(srv.plan_dir, 'pilot_ids.txt') + ' has sha256 '),
            ('canary sha', dict(canarySha=other), os.path.join(srv.plan_dir, 'canary_ids.txt') + ' has sha256 '),
            ('manifest sha', dict(manifestSha=other), os.path.join(srv.plan_dir, 'pilot_manifest.csv') + ' has sha256 '),
            ('pilot size', dict(pilotCount=61), 'plan.json says 60 / 50, expected 61 / 50'),
            ('site 5050', dict(site='http://10.103.25.14:5050'), 'the receiver http://10.103.25.14:5050 is the production port 5050 -- refused'),
            ('no staging token', dict(sc=srv.scenario(Registry={MACHINE_KEY: {'DRONE_API_TOKEN': W1_TOKEN},
                                                               SITE_KEY: {'AppEnvironmentExtra': ['PORT=5051']}})),
             'the staging service environment holds 0 DRONE_API_TOKEN entries, expected exactly one'),
            ('two staging tokens', dict(sc=srv.scenario(Registry={MACHINE_KEY: {}, SITE_KEY: {
                'AppEnvironmentExtra': SITE_ENV + ['DRONE_API_TOKEN=' + MACHINE_TOKEN]}})),
             'the staging service environment holds 2 DRONE_API_TOKEN entries, expected exactly one'),
            ('token refused', dict(sc=srv.scenario(Token='another')), 'staging refused the DRONE_API_TOKEN of its own service on a read-only call'),
            ('production head', dict(prodExpected=PROD_NOW), 'production is not as expected (BEFORE)'),
            ('collector due', dict(sc=srv.scenario(tasks={'DroneCollectorDaily': {'NextRunTime': 1.5}})),
             'production task DroneCollectorDaily starts in '),
            ('collector running', dict(sc=srv.scenario(Processes=[{'ProcessId': 777, 'Name': 'python.exe',
                                                                   'CommandLine': 'python -m drone_collector.main'}])),
             '1 collector or cycle process(es) are running (pid 777)'),
            ('bot running', dict(sc=srv.scenario(Services=dict(srv.scenario()['Services'], TransportBotStaging={'Status': 'Running', 'StartType': 'Automatic'}))),
             'TransportBotStaging is Running Automatic'),
            ('iso enabled', dict(sc=srv.scenario(tasks={ISO: {'State': 'Ready'}})), 'DjiAreaRefreshStaging is not Disabled'),
            ('launcher', dict(sc=srv.scenario(Registry={MACHINE_KEY: {}, SITE_KEY: {'AppEnvironmentExtra': SITE_ENV + ['DJI_REFRESH_LAUNCHER=subprocess']}})),
             'DJI_REFRESH_LAUNCHER is back in the staging site environment (BEFORE)'),
            ('writer task', dict(sc=srv.scenario(tasks={'StagingImport': {'State': 'Ready', 'Execute': 'python.exe',
                                                                         'Arguments': 'C:\\transport-report-staging\\tools\\import.py'}})),
             'enabled scheduled task(s) that may collect or write staging: StagingImport'),
            ('w1 log', dict(w1Log=os.path.join(self.w1_run, 'collector_stdout.log')),
             'the W1 log %s does not end W1 with STEP=PASS and DECISION=GO_TO_500' % os.path.join(self.w1_run, 'collector_stdout.log')),
            ('w1 run id', dict(w1RunId='sources:ids-file:20261008T000000Z'), 'the W1 RUN SUMMARY is not the verified one'),
            ('w1 sources', dict(w1New=49), 'the W1 RUN SUMMARY is not the verified one'),
            ('w1 numbers', dict(w1Exact=19, w1NotInCatalog=31), 'the W1 measurement is not the verified one'),
            ('pilot checkout elsewhere', dict(pin=PIN), 'the pilot checkout %s is at %s' % (srv.src, srv.pin)),
            ('production database unreadable', dict(prodDb=os.path.join(self.tmp, 'none.db')), 'the read-only check newrows exit 1'),
        ]
        for name, kw, message in cases:
            with self.subTest(name):
                self.setUp()
                out = self.run_w2(**kw)
                self.assertStopBeforeDji(out, message)
        # Files and databases on the server, one at a time.
        b1_closed = os.path.join(srv.b1_run, 'returned.txt')
        drones = os.path.join(srv.prod, 'drones.py')
        drones_text = read_bytes(drones)

        def outside_attribution(con):
            con.execute('UPDATE dji_field_attributions SET superseded_at = ? WHERE flight_id = ? AND superseded_at IS NULL',
                        ('2026-10-08 12:00:00', self.outside_flight()))

        def canary_attribution(con):
            con.execute('UPDATE dji_field_attributions SET superseded_at = ? WHERE flight_id = ? AND superseded_at IS NULL',
                        ('2026-10-08 12:00:00', self.canary[0]))

        def catalog(con):
            Seed(con).geometry('d' * 32)

        def migration(con):
            con.execute("INSERT INTO schema_migrations (name, applied_at) VALUES ('SYNTH_99', '2026-10-08')")

        def w1_revision_elsewhere(con):
            con.execute("UPDATE dji_source_revisions SET capture_run_id = 'sources:ids-file:OTHER' WHERE id = "
                        "(SELECT MIN(id) FROM dji_source_revisions WHERE capture_run_id = ?)", (self.w1_run_id,))

        def receiver(con):
            con.execute("INSERT INTO dji_source_revisions (provider_account_id, flight_id, scope_key, source_type, sha256, "
                        "size_bytes, captured_at_utc, capture_run_id, is_evidence_import, storage_kind, body_text, received_at) "
                        "VALUES ('X', ?, 'x', 'CARD', ?, 1, '2026-10-08', 'other', 0, 'inline', '{}', '2026-10-08')",
                        (self.outside_flight(), 'e' * 64))

        for name, change, message in (
                ('outside flight changed after W1', outside_attribution, 'staging changed after W1'),
                ('a W1 result changed after W1', canary_attribution, 'staging changed after W1'),
                ('evidence received after W1', receiver, 'staging changed after W1'),
                ('field catalog', catalog, 'the staging field catalog differs from the B0 copy'),
                ('migration', migration, 'the staging database reports 61 registered migrations, expected 60'),
                ('a W1 revision not of the W1 run', w1_revision_elsewhere,
                 'staging does not hold the W1 evidence as verified (its sources, the 50 cards)')):
            with self.subTest(name):
                self.setUp()
                con = sqlite3.connect(srv.db)
                change(con)
                con.commit()
                con.close()
                changed = read_bytes(srv.db)
                out = self.run_w2()
                self.assertIn('STEP=STOP - STEP FAILED: ' + message, out)
                self.assertNotIn('== 2.', out)
                self.assertEqual(srv.collector_runs(), [])
                self.assertEqual(read_bytes(srv.db), changed)
                self.assertEqual(srv.state(), self.before)
        # W1 itself, one piece of its evidence at a time.
        w1_log_text = read(self.w1_log)
        for name, edit, message in (
                ('W1 decided SIMPLIFY', lambda t: t.replace('DECISION=GO_TO_500', 'DECISION=SIMPLIFY'),
                 'does not end W1 with STEP=PASS and DECISION=GO_TO_500'),
                ('W1 ended with STOP', lambda t: t.replace('\nSTEP=PASS', '\nSTEP=STOP - something'),
                 'does not end W1 with STEP=PASS and DECISION=GO_TO_500')):
            with self.subTest(name):
                self.setUp()
                other_log = os.path.join(self.tmp, 'w1_variant.log')
                write(other_log, edit(w1_log_text))
                self.assertNotEqual(read(other_log), w1_log_text)
                out = self.run_w2(w1Log=other_log)
                self.assertStopBeforeDji(out, 'the W1 log %s %s' % (other_log, message))
        w1_files = {name: os.path.join(self.w1_run, name) for name in ('collector_stdout.log', 'fingerprint_pre.json', 'canary_ids.txt')}
        for name, target, edit, message in (
                ('W1 visited 49', 'collector_stdout.log', lambda t: t.replace('sources_visited=50', 'sources_visited=49'), 'the W1 RUN SUMMARY is not the verified one'),
                ('W1 cards 49', 'collector_stdout.log', lambda t: t.replace('sources_card=50', 'sources_card=49'), 'the W1 RUN SUMMARY is not the verified one'),
                ('W1 ingest error', 'collector_stdout.log', lambda t: t.replace('sources_ingest_errors=0', 'sources_ingest_errors=1'), 'the W1 RUN SUMMARY is not the verified one'),
                ('W1 batch refused', 'collector_stdout.log', lambda t: t.replace('sources_batch_accepted=true', 'sources_batch_accepted=false'), 'the W1 RUN SUMMARY is not the verified one'),
                ('W1 started elsewhere', 'fingerprint_pre.json', lambda t: t + ' ', 'W1 did not start from the B0 staging fingerprint'),
                ('W1 canary list edited', 'canary_ids.txt', lambda t: t + '# edited\n', 'the W1 run folder does not hold the frozen canary list')):
            with self.subTest(name):
                self.setUp()
                path = w1_files[target]
                kept = read_bytes(path)
                text = kept.decode('utf-8')
                self.assertNotEqual(edit(text), text)
                with open(path, 'wb') as fh:
                    fh.write(edit(text).encode('utf-8'))
                try:
                    self.assertStopBeforeDji(self.run_w2(), message)
                finally:
                    with open(path, 'wb') as fh:
                        fh.write(kept)
        with self.subTest('pilot checkout edited'):
            self.setUp()
            config = os.path.join(srv.src, 'drone_collector', 'config.py')
            kept = read_bytes(config)
            write(config, '# edited\n', 'a')
            try:
                self.assertStopBeforeDji(self.run_w2(), 'the pilot checkout %s is at %s with 1 tracked change(s)' % (srv.src, srv.pin))
            finally:
                with open(config, 'wb') as fh:
                    fh.write(kept)
        with self.subTest('W1 collector exit'):
            self.setUp()
            exit_file = os.path.join(self.w1_run, 'collector_exit.txt')
            kept = read_bytes(exit_file)
            write(exit_file, '1\n')
            try:
                self.assertStopBeforeDji(self.run_w2(), 'the W1 RUN SUMMARY is not the verified one')
            finally:
                with open(exit_file, 'wb') as fh:
                    fh.write(kept)
        with self.subTest('B1 run closed'):
            self.setUp()
            write(b1_closed, 'returned\n')
            try:
                self.assertStopBeforeDji(self.run_w2(), 'expected exactly one open B1 run with swapped.txt')
            finally:
                os.remove(b1_closed)
        with self.subTest('production DJI code edited'):
            self.setUp()
            write(drones, '# edited\n', 'a')
            try:
                out = self.run_w2()
                self.assertIn('STEP=STOP - STEP FAILED: the production DJI code (drone_collector, dji_area, drones.py) '
                              'differs from %s (diff exit 0, 1 local change(s))' % PROD, out)
                self.assertEqual(srv.collector_runs(), [])
            finally:
                with open(drones, 'wb') as fh:
                    fh.write(drones_text)
        with self.subTest('production lock owner alive'):
            self.setUp()
            write(srv.lock + '.owner', json.dumps({'pid': os.getpid(), 'purpose': 'daily'}))
            out = self.run_w2()
            self.assertIn('STEP=STOP - STEP FAILED: the production collector lock is held by running pid %d' % os.getpid(), out)
            self.assertEqual(srv.collector_runs(), [])
            os.remove(srv.lock + '.owner')

    def outside_flight(self):
        con = sqlite3.connect(self.srv.db)
        try:
            marks = ','.join('?' * len(self.pilot))
            return con.execute('SELECT flight_id FROM dji_field_attributions WHERE superseded_at IS NULL AND flight_id NOT IN (%s) '
                               'ORDER BY flight_id LIMIT 1' % marks, self.pilot).fetchone()[0]
        finally:
            con.close()

    def test_stops_during_and_after_the_collection(self):
        srv = self.srv
        first, canary = self.remaining[0], self.canary[0]
        cases = [
            ('HTTP 429', dict(hang_after=2, log_after={'2': [DESCRIPTOR_LINE % (self.remaining[1], 429, 135)]}),
             'W2 was stopped: stop marker HTTP_429'),
            ('challenge', dict(hang_after=1, log_after={'1': ['the page shows a captcha challenge']}), 'W2 was stopped: stop marker CAPTCHA'),
            ('session expired', dict(hang_after=1, log_after={'1': ['DJI session expired during the run']}), 'W2 was stopped: stop marker SESSION'),
            ('canary visited', dict(visit_also=[canary]), 'W2 was stopped: the collector visited a W1 canary flight'),
            ('two refused cards', dict(status={str(f): 'no_v4' for f in self.remaining[2:4]}, exit=18),
             'W2 was stopped: 2 flights came without a card while their other parts came'),
            # The collector walks the ids ascending; five neighbours in that order.
            ('five without card', dict(status={str(f): 'nothing' for f in sorted(self.remaining)[1:6]}, exit=18),
             'W2 was stopped: 5 flights in a row came without a card'),
            ('exit 1', dict(exit=1), 'the collector ended with exit 1'),
            ('ingest errors', dict(ingest_errors=2), 'staging did not accept every source (accepted=true errors=2)'),
            ('refused requests', dict(rejected=1), 'DJI refused 1 request(s) that were not V4 downloads'),
            ('production database', dict(production_db={'db': srv.prod_db, 'this_run': True}), None),
            # Since the launch in UTC: the receiver stamps UTC, the server runs at UTC+5.
            ('production evidence since the launch', dict(production_db={'db': srv.prod_db, 'evidence': True}), None),
            ('evidence of a flight outside since the launch', dict(outside_evidence=[self.outside_flight()]),
             'staging holds new evidence that is not this run of the 450 W2 flights'),
            # One part of the gate at a time: no other part sees these.
            ('a revision of a flight outside', dict(outside_revisions=[self.outside_flight()],
                                                     status={str(sorted(self.remaining)[0]): 'nothing'}, exit=18),
             'staging holds new evidence that is not this run of the 450 W2 flights'),
            ('a revision of another run', dict(other_run_revisions=[sorted(self.remaining)[0]],
                                               status={str(sorted(self.remaining)[0]): 'nothing'}, exit=18),
             'staging holds new evidence that is not this run of the 450 W2 flights'),
            ('foreign revision', dict(foreign_revisions=[self.outside_flight()]),
             'staging holds new evidence that is not this run of the 450 W2 flights'),
            ('canary revision of another run', dict(other_run_revisions=[canary]),
             'staging holds new evidence that is not this run of the 450 W2 flights'),
            ('a second card in the run', dict(repeated_revisions=[first]),
             'staging holds new evidence that is not this run of the 450 W2 flights'),
            ('fewer new than reported', dict(report_new=11), 'staging holds 10 new revisions, the collector reported 11'),
            ('visited fewer', dict(report_visited=9), 'the collector requested 10, skipped  and visited 9 of 10'),
            ('three pages', dict(status={str(f): 'page_error' for f in sorted(self.remaining)[2:5]}),
             'W2 was stopped: three record pages in a row did not open'),
            ('configuration elsewhere', dict(report_outbox=srv.prod_outbox, hang_after=1),
             "W2 was stopped: the collector configuration does not show 'outbox_dir': "),
            ('session written', dict(touch_session=True), 'the production DJI session file changed during the run'),
            ('production log', dict(also_log=srv.prod_log),
             'the run was not logged by the pilot checkout only (pilot=True production=True)'),
        ]
        prod_db = read_bytes(srv.prod_db)
        session, session_times = read_bytes(srv.session), os.stat(srv.session)
        prod_log = read_bytes(srv.prod_log)
        for name, changes, message in cases:
            with self.subTest(name):
                self.setUp()
                out = self.run_w2(collector=self.collector(**changes))
                if message is None:
                    self.assertIn('STEP=STOP - STEP FAILED: the production database received W2 evidence', out)
                    self.assertNotIn('RECALC_', out)
                    self.assertIn('W2_STATE=GATE_PENDING', out)
                    with open(srv.prod_db, 'wb') as fh:
                        fh.write(prod_db)
                    continue
                stopped = message.startswith(('W2 was stopped', 'the collector ended with exit'))
                self.assertStopBeforeRecalc(out, message, 'COLLECTION_STOPPED' if stopped else 'GATE_PENDING')
                if name in ('session written', 'production log'):
                    with open(srv.session, 'wb') as fh:
                        fh.write(session)
                    os.utime(srv.session, ns=(session_times.st_atime_ns, session_times.st_mtime_ns))
                    with open(srv.prod_log, 'wb') as fh:
                        fh.write(prod_log)
                self.assertEqual(srv.state(), self.before)

    def test_a_stopped_collection_is_never_collected_again(self):
        srv = self.srv
        out = self.run_w2(collector=self.collector(hang_after=3, log_after={'3': [DESCRIPTOR_LINE % (self.remaining[2], 403, 135)]}))
        self.assertStopBeforeRecalc(out, 'W2 was stopped: stop marker HTTP_403')
        self.assertIn('PARTIAL_W2_STATE visited=3 of 10 complete=3 visited_again_by_a_continuation=0 not_visited_yet=7 collector_exit=', out)
        self.assertIn('outbox_pending=3 outbox_sent=0', out)
        run = self.runs()[0]
        # 18. Pasting again (twice): no collection, the state of the stopped run and what to do;
        # the second time its collector is (said to be) still alive and is named with its kill command.
        live = [{'ProcessId': 4321, 'Name': 'python.exe', 'CommandLine': 'python.exe -m drone_collector.main --sources '
                 '--ids-file "%s" --send-sources' % os.path.join(run, 'remaining_450_ids.txt')}]
        for sc in (srv.scenario(), srv.scenario(Processes=live)):
            out = self.run_w2(sc=sc)
            self.assertIn('STEP=STOP - STEP FAILED: an earlier W2 run started the collector and its collection did not end by itself (%s)' % run, out)
            self.assertIn('PARTIAL_W2_STATE visited=3 of 10 ', out)
            self.assertIn('PARTIAL_W2_NEXT=nothing is collected again by this block.', out)
            self.assertIn('W2_STATE=COLLECTION_STOPPED', out)
            self.assertEqual(len(srv.collector_runs()), 1)
            self.assertEqual(len(self.runs()), 1)
            self.assertEqual([c for c in self.calls if c.startswith(('Stop-Service', 'Start-Service', 'WEB Post'))], [])
        self.assertNotIn('COLLECTOR_STILL_RUNNING', self.run_w2().split('W2_STATE=')[0])
        self.assertIn('COLLECTOR_STILL_RUNNING pid=4321 -- the collector of this run is working without supervision; '
                      'stop it now: taskkill /PID 4321 /T /F', out)

    def test_a_second_window_does_not_touch_a_supervised_collection(self):
        """Pasted again while the first window still watches its collector: no taskkill advice, nothing done."""
        srv = self.srv
        first_stem = os.path.join(self.tmp, 'w2_first_window')
        write(first_stem + '.ps1', self.text())
        write(first_stem + '.scenario.json', json.dumps(srv.scenario()))
        write(srv.fake, json.dumps(self.collector(hang_after=2, hang_s=60)))
        env = {k: v for k, v in os.environ.items()
               if not re.match(r'(?i)(DJI_|DRONE_|VEHICLE_SOFT_|PLAYWRIGHT_|PYTHON|HTTPS?_PROXY$|NO_PROXY$)', k)}
        env.update({'CARD_PILOT_FAKE_COLLECTOR': srv.fake, 'PLAYWRIGHT_BROWSERS_PATH': srv.browsers})
        if os.name != 'nt':
            env['TZ'] = W1_TZ
        # [REASON]: the first window's output goes to a file, not a pipe: nobody reads it while it
        # runs, and a Windows pipe buffer fills long before the first flight, blocking the window.
        first_log = open(first_stem + '.out.txt', 'w', encoding='utf-8')
        self.addCleanup(first_log.close)
        first = subprocess.Popen([POWERSHELL, '-NoProfile', '-NonInteractive', '-ExecutionPolicy', 'Bypass', '-File', W1_HARNESS,
                                  '-BlockFile', first_stem + '.ps1', '-ScenarioFile', first_stem + '.scenario.json',
                                  '-CallsFile', first_stem + '.calls.txt'],
                                 stdout=first_log, stderr=subprocess.STDOUT, env=env)
        # [REASON]: killing the first window would leave its collector holding the shared lock;
        # it is let finish (a 60 s pause, then S2) whatever this test finds. The Windows runner
        # needs minutes for the checks before the first flight.
        self.addCleanup(lambda: first.poll() is not None or first.wait(timeout=900))
        for _ in range(1200):
            if first.poll() is not None:
                self.fail('the first window ended early: ' + read(first_stem + '.out.txt'))
            runs = self.runs()
            if runs and os.path.exists(os.path.join(runs[0], 'supervisor.txt')) and \
                    len(re.findall(r': Flight \d+: V4', read(os.path.join(runs[0], 'collector_stdout.log')))) >= 2:
                break
            time.sleep(0.5)
        else:
            self.fail('the first window did not reach its second flight')
        live = [{'ProcessId': 4321, 'Name': 'python.exe', 'CommandLine': 'python.exe -m drone_collector.main --sources '
                 '--ids-file "%s" --send-sources' % os.path.join(runs[0], 'remaining_450_ids.txt')}]
        out = self.run_w2(sc=srv.scenario(Processes=live))
        self.assertIn('STEP=STOP - STEP FAILED: W2 is collecting now in another PowerShell window (%s)' % runs[0], out)
        self.assertIn('W2_STATE=RUNNING', out)
        self.assertNotIn('taskkill', out.split('LOG FILE:')[0].split('== 1.')[1])
        self.assertNotIn('PARTIAL_W2_STATE', out)
        first.wait(timeout=900)
        first_log.close()
        first_out = read(first_stem + '.out.txt')
        self.assertEqual(first_out.splitlines()[-1], 'STEP=PASS', first_out)
        self.assertEqual(len(srv.collector_runs()), 1)

    def test_what_a_stopped_collector_printed_is_kept(self):
        """The stop sign and one more flight arrive together: that flight counts as visited."""
        ids = sorted(self.remaining)
        burst = [DESCRIPTOR_LINE % (ids[1], 429, 135), 'Flight %d: V4 (airlines, card, route, v4)' % ids[2]]
        out = self.run_w2(collector=self.collector(hang_after=2, burst_after={'2': burst}))
        self.assertStopBeforeRecalc(out, 'W2 was stopped: stop marker HTTP_429')
        self.assertIn('PARTIAL_W2_STATE visited=3 of 10 complete=3 ', out)
        self.assertIn('Flight %d: V4 (airlines, card, route, v4)' % ids[2], read(os.path.join(self.runs()[0], 'collector_stdout.log')))

    def test_time_limit_holds_while_the_collector_keeps_talking(self):
        """A slow DJI that answers every second never leaves 5 s of silence; the limit still holds."""
        original = self.text
        self.text = lambda **kw: original(**kw).replace('  $maxCollectMin = 100\n', '  $maxCollectMin = 0.05\n')
        out = self.run_w2(collector=self.collector(pace_s=1))
        self.assertStopBeforeRecalc(out, 'W2 was stopped: the run passed the 0.05 min limit')
        self.assertFalse(json.loads(read(self.srv.record))['finished'])
        self.assertRegex(out, r'PARTIAL_W2_STATE visited=[1-9] of 10 ')

    def test_a_summary_counter_of_429_is_not_a_dji_answer(self):
        """sources_v4_failed=429 in RUN SUMMARY: the run is complete, not refused."""
        out = self.run_w2(collector=self.collector(v4_failed=429))
        self.assertIn('sources_v4_failed=429', out)
        self.assertIn('COLLECTOR_GATE=PASS', out)
        self.assertEqual(out.splitlines()[-1], 'STEP=PASS', out)

    def test_a_gate_cut_off_is_checked_again_without_dji(self):
        """The collector ended by itself; the gate stopped on a passing condition; the next paste re-checks it."""
        srv = self.srv
        out = self.run_w2(collector=self.collector(leave_owner_pid=os.getpid()))
        self.assertStopBeforeRecalc(out, 'the production lock is held by running pid %d' % os.getpid(), 'GATE_PENDING')
        os.remove(srv.lock + '.owner')
        run = self.runs()[0]
        out = self.run_w2()
        self.assertEqual(out.splitlines()[-1], 'STEP=PASS', out)
        self.assertIn('MODE=gate of ' + run, out)
        self.assertIn('COLLECTOR_GATE=PASS', out)
        self.assertEqual(len(srv.collector_runs()), 1)
        self.assertEqual([c for c in self.calls if c.startswith('WEB Post')], [])
        self.assertEqual(self.runs(), [run])

    def test_a_gate_checked_later_ignores_what_production_stored_after_w2(self):
        """The nightly production run may store a list row of a September flight after W2 ended."""
        srv = self.srv
        out = self.run_w2(collector=self.collector(leave_owner_pid=os.getpid()))
        self.assertIn('W2_STATE=GATE_PENDING', out)
        os.remove(srv.lock + '.owner')
        prod_db = read_bytes(srv.prod_db)
        later = (datetime.now(timezone.utc) + timedelta(hours=3)).strftime('%Y-%m-%d %H:%M:%S')
        con = sqlite3.connect(srv.prod_db)
        con.execute("INSERT INTO dji_source_revisions (provider_account_id, flight_id, scope_key, source_type, sha256, "
                    "size_bytes, captured_at_utc, capture_run_id, is_evidence_import, storage_kind, body_text, received_at, "
                    "last_seen_at) VALUES ('P', ?, 'list', 'LIST', ?, 1, ?, 'flights-nightly', 0, 'inline', '{}', ?, ?)",
                    (self.remaining[0], 'a' * 64, later, later, later))
        con.commit()
        con.close()
        try:
            out = self.run_w2()
            self.assertEqual(out.splitlines()[-1], 'STEP=PASS', out)
            self.assertIn('PROD_DB_AFTER run_rows=0 w2_sources_since=0 w2_evidence_since=0 (UTC ', out)
        finally:
            with open(srv.prod_db, 'wb') as fh:
                fh.write(prod_db)

    def test_a_collector_that_ended_before_its_first_flight_did_not_start_w2(self):
        srv = self.srv
        out = self.run_w2(collector=self.collector(status={str(min(self.remaining)): 'stop_here'}, exit=1))
        self.assertIn('STEP=STOP - STEP FAILED: the collector ended with exit 1', out)
        self.assertIn('W2_STATE=NOT_STARTED -- W2 did not visit DJI', out)
        out = self.run_w2()
        self.assertEqual(out.splitlines()[-1], 'STEP=PASS', out)
        self.assertEqual(len(srv.collector_runs()), 2)

    def test_busy_lock_collects_nothing_and_may_run_again(self):
        srv = self.srv
        holder = subprocess.Popen(
            [sys.executable, '-c', 'import os, sys, time; sys.path.insert(0, sys.argv[1]); '
             'from drone_collector import runlock; l = runlock.RunLock(sys.argv[2], purpose="daily"); '
             'assert l.acquire(wait_s=0); os.remove(sys.argv[2] + ".owner"); print("held", flush=True); time.sleep(600)',
             srv.src, srv.lock], stdout=subprocess.PIPE, text=True)
        self.addCleanup(holder.stdout.close)
        self.addCleanup(holder.wait)
        self.addCleanup(holder.kill)
        self.assertEqual(holder.stdout.readline().strip(), 'held')
        out = self.run_w2()
        self.assertIn('STEP=STOP - STEP FAILED: the production collector took the shared lock first (exit 24)', out)
        self.assertIn('W2_STATE=NOT_STARTED -- W2 did not visit DJI', out)
        self.assertEqual(read_bytes(srv.db), self.post_w1)
        holder.kill()
        holder.wait()
        out = self.run_w2()
        self.assertEqual(out.splitlines()[-1], 'STEP=PASS', out)
        self.assertEqual(len(self.runs()), 2)

    def test_an_interrupted_s2_continues_without_dji(self):
        srv = self.srv
        sc = srv.scenario(StartFails=['TransportReportStaging'])
        out = self.run_w2(sc=sc, services_back=False)
        self.assertIn('COLLECTOR_GATE=PASS', out)
        self.assertIn('RECALC_APPLY flights_in_period=10', out)
        self.assertIn("STEP=STOP - Service 'TransportReportStaging' cannot be started.", out)
        self.assertIn('STAGING_SITE_RESTART_FAILED=', out)
        self.assertIn('W2_STATE=S2_PENDING -- the 450 are collected and verified; pasting this block again runs only S2', out)
        self.assertEqual(len(srv.collector_runs()), 1)
        run = self.runs()[0]
        # 12. Pasted again with the site still stopped: S2 only, the same run, no DJI, no token check,
        # the site started again first, the recalculation repeats as unchanged.
        stopped = srv.scenario()
        stopped['Services']['TransportReportStaging']['Status'] = 'Stopped'
        # While S2 runs, the run carries this window's supervisor marker (a second window waits).
        seen = os.path.join(self.tmp, 'supervisor_seen.txt')
        hook = os.path.join(self.tmp, 'copy_supervisor.py')
        write(hook, 'import shutil, sys\nshutil.copyfile(sys.argv[1], sys.argv[2])\n')
        stopped['OnStopStaging'] = [sys.executable, hook, os.path.join(run, 'supervisor.txt'), seen]
        out = self.run_w2(sc=stopped, services_back=False)
        self.assertEqual(out.splitlines()[-1], 'STEP=PASS', out)
        self.assertIn('MODE=resume S2 of ' + run, out)
        self.assertRegex(read(seen), r'^\d+ \d+\n$')
        self.assertFalse(os.path.exists(os.path.join(run, 'supervisor.txt')))
        self.assertIn('STAGING_SITE_STARTED_AGAIN=the interrupted S2 of this run had left it stopped', out)
        self.assertRegex(out, r'CHANGED_SINCE_GATE flights=\d+ outside_w2=0 w1_canary=0 raw=1 decisions=1 migrations=1 sources=1')
        self.assertIn('RECALC_APPLY flights_in_period=10 calc_writes=10 (unchanged=10)', out)
        self.assertIn('COLLECTION visited=60 of 60', out)
        self.assertIn('W2_STATE=COMPLETE', out)
        self.assertEqual(len(srv.collector_runs()), 1)
        self.assertEqual([c for c in self.calls if c.startswith('WEB Post')], [])
        self.assertEqual(self.runs(), [run])
        self.assertEqual(srv.state(), self.before)

    def test_s2_does_not_continue_on_a_staging_changed_after_the_gate(self):
        srv = self.srv
        out = self.run_w2(sc=srv.scenario(StartFails=['TransportReportStaging']), services_back=False)
        self.assertIn('W2_STATE=S2_PENDING', out)
        con = sqlite3.connect(srv.db)
        con.execute('UPDATE dji_field_attributions SET superseded_at = ? WHERE flight_id = ? AND superseded_at IS NULL',
                    ('2026-10-08 12:00:00', self.canary[1]))
        con.commit()
        con.close()
        out = self.run_w2()
        self.assertIn('STEP=STOP - STEP FAILED: staging changed after the W2 collection gate beyond the recalculation of the 450', out)
        self.assertIn('w1_canary=1', out)
        self.assertNotIn('RECALC_', out)
        self.assertEqual(len(srv.collector_runs()), 1)
        # A source revision received after the gate is a change too, even with every flight as it was.
        self.setUp()
        out = self.run_w2(sc=srv.scenario(StartFails=['TransportReportStaging']), services_back=False)
        self.assertIn('W2_STATE=S2_PENDING', out)
        con = sqlite3.connect(srv.db)
        con.execute("INSERT INTO dji_source_revisions (provider_account_id, flight_id, scope_key, source_type, sha256, "
                    "size_bytes, captured_at_utc, capture_run_id, is_evidence_import, storage_kind, body_text, received_at) "
                    "VALUES ('X', ?, 'x', 'ROUTE', ?, 1, '2026-10-08', 'other', 0, 'inline', '{}', '2026-10-08')",
                    (self.outside_flight(), 'f' * 64))
        con.commit()
        con.close()
        out = self.run_w2()
        self.assertIn('STEP=STOP - STEP FAILED: staging changed after the W2 collection gate beyond the recalculation of the 450', out)
        self.assertRegex(out, r'CHANGED_SINCE_GATE flights=\d+ outside_w2=0 w1_canary=0 raw=1 decisions=1 migrations=1 sources=0')

    def test_a_child_left_by_the_collector_stops_the_gate(self):
        """A driver-like child outlives the collector: the gate stops; once it is gone, the gate passes."""
        srv = self.srv
        out = self.run_w2(collector=self.collector(driver_like_child=40))
        self.assertStopBeforeRecalc(out, 'a process the collector (pid ', 'GATE_PENDING')
        self.assertIn('(when the collector ended, its output stayed open 30 s)', out)
        child = json.loads(read(srv.record))['driver_like_child']
        for _ in range(120):
            if not pid_alive(child):
                break
            time.sleep(0.5)
        self.assertFalse(pid_alive(child))
        out = self.run_w2()
        self.assertEqual(out.splitlines()[-1], 'STEP=PASS', out)
        self.assertIn('COLLECTOR_CHILDREN_LEFT=0', out)
        self.assertEqual(len(srv.collector_runs()), 1)

    def test_a_second_window_does_not_run_a_second_s2(self):
        """S2 pending and another window is working on it: nothing is done, the site is not touched."""
        srv = self.srv
        out = self.run_w2(sc=srv.scenario(StartFails=['TransportReportStaging']), services_back=False)
        self.assertIn('W2_STATE=S2_PENDING', out)
        run = self.runs()[0]
        self.assertFalse(os.path.exists(os.path.join(run, 'supervisor.txt')))
        sleeper = subprocess.Popen([POWERSHELL, '-NoProfile', '-NonInteractive', '-Command',
                                    '"$PID " + (Get-Process -Id $PID).StartTime.ToUniversalTime().Ticks; Start-Sleep -Seconds 300'],
                                   stdout=subprocess.PIPE, text=True)
        self.addCleanup(sleeper.wait)
        self.addCleanup(sleeper.kill)
        write(os.path.join(run, 'supervisor.txt'), sleeper.stdout.readline().strip() + '\n')
        stopped = srv.scenario()
        stopped['Services']['TransportReportStaging']['Status'] = 'Stopped'
        out = self.run_w2(sc=stopped)
        self.assertIn('STEP=STOP - STEP FAILED: W2 is collecting now in another PowerShell window (%s)' % run, out)
        self.assertIn('W2_STATE=RUNNING', out)
        self.assertEqual([c for c in self.calls if re.match(r'(Stop|Start)-Service', c)], [])
        self.assertTrue(os.path.exists(os.path.join(run, 'supervisor.txt')))

    def test_s2_postconditions_stop_s2(self):
        """Each S2 check on its own: the change slips past every check before it and S2 stops."""
        srv = self.srv
        con = sqlite3.connect(self.srv.db)
        w1_revision = con.execute('SELECT MIN(id) FROM dji_source_revisions WHERE capture_run_id = ?',
                                  (self.w1_run_id,)).fetchone()[0]
        b0_revision = con.execute('SELECT MIN(id) FROM dji_source_revisions').fetchone()[0]
        con.close()
        outside = self.outside_flight()
        # [REASON]: a script file, not python -c: Windows PowerShell 5.1 drops the inner double
        # quotes of a native argument, and the hook would silently do nothing.
        hook = os.path.join(self.tmp, 'supersede_on_stop.py')
        write(hook, 'import sqlite3, sys\nc = sqlite3.connect(sys.argv[1])\n'
                    "c.execute('UPDATE dji_field_attributions SET superseded_at = ? WHERE flight_id = ? "
                    "AND superseded_at IS NULL', ('2026-10-08 12:00:00', int(sys.argv[2])))\n"
                    'c.commit()\nassert c.total_changes, sys.argv\n')
        during_s2 = srv.scenario(OnStopStaging=[sys.executable, hook, srv.db, str(outside)])
        catalog_hook = os.path.join(self.tmp, 'catalog_on_stop.py')
        write(catalog_hook, 'import sqlite3, sys\nc = sqlite3.connect(sys.argv[1])\n'
                            "c.execute('UPDATE dji_land_geometries SET md5_verified = 1 - md5_verified')\n"
                            'c.commit()\nassert c.total_changes, sys.argv\n')
        cases = [
            ('a flight outside changed during S2', dict(sc=during_s2),
             'the recalculation changed something other than the 450 W2 flights'),
            ('a W1 result changed during the collection', dict(collector=self.collector(supersede_attr_flights=[self.canary[0]])),
             'since W1 something other than the 450 W2 flights changed (the 50 W1 results must stay as they are)'),
            ('a B0 revision rewritten during the collection', dict(collector=self.collector(tamper_revision_ids=[b0_revision])),
             'measure exit 5 (5 = an immutability gate against B0 failed)'),
            ('a W1 revision rewritten during the collection', dict(collector=self.collector(tamper_revision_ids=[w1_revision])),
             'measure against the state after W1 exit 5'),
            ('the field catalog changed during S2', dict(sc=srv.scenario(OnStopStaging=[sys.executable, catalog_hook, srv.db])),
             'the staging field catalog changed during W2'),
        ]
        # The W1 log lost a flight line (its RUN SUMMARY intact): the 500 are not all accounted for.
        w1_out = os.path.join(self.w1_run, 'collector_stdout.log')
        kept = read_bytes(w1_out)
        lines = kept.decode('utf-8').split('\n')
        first_v4 = next(i for i, l in enumerate(lines) if re.search(r': Flight \d+: V4 \(', l))
        with self.subTest('a W1 flight missing from its log'):
            self.setUp()
            with open(w1_out, 'wb') as fh:
                fh.write('\n'.join(lines[:first_v4] + lines[first_v4 + 1:]).encode('utf-8'))
            try:
                out = self.run_w2()
            finally:
                with open(w1_out, 'wb') as fh:
                    fh.write(kept)
            self.assertIn('STEP=STOP - STEP FAILED: the W1 and W2 logs together visited 59 of the 60 frozen flights', out)
            self.assertIn('W2_STATE=S2_PENDING', out)
        for name, kw, message in cases:
            with self.subTest(name):
                self.setUp()
                out = self.run_w2(**kw)
                self.assertIn('COLLECTOR_GATE=PASS', out)
                self.assertIn('STEP=STOP - STEP FAILED: ' + message, out)
                self.assertIn('STAGING_SITE_RESTARTED=Running', out)
                self.assertIn('W2_STATE=S2_PENDING', out)
                self.assertNotIn('PILOT_STATUS=COMPLETE', out)
                self.assertFalse(os.path.exists(os.path.join(self.runs()[0], 's2_done.txt')))

    def test_a_recalculation_beyond_the_450_is_refused(self):
        """Negative control: the same S2 over the whole pilot stops at flights_in_period."""
        text = self.text()
        old = "$flightArgs = @($remaining | ForEach-Object { '--flight-id'; [string]$_ })"
        self.assertEqual(text.count(old), 1)
        original = self.text
        self.text = lambda **kw: original(**kw).replace(old, old.replace('$remaining', '$pilotIds'))
        out = self.run_w2()
        self.assertIn('STEP=STOP - STEP FAILED: recalc --dry-run took 60 flights, expected exactly the 10 W2 flights', out)
        self.assertIn('STAGING_SITE_RESTARTED=Running', out)
        for name in ('dji_field_attributions', 'dji_area_calculations'):
            self.assertEqual(self.table(name, self.canary), self.post_w1_table(name, self.canary), name)


if __name__ == '__main__':
    unittest.main()
