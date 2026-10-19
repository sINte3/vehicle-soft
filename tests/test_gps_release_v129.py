# -*- coding: utf-8 -*-
"""Выпуск v1.29 на production: docs/GPS_RELEASE_V129_RUNBOOK.md.

Выпуск -- метод «время»: сборщик сохраняет зажигание и снимки счётчика
моточасов (GPS-24, GPS-25), таблица часов `gps_daily_engine` (миграция
GPS_ENGINE_001, GPS-26, пробег -- GPS-29), сверка заявок «время» и одна
«Сверка» в меню (agro-work §9.6-9.8), проход `sbor_nepolnyy` без точек на
диске (GPS-30). Блоки ранбука владелец вставляет в Windows PowerShell 5.1 на
боевом сервере, и шаг 3 останавливает три службы и применяет миграцию.

Блок шага 3 -- блок выпуска v1.20 (он прошёл на этом сервере с миграцией),
шаг 2 и откат -- блоки v1.28; меняются только постоянные, имя миграции и
журналы. Здесь держится:

  * текст: нет плейсхолдеров и `&&`, ASCII без табуляций, git -- только
    проверенные команды, службы останавливаются только в выпуске и откате,
    порядок процедуры с миграцией, проверка ничего не меняет, постоянные
    настоящие, закрепление верно, в дельте ровно одна миграция и нет общих
    файлов устава, а код дельты -- ровно треки GPS и agro-work с 19.10;
  * блоки -- те же, что в выпусках v1.20 и v1.28, кроме перечисленных
    постоянных и строк, а их функции дословно те же, что в выпуске v1.20;
  * строка миграции, которую ждёт блок, -- та, что печатает миграция;
  * то, что шаги 4 и 5 велят увидеть, -- то, что печатают инструменты:
    дозаполнение, расчёт часов, сборщик; флаги команд есть у argparse;
    третья строка ночного расчёта -- та, что записана в gps/README.md, а две
    прежние -- те, что в ранбуке развёртывания;
  * то, что ранбук называет новым, в production действительно новое;
  * блоки исполняются против подставных git, служб, питона и сайта
    (tests/agro_work_release_harness.ps1) во всех путях «стоп», а блок
    третьей строки -- против временного файла: на PowerShell 7 и -- в CI --
    на Windows PowerShell 5.1.

Запуск: python -m unittest tests.test_gps_release_v129 -v
Блоки в PowerShell: GPS_RELEASE_POWERSHELL=pwsh (или powershell) в окружении.
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
from contextlib import redirect_stdout
from urllib.parse import parse_qs

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if REPO_ROOT not in sys.path:
    sys.path.insert(0, REPO_ROOT)

import migration_utils                                             # noqa: E402
import migrate_gps_engine_001 as engine_mig                        # noqa: E402
from tests.test_agro_work_runbook import (                    # noqa: E402
    BACKUP_PATTERNS, DRIFT_PATTERNS, GATE_SEPARATOR, HARNESS, PYTHON,
    _backup_outputs, _entry, _lock_outputs, commands, constants, gate_lines,
    git, has_commit, open_items, parse_backup, parse_drift, prepare, sections,
    steps, text)
from tests.test_agro_work_runbook import RELEASE as V120     # noqa: E402
from tests.test_agro_work_release_v122 import (              # noqa: E402
    functions, release_block)
from tests.test_gps_release_v128 import RELEASE as V128      # noqa: E402
from tools import check_migration_drift                       # noqa: E402

RELEASE = os.path.join(REPO_ROOT, 'docs', 'GPS_RELEASE_V129_RUNBOOK.md')
# [REASON]: то же правило, что у выпусков v1.20-v1.28, плюс модули, которые
# шаг 4 запускает через `-m`: им тоже нужна папка программы текущей.
RELATIVE_SCRIPT = re.compile(r'(?<![\\A-Za-z:-])(tools\\|migrate_[A-Za-z0-9_]+\.py'
                             r'|run_server\.py|-m unittest|-m gps\.daily'
                             r'|-m gps\.engine_hours|-m gps_collector\.)')
# Production на момент подготовки: строка production в docs/DEPLOYED.md.
BASELINE = '784d18b722dbcc761081e67dcc03ca824355244f'
# [REASON]: окружение расчёта GPS -- отдельный venv без пробела в пути; им
# ночной расчёт запускает и площадь, и часы (gps_daily.bat). Шаг 4 считает
# часы тем же окружением, что и ночь.
GEO_PYTHON = '& C:\\gps_venv\\Scripts\\python.exe'
SERVICES = "@('TransportReport', 'TransportBot', 'TransportBot003')"
BLOCKS = ('Шаг 2', 'Шаг 3', 'Откат')
MIGRATION = 'migrate_gps_engine_001.py'
PENDING_ID = 'GPS_ENGINE_001 (migrate_gps_engine_001.py)'
REFILL_FROM = '2026-08-01'
BAT = 'C:\\transport-report\\gps_daily.bat'
BAT_COPY = 'C:\\VehicleSoft_Release\\gps_daily_before_v129.bat'
BAT_LOG = 'C:\\VehicleSoft_Release\\release_v129_step4_bat.log'
THIRD_LINE = 'C:\\gps_venv\\Scripts\\python.exe -m gps.engine_hours >> gps_daily.log 2>&1'


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


def read(*parts):
    with open(os.path.join(REPO_ROOT, *parts), encoding='utf-8') as fh:
        return fh.read()


def in_code(path, pattern):
    found = re.findall(pattern, read(*path.split('/')), re.M)
    assert len(found) == 1, (path, pattern, found)
    return found[0]


def bat_block():
    """Блок 4.1 -- третья строка ночного расчёта, как есть."""
    parts = [p for p in re.findall(r'```powershell\n(.*?)```', raw_step('Шаг 4'), re.S)
             if '$bat  = ' in p]
    assert len(parts) == 1, len(parts)
    return parts[0]


def method_version():
    """METHOD_VERSION gps/engine_hours.py -- по числам правил R и P."""
    gap = int(in_code('gps/ignition.py', r'^MAX_GAP_S = (\d+)$'))
    speed = float(in_code('gps/mileage.py', r'^MAX_SPEED_KMH = ([0-9.]+)$'))
    pattern = in_code('gps/engine_hours.py',
                      r"^METHOD_VERSION = '(ignition-R%d-P%d-\d{4}-\d{2}-\d{2})' %")
    return pattern % (gap, speed)


class ReleaseV129Runbook(unittest.TestCase):
    """Выпуск на production: блоки, которые остановят боевые службы."""

    def lines(self):
        return commands(path=RELEASE)

    def test_there_are_commands_at_all(self):
        self.assertGreater(len(self.lines()), 15)

    def test_no_placeholders_and_no_double_ampersand(self):
        for line in self.lines():
            self.assertNotRegex(line, r'<[^>]*>', line)
            self.assertNotIn('&&', line)
            # `@echo off` -- первая строка gps_daily.bat, которую блок 4.1 сверяет.
            self.assertNotIn('@', line.replace('@(', '').replace('@arguments', '')
                             .replace('@{', '').replace("'@echo off'", ''), line)
            for word in ('YYYY', 'XXX', 'TODO', '...'):
                self.assertNotIn(word, line, line)
        # Метки черновика не остались и в тексте: владелец читает его целиком.
        self.assertNotRegex(text(RELEASE), r'@[A-Z0-9_]+@')

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
            'if ($open -gt 0)',                               # проверки
            'if ($head -ne $baseline)',
            'if (($migrations.Count -ne 1) -or ($migrations[0] -ne $migration))',
            '$drift0 = Get-Drift',
            '$stopped = $true',
            'foreach ($name in $services) { Stop-Service',
            "tools\\check_db_lock.py', '--db', $db",
            'Invoke-Tool $backupBat',
            "'merge', '--ff-only', $release",
            '$drift1 = Get-Drift',
            'if ($drift1.Pending -ne $want1)',
            '$first = Invoke-Tool $py @($migration)',
            '$second = Invoke-Tool $py @($migration)',
            '$drift2 = Get-Drift',
            'if ($drift2.Registered -ne ($drift0.Registered + 1))',
            '{ Restart-Service -Name $name }',
            "($site + '/login')",
            "($site + '/agro-work/')",
        ]
        positions = [body.index(mark) for mark in marks]
        self.assertEqual(positions, sorted(positions))
        # Миграция -- только после копии и перемотки, и база перед ней снова
        # проверяется на блокировку: службы остановлены, но мало ли кто ещё.
        locks = [m.start() for m in re.finditer(
            re.escape("tools\\check_db_lock.py', '--db', $db"), body)]
        self.assertEqual(len(locks), 2)
        self.assertLess(body.index("'merge', '--ff-only', $release"), locks[1])
        self.assertLess(locks[1], body.index('$first = Invoke-Tool $py @($migration)'))
        self.assertEqual(body.count('Invoke-Tool $py @($migration)'), 2)

    def test_the_check_step_changes_nothing(self):
        body = block('Шаг 2')
        for word in ('Stop-Service', 'Restart-Service', "'merge'", 'reset',
                     'Invoke-Tool $backupBat', '@($migration)', '& $py $migration'):
            self.assertNotIn(word, body)
        self.assertIn("if (($migrations.Count -ne 1) -or ($migrations[0] -ne "
                      "$migration))", body)

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
            'migration': "'%s'" % MIGRATION,
            'pendingId': "'%s'" % PENDING_ID,
            'doneLine': "'%s'" % done_line(),
            'againLine': "'Already applied. Nothing to do.'",
        }
        reviewed = set()
        for name in BLOCKS:
            found = constants(block(name))
            with self.subTest(block=name):
                for key, value in found.items():
                    if key in expected:
                        self.assertEqual(value, expected[key], key)
                self.assertEqual(found['baseline'], expected['baseline'])
                self.assertRegex(found['reviewed'], r"^'[0-9a-f]{40}'$")
                self.assertRegex(found['log'],
                                 r"^'C:\\VehicleSoft_Release\\release_v129_[a-z0-9]+\.log'$")
                reviewed.add(found['reviewed'])
                self.assertIn("Where-Object { $_ -notlike 'docs/*' }",
                              block(name))
                if name != 'Откат':
                    self.assertIn('Get-OpenItems @(& git show '
                                  '"${release}:docs/RELEASE_GATE.md")',
                                  block(name))
                    self.assertIn('if ($open -gt 0) { throw', block(name))
                    self.assertEqual(found['migration'], expected['migration'])
        for key in ('migration', 'pendingId', 'doneLine', 'againLine'):
            self.assertIn(key, constants(block('Шаг 3')), key)
        self.assertEqual(len(reviewed), 1)
        block_logs = {constants(block(name))['log'].strip("'") for name in BLOCKS}
        self.assertEqual(len(block_logs), 3)
        # Журналы шага 4 -- те, в которые его команды пишут через `*>`.
        step_logs = set()
        for line in self.lines():
            step_logs.update(re.findall(
                r'\*>>? (C:\\VehicleSoft_Release\\release_v129_[a-z0-9_]+\.log)', line))
        self.assertEqual(step_logs, {
            BAT_LOG, 'C:\\VehicleSoft_Release\\release_v129_step4_refill.log',
            'C:\\VehicleSoft_Release\\release_v129_step4_hours.log'})
        # Журналы, которые ранбук велит прислать, -- ровно те, что блоки и
        # шаг 4 пишут: каждый назван полным путём, чужой (прежнего выпуска)
        # не назван нигде.
        prose = re.sub(r'```powershell\n.*?```', '', text(RELEASE), flags=re.S)
        named = set(re.findall(r'release_v\d+_[a-z0-9_]+\.log', prose))
        self.assertEqual(named, {log.split('\\')[-1]
                                 for log in block_logs | step_logs})
        for log in block_logs | step_logs:
            self.assertIn('`%s`' % log, prose)
        with open(os.path.join(REPO_ROOT, 'backup_production_db.bat'),
                  encoding='ascii') as fh:
            self.assertIn('--source "%s"' % expected['db'].strip("'"), fh.read())
        # Блок готовился от записанного production. После выпуска строка
        # production сменится, а этот коммит останется в журнале релизов.
        self.assertIn('`%s`' % BASELINE[:7], read('docs', 'DEPLOYED.md'))

    def test_the_blocks_are_those_that_passed_but_the_constants(self):
        # Шаг 3 -- блок v1.20 (прошёл на этом сервере с миграцией 30.09),
        # шаг 2 и откат -- блоки v1.28 (прошли 19.10). Здесь они те же; другие
        # только постоянные, имя миграции и строки, перечисленные ниже, --
        # каждая заменяется ровно один раз.
        def bare(body, keys):
            found = constants(body)
            for key in keys:
                self.assertEqual(body.count(found[key]), 1, key)
                body = body.replace(found[key], "'@%s@'" % key)
            return body

        def once(body, old, new):
            self.assertEqual(body.count(old), 1, old)
            return body.replace(old, new)

        keys3 = ('baseline', 'reviewed', 'log', 'work', 'migration', 'pendingId',
                 'doneLine')
        theirs = bare(release_block('Шаг 3', path=V120), keys3)
        theirs = once(theirs, 'was PR #154 merged in step 1?',
                      'was the release PR merged in step 1?')
        theirs = once(theirs, "'== 6. Migration AGRO_WORK_001 - seven new empty tables'",
                      "'== 6. Migration GPS_ENGINE_001 - one new empty table'")
        theirs = once(theirs, 'send the log file before step 5',
                      'send the log file before step 4')
        self.assertEqual(bare(block('Шаг 3'), keys3), theirs)

        theirs = bare(release_block('Шаг 2', path=V128), ('baseline', 'reviewed', 'log'))
        theirs = once(theirs, "$reviewed  = '@reviewed@'\n",
                      "$reviewed  = '@reviewed@'\n$migration = '%s'\n" % MIGRATION)
        theirs = once(
            theirs,
            """if ($migrations.Count -gt 0) { throw "migrations in the release: """
            """$($migrations -join ', '); this release has none" }""",
            """if (($migrations.Count -ne 1) -or ($migrations[0] -ne $migration)) """
            """{ throw "migrations in the release: $($migrations -join ', '); """
            """expected only $migration" }""")
        self.assertEqual(bare(block('Шаг 2'), ('baseline', 'reviewed', 'log')), theirs)
        # Та же проверка миграции -- слово в слово как в шаге 2 выпуска v1.20.
        line = [ln for ln in block('Шаг 2').splitlines() if '$migrations.Count' in ln]
        self.assertEqual(len(line), 1)
        self.assertIn(line[0], release_block('Шаг 2', path=V120))

        keys = ('baseline', 'reviewed', 'log')
        self.assertEqual(bare(block('Откат'), keys),
                         bare(release_block('Откат', path=V128), keys))

    def test_the_helpers_are_word_for_word_those_of_v120(self):
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
        # Всё, что изменилось после проверенного коммита ДО последней правки
        # ранбука, -- только docs/. Код, влитый позже, ловят шаги 2 и 3.
        # Держится там, где оба коммита есть в клоне (в CI клон глубиной 1).
        commit = constants(block('Шаг 3'))['reviewed'].strip("'")
        written = git('log', '-1', '--format=%H', '--',
                      'docs/GPS_RELEASE_V129_RUNBOOK.md').strip()
        if not (written and has_commit(commit) and has_commit(written)):
            self.skipTest('the pinned history is outside this clone')
        ancestor = subprocess.run(['git', 'merge-base', '--is-ancestor', commit,
                                   written], cwd=REPO_ROOT)
        self.assertEqual(ancestor.returncode, 0)
        names = git('diff', '--name-only', commit, written).split()
        self.assertEqual([n for n in names if not n.startswith('docs/')], [])

    def test_the_delta_is_the_gps_and_agro_work_tracks(self):
        commit = constants(block('Шаг 3'))['reviewed'].strip("'")
        if not (has_commit(BASELINE) and has_commit(commit)):
            self.skipTest('the release delta is outside this clone')
        names = git('diff', '--name-only', BASELINE, commit).split()
        self.assertEqual([n for n in names if n.startswith('migrate_')], [MIGRATION])
        # [REASON]: общие файлы устава правятся только с объявлением; в этом
        # выпуске их нет, и ранбук этого не объявляет.
        for shared in ('app.py', 'models.py', 'templates/base_next.html',
                       'static/css/design-system.css', 'migration_utils.py',
                       'excel_export.py', 'requirements.txt'):
            self.assertNotIn(shared, names)
        # [REASON]: из кода программы меняется ровно этот список -- треки GPS
        # и agro-work с 19.10: сборщик (зажигание, счётчик, дозаполнение),
        # таблица часов и её расчёт, правила R и P, ночной расчёт (GPS-30),
        # сверка «время» и одна «Сверка», ручные инструменты. Шаги 4 и 5
        # смотрят именно это; чужой код в дельте сделал бы их недостаточной
        # проверкой.
        code = sorted(n for n in names if not n.startswith(('docs/', 'tests/')))
        self.assertEqual(code, [
            '.github/workflows/checks.yml',
            'agro_work/labels.py', 'agro_work/methods.py',
            'agro_work/reconcile.py', 'agro_work_routes.py',
            'gps/README.md', 'gps/daily.py', 'gps/engine_hours.py',
            'gps/ignition.py', 'gps/mileage.py',
            'gps/tests/test_daily.py', 'gps/tests/test_engine_hours.py',
            'gps/tests/test_ignition_rule.py', 'gps/tests/test_mileage_rule.py',
            'gps/tolerance.py',
            'gps_collector/README.md', 'gps_collector/config.py',
            'gps_collector/main.py', 'gps_collector/refill.py',
            'gps_collector/storage.py', 'gps_collector/tests/support.py',
            'gps_collector/tests/test_ignition.py',
            'gps_collector/tests/test_storage.py', 'gps_collector/wialon.py',
            MIGRATION,
            'templates/_module_nav.html', 'templates/agro_work/_period.html',
            'templates/agro_work/_tabs.html',
            'templates/agro_work/applications.html',
            'templates/agro_work/dashboard.html',
            'templates/agro_work/work_days.html',
            'tools/agro_work_reconcile.py', 'tools/gps_engine_hours_check.py',
            'tools/gps_engine_report_check.py', 'tools/gps_engine_report_probe.py',
            'tools/gps_ignition_sensors.py',
            'tools/ux/check_agro_work_screen.mjs', 'tools/ux/serve_agro_work.py'])
        self.assertIn('tests/test_gps_release_v129.py', names)
        self.assertIn('tests/agro_work_release_harness.ps1', names)

    def test_the_merged_prs_named_in_step_two_are_the_delta(self):
        # Шаг 2 говорит, какие «Merge pull request» владелец увидит. Список
        # сверяется с историей от production до проверенного коммита (PR
        # выпуска вливается позже и назван отдельно).
        commit = constants(block('Шаг 3'))['reviewed'].strip("'")
        if not (has_commit(BASELINE) and has_commit(commit)):
            self.skipTest('the release delta is outside this clone')
        merged = {int(n) for n in re.findall(
            r'^Merge pull request #(\d+)', git('log', '--merges', '--format=%s',
                                                '%s..%s' % (BASELINE, commit)), re.M)}
        self.assertTrue(merged)
        body = step('Шаг 2')
        named = set()
        for first, last in re.findall(r'`#(\d+)`–`#(\d+)`', body):
            named.update(range(int(first), int(last) + 1))
        named.update(int(n) for n in re.findall(r'`#(\d+)`', body))
        self.assertTrue(merged <= named, sorted(merged - named))
        # PR этого ранбука вливается после проверенного коммита и назван
        # отдельно: вливать владельцу нечего (шаг 1).
        runbook_pr = int(re.search(r'`#(\d+)` — этот ранбук', body).group(1))
        self.assertEqual(named - merged, {runbook_pr})
        self.assertNotIn('/pull/', text(RELEASE))
        self.assertIn('вливать на GitHub ничего не нужно', step('Шаг 1'))
        # Без squash: каждый PR дельты -- со строкой «Merge pull request».
        # Сверху -- свои коммиты ветки ранбука (закреплённый -- среди них),
        # ниже последнего мержа чужих коммитов без мержа нет.
        subjects = git('log', '--first-parent', '--format=%s',
                       '%s..%s' % (BASELINE, commit)).splitlines()
        while subjects and not subjects[0].startswith('Merge pull request #'):
            subjects.pop(0)
        self.assertTrue(subjects)
        self.assertTrue(all(s.startswith('Merge pull request #') for s in subjects),
                        subjects)

    def test_what_the_runbook_calls_new_is_new(self):
        # Ранбук говорит: таблицы часов, дозаполнения и зажигания у точек на
        # production нет, а в проверенном коммите они есть и печатают то,
        # что шаги 3-5 велят увидеть.
        commit = constants(block('Шаг 3'))['reviewed'].strip("'")
        if not (has_commit(BASELINE) and has_commit(commit)):
            self.skipTest('the release delta is outside this clone')
        for path in (MIGRATION, 'gps/engine_hours.py', 'gps/ignition.py',
                     'gps/mileage.py', 'gps_collector/refill.py'):
            with self.subTest(path=path):
                absent = subprocess.run(['git', 'cat-file', '-e',
                                         '%s:%s' % (BASELINE, path)],
                                        cwd=REPO_ROOT, capture_output=True)
                self.assertNotEqual(absent.returncode, 0, path)
        self.assertNotIn('IGNITION_COLUMN',
                         git('show', '%s:gps_collector/storage.py' % BASELINE))
        self.assertNotIn('gps_daily_engine',
                         git('show', '%s:agro_work/reconcile.py' % BASELINE))
        self.assertNotIn('ignition_line',
                         git('show', '%s:gps_collector/main.py' % BASELINE))
        self.assertNotIn('left alone -- their points are no longer on disk',
                         git('show', '%s:gps/daily.py' % BASELINE).split(
                             'def catch_up(')[1].split('def main(')[0])
        for path in (MIGRATION, 'gps/engine_hours.py', 'gps_collector/refill.py',
                     'gps_collector/main.py', 'gps/daily.py'):
            self.assertEqual(read(*path.split('/')),
                             git('show', '%s:%s' % (commit, path)), path)

    def test_step_three_waits_for_what_the_migration_prints(self):
        self.assertIn("print('Done. Table gps_daily_engine with %d columns and %d "
                      "index is in place.'", read(MIGRATION))
        self.assertIn("print('Already applied. Nothing to do.')", read(MIGRATION))
        self.assertEqual(done_line(),
                         'Done. Table gps_daily_engine with 11 columns and 1 index '
                         'is in place.')
        self.assertIn('`%s`' % done_line(), step('Шаг 3'))
        self.assertIn('`Already applied. Nothing to do.`', step('Шаг 3'))
        self.assertEqual(engine_mig.MIGRATION_ID, 'GPS_ENGINE_001')
        # Реестр на production -- 60 (docs/DEPLOYED.md), миграция -- одна.
        self.assertIn('| production | `%s` |' % BASELINE[:7], read('docs', 'DEPLOYED.md'))
        self.assertRegex(read('docs', 'DEPLOYED.md'),
                         r'\| production \| `%s` \| [^|]+ \| [^|]+ \| 60 \|' % BASELINE[:7])
        self.assertIn('`MIGRATIONS REGISTERED: 60 -> 61`', step('Шаг 3'))

    def test_step_four_waits_for_what_the_tools_print(self):
        refill = read('gps_collector', 'refill.py')
        hours = read('gps', 'engine_hours.py')
        collector = read('gps_collector', 'main.py')
        body = step('Шаг 4')
        # Дозаполнение: итог, точки, месяцы, последняя строка, коды выхода.
        for printed, shown in (
                ('"objects: %d | without an ignition sensor: %d | nothing to "\n'
                 '                "fill: %d | asked: %d | failed: %d"',
                 '`objects: ... | without an ignition sensor: ... | nothing to fill: '
                 '... | asked: ... | failed: 0`'),
                ('"points without ignition before: %d | messages read: %d, with "\n'
                 '                "an ignition value: %d | points filled: %d | pieces truncated "\n'
                 '                "by the limit: %d"',
                 '`points without ignition before: ... | messages read: ..., with an '
                 'ignition value: ... | points filled: ... | pieces truncated by the '
                 'limit: ...`'),
                ('"  %s: points %d, with ignition %d"',
                 '`2026-09: points ..., with ignition ...`'),
                ('print("REFILL ONLY: no point inserted, no watermark moved, nothing "\n'
                 '          "written to Wialon")',
                 '`REFILL ONLY: no point inserted, no watermark moved, nothing '
                 'written to Wialon`'),
                ('print("before:")', '`before:`'),
                ('print("after:")', '`after:`')):
            with self.subTest(printed=shown):
                self.assertIn(printed, refill)
                self.assertIn(shown, body)
        self.assertIn('EXIT_OK = 0\nEXIT_PARTIAL = 1\nEXIT_BAD_INPUT = 2\n'
                      'EXIT_LOGIN = 3\n', refill)
        for code in ('`exit 0`', '`exit 1`', '`exit 3`'):
            self.assertIn(code, body.split('**4.3.')[0])
        self.assertIn('ERROR: ne udalos voyti', refill)
        self.assertIn('`ERROR: ne udalos voyti`', text(RELEASE))
        # Расчёт часов: первая и последняя строки, строка суток, коды выхода.
        self.assertIn("print('engine hours %s -> %s (%d days), rule %s'", hours)
        self.assertIn('`engine hours %s -> ... (... days), rule %s`'
                      % (REFILL_FROM, method_version()), body)
        self.assertIn("print('object-days with points: %d | recomputed: %d | "
                      "unchanged: %d | '\n          'recomputed with ignition data: "
                      "%d | failed: %d'", hours)
        self.assertIn('`object-days with points: ... | recomputed: ... | unchanged: '
                      '... | recomputed with ignition data: ... | failed: 0`', body)
        self.assertIn("log('%s: objects %d, recomputed %d' % (day, len(on_disk), done))",
                      hours)
        self.assertIn('`2026-09-15: objects ..., recomputed ...`', body)
        self.assertIn("print('  FAILED %s %s: %s' % (day, unit_id, kind))", hours)
        self.assertIn("sys.stderr.write('ERROR: no table %s -- migration GPS_ENGINE_001 is '\n"
                      "                         'not applied\\n' % TABLE)", hours)
        self.assertIn("TABLE = 'gps_daily_engine'", hours)
        self.assertIn('`ERROR: no table gps_daily_engine -- migration GPS_ENGINE_001 '
                      'is not applied`', body)
        self.assertIn('EXIT_OK = 0\nEXIT_BAD_INPUT = 2\nEXIT_PARTIAL = 5\n', hours)
        after = body.split('**4.3.')[1].split('**4.4.')[0]
        for code in ('`exit 0`', '`exit 5`', '`exit 2`'):
            self.assertIn(code, after)
        self.assertEqual(method_version(), 'ignition-R300-P150-2026-10-19')
        # Шаг 5: строки сборщика и ночного расчёта.
        self.assertIn('"ignition: %d of %d points with a position carry it | asked "\n'
                      '                "objects without an ignition sensor: %d | filled '
                      'into points "\n                "stored before: %d | engine-hour '
                      'counters noted: %d"', collector)
        five = step('Шаг 5')
        self.assertIn('`ignition: ... of ... points with a position carry it | asked '
                      'objects without an ignition sensor: ... | filled into points '
                      'stored before: ... | engine-hour counters noted: ...`', five)
        self.assertEqual(in_code('gps/engine_hours.py', r'^WINDOW_DAYS = (\d+)$'), '30')
        self.assertIn('`engine hours ... -> ... (30 days), rule %s`' % method_version(),
                      five)
        self.assertIn('`object-days with points: ... | failed: 0`', five)
        self.assertIn("-Pattern '^ignition:'", five)
        self.assertIn("-Pattern '^engine hours', '^object-days with points'", five)

    def test_step_four_flags_exist_and_the_order_is_safe(self):
        refill = read('gps_collector', 'refill.py')
        hours = read('gps', 'engine_hours.py')
        lines = commands(raw_step('Шаг 4'))
        # Запуски -- строки с `&`; в блоке 4.1 модуль часов назван строкой
        # данных (`$line = ...`), это не запуск.
        runs = [line for line in lines if line.startswith('& ')]
        fills = [line for line in runs if '-m gps_collector.refill' in line]
        self.assertEqual(len(fills), 1)
        self.assertTrue(fills[0].startswith(PYTHON + ' -m gps_collector.refill '))
        self.assertIn(" --from %s --to (Get-Date -Format 'yyyy-MM-dd') " % REFILL_FROM,
                      fills[0])
        for flag in re.findall(r' (--[a-z-]+)', fills[0]):
            self.assertIn('parser.add_argument("%s"' % flag, refill)
        # `--to` позже «сейчас» инструмент сам обрезает: последние минуты --
        # ночному сбору.
        self.assertIn('t_to = min(t_to, int(now - config.LAG_MINUTES * 60))', refill)
        counts = [line for line in runs if '-m gps.engine_hours' in line]
        self.assertEqual(len(counts), 1)
        self.assertTrue(counts[0].startswith(GEO_PYTHON + ' -m gps.engine_hours '))
        self.assertIn(' --from %s ' % REFILL_FROM, counts[0])
        for flag in re.findall(r' (--[a-z-]+)', counts[0]):
            self.assertIn("parser.add_argument('%s'" % flag, hours)
        # Ни одна команда шага 4 не пишет в базу, кроме расчёта часов, и
        # расчёт идёт после дозаполнения; базу ему не подменяют.
        order = [i for i, line in enumerate(runs)
                 if '-m gps_collector.refill' in line or '-m gps.engine_hours' in line]
        self.assertEqual(order, sorted(order))
        self.assertNotIn('--db', counts[0])
        self.assertNotIn('--dir', counts[0] + fills[0])
        # Дозаполнение дописывает журнал (повтор после Ctrl+C или `exit 1`),
        # расчёт -- переписывает.
        self.assertIn('*>> C:\\VehicleSoft_Release\\release_v129_step4_refill.log', fills[0])
        self.assertIn('*> C:\\VehicleSoft_Release\\release_v129_step4_hours.log', counts[0])
        self.assertNotIn('gps_mirror_contours', ' '.join(lines))
        self.assertNotIn('gps_retention', ' '.join(self.lines()))

    def test_the_third_line_is_the_one_the_track_wrote(self):
        rollout = read('docs', 'GPS_ROLLOUT_RUNBOOK.md')
        readme = read('gps', 'README.md')
        body = bat_block()
        want = re.search(r"^\$want = @\((.*)\)$", body, re.M).group(1)
        lines = re.findall(r"'([^']*)'", want)
        self.assertEqual(lines, [
            '@echo off', 'cd /d C:\\transport-report',
            'C:\\gps_venv\\Scripts\\python.exe -m gps.daily >> gps_daily.log 2>&1',
            'C:\\gps_venv\\Scripts\\python.exe -m gps.daily --catch-up >> gps_daily.log 2>&1'])
        # Две прежние строки -- те, что ранбук развёртывания велел записать.
        section = rollout.split('`C:\\transport-report\\gps_daily.bat`:')[1].split('```')[1]
        self.assertEqual([line for line in section.splitlines() if line.strip()], lines)
        # Третья -- та, что записана в gps/README.md.
        self.assertIn("$line = '%s'" % THIRD_LINE, body)
        self.assertIn('    ' + THIRD_LINE, readme)
        self.assertIn("$bat  = '%s'" % BAT, body)
        self.assertIn("$copy = '%s'" % BAT_COPY, body)
        self.assertTrue(body.startswith('cd C:\\transport-report\n& {\n'))
        self.assertIn('} *> %s\n' % BAT_LOG, body)
        self.assertIn('Set-Content -LiteralPath $bat -Value ($want + $line) -Encoding ASCII',
                      body)
        # Откат возвращает прежний файл ровно из этой копии.
        self.assertIn('Copy-Item -LiteralPath %s -Destination %s -Force' % (BAT_COPY, BAT),
                      ' '.join(commands(raw_step('Откат'))))
        self.assertIn('02:30', text(RELEASE))
        self.assertIn('schtasks /create /tn "GpsDaily" /tr '
                      '"C:\\transport-report\\gps_daily.bat" /sc daily /st 02:30', rollout)

    def test_the_time_rule_the_runbook_describes_is_the_rule_in_code(self):
        flat = ' '.join(text(RELEASE).split())
        reconcile = read('agro_work', 'reconcile.py')
        tolerance = read('gps', 'tolerance.py')
        labels = read('agro_work', 'labels.py')
        self.assertIn('зажигание включено 30 минут и больше', flat)
        self.assertIn('пробег за окно от 3 км', flat)
        self.assertIn('TRUCK_WORK_KM = 3.0', reconcile)
        self.assertIn("TRUCK_CATEGORY = 'yuk_transport'", reconcile)
        self.assertIn('до 15 % (не меньше 0,5 ч) — в допуске, до 25 % — на грани', flat)
        self.assertRegex(tolerance, r'0\.15')
        self.assertRegex(tolerance, r'0\.25')
        # Причины -- те, что на экране.
        for label in ('Метод «время»: трек GPS время работы пока не публикует',
                      'Нет данных GPS за часть суток окна'):
            with self.subTest(label=label):
                self.assertIn(label, labels)
                self.assertIn(label, flat)
        self.assertIn('853', flat)
        self.assertIn('853', read('docs', 'tracks', 'gps-plan-fakt.md'))

    def test_every_step_that_runs_a_script_first_goes_to_production(self):
        found = steps(RELEASE)
        self.assertGreaterEqual(len(found), 5)
        checked = 0
        for title, section in found.items():
            lines = commands(section)
            first = next((i for i, line in enumerate(lines)
                          if RELATIVE_SCRIPT.search(line)
                          and not re.match(r"^\$[A-Za-z]+\s*= (@\()?'", line)), None)
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
        step4 = [body for title, body in found.items() if title.startswith('Шаг 4')][0]
        parts = re.findall(r'```powershell\n(.*?)```', step4, re.S)
        self.assertEqual(len(parts), 3)
        for part in parts:
            with self.subTest(part=part[:60]):
                self.assertTrue(part.startswith('cd C:\\transport-report\n'), part)
        self.assertGreaterEqual(checked, 3)

    def test_every_named_script_exists_and_python_is_quoted(self):
        names = set()
        for line in self.lines():
            names.update(re.findall(r'(tools\\[A-Za-z0-9_]+\.py)', line))
            if 'python.exe' not in line.lower():
                continue
            if re.match(r"^\$[A-Za-z]+\s+= (@\()?'", line):
                continue                   # постоянная блока, проверена выше
            self.assertTrue(line.startswith(PYTHON)
                            or line.startswith(GEO_PYTHON), line)
            if '-m gps.engine_hours' in line:
                self.assertTrue(line.startswith(GEO_PYTHON), line)   # как ночью
            else:
                self.assertTrue(line.startswith(PYTHON), line)
        self.assertEqual(names, {'tools\\check_migration_drift.py',
                                 'tools\\check_db_lock.py'})
        for name in names:
            path = os.path.join(REPO_ROOT, name.replace('\\', os.sep))
            self.assertTrue(os.path.isfile(path), name)
        for path in (MIGRATION, 'gps/engine_hours.py', 'gps_collector/refill.py'):
            self.assertTrue(os.path.isfile(os.path.join(REPO_ROOT, *path.split('/'))))

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
        self.assertGreaterEqual(len(table), 14)
        blocks = ''.join(block(name) for name in BLOCKS)
        for line in table:
            for shown in re.findall(r'`RESULT: STOP - ([^`]*)`', line):
                words = shown.split('...')[0].strip()
                with self.subTest(stop=words):
                    self.assertTrue(words)
                    self.assertIn(words, blocks)
        self.assertIn("'BAT: STOP - gps_daily.bat is not as expected - nothing changed'",
                      bat_block())
        self.assertIn('`BAT: STOP - gps_daily.bat is not as expected`', text(RELEASE))

    def test_the_screen_addresses_are_real(self):
        source = read('agro_work_routes.py')
        addresses = re.findall(r'http://10\.103\.25\.14:5050(/[a-z/-]*)'
                               r'\?([^`\s]+)', text(RELEASE))
        self.assertEqual(sorted({path for path, _ in addresses}), ['/agro-work/'])
        for path, query in addresses:
            with self.subTest(address=query):
                parsed = parse_qs(query, strict_parsing=True)
                self.assertEqual(parsed, {'from': ['2026-09-01'], 'to': ['2026-09-30']})
        self.assertIn("_parse_date(request.args.get('to'))", source)
        self.assertIn("_parse_date(request.args.get('from'))", source)
        self.assertGreaterEqual(int(in_code('agro_work_routes.py',
                                            r'^MAX_PERIOD_DAYS = (\d+)$')), 30)
        tabs = read('templates', 'agro_work', '_tabs.html')
        flat = ' '.join(text(RELEASE).split())
        for tab in ('Свод', 'Заявка → работа', 'Работа → заявка'):
            with self.subTest(tab=tab):
                self.assertIn(tab, tabs)
                self.assertIn('«%s»' % tab, flat)
        nav = read('templates', '_module_nav.html')
        self.assertIn("'ep': 'agro_work.dashboard',     'ru': 'Сверка'", nav)
        self.assertIn("'%.2f ч'", read('templates', 'agro_work', 'applications.html'))
        self.assertIn("'пробег %.1f км'", read('templates', 'agro_work', 'applications.html'))

    def test_the_owner_gate_before_step_one_is_the_check_s_verdicts(self):
        check = read('tools', 'gps_engine_report_check.py')
        flat = ' '.join(text(RELEASE).split())
        for line in ('VERDICT HOURS: MET', 'VERDICT MILEAGE: MET'):
            with self.subTest(line=line):
                self.assertIn('`%s`' % line, flat)
        self.assertIn("'VERDICT HOURS: %s'", check)
        self.assertIn("'VERDICT MILEAGE: %s'", check)
        self.assertLess(flat.index('VERDICT HOURS: MET'), flat.index('## Шаг 2.'))
        self.assertIn('VERDICT HOURS: MET', step('Шаг 1'))

    def test_no_secret_and_no_person_names(self):
        body = text(RELEASE)
        self.assertNotRegex(body, r'password=(?!`)\S')
        self.assertNotIn('wialon_token', body)
        self.assertNotIn('agro_work_credentials', body)


# --- вывод настоящих инструментов ---------------------------------------------

_OUTPUTS = {}


def done_line():
    return ('Done. Table gps_daily_engine with %d columns and %d index is in place.'
            % (len(engine_mig.EXPECTED_COLUMNS), len(engine_mig.INDEXES)))


def real_outputs():
    """Вывод настоящих инструментов на временных базах, один раз на прогон.

    Копия и блокировка -- те же, что у выпуска v1.20; реестр и миграция --
    для GPS_ENGINE_001.
    """
    if _OUTPUTS:
        return _OUTPUTS
    folder = tempfile.mkdtemp()
    try:
        _OUTPUTS.update(_backup_outputs(folder))
        _OUTPUTS.update(_lock_outputs(folder))
        with warnings.catch_warnings():
            # Разбор старых migrate_*.py печатает DeprecationWarning о «\P» в
            # migrate_add_wialon.py -- исторический шум.
            warnings.simplefilter('ignore')
            _OUTPUTS.update(_drift_outputs(folder))
        _OUTPUTS.update(_migration_outputs(folder))
    finally:
        shutil.rmtree(folder, True)
    return _OUTPUTS


def _drift_outputs(folder):
    ids, _ = check_migration_drift.scan_migration_files(REPO_ROOT)
    legacy = set(check_migration_drift.read_backfill_map(REPO_ROOT))
    ours = engine_mig.MIGRATION_ID
    other = sorted(set(ids) - {ours})[0]
    old_root = os.path.join(folder, 'old_tree')
    os.makedirs(old_root)
    for name in os.listdir(REPO_ROOT):
        if name.startswith('migrate_') and name.endswith('.py') and name != MIGRATION:
            shutil.copy(os.path.join(REPO_ROOT, name), old_root)

    def registry(path, names):
        con = sqlite3.connect(path)
        con.execute(migration_utils._CREATE_TABLE_SQL)
        con.executemany('INSERT INTO schema_migrations (name, applied_at) '
                        "VALUES (?, '2026-10-19 12:00:00')",
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
    def new_db(name, ddl):
        path = os.path.join(folder, name)
        con = sqlite3.connect(path)
        con.execute(ddl)
        con.commit()
        con.close()
        return path

    def run(db):
        saved = (engine_mig.DB_PATH, migration_utils.DB_PATH)
        engine_mig.DB_PATH = migration_utils.DB_PATH = db
        out = io.StringIO()
        try:
            with redirect_stdout(out):
                try:
                    engine_mig.run()
                    code = 0
                except SystemExit as exc:
                    code = exc.code
        finally:
            engine_mig.DB_PATH, migration_utils.DB_PATH = saved
        return _entry(code, out.getvalue().splitlines())

    db = new_db('migrate.db', 'CREATE TABLE gps_daily_aggregates (id INTEGER '
                              'PRIMARY KEY, work_date DATE, wialon_id INTEGER)')
    done = run(db)
    again = run(db)
    # Предусловие не выполнено: расчёта GPS в базе нет -- откат, код 1.
    fail = run(new_db('migrate_fail.db', 'CREATE TABLE users (id INTEGER PRIMARY KEY)'))
    return {'migrate_done': done, 'migrate_again': again, 'migrate_fail': fail}


class ReleaseV129ToolFormats(unittest.TestCase):
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
        before = parse_drift(self.out['drift_before']['lines'])
        merged = parse_drift(self.out['drift_merged']['lines'])
        migrated = parse_drift(self.out['drift_migrated']['lines'])
        # До перемотки миграции нет ни в файлах, ни в реестре; после -- файл
        # есть, строки нет; после миграции -- строка есть, реестр на одну
        # больше. Ровно это сверяет блок.
        self.assertNotIn(PENDING_ID, before['pending'])
        self.assertEqual(merged['pending'], sorted(before['pending'] + [PENDING_ID]))
        self.assertEqual(migrated['pending'], before['pending'])
        self.assertEqual(migrated['registered'], before['registered'] + 1)
        extra = parse_drift(self.out['drift_extra_migrated']['lines'])
        self.assertEqual(len(extra['pending']), 1)          # чужая, не наша

    def test_migration_lines(self):
        done, again, fail = (self.out['migrate_done'], self.out['migrate_again'],
                             self.out['migrate_fail'])
        self.assertEqual(done['code'], 0)
        self.assertIn(done_line(), done['lines'])
        self.assertIn(constants(block('Шаг 3'))['doneLine'].strip("'"), done['lines'])
        self.assertEqual(again['code'], 0)
        self.assertEqual(again['lines'], ['Already applied. Nothing to do.'])
        self.assertEqual(fail['code'], 1)
        self.assertNotIn(done_line(), fail['lines'])
        self.assertTrue(all(line.isascii() for line in
                            done['lines'] + again['lines'] + fail['lines']))

    def test_lock_and_gate_lines(self):
        self.assertIn("'no process holds the database'", block('Шаг 3'))
        lines = read('docs', 'RELEASE_GATE.md').splitlines()
        self.assertGreaterEqual(open_items(lines), 0)
        self.assertEqual(open_items(gate_lines(2)), 2)
        for name in ('Шаг 2', 'Шаг 3'):
            self.assertIn("-eq '%s'" % GATE_SEPARATOR, block(name))


POWERSHELL = os.environ.get('GPS_RELEASE_POWERSHELL')
RELEASE_HASH = '5e1ea5e2' + 'c0ffee' * 5 + 'ab'
REVIEWED_HASH = '7e71e3ef' + 'bead00' * 5 + 'cd'
TEST_SERVICES = ['V129TestReport', 'V129TestBot', 'V129TestBot003']
LOGIN_BODY = '<form method="post" class="vs-login-form">'


@unittest.skipUnless(POWERSHELL, 'GPS_RELEASE_POWERSHELL is not set')
class ReleaseV129BlocksInPowerShell(unittest.TestCase):
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

    def outputs(self, **replace):
        """Вывод инструментов; replace -- {ключ сценария: ключ вывода}."""
        out = dict(self.out)
        for key, source in replace.items():
            out[key] = self.out[source]
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
            'DiffNames': ['gps/engine_hours.py', 'gps_collector/main.py', MIGRATION,
                          'docs/GPS_RELEASE_V129_RUNBOOK.md'],
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
            'site': "'http://v129-test.invalid'", 'work': "'%s'" % work,
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
                           'python ' + MIGRATION, 'Restart-Service', 'GET /login',
                           'GET /agro-work/')
        self.assertEqual([c for c in calls if c.startswith('python migrate')],
                         ['python ' + MIGRATION] * 2)
        self.assertEqual(len([c for c in calls if 'check_migration_drift' in c]), 3)
        self.assertEqual(len([c for c in calls if 'check_db_lock' in c]), 2)
        self.assertBackUp(calls)
        registered = parse_drift(self.out['drift_before']['lines'])['registered']
        self.assertIn('MIGRATIONS REGISTERED: %d -> %d' % (registered, registered + 1),
                      output)
        self.assertIn(done_line(), output)
        self.assertIn('Already applied. Nothing to do.', output)
        self.assertIn('BACKUP: ', output)
        self.assertIn('SMOKE /agro-work/: asks to log in - the section is there',
                      output)
        self.assertIn('NEW TRACEBACKS IN logs\\error.log: 0', output)
        self.assertIn('PROGRAM VERSION NOW: %s' % RELEASE_HASH[:7], output)
        self.assertIn('RESULT: RELEASE PASSED', transcript)

    def test_a_stale_wal_is_the_known_noise_not_a_stop(self):
        result, _calls, output, _ = self.run_block(
            'Шаг 3', self.scenario(LockKind='stale'))
        self.assertEqual(result, 'RESULT: RELEASE PASSED', output)

    def test_another_track_pending_migration_is_left_alone(self):
        # На проде две миграции Дронов без строки в реестре (§0.5 трека GPS):
        # они ждут и до выпуска, и после -- выпуск проходит, наша -- применена.
        out = self.outputs(drift_before='drift_extra_before',
                           drift_merged='drift_extra_merged',
                           drift_migrated='drift_extra_migrated')
        result, _calls, output, _ = self.run_block('Шаг 3', self.scenario(Outputs=out))
        self.assertEqual(result, 'RESULT: RELEASE PASSED', output)

    def test_checks_that_fail_change_nothing(self):
        cases = {
            'not run as administrator': dict(Admin=False),
            'not SRV-YOQSH': dict(Host='SOME-PC'),
            'git fetch failed': dict(FetchCode=128),
            'not in main yet': dict(Ancestry=[[BASELINE, RELEASE_HASH]]),
            'changes outside docs/': dict(ChangedAfterReviewed=['docs/a.md',
                                                                'gps/daily.py']),
            'the release gate is closed: 1 open item': dict(GateLines=gate_lines(1)),
            'could not be read': dict(GateLines=[]),
            'already on the server': dict(Head=RELEASE_HASH),
            'this release was prepared for': dict(Head='0' * 40),
            'edited on this server': dict(Modified=[' M app.py']),
            'does not continue': dict(Ancestry=[[REVIEWED_HASH, RELEASE_HASH]]),
            'migrations in the release: migrate_fin_recon_storage_001.py, %s; '
            'expected only %s' % (MIGRATION, MIGRATION): dict(
                DiffNames=['migrate_fin_recon_storage_001.py', MIGRATION]),
            'migrations in the release: ; expected only %s' % MIGRATION: dict(
                DiffNames=['gps/engine_hours.py']),
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
            'did not stop within 90 seconds': dict(StopFails=['V129TestBot']),
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
                self.assertFalse([c for c in calls if c.startswith('python migrate')])
        result, calls, _, _ = self.run_block('Шаг 3',
                                             self.scenario(BackupKind='fail'))
        self.assertFalse([c for c in calls if c.startswith('git merge ')])

    def test_a_migration_that_fails_is_a_stop_on_the_new_version(self):
        result, calls, output, _ = self.run_block(
            'Шаг 3', self.scenario(MigrateFirst='fail'))
        self.assertIn('the migration did not finish (exit 1)', result)
        self.assertBackUp(calls)
        self.assertEqual([c for c in calls if c.startswith('python migrate')],
                         ['python ' + MIGRATION])
        self.assertIn('PROGRAM VERSION NOW: %s' % RELEASE_HASH[:7], output)
        self.assertNotIn('GET /login', calls)

    def test_a_second_run_that_is_not_already_applied_is_a_stop(self):
        result, calls, output, _ = self.run_block(
            'Шаг 3', self.scenario(MigrateSecond='done'))
        self.assertIn('the second run of the migration did not answer that it is '
                      'already applied', result)
        self.assertBackUp(calls)

    def test_an_unexpected_pending_list_after_the_update_is_a_stop(self):
        # После перемотки наша миграция обязана ждать, и только она. Если
        # реестр уже её знает -- кто-то применил её мимо выпуска.
        out = self.outputs(drift_merged='drift_migrated')
        result, calls, output, _ = self.run_block('Шаг 3', self.scenario(Outputs=out))
        self.assertIn('after the update the pending migrations are', result)
        self.assertFalse([c for c in calls if c.startswith('python migrate')])
        self.assertBackUp(calls)
        self.assertIn('PROGRAM VERSION NOW: %s' % RELEASE_HASH[:7], output)

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
        self.assertIn('PROGRAM VERSION NOW: %s' % RELEASE_HASH[:7], output)

    def test_new_tracebacks_after_the_start_are_a_warning(self):
        result, _calls, output, _ = self.run_block('Шаг 3', self.scenario(
            ErrorLogAfterStart='Traceback (most recent call last):\n  boom'))
        self.assertTrue(result.startswith(
            'RESULT: RELEASE PASSED WITH A WARNING - new errors'), output)
        self.assertIn('send the log file before step 4', result)
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
            'expected only %s' % MIGRATION: dict(DiffNames=['migrate_x_001.py']),
            'expected only %s ' % MIGRATION: dict(DiffNames=['gps/daily.py']),
            'drive D:': dict(FreeBytes=10),
            'drive D: not found': dict(FreeBytes=None),
            'not run as administrator': dict(Admin=False),
        }
        for message, change in cases.items():
            with self.subTest(case=message):
                result, calls, output, _ = self.run_block(
                    'Шаг 2', self.scenario(**change))
                self.assertTrue(result.startswith('RESULT: STOP - '), output)
                self.assertIn(message.strip(), result)
                self.assertUntouched(calls)

    # --- откат -------------------------------------------------------------------

    def test_the_rollback_returns_the_production_commit(self):
        result, calls, output, _ = self.run_block('Откат',
                                                  self.scenario(Head=RELEASE_HASH))
        self.assertEqual(result, 'RESULT: ROLLBACK PASSED', output)
        self.assertInOrder(calls, 'Stop-Service', 'git reset --keep ' + BASELINE,
                           'Restart-Service', 'GET /login')
        self.assertBackUp(calls)
        self.assertFalse([c for c in calls if c.startswith('python')])
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


@unittest.skipUnless(POWERSHELL, 'GPS_RELEASE_POWERSHELL is not set')
class ThirdLineInPowerShell(unittest.TestCase):
    """Блок 4.1 против временного gps_daily.bat: добавить, не трогать, отказать."""

    WANT = ['@echo off', 'cd /d C:\\transport-report',
            'C:\\gps_venv\\Scripts\\python.exe -m gps.daily >> gps_daily.log 2>&1',
            'C:\\gps_venv\\Scripts\\python.exe -m gps.daily --catch-up >> gps_daily.log 2>&1']

    def setUp(self):
        self.folder = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.folder, True)
        self.bat = os.path.join(self.folder, 'gps_daily.bat')
        self.copy = os.path.join(self.folder, 'gps_daily_before_v129.bat')
        self.log = os.path.join(self.folder, 'release_v129_step4_bat.log')

    def write_bat(self, lines, newline='\r\n', tail=True):
        with open(self.bat, 'w', encoding='ascii', newline='') as fh:
            fh.write(newline.join(lines) + (newline if tail else ''))

    def bat_lines(self, path=None):
        with open(path or self.bat, encoding='ascii') as fh:
            return [line for line in fh.read().splitlines() if line.strip()]

    def run_bat_block(self):
        body = bat_block()
        # Журнал назван дважды: блок пишет в него и затем показывает.
        for old, new, count in ((BAT_COPY, self.copy, 1), (BAT_LOG, self.log, 2),
                                (BAT, self.bat, 1),
                                ('cd C:\\transport-report\n',
                                 'cd "%s"\n' % self.folder, 1)):
            self.assertEqual(body.count(old), count, old)
            body = body.replace(old, new)
        script = os.path.join(self.folder, 'step41.ps1')
        with open(script, 'w', encoding='ascii') as fh:
            fh.write(body)
        proc = subprocess.run([POWERSHELL, '-NoProfile', '-NonInteractive',
                               '-ExecutionPolicy', 'Bypass', '-File', script],
                              capture_output=True, text=True, timeout=120)
        return proc.stdout + proc.stderr

    def test_the_line_is_added_and_the_old_file_is_kept(self):
        self.write_bat(self.WANT)
        with open(self.bat, 'rb') as fh:
            before = fh.read()
        output = self.run_bat_block()
        self.assertIn('BAT: the third line is added, the old file is saved as', output)
        self.assertEqual(self.bat_lines(), self.WANT + [THIRD_LINE])
        with open(self.copy, 'rb') as fh:
            self.assertEqual(fh.read(), before)
        with open(self.bat, 'rb') as fh:
            self.assertTrue(fh.read().isascii())
        self.assertIn('--- gps_daily.bat now:', output)
        self.assertIn(THIRD_LINE, output)

    def test_a_file_without_the_last_newline_is_still_rewritten_whole(self):
        # [REASON]: дописывание в конец склеило бы новую строку с последней
        # строкой файла без перевода строки; блок пишет файл целиком.
        self.write_bat(self.WANT, tail=False)
        self.run_bat_block()
        self.assertEqual(self.bat_lines(), self.WANT + [THIRD_LINE])

    def test_a_second_run_changes_nothing(self):
        self.write_bat(self.WANT + [THIRD_LINE])
        with open(self.bat, 'rb') as fh:
            before = fh.read()
        output = self.run_bat_block()
        self.assertIn('BAT: the third line is already in place - nothing changed', output)
        with open(self.bat, 'rb') as fh:
            self.assertEqual(fh.read(), before)
        self.assertFalse(os.path.exists(self.copy))

    def test_another_file_is_left_alone(self):
        other = list(self.WANT)
        other[2] = 'C:\\gps_venv\\Scripts\\python.exe -m gps.daily --window-days 7 >> gps_daily.log 2>&1'
        for lines in (other, self.WANT[:3], self.WANT + ['echo done']):
            with self.subTest(lines=lines):
                self.write_bat(lines)
                with open(self.bat, 'rb') as fh:
                    before = fh.read()
                output = self.run_bat_block()
                self.assertIn('BAT: STOP - gps_daily.bat is not as expected - '
                              'nothing changed', output)
                with open(self.bat, 'rb') as fh:
                    self.assertEqual(fh.read(), before)
                self.assertFalse(os.path.exists(self.copy))


if __name__ == '__main__':
    unittest.main()
