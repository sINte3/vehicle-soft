# -*- coding: utf-8 -*-
"""agro-work: порядок для владельца вставляется в консоль сервера как есть.

Ранбук трека Дронов дважды называл то, чего на сервере нет, и оба раза это
поймал человек, а не проверка. Здесь держится:

  * в командах нет плейсхолдеров (`<...>`, `YYYY`, `XXX`) и `&&`;
  * ни одна команда не обновляет C:\\transport-report (git pull -- только
    релизом) и не пишет туда: клон, копия базы и секреты -- в своих папках;
  * каждый упомянутый `tools\\*.py` и `migrate_*.py` существует;
  * питон вызывается полным путём в кавычках через `&`;
  * числа строк «Ожидается» равны настоящим: число тестов самопроверки и
    строка итога миграции;
  * пароль и токены в ранбуке не встречаются, файл учётных данных не
    выводится на экран ни одной командой.

Ранбук вывода на production (docs/AGRO_WORK_RELEASE_RUNBOOK.md) с 30.09
несёт сам выпуск: владелец поручил его сессии («Релиз делай если нужно»).
Его большие блоки PowerShell остановят службы боевого сервера, поэтому для
них держится больше:

  * git -- только проверенные команды: перемотка `merge --ff-only` ровно
    одна, в шаге 3, после резервной копии; `reset --keep` -- только в
    откате; ни `pull`, ни `checkout`, ни `push`, ни `--hard`;
  * службы останавливаются только в шаге 3 и в откате, и в обоих блоках
    их поднимает `finally` -- на любой остановке посередине;
  * порядок шага 3 -- порядок docs/RELEASE_AND_BACKUP_PROCEDURE.md: проверки,
    остановка, блокировка, копия, перемотка, миграция дважды, подъём, дым;
  * постоянные блоков -- настоящие: коммит production, путь к питону, имена
    служб, строки миграции и дрейфа;
  * регулярные выражения блоков совпадают с выводом НАСТОЯЩИХ инструментов
    (ReleaseToolFormats);
  * сами блоки исполняются против подставных git, служб, питона и сайта во
    всех путях «стоп» (ReleaseBlocksInPowerShell): на PowerShell 7 и --
    в CI -- на Windows PowerShell 5.1, которая стоит на сервере.

Запуск: python -m unittest tests.test_agro_work_runbook -v
Блоки в PowerShell: AGRO_WORK_POWERSHELL=pwsh (или powershell) в окружении.
"""

import io
import json
import os
import re
import shutil
import sqlite3
import subprocess
import sys
import tempfile
import unittest
import warnings
from contextlib import redirect_stderr, redirect_stdout

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if REPO_ROOT not in sys.path:
    sys.path.insert(0, REPO_ROOT)

import migrate_agro_work_001 as mig                      # noqa: E402
import migration_utils                                   # noqa: E402
from tools import check_db_lock, check_migration_drift   # noqa: E402

RUNBOOK = os.path.join(REPO_ROOT, 'docs', 'AGRO_WORK_B1_RUNBOOK.md')
RELEASE = os.path.join(REPO_ROOT, 'docs', 'AGRO_WORK_RELEASE_RUNBOOK.md')
PYTHON = '& "C:\\Program Files\\Python314\\python.exe"'


def text(path=RUNBOOK):
    with open(path, encoding='utf-8') as fh:
        return fh.read()


def commands(body=None, path=RUNBOOK):
    """Строки из блоков ```powershell."""
    out = []
    for block in re.findall(r'```powershell\n(.*?)```', text(path) if body is None
                            else body, re.S):
        out.extend(line for line in block.splitlines() if line.strip())
    return out


def steps(path=RUNBOOK):
    """{заголовок шага: его текст} -- от «## Шаг» до следующего «## »."""
    return {match.group(1): match.group(2) for match in re.finditer(
        r'^## (Шаг [^\n]*)\n(.*?)(?=^## |\Z)', text(path), re.S | re.M)}


# Скрипт, названный относительно текущей папки: `tools\...`, `migrate_...`,
# `run_server.py`, `-m unittest`. Абсолютный путь сюда не попадает.
RELATIVE_SCRIPT = re.compile(r'(?<![\\A-Za-z:])(tools\\|migrate_[A-Za-z0-9_]+\.py'
                             r'|run_server\.py|-m unittest)')
CD_CLONE = 'cd C:\\VehicleSoft_AgroWork'


class Runbook(unittest.TestCase):
    def test_there_are_commands_at_all(self):
        self.assertGreater(len(commands()), 20)

    def test_no_placeholders_and_no_double_ampersand(self):
        for line in commands():
            self.assertNotRegex(line, r'<[^>]*>', line)
            self.assertNotIn('&&', line)
            for word in ('YYYY', 'XXX', 'TODO', '...'):
                self.assertNotIn(word, line, line)

    def test_nothing_updates_or_writes_the_production_checkout(self):
        for line in commands():
            lowered = line.lower()
            if 'c:\\transport-report' not in lowered:
                continue
            allowed = ('config --get remote.origin.url' in lowered
                       or ('agro_work_copy_db.py' in lowered
                           and '--from c:\\transport-report\\instance\\'
                               'transport.db --to c:\\vehiclesoft_agrowork'
                           in lowered))
            self.assertTrue(allowed, line)
        for line in commands():
            self.assertNotRegex(line.lower(), r'git\s.*\bpull\b.*transport-report')

    def test_every_named_script_exists(self):
        names = set()
        for line in commands():
            names.update(re.findall(r'(tools\\[A-Za-z0-9_]+\.py)', line))
            names.update(re.findall(r'\b(migrate_[A-Za-z0-9_]+\.py)', line))
            names.update(re.findall(r'\b(run_server\.py)', line))
            for module in re.findall(r'\b(tests\.test_[A-Za-z0-9_]+)', line):
                names.add(module.replace('.', os.sep) + '.py')
        self.assertTrue(names)
        for name in names:
            path = os.path.join(REPO_ROOT, name.replace('\\', os.sep))
            self.assertTrue(os.path.isfile(path), name)

    def test_every_step_that_runs_a_script_first_goes_to_the_clone(self):
        # 29.09: новое окно PowerShell от имени администратора открылось в
        # C:\Windows\system32, и шаг 10 ответил «can't open file
        # 'C:\\Windows\\system32\\tools\\agro_work_methods.py'». Шаг начинают
        # и в новом окне, поэтому в клон он переходит сам, до первого скрипта.
        found = steps()
        self.assertGreaterEqual(len(found), 12)
        for title, section in found.items():
            lines = commands(section)
            first = next((i for i, line in enumerate(lines)
                          if RELATIVE_SCRIPT.search(line)), None)
            if first is None:
                continue
            with self.subTest(step=title):
                self.assertIn(CD_CLONE, lines[:first])

    def test_python_is_called_by_its_full_quoted_path(self):
        for line in commands():
            if 'python' in line.lower():
                self.assertTrue(line.startswith(PYTHON), line)

    def test_the_self_check_count_is_the_real_count(self):
        line = [c for c in commands() if '-m unittest' in c][0]
        modules = re.findall(r'\b(tests\.test_[A-Za-z0-9_]+)', line)
        suite = unittest.defaultTestLoader.loadTestsFromNames(modules)
        stated = re.search(r'`Ran (\d+) tests`', text())
        self.assertIsNotNone(stated)
        self.assertEqual(int(stated.group(1)), suite.countTestCases())

    def test_the_self_check_covers_every_stdlib_test_module_of_the_track(self):
        line = [c for c in commands() if '-m unittest' in c][0]
        listed = set(re.findall(r'\btests\.(test_agro_work_[a-z_]+)', line))
        present = {name[:-3] for name in os.listdir(os.path.join(REPO_ROOT, 'tests'))
                   if name.startswith('test_agro_work_') and name.endswith('.py')}
        # Экран и сам ранбук требуют Flask/документа -- на сервере их гоняет
        # не самопроверка шага 2.
        self.assertEqual(listed, present - {'test_agro_work_screen',
                                            'test_agro_work_runbook'})

    def test_the_migration_line_is_what_the_migration_prints(self):
        expected = ('Done. %d agro_work tables (%d columns), %d indexes and %d '
                    'triggers are in place.'
                    % (len(mig.EXPECTED_COLUMNS),
                       sum(len(c) for c in mig.EXPECTED_COLUMNS.values()),
                       len(mig.INDEXES), len(mig.TRIGGERS)))
        self.assertIn(expected, text())

    def test_no_secret_and_the_credentials_file_is_never_shown(self):
        body = text()
        self.assertNotRegex(body, r'password=(?!`)\S')
        for line in commands():
            if 'agro_work_credentials.txt' in line:
                shown = re.match(r'\s*(Get-Content|type|cat)\b', line, re.I)
                self.assertTrue(shown is None or 'Measure-Object' in line, line)

    def test_the_branch_named_is_the_branch_of_this_work(self):
        self.assertIn('--branch claude/elegant-edison-zgmpbb', text())


class ReleaseRunbook(unittest.TestCase):
    """Выпуск на production: блоки, которые остановят боевые службы."""

    def lines(self):
        return commands(path=RELEASE)

    def test_there_are_commands_at_all(self):
        self.assertGreater(len(self.lines()), 15)

    def test_no_placeholders_and_no_double_ampersand(self):
        for line in self.lines():
            self.assertNotRegex(line, r'<[^>]*>', line)
            self.assertNotIn('&&', line)
            for word in ('YYYY', 'XXX', 'TODO', '...'):
                self.assertNotIn(word, line, line)

    def test_blocks_are_ascii_without_tabs(self):
        # Консоль сервера -- Windows PowerShell 5.1 с кодовой страницей OEM, а
        # табуляция при вставке мышью уходит в автодополнение.
        for body in re.findall(r'```powershell\n(.*?)```', text(RELEASE), re.S):
            self.assertTrue(body.isascii(), body[:80])
            self.assertNotIn('\t', body)

    def test_git_is_limited_to_the_reviewed_commands(self):
        allowed = {'fetch', 'rev-parse', 'log', 'status', 'merge-base', 'diff',
                   'show', 'reset'}
        # Вызов git -- `& git ...` или git в начале строки; текст сообщений
        # («git merge --ff-only failed») вызовом не считается.
        invoked = re.compile(r'(?:^|&\s*)git\s+(?:--no-pager\s+|-C\s+\S+\s+)*'
                             r'([a-z][a-z-]*)')
        for line in self.lines():
            for sub in invoked.findall(line.strip()):
                self.assertIn(sub, allowed, line)
            lowered = line.lower()
            for word in ('git pull', 'checkout', 'git push', 'rebase',
                         'git clean', 'stash', '--hard', 'git tag'):
                self.assertNotIn(word, lowered, line)
        release = release_block('Шаг 3')
        rollback = release_block('Откат')
        # Перемотка -- одна на весь ранбук, в шаге 3 и ровно на проверенный
        # коммит; откат -- `reset --keep` на коммит production, только в откате.
        self.assertEqual(text(RELEASE).count("'merge', '--ff-only'"), 1)
        self.assertIn("Invoke-Tool 'git' @('merge', '--ff-only', $release)",
                      release)
        resets = [line.strip() for line in self.lines()
                  if re.search(r'&\s*git\s+reset\b', line)]
        self.assertEqual(len(resets), 1)
        self.assertTrue(resets[0].startswith('& git reset --keep $baseline '))
        self.assertIn(resets[0], rollback)

    def test_services_stop_only_in_the_release_and_the_rollback(self):
        for line in self.lines():
            self.assertNotRegex(line, r'(?i)\bstart-service\b|nssm', line)
        stopping = {title for title, body in sections(RELEASE).items()
                    if 'Stop-Service' in body}
        self.assertEqual({title.split('.')[0].split(' —')[0]
                          for title in stopping}, {'Шаг 3', 'Откат'})
        for name in ('Шаг 3', 'Откат'):
            body = release_block(name)
            with self.subTest(block=name):
                stop = body.index('Stop-Service')
                self.assertLess(body.index('$stopped = $true'), stop)
                # `finally` главного try -- с начала строки; у функций свой,
                # с отступом.
                finally_at = re.search(r'^\} finally \{$', body, re.M).start()
                self.assertLess(stop, finally_at)
                after = body[finally_at:]
                self.assertIn('if ($stopped) {', after)
                self.assertIn('foreach ($name in $services) '
                              '{ Restart-Service -Name $name }', after)

    def test_the_release_follows_the_procedure_order(self):
        body = release_block('Шаг 3')
        marks = [
            'if ($open -gt 0)',                               # проверки
            'if ($head -ne $baseline)',
            'if (($migrations.Count -ne 1)',
            '$stopped = $true',                               # шаг 2 порядка
            'foreach ($name in $services) { Stop-Service',
            "tools\\check_db_lock.py', '--db', $db",          # шаг 5
            'Invoke-Tool $backupBat',                         # шаг 3 порядка
            "'merge', '--ff-only', $release",                 # шаг 4
            "tools\\check_migration_drift.py', '--db', $db)).Lines\n"
            "  $want1",                                       # шаг 6
            '$first = Invoke-Tool $py @($migration)',         # шаг 7
            '$second = Invoke-Tool $py @($migration)',
            '$drift2 = Get-Drift',
            '{ Restart-Service -Name $name }',                # шаг 8
            "($site + '/login')",                             # шаг 9
            "($site + '/agro-work/')",
        ]
        positions = [body.index(mark) for mark in marks]
        self.assertEqual(positions, sorted(positions))
        # Блокировка -- до копии и ещё раз перед миграцией: копия штатно
        # закрывает базу, а миграция не должна делить её ни с кем.
        self.assertEqual(body.count("tools\\check_db_lock.py', '--db', $db"), 2)
        self.assertLess(body.rindex("tools\\check_db_lock.py"),
                        body.index('$first = Invoke-Tool'))

    def test_the_check_step_changes_nothing(self):
        body = release_block('Шаг 2')
        for word in ('Stop-Service', 'Restart-Service', "'merge'", 'reset',
                     'Invoke-Tool $backupBat', 'migrate_agro_work_001.py\')'):
            self.assertNotIn(word, body)
        self.assertNotRegex(body, r'&\s*\$py\s+\$migration')

    def test_the_constants_are_the_real_ones(self):
        expected = {
            'prod': "'C:\\transport-report'",
            'db': "'C:\\transport-report\\instance\\transport.db'",
            'py': "'C:\\Program Files\\Python314\\python.exe'",
            'backupBat': "'C:\\transport-report\\backup_production_db.bat'",
            'errLog': "'C:\\transport-report\\logs\\error.log'",
            'services': "@('TransportReport', 'TransportBot', 'TransportBot003')",
            'site': "'http://10.103.25.14:5050'",
            'baseline': "'%s'" % BASELINE,
            'migration': "'migrate_agro_work_001.py'",
            'pendingId': "'%s (migrate_agro_work_001.py)'" % mig.MIGRATION_ID,
            'doneLine': "'%s'" % done_line(),
            'againLine': "'Already applied. Nothing to do.'",
            'work': "'C:\\VehicleSoft_AgroWork'",
        }
        reviewed = set()
        for name in ('Шаг 2', 'Шаг 3', 'Откат'):
            found = constants(release_block(name))
            with self.subTest(block=name):
                for key, value in found.items():
                    if key in expected:
                        self.assertEqual(value, expected[key], key)
                self.assertEqual(found['baseline'], expected['baseline'])
                self.assertRegex(found['reviewed'], r"^'[0-9a-f]{40}'$")
                reviewed.add(found['reviewed'])
                # Код выпуска -- код проверенного коммита; после него в main
                # -- только docs/.
                self.assertIn("Where-Object { $_ -notlike 'docs/*' }",
                              release_block(name))
                if name != 'Откат':
                    self.assertIn('Get-OpenItems @(& git show '
                                  '"${release}:docs/RELEASE_GATE.md")',
                                  release_block(name))
                    self.assertIn('if ($open -gt 0) { throw',
                                  release_block(name))
        self.assertEqual(len(reviewed), 1)
        self.assertIn('https://github.com/sINte3/vehicle-soft/pull/%s' % PR_NUMBER,
                      text(RELEASE))
        with open(os.path.join(REPO_ROOT, 'backup_production_db.bat'),
                  encoding='ascii') as fh:
            bat = fh.read()
        self.assertIn('--source "C:\\transport-report\\instance\\transport.db"',
                      bat)
        with open(os.path.join(REPO_ROOT, 'migrate_agro_work_001.py'),
                  encoding='utf-8') as fh:
            self.assertIn("print('Already applied. Nothing to do.')", fh.read())
        with open(os.path.join(REPO_ROOT, 'docs', 'DEPLOYED.md'),
                  encoding='utf-8') as fh:
            deployed = fh.read()
        # Блок выпуска готовился от записанного production baseline. После
        # выпуска строка production сменится, а baseline останется в журнале
        # релизов -- проверка не должна падать у того, кто её обновит.
        self.assertIn('`%s`' % BASELINE[:7], deployed)

    def test_the_pin_was_right_when_the_runbook_was_written(self):
        # Блок выкатывает код ровно проверенного коммита: всё, что изменилось
        # после него ДО последней правки ранбука, -- только docs/. Код,
        # влитый в main позже, ловят шаги 2 и 3 (они остановят выпуск), а не
        # эта проверка: сравнение с HEAD падало бы у каждого трека, который
        # добавил свой код, -- ничего не сломав. Держится там, где оба
        # коммита есть в клоне (локально; в CI клон глубиной 1).
        commit = constants(release_block('Шаг 3'))['reviewed'].strip("'")
        written = git('log', '-1', '--format=%H', '--',
                      'docs/AGRO_WORK_RELEASE_RUNBOOK.md').strip()
        if not (written and has_commit(commit) and has_commit(written)):
            self.skipTest('the pinned history is outside this clone')
        ancestor = subprocess.run(['git', 'merge-base', '--is-ancestor', commit,
                                   written], cwd=REPO_ROOT)
        self.assertEqual(ancestor.returncode, 0)
        names = git('diff', '--name-only', commit, written).split()
        self.assertEqual([n for n in names if not n.startswith('docs/')], [])

    def test_the_delta_carries_exactly_the_one_migration(self):
        # Дельта выпуска -- от production до проверенного коммита, а не до
        # HEAD: миграция, которую позже добавит другой трек, в этот выпуск не
        # едет (шаги 2 и 3 её не пропустят) и эту проверку не роняет.
        commit = constants(release_block('Шаг 3'))['reviewed'].strip("'")
        if not (has_commit(BASELINE) and has_commit(commit)):
            self.skipTest('the release delta is outside this clone')
        names = git('diff', '--name-only', BASELINE, commit).split()
        self.assertEqual([n for n in names if n.startswith('migrate_')],
                         ['migrate_agro_work_001.py'])

    def test_every_step_that_runs_a_script_first_goes_to_production(self):
        found = steps(RELEASE)
        self.assertGreaterEqual(len(found), 8)
        for title, section in found.items():
            lines = commands(section)
            # Строка Set-Content пишет содержимое .bat, а не запускает его:
            # путь `tools\...` в ней -- текст файла.
            # Постоянная блока (`$migration = 'migrate_...py'`) -- имя, а не
            # запуск.
            first = next((i for i, line in enumerate(lines)
                          if RELATIVE_SCRIPT.search(line)
                          and not line.startswith('Set-Content')
                          and not re.match(r"^\$[A-Za-z]+\s*= '", line)), None)
            if first is None:
                continue
            with self.subTest(step=title):
                before = [line.strip() for line in lines[:first]]
                if 'Set-Location -LiteralPath $prod' in before:
                    self.assertEqual(constants(section)['prod'],
                                     "'C:\\transport-report'")
                else:
                    self.assertIn('cd C:\\transport-report', before)

    def test_every_named_script_exists_and_python_is_quoted(self):
        names = set()
        for line in self.lines():
            names.update(re.findall(r'(tools\\[A-Za-z0-9_]+\.py)', line))
            names.update(re.findall(r'\b(migrate_[A-Za-z0-9_]+\.py)', line))
            if 'python.exe' not in line.lower() or line.startswith('Set-Content'):
                continue
            if re.match(r"^\$py\s+= '", line):
                continue                   # постоянная блока, проверена выше
            self.assertTrue(line.startswith(PYTHON), line)
        self.assertTrue(names)
        for name in names:
            path = os.path.join(REPO_ROOT, name.replace('\\', os.sep))
            self.assertTrue(os.path.isfile(path), name)

    def test_the_smoke_markers_are_real(self):
        with open(os.path.join(REPO_ROOT, 'templates', 'login.html'),
                  encoding='utf-8') as fh:
            self.assertIn('class="vs-login-form"', fh.read())
        with open(os.path.join(REPO_ROOT, 'agro_work_routes.py'),
                  encoding='utf-8') as fh:
            self.assertIn("url_prefix='/agro-work'", fh.read())
        self.assertIn("-notmatch 'vs-login-form'", release_block('Шаг 3'))

    def test_the_owner_decisions_come_from_the_verification_folder(self):
        body = ' '.join(self.lines())
        self.assertIn('agro_work_links.py --from-csv '
                      'C:\\VehicleSoft_AgroWork\\agro_work_unmatched.csv', body)
        self.assertIn('agro_work_methods.py --import '
                      'C:\\VehicleSoft_AgroWork\\agro_work_methods.xlsx', body)

    def test_the_nightly_task_runs_the_file_that_is_written(self):
        writes = [line for line in self.lines() if line.startswith('Set-Content')]
        self.assertEqual(len(writes), 2)            # 1 000 историй, затем 300
        targets = {re.search(r'-Path (\S+)', line).group(1) for line in writes}
        self.assertEqual(len(targets), 1)
        task = [line for line in self.lines() if 'schtasks /create' in line]
        self.assertEqual(len(task), 1)
        self.assertIn('/tr "%s"' % targets.pop(), task[0])
        self.assertIn('/ru SYSTEM', task[0])
        first, later = writes
        for line in writes:
            self.assertIn('tools\\agro_work_import.py --credentials '
                          'C:\\VehicleSoft_Secrets\\agro_work_credentials.txt',
                          line)
        # Решение владельца 29.09 (вопрос 10): пока догружается история --
        # 1 000 за ночь, потом умолчание.
        self.assertIn('--max-history 1000', first)
        self.assertNotIn('--max-history', later)

    def test_the_migration_line_is_what_the_migration_prints(self):
        self.assertIn(done_line(), text(RELEASE))

    def test_no_secret_and_the_credentials_file_is_never_shown(self):
        self.assertNotRegex(text(RELEASE), r'password=(?!`)\S')
        for line in self.lines():
            if 'agro_work_credentials.txt' in line:
                shown = re.match(r'\s*(Get-Content|type|cat)\b', line, re.I)
                self.assertIsNone(shown, line)


class ReleaseToolFormats(unittest.TestCase):
    """Разбор вывода в блоках -- по выводу НАСТОЯЩИХ инструментов.

    Блок шага 3 читает строки резервной копии, проверки блокировки, дрейфа
    миграций и самой миграции регулярными выражениями. Выражение, которое не
    совпадает с настоящим выводом, остановит выпуск на пустом месте -- или,
    хуже, не заметит, что копия не записалась.
    """

    @classmethod
    def setUpClass(cls):
        cls.out = real_outputs()

    def assertPatternInBlock(self, pattern):
        self.assertIn("'%s'" % pattern, release_block('Шаг 3'))

    def test_backup_lines(self):
        for pattern in BACKUP_PATTERNS.values():
            self.assertPatternInBlock(pattern)
        ok = self.out['backup_ok']
        self.assertEqual(ok['code'], 0)
        found = parse_backup(ok['lines'])
        self.assertTrue(found['ok'], ok['lines'])
        self.assertTrue(found['path'].endswith('.db'), found)
        self.assertFalse(parse_backup(self.out['backup_fail']['lines'])['ok'])
        self.assertEqual(self.out['backup_fail']['code'], 1)
        self.assertFalse(parse_backup(self.out['backup_mismatch']['lines'])['ok'])

    def test_drift_lines(self):
        for pattern in DRIFT_PATTERNS.values():
            self.assertPatternInBlock(pattern)
        before = parse_drift(self.out['drift_before']['lines'])
        merged = parse_drift(self.out['drift_merged']['lines'])
        migrated = parse_drift(self.out['drift_migrated']['lines'])
        pending_id = '%s (migrate_agro_work_001.py)' % mig.MIGRATION_ID
        self.assertGreater(before['registered'], 0)
        self.assertEqual(merged['pending'], sorted(before['pending'] + [pending_id]))
        self.assertEqual(migrated['pending'], before['pending'])
        self.assertEqual(migrated['registered'], before['registered'] + 1)
        extra = parse_drift(self.out['drift_extra_before']['lines'])
        self.assertEqual(len(extra['pending']), 1)          # чужая, не наша
        self.assertNotIn(pending_id, extra['pending'])

    def test_lock_lines(self):
        self.assertIn("'no process holds the database'", release_block('Шаг 3'))
        self.assertEqual(self.out['lock_clean']['code'], 0)
        stale = self.out['lock_stale']
        self.assertEqual(stale['code'], 3)
        self.assertTrue(any('no process holds the database' in line
                            for line in stale['lines']), stale['lines'])
        held = self.out['lock_held']
        self.assertEqual(held['code'], 2)
        self.assertFalse(any('no process holds the database' in line
                             for line in held['lines']), held['lines'])

    def test_gate_lines(self):
        # Блок читает гейт без кириллицы: первая таблица в пять столбцов --
        # «Открытые пункты», её строки -- открытые пункты. Сверка с разбором
        # по заголовку раздела на настоящем файле.
        with open(os.path.join(REPO_ROOT, 'docs', 'RELEASE_GATE.md'),
                  encoding='utf-8') as fh:
            lines = fh.read().splitlines()
        heading = next(i for i, line in enumerate(lines)
                       if line.startswith('## Открытые пункты'))
        after = next(i for i in range(heading + 1, len(lines))
                     if lines[i].startswith('## '))
        table = [line for line in lines[heading:after] if line.startswith('|')]
        self.assertEqual(open_items(lines), len(table) - 2)
        self.assertEqual(open_items(gate_lines(0)), 0)
        self.assertEqual(open_items(gate_lines(2)), 2)
        self.assertEqual(open_items([]), -1)
        for name in ('Шаг 2', 'Шаг 3'):
            self.assertIn("-eq '%s'" % GATE_SEPARATOR, release_block(name))

    def test_migration_lines(self):
        self.assertEqual(self.out['migrate_done']['code'], 0)
        self.assertIn(done_line(), self.out['migrate_done']['lines'])
        self.assertEqual(self.out['migrate_again']['code'], 0)
        self.assertIn('Already applied. Nothing to do.',
                      self.out['migrate_again']['lines'])
        self.assertEqual(self.out['migrate_fail']['code'], 1)
        self.assertNotIn(done_line(), self.out['migrate_fail']['lines'])


POWERSHELL = os.environ.get('AGRO_WORK_POWERSHELL')
RELEASE_HASH = '5e1ea5e0' + 'c0ffee' * 5 + 'ab'
REVIEWED_HASH = '7e71e3ed' + 'bead00' * 5 + 'cd'
TEST_SERVICES = ['AgroTestReport', 'AgroTestBot', 'AgroTestBot003']
LOGIN_BODY = '<form method="post" class="vs-login-form">'


@unittest.skipUnless(POWERSHELL, 'AGRO_WORK_POWERSHELL is not set')
class ReleaseBlocksInPowerShell(unittest.TestCase):
    """Блоки ранбука исполняются против подставного сервера.

    Путь каждой остановки: что тронуто и что нет, подняты ли службы, какую
    строку RESULT увидит владелец. Службы, пути, сайт и питон в блоке
    подменяются до запуска (prepare), поэтому стенд не может дотянуться до
    настоящих, даже будучи запущенным на боевом сервере.
    """

    @classmethod
    def setUpClass(cls):
        cls.out = real_outputs()

    def setUp(self):
        self.folder = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.folder, True)

    def scenario(self, **changes):
        value = {
            'Host': 'SRV-YOQSH', 'Admin': True, 'Head': BASELINE,
            'Release': RELEASE_HASH, 'Reviewed': REVIEWED_HASH,
            'Ancestry': [[BASELINE, RELEASE_HASH], [REVIEWED_HASH, RELEASE_HASH],
                         [BASELINE, REVIEWED_HASH]],
            'ChangedAfterReviewed': ['docs/RELEASE_GATE.md'],
            'GateLines': gate_lines(0),
            'FetchCode': 0, 'MergeCode': 0, 'ResetCode': 0,
            'DiffNames': ['agro_work/store.py', 'migrate_agro_work_001.py',
                          'docs/AGRO_WORK_RELEASE_RUNBOOK.md'],
            'Modified': [], 'Services': TEST_SERVICES, 'StopFails': [],
            'FreeBytes': 50 * 1024 ** 3, 'LockKind': 'clean',
            'BackupKind': 'ok', 'MigrateFirst': 'done',
            'MigrateSecond': 'again',
            'Web': {'/login': {'Status': 200, 'Body': LOGIN_BODY},
                    '/agro-work/': {'Status': 200, 'Body': LOGIN_BODY}},
            'ErrorLogAfterStart': None,
            'Outputs': dict(self.out),
        }
        value.update(changes)
        return value

    def run_block(self, name, scenario):
        folder = tempfile.mkdtemp(dir=self.folder)       # свой на каждый прогон
        work = os.path.join(folder, 'work')
        prod = os.path.join(folder, 'prod')
        os.makedirs(os.path.join(prod, 'instance'))
        db = os.path.join(prod, 'instance', 'transport.db')
        errlog = os.path.join(prod, 'error.log')
        files = []
        for base in ('credentials.txt', 'unmatched.csv', 'methods.xlsx', 'db'):
            path = db if base == 'db' else os.path.join(folder, base)
            with open(path, 'w', encoding='ascii') as fh:
                fh.write('x' * 64)
            files.append(path)
        with open(errlog, 'w', encoding='ascii') as fh:
            fh.write('INFO:waitress:Serving on http://0.0.0.0:5050\n')
        scenario['ErrorLog'] = errlog
        log = os.path.join(work, 'release.log')
        body = prepare(release_block(name), {
            'prod': "'%s'" % prod, 'db': "'%s'" % db,
            'py': "'Invoke-FakePython'", 'backupBat': "'Invoke-FakeBackup'",
            'errLog': "'%s'" % errlog,
            'services': '@(%s)' % ', '.join("'%s'" % s for s in TEST_SERVICES),
            'files': '@(%s)' % ', '.join("'%s'" % f for f in files[:3]),
            'site': "'http://agro-test.invalid'", 'work': "'%s'" % work,
            'log': "'%s'" % log, 'admin': '$global:scenario.Admin',
            'reviewed': "'%s'" % REVIEWED_HASH,
        })
        block_file = os.path.join(folder, 'block.ps1')
        scenario_file = os.path.join(folder, 'scenario.json')
        calls_file = os.path.join(folder, 'calls.txt')
        with open(block_file, 'w', encoding='ascii') as fh:
            fh.write(body)
        with open(scenario_file, 'w', encoding='ascii') as fh:
            json.dump(scenario, fh, ensure_ascii=True)
        proc = subprocess.run(
            [POWERSHELL, '-NoProfile', '-NonInteractive', '-ExecutionPolicy',
             'Bypass', '-File', HARNESS, '-BlockFile', block_file,
             '-ScenarioFile', scenario_file, '-CallsFile', calls_file],
            capture_output=True, text=True, timeout=300)
        output = proc.stdout + proc.stderr
        results = [line for line in output.splitlines()
                   if line.startswith('RESULT: ')]
        self.assertEqual(len(results), 1, output)
        with open(calls_file, encoding='utf-8-sig') as fh:
            calls = [line for line in fh.read().splitlines() if line]
        transcript = ''
        if os.path.exists(log):
            with open(log, encoding='utf-8-sig', errors='replace') as fh:
                transcript = fh.read()
        return results[0], calls, output, transcript

    def assertInOrder(self, calls, *prefixes):
        where = []
        for prefix in prefixes:
            hits = [i for i, call in enumerate(calls) if call.startswith(prefix)]
            self.assertTrue(hits, '%s not called: %s' % (prefix, calls))
            where.append(hits[0])
        self.assertEqual(where, sorted(where), calls)

    def assertUntouched(self, calls):
        for prefix in ('Stop-Service', 'Restart-Service', 'Start-Service',
                       'backup', 'git merge ', 'git reset', 'python migrate'):
            self.assertFalse([c for c in calls if c.startswith(prefix)],
                             '%s: %s' % (prefix, calls))

    def assertBackUp(self, calls):
        stops = [c for c in calls if c.startswith('Stop-Service')]
        starts = [c for c in calls if c.startswith('Restart-Service')]
        self.assertEqual(len(stops), 3, calls)
        self.assertEqual(sorted(s.split()[1] for s in starts), sorted(TEST_SERVICES))
        self.assertLess(calls.index(stops[0]), calls.index(starts[0]))
        self.assertFalse([c for c in calls if c.startswith('Start-Service')])

    # --- шаг 3: выпуск ---------------------------------------------------------

    def test_release_passes_in_the_order_of_the_procedure(self):
        result, calls, output, transcript = self.run_block('Шаг 3', self.scenario())
        self.assertEqual(result, 'RESULT: RELEASE PASSED', output)
        self.assertInOrder(calls, 'git fetch', 'python tools\\check_migration_drift',
                           'Stop-Service', 'python tools\\check_db_lock', 'backup',
                           'git merge --ff-only ' + RELEASE_HASH,
                           'python migrate_agro_work_001.py', 'Restart-Service',
                           'GET /login', 'GET /agro-work/')
        self.assertEqual(calls.count('python migrate_agro_work_001.py'), 2)
        self.assertEqual(len([c for c in calls if 'check_db_lock' in c]), 2)
        self.assertBackUp(calls)
        self.assertIn('MIGRATIONS REGISTERED: ', output)
        self.assertIn('BACKUP: ', output)
        self.assertIn('NEW TRACEBACKS IN logs\\error.log: 0', output)
        self.assertIn('PROGRAM VERSION NOW: %s' % RELEASE_HASH[:7], output)
        self.assertIn('RESULT: RELEASE PASSED', transcript)

    def test_a_stale_wal_is_the_known_noise_not_a_stop(self):
        result, calls, output, _ = self.run_block('Шаг 3',
                                                  self.scenario(LockKind='stale'))
        self.assertEqual(result, 'RESULT: RELEASE PASSED', output)

    def test_another_track_pending_migration_is_left_alone(self):
        out = dict(self.out)
        for key in ('before', 'merged', 'migrated'):
            out['drift_' + key] = out['drift_extra_' + key]
        result, calls, output, _ = self.run_block('Шаг 3',
                                                  self.scenario(Outputs=out))
        self.assertEqual(result, 'RESULT: RELEASE PASSED', output)
        self.assertEqual(calls.count('python migrate_agro_work_001.py'), 2)

    def test_checks_that_fail_change_nothing(self):
        cases = {
            'not run as administrator': dict(Admin=False),
            'not SRV-YOQSH': dict(Host='SOME-PC'),
            'git fetch failed': dict(FetchCode=128),
            'not in main yet': dict(Ancestry=[[BASELINE, RELEASE_HASH]]),
            'changes outside docs/: docs/a.md': None,
            'the release gate is closed: 1 open item': dict(GateLines=gate_lines(1)),
            'could not be read': dict(GateLines=[]),
            'already on the server': dict(Head=RELEASE_HASH),
            'this release was prepared for': dict(Head='0' * 40),
            'edited on this server': dict(Modified=[' M app.py']),
            'does not continue': dict(Ancestry=[[REVIEWED_HASH, RELEASE_HASH]]),
            'expected only migrate_agro_work_001.py': dict(DiffNames=[
                'migrate_agro_work_001.py', 'migrate_drones_x_001.py']),
        }
        cases['changes outside docs/: docs/a.md'] = dict(
            ChangedAfterReviewed=['docs/a.md', 'drones.py'])
        for message, change in cases.items():
            with self.subTest(case=message):
                result, calls, output, _ = self.run_block(
                    'Шаг 3', self.scenario(**change))
                self.assertTrue(result.startswith('RESULT: STOP - '), output)
                self.assertIn(message.replace(': docs/a.md', ''), result)
                self.assertIn('Nothing was changed', output)
                self.assertUntouched(calls)

    def test_a_stop_before_the_update_brings_the_old_version_back(self):
        cases = {
            'did not stop within 90 seconds': dict(StopFails=['AgroTestBot']),
            'held by another program': dict(LockKind='held'),
            'the backup did not pass': dict(BackupKind='fail'),
            'the backup did not pass ': dict(BackupKind='mismatch'),
            'git merge --ff-only failed': dict(MergeCode=1),
        }
        for message, change in cases.items():
            with self.subTest(case=message):
                result, calls, output, _ = self.run_block(
                    'Шаг 3', self.scenario(**change))
                self.assertTrue(result.startswith('RESULT: STOP - '), output)
                self.assertIn(message.strip(), result)
                self.assertBackUp(calls)
                self.assertFalse([c for c in calls
                                  if c.startswith('python migrate')], calls)
                self.assertIn('PROGRAM VERSION NOW: %s' % BASELINE[:7], output)
        # Копия, которой нет, -- до перемотки: код не сдвинулся.
        result, calls, _, _ = self.run_block('Шаг 3',
                                             self.scenario(BackupKind='fail'))
        self.assertFalse([c for c in calls if c.startswith('git merge ')])

    def test_a_failed_migration_restarts_the_new_code_and_stops(self):
        result, calls, output, _ = self.run_block(
            'Шаг 3', self.scenario(MigrateFirst='fail'))
        self.assertEqual(result, 'RESULT: STOP - the migration did not finish '
                                 '(exit 1)', output)
        self.assertEqual(calls.count('python migrate_agro_work_001.py'), 1)
        self.assertBackUp(calls)
        self.assertIn('PROGRAM VERSION NOW: %s' % RELEASE_HASH[:7], output)
        self.assertNotIn('GET /login', calls)

    def test_a_second_run_that_migrates_again_is_a_stop(self):
        result, calls, output, _ = self.run_block(
            'Шаг 3', self.scenario(MigrateSecond='done'))
        self.assertIn('second run of the migration', result)
        self.assertBackUp(calls)

    def test_unexpected_drift_after_the_update_stops_before_the_migration(self):
        out = dict(self.out)
        out['drift_merged'] = out['drift_before']     # файл миграции не приехал
        result, calls, output, _ = self.run_block('Шаг 3',
                                                  self.scenario(Outputs=out))
        self.assertIn('pending migrations are', result)
        self.assertFalse([c for c in calls if c.startswith('python migrate')])
        self.assertBackUp(calls)

    def test_a_dead_site_after_the_start_is_a_stop(self):
        web = {'/login': {'Status': 503, 'Body': ''},
               '/agro-work/': {'Status': 503, 'Body': ''}}
        result, calls, output, _ = self.run_block('Шаг 3', self.scenario(Web=web))
        self.assertIn('the login page did not open', result)
        self.assertBackUp(calls)
        web = {'/login': {'Status': 200, 'Body': LOGIN_BODY},
               '/agro-work/': {'Status': 404, 'Body': 'Not Found'}}
        result, calls, output, _ = self.run_block('Шаг 3', self.scenario(Web=web))
        self.assertIn('/agro-work/ answered', result)

    def test_new_tracebacks_after_the_start_are_a_warning(self):
        result, calls, output, _ = self.run_block('Шаг 3', self.scenario(
            ErrorLogAfterStart='Traceback (most recent call last):\n  boom'))
        self.assertTrue(result.startswith(
            'RESULT: RELEASE PASSED WITH A WARNING - new errors'), output)
        self.assertIn('NEW TRACEBACKS IN logs\\error.log: 1', output)

    # --- шаг 2: проверка -------------------------------------------------------

    def test_the_check_passes_and_touches_nothing(self):
        result, calls, output, transcript = self.run_block('Шаг 2', self.scenario())
        self.assertEqual(result, 'RESULT: CHECK PASSED - go on to step 3', output)
        self.assertUntouched(calls)
        self.assertIn('git fetch --quiet origin', calls)
        self.assertIn('RELEASE: %s -> %s' % (BASELINE, RELEASE_HASH), output)
        self.assertIn('Merge pull request #154', output)
        self.assertIn('RESULT: CHECK PASSED', transcript)

    def test_the_check_stops_on_what_the_release_would_stop_on(self):
        cases = {
            'the release gate is closed': dict(GateLines=gate_lines(1)),
            'not in main yet': dict(Ancestry=[[BASELINE, RELEASE_HASH]]),
            'changes outside docs/': dict(ChangedAfterReviewed=['app.py']),
            'already on the server': dict(Head=RELEASE_HASH),
            'drive D:': dict(FreeBytes=10),
            'drive D: not found': dict(FreeBytes=None),
            'not run as administrator': dict(Admin=False),
        }
        for message, change in cases.items():
            with self.subTest(case=message):
                result, calls, output, _ = self.run_block(
                    'Шаг 2', self.scenario(**change))
                self.assertTrue(result.startswith('RESULT: STOP - '), output)
                self.assertIn(message, result)
                self.assertUntouched(calls)

    # --- откат -------------------------------------------------------------------

    def test_the_rollback_returns_the_production_commit(self):
        result, calls, output, _ = self.run_block('Откат',
                                                  self.scenario(Head=RELEASE_HASH))
        self.assertEqual(result, 'RESULT: ROLLBACK PASSED', output)
        self.assertInOrder(calls, 'Stop-Service', 'git reset --keep ' + BASELINE,
                           'Restart-Service', 'GET /login')
        self.assertBackUp(calls)
        self.assertIn('ROLLED BACK: %s -> %s' % (RELEASE_HASH, BASELINE), output)

    def test_the_rollback_refuses_what_it_did_not_deploy(self):
        cases = {
            'nothing to roll back': dict(Head=BASELINE),
            'not at this release': dict(Head='a' * 40),
            'past this release': dict(Head=RELEASE_HASH,
                                      ChangedAfterReviewed=['gps/daily.py']),
        }
        for message, change in cases.items():
            with self.subTest(case=message):
                result, calls, output, _ = self.run_block(
                    'Откат', self.scenario(**change))
                self.assertIn(message, result)
                self.assertUntouched(calls)


# --- помощники выпуска --------------------------------------------------------

HARNESS = os.path.join(REPO_ROOT, 'tests', 'agro_work_release_harness.ps1')
BASELINE = 'eb7d0034333e996258232e6e254806c656a99b47'
PR_NUMBER = 154
GATE_SEPARATOR = '|---|---|---|---|---|'
BACKUP_PATTERNS = {
    'source': r'Source size\s*:\s*([\d,]+) bytes',
    'dest': r'Dest size\s*:\s*([\d,]+) bytes',
    'integrity': 'Integrity check : ok',
    'success': '^SUCCESS: Backup written to:',
}
DRIFT_PATTERNS = {
    'registered': r'^registered migrations: (\d+);',
    'header': r'^file-but-not-registered: \d+$',
    'item': r'^  - (.+)$',
}


def git(*args):
    return subprocess.run(['git'] + list(args), cwd=REPO_ROOT,
                          capture_output=True, text=True, check=True).stdout


def has_commit(commit):
    return subprocess.run(['git', 'cat-file', '-e', commit + '^{commit}'],
                          cwd=REPO_ROOT, capture_output=True).returncode == 0


def open_items(lines):
    """То же, что Get-OpenItems шагов 2 и 3, -- на Python."""
    for i, line in enumerate(lines):
        if line.strip() == GATE_SEPARATOR:
            count = 0
            for row in lines[i + 1:]:
                if not row.startswith('|'):
                    break
                count += 1
            return count
    return -1


def gate_lines(rows):
    """Настоящий docs/RELEASE_GATE.md с `rows` открытыми пунктами."""
    with open(os.path.join(REPO_ROOT, 'docs', 'RELEASE_GATE.md'),
              encoding='utf-8') as fh:
        lines = fh.read().splitlines()
    at = lines.index(GATE_SEPARATOR) + 1
    end = at
    while end < len(lines) and lines[end].startswith('|'):
        end += 1
    extra = ['| Трек %d | что | PR | что не проверено | кто |' % n
             for n in range(rows)]
    return lines[:at] + extra + lines[end:]


def done_line():
    return ('Done. %d agro_work tables (%d columns), %d indexes and %d '
            'triggers are in place.'
            % (len(mig.EXPECTED_COLUMNS),
               sum(len(c) for c in mig.EXPECTED_COLUMNS.values()),
               len(mig.INDEXES), len(mig.TRIGGERS)))


def sections(path=RELEASE):
    """{заголовок: текст} для каждого раздела «## »."""
    return {match.group(1): match.group(2) for match in re.finditer(
        r'^## ([^\n]*)\n(.*?)(?=^## |\Z)', text(path), re.S | re.M)}


def release_block(prefix):
    """Большой блок `& { ... }` раздела, чей заголовок начинается с prefix."""
    found = [body for title, body in sections(RELEASE).items()
             if title.startswith(prefix)]
    assert len(found) == 1, (prefix, len(found))
    blocks = [b for b in re.findall(r'```powershell\n(.*?)```', found[0], re.S)
              if b.startswith('& {')]
    assert len(blocks) == 1, (prefix, len(blocks))
    return blocks[0]


def constants(block):
    """{имя: значение} для присваиваний с начала строки: `$prod = '...'`."""
    return {m.group(1): m.group(2).strip() for m in re.finditer(
        r'^\$([A-Za-z]+)\s*= (.+)$', block, re.M)}


def prepare(block, values):
    """Подменить постоянные блока; каждая обязана существовать ровно раз."""
    for name, value in values.items():
        pattern = re.compile(r'^\$%s(\s*)= .+$' % name, re.M)
        if name in constants(block):
            assert len(pattern.findall(block)) == 1, name
            block = pattern.sub(lambda m: '$%s%s= %s' % (name, m.group(1), value),
                                block)
    return block


def parse_backup(lines):
    """То же, что Get-Backup шага 3, -- на Python, для сверки с настоящим."""
    source = dest = path = None
    integrity = False
    for i, line in enumerate(lines):
        m = re.search(BACKUP_PATTERNS['source'], line)
        source = m.group(1) if m else source
        m = re.search(BACKUP_PATTERNS['dest'], line)
        dest = m.group(1) if m else dest
        integrity = integrity or bool(re.search(BACKUP_PATTERNS['integrity'], line))
        if re.search(BACKUP_PATTERNS['success'], line) and i + 1 < len(lines):
            path = lines[i + 1].strip()
    return {'ok': bool(path) and integrity and bool(source) and source == dest,
            'path': path}


def parse_drift(lines):
    """То же, что Get-Drift шага 3."""
    registered, pending, inside = -1, [], False
    for line in lines:
        m = re.search(DRIFT_PATTERNS['registered'], line)
        if m:
            registered = int(m.group(1))
        if re.search(DRIFT_PATTERNS['header'], line):
            inside = True
            continue
        m = re.search(DRIFT_PATTERNS['item'], line)
        if inside and m:
            pending.append(m.group(1))
            continue
        inside = False
    return {'registered': registered, 'pending': sorted(pending)}


_OUTPUTS = {}


def real_outputs():
    """Вывод настоящих инструментов на временных базах, один раз на прогон."""
    if _OUTPUTS:
        return _OUTPUTS
    folder = tempfile.mkdtemp()
    try:
        _OUTPUTS.update(_backup_outputs(folder))
        _OUTPUTS.update(_lock_outputs(folder))
        _OUTPUTS.update(_drift_outputs(folder))
        _OUTPUTS.update(_migration_outputs(folder))
    finally:
        shutil.rmtree(folder, True)
    return _OUTPUTS


def _entry(code, lines):
    return {'code': int(code or 0), 'lines': [line.rstrip() for line in lines]}


def _new_db(path, ddl='CREATE TABLE equipment (id INTEGER PRIMARY KEY, '
                     'name TEXT, plate TEXT)'):
    con = sqlite3.connect(path)
    con.execute(ddl)
    con.commit()
    con.close()
    return path


def _backup_outputs(folder):
    db = _new_db(os.path.join(folder, 'backup_source.db'))
    script = os.path.join(REPO_ROOT, 'backup_transport_db.py')

    def run(source):
        proc = subprocess.run([sys.executable, script, '--source', source,
                               '--dest-dir', os.path.join(folder, 'backups')],
                              capture_output=True, text=True)
        # Строки самого backup_production_db.bat после питона.
        tail = ('Backup completed successfully.' if proc.returncode == 0 else
                'Backup FAILED. See backup_transport_db.py output above.')
        return _entry(proc.returncode, proc.stdout.splitlines() + [tail])

    ok = run(db)
    mismatch = dict(ok, lines=[re.sub(r'(Dest size\s*:\s*)[\d,]+', r'\g<1>1,024',
                                      line) for line in ok['lines']])
    return {'backup_ok': ok, 'backup_mismatch': mismatch,
            'backup_fail': run(os.path.join(folder, 'missing.db'))}


def _lock_outputs(folder):
    def run(db, *extra):
        out = io.StringIO()
        with redirect_stdout(out), redirect_stderr(out):
            code = check_db_lock.main(['--db', db] + list(extra))
        return _entry(code, out.getvalue().splitlines())

    clean = run(_new_db(os.path.join(folder, 'lock_clean.db')))
    stale_db = _new_db(os.path.join(folder, 'lock_stale.db'))
    for suffix in ('-wal', '-shm'):
        open(stale_db + suffix, 'wb').close()
    stale = run(stale_db)
    held_db = _new_db(os.path.join(folder, 'lock_held.db'))
    holder = sqlite3.connect(held_db)
    try:
        holder.execute('PRAGMA locking_mode=EXCLUSIVE')
        holder.execute('BEGIN EXCLUSIVE')
        held = run(held_db, '--busy-timeout-ms', '50')
    finally:
        holder.rollback()
        holder.close()
    return {'lock_clean': clean, 'lock_stale': stale, 'lock_held': held}


def _drift_outputs(folder):
    # Разбор старых migrate_*.py печатает DeprecationWarning о «\P» в
    # migrate_add_wialon.py -- исторический шум, не предмет этой проверки.
    with warnings.catch_warnings():
        warnings.simplefilter('ignore')
        return _drift_outputs_quiet(folder)


def _drift_outputs_quiet(folder):
    ids, _ = check_migration_drift.scan_migration_files(REPO_ROOT)
    legacy = set(check_migration_drift.read_backfill_map(REPO_ROOT))
    ours = mig.MIGRATION_ID
    other = sorted(set(ids) - {ours})[0]
    old_root = os.path.join(folder, 'old_tree')
    os.makedirs(old_root)
    for name in os.listdir(REPO_ROOT):
        if (name.startswith('migrate_') and name.endswith('.py')
                and name != 'migrate_agro_work_001.py'):
            shutil.copy(os.path.join(REPO_ROOT, name), old_root)

    def registry(path, names):
        con = sqlite3.connect(path)
        con.execute(migration_utils._CREATE_TABLE_SQL)
        con.executemany('INSERT INTO schema_migrations (name, applied_at) '
                        "VALUES (?, '2026-09-30 12:00:00')",
                        [(n,) for n in sorted(names)])
        con.commit()
        con.close()
        return path

    def run(db, root):
        out = io.StringIO()
        with redirect_stdout(out):
            code = check_migration_drift.main(['--db', db, '--root', root])
        return _entry(code, out.getvalue().splitlines())

    result = {}
    for prefix, skip in (('drift_', set()), ('drift_extra_', {other})):
        known = (set(ids) | legacy) - {ours} - skip
        before = registry(os.path.join(folder, prefix + 'before.db'), known)
        after = registry(os.path.join(folder, prefix + 'after.db'), known | {ours})
        result[prefix + 'before'] = run(before, old_root)
        result[prefix + 'merged'] = run(before, REPO_ROOT)
        result[prefix + 'migrated'] = run(after, REPO_ROOT)
    return result


def _migration_outputs(folder):
    def run(db):
        saved = (mig.DB_PATH, migration_utils.DB_PATH)
        mig.DB_PATH = migration_utils.DB_PATH = db
        out = io.StringIO()
        try:
            with redirect_stdout(out):
                try:
                    mig.run()
                    code = 0
                except SystemExit as exc:
                    code = exc.code
        finally:
            mig.DB_PATH, migration_utils.DB_PATH = saved
        return _entry(code, out.getvalue().splitlines())

    db = _new_db(os.path.join(folder, 'migrate.db'))
    done = run(db)
    again = run(db)
    fail = run(_new_db(os.path.join(folder, 'migrate_fail.db'),
                       'CREATE TABLE users (id INTEGER PRIMARY KEY)'))
    return {'migrate_done': done, 'migrate_again': again, 'migrate_fail': fail}


if __name__ == '__main__':
    unittest.main()
