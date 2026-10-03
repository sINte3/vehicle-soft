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
BlocksInPowerShell исполняет B1 и R, как они напечатаны в документе, против
подставного сервера (tests/card_pilot_blocks_harness.ps1): подменены имя
машины, службы, планировщик, реестр, диски и сеть; настоящие -- git (история
этого репозитория: main c34ea9a, пин 39eab50, площадка 2013bed, production
8df5683), python, backup_transport_db.py, check_db_lock.py,
check_migration_drift.py, инструмент пилота и SQLite. Пути: PASS, возврат и
повтор; отказы до первого изменения; каждая остановка после него и R после
неё; повторяемость R; повреждённая и отсутствующая база.
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
import unittest

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if REPO_ROOT not in sys.path:
    sys.path.insert(0, REPO_ROOT)
from tests.test_dji_card_coverage_pilot import build_db  # noqa: E402

DOC = os.path.join(REPO_ROOT, 'docs', 'DRONE_CARD_COVERAGE_001.md')
PROD_SERVICES = ('TransportReport', 'TransportBot', 'TransportBot003')
POWERSHELL = os.environ.get('CARD_PILOT_POWERSHELL')
HERE = os.path.dirname(os.path.abspath(__file__))
HARNESS = os.path.join(HERE, 'card_pilot_blocks_harness.ps1')
HOLDER = os.path.join(HERE, 'card_pilot_db_holder.py')
WRITER = os.path.join(HERE, 'card_pilot_db_writer.py')


def blocks():
    text = open(DOC, encoding='utf-8').read()
    out = {}
    for name in ('B0', 'B1', 'D1', 'R'):
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
            self.assertNotIn(word, r)


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
        }
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
        return out, calls, services, registry

    def run_block(self, bed, sc, **override):
        return self.run_text(bed.block(**override), sc)

    def run_restore(self, bed, sc, **override):
        return self.run_text(bed.restore_block(**override), sc)

    @staticmethod
    def carry(sc, services, registry):
        """The next block sees the services and registry the previous one left."""
        nxt = dict(sc)
        nxt['Services'] = services
        nxt['Registry'] = registry
        nxt.pop('HoldDb', None)
        return nxt

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
            'Set-Service TransportBotStaging Disabled', 'Stop-Service TransportBotStaging',
            'Set-Service TransportBot003Staging Disabled', 'Stop-Service TransportBot003Staging',
            'Stop-Service TransportReportStaging',
            'Set-ItemProperty %s AppEnvironmentExtra MultiString' % SITE_KEY,
            'Start-Service TransportReportStaging'])
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
        for name in ('swapped.txt', 'bots_disabled.txt', 'fingerprint_placed.json',
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
                    'TASKS_CHECKED=3 enabled',
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
            'Set-Service TransportBot003Staging Automatic', 'Start-Service TransportBot003Staging'])
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
            ('may write to staging: StagingDroneDaily', dict(sc=scenario(Tasks=[
                {'TaskName': 'StagingDroneDaily', 'State': 'Ready',
                 'Execute': 'C:\\transport-report-staging\\x.bat', 'Arguments': ''}]))),
            ('may write to staging: HoldoutCollector', dict(sc=scenario(Tasks=[
                {'TaskName': 'HoldoutCollector', 'State': 'Ready',
                 'Execute': 'C:\\VehicleSoft_Holdout_Staging\\venv\\python.exe',
                 'Arguments': '-m drone_collector.main --sources'}]))),
            ('may write to staging: HoldoutSources', dict(sc=scenario(Tasks=[
                {'TaskName': 'HoldoutSources', 'State': 'Ready',
                 'Execute': 'C:\\transport-report\\drone_collector\\.venv\\Scripts\\python.exe',
                 'Arguments': '-m drone_collector.main --sources --send-sources',
                 'WorkingDirectory': 'C:\\VehicleSoft_Holdout_Staging'}]))),
            ('may write to staging: TransportDBBackupStaging', dict(sc=scenario(Tasks=[
                {'TaskName': 'TransportDBBackupStaging', 'State': 'Ready',
                 'Execute': 'C:\\Users\\x\\evil.bat', 'Arguments': ''}]))),
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
        # The first read of the staging database is the online backup
        # (section 2): the run folder exists, nothing on staging changed, the
        # block closes the run itself and can be run again.
        bed = Bed(os.path.join(self.tmp, 'bed'))
        good = open(bed.db, 'rb').read()
        open(bed.db, 'wb').write(b'not a database ' * 100)
        before = sha(bed.db)
        out, calls, _, _ = self.run_block(bed, scenario())
        self.assertIn('STEP=STOP - STEP FAILED: online backup staging_before', out, out)
        self.assertUntouched(bed, calls, before, out)
        self.assertEqual(len(self.runs(bed)), 1)
        open(bed.db, 'wb').write(good)
        out, calls, svc, reg = self.run_block(bed, scenario())
        self.assertIn('STEP=PASS', out, out)

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

    def test_restore_converges_after_a_stop(self):
        bed = Bed(os.path.join(self.tmp, 'bed'))
        tasks = [
            {'TaskName': 'TransportDBBackupStaging', 'State': 'Ready',
             'Execute': 'C:\\transport-report-staging\\backup_staging_db.bat', 'Arguments': ''},
            {'TaskName': 'TopazFuelAgent', 'State': 'Ready',
             'Execute': 'C:\\Program Files\\Python314\\python.exe', 'Arguments': 'C:\\topaz_agent.py'},
        ]
        run, final, nxt = self.b1_pass(bed, scenario(Tasks=tasks))
        bad = dict(nxt, Web={'/login': {'Status': 500, 'Body': 'x'},
                             '/drones/fields': {'Status': 200, 'Body': LOGIN}})
        out, calls, svc, reg = self.run_restore(bed, bad)
        self.assertIn('STEP=STOP', out, out)
        self.assertIn('STAGING_CHANGED=yes', out)
        self.assertTrue(os.path.exists(os.path.join(run, 'db_restored.txt')))
        self.assertFalse(os.path.exists(os.path.join(run, 'returned.txt')))
        out, calls, svc, reg = self.run_restore(bed, self.carry(scenario(), svc, reg))
        self.assertIn('STEP=PASS', out, out)
        self.assertIn('DB_RESTORED=already', out)
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


@unittest.skipUnless(POWERSHELL and os.name == 'nt', 'needs Windows and CARD_PILOT_POWERSHELL')
class TaskDiscoveryOnWindows(unittest.TestCase):
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
                  'Register-ScheduledTask -TaskName \'%s\' -Action $a -Trigger $t -Settings (New-ScheduledTaskSettingsSet -MultipleInstances IgnoreNew) | Out-Null\n'
                  % (self.wrapper, self.root, self.task))
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
                    'FILE ' + self.wrapper, 'POINTS_AT STAGING_FOLDER=yes', 'POINTS_AT DAILY_CYCLE=yes',
                    'STAGING_WRITER=yes', 'CYCLE_RUNS_BY_KIND SCHEDULED count=1',
                    '[line hidden: it names TOKEN]', '-Token [hidden]', 'BUTTON_STARTS_THIS_TASK=no'):
            self.assertIn(key, out)
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


if __name__ == '__main__':
    unittest.main()
