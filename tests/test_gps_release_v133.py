# -*- coding: utf-8 -*-
"""Выпуск v1.33 на production: docs/GPS_RELEASE_V133_RUNBOOK.md.

Выпуск -- вердикты метода «время» на «Сверке» (`TIME_VERDICTS_PUBLISHED =
True`, PR #254: проверка часов против счётчика моточасов Wialon 29.10 --
MET) и строка-пояснение над списком «Заявка → работа» (коммит выпуска).
Скрипты недельной выгрузки 1С (GPS-38, PR #249) едут вместе, но программа их
не вызывает. Без миграции. Production -- `c4b0396` (выпуск v1.32), а `main`
ушёл дальше: выпуск ЗАКРЕПЛЁН на коммите `167c210`, как выпуск v1.32 был
закреплён на `c4b0396` (PR #251), и всё, что влито в `main` позже, на сервер
не едет.

Блоки владелец вставляет в Windows PowerShell 5.1 на боевом сервере, и шаг 3
останавливает три службы. Это блоки выпуска v1.31 (прошли на этом сервере
22.10), в которых сменились постоянные, а строки, читавшие выпуск из
origin/main, заменены строками закреплённого выпуска v1.32 (прошли здесь
30.10) -- слово в слово. Здесь держится:

  * текст: нет плейсхолдеров и `&&`, ASCII без табуляций, git -- только
    проверенные команды, службы останавливаются только в выпуске и откате,
    порядок процедуры, проверка ничего не меняет, постоянные настоящие,
    закрепление верно, в дельте нет миграций и общих файлов устава, а код
    дельты -- ровно вердикты «время», пояснение и скрипты 1С, которые
    программа не вызывает;
  * блоки -- блоки v1.31 после ровно перечисленных замен, и каждая
    вставленная строка есть в блоках выпуска v1.32; функции блоков дословно
    те же, что в выпуске v1.20;
  * то, что ранбук называет новым, в production действительно новое;
  * то, что шаг 4 велит увидеть, -- подписи шаблонов и поведение кода;
  * сами блоки исполняются против подставных git, служб, питона и сайта
    (tests/agro_work_release_harness.ps1) во всех путях «стоп», включая
    закрепление: выпуск не в `main`, файл выпуска уже на сервере, `main`
    впереди выпуска; на PowerShell 7 и -- в CI -- на Windows PowerShell 5.1.

Запуск: python -m unittest tests.test_gps_release_v133 -v
Блоки в PowerShell: GPS_RELEASE_POWERSHELL=pwsh (или powershell) в окружении.
"""

import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import unittest

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if REPO_ROOT not in sys.path:
    sys.path.insert(0, REPO_ROOT)

from tests.test_agro_work_runbook import (                    # noqa: E402
    BACKUP_PATTERNS, DRIFT_PATTERNS, GATE_SEPARATOR, HARNESS, PYTHON,
    commands, constants, gate_lines, git, has_commit, open_items,
    parse_backup, parse_drift, prepare, real_outputs, sections, steps, text)
from tests.test_agro_work_runbook import RELEASE as V120     # noqa: E402
from tests.test_agro_work_release_v122 import (              # noqa: E402
    functions, release_block)

RELEASE = os.path.join(REPO_ROOT, 'docs', 'GPS_RELEASE_V133_RUNBOOK.md')
V131 = os.path.join(REPO_ROOT, 'docs', 'GPS_RELEASE_V131_RUNBOOK.md')
V132 = os.path.join(REPO_ROOT, 'docs', 'FIN_RECON_002_RELEASE_V132_RUNBOOK.md')
THIS_TEST = 'tests/test_gps_release_v133.py'
# [REASON]: то же правило, что у выпусков v1.20-v1.31: относительный путь
# скрипта допустим только после перехода в папку программы.
RELATIVE_SCRIPT = re.compile(r'(?<![\\A-Za-z:-])(tools\\|migrate_[A-Za-z0-9_]+\.py'
                             r'|run_server\.py|-m unittest)')
# Production на момент подготовки: строка production в docs/DEPLOYED.md
# (выпуск v1.32, 30.10).
BASELINE = 'c4b039630fd75264994267abe69f89d9eaba7632'
# Коммит выпуска: пояснение над списком «Заявка → работа» поверх main с
# PR #256. Его и проверила сессия; ранбук и тест едут после него.
PINNED = '167c210e3ae1fc37b5d205b21ee59ebe2b76c092'
SERVICES = "@('TransportReport', 'TransportBot', 'TransportBot003')"
BLOCKS = ('Шаг 2', 'Шаг 3', 'Откат')
LIVE_DB = 'C:\\transport-report\\instance\\transport.db'
# Реестр миграций production после выпуска v1.32 (docs/DEPLOYED.md).
REGISTERED = 65

# Замены, которыми блоки v1.31 становятся блоками v1.33. Справа -- строки
# закреплённого выпуска v1.32; тест ниже требует каждую из них в его блоках.
READ_MAIN_V131 = """\
  $release = [string](& git rev-parse origin/main)
  & git merge-base --is-ancestor $reviewed $release
  if ($LASTEXITCODE -ne 0) { throw 'the checked version is not in main yet - was the release PR merged in step 1?' }
  $changed = @(& git diff --name-only $reviewed $release | Where-Object { $_ -notlike 'docs/*' })
  if ($changed.Count -gt 0) { throw "after the checked version main got changes outside docs/: $($changed -join ', ')" }
  $open = Get-OpenItems @(& git show "${release}:docs/RELEASE_GATE.md")
"""
READ_MAIN_PINNED = """\
  $main = [string](& git rev-parse origin/main)
  & git merge-base --is-ancestor $release $main
  if ($LASTEXITCODE -ne 0) { throw "the release commit is not in main: $release, main $main" }
  & git merge-base --is-ancestor $reviewed $release
  if ($LASTEXITCODE -ne 0) { throw 'the release commit does not contain the checked version' }
  $changed = @(& git diff --name-only $reviewed $release | Where-Object { ($_ -notlike 'docs/*') -and ($_ -notlike '.github/*') -and ($_ -ne $runbookTest) })
  if ($changed.Count -gt 0) { throw "between the checked version and the release commit there are changes outside docs/: $($changed -join ', ')" }
  $open = Get-OpenItems @(& git show "${main}:docs/RELEASE_GATE.md")
"""
RELEASE_LINE = '  Write-Host "RELEASE: $head -> $release"\n'
UNTRACKED = ("  $untracked = @(& git status --porcelain --untracked-files=all | "
             "Where-Object { $_ -match '^\\?\\? ' })\n")
CLASH = """\
  $clash = @(& git diff --name-only --diff-filter=A $head $release | Where-Object { Test-Path -LiteralPath (Join-Path $prod $_) })
  if ($clash.Count -gt 0) { throw "files of the release already lie on the server: $($clash -join ', ')" }
"""
UNTRACKED_LINE = ('  Write-Host "UNTRACKED FILES: $($untracked.Count), none of them '
                  'is a file of the release"\n')
LATER = """\
  $later = @(& git --no-pager log --format=%h "$release..$main")
  Write-Host "MAIN: $main - $($later.Count) later commit(s) of main are not taken"
"""
ROLLBACK_V131 = ("  $changed = @(& git diff --name-only $reviewed $head | "
                 "Where-Object { $_ -notlike 'docs/*' })\n")
ROLLBACK_PINNED = ("  $changed = @(& git diff --name-only $reviewed $head | "
                   "Where-Object { ($_ -notlike 'docs/*') -and ($_ -notlike "
                   "'.github/*') -and ($_ -ne $runbookTest) })\n")
PIN_CONSTANTS = ("$release   = '@reviewed@'\n"
                 "$runbookTest = '%s'\n" % THIS_TEST)
EDITS = {
    'Шаг 2': [(READ_MAIN_V131, READ_MAIN_PINNED),
              (RELEASE_LINE, UNTRACKED + CLASH + UNTRACKED_LINE + RELEASE_LINE
               + LATER)],
    'Шаг 3': [(READ_MAIN_V131, READ_MAIN_PINNED),
              (RELEASE_LINE, CLASH + RELEASE_LINE + LATER)],
    'Откат': [(ROLLBACK_V131, ROLLBACK_PINNED)],
}


def block(prefix):
    return release_block(prefix, path=RELEASE)


def raw_step(prefix):
    """Текст раздела ранбука, чей заголовок начинается с prefix, как есть."""
    found = [body for title, body in sections(RELEASE).items()
             if title.startswith(prefix)]
    assert len(found) == 1, (prefix, len(found))
    return found[0]


def step(prefix):
    """Тот же раздел в одну строку: для фраз, перенесённых по словам."""
    return ' '.join(raw_step(prefix).split())


def flat():
    return ' '.join(text(RELEASE).split())


def read(*parts):
    with open(os.path.join(REPO_ROOT, *parts), encoding='utf-8') as fh:
        return fh.read()


def reviewed():
    return constants(block('Шаг 3'))['reviewed'].strip("'")


class ReleaseV133Runbook(unittest.TestCase):
    """Выпуск на production: блоки, которые остановят боевые службы."""

    def lines(self):
        return commands(path=RELEASE)

    def test_there_are_commands_at_all(self):
        self.assertGreater(len(self.lines()), 15)

    def test_no_placeholders_and_no_double_ampersand(self):
        for line in self.lines():
            self.assertNotRegex(line, r'<[^>]*>', line)
            self.assertNotIn('&&', line)
            self.assertNotIn('@', line.replace('@(', '').replace('@arguments', '')
                             .replace('@{', ''), line)      # @REVIEWED@, @PR@
            for word in ('YYYY', 'XXX', 'TODO', '...'):
                self.assertNotIn(word, line, line)
        # Метки черновика не остались и в тексте: владелец читает его целиком.
        self.assertNotRegex(text(RELEASE), r'@[A-Za-z0-9_]+@')

    def test_blocks_are_ascii_without_tabs(self):
        # Консоль сервера -- Windows PowerShell 5.1 с кодовой страницей OEM, а
        # табуляция при вставке мышью уходит в автодополнение.
        bodies = re.findall(r'```powershell\n(.*?)```', text(RELEASE), re.S)
        self.assertEqual(len(bodies), 3)
        for body in bodies:
            self.assertTrue(body.isascii(), body[:80])
            self.assertNotIn('\t', body)

    def test_git_is_limited_to_the_reviewed_commands(self):
        allowed = {'fetch', 'rev-parse', 'log', 'status', 'merge-base', 'diff',
                   'show', 'reset'}
        invoked = re.compile(r'(?:^|&\s*)git\s+(?:--no-pager\s+|-C\s+\S+\s+)*'
                             r'([a-z][a-z-]*)')
        for line in self.lines():
            for sub in invoked.findall(line.strip()):
                self.assertIn(sub, allowed, line)
            lowered = line.lower()
            for word in ('git pull', 'checkout', 'git push', 'rebase',
                         'git clean', 'stash', '--hard', 'git tag'):
                self.assertNotIn(word, lowered, line)
        # Перемотка -- одна на весь ранбук, в шаге 3 и ровно на коммит
        # выпуска; откат -- `reset --keep` на коммит production, только в откате.
        self.assertEqual(text(RELEASE).count("'merge', '--ff-only'"), 1)
        self.assertIn("Invoke-Tool 'git' @('merge', '--ff-only', $release)",
                      block('Шаг 3'))
        resets = [line.strip() for line in self.lines()
                  if re.search(r'&\s*git\s+reset\b', line)]
        self.assertEqual(len(resets), 1)
        self.assertTrue(resets[0].startswith('& git reset --keep $baseline '))
        self.assertIn(resets[0], block('Откат'))

    def test_services_stop_only_in_the_release_and_the_rollback(self):
        for line in self.lines():
            self.assertNotRegex(line, r'(?i)\bstart-service\b|nssm', line)
        stopping = {title for title, body in sections(RELEASE).items()
                    if 'Stop-Service' in body}
        self.assertEqual({title.split('.')[0].split(' —')[0]
                          for title in stopping}, {'Шаг 3', 'Откат'})
        for name in ('Шаг 3', 'Откат'):
            body = block(name)
            with self.subTest(block=name):
                stop = body.index('Stop-Service')
                self.assertLess(body.index('$stopped = $true'), stop)
                finally_at = re.search(r'^\} finally \{$', body, re.M).start()
                self.assertLess(stop, finally_at)
                after = body[finally_at:]
                self.assertIn('if ($stopped) {', after)
                self.assertIn('foreach ($name in $services) '
                              '{ Restart-Service -Name $name }', after)

    def test_the_release_follows_the_procedure_order(self):
        body = block('Шаг 3')
        marks = [
            '& git merge-base --is-ancestor $release $main',   # проверки
            'if ($open -gt 0)',
            'if ($head -ne $baseline)',
            'if ($migrations.Count -gt 0)',
            'if ($clash.Count -gt 0)',
            'Write-Host "RELEASE: $head -> $release"',
            'Write-Host "MAIN: $main',
            '$drift0 = Get-Drift',
            '$stopped = $true',
            'foreach ($name in $services) { Stop-Service',
            "tools\\check_db_lock.py', '--db', $db",
            'Invoke-Tool $backupBat',
            "'merge', '--ff-only', $release",
            '$drift1 = Get-Drift',
            '{ Restart-Service -Name $name }',
            "($site + '/login')",
            "($site + '/agro-work/')",
        ]
        positions = [body.index(mark) for mark in marks]
        self.assertEqual(positions, sorted(positions))
        # Базу шаг 3 не пишет: блокировка -- один раз, перед копией.
        self.assertEqual(body.count("tools\\check_db_lock.py', '--db', $db"), 1)
        self.assertNotRegex(body, r'migrate_[A-Za-z0-9_]+\.py')

    def test_the_check_step_changes_nothing(self):
        body = block('Шаг 2')
        for word in ('Stop-Service', 'Restart-Service', "'merge'", 'reset',
                     'Invoke-Tool $backupBat'):
            self.assertNotIn(word, body)
        self.assertNotRegex(body, r'migrate_[A-Za-z0-9_]+\.py')

    def test_the_constants_are_the_real_ones(self):
        expected = {
            'prod': "'C:\\transport-report'",
            'db': "'%s'" % LIVE_DB,
            'py': "'C:\\Program Files\\Python314\\python.exe'",
            'backupBat': "'C:\\transport-report\\backup_production_db.bat'",
            'errLog': "'C:\\transport-report\\logs\\error.log'",
            'services': SERVICES,
            'site': "'http://10.103.25.14:5050'",
            'baseline': "'%s'" % BASELINE,
            'reviewed': "'%s'" % PINNED,
            'runbookTest': "'%s'" % THIS_TEST,
            'work': "'C:\\VehicleSoft_Release'",
        }
        for name in BLOCKS:
            found = constants(block(name))
            with self.subTest(block=name):
                for key, value in found.items():
                    if key in expected:
                        self.assertEqual(value, expected[key], key)
                for key in ('baseline', 'reviewed', 'runbookTest'):
                    self.assertIn(key, found)
                self.assertRegex(found['log'],
                                 r"^'C:\\VehicleSoft_Release\\release_v133_[a-z0-9]+\.log'$")
                self.assertIn("Where-Object { ($_ -notlike 'docs/*') -and "
                              "($_ -notlike '.github/*') -and ($_ -ne "
                              "$runbookTest) }", block(name))
                if name != 'Откат':
                    # Выпуск закреплён: коммит выпуска -- проверенный, а гейт
                    # читается с вершины main, где лежат сегодняшние пункты.
                    self.assertEqual(found['release'], "'%s'" % PINNED)
                    self.assertIn('Get-OpenItems @(& git show '
                                  '"${main}:docs/RELEASE_GATE.md")', block(name))
                    self.assertIn('if ($open -gt 0) { throw', block(name))
                    self.assertNotIn('$release = [string](& git rev-parse',
                                     block(name))
                else:
                    self.assertNotIn('release', found)
        # Постоянная $runbookTest называет этот самый файл.
        self.assertTrue(os.path.isfile(os.path.join(REPO_ROOT, THIS_TEST)))
        self.assertEqual(os.path.abspath(__file__),
                         os.path.join(REPO_ROOT, *THIS_TEST.split('/')))
        block_logs = {constants(block(name))['log'].strip("'") for name in BLOCKS}
        self.assertEqual(len(block_logs), 3)
        # Журналы, которые ранбук велит прислать, -- ровно те, что пишут
        # блоки: каждый назван полным путём, чужой (прежнего выпуска) не
        # назван нигде.
        prose = re.sub(r'```powershell\n.*?```', '', text(RELEASE), flags=re.S)
        named = set(re.findall(r'release_v\d+_[a-z0-9_]+\.log', prose))
        self.assertEqual(named, {log.split('\\')[-1] for log in block_logs})
        for log in block_logs:
            self.assertIn('`%s`' % log, prose)
        with open(os.path.join(REPO_ROOT, 'backup_production_db.bat'),
                  encoding='ascii') as fh:
            self.assertIn('--source "%s"' % LIVE_DB, fh.read())
        # Блок готовился от записанного production. После выпуска строка
        # production сменится, а этот коммит останется в журнале релизов.
        self.assertIn('| v1.32 | `%s` |' % BASELINE[:7], read('docs', 'DEPLOYED.md'))
        self.assertRegex(text(RELEASE),
                         r'https://github\.com/sINte3/vehicle-soft/pull/\d+')

    def test_the_blocks_are_those_of_v131_with_the_pin_of_v132(self):
        # Блоки v1.31 прошли на этом сервере 22.10, строки закреплённого
        # выпуска v1.32 -- 30.10. Здесь -- блоки v1.31, в которых сменились
        # постоянные и ровно эти строки; иных отличий нет.
        def bare(body):
            found = constants(body)
            for key in ('baseline', 'reviewed', 'log'):
                self.assertEqual(body.count(found[key]), 1 if key != 'reviewed'
                                 or 'release' not in found else 2, key)
                body = body.replace(found[key], "'@%s@'" % key)
            return body
        v132 = commands(path=V132)
        for name in BLOCKS:
            with self.subTest(block=name):
                theirs = bare(release_block(name, path=V131))
                reviewed_line = "$reviewed  = '@reviewed@'\n"
                pin = PIN_CONSTANTS if name != 'Откат' else PIN_CONSTANTS.split('\n', 1)[1]
                self.assertEqual(theirs.count(reviewed_line), 1)
                theirs = theirs.replace(reviewed_line, reviewed_line + pin)
                for old, new in EDITS[name]:
                    self.assertEqual(theirs.count(old), 1, old[:60])
                    theirs = theirs.replace(old, new)
                    for line in new.splitlines():
                        self.assertIn(line, v132, line)
                self.assertEqual(bare(block(name)), theirs)

    def test_the_helpers_are_word_for_word_those_of_v120(self):
        # Функции блоков прошли на этом сервере в выпусках v1.20-v1.31 (те же
        # слово в слово). Копия с опечаткой проверкой текста не ловится,
        # поэтому сличается.
        theirs = {}
        for name in BLOCKS:
            theirs.update(functions(release_block(name, path=V120)))
        for name in BLOCKS:
            ours = functions(block(name))
            with self.subTest(block=name):
                self.assertTrue(ours)
                for function, body in ours.items():
                    self.assertEqual(body, theirs[function], function)
        self.assertEqual(set(functions(block('Шаг 3'))),
                         {'Get-OpenItems', 'Invoke-Tool', 'Get-Drift',
                          'Get-Backup', 'Test-Lock', 'Wait-Services',
                          'Read-NewText'})

    def test_the_pin_was_right_when_the_runbook_was_written(self):
        # Коммит выпуска -- предок последней правки ранбука, и после него до
        # этой правки менялись только docs/, CI, этот тест и общий стенд
        # блоков: на сервер из этого не едет ничего. Держится там, где оба
        # коммита есть в клоне (в CI клон глубиной 1).
        written = git('log', '-1', '--format=%H', '--',
                      'docs/GPS_RELEASE_V133_RUNBOOK.md').strip()
        if not (written and has_commit(PINNED) and has_commit(written)):
            self.skipTest('the pinned history is outside this clone')
        ancestor = subprocess.run(['git', 'merge-base', '--is-ancestor', PINNED,
                                   written], cwd=REPO_ROOT)
        self.assertEqual(ancestor.returncode, 0)
        names = git('diff', '--name-only', PINNED, written).split()
        self.assertEqual([n for n in names if not n.startswith(('docs/', '.github/'))
                          and n not in (THIS_TEST,
                                        'tests/agro_work_release_harness.ps1')], [])
        self.assertIn('docs/GPS_RELEASE_V133_RUNBOOK.md', names)

    def delta(self):
        if not (has_commit(BASELINE) and has_commit(PINNED)):
            self.skipTest('the release delta is outside this clone')
        return git('diff', '--name-only', BASELINE, PINNED).split()

    def test_the_delta_is_the_time_verdicts_and_the_1c_scripts(self):
        names = self.delta()
        self.assertEqual([n for n in names if n.startswith('migrate_')], [])
        # [REASON]: общие файлы устава правятся только с объявлением; в этом
        # выпуске их нет, и ранбук этого не объявляет.
        for shared in ('app.py', 'models.py', 'templates/base_next.html',
                       'static/css/design-system.css', 'migration_utils.py',
                       'excel_export.py'):
            self.assertNotIn(shared, names)
        # [REASON]: из кода программы меняется ровно этот список -- вердикты
        # «время» (PR #254), пояснение над списком (коммит выпуска) и скрипты
        # недельной выгрузки 1С (PR #249), которые программа не вызывает
        # (ниже). Шаг 4 смотрит вердикты; чужой код в дельте сделал бы шаг 4
        # недостаточной проверкой.
        code = sorted(n for n in names if not n.startswith(('docs/', 'tests/')))
        self.assertEqual(code, [
            '.github/workflows/checks.yml',
            'agro_work/labels.py',
            'agro_work/reconcile.py',
            'templates/agro_work/applications.html',
            'templates/agro_work/dashboard.html',
            'tools/onec_com.ps1',
            'tools/onec_export.ps1',
            'tools/onec_weekly.ps1',
            'tools/onec_weekly_task.ps1'])
        self.assertIn('tests/test_agro_work_time_method.py', names)
        self.assertIn('tests/test_agro_work_screen.py', names)
        self.assertIn('tests/test_onec_weekly.py', names)
        # Тест этого ранбука едет после коммита выпуска, а не в нём.
        self.assertNotIn(THIS_TEST, names)

    def test_the_new_files_are_the_three_the_runbook_guards(self):
        # Шаг 2 проверяет, что ни один НОВЫЙ файл выпуска не лежит уже в
        # папке программы. Новых файлов -- три, все от GPS-38; таблица
        # «что делать» объясняет остановку скриптом 1С, скопированным руками.
        if not (has_commit(BASELINE) and has_commit(PINNED)):
            self.skipTest('the release delta is outside this clone')
        added = git('diff', '--name-only', '--diff-filter=A', BASELINE,
                    PINNED).split()
        self.assertEqual(sorted(added), ['tests/test_onec_weekly.py',
                                         'tools/onec_weekly.ps1',
                                         'tools/onec_weekly_task.ps1'])
        self.assertIn('скрипт выгрузки 1С, скопированный туда раньше руками',
                      flat())

    def test_the_1c_scripts_ride_along_without_being_called(self):
        # «Что произойдёт»: скрипты 1С приедут, но программа их не вызывает.
        # Держится, пока их имена встречаются только в tools/, tests/ и CI.
        if not has_commit(PINNED):
            self.skipTest('the pinned commit is outside this clone')
        users = git('grep', '-l', '-E', r'onec_(weekly|export|com)\b', PINNED,
                    '--', '.', ':!docs').split()
        users = sorted(name.split(':', 1)[1] for name in users)
        self.assertTrue(users)
        self.assertEqual([name for name in users if not name.startswith(
            ('tools/', 'tests/', '.github/'))], [])
        self.assertIn('Скрипты недельной выгрузки 1С (GPS-38) тоже приедут в '
                      'папку программы, но программа их не вызывает', flat())

    def test_the_merged_prs_named_in_step_two_are_the_delta(self):
        # Шаг 2 говорит, какие «Merge pull request» владелец увидит. Список
        # сверяется с историей от production до коммита выпуска (PR ранбука
        # вливается позже и назван отдельно).
        if not (has_commit(BASELINE) and has_commit(PINNED)):
            self.skipTest('the release delta is outside this clone')
        merged = {int(n) for n in re.findall(
            r'^Merge pull request #(\d+)', git('log', '--merges', '--format=%s',
                                                '%s..%s' % (BASELINE, PINNED)), re.M)}
        self.assertEqual(merged, {249, 250, 251, 252, 253, 254, 255, 256})
        body = step('Шаг 2')
        named = {int(n) for n in re.findall(r'`#(\d+)`', body)}
        release_pr = int(re.search(r'/pull/(\d+)', text(RELEASE)).group(1))
        self.assertGreater(release_pr, 256)
        self.assertEqual(named, merged | {release_pr})
        self.assertIn('восемь строк влитых изменений', body)
        # Коммит выпуска -- не слияние: в списке его нет, ранбук это говорит.
        parents = git('log', '-1', '--format=%P', PINNED).split()
        self.assertEqual(len(parents), 1)
        self.assertIn('в списке не печатается: это не слияние', body)

    def test_what_the_runbook_calls_new_is_new(self):
        # Ранбук говорит: в v1.32 у заявок «время» нет вердикта, выпуск его
        # приносит вместе с пояснениями. Держится, только пока это правда для
        # production, от которого выпуск начинается, и для коммита выпуска.
        if not (has_commit(BASELINE) and has_commit(PINNED)):
            self.skipTest('the release delta is outside this clone')
        for path, line in (
                ('agro_work/reconcile.py', 'TIME_VERDICTS_PUBLISHED = True'),
                ('agro_work/labels.py',
                 "('Метод «время»: часы зажигания по GPS ещё не готовы'"),
                ('templates/agro_work/applications.html',
                 'У заявок метода «время» вместо гектаров'),
                ('templates/agro_work/dashboard.html',
                 'Сверяются заявки видов работ с методами «гектары» и «время»')):
            with self.subTest(path=path):
                self.assertNotIn(line, git('show', '%s:%s' % (BASELINE, path)))
                self.assertIn(line, git('show', '%s:%s' % (PINNED, path)))
        self.assertIn('TIME_VERDICTS_PUBLISHED = False',
                      git('show', '%s:agro_work/reconcile.py' % BASELINE))
        # Проверки, которые держат новое, -- в наборе коммита выпуска.
        screen = git('show', '%s:tests/test_agro_work_screen.py' % PINNED)
        self.assertIn('def test_the_list_explains_the_time_method(self):', screen)

    def test_the_verdicts_are_what_the_runbook_says(self):
        # «Что произойдёт» и шаг 4: пороги -- постоянные кода, а причина
        # «метод «время»» ставится только непубликуемой заявке «время».
        source = read('agro_work', 'reconcile.py')
        self.assertIn('\nTIME_VERDICTS_PUBLISHED = True\n', source)
        self.assertIn('timed = (method == methods.METHOD_TIME and self.engine_ready\n'
                      '                 and TIME_VERDICTS_PUBLISHED)', source)
        self.assertIn('        if method != methods.METHOD_GA and not timed:\n'
                      '            row[\'reason\'] = METHOD_REASON.get(method, '
                      'R_METHOD_UNMARKED)\n', source)
        self.assertEqual(source.count('R_METHOD_TIME'), 3)  # имя, причины, словарь
        from agro_work import reconcile as rc
        from gps import tolerance
        self.assertEqual(rc.TIME_WORK_SECONDS, 30 * 60)
        self.assertEqual(rc.TRUCK_WORK_KM, 3.0)
        # Допуск в часах: 15 % или 0,5 ч -- в допуске, до 25 % -- на грани
        # (без добавки в часах: у короткой заявки грани может не быть).
        for base, fact, verdict in ((10.0, 11.5, 'ok'), (10.0, 12.5, 'warn'),
                                    (10.0, 12.6, 'fail'), (2.0, 2.5, 'ok'),
                                    (2.0, 2.6, 'fail')):
            with self.subTest(base=base, fact=fact):
                self.assertEqual(tolerance.verdict_for_hours(base, fact)[0],
                                 verdict)
        for claim in (
                '«Работа была» — включено не меньше 30 минут за окно; '
                '«Работы по GPS не было» — все сутки окна известны, и включено '
                'меньше 30 минут',
                '«В допуске» при расхождении не больше 15 % или 0,5 ч, «На '
                'грани» — до 25 %, дальше — «Вне допуска»',
                'у грузовиков, работала ли машина, решает пробег окна — от 3 км',
                'Книга сверки Excel показывает те же вердикты, что экран; '
                'столбцы книги прежние'):
            with self.subTest(claim=claim[:30]):
                self.assertIn(claim, flat())

    def test_what_step_four_names_is_on_the_screen(self):
        # Подписи, которые шаг 4 велит найти, -- слово в слово подписи
        # шаблонов, меню и словарей. Адресов шаг не даёт: экраны открываются
        # из меню.
        body = step('Шаг 4')
        self.assertNotIn('http://', body)
        menu = read('templates', '_module_nav.html')
        tabs = read('templates', 'agro_work', '_tabs.html')
        page = read('templates', 'agro_work', 'applications.html')
        period = read('templates', 'agro_work', '_period.html')
        dashboard = read('templates', 'agro_work', 'dashboard.html')
        routes = read('agro_work_routes.py')
        labels = read('agro_work', 'labels.py')
        methods = read('agro_work', 'methods.py')
        where = {
            '«Сверку»': ("'ru': 'Сверка'", menu),
            '«Заявка → работа»': ("'Заявка → работа' if is_ru", tabs),
            '«Свод»': ("'Свод' if is_ru", tabs),
            '«Причина»': ("_aw_t('Сабаб', 'Причина')", routes),
            '«Вердикт»': ("_aw_t('Ҳукм', 'Вердикт')", routes),
            '«Метод «время»: часы зажигания по GPS ещё не готовы»':
                ("rc.R_METHOD_TIME: ('Метод «время»: часы зажигания по GPS ещё "
                 "не готовы'", labels),
            '«Показать»': ("'Показать' if is_ru", period),
            '«все»': ("'все' if is_ru", period),
            '«С»': ("'С' if is_ru", period),
            '«Заявок с такими условиями нет»':
                ("'Заявок с такими условиями нет' if is_ru", page),
            '«Работа была»': ("rc.V_WORK: ('Работа была'", labels),
            '«время»': ("METHOD_TIME: ('время'", methods),
            '«гектары»': ("METHOD_GA: ('гектары'", methods),
            '«Га или часы по GPS в окне»':
                ("'Га или часы по GPS в окне' if is_ru", page),
            '«Объём против GPS»': ("'Объём против GPS' if is_ru", page),
            '«В допуске»': ("rc.VOL_OK: ('В допуске'", labels),
            '«На грани»': ("rc.VOL_WARN: ('На грани'", labels),
            '«Вне допуска»': ("rc.VOL_FAIL: ('Вне допуска'", labels),
            '«в заявке … ч · по GPS … ч»': ("'в заявке %.2f ч · по GPS %.2f ч'", page),
            '«в заявке … га · по GPS … га»':
                ("'в заявке %.2f га · по GPS %.2f га'", page),
            '«У заявок метода «время» вместо гектаров — часы с включённым '
            'зажиганием по GPS…»':
                ("'У заявок метода «время» вместо гектаров — часы с включённым "
                 "зажиганием по GPS.", page),
            '«Сверяются заявки видов работ с методами «гектары» и «время» '
            '(часы зажигания по GPS)…»':
                ("'Сверяются заявки видов работ с методами «гектары» и «время» "
                 "(часы зажигания по GPS):", dashboard),
        }
        for named, (source_line, source) in where.items():
            with self.subTest(label=named[:40]):
                self.assertIn(named, body)
                self.assertIn(source_line, source)
        # Часы в столбце -- формат шаблона: 3 часа печатаются «3.00 ч».
        self.assertIn("('%.2f ч' if is_ru else '%.2f соат')|format(row.gps_on_s / 3600)",
                      page)
        self.assertEqual('%.2f ч' % 3, '3.00 ч')
        self.assertIn('`3.00 ч`', body)
        # Период по умолчанию -- две недели по вчерашний день; «С» 01.10 --
        # тридцать с небольшим суток, в пределе экрана.
        self.assertIn('DEFAULT_PERIOD_DAYS = 14', routes)
        self.assertIn('две недели по вчерашний день', body)
        limit = int(re.search(r'^MAX_PERIOD_DAYS = (\d+)', routes, re.M).group(1))
        self.assertGreaterEqual(limit, 62)
        # Фильтр «Причина» перечисляет причину «время» всегда, а не только
        # когда она встречается: 4.1 видит её в списке и после выпуска.
        self.assertIn("_choice('reason', _aw_t('Сабаб', 'Причина'), rc.FORWARD_REASONS,",
                      routes)
        from agro_work import reconcile as rc
        self.assertIn(rc.R_METHOD_TIME, rc.FORWARD_REASONS)

    def test_every_step_that_runs_a_script_first_goes_to_production(self):
        found = steps(RELEASE)
        self.assertGreaterEqual(len(found), 4)
        checked = 0
        for title, section in found.items():
            lines = commands(section)
            first = next((i for i, line in enumerate(lines)
                          if RELATIVE_SCRIPT.search(line)
                          and not re.match(r"^\$[A-Za-z]+\s*= '", line)), None)
            if first is None:
                continue
            with self.subTest(step=title):
                before = [line.strip() for line in lines[:first]]
                self.assertIn('Set-Location -LiteralPath $prod', before)
                self.assertEqual(constants(section)['prod'],
                                 "'C:\\transport-report'")
                checked += 1
        self.assertGreaterEqual(checked, 2)

    def test_every_named_script_exists_and_python_is_quoted(self):
        names = set()
        for line in self.lines():
            names.update(re.findall(r'(tools\\[A-Za-z0-9_]+\.py)', line))
            if 'python.exe' not in line.lower():
                continue
            if re.match(r"^\$py\s+= '", line):
                continue                   # постоянная блока, проверена выше
            self.assertTrue(line.startswith(PYTHON), line)
        self.assertEqual(names, {'tools\\check_migration_drift.py',
                                 'tools\\check_db_lock.py'})
        for name in names:
            path = os.path.join(REPO_ROOT, name.replace('\\', os.sep))
            self.assertTrue(os.path.isfile(path), name)

    def test_the_smoke_markers_are_real(self):
        self.assertIn('class="vs-login-form"', read('templates', 'login.html'))
        source = read('agro_work_routes.py')
        self.assertIn("url_prefix='/agro-work'", source)
        route = source.index("@agro_work_bp.route('/')\n")
        self.assertIn('@login_required', source[route:route + 120])
        screen = read('tests', 'test_agro_work_screen.py')
        self.assertIn('class AnonymousVisitor(ScreenCase):', screen)
        self.assertIn("client.get('/agro-work/', follow_redirects=True)", screen)
        body = block('Шаг 3')
        self.assertIn("-notmatch 'vs-login-form'", body)
        self.assertIn("($agroBody -notmatch 'vs-login-form')", body)

    def test_every_stop_in_the_table_is_one_a_block_prints(self):
        table = [line for line in text(RELEASE).splitlines()
                 if line.startswith('| `RESULT: STOP - ')]
        self.assertGreaterEqual(len(table), 12)
        blocks = ''.join(block(name) for name in BLOCKS)
        for line in table:
            shown = re.match(r'\| `RESULT: STOP - ([^`]*)`', line).group(1)
            words = shown.split('...')[0].strip()
            with self.subTest(stop=words):
                self.assertTrue(words)
                self.assertIn(words, blocks)
        # Остановки закреплённого выпуска в таблице есть.
        shown = ' '.join(table)
        for words in ('the release commit is not in main',
                      'the release commit does not contain the checked version',
                      'between the checked version and the release commit',
                      'files of the release already lie on the server'):
            with self.subTest(pinned=words):
                self.assertIn(words, shown)

    def test_the_registry_line_is_the_one_production_has(self):
        # После выпуска v1.32 реестр -- 65 (docs/DEPLOYED.md); в этом выпуске
        # миграций нет, и шаг 3 печатает реестр до и после одним числом.
        self.assertIn('реестр миграций 61 → %d' % REGISTERED,
                      read('docs', 'DEPLOYED.md'))
        self.assertIn('`MIGRATIONS REGISTERED: %d -> %d, unchanged`'
                      % (REGISTERED, REGISTERED), text(RELEASE))
        self.assertIn('"MIGRATIONS REGISTERED: $($drift0.Registered) -> '
                      '$($drift1.Registered), unchanged"', block('Шаг 3'))

    def test_the_hashes_in_the_prose_are_the_pinned_ones(self):
        # Короткие хеши, которые ранбук велит увидеть, -- начала постоянных.
        prose = re.sub(r'```powershell\n.*?```', '', text(RELEASE), flags=re.S)
        shown = set(re.findall(r'`([0-9a-f]{7})(?:\.\.\.)?`', prose))
        self.assertEqual(shown, {BASELINE[:7], PINNED[:7]})
        self.assertIn('`RELEASE: %s... -> %s...`' % (BASELINE[:7], PINNED[:7]),
                      prose)

    def test_no_secret_and_no_person_names(self):
        body = text(RELEASE)
        self.assertNotRegex(body, r'password=(?!`)\S')
        self.assertNotIn('wialon_token', body)
        self.assertNotIn('agro_work_credentials', body)
        self.assertNotIn('onec_credentials', body)


class ReleaseV133ToolFormats(unittest.TestCase):
    """Разбор вывода в блоке шага 3 -- по выводу НАСТОЯЩИХ инструментов."""

    @classmethod
    def setUpClass(cls):
        cls.out = real_outputs()

    def assertPatternInBlock(self, pattern):
        self.assertIn("'%s'" % pattern, block('Шаг 3'))

    def test_backup_lines(self):
        for pattern in BACKUP_PATTERNS.values():
            self.assertPatternInBlock(pattern)
        self.assertTrue(parse_backup(self.out['backup_ok']['lines'])['ok'])
        self.assertFalse(parse_backup(self.out['backup_fail']['lines'])['ok'])
        self.assertFalse(parse_backup(self.out['backup_mismatch']['lines'])['ok'])

    def test_drift_lines(self):
        for pattern in DRIFT_PATTERNS.values():
            self.assertPatternInBlock(pattern)
        # До выпуска и после перемотки реестр один и тот же: всё применено.
        state = parse_drift(self.out['drift_migrated']['lines'])
        self.assertGreater(state['registered'], 0)
        self.assertEqual(state['pending'], [])
        extra = parse_drift(self.out['drift_extra_migrated']['lines'])
        self.assertEqual(len(extra['pending']), 1)          # чужая, не наша

    def test_lock_and_gate_lines(self):
        self.assertIn("'no process holds the database'", block('Шаг 3'))
        lines = read('docs', 'RELEASE_GATE.md').splitlines()
        self.assertGreaterEqual(open_items(lines), 0)
        self.assertEqual(open_items(gate_lines(2)), 2)
        for name in ('Шаг 2', 'Шаг 3'):
            self.assertIn("-eq '%s'" % GATE_SEPARATOR, block(name))


POWERSHELL = os.environ.get('GPS_RELEASE_POWERSHELL')
RELEASE_HASH = '5e1ea5e3' + 'c0ffee' * 5 + 'ab'
REVIEWED_HASH = '7e71e3e3' + 'bead00' * 5 + 'cd'
MAIN_HASH = '3a1d0003' + 'f00d00' * 5 + 'ef'
TEST_SERVICES = ['V133TestReport', 'V133TestBot', 'V133TestBot003']
LOGIN_BODY = '<form method="post" class="vs-login-form">'


@unittest.skipUnless(POWERSHELL, 'GPS_RELEASE_POWERSHELL is not set')
class ReleaseV133BlocksInPowerShell(unittest.TestCase):
    """Блоки ранбука исполняются против подставного сервера.

    Путь каждой остановки: что тронуто и что нет, подняты ли службы, какую
    строку RESULT увидит владелец. Службы, пути, сайт и питон подменяются до
    запуска (prepare), поэтому стенд не дотянется до настоящих. Коммит
    выпуска, проверенный коммит и вершина main в стенде -- три разных хеша:
    так видно, какая проверка какой из них читает.
    """

    @classmethod
    def setUpClass(cls):
        cls.out = real_outputs()

    def setUp(self):
        self.folder = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.folder, True)

    def outputs(self, before='drift_migrated', after='drift_migrated'):
        """Вывод инструментов: реестр до перемотки и после неё."""
        out = dict(self.out)
        out['drift_before'] = self.out[before]
        out['drift_merged'] = self.out[after]
        return out

    def scenario(self, **changes):
        value = {
            'Host': 'SRV-YOQSH', 'Admin': True, 'Head': BASELINE,
            'Release': RELEASE_HASH, 'Reviewed': REVIEWED_HASH,
            'Main': MAIN_HASH,
            'Ancestry': [[BASELINE, RELEASE_HASH], [REVIEWED_HASH, RELEASE_HASH],
                         [BASELINE, REVIEWED_HASH], [RELEASE_HASH, MAIN_HASH]],
            'ChangedAfterReviewed': ['docs/RELEASE_GATE.md'],
            'GateLines': gate_lines(0),
            'FetchCode': 0, 'MergeCode': 0, 'ResetCode': 0,
            'DiffNames': ['agro_work/reconcile.py', 'tools/onec_weekly.ps1',
                          'docs/GPS_RELEASE_V133_RUNBOOK.md'],
            'Modified': [], 'Services': TEST_SERVICES, 'StopFails': [],
            'FreeBytes': 50 * 1024 ** 3, 'LockKind': 'clean',
            'BackupKind': 'ok', 'MigrateFirst': 'done',
            'MigrateSecond': 'again',
            'Web': {'/login': {'Status': 200, 'Body': LOGIN_BODY},
                    '/agro-work/': {'Status': 200, 'Body': LOGIN_BODY}},
            'ErrorLogAfterStart': None,
            'Outputs': self.outputs(),
        }
        value.update(changes)
        return value

    def run_block(self, name, scenario):
        folder = tempfile.mkdtemp(dir=self.folder)
        work = os.path.join(folder, 'work')
        prod = os.path.join(folder, 'prod')
        os.makedirs(os.path.join(prod, 'instance'))
        db = os.path.join(prod, 'instance', 'transport.db')
        errlog = os.path.join(prod, 'error.log')
        with open(db, 'w', encoding='ascii') as fh:
            fh.write('x' * 64)
        with open(errlog, 'w', encoding='ascii') as fh:
            fh.write('INFO:waitress:Serving on http://0.0.0.0:5050\n')
        scenario['ErrorLog'] = errlog
        log = os.path.join(work, 'release.log')
        body = prepare(block(name), {
            'prod': "'%s'" % prod, 'db': "'%s'" % db,
            'py': "'Invoke-FakePython'", 'backupBat': "'Invoke-FakeBackup'",
            'errLog': "'%s'" % errlog,
            'services': '@(%s)' % ', '.join("'%s'" % s for s in TEST_SERVICES),
            'site': "'http://v133-test.invalid'", 'work': "'%s'" % work,
            'log': "'%s'" % log, 'admin': '$global:scenario.Admin',
            'reviewed': "'%s'" % REVIEWED_HASH, 'release': "'%s'" % RELEASE_HASH,
        })
        self.assertNotIn(PINNED, body)
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
        self.assertInOrder(calls, 'git fetch',
                           'git merge-base --is-ancestor %s %s' % (RELEASE_HASH,
                                                                   MAIN_HASH),
                           'git show %s:docs/RELEASE_GATE.md' % MAIN_HASH,
                           'git diff --name-only --diff-filter=A %s %s'
                           % (BASELINE, RELEASE_HASH),
                           'git log --format=%%h %s..%s' % (RELEASE_HASH, MAIN_HASH),
                           'python tools\\check_migration_drift',
                           'Stop-Service', 'python tools\\check_db_lock', 'backup',
                           'git merge --ff-only ' + RELEASE_HASH,
                           'Restart-Service', 'GET /login', 'GET /agro-work/')
        # Перемотка -- на коммит выпуска, не на вершину main.
        self.assertFalse([c for c in calls if c.startswith('git merge ')
                          and MAIN_HASH in c])
        self.assertFalse([c for c in calls if c.startswith('python migrate')])
        self.assertEqual(len([c for c in calls if 'check_migration_drift' in c]), 2)
        self.assertEqual(len([c for c in calls if 'check_db_lock' in c]), 1)
        self.assertBackUp(calls)
        self.assertIn('RELEASE: %s -> %s' % (BASELINE, RELEASE_HASH), output)
        self.assertIn('MAIN: %s - 3 later commit(s) of main are not taken'
                      % MAIN_HASH, output)
        self.assertRegex(output, r'MIGRATIONS REGISTERED: (\d+) -> \1, unchanged')
        self.assertIn('BACKUP: ', output)
        self.assertIn('SMOKE /agro-work/: asks to log in - the section is there',
                      output)
        self.assertIn('NEW TRACEBACKS IN logs\\error.log: 0', output)
        self.assertIn('PROGRAM VERSION NOW: %s' % RELEASE_HASH[:7], output)
        self.assertIn('RESULT: RELEASE PASSED', transcript)

    def test_ci_and_this_test_after_the_checked_version_do_not_stop_it(self):
        # Между проверенным коммитом и коммитом выпуска допускаются docs/,
        # .github/ и тест ранбука -- как у выпуска v1.32.
        result, _calls, output, _ = self.run_block('Шаг 3', self.scenario(
            ChangedAfterReviewed=['docs/a.md', '.github/workflows/checks.yml',
                                  THIS_TEST]))
        self.assertEqual(result, 'RESULT: RELEASE PASSED', output)

    def test_a_stale_wal_is_the_known_noise_not_a_stop(self):
        result, _calls, output, _ = self.run_block(
            'Шаг 3', self.scenario(LockKind='stale'))
        self.assertEqual(result, 'RESULT: RELEASE PASSED', output)

    def test_another_track_pending_migration_is_left_alone(self):
        # На проде две миграции Дронов без строки в реестре (с выпуска v1.20):
        # список до перемотки и после один и тот же -- выпуск проходит.
        out = self.outputs('drift_extra_migrated', 'drift_extra_migrated')
        result, _calls, output, _ = self.run_block('Шаг 3',
                                                   self.scenario(Outputs=out))
        self.assertEqual(result, 'RESULT: RELEASE PASSED', output)

    def test_checks_that_fail_change_nothing(self):
        pin = [[BASELINE, RELEASE_HASH], [REVIEWED_HASH, RELEASE_HASH],
               [BASELINE, REVIEWED_HASH]]
        cases = {
            'not run as administrator': dict(Admin=False),
            'not SRV-YOQSH': dict(Host='SOME-PC'),
            'git fetch failed': dict(FetchCode=128),
            'the release commit is not in main: %s, main %s'
            % (RELEASE_HASH, MAIN_HASH): dict(Ancestry=pin),
            'the release commit does not contain the checked version': dict(
                Ancestry=[[BASELINE, RELEASE_HASH], [RELEASE_HASH, MAIN_HASH]]),
            'between the checked version and the release commit there are '
            'changes outside docs/: tests/other.py': dict(
                ChangedAfterReviewed=['docs/a.md', 'tests/other.py']),
            'the release gate is closed: 1 open item': dict(GateLines=gate_lines(1)),
            'could not be read': dict(GateLines=[]),
            'already on the server': dict(Head=RELEASE_HASH),
            'this release was prepared for': dict(Head='0' * 40),
            'edited on this server': dict(Modified=[' M app.py']),
            'does not continue': dict(Ancestry=[[REVIEWED_HASH, RELEASE_HASH],
                                                [RELEASE_HASH, MAIN_HASH]]),
            'migrations in the release: migrate_fin_recon_storage_001.py; this '
            'release has none': dict(DiffNames=['agro_work/reconcile.py',
                                                'migrate_fin_recon_storage_001.py']),
            # error.log лежит в папке программы стенда: новый файл выпуска с
            # тем же именем перемотка не смогла бы положить.
            'files of the release already lie on the server: error.log': dict(
                DiffNames=['agro_work/reconcile.py', 'error.log']),
        }
        for message, change in cases.items():
            with self.subTest(case=message[:40]):
                result, calls, output, _ = self.run_block(
                    'Шаг 3', self.scenario(**change))
                self.assertTrue(result.startswith('RESULT: STOP - '), output)
                self.assertIn(message, result)
                self.assertIn('Nothing was changed', output)
                self.assertUntouched(calls)

    def test_a_stop_before_the_update_brings_the_old_version_back(self):
        cases = {
            'did not stop within 90 seconds': dict(StopFails=['V133TestBot']),
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
                self.assertIn('PROGRAM VERSION NOW: %s' % BASELINE[:7], output)
        # Копия, которой нет, -- до перемотки: код не сдвинулся.
        result, calls, _, _ = self.run_block('Шаг 3',
                                             self.scenario(BackupKind='fail'))
        self.assertFalse([c for c in calls if c.startswith('git merge ')])

    def test_a_changed_registry_after_the_update_is_a_stop(self):
        out = self.outputs('drift_migrated', 'drift_merged')
        result, calls, output, _ = self.run_block('Шаг 3',
                                                  self.scenario(Outputs=out))
        self.assertIn('after the update the migrations are', result)
        self.assertBackUp(calls)
        self.assertIn('PROGRAM VERSION NOW: %s' % RELEASE_HASH[:7], output)
        self.assertNotIn('GET /login', calls)

    def test_a_dead_site_after_the_start_is_a_stop(self):
        web = {'/login': {'Status': 503, 'Body': ''},
               '/agro-work/': {'Status': 503, 'Body': ''}}
        result, calls, _output, _ = self.run_block('Шаг 3', self.scenario(Web=web))
        self.assertIn('the login page did not open', result)
        self.assertBackUp(calls)
        self.assertNotIn('GET /agro-work/', calls)

    def test_a_missing_section_after_the_start_is_a_stop(self):
        web = {'/login': {'Status': 200, 'Body': LOGIN_BODY},
               '/agro-work/': {'Status': 404, 'Body': 'Not Found'}}
        result, calls, output, _ = self.run_block('Шаг 3', self.scenario(Web=web))
        self.assertIn('/agro-work/ answered', result)
        self.assertBackUp(calls)
        self.assertIn('SMOKE /login: 200', output)
        self.assertNotIn('SMOKE /agro-work/', output)
        self.assertInOrder(calls, 'Restart-Service', 'GET /login',
                           'GET /agro-work/')
        self.assertIn('PROGRAM VERSION NOW: %s' % RELEASE_HASH[:7], output)

    def test_new_tracebacks_after_the_start_are_a_warning(self):
        result, _calls, output, _ = self.run_block('Шаг 3', self.scenario(
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
        self.assertIn('git show %s:docs/RELEASE_GATE.md' % MAIN_HASH, calls)
        self.assertIn('UNTRACKED FILES: 2, none of them is a file of the release',
                      output)
        self.assertIn('RELEASE: %s -> %s' % (BASELINE, RELEASE_HASH), output)
        self.assertIn('MAIN: %s - 3 later commit(s) of main are not taken'
                      % MAIN_HASH, output)
        self.assertIn('Merge pull request', output)
        self.assertIn('RESULT: CHECK PASSED', transcript)

    def test_the_check_stops_on_what_the_release_would_stop_on(self):
        cases = {
            'the release gate is closed': dict(GateLines=gate_lines(1)),
            'the release commit is not in main': dict(Ancestry=[
                [BASELINE, RELEASE_HASH], [REVIEWED_HASH, RELEASE_HASH]]),
            'does not contain the checked version': dict(Ancestry=[
                [BASELINE, RELEASE_HASH], [RELEASE_HASH, MAIN_HASH]]),
            'changes outside docs/': dict(ChangedAfterReviewed=['app.py']),
            'already on the server': dict(Head=RELEASE_HASH),
            'this release has none': dict(DiffNames=['migrate_x_001.py']),
            'files of the release already lie on the server': dict(
                DiffNames=['error.log']),
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

    def test_the_rollback_takes_ci_and_this_test_as_not_code(self):
        result, _calls, output, _ = self.run_block('Откат', self.scenario(
            Head=RELEASE_HASH, ChangedAfterReviewed=[
                '.github/workflows/checks.yml', THIS_TEST]))
        self.assertEqual(result, 'RESULT: ROLLBACK PASSED', output)

    def test_the_rollback_refuses_what_it_did_not_deploy(self):
        cases = {
            'nothing to roll back': dict(Head=BASELINE),
            'not at this release': dict(Head='a' * 40),
            'past this release': dict(Head=RELEASE_HASH,
                                      ChangedAfterReviewed=['agro_work/reconcile.py']),
        }
        for message, change in cases.items():
            with self.subTest(case=message):
                result, calls, _output, _ = self.run_block(
                    'Откат', self.scenario(**change))
                self.assertIn(message, result)
                self.assertUntouched(calls)


if __name__ == '__main__':
    unittest.main()
