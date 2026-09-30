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
    """Вывод на production: только шаги трека, код привозит релиз.

    Владелец 28.09: `git pull` в C:\\transport-report в шаги владельцу не
    ставить. Код на сервер привозит общий порядок релиза
    (docs/RELEASE_AND_BACKUP_PROCEDURE.md), он же останавливает и поднимает
    службы; здесь git только читает, а службы не трогаются.
    """

    READ_ONLY_GIT = ('log', 'merge-base', 'rev-parse', 'status')

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

    def test_git_only_reads(self):
        for line in self.lines():
            match = re.match(r'\s*git\s+(?:--no-pager\s+|-C\s+\S+\s+)*(\S+)', line)
            if match:
                self.assertIn(match.group(1), self.READ_ONLY_GIT, line)

    def test_services_are_left_to_the_release(self):
        for line in self.lines():
            self.assertNotRegex(line, r'(?i)\b(stop|start|restart)-service\b|nssm',
                                line)

    def test_every_named_script_exists_and_python_is_quoted(self):
        names = set()
        for line in self.lines():
            names.update(re.findall(r'(tools\\[A-Za-z0-9_]+\.py)', line))
            names.update(re.findall(r'\b(migrate_[A-Za-z0-9_]+\.py)', line))
            if 'python.exe' in line.lower() and not line.startswith('Set-Content'):
                self.assertTrue(line.startswith(PYTHON), line)
        self.assertTrue(names)
        for name in names:
            path = os.path.join(REPO_ROOT, name.replace('\\', os.sep))
            self.assertTrue(os.path.isfile(path), name)

    def test_every_step_that_runs_a_script_first_goes_to_production(self):
        found = steps(RELEASE)
        self.assertGreaterEqual(len(found), 8)
        for title, section in found.items():
            lines = commands(section)
            # Строка Set-Content пишет содержимое .bat, а не запускает его:
            # путь `tools\...` в ней -- текст файла.
            first = next((i for i, line in enumerate(lines)
                          if RELATIVE_SCRIPT.search(line)
                          and not line.startswith('Set-Content')), None)
            if first is None:
                continue
            with self.subTest(step=title):
                self.assertIn('cd C:\\transport-report', lines[:first])

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
        expected = ('Done. %d agro_work tables (%d columns), %d indexes and %d '
                    'triggers are in place.'
                    % (len(mig.EXPECTED_COLUMNS),
                       sum(len(c) for c in mig.EXPECTED_COLUMNS.values()),
                       len(mig.INDEXES), len(mig.TRIGGERS)))
        self.assertIn(expected, text(RELEASE))

    def test_no_secret_and_the_credentials_file_is_never_shown(self):
        self.assertNotRegex(text(RELEASE), r'password=(?!`)\S')
        for line in self.lines():
            if 'agro_work_credentials.txt' in line:
                shown = re.match(r'\s*(Get-Content|type|cat)\b', line, re.I)
                self.assertIsNone(shown, line)


if __name__ == '__main__':
    unittest.main()
