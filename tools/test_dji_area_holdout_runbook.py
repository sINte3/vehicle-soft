# -*- coding: utf-8 -*-
"""Проверка блоков docs/DJI_AREA_SIMPLIFY_001_RUNBOOK.md.

[REASON]: блоки вставляются в консоль ВЕРБАТИМ, и один из них ходит в кабинет
DJI и пишет на площадку. Свойства ниже ломаются молча -- ни `py_compile`, ни
глаз при чтении диффа их не ловят:

  * плейсхолдеров нет: строка вида `<путь>` будет выполнена буквально;
  * `&&` нет: в PowerShell 5 его не существует;
  * python с пробелом в пути вызывается через `&`;
  * каждый `throw` стоит за проверкой на той же строке;
  * production не назван нигде, порт 5050 -- тоже;
  * блок W проверяет, что приёмник -- площадка, ДО первого обращения к кабинету,
    и печатает хеш плана ДО первого запроса V4: на этом держится слово «слепой»;
  * блок S делает резервную копию до записи, доказывает идемпотентность
    воротами, берёт оба хеша вокруг отчёта и поднимает службу в `finally`;
  * коды 3 и 5 отчёта -- результат, а не сбой: блок не имеет права упасть на
    них и потерять архив;
  * названные файлы репозитория существуют.

Запуск:  python tools\\test_dji_area_holdout_runbook.py
"""

import os
import re
import sys
import unittest

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if REPO_ROOT not in sys.path:
    sys.path.insert(0, REPO_ROOT)

RUNBOOK = os.path.join(REPO_ROOT, 'docs', 'DJI_AREA_SIMPLIFY_001_RUNBOOK.md')
BRANCH = 'claude/dji-area-simplify-001'
PY_PATH = r'C:\Program Files\Python314\python.exe'


def read():
    with open(RUNBOOK, encoding='utf-8') as fh:
        return fh.read()


def blocks(text=None):
    return re.findall(r'```powershell\n(.*?)```', text or read(), re.S)


def block_w():
    return [b for b in blocks() if '--send-sources' in b][0]


def block_s():
    return [b for b in blocks() if 'Stop-Service' in b][0]


def pos(block, needle):
    index = block.find(needle)
    if index < 0:
        raise AssertionError('%r is not in the block' % needle)
    return index


class EveryBlock(unittest.TestCase):

    def test_there_are_exactly_three_blocks_and_each_is_recognised(self):
        found = blocks()
        self.assertEqual(len(found), 3)
        self.assertEqual(sum('--save-session' in b for b in found), 1)
        self.assertEqual(sum('--send-sources' in b for b in found), 1)
        self.assertEqual(sum('Stop-Service' in b for b in found), 1)

    def test_a_throw_stops_the_whole_paste_not_one_line(self):
        # [REASON]: консоль исполняет вставленные строки по одной, и `throw`
        # прекращает только свою. Без обёртки `& { ... }` отказ на проверке
        # приёмника не остановил бы сборщик строкой ниже -- гейт площадки
        # существовал бы только на бумаге.
        for block in (block_w(), block_s()):
            lines = [ln for ln in block.strip().splitlines() if ln.strip()]
            self.assertEqual(lines[0], '& {')
            self.assertEqual(lines[-1], '}')
            depth = 0
            for number, line in enumerate(lines):
                depth += line.count('{') - line.count('}')
                if number < len(lines) - 1:
                    self.assertGreater(depth, 0, line)
            self.assertEqual(depth, 0)

    def test_no_placeholders_survive(self):
        for block in blocks():
            self.assertIsNone(re.search(r'<[^>\n]{1,60}>', block), block[:80])
            self.assertNotIn('...', block)
            self.assertNotIn('TODO', block)

    def test_powershell_has_no_and_and(self):
        for block in blocks():
            self.assertNotIn('&&', block)

    def test_python_with_a_space_in_its_path_is_called_through_ampersand(self):
        for block in blocks():
            self.assertIn(PY_PATH, block)
            for line in block.splitlines():
                stripped = line.strip()
                if not re.search(r'(\$py\b|python\.exe)', stripped):
                    continue
                # Присваивание и проверка существования python не вызывают.
                if stripped.startswith('$py') or stripped.startswith(
                        'if (-not (Test-Path -LiteralPath $py))'):
                    continue
                self.assertTrue(stripped.startswith('&'), line)

    def test_every_throw_is_guarded_on_its_own_line(self):
        for block in blocks():
            for line in block.splitlines():
                if 'throw' in line:
                    self.assertRegex(line.strip(), r'^if \(', line)

    def test_production_is_not_named_anywhere(self):
        for block in blocks():
            self.assertNotIn(':5050', block)
            self.assertNotIn('DroneCollectorDaily', block)
            for match in re.finditer(r'transport-report[\w-]*', block):
                self.assertEqual(match.group(0), 'transport-report-staging')
            for match in re.finditer(r"'(TransportReport\w*)'", block):
                self.assertEqual(match.group(1), 'TransportReportStaging')

    def test_git_is_paged_off(self):
        for block in blocks():
            for line in block.splitlines():
                if re.search(r'\bgit\b.*\blog\b', line):
                    self.assertIn('--no-pager', line)

    def test_the_branch_named_is_the_working_branch(self):
        self.assertIn("$branch  = '%s'" % BRANCH, block_w())
        self.assertIn("$branch  = '%s'" % BRANCH, block_s())

    def test_every_repo_file_the_blocks_run_is_in_the_worktree(self):
        named = set()
        for block in blocks():
            named.update(re.findall(r'(tools\\[\w]+\.py)', block))
            named.update(m.replace('.', os.sep) + '.py' for m in re.findall(
                r'-m unittest ([\w.]+)', block))
        self.assertIn(r'tools\dji_area_holdout.py', named)
        self.assertIn(r'tools\dji_area_idempotence_gate.py', named)
        for rel in named:
            path = os.path.join(REPO_ROOT, rel.replace('\\', os.sep))
            self.assertTrue(os.path.exists(path), rel)

    def test_control_a_made_up_name_would_be_caught(self):
        # Отрицательный контроль к проверке выше: она обязана уметь падать.
        path = os.path.join(REPO_ROOT, 'tools', 'dji_area_holdout_NOT_THERE.py')
        self.assertFalse(os.path.exists(path))


class TheWorkstationBlock(unittest.TestCase):

    def test_the_receiver_is_proved_to_be_staging_before_any_collector_run(self):
        block = block_w()
        first_collector = pos(block, '-m drone_collector.main')
        # [REASON]: гейтов ДВА, и каждый ищется своей строкой. Общий поиск
        # `-notmatch ':5051'` находил бы второй гейт, когда первый удалён, --
        # проверка проходила бы при блоке, готовом слать на production.
        # Переменная окружения перекрывает `.env`, поэтому нужны оба.
        for guard in ("Where-Object { $_.Line -notmatch ':5051' }",
                      'if ($notStaging.Count -gt 0) { throw',
                      "($env:VEHICLE_SOFT_BASE_URL -notmatch ':5051')) "
                      "{ throw",
                      'if ($urlLines.Count -eq 0) { throw'):
            self.assertLess(pos(block, guard), first_collector, guard)
        # [REASON]: dotenv отдаёт ПОСЛЕДНЮЮ из одноимённых строк. Проверка
        # только первой пропустила бы `.env`, где площадка названа первой, а
        # действует вторая строка.
        self.assertNotIn('Select-Object -First 1', block)

    def test_the_token_is_never_printed(self):
        block = block_w()
        self.assertNotIn('DRONE_API_TOKEN', block)
        for line in block.splitlines():
            if 'Write-Host' in line:
                self.assertNotIn('$urlLines', line)
                self.assertNotIn('$notStaging', line)
                self.assertNotIn('$env:', line)

    def test_the_plan_is_locked_and_printed_before_the_first_v4_is_asked(self):
        block = block_w()
        order = [pos(block, '--dry-run'),
                 pos(block, 'dji_area_holdout.py list-db'),
                 pos(block, 'dji_area_holdout.py plan'),
                 pos(block, 'PLAN SHA256'),
                 pos(block, '--sources --ids-file')]
        self.assertEqual(order, sorted(order))

    def test_the_first_list_walk_sends_nothing(self):
        block = block_w()
        walk = [ln for ln in block.splitlines() if '--dry-run' in ln]
        self.assertEqual(len(walk), 1)
        self.assertNotIn('--send', walk[0])
        self.assertNotIn('--kind', walk[0])

    def test_only_the_planned_ids_are_captured(self):
        block = block_w()
        capture = [ln for ln in block.splitlines() if '--sources' in ln]
        self.assertEqual(len(capture), 1)
        self.assertIn('--ids-file $ids', capture[0])
        self.assertNotIn('--from', capture[0])
        self.assertIn(r"plan\capture_ids.txt'", block)

    def test_self_tests_run_before_anything_is_collected(self):
        block = block_w()
        self.assertLess(pos(block, r'tools\test_dji_area_holdout.py'),
                        pos(block, '-m drone_collector.main'))

    def test_resumable_exit_18_is_not_an_error_and_nothing_else_is_allowed(self):
        block = block_w()
        self.assertIn('if (($rc -ne 0) -and ($rc -ne 18)) { throw', block)

    def test_an_existing_plan_is_never_rebuilt(self):
        block = block_w()
        guard = pos(block, 'if (-not (Test-Path -LiteralPath $plan)) {')
        self.assertLess(guard, pos(block, 'dji_area_holdout.py plan'))
        self.assertLess(guard, pos(block, '--dry-run'))


class TheStagingBlock(unittest.TestCase):

    def test_nothing_is_touched_without_the_plan_file(self):
        block = block_s()
        self.assertLess(pos(block, 'if (-not (Test-Path -LiteralPath $planIn))'),
                        pos(block, 'Remove-Item'))
        self.assertLess(pos(block, 'if (-not (Test-Path -LiteralPath $planIn))'),
                        pos(block, 'Stop-Service'))

    def test_cleanup_never_removes_the_plan_or_the_base_directory(self):
        block = block_s()
        for line in block.splitlines():
            if 'Remove-Item' in line:
                self.assertRegex(line, r'-LiteralPath \$(src|out|recalc) ')

    def test_no_write_happens_before_a_backup_is_taken_and_verified(self):
        block = block_s()
        self.assertLess(pos(block, 'Copy-Item -LiteralPath $db'),
                        pos(block, '--apply'))
        self.assertLess(pos(block, 'backup was not created'),
                        pos(block, '--apply'))

    def test_the_write_step_proves_idempotence_by_machine(self):
        block = block_s()
        self.assertEqual(block.count('--apply'), 2)
        self.assertIn("'apply1.json'", block)
        self.assertIn("'apply2.json'", block)
        gate = pos(block, r'tools\dji_area_idempotence_gate.py --summary')
        self.assertLess(block.rfind('--apply'), gate)
        self.assertIn("apply2.json')", block[gate:gate + 120])

    def test_the_read_only_hashes_bracket_the_report_not_the_recalc(self):
        block = block_s()
        before = pos(block, 'DB SHA256 BEFORE')
        report = pos(block, 'dji_area_holdout.py report')
        after = pos(block, 'DB SHA256 AFTER')
        self.assertLess(block.rfind('--apply'), before)
        self.assertLess(before, report)
        self.assertLess(report, after)
        self.assertIn('if ($before -ne $after) { throw', block)

    def test_a_stopped_service_is_always_restarted_in_a_finally(self):
        block = block_s()
        self.assertLess(pos(block, 'Stop-Service'), pos(block, '} finally {'))
        tail = block[pos(block, '} finally {'):]
        self.assertIn('Restart-Service -Name $service', tail)
        self.assertNotIn('Start-Service', block.replace('Restart-Service', ''))
        self.assertIn("-ne 'Running'", tail)

    def test_everything_that_reads_or_writes_the_database_is_inside_try(self):
        block = block_s()
        start = pos(block, 'try {')
        end = pos(block, '} finally {')
        for needle in ('dji_area_recalc.py', 'dji_area_holdout.py report',
                       'Get-FileHash', 'Copy-Item -LiteralPath $db'):
            for match in re.finditer(re.escape(needle), block):
                self.assertTrue(start < match.start() < end, needle)

    def test_fail_and_inconclusive_are_results_not_crashes(self):
        block = block_s()
        self.assertIn('if (($rc -ne 0) -and ($rc -ne 3) -and ($rc -ne 5)) '
                      '{ throw', block)
        # Архив собирается ПОСЛЕ этой проверки и при 3, и при 5.
        self.assertLess(pos(block, '($rc -ne 5)'),
                        pos(block, 'Compress-Archive'))
        report_line = [ln for ln in block.splitlines()
                       if 'dji_area_holdout.py report' in ln][0]
        following = block[pos(block, report_line) + len(report_line):]
        self.assertTrue(following.lstrip().startswith('$rc = $LASTEXITCODE'))

    def test_the_period_comes_from_the_locked_plan_not_from_a_typed_date(self):
        block = block_s()
        self.assertIn('$from = $locked.locked.period.from', block)
        self.assertIn('$to = $locked.locked.period.to', block)
        self.assertIsNone(re.search(r'--(from|to) 20\d\d-', block))

    def test_the_same_plan_hash_is_printed_in_both_blocks(self):
        self.assertIn('"PLAN SHA256: " + $locked.locked_sha256', block_w())
        self.assertIn('"PLAN SHA256: " + $locked.locked_sha256', block_s())


if __name__ == '__main__':
    unittest.main()
