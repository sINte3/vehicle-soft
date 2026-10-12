# -*- coding: utf-8 -*-
"""FIN-RECON-STORAGE-001: ранбук владельца docs/FIN_RECON_STORAGE_001_RUNBOOK.md.

Восемь блоков владелец вставляет в Windows PowerShell 5.1 на сервере, где
рядом работают production и staging. Здесь держится:

  * текст: восемь блоков, ASCII без табуляций, без плейсхолдеров и `&&`;
    каждый блок сначала проверяет имя компьютера; постоянные одинаковы во
    всех блоках; закрепления ($sha, $packageZipSha, $contentSha) настоящие,
    и после $sha ветка меняет только docs/, tests/ и .github/ -- то же
    правило, что блок 2 проверяет на сервере;
  * безопасность: git в папках production и staging -- только чтение
    (rev-parse, symbolic-ref, status); ни одной команды, которая
    останавливает, запускает или перенастраивает службы; питон не получает
    ни одного пути production; брандмауэр открывается только для сети
    10.103.0.0/16;
  * сами блоки исполняются (tests/fin_recon_runbook_harness.ps1) с
    НАСТОЯЩИМИ git и python на синтетическом репозитории, синтетических
    production и staging и синтетическом пакете: путь владельца целиком и
    каждая остановка -- чужой компьютер, подменённый архив, чужой пакет,
    правка кода после проверенного коммита, падающие тесты, занятый порт,
    чужой процесс с номером клона, изменение staging во время блока.

ВСЕ ДАННЫЕ СИНТЕТИЧЕСКИЕ. Запуск:
  python -m unittest tests.test_fin_recon_runbook -v
Блоки в PowerShell: FIN_RECON_POWERSHELL=pwsh (или powershell) в окружении;
FIN_RECON_POWERSHELL_MAJOR=5 заставляет проверить, что это именно 5.1.
"""

import glob
import hashlib
import json
import os
import re
import shutil
import sqlite3
import stat
import subprocess
import sys
import tempfile
import unittest
import zipfile

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if REPO_ROOT not in sys.path:
    sys.path.insert(0, REPO_ROOT)

from fin_recon import package  # noqa: E402
from tests import fin_recon_fixture as fx  # noqa: E402

RUNBOOK = os.path.join(REPO_ROOT, 'docs', 'FIN_RECON_STORAGE_001_RUNBOOK.md')
HARNESS = os.path.join(REPO_ROOT, 'tests', 'fin_recon_runbook_harness.ps1')
POWERSHELL = os.environ.get('FIN_RECON_POWERSHELL')
POWERSHELL_MAJOR = os.environ.get('FIN_RECON_POWERSHELL_MAJOR')
BRANCH = 'claude/fin-recon-prompt-ry4xsx'
# [REASON]: блок 2 принимает после проверенного коммита только эти папки --
# они не исполняются приложением. Правило здесь и в блоке одно.
AFTER_REVIEWED = re.compile(r'^(docs|tests|\.github)/')
READ_ONLY_GIT = {'rev-parse', 'symbolic-ref', 'status'}
APP_GIT = {'clone', 'fetch', 'cat-file', 'merge-base', 'diff', 'rev-parse',
           'status', 'checkout'}
TEST_MODULES = ('tests.test_fin_recon_contract', 'tests.test_fin_recon_package',
                'tests.test_fin_recon_import', 'tests.test_fin_recon_cli',
                'tests.test_fin_recon_migration')


def read(path):
    with open(path, encoding='utf-8') as fh:
        return fh.read()


def blocks():
    """{номер: код} -- ограда powershell, что идёт за «## Блок N.»."""
    found = {}
    current = None
    for m in re.finditer(r'^## Блок (\d+)\.|^```powershell\n(.*?)^```',
                         read(RUNBOOK), re.M | re.S):
        if m.group(1):
            current = int(m.group(1))
        elif current is not None:
            assert current not in found, current
            found[current] = m.group(2)
            current = None
    return found


CONSTANT = re.compile(r"^  \$([A-Za-z]+) = ('(?:[^']|'')*'|\d+)$", re.M)


def constants(code):
    """Литеральные постоянные блока: `  $name = 'value'` или число."""
    return {m.group(1): m.group(2) for m in CONSTANT.finditer(code)}


def literal(value):
    if isinstance(value, int):
        return str(value)
    return "'" + str(value).replace("'", "''") + "'"


def prepare(code, values):
    """Подменить постоянные; каждая заменяемая встречается ровно раз."""
    present = constants(code)
    for name, value in values.items():
        if name not in present:
            continue
        pattern = re.compile(r'^  \$%s = .+$' % name, re.M)
        assert len(pattern.findall(code)) == 1, name
        code = pattern.sub(lambda m: '  $%s = %s' % (name, literal(value)),
                           code)
    return code


def without_functions(code):
    """Тело блока без определений функций -- то, что исполняется по порядку."""
    return re.sub(r'^  function .*?^  }\n', '', code, flags=re.M | re.S)


def git_calls(code):
    """(папка, подкоманда) каждого Invoke-Native 'git' @(...) блока."""
    found = []
    for m in re.finditer(r"Invoke-Native 'git' @\(([^)]*)\)", code):
        tokens = [t.strip() for t in m.group(1).split(',')]
        folder = None
        if "'-C'" in tokens:
            at = tokens.index("'-C'")
            folder = tokens[at + 1]
            tokens = tokens[at + 2:]
        words = [t.strip("'") for t in tokens if t.startswith("'")]
        subs = [w for w in words if not w.startswith('-')]
        found.append((folder, subs[0] if subs else words[0]))
    return found


def sha256_file(path):
    digest = hashlib.sha256()
    with open(path, 'rb') as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b''):
            digest.update(chunk)
    return digest.hexdigest()


def git(cwd, *args):
    proc = subprocess.run(
        ['git', '-c', 'user.name=fin-recon-test',
         '-c', 'user.email=fin-recon-test@invalid', '-c', 'commit.gpgsign=false',
         '-c', 'core.autocrlf=false'] + list(args),
        cwd=cwd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
    if proc.returncode != 0:
        raise AssertionError('git %s: %s' % (' '.join(args),
                                             proc.stdout.decode('utf-8', 'replace')))
    return proc.stdout.decode('utf-8', 'replace').strip()


def has_commit(commit):
    return subprocess.run(['git', 'cat-file', '-e', commit + '^{commit}'],
                          cwd=REPO_ROOT, stdout=subprocess.DEVNULL,
                          stderr=subprocess.DEVNULL).returncode == 0


class RunbookText(unittest.TestCase):
    """То, что видно в тексте ранбука до запуска."""

    def setUp(self):
        self.blocks = blocks()

    def test_eight_blocks_in_order(self):
        self.assertEqual(sorted(self.blocks), list(range(1, 9)))

    def test_blocks_are_pasteable(self):
        # Консоль сервера -- Windows PowerShell 5.1 с кодовой страницей OEM,
        # а табуляция при вставке мышью уходит в автодополнение.
        for number, code in self.blocks.items():
            with self.subTest(block=number):
                self.assertTrue(code.isascii())
                self.assertNotIn('\t', code)
                self.assertNotIn('&&', code)
                self.assertNotRegex(code, r'<[A-Za-z][^>]*>')
                for word in ('TODO', 'XXX', 'YYYY'):
                    self.assertNotIn(word, code)
                lines = code.rstrip('\n').split('\n')
                self.assertEqual((lines[0], lines[-1]), ('& {', '}'))

    def test_every_block_checks_the_computer_before_anything_else(self):
        check = "if ((hostname) -ne $expectedHost) { throw"
        acting = ('New-Item', 'Remove-Item', 'Copy-Item', 'Move-Item',
                  'Set-Content', 'Add-Content', 'Expand-Archive',
                  'Start-Process', 'Stop-Process', 'New-NetFirewallRule',
                  'Remove-NetFirewallRule', 'Invoke-Native', 'Get-SiteState')
        for number, code in self.blocks.items():
            with self.subTest(block=number):
                self.assertEqual(constants(code)['expectedHost'], "'srv-yoqsh'")
                body = without_functions(code)
                self.assertIn(check, body)
                first_check = body.index(check)
                for word in acting:
                    if word in body:
                        self.assertLess(first_check, body.index(word), word)

    def test_constants_agree_across_blocks(self):
        seen = {}
        for code in self.blocks.values():
            for name, value in constants(code).items():
                seen.setdefault(name, set()).add(value)
        for name, values in seen.items():
            if name == 'secondRoot':
                continue
            self.assertEqual(len(values), 1, (name, values))
        self.assertEqual(seen['period'], {"'2026-03'"})
        self.assertEqual(seen['version'], {'1'})
        self.assertEqual(seen['port'], {'5052'})
        self.assertEqual(seen['python'],
                         {"'C:\\Program Files\\Python314\\python.exe'"})
        self.assertEqual(seen['secondRoot'], {"''"})

    def test_pins_are_real_values(self):
        merged = {}
        for code in self.blocks.values():
            merged.update(constants(code))
        sha = merged['sha'].strip("'")
        self.assertRegex(sha, r'^[0-9a-f]{40}$')
        self.assertNotEqual(sha, '0' * 40)
        for name in ('packageZipSha', 'contentSha'):
            value = merged[name].strip("'")
            self.assertRegex(value, r'^[0-9a-f]{64}$', name)
            self.assertNotEqual(value, '0' * 64, name)

    def test_after_the_reviewed_commit_only_docs_and_tests_change(self):
        merged = {}
        for code in self.blocks.values():
            merged.update(constants(code))
        sha = merged['sha'].strip("'")
        if not has_commit(sha):
            self.skipTest('the reviewed commit is not in this clone')
        self.assertEqual(subprocess.run(
            ['git', 'merge-base', '--is-ancestor', sha, 'HEAD'],
            cwd=REPO_ROOT).returncode, 0)
        changed = git(REPO_ROOT, 'diff', '--name-only', sha, 'HEAD').split()
        for path in changed:
            self.assertRegex(path, AFTER_REVIEWED)
        for path in ('tools/fin_recon_storage.py',
                     'migrate_fin_recon_storage_001.py',
                     'backup_transport_db.py', 'run_server.py',
                     'tools/check_migration_drift.py'):
            self.assertEqual(subprocess.run(
                ['git', 'cat-file', '-e', '%s:%s' % (sha, path)],
                cwd=REPO_ROOT).returncode, 0, path)

    def test_git_reads_only_in_production_and_staging(self):
        for number, code in self.blocks.items():
            for folder, sub in git_calls(code):
                with self.subTest(block=number, folder=folder, sub=sub):
                    # Блок 1 обходит обе площадки в цикле через $root.
                    if folder in ('$prodRoot', '$stagingRoot', '$root'):
                        self.assertIn(sub, READ_ONLY_GIT)
                    elif folder is None:
                        self.assertIn(sub, {'--version', 'clone'})
                    else:
                        self.assertEqual(folder, '$appDir')
                        self.assertIn(sub, APP_GIT)
            for word in ("'pull'", "'push'", "'reset'", "'clean'", "'stash'",
                         "'rebase'", "'commit'", "'merge'", "'tag'", "'config'",
                         "'remote'", "'worktree'", "'branch'", 'git pull'):
                self.assertNotIn(word, code, (number, word))
            # git -- только через Invoke-Native, никогда голой командой.
            self.assertNotRegex(code, r'(?m)^\s*(&\s*)?git\s')
        checkouts = re.findall(r".*'checkout'.*", ''.join(self.blocks.values()))
        self.assertEqual(len(checkouts), 1)
        self.assertIn("'--detach', $sha)", checkouts[0])

    def test_no_block_touches_a_service(self):
        for number, code in self.blocks.items():
            with self.subTest(block=number):
                self.assertNotRegex(
                    code, r'(?i)\b(stop|start|restart|set|new|remove|suspend)-service\b')
                self.assertNotIn('nssm', code.lower())
                for m in re.finditer(r"Invoke-Native 'sc\.exe' @\(([^)]*)\)", code):
                    self.assertTrue(m.group(1).startswith("'qc'"), m.group(0))
        stops = [(n, line.strip()) for n, code in self.blocks.items()
                 for line in code.split('\n') if 'Stop-Process' in line]
        self.assertEqual({n for n, _ in stops}, {4, 5})
        for _n, line in stops:
            self.assertRegex(line, r'Stop-Process -Id (\$running\.Id|\$clonePid) -Force')

    def test_python_never_gets_production_and_staging_only_for_reading(self):
        for number, code in self.blocks.items():
            for line in code.split('\n'):
                if 'Invoke-Native $python' not in line:
                    continue
                with self.subTest(block=number, line=line.strip()[:60]):
                    self.assertNotIn('$prodRoot', line)
                    if '$stagingRoot' in line or '$stagingDb' in line:
                        self.assertTrue(('$snapshotTool' in line) or
                                        ('backup_transport_db.py' in line), line)

    def test_scripts_and_modules_the_blocks_run_exist(self):
        text = ''.join(self.blocks.values())
        for script in ('fin_recon_storage.py', 'check_migration_drift.py',
                       'state_snapshot.py'):
            self.assertIn("'%s'" % script, text)
            self.assertTrue(os.path.isfile(os.path.join(REPO_ROOT, 'tools', script)))
        for script in ('backup_transport_db.py', 'migrate_fin_recon_storage_001.py',
                       'run_server.py'):
            self.assertIn("'%s'" % script, text)
            self.assertTrue(os.path.isfile(os.path.join(REPO_ROOT, script)))
        named = re.findall(r"'(tests\.test_fin_recon_[a-z]+)'", self.blocks[2])
        self.assertEqual(tuple(named), TEST_MODULES)
        for module in named:
            self.assertTrue(os.path.isfile(os.path.join(
                REPO_ROOT, *module.split('.')) + '.py'), module)

    def test_firewall_opens_only_the_lan_and_closes_by_the_same_name(self):
        four, five = self.blocks[4], self.blocks[5]
        self.assertEqual(constants(four)['lan'], "'10.103.0.0/16'")
        self.assertEqual(constants(four)['ruleName'], constants(five)['ruleName'])
        rule = [line for line in four.split('\n') if 'New-NetFirewallRule' in line]
        self.assertEqual(len(rule), 1)
        self.assertIn('-LocalPort $port -RemoteAddress $lan', rule[0])
        self.assertNotIn('Any', four)
        self.assertNotIn('New-NetFirewallRule', ''.join(
            code for n, code in self.blocks.items() if n != 4))

    def test_the_archive_is_never_removed(self):
        for number, code in self.blocks.items():
            for line in code.split('\n'):
                if 'Remove-Item' in line:
                    self.assertRegex(line.strip(),
                                     r'Remove-Item -LiteralPath (\$pidFile|\$appDir)\b',
                                     (number, line))


@unittest.skipUnless(POWERSHELL, 'FIN_RECON_POWERSHELL is not set')
class RunbookBlocksInPowerShell(unittest.TestCase):
    """Блоки исполняются против синтетического сервера.

    Синтетический репозиторий (проверенный коммит и поздний коммит docs),
    синтетические production и staging (git, база, state_snapshot.py),
    синтетический пакет и ZIP. Подменено только то, чего нет вне Windows:
    имя компьютера, службы, порты, брандмауэр, процесс клона, icacls,
    sc.exe, диски и сайт.
    """

    @classmethod
    def setUpClass(cls):
        if not shutil.which('git'):
            raise unittest.SkipTest('git is not on PATH')
        cls.world = tempfile.mkdtemp(prefix='fin_recon_rb_')
        cls.origin = os.path.join(cls.world, 'origin')
        cls.sha = make_origin(cls.origin)
        cls.pkg = make_package(cls.world)
        if POWERSHELL_MAJOR:
            proc = subprocess.run(
                [POWERSHELL, '-NoProfile', '-NonInteractive', '-Command',
                 '$PSVersionTable.PSVersion.Major'],
                stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
            assert proc.stdout.decode().strip() == POWERSHELL_MAJOR, proc.stdout

    @classmethod
    def tearDownClass(cls):
        rmtree(cls.world)

    def setUp(self):
        self.root = tempfile.mkdtemp(dir=self.world)
        self.server = make_server(self.root, self.pkg)
        self.calls = []

    # ── running a block ─────────────────────────────────────────────────

    def values(self, **changes):
        s = self.server
        value = {
            'expectedHost': 'srv-test', 'prodRoot': s['prod'],
            'stagingRoot': s['staging'], 'finRoot': s['fin'],
            'evidenceRoot': s['evidence'], 'backupRoot': s['backups'],
            'python': sys.executable,
            'stagingUrl': 'http://127.0.0.1:5051/login',
            'repoUrl': self.origin, 'branch': BRANCH, 'sha': self.sha,
            'packageZip': 'FIN_RECON_EVIDENCE_2031-03_v1.zip',
            'packageZipSha': self.pkg['zip_sha'],
            'packageId': 'synthetic-2031-03-v1',
            'contentSha': self.pkg['content_sha'], 'period': '2031-03',
            'expectName': 'expect.json', 'publicHost': '127.0.0.1',
            'fullZip': 'FULL_EVIDENCE_TEST.zip', 'largeOriginalBytes': 4321,
        }
        value.update(changes)
        return value

    def scenario(self, **changes):
        value = {
            'Host': 'srv-test', 'StateFile': self.server['state'],
            'Services': [
                {'Name': 'TransportBot', 'Status': 'Running', 'StartType': 'Automatic'},
                {'Name': 'TransportReport', 'Status': 'Running', 'StartType': 'Automatic'},
                {'Name': 'TransportReportStaging', 'Status': 'Running', 'StartType': 'Automatic'}],
            'Web': {'/login': {'Status': 200, 'Body': LOGIN_BODY},
                    '/drones/fin-recon': {'Status': 200, 'Body': LOGIN_BODY}},
            'ClonePid': CLONE_PID, 'CloneTicks': CLONE_TICKS,
            'IcaclsCode': 0, 'PartitionDisks': [0, 1],
            'Volumes': [{'DriveLetter': 'C', 'DriveType': 'Fixed',
                         'SizeRemaining': 50 * 1024 ** 3},
                        {'DriveLetter': 'E', 'DriveType': 'Removable',
                         'SizeRemaining': 20 * 1024 ** 3}],
        }
        value.update(changes)
        return value

    def run_block(self, number, values=None, **scenario):
        code = prepare(blocks()[number], self.values(**(values or {})))
        folder = tempfile.mkdtemp(dir=self.root, prefix='run_')
        block_file = os.path.join(folder, 'block.ps1')
        scenario_file = os.path.join(folder, 'scenario.json')
        calls_file = os.path.join(folder, 'calls.txt')
        with open(block_file, 'w', encoding='utf-8') as fh:
            fh.write(code)
        with open(scenario_file, 'w', encoding='ascii') as fh:
            json.dump(self.scenario(**scenario), fh, ensure_ascii=True)
        env = {k: v for k, v in os.environ.items()
               if not k.startswith('GIT_') and k not in ('SECRET_KEY', 'FLASK_ENV')}
        env.update({'PORT': '7777', 'HOST': 'console-host',
                    'FUEL_API_TOKEN': 'console-token'})
        proc = subprocess.run(
            [POWERSHELL, '-NoProfile', '-NonInteractive', '-ExecutionPolicy',
             'Bypass', '-File', HARNESS, '-BlockFile', block_file,
             '-ScenarioFile', scenario_file, '-CallsFile', calls_file],
            stdout=subprocess.PIPE, stderr=subprocess.STDOUT, env=env,
            timeout=600)
        out = proc.stdout.decode('utf-8', 'replace').replace('\r\n', '\n')
        with open(calls_file, encoding='utf-8-sig') as fh:
            calls = [line for line in fh.read().splitlines() if line]
        self.calls.extend(calls)
        return out, calls

    def assertPassed(self, out):
        lines = [l for l in out.split('\n') if l and not l.startswith('ENV_AFTER')]
        self.assertNotIn('THROWN=', out, out)
        self.assertEqual(lines[-1], 'STEP=PASS', out)

    def assertStopped(self, out, fragment):
        self.assertNotIn('STEP=PASS', out, out)
        thrown = [l for l in out.split('\n') if l.startswith('THROWN=')]
        self.assertEqual(len(thrown), 1, out)
        self.assertIn('STEP FAILED', thrown[0], out)
        self.assertIn(fragment, thrown[0], out)

    def assertGitReadOnlyOnSites(self):
        for call in self.calls:
            if not call.startswith('git '):
                continue
            words = call.split(' ')
            if '-C' in words:
                folder = words[words.index('-C') + 1]
                if folder in (self.server['prod'], self.server['staging']):
                    sub = words[words.index('-C') + 2]
                    self.assertIn(sub, READ_ONLY_GIT, call)

    def server_lines(self):
        with open(self.server['state'], encoding='ascii') as fh:
            return [line for line in fh.read().splitlines() if line]

    def set_server_lines(self, lines):
        with open(self.server['state'], 'w', encoding='ascii') as fh:
            fh.write('\n'.join(lines) + '\n')

    def sites_unchanged(self):
        s = self.server
        for site in ('prod', 'staging'):
            self.assertEqual(git(s[site], 'rev-parse', 'HEAD'), s[site + '_head'])
            self.assertEqual(git(s[site], 'status', '--porcelain',
                                 '--untracked-files=no'), '')
        self.assertEqual(sha256_file(s['prod_db']), s['prod_db_sha'])
        self.assertEqual(sha256_file(s['staging_db']), s['staging_db_sha'])
        lines = self.server_lines()
        for line in SERVICE_LINES:
            self.assertIn(line, lines)

    # ── the owner's path ────────────────────────────────────────────────

    def test_owner_path_end_to_end(self):
        s = self.server
        target = os.path.join(s['evidence'], '2031-03', 'v1')
        app = os.path.join(s['fin'], 'app')
        clone_db = os.path.join(app, 'instance', 'transport.db')
        pid_file = os.path.join(s['fin'], 'state', 'clone.json')

        out, calls = self.run_block(1)
        self.assertPassed(out)
        self.assertIn('PROD_HEAD=' + s['prod_head'], out)
        self.assertIn('STAGING_HEAD=' + s['staging_head'], out)
        self.assertIn('STAGING_TRACKED_CHANGES=0', out)
        self.assertIn('schema_migrations rows: 2', out)
        self.assertIn('STAGING_LOGIN=200', out)
        self.assertIn('PORT_5052=free', out)
        self.assertEqual([c for c in calls if not c.startswith(('git ', 'GET '))], [])

        out, calls = self.run_block(2)
        self.assertPassed(out)
        self.assertRegex(out, r'TESTS=Ran \d+ tests exit=0')
        self.assertIn('PACKAGE_ZIP_SHA256=' + self.pkg['zip_sha'], out)
        self.assertIn('PACKAGE_PLACED=' + target, out)
        self.assertIn('EXPECTATIONS 24 checked, 0 failed', out)
        self.assertIn('RESULT=PASS package=synthetic-2031-03-v1 content_sha256='
                      + self.pkg['content_sha'], out)
        self.assertIn('import #2=ALREADY_IMPORTED rows_written=0', out)
        self.assertIn('unchanged=True', out)
        self.assertIn('RESULT=PASS restore-test period=2031-03 version=1', out)
        self.assertEqual(sum(c.startswith('git clone ') for c in calls), 1)
        self.assertIn('icacls %s /inheritance:r /grant:r *S-1-5-32-544:(OI)(CI)F '
                      '*S-1-5-18:(OI)(CI)F' % s['evidence'], calls)
        self.assertEqual(package.verify(target).content_sha256,
                         self.pkg['content_sha'])
        for path in glob.glob(os.path.join(target, '**', '*'), recursive=True):
            if os.path.isfile(path):
                self.assertFalse(os.stat(path).st_mode & stat.S_IWRITE, path)
        self.assertEqual(glob.glob(os.path.join(s['evidence'], '2031-03',
                                                'incoming_*')), [])
        self.assertEqual(len(os.listdir(os.path.join(s['fin'], 'restore'))), 1)
        self.assertEqual(git(app, 'rev-parse', 'HEAD'), self.sha)

        out, calls = self.run_block(2)                      # повтор
        self.assertPassed(out)
        self.assertIn('PACKAGE_ALREADY_PLACED=' + target, out)
        self.assertFalse([c for c in calls if c.startswith('git clone ')])
        self.assertFalse([c for c in calls if c.startswith('git -C %s checkout' % app)])
        self.assertEqual(len(os.listdir(os.path.join(s['fin'], 'restore'))), 2)

        out, calls = self.run_block(3)
        self.assertPassed(out)
        self.assertRegex(out, r'STAGING_BACKUP=.* integrity=ok')
        self.assertIn('CLONE_MIGRATIONS_NOT_IN_STAGING_COPY=FIN_RECON_STORAGE_001', out)
        self.assertIn('MIGRATION=Done.', out)
        self.assertIn('IMPORT=IMPORTED', out)
        self.assertIn('RESULT=PASS verify period=2031-03 version=1', out)
        self.assertIn('PROD_AND_STAGING_UNCHANGED=yes', out)
        self.assertIn('sc.exe qc TransportReportStaging', calls)
        con = sqlite3.connect(clone_db)
        try:
            self.assertEqual(con.execute(
                'SELECT period, version, status, owner_approved_at, '
                'financially_closed_at FROM fin_recon_period_snapshot').fetchall(),
                [('2031-03', 1, 'RECONCILED_WITH_EXCEPTIONS', None, None)])
            self.assertEqual(con.execute('SELECT name FROM users').fetchall(),
                             [('staging-user',)])
        finally:
            con.close()
        backups = os.listdir(s['backups'])
        self.assertEqual(len(backups), 1)
        self.sites_unchanged()

        out, calls = self.run_block(3)                      # повтор
        self.assertStopped(out, 'the clone database already exists')
        self.assertEqual(os.listdir(s['backups']), backups)

        lines = self.server_lines()                         # порт занят чужим
        self.set_server_lines(lines + ['listen 5052 9999'])
        out, calls = self.run_block(4)
        self.assertStopped(out, 'port 5052 is used by process 9999')
        self.assertFalse([c for c in calls if c.startswith('Start-Process')])
        self.set_server_lines(lines)

        broken = {'/login': {'Status': 200, 'Body': '<html>maintenance</html>'},
                  '/drones/fin-recon': {'Status': 200, 'Body': LOGIN_BODY}}
        out, calls = self.run_block(4, Web=broken)          # клон не тот
        self.assertStopped(out, 'smoke check of the clone')
        self.assertIn('CLONE_STOPPED_AFTER_FAILED_CHECK pid=%d' % CLONE_PID, out)
        self.assertIn('Stop-Process %d' % CLONE_PID, calls)
        self.assertFalse(os.path.exists(pid_file))
        self.assertEqual(self.server_lines(), lines)

        out, calls = self.run_block(4)
        self.assertPassed(out)
        self.assertIn('SINCE_BLOCK3=same', out)
        self.assertIn('CLONE_VERIFY=PASS', out)
        self.assertIn('CLONE_STARTED pid=%d' % CLONE_PID, out)
        self.assertIn('CLONE_LOGIN=200', out)
        self.assertIn('FIREWALL_RULE=created VehicleSoft-FinRecon-Clone-5052 TCP '
                      '5052 from 10.103.0.0/16', out)
        self.assertIn('CLONE_URL=http://127.0.0.1:5052/drones/fin-recon', out)
        started = [c for c in calls if c.startswith('Start-Process')]
        self.assertEqual(started, [
            'Start-Process %s run_server.py cwd=%s window=Minimized passthru=True '
            'SECRET_KEY=hex64 PORT=5052 HOST=0.0.0.0 FLASK_ENV=sqlite_prod '
            'FUEL_API_TOKEN=unset DRONE_API_TOKEN=unset TG_BOT_TOKEN=unset'
            % (sys.executable, app)])
        self.assertIn('New-NetFirewallRule VehicleSoft-FinRecon-Clone-5052 Inbound '
                      'TCP 5052 10.103.0.0/16 Allow', calls)
        for line in ('ENV_AFTER PORT=7777', 'ENV_AFTER HOST=console-host',
                     'ENV_AFTER FLASK_ENV=', 'ENV_AFTER SECRET_KEY=unset',
                     'ENV_AFTER FUEL_API_TOKEN=set'):
            self.assertIn(line, out.split('\n'))
        with open(pid_file, encoding='ascii') as fh:
            record = json.load(fh)
        self.assertEqual(record, {'Id': CLONE_PID, 'Started': CLONE_TICKS})

        out, calls = self.run_block(4)                      # повтор
        self.assertPassed(out)
        self.assertIn('CLONE_ALREADY_RUNNING pid=%d' % CLONE_PID, out)
        self.assertIn('FIREWALL_RULE=exists VehicleSoft-FinRecon-Clone-5052', out)
        self.assertFalse([c for c in calls if c.startswith(('Start-Process', 'New-Net'))])

        second = s['second']
        out, calls = self.run_block(7, PartitionDisks=[0, 0, 1])
        self.assertStopped(out, 'write a drive of ANOTHER disk')
        self.assertIn('CANDIDATE E:\\ DISK=1 TYPE=Removable FREE_GB=20', out)
        self.assertNotIn('CANDIDATE C:', out)
        self.assertFalse(os.path.exists(second))
        os.makedirs(second)
        out, calls = self.run_block(7, values={'secondRoot': second},
                                    PartitionDisks=[0, 0])
        self.assertStopped(out, 'is on the same physical disk 0 as the archive')
        self.assertEqual(os.listdir(second), [])
        out, calls = self.run_block(7, values={'secondRoot': second})
        self.assertPassed(out)
        copy = os.path.join(second, 'fin-recon-evidence', '2031-03', 'v1')
        self.assertIn('SECOND_COPY_WRITTEN=%s (disk 1)' % copy, out)
        self.assertIn('SECOND_COPY=PASS', out)
        self.assertEqual(package.verify(copy).content_sha256, self.pkg['content_sha'])
        out, calls = self.run_block(7, values={'secondRoot': second})
        self.assertPassed(out)
        self.assertIn('SECOND_COPY_EXISTS=' + copy, out)
        with open(os.path.join(s['evidence'], '2031-03', 'COPIES.txt'),
                  encoding='utf-8-sig') as fh:
            self.assertEqual(len(fh.read().splitlines()), 2)

        inbox_zip = os.path.join(s['fin'], 'inbox', 'FULL_EVIDENCE_TEST.zip')
        full_dir = os.path.join(s['evidence'], '2031-03', 'v1-full-evidence')
        out, calls = self.run_block(8)
        self.assertStopped(out, 'put FULL_EVIDENCE_TEST.zip into')
        write_zip(inbox_zip, {'SOURCES/other.xlsx': b'x' * 4320})
        out, calls = self.run_block(8)
        self.assertStopped(out, 'does not hold exactly one file of 4321 bytes')
        self.assertFalse(os.path.exists(full_dir))
        os.remove(inbox_zip)
        member = bytes(range(256)) * 16 + b'y' * 225          # 4321 байт
        write_zip(inbox_zip, {'SOURCES/big_original.xlsx': member,
                              'notes.txt': b'synthetic'})
        out, calls = self.run_block(8)
        self.assertPassed(out)
        self.assertIn('FULL_EVIDENCE=%s BYTES=%d SHA256=%s' % (
            os.path.join(full_dir, 'FULL_EVIDENCE_TEST.zip'),
            os.path.getsize(inbox_zip), sha256_file(inbox_zip)), out)
        self.assertIn('LARGE_ORIGINAL_SHA256=%s BYTES=4321'
                      % hashlib.sha256(member).hexdigest(), out)
        out, calls = self.run_block(8)
        self.assertPassed(out)
        self.assertIn('FULL_EVIDENCE_ALREADY=', out)

        out, calls = self.run_block(6)                      # клон ещё работает
        self.assertStopped(out, 'the clone may still run')
        self.assertTrue(os.path.isdir(app))

        out, calls = self.run_block(5)
        self.assertPassed(out)
        self.assertIn('CLONE_STOPPED pid=%d' % CLONE_PID, out)
        self.assertIn('FIREWALL_RULE=removed VehicleSoft-FinRecon-Clone-5052', out)
        self.assertEqual([c for c in calls if c.startswith('Stop-Process')],
                         ['Stop-Process %d' % CLONE_PID])
        self.assertEqual(self.server_lines(), lines)

        out, calls = self.run_block(5)                      # повтор
        self.assertPassed(out)
        self.assertIn('CLONE_NOT_RUNNING (no record of a started clone)', out)
        self.assertIn('FIREWALL_RULE=absent', out)
        self.assertFalse([c for c in calls if c.startswith('Stop-Process')])

        out, calls = self.run_block(6)
        self.assertPassed(out)
        self.assertIn('CLONE_DELETED=' + app, out)
        self.assertFalse(os.path.exists(app))
        self.assertEqual(package.verify(target).content_sha256, self.pkg['content_sha'])
        self.assertEqual(os.listdir(s['backups']), backups)
        self.assertEqual(len(os.listdir(os.path.join(s['fin'], 'restore'))), 2)

        self.sites_unchanged()
        self.assertGitReadOnlyOnSites()

    # ── every stop ──────────────────────────────────────────────────────

    def test_another_computer_stops_every_block_before_anything(self):
        before = snapshot_tree(self.root)
        for number in range(1, 9):
            with self.subTest(block=number):
                out, calls = self.run_block(number, Host='SRV-OTHER')
                self.assertStopped(out, 'this computer is SRV-OTHER, not srv-test')
                self.assertEqual(calls, [])
        self.assertEqual(snapshot_tree(self.root, skip='run_'), before)

    def test_a_tampered_archive_is_refused_before_it_is_unpacked(self):
        with open(self.server['inbox_zip'], 'ab') as fh:
            fh.write(b'\0')
        out, _calls = self.run_block(2)
        self.assertStopped(out, 'has SHA-256')
        self.assertFalse(os.path.exists(os.path.join(self.server['evidence'], '2031-03')))

    def test_a_package_that_is_not_the_reviewed_one_is_kept_aside(self):
        out, _calls = self.run_block(2, values={'contentSha': 'f' * 64})
        self.assertStopped(out, 'the unpacked package did not verify')
        period = os.path.join(self.server['evidence'], '2031-03')
        self.assertFalse(os.path.exists(os.path.join(period, 'v1')))
        self.assertEqual(len(glob.glob(os.path.join(period, 'incoming_*'))), 1)

    def test_application_code_changed_after_the_reviewed_commit(self):
        origin = os.path.join(self.root, 'origin_changed')

        def later(root):
            with open(os.path.join(root, 'fin_recon', '__init__.py'), 'a',
                      encoding='utf-8') as fh:
                fh.write('# changed after review\n')
            return ['fin_recon/__init__.py']
        sha = make_origin(origin, later=later)
        out, calls = self.run_block(2, values={'repoUrl': origin, 'sha': sha})
        self.assertStopped(out, 'changes application files: fin_recon/__init__.py')
        self.assertFalse([c for c in calls if ' checkout ' in c])
        self.assertFalse(os.path.exists(os.path.join(self.server['evidence'], '2031-03')))

    def test_failing_tests_stop_before_the_archive(self):
        origin = os.path.join(self.root, 'origin_failing')

        def plant(root):
            with open(os.path.join(root, 'tests', 'test_fin_recon_contract.py'),
                      'a', encoding='utf-8') as fh:
                fh.write('\n\nclass Planted(unittest.TestCase):\n'
                         '    def test_planted(self):\n'
                         "        self.fail('planted failure')\n")
        sha = make_origin(origin, plant=plant)
        out, _calls = self.run_block(2, values={'repoUrl': origin, 'sha': sha})
        self.assertStopped(out, 'the tests did not pass')
        self.assertRegex(out, r'TESTS=Ran \d+ tests exit=1')
        self.assertFalse(os.path.exists(os.path.join(self.server['evidence'], '2031-03')))

    def test_a_stranger_with_the_clone_number_is_never_stopped(self):
        state = os.path.join(self.server['fin'], 'state')
        os.makedirs(state)
        record = os.path.join(state, 'clone.json')
        # Номер достался службе staging после перезагрузки: другое время старта.
        with open(record, 'w', encoding='ascii') as fh:
            json.dump({'Id': STAGING_PID, 'Started': CLONE_TICKS}, fh)
        out, calls = self.run_block(5)
        self.assertStopped(out, 'process %d is not the clone' % STAGING_PID)
        self.assertFalse([c for c in calls if c.startswith('Stop-Process')])
        self.assertTrue(os.path.exists(record))
        # То же время старта, но слушает 5051, а не 5052 -- тоже не клон.
        with open(record, 'w', encoding='ascii') as fh:
            json.dump({'Id': STAGING_PID, 'Started': SERVICE_TICKS}, fh)
        out, calls = self.run_block(5)
        self.assertStopped(out, 'process %d is not the clone' % STAGING_PID)
        self.assertFalse([c for c in calls if c.startswith('Stop-Process')])
        self.assertEqual(self.server_lines(), SERVICE_LINES)
        # Слушает 5052, но запущен не тогда, когда блок 4 записал клон: этот
        # процесс запустил кто-то другой -- блок его тоже не трогает.
        stranger = ['process %d %d' % (CLONE_PID, SERVICE_TICKS),
                    'listen 5052 %d' % CLONE_PID]
        self.set_server_lines(SERVICE_LINES + stranger)
        with open(record, 'w', encoding='ascii') as fh:
            json.dump({'Id': CLONE_PID, 'Started': CLONE_TICKS}, fh)
        out, calls = self.run_block(5)
        self.assertStopped(out, 'process %d is not the clone' % CLONE_PID)
        self.assertFalse([c for c in calls if c.startswith('Stop-Process')])
        self.assertEqual(self.server_lines(), SERVICE_LINES + stranger)

    def test_a_change_of_staging_during_a_block_is_reported(self):
        out, _calls = self.run_block(5, FlipFromCall=2,
                                     FlipService='TransportReportStaging')
        self.assertStopped(out, 'production or staging changed while this block ran')
        self.assertIn('TransportReportStaging=Stopped', out)

    def test_blocks_refuse_to_run_out_of_order(self):
        out, _calls = self.run_block(3)
        self.assertStopped(out, 'the app folder is missing -- run block 2 first')
        out, _calls = self.run_block(4)
        self.assertStopped(out, 'the clone is not created -- run block 3 first')
        out, _calls = self.run_block(7)
        self.assertStopped(out, 'the archive is missing -- run block 2 first')
        out, _calls = self.run_block(6)
        self.assertPassed(out)
        self.assertIn('CLONE_ABSENT=', out)


# ── the synthetic world ─────────────────────────────────────────────────────

LOGIN_BODY = '<form method="post" class="vs-login-form">'
CLONE_PID = 4242
CLONE_TICKS = 638640000000000000
PROD_PID = 3000
STAGING_PID = 3100
SERVICE_TICKS = 638600000000000000
SERVICE_LINES = ['process %d %d' % (PROD_PID, SERVICE_TICKS),
                 'process %d %d' % (STAGING_PID, SERVICE_TICKS),
                 'listen 5050 %d' % PROD_PID,
                 'listen 5051 %d' % STAGING_PID]


def _force_remove(func, path, _exc):
    os.chmod(path, stat.S_IWRITE)
    func(path)


def rmtree(path):
    """Remove a tree with read-only files (the package, git objects)."""
    if sys.version_info >= (3, 12):
        shutil.rmtree(path, onexc=_force_remove)
    else:
        shutil.rmtree(path, onerror=_force_remove)


def copy_as_in_git(source, dest):
    """Copy with LF line ends, as the files are stored in git.

    [REASON]: on the Windows runner the working copy has CRLF; committed
    as-is into the synthetic origin, the blobs would carry CRLF, and a
    checkout with core.autocrlf could report them modified and make block 2
    check the code out again. LF blobs behave as the real repository does.
    """
    with open(source, 'rb') as fh:
        data = fh.read().replace(b'\r\n', b'\n')
    with open(dest, 'wb') as fh:
        fh.write(data)


def repo_files():
    """What the blocks run in the app folder, at their repository paths."""
    names = [os.path.relpath(p, REPO_ROOT).replace(os.sep, '/')
             for p in glob.glob(os.path.join(REPO_ROOT, 'fin_recon', '*.py'))]
    names += ['tools/fin_recon_storage.py', 'tools/check_migration_drift.py',
              'migrate_fin_recon_storage_001.py', 'migration_utils.py',
              'backup_transport_db.py', 'tests/fin_recon_fixture.py']
    names += [m.replace('.', '/') + '.py' for m in TEST_MODULES]
    return sorted(names)


def make_origin(root, plant=None, later=None):
    """Synthetic origin: the reviewed commit, then one later commit."""
    os.makedirs(root)
    git(root, 'init', '-q')
    git(root, 'symbolic-ref', 'HEAD', 'refs/heads/' + BRANCH)
    names = repo_files()
    for rel in names:
        dest = os.path.join(root, *rel.split('/'))
        os.makedirs(os.path.dirname(dest), exist_ok=True)
        copy_as_in_git(os.path.join(REPO_ROOT, *rel.split('/')), dest)
    if plant:
        plant(root)
    git(root, 'add', '--', *names)
    git(root, 'commit', '-q', '-m', 'reviewed')
    sha = git(root, 'rev-parse', 'HEAD')
    if later:
        changed = later(root)
    else:
        os.makedirs(os.path.join(root, 'docs'))
        with open(os.path.join(root, 'docs', 'after.md'), 'w', encoding='ascii') as fh:
            fh.write('pinned after review\n')
        changed = ['docs/after.md']
    git(root, 'add', '--', *changed)
    git(root, 'commit', '-q', '-m', 'later')
    return sha


def write_zip(path, members):
    with zipfile.ZipFile(path, 'w', zipfile.ZIP_DEFLATED) as zf:
        for name in sorted(members):
            zf.writestr(name, members[name])


def make_package(world):
    folder = os.path.join(world, 'pkg')
    fx.write_package(folder)
    members = {}
    for dirpath, dirnames, filenames in os.walk(folder):
        dirnames.sort()
        for name in sorted(filenames):
            full = os.path.join(dirpath, name)
            with open(full, 'rb') as fh:
                members[os.path.relpath(full, folder).replace(os.sep, '/')] = fh.read()
    zip_path = os.path.join(world, 'package.zip')
    write_zip(zip_path, members)
    return {'zip': zip_path, 'zip_sha': sha256_file(zip_path),
            'content_sha': package.verify(folder).content_sha256}


def make_server(root, pkg):
    s = {'fin': os.path.join(root, 'fin'),
         'evidence': os.path.join(root, 'evidence'),
         'backups': os.path.join(root, 'backups'),
         'second': os.path.join(root, 'second'),
         'state': os.path.join(root, 'server_state.txt'),
         'prod': os.path.join(root, 'prod'),
         'staging': os.path.join(root, 'staging')}
    for site in ('prod', 'staging'):
        folder = s[site]
        os.makedirs(os.path.join(folder, 'instance'))
        git(folder, 'init', '-q')
        with open(os.path.join(folder, 'README.txt'), 'w', encoding='ascii') as fh:
            fh.write('synthetic %s checkout\n' % site)
        names = ['README.txt']
        if site == 'staging':
            os.makedirs(os.path.join(folder, 'tools'))
            copy_as_in_git(os.path.join(REPO_ROOT, 'tools', 'state_snapshot.py'),
                           os.path.join(folder, 'tools', 'state_snapshot.py'))
            names.append('tools/state_snapshot.py')
        git(folder, 'add', '--', *names)
        git(folder, 'commit', '-q', '-m', 'synthetic ' + site)
        s[site + '_head'] = git(folder, 'rev-parse', 'HEAD')
    # Production: the file is never opened -- random bytes prove it.
    s['prod_db'] = os.path.join(s['prod'], 'instance', 'transport.db')
    with open(s['prod_db'], 'wb') as fh:
        fh.write(hashlib.sha256(b'production').digest() * 64)
    s['staging_db'] = os.path.join(s['staging'], 'instance', 'transport.db')
    con = sqlite3.connect(s['staging_db'])
    con.executescript(
        "CREATE TABLE schema_migrations (id INTEGER PRIMARY KEY AUTOINCREMENT, "
        "name TEXT UNIQUE NOT NULL, applied_at DATETIME NOT NULL, checksum TEXT, "
        "description TEXT);"
        "INSERT INTO schema_migrations (name, applied_at) VALUES "
        "('SYNTHETIC_OLD_001', '2026-01-01 00:00:00'), "
        "('SYNTHETIC_OLD_002', '2026-02-01 00:00:00');"
        "CREATE TABLE users (id INTEGER PRIMARY KEY, name TEXT);"
        "INSERT INTO users (name) VALUES ('staging-user');")
    con.close()
    for key in ('prod_db', 'staging_db'):
        s[key + '_sha'] = sha256_file(s[key])
    os.makedirs(os.path.join(s['fin'], 'inbox'))
    s['inbox_zip'] = os.path.join(s['fin'], 'inbox', 'FIN_RECON_EVIDENCE_2031-03_v1.zip')
    shutil.copyfile(pkg['zip'], s['inbox_zip'])
    with open(s['state'], 'w', encoding='ascii') as fh:
        fh.write('\n'.join(SERVICE_LINES) + '\n')
    return s


def snapshot_tree(root, skip=None):
    """Every file under root with its size -- to prove nothing was written."""
    found = {}
    for dirpath, dirnames, filenames in os.walk(root):
        if skip:
            dirnames[:] = [d for d in dirnames if not d.startswith(skip)]
        for name in filenames:
            path = os.path.join(dirpath, name)
            if skip and os.path.basename(os.path.dirname(path)).startswith(skip):
                continue
            found[os.path.relpath(path, root)] = os.path.getsize(path)
    return found


if __name__ == '__main__':
    unittest.main()
