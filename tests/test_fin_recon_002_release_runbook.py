# -*- coding: utf-8 -*-
"""docs/FIN_RECON_002_RELEASE_V132_RUNBOOK.md: blocks pasted verbatim into the production console.

The same discipline as the release runbooks before it (tests/test_gps_release_v131.py):
no placeholders, ASCII, the pins are the real ones, every string a block waits
for is one the tool really prints, every STOP of the table is one a block
prints, the helper functions are those of v1.31 word for word. On a machine
with Windows PowerShell (CI job windows-powershell-51, or the workstation)
each block is parsed by PowerShell itself.

Run: python -m unittest tests.test_fin_recon_002_release_runbook -v
"""
import glob
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
import unittest

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
for _p in (REPO_ROOT, os.path.join(REPO_ROOT, 'tools')):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from tests.test_agro_work_release_v122 import functions, release_block  # noqa: E402
from tests.test_agro_work_runbook import (commands, constants, gate_lines, git, has_commit, prepare,  # noqa: E402
                                          sections, text)
from tests.test_gps_release_v131 import RELEASE as V131  # noqa: E402

import drone_alias_decisions as decisions  # noqa: E402
import fin_recon_002_release_check as rc  # noqa: E402
import fin_recon_002_staging_check as staging  # noqa: E402
import migrate_drones_works_merged_customer_link_001 as link  # noqa: E402

RUNBOOK = os.path.join(REPO_ROOT, 'docs', 'FIN_RECON_002_RELEASE_V132_RUNBOOK.md')
RUNBOOK_TEST = 'tests/test_fin_recon_002_release_runbook.py'
BLOCKS = ('Шаг 2', 'Шаг 3', 'Восстановление', 'Откат')
BASELINE = '998bc20bfdb81e6d90ab413b63ab59da665aac65'
# The merge commit of PR #232: the release is this commit, not the top of main.
RELEASE = 'c4b039630fd75264994267abe69f89d9eaba7632'


def block(prefix):
    return release_block(prefix, path=RUNBOOK)


def const(prefix, name):
    return constants(block(prefix))[name].strip("'")


def read(*parts):
    with open(os.path.join(REPO_ROOT, *parts), encoding='utf-8') as fh:
        return fh.read()


class ReleaseV132Runbook(unittest.TestCase):

    def test_four_blocks_ascii_without_tabs_placeholders_or_double_ampersand(self):
        bodies = re.findall(r'```powershell\n(.*?)```', text(RUNBOOK), re.S)
        self.assertEqual(len(bodies), 4)
        for body in bodies:
            self.assertTrue(body.isascii(), body[:80])
            self.assertNotIn('\t', body)
        for line in commands(path=RUNBOOK):
            self.assertNotRegex(line, r'<[^>]*>', line)
            self.assertNotIn('&&', line)
            for word in ('YYYY', 'XXX', 'TODO', '...'):
                self.assertNotIn(word, line, line)
        self.assertNotRegex(text(RUNBOOK), r'@[A-Z0-9_]+@')

    def test_the_pins_are_the_same_in_every_block(self):
        for name in ('baseline', 'reviewed', 'runbookTest', 'prod', 'services'):
            values = {constants(block(b))[name] for b in BLOCKS}
            self.assertEqual(len(values), 1, (name, values))
        for name in ('release', 'tableSha', 'bookShas', 'table', 'frdir', 'expectMig', 'forbid', 'db', 'py',
                     'backupBat'):
            self.assertEqual(constants(block('Шаг 2'))[name], constants(block('Шаг 3'))[name], name)
        # The stop mark of the window is the one the restore reads and the rollback refuses on.
        for name in ('stopMark', 'work'):
            values = {constants(block(b))[name] for b in ('Шаг 3', 'Восстановление', 'Откат')}
            self.assertEqual(len(values), 1, (name, values))
        for name in ('db', 'py', 'site'):
            self.assertEqual(constants(block('Шаг 3'))[name], constants(block('Восстановление'))[name], name)

    def test_the_baseline_is_production(self):
        self.assertEqual(const('Шаг 3', 'baseline'), BASELINE)
        self.assertIn('Production baseline | `%s`' % BASELINE, read('docs', 'RELEASE_GATE.md'))
        self.assertEqual(const('Шаг 3', 'runbookTest'), RUNBOOK_TEST)
        self.assertTrue(os.path.isfile(os.path.join(REPO_ROOT, *RUNBOOK_TEST.split('/'))))

    def test_the_release_is_the_pinned_commit_not_the_top_of_main(self):
        # [REASON]: main moves on after the release PR (GPS PRs #249, #250 on
        # 29.10). The release files on the server were checked for c4b0396;
        # steps 2 and 3 must rehearse and install exactly it, never whatever
        # origin/main points at that day.
        for name in ('Шаг 2', 'Шаг 3'):
            body = block(name)
            self.assertEqual(const(name, 'release'), RELEASE)
            self.assertEqual(len(re.findall(r'(?m)^\s*\$release\s*=', body)), 1, name)
            self.assertNotIn('$release = [string](& git rev-parse origin/main)', body)
            self.assertIn('$main = [string](& git rev-parse origin/main)', body)
            at_main = body.index('& git merge-base --is-ancestor $release $main')
            self.assertLess(at_main, body.index("& git merge-base --is-ancestor $head $release"), name)
            self.assertIn('throw "the release commit is not in main: $release, main $main"', body)
            self.assertIn('& git diff --name-only $reviewed $release', body)
            # The gate is the project's list as it stands now: read on main.
            self.assertIn('& git show "${main}:docs/RELEASE_GATE.md"', body)
            self.assertIn('if ($head -ne $baseline)', body)
        self.assertIn('checkout --quiet --detach $release', block('Шаг 2'))
        self.assertIn("Invoke-Tool 'git' @('merge', '--ff-only', $release)", block('Шаг 3'))
        self.assertIn('if ($now -ne $release)', block('Шаг 3'))
        self.assertNotIn('$main', block('Восстановление') + block('Откат'))
        self.assertIn('`%s`' % RELEASE, text(RUNBOOK))

    def test_the_release_files_are_the_ones_the_tools_check(self):
        self.assertEqual(const('Шаг 3', 'tableSha'), rc.RELEASE_TABLE_SHA)
        self.assertEqual(const('Шаг 3', 'bookShas'), ' '.join(sorted(staging.BOOKS.values())))
        self.assertEqual(const('Шаг 3', 'forbid'), rc.FORBID)
        self.assertEqual(const('Шаг 3', 'batch'), rc.BATCH)
        self.assertEqual(const('Шаг 3', 'table').rsplit('\\', 1)[1], 'FIN_RECON_002_RELEASE_DECISIONS_20261029.csv')
        self.assertIn(rc.RELEASE_TABLE_SHA, text(RUNBOOK))
        for sha in staging.BOOKS.values():
            self.assertIn('`%s`' % sha, text(RUNBOOK))

    def test_the_registry_names_without_a_file_are_the_three_the_window_writes(self):
        want = sorted([link.SCOPES['june']['migration_id'], link.SCOPES['june-anchor']['migration_id'],
                       decisions.migration_id(rc.RELEASE_TABLE_SHA)])
        self.assertEqual(sorted(const('Шаг 3', 'noFile').split()), want)
        # The fourth registry row has its file and leaves the pending list.
        self.assertEqual(len(rc.expected_registry()), 4)
        self.assertIn('$drift0.Registered + 4', block('Шаг 3'))

    def test_the_migrations_of_the_release_are_the_two_of_this_track(self):
        self.assertEqual(const('Шаг 3', 'expectMig'),
                         'migrate_drones_works_merged_customer_link_001.py '
                         'migrate_drones_works_payer_unidentified_001.py')
        if not (has_commit(BASELINE) and has_commit(RELEASE)):
            self.skipTest('production baseline or the release is outside this clone')
        names = sorted(n for n in git('diff', '--name-only', BASELINE, RELEASE).split() if n.startswith('migrate_'))
        self.assertEqual(' '.join(names), const('Шаг 3', 'expectMig'))

    def test_the_pinned_release_holds_the_checked_code(self):
        commit = const('Шаг 3', 'reviewed')
        if not (has_commit(commit) and has_commit(RELEASE) and has_commit(BASELINE)):
            self.skipTest('the pinned history is outside this clone')

        def ancestor(older, newer):
            return subprocess.run(['git', 'merge-base', '--is-ancestor', older, newer], cwd=REPO_ROOT).returncode == 0

        self.assertTrue(ancestor(BASELINE, RELEASE), 'production is not an ancestor of the release')
        self.assertTrue(ancestor(commit, RELEASE), 'the release does not contain the checked version')
        self.assertTrue(ancestor(RELEASE, 'HEAD'), 'this runbook is not written on top of the release')
        # Between the checked version and the release only what never runs on
        # the server: docs, the CI workflow and this test (the blocks allow
        # exactly these). What main got after the release does not matter.
        names = git('diff', '--name-only', commit, RELEASE).split()
        self.assertEqual([n for n in names if not n.startswith(('docs/', '.github/')) and n != RUNBOOK_TEST], [])
        for name in BLOCKS:
            self.assertIn("($_ -notlike 'docs/*') -and ($_ -notlike '.github/*') -and ($_ -ne $runbookTest)",
                          block(name))

    def test_git_is_limited_and_each_verb_stays_in_its_step(self):
        invoked = re.compile(r"(?:^|&\s*|'git' @\(')git\s+(?:--no-pager\s+|-C\s+\S+\s+)*([a-z][a-z-]*)|"
                             r"Invoke-Tool 'git' @\('([a-z-]+)'")
        seen = {}
        for name in BLOCKS:
            for line in block(name).splitlines():
                for m in invoked.finditer(line.strip()):
                    seen.setdefault(m.group(1) or m.group(2), set()).add(name)
        self.assertEqual(set(seen) - {'fetch', 'rev-parse', 'merge-base', 'diff', 'show', 'status', 'log', 'merge',
                                      'reset', 'clone', 'checkout'}, set())
        self.assertEqual(seen['clone'], {'Шаг 2'})
        self.assertEqual(seen['checkout'], {'Шаг 2'})
        self.assertEqual(seen['merge'], {'Шаг 3'})
        self.assertEqual(seen['reset'], {'Шаг 3', 'Восстановление', 'Откат'})
        self.assertIn("checkout --quiet --detach $release", block('Шаг 2'))

    def test_services_stop_only_in_the_window_and_the_rollback(self):
        self.assertNotIn('Stop-Service', block('Шаг 2'))
        self.assertIn('Stop-Service', block('Шаг 3'))
        self.assertIn('Stop-Service', block('Откат'))
        # The restore never stops anything: it runs only while the window left the services stopped.
        self.assertNotIn('Stop-Service', block('Восстановление'))

    def test_the_helpers_are_word_for_word_those_of_v131(self):
        v131 = {}
        for name in ('Шаг 3', 'Откат'):
            v131.update(functions(release_block(name, path=V131)))
        for name in BLOCKS:
            for fn, body in functions(block(name)).items():
                if fn == 'Get-NoFile':
                    continue
                with self.subTest(block=name, function=fn):
                    self.assertEqual(body, v131[fn])

    def test_the_window_runs_in_the_decided_order(self):
        body = block('Шаг 3')
        order = ["Stop-Service", "Invoke-Tool $backupBat", "'merge', '--ff-only'",
                 "'migrate_drones_works_payer_unidentified_001.py'", "'migrate_drones_works_merged_customer_link_001.py'",
                 "'tools\\drone_alias_decisions.py'", "'tools\\import_drone_works.py'",
                 "$drift1Lines = ", "'tools\\fin_recon_002_release_check.py'", "Restart-Service",
                 "($site + '/drones/works')"]
        at = [body.index(step) for step in order]
        self.assertEqual(at, sorted(at), order)
        self.assertIn("foreach ($scope in @('june', 'june-anchor'))", body)
        # [REASON]: the database starts to change with the payer column. A stop
        # before it returns the code and starts the old program; a stop after it
        # (the release check included) must not start anything on a partly
        # changed database -- the restore block is the way back.
        self.assertLess(body.index("'merge', '--ff-only'"), body.index('$dbTouched = $true'))
        self.assertLess(body.index('$dbTouched = $true'), body.index("'migrate_drones_works_payer_unidentified_001.py'"))
        self.assertLess(body.index("'^RESULT: RELEASE CHECK PASSED'"), body.index('$passed = $true'))
        self.assertLess(body.index('$passed = $true'), body.index('} catch {\n  $failure = $_.Exception.Message'))
        self.assertIn('if ($dbTouched -and (-not $passed)) {\n    $noStart = $true', body)
        self.assertIn("if ($updated -and (-not $dbTouched))", body)
        self.assertNotIn('payerDone', body)
        self.assertLess(body.index('git reset --keep $baseline'), body.index('Restart-Service'))
        self.assertIn('if ($stopped -and (-not $noStart))', body)
        # The window refuses to run without a rehearsal of this very version.
        self.assertIn('fr002_step2_passed_$release.txt', block('Шаг 2'))
        self.assertIn('fr002_step2_passed_$release.txt', body)

    def test_every_string_a_block_waits_for_is_one_the_tool_prints(self):
        self.assertIn("'Done. %d column added to drone_works, no row backfilled.'",
                      read('migrate_drones_works_payer_unidentified_001.py'))
        self.assertIn("'^Done\\. 1 column added to drone_works'", block('Шаг 3'))
        # Both print the status word after «RESULT: »; the window waits for APPLIED.
        for source, made in ((read('migrate_drones_works_merged_customer_link_001.py'), "_result('APPLIED', 0"),
                             (read('tools', 'drone_alias_decisions.py'), "result('APPLIED', 0")):
            self.assertIn("print('RESULT: %s' % status)", source)
            self.assertIn(made, source)
        self.assertEqual(block('Шаг 3').count("-match '^RESULT: APPLIED'"), 2)
        self.assertIn("'WRITTEN: %d customers created, %d works inserted, '\n"
                      "              '%d already present and skipped.' % (created, inserted, skipped)",
                      read('tools', 'import_drone_works.py'))
        self.assertIn("'^WRITTEN: 0 customers created, 132 works inserted, 0 already present and skipped\\.$'",
                      block('Шаг 3'))
        self.assertIn("'RESULT: RELEASE CHECK %s (%d failed)'", read('tools', 'fin_recon_002_release_check.py'))
        self.assertIn("'RESULT: %s (%d failed). Report:", read('tools', 'fin_recon_002_staging_check.py'))
        drift = read('tools', 'check_migration_drift.py')
        for header in ('registered-but-no-file', 'file-but-not-registered', 'registered migrations: '):
            self.assertIn(header, drift)

    def test_every_named_script_exists_and_python_is_quoted(self):
        names = set()
        for line in commands(path=RUNBOOK):
            names.update(re.findall(r'(tools\\[A-Za-z0-9_]+\.py)', line))
            names.update(re.findall(r"'(migrate_[A-Za-z0-9_]+\.py)'", line))
            names.update(re.findall(r'\b(migrate_[A-Za-z0-9_]+\.py)\b', line))
        self.assertIn('tools\\fin_recon_002_release_check.py', names)
        self.assertIn('tools\\fin_recon_002_staging_check.py', names)
        for name in names:
            self.assertTrue(os.path.isfile(os.path.join(REPO_ROOT, name.replace('\\', os.sep))), name)
        self.assertEqual(const('Шаг 3', 'py'), 'C:\\Program Files\\Python314\\python.exe')
        self.assertIn('tools\\drone_works_manifest.txt', block('Шаг 3'))

    def test_the_smoke_markers_are_real(self):
        self.assertIn('class="vs-login-form"', read('templates', 'login.html'))
        drones = read('drones.py')
        self.assertIn("url_prefix='/drones'", drones)
        self.assertIn("@drones_bp.route('/works')\n@module_required('drones')", drones)
        self.assertIn('@login_required', read('models.py').split('def module_required', 1)[1][:800])

    def test_every_stop_in_the_table_is_one_a_block_prints(self):
        table = [line for line in text(RUNBOOK).splitlines() if line.startswith('| `RESULT: STOP - ')]
        self.assertGreaterEqual(len(table), 20)
        blocks = ''.join(block(name) for name in BLOCKS)
        for line in table:
            words = re.match(r'\| `RESULT: STOP - ([^`]*)`', line).group(1).split('...')[0].strip()
            with self.subTest(stop=words):
                self.assertTrue(words)
                self.assertIn(words, blocks)

    def test_the_manifest_carries_both_june_books(self):
        manifest = read('tools', 'drone_works_manifest.txt')
        for name in staging.BOOKS:
            self.assertRegex(manifest, r'(?m)^%s\s+2026-06$' % re.escape(name))

    def test_no_secret(self):
        body = text(RUNBOOK)
        self.assertNotRegex(body, r'password=(?!`)\S')
        self.assertNotIn('token', body.lower())


def powershell():
    return (os.environ.get('FIN_RECON_RELEASE_POWERSHELL') or shutil.which('powershell')
            or shutil.which('powershell.exe'))


@unittest.skipUnless(powershell(), 'Windows PowerShell is not on this machine')
class ReleaseV132BlocksParseInPowerShell(unittest.TestCase):
    """Each block is parsed by PowerShell itself: a block that does not parse never starts."""

    def test_every_block_parses(self):
        for name in BLOCKS:
            with self.subTest(block=name), tempfile.TemporaryDirectory() as tmp:
                path = os.path.join(tmp, 'block.ps1')
                with open(path, 'w', encoding='ascii', newline='\r\n') as fh:
                    fh.write(block(name))
                probe = ('$e = $null; $t = $null; '
                         '[void][System.Management.Automation.Language.Parser]::ParseFile("%s", [ref]$t, [ref]$e); '
                         'Write-Output ("ERRORS " + $e.Count); $e | ForEach-Object { Write-Output $_.Message }' % path)
                out = subprocess.run([powershell(), '-NoProfile', '-NonInteractive', '-Command', probe],
                                     capture_output=True, text=True, timeout=120)
                self.assertIn('ERRORS 0', out.stdout, out.stdout + out.stderr)


HARNESS = os.path.join(REPO_ROOT, 'tests', 'fin_recon_002_release_harness.ps1')
RELEASE_HASH = RELEASE
# origin/main two commits past the release, as on 29.10 (stand-in hashes).
MAIN_HASH = '5ca1ab1e' * 5
LATER = ['5ca1ab1', '1a7e2c0']
TEST_SERVICES = ['V132TestReport', 'V132TestBot', 'V132TestBot003']
DB_BEFORE = b'SQLite format 3\x00' + b'x' * 48
# The STOP the window prints when the named tool fails.
STOP_AT = {
    'backup': 'the backup did not pass',
    'merge': 'git merge --ff-only failed',
    'untracked': 'the untracked files changed during the update',
    'payer': 'the payer_unidentified migration did not pass',
    'link': 'the link migration --scope june-anchor did not pass',
    'decisions': 'the decision table did not pass',
    'import': 'the import did not pass',
    'registry': 'after the release the registry is',
    'release_check': 'the release check did not pass',
}
BEFORE_THE_DATABASE = ('backup', 'merge', 'untracked')
AFTER_THE_DATABASE = ('payer', 'link', 'decisions', 'import', 'registry', 'release_check')


def sha256(path):
    with open(path, 'rb') as fh:
        return hashlib.sha256(fh.read()).hexdigest()


def read_bytes(path):
    with open(path, 'rb') as fh:
        return fh.read()


class ReleaseV132BlocksRunWhereRequired(unittest.TestCase):
    """A skipped class passes as quietly as a passing one.

    The CI job windows-powershell-51 sets FIN_RECON_RELEASE_RUN_REQUIRED=1:
    there the execution class below must run, not skip.
    """

    def test_the_execution_class_is_not_skipped_where_it_is_required(self):
        if os.environ.get('FIN_RECON_RELEASE_RUN_REQUIRED') != '1':
            self.skipTest('FIN_RECON_RELEASE_RUN_REQUIRED is not set')
        self.assertEqual(os.name, 'nt')
        self.assertTrue(powershell(), 'no Windows PowerShell: ReleaseV132BlocksRunInPowerShell would be skipped')


# [REASON]: the blocks join server paths with a backslash ("$frdir\books") and
# the server runs Windows PowerShell 5.1, so they are executed on Windows only:
# here on the workstation and in the CI job windows-powershell-51. The Linux
# job still parses every block with pwsh 7 (the class above).
@unittest.skipUnless(os.name == 'nt' and powershell(), 'the blocks run on Windows PowerShell only')
class ReleaseV132BlocksRunInPowerShell(unittest.TestCase):
    """Steps 2 and 3, the restore and the rollback run against a stand-in server.

    origin/main stands two commits past the release, as on 29.10: both steps
    must rehearse and install the pinned commit, not the top of main.

    The defect this guards (independent review 29.10): a stop after the
    database started to change -- the release check included -- restarted the
    services on a partly changed database. Now only a stop before the first
    write starts the old program; after it the services stay stopped, the
    backup, receipts and log stay, and the restore block brings back the
    window's backup, proven byte for byte, before anything starts.
    """

    def setUp(self):
        self.server = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.server, True)
        self.prod = os.path.join(self.server, 'prod')
        self.work = os.path.join(self.server, 'work')
        self.frdir = os.path.join(self.server, 'fr')
        os.makedirs(os.path.join(self.prod, 'instance'))
        os.makedirs(os.path.join(self.frdir, 'books'))
        os.makedirs(self.work)
        os.makedirs(os.path.join(self.server, 'backups'))
        self.daily = os.path.join(self.server, 'daily')
        os.makedirs(self.daily)
        nightly = os.path.join(self.daily, 'transport_20261030_013000.db')
        with open(nightly, 'wb') as fh:
            fh.write(DB_BEFORE)
        # The harness clock reads 2026-10-30 10:00; the nightly copy is from 01:30.
        stamp = time.mktime((2026, 10, 30, 1, 30, 0, 0, 0, -1))
        os.utime(nightly, (stamp, stamp))
        self.db = os.path.join(self.prod, 'instance', 'transport.db')
        with open(self.db, 'wb') as fh:
            fh.write(DB_BEFORE)
        self.errlog = os.path.join(self.prod, 'error.log')
        with open(self.errlog, 'w', encoding='ascii') as fh:
            fh.write('INFO:waitress:Serving on http://0.0.0.0:5050\n')
        self.table = os.path.join(self.frdir, 'release_table.csv')
        with open(self.table, 'w', encoding='ascii') as fh:
            fh.write('stand-in table\n')
        books = []
        for name in ('june_one.xlsx', 'june_two.xlsx'):
            path = os.path.join(self.frdir, 'books', name)
            with open(path, 'w', encoding='ascii') as fh:
                fh.write('stand-in book %s\n' % name)
            books.append(sha256(path))
        self.book_shas = ' '.join(sorted(books))
        with open(os.path.join(self.work, 'fr002_step2_passed_%s.txt' % RELEASE_HASH), 'w', encoding='ascii') as fh:
            fh.write('rehearsal passed\n')
        self.backup = os.path.join(self.server, 'backups', 'transport_window.db')
        self.stop_mark = os.path.join(self.work, 'fr002_step3_db_stop.txt')
        self.runs = 0

    def scenario(self, **changes):
        value = {
            'Host': 'SRV-YOQSH', 'Head': BASELINE, 'Baseline': BASELINE,
            'Reviewed': const('Шаг 3', 'reviewed'), 'Release': RELEASE_HASH, 'Main': MAIN_HASH, 'Later': LATER,
            'History': [BASELINE, const('Шаг 3', 'reviewed'), RELEASE_HASH, MAIN_HASH],
            'Services': TEST_SERVICES, 'ServiceStatus': 'Running',
            'GateLines': gate_lines(0), 'FailAt': None, 'CopyCorrupts': False,
            'NoFile': const('Шаг 3', 'noFile').split(), 'Db': self.db, 'BackupFile': self.backup,
        }
        value.update(changes)
        return value

    def run_block(self, name, scenario):
        self.runs += 1
        folder = os.path.join(self.server, 'run%d' % self.runs)
        os.makedirs(folder)
        log = os.path.join(self.work, 'release_%d.log' % self.runs)
        body = prepare(block(name), {
            'prod': "'%s'" % self.prod, 'db': "'%s'" % self.db,
            'py': "'Invoke-FakePython'", 'backupBat': "'Invoke-FakeBackup'",
            'errLog': "'%s'" % self.errlog,
            'services': '@(%s)' % ', '.join("'%s'" % s for s in TEST_SERVICES),
            'site': "'http://v132-test.invalid'", 'work': "'%s'" % self.work,
            'log': "'%s'" % log, 'admin': '$true', 'frdir': "'%s'" % self.frdir,
            'table': "'%s'" % self.table, 'tableSha': "'%s'" % sha256(self.table),
            'bookShas': "'%s'" % self.book_shas, 'stopMark': "'%s'" % self.stop_mark,
            'daily': "'%s'" % self.daily, 'repoUrl': "'https://v132-test.invalid/repo.git'",
        })
        block_file = os.path.join(folder, 'block.ps1')
        scenario_file = os.path.join(folder, 'scenario.json')
        calls_file = os.path.join(folder, 'calls.txt')
        with open(block_file, 'w', encoding='ascii') as fh:
            fh.write(body)
        with open(scenario_file, 'w', encoding='ascii') as fh:
            json.dump(scenario, fh, ensure_ascii=True)
        proc = subprocess.run(
            [powershell(), '-NoProfile', '-NonInteractive', '-ExecutionPolicy', 'Bypass', '-File', HARNESS,
             '-BlockFile', block_file, '-ScenarioFile', scenario_file, '-CallsFile', calls_file],
            capture_output=True, text=True, encoding='utf-8', errors='replace', timeout=300)
        output = proc.stdout + proc.stderr
        results = [line for line in output.splitlines() if line.startswith('RESULT: ')]
        self.assertEqual(len(results), 1, output)
        with open(calls_file, encoding='utf-8-sig') as fh:
            calls = [line for line in fh.read().splitlines() if line]
        return results[0], calls, output, log

    def started(self, calls):
        return [c for c in calls if c.startswith(('Restart-Service', 'Start-Service'))]

    def assertInOrder(self, calls, *prefixes):
        where = []
        for prefix in prefixes:
            hits = [i for i, call in enumerate(calls) if call.startswith(prefix)]
            self.assertTrue(hits, '%s not called: %s' % (prefix, calls))
            where.append(hits[0])
        self.assertEqual(where, sorted(where), calls)

    def mark(self):
        with open(self.stop_mark, encoding='ascii') as fh:
            return dict(line.split(': ', 1) for line in fh.read().splitlines() if ': ' in line)

    def kept(self):
        return glob.glob(os.path.join(self.work, 'fr002_db_after_stop_*'))

    def stop_the_window(self, fail_at='release_check'):
        result, calls, output, log = self.run_block('Шаг 3', self.scenario(FailAt=fail_at))
        self.assertTrue(result.startswith('RESULT: STOP - ' + STOP_AT[fail_at]), output)
        self.assertTrue(os.path.isfile(self.stop_mark), output)
        return output

    def passed_files(self):
        return sorted(os.path.basename(p) for p in glob.glob(os.path.join(self.work, 'fr002_step2_passed_*.txt')))

    # --- step 2 -----------------------------------------------------------------

    def test_step_2_rehearses_the_pinned_commit_while_main_moved_on(self):
        os.remove(os.path.join(self.work, 'fr002_step2_passed_%s.txt' % RELEASE_HASH))
        result, calls, output, log = self.run_block('Шаг 2', self.scenario())
        self.assertEqual(result, 'RESULT: CHECK PASSED - go on to step 3', output)
        self.assertEqual([c for c in calls if c.startswith('git checkout')],
                         ['git checkout --quiet --detach ' + RELEASE_HASH])
        self.assertInOrder(calls, 'git merge-base --is-ancestor %s %s' % (RELEASE_HASH, MAIN_HASH),
                           'git clone', 'git checkout', 'python ')
        self.assertIn('git show %s:docs/RELEASE_GATE.md' % MAIN_HASH, calls)
        self.assertEqual(self.passed_files(), ['fr002_step2_passed_%s.txt' % RELEASE_HASH])
        self.assertIn('RELEASE: %s -> %s' % (BASELINE, RELEASE_HASH), output)
        self.assertIn('MAIN: %s - 2 later commit(s) of main are not taken' % MAIN_HASH, output)
        self.assertFalse([c for c in calls if c.startswith(('Stop-Service', 'backup', 'git merge ', 'git reset'))],
                         calls)

    def test_step_2_does_not_mark_a_failed_rehearsal(self):
        os.remove(os.path.join(self.work, 'fr002_step2_passed_%s.txt' % RELEASE_HASH))
        result, calls, output, log = self.run_block('Шаг 2', self.scenario(FailAt='rehearsal'))
        self.assertTrue(result.startswith('RESULT: STOP - the rehearsal did not pass'), output)
        self.assertEqual(self.passed_files(), [])

    def test_a_release_outside_main_or_a_server_off_the_baseline_stops_before_anything(self):
        cases = {
            # main does not contain the release commit (History has no main in it).
            'the release commit is not in main': dict(History=[BASELINE, const('Шаг 3', 'reviewed'), RELEASE_HASH]),
            # production is not at 998bc20.
            'the program here is at': dict(Head='0bad0bad' * 5),
        }
        for name in ('Шаг 2', 'Шаг 3'):
            for stop, change in cases.items():
                with self.subTest(block=name, stop=stop):
                    self.setUp()
                    result, calls, output, log = self.run_block(name, self.scenario(**change))
                    self.assertTrue(result.startswith('RESULT: STOP - ' + stop), output)
                    self.assertFalse([c for c in calls if c.startswith(
                        ('Stop-Service', 'backup', 'git clone', 'git checkout', 'git merge ', 'git reset', 'python'))],
                        calls)
                    self.assertEqual(self.started(calls), [])
                    self.assertEqual(read_bytes(self.db), DB_BEFORE)

    # --- step 3 -----------------------------------------------------------------

    def test_the_release_passes_and_starts_the_new_program(self):
        result, calls, output, log = self.run_block('Шаг 3', self.scenario())
        self.assertEqual(result, 'RESULT: RELEASE PASSED', output)
        # main is two commits ahead; the code moves to the release and no further.
        self.assertEqual([c for c in calls if c.startswith('git merge ')], ['git merge --ff-only ' + RELEASE_HASH])
        self.assertIn('UPDATED: %s -> %s' % (BASELINE, RELEASE_HASH), output)
        self.assertIn('MAIN: %s - 2 later commit(s) of main are not taken' % MAIN_HASH, output)
        self.assertInOrder(calls, 'Stop-Service', 'backup', 'git merge --ff-only',
                           'python migrate_drones_works_payer_unidentified_001.py',
                           'python migrate_drones_works_merged_customer_link_001.py',
                           'python tools\\drone_alias_decisions.py', 'python tools\\import_drone_works.py',
                           'python tools\\fin_recon_002_release_check.py', 'Restart-Service', 'GET /login')
        self.assertEqual(sorted(c.split()[1] for c in self.started(calls)), sorted(TEST_SERVICES))
        self.assertFalse(os.path.exists(self.stop_mark))
        self.assertFalse([c for c in calls if c.startswith('git reset')])

    def test_a_stop_before_the_database_changes_starts_the_old_program(self):
        for fail_at in BEFORE_THE_DATABASE:
            with self.subTest(fail_at=fail_at):
                self.setUp()
                result, calls, output, log = self.run_block('Шаг 3', self.scenario(FailAt=fail_at))
                self.assertTrue(result.startswith('RESULT: STOP - ' + STOP_AT[fail_at]), output)
                self.assertFalse([c for c in calls if c.startswith('python migrate')], calls)
                self.assertEqual(read_bytes(self.db), DB_BEFORE)
                self.assertFalse(os.path.exists(self.stop_mark))
                self.assertEqual(sorted(c.split()[1] for c in self.started(calls)), sorted(TEST_SERVICES), output)
                if fail_at == 'untracked':
                    # The code had moved: it goes back before anything starts.
                    self.assertInOrder(calls, 'git merge --ff-only', 'git reset --keep ' + BASELINE, 'Restart-Service')
                else:
                    self.assertFalse([c for c in calls if c.startswith('git reset')], calls)

    def test_a_stop_after_the_database_changed_leaves_the_services_stopped(self):
        for fail_at in AFTER_THE_DATABASE:
            with self.subTest(fail_at=fail_at):
                self.setUp()
                result, calls, output, log = self.run_block('Шаг 3', self.scenario(FailAt=fail_at))
                self.assertTrue(result.startswith('RESULT: STOP - ' + STOP_AT[fail_at]), output)
                self.assertEqual(self.started(calls), [], output)
                self.assertFalse([c for c in calls if c.startswith(('git reset', 'GET '))], calls)
                self.assertIn('the services are NOT started', output)
                # The backup, the receipts and the log stay; the mark names them.
                mark = self.mark()
                self.assertEqual(mark['RELEASE'], RELEASE_HASH)
                self.assertEqual(mark['BACKUP'], self.backup)
                self.assertEqual(read_bytes(self.backup), DB_BEFORE)
                self.assertTrue(os.path.isdir(mark['RECEIPTS']), mark)
                self.assertEqual(mark['LOG'], log)
                self.assertTrue(os.path.isfile(log))
                self.assertIn(STOP_AT[fail_at], mark['FAILURE'])
                self.assertIn('BACKUP OF THIS WINDOW: ' + self.backup, output)

    def test_the_window_does_not_start_while_a_stop_mark_lies(self):
        self.stop_the_window('import')
        result, calls, output, log = self.run_block('Шаг 3', self.scenario())
        self.assertTrue(result.startswith('RESULT: STOP - a previous window stopped after the database changed'),
                        output)
        self.assertFalse([c for c in calls if c.startswith(('Stop-Service', 'backup', 'git merge', 'python'))], calls)
        self.assertEqual(self.started(calls), [])

    # --- the restore ------------------------------------------------------------

    def test_the_restore_returns_the_window_backup_and_the_old_code_then_starts(self):
        self.stop_the_window('release_check')
        self.assertNotEqual(read_bytes(self.db), DB_BEFORE)
        self.assertTrue(os.path.isfile(self.db + '-wal'))
        result, calls, output, log = self.run_block(
            'Восстановление', self.scenario(Head=RELEASE_HASH, ServiceStatus='Stopped'))
        self.assertEqual(result, 'RESULT: RESTORE PASSED', output)
        self.assertEqual(read_bytes(self.db), DB_BEFORE)
        self.assertEqual(read_bytes(self.db), read_bytes(self.backup))
        self.assertFalse(os.path.exists(self.db + '-wal'))
        # The window's files are moved aside, not deleted.
        kept = self.kept()
        self.assertEqual(len(kept), 1, kept)
        self.assertIn(b'written by the window', read_bytes(os.path.join(kept[0], 'transport.db')))
        self.assertTrue(os.path.isfile(os.path.join(kept[0], 'transport.db-wal')))
        self.assertInOrder(calls, 'python tools\\check_db_lock.py', 'copy', 'git reset --keep ' + BASELINE,
                           'Restart-Service', 'GET /login')
        self.assertFalse([c for c in calls if c.startswith('Stop-Service')])
        self.assertFalse(os.path.exists(self.stop_mark))
        self.assertEqual(len(glob.glob(os.path.join(self.work, 'fr002_step3_db_stop_restored_*.txt'))), 1)

    def test_the_restore_without_a_mark_changes_nothing(self):
        result, calls, output, log = self.run_block(
            'Восстановление', self.scenario(Head=RELEASE_HASH, ServiceStatus='Stopped'))
        self.assertEqual(result, 'RESULT: STOP - no window stopped after a database change - this block is not needed',
                         output)
        # Only the closing line that names the program version.
        self.assertEqual(calls, ['git rev-parse --short HEAD'])
        self.assertEqual(read_bytes(self.db), DB_BEFORE)

    def test_the_restore_refuses_while_the_services_run_or_the_backup_is_gone(self):
        self.stop_the_window('import')
        window_db = read_bytes(self.db)
        result, calls, output, log = self.run_block(
            'Восстановление', self.scenario(Head=RELEASE_HASH, ServiceStatus='Running'))
        self.assertTrue(result.startswith('RESULT: STOP - these services are not stopped'), output)
        self.assertEqual(self.started(calls), [])
        self.assertEqual(read_bytes(self.db), window_db)
        os.rename(self.backup, self.backup + '.elsewhere')
        result, calls, output, log = self.run_block(
            'Восстановление', self.scenario(Head=RELEASE_HASH, ServiceStatus='Stopped'))
        self.assertTrue(result.startswith('RESULT: STOP - the backup of the window is not found'), output)
        self.assertEqual(self.started(calls), [])
        self.assertFalse([c for c in calls if c.startswith(('copy', 'git reset'))], calls)
        self.assertEqual(read_bytes(self.db), window_db)
        self.assertEqual(self.kept(), [])

    def test_a_restore_that_cannot_prove_the_copy_does_not_start(self):
        self.stop_the_window('link')
        result, calls, output, log = self.run_block(
            'Восстановление', self.scenario(Head=RELEASE_HASH, ServiceStatus='Stopped', CopyCorrupts=True))
        self.assertEqual(result, 'RESULT: STOP - the restored database differs from the backup', output)
        self.assertEqual(self.started(calls), [])
        self.assertFalse([c for c in calls if c.startswith('git reset')], calls)
        self.assertIn('THE SERVICES ARE NOT STARTED', output)
        kept = self.kept()
        self.assertEqual(len(kept), 1, kept)
        self.assertIn(b'written by the window', read_bytes(os.path.join(kept[0], 'transport.db')))
        self.assertTrue(os.path.isfile(self.stop_mark))

    # --- the rollback -----------------------------------------------------------

    def test_the_rollback_refuses_after_a_window_stopped_on_a_changed_database(self):
        self.stop_the_window('decisions')
        result, calls, output, log = self.run_block(
            'Откат', self.scenario(Head=RELEASE_HASH, ServiceStatus='Stopped'))
        self.assertTrue(result.startswith('RESULT: STOP - the window stopped after a database change'), output)
        self.assertEqual(calls, ['git rev-parse --short HEAD'])
        self.assertTrue(os.path.isfile(self.stop_mark))


if __name__ == '__main__':
    unittest.main()
