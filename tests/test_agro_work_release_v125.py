# -*- coding: utf-8 -*-
"""Выпуск v1.25 на production: docs/AGRO_WORK_RELEASE_V125_RUNBOOK.md.

Выпуск везёт U2 -- объём заявки agro-work против гектаров GPS (решения
сессии по поручению владельца 08.10, раздел 9.2 трека agro-work). Блоки
этого ранбука владелец вставляет в Windows PowerShell 5.1 на боевом
сервере, и шаг 3 останавливает три службы. Это блоки выпуска v1.24 (они же
-- v1.22; прошли на этом сервере 08.10): меняются только постоянные
baseline, reviewed и журнал. Здесь держится:

  * текст: нет плейсхолдеров и `&&`, ASCII без табуляций, git -- только
    проверенные команды, службы останавливаются только в выпуске и откате,
    порядок процедуры, проверка ничего не меняет, постоянные настоящие,
    закрепление верно, в дельте нет миграций, а изменение U2 есть;
  * блоки -- те же, что в выпуске v1.24, кроме трёх постоянных, а их
    функции дословно те же, что в выпуске v1.20;
  * то, что ранбук велит увидеть в шаге 4 -- девять чисел «Итого»,
    карточку и столбец объёма на двух языках, адреса, строки вывода
    отчёта, -- есть в коде;
  * сами блоки исполняются против подставных git, служб, питона и сайта
    (tests/agro_work_release_harness.ps1) во всех путях «стоп»: на
    PowerShell 7 и -- в CI -- на Windows PowerShell 5.1.

Запуск: python -m unittest tests.test_agro_work_release_v125 -v
Блоки в PowerShell: AGRO_WORK_POWERSHELL=pwsh (или powershell) в окружении.
"""

import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import unittest
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
from tests.test_agro_work_release_v124 import RELEASE as V124  # noqa: E402

RELEASE = os.path.join(REPO_ROOT, 'docs',
                       'AGRO_WORK_RELEASE_V125_RUNBOOK.md')
# [REASON]: то же правило, что у выпусков v1.20-v1.24: относительный путь
# скрипта допустим только после перехода в папку программы.
RELATIVE_SCRIPT = re.compile(r'(?<![\\A-Za-z:-])(tools\\|migrate_[A-Za-z0-9_]+\.py'
                             r'|run_server\.py|-m unittest)')
# Production на момент подготовки: строка production в docs/DEPLOYED.md.
BASELINE = '89f358640f235e64eb60b4795a6c7e1ff1c75ed1'
SERVICES = "@('TransportReport', 'TransportBot', 'TransportBot003')"
BLOCKS = ('Шаг 2', 'Шаг 3', 'Откат')


def block(prefix):
    return release_block(prefix, path=RELEASE)


def step(prefix):
    """Текст раздела ранбука, чей заголовок начинается с prefix, в одну строку."""
    found = [body for title, body in sections(RELEASE).items()
             if title.startswith(prefix)]
    assert len(found) == 1, (prefix, len(found))
    return ' '.join(found[0].split())


def read(*parts):
    with open(os.path.join(REPO_ROOT, *parts), encoding='utf-8') as fh:
        return fh.read()


class ReleaseV125Runbook(unittest.TestCase):
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
        # Базу этот выпуск не пишет: блокировка -- один раз, перед копией.
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
        for name in BLOCKS:
            found = constants(block(name))
            with self.subTest(block=name):
                for key, value in found.items():
                    if key in expected:
                        self.assertEqual(value, expected[key], key)
                self.assertEqual(found['baseline'], expected['baseline'])
                self.assertRegex(found['reviewed'], r"^'[0-9a-f]{40}'$")
                self.assertRegex(found['log'],
                                 r"^'C:\\VehicleSoft_Release\\release_v125_[a-z0-9]+\.log'$")
                reviewed.add(found['reviewed'])
                self.assertIn("Where-Object { $_ -notlike 'docs/*' }",
                              block(name))
                if name != 'Откат':
                    self.assertIn('Get-OpenItems @(& git show '
                                  '"${release}:docs/RELEASE_GATE.md")',
                                  block(name))
                    self.assertIn('if ($open -gt 0) { throw', block(name))
        self.assertEqual(len(reviewed), 1)
        logs = [constants(block(name))['log'] for name in BLOCKS]
        self.assertEqual(len(set(logs)), 3)
        # Журналы, которые ранбук велит прислать, -- ровно те, что блоки
        # пишут: каждый назван, и чужой (прежнего выпуска) не назван нигде.
        prose = re.sub(r'```powershell\n.*?```', '', text(RELEASE), flags=re.S)
        named = set(re.findall(r'release_v\d+_[a-z0-9]+\.log', prose))
        self.assertEqual(named, {log.strip("'").split('\\')[-1]
                                 for log in logs})
        for log in logs:
            self.assertIn('`%s`' % log.strip("'"), prose)
        with open(os.path.join(REPO_ROOT, 'backup_production_db.bat'),
                  encoding='ascii') as fh:
            self.assertIn('--source "C:\\transport-report\\instance\\transport.db"',
                          fh.read())
        # Блок готовился от записанного production. После выпуска строка
        # production сменится, а этот коммит останется в журнале релизов.
        self.assertIn('`%s`' % BASELINE[:7], read('docs', 'DEPLOYED.md'))

    def test_the_blocks_are_those_of_v124_but_the_constants(self):
        # Блоки v1.24 (они же -- v1.22) прошли на этом сервере 08.10. Здесь
        # они те же; другие только production, на котором выпуск
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
                                 bare(release_block(name, path=V124)))

    def test_the_helpers_are_word_for_word_those_of_v120(self):
        # Функции блоков прошли на этом сервере в выпусках v1.20-v1.24 (те же
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
                      'docs/AGRO_WORK_RELEASE_V125_RUNBOOK.md').strip()
        if not (written and has_commit(commit) and has_commit(written)):
            self.skipTest('the pinned history is outside this clone')
        ancestor = subprocess.run(['git', 'merge-base', '--is-ancestor', commit,
                                   written], cwd=REPO_ROOT)
        self.assertEqual(ancestor.returncode, 0)
        names = git('diff', '--name-only', commit, written).split()
        self.assertEqual([n for n in names if not n.startswith('docs/')], [])

    def test_the_delta_carries_no_migration_and_the_u2_change(self):
        commit = constants(block('Шаг 3'))['reviewed'].strip("'")
        if not (has_commit(BASELINE) and has_commit(commit)):
            self.skipTest('the release delta is outside this clone')
        names = git('diff', '--name-only', BASELINE, commit).split()
        self.assertEqual([n for n in names if n.startswith('migrate_')], [])
        # U2: ядро и подписи сверки, общий модуль допусков, маршруты,
        # «Свод» и «Заявка -> работа», отчёт.
        for name in ('agro_work/reconcile.py', 'agro_work/labels.py',
                     'gps/tolerance.py', 'gps_routes.py', 'agro_work_routes.py',
                     'tools/agro_work_reconcile.py',
                     'templates/agro_work/dashboard.html',
                     'templates/agro_work/applications.html'):
            self.assertIn(name, names)
        # Общих файлов устава выпуск не трогает.
        for name in ('app.py', 'models.py', 'templates/base_next.html',
                     'static/css/design-system.css', 'migration_utils.py',
                     'excel_export.py'):
            self.assertNotIn(name, names)

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
            self.assertTrue(line.startswith(PYTHON), line)
        self.assertEqual(names, {'tools\\check_migration_drift.py',
                                 'tools\\check_db_lock.py',
                                 'tools\\agro_work_reconcile.py'})
        for name in names:
            path = os.path.join(REPO_ROOT, name.replace('\\', os.sep))
            self.assertTrue(os.path.isfile(path), name)

    def test_the_smoke_markers_are_real(self):
        self.assertIn('class="vs-login-form"', read('templates', 'login.html'))
        # /agro-work/ без входа ведёт на форму входа -- её и ждёт проверка;
        # это держит tests.test_agro_work_screen.AnonymousVisitor.
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
        # Таблица «Если что-то пошло не так» называет остановки словами
        # блоков; строка, которой ни один блок не печатает, владельца
        # запутает.
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

    def test_the_lines_the_owner_waits_for_are_what_the_tool_prints(self):
        tool = read('tools', 'agro_work_reconcile.py')
        flat = step('Шаг 4')
        waited = {
            '`applications: ...`': "'applications: %d | work confirmed %d",
            '`machine-days with GPS work: ...`':
                "'machine-days with GPS work: %d | covered %d | "
                "WITHOUT APPLICATION %d '",
            '`work confirmed, volume vs GPS: ...`':
                "'  work confirmed, volume vs GPS: within tolerance %d | "
                "borderline %d '",
            '`work confirmed`': "work confirmed %d",
            '`cannot compare the volume, why: ...`':
                "'  cannot compare the volume, why'",
        }
        for shown, printed in waited.items():
            with self.subTest(line=shown):
                self.assertIn(shown, flat)
                self.assertIn(printed, tool)
        table = step('Если что-то пошло не так')
        self.assertIn('нет строки `work confirmed, volume vs GPS`', table)

    def test_the_lines_said_to_be_new_were_not_in_production(self):
        # Шаг 4.5: «в выводе прежней версии этих двух строк не было» -- по ним
        # видно, что отчёт запущен из новой версии. Держится, только пока это
        # правда для production, от которого выпуск начинается.
        if not has_commit(BASELINE):
            self.skipTest('production is outside this clone')
        before = git('show', '%s:tools/agro_work_reconcile.py' % BASELINE)
        self.assertIn('work confirmed %d', before)
        for line in ('work confirmed, volume vs GPS',
                     'cannot compare the volume, why'):
            with self.subTest(line=line):
                self.assertNotIn(line, before)
        self.assertIn('В выводе прежней версии этих двух строк не было',
                      step('Шаг 4'))

    def test_the_nine_numbers_are_the_nine_columns_of_the_total_row(self):
        # Шаг 4.1 велит сверять девять чисел «Итого»: столько числовых
        # столбцов у таблицы свода, и порядок -- тот, что назван в шаге 2.
        page = read('templates', 'agro_work', 'dashboard.html')
        head = page[page.index('<thead>'):page.index('</thead>')]
        numeric = re.findall(r'<th class="right">\{\{ \'([^\']+)\'', head)
        self.assertEqual(numeric, ['Машин', 'Заявок', 'Работа была',
                                   'Работы не было', 'Без вердикта',
                                   'Суток с работой', 'Покрыты заявкой',
                                   'Без заявки', 'Без вердикта'])
        self.assertIn('В ней девять чисел по порядку: машин, заявок, работа '
                      'была, работы нет, без вердикта (заявки), суток с '
                      'работой по GPS, покрыто, без заявки, без вердикта '
                      '(сутки).', step('Шаг 2'))

    def test_the_screens_of_step_four_are_real_addresses(self):
        source = read('agro_work_routes.py')
        body = sections(RELEASE)[next(title for title in sections(RELEASE)
                                      if title.startswith('Шаг 4'))]
        addresses = re.findall(r'http://10\.103\.25\.14:5050(/agro-work/[a-z-]*)'
                               r'\?([^`\s]+)', body)
        self.assertEqual(sorted({path for path, _ in addresses}),
                         ['/agro-work/', '/agro-work/applications'])
        from agro_work import reconcile as rc
        # [REASON]: параметры сличаются целиком, а не подстрокой: лишняя
        # буква в значении фильтра даст пустой список, а подстрока её не
        # заметит.
        expected = {
            '/agro-work/': {'from': ['2026-09-01'], 'to': ['2026-09-30']},
            '/agro-work/applications': {'from': ['2026-09-01'],
                                        'to': ['2026-09-30'],
                                        'verdict': [rc.V_WORK]},
        }
        for path, query in addresses:
            with self.subTest(address=path):
                route = '' if path == '/agro-work/' else path[len('/agro-work/'):]
                self.assertIn("@agro_work_bp.route('/%s')" % route, source)
                self.assertEqual(parse_qs(query, strict_parsing=True),
                                 expected[path])
        self.assertIn("verdict = (request.args.get('verdict')", source)
        self.assertIn("volume = (request.args.get('volume')", source)

    def test_the_volume_screens_of_step_four_are_the_screens_of_the_code(self):
        # Подписи, которые шаг 4 велит найти, -- слово в слово подписи
        # шаблонов и словаря; N -- место числа.
        from agro_work import labels, reconcile as rc
        flat = step('Шаг 4')
        dashboard = read('templates', 'agro_work', 'dashboard.html')
        applications = read('templates', 'agro_work', 'applications.html')
        routes = read('agro_work_routes.py')
        self.assertIn("'Работа была: объём против GPS' if is_ru else "
                      "'Иш бўлган: ҳажм GPS га қарши'", dashboard)
        self.assertIn('«Работа была: объём против GPS»', ' '.join(
            step('Шаг 4').split()))
        self.assertIn('«Иш бўлган: ҳажм GPS га қарши»', flat)
        # Строки карточки -- подписи светофора; на экране «подпись: N».
        self.assertIn("{{ volume_label(key) }}: {{ total.get(counter, 0) }}",
                      dashboard)
        for key in rc.VOLUME_VERDICTS:
            ru, uz = labels.VOLUME[key]
            with self.subTest(key=key):
                self.assertIn('«%s: N»' % ru, flat)
                self.assertIn('«%s: N»' % uz, flat)
                self.assertNotRegex(uz, '[A-Za-z]')
        self.assertIn("'Объём против GPS' if is_ru else 'Ҳажм GPS га қарши'",
                      applications)
        self.assertIn("_aw_t('Ҳажм GPS га қарши', 'Объём против GPS')", routes)
        self.assertIn('«Объём против GPS»', flat)
        self.assertIn('«Ҳажм GPS га қарши»', flat)
        self.assertIn('в заявке %.2f га · по GPS %.2f га', applications)
        self.assertIn('«в заявке N га · по GPS N га»', flat)
        for key in (rc.VOL_FAIL, rc.VOL_WARN, rc.VOL_OK, rc.VOL_NONE):
            self.assertIn('«%s»' % labels.VOLUME[key][0], flat)
        # Допуски, названные в «Что произойдёт», -- допуски кода.
        from gps import tolerance
        self.assertEqual((tolerance.TOLERANCE_GA_OK, tolerance.TOLERANCE_GA_WARN),
                         ((0.10, 0.3), (0.20, 0.5)))
        self.assertIn('в допуске — расхождение не больше 10 % или 0,3 га, на '
                      'грани — до 20 % или 0,5 га', step('Что произойдёт'))
        self.assertIn('раздел 9.2 трека agro-work', ' '.join(
            text(RELEASE).split()))
        self.assertIn('### 9.2 U2: объём заявки против гектаров GPS',
                      read('docs', 'tracks', 'agro-work.md'))


class ReleaseV125ToolFormats(unittest.TestCase):
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


POWERSHELL = os.environ.get('AGRO_WORK_POWERSHELL')
RELEASE_HASH = '5e1ea5e1' + 'c0ffee' * 5 + 'ab'
REVIEWED_HASH = '7e71e3ee' + 'bead00' * 5 + 'cd'
TEST_SERVICES = ['V125TestReport', 'V125TestBot', 'V125TestBot003']
LOGIN_BODY = '<form method="post" class="vs-login-form">'


@unittest.skipUnless(POWERSHELL, 'AGRO_WORK_POWERSHELL is not set')
class ReleaseV125BlocksInPowerShell(unittest.TestCase):
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
            'DiffNames': ['agro_work/reconcile.py', 'gps/tolerance.py',
                          'tools/agro_work_reconcile.py',
                          'docs/AGRO_WORK_RELEASE_V125_RUNBOOK.md'],
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
            'site': "'http://v125-test.invalid'", 'work': "'%s'" % work,
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
                                                                'agro_work_routes.py']),
            'the release gate is closed: 1 open item': dict(GateLines=gate_lines(1)),
            'could not be read': dict(GateLines=[]),
            'already on the server': dict(Head=RELEASE_HASH),
            'this release was prepared for': dict(Head='0' * 40),
            'edited on this server': dict(Modified=[' M app.py']),
            'does not continue': dict(Ancestry=[[REVIEWED_HASH, RELEASE_HASH]]),
            'migrations in the release: migrate_drones_x_001.py; this release '
            'has none': dict(DiffNames=['agro_work/reconcile.py',
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
            'did not stop within 90 seconds': dict(StopFails=['V125TestBot']),
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
               '/agro-work/': {'Status': 503, 'Body': ''}}
        result, calls, _output, _ = self.run_block('Шаг 3', self.scenario(Web=web))
        self.assertIn('the login page did not open', result)
        self.assertBackUp(calls)
        self.assertNotIn('GET /agro-work/', calls)

    def test_a_missing_section_after_the_start_is_a_stop(self):
        # Страница входа жива, а раздела сверки нет (404): службы поднялись,
        # но не на том коде. Версия -- новая, службы подняты.
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
