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
import os
import re
import shutil
import subprocess
import sys
import tempfile
import unittest

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
for _p in (REPO_ROOT, os.path.join(REPO_ROOT, 'tools')):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from tests.test_agro_work_release_v122 import functions, release_block  # noqa: E402
from tests.test_agro_work_runbook import commands, constants, git, has_commit, sections, text  # noqa: E402
from tests.test_gps_release_v131 import RELEASE as V131  # noqa: E402

import drone_alias_decisions as decisions  # noqa: E402
import fin_recon_002_release_check as rc  # noqa: E402
import fin_recon_002_staging_check as staging  # noqa: E402
import migrate_drones_works_merged_customer_link_001 as link  # noqa: E402

RUNBOOK = os.path.join(REPO_ROOT, 'docs', 'FIN_RECON_002_RELEASE_V132_RUNBOOK.md')
RUNBOOK_TEST = 'tests/test_fin_recon_002_release_runbook.py'
BLOCKS = ('Шаг 2', 'Шаг 3', 'Откат')
BASELINE = '998bc20bfdb81e6d90ab413b63ab59da665aac65'


def block(prefix):
    return release_block(prefix, path=RUNBOOK)


def const(prefix, name):
    return constants(block(prefix))[name].strip("'")


def read(*parts):
    with open(os.path.join(REPO_ROOT, *parts), encoding='utf-8') as fh:
        return fh.read()


class ReleaseV132Runbook(unittest.TestCase):

    def test_three_blocks_ascii_without_tabs_placeholders_or_double_ampersand(self):
        bodies = re.findall(r'```powershell\n(.*?)```', text(RUNBOOK), re.S)
        self.assertEqual(len(bodies), 3)
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
        for name in ('tableSha', 'bookShas', 'table', 'frdir', 'expectMig', 'forbid', 'db', 'py', 'backupBat'):
            self.assertEqual(constants(block('Шаг 2'))[name], constants(block('Шаг 3'))[name], name)

    def test_the_baseline_is_production(self):
        self.assertEqual(const('Шаг 3', 'baseline'), BASELINE)
        self.assertIn('Production baseline | `%s`' % BASELINE, read('docs', 'RELEASE_GATE.md'))
        self.assertEqual(const('Шаг 3', 'runbookTest'), RUNBOOK_TEST)
        self.assertTrue(os.path.isfile(os.path.join(REPO_ROOT, *RUNBOOK_TEST.split('/'))))

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
        if not has_commit(BASELINE):
            self.skipTest('production baseline is outside this clone')
        names = sorted(n for n in git('diff', '--name-only', BASELINE, 'HEAD').split() if n.startswith('migrate_'))
        self.assertEqual(' '.join(names), const('Шаг 3', 'expectMig'))

    def test_the_pin_was_right_when_the_runbook_was_written(self):
        commit = const('Шаг 3', 'reviewed')
        written = git('log', '-1', '--format=%H', '--', 'docs/FIN_RECON_002_RELEASE_V132_RUNBOOK.md').strip()
        if not (written and has_commit(commit) and has_commit(written)):
            self.skipTest('the pinned history is outside this clone')
        self.assertEqual(subprocess.run(['git', 'merge-base', '--is-ancestor', commit, written],
                                        cwd=REPO_ROOT).returncode, 0)
        # After the pinned commit only what never runs on the server: docs,
        # the CI workflow and this test (the blocks allow exactly these).
        names = git('diff', '--name-only', commit, written).split()
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
        self.assertEqual(seen['reset'], {'Шаг 3', 'Откат'})
        self.assertIn("checkout --quiet --detach $release", block('Шаг 2'))

    def test_services_stop_only_in_the_window_and_the_rollback(self):
        self.assertNotIn('Stop-Service', block('Шаг 2'))
        self.assertIn('Stop-Service', block('Шаг 3'))
        self.assertIn('Stop-Service', block('Откат'))

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
        # Without the column the new code cannot run: back to the baseline before the start.
        self.assertIn("if ($updated -and (-not $payerDone))", body)
        self.assertLess(body.index('git reset --keep $baseline'), body.index('Restart-Service'))
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


if __name__ == '__main__':
    unittest.main()
