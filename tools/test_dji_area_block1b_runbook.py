# -*- coding: utf-8 -*-
"""Проверка блока этапа 1B в docs/DJI_AREA_BLOCK1B_RUNBOOK.md.

[REASON]: этот блок вставляется в консоль сервера ВЕРБАТИМ. Трек уже дважды
называл в ранбуке то, чего на сервере нет -- путь репозитория, затем python
без Playwright, -- и оба раза ловил это человек, а не проверка. Здесь держатся
свойства, которые ломаются молча:

  * плейсхолдеров нет вовсе. Строка вида `<каталог прогона>` будет вставлена
    буквально и выполнена буквально;
  * `&&` нет: в PowerShell его не существует, и склеенная строка молча
    выполнит не то;
  * путь к python содержит пробел, поэтому вызывается через `&` и в кавычках;
  * каждому `throw` предшествует проверка -- код возврата, `Test-Path` или
    сравнение хешей. `throw` без условия останавливает работу всегда;
  * блок называет ИМЕНА ФАЙЛОВ, которые в репозитории действительно есть.
    Это ровно та ошибка, на которой трек уже горел;
  * читается только база площадки; production в блоке не упоминается.
"""

import os
import re
import sys
import unittest

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if REPO_ROOT not in sys.path:
    sys.path.insert(0, REPO_ROOT)

RUNBOOK = os.path.join(REPO_ROOT, 'docs', 'DJI_AREA_BLOCK1B_RUNBOOK.md')
PY_PATH = r'C:\Program Files\Python314\python.exe'


def read():
    with open(RUNBOOK, encoding='utf-8') as fh:
        return fh.read()


def blocks(text):
    return re.findall(r'```powershell\n(.*?)```', text, re.S)


class TheBlockIsPasteable(unittest.TestCase):

    def setUp(self):
        self.text = read()
        self.blocks = blocks(self.text)

    def test_there_is_exactly_one_block_to_paste(self):
        # Владелец просил ОДИН блок за раз. Два блока -- уже порядок действий,
        # а порядок теряется при копировании.
        self.assertEqual(len(self.blocks), 1)

    def test_no_placeholders_survive(self):
        # <...> в готовой к вставке команде будет вставлен буквально.
        for block in self.blocks:
            found = re.findall(r'<[^>\n]{2,40}>', block)
            self.assertEqual(found, [], 'placeholder(s) left: %r' % (found,))
            for token in ('TODO', 'FIXME', 'ПУТЬ', 'ваш ', 'сюда'):
                self.assertNotIn(token, block)

    def test_powershell_has_no_and_and(self):
        for block in self.blocks:
            self.assertNotIn('&&', block)

    def test_python_with_a_space_in_its_path_is_called_through_ampersand(self):
        for block in self.blocks:
            self.assertIn(PY_PATH, block)
            for line in block.splitlines():
                if PY_PATH in line and not line.strip().startswith('$py'):
                    self.fail('python path used outside the $py variable: %r'
                              % line)
            self.assertRegex(block, r'&\s+\$py\s')

    def test_every_throw_is_guarded_by_a_check(self):
        for block in self.blocks:
            for line in block.splitlines():
                if 'throw' not in line:
                    continue
                guarded = ('if (' in line and (
                    '$LASTEXITCODE' in line or 'Test-Path' in line
                    or '-ne' in line or '-notlike' in line or '-not ' in line))
                self.assertTrue(guarded, 'unguarded throw: %r' % line)

    def test_the_read_only_proof_is_actually_compared(self):
        block = self.blocks[0]
        self.assertIn('SHA256 BEFORE', block)
        self.assertIn('SHA256 AFTER', block)
        # Напечатать два хеша мало: их надо СРАВНИТЬ и остановиться.
        self.assertRegex(block, r'if \(\$before -ne \$after\)\s*\{\s*throw')

    def test_it_runs_the_tool_self_test_before_touching_the_live_database(self):
        block = self.blocks[0]
        self_test = block.index('test_dji_area_block1b.py')
        live_run = block.index('dji_area_block1b.py --db')
        self.assertLess(self_test, live_run)

    def test_production_is_not_named_anywhere(self):
        # Прод в этом этапе не участвует. Упоминание пути прода в блоке,
        # который будет вставлен не глядя, -- это авария на одну опечатку.
        for block in self.blocks:
            self.assertNotIn(r'C:\transport-report\\', block)
            self.assertNotRegex(block, r"'C:\\transport-report'")
            self.assertNotIn('SRV-YOQSH', block)

    def test_it_never_starts_or_restarts_a_service(self):
        for block in self.blocks:
            for verb in ('Start-Service', 'Restart-Service', 'Stop-Service'):
                self.assertNotIn(verb, block)

    def test_git_is_paged_off(self):
        # Пейджер съедает следующие команды вставленного блока.
        for block in self.blocks:
            for line in block.splitlines():
                if re.search(r'&\s+git\b', line) and ' log' in line:
                    self.assertIn('--no-pager', line)


class TheBlockNamesThingsThatExist(unittest.TestCase):

    def setUp(self):
        self.block = blocks(read())[0]

    def test_every_repo_file_the_block_runs_is_in_the_worktree(self):
        named = set(re.findall(r'tools\\([A-Za-z0-9_]+\.py)', self.block))
        self.assertTrue(named, 'the block runs no repository file at all')
        for name in sorted(named):
            path = os.path.join(REPO_ROOT, 'tools', name)
            self.assertTrue(os.path.isfile(path), 'named but missing: %s' % path)

    def test_control_a_made_up_name_would_be_caught(self):
        # Отрицательный контроль проверки выше: она обязана уметь провалиться.
        self.assertFalse(os.path.isfile(
            os.path.join(REPO_ROOT, 'tools', 'no_such_tool_here.py')))

    def test_the_branch_named_is_the_working_branch(self):
        self.assertIn('claude/dji-agras-area-review-7sw9c1', self.block)

    def test_the_hardware_ids_are_the_two_machines_under_study(self):
        # №5 и №6 за 18.08 -- те, по которым владелец вёл расследование.
        self.assertIn('1581F574B2387001009R', self.block)
        self.assertIn('1581F574B235W00100Q5', self.block)
        self.assertIn('--date 2026-08-18', self.block)


if __name__ == '__main__':
    unittest.main()
