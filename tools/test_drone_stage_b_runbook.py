# -*- coding: utf-8 -*-
"""Проверка ранбука следующего безопасного шага этапа B.

    python tools/test_drone_stage_b_runbook.py

Ранбук — это готовые к вставке команды PowerShell, и вставлены они будут
буквально. Поэтому он проверяется так же, как код.

Что здесь держится и почему именно это:

* **интерпретатор — venv сборщика.** Playwright и Chromium стоят в
  `drone_collector\\.venv`; системный Python их не видит, и живой прогон упал
  бы на импорте. Один раз ранбук уже звал системный Python, и заметил это
  владелец, а не проверка;
* **репозиторий — чекаут пилота `C:\\VehicleSoft_DJI_StageB_Pilot`.** Один
  раз в ранбуке стоял несуществующий `C:\\vehicle-soft`;
* **геометрия — только по `--geometry-id`.** Полный сбор качает 5 489
  контуров, то есть предъявляет пять с половиной тысяч подписанных ссылок
  ради одной проверки формата. Первый пилот так делать не должен, и это
  свойство ранбука, а не кода: код полный сбор по-прежнему умеет;
* **никаких установок.** `pip install` и `playwright install` внутри живого
  прогона — это правка среды в момент, когда меряют среду;
* **PASS не печатается после отказа.** Первый живой прогон был вставлен
  отдельными операторами: после `throw` следующие команды всё равно
  выполнились и напечатали `PASS` при коде выхода 4;
* **настоящего сбора маршрутов в ранбуке нет.** Нативный `fetch` опровергнут
  живым прогоном; предлагать его снова значит приглашать повторить
  девятнадцать отказов 408.

Вывод только ASCII: файл гоняется и на консоли Windows.
"""

import os
import re
import sys
import unittest

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, REPO_ROOT)

TRACK = os.path.join(REPO_ROOT, 'docs', 'tracks', 'drones.md')

VENV_PYTHON = (r'C:\VehicleSoft_DJI_StageB_Pilot\drone_collector'
               r'\.venv\Scripts\python.exe')
SYSTEM_PYTHON = r'C:\Program Files\Python314\python.exe'
PILOT_UUID = 'baf71584-64e2-49c5-8a41-25fca4ad5f6e'

# Начало пункта 9а и начало следующего пункта: ранбук лежит между ними.
SECTION_START = '9\u0430. **'
SECTION_END = '9\u0432.'


def runbook_text():
    with open(TRACK, encoding='utf-8') as handle:
        text = handle.read()
    start = text.index(SECTION_START)
    end = text.index(SECTION_END, start)
    return text[start:end]


def fenced_blocks(text):
    """Содержимое ``` ``` блоков ранбука."""
    blocks = []
    current = None
    for line in text.splitlines():
        stripped = line.strip()
        if stripped.startswith('```'):
            if current is None:
                current = []
            else:
                blocks.append('\n'.join(current))
                current = None
            continue
        if current is not None:
            current.append(line)
    return blocks


def command_lines(text):
    """Строки, которые владелец вставит в PowerShell.

    [REASON]: отбираются ТОЛЬКО команды -- строки с `&` или `Set-Location`.
    Проза вокруг них называет системный Python как раз для того, чтобы
    объяснить, почему его нельзя брать; ловить это слово в объяснении значило
    бы запретить объяснение.
    """
    lines = []
    for line in text.splitlines():
        stripped = line.strip()
        if stripped.startswith('&') or stripped.startswith('Set-Location'):
            lines.append(stripped)
    return lines


class RunbookInterpreterTests(unittest.TestCase):

    def setUp(self):
        self.text = runbook_text()
        self.commands = command_lines(self.text)

    def test_there_are_commands_to_check(self):
        """Отрицательный контроль самой проверки: пустой набор её обманет."""
        self.assertGreaterEqual(len(self.commands), 8,
                                'runbook commands were not found at all')

    def test_every_python_call_uses_the_collector_venv(self):
        for line in self.commands:
            if 'python.exe' not in line:
                continue
            self.assertIn(VENV_PYTHON, line,
                          'a runbook command runs a python that is not the '
                          'collector venv: %s' % line)

    def test_no_command_uses_the_system_python(self):
        for line in self.commands:
            self.assertNotIn(SYSTEM_PYTHON, line,
                             'the system python is back in a runbook command: '
                             '%s' % line)

    def test_the_repository_path_is_the_pilot_checkout(self):
        self.assertIn(r'Set-Location "C:\VehicleSoft_DJI_StageB_Pilot"',
                      self.text)
        self.assertNotIn(r'C:\vehicle-soft', self.text)

    def test_the_preflight_checks_playwright(self):
        self.assertIn('PLAYWRIGHT_IMPORT=PASS', self.text)
        self.assertTrue(any('--version' in line for line in self.commands))
        self.assertTrue(any('--help' in line for line in self.commands))

    def test_the_runbook_installs_nothing(self):
        for line in self.commands:
            self.assertNotIn('pip install', line)
            self.assertNotIn('playwright install', line)


class RunbookSingleBlockTests(unittest.TestCase):
    """PASS не может напечататься после отказа.

    [REASON]: первый живой прогон был вставлен в консоль ОТДЕЛЬНЫМИ
    операторами. После `throw "Route dry run stopped..."` вставленные следом
    команды всё равно выполнились, и оператор увидел
    `ROUTE_DRY_RUN_VALIDATION=PASS` при фактическом коде выхода 4. Проверка
    держит две вещи: каждый блок цельный, и PASS в нём стоит последним.
    """

    def setUp(self):
        self.text = runbook_text()
        self.blocks = fenced_blocks(self.text)

    def test_there_are_blocks_to_check(self):
        """Отрицательный контроль самой проверки."""
        self.assertGreaterEqual(len(self.blocks), 4)

    def test_every_block_that_can_fail_is_a_single_powershell_block(self):
        for block in self.blocks:
            if 'throw' not in block:
                continue
            self.assertIn('& {', block,
                          'a block that can throw is not wrapped in & { ... }:'
                          '\n%s' % block)

    def test_every_pass_line_is_the_last_statement_of_its_block(self):
        """Вердикт блока -- это `Write-Output "...=PASS"`.

        [REASON]: `PLAYWRIGHT_IMPORT=PASS` печатает питон внутри `-c`, и это
        не вердикт блока, а одна из его проверок. Вердиктом считается только
        то, что печатает сам PowerShell.
        """
        for block in self.blocks:
            lines = [line.strip() for line in block.splitlines()
                     if line.strip()]
            pass_lines = [i for i, line in enumerate(lines)
                          if line.startswith('Write-Output')
                          and '=PASS' in line]
            for index in pass_lines:
                after = [line for line in lines[index + 1:]
                         if line not in ('}', ')', '```')]
                self.assertEqual(after, [],
                                 'something can run after PASS is printed:'
                                 '\n%s' % block)

    def test_every_throw_is_preceded_by_an_exit_code_check(self):
        for block in self.blocks:
            if 'throw' not in block:
                continue
            self.assertIn('$LASTEXITCODE', block,
                          'a block throws without looking at an exit code:'
                          '\n%s' % block)

    def test_no_block_prints_pass_without_checking_anything(self):
        for block in self.blocks:
            if '=PASS' not in block:
                continue
            self.assertIn('$LASTEXITCODE', block,
                          'a block prints PASS without checking an exit code:'
                          '\n%s' % block)


class RunbookRouteCollectionIsClosedTests(unittest.TestCase):
    """Сбор маршрутов закрыт, и ранбук не должен его предлагать.

    [REASON]: нативный `fetch` опровергнут живым прогоном. Команда настоящего
    сбора, оставленная в ранбуке, была бы приглашением повторить девятнадцать
    отказов 408 и записать их в карантин.
    """

    def setUp(self):
        self.text = runbook_text()
        self.commands = command_lines(self.text)

    def test_the_runbook_offers_no_real_route_collection(self):
        for line in self.commands:
            self.assertNotIn('--routes', line,
                             'the runbook still offers a route collection '
                             'run: %s' % line)

    def test_the_runbook_offers_the_observation_instead(self):
        self.assertTrue(any('--route-ui-probe' in line
                            for line in self.commands),
                        'the runbook names no safe next step for routes')

    def test_the_runbook_states_what_the_live_run_disproved(self):
        """Статусы живого прогона записаны в трековом файле рядом с ранбуком."""
        with open(TRACK, encoding='utf-8') as handle:
            whole = handle.read()
        self.assertIn('ROUTE_NATIVE_FETCH_TRANSPORT=DISPROVED', whole)
        self.assertIn('ROUTE_LIVE_COLLECTION=BLOCKED', whole)
        self.assertIn('408', whole)

    def test_the_runbook_does_not_blame_the_machine_clock(self):
        """Часы у обоих запросов одни, и штатные запросы прошли."""
        with open(TRACK, encoding='utf-8') as handle:
            lowered = handle.read().lower()
        for wrong in ('синхронизиров', 'w32tm', 'sync the clock',
                      'переведите часы'):
            self.assertNotIn(wrong, lowered)


class RunbookGeometryScopeTests(unittest.TestCase):

    def setUp(self):
        self.text = runbook_text()
        self.commands = command_lines(self.text)

    def geometry_commands(self):
        return [line for line in self.commands if '--with-geometry' in line]

    def test_the_runbook_collects_geometry_at_all(self):
        """Отрицательный контроль: без команд геометрии проверка ниже пуста."""
        self.assertGreaterEqual(len(self.geometry_commands()), 2)

    def test_every_geometry_command_names_one_contour(self):
        for line in self.geometry_commands():
            self.assertIn('--geometry-id', line,
                          'a runbook geometry command would download the '
                          'whole directory: %s' % line)

    def test_the_pilot_contour_is_the_confirmed_one(self):
        for line in self.geometry_commands():
            self.assertIn(PILOT_UUID, line)

    def test_the_geometry_step_is_run_dry_first(self):
        geometry = self.geometry_commands()
        self.assertIn('--dry-run', geometry[0],
                      'the first geometry command of the pilot is not a dry '
                      'run')

    def test_the_geometry_step_is_repeated_for_idempotence(self):
        real = [line for line in self.geometry_commands()
                if '--dry-run' not in line]
        self.assertGreaterEqual(len(real), 2,
                                'the pilot never repeats the real geometry '
                                'run, so idempotence is not demonstrated')

    def test_the_runbook_states_that_nothing_is_sent(self):
        self.assertIn('\u041e\u0442\u043f\u0440\u0430\u0432\u043a\u0438 \u0432 Vehicle Soft', self.text)


if __name__ == '__main__':
    unittest.main(verbosity=2)
