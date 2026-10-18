# -*- coding: utf-8 -*-
"""Выпуск v1.28 на production: docs/GPS_RELEASE_V128_RUNBOOK.md.

Выпуск включает правило территорий (GPS-20, `gps/territory.py`): с даты
`TERRITORY_FROM` ночной расчёт режет участки по зонам списка владельца, и
шаг 4 записывает участкам до 15.10 поле по нынешним контурам (GPS-22,
`tools/gps_reattach_contours.py`). Блоки этого ранбука владелец вставляет в
Windows PowerShell 5.1 на боевом сервере, и шаг 3 останавливает три службы.
Это блоки выпуска v1.27 (они же -- v1.22; прошли на этом сервере 08.10 и
09.10): меняются только постоянные baseline, reviewed и журнал. Здесь
держится:

  * текст: нет плейсхолдеров и `&&`, ASCII без табуляций, git -- только
    проверенные команды, службы останавливаются только в выпуске и откате,
    порядок процедуры, проверка ничего не меняет, постоянные настоящие,
    закрепление верно, в дельте нет миграций и общих файлов устава, а код
    дельты -- ровно трек GPS с 09.10 и правка импорта ведомостей Дронов;
  * блоки -- те же, что в выпуске v1.27, кроме трёх постоянных, а их
    функции дословно те же, что в выпуске v1.20;
  * дата включения правила одна везде: в коде (`TERRITORY_FROM`), в тесте
    движка, в команде проверки территории и в строках, которые ранбук
    велит увидеть; она настоящая дата и позже последнего списка зон;
  * то, что шаг 4 велит увидеть, -- то, что печатают инструменты: строки
    территории из `gps/daily.py` и `gps/territory.py` (две зоны больше 50 га
    и 85 в правиле -- по списку движка), строки GPS-22 и его коды выхода,
    строки копии базы; флаги команд есть у argparse инструментов; `gps.daily`
    запускается только по копии базы, GPS-22 пишет после резервной копии;
  * то, что ранбук называет новым, в production действительно новое;
  * сами блоки исполняются против подставных git, служб, питона и сайта
    (tests/agro_work_release_harness.ps1) во всех путях «стоп»: на
    PowerShell 7 и -- в CI -- на Windows PowerShell 5.1.

Запуск: python -m unittest tests.test_gps_release_v128 -v
Блоки в PowerShell: GPS_RELEASE_POWERSHELL=pwsh (или powershell) в окружении.
"""

import csv
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import unittest
from datetime import datetime
from urllib.parse import parse_qs

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
from tests.test_agro_work_release_v127 import RELEASE as V127  # noqa: E402

RELEASE = os.path.join(REPO_ROOT, 'docs', 'GPS_RELEASE_V128_RUNBOOK.md')
# [REASON]: то же правило, что у выпусков v1.20-v1.27, но дефис перед `tools\`
# не в счёт: `C:\gps-tools\check\...` -- папка выводов, а не запуск скрипта
# по относительному пути (как в тесте выпуска v1.23).
RELATIVE_SCRIPT = re.compile(r'(?<![\\A-Za-z:-])(tools\\|migrate_[A-Za-z0-9_]+\.py'
                             r'|run_server\.py|-m unittest|-m gps\.daily)')
# Production на момент подготовки: строка production в docs/DEPLOYED.md.
BASELINE = '7235c6657bcb8d04d3cc76795b27cab30db0566f'
# [REASON]: окружение расчёта GPS -- отдельный venv без пробела в пути; им
# запускается всё, что тянет numpy/shapely: gps.daily и GPS-22 в шаге 4.
GEO_PYTHON = '& C:\\gps_venv\\Scripts\\python.exe'
SERVICES = "@('TransportReport', 'TransportBot', 'TransportBot003')"
BLOCKS = ('Шаг 2', 'Шаг 3', 'Откат')
LIVE_DB = 'C:\\transport-report\\instance\\transport.db'
CHECK_DB = 'C:\\gps-tools\\check\\v128_check.db'
EMPTY_POINTS = 'C:\\gps-tools\\check\\v128_empty_points'
# Последний список зон владельца: правило мерили по нему (GPS-19 v3), и
# 15.10 -- день записи зеркала, после которого ночной расчёт выбирал поле по
# нынешним контурам.
LAST_LIST_DAY = '2026-10-15'


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


def release_day():
    """TERRITORY_FROM из gps/territory.py -- текстом, без shapely здесь.

    Ровно одна строка `TERRITORY_FROM = 'YYYY-MM-DD'`: `None` (выключено) или
    любая другая запись даты -- не этот выпуск.
    """
    found = re.findall(r"^TERRITORY_FROM = '(\d{4}-\d{2}-\d{2})'$",
                       read('gps', 'territory.py'), re.M)
    assert len(found) == 1, 'TERRITORY_FROM is not a single YYYY-MM-DD: %r' % (found,)
    assert datetime.strptime(found[0], '%Y-%m-%d').strftime('%Y-%m-%d') == found[0]
    return found[0]


def in_code(path, pattern):
    found = re.findall(pattern, read(*path.split('/')), re.M)
    assert len(found) == 1, (path, pattern, found)
    return found[0]


def zone_list():
    """(зоны больше 50 га по возрастанию id, сколько зон в правиле) -- из списка движка."""
    large_ha = float(in_code('gps/territory.py', r'^LARGE_ZONE_HA = ([0-9.]+)$'))
    with open(os.path.join(REPO_ROOT, 'gps', 'data', 'territory_zones.csv'),
              encoding='utf-8-sig', newline='') as fh:
        rows = list(csv.DictReader(fh))
    large = sorted(int(r['zona_id']) for r in rows
                   if r['ploshchad_ga'] and float(r['ploshchad_ga']) > large_ha)
    return large, len(rows) - len(large)


class ReleaseV128Runbook(unittest.TestCase):
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
                             .replace('@{', ''), line)      # @D@, @REVIEWED@, @PR@
            for word in ('YYYY', 'XXX', 'TODO', '...'):
                self.assertNotIn(word, line, line)
        # Метки черновика не остались и в тексте: владелец читает его целиком.
        self.assertNotRegex(text(RELEASE), r'@[A-Z]+@')

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
            'if ($migrations.Count -gt 0)',
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
            'work': "'C:\\VehicleSoft_Release'",
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
                                 r"^'C:\\VehicleSoft_Release\\release_v128_[a-z0-9]+\.log'$")
                reviewed.add(found['reviewed'])
                self.assertIn("Where-Object { $_ -notlike 'docs/*' }",
                              block(name))
                if name != 'Откат':
                    self.assertIn('Get-OpenItems @(& git show '
                                  '"${release}:docs/RELEASE_GATE.md")',
                                  block(name))
                    self.assertIn('if ($open -gt 0) { throw', block(name))
        self.assertEqual(len(reviewed), 1)
        block_logs = {constants(block(name))['log'].strip("'") for name in BLOCKS}
        self.assertEqual(len(block_logs), 3)
        # Журналы шага 4 -- те, в которые его команды пишут через `*>`.
        step_logs = set()
        for line in self.lines():
            step_logs.update(re.findall(
                r'\*>>? (C:\\VehicleSoft_Release\\release_v128_[a-z0-9_]+\.log)', line))
        self.assertEqual(len(step_logs), 5)
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
            self.assertIn('--source "%s"' % LIVE_DB, fh.read())
        # Блок готовился от записанного production. После выпуска строка
        # production сменится, а этот коммит останется в журнале релизов.
        self.assertIn('`%s`' % BASELINE[:7], read('docs', 'DEPLOYED.md'))
        self.assertRegex(text(RELEASE),
                         r'https://github\.com/sINte3/vehicle-soft/pull/\d+')

    def test_the_blocks_are_those_of_v127_but_the_constants(self):
        # Блоки v1.27 (они же -- v1.22) прошли на этом сервере 08.10 и 09.10.
        # Здесь они те же; другие только production, на котором выпуск
        # начинается, проверенный коммит и имя журнала.
        def bare(body):
            found = constants(body)
            for key in ('baseline', 'reviewed', 'log'):
                self.assertEqual(body.count(found[key]), 1, key)
                body = body.replace(found[key], "'@%s@'" % key)
            return body
        for name in BLOCKS:
            with self.subTest(block=name):
                self.assertEqual(bare(block(name)),
                                 bare(release_block(name, path=V127)))

    def test_the_helpers_are_word_for_word_those_of_v120(self):
        # Функции блоков прошли на этом сервере в выпусках v1.20-v1.27 (те же
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
        # Всё, что изменилось после проверенного коммита ДО последней правки
        # ранбука, -- только docs/. Код, влитый позже, ловят шаги 2 и 3.
        # Держится там, где оба коммита есть в клоне (в CI клон глубиной 1).
        commit = constants(block('Шаг 3'))['reviewed'].strip("'")
        written = git('log', '-1', '--format=%H', '--',
                      'docs/GPS_RELEASE_V128_RUNBOOK.md').strip()
        if not (written and has_commit(commit) and has_commit(written)):
            self.skipTest('the pinned history is outside this clone')
        ancestor = subprocess.run(['git', 'merge-base', '--is-ancestor', commit,
                                   written], cwd=REPO_ROOT)
        self.assertEqual(ancestor.returncode, 0)
        names = git('diff', '--name-only', commit, written).split()
        self.assertEqual([n for n in names if not n.startswith('docs/')], [])

    def test_the_delta_is_the_gps_track_and_the_drones_fix(self):
        commit = constants(block('Шаг 3'))['reviewed'].strip("'")
        if not (has_commit(BASELINE) and has_commit(commit)):
            self.skipTest('the release delta is outside this clone')
        names = git('diff', '--name-only', BASELINE, commit).split()
        self.assertEqual([n for n in names if n.startswith('migrate_')], [])
        # [REASON]: общие файлы устава правятся только с объявлением; в этом
        # выпуске их нет, и ранбук этого не объявляет.
        for shared in ('app.py', 'models.py', 'templates/base_next.html',
                       'static/css/design-system.css', 'migration_utils.py',
                       'excel_export.py'):
            self.assertNotIn(shared, names)
        # [REASON]: из кода программы меняется ровно этот список -- трек GPS
        # с 09.10 (движок, ночной расчёт, правило территорий, ручные
        # инструменты, экраны сверки и «Факт по технике») и правка импорта
        # ведомостей Дронов (PR #197, squash). Шаг 4 и шаг 5 смотрят правило
        # и GPS-22; чужой код в дельте сделал бы их недостаточной проверкой.
        code = sorted(n for n in names if not n.startswith(('docs/', 'tests/')))
        self.assertEqual(code, [
            '.github/workflows/checks.yml',
            'agro_work_routes.py',
            'gps/README.md', 'gps/area.py', 'gps/daily.py',
            'gps/data/territory_zones.csv', 'gps/edge.py', 'gps/edge_scale.py',
            'gps/multipass.py', 'gps/territory.py',
            'gps/tests/fixtures/nh_80080ha_20261001_work.csv',
            'gps/tests/test_edge.py', 'gps/tests/test_edge_replay.py',
            'gps/tests/test_edge_scale.py', 'gps/tests/test_edge_scale_replay.py',
            'gps/tests/test_multipass.py', 'gps/tests/test_multipass_scan.py',
            'gps/tests/test_reattach_contours.py',
            'gps/tests/test_recompute_days.py',
            'gps/tests/test_territory_report.py',
            'gps/tests/test_territory_rule.py',
            'gps_routes.py', 'static/js/vs-combobox.js',
            'templates/agro_work/applications.html',
            'templates/agro_work/dashboard.html',
            'templates/agro_work/machine.html', 'templates/gps/fact.html',
            'tools/gps_acceptance_compare.py', 'tools/gps_alpha_replay.py',
            'tools/gps_alpha_report.py', 'tools/gps_bench_export.py',
            'tools/gps_day_kml.py', 'tools/gps_edge_replay.py',
            'tools/gps_edge_scale_replay.py', 'tools/gps_implausible_sites.py',
            'tools/gps_link_by_list.py', 'tools/gps_mark_not_ours.py',
            'tools/gps_mirror_diff.py', 'tools/gps_multipass_scan.py',
            'tools/gps_reattach_contours.py', 'tools/gps_recompute_days.py',
            'tools/gps_territory_report.py', 'tools/gps_track_only_days.py',
            'tools/gps_units_inventory.py', 'tools/gps_zone_groups.py',
            'tools/import_drone_works.py', 'tools/test_import_drone_works.py',
            'tools/test_import_drone_works_fin_recon_h5.py',
            'tools/ux/check_gps_fact_picker.mjs'])
        self.assertIn('tests/test_gps_release_v128.py', names)
        # Правка Дронов влита squash: в дельте она одним коммитом без мержа.
        subjects = git('log', '--format=%s', '%s..%s' % (BASELINE, commit))
        self.assertIn('fix(drone-works): preserve jobs and block unsafe '
                      'historical reimports', subjects)

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
        # В список попали только PR дельты и PR выпуска (следующий за ними):
        # чужого номера, которого в дельте нет, шаг 2 не обещает.
        release_pr = int(re.search(r'/pull/(\d+)', text(RELEASE)).group(1))
        self.assertEqual(named - merged - {197}, {release_pr})
        self.assertNotIn(197, merged)                      # squash -- без мержа
        self.assertIn('`#197`', body)
        self.assertIn('squash', body)

    def test_the_start_date_is_the_release_day_everywhere(self):
        day = release_day()
        # [REASON]: правило мерили по списку 15.10 (GPS-19 v3) и включают
        # выпуском после него: дата раньше списка пересчитала бы с правилом
        # сутки, которые владелец уже видел (gps/territory.py, [REASON] у
        # TERRITORY_FROM).
        self.assertGreater(day, LAST_LIST_DAY)
        engine_test = read('gps', 'tests', 'test_territory_rule.py')
        self.assertIn('RELEASE_DAY = "%s"' % day, engine_test)
        self.assertNotIn('def test_the_default_is_off', engine_test)
        self.assertIn('territory.TERRITORY_FROM, RELEASE_DAY', engine_test)
        # Команда проверки территории -- за этот день, по копии, с пустой
        # папкой точек; строки, которые ранбук велит увидеть, -- с этим днём.
        daily = [line for line in self.lines() if '-m gps.daily' in line]
        self.assertEqual(len(daily), 1)
        self.assertIn(' --date %s --unit 393 --db %s --dir %s ' % (day, CHECK_DB, EMPTY_POINTS),
                      daily[0])
        self.assertTrue(daily[0].startswith(GEO_PYTHON + ' -m gps.daily '))
        self.assertNotIn(LIVE_DB, daily[0])
        self.assertNotIn(LIVE_DB, step('Шаг 4').split('**4.3.')[0].split('**4.1.')[1]
                         .replace('--from %s' % LIVE_DB, ''))
        label = in_code('gps/area.py', r'^TERRITORY_RULE_VERSION = "([^"]+)"$')
        self.assertEqual(text(RELEASE).count('(from %s): 85 zone(s)' % day), 2)
        self.assertIn('`territory rule %s (from %s): 85 zone(s)`' % (label, day),
                      text(RELEASE))
        self.assertIn('`%s: 1 object(s), ... contour(s) in the directory`' % day,
                      text(RELEASE))
        human = datetime.strptime(day, '%Y-%m-%d').strftime('%d.%m.%Y')
        self.assertGreaterEqual(text(RELEASE).count(human), 8)
        self.assertIn('http://10.103.25.14:5050/gps/fact?date=%s' % day,
                      text(RELEASE))
        self.assertIn('Дата включения правила записана в код — %s.' % human,
                      text(RELEASE))

    def test_what_the_runbook_calls_new_is_new(self):
        # Ранбук говорит: правила территорий и GPS-22 на production нет, а в
        # проверенном коммите правило включено этой датой и расчёт печатает
        # строки, которые шаги 4 и 5 велят увидеть.
        commit = constants(block('Шаг 3'))['reviewed'].strip("'")
        if not (has_commit(BASELINE) and has_commit(commit)):
            self.skipTest('the release delta is outside this clone')
        for path in ('gps/territory.py', 'tools/gps_reattach_contours.py'):
            with self.subTest(path=path):
                absent = subprocess.run(['git', 'cat-file', '-e',
                                         '%s:%s' % (BASELINE, path)],
                                        cwd=REPO_ROOT, capture_output=True)
                self.assertNotEqual(absent.returncode, 0, path)
        self.assertNotIn('territory', git('show', '%s:gps/daily.py' % BASELINE))
        self.assertNotIn('TERRITORY_RULE_VERSION',
                         git('show', '%s:gps/area.py' % BASELINE))
        day = release_day()
        self.assertIn("\nTERRITORY_FROM = '%s'\n" % day,
                      git('show', '%s:gps/territory.py' % commit))
        self.assertIn('"territory rule %s (from %s): %d zone(s)"',
                      git('show', '%s:gps/daily.py' % commit))
        self.assertEqual(read('gps', 'territory.py'),
                         git('show', '%s:gps/territory.py' % commit))

    def test_step_four_waits_for_what_the_tools_print(self):
        tool = read('tools', 'gps_reattach_contours.py')
        body = step('Шаг 4')
        for printed in ('DRY RUN - nothing was written to the database (opened mode=ro)',
                        'changes that --apply would make: ',
                        'sites scanned', 'same field', 'another field',
                        'gets a field', 'loses its field',
                        'of the changes, sites with an operator answer'):
            with self.subTest(printed=printed):
                self.assertIn(printed, tool)
                self.assertIn(printed.rstrip(), body)
        # Отказ записи печатается как `FAILED: <причина>`; таблица «Если
        # что-то пошло не так» называет его так же.
        self.assertIn('field_contours changed since the plan was made', tool)
        self.assertIn("print('FAILED: %s' % problem)", tool)
        self.assertIn('`FAILED: field_contours changed since the plan was made`',
                      text(RELEASE))
        self.assertIn("'written: %d site(s) got their field again, one transaction; '\n"
                      "          'skipped: %d'", tool)
        self.assertIn('`written: ... site(s) got their field again, one transaction; skipped: 0`',
                      body)
        self.assertIn("print('report : %s' % args.out)", tool)
        self.assertIn('`report : C:\\gps-tools\\check\\reattach_v128_apply.csv`', body)
        self.assertIn('EXIT_OK, EXIT_FAILED, EXIT_BAD_INPUT = 0, 1, 2', tool)
        self.assertIn('EXIT_SKIPPED, EXIT_BUSY, EXIT_REPORT_FAILED = 3, 4, 5', tool)
        self.assertIn('BUSY_TIMEOUT_S = 30', tool)
        for code in range(6):
            self.assertIn('`%d`' % code if code else '`exit 0`', body)
        # Строки территории -- из расчёта и правила; зоны и число -- из
        # списка движка, а не из головы.
        daily = read('gps', 'daily.py')
        territory = read('gps', 'territory.py')
        self.assertIn('"territory rule %s (from %s): %d zone(s)"', daily)
        self.assertIn("'  territory: %d zone(s) left out of the rule: %s'", territory)
        self.assertIn("'larger than %g ha' % LARGE_ZONE_HA", territory)
        self.assertEqual(in_code('gps/territory.py', r'^LARGE_ZONE_HA = ([0-9.]+)$'),
                         '50.0')
        large, in_rule = zone_list()
        self.assertEqual(large, [7603, 18227])
        self.assertEqual(in_rule, 85)
        left_out = ('`territory: 2 zone(s) left out of the rule: 7603 (larger than 50 ha), '
                    '18227 (larger than 50 ha)`')
        self.assertEqual(text(RELEASE).count(left_out), 2)      # шаги 4 и 5
        self.assertIn('REASON_NO_POINTS = "net_tochek"', daily)
        self.assertIn('print("  %-8s %s" % (unit_id, result.reason))', daily)
        self.assertIn('`393      net_tochek`', raw_step('Шаг 4'))   # %-8s
        self.assertIn('"\\npublished: %d | not computed: %d | sites: %d"', daily)
        self.assertIn('`published: 0 | not computed: 1 | sites: 0`', body)
        self.assertIn('"ERROR: territory rule: %s -- nothing was "', daily)
        self.assertIn('`ERROR: territory rule`', body)
        # Копия базы -- инструментом agro-work; его строки и отказы настоящие.
        copy_tool = read('tools', 'agro_work_copy_db.py')
        self.assertIn("print('copied %d bytes to %s'", copy_tool)
        self.assertIn("print('integrity of the copy: %s' % check)", copy_tool)
        self.assertIn("FORBIDDEN_FOLDER = 'transport-report'", copy_tool)
        self.assertIn('`integrity of the copy: ok`', body)
        self.assertIn('`ERROR: target is inside a transport-report folder`',
                      text(RELEASE))

    def test_step_four_flags_exist_and_the_order_is_safe(self):
        # Каждый флаг команд шага 4 есть у argparse инструмента: флаг,
        # которого нет, остановит владельца сообщением argparse.
        tool = read('tools', 'gps_reattach_contours.py')
        copy_tool = read('tools', 'agro_work_copy_db.py')
        daily = read('gps', 'daily.py')
        lines = commands(raw_step('Шаг 4'))
        reattach = [line for line in lines if 'gps_reattach_contours.py' in line]
        self.assertEqual(len(reattach), 4)
        for line in reattach:
            with self.subTest(line=line):
                self.assertTrue(line.startswith(GEO_PYTHON + ' tools\\gps_reattach_contours.py --db %s ' % LIVE_DB), line)
                for flag in re.findall(r' (--[a-z-]+)', line):
                    self.assertIn("parser.add_argument('%s'" % flag, tool)
                self.assertRegex(line, r' --out C:\\gps-tools\\check\\reattach_v128_[a-z]+\.csv ')
        copies = [line for line in lines if 'agro_work_copy_db.py' in line]
        self.assertEqual(len(copies), 1)
        self.assertTrue(copies[0].startswith(PYTHON + ' tools\\agro_work_copy_db.py '))
        self.assertIn(' --from %s --to %s --replace' % (LIVE_DB, CHECK_DB), copies[0])
        for flag in ('--from', '--to', '--replace'):
            self.assertIn("parser.add_argument('%s'" % flag, copy_tool)
        daily_line = next(line for line in lines if '-m gps.daily' in line)
        for flag in re.findall(r' (--[a-z-]+)', daily_line):
            self.assertIn('parser.add_argument("%s"' % flag, daily)
        self.assertIn('if __name__ == "__main__":', daily)
        # Порядок: копия -> территория по копии -> сухой прогон -> проверка
        # с 15.10 -> резервная копия -> запись -> повторный сухой прогон.
        # Пишущая команда -- одна, с --apply, после резервной копии.
        def first(predicate):
            return next(i for i, line in enumerate(lines) if predicate(line))
        order = [
            first(lambda l: 'agro_work_copy_db.py' in l),
            first(lambda l: '-m gps.daily' in l),
            first(lambda l: 'gps_reattach_contours.py' in l
                  and '--since' not in l and '--apply' not in l),
            first(lambda l: '--since %s ' % LAST_LIST_DAY in l),
            first(lambda l: l.strip() == '& C:\\transport-report\\backup_production_db.bat'),
            first(lambda l: '--apply' in l),
        ]
        self.assertEqual(order, sorted(order))
        self.assertEqual(sum('--apply' in line for line in lines), 1)
        after = [i for i, line in enumerate(lines)
                 if 'gps_reattach_contours.py' in line and i > order[-1]]
        self.assertEqual(len(after), 1)
        self.assertNotIn('--apply', lines[after[0]])
        self.assertNotIn('--since', lines[after[0]])
        # Пустая папка точек создаётся до расчёта, и это не папка точек прода.
        made = first(lambda l: l.startswith('New-Item -ItemType Directory -Force -Path %s' % EMPTY_POINTS))
        self.assertLess(made, order[1])
        self.assertNotIn('instance', EMPTY_POINTS)
        self.assertNotIn('gps_mirror_contours', ' '.join(lines))

    def test_step_five_names_the_method_version_of_the_code(self):
        method = in_code('gps/area.py', r'^METHOD_VERSION = "([^"]+)"$')
        label = in_code('gps/area.py', r'^TERRITORY_RULE_VERSION = "([^"]+)"$')
        self.assertEqual(label, 'territory-2026-10-15_v3')
        self.assertEqual(text(RELEASE).count('%s+%s' % (method, label)), 2)
        self.assertIn('Версия метода: ', read('templates', 'gps', 'fact.html'))
        self.assertIn('aggregate.method_version', read('templates', 'gps', 'fact.html'))
        # Метка -- у суток, дошедших до поиска участков; у суток с причиной
        # её нет (gps/territory.py, докстринг). Ранбук говорит то же.
        self.assertIn('у суток с причиной', step('Шаг 5').lower()
                      .replace('у суток с причиной (нет', 'у суток с причиной (нет'))
        # Журнал ночного расчёта -- тот, в который пишет gps_daily.bat.
        rollout = read('docs', 'GPS_ROLLOUT_RUNBOOK.md')
        self.assertIn('-m gps.daily >> gps_daily.log 2>&1', rollout)
        self.assertIn('-m gps.daily --catch-up >> gps_daily.log 2>&1', rollout)
        self.assertIn("Select-String -Path C:\\transport-report\\gps_daily.log",
                      step('Шаг 5'))
        self.assertIn('schtasks /create /tn "GpsDaily" /tr '
                      '"C:\\transport-report\\gps_daily.bat" /sc daily /st 02:30', rollout)
        self.assertIn('02:30', text(RELEASE))

    def test_the_rule_the_runbook_describes_is_the_rule_in_code(self):
        territory = read('gps', 'territory.py')
        self.assertEqual(in_code('gps/territory.py', r'^DROP_SHARE = ([0-9.]+)$'), '0.5')
        self.assertIn('MIN_WORK_AREA_HA = 0.3', read('gps', 'area.py'))
        flat = ' '.join(text(RELEASE).split())
        self.assertIn('наполовину и больше, не гектары целиком', flat)
        self.assertIn('остатки меньше 0,3 га', flat)
        self.assertIn('85 зонам', flat)
        self.assertIn('Снятого участка на экране нет', flat)
        # Сверка agro-work -- решение владельца 18.10, код сверки не менялся.
        self.assertIn('«работы не было»', flat)
        self.assertIn('18.10', flat)
        reconcile = read('agro_work', 'reconcile.py')
        self.assertIn("GPS_REASON_NO_MOTION = 'net_dvizheniya'", reconcile)
        self.assertIn('def day_state(aggregate, sites):', reconcile)
        self.assertIn('ZONES_PATH', territory)

    def test_every_step_that_runs_a_script_first_goes_to_production(self):
        found = steps(RELEASE)
        self.assertGreaterEqual(len(found), 5)
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
        # Шаг 4 состоит из нескольких блоков по две-три команды: каждый
        # начинается с перехода в папку программы, потому что владелец
        # вставляет их по одному, иногда в новое окно.
        step4 = [body for title, body in found.items() if title.startswith('Шаг 4')][0]
        for part in re.findall(r'```powershell\n(.*?)```', step4, re.S):
            with self.subTest(part=part[:60]):
                if RELATIVE_SCRIPT.search(part):
                    self.assertTrue(part.startswith('cd C:\\transport-report\n'), part)
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
            if 'gps_reattach_contours' in line or '-m gps.daily' in line:
                self.assertTrue(line.startswith(GEO_PYTHON), line)   # геометрия
            else:
                self.assertTrue(line.startswith(PYTHON), line)
        self.assertEqual(names, {'tools\\check_migration_drift.py',
                                 'tools\\check_db_lock.py',
                                 'tools\\agro_work_copy_db.py',
                                 'tools\\gps_reattach_contours.py'})
        for name in names:
            path = os.path.join(REPO_ROOT, name.replace('\\', os.sep))
            self.assertTrue(os.path.isfile(path), name)
        self.assertTrue(os.path.isfile(os.path.join(REPO_ROOT, 'gps', 'daily.py')))

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
        self.assertGreaterEqual(len(table), 10)
        blocks = ''.join(block(name) for name in BLOCKS)
        for line in table:
            shown = re.match(r'\| `RESULT: STOP - ([^`]*)`', line).group(1)
            words = shown.split('...')[0].strip()
            with self.subTest(stop=words):
                self.assertTrue(words)
                self.assertIn(words, blocks)

    def test_the_screen_addresses_are_real(self):
        source = read('gps_routes.py')
        addresses = re.findall(r'http://10\.103\.25\.14:5050(/gps/[a-z-]*)'
                               r'\?([^`\s]+)', text(RELEASE))
        self.assertEqual(sorted({path for path, _ in addresses}), ['/gps/fact'])
        dates = set()
        for path, query in addresses:
            with self.subTest(address=query):
                parsed = parse_qs(query, strict_parsing=True)
                self.assertEqual(set(parsed), {'date'})
                dates.add(parsed['date'][0])
        self.assertEqual(dates, {'2026-10-14', release_day()})
        self.assertIn("gps_bp = Blueprint('gps', __name__, url_prefix='/gps')",
                      source)
        route = source.index("@gps_bp.route('/fact')\n")
        self.assertIn('@login_required', source[route:route + 120])
        self.assertIn("_parse_date(request.args.get('date'))", source)

    def test_no_secret_and_no_person_names(self):
        body = text(RELEASE)
        self.assertNotRegex(body, r'password=(?!`)\S')
        self.assertNotIn('wialon_token', body)
        self.assertNotIn('agro_work_credentials', body)


class ReleaseV128ToolFormats(unittest.TestCase):
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
RELEASE_HASH = '5e1ea5e2' + 'c0ffee' * 5 + 'ab'
REVIEWED_HASH = '7e71e3ef' + 'bead00' * 5 + 'cd'
TEST_SERVICES = ['V128TestReport', 'V128TestBot', 'V128TestBot003']
LOGIN_BODY = '<form method="post" class="vs-login-form">'


@unittest.skipUnless(POWERSHELL, 'GPS_RELEASE_POWERSHELL is not set')
class ReleaseV128BlocksInPowerShell(unittest.TestCase):
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
            'DiffNames': ['gps/territory.py', 'gps/daily.py',
                          'tools/gps_reattach_contours.py',
                          'docs/GPS_RELEASE_V128_RUNBOOK.md'],
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
            'site': "'http://v128-test.invalid'", 'work': "'%s'" % work,
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
                           'Restart-Service', 'GET /login', 'GET /agro-work/')
        self.assertFalse([c for c in calls if c.startswith('python migrate')])
        self.assertEqual(len([c for c in calls if 'check_migration_drift' in c]), 2)
        self.assertEqual(len([c for c in calls if 'check_db_lock' in c]), 1)
        self.assertBackUp(calls)
        self.assertRegex(output, r'MIGRATIONS REGISTERED: (\d+) -> \1, unchanged')
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
        # На проде две миграции Дронов без строки в реестре (§0.5 трека):
        # список до перемотки и после один и тот же -- выпуск проходит.
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
                                                                'gps/daily.py']),
            'the release gate is closed: 1 open item': dict(GateLines=gate_lines(1)),
            'could not be read': dict(GateLines=[]),
            'already on the server': dict(Head=RELEASE_HASH),
            'this release was prepared for': dict(Head='0' * 40),
            'edited on this server': dict(Modified=[' M app.py']),
            'does not continue': dict(Ancestry=[[REVIEWED_HASH, RELEASE_HASH]]),
            'migrations in the release: migrate_fin_recon_storage_001.py; this '
            'release has none': dict(DiffNames=['gps/territory.py',
                                                'migrate_fin_recon_storage_001.py']),
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
            'did not stop within 90 seconds': dict(StopFails=['V128TestBot']),
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
