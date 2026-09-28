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

Запуск: python -m unittest tests.test_agro_work_runbook -v
"""

import os
import re
import sys
import unittest

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if REPO_ROOT not in sys.path:
    sys.path.insert(0, REPO_ROOT)

import migrate_agro_work_001 as mig                      # noqa: E402

RUNBOOK = os.path.join(REPO_ROOT, 'docs', 'AGRO_WORK_B1_RUNBOOK.md')
PYTHON = '& "C:\\Program Files\\Python314\\python.exe"'


def text():
    with open(RUNBOOK, encoding='utf-8') as fh:
        return fh.read()


def commands():
    """Строки из блоков ```powershell."""
    out = []
    for block in re.findall(r'```powershell\n(.*?)```', text(), re.S):
        out.extend(line for line in block.splitlines() if line.strip())
    return out


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


if __name__ == '__main__':
    unittest.main()
