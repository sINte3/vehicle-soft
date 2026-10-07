# -*- coding: utf-8 -*-
"""Выпуск v1.22 на production: docs/GPS_RELEASE_RUNBOOK.md.

Блоки этого ранбука владелец вставляет в Windows PowerShell 5.1 на боевом
сервере, и шаг 3 останавливает три службы. Они -- блоки выпуска v1.20
(docs/AGRO_WORK_RELEASE_RUNBOOK.md, прошли на этом сервере 01.10.2026) без
миграции, с постоянными этого выпуска; v1.21 прошёл с ними 02.10. Шаг 3 v1.22
вдобавок сверяет метод GPS на диске после перемотки, а шаг 4 -- пересчёт
прошедших суток `tools/gps_recompute_days.py`. Здесь держится:

  * текст: нет плейсхолдеров и `&&`, ASCII без табуляций, git -- только
    проверенные команды, службы останавливаются только в выпуске и откате,
    порядок процедуры, проверка ничего не меняет, постоянные настоящие,
    закрепление верно, в дельте нет миграций;
  * вспомогательные функции блоков ДОСЛОВНО те же, что в выпуске v1.20: они
    там прошли на сервере, и копия с опечаткой проверкой текста не ловится;
  * строки, которые ранбук велит ждать от `tools/gps_recompute_days.py`, --
    то, что инструмент действительно печатает; метод, который шаг 3 ждёт на
    диске, -- тот, что в `gps/area.py`;
  * сами блоки исполняются против подставных git, служб, питона и сайта
    (tests/agro_work_release_harness.ps1 -- тот же стенд, что у v1.20) во всех
    путях «стоп»: на PowerShell 7 и -- в CI -- на Windows PowerShell 5.1.

Запуск: python -m unittest tests.test_gps_release_runbook -v
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

RELEASE = os.path.join(REPO_ROOT, 'docs', 'GPS_RELEASE_RUNBOOK.md')
# [REASON]: то же правило, что у выпуска v1.20, но дефис перед `tools\` не в
# счёт: путь `C:\gps-tools\check\...` -- папка, а не запуск скрипта по
# относительному пути. В ранбуке v1.20 таких путей не было.
RELATIVE_SCRIPT = re.compile(r'(?<![\\A-Za-z:-])(tools\\|migrate_[A-Za-z0-9_]+\.py'
                             r'|run_server\.py|-m unittest)')
# Production на момент подготовки: строка production в docs/DEPLOYED.md.
BASELINE = '8df568394a840054ef6f842c6a8b272ca4c31aa8'
# [REASON]: окружение расчёта GPS -- отдельный venv без пробела в пути; им
# запускается всё, что тянет numpy/shapely, в том числе пересчёт шага 4.
GEO_PYTHON = '& C:\\gps_venv\\Scripts\\python.exe'


def method_in_code():
    """METHOD_VERSION из gps/area.py -- текстом, без numpy в этом окружении."""
    with open(os.path.join(REPO_ROOT, 'gps', 'area.py'), encoding='utf-8') as fh:
        found = re.findall(r'^METHOD_VERSION = "([^"]+)"$', fh.read(), re.M)
    assert len(found) == 1, found
    return found[0]
SERVICES = "@('TransportReport', 'TransportBot', 'TransportBot003')"


def release_block(prefix, path=RELEASE):
    """Большой блок `& { ... }` раздела, чей заголовок начинается с prefix."""
    found = [body for title, body in sections(path).items()
             if title.startswith(prefix)]
    assert len(found) == 1, (prefix, len(found))
    blocks = [b for b in re.findall(r'```powershell\n(.*?)```', found[0], re.S)
              if b.startswith('& {')]
    assert len(blocks) == 1, (prefix, len(blocks))
    return blocks[0]


def functions(block):
    """{имя: текст} функций блока -- от `function X(` до `}` с начала строки."""
    return {m.group(1): m.group(0) for m in re.finditer(
        r'^function ([A-Za-z-]+)\(.*?^\}$', block, re.M | re.S)}


class GpsReleaseRunbook(unittest.TestCase):
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
        invoked = re.compile(r'(?:^|&\s*)git\s+(?:--no-pager\s+|-C\s+\S+\s+)*'
                             r'([a-z][a-z-]*)')
        for line in self.lines():
            for sub in invoked.findall(line.strip()):
                self.assertIn(sub, allowed, line)
            lowered = line.lower()
            for word in ('git pull', 'checkout', 'git push', 'rebase',
                         'git clean', 'stash', '--hard', 'git tag'):
                self.assertNotIn(word, lowered, line)
        # Перемотка -- одна на весь ранбук, в шаге 3 и ровно на проверенный
        # коммит; откат -- `reset --keep` на коммит production, только в откате.
        self.assertEqual(text(RELEASE).count("'merge', '--ff-only'"), 1)
        self.assertIn("Invoke-Tool 'git' @('merge', '--ff-only', $release)",
                      release_block('Шаг 3'))
        resets = [line.strip() for line in self.lines()
                  if re.search(r'&\s*git\s+reset\b', line)]
        self.assertEqual(len(resets), 1)
        self.assertTrue(resets[0].startswith('& git reset --keep $baseline '))
        self.assertIn(resets[0], release_block('Откат'))

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
            'if ($migrations.Count -gt 0)',
            '$drift0 = Get-Drift',
            '$stopped = $true',
            'foreach ($name in $services) { Stop-Service',
            "tools\\check_db_lock.py', '--db', $db",
            'Invoke-Tool $backupBat',
            "'merge', '--ff-only', $release",
            '$drift1 = Get-Drift',
            "Join-Path $prod 'gps\\area.py'",
            '{ Restart-Service -Name $name }',
            "($site + '/login')",
            "($site + '/wialon/mapping')",
            "($site + '/drones/fields')",
        ]
        positions = [body.index(mark) for mark in marks]
        self.assertEqual(positions, sorted(positions))
        # Базу этот выпуск не пишет: блокировка -- один раз, перед копией.
        self.assertEqual(body.count("tools\\check_db_lock.py', '--db', $db"), 1)
        self.assertNotRegex(body, r'migrate_[A-Za-z0-9_]+\.py')

    def test_the_check_step_changes_nothing(self):
        body = release_block('Шаг 2')
        for word in ('Stop-Service', 'Restart-Service', "'merge'", 'reset',
                     'Invoke-Tool $backupBat'):
            self.assertNotIn(word, body)
        self.assertNotRegex(body, r'migrate_[A-Za-z0-9_]+\.py')

    def test_the_constants_are_the_real_ones(self):
        expected = {
            'prod': "'C:\\transport-report'",
            'db': "'C:\\transport-report\\instance\\transport.db'",
            'py': "'C:\\Program Files\\Python314\\python.exe'",
            'backupBat': "'C:\\transport-report\\backup_production_db.bat'",
            'errLog': "'C:\\transport-report\\logs\\error.log'",
            'services': SERVICES,
            'site': "'http://10.103.25.14:5050'",
            'baseline': "'%s'" % BASELINE,
            'work': "'C:\\VehicleSoft_Release'",
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
                self.assertRegex(found['log'],
                                 r"^'C:\\VehicleSoft_Release\\release_v122_[a-z0-9]+\.log'$")
                reviewed.add(found['reviewed'])
                self.assertIn("Where-Object { $_ -notlike 'docs/*' }",
                              release_block(name))
                if name != 'Откат':
                    self.assertIn('Get-OpenItems @(& git show '
                                  '"${release}:docs/RELEASE_GATE.md")',
                                  release_block(name))
                    self.assertIn('if ($open -gt 0) { throw',
                                  release_block(name))
        self.assertEqual(len(reviewed), 1)
        # Метод, который шаг 3 ждёт на диске после перемотки, -- действующий.
        self.assertEqual(constants(release_block('Шаг 3'))['method'],
                         "'%s'" % method_in_code())
        self.assertEqual(method_in_code(), 'overflow-cap-2026-10-07')
        logs = [constants(release_block(name))['log']
                for name in ('Шаг 2', 'Шаг 3', 'Откат')]
        self.assertEqual(len(set(logs)), 3)
        with open(os.path.join(REPO_ROOT, 'backup_production_db.bat'),
                  encoding='ascii') as fh:
            self.assertIn('--source "C:\\transport-report\\instance\\transport.db"',
                          fh.read())
        with open(os.path.join(REPO_ROOT, 'docs', 'DEPLOYED.md'),
                  encoding='utf-8') as fh:
            deployed = fh.read()
        # Блок готовился от записанного production. После выпуска строка
        # production сменится, а этот коммит останется в журнале релизов.
        self.assertIn('`%s`' % BASELINE[:7], deployed)

    def test_the_helpers_are_word_for_word_those_of_v120(self):
        # Функции блоков прошли на этом сервере 01.10 в выпуске v1.20.
        # Копия с опечаткой проверкой текста не ловится, поэтому сличается.
        theirs = {}
        for name in ('Шаг 2', 'Шаг 3', 'Откат'):
            theirs.update(functions(release_block(name, path=V120)))
        for name in ('Шаг 2', 'Шаг 3', 'Откат'):
            ours = functions(release_block(name))
            with self.subTest(block=name):
                self.assertTrue(ours)
                for function, body in ours.items():
                    self.assertEqual(body, theirs[function], function)
        self.assertEqual(set(functions(release_block('Шаг 3'))),
                         {'Get-OpenItems', 'Invoke-Tool', 'Get-Drift',
                          'Get-Backup', 'Test-Lock', 'Wait-Services',
                          'Read-NewText'})

    def test_the_pin_was_right_when_the_runbook_was_written(self):
        # Всё, что изменилось после проверенного коммита ДО последней правки
        # ранбука, -- только docs/. Код, влитый позже, ловят шаги 2 и 3.
        # Держится там, где оба коммита есть в клоне (в CI клон глубиной 1).
        commit = constants(release_block('Шаг 3'))['reviewed'].strip("'")
        written = git('log', '-1', '--format=%H', '--',
                      'docs/GPS_RELEASE_RUNBOOK.md').strip()
        if not (written and has_commit(commit) and has_commit(written)):
            self.skipTest('the pinned history is outside this clone')
        ancestor = subprocess.run(['git', 'merge-base', '--is-ancestor', commit,
                                   written], cwd=REPO_ROOT)
        self.assertEqual(ancestor.returncode, 0)
        names = git('diff', '--name-only', commit, written).split()
        self.assertEqual([n for n in names if not n.startswith('docs/')], [])

    def test_the_delta_carries_no_migration(self):
        commit = constants(release_block('Шаг 3'))['reviewed'].strip("'")
        if not (has_commit(BASELINE) and has_commit(commit)):
            self.skipTest('the release delta is outside this clone')
        names = git('diff', '--name-only', BASELINE, commit).split()
        self.assertEqual([n for n in names if n.startswith('migrate_')], [])
        self.assertIn('gps/area.py', names)
        self.assertIn('tools/gps_recompute_days.py', names)

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
                if 'Set-Location -LiteralPath $prod' in before:
                    self.assertEqual(constants(section)['prod'],
                                     "'C:\\transport-report'")
                else:
                    self.assertIn('cd C:\\transport-report', before)
                checked += 1
        self.assertGreaterEqual(checked, 3)

    def test_every_named_script_exists_and_python_is_quoted(self):
        names = set()
        for line in self.lines():
            names.update(re.findall(r'(tools\\[A-Za-z0-9_]+\.py)', line))
            if 'python.exe' not in line.lower():
                continue
            if re.match(r"^\$py\s+= '", line):
                continue                   # постоянная блока, проверена выше
            self.assertTrue(line.startswith(PYTHON)
                            or line.startswith(GEO_PYTHON), line)
        self.assertEqual(names, {'tools\\check_migration_drift.py',
                                 'tools\\check_db_lock.py',
                                 'tools\\gps_recompute_days.py'})
        for name in names:
            path = os.path.join(REPO_ROOT, name.replace('\\', os.sep))
            self.assertTrue(os.path.isfile(path), name)

    def test_the_smoke_markers_are_real(self):
        with open(os.path.join(REPO_ROOT, 'templates', 'login.html'),
                  encoding='utf-8') as fh:
            self.assertIn('class="vs-login-form"', fh.read())
        with open(os.path.join(REPO_ROOT, 'wialon_import.py'),
                  encoding='utf-8') as fh:
            source = fh.read()
        route = source.index("@app.route('/wialon/mapping')")
        # Аноним получает страницу входа -- её и ждёт проверка после пуска.
        self.assertIn('@admin_required', source[route:route + 200])
        self.assertIn("-notmatch 'vs-login-form'", release_block('Шаг 3'))
        # /drones/fields (PR #160): тот же ответ анониму -- module_required
        # обёрнут в login_required. На прежней версии адреса нет вовсе (404),
        # поэтому эта проверка ещё и отличает новую версию от старой.
        with open(os.path.join(REPO_ROOT, 'drones.py'), encoding='utf-8') as fh:
            source = fh.read()
        self.assertIn("Blueprint('drones', __name__, url_prefix='/drones')", source)
        route = source.index("@drones_bp.route('/fields')\n")
        self.assertIn("@module_required('drones')", source[route:route + 80])
        with open(os.path.join(REPO_ROOT, 'models.py'), encoding='utf-8') as fh:
            source = fh.read()
        guard = source[source.index('def module_required('):]
        self.assertIn('@login_required', guard[:guard.index('return decorator')])
        block = release_block('Шаг 3')
        self.assertIn("($fieldsBody -notmatch 'vs-login-form')", block)

    def test_the_lines_the_owner_waits_for_are_what_the_tool_prints(self):
        with open(os.path.join(REPO_ROOT, 'tools', 'gps_recompute_days.py'),
                  encoding='utf-8') as fh:
            tool = fh.read()
        for printed in ("'method of this code: %s (previous: %s)'",
                        "'operator answers (work/passage) in the window: %d'",
                        "'PLAN ONLY: nothing was written. Add --apply to recompute.'",
                        "'  %s: %.2f ha -> %.2f ha (%+.2f); published machine-days %d -> %d'",
                        "'machine-days with more hectares than before: %d (the rule never adds '",
                        "'rows of counted objects still not on %s: %d'",
                        "'RESULT: RECOMPUTED %d day(s) by %s; rows of counted objects left on '",
                        "'another method: %d'",
                        "'== %s (%d of %d)'",
                        "'days with points in %s: %d of %d'",
                        "'rows of counted objects already on %s before this run: %d -- this '"):
            with self.subTest(line=printed):
                self.assertIn(printed, tool)
        step = next(body for title, body in steps(RELEASE).items()
                    if title.startswith('Шаг 4'))
        for waited in ('`method of this code: overflow-cap-2026-10-07 (previous:\n'
                       '  adaptive-alpha-2026-08-12)`',
                       '`operator answers (work/passage) in the window: 0`',
                       '`PLAN ONLY: nothing was written. Add --apply to recompute.`',
                       '`2026-09: 7336.63 ha -> 6201.30 ha (-1135.33); published '
                       'machine-days 3346\n  -> 3346`',
                       '`machine-days with more hectares than before: 0`',
                       '`rows of counted objects still not on overflow-cap-2026-10-07: 0`',
                       '`RESULT: RECOMPUTED ... day(s) by overflow-cap-2026-10-07; rows\n'
                       '  of counted objects left on another method: 0`',
                       '`exit 0`',
                       '`days with points in C:\\transport-report\\instance:`'):
            with self.subTest(waited=waited):
                self.assertIn(waited, step)
        # Пишущая команда -- одна, с --apply, и только после плана и копии.
        lines = commands(step)
        writes = [i for i, line in enumerate(lines) if '--apply' in line]
        self.assertEqual(len(writes), 1)
        plan = [i for i, line in enumerate(lines)
                if 'gps_recompute_days.py' in line and '--apply' not in line]
        backup = [i for i, line in enumerate(lines)
                  if line.strip() == '& C:\\transport-report\\backup_production_db.bat']
        self.assertEqual(len(plan), 1)
        self.assertEqual(len(backup), 1)
        self.assertLess(plan[0], backup[0])
        self.assertLess(backup[0], writes[0])
        self.assertTrue(lines[writes[0]].startswith(GEO_PYTHON + ' -u '))
        # Повтор 4.3 дописывает журнал: сводка первого прогона не затирается.
        self.assertIn(' --apply *>> C:\\VehicleSoft_Release\\'
                      'release_v122_step4_recompute.log', lines[writes[0]])


class GpsReleaseToolFormats(unittest.TestCase):
    """Разбор вывода в блоке шага 3 -- по выводу НАСТОЯЩИХ инструментов."""

    @classmethod
    def setUpClass(cls):
        cls.out = real_outputs()

    def assertPatternInBlock(self, pattern):
        self.assertIn("'%s'" % pattern, release_block('Шаг 3'))

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
        self.assertIn("'no process holds the database'", release_block('Шаг 3'))
        with open(os.path.join(REPO_ROOT, 'docs', 'RELEASE_GATE.md'),
                  encoding='utf-8') as fh:
            lines = fh.read().splitlines()
        self.assertGreaterEqual(open_items(lines), 0)
        self.assertEqual(open_items(gate_lines(2)), 2)
        for name in ('Шаг 2', 'Шаг 3'):
            self.assertIn("-eq '%s'" % GATE_SEPARATOR, release_block(name))


POWERSHELL = os.environ.get('GPS_RELEASE_POWERSHELL')
RELEASE_HASH = '5e1ea5e0' + 'c0ffee' * 5 + 'ab'
REVIEWED_HASH = '7e71e3ed' + 'bead00' * 5 + 'cd'
TEST_SERVICES = ['GpsTestReport', 'GpsTestBot', 'GpsTestBot003']
LOGIN_BODY = '<form method="post" class="vs-login-form">'


@unittest.skipUnless(POWERSHELL, 'GPS_RELEASE_POWERSHELL is not set')
class GpsReleaseBlocksInPowerShell(unittest.TestCase):
    """Блоки ранбука исполняются против подставного сервера.

    Путь каждой остановки: что тронуто и что нет, подняты ли службы, какую
    строку RESULT увидит владелец. Службы, пути, сайт и питон подменяются до
    запуска (prepare), поэтому стенд не дотянется до настоящих.
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
            'Ancestry': [[BASELINE, RELEASE_HASH], [REVIEWED_HASH, RELEASE_HASH],
                         [BASELINE, REVIEWED_HASH]],
            'ChangedAfterReviewed': ['docs/RELEASE_GATE.md'],
            'GateLines': gate_lines(0),
            'FetchCode': 0, 'MergeCode': 0, 'ResetCode': 0,
            'DiffNames': ['gps/area.py', 'tools/gps_recompute_days.py',
                          'docs/GPS_RELEASE_RUNBOOK.md'],
            'Modified': [], 'Services': TEST_SERVICES, 'StopFails': [],
            'FreeBytes': 50 * 1024 ** 3, 'LockKind': 'clean',
            'BackupKind': 'ok', 'MigrateFirst': 'done',
            'MigrateSecond': 'again',
            'Web': {'/login': {'Status': 200, 'Body': LOGIN_BODY},
                    '/wialon/mapping': {'Status': 200, 'Body': LOGIN_BODY},
                    '/drones/fields': {'Status': 200, 'Body': LOGIN_BODY}},
            'ErrorLogAfterStart': None,
            'Outputs': self.outputs(),
            'MethodLine': 'METHOD_VERSION = "overflow-cap-2026-10-07"',
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
        # файл метода GPS, каким его оставит перемотка: шаг 3 читает его с диска
        os.makedirs(os.path.join(prod, 'gps'))
        with open(os.path.join(prod, 'gps', 'area.py'), 'w', encoding='ascii') as fh:
            fh.write('PREVIOUS_METHOD_VERSION = "adaptive-alpha-2026-08-12"\n%s\n'
                     % scenario.pop('MethodLine'))
        scenario['ErrorLog'] = errlog
        log = os.path.join(work, 'release.log')
        body = prepare(release_block(name), {
            'prod': "'%s'" % prod, 'db': "'%s'" % db,
            'py': "'Invoke-FakePython'", 'backupBat': "'Invoke-FakeBackup'",
            'errLog': "'%s'" % errlog,
            'services': '@(%s)' % ', '.join("'%s'" % s for s in TEST_SERVICES),
            'site': "'http://gps-test.invalid'", 'work': "'%s'" % work,
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
                           'Restart-Service', 'GET /login', 'GET /wialon/mapping',
                           'GET /drones/fields')
        self.assertFalse([c for c in calls if c.startswith('python migrate')])
        self.assertEqual(len([c for c in calls if 'check_migration_drift' in c]), 2)
        self.assertEqual(len([c for c in calls if 'check_db_lock' in c]), 1)
        self.assertBackUp(calls)
        self.assertRegex(output, r'MIGRATIONS REGISTERED: (\d+) -> \1, unchanged')
        self.assertIn('BACKUP: ', output)
        self.assertIn('SMOKE /wialon/mapping: asks to log in', output)
        self.assertIn('SMOKE /drones/fields: asks to log in', output)
        self.assertIn('NEW TRACEBACKS IN logs\\error.log: 0', output)
        self.assertIn('PROGRAM VERSION NOW: %s' % RELEASE_HASH[:7], output)
        self.assertIn('GPS METHOD: overflow-cap-2026-10-07', output)
        self.assertIn('RESULT: RELEASE PASSED', transcript)

    def test_the_previous_gps_method_on_disk_is_a_stop(self):
        # Перемотка прошла, но на диске прежний метод: ночной расчёт пошёл бы
        # прежним путём. Службы подняты, база не менялась, шаг 4 не делать.
        for line in ('METHOD_VERSION = "adaptive-alpha-2026-08-12"', '# nothing'):
            with self.subTest(line=line):
                result, calls, output, _ = self.run_block(
                    'Шаг 3', self.scenario(MethodLine=line))
                self.assertIn('after the update gps\\area.py says', result)
                self.assertIn('expected the method overflow-cap-2026-10-07',
                              result)
                self.assertBackUp(calls)
                self.assertNotIn('GPS METHOD:', output)
                self.assertNotIn('GET /login', calls)

    def test_a_stale_wal_is_the_known_noise_not_a_stop(self):
        result, _calls, output, _ = self.run_block(
            'Шаг 3', self.scenario(LockKind='stale'))
        self.assertEqual(result, 'RESULT: RELEASE PASSED', output)

    def test_another_track_pending_migration_is_left_alone(self):
        out = self.outputs('drift_extra_migrated', 'drift_extra_migrated')
        result, _calls, output, _ = self.run_block('Шаг 3',
                                                   self.scenario(Outputs=out))
        self.assertEqual(result, 'RESULT: RELEASE PASSED', output)

    def test_checks_that_fail_change_nothing(self):
        cases = {
            'not run as administrator': dict(Admin=False),
            'not SRV-YOQSH': dict(Host='SOME-PC'),
            'git fetch failed': dict(FetchCode=128),
            'not in main yet': dict(Ancestry=[[BASELINE, RELEASE_HASH]]),
            'changes outside docs/': dict(ChangedAfterReviewed=['docs/a.md',
                                                                'drones.py']),
            'the release gate is closed: 1 open item': dict(GateLines=gate_lines(1)),
            'could not be read': dict(GateLines=[]),
            'already on the server': dict(Head=RELEASE_HASH),
            'this release was prepared for': dict(Head='0' * 40),
            'edited on this server': dict(Modified=[' M app.py']),
            'does not continue': dict(Ancestry=[[REVIEWED_HASH, RELEASE_HASH]]),
            'migrations in the release: migrate_drones_x_001.py; this release '
            'has none': dict(DiffNames=['wialon_import.py',
                                        'migrate_drones_x_001.py']),
        }
        for message, change in cases.items():
            with self.subTest(case=message):
                result, calls, output, _ = self.run_block(
                    'Шаг 3', self.scenario(**change))
                self.assertTrue(result.startswith('RESULT: STOP - '), output)
                self.assertIn(message, result)
                self.assertIn('Nothing was changed', output)
                self.assertUntouched(calls)

    def test_a_stop_before_the_update_brings_the_old_version_back(self):
        cases = {
            'did not stop within 90 seconds': dict(StopFails=['GpsTestBot']),
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
        # После перемотки появилась незарегистрированная миграция: в этом
        # выпуске её быть не может. Службы подняты, версия -- новая.
        out = self.outputs('drift_migrated', 'drift_merged')
        result, calls, output, _ = self.run_block('Шаг 3',
                                                  self.scenario(Outputs=out))
        self.assertIn('after the update the migrations are', result)
        self.assertBackUp(calls)
        self.assertIn('PROGRAM VERSION NOW: %s' % RELEASE_HASH[:7], output)
        self.assertNotIn('GET /login', calls)

    def test_a_dead_site_after_the_start_is_a_stop(self):
        web = {'/login': {'Status': 503, 'Body': ''},
               '/wialon/mapping': {'Status': 503, 'Body': ''}}
        result, calls, _output, _ = self.run_block('Шаг 3', self.scenario(Web=web))
        self.assertIn('the login page did not open', result)
        self.assertBackUp(calls)
        web = {'/login': {'Status': 200, 'Body': LOGIN_BODY},
               '/wialon/mapping': {'Status': 404, 'Body': 'Not Found'},
               '/drones/fields': {'Status': 200, 'Body': LOGIN_BODY}}
        result, calls, _output, _ = self.run_block('Шаг 3', self.scenario(Web=web))
        self.assertIn('/wialon/mapping answered', result)
        self.assertNotIn('GET /drones/fields', calls)

    def test_the_old_code_still_running_is_a_stop(self):
        # Службы поднялись, но на прежней версии: /drones/fields там нет (404),
        # а /login и /wialon/mapping отвечают как обычно.
        web = {'/login': {'Status': 200, 'Body': LOGIN_BODY},
               '/wialon/mapping': {'Status': 200, 'Body': LOGIN_BODY},
               '/drones/fields': {'Status': 404, 'Body': 'Not Found'}}
        result, calls, output, _ = self.run_block('Шаг 3', self.scenario(Web=web))
        self.assertIn('/drones/fields answered', result)
        self.assertIn('SMOKE /wialon/mapping: asks to log in', output)
        self.assertNotIn('SMOKE /drones/fields', output)
        self.assertInOrder(calls, 'Restart-Service', 'GET /login', 'GET /drones/fields')

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
        self.assertIn('RELEASE: %s -> %s' % (BASELINE, RELEASE_HASH), output)
        self.assertIn('Merge pull request', output)
        self.assertIn('RESULT: CHECK PASSED', transcript)

    def test_the_check_stops_on_what_the_release_would_stop_on(self):
        cases = {
            'the release gate is closed': dict(GateLines=gate_lines(1)),
            'not in main yet': dict(Ancestry=[[BASELINE, RELEASE_HASH]]),
            'changes outside docs/': dict(ChangedAfterReviewed=['app.py']),
            'already on the server': dict(Head=RELEASE_HASH),
            'this release has none': dict(DiffNames=['migrate_x_001.py']),
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
                result, calls, _output, _ = self.run_block(
                    'Откат', self.scenario(**change))
                self.assertIn(message, result)
                self.assertUntouched(calls)


if __name__ == '__main__':
    unittest.main()
