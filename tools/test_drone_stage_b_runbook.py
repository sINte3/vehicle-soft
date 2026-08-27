# -*- coding: utf-8 -*-
"""Проверка ранбука первого живого прогона этапа B.

    python tools/test_drone_stage_b_runbook.py

Ранбук — это готовые к вставке команды PowerShell, и вставлены они будут
буквально. Поэтому он проверяется так же, как код.

Что здесь держится и почему именно это:

* **интерпретатор — venv сборщика.** Playwright и Chromium стоят в
  `drone_collector\\.venv`; системный Python их не видит, и живой прогон упал
  бы на импорте. Один раз ранбук уже звал системный Python, и заметил это
  владелец, а не проверка;
* **репозиторий — `C:\\transport-report`.** Один раз в ранбуке стоял
  несуществующий `C:\\vehicle-soft`;
* **геометрия — только по `--geometry-id`.** Полный сбор качает 5 489
  контуров, то есть предъявляет пять с половиной тысяч подписанных ссылок
  ради одной проверки формата. Первый пилот так делать не должен, и это
  свойство ранбука, а не кода: код полный сбор по-прежнему умеет;
* **никаких установок.** `pip install` и `playwright install` внутри живого
  прогона — это правка среды в момент, когда меряют среду.

Вывод только ASCII: файл гоняется и на консоли Windows.
"""

import os
import re
import sys
import unittest

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, REPO_ROOT)

TRACK = os.path.join(REPO_ROOT, 'docs', 'tracks', 'drones.md')

VENV_PYTHON = r'C:\transport-report\drone_collector\.venv\Scripts\python.exe'
SYSTEM_PYTHON = r'C:\Program Files\Python314\python.exe'
PILOT_UUID = 'baf71584-64e2-49c5-8a41-25fca4ad5f6e'

# Начало пункта 9а и начало следующего пункта: ранбук лежит между ними.
SECTION_START = '9\u0430. **\u041f\u0435\u0440\u0432\u044b\u0439 \u0436\u0438\u0432\u043e\u0439 \u043f\u0440\u043e\u0433\u043e\u043d \u044d\u0442\u0430\u043f\u0430 B'
SECTION_END = '9\u0431.'


def runbook_text():
    with open(TRACK, encoding='utf-8') as handle:
        text = handle.read()
    start = text.index(SECTION_START)
    end = text.index(SECTION_END, start)
    return text[start:end]


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

    def test_the_repository_path_is_the_real_one(self):
        self.assertIn(r'Set-Location "C:\transport-report"', self.text)
        self.assertNotIn(r'C:\vehicle-soft', self.text)

    def test_the_preflight_checks_playwright(self):
        self.assertIn('PLAYWRIGHT_IMPORT=PASS', self.text)
        self.assertTrue(any('--version' in line for line in self.commands))
        self.assertTrue(any('--help' in line for line in self.commands))

    def test_the_runbook_installs_nothing(self):
        for line in self.commands:
            self.assertNotIn('pip install', line)
            self.assertNotIn('playwright install', line)


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


class RunbookRouteScopeTests(unittest.TestCase):

    def setUp(self):
        self.commands = command_lines(runbook_text())

    def route_commands(self):
        return [line for line in self.commands if '--routes' in line]

    def test_the_route_step_is_run_dry_first(self):
        routes = self.route_commands()
        self.assertGreaterEqual(len(routes), 3)
        self.assertIn('--dry-run', routes[0])

    def test_the_route_step_is_repeated_for_idempotence(self):
        real = [line for line in self.route_commands()
                if '--dry-run' not in line]
        self.assertGreaterEqual(len(real), 2)
        self.assertEqual(real[0], real[1],
                         'the repeat run is not the identical command')

    def test_the_route_step_covers_one_day(self):
        for line in self.route_commands():
            match = re.search(r'--from (\S+) --to (\S+)', line)
            self.assertIsNotNone(match, line)
            self.assertEqual(match.group(1), match.group(2),
                             'the pilot route run is not limited to one day: '
                             '%s' % line)


if __name__ == '__main__':
    unittest.main(verbosity=2)
