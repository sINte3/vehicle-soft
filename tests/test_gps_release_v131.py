# -*- coding: utf-8 -*-
"""Выпуск v1.31 на production: docs/GPS_RELEASE_V131_RUNBOOK.md.

Выпуск -- граница организаций на экранах GPS (`gps_routes._org_scope`,
решение владельца 22.10: не-админ видит только машины своих организаций).
Код правила K1 (`gps/multipass.py`) и инструмента его приёмки едут вместе,
как и инструмент проверки пробега грузовиков (GPS-32), но программа и
ночной расчёт их не вызывают. Без миграции. Блоки ранбука
владелец вставляет в Windows PowerShell 5.1 на боевом сервере, и шаг 3
останавливает три службы. Это блоки выпуска v1.30 (они же -- v1.28, v1.27
и v1.22; прошли на этом сервере 08.10, 09.10, 19.10 и 22.10): меняются
только постоянные baseline, reviewed и журнал. Здесь держится:

  * текст: нет плейсхолдеров и `&&`, ASCII без табуляций, git -- только
    проверенные команды, службы останавливаются только в выпуске и откате,
    порядок процедуры, проверка ничего не меняет, постоянные настоящие,
    закрепление верно, в дельте нет миграций и общих файлов устава, а код
    дельты -- ровно граница организаций, правило K1 и инструмент GPS-32,
    которые программа не вызывает;
  * блоки -- те же, что в выпуске v1.30, кроме трёх постоянных, а их
    функции дословно те же, что в выпуске v1.20;
  * то, что ранбук называет новым, в production действительно новое;
  * то, что шаг 4 велит увидеть, -- подписи шаблонов и поведение кода:
    список только своих машин, чужая машина по адресу не открывается,
    предупреждение «не найдено», администратор без границы;
  * сами блоки исполняются против подставных git, служб, питона и сайта
    (tests/agro_work_release_harness.ps1) во всех путях «стоп»: на
    PowerShell 7 и -- в CI -- на Windows PowerShell 5.1.

Запуск: python -m unittest tests.test_gps_release_v131 -v
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
from tests.test_gps_release_v130 import RELEASE as V130      # noqa: E402

RELEASE = os.path.join(REPO_ROOT, 'docs', 'GPS_RELEASE_V131_RUNBOOK.md')
# [REASON]: то же правило, что у выпусков v1.20-v1.30: относительный путь
# скрипта допустим только после перехода в папку программы.
RELATIVE_SCRIPT = re.compile(r'(?<![\\A-Za-z:-])(tools\\|migrate_[A-Za-z0-9_]+\.py'
                             r'|run_server\.py|-m unittest)')
# Production на момент подготовки: строка production в docs/DEPLOYED.md.
BASELINE = 'a86b08d73436a3234fc856d54d7c209915e4a297'
SERVICES = "@('TransportReport', 'TransportBot', 'TransportBot003')"
BLOCKS = ('Шаг 2', 'Шаг 3', 'Откат')
LIVE_DB = 'C:\\transport-report\\instance\\transport.db'
# Реестр миграций production после выпуска v1.30 (docs/DEPLOYED.md).
REGISTERED = 61


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


def reviewed():
    return constants(block('Шаг 3'))['reviewed'].strip("'")


class ReleaseV131Runbook(unittest.TestCase):
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
        self.assertNotRegex(text(RELEASE), r'@[A-Z0-9_]+@')

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
        pinned = set()
        for name in BLOCKS:
            found = constants(block(name))
            with self.subTest(block=name):
                for key, value in found.items():
                    if key in expected:
                        self.assertEqual(value, expected[key], key)
                self.assertEqual(found['baseline'], expected['baseline'])
                self.assertRegex(found['reviewed'], r"^'[0-9a-f]{40}'$")
                self.assertRegex(found['log'],
                                 r"^'C:\\VehicleSoft_Release\\release_v131_[a-z0-9]+\.log'$")
                pinned.add(found['reviewed'])
                self.assertIn("Where-Object { $_ -notlike 'docs/*' }",
                              block(name))
                if name != 'Откат':
                    self.assertIn('Get-OpenItems @(& git show '
                                  '"${release}:docs/RELEASE_GATE.md")',
                                  block(name))
                    self.assertIn('if ($open -gt 0) { throw', block(name))
        self.assertEqual(len(pinned), 1)
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
        self.assertIn('`%s`' % BASELINE[:7], read('docs', 'DEPLOYED.md'))
        self.assertRegex(text(RELEASE),
                         r'https://github\.com/sINte3/vehicle-soft/pull/\d+')

    def test_the_blocks_are_those_of_v130_but_the_constants(self):
        # Блоки v1.30 (они же -- v1.28, v1.27 и v1.22) прошли на этом сервере
        # 08.10, 09.10, 19.10 и 22.10. Здесь они те же; другие только
        # production, на котором выпуск начинается, проверенный коммит и имя
        # журнала.
        def bare(body):
            found = constants(body)
            for key in ('baseline', 'reviewed', 'log'):
                self.assertEqual(body.count(found[key]), 1, key)
                body = body.replace(found[key], "'@%s@'" % key)
            return body
        for name in BLOCKS:
            with self.subTest(block=name):
                self.assertEqual(bare(block(name)),
                                 bare(release_block(name, path=V130)))

    def test_the_helpers_are_word_for_word_those_of_v120(self):
        # Функции блоков прошли на этом сервере в выпусках v1.20-v1.30 (те же
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
        commit = reviewed()
        written = git('log', '-1', '--format=%H', '--',
                      'docs/GPS_RELEASE_V131_RUNBOOK.md').strip()
        if not (written and has_commit(commit) and has_commit(written)):
            self.skipTest('the pinned history is outside this clone')
        ancestor = subprocess.run(['git', 'merge-base', '--is-ancestor', commit,
                                   written], cwd=REPO_ROOT)
        self.assertEqual(ancestor.returncode, 0)
        names = git('diff', '--name-only', commit, written).split()
        self.assertEqual([n for n in names if not n.startswith('docs/')], [])

    def test_the_delta_is_the_org_scope_and_the_k1_rule(self):
        commit = reviewed()
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
        # [REASON]: из кода программы меняется ровно этот список -- граница
        # организаций (PR #225), правило K1 с инструментом приёмки (PR #226) и
        # инструмент GPS-32 (PR #227), которые программа не вызывает (ниже). Шаг 4 смотрит границу; чужой
        # код в дельте сделал бы шаг 4 недостаточной проверкой.
        code = sorted(n for n in names if not n.startswith(('docs/', 'tests/')))
        self.assertEqual(code, [
            '.github/workflows/checks.yml',
            'gps/multipass.py',
            'gps/tests/test_multipass_acceptance.py',
            'gps/tests/test_multipass_k1.py',
            'gps_routes.py',
            'tools/gps_mileage_report_check.py',
            'tools/gps_multipass_acceptance.py'])
        self.assertIn('tests/test_gps_release_v131.py', names)
        self.assertIn('tests/test_gps_org_scope.py', names)
        self.assertIn('tests/test_gps_mileage_report_check.py', names)

    def test_the_k1_rule_rides_along_without_being_called(self):
        # «Что произойдёт»: код правила кратности приедет, но программа и
        # ночной расчёт его не вызывают. Держится, пока gps.multipass
        # импортируют только инструменты tools/ и его собственные тесты.
        commit = reviewed()
        if not has_commit(commit):
            self.skipTest('the pinned commit is outside this clone')
        users = git('grep', '-l', '-w', 'multipass', commit, '--', '*.py').split()
        users = sorted(name.split(':', 1)[1] for name in users)
        self.assertEqual([name for name in users if not name.startswith(
            ('tools/', 'gps/tests/', 'tests/'))], ['gps/multipass.py'])
        flat = ' '.join(text(RELEASE).split())
        self.assertIn('Код правила кратности прохода (K1) и инструмент его '
                      'проверки тоже приедут, но программа и ночной расчёт их '
                      'не вызывают', flat)

    def test_the_mileage_check_rides_along_without_being_called(self):
        # «Что произойдёт»: инструмент GPS-32 приедет, но программа его не
        # вызывает. Держится, пока вне tests/ его имя встречается только в нём
        # самом.
        commit = reviewed()
        if not has_commit(commit):
            self.skipTest('the pinned commit is outside this clone')
        users = git('grep', '-l', '-w', 'gps_mileage_report_check', commit,
                    '--', '*.py').split()
        users = sorted(name.split(':', 1)[1] for name in users)
        self.assertEqual([name for name in users if not name.startswith('tests/')],
                         ['tools/gps_mileage_report_check.py'])
        self.assertIn('tests/test_gps_mileage_report_check.py', users)
        flat = ' '.join(text(RELEASE).split())
        self.assertIn('Инструмент проверки пробега грузовиков (GPS-32) тоже '
                      'приедет; его запускают вручную, программа его не '
                      'вызывает', flat)

    def test_the_merged_prs_named_in_step_two_are_the_delta(self):
        # Шаг 2 говорит, какие «Merge pull request» владелец увидит. Список
        # сверяется с историей от production до проверенного коммита (PR
        # ранбука вливается позже и назван отдельно).
        commit = reviewed()
        if not (has_commit(BASELINE) and has_commit(commit)):
            self.skipTest('the release delta is outside this clone')
        merged = {int(n) for n in re.findall(
            r'^Merge pull request #(\d+)', git('log', '--merges', '--format=%s',
                                                '%s..%s' % (BASELINE, commit)), re.M)}
        self.assertEqual(merged, {224, 225, 226, 227})
        body = step('Шаг 2')
        named = {int(n) for n in re.findall(r'`#(\d+)`', body)}
        release_pr = int(re.search(r'/pull/(\d+)', text(RELEASE)).group(1))
        self.assertGreater(release_pr, 227)
        self.assertEqual(named, merged | {release_pr})

    def test_what_the_runbook_calls_new_is_new(self):
        # Ранбук говорит: в v1.30 границы организаций нет, выпуск её приносит.
        # Держится, только пока это правда для production, от которого выпуск
        # начинается, и для проверенного коммита.
        commit = reviewed()
        if not (has_commit(BASELINE) and has_commit(commit)):
            self.skipTest('the release delta is outside this clone')
        for new in ('def _org_scope():', 'def _units_in_scope(scope):',
                    'if allowed is not None and site.wialon_id not in allowed:'):
            with self.subTest(new=new):
                self.assertNotIn(new, git('show', '%s:gps_routes.py' % BASELINE))
                self.assertIn(new, git('show', '%s:gps_routes.py' % commit))
        absent = subprocess.run(['git', 'cat-file', '-e',
                                 '%s:tests/test_gps_org_scope.py' % BASELINE],
                                cwd=REPO_ROOT, capture_output=True)
        self.assertNotEqual(absent.returncode, 0)
        # Проверки, которые держат новое, -- в наборе проверенного коммита.
        for test in (
                'def test_fact_lists_only_the_machines_of_ones_organizations(self):',
                'def test_the_admin_still_sees_every_machine(self):',
                'def test_a_foreign_machine_in_the_address_is_not_opened(self):',
                'def test_an_answer_on_a_foreign_site_is_refused_and_not_written(self):',
                'def test_the_period_card_lists_only_ones_own_machines(self):',
                'def test_a_user_without_organizations_sees_no_machine(self):'):
            with self.subTest(test=test):
                self.assertIn(test, git('show', '%s:tests/test_gps_org_scope.py'
                                        % commit))

    def test_the_scope_is_what_the_runbook_says(self):
        # «Что произойдёт» и шаг 4: администратор без границы; не-админ -- его
        # организации, без организаций -- ни одной машины; граница режет
        # «Факт», ответ по участку и «Машину за период». Текст читается, а не
        # импортируется: эти тесты идут в CI без Flask.
        source = read('gps_routes.py')
        scope = source[source.index('def _org_scope():'):
                       source.index('def _units_in_scope(scope):')]
        self.assertIn('    if current_user.is_admin:\n        return None\n', scope)
        self.assertIn('    return set(current_user.get_org_ids()) or {0}\n', scope)
        for route in ("@gps_bp.route('/fact')", "@gps_bp.route('/fact/answer'",
                      "@gps_bp.route('/machine')"):
            with self.subTest(route=route):
                start = source.index(route)
                end = source.find('@gps_bp.route(', start + 1)
                body = source[start:end if end > 0 else len(source)]
                self.assertIn('allowed = _units_in_scope(_org_scope())', body)
        answer = source[source.index("@gps_bp.route('/fact/answer'"):]
        self.assertRegex(answer.split('label = ')[0],
                         r'if allowed is not None and site\.wialon_id not in '
                         r'allowed:\n(?:\s+#.*\n)*\s+abort\(403\)')
        flat = ' '.join(text(RELEASE).split())
        for claim in (
                'Администратор видит всё, как раньше.',
                'ответ «работа/проезд» по участку чужой машины программа не '
                'принимает',
                '«Машина за период» — та же граница',
                'сотрудник, у которого в учётной записи не указана ни одна '
                'организация, не видит ни одной машины'):
            with self.subTest(claim=claim):
                self.assertIn(claim, flat)

    def test_a_foreign_address_opens_one_of_ones_own_machines(self):
        # Шаг 4.3: адрес чужой машины, выбранной при «Все организации»,
        # открывает у сотрудника его машину и предупреждение «не найдено».
        # Так устроен выбор машины на «Факте»: чужой машины нет среди суток
        # (граница режет их запрос), номер из адреса сбрасывается, набранное
        # имя ищется только среди его машин, а без находки открывается первая
        # его машина с посчитанными сутками.
        source = read('gps_routes.py')
        fact = source[source.index("@gps_bp.route('/fact')"):
                      source.index('def option_note(a):')]
        for line in (
                'aggregate_query = aggregate_query.filter(\n'
                '            GpsDailyAggregate.wialon_id.in_(list(allowed)))',
                'if asked not in by_id or not fits(by_id[asked], with_open=False):\n'
                '        asked = None',
                "search = {'text': typed, 'found': len(found),",
                'if unit_id is None and listed:',
                'unit_id = (published or listed)[0].wialon_id'):
            with self.subTest(line=line[:40]):
                self.assertIn(line, fact)
        page = read('templates', 'gps', 'fact.html')
        self.assertIn("{% if search and search.found == 0 %}", page)
        self.assertIn("'По «%s» машин не найдено — проверьте номер или фильтры.' "
                      "if is_ru", page)
        self.assertIn('<option value="">{{ \'Все организации\' if is_ru', page)
        flat = step('Шаг 4')
        self.assertIn('оставляете в фильтре «Организация» «Все организации»', flat)
        self.assertIn('над полем «Машина» появится предупреждение, что машин не '
                      'найдено, а ниже — одна из его машин', flat)
        # С выбранной чужой организацией в адресе сотрудник не увидит ни одной
        # машины, а не свою -- поэтому шаг велит оставить «Все организации».
        self.assertIn("elif filters['org'] is not None and machine.org_id != "
                      "filters['org']:", fact)

    def test_what_step_four_names_is_on_the_screen(self):
        # Подписи, которые шаг 4 велит найти, -- слово в слово подписи
        # шаблонов и меню. Адресов шаг не даёт: экраны открываются из меню.
        flat = step('Шаг 4')
        self.assertNotIn('http://', flat)
        fact = read('templates', 'gps', 'fact.html')
        card = read('templates', 'gps', 'machine.html')
        menu = read('templates', '_module_nav.html')
        named = set(re.findall(r'«([^«»]+)»', flat.split('**Прислать:**')[0]))
        for label in ('Организация', 'Все организации',
                      'Машина — наберите номер или модель'):
            with self.subTest(label=label):
                self.assertIn(label, named)
                self.assertIn("'%s' if is_ru" % label, fact)
                self.assertIn("'%s' if is_ru" % label, card)
        for label in ('Факт по технике', 'Машина за период'):
            with self.subTest(label=label):
                self.assertIn(label, named)
                self.assertIn("'ru': '%s'" % label, menu)
        # Ни одной подписи в кавычках, которой нет на экране или в меню, --
        # кроме падежной формы названия экрана и короткого имени поля.
        known = {'Организация', 'Все организации',
                 'Машина — наберите номер или модель', 'Факт по технике',
                 'Машина за период', 'Факте по технике', 'Машина'}
        self.assertEqual(named - known, set())
        self.assertIn('Машина — наберите номер или модель', named)

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
        self.assertGreaterEqual(len(table), 10)
        blocks = ''.join(block(name) for name in BLOCKS)
        for line in table:
            shown = re.match(r'\| `RESULT: STOP - ([^`]*)`', line).group(1)
            words = shown.split('...')[0].strip()
            with self.subTest(stop=words):
                self.assertTrue(words)
                self.assertIn(words, blocks)

    def test_the_registry_line_is_the_one_production_has(self):
        # После выпуска v1.30 реестр -- 61 (docs/DEPLOYED.md); в этом выпуске
        # миграций нет, и шаг 3 печатает реестр до и после одним числом.
        self.assertIn('реестр 61 → 61', read('docs', 'DEPLOYED.md'))
        self.assertIn('`MIGRATIONS REGISTERED: %d -> %d, unchanged`'
                      % (REGISTERED, REGISTERED), text(RELEASE))
        self.assertIn('"MIGRATIONS REGISTERED: $($drift0.Registered) -> '
                      '$($drift1.Registered), unchanged"', block('Шаг 3'))

    def test_no_secret_and_no_person_names(self):
        body = text(RELEASE)
        self.assertNotRegex(body, r'password=(?!`)\S')
        self.assertNotIn('wialon_token', body)
        self.assertNotIn('agro_work_credentials', body)


class ReleaseV131ToolFormats(unittest.TestCase):
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
TEST_SERVICES = ['V131TestReport', 'V131TestBot', 'V131TestBot003']
LOGIN_BODY = '<form method="post" class="vs-login-form">'


@unittest.skipUnless(POWERSHELL, 'GPS_RELEASE_POWERSHELL is not set')
class ReleaseV131BlocksInPowerShell(unittest.TestCase):
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
            'DiffNames': ['gps_routes.py', 'gps/multipass.py',
                          'docs/GPS_RELEASE_V131_RUNBOOK.md'],
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
            'site': "'http://v131-test.invalid'", 'work': "'%s'" % work,
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
        # На проде две миграции Дронов без строки в реестре (с выпуска v1.20):
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
                                                                'gps_routes.py']),
            'the release gate is closed: 1 open item': dict(GateLines=gate_lines(1)),
            'could not be read': dict(GateLines=[]),
            'already on the server': dict(Head=RELEASE_HASH),
            'this release was prepared for': dict(Head='0' * 40),
            'edited on this server': dict(Modified=[' M app.py']),
            'does not continue': dict(Ancestry=[[REVIEWED_HASH, RELEASE_HASH]]),
            'migrations in the release: migrate_fin_recon_storage_001.py; this '
            'release has none': dict(DiffNames=['gps_routes.py',
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
            'did not stop within 90 seconds': dict(StopFails=['V131TestBot']),
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
                                      ChangedAfterReviewed=['gps_routes.py']),
        }
        for message, change in cases.items():
            with self.subTest(case=message):
                result, calls, _output, _ = self.run_block(
                    'Откат', self.scenario(**change))
                self.assertIn(message, result)
                self.assertUntouched(calls)


if __name__ == '__main__':
    unittest.main()
