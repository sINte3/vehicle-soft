# -*- coding: utf-8 -*-
"""Выпуск v1.35 на production: docs/FIN_RECON_003_RELEASE_V135_RUNBOOK.md.

FIN-RECON-003: защита июльского импорта дронов (PR #260, #261, #269).
Production -- `a73d756` (выпуск v1.34); выпуск закреплён на изолированном
коммите `90516935`: v1.34 + ветка PR #261 + патч, без GPS/agro-work из
`main`. Без миграции. Импорт июля выпуском не выполняется.

Шаги 2, 3 и откат -- блоки выпуска v1.34 (прошли на этом сервере 04.11, их
пути «стоп» исполняются tests/test_gps_release_v134.py), в которых сменились
только постоянные. Новые -- шаг 1 (репетиция на копии вместо занятого
staging) и шаг 4 (preflight после выпуска). Здесь держится:

  * текст: нет плейсхолдеров и `&&`, ASCII без табуляций, постоянные
    настоящие, журналы названы те, что пишут блоки;
  * блоки 2, 3 и откат -- блоки v1.34 слово в слово, кроме постоянных;
  * шаги 1 и 4 ничего не меняют: ни служб, ни перемотки, ни записи; git на
    production -- только чтение, checkout -- только в новой папке копии;
  * preflight в ops/ -- тот самый, что прошёл стенд 05.11 (SHA-256 байтов
    CRLF), а блоки сверяют именно этот SHA;
  * имена проверок справочника, которые блоки прощают, -- те, что preflight
    печатает, и среди них нет ни одной проверки выпуска;
  * дельта выпуска -- ровно три файла кода, без миграций;
  * функция вердикта исполняется в PowerShell на подставных выводах
    preflight (pwsh 7 и -- в CI -- Windows PowerShell 5.1).

Сами шаги 1 и 4 целиком прогнаны против подставного сервера (настоящий git,
настоящий preflight, синтетическая база) при подготовке выпуска; итог -- в
описании PR. Здесь такой стенд не строится: в CI клон глубиной 1.

Запуск: python -m unittest tests.test_fin_recon_003_release_v135 -v
Функции в PowerShell: FIN_RECON_RELEASE_POWERSHELL=pwsh (или powershell).
"""

import hashlib
import os
import re
import subprocess
import tempfile
import unittest

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
RELEASE = os.path.join(REPO_ROOT, 'docs', 'FIN_RECON_003_RELEASE_V135_RUNBOOK.md')
V134 = os.path.join(REPO_ROOT, 'docs', 'GPS_RELEASE_V134_RUNBOOK.md')
THIS_TEST = 'tests/test_fin_recon_003_release_v135.py'
# Production на момент подготовки: выпуск v1.34 (docs/DEPLOYED.md,
# «Текущий релиз» в docs/RELEASE_GATE.md).
BASELINE = 'a73d756dcb67c06388e58bc7c4a0a1ac64e59e34'
# Изолированный коммит выпуска (PR #269, вариант A): его и только его
# проверяли сессия, стенд preflight и CI.
PINNED = '90516935fbd45a977c8c855d24e9385e621a437a'
# Слияние ветки PR #261 в v1.34 внутри выпуска; ранбук называет его в шаге 2.
PR261_MERGE = 'b4cd7d2'
PREFLIGHT = 'ops/fin_recon_003/FIN_RECON_003_JULY_PREFLIGHT_READONLY.ps1'
# [REASON]: SHA-256 байтов CRLF, которые прошли стенд preflight 05.11
# (186/186, Windows PowerShell 5.1). В git файл лежит с LF; блоки собирают
# CRLF из `git show` и сверяют этот SHA, поэтому сервер исполняет ровно
# проверенные байты.
PREFLIGHT_SHA = '7d1823ef9edc97fa7b955a6cc1e5e6944d06328758cb3ab04c884ce08aa3e46d'
CORE_SHA = '30298d1962e9b346071ddcaae500c61e61a8f91dda6a7810da2389260227da7b'
RUNTIME = ['tools/drone_works_fingerprint_export.py', 'tools/drone_works_selective.py',
           'tools/import_drone_works.py']
SUITES = ['tools.test_import_drone_works_fin_recon_h5', 'tools.test_import_drone_works',
          'tools.test_import_drone_works_counterless', 'tools.test_drone_works_selective',
          'tools.test_drone_works_fingerprint_export', 'tests.test_drone_works_upload_002',
          'tests.test_drone_works_screens', 'tests.test_drone_payer_unidentified',
          'tests.test_drone_works_migration']
# [REASON]: проверки справочника, закреплённые за снимком 05.11. Справочник
# живёт (новые карточки и работы других месяцев), а выпуск его не читает и
# не пишет; их расхождение -- замечание к импорту июля, не стоп выпуска.
DIRECTORY = ['customers', 'aliases_all', 'customers_904_1009', 'works_total'] + [
    'hash_%s_%s' % (kind, band) for kind in ('customers', 'aliases_to')
    for band in ('le_903', '904_996', '997_1009', 'gt_1009')]
COPIED = ('Шаг 2', 'Шаг 3', 'Откат')
NEW = ('Шаг 1', 'Шаг 4')
LOGS = {'Шаг 1': 'release_v135_step1.log', 'Шаг 2': 'release_v135_step2.log',
        'Шаг 3': 'release_v135_step3.log', 'Шаг 4': 'release_v135_step4.log',
        'Откат': 'release_v135_rollback.log'}


def text(path=RELEASE):
    with open(path, encoding='utf-8') as fh:
        return fh.read()


def sections(path=RELEASE):
    return {m.group(1): m.group(2) for m in re.finditer(
        r'^## ([^\n]*)\n(.*?)(?=^## |\Z)', text(path), re.S | re.M)}


def block(prefix, path=RELEASE):
    """Большой блок `& { ... }` раздела, чей заголовок начинается с prefix."""
    found = [body for title, body in sections(path).items() if title.startswith(prefix)]
    assert len(found) == 1, (prefix, len(found))
    blocks = [b for b in re.findall(r'```powershell\n(.*?)```', found[0], re.S)
              if b.startswith('& {')]
    assert len(blocks) == 1, (prefix, len(blocks))
    return blocks[0]


def constants(body):
    return {m.group(1): m.group(2).strip() for m in re.finditer(
        r'^\$([A-Za-z]+)\s*= (.+)$', body, re.M)}


def functions(body):
    return {m.group(1): m.group(0) for m in re.finditer(
        r'^function ([A-Za-z-]+)\(.*?^\}$', body, re.M | re.S)}


def lines(body=None):
    found = re.findall(r'```powershell\n(.*?)```', text() if body is None else body, re.S)
    return [line for b in found for line in b.splitlines() if line.strip()]


def git(*args):
    return subprocess.run(['git'] + list(args), cwd=REPO_ROOT, capture_output=True,
                          text=True, check=True).stdout


def has_commit(commit):
    return subprocess.run(['git', 'cat-file', '-e', commit + '^{commit}'], cwd=REPO_ROOT,
                          capture_output=True).returncode == 0


def preflight_bytes():
    with open(os.path.join(REPO_ROOT, *PREFLIGHT.split('/')), 'rb') as fh:
        return fh.read()


class ReleaseV135Runbook(unittest.TestCase):

    def test_five_blocks_ascii_without_tabs(self):
        # Консоль сервера -- Windows PowerShell 5.1 с кодовой страницей OEM, а
        # табуляция при вставке мышью уходит в автодополнение.
        bodies = re.findall(r'```powershell\n(.*?)```', text(), re.S)
        self.assertEqual(len(bodies), 5)
        for body in bodies:
            self.assertTrue(body.isascii(), body[:80])
            self.assertNotIn('\t', body)
            self.assertTrue(body.startswith('& {\n'))
            self.assertTrue(body.endswith('\n}\n'))

    def test_no_placeholders_and_no_double_ampersand(self):
        for line in lines():
            self.assertNotRegex(line, r'<[^>]*>', line)
            self.assertNotIn('&&', line)
            self.assertNotIn('@', line.replace('@(', '').replace('@{', '').replace('@arguments', ''), line)
            for word in ('YYYY', 'XXX', 'TODO', '...'):
                self.assertNotIn(word, line, line)
        self.assertNotRegex(text(), r'@[A-Za-z0-9_]+@')

    def test_the_copied_blocks_are_those_of_v134_but_the_constants(self):
        # Блоки v1.34 прошли на этом сервере 04.11, их пути «стоп» исполняет
        # tests/test_gps_release_v134.py. Здесь другие только production, с
        # которого выпуск начинается, коммит выпуска, тест ранбука и журнал.
        def bare(body):
            found = constants(body)
            for key in ('baseline', 'reviewed', 'release', 'runbookTest', 'log'):
                if key in found:
                    body = re.sub(r'^\$%s(\s*)= .+$' % key, '$%s = @%s@' % (key, key),
                                  body, flags=re.M)
            return body
        for name in COPIED:
            with self.subTest(block=name):
                self.assertEqual(bare(block(name)), bare(block(name, path=V134)))
                self.assertNotEqual(block(name), block(name, path=V134))

    def test_the_constants_are_the_real_ones(self):
        for name in COPIED + NEW:
            found = constants(block(name))
            with self.subTest(block=name):
                self.assertEqual(found['log'], "'C:\\VehicleSoft_Release\\%s'" % LOGS[name])
                self.assertEqual(found['work'], "'C:\\VehicleSoft_Release'")
                self.assertEqual(found['prod'], "'C:\\transport-report'")
                if 'baseline' in found:
                    self.assertEqual(found['baseline'], "'%s'" % BASELINE)
                for key in ('reviewed', 'release'):
                    if key in found:
                        self.assertEqual(found[key], "'%s'" % PINNED)
                if 'runbookTest' in found:
                    self.assertEqual(found['runbookTest'], "'%s'" % THIS_TEST)
                if name in NEW:
                    self.assertEqual(found['db'], "'C:\\transport-report\\instance\\transport.db'")
                    self.assertEqual(found['py'], "'C:\\Program Files\\Python314\\python.exe'")
                    self.assertEqual(found['preflight'], "'%s'" % PREFLIGHT)
                    self.assertEqual(found['preflightSha'], "'%s'" % PREFLIGHT_SHA)
                    self.assertEqual(found['directory'],
                                     '@(%s)' % ', '.join("'%s'" % n for n in DIRECTORY))
        self.assertEqual(os.path.abspath(__file__),
                         os.path.join(REPO_ROOT, *THIS_TEST.split('/')))
        step1 = constants(block('Шаг 1'))
        self.assertEqual(step1['runtime'], '@(%s)' % ', '.join("'%s'" % n for n in RUNTIME))
        self.assertEqual(step1['suites'], '@(%s)' % ', '.join("'%s'" % n for n in SUITES))
        self.assertEqual(step1['qa'], "'C:\\VehicleSoft_FR3_QA'")
        # Журналы, которые ранбук велит прислать, -- ровно те, что пишут блоки.
        prose = re.sub(r'```powershell\n.*?```', '', text(), flags=re.S)
        self.assertEqual(set(re.findall(r'release_v\d+_[a-z0-9_]+\.log', prose)),
                         set(LOGS.values()))

    def test_the_new_steps_change_nothing(self):
        allowed_on_prod = {'rev-parse', 'status', 'config', 'show'}
        for name in NEW:
            body = block(name)
            with self.subTest(block=name):
                for word in ('Stop-Service', 'Restart-Service', 'Start-Service', 'merge --ff-only',
                             "'merge'", 'reset', '--apply', 'backupBat', 'Remove-Item', 'git fetch',
                             'git pull', 'git push', 'rebase', 'stash', 'git clean', 'git tag',
                             'Set-Content', 'Out-File', 'nssm'):
                    self.assertNotIn(word, body)
                self.assertNotRegex(body, r'migrate_[A-Za-z0-9_]+\.py')
                for target, sub in re.findall(r'& git -C (\$[a-z]+) ([a-z-]+)', body):
                    if target == '$prod':
                        self.assertIn(sub, allowed_on_prod)
                    else:
                        self.assertEqual(target, '$clone')
                        self.assertIn(sub, {'checkout', 'rev-parse', 'merge-base', 'diff', 'show'})
                bare = re.findall(r'& git (?!-C )([a-z-]+)', body)
                self.assertEqual(bare, ['clone'] if name == 'Шаг 1' else [])
                # Одна запись на диск помимо журнала -- файл preflight, и тот
                # в новой папке этого прогона.
                self.assertEqual(body.count('WriteAllText('), 1)
                self.assertIn("[IO.File]::WriteAllText($ps1, (($body -join \"`r`n\") + \"`r`n\"), "
                              "[Text.Encoding]::ASCII)", body)
                self.assertIn('-ExpectedHead $release', body)
                self.assertIn('$note = Get-Verdict (Invoke-Preflight ', body)
        step1 = block('Шаг 1')
        self.assertEqual(step1.count('checkout'), 1)
        self.assertIn('& git -C $clone checkout --quiet --detach $release', step1)
        self.assertIn("$run = Join-Path $qa ('run_' + (Get-Date -Format 'yyyyMMdd_HHmmss'))", step1)
        self.assertIn("Invoke-Preflight $clone 'TransportReport' ", step1)
        self.assertIn("Invoke-Preflight $clone 'TransportReport_NOT_EXISTING' ", step1)
        self.assertIn("Invoke-Preflight $prod 'TransportReport' ", block('Шаг 4'))
        # Адрес GitHub может нести учётные данные: вывод clone не печатается.
        self.assertIn('& git clone --quiet $origin $clone 2>&1 | Out-Null', step1)
        self.assertNotRegex(step1, r'Write-Host[^\n]*\$origin')

    def test_the_new_steps_share_their_helpers(self):
        one, four = functions(block('Шаг 1')), functions(block('Шаг 4'))
        self.assertEqual(set(one), {'Invoke-Preflight', 'Get-Verdict'})
        self.assertEqual(one, four)
        verdict = one['Get-Verdict']
        for needle in ("'RESULT=*'", "'IMPORT_AUTHORIZED=NO*'", '"CODE_RELEASE_PIN=$release"',
                       "'CHECK code_head_is_pinned_release=PASS*'", "'SERVICE_HEALTH=VERIFIED'",
                       "'RESULT=PREFLIGHT_PASSED'", "'RESULT=PREFLIGHT_FAILED'",
                       '$directory -notcontains $_'):
            self.assertIn(needle, verdict)

    def test_the_preflight_file_is_the_reviewed_one(self):
        raw = preflight_bytes()
        self.assertTrue(raw.isascii())
        crlf = raw.replace(b'\r\n', b'\n').replace(b'\n', b'\r\n')
        self.assertEqual(hashlib.sha256(crlf).hexdigest(), PREFLIGHT_SHA)
        source = raw.decode('ascii')
        self.assertIn("$CoreSha256 = '%s'" % CORE_SHA, source)
        self.assertIn("[string]$ExpectedHead = ''", source)
        self.assertIn("[string[]]$RequiredServices = @('TransportReport')", source)
        for line in ('RESULT=PREFLIGHT_PASSED', 'RESULT=PREFLIGHT_FAILED', 'SERVICE_HEALTH=VERIFIED',
                     'SERVICE_HEALTH=NOT_VERIFIED (', 'IMPORT_AUTHORIZED=NO (',
                     "'CODE_RELEASE_PIN=' + $(if ($pinned) { $ExpectedHead }"):
            self.assertIn(line, source)

    def test_the_forgiven_checks_are_directory_checks_the_preflight_prints(self):
        source = preflight_bytes().decode('ascii')
        for name in ('customers', 'aliases_all', 'customers_904_1009', 'works_total'):
            self.assertIn("out.check('%s'," % name, source)
        self.assertIn("ranges = (('le_903', 0, 903), ('904_996', 904, 996),", source)
        self.assertIn("('997_1009', 997, 1009), ('gt_1009', 10" , source)
        self.assertIn("key = 'customers_' + label", source)
        self.assertIn("key = 'aliases_to_' + label", source)
        self.assertEqual(source.count("out.check('hash_' + key,"), 2)
        # Ни одна проверка выпуска не прощается: код, поведение, июль, R01,
        # R02, №136/138, миграции, таблицы.
        release_checks = set(re.findall(r"out\.check\('([a-z0-9_]+)'", source))
        for name in ('code_head_is_pinned_release', 'july_works', 'customer_%d_type',
                     'migrations_registered', 'r02_key_not_in_db', 'works_from_five_books_exact',
                     'web_upload_july_preview_pending', 'tables_present'):
            self.assertIn(name.split('%')[0], ' '.join(release_checks) + ' ' + source)
        for name in DIRECTORY:
            self.assertFalse(name.startswith(('code_', 'behaviour_', 'july', 'customer_1',
                                              'folded_', 'r02', 'works_from', 'web_', 'migrations',
                                              'tables', 'unique', 'copy', 'drone_works')), name)
        self.assertNotIn('hash_july_works_unchanged_since_0411', DIRECTORY)

    def test_the_suites_exist(self):
        for suite in SUITES:
            path = os.path.join(REPO_ROOT, *suite.split('.')) + '.py'
            self.assertTrue(os.path.isfile(path), suite)
            if has_commit(PINNED):
                git('cat-file', '-e', '%s:%s.py' % (PINNED, suite.replace('.', '/')))

    def test_the_release_delta_is_three_files_without_migrations(self):
        if not (has_commit(BASELINE) and has_commit(PINNED)):
            self.skipTest('the release history is outside this clone')
        self.assertEqual(subprocess.run(['git', 'merge-base', '--is-ancestor', BASELINE, PINNED],
                                        cwd=REPO_ROOT).returncode, 0)
        names = git('diff', '--name-only', BASELINE, PINNED).split()
        self.assertEqual([n for n in names if n.startswith('migrate_')], [])
        code = sorted(n for n in names if not n.startswith(('docs/', '.github/', 'tools/test_')))
        self.assertEqual(code, RUNTIME)
        merges = git('log', '--merges', '--format=%h %s', '%s..%s' % (BASELINE, PINNED))
        self.assertIn(PR261_MERGE + ' ', merges)
        self.assertIn('Merge pull request #260 ', merges)
        head = git('rev-parse', 'HEAD').strip()
        self.assertEqual(subprocess.run(['git', 'merge-base', '--is-ancestor', PINNED, head],
                                        cwd=REPO_ROOT).returncode, 0)

    def test_the_short_hashes_in_the_prose_are_the_pinned_ones(self):
        prose = re.sub(r'```powershell\n.*?```', '', text(), flags=re.S)
        shown = set(re.findall(r'`([0-9a-f]{7})(?:\.\.\.)?`', prose))
        self.assertEqual(shown, {BASELINE[:7], PINNED[:7], PR261_MERGE})
        self.assertIn('`RELEASE: %s... -> %s...`' % (BASELINE[:7], PINNED[:8]), prose)
        self.assertIn(PINNED, prose)

    def test_every_stop_in_the_table_is_one_a_block_prints(self):
        table = [line for line in text().splitlines() if line.startswith('| `RESULT: STOP - ')]
        self.assertGreaterEqual(len(table), 20)
        blocks = ''.join(block(name) for name in COPIED + NEW)
        for line in table:
            shown = re.match(r'\| `RESULT: STOP - ([^`]*)`', line).group(1)
            words = shown.split('...')[0].strip()
            with self.subTest(stop=words):
                self.assertTrue(words)
                self.assertIn(words, blocks)

    def test_the_gate_names_this_release_as_the_next(self):
        with open(os.path.join(REPO_ROOT, 'docs', 'RELEASE_GATE.md'), encoding='utf-8') as fh:
            gate = fh.read()
        row = [line for line in gate.splitlines() if line.startswith('| Следующий выпуск |')]
        self.assertEqual(len(row), 1)
        self.assertIn('v1.35', row[0])
        self.assertIn(PINNED, row[0])
        self.assertIn('docs/FIN_RECON_003_RELEASE_V135_RUNBOOK.md', row[0])

    def test_no_secret_and_no_person_names(self):
        body = text()
        self.assertNotRegex(body, r'password=(?!`)\S')
        for word in ('token', 'фукаро', 'бобо', 'когон'):
            self.assertNotIn(word, body.lower())


POWERSHELL = os.environ.get('FIN_RECON_RELEASE_POWERSHELL')
GOOD = ['PREFLIGHT=FIN_RECON_003_JULY_READONLY_V2 stamp=x host=x',
        'CODE_RELEASE_PIN=' + PINNED,
        'SERVICE_REQUIRED TransportReport=Running start=Automatic',
        'CHECK code_head_is_pinned_release=PASS expected=%s actual=%s' % (PINNED[:12], PINNED[:12]),
        'CHECK customer_136_type=PASS expected=external actual=external',
        'CHECK customers=PASS expected=1009 actual=1009',
        'TEMP_COPY_REMOVED=YES', 'DB_CODE_PREFLIGHT=OK checks_failed=0',
        'SERVICE_HEALTH=VERIFIED', 'RESULT=PREFLIGHT_PASSED',
        'IMPORT_AUTHORIZED=NO (a technical preflight is not an approval; the signed acceptance act is separate)']


def changed(lines_in, **swap):
    out = []
    for line in lines_in:
        for old, new in swap.items():
            if line.startswith(old.replace('_', ' ', 1) if old.startswith('CHECK_') else old):
                line = new
        if line is not None:
            out.append(line)
    return out


@unittest.skipUnless(POWERSHELL, 'FIN_RECON_RELEASE_POWERSHELL is not set')
class ReleaseV135BlocksInPowerShell(unittest.TestCase):
    """Блоки разбираются самим PowerShell, а вердикт исполняется на выводах preflight."""

    def test_every_block_parses(self):
        for name in COPIED + NEW:
            with self.subTest(block=name), tempfile.TemporaryDirectory() as tmp:
                path = os.path.join(tmp, 'block.ps1')
                with open(path, 'w', encoding='ascii', newline='\r\n') as fh:
                    fh.write(block(name))
                probe = ('$e = $null; $t = $null; '
                         '[void][System.Management.Automation.Language.Parser]::ParseFile("%s", [ref]$t, [ref]$e); '
                         'Write-Output ("ERRORS " + $e.Count); $e | ForEach-Object { Write-Output $_.Message }'
                         % path)
                out = subprocess.run([POWERSHELL, '-NoProfile', '-NonInteractive', '-Command', probe],
                                     capture_output=True, text=True, timeout=120)
                self.assertIn('ERRORS 0', out.stdout, out.stdout + out.stderr)

    def verdict(self, preflight_lines):
        """Get-Verdict шага 1 на этих строках: ('NOTE', текст) или ('STOP', текст)."""
        body = block('Шаг 1')
        found = constants(body)
        script = '\n'.join([
            '$ErrorActionPreference = "Continue"',
            '$release = %s' % found['release'],
            '$directory = %s' % found['directory'],
            functions(body)['Get-Verdict'],
            '$text = @(Get-Content -LiteralPath $args[0] -Encoding ASCII)',
            'try { $n = Get-Verdict $text; Write-Output ("NOTE:" + $n) }',
            'catch { Write-Output ("STOP:" + $_.Exception.Message) }',
        ]) + '\n'
        with tempfile.TemporaryDirectory() as tmp:
            data = os.path.join(tmp, 'preflight.txt')
            with open(data, 'w', encoding='ascii', newline='\r\n') as fh:
                fh.write('\n'.join(preflight_lines) + '\n')
            path = os.path.join(tmp, 'verdict.ps1')
            with open(path, 'w', encoding='ascii', newline='\r\n') as fh:
                fh.write(script)
            out = subprocess.run([POWERSHELL, '-NoProfile', '-NonInteractive', '-ExecutionPolicy',
                                  'Bypass', '-File', path, data], capture_output=True, text=True,
                                 timeout=120)
        got = [l for l in out.stdout.splitlines() if l.startswith(('NOTE:', 'STOP:'))]
        self.assertEqual(len(got), 1, out.stdout + out.stderr)
        kind, _, message = got[0].partition(':')
        return kind, message

    def test_a_passed_preflight_passes_without_a_note(self):
        self.assertEqual(self.verdict(GOOD), ('NOTE', ''))

    def failed(self, *names, result='RESULT=PREFLIGHT_FAILED'):
        out = [l for l in GOOD if not l.startswith(('RESULT=', 'DB_CODE_PREFLIGHT='))]
        at = out.index('TEMP_COPY_REMOVED=YES')
        out[at:at] = ['CHECK %s=FAIL expected=a actual=b' % n for n in names]
        out.insert(out.index('IMPORT_AUTHORIZED=NO (a technical preflight is not an approval; '
                             'the signed acceptance act is separate)'), result)
        return out

    def test_directory_drift_alone_is_a_note(self):
        kind, message = self.verdict(self.failed('customers', 'hash_customers_gt_1009',
                                                 'hash_aliases_to_997_1009', 'works_total'))
        self.assertEqual(kind, 'NOTE')
        self.assertIn('the customer directory changed since 05.11', message)
        self.assertIn('customers, hash_customers_gt_1009, hash_aliases_to_997_1009, works_total',
                      message)

    def test_every_release_check_stops(self):
        for name in ('customer_136_type', 'july_works', 'hash_july_works_unchanged_since_0411',
                     'behaviour_legacy_and_web_july_write_refused', 'code_blob_head_import_drone_works',
                     'migrations_registered', 'r02_key_not_in_db', 'folded_key_r01_kogon_fukaro_cards',
                     'web_upload_july_preview_pending'):
            with self.subTest(check=name):
                kind, message = self.verdict(self.failed('customers', name))
                self.assertEqual(kind, 'STOP')
                self.assertIn('failed checks: ' + name, message)

    def test_what_the_verdict_refuses(self):
        cases = {
            'the RESULT line is missing': [l for l in GOOD if not l.startswith('RESULT=')],
            'two RESULT lines': GOOD + ['RESULT=PREFLIGHT_PASSED'],
            'no IMPORT_AUTHORIZED=NO': [l for l in GOOD if not l.startswith('IMPORT_AUTHORIZED')],
            'pinned elsewhere': ['CODE_RELEASE_PIN=' + BASELINE if l.startswith('CODE_RELEASE_PIN=')
                                 else l for l in GOOD],
            'not pinned': ['CODE_RELEASE_PIN=NOT_GIVEN' if l.startswith('CODE_RELEASE_PIN=')
                           else l for l in GOOD],
            'head check failed': [l.replace('=PASS', '=FAIL') if 'code_head_is_pinned' in l else l
                                  for l in GOOD],
            'service down': ['SERVICE_HEALTH=NOT_VERIFIED (TransportReport Stopped)'
                             if l == 'SERVICE_HEALTH=VERIFIED' else l for l in GOOD],
            'incomplete': ['RESULT=PREFLIGHT_INCOMPLETE python exit 1' if l.startswith('RESULT=')
                           else l for l in GOOD],
            'failed with no failed check': self.failed(),
            'not pinned result': ['RESULT=CODE_RELEASE_NOT_PINNED (rerun)' if l.startswith('RESULT=')
                                  else l for l in GOOD],
        }
        for label, case in cases.items():
            with self.subTest(case=label):
                self.assertEqual(self.verdict(case)[0], 'STOP')

    def test_the_negative_control_discriminates(self):
        # Тот же стенд с функцией, которая прощает всё, пропустил бы стоп:
        # значит, проверка выше различает верную и неверную функцию.
        body = block('Шаг 1')
        broken = functions(body)['Get-Verdict'].replace('$directory -notcontains $_',
                                                        '$false')
        self.assertNotEqual(broken, functions(body)['Get-Verdict'])
        found = constants(body)
        script = '\n'.join([
            '$release = %s' % found['release'], '$directory = %s' % found['directory'], broken,
            '$text = @(Get-Content -LiteralPath $args[0] -Encoding ASCII)',
            'try { $n = Get-Verdict $text; Write-Output ("NOTE:" + $n) }',
            'catch { Write-Output ("STOP:" + $_.Exception.Message) }']) + '\n'
        with tempfile.TemporaryDirectory() as tmp:
            data = os.path.join(tmp, 'preflight.txt')
            with open(data, 'w', encoding='ascii', newline='\r\n') as fh:
                fh.write('\n'.join(self.failed('customer_136_type')) + '\n')
            path = os.path.join(tmp, 'verdict.ps1')
            with open(path, 'w', encoding='ascii', newline='\r\n') as fh:
                fh.write(script)
            out = subprocess.run([POWERSHELL, '-NoProfile', '-NonInteractive', '-ExecutionPolicy',
                                  'Bypass', '-File', path, data], capture_output=True, text=True,
                                 timeout=120)
        self.assertIn('NOTE:', out.stdout, out.stdout + out.stderr)


if __name__ == '__main__':
    unittest.main()
